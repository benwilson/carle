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
