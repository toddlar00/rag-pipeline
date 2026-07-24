# Improvement Roadmap

This roadmap was reconstructed on 2026-07-21 from the repository, tests, and
published pull requests after the original chat brainstorm was not preserved as
a durable artifact. It was comprehensively refreshed on 2026-07-24 against
`main`, the exact PR #43 head, every open pull request, CI policy, the full test
suite, and the two Claude-authored plans under `docs/`. It records the completed
work without treating stale plan checkboxes as backlog. A second exact-tree
audit on the same date covered the current R0/R4 commits and local tree,
module/import shape, security-sensitive UI and cloud-transport boundaries,
all 43 pull requests, review state, and the executable CI/evaluation surfaces.
The remaining improvements below are ordered and acceptance-testable.

Status terms:

- **Baseline** — present in the initial private-repository snapshot on `main`.
- **Implemented (draft)** — coded, validated, pushed, and opened as a draft PR,
  but not yet merged into `main`.
- **Integration candidate (draft)** — all implementation histories are combined
  in one draft PR, pending exact-head validation, review, and merge.
- **Integrated** — present on `main` through the cumulative integration PR.
- **Implemented locally** — coded, committed, and validated in the local
  repository, but not yet pushed, reviewed, or merged.
- **In progress** — active branch; not yet published as a PR.
- **Owner review pending** — implementation is complete in a draft PR, but a
  release decision requires an explicitly identified human owner.
- **Planned** — scoped direction, not yet implemented.

## Current status

