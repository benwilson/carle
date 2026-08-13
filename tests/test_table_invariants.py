"""The honesty gate.

These tests are the mechanism that keeps the command table trustworthy as it fills.
Every rule has at least one deliberately malformed fixture, and each fails on its own
rule code rather than on shared prose — so the messages can be reworded without quietly
destroying a test's ability to discriminate.

If you are changing these, read CONTRIBUTING.md first — the rules they enforce are the
point of the repository, not incidental scaffolding.
"""

from __future__ import annotations

import datetime as dt
import re
import textwrap
from pathlib import Path

import pytest
import yaml

from carle.table import (
    CATEGORIES,
    PUBLISHED_COUNTS,
    STATUSES,
    TableError,
    load_seeded_ids,
    load_table,
    validate_table,
)

VALID_ROW = {
    "id": "song_01",
    "capability": "Song 1 of 10",
    "category": "song",
    "provenance": "vendor-marketing",
    "status": "unmapped",
}

#: A frame that actually builds: family 0xB3, payload [0x03, 0x00].
FRAME_FIELDS = {"family": "0xB3", "payload": ["0x03", "0x00"]}

CONFIRMED_ROW = {
    **VALID_ROW,
    **FRAME_FIELDS,
    "provenance": "decompile",
    "status": "confirmed",
    "derivation": "CommandBuilder.playSong",
}


def write_table(tmp_path: Path, rows: list[dict], note: str = "test fixture") -> Path:
    path = tmp_path / "commands.yaml"
    path.write_text(
        yaml.safe_dump({"coverage_note": note, "commands": rows}, sort_keys=False),
        encoding="utf-8",
    )
    return path


def seeded_rows() -> list[dict]:
    """A minimal table that satisfies every published count."""
    rows: list[dict] = []
    for category, count in PUBLISHED_COUNTS.items():
        for index in range(1, count + 1):
            rows.append(
                {
                    "id": f"{category}_{index:02d}",
                    "capability": f"{category} {index}",
                    "category": category,
                    "provenance": "vendor-marketing",
                    "status": "unmapped",
                }
            )
    return rows


def problems_for(tmp_path: Path, rows: list[dict], **kwargs) -> list[str]:
    table = load_table(write_table(tmp_path, rows))
    return validate_table(table, root=tmp_path, **kwargs)


def evidence_log(
    tmp_path: Path,
    name: str = "song_01.log",
    body: str | None = None,
    *,
    entry_id: str = "song_01",
    kind: str = "send",
    frame_hex: str = "B3 02 03 00 03 AA",
    parameters: str = "",
    write: str = "ok",
) -> str:
    """Write a log the gate will actually accept, unless a test asks otherwise.

    The gate now parses this file, so a placeholder string is no longer enough — which
    is the whole point of the rule.
    """
    directory = tmp_path / "evidence"
    directory.mkdir(exist_ok=True)
    if body is None:
        body = (
            f"kind: {kind}\n"
            f"entry: {entry_id}\n"
            f"frame: {frame_hex}\n"
            f"parameters: {parameters}\n"
            "timestamp: 2026-08-11T12:00:00.000000Z\n"
            "platform: darwin\n"
            "peripheral: AA:BB:CC:DD:EE:FF\n"
            f"write: {write}\n"
            "write_detail: \n"
            "notifications: \n"
        )
    (directory / name).write_text(body, encoding="utf-8")
    return f"evidence/{name}"


def observation(
    logs: str | list[str],
    *,
    parameters: dict | None = None,
    behavior: str = "Robot plays a song",
    date: str = "2026-08-11",
    platform: str = "darwin",
    withdrawn: str | None = None,
) -> dict:
    built = {
        "parameters": parameters if parameters is not None else {},
        "behavior": behavior,
        "evidence": {
            "date": date,
            "platform": platform,
            "logs": [logs] if isinstance(logs, str) else list(logs),
        },
    }
    if withdrawn is not None:
        built["withdrawn"] = withdrawn
    return built


