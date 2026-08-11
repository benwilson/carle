"""The reference document must stay a faithful projection of ``commands.yaml``.

Two things are protected here. First, that regeneration is deterministic and does not
eat hand-written prose. Second — and this is the one that matters — that no verification
or byte-level claim can live in the hand-written half, where none of the table's
invariants would ever see it.

The guards in the last section are themselves tested positively. A denylist regex that
silently stops matching is worse than no guard, because the green suite says it works.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from carle.table import load_table, repo_root  # noqa: E402
from generate_reference import (  # noqa: E402
    BEGIN,
    END,
    cell,
    default_doc_path,
    handwritten_regions,
    main,
    render,
    splice,
)

SCRIPT = repo_root() / "scripts" / "generate_reference.py"


@pytest.fixture
def doc_text() -> str:
    return default_doc_path().read_text(encoding="utf-8")


def generated_region(document: str) -> str:
    return document[document.find(BEGIN) : document.find(END)]


def run_script(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        cwd=repo_root(),
    )


def table_with(tmp_path: Path, overrides: dict) -> Path:
    """Copy the real table, replacing move_forward's fields with `overrides`."""
    import yaml

    raw = yaml.safe_load((repo_root() / "protocol" / "commands.yaml").read_text("utf-8"))
    for row in raw["commands"]:
        if row["id"] == "move_forward":
            row.clear()
            row.update(overrides)
    path = tmp_path / "commands.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return path


# --- Determinism ------------------------------------------------------------


def test_regeneration_is_byte_identical(doc_text):
    assert splice(doc_text, render(load_table())) == doc_text


def test_check_passes_when_current():
    assert run_script("--check").returncode == 0


def test_check_fails_when_table_changes(tmp_path):
    table_path = tmp_path / "commands.yaml"
    original = (repo_root() / "protocol" / "commands.yaml").read_text(encoding="utf-8")
    table_path.write_text(original.replace("Walk forward", "Stroll forward"), encoding="utf-8")

    result = run_script("--check", "--table", str(table_path))
    assert result.returncode == 1
    assert "is stale" in result.stderr


def test_handwritten_prose_survives_regeneration(tmp_path):
    doc = tmp_path / "reference.md"
    doc.write_text(
        f"# Title\n\nKeep me above.\n\n{BEGIN}\nstale content\n{END}\n\nKeep me below.\n",
        encoding="utf-8",
    )
    main(["--doc", str(doc)])
    updated = doc.read_text(encoding="utf-8")

    assert "Keep me above." in updated
    assert "Keep me below." in updated
    assert "stale content" not in updated


