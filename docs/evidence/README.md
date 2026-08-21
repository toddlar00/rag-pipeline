# Evidence ledger

This directory holds point-in-time records that have been separated from the
live scheduling state in [`ROADMAP.md`](../../ROADMAP.md). Evidence records
describe what was observed at a named source identity. They do not authorize a
release, settle an owner decision, or override a maintained architecture
decision record.

## Append-only rule

- Name each record with its observation date and source commit or other stable
  identity.
- After a record is merged, do not silently revise it. Add a new record that
  identifies and supersedes the earlier one.
- Bind repository observations to the full commit and tree. Bind preserved
  file snapshots to their Git blob and SHA-256 digest.
- Label local self-audits, hosted workflow results, human reviews, owner
  decisions, and diagnostic failures distinctly. One is not a substitute for
  another.
- Keep new entries content-free: aggregates, opaque digests, generated or CC0
  fixture identities, and non-corpus repository workflow metadata only. Do not
  add private source excerpts, derived wording, paths, prompts, queries, or
  judgments.
- The moved roadmap snapshot below preserves already-tracked historical text
  byte-for-byte. Its presence is provenance, not precedent for adding or
  expanding a private-derived artifact class. The unresolved
  [private-source policy](../governance/private-source-documentation-policy-proposal.md)
  continues to govern it.

Relative links inside an exact-byte snapshot retain the meaning they had at
the snapshot's original repository-root location and commit. Consult that
commit when a moved relative link no longer resolves from this directory.

For the historical snapshot, each explicitly named pull-request number or URL
is its PR lookup key. A subject head/tree that the preserved narrative does not
state remains “not established”; do not infer it from neighboring prose. The
earlier [`INTEGRATION_AUDIT.md`](../../INTEGRATION_AUDIT.md) remains the
exact-head record for PRs #1-#28.

## Index

| Record | Observation | Source identity | Classification | Purpose |
| --- | --- | --- | --- | --- |
| [Main status at `443dce4`](2026-08-18-main-443dce4.md) | 2026-08-18 | commit `443dce4c312737eebb57a5bba6fc0abb9ace1a26`; tree `344a9b224dd5942a50d370bc10dfb62384962440` | Content-free aggregate; local self-audit plus hosted-workflow metadata from the private repository | Exact baseline used for the next improvement program |
| [Roadmap history through `443dce4`](roadmap-through-2026-08-18-443dce4.md) | Through 2026-08-18 | the same commit/tree; `ROADMAP.md` blob `5683793130be8feb4e78ffafe89050e5789e3b2c`; SHA-256 `3b6b91d237efefa957c3aa7b5a35bed31f69d39d24c036374ae1e05fcebd9583` | Exact-byte moved historical snapshot; may contain already-tracked policy-governed material | Preserves the full R0-R12 chronology, PR narratives, old counts, and superseded freeze without keeping them live |
| [Task 0.2 seed promotion at `d142065`](2026-08-21-ci-promotion-seed-d142065.md) | 2026-08-21 | merge `d142065cdcc890e2ad64057e0c9f627a079f93d6`; tree `1c2a1c7ec6e3dcafc3b7265ca1c4b2d925f0b336`; evidence head `aa7a23bbb0179beab001261da43529d670ee5720` | Content-free aggregate; hosted-workflow metadata; independent agent review (not a human review or owner decision) | Records the deterministic CI promotion seed's verified evidence chain, review findings F1-F5, and history-preserving integration |
| [Task 0.2 closure at `1e79540`](2026-08-21-task-0-2-closure-1e79540.md) | 2026-08-21 | merge `1e79540c9e3fbbb31f5eabdac35d608d68b7588f`; tree `96ff1600afc3aa1fcefac3d515bbdaf258f7c7b9`; hardening source `376750d6b992b04064d61e142d516c2ebda3b696`; evidence child `f8fc95bc1d721471f52a4fe5a184df41d8e0b4c1`; activation head `476464d0c9f0be8792edb0f187cce205d20a4ff1` | Content-free aggregate; hosted-workflow metadata; independent agent review (not a human review or owner decision) | Records the hardening checkpoint, the activation run's first live promotion-gate pass, and Task 0.2 completion |
