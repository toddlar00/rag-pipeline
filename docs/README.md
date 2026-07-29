# Documentation Map

This index separates current decisions and work from historical planning
material. When documents disagree, use the authority order below.

## Current sources of truth

1. Approved governance decisions — owner-controlled policy boundaries that an
   agent cannot choose, especially private-source disclosure and corpus
   approval.
2. Architecture decision records — maintained behavioral and threat-model
   decisions that implementations and tests must satisfy.
3. [`ROADMAP.md`](../ROADMAP.md) — prioritized R0-R12 backlog, current delivery
   state, dependencies, acceptance evidence, and PR disposition.
4. [`README.md`](../README.md) — operator-facing commands and current behavior.
5. Historical plans — design inputs only; unchecked boxes are not live tasks.

## Architecture decisions

- [Release security policy](architecture/decisions/release-security-policy.md)
  — local/cloud egress, UI trust, model acquisition, caches, tenancy,
  transport environment, worker receipts, and migration.
- [Local endpoint boundaries](architecture/decisions/local-endpoint-boundaries.md)
  — canonical endpoint validation, provider credentials, redirects, and the
  loopback-only UI exposure boundary.
- [Process-supervision extraction](architecture/decisions/process-supervision-extraction.md)
  — containment policy and the stable `rag.py` facade.
- [Runtime-supervision binding](architecture/decisions/runtime-supervision-binding.md)
  — the frozen production capability consumed by durable jobs, the removal of
  the final import cycle, and the later service-host boundary it enabled.
- [Service-host binding and isolated search composition](architecture/decisions/service-host-binding.md)
  — the frozen service-host capabilities, shared path lease and embedding
  policy, hidden retrieval child, and later job/root composition work.
- [Service job-coordination binding](architecture/decisions/service-job-coordination-binding.md)
  — the inward durable engine, stable manager facade, frozen service
  launch/reconcile capability, and remaining application-root work.
- [Job-application binding](architecture/decisions/job-application-binding.md)
  — the frozen CLI/UI job capability, manager-shell isolation, atomic
  per-action lookup, and the prerequisite consumed by the service-role root.
- [Service HTTP boundary and lazy application composition](architecture/decisions/service-application-composition.md)
  — the structural HTTP adapter, frozen HTTP policy, stable service facade,
  lazy one-generation service root, and residual pipeline-root work.
- [Document-structure profiles](architecture/decisions/document-structure-profiles.md)
  — immutable reviewed profiles and fail-closed evidence.
- [Context-aware retrieval](architecture/decisions/context-aware-retrieval.md)
  — stable adjacency, bounded context, and independent citations.
- [Table-family evaluation](architecture/decisions/table-family-evaluation.md)
  — exact-once logical relevance and owner-selected child aliases.
- [Evaluation input-contract dependency boundary](architecture/decisions/evaluation-input-contract.md)
  — strict shared review/release inputs, the shared query domain,
  compatibility aliases, and both evaluation-side R8 inversions.
- [CI security ownership policy](architecture/decisions/ci-security-ownership-policy.md)
  — security-workflow trigger ownership, pinned actions, checkout credential
  isolation, least privilege, and the residual secret-scan boundary.
- [Dependency compatibility domains](architecture/decisions/dependency-compatibility-domains.md)
  — exact non-overlapping upgrade groups, manifest coverage, lock refresh and
  domain-specific qualification before dependency changes.
- [Architecture and facade inventory policy](architecture/decisions/architecture-facade-inventory-policy.md)
  — schema-v3 static graph, facade/mutation characterization, normalized
  runtime contract, and reviewed baseline refreshes.
- [Phase A0 benchmark policy](architecture/decisions/phase-a0-benchmark-policy.md)
  — the cross-platform A0a harness contract, repaired local A0b replacement
  candidates, the first hosted failure diagnosis, and the still-pending hosted
  replacement frozen-head checkpoint.

With the local CI-ownership and Phase A0a decisions plus the implemented and
independently audited architecture-inventory policy now maintained here, the
roadmap's remaining missing ADR coverage is vector lifecycle, table-row
retrieval/evaluation, immutable source generation, pipeline composition/facade
semantics, and release platform/support tiers. Historical implementation plans
are not substitutes for those records.

## Governance

- [Private-source documentation policy proposal](governance/private-source-documentation-policy-proposal.md)
  — content-free inventory and the unresolved owner decision. It is not an
  approval to publish corpus-derived material.

## Historical Claude plans

- [Process-supervision extraction plan](superpowers/plans/2026-07-22-process-supervision-extraction-plan.md)
- [Code improvements and structure-profile plan](superpowers/plans/2026-07-23-codex-improvements-structure-profiles.md)

Both plans are retained for provenance and begin with supersession warnings.
Their branch instructions, line/test counts, mutable-profile sketch, unchecked
boxes, and neighbor-citation sketch are stale. The implemented code, maintained
ADRs, and `ROADMAP.md` deliberately supersede them.

## Evidence and publication state

[`INTEGRATION_AUDIT.md`](../INTEGRATION_AUDIT.md) is the historical exact-head
record for PRs #1-#28. PRs #31-#43, the local R0/R0A/R0B/R4/R8/R10/R12
changes, the accepted local R7 architecture-inventory gate and CI-security
ownership gate, CI status, review state, and release gaps are summarized in
`ROADMAP.md` until R12 moves
point-in-time transcripts and test counts into an immutable `docs/evidence/`
ledger keyed by commit and PR.

The Phase A0a harness and its local tests are implementation evidence only. The
first frozen A0b hosted attempt at `ba9c66d` exposed checkout-EOL drift in lock
and architecture-inventory bytes plus host-dependent validation of the
drive-relative path `C:escape.py`; it did not pass A0b. Replacement source
`7594f8b` repaired those gates, and `fdb08d2` normalized the supported Python
3.10-3.14 runtime-contract differences without weakening the probe's home/tilde
denial. Gate-only `ed2995e` froze those candidates; R2 source `537f72b` and
gate-only refresh `b813aa7` followed. Source-changing defect corrections in
`c1bc042` invalidated both earlier report pairs, and the later publication-
readiness implementation superseded that refresh in turn. Fresh CPython 3.12
x86-64 Windows/Linux candidates now bind clean source `e904fa6`, the same eight
LF/`HEAD`-identical inputs, and the complete 9×5 contract. Both matching local
comparisons independently pass. The following reports-and-documentation-only
commit freezes the current local candidate; A0b still requires publishing its
exact commit/tree, passing hosted comparisons on both operating systems,
retaining successful evidence artifacts, and exact-head review. None of those
hosted or review gates is implied by A0a, the failed first attempt, or a
local-only result.

Ignored `output/` paths are not durable evidence in a fresh clone. A roadmap or
PR claim that depends on private output must be backed by a tracked content-free
receipt, a digest-bound retained CI artifact, or an exact PR/commit record.
