"""An MCP server exposing the daemon's control ops as tools (U6).

Each MCP tool maps to the same socket-protocol request the CLI sends (KTD1), so the two
interfaces cannot drift — the adapter here is thin. The `mcp` package is an optional
dependency (KTD8): importing this module never requires it; only `build_server` does, so
the core package installs and runs without `mcp`.

Launch it with the `carle-mcp` console script or `python -m carle.daemon.mcp_server`.
"""

from __future__ import annotations

from carle.daemon import client

# --- tool logic: plain, testable, no MCP dependency --------------------------------


def enqueue(items: list, requester=None) -> dict:
    """Enqueue a list of step items (moves or primitives) on the daemon."""
    return (requester or client.request)({"op": "enqueue", "items": items})


def clear(requester=None) -> dict:
    """Drop the daemon's pending queue."""
    return (requester or client.request)({"op": "clear"})


def stop(requester=None) -> dict:
    """Abort now and return the robot to neutral."""
    return (requester or client.request)({"op": "stop"})


def list_moves(requester=None) -> dict:
    """List the named moves the daemon knows."""
    return (requester or client.request)({"op": "list_moves"})


def status(requester=None) -> dict:
    """The robot's current state: connection, battery, current step, and queue."""
    return (requester or client.request)({"op": "status"})


# --- the MCP server (lazy import of the optional `mcp` package) ---------------------


def build_server(requester=None):
    """Build the FastMCP server. Raises a clear error when `mcp` is not installed."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover - exercised only without mcp installed
        raise RuntimeError(
            "the MCP server needs the optional 'mcp' package: pip install 'carle[mcp]'"
        ) from exc

    server = FastMCP("carle-robot")
    server.tool()(lambda items: enqueue(items, requester))
    server.tool()(lambda: clear(requester))
    server.tool()(lambda: stop(requester))
    server.tool()(lambda: list_moves(requester))
    server.resource("carle://status")(lambda: status(requester))
    return server


def main() -> None:  # pragma: no cover - runtime entry point, needs mcp + a live daemon
    build_server().run()


if __name__ == "__main__":  # pragma: no cover
    main()
