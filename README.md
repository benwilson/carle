# carle

A protocol reference for the **Ruko 1088** smart robot's Bluetooth control channel, plus a
cross-platform CLI that proves the reference is correct and a small, growing set of tools that
drive the robot from it.

The 1088 is controlled by an Android/iOS app called **Carle**, published by iHunuo. No public
documentation describes how it talks to the robot. This repository is an attempt to write that
documentation and to keep it honest.

## Status

The whole BLE surface is documented from the official Android app — all four command families,
the firmware-update stack, and the controller chip — and hardware sessions have confirmed the
movement and media commands and driven the robot live: waving, a short dance, and speaking
through its own speaker.

| Area | State |
|---|---|
| BLE service and characteristic UUIDs | Documented |
| Command frame format (all four families) | Documented |
| Movement / limbs (`0xB6`) | Confirmed on hardware, mapped across the parameter space |
| Media and volume (`0xB3`) | Confirmed frame-for-frame |
| Gyro / tilt (`0xB5`) and sequences (`0xB2`) | Decoded from the app |
| Command encodings | 7 derived from the app; 3 confirmed on hardware |
| Hardware observations | 28 across 3 commands, backed by 210 committed send logs |
| Notify characteristic + robot state | Documented — notify is discarded; battery/versions come via reads |
| Audio channel (`JT_Speaker`) | Verified — plays arbitrary host audio |
| Firmware update (OTA/DFU) and chip | Documented from the app (Realtek `RTL8763B`) |
| CLI scan / connect / info / send / confirm | Working |
| Driving the robot (movement, media, audio, keep-alive) | Working |

What remains needs the hardware or the iOS app, not more decompiling: what each `0xB2`
sequence code does on a real robot, a true pivot-in-place, and the firmware image (behind a
geo-fenced vendor server). [`docs/protocol-reference.md`](docs/protocol-reference.md) is the
full reference; [`docs/movement-vocabulary.md`](docs/movement-vocabulary.md) maps plain-language
moves to the byte primitives, with servo-safe timing.

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

To send a command, and then record what the robot did:

```bash
uv run carle send media_music --dry-run          # see the frame first
uv run carle send media_music --address <address>
uv run carle confirm media_music --behavior "Played a song"
```

A command is not documented until that second step has happened against real hardware.
See [`CONTRIBUTING.md`](CONTRIBUTING.md) for exactly what `confirmed` claims — and what it
does not.

### Driving the robot

Beyond single commands, the robot can be driven continuously over a held connection — that is
what makes it wave or dance rather than twitch once. Movement is composed from byte primitives;
[`docs/movement-vocabulary.md`](docs/movement-vocabulary.md) maps plain-language moves ("wave",
"fist pump", "sway") to those primitives, and states the servo-safe timing the little geared
joints need — driven too fast, they squeal and strain.

Left alone, the robot resumes its own idle routine within a second or two. To keep it still and
under the control plane's authority, [`tools/keepalive.py`](tools/keepalive.py) holds the link
and streams a no-op movement frame just often enough to deny the idle routine its window:

```bash
uv run python tools/keepalive.py <address>
```

The robot also exposes a separate Bluetooth audio sink, `JT_Speaker`. Paired as a normal system
output it plays any host audio — so, on macOS, `say "hello"` speaks through the robot. The
control link and the audio sink are independent surfaces that can be used together.

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
