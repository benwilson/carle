"""Bluetooth transport for the Ruko 1088.

The Bleak surface is kept deliberately narrow — discovery and service enumeration —
so the CLI's behavior can be tested against a fake backend without hardware. Anything
richer would make the fake a second implementation rather than a stand-in.

No command dispatch lives here. There is nothing to dispatch until the protocol is
documented, and shipping a `send` that writes guessed bytes is the failure this project
is organized to avoid.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

#: The 1088 advertises under a name beginning with this prefix, per the vendor manual.
#: `JT` is a JieLi-family module prefix; the trailing characters vary per unit.
ROBOT_NAME_PREFIX = "JT_"

DEFAULT_SCAN_TIMEOUT = 8.0


class TransportError(Exception):
    """Raised when the adapter or the peripheral cannot be reached."""


@dataclass(frozen=True)
class Peripheral:
    address: str
    name: str | None = None
    rssi: int | None = None

    @property
    def looks_like_robot(self) -> bool:
        return bool(self.name and self.name.startswith(ROBOT_NAME_PREFIX))


@dataclass(frozen=True)
class Characteristic:
    uuid: str
    description: str = ""
    properties: tuple[str, ...] = ()


@dataclass(frozen=True)
class Service:
    uuid: str
    description: str = ""
    characteristics: list[Characteristic] = field(default_factory=list)


@runtime_checkable
class Backend(Protocol):
    """What the CLI needs from a Bluetooth stack. Implemented by Bleak and by the tests."""

    async def discover(self, timeout: float) -> list[Peripheral]: ...

    async def services(self, address: str, timeout: float) -> list[Service]: ...


class BleakBackend:
    """The real backend. Imports Bleak lazily so `--help` works without a Bluetooth stack."""

    async def discover(self, timeout: float = DEFAULT_SCAN_TIMEOUT) -> list[Peripheral]:
        try:
            from bleak import BleakScanner
        except ImportError as exc:  # pragma: no cover - dependency is declared
            raise TransportError(f"bleak is not installed: {exc}") from exc

        try:
            devices = await BleakScanner.discover(timeout=timeout)
        except Exception as exc:
            raise TransportError(f"scan failed: {exc}") from exc

        return [
            Peripheral(
                address=str(device.address),
                name=device.name,
                rssi=getattr(device, "rssi", None),
            )
            for device in devices
        ]

    async def services(self, address: str, timeout: float = DEFAULT_SCAN_TIMEOUT) -> list[Service]:
        try:
            from bleak import BleakClient
        except ImportError as exc:  # pragma: no cover - dependency is declared
            raise TransportError(f"bleak is not installed: {exc}") from exc

        try:
            async with BleakClient(address, timeout=timeout) as client:
                return [
                    Service(
                        uuid=str(service.uuid),
                        description=service.description or "",
                        characteristics=[
                            Characteristic(
                                uuid=str(char.uuid),
                                description=char.description or "",
                                properties=tuple(char.properties),
                            )
                            for char in service.characteristics
                        ],
                    )
                    for service in client.services
                ]
        except Exception as exc:
            raise TransportError(f"could not connect to {address}: {exc}") from exc


def filter_robots(peripherals: list[Peripheral]) -> list[Peripheral]:
    return [p for p in peripherals if p.looks_like_robot]


#: CBManagerAuthorization, from Apple's CoreBluetooth headers.
_MACOS_AUTHORIZATION = {0: "not-determined", 1: "restricted", 2: "denied", 3: "allowed"}


def macos_authorization(platform: str | None = None) -> str | None:
    """Report the process's Bluetooth authorization on macOS, or ``None`` elsewhere.

    Worth the awkwardness because of how macOS fails: a process that touches
    CoreBluetooth without authorization is **terminated by the OS** — SIGABRT, no
    traceback, no output. That is not catchable from Python, so the only way to give the
    user an explanation is to ask before scanning. Reading the authorization value
    itself does not trigger the abort.

    Returns ``None`` when the state cannot be read, which is treated as "proceed".
    """
    platform = platform if platform is not None else sys.platform
    if platform != "darwin":
        return None
    try:
        import CoreBluetooth  # noqa: PLC0415 - optional, macOS-only, imported lazily
    except ImportError:
        return None
    try:
        return _MACOS_AUTHORIZATION.get(int(CoreBluetooth.CBCentralManager.authorization()))
    except Exception:
        return None


AUTHORIZATION_DENIED_HELP = """macOS has denied this process access to Bluetooth.

Grant it to the application running this command — your terminal, not Python itself —
under System Settings > Privacy & Security > Bluetooth, then restart that application."""

AUTHORIZATION_UNDETERMINED_HELP = """note: macOS has not yet been asked for Bluetooth
access for this process.

If a permission prompt appears, accept it. If this command exits with no output at all,
macOS terminated it for lacking Bluetooth access — grant it under System Settings >
Privacy & Security > Bluetooth to the terminal application, then restart that
application and try again."""


def describe_identity(address: str, platform: str | None = None) -> str:
    """Render a peripheral's identity, labeled for the platform that produced it.

    This is not cosmetic. macOS CoreBluetooth never exposes the Bluetooth MAC — it hands
    out a system-assigned UUID that differs per host — so presenting the same field as an
    "address" on all three platforms would mislead anyone comparing notes across machines.
    """
    platform = platform if platform is not None else sys.platform
    if platform == "darwin":
        return f"{address} (macOS system-assigned UUID, not a Bluetooth address)"
    if platform.startswith("linux"):
        return f"{address} (Bluetooth MAC address)"
    if platform.startswith("win"):
        return f"{address} (Windows device address)"
    return f"{address} (address as reported by {platform})"


EMPTY_SCAN_HELP = f"""No Bluetooth peripherals advertising a '{ROBOT_NAME_PREFIX}' name were found.

Likely causes, in order:
  1. The robot is switched off, asleep, or out of range.
  2. The robot is already connected to the Carle app on a phone.
  3. The adapter is present but sees nothing — check with 'carle scan --all'.

Run 'carle scan --all' to list every peripheral the adapter can see. If that is also
empty, the adapter rather than the robot is the problem."""
