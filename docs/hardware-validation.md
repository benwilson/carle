# Hardware validation — the open checklist

Everything in this repo that **needs the physical Ruko 1088 robot to confirm**, why it is still
unverified, and the exact steps to validate it. Work built without the robot present is
mock-tested and reviewed but not proven against hardware; this file is the queue for a session
with the robot on the desk.

**How to use this:** pick an item, run its steps with the robot powered on and in range, observe
what actually happens, and record the result — either by promoting a command with `carle confirm`
(which the honesty gate enforces) or by updating [`docs/protocol-reference.md`](protocol-reference.md)
and the note in this file. Cross an item off only when a real observation backs it.

Items are ordered by value. **A–B** are the highest-leverage; **C** validates fixes already
merged; the rest are longer-tail.

---

## 0. One-time setup (do this first each session)

1. **Power the robot on** and keep it within Bluetooth range.
2. **Grant the terminal Bluetooth access** (macOS 12+): System Settings → Privacy & Security →
   Bluetooth → enable your terminal app, then restart it. Without this, `carle scan` is
   SIGKILL'd with no traceback (exit 134) — the CLI prints a note first if it can detect the state.
3. **Find the robot and confirm the link:**
   ```bash
   uv sync
   uv run carle scan                 # look for a JT_ name; note the address
   uv run carle info <address>       # prints the real GATT services/characteristics
   ```
   The robot's BLE address on the dev unit has been `5B4DDB82-BB32-D566-E1F9-212CE37BD9A7`
   (CoreBluetooth UUID; it may differ on another Mac).
4. **For anything involving motion/queue/faces/audio-animation, start the daemon** (it holds the
   single link; per-call `send`/`connect`/`info` refuse while it runs):
   ```bash
   uv run carle daemon start <address>
   uv run carle status               # connection, battery, current step, held face, queue depth
   ```
   Stop it with `uv run carle daemon stop` when switching to per-call verbs.

---

## A. Speak service — the full audio + animation path  ⟵ biggest unverified surface

