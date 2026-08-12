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


def test_cleanup_removes_the_scratch_dir_and_is_idempotent(tmp_path):
    sub = tmp_path / "clip"
    result = capture_frames(scratch_dir=sub, frames=2, runner=_runner_that_writes(2))
    assert sub.exists()
    result.cleanup()
    assert not sub.exists()
    result.cleanup()  # second call must not raise
