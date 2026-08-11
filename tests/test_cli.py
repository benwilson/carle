"""CLI behavior, proven against a fake Bluetooth backend.

No robot is reachable from CI or from a development machine, so these tests exercise the
CLI's logic rather than Bleak itself. That boundary is deliberate and it is also the
limit of what they prove: the first session with real hardware is the genuine integration
test for transport.
"""

from __future__ import annotations

import pytest

from carle.cli import _check_macos_authorization, main
from carle.transport import (
    _MACOS_AUTHORIZATION,
    DEFAULT_SCAN_TIMEOUT,
    Characteristic,
    Peripheral,
    SendResult,
    Service,
    TransportError,
    describe_identity,
    filter_robots,
    macos_authorization,
)


class FakeBackend:
    def __init__(
        self, peripherals=None, services=None, error: Exception | None = None, notifications=None
    ):
        self._peripherals = peripherals or []
        self._services = services or []
        self._error = error
        #: Recorded so tests can prove --timeout actually reaches the transport.
        self.timeouts: list[float] = []
        self.sent: list[bytes] = []
        self._notifications = notifications or []

    async def discover(self, timeout: float):
        self.timeouts.append(timeout)
        if self._error:
            raise self._error
        return list(self._peripherals)

    async def services(self, address: str, timeout: float):
        self.timeouts.append(timeout)
        if self._error:
            raise self._error
        return list(self._services)

    async def send(self, address: str, payload: bytes, timeout: float = 8.0):
        self.timeouts.append(timeout)
        self.sent.append(payload)
        if self._error:
            raise self._error
        return SendResult(ok=True, detail="fake", notifications=list(self._notifications))


ROBOT = Peripheral(address="AA:BB:CC:DD:EE:FF", name="JT_1234", rssi=-52)
OTHER = Peripheral(address="11:22:33:44:55:66", name="Other Device", rssi=-70)


# --- scan -------------------------------------------------------------------


def test_scan_lists_only_the_robot(capsys):
    exit_code = main(["scan"], backend=FakeBackend([ROBOT, OTHER]))
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "JT_1234" in out
    assert "Other Device" not in out


def test_scan_all_lists_every_peripheral(capsys):
    main(["scan", "--all"], backend=FakeBackend([ROBOT, OTHER]))
    out = capsys.readouterr().out

    assert "JT_1234" in out
    assert "Other Device" in out


def test_empty_scan_exits_zero_with_a_diagnostic(capsys):
    """An empty scan is the normal case with no robot present, not a failure.

    Permission guidance is deliberately absent here — that case is caught earlier by the
    authorization pre-check, because on macOS it never reaches this code path at all.
    """
    exit_code = main(["scan"], backend=FakeBackend([]))
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "JT_" in out
    assert "scan --all" in out


def test_scan_reports_adapter_failure_as_an_error(capsys):
    exit_code = main(["scan"], backend=FakeBackend(error=TransportError("no adapter")))

    assert exit_code == 1
    assert "no adapter" in capsys.readouterr().err


def test_unnamed_peripheral_does_not_crash_scan(capsys):
    main(["scan", "--all"], backend=FakeBackend([Peripheral(address="00:11", name=None)]))
    assert "(unnamed)" in capsys.readouterr().out


# --- connect ----------------------------------------------------------------


def test_connect_reports_success(capsys):
    exit_code = main(["connect", "AA:BB"], backend=FakeBackend(services=[Service(uuid="180a")]))

    assert exit_code == 0
    assert "Connected to" in capsys.readouterr().out


def test_connect_timeout_exits_non_zero(capsys):
    backend = FakeBackend(error=TransportError("could not connect to AA:BB: timeout"))
    exit_code = main(["connect", "AA:BB"], backend=backend)

    assert exit_code == 1
    assert "timeout" in capsys.readouterr().err


# --- info -------------------------------------------------------------------


def test_info_prints_every_uuid_verbatim(capsys):
    services = [
        Service(
            uuid="0000ffe0-0000-1000-8000-00805f9b34fb",
            description="Vendor specific",
            characteristics=[
                Characteristic(
                    uuid="0000ffe1-0000-1000-8000-00805f9b34fb",
                    description="Write channel",
                    properties=("write-without-response", "notify"),
                )
            ],
        )
    ]
    exit_code = main(["info", "AA:BB"], backend=FakeBackend(services=services))
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "0000ffe0-0000-1000-8000-00805f9b34fb" in out
    assert "0000ffe1-0000-1000-8000-00805f9b34fb" in out
    assert "write-without-response" in out


