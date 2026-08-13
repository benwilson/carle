"""The local HTTP API — accept a clip or a live stream and drive playback (U4).

Any app on the machine, in any language, POSTs already-rendered audio to a loopback HTTP
server and the robot voices it (R1, R2, R3). Text is never accepted; the caller renders
the audio and hands over the bytes. Three endpoints:

- ``POST /speak/clip`` — the request body is a whole finished clip. It is decoded and
  resampled to the device rate (`decode_clip`) and played through the sink with one
  blocking write.
- ``POST /speak/stream`` — the request body is a live, incrementally-delivered compressed
  stream. The body is read in chunks and fed through the streaming decoder into a
  `StreamPlayer`; the bounded queue plus the blocking body read make TCP flow control the
  backpressure (KTD8).
- ``POST /speak/stop`` — interrupt the in-flight clip or stream and return the robot to
  neutral (R7).

The server is **synchronous / thread-per-request** on the stdlib `ThreadingHTTPServer`
(no new dependency), bound to loopback only (127.0.0.1). A **single playback lock** (KTD8)
guards the device: a second clip/stream request while one is active gets HTTP 409 rather
than two playbacks racing one device or a shared animation state. The target device is
resolved server-side to a PortAudio index; if it is not a connected output the request
fails loudly and the service never falls back to the host default (AE2, AE5).

Three seams are injected so tests use fakes and U6 can wire the real U1/U2/U3 components
and U5's animation coordinator:

- a **clip player** (`ClipPlayer`: `prepare` -> `play` -> `stop`) — decode + resolve, then
  a blocking write, with a best-effort cancel hook;
- a **stream-player factory** (`() -> StreamLike`) building one `StreamPlayer` per request;
- an **animation lifecycle hook** (`AnimationHook`: `on_start()` / `on_end(outcome)`),
  defaulting to a no-op. The server calls `on_start()` when playback begins and
  `on_end(outcome)` when it completes, is stopped, or the device dies.

No audio backend is imported at module load: the default clip player and stream factory
build the lazy U1/U3 components, which import `sounddevice` and friends only when a real
playback runs (KTD9). So this module imports — and its tests collect — with no PortAudio.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import TimeoutError as FuturesTimeout
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Protocol
from urllib.parse import parse_qs, urlparse

from carle.speak.decode import (
    DEFAULT_CHANNELS,
    DecodeError,
    RawPcmFormat,
    decode_clip,
    stream_pcm_blocks,
)
from carle.speak.sink import AudioSink, DeviceUnavailableError
from carle.speak.stream import Outcome, StreamPlayer

_log = logging.getLogger(__name__)

#: The service binds loopback only — never a routable address. A caller is a local app.
LOOPBACK = "127.0.0.1"
LOOPBACK_HOSTS = frozenset({LOOPBACK, "localhost", "::1"})
DEFAULT_PORT = 8081

#: The robot advertises its A2DP audio sink under this Bluetooth name — the default target.
DEFAULT_DEVICE_NAME = "JT_Speaker"

#: The A2DP sink is commonly 44.1 kHz stereo; the default decode target matches (KTD6).
DEFAULT_SAMPLERATE = 44100

#: How long a body read on the wire is drained before a chunked stream read gives up.
_READ_SIZE = 65536
#: A stream request waits this long for the device to drain before forcing a stop.
DEFAULT_STREAM_TIMEOUT = 300.0
#: How long to wait for the producer thread to unwind after playback ends.
_JOIN_TIMEOUT = 5.0


# --- injectable seams -----------------------------------------------------------------


class ClipPlayer(Protocol):
    """Decode + play a whole clip, split so animation wraps only real playback.

    `prepare` decodes the bytes and resolves the target device — raising `DecodeError`
    (bad bytes) or `DeviceUnavailableError` (no such connected output) *before* any audio
    or animation. `play` performs the blocking write of a prepared handle. `stop` cancels
    an in-flight `play` best-effort (a blocking PortAudio write is not truly interruptible,
    so the real implementation's `stop` is a documented no-op; a fake can release it).
    """

    def prepare(self, data: bytes, *, declared: RawPcmFormat | None = None) -> object: ...

    def play(self, prepared: object) -> None: ...

    def stop(self) -> None: ...


class StreamLike(Protocol):
    """The slice of a `StreamPlayer` the server drives (U3)."""

    def start(self) -> None: ...

    def enqueue(self, block: object) -> None: ...

    def finish(self) -> None: ...

    def stop(self) -> None: ...

    def wait(self, timeout: float | None = None) -> Outcome: ...


class AnimationHook(Protocol):
    """The animation lifecycle U6 wires U5's coordinator into.

    `on_start()` fires once when playback begins; `on_end(outcome)` fires once when it ends
    — whether the clip/stream `COMPLETED`, was `STOPPED`, or the device `DIED`. The server
    only ever calls these around real playback, never around a rejected (4xx) request.
    """

    def on_start(self) -> None: ...

    def on_end(self, outcome: Outcome) -> None: ...


class NoopAnimation:
    """The default animation hook: does nothing (audio plays, nothing animates)."""

    def on_start(self) -> None:
        return None

    def on_end(self, outcome: Outcome) -> None:
        return None


#: A block ready to hand to `StreamPlayer.enqueue` (a PCM ndarray, or a test stand-in).
StreamDecode = Callable[..., Iterator[object]]


class SinkClipPlayer:
    """The default `ClipPlayer`: decode with U2, play with U1's `AudioSink`.

    `prepare` decodes and normalizes to the device rate/channels, then resolves the sink's
    index so a missing device raises `DeviceUnavailableError` before `on_start`. `play`
    hands the normalized buffer to the sink's blocking write.
    """

    def __init__(
        self,
        sink: AudioSink,
        *,
        samplerate: int = DEFAULT_SAMPLERATE,
        channels: int = DEFAULT_CHANNELS,
        decode: Callable[..., Any] = decode_clip,
    ) -> None:
        self._sink = sink
        self._samplerate = samplerate
        self._channels = channels
        self._decode = decode

    def prepare(self, data: bytes, *, declared: RawPcmFormat | None = None) -> object:
        pcm = self._decode(
            data,
            target_samplerate=self._samplerate,
            target_channels=self._channels,
            declared=declared,
        )
        self._sink.resolve()  # raise DeviceUnavailableError now — never fall back (AE5)
        return pcm

    def play(self, prepared: object) -> None:
        self._sink.play(
            prepared.samples,  # type: ignore[attr-defined]
            samplerate=prepared.samplerate,  # type: ignore[attr-defined]
            channels=prepared.channels,  # type: ignore[attr-defined]
        )

    def stop(self) -> None:
        # A blocking PortAudio write cannot be interrupted from another thread; real
        # cancellation is a best-effort no-op. The stop endpoint still returns the robot to
        # neutral once the clip finishes. Tests inject a fake whose stop releases play().
        return None


# --- HTTP response envelope -----------------------------------------------------------


@dataclass(frozen=True)
class Response:
    """A dispatch result: an HTTP status and a JSON body (the daemon's error envelope)."""

    status: int
    body: dict


def _ok(**fields: Any) -> Response:
    return Response(200, {"ok": True, **fields})


def _err(status: int, message: str, **fields: Any) -> Response:
    return Response(status, {"ok": False, "error": message, **fields})


# --- the service (the testable core) --------------------------------------------------


class SpeakService:
    """Dispatch clip/stream/stop requests against the injected playback components.

    This is the transport-free core: `handle_clip`, `handle_stream`, and `handle_stop`
    take already-parsed request data and return a `Response`. `SpeakServer` wraps it in the
    stdlib HTTP server, but the core is exercised directly (and through a real loopback
    server) in tests. The single playback lock is held for the whole of one playback, so a
    second speak request while one is active returns 409 (KTD8).
    """

    def __init__(
        self,
        *,
        device_name: str = DEFAULT_DEVICE_NAME,
        samplerate: int = DEFAULT_SAMPLERATE,
        channels: int = DEFAULT_CHANNELS,
        clip_player: ClipPlayer | None = None,
        stream_factory: Callable[[], StreamLike] | None = None,
        stream_decode: StreamDecode | None = None,
        animation: AnimationHook | None = None,
        stream_timeout: float = DEFAULT_STREAM_TIMEOUT,
    ) -> None:
        self._device_name = device_name
        self._samplerate = samplerate
        self._channels = channels
        self._clip_player = clip_player or SinkClipPlayer(
            AudioSink(device_name), samplerate=samplerate, channels=channels
        )
        self._stream_factory = stream_factory or self._default_stream_factory
        self._stream_decode = stream_decode or self._default_stream_decode
        self._animation = animation or NoopAnimation()
        self._stream_timeout = stream_timeout

        #: The single playback gate (KTD8): held non-blocking for one playback at a time.
        self._playback_lock = threading.Lock()
        #: Guards the active-stop handle and the stop-requested flag, both cross-thread.
        self._active_lock = threading.Lock()
        self._active_stop: Callable[[], None] | None = None
        self._stop_requested = False

    # --- defaults (build the real, lazy U1/U3 components) --------------------------

    def _default_stream_factory(self) -> StreamLike:
        return StreamPlayer(self._device_name, samplerate=self._samplerate, channels=self._channels)

    def _default_stream_decode(
        self, chunks: Iterable[bytes], *, source_format: str | None = None
    ) -> Iterator[object]:
        for buf in stream_pcm_blocks(
            chunks,
            target_samplerate=self._samplerate,
            target_channels=self._channels,
            source_format=source_format,
        ):
            yield buf.samples

    # --- the playback gate --------------------------------------------------------

    def _acquire(self) -> bool:
        return self._playback_lock.acquire(blocking=False)

    def _release(self) -> None:
        with self._active_lock:
            self._active_stop = None
            self._stop_requested = False
        self._playback_lock.release()

    def _arm_stop(self, stop: Callable[[], None]) -> None:
        with self._active_lock:
            self._active_stop = stop
            self._stop_requested = False

    # --- clip ---------------------------------------------------------------------

    def handle_clip(
        self, data: bytes, *, params: dict | None = None, headers: Any = None
    ) -> Response:
        """Decode a whole clip and play it through the sink (F1)."""
        try:
            declared = _parse_declared(params or {}, headers)
        except ValueError as exc:
            return _err(400, f"bad format parameters: {exc}")
        if not self._acquire():
            return _err(409, "busy: a playback is already active")
        try:
            return self._play_clip(data, declared)
        finally:
            self._release()

    def _play_clip(self, data: bytes, declared: RawPcmFormat | None) -> Response:
        self._arm_stop(self._clip_player.stop)
        try:
            prepared = self._clip_player.prepare(data, declared=declared)
        except DecodeError as exc:
            return _err(400, f"could not decode audio: {exc}")
        except DeviceUnavailableError as exc:
            return _err(503, str(exc))
        self._animation.on_start()
        try:
            self._clip_player.play(prepared)
        except DeviceUnavailableError as exc:
            self._animation.on_end(Outcome.DIED)
            return _err(503, str(exc))
        except Exception as exc:  # noqa: BLE001 - a lost device mid-clip must not crash
            self._animation.on_end(Outcome.DIED)
            return _err(500, f"playback failed: {exc}")
        with self._active_lock:
            outcome = Outcome.STOPPED if self._stop_requested else Outcome.COMPLETED
        self._animation.on_end(outcome)
        return _ok(outcome=outcome.value)

    # --- stream -------------------------------------------------------------------

    def handle_stream(
        self, body: Iterable[bytes], *, params: dict | None = None, headers: Any = None
    ) -> Response:
        """Read the body incrementally into a `StreamPlayer` and play as it arrives (F2)."""
        source_format = _get(params or {}, headers, "codec", "X-Speak-Codec")
        if not self._acquire():
            return _err(409, "busy: a playback is already active")
        try:
            return self._play_stream(body, source_format)
        finally:
            self._release()

    def _play_stream(self, body: Iterable[bytes], source_format: str | None) -> Response:
        player = self._stream_factory()
        self._arm_stop(player.stop)
        errors: dict[str, BaseException] = {}

        def produce() -> None:
            try:
                for block in self._stream_decode(body, source_format=source_format):
                    player.enqueue(block)
                player.finish()
            except DecodeError as exc:
                errors["decode"] = exc
                player.stop()
            except Exception as exc:  # noqa: BLE001 - a producer error must tear down cleanly
                errors["error"] = exc
                player.stop()

        # Feed the producer first so the player's pre-roll sees blocks; if the device is
        # unavailable, start() raises and stop() drains the queue to release the producer.
        producer = threading.Thread(target=produce, name="speak-stream-producer", daemon=True)
        producer.start()
        try:
            player.start()
        except DeviceUnavailableError as exc:
            player.stop()
            producer.join(_JOIN_TIMEOUT)
            return _err(503, str(exc))
        self._animation.on_start()
        outcome = self._await_stream(player)
        producer.join(_JOIN_TIMEOUT)
        self._animation.on_end(outcome)
        if "decode" in errors:
            return _err(
                400, f"could not decode audio stream: {errors['decode']}", outcome=outcome.value
            )
        return _ok(outcome=outcome.value)

    def _await_stream(self, player: StreamLike) -> Outcome:
        try:
            return player.wait(self._stream_timeout)
        except FuturesTimeout:
            # A stalled-but-open stream: force the device down and take the stop outcome.
            player.stop()
            try:
                return player.wait(_JOIN_TIMEOUT)
            except FuturesTimeout:
                return Outcome.DIED

    # --- stop ---------------------------------------------------------------------

    def handle_stop(self) -> Response:
        """Interrupt the in-flight clip or stream; the playback path fires on_end (R7)."""
        with self._active_lock:
            stop = self._active_stop
            if stop is not None:
                self._stop_requested = True
        if stop is None:
            return _ok(stopped=False, reason="nothing is playing")
        try:
            stop()
        except Exception as exc:  # noqa: BLE001 - a stop hook error is reported, not raised
            return _err(500, f"stop failed: {exc}")
        return _ok(stopped=True)


# --- request parsing helpers ----------------------------------------------------------


def _get(params: dict, headers: Any, key: str, header: str) -> str | None:
    """Read a request value from the query string first, then a header."""
    values = params.get(key)
    if values:
        return values[0]
    if headers is not None:
        value = headers.get(header)
        if value is not None:
            return value
    return None


def _parse_declared(params: dict, headers: Any) -> RawPcmFormat | None:
    """Build a `RawPcmFormat` when the caller declared headerless raw PCM, else `None`.

    `format=raw` (query) or `X-Speak-Format: raw` (header) selects raw PCM and then
    requires a `samplerate` and `channels`; `dtype` defaults to int16. Any other/absent
    format means a container (WAV/MP3/FLAC/OGG) the decoder sniffs.
    """
    fmt = _get(params, headers, "format", "X-Speak-Format")
    if fmt is None or fmt.lower() != "raw":
        return None
    samplerate = _get(params, headers, "samplerate", "X-Speak-Samplerate")
    channels = _get(params, headers, "channels", "X-Speak-Channels")
    dtype = _get(params, headers, "dtype", "X-Speak-Dtype") or "int16"
    if samplerate is None or channels is None:
        raise ValueError("raw format requires samplerate and channels")
    return RawPcmFormat(samplerate=int(samplerate), channels=int(channels), dtype=dtype)


# --- the HTTP transport ---------------------------------------------------------------


class SpeakRequestHandler(BaseHTTPRequestHandler):
    """Map loopback HTTP requests to `SpeakService` calls, one thread per request.

    The body is read incrementally (Content-Length in `_READ_SIZE` reads, or a chunked
    transfer-encoding parsed frame by frame), so the stream endpoint feeds the player as
    bytes arrive rather than buffering the whole request first (AE4). Every dispatch is
    wrapped so a bad body or a backend fault becomes a JSON 4xx/5xx, never a dead thread.
    """

    protocol_version = "HTTP/1.1"
    server_version = "carle-speak/1"

    @property
    def _service(self) -> SpeakService:
        return self.server.service  # type: ignore[attr-defined]

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's required name
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        params = parse_qs(parsed.query)
        try:
            if path == "/speak/clip":
                resp = self._service.handle_clip(
                    self._read_body(), params=params, headers=self.headers
                )
            elif path == "/speak/stream":
                resp = self._service.handle_stream(
                    self._iter_body(), params=params, headers=self.headers
                )
            elif path == "/speak/stop":
                self._drain_body()
                resp = self._service.handle_stop()
            else:
                resp = _err(404, f"unknown path {path!r}")
        except Exception as exc:  # noqa: BLE001 - a handler must never crash its thread
            _log.exception("speak request failed")
            resp = _err(500, f"internal error: {exc}")
        self._send(resp)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's required name
        self._send(_err(405, "use POST to /speak/clip, /speak/stream, or /speak/stop"))

    # --- body reading -------------------------------------------------------------

    def _iter_body(self) -> Iterator[bytes]:
        """Yield request-body bytes incrementally (chunked or Content-Length)."""
        encoding = self.headers.get("Transfer-Encoding", "")
        if "chunked" in encoding.lower():
            yield from self._iter_chunked()
            return
        remaining = int(self.headers.get("Content-Length", 0) or 0)
        while remaining > 0:
            chunk = self.rfile.read(min(_READ_SIZE, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk

    def _iter_chunked(self) -> Iterator[bytes]:
        while True:
            size_line = self.rfile.readline()
            if not size_line:
                return
            token = size_line.split(b";", 1)[0].strip()
            try:
                size = int(token, 16)
            except ValueError:
                return
            if size == 0:
                self.rfile.readline()  # the trailing CRLF after the terminal chunk
                return
            remaining = size
            while remaining > 0:
                chunk = self.rfile.read(remaining)
                if not chunk:
                    return
                remaining -= len(chunk)
                yield chunk
            self.rfile.readline()  # the CRLF that follows the chunk data

    def _read_body(self) -> bytes:
        return b"".join(self._iter_body())

    def _drain_body(self) -> None:
        for _ in self._iter_body():
            pass

    # --- response -----------------------------------------------------------------

    def _send(self, resp: Response) -> None:
        body = json.dumps(resp.body).encode("utf-8")
        self.send_response(resp.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - base signature
        _log.debug("speak %s - %s", self.address_string(), format % args)


class _SpeakHTTPServer(ThreadingHTTPServer):
    """A `ThreadingHTTPServer` that carries the `SpeakService` for its handlers."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address: tuple[str, int], service: SpeakService) -> None:
        super().__init__(server_address, SpeakRequestHandler)
        self.service = service


class SpeakServer:
    """Bind the speak service to a loopback HTTP port and serve it (KTD4, KTD8).

    Binds 127.0.0.1 only — a non-loopback host is refused, so the audio path is never
    exposed off the machine. `serve_forever`/`shutdown` mirror the stdlib server.
    """

    def __init__(
        self, service: SpeakService, *, host: str = LOOPBACK, port: int = DEFAULT_PORT
    ) -> None:
        if host not in LOOPBACK_HOSTS:
            raise ValueError(f"the speak server binds loopback only, not {host!r}")
        self._httpd = _SpeakHTTPServer((host, port), service)

    @property
    def address(self) -> tuple[str, int]:
        """The bound `(host, port)` — the port is concrete even when 0 was requested."""
        return self._httpd.server_address  # type: ignore[return-value]

    def serve_forever(self) -> None:
        self._httpd.serve_forever()

    def shutdown(self) -> None:
        self._httpd.shutdown()

    def server_close(self) -> None:
        self._httpd.server_close()
