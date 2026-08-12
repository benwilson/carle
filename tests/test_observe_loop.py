"""U3 — the observation model and agreement/retry engine, driven by fake seams."""

from __future__ import annotations

from carle.observe.loop import (
    CodeResult,
    Observation,
    derive_code,
    observations_agree,
)


class FakeClip:
    def __init__(self):
        self.frames = ["f1", "f2", "f3"]
        self.cleaned = False

    def cleanup(self):
        self.cleaned = True


def _seams(observations):
    """Build drive/capture/judge seams that return the scripted observations in order."""
    clips = []
    calls = {"drive": [], "variations": []}

    def drive(family, code, variation):
        calls["drive"].append((family, code, variation))

    def capture(variation):
        calls["variations"].append(variation)
        clip = FakeClip()
        clips.append(clip)
        return clip

    seq = iter(observations)

    def judge(_frames):
        return next(seq)

    return drive, capture, judge, calls, clips


def obs(joint="left_arm", motion="raise", direction="up", confidence=0.9, code=1):
    return Observation(
        code=code, joint=joint, motion=motion, direction=direction, confidence=confidence
    )


def test_two_agreeing_readings_confirm():
    drive, capture, judge, _calls, clips = _seams([obs(), obs()])
    result = derive_code("gesture", 1, drive=drive, capture=capture, judge=judge)
    assert result.confirmed
    assert result.observation.motion == "raise"
    assert result.attempts == 2
    assert all(c.cleaned for c in clips)  # frames discarded every attempt (R7)


def test_disagree_then_a_matching_third_confirms():
    # baseline A, then B (disagree), then A again after a variation -> A reproduced.
    a = obs(motion="raise")
    b = obs(motion="lower")
    drive, capture, judge, calls, _clips = _seams([a, b, a])
    result = derive_code("gesture", 1, drive=drive, capture=capture, judge=judge, retry_limit=2)
    assert result.confirmed
    assert result.observation.motion == "raise"
    assert result.attempts == 3
    assert calls["variations"][:3] == ["baseline", "brighter", "longer"]


def test_persistent_disagreement_is_uncertain():
    reads = [obs(motion="raise"), obs(motion="lower"), obs(motion="lateral"), obs(motion="bend")]
    drive, capture, judge, _calls, _clips = _seams(reads)
    result = derive_code("gesture", 1, drive=drive, capture=capture, judge=judge, retry_limit=2)
    assert not result.confirmed
    assert result.status == "uncertain"
    assert result.observation is None


def test_low_confidence_never_confirms_and_walks_the_ladder():
    # Same reading each time but below the floor -> can never agree -> uncertain, and the
    # capture seam is asked for successive variations.
    low = [obs(confidence=0.2) for _ in range(4)]
    drive, capture, judge, calls, _clips = _seams(low)
    result = derive_code("gesture", 1, drive=drive, capture=capture, judge=judge, retry_limit=2)
    assert result.status == "uncertain"
    assert calls["variations"] == ["baseline", "brighter", "longer", "raise_first"]


def test_a_returning_gesture_still_reports_motion_not_none():
    # A gesture that animates and returns to its start: the judge reads the mid-clip motion,
    # so the confirmed observation carries a real motion, not "none".
    moving = obs(joint="left_arm", motion="bend", direction="up", confidence=0.9)
    drive, capture, judge, _calls, _clips = _seams([moving, moving])
    result = derive_code("gesture", 1, drive=drive, capture=capture, judge=judge)
    assert result.confirmed
    assert result.observation.motion == "bend"
    assert result.observation.motion != "none"


def test_every_code_resolves_within_the_limit():
    reads = [obs(motion=str(i)) for i in range(10)]  # all different, never agree
    drive, capture, judge, calls, _clips = _seams(reads)
    result = derive_code(
        "gesture", 1, drive=drive, capture=capture, judge=judge, repeats=2, retry_limit=2
    )
    assert isinstance(result, CodeResult)
    assert result.attempts == 4  # repeats + retry_limit, bounded
    assert len(calls["drive"]) == 4


def test_observations_agree_helper():
    assert observations_agree(obs(), obs())
    assert not observations_agree(obs(motion="raise"), obs(motion="lower"))
    assert not observations_agree(obs(confidence=0.2), obs(confidence=0.2))
