"""Property-based coverage for the frame codec — the wire format other implementers rely on.

`test_frame.py` pins hand-picked examples; this fuzzes `build`/`parse` across the whole input
space so the two contracts that matter to anyone re-implementing the protocol hold generally:

1. **Round-trip:** `parse(build(family, payload))` recovers the exact family and payload.
2. **Totality on the error path:** `build` and `parse` raise only `FrameError` — never a bare
   `TypeError`/`IndexError`/`ValueError` — for any input, so a caller decoding a corrupted log
   or an untrusted frame gets a clean, catchable error rather than a crash.
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from carle import frame
from carle.frame import FAMILIES, MAX_PAYLOAD, TERMINATOR, FrameError

families = st.sampled_from(sorted(FAMILIES))
payloads = st.lists(st.integers(min_value=0, max_value=0xFF), max_size=MAX_PAYLOAD)


@given(family=families, payload=payloads)
def test_build_then_parse_round_trips(family: int, payload: list[int]) -> None:
    recovered_family, recovered_payload = frame.parse(frame.build(family, payload))
    assert recovered_family == family
    assert recovered_payload == bytes(payload)


@given(family=families, payload=payloads)
def test_the_built_envelope_is_well_formed(family: int, payload: list[int]) -> None:
    wire = frame.build(family, payload)
    assert wire[0] == family
    assert wire[1] == len(payload)  # the length field
    assert wire[-2] == sum(payload) & 0xFF  # the checksum
    assert wire[-1] == TERMINATOR
    assert len(wire) == len(payload) + 4


@given(data=st.binary(max_size=300))
def test_parse_is_total_on_arbitrary_bytes(data: bytes) -> None:
    # The parser must be total: a well-formed envelope round-trips, and anything else is a
    # clean FrameError — never an IndexError/TypeError from indexing past the end or summing.
    try:
        family, payload = frame.parse(data)
    except FrameError:
        return
    assert isinstance(family, int) and isinstance(payload, bytes)
    # A successful parse means the bytes ARE a valid envelope: re-encoding reproduces them.
    reencoded = bytes([family, len(payload), *payload, frame.checksum(payload), TERMINATOR])
    assert reencoded == data


@given(family=families, payload=st.lists(st.integers(0, 0xFF), min_size=1, max_size=MAX_PAYLOAD))
def test_a_corrupted_payload_byte_is_rejected(family: int, payload: list[int]) -> None:
    wire = bytearray(frame.build(family, payload))
    # Flip every bit of the first payload byte. XOR 0xFF always changes a byte, and it always
    # changes the checksum (2*b == 255 mod 256 has no solution), so parse must reject it.
    wire[2] ^= 0xFF
    try:
        frame.parse(bytes(wire))
    except FrameError:
        return
    raise AssertionError("parse accepted a frame whose payload no longer matches its checksum")


@given(bad=st.one_of(st.none(), st.text(), st.floats(), st.booleans(), st.binary()))
def test_build_rejects_a_non_family_cleanly(bad: object) -> None:
    # The regression for build(None) raising 'unsupported format string passed to NoneType'
    # instead of a FrameError: any non-family value must be a clean, catchable FrameError.
    try:
        frame.build(bad, [1, 2])  # type: ignore[arg-type]
    except FrameError:
        return
    raise AssertionError(f"build accepted a non-family {bad!r} without raising FrameError")


@given(family=families, junk=st.one_of(st.none(), st.text(min_size=1), st.floats(), st.booleans()))
def test_build_rejects_a_non_byte_payload_element_cleanly(family: int, junk: object) -> None:
    try:
        frame.build(family, [1, junk, 2])  # type: ignore[list-item]
    except FrameError:
        return
    raise AssertionError(f"build accepted a non-byte payload element {junk!r} without FrameError")
