"""U5 — the `carle observe` CLI surface, with injected fakes (no camera, robot, or daemon)."""

from __future__ import annotations

from carle.cli import main
from carle.observe.capture import CaptureError
from carle.observe.loop import CodeResult, Observation


def _connected(_req):
    return {"ok": True, "status": {"connected": True, "battery": None}}


def _disconnected(_req):
    return {"ok": True, "status": {"connected": False, "battery": None}}


def test_dry_run_lists_the_default_arm_set_and_touches_no_hardware(capsys):
    code = main(["observe", "--dry-run"], daemon_live=lambda: False)
    out = capsys.readouterr().out
    assert code == 0
    # 12 poses + 24 gestures = 36 codes; dry-run never checks the daemon.
    assert "36 codes" in out
    assert "pose:1" in out and "gesture:24" in out


def test_codes_flag_overrides_the_set(capsys):
    code = main(["observe", "--dry-run", "--codes", "gesture:5,pose:7"], daemon_live=lambda: False)
    out = capsys.readouterr().out
    assert code == 0
    assert "2 codes" in out
    assert "gesture:5" in out and "pose:7" in out


def test_down_daemon_errors_and_reports_zero(capsys):
    code = main(
        ["observe"],
        daemon_live=lambda: False,
        observe_derive=lambda f, c: None,
        observe_record=lambda r: None,
    )
    assert code == 1
    assert "no daemon" in capsys.readouterr().err


def test_disconnected_robot_halts_before_driving(capsys):
    driven = {"n": 0}

    def derive(_f, _c):
        driven["n"] += 1
        return None

    code = main(
        ["observe"],
        daemon_live=lambda: True,
        requester=_disconnected,
        observe_derive=derive,
        observe_record=lambda r: None,
    )
    assert code == 1
    assert "not connected" in capsys.readouterr().err
    assert driven["n"] == 0  # never drove a dead robot (R11)


def test_capture_error_midrun_stops_and_reports_count(capsys):
    calls = {"n": 0}

    def derive(family, code):
        calls["n"] += 1
        if calls["n"] == 2:
            raise CaptureError("camera went dark")
        return CodeResult(family, code, "confirmed", Observation(code, "left_arm", "raise"), 2)

    code = main(
        ["observe", "--codes", "gesture:1,gesture:2,gesture:3"],
        daemon_live=lambda: True,
        requester=_connected,
        observe_derive=derive,
        observe_record=lambda r: None,
    )
    assert code == 1
    err = capsys.readouterr().err
    assert "stopped after 1 codes" in err and "camera went dark" in err


def test_full_run_records_every_code(capsys):
    recorded = []

    def derive(family, code):
        return CodeResult(family, code, "confirmed", Observation(code, "left_arm", "raise"), 2)

    code = main(
        ["observe", "--codes", "gesture:1,gesture:2"],
        daemon_live=lambda: True,
        requester=_connected,
        observe_derive=derive,
        observe_record=recorded.append,
    )
    assert code == 0
    assert "observed 2 codes" in capsys.readouterr().out
    assert len(recorded) == 2
