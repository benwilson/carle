"""Loader and validator for ``protocol/commands.yaml``.

The YAML file is the single owner of every command entry and its verification state.
The published reference is generated from it, and the invariant suite validates it.
Both go through this module so there is exactly one definition of the schema.
"""

from __future__ import annotations

import datetime as _datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from carle import frame

STATUSES = ("unmapped", "unlocated", "decoded", "confirmed")
PROVENANCES = ("vendor-marketing", "decompile")
CATEGORIES = ("movement", "song", "dance", "gymnastic", "story", "voice", "system")

#: Capability counts Ruko publishes, treated as a FLOOR rather than an equality.
#: A floor is what the supersede workflow needs: when the decompile shows ten songs are
#: one parameterized opcode, the replacement row is added alongside the ten retained ids
#: and the category grows. Deletion is caught by the seeded-id retention rule instead.
#: `movement` is included even though Ruko publishes no movement count — without it that
#: category would have no floor at all.
PUBLISHED_COUNTS = {
    "movement": 6,
    "song": 10,
    "dance": 8,
    "gymnastic": 2,
    "story": 4,
    "voice": 14,
    # Ruko publishes no count for device-level commands like volume; the floor is zero
    # so the category is still declared and cannot be added without a deliberate edit.
    "system": 0,
}

#: Hardware observation logs live here and nowhere else. A `confirmed` row's evidence
#: must resolve to a real file inside this directory — see `_validate_log_path`.
EVIDENCE_DIR = "evidence"

_REQUIRED = ("id", "capability", "category", "provenance", "status")
_OPTIONAL = (
    "family",
    "payload",
    "parameters",
    "derivation",
    "observed_behavior",
    "observed_parameters",
    "hardware_evidence",
    "superseded_by",
)
_EVIDENCE_FIELDS = ("date", "platform", "log")
_PARAMETER_FIELDS = ("min", "max", "default")


class TableError(Exception):
    """Raised when ``commands.yaml`` cannot be parsed into the expected shape."""


@dataclass(frozen=True)
class Entry:
    id: str
    capability: str
    category: str
    provenance: str
    status: str
    family: int | None = None
    payload: list[Any] | None = None
    parameters: dict[str, dict[str, Any]] | None = None
    derivation: str | None = None
    observed_behavior: str | None = None
    observed_parameters: dict[str, int] | None = None
    hardware_evidence: dict[str, Any] | None = None
    superseded_by: list[str] | None = None

    @property
    def has_frame(self) -> bool:
        """Whether this entry carries protocol bytes at all.

        Explicitly `is not None` on the family and a length check on the payload,
        never truthiness: `family: 0x00` and `payload: []` are both falsy and both
        legal, so a truthiness test would let an unearned row carry `family: 0x00`
        straight past the state rules.
        """
        return self.family is not None and bool(self.payload)

    def resolved_payload(self, overrides: dict[str, int] | None = None) -> bytes:
        return frame.resolve(self.payload or [], self.parameters, overrides)

    def build_frame(self, overrides: dict[str, int] | None = None) -> bytes:
        if not self.has_frame:
            raise TableError(f"{self.id}: has no frame to build")
        return frame.build(self.family, self.resolved_payload(overrides))


@dataclass(frozen=True)
class Table:
    coverage_note: str
    entries: list[Entry] = field(default_factory=list)

    def by_category(self, category: str) -> list[Entry]:
        return [e for e in self.entries if e.category == category]


def repo_root() -> Path:
    """Repository root, resolved from this file's location."""
    return Path(__file__).resolve().parents[2]


def default_table_path() -> Path:
    return repo_root() / "protocol" / "commands.yaml"


