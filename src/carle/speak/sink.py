"""The device-targeted PortAudio sink — play PCM to a chosen output, not the default.

`AudioSink` plays a decoded PCM buffer to one output device selected by name, addressing
it by the numeric index PortAudio assigns (KTD1). The host's selected default output is
never touched: this module never assigns `sounddevice.default.device`, and a missing
target fails loudly with `DeviceUnavailableError` rather than falling back to the default
(AE5) — falling back would leak the robot's audio to whatever the operator is listening
on.

Two seams are injected so tests never need a real PortAudio stack (the injected-dependency
style of `carle.daemon.engine.Engine`):

- `query_devices` returns the list of device records (name + `max_output_channels` +
  `default_samplerate`), positionally indexed the way PortAudio indexes devices.
- `stream_factory` builds a blocking output stream for a resolved index; the default
  factory imports `sounddevice` LAZILY so this module imports without the `speak` extra
  or a system PortAudio present (KTD9).

The resolved index is cached, and re-resolved on a stream error — a Bluetooth speaker that
drops and reconnects can come back under a different index (KTD1). If it is gone entirely,
the re-resolve raises `DeviceUnavailableError` and the failure surfaces.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, Protocol

#: A device record as `sounddevice.query_devices()` yields it: a mapping with at least a
#: `name`, an integer `max_output_channels`, and a float `default_samplerate`.
DeviceRecord = dict[str, Any]

DEFAULT_DTYPE = "float32"


class DeviceUnavailableError(Exception):
    """Raised when the configured target output device is not a connected output.

    The sink raises this instead of playing to the host default (AE5): silently falling
    back would leak the robot's audio to whatever output the operator has selected.
    """


class OutputStream(Protocol):
    """The slice of a blocking PortAudio output stream the sink drives.

    `sounddevice.OutputStream` (opened without a callback) implements it; tests fake it.
    Used as a context manager so the stream is started and closed around the write.
    """

    def __enter__(self) -> OutputStream: ...

    def __exit__(self, *exc: object) -> bool | None: ...

    def write(self, data: object) -> None: ...


def default_query_devices() -> Sequence[DeviceRecord]:
    """Return every device PortAudio sees. Imports `sounddevice` lazily (KTD9)."""
    import sounddevice  # noqa: PLC0415 - lazy so the module imports without the extra

    return sounddevice.query_devices()


def default_refresh_devices() -> None:
    """Rescan PortAudio's device topology. Imports `sounddevice` lazily (KTD9).

    PortAudio snapshots the device list at initialization. A Bluetooth device that
    vanished and re-registered — the robot being power-cycled is the concrete case,
    observed on hardware — keeps its stale, dead entry in that snapshot, so every open
    against it fails (`PaErrorCode -9986`) even after CoreAudio lists the device again.
    `sounddevice`'s `_terminate`/`_initialize` pair is the library's own re-enumeration
    path (it exposes no public one).
    """
    import sounddevice  # noqa: PLC0415 - lazy so the module imports without the extra

    sounddevice._terminate()  # noqa: SLF001 - the library's documented re-scan idiom
    sounddevice._initialize()  # noqa: SLF001


def default_stream_factory(
    *, device: int, samplerate: int, channels: int, dtype: str
) -> OutputStream:
    """Open a blocking output stream on `device`. Imports `sounddevice` lazily (KTD9).

    The stream is bound to the resolved device index only; `sounddevice.default.device`
    is never assigned, so the host's selected default output is left untouched (R5).
    """
    import sounddevice  # noqa: PLC0415 - lazy so the module imports without the extra

    return sounddevice.OutputStream(
        device=device, samplerate=samplerate, channels=channels, dtype=dtype
    )


class AudioSink:
    """Play PCM buffers to one named output device, never to the host default."""

    def __init__(
        self,
        device_name: str,
        *,
        query_devices: Callable[[], Sequence[DeviceRecord]] = default_query_devices,
        stream_factory: Callable[..., OutputStream] = default_stream_factory,
        refresh_devices: Callable[[], None] = default_refresh_devices,
    ) -> None:
        self._device_name = device_name
        self._query_devices = query_devices
        self._stream_factory = stream_factory
        self._refresh_devices = refresh_devices
        #: The resolved PortAudio index, cached after the first lookup and invalidated on
        #: a stream error so a reconnected device is re-resolved (KTD1).
        self._index: int | None = None

    def recover(self) -> None:
        """Drop the cached index and rescan the device topology.

        Called after a stream open/write error: re-resolving against PortAudio's original
        snapshot is not enough when the device re-registered (a power cycle) — the
        snapshot itself holds the dead entry, so it must be rebuilt first. A failure in
        the rescan is swallowed; the subsequent resolve fails loudly on its own if the
        device is truly gone.
        """
        self._index = None
        try:
            self._refresh_devices()
        except Exception:  # noqa: BLE001 - resolve() is the loud failure path, not this
            pass

    @property
    def device_name(self) -> str:
        return self._device_name

    def _resolve_index(self) -> int:
        """Find the output device matching the configured name, or raise (AE5).

        Only devices with `max_output_channels > 0` are eligible, so an input device that
        happens to share the name is never picked.
        """
        for index, record in enumerate(self._query_devices()):
            if record.get("max_output_channels", 0) > 0 and record.get("name") == self._device_name:
                return index
        raise DeviceUnavailableError(
            f"output device {self._device_name!r} is not a connected output; "
            "refusing to fall back to the host default"
        )

    def resolve(self) -> int:
        """Return the cached index, resolving (and caching) it on first use."""
        if self._index is None:
            self._index = self._resolve_index()
        return self._index

    def play(
        self,
        pcm: object,
        *,
        samplerate: int,
        channels: int,
        dtype: str = DEFAULT_DTYPE,
    ) -> None:
        """Play a decoded PCM buffer to the target device via a blocking write (KTD3).

        On a stream error the device topology is rescanned and the index re-resolved once
        (`recover`) — a Bluetooth device that dropped, or was power-cycled and
        re-registered, comes back under a fresh PortAudio entry. If it re-resolves, the
        clip is replayed; if the device is gone, the re-resolve raises
        `DeviceUnavailableError`, and a second stream error surfaces to the caller.
        """
        index = self.resolve()
        try:
            self._write_clip(index, pcm, samplerate=samplerate, channels=channels, dtype=dtype)
            return
        except DeviceUnavailableError:
            raise
        except Exception:
            # A stream error may mean the device re-registered: rescan, then re-resolve.
            self.recover()
        index = self.resolve()  # raises DeviceUnavailableError if the device is truly gone
        self._write_clip(index, pcm, samplerate=samplerate, channels=channels, dtype=dtype)

    def _write_clip(
        self, index: int, pcm: object, *, samplerate: int, channels: int, dtype: str
    ) -> None:
        stream = self._stream_factory(
            device=index, samplerate=samplerate, channels=channels, dtype=dtype
        )
        with stream:
            stream.write(pcm)
