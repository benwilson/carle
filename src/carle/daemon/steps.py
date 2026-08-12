"""The queue's language — atomic step types (U2).

A step is the smallest thing the engine executes. Every step carries a `StepMode`:
`AWAIT` blocks the queue until the step completes, `SPAWN` starts it and lets the queue
proceed (KTD5), which is how speech runs while the robot moves.

Movement steps build `0xB6` frames and media steps `0xB3` frames through `carle.frame`;
bytes are never hand-assembled. The movement payload order matches the protocol
reference: `[mode, speed, direction, waist, limb, p5]`.

`SAFE_HOLD` is the servo-safe minimum a joint target holds before it may change
(docs/movement-vocabulary.md). Macros in `carle.daemon.moves` never emit a shorter hold,
and the engine's composition guard enforces the same floor across concurrent tracks.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from carle import frame

#: Servo-safe minimum hold, in seconds. Driving a joint faster than this squeals the
#: geared servos (docs/movement-vocabulary.md).
SAFE_HOLD = 0.5

MOVEMENT_FAMILY = 0xB6
MEDIA_FAMILY = 0xB3
EXPRESSION_FAMILY = 0xB2


class StepMode(Enum):
    AWAIT = "await"
    SPAWN = "spawn"


@dataclass(frozen=True)
class MovementStep:
    """One `0xB6` movement frame held for `hold` seconds.

    A pose sets `limb`; a waist lean sets `waist`; locomotion sets `mode`/`speed`/
    `direction`. The `joint_bytes` property is what the engine's cross-track guard reads.
    """

    mode: int = 0
    speed: int = 0
    direction: int = 0
    waist: int = 0
    limb: int = 0
    p5: int = 0
    hold: float = SAFE_HOLD
    step_mode: StepMode = StepMode.AWAIT

    def build(self) -> bytes:
        return frame.build(
            MOVEMENT_FAMILY,
            [self.mode, self.speed, self.direction, self.waist, self.limb, self.p5],
        )

    @property
    def joint_bytes(self) -> dict[str, int]:
        """The individually-addressable joints this step drives (limb and waist)."""
        return {"limb": self.limb, "waist": self.waist}


@dataclass(frozen=True)
class PauseStep:
    duration: float
    step_mode: StepMode = StepMode.AWAIT


@dataclass(frozen=True)
class SayStep:
    text: str
    step_mode: StepMode = StepMode.SPAWN  # speech usually overlaps motion


@dataclass(frozen=True)
class MediaStep:
    sub: int
    index: int = 0
    step_mode: StepMode = StepMode.SPAWN

    def build(self) -> bytes:
        return frame.build(MEDIA_FAMILY, [self.sub, self.index])


@dataclass(frozen=True)
class GestureStep:
    """Pulse one `0xB2` action code a single time — a fire-and-forget trigger.

    The `0xB2` limb/move codes (1-38) re-run their motion every time the frame arrives, so
    unlike a face they must be sent ONCE, never held/re-asserted (re-asserting an arm code
    squeals the servos). The daemon's ongoing heartbeat keeps the idle routine off between
    gestures; this step just fires the code and lets the queue proceed.
    """

    code: int
    step_mode: StepMode = StepMode.SPAWN  # a trigger never blocks the queue

    def build(self) -> bytes:
        return frame.build(EXPRESSION_FAMILY, [self.code])


@dataclass(frozen=True)
class FaceStep:
    """Set the robot's LED face to one `0xB2` expression code, or clear it.

    Unlike a media trigger, a face is *held display state*: the engine re-asserts the
    held code on its heartbeat cadence so the robot's idle routine cannot repaint the
    face between frames (docs/protocol-reference.md, family 0xB2). `code` 0 clears the
    hold and lets the idle face resume. The expression codes are 39-48 (five faces, in
    odd/even pairs); the engine sends whatever code it is given.
    """

    code: int
    step_mode: StepMode = StepMode.SPAWN  # setting the face never blocks the queue

    def build(self) -> bytes:
        return frame.build(EXPRESSION_FAMILY, [self.code])


Step = MovementStep | PauseStep | SayStep | MediaStep | FaceStep | GestureStep


# --- Readable constructors for the primitives macros are built from -----------------


def pose(limb: int, hold: float = SAFE_HOLD, step_mode: StepMode = StepMode.AWAIT) -> MovementStep:
    """Raise/return one limb-selector joint (1-12)."""
    return MovementStep(limb=limb, hold=hold, step_mode=step_mode)


def waist(
    value: int, hold: float = SAFE_HOLD, step_mode: StepMode = StepMode.AWAIT
) -> MovementStep:
    """Lean at the waist (1) or return upright (2)."""
    return MovementStep(waist=value, hold=hold, step_mode=step_mode)


def travel(
    direction: int,
    speed: int,
    mode: int = 1,
    hold: float = SAFE_HOLD,
    step_mode: StepMode = StepMode.AWAIT,
) -> MovementStep:
    """Walk (mode 1) or slide (mode 2) a heading. This moves the robot across the floor."""
    return MovementStep(mode=mode, speed=speed, direction=direction, hold=hold, step_mode=step_mode)


def face(code: int) -> FaceStep:
    """Hold an LED expression code (39-48), or clear the held face with 0."""
    return FaceStep(code=code)


def gesture(code: int) -> GestureStep:
    """Pulse a `0xB2` limb/move code (1-38) once — fire-and-forget, never held."""
    return GestureStep(code=code)
