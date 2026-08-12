"""U4 — the daemon server and Unix-socket control channel.

The server runs in-process over a real Unix socket in a tmp dir, with a fake connection
so no Bluetooth is touched. The tick interval is set huge so no tick consumes queued
steps during a test — pending counts stay deterministic (the engine's timing is U3).
"""

from __future__ import annotations

import asyncio
import contextlib
import tempfile
from pathlib import Path

import pytest

from carle.daemon.protocol import dumps, loads
from carle.daemon.server import DaemonAlreadyRunning, DaemonServer, is_daemon_live


class FakeConn:
    def __init__(self) -> None:
        self.is_connected = True
        self.sent: list[bytes] = []

    async def connect(self) -> None:
        self.is_connected = True

    async def send_frame(self, payload: bytes) -> None:
        self.sent.append(payload)

    async def read_battery(self) -> int | None:
        return 91

    async def close(self) -> None:
        self.is_connected = False


def sockdir() -> Path:
    # Unix socket paths are length-limited (~104 chars on macOS); pytest's tmp_path is
    # too long, so use a short dir under /tmp.
    return Path(tempfile.mkdtemp(prefix="cw", dir="/tmp"))


def make_server(d: Path):
    return DaemonServer(
        "AA:BB",
        socket_path=d / "d.sock",
        lock_path=d / "d.lock",
        connection=FakeConn(),
        tts=lambda _t: None,
        tick_interval=100.0,  # effectively no ticks during a test
    )


async def _wait_up(sock, timeout=2.0):
    for _ in range(int(timeout / 0.01)):
        if sock.exists() and await is_daemon_live(sock):
            return
        await asyncio.sleep(0.01)
    raise AssertionError("server did not come up")


async def _request(sock, req: dict) -> dict:
    reader, writer = await asyncio.open_unix_connection(str(sock))
    writer.write(dumps(req))
    await writer.drain()
    line = await reader.readline()
    writer.close()
    with contextlib.suppress(Exception):
        await writer.wait_closed()
    return loads(line)


async def _serve(server, body):
    """Run the server, execute `body(sock)`, then shut it down cleanly."""
    sock = server._socket_path
    task = asyncio.ensure_future(server.serve())
    try:
        await _wait_up(sock)
        await body(sock)
    finally:
        if sock.exists():
            with contextlib.suppress(Exception):
                await _request(sock, {"op": "shutdown"})
        with contextlib.suppress(Exception):
            await asyncio.wait_for(task, timeout=2.0)


def test_enqueue_round_trip_lands_on_the_engine():
    server = make_server(sockdir())

    async def body(sock):
        resp = await _request(sock, {"op": "enqueue", "items": [{"pose": 1}]})
        assert resp == {"ok": True, "enqueued": 1}
        status = await _request(sock, {"op": "status"})
        assert status["ok"] is True
        assert status["status"]["pending"] == 1
        assert status["status"]["battery"] == 91

    asyncio.run(_serve(server, body))


def test_two_clients_enqueue_serialize_onto_one_queue():
    server = make_server(sockdir())

    async def body(sock):
        a, b = await asyncio.gather(
            _request(sock, {"op": "enqueue", "items": [{"pose": 1}]}),
            _request(sock, {"op": "enqueue", "items": [{"pose": 3}]}),
        )
        assert a["ok"] and b["ok"]
        # Both landed on one queue; serialization means no lost or corrupt request.
        limbs = {s.limb for s in server._engine._pending}
        assert limbs == {1, 3}

    asyncio.run(_serve(server, body))


def test_list_moves_returns_the_registry():
    server = make_server(sockdir())

    async def body(sock):
        resp = await _request(sock, {"op": "list_moves"})
        assert resp["moves"] == ["fist_pump", "sway", "wave"]

    asyncio.run(_serve(server, body))


def test_clear_and_stop_reach_the_engine():
    server = make_server(sockdir())

    async def body(sock):
        await _request(sock, {"op": "enqueue", "items": [{"pose": 1}, {"pose": 3}]})
        assert (await _request(sock, {"op": "clear"}))["ok"] is True
        assert (await _request(sock, {"op": "stop"}))["ok"] is True

    asyncio.run(_serve(server, body))


def test_second_start_with_a_live_socket_refuses():
    d = sockdir()
    server = make_server(d)

    async def body(sock):
        other = make_server(d)  # same dir -> same socket path
        with pytest.raises(DaemonAlreadyRunning):
            await other._acquire()

    asyncio.run(_serve(server, body))


def test_stale_socket_is_cleared_and_startup_proceeds():
    async def scenario():
        server = make_server(sockdir())
        server._socket_path.parent.mkdir(parents=True, exist_ok=True)
        server._socket_path.write_bytes(b"stale")  # a regular file, not a live daemon
        await server._acquire()  # detects it is not live, removes it, proceeds
        assert server._lock_path.exists()
        server._release()

    asyncio.run(scenario())


def test_malformed_json_returns_a_structured_error():
    server = make_server(sockdir())

    async def body(sock):
        reader, writer = await asyncio.open_unix_connection(str(sock))
        writer.write(b"not json at all\n")
        await writer.drain()
        resp = loads(await reader.readline())
        writer.close()
        assert resp["ok"] is False
        assert "JSON" in resp["error"]

    asyncio.run(_serve(server, body))


def test_shutdown_removes_the_socket_and_stops_the_server():
    async def scenario():
        server = make_server(sockdir())
        task = asyncio.ensure_future(server.serve())
        await _wait_up(server._socket_path)
        resp = await _request(server._socket_path, {"op": "shutdown"})
        assert resp["shutting_down"] is True
        await asyncio.wait_for(task, timeout=2.0)
        assert not server._socket_path.exists()

    asyncio.run(scenario())
