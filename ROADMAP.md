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
| Immutable source-generation provenance | Implemented (draft) | [PR #34](https://github.com/toddlar00/rag-pipeline/pull/34): conversion, preprocessing, Docling, table recovery, chunking, and schema-v2 quality reports bind one exact PDF/Docling generation; multi-output leases prevent interleaved publishers; marker-owned private scratch is self-cleaning and dry-run prunable after hard termination. A disposable 912-page Ethics run produced 1,715 records, six bound recovered-table chunks, and a warning-free PASS report |
| Configurable document-structure profiles | Implemented (draft) | [PR #35](https://github.com/toddlar00/rag-pipeline/pull/35): an immutable reviewed registry now drives front/back matter, primary divisions, TOC hierarchy, canonical titles, cross-references, classification, quality checks, and exports. Schema-v3 chunk receipts attest the exact profile revision and digest; unknown, mismatched, legacy, and tampered profile evidence fails closed |
| Context-aware retrieval assembly | Implemented (draft) | [PR #36](https://github.com/toddlar00/rag-pipeline/pull/36): stable published-order linkage, exact index-generation binding, source/chapter/filter isolation, duplicate-text alias provenance, bounded neighboring evidence, and independent citations are wired through Chroma, Qdrant, CLI, UI, grounded answers, and evaluation. A disposable 1,715-record Ethics index passed a real context query |
| Table-specific retrieval | Implemented (draft) | [PR #37](https://github.com/toddlar00/rag-pipeline/pull/37): optional caption/header-propagated row children, source-wide continued-table eligibility, deterministic repeated-fragment identity, exact parent/child and source-shape attestation, family-aware result collapse, independent citations, and canonical-consumer isolation. A disposable Ethics run produced 69 children and returned the exact requested demographic row first |
| Ethics retrieval calibration | In progress | A current-schema clean-room rebuild preserved all 20 unique IDs behind 24 judgments in the exact 1,715-record corpus. A private owner packet now combines those judgments with 195 unique top-10 candidates across four modes, while a content-free receipt and strict four-mode release-policy contract make promotion explicit and review-bound. Actual owner decisions and final thresholds remain outstanding |
| Process supervision extraction | Implemented (draft) | [PR #33](https://github.com/toddlar00/rag-pipeline/pull/33): deadline supervision, Windows/POSIX containment, startup gates, termination confirmation, and generic entrypoint policy moved to stdlib-only `process_supervision.py`; `rag.py` retains late-bound compatibility wrappers |
| Vector-index lifecycle extraction | Implemented (draft) | [PR #38](https://github.com/toddlar00/rag-pipeline/pull/38): a standard-library-only policy layer owns deterministic reconciliation, dirty-marker ownership, mutation epochs, exact-ID verification, callback-reentry exclusion, legacy-hash repair, and close/manifest/marker commit ordering |
| Cumulative release migration rehearsal | Implemented (draft) | [PR #39](https://github.com/toddlar00/rag-pipeline/pull/39): actual Chroma and Qdrant probes recreate the integrated schema-5 manifest, rebuild one exact collection to schema 8, preserve and query a sibling collection, verify the no-op path, and require immediate lock release |
| Evidence-grounded answer evaluation | Implemented (draft) | [PR #40](https://github.com/toddlar00/rag-pipeline/pull/40): corpus-pinned claim judgments, exact citation-entailment and unsupported-claim metrics, abstention and prompt-envelope fixtures, fail-closed named release gates, runtime source-identity hardening, and schema-v5 redacted reports |
| Durable operational attempt evidence | Implemented (draft) | [PR #41](https://github.com/toddlar00/rag-pipeline/pull/41): strict redacted attempt reports bind lifecycle timing and terminal outcomes; successful cleanup is receipted before state publication; corrupt evidence, clock skew, cancellation races, failed final writes, and resume boundaries converge conservatively and idempotently |
| Queue pressure and durable recovery drills | Implemented (draft) | [PR #42](https://github.com/toddlar00/rag-pipeline/pull/42): schema-v2 typed run aggregates, exact committed/attempted vector mutation and queue-pressure metrics, supervised hard-kill recovery, and synced-publication fault injection produce strict private redacted evidence |
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
  late-bound facade wrappers. Vector lifecycle is now extracted behind the same
  stable facade on the active milestone branch; physical backend adapters,
  leases, embeddings, and workers remain deliberately runtime-owned.
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
0.872/0.869/0.958 and MAP of 0.836/0.851/0.941. Those figures were diagnostic,
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

The current calibration-hardening pass rebuilds the same 912-page source under
conversion/chunk/quality/index schemas 2/3/4/8. The resulting chunks SHA-256 is
`a56f145f09a6c97efac1ad622e478a735b9f23fb1d48d4807ae89edd4fd7a790`:
the record count remains 1,715, all 20 unique judged stable IDs survive, and a
second exact resume performs zero vector mutations. Schema-v5 diagnostics now
measure vector, vector-reranked, hybrid, and hybrid-reranked Success@3 at
0.923/1.000/0.923/1.000, nDCG@10 at 0.872/0.986/0.874/0.986, and MAP at
0.836/0.977/0.862/0.977.

`evaluation_review.py` re-pins only after exact ID validation, produces a
private packet containing the 24 judged passages plus 195 unique top-10
retrieval candidates, and refuses promotion until every query and judgment is
explicitly approved with the exact owner attestation. Its portable receipt
contains hashes, counts, tags, and time but no corpus/query text, stable IDs,
paths, or reviewer label. `evaluation_release.py` then requires one immutable
approved policy to bind that receipt, the reviewed query/corpus bytes, the
model-artifact lock, retrieval configuration, and complete Success/Recall/
nDCG/MAP/abstention/filter/false-answer gates for all four modes. Direct CLI
overrides fail closed. This work deliberately does not create approval or final
thresholds: those two decisions remain corpus-owner responsibilities.

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
complete repository suite passes with 1,210 tests and 7 platform skips; Ruff,
Python compilation, and `git diff --check` are clean. A disposable real run
converted all 912 pages of `Ethics.pdf`, then produced 1,715 chunks, six
PDF-bound recovered table chunks, and a schema-v2 quality PASS with no failed or
warning checks. The existing user output tree was not changed.
The stacked implementation is published as draft [PR
#34](https://github.com/toddlar00/rag-pipeline/pull/34), based on PR #33 until
the process-supervision dependency merges.

## Configurable document-structure profiles milestone

`document_profiles.py` is now the immutable policy boundary for publisher
structure. The default `us-law-casebook-v1` profile preserves the characterized
Arabic-chapter Ethics/casebook behavior; `roman-parts-book-v1` covers a second
family with Roman-numbered Parts plus bibliography and glossary back matter.
Each reviewed profile owns its context-qualified division patterns,
front/back-matter rules, TOC seeds and hierarchy, canonical-title format,
numbering styles, and fail-closed unknown-layout policy. Callers resolve one
profile and pass it explicitly through scaffold construction, chunk enrichment,
classification, cross-references, publication quality, pipeline orchestration,
resume commands, interactive menus, and exports. There is no mutable active
profile, automatic layout guess, or arbitrary runtime JSON policy.

Chunk completion advances to schema v3 and records the profile schema, name,
revision, and canonical policy SHA-256 as a strict top-level receipt. A
credential-free composite digest binds that receipt to the complete parameter
digest without persisting endpoint userinfo, query credentials, or API keys.
Old schema-v1/v2 chunk completion, a changed policy, missing fields, unknown
names, detached receipts, and tampering all force re-chunking.
At this profile milestone, conversion and corpus-quality evidence remained
schema v2; ordered resume keeps a
valid conversion generation, rebuilds chunks under schema v3, republishes the
exactly bound quality report, and only then reconciles the index. Generated
resume commands always serialize `--structure-profile`, including the default.

Adversarial tests cover registry immutability, strict profile registration,
Arabic/Roman/word ordinal normalization, context isolation, simultaneous runs
with different profiles, invalid CLI choices, full/chunk/batch forwarding,
interactive serialization, migration and receipt tampering, wrong-layout
failure before publication, two publisher fixtures, Roman display/export
semantics, and unchanged default-profile behavior. The complete repository
suite passes with 1,249 tests and 7 platform skips; Ruff and `git diff --check`
are clean. A disposable profile-only chunk run over the exact 912-page Ethics
Docling generation produced the expected 1,715 records, 447 scaffold entries,
14 chapters, complete source-lineage coverage, zero structural leaks, and a
schema-v3/profile-bound quality PASS. The existing user output tree remained
byte-for-byte unchanged.

An independent adversarial audit reproduced fail-open empty TOCs, profile
substitution during quality repair, a detached top-level receipt, omitted and
hallucinated LLM divisions, Roman-display subnumber artifacts, weak heading
repair, spaced-word ordinal truncation, endpoint-secret persistence, and an
endpoint-query provenance collision. Each reproducer is now a regression test.
The final re-audit confirmed exact deterministic LLM primary titles/pages,
complete division coverage, strict receipt/parameter binding, secret-free
full-URL endpoint fingerprints, and exact leading division titles; it reported
no remaining material issue.
The stacked implementation is published as draft [PR
#35](https://github.com/toddlar00/rag-pipeline/pull/35), based on PR #34 until
the immutable-source dependency merges.

## Context-aware retrieval assembly milestone

The current branch adds stable, intrinsic chunk IDs plus immediate
previous/next IDs within a deterministic source-and-explicit-chapter parent.
Linkage is attached only after final deduplication and publication ordering, and
schema-v3 quality evidence recomputes every linkage field so skipped,
nonreciprocal, cross-source, cross-chapter, or forged relationships fail closed.
Schema-v7 index manifests bind that exact chunks and quality generation. The
legacy schema-v6/schema-v2 pair remains queryable only with context disabled;
context-enabled retrieval requires regeneration.

Query-time assembly is opt-in with a window of zero to two. It preserves ranked
primary hits, reapplies content/chapter filters, reserves primary IDs, collapses
overlapping neighborhoods, renders byte-identical text once with all equivalent
source occurrences retained as aliases, and never crosses an unproven context
boundary. Total serialized supplementary evidence and per-neighbor text are
character-bounded, locating metadata is independently capped and charged to the
total, and grounded-answer prompts apply a separate 2,400-character excerpt cap
per source.
Neighbors receive independent stable IDs and citations, never inherit an anchor
relevance score, and cannot silently support the primary citation.

The behavior is available through Chroma and Qdrant search, CLI JSON and text
output, the local UI, and the evaluation harness. The service v1 response stays
context-off and therefore retains its established wire shape. Failure-injected
coverage includes stale and missing manifests, path replacement after snapshot
load, wrong vector text or identity metadata, legacy compatibility, linkage
tampering, duplicate provenance, prompt caps, UI serialization/rendering, and
both backend paths.

A disposable regeneration of the exact 912-page Ethics source produced 1,715
records in 14 context parents: 1,679 linked chunks, 36 safely isolated chunks,
and zero linkage issues. A schema-v7 Chroma index over that generation returned
three unchanged ranked hits plus five unique chapter-9 neighbors for a
contingent-fee query, using 6,925 of the 8,000 serialized supplementary
characters with no duplicate text, primary-ID reuse, source crossing, or
chapter crossing. The
existing user output tree remained unchanged.

The stacked implementation is published as draft [PR
#36](https://github.com/toddlar00/rag-pipeline/pull/36), based on PR #35 until
the document-profile dependency merges.

## Table-specific retrieval milestone

The optional `--table-children` path now derives one independently citable
retrieval record per strict Markdown data row while preserving the complete
parent table as the canonical publication record. Every child repeats its
caption, header, and separator, inherits exact source/page/section provenance,
and receives a stable identity from its parent plus row ordinal. Children are
appended after all canonical records and remain isolated from ordinary
previous/next context. Continued fragments sharing one exact Docling table ref
qualify by their aggregate source-row count, so a one-row continuation is no
longer skipped merely because its sibling fragment contains the other rows.

Repeated long rows can produce byte-identical row-packed fragments before the
optional expansion step. Deterministic fragment occurrence metadata now runs
on the default path before deduplication, preserves every legitimate source
occurrence, keeps the historical identity of occurrence zero, and gives later
occurrences distinct parent and child IDs. Source families must share an exact
Markdown schema. Quality schema v4 strictly recomputes every role, count,
ordinal, local dimension, source-wide row/fragment total, child rendering, and
ordering invariant; it also compares non-recovered families with bound Docling
matrix dimensions. PDF-recovered tables instead retain their hash-verified PDF
and conversion-manifest evidence because the defective native matrix is the
reason recovery was required.

Index manifest schema v8 records the exact table-child count and requires a
current quality binding whenever children exist. Search overfetches before
family collapse, suppresses a parent only when a child from that same family
is present, retains independently relevant sibling rows, and performs the
collapse before reranking and final top-N selection in both Chroma and Qdrant.
The deterministic offline BM25 adapter uses the same policy. Exports,
flashcards, question and brief generation, citation graphs, and RAPTOR consume
only canonical records, so enabling row retrieval does not duplicate study or
publication material. Safe schema-v3 quality and schema-v6/v7 index generations
remain readable where the requested feature does not require current evidence.

A disposable run over the exact Ethics Docling generation produced 1,715
canonical records plus 69 row children from 15 parent fragments across 13
source-table families. All 1,784 stable IDs were unique, all 69 children were
context-isolated, and the schema-v4 quality report passed with zero table issues.
A real 768-dimensional Chroma index published schema-v8 evidence for all 1,784
records. The query “What percent of U.S. lawyers were Black in 2021?” returned
the exact `Black | 12.6 | 12.4 | 5 | 5` row first; canonical export loaded all
1,784 records and emitted exactly 1,715. Original user Ethics artifacts remained
byte-for-byte unchanged.

Adversarial review reproduced and closed partial continued-table expansion,
dimension-counter spoofing with JSON numeric lookalikes, identical-fragment
loss, colliding parent IDs, incompatible same-source schemas, default-path
deduplication loss, and false native-shape failures for PDF-recovered tables.
The final independent re-audit found no remaining material issue. The complete
suite passes with 1,304 tests and 7 platform skips; Ruff, the CI compile set,
dependency/model-artifact policies, both offline retrieval baselines, and
`git diff --check` pass. The stacked implementation is published as draft [PR
#37](https://github.com/toddlar00/rag-pipeline/pull/37), based on PR #36.

## Vector-index lifecycle extraction milestone

`vector_lifecycle.py` now owns the backend-neutral transaction policy for one
exact vector-index generation. It rejects duplicate source identities, plans
deterministic additions, replacements, and removals, acquires or reuses a
collection-scoped dirty marker before worker startup, and revalidates marker
ownership immediately before every physical mutation. Each mutation advances
an internal epoch, so verification evidence obtained before a later delete,
create, or upsert cannot authorize commit.

The Chroma and Qdrant paths retain their physical clients, locks, embedding
pipelines, bounded scans, and backend-specific mutation checks in `rag.py`, but
both now use the same lifecycle for reconciliation and publication. Commit is
strictly ordered as marker revalidation, client close, marker revalidation,
manifest publication, and marker cleanup. A close, manifest, ownership, or
cleanup failure therefore cannot silently publish a clean but unverified index.
Late-bound facade callbacks preserve the existing public and monkeypatch seams.

Failure-injected tests cover add, replace, remove, mixed and no-op plans;
foreign-marker replacement; marker creation failure; ownership loss after
embedding and between upserts; receipt invalidation; rebuild verification; and
every close/manifest/cleanup failure boundary. The full repository suite passes
with 1,335 tests and 7 platform skips, along with Ruff, compile checks,
dependency/model-artifact policy checks, and `git diff --check`. Disposable
real-client runs against Chroma 1.5.5 and Qdrant local mode each completed a
create, no-op, and mixed update with exactly two final records, two changed
records, one removal, matching manifest hashes, and no residual dirty marker.
An independent exploit-oriented re-audit reproduced and closed callback
reentry during verification and commit, then found no remaining code blocker.
The stacked implementation is published as draft [PR
#38](https://github.com/toddlar00/rag-pipeline/pull/38), based on PR #37.

## Cumulative release migration rehearsal

The release candidate is a cumulative stack over the last integrated `main`.
Its document profiles, source-generation receipts, context assembly,
table-row retrieval, process supervision, corpus attestation, and vector
lifecycle policies therefore need one release-shaped proof in addition to the
focused stacked PRs. The rehearsal constructs the exact index-manifest payload
emitted by integrated schema 5, keeps a second collection in the same physical
database, and runs the candidate indexer without an explicit full-reindex
override.

Both actual local clients must classify that old generation as incompatible,
rebuild only the selected collection into schema 8, publish the exact two
target hashes and IDs, leave the sibling manifest byte-identical and its point
count intact, clear the owned dirty marker, accept a subsequent no-op, and
return both rebuilt and sibling records through search. The same subprocess
then removes the database directory before exit, converting latent Windows
handle retention into a test failure rather than tolerating it as cleanup
noise.

The first Qdrant rehearsal exposed a real Windows `storage.sqlite` handle that
survived public `close()` after repeated multi-collection operations. Local
Qdrant persistence creates short-lived SQLite cursors that are unreachable but
not necessarily finalized immediately. Client teardown now performs one
explicit garbage collection only for filesystem-local Qdrant on Windows;
remote clients, Chroma, and other platforms are unchanged. Direct tests prove
that scope, and both real-client migration probes now pass locally. Independent
review reproduced the lock with collection disabled and with generation-0/1
collection in five of five runs each; full collection released it immediately
in five of five runs and left no code blocker. The full suite passes with 1,342
tests and 7 platform skips, plus Ruff, compilation, dependency/model-artifact
policy, and diff checks. The full-collection pause and private local-client
detector are explicit temporary compatibility costs to retire when a pinned
Qdrant release closes every cursor. The complete candidate is published against
`main` as draft [PR #39](https://github.com/toddlar00/rag-pipeline/pull/39);
all 15 exact-head checks pass across Linux and Windows, Python 3.10-3.14, the
full locked CPU environment, local service, real Chroma/Qdrant migration,
offline retrieval, and both supply-chain jobs.

## Evidence-grounded answer evaluation milestone

Schema-v2 grounding fixtures now label every authored answer line as one ordered
claim. Supported claims name exhaustive stable source IDs plus text anchors that
must occur in the exact model-visible corpus excerpt; empty support sets label
claims that the runtime must withhold. Every v2 CLI run requires all queries to
pin both a valid corpus SHA-256 and positive record count, and release gates
reject judgments still marked for corpus-owner review. Sentinel answers must
have no claims, citation-only denominator padding is invalid, and scorer version
2 is part of strict baseline compatibility.

Reports use micro-averaged `claim_citation_entailment_accuracy`,
`unsupported_claim_rate`, `answer_abstention_accuracy`, and
`prompt_injection_fixture_accuracy`, with exact claim/case and query
denominators globally and per slice. Safety rates are not rounded before gating.
The backward-compatible `grounding_accuracy=1` release shorthand expands into
every named metric applicable to the suite, so missing or independently failing
components fail closed without requiring a workflow-file change.

The Property and Constitutional Law CC0 suites each add a pinned adversarial
source plus positive and negative answer cases. The prompt fixture parses exact
one-line JSON envelopes, pins the full instruction prefix, checks payload
identity and marker ownership, and treats the expected safe answer outcome as
part of the score. Runtime hardening prevents source-controlled raw metadata
from forging an equivalent stable ID, scopes direct-quotation evidence to valid
citations in the same paragraph, and escapes next-line plus Unicode line and
paragraph separators inside source JSON. Summary reports hash claim and source
identities. These deterministic fixtures validate serialization and citation
policy; they do not claim that a live model is generally injection-resistant or
perform open-ended semantic entailment.

Both offline suites report perfect claim-entailment, answer-abstention, and
prompt-fixture accuracy with zero exposed unsupported claims. The final local
repository run passes 1,372 tests with 7 platform skips, plus both baselines,
dependency/model-artifact policy, Ruff, Python compilation, and diff checks. An
independent exploit-oriented audit reproduced each reported boundary and found
no remaining material blocker. The stacked implementation is published as
draft [PR #40](https://github.com/toddlar00/rag-pipeline/pull/40), based on the
cumulative rehearsal in PR #39.

## Durable operational attempt evidence milestone

Every managed attempt now publishes a strict schema-v1
`attempt.report.json`. The content-free report binds the exact job, attempt,
run, and operation while recording submitted, manager-start, worker-start,
cancel, recovery, cleanup, and finish milestones plus derived dispatch,
startup, worker, cancellation, recovery, and total durations. It records the
terminal reason, finalizer, worker-telemetry status, cleanup result, and exact
process-recovery action without exposing arguments, paths, credentials,
process identities, logs, exceptions, prompts, or model output.

Recovery publishes a preterminal receipt only after definitive cleanup, so a
failed final report write cannot erase a successful exact-worker termination.
Unconfirmed actions remain retryable. Missing, malformed, oversized, or
future-clock evidence cannot suppress conservative terminalization; malformed
cancellation evidence is isolated from separately valid process identity.
Cancellation publication is serialized with state transitions and resume,
and late accepted requests are merged as requested-but-unobserved without
rewriting the terminal classification. Terminal repair is bounded, private,
monotonic, idempotent, and preserves the exact recovery action.

The clean commit passed 1,412 tests with 7 platform skips, Ruff, compilation,
dependency and model-artifact policy, and diff checks. An independent
exploit-oriented audit reproduced the cleanup, report-publication, clock,
corrupt-marker, cancellation, and cross-attempt resume races and found no
remaining P0/P1 blocker. The implementation is published against PR #40 as
draft [PR #41](https://github.com/toddlar00/rag-pipeline/pull/41).

## Queue pressure and durable recovery drill milestone

Run-report schema v2 now retains typed numeric and boolean stage aggregates,
rejects type drift and non-finite cumulative values before event publication,
and binds recovery source counts, active-stage closures, and closure duration
into the terminal event. Repeated interrupted finalization is exact even when
the derived report is missing, while schema-v1 committed terminal reports
remain readable.

Successful index outcomes expose exact physical collection, record-delete, and
upsert calls plus bounded-queue admissions, saturation events, and wait time.
Failed index stages use separately named content-free attempt metrics and an
explicit committed flag, preserving evidence without claiming that ambiguous
physical work committed. Backend invariants distinguish Chroma batching,
Qdrant's single batched deletion, and append-only Qdrant updates with no prior
point to delete.

The disposable operational drill launches a telemetry worker through the
existing startup gate and Windows Job Object/POSIX process-group containment,
requires confirmed process-tree cleanup before recovery, and fails closed when
confirmation is uncertain. A second drill injects two synced-folder sharing
violations and proves one pinned private staging payload is retried, published,
verified, and cleaned. The strict size-bounded report contains no paths,
process identities, exceptions, credentials, corpus text, prompts, or model
output.

The exact clean commit passed 1,466 tests with 7 platform skips, Ruff,
compilation, dependency and model-artifact policy, and diff checks. An
independent exploit-oriented audit reproduced containment, cleanup-confirmation,
schema, ownership-race, queue-overflow, and physical-operation boundaries and
found no remaining P0/P1 blocker. The implementation is published against PR
#41 as draft [PR #42](https://github.com/toddlar00/rag-pipeline/pull/42).

## Next improvement milestones

The former P2 release rehearsal, P3 grounded-answer evaluation, P4a durable
attempt evidence, and P4b queue-pressure/recovery-drill work are now published
as draft PRs #39 through #42. The remaining material priority is:

| Priority | Milestone | Acceptance evidence |
|---|---|---|
| P1 | Ethics retrieval calibration | A corpus-owner-reviewed judged set covers rule text, author explanation, cases, tables, cross-page continuations, filters, and abstention; dense, hybrid, and reranked modes receive explicit release thresholds |

## Completion rule

A milestone is complete only when its behavior is implemented, failure-injected,
covered by the full test suite and static checks, exercised against the relevant
real optional client where practical, independently reviewed, and published as a
mergeable draft PR. Review evidence must be durable in a committed audit or PR
review/comment rather than existing only in an ephemeral work log. A milestone
becomes integrated only after merge into `main`.

Rows marked “Implemented (draft)” still require review and merge; the earlier
merged milestones have reached the integrated state.
