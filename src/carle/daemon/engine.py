"""The queue engine — the tick loop, heartbeat, joint-state, and control ops (U3).

The engine is driven by an injected `clock` and `connection`, so tests advance ticks
deterministically without sleeping or touching Bluetooth. Each `tick`:

1. advances the main await queue (one step at a time) and any spawned tracks,
2. composes the six-byte movement target from every active track (last-writer-wins),
3. applies the KTD4 guard — an emitted frame changes at most one *joint* byte (waist or
   limb) from the previous frame, and never faster than the servo-safe minimum, so
   cross-track composition can never produce the squeal a bare last-writer-wins would,
4. sends the frame when it changed, else sends the no-op once the silence floor passes
   (R6/R7), and
5. on a send failure, applies the per-type resume policy (KTD6): re-run a `pose`/`pause`,
   drop an interrupted `move`, do not re-fire a `say`/`media`.

`stop` terminates every spawned step (including TTS subprocesses) before walking joints
back to neutral (KTD5/R9); `clear` drops pending but lets the current step and in-flight
spawns finish (R8).
"""

from __future__ import annotations

import shutil
import subprocess
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from carle import frame
from carle.daemon.connection import DaemonConnectionError
from carle.daemon.steps import (
    SAFE_HOLD,
    FaceStep,
    GestureStep,
    MediaStep,
    MovementStep,
    PauseStep,
    SayStep,
    Step,
    StepMode,
)

#: The heartbeat frame: a movement frame with every byte zero (docs/protocol-reference).
NOOP = frame.build(0xB6, [0, 0, 0, 0, 0, 0])

#: Movement payload indices that address individual joints (the KTD4 guard limits these).
_WAIST_INDEX = 3
_LIMB_INDEX = 4

DEFAULT_SILENCE_FLOOR = 1.0


@dataclass
class _MovementTrack:
    step: MovementStep
    deadline: float


class _TtsHandle:
    """A live host-TTS process the engine can terminate on `stop`."""

    def __init__(self, process: subprocess.Popen) -> None:
        self._process = process

    @property
    def finished(self) -> bool:
        return self._process.poll() is not None

    def terminate(self) -> None:
        if self._process.poll() is None:
            self._process.terminate()


def default_tts(text: str):
    """Speak `text` through the host tool, or return None when none is available (KTD8)."""
    tool = shutil.which("say")
    if tool is None:
        return None
    return _TtsHandle(subprocess.Popen([tool, text]))  # noqa: S603 - fixed argv, no shell


def _payload(built_frame: bytes) -> list[int]:
    return list(frame.parse(built_frame)[1])


