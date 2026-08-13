"""U4 — the local HTTP speak API, driven with fakes for the sink, stream, and animation.

No real PortAudio, `sounddevice`, or audio device is touched: a `FakeClipPlayer` and a
`FakeStreamPlayer` stand in for U1/U3, a passthrough `fake_stream_decode` replaces U2's
backend-loading decoder, and a `FakeAnimation` records the lifecycle hooks U5 will drive.
The module imports lazily (KTD9), so this file and `carle.speak.server` collect without the
`speak` extra installed.

Two levels are exercised: the transport-free `SpeakService` core directly, and a real
`ThreadingHTTPServer` on 127.0.0.1:0 hit with `http.client` (all stdlib) to prove the wire
path — chunked reads, the loopback bind, and the JSON error envelope.
"""

from __future__ import annotations

import http.client
import threading
import time

import pytest

from carle.speak.decode import DecodeError, RawPcmFormat
from carle.speak.server import SpeakServer, SpeakService
from carle.speak.sink import DeviceUnavailableError
from carle.speak.stream import Outcome

# --- fakes ----------------------------------------------------------------------------


class FakeAnimation:
    """Records the animation lifecycle hooks U5 will implement."""

    def __init__(self) -> None:
        self.starts = 0
        self.ends: list[Outcome] = []

    def on_start(self) -> None:
        self.starts += 1

    def on_end(self, outcome: Outcome) -> None:
        self.ends.append(outcome)


class FakeClipPlayer:
    """A clip player that never touches audio: prepare/play/stop are controllable."""

    def __init__(self) -> None:
        self.prepared: list[tuple[bytes, RawPcmFormat | None]] = []
        self.played: list[object] = []
        self.stopped = False
        self.raise_decode = False
        self.raise_unavailable = False
        self.block_play = False
        self._release = threading.Event()

    def prepare(self, data: bytes, *, declared: RawPcmFormat | None = None) -> object:
        if self.raise_decode:
            raise DecodeError("garbage bytes")
        if self.raise_unavailable:
            raise DeviceUnavailableError("output device 'JT_Speaker' is not a connected output")
        handle = (data, declared)
        self.prepared.append(handle)
        return handle

    def play(self, prepared: object) -> None:
        if self.block_play:
            self._release.wait(5)  # released by stop(): stands in for an interruptible clip
        self.played.append(prepared)

    def stop(self) -> None:
        self.stopped = True
        self._release.set()


class FakeStreamPlayer:
    """A stream player whose done-signal resolves on finish (COMPLETED) or stop (STOPPED)."""

    def __init__(self, *, unavailable: bool = False) -> None:
        self.enqueued: list[object] = []
        self.started = False
        self.finished = False
        self.stopped = False
        self._unavailable = unavailable
        self._done: Future = _resolvable()

    def start(self) -> None:
        if self._unavailable:
            raise DeviceUnavailableError("output device 'JT_Speaker' is not a connected output")
        self.started = True

    def enqueue(self, block: object) -> None:
        self.enqueued.append(block)

    def finish(self) -> None:
        self.finished = True
        self._done.resolve(Outcome.COMPLETED)

    def stop(self) -> None:
        self.stopped = True
        self._done.resolve(Outcome.STOPPED)

    def wait(self, timeout: float | None = None) -> Outcome:
        return self._done.result(timeout)