| Milestone | Status | Durable result |
|---|---|---|
| Foundation | Baseline | End-to-end PDF ingestion, enriched chunking, shared LLM runtime, grounded answers, hybrid retrieval/reranking, evaluation harness, Chroma/Qdrant indexing, CLI, UI, docs, and tests |
| Repository truth and exhaustive source gate | Implemented locally | Local commit `88fd301` relocates and tests the two local launchers, ignores `tmp/` and `.worktrees/`, replaces the stale compile list with deterministic Git-index discovery, curates the Claude plans and developer guide, adds the process-supervision ADR, and records the unresolved private-source policy as an explicit owner decision. Its exact tree passes 1,507 tests with 7 skips |
| Ethics corpus coherence and publication quality | Implemented (draft) | [PR #31](https://github.com/toddlar00/rag-pipeline/pull/31): canonical scaffold reconstruction, exact source identity, complete tables and nested footnotes, exact embedding budgets, regenerated exports, and an exactly reconciled 1,715-record Chroma index |
| Machine-readable corpus quality attestation | Implemented (draft) | [PR #31](https://github.com/toddlar00/rag-pipeline/pull/31): schema-v1 report binds the exact Docling source, chunks bytes, parameters, source-lineage coverage, tables, normalization, classification, entities, token budgets, and stable/hash roots; resume, export, retrieval, and index publication fail closed on missing, stale, malformed, or mismatched evidence |
| Synced-folder publication and read resilience | Implemented (draft) | [PR #31](https://github.com/toddlar00/rag-pipeline/pull/31): bounded Windows sharing-violation retries republish only a pinned staging file; marker and exact artifact reads retry only content-identical ctime churn while failing closed on content-generation changes; exact hashes bypass unsafe stat caching on Windows |
| Immutable source-generation provenance | Implemented (draft) | [PR #34](https://github.com/toddlar00/rag-pipeline/pull/34): conversion, preprocessing, Docling, table recovery, chunking, and schema-v2 quality reports bind one exact PDF/Docling generation; multi-output leases prevent interleaved publishers; marker-owned private scratch is self-cleaning and dry-run prunable after hard termination. A disposable 912-page Ethics run produced 1,715 records, six bound recovered-table chunks, and a warning-free PASS report |
| Configurable document-structure profiles | Implemented (draft) | [PR #35](https://github.com/toddlar00/rag-pipeline/pull/35): an immutable reviewed registry now drives front/back matter, primary divisions, TOC hierarchy, canonical titles, cross-references, classification, quality checks, and exports. Schema-v3 chunk receipts attest the exact profile revision and digest; unknown, mismatched, legacy, and tampered profile evidence fails closed |
| Context-aware retrieval assembly | Implemented (draft) | [PR #36](https://github.com/toddlar00/rag-pipeline/pull/36): stable published-order linkage, exact index-generation binding, source/chapter/filter isolation, duplicate-text alias provenance, bounded neighboring evidence, and independent citations are wired through Chroma, Qdrant, CLI, UI, grounded answers, and evaluation. A disposable 1,715-record Ethics index passed a real context query |
| Table-specific retrieval | Implemented (draft) | [PR #37](https://github.com/toddlar00/rag-pipeline/pull/37): optional caption/header-propagated row children, source-wide continued-table eligibility, deterministic repeated-fragment identity, exact parent/child and source-shape attestation, family-aware result collapse, independent citations, and canonical-consumer isolation. A disposable Ethics run produced 69 children and returned the exact requested demographic row first |
| Table-family evaluation correctness | Implemented locally | Commits `e6c91c7` and `b1c7dd1` add explicit owner-selected child aliases, a sealed corpus-bound attestation API, exact-once logical-qrel scoring, exact/accepted-child match provenance, grounding isolation, packet-size preflight, schema-v6 reports/baselines, and a 10-record/6-query CC0 CLI suite with hard-negative upper and lower gates. The actual Ethics table/context ablations and owner approval remain outstanding |
| Public UI and cloud-endpoint safety | Implemented locally | The R0A candidate removes the Gradio share path, binds `127.0.0.1` explicitly, centralizes versioned endpoint attestation before credential/cache/transport access, rejects redirects and ambiguous targets, isolates loopback proxies, prevents ambient `.netrc` credential replacement, and validates job persistence. It passes the full 1,678-test tree and an independent exploit-oriented audit |
| Residual local/cloud trust boundary | Implemented locally | R0B adds versioned release-security policy v1 across CLI/Python/UI/service/evaluation/workers/jobs: trusted-single-user UI opt-in, local-only egress, release inline-secret rejection and cache-off default, opaque custom-gateway tenancy, cache-only model loading with explicit verified sync, pinned provider transports, bounded streamed JSON for every Requests-owned provider path, policy-controlled proxy/CA trust, disabled auxiliary telemetry, and strict provenance. The exact local tree passes 1,806 tests with 7 skips |
| Ethics retrieval calibration | Owner review pending | [PR #43](https://github.com/toddlar00/rag-pipeline/pull/43): a current-schema clean-room rebuild preserved all 20 unique IDs behind 24 judgments in the exact 1,715-record corpus. A private owner packet now combines those judgments with 195 unique top-10 candidates across four modes, while a content-free receipt and strict four-mode release-policy contract make promotion explicit and review-bound. Actual owner decisions and final thresholds remain outstanding |
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
passed 24 of 24 exact-head checks, received a durable no-blocker audit record, and
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
the PR #31 audit trail and a local ignored
`output/Ethics_3/COHERENCE_AUDIT.md`. The latter is not durable in a fresh
clone; R0/R12 must preserve only policy-approved, content-free receipts or
digest-bound retained artifacts before treating this narrative as release
evidence.

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

This calibration-hardening slice is published in draft [PR
#43](https://github.com/toddlar00/rag-pipeline/pull/43). It remains owner-review pending
until the corpus owner records every decision and approves final release floors.

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
structure. The production-qualified default `us-law-casebook-v1` preserves the
characterized Arabic-chapter behavior. `roman-parts-book-v1` encodes a second
family with Roman-numbered Parts plus bibliography and glossary back matter,
but remains synthetic-only and experimental until R11 records an authorized
real-corpus receipt. Each registered profile owns its context-qualified
division patterns,
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

## 2026-07-24 evidence refresh

The forward plan below is based on the following repository state. These facts
are a snapshot, not release claims:

- `main` is `54cdb00`. The last published exact head is `e2196a1` on PR #43, 33
  commits and 80 changed files ahead of `main` with 28,881 insertions and 2,014
  deletions. That published head remains the last GitHub CI reference. The
  local branch adds commits `88fd301`, `e6c91c7`, `b1c7dd1` (the validated R4
  CLI follow-up), `db029ce` (R0A), and the validated R0B change documented
  below. R0B is not yet a published immutable GitHub head; its exact cumulative diff/counts must be
  recorded by R1 rather than inferred from the earlier PR heads.
- PRs #31 and #33-#43 form one dependency stack. PR #39 is cumulative only
  through PR #38; PRs #40-#43 are later stacked work. PR #32 is a standalone
  process-supervision design document, and PR #30 is an independent Dependabot
  update. Neither belongs to the current implementation stack.
- All implementation PRs are open drafts and GitHub reports them mergeable.
  They have durable self-audit evidence, but none has a submitted GitHub review.
  The repository's current GitHub plan does not permit protected-branch rules
  for this private repository, so required review and exact-head checks are not
  mechanically enforced.
- The pre-R0A statement-coverage probe reported 75% over application and tool
  code when tests were omitted. That number is directional: branch coverage is
  disabled and separately launched workers are not automatically combined. CI
  does not currently measure coverage. The current local tree passes 1,806 tests
  with 7 platform skips. The run emits 429 dependency deprecation warnings on Python
  3.14, primarily from FastAPI/Starlette's `asyncio.iscoroutinefunction`
  compatibility path.
- CI is broad across Linux, Windows, Python 3.10-3.14, the full CPU environment,
  the loopback service, both local vector clients, dependency resolution, SBOM,
  vulnerability, license, model-artifact, and offline-evaluation checks. Its
  PR #43 `py_compile` list is stale, however: it omits several current modules.
  The current R0 branch replaces it with a tested, deterministic compile of all
  Git-tracked Python sources; the current exact inventory
  contains 120 files.
- Four supply-chain exceptions expire on 2026-08-31: the Chroma vulnerability
  exception, normalized Torch and Torchvision audit skips, the PyMuPDF license
  exception, and FlagEmbedding's missing wheel-license metadata allowance.
- The GitHub repository is private and the connected application has
  administrative repository access. It has no issues, milestones, tags, or
  releases, and the open implementation stack has no submitted review. The
  local R0/R4 commits are not published because the current command-line OAuth
  token cannot push an ancestry that changes workflow files without GitHub's
  `workflow` scope; this is a publication/authentication condition, not a code
  validation failure.
- At the audited PR #43 head, `_run_civpro.py` and `_resume_civpro.py` remained
  in the root; `tmp/` and `.worktrees/` were not ignored; and `CLAUDE.md` plus
  `docs/` were untracked. The current R0 branch corrects those mechanical and
  documentation-state defects. The current roughly 2,700-line, 141 KB
  README still requires the task-oriented decomposition planned in R12.

### Pull-request topology and disposition

| PR range | Observed state | Roadmap disposition |
|---|---|---|
| [#1](https://github.com/toddlar00/rag-pipeline/pull/1)-[#2](https://github.com/toddlar00/rag-pipeline/pull/2) | GitHub marks merged; their histories landed through cumulative PR #28 | Integrated history; retain as audit evidence |
| #3-#27 | Closed without individual merge after their exact histories were combined in #28 | Correctly superseded; do not reopen merely to reproduce the old stack |
| [#28](https://github.com/toddlar00/rag-pipeline/pull/28)-[#29](https://github.com/toddlar00/rag-pipeline/pull/29) | Merged cumulative implementation and integration record | Current `main` baseline and rollback ancestor |
| [#30](https://github.com/toddlar00/rag-pipeline/pull/30) | Open non-draft Dependabot group; 13 direct dependency changes and stale generated locks | Keep out of R1; replace/rebase as reviewable R2 compatibility groups |
| [#31](https://github.com/toddlar00/rag-pipeline/pull/31) | Open draft from `main`; first current-stack implementation | Preserve focused corpus/quality review evidence; include its exact tree in R1 |
| [#32](https://github.com/toddlar00/rag-pipeline/pull/32) | Open standalone design draft | Superseded by implemented #33 plus the maintained ADR; close after R0 review |
| [#33](https://github.com/toddlar00/rag-pipeline/pull/33)-[#38](https://github.com/toddlar00/rag-pipeline/pull/38) | Open focused draft dependency chain | Preserve focused diffs/audits; integrate through one current cumulative head |
| [#39](https://github.com/toddlar00/rag-pipeline/pull/39) | Open draft cumulative PR to `main`, but only through #38 | Valuable migration rehearsal, not the current integration candidate |
| [#40](https://github.com/toddlar00/rag-pipeline/pull/40)-[#43](https://github.com/toddlar00/rag-pipeline/pull/43) | Open drafts stacked after #39; #43 is the last published/CI-green head | Include in the new R1 cumulative PR after owner/privacy and R0A/R0B gates |
| Local sequence through `88fd301`, `e6c91c7`, `b1c7dd1`, `db029ce`, and R0B | Validated local commits; no remote branch/PR | Restore workflow-capable GitHub authentication, then publish only as part of the exact current candidate |

No open project PR has a submitted GitHub review. “Mergeable” and self-audit
comments are not approval, and green checks on different stacked heads do not
compose into evidence for an untested cumulative tree.

### Program gate matrix

The top inventory records individual delivered capabilities. This matrix keeps
the five dimensions that determine whether a roadmap milestone is actually
done separate; “code exists” does not imply “integrated” or “owner approved.”

| Milestone | Implementation | Validation/evidence | Integration | Owner/reviewer gate | Next action |
|---|---|---|---|---|---|
| R0 repository/privacy truth | Mechanical work local | Current full suite includes it | Not published | Private-source data classes/history/PR surfaces require owner decision | Record decision, audit tree/GitHub/artifacts, reconcile #32 |
| R0A endpoint/exposure | Local commit `db029ce` | 1,678-test checkpoint; current full tree remains green | Not published | Exact cumulative security review absent | Include unchanged in R1 candidate |
| R0B release security | Implemented locally | 1,806 tests; dedicated hostile transport/policy coverage | Not published | Exact cumulative security review absent | Publish with R1, replay in R2 |
| R1 convergence | Not created | Prior stacked heads are green, not composable evidence | No current cumulative PR beyond #38 | Independent named review and manual merge gate required | Restore workflow-scoped auth and open one exact-head PR |
| R2 dependencies/licenses | PR #30 is an unsuitable bulk proposal | Universal-lock check fails; four exceptions expire 2026-08-31 | Separate open PR, excluded from R1 | PyMuPDF/repository/license decisions need owner | Split by compatibility domain, relock, replay transports |
| R3 Ethics calibration | Review/receipt machinery in #43 | Content-free receipt path is tested; private decisions absent | Draft PR #43 | Corpus owner must decide judgments/abstention/thresholds | Complete owner review without agent-fabricated approval |
| R4 table/context evaluation | Reusable exact semantics and CC0 CLI suite local | Portable baseline/gates pass | Not published | Private aliases/ablations require R3 owner labels | Run 0/1/2-context and table on/off four-mode study |
| R5 release contract | Planned; migration rehearsal exists in #39 | No tag/release/manifest/rollback execution | None | Version, distribution, privacy, and release approval needed | Start only after R0-R4/R2 gates and R12 Phase A |
| R6 vector-client debt | Windows Qdrant workaround exists in #39 stack | Reproducer and real-client evidence exist for pinned version | Draft stack | No policy decision unless workaround persists | Retest after R2; remove or isolate/version-gate |
| R7 quality gates | Exhaustive compile/Ruff exist; coverage/types/DAG do not | Point coverage probe is non-gating and misses subprocesses | None | Maintainer selects ratchet/tool baseline | Add architecture inventory, branch coverage, typed leaves |
| R8 dependency direction | Planned | Current import cycles are directly observed | None | No owner decision | Characterize consumers, then invert one boundary per PR |
| R9 orchestration decomposition | First policy leaves extracted; hot spots remain | Failure-injection suite provides characterization base | None | No owner decision | Wait for R7/R10A, then slice main/chunk/eval/OpenAPI |
| R10 performance/capacity | Telemetry and queue metrics exist | No stable capacity budgets or hardware baselines | None | Authorized corpus/hardware/cost scope needed for Phase B | Establish small/medium CPU Phase A before R8/R9 |
| R11 corpus/profile breadth | Second profile and synthetic fixtures exist | No authorized real receipt for `roman-parts-book-v1` | None | Corpus authorization/qualification required | Add content-free profile diagnostics and real receipt |
| R12 packaging/docs/UX | Security ADR and docs index started; packaging/split not done | Examples are not yet parser-executed in CI | None | Product language and remote-scope decisions remain | Complete console entry points and short release guides before R5 |

## Architecture and risk map

The proposed tree contains 120 Python files: 54 application/tool files
(51,509 lines) and 66 test files (36,591 lines). Size is not itself a defect,
but it makes the remaining concentration and dependency risks measurable.

| Surface | Current strength | Material remaining risk | Roadmap owner |
|---|---|---|---|
| Ingestion, source identity, and quality | PRs #31, #34, and #35 bind exact source generations, immutable structure profiles, recovered tables, lineage, and fail-closed quality receipts | The second profile is synthetic-only; `_chunk_document_locked` remains roughly 875 lines and `_prepare_source_preserving_chunks` roughly 410 | R9, R11 |
| Retrieval and vector publication | PRs #36-#38 add bounded context, table rows, family collapse, and one guarded lifecycle for both stores | Production table/context benefit is not owner-calibrated; Windows local Qdrant still needs private-client detection plus forced garbage collection | R3, R4, R6 |
| Evaluation and release | PRs #40/#43 plus local R4 bind judgments, grounding, review receipts, release modes, table policies, and portable baselines | Owner approval and private four-mode ablations remain absent; `eval.py`, `evaluation_release.py`, and `evaluation_review.py` form a lazy/import-time cycle that obscures the contract boundary | R3, R4, R8 |
| LLM execution | Integrated budgets, single-flight caching, adapter extraction, artifact locks, and transport accounting; local R0A/R0B adds endpoint validation, local-only consent, cache/tenant isolation, pinned provider transports, explicit environment trust, cache-only models, and release-safe secret/cache defaults | Exact-head review/CI is absent; R2 dependency upgrades can change SDK/HTTP behavior; application code cannot enforce OS DNS/firewall or validate a deliberately trusted interception proxy | R1, R2, R5 |
| Jobs, service, and recovery | Integrated durable jobs/service plus PRs #41/#42 provide containment, terminal evidence, cancellation, recovery, queue metrics, and real fault drills | `job_manager` imports `rag`, while `rag` lazily imports `job_manager`; POSIX descendants can deliberately escape the process group with `setsid()`, so worker extensions remain trusted code rather than sandboxed plugins | R5, R8 |
| UI and exposure boundary | Local Search, Export, Info, and Jobs use bounded workers/private storage; local R0A removes public sharing and R0B refuses startup without explicit trusted-single-user acknowledgement | Shared-host or remote UI remains unsupported and needs principal authentication plus origin/session controls as a separate product | R1, R12 |
| Supply chain | Universal hash locks, model byte locks, SBOM/ML-BOM, scheduled advisory/license checks, and real-client profiles are unusually strong | PR #30 is an unreviewable 13-package jump with stale locks; four policy exceptions expire 2026-08-31; Python 3.14 emits 429 dependency warnings | R2 |
| Static quality and architecture | Extracted leaves, direct failure injection, 1,806 passing tests, exhaustive source compilation, and a wide OS/Python matrix reduce regression risk | No branch/subprocess coverage ratchet, no type checker, minimal Ruff rules, and no enforced import DAG; `rag.main` remains too large | R7-R9 |
| Release and governance | The repository is private, PR evidence is detailed, and exact-head migration rehearsals exist | Thirteen project PRs (#31-#43) remain draft: twelve are stacked implementation PRs, while #32 is a superseded standalone design; the cumulative PR stops at #38, no review is submitted, no live issues/milestones exist, and there is no tag/release/rollback manifest | R0, R1, R5 |
| Documentation and product entry | README, `docs/README.md`, developer guide, maintained ADRs, and clearly historical Claude plans expose most operator/design knowledge | The README exceeds 2,600 lines; point-in-time evidence bloats this roadmap; vector lifecycle/table retrieval/source generation still lack ADRs; private-source policy remains unresolved | R0, R12 |

Two findings were release blockers on the published PR heads rather than
ordinary cleanup: the public Gradio share path bypassed the local-only product
premise, and hostname-only provider classification did not establish a safe
credentialed transport. The local R0A candidate closes both. They remain
integration gates until that exact tree is reviewed and merged; earlier heads
must not be released merely because their own checks were green.

The R0B audit then found a third class of release blocker: provider SDKs could
replace nominal endpoints, follow redirects, retry, or inherit proxy/CA state
after the application had approved only the provider name. The local R0B tree
pins or directly owns those transports and tests hostile environment state. It
is still an integration candidate, not a release claim, until R1 review/CI.

## Claude-plan reconciliation

The two files under `docs/superpowers/plans/` are useful design history, but
they contain 57 unchecked task boxes and stale branch, line-count, test-count,
and interface assumptions. They must not be executed or committed verbatim.

| Claude proposal | Actual repository state | Roadmap disposition |
|---|---|---|
| Repository hygiene | Implemented and validated on the current R0 branch except for the owner policy/PR lifecycle gates | Finish the private-source decision, publish the branch, and reconcile PR #32 before marking R0 complete |
| Process-supervision extraction | Implemented by PR #33 with a stdlib-only policy module, compatibility facade, direct tests, and cross-platform CI | Mark the plan implemented/superseded; preserve a concise ADR instead of live checkboxes |
| Document-structure profiles | Implemented by PR #35 with explicit per-call immutable profiles, fail-closed unknown layouts, and schema-v3 rebuild evidence | Retain the stronger implementation and maintained ADR; reject the plan's mutable global, fallback, and legacy-compatibility proposals |
| Stable adjacency/context retrieval | Implemented by PR #36 across stores, CLI, UI, grounded answers, and evaluation | Retain the independent-neighbor-citation ADR; add a complete owner-approved corpus ablation in R4 |
| Table-specific retrieval | Implemented by PR #37, including attested row children and family-aware result collapse; local R4 now adds family-aware judgment semantics and a portable CLI suite | Keep the code and local evaluator contract; require the owner-reviewed private table/context ablations before release calibration |
| Vector-lifecycle extraction | Implemented and failure-injected by PR #38 | Remove it from the active backlog; retain the temporary Windows/Qdrant compatibility cost in R6 |
| Ethics calibration | Review packet, receipt, and fail-closed four-mode release contract implemented by PR #43 | Corpus-owner relevance decisions and final thresholds remain R3 |
| Leaf-module static typing | No mypy or Pyright configuration, dependency, or CI gate exists | Implement incrementally in R7 after branch convergence |
| README split | Not implemented; the README has grown since the plan was written | Implement task-oriented documentation in R12 |
| Dissolve runtime dependency seams | Still present: `job_manager.py` imports `rag`, while `rag.py` lazily imports `job_manager`; `service_runtime.py` depends on both. `eval.py`, `evaluation_release.py`, and `evaluation_review.py` form a cycle | Promote from an assumption to the explicit R8 architecture milestone |

The process-supervision plan links a specification that is absent from the
current branch but present in standalone PR #32. PR #33 already implements that
design. R0 must convert any still-useful rationale into an ADR, repair the link,
and close or supersede PR #32 rather than merging an obsolete execution plan.

The Claude material also exposes a documentation-policy contradiction. It says
private source text must never be excerpted or paraphrased into committed
artifacts, while tracked README/roadmap passages and PR #37's description
include exact source-specific wording. The repository being private reduces
exposure but does not resolve the contradiction. R0 therefore requires an owner
decision about permitted metadata and a corresponding documentation audit
before integration. History rewriting is not implied; if the owner requires
historical removal, that must be a separately approved, carefully scoped action.

## Detailed forward roadmap

Priorities describe release order, not desirability. R0, R0A, R0B, and R1-R3
are the convergence gate and can proceed partly in parallel, but the complete
R0A/R0B trust boundary must precede the exact-head merge. R0B was initially
planned as a focused post-R1 change; implementation exposed SDK transport gaps
that affect release safety, so the roadmap deliberately advances it into the
single R1 cumulative candidate rather than merging an unsafe intermediate tree.
R4-R6 make the first versioned release credible. R7-R10
reduce change risk and operating cost after the branch stack has converged.
R11-R12 expand corpus confidence and product scope only after the same safety
boundaries are reusable.

### R0 (P0, small decision/enforcement; remediation conditional): establish documentation, privacy, and repository truth

**Outcome.** One current source of truth exists before any cumulative merge,
and local/private material cannot be mistaken for project content.

**Progress (2026-07-24).** The mechanical and documentation work is implemented
on `agent/repository-truth-compile-gate`: both launchers are moved and directly
tested; local transient directories are ignored; 110 proposed tracked Python
sources pass the exhaustive compile gate; the two plans are historical; the
developer guide plus the process-supervision, structure-profile, and context
assembly ADRs match the current stack; and a
content-free policy proposal inventories the remaining owner choice. Ruff,
dependency/model-artifact policies, diff checks, 13 focused tests, and the full
suite (1,507 passed, 7 skipped) are green. The GitHub-surface audit is recorded
below; owner policy selection, PR #32 disposition, and publication remain open.

**Work.**

- **Remaining owner decision:** decide and document which private-corpus
  artifacts are permitted in Git and PRs. At minimum distinguish aggregate
  metrics, stable IDs, page references,
  authored queries, content-free receipts, and raw/paraphrased source text.
- **Remaining after the decision:** audit tracked docs, active PR descriptions/
  comments, and proposed docs against that decision. Replace disallowed examples
  with synthetic or CC0 material.
- **Remaining after the decision:** extend the audit to retained CI artifacts,
  issue/release surfaces, and Git history by data class. Record whether existing history is accepted or needs a
  separately authorized remediation; do not rewrite it implicitly.
- **Remaining after the decision:** propagate the chosen boundary into
  contributor guidance, a PR template, evaluation/release tooling, and
  artifact-retention instructions. Add
  deterministic checks for file classes and known raw-artifact patterns while
  retaining human review for paraphrase and minimum-necessary disclosure.
- **Implemented locally:** add `Implemented/Superseded` status banners to both
  Claude plans and convert durable architectural rationale into short ADRs. Do
  not retain executable unchecked task lists as the active backlog.
- **Remaining PR lifecycle:** reconcile PR #32 with PR #33: bring forward only
  the current ADR, repair the
  dangling link, then close/supersede the standalone design PR.
- **Implemented locally:** update `CLAUDE.md` to match the current policy,
  quality, retrieval, evaluation, and operational module graph.
- **Implemented locally:** move `_run_civpro.py` and `_resume_civpro.py` under
  `scripts/`, correct their root discovery, and ignore `tmp/` plus `.worktrees/`
  without deleting either directory.
- **Implemented locally:** replace CI's hand-maintained source compile list with
  deterministic discovery of every Git-tracked Python file, with explicit
  exclusions documented in code.

**Acceptance evidence.** A fresh clone has no dangling project-relative links;
all tracked Python files are compiled by CI; the two plans clearly identify the
implementing PRs; the updated developer guide matches the module graph; the
privacy decision is recorded and a data-class audit covers the tree, GitHub
surfaces, retained artifacts, and history; contributor/template/evaluation/
release guidance agrees; deterministic enforcement and semantic human review
are recorded; local scratch/worktree directories stay untracked; and the full
suite remains green.

### R0A (P0, small): close public-exposure and credential-transport gaps

**Outcome.** No supported command can accidentally turn a private local corpus
into an unauthenticated public application or send a provider credential over
an untrusted/plaintext endpoint.

**Why this precedes integration.** At the audited PR #43 head, `ui.py` accepted
`--share` and passed it directly to Gradio while the product and service threat
model was otherwise loopback-only. Provider predicates classified only the
normalized hostname, so provider environment credentials could be selected for
plaintext or otherwise malformed URLs before the adapter enforced a complete
transport contract.

**Work.**

- Remove or fail closed on `ui.py --share`, set the Gradio bind host and
  `share=False` explicitly, delete the README instruction that advertises the
  public tunnel, and characterize the complete launch arguments in tests.
  Restoring remote access requires a separate owner-approved threat model with
  authentication, TLS, authorization, CSRF/origin controls, rate/tenant
  isolation, audit retention, corpus-disclosure rules, and a resolved
  distribution-license basis.
- Add one canonical endpoint validator used before key resolution and again at
  the transport boundary. Official provider identity must require `https`, the
  exact normalized host, an allowed/default port, no userinfo/query/fragment,
  and a reviewed base path. Custom cloud endpoints must use HTTPS except for an
  explicit loopback-only HTTP case; loopback must never auto-select an official
  provider's environment key.
- Reject ambiguous/malformed URLs, Unicode/IDNA and trailing-dot tricks,
  scheme-relative values, credential-bearing authorities, unsafe redirects,
  and path/query values that could leak through logs or endpoint IDs. Keep
  custom explicit-key behavior separate from automatic environment-key
  selection.
- Make the report/cache endpoint identity demonstrably credential-free. Retain
  only normalized nonsecret origin/base-path identity (or a hash); never fall
  back to the raw malformed URL.

**Acceptance evidence.** Parser and launch tests prove there is no public-share
path; the UI binds loopback explicitly; a matrix of official, custom HTTPS,
loopback HTTP, plaintext public, malformed, userinfo, redirect, port, IDNA,
query, and fragment cases fails or succeeds exactly as documented; no provider
environment key is read for an invalid/unofficial endpoint; transport tests
prove validation occurs before the first request; reports/logs contain no URL
credentials or secret-bearing path/query material; and the full cross-platform
suite remains green.

**Progress (2026-07-24).** The local R0A candidate implements
`endpoint_policy.py` as a standard-library-only, versioned trust boundary.
Official providers require an exact reviewed HTTPS origin and base path;
custom public targets require HTTPS; and plaintext is limited to canonical
literal loopback IPs. Userinfo, queries, fragments, IDNA/punycode, trailing-dot
and official-lookalike hosts, localhost aliases, alternate numeric addresses,
IPv4-mapped IPv6, unusable targets, ambiguous ports/paths, and redirects fail
closed without echoing submitted values. Custom endpoint identities are opaque,
and direct LLM composition validates before cache lookup.

Credential lookup is lazy and follows validation. Loopback never reads an
ambient provider key or proxy configuration. Credentialed Requests calls use
an explicit redacted Bearer-auth object so `.netrc` cannot replace the selected
key; OpenAI-compatible, Ollama, supported API-embedding, and cloud-reranking
calls disable and explicitly reject redirects, with finite deadlines. Background job
submission validates/canonicalizes endpoints before creating a spec, rejects
secret/endpoint spelling variants and post-terminator tricks, and the CLI emits
value-free parse errors. The Gradio launcher has no `--share` option and passes
`server_name="127.0.0.1"` plus `share=False` literally. The decision is recorded
in `docs/architecture/decisions/local-endpoint-boundaries.md`.

The focused endpoint, CLI, runtime, transport, job, UI, artifact, embedding, and
retrieval set passes 509 tests with one optional skip. The full tree passes
1,678 tests with 7 platform skips and the same 429 third-party
Python 3.14 deprecation warnings already tracked in R2. Ruff, diff checks,
113-source compilation, dependency/model-artifact policy, and all three
portable offline retrieval baselines pass. An independent exploit-oriented
audit found no remaining material R0A blocker.

### R0B (P0, medium, pre-R1 release gate): bind residual local/cloud trust

**Outcome.** Release defaults cannot expose UI actions to an unintended local
principal, persist private LLM output unexpectedly, or reuse custom-gateway
responses across credential tenants; network trust assumptions are explicit.

**Why R0A was insufficient.** Loopback prevents remote binding but does not
authenticate users on a shared host. The prior CLI admitted keys in process
arguments and persisted successful LLM text by default. Cache and single-flight
identity did not distinguish accounts sharing one custom gateway URL/model.
Provider SDKs could also honor ambient base URLs, redirects, retries, proxies,
and custom certificate configuration after the caller had approved only the
nominal provider. Runtime model loaders could initiate an apparently unrelated
Hub request on first use.

**Progress (2026-07-24).** Implemented locally on
`agent/release-security-policy`:

- `release_security.py` defines one immutable, standard-library-only schema-v1
  record. Release defaults are `local-only`, model `cache-only`, LLM cache
  `off`, inline keys rejected, auxiliary telemetry disabled, and UI disabled.
  Strict value-free receipts reject unsupported, malformed, missing, or extra
  fields; trusted workers are not described as a cryptographic boundary.
- The unauthenticated Gradio UI refuses both programmatic construction and CLI
  launch without `--trust-local-user`, displays the supported trusted-session
  boundary, binds literal loopback, disables sharing/analytics, and sends the
  same receipt through search workers and reindex jobs.
- CLI, direct Python, evaluation, service startup, UI, supervised workers,
  durable jobs, resume commands, conversion, chunking, indexing, retrieval,
  reranking, answer generation, and RAPTOR use the same policy. Durable records
  pin its version and opaque identity while preserving inner `--` arguments and
  excluding raw namespaces/credentials.
- Every cloud embedding, reranker, and generation path gates before credential
  lookup, provider/tokenizer import, cache access, or transport. An ambient
  Gemini key is ignored by local-only execution rather than disabling Ollama.
- Voyage, OpenAI, and Cohere embedding plus Cohere/Jina reranking data paths use
  fixed reviewed HTTPS origins, finite deadlines, explicit bearer auth, zero
  implicit SDK retries, policy-controlled Requests environment trust, redirect
  refusal, and strict response validation. All five paths plus OpenAI-compatible
  generation and Ollama now retain session ownership through bounded streaming,
  enforce JSON MIME/UTF-8/framing/depth and decoded-byte ceilings before parsing,
  and discard body-free failures. Unsupported MiniMax embedding IDs
  fail closed instead of calling an undocumented contract.
- Generation defaults and payloads were reconciled with current provider
  contracts: Gemini uses live stable `gemini-3.6-flash`, omits deprecated
  sampling parameters, and maps thinking to high/minimal; MiniMax uses M3,
  separates reasoning, maps adaptive/disabled thinking, and uses its current
  completion field. Legacy MiniMax M2.x cannot pretend to disable reasoning.
- Release custom gateways require a validated nonsecret namespace. Only its
  SHA-256 identity enters cache keys, single-flight identity, reports, events,
  jobs, and resume provenance. Cache record schema v3/key schema v2 rejects
  ambiguous legacy records and isolates tenant identities.
- Runtime model loading is cache-only and fails before Hub import on a miss.
  `tools/sync_model_artifacts.py` performs exact-revision, allowlisted,
  size-bounded, byte-verified synchronization through an owned Requests session
  without ambient Hub/netrc auth or SDK retry behavior. Redirects are capped
  and must remain credential-free HTTPS; staging is removed on every failure.
  A reviewed-sync runtime flag is explicit, and unpinned models additionally
  require the development profile plus the existing environment escape hatch.
- Chroma, Gradio, and Hugging Face auxiliary telemetry is disabled explicitly.
  Release cloud/model sync refuses named proxy, CA, Hub endpoint, provider base
  URL, and provider-mode environment overrides unless the operator explicitly
  trusts them; loopback never inherits them.
- The maintained threat model and feature data-flow/migration matrix are in
  `docs/architecture/decisions/release-security-policy.md` and the operator
  commands in README use the new defaults.

The exact local tree passes Ruff and the full suite: **1,806 passed, 7 skipped**
with the already tracked 429 dependency deprecation warnings. The dedicated
43-test release-boundary suite covers all API embedding families, both cloud reranker
families, hostile ambient endpoints/provider modes, Requests and Gemini
environment trust, redirects, malformed/non-finite response data, installed
google-genai transport construction, inline-secret redaction, local Ollama,
Chroma telemetry, and local-only ambient-key behavior. The broader focused
security/service/model/provider/search coverage is included in the full run;
31 additional provider-transport tests own the MIME, framing, decoded-size,
compression, nesting, timeout, safe-diagnostic, and cleanup matrix.

**Residual release work.** R1 must publish, run cross-platform CI on, and obtain
independent review of this exact cumulative tree. R2 must replay the transport
matrix against upgraded dependency versions and provider schemas. The seven
Requests-owned JSON paths now have explicit pre-parse ceilings and an adversarial
matrix; Gemini still materializes through google-genai and needs an equivalent
SDK ceiling or an owned REST replacement before this is an all-provider claim.
URL and application policy do not
attest OS DNS, routing, firewall, or a deliberately trusted interception proxy;
deployments that require destination enforcement must supply external egress
controls. Shared-host UI remains unsupported rather than falsely described as
authenticated.

**Acceptance evidence.** The UI cannot start without the explicit supported
principal assumption; local-only blocks every cloud data path before sensitive
work; release argv rejects inline keys without echo; plaintext cache retention
is opt-in; custom tenants cannot share cached or in-flight work; legacy cache
ambiguity fails closed; policy receipts contain no namespace/key value; model
cache misses do not download; hostile SDK/environment endpoints cannot replace
reviewed origins; redirects and implicit retries are disabled; and the complete
R0A endpoint matrix remains green.

### R1 (P0, medium): converge PRs #31-#43 and local R0/R0A/R0B/R4 into one reviewed exact-head change

**Outcome.** `main` contains one reproducible tree rather than a long-lived
stack whose middle cumulative PR stops before the current head.

**Work.**

- Freeze and record the intended exact tree. Create a new cumulative PR from
  the current local stack head after R0B to `main`, or equivalently advance a
  cumulative branch without rewriting the focused review histories. Include
  commits `88fd301`, `e6c91c7`, `b1c7dd1`, and `db029ce` plus the reviewed R0B
  commit. Do
  not merge the stacked PRs one by one and assume their earlier checks compose.
- Keep PR #30 out of this convergence change. Its dependency/API migration is
  R2, so functional integration and dependency churn remain independently
  diagnosable.
- Re-run every applicable workflow on the cumulative head, including the two
  real-client migration/lock-release probes, the full locked CPU suite, service
  loopback tests, offline suites, dependency policies, and supply-chain jobs.
- Obtain a formal human review of the exact cumulative diff. Self-authored audit
  comments remain useful evidence but are not a substitute for an independent
  submitted review.
- Publish a review manifest that maps focused PR/commit ranges and file domains
  to named reviewer roles. Require separate privacy/security, migration/release,
  evaluation, and cross-stack delta sign-offs, all bound to the final commit, so
  a roughly 35,000-line cumulative diff does not receive only nominal approval.
- Preserve `INTEGRATION_AUDIT.md` as the historical #1-#28 record and publish a
  new exact-head integration audit for the R1 candidate. Bind its scope,
  findings, remediation disposition, workflows, reviewer, and commit rather
  than relabeling the older audit as current.
- Because branch protection is unavailable on the present plan, record a manual
  merge checklist with exact commit, check conclusions, reviewer, privacy
  decision, migration result, and rollback commit. Alternatively, upgrade the
  repository plan and require checks/review mechanically.
- After the cumulative commit is on `main`, close the focused draft PRs as
  superseded and preserve their audit links, following the PR #28 precedent.
- Reconcile duplicate rebased local branch identities (`4ef7224`/`227a760` and
  `3ff6bd1`/`c2ef258`) only after integration, preserving one named rollback
  pointer until the release manifest exists.

**Acceptance evidence.** The cumulative PR has no unexpected tree difference
from the reviewed stack head; every required check passes on that exact commit;
Chroma and Qdrant each rebuild the target generation, preserve a sibling
collection, serve search, complete a no-op rerun, and release filesystem locks;
an independent review and manual/protected merge gate are durable; and a clean
clone of `main` reproduces all dependency-light tests.

### R2 (P0, medium-large, deadline 2026-08-31): resolve dependency and license debt

**Outcome.** No release relies on an expired exception or an untested bulk
dependency update.

**Work.**

- Replace or rebase PR #30 after R1. It proposes 13 direct upgrades, including
  major API lines for OpenAI, Cohere, Google GenAI, Sentence Transformers, and
  PDF/vector dependencies, but currently fails the universal-lock consistency
  check and does not update generated locks.
- Upgrade in reviewable compatibility groups, regenerate every universal lock,
  and test each provider adapter with deterministic fake transports plus one
  explicitly authorized live smoke where credentials and cost policy permit.
- **Implemented locally:** recompute the direct dependency surface before
  upgrading it. R0B replaced Voyage/OpenAI/Cohere embedding and Cohere rerank
  SDK transports with pinned direct HTTP, so the unused Voyage, OpenAI, and
  Cohere SDK declarations and 13 SDK-only transitive packages were removed from
  the full lock (16 packages total). Dependency policy now rejects their silent
  reintroduction while those providers use the owned transport.
  Keep google-genai only while its typed response contract justifies the
  policy-pinned client, and preserve Requests as the owned provider transport.
- Re-run the R0A/R0B transport-conformance suite after each provider SDK group
  so dependency changes cannot restore redirects, ambient credentials, or an
  unreviewed proxy/certificate policy.
- Add a reviewed provider-contract inventory for endpoint, model ID, request
  fields, output limits, thinking semantics, response schema, and deprecation
  date. A scheduled advisory check may report drift, but changing a model or
  payload remains a reviewed source change. This prevents another retired
  default model, undocumented embedding endpoint, or stale context limit from
  surviving until a production call.
- **Implemented locally for the seven Requests-owned paths:** bound responses
  before JSON materialization using both a conservative `Content-Length` check
  and decoded streamed-byte ceiling, while retaining strict shape/numeric
  validation. The matrix covers absent, malformed, ambiguous, and dishonest
  lengths without logging bodies. Prove an equivalent limit in google-genai or
  replace Gemini with the owned transport before closing the all-provider item.
- Treat the current Python 3.14 warning inventory as migration evidence rather
  than harmless noise. Upgrade or constrain FastAPI/Starlette/websockets and
  the SWIG-backed clients so supported versions have an owned, budgeted warning
  baseline before Python 3.16 removes the deprecated asyncio APIs.
- Re-evaluate the Chroma vulnerability exception against the then-current pinned
  release. If no fixed embedded client exists, record a fresh owner decision,
  retain filesystem-local-only use, and keep Qdrant mandatory for networked
  deployments.
- Decide and record the legal basis for PyMuPDF. Either record the applicable
  commercial license, accept the documented AGPL obligations for the intended
  distribution, or replace/remove the dependency before distribution or hosted
  use. During the compatibility change, standardize on the supported `pymupdf`
  import rather than preserving the legacy `fitz` alias indefinitely.
- Choose and add an explicit repository code license, or record that the
  private code is intentionally proprietary/all-rights-reserved. Third-party
  inventory policy does not define rights in this repository's own code.
- Reconfirm the Torch/Torchvision normalized-version audit and FlagEmbedding
  upstream-license evidence; remove exceptions when tooling/package metadata
  permits rather than merely extending dates.

**Acceptance evidence.** Lock regeneration produces no diff on a second run;
resolution passes on Python 3.10 and 3.14; `pip check`, the full installed suite,
both vector-client probes, service profile, vulnerability policy, license
policy, SBOM/ML-BOM, model-artifact verification, offline release suites, and
the R0A/R0B transport-conformance suite all pass; every remaining exception
names an owner, narrow scope, evidence, and new
review date; no exception is expired on the release commit; and CI rejects new
project-owned deprecations while any unavoidable third-party warnings are
exactly scoped and time-bounded. Each provider SDK group has its own recorded
passing transport-conformance result before the next group or release proceeds.

### R3 (P0, owner decision): finish Ethics review and four-mode calibration

**Outcome.** The retrieval policy is grounded in owner-reviewed relevance, not
agent-authored labels or convenient thresholds.

**Work.**

- Have the corpus owner review all 14 current queries and 24 judgments in the
  private packet, explicitly accepting, editing, or rejecting each judgment.
- Finalize the content-free review receipt only from the reviewed packet. Do not
  infer approval from unchanged draft labels and do not put source text in the
  receipt.
- Run all four policy-bound retrieval modes against the exact approved corpus,
  model locks, index manifest, and judged-set digest. Select per-mode floors only
  after inspecting errors and confidence/denominator limits.
- Record known blind spots separately: the current set is small, table children
  are disabled for exact-ID correctness, and context-window benefit has not yet
  been established by a full-corpus ablation.

**Acceptance evidence.** No judgment remains `draft_requires_corpus_owner`; the
receipt validates against the exact query and corpus digests; all four modes
have explicit owner-approved floors and immutable configuration bindings; the
release command fails closed on any missing/mismatched approval; and reports
contain only policy-approved metadata. This milestone is blocked on human
review by design, not on additional code generation.

### R4 (P1, medium): make evaluation semantics match context and table retrieval

**Outcome.** New retrieval features can be calibrated without false misses,
inflated relevance, or answer-quality regressions.

**Work.**

- **Implemented locally:** add a versioned judgment-family schema so an
  attested row child may satisfy a judged parent exactly once. Parent, matching
  child, sibling row, unrelated table, duplicate family, and filtered-family
  cases must be explicit.
- **Implemented locally:** bind table-child generation policy, family
  semantics, context window, context budget, and collapse behavior into
  baselines and release policies.
- After R3, run context windows 0/1/2 across all four modes. Compare primary
  ranking, grounded claim/citation entailment, unsupported-claim rate,
  abstention, prompt size, latency, and returned-evidence size.
- Re-run table-disabled and table-enabled Ethics calibration with the same
  owner-approved queries. Report both retrieval benefit and any parent/child
  displacement rather than selecting only the favorable run.
- **Implemented locally:** add a portable CC0 table-family mini suite and CLI
  baseline with real generated children, sibling/unrelated hard negatives, and
  two-sided slice gates so an incorrectly higher score also fails.
- **Implemented locally:** keep retrieval relevance aliases independent from
  grounding `entailed_by` evidence, add a regression case for that boundary,
  and expose an explicit `exact` versus `accepted_table_child` match kind in
  detailed reports instead of requiring reviewers to infer it from two IDs.
- **Implemented locally:** preflight the serialized owner-review packet against
  its size ceiling, and replace the former direct-Python
  `table_family_members` dictionary seam so non-CLI callers cannot mistake
  caller-supplied membership for corpus-derived attestation.
- Expand the approved set with chapter-balanced paraphrases, hard negatives,
  filters, tables, cross-page continuations, numeric/multi-hop cases, ambiguity,
  and abstention. Keep raw private evidence out of committed reports.

**Acceptance evidence.** Deterministic unit tests cover every family case; one
gold judgment cannot receive multiple credit; four-mode baselines reject schema
or configuration drift; the complete ablation is reproducible; grounded-answer
gates pass on the approved suite; and the owner signs off on the expanded set.

**Progress (2026-07-24).** Commit `e6c91c7` implements the versioned
`table_family.accepted_child_chunk_ids` contract. It requires a positive
parent-chunk judgment, full corpus pinning, explicit review status, sorted unique
children, complete parent/child metadata attestation from the exact chunks
artifact, and an index-manifest child-count match. The scorer maps the parent
and only its selected children to one logical qrel, so a sibling, unrelated or
metadata-spoofed table, parent-after-child, or second accepted child receives no
extra credit. Review packets show the parent and every selected child together
and omit them from unjudged diagnostics. Report schema 6, judgment scorer 2,
review-packet schema 2, and release-policy schema 2 bind the table-generation,
collapse, context-window, and context-budget contracts; the two pre-existing
offline baselines were migrated and pass their zero-regression gates. The decision is
recorded in `docs/architecture/decisions/table-family-evaluation.md`. The exact
local tree passed 1,528 tests with 7 skips; the 168 focused evaluator, review,
release, offline-retrieval, table-core, and asset tests also pass.

Commit `b1c7dd1` replaces the raw membership-dictionary API
with an immutable factory-only attestation bound to corpus digest, record count,
and ID scheme; reports now distinguish an exact judgment match from the actual
accepted child that satisfied it while hashing both identities in summary mode.
It proves that retrieval aliases do not widen grounding entailment and checks
review-packet bytes before publication. The checked-in CC0 suite contains two
four-row fictional tables, 10 expanded records, 8 children, and 6 queries. Its
BM25 baseline reports Success@1 0.667, Success@3 1.0, MRR 0.833, Recall@1
0.583, Recall@3 1.0, nDCG@3 0.877, and MAP 0.833. Both sibling and unrelated
hard-negative slices are pinned to Success@1 0 and MRR 0.5. All three portable
CLI baselines pass; 195 focused tests pass with 1 optional skip, and the full
local tree passes 1,535 tests with 7 platform skips plus Ruff, exhaustive
111-source compilation, dependency/model-artifact policy, and diff checks.

This completes only the reusable correctness machinery. R3 owner review must
still freeze the private judgments; table-enabled query bytes need explicit
owner-selected children and a fresh receipt; and the 0/1/2 context plus
table-disabled/table-enabled four-mode Ethics ablations, grounded-answer checks,
latency/prompt/evidence-size measurements, expanded chapter-balanced cases, and
owner sign-off remain required before R4 is complete. The portable table-family
CLI fixture and reusable correctness follow-ups are complete; the remaining
work is private-corpus evidence and owner judgment, not another aliasing-policy
change.

### R5 (P1, medium): create a versioned release and migration contract

**Outcome.** Operators can identify, reproduce, migrate to, and roll back a
known release.

**Work.**

- Choose the first release version after R0B, R1-R4, and R2, add a changelog and
  release notes, create one product-version source, replace the two hard-coded
  service `1.0.0` values, expose a CLI version, and tag the exact commit. Attach no
  private corpus artifacts to a GitHub release.
- Define the supported command exit codes and public Python API/facade policy,
  and centralize the distributed artifact/report/service schema versions in a
  compatibility registry without forcing all schemas to advance together.
- Publish a compatibility matrix for Python versions, dependency profiles,
  CPU versus CUDA support tiers, production-qualified versus experimental
  structure profiles, artifact/report schemas, profile receipts, Chroma/Qdrant
  backends, and supported upgrade paths from the last `main` generation. The
  first release is reproducible CPU-only unless a hash-pinned CUDA environment
  passes an identified hardware qualification; `roman-parts-book-v1` remains
  experimental until R11 supplies an authorized real-corpus receipt.
- Turn the existing cumulative migration rehearsal into a documented preflight
  and rollback runbook. Define backup, failure, retry, dirty-marker, sibling
  collection, and downgrade expectations.
- State the process threat boundary: workers and extensions are trusted code,
  local supervision is not a sandbox, and a POSIX descendant can deliberately
  escape process-group containment with `setsid()`. Prohibit untrusted worker
  extensions in the supported release; stronger cgroup/container isolation is
  a separate product milestone.
- Add a machine-readable release manifest binding source commit, lock hashes,
  model-artifact lock, schema versions, evaluation policy/receipt digests, and
  completed workflow URLs or conclusions.
- Compose the R0B release-security record into a production/release profile that
  also fails if developer-only escape hatches such as
  `RAG_ALLOW_UNPINNED_MODELS=1` are enabled, while retaining their explicit,
  logged use in local development.
- Complete R12's Phase A packaging/docs slice: stable console entry points plus
  short install, security/data-flow, migration/rollback, and operator guides
  must exist before the tag even if the full README/UI redesign remains later.

**Acceptance evidence.** A disposable environment installs from locked inputs,
migrates both backends from the prior generation, verifies search/no-op/sibling
preservation, exercises the rollback runbook, and reproduces the release
manifest. The Git tag and release notes identify the exact reviewed commit and
known limitations; CLI/service version values agree; compatibility tests pin
public exit codes and supported schema migrations; and release-profile tests
reject every unpinned-model bypass. CPU/CUDA and structure-profile support tiers
are explicit, every supported install/entry-point example is executable, and
the trusted-worker/non-sandbox boundary is present in the release threat model.

### R6 (P1, small-medium): retire or isolate vector-client compatibility debt

**Outcome.** The Windows Qdrant cursor workaround is a tested adapter decision,
not permanent private-client coupling hidden in orchestration.

**Work.**

- Preserve a minimal reproducer for the Windows local-Qdrant SQLite handle that
  survives public `close()` after multi-collection migration.
- Re-run it when Qdrant is upgraded in R2. If the pinned client reliably releases
  every cursor, remove the private local-client detector and forced collection;
  otherwise isolate both behind a versioned backend adapter with an explicit
  sunset condition.
- Expand the real-client matrix to prove immediate directory deletion after
  create, incremental update, rebuild, sibling preservation, failure recovery,
  and no-op on Windows.

**Acceptance evidence.** The reproducer passes repeatedly on the supported
Windows runner; no remote or non-Windows client receives the workaround; the
adapter is version-gated and documented if retained; and removal is covered by
the same lock-release regression tests.

### R7 (P1, medium): add risk-based coverage, typing, lint, and architecture gates

**Outcome.** CI detects untested or boundary-breaking changes without demanding
a disruptive whole-repository rewrite.

**Work.**

- Commit Coverage.py configuration with branch measurement and subprocess data
  combination where supported. First publish a trustworthy baseline, then add a
  no-regression ratchet for safety-critical leaf modules and changed lines.
- Prioritize uncovered behavior in `supervised_worker.py`, process supervision,
  `rag.py` orchestration, the UI, inspection/scaffold CLIs, artifact review, and
  dependency/security tooling. Distinguish genuinely uncovered code from child
  processes that were not traced by the initial probe.
- Introduce Pyright or mypy on stable stdlib-only leaves first:
  `operation_contracts`, `endpoint_policy`, `release_security`, `cli_policy`,
  `index_state`,
  `document_profiles`, `vector_lifecycle`, `service_contracts`,
  `evaluation_release`, and related policy records. Add security-critical
  `job_runtime` after its imported policy/storage layer is clean. Expand by
  dependency layer, not by blanket ignores.
- Add an import-DAG test that prevents leaf modules from importing `rag` or
  physical clients, detects both current reciprocal cycles, and records the few
  intentional facade edges. Generate the direct `rag._...` test-reference
  inventory so compatibility debt is reduced deliberately rather than broken
  accidentally.
- Add a deterministic AST architecture inventory for tracked-source counts,
  function spans/arity, import cycles, and private-facade references. Use it to
  generate or check roadmap/maintainer evidence instead of hand-maintaining
  volatile counts.
- Stage additional Ruff rules after zero-warning baseline cleanup. Add
  property/state-machine tests for strict JSON schemas, ownership markers,
  publication recovery, pagination, and policy parsing; do not use a high
  percentage target as a substitute for failure-injection quality.
- **Implemented locally for the seven Requests-owned paths:** adversarial tests
  cover wrong MIME, oversized, deeply nested, decompression-expanded, chunked,
  truncated, duplicate-field, non-standard-number, invalid-UTF-8, and slow JSON.
  They prove the streamed decoded-byte ceiling fires before parsing and that
  diagnostics remain body- and credential-free when `Content-Length` is absent,
  malformed, ambiguous, or dishonest. Extend the same invariant to Gemini when
  its R2 transport decision is made.
- Add a narrowly configured static security scan after triaging its baseline;
  gate new high-confidence credential, URL, subprocess, unsafe-deserialization,
  and path-handling findings rather than accepting a permanent suppression
  inventory.

**Acceptance evidence.** CI reports branch and statement coverage with a
documented subprocess caveat; changed safety-critical code cannot lower its
ratchet; the initial typed leaf set passes with no broad suppression; forbidden
import edges fail a focused test; the architecture inventory reproduces the
documented snapshot; the provider size/MIME matrix fails closed without body
retention; and the full Python/OS matrix remains green.

### R8 (P2, large in small slices): remove runtime and evaluation dependency cycles

**Outcome.** Runtime composition points inward through narrow protocols while
`rag.py` remains a compatible public facade and evaluation review becomes a
one-way application over shared contracts rather than a reciprocal import.

**Work.**

- Characterize job submission, startup gating, cancellation, exact-worker
  termination, recovery, reindex, search, service request, and Windows cleanup
  behavior before moving imports.
- Define narrow runtime protocols for pipeline execution, search/index
  operations, storage/retention, and telemetry. Inject implementations into job
  and service layers instead of importing the `rag` module as a service locator.
- Move query/corpus validation and review-packet input contracts out of
  `eval.py` into a dependency-light evaluation domain module. Let `eval.py`,
  `evaluation_release.py`, and `evaluation_review.py` depend one-way on that
  layer instead of forming their current three-module cycle. Preserve CLI
  wrappers and schema bytes through characterization tests.
- Move application composition to one root module. Keep late-bound wrappers and
  re-exports until all tests and callers have migrated.
- Enforce the target dependency graph in the R7 architecture test and delete
  compatibility edges only after direct consumers are proven absent.

**Acceptance evidence.** `job_manager.py` and `service_runtime.py` no longer
import `rag`; `rag.py` does not need a lazy `job_manager` import for core
composition; `eval.py`, `evaluation_release.py`, and `evaluation_review.py` are
acyclic; existing CLI/service/Python surfaces remain compatible; direct
protocol tests cover error propagation and recovery; and every supervision, evaluation, and
real-client regression passes unchanged.

### R9 (P2, large in behavior-preserving slices): decompose orchestration hot spots

**Outcome.** High-risk changes no longer require editing thousand-line control
flows or mirrored schema builders/validators.

**Work.**

- Extract the roughly 1,100-line `rag.main` and 365-line `interactive_menu`
  into a typed
  command registry plus leaf handlers while preserving exact help text, defaults,
  exit codes, resume serialization, and monkeypatch seams.
- Split the roughly 875-line `_chunk_document_locked` into pure source
  preparation,
  classification, chunk assembly, quality validation, and publication stages
  coordinated by one transaction object. Preserve leases, provenance, fault
  injection, and commit ordering.
- Replace internal 20-29-parameter orchestration calls (while preserving public
  facade signatures) with frozen operation configuration and injected
  collaborator objects. Move mutable embedding, reranker, throttle, artifact,
  and BM25 cache ownership to an explicit composition/lifecycle boundary so
  concurrent tests and services can isolate state.
- Split `eval._main_with_args` and `_evaluate_impl` into validation, execution,
  scoring, measurement, and publication stages after R8 removes the reciprocal
  review import. Preserve exact report/baseline bytes and exit behavior.
- Consolidate `quality_core`'s large report builder and validator around a
  declarative exact-field schema so they cannot silently drift. Add generated
  canonical examples plus mutation/property tests before deleting mirrored code.
- Replace the roughly 510-line hand-built OpenAPI function with a declarative
  schema/route source that still reproduces the committed static snapshot and
  cannot drift from the live service contract.
- Split very large test files by behavioral contract only when doing so improves
  ownership and diagnostics; do not combine refactoring with policy changes.

**Acceptance evidence.** Characterization snapshots prove CLI compatibility;
each pure stage has direct tests; all failure-injection and source-generation
tests pass; report bytes/digests remain stable unless a deliberate schema
version changes; and each slice is independently reviewable and reversible.

### R10 (P1/P2, medium): establish performance baselines and capacity budgets

**Outcome.** Existing telemetry becomes an operational decision tool rather
than descriptive data with no release ceiling.

**Work.**

- **Phase A, before R8/R9:** record stable small/medium CPU baselines for the
  orchestration hot spots those refactors will touch, then add generous
  no-regression ceilings. Keep this slice dependency-light and non-flaky; its
  purpose is to catch architectural performance regressions, not claim full
  production capacity.
- **Phase B, after the architecture slices:** expand to authorized large-corpus,
  GPU, overload, recovery, and cost scenarios and publish operator capacity
  recommendations.
- Define representative small, medium, and authorized large-corpus scenarios
  for conversion, chunking, embedding, indexing, retrieval, reranking, grounded
  answer generation, queue saturation, recovery, and retention.
- Include evaluator/review workloads: corpus/query record counts, diagnostic
  report bytes, unique candidates, review-packet construction memory, strict
  parser limits, and final serialized bytes. The current code materializes
  these structures in memory; establish a measured threshold before choosing a
  streaming redesign.
- Record wall time, p50/p95 latency, peak memory, queue wait/saturation, vector
  mutations, storage growth, token reservations/use, cache behavior, and
  caller-priced cost under pinned hardware/model/dependency identities.
- Add an offline, lock-derived model-sync plan that reports per-bundle and
  aggregate download bytes, already-present bytes, peak staging/destination
  space, and free-space preflight. Baseline the minimal PDF plus default
  embedding selection separately from the roughly 7.5-GiB all-consumer set.
- Add generous regression budgets first, normalize noisy measurements, and keep
  cost/network benchmarks opt-in unless credentials and spend ceilings are
  explicit. Exercise bounded overload and cancellation rather than only the
  success path.

**Acceptance evidence.** Phase A guards the R8/R9 hot paths before their first
behavior-preserving slice. All benchmarks emit redacted schema-validated
reports; baselines name hardware and model/lock identities; repeated local/CI
runs show documented variability; material regressions fail a dedicated
non-flaky gate; the offline sync plan exactly reconciles to the lock and detects
insufficient space before transport; and Phase B yields operator-facing
queue/recovery capacity recommendations.

### R11 (P3, medium): qualify profiles and retrieval across authorized corpora

**Outcome.** The second structure profile and general retrieval claims are
supported by real authorized evidence, not only synthetic fixtures or one
private casebook.

**Work.**

- Build a content-free corpus/profile qualification matrix keyed by immutable
  generation digest, profile/revision, page count, and validation status rather
  than filenames or excerpts.
- Add a structure-only diagnostic command that reports recognized divisions,
  unmatched headings, front/back-matter decisions, and profile digest without
  emitting source text.
- Validate `roman-parts-book-v1` on at least one authorized real corpus before
  calling it production-qualified. Add new profiles only as reviewed immutable
  code; do not restore arbitrary runtime JSON or unknown-layout fallback.
- Add a small generated or provenance-recorded CC0 PDF that runs through the
  actual converter-to-chunk path in CI instead of relying almost entirely on
  mocked conversion objects. Keep any full-size or GPU qualification on an
  explicitly authorized self-hosted/manual path with hardware, artifact, time,
  and cost identities recorded.
- For every production corpus, require owner-reviewed retrieval and grounded
  answer suites, release floors, and drift evidence after profile/model/index
  changes.

**Acceptance evidence.** Each declared production profile has one real
authorized validation receipt and synthetic regression fixtures; unknown or
mismatched layouts fail before expensive publication; diagnostics contain no
source excerpts; and release policy is corpus-specific rather than inferred
from the Property/Constitutional Law mini suites.

### R12 (P3, medium-large): improve packaging, documentation, and product UX

**Outcome.** New operators can install and use the local product without reading
a multi-thousand-line README or invoking repository-internal script paths.

**Work.**

- **Phase A, before R5:** add package metadata and stable console entry points
  for pipeline, evaluation, service, and inspection commands; publish concise
  install, security/data-flow, migration, and operator guides; and execute their
  command examples against real parsers and the locked CPU environment.
- **Phase B:** split the README into a concise quickstart/architecture index plus
  focused operator, ingestion, retrieval, evaluation, service, security/privacy,
  migration, and contributor guides. Generate or test command examples against
  the real parsers to prevent documentation drift.
- Promote model synchronization to a stable entry point with `--plan`/dry-run
  size output and task presets (PDF ingestion, default retrieval,
  classification) so first-run instructions do not silently download every
  reviewed model and hard-coded documentation sizes cannot drift.
- Audit product language around “grounded” answers. Runtime validation proves
  citation identity, citation placement, quote fidelity, and deterministic
  labeled fixtures; it is not a general semantic-entailment verifier for live
  paraphrases. State that boundary unless a separately calibrated entailment
  model/human evaluation is added.
- Delay a `src/` relocation until import-cycle work is complete; packaging and
  architecture migration need not be one change.
- Move completed point-in-time milestone narratives and test counts from this
  roadmap into a linked evidence ledger. Keep current risks, dependencies,
  status, and acceptance here, with generated inventory values where practical.
- Keep `docs/README.md` as the discovery index and complete maintained ADR
  coverage for vector lifecycle, table-row retrieval, and immutable
  source-generation policy. Historical Claude plans remain provenance, never
  substitutes for current decisions.
- Add end-to-end UI/service tests for first-run setup, empty/error states,
  cancellation/resume, accessible labels/keyboard use, result citations, and
  recovery guidance. Keep browser artifacts content-free.
- Keep the current service loopback-only. Any remote/multi-user deployment is a
  separate product milestone requiring a threat model, TLS/proxy/auth design,
  rate/tenant isolation, audit retention, and a resolved PyMuPDF distribution
  basis; it is not an incidental host-binding flag.

**Acceptance evidence.** Phase A is complete before the first version tag: a
clean environment installs from the documented locked profile and runs each
console entry point, and the short security/migration/operator guides are
checked. Phase B leaves a short navigable README and current roadmap;
representative UI flows pass on Windows and Linux; and no change weakens
loopback or private-data boundaries.

## Recommended execution sequence

| Sequence | Workstream | Dependency or gate |
|---|---|---|
| 1 | R0 private-source decision/audit and R3 corpus-owner review | Human decisions can proceed while the already implemented R0A/R0B candidates receive exact-head review preparation |
| 2 | R1 exact-head convergence including R0A/R0B | Requires R0's integration policy and both safety fixes; excludes dependency PR #30; restore workflow-scoped publication auth first |
| 3 | R2 dependency/license resolution | Split the bulk update, replay R0A/R0B transport tests, and finish exceptions before 2026-08-31 |
| 4 | R4 private-corpus ablations and expansion | Reusable semantics/CLI coverage are local; remaining evidence requires R3's frozen judgments |
| 5 | R12 Phase A, R5 first versioned release, and R6 client-workaround decision | Requires R0B, R1-R4, R2 release locks, stable entry points, and minimum release docs |
| 6 | R7 quality gates and R10 Phase A baselines | Establish both before architecture slices so correctness and performance are measurable |
| 7 | R8 dependency direction and R9 orchestration decomposition | Small, behavior-preserving PRs guarded by R7 and the Phase A baselines |
| 8 | R10 Phase B capacity, R11 corpus breadth, and R12 Phase B experience | Build on stable release contracts and approved privacy policy |

After owner review of this roadmap, create one GitHub issue per R0, R0A, R0B,
and R1-R12 item,
use P0-P3 labels plus a first-release milestone, and link each PR to exactly one
primary acceptance section. The Markdown roadmap remains the architectural
ordering document; issues become the live assignment and execution state.

## Explicit non-goals

- Do not execute the Claude plans verbatim or restore their mutable global
  profile, unknown-layout fallback, or legacy profile-evidence compatibility.
- Do not fabricate Ethics approvals, infer thresholds from the current small
  denominator, or enable table children in private release calibration before
  the owner reviews its explicit family aliases.
- Do not merge 13 dependency upgrades merely to make Dependabot green, and do
  not extend security/license exceptions without a fresh owner decision.
- Do not use a whole-project typing conversion, arbitrary coverage percentage,
  or broad `# type: ignore` inventory as a proxy for risk reduction.
- Do not perform a large `rag.py` rewrite before exact-head integration. Preserve
  public facades and land characterized, reversible slices.
- Do not expose Chroma, the current service, or the Gradio UI over a network.
  Remote deployment is outside the approved loopback/private-storage threat
  model; a public share link is not an authentication design.
- Do not treat deadline/process-tree supervision as a sandbox for untrusted
  extensions. The documented POSIX new-session escape remains outside the
  supported trusted-worker boundary unless a separate OS isolation milestone
  replaces it.
- Do not rewrite Git history, delete worktrees/scratch data, publish a release,
  or close PRs until the corresponding owner-approved milestone authorizes it.

## Completion rule

A code milestone is complete only when its behavior is implemented,
failure-injected, covered by the full test suite and static checks, exercised
against the relevant real optional client where practical, independently
reviewed, and published as a mergeable draft PR. Technical self-audits may be
durable supporting evidence, but release integration additionally requires a
submitted human review or an owner-signed manual merge record for the exact
commit. A milestone becomes integrated only after merge into `main`.

An integrated milestone is release-ready only when its applicable corpus-owner,
privacy, dependency, vulnerability, license, migration, and rollback gates also
pass. A green implementation PR alone does not satisfy those owner or lifecycle
decisions.

Rows marked “Implemented (draft)” still require review and merge; the earlier
merged milestones have reached the integrated state.