def load_table(path: Path | str | None = None) -> Table:
    """Parse ``commands.yaml`` into a :class:`Table`.

    Raises ``TableError`` on anything that is not structurally the expected shape.
    Semantic rules (state transitions, evidence resolvability) are checked by
    :func:`validate_table` so callers can report every violation at once.
    """
    path = Path(path) if path is not None else default_table_path()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise TableError(f"{path} does not exist") from exc
    except yaml.YAMLError as exc:
        raise TableError(f"{path} is not valid YAML: {exc}") from exc

    if not isinstance(raw, dict):
        raise TableError(f"{path} must contain a mapping at the top level")

    coverage_note = raw.get("coverage_note")
    if not isinstance(coverage_note, str) or not coverage_note.strip():
        raise TableError(f"{path} must carry a non-empty 'coverage_note'")

    rows = raw.get("commands")
    if not isinstance(rows, list):
        raise TableError(f"{path} must carry a 'commands' list")

    entries = [_build_entry(row, index, path) for index, row in enumerate(rows)]
    return Table(coverage_note=coverage_note, entries=entries)


def _build_entry(row: Any, index: int, path: Path) -> Entry:
    where = f"{path} entry {index}"
    if not isinstance(row, dict):
        raise TableError(f"{where} must be a mapping")

    unknown = set(row) - set(_REQUIRED) - set(_OPTIONAL)
    if unknown:
        raise TableError(f"{where} has unknown field(s): {', '.join(sorted(unknown))}")

    missing = [key for key in _REQUIRED if key not in row]
    if missing:
        raise TableError(f"{where} is missing required field(s): {', '.join(missing)}")

    if row["status"] not in STATUSES:
        raise TableError(f"{where} has status {row['status']!r}; expected one of {STATUSES}")
    if row["provenance"] not in PROVENANCES:
        raise TableError(
            f"{where} has provenance {row['provenance']!r}; expected one of {PROVENANCES}"
        )
    if row["category"] not in CATEGORIES:
        raise TableError(f"{where} has category {row['category']!r}; expected one of {CATEGORIES}")

    evidence = row.get("hardware_evidence")
    if evidence is not None and not isinstance(evidence, dict):
        raise TableError(f"{where} hardware_evidence must be a mapping")

    superseded_by = row.get("superseded_by")
    if superseded_by is not None and not isinstance(superseded_by, list):
        raise TableError(f"{where} superseded_by must be a list of ids")

    payload = row.get("payload")
    if payload is not None and not isinstance(payload, list):
        raise TableError(f"{where} payload must be a list of bytes and {{name}} references")

    family = row.get("family")
    if family is not None:
        try:
            family = frame.byte_literal(family)
        except frame.FrameError as exc:
            raise TableError(f"{where} family: {exc}") from exc

    parameters = row.get("parameters")
    if parameters is not None:
        if not isinstance(parameters, dict):
            raise TableError(f"{where} parameters must be a mapping of name to declaration")
        for name, spec in parameters.items():
            if not isinstance(spec, dict):
                raise TableError(f"{where} parameter {name!r} must be a mapping")
            missing_fields = [key for key in _PARAMETER_FIELDS if key not in spec]
            if missing_fields:
                raise TableError(
                    f"{where} parameter {name!r} is missing {', '.join(missing_fields)}"
                )

    observed_parameters = row.get("observed_parameters")
    if observed_parameters is not None and not isinstance(observed_parameters, dict):
        raise TableError(f"{where} observed_parameters must be a mapping")

    return Entry(
        id=row["id"],
        capability=row["capability"],
        category=row["category"],
        provenance=row["provenance"],
        status=row["status"],
        family=family,
        payload=payload,
        parameters=parameters,
        derivation=row.get("derivation"),
        observed_behavior=row.get("observed_behavior"),
        observed_parameters=observed_parameters,
        hardware_evidence=evidence,
        superseded_by=superseded_by,
    )


