# Ruko 1088 — BLE Protocol Reference

**Nothing in this document describes the protocol yet.** The structure below is in place and
the command table is real, but every capability is still unmapped. See
[`docs/method.md`](method.md) for how the missing content gets produced.

This document is partly generated. The command table is rendered from
[`protocol/commands.yaml`](../protocol/commands.yaml) by
`scripts/generate_reference.py`; everything else is hand-written. Do not edit inside the
generated markers — edit the YAML and regenerate.

## Transport

The robot advertises over Bluetooth Low Energy under a name beginning `JT_`. Control uses a
single vendor service with a write characteristic and a notify characteristic:

| Role | UUID |
|---|---|
| Service | `0000AE00-0000-1000-8000-00805f9b34fb` |
| Write (commands) | `0000AE01-0000-1000-8000-00805f9b34fb` |
| Notify (state) | `0000AE02-0000-1000-8000-00805f9b34fb` |

These are not hardcoded in the app. They arrive as `serviceid`, `characteristicid` and
`notifyid` meta-data entries on the controlling activity in `AndroidManifest.xml`, which the
app reads at startup and hands to its command manager. That indirection is worth knowing: a
sibling app from the same publisher could ship a different robot by changing three manifest
values and nothing else.

Writes are split into chunks of at most twenty payload bytes, and the app waits for each
write's callback before sending the next.

**Not yet documented.** What the notify characteristic reports, and whether the robot sends
anything unprompted. Nothing in the app's send path reads it back.

## Frame format

Every command shares one envelope:

```
[0]          family      which command group this belongs to
[1]          N           payload length
[2 .. N+1]   payload     first byte is the sub-command
[N+2]        checksum    sum of the payload bytes, truncated to 8 bits
[N+3]        terminator  always 0xAA
```

Total frame length is always `N + 4`.

Four families appear in the app, one per control screen:

| Family | Purpose |
|---|---|
| `0xB2` | User-programmed command sequences |
| `0xB3` | Media triggers and volume |
| `0xB5` | Gyro / tilt control |
| `0xB6` | Movement and limb articulation |

The checksum covers the payload only — not the family byte, not the length, not itself. The
app computes it inline at each send site rather than in a shared helper, which is why the same
arithmetic appears once per screen.

Movement frames (`0xB6`) carry six payload bytes:

| Byte | Meaning |
|---|---|
| 0 | mode — 1 walks, 2 slides |
| 1 | speed |
| 2 | direction, 1-8 |
| 3 | waist — 1 leans left, 2 returns upright |
| 4 | limb selector, 1-12 |
| 5 | no observable effect; see below |

Direction runs counter-clockwise from 1 at RIGHT, so 3 is UP, 5 is LEFT and 7 is DOWN, with 0
meaning no movement. Setting a direction and a speed makes the robot move; it takes its steps
and stops on its own.

Byte 0 selects the movement mode. At 1 the robot walks, taking steps. At 2 it slides, rolling
forward without stepping. The direction byte applies to either.

That distinction is the vendor's own: Ruko's product copy describes "walking and sliding in
multiple directions", and this byte is what chooses between them. Value 0 walked in an earlier
run but has not been compared against the other two directly.

Two earlier readings of this byte were published and withdrawn — first as rotating versus
travelling, then as selecting which leg leads. Both came from watching a gait across a room,
which does not distinguish reliably. Stepping versus rolling does.

The app writes 1 or 2 here and never 0. The vendor's 2.4 GHz remote carries two separate
four-way pads, which is consistent with two movement modes, though a remote is a different
radio and its buttons are not a measurement.

The limb selector takes twelve values arranged as six pairs, running left then right through
each articulation. Odd values raise and even values return:

| Values | Joint | Motion |
|---|---|---|
| 1, 2 | Left arm | forward raise, a reaching motion |
| 3, 4 | Right arm | forward raise |
| 5, 6 | Left shoulder | lateral raise, a flapping motion |
| 7, 8 | Right shoulder | lateral raise |
| 9, 10 | Left elbow | bend, a handshake motion |
| 11, 12 | Right elbow | bend |

A limb holds its new position rather than springing back. Sending values in quick succession
runs them in order, which is also the only practical way to watch one — the robot's idle
routine resumes within a second or two and moves the same joints. Back-to-back sends deny that
routine its window, which is how the negative result on byte 5 below was obtained.

