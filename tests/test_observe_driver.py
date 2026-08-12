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
