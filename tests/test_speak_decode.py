"""U2 — audio decoding and normalization into one internal PCM shape.

Fixtures are synthesized in-test with `soundfile` (a short sine tone) rather than
committed, so the tests carry no binary blobs and prove the real decode paths end to end:
WAV through `soundfile`, compressed through `miniaudio`, streaming through `miniaudio`'s
incremental decoder, and resampling through `soxr`.

Note on the compressed fixture: `soundfile` in this environment can also write MP3, so
`compressed_bytes` covers the true MP3 branch. FLAC and OGG are exercised too — all three
travel the identical `miniaudio.decode` / `miniaudio.stream_any` code (only the container
sniff differs), so if an MP3 encoder were ever unavailable, the FLAC/OGG cases alone would
still cover the compressed path.
"""

from __future__ import annotations

import io

import numpy as np
import pytest
import soundfile as sf

from carle.speak.decode import (
    DecodeError,
    RawPcmFormat,
    decode_clip,
    normalize,
    remix,
    resample,
    sniff_container,
    stream_pcm_blocks,
)


def tone(*, seconds: float = 0.2, samplerate: int = 48000, channels: int = 2) -> np.ndarray:
    """A short 440 Hz sine as a float32 `(frames, channels)` array."""
    t = np.linspace(0, seconds, int(samplerate * seconds), endpoint=False)
    wave = (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    return np.repeat(wave.reshape(-1, 1), channels, axis=1)


def encode(samples: np.ndarray, samplerate: int, fmt: str) -> bytes:
    """Encode samples to container `fmt` (WAV/FLAC/OGG/MP3) in memory via soundfile."""
    buffer = io.BytesIO()
    sf.write(buffer, samples, samplerate, format=fmt)
    return buffer.getvalue()


def chunked(data: bytes, size: int) -> list[bytes]:
    """Split bytes into `size`-byte chunks, mimicking bytes arriving over the wire."""
    return [data[i : i + size] for i in range(0, len(data), size)]


def test_decodes_wav_to_float32_at_expected_rate_and_channels():
    data = encode(tone(samplerate=44100, channels=2), 44100, "WAV")

    pcm = decode_clip(data, target_samplerate=44100, target_channels=2)

    assert pcm.samples.dtype == np.float32
    assert pcm.samplerate == 44100
    assert pcm.channels == 2
    assert pcm.samples.shape == (pcm.frames, 2)
    assert pcm.frames == pytest.approx(int(44100 * 0.2), abs=2)


def test_decodes_compressed_clip_without_any_external_binary():
    # Real MP3 fixture (soundfile encodes it here); the same miniaudio branch decodes
    # FLAC and OGG, so all three are asserted through one compressed decode path.
    for fmt in ("MP3", "FLAC", "OGG"):
        data = encode(tone(samplerate=44100, channels=2), 44100, fmt)

        pcm = decode_clip(data, target_samplerate=44100, target_channels=2)

        assert pcm.samples.dtype == np.float32, fmt
        assert pcm.samplerate == 44100, fmt
        assert pcm.channels == 2, fmt
        # Lossy codecs pad a little; just assert we got a plausible amount of audio.
        assert pcm.frames > int(44100 * 0.1), fmt


def test_incrementally_decodes_a_chunked_compressed_stream():
    data = encode(tone(seconds=0.4, samplerate=44100, channels=2), 44100, "MP3")
    # Small chunks so several source reads are needed — proves it does not need the whole
    # buffer up front; a generator (not a list) makes "consumed as it arrives" concrete.
    chunks = iter(chunked(data, 256))

    blocks = list(
        stream_pcm_blocks(
            chunks,
            target_samplerate=44100,
            target_channels=2,
            source_format="mp3",
            frames_per_block=1024,
        )
    )

    assert len(blocks) > 1  # PCM came out in multiple incremental blocks, not one buffer
    assert all(block.samples.dtype == np.float32 for block in blocks)
    assert all(block.channels == 2 for block in blocks)
    total_frames = sum(block.frames for block in blocks)
    assert total_frames > int(44100 * 0.3)


def test_stream_yields_first_block_before_the_source_is_exhausted():
    # Pulling the first PCM block must not require draining every chunk — the generator
    # stops reading once it has enough for a block, which is the backpressure U3 relies on.
    # A long, less-compressible clip (tone plus noise) well past miniaudio's ~64 KB
    # read-ahead, so decoding one leading block reads only a fraction of the source and
    # most chunks stay unread when the first PCM block comes out.
    rng = np.random.default_rng(0)
    t = np.linspace(0, 10.0, 44100 * 10, endpoint=False)
    signal = (0.3 * np.sin(2 * np.pi * 440 * t) + 0.1 * rng.standard_normal(len(t))).astype(
        np.float32
    )
    samples = np.repeat(signal.reshape(-1, 1), 2, axis=1)
    data = encode(samples, 44100, "MP3")
    total_chunks = len(chunked(data, 256))
    assert total_chunks > 300  # comfortably larger than the read-ahead buffer
    pulled = 0

    def counting_chunks() -> object:
        nonlocal pulled
        for chunk in chunked(data, 256):
            pulled += 1
            yield chunk

    stream = stream_pcm_blocks(
        counting_chunks(), target_samplerate=44100, target_channels=2, source_format="mp3"
    )
    first = next(stream)

    assert first.frames > 0
    assert 0 < pulled < total_chunks  # some, but not all, chunks consumed


def test_resamples_48k_to_44100_via_soxr():
    samples = tone(seconds=1.0, samplerate=48000, channels=2)

    out = resample(samples, 48000, 44100)

    assert out.dtype == np.float32
    assert out.shape[1] == 2
    assert out.shape[0] == pytest.approx(samples.shape[0] * 44100 / 48000, rel=0.001)


def test_decode_clip_resamples_to_the_target_device_rate():
    data = encode(tone(seconds=0.5, samplerate=48000, channels=2), 48000, "WAV")

    pcm = decode_clip(data, target_samplerate=44100, target_channels=2)

    assert pcm.samplerate == 44100
    assert pcm.frames == pytest.approx(int(48000 * 0.5) * 44100 / 48000, rel=0.01)


def test_mono_source_is_upmixed_to_stereo_not_silent_on_one_channel():
    mono = tone(seconds=0.2, samplerate=44100, channels=1)
    data = encode(mono, 44100, "WAV")

    pcm = decode_clip(data, target_samplerate=44100, target_channels=2)

    assert pcm.channels == 2
    assert pcm.samples.shape[1] == 2
    # Both channels carry the same signal — neither is silent (the half-volume bug).
    assert np.allclose(pcm.samples[:, 0], pcm.samples[:, 1])
    assert np.any(pcm.samples[:, 1] != 0.0)


def test_stereo_source_is_downmixed_to_mono():
    left = tone(seconds=0.1, samplerate=44100, channels=1)
    stereo = np.concatenate([left, -left], axis=1)

    out = remix(stereo, 1)

    assert out.shape[1] == 1
    # Averaging L and -L cancels to near silence, proving both channels were mixed.
    assert np.allclose(out, 0.0, atol=1e-6)


def test_raw_pcm_with_declared_format_is_accepted_and_normalized():
    # Full-scale int16 samples across two channels; declared, since there is no header.
    frames = np.array([[16384, -16384], [-32768, 32767]], dtype=np.int16)
    raw = frames.tobytes()

    pcm = decode_clip(
        raw,
        target_samplerate=44100,
        target_channels=2,
        declared=RawPcmFormat(samplerate=44100, channels=2, dtype="int16"),
    )

    assert pcm.samples.dtype == np.float32
    assert pcm.channels == 2
    assert pcm.frames == 2
    # int16 is scaled into [-1, 1): 16384/32768 == 0.5, -32768/32768 == -1.0.
    assert pcm.samples[0, 0] == pytest.approx(0.5, abs=1e-4)
    assert pcm.samples[1, 0] == pytest.approx(-1.0, abs=1e-4)


def test_raw_unsigned_8bit_pcm_is_centred_not_dc_offset():
    # 8-bit PCM is unsigned: silence is the midpoint 128, full scale is 0 and 255. Without
    # re-centring, every sample would land in [0, 1) and play with a constant DC offset.
    frames = np.array([[128, 128], [0, 0], [255, 255]], dtype=np.uint8)
    raw = frames.tobytes()

    pcm = decode_clip(
        raw,
        target_samplerate=44100,
        target_channels=2,
        declared=RawPcmFormat(samplerate=44100, channels=2, dtype="uint8"),
    )

    assert pcm.samples.dtype == np.float32
    # Midpoint 128 is silence (~0), not +0.5; 0 is the negative rail, 255 near the positive.
    assert pcm.samples[0, 0] == pytest.approx(0.0, abs=1e-4)
    assert pcm.samples[1, 0] == pytest.approx(-1.0, abs=1e-4)
    assert pcm.samples[2, 0] == pytest.approx(0.992, abs=1e-3)
    # The whole clip is zero-mean-ish, not biased positive (the DC-offset regression).
    assert abs(float(pcm.samples.mean())) < 0.01


def test_unsupported_garbage_bytes_raise_a_clear_decode_error():
    with pytest.raises(DecodeError):
        decode_clip(b"this is not audio at all", target_samplerate=44100)


def test_corrupt_stream_raises_decode_error_not_a_crash():
    garbage = iter([b"\x00\x01\x02\x03" * 64])
    with pytest.raises(DecodeError):
        list(
            stream_pcm_blocks(
                garbage, target_samplerate=44100, target_channels=2, source_format="mp3"
            )
        )


def test_sniff_container_routes_each_magic():
    assert sniff_container(encode(tone(), 48000, "WAV")) == "wav"
    assert sniff_container(encode(tone(), 48000, "FLAC")) == "flac"
    assert sniff_container(encode(tone(), 48000, "OGG")) == "ogg"
    assert sniff_container(encode(tone(), 48000, "MP3")) == "mp3"
    assert sniff_container(b"nope") == "unknown"


def test_normalize_mixes_before_resampling():
    mono = tone(seconds=0.2, samplerate=48000, channels=1)

    pcm = normalize(mono, 48000, target_samplerate=44100, target_channels=2)

    assert pcm.channels == 2
    assert pcm.samplerate == 44100
    assert pcm.samples.shape[1] == 2
    assert pcm.frames == pytest.approx(int(48000 * 0.2) * 44100 / 48000, rel=0.01)
