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