def good_observation(tmp_path: Path, name: str = "song_01-good.log") -> dict:
    """A valid observation to sit alongside a malformed one in every fixture.

    Every fixture below carries at least two observations deliberately. The rules are
    applied per observation now, so the specific bug to guard against is a loop that
    validates the first and returns early — with a one-observation fixture, that bug
    passes every test here while letting an entry's second through twenty-fifth claim
    say anything at all.
    """
    return observation(evidence_log(tmp_path, name=name))


#: A rule code, not the `[3]` observation index that now precedes it. Splitting on the
#: first bracket silently returned "3" for every per-observation violation, so every
#: rule below reported no code at all and the tests asserting on them could not fail.
RULE_CODE = re.compile(r"\[([a-z][a-z0-9]*(?:[.-][a-z0-9]+)+)\]")


def codes(problems: list[str]) -> set[str]:
    """Extract the bracketed rule codes so assertions do not depend on prose."""
    return {match.group(1) for problem in problems for match in RULE_CODE.finditer(problem)}


# --- The real table ---------------------------------------------------------


def test_real_table_passes_every_invariant():
    table = load_table()
    assert validate_table(table, seeded_ids=load_seeded_ids()) == []


def test_no_frame_outlives_its_provenance():
    """Replaces the earlier "no encodings exist yet" guard, which the first decompile
    retired by design. The durable rule is the one that still holds: bytes may only
    come from the app, never from a row seeded out of vendor marketing copy."""
    table = load_table()
    fabricated = [e.id for e in table.entries if e.has_frame and e.provenance != "decompile"]
    assert fabricated == []


def test_every_encoded_row_records_where_it_came_from():
    """R11 in miniature: a frame with no derivation cannot be reproduced or checked."""
    table = load_table()
    undocumented = [e.id for e in table.entries if e.has_frame and not e.derivation]
    assert undocumented == []


def test_seed_snapshot_matches_the_vendor_seeded_rows():
    """The anti-deletion guard reads its expectations from a text file in the same diff.

    Without this, a contributor could delete an inconvenient row and 'fix the test' by
    deleting the matching line from the snapshot, and every gate would stay green.
    """
    table = load_table()
    seeded = set(load_seeded_ids())
    vendor_rows = {e.id for e in table.entries if e.provenance == "vendor-marketing"}
    assert seeded == vendor_rows


def test_every_category_has_a_published_floor():
    """A category with no floor is unguarded — movement was, and rows could vanish."""
    assert set(PUBLISHED_COUNTS) == set(CATEGORIES)


# --- The migration kept what the entries already claimed (U2) ----------------
#
# Pinned here, in the test file, rather than read from the table — a proof that reads
# its expectation from the thing it is checking proves nothing.

PRE_MIGRATION_BEHAVIOR = {
    "media_story": (
        "After a noticeable silent gap of roughly ten seconds, began narrating a story: "
        "'The Princess and the Pea'. Sent at index 0 from a quiet robot. Confirms the "
        "decompiled category mapping — OtherActivity's stroy_btn writes payload byte 0x01."
    ),
    "move_rocker": (
        "Walked forward. Sent with mode at its default of 0, speed 50 and direction 3, "
        "which confirms the direction mapping derived from NormolContorlActivity "
        "(counter-clockwise from RIGHT=1, so 3 is up/forward) and shows that mode is not "
        "an enable — the app only ever writes 1 or 2 there, but 0 moves the robot."
    ),
}


@pytest.mark.parametrize("entry_id", sorted(PRE_MIGRATION_BEHAVIOR))
def test_the_migration_preserved_what_the_entry_already_claimed(entry_id):
    entry = next(e for e in load_table().entries if e.id == entry_id)
    assert entry.observations[0].behavior == PRE_MIGRATION_BEHAVIOR[entry_id]


def test_the_migrated_move_rocker_observation_keeps_its_parameters():
    entry = next(e for e in load_table().entries if e.id == "move_rocker")
    assert entry.observations[0].parameters == {"direction": 3, "speed": 50}


def test_media_music_split_kept_every_track_it_named():
    """The one behaviour became three, one per index. The split is where a caveat gets
    quietly dropped, so the substance is pinned rather than the wording."""
    entry = next(e for e in load_table().entries if e.id == "media_music")
    assert len(entry.observations) == 3
    joined = " ".join(o.behavior for o in entry.observations)
    for kept in ("Old MacDonald", "ABC song", "Merry Christmas", "CONFOUNDED", "idle"):
        assert kept in joined, kept


