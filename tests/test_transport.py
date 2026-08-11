"""Transport-layer units that do not need a robot.

The Bleak-facing half of `BleakBackend.send` cannot be proven here — no adapter, no
peripheral. What can be proven is the framing around it.
"""

from __future__ import annotations

from carle.transport import CHUNK_SIZE, SendResult, chunked


def test_a_short_frame_is_one_chunk():
    assert chunked(bytes([0xB3, 0x02, 0x03, 0x00, 0x03, 0xAA])) == [
        bytes([0xB3, 0x02, 0x03, 0x00, 0x03, 0xAA])
    ]


def test_a_long_payload_splits_at_the_app_s_limit():
    data = bytes(range(50))
    parts = chunked(data)
    assert [len(p) for p in parts] == [CHUNK_SIZE, CHUNK_SIZE, 10]
    assert b"".join(parts) == data


def test_an_exact_multiple_does_not_produce_an_empty_tail():
    parts = chunked(bytes(CHUNK_SIZE * 2))
    assert len(parts) == 2


def test_an_empty_payload_still_yields_one_write():
    assert chunked(b"") == [b""]


def test_a_send_result_defaults_to_no_notifications():
    assert SendResult(ok=True).notifications == []
