"""Send logs — the artifact that stands between a claim and a `confirmed` entry.

A log is written by ``carle send`` when it actually transmits to a peripheral. It is
plain text, one ``key: value`` per line, so a reviewer can read it in a diff without
tooling and the invariant suite can parse it without guessing.

Two things deliberately do **not** write here:

- **Dry runs.** They print and exit. A dry run is hardware-free by definition, so a
  dry-run log sitting in ``evidence/`` would be a promotion waiting to happen, held
  back only by one editable line of text.
- **Raw sends.** They go to a scratch directory outside ``evidence/`` entirely.

Reading and writing both live here so the CLI and the invariant suite cannot drift
on the format — the gate parses the same file the tool wrote, through the same code.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from pathlib import Path

from carle import frame

#: Colon-free because the CI matrix includes Windows, and microsecond-resolution
#: because two sends inside one second is exactly what a test loop does.
TIMESTAMP_FORMAT = "%Y%m%dT%H%M%S%fZ"

LOG_SUFFIX = ".log"
SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9_]*$")

KIND_SEND = "send"
KIND_RAW = "raw"
KINDS = (KIND_SEND, KIND_RAW)


class EvidenceError(Exception):
    """Raised when a log cannot be written, or cannot be read as a log."""


@dataclass(frozen=True)
class SendLog:
    kind: str
    frame: bytes
    timestamp: dt.datetime
    platform: str
    peripheral: str
    write_ok: bool
    entry_id: str | None = None
    parameters: dict[str, int] = field(default_factory=dict)
    write_detail: str = ""
    notifications: list[bytes] = field(default_factory=list)

    @property
    def promotable(self) -> bool:
        """Whether this log may support a promotion to `confirmed`.

        A raw send bypassed the table, so nothing ties its bytes to an entry.
        """
        return self.kind == KIND_SEND and bool(self.entry_id) and self.write_ok

    @property
    def date(self) -> dt.date:
        return self.timestamp.date()

    def filename(self) -> str:
        stem = self.entry_id if self.entry_id else "raw"
        return f"{stem}-{self.timestamp.strftime(TIMESTAMP_FORMAT)}{LOG_SUFFIX}"


def _check_id(entry_id: str | None) -> None:
    if entry_id is None:
        return
    if not SAFE_ID.match(entry_id):
        raise EvidenceError(
            f"entry id {entry_id!r} is not filename-safe; ids are lowercase "
            "alphanumerics and underscores"
        )


def render(log: SendLog) -> str:
    params = " ".join(f"{k}={v}" for k, v in sorted(log.parameters.items()))
    notifications = " | ".join(frame.to_hex(n) for n in log.notifications)
    lines = [
        f"kind: {log.kind}",
        f"entry: {log.entry_id or ''}",
        f"frame: {frame.to_hex(log.frame)}",
        f"parameters: {params}",
        f"timestamp: {log.timestamp.strftime('%Y-%m-%dT%H:%M:%S.%f')}Z",
        f"platform: {log.platform}",
        f"peripheral: {log.peripheral}",
        f"write: {'ok' if log.write_ok else 'failed'}",
        f"write_detail: {log.write_detail}",
        f"notifications: {notifications}",
    ]
    return "\n".join(lines) + "\n"


def write_log(log: SendLog, directory: Path | str) -> Path:
    """Write a log, refusing to overwrite an existing one."""
    if log.kind not in KINDS:
        raise EvidenceError(f"kind {log.kind!r} is not one of {KINDS}")
    _check_id(log.entry_id)

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / log.filename()
    if path.exists():
        raise EvidenceError(f"{path} already exists; refusing to overwrite an observation")
    path.write_text(render(log), encoding="utf-8")
    return path


def read_log(path: Path | str) -> SendLog:
    """Parse a log, rejecting anything that is not one rather than half-reading it."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise EvidenceError(f"{path} cannot be read: {exc}") from exc

    fields: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        key, sep, value = line.partition(":")
        if not sep:
            raise EvidenceError(f"{path} line {line!r} is not 'key: value'")
        fields[key.strip()] = value.strip()

    required = ("kind", "frame", "timestamp", "platform", "write")
    missing = [key for key in required if key not in fields]
    if missing:
        raise EvidenceError(f"{path} is missing {', '.join(missing)}")

    kind = fields["kind"]
    if kind not in KINDS:
        raise EvidenceError(f"{path} has kind {kind!r}, expected one of {KINDS}")

    entry_id = fields.get("entry") or None
    _check_id(entry_id)

    try:
        timestamp = dt.datetime.strptime(fields["timestamp"], "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError as exc:
        raise EvidenceError(f"{path} timestamp {fields['timestamp']!r} is not ISO 8601") from exc

    parameters: dict[str, int] = {}
    for pair in fields.get("parameters", "").split():
        name, sep, value = pair.partition("=")
        if not sep or not value.lstrip("-").isdigit():
            raise EvidenceError(f"{path} parameter {pair!r} is not 'name=value'")
        parameters[name] = int(value)

    # from_hex raises FrameError, which callers do not catch — one bad byte would take
    # down the whole invariant run instead of reporting a rule violation.
    try:
        parsed_frame = frame.from_hex(fields["frame"])
        notifications = [
            frame.from_hex(chunk)
            for chunk in fields.get("notifications", "").split("|")
            if chunk.strip()
        ]
    except frame.FrameError as exc:
        raise EvidenceError(f"{path} contains an unreadable byte sequence: {exc}") from exc

    return SendLog(
        kind=kind,
        frame=parsed_frame,
        timestamp=timestamp,
        platform=fields["platform"],
        peripheral=fields.get("peripheral", ""),
        write_ok=fields["write"] == "ok",
        entry_id=entry_id,
        parameters=parameters,
        write_detail=fields.get("write_detail", ""),
        notifications=notifications,
    )


def logs_for(entry_id: str, directory: Path | str) -> list[Path]:
    """Every log for an entry, oldest first. Filenames sort chronologically by design."""
    directory = Path(directory)
    if not directory.is_dir():
        return []
    return sorted(directory.glob(f"{entry_id}-*{LOG_SUFFIX}"))


def promotable_logs(entry_id: str, directory: Path | str) -> list[tuple[Path, SendLog]]:
    """Every log that could support a promotion, newest first."""
    found: list[tuple[Path, SendLog]] = []
    for path in reversed(logs_for(entry_id, directory)):
        try:
            log = read_log(path)
        except EvidenceError:
            continue
        if log.promotable and log.entry_id == entry_id:
            found.append((path, log))
    return found


def latest_promotable(entry_id: str, directory: Path | str) -> SendLog | None:
    """The most recent log that may support a promotion, or None."""
    found = promotable_logs(entry_id, directory)
    return found[0][1] if found else None


def find_log_path(log: SendLog, directory: Path | str) -> Path:
    return Path(directory) / log.filename()
