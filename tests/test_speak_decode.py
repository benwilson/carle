"""U2 — audio decoding and normalization into one internal PCM shape.

Fixtures are synthesized in-test with `soundfile` (a short sine tone) — plus `av` for the
one format nothing else here encodes (ADTS AAC) — rather than committed, so the tests
carry no binary blobs and prove the real decode paths end to end: WAV clips through
`soundfile`, compressed clips through `miniaudio` (AAC/Opus clips through the `av`
fallback), streaming through `av`'s incremental demux/decode, raw declared PCM through
the frame-aligned carry path, and resampling through `soxr`.

Note on the compressed clip fixture: `soundfile` in this environment can also write MP3,
so the clip tests cover the true MP3 branch; FLAC and OGG travel the identical
`miniaudio.decode` code (only the container sniff differs). The stream tests
parameterize over every container a TTS pipeline plausibly pipes: WAV, FLAC, Ogg-Vorbis,
MP3, Ogg-Opus, ADTS AAC, and declared raw PCM.
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


# --- the TTS stream formats (the av path) ---------------------------------------------


def encode_ogg_opus(samples: np.ndarray, samplerate: int) -> bytes:
    """Encode an Ogg-Opus fixture via soundfile (libsndfile carries an Opus encoder)."""
    buffer = io.BytesIO()
    sf.write(buffer, samples, samplerate, format="OGG", subtype="OPUS")
    return buffer.getvalue()


def encode_adts_aac(samples: np.ndarray, samplerate: int) -> bytes:
    """Encode an ADTS AAC fixture with `av` (neither soundfile nor miniaudio does AAC)."""
    import av

    buffer = io.BytesIO()
    interleaved = np.ascontiguousarray(samples, dtype=np.float32).reshape(1, -1)
    with av.open(buffer, mode="w", format="adts") as container:
        stream = container.add_stream("aac", rate=samplerate)
        stream.layout = "stereo"
        frame = av.AudioFrame.from_ndarray(interleaved, format="flt", layout="stereo")
        frame.sample_rate = samplerate
        for packet in stream.encode(frame):
            container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    return buffer.getvalue()


@pytest.mark.parametrize("fmt", ["WAV", "FLAC", "OGG", "MP3"])
def test_stream_decodes_every_sniffable_container(fmt):
    # WAV and FLAC streamed nothing (or zero frames, silently) on the old decoder; all
    # four are formats a TTS pipeline may pipe at the stream endpoint.
    data = encode(tone(seconds=0.4, samplerate=44100, channels=2), 44100, fmt)

    blocks = list(
        stream_pcm_blocks(iter(chunked(data, 512)), target_samplerate=44100, target_channels=2)
    )

    total = sum(block.frames for block in blocks)
    assert total > int(44100 * 0.3)
    assert all(block.samples.dtype == np.float32 for block in blocks)
    assert all(block.channels == 2 for block in blocks)


def test_stream_decodes_ogg_opus():
    # Opus shares OGG's magic with Vorbis but is a different codec — the case the old
    # vorbis-only decoder could never play. Opus is fixed at 48 kHz internally.
    data = encode_ogg_opus(tone(seconds=0.4, samplerate=48000, channels=2), 48000)

    blocks = list(
        stream_pcm_blocks(iter(chunked(data, 512)), target_samplerate=44100, target_channels=2)
    )

    assert sum(block.frames for block in blocks) > int(44100 * 0.3)


def test_stream_decodes_adts_aac():
    data = encode_adts_aac(tone(seconds=0.4, samplerate=44100, channels=2), 44100)

    assert sniff_container(data) == "aac"
    blocks = list(
        stream_pcm_blocks(iter(chunked(data, 512)), target_samplerate=44100, target_channels=2)
    )

    assert sum(block.frames for block in blocks) > int(44100 * 0.3)


def test_stream_decodes_declared_raw_pcm_across_chunk_boundaries():
    samples = tone(seconds=0.4, samplerate=22050, channels=1)
    pcm16 = (samples * 32767.0).astype(np.int16).tobytes()
    declared = RawPcmFormat(samplerate=22050, channels=1, dtype="int16")

    # An odd chunk size splits frames across chunk boundaries; the carry must keep every
    # frame. The declared rate differs from the target so the stateful resampler runs.
    blocks = list(
        stream_pcm_blocks(
            iter(chunked(pcm16, 333)),
            target_samplerate=44100,
            target_channels=2,
            declared=declared,
        )
    )

    total = sum(block.frames for block in blocks)
    assert all(block.channels == 2 for block in blocks)
    assert total == pytest.approx(int(22050 * 0.4) * 2, rel=0.02)


def test_raw_stream_ending_mid_frame_is_an_error():
    declared = RawPcmFormat(samplerate=22050, channels=2, dtype="int16")

    with pytest.raises(DecodeError):
        list(
            stream_pcm_blocks(
                iter([b"\x00" * 6]),  # six bytes: one and a half 4-byte frames
                target_samplerate=22050,
                target_channels=2,
                declared=declared,
            )
        )


def test_garbage_stream_is_an_error_not_a_silent_success():
    # The old decoder could "complete" a stream that produced zero PCM frames — the
    # caller got 200 and the robot said nothing. Any zero-frame outcome must raise.
    with pytest.raises(DecodeError):
        list(stream_pcm_blocks(iter([b"\x00" * 9000]), target_samplerate=44100))


def test_empty_stream_is_an_error():
    with pytest.raises(DecodeError):
        list(stream_pcm_blocks(iter([]), target_samplerate=44100))


def test_clip_decodes_ogg_opus_and_adts_aac():
    # Both land on the av fallback: Opus past miniaudio's vorbis-only OGG path, AAC past
    # the sniffer's new ADTS branch.
    for data in (
        encode_ogg_opus(tone(seconds=0.4, samplerate=48000, channels=2), 48000),
        encode_adts_aac(tone(seconds=0.4, samplerate=44100, channels=2), 44100),
    ):
        pcm = decode_clip(data, target_samplerate=44100, target_channels=2)
        assert pcm.samples.dtype == np.float32
        assert pcm.frames > int(44100 * 0.3)


def test_stream_blocks_never_exceed_the_player_block_size():
    # The StreamPlayer hands one block to one device callback and TRUNCATES an oversized
    # block, so any block over `frames_per_block` plays sped-up and garbled (the WAV/FLAC
    # bug heard on hardware 2026-08-13: a WAV demuxer's packets dwarf an MP3 frame). Every
    # format must come out re-cut to the device cadence: all blocks exactly
    # `frames_per_block` frames except a short final one.
    sources = [
        encode(tone(seconds=0.4, samplerate=44100, channels=2), 44100, "WAV"),
        encode(tone(seconds=0.4, samplerate=44100, channels=2), 44100, "FLAC"),
        encode(tone(seconds=0.4, samplerate=44100, channels=2), 44100, "MP3"),
        encode_ogg_opus(tone(seconds=0.4, samplerate=48000, channels=2), 48000),
        encode_adts_aac(tone(seconds=0.4, samplerate=44100, channels=2), 44100),
    ]
    for data in sources:
        blocks = list(
            stream_pcm_blocks(
                iter(chunked(data, 512)),
                target_samplerate=44100,
                target_channels=2,
                frames_per_block=1024,
            )
        )
        assert all(block.frames == 1024 for block in blocks[:-1])
        assert blocks[-1].frames <= 1024

    # The raw declared path re-cuts too: one big posted body must not become huge blocks.
    samples = tone(seconds=0.4, samplerate=44100, channels=1)
    pcm16 = (samples * 32767.0).astype(np.int16).tobytes()
    blocks = list(
        stream_pcm_blocks(
            iter([pcm16]),  # a single giant chunk
            target_samplerate=44100,
            target_channels=2,
            declared=RawPcmFormat(samplerate=44100, channels=1, dtype="int16"),
            frames_per_block=1024,
        )
    )
    assert all(block.frames == 1024 for block in blocks[:-1])
    assert blocks[-1].frames <= 1024
