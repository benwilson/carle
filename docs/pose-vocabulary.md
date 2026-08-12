# Pose vocabulary — human-interaction poses

This is the layer above [movement-vocabulary.md](movement-vocabulary.md): that document maps
each byte to a motion, this one composes those motions into **held body poses a robot would
strike toward a person** — waving, celebrating, offering a hand. It was built and verified with
the camera-in-the-loop [`carle observe`](observe-loop.md) loop: each pose was struck on hardware,
recorded, and read back against intent.

## The arm model (what the robot can actually hold)

Each arm is a **3-DOF system** — three independently-driven, independently-holding joints that
**compose**, so an arm's reachable poses are the *product* of the three axes, not a choice
between them:

| Axis | Left codes | Right codes | Motion |
|---|---|---|---|
| Shoulder — forward | `1` / `2` | `3` / `4` | raises the arm out in front (flexion) |
| Shoulder — lateral | `5` / `6` | `7` / `8` | raises the arm out to the side (abduction) |
| Elbow | `9` / `10` | `11` / `12` | bends the forearm up |

Odd raises, even returns. Driving forward **and** lateral together holds the arm at a diagonal,
up-and-out (higher and wider than either alone) — this is what makes a "Y" pose reachable and is
why the two are separate axes, not one joint. Add the elbow and the arm reaches a high, out, and
bent configuration. Full derivation in the [protocol reference](protocol-reference.md).

### Limits and quirks (learned on hardware)

- **No overhead, no behind, no wrist/hand.** The shoulders top out around chest/shoulder height,
  the arms don't reach behind the torso, and there is no wrist or finger articulation. So
  "arms up in celebration" is chest-height-forward, and "salute" is "hand near the head," not a
  crisp hand-to-forehead.
- **The right lateral servo is weak.** Code `7` (right shoulder lateral) under-extends on this
  unit — a servo asymmetry, not a code difference — so right-side lateral and diagonal poses come
  out shallower than the left.
- **The waist can't hold a pose.** The torso lean (`waist` byte) is spring-return: it stays
  leaned only while actively driven and snaps upright when the command clears. So **held body
  poses are arms-only**; the waist is for transient sway, not posture.

## Building a held pose

- **Use `0xB6` poses (`pose:N`), not `0xB2` gestures.** `pose:N` holds; `gesture:N` plays a
  canned animation and doesn't hold. Compose a pose by pulsing several `pose:N` joints in turn —
  each holds independently.
- **Drive each joint hard.** A single brief pulse under-extends the servo, and earlier joints
  relax while a later one is driven. Repeat each joint's pulse (≈3×) to reach full extension,
  then re-assert every joint once more as a lock-in pass so the whole compound pose settles.
- **Start from a clean rest.** Pulse `gesture:19` (both arms down) first, then assemble.

## The 20 interaction poses

Each pose is a set of held `pose:N` codes, built from a reset-down. "Reads as" is what the camera
verified the robot actually strikes.

| Pose (toward a human) | Codes | Reads as |
|---|---|---|
| Wave Hello | `3, 11` | right hand raised up |
| Wave Goodbye | `1, 9` | left hand raised up |
| Celebration "Arms Up!" | `1, 3` | both arms up to shoulder height |
| Victory "Arms Wide" | `5, 7` | both arms straight out to the sides (the crispest) |
| Handshake Offer | `3` | right arm extended forward |
| Reach Out "take my hand" | `1` | left arm extended forward |
| High Five | `7, 11` | right hand up and out |
| Applause / Clap | `1, 3, 9, 11` | both forearms up in front |
| Shrug "I dunno" | `5, 7, 9, 11` | arms out, forearms up (palms-up shrug) |
| Directing "this way" | `1, 7` | left arm forward (stop), right arm out (go) |
| Presenter "Ta-da" | `5, 3` | left arm out, right arm forward |
| "Let's Dance!" | `1, 7, 11` | left forward, right up-and-out — asymmetric diagonal |
| Dab | `5, 9, 3` | left arm up-out, right arm across (approximate) |
| Fist Bump | `1, 3, 11` | arms forward, right fist out (approximate) |
| Robot Wave (mechanical) | `1, 9, 7` | left arm bent at a right angle, right arm out |
| YMCA "Y" | `5, 1, 7, 3` | arms diagonal, up-and-out in a broad V |
| Flex "I'm strong" | `5, 9` | left arm out, elbow bent (single bicep) |

### Poses the robot can't tell apart

Because several social gestures use the **same joints**, the robot strikes them identically —
the distinction is human, not mechanical:

- **Come Here** (`3, 11`) is identical to **Wave Hello**.
- **The Thinker** (`1, 9`) is identical to **Wave Goodbye** (hand up near the face, not to the chin).
- **Salute** (`7, 11`) is identical to **High Five** (hand up-and-out, not to the forehead).

This is the honest ceiling of the robot's social vocabulary: expressive from the shoulders and
elbows, chest-height and out — roughly a dozen distinct arm configurations, each of which can
carry several human meanings depending on context.
