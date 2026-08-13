"""U6 — the `carle speak-server` CLI surface and its compose helper, with injected fakes.

No real audio device, no real loopback bind, no real daemon socket: the service, server,
and animation factories are injected (`build_speak_server`) or monkeypatched (through
`carle.cli.main`), so these are unit tests. Real playback is the manual hardware smoke.
"""

from __future__ import annotations

import pytest

from carle.cli import build_parser, main
from carle.speak.server import DEFAULT_PORT, Outcome, SpeakService
from carle.speak.service import build_speak_server

# --- fakes ----------------------------------------------------------------------------


class FakeService:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class FakeSpeakServer:
    """A stand-in for `SpeakServer`: records the composed service and its kwargs."""

    def __init__(self, service, **kwargs):
        self.service = service
        self.kwargs = kwargs


class FakeAnimation:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class ServingServer:
    """A fake the CLI can `run_speak_server` on: Ctrl-C the moment it serves."""

    address = ("127.0.0.1", 8081)

    def __init__(self):
        self.shutdown_called = False
        self.closed = False

    def serve_forever(self):
        raise KeyboardInterrupt

    def shutdown(self):
        self.shutdown_called = True

    def server_close(self):
        self.closed = True


# --- build_speak_server: the wiring threads device / socket / port ---------------------


def test_build_threads_device_socket_and_port_and_builds_an_animation():
    captured: dict = {}

    def service_factory(**kw):
        captured["service"] = kw
        return FakeService(**kw)

    def server_factory(service, **kw):
        captured["server_service"] = service
        captured["server"] = kw
        return FakeSpeakServer(service, **kw)

    def animation_factory(**kw):
        captured["animation"] = kw
        return FakeAnimation(**kw)

    server = build_speak_server(
        device_name="JT_Speaker",
        port=9099,
        socket_path="/tmp/carle.sock",
        animate=True,
        service_factory=service_factory,
        server_factory=server_factory,
        animation_factory=animation_factory,
    )

    # The daemon socket reaches the animation; the device reaches the service; the port
    # reaches the server; and the built animation is the one wired into the service.
    assert captured["animation"] == {"socket_path": "/tmp/carle.sock"}
    assert captured["service"]["device_name"] == "JT_Speaker"
    assert isinstance(captured["service"]["animation"], FakeAnimation)
    assert captured["server"] == {"port": 9099}
    assert captured["server_service"] is server.service


def test_build_with_no_animate_builds_no_animation():
    captured: dict = {}

    def service_factory(**kw):
        captured["service"] = kw
        return FakeService(**kw)

    def animation_factory(**kw):  # pragma: no cover - must never be called
        raise AssertionError("animate=False must not build an animation")

    build_speak_server(
        device_name="Other",
        port=DEFAULT_PORT,
        socket_path="/tmp/carle.sock",
        animate=False,
        service_factory=service_factory,
        server_factory=FakeSpeakServer,
        animation_factory=animation_factory,
    )

    assert captured["service"]["device_name"] == "Other"
    assert captured["service"]["animation"] is None


# --- carle speak-server: parses and constructs through the injected build --------------


def test_speak_server_parses_device_port_socket_and_builds(monkeypatch, capsys):
    captured: dict = {}

    def fake_build(**kwargs):
        captured.update(kwargs)
        return ServingServer()

    monkeypatch.setattr("carle.speak.service.require_speak_backends", lambda: None)
    monkeypatch.setattr("carle.speak.service.build_speak_server", fake_build)

    code = main(
        ["speak-server", "--device", "MySpeaker", "--port", "9099", "--socket", "/tmp/s.sock"]
    )

    assert code == 0
    assert captured["device_name"] == "MySpeaker"
    assert captured["port"] == 9099
    assert str(captured["socket_path"]) == "/tmp/s.sock"
    assert captured["animate"] is True
    assert "listening on http://127.0.0.1:8081" in capsys.readouterr().out


def test_speak_server_no_animate_disables_animation(monkeypatch):
    captured: dict = {}

    def fake_build(**kwargs):
        captured.update(kwargs)
        return ServingServer()

    monkeypatch.setattr("carle.speak.service.require_speak_backends", lambda: None)
    monkeypatch.setattr("carle.speak.service.build_speak_server", fake_build)

    code = main(["speak-server", "--no-animate"])

    assert code == 0
    assert captured["animate"] is False
    assert captured["device_name"] == "JT_Speaker"  # the default target device


def test_speak_server_shuts_down_and_closes_on_interrupt(monkeypatch):
    served = ServingServer()
    monkeypatch.setattr("carle.speak.service.require_speak_backends", lambda: None)
    monkeypatch.setattr("carle.speak.service.build_speak_server", lambda **_: served)

    assert main(["speak-server"]) == 0
    # A clean Ctrl-C path releases the socket rather than leaving the port bound.
    assert served.shutdown_called and served.closed


