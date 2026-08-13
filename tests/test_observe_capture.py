"""U1 — webcam capture, exercised entirely against a fake ffmpeg runner (no camera)."""

from __future__ import annotations

from pathlib import Path

import pytest

from carle.observe.capture import CaptureError, build_argv, capture_frames


def test_build_argv_carries_device_duration_and_frame_count():
    argv = build_argv("0", 3.0, 6, "/tmp/x/frame_%03d.jpg")
    assert argv[0] == "ffmpeg"
    assert "avfoundation" in argv
    assert "-an" in argv  # video-only
    assert "0" in argv  # device
    assert "-frames:v" in argv and argv[argv.index("-frames:v") + 1] == "6"
    assert argv[-1] == "/tmp/x/frame_%03d.jpg"


def _runner_that_writes(n: int):
    """A fake runner that creates `n` frame files where the argv's out-pattern points."""

    def runner(argv, _timeout):
        pattern = Path(argv[-1])
        for i in range(1, n + 1):
            (pattern.parent / f"frame_{i:03d}.jpg").write_bytes(b"jpeg")
        return 0, ""

    return runner


def test_capture_returns_the_sampled_frames(tmp_path):
    result = capture_frames(scratch_dir=tmp_path, frames=6, runner=_runner_that_writes(6))
    assert len(result.frames) == 6
    assert all(p.exists() for p in result.frames)


def test_nonzero_ffmpeg_exit_raises_capture_error(tmp_path):
    def runner(_argv, _timeout):
        return 1, "Selected pixel format not supported"

    with pytest.raises(CaptureError, match="exited 1"):
        capture_frames(scratch_dir=tmp_path, runner=runner)


def test_no_frames_produced_raises_capture_error(tmp_path):
    with pytest.raises(CaptureError, match="no frames"):
        capture_frames(scratch_dir=tmp_path, runner=lambda _a, _t: (0, ""))


def test_runner_failure_propagates_as_capture_error(tmp_path):
    # The default runner raises CaptureError on a timeout / missing binary; a runner that
    # raises one must propagate unchanged rather than be swallowed.
    def runner(_argv, _timeout):
        raise CaptureError("capture timed out after 30s")

    with pytest.raises(CaptureError, match="timed out"):
        capture_frames(scratch_dir=tmp_path, runner=runner)


def test_failure_removes_a_temp_scratch_dir_it_created(monkeypatch):
    # With scratch_dir=None capture creates its own temp dir. A failure before a
    # CaptureResult exists (so cleanup() is never callable) must not orphan it.
    import tempfile

    created: list[Path] = []
    real_mkdtemp = tempfile.mkdtemp

    def tracking_mkdtemp(*args, **kwargs):
        made = real_mkdtemp(*args, **kwargs)
        created.append(Path(made))
        return made

    monkeypatch.setattr("carle.observe.capture.tempfile.mkdtemp", tracking_mkdtemp)

    with pytest.raises(CaptureError, match="exited 1"):
        capture_frames(runner=lambda _a, _t: (1, "boom"))

    assert created and not created[0].exists()  # the temp dir was cleaned up, not orphaned


def test_failure_leaves_a_caller_supplied_scratch_dir_in_place(tmp_path):
    # The caller owns a scratch_dir it passed in, so a failure must not delete it.
    sub = tmp_path / "mine"
    with pytest.raises(CaptureError):
        capture_frames(scratch_dir=sub, runner=lambda _a, _t: (1, "boom"))
    assert sub.exists()


def test_cleanup_removes_the_scratch_dir_and_is_idempotent(tmp_path):
    sub = tmp_path / "clip"
    result = capture_frames(scratch_dir=sub, frames=2, runner=_runner_that_writes(2))
    assert sub.exists()
    result.cleanup()
    assert not sub.exists()
    result.cleanup()  # second call must not raise


# --- the variation ladder capture (KTD6) ----------------------------------------------

from carle.observe.capture import (  # noqa: E402 - grouped with the tests that use them
    CAPTURE_SPECS,
    DEFAULT_CROP,
    MOTION_THRESHOLD,
    MotionRecording,
    build_extract_argv,
    build_record_argv,
    build_sample_argv,
    spec_for,
)


def test_variation_specs_cover_every_ladder_rung():
    from carle.observe.loop import DEFAULT_VARIATIONS

    for rung in DEFAULT_VARIATIONS:
        assert rung in CAPTURE_SPECS
    assert CAPTURE_SPECS["brighter"].brightness > CAPTURE_SPECS["baseline"].brightness
    assert CAPTURE_SPECS["longer"].duration > CAPTURE_SPECS["baseline"].duration
    # repeat records longer than baseline so all three pulses land inside the clip
    assert CAPTURE_SPECS["repeat"].duration > CAPTURE_SPECS["baseline"].duration
    assert spec_for("no-such-rung") == CAPTURE_SPECS["baseline"]


def test_record_argv_is_full_rate_and_extract_argv_crops_the_motion_detection():
    spec = spec_for("baseline")
    record = build_record_argv("0", spec, "/tmp/clip.mp4")
    assert "fps=" not in " ".join(record)  # full rate: no sampling at record time
    assert f"-t" in record and f"{spec.duration}" in record

    extract = build_extract_argv("/tmp/clip.mp4", spec, "/tmp/f_%03d.jpg")
    vf = extract[extract.index("-vf") + 1]
    w, h, x, y = DEFAULT_CROP
    assert f"crop=iw*{w}:ih*{h}:iw*{x}:ih*{y}" in vf  # detection confined to the robot
    assert f"gt(scene,{MOTION_THRESHOLD})" in vf


class _FakeFfmpeg:
    """Routes record/extract/sample argvs: writes a video, then motion frames (or none)."""

    def __init__(self, motion_frames: int) -> None:
        self.motion_frames = motion_frames
        self.calls: list[str] = []

    def __call__(self, argv, _timeout):
        argv = list(argv)
        out = argv[-1]
        if out.endswith(".mp4"):
            self.calls.append("record")
            Path(out).write_bytes(b"video")
            return 0, ""
        vf = argv[argv.index("-vf") + 1]
        if "select" in vf:
            self.calls.append("extract")
            for i in range(self.motion_frames):
                Path(out % (i + 1)).write_bytes(b"jpg")
            return 0, ""
        self.calls.append("sample")
        for i in range(3):
            Path(out % (i + 1)).write_bytes(b"jpg")
        return 0, ""


def test_motion_recording_extracts_the_frames_that_moved(tmp_path):
    ffmpeg = _FakeFfmpeg(motion_frames=4)
    recording = MotionRecording("baseline", scratch_dir=tmp_path, runner=ffmpeg)

    result = recording.finish()

    assert ffmpeg.calls == ["record", "extract"]
    assert len(result.frames) == 4


def test_motion_recording_falls_back_to_sampling_when_nothing_moved(tmp_path):
    # No motion above threshold is a VALID outcome (the code may do nothing visible): the
    # judge must get evenly sampled frames to read the stillness, not a capture error.
    ffmpeg = _FakeFfmpeg(motion_frames=0)
    recording = MotionRecording("baseline", scratch_dir=tmp_path, runner=ffmpeg)

    result = recording.finish()

    assert ffmpeg.calls == ["record", "extract", "sample"]
    assert len(result.frames) == 3


def test_motion_recording_surfaces_a_failed_recording(tmp_path):
    def broken(argv, _timeout):
        return 1, "avfoundation: device busy"

    recording = MotionRecording("baseline", scratch_dir=tmp_path, runner=broken)

    with pytest.raises(CaptureError):
        recording.finish()
