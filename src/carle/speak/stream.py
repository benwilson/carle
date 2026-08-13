"""Streaming playback — voice a live, incrementally-delivered audio stream in real time.

Where the sink (U1) plays a whole clip with one blocking write, this module plays audio
that is still arriving. A callback `OutputStream` pulls fixed-size PCM blocks from a
bounded `queue.Queue` that a producer (the U4 chunk reader) feeds as bytes decode. The
queue is the backpressure point: when it fills, the producer's `enqueue` blocks, so a
caller that outpaces playback is slowed rather than allowed to buffer the whole stream in
memory (R2). On an empty queue at callback time the callback writes silence and records an
underflow — a momentary starve never crashes the stream.

Completion is **device-drain, not source-drain** (KTD6). When the producer calls `finish`
a sentinel is enqueued behind the last real block; the callback raises the backend's
`CallbackStop` only once it reaches that sentinel, which stops the stream *after* the
frames already handed to the device have played out. The device's `finished_callback`
then fires, resolving a single terminal signal — a `concurrent.futures.Future` exposed as
`done` — to `Outcome.COMPLETED`. A coordinator (U5) waits on that Future to know playback
truly finished, not merely that the source queue emptied.

Two other paths resolve the same terminal signal:

- **Device loss mid-stream** (a Bluetooth drop): a callback write raises, the player
  records the error and aborts the stream, and `done` resolves to `Outcome.DIED`.
  Re-resolving the device index does *not* revive an in-flight callback stream — the
  caller must open a fresh player (contrast the sink's in-place re-resolve).
- **Stop/flush** (`stop`): the stream is stopped and closed, the queue drained (releasing
  any blocked producer), and `done` resolves to `Outcome.STOPPED`. A stop counts as done
  for the animation return-to-neutral (R7).

The device-name -> index resolution is reused from U1 (an internal `AudioSink`), never
duplicated. The backend is imported lazily inside the default stream factory and the
default callback-exception resolver, so this module imports — and its tests collect — on a
lean or headless runner without the `carle[speak]` extra (KTD9).
"""

from __future__ import annotations

import contextlib
import queue
import threading
from collections.abc import Callable
from concurrent.futures import Future
from enum import Enum
from typing import Protocol

from carle.speak.sink import (
    DEFAULT_DTYPE,
    AudioSink,
    DeviceRecord,
    default_query_devices,
)

#: Sentinel enqueued by `finish` behind the last real block. When the callback dequeues it
#: the source is drained *and* every real block has been consumed, so it is the correct
#: point to begin the device-drain stop (not merely when the queue first runs empty).
_SENTINEL = object()

DEFAULT_BLOCKSIZE = 1024
DEFAULT_MAX_BLOCKS = 32
DEFAULT_PREROLL_BLOCKS = 4
DEFAULT_PREROLL_TIMEOUT = 2.0

#: How long a full-queue `enqueue`/`finish` waits between re-checks that the player is
#: still consuming. Bounds the producer's wait so a stopped/died player (whose callback no
#: longer drains the queue) releases it within this interval instead of blocking forever.
_ENQUEUE_POLL = 0.1


class Outcome(str, Enum):
    """How a stream ended — the value carried by the terminal `done` Future.

    A str-enum so a coordinator can compare against `"completed"` directly while the
    members stay self-documenting. `STOPPED` and `COMPLETED` both mean "playback is over,
    return to neutral" (R7); `DIED` additionally means the device was lost mid-stream.
    """

    COMPLETED = "completed"
    STOPPED = "stopped"
    DIED = "died"


class CallbackStream(Protocol):
    """The slice of a callback `OutputStream` the player drives.

    `sounddevice.OutputStream` opened *with* a callback implements it; tests fake it. The
    player installs its own callback and finished-callback at construction time via the
    stream factory, then only starts, stops, and closes the stream here.
    """

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def close(self) -> None: ...


def default_callback_stream_factory(
    *,
    device: int,
    samplerate: int,
    channels: int,
    dtype: str,
    blocksize: int,
    callback: Callable[..., None],
    finished_callback: Callable[[], None],
) -> CallbackStream:
    """Open a callback output stream on `device`. Imports `sounddevice` lazily (KTD9).

    Bound to the resolved device index only; `sounddevice.default.device` is never
    assigned, so the host's selected default output is left untouched (R5).
    """
    import sounddevice  # noqa: PLC0415 - lazy so the module imports without the extra

    return sounddevice.OutputStream(
        device=device,
        samplerate=samplerate,
        channels=channels,
        dtype=dtype,
        blocksize=blocksize,
        callback=callback,
        finished_callback=finished_callback,
    )