def validate_entry(entry: Entry, *, root: Path | None = None) -> list[str]:
    """Return every rule violation for one entry. Empty list means the entry is valid."""
    root = root or repo_root()
    problems: list[str] = []
    unearned = entry.status in ("unmapped", "unlocated")

    # State rules. An entry may never claim more verification than its status earns.
    # `family` is checked with `is not None` rather than truthiness because 0x00 is a
    # legal byte; everything else is checked for emptiness, since an empty string or
    # list is an absent value dressed up as a present one.
    if unearned:
        if entry.family is not None:
            problems.append(
                f"{entry.id}: [state.unearned] status '{entry.status}' must not carry family"
            )
        for name in (
            "payload",
            "derivation",
            "observed_behavior",
            "observed_parameters",
            "hardware_evidence",
        ):
            if getattr(entry, name):
                problems.append(
                    f"{entry.id}: [state.unearned] status '{entry.status}' must not carry {name}"
                )
    elif entry.status == "decoded":
        if entry.family is None:
            problems.append(f"{entry.id}: [state.decoded-missing] status 'decoded' requires family")
        for name in ("payload", "derivation"):
            if not getattr(entry, name):
                problems.append(
                    f"{entry.id}: [state.decoded-missing] status 'decoded' requires {name}"
                )
        if entry.hardware_evidence:
            problems.append(
                f"{entry.id}: [state.decoded-evidence] status 'decoded' must not carry "
                "hardware_evidence (hardware evidence means the entry is 'confirmed')"
            )
    elif entry.status == "confirmed":
        if entry.family is None:
            problems.append(
                f"{entry.id}: [state.confirmed-missing] status 'confirmed' requires family"
            )
        for name in ("payload", "derivation", "observed_behavior", "hardware_evidence"):
            if not getattr(entry, name):
                problems.append(
                    f"{entry.id}: [state.confirmed-missing] status 'confirmed' requires {name}"
                )
        # `observed_parameters` is checked for presence, not emptiness: a command with
        # no parameters legitimately records `{}`, and that is a real observation.
        if entry.observed_parameters is None:
            problems.append(
                f"{entry.id}: [state.confirmed-missing] status 'confirmed' requires "
                "observed_parameters"
            )

    # Provenance rule. A row seeded from marketing copy describes a capability,
    # never a protocol command, so it can never carry protocol bytes.
    if entry.provenance == "vendor-marketing" and entry.has_frame:
        problems.append(
            f"{entry.id}: [provenance.marketing-encoding] provenance 'vendor-marketing' must "
            "not carry protocol bytes; a frame requires provenance 'decompile'"
        )

    problems.extend(_validate_frame(entry))
    problems.extend(_validate_evidence(entry, root))
    return problems


def _validate_frame(entry: Entry) -> list[str]:
    """The frame must be buildable, and its parameters must line up with its payload."""
    problems: list[str] = []
    if entry.family is None and not entry.payload:
        return problems

    if entry.family is not None and entry.family not in frame.FAMILIES:
        known = ", ".join(f"0x{f:02X}" for f in sorted(frame.FAMILIES))
        problems.append(
            f"{entry.id}: [frame.family] family 0x{entry.family:02X} is not one of the "
            f"documented families ({known})"
        )

    declared = set(entry.parameters or {})
    referenced = frame.referenced_parameters(entry.payload or [])

    for name in sorted(referenced - declared):
        problems.append(
            f"{entry.id}: [frame.undeclared-parameter] payload references {{{name}}} "
            "but no parameter declares it"
        )
    for name in sorted(declared - referenced):
        problems.append(
            f"{entry.id}: [frame.dead-parameter] parameter {name!r} is declared but the "
            "payload never references it"
        )

    if not problems and entry.has_frame:
        try:
            entry.build_frame()
        except (frame.FrameError, TableError) as exc:
            problems.append(f"{entry.id}: [frame.unbuildable] {exc}")

    return problems


