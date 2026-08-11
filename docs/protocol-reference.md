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

Movement frames (`0xB6`, six payload bytes) carry a speed value, an eight-way direction, and a
limb-action selector. Direction runs counter-clockwise from 1 at RIGHT, so 3 is UP, 5 is LEFT
and 7 is DOWN, with 0 meaning no movement. Three payload bytes have no established meaning yet.

**Not yet documented.** The gyro family's payload layout, the programmed-sequence format, and
the three unexplained movement bytes.

## Audio channel

*Not documented.*

The robot is understood to expose a Bluetooth audio sink separate from its BLE control link,
pairable from ordinary system Bluetooth settings. **That understanding comes from a marketplace
listing, not from vendor documentation or observation, and it has not been checked.** Whether
the two channels are genuinely independent — and so whether a program can drive motion while
audio plays — is an open question, and one of the more interesting ones.

## Command table

The table below lists the robot's known capabilities and the verification state of each. It is
generated; see [`protocol/commands.yaml`](../protocol/commands.yaml) for the source.

<!-- BEGIN GENERATED COMMAND TABLE -->

> **Coverage note.** This table's row set is seeded from vendor-published
> capability counts, not from the protocol. It is not a complete list of
> protocol commands.
>
> Row set and status as of the first decompile of the Carle Android app (base.apk,
> com.ihunuo.jtlrobot). Nothing here has touched a robot: every derived frame is
> `decoded`, not `confirmed`.
>
> What the decompile changed. The vendor's published capability counts do NOT map
> one-to-one onto protocol commands. The app exposes a single trigger per media
> category and lets the robot cycle internally, so the ten songs, eight dance tracks,
> four stories and two gymnastic routines are four commands, not twenty-four. The
> originals are retained as `unlocated` with `superseded_by` rather than deleted, so
> the collapse stays traceable — and so they are already in place if the second payload
> byte turns out to select an individual item. The app always sends 0 there; only
> hardware can settle it.
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

**50 entries:** 44 unlocated, 6 decoded.

### Movement

| ID | Capability | Status | Frame | Observed behavior | Evidence |
|---|---|---|---|---|---|
| `move_forward` | Walk forward (superseded by move_rocker) | unlocated | — | — | — |
| `move_backward` | Walk backward (superseded by move_rocker) | unlocated | — | — | — |
| `turn_left` | Turn left (superseded by move_rocker) | unlocated | — | — | — |
| `turn_right` | Turn right (superseded by move_rocker) | unlocated | — | — | — |
| `slide_left` | Slide left (superseded by move_rocker) | unlocated | — | — | — |
| `slide_right` | Slide right (superseded by move_rocker) | unlocated | — | — | — |
| `move_rocker` | Drive movement and limbs; direction 1-8 counter-clockwise from RIGHT | decoded | `B6 06 00 00 00 00 00 00 00 AA` | — | derived: `NormolContorlActivity.sendconmmde() — decompiled from base.apk (com.ihunuo.jtlrobot)` |

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

| ID | Capability | Status | Frame | Observed behavior | Evidence |
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
| `media_music` | Trigger the songs | decoded | `B3 02 03 00 03 AA` | — | derived: `OtherActivity.onClick, R.id.music_btn — decompiled from base.apk (com.ihunuo.jtlrobot)` |

Parameters. The frame above is shown at each parameter's default.

| Command | Parameter | Range | Default |
|---|---|---|---|
| `media_music` | `index` | 0–255 | 0 |

### Dance tracks

| ID | Capability | Status | Frame | Observed behavior | Evidence |
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

| ID | Capability | Status | Frame | Observed behavior | Evidence |
|---|---|---|---|---|---|
| `gymnastic_01` | Gymnastic routine 1 of 2 (name not yet identified) (superseded by media_gymnastics) | unlocated | — | — | — |
| `gymnastic_02` | Gymnastic routine 2 of 2 (name not yet identified) (superseded by media_gymnastics) | unlocated | — | — | — |
| `media_gymnastics` | Trigger the gymnastics routines | decoded | `B3 02 00 00 00 AA` | — | derived: `OtherActivity.onClick, R.id.ticao_btn — decompiled from base.apk (com.ihunuo.jtlrobot)` |

Parameters. The frame above is shown at each parameter's default.

| Command | Parameter | Range | Default |
|---|---|---|---|
| `media_gymnastics` | `index` | 0–255 | 0 |

### Stories

| ID | Capability | Status | Frame | Observed behavior | Evidence |
|---|---|---|---|---|---|
| `story_01` | Story 1 of 4 (title not yet identified) (superseded by media_story) | unlocated | — | — | — |
| `story_02` | Story 2 of 4 (title not yet identified) (superseded by media_story) | unlocated | — | — | — |
| `story_03` | Story 3 of 4 (title not yet identified) (superseded by media_story) | unlocated | — | — | — |
| `story_04` | Story 4 of 4 (title not yet identified) (superseded by media_story) | unlocated | — | — | — |
| `media_story` | Trigger the stories | decoded | `B3 02 01 00 01 AA` | — | derived: `OtherActivity.onClick, R.id.stroy_btn — decompiled from base.apk (com.ihunuo.jtlrobot)` |

Parameters. The frame above is shown at each parameter's default.

| Command | Parameter | Range | Default |
|---|---|---|---|
| `media_story` | `index` | 0–255 | 0 |

### Voice commands

| ID | Capability | Status | Frame | Observed behavior | Evidence |
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

| ID | Capability | Status | Frame | Observed behavior | Evidence |
|---|---|---|---|---|---|
| `volume_set` | Set volume; payload byte 2 cycles 0, 1, 2 | decoded | `B3 02 04 00 04 AA` | — | derived: `OtherActivity.onClick, R.id.voice_btn — decompiled from base.apk (com.ihunuo.jtlrobot)` |

Parameters. The frame above is shown at each parameter's default.

| Command | Parameter | Range | Default |
|---|---|---|---|
| `volume_set` | `level` | 0–2 | 0 |

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
  The row points at a log file that has to exist in the repository.

The distinction between the first two states matters more than it looks. Without it, a
capability nobody investigated is indistinguishable from one that was hunted for and missed,
and the table cannot honestly report what work remains.

The rules are enforced by `tests/test_table_invariants.py` and run in CI, rather than being a
convention this project promises to follow. The suite opens the cited log and checks it
records the same frame the entry builds, so the strongest status in the table cannot be
claimed by editing this repository's data by hand.

One boundary worth stating plainly. The strongest claim here is that the tool issued
exactly these bytes and a contributor reported what followed. Writes go out without
requesting a response, so a successful send means the host's Bluetooth stack took the
bytes — not that the robot received them — and the behavior column is a human report.