def test_info_handles_a_peripheral_with_no_services(capsys):
    exit_code = main(["info", "AA:BB"], backend=FakeBackend(services=[]))

    assert exit_code == 0
    assert "no GATT services" in capsys.readouterr().out


# --- platform-dependent identity (KTD5) -------------------------------------


def test_macos_identity_is_labeled_as_a_system_uuid():
    rendered = describe_identity("1A2B3C4D-0000-0000-0000-000000000000", platform="darwin")
    assert "system-assigned UUID" in rendered
    assert "Bluetooth address" not in rendered.replace("not a Bluetooth address", "")


def test_linux_identity_is_labeled_as_a_mac():
    assert "MAC address" in describe_identity("AA:BB:CC:DD:EE:FF", platform="linux")


def test_windows_identity_is_labeled_for_windows():
    assert "Windows" in describe_identity("AA:BB:CC:DD:EE:FF", platform="win32")


def test_unknown_platform_still_names_itself():
    assert "freebsd" in describe_identity("AA:BB", platform="freebsd")


# --- no write path ----------------------------------------------------------


def test_send_requires_an_entry_or_raw():
    """Replaces the old guard asserting `send` must not exist. The frame format is now
    documented, so the command is legitimate — but it still refuses to guess."""
    assert main(["send"], backend=FakeBackend(), authorization=None) == 1


# --- filtering --------------------------------------------------------------


def test_filter_robots_matches_on_the_jt_prefix():
    assert filter_robots([ROBOT, OTHER]) == [ROBOT]


# --- macOS Bluetooth authorization ------------------------------------------
#
# Observed on a real macOS 15 machine: a process that touches CoreBluetooth without
# authorization is killed by the OS with SIGABRT and prints nothing at all. Python
# cannot catch that, so the only way to explain it is to check before scanning.


def test_denied_authorization_blocks_the_scan(capsys):
    assert _check_macos_authorization("denied") == 1
    assert "Privacy & Security" in capsys.readouterr().err


def test_restricted_authorization_blocks_the_scan(capsys):
    assert _check_macos_authorization("restricted") == 1
    assert "Privacy & Security" in capsys.readouterr().err


def test_undetermined_authorization_warns_but_proceeds(capsys):
    """The warning has to print before the scan, because the abort leaves no output."""
    assert _check_macos_authorization("not-determined") is None
    assert "terminated it" in capsys.readouterr().err


def test_allowed_authorization_is_silent(capsys):
    assert _check_macos_authorization("allowed") is None
    assert capsys.readouterr().err == ""


def test_non_macos_authorization_is_silent(capsys):
    assert _check_macos_authorization(None) is None
    assert capsys.readouterr().err == ""


def test_authorization_probe_returns_none_off_macos():
    assert macos_authorization(platform="linux") is None
    assert macos_authorization(platform="win32") is None


def test_authorization_enum_matches_apple_values():
    """CBManagerAuthorization. Swapping denied and allowed left the whole suite green,
    which would tell an unauthorized process it may proceed — the exact SIGABRT case."""
    assert _MACOS_AUTHORIZATION == {
        0: "not-determined",
        1: "restricted",
        2: "denied",
        3: "allowed",
    }


@pytest.mark.parametrize("command", [["scan"], ["connect", "AA:BB"], ["info", "AA:BB"]])
def test_denied_authorization_blocks_every_command(capsys, command):
    """connect and info build a BleakClient, which initializes a central manager exactly
    as a scan does. Guarding only scan left them dying silently."""
    exit_code = main(command, backend=FakeBackend(), authorization="denied")

    assert exit_code == 1
    assert "Privacy & Security" in capsys.readouterr().err


# --- --timeout reaches the transport ----------------------------------------


@pytest.mark.parametrize("command", [["scan"], ["connect", "AA:BB"], ["info", "AA:BB"]])
def test_timeout_is_forwarded_to_the_backend(command):
    """Hardcoding the timeout in the CLI left every test passing, while on real hardware
    it would turn every scan and connect into an instant failure."""
    backend = FakeBackend(services=[Service(uuid="180a")])
    main([*command, "--timeout", "2.5"], backend=backend, authorization=None)

    assert backend.timeouts == [2.5]


def test_default_timeout_is_forwarded(command=None):
    backend = FakeBackend()
    main(["scan"], backend=backend, authorization=None)

    assert backend.timeouts == [DEFAULT_SCAN_TIMEOUT]


# --- send (U4) --------------------------------------------------------------


