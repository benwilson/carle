"""The daemon process — connection, engine, heartbeat, and the socket server (U4).

One asyncio loop runs three things: the held BLE connection, the engine tick (~100 ms),
and an `asyncio.start_unix_server` that dispatches control requests to the engine. All
requests serialize on one engine lock (R13), so two clients enqueuing land in a single
order. A single instance is enforced by the socket path plus a lock file under `.carle/`
(KTD7): a second start refuses; a stale socket from a crash is detected by connecting to
it and, on connection-refused, removed before binding. A `shutdown` request stops the
tick, stills the robot, removes the socket, and exits.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from collections.abc import Callable
from pathlib import Path

from carle.daemon import moves
from carle.daemon.connection import DaemonConnection
from carle.daemon.engine import NOOP, Engine, default_tts
from carle.daemon.protocol import OPS, ProtocolError, dumps, loads, parse_steps

_log = logging.getLogger(__name__)

TICK_INTERVAL = 0.1
DEFAULT_SOCKET_PATH = Path(".carle/daemon.sock")
DEFAULT_LOCK_PATH = Path(".carle/daemon.lock")


class DaemonAlreadyRunning(Exception):
    """Raised when a live daemon already holds the socket."""


async def is_daemon_live(socket_path: Path | str) -> bool:
    """True when something is accepting connections on `socket_path`."""
    try:
        _reader, writer = await asyncio.open_unix_connection(str(socket_path))
    except (ConnectionRefusedError, FileNotFoundError, OSError):
        return False
    writer.close()
    with contextlib.suppress(Exception):
        await writer.wait_closed()
    return True


class DaemonServer:
    def __init__(
        self,
        address: str,
        *,
        socket_path: Path | str = DEFAULT_SOCKET_PATH,
        lock_path: Path | str = DEFAULT_LOCK_PATH,
        silence_floor: float = 1.0,
        connection=None,
        tts: Callable[[str], object | None] = default_tts,
        clock: Callable[[], float] = time.monotonic,
        tick_interval: float = TICK_INTERVAL,
    ) -> None:
        self._socket_path = Path(socket_path)
        self._lock_path = Path(lock_path)
        self._tick_interval = tick_interval
        self._conn = connection if connection is not None else DaemonConnection(address)
        self._engine = Engine(self._conn, clock=clock, tts=tts, silence_floor=silence_floor)
        self._lock = asyncio.Lock()
        self._shutdown = asyncio.Event()
        self._lock_fd: int | None = None

    # --- request dispatch (the testable core) ------------------------------------

    async def handle_request(self, req: dict) -> dict:
        """Dispatch one request against the engine."""
        op = req.get("op")
        if op not in OPS:
            return {"ok": False, "error": f"unknown op {op!r}"}
        # The battery read is a GATT round-trip; take the status snapshot under the
        # engine lock but read battery outside it, so a slow read cannot block an
        # emergency `stop` queued behind a `status` (the read itself is bounded).
        if op == "status":
            async with self._lock:
                status = self._engine.status()
            status["battery"] = await self._engine.battery()
            return {"ok": True, "status": status}
        async with self._lock:
            try:
                return await self._dispatch(op, req)
            except ProtocolError as exc:
                return {"ok": False, "error": str(exc)}

    async def _dispatch(self, op: str, req: dict) -> dict:
        if op == "enqueue":
            steps = parse_steps(req.get("items", []))
            self._engine.enqueue(steps)
            return {"ok": True, "enqueued": len(steps)}
        if op == "clear":
            self._engine.clear()
            return {"ok": True}
        if op == "stop":
            self._engine.stop()
            return {"ok": True}
        if op == "list_moves":
            return {"ok": True, "moves": moves.move_names()}
        if op == "shutdown":
            self._shutdown.set()
            return {"ok": True, "shutting_down": True}
        return {"ok": False, "error": f"unhandled op {op!r}"}  # pragma: no cover

    # --- the socket loop ---------------------------------------------------------

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            async for line in reader:
                if not line.strip():
                    continue
                try:
                    request = loads(line)
                except ProtocolError as exc:
                    writer.write(dumps({"ok": False, "error": str(exc)}))
                else:
                    writer.write(dumps(await self.handle_request(request)))
                await writer.drain()
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def _tick_loop(self):
        while not self._shutdown.is_set():
            try:
                await self._engine.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - a transient tick error must not kill the loop
                _log.exception("daemon tick failed")  # but a real bug is logged, not silent
            await asyncio.sleep(self._tick_interval)

    def _lock_holder_alive(self) -> bool:
        """True when the pid recorded in the lock file is a live process."""
        try:
            pid = int(self._lock_path.read_text(encoding="utf-8").strip())
        except (FileNotFoundError, ValueError):
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:  # exists but not ours to signal
            return True
        return True

    def _acquire(self) -> None:
        """Take the single-instance lock atomically, then clear any stale socket (KTD7)."""
        self._socket_path.parent.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                fd = os.open(self._lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                if self._lock_holder_alive():
                    raise DaemonAlreadyRunning(
                        f"a daemon already holds {self._lock_path}"
                    ) from None
                with contextlib.suppress(FileNotFoundError):
                    self._lock_path.unlink()  # stale lock; the holder is dead
                continue
            os.write(fd, str(_pid()).encode("utf-8"))
            self._lock_fd = fd
            break
        with contextlib.suppress(FileNotFoundError):
            self._socket_path.unlink()  # a stale socket from a crashed run

    def _release(self) -> None:
        if self._lock_fd is not None:
            with contextlib.suppress(OSError):
                os.close(self._lock_fd)
            self._lock_fd = None
        with contextlib.suppress(FileNotFoundError):
            self._socket_path.unlink()
        with contextlib.suppress(FileNotFoundError):
            self._lock_path.unlink()

    async def serve(self) -> None:
        """Run the daemon until a `shutdown` request arrives."""
        # Acquire outside the try: on DaemonAlreadyRunning we do NOT own the lock and must
        # not run the cleanup that would delete the live holder's lock and socket.
        self._acquire()
        tick_task: asyncio.Task | None = None
        try:
            # A connect failure is not fatal: the first send schedules a reconnect.
            with contextlib.suppress(Exception):
                await self._conn.connect()
            server = await asyncio.start_unix_server(
                self._handle_client, path=str(self._socket_path)
            )
            tick_task = asyncio.ensure_future(self._tick_loop())
            async with server:
                await self._shutdown.wait()
        finally:
            if tick_task is not None:
                tick_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await tick_task  # await it, so a stray tick can't reconnect post-close
            with contextlib.suppress(Exception):
                await self._conn.send_frame(NOOP)  # still the robot best-effort
            with contextlib.suppress(Exception):
                await self._conn.close()
            self._release()


def _pid() -> int:
    import os

    return os.getpid()
