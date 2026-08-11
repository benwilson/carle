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
