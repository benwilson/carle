"""U3 — the queue engine, driven by a fake clock, connection, and TTS.

Every scenario is deterministic: the clock is a value the test advances between ticks,
the connection records frames and can simulate a drop, and TTS is a fake that records
calls and can be told a subprocess is absent or finished. No real time passes and no
Bluetooth is touched.
"""

from __future__ import annotations

import asyncio

from carle import frame
from carle.daemon import moves
from carle.daemon.connection import DaemonConnectionError
from carle.daemon.engine import NOOP, Engine
from carle.daemon.steps import MediaStep, SayStep, StepMode, face, gesture, pose, travel, waist


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


class FakeConn:
    def __init__(self, battery: int | None = 80) -> None:
        self.is_connected = True
        self.sent: list[bytes] = []
        self.drop_next = False
        self._battery = battery

    async def send_frame(self, payload: bytes) -> None:
        if self.drop_next:
            self.drop_next = False
            self.is_connected = False
            raise DaemonConnectionError("dropped")
        self.sent.append(payload)

    async def read_battery(self) -> int | None:
        return self._battery


class FakeHandle:
    def __init__(self) -> None:
        self.terminated = False
        self.finished = False

    def terminate(self) -> None:
        self.terminated = True


class FakeTts:
    def __init__(self, available: bool = True) -> None:
        self.available = available
        self.calls: list[str] = []
        self.handles: list[FakeHandle] = []

    def __call__(self, text: str):
        self.calls.append(text)
        if not self.available:
            return None
        handle = FakeHandle()
        self.handles.append(handle)
        return handle


def make_engine(silence_floor: float = 1.0, tts: FakeTts | None = None):
    clock = Clock()
    conn = FakeConn()
    engine = Engine(conn, clock=clock.now, tts=tts or FakeTts(), silence_floor=silence_floor)
    return engine, conn, clock


def limbs(sent: list[bytes]) -> list[int]:
    return [frame.parse(f)[1][4] for f in sent]


def test_heartbeat_fires_after_the_silence_floor():
    async def scenario():
        engine, conn, clock = make_engine(silence_floor=1.0)
        await engine.tick()  # t=0: establishes the idle baseline (a NOOP)
        baseline = len(conn.sent)
        for _ in range(9):  # t=0.1 .. 0.9: nothing changes, floor not reached
            clock.advance(0.1)
            await engine.tick()
        assert len(conn.sent) == baseline
        clock.advance(0.2)  # past the floor (avoids float-accumulation equality)
        await engine.tick()
        assert len(conn.sent) == baseline + 1
        assert conn.sent[-1] == NOOP

    asyncio.run(scenario())


def test_a_held_face_is_reasserted_as_the_heartbeat():
    # A face is held display state: once set, the heartbeat re-asserts that 0xB2 frame
    # (never a bare NOOP) so the idle routine cannot repaint the LED face between frames.
    async def scenario():
        engine, conn, clock = make_engine(silence_floor=1.0)
        face_frame = frame.build(0xB2, [39])
        engine.enqueue([face(39)])
        await engine.tick()  # t=0: the baseline movement NOOP goes out first
        clock.advance(0.1)
        await engine.tick()  # t=0.1: a newly-set face is asserted at once, not on the floor
        assert conn.sent[-1] == face_frame
        assert engine.status()["face"] == 39
        clock.advance(1.1)  # cross the silence floor
        await engine.tick()
        assert conn.sent[-1] == face_frame  # the heartbeat re-asserted the face, not a NOOP

    asyncio.run(scenario())


def test_face_clear_and_stop_return_the_heartbeat_to_noop():
    async def scenario():
        engine, conn, clock = make_engine(silence_floor=1.0)
        engine.enqueue([face(39)])
        await engine.tick()
        clock.advance(0.1)
        await engine.tick()  # face held
        assert engine.status()["face"] == 39

        engine.enqueue([face(0)])  # 0 clears the hold
        clock.advance(0.1)
        await engine.tick()
        assert engine.status()["face"] is None
        clock.advance(1.1)
        await engine.tick()  # past the floor: heartbeat is a bare NOOP again
        assert conn.sent[-1] == NOOP

        engine.enqueue([face(41)])  # set another, then abort
        clock.advance(0.1)
        await engine.tick()
        engine.stop()
        assert engine.status()["face"] is None  # stop drops the held face too

    asyncio.run(scenario())


def test_a_gesture_fires_once_and_is_never_reasserted():
    # A 0xB2 gesture must be pulsed a single time — re-asserting it re-runs the motion and
    # squeals the servos. So the frame appears exactly once, then never again, even as the
    # heartbeat keeps ticking (a NOOP, not the gesture).
    async def scenario():
        engine, conn, clock = make_engine(silence_floor=1.0)
        gesture_frame = frame.build(0xB2, [1])
        engine.enqueue([gesture(1)])
        for _ in range(30):  # three seconds of ticks
            await engine.tick()
            clock.advance(0.1)
        assert conn.sent.count(gesture_frame) == 1  # fired once, never re-asserted
        assert engine.status()["face"] is None  # a gesture is not held state

    asyncio.run(scenario())


