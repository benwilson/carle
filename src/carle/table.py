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
    "observations",
    "superseded_by",
)
_EVIDENCE_FIELDS = ("date", "platform", "logs")
_PARAMETER_FIELDS = ("min", "max", "default")
_OBSERVATION_FIELDS = ("parameters", "behavior", "evidence", "withdrawn")


class TableError(Exception):
    """Raised when ``commands.yaml`` cannot be parsed into the expected shape."""


@dataclass(frozen=True)
class Observation:
    """One watched behaviour of one command at one point in its parameter space.

    An entry carries a list of these rather than a single behaviour, because a
    parameterized command like ``move_rocker`` is one frame spanning a whole space:
    walking forward and raising an arm are the same command at different bytes.

    ``logs`` is a list, not a single path. Most of these findings were read from a
    *sequence* of sends — alternating two limb values on a loop, or holding one value
    for a minute — and citing one arbitrary member would make that log appear to back
    a behaviour it alone did not produce. Every cited log must have been sent at this
    observation's parameters, so a multi-log observation is a repeated send, never a
    swept one.
    """

    parameters: dict[str, int]
    behavior: str
    date: Any
    platform: str
    logs: list[str]
    withdrawn: str | None = None

    @property
    def live(self) -> bool:
        """Whether this observation still supports the entry's status.

        Withdrawal changes exactly this one thing. It never exempts the observation
        from log validation, or ``withdrawn`` becomes the flag that walks anything
        past the gate.
        """
        return self.withdrawn is None


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
    observations: list[Observation] = field(default_factory=list)
    superseded_by: list[str] | None = None

    @property
    def live_observations(self) -> list[Observation]:
        return [o for o in self.observations if o.live]

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

    observations = _build_observations(row.get("observations"), where)

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
        observations=observations,
        superseded_by=superseded_by,
    )


