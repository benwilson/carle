"""The daemon's control-channel protocol — newline-delimited JSON (U4, KTD1).

One request per line, one response per line. The CLI and the MCP server both speak this
verbatim, so the two interfaces can never drift. A request is `{"op": ...}`; a response
is `{"ok": true, ...}` or `{"ok": false, "error": ...}`.

`parse_steps` turns the wire's step dicts into `carle.daemon.steps` objects, expanding a
named move through the macro registry — the one place the wire vocabulary meets the queue
language.
"""

from __future__ import annotations

import json

from carle.daemon import moves
from carle.daemon.steps import (
    MediaStep,
    PauseStep,
    SayStep,
    Step,
    StepMode,
    pose,
    travel,
    waist,
)

OPS = frozenset({"enqueue", "clear", "stop", "status", "list_moves", "shutdown"})


class ProtocolError(Exception):
    """Raised on a malformed request or step item."""


def dumps(obj: dict) -> bytes:
    return (json.dumps(obj) + "\n").encode("utf-8")


def loads(line: bytes | str) -> dict:
    try:
        obj = json.loads(line)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ProtocolError(f"not valid JSON: {exc}") from exc
    if not isinstance(obj, dict):
        raise ProtocolError("request must be a JSON object")
    return obj


def _step_mode(item: dict, default: StepMode) -> StepMode:
    raw = item.get("mode")
    if raw is None:
        return default
    try:
        return StepMode(raw)
    except ValueError as exc:
        raise ProtocolError(f"mode must be 'await' or 'spawn', got {raw!r}") from exc


def parse_steps(items: list) -> list[Step]:
    """Expand a list of wire step dicts into engine steps.

    Item shapes: `{"move": "wave"}` (a macro), `{"pose": N, "hold": s}`,
    `{"waist": v, "hold": s}`, `{"travel": {...}}`, `{"pause": s}`,
    `{"say": text, "mode": "spawn"}`, `{"media": {"sub": n, "index": n}}`.
    """
    if not isinstance(items, list):
        raise ProtocolError("enqueue 'items' must be a list")
    steps: list[Step] = []
    for item in items:
        if not isinstance(item, dict):
            raise ProtocolError(f"each item must be an object, got {item!r}")
        steps.extend(_parse_item(item))
    return steps


def _parse_item(item: dict) -> list[Step]:
    if "move" in item:
        # A named macro expands to a servo-safe primitive sequence.
        return moves.expand(str(item["move"]))
    hold = float(item.get("hold", 0.5))
    if "pose" in item:
        return [pose(int(item["pose"]), hold=hold, step_mode=_step_mode(item, StepMode.AWAIT))]
    if "waist" in item:
        return [waist(int(item["waist"]), hold=hold, step_mode=_step_mode(item, StepMode.AWAIT))]
    if "travel" in item:
        t = item["travel"]
        return [
            travel(
                direction=int(t["direction"]),
                speed=int(t["speed"]),
                mode=int(t.get("mode", 1)),
                hold=float(t.get("hold", hold)),
                step_mode=_step_mode(item, StepMode.AWAIT),
            )
        ]
    if "pause" in item:
        return [PauseStep(float(item["pause"]), step_mode=_step_mode(item, StepMode.AWAIT))]
    if "say" in item:
        return [SayStep(str(item["say"]), step_mode=_step_mode(item, StepMode.SPAWN))]
    if "media" in item:
        m = item["media"]
        return [
            MediaStep(
                int(m["sub"]), int(m.get("index", 0)), step_mode=_step_mode(item, StepMode.SPAWN)
            )
        ]
    raise ProtocolError(f"unrecognized step item: {sorted(item)}")
