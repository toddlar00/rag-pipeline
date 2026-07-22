# Improvement Roadmap

This roadmap was reconstructed on 2026-07-21 from the repository, tests, and
published pull requests after the original chat brainstorm was not preserved as
a durable artifact. It records the recoverable milestones and turns the
remaining improvement themes into an explicit, ordered backlog.

Status terms:

- **Baseline** — present in the initial private-repository snapshot on `main`.
- **Implemented (draft)** — coded, validated, pushed, and opened as a draft PR,
  but not yet merged into `main`.
- **In progress** — active branch; not yet published as a PR.
- **Planned** — scoped direction, not yet implemented.

## Current status

| Milestone | Status | Durable result |
|---|---|---|
| Foundation | Baseline | End-to-end PDF ingestion, enriched chunking, shared LLM runtime, grounded answers, hybrid retrieval/reranking, evaluation harness, Chroma/Qdrant indexing, CLI, UI, docs, and tests |
| Exact LLM transport budget | Implemented (draft) | [PR #1](https://github.com/toddlar00/rag-pipeline/pull/1) |
| Qdrant manifest reconciliation | Implemented (draft) | [PR #2](https://github.com/toddlar00/rag-pipeline/pull/2) |
| Qdrant interrupted-update guard | Implemented (draft) | [PR #3](https://github.com/toddlar00/rag-pipeline/pull/3) |
| Bounded Qdrant integrity scans | Implemented (draft) | [PR #4](https://github.com/toddlar00/rag-pipeline/pull/4) |
| Qdrant mutation-status validation | Implemented (draft) | [PR #5](https://github.com/toddlar00/rag-pipeline/pull/5) |
| Qdrant worker lifecycle cleanup | Implemented (draft) | [PR #6](https://github.com/toddlar00/rag-pipeline/pull/6) |
| Chroma worker lifecycle cleanup | Implemented (draft) | [PR #7](https://github.com/toddlar00/rag-pipeline/pull/7) |
| Chroma interrupted-update guard | Implemented (draft) | [PR #8](https://github.com/toddlar00/rag-pipeline/pull/8) |
| Chroma parallel/API lifecycle cleanup | Implemented (draft) | [PR #9](https://github.com/toddlar00/rag-pipeline/pull/9) |
| Chroma record reconciliation and no-op detection | Implemented (draft) | [PR #10](https://github.com/toddlar00/rag-pipeline/pull/10) |
| Deterministic vector-client lifecycle | Implemented (draft) | [PR #11](https://github.com/toddlar00/rag-pipeline/pull/11) |
| Cross-process vector-store concurrency | Implemented (draft) | [PR #12](https://github.com/toddlar00/rag-pipeline/pull/12) |
| Hard operation deadlines and crash-safe publication | Implemented (draft) | [PR #13](https://github.com/toddlar00/rag-pipeline/pull/13) |
| Reproducible CI and supply-chain gates | Implemented (draft) | [PR #14](https://github.com/toddlar00/rag-pipeline/pull/14) |
| Retrieval-domain modularization | Implemented (draft) | [PR #15](https://github.com/toddlar00/rag-pipeline/pull/15) |
| Artifact I/O modularization | Implemented (draft) | [PR #16](https://github.com/toddlar00/rag-pipeline/pull/16) |
| Chunking-domain modularization | Implemented (draft) | [PR #17](https://github.com/toddlar00/rag-pipeline/pull/17) |
| Index-state modularization | Implemented (draft) | [PR #18](https://github.com/toddlar00/rag-pipeline/pull/18) |
| LLM-provider modularization | Implemented (draft) | [PR #19](https://github.com/toddlar00/rag-pipeline/pull/19) |
| CLI-policy modularization | Implemented (draft) | [PR #20](https://github.com/toddlar00/rag-pipeline/pull/20) |
| PDF-ingestion modularization | Implemented (draft) | [PR #21](https://github.com/toddlar00/rag-pipeline/pull/21) |
| Model-artifact supply chain and ML-BOM | Implemented (draft) | [PR #22](https://github.com/toddlar00/rag-pipeline/pull/22) |
| Multi-subject adversarial retrieval evaluation | Implemented (draft) | [PR #23](https://github.com/toddlar00/rag-pipeline/pull/23) |
| Structured run telemetry and committed index outcomes | Implemented (draft) | [PR #24](https://github.com/toddlar00/rag-pipeline/pull/24) |
| Private storage policy and lifecycle retention | Implemented (draft) | [PR #25](https://github.com/toddlar00/rag-pipeline/pull/25) |
| Durable cancellable/resumable background jobs | Implemented (draft) | [PR #26](https://github.com/toddlar00/rag-pipeline/pull/26) |
| Stable authenticated local service/API | Implemented (draft) | [PR #27](https://github.com/toddlar00/rag-pipeline/pull/27) |

PR #1 is independent of the index-integrity stack and can be reviewed or merged
separately. The index-integrity stack must be reviewed and merged in order:
**#2 -> #3 -> #4 -> #5 -> #6 -> #7 -> #8 -> #9 -> #10 -> #11 -> #12 -> #13 -> #14 -> #15 -> #16 -> #17 -> #18 -> #19 -> #20 -> #21 -> #22 -> #23 -> #24 -> #25 -> #26 -> #27**.
Until those PRs merge,
implementation progress is ahead of integration progress.

## Implementation status and remaining integration work

### 1. Integrate the published draft stack

- Review and merge the cumulative integration head after its exact-head CI,
  dependency, and supply-chain checks pass. The individual PRs remain useful as
  review-sized slices and preserve the implementation history.
- Keep the expiring ChromaDB, PyMuPDF, FlagEmbedding, and CPU-wheel audit
  exceptions under active review; replace them with fixed upstream releases or
  recorded license decisions before their 2026-08-31 deadline.

### 2. Modularization boundary

Implemented across draft PRs #15-#21: standard-library-only domain and policy
seams now cover retrieval, artifact I/O, chunking, index state, LLM transports,
CLI policy, and ingestion safety while `rag.py` remains the stable runtime and
Python compatibility facade. Integration remains pending.

- The extracted modules own deterministic policy, typed records, and explicit
  callback/protocol boundaries; `rag.py` re-exports the established surface.
- Runtime orchestration, process supervision, mutable caches, and physical
  Chroma/Qdrant backends deliberately remain in `rag.py`. Further decomposition
  is a distinct future track, starting with process supervision and then vector
  lifecycle, because those OS-containment and lease boundaries require their
  own failure-injection milestones.
- This phase is therefore a completed policy-seam extraction, not a claim that
  `rag.py` has become a thin or fully decomposed facade.

### 3. Expand retrieval evaluation

Implemented in draft PR #23 with pinned CC0 Property and Constitutional Law
mini corpora, adversarial citation/abstention/filter/long-context fixtures,
portable deterministic baselines, redacted CI artifacts, and relevance,
grounding, latency, memory, storage, token-use, and caller-priced cost metrics.
The controlled suites validate the evaluation machinery; expert review and
production dense/hybrid/reranked calibration remain necessary for each full
private corpus before its scores become release gates.

Delivered behavior includes corpus-pinned judged sets for multiple subjects,
adversarial citation/abstention/filter/long-context cases, CI threshold and
baseline-regression gates, retained machine-readable reports, and resource/cost
metrics. Adding a new private production corpus remains a corpus-owner task: it
requires expert judgments and representative dense/hybrid/reranked runs rather
than treating the checked-in lexical fixtures as universal quality evidence.

### 4. Improve operations, privacy, and product surfaces

The first three operations slices are implemented in draft PRs #24-#26, and
the stable service/API slice is implemented in draft PR #27.
PR #24 adds supervisor-allocated run IDs that correlate prompt-free stage,
committed-index, and LLM metrics; confirmed killed-worker recovery; visible
`partial` batch status; and aliased-output rejection. PR #25 adds verified
current-user-only storage, recursive legacy migration, ownership manifests,
post-quarantine identity validation, and dry-run-first run/cache/UI retention
under pipeline and vector leases. PR #26 adds private immutable job specs,
atomic attempt state, detached process-tree supervision, explicit cancellation
and resume, exact run binding, bounded logs, conservative restart recovery, and
a local-only Jobs UI. Its independent durability audit additionally gates the
same worker PID until OS containment and durable registration, propagates every
unconfirmed cleanup into non-resumable `orphaned`, holds a verified Windows
process handle across recovery termination, scopes cancellation markers per
attempt, pins and revalidates working/output directory identities, rejects
oversize persisted specs and bindings before publication, revalidates completed
batch stages, and coordinates reconciliation, cancellation, quarantine, and
deletion with short root plus per-job leases. Job-store schema v2 intentionally
fails closed on the unreleased v1 prototype because its missing directory
identities cannot be reconstructed safely. The service draft adds a strict
dependency-free v1 contract, an authenticated loopback-only HTTP adapter,
static credential-free Qdrant corpus bindings, supervised search with bounded
redacted results, reader/admin roles, deterministic reindex idempotency,
attempt/revision ETags, confirmation-bound terminal deletion, and authenticated
static OpenAPI. It also holds a singleton service-state lease, reconciles
crash-left queued attempts into explicit-resume failures, uses verified private
temporary storage, and adds Linux/Windows CI coverage for the live socket
contract.
PR #27 head `8e74069` passed all 24 exact-head CI, compatibility, and
supply-chain checks. These operations slices remain draft work until the
cumulative branch—including independent PR #1—is reviewed and integrated.

## Completion rule

A milestone is complete only when its behavior is implemented, failure-injected,
covered by the full test suite and static checks, exercised against the relevant
real optional client where practical, independently reviewed, and published as a
mergeable draft PR. Review evidence must be durable in a committed audit or PR
review/comment rather than existing only in an ephemeral work log. A milestone
becomes integrated only after merge into `main`.