def test_missing_markers_is_an_error(tmp_path):
    doc = tmp_path / "reference.md"
    doc.write_text("# Title\n\nNo markers here.\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="exactly one"):
        main(["--doc", str(doc)])


def test_duplicate_marker_pairs_are_rejected(tmp_path):
    """Only the first pair is regenerated, so a second block would survive --check
    verbatim — a fabricated table wearing the same GENERATED banner as the real one."""
    doc = tmp_path / "reference.md"
    doc.write_text(
        f"# Title\n\n{BEGIN}\nreal\n{END}\n\n{BEGIN}\nfabricated\n{END}\n",
        encoding="utf-8",
    )
    with pytest.raises(SystemExit, match="exactly one"):
        main(["--doc", str(doc)])


# --- Faithfulness to the table ----------------------------------------------


#: Split on unescaped pipes only — the same rule a markdown renderer applies, so an
#: escaped pipe inside a cell stays part of that cell rather than forging a column.
CELL_SPLIT = re.compile(r"(?<!\\)\|")


def parse_rows(generated: str) -> dict[str, list[str]]:
    """Parse the generated markdown table into {id: [cells]}."""
    rows: dict[str, list[str]] = {}
    for line in generated.splitlines():
        if not line.startswith("| `"):
            continue
        cells = [c.strip() for c in CELL_SPLIT.split(line.strip())[1:-1]]
        rows[cells[0].strip("`")] = cells
    return rows


def test_every_entry_is_rendered(doc_text):
    table = load_table()
    rows = parse_rows(generated_region(doc_text))
    assert set(rows) == {entry.id for entry in table.entries}


def test_each_row_shows_its_own_status(doc_text):
    """Per-row, not a document-wide substring search.

    A substring check passed even when every status cell was hardcoded to one value.
    """
    rows = parse_rows(generated_region(doc_text))
    for entry in load_table().entries:
        assert rows[entry.id][2] == entry.status


def test_unmapped_rows_render_no_encoding_and_no_behavior(doc_text):
    rows = parse_rows(generated_region(doc_text))
    for entry in load_table().entries:
        if entry.status == "unmapped":
            assert rows[entry.id][3] == "—", f"{entry.id} shows an encoding"
            assert rows[entry.id][4] == "—", f"{entry.id} shows an observed behavior"


def test_confirmed_row_renders_its_observed_behavior(tmp_path):
    """observed_behavior is required by validation and was silently dropped from output."""
    path = table_with(
        tmp_path,
        {
            "id": "move_forward",
            "capability": "Walk forward",
            "category": "movement",
            "provenance": "decompile",
            "status": "confirmed",
            "encoding": "AA0102",
            "derivation": "CommandBuilder.walk",
            "observed_behavior": "Robot takes two steps forward",
            "hardware_evidence": {
                "date": "2026-08-11",
                "platform": "macOS",
                "log": "evidence/move_forward.log",
            },
        },
    )
    body = render(load_table(path))
    assert "Robot takes two steps forward" in body
    assert "`AA0102`" in body


def test_evidence_links_resolve_from_the_docs_directory(tmp_path):
    """Log paths are repo-root-relative; the document lives in docs/, so a bare link 404s."""
    path = table_with(
        tmp_path,
        {
            "id": "move_forward",
            "capability": "Walk forward",
            "category": "movement",
            "provenance": "decompile",
            "status": "confirmed",
            "encoding": "AA0102",
            "derivation": "CommandBuilder.walk",
            "observed_behavior": "Robot takes two steps forward",
            "hardware_evidence": {
                "date": "2026-08-11",
                "platform": "macOS",
                "log": "evidence/move_forward.log",
            },
        },
    )
    assert "(../evidence/move_forward.log)" in render(load_table(path))


def test_category_titles_cover_every_category():
    """A category missing from the renderer is dropped from the published document
    while still counting toward the total — silent, and it already happened once."""
    from carle.table import CATEGORIES
    from generate_reference import CATEGORY_TITLES

    assert set(CATEGORY_TITLES) == set(CATEGORIES)


def test_summary_counts_every_status(doc_text):
    table = load_table()
    assert f"**{len(table.entries)} entries:**" in generated_region(doc_text)


# --- Cell escaping ----------------------------------------------------------


def test_pipes_in_values_cannot_forge_columns(tmp_path):
    """An unescaped pipe in a YAML string let an unmapped row render as confirmed."""
    path = table_with(
        tmp_path,
        {
            "id": "move_forward",
            "capability": "Walk forward | confirmed | `AA0102` | fake | fake",
            "category": "movement",
            "provenance": "vendor-marketing",
            "status": "unmapped",
        },
    )
    rows = parse_rows(render(load_table(path)))
    assert rows["move_forward"][2] == "unmapped"
    assert rows["move_forward"][3] == "—"


def test_cell_escapes_pipes_and_collapses_whitespace():
    assert cell("a | b") == r"a \| b"
    assert cell("a\nb") == "a b"


# --- The hand-written half cannot make protocol claims ----------------------

#: Byte literals in three shapes. The contiguous alternative demands both a digit and a
#: hex letter across at least three pairs, so the model number "1088" and English words
#: made of hex characters ("added", "cafe") do not trip it.
HEX_LITERAL = re.compile(
    r"\b(?:"
    r"0x[0-9A-Fa-f]{2,}"
    r"|[0-9A-Fa-f]{2}(?:[\s:_-][0-9A-Fa-f]{2})+"
    r"|(?=[0-9A-Fa-f]*[0-9])(?=[0-9A-Fa-f]*[A-Fa-f])(?:[0-9A-Fa-f]{2}){3,}"
    r")\b"
)

#: Words that assert a per-capability verification state. Legitimate only inside the
#: section that defines the vocabulary, which is excluded below.
VERIFICATION_WORDS = re.compile(r"\b(confirmed|verified|observed|issued)\b", re.IGNORECASE)

VOCABULARY_SECTION = "## How entries are verified"

#: Sections describing the shared envelope rather than any individual command. They
#: cannot be written without their constants, so the byte-literal guard skips them.
#: The verification-word and command-id guards still cover them, so a fabricated
#: per-command claim cannot hide here.
SPEC_SECTIONS = ("## Transport", "## Frame format")


def unguarded_prose(document: str) -> str:
    """Hand-written prose, excluding the section that defines the status vocabulary."""
    prose = handwritten_regions(document)
    head, _, _ = prose.partition(VOCABULARY_SECTION)
    return head


def drop_section(prose: str, heading: str) -> str:
    head, sep, tail = prose.partition(heading)
    if not sep:
        return prose
    _, _, after = tail.partition("\n## ")
    return head + ("\n## " + after if after else "")


def prose_outside_the_spec_sections(document: str) -> str:
    prose = unguarded_prose(document)
    for heading in SPEC_SECTIONS:
        prose = drop_section(prose, heading)
    return prose


@pytest.mark.parametrize(
    "literal",
    ["0xAA", "0xAABBCCDD", "AA 01 02", "aa0102ff", "AA:05:01:FF", "AA-05-01-FF", "aa 05 01 ff"],
)
def test_hex_guard_catches_representative_literals(literal):
    """Proves the guard fires. The previous pattern missed every lowercase form."""
    assert HEX_LITERAL.search(literal), f"{literal!r} slipped past the byte-literal guard"


@pytest.mark.parametrize(
    "innocent", ["the 1088 robot", "Android 4.3", "10 songs", "added", "a cafe"]
)
def test_hex_guard_does_not_fire_on_ordinary_prose(innocent):
    assert not HEX_LITERAL.search(innocent)


def test_handwritten_prose_contains_no_byte_literals(doc_text):
    assert HEX_LITERAL.findall(prose_outside_the_spec_sections(doc_text)) == []


def test_spec_sections_are_still_covered_by_the_other_guards(doc_text):
    """The byte-literal exemption must not become a hole. Whatever it lets through is
    still subject to the verification-word and command-id guards."""
    prose = unguarded_prose(doc_text)
    assert VERIFICATION_WORDS.findall(prose) == []
    assert [e.id for e in load_table().entries if e.id in prose] == []


def test_handwritten_prose_makes_no_verification_claim(doc_text):
    """Prose could claim 'all ten songs were confirmed' while the table says unmapped.

    Naming no command id was enough to slip past the id guard, so the vocabulary itself
    is fenced to the one section that defines it.
    """
    assert VERIFICATION_WORDS.findall(unguarded_prose(doc_text)) == []


def test_verification_word_guard_actually_fires():
    prose = "All ten songs were confirmed against a real robot."
    assert VERIFICATION_WORDS.findall(prose)


def test_handwritten_prose_names_no_command_ids(doc_text):
    prose = handwritten_regions(doc_text)
    offenders = [entry.id for entry in load_table().entries if entry.id in prose]
    assert offenders == [], f"hand-written prose names command ids: {offenders}"