def _validate_evidence(entry: Entry, root: Path) -> list[str]:
    """Hardware evidence must resolve, not merely be present.

    A presence check would let ``hardware_evidence: {log: anything}`` pass the gate,
    which is the failure mode the whole rule exists to prevent.
    """
    evidence = entry.hardware_evidence
    if evidence is None:
        return []

    problems: list[str] = []
    missing = [key for key in _EVIDENCE_FIELDS if not evidence.get(key)]
    if missing:
        problems.append(f"{entry.id}: hardware_evidence is missing {', '.join(missing)}")

    raw_date = evidence.get("date")
    if raw_date is not None:
        observed: _datetime.date | None = None
        if isinstance(raw_date, _datetime.date):
            observed = raw_date
        else:
            try:
                observed = _datetime.date.fromisoformat(str(raw_date))
            except ValueError:
                problems.append(
                    f"{entry.id}: [evidence.date] hardware_evidence.date {raw_date!r} "
                    "is not an ISO 8601 date"
                )
        # A future date cannot describe an observation that already happened.
        if observed is not None and observed > _datetime.date.today():
            problems.append(
                f"{entry.id}: [evidence.date] hardware_evidence.date {raw_date} is in the "
                "future; it must record when the robot was actually observed"
            )

    log = evidence.get("log")
    if log:
        path_problems = _validate_log_path(entry.id, str(log), root)
        problems.extend(path_problems)
        if not path_problems:
            problems.extend(_validate_log_contents(entry, root / str(log)))

    return problems


def _validate_log_contents(entry: Entry, path: Path) -> list[str]:
    """Open the cited log and check it actually supports the claim.

    This is the rule that makes the evidence mechanism real. Without it the gate
    checks only that *some* non-empty file sits in ``evidence/``, so anyone can
    hand-edit an entry to `confirmed`, point at a dry-run log, and watch every CI
    step pass — the CLI's rules would be enforced only by running the CLI.
    """
    from carle import evidence as evidence_log  # local import: evidence imports frame, not table

    prefix = f"{entry.id}: [evidence.log-shape]"
    try:
        log = evidence_log.read_log(path)
    except evidence_log.EvidenceError as exc:
        return [f"{prefix} {exc}"]

    problems: list[str] = []
    if log.kind != evidence_log.KIND_SEND:
        problems.append(f"{prefix} log kind is {log.kind!r}; only a real send is evidence")
    if log.entry_id != entry.id:
        problems.append(f"{prefix} log names entry {log.entry_id!r}, not this one")
    if not log.write_ok:
        problems.append(f"{prefix} log records a failed write")

    if problems or not entry.has_frame:
        return problems

    try:
        expected = entry.build_frame(entry.observed_parameters or {})
    except (frame.FrameError, TableError) as exc:
        return [f"{prefix} entry cannot be rebuilt at its observed parameters: {exc}"]

    if log.frame != expected:
        problems.append(
            f"{prefix} log records {frame.to_hex(log.frame)} but the entry rebuilds to "
            f"{frame.to_hex(expected)}; the observation described a different frame"
        )
    if log.parameters != (entry.observed_parameters or {}):
        problems.append(
            f"{prefix} log was sent with {log.parameters or 'no parameters'} but the entry "
            f"records {entry.observed_parameters or 'none'}"
        )
    return problems


def _validate_log_path(entry_id: str, log: str, root: Path) -> list[str]:
    """A confirmed row's evidence must point at a real log file inside ``evidence/``.

    A bare existence check is not enough, and the gap is not theoretical: ``root / log``
    silently discards ``root`` when ``log`` is absolute, and any path that happens to
    exist satisfies it. ``log: LICENSE``, ``log: .``, and ``log: /etc/hosts`` all earned
    ``status: confirmed`` under the previous check — the single strongest claim this
    repository makes, available to anyone who never touched a robot.
    """
    prefix = f"{entry_id}: [evidence.log]"
    path = Path(log)

    if path.is_absolute():
        return [
            f"{prefix} must be a repo-relative path under {EVIDENCE_DIR}/, "
            f"got the absolute path {log!r}"
        ]

    resolved = (root / path).resolve()
    evidence_root = (root / EVIDENCE_DIR).resolve()

    if not resolved.is_relative_to(evidence_root):
        return [f"{prefix} {log!r} resolves outside {EVIDENCE_DIR}/"]
    if not resolved.exists():
        return [f"{prefix} {log!r} does not exist on disk"]
    if not resolved.is_file():
        return [f"{prefix} {log!r} is not a file"]
    if resolved.name == "README.md":
        return [f"{prefix} {log!r} is the directory's own README, not an observation log"]
    if resolved.stat().st_size == 0:
        return [f"{prefix} {log!r} is empty; record what the robot actually did"]

    return []