# --- The ingested session (U6) ----------------------------------------------


def test_move_rocker_carries_every_limb_value_that_was_watched():
    """Every value except 2, which the notebook marked as INFERRED from the 3/4 pairing
    rather than separately seen. A committed log for it exists, so an observation would
    pass every rule here and publish an inference behind an evidence link."""
    entry = next(e for e in load_table().entries if e.id == "move_rocker")
    limbs = sorted(o.parameters["limb"] for o in entry.observations if "limb" in o.parameters)
    assert limbs == [1, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]


def test_every_cited_log_is_committed_and_parses():
    from carle.evidence import read_log
    from carle.table import repo_root

    for entry in load_table().entries:
        for observation in entry.observations:
            for log in observation.logs:
                assert log.startswith("evidence/"), log
                read_log(repo_root() / log)


def test_a_sequence_derived_observation_cites_more_than_one_log():
    """The limb joints, the waist and the p5 negative were read from alternating or
    swept runs. One arbitrary member log would appear to back a behaviour it alone did
    not produce."""
    entry = next(e for e in load_table().entries if e.id == "move_rocker")
    limb_nine = next(o for o in entry.observations if o.parameters.get("limb") == 9)
    assert len(limb_nine.logs) > 1


def test_no_log_is_cited_twice_across_the_whole_table():
    seen: set[str] = set()
    for entry in load_table().entries:
        for observation in entry.observations:
            for log in observation.logs:
                assert log not in seen, log
                seen.add(log)


def test_the_withdrawn_byte_zero_reading_is_kept_with_its_reason():
    """A reader who cannot see what this document got wrong cannot calibrate it."""
    entry = next(e for e in load_table().entries if e.id == "move_rocker")
    withdrawn = [o for o in entry.observations if not o.live]
    assert len(withdrawn) == 1
    assert "turn in place" in withdrawn[0].behavior
    # Both failed readings are named: the rotation, and the leg-selector guess that
    # followed it. They were two interpretations of the same sends, so they are one
    # observation — citing the same logs twice is what KTD9 forbids.
    assert "rotating" in withdrawn[0].withdrawn
    assert "which leg leads" in withdrawn[0].withdrawn


# --- Evidence rules (AE1) ---------------------------------------------------


def test_confirmed_without_any_observation_fails(tmp_path):
    row = {**CONFIRMED_ROW}
    assert "state.confirmed-missing" in codes(problems_for(tmp_path, [row]))


def test_confirmed_whose_observations_are_all_withdrawn_fails(tmp_path):
    """AE12. A withdrawn observation does not support the status it was promoted for."""
    row = {
        **CONFIRMED_ROW,
        "observations": [
            observation(evidence_log(tmp_path, name="a.log"), withdrawn="misread the robot"),
            observation(evidence_log(tmp_path, name="b.log"), withdrawn="also misread"),
        ],
    }
    assert "state.confirmed-missing" in codes(problems_for(tmp_path, [row]))


def test_decoded_whose_observations_are_all_withdrawn_passes(tmp_path):
    """AE12, the other half. A fully-retracted finding must not force its own deletion.

    Without this, the only way to satisfy the gate after withdrawing an entry's last
    live observation would be to DELETE the withdrawn record — destroying exactly the
    history that keeping retractions visible exists to preserve.
    """
    row = {
        **VALID_ROW,
        **FRAME_FIELDS,
        "provenance": "decompile",
        "status": "decoded",
        "derivation": "CommandBuilder.playSong",
        "observations": [
            observation(evidence_log(tmp_path, name="a.log"), withdrawn="misread the robot"),
            observation(evidence_log(tmp_path, name="b.log"), withdrawn="also misread"),
        ],
    }
    assert [p for p in problems_for(tmp_path, [row]) if p.startswith("song_01")] == []


@pytest.mark.parametrize("field", ["payload", "derivation"])
def test_confirmed_missing_any_required_field_fails(tmp_path, field):
    row = {**CONFIRMED_ROW, field: None, "observations": [good_observation(tmp_path)]}
    assert "state.confirmed-missing" in codes(problems_for(tmp_path, [row]))


