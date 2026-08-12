# The observe loop

`carle observe` is an autonomous, camera-in-the-loop harness that derives what each robot
movement command actually does — arms first — and writes what it finds into the
[protocol reference](protocol-reference.md). It assumes nothing works and re-derives every
mapping from what the camera sees.

## How one code is derived

For each code the loop runs this cycle (F1 in the [plan](plans/2026-08-12-005-feat-autonomous-movement-verification-plan.md)):

1. **Drive** the code once through the running daemon (`gesture:N` for a `0xB2` hand code,
   `pose:N` for a `0xB6` limb pose) — one pulse, never a stream, so the servos are safe.
2. **Capture** a short clip from the webcam (camera 0) and sample brightened frames. The clip
   spans the pose *before* the command through the motion settling, so a gesture that animates
   and returns to its start is still visible mid-clip.
3. **Read** the motion from the frames into a structured `Observation` (joint, motion,
   direction, confidence).
4. **Reproduce** it: derive the code again independently. A finding is written only when two
   readings agree (same joint, motion, and direction, both above a confidence floor). This is
   how the loop earns the right to edit canonical docs with no human and no retained evidence.
5. On an unreadable or disagreeing read, **retry** with a bounded variation ladder — brighter
   capture, longer capture, raise the limb first to exaggerate the motion, repeat the pulse —
   then mark the code **uncertain** rather than guessing. The loop never stalls on one code.
6. **Discard** the video and frames. Runs are ephemeral; the reference prose is the only
   durable output.

Defaults: two agreeing readings confirm; two extra retries per code; confidence floor 0.6.
Tune them with `--repeats`, `--retries`, `--codes`, and `--device`.

## What is code and what is the agent

The harness is deterministic scaffolding — capture, driver, the agreement/retry engine, the
recorder, and the CLI wiring. Two seams are **fulfilled by the orchestrating agent at run
time**:

- the **judge** — reads the sampled frames and returns the `Observation`;
- the **writer** — applies a confirmed finding to `docs/protocol-reference.md` prose (or an
  "uncertain" note), overwriting an existing entry when the reproduced reading disagrees with it.

So a real run is an agent session against a charged robot, a running daemon, and camera 0 aimed
at it with enough light. There is no headless judge: `carle observe` without an agent errors and
points you at `--dry-run`, which lists the code set and planned loop and touches no hardware.
Every committed test uses fakes for capture, the daemon, the judge, and the writer — CI never
opens a camera, drives a robot, or starts a daemon.

## Preconditions for a live run

- The daemon is running and holding the link (`carle daemon start <address>`); the observe loop
  drives through it and reads its `status` first, halting cleanly if the robot has dropped or its
  battery is dead.
- Camera 0 is aimed at the robot with enough light (the loop brightens frames, but framing and
  light still matter).

Scope: the first run covers the arms (`0xB6` poses 1-12, `0xB2` gestures 1-24). The same loop
generalizes to the other movement families later by adding to the code set.
