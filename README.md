# carle

A protocol reference for the **Ruko 1088** smart robot's Bluetooth control channel, plus a
cross-platform CLI that exists to prove the reference is correct.

The 1088 is controlled by an Android/iOS app called **Carle**, published by iHunuo. No public
documentation describes how it talks to the robot. This repository is an attempt to write that
documentation and to keep it honest.

## Status

The transport and frame format are documented from the official Android app. **No command has
been run against a robot yet** — every derived frame is marked `decoded`, not `confirmed`.

| Area | State |
|---|---|
| BLE service and characteristic UUIDs | Documented |
| Command frame format | Documented |
| Command encodings | 6 decoded from the app, 0 confirmed on hardware |
| Notify characteristic contents | Not documented |
| Audio channel | Not documented |
| CLI scan / connect / info | Working |
| Sending commands | Not implemented |

What unblocks the rest: a session with a real robot, and a write path in the CLI.
[`docs/method.md`](docs/method.md) describes the procedure. If you have the hardware, that
document is the place to start.

### What the first decompile changed

Ruko's published capability counts do **not** map one-to-one onto protocol commands. The app
sends a single trigger per media category and lets the robot cycle internally, so the ten
songs, eight dance tracks, four stories and two gymnastic routines turn out to be four
commands. Movement collapsed the same way: one parameterized command with an eight-way
direction field rather than six directional ones.

The original rows are kept and marked `unlocated` with `superseded_by`, not deleted, so the
collapse stays traceable — and so they are already in place if the unused payload byte turns
out to select an individual track.

The fourteen voice commands appear nowhere in the Bluetooth layer. They look like onboard
speech recognition with no protocol surface at all.

## Quickstart

Requires Python 3.10+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/benwilson/carle.git
cd carle
uv sync
uv run carle scan
```

`scan` looks for a BLE peripheral advertising a `JT_` name, which is how the 1088 presents
itself. With the robot switched on and nearby you should see it listed. Then:

```bash
uv run carle info <address-from-scan>
```

`info` prints the peripheral's GATT services and characteristics exactly as discovered. That
output is the raw material for documenting the service UUIDs — if you run it against a real
1088, please open an issue with the result.

### macOS: grant Bluetooth permission first

On macOS 12 and later, the **terminal application** running `carle` needs Bluetooth access —
not the Python package, and not `carle` itself.

Without it, macOS does not return an error. It **terminates the process** — no output, no
traceback, exit code 134. This is not catchable from Python, so `carle scan` checks the
authorization state up front and prints an explanation before it attempts anything. If you see
that note followed by silence, this is what happened.

Grant access under **System Settings → Privacy & Security → Bluetooth**, then restart the
terminal application.

## What this repository is not

This is an interoperability reference. It documents how to talk to the robot. It is not a
security assessment and does not make recommendations about the security of the device.

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md). The short version: a command is not documented until
someone has issued it and watched the robot respond, and the test suite enforces that.

## License

MIT — see [`LICENSE`](LICENSE).
