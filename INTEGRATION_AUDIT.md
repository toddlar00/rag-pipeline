# Cross-Milestone Integration Audit

Date: 2026-07-22

## Scope

This audit covers the private repository's baseline, independent LLM transport
budget change (PR #1), and the ordered PR #2 through PR #27 stack. It checks
branch ancestry, implementation claims, test and supply-chain evidence,
cross-milestone interactions, documentation accuracy, and readiness for one
cumulative integration review.

The audit was performed as a read-only pass separate from implementation. Its
initial findings were then remediated on `agent/integrate-all-milestones`; this
record makes the findings and their disposition durable.

## Findings and disposition

### 1. Independent LLM milestone was not tested with the stack — remediated

PR #1 was based directly on `main`, while PRs #2 through #27 formed a separate
stack. The green PR #27 head therefore did not contain or exercise the exact LLM
transport-attempt budget.

The integration branch merges both histories. Conflict resolution preserved the
later `llm_adapters.py` and `cli_policy.py` boundaries, ports atomic retry
admission into those modules, retains thread-safe throttle initialization, and
adds `max_llm_transport_attempts` to chunk-completion provenance. A budget change
therefore invalidates resumable chunk output instead of silently reusing output
produced under another physical-call ceiling.

Local combined validation after the merge:

- full test suite: 1,011 passed, 7 skipped;
- focused LLM, adapter, CLI, pipeline, and publication suite: 117 passed;
- Ruff, Python compilation, and `git diff --check`: passed.

[PR #28](https://github.com/toddlar00/rag-pipeline/pull/28)'s check rollup is
the authoritative exact-head CI, dependency-resolution, real-vector-client,
vulnerability, license, and SBOM evidence.

### 2. Independent review evidence was ephemeral — remediated

The milestone PR descriptions recorded several independent security, durability,
and release audits, but no durable repository artifact tied the final stack-wide
findings together. This file records the independent final review, including its
blocking findings rather than only its successful checks. The cumulative PR also
provides a durable discussion and check-rollup location.

### 3. Modularization status overstated physical decomposition — remediated

PRs #15 through #21 extracted genuine dependency-light policy/domain modules for
retrieval, artifact I/O, chunking, index state, LLM transports, CLI policy, and
ingestion safety. They did not fully split runtime orchestration, process
supervision, mutable caches, or physical vector backends out of `rag.py`.

`ROADMAP.md` now describes the delivered boundary precisely. Further extraction
is a separate future track rather than a hidden acceptance criterion. The safest
next seams, in order, are process supervision and vector lifecycle; both require
dedicated cross-platform containment and lease regression work and should not be
mixed into final integration.

### 4. Roadmap repeated delivered work as future imperatives — remediated

Retrieval evaluation and operations/privacy/service sections now distinguish
delivered machinery from corpus-owner calibration and integration work. Exact
PR #27 check status is recorded, and the completion rule requires durable review
evidence.

## Stack integrity evidence

- Repository visibility: private.
- PRs #1 through #27: open drafts and GitHub-reported mergeable at audit time.
- Every remote PR head matched its advertised object ID.
- Every PR #2 through #27 base was an ancestor of its head.
- PR #27 head `8e74069`: 24 of 24 checks successful.
- PRs #14 through #26: all available exact-head checks successful.
- Earlier PRs without workflows receive cumulative regression coverage on the
  integration head; PR #1 is now included there as well.

## Explicit residual responsibilities

These are not represented as completed product evidence:

- Each private production corpus still needs expert-reviewed relevance judgments
  and representative dense/hybrid/reranked calibration before its scores become
  release gates.
- Time-bounded dependency/license exceptions must be removed, upgraded, or
  renewed through a recorded review before 2026-08-31.
- Merging into `main` remains a separate integration action until the cumulative
  pull request is reviewed and its exact-head checks pass.

## Integration decision

The combined implementation is ready for cumulative automated validation and PR
review. It is not represented as integrated until the cumulative head passes and
is merged into `main`.
