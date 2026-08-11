"""The honesty gate.

These tests are the mechanism that keeps the command table trustworthy as it fills.
Every one of them is written to fail against a deliberately malformed table, and each
malformed case fails for its own distinct reason rather than a shared parse error.

If you are changing these, read CONTRIBUTING.md first — the rules they enforce are the
point of the repository, not incidental scaffolding.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml

from carle.table import (
    PUBLISHED_COUNTS,
    TableError,
    load_seeded_ids,
    load_table,
    validate_table,
)

VALID_ROW = {
    "id": "song_01",
    "capability": "Song 1 of 10",
    "category": "song",
    "provenance": "vendor-marketing",
    "status": "unmapped",
}


def write_table(tmp_path: Path, rows: list[dict], note: str = "test fixture") -> Path:
    path = tmp_path / "commands.yaml"
    path.write_text(
        yaml.safe_dump({"coverage_note": note, "commands": rows}, sort_keys=False),
        encoding="utf-8",
    )
    return path


def seeded_rows() -> list[dict]:
    """A minimal table that satisfies every published count."""
    rows: list[dict] = []
    for category, count in PUBLISHED_COUNTS.items():
        for index in range(1, count + 1):
            rows.append(
                {
                    "id": f"{category}_{index:02d}",
                    "capability": f"{category} {index}",
                    "category": category,
                    "provenance": "vendor-marketing",
                    "status": "unmapped",
                }
            )
    return rows


def problems_for(tmp_path: Path, rows: list[dict], **kwargs) -> list[str]:
    table = load_table(write_table(tmp_path, rows))
    return validate_table(table, root=tmp_path, **kwargs)


# --- The real table ---------------------------------------------------------


def test_real_table_passes_every_invariant():
    table = load_table()
    assert validate_table(table, seeded_ids=load_seeded_ids()) == []


def test_real_table_has_no_encodings_yet():
    """This slice ships structure, not protocol content. Guards against a stray encoding."""
    table = load_table()
    assert [e.id for e in table.entries if e.has_encoding] == []


# --- Evidence rules (AE1) ---------------------------------------------------


def test_confirmed_without_hardware_evidence_fails(tmp_path):
    row = {
        **VALID_ROW,
        "provenance": "decompile",
        "status": "confirmed",
        "encoding": "AA0102",
        "derivation": "CommandBuilder.playSong",
        "observed_behavior": "Robot plays a song",
    }
    problems = problems_for(tmp_path, [row])
    assert any("requires hardware_evidence" in p for p in problems)


def test_decoded_carrying_hardware_evidence_fails(tmp_path):
    """Hardware evidence means the entry is confirmed, not decoded."""
    row = {
        **VALID_ROW,
        "provenance": "decompile",
        "status": "decoded",
        "encoding": "AA0102",
        "derivation": "CommandBuilder.playSong",
        "hardware_evidence": {"date": "2026-08-11", "platform": "macOS", "log": "evidence/x.log"},
    }
    problems = problems_for(tmp_path, [row])
    assert any("must not carry hardware_evidence" in p for p in problems)


def test_hardware_evidence_log_must_exist_on_disk(tmp_path):
    """A presence check would let `log: anything` pass. The path has to resolve."""
    row = {
        **VALID_ROW,
        "provenance": "decompile",
        "status": "confirmed",
        "encoding": "AA0102",
        "derivation": "CommandBuilder.playSong",
        "observed_behavior": "Robot plays a song",
        "hardware_evidence": {
            "date": "2026-08-11",
            "platform": "macOS",
            "log": "evidence/does-not-exist.log",
        },
    }
    problems = problems_for(tmp_path, [row])
    assert any("does not exist on disk" in p for p in problems)


def test_hardware_evidence_log_that_exists_passes(tmp_path):
    (tmp_path / "evidence").mkdir()
    (tmp_path / "evidence" / "song_01.log").write_text("observed", encoding="utf-8")
    row = {
        **VALID_ROW,
        "provenance": "decompile",
        "status": "confirmed",
        "encoding": "AA0102",
        "derivation": "CommandBuilder.playSong",
        "observed_behavior": "Robot plays a song",
        "hardware_evidence": {
            "date": "2026-08-11",
            "platform": "macOS",
            "log": "evidence/song_01.log",
        },
    }
    problems = [p for p in problems_for(tmp_path, [row]) if p.startswith("song_01")]
    assert problems == []


def test_hardware_evidence_date_must_be_iso(tmp_path):
    (tmp_path / "evidence").mkdir()
    (tmp_path / "evidence" / "song_01.log").write_text("observed", encoding="utf-8")
    row = {
        **VALID_ROW,
        "provenance": "decompile",
        "status": "confirmed",
        "encoding": "AA0102",
        "derivation": "CommandBuilder.playSong",
        "observed_behavior": "Robot plays a song",
        "hardware_evidence": {
            "date": "last Tuesday",
            "platform": "macOS",
            "log": "evidence/song_01.log",
        },
    }
    problems = problems_for(tmp_path, [row])
    assert any("is not an ISO 8601 date" in p for p in problems)


def test_decoded_without_derivation_fails(tmp_path):
    """R11: a decoded frame must record where in the app it came from."""
    row = {
        **VALID_ROW,
        "provenance": "decompile",
        "status": "decoded",
        "encoding": "AA0102",
    }
    problems = problems_for(tmp_path, [row])
    assert any("requires a derivation" in p for p in problems)


# --- State rules ------------------------------------------------------------


def test_unmapped_carrying_an_encoding_fails(tmp_path):
    row = {**VALID_ROW, "encoding": "AA0102"}
    problems = problems_for(tmp_path, [row])
    assert any("must not carry encoding" in p for p in problems)


def test_unlocated_carrying_an_encoding_fails(tmp_path):
    row = {**VALID_ROW, "status": "unlocated", "encoding": "AA0102"}
    problems = problems_for(tmp_path, [row])
    assert any("must not carry encoding" in p for p in problems)


def test_unlocated_without_an_encoding_passes(tmp_path):
    """'Searched and not found' is a legitimate resting state, distinct from 'unmapped'."""
    row = {**VALID_ROW, "status": "unlocated"}
    problems = [p for p in problems_for(tmp_path, [row]) if p.startswith("song_01")]
    assert problems == []


def test_unknown_status_is_rejected(tmp_path):
    row = {**VALID_ROW, "status": "probably-works"}
    with pytest.raises(TableError, match="expected one of"):
        load_table(write_table(tmp_path, [row]))


# --- Provenance rule --------------------------------------------------------


def test_vendor_marketing_row_cannot_carry_an_encoding(tmp_path):
    """A marketing row describes a capability; only the decompile produces an encoding."""
    row = {
        **VALID_ROW,
        "provenance": "vendor-marketing",
        "status": "decoded",
        "encoding": "AA0102",
        "derivation": "CommandBuilder.playSong",
    }
    problems = problems_for(tmp_path, [row])
    assert any("must not carry an encoding" in p for p in problems)


# --- Coverage rules (AE2) ---------------------------------------------------


def test_dropping_a_seeded_id_fails(tmp_path):
    rows = seeded_rows()
    dropped = rows.pop(0)
    problems = problems_for(tmp_path, rows, seeded_ids=[dropped["id"]])
    assert any("seeded id is absent" in p for p in problems)


def test_retaining_a_seeded_id_with_superseded_by_passes(tmp_path):
    """The decompile may merge rows. That is recorded, not erased."""
    rows = seeded_rows()
    rows[0] = {**rows[0], "superseded_by": ["song_all"]}
    problems = problems_for(tmp_path, rows, seeded_ids=[rows[0]["id"]])
    assert [p for p in problems if "seeded id is absent" in p] == []


def test_nine_songs_instead_of_ten_fails(tmp_path):
    rows = [r for r in seeded_rows() if r["id"] != "song_10"]
    problems = problems_for(tmp_path, rows)
    assert any("category 'song' has 9 rows; Ruko publishes 10" in p for p in problems)


def test_duplicate_ids_fail(tmp_path):
    rows = seeded_rows()
    rows.append({**rows[0]})
    problems = problems_for(tmp_path, rows)
    assert any("id appears 2 times" in p for p in problems)


# --- Structural rules -------------------------------------------------------


def test_unknown_field_is_rejected(tmp_path):
    row = {**VALID_ROW, "confidence": "pretty sure"}
    with pytest.raises(TableError, match="unknown field"):
        load_table(write_table(tmp_path, [row]))


def test_missing_coverage_note_is_rejected(tmp_path):
    path = tmp_path / "commands.yaml"
    path.write_text(yaml.safe_dump({"commands": [VALID_ROW]}), encoding="utf-8")
    with pytest.raises(TableError, match="coverage_note"):
        load_table(path)


def test_malformed_yaml_is_rejected(tmp_path):
    path = tmp_path / "commands.yaml"
    path.write_text(
        textwrap.dedent("""\
        coverage_note: "unterminated
        commands: [
    """),
        encoding="utf-8",
    )
    with pytest.raises(TableError, match="not valid YAML"):
        load_table(path)
