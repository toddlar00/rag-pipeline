# Improvement Roadmap

This roadmap was reconstructed on 2026-07-21 from the repository, tests, and
published pull requests after the original chat brainstorm was not preserved as
a durable artifact. It records the recoverable milestones and turns the
remaining improvement themes into an explicit, ordered backlog.

Status terms:

- **Baseline** — present in the initial private-repository snapshot on `main`.
- **Implemented (draft)** — coded, validated, pushed, and opened as a draft PR,
  but not yet merged into `main`.
- **Integration candidate (draft)** — all implementation histories are combined
  in one draft PR, pending exact-head validation, review, and merge.
- **Integrated** — present on `main` through the cumulative integration PR.
- **Validated locally** — implemented and exercised against the relevant real
  artifact, but not yet committed, reviewed, or merged.
- **In progress** — active branch; not yet published as a PR.
- **Planned** — scoped direction, not yet implemented.

## Current status

| Milestone | Status | Durable result |
|---|---|---|
| Foundation | Baseline | End-to-end PDF ingestion, enriched chunking, shared LLM runtime, grounded answers, hybrid retrieval/reranking, evaluation harness, Chroma/Qdrant indexing, CLI, UI, docs, and tests |
| Ethics corpus coherence and publication quality | Implemented (draft) | [PR #31](https://github.com/toddlar00/rag-pipeline/pull/31): canonical scaffold reconstruction, exact source identity, complete tables and nested footnotes, exact embedding budgets, regenerated exports, and an exactly reconciled 1,715-record Chroma index |
| Machine-readable corpus quality attestation | Implemented (draft) | [PR #31](https://github.com/toddlar00/rag-pipeline/pull/31): schema-v1 report binds the exact Docling source, chunks bytes, parameters, source-lineage coverage, tables, normalization, classification, entities, token budgets, and stable/hash roots; resume, export, retrieval, and index publication fail closed on missing, stale, malformed, or mismatched evidence |
| Synced-folder publication resilience | Implemented (draft) | [PR #31](https://github.com/toddlar00/rag-pipeline/pull/31): bounded Windows sharing-violation retries republish only a pinned staging file; marker reads retry only content-identical ctime churn and fail closed on every identity, byte, link, ownership, or schema change |
| Exact LLM transport budget | Integrated | [PR #1](https://github.com/toddlar00/rag-pipeline/pull/1) |
| Qdrant manifest reconciliation | Integrated | [PR #2](https://github.com/toddlar00/rag-pipeline/pull/2) |
| Qdrant interrupted-update guard | Integrated | [PR #3](https://github.com/toddlar00/rag-pipeline/pull/3) |
| Bounded Qdrant integrity scans | Integrated | [PR #4](https://github.com/toddlar00/rag-pipeline/pull/4) |
| Qdrant mutation-status validation | Integrated | [PR #5](https://github.com/toddlar00/rag-pipeline/pull/5) |
| Qdrant worker lifecycle cleanup | Integrated | [PR #6](https://github.com/toddlar00/rag-pipeline/pull/6) |
| Chroma worker lifecycle cleanup | Integrated | [PR #7](https://github.com/toddlar00/rag-pipeline/pull/7) |
| Chroma interrupted-update guard | Integrated | [PR #8](https://github.com/toddlar00/rag-pipeline/pull/8) |
| Chroma parallel/API lifecycle cleanup | Integrated | [PR #9](https://github.com/toddlar00/rag-pipeline/pull/9) |
| Chroma record reconciliation and no-op detection | Integrated | [PR #10](https://github.com/toddlar00/rag-pipeline/pull/10) |
| Deterministic vector-client lifecycle | Integrated | [PR #11](https://github.com/toddlar00/rag-pipeline/pull/11) |
| Cross-process vector-store concurrency | Integrated | [PR #12](https://github.com/toddlar00/rag-pipeline/pull/12) |
| Hard operation deadlines and crash-safe publication | Integrated | [PR #13](https://github.com/toddlar00/rag-pipeline/pull/13) |
| Reproducible CI and supply-chain gates | Integrated | [PR #14](https://github.com/toddlar00/rag-pipeline/pull/14) |
| Retrieval-domain modularization | Integrated | [PR #15](https://github.com/toddlar00/rag-pipeline/pull/15) |
| Artifact I/O modularization | Integrated | [PR #16](https://github.com/toddlar00/rag-pipeline/pull/16) |
| Chunking-domain modularization | Integrated | [PR #17](https://github.com/toddlar00/rag-pipeline/pull/17) |
| Index-state modularization | Integrated | [PR #18](https://github.com/toddlar00/rag-pipeline/pull/18) |
| LLM-provider modularization | Integrated | [PR #19](https://github.com/toddlar00/rag-pipeline/pull/19) |
| CLI-policy modularization | Integrated | [PR #20](https://github.com/toddlar00/rag-pipeline/pull/20) |
| PDF-ingestion modularization | Integrated | [PR #21](https://github.com/toddlar00/rag-pipeline/pull/21) |
| Model-artifact supply chain and ML-BOM | Integrated | [PR #22](https://github.com/toddlar00/rag-pipeline/pull/22) |
| Multi-subject adversarial retrieval evaluation | Integrated | [PR #23](https://github.com/toddlar00/rag-pipeline/pull/23) |
| Structured run telemetry and committed index outcomes | Integrated | [PR #24](https://github.com/toddlar00/rag-pipeline/pull/24) |
| Private storage policy and lifecycle retention | Integrated | [PR #25](https://github.com/toddlar00/rag-pipeline/pull/25) |
| Durable cancellable/resumable background jobs | Integrated | [PR #26](https://github.com/toddlar00/rag-pipeline/pull/26) |
| Stable authenticated local service/API | Integrated | [PR #27](https://github.com/toddlar00/rag-pipeline/pull/27) |
| Cross-milestone cumulative integration | Integrated | [PR #28](https://github.com/toddlar00/rag-pipeline/pull/28) |

PR #1 began independently from the ordered #2 through #27 stack. [PR
#28](https://github.com/toddlar00/rag-pipeline/pull/28) combined both histories,
passed 24 of 24 exact-head checks, received a durable no-blocker review, and
merged into `main` as `f67370f`. PRs #3 through #27 were then closed as
superseded review slices without deleting their branches or history; PRs #1 and
#2 were recognized by GitHub as merged.

## Implemented scope and residual lifecycle work

### 1. Maintain the integrated release

- Preserve the cumulative CI, dependency, real-vector-client, vulnerability,
  license, and SBOM gates that passed on PR #28.
- Keep the expiring ChromaDB, PyMuPDF, FlagEmbedding, and CPU-wheel audit
  exceptions under active review; replace them with fixed upstream releases or
  recorded license decisions before their 2026-08-31 deadline.

### 2. Modularization boundary

Implemented across PRs #15-#21: standard-library-only domain and policy
seams now cover retrieval, artifact I/O, chunking, index state, LLM transports,
CLI policy, and ingestion safety while `rag.py` remains the stable runtime and
Python compatibility facade. These seams are integrated on `main`.

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

Implemented in PR #23 with pinned CC0 Property and Constitutional Law
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

The first three operations slices were implemented in PRs #24-#26, and the
stable service/API slice was implemented in PR #27. All are integrated.
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
identities cannot be reconstructed safely. The service layer adds a strict
dependency-free v1 contract, an authenticated loopback-only HTTP adapter,
static credential-free Qdrant corpus bindings, supervised search with bounded
redacted results, reader/admin roles, deterministic reindex idempotency,
attempt/revision ETags, confirmation-bound terminal deletion, and authenticated
static OpenAPI. It also holds a singleton service-state lease, reconciles
crash-left queued attempts into explicit-resume failures, uses verified private
temporary storage, and adds Linux/Windows CI coverage for the live socket
contract.
PR #27 head `8e74069` passed all 24 exact-head checks before combination. PR #28
head `80b7463` then passed all 24 CI, compatibility, and supply-chain checks with
independent PR #1 included before merging into `main`.

## Ethics corpus coherence milestone

The local 2026-07-22 remediation makes publication fail closed when chunks
contain known structural leaks, malformed canonical paths, damaged URLs or
hyphenation, repeated editorial disclaimers, invalid explicit content lanes,
unpreserved tables, or invalid extracted case entities. Source preparation now
separates substantive items that Docling merges across structural boundaries,
keeps nested table/picture footnotes together, preserves table captions, and
uses source-PDF bounding boxes to repair real cell omissions without treating
harmless token fusion as lost content.

The final `Ethics.pdf` regeneration produced 1,715 chunks across 14 canonical
chapters plus 36 substantive front-matter chunks. It retains 35 substantive
source tables as 39 row-bounded Markdown chunks; six tables on PDF pages 391,
469, 510, 542, 637, and 706 required source-PDF recovery. Exact source lineage
covers all 6,755 eligible Docling items, including the formerly orphaned page
682 `Theophylline` picture caption. There are no structural, normalization,
classification, entity, table, lineage, or token-budget failures. Two
canonical-text duplicate groups are retained intentionally because they are
distinct source occurrences rather than deduplication artifacts. The Nomic
embedding contract is exact for all records: raw chunk counts top out at 506
tokens, final task-prefixed inputs top out at the model's 512-token limit, and
no input is truncated.

The unified export contains 342,886 words; the 15 chapter/front-matter files
contain 343,650 words. Six current hybrid retrieval probes recover the intended
pages at ranks 1, 1, 1, 1, 1, and 3. The last result is the page 542 rule table,
behind closely related explanatory material, and remains a table-retrieval
calibration target. The Chroma collection, stable IDs, documents, metadata,
hashes, report binding, and manifests exactly match the 1,715-record JSONL. A
second no-op resume preserved all three artifact hashes and reported 0 changed,
1,715 unchanged, and 0 removed records. Evidence is recorded in
`output/Ethics_3/COHERENCE_AUDIT.md`.

The adjacent `Ethics_3_chunks.quality.json` is a deterministic PASS report over
all 1,715 records and all 6,755 eligible source identities. Its exact SHA-256
is committed into the schema-v6 index manifest. New lineaged corpora cannot be
indexed, exported, queried through the hybrid path, or accepted by resume when
that report is absent, stale, malformed, oversized, or hash-mismatched; legacy
unlineaged corpora retain an explicit compatibility path.

The same real run exposed transient Dropbox marker and Windows atomic-replace
interference. Publication now retries only `winerror` 5/32/33, only around the
already-written staging-file replace, with bounded backoff and fresh parent,
leaf, staging identity, link, and privacy checks immediately before every
retry. Retention marker reads retry only a ctime-only race while pinning device,
inode, size, mtime, link count, and the exact bytes hash across attempts.

Local validation for the implementation is 1,118 passed and 7 skipped in the
full suite, 436 passed and 1 skipped in the independent blocker-focused audit,
successful Python compilation, six live hybrid probes, two real resume runs,
and a clean `git diff --check`.
This milestone is published in draft [PR
#31](https://github.com/toddlar00/rag-pipeline/pull/31) with all 15 head checks
passing. It remains “Implemented (draft)” pending review and merge evidence.

## Next improvement milestones

| Priority | Milestone | Acceptance evidence |
|---|---|---|
| P1 | Ethics retrieval calibration | A corpus-owner-reviewed judged set covers rule text, author explanation, cases, tables, cross-page continuations, filters, and abstention; dense, hybrid, and reranked modes receive explicit release thresholds |
| P2 | Configurable document-structure profiles | Front/back-matter labels, chapter patterns, and canonical-title rules move behind tested profiles, with fixtures from multiple publishers and safe unknown-layout behavior |
| P2 | Context-aware retrieval assembly | Stable adjacency/parent identifiers allow query-time neighboring-chunk stitching without duplicate text, chapter leakage, or citation ambiguity |
| P2 | Table-specific retrieval | Large tables gain optional row-level child records linked to their preserved parent table, with header propagation and table-focused relevance tests |
| P3 | Runtime decomposition | Process supervision and vector lifecycle move out of `rag.py` in separate failure-injected milestones while the compatibility facade remains stable |

## Completion rule

A milestone is complete only when its behavior is implemented, failure-injected,
covered by the full test suite and static checks, exercised against the relevant
real optional client where practical, independently reviewed, and published as a
mergeable draft PR. Review evidence must be durable in a committed audit or PR
review/comment rather than existing only in an ephemeral work log. A milestone
becomes integrated only after merge into `main`.

Rows marked “Implemented (draft)” still require review and merge; the earlier
merged milestones have reached the integrated state.
