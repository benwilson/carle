"""U3 — streaming playback, driven entirely by a fake callback stream.

No real PortAudio or `sounddevice` is touched: a `FakeBackend` supplies the device list,
the callback-exception types, and a `FakeCallbackStream` whose `pump()` synchronously
invokes the player's audio callback with a recording buffer. That lets each scenario step
the callback deterministically — play a block, starve the queue, drain the device, or lose
the device — without threads or sleeps in the audio path. The module imports lazily (KTD9),
so this file and `carle.speak.stream` collect without the `speak` extra installed.

The device-drain vs source-drain distinction is modelled faithfully: hitting the drain
sentinel raises the fake `CallbackStop`, which the fake treats as "stop *after* the buffer
flushes" — it does NOT fire the finished-callback until a later `finish_drain()`. Only then
does the terminal signal resolve `COMPLETED`, proving completion follows the device.
"""

from __future__ import annotations

import threading

import pytest

from carle.speak.stream import Outcome, StreamPlayer

BLOCKSIZE = 4


class FakeCallbackStop(Exception):
    """Stand-in for `sounddevice.CallbackStop`: stop cleanly after the buffer drains."""


class FakeCallbackAbort(Exception):
    """Stand-in for `sounddevice.CallbackAbort`: tear the stream down at once."""


_UNSET = object()


class RecordingBuffer:
    """A fake `outdata`: records what the callback assigns into it, per pump."""

    def __init__(self, frames: int) -> None:
        self._frames = frames
        self.full_slice: object = _UNSET  # value of an `outdata[:] = ...` assignment

    def __len__(self) -> int:
        return self._frames

    def __setitem__(self, key: object, value: object) -> None:
        if isinstance(key, slice) and key == slice(None):
            self.full_slice = value


class DeadBuffer:
    """A fake `outdata` for a lost device: any write raises, as a dead sink would."""

    def __init__(self, frames: int) -> None:
        self._frames = frames

    def __len__(self) -> int:
        return self._frames

    def __setitem__(self, key: object, value: object) -> None:
        raise RuntimeError("PortAudio write to a disconnected device")


class FakeCallbackStream:
    """A fake callback `OutputStream`: `pump()` drives the player's callback one block."""

    def __init__(self, *, blocksize, callback, finished_callback) -> None:
        self.blocksize = blocksize
        self.callback = callback
        self.finished_callback = finished_callback
        self.started = False
        self.stopped = False
        self.closed = False
        self.played: list[object] = []  # real blocks handed to the device, in order
        self.silences = 0
        self.drain_pending = False
        self._finished_fired = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True
        self._fire_finished()  # a real backend fires the finished-callback from stop()

    def close(self) -> None:
        self.closed = True

    def _fire_finished(self) -> None:
        if not self._finished_fired:
            self._finished_fired = True
            self.finished_callback()

    def pump(self, *, status: object = None, dead: bool = False) -> None:
        """Invoke the callback once with a fresh output buffer and record the result."""
        buf: object = DeadBuffer(self.blocksize) if dead else RecordingBuffer(self.blocksize)
        try:
            self.callback(buf, self.blocksize, None, status)
        except FakeCallbackStop:
            # Device-drain: the buffer must flush before the stream truly finishes, so the
            # finished-callback is deferred to finish_drain() — NOT fired here.
            self.drain_pending = True
            return
        except FakeCallbackAbort:
            # Abort tears down now: the finished-callback fires immediately.
            self._fire_finished()
            return
        assert not dead  # a dead write should have aborted above
        value = buf.full_slice  # type: ignore[attr-defined]
        if value == 0:
            self.silences += 1
        elif value is not _UNSET:
            self.played.append(value)

    def finish_drain(self) -> None:
        """Emulate the device buffer flushing after a CallbackStop; fire the finish."""
        assert self.drain_pending
        self._fire_finished()


class FakeBackend:
    """A fake PortAudio backend: device list, exception types, and the stream factory."""

    def __init__(self, devices: list[dict] | None = None) -> None:
        self.devices = devices or [out("JT_Speaker")]
        self.stream: FakeCallbackStream | None = None

    def query_devices(self) -> list[dict]:
        return self.devices

    def callback_exceptions(self) -> tuple[type[BaseException], type[BaseException]]:
        return FakeCallbackStop, FakeCallbackAbort

    def stream_factory(
        self, *, device, samplerate, channels, dtype, blocksize, callback, finished_callback
    ) -> FakeCallbackStream:
        stream = FakeCallbackStream(
            blocksize=blocksize, callback=callback, finished_callback=finished_callback
        )
        self.stream = stream
        return stream