@pytest.mark.parametrize("field", ["payload", "derivation"])
def test_confirmed_with_an_empty_string_field_fails(tmp_path, field):
    """An empty value satisfied the old `is None` check while rendering as nothing."""
    row = {
        **CONFIRMED_ROW,
        field: [] if field == "payload" else "",
        "observations": [good_observation(tmp_path)],
    }
    assert "state.confirmed-missing" in codes(problems_for(tmp_path, [row]))


def test_decoded_carrying_a_live_observation_fails(tmp_path):
    """A live observation means the entry is confirmed, not decoded."""
    row = {
        **VALID_ROW,
        **FRAME_FIELDS,
        "provenance": "decompile",
        "status": "decoded",
        "derivation": "CommandBuilder.playSong",
        "observations": [good_observation(tmp_path)],
    }
    assert "state.status-mismatch" in codes(problems_for(tmp_path, [row]))


@pytest.mark.parametrize("field", ["payload", "derivation"])
def test_decoded_missing_a_required_field_fails(tmp_path, field):
    row = {
        **VALID_ROW,
        **FRAME_FIELDS,
        "provenance": "decompile",
        "status": "decoded",
        "derivation": "CommandBuilder.playSong",
        field: [] if field == "payload" else "",
    }
    assert "state.decoded-missing" in codes(problems_for(tmp_path, [row]))


# --- Evidence must RESOLVE, not merely exist --------------------------------
#
# These are the bypasses a reviewer demonstrated: `log: LICENSE`, `log: .`, and
# `log: /etc/hosts` all earned `status: confirmed` under a bare existence check.


def _confirmed_with_log(tmp_path: Path, log: str, **kwargs) -> list[str]:
    """A confirmed row whose SECOND observation is the one under test.

    The first is valid, so a gate that stops after the first observation reports
    nothing and every one of these tests fails loudly rather than passing by accident.
    """
    row = {
        **CONFIRMED_ROW,
        "observations": [good_observation(tmp_path), observation(log, **kwargs)],
    }
    return problems_for(tmp_path, [row])


def test_evidence_log_that_exists_in_evidence_dir_passes(tmp_path):
    log = evidence_log(tmp_path)
    assert [p for p in _confirmed_with_log(tmp_path, log) if p.startswith("song_01")] == []


def test_evidence_log_pointing_outside_evidence_dir_fails(tmp_path):
    (tmp_path / "LICENSE").write_text("MIT", encoding="utf-8")
    assert "evidence.log" in codes(_confirmed_with_log(tmp_path, "LICENSE"))


def test_evidence_log_with_an_absolute_path_fails(tmp_path):
    """`root / log` silently discards root when log is absolute."""
    assert "evidence.log" in codes(_confirmed_with_log(tmp_path, "/etc/hosts"))


def test_evidence_log_escaping_via_traversal_fails(tmp_path):
    assert "evidence.log" in codes(_confirmed_with_log(tmp_path, "evidence/../LICENSE"))


def test_evidence_log_pointing_at_a_directory_fails(tmp_path):
    (tmp_path / "evidence").mkdir()
    assert "evidence.log" in codes(_confirmed_with_log(tmp_path, "evidence"))


def test_evidence_log_pointing_at_the_readme_fails(tmp_path):
    log = evidence_log(tmp_path, name="README.md", body="explanatory text")
    assert "evidence.log" in codes(_confirmed_with_log(tmp_path, log))


def test_empty_evidence_log_fails(tmp_path):
    log = evidence_log(tmp_path, body="")
    assert "evidence.log" in codes(_confirmed_with_log(tmp_path, log))


def test_missing_evidence_log_fails(tmp_path):
    (tmp_path / "evidence").mkdir()
    assert "evidence.log" in codes(_confirmed_with_log(tmp_path, "evidence/nope.log"))


def test_evidence_date_must_be_iso(tmp_path):
    problems = _confirmed_with_log(tmp_path, evidence_log(tmp_path), date="last Tuesday")
    assert "evidence.date" in codes(problems)


