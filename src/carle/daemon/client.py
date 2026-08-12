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
from carle.daemon.server import DEFAULT_SOCKET_PATH, is_daemon_live


class NoDaemonError(Exception):
    """Raised when no daemon is accepting connections on the socket."""


def request(req: dict, socket_path: Path | str = DEFAULT_SOCKET_PATH) -> dict:
    """Send one request to the daemon and return its response."""

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

    return asyncio.run(go())


def daemon_live(socket_path: Path | str = DEFAULT_SOCKET_PATH) -> bool:
    """True when a daemon is holding the link (used by the KTD10 refusal guard)."""
    return asyncio.run(is_daemon_live(socket_path))