def test_clear_lets_the_current_step_finish_and_drops_pending():
    async def scenario():
        engine, conn, clock = make_engine()
        engine.enqueue([pose(1, hold=0.5)])
        await engine.tick()  # sends limb=1
        engine.enqueue([pose(3, hold=0.5)])
        engine.clear()  # drop the queued pose(3); the in-flight pose(1) still holds
        for _ in range(8):
            clock.advance(0.1)
            await engine.tick()
        assert 1 in limbs(conn.sent)
        assert 3 not in limbs(conn.sent)  # the cleared step never ran

    asyncio.run(scenario())


def test_stop_returns_a_raised_joint_to_neutral():
    async def scenario():
        engine, conn, clock = make_engine()
        engine.enqueue([pose(1, hold=5.0)])
        await engine.tick()  # holds limb=1
        assert limbs(conn.sent)[-1] == 1
        engine.stop()
        # The neutral return is itself a joint change, so it honors the servo floor: the
        # reversal to limb=2 lands only once ~0.5s has passed since limb=1 was set.
        clock.advance(0.6)
        await engine.tick()  # emits the return (limb=2)
        assert limbs(conn.sent)[-1] == 2
        clock.advance(0.6)
        await engine.tick()  # then all-zero
        assert limbs(conn.sent)[-1] == 0

    asyncio.run(scenario())


def test_two_tracks_on_one_joint_resolve_last_writer_wins():
    async def scenario():
        engine, conn, clock = make_engine()
        # A spawned pose(5) track plus a main pose(6); both drive the limb byte.
        engine.enqueue([pose(5, hold=5.0, step_mode=StepMode.SPAWN), pose(6, hold=5.0)])
        await engine.tick()
        # One frame, one joint; the spawn track is applied last, so it wins.
        assert limbs(conn.sent)[-1] == 5

    asyncio.run(scenario())


def test_cross_track_frame_never_changes_two_joints_at_once():
    async def scenario():
        engine, conn, clock = make_engine()
        # A spawned limb pose plus a main waist lean: composed target has both joints,
        # but the KTD4 guard emits at most one joint change per frame.
        engine.enqueue([pose(1, hold=5.0, step_mode=StepMode.SPAWN), waist(1, hold=5.0)])
        for _ in range(4):
            await engine.tick()
            clock.advance(0.1)
        for f in conn.sent:
            payload = frame.parse(f)[1]
            active_joints = sum(1 for v in (payload[3], payload[4]) if v)
            assert active_joints <= 1, "a frame drove two joints at once"

    asyncio.run(scenario())


def test_a_joint_never_changes_faster_than_the_safe_hold():
    async def scenario():
        engine, conn, clock = make_engine()
        engine.enqueue(moves.expand("wave"))  # pose(5)/pose(6) at 0.5s holds
        change_times: list[float] = []
        last_limb = None
        for _ in range(40):  # 4 seconds at 0.1s ticks
            await engine.tick()
            cur = limbs(conn.sent)[-1] if conn.sent else 0
            if cur != last_limb:
                change_times.append(clock.now())
                last_limb = cur
            clock.advance(0.1)
        deltas = [b - a for a, b in zip(change_times, change_times[1:], strict=False)]
        assert all(d >= 0.5 - 1e-9 for d in deltas), f"limb changed too fast: {deltas}"

    asyncio.run(scenario())


def test_short_holds_cannot_beat_the_servo_floor():
    # The rate limit is a real time floor, not a promise resting on macro holds: even
    # when steps declare 0.1s holds, the emitted limb byte never flips faster than 0.5s.
    async def scenario():
        engine, conn, clock = make_engine()
        engine.enqueue([pose(5, hold=0.1), pose(6, hold=0.1), pose(5, hold=0.1), pose(6, hold=0.1)])
        change_times: list[float] = []
        last_limb = None
        for _ in range(40):
            await engine.tick()
            cur = limbs(conn.sent)[-1] if conn.sent else 0
            if cur != last_limb:
                change_times.append(clock.now())
                last_limb = cur
            clock.advance(0.1)
        deltas = [b - a for a, b in zip(change_times, change_times[1:], strict=False)]
        assert all(d >= 0.5 - 1e-9 for d in deltas), f"floor breached: {deltas}"

    asyncio.run(scenario())


