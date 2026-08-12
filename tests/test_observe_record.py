"""U4 — the findings recorder, exercised with a fake reference writer."""

from __future__ import annotations

import pytest

from carle.observe.loop import CodeResult, Observation
from carle.observe.record import record_result


class FakeWriter:
    """Records what the recorder asked it to apply to the reference."""

    def __init__(self, raises: bool = False):
        self.raises = raises
        self.confirmed: list[tuple] = []
        self.uncertain: list[CodeResult] = []

    def __call__(self, result, prior=None):
        if self.raises:
            raise RuntimeError("writer blew up")
        if result.confirmed:
            self.confirmed.append((result, prior))
        else:
            self.uncertain.append(result)


def _confirmed(motion="raise", code=1):
    obs = Observation(code=code, joint="left_arm", motion=motion, direction="up", confidence=0.9)
    return CodeResult("gesture", code, "confirmed", obs, 2)


def _uncertain(code=1):
    return CodeResult("gesture", code, "uncertain", None, 4)


def test_confirmed_result_applies_the_finding_once():
    w = FakeWriter()
    record_result(_confirmed(), writer=w)
    assert len(w.confirmed) == 1
    assert w.confirmed[0][0].observation.motion == "raise"
    assert w.uncertain == []


def test_uncertain_result_records_a_note_and_no_confirmed_write():
    w = FakeWriter()
    record_result(_uncertain(), writer=w)
    assert w.uncertain and w.uncertain[0].status == "uncertain"
    assert w.confirmed == []


def test_confirmed_result_differing_from_prior_requests_an_overwrite():
    w = FakeWriter()
    prior = Observation(code=1, joint="left_arm", motion="lower", direction="down", confidence=0.9)
    record_result(_confirmed(motion="raise"), writer=w, prior=prior)
    result, seen_prior = w.confirmed[0]
    assert seen_prior is prior  # the writer is handed the prior entry to overwrite
    assert result.observation.motion != prior.motion


def test_frames_are_discarded_even_when_the_writer_raises():
    cleaned = {"done": False}

    def cleanup():
        cleaned["done"] = True

    with pytest.raises(RuntimeError):
        record_result(_confirmed(), writer=FakeWriter(raises=True), cleanup=cleanup)
    assert cleaned["done"] is True  # ephemeral cleanup ran despite the failure (R7)
