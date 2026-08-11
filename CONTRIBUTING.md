# Contributing

Two rules govern this repository. Both exist because the whole value of a protocol reference is
that a reader can trust it.

## 1. The evidence rule

**A command is not documented until someone has issued it and observed the robot's response.**

Every row in [`protocol/commands.yaml`](protocol/commands.yaml) carries a `status`:

| Status | Meaning | Required fields |
|---|---|---|
| `unmapped` | Capability is known to exist; nobody has looked for its frame yet | no `family` or `payload` |
| `unlocated` | Searched the decompiled app; the frame was not found | no `family` or `payload` |
| `decoded` | Frame derived from the app, never run against hardware | `family`, `payload`, `derivation` |
| `confirmed` | Issued via the CLI and the robot's response was observed | the above plus at least one live `observations` entry |

### Observations are a list

An entry carries an `observations` list rather than one behaviour. A parameterized command is
one frame spanning a whole parameter space — walking forward and raising an arm are the same
command at different bytes — so a single slot could only ever describe one point of it.

Each observation carries the `parameters` it was sent at, the `behavior` that followed, and an
`evidence` block naming the `date`, the `platform`, and a list of `logs`. Every rule below is
applied to **every** observation independently.

`logs` is a list because most findings here were read from a *sequence* of sends: alternating
two limb values on a loop, or holding one value for a minute. Citing one arbitrary member
would make that log appear to back a behaviour it alone did not produce. Every cited log must
have been sent at that observation's parameters, so a multi-log observation is a repeated
send, never a swept one — a sweep is many observations, one per value.

**No two observations may cite the same log.** The reference publishes an observation count,
which a reader takes as a measure of how widely a command was exercised; one send read twice
would read as two independent confirmations.

### Withdrawing an observation

A published reading that turns out to be wrong is **withdrawn**, not deleted. Add a
`withdrawn` key giving the reason, and the observation stays in the table and in the published
reference, marked as retracted. A reader who cannot see what this document got wrong cannot
calibrate it against its own error rate.

There is no `carle withdraw`. Retracting a published claim should be a deliberate hand-edit,
and the gate accepts one either way.

Withdrawal changes exactly one thing: whether the observation supports the entry's status.
It never exempts the observation from log validation — otherwise `withdrawn` becomes the flag
that walks anything at all past the gate. An entry whose observations are *all* withdrawn is
`decoded`, not `confirmed`, and that is a legal resting state: a fully-retracted finding must
not have to be deleted to satisfy the gate.

### The frame

The table stores a `family` byte and a `payload` template. Length, checksum and
terminator are computed — never stored, so they cannot drift out of step with the
payload. A payload item is a byte literal or a `{name}` reference resolved from a
`parameters` block giving each name a range and a default.

### What `confirmed` actually means

**The CLI issued this exact frame, and a contributor reported the resulting behavior** — at
least once, and separately for every observation the entry carries.

That is the whole claim, and it is worth stating plainly — including the parts the
tooling cannot back.

The write goes out without requesting a response, so a successful send means the host's
Bluetooth stack accepted the bytes, not that the robot received them.
`observed_behavior` is a human report with nothing mechanical behind it.

What the test suite actually checks is **internal consistency**: that the committed log
names this entry, records a send rather than a raw write, carries the frame the entry
rebuilds at the parameters the log records, and agrees with the entry on date and
platform. It cannot tell whether that log came from a real robot. A determined
contributor can hand-write one — it is a text file in the repository.

So the honest description is that these rules make a false claim *deliberate* rather
than accidental, and leave it visible in a diff for a reviewer to catch. They do not
make it impossible. Authenticity rests on you, and on review.

Every log an observation cites must sit under `evidence/`. The invariant suite **opens each
one** and requires it to name this entry, to be a real send rather than a dry run, to record a
successful write, to carry the frame the entry rebuilds at *that observation's* parameters, to
have been sent at exactly those parameters, and to agree on date and platform. Checking only
that the file existed let anyone point at any non-empty file and pass every gate.

The parameter check is not implied by the frame check: two parameter sets can resolve to the
same bytes, and a log recorded at one must not be able to back a claim about the other.

`tests/test_table_invariants.py` enforces all of this, and CI runs it on each pull
request — by name, and again without pytest at all. This is deliberate: a convention
erodes, a test does not.

Commit the send logs with the observation. The gate resolves each path on a fresh checkout,
so an uncommitted log fails the build for everyone else.

A log's *directory* is a filing choice, not what makes it evidence. What makes it evidence is
that it records a real send, is committed, and is consistent with the entry that cites it.
Logs written to a scratch directory during a sweep are promoted into `evidence/` by copying
them — but only ones an observation actually cites. A committed log nobody watched is a send
waiting to be minted into a published observation, which is the opposite of the point.

### The robot acts on its own

It runs an idle routine unprompted — music, movement, speech — using the same content a
command produces. Attribute only what happens within a few seconds of your send, and note it
when the timing was loose. One entry here already had to be narrowed after its "the command
kept going" behaviour turned out to be the robot amusing itself.

If you decode a frame but have no robot to test it against, mark it `decoded`. That is a real
contribution and the honest label for it.

Do not delete a row because you could not map it. Mark it `unlocated`, or — if the decompile
shows the capability merged into or split across other commands — keep the id and add
`superseded_by` naming the replacements. Rows seeded from vendor marketing carry
`provenance: vendor-marketing` and can never carry an `encoding`; a real encoding requires
`provenance: decompile`.

## 2. The interoperability rule

**This repository documents how to talk to the robot. It does not advise anyone about what that
implies.**

The line, concretely:

- **In scope:** "The control characteristic accepts writes without pairing or bonding." That is
  a factual property of the protocol, and a reader implementing a client needs it.
- **Out of scope:** "Because the channel is unauthenticated, an attacker within range could
  drive a nearby robot; consider whether this is appropriate for a children's toy." That is
  advisory content, and it does not belong here.

Pull requests adding threat models, risk ratings, disclosure timelines, or security
recommendations will be declined. This is a settled decision, not an oversight.

## Working on the code

```bash
uv sync
uv run pytest
uv run ruff check .
uv run ruff format .
```

If you change `protocol/commands.yaml`, regenerate the reference:

```bash
uv run python scripts/generate_reference.py
```

CI runs `--check` on that generator, so a stale `docs/protocol-reference.md` fails the build.
Never hand-edit the generated region — edit the YAML and regenerate.

## Archived vendor documents

`official-docs/` holds vendor-published material. Every file there must have an entry in
`official-docs/manifest.yaml` recording its `source_url` and the `retrieved` date, and every
archived file must declare `capture: verbatim` or `capture: extracted` so a transcription is
never mistaken for a byte-for-byte copy.

If a source cannot be reached, record the entry anyway with `source_url`, the attempt date, and
a `retrieval_failed` note explaining what happened — omit `local_path` in that case. Never guess
at a URL to fill the field.