Eleven of the twelve values were watched directly. Value 2 was not: it is inferred as the
left-arm return from the way 3 and 4 pair on the right. A log for it exists, but the
observations table below carries no row for it, because an inference behind an evidence link
reads like something someone saw.

Nothing in the decompiled app explains these twelve numbers; it binds twelve on-screen buttons
to them and stops there. The mapping above comes from watching the robot.

The waist byte follows the same convention as the limb selector: an odd value acts, an even
value returns. It reaches a joint nothing else does, which brings the count of separately
addressable articulations to seven — six in the arms, one at the waist. Ruko publishes nine
motor drives; the remaining two are presumably in the legs, which the direction and speed
bytes drive together rather than individually.

Byte 5 produced no effect under four separate methods: a sweep across small, mid and maximum
values, sustained bursts of a single value, alternation between the extremes, and a full
minute held down with the idle routine suppressed throughout. That is not proof the byte is
dead — it may need a mode nothing here sets, or act on something with no outward sign — but
it is a thorough negative result, and worth recording so the next person does not repeat it.

**Not yet documented.** The gyro family's payload layout and the programmed-sequence format.
Also unknown: how to stop the robot. Nothing decoded here halts anything, though the remote
carries an unexamined centre key that may do it.

## Audio channel

*Not documented.*

The robot is understood to expose a Bluetooth audio sink separate from its BLE control link,
pairable from ordinary system Bluetooth settings. **That understanding comes from a marketplace
listing, not from vendor documentation or observation, and it has not been checked.** Whether
the two channels are genuinely independent — and so whether a program can drive motion while
audio plays — is an open question, and one of the more interesting ones.

## Autonomous behaviour

**The robot acts without being told to.** Left alone, with nothing written to it, it begins
playing music, moving and talking by itself after a delay. This was checked directly: a
quiet robot, no traffic on the wire, and it started up anyway.

This matters more for reading this document than for writing a client. Anything the robot
does more than a few seconds after a frame arrives may be its own idea rather than a
response, and telling the two apart from across a room is not reliable. An early entry here
recorded a command as continuing to produce content for minutes; it was the robot's idle
routine, and the entry has been narrowed to what happened immediately.

So the working rule for anyone adding to this reference: attribute only what happens within
a few seconds of a send, and say so when the timing was loose. Movement is easy — the robot
takes its steps and stops. Audio is harder, because the idle routine plays the same content
a command does.

Nothing is known yet about what triggers the idle routine, how long it waits, or whether a
frame exists that suppresses it.

## Command table

The table below lists the robot's known capabilities and the verification state of each. It is
generated; see [`protocol/commands.yaml`](../protocol/commands.yaml) for the source.

<!-- BEGIN GENERATED COMMAND TABLE -->

> **Coverage note.** This table's row set is seeded from vendor-published
> capability counts, not from the protocol. It is not a complete list of
> protocol commands.
>
> Row set and status as of the first hardware session (2026-08-11), following the first
> decompile of the Carle Android app (base.apk, com.ihunuo.jtlrobot). That session's
> findings are recorded per observation on each entry rather than one per command: the
> movement frame alone was watched at two dozen points of its parameter space.
>
> Hardware corrected the decompile on two points.
>
> The second payload byte selects a track, and the app never uses it. Every send site in
> the app hardcodes 0 there, so this was recorded as an open question — hardware settled
> it: index 0, 1 and 2 each begin with a different song. Those tracks are reachable from
> the protocol but not from the vendor's own app.
>
> The robot acts unprompted. It enters pre-programmed idle behaviour on its own after a
> delay, which is a confound for every observation in this file. Content that arrived
> minutes after a send was initially read as playback continuing; it cannot be attributed
> to the command, and the media_music entry has been narrowed to what was seen immediately.
> Any observation taken more than a few seconds after a send should be treated as suspect
> until the idle behaviour is characterised. Movement, by contrast, was one-shot: the robot
> walked and stopped by itself.
>
> What the decompile established, unchanged. The vendor's published capability counts do
> not map one-to-one onto protocol commands: the app exposes a single trigger per media
> category, so the ten songs, eight dance tracks, four stories and two gymnastic routines
> are four parameterized commands rather than twenty-four. The originals are retained as
> `unlocated` with `superseded_by` rather than deleted, so the collapse stays traceable.
> Note this now reads differently than when it was written — the tracks ARE individually
> addressable, just through one command's parameter rather than through separate commands.
>
> Movement collapsed the same way: one parameterized command with an 8-way direction
> field, not six directional commands.
>
> The fourteen voice commands appear nowhere in the BLE layer. They look like onboard
> speech recognition with no protocol surface at all, so they are `unlocated` — searched
> for, not found — rather than awaiting a frame that may not exist.
>
> Counts asserted by the invariant suite are floors, from Ruko's published
> specifications: 6 movement, 10 songs, 8 dance tracks, 2 gymnastic routines, 4 stories,
> 14 voice commands. Superseded rows still count toward them.
>
> Ruko also advertises "200 programmable commands by app". The decompile supports the
> working interpretation that this is user-composed sequence capacity rather than
> distinct opcodes: CustomControlActivity assembles sequences from the same command
> family (0xB2). UNCONFIRMED.
>
> Open, and cheap to settle with the robot in hand: whether `index` wraps past the
> published track count, what values above it do (the declared range is the full byte
> because nothing establishes a narrower one), and whether the other three media
> categories address tracks the same way.

