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
| `confirmed` | Issued via the CLI and the robot's response was observed | the above plus `observed_behavior`, `observed_parameters`, `hardware_evidence` |

The table stores a `family` byte and a `payload` template. Length, checksum and
terminator are computed — never stored, so they cannot drift out of step with the
payload. A payload item is a byte literal or a `{name}` reference resolved from a
`parameters` block giving each name a range and a default.

### What `confirmed` actually means

**The CLI issued this exact frame, and a contributor reported the resulting behavior.**

That is the whole claim, and it is worth stating plainly. The write is
write-without-response, so a successful send means the host's Bluetooth stack accepted
the bytes — not that the robot received them. `observed_behavior` is a human report with
nothing mechanical behind it. What the tooling does guarantee is that the frame in the
log is the frame the entry builds, that the log was written by a real send rather than a
dry run, and that the log is committed where anyone can read it.

`hardware_evidence` names a `date`, a `platform`, and a `log` path under `evidence/`. The
invariant suite **opens that log** and requires it to name this entry, to be a real send
rather than a dry run, and to record the frame the entry rebuilds at its observed
parameters. Checking only that the file existed let anyone point at any non-empty file
and pass every gate.

`tests/test_table_invariants.py` enforces all of this, and CI runs it on each pull
request — by name, and again without pytest at all. This is deliberate: a convention
erodes, a test does not.

Commit the send log with the promotion. The gate resolves the path on a fresh checkout,
so an uncommitted log fails the build for everyone else.

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
