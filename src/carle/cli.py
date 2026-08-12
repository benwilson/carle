"""Command-line interface for the Ruko 1088.

    carle scan                 list peripherals that look like the robot
    carle connect ADDRESS      confirm a connection can be established
    carle info ADDRESS         print the peripheral's GATT services
    carle send ID              issue a documented command
    carle confirm ID           promote a command to confirmed using its send log

`send` and `confirm` are two halves of one loop: send writes a log recording exactly
what went out, you watch the robot, and confirm reads that log back. They are separate
commands because the observation happens between them, in the physical world.

`confirm` is convenience, not enforcement. The invariant suite re-derives the same
judgement from the committed files, so a promotion nobody earned fails in CI whether or
not this code ever ran.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import sys
from pathlib import Path

from carle import __version__, evidence, frame
from carle.table import TableError, load_rows, load_table, save_table
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

EVIDENCE_DIR = "evidence"
#: Raw sends bypass the table, so their logs stay out of evidence/ entirely rather
#: than relying on one editable line to keep them from supporting a promotion.
RAW_LOG_DIR = ".carle/raw-logs"

#: Commands that actually touch CoreBluetooth. A dry run does not, and must not be
#: blocked by the macOS authorization guard.
BLUETOOTH_COMMANDS = {"scan", "connect", "info"}


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

    send = sub.add_parser("send", help="issue a documented command to the robot")
    send.add_argument("entry", nargs="?", help="command id from protocol/commands.yaml")
    send.add_argument("--address", help="peripheral address from `carle scan`")
    send.add_argument(
        "--param",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="override a declared parameter; repeatable",
    )
    send.add_argument(
        "--dry-run",
        action="store_true",
        help="print the frame and exit without connecting or writing a log",
    )
    send.add_argument("--raw", metavar="HEX", help="send an arbitrary payload, bypassing the table")
    send.add_argument("--family", metavar="BYTE", help="family byte for --raw, e.g. 0xB3")
    send.add_argument("--timeout", type=float, default=DEFAULT_SCAN_TIMEOUT)
    send.add_argument("--evidence-dir", type=Path, default=None)
    send.add_argument("--table", type=Path, default=None, help=argparse.SUPPRESS)

    confirm = sub.add_parser(
        "confirm", help="promote a decoded command to confirmed using its send log"
    )
    confirm.add_argument("entry")
    confirm.add_argument(
        "--behavior", required=True, help="what the robot actually did, in your words"
    )
    confirm.add_argument("--evidence-dir", type=Path, default=None)
    confirm.add_argument(
        "--log", help="which send log to confirm from, when more than one could apply"
    )
    confirm.add_argument("--table", type=Path, default=None, help=argparse.SUPPRESS)

    daemon = sub.add_parser("daemon", help="the always-on control plane that holds the link")
    daemon_sub = daemon.add_subparsers(dest="daemon_command", required=True)
    d_start = daemon_sub.add_parser("start", help="hold the link and run the command queue")
    d_start.add_argument("address", help="peripheral address from `carle scan`")
    d_start.add_argument(
        "--foreground", action="store_true", help="run in this terminal instead of backgrounding"
    )
    d_start.add_argument(
        "--interval", type=float, default=1.0, help="heartbeat silence floor in seconds"
    )
    daemon_sub.add_parser("stop", help="shut the daemon down")
    daemon_sub.add_parser("status", help="print the daemon's state")

    queue = sub.add_parser("queue", help="enqueue moves or steps on the running daemon")
    queue.add_argument(
        "items",
        nargs="+",
        metavar="MOVE|kind:value",
        help="a move name (wave) or a primitive (pose:5, waist:1, face:39, pause:1.0, say:hello)",
    )
    sub.add_parser("clear", help="drop the daemon's pending queue")
    sub.add_parser("stop", help="abort now and return the robot to neutral")
    sub.add_parser("status", help="print the robot's state via the daemon")

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


def _parse_params(pairs: list[str]) -> dict[str, int]:
    values: dict[str, int] = {}
    for pair in pairs:
        name, sep, raw = pair.partition("=")
        if not sep:
            raise ValueError(f"--param {pair!r} is not NAME=VALUE")
        try:
            values[name.strip()] = int(raw, 0)
        except ValueError as exc:
            raise ValueError(f"--param {pair!r} value is not a number") from exc
    return values


def _evidence_dir(args: argparse.Namespace, raw: bool) -> Path:
    if args.evidence_dir is not None:
        return Path(args.evidence_dir)
    root = table_root()
    return root / (RAW_LOG_DIR if raw else EVIDENCE_DIR)


def table_root() -> Path:
    from carle.table import repo_root

    return repo_root()


def _run_send(args: argparse.Namespace, backend: Backend) -> int:
    raw_mode = args.raw is not None
    try:
        overrides = _parse_params(args.param)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    entry_id = None
    try:
        if raw_mode:
            if args.family is None:
                print("error: --raw also needs --family, e.g. --family 0xB3", file=sys.stderr)
                return 1
            if overrides:
                # A raw payload has no declared parameters, so recording overrides in
                # its log would describe bytes that carry no such thing.
                print("error: --param has no meaning with --raw", file=sys.stderr)
                return 1
            payload = frame.from_hex(args.raw)
            built = frame.build(frame.byte_literal(args.family), payload)
        else:
            if not args.entry:
                print("error: give a command id, or use --raw with --family", file=sys.stderr)
                return 1
            entry = next((e for e in load_table(args.table).entries if e.id == args.entry), None)
            if entry is None:
                print(f"error: no command with id {args.entry!r}", file=sys.stderr)
                return 1
            if not entry.has_frame:
                print(
                    f"error: {entry.id} is '{entry.status}' — its frame is unknown, so there "
                    "is nothing to send. Only decoded and confirmed commands have bytes.",
                    file=sys.stderr,
                )
                return 1
            entry_id = entry.id
            built = entry.build_frame(overrides)
    except (frame.FrameError, TableError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.dry_run:
        # No connection, and deliberately no log: a dry run is hardware-free, so a log
        # for one sitting in evidence/ would be a promotion waiting to happen.
        print(frame.to_hex(built))
        return 0

    if not args.address:
        print("error: --address is required; run `carle scan` to find it", file=sys.stderr)
        return 1

    try:
        result = asyncio.run(backend.send(args.address, built, args.timeout))
    except TransportError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    log = evidence.SendLog(
        kind=evidence.KIND_RAW if raw_mode else evidence.KIND_SEND,
        frame=built,
        timestamp=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None),
        platform=sys.platform,
        peripheral=args.address,
        write_ok=result.ok,
        entry_id=entry_id,
        parameters=overrides,
        write_detail=result.detail,
        notifications=result.notifications,
    )
    try:
        path = evidence.write_log(log, _evidence_dir(args, raw_mode))
    except evidence.EvidenceError as exc:
        print(f"error: sent, but the log could not be written: {exc}", file=sys.stderr)
        return 1

    print(f"sent {frame.to_hex(built)}")
    for note in result.notifications:
        print(f"  notified: {frame.to_hex(note)}")
    print(f"logged to {path}")
    if not raw_mode:
        print(f'confirm it with: carle confirm {entry_id} --behavior "what the robot did"')
    return 0


def _run_confirm(args: argparse.Namespace) -> int:
    entry = next((e for e in load_table(args.table).entries if e.id == args.entry), None)
    if entry is None:
        print(f"error: no command with id {args.entry!r}", file=sys.stderr)
        return 1
    if not entry.has_frame:
        print(f"error: {entry.id} has no frame, so there is nothing to confirm", file=sys.stderr)
        return 1
    # An already-confirmed entry is no longer refused. That refusal existed to stop
    # evidence being overwritten, and appending does not overwrite — a parameterized
    # command is one frame spanning a whole space, and confirming it once described a
    # single point of that space. What still cannot happen is citing the same log
    # twice, which is checked below.
    if not args.behavior.strip():
        print("error: --behavior cannot be blank; say what the robot did", file=sys.stderr)
        return 1

    directory = _evidence_dir(args, raw=False)
    candidates = evidence.promotable_logs(entry.id, directory)
    if not candidates:
        print(
            f"error: no send log for {entry.id} in {directory}. Run `carle send {entry.id} "
            "--address ...` against a real robot first — a dry run does not count.",
            file=sys.stderr,
        )
        return 1

    if args.log:
        candidates = [(p, log) for p, log in candidates if p.name == args.log]
        if not candidates:
            print(f"error: no promotable log named {args.log!r} for {entry.id}", file=sys.stderr)
            return 1
    elif len(candidates) > 1:
        # Silently taking the newest binds the operator's description to whichever send
        # happened last, which may not be the one they watched.
        print(
            f"error: {len(candidates)} promotable logs for {entry.id}. Say which one you "
            "watched with --log:",
            file=sys.stderr,
        )
        for path, log in candidates:
            params = log.parameters or "defaults"
            print(f"  {path.name}  ({params})", file=sys.stderr)
        return 1

    log_path, log = candidates[0]

    # Cite the file that was actually checked, not a reconstructed path — the two
    # diverged whenever --evidence-dir pointed elsewhere, publishing a citation to a
    # file the CLI had never opened. The directory must also be named `evidence`, since
    # that is the only place the invariant suite will resolve a citation from.
    resolved_dir = directory.resolve()
    if resolved_dir.name != EVIDENCE_DIR or log_path.resolve().parent != resolved_dir:
        print(
            f"error: {log_path} is not inside a directory named {EVIDENCE_DIR}/, so it "
            "cannot be cited as evidence.",
            file=sys.stderr,
        )
        return 1
    citation = f"{EVIDENCE_DIR}/{log_path.name}"

    # One send is one observation. The reference publishes an observation count, which
    # a reader reads as how widely the command was exercised; the same log cited twice
    # would show as two independent confirmations of a single press of the button.
    already = next(
        (i for i, o in enumerate(entry.observations) if citation in o.logs),
        None,
    )
    if already is not None:
        print(
            f"error: {citation} is already cited by observation {already} of {entry.id}. "
            "Send again and confirm that log, or edit the existing observation by hand.",
            file=sys.stderr,
        )
        return 1

    try:
        expected = entry.build_frame(log.parameters)
    except (frame.FrameError, TableError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if log.frame != expected:
        print(
            f"error: the log recorded {frame.to_hex(log.frame)} but {entry.id} now builds to "
            f"{frame.to_hex(expected)}. The entry changed after the observation.",
            file=sys.stderr,
        )
        return 1

    rows = load_rows(args.table)
    total = 0
    for row in rows:
        if row["id"] == entry.id:
            row["status"] = "confirmed"
            observations = row.setdefault("observations", [])
            observations.append(
                {
                    "parameters": dict(log.parameters),
                    "behavior": args.behavior,
                    "evidence": {
                        # The calendar date, not the timestamp: table.py validates this
                        # with date.fromisoformat, which accepts nothing else on 3.10.
                        "date": log.date.isoformat(),
                        "platform": log.platform,
                        # A list of one. Citing several is a hand-edit for a finding read
                        # from a sequence; the CLI only ever watches one send at a time.
                        "logs": [citation],
                    },
                }
            )
            total = len(observations)
    save_table(rows, args.table)

    print(f"{entry.id} is confirmed, citing {citation}")
    print(f"it now carries {total} observation{'' if total == 1 else 's'}")
    print("regenerate the reference: uv run python scripts/generate_reference.py")
    return 0


def _tokens_to_items(tokens: list[str]) -> list[dict]:
    """Turn CLI queue tokens into protocol step items.

    A bare token is a named move (`wave`); a `kind:value` token is a primitive.
    """
    items: list[dict] = []
    for token in tokens:
        if ":" not in token:
            items.append({"move": token})
            continue
        kind, _, value = token.partition(":")
        if kind in ("pose", "waist"):
            items.append({kind: int(value)})
        elif kind == "face":
            # face:39 holds an LED expression; face:clear (or face:off/0) drops the hold.
            code = 0 if value in ("clear", "off") else int(value)
            items.append({"face": code})
        elif kind == "pause":
            items.append({"pause": float(value)})
        elif kind == "say":
            items.append({"say": value})
        else:
            raise ValueError(f"unknown step kind {kind!r} in {token!r}")
    return items


def _print_reply(reply: dict) -> int:
    """Print a daemon reply and return an exit code from its `ok` field."""
    if not reply.get("ok", False):
        print(f"error: {reply.get('error', 'the daemon refused the request')}", file=sys.stderr)
        return 1
    if "status" in reply:
        s = reply["status"]
        battery = "unknown" if s.get("battery") is None else f"{s['battery']}%"
        connected = "connected" if s.get("connected") else "disconnected"
        face = f"; face {s['face']}" if s.get("face") is not None else ""
        print(
            f"{connected}; battery {battery}; doing {s.get('current') or 'nothing'}{face}; "
            f"{s.get('pending', 0)} queued, {s.get('spawns', 0)} spawned"
        )
    elif "moves" in reply:
        print(", ".join(reply["moves"]))
    elif "enqueued" in reply:
        print(f"enqueued {reply['enqueued']} step(s)")
    else:
        print("ok")
    return 0


def _run_daemon(args: argparse.Namespace, requester) -> int:
    from carle.daemon.client import NoDaemonError
    from carle.daemon.server import UNIX_SOCKETS

    if args.daemon_command == "start":
        if not UNIX_SOCKETS:
            print(
                "error: the carle daemon requires Unix domain sockets and is POSIX-only",
                file=sys.stderr,
            )
            return 1
        if args.foreground:
            from carle.daemon.server import DaemonServer, DaemonUnsupported

            try:
                asyncio.run(DaemonServer(args.address, silence_floor=args.interval).serve())
            except KeyboardInterrupt:
                pass
            except DaemonUnsupported as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 1
            return 0
        import subprocess

        subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            [
                sys.executable,
                "-m",
                "carle.cli",
                "daemon",
                "start",
                args.address,
                "--foreground",
                "--interval",
                str(args.interval),
            ],
            start_new_session=True,
        )
        print(f"daemon started for {args.address}")
        return 0
    op = "shutdown" if args.daemon_command == "stop" else "status"
    try:
        return _print_reply(requester({"op": op}))
    except NoDaemonError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _run_client_op(op_or_items, requester) -> int:
    """Run a queue/clear/stop/status request, reporting a missing daemon cleanly."""
    from carle.daemon.client import NoDaemonError

    request = (
        op_or_items if isinstance(op_or_items, dict) else {"op": "enqueue", "items": op_or_items}
    )
    try:
        return _print_reply(requester(request))
    except NoDaemonError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _daemon_guard(daemon_live) -> int | None:
    """Refuse a per-call BLE verb while the daemon holds the link (KTD10)."""
    if daemon_live():
        print(
            "error: daemon holds the link — use `carle queue` or stop the daemon",
            file=sys.stderr,
        )
        return 1
    return None


def main(
    argv: list[str] | None = None,
    backend: Backend | None = None,
    authorization: str | None = "auto",
    requester=None,
    daemon_live=None,
) -> int:
    args = build_parser().parse_args(argv)

    if requester is None or daemon_live is None:
        from carle.daemon import client

        requester = requester or client.request
        daemon_live = daemon_live or client.daemon_live

    # Daemon lifecycle and the queue-control verbs speak to the socket, never the radio.
    if args.command == "daemon":
        return _run_daemon(args, requester)
    if args.command == "queue":
        try:
            items = _tokens_to_items(args.items)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        return _run_client_op(items, requester)
    if args.command == "clear":
        return _run_client_op({"op": "clear"}, requester)
    if args.command == "stop":
        return _run_client_op({"op": "stop"}, requester)
    if args.command == "status":
        return _run_client_op({"op": "status"}, requester)

    # A dry run and a confirm never reach CoreBluetooth, so the authorization guard
    # must not block them — on a machine with Bluetooth denied it otherwise fails a
    # command that touches no radio at all.
    needs_bluetooth = args.command in BLUETOOTH_COMMANDS or (
        args.command == "send" and not args.dry_run
    )

    # The daemon is the sole link-holder: a per-call BLE verb must refuse while it runs,
    # or two writers would contend for the connection (KTD10, R17).
    if needs_bluetooth and _daemon_guard(daemon_live) is not None:
        return 1

    if authorization == "auto":
        # Only the real backend touches CoreBluetooth, so only it can be SIGABRT'd.
        # An injected backend is a test double and needs no permission.
        authorization = macos_authorization() if backend is None and needs_bluetooth else None

    if needs_bluetooth:
        # `connect` and `info` build a BleakClient, which initializes a central manager
        # exactly as a scan does. Guarding only `scan` left those two dying by SIGABRT
        # with no output, the failure this check exists for.
        blocked = _check_macos_authorization(authorization)
        if blocked is not None:
            return blocked

    if args.command == "confirm":
        return _run_confirm(args)

    backend = backend or BleakBackend()

    if args.command == "scan":
        return _run_scan(args, backend)
    if args.command == "connect":
        return _run_connect(args, backend)
    if args.command == "info":
        return _run_info(args, backend)
    if args.command == "send":
        return _run_send(args, backend)
    raise AssertionError(f"unhandled command {args.command!r}")


if __name__ == "__main__":
    raise SystemExit(main())
