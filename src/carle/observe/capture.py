"""Webcam capture for the observe loop (U1).

Records a short clip from a webcam (camera 0 by default) with ffmpeg and extracts
brightened frames into a scratch dir. The subprocess runner is injected, so no test
shells out or opens a camera. Frames are ephemeral: the caller runs `cleanup` once it has
judged them, and nothing is retained (R7).

Two capture styles:

- `capture_frames` — the original one-shot: record briefly, sample frames evenly. Fine
  for a held pose; **provably misses a canned gesture animation** (a live run sampled at
  1 fps caught nothing of one twice in a row).
- `MotionRecording` — the variation-ladder capture (KTD6): record at the camera's full
  rate while the code fires mid-recording, then extract the frames where something
  actually changed, with the scene detection **cropped to the robot's region** (an
  animated background — a projector on a whiteboard, on the live run — false-triggers a
  whole-frame detector). A recording with no motion above threshold falls back to evenly
  sampled frames, so the judge sees a legible "still at rest" rather than a capture
  error: no visible motion is itself a valid reading.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import threading
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
    """Record a clip and return its sampled frames. Raises CaptureError on any failure.

    A failure before a `CaptureResult` exists (a nonzero ffmpeg exit, an empty burst, a
    timeout or missing binary from the runner) still removes the scratch dir we created, so
    a flaky ffmpeg over a long autonomous run does not orphan a `carle-observe-*` temp dir
    per failure. A caller-supplied `scratch_dir` is left alone — the caller owns it.
    """
    owns_scratch = scratch_dir is None
    scratch = Path(scratch_dir) if scratch_dir else Path(tempfile.mkdtemp(prefix="carle-observe-"))
    scratch.mkdir(parents=True, exist_ok=True)
    out_pattern = str(scratch / "frame_%03d.jpg")
    try:
        code, stderr = runner(build_argv(device, duration, frames, out_pattern), timeout)
        if code != 0:
            raise CaptureError(f"ffmpeg exited {code}: {stderr.strip()[:200]}")
        paths = sorted(scratch.glob("frame_*.jpg"))
        if not paths:
            raise CaptureError("capture produced no frames")
    except BaseException:
        if owns_scratch:
            shutil.rmtree(scratch, ignore_errors=True)
        raise
    return CaptureResult(scratch_dir=scratch, frames=paths)


# --- variation-ladder capture (KTD6) --------------------------------------------------

#: The camera region the motion detector watches, as ffmpeg crop fractions
#: (width, height, x-offset, y-offset of the kept region). The default keeps the central
#: lower portion of a portrait frame — where the sweep setup centers the robot — and
#: excludes the background above/behind it.
DEFAULT_CROP = (0.7, 0.55, 0.15, 0.35)

#: Scene-change fraction that counts as robot motion inside the cropped region. Tuned on
#: the live 2026-08-13 run: 0.0015-0.002 caught a shallow single-arm excursion.
MOTION_THRESHOLD = 0.0015

#: Seconds finish() waits beyond the recording duration before declaring the recorder hung.
FINISH_GRACE = 15.0


@dataclass(frozen=True)
class CaptureSpec:
    """One rung's capture parameters: how long, how bright, how many frames out."""

    duration: float
    brightness: float
    frames: int


#: Capture parameters per variation rung (loop.DEFAULT_VARIATIONS). The drive-side rungs
#: (`raise_first`, `repeat`) capture like the baseline — their difference lives in the
#: drive seam — except `repeat` records longer so all three pulses land inside the clip.
CAPTURE_SPECS: dict[str, CaptureSpec] = {
    "baseline": CaptureSpec(duration=8.0, brightness=0.28, frames=6),
    "brighter": CaptureSpec(duration=8.0, brightness=0.45, frames=6),
    "longer": CaptureSpec(duration=14.0, brightness=0.28, frames=8),
    "raise_first": CaptureSpec(duration=10.0, brightness=0.28, frames=6),
    "repeat": CaptureSpec(duration=12.0, brightness=0.28, frames=8),
}


def spec_for(variation: str) -> CaptureSpec:
    """The capture spec for a ladder rung; unknown rungs capture like the baseline."""
    return CAPTURE_SPECS.get(variation, CAPTURE_SPECS["baseline"])


def build_record_argv(device: str, spec: CaptureSpec, video_path: str) -> list[str]:
    """Record the camera at full rate to a video file — no frame sampling at this stage."""
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
        "-an",
        "-t",
        f"{spec.duration}",
        "-vf",
        f"eq=brightness={spec.brightness}:contrast=1.5:saturation=1.3",
        "-y",
        video_path,
    ]