def test_send_transmits_the_documented_frame(tmp_path):
    backend = FakeBackend()
    code = main(
        ["send", "media_music", "--address", "AA:BB", "--evidence-dir", str(tmp_path)],
        backend=backend,
        authorization=None,
    )
    assert code == 0
    assert backend.sent == [bytes([0xB3, 0x02, 0x03, 0x00, 0x03, 0xAA])]


def test_a_parameter_override_changes_the_frame(tmp_path):
    backend = FakeBackend()
    main(
        [
            "send",
            "media_music",
            "--param",
            "index=3",
            "--address",
            "AA:BB",
            "--evidence-dir",
            str(tmp_path),
        ],
        backend=backend,
        authorization=None,
    )
    assert backend.sent == [bytes([0xB3, 0x02, 0x03, 0x03, 0x06, 0xAA])]


def test_send_writes_a_log(tmp_path):
    main(
        ["send", "media_music", "--address", "AA:BB", "--evidence-dir", str(tmp_path)],
        backend=FakeBackend(),
        authorization=None,
    )
    logs = list(tmp_path.glob("media_music-*.log"))
    assert len(logs) == 1


def test_a_dry_run_writes_no_log_and_never_connects(tmp_path, capsys):
    backend = FakeBackend()
    code = main(
        ["send", "media_music", "--dry-run", "--evidence-dir", str(tmp_path)],
        backend=backend,
        authorization=None,
    )
    assert code == 0
    assert backend.sent == []
    assert list(tmp_path.glob("*.log")) == []
    assert "B3 02 03 00 03 AA" in capsys.readouterr().out


def test_a_dry_run_works_with_bluetooth_denied(tmp_path, capsys):
    """It touches no radio, so the authorization guard must not block it."""
    code = main(
        ["send", "media_music", "--dry-run", "--evidence-dir", str(tmp_path)],
        backend=None,
        authorization="denied",
    )
    assert code == 0
    assert "B3 02 03 00 03 AA" in capsys.readouterr().out


def test_send_refuses_an_entry_with_no_frame(tmp_path, capsys):
    code = main(
        ["send", "song_01", "--address", "AA:BB", "--evidence-dir", str(tmp_path)],
        backend=FakeBackend(),
        authorization=None,
    )
    assert code == 1
    assert "unlocated" in capsys.readouterr().err


def test_send_refuses_an_unknown_id(tmp_path, capsys):
    code = main(
        ["send", "no_such_command", "--dry-run", "--evidence-dir", str(tmp_path)],
        backend=FakeBackend(),
        authorization=None,
    )
    assert code == 1
    assert "no_such_command" in capsys.readouterr().err


def test_send_refuses_an_out_of_range_parameter(tmp_path, capsys):
    backend = FakeBackend()
    code = main(
        [
            "send",
            "volume_set",
            "--param",
            "level=9",
            "--address",
            "AA:BB",
            "--evidence-dir",
            str(tmp_path),
        ],
        backend=backend,
        authorization=None,
    )
    assert code == 1
    assert backend.sent == []
    assert "0-2" in capsys.readouterr().err


def test_send_requires_an_address_when_not_dry_running(tmp_path, capsys):
    code = main(
        ["send", "media_music", "--evidence-dir", str(tmp_path)],
        backend=FakeBackend(),
        authorization=None,
    )
    assert code == 1
    assert "--address" in capsys.readouterr().err


def test_raw_bypasses_the_table(tmp_path):
    backend = FakeBackend()
    main(
        [
            "send",
            "--raw",
            "01 02",
            "--family",
            "0xB6",
            "--address",
            "AA:BB",
            "--evidence-dir",
            str(tmp_path),
        ],
        backend=backend,
        authorization=None,
    )
    assert backend.sent == [bytes([0xB6, 0x02, 0x01, 0x02, 0x03, 0xAA])]


def test_raw_needs_a_family(tmp_path, capsys):
    code = main(
        ["send", "--raw", "01 02", "--dry-run", "--evidence-dir", str(tmp_path)],
        backend=FakeBackend(),
        authorization=None,
    )
    assert code == 1
    assert "--family" in capsys.readouterr().err


def test_a_transport_failure_exits_non_zero(tmp_path, capsys):
    backend = FakeBackend(error=TransportError("robot is asleep"))
    code = main(
        ["send", "media_music", "--address", "AA:BB", "--evidence-dir", str(tmp_path)],
        backend=backend,
        authorization=None,
    )
    assert code == 1
    assert "asleep" in capsys.readouterr().err


def test_notifications_are_reported_and_logged(tmp_path, capsys):
    backend = FakeBackend(notifications=[b"\xaa\xbb"])
    main(
        ["send", "media_music", "--address", "AA:BB", "--evidence-dir", str(tmp_path)],
        backend=backend,
        authorization=None,
    )
    assert "AA BB" in capsys.readouterr().out


