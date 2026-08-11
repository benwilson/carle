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

from carle.frame import to_hex  # noqa: E402
from carle.table import STATUSES, Table, load_table, repo_root  # noqa: E402

BEGIN = "<!-- BEGIN GENERATED COMMAND TABLE -->"
END = "<!-- END GENERATED COMMAND TABLE -->"

#: Display order and headings. Must cover every value in table.CATEGORIES — a category
#: missing here is silently dropped from the published document while still counting
#: toward the total, which is how `volume_set` disappeared the first time.
CATEGORY_TITLES = {
    "movement": "Movement",
    "song": "Songs",
    "dance": "Dance tracks",
    "gymnastic": "Gymnastic routines",
    "story": "Stories",
    "voice": "Voice commands",
    "system": "Device commands",
}


def cell(value: object) -> str:
    """Escape a value for a markdown table cell.

    Unescaped pipes let a YAML string forge extra columns: a `capability` containing
    `| confirmed | \\`AA0102\\` |` renders as a confirmed row with an encoding while the
    entry itself is unmapped and passes every invariant.
    """
    text = str(value).replace("|", "\\|")
    return " ".join(text.split())


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
    # Ordered by STATUSES so a status added to the schema cannot be silently dropped
    # from the summary while still being counted in the total.
    summary = ", ".join(f"{counts[s]} {s}" for s in STATUSES if s in counts)
    lines.append(f"**{len(table.entries)} entries:** {summary}.")
    lines.append("")

    for category, title in CATEGORY_TITLES.items():
        entries = table.by_category(category)
        if not entries:
            continue
        lines.append(f"### {title}")
        lines.append("")
        lines.append("| ID | Capability | Status | Frame at defaults | Observations | Evidence |")
        lines.append("|---|---|---|---|---|---|")
        for entry in entries:
            # Always a concrete frame, built at declared defaults. After the migration
            # no row is fully literal, so rendering templates only for literal rows
            # would publish a schema where the reader expects bytes.
            #
            # At DEFAULTS, and the column says so. An entry now carries many
            # observations at many parameter sets, so there is no single frame this
            # row could show; picking one would publish it as *the* encoding. The
            # per-observation frames are in the observations section below.
            encoding = "—"
            if entry.has_frame:
                try:
                    encoding = f"`{cell(to_hex(entry.build_frame()))}`"
                except Exception:  # noqa: BLE001 - a broken row is reported by the gate
                    encoding = "_unbuildable_"

            live = len(entry.live_observations)
            withdrawn = len(entry.observations) - live
            if entry.observations:
                observed = f"{live} observed"
                if withdrawn:
                    observed += f", {withdrawn} withdrawn"
                observed += f" — [see below](#{entry.id})"
            else:
                observed = "—"

            evidence = "—"
            if entry.observations:
                logs = sum(len(o.logs) for o in entry.observations)
                evidence = f"{logs} send{'' if logs == 1 else 's'} logged"
            elif entry.derivation:
                evidence = f"derived: `{cell(entry.derivation)}`"

            capability = cell(entry.capability)
            if entry.superseded_by:
                targets = ", ".join(cell(t) for t in entry.superseded_by)
                capability += f" (superseded by {targets})"

            lines.append(
                f"| `{cell(entry.id)}` | {capability} | {entry.status} "
                f"| {encoding} | {observed} | {evidence} |"
            )
        lines.append("")

        parameterized = [e for e in entries if e.parameters]
        if parameterized:
            lines.append("Parameters. The frame above is shown at each parameter's default.")
            lines.append("")
            lines.append("| Command | Parameter | Range | Default |")
            lines.append("|---|---|---|---|")
            for entry in parameterized:
                for name, spec in entry.parameters.items():
                    lines.append(
                        f"| `{cell(entry.id)}` | `{cell(name)}` "
                        f"| {spec['min']}–{spec['max']} | {spec['default']} |"
                    )
            lines.append("")

    lines.extend(render_observations(table))
    return "\n".join(lines).rstrip() + "\n"


def log_links(logs: list[str]) -> str:
    """Link every cited log, collapsing a long run to its first and last.

    Log paths are repo-root-relative but this document lives in `docs/`, so a bare
    link would resolve to `docs/evidence/...` and 404.
    """
    links = [f"[{i + 1}](../{cell(log)})" for i, log in enumerate(logs)]
    if len(links) <= 3:
        return ", ".join(links)
    # The count is the claim; the endpoints let a reader find the run. Every log in
    # between is committed under the same entry id in evidence/, not hidden.
    return f"{len(logs)} sends, {links[0]}…{links[-1]}"


def render_observations(table: Table) -> list[str]:
    """One section per entry that has observations.

    Kept out of the main table deliberately: `move_rocker` alone carries two dozen,
    and inlining them would bury a fifty-row table. The main row shows the frame at
    declared defaults and a count; the real findings are here, each with the frame
    that was actually sent and the logs that recorded it.
    """
    observed = [entry for entry in table.entries if entry.observations]
    if not observed:
        return []

    lines = ["### Observations", ""]
    lines.append("What the robot did, per command and per parameter set. Each row is one")
    lines.append("behaviour watched on hardware; the frame shown is the one that was sent,")
    lines.append("not the default. A withdrawn row is a published reading that turned out to")
    lines.append("be wrong — kept, with its reason, so a reader can calibrate this document")
    lines.append("against its own error rate.")
    lines.append("")

    for entry in observed:
        lines.append(f"#### `{cell(entry.id)}`")
        lines.append("")
        lines.append("| Sent at | Frame | What the robot did | Logs |")
        lines.append("|---|---|---|---|")
        for observation in entry.observations:
            sent = (
                ", ".join(f"{k}={v}" for k, v in sorted(observation.parameters.items()))
                or "defaults"
            )
            try:
                built = f"`{cell(to_hex(entry.build_frame(observation.parameters)))}`"
            except Exception:  # noqa: BLE001 - a broken row is reported by the gate
                built = "_unbuildable_"
            behavior = cell(observation.behavior)
            if not observation.live:
                behavior = f"**WITHDRAWN.** {behavior} **Why:** {cell(observation.withdrawn)}"
            lines.append(f"| {cell(sent)} | {built} | {behavior} | {log_links(observation.logs)} |")
        lines.append("")

    return lines


def splice(document: str, body: str) -> str:
    """Replace the region between the markers, leaving hand-written prose intact."""
    if document.count(BEGIN) != 1 or document.count(END) != 1:
        # Only the first marker pair is ever regenerated, so a second one would sit in
        # the untouched tail and survive --check verbatim — a fabricated table wearing
        # the same "GENERATED" banner as the real one.
        raise SystemExit(
            f"The reference document must contain exactly one {BEGIN} and one {END}; "
            f"found {document.count(BEGIN)} and {document.count(END)}."
        )
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