**What:** `carle speak-server` (PRs #16, #17) plays caller-supplied audio to the robot's
Bluetooth speaker as an explicitly targeted device and animates the robot while it "talks."
**Why unverified:** every test is mocked — no real PortAudio device, robot, or daemon in CI. The
BLE-vs-A2DP independence was checked once on hardware (2026-08-12), but the end-to-end service
(clip + stream + animation + return-to-neutral + system-default-untouched) has never run against
the robot. See [`docs/speak.md`](speak.md).

**Steps:**
1. Install the audio extra and pair the robot **as a Bluetooth audio output** (`JT_Speaker`) in
   macOS Sound settings. **Do not** make it the system default — leave your own output (e.g.
   AirPods) selected.
   ```bash
   uv sync --extra speak
   ```
2. Start the daemon (for animation) and the speak server:
   ```bash
   uv run carle daemon start <address>
   uv run carle speak-server         # loopback :8081; --no-animate to test audio alone
   ```
3. Play a clip and a live stream:
   ```bash
   say -o /tmp/answer.wav --data-format=LEF32@22050 "Hello, I am the robot."   # or any WAV/MP3
   curl --data-binary @/tmp/answer.wav http://127.0.0.1:8081/speak/clip
   some-tts-command | curl --data-binary @- http://127.0.0.1:8081/speak/stream
   curl -X POST http://127.0.0.1:8081/speak/stop
   ```

**Confirm each of these — they are the load-bearing guarantees:** *(all validated 2026-08-13)*
- [x] The clip and the stream actually play **through the robot's speaker** — operator heard both
      (WAV clip via `/speak/clip`; MP3 via `/speak/stream` — note the stream endpoint rejects a
      raw WAV body with 400 "failed to init decoder"; encoded audio such as MP3 is what it wants).
- [x] Your **host default output is never changed** — default was `MacBook Pro Speakers` before
      and after every request. **Caveat found:** macOS had silently made `JT_Speaker` the system
      default at pairing time; the setup step should say to check and switch it back first.
- [x] The robot **animates while speaking** (talking LED face + gestures, camera-verified) and
      **returned to a resting face + arms-down after playback completed**.
- [x] `POST /speak/stop` interrupted a 22 s stream at the 10 s mark (request returned
      `outcome: "stopped"`, stop returned `stopped: true`); daemon state clean afterwards.
- [x] Second request during playback → **409** "busy"; nonexistent device → **503** "refusing to
      fall back to the host default". GET on `/speak/stop` → 405.
- [x] `carle queue wave` fired mid-stream at ~5 s; playback was still live at 10 s and audible
      throughout (re-confirms BLE/A2DP independence).

Neither deferred follow-up (300 s watchdog, lost-stop race) was triggered in these runs; both
remain open as code follow-ups.

**Stream-format matrix (2026-08-13):** measured first as MP3-only (WAV/FLAC/Vorbis/Opus/AAC
refused; raw "accepted" but silently played nothing — a zero-frame false pass). The decoder
was then rebuilt on `av` the same day: all six TTS-relevant containers plus declared raw PCM
now stream, a zero-frame decode is a 400, and blocks are re-cut to the player's fixed size
(the first cut played WAV/FLAC sped-up and garbled on the robot — the `StreamPlayer`
truncates oversized blocks). Unit-tested end to end; details in [`docs/speak.md`](speak.md).
- [x] **Post-fix hardware listen (2026-08-13):** all seven — MP3, WAV, FLAC, Ogg-Vorbis,
      Ogg-Opus, ADTS AAC, declared raw PCM — played through the robot's speaker; operator
      reports each sounded right, every request took ~4.0 s for the ~3.9 s clip, and the host
      default output stayed on the Mac's own speakers throughout.

**Power-cycle resilience (2026-08-13) — RESOLVED, both halves fixed and validated live:**
surviving the robot being switched off and on no longer requires restarting anything.
- The **BLE daemon** reconnects on its own, and the engine now **pauses the queue during an
  outage and resumes it after reconnect** (deadlines shifted by the outage; held state — the
  face, a held pose — is re-asserted because the robot may have rebooted). Validated with a
  mid-queue power cycle: the queue froze at 15 steps for the whole outage, then drained
  physically after reconnect with the held face intact.
- The **speak server** recovers in-process: on a stream open/write error the sink rescans
  PortAudio's device topology (its init-time snapshot is what went stale — `PaErrorCode
  -9986` forever, previously) and re-resolves the device once before failing. Validated: a
  server started before the power cycle played a clip right after it, no restart.

**Known follow-ups to watch for during this test** (deferred in PR #16, still open): the
animation watchdog tears the face down at a fixed 300 s regardless of real playback (fine for
short answers); a `/speak/stop` in the microseconds before playback starts can report a false
"nothing playing" (lost-stop race).

---

## B. Inbound state reads — battery and firmware versions

**What:** the battery characteristic (`0x2A19`) and the two version characteristics are decoded
from the Android app, and `carle status` surfaces battery.
**Why unverified:** on the dev unit the battery read comes back **empty**, so `status` shows
battery unknown. Whether the robot actually exposes these read paths is the open question.

**Steps:**
```bash
uv run carle info <address>          # does it advertise 0x2A19 and the version characteristics?
uv run carle daemon start <address>
uv run carle status                  # does battery show a real percentage, or unknown?
```
**Confirm:**
- [x] Does `carle info` list the battery + version characteristics at all on this unit?
      **No — resolved 2026-08-13.** Full service discovery returned exactly one service: the
      `AE00` control service (`AE01` write-without-response, `AE02` notify). No battery service
      `0x180F`, no OTA interface service `0xD0FF`, no version characteristics.
- [x] Does `status` report a real battery percentage? **No — "battery unknown", which is correct:
      there is no battery characteristic on this unit to read.** README + protocol-reference
      caveats updated accordingly.
- [x] Do the version reads return anything? **They cannot — the characteristics do not exist on
      this unit.**

Recorded in the README status table and `protocol-reference.md` (inbound-reads note, 2026-08-13).
**Note:** the robot does **not** announce low battery by voice — do not re-introduce that claim
(it was a fabrication corrected earlier).

---

## C. Daemon concurrency fixes — validate the merged fixes, then the still-open ones

PR #18 added a **write timeout** on `send_frame` (a hung BLE write can no longer wedge the tick
loop). It is unit-tested against a fake, but validate it does not disturb normal driving on
hardware, and confirm the daemon survives a real mid-drive link drop:

**Steps:**
```bash
uv run carle daemon start <address>
uv run carle queue wave              # drive some motion
uv run carle queue pose:5 face:47 gesture:1
# Now cause a real drop: walk the robot out of range / power-cycle it mid-queue.
uv run carle status                  # daemon should report disconnected, then reconnect and resume
```
**Confirm the merged fixes hold live:**
- [x] Normal driving (moves, poses, faces, gestures) still works with the write timeout in place.
      **Confirmed 2026-08-13:** `queue wave` (6 steps) and `queue pose:5 face:47 gesture:1`
      (3 steps) both drained; operator observed the wave, pose 5, and the face-47 smile live.
- [x] A real link drop mid-drive does **not** freeze the daemon; it reconnects and the heartbeat resumes.
      **Ran 2026-08-13 (robot powered off ~10 s mid-queue):** no freeze; `status` flipped to
      `disconnected` within seconds and back to `connected` right after power-on. **But the
      finding that matters: the queue kept EXECUTING while disconnected** — 18 → 11 → 5 queued
      during the outage — burning every remaining step into the dead link (writes are
      without-response, so nothing bounces). By reconnect the queue was empty; steps that "ran"
      during the outage never happened physically and are silently lost. The tick loop must
      pause on disconnect and resume the queue after reconnect. Held-face re-assertion across
      the reboot looked absent on camera (display changed when face:47 was re-sent) but LED
      faces animate, so treat that half as unconfirmed.

**Still-open, need hardware to design/validate** (deferred in PR #18 — do NOT "fix" these blind;
each needs the robot to see the real behavior first):
- [ ] **stop-vs-tick return-to-neutral race** — issue `carle stop` repeatedly during an active
      pose/move and check the robot **always** walks its joints back to neutral (never leaves a
      joint physically extended). If it ever sticks, the tick/stop lock race is real and needs the
      shared-lock fix validated here.
      **OBSERVED 2026-08-13 — the race is real.** Five rounds of `queue pose:5` + `stop` at
      0.3/1/2/0.5/1.5 s into the hold left the left arm physically extended (camera-verified)
      while the daemon reported `doing nothing; 0 queued`. A follow-up `carle stop` returned `ok`
      but moved nothing — once the daemon believes it is idle, stop is a no-op and the robot stays
      desynced. Recovery that worked: `queue gesture:19` (arms-down reset).
      **FIXED + validated on hardware later the same day.** The mechanism was not a lock race
      (the daemon is single-loop asyncio): a second stop truncated the first stop's neutral
      walk mid-servo-travel and rebuilt it from an already-stale `_last_sent`. Stops now
      converge — a stop during a walk preserves the walk — and every walk ends with the
      bilateral arms-down gesture so a desynced joint still comes home (stop is no longer a
      no-op when the daemon believes it is idle). Three pose + double-stop rounds on hardware:
      arms fully home each time (camera).
- [ ] **interrupted-pose resume** — hold a pose (`carle queue pose:N`), drop the link briefly, and
      see whether the pose resumes after reconnect or is silently forgotten (`RECONNECT_BACKOFF`
      1.5 s > `SAFE_HOLD` 0.5 s suggests it is dropped). Correct resume semantics need this
      observation before coding.
- [ ] **media-vs-heartbeat quiet window** — trigger media/a melody, then let the heartbeat run, and
      time how long the audio plays before the `0xB6` NOOP cuts it. **Measuring the real song/story
      durations is the blocker** for implementing a proper quiet window (see item F).

---

## D. Reconnect-resume after a dropped link

**What:** the daemon schedules a background reconnect on a dropped link.
**Why unverified:** never exercised end-to-end on hardware (only against the fake client).
- [x] Power-cycle or range-drop the robot while the daemon runs; confirm it reconnects on its own
      and the queue continues, holding the link as the sole owner.
      **Ran 2026-08-13, split verdict:** reconnect-on-its-own WORKS (twice — a quiet power
      cycle while idle, and a mid-drive one). "The queue continues" is REFUTED in the way that
      matters: the queue does not pause during the outage — it executes into the dead link and
      is empty by reconnect (see item C's link-drop entry). Fix direction: tick loop pauses on
      disconnect, queue resumes after reconnect.

---

## E. Compound `0xB6` poses — more than one joint per frame

**What:** whether a single `0xB6` frame can drive **multiple joints at once**.
**Why unverified:** every compound pose so far was built by pulsing one joint at a time; a
multi-joint single frame was never tried.
- [x] Craft a `0xB6` frame setting several joint fields at once (`carle send ... --raw`, daemon
      stopped) and observe whether the robot moves them together or ignores/garbles it.
      **Answered 2026-08-13 (camera-verified): YES, in one proven form.** `limb=5, byte5=7` in a
      single frame raises both lateral shoulders together (reproduced ×2); `limb=6, byte5=8`
      lowers both. Byte 5 is inert alone, reversed, cross-axis, and in the forward/elbow pairs
      tried. Full observation table in `protocol-reference.md` (byte-5 update). Follow-up: retry
      the elbow pair `9,11` with a side-on camera, and probe whether waist or movement fields can
      ride along with a limb code in one frame.

---

## F. Song / story / media durations and per-track behavior

**Session note (2026-08-13):** `volume_set` (`0xB3`, level 0) was sent on hardware for the first
time; it produced **no audible change in the idle-routine sounds**. Open hypothesis (operator's):
it may govern the A2DP speaker / media playback path rather than the robot's own chirps. Retest
levels 0/1/2 against `0xB3` media playback specifically when measuring durations below.

**What:** the `0xB3` media library plays songs/stories/dances/gymnastics; the `0xB2` Music tab
plays melody snippets.
**Why unverified:** individual clip **lengths** were never measured (the room mic was too far /
too faint to time them), and this timing is the blocker for the media quiet-window (item C).
- [ ] With a quiet room and the mic close, trigger each media category (`carle queue media:...`)
      and **time how long each plays**. Record representative durations.
- [ ] Re-confirm the "no per-track select" finding: fire the same `index` repeatedly and confirm the
      robot still cycles different items (already observed once; a second pass strengthens it).
- [ ] Confirm there is still **no BLE playback-end signal** on the notify characteristic during a
      long clip (needed to know the daemon can only go quiet for a fixed window, not wait for end).

---

## G. Observe loop — the autonomous camera-in-the-loop mapping

**What:** `carle observe` drives each arm code, watches via webcam, and writes what it reproduces.
**Why unverified without hardware:** it needs the robot **and** a camera pointed at it. PR #18 also
added a per-code connectivity/battery re-check (halts a run if the robot drops mid-sweep) — validate
that halts correctly live.
- [x] Run `carle observe --dry-run` first to see the plan (no hardware). Then run it for real under
      the orchestrating agent with the robot + camera, and confirm it drives, captures, and records.
      **Scoped live run 2026-08-13 (gesture:5, agent as judge/writer):** the cycle works —
      drive through the daemon, capture, judge, retry, record. Three operational findings:
      (1) the default 1 fps / 6-frame sampling **missed the gesture animation entirely** in two
      full rounds — canned `0xB2` gestures are too fast for it; a 30 fps clip with
      scene-detect frame extraction caught the motion. (2) Anything animated in the background
      (here: a projector on the whiteboard) triggers false scene changes — motion detection
      must be cropped to the robot's region. (3) The gesture:5 motion caught on camera was a
      **small** left-arm out/up excursion, far shallower than the full lateral raise the
      reference records from the earlier session — logged as uncertain, not a doc edit;
      redrive it hard (3× pulse) when the variation ladder exists in code.
- [ ] Confirm the mid-run halt: drop the link partway through and check the run stops with
      "stopped after N codes," rather than recording garbage for the rest.
- [ ] **Variation ladder (feature gap, deferred in PR #18):** `drive_code`/`capture_frames` do not
      yet implement the brighter/longer/raise-first variations `observe/loop.py` documents. Deciding
      the right brightness/duration bumps and the raise-first pre-pose wants the camera + robot in
      the loop. **Design input from the 2026-08-13 scoped run:** the first ladder rung should be
      *sampling density*, not brightness — capture at full rate and extract frames by scene change
      **cropped to the robot's region** (background screens/projectors false-trigger otherwise);
      1 fps sampling provably misses whole gesture animations. Add a repeat-pulse rung (≈3×) for
      under-extending servos before concluding a motion is shallow.

---

## H. Firmware / OTA / DFU  ⟵ blocked, lowest priority

**What:** the OTA/DFU update stack (Realtek `RTL8763B`) is documented from the app.
**Why blocked:** the firmware image lives behind a **geo-fenced vendor server** (`d.ihunuo.com`);
`tools/fw_probe.py` is the probe. This needs the vendor endpoint reachable and is not routine.
**New wrinkle (2026-08-13):** the OTA interface service (`0xD0FF`, incl. the `0xFFD1` DFU-reboot
characteristic) is absent from the dev unit's GATT table over a normal connection (item B), so
the documented way into DFU mode is itself unconfirmed to exist on this unit.
- [ ] Only if the endpoint is reachable: probe with `tools/fw_probe.py` and record what the DFU
      surface actually accepts. Treat as research, not a routine validation.

---

## Recording a result

- **A command becomes `confirmed` only after a real send + observation:**
  ```bash
  uv run carle send <id> --address <address>
  uv run carle confirm <id> --behavior "what the robot actually did, in your words"
  ```
  The invariant suite (`tests/test_table_invariants.py`) re-derives the same judgement from the
  committed log, so a promotion nobody earned fails CI. See [`CONTRIBUTING.md`](../CONTRIBUTING.md).
- **For non-command findings** (battery reads, durations, resume behavior, races), update
  `docs/protocol-reference.md` and the relevant checkbox/caveat here and in the README status table.
- **For the deferred code follow-ups** (items C, G), once you have the live observation, the fix +
  its regression test can be written against what you saw — not guessed at.

_Last updated at the end of the pre-hardware session: speak service (PRs #16, #17), core hardening
(#18), and codec/protocol property tests (#19 + follow-up) are all merged and green; none of it has
been run against the robot yet._