def test_evidence_date_in_the_future_fails(tmp_path):
    """An observation cannot have happened tomorrow."""
    # Compute tomorrow against the same UTC clock the validator uses (table.py's
    # future check is deliberately UTC — see its comment). Using local date.today()
    # here made the test flaky in the evening west of UTC, where local tomorrow ==
    # UTC today and so is not "in the future" by the validator's clock.
    tomorrow = (dt.datetime.now(dt.timezone.utc).date() + dt.timedelta(days=1)).isoformat()
    problems = _confirmed_with_log(tmp_path, evidence_log(tmp_path), date=tomorrow)
    assert "evidence.date" in codes(problems)


def test_evidence_missing_platform_fails(tmp_path):
    problems = _confirmed_with_log(tmp_path, evidence_log(tmp_path), platform="")
    assert "observation.shape" in codes(problems)


def test_an_observation_citing_no_logs_fails(tmp_path):
    """An observation with an empty list has nothing behind it at all."""
    row = {**CONFIRMED_ROW, "observations": [good_observation(tmp_path), observation([])]}
    assert "observation.shape" in codes(problems_for(tmp_path, [row]))


# --- Multi-log observations (KTD8) and log reuse (KTD9) ---------------------


def test_an_observation_may_cite_several_logs(tmp_path):
    """Most of these findings were read from a SEQUENCE of sends, not a single one.

    Alternating two limb values on a loop, or holding one value for a minute, is what
    produced the reading. Citing one arbitrary member would make that log appear to
    back a behaviour it alone did not produce.
    """
    logs = [evidence_log(tmp_path, name=f"seq-{i}.log") for i in range(3)]
    row = {**CONFIRMED_ROW, "observations": [good_observation(tmp_path), observation(logs)]}
    assert [p for p in problems_for(tmp_path, [row]) if p.startswith("song_01")] == []


def test_one_bad_log_in_a_multi_log_observation_is_named(tmp_path):
    """Every cited log is validated, not just the first — and the message says which."""
    logs = [
        evidence_log(tmp_path, name="seq-0.log"),
        evidence_log(tmp_path, name="seq-1.log"),
        evidence_log(tmp_path, name="seq-2.log", entry_id="dance_01"),
    ]
    row = {**CONFIRMED_ROW, "observations": [good_observation(tmp_path), observation(logs)]}
    problems = problems_for(tmp_path, [row])
    assert "evidence.log-shape" in codes(problems)
    assert any("log 2" in p for p in problems), problems


def test_two_observations_citing_the_same_log_fail(tmp_path):
    """AE15. The reference publishes an observation count, which a reader takes as how
    widely the command was exercised; one send read twice would inflate it."""
    log = evidence_log(tmp_path)
    row = {**CONFIRMED_ROW, "observations": [observation(log), observation(log)]}
    assert "observation.duplicate-log" in codes(problems_for(tmp_path, [row]))


def test_a_log_reused_inside_a_multi_log_observation_is_caught(tmp_path):
    """The duplicate rule counts every cited log, not just single-log observations."""
    shared = evidence_log(tmp_path, name="shared.log")
    row = {
        **CONFIRMED_ROW,
        "observations": [
            observation([evidence_log(tmp_path, name="seq-0.log"), shared]),
            observation(shared),
        ],
    }
    assert "observation.duplicate-log" in codes(problems_for(tmp_path, [row]))


# --- State rules ------------------------------------------------------------


@pytest.mark.parametrize("status", ["unmapped", "unlocated"])
@pytest.mark.parametrize("field", ["payload", "derivation"])
def test_unearned_status_carrying_content_fails(tmp_path, status, field):
    row = {**VALID_ROW, "status": status, field: ["0x00"] if field == "payload" else "something"}
    assert "state.unearned" in codes(problems_for(tmp_path, [row]))


@pytest.mark.parametrize("status", ["unmapped", "unlocated"])
@pytest.mark.parametrize("withdrawn", [None, "misread it"])
def test_unearned_status_carrying_any_observation_fails(tmp_path, status, withdrawn):
    """Not even a withdrawn one: 'unmapped' means nobody has looked for the frame, so
    there is nothing to have watched, retracted, or otherwise."""
    row = {
        **VALID_ROW,
        "status": status,
        "observations": [observation(evidence_log(tmp_path), withdrawn=withdrawn)],
    }
    assert "state.unearned" in codes(problems_for(tmp_path, [row]))


