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
A third reconciliation at local head `c008bbb` measured the then-current
cumulative delta, source/function/facade shape, workflow trigger coverage,
packaging and release gaps, and the prerequisites for any further R8/R9 work.
A fourth gate-focused audit on 2026-07-25 reconciled the live GitHub topology,
the accepted schema-v3 architecture/facade inventory at `62cb574`, CI security
ownership, the Phase A0a benchmark harness at `64843d1`, and every maintained
or historical
Claude-authored document under `docs/`. Point-in-time counts below are bound to
their named commit; they are not silently carried forward. The remaining
improvements are ordered and acceptance-testable.
A fifth gate execution on 2026-07-25 froze clean pre-gate source `0fe69f3`,
synchronized exact hash-locked full/test CPU environments on Windows and Linux,
and produced the first complete Phase A0b candidate. Its published frozen head
`ba9c66d` did not pass A0b: hosted execution exposed checkout-EOL lock drift,
host-dependent acceptance of `C:escape.py` in the source inventory, and CRLF
drift in the Windows architecture inventory. A sixth truth-repair cycle froze
intermediate repair `7594f8b`, then matrix qualification exposed additional
CPython 3.10-3.14 normalization defects. Final pre-gate source `fdb08d2` (tree
`d7e758c`) closes both defect groups, reproduces its architecture contract on
Windows and Linux across Python 3.10-3.14, and independently passes both local
9×5 comparisons. The replacement hosted exact-head checkpoint remains
deliberately distinct and pending.
A later R2 preparation checkpoint `537f72b` and gate-only refresh `b813aa7`
superseded that report pair. The 2026-07-25 release-defect corrections and the
2026-07-29 publication-readiness implementation then changed Python source and
invalidated the earlier reports in turn. Current clean pre-gate source
`e904fa6` (tree `4fe1f45`) records 2,563 functions and 607 compact runtime
callables; fresh locked Windows/Linux candidates bind that source and each
independently passes its complete same-platform 9×5 comparison. The following
reports-and-documentation-only commit freezes the new local candidate. Hosted
exact-head evidence and review remain pending.
On 2026-07-29, the publication-readiness branch added exact source-token and
reading-order fidelity, occurrence-bound heading lineage, strict Markdown
validation, one atomic five-gate READY receipt, and receipt-bound NotebookLM,
ChatGPT, and Claude derivatives. The implementation tree passes 3,081 tests
with 7 platform/optional skips, every local static gate, all three portable
retrieval baselines, the real pinned Zettlr profile, and a five-test real
Chroma/Qdrant smoke. A content-free local rehearsal passed all five gates and
produced a validated multi-document NotebookLM package; no private output is
tracked. Paired local Phase A0 evidence now passes on Windows and Linux; hosted
exact-head validation and independent review remain pending.
On 2026-08-01, GitHub Actions billing was restored after the 2026-07-25 outage
that had blocked every hosted job, and the R1 cumulative candidate merged to
`main` through history-preserving
[#44](https://github.com/toddlar00/rag-pipeline/pull/44) at its all-green
exact head `ed2995e`; the merged tree is byte-identical to the validated tree
and PRs #31-#43 closed as integrated. The R2 convergence branch then merged
the strict LLM/TOC output-contract line (#68-#71) into the
publication-readiness line (#73, which contains #66), composing the one
`rag.py` conflict so both lines' behavior is preserved, reconciling the
chunk-parameter version pin to the extracted policy constant, and refreshing
the architecture inventory to 2,597 functions. At those reconciliation
commits the merged tree passed 3,375 tests with 7 platform/optional skips,
every fast static gate, and all three offline retrieval suites; the complete
battery accompanies the frozen pre-gate checkpoint. Dependabot #72 closed in
favor of a policy-compliant `tools/refresh_locks.py` pass (successor #74 is
to be closed the same way), and a two-lane CI split (draft fast lane; full
matrix for `main`, manual dispatch, and `*-candidate` heads) is prepared on
`agent/ci-two-lane`. Fresh paired Phase A0 evidence at the merged checkpoint,
hosted exact-head validation, and owner review remain pending. The
remediation decisions are recorded in
`docs/superpowers/specs/2026-07-31-pr-remediation-design.md`.

Status terms:

- **Baseline** — present in the initial private-repository snapshot on `main`.
- **Implemented (draft)** — coded, validated, pushed, and opened as a draft PR,
  but not yet merged into `main`.
- **Integration candidate (draft)** — all implementation histories are combined
  in one draft PR, pending exact-head validation, review, and merge.
- **Integrated** — present on `main` through the cumulative integration PR.
- **Implemented locally** — coded, committed, and validated in the local
  repository, but not yet pushed or merged. Any independent technical audit is
  named separately and does not substitute for submitted GitHub/human review.
- **In progress** — active branch; not yet published as a PR.
- **Owner review pending** — implementation is complete in a draft PR, but a
  release decision requires an explicitly identified human owner.
- **Planned** — scoped direction, not yet implemented.

## Current status

| Milestone | Status | Durable result |
|---|---|---|
| Foundation | Baseline | End-to-end PDF ingestion, enriched chunking, shared LLM runtime, grounded answers, hybrid retrieval/reranking, evaluation harness, Chroma/Qdrant indexing, CLI, UI, docs, and tests |
| Repository truth and exhaustive source gate | Integrated via #44; owner decision pending | Commit `88fd301` relocates and tests the two local launchers, ignores `tmp/` and `.worktrees/`, replaces the stale compile list with deterministic Git-index discovery, curates the Claude plans and developer guide, adds the process-supervision ADR, and records the unresolved private-source policy as an explicit owner decision. The implementation merged to `main` via #44 on 2026-08-01; the replacement source-gate checkpoint `e904fa6` is carried forward by the R2 convergence candidate |
| CI security ownership and workflow invariants | Integrated via #44 | Commit `17bdff7` adds a machine-readable map of current, reserved, and governance owners; a general CI gate proves symmetric security-workflow coverage, full-SHA action pinning, checkout credential isolation, and repository-wide read-only permissions. The implementation merged to `main` via #44 on 2026-08-01; content-aware secret scanning remains a separate R7 item |
| Deterministic architecture and `rag` facade inventory | Integrated via #44; replacement refresh in the R2 candidate | Schema-v3 was introduced at `62cb574`; the published `ba9c66d` inventory records the tracked-source graph, definition and signature hashes, paired contextual import provenance, production/test facade consumers, private reads, mutation seams, and an isolated runtime contract. Historical source `fdb08d2` records 1,981 functions and reproduced across Windows/Linux CPython 3.10-3.14; current clean source `e904fa6` records 2,563 functions and 607 compact runtime callables. Broader R7 coverage, typing, lint, and static security work remain open |
| Phase A0 architecture benchmark | A0a implemented; A0b regeneration at the R2 merged checkpoint pending | Commit `64843d1` introduced nine contained fresh-process scenarios run five times; portability closure `77a0f70` redirects Linux's standard-library user base into the run-local temporary root without setting `HOME` or `CODEX_HOME`. The first hosted frozen head `ba9c66d` exposed checkout-EOL lock and inventory drift plus POSIX acceptance of the adversarial drive-relative path `C:escape.py`; it did not pass A0b. Repairs `7594f8b` and `fdb08d2` close the hosted and CPython 3.10-3.14 normalization defects. After interim R2 refresh `b813aa7`, source-changing work required clean checkpoint `e904fa6`, whose paired baselines each passed an independent matching local comparison. Merging the strict output-contract line into the R2 convergence candidate changes Python source again and invalidates both that pair and the contract line's own interim pair; fresh paired CPython 3.12 x86-64 baselines must bind the merged clean pre-gate checkpoint and are frozen by its following reports/docs-only commit. Successful hosted evidence remains strict and retained for 30 days. A0b still requires both hosted jobs to pass at that exact published commit/tree before it can gate R8 |
| Five-gate logical publication and AI project exports | Integration candidate (draft) | Schema-v7 chunk receipts and schema-v12 quality evidence bind source-oracle registries, exact lexical ownership and physical order, occurrence-bound heading lineage, strict Pandoc/Zettlr-valid Markdown, and schema-v9 physical vector parity into one atomic READY receipt. Receipt-bound NotebookLM, ChatGPT, and Claude packages preserve page locators and endnotes while removing hidden comments and package-local links. The implementation passes 3,081 tests with 7 skips, every local static gate, all three offline retrieval suites, real Zettlr validation, real Chroma/Qdrant smoke, a content-free local rehearsal, and paired local Phase A0 comparisons. Combined with the strict output-contract line in the R2 convergence candidate; exact-head CI and owner review remain required |
| Dependency compatibility domains | Integration candidate (draft) | [PR #66](https://github.com/toddlar00/rag-pipeline/pull/66): a reviewed `dependency-compatibility-domains.json` map assigns every governed direct dependency to one compatibility domain; the dependency-policy gate enforces one domain per pull request, lockfile currency against the base, and a change-free policy introduction, with a dedicated hosted dependency-compatibility workflow. Combined into the R2 convergence candidate |
| Strict LLM and TOC output contracts | Integration candidate (draft) | [PRs #68-#71](https://github.com/toddlar00/rag-pipeline/pull/71): exact classification, TOC hierarchy, TOC layout, and Boolean TOC page-verification output contracts with bounded JSON prompt-evidence framing and contract provenance in chunk parameters. Combined into the R2 convergence candidate |
| Ethics corpus coherence and publication quality | Integrated | [PR #31](https://github.com/toddlar00/rag-pipeline/pull/31): canonical scaffold reconstruction, exact source identity, complete tables and nested footnotes, exact embedding budgets, regenerated exports, and an exactly reconciled 1,715-record Chroma index |
| Machine-readable corpus quality attestation | Integrated | [PR #31](https://github.com/toddlar00/rag-pipeline/pull/31): schema-v1 report binds the exact Docling source, chunks bytes, parameters, source-lineage coverage, tables, normalization, classification, entities, token budgets, and stable/hash roots; resume, export, retrieval, and index publication fail closed on missing, stale, malformed, or mismatched evidence |
| Synced-folder publication and read resilience | Integrated | [PR #31](https://github.com/toddlar00/rag-pipeline/pull/31): bounded Windows sharing-violation retries republish only a pinned staging file; marker and exact artifact reads retry only content-identical ctime churn while failing closed on content-generation changes; exact hashes bypass unsafe stat caching on Windows |
| Immutable source-generation provenance | Integrated | [PR #34](https://github.com/toddlar00/rag-pipeline/pull/34): conversion, preprocessing, Docling, table recovery, chunking, and schema-v2 quality reports bind one exact PDF/Docling generation; multi-output leases prevent interleaved publishers; marker-owned private scratch is self-cleaning and dry-run prunable after hard termination. A disposable 912-page Ethics run produced 1,715 records, six bound recovered-table chunks, and a warning-free PASS report |
| Configurable document-structure profiles | Integrated | [PR #35](https://github.com/toddlar00/rag-pipeline/pull/35): an immutable reviewed registry now drives front/back matter, primary divisions, TOC hierarchy, canonical titles, cross-references, classification, quality checks, and exports. Schema-v3 chunk receipts attest the exact profile revision and digest; unknown, mismatched, legacy, and tampered profile evidence fails closed |
| Context-aware retrieval assembly | Integrated | [PR #36](https://github.com/toddlar00/rag-pipeline/pull/36): stable published-order linkage, exact index-generation binding, source/chapter/filter isolation, duplicate-text alias provenance, bounded neighboring evidence, and independent citations are wired through Chroma, Qdrant, CLI, UI, grounded answers, and evaluation. A disposable 1,715-record Ethics index passed a real context query |
| Table-specific retrieval | Integrated | [PR #37](https://github.com/toddlar00/rag-pipeline/pull/37): optional caption/header-propagated row children, source-wide continued-table eligibility, deterministic repeated-fragment identity, exact parent/child and source-shape attestation, family-aware result collapse, independent citations, and canonical-consumer isolation. A disposable Ethics run produced 69 children and returned the exact requested demographic row first |
| Table-family evaluation correctness | Integrated via #44 | Commits `e6c91c7` and `b1c7dd1` add explicit owner-selected child aliases, a sealed corpus-bound attestation API, exact-once logical-qrel scoring, exact/accepted-child match provenance, grounding isolation, packet-size preflight, schema-v6 reports/baselines, and a 10-record/6-query CC0 CLI suite with hard-negative upper and lower gates. The implementation merged to `main` via #44 on 2026-08-01; actual Ethics table/context ablations and owner approval remain outstanding |
| Public UI and cloud-endpoint safety | Integrated via #44 | The R0A candidate removes the Gradio share path, binds `127.0.0.1` explicitly, centralizes versioned endpoint attestation before credential/cache/transport access, rejects redirects and ambiguous targets, isolates loopback proxies, prevents ambient `.netrc` credential replacement, and validates job persistence. It passes the full 1,678-test tree and an independent exploit-oriented audit and merged to `main` via #44 on 2026-08-01 |
| Residual local/cloud trust boundary | Integrated via #44 | R0B adds versioned release-security policy v1 across CLI/Python/UI/service/evaluation/workers/jobs: trusted-single-user UI opt-in, local-only egress, release inline-secret rejection and cache-off default, opaque custom-gateway tenancy, cache-only model loading with explicit verified sync, pinned provider transports, bounded streamed JSON for every Requests-owned provider path, policy-controlled proxy/CA trust, disabled auxiliary telemetry, and strict provenance. The implementation merged to `main` via #44 on 2026-08-01 with the owner merge decision as its review gate |
| Least-privilege egress and adversarial enrichment | Planned | R0C narrows broad `allow-cloud` consent by provider/feature/data class/corpus, makes existing optional LLM budgets release requirements, adds transfer/cost preflight, hardens ingestion-side LLM schemas, and records or isolates the three pinned remote-code approvals |
| Offline model-sync planning and task presets | Integrated via #44 | A shared R10/R12 slice adds one lock-derived schema-v1 plan used by preflight and execution, presets for PDF ingestion/default retrieval/optional classification, explicit all-consumer selection, exact cache-aware byte and peak-space accounting, free-space refusal before transport/publication, full auxiliary/transform bundle identities, and explicit unsafe-pickle reporting. The exact slice passes 1,839 tests with 7 platform skips; 72 focused tests and a separate adversarial audit cover the boundary. It merged to `main` via #44 on 2026-08-01 but does not complete either milestone |
| Evaluation and runtime dependency boundaries | Integrated via #44 | R8a/R8b extract strict evaluation inputs and the query/judgment domain; R8c-1 through R8c-4 invert durable supervision, service-host/search, job coordination, and CLI/UI job use; R8c-5 separates the structural HTTP adapter and supplies a lazy one-generation outer root for the production service role. The manager shell has zero production Python-import consumers while remaining the detached child, and the tracked graph is acyclic. Direct tests cover bindings, atomic snapshots, import isolation/order, error and `BaseException` cleanup, exact composition/coordination/search wiring, facade continuity, and cross-process exclusion. The work merged to `main` via #44 on 2026-08-01; R7 and R8 remain open for broader static gates and pipeline-facade/root convergence |
| Ethics retrieval calibration | Integrated via #44; owner calibration pending | [PR #43](https://github.com/toddlar00/rag-pipeline/pull/43): a current-schema clean-room rebuild preserved all 20 unique IDs behind 24 judgments in the exact 1,715-record corpus. A private owner packet now combines those judgments with 195 unique top-10 candidates across four modes, while a content-free receipt and strict four-mode release-policy contract make promotion explicit and review-bound. Actual owner decisions and final thresholds remain outstanding |
| Process supervision extraction | Integrated | [PR #33](https://github.com/toddlar00/rag-pipeline/pull/33): deadline supervision, Windows/POSIX containment, startup gates, termination confirmation, and generic entrypoint policy moved to stdlib-only `process_supervision.py`; `rag.py` retains late-bound compatibility wrappers |
| Runtime-supervision binding | Integrated via #44 | R8c-1 freezes the production entrypoint, supervisor, cleanup exception, and timeout policy in `runtime_supervision.py`; durable jobs snapshot that capability per operation instead of using `rag.py` as a service locator. R8c-2 reuses that policy in the service host, R8c-3 moves the engine to `job_coordination.py`, and R8c-5 later supplies the service-role root. Broader pipeline/UI convergence remains open |
| Service-host binding and isolated search | Integrated via #44 | R8c-2 freezes the worker path, supervisor, cleanup type, singleton-lease factory, and remote-model predicate; `service_runtime.py` no longer imports `rag`, while `service_search_worker.py` is the sole explicit child composition shell. `resource_lease.py` preserves the exact shared `.rag-locks` identity and `embedding_policy.py` prevents host/provider classification drift. Exact search arguments, query-free argv, redacted envelopes, cleanup fatality, binding snapshots, startup rollback, import isolation, real Qdrant parity, and cross-process crash release are directly tested. The exact checkpoint passed 1,998 tests with 7 platform skips plus Ruff, 135-source compilation, dependency/model-artifact policy, and diff gates; R8c-5 later supplies the service-role root, while integration and pipeline/UI convergence remain open |
| Service job-coordination binding | Integrated via #44 | R8c-3 makes `job_coordination.py` the durable engine, keeps object-identical supported aliases plus the executable CLI in `job_manager.py`, and snapshots launch, reconcile, and corruption policy as one frozen service capability. Exact active-lease and `fail_queued` calls, default replacement, launcher precedence, ready-handshake child routing, error mapping, launch rollback, pickle/type compatibility, restart recovery, foreign-job isolation, and fresh-process manager-shell isolation are directly tested. The exact checkpoint passed 2,038 tests with 7 platform skips plus Ruff, 138-source compilation, dependency/model-artifact policy, and diff gates; R8c-5 later supplies the service-role root, while integration and broader convergence remain open |
| Job-application binding and shell isolation | Integrated via #44 | R8c-4 freezes CLI/UI store construction, launch, single/all-job reconciliation, and manager-error classification in `job_application.py`. RAG resolves one generation per command; UI actions reuse one generation through nested refresh while retaining current-root/fresh-store behavior and shared-mode isolation. Exact status/cancel/resume order, timeout forwarding, `BaseException` rollback/precedence, redaction, facade identity, import order, and zero production manager-shell Python-import consumers are tested. Detached launch intentionally still executes the stable `job_manager.py` child. The exact checkpoint passed 2,063 tests with 7 platform skips plus Ruff, 140-source compilation, dependency/model-artifact policy, and diff gates; R8c-5 later supplies the service-role root, while integration and pipeline/UI convergence remain open |
| Service HTTP boundary and lazy application composition | Integrated via #44 | R8c-5 moves FastAPI, authentication, routes, lifecycle, and OpenAPI ownership into `service_http.py`, which imports only `service_contracts.py` first-party and consumes a structural runtime port plus frozen HTTP policy. Dependency-light `application_composition.py` captures runtime, host, job, and HTTP capabilities once for the service executable; `service_api.py` retains exact public aliases, credential pickle identity, token/CLI behavior, embedded `create_app`, and flat-script operation. The exact tree passes 2,116 tests with 7 platform skips and 637 dependency warnings plus Ruff, 144-source compilation, dependency/model-artifact policy, diff gates, live Uvicorn, and independent adversarial review. This is a service-role root, not a universal root; integration and pipeline/UI convergence remain open |
| Vector-index lifecycle extraction | Integrated | [PR #38](https://github.com/toddlar00/rag-pipeline/pull/38): a standard-library-only policy layer owns deterministic reconciliation, dirty-marker ownership, mutation epochs, exact-ID verification, callback-reentry exclusion, legacy-hash repair, and close/manifest/marker commit ordering |
| Cumulative release migration rehearsal | Integrated | [PR #39](https://github.com/toddlar00/rag-pipeline/pull/39): actual Chroma and Qdrant probes recreate the integrated schema-5 manifest, rebuild one exact collection to schema 8, preserve and query a sibling collection, verify the no-op path, and require immediate lock release |
| Evidence-grounded answer evaluation | Integrated | [PR #40](https://github.com/toddlar00/rag-pipeline/pull/40): corpus-pinned claim judgments, exact citation-entailment and unsupported-claim metrics, abstention and prompt-envelope fixtures, fail-closed named release gates, runtime source-identity hardening, and schema-v5 redacted reports |
| Durable operational attempt evidence | Integrated | [PR #41](https://github.com/toddlar00/rag-pipeline/pull/41): strict redacted attempt reports bind lifecycle timing and terminal outcomes; successful cleanup is receipted before state publication; corrupt evidence, clock skew, cancellation races, failed final writes, and resume boundaries converge conservatively and idempotently |
| Queue pressure and durable recovery drills | Integrated | [PR #42](https://github.com/toddlar00/rag-pipeline/pull/42): schema-v2 typed run aggregates, exact committed/attempted vector mutation and queue-pressure metrics, supervised hard-kill recovery, and synced-publication fault injection produce strict private redacted evidence |
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

## 2026-07-25 evidence refresh

The forward plan below is based on the following repository state. These facts
are a snapshot, not release claims:

- `main` is `54cdb00`. Draft PR
  [#44](https://github.com/toddlar00/rag-pipeline/pull/44) published the first
  cumulative frozen head `ba9c66d` on `agent/r1-cumulative-gate`, 66 commits and
  177 changed files ahead of `main`. Its hosted checks are diagnostic evidence,
  not a passing release gate. The current clean pre-gate source checkpoint is
  `c1bc042c862c42964e6084967f57987944e29f6a` (tree
  `4a37989c32d2a6743ccdef47bfe20460d165af32`), 73 commits and 181 changed files
  ahead of `main`, with 102,182 insertions and 6,205 deletions. The large
  insertion count includes the canonical architecture inventory and should not
  be mistaken for equivalent executable-code growth. This aggregation includes
  the R0/R0A/R0B, R4, R7, R8, R10, and R12 slices after PR #43. Replacement
  reports and evidence documentation form the final local gate-only candidate;
  R1 must publish and record its exact commit/tree rather than treat `ba9c66d`
  or the pre-gate source checkpoint as the passing candidate.
- PRs #31 and #33-#43 form one dependency stack. PR #39 is cumulative only
  through PR #38; PRs #40-#43 are later stacked work. PR #32 is a standalone
  process-supervision design document, and PR #30 is an independent Dependabot
  update whose dependency-compatibility check is failing. Neither belongs to
  the current implementation stack.
- The cumulative PR #44 and the focused implementation stack remain open
  drafts. They have durable self-audit evidence, but none has a submitted
  GitHub review.
  The repository's current GitHub plan does not permit protected-branch rules
  for this private repository, so required review and exact-head checks are not
  mechanically enforced.
- The pre-R0A statement-coverage probe reported 75% over application and tool
  code when tests were omitted. That number is directional: branch coverage is
  disabled and separately launched workers are not automatically combined. CI
  does not currently measure coverage. The `c008bbb` R8c-5 tree passed 2,116
  tests with 7 platform skips and emitted 637 dependency deprecation warnings
  primarily from
  FastAPI/Starlette's `asyncio.iscoroutinefunction` compatibility path. This is
  historical SHA-bound evidence. The `ba9c66d` hosted attempt then found three
  truth defects: checkout-EOL lock drift in both A0 cells, host-native source
  validation that accepted `C:escape.py` on Linux, and CRLF architecture-
  inventory drift in the Windows full unit cell. Intermediate source `7594f8b`
  repairs that hosted defect set. Local Python 3.10-3.14 qualification then
  exposed AST, lazy-`sysconfig`, inherited-`Path.home`, public-`Path` identity,
  `typing.Any`, implicit-optional, and nested-forward-reference drift. Earlier
  source `fdb08d2` closes those cases; its locked suites passed 2,233 tests with
  7 skips on Windows and 2,236 tests with 4 skips on native Linux, and its
  architecture contract reproduced across all ten OS/version cells. R2 source
  `537f72b` and gate-only refresh `b813aa7` followed. Current source `c1bc042`
  passes the exact locked Windows suite with 2,287 tests, 7 skips, and the known
  Starlette/httpx warning. Both newly regenerated exact full-profile A0
  comparisons independently pass locally. This is replacement local evidence,
  not a successful hosted replacement checkpoint.
- Schema-v3 was introduced at `62cb574`; the replacement checkpoint preserves
  an inventory of 150 tracked Python files: 53 production,
  81 test, 14 tool, and two script files. The non-test architecture surface has
  69 modules, 62,441 physical lines, 1,995 functions (1,314 top-level), 189
  classes, and 145 first-party edges. `rag.py` owns 16,386 lines, 426 functions
  (296 top-level), 17 classes, and 27 direct first-party dependencies. The
  largest already-measured risks remain
  `rag.main` (1,106 lines), `_chunk_document_locked` (876),
  `quality_core.validate_quality_report` (441),
  `quality_core.build_quality_report` (416), `interactive_menu` (366), and the
  evaluator/service OpenAPI orchestration. The first-party graph is acyclic.
- Forty-one test modules consume `rag`; they contain 919 reads of 223 distinct
  private names. The inventory records 143 top-level and 22 nested literal
  monkeypatch seams plus six dynamic patch sites, direct assignment/deletion,
  re-exported facades, import-star behavior, signatures, type hints,
  module/type/pickle identity, reload, and namespace mutation/restoration. That
  is a large compatibility surface, now a machine-checked prerequisite rather
  than an estimate.
- CI is broad across Linux, Windows, Python 3.10-3.14, the full CPU environment,
  the loopback service, both local vector clients, dependency resolution, SBOM,
  vulnerability, license, model-artifact, and offline-evaluation checks. Local
  commits replace PR #43's stale compile list, gate the architecture inventory,
  and validate machine-readable security-workflow ownership plus pinned-action,
  checkout-credential, and permission invariants. The first cumulative hosted
  run supplied the failure diagnosis above. Its A0 evidence is not passing
  evidence. Replacement Phase A0b per-OS baseline candidates and independent
  local same-platform comparisons now exist. Failed comparisons publish only a
  validated content-free candidate for 7-day retention; successful cells still
  require strict two-file attestations retained for 30 days. The repaired PR
  jobs check the exact PR head. Replacement hosted comparisons and retained
  successful reports remain pending.
- Five policy records across four supply-chain exception families expire on
  2026-08-31: the Chroma vulnerability exception; separate normalized Torch and
  Torchvision audit skips; the PyMuPDF license exception; and FlagEmbedding's
  missing wheel-license metadata allowance.
- The GitHub repository is private. Sixteen R0-R12 tracking issues (#45-#60),
  P0-P3 labels, and the `first-release` milestone now exist. PR #30 is open and
  non-draft; PRs #31-#44 are open drafts, and the implementation stack still has
  no submitted review. The cumulative branch is publishable, but its remote
  `ba9c66d` head is superseded for gate purposes by current local source
  `c1bc042`. This
  is a pending replacement-publication and review condition, not permission to
  weaken the exact-head or owner gates.
- At the audited PR #43 head, `_run_civpro.py` and `_resume_civpro.py` remained
  in the root; `tmp/` and `.worktrees/` were not ignored; and `CLAUDE.md` plus
  `docs/` were untracked. The current R0 branch corrects those mechanical and
  documentation-state defects. The current roughly 2,900-line, 152 KB
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
| [#44](https://github.com/toddlar00/rag-pipeline/pull/44) | Open cumulative draft; remote head `ba9c66d` ran hosted checks and exposed A0/source/inventory checkout defects | Preserve the hosted diagnosis, but do not treat this superseded head as passing A0b evidence |
| Current pre-gate source `c1bc042` | Preserves the platform-neutral source validation, LF and lock-blob invariants, safe A0 diagnostics/artifacts, exact-PR-head checkout, and cross-version runtime normalization from `7594f8b`/`fdb08d2`; adds the reviewed release-defect corrections and a refreshed 1,995-function/378-callable inventory. The exact locked Windows suite and both independent local A0 comparisons pass | Publish the reports/docs-only child, record its exact commit/tree pair on #44, and require both replacement hosted A0 cells plus broader checks |

No open project PR has a submitted GitHub review. “Mergeable” and self-audit
comments are not approval, and green checks on different stacked heads do not
compose into evidence for an untested cumulative tree.

### Program gate matrix

The top inventory records individual delivered capabilities. This matrix keeps
the five dimensions that determine whether a roadmap milestone is actually
done separate; “code exists” does not imply “integrated” or “owner approved.”

| Milestone | Implementation | Validation/evidence | Integration | Owner/reviewer gate | Next action |
|---|---|---|---|---|---|
| R0 repository/privacy truth | Mechanical work integrated via #44 | Its checkpoint passed 1,507 tests; the cumulative hosted validation passed at exact head `ed2995e` and #44 merged on 2026-08-01. The private-source owner decision remains pending | Merged #44 | Private-source data classes/history/PR surfaces require owner decision | Record decision and audit tree/GitHub/artifacts (#32 closed as superseded by the merged ADR) |
| R0A endpoint/exposure | Integrated via #44 from `db029ce` | 1,678-test checkpoint plus later full-tree evidence | Merged #44 | Exact replacement-head security review absent | Preserved through the merged R1 head; revisit in R2 review |
| R0B release security | Integrated via #44 | 1,806-test checkpoint; dedicated hostile transport/policy coverage plus later full-tree evidence | Merged #44 | Exact replacement-head security review absent | R1 merged; replay review in the R2 candidate |
| R0C least-privilege egress/enrichment | Planned follow-up | Existing budgets and byte-pinned model code provide substrate; provider/data consent, strict ingestion schemas, preflight, and model-code confinement evidence do not exist | None | Owner must define supported cloud/model-code tier | Complete before advertising those paths as release-qualified, or mark them experimental/unsupported in R5 |
| R1 convergence | Merged to `main` on 2026-08-01 | #44 merged history-preserving at its all-green exact head `ed2995e` (merge commit `2d8e4f9`, tree byte-identical to the validated tree); all three post-merge `main` workflows passed. The remote `ba9c66d` attempt remains a diagnosed historical failure | Merged #44 | Owner merge decision recorded 2026-08-01 | Complete; hosted A0b continues at the R2 convergence candidate's frozen head |
| R2 dependencies/licenses | PR #30 is an unsuitable bulk proposal | Universal-lock check fails; five policy records across four exception families expire 2026-08-31 | Separate open PR, excluded from R1 | PyMuPDF/repository/license decisions need owner | Split by compatibility domain, relock, replay transports |
| R3 Ethics calibration | Review/receipt machinery in #43 | Content-free receipt path is tested; private decisions absent | Draft PR #43 | Corpus owner must decide judgments/abstention/thresholds | Complete owner review without agent-fabricated approval |
| R4 table/context evaluation | Reusable exact semantics and CC0 CLI suite integrated via #44 | Portable baseline/gates pass | Merged #44 | Private aliases/ablations require R3 owner labels | Run 0/1/2-context and table on/off four-mode study |
| R5 release contract | Planned; migration rehearsal exists in #39 | No tag/release/manifest/rollback execution | None | Version, distribution, privacy, cloud/model-code tier, and release approval needed | Start only after R0-R4/R2 gates, R0C disposition, and R12 Phase A |
| R6 vector-client debt | Windows Qdrant workaround exists in #39 stack | Reproducer and real-client evidence exist for pinned version | Draft stack | No policy decision unless workaround persists | Retest after R2; remove or isolate/version-gate |
| R7 quality gates | Exhaustive compile/Ruff, security-workflow ownership, and schema-v3 architecture/facade gates merged via #44; the R2 convergence candidate refreshes the inventory to 2,597 functions. Branch coverage, typing, expanded lint, and static secret/security scans do not exist | The canonical inventory covers all tracked sources, paired import provenance, spans/arity, definitions, production/test consumers, private reads, mutation seams, and isolated runtime behavior. The earlier `fdb08d2` contract reproduced across Windows/Linux CPython 3.10-3.14; the current inventory passes its deterministic gate and an independent no-blocker audit but has not repeated that ten-cell matrix. Point coverage remains non-gating and misses subprocesses | Merged #44 plus the R2 candidate refresh | Baseline changes require a named reason/reviewer; maintainer still selects coverage/type/lint ratchets | Publish and preserve the accepted inventory; next add branch/subprocess coverage, typed leaves, staged lint, content-aware secret scanning, and changed-safety-code ratchets |
| R8 dependency direction | Evaluation inversion, durable supervision binding, service-host/search inversion, service job coordination, CLI/UI job-application inversion, HTTP implementation extraction, and a service-role outer root merged via #44 | Exact-function extraction, facade/type/pickle compatibility, import order/isolation, atomic bindings/root construction, cleanup/failure propagation, exact job/search/HTTP wiring, shell isolation, shared lock identity, live Uvicorn, and cross-process tests cover an acyclic first-party graph | Merged #44 | No owner decision | Carry the accepted R7 facade inventory through A0b and final R1 convergence; then move pipeline implementation ownership behind the stable `rag.py` facade, migrating the search child, UI/CLI, and evaluator in separate slices without absorbing intentional child shells |
| R9 orchestration decomposition | First policy leaves extracted; hot spots remain | Failure-injection suite provides characterization base | None | No owner decision | Wait for broader R7 and R10 Phase A1, then slice main/chunk/eval/OpenAPI |
| R10 performance/capacity | Offline cache-aware model-sync planning, the nine-scenario Phase A0a harness, and repaired local A0b replacement candidates are implemented; runtime telemetry and queue metrics exist | A0a's containment, deterministic contracts, negative controls, real completion validators, lock contention, and Linux user-base isolation pass focused review. Replacement clean-source Windows/Linux full-profile baselines bind `c1bc042`, share eight exact inputs and the full 9×5 contract, and each independently passes its local same-platform comparison. The first hosted attempt failed on diagnosed gate/checkout defects, the replacement hosted matrix is pending, and no later capacity budget exists | Merged #44 carried the `fdb08d2`-lineage baselines; the R2 convergence candidate regenerates the pair at its merged checkpoint | A0b needs both retained hosted reports and exact-head review; authorized corpus/hardware/cost scope is still required for Phase B | Publish the replacement gate-only head, require both hosted A0b jobs before R8c-6, then implement the separately gated A1a/A1b/A1c groups before corresponding R9 slices |
| R11 corpus/profile breadth | Second profile and synthetic fixtures exist | No authorized real receipt for `roman-parts-book-v1` | None | Corpus authorization/qualification required | Add content-free profile diagnostics and real receipt |
| R12 packaging/docs/UX | Task-oriented model-sync presets, offline plan output, and plan-based first-run guidance merged via #44; packaging/README split is not done | Planner behavior is tested; documentation examples are not yet parser-executed in CI | Merged #44 | Product language and remote-scope decisions remain | Add stable console entry points and short parser-checked release guides before R5 |

## 2026-07-25 defect remediation pass

An adversarial source audit ran eight domain-scoped finders across the tracked
first-party sources, then had an independent skeptic attempt to refute each
finding against the real execution path. Twenty-four raw findings yielded nine
confirmed defects and five traced refutations; two further defects were found
outside that audit. The refutations are recorded below so they are not
re-reported.

Every correction is a release-blocking or correctness-preserving source fix
that satisfies the change freeze's explicit exception. Each carries a
regression test that was observed failing against the pre-fix source and
passing after it. The set was validated together against the full suite, Ruff,
the 150-source compilation gate, the dependency, model-artifact, CI-security,
and architecture-inventory gates, and all three offline evaluation suites.

Because the pass changes Python source, clean checkpoint `c1bc042` invalidates
the preceding A0 reports. The exact locked Windows suite passes 2,287 tests
with 7 skips and the one known Starlette/httpx warning. Strictly synchronized
189-package Windows and 187-package Linux CPU environments produced paired
clean-source baselines with the same eight exact inputs and complete 9×5
contract; each passed a second independent same-platform comparison. The
following reports/docs-only commit is the required local gate candidate.
Hosted comparisons, retained hosted evidence, and exact-head review remain
pending and are not implied by these local results.

| Defect | Surface | Correction |
|---|---|---|
| `rag storage --delete-run` re-derived a stem from the manifest's already-stemmed `job_scope`, so a run whose scope contains a dot took a different lease than the pipeline holding it and deleted a live run directory mid-write | `rag.py` | `_pipeline_job_lock` takes an explicit `scope_name`; both sides now derive one lease identity |
| A chunk containing U+0085, U+2028, or U+2029 was written literally by `ensure_ascii=False` and then torn by `str.splitlines`, so the pipeline produced a chunks artifact it could not read back | `storage_policy.py`, `artifact_io.py`, `eval.py`, `evaluation_review.py`, `offline_retrieval.py`, `run_telemetry.py` | One `jsonl_lines` reader beside the writer recognizes only the terminators `atomic_write_private` can emit |
| `_dedup_nearby_lines` deleted repeated Markdown table rows and an adjacent table's header/separator, silently losing cells and merging one table into another | `chunking_core.py` | Table rows are exempt from the duplicate test; prose page-furniture removal is unchanged |
| `preprocess --force` had no input/output aliasing check, so an aliased `-o` replaced the private source PDF with its own stripped output | `rag.py` | Publication is refused by the existing `validate_distinct_output_paths` contract before any analysis |
| Plaintext-export `char_start`/`char_end`, documented as a citation contract, indexed an in-memory string while text-mode publication translated `\n` to `\r\n` on Windows | `rag.py` | The `.txt` is published as exact UTF-8 bytes through `_atomic_write_exact_utf8` |
| `SSLKEYLOGFILE` was absent from the ambient-network override list; unlike every other entry it is read directly by urllib3/httpx when building an SSL context, so `trust_env = False` did not disable TLS session-key export of private corpus text and credentials | `release_security.py` | The variable fails closed on policy alone unless `--trust-environment-network` is passed |
| Any non-`ServiceRuntimeError` from the search path marked the service permanently unhealthy, so one transient filesystem or busy-lease failure took it down until restart | `service_runtime.py` | `OSError`/`StoragePolicyError` map to a retryable `service_unavailable`; unclassified exceptions still fail closed |
| The unauthenticated `/health/ready` probe ran its blocking filesystem walk on the asyncio event loop | `service_http.py` | The probe is offloaded with `asyncio.to_thread`, matching every other handler |
| A corrupt cancel marker aborted `run_job` after the terminal transition had committed, losing the terminal attempt report and the manager result | `job_coordination.py` | The tolerant recovery-evidence reader is used; the damaged marker is left for reconciliation, which owns the repair fields a non-recovery report may not carry |
| `_load_index_manifest` omitted `UnicodeError`, so a torn manifest crashed indexing instead of triggering the documented safe rebuild | `index_state.py` | The handler matches its sibling reader in the same module |
| A small but deeply nested cache record raised `RecursionError` out of `_read_cache`, making every later `execute()` fail | `llm_runtime.py` | The record is counted as `cache_corrupt` and missed, as for every other corruption class |

**Refuted with a traced path** (do not re-report): structural page ranges do
not leak for boundary-straddling chunks, because items are split per source
item before enrichment; `--max-regression` is a one-directional drop detector
by specification and lower-is-better rates are gated by `--fail-over`;
`classify_runtime_consumer`'s `.bin` test is a reporting classifier, not the
load-time safety control; and `tools/check_licenses.py` returning zero
violations on an empty report is not reachable as a release gate.

**Deferred defects** — real, but each changes published artifact content or
needs evidence this pass did not produce:

- `enrich_chunk` publishes `table_rows` counted from non-separator lines, so it
  includes the header and any preamble and disagrees with the authoritative
  `table_retrieval_core` parse used for parents. Confirmed. Fixing it changes
  published chunk metadata and therefore every corpus digest, so it needs a
  schema/migration decision rather than an in-freeze correction.
- The Docling progress log handler and `tqdm` bar are not released when a
  conversion fails, so later batch items inherit them. Reported, not
  independently verified.
- `validate_model_artifact_lock` raises a bare `KeyError` instead of
  `ModelArtifactError` when the lock omits an optional field the policy
  declares. Reported, not independently verified.

**Migration note.** The chunking correction changes future chunking output only
for corpora that actually contain repeated table rows or adjacent tables.
Existing artifacts and their recorded hashes are untouched, but a corpus must
be re-chunked to gain the recovered cells, and its chunks SHA-256 will change
when it is.

## Architecture and risk map

The proposed tree already contains dozens of application/tool modules and test
modules. Size is not itself a defect, but it makes the remaining concentration
and dependency risks measurable. R7 now generates the exact source, line,
definition, edge, and facade inventory; the remaining work is to preserve that
gate and add coverage, typing, lint, and security ratchets rather than return to
hand-maintained counts.

| Surface | Current strength | Material remaining risk | Roadmap owner |
|---|---|---|---|
| Ingestion, source identity, and quality | PRs #31, #34, and #35 bind exact source generations, immutable structure profiles, recovered tables, lineage, and fail-closed quality receipts | The second profile is synthetic-only; `_chunk_document_locked` remains roughly 875 lines and `_prepare_source_preserving_chunks` roughly 410 | R9, R11 |
| Retrieval and vector publication | PRs #36-#38 add bounded context, table rows, family collapse, and one guarded lifecycle for both stores | Production table/context benefit is not owner-calibrated; Windows local Qdrant still needs private-client detection plus forced garbage collection | R3, R4, R6 |
| Evaluation and release | PRs #40/#43 plus local R4 bind judgments, grounding, review receipts, release modes, table policies, and portable baselines; local R8a/R8b provide strict input and query-domain boundaries with an acyclic evaluator/review/release graph | Owner approval and private four-mode ablations remain absent; the legacy general-query parser remains intentionally permissive and needs a separate policy decision before any strictification | R3, R4, R8 |
| LLM execution | Integrated budgets, single-flight caching, adapter extraction, artifact locks, and transport accounting; local R0A/R0B adds endpoint validation, local-only consent, cache/tenant isolation, pinned provider transports, explicit environment trust, cache-only models, and release-safe secret/cache defaults | Exact-head review/CI is absent; `allow-cloud` is not provider/feature/data scoped; ingestion enrichment has permissive schemas; three pinned model bundles execute reviewed remote Python without process confinement; R2 upgrades can change transport behavior; application code cannot enforce OS DNS/firewall | R0C, R1, R2, R5 |
| Jobs, service, and recovery | Integrated durable jobs/service plus PRs #41/#42 provide containment, terminal evidence, cancellation, recovery, queue metrics, and real fault drills; local R8c-1 through R8c-4 invert supervision, host/search, coordination, and application job use; R8c-5 separates HTTP ownership and gives the production service one lazy outer root while retaining compatible facades and intentional children | `rag.py` still combines pipeline implementation and CLI ownership, so UI/pipeline composition remains split; POSIX descendants can deliberately escape the process group with `setsid()`, so worker extensions remain trusted code rather than sandboxed plugins | R5, R8 |
| UI and exposure boundary | Local Search, Export, Info, and Jobs use bounded workers/private storage; local R0A removes public sharing and R0B refuses startup without explicit trusted-single-user acknowledgement | Shared-host or remote UI remains unsupported; current local Markdown interpolates source-controlled text and some paths return raw exception detail; browser-origin, CSRF/WebSocket, sanitization, and accessibility behavior are not end-to-end qualified | R1, R12 |
| Supply chain | Universal hash locks, model byte locks, SBOM/ML-BOM, scheduled advisory/license checks, and real-client profiles are unusually strong | PR #30 is an unreviewable 13-package jump with stale locks; five policy records across four exception families expire 2026-08-31; Python 3.14 emits 637 dependency warnings in the R8c-5 tree | R2 |
| Static quality and architecture | Extracted leaves, direct failure injection, exhaustive source compilation, a wide OS/Python matrix, machine-owned security triggers, and a cross-OS schema-v3 architecture/facade inventory reduce regression risk | No branch/subprocess coverage ratchet or type checker; Ruff enables only `E4`, `E7`, `E9`, and `F`; no pinned content-aware secret/static-security scan exists; hosted exact-head evidence is absent; `rag.main` remains too large | R7-R9 |
| Release and governance | The repository is private, PR evidence is detailed, and exact-head migration rehearsals exist | Thirteen project PRs (#31-#43) remain draft: twelve are stacked implementation PRs, while #32 is a superseded standalone design; the cumulative PR stops at #38, no review is submitted, no live issues/milestones exist, and there is no tag/release/rollback manifest | R0, R1, R5 |
| Documentation and product entry | README, `docs/README.md`, developer guide, maintained ADRs, and clearly historical Claude plans expose most operator/design knowledge | The README exceeds 2,900 lines; point-in-time evidence bloats this roadmap; vector lifecycle/table retrieval/source generation still lack ADRs; private-source policy remains unresolved | R0, R12 |

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
| Dissolve runtime dependency seams | Evaluation release/review use `evaluation_inputs.py`, evaluator/review use `evaluation_queries.py`, R8c-1 through R8c-4 invert durable supervision, service hosting/search, coordination, and CLI/UI job use. R8c-5 moves the structural FastAPI adapter to `service_http.py` and introduces the lazy service-role root while retaining `service_api.py`. Detached launches still execute the stable manager shell despite its zero production Python-import consumers. The first-party graph is acyclic; only `rag.py`/UI pipeline composition remains split | Retain the enforced acyclic baseline; separate pipeline implementation from the `rag.py` facade in characterized slices, then widen the existing service-role root |

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
R0C is a post-convergence qualification gate for supported cloud/model-code
paths, not permission to enlarge R1. R4-R6 plus R12 Phase A make the first
versioned release credible. R7-R10 reduce change risk and operating cost after
the branch stack has converged. R11 and R12 Phase B expand corpus confidence
and product scope only after the same safety boundaries are reusable.

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
- **Integrated via #44:** add `Implemented/Superseded` status banners to both
  Claude plans and convert durable architectural rationale into short ADRs. Do
  not retain executable unchecked task lists as the active backlog.
- **Remaining PR lifecycle:** reconcile PR #32 with PR #33: bring forward only
  the current ADR, repair the
  dangling link, then close/supersede the standalone design PR.
- **Integrated via #44:** update `CLAUDE.md` to match the current policy,
  quality, retrieval, evaluation, and operational module graph.
- **Integrated via #44:** move `_run_civpro.py` and `_resume_civpro.py` under
  `scripts/`, correct their root discovery, and ignore `tmp/` plus `.worktrees/`
  without deleting either directory.
- **Integrated via #44:** replace CI's hand-maintained source compile list with
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

**Progress (2026-07-24; merged to `main` via #44 on 2026-08-01).** Implemented
from `agent/release-security-policy`:

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

### R0C (P1, before cloud/model-code qualification): make egress consent and LLM enrichment least-privilege

**Outcome.** A cloud-enabled or remote-model-code release grants narrowly
scoped authority, forecasts and caps transfer/cost, treats source and model
output as hostile data, and states where executable model code remains trusted.
The first release may defer this only by declaring the affected cloud and
remote-code features experimental/unsupported and recording the owner decision
in its compatibility and threat-model manifests.

**Why R0B is not the endpoint.** R0B correctly gates every cloud path before
credential lookup and provides optional hard caps for provider calls,
transport attempts, and reserved tokens. Its schema-v1 `allow-cloud` value is
nevertheless run-wide: it does not bind consent to a provider, fallback,
feature, data class, or corpus. Exact model byte pins prevent drift, but the
three approved `trust_remote_code` bundles still execute Python with ordinary
process privileges. Grounded-answer parsing is adversarially constrained while
classification, TOC/scaffold enrichment, and generated retrieval context use
more permissive output acceptance.

**Work.**

- Define release-security policy v2 with explicit provider and fallback
  allowlists, operation/feature, data class, and opaque corpus/run identity.
  Query-only consent must not authorize corpus embedding or per-chunk
  enrichment, and approval for one provider must not authorize an ambient
  credential for another fallback.
- Require finite release-mode values for the existing call, transport-attempt,
  and reserved-token budgets; add transmitted/received byte ceilings and an
  optional caller-priced cost ceiling. Before the first cloud dispatch, emit a
  content-free preflight with expected records, requests, payload bytes,
  reserved tokens, destinations, fallbacks, and cost, then reconcile actual use
  within a documented variance.
- Treat every document block as untrusted evidence. Make `_llm_classify`
  accept one exact normalized label rather than a substring. Replace
  first/last-brace extraction in TOC layout, scaffold creation/verification,
  and related enrichment with unique-field, exact-type, depth/size/range-bounded
  schemas that reject trailing prose, duplicate fields, controls, non-finite
  numbers, and unexpected keys. Bound generated context and prevent model text
  from entering ordinary logs or content-free reports.
- Prefer supported native architectures or reviewed local adapters that remove
  `trust_remote_code`. Where unavoidable, enumerate every executed code file
  and digest in the release manifest and evaluate a scrubbed, network-denied,
  least-privilege child process with a bounded input channel. Make clear that
  application `local-only` policy alone does not sandbox that Python.
- Maintain a reviewed, dated provider privacy/retention and model-code register
  outside executable defaults. Keep generated briefs/questions/context labeled
  generated and unverified unless a separate approved evaluation supports a
  stronger claim.

**Acceptance evidence.** Provider/feature/data-class cross-product tests prove
that consent cannot widen through fallback; missing or exceeded budgets fail
before transport; receipts remain source- and secret-free; preflight and actual
usage reconcile; prompt-injection fixtures cannot select a class by substring,
forge hierarchy/citations, inject markup/log lines, or bypass strict schemas;
malformed output fails closed or uses the documented deterministic fallback;
and every remaining model-code execution has a digest, named review, scrubbed
environment, and a network-canary result.

### R1 (P0, medium): converge PRs #31-#43 and all validated local slices into one reviewed exact-head change

**Outcome.** `main` contains one reproducible tree rather than a long-lived
stack whose middle cumulative PR stops before the current head.

**Work.**

- Establish and record a clean **pre-gate source checkpoint** after every
  intended Python source and dependency/model-lock change. Record its full
  commit SHA (`git rev-parse HEAD`) and tree SHA (`git rev-parse HEAD^{tree}`).
  That checkpoint must already contain the reviewed workflow, LF policy, and
  executable gates used by generation. Generate and independently compare the
  per-OS A0b baselines from it. The following gate-only evidence commit may add
  only the two reviewed baselines and their provenance/status documentation; it
  may not change Python source, a workflow, attribute policy, or any lock. Then
  record a separate **final R1 candidate** commit/tree pair and use only that
  final pair for workflows, review, migration rehearsal, and merge. Any later
  source or lock change invalidates both A0b baselines and requires a new
  pre-gate checkpoint; any other change requires a new final candidate pair.
  Preserve the pre-gate source commit in `main` history: a squash or
  history-rewriting rebase makes the ancestor-bound source-delta proof fail and
  requires baseline regeneration from the replacement history.
- The cumulative branch `agent/r1-cumulative-gate` and PR #44 merged to
  `main` on 2026-08-01 without rewriting the focused review histories. The
  process included every
  local
  slice after `e2196a1`, not merely the earlier R0/R4 subset. Do not merge the
  stacked PRs one by one and assume their earlier checks compose. Put the
  already-started R7 architecture gate and R10 Phase A0 benchmark work in a
  separately reviewable, gate-only slice on that cumulative branch: it may make
  evidence truthful and reproducible, but must not introduce product features
  or silently change release behavior. Its manifest must prove that the final
  candidate differs from the pre-gate source checkpoint only in the two
  baseline reports and declared provenance/status documentation paths.
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
  the current roughly 105,000-changed-line cumulative diff does not receive only
  nominal approval. Freeze the candidate before review; any later code change
  invalidates the exact-head sign-offs and affected workflow conclusions.
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
from the reviewed stack head; the manifest's source commit SHA and tree SHA
match a freshly fetched checkout and remain unchanged through review and merge;
the gate-only slice has its own compact diff, reviewer, and passing checks;
every required check passes on that exact commit and tree;
Chroma and Qdrant each rebuild the target generation, preserve a sibling
collection, serve search, complete a no-op rerun, and release filesystem locks;
an independent review and manual/protected merge gate are durable; and a clean
clone of `main` reproduces all dependency-light tests.

### R2 (P0, medium-large, deadline 2026-08-31): resolve dependency and license debt

**Outcome.** No release relies on an expired exception or an untested bulk
dependency update.

**Work.**

- **R2a — decision and preparation, start now without changing the frozen
  dependency tree:** open one tracked item per expiring exception and assign a
  decision owner, technical owner, evidence link, replacement or renewal path,
  and latest decision date. Reproduce the current Chroma, Torch/Torchvision,
  PyMuPDF, and FlagEmbedding conditions; complete the repository-license and
  PyMuPDF distribution decisions; and prepare provider-contract and warning
  inventories. During the immediate freeze this work is read-only or
  documentation-only: non-gate test/code changes wait until the final R1 pair
  is frozen unless they expose a release-blocking defect that satisfies the
  freeze's explicit exception. It must not mix dependency upgrades into R1.
- **R2b — compatibility updates, start from the frozen R1 commit/tree:** replace
  or rebase PR #30 only after R1 is frozen. It proposes 13 direct upgrades,
  including
  major API lines for OpenAI, Cohere, Google GenAI, Sentence Transformers, and
  PDF/vector dependencies, but currently fails the universal-lock consistency
  check and does not update generated locks. Because the local tree has since
  removed the unused Voyage/OpenAI/Cohere SDK declarations, prefer superseding
  #30 over mechanically rebasing its obsolete dependency surface.
- Replace the single all-package Dependabot group with reviewable compatibility
  domains: PDF/Docling, vector stores, ML/runtime, service/UI, provider
  transport, and test/audit tooling. Regenerate every universal lock for one
  domain at a time,
  and test each provider adapter with deterministic fake transports plus one
  explicitly authorized live smoke where credentials and cost policy permit.
- **Integrated via #44:** recompute the direct dependency surface before
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
- **Integrated via #44 for the seven Requests-owned paths:** bound responses
  before JSON materialization using both a conservative `Content-Length` check
  and decoded streamed-byte ceiling, while retaining strict shape/numeric
  validation. The matrix covers absent, malformed, ambiguous, and dishonest
  lengths without logging bodies. Prove an equivalent limit in google-genai or
  replace Gemini with the owned transport before closing the all-provider item.
- Treat the current Python 3.14 warning inventory as migration evidence rather
  than harmless noise. Upgrade or constrain FastAPI/Starlette/websockets and
  the SWIG-backed clients so supported versions have an owned, budgeted warning
  baseline before Python 3.16 removes the deprecated asyncio APIs.
- **Integrated via #44:** workflow selection follows a machine-readable map of
  current, reserved, and governance owners. It covers release, endpoint,
  provider/LLM, model-supply, vector/runtime, and reserved
  `pipeline_runtime.py` paths; a general CI checker enforces symmetric security
  triggers, full-SHA action pinning, `persist-credentials: false`, and exactly
  read-only repository permissions.
- Add a pinned, content-aware repository secret scan with an explicit reviewed
  baseline and test-fixture policy. Bring its action, configuration, baseline,
  and owners under the same policy, and ensure scan output cannot reproduce
  matched private/source values.
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
- Treat **2026-08-15** as the escalation checkpoint, not 2026-08-31 as the first
  decision date. By August 15 every exception must have passing replacement
  evidence or a submitted owner decision. Any item still unresolved then is
  escalated to the release owner with an explicit choice to remove/disable the
  affected capability, hold the release, or approve a narrowly scoped renewal;
  silence never extends an exception. No exception may remain expired on or
  after August 31.

**Acceptance evidence.** R2a has named owners, reproducible evidence, and a
dated disposition for every exception without changing R1's dependency tree.
R2b starts from the recorded R1 commit/tree; lock regeneration produces no diff
on a second run;
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
- Freeze one immutable label generation before inspecting calibration results:
  query text identities, positive/negative/abstention labels, accepted table
  children, filters, and ambiguity decisions must be bound to the receipt.
  Changing any label after seeing a run creates a new labeled-set digest and
  requires the complete R4 matrix to be rerun; thresholds may never be tuned
  against a silently changing judgment set.
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
frozen label generation has an owner, revision, digest, and change record; the
receipt validates against the exact query and corpus digests; all four modes
have explicit owner-approved floors and immutable configuration bindings; the
release command fails closed on any missing/mismatched approval; and reports
contain only policy-approved metadata. This milestone is blocked on human
review by design, not on additional code generation.

### R4 (P1, medium): make evaluation semantics match context and table retrieval

**Outcome.** New retrieval features can be calibrated without false misses,
inflated relevance, or answer-quality regressions.

**Work.**

- **Integrated via #44:** add a versioned judgment-family schema so an
  attested row child may satisfy a judged parent exactly once. Parent, matching
  child, sibling row, unrelated table, duplicate family, and filtered-family
  cases must be explicit.
- **Integrated via #44:** bind table-child generation policy, family
  semantics, context window, context budget, and collapse behavior into
  baselines and release policies.
- After R3 freezes the labels, run one explicit factorial calibration matrix:
  **4 retrieval modes × 3 context windows (0/1/2) × 2 table-child states
  (disabled/enabled) = 24 cells**. Every cell must use the same corpus
  generation, model locks, index generation, query/judgment digest, filters,
  deterministic seeds, and scoring code. Compare primary ranking, grounded
  claim/citation entailment, unsupported-claim rate, abstention, prompt size,
  latency, returned-evidence size, and parent/child displacement. Publish all 24
  content-free results and predeclare aggregation and selection rules rather
  than reporting only favorable cells.
- **Integrated via #44:** add a portable CC0 table-family mini suite and CLI
  baseline with real generated children, sibling/unrelated hard negatives, and
  two-sided slice gates so an incorrectly higher score also fails.
- **Integrated via #44:** keep retrieval relevance aliases independent from
  grounding `entailed_by` evidence, add a regression case for that boundary,
  and expose an explicit `exact` versus `accepted_table_child` match kind in
  detailed reports instead of requiring reviewers to infer it from two IDs.
- **Integrated via #44:** preflight the serialized owner-review packet against
  its size ceiling, and replace the former direct-Python
  `table_family_members` dictionary seam so non-CLI callers cannot mistake
  caller-supplied membership for corpus-derived attestation.
- Expand the approved set with chapter-balanced paraphrases, hard negatives,
  filters, tables, cross-page continuations, numeric/multi-hop cases, ambiguity,
  and abstention. Keep raw private evidence out of committed reports.

**Acceptance evidence.** Deterministic unit tests cover every family case; one
gold judgment cannot receive multiple credit; four-mode baselines reject schema
or configuration drift; all 24 labeled matrix cells are present exactly once,
share the frozen R3 identities, and are reproducible; grounded-answer
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

- Choose the first release version after R0B, R1-R4, R2, and an explicit R0C
  cloud/model-code support-tier disposition; add a changelog and release notes,
  create one product-version source, replace the two hard-coded service `1.0.0`
  values, expose a CLI version, and tag the exact commit. Attach no private
  corpus artifacts to a GitHub release.
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
- Make platform claims tiered and testable rather than treating every CI cell as
  equally supported:
  - **Tier 1, release-blocking:** the locked CPU profile on named Windows x86-64
    and Linux x86-64 versions, with the exact CPython minor(s), vector backends,
    installer, CLI/service flows, migration, and rollback combinations listed
    in the release manifest.
  - **Tier 2, compatibility-checked:** additional CPython minors or backend
    combinations that run scheduled CI but may have documented exclusions. A
    failure removes that combination's claim until fixed; a skip is never
    evidence of support.
  - **Tier 3, experimental/unsupported:** CUDA/GPU, macOS or other unqualified
    platforms, remote/multi-user use, and corpus profiles without the R11
    qualification. These are opt-in and cannot inherit Tier-1 guarantees.
- Turn the existing cumulative migration rehearsal into a documented preflight
  and rollback runbook. Define backup, failure, retry, dirty-marker, sibling
  collection, and downgrade expectations. Set explicit RPO/RTO targets and add
  a clean-environment restore drill covering chunks, receipts, job/service
  state, and both vector backends; corrupt/incomplete backups must fail closed
  without damaging the active generation.
- Define content-free operator thresholds and escalation for queue saturation,
  orphaned jobs, dirty markers, storage/cache growth, repeated provider
  failures, and failed recovery drills. Document credential rotation and a
  privacy/security incident path instead of relying on test-only fault drills.
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
  Add `SECURITY.md`, a repository license/proprietary notice, supported-version
  and vulnerability-reporting policy, token/credential rotation guidance, and
  content-free incident/recovery checklists.

**Acceptance evidence.** A disposable environment installs from locked inputs,
migrates both backends from the prior generation, verifies search/no-op/sibling
preservation, restores a known generation within the stated RPO/RTO, rejects a
corrupt backup without changing the current generation, exercises the rollback
runbook, and reproduces the release
manifest. The Git tag and release notes identify the exact reviewed commit and
known limitations; CLI/service version values agree; compatibility tests pin
public exit codes and supported schema migrations; and release-profile tests
reject every unpinned-model bypass. CPU/CUDA and structure-profile support tiers
are explicit; every declared Tier-1 cell passes without unexplained skips,
Tier-2 failures remove only the affected claim, and Tier-3 limitations are
prominent; every supported install/entry-point example is executable, and
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
- Run the Windows reproducer as **10 consecutive fresh-process repetitions** for
  each supported Qdrant-client/CPython combination. Cap each repetition at 180
  seconds and the complete compatibility job at 30 minutes; on timeout,
  terminate the contained process tree, retain only redacted handle/phase
  diagnostics, and fail the gate. Record repetition number, client/runtime/OS
  identity, phase, cleanup result, and elapsed time so “passes repeatedly” is
  falsifiable and does not permit an unbounded CI hang.

**Acceptance evidence.** All 10 bounded fresh-process repetitions pass on every
supported Windows cell with immediate deletion and no surviving child; the
30-minute job deadline is itself tested; no remote or non-Windows client
receives the workaround; the adapter is version-gated and documented if
retained; and removal is covered by
the same lock-release regression tests.

### R7 (P1, medium): add risk-based coverage, typing, lint, and architecture gates

**Outcome.** CI detects untested or boundary-breaking changes without demanding
a disruptive whole-repository rewrite.

**Work.**

- Commit Coverage.py configuration with branch measurement and subprocess data
  combination where supported. First publish a trustworthy baseline, then add a
  no-regression ratchet for safety-critical leaf modules and changed lines.
  Retain schema-versioned statement/branch reports plus warning and duration
  summaries so the baseline is comparable instead of existing only in console
  output. Hosted-runner timing is initially diagnostic, not a hard budget.
- Prioritize uncovered behavior in `supervised_worker.py`, process supervision,
  `rag.py` orchestration, the UI, inspection/scaffold CLIs, artifact review, and
  dependency/security tooling. Distinguish genuinely uncovered code from child
  processes that were not traced by the initial probe.
- Introduce Pyright or mypy on stable stdlib-only leaves first:
  begin with `service_contracts`, `embedding_policy`, `endpoint_policy`,
  `job_coordination_contracts`, `operation_contracts`, and `index_state`, then
  expand into `release_security`, `cli_policy`, `document_profiles`,
  `vector_lifecycle`, evaluation contracts, and the runtime bindings. Add
  security-critical `job_runtime` only after its imported policy/storage layer
  is clean. Expand by dependency layer, not by blanket ignores.
- **Integrated via #44; normalization established at `fdb08d2`, current
  2,563-function/607-callable inventory refreshed at `e904fa6`:** the
  schema-v3 tracked-source architecture/facade
  inventory subsumes the earlier AST import-DAG gate. It requires the
  first-party graph to remain acyclic, pins both shared evaluation domains and
  runtime/application bindings, and records tracked-source counts,
  function/class signatures and spans, paired import context/provenance,
  private facade reads, re-exported aliases, direct/nested/dynamic patch seams,
  assignment/deletion, namespace/import-star behavior, type hints,
  module/type/pickle identity, and reload/restoration behavior. The isolated
  runtime probe is supervised and output-bounded. The `fdb08d2` snapshot
  reproduced byte-identically across Windows and Linux CPython 3.10-3.14; the
  current `e904fa6` snapshot passes its deterministic local gate but has not
  repeated that ten-cell matrix. An independent AST graph must exactly match
  every static edge.
- Preserve the architecture/facade baseline as canonical, reviewable JSON with
  repository-relative paths and stable ordering. A failed check must print a
  bounded semantic summary—section counts plus added/removed/changed modules,
  edges, signatures, facade reads, and mutation seams—rather than an opaque
  whole-file or one-line replacement; publish the full machine-readable diff as
  a CI artifact when the bounded summary truncates it. Baseline updates require
  a named reason and reviewer, not an unconditional regeneration step.
- Continue generating the same structural baseline from fresh Windows and Linux
  checkouts at every frozen candidate and compare normalized sections
  byte-for-byte.
  Runtime- or platform-specific diagnostics must live in explicitly named,
  non-gating fields unless both Tier-1 platforms have their own reviewed
  baseline. This cross-OS check must run before the inventory can guard R8.
- Stage additional Ruff rules after zero-warning baseline cleanup. Add
  property/state-machine tests for strict JSON schemas, ownership markers,
  publication recovery, pagination, and policy parsing; do not use a high
  percentage target as a substitute for failure-injection quality.
- **Integrated via #44 for the seven Requests-owned paths:** adversarial tests
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
- Generate or validate CI path-filter ownership for security-critical files.
  Moving a provider, model, or vector client must update the owning audit
  workflow and any fixed-file advisory invariant in the same change.

**Acceptance evidence.** CI reports branch and statement coverage with a
documented subprocess caveat; changed safety-critical code cannot lower its
ratchet; the initial typed leaf set passes with no broad suppression; forbidden
import edges fail a focused test; the architecture inventory reproduces the
documented snapshot and the complete facade baseline on Windows and Linux, and
drift produces a compact semantic diff; security-workflow triggers
follow implementation ownership; the provider size/MIME matrix fails closed
without body retention; and the full Python/OS matrix remains green.

**Delivered draft slice.** The import/evaluation checks, complete minimum R8
architecture/facade inventory, compact semantic drift reporting, cross-OS
reproduction, and CI security-ownership gate merged to `main` via #44;
the final compatibility refresh remains local until the replacement push. The
schema-v3 edge representation was independently challenged and corrected so
context/origin provenance cannot collapse into the same projection; the final
audit found no material blocker. Coverage and subprocess combination, typing,
staged lint expansion, content-aware secret/static-security scanning, hosted
exact-head evidence, and the other acceptance clauses remain open. This
minimum inventory does not close those broader R7 tracks or mark R7 complete.

### R8 (P2, large in small slices): invert dependencies and centralize composition

**Outcome.** Runtime composition points inward through narrow protocols while
`rag.py` remains a compatible public facade and evaluation review becomes a
one-way application over shared contracts rather than a reciprocal import.

**Work.**

- **Integrated via #44 (R8a):** extract `_hex_digest`,
  `_strict_json_bytes`, `_read_snapshot`, and `_corpus_contract` into
  dependency-light `evaluation_inputs.py`. `evaluation_release.py` now imports
  that leaf directly; `evaluation_review.py` preserves object-identical aliases
  at the former names. Strict parsing additionally rejects depth and numeric
  overflow, while malformed corpus fields now produce controlled contract
  errors. Snapshot, corpus, canonical-byte, schema, and CLI contracts remain
  otherwise unchanged. The maintained decision is recorded in
  `docs/architecture/decisions/evaluation-input-contract.md`.
- Characterize job submission, startup gating, cancellation, exact-worker
  termination, recovery, reindex, search, service request, and Windows cleanup
  behavior before moving imports.
- Define narrow runtime protocols for pipeline execution, search/index
  operations, storage/retention, and telemetry. Inject implementations into job
  and service layers instead of importing the `rag` module as a service locator.
- **Integrated via #44 (R8b):** move the exact 13-function query schema,
  corpus-pin/snapshot, judged-ID/table-family, and grounding-evidence closure
  plus five constants into `evaluation_queries.py`. `eval.py` preserves
  object-identical private aliases and its legacy loader/digest cache; review
  calls the domain directly. Characterization fixes legacy bytes, types,
  errors, physical line numbers, success-only digest publication, query
  non-mutation, strict-parser separation, and all three corpus-policy levels.
- **Integrated via #44 (R8c-1):** bind the stable worker entrypoint, concrete
  supervisor, cleanup exception, and immutable timeout policy in
  `runtime_supervision.py`. The engine now owned by `job_coordination.py`
  resolves one binding per run or launch and no longer imports `rag.py`;
  explicit caller overrides and the late-bound `rag.py` facade remain
  compatible. Direct tests cover binding
  validation and immutability, call-time atomic replacement, timeout/default
  precedence, cleanup classification, detached launch, import order, and
  transitive manager isolation.
- **Integrated via #44 (R8c-2):** bind the fixed private-search child,
  concrete supervisor and cleanup type, exact service-instance lease factory,
  and remote-embedding predicate in `service_runtime_binding.py`.
  `service_runtime.py` snapshots that capability and no longer imports `rag`;
  `service_search_worker.py` is the sole child composition shell that injects
  `rag.search_index`. `embedding_policy.py` is the shared classification leaf,
  while `resource_lease.py` owns the exact canonical `.rag-locks` policy and
  shared reentrant/OS lease used by both the service and legacy facade.
  Characterization covers all search modes and kwargs, policy identity,
  query-free argv, strict redacted envelopes, actual cleanup failures,
  binding replacement, startup rollback, exact sidecar continuity, real local
  Qdrant parity, isolated imports, and cross-process exclusion/crash release.
- **Integrated via #44 (R8c-3):** move detached launch, execution, and
  conservative recovery into `job_coordination.py`; retain `job_manager.py` as
  the compatible import/executable facade; and place the object-identical
  result/error contracts plus frozen service capability in
  `job_coordination_contracts.py`. `service_runtime.py` snapshots one launch,
  reconcile, and canonical corruption generation per service while preserving
  explicit launcher precedence, exact active-lease and `fail_queued`
  forwarding, the stable manager child path, direct construction, pickle/type
  identity, and every public schema. Fresh-process checks prove that importing,
  constructing, starting, and closing the service loads neither `job_manager`
  nor `rag`; real detached, restart, idempotency, failure, foreign-job, CLI, and
  UI tests preserve recovery behavior.
- **Integrated via #44 (R8c-4):** add the dependency-inward frozen
  `JobApplicationBinding` containing the job-store factory, detached launch,
  single/all-job reconciliation, and manager-error classification. `rag.py`
  snapshots one complete generation per jobs command, and each UI action uses
  one generation through nested refresh while preserving fresh stores and
  call-time root/timeout configuration. No production Python module now imports
  `job_manager.py`; its public aliases, pickle identities, CLI, exact child
  path, and direct detached behavior remain compatible. Characterization pins
  every job action, cancellation polling, resume revision order, launch
  rollback and primary-error precedence, shared-mode zero access, redaction,
  import isolation, and generation replacement.
- **Integrated via #44 (R8c-5):** move authentication, bounded HTTP handling,
  lifecycle, routes, and OpenAPI into `service_http.py` behind a structural
  `ServiceRuntimePort` and frozen runtime-error/job-page policy. That module
  imports only `service_contracts.py` first-party. Add dependency-light
  `application_composition.py`, whose lazy thread-safe singleton captures the
  concrete runtime and HTTP factories plus runtime, job, and HTTP bindings as
  one service generation. `service_api.py` remains the token/config/import/
  executable facade, with object-identical auth/OpenAPI/credential aliases,
  legacy pickle identity, embedded `create_app`, flat CLI, redaction, and lazy
  Uvicorn behavior. The combined service factory preserves direct runtime
  construction, omission-versus-`None` defaults, exact failure propagation,
  and ASGI-owned startup/cleanup.
- **Prerequisite for the next physical move:** carry the already accepted R7
  namespace/monkeypatch inventory unchanged through the clean pre-gate source
  checkpoint, pass the separate R10 Phase A0b per-OS baseline/CI checkpoint,
  then publish and freeze the final R1 candidate. Full-project typing and full
  Phase-A1 capacity work are not blockers, but another large unpublished
  architecture slice is.
- **R8c-6 spike, facade feasibility before the move:** build an isolated
  prototype against a generated miniature module and the complete R7 facade
  contract. Prove or disprove that the proposed `ModuleType` forwarding proxy
  can preserve reads, assignment, deletion/restoration, dynamically added
  names, `dir`, `vars`, import-star behavior, both import orders, reload,
  signatures/type hints, logger identity, and class/function pickle paths on
  every supported Python minor. The spike must not move production ownership.
  Record the result and unsupported semantics in the pipeline-composition ADR;
  if the proxy cannot satisfy the declared contract, choose and characterize a
  simpler alias/explicit-forwarding design before R8c-6a rather than discovering
  the incompatibility during the source move.
- **R8c-6a, preferred ownership split:** move the implementation wholesale from
  `rag.py` to `pipeline_runtime.py`, then make `rag.py` a thin import/executable
  facade backed by a `ModuleType` forwarding proxy. Keep all mutable runtime
  state in exactly one implementation namespace and initially rewire no
  consumer. This is a mechanical ownership split, not functional decomposition
  and not completion of R8 or R9.
- The R8c-6a compatibility contract must cover the generated complete namespace,
  including names exposed only because no `__all__` exists: reads, writes,
  deletes, dynamically added attributes, `dir`, `vars`, import-star behavior,
  direct and nested monkeypatch restoration, signatures, type-hint lookup, and
  public/private imports. Preserve `rag.py` as the supervised script and
  `python -m rag` target; keep the `rag` logger name, legacy function/class
  `__module__` and pickle paths, one-time environment/fork hooks, and both
  import orders. Explicitly decide and test reload behavior; direct mutation of
  `rag.__dict__` is unsupported unless a safe forwarding design is proven.
- Update ownership-sensitive policy in the same slice: the fixed-file Chroma
  advisory check must resolve the actual physical adapter owner, and the
  security workflow must trigger for `pipeline_runtime.py` and every future
  provider/vector implementation. Record traceback/source-file movement as an
  intentional compatibility boundary in a maintained pipeline-composition ADR.
- **R8c-6b, isolated consumer move:** make `service_search_worker.py` use the
  implementation owner (or an injected search capability) while retaining its
  stable physical child path and exact redacted envelope, timeout, cleanup,
  lease, Chroma, and Qdrant behavior. Land this separately from the facade move.
- **R8c-6c, role-specific composition:** define frozen structural capability
  groups for search/index inspection, export, artifact integrity, deadline
  policy, and pipeline execution. Construct them lazily in
  `application_composition.py`; migrate `ui.py` and `eval.py` in separate
  slices so service-only composition never loads the pipeline runtime and each
  operation snapshots one complete binding generation. Do not make those
  modules broad permanent consumers of the moved monolith.
- Keep intentional physical search, manager, and supervision child shells out
  of the in-process root. Enforce the target dependency graph in the R7 gate and
  delete compatibility edges only after generated consumer evidence reaches
  zero.
- Treat R8c-6a, R8c-6b, and each role migrated under R8c-6c as separate named
  review slices. Before a slice starts, its manifest must name the implementation
  owner, independent reviewer, owned modules/capabilities, exact pre-slice
  commit/tree, rollback commit or revert procedure, and rollback triggers.
  Before the next slice begins, demonstrate that rollback in a disposable
  checkout (including facade imports and physical child routing), publish the
  compact architecture/facade delta, and bind acceptance evidence to the
  post-slice commit/tree. No slice may inherit an unnamed owner or an untested
  “revert the whole stack” rollback obligation.

**Acceptance evidence.** The facade feasibility spike has a reviewed ADR
decision before production ownership moves, and every ownership/consumer slice
has a named owner, reviewer, bounded diff, tested rollback, and exact commit/tree
evidence. The already-delivered job/service/evaluation graph remains acyclic.
For R8c-6a, Git recognizes a near-total source move;
`rag -> pipeline_runtime` is one-way; the generated namespace, mutation,
import-order, executable, logger, type, pickle, supervision, and policy-owner
contracts pass; and the full R8c-5 baseline is met or exceeded. R8c-6b leaves
the service child dependent on the implementation/search capability rather than
the facade. R8c-6c leaves no production Python importer of `rag`, preserves the
facade as a supported executable/import shell, and proves that service-only
root resolution does not import the pipeline. Every slice independently passes
the supervision, failure-injection, evaluation, live-service, and real-client
regressions.

**Delivered #44 slices (merged 2026-08-01).** The evaluation boundary/architecture tests cover
strict inputs, compatibility identity, depth/numeric/type failures, all
four-module import orders, eager isolation, resolver semantics, legacy
loader/query staging, and the acyclic evaluation graph; cross-surface
  regressions cover oversized numeric inputs. Runtime-binding,
  coordination-contract, manager/facade, service-host, worker,
  embedding-policy, shared-lease, HTTP-adapter, service-root, and architecture
  tests cover the R8c-1/R8c-2/R8c-3/R8c-4/R8c-5 capabilities and isolation
  boundaries. Existing
  evaluator/review/grounding, process-supervision, and vector-concurrency suites
  continue to exercise the compatibility facades. The tracked first-party
  import graph is acyclic, and the manager shell has zero production Python
  import consumers while remaining the intentional detached child entrypoint.
  The exact R8c-5 tree passes 2,116 tests with 7 platform skips and 637
  dependency warnings; all 144 tracked Python sources compile, and Ruff,
  dependency/model-artifact policy, diff, live-Uvicorn, and independent
  adversarial-review gates pass. R8 remains open
  and unpublished for pipeline-facade extraction and wider CLI/UI composition;
  the production service role now has a true outer root.

### R9 (P2, large in behavior-preserving slices): decompose orchestration hot spots

**Outcome.** High-risk changes no longer require editing thousand-line control
flows or mirrored schema builders/validators.

**Work.** Each item below is a separately named review/rollback slice; do not
combine two merely because they touch the same monolith.

- **R9a — error-taxonomy characterization:** before extraction, define the
  stable categories for usage/input, configuration, policy refusal, integrity,
  dependency/unavailable, transient provider/backend, cancellation, deadline,
  cleanup, and unexpected internal failures. Pin how each category maps to CLI
  exit codes, service HTTP statuses/envelopes, job terminal states, retryability,
  redacted operator diagnostics, and primary-versus-cleanup error precedence.
  Refactoring may move an error's owner but may not silently change its category
  or disclose the original exception/path.
- **R9b — CLI parser and dispatch registry:** after R8c-6 establishes
  implementation ownership, extract the roughly 1,100-line pipeline-runtime
  `main` parser/dispatch into a typed command registry plus leaf handlers.
  Preserve exact help, defaults, exit codes, resume serialization, lazy imports,
  supervised child routing, facade monkeypatch seams, and the R9a mappings.
- **R9c — interactive-menu presentation:** extract the roughly 365-line
  `interactive_menu` separately from R9b. Keep input/output transcripts,
  cancellation, noninteractive behavior, and dispatch identity characterized.
- **R9d — chunk transaction stages:** split the roughly 875-line
  `_chunk_document_locked` into pure source preparation, classification, chunk
  assembly, quality validation, and publication stages coordinated by one
  transaction object. Preserve leases, provenance, fault injection, error
  precedence, and commit ordering.
- **R9e — operation configuration:** replace internal 20-30-parameter
  orchestration calls (while preserving public facade signatures) with frozen
  operation configuration and injected collaborator objects.
- **R9f — cache and lifecycle ownership:** separately move mutable embedding,
  reranker, throttle, artifact, and BM25 cache ownership to an explicit
  composition/lifecycle boundary so concurrent tests and services isolate state.
- **R9g — evaluation stages:** split `eval._main_with_args` and
  `_evaluate_impl` into validation, execution, scoring, measurement, and
  publication after R8 removes the reciprocal review import. Preserve exact
  report/baseline bytes, exit behavior, and R9a mappings.
- **R9h — declarative quality schema:** consolidate `quality_core`'s report
  builder and validator around a declarative exact-field schema. Add generated
  canonical examples plus mutation/property tests before deleting mirrored code.
- **R9i — declarative OpenAPI source:** replace the roughly 510-line hand-built
  OpenAPI function with one schema/route source that reproduces the committed
  static snapshot and cannot drift from the live service contract.
- **R9j — physical vector adapters:** extract Chroma and Qdrant adapters only
  after shared transaction, lease, dirty-marker, manifest, and workflow-trigger
  ownership are stable. Keep backend-specific failure and close semantics
  directly characterized; adapter extraction cannot weaken lifecycle gates.
- Split very large test files by behavioral contract only when doing so improves
  ownership and diagnostics; do not combine refactoring with policy changes.

**Acceptance evidence.** The R9a taxonomy is machine-tested across CLI,
service, and job surfaces before R9b begins; characterization snapshots prove
CLI/menu compatibility; each pure stage has direct tests; all failure-injection
and source-generation tests pass; report bytes/digests remain stable unless a
deliberate schema version changes; and R9a-R9j each has its own owner, diff,
acceptance record, and demonstrated rollback.

### R10 (P1/P2, medium): establish performance baselines and capacity budgets

**Outcome.** Existing telemetry becomes an operational decision tool rather
than descriptive data with no release ceiling.

**Work.**

- **Phase A0a — integrated via #44, before R8c-6:** the contained schema-v3
  harness runs nine fresh-process scenarios five times: cold `import rag`, real
  CLI help and outer-supervised `info`, service-root construction, worker import
  isolation, actual UI/service worker entrypoints, inherited isolation-guard
  enforcement, a generated completed-artifact no-op resume, and offline
  export/retrieval. It pins exact call/return contracts, output identities,
  first- and third-party import roots, source/lock identities before and after,
  complete scenario-set identity, bounded output, process-tree cleanup, and
  network/DNS/home/profile denial inherited by nested Python children. Strict
  JSON controls, source-drift tests, deliberately broken completion validators,
  wrong wiring, isolation mutations, and a real busy-then-acquired interprocess
  vector lock all fail as required. Local reports prove the harness works; they
  are not an authoritative baseline or CI gate.
- **Phase A0b — repaired local replacement assembled; hosted checkpoint still
  before R8c-6:** the first published frozen head `ba9c66d` ran the hosted
  matrix but did not satisfy A0b. Both A0 cells exposed checkout-EOL drift in
  lock inputs; Linux also showed host-native acceptance of the adversarial
  drive-relative inventory path `C:escape.py`, and the Windows full suite found
  CRLF architecture-inventory drift. Intermediate repair `7594f8b` makes
  source-path validation platform-neutral, pins canonical LF bytes, requires
  every dependency/model input to equal its `HEAD` blob, and makes PR A0 jobs
  check out the exact PR head. Python 3.10-3.14 qualification then found and
  closed interpreter-specific AST, `sysconfig`, `Path`, and typing drift in
  historical source `fdb08d2`; its 1,981-function/377-callable inventory
  reproduced across all ten supported OS/version cells. Gate-only `ed2995e`
  froze those reports, followed by R2 source `537f72b` and refresh `b813aa7`.
  Release-defect source `c1bc042c862c42964e6084967f57987944e29f6a`
  (tree `4a37989c32d2a6743ccdef47bfe20460d165af32`) then changed Python and required
  another paired refresh. Its exact locked Windows suite passes 2,287 tests
  with 7 skips, and its inventory records 1,995 functions and 378 compact
  runtime callables. Publication-readiness source
  `e904fa6ea7419dac6797dc11af7cb1e507fb456c` (tree
  `4fe1f457d6a60668319234988fb181bb989ad2fb`) changed Python again and superseded
  that refresh. Its exact locked Windows suite passes 3,081 tests with 7 skips,
  and its inventory records 2,563 functions and 607 compact runtime callables.
  Replacement Windows and Linux CPython 3.12.13 reports share eight exact
  inputs and the full nine-scenario/five-repetition contract, and both matching
  local comparisons independently pass. The gate-only delta after `e904fa6` is
  limited to those two reports and provenance/status documentation; the
  workflow, LF policy, and executable gates are already in the source
  checkpoint and were exercised during generation. The following
  reports/docs-only commit freezes the final
  local R1 candidate; publish it and
  require both replacement hosted cells before authorizing
  R8c-6. Any source/lock change regenerates both baselines. Deterministic
  operation counts, output bytes, import classes, and serialized identities
  gate; wall/RSS remain diagnostic until repeatability supports a reviewed
  budget. Local reproduction alone does not satisfy the hosted checkpoint.
- **A0 failure diagnosis — implemented in the replacement source:** the harness
  publishes a schema-validated, redacted candidate before contract comparison
  and reports only stable content-free stages/codes. Hosted failed comparisons
  retain that report for 7 days; a failure before candidate publication does not
  fabricate an artifact. Passing cells still revalidate the exact report and
  candidate identity, build the strict two-file attestation bundle, and retain
  it for 30 days.
- **Phase A1a — command and artifact construction, before the corresponding R9
  slices:** add stable small/medium CPU baselines for parser/dispatch, chunk
  preparation/publication, quality build/validation, and OpenAPI generation.
- **Phase A1b — retrieval and evaluation:** baseline cold/warm BM25 and context
  assembly, table-family collapse, vector mutation planning, evaluation
  scoring/report construction, and review-packet materialization.
- **Phase A1c — service and recovery:** baseline service construction/search,
  queue saturation, cancellation, recovery, containment, and cleanup. Use
  bounded overload rather than an unbounded soak test.
  All A1 groups use generated or CC0 inputs with no network, credentials,
  private corpus, or unreviewed model. Their purpose is to catch architectural
  regressions, not claim full production capacity; each group gates only its
  corresponding R9 slices.
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
- **Integrated via #44 (shared R10/R12 slice):** the offline, lock-derived
  schema-v1 model-sync plan reports per-bundle and aggregate uncached/required
  download bytes, verified already-present runtime bytes, additional runtime
  bytes, peak staging/destination space, margin, and free-space sufficiency.
  Execution consumes the same canonical selection and rechecks capacity before
  transport and each publication. Retain PDF-ingestion plus default-retrieval
  as the first-full-run footprint baseline and compare it with explicit
  `--all`; derive every value from the current plan rather than a prose size.
- Use a schema-versioned, content-free benchmark record naming commit, Python,
  OS, CPU/memory, dependency/model-lock identity, warm/cold state, and scenario
  inputs. Run at least five repetitions, retain median/p95/peak RSS and observed
  variability, and distinguish deterministic gates from diagnostic timing.
- Add generous regression budgets only after repeatability is demonstrated,
  normalize noisy measurements, and keep cost/network benchmarks opt-in unless
  credentials and spend ceilings are explicit. Exercise bounded overload and
  cancellation rather than only the success path.

**Acceptance evidence.** A0a's harness and tests pass. Both local A0b baseline
candidates reproduce under their matching exact full/test CPU profile, while
the separate retained hosted baseline/CI checkpoint still guards the R8
ownership move; no local smoke, partial scenario report, or local-only
comparison can substitute for that final A0b evidence. A1a, A1b, and A1c
independently guard their corresponding semantic R9 slices. All benchmarks emit redacted,
schema-validated reports; baselines name commit, hardware, runtime, and
model/lock identities;
repeated local/CI runs show documented variability; deterministic output/count
regressions always fail while wall/RSS ceilings activate only for stable
scenarios; the offline sync plan exactly reconciles to the lock and detects
insufficient space before transport; and Phase B yields operator-facing
queue/recovery capacity recommendations.

### R11 (P3, medium): qualify profiles and retrieval across authorized corpora

**Outcome.** The second structure profile and general retrieval claims are
supported by pair-specific real authorized evidence, not only synthetic
fixtures or one private casebook. Qualification attaches to an exact corpus
generation plus profile revision; it is not a transferable claim about every
document that selects the same profile.

**Work.**

- Build a content-free corpus/profile qualification matrix keyed by immutable
  generation digest, profile/revision, page count, and validation status rather
  than filenames or excerpts.
- Give every `(corpus generation, profile revision)` pair separate
  **structure-qualified**, **retrieval-qualified**, **grounded-answer-qualified**,
  and **production-qualified** states with evidence digests, owner, review date,
  supported model/index identities, and expiry/requalification triggers. A
  structure pass cannot silently promote retrieval or answer claims.
- Add a structure-only diagnostic command that reports recognized divisions,
  unmatched headings, front/back-matter decisions, and profile digest without
  emitting source text.
- Validate `roman-parts-book-v1` on at least one authorized real corpus before
  calling that exact corpus/profile pair production-qualified. A reusable
  profile-wide production claim requires two structurally distinct authorized
  corpora; with only one, label it single-corpus-qualified. Add new profiles
  only as reviewed immutable code; do not restore arbitrary runtime JSON or
  unknown-layout fallback.
- Add a small generated or provenance-recorded CC0 PDF that runs through the
  actual converter-to-chunk path in CI instead of relying almost entirely on
  mocked conversion objects. Keep any full-size or GPU qualification on an
  explicitly authorized self-hosted/manual path with hardware, artifact, time,
  and cost identities recorded.
- For every production corpus, require owner-reviewed retrieval and grounded
  answer suites, release floors, and drift evidence after profile/model/index
  changes.

**Acceptance evidence.** Each declared production corpus/profile pair has one
real authorized validation receipt and synthetic regression fixtures, and each
release claim names its exact pair and qualification state;
profile-wide claims have two structurally distinct authorized receipts or are
explicitly labeled single-corpus-qualified; unknown or mismatched layouts fail
before expensive publication; diagnostics contain no source excerpts; and
release policy is corpus-specific rather than inferred from the
Property/Constitutional Law mini suites.

### R12 (P1/P3, medium-large): improve packaging, documentation, and product UX

**Outcome.** New operators can install and use the local product without reading
a multi-thousand-line README or invoking repository-internal script paths.

**Work.**

- **Phase A, before R5:** add package metadata and stable console entry points
  for pipeline, evaluation, service, and inspection commands; publish concise
  install, security/data-flow, migration, and operator guides; and execute their
  command examples against real parsers and the locked CPU environment. Use
  PEP 621/build metadata with one dependency-authority strategy, build and
  install a wheel in a clean environment, and prove detached/supervised routing
  for the stable `rag.py`, `job_manager.py`, `service_search_worker.py`, and
  `supervised_worker.py` child roles. A `src/` move is not required.
- **Phase B:** split the README into a concise quickstart/architecture index plus
  focused operator, ingestion, retrieval, evaluation, service, security/privacy,
  migration, and contributor guides. Generate or test command examples against
  the real parsers to prevent documentation drift.
- **Integrated via #44 (shared R10/R12 slice):** the repository sync tool now
  has offline `--plan` and schema-v1 JSON output, canonical task presets for PDF
  ingestion, default retrieval, and optional classification, explicit `--all`,
  exact cache-aware byte/space preflight, and blocked-consumer reporting. The
  README now plans before synchronizing and contains no hard-coded model total.
  Promotion from `tools/sync_model_artifacts.py` to a stable installed console
  entry point and parser-executed documentation examples remain Phase A work;
  exact replacement-head review and integration are still pending.
- Audit product language around “grounded” answers. Runtime validation proves
  citation identity, citation placement, quote fidelity, and deterministic
  labeled fixtures; it is not a general semantic-entailment verifier for live
  paraphrases. State that boundary unless a separately calibrated entailment
  model/human evaluation is added.
- Delay a `src/` relocation until R8 composition-boundary work is complete;
  packaging and architecture migration need not be one change.
- Move completed point-in-time milestone narratives and test counts from this
  roadmap into a linked evidence ledger. Keep current risks, dependencies,
  status, and acceptance here, with generated inventory values where practical.
- Keep `docs/README.md` as the discovery index and complete maintained ADR
  coverage for vector lifecycle, table-row retrieval/evaluation, immutable
  source-generation policy, pipeline composition/facade semantics, and release
  platform/support tiers. Historical Claude plans remain provenance, never
  substitutes for current decisions.
- Add real-browser UI/service tests for first-run setup, empty/error states,
  cancellation/resume, accessible labels/keyboard use, result citations, and
  recovery guidance. Include hostile-origin forms/fetch/WebSockets plus
  source-controlled HTML, Markdown, links, metadata, and exception canaries;
  explicitly escape/sanitize private result text and replace raw exception/path
  rendering with fixed diagnostics plus local correlation IDs. Keep browser
  artifacts content-free.
- Keep peer-loopback enforcement non-disableable through production application
  construction; any `enforce_peer_loopback=False` seam must be internal to
  isolated tests. If Gradio does not provide a proved origin/session boundary,
  add a per-launch secret and explicit host/origin validation or retain the UI
  as a documented trusted-single-user-only tool.
- Use “logical deletion” consistently. Ordinary unlink/rmtree behavior cannot
  promise secure erasure on SSDs, Dropbox history, snapshots, or backups;
  correct the remaining runtime message and document any encrypted-volume and
  backup-retention requirement separately.
- Keep the current service loopback-only. Any remote/multi-user deployment is a
  separate product milestone requiring a threat model, TLS/proxy/auth design,
  rate/tenant isolation, audit retention, and a resolved PyMuPDF distribution
  basis; it is not an incidental host-binding flag.

**Exit checkpoints.** Track these as distinct acceptance items; prose or a
passing unit suite cannot substitute for another checkpoint.

- **ADR exit:** maintained, accepted decisions cover vector lifecycle,
  table-row retrieval/evaluation, immutable source generation, pipeline
  composition/facade semantics, and release platform/support tiers. Each names
  status, decision owner, consequences, rollback/supersession path, and related
  tests; `docs/README.md` links every current ADR and labels historical Claude
  plans as provenance.
- **Parser/documentation exit:** a machine-readable inventory extracts every
  documented command example and runs it through the real installed parser
  under the locked CPU profile. Help/default/exit-code snapshots and invalid
  examples are tested; stale flags, repository-only paths, or examples that
  require ambient credentials fail CI.
- **Wheel exit:** build the wheel from a clean source archive, inspect its file
  and metadata inventory for missing runtime files or private/generated data,
  install it without network access into a fresh environment, run `pip check`,
  execute every console entry point, and exercise every physical supervised
  child role. The installed commands must not depend on the repository working
  directory.
- **Browser exit:** run real-browser tests with content-free fixtures for first
  setup, empty/error states, search/citations, cancellation/resume, keyboard and
  label accessibility, hostile origins, WebSockets/fetch/forms, injected
  HTML/Markdown/links, and fixed redacted error/correlation output. Capture only
  content-free traces/screenshots and enforce a bounded per-test and suite
  deadline.
- **Platform exit:** run the parser, wheel, browser, service-loopback, child-role,
  migration, and rollback checkpoints on every R5 Tier-1 Windows and Linux cell
  without unexplained skips. Record Tier-2 exclusions separately; Tier-3
  experiments cannot satisfy or weaken this exit.

**Acceptance evidence.** Phase A is complete before the first version tag: a
clean environment installs from the documented locked profile and runs each
console entry point plus every physical child role, and the short
security/migration/operator guides are checked. The ADR, parser/documentation,
wheel, browser, and Tier-1 platform exits above each have a separately linked
passing record. Phase B leaves a short
navigable README and current roadmap; hostile-origin and injected-markup cases
remain inert, UI errors disclose no private paths/raw exception text,
representative flows pass on Windows and Linux, logical-deletion language is
accurate, and no change weakens loopback or private-data boundaries.

## Immediate change freeze

Effective now, do not start new product features, broad dependency upgrades, or
additional R8/R9 architecture moves. Finish only the already-started minimum R7
architecture/facade inventory and R10 Phase A0 harness/baseline work as the
reviewable R1 gate slice, plus defects required to make those gates truthful.
Owner reviews, R0/R2a decisions, issue/ADR creation, publication-auth repair,
and read-only evidence collection may proceed in parallel because they do not
enlarge product behavior. Once the gate slice is frozen, any source correction
must be release-blocking, receive an owner/reviewer, and produce a new recorded
commit/tree pair; otherwise it waits until after R1 integration. This freeze
does not declare R7 or A0 complete.

## Recommended execution sequence

| Sequence | Workstream | Dependency or gate |
|---|---|---|
| 1 | Immediate scope freeze, issue/milestone creation, R0 private-source decision/audit, and R3 corpus-owner review | Human decisions and tracking proceed while the already-started R7/A0 gate work finishes; no new feature slice starts |
| 2 | R1 exact-head convergence including R0A/R0B plus the reviewable R7/A0 gate-only slice | Requires R0's integration policy and both safety fixes; excludes dependency PR #30; publish only the reports/provenance child after `e904fa6`, record its exact commit/tree pair, then require the replacement hosted gates |
| 3 | R2a decisions/evidence now; R2b dependency compatibility after R1 freeze | Keep R2a from changing R1 dependencies, split R2b by compatibility domain, replay R0A/R0B transports, escalate unresolved exceptions on 2026-08-15, and finish before 2026-08-31 |
| 4 | R0C cloud/model-code qualification or explicit deferral | Required before the release advertises cloud-assisted ingestion or trusted remote model code as supported; policy/schema/model isolation work can proceed beside owner-led R3/R4 evidence |
| 5 | R4 private-corpus ablations and expansion | Reusable semantics/CLI coverage are local; remaining evidence requires R3's frozen judgments |
| 6 | R12 Phase A, R5 first versioned release, and R6 client-workaround decision | Requires R0B, R1-R4, R2 release locks, an R0C support-tier decision, stable entry points, and minimum release docs |
| 7 | Broader R7 and R10 Phase A1a/A1b/A1c | The minimum inventory and authoritative A0 checkpoint belong to the frozen R1 gate slice; coverage, typed leaves, lint/static-security ratchets, and separately grouped semantic-hotspot baselines then advance in reviewable slices |
| 8 | R8c-6 pipeline ownership, role-specific composition, then R9 decomposition | Move implementation ownership behind the stable `rag.py` shell first, migrate the service search child and UI/evaluator capabilities separately, preserve intentional physical children, then extract parser/menu/config/chunk/cache/backend slices only after their direct characterization and Phase-A evidence |
| 9 | R10 Phase B capacity, R11 corpus breadth, and R12 Phase B experience | Build on stable release contracts and approved privacy policy |

Create the GitHub tracking structure now; roadmap owner review may refine it but
is not a prerequisite. Open one issue per R0, R0A, R0B, R0C, and R1-R12 item,
use P0-P3 labels plus a first-release milestone, assign decision and technical
owners where they differ, and link each PR to exactly one primary acceptance
section. Add the 2026-08-15 R2 escalation checkpoint and immediate change freeze
as milestone-level tracking items. The Markdown roadmap remains the
architectural ordering document; issues become the live assignment and
execution state.

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
