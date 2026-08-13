"""Decode caller audio into one internal PCM shape at the device sample rate.

Callers hand the speak service audio in whatever shape they rendered it — a finished
WAV or compressed clip (R1), or a live compressed stream delivered incrementally (R2).
This module turns all of that into a single internal representation, `PcmBuffer`: a
float32 NumPy array shaped `(frames, channels)`, at a known channel count and sample
rate. The sink (U1) and the streaming player (U3) both speak that one shape, so a mono
source into a stereo sink is up-mixed rather than played silent on one channel, and a
48 kHz clip into a 44.1 kHz A2DP device is resampled to match (R4).

Two decode paths share that normalization:

- Whole clip (`decode_clip`): WAV/PCM goes through `soundfile`; MP3/FLAC/OGG through
  `miniaudio` (no `ffmpeg`); raw headerless PCM is accepted when the caller declares its
  `RawPcmFormat`. The container is sniffed from the leading magic bytes.
- Live stream (`stream_pcm_blocks`): `miniaudio`'s incremental decoder is fed compressed
  bytes as they arrive and yields PCM blocks out — the whole buffer is never required, so
  the U3 chunk reader can push bytes in and pull blocks out with backpressure.

KTD9: `soundfile`, `miniaudio`, `soxr`, and even `numpy` are imported LAZILY inside the
functions, never at module top, so this module imports — and the tests collect — on a
lean or headless runner without the `carle[speak]` extra installed.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

#: The internal channel count the service normalizes toward when a caller does not ask
#: for a specific one. The robot's A2DP sink is stereo.
DEFAULT_CHANNELS = 2


class DecodeError(Exception):
    """Raised when audio bytes cannot be decoded — an unknown container or corrupt data.

    The decode functions surface this instead of letting a backend exception (or a
    segfault-adjacent crash) escape, so a caller handing over garbage gets a clear error.
    """


@dataclass(frozen=True)
class PcmBuffer:
    """The one internal PCM shape shared across the clip and stream paths.

    `samples` is a float32 NumPy array shaped `(frames, channels)`; `samplerate` and
    `channels` describe it. The annotation stays a string (via `from __future__` and the
    `TYPE_CHECKING` import) so this module — and this dataclass — need no `numpy` at
    import time (KTD9).
    """

    samples: np.ndarray
    samplerate: int
    channels: int

    @property
    def frames(self) -> int:
        """The number of PCM frames (rows) in the buffer."""
        return int(self.samples.shape[0])


@dataclass(frozen=True)
class RawPcmFormat:
    """A caller's declaration of headerless raw-PCM bytes: how to read them.

    `dtype` is a NumPy dtype string — `"float32"`, `"int16"`, `"int32"`, etc. Integer
    samples are scaled to the float32 range `[-1, 1)`; float samples are taken as-is.
    """

    samplerate: int
    channels: int
    dtype: str = "int16"


def sniff_container(data: bytes) -> str:
    """Identify the audio container from its leading magic bytes.

    Returns one of `"wav"`, `"flac"`, `"ogg"`, `"mp3"`, or `"unknown"`. This does not
    validate the whole file — it only routes the bytes to the right decoder; a truncated
    or corrupt body is caught later when the backend actually decodes it.
    """
    if len(data) < 2:
        return "unknown"
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "wav"
    if data[:4] == b"fLaC":
        return "flac"
    if data[:4] == b"OggS":
        return "ogg"
    # MP3: an ID3 tag, or a raw frame sync (11 set bits: 0xFF then top 3 bits of byte 2).
    if data[:3] == b"ID3" or (data[0] == 0xFF and (data[1] & 0xE0) == 0xE0):
        return "mp3"
    return "unknown"


def decode_clip(
    data: bytes,
    *,
    target_samplerate: int,
    target_channels: int = DEFAULT_CHANNELS,
    declared: RawPcmFormat | None = None,
) -> PcmBuffer:
    """Decode a whole clip to a normalized `PcmBuffer` at the target rate and channels.

    With `declared`, the bytes are read as raw headerless PCM in that format. Otherwise
    the container is sniffed: WAV goes through `soundfile`, MP3/FLAC/OGG through
    `miniaudio`. The decoded samples are then up/down-mixed to `target_channels` and
    resampled to `target_samplerate` (R1, R4). Unknown or corrupt bytes raise
    `DecodeError` rather than crashing.
    """
    if declared is not None:
        samples, samplerate = _decode_raw_pcm(data, declared)
    else:
        container = sniff_container(data)
        if container == "wav":
            samples, samplerate = _decode_with_soundfile(data)
        elif container in ("mp3", "flac", "ogg"):
            samples, samplerate = _decode_with_miniaudio(data, container)
        else:
            raise DecodeError(
                "unrecognized audio container; expected WAV/MP3/FLAC/OGG, or pass a "
                "RawPcmFormat for headerless PCM"
            )
    return normalize(
        samples,
        samplerate,
        target_samplerate=target_samplerate,
        target_channels=target_channels,
    )


def stream_pcm_blocks(
    chunks: Iterable[bytes],
    *,
    target_samplerate: int,
    target_channels: int = DEFAULT_CHANNELS,
    source_format: str | None = None,
    frames_per_block: int = 1024,
) -> Iterator[PcmBuffer]:
    """Incrementally decode a compressed byte stream into normalized PCM blocks (R2).

    `chunks` is any iterable of compressed bytes — the U3 chunk reader feeds it as bytes
    arrive over the wire. `miniaudio`'s streaming decoder pulls from those chunks and this
    generator yields `PcmBuffer` blocks out as they decode, so the whole clip is never
    buffered. `miniaudio` handles the channel and sample-rate conversion inline on the
    stream path (the whole-clip path uses `soxr`, per KTD9); `source_format` may name the
    codec (`"mp3"`/`"flac"`/`"ogg"`) to skip auto-detection.

    Backends are imported lazily here (KTD9). A decode failure surfaces as `DecodeError`.
    """
    import miniaudio  # noqa: PLC0415 - lazy so the module imports without the extra
    import numpy as np  # noqa: PLC0415 - lazy so the module imports without the extra

    file_format = _miniaudio_file_format(source_format)
    source = _make_iterator_source(chunks)
    try:
        generator = miniaudio.stream_any(
            source,
            file_format,
            miniaudio.SampleFormat.FLOAT32,
            target_channels,
            target_samplerate,
            frames_per_block,
        )
        for frames in generator:
            if len(frames) == 0:
                continue
            block = np.frombuffer(frames, dtype=np.float32).reshape(-1, target_channels)
            if block.shape[0] == 0:
                continue
            yield PcmBuffer(block, target_samplerate, target_channels)
    except miniaudio.MiniaudioError as exc:  # DecodeError is a MiniaudioError subclass
        raise DecodeError(f"failed to decode audio stream: {exc}") from exc


def normalize(
    samples: np.ndarray,
    samplerate: int,
    *,
    target_samplerate: int,
    target_channels: int,
) -> PcmBuffer:
    """Coerce decoded samples to float32, `target_channels`, and `target_samplerate`.

    Mixing runs before resampling so the resampler works on the final channel layout.
    Returns the shared `PcmBuffer` shape used by the sink and the streaming player.
    """
    samples = _to_float32_2d(samples, channels=samples.shape[1])
    samples = remix(samples, target_channels)
    if samplerate != target_samplerate:
        samples = resample(samples, samplerate, target_samplerate)
    return PcmBuffer(samples, target_samplerate, target_channels)


def remix(samples: np.ndarray, target_channels: int) -> np.ndarray:
    """Up/down-mix a `(frames, channels)` buffer to `target_channels`.

    Mono to stereo duplicates the channel (so a mono source is not silent on one side of a
    stereo sink); stereo to mono averages. Any other combination averages to mono first
    and then broadcasts, which keeps the audio audible rather than dropping channels.
    """
    import numpy as np  # noqa: PLC0415 - lazy so the module imports without the extra

    channels = samples.shape[1]
    if channels == target_channels:
        return samples
    if channels == 1:
        return np.repeat(samples, target_channels, axis=1)
    if target_channels == 1:
        return samples.mean(axis=1, keepdims=True).astype(np.float32)
    mono = samples.mean(axis=1, keepdims=True).astype(np.float32)
    return np.repeat(mono, target_channels, axis=1)


def resample(samples: np.ndarray, from_rate: int, to_rate: int) -> np.ndarray:
    """Resample a `(frames, channels)` float32 buffer with `soxr` (KTD6, KTD9).

    `soundfile` and `sounddevice` do not resample, so this is applied on the whole-clip
    paths to match the target device rate. Imports `soxr` lazily.
    """
    import numpy as np  # noqa: PLC0415 - lazy so the module imports without the extra
    import soxr  # noqa: PLC0415 - lazy so the module imports without the extra

    out = soxr.resample(samples, from_rate, to_rate)
    return np.ascontiguousarray(out, dtype=np.float32)


def _decode_with_soundfile(data: bytes) -> tuple[np.ndarray, int]:
    """Decode WAV/PCM bytes to `(float32 (frames, channels), samplerate)` (KTD2)."""
    import io  # noqa: PLC0415

    import soundfile  # noqa: PLC0415 - lazy so the module imports without the extra

    try:
        samples, samplerate = soundfile.read(io.BytesIO(data), dtype="float32", always_2d=True)
    except Exception as exc:  # soundfile raises its own error types on bad data
        raise DecodeError(f"failed to decode WAV/PCM audio: {exc}") from exc
    return samples, int(samplerate)


def _decode_with_miniaudio(data: bytes, container: str) -> tuple[np.ndarray, int]:
    """Decode MP3/FLAC/OGG bytes with `miniaudio` at their native rate/channels (KTD2).

    Native rate and channel count are read first (so `miniaudio` does not resample here —
    `soxr` does that in `normalize`). The MP3 path is the same `miniaudio.decode` code as
    FLAC/OGG; a tiny MP3 fixture is hard to synthesize, so tests exercise this through a
    FLAC/OGG fixture, but MP3 travels the identical branch.
    """
    import miniaudio  # noqa: PLC0415 - lazy so the module imports without the extra
    import numpy as np  # noqa: PLC0415 - lazy so the module imports without the extra

    info_fn = {
        "mp3": miniaudio.mp3_get_info,
        "flac": miniaudio.flac_get_info,
        "ogg": miniaudio.vorbis_get_info,
    }[container]
    try:
        info = info_fn(data)
        decoded = miniaudio.decode(
            data,
            output_format=miniaudio.SampleFormat.FLOAT32,
            nchannels=info.nchannels,
            sample_rate=info.sample_rate,
        )
    except miniaudio.MiniaudioError as exc:  # DecodeError is a MiniaudioError subclass
        raise DecodeError(f"failed to decode {container} audio: {exc}") from exc
    samples = np.frombuffer(decoded.samples, dtype=np.float32).reshape(-1, decoded.nchannels)
    return samples, int(decoded.sample_rate)


def _decode_raw_pcm(data: bytes, declared: RawPcmFormat) -> tuple[np.ndarray, int]:
    """Read headerless PCM bytes per the caller's declared format (KTD2)."""
    import numpy as np  # noqa: PLC0415 - lazy so the module imports without the extra

    try:
        dtype = np.dtype(declared.dtype)
        flat = np.frombuffer(data, dtype=dtype)
    except (TypeError, ValueError) as exc:
        raise DecodeError(f"invalid raw PCM declaration: {exc}") from exc
    if declared.channels < 1:
        raise DecodeError("raw PCM channel count must be at least 1")
    if flat.size % declared.channels != 0:
        raise DecodeError(
            f"raw PCM byte length is not a whole number of frames for {declared.channels} channels"
        )
    samples = flat.reshape(-1, declared.channels)
    return _to_float32_2d(samples, channels=declared.channels), declared.samplerate