def test_unlocated_without_content_passes(tmp_path):
    """'Searched and not found' is a legitimate resting state, distinct from 'unmapped'."""
    row = {**VALID_ROW, "status": "unlocated"}
    assert [p for p in problems_for(tmp_path, [row]) if p.startswith("song_01")] == []


def test_unknown_status_is_rejected(tmp_path):
    row = {**VALID_ROW, "status": "probably-works"}
    with pytest.raises(TableError, match="expected one of"):
        load_table(write_table(tmp_path, [row]))


def test_status_vocabulary_is_the_documented_four():
    assert STATUSES == ("unmapped", "unlocated", "decoded", "confirmed")


# --- Provenance rule --------------------------------------------------------


def test_vendor_marketing_row_cannot_carry_an_encoding(tmp_path):
    """A marketing row describes a capability; only the decompile produces an encoding."""
    row = {
        **VALID_ROW,
        **FRAME_FIELDS,
        "status": "decoded",
        "derivation": "CommandBuilder.playSong",
    }
    assert "provenance.marketing-encoding" in codes(problems_for(tmp_path, [row]))


def test_unknown_provenance_is_rejected(tmp_path):
    row = {**VALID_ROW, "provenance": "vibes"}
    with pytest.raises(TableError, match="expected one of"):
        load_table(write_table(tmp_path, [row]))


# --- Coverage rules (AE2) ---------------------------------------------------


def test_dropping_a_seeded_id_fails(tmp_path):
    rows = seeded_rows()
    dropped = rows.pop(0)
    assert "table.seeded-missing" in codes(problems_for(tmp_path, rows, seeded_ids=[dropped["id"]]))


def test_retaining_a_seeded_id_with_superseded_by_passes(tmp_path):
    """The decompile may merge rows. That is recorded, not erased."""
    rows = seeded_rows()
    rows.append({**VALID_ROW, "id": "song_all", "capability": "All songs, one opcode"})
    rows[0] = {**rows[0], "superseded_by": ["song_all"]}
    problems = problems_for(tmp_path, rows, seeded_ids=[rows[0]["id"]])
    assert codes(problems) == set()


def test_superseded_by_naming_a_missing_row_fails(tmp_path):
    """A supersede that points nowhere is a deletion with better manners."""
    rows = seeded_rows()
    rows[0] = {**rows[0], "superseded_by": ["song_that_does_not_exist"]}
    assert "table.superseded-dangling" in codes(problems_for(tmp_path, rows))


def test_a_category_below_its_published_floor_fails(tmp_path):
    rows = [r for r in seeded_rows() if r["id"] != "song_10"]
    assert "table.count-floor" in codes(problems_for(tmp_path, rows))


def test_a_category_above_its_floor_passes(tmp_path):
    """Adding a replacement row must not break the build — that is the supersede path."""
    rows = seeded_rows()
    rows.append({**VALID_ROW, "id": "song_all", "capability": "All songs, one opcode"})
    assert "table.count-floor" not in codes(problems_for(tmp_path, rows))


def test_duplicate_ids_fail(tmp_path):
    rows = seeded_rows()
    rows.append({**rows[0]})
    assert "table.duplicate-id" in codes(problems_for(tmp_path, rows))


# --- Structural rules -------------------------------------------------------


def test_unknown_field_is_rejected(tmp_path):
    row = {**VALID_ROW, "confidence": "pretty sure"}
    with pytest.raises(TableError, match="unknown field"):
        load_table(write_table(tmp_path, [row]))


def test_missing_required_field_is_rejected(tmp_path):
    row = {k: v for k, v in VALID_ROW.items() if k != "capability"}
    with pytest.raises(TableError, match="missing required field"):
        load_table(write_table(tmp_path, [row]))


def test_unknown_category_is_rejected(tmp_path):
    row = {**VALID_ROW, "category": "interpretive-dance"}
    with pytest.raises(TableError, match="expected one of"):
        load_table(write_table(tmp_path, [row]))


def test_non_mapping_row_is_rejected(tmp_path):
    with pytest.raises(TableError, match="must be a mapping"):
        load_table(write_table(tmp_path, ["just a string"]))


