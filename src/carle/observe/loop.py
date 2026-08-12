"""Observation model and the agreement/retry engine (U3).

This is the deterministic trust core. The judge (agent at run time, a fake in tests) turns
sampled frames into a structured `Observation`; the engine requires the same reading across
independent repeats before confirming it (R4), retries with a bounded variation ladder when a
reading is low-confidence or disagrees (R5), and marks a code "uncertain" rather than guessing
once the ladder is exhausted (R5/R8). It never stalls: every code resolves within the limit.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

#: Both observations of a code must clear this confidence before they can agree (KTD8).
CONFIDENCE_FLOOR = 0.6

#: How many agreeing readings confirm a code, and how many extra retries the ladder allows (KTD6).
DEFAULT_REPEATS = 2
DEFAULT_RETRY_LIMIT = 2

#: The variation ladder, in order — each rung tells the capture/drive seams how to differ so a
#: subtle or ambiguous motion becomes legible (KTD6). The raise-first rung deliberately changes
#: the before-pose to exaggerate the motion. The ladder is a superset of the default attempt
#: budget: the last rung ("repeat the pulse") is reached only at a higher `--retries`, and the
#: index is clamped to the last rung so a short budget simply uses the earlier rungs.
DEFAULT_VARIATIONS: tuple[str, ...] = ("baseline", "brighter", "longer", "raise_first", "repeat")


@dataclass(frozen=True)
class Observation:
    """One structured reading of a code's effect, produced by the judge seam."""

    code: int
    joint: str  # e.g. "left_arm", "right_elbow", "waist", "none"
    motion: str  # e.g. "raise", "lower", "lateral", "bend", "none"
    direction: str = ""  # optional finer direction, e.g. "up" / "out"
    confidence: float = 0.0
    notes: str = ""

    @property
    def key(self) -> tuple[str, str]:
        # Agreement is on joint + motion (KTD8). `direction` is optional finer detail the
        # writer keeps, not an agreement axis — else a judge filling it inconsistently across
        # two otherwise-matching reads would spuriously route the code to "uncertain".
        return (self.joint, self.motion)


@dataclass(frozen=True)
class CodeResult:
    """The outcome for one code: a reproduced `Observation`, or `uncertain`."""

    family: str
    code: int
    status: str  # "confirmed" or "uncertain"
    observation: Observation | None = None
    attempts: int = 0

    @property
    def confirmed(self) -> bool:
        return self.status == "confirmed"


def observations_agree(a: Observation, b: Observation, floor: float = CONFIDENCE_FLOOR) -> bool:
    """Two readings agree when joint, motion, and direction match and both clear the floor."""
    if a is None or b is None:
        return False
    if a.confidence < floor or b.confidence < floor:
        return False
    return a.key == b.key


def _reproduced(observations: list[Observation], repeats: int, floor: float) -> Observation | None:
    """Return a reading seen `repeats` times among floor-clearing observations, else None."""
    groups: dict[tuple[str, str, str], list[Observation]] = {}
    for obs in observations:
        if obs is None or obs.confidence < floor:
            continue
        groups.setdefault(obs.key, []).append(obs)
    for group in groups.values():
        if len(group) >= repeats:
            return max(group, key=lambda o: o.confidence)  # the most confident representative
    return None


#: Seams the engine drives. `drive(family, code, variation)` pulses the code; `capture(variation)`
#: records and returns something with `.frames` and `.cleanup()`; `judge(frames)` -> Observation.
DriveSeam = Callable[[str, int, str], object]
CaptureSeam = Callable[[str], object]
JudgeSeam = Callable[[list], Observation]


def derive_code(
    family: str,
    code: int,
    *,
    drive: DriveSeam,
    capture: CaptureSeam,
    judge: JudgeSeam,
    repeats: int = DEFAULT_REPEATS,
    retry_limit: int = DEFAULT_RETRY_LIMIT,
    variations: tuple[str, ...] = DEFAULT_VARIATIONS,
    floor: float = CONFIDENCE_FLOOR,
) -> CodeResult:
    """Derive one code: repeat until `repeats` readings agree, else mark uncertain."""
    observations: list[Observation] = []
    max_attempts = repeats + retry_limit
    for attempt in range(max_attempts):
        variation = variations[min(attempt, len(variations) - 1)]
        drive(family, code, variation)
        clip = capture(variation)
        try:
            observations.append(judge(clip.frames))
        finally:
            clip.cleanup()  # frames are ephemeral, discarded every attempt (R7)
        found = _reproduced(observations, repeats, floor)
        if found is not None:
            return CodeResult(family, code, "confirmed", found, attempt + 1)
    return CodeResult(family, code, "uncertain", None, max_attempts)
