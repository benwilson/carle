"""Named-move macros — registry data, not engine code (U2, KTD9).

A macro is a name mapped to an ordered sequence of servo-safe primitive steps. The
engine expands a named move into these steps; it has no per-move logic of its own, so a
new move — the still-unmapped leg-forward code, say — slots in as one registry entry
without touching the engine.

The seed set comes straight from docs/movement-vocabulary.md. Its honesty carries over:
`wave` is the arm *sweep*, the closest the arm joints get to a wave; the raised-arm elbow
bob reads as a `fist_pump`, not a wave.
"""

from __future__ import annotations

from collections.abc import Callable

from carle.daemon.steps import MovementStep, Step, pose, waist


def _flap(up: int, down: int, times: int = 3) -> list[Step]:
    steps: list[Step] = []
    for _ in range(times):
        steps.append(pose(up))
        steps.append(pose(down))
    return steps


def _wave() -> list[Step]:
    # Left shoulder sweep: the outstretched arm swings side to side (limb 5 up / 6 down).
    return _flap(5, 6, times=3)


def _fist_pump() -> list[Step]:
    # Raise the left arm and hold, then bob the forearm at the elbow (9 up / 10 down),
    # then lower — reads as pumping a fist in the air.
    steps: list[Step] = [pose(1)]
    for _ in range(3):
        steps.append(pose(9))
        steps.append(pose(10))
    steps.append(pose(2))
    return steps


def _sway() -> list[Step]:
    # Lean left at the waist and return, a few times — dancing in place.
    steps: list[Step] = []
    for _ in range(3):
        steps.append(waist(1))
        steps.append(waist(2))
    return steps


#: name -> factory producing a fresh step list. Factories, not shared lists, so an
#: expansion is never mutated in place by the engine.
MACROS: dict[str, Callable[[], list[Step]]] = {
    "wave": _wave,
    "fist_pump": _fist_pump,
    "sway": _sway,
}


def expand(name: str) -> list[Step]:
    """Expand a named move into its primitive steps, or raise on an unknown name."""
    if name not in MACROS:
        known = ", ".join(sorted(MACROS))
        raise ValueError(f"unknown move {name!r}; known moves: {known}")
    return MACROS[name]()


def move_names() -> list[str]:
    """Every registered move name, sorted — what `list_moves` returns."""
    return sorted(MACROS)


def is_servo_safe(steps: list[Step]) -> bool:
    """True when no movement step drives more than one joint at once.

    A guard the tests use: a macro step must change at most one of the two
    individually-addressable joints (limb, waist). Locomotion bytes are not joints.
    """
    for step in steps:
        if isinstance(step, MovementStep):
            active = [name for name, value in step.joint_bytes.items() if value]
            if len(active) > 1:
                return False
    return True
