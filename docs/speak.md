# Speak: route rendered audio to the robot's speaker

The 1088 exposes a Bluetooth audio sink, `JT_Speaker`, separate from its BLE control
channel. The **speak service** is a small local HTTP API that plays caller-supplied audio
through that speaker as an explicitly targeted output device — **without changing the host's
default output**. Another app (say, a microphone listener that captures a question and renders
an answer) can POST that answer's audio and have it come out of the robot while your own
headphones keep playing everything else.

While the audio plays, the service drives the robot over the control-plane daemon so it *looks*
like it is talking — a talking LED face and periodic arm gestures — and returns it to neutral
when playback truly finishes.

Two facts make this safe and worth doing:

- **The BLE control link and the A2DP audio sink are independent.** Driving BLE animation while
  host audio streams through the robot's speaker does not interrupt the audio (validated on
  hardware 2026-08-12). So the robot can gesture while it "speaks."
- **The system default output is never touched.** The service resolves the robot's speaker by
  name to a specific device and writes only to it. Your AirPods stay selected as the system
  output; only this audio is redirected.

Designed cross-platform (via PortAudio/`sounddevice`); validated on macOS.

## Install

The audio backends live behind an optional extra so the core CLI stays lean:

```bash
pip install 'carle[speak]'
# or, in this repo:
uv sync --extra speak
```

This pulls in `sounddevice` (PortAudio), `soundfile`, `miniaudio`, and `soxr`. On a machine
without the extra, `carle speak-server` prints a one-line install hint rather than a traceback;
the rest of the CLI is unaffected.

`sounddevice` needs the system PortAudio library present (`libportaudio`) — installed with the
wheel on most platforms; on Linux CI, `apt-get install libportaudio2`.

## Setup

1. **Pair the robot as a Bluetooth *audio output*** in your OS sound settings (it appears as
   `JT_Speaker`). Do **not** select it as the system default — leave your normal output
   selected. The service targets it by name regardless of what the system default is.
2. **Start the control-plane daemon** so the robot can animate while it speaks:
   ```bash
   uv run carle daemon start <address>
   ```
   (Animation is optional — run with `--no-animate` to skip it and just play audio.)
3. **Start the speak server:**
   ```bash
   uv run carle speak-server
   # defaults: --device JT_Speaker  --port 8081  --socket <daemon socket>  (--no-animate to skip animation)
   ```

## Play audio

The server listens on loopback only (`127.0.0.1`) — the audio path is never exposed off the
machine. POST rendered audio to it:

```bash
# a whole clip (WAV / MP3 / FLAC / OGG), decoded and played
curl --data-binary @answer.wav http://127.0.0.1:<port>/speak/clip

# a live stream — pipe encoded audio as it is produced
some-tts | curl --data-binary @- http://127.0.0.1:<port>/speak/stream

# stop the current playback and return the robot to neutral
curl -X POST http://127.0.0.1:<port>/speak/stop
```

While a clip or stream plays, the robot holds a talking face and pulses gestures; it returns to
neutral only after playback actually finishes (or on `stop`). A second speak request while one
is already playing is refused with `409` — one playback owns the device at a time.

If the target device is unavailable, the request fails loudly (`503`) rather than leaking the
audio to the host's default output.

## Status

- **Validated on macOS**, designed cross-platform. The BLE-vs-audio independence and the
  system-default-untouched guarantee are the load-bearing behaviors; the manual hardware smoke
  test is: pair the robot, start the daemon, POST a WAV clip and an MP3 stream, and confirm the
  robot's speaker plays them, the robot animates and returns to neutral, and the host's default
  output (e.g. AirPods) is untouched throughout.
- There is **no playback-end signal from the robot** on the BLE channel, so the animation's
  return-to-neutral is driven by the audio device draining, not by anything the robot reports.