def test_spawned_say_lets_movement_continue_and_heartbeat_fires():
    async def scenario():
        tts = FakeTts()
        engine, conn, clock = make_engine(silence_floor=1.0, tts=tts)
        engine.enqueue([SayStep(text="hello", step_mode=StepMode.SPAWN), pose(1, hold=5.0)])
        await engine.tick()
        assert tts.calls == ["hello"]  # speech started
        assert limbs(conn.sent)[-1] == 1  # and movement ran
        before = len(conn.sent)
        clock.advance(1.0)
        await engine.tick()
        assert len(conn.sent) > before  # heartbeat still fires while speech plays

    asyncio.run(scenario())


def test_stop_terminates_a_backgrounded_say():
    async def scenario():
        tts = FakeTts()
        engine, conn, clock = make_engine(tts=tts)
        engine.enqueue([SayStep(text="hi", step_mode=StepMode.SPAWN)])
        await engine.tick()
        engine.stop()
        assert tts.handles[0].terminated

    asyncio.run(scenario())


def test_say_degrades_to_a_noop_when_no_speech_tool():
    async def scenario():
        tts = FakeTts(available=False)
        engine, conn, clock = make_engine(tts=tts)
        engine.enqueue([SayStep(text="hi", step_mode=StepMode.AWAIT), pose(1)])
        await engine.tick()  # say is a no-op; the queue advances to the pose
        assert tts.calls == ["hi"]
        assert limbs(conn.sent)[-1] == 1  # queue did not stall on the absent tool

    asyncio.run(scenario())


def test_reconnect_reruns_a_pose_but_drops_a_move():
    async def scenario():
        # pose: interrupted send re-runs from its hold.
        engine, conn, clock = make_engine()
        engine.enqueue([pose(1, hold=5.0)])
        conn.drop_next = True
        await engine.tick()  # send raises; policy keeps the pose current
        assert 1 not in limbs(conn.sent)
        conn.is_connected = True  # reconnect
        clock.advance(0.1)
        await engine.tick()
        assert limbs(conn.sent)[-1] == 1  # pose re-ran

        # move: interrupted locomotion is dropped, not re-run.
        engine2, conn2, clock2 = make_engine()
        engine2.enqueue([travel(direction=3, speed=50, hold=5.0)])
        conn2.drop_next = True
        await engine2.tick()  # send raises; policy drops the move
        conn2.is_connected = True
        clock2.advance(0.1)
        await engine2.tick()
        directions = [frame.parse(f)[1][2] for f in conn2.sent]
        assert 3 not in directions  # the move was not replayed

    asyncio.run(scenario())


def test_media_that_fails_mid_send_is_not_refired():
    async def scenario():
        engine, conn, clock = make_engine()
        engine.enqueue([MediaStep(sub=3, index=0)])
        conn.drop_next = True
        await engine.tick()  # media send fails
        conn.is_connected = True
        clock.advance(0.1)
        await engine.tick()
        media = [f for f in conn.sent if frame.parse(f)[0] == 0xB3]
        assert media == []  # never re-fired

    asyncio.run(scenario())


def test_status_reports_connection_current_and_pending():
    async def scenario():
        engine, conn, clock = make_engine()
        engine.enqueue([pose(1, hold=5.0), pose(3), pose(5)])
        await engine.tick()
        status = engine.status()
        assert status["connected"] is True
        assert status["current"] == "MovementStep"
        assert status["pending"] == 2
        assert await engine.battery() == 80

    asyncio.run(scenario())


def test_macro_enqueue_expands_and_executes():
    async def scenario():
        engine, conn, clock = make_engine()
        engine.enqueue(moves.expand("wave"))
        seen: list[int] = []
        for _ in range(40):
            await engine.tick()
            cur = limbs(conn.sent)[-1] if conn.sent else 0
            if not seen or seen[-1] != cur:
                seen.append(cur)
            clock.advance(0.1)
        # The wave's 5/6 sweep appears in order among the emitted limb values.
        assert 5 in seen and 6 in seen
        assert seen.index(5) < seen.index(6)

    asyncio.run(scenario())


# --- link-outage pause/resume (hardware finding 2026-08-13) ---------------------------


def test_queue_pauses_while_disconnected_and_resumes_after_reconnect():
    # On hardware, a mid-queue power cycle burned the whole queue into the dead link
    # (writes are without-response): steps "ran" while disconnected and nothing remained
    # to resume. The queue must wait out the outage instead.
    async def scenario():
        engine, conn, clock = make_engine()
        engine.enqueue([pose(1, hold=1.0), pose(3, hold=1.0)])
        await engine.tick()  # t=0: pose 1 becomes current and is sent
        assert limbs(conn.sent)[-1] == 1
        sent_before = len(conn.sent)

        conn.is_connected = False  # the transport notices the drop; no send has failed
        for _ in range(30):  # a 3 s outage
            clock.advance(0.1)
            await engine.tick()
        assert len(conn.sent) == sent_before  # nothing executed into the dead link
        assert engine.status()["pending"] == 1  # pose 3 still queued, not burned

        conn.is_connected = True
        clock.advance(0.1)
        await engine.tick()
        # The outage shifted the hold deadline: pose 1 is still current — not expired by
        # wall clock — and is re-sent because the robot may have rebooted mid-outage.
        assert engine.status()["current"] == "MovementStep"
        assert limbs(conn.sent)[-1] == 1

    asyncio.run(scenario())