def default_callback_exceptions() -> tuple[type[BaseException], type[BaseException]]:
    """Return `(CallbackStop, CallbackAbort)` from `sounddevice`, imported lazily (KTD9).

    These are the exact exception types the PortAudio callback must raise to stop the
    stream cleanly after its buffer drains (`CallbackStop`) or to tear it down at once
    (`CallbackAbort`). They are injectable so tests need no real backend.
    """
    import sounddevice  # noqa: PLC0415 - lazy so the module imports without the extra

    return sounddevice.CallbackStop, sounddevice.CallbackAbort


class StreamPlayer:
    """Play a live stream of fixed-size PCM blocks to one named output device.

    Lifecycle mirrors the daemon engine's spawn/await model: construct, `enqueue` blocks
    (a producer, which blocks under backpressure), `finish` to signal end-of-source,
    `start` to open and run the device stream, and `stop` to tear down early. Wait on
    `done` for the single terminal signal.
    """

    def __init__(
        self,
        device_name: str,
        *,
        samplerate: int,
        channels: int,
        dtype: str = DEFAULT_DTYPE,
        blocksize: int = DEFAULT_BLOCKSIZE,
        max_blocks: int = DEFAULT_MAX_BLOCKS,
        preroll_blocks: int = DEFAULT_PREROLL_BLOCKS,
        preroll_timeout: float = DEFAULT_PREROLL_TIMEOUT,
        query_devices: Callable[[], list[DeviceRecord]] = default_query_devices,
        stream_factory: Callable[..., CallbackStream] = default_callback_stream_factory,
        callback_exceptions: Callable[
            [], tuple[type[BaseException], type[BaseException]]
        ] = default_callback_exceptions,
    ) -> None:
        self._samplerate = samplerate
        self._channels = channels
        self._dtype = dtype
        self._blocksize = blocksize
        self._preroll_blocks = preroll_blocks
        self._preroll_timeout = preroll_timeout
        self._stream_factory = stream_factory
        self._callback_exceptions = callback_exceptions

        #: The device-name -> index resolution is reused from U1, never duplicated.
        self._sink = AudioSink(device_name, query_devices=query_devices)

        #: The bounded backpressure point: a full queue blocks the producer's `enqueue`.
        self._queue: queue.Queue[object] = queue.Queue(maxsize=max_blocks)
        self._lock = threading.Lock()
        self._preroll_ready = threading.Event()
        if preroll_blocks <= 0:
            self._preroll_ready.set()

        self._stream: CallbackStream | None = None
        self._stop_exc: type[BaseException] | None = None
        self._abort_exc: type[BaseException] | None = None

        #: The single terminal signal a coordinator (U5) waits on; its result is an
        #: `Outcome`. Resolved exactly once, from the device's finished-callback.
        self._done: Future[Outcome] = Future()

        self._enqueued = 0
        self._played = 0
        self._underflows = 0
        self._source_done = False
        self._stopping = False
        self._error: BaseException | None = None

    # --- producer side ----------------------------------------------------------------

    @property
    def device_name(self) -> str:
        return self._sink.device_name

    @property
    def done(self) -> Future[Outcome]:
        """The terminal signal: a Future resolved once to an `Outcome` (U5 waits on it)."""
        return self._done

    @property
    def underflows(self) -> int:
        """How many callbacks found the queue empty and wrote silence instead."""
        with self._lock:
            return self._underflows

    @property
    def played(self) -> int:
        """How many real PCM blocks the callback has handed to the device."""
        with self._lock:
            return self._played

    def enqueue(self, block: object) -> None:
        """Hand one fixed-size PCM block to the player; blocks when the queue is full.

        A full queue is the backpressure signal (R2): the producer stalls here rather than
        buffering the whole stream, so TCP flow control pushes back on the caller upstream.
        Once the player has stopped or the device has died, nothing drains the queue, so the
        wait is bounded — the producer wakes, sees the terminal state, and drops the block
        rather than wedging forever (a leaked producer thread on every mid-stream drop).
        """
        if not self._put_while_consuming(block):
            return
        with self._lock:
            self._enqueued += 1
            if self._enqueued >= self._preroll_blocks:
                self._preroll_ready.set()

    def finish(self) -> None:
        """Signal end-of-source: enqueue the drain sentinel behind the last real block.

        The callback keeps playing queued blocks and only begins the device-drain stop
        when it reaches this sentinel, so completion follows the device, not the source.
        """
        with self._lock:
            self._source_done = True
        self._preroll_ready.set()
        self._put_while_consuming(_SENTINEL)

    def _put_while_consuming(self, item: object) -> bool:
        """Put one item, blocking for backpressure but bailing if the player is done.

        Returns True once the item is queued, or False if the player stopped or died first
        — in which case the item is dropped, because no callback will ever consume it.
        """
        while not (self._stopping or self._done.done()):
            try:
                self._queue.put(item, timeout=_ENQUEUE_POLL)
            except queue.Full:
                continue
            return True
        return False

    # --- lifecycle --------------------------------------------------------------------

    def start(self) -> None:
        """Resolve the device, pre-roll a few blocks, then open and run the stream.

        Resolution reuses U1's `AudioSink`; a missing target raises `DeviceUnavailableError`
        before any stream is opened (no fallback to the host default). Pre-roll waits until
        a few blocks are queued (or the source finished, or the timeout elapses) so the
        first callbacks do not immediately underflow.
        """
        index = self._sink.resolve()
        self._stop_exc, self._abort_exc = self._callback_exceptions()
        if self._preroll_blocks > 0:
            self._preroll_ready.wait(self._preroll_timeout)
        stream = self._stream_factory(
            device=index,
            samplerate=self._samplerate,
            channels=self._channels,
            dtype=self._dtype,
            blocksize=self._blocksize,
            callback=self._callback,
            finished_callback=self._finished,
        )
        # Publish the stream under the lock and honour a stop that raced this setup: if
        # `stop()` already ran, do NOT start a stream it will never tear down (an orphaned,
        # unmonitored device stream that also holds the playback lock). Resolve instead.
        with self._lock:
            started = not self._stopping
            if started:
                self._stream = stream
        if not started:
            with contextlib.suppress(Exception):
                stream.close()
            if not self._done.done():
                self._finished()
            return
        stream.start()

    def stop(self) -> None:
        """Tear down early: stop and close the stream, drain the queue, resolve STOPPED.

        Draining releases any producer blocked on a full queue and retains no blocks, so a
        stop cannot leak memory. Safe to call before `start` (no stream yet) and idempotent
        (the terminal signal resolves at most once). `_stopping` and `_stream` are read
        together under the lock so a stop racing `start` cannot miss a stream `start` is
        mid-way through publishing.
        """
        with self._lock:
            self._stopping = True
            stream = self._stream
        if stream is not None:
            stream.stop()  # a real backend fires finished_callback from here
            stream.close()
        self._drain_queue()
        # If no stream ever ran (stopped before start), resolve the terminal signal here.
        if not self._done.done():
            self._finished()

    def wait(self, timeout: float | None = None) -> Outcome:
        """Block until the terminal signal resolves and return the `Outcome`."""
        return self._done.result(timeout)

    # --- callback side (runs on the device's audio thread) ----------------------------

    def _callback(self, outdata: object, frames: int, time_info: object, status: object) -> None:
        """Fill one output block from the queue, or write silence on underflow.

        Raising the backend's `CallbackStop` (on the drain sentinel) stops the stream after
        its buffer plays out. Any other exception is a lost device: it is recorded and
        re-raised as `CallbackAbort` so the stream tears down and `done` resolves to DIED.
        """
        try:
            self._render(outdata, frames, status)
        except BaseException as exc:
            if self._stop_exc is not None and isinstance(exc, self._stop_exc):
                raise  # a clean, intended device-drain stop — let it propagate
            with self._lock:
                self._error = exc
            if self._abort_exc is not None:
                raise self._abort_exc from exc
            raise

    def _render(self, outdata: object, frames: int, status: object) -> None:
        try:
            item = self._queue.get_nowait()
        except queue.Empty:
            # Underflow: no data ready. Write silence and keep the stream alive (R2).
            # This is the underrun, counted once — a truthy `status` on the same empty
            # callback describes the same starve, so we don't reach the check below.
            outdata[:] = 0  # type: ignore[index]
            with self._lock:
                self._underflows += 1
            return
        if status:
            # A starve PortAudio flagged for this block even though data was ready.
            with self._lock:
                self._underflows += 1
        if item is _SENTINEL:
            # Source drained and every real block consumed: begin the device-drain stop.
            assert self._stop_exc is not None
            raise self._stop_exc
        self._write_block(outdata, item, frames)

    def _write_block(self, outdata: object, block: object, frames: int) -> None:
        n = len(block)  # type: ignore[arg-type]
        if n == frames:
            outdata[:] = block  # type: ignore[index]
        elif n < frames:
            # A short final block: play it, pad the rest of the device buffer with silence.
            outdata[:n] = block  # type: ignore[index]
            outdata[n:] = 0  # type: ignore[index]
        else:
            outdata[:] = block[:frames]  # type: ignore[index]
        with self._lock:
            self._played += 1

    def _finished(self) -> None:
        """Resolve the terminal signal exactly once when the device stream finishes.

        DIED wins over a stop (a device lost mid-teardown is still a death); otherwise an
        intended `stop` is STOPPED and a natural device drain is COMPLETED.
        """
        with self._lock:
            if self._done.done():
                return
            if self._error is not None:
                outcome = Outcome.DIED
            elif self._stopping:
                outcome = Outcome.STOPPED
            else:
                outcome = Outcome.COMPLETED
            self._done.set_result(outcome)

    def _drain_queue(self) -> None:
        try:
            while True:
                self._queue.get_nowait()
        except queue.Empty:
            pass