**50 entries:** 44 unlocated, 3 decoded, 3 confirmed.

### Movement

| ID | Capability | Status | Frame at defaults | Observations | Evidence |
|---|---|---|---|---|---|
| `move_forward` | Walk forward (superseded by move_rocker) | unlocated | — | — | — |
| `move_backward` | Walk backward (superseded by move_rocker) | unlocated | — | — | — |
| `turn_left` | Turn left (superseded by move_rocker) | unlocated | — | — | — |
| `turn_right` | Turn right (superseded by move_rocker) | unlocated | — | — | — |
| `slide_left` | Slide left (superseded by move_rocker) | unlocated | — | — | — |
| `slide_right` | Slide right (superseded by move_rocker) | unlocated | — | — | — |
| `move_rocker` | Drive movement and limbs; direction 1-8 counter-clockwise from RIGHT | confirmed | `B6 06 00 00 00 00 00 00 00 AA` | 23 observed, 1 withdrawn — [see below](#move_rocker) | 206 sends logged |

Parameters. The frame above is shown at each parameter's default.

| Command | Parameter | Range | Default |
|---|---|---|---|
| `move_rocker` | `mode` | 0–2 | 0 |
| `move_rocker` | `speed` | 0–255 | 0 |
| `move_rocker` | `direction` | 0–8 | 0 |
| `move_rocker` | `p3` | 0–2 | 0 |
| `move_rocker` | `limb` | 0–12 | 0 |
| `move_rocker` | `p5` | 0–255 | 0 |

### Songs

| ID | Capability | Status | Frame at defaults | Observations | Evidence |
|---|---|---|---|---|---|
| `song_01` | Song 1 of 10 (title not yet identified) (superseded by media_music) | unlocated | — | — | — |
| `song_02` | Song 2 of 10 (title not yet identified) (superseded by media_music) | unlocated | — | — | — |
| `song_03` | Song 3 of 10 (title not yet identified) (superseded by media_music) | unlocated | — | — | — |
| `song_04` | Song 4 of 10 (title not yet identified) (superseded by media_music) | unlocated | — | — | — |
| `song_05` | Song 5 of 10 (title not yet identified) (superseded by media_music) | unlocated | — | — | — |
| `song_06` | Song 6 of 10 (title not yet identified) (superseded by media_music) | unlocated | — | — | — |
| `song_07` | Song 7 of 10 (title not yet identified) (superseded by media_music) | unlocated | — | — | — |
| `song_08` | Song 8 of 10 (title not yet identified) (superseded by media_music) | unlocated | — | — | — |
| `song_09` | Song 9 of 10 (title not yet identified) (superseded by media_music) | unlocated | — | — | — |
| `song_10` | Song 10 of 10 (title not yet identified) (superseded by media_music) | unlocated | — | — | — |
| `media_music` | Trigger the songs | confirmed | `B3 02 03 00 03 AA` | 3 observed — [see below](#media_music) | 3 sends logged |

Parameters. The frame above is shown at each parameter's default.

| Command | Parameter | Range | Default |
|---|---|---|---|
| `media_music` | `index` | 0–255 | 0 |

### Dance tracks

| ID | Capability | Status | Frame at defaults | Observations | Evidence |
|---|---|---|---|---|---|
| `dance_01` | Dance track 1 of 8 (title not yet identified) (superseded by media_dance) | unlocated | — | — | — |
| `dance_02` | Dance track 2 of 8 (title not yet identified) (superseded by media_dance) | unlocated | — | — | — |
| `dance_03` | Dance track 3 of 8 (title not yet identified) (superseded by media_dance) | unlocated | — | — | — |
| `dance_04` | Dance track 4 of 8 (title not yet identified) (superseded by media_dance) | unlocated | — | — | — |
| `dance_05` | Dance track 5 of 8 (title not yet identified) (superseded by media_dance) | unlocated | — | — | — |
| `dance_06` | Dance track 6 of 8 (title not yet identified) (superseded by media_dance) | unlocated | — | — | — |
| `dance_07` | Dance track 7 of 8 (title not yet identified) (superseded by media_dance) | unlocated | — | — | — |
| `dance_08` | Dance track 8 of 8 (title not yet identified) (superseded by media_dance) | unlocated | — | — | — |
| `media_dance` | Trigger the dance tracks | decoded | `B3 02 02 00 02 AA` | — | derived: `OtherActivity.onClick, R.id.dance_btn — decompiled from base.apk (com.ihunuo.jtlrobot)` |

Parameters. The frame above is shown at each parameter's default.

| Command | Parameter | Range | Default |
|---|---|---|---|
| `media_dance` | `index` | 0–255 | 0 |

### Gymnastic routines

| ID | Capability | Status | Frame at defaults | Observations | Evidence |
|---|---|---|---|---|---|
| `gymnastic_01` | Gymnastic routine 1 of 2 (name not yet identified) (superseded by media_gymnastics) | unlocated | — | — | — |
| `gymnastic_02` | Gymnastic routine 2 of 2 (name not yet identified) (superseded by media_gymnastics) | unlocated | — | — | — |
| `media_gymnastics` | Trigger the gymnastics routines | decoded | `B3 02 00 00 00 AA` | — | derived: `OtherActivity.onClick, R.id.ticao_btn — decompiled from base.apk (com.ihunuo.jtlrobot)` |

Parameters. The frame above is shown at each parameter's default.

| Command | Parameter | Range | Default |
|---|---|---|---|
| `media_gymnastics` | `index` | 0–255 | 0 |

### Stories

| ID | Capability | Status | Frame at defaults | Observations | Evidence |
|---|---|---|---|---|---|
| `story_01` | Story 1 of 4 (title not yet identified) (superseded by media_story) | unlocated | — | — | — |
| `story_02` | Story 2 of 4 (title not yet identified) (superseded by media_story) | unlocated | — | — | — |
| `story_03` | Story 3 of 4 (title not yet identified) (superseded by media_story) | unlocated | — | — | — |
| `story_04` | Story 4 of 4 (title not yet identified) (superseded by media_story) | unlocated | — | — | — |
| `media_story` | Trigger the stories | confirmed | `B3 02 01 00 01 AA` | 1 observed — [see below](#media_story) | 1 send logged |

Parameters. The frame above is shown at each parameter's default.

| Command | Parameter | Range | Default |
|---|---|---|---|
| `media_story` | `index` | 0–255 | 0 |

### Voice commands

| ID | Capability | Status | Frame at defaults | Observations | Evidence |
|---|---|---|---|---|---|
| `voice_01` | Voice command 1 of 14 (phrase not yet identified) | unlocated | — | — | — |
| `voice_02` | Voice command 2 of 14 (phrase not yet identified) | unlocated | — | — | — |
| `voice_03` | Voice command 3 of 14 (phrase not yet identified) | unlocated | — | — | — |
| `voice_04` | Voice command 4 of 14 (phrase not yet identified) | unlocated | — | — | — |
| `voice_05` | Voice command 5 of 14 (phrase not yet identified) | unlocated | — | — | — |
| `voice_06` | Voice command 6 of 14 (phrase not yet identified) | unlocated | — | — | — |
| `voice_07` | Voice command 7 of 14 (phrase not yet identified) | unlocated | — | — | — |
| `voice_08` | Voice command 8 of 14 (phrase not yet identified) | unlocated | — | — | — |
| `voice_09` | Voice command 9 of 14 (phrase not yet identified) | unlocated | — | — | — |
| `voice_10` | Voice command 10 of 14 (phrase not yet identified) | unlocated | — | — | — |
| `voice_11` | Voice command 11 of 14 (phrase not yet identified) | unlocated | — | — | — |
| `voice_12` | Voice command 12 of 14 (phrase not yet identified) | unlocated | — | — | — |
| `voice_13` | Voice command 13 of 14 (phrase not yet identified) | unlocated | — | — | — |
| `voice_14` | Voice command 14 of 14 (phrase not yet identified) | unlocated | — | — | — |

### Device commands

| ID | Capability | Status | Frame at defaults | Observations | Evidence |
|---|---|---|---|---|---|
| `volume_set` | Set volume; payload byte 2 cycles 0, 1, 2 | decoded | `B3 02 04 00 04 AA` | — | derived: `OtherActivity.onClick, R.id.voice_btn — decompiled from base.apk (com.ihunuo.jtlrobot)` |

Parameters. The frame above is shown at each parameter's default.

| Command | Parameter | Range | Default |
|---|---|---|---|
| `volume_set` | `level` | 0–2 | 0 |

### Observations

What the robot did, per command and per parameter set. Each row is one
behaviour watched on hardware; the frame shown is the one that was sent,
not the default. A withdrawn row is a published reading that turned out to
be wrong — kept, with its reason, so a reader can calibrate this document
against its own error rate.

#### `media_story`

| Sent at | Frame | What the robot did | Logs |
|---|---|---|---|
| defaults | `B3 02 01 00 01 AA` | After a noticeable silent gap of roughly ten seconds, began narrating a story: 'The Princess and the Pea'. Sent at index 0 from a quiet robot. Confirms the decompiled category mapping — OtherActivity's stroy_btn writes payload byte 0x01. | [1](../evidence/media_story-20260811T211406739712Z.log) |

#### `media_music`

| Sent at | Frame | What the robot did | Logs |
|---|---|---|---|
| defaults | `B3 02 03 00 03 AA` | Began "Old MacDonald Had a Farm", with dancing. Sent at the declared default index, which the app itself hardcodes. | [1](../evidence/media_music-20260811T210822337016Z.log) |
| index=1 | `B3 02 03 01 04 AA` | Began the ABC song, with dancing. | [1](../evidence/media_music-20260811T210912436333Z.log) |
| index=2 | `B3 02 03 02 05 AA` | Began "We Wish You a Merry Christmas", with dancing. CONFOUNDED: further songs played minutes later with no command on the wire, first read as playback continuing on its own — but the robot is since known to enter pre-programmed idle behaviour unprompted, so that later content cannot be attributed to this command. Only the track each index STARTS with is established. | [1](../evidence/media_music-20260811T211055517492Z.log) |

#### `move_rocker`

| Sent at | Frame | What the robot did | Logs |
|---|---|---|---|
| direction=3, speed=50 | `B6 06 00 32 03 00 00 00 35 AA` | Walked forward. Sent with mode at its default of 0, speed 50 and direction 3, which confirms the direction mapping derived from NormolContorlActivity (counter-clockwise from RIGHT=1, so 3 is up/forward) and shows that mode is not an enable — the app only ever writes 1 or 2 there, but 0 moves the robot. | [1](../evidence/move_rocker-20260811T211646910616Z.log) |
| limb=1 | `B6 06 00 00 00 00 01 00 01 AA` | Left arm raised, as though offering a handshake. The arm STAYS raised — these are poses, not gestures. | [1](../evidence/move_rocker-20260811T212202450866Z.log) |
| limb=3 | `B6 06 00 00 00 00 03 00 03 AA` | Right arm raised to the same handshake position limb 1 gives the left. Read from alternating 3/4/3, which moved the arm up, down, up. | 4 sends, [1](../evidence/move_rocker-20260811T212322911335Z.log)…[4](../evidence/move_rocker-20260811T212354260482Z.log) |
| limb=4 | `B6 06 00 00 00 00 04 00 04 AA` | The right-arm return. Read from the same 3/4/3 alternation. | [1](../evidence/move_rocker-20260811T212326061149Z.log), [2](../evidence/move_rocker-20260811T212350840245Z.log) |
| limb=5 | `B6 06 00 00 00 00 05 00 05 AA` | LEFT SHOULDER — a lateral raise, described as flapping like a bird. Not the elbow: the distinction from 1/2 is the AXIS of motion, not how far the arm travels. Read from alternating 5/6/5. | [1](../evidence/move_rocker-20260811T212422432599Z.log), [2](../evidence/move_rocker-20260811T212429931981Z.log) |
| limb=6 | `B6 06 00 00 00 00 06 00 06 AA` | The return for 5, from the same alternation. | [1](../evidence/move_rocker-20260811T212425792417Z.log) |
| limb=7 | `B6 06 00 00 00 00 07 00 07 AA` | RIGHT SHOULDER — a lateral raise mirroring 5/6. Read from alternating 7/8 on a loop: the right arm lifts and lowers, flapping. | 10 sends, [1](../evidence/move_rocker-20260811T212522431769Z.log)…[10](../evidence/move_rocker-20260811T212736412740Z.log) |
| limb=8 | `B6 06 00 00 00 00 08 00 08 AA` | The return for 7, from the same alternation. | 9 sends, [1](../evidence/move_rocker-20260811T212527172131Z.log)…[9](../evidence/move_rocker-20260811T212738783455Z.log) |
| limb=9 | `B6 06 00 00 00 00 09 00 09 AA` | LEFT ELBOW — a bend at the forearm producing a handshake motion. Read from alternating 9/10. The side was not stated by the observer at the time; 11/12 settled it retroactively as the right, making this the left. | 6 sends, [1](../evidence/move_rocker-20260811T212759602817Z.log)…[6](../evidence/move_rocker-20260811T212828643188Z.log) |
| limb=10 | `B6 06 00 00 00 00 0A 00 0A AA` | The return for 9, from the same alternation. | 6 sends, [1](../evidence/move_rocker-20260811T212803298165Z.log)…[6](../evidence/move_rocker-20260811T212832694009Z.log) |
| limb=11 | `B6 06 00 00 00 00 0B 00 0B AA` | RIGHT ELBOW bend, mirroring 9/10 — which is what settles 9/10 as the left. Read from alternating 11/12. | 6 sends, [1](../evidence/move_rocker-20260811T212849553538Z.log)…[6](../evidence/move_rocker-20260811T212922492445Z.log) |
| limb=12 | `B6 06 00 00 00 00 0C 00 0C AA` | The return for 11, from the same alternation. | 6 sends, [1](../evidence/move_rocker-20260811T212853663428Z.log)…[6](../evidence/move_rocker-20260811T212924893813Z.log) |
| p3=1 | `B6 06 00 00 00 01 00 00 01 AA` | Leaned to the LEFT, bending slightly at the WAIST — a joint the limb selector does not reach. The lean holds. Follows the limb byte's convention: odd acts, even returns. | 10 sends, [1](../evidence/move_rocker-20260811T213139024791Z.log)…[10](../evidence/move_rocker-20260811T213253363924Z.log) |
| p3=2 | `B6 06 00 00 00 02 00 00 02 AA` | Returned upright from the leaned position. The observer confirmed the command did this, not their hand. | 10 sends, [1](../evidence/move_rocker-20260811T213141306650Z.log)…[10](../evidence/move_rocker-20260811T213308874593Z.log) |
| p5=1 | `B6 06 00 00 00 00 00 01 01 AA` | No movement of any kind. The byte the app never writes produces no observable effect. Also held for a full minute across 22 back-to-back sends, which suppressed the idle routine for the whole window — still nothing. | 36 sends, [1](../evidence/move_rocker-20260811T213350634876Z.log)…[36](../evidence/move_rocker-20260811T213916498028Z.log) |
| p5=2 | `B6 06 00 00 00 00 00 02 02 AA` | No movement of any kind. The byte the app never writes produces no observable effect. | [1](../evidence/move_rocker-20260811T213355945737Z.log), [2](../evidence/move_rocker-20260811T213358735488Z.log) |
| p5=3 | `B6 06 00 00 00 00 00 03 03 AA` | No movement of any kind. The byte the app never writes produces no observable effect. | [1](../evidence/move_rocker-20260811T213401044670Z.log), [2](../evidence/move_rocker-20260811T213403866085Z.log) |
| p5=8 | `B6 06 00 00 00 00 00 08 08 AA` | No movement of any kind. The byte the app never writes produces no observable effect. | [1](../evidence/move_rocker-20260811T213407314727Z.log), [2](../evidence/move_rocker-20260811T213409534961Z.log) |
| p5=64 | `B6 06 00 00 00 00 00 40 40 AA` | No movement of any kind. The byte the app never writes produces no observable effect. | [1](../evidence/move_rocker-20260811T213413704647Z.log), [2](../evidence/move_rocker-20260811T213416374417Z.log) |
| p5=128 | `B6 06 00 00 00 00 00 80 80 AA` | No movement of any kind. The byte the app never writes produces no observable effect. | 8 sends, [1](../evidence/move_rocker-20260811T213420034830Z.log)…[8](../evidence/move_rocker-20260811T213602816015Z.log) |
| p5=255 | `B6 06 00 00 00 00 00 FF FF AA` | No movement of any kind. The byte the app never writes produces no observable effect. Includes twelve alternations against p5=1, run to test whether this byte drove the LED face and ears; the display did not track the send rhythm, so the activity seen during the sweep is not attributable to it. | 14 sends, [1](../evidence/move_rocker-20260811T213426066177Z.log)…[14](../evidence/move_rocker-20260811T213723277884Z.log) |
| direction=3, mode=1, speed=120 | `B6 06 01 78 03 00 00 00 7C AA` | WALKS forward, taking recognisable steps and leading with the LEFT foot. Read from 30 sends five seconds apart. | 30 sends, [1](../evidence/move_rocker-20260811T214307828193Z.log)…[30](../evidence/move_rocker-20260811T214632190630Z.log) |
| direction=3, mode=2, speed=120 | `B6 06 02 78 03 00 00 00 7D AA` | SLIDES forward — rolling, without stepping at all. Read from 30 sends five seconds apart. This is the vendor's own 'sliding', which is where the seeded slide_left and slide_right rows came from before anyone knew what it meant. | 30 sends, [1](../evidence/move_rocker-20260811T214341907868Z.log)…[30](../evidence/move_rocker-20260811T214755139405Z.log) |
| direction=3, mode=1, speed=50 | `B6 06 01 32 03 00 00 00 36 AA` | **WITHDRAWN.** Read at the time as a right turn in place. **Why:** Wrong, and retracted by the observer during the session. Repeated sends at speed 120 showed the robot travelling rather than rotating. A second reading of the same sends — that the byte picks which leg leads, from the left foot leading here — is not recorded as its own observation because it was an interpretation of these same sends rather than a separate watching; it fell over when mode 2 turned out not to step at all. | 6 sends, [1](../evidence/move_rocker-20260811T214152708519Z.log)…[6](../evidence/move_rocker-20260811T214207319121Z.log) |

<!-- END GENERATED COMMAND TABLE -->

## How entries are verified

Each row carries a status, and the test suite enforces what each status is allowed to claim:

- **unmapped** — the capability is published by the vendor, but nobody has looked for its frame
  yet. No byte content.
- **unlocated** — someone searched the decompiled app and did not find a frame for it. Also no
  byte content, but it means something different: the search happened.
- **decoded** — a frame was derived from the app and recorded together with where in the app it
  came from. It has never been sent to a robot.
- **confirmed** — the frame was issued through the CLI and someone watched the robot respond.
  The row points at log files that have to exist in the repository.

A confirmed row carries a *list* of observations, one per point of the parameter space that
was exercised, each with its own parameters, its own behaviour and its own logs. The movement
command carries two dozen. Some cite several logs, because the finding was read from a run of
repeated sends rather than a single one.

An observation marked **withdrawn** is a reading that was published and turned out to be
wrong. It is kept, with its reason, rather than deleted — a reference a reader cannot check
against its own error rate is harder to trust, not easier.

The distinction between the first two states matters more than it looks. Without it, a
capability nobody investigated is indistinguishable from one that was hunted for and missed,
and the table cannot honestly report what work remains.

The rules are enforced by `tests/test_table_invariants.py` and run in CI, rather than being a
convention this project promises to follow. The suite opens every cited log — not the first,
every one — and checks each records the same frame the entry builds at that observation's own
parameters, so the strongest status in the table cannot be claimed by editing this
repository's data by hand.

One boundary worth stating plainly. The strongest claim here is that the tool issued
exactly these bytes and a contributor reported what followed. Writes go out without
requesting a response, so a successful send means the host's Bluetooth stack took the
bytes — not that the robot received them — and the behavior column is a human report.
