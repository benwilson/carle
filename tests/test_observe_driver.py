"""U2 — the command driver, exercised against a fake requester (no BLE, no daemon)."""

from __future__ import annotations

import pytest

from carle.observe.driver import GESTURE, POSE, WAIST, DriverError, drive_code


class FakeRequester:
    def __init__(self, reply=None):
        self.reply = reply or {"ok": True, "enqueued": 1}
        self.requests: list[dict] = []

    def __call__(self, req):
        self.requests.append(req)
        return self.reply


def test_gesture_code_issues_one_enqueue_pulse():
    fr = FakeRequester()
    drive_code(GESTURE, 1, requester=fr, daemon_live=lambda: True)
    assert fr.requests == [{"op": "enqueue", "items": [{"gesture": 1}]}]


def test_pose_and_waist_codes_use_the_wrapped_enqueue_form():
    fr = FakeRequester()
    drive_code(POSE, 5, requester=fr, daemon_live=lambda: True)
    drive_code(WAIST, 1, requester=fr, daemon_live=lambda: True)
    assert fr.requests == [
        {"op": "enqueue", "items": [{"pose": 5}]},
        {"op": "enqueue", "items": [{"waist": 1}]},
    ]


def test_no_live_daemon_raises_and_issues_no_request():
    fr = FakeRequester()
    with pytest.raises(DriverError, match="no daemon"):
        drive_code(GESTURE, 1, requester=fr, daemon_live=lambda: False)
    assert fr.requests == []


def test_unknown_family_raises():
    fr = FakeRequester()
    with pytest.raises(DriverError, match="unknown movement family"):
        drive_code("legs", 1, requester=fr, daemon_live=lambda: True)


def test_one_call_per_drive_no_stream():
    fr = FakeRequester()
    drive_code(GESTURE, 7, requester=fr, daemon_live=lambda: True)
    assert len(fr.requests) == 1  # a single pulse, never a streamed loop


# --- the variation ladder drive (KTD6) ------------------------------------------------

from carle.observe.driver import (  # noqa: E402 - grouped with the tests that use them
    REPEAT_PULSES,
    drive_for_variation,
    pre_pulse_for,
)


def _recording_requester():
    sent = []

    def requester(payload):
        sent.append(payload)
        return {"ok": True}

    return requester, sent


def test_pre_pulse_maps_lowering_codes_to_their_paired_raise():
    # Poses: the even return's pre-pose is its own odd raise; raises need none.
    assert pre_pulse_for("pose", 6) == 5
    assert pre_pulse_for("pose", 5) is None
    # Gestures: lowering PAIRS map to the preceding raise pair, mirrored on the right half.
    assert pre_pulse_for("gesture", 3) == 1
    assert pre_pulse_for("gesture", 7) == 5
    assert pre_pulse_for("gesture", 12) == 9
    assert pre_pulse_for("gesture", 15) == 13
    assert pre_pulse_for("gesture", 20) == 17
    assert pre_pulse_for("gesture", 5) is None  # a raise: nothing to pre-pose


def test_baseline_variation_is_a_single_pulse():
    requester, sent = _recording_requester()

    drive_for_variation("pose", 5, "baseline", requester=requester, daemon_live=lambda: True)

    assert sent == [{"op": "enqueue", "items": [{"pose": 5}]}]


def test_repeat_variation_pulses_three_times_with_gaps():
    requester, sent = _recording_requester()
    naps: list[float] = []

    drive_for_variation(
        "gesture", 5, "repeat", requester=requester, daemon_live=lambda: True, sleeper=naps.append
    )

    assert len(sent) == REPEAT_PULSES
    assert all(p == {"op": "enqueue", "items": [{"gesture": 5}]} for p in sent)
    assert len(naps) == REPEAT_PULSES - 1  # a servo-safe gap between pulses, none before


def test_raise_first_variation_pulses_the_paired_raise_then_the_code():
    requester, sent = _recording_requester()
    naps: list[float] = []

    drive_for_variation(
        "pose", 6, "raise_first", requester=requester, daemon_live=lambda: True, sleeper=naps.append
    )

    assert sent == [
        {"op": "enqueue", "items": [{"pose": 5}]},
        {"op": "enqueue", "items": [{"pose": 6}]},
    ]
    assert len(naps) == 1


def test_raise_first_on_a_raise_code_is_just_the_pulse():
    requester, sent = _recording_requester()

    drive_for_variation("gesture", 1, "raise_first", requester=requester, daemon_live=lambda: True)

    assert sent == [{"op": "enqueue", "items": [{"gesture": 1}]}]