class Engine:
    def __init__(
        self,
        connection,
        *,
        clock: Callable[[], float] = time.monotonic,
        tts: Callable[[str], object | None] = default_tts,
        silence_floor: float = DEFAULT_SILENCE_FLOOR,
    ) -> None:
        self._conn = connection
        self._clock = clock
        self._tts = tts
        self._silence_floor = silence_floor

        self._pending: deque[Step] = deque()
        self._current: Step | None = None
        self._current_deadline: float = 0.0
        self._current_say = None  # a live await-say handle blocking the main track
        self._spawn_movement: list[_MovementTrack] = []
        self._spawn_say: list[object] = []
        self._last_sent: bytes | None = None
        self._last_write: float | None = None
        #: The held LED expression: the code the robot's face should show, and the frame
        #: that sets it. Held display state (not a one-shot) — re-asserted on the heartbeat
        #: cadence so the idle routine cannot repaint the face between frames. None = no
        #: hold, so the idle face resumes.
        self._face_code: int | None = None
        self._face_frame: bytes | None = None
        self._last_face_sent: bytes | None = None
        #: When the link went down, or None while connected. While set, ticks execute
        #: nothing — steps run into a dead link are physically lost (writes are without
        #: response), which is how a whole queue burned away during a power cycle on
        #: hardware. On resume every deadline is shifted by the outage.
        self._paused_at: float | None = None
        #: How many steps of an in-flight stop() neutral walk remain in the queue. While
        #: non-zero, another stop() must not truncate the walk — cutting the return
        #: command's hold short mid-servo-travel is what left an arm physically stuck.
        self._returning_steps: int = 0
        #: When each joint byte last changed in the emitted stream — the KTD4 rate-limit's
        #: memory, so a joint can never flip faster than the servo-safe minimum regardless
        #: of which track wrote it or how short a step's declared hold is.
        self._joint_changed_at: dict[int, float] = {}

    # --- control operations -------------------------------------------------------

    def enqueue(self, steps: list[Step]) -> None:
        self._pending.extend(steps)

    def clear(self) -> None:
        """Drop pending steps; the current step and in-flight spawns finish (R8)."""
        self._pending.clear()
        # Any in-flight stop walk was just dropped with the rest of the queue; forgetting
        # that lets the next stop() build a fresh walk instead of trimming to nothing.
        self._returning_steps = 0

    def stop(self) -> None:
        """Abort now: kill spawns, then walk every non-neutral joint back (R9, KTD5).

        Stops converge instead of racing: while a previous stop's neutral walk is still
        executing, another stop keeps the walk intact (truncating a return command's hold
        mid-servo-travel left an arm physically stuck on hardware) and only drops steps
        queued after it. And because the daemon's picture of the joints can desync from
        the robot (a truncated walk, a robot-side reboot), every fresh walk ends with the
        bilateral arms-down gesture — a physical reset that does not depend on
        `_last_sent` being the truth.
        """
        for handle in self._spawn_say:
            handle.terminate()
        if self._current_say is not None:
            self._current_say.terminate()
        self._spawn_say.clear()
        self._spawn_movement.clear()
        self._current_say = None
        # A held face is part of "what the robot is doing", so an abort clears it too and
        # lets the idle face resume; the next heartbeat is a NOOP rather than the face.
        self._face_code = None
        self._face_frame = None
        if self._returning_steps:
            # A neutral walk is underway: let it finish, drop anything queued behind it.
            while len(self._pending) > self._returning_steps:
                self._pending.pop()
            return
        self._current = None
        # Replace the queue with a neutral-return walk for whatever joints are raised,
        # sealed with the bilateral arms-down gesture (docs/protocol-reference: hand code
        # 19 brings both arms down together) so a desynced joint still comes home.
        walk = [*self._neutral_return(), GestureStep(code=19), MovementStep()]
        self._pending = deque(walk)
        self._returning_steps = len(walk)

    def status(self) -> dict:
        current = type(self._current).__name__ if self._current is not None else None
        return {
            "connected": bool(getattr(self._conn, "is_connected", False)),
            "current": current,
            "pending": len(self._pending),
            "spawns": len(self._spawn_movement) + len(self._spawn_say),
            "face": self._face_code,
        }

    async def battery(self) -> int | None:
        return await self._conn.read_battery()

    def _neutral_return(self) -> list[Step]:
        """Return steps that bring the last target's joints to rest, then to no-command."""
        if self._last_sent is None:
            return []
        payload = _payload(self._last_sent)
        steps: list[Step] = []
        waist_v, limb_v = payload[_WAIST_INDEX], payload[_LIMB_INDEX]
        # Odd values raise; the paired even value returns. Even/zero is already at rest.
        if limb_v:
            steps.append(MovementStep(limb=limb_v + 1 if limb_v % 2 else 0))
        if waist_v:
            steps.append(MovementStep(waist=waist_v + 1 if waist_v % 2 else 0))
        steps.append(MovementStep())  # all-zero: no command, joints hold at rest
        return steps

    # --- the tick -----------------------------------------------------------------

    async def tick(self) -> None:
        now = self._clock()
        if not getattr(self._conn, "is_connected", True):
            # Link down: execute nothing. A step run now would burn into the dead link
            # (writes are without-response) and be lost — the queue waits for the link.
            if self._paused_at is None:
                self._paused_at = now
                ensure_reconnect = getattr(self._conn, "ensure_reconnect", None)
                if ensure_reconnect is not None:
                    ensure_reconnect()
            return
        if self._paused_at is not None:
            self._resume_after_outage(now)
        self._prune_spawns(now)
        media_frames = self._advance(now)
        composed = self._compose()
        guarded = self._guard(composed, now)
        await self._emit(now, guarded, media_frames)

    def _resume_after_outage(self, now: float) -> None:
        """Shift every deadline by the outage and force a full re-assert of held state.

        The robot may have rebooted during the outage (a power cycle is the common cause),
        so `_last_sent`/`_last_face_sent` are no longer the truth about what it shows or
        holds — clearing them makes the next emit re-send the composed target and the held
        face instead of deduplicating against state the robot lost.
        """
        gap = now - self._paused_at
        self._paused_at = None
        if self._current is not None:
            self._current_deadline += gap
        for track in self._spawn_movement:
            track.deadline += gap
        self._last_sent = None
        self._last_face_sent = None
        self._last_write = None

    def _prune_spawns(self, now: float) -> None:
        self._spawn_movement = [t for t in self._spawn_movement if now < t.deadline]
        self._spawn_say = [h for h in self._spawn_say if not h.finished]

    def _advance(self, now: float) -> list[bytes]:
        """Advance the main await queue, returning any media frames to send this tick."""
        media_frames: list[bytes] = []

        # An await-say blocks the main track until its subprocess finishes.
        if self._current_say is not None:
            if self._current_say.finished:
                self._current_say = None
                self._current = None
            else:
                return media_frames

        while self._pending and (self._current is None or now >= self._current_deadline):
            step = self._pending.popleft()
            if self._returning_steps:
                self._returning_steps -= 1
            if isinstance(step, MovementStep):
                if step.step_mode is StepMode.SPAWN:
                    self._spawn_movement.append(_MovementTrack(step, now + step.hold))
                    continue
                self._current = step
                self._current_deadline = now + step.hold
                break
            if isinstance(step, PauseStep):
                if step.step_mode is StepMode.SPAWN:
                    continue
                self._current = step
                self._current_deadline = now + step.duration
                break
            if isinstance(step, (MediaStep, GestureStep)):
                # Both are fire-and-forget triggers: sent once this tick, never held.
                # A 0xB2 gesture must NOT be re-asserted or its motion re-runs and squeals.
                media_frames.append(step.build())
                continue
            if isinstance(step, FaceStep):
                # Setting the face is instantaneous held state: record it and let the
                # queue proceed. code 0 clears the hold so the idle face resumes.
                self._face_code = step.code or None
                self._face_frame = step.build() if step.code else None
                continue
            if isinstance(step, SayStep):
                handle = self._tts(step.text)
                if handle is None:
                    continue  # host speech tool absent: logged no-op, queue advances
                if step.step_mode is StepMode.SPAWN:
                    self._spawn_say.append(handle)
                    continue
                self._current_say = handle
                self._current = step
                break
        else:
            # queue drained or current still holding
            if self._current is not None and now >= self._current_deadline:
                self._current = None
        return media_frames

    def _compose(self) -> list[int]:
        """Last-writer-wins target from the current movement step plus spawn tracks."""
        target = [0, 0, 0, 0, 0, 0]
        if isinstance(self._current, MovementStep):
            target = _payload(self._current.build())
        for track in self._spawn_movement:
            for i, byte in enumerate(_payload(track.step.build())):
                if byte:
                    target[i] = byte
        return target

    def _guard(self, composed: list[int], now: float) -> list[int]:
        """Rate-limit joint changes and forbid compound multi-joint frames (KTD4).

        Two guarantees. First, a joint byte never changes more often than the servo-safe
        minimum, regardless of which track wrote it or how short a step's declared hold
        is — a real time floor, not a promise that rests on macro holds. A change that
        arrives too soon is suppressed (the previous value is held) until the floor
        elapses. Second, a frame never carries two non-zero joints at once (whether the
        robot even acts on a compound pose is an open protocol question); the joint
        already active in the last frame is kept for continuity and the other waits.
        """
        last = _payload(self._last_sent) if self._last_sent is not None else [0] * 6
        guarded = list(composed)
        for i in (_WAIST_INDEX, _LIMB_INDEX):
            if guarded[i] != last[i]:
                since = now - self._joint_changed_at.get(i, float("-inf"))
                if since < SAFE_HOLD:
                    guarded[i] = last[i]  # too soon: hold the previous value
        if guarded[_WAIST_INDEX] and guarded[_LIMB_INDEX]:
            if last[_LIMB_INDEX]:  # a held limb stays; the new waist lean waits
                guarded[_WAIST_INDEX] = 0
            else:  # otherwise keep the waist and defer the limb
                guarded[_LIMB_INDEX] = 0
        return guarded

    async def _emit(self, now: float, guarded: list[int], media_frames: list[bytes]) -> None:
        for media in media_frames:
            if not await self._send(media, is_target=False):
                return
            self._last_write = now  # any real frame resets the heartbeat timer (R6)
        target_frame = frame.build(0xB6, guarded)
        if target_frame != self._last_sent:
            last = _payload(self._last_sent) if self._last_sent is not None else [0] * 6
            if await self._send(target_frame, is_target=True):
                for i in (_WAIST_INDEX, _LIMB_INDEX):
                    if guarded[i] != last[i]:
                        self._joint_changed_at[i] = now
                self._last_sent = target_frame
                self._last_write = now
            return
        # Heartbeat. When a face is held, the heartbeat frame IS that face, so re-asserting
        # it on the silence floor both keeps the link warm and denies the idle routine its
        # window to repaint the LED face. A newly-set (or changed) face is asserted at once,
        # not on the next floor tick, so `carle queue face:39` shows immediately.
        heartbeat = self._face_frame if self._face_frame is not None else NOOP
        face_changed = self._face_frame is not None and self._face_frame != self._last_face_sent
        floor_passed = self._last_write is None or now - self._last_write >= self._silence_floor
        if face_changed or floor_passed:
            if await self._send(heartbeat, is_target=False):
                self._last_write = now
                self._last_face_sent = self._face_frame

    async def _send(self, payload: bytes, *, is_target: bool) -> bool:
        """Send one frame. On a dropped link, apply the per-type resume policy (KTD6)."""
        try:
            await self._conn.send_frame(payload)
            return True
        except DaemonConnectionError:
            if is_target:
                self._apply_resume_policy()
            # media/say sends are not re-fired; the caller stops emitting this tick
            return False

    def _apply_resume_policy(self) -> None:
        """Re-run pose/pause; drop an interrupted move; leave say/media alone (KTD6)."""
        current = self._current
        is_locomotion = isinstance(current, MovementStep) and bool(
            current.mode or current.speed or current.direction
        )
        if is_locomotion:
            self._current = None  # an interrupted locomotion move is dropped, not re-run
        # pose/pause: keep _current so the next tick re-sends it from its hold
