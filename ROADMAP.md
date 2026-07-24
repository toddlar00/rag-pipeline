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
| Synced-folder publication and read resilience | Implemented (draft) | [PR #31](https://github.com/toddlar00/rag-pipeline/pull/31): bounded Windows sharing-violation retries republish only a pinned staging file; marker and exact artifact reads retry only content-identical ctime churn while failing closed on content-generation changes; exact hashes bypass unsafe stat caching on Windows |
| Immutable source-generation provenance | Validated locally | Conversion, preprocessing, Docling, table recovery, chunking, and schema-v2 quality reports bind one exact PDF/Docling generation; multi-output leases prevent interleaved publishers; marker-owned private scratch is self-cleaning and dry-run prunable after hard termination. A disposable 912-page Ethics run produced 1,715 records, six bound recovered-table chunks, and a warning-free PASS report |
| Ethics retrieval calibration | In progress | A 14-query, 24-judgment draft is pinned to the exact 1,715-record corpus and covers rules, explanations, cases, tables, cross-page chunks, filters, abstention, outline distractors, and positive outline intents; corpus-owner review and release thresholds remain outstanding |
| Process supervision extraction | Implemented (draft) | [PR #33](https://github.com/toddlar00/rag-pipeline/pull/33): deadline supervision, Windows/POSIX containment, startup gates, termination confirmation, and generic entrypoint policy moved to stdlib-only `process_supervision.py`; `rag.py` retains late-bound compatibility wrappers |
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
- Runtime orchestration, mutable caches, and physical Chroma/Qdrant backends
  deliberately remain in `rag.py`. Process supervision is now extracted behind
  late-bound facade wrappers; vector lifecycle is the next decomposition slice
  because its lease and mutation boundaries require a separate failure-injected
  milestone.
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
contain 343,650 words. The original six hybrid probes recovered their nominated
pages at ranks 1, 1, 1, 1, 1, and 3, but decomposition showed that the last
probe, “Contingent-fee expense calculation,” was underspecified: page 543
contains the calculation and correctly ranks first, while page 542 states the
disclosure rule and is dense rank 1. A Rule-1.5(c)-specific query ranks the
page-542 table first. The Chroma collection, stable IDs, documents, metadata,
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

The same real run exposed transient Dropbox metadata and Windows atomic-replace
interference. Publication now retries only `winerror` 5/32/33, only around the
already-written staging-file replace, with bounded backoff and fresh parent,
leaf, staging identity, link, and privacy checks immediately before every
retry. Retention markers and exact artifact snapshots retry only ctime-only
races. Artifact reads pin device, inode, size, mtime, and exact bytes across
attempts; retention markers additionally pin link count, ownership, and schema.
Because Windows exposes creation time through `st_ctime`, exact artifact hashes
are recomputed there on every verification; stat-keyed digest caching remains
enabled only where ctime is a usable change counter. An adversarial same-size
rewrite with restored mtime confirms that stale bytes cannot alias a cache hit.

The first retrieval-calibration pass adds
`eval_queries_ethics_draft.jsonl`: 14 corpus-pinned queries with 24 graded
judgments. It separately grades page 542's disclosure rule and page 543's
calculation, marks the page-503 legal-fees outline irrelevant to those intents,
and also includes three queries where concise outlines are positive evidence.
The initial depth-20 comparison produced vector/hybrid/reranked nDCG@10 of
0.872/0.869/0.958 and MAP of 0.836/0.851/0.941. Those figures are diagnostic,
not gates: every query is explicitly marked as requiring corpus-owner review.
Together with the targeted ablations, they guard against adopting global
stemming, equal fusion weights, or blanket outline penalties merely to improve
one probe while harming already judged legal retrieval.

Local validation for the implementation is 1,127 passed and 7 skipped in the
full suite, 436 passed and 1 skipped in the initial independent blocker-focused
audit, and 81 passed in the post-fix artifact/evaluation re-audit. Python
compilation, six live hybrid probes, two real resume runs, and `git diff
--check` also pass.
This milestone is published in draft [PR
#31](https://github.com/toddlar00/rag-pipeline/pull/31) with all 15 head checks
passing. It remains “Implemented (draft)” pending review and merge evidence.

## Process supervision extraction milestone

The first P3 runtime-decomposition slice moves operating-system containment,
startup gating, deadline/cancellation control, termination confirmation, and
generic entrypoint routing into the standard-library-only
`process_supervision.py`. `rag.py` remains the public compatibility facade: it
snapshots its constants and resolves job, gate, termination, telemetry, CLI,
and entrypoint collaborators for every call so existing consumers and
monkeypatch-based failure tests retain their behavior.

Seventeen direct module tests exercise the dependency boundary, facade exports,
frozen configuration, Windows-job failure cases, timeout and cancellation
cleanup, telemetry ordering,
pre-launch failures, command routing, recursion prevention, and exact environment
restoration. The unchanged real-process characterization suite passes on the
facade. Real supervised `info` and Ethics hybrid-query smokes also pass; the
Rule-1.5(c) table remains rank 1. The complete repository suite passes with
1,144 tests and 7 platform skips; Ruff, compileall, and `git diff --check` are
clean. The stacked implementation is published as draft [PR
#33](https://github.com/toddlar00/rag-pipeline/pull/33), based on PR #31 until
the Ethics-coherence dependency merges.

## Immutable source-generation provenance milestone

Conversion now streams a single opened PDF generation into a private scratch
pathname using bounded memory and gives only that immutable pathname to
preprocessing and Docling. Schema-v2 conversion evidence binds the original
source name, size, SHA-256, capture policy, effective original/preprocessed
input, exact Docling JSON and Markdown outputs, and the published preprocessed
PDF whenever it was the effective input. Concurrent conversion writers
hold a sorted lease across the complete output set, so JSON, Markdown,
preprocessed PDF, and completion evidence cannot be interleaved.

Chunking parses one captured Docling JSON byte generation for both its validated
model and mapping views. PDF table recovery requires a capture-verified
conversion-v2 manifest; explicit `--source-pdf` mismatches are fatal, inferred
candidates must match the bound source hash, and recovered candidates are
discarded if snapshot-exit verification fails. Schema-v2 chunk and quality
artifacts carry the same exact Docling, conversion-manifest, and optional
recovery-PDF inputs. Sorted leases serialize standalone chunk, completion, and
quality publication. Legacy conversion/chunk schema-v1 evidence forces a
one-time rebuild rather than being reported as verified.

Hard termination cannot turn arbitrary temporary content into a deletion
target. Every private snapshot tree lives under a fixed owned root, contains a
strict nonce/PID/process-birth/creation-time marker, and is removed
automatically only after the marker is old and that exact process generation is
gone. Cleanup pins both the owned root and direct run directory across complete
validation and relative deletion, rejects device-boundary crossings, links,
junctions, nested directories, special files, and multiply linked files, and
keeps or restores the marker after any incomplete removal.
`storage --prune-snapshot-scratch` exposes the same policy as a genuinely
read-only dry run followed by an explicit apply action. Windows process
liveness uses a read-only process-handle query
rather than `os.kill(pid, 0)`, which does not have POSIX probe semantics there.

Migration is deliberately ordered rather than in-place: `full --resume`
regenerates invalid schema-v1 conversion and chunk evidence, publishes a
schema-v2 quality report whose inputs and parameter digest exactly equal the
chunk-v2 completion, and only then reconciles an index with the new report
binding. Direct query, export, or index use of the old evidence fails closed;
`--full-reindex` provides an explicit replace-the-collection migration when an
operator does not want incremental reconciliation.

Adversarial tests cover bounded reads, same-generation snapshots, cleanup-error
precedence, stale-owner selection, recovery rollback, explicit hash mismatch,
unbound recovered tables, Docling A/B races, and concurrent publishers. The
complete repository suite passes with 1,209 tests and 7 platform skips; Ruff,
Python compilation, and `git diff --check` are clean. A disposable real run
converted all 912 pages of `Ethics.pdf`, then produced 1,715 chunks, six
PDF-bound recovered table chunks, and a schema-v2 quality PASS with no failed or
warning checks. The existing user output tree was not changed.

## Next improvement milestones

| Priority | Milestone | Acceptance evidence |
|---|---|---|
| P1 | Ethics retrieval calibration | A corpus-owner-reviewed judged set covers rule text, author explanation, cases, tables, cross-page continuations, filters, and abstention; dense, hybrid, and reranked modes receive explicit release thresholds |
| P2 | Configurable document-structure profiles | Front/back-matter labels, chapter patterns, and canonical-title rules move behind tested profiles, with fixtures from multiple publishers and safe unknown-layout behavior |
| P2 | Context-aware retrieval assembly | Stable adjacency/parent identifiers allow query-time neighboring-chunk stitching without duplicate text, chapter leakage, or citation ambiguity |
| P2 | Table-specific retrieval | Large tables gain optional header-propagated cell/paragraph child records linked to their preserved parent table, with duplicate collapse and table-focused relevance tests |
| P3 | Runtime decomposition | Vector lifecycle moves out of `rag.py` in a separate failure-injected milestone; process supervision is extracted behind the stable facade |

## Completion rule

A milestone is complete only when its behavior is implemented, failure-injected,
covered by the full test suite and static checks, exercised against the relevant
real optional client where practical, independently reviewed, and published as a
mergeable draft PR. Review evidence must be durable in a committed audit or PR
review/comment rather than existing only in an ephemeral work log. A milestone
becomes integrated only after merge into `main`.

Rows marked “Implemented (draft)” still require review and merge; the earlier
merged milestones have reached the integrated state.