def out(name: str, *, channels: int = 2) -> dict:
    return {"name": name, "max_output_channels": channels, "default_samplerate": 44100.0}


def blk(marker: int) -> list[int]:
    """A distinct, identity-comparable PCM block of the fixed block size."""
    return [marker] * BLOCKSIZE


def make_player(
    backend: FakeBackend,
    *,
    preroll_blocks: int = 0,
    max_blocks: int = 32,
    sink: object = None,
) -> StreamPlayer:
    return StreamPlayer(
        "JT_Speaker",
        samplerate=44100,
        channels=2,
        blocksize=BLOCKSIZE,
        max_blocks=max_blocks,
        preroll_blocks=preroll_blocks,
        query_devices=backend.query_devices,
        stream_factory=backend.stream_factory,
        callback_exceptions=backend.callback_exceptions,
        sink=sink,
    )


def start_player(
    *, blocks: list[object], finish: bool = False, preroll_blocks: int = 0
) -> tuple[StreamPlayer, FakeCallbackStream]:
    """Build a player, enqueue `blocks`, optionally `finish`, start, and return the stream."""
    backend = FakeBackend()
    player = make_player(backend, preroll_blocks=preroll_blocks)
    for block in blocks:
        player.enqueue(block)
    if finish:
        player.finish()
    player.start()
    assert backend.stream is not None
    return player, backend.stream


def test_players_sharing_a_sink_resolve_the_device_once():
    # Two players — as two sequential stream requests would build — sharing one AudioSink
    # scan the device list once, not once per request (the per-request re-resolve the clip
    # path already avoids).
    from carle.speak.sink import AudioSink

    scans = {"n": 0}

    def counting_query() -> list[dict]:
        scans["n"] += 1
        return [out("JT_Speaker")]

    shared = AudioSink("JT_Speaker", query_devices=counting_query)
    backend = FakeBackend()

    make_player(backend, sink=shared).start()
    make_player(backend, sink=shared).start()

    assert scans["n"] == 1  # resolved once and cached across both players


def test_module_imports_and_exposes_the_outcomes_without_a_backend():
    # Collection alone proves the lazy import (KTD9); the outcomes are the terminal signal.
    assert {Outcome.COMPLETED, Outcome.STOPPED, Outcome.DIED}
    assert Outcome.COMPLETED == "completed"


def test_enqueued_blocks_are_played_in_order():
    blocks = [blk(0), blk(1), blk(2)]
    player, stream = start_player(blocks=blocks)

    for _ in blocks:
        stream.pump()

    assert stream.played == blocks  # played through the fake callback stream, in order
    assert player.played == 3
    assert not player.done.done()  # still live; nothing has ended the stream


def test_empty_queue_writes_silence_records_underflow_and_does_not_crash():
    player, stream = start_player(blocks=[])

    stream.pump()  # queue empty at callback time

    assert stream.silences == 1  # silence written to the device, not a crash
    assert player.underflows == 1  # the underflow was recorded
    assert not player.done.done()  # a starve does not end the stream


def test_full_queue_blocks_the_producer_as_backpressure():
    backend = FakeBackend()
    player = make_player(backend, max_blocks=2)
    player.enqueue(blk(0))
    player.enqueue(blk(1))  # queue now full

    started = threading.Event()
    returned = threading.Event()

    def produce() -> None:
        started.set()
        player.enqueue(blk(2))  # must block: the bounded queue is full
        returned.set()

    thread = threading.Thread(target=produce)
    thread.start()
    started.wait(1)

    # The producer is blocked on the full queue — backpressure, not unbounded buffering.
    assert not returned.wait(0.2)

    player._queue.get_nowait()  # a consumer frees one slot
    assert returned.wait(1)  # now the blocked put completes
    thread.join(1)


def test_stop_releases_a_producer_blocked_on_a_full_queue():
    # The wedge that a real Bluetooth drop triggers: a producer blocked on a full queue must
    # be released when the player stops, or the producer thread leaks forever.
    backend = FakeBackend()
    player = make_player(backend, max_blocks=1)
    player.enqueue(blk(0))  # queue now full

    entered = threading.Event()
    returned = threading.Event()

    def produce() -> None:
        entered.set()
        player.enqueue(blk(1))  # blocks: the queue is full and nothing consumes it
        returned.set()

    thread = threading.Thread(target=produce)
    thread.start()
    entered.wait(1)
    assert not returned.wait(0.2)  # genuinely blocked on backpressure

    player.stop()  # a stop with no consumer must still release the blocked producer
    assert returned.wait(1)  # released within the poll interval, not wedged forever
    thread.join(1)
    assert player.done.done()
    assert player.wait() == Outcome.STOPPED


