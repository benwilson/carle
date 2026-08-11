# Pending observations

**These are not evidence and nothing here is published.** They are hardware observations
taken faster than the schema can currently record them.

`protocol/commands.yaml` stores one `observed_behavior` and one `observed_parameters` per
entry. A parameterized command like `move_rocker` spans a whole parameter space, and a
single observation cannot describe it — walking forward and raising an arm are the same
command at different bytes. The multi-observation model that fixes this is planned but not
built, so these are held here until it lands, each paired with the send log that backs it.

Nothing in this file has passed the honesty gate. Treat it as a notebook.

## move_rocker

| Parameters | Log | What the robot did |
|---|---|---|
| `direction=3 speed=50` | `move_rocker-20260811T211646910616Z.log` | Walked forward, then stopped by itself. Recorded on the entry. |
| `limb=1` | `move_rocker-20260811T212202450866Z.log` | Left arm raised, as though offering a handshake. The arm STAYS raised — these are poses, not gestures. |
| `limb=2` | `move_rocker-20260811T212254951678Z.log` | *(not separately seen — implied as the left-arm return by the 3/4 pairing)* |
| `limb=3` | see 21:23-21:24 logs | Right arm raised to the same handshake position `limb=1` gives the left. |
| `limb=4` | see 21:23-21:24 logs | The right-arm return. Alternating 3/4/3 moved the arm up, down, up. |
| `limb=5` | see 21:25 logs | LEFT SHOULDER — lateral raise, described as flapping like a bird. Not the elbow: the distinction from `1`/`2` is the axis of motion, not how far the arm travels. |
| `limb=6` | see 21:25 logs | The return for 5; alternating 5/6/5 produced the flapping motion. |
| `limb=7` | see 21:27 logs | RIGHT SHOULDER — lateral raise, mirroring 5/6. Confirmed by alternating 7/8 on a loop: the right arm lifts and lowers, flapping. |
| `limb=8` | see 21:27 logs | The return for 7. |
| `limb=9` | see 21:29 logs | ELBOW bend — alternating 9/10 produced a handshake motion at the forearm. Side not stated by the observer; the left-then-right pattern predicts left, and 11/12 should settle it. |
| `limb=10` | see 21:29 logs | The return for 9. |
| `limb=11` | see 21:31 logs | RIGHT elbow bend, mirroring 9/10 — which retroactively settles that 9/10 was the left. |
| `limb=12` | see 21:31 logs | The return for 11. |

**The limb selector is fully mapped.** Six pairs, left then right through each
articulation, odd raising and even returning: arms forward (1-4), shoulders laterally
(5-8), elbows bending (9-12). Every value watched on hardware.

## media_music

| Parameters | Log | What the robot did |
|---|---|---|
| *(defaults, index 0)* | `media_music-20260811T210822337016Z.log` | Began "Old MacDonald Had a Farm", with dancing. Recorded on the entry. |
| `index=1` | `media_music-20260811T210912436333Z.log` | Began the ABC song, with dancing. |
| `index=2` | `media_music-20260811T211055517492Z.log` | Began "We Wish You a Merry Christmas", with dancing. |

## Robot behaviour, not a command

- **Limb commands set a pose.** The joint holds its new position rather than returning to
  rest. Seen at `limb=1`.
- **Limb values are paired, odd raises and even returns.** `1`/`2` is the left arm, `3`/`4`
  the right. This matches the decompiled handler split, where odd values 1,3,5,7,9,11 and
  even values 2,4,6,8,10,12 are bound to two separate groups of on-screen controls. Six
  pairs for six joints; vendor copy mentions arm, shoulder and elbow articulation.
  REVISED: the pairs differ by AXIS, not by joint distance. `1`/`2` raises the arm forward
  (a handshake reach); `5`/`6` raises it laterally from the shoulder (flapping). An earlier
  reading called `5`/`6` the elbow — wrong, corrected by watching the robot. If `7`/`8`
  mirrors `5`/`6` on the right, the layout is left-then-right through each articulation, and
  `9`-`12` are most likely the elbows, which nothing has moved yet.
- **Commands queue and execute in order.** Three sends about a second apart ran as three
  distinct movements rather than the last one winning.
- **Sending back to back denies the idle routine its window.** This is the practical way to
  run a sweep — a lone command is overwritten before you can describe it.
- **The idle routine resumes almost immediately** after a command finishes, and it moves the
  limbs, so a pose is overwritten within a second or two. This is the practical limit on
  observation: watch the instant the frame lands or you are describing the idle routine.
  Untested idea — sending commands back to back may deny it a window.

## move_rocker payload byte 3 (`p3`)

| Parameters | What the robot did |
|---|---|
| `p3=1` / `p3=2` alternating | Leaned to the left, bending slightly at the WAIST. A joint the limb selector does not reach — the seventh motor drive of the nine Ruko publishes. Follows the same convention as the limb byte — odd acts, even returns. |
| `p3=1` alone | Leans left at the waist, and holds. |
| `p3=2` alone | Returns upright. Confirmed by the observer that the command did this, not their hand. |

## move_rocker payload byte 5 (`p5`)

| Parameters | What the robot did |
|---|---|
| `p5` = 1, 2, 3, 8, 64, 128, 255 | No movement of any kind, across two separate sweeps. The byte the app never writes appears to produce no motion. |
| — | The observer noted the LED face and ears changing during the sweep, but flagged that both do so during idle as well, so the activity is NOT attributable. Untested hypothesis: `p5` drives the face/ear display, which nothing else in the movement frame reaches. Tested by alternating 1 and 255 twelve times: the display did not track the send rhythm. Hypothesis unsupported. |
| `p5=1` held 60s | 22 back-to-back sends over a full minute, which also suppresses the idle routine for the whole window. Nothing. Across four separate methods — value sweep, sustained bursts, extreme alternation, and a held minute — this byte has no observable effect. |

## move_rocker payload byte 0 (`mode`)

Ten sends at each mode, identical `direction=3` and `speed=120`, five-second gap between.
Attribution is by the order the observer reported, which matched the order sent.

| Parameters | What the robot did |
|---|---|
| `mode=0 direction=3 speed=50` | Walked forward. (Earlier session.) |
| `mode=1 direction=3 speed=120` | Steps FORWARD, leading with the LEFT foot. An earlier reading of this as a turn in place was withdrawn by the observer and is wrong. Twenty sends at speed 120 settled it: the robot travels rather than rotating. |
| `mode=2 direction=3 speed=120` | Moves forward in small jerky advances rather than recognisable steps. Twenty sends. No clear leading foot. |

NOT CHARACTERISED, after three attempts. What holds: every value tested moves the robot
forward, so the byte does not choose between rotating and travelling. What does not hold: a
first reading as rotate-versus-travel, withdrawn by the observer; and a leg-selector
hypothesis from the left foot leading at mode 1, unsupported once mode 2 produced small jerky
advances rather than a right-foot lead.

The difference is real but too fine for eye observation across a room. What would settle it is
a measurement rather than a description: mark a start line, run twenty sends at one mode,
measure the distance travelled, repeat for the other. Distance per send is objective in a way
that 'steps' versus 'jerks' is not. Retesting with a long run of sends, where displacement and facing diverge
obviously. A rotation hypothesis was predicted before testing, from a photograph of the 2.4 GHz remote: it carries two
four-way pads, one centred on a walking figure and the other on rotation arrows. The app
writes 1 or 2 into this byte, which is the two pads.
