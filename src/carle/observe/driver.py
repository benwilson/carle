"""Drive one movement code through the running daemon (U2).

The observe loop never touches BLE directly — it enqueues one item on the running daemon,
inheriting the daemon's servo-safety (a 0xB2 gesture pulses once, a 0xB6 pose holds) and
its body-idle suppression (R10). Item kinds are only recognised under the `enqueue` op, so
every family sends the wrapped form; a bare ``{"pose": n}`` would be rejected by the daemon.
"""

from __future__ import annotations

from collections.abc import Callable

from carle.daemon import client
from carle.daemon.client import NoDaemonError

#: The families the observe loop drives. GESTURE is a 0xB2 hand code; POSE/WAIST are 0xB6.
GESTURE = "gesture"
POSE = "pose"
WAIST = "waist"

#: family -> the enqueue item key the daemon's protocol.parse_steps understands.
_ITEM_KEY = {GESTURE: "gesture", POSE: "pose", WAIST: "waist"}

#: The movement families the driver can drive today (the observe loop validates against this).
FAMILIES = frozenset(_ITEM_KEY)


class DriverError(Exception):
    """Raised when a code cannot be driven — no live daemon, or an unknown family."""


Requester = Callable[[dict], dict]
LiveCheck = Callable[[], bool]


def drive_code(
    family: str,
    code: int,
    *,
    requester: Requester | None = None,
    daemon_live: LiveCheck | None = None,
) -> dict:
    """Enqueue one movement code as a single daemon pulse. Raises DriverError if no daemon."""
    live = daemon_live if daemon_live is not None else client.daemon_live
    request = requester if requester is not None else client.request
    key = _ITEM_KEY.get(family)
    if key is None:
        raise DriverError(f"unknown movement family {family!r}")
    if not live():
        raise DriverError(
            "no daemon is holding the link — start it with `carle daemon start <address>`"
        )
    try:
        return request({"op": "enqueue", "items": [{key: int(code)}]})
    except NoDaemonError as exc:
        raise DriverError(str(exc)) from exc


# --- variation-ladder drive (KTD6) ----------------------------------------------------

#: Pulses for the "repeat" rung, and the pause between pulses (servo-safe: well over the
#: engine's SAFE_HOLD, and matching the pose vocabulary's drive-each-joint-hard guidance).
REPEAT_PULSES = 3
PULSE_GAP = 0.8

#: Within a 12-code hand-tab half, the lowering pairs and the raise that precedes each —
#: the "raise_first" pre-pose that makes a lowering motion legible from a rest start
#: (docs/protocol-reference: pairs (3,4) lower from (1,2)'s raise, (7,8) bring both arms
#: down, (11,12) lower the elbow (9,10) bends up).
_GESTURE_PRE_RAISE = {3: 1, 4: 1, 7: 5, 8: 5, 11: 9, 12: 9}


def pre_pulse_for(family: str, code: int) -> int | None:
    """The code to pulse before `code` on the raise_first rung, or None when moot.

    A lowering/return code read from a rest start shows nothing — there is nothing to
    lower. Raising the paired joint first gives the motion something to act on. Raise
    codes need no pre-pose.
    """
    if family == POSE:
        # 0xB6 poses: odd raises, its even pair returns (docs/protocol-reference). The
        # even return's pre-pose is its own odd raise.
        if 1 <= code <= 12 and code % 2 == 0:
            return code - 1
        return None
    if family == GESTURE:
        # 0xB2 hand codes: left half 1-12, right half 13-24 mirrors it. Lowering PAIRS
        # (not the evens) map to the raise pair that precedes them.
        if 1 <= code <= 24:
            half_offset = 12 if code > 12 else 0
            pre = _GESTURE_PRE_RAISE.get(code - half_offset)
            if pre is not None:
                return pre + half_offset
        return None
    return None


def drive_for_variation(
    family: str,
    code: int,
    variation: str,
    *,
    requester: Requester | None = None,
    daemon_live: LiveCheck | None = None,
    sleeper: Callable[[float], None] | None = None,
) -> None:
    """Drive one code the way the ladder rung asks (KTD6).

    `baseline`/`brighter`/`longer` differ only in capture, so they drive one pulse.
    `raise_first` pulses the paired raise, waits, then the code. `repeat` pulses the code
    three times — an under-extending servo reaches full travel on repeated drive (pose
    vocabulary), so a shallow first read gets a fair second look.
    """
    import time  # noqa: PLC0415 - here so tests inject sleeper without patching time

    sleep = sleeper if sleeper is not None else time.sleep
    if variation == "repeat":
        for pulse in range(REPEAT_PULSES):
            if pulse:
                sleep(PULSE_GAP)
            drive_code(family, code, requester=requester, daemon_live=daemon_live)
        return
    if variation == "raise_first":
        pre = pre_pulse_for(family, code)
        if pre is not None:
            drive_code(family, pre, requester=requester, daemon_live=daemon_live)
            sleep(PULSE_GAP)
    drive_code(family, code, requester=requester, daemon_live=daemon_live)
