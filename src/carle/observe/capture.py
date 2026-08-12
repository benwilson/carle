"""Webcam capture for the observe loop (U1).

Records a short clip from a webcam (camera 0 by default) with ffmpeg and extracts
brightened, evenly-sampled frames into a scratch dir. The subprocess runner is injected,
so no test shells out or opens a camera. Frames are ephemeral: the caller runs `cleanup`
once it has judged them, and nothing is retained (R7).
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

#: Defaults for a capture. Camera 0 is the MacBook Pro Camera used in the hardware session.
DEFAULT_DEVICE = "0"
DEFAULT_DURATION = 3.0
DEFAULT_FRAMES = 6

#: Bound on the ffmpeg call so a hung camera cannot stall the loop.
CAPTURE_TIMEOUT = 30.0

#: Brightness/contrast lift for dim captures (the office lighting in the prior session was low).
BRIGHTEN = "eq=brightness=0.28:contrast=1.5:saturation=1.3"


class CaptureError(Exception):
    """Raised when a clip cannot be recorded or no frames come out of it."""


#: A runner takes an ffmpeg argv and a timeout and returns (returncode, stderr_text).
#: It may raise CaptureError directly (missing binary, timeout). The default shells out;
#: tests pass a fake that writes frame files instead.
Runner = Callable[[Sequence[str], float], tuple[int, str]]


@dataclass
class CaptureResult:
    """The frames sampled from one clip, plus their ephemeral scratch dir."""

    scratch_dir: Path
    frames: list[Path]

    def cleanup(self) -> None:
        """Delete the scratch dir and its frames. Safe to call more than once (R7)."""
        shutil.rmtree(self.scratch_dir, ignore_errors=True)


def _default_runner(argv: Sequence[str], timeout: float) -> tuple[int, str]:
    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv built here, no shell
            list(argv), capture_output=True, text=True, timeout=timeout, check=False
        )
    except FileNotFoundError as exc:
        raise CaptureError(f"ffmpeg not found on PATH: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise CaptureError(f"capture timed out after {timeout:.0f}s") from exc
    return proc.returncode, proc.stderr or ""


def build_argv(device: str, duration: float, frames: int, out_pattern: str) -> list[str]:
    """The ffmpeg avfoundation invocation: video-only, brightened, `frames` sampled evenly."""
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "avfoundation",
        "-pixel_format",
        "uyvy422",
        "-framerate",
        "30",
        "-i",
        device,
        "-an",  # video only: never block trying to open an audio device
        "-t",
        f"{duration}",
        "-vf",
        f"{BRIGHTEN},fps={frames}/{duration}",
        "-frames:v",
        str(frames),
        "-y",
        out_pattern,
    ]


def capture_frames(
    *,
    device: str = DEFAULT_DEVICE,
    duration: float = DEFAULT_DURATION,
    frames: int = DEFAULT_FRAMES,
    scratch_dir: str | Path | None = None,
    runner: Runner = _default_runner,
    timeout: float = CAPTURE_TIMEOUT,
) -> CaptureResult:
    """Record a clip and return its sampled frames. Raises CaptureError on any failure."""
    scratch = Path(scratch_dir) if scratch_dir else Path(tempfile.mkdtemp(prefix="carle-observe-"))
    scratch.mkdir(parents=True, exist_ok=True)
    out_pattern = str(scratch / "frame_%03d.jpg")
    code, stderr = runner(build_argv(device, duration, frames, out_pattern), timeout)
    if code != 0:
        raise CaptureError(f"ffmpeg exited {code}: {stderr.strip()[:200]}")
    paths = sorted(scratch.glob("frame_*.jpg"))
    if not paths:
        raise CaptureError("capture produced no frames")
    return CaptureResult(scratch_dir=scratch, frames=paths)