# --- missing audio extra degrades to one clear line, not a traceback -------------------


def test_missing_audio_extra_reports_install_hint(monkeypatch, capsys):
    def boom(**_kwargs):
        raise ImportError("No module named 'sounddevice'")

    monkeypatch.setattr("carle.speak.service.build_speak_server", boom)

    code = main(["speak-server"])

    assert code == 1
    err = capsys.readouterr().err
    assert "pip install 'carle[speak]'" in err
    assert "Traceback" not in err


def test_missing_backends_probe_reports_install_hint(monkeypatch, capsys):
    # The real degradation path: the audio backends are lazy everywhere else, so the startup
    # probe is what actually detects a missing extra. If it raises ImportError, the CLI must
    # print the one-line hint and exit 1 — not start the server and fail on the first request.
    def boom():
        raise ImportError("No module named 'sounddevice'")

    monkeypatch.setattr("carle.speak.service.require_speak_backends", boom)
    # If the probe short-circuits correctly, build is never reached.
    monkeypatch.setattr(
        "carle.speak.service.build_speak_server",
        lambda **_: pytest.fail("build must not run when the backends are missing"),
    )

    code = main(["speak-server"])

    assert code == 1
    err = capsys.readouterr().err
    assert "pip install 'carle[speak]'" in err
    assert "Traceback" not in err


def test_port_in_use_reports_a_clean_error_not_a_traceback(monkeypatch, capsys):
    monkeypatch.setattr("carle.speak.service.require_speak_backends", lambda: None)

    def boom(**_kwargs):
        raise OSError("[Errno 48] Address already in use")

    monkeypatch.setattr("carle.speak.service.build_speak_server", boom)

    code = main(["speak-server"])

    assert code == 1
    err = capsys.readouterr().err
    assert "could not start the speak server" in err
    assert "Address already in use" in err
    assert "Traceback" not in err


# --- the subcommand is registered (appears in help / parses) ---------------------------


def test_speak_server_subcommand_is_registered():
    parser = build_parser()
    args = parser.parse_args(["speak-server"])
    assert args.command == "speak-server"
    # The documented defaults are in place without any flag.
    assert args.device == "JT_Speaker"
    assert args.port == DEFAULT_PORT
    assert args.no_animate is False


def test_speak_server_shows_in_top_level_help(capsys):
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--help"])
    assert "speak-server" in capsys.readouterr().out


def test_build_parser_works_without_constructing_audio_backends():
    # The U7 "core install unaffected" smoke: building the parser (and so `carle --help`)
    # never constructs a sink/stream, so it does not need the `carle[speak]` extra.
    parser = build_parser()
    assert parser.parse_args(["speak-server"]).command == "speak-server"


# --- F1/F2 shape: a clip and a stream both reach a fake sink and animate ----------------


class FakeClipPlayer:
    """The U4 clip seam: record what got played; never touch a device."""

    def __init__(self):
        self.played: list[bytes] = []

    def prepare(self, data, *, declared=None):
        return data

    def play(self, prepared):
        self.played.append(prepared)

    def stop(self):
        return None


class FakeStream:
    """The U3 stream seam: record enqueued blocks; complete immediately."""

    def __init__(self):
        self.blocks: list = []
        self.started = False

    def start(self):
        self.started = True

    def enqueue(self, block):
        self.blocks.append(block)

    def finish(self):
        return None

    def stop(self):
        return None

    def wait(self, timeout=None):
        return Outcome.COMPLETED


class RecordingAnimation:
    """The U5 hook: record the start/end edges the service drives around playback."""

    def __init__(self):
        self.events: list = []

    def on_start(self):
        self.events.append("start")

    def on_end(self, outcome):
        self.events.append(("end", outcome))


def test_clip_and_stream_both_reach_the_sink_and_trigger_animation():
    animation = RecordingAnimation()
    clip = FakeClipPlayer()
    stream = FakeStream()

    service = SpeakService(
        clip_player=clip,
        stream_factory=lambda: stream,
        stream_decode=lambda body, *, source_format=None: iter([b"blockA", b"blockB"]),
        animation=animation,
    )

    # F1: a finished clip reaches the sink and drives a full animation cycle.
    clip_resp = service.handle_clip(b"wav-bytes")
    assert clip_resp.status == 200
    assert clip.played == [b"wav-bytes"]

    # F2: a streamed body reaches the stream player incrementally and animates too.
    stream_resp = service.handle_stream([b"chunk"])
    assert stream_resp.status == 200
    assert stream.blocks == [b"blockA", b"blockB"]

    # Both playbacks bracketed by exactly one start + one end apiece.
    assert animation.events.count("start") == 2
    ends = [e for e in animation.events if isinstance(e, tuple)]
    assert ends == [("end", Outcome.COMPLETED), ("end", Outcome.COMPLETED)]
