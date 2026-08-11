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
    Characteristic,
    Peripheral,
    Service,
    TransportError,
    describe_identity,
    filter_robots,
    macos_authorization,
)


class FakeBackend:
    def __init__(self, peripherals=None, services=None, error: Exception | None = None):
        self._peripherals = peripherals or []
        self._services = services or []
        self._error = error

    async def discover(self, timeout: float):
        if self._error:
            raise self._error
        return list(self._peripherals)

    async def services(self, address: str, timeout: float):
        if self._error:
            raise self._error
        return list(self._services)


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


def test_there_is_no_send_subcommand():
    """Dispatch needs a frame format nobody has documented. Guards against adding one early."""
    with pytest.raises(SystemExit):
        main(["send", "move_forward"], backend=FakeBackend())


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
