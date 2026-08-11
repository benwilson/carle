# carle

A protocol reference for the **Ruko 1088** smart robot's Bluetooth control channel, plus a
cross-platform CLI that exists to prove the reference is correct.

The 1088 is controlled by an Android/iOS app called **Carle**, published by iHunuo. No public
documentation describes how it talks to the robot. This repository is an attempt to write that
documentation and to keep it honest.

## Status

**No protocol content is documented yet.** Every command in the table is marked `unmapped`.

| Area | State |
|---|---|
| BLE service and characteristic UUIDs | Not documented |
| Command frame format | Not documented |
| Command encodings | None — every row is `unmapped` |
| Audio channel | Not documented |
| CLI scan / connect / info | Working |
| Command table structure and honesty gate | Working |

What unblocks the rest: pulling the Carle APK off an Android device and decompiling it, then
confirming each decoded frame against a physical robot. [`docs/method.md`](docs/method.md)
describes both procedures. If you have the hardware, that document is the place to start.

The table in [`docs/protocol-reference.md`](docs/protocol-reference.md) lists the robot's
*published capabilities* so the work has a checklist. Those rows are seeded from Ruko's
marketing copy, not from the protocol — the row set is not known to be complete, and the rows
are not known to map one-to-one onto protocol commands.

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
uv run carle info --address <address-from-scan>
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
