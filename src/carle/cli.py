"""Command-line interface for the Ruko 1088.

Three commands, all read-only against the robot:

    carle scan             list peripherals that look like the robot
    carle connect ADDRESS  confirm a connection can be established
    carle info ADDRESS     print the peripheral's GATT services and characteristics

`info` is the important one for now. Its raw output is the material the transport
section of docs/protocol-reference.md gets written from, so it prints what it discovers
verbatim rather than interpreting it.

There is no command that writes to the robot. Adding one requires knowing the frame
format, which nobody does yet.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from carle import __version__
from carle.transport import (
    AUTHORIZATION_DENIED_HELP,
    AUTHORIZATION_UNDETERMINED_HELP,
    DEFAULT_SCAN_TIMEOUT,
    EMPTY_SCAN_HELP,
    Backend,
    BleakBackend,
    TransportError,
    describe_identity,
    filter_robots,
    macos_authorization,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="carle",
        description="Talk to a Ruko 1088 robot over Bluetooth Low Energy.",
    )
    parser.add_argument("--version", action="version", version=f"carle {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="list nearby peripherals that look like the robot")
    scan.add_argument(
        "--all",
        action="store_true",
        help="list every peripheral, not just those advertising a JT_ name",
    )
    scan.add_argument("--timeout", type=float, default=DEFAULT_SCAN_TIMEOUT)

    connect = sub.add_parser("connect", help="confirm a connection can be established")
    connect.add_argument("address")
    connect.add_argument("--timeout", type=float, default=DEFAULT_SCAN_TIMEOUT)

    info = sub.add_parser("info", help="print the peripheral's GATT services")
    info.add_argument("address")
    info.add_argument("--timeout", type=float, default=DEFAULT_SCAN_TIMEOUT)

    return parser


def _check_macos_authorization(authorization: str | None) -> int | None:
    """Return an exit code when scanning cannot proceed, or ``None`` to continue."""
    if authorization in ("denied", "restricted"):
        print(AUTHORIZATION_DENIED_HELP, file=sys.stderr)
        return 1
    if authorization == "not-determined":
        print(AUTHORIZATION_UNDETERMINED_HELP, file=sys.stderr)
    return None


def _run_scan(args: argparse.Namespace, backend: Backend) -> int:
    try:
        peripherals = asyncio.run(backend.discover(args.timeout))
    except TransportError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    shown = peripherals if args.all else filter_robots(peripherals)

    if not shown:
        # An empty scan is the normal result on a machine with no robot nearby, and on
        # macOS it is also what a denied Bluetooth permission looks like. Neither is an
        # error, so this exits zero and explains rather than failing the caller.
        print(EMPTY_SCAN_HELP if not args.all else "No Bluetooth peripherals found.")
        return 0

    for peripheral in shown:
        label = peripheral.name or "(unnamed)"
        rssi = f"  rssi {peripheral.rssi}" if peripheral.rssi is not None else ""
        print(f"{label}\n  {describe_identity(peripheral.address)}{rssi}")
    return 0


def _run_connect(args: argparse.Namespace, backend: Backend) -> int:
    try:
        asyncio.run(backend.services(args.address, args.timeout))
    except TransportError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Connected to {describe_identity(args.address)}")
    return 0


def _run_info(args: argparse.Namespace, backend: Backend) -> int:
    try:
        services = asyncio.run(backend.services(args.address, args.timeout))
    except TransportError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(describe_identity(args.address))
    if not services:
        print("\nThe peripheral exposed no GATT services.")
        return 0

    print(f"\n{len(services)} service(s):\n")
    for service in services:
        suffix = f"  {service.description}" if service.description else ""
        print(f"  service {service.uuid}{suffix}")
        for char in service.characteristics:
            props = ", ".join(char.properties) if char.properties else "no properties"
            detail = f"  {char.description}" if char.description else ""
            print(f"    char  {char.uuid}  [{props}]{detail}")
        print()

    print(
        "This output is raw. If you ran it against a real 1088, please open an issue "
        "with the result —\nit is what the transport section of the protocol reference "
        "gets written from."
    )
    return 0


def main(
    argv: list[str] | None = None,
    backend: Backend | None = None,
    authorization: str | None = "auto",
) -> int:
    args = build_parser().parse_args(argv)

    if authorization == "auto":
        # Only the real backend touches CoreBluetooth, so only it can be SIGABRT'd.
        # An injected backend is a test double and needs no permission.
        authorization = macos_authorization() if backend is None else None

    # Every command reaches CoreBluetooth — `connect` and `info` build a BleakClient,
    # which initializes a central manager exactly as a scan does. Guarding only `scan`
    # left those two dying by SIGABRT with no output, the failure this check exists for.
    blocked = _check_macos_authorization(authorization)
    if blocked is not None:
        return blocked

    backend = backend or BleakBackend()

    if args.command == "scan":
        return _run_scan(args, backend)
    if args.command == "connect":
        return _run_connect(args, backend)
    if args.command == "info":
        return _run_info(args, backend)
    raise AssertionError(f"unhandled command {args.command!r}")


if __name__ == "__main__":
    raise SystemExit(main())
