"""U2 — the step model and macro registry."""

from __future__ import annotations

import pytest

from carle import frame
from carle.daemon import moves
from carle.daemon.steps import (
    SAFE_HOLD,
    FaceStep,
    MediaStep,
    MovementStep,
    SayStep,
    StepMode,
    face,
    pose,
    waist,
)


def test_movement_step_builds_the_expected_0xb6_frame():
    step = MovementStep(mode=1, speed=50, direction=3, limb=0)
    assert step.build() == frame.build(0xB6, [1, 50, 3, 0, 0, 0])


def test_pose_and_waist_set_one_joint_byte():
    assert pose(5).build() == frame.build(0xB6, [0, 0, 0, 0, 5, 0])
    assert waist(1).build() == frame.build(0xB6, [0, 0, 0, 1, 0, 0])


def test_media_step_builds_the_expected_0xb3_frame():
    assert MediaStep(sub=3, index=2).build() == frame.build(0xB3, [3, 2])


def test_wave_and_fist_pump_expand_to_their_documented_sequences():
    wave = moves.expand("wave")
    # Left shoulder sweep: 5 up / 6 down, three times.
    assert [s.limb for s in wave] == [5, 6, 5, 6, 5, 6]

    pump = moves.expand("fist_pump")
    # Raise the arm (1), bob the elbow (9/10) three times, lower (2).
    assert [s.limb for s in pump] == [1, 9, 10, 9, 10, 9, 10, 2]


def test_sway_expands_to_waist_lean_and_return():
    sway = moves.expand("sway")
    assert [s.waist for s in sway] == [1, 2, 1, 2, 1, 2]


def test_every_macro_step_changes_at_most_one_joint_and_holds_safely():
    for name in moves.move_names():
        steps = moves.expand(name)
        assert moves.is_servo_safe(steps), f"{name} drives two joints in one step"
        for step in steps:
            assert step.hold >= SAFE_HOLD, f"{name} has a sub-safe hold"


def test_unknown_macro_name_raises():
    with pytest.raises(ValueError, match="unknown move"):
        moves.expand("moonwalk")


def test_move_names_lists_the_registry():
    assert moves.move_names() == ["fist_pump", "sway", "wave"]


def test_say_and_media_carry_their_step_mode():
    assert SayStep(text="hi").step_mode is StepMode.SPAWN
    assert SayStep(text="hi", step_mode=StepMode.AWAIT).step_mode is StepMode.AWAIT
    assert MediaStep(sub=3).step_mode is StepMode.SPAWN


def test_face_step_builds_the_expected_0xb2_frame():
    # An LED expression is one 0xB2 action code (39-48 are the five faces).
    assert FaceStep(code=39).build() == frame.build(0xB2, [39])
    assert face(47).build() == frame.build(0xB2, [47])
    assert face(39).step_mode is StepMode.SPAWN  # setting the face never blocks the queue


def test_protocol_parses_a_face_item():
    from carle.daemon.protocol import parse_steps

    steps = parse_steps([{"face": 39}, {"face": 0}])
    assert [type(s) for s in steps] == [FaceStep, FaceStep]
    assert [s.code for s in steps] == [39, 0]