def _to_float32_2d(samples: np.ndarray, *, channels: int) -> np.ndarray:
    """Coerce a sample array to a float32 `(frames, channels)` array in `[-1, 1)`.

    Signed integer PCM is scaled by the dtype's full-scale magnitude. Unsigned integer PCM
    (e.g. 8-bit `uint8`, where silence is the midpoint 128, not 0) is first re-centred on
    its midpoint, then scaled — otherwise every sample lands in `[0, 1)` and the clip plays
    with a constant DC offset (a loud thump / halved headroom). Float PCM is cast as-is.
    """
    import numpy as np  # noqa: PLC0415 - lazy so the module imports without the extra

    array = np.asarray(samples)
    if array.ndim == 1:
        array = array.reshape(-1, channels)
    if np.issubdtype(array.dtype, np.integer):
        info = np.iinfo(array.dtype)
        if info.min == 0:  # unsigned: silence is the midpoint, so centre before scaling
            midpoint = (float(info.max) + 1.0) / 2.0
            array = (array.astype(np.float32) - midpoint) / midpoint
        else:  # signed: 0 is already silence; scale by full-scale magnitude
            array = array.astype(np.float32) / (float(info.max) + 1.0)
    else:
        array = array.astype(np.float32, copy=False)
    return np.ascontiguousarray(array, dtype=np.float32)