def test_non_list_observations_is_rejected(tmp_path):
    row = {**CONFIRMED_ROW, "observations": "I tested it"}
    with pytest.raises(TableError, match="observations must be a list"):
        load_table(write_table(tmp_path, [row]))


def test_non_mapping_observation_is_rejected(tmp_path):
    row = {**CONFIRMED_ROW, "observations": ["I tested it"]}
    with pytest.raises(TableError, match="must be a mapping"):
        load_table(write_table(tmp_path, [row]))


def test_observation_without_an_evidence_block_is_rejected(tmp_path):
    row = {**CONFIRMED_ROW, "observations": [{"parameters": {}, "behavior": "it danced"}]}
    with pytest.raises(TableError, match="'evidence' mapping"):
        load_table(write_table(tmp_path, [row]))


def test_unknown_observation_field_is_rejected(tmp_path):
    row = {**CONFIRMED_ROW, "observations": [{**observation("x"), "confidence": "high"}]}
    with pytest.raises(TableError, match="unknown field"):
        load_table(write_table(tmp_path, [row]))


def test_non_list_superseded_by_is_rejected(tmp_path):
    row = {**VALID_ROW, "superseded_by": "song_all"}
    with pytest.raises(TableError, match="superseded_by must be a list"):
        load_table(write_table(tmp_path, [row]))


def test_missing_coverage_note_is_rejected(tmp_path):
    path = tmp_path / "commands.yaml"
    path.write_text(yaml.safe_dump({"commands": [VALID_ROW]}), encoding="utf-8")
    with pytest.raises(TableError, match="coverage_note"):
        load_table(path)


def test_missing_commands_list_is_rejected(tmp_path):
    path = tmp_path / "commands.yaml"
    path.write_text(yaml.safe_dump({"coverage_note": "note"}), encoding="utf-8")
    with pytest.raises(TableError, match="'commands' list"):
        load_table(path)


def test_missing_file_is_rejected(tmp_path):
    with pytest.raises(TableError, match="does not exist"):
        load_table(tmp_path / "absent.yaml")


def test_malformed_yaml_is_rejected(tmp_path):
    path = tmp_path / "commands.yaml"
    path.write_text(
        textwrap.dedent("""\
        coverage_note: "unterminated
        commands: [
    """),
        encoding="utf-8",
    )
    with pytest.raises(TableError, match="not valid YAML"):
        load_table(path)


# --- The gate parses the log (AE9, KTD9) ------------------------------------
#
# A path-existence check let anyone hand-edit an entry to `confirmed`, point at any
# non-empty file in evidence/, and watch every CI step pass. These are the tests that
# make the rule real: none of them runs `carle confirm`.


def test_a_matching_log_passes(tmp_path):
    log = evidence_log(tmp_path)
    problems = [p for p in _confirmed_with_log(tmp_path, log) if p.startswith("song_01")]
    assert problems == []


def test_a_dry_run_log_is_not_evidence(tmp_path):
    """Dry runs never write to evidence/, but a hand-placed one must still be caught."""
    log = evidence_log(tmp_path, kind="raw")
    assert "evidence.log-shape" in codes(_confirmed_with_log(tmp_path, log))


def test_a_log_naming_another_entry_is_rejected(tmp_path):
    log = evidence_log(tmp_path, entry_id="dance_01")
    assert "evidence.log-shape" in codes(_confirmed_with_log(tmp_path, log))


def test_a_log_recording_a_different_frame_is_rejected(tmp_path):
    log = evidence_log(tmp_path, frame_hex="B3 02 01 00 01 AA")
    assert "evidence.log-shape" in codes(_confirmed_with_log(tmp_path, log))


def test_a_log_recording_a_failed_write_is_rejected(tmp_path):
    log = evidence_log(tmp_path, write="failed")
    assert "evidence.log-shape" in codes(_confirmed_with_log(tmp_path, log))


def test_a_log_whose_parameters_disagree_with_the_observation_is_rejected(tmp_path):
    """Not implied by the frame check: the two agree here and the parameters do not.

    Two parameter sets can resolve to the same bytes, so a log recorded at one must
    not be able to back a claim about another that happens to build identically.
    """
    log = evidence_log(tmp_path, parameters="index=0")
    problems = _confirmed_with_log(tmp_path, log)
    assert "evidence.log-shape" in codes(problems)
    assert any("index" in p for p in problems), problems


