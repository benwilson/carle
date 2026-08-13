"""Animation coordination — make the robot read as talking while audio plays (U5).

The speak server (U4) drives one hook around every real playback: it calls ``on_start()``
when audio begins and ``on_end(outcome)`` once when it ends — whether the clip/stream
``COMPLETED``, was ``STOPPED``, or the device ``DIED``. `RobotAnimation` is the concrete
hook that turns those two edges into robot motion over the control-plane daemon (KTD5):

- ``on_start()`` enqueues a held *talking* LED face (code 45) and then pulses a couple of
  arm gestures on an **interval timer** — a background thread that emits one gesture every
  ``interval`` seconds — so the robot keeps moving for the audio's duration (R6).
- ``on_end(outcome)`` stops that timer and enqueues a **neutral return** — clear the face
  (``face`` 0) and bring the arms down (``gesture`` 19). It fires for *every* outcome, so
  the robot never holds the talking face after the audio is over (R7).

Two invariants keep the talking face from being held forever:

- A **watchdog timeout** armed at ``on_start`` returns the robot to neutral after
  ``watchdog`` seconds even if ``on_end`` is somehow never called (a stalled, silent, but
  still-open stream). Teardown is idempotent, so whichever of ``on_end`` and the watchdog
  fires first wins and the other is a no-op.
- **Graceful degradation:** every daemon call is wrapped so that if the daemon is not
  reachable (or any request raises), the failure is logged and animation is skipped — the
  exception never propagates back into the server's playback path. Audio must play whether
  or not the robot animates (KTD5).

The daemon is reached only as a *client*: the request callable (default
``carle.daemon.client.request``) is injected, so tests pass a fake that captures the
enqueued items and never touches a real socket. Item shapes are the daemon's wire
vocabulary (``carle.daemon.protocol``): a held face is ``{"face": N}`` (0 clears) and a
canned gesture pulse is ``{"gesture": N}``, wrapped in ``{"op": "enqueue", "items": [...]}``.
"""

from __future__ import annotations

import functools
import logging
import threading
from collections.abc import Callable, Sequence

from carle.daemon import client
from carle.speak.stream import Outcome

_log = logging.getLogger(__name__)

#: The LED expression code that reads as a talking face (docs/protocol-reference.md).
TALKING_FACE = 45
#: Face code 0 clears the LED face back to neutral.
NEUTRAL_FACE = 0
#: The canned 0xB2 gesture that brings the arms down to a neutral rest (per plan KTD5).
NEUTRAL_GESTURE = 19
#: A couple of canned arm gestures pulsed in turn while talking. Illustrative and tunable
#: — any 0xB2 hand/arm codes read as gesturing; alternating two keeps the motion lively.
TALKING_GESTURES: tuple[int, ...] = (1, 3)

#: Seconds between gesture pulses while talking (a calm, servo-safe cadence).
DEFAULT_INTERVAL = 2.0
#: Max seconds the talking face may be held before the watchdog forces a neutral return.
DEFAULT_WATCHDOG = 300.0
#: How long teardown waits for the gesture thread to unwind before enqueuing neutral.
_JOIN_TIMEOUT = 5.0

#: The injected daemon-request callable: takes one request object, returns the response.
RequestFn = Callable[[dict], object]


