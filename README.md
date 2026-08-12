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
| Driving the robot (movement, media, audio) | Working |
| Control-plane daemon (queue, heartbeat, CLI + MCP, state) | Built; unit-tested against fakes, not yet hardware-validated |

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

Beyond single commands, an always-on **control-plane daemon** holds the link and runs a timed
command queue — that is what makes the robot wave or dance rather than twitch once. Left alone,
the robot resumes its own idle routine within a second or two; the daemon heartbeats a no-op
frame just often enough to deny that routine its window, and stays the sole holder of the link.

```bash
uv run carle daemon start <address>      # hold the link and run the queue
uv run carle queue wave                  # enqueue a named move (or pose:5, pause:1.0, say:hello)
uv run carle queue face:47               # hold an LED expression (39-48); face:clear drops it
uv run carle queue gesture:1             # pulse a 0xB2 arm gesture once (1-24 hand/arm codes)
uv run carle status                      # connection, battery, current step, held face, queue depth
uv run carle clear                       # drop pending steps
uv run carle stop                        # abort now and return the robot to neutral
uv run carle daemon stop                 # shut the daemon down
```

Moves are composed from byte primitives; [`docs/movement-vocabulary.md`](docs/movement-vocabulary.md)
maps plain-language moves ("wave", "fist pump", "sway") to those primitives and states the
servo-safe timing the little geared joints need — driven too fast, they squeal and strain. The
daemon exposes the same queue to AI agents through an MCP server (`carle-mcp`, installed with
`pip install 'carle[mcp]'`).

Because the daemon is the sole link-holder, `carle send/connect/info` refuse while it is
running — stop the daemon, or drive through `carle queue`. The daemon supersedes the earlier
`tools/keepalive.py`, which is kept, marked deprecated, only as the single-purpose experiment
for the first live-robot heartbeat validation.

The daemon's control channel is a Unix domain socket, so **the daemon is POSIX-only** (macOS
and Linux). On Windows the single-shot verbs (`carle send`, `scan`, `info`) still work;
`carle daemon start` and the queue verbs report that the daemon needs Unix sockets rather than
crashing.

**Speech.** The robot also exposes a separate Bluetooth audio sink, `JT_Speaker`. Paired and
selected as the host's system audio output, it plays any host audio — so a `say:` step (macOS
`say`) speaks through the robot. The daemon does not pair or route audio; that is host setup.

**Partly hardware-validated (2026-08-12).** A first live session confirmed the core: the daemon
holds a real BLE link, `carle queue face:N` holds an LED expression, and the heartbeat genuinely
beats the idle timer — streaming a frame continuously crowds the idle routine out, which is how
each expression face was held long enough to read (see the protocol reference's Expression
codes). Still awaiting a live session: the servo-safe movement cadence, the neutral-return,
reconnect resume, whether the robot acts on a compound multi-joint frame, and whether audio and
motion run together. On the test unit the standard battery read returned nothing, so `status`
showed the battery as unknown.

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
