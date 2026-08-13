"""A synchronous client for the daemon's control socket (U5).

The CLI is a thin, blocking client: it opens the Unix socket, sends one request, reads
one response, and returns. The MCP server (U6) reuses the same request shape over the
same socket, so the two interfaces never drift.
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

from carle.daemon import protocol
from carle.daemon.server import DEFAULT_SOCKET_PATH, UNIX_SOCKETS, is_daemon_live


class NoDaemonError(Exception):
    """Raised when no daemon is accepting connections on the socket."""


def request(
    req: dict,
    socket_path: Path | str = DEFAULT_SOCKET_PATH,
    *,
    timeout: float | None = None,
) -> dict:
    """Send one request to the daemon and return its response.

    ``timeout`` bounds the whole connect+send+read exchange. It defaults to None (wait
    indefinitely, the CLI's behavior) but a caller on a latency-sensitive path — the speak
    animation runs on the HTTP worker thread while it holds the playback lock — passes a
    short value so a hung or wedged daemon cannot block it forever.
    """
    if not UNIX_SOCKETS:
        # POSIX-only subsystem: no daemon can exist here, so report it like a missing one.
        raise NoDaemonError("the carle daemon requires Unix domain sockets and is POSIX-only")

    async def go() -> dict:
        try:
            reader, writer = await asyncio.open_unix_connection(str(socket_path))
        except (ConnectionRefusedError, FileNotFoundError, OSError) as exc:
            raise NoDaemonError(
                f"no daemon at {socket_path} — is it running? start it with `carle daemon start`"
            ) from exc
        writer.write(protocol.dumps(req))
        await writer.drain()
        line = await reader.readline()
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
        if not line:
            raise NoDaemonError("the daemon closed the connection without replying")
        return protocol.loads(line)

    async def run() -> dict:
        if timeout is None:
            return await go()
        return await asyncio.wait_for(go(), timeout)

    try:
        return asyncio.run(run())
    except (TimeoutError, asyncio.TimeoutError) as exc:
        raise NoDaemonError(f"daemon did not respond within {timeout}s") from exc


def daemon_live(socket_path: Path | str = DEFAULT_SOCKET_PATH) -> bool:
    """True when a daemon is holding the link (used by the KTD10 refusal guard)."""
    return asyncio.run(is_daemon_live(socket_path))