def test_enqueue_after_stop_drops_the_block_without_blocking():
    # Once the player is done, enqueue must not block on a full queue (nothing will drain it)
    # — it drops the late block instead of leaking the producer.
    backend = FakeBackend()
    player = make_player(backend, max_blocks=1)
    player.stop()

    player.enqueue(blk(0))  # returns immediately despite capacity 1 and no consumer
    player.enqueue(blk(1))
    assert player._queue.qsize() == 0  # nothing buffered for a consumer that never runs


def test_stop_before_start_never_opens_an_orphan_device_stream():
    # A /speak/stop landing between arm and start: start must see the stop and refuse to open
    # a stream nothing would tear down, rather than leave an unmonitored device stream running.
    backend = FakeBackend()
    player = make_player(backend)
    player.stop()  # stop wins the race, before start

    player.start()

    stream = backend.stream
    if stream is not None:  # start may have built the object, but must not have run it
        assert stream.started is False
        assert stream.closed is True
    assert player.done.done()
    assert player.wait() == Outcome.STOPPED


def test_stop_mid_stream_drains_closes_and_fires_the_terminal_signal():
    player, stream = start_player(blocks=[blk(0), blk(1)])
    stream.pump()  # play one block, then stop with a block still queued

    player.stop()

    assert stream.stopped and stream.closed  # drained and closed cleanly
    assert player._queue.empty()  # the pending block was drained (no leak, R7)
    assert player.done.result(1) == Outcome.STOPPED  # terminal signal fired (R7)


def test_completed_fires_only_after_device_drain_not_when_source_queue_empties():
    player, stream = start_player(blocks=[blk(0), blk(1)], finish=True)

    stream.pump()  # block 0
    stream.pump()  # block 1
    assert not player.done.done()

    stream.pump()  # hits the drain sentinel -> CallbackStop; device-drain begins
    # The source queue is now empty, but the device buffer has NOT flushed yet:
    assert not player.done.done()

    stream.finish_drain()  # the device buffer flushes
    assert player.done.result(1) == Outcome.COMPLETED  # completion follows the device


def test_device_loss_mid_stream_tears_down_and_fires_died():
    player, stream = start_player(blocks=[blk(0)])

    stream.pump(dead=True)  # the device write raises: a Bluetooth drop mid-stream

    assert player.done.result(1) == Outcome.DIED  # terminal signal, died outcome (R7)


def test_unbounded_producer_does_not_grow_memory_past_the_bounded_queue():
    # A fast producer cannot buffer without limit: once the queue is full, enqueue blocks.
    backend = FakeBackend()
    player = make_player(backend, max_blocks=3)
    for i in range(3):
        player.enqueue(blk(i))

    with pytest.raises(Exception):  # noqa: B017 - queue.Full is what we want to observe
        player._queue.put_nowait(blk(99))  # a 4th block cannot be buffered

    assert player._queue.qsize() == 3  # capped at the bound, regardless of producer speed


def test_a_failed_open_recovers_the_sink_and_retries_once():
    # A power-cycled device leaves PortAudio's snapshot stale: the first open fails
    # (PaErrorCode -9986 on hardware). start() must ask the sink to rescan, re-resolve,
    # and open once more — a restart of the whole server must not be the fix.
    backend = FakeBackend(devices=[out("Stale"), out("JT_Speaker")])
    opens: list[int] = []
    refreshes: list[bool] = []

    def failing_then_ok_factory(*, device, **kwargs):
        opens.append(device)
        if len(opens) == 1:
            raise RuntimeError("Internal PortAudio error (simulated -9986)")
        return backend.stream_factory(device=device, **kwargs)

    def refresh() -> None:
        refreshes.append(True)
        backend.devices = [out("Stale"), out("Gone"), out("JT_Speaker")]

    from carle.speak.sink import AudioSink

    sink = AudioSink(
        "JT_Speaker",
        query_devices=backend.query_devices,
        stream_factory=failing_then_ok_factory,
        refresh_devices=refresh,
    )
    player = StreamPlayer(
        "JT_Speaker",
        samplerate=44100,
        channels=2,
        blocksize=BLOCKSIZE,
        query_devices=backend.query_devices,
        stream_factory=failing_then_ok_factory,
        callback_exceptions=backend.callback_exceptions,
        sink=sink,
    )
    player.finish()

    player.start()

    assert refreshes == [True]
    assert opens == [1, 2]  # stale index first, the fresh post-rescan index on retry
    assert backend.stream is not None  # the second open produced the live stream
    player.stop()
