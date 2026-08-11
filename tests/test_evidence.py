"""Send logs must be written and read by the same code, or the gate and the CLI drift.

The gate parses these files to decide whether a `confirmed` entry earned its status, so
a log that half-parses is worse than one that fails outright.
"""

from __future__ import annotations

import datetime as dt

import pytest

from carle import evidence

FRAME = bytes([0xB3, 0x02, 0x03, 0x00, 0x03, 0xAA])
WHEN = dt.datetime(2026, 8, 11, 12, 0, 0, 123456)


def make(**overrides) -> evidence.SendLog:
    fields = dict(
        kind=evidence.KIND_SEND,
        frame=FRAME,
        timestamp=WHEN,
        platform="darwin",
        peripheral="AA:BB:CC:DD:EE:FF",
        write_ok=True,
        entry_id="media_music",
        parameters={"index": 0},
    )
    fields.update(overrides)
    return evidence.SendLog(**fields)


def test_a_log_round_trips(tmp_path):
    path = evidence.write_log(make(), tmp_path)
    read = evidence.read_log(path)

    assert read.frame == FRAME
    assert read.entry_id == "media_music"
    assert read.parameters == {"index": 0}
    assert read.platform == "darwin"
    assert read.write_ok is True


def test_notifications_round_trip(tmp_path):
    path = evidence.write_log(make(notifications=[b"\x01\x02", b"\x03"]), tmp_path)
    assert evidence.read_log(path).notifications == [b"\x01\x02", b"\x03"]


def test_filenames_are_windows_legal(tmp_path):
    """The CI matrix includes Windows, where a colon cannot appear in a filename."""
    path = evidence.write_log(make(), tmp_path)
    assert ":" not in path.name


def test_two_sends_in_the_same_second_get_distinct_names(tmp_path):
    first = evidence.write_log(make(timestamp=WHEN), tmp_path)
    second = evidence.write_log(make(timestamp=WHEN.replace(microsecond=999999)), tmp_path)
    assert first.name != second.name


def test_writing_over_an_existing_log_is_refused(tmp_path):
    evidence.write_log(make(), tmp_path)
    with pytest.raises(evidence.EvidenceError, match="refusing to overwrite"):
        evidence.write_log(make(), tmp_path)


def test_a_raw_log_is_not_promotable(tmp_path):
    log = make(kind=evidence.KIND_RAW, entry_id=None)
    assert log.promotable is False


def test_a_failed_write_is_not_promotable():
    assert make(write_ok=False).promotable is False


def test_a_real_successful_send_is_promotable():
    assert make().promotable is True


def test_an_unsafe_entry_id_is_refused(tmp_path):
    """A separator in the id would write the log outside the directory entirely."""
    with pytest.raises(evidence.EvidenceError, match="filename-safe"):
        evidence.write_log(make(entry_id="../../etc/passwd"), tmp_path)


def test_a_malformed_log_is_rejected_rather_than_half_read(tmp_path):
    path = tmp_path / "media_music-20260811T120000000000Z.log"
    path.write_text("I definitely tested this\n", encoding="utf-8")
    with pytest.raises(evidence.EvidenceError):
        evidence.read_log(path)


def test_a_log_missing_a_required_field_is_rejected(tmp_path):
    path = tmp_path / "x.log"
    path.write_text("kind: send\nentry: media_music\n", encoding="utf-8")
    with pytest.raises(evidence.EvidenceError, match="missing"):
        evidence.read_log(path)


def test_a_bad_timestamp_is_rejected(tmp_path):
    path = tmp_path / "x.log"
    path.write_text(
        "kind: send\nentry: a\nframe: B3 00 00 AA\nparameters: \n"
        "timestamp: last Tuesday\nplatform: darwin\nwrite: ok\n",
        encoding="utf-8",
    )
    with pytest.raises(evidence.EvidenceError, match="ISO 8601"):
        evidence.read_log(path)


def test_latest_promotable_picks_the_most_recent(tmp_path):
    evidence.write_log(make(timestamp=WHEN), tmp_path)
    evidence.write_log(make(timestamp=WHEN.replace(hour=15), parameters={"index": 5}), tmp_path)
    latest = evidence.latest_promotable("media_music", tmp_path)
    assert latest is not None
    assert latest.parameters == {"index": 5}


def test_latest_promotable_skips_raw_and_failed_logs(tmp_path):
    evidence.write_log(make(kind=evidence.KIND_RAW, entry_id="media_music"), tmp_path)
    assert evidence.latest_promotable("media_music", tmp_path) is None


def test_latest_promotable_is_none_when_the_directory_is_empty(tmp_path):
    assert evidence.latest_promotable("media_music", tmp_path) is None


def test_the_date_is_the_calendar_date(tmp_path):
    """hardware_evidence.date is validated with date.fromisoformat, which takes nothing else."""
    assert make().date.isoformat() == "2026-08-11"
