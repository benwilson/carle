"""U5 — animation coordination, driven entirely by a fake daemon request callable.

No real daemon or socket is touched: a `FakeClient` captures every enqueue request and
signals a threading.Event when a gesture pulse arrives, so a scenario can wait on real
motion without sleeping for a fixed time. Intervals and the watchdog are injected tiny so
the tests run fast and deterministically.

Item shapes mirror the daemon wire (`carle.daemon.protocol`): a held face is
``{"face": N}`` (0 clears) and a canned gesture is ``{"gesture": N}``, both inside
``{"op": "enqueue", "items": [...]}``.
"""

from __future__ import annotations

import threading

from carle.speak.animate import (
    NEUTRAL_FACE,
    NEUTRAL_GESTURE,
    TALKING_FACE,
    RobotAnimation,
)
from carle.speak.stream import Outcome


class FakeClient:
    """Capture enqueue requests; signal when a gesture pulse is seen (or optionally raise)."""

    def __init__(self, *, raises: bool = False) -> None:
        self._raises = raises
        self._lock = threading.Lock()
        self.requests: list[dict] = []
        self.gesture_seen = threading.Event()

    def request(self, obj: dict) -> dict:
        if self._raises:
            raise ConnectionError("no daemon at /tmp/carle.sock — is it running?")
        with self._lock:
            self.requests.append(obj)
        for item in obj.get("items", []):
            if "gesture" in item:
                self.gesture_seen.set()
        return {"ok": True}

    # --- assertions helpers -------------------------------------------------------

    def all_items(self) -> list[dict]:
        with self._lock:
            return [item for req in self.requests for item in req.get("items", [])]

    def faces(self) -> list[int]:
        return [item["face"] for item in self.all_items() if "face" in item]

    def gestures(self) -> list[int]:
        return [item["gesture"] for item in self.all_items() if "gesture" in item]


def make_animation(client: FakeClient, *, interval: float = 0.01, watchdog: float = 30.0):
    return RobotAnimation(request=client.request, interval=interval, watchdog=watchdog)


def test_on_start_enqueues_talking_face_and_pulses_at_least_one_gesture():
    # Covers R6: a talking LED face plus motion for the audio's duration.
    client = FakeClient()
    anim = make_animation(client)

    anim.on_start()
    try:
        assert client.gesture_seen.wait(2), "no gesture pulse arrived through the daemon"
    finally:
        anim.on_end(Outcome.COMPLETED)

    assert TALKING_FACE in client.faces()  # the talking face was enqueued
    assert client.gestures(), "expected at least one arm gesture while talking"


def test_on_end_completed_stops_the_timer_and_returns_to_neutral():
    # Covers R7: audio ended -> clear the face and bring the arms down.
    client = FakeClient()
    anim = make_animation(client)

    anim.on_start()
    client.gesture_seen.wait(2)
    anim.on_end(Outcome.COMPLETED)

    items = client.all_items()
    assert items[-2:] == [{"face": NEUTRAL_FACE}, {"gesture": NEUTRAL_GESTURE}]
    # The timer is stopped: no further gestures after the neutral return.
    count = len(client.gestures())
    threading.Event().wait(0.05)
    assert len(client.gestures()) == count


def test_on_end_died_returns_to_neutral():
    # Covers R7: a lost device mid-playback still returns the robot to neutral.
    client = FakeClient()
    anim = make_animation(client)

    anim.on_start()
    client.gesture_seen.wait(2)
    anim.on_end(Outcome.DIED)

    assert client.faces()[-1] == NEUTRAL_FACE
    assert client.gestures()[-1] == NEUTRAL_GESTURE


def test_watchdog_returns_to_neutral_when_on_end_never_comes():
    # A stalled, silent-but-open stream: on_end is never called, but the watchdog fires and
    # the robot returns to neutral rather than holding the talking face forever.
    client = FakeClient()
    anim = make_animation(client, watchdog=0.05)

    anim.on_start()
    # Deliberately never call on_end — the watchdog must clean up on its own.
    deadline = threading.Event()
    for _ in range(200):
        if client.faces()[-1:] == [NEUTRAL_FACE]:
            break
        deadline.wait(0.02)
    assert client.faces()[-1] == NEUTRAL_FACE, "watchdog did not return the face to neutral"
    assert client.gestures()[-1] == NEUTRAL_GESTURE


def test_no_daemon_skips_animation_without_raising():
    # Graceful degradation: the client raises (no daemon). on_start/on_end must swallow it
    # so the server's playback path is unaffected.
    client = FakeClient(raises=True)
    anim = make_animation(client)

    anim.on_start()  # must not raise
    anim.on_end(Outcome.COMPLETED)  # must not raise

    assert client.requests == []  # nothing captured — the raising client recorded nothing


def test_watchdog_and_on_end_are_idempotent_together():
    # Whichever of on_end / watchdog fires first wins; the other is a no-op, so neutral is
    # enqueued exactly once (the talking face is never re-cleared or the arms re-dropped).
    client = FakeClient()
    anim = make_animation(client, watchdog=0.05)

    anim.on_start()
    client.gesture_seen.wait(2)
    anim.on_end(Outcome.STOPPED)
    threading.Event().wait(0.1)  # give the watchdog time to (not) fire a second neutral

    assert client.faces().count(NEUTRAL_FACE) == 1
    assert client.gestures().count(NEUTRAL_GESTURE) == 1
