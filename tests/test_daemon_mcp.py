"""U6 — the MCP server's tool mapping, tested without the `mcp` package or a daemon."""

from __future__ import annotations

import importlib

from carle.daemon import mcp_server


class FakeRequester:
    def __init__(self, reply: dict) -> None:
        self.reply = reply
        self.requests: list[dict] = []

    def __call__(self, req: dict) -> dict:
        self.requests.append(req)
        return self.reply


def test_each_tool_maps_to_the_correct_socket_request():
    fr = FakeRequester({"ok": True})
    mcp_server.enqueue([{"move": "wave"}], requester=fr)
    mcp_server.clear(requester=fr)
    mcp_server.stop(requester=fr)
    mcp_server.list_moves(requester=fr)
    assert fr.requests == [
        {"op": "enqueue", "items": [{"move": "wave"}]},
        {"op": "clear"},
        {"op": "stop"},
        {"op": "list_moves"},
    ]


def test_enqueue_returns_the_daemon_reply():
    fr = FakeRequester({"ok": True, "enqueued": 6})
    assert mcp_server.enqueue([{"move": "wave"}], requester=fr) == {"ok": True, "enqueued": 6}


def test_status_resource_returns_the_state_snapshot():
    snapshot = {"ok": True, "status": {"connected": True, "battery": 80, "pending": 0}}
    fr = FakeRequester(snapshot)
    assert mcp_server.status(requester=fr) == snapshot
    assert fr.requests == [{"op": "status"}]


def test_list_moves_passes_through_the_registry_names():
    fr = FakeRequester({"ok": True, "moves": ["fist_pump", "sway", "wave"]})
    assert mcp_server.list_moves(requester=fr)["moves"] == ["fist_pump", "sway", "wave"]


def test_importing_the_module_does_not_require_mcp():
    # The core package must install and import without the optional `mcp` dependency.
    module = importlib.import_module("carle.daemon.mcp_server")
    assert hasattr(module, "enqueue")
    assert hasattr(module, "build_server")


def test_build_server_reports_a_clear_error_without_mcp():
    # `mcp` is not a base dependency, so build_server raises a clear install hint.
    try:
        import mcp.server.fastmcp  # noqa: F401
    except ImportError:
        import pytest

        with pytest.raises(RuntimeError, match="pip install 'carle\\[mcp\\]'"):
            mcp_server.build_server()