def test_pausing_on_a_noticed_drop_nudges_the_reconnect_loop():
    # Only a FAILED SEND used to schedule the background reconnect. A drop noticed via
    # is_connected while the engine is quiet must nudge the reconnect loop itself.
    async def scenario():
        class NudgingConn(FakeConn):
            def __init__(self) -> None:
                super().__init__()
                self.nudges = 0

            def ensure_reconnect(self) -> None:
                self.nudges += 1

        clock = Clock()
        conn = NudgingConn()
        engine = Engine(conn, clock=clock.now, tts=FakeTts())
        conn.is_connected = False
        await engine.tick()
        assert conn.nudges == 1
        clock.advance(0.1)
        await engine.tick()  # staying paused does not re-nudge; the loop retries itself
        assert conn.nudges == 1

    asyncio.run(scenario())


def test_held_face_is_reasserted_after_an_outage():
    # A power cycle resets the robot's display while the daemon still "holds" a face.
    # After reconnect the held face must be re-sent, not deduplicated against state the
    # robot lost.
    async def scenario():
        engine, conn, clock = make_engine()
        face_frame = frame.build(0xB2, [39])
        engine.enqueue([face(39)])
        await engine.tick()
        clock.advance(0.1)
        await engine.tick()
        assert face_frame in conn.sent

        conn.is_connected = False
        clock.advance(1.0)
        await engine.tick()  # paused
        conn.is_connected = True
        clock.advance(0.1)
        await engine.tick()  # re-asserts the movement target first
        sent_after_reconnect = len(conn.sent)
        clock.advance(0.1)
        await engine.tick()  # then the held face goes out again at once
        assert conn.sent[sent_after_reconnect:].count(face_frame) == 1

    asyncio.run(scenario())


# --- converging stop (hardware finding 2026-08-13: the stuck-arm truncation) ----------


def test_a_second_stop_does_not_truncate_the_neutral_walk():
    # Repeated stops during a held pose left the arm physically extended on hardware: a
    # later stop cleared the in-flight return command mid-servo-travel. A stop during a
    # stop's walk must let the walk finish.
    async def scenario():
        engine, conn, clock = make_engine()
        engine.enqueue([pose(5, hold=10.0)])
        await engine.tick()
        assert limbs(conn.sent)[-1] == 5

        engine.stop()
        clock.advance(0.6)
        await engine.tick()  # the walk's limb-6 return becomes current and is sent
        assert limbs(conn.sent)[-1] == 6
        engine.stop()  # a second stop mid-walk
        assert engine.status()["current"] == "MovementStep"  # return hold NOT truncated

        for _ in range(30):
            clock.advance(0.2)
            await engine.tick()
        movement = [f for f in conn.sent if frame.parse(f)[0] == 0xB6]
        assert limbs(movement)[-1] == 0  # the walk ended at the all-zero target
        gestures = [f for f in conn.sent if frame.parse(f)[0] == 0xB2]
        assert len(gestures) == 1  # one bilateral arms-down reset — not one per stop

    asyncio.run(scenario())


def test_stop_when_idle_still_resets_the_body():
    # On hardware the daemon's picture of the joints desynced from the robot ("doing
    # nothing" while an arm stood extended), and stop no-opped off _last_sent. A stop must
    # bring the body home even when the daemon believes there is nothing to undo.
    async def scenario():
        engine, conn, clock = make_engine()
        await engine.tick()  # idle baseline
        engine.stop()
        for _ in range(10):  # walk through the all-zero hold to the gesture
            clock.advance(0.2)
            await engine.tick()
        arms_down = [f for f in conn.sent if frame.parse(f)[0] == 0xB2]
        assert len(arms_down) == 1

    asyncio.run(scenario())


def test_stop_drops_steps_queued_behind_an_active_walk():
    async def scenario():
        engine, conn, clock = make_engine()
        engine.enqueue([pose(5, hold=10.0)])
        await engine.tick()
        engine.stop()
        clock.advance(0.6)
        await engine.tick()  # walk underway
        engine.enqueue([pose(1, hold=1.0)])  # queued behind the walk
        engine.stop()  # keeps the walk, drops the pose queued after it
        for _ in range(30):
            clock.advance(0.2)
            await engine.tick()
        movement = [f for f in conn.sent if frame.parse(f)[0] == 0xB6]
        assert 1 not in limbs(movement)

    asyncio.run(scenario())