def test_a_withdrawn_observations_log_is_still_validated(tmp_path):
    """AE14. Withdrawal changes exactly one thing — whether the observation supports
    the status. If it also skipped log validation, `withdrawn` would be the flag that
    walks anything at all past the gate."""
    log = evidence_log(tmp_path, entry_id="dance_01")
    problems = _confirmed_with_log(tmp_path, log, withdrawn="retracted, but still checked")
    assert "evidence.log-shape" in codes(problems)


def test_a_withdrawn_observation_with_a_blank_reason_is_rejected(tmp_path):
    """A retraction with no reason tells a reader nothing about what went wrong."""
    problems = _confirmed_with_log(tmp_path, evidence_log(tmp_path), withdrawn="   ")
    assert "observation.shape" in codes(problems)


def test_a_withdrawn_and_a_live_observation_together_are_validly_confirmed(tmp_path):
    row = {
        **CONFIRMED_ROW,
        "observations": [
            observation(evidence_log(tmp_path, name="a.log"), withdrawn="misread it"),
            observation(evidence_log(tmp_path, name="b.log")),
        ],
    }
    assert [p for p in problems_for(tmp_path, [row]) if p.startswith("song_01")] == []


def test_an_unparseable_log_is_rejected(tmp_path):
    log = evidence_log(tmp_path, body="I definitely tested this, trust me\n")
    assert "evidence.log-shape" in codes(_confirmed_with_log(tmp_path, log))


# --- Frame rules ------------------------------------------------------------


def test_an_undocumented_family_is_rejected(tmp_path):
    row = {
        **VALID_ROW,
        "provenance": "decompile",
        "status": "decoded",
        "family": "0xFF",
        "payload": ["0x00"],
        "derivation": "somewhere",
    }
    assert "frame.family" in codes(problems_for(tmp_path, [row]))


def test_a_payload_referencing_an_undeclared_parameter_is_rejected(tmp_path):
    row = {
        **VALID_ROW,
        "provenance": "decompile",
        "status": "decoded",
        "family": "0xB3",
        "payload": ["0x00", "{index}"],
        "derivation": "somewhere",
    }
    assert "frame.undeclared-parameter" in codes(problems_for(tmp_path, [row]))


def test_a_declared_parameter_the_payload_ignores_is_rejected(tmp_path):
    row = {
        **VALID_ROW,
        "provenance": "decompile",
        "status": "decoded",
        "family": "0xB3",
        "payload": ["0x00"],
        "derivation": "somewhere",
        "parameters": {"index": {"min": 0, "max": 9, "default": 0}},
    }
    assert "frame.dead-parameter" in codes(problems_for(tmp_path, [row]))


def test_an_unearned_row_carrying_family_zero_is_rejected(tmp_path):
    """0x00 is falsy and legal, so a truthiness check would let it through."""
    row = {**VALID_ROW, "family": "0x00"}
    assert "state.unearned" in codes(problems_for(tmp_path, [row]))


def test_a_whitespace_only_behavior_is_rejected(tmp_path):
    """It renders as an empty cell beside a confirmed status and an evidence link."""
    problems = _confirmed_with_log(tmp_path, evidence_log(tmp_path), behavior="   ")
    assert "observation.shape" in codes(problems)


def test_a_date_disagreeing_with_the_log_is_rejected(tmp_path):
    problems = _confirmed_with_log(tmp_path, evidence_log(tmp_path), date="2020-01-01")
    assert "evidence.log-shape" in codes(problems)


def test_a_platform_disagreeing_with_the_log_is_rejected(tmp_path):
    problems = _confirmed_with_log(tmp_path, evidence_log(tmp_path), platform="win32")
    assert "evidence.log-shape" in codes(problems)


def test_a_log_with_unreadable_hex_reports_a_rule_rather_than_crashing(tmp_path):
    """One bad byte used to raise FrameError straight through the gate, abandoning
    every remaining entry's rules instead of reporting a violation."""
    log = evidence_log(tmp_path, frame_hex="ZZ 02 QQ")
    assert "evidence.log-shape" in codes(_confirmed_with_log(tmp_path, log))
