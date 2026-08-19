# Documentation Map

This index separates current decisions and work from historical planning
material. When documents disagree, use the authority order below.

## Current sources of truth

1. Approved governance decisions — owner-controlled policy boundaries that an
   agent cannot choose, especially private-source disclosure and corpus
   approval.
2. Architecture decision records, machine-readable policy, and schemas —
   maintained behavioral and threat-model constraints that implementations and
   tests must satisfy.
3. [`ROADMAP.md`](../ROADMAP.md) — live delivery state, work authorization,
   dependencies, owner blockers, next actions, and acceptance gates.
4. [`README.md`](../README.md) — operator-facing commands and current behavior.
5. Historical plans — design inputs only; unchecked boxes are not live tasks.

Runtime source and executable tests establish observed behavior and expose
drift. They cannot override an owner decision or authorize work.

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
  — security-workflow trigger ownership, base-trusted risk classification,
  fail-closed promotion aggregation, pinned actions, checkout credential
  isolation, least privilege, and the residual secret-scan boundary.
- [Dependency compatibility domains](architecture/decisions/dependency-compatibility-domains.md)
  — exact non-overlapping upgrade groups, manifest coverage, lock refresh and
  domain-specific qualification before dependency changes.
- [LLM output contracts](architecture/decisions/llm-output-contracts.md)
  — integrated hostile generated-text validation, classification prompt
  framing, cache/single-flight enforcement, the owner-pending
  semantic-rejection authority rule, and content-free receipts.
- [TOC hierarchy output contract](architecture/decisions/toc-hierarchy-output-contract.md)
  — integrated exact hierarchy-array validation, untrusted TOC prompt framing,
  atomic multi-batch fallback, and opt-in chunk provenance; inherited R0C
  authority remains owner-pending.
- [TOC layout output contract](architecture/decisions/toc-layout-output-contract.md)
  — integrated exact layout-hint validation, bounded source framing,
  content-free fallback, and conditional completion identity; inherited R0C
  authority remains owner-pending.
- [TOC page-verification output contract](architecture/decisions/toc-verification-output-contract.md)
  — integrated exact Boolean verification mechanics, bounded page-evidence
  framing, explicit inconclusive accounting, and content-free diagnostics;
  inherited R0C authority remains owner-pending.
- [Architecture and facade inventory policy](architecture/decisions/architecture-facade-inventory-policy.md)
  — schema-v3 static graph, facade/mutation characterization, normalized
  runtime contract, and reviewed baseline refreshes.
- [Phase A0 benchmark policy](architecture/decisions/phase-a0-benchmark-policy.md)
  — the cross-platform A0 contract, earlier diagnostic failures, the current
  passing hosted technical checkpoint, and the still-open review/architecture
  authorization boundary.

With the CI-ownership, architecture-inventory, and Phase A0 decisions now
maintained here, the roadmap's remaining missing ADR coverage is vector
lifecycle, table-row
retrieval/evaluation, immutable source generation, pipeline composition/facade
semantics, and release platform/support tiers. Historical implementation plans
are not substitutes for those records.

## Governance

- [Private-source documentation policy proposal](governance/private-source-documentation-policy-proposal.md)
  — content-free inventory and the unresolved owner decision. It is not an
  approval to publish corpus-derived material.

## Current implementation plan

- [Next improvement program](superpowers/plans/2026-08-18-next-improvement-program.md)
  — prioritized release-readiness, semantic-qualification, quality-gate, product,
  and architecture work. It remains subordinate to `ROADMAP.md`; follow the
  roadmap's current authorization table and do not infer an owner decision.

## Historical Claude plans

- [Process-supervision extraction plan](superpowers/plans/2026-07-22-process-supervision-extraction-plan.md)
- [Code improvements and structure-profile plan](superpowers/plans/2026-07-23-codex-improvements-structure-profiles.md)

Both plans are retained for provenance and begin with supersession warnings.
Their branch instructions, line/test counts, mutable-profile sketch, unchecked
boxes, and neighbor-citation sketch are stale. The implemented code, maintained
ADRs, and `ROADMAP.md` deliberately supersede them.

## Evidence and publication state

[`INTEGRATION_AUDIT.md`](../INTEGRATION_AUDIT.md) is the historical exact-head
record for PRs #1-#28. The append-only
[`docs/evidence/` ledger](evidence/README.md) now holds later point-in-time
status, test counts, workflow identities, and the exact-byte historical roadmap
snapshot. `ROADMAP.md` contains only live scheduling state; an evidence record
does not settle an owner decision.

The current Phase A0 pair binds source checkpoint `4551c50` to evidence head
`443dce4`. Its paired local comparisons and both hosted Windows/Linux jobs pass;
the exact identities and report digests are recorded in the
[`443dce4` evidence entry](evidence/2026-08-18-main-443dce4.md). No submitted
exact-head human review or separate R8c-6 authorization was found, so the
passing technical checkpoint is not permission for that architecture move.
Earlier candidate, invalidation, and failure history remains in the
[moved roadmap snapshot](evidence/roadmap-through-2026-08-18-443dce4.md).

Ignored `output/` paths are not durable evidence in a fresh clone. A roadmap or
PR claim that depends on private output must be backed by a tracked content-free
receipt, a digest-bound retained CI artifact, or an exact PR/commit record.
