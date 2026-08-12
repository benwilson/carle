# Movement vocabulary

This is the layer above the [protocol reference](protocol-reference.md). The reference says
which **byte** produces a motion; this says what that motion **looks like**, and how to
sequence motions into moves a person would name — "wave", "fist pump", "sway" — without
hurting the hardware.

It is a living document. Descriptions are marked with how well they are known:
**(confirmed)** watched directly and agreed on, **(observed once)** seen in the first
hardware session and not re-checked, **(inferred)** predicted from a mirror joint or the
decompile but not watched. Tighten these as the robot is driven.

## How motion is produced

Every movement rides on the `0xB6` frame — payload `[mode, speed, direction, waist, limb, p5]`
— streamed to the robot over a held connection. Three facts shape everything below:

- **Poses hold.** A limb stays where it was put; it does not spring back. The "return" is a
  second command, not gravity.
- **The idle routine resumes** within ~1–2 s of silence, moving the same joints. To keep a
  deliberate motion clean, stream frames while it runs.
- **The robot drifts on its own** — see the reference's Autonomous behaviour note. Attribute
  only what happens within a second of a frame.

**Two families reach the arms.** The `0xB6` frame documented here drives joints directly, and a
pose *holds* — which is what this document is about. The `0xB2` programmed-sequence "hand" codes
(1-24) also move the arms, but as pre-canned *animations* that play once and do not hold; they
were mapped on hardware in the [protocol reference](protocol-reference.md#handarm-codes-1-24-on-hardware).
Rule of thumb, mirrored in the daemon's verbs: for a **held** pose use `0xB6` (`carle queue
pose:N`); for a **canned gesture** use `0xB2` (`carle queue gesture:N`). Because each `0xB6` limb
holds independently, pulsing several poses in turn builds a held multi-joint pose — left shoulder
out (`pose:5`) then right shoulder out (`pose:7`) leaves both arms held out at once.

## Servo-safe timing — the rules that matter most

The joints are small geared servos in a toy, not fast actuators. Driving them wrong makes
them squeal and, over time, wears or stalls them. The squeal is the single most important
thing learned about driving this robot. The rules:

1. **Hold, don't hammer.** Streaming the *same* pose frame every ~100 ms is gentle — the
   servo reaches its target and simply holds. What strains a servo is changing its *target*
   quickly. Streaming is fine; rapid change is not.
2. **Give each new target ~0.4–0.6 s.** Do not change a joint's commanded value more often
   than roughly every 4–6 frames. A 200 ms reversal squeals; ~0.5 s per pose is calm. This
   is the difference between a move and a grind.
3. **One joint at a time.** Do not drive two joints toward conflicting positions in
   overlapping frames. Re-raising the arm (`limb=1`) *while* bending the elbow (`limb=9`)
   makes two servos fight — that is what caused the squeal. Move a joint, let it settle,
   then the next.
4. **Don't re-drive a holding joint.** Once a pose is set and holding, stop commanding it.
   Re-asserting the same target fights the servo for no gain.
5. **Return at the same calm cadence.** For a repeated motion, alternate value ↔ return at
   the ~0.5 s pace, never faster.
6. **Locomotion moves the robot.** Any non-zero `direction` + `speed` travels across the
   floor. Keep speed modest, keep it short, stop with `direction=0, speed=0`, and give it
   floor space — not a tabletop.

A practical way to honor all of this: build a move as a list of `(pose, hold_frames)` steps,
stream each pose for its hold at 100 ms/frame, and only ever change one payload byte between
steps.

## Primitives — one joint at a time

### Limb byte (payload byte 4)

Left arm then right, odd raises / even returns.

| Value | Joint | What it looks like | Known |
|---|---|---|---|
| 1 / 2 | left arm | 1 raises the whole arm out front to ~chest height (a "handshake offer") and holds; 2 lowers it | observed once |
| 3 / 4 | right arm | mirror of 1/2 on the right | observed once |
| 5 / 6 | left shoulder | arm held straight out at chest height, swings **left↔right horizontally** — a lateral sweep, not an up/down flap | **confirmed** |
| 7 / 8 | right shoulder | mirror of 5/6 on the right, but the lateral raise **under-extends** on this unit — a weaker servo than the left's 5/6 | observed once |
| 9 / 10 | left elbow | forearm bends up/down at the elbow; with the arm already raised it bobs the fist | **confirmed** |
| 11 / 12 | right elbow | mirror of 9/10 on the right | inferred |

**The forward and lateral shoulder codes compose.** `1/2` (arm forward) and `5/6` (arm lateral)
are two independent axes of the *same* left shoulder, not either/or: hold both and the arm sits
at a diagonal, up-and-out, higher and wider than lateral alone — so a "Y" pose is reachable, and
each shoulder has two degrees of freedom plus the elbow. Same on the right with `3/4` + `7/8`.
This came from the camera-in-the-loop pose experiment and corrects the earlier read that treated
the two as separate joints. See the [protocol reference](protocol-reference.md).

### Waist byte (payload byte 3)

| Value | What it looks like | Known |
|---|---|---|
| 1 | leans/bends left at the waist and holds | observed once |
| 2 | returns upright | observed once |

Alternating 1 ↔ 2 at the calm cadence = a side-to-side torso sway.

### Locomotion (mode, direction, speed)

- **mode** (byte 0): 1 walks (steps), 2 slides (rolls without stepping).
- **direction** (byte 2): eight octants, counter-clockwise from RIGHT — 1 right, 2 up-right,
  3 up/forward, 4 up-left, 5 left, 6 down-left, 7 down/back, 8 down-right; 0 = no travel.
  Direction makes the robot **travel** that heading; it is not a confirmed pivot-in-place.
- **speed** (byte 1): the app uses 0–100 (and caps there); the byte holds 0–255 and 120
  moved the robot. Higher is faster.

## Named moves — the natural-language layer

What a person would say → how to build it, at servo-safe cadence (each step ≥ ~0.5 s, one
byte changing at a time). These names are the source for the control-plane daemon's macro
registry (`src/carle/daemon/moves.py`): each expands to a servo-safe primitive sequence the
engine streams, so `carle queue wave` runs the row below. A new move — the still-unmapped
leg-forward code, say — drops in as one registry entry without touching the engine.

| Say | Build | Known |
|---|---|---|
| **fist pump** | `limb=1` (raise, hold ~0.5 s), then `limb=9` ↔ `limb=10` alternating, ~0.5 s each | **confirmed** — reads clearly as pumping a fist |
| **arm sweep** | `limb=5` ↔ `limb=6` alternating, ~0.5 s each | **confirmed** — outstretched arm swings side to side; closest thing to a "wave" |
| **both arms up** | `limb=1`, settle, then `limb=3` | inferred |
| **sway / groove** | `waist=1` ↔ `waist=2` alternating, ~0.5 s each | observed once |
| **shoulder shimmy** | `limb=5`, settle, `limb=7`, settle, repeat | inferred |
| **walk forward** | `mode=1, speed≈50, direction=3`, streamed; stop with `direction=0, speed=0` | confirmed |
| **glide / slide forward** | `mode=2, speed≈60, direction=3` | confirmed |
| **turn / spin** | sweep `direction` around while `mode=2` — **uncertain**, tends to travel in an arc rather than pivot | unresolved |
| **do a little dance** | trigger `media_music` (`0xB3 03`) or `media_dance` (`0xB3 02`); the robot runs its own music-and-motion routine | confirmed |

### A note on "wave"

A textbook hello-wave — arm held high, hand oscillating — is **not cleanly in this robot's
range**. The arm joints give raise/lower (1/2), a horizontal shoulder sweep (5/6), and an
elbow bob (9/10). The closest to a wave is the **arm sweep** (5/6); the raised-arm + elbow
combination reads as a **fist pump** instead. When someone asks for a wave, the honest move
is the arm sweep, with a note that it is a sweep rather than a hand-wave.

## To confirm / open questions

- Right-side mirrors: 7/8 (shoulder) and 11/12 (elbow).
- A real pivot-in-place — is there any mode/direction combination, or is it simply not
  exposed on this channel?
- Whether two joints can move in one frame (limb **and** waist non-zero) for a compound
  pose, or whether the robot acts on only one joint per frame.
- How gentle is gentle: the ~0.5 s figure is a safe starting point from the squeal, not a
  measured servo spec. A move that still strains should slow down further.