def validate_table(
    table: Table,
    *,
    root: Path | None = None,
    seeded_ids: list[str] | None = None,
) -> list[str]:
    """Return every rule violation across the whole table."""
    root = root or repo_root()
    problems: list[str] = []

    for entry in table.entries:
        problems.extend(validate_entry(entry, root=root))

    seen: dict[str, int] = {}
    for entry in table.entries:
        seen[entry.id] = seen.get(entry.id, 0) + 1
    for entry_id, count in sorted(seen.items()):
        if count > 1:
            problems.append(
                f"{entry_id}: [table.duplicate-id] id appears {count} times; ids must be unique"
            )

    present = {entry.id for entry in table.entries}

    if seeded_ids is not None:
        for entry_id in seeded_ids:
            if entry_id not in present:
                problems.append(
                    f"{entry_id}: [table.seeded-missing] seeded id is absent from the table. "
                    "Mark it 'unlocated' or keep it with superseded_by; do not delete it"
                )

    # A supersede has to be traceable, or it is just a deletion with better manners.
    for entry in table.entries:
        for target in entry.superseded_by or []:
            if target not in present:
                problems.append(
                    f"{entry.id}: [table.superseded-dangling] superseded_by names {target!r}, "
                    "which is not a row in the table"
                )

    for category, expected in PUBLISHED_COUNTS.items():
        actual = len(table.by_category(category))
        # A floor, not an equality: the decompile is expected to ADD rows (a replacement
        # command alongside the superseded originals). Only shrinkage is a defect.
        if actual < expected:
            problems.append(
                f"category '{category}': [table.count-floor] has {actual} rows, "
                f"below the {expected} Ruko publishes"
            )

    return problems


#: Everything above this key is prose a round-trip would destroy — the comment header
#: and `coverage_note`, which is a `|` literal block PyYAML re-emits as a quoted folded
#: scalar. Splitting here and re-emitting the top verbatim keeps a promotion to a
#: one-entry diff instead of a whole-file reflow.
_SPLIT_KEY = "\ncommands:"


def save_table(rows: list[dict[str, Any]], path: Path | str | None = None) -> None:
    """Write the command rows back, preserving everything above ``commands:``.

    Replaces atomically: a partial write would corrupt the file the entire published
    reference is generated from.
    """
    path = Path(path) if path is not None else default_table_path()
    current = path.read_text(encoding="utf-8")
    head, sep, _ = current.partition(_SPLIT_KEY)
    if not sep:
        raise TableError(f"{path} has no top-level 'commands:' key to split on")

    body = yaml.safe_dump({"commands": rows}, sort_keys=False, allow_unicode=True, width=96)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(head + "\n" + body, encoding="utf-8")
    temp.replace(path)


def load_rows(path: Path | str | None = None) -> list[dict[str, Any]]:
    """The raw row mappings, for callers that need to write them back unchanged."""
    path = Path(path) if path is not None else default_table_path()
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return raw["commands"]


def load_seeded_ids(path: Path | str | None = None) -> list[str]:
    """Read the checked-in snapshot of ids created when the table was first seeded."""
    path = Path(path) if path is not None else repo_root() / "tests" / "fixtures" / "seeded_ids.txt"
    lines = path.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]