class RobotAnimation:
    """Drive the robot's talking face + motion for a playback, then return it to neutral.

    Satisfies the server's ``AnimationHook`` protocol (``on_start`` / ``on_end``). Construct
    one per service and pass it as ``SpeakService(animation=...)``; the same instance is
    reused across playbacks (each ``on_start`` starts fresh, each ``on_end`` tears down).

    All daemon interaction goes through the injected ``request`` callable; a raising client
    (no daemon) degrades to "no animation", never breaking playback.
    """

    def __init__(
        self,
        *,
        request: RequestFn | None = None,
        socket_path: str | None = None,
        face_code: int = TALKING_FACE,
        gestures: Sequence[int] = TALKING_GESTURES,
        neutral_face: int = NEUTRAL_FACE,
        neutral_gesture: int = NEUTRAL_GESTURE,
        interval: float = DEFAULT_INTERVAL,
        watchdog: float = DEFAULT_WATCHDOG,
    ) -> None:
        if request is None:
            request = (
                client.request
                if socket_path is None
                else functools.partial(client.request, socket_path=socket_path)
            )
        self._request = request
        self._face_code = face_code
        self._gestures = tuple(gestures)
        self._neutral_face = neutral_face
        self._neutral_gesture = neutral_gesture
        self._interval = interval
        self._watchdog = watchdog

        #: Guards the lifecycle state below; teardown runs at most once per playback.
        self._lock = threading.Lock()
        self._active = False
        self._stop_event = threading.Event()
        self._gesture_thread: threading.Thread | None = None
        self._watchdog_timer: threading.Timer | None = None
        self._gesture_index = 0

    # --- the hook (called by the speak server around real playback) -------------------

    def on_start(self) -> None:
        """Enqueue the talking face and start pulsing gestures for the audio's duration."""
        with self._lock:
            if self._active:
                return  # already animating a playback; ignore a duplicate start
            self._active = True
            self._stop_event = threading.Event()
            self._gesture_index = 0

        if not self._enqueue([{"face": self._face_code}]):
            # No daemon: skip animation entirely. Playback is unaffected (graceful degrade).
            with self._lock:
                self._active = False
            return

        thread = threading.Thread(
            target=self._gesture_loop, name="speak-animate-gestures", daemon=True
        )
        watchdog = threading.Timer(self._watchdog, self._on_watchdog)
        watchdog.daemon = True
        with self._lock:
            self._gesture_thread = thread
            self._watchdog_timer = watchdog
        thread.start()
        watchdog.start()

    def on_end(self, outcome: Outcome) -> None:
        """Stop the gesture timer and return the robot to neutral, for every outcome (R7)."""
        _log.debug("animation ending on outcome %s", getattr(outcome, "value", outcome))
        self._teardown()

    # --- internals -------------------------------------------------------------------

    def _on_watchdog(self) -> None:
        """Safety net: never leave the talking face held if ``on_end`` never arrives."""
        _log.warning(
            "speak animation watchdog fired after %.0fs — forcing a neutral return",
            self._watchdog,
        )
        self._teardown()

    def _gesture_loop(self) -> None:
        """Emit one arm gesture every ``interval`` seconds until told to stop."""
        if not self._gestures:
            return
        while not self._stop_event.wait(self._interval):
            gesture = self._gestures[self._gesture_index % len(self._gestures)]
            self._gesture_index += 1
            if not self._enqueue([{"gesture": gesture}]):
                # The daemon went away mid-playback; stop pulsing. on_end still runs.
                return

    def _teardown(self) -> None:
        """Stop the timer/thread and enqueue the neutral return exactly once."""
        with self._lock:
            if not self._active:
                return  # on_end and the watchdog race; the first one through wins
            self._active = False
            self._stop_event.set()
            thread = self._gesture_thread
            watchdog = self._watchdog_timer
            self._gesture_thread = None
            self._watchdog_timer = None

        if watchdog is not None:
            watchdog.cancel()
        if thread is not None and thread is not threading.current_thread():
            thread.join(_JOIN_TIMEOUT)
        # Enqueue neutral only after the gesture loop is stopped, so no late pulse can
        # override the neutral pose. Failure here is logged, never raised.
        self._enqueue([{"face": self._neutral_face}, {"gesture": self._neutral_gesture}])

    def _enqueue(self, items: list[dict]) -> bool:
        """Send one enqueue request to the daemon; return False (and log) on any failure.

        This is the graceful-degradation boundary: a missing/raising daemon becomes a
        logged skip, never an exception that could break the server's playback path.
        """
        try:
            self._request({"op": "enqueue", "items": items})
        except Exception as exc:  # noqa: BLE001 - animation must never break playback
            _log.warning("speak animation skipped — daemon unreachable: %s", exc)
            return False
        return True