def _make_iterator_source(chunks: Iterable[bytes]) -> object:
    """Wrap an iterable of compressed chunks as a `miniaudio.StreamableSource`.

    `miniaudio`'s streaming decoder pulls bytes by calling `read(n)`; this buffers the
    incoming chunks and hands out up to `n` bytes per call, returning `b""` at end of
    stream. The class is built lazily so `miniaudio` is only imported when streaming.
    """
    import miniaudio  # noqa: PLC0415 - lazy so the module imports without the extra

    class _IteratorSource(miniaudio.StreamableSource):
        def __init__(self, chunks: Iterable[bytes]) -> None:
            self._iterator = iter(chunks)
            self._buffer = bytearray()
            self._exhausted = False

        def read(self, num_bytes: int) -> bytes:
            while len(self._buffer) < num_bytes and not self._exhausted:
                try:
                    self._buffer.extend(next(self._iterator))
                except StopIteration:
                    self._exhausted = True
            if not self._buffer:
                return b""
            taken = bytes(self._buffer[:num_bytes])
            del self._buffer[:num_bytes]
            return taken

    return _IteratorSource(chunks)


def _miniaudio_file_format(source_format: str | None) -> object:
    """Map a codec name (or `None`) to a `miniaudio.FileFormat` for the stream decoder."""
    import miniaudio  # noqa: PLC0415 - lazy so the module imports without the extra

    if source_format is None:
        return miniaudio.FileFormat.UNKNOWN
    mapping = {
        "mp3": miniaudio.FileFormat.MP3,
        "flac": miniaudio.FileFormat.FLAC,
        "ogg": miniaudio.FileFormat.VORBIS,
        "vorbis": miniaudio.FileFormat.VORBIS,
        "wav": miniaudio.FileFormat.WAV,
    }
    try:
        return mapping[source_format.lower()]
    except KeyError as exc:
        raise DecodeError(f"unknown source format {source_format!r}") from exc
