"""Frame construction, parsing, parameter resolution, and the U1 migration proof.

The migration test at the bottom is the important one. It is the only evidence that
replacing six stored frame strings with family-plus-payload templates did not quietly
change a single byte, and the stored strings it checks against are gone from the
working tree — they exist only in this file and in git history.
"""

from __future__ import annotations

import pytest

from carle import frame
from carle.table import load_table

FULL = {"min": 0, "max": 255, "default": 0}


# --- Building ---------------------------------------------------------------


def test_build_produces_the_documented_envelope():
    assert frame.build(0xB3, [0x03, 0x00]) == bytes([0xB3, 0x02, 0x03, 0x00, 0x03, 0xAA])


def test_checksum_truncates_to_eight_bits():
    """0x80 + 0x81 = 0x101, which must land as 0x01."""
    built = frame.build(0xB3, [0x80, 0x81])
    assert built[-2] == 0x01


def test_empty_payload_gives_zero_length_and_zero_checksum():
    assert frame.build(0xB3, []) == bytes([0xB3, 0x00, 0x00, 0xAA])


def test_build_rejects_an_undocumented_family():
    """With the checksum computed, an invented opcode would otherwise render as a
    perfectly well-formed frame, indistinguishable from a real one."""
    with pytest.raises(frame.FrameError, match="not one of the documented families"):
        frame.build(0xFF, [0x00])


def test_build_rejects_an_oversized_payload():
    with pytest.raises(frame.FrameError, match="at most 255"):
        frame.build(0xB3, [0] * 256)


def test_build_rejects_a_byte_outside_range():
    with pytest.raises(frame.FrameError, match="outside 0-255"):
        frame.build(0xB3, [0x00, 300])


# --- Parsing ----------------------------------------------------------------


def test_parse_round_trips_build():
    original_family, original_payload = 0xB6, bytes([1, 2, 3, 4, 5, 6])
    family, payload = frame.parse(frame.build(original_family, original_payload))
    assert (family, payload) == (original_family, original_payload)


def test_parse_rejects_a_bad_checksum():
    bad = bytearray(frame.build(0xB3, [0x03, 0x00]))
    bad[-2] ^= 0xFF
    with pytest.raises(frame.FrameError, match="checksum"):
        frame.parse(bytes(bad))


def test_parse_rejects_a_bad_terminator():
    bad = bytearray(frame.build(0xB3, [0x03, 0x00]))
    bad[-1] = 0x00
    with pytest.raises(frame.FrameError, match="terminator"):
        frame.parse(bytes(bad))


def test_parse_rejects_a_length_that_disagrees_with_the_content():
    bad = bytearray(frame.build(0xB3, [0x03, 0x00]))
    bad[1] = 0x05
    with pytest.raises(frame.FrameError, match="should be"):
        frame.parse(bytes(bad))


def test_parse_rejects_a_frame_shorter_than_the_envelope():
    with pytest.raises(frame.FrameError, match="envelope alone is 4"):
        frame.parse(bytes([0xB3, 0x00]))


# --- Resolution -------------------------------------------------------------


def test_resolution_uses_declared_defaults():
    resolved = frame.resolve(["0x03", "{index}"], {"index": {"min": 0, "max": 9, "default": 0}})
    assert resolved == bytes([0x03, 0x00])


def test_an_in_range_override_replaces_the_default():
    resolved = frame.resolve(
        ["0x03", "{index}"], {"index": {"min": 0, "max": 9, "default": 0}}, {"index": 7}
    )
    assert resolved == bytes([0x03, 0x07])


def test_an_out_of_range_override_is_rejected_naming_the_range():
    with pytest.raises(frame.FrameError, match="outside its documented range 0-2"):
        frame.resolve(["{level}"], {"level": {"min": 0, "max": 2, "default": 0}}, {"level": 5})


def test_an_unknown_override_is_rejected():
    with pytest.raises(frame.FrameError, match="no such parameter"):
        frame.resolve(["{level}"], {"level": dict(FULL)}, {"loudness": 1})


def test_byte_literals_accept_hex_and_decimal():
    assert frame.resolve(["0xB3", "179"]) == bytes([0xB3, 0xB3])


def test_a_nonsense_literal_is_rejected():
    with pytest.raises(frame.FrameError, match="not a byte literal"):
        frame.resolve(["banana"])


def test_referenced_parameters_finds_every_reference():
    assert frame.referenced_parameters(["0x00", "{a}", "{b}", "0x01"]) == {"a", "b"}


# --- Hex helpers ------------------------------------------------------------


def test_hex_round_trips():
    assert frame.from_hex(frame.to_hex(bytes([0xB3, 0x02, 0xAA]))) == bytes([0xB3, 0x02, 0xAA])


@pytest.mark.parametrize("text", ["B3:02:AA", "B3-02-AA", "B3 02 AA", "B3,02,AA"])
def test_from_hex_accepts_common_separators(text):
    assert frame.from_hex(text) == bytes([0xB3, 0x02, 0xAA])


def test_from_hex_rejects_non_hex():
    with pytest.raises(frame.FrameError, match="not a hex byte sequence"):
        frame.from_hex("not bytes")


# --- Migration proof (U1) ---------------------------------------------------
#
# The stored strings below are what protocol/commands.yaml held before the schema
# changed. Four were literal frames and are checked byte-for-byte. Two were
# placeholder templates containing `<sum>`, which no built frame can equal, so they
# are checked by rendering the new template back into placeholder form.

STORED_LITERAL_FRAMES = {
    "media_gymnastics": "B3 02 00 00 00 AA",
    "media_story": "B3 02 01 00 01 AA",
    "media_dance": "B3 02 02 00 02 AA",
    "media_music": "B3 02 03 00 03 AA",
}

STORED_TEMPLATES = {
    "volume_set": "B3 02 04 <level> <sum> AA",
    "move_rocker": "B6 06 <mode> <speed> <direction> <p3> <limb> <p5> <sum> AA",
    "move_gyro": "B5 05 <mode> 00 <direction> <limb> <sway> <sum> AA",
}


def entry(entry_id: str):
    return next(e for e in load_table().entries if e.id == entry_id)


@pytest.mark.parametrize(("entry_id", "stored"), sorted(STORED_LITERAL_FRAMES.items()))
def test_literal_rows_rebuild_byte_for_byte(entry_id, stored):
    assert frame.to_hex(entry(entry_id).build_frame()) == stored


@pytest.mark.parametrize(("entry_id", "stored"), sorted(STORED_TEMPLATES.items()))
def test_template_rows_rerender_to_their_stored_placeholder_form(entry_id, stored):
    row = entry(entry_id)
    assert frame.render_template(row.family, row.payload) == stored


def test_every_migrated_row_is_covered_by_one_of_the_two_checks():
    """Guards against a seventh encoded row appearing with no migration evidence."""
    encoded = {e.id for e in load_table().entries if e.has_frame}
    assert encoded == set(STORED_LITERAL_FRAMES) | set(STORED_TEMPLATES)


def test_the_track_selection_experiment_is_one_parameter_away():
    """The open question the decompile could not settle, expressed as data."""
    assert frame.to_hex(entry("media_music").build_frame({"index": 3})) == "B3 02 03 03 06 AA"
