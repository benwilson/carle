"""The reference document must stay a faithful projection of ``commands.yaml``.

Two things are being protected here. First, that regeneration is deterministic and does
not eat hand-written prose. Second — and this is the one that matters — that no
verification or byte-level claim can live in the hand-written half, where none of the
table's invariants would ever see it.
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


def run_script(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        cwd=repo_root(),
    )


# --- Determinism ------------------------------------------------------------


def test_regeneration_is_byte_identical(doc_text):
    """Running the generator against an unchanged table must not alter the file."""
    assert splice(doc_text, render(load_table())) == doc_text


def test_check_passes_when_current():
    assert run_script("--check").returncode == 0


def test_check_fails_when_table_changes(tmp_path):
    """--check must detect drift, which is what makes the CI gate meaningful."""
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
    with pytest.raises(SystemExit, match="Markers not found"):
        main(["--doc", str(doc)])


# --- Faithfulness to the table ----------------------------------------------


def test_every_entry_is_rendered(doc_text):
    table = load_table()
    generated = doc_text[doc_text.find(BEGIN) : doc_text.find(END)]
    for entry in table.entries:
        assert f"`{entry.id}`" in generated, f"{entry.id} is missing from the reference"
        assert entry.status in generated


def test_unmapped_rows_render_no_encoding(doc_text):
    """An unmapped row must not show byte content, because it has none."""
    generated = doc_text[doc_text.find(BEGIN) : doc_text.find(END)]
    for line in generated.splitlines():
        if line.startswith("| `") and "unmapped" in line:
            cells = [c.strip() for c in line.split("|")]
            assert cells[4] == "—", f"unmapped row shows an encoding: {line}"


def test_confirmed_row_renders_its_observed_behavior(tmp_path):
    """Forward-looking: the generator must handle the states the hardware work produces."""
    table_path = tmp_path / "commands.yaml"
    original = (repo_root() / "protocol" / "commands.yaml").read_text(encoding="utf-8")
    table_path.write_text(
        original.replace(
            "  - id: move_forward\n"
            "    capability: Walk forward\n"
            "    category: movement\n"
            "    provenance: vendor-marketing\n"
            "    status: unmapped\n",
            "  - id: move_forward\n"
            "    capability: Walk forward\n"
            "    category: movement\n"
            "    provenance: decompile\n"
            "    status: confirmed\n"
            "    encoding: AA0102\n"
            "    derivation: CommandBuilder.walk\n"
            "    observed_behavior: Robot takes two steps forward\n"
            "    hardware_evidence:\n"
            "      date: 2026-08-11\n"
            "      platform: macOS\n"
            "      log: evidence/move_forward.log\n",
        ),
        encoding="utf-8",
    )
    body = render(load_table(table_path))
    assert "`AA0102`" in body
    assert "2026-08-11" in body


# --- The hand-written half cannot make protocol claims ----------------------

HEX_LITERAL = re.compile(r"\b(?:0x[0-9A-Fa-f]{2}|[0-9A-F]{2}(?:\s?[0-9A-F]{2}){2,})\b")


def test_handwritten_prose_contains_no_byte_literals(doc_text):
    """Byte content belongs in the table, where the invariants can police it."""
    prose = handwritten_regions(doc_text)
    assert HEX_LITERAL.findall(prose) == []


def test_handwritten_prose_names_no_command_ids(doc_text):
    """The hand-written half must make no per-capability claim.

    Without this, prose could assert a capability is confirmed while the table shows it
    unmapped, and every gate would still pass — the exact failure this repo exists to
    prevent. Discussing the status vocabulary in general terms stays fine.
    """
    prose = handwritten_regions(doc_text)
    offenders = [entry.id for entry in load_table().entries if entry.id in prose]
    assert offenders == [], f"hand-written prose names command ids: {offenders}"