def build_extract_argv(
    video_path: str,
    spec: CaptureSpec,
    out_pattern: str,
    *,
    crop: tuple[float, float, float, float] = DEFAULT_CROP,
    threshold: float = MOTION_THRESHOLD,
) -> list[str]:
    """Pull the frames where the robot's region changed — motion, not wallpaper.

    The crop confines scene detection to the robot; the select filter keeps only frames
    whose change against the previous kept frame clears `threshold`.
    """
    w, h, x, y = crop
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        video_path,
        "-vf",
        f"crop=iw*{w}:ih*{h}:iw*{x}:ih*{y},select='gt(scene,{threshold})'",
        "-vsync",
        "vfr",
        "-frames:v",
        str(spec.frames),
        "-y",
        out_pattern,
    ]


def build_sample_argv(video_path: str, spec: CaptureSpec, out_pattern: str) -> list[str]:
    """Evenly sample frames from the recording — the no-motion fallback."""
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        video_path,
        "-vf",
        f"fps={spec.frames}/{spec.duration}",
        "-frames:v",
        str(spec.frames),
        "-y",
        out_pattern,
    ]


class MotionRecording:
    """A two-phase capture: the camera records in the background while the code fires.

    `loop.derive_code` calls the drive seam and then the capture seam, an order that
    cannot express "the recording must already be rolling when a transient gesture
    fires". This object is how the composed seams express it anyway: the drive seam
    starts a `MotionRecording` before pulsing the code, and the capture seam calls
    `finish()`, which waits the recording out and extracts the motion frames (falling
    back to even sampling when nothing moved).
    """

    def __init__(
        self,
        variation: str,
        *,
        device: str = DEFAULT_DEVICE,
        scratch_dir: str | Path | None = None,
        runner: Runner = _default_runner,
        crop: tuple[float, float, float, float] = DEFAULT_CROP,
        threshold: float = MOTION_THRESHOLD,
    ) -> None:
        self._spec = spec_for(variation)
        self._runner = runner
        self._device = str(device)
        self._crop = crop
        self._threshold = threshold
        self._owns_scratch = scratch_dir is None
        self._scratch = (
            Path(scratch_dir) if scratch_dir else Path(tempfile.mkdtemp(prefix="carle-observe-"))
        )
        self._scratch.mkdir(parents=True, exist_ok=True)
        self._video = str(self._scratch / "clip.mp4")
        self._result: tuple[int, str] | None = None
        self._error: BaseException | None = None
        self._thread = threading.Thread(target=self._record, daemon=True)
        self._thread.start()

    def _record(self) -> None:
        try:
            self._result = self._runner(
                build_record_argv(self._device, self._spec, self._video),
                self._spec.duration + CAPTURE_TIMEOUT,
            )
        except BaseException as exc:  # noqa: BLE001 - surfaced from finish() on the caller
            self._error = exc

    def finish(self) -> CaptureResult:
        """Wait the recording out, then extract motion frames (or the sampled fallback)."""
        self._thread.join(self._spec.duration + FINISH_GRACE)
        try:
            if self._thread.is_alive():
                raise CaptureError("recording did not finish in time")
            if self._error is not None:
                if isinstance(self._error, CaptureError):
                    raise self._error
                raise CaptureError(f"recording failed: {self._error}") from self._error
            code, stderr = self._result
            if code != 0:
                raise CaptureError(f"ffmpeg exited {code}: {stderr.strip()[:200]}")
            out_pattern = str(self._scratch / "frame_%03d.jpg")
            code, stderr = self._runner(
                build_extract_argv(
                    self._video,
                    self._spec,
                    out_pattern,
                    crop=self._crop,
                    threshold=self._threshold,
                ),
                CAPTURE_TIMEOUT,
            )
            if code != 0:
                raise CaptureError(f"motion extraction exited {code}: {stderr.strip()[:200]}")
            paths = sorted(self._scratch.glob("frame_*.jpg"))
            if not paths:
                # Nothing moved in the robot's region: sample evenly so the judge can see
                # (and report) the stillness rather than the run erroring out.
                code, stderr = self._runner(
                    build_sample_argv(self._video, self._spec, out_pattern), CAPTURE_TIMEOUT
                )
                if code != 0:
                    raise CaptureError(f"fallback sampling exited {code}: {stderr.strip()[:200]}")
                paths = sorted(self._scratch.glob("frame_*.jpg"))
            if not paths:
                raise CaptureError("capture produced no frames")
        except BaseException:
            if self._owns_scratch:
                shutil.rmtree(self._scratch, ignore_errors=True)
            raise
        return CaptureResult(scratch_dir=self._scratch, frames=paths)