class _resolvable:
    """A tiny Future-like: resolves once, `result(timeout)` blocks until then."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._value: Outcome | None = None

    def resolve(self, value: Outcome) -> None:
        if self._value is None:
            self._value = value
            self._event.set()

    def result(self, timeout: float | None = None) -> Outcome:
        if not self._event.wait(timeout):
            raise TimeoutError("stream did not resolve")
        assert self._value is not None
        return self._value


# alias so annotations above read naturally
Future = _resolvable


def fake_stream_decode(chunks, *, source_format=None):
    """Passthrough decoder: each incoming body chunk becomes one enqueued block."""
    yield from chunks


# --- direct SpeakService (core) tests -------------------------------------------------


def test_clip_decodes_plays_and_fires_animation_hooks():
    clip = FakeClipPlayer()
    anim = FakeAnimation()
    service = SpeakService(clip_player=clip, animation=anim)

    resp = service.handle_clip(b"a whole clip")

    assert resp.status == 200
    assert resp.body == {"ok": True, "outcome": "completed"}
    assert clip.played == [(b"a whole clip", None)]  # decoded + played through the fake sink
    assert anim.starts == 1  # on_start fired when playback began
    assert anim.ends == [Outcome.COMPLETED]  # on_end fired on completion


def test_clip_with_declared_raw_format_is_passed_through():
    clip = FakeClipPlayer()
    service = SpeakService(clip_player=clip)

    params = {"format": ["raw"], "samplerate": ["16000"], "channels": ["1"], "dtype": ["int16"]}
    resp = service.handle_clip(b"\x00\x01", params=params)

    assert resp.status == 200
    (_data, declared) = clip.prepared[0]
    assert declared == RawPcmFormat(samplerate=16000, channels=1, dtype="int16")


def test_garbage_body_is_a_400_not_a_crash():
    clip = FakeClipPlayer()
    clip.raise_decode = True
    anim = FakeAnimation()
    service = SpeakService(clip_player=clip, animation=anim)

    resp = service.handle_clip(b"not audio")

    assert resp.status == 400
    assert resp.body["ok"] is False
    assert anim.starts == 0  # a rejected body never animates
    assert anim.ends == []


def test_unavailable_device_errors_without_falling_back():
    clip = FakeClipPlayer()
    clip.raise_unavailable = True
    anim = FakeAnimation()
    service = SpeakService(clip_player=clip, animation=anim)

    resp = service.handle_clip(b"a clip")

    assert resp.status == 503  # reported unavailable...
    assert "not a connected output" in resp.body["error"]
    assert clip.played == []  # ...and NOT played to any fallback device (AE2, AE5)
    assert anim.starts == 0


def test_second_playback_while_busy_returns_409():
    clip = FakeClipPlayer()
    clip.block_play = True  # the first clip stays "playing" until stopped
    service = SpeakService(clip_player=clip)

    first_done = threading.Event()

    def first() -> None:
        service.handle_clip(b"first")
        first_done.set()

    thread = threading.Thread(target=first)
    thread.start()
    _wait_until(lambda: clip.block_play and len(clip.prepared) == 1)

    # A second request lands while the first still holds the device.
    busy = service.handle_clip(b"second")
    assert busy.status == 409
    assert busy.body["ok"] is False
    assert "busy" in busy.body["error"]

    clip.stop()  # release the first so the test can join cleanly
    assert first_done.wait(5)
    thread.join(5)


def test_stop_interrupts_in_flight_clip_and_fires_on_end():
    clip = FakeClipPlayer()
    clip.block_play = True
    anim = FakeAnimation()
    service = SpeakService(clip_player=clip, animation=anim)

    result: dict = {}

    def play() -> None:
        result["resp"] = service.handle_clip(b"a long clip")

    thread = threading.Thread(target=play)
    thread.start()
    _wait_until(lambda: anim.starts == 1)  # playback is under way

    stop_resp = service.handle_stop()

    assert stop_resp.status == 200
    assert stop_resp.body == {"ok": True, "stopped": True}
    thread.join(5)
    assert clip.stopped is True
    assert result["resp"].body["outcome"] == "stopped"
    assert anim.ends == [Outcome.STOPPED]  # returned to neutral on stop (R7)


def test_stop_with_nothing_playing_is_a_noop_ok():
    service = SpeakService(clip_player=FakeClipPlayer())
    resp = service.handle_stop()
    assert resp.status == 200
    assert resp.body == {"ok": True, "stopped": False, "reason": "nothing is playing"}


def test_stream_reads_chunks_incrementally_into_the_player():
    player = FakeStreamPlayer()
    anim = FakeAnimation()
    service = SpeakService(
        stream_factory=lambda: player,
        stream_decode=fake_stream_decode,
        animation=anim,
    )

    body = [b"chunk-0", b"chunk-1", b"chunk-2"]
    resp = service.handle_stream(iter(body))

    assert resp.status == 200
    assert resp.body["outcome"] == "completed"
    assert player.started is True
    assert player.enqueued == body  # each chunk fed through as a block, in order (AE4)
    assert player.finished is True
    assert anim.starts == 1
    assert anim.ends == [Outcome.COMPLETED]


def test_stream_on_unavailable_device_errors_and_does_not_animate():
    player = FakeStreamPlayer(unavailable=True)
    anim = FakeAnimation()
    service = SpeakService(
        stream_factory=lambda: player, stream_decode=fake_stream_decode, animation=anim
    )

    resp = service.handle_stream(iter([b"chunk"]))

    assert resp.status == 503
    assert anim.starts == 0  # never animated for an unavailable device (AE5)


def test_stream_decode_error_is_a_400():
    player = FakeStreamPlayer()

    def boom(chunks, *, source_format=None):
        yield from ()
        raise DecodeError("corrupt stream")

    service = SpeakService(stream_factory=lambda: player, stream_decode=boom)

    resp = service.handle_stream(iter([b"bad"]))

    assert resp.status == 400
    assert player.stopped is True  # the player was torn down on the decode failure


# --- real loopback HTTP server tests --------------------------------------------------


@pytest.fixture
def running_server():
    """Start a real ThreadingHTTPServer on 127.0.0.1:0 with injected fakes."""
    clip = FakeClipPlayer()
    stream = FakeStreamPlayer()
    anim = FakeAnimation()
    service = SpeakService(
        clip_player=clip,
        stream_factory=lambda: stream,
        stream_decode=fake_stream_decode,
        animation=anim,
    )
    server = SpeakServer(service, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, clip, stream, anim
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def test_loopback_only_bind_is_enforced():
    with pytest.raises(ValueError, match="loopback only"):
        SpeakServer(SpeakService(), host="0.0.0.0", port=0)


def test_http_clip_round_trip(running_server):
    server, clip, _stream, anim = running_server
    host, port = server.address

    conn = http.client.HTTPConnection(host, port)
    conn.request("POST", "/speak/clip", body=b"a real wire clip")
    resp = conn.getresponse()
    payload = resp.read()
    conn.close()

    assert resp.status == 200
    assert b'"outcome": "completed"' in payload
    assert clip.played == [(b"a real wire clip", None)]
    assert anim.starts == 1


def test_http_stream_chunked_arrives_incrementally(running_server):
    server, _clip, stream, _anim = running_server
    host, port = server.address

    conn = http.client.HTTPConnection(host, port)
    # Chunked transfer-encoding: an iterable body makes http.client stream it.
    conn.request("POST", "/speak/stream", body=iter([b"one", b"two", b"three"]))
    resp = conn.getresponse()
    resp.read()
    conn.close()

    assert resp.status == 200
    assert stream.enqueued == [b"one", b"two", b"three"]  # fed as the chunks arrived (AE4)
    assert stream.finished is True


def test_http_garbage_body_is_4xx_not_a_crash(running_server):
    server, clip, _stream, _anim = running_server
    clip.raise_decode = True
    host, port = server.address

    conn = http.client.HTTPConnection(host, port)
    conn.request("POST", "/speak/clip", body=b"\x00\xffnot audio at all")
    resp = conn.getresponse()
    payload = resp.read()
    conn.close()

    assert resp.status == 400
    assert b'"ok": false' in payload


def test_http_unknown_path_is_404(running_server):
    server, *_ = running_server
    host, port = server.address

    conn = http.client.HTTPConnection(host, port)
    conn.request("POST", "/nope", body=b"")
    resp = conn.getresponse()
    resp.read()
    conn.close()

    assert resp.status == 404


def test_http_get_is_405(running_server):
    server, *_ = running_server
    host, port = server.address

    conn = http.client.HTTPConnection(host, port)
    conn.request("GET", "/speak/clip")
    resp = conn.getresponse()
    resp.read()
    conn.close()

    assert resp.status == 405


# --- helpers --------------------------------------------------------------------------


def _wait_until(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("condition not met within timeout")