# --- confirm (U6) -----------------------------------------------------------


@pytest.fixture
def workspace(tmp_path):
    """A throwaway copy of the real table plus an evidence directory."""
    import shutil

    from carle.table import default_table_path

    table = tmp_path / "commands.yaml"
    shutil.copy(default_table_path(), table)
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    return table, evidence_dir


def send_then(workspace, *extra):
    table, evidence_dir = workspace
    main(
        [
            "send",
            "media_music",
            "--address",
            "AA:BB",
            *extra,
            "--evidence-dir",
            str(evidence_dir),
            "--table",
            str(table),
        ],
        backend=FakeBackend(),
        authorization=None,
    )


def confirm(workspace, *extra, behavior="It played a song"):
    table, evidence_dir = workspace
    return main(
        [
            "confirm",
            "media_music",
            "--behavior",
            behavior,
            *extra,
            "--evidence-dir",
            str(evidence_dir),
            "--table",
            str(table),
        ],
        authorization=None,
    )


def status_of(table, entry_id="media_music"):
    from carle.table import load_table as load

    return next(e for e in load(table).entries if e.id == entry_id).status


def test_a_send_then_confirm_promotes_the_entry(workspace):
    table, _ = workspace
    send_then(workspace)
    assert confirm(workspace) == 0
    assert status_of(table) == "confirmed"


def test_the_promoted_entry_passes_the_invariant_suite(workspace):
    from carle.table import load_table as load
    from carle.table import validate_table

    table, evidence_dir = workspace
    send_then(workspace)
    confirm(workspace)
    problems = [
        p
        for p in validate_table(load(table), root=evidence_dir.parent)
        if p.startswith("media_music")
    ]
    assert problems == []


def test_the_promotion_records_the_parameters_that_were_sent(workspace):
    from carle.table import load_table as load

    table, _ = workspace
    send_then(workspace, "--param", "index=3")
    confirm(workspace)
    entry = next(e for e in load(table).entries if e.id == "media_music")
    assert entry.observed_parameters == {"index": 3}


def test_confirm_refuses_without_a_log(workspace, capsys):
    table, _ = workspace
    assert confirm(workspace) == 1
    assert status_of(table) == "decoded"
    assert "no send log" in capsys.readouterr().err


def test_confirm_refuses_a_raw_log(workspace, capsys):
    table, evidence_dir = workspace
    main(
        [
            "send",
            "--raw",
            "03 00",
            "--family",
            "0xB3",
            "--address",
            "AA:BB",
            "--evidence-dir",
            str(evidence_dir),
            "--table",
            str(table),
        ],
        backend=FakeBackend(),
        authorization=None,
    )
    assert confirm(workspace) == 1
    assert status_of(table) == "decoded"


def test_confirm_refuses_when_the_entry_changed_after_the_observation(workspace, capsys):
    import yaml

    table, _ = workspace
    send_then(workspace)
    raw = yaml.safe_load(table.read_text("utf-8"))
    for row in raw["commands"]:
        if row["id"] == "media_music":
            row["payload"] = ["0x09", "{index}"]
    table.write_text(yaml.safe_dump(raw, sort_keys=False), "utf-8")

    assert confirm(workspace) == 1
    assert "changed after the observation" in capsys.readouterr().err


def test_confirming_twice_is_refused(workspace, capsys):
    send_then(workspace)
    confirm(workspace)
    assert confirm(workspace) == 1
    assert "already confirmed" in capsys.readouterr().err


def test_confirm_requires_a_behavior_description(workspace):
    table, evidence_dir = workspace
    with pytest.raises(SystemExit):
        main(["confirm", "media_music", "--table", str(table)], authorization=None)


def test_the_comment_header_survives_a_promotion(workspace):
    table, _ = workspace
    before = table.read_text("utf-8").split("commands:")[0]
    send_then(workspace)
    confirm(workspace)
    after = table.read_text("utf-8").split("commands:")[0]
    assert before == after


def test_unrelated_entries_are_untouched_by_a_promotion(workspace):
    import yaml

    table, _ = workspace
    before = {r["id"]: r for r in yaml.safe_load(table.read_text("utf-8"))["commands"]}
    send_then(workspace)
    confirm(workspace)
    after = {r["id"]: r for r in yaml.safe_load(table.read_text("utf-8"))["commands"]}
    for entry_id in before:
        if entry_id != "media_music":
            assert before[entry_id] == after[entry_id]
