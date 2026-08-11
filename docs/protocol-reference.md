# Ruko 1088 — BLE Protocol Reference

**Nothing in this document describes the protocol yet.** The structure below is in place and
the command table is real, but every capability is still unmapped. See
[`docs/method.md`](method.md) for how the missing content gets produced.

This document is partly generated. The command table is rendered from
[`protocol/commands.yaml`](../protocol/commands.yaml) by
`scripts/generate_reference.py`; everything else is hand-written. Do not edit inside the
generated markers — edit the YAML and regenerate.

## Transport

*Not documented.*

The robot advertises over Bluetooth Low Energy under a name beginning `JT_`, which is the only
transport fact currently established, and it comes from the vendor's user manual rather than
from observation. The GATT service layout is unknown: which service the mobile app uses, which
characteristic accepts writes, and whether any characteristic notifies state back have all yet
to be recorded.

Running `carle info` against a real robot prints the discovered services and characteristics
verbatim. That output is what this section will be written from.

## Frame format

*Not documented.*

How a payload is framed — leading bytes, length field, opcode position, argument layout, and
any trailing checksum or terminator — is unknown. This section is filled from the decompiled
mobile app's command builder, then checked against a packet capture.

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
> Every row below is seeded from Ruko's published capability counts, not from the
> protocol. That means the row set is NOT known to be complete, and the rows are NOT
> known to map one-to-one onto protocol commands — ten "songs" may turn out to be one
> opcode with a parameter. Seeded rows exist to give the reverse-engineering work a
> checklist, not to assert coverage.
>
> Counts asserted by the invariant suite, from Ruko's published specifications:
> 10 songs, 8 dance tracks, 2 gymnastic routines, 4 stories, 14 voice commands.
>
> Movement rows are seeded from the vendor's prose description of the robot's motion
> ("forward/backward/turning", "walking and sliding") and carry no asserted count,
> because Ruko publishes only "9 motor drives" — a hardware figure, not a capability
> enumeration. Arm, shoulder, and elbow articulation is described in vendor copy but
> not enumerated, so no articulation rows are seeded.
>
> Ruko also advertises "200 programmable commands by app". The working interpretation
> is that this describes user-composed sequence slots rather than distinct protocol
> opcodes, which is why it is not seeded as 200 rows. UNCONFIRMED — the decompile
> settles it.
>
> The archived user manual in official-docs/ may supply real names for the songs,
> stories, and voice commands currently recorded as numbered placeholders.

**44 entries:** 44 unmapped.

### Movement

| ID | Capability | Status | Encoding | Evidence |
|---|---|---|---|---|
| `move_forward` | Walk forward | unmapped | — | — |
| `move_backward` | Walk backward | unmapped | — | — |
| `turn_left` | Turn left | unmapped | — | — |
| `turn_right` | Turn right | unmapped | — | — |
| `slide_left` | Slide left | unmapped | — | — |
| `slide_right` | Slide right | unmapped | — | — |

### Songs

| ID | Capability | Status | Encoding | Evidence |
|---|---|---|---|---|
| `song_01` | Song 1 of 10 (title not yet identified) | unmapped | — | — |
| `song_02` | Song 2 of 10 (title not yet identified) | unmapped | — | — |
| `song_03` | Song 3 of 10 (title not yet identified) | unmapped | — | — |
| `song_04` | Song 4 of 10 (title not yet identified) | unmapped | — | — |
| `song_05` | Song 5 of 10 (title not yet identified) | unmapped | — | — |
| `song_06` | Song 6 of 10 (title not yet identified) | unmapped | — | — |
| `song_07` | Song 7 of 10 (title not yet identified) | unmapped | — | — |
| `song_08` | Song 8 of 10 (title not yet identified) | unmapped | — | — |
| `song_09` | Song 9 of 10 (title not yet identified) | unmapped | — | — |
| `song_10` | Song 10 of 10 (title not yet identified) | unmapped | — | — |

### Dance tracks

| ID | Capability | Status | Encoding | Evidence |
|---|---|---|---|---|
| `dance_01` | Dance track 1 of 8 (title not yet identified) | unmapped | — | — |
| `dance_02` | Dance track 2 of 8 (title not yet identified) | unmapped | — | — |
| `dance_03` | Dance track 3 of 8 (title not yet identified) | unmapped | — | — |
| `dance_04` | Dance track 4 of 8 (title not yet identified) | unmapped | — | — |
| `dance_05` | Dance track 5 of 8 (title not yet identified) | unmapped | — | — |
| `dance_06` | Dance track 6 of 8 (title not yet identified) | unmapped | — | — |
| `dance_07` | Dance track 7 of 8 (title not yet identified) | unmapped | — | — |
| `dance_08` | Dance track 8 of 8 (title not yet identified) | unmapped | — | — |

### Gymnastic routines

| ID | Capability | Status | Encoding | Evidence |
|---|---|---|---|---|
| `gymnastic_01` | Gymnastic routine 1 of 2 (name not yet identified) | unmapped | — | — |
| `gymnastic_02` | Gymnastic routine 2 of 2 (name not yet identified) | unmapped | — | — |

### Stories

| ID | Capability | Status | Encoding | Evidence |
|---|---|---|---|---|
| `story_01` | Story 1 of 4 (title not yet identified) | unmapped | — | — |
| `story_02` | Story 2 of 4 (title not yet identified) | unmapped | — | — |
| `story_03` | Story 3 of 4 (title not yet identified) | unmapped | — | — |
| `story_04` | Story 4 of 4 (title not yet identified) | unmapped | — | — |

### Voice commands

| ID | Capability | Status | Encoding | Evidence |
|---|---|---|---|---|
| `voice_01` | Voice command 1 of 14 (phrase not yet identified) | unmapped | — | — |
| `voice_02` | Voice command 2 of 14 (phrase not yet identified) | unmapped | — | — |
| `voice_03` | Voice command 3 of 14 (phrase not yet identified) | unmapped | — | — |
| `voice_04` | Voice command 4 of 14 (phrase not yet identified) | unmapped | — | — |
| `voice_05` | Voice command 5 of 14 (phrase not yet identified) | unmapped | — | — |
| `voice_06` | Voice command 6 of 14 (phrase not yet identified) | unmapped | — | — |
| `voice_07` | Voice command 7 of 14 (phrase not yet identified) | unmapped | — | — |
| `voice_08` | Voice command 8 of 14 (phrase not yet identified) | unmapped | — | — |
| `voice_09` | Voice command 9 of 14 (phrase not yet identified) | unmapped | — | — |
| `voice_10` | Voice command 10 of 14 (phrase not yet identified) | unmapped | — | — |
| `voice_11` | Voice command 11 of 14 (phrase not yet identified) | unmapped | — | — |
| `voice_12` | Voice command 12 of 14 (phrase not yet identified) | unmapped | — | — |
| `voice_13` | Voice command 13 of 14 (phrase not yet identified) | unmapped | — | — |
| `voice_14` | Voice command 14 of 14 (phrase not yet identified) | unmapped | — | — |

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
convention this project promises to follow.
