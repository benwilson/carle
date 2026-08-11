#!/usr/bin/env python3
"""Render the command table from ``protocol/commands.yaml`` into the reference document.

The table lives between two markers in ``docs/protocol-reference.md``. Prose outside
those markers is hand-written and survives regeneration untouched.

    uv run python scripts/generate_reference.py           # write
    uv run python scripts/generate_reference.py --check    # verify, exit 1 on drift

CI runs the ``--check`` form, so a stale reference fails the build.
"""

from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from carle.table import Table, load_table, repo_root  # noqa: E402

BEGIN = "<!-- BEGIN GENERATED COMMAND TABLE -->"
END = "<!-- END GENERATED COMMAND TABLE -->"

CATEGORY_TITLES = {
    "movement": "Movement",
    "song": "Songs",
    "dance": "Dance tracks",
    "gymnastic": "Gymnastic routines",
    "story": "Stories",
    "voice": "Voice commands",
}

STATUS_LABELS = {
    "unmapped": "unmapped",
    "unlocated": "unlocated",
    "decoded": "decoded",
    "confirmed": "confirmed",
}


def default_doc_path() -> Path:
    return repo_root() / "docs" / "protocol-reference.md"


def render(table: Table) -> str:
    """Build the generated region's body, excluding the markers themselves."""
    lines: list[str] = []

    lines.append("> **Coverage note.** This table's row set is seeded from vendor-published")
    lines.append("> capability counts, not from the protocol. It is not a complete list of")
    lines.append("> protocol commands.")
    lines.append(">")
    for note_line in table.coverage_note.rstrip().splitlines():
        lines.append(f"> {note_line}".rstrip())
    lines.append("")

    counts: dict[str, int] = {}
    for entry in table.entries:
        counts[entry.status] = counts.get(entry.status, 0) + 1
    summary = ", ".join(f"{counts[s]} {STATUS_LABELS[s]}" for s in STATUS_LABELS if s in counts)
    lines.append(f"**{len(table.entries)} entries:** {summary}.")
    lines.append("")

    for category, title in CATEGORY_TITLES.items():
        entries = table.by_category(category)
        if not entries:
            continue
        lines.append(f"### {title}")
        lines.append("")
        lines.append("| ID | Capability | Status | Encoding | Evidence |")
        lines.append("|---|---|---|---|---|")
        for entry in entries:
            encoding = f"`{entry.encoding}`" if entry.encoding else "—"
            evidence = "—"
            if entry.hardware_evidence:
                log = entry.hardware_evidence.get("log", "")
                date = entry.hardware_evidence.get("date", "")
                evidence = f"[{date}]({log})" if log else str(date)
            elif entry.derivation:
                evidence = f"derived: `{entry.derivation}`"
            capability = entry.capability
            if entry.superseded_by:
                capability += f" (superseded by {', '.join(entry.superseded_by)})"
            lines.append(
                f"| `{entry.id}` | {capability} | {entry.status} | {encoding} | {evidence} |"
            )
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def splice(document: str, body: str) -> str:
    """Replace the region between the markers, leaving hand-written prose intact."""
    start = document.find(BEGIN)
    end = document.find(END)
    if start == -1 or end == -1:
        raise SystemExit(
            f"Markers not found in the reference document. Expected {BEGIN} and {END}."
        )
    if end < start:
        raise SystemExit("Reference document markers are in the wrong order.")
    head = document[: start + len(BEGIN)]
    tail = document[end:]
    return f"{head}\n\n{body}\n{tail}"


def handwritten_regions(document: str) -> str:
    """Everything outside the generated markers."""
    start = document.find(BEGIN)
    end = document.find(END)
    if start == -1 or end == -1:
        return document
    return document[:start] + document[end + len(END) :]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero if the document is stale instead of rewriting it",
    )
    parser.add_argument("--table", type=Path, default=None, help="path to commands.yaml")
    parser.add_argument("--doc", type=Path, default=None, help="path to the reference document")
    args = parser.parse_args(argv)

    doc_path = args.doc or default_doc_path()
    table = load_table(args.table)
    current = doc_path.read_text(encoding="utf-8")
    updated = splice(current, render(table))

    if args.check:
        if current == updated:
            return 0
        diff = difflib.unified_diff(
            current.splitlines(keepends=True),
            updated.splitlines(keepends=True),
            fromfile=f"{doc_path} (on disk)",
            tofile=f"{doc_path} (regenerated)",
        )
        sys.stdout.writelines(diff)
        print(
            f"\n{doc_path} is stale. Run: uv run python scripts/generate_reference.py",
            file=sys.stderr,
        )
        return 1

    if current != updated:
        doc_path.write_text(updated, encoding="utf-8")
        print(f"Updated {doc_path}")
    else:
        print(f"{doc_path} already current")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
