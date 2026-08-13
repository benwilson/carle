"""U1 — the device-targeted PortAudio sink, driven entirely by injected fakes.

No real PortAudio or `sounddevice` is touched: a `FakeAudio` supplies the device list and
a fake blocking stream, and it also exposes a `default_device` seam so a test can prove the
sink never assigns the host default. The module imports lazily (KTD9), so this file — and
`carle.speak.sink` — collect without the `speak` extra installed.
"""

from __future__ import annotations

import pytest

from carle.speak.sink import AudioSink, DeviceUnavailableError


class FakeStream:
    """A blocking output stream: records the buffers written and the index it opened on."""

    def __init__(
        self, audio: FakeAudio, *, device: int, samplerate: int, channels: int, dtype: str
    ):
        self.audio = audio
        self.device = device
        self.samplerate = samplerate
        self.channels = channels
        self.dtype = dtype
        # Snapshot the failure decision at open time, so a factory can flip the flag for
        # the *next* stream (a reconnect) after opening this one.
        self.will_fail = audio.fail_writes
        self.written: list[object] = []
        self.entered = False
        self.closed = False

    def __enter__(self) -> FakeStream:
        self.entered = True
        return self

    def __exit__(self, *exc: object) -> bool:
        self.closed = True
        return False

    def write(self, data: object) -> None:
        if self.will_fail:
            raise RuntimeError("PortAudio stream error (simulated device drop)")
        self.written.append(data)


class FakeAudio:
    """A fake PortAudio: a device list, a stream factory, and a watched default seam."""

    def __init__(self, devices: list[dict], *, fail_writes: bool = False) -> None:
        self.devices = devices
        self.fail_writes = fail_writes
        self.streams: list[FakeStream] = []
        self._default_device: object = None
        self.default_set_count = 0

    def query_devices(self) -> list[dict]:
        return self.devices

    def stream_factory(
        self, *, device: int, samplerate: int, channels: int, dtype: str
    ) -> FakeStream:
        stream = FakeStream(
            self, device=device, samplerate=samplerate, channels=channels, dtype=dtype
        )
        self.streams.append(stream)
        return stream

    @property
    def default_device(self) -> object:
        return self._default_device

    @default_device.setter
    def default_device(self, value: object) -> None:
        # If the sink ever tried to steer the host default, it would land here.
        self._default_device = value
        self.default_set_count += 1


def out(name: str, *, channels: int = 2) -> dict:
    return {"name": name, "max_output_channels": channels, "default_samplerate": 44100.0}


def mic(name: str) -> dict:
    return {"name": name, "max_output_channels": 0, "max_input_channels": 2}


def make_sink(audio: FakeAudio, name: str = "JT_Speaker") -> AudioSink:
    return AudioSink(name, query_devices=audio.query_devices, stream_factory=audio.stream_factory)


def test_resolves_name_to_the_matching_output_index_not_an_input():
    # A microphone shares the target name but has no output channels: the sink must skip it
    # and resolve to the output device that carries the same name.
    audio = FakeAudio([out("BuiltIn"), mic("JT_Speaker"), out("JT_Speaker")])
    sink = make_sink(audio)

    assert sink.resolve() == 2


def test_plays_the_buffer_to_the_resolved_index_and_never_sets_the_default():
    audio = FakeAudio([out("BuiltIn"), out("JT_Speaker")])
    sink = make_sink(audio)
    buffer = object()

    sink.play(buffer, samplerate=44100, channels=2)

    assert len(audio.streams) == 1
    stream = audio.streams[0]
    assert stream.device == 1  # the targeted output, not the host default at index 0
    assert stream.written == [buffer]
    assert stream.entered and stream.closed  # opened and closed around the write
    # The core guarantee (R5): the host's selected default output was never touched.
    assert audio.default_set_count == 0
    assert audio.default_device is None


def test_unknown_target_raises_device_unavailable_and_never_opens_a_stream():
    audio = FakeAudio([out("BuiltIn"), out("AirPods")])
    sink = make_sink(audio, name="JT_Speaker")

    with pytest.raises(DeviceUnavailableError):
        sink.play(object(), samplerate=44100, channels=2)

    # No fallback to a default device: nothing was played at all (AE5).
    assert audio.streams == []


def test_stream_error_reresolves_and_replays_when_the_device_reconnects():
    # First play fails mid-write (a Bluetooth drop); by the retry the device is back — the
    # sink must re-resolve the index and replay rather than surfacing the transient error.
    audio = FakeAudio([out("JT_Speaker")], fail_writes=True)
    sink = make_sink(audio)
    assert sink.resolve() == 0

    def factory(*, device, samplerate, channels, dtype):
        # The first stream opens while writes fail; the reconnected device then succeeds.
        stream = audio.stream_factory(
            device=device, samplerate=samplerate, channels=channels, dtype=dtype
        )
        audio.fail_writes = False
        return stream

    sink._stream_factory = factory

    buffer = object()
    sink.play(buffer, samplerate=44100, channels=2)

    assert len(audio.streams) == 2  # first (errored) + the successful replay
    assert audio.streams[0].written == []  # the first write raised before recording
    assert audio.streams[1].written == [buffer]  # the replay reached the reconnected device
    assert audio.default_set_count == 0


def test_stream_error_then_device_gone_surfaces_device_unavailable():
    # The stream errors and the device never comes back: the re-resolve finds nothing and
    # the failure surfaces as DeviceUnavailableError rather than being swallowed.
    audio = FakeAudio([out("JT_Speaker")], fail_writes=True)
    sink = make_sink(audio)
    assert sink.resolve() == 0

    def factory(*, device, samplerate, channels, dtype):
        stream = audio.stream_factory(
            device=device, samplerate=samplerate, channels=channels, dtype=dtype
        )
        audio.devices = [out("BuiltIn")]  # JT_Speaker vanished by the time we re-resolve
        return stream

    sink._stream_factory = factory

    with pytest.raises(DeviceUnavailableError):
        sink.play(object(), samplerate=44100, channels=2)
