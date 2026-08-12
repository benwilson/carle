"""U5 — the CLI daemon and queue verbs, and the KTD10 coexistence guard.

`main` takes an injectable `requester` (the socket client) and `daemon_live` check, so
these tests exercise the CLI's request-building, output, and guards without a running
daemon or any Bluetooth.
"""

from __future__ import annotations

from carle.cli import main
from carle.daemon.client import NoDaemonError


class FakeRequester:
    def __init__(self, reply: dict) -> None:
        self.reply = reply
        self.requests: list[dict] = []

    def __call__(self, req: dict) -> dict:
        self.requests.append(req)
        return self.reply


def no_daemon(_req: dict) -> dict:
    raise NoDaemonError("no daemon at .carle/daemon.sock — is it running?")


def test_queue_move_sends_an_enqueue_request(capsys):
    fr = FakeRequester({"ok": True, "enqueued": 6})
    code = main(["queue", "wave"], requester=fr, daemon_live=lambda: False)
    assert code == 0
    assert fr.requests == [{"op": "enqueue", "items": [{"move": "wave"}]}]
    assert "enqueued 6" in capsys.readouterr().out


def test_queue_parses_primitive_tokens(capsys):
    fr = FakeRequester({"ok": True, "enqueued": 3})
    main(["queue", "pose:5", "pause:1.0", "say:hello"], requester=fr, daemon_live=lambda: False)
    assert fr.requests[0]["items"] == [{"pose": 5}, {"pause": 1.0}, {"say": "hello"}]


def test_queue_parses_face_tokens(capsys):
    fr = FakeRequester({"ok": True, "enqueued": 2})
    main(["queue", "face:39", "face:clear"], requester=fr, daemon_live=lambda: False)
    # face:N holds an LED expression; face:clear drops the hold (code 0).
    assert fr.requests[0]["items"] == [{"face": 39}, {"face": 0}]


def test_queue_rejects_an_unknown_step_kind(capsys):
    fr = FakeRequester({"ok": True})
    code = main(["queue", "spin:9"], requester=fr, daemon_live=lambda: False)
    assert code == 1
    assert "unknown step kind" in capsys.readouterr().err
    assert fr.requests == []  # nothing sent


def test_clear_and_stop_send_their_requests():
    fr = FakeRequester({"ok": True})
    assert main(["clear"], requester=fr, daemon_live=lambda: False) == 0
    assert main(["stop"], requester=fr, daemon_live=lambda: False) == 0
    assert [r["op"] for r in fr.requests] == ["clear", "stop"]


def test_status_renders_the_daemon_reply(capsys):
    reply = {
        "ok": True,
        "status": {
            "connected": True,
            "battery": 80,
            "current": "MovementStep",
            "pending": 2,
            "spawns": 0,
        },
    }
    main(["status"], requester=FakeRequester(reply), daemon_live=lambda: False)
    out = capsys.readouterr().out
    assert "connected" in out and "80%" in out and "2 queued" in out


def test_daemon_stop_sends_shutdown():
    fr = FakeRequester({"ok": True, "shutting_down": True})
    main(["daemon", "stop"], requester=fr, daemon_live=lambda: False)
    assert fr.requests == [{"op": "shutdown"}]


def test_a_verb_with_no_daemon_prints_a_clear_error(capsys):
    code = main(["queue", "wave"], requester=no_daemon, daemon_live=lambda: False)
    assert code == 1
    assert "is it running" in capsys.readouterr().err


def test_send_refuses_while_the_daemon_holds_the_link(capsys):
    # The guard fires before any backend is built, so no Bluetooth is touched.
    code = main(
        ["send", "media_music", "--address", "AA:BB"],
        authorization=None,
        daemon_live=lambda: True,
    )
    assert code == 1
    assert "daemon holds the link" in capsys.readouterr().err


def test_dry_run_send_is_not_blocked_by_the_daemon(capsys):
    # A dry run touches no radio, so the coexistence guard must not block it.
    code = main(["send", "media_music", "--dry-run"], authorization=None, daemon_live=lambda: True)
    assert code == 0
    assert "B3 02 03 00 03 AA" in capsys.readouterr().out


def test_daemon_start_refuses_cleanly_without_unix_sockets(capsys, monkeypatch):
    # On a platform without Unix domain sockets the daemon cannot run; starting it must
    # print a clear POSIX-only error and exit 1, never crash with an AttributeError.
    monkeypatch.setattr("carle.daemon.server.UNIX_SOCKETS", False)
    code = main(["daemon", "start", "AA:BB"], requester=no_daemon, daemon_live=lambda: False)
    assert code == 1
    assert "POSIX-only" in capsys.readouterr().err
