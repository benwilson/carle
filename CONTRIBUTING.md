# Contributing

Two rules govern this repository. Both exist because the whole value of a protocol reference is
that a reader can trust it.

## 1. The evidence rule

**A command is not documented until someone has issued it and observed the robot's response.**

Every row in [`protocol/commands.yaml`](protocol/commands.yaml) carries a `status`:

| Status | Meaning | Required fields |
|---|---|---|
| `unmapped` | Capability is known to exist; nobody has looked for its frame yet | no `encoding` |
| `unlocated` | Searched the decompiled app; the frame was not found | no `encoding` |
| `decoded` | Frame derived from the app, never run against hardware | `encoding`, `derivation` |
| `confirmed` | Issued via the CLI and the robot's response was observed | `encoding`, `derivation`, `observed_behavior`, `hardware_evidence` |

`hardware_evidence` names a `date`, a `platform`, and a `log` path under `evidence/` that must
actually exist. `tests/test_table_invariants.py` enforces every one of these rules, and CI runs
it on each pull request. This is deliberate: a convention erodes, a test does not.

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
`official-docs/manifest.yaml` recording its source URL and the date it was retrieved. If you add
a document and cannot cite where it came from, say so with `provenance: unverified` rather than
guessing at a URL.
