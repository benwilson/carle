"""Wire-frame construction and parsing for the Ruko 1088.

Every command the app sends shares one envelope::

    [0]        family      which command group this belongs to
    [1]        N           payload length
    [2 .. N+1] payload      first byte is the sub-command
    [N+2]      checksum    sum of the payload bytes, truncated to 8 bits
    [N+3]      terminator  always 0xAA

Only the family and the payload are stored in ``protocol/commands.yaml``. Length,
checksum and terminator are computed here, so a contributor cannot edit a payload,
forget the checksum, and publish a frame that could never work.

This module is deliberately schema-free: it deals in families, byte templates and
parameter mappings, never in table entries. ``carle.table`` owns the schema.
"""

from __future__ import annotations

import re
from typing import Any

TERMINATOR = 0xAA

#: The command families the decompiled app actually uses. Constraining this matters
#: because the checksum is now computed: without a closed set, an invented opcode
#: would render as a perfectly well-formed frame, indistinguishable from a real one.
FAMILIES: dict[int, str] = {
    0xB2: "programmed sequences",
    0xB3: "media and volume",
    0xB5: "gyro and tilt",
    0xB6: "movement and limbs",
}

#: A payload item is either a byte literal or a `{name}` parameter reference.
PARAMETER_REF = re.compile(r"^\{([a-z][a-z0-9_]*)\}$")

MAX_PAYLOAD = 255


class FrameError(Exception):
    """Raised when a frame cannot be built, parsed, or resolved."""


def checksum(payload: bytes | list[int]) -> int:
    return sum(payload) & 0xFF


def build(family: int, payload: bytes | list[int]) -> bytes:
    """Assemble the wire frame for a family and a resolved payload."""
    if family not in FAMILIES:
        known = ", ".join(f"0x{f:02X}" for f in sorted(FAMILIES))
        raise FrameError(f"family 0x{family:02X} is not one of the documented families ({known})")
    if len(payload) > MAX_PAYLOAD:
        raise FrameError(f"payload is {len(payload)} bytes; the length field holds at most 255")
    for index, byte in enumerate(payload):
        if not 0 <= byte <= 0xFF:
            raise FrameError(f"payload byte {index} is {byte}, outside 0-255")
    return bytes([family, len(payload), *payload, checksum(payload), TERMINATOR])


def parse(frame: bytes) -> tuple[int, bytes]:
    """Recover the family and payload from a wire frame, validating the envelope."""
    if len(frame) < 4:
        raise FrameError(f"frame is {len(frame)} bytes; the envelope alone is 4")

    family, declared = frame[0], frame[1]
    expected_total = declared + 4
    if len(frame) != expected_total:
        raise FrameError(
            f"frame declares a {declared}-byte payload, so it should be "
            f"{expected_total} bytes, but it is {len(frame)}"
        )

    payload = frame[2 : 2 + declared]
    if frame[2 + declared] != checksum(payload):
        raise FrameError(
            f"checksum is 0x{frame[2 + declared]:02X}; the payload sums to "
            f"0x{checksum(payload):02X}"
        )
    if frame[3 + declared] != TERMINATOR:
        raise FrameError(f"terminator is 0x{frame[3 + declared]:02X}, expected 0x{TERMINATOR:02X}")

    return family, bytes(payload)


def resolve(
    template: list[Any],
    parameters: dict[str, dict[str, Any]] | None = None,
    overrides: dict[str, int] | None = None,
) -> bytes:
    """Turn a payload template into concrete bytes.

    ``template`` items are byte literals or ``{name}`` references. ``parameters``
    declares each name's ``min``, ``max`` and ``default``. ``overrides`` supplies
    values for this particular send; anything absent falls back to its default.
    """
    parameters = parameters or {}
    overrides = overrides or {}

    unknown = set(overrides) - set(parameters)
    if unknown:
        raise FrameError(
            f"no such parameter: {', '.join(sorted(unknown))}. "
            f"This command accepts {', '.join(sorted(parameters)) or 'none'}"
        )

    out: list[int] = []
    for item in template:
        name = parameter_name(item)
        if name is None:
            out.append(byte_literal(item))
            continue
        spec = parameters[name]
        value = int(overrides.get(name, spec["default"]))
        low, high = int(spec["min"]), int(spec["max"])
        if not low <= value <= high:
            raise FrameError(f"{name} is {value}, outside its documented range {low}-{high}")
        if not 0 <= value <= 0xFF:
            # A declared range wider than a byte would otherwise surface as an uncaught
            # ValueError from bytes(), crashing the gate rather than reporting a rule.
            raise FrameError(f"{name} resolves to {value}, which is not a byte")
        out.append(value)
    return bytes(out)


def parameter_name(item: Any) -> str | None:
    """Return the parameter a template item references, or None if it is a literal."""
    if isinstance(item, int):
        return None
    match = PARAMETER_REF.match(str(item).strip())
    return match.group(1) if match else None


def byte_literal(item: Any) -> int:
    """Read a byte written as an int or as a string like ``0xB3``.

    The table stores bytes in hex because it is a protocol reference people read;
    `0xB3` is the form that appears in the decompiled source and in every capture.
    """
    if isinstance(item, bool):
        raise FrameError(f"{item!r} is not a byte")
    if isinstance(item, int):
        value = item
    else:
        text = str(item).strip()
        try:
            value = int(text, 16) if text.lower().startswith("0x") else int(text, 10)
        except ValueError as exc:
            raise FrameError(f"{item!r} is not a byte literal") from exc
    if not 0 <= value <= 0xFF:
        raise FrameError(f"{item!r} is outside 0-255")
    return value


def referenced_parameters(template: list[Any]) -> set[str]:
    return {name for item in template if (name := parameter_name(item)) is not None}


def to_hex(frame: bytes) -> str:
    return " ".join(f"{byte:02X}" for byte in frame)


def from_hex(text: str) -> bytes:
    cleaned = text.replace(":", " ").replace("-", " ").replace(",", " ").split()
    try:
        return bytes(int(part, 16) for part in cleaned)
    except ValueError as exc:
        raise FrameError(f"{text!r} is not a hex byte sequence") from exc


def render_template(family: int, template: list[Any]) -> str:
    """Render a template in the placeholder form the pre-migration table stored.

    Used once, by the migration check, to prove a parameterized row converted
    faithfully. A byte-for-byte comparison is impossible for those rows because the
    stored string contains ``<sum>`` rather than a computed checksum.
    """
    parts = [f"{family:02X}", f"{len(template):02X}"]
    for item in template:
        name = parameter_name(item)
        parts.append(f"<{name}>" if name else f"{byte_literal(item):02X}")
    parts.extend(["<sum>", f"{TERMINATOR:02X}"])
    return " ".join(parts)