def _build_observations(raw: Any, where: str) -> list[Observation]:
    """Parse the ``observations`` list into :class:`Observation` objects.

    Structural rejection only — shape, types, unknown keys. Whether an observation is
    *earned* (its logs resolve, its frame matches, its status agrees) is decided by
    :func:`validate_entry`, which reports every violation at once with a rule code.
    """
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise TableError(f"{where} observations must be a list")

    built: list[Observation] = []
    for index, item in enumerate(raw):
        at = f"{where} observation {index}"
        if not isinstance(item, dict):
            raise TableError(f"{at} must be a mapping")

        unknown = set(item) - set(_OBSERVATION_FIELDS)
        if unknown:
            raise TableError(f"{at} has unknown field(s): {', '.join(sorted(unknown))}")

        parameters = item.get("parameters") or {}
        if not isinstance(parameters, dict):
            raise TableError(f"{at} parameters must be a mapping")

        evidence = item.get("evidence")
        if not isinstance(evidence, dict):
            raise TableError(f"{at} must carry an 'evidence' mapping")

        logs = evidence.get("logs")
        if logs is not None and not isinstance(logs, list):
            raise TableError(f"{at} evidence.logs must be a list of paths")

        withdrawn = item.get("withdrawn")
        if withdrawn is not None and not isinstance(withdrawn, str):
            raise TableError(f"{at} withdrawn must be the reason for the retraction")

        built.append(
            Observation(
                parameters=parameters,
                behavior=item.get("behavior") or "",
                date=evidence.get("date"),
                platform=evidence.get("platform") or "",
                logs=[str(log) for log in (logs or [])],
                withdrawn=withdrawn,
            )
        )
    return built


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
        for name in ("payload", "derivation"):
            if getattr(entry, name):
                problems.append(
                    f"{entry.id}: [state.unearned] status '{entry.status}' must not carry {name}"
                )
        # Not even a withdrawn one. `unmapped` means nobody has looked for the frame;
        # there is nothing to have watched, retracted or otherwise.
        if entry.observations:
            problems.append(
                f"{entry.id}: [state.unearned] status '{entry.status}' must not carry observations"
            )
    elif entry.status == "decoded":
        if entry.family is None:
            problems.append(f"{entry.id}: [state.decoded-missing] status 'decoded' requires family")
        for name in ("payload", "derivation"):
            if not getattr(entry, name):
                problems.append(
                    f"{entry.id}: [state.decoded-missing] status 'decoded' requires {name}"
                )
    elif entry.status == "confirmed":
        if entry.family is None:
            problems.append(
                f"{entry.id}: [state.confirmed-missing] status 'confirmed' requires family"
            )
        for name in ("payload", "derivation"):
            value = getattr(entry, name)
            if isinstance(value, str):
                value = value.strip()
            if not value:
                problems.append(
                    f"{entry.id}: [state.confirmed-missing] status 'confirmed' requires {name}"
                )

    # KTD3, in one direction: `status` is 'confirmed' if and only if at least one
    # observation is not withdrawn. A `decoded` entry may keep observations provided
    # every one of them is withdrawn — otherwise a finding that was published and
    # then fully retracted would have to be DELETED to satisfy the gate, destroying
    # exactly the record that keeping retractions visible exists to preserve.
    if entry.status == "confirmed" and not entry.live_observations:
        problems.append(
            f"{entry.id}: [state.confirmed-missing] status 'confirmed' requires at least one "
            "observation that is not withdrawn"
        )
    if entry.status != "confirmed" and entry.live_observations:
        problems.append(
            f"{entry.id}: [state.status-mismatch] status '{entry.status}' carries a live "
            "observation; an observation that has not been withdrawn means 'confirmed'"
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
    """Validate every observation independently, and the list as a whole.

    Every rule below is applied per observation rather than once per entry. That is
    the whole point of the list: an entry with twenty-five observations makes
    twenty-five separate claims, and a gate that checked only the first would let the
    other twenty-four say anything at all.
    """
    problems: list[str] = []
    seen_logs: dict[object, int] = {}

    for index, observation in enumerate(entry.observations):
        problems.extend(_validate_observation(entry, observation, index, root))
        # KTD9. The published reference shows an observation count, which a reader
        # takes as a measure of how widely the command was exercised. Two
        # observations over one send would read as two independent confirmations.
        for log in observation.logs:
            # Key off the resolved path, not the raw string, so two path-equivalent
            # spellings of one file ("evidence/x.log" vs "evidence/./x.log") cannot pass as
            # two independent confirmations of a single send.
            key = _log_identity(log, root)
            if key in seen_logs:
                problems.append(
                    f"{entry.id}[{index}]: [observation.duplicate-log] cites {log!r}, which "
                    f"observation {seen_logs[key]} already cites; one send is one observation"
                )
            else:
                seen_logs[key] = index

    return problems


def _log_identity(log: str, root: Path) -> object:
    """A canonical key for one evidence log, so path-equivalent spellings collide.

    Resolves the same way `_validate_log_path` does. Falls back to the raw string if the
    value cannot be turned into a path (already reported as a shape violation elsewhere).
    """
    try:
        return (root / Path(log)).resolve()
    except (TypeError, ValueError, OSError):
        return log


def _validate_observation(
    entry: Entry, observation: Observation, index: int, root: Path
) -> list[str]:
    """Every rule for one observation. ``withdrawn`` exempts it from none of them."""
    where = f"{entry.id}[{index}]"
    problems: list[str] = []

    if not observation.behavior.strip():
        problems.append(
            f"{where}: [observation.shape] behavior is blank; record what the robot did"
        )
    if observation.withdrawn is not None and not observation.withdrawn.strip():
        problems.append(
            f"{where}: [observation.shape] withdrawn must give the reason for the retraction"
        )
    missing = [name for name in _EVIDENCE_FIELDS if not getattr(observation, name)]
    if missing:
        problems.append(f"{where}: [observation.shape] evidence is missing {', '.join(missing)}")

    problems.extend(_validate_observation_date(where, observation))

    for log_index, log in enumerate(observation.logs):
        at = where if len(observation.logs) == 1 else f"{where} log {log_index}"
        path_problems = _validate_log_path(at, log, root)
        problems.extend(path_problems)
        if not path_problems:
            problems.extend(_validate_log_contents(entry, observation, at, root / log))

    return problems


def _validate_observation_date(where: str, observation: Observation) -> list[str]:
    raw_date = observation.date
    if raw_date is None:
        return []

    problems: list[str] = []
    observed: _datetime.date | None = None
    if isinstance(raw_date, _datetime.date):
        observed = raw_date
    else:
        try:
            observed = _datetime.date.fromisoformat(str(raw_date))
        except ValueError:
            problems.append(
                f"{where}: [evidence.date] evidence.date {raw_date!r} is not an ISO 8601 date"
            )
    # A future date cannot describe an observation that already happened.
    # Compare against the same clock the CLI stamps with. `date.today()` is local,
    # so an evening promotion west of UTC wrote a 'future' date and failed CI.
    if observed is not None and observed > _datetime.datetime.now(_datetime.timezone.utc).date():
        problems.append(
            f"{where}: [evidence.date] evidence.date {raw_date} is in the future; it must "
            "record when the robot was actually observed"
        )
    return problems


def _validate_log_contents(
    entry: Entry, observation: Observation, where: str, path: Path
) -> list[str]:
    """Open the cited log and check it actually supports this observation's claim.

    This is the rule that makes the evidence mechanism real. Without it the gate
    checks only that *some* non-empty file sits in ``evidence/``, so anyone can
    hand-edit an entry to `confirmed`, point at a dry-run log, and watch every CI
    step pass — the CLI's rules would be enforced only by running the CLI.
    """
    from carle import evidence as evidence_log  # local import: evidence imports frame, not table

    prefix = f"{where}: [evidence.log-shape]"
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
        expected = entry.build_frame(observation.parameters or {})
    except (frame.FrameError, TableError) as exc:
        return [f"{prefix} entry cannot be rebuilt at this observation's parameters: {exc}"]

    if log.frame != expected:
        problems.append(
            f"{prefix} log records {frame.to_hex(log.frame)} but the entry rebuilds to "
            f"{frame.to_hex(expected)}; the observation described a different frame"
        )
    recorded_date = observation.date or ""
    if str(recorded_date) != log.date.isoformat():
        problems.append(
            f"{prefix} observation is dated {recorded_date}, but the log was "
            f"written {log.date.isoformat()}"
        )
    if observation.platform != log.platform:
        problems.append(
            f"{prefix} observation names platform {observation.platform!r}, but the log "
            f"records {log.platform!r}"
        )
    # Not implied by the frame check: two different parameter sets can resolve to the
    # same bytes, and a log recorded at `limb=1` must not be able to back a claim
    # about some other parameter that happens to build identically.
    if log.parameters != (observation.parameters or {}):
        problems.append(
            f"{prefix} log was sent with {log.parameters or 'no parameters'} but the "
            f"observation records {observation.parameters or 'none'}"
        )
    return problems


def _validate_log_path(where: str, log: str, root: Path) -> list[str]:
    """An observation's evidence must point at a real log file inside ``evidence/``.

    A bare existence check is not enough, and the gap is not theoretical: ``root / log``
    silently discards ``root`` when ``log`` is absolute, and any path that happens to
    exist satisfies it. ``log: LICENSE``, ``log: .``, and ``log: /etc/hosts`` all earned
    ``status: confirmed`` under the previous check — the single strongest claim this
    repository makes, available to anyone who never touched a robot.
    """
    prefix = f"{where}: [evidence.log]"
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
