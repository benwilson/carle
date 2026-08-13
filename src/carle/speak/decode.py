"""Decode caller audio into one internal PCM shape at the device sample rate.

Callers hand the speak service audio in whatever shape they rendered it — a finished
WAV or compressed clip (R1), or a live compressed stream delivered incrementally (R2).
This module turns all of that into a single internal representation, `PcmBuffer`: a
float32 NumPy array shaped `(frames, channels)`, at a known channel count and sample
rate. The sink (U1) and the streaming player (U3) both speak that one shape, so a mono
source into a stereo sink is up-mixed rather than played silent on one channel, and a
48 kHz clip into a 44.1 kHz A2DP device is resampled to match (R4).

Two decode paths share that normalization:

- Whole clip (`decode_clip`): WAV/PCM goes through `soundfile`; MP3/FLAC/OGG-Vorbis
  through `miniaudio`; AAC, Ogg-Opus, and anything the sniffer cannot name fall through
  to `av` (PyAV, FFmpeg's libraries in-process — no subprocess); raw headerless PCM is
  accepted when the caller declares its `RawPcmFormat`. The container is sniffed from the
  leading magic bytes.
- Live stream (`stream_pcm_blocks`): `av`'s demux/decode loop is fed compressed bytes as
  they arrive and yields PCM blocks out — the whole buffer is never required, so the U3
  chunk reader can push bytes in and pull blocks out with backpressure. This covers the
  formats current TTS APIs stream: MP3, WAV, FLAC, Ogg-Vorbis, Ogg-Opus, and AAC(ADTS),
  plus declared raw PCM (`RawPcmFormat`), the low-latency option (OpenAI `pcm`,
  ElevenLabs `pcm_*`, Google `LINEAR16`). A stream that decodes to zero audio frames is
  an error, never a silent no-op "success".

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

    Returns one of `"wav"`, `"flac"`, `"ogg"`, `"aac"`, `"mp3"`, or `"unknown"`. This
    does not validate the whole file — it only routes the bytes to the right decoder; a
    truncated or corrupt body is caught later when the backend actually decodes it.
    """
    if len(data) < 2:
        return "unknown"
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "wav"
    if data[:4] == b"fLaC":
        return "flac"
    if data[:4] == b"OggS":
        return "ogg"
    # AAC in an ADTS wrapper: a 12-bit sync (0xFFF) with layer bits zero. Checked before
    # MP3 because the looser MP3 sync test below also matches an ADTS header.
    if data[0] == 0xFF and (data[1] & 0xF6) == 0xF0:
        return "aac"
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
    the container is sniffed: WAV goes through `soundfile`; MP3/FLAC/OGG-Vorbis through
    `miniaudio`; AAC, Ogg-Opus (which shares OGG's magic but not its codec), and unknown
    containers go through `av`. The decoded samples are then up/down-mixed to
    `target_channels` and resampled to `target_samplerate` (R1, R4). Corrupt bytes raise
    `DecodeError` rather than crashing.
    """
    if declared is not None:
        samples, samplerate = _decode_raw_pcm(data, declared)
    else:
        container = sniff_container(data)
        if container == "wav":
            samples, samplerate = _decode_with_soundfile(data)
        elif container == "ogg":
            # OGG is a container: Vorbis decodes on miniaudio, Opus only on av.
            try:
                samples, samplerate = _decode_with_miniaudio(data, container)
            except DecodeError:
                samples, samplerate = _decode_with_av(data)
        elif container in ("mp3", "flac"):
            samples, samplerate = _decode_with_miniaudio(data, container)
        else:  # "aac", or an unknown container av may still recognize
            samples, samplerate = _decode_with_av(data)
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
    declared: RawPcmFormat | None = None,
    frames_per_block: int = 1024,
) -> Iterator[PcmBuffer]:
    """Incrementally decode a byte stream into normalized fixed-size PCM blocks (R2).

    `chunks` is any iterable of bytes — the U3 chunk reader feeds it as bytes arrive over
    the wire. With `declared`, the bytes are headerless raw PCM in that format and are
    converted block-by-block (resampled with a stateful `soxr` stream so block boundaries
    stay artifact-free). Otherwise the bytes are a compressed container demuxed and
    decoded incrementally by `av`; `source_format` may name the container
    (`"mp3"`/`"wav"`/`"flac"`/`"ogg"`/`"aac"`) to skip probing, which sniffs the leading
    magic bytes and lets `av` probe when they are unfamiliar.

    Every yielded block holds exactly `frames_per_block` frames except the last, which
    may be short. This invariant is load-bearing: the `StreamPlayer` hands one block to
    one device callback and **truncates a block larger than the device buffer**, so an
    oversized block plays sped-up and garbled. Decoders produce whatever frame size the
    codec likes (a WAV demuxer's packets are far larger than an MP3 frame); the re-chunk
    here is what restores the device-shaped cadence.

    Backends are imported lazily here (KTD9). A decode failure surfaces as `DecodeError`,
    and so does a stream that ends without producing a single audio frame — a caller must
    never see a "successful" playback of zero audio.
    """
    if declared is not None:
        inner = _stream_raw_pcm(
            chunks,
            declared,
            target_samplerate=target_samplerate,
            target_channels=target_channels,
        )
    else:
        inner = _stream_with_av(
            chunks,
            source_format=source_format,
            target_samplerate=target_samplerate,
            target_channels=target_channels,
        )
    yield from _rechunk(inner, frames_per_block, target_samplerate, target_channels)


def _rechunk(
    blocks: Iterator[PcmBuffer],
    frames_per_block: int,
    samplerate: int,
    channels: int,
) -> Iterator[PcmBuffer]:
    """Re-cut a stream of arbitrary-size PCM blocks into exact `frames_per_block` blocks.

    Buffers across input-block boundaries; only the final block may be short. Yields
    nothing extra for an empty source (the zero-frame guard lives in the decoders).
    """
    import numpy as np  # noqa: PLC0415 - lazy so the module imports without the extra

    held: list[np.ndarray] = []
    buffered = 0
    for block in blocks:
        held.append(block.samples)
        buffered += block.samples.shape[0]
        while buffered >= frames_per_block:
            merged = held[0] if len(held) == 1 else np.concatenate(held, axis=0)
            cut = np.ascontiguousarray(merged[:frames_per_block], dtype=np.float32)
            rest = merged[frames_per_block:]
            held = [rest] if rest.shape[0] else []
            buffered = rest.shape[0]
            yield PcmBuffer(cut, samplerate, channels)
    if buffered:
        merged = held[0] if len(held) == 1 else np.concatenate(held, axis=0)
        yield PcmBuffer(
            np.ascontiguousarray(merged, dtype=np.float32), samplerate, channels
        )


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


class _ChunkReader:
    """Wrap an iterable of byte chunks as the non-seekable file-like `av.open` reads.

    `av` pulls bytes by calling `read(n)`; this buffers the incoming chunks and hands out
    up to `n` bytes per call, returning `b""` at end of stream. It never seeks —
    `seekable()` says so — which keeps `av` in pure streaming mode.
    """

    def __init__(self, chunks: Iterable[bytes], head: bytes = b"") -> None:
        self._iterator = iter(chunks)
        self._buffer = bytearray(head)
        self._exhausted = False

    def read(self, num_bytes: int = -1) -> bytes:
        while (num_bytes < 0 or len(self._buffer) < num_bytes) and not self._exhausted:
            try:
                self._buffer.extend(next(self._iterator))
            except StopIteration:
                self._exhausted = True
        if num_bytes < 0:
            num_bytes = len(self._buffer)
        taken = bytes(self._buffer[:num_bytes])
        del self._buffer[:num_bytes]
        return taken

    def seekable(self) -> bool:
        return False


#: Container names accepted as a `source_format` hint, mapped to `av` demuxer names.
#: Vorbis and Opus both live in an OGG container, so both hints select the ogg demuxer.
_AV_FORMAT_NAMES = {
    "mp3": "mp3",
    "wav": "wav",
    "flac": "flac",
    "ogg": "ogg",
    "vorbis": "ogg",
    "opus": "ogg",
    "aac": "aac",
}


def _stream_with_av(
    chunks: Iterable[bytes],
    *,
    source_format: str | None,
    target_samplerate: int,
    target_channels: int,
) -> Iterator[PcmBuffer]:
    """Demux and decode a compressed stream with `av`, yielding normalized PCM blocks.

    The container is taken from `source_format` when the caller named one, else sniffed
    from the first chunk's magic bytes; when neither names it, `av` probes. `av`'s
    resampler converts each decoded frame to packed float32 at the target rate and
    channel count, so blocks come out already in the sink's shape.
    """
    import av  # noqa: PLC0415 - lazy so the module imports without the extra
    import numpy as np  # noqa: PLC0415 - lazy so the module imports without the extra

    if source_format is not None:
        fmt = _AV_FORMAT_NAMES.get(source_format.lower())
        if fmt is None:
            raise DecodeError(f"unknown source format {source_format!r}")
        reader = _ChunkReader(chunks)
    else:
        iterator = iter(chunks)
        head = b""
        for chunk in iterator:  # first non-empty chunk carries the magic bytes
            if chunk:
                head = chunk
                break
        if not head:
            raise DecodeError("empty audio stream")
        sniffed = sniff_container(head)
        fmt = _AV_FORMAT_NAMES.get(sniffed)  # None for "unknown": let av probe
        reader = _ChunkReader(iterator, head=head)

    layout = "stereo" if target_channels == 2 else "mono"
    if target_channels not in (1, 2):
        raise DecodeError(f"unsupported target channel count {target_channels}")
    total_frames = 0
    try:
        with av.open(reader, mode="r", format=fmt) as container:
            resampler = av.AudioResampler(
                format="flt", layout=layout, rate=target_samplerate
            )
            audio = container.streams.audio[0]
            for frame in container.decode(audio):
                for out in resampler.resample(frame):
                    block = out.to_ndarray().reshape(-1, target_channels)
                    if block.shape[0] == 0:
                        continue
                    total_frames += block.shape[0]
                    yield PcmBuffer(
                        np.ascontiguousarray(block, dtype=np.float32),
                        target_samplerate,
                        target_channels,
                    )
            for out in resampler.resample(None):  # flush the resampler's tail
                block = out.to_ndarray().reshape(-1, target_channels)
                if block.shape[0] == 0:
                    continue
                total_frames += block.shape[0]
                yield PcmBuffer(
                    np.ascontiguousarray(block, dtype=np.float32),
                    target_samplerate,
                    target_channels,
                )
    except av.FFmpegError as exc:
        raise DecodeError(f"failed to decode audio stream: {exc}") from exc
    except (IndexError, ValueError) as exc:  # no audio stream in the container
        raise DecodeError(f"stream carries no decodable audio: {exc}") from exc
    if total_frames == 0:
        raise DecodeError("audio stream decoded to zero frames")


def _stream_raw_pcm(
    chunks: Iterable[bytes],
    declared: RawPcmFormat,
    *,
    target_samplerate: int,
    target_channels: int,
) -> Iterator[PcmBuffer]:
    """Convert declared headerless raw PCM chunks into normalized PCM blocks.

    Bytes are frame-aligned across chunk boundaries (a frame split over two chunks is
    carried, never dropped) and resampled with a stateful `soxr.ResampleStream`, so the
    rate conversion is seamless across blocks rather than per-block.
    """
    import numpy as np  # noqa: PLC0415 - lazy so the module imports without the extra

    try:
        frame_bytes = np.dtype(declared.dtype).itemsize * declared.channels
    except (TypeError, ValueError) as exc:
        raise DecodeError(f"invalid raw PCM declaration: {exc}") from exc
    if declared.channels < 1:
        raise DecodeError("raw PCM channel count must be at least 1")

    resampler = _RawStreamResampler(
        declared.samplerate, target_samplerate, target_channels
    )
    pending = bytearray()
    total_frames = 0
    for chunk in chunks:
        pending.extend(chunk)
        usable = len(pending) - (len(pending) % frame_bytes)
        if usable == 0:
            continue
        samples, _ = _decode_raw_pcm(bytes(pending[:usable]), declared)
        del pending[:usable]
        block = resampler.push(remix(samples, target_channels))
        if block.shape[0]:
            total_frames += block.shape[0]
            yield PcmBuffer(block, target_samplerate, target_channels)
    if len(pending) % frame_bytes:
        raise DecodeError(
            f"raw PCM stream ended mid-frame ({len(pending)} trailing bytes for "
            f"{frame_bytes}-byte frames)"
        )
    block = resampler.flush()
    if block.shape[0]:
        total_frames += block.shape[0]
        yield PcmBuffer(block, target_samplerate, target_channels)
    if total_frames == 0:
        raise DecodeError("audio stream decoded to zero frames")


class _RawStreamResampler:
    """A stateful rate converter for the raw-PCM stream path.

    Same-rate streams pass through untouched; differing rates run through one
    `soxr.ResampleStream` for the stream's whole life so filter state carries across
    blocks. Imports lazily (KTD9).
    """

    def __init__(self, from_rate: int, to_rate: int, channels: int) -> None:
        self._passthrough = from_rate == to_rate
        self._channels = channels
        if not self._passthrough:
            import soxr  # noqa: PLC0415 - lazy so the module imports without the extra

            self._stream = soxr.ResampleStream(
                from_rate, to_rate, channels, dtype="float32"
            )

    def push(self, block: np.ndarray) -> np.ndarray:
        import numpy as np  # noqa: PLC0415 - lazy so the module imports without the extra

        if self._passthrough:
            return block
        out = self._stream.resample_chunk(block)
        return np.ascontiguousarray(out, dtype=np.float32)

    def flush(self) -> np.ndarray:
        import numpy as np  # noqa: PLC0415 - lazy so the module imports without the extra

        if self._passthrough:
            return np.empty((0, self._channels), dtype=np.float32)
        out = self._stream.resample_chunk(
            np.empty((0, self._channels), dtype=np.float32), last=True
        )
        return np.ascontiguousarray(out, dtype=np.float32)


def _decode_with_av(data: bytes) -> tuple[np.ndarray, int]:
    """Decode a whole clip with `av` at its native rate/channels (AAC, Opus, unknown).

    Like `_decode_with_miniaudio`, this returns native-rate samples and leaves the
    rate/channel normalization to `normalize` (`soxr` per KTD9). The samples are
    converted to packed float32 at the source's own layout.
    """
    import io  # noqa: PLC0415

    import av  # noqa: PLC0415 - lazy so the module imports without the extra
    import numpy as np  # noqa: PLC0415 - lazy so the module imports without the extra

    blocks: list[np.ndarray] = []
    samplerate = 0
    channels = 0
    try:
        with av.open(io.BytesIO(data), mode="r") as container:
            audio = container.streams.audio[0]
            resampler = None
            for frame in container.decode(audio):
                if resampler is None:
                    samplerate = frame.sample_rate
                    source_channels = frame.layout.nb_channels
                    layout = "stereo" if source_channels >= 2 else "mono"
                    channels = 2 if source_channels >= 2 else 1
                    resampler = av.AudioResampler(
                        format="flt", layout=layout, rate=samplerate
                    )
                for out in resampler.resample(frame):
                    blocks.append(out.to_ndarray().reshape(-1, channels))
            if resampler is not None:
                for out in resampler.resample(None):
                    blocks.append(out.to_ndarray().reshape(-1, channels))
    except av.FFmpegError as exc:
        raise DecodeError(f"failed to decode audio: {exc}") from exc
    except (IndexError, ValueError) as exc:
        raise DecodeError(f"clip carries no decodable audio: {exc}") from exc
    if not blocks:
        raise DecodeError("audio clip decoded to zero frames")
    samples = np.concatenate(blocks, axis=0).astype(np.float32, copy=False)
    return np.ascontiguousarray(samples), int(samplerate)
