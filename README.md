# Legal RAG Pipeline

GPU-accelerated pipeline for converting law school textbooks (and other dense
PDFs) into LLM-ready markdown, queryable vector indexes, case briefs, exam
questions, and Anki flashcards. Built for legal education with optional cloud
or local LLM assistance.

Handles scanned PDFs, structure-aware chunking, TOC-based hierarchy detection,
hybrid search (BM25 + vector + cross-encoder reranking), LLM classification,
contextual retrieval, optional row-level table retrieval, RAPTOR multi-level
summaries, citation graph extraction, and source-grounded answer generation
with explicit abstention.

See [`ROADMAP.md`](ROADMAP.md) for implemented hardening milestones, merge
status, and the ordered improvement backlog. The final cross-stack findings and
their disposition are recorded in
[`INTEGRATION_AUDIT.md`](INTEGRATION_AUDIT.md).

## Architecture

```
PDF
 |
 v
[preprocess] -----> Inspect scan images + text-layer quality
 |                  - Strip background scans only when text is preserved
 |                  - Keep image-only scans intact for OCR
 |
 v
[convert] --------> Docling GPU layout model --> DoclingDocument (JSON + markdown)
 |                  - OCR auto-detection (`--ocr` / `--no-ocr` override)
 |                  - Heading hierarchy detection
 |                  - Table structure detection
 |                  - Watermark removal
 |                  - Encoding normalization (UTF-8)
 |
 v
[chunk] ----------> HybridChunker (structure-aware, token-budgeted)
 |                  - TOC-based hierarchical section paths
 |                  - Content classification (regex / LLM / zero-shot)
 |                  - LLM heading reconstruction for bare markers
 |                  - Contextual retrieval prefixes (LLM)
 |                  - Quality scoring (LLM, 1-5 scale)
 |                  - Footnote separation
 |                  - Exact Docling source-item lineage
 |                  - Structural filtering (TOC, index, front matter)
 |                  - Source-aware trigram Jaccard deduplication
 |                  - Chapter detection & propagation
 |                  - Optional header-propagated table-row retrieval children
 |
 v
Enriched Chunks + Bound Quality Report
 |                  - Exact source/chunks/parameter hashes
 |                  - Lineage, table-family, structure, normalization, entity,
 |                    classification, duplicate, and token-budget checks
 |
 +---> [index] ---------> ChromaDB or Qdrant (manifest-validated incremental index)
 |
 +---> [query] ---------> Hybrid search + reranking + grounded, cited answers
 |
 +---> [export] --------> Markdown / Plain text / Anki flashcards
 |
 +---> [raptor] --------> 3-level recursive summary tree
 |
 +---> [brief] ---------> Structured case briefs (Facts/Issue/Holding/Reasoning)
 |
 +---> [generate-questions] -> Exam-style hypotheticals & doctrinal questions
 |
 +---> [extract-questions] --> Q&A pairs from Notes & Questions sections
 |
 +---> [citations] -----> Citation graph (cases, statutes, cross-refs)
```

`rag.py` remains the stable command and Python compatibility facade.
`retrieval_core.py` is its standard-library-only retrieval domain: structured
search/grounding results, stable chunk identity, legal lexical analysis,
rank fusion, metadata filters, grounded prompt construction, and citation
validation. Backend clients, mutable caches, LLM calls, and CLI orchestration
remain outside that leaf module.

`artifact_io.py` is a second standard-library-only leaf for fail-closed chunk
snapshot reads, atomic file replacement, and completion-record validation.
`rag.py` injects stable-ID, hashing, cleanup, and schema policy through its
existing compatibility functions.

`chunking_core.py` is the standard-library-only text preparation layer. It
owns deterministic normalization, structural filtering, near-duplicate
detection, rule-based content classification, and basic chunk metadata helpers;
Docling, LLM enrichment, and chunk publication remain orchestrated by `rag.py`.

`quality_core.py` is the standard-library-only corpus-attestation layer. It
builds and strictly validates a deterministic adjacent `*_chunks.quality.json`
report over the exact Docling and chunks snapshots. New source-lineaged corpora
must pass its source coverage, structure, normalization, token, table,
classification, entity, stable-ID, and chunk-hash checks before downstream
publication or use.

`table_retrieval_core.py` is the standard-library-only table retrieval policy.
It strictly parses preserved Markdown tables, derives optional one-row children
that repeat their caption and header, groups continued fragments by exact
Docling table lineage, attests every parent/child family and its dimensions,
keeps retrieval-only children out of publication consumers, and removes a
retrieved parent only when a more specific child from that same family is also
present.

`index_state.py` is the standard-library-only index policy layer. It owns
collection-scoped manifest and dirty-marker rules, incremental rebuild
decisions, and query compatibility checks; `rag.py` injects schema, logging,
atomic publication, artifact hashing, and vector-store lease collaborators.

`vector_lifecycle.py` is the standard-library-only vector mutation policy
layer. It computes deterministic add/replace/remove plans, owns dirty-marker
acquisition and revalidation, invalidates stale verification evidence after
every physical mutation, and permits manifest publication only after an exact
post-mutation identity check. `rag.py` retains vector-store locking, client
construction, embedding workers, and the physical Chroma/Qdrant adapters.

`llm_adapters.py` translates Ollama, Gemini, and OpenAI-compatible transport
responses into the typed, provider-neutral contracts in `llm_runtime.py`.
Gemini remains lazily imported, while `rag.py` retains provider selection,
runtime composition, mutable caches/throttles, and the compatibility facades.

`cli_policy.py` is the standard-library-only command policy layer for timeout
validation and scanning, resume-command serialization, provider and credential
option mapping, menu LLM detection, and secret redaction/environment routing.
`rag.py` injects live defaults and endpoint predicates while retaining argparse
definitions and dispatch, runtime mutation, pipeline execution, and
output/artifact behavior.

`process_supervision.py` is the standard-library-only deadline and containment
runtime. It owns Windows Job Objects, POSIX process groups, same-PID startup
gates, bounded termination confirmation, cancellation/deadline control, and the
generic supervised entrypoint flow. `rag.py` snapshots its timing/environment
configuration and late-binds telemetry and compatibility collaborators for
each call, preserving the established facade and monkeypatch seams.

`ingestion_core.py` is the standard-library-only PDF safety layer for text-layer
quality, page-coverage-aware background detection, complete pre-mutation
inspection, and mixed-page/shared-xref removal planning. `rag.py` and
`preprocess_pdf.py` retain lazy PyMuPDF access, paths, saving, progress, logging,
CLI reporting, OCR/Docling orchestration, and artifact publication.

`model_artifacts.py` is the standard-library-only local-model supply-chain
layer. It validates the reviewed policy and resolved lock, synchronizes only
consumer-allowlisted files at immutable commits, verifies raw SHA-256 bytes,
and atomically publishes isolated regular-file trees for runtime loaders. It
also assembles Docling's layout/TableFormer/RapidOCR tree and emits the
CycloneDX ML-BOM companion to the Python-package SBOM.

`operation_contracts.py` defines the dependency-free, committed
`IndexOutcome` returned by both vector backends. `run_telemetry.py` provides the
shared run ID, stage lifecycle, safe diagnostic, event-stream, aggregate-report,
and interrupted-worker recovery contract used by the CLI and LLM runtime.
`storage_policy.py` is the shared owner-only permission and link-aware atomic
publication layer. `retention.py` validates pipeline/UI/cache ownership and
implements dry-run-first quarantine and deletion plans.

## Quick Start

The portable command-line and CPU dependency profiles are tested on CPython
3.10 through 3.14. Clean core and core-plus-optional environments are installed
and tested on Python 3.12, with resolution checks at both ends of that range.
The RTX 50-series/CUDA 12.8 setup below intentionally requires Python 3.12-3.14.

```bash
# 1. Install PyTorch with CUDA (must come first for GPU support)
pip install "torch>=2.7,<3" --index-url https://download.pytorch.org/whl/cu128

# 2. Install the core dependencies
pip install -r requirements.txt

# 3. Full pipeline -- one command
python rag.py full --pdf Civil_procedure.pdf --force

# Select the reviewed layout policy when the book is not a U.S. law casebook
python rag.py full --pdf Scholarly_book.pdf \
  --structure-profile roman-parts-book-v1

# 4. Interactive menu (no arguments)
python rag.py
```

For a first run of `Civil_procedure.pdf`, the main artifacts are scoped to one
book directory. A temporary preprocessed PDF, when needed, is run-named beside
that directory:

```
output/
|-- .rag-jobs/                           # Private durable background-job state
|-- Civil_procedure/
|   |-- .rag-run.json                     # Retention ownership/state marker
|   |-- Civil_procedure.json             # DoclingDocument
|   |-- Civil_procedure_docling.md        # Raw Docling conversion markdown
|   |-- Civil_procedure_chunks.jsonl      # Enriched chunks
|   |-- Civil_procedure_chunks.quality.json # Exact quality attestation
|   |-- Civil_procedure.md                # Final, filtered unified export
|   |-- Civil_procedure_chroma/           # Chroma index (default backend)
|   `-- Chapters/                         # Only with --split-chapters
`-- Civil_procedure_preprocessed.pdf      # Only when preprocessing is needed
```

The raw `*_docling.md` conversion and final `.md` export are distinct files.
The default collection for this run is `civil_procedure`.

A new `full` or `batch` run never reuses an existing book directory: it creates
`Civil_procedure_2/`, then `_3/`, continuing after the highest existing run
number. The suffix is also applied to every artifact and the collection name
(for example, `civil_procedure_2`). `--force` does not change this allocation;
`--resume` reuses the latest existing run and skips its completed stages.
Runs created before ownership manifests were introduced can still resume, but
remain deliberately ineligible for automatic deletion: retention does not infer
ownership from a legacy filename or directory layout.

### Multiple textbooks

```bash
# One at a time
python rag.py full --pdf Civil_procedure.pdf
python rag.py full --pdf Torts_casebook.pdf

# Batch mode with resume (processes all, skips completed steps)
python rag.py batch Civil_procedure.pdf Torts_casebook.pdf Con_law.pdf --resume
```

## Commands

| Command | Purpose |
|---------|---------|
| `preprocess` | Safely inspect/strip background scans when a usable text layer exists |
| `convert` | PDF to DoclingDocument via GPU layout model, with automatic OCR detection |
| `chunk` | DoclingDocument to enriched chunks with metadata |
| `index` | Chunks to a manifest-validated incremental ChromaDB or Qdrant index |
| `export` | Chunks to markdown, plain text, or Anki flashcards |
| `query` | Search with auto hybrid retrieval and adaptive reranking |
| `brief` | Generate structured case briefs via LLM |
| `generate-questions` | Generate exam-style questions via LLM |
| `extract-questions` | Extract Q&A pairs from Notes & Questions sections |
| `citations` | Build citation graph (cases, statutes, cross-refs) |
| `raptor` | Build RAPTOR recursive summary tree |
| `info` | Inspect output artifacts and pipeline status |
| `full` | End-to-end: all steps in one command (with `--resume`) |
| `batch` | Process multiple PDFs end-to-end with per-PDF resume |
| `storage` | Dry-run-first retention for owned runs, caches, and UI exports |
| `jobs` | Submit, inspect, cancel, resume, and safely delete durable background work |

Global flags: `-v` / `--verbose` (DEBUG output), `--quiet` (warnings only).

### Hard operation deadlines

Commands that can open a vector store run in an isolated worker process. Use
`--operation-timeout SECONDS` to replace the positive wall-clock deadline; a
timeout terminates the Windows worker Job Object or the POSIX worker process
group, releases its operating-system lease, retains any interrupted-update
marker, prints no command arguments or secrets, and exits with status `124`.
The target waits behind an OS start gate until containment and durable worker
registration finish, and normal direct-worker exit also drains descendants.
Guided-menu index, query, info, full, and batch actions use the same boundary.
On POSIX, a descendant that deliberately starts a new session is outside the
process-group guarantee. Defaults are:

| Command | Deadline |
|---------|----------|
| `query` | 300 seconds |
| `info` | 120 seconds |
| `index` | 7,200 seconds |
| `full` | 14,400 seconds |
| `batch` | 43,200 seconds |
| `eval.py` | 14,400 seconds |

The Web UI applies the same isolated boundary to the vector-store work in each
Search and Info callback; its defaults are 300 and 120 seconds. Override them
with `--search-timeout` and `--info-timeout` when starting `ui.py`.

`--db-lock-timeout` only bounds how long a worker waits to acquire the database
lease. `--operation-timeout` bounds the whole isolated command, including a
storage call that never returns. Direct Python API calls remain in the caller's
process; applications requiring a hard cancellation boundary should invoke the
CLI or isolate those calls in their own supervised process.

### Durable background jobs

Long-running non-interactive commands can run under a detached, durable
manager. Submit options belong before `--`; everything after it is the normal
pipeline command:

```bash
# Submit and return after the detached manager records its ready handshake
python rag.py jobs submit --timeout 14400 -- \
  full --pdf Civil_procedure.pdf --split-chapters

# Redacted status surfaces never print argv, source paths, or attempt tokens
python rag.py jobs list
python rag.py jobs status JOB_ID --json

# Cancellation is bound to the current attempt and terminates its worker tree
python rag.py jobs cancel JOB_ID --wait --wait-timeout 30

# Resume is always explicit and creates a new attempt; it is never automatic
python rag.py jobs resume JOB_ID

# Deletion is terminal-only and dry-run-first
python rag.py jobs delete JOB_ID
python rag.py jobs delete JOB_ID --apply
```

The allowed commands are `preprocess`, `convert`, `chunk`, `index`,
`extract-questions`, `generate-questions`, `citations`, `raptor`, `brief`,
`export`, `full`, and `batch`. Interactive, query/status, nested `jobs`, and
destructive `storage` commands stay foreground-only. Credential flags and
manager-owned timeout, telemetry, and hidden resume-binding flags are rejected;
configure provider credentials through environment/configuration instead.
This keeps secrets out of the immutable private job spec and normal status
responses, but environment credentials are still available to the worker
process under the current user account.

Each job uses an immutable digest-bound `spec.json`, atomic `state.json`, a
separate attempt-token-bound cancellation marker for every attempt, and one
OS-backed manager lease under `output/.rag-jobs/JOB_ID/`. The spec pins the
canonical submission working directory and private output-root filesystem
identities, and every detached attempt runs from that directory. Attempts store
private runtime metadata, a bounded final 8 MiB worker-log tail, and correlated
event/report files. Each attempt also has an atomic, manager-owned
`attempt.report.json` schema-v1 outcome snapshot. It binds the job, attempt,
run, and operation; records submitted/manager/worker/cancel/recovery/cleanup/
finish times; derives dispatch, startup, worker, cancellation, recovery, and
total durations; and records the redacted finalizer, terminal reason, cleanup,
worker-telemetry, and process-recovery outcomes. Reconciliation explicitly
marks unavailable runtime metadata, report repair, and reconstructed timing;
an intact terminal report is immutable and repeated reconciliation leaves it
byte-identical.

The attempt report never contains arguments, paths, attempt tokens, process
IDs or birth identities, logs, exception text, prompts, or model responses. It
is strict, size-bounded, atomically replaced, and current-user-only. This makes
it suitable as durable operational evidence, not as a substitute for the
private worker log or the stage-level `run.report.json`. POSIX uses verified
`0700`/`0600` modes; Windows uses a protected DACL for only the current SID.
Logs can contain source paths and model output even though status and telemetry
are redacted, so treat the entire job root as sensitive. Job records persist
for audit and explicit resume; there is no automatic deletion. `jobs delete`
uses ownership validation, an irreversible `deleting` state, quarantine,
identity revalidation, and explicit `--apply` before removing a safe terminal
job. It refuses active jobs and jobs with unconfirmed cleanup.

The current on-disk job-store schema is version 2. The version-1 prototype was
never released and is intentionally not auto-migrated: it did not persist the
submission/output directory identities needed to authorize a resumed worker.
Version-1 records therefore fail closed with an unsupported-schema error. Only
archive or remove such development records after independently confirming that
their manager and worker trees are gone.

The state machine is `queued -> starting -> running -> terminal`, with
`cancel_requested` between a running attempt and confirmed cancellation.
Terminal states are `succeeded`, `partial`, `failed`, `cancelled`, and
`interrupted`; `orphaned` is a terminal but deliberately non-resumable state for
unverifiable or uncleared processes, and `deleting` is irreversible. The
manager never claims `cancelled` until process-tree cleanup and matching
cancellation telemetry are both confirmed. Queued cancellation terminalizes
without launching a worker. A status/list call reconciles a managerless attempt
using PID plus process-birth identity; it will not signal a reused or
unverifiable PID and never auto-resumes provider calls. Windows Job Objects kill
descendants if a manager exits. Linux recovery targets only an exact
PID/birth-matched process-group leader. Platforms without a verifiable birth
identity and all cleanup uncertainty become `orphaned`, blocking resume.
On POSIX, a descendant that deliberately calls `setsid()` can escape the
worker's process group; do not execute untrusted worker extensions under this
local supervision boundary.

For `full` and `batch`, the worker durably binds a pathname hash and item index
to the allocated run while holding the per-PDF lease. A resumed attempt targets
that exact run even if another same-stem run was created later. Completed batch
items re-enter exact-run resume so source, completion manifests, and outputs are
revalidated; valid stages then skip individually. Failed or untouched items
resume or allocate exactly once. Resuming cloud/LLM work can repeat paid or
externally visible
calls that failed before their response was durably committed, so inspect
status/logs and provider usage before choosing `jobs resume`.

### Authenticated local service (draft v1)

`service_api.py` exposes a stable, authenticated application boundary for one
local OS user. It is deliberately smaller than the CLI: clients can inspect
configured corpora, retrieve bounded Qdrant search hits, and manage durable
reindex jobs. It does not expose conversion, arbitrary commands, filesystem
paths, job arguments or logs, provider identities, raw exceptions, reranking,
or LLM answer generation.

Create an isolated environment and install the exact narrow service runtime.
Contributors can add the separately locked test tools after verifying the
runtime-only boundary, which is the same order CI uses:

```bash
python -m venv .venv
# Activate .venv using the command for your shell, then:
python -m pip install --require-hashes -r requirements-service.lock
# Development/testing only:
python -m pip install --require-hashes -r requirements-test.lock
```

Create distinct owner-only credentials. The command creates and hardens the
parent directory and files, and reports success without printing either token:

```bash
python service_api.py init-tokens \
  --reader-token-file output/.rag-service-private/reader.token \
  --admin-token-file output/.rag-service-private/admin.token
```

Copy [`service-config.example.json`](service-config.example.json) to that
private directory, edit its corpus binding, then enforce the same cross-platform
private-file policy. The example's relative paths assume this exact destination;
all relative config paths resolve from the config file's directory.

```bash
cp service-config.example.json output/.rag-service-private/config.json
python -c "from pathlib import Path; import storage_policy; storage_policy.enforce_private_path(Path('output/.rag-service-private/config.json'), directory=False)"
```

The registry is credential-free, strictly shaped, Qdrant-only, and rejects
links. Its database directory and chunks JSONL must already exist, while the
collection name and embedding model must exactly match the indexed collection.
The example uses MiniMax `embo-01`, which requires `MINIMAX_API_KEY` and works
with the narrow service profile's HTTP dependencies. Change it to the model
that built the index. Local sentence-transformer models require the full locked
runtime; Voyage, OpenAI, and Cohere embeddings require their optional SDKs, so
add the full locked runtime while retaining `requirements-service.lock` when
using those backends. The service profile does not silently install those
heavier providers.

Loopback describes who can call this HTTP service; it does not prevent provider
network egress. A cloud embedding model sends raw search query text during
search and corpus chunk text during reindex, using credentials inherited by the
supervised worker. Use a compatible local embedding model with the full locked
runtime, and verify its configuration, when queries and corpus text must remain
on the machine.

Start one worker on literal loopback:

```bash
python service_api.py serve \
  --config output/.rag-service-private/config.json \
  --reader-token-file output/.rag-service-private/reader.token \
  --admin-token-file output/.rag-service-private/admin.token \
  --working-directory . \
  --output-root output \
  --job-root output/.rag-service-jobs \
  --service-state-root output/.rag-service
```

The examples below assume the two token values have been loaded into
`READER_TOKEN` and `ADMIN_TOKEN` without printing them. Health endpoints are
unauthenticated; reader credentials can inspect the versioned contract/corpora
and search, while admin credentials can also submit and manage jobs.

```bash
# Process and corpus readiness
curl -i http://127.0.0.1:8765/health/live
curl -i http://127.0.0.1:8765/health/ready
curl -sS -H "Authorization: Bearer $READER_TOKEN" \
  http://127.0.0.1:8765/v1/corpora

# Bounded primary retrieval only: no reranker, neighbor context, or answer
curl -sS -X POST \
  -H "Authorization: Bearer $READER_TOKEN" \
  -H "Content-Type: application/json" \
  --data '{"query":"minimum contacts","limit":5,"mode":"auto","filters":{"chapter_num":4}}' \
  http://127.0.0.1:8765/v1/corpora/civil_procedure/search

# A new key returns 202; an exact replay returns the same job with 200
curl -i -X POST \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Idempotency-Key: civpro-2026-07-22-v1" \
  -H "Content-Type: application/json" \
  --data '{"full_reindex":false}' \
  http://127.0.0.1:8765/v1/corpora/civil_procedure/reindex
```

Every job response includes an `ETag` for its exact attempt and revision.
Set `JOB_ID` from the response and `ETAG` to the most recently returned quoted
value (for example, `ETAG='"rag-job-a1-r4"'`). Mutations reject stale values.
Cancellation is active-job-only; resume is explicit and only accepts a current
`partial`, `failed`, `cancelled`, or `interrupted` attempt. Refresh status and
the ETag between each operation.

```bash
curl -i -H "Authorization: Bearer $ADMIN_TOKEN" \
  "http://127.0.0.1:8765/v1/jobs/$JOB_ID"

curl -i -X POST \
  -H "Authorization: Bearer $ADMIN_TOKEN" -H "If-Match: $ETAG" \
  "http://127.0.0.1:8765/v1/jobs/$JOB_ID/cancel"

curl -i -X POST \
  -H "Authorization: Bearer $ADMIN_TOKEN" -H "If-Match: $ETAG" \
  "http://127.0.0.1:8765/v1/jobs/$JOB_ID/resume"

# Inspect the terminal-only dry run, refresh ETAG, then confirm the exact ID
curl -i -X POST -H "Authorization: Bearer $ADMIN_TOKEN" \
  "http://127.0.0.1:8765/v1/jobs/$JOB_ID/deletion-plan"
curl -i -X DELETE \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "If-Match: $ETAG" -H "X-Confirm-Job-ID: $JOB_ID" \
  "http://127.0.0.1:8765/v1/jobs/$JOB_ID"
```

Use a new idempotency key for a different reindex intent; reusing a key with a
different request conflicts instead of mutating the existing job. If the
service stops between durable queueing and launch, startup terminalizes that
stale `queued` attempt as `failed`; it never auto-launches or repeats provider
work. An administrator must inspect it and submit an ETag-guarded resume.

This is a loopback-only, single-principal boundary, not a multi-user or hosted
API. It accepts only `127.0.0.1` or `::1`, verifies the peer, disables proxy
headers, CORS, Swagger/ReDoc, Uvicorn access logs, and the server header, and
serves the static OpenAPI 3.1 contract only to an authenticated reader at
`/v1/openapi.json` (the committed snapshot is
[`service-openapi-v1.json`](service-openapi-v1.json)). Do not place it behind a
reverse proxy, port forward, container bridge, or TLS terminator, and do not
reuse its two roles as tenant isolation.

The service defaults to the dedicated `output/.rag-service-jobs` root; never
share the CLI's `output/.rag-jobs` root with it. Private markers bind that job
root bidirectionally to one stable service-state root. Startup fails closed for
an unowned nonempty root or a mismatched owner, and the API hides and never
reconciles or mutates jobs whose immutable execution spec is not an exact
configured service reindex. The enforced singleton lease is on the
service-state root; per-job leases coordinate records but do not turn the store
into a multi-principal broker. Search queries and bounded results cross the
supervised process boundary through private short-lived files under
`.rag-service/search-tmp`; verified cleanup runs after each request and at
startup. That cleanup, job deletion, and retention are logical filesystem
deletion, not guaranteed physical erasure from SSDs, backups, snapshots, or
cloud-sync history. Put state and job roots on suitable private storage for the
data's sensitivity.

### Structured run telemetry

Every foreground pipeline command accepts an optional correlated event stream
and aggregate report. The `jobs` facade reserves these flags and injects
attempt-owned paths itself:

```bash
python rag.py full --pdf book.pdf \
  --run-id semester-build-7 \
  --run-events private-telemetry/full.events.jsonl \
  --run-report private-telemetry/full.report.json
```

`--run-id` must be a 1-128 character opaque identifier containing only letters,
digits, `.`, `_`, or `-`; omit it to generate a random ID. For supervised
vector-store commands, the parent allocates and persists the run identity before
starting the worker. The same ID appears in stage events, the aggregate run
report, and any `--llm-events` or `--llm-report` output. It is correlation only:
it does not change an LLM request ID or cache key.

The schema-v1 JSONL stream records a sequence number, timestamp, operation,
stage, status, numeric/boolean metrics, and a safe diagnostic when applicable.
The schema-v2 JSON report summarizes run status (`succeeded`, `partial`,
`failed`, or `cancelled`), elapsed time, event count, per-stage counts and
durations, and typed numeric/boolean metric aggregates. The job supervisor can
still read an already-committed schema-v1 terminal report. New writers always
publish schema v2, reject metric type drift and non-finite aggregate totals
before appending an event, and persist strict JSON.

Pipeline stages cover conversion, chunk/index lease acquisition, chunking,
indexing, exports, and RAPTOR. Successful index metrics come from the committed
`IndexOutcome`: disposition plus total, changed, unchanged, removed, upserted,
batch, physically verified record counts, exact physical mutation calls, and
bounded-queue pressure. A failed index stage instead records separately named
content-free attempted delete/create/upsert/queue counts with
`committed=false`; it does not mislabel partial physical work as a committed
outcome. LLM observations aggregate calls, attempts, retries, latency, and
exact/estimated tokens.

Each invocation replaces the supplied run-event/report files with its current
run. Run and LLM event/report outputs must resolve to pairwise distinct files;
lexical, case, symlink, and existing hard-link aliases are rejected. A `batch`
that completes its best-effort loop but has failed, missing, or
unprocessed inputs reports `partial` while preserving the command's existing
summary behavior. After a deadline or interruption, the supervisor first
confirms worker cleanup, then recovers the current event stream, terminates any
unmatched stages, and writes the final failure/cancellation record. Recovery
counts and stage-closure time are also bound into the terminal event, so a
missing report can be reconstructed and repeated finalization preserves the
same evidence. A terminal success already committed by the worker wins over a
late cancellation.

Run telemetry intentionally omits source and output paths, prompts, responses,
credentials, endpoints, and exception messages. Its message fingerprint is a
process-keyed opaque digest rather than a reusable plaintext hash. This does not
sanitize normal console/file logs, which can still contain paths and operational
details. Telemetry uses the same private storage policy as pipeline artifacts
and LLM outputs: POSIX directories/files are verified at `0700`/`0600`, while
Windows paths receive a protected DACL granting full control only to the current
user SID.

### Operational recovery drills

Run both disposable recovery drills into a new or empty evidence directory:

```bash
python tools/run_operational_drills.py \
  --output-dir output/operational-drill-2026-07-24
```

The hard-kill drill starts a real child telemetry writer inside a supervised
process tree, waits until one stage is durably active, kills the tree, confirms
complete cleanup, and only then recovers the event stream into a terminal
report. Parent death also closes the containment primitive and kills the tree;
unconfirmed cleanup is an explicit failed result. Its child receives only
allowlisted interpreter/OS environment plumbing, not ambient provider
credentials. The synced-publication
drill writes one private staging payload while an injected replacement seam
raises two Windows sharing violations; publication must retry the same pinned
bytes, verify the target, and leave no staging file.

`operational-drill.report.json` is a strict, size-bounded schema-v1 report with
only a random run ID, timestamps, durations, counts, booleans, and safe terminal
status. It contains no paths, process identities, exception strings,
credentials, corpus text, prompts, or model output. The drill directory must be
empty so evidence from separate runs cannot be interleaved or silently
overwritten. Exit status is `0` only when both drills pass, `1` when a completed
report contains a failed drill, and `2` when the run cannot start or publish.

### Private storage and retention

Sensitive pipeline artifacts, derived study outputs, vector-store roots, LLM
cache/events/reports, telemetry, lock sentinels, and UI exports use one shared
storage policy. Existing managed parent directories are hardened before content
is staged. Atomic writers reject symbolic-link/junction components, stage and
flush a private file, and replace the final path; append-only LLM events reject
hard-linked files and use a no-follow Windows handle or POSIX descriptor.
Pathname-only PDF writers publish through a private random staging path. On
Windows, this is real ACL enforcement rather than `chmod` emulation.
Existing cache and vector-store trees are recursively migrated before use;
root-identity records under the private sibling `.rag-storage-policy/`
directory avoid repeating that full scan. Legacy pipeline runs are recursively
hardened whenever they resume.

Each new `full` or `batch` run receives a private `.rag-run.json` ownership
manifest. UI exports receive `.rag-owned.json` and become retention-eligible
only after they reach `complete`. Cleanup is always a dry run unless `--apply`
is present:

```bash
# Inspect, then delete one manifest-owned run under pipeline/vector leases
python rag.py storage --delete-run Civil_procedure
python rag.py storage --delete-run Civil_procedure --apply

# Prune validated plaintext LLM responses older than 30 days and keep the
# remaining owned cache within the default 5 GiB ceiling
python rag.py storage --prune-llm-cache --older-than-days 30
python rag.py storage --prune-llm-cache --older-than-days 30 --apply

# Prune completed UI exports; incomplete/failed directories require review
python rag.py storage --prune-ui-exports --older-than-days 7

# Inspect recoverable leftovers from an interrupted deletion, then purge them
python rag.py storage --purge-quarantine --older-than-days 7
python rag.py storage --purge-quarantine --older-than-days 7 --apply

# Inspect, then prune old marker-owned PDF snapshot trees left by a hard kill
python rag.py storage --prune-snapshot-scratch --older-than-days 1
python rag.py storage --prune-snapshot-scratch --older-than-days 1 --apply
```

Use `--output-root`, `--llm-cache-dir`, `--snapshot-scratch-root`,
`--max-cache-bytes`, and `--json` for custom locations, size policy, and
automation. `--snapshot-scratch-root` is the base directory containing the
owned `rag-pipeline-scratch-v1` directory; `RAG_SNAPSHOT_SCRATCH` selects the
same base for conversion and table-recovery snapshots. A plan deletes only data whose
marker/schema/token or cache key validates; unowned directories, special files,
links, junctions, and hard-linked content fail closed. Applied run deletion
replans after acquiring the run and all existing vector-store leases, moves the
exact owned entries into `.rag-quarantine`, and rolls back staged moves if the
transaction cannot complete. Each moved inode/tree is fingerprinted again in
quarantine before erasure, so a post-plan atomic replacement is preserved and
the operation rolls back. Do not manually delete `.rag-run.json`,
`.rag-owned.json`, storage-policy records, update markers, or lock sidecars.

Deletion here is logical filesystem deletion, not a guarantee of physical
erasure. Dropbox/cloud history, backups, snapshots, another hard-link alias,
and SSD wear-leveling may retain bytes. Use the provider's retention controls
and device-appropriate cryptographic erasure when those copies are in scope.

### Interactive Menu

Running `python rag.py` with no arguments launches a guided menu that walks
through file selection, options, and command construction. All commands are
accessible through the menu, with file path validation and sensible defaults.
For LLM-backed operations, the menu also offers DeepSeek V4 Pro/Flash, MiniMax,
Ollama, Gemini, and custom providers. API keys entered there use a hidden prompt
and are redacted from the generated command shown on screen. They are removed
from worker process arguments and supplied only through that worker's child-only
environment; press Enter at the key prompt to use the corresponding ambient
environment variable instead.

## Searching

The standalone `query` command cannot infer which book run to use. Pass that
run's database, chunks file, and collection explicitly. These examples target
the first `Civil_procedure.pdf` run shown above.

```bash
# Auto mode (default): calibrated hybrid when available; vector fallback
python rag.py query "minimum contacts personal jurisdiction" \
  --db output/Civil_procedure/Civil_procedure_chroma \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  --collection civil_procedure

# Require hybrid search rather than allowing a vector fallback
python rag.py query "Rule 12(b)(6) motion to dismiss" --hybrid \
  --db output/Civil_procedure/Civil_procedure_chroma \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  --collection civil_procedure

# Search + source-grounded LLM answer with validated citation IDs
python rag.py query "minimum contacts test" --answer \
  --db output/Civil_procedure/Civil_procedure_chroma \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  --collection civil_procedure

# Opt-in neighboring evidence; each neighbor keeps its own citation identity
python rag.py query "minimum contacts test" --answer --context-window 1 \
  --db output/Civil_procedure/Civil_procedure_chroma \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  --collection civil_procedure

# Filter by content type
python rag.py query "stream of commerce" --type case_opinion \
  --db output/Civil_procedure/Civil_procedure_chroma \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  --collection civil_procedure

# Filter by chapter
python rag.py query "supplemental jurisdiction" --chapter 12 \
  --db output/Civil_procedure/Civil_procedure_chroma \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  --collection civil_procedure

# JSON output for programmatic use
python rag.py query "Erie doctrine" --json -n 10 \
  --db output/Civil_procedure/Civil_procedure_chroma \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  --collection civil_procedure

# Force vector-only retrieval and cross-encoder reranking
python rag.py query "due process" --vector-only --rerank \
  --db output/Civil_procedure/Civil_procedure_chroma \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  --collection civil_procedure

# Qdrant backend with native hybrid search
python rag.py query "Rule 12(b)(6)" --db-backend qdrant --hybrid \
  --db output/Civil_procedure/Civil_procedure_qdrant \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  --collection civil_procedure
```

### How search works

```
Query --> [1] Embedding (nomic MoE) --> Vector similarity (ChromaDB/Qdrant)
                                              |
      --> [2] Legal analyzer + BM25 ---> [3] weighted RRF (auto/--hybrid)
                                              |
                                              v
                              [4] Table-family collapse
                                              |
                                              v
                                  [5] Adaptive reranker (BGE)
                                              |
                                              v
                                        Top-K results
                                              |
                                              v
                              [6] Neighbor assembly (opt-in)
                                              |
                                              v
                                     [7] LLM answer (if --answer)
```

1. **Vector search** retrieves semantically similar chunks.
2. **Legal lexical search** normalizes citation typography and aliases such as
   `§ 1332`, `U.S.C.`, and `Fed. R. Civ. P. 12(b)(6)`, then searches raw text
   plus bounded case, section, heading, and context metadata.
3. **RRF fusion** combines the rankings. Chroma defaults to the judged-set
   calibration `dense=0.5`, `lexical=1.0`, `k=10`; Qdrant uses native RRF.
4. **Table-family collapse** suppresses a whole-table parent only when one of
   its row children is already among the candidates. Distinct sibling rows are
   retained so answers may draw on more than one row.
5. **Adaptive reranking** reranks vector fallback automatically but preserves
   calibrated hybrid order. `--rerank` forces BGE reranking and `--no-rerank`
   disables it.
6. **Neighbor assembly** (optional) resolves canonical preceding/following
   chunks from the exact quality-attested JSONL generation. It preserves the
   ranked hits, hard filters, source/chapter boundary, and one stable ID per
   supplementary source.
7. **Answer generation** (optional) gives each retrieved source a stable ID and
   requires the configured LLM to cite those sources as `[S1]`, `[S2]`, and so on.

Use `--vector-only` to suppress lexical retrieval. Advanced reproducibility
controls are `--overfetch`, `--rrf-k`, `--dense-weight`, `--sparse-weight`, and
`--reranker-model`. Search responses distinguish the requested mode (`auto`,
`hybrid`, or `vector`) from the effective mode after any safe fallback.

`--context-window 1` or `2` attaches supplementary neighbors after ranking;
the default `0` performs no context-specific snapshot load and preserves the
historical response shape. Context-enabled queries require the chunks and
quality-report SHA-256 values to match the active index manifest for either
backend. Assembly follows final published order, never crosses a source or
explicit chapter, reapplies content/chapter filters, reserves ranked primaries,
and emits identical text only once while retaining equivalent-source aliases.
Previous chunks contribute their tail and following chunks their head. Bound
the total serialized supplementary evidence and each neighbor's text with the
character-based `--context-max-characters` and
`--context-segment-characters` controls. Locating metadata is independently
bounded and charged to the total. Answer generation separately caps each
rendered primary or neighbor excerpt at 2,400 characters and admits at most five
primaries plus ten supplementary sources. The defaults are 8,000 total and
1,600 per-neighbor text; hard maxima are 32,000 and 8,000. Ranked retrieval
metrics remain based only on primary hits;
supplementary context is not promoted into the ranking.

### Answer Generation (`--answer`)

After retrieval and reranking, the `--answer` flag assigns the retrieved chunks
query-local citation labels such as `[S1]`, while retaining a stable source ID
for each chunk. Retrieved text is marked as untrusted evidence in the prompt.
The generated answer may cite only supplied source labels. Unknown labels,
uncited answer paragraphs, or direct quotations absent from the exact supplied
source excerpt cause the pipeline to withhold the entire answer. Long chunks use
a query-centered excerpt so matching evidence near the end is not discarded.
Empty responses and explicit insufficient-evidence responses also abstain.
When neighbor assembly is enabled, every rendered supplementary segment
receives its own `[S#]` label and stable ID; byte-identical occurrences are
represented as equivalent-source aliases. The pipeline never concatenates
neighbor text under the primary chunk's citation, so quote validation and
source tracing remain exact. Supplementary source mappings report `score: null`
and `score_kind: supplementary_context`; they never inherit the primary hit's
relevance score.

The terminal output prints the answer and cited source locations before the
ordinary ranked results. Combine `--answer` with `--json` for a structured
payload:

```json
{
  "answer": "The governing rule is ... [S1].",
  "citations": ["S1"],
  "sources": {
    "S1": {
      "source_id": "chunk_0123456789abcdef",
      "score": 0.92,
      "metadata": {"page_range": "pp.101-103"},
      "excerpt": "..."
    }
  },
  "answer_warnings": [],
  "abstained": false,
  "results": []
}
```

`sources` maps query-local labels to stable IDs, retrieval scores, metadata,
and excerpts. `answer_warnings` records removed/invalid citations or provider
failures, and `abstained` is `true` whenever an unsupported answer was withheld.

```bash
python rag.py query "What is the minimum contacts test?" --answer \
  --db output/Civil_procedure/Civil_procedure_chroma \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  --collection civil_procedure
```

## Optional LLM Intelligence Layer

MiniMax M2.7-highspeed remains the default cloud model. DeepSeek is also
supported directly through its official `https://api.deepseek.com` endpoint,
with `deepseek-v4-pro` and `deepseek-v4-flash` as the built-in model choices.
See the official [DeepSeek API documentation](https://api-docs.deepseek.com/)
and [thinking-mode guide](https://api-docs.deepseek.com/guides/thinking_mode/).

LLM-enabled operations include content classification, contextual retrieval
prefixes, RAPTOR summaries, case briefs, exam questions, flashcards, quality
scoring, heading reconstruction, answer generation, and optional TOC scaffold
parsing/review (`--llm-scaffold`).

**Provider chain**: configured OpenAI-compatible cloud API -> Ollama (local) ->
Gemini (API) -> deterministic fallback where the feature supports one. A cloud
provider is skipped when it has no key; a failed or empty response falls through
to the next provider. Features without a deterministic fallback return no LLM
result after all configured providers fail.

### Reproducible LLM execution

Every LLM-backed CLI command uses a shared execution layer that records the
actual provider/model selected, applies run-wide budgets, coalesces identical
in-flight requests, and can reuse successful results across runs. Existing
Python integrations remain compatible: `_call_llm(...)` still returns
`str | None`, while `_call_llm_result(...)` exposes structured provenance.

CLI calls use a persistent, machine-local response cache by default. Direct
Python calls default to `off`, preserving the original library behavior. A
cache key covers the exact prompt digest, operation and prompt versions,
generation settings, timeout, fallback policy, and ordered provider/model/
endpoint identities. Prompts, API keys, and raw endpoint URLs are not stored in
the cache key or record. Only non-empty successful responses are cached, and
entries use integrity hashes plus atomic replacement so truncated or tampered
records become misses and are repaired by the next successful call.

The cache itself contains successful response text in plaintext. Treat its
directory as sensitive when textbook excerpts, client facts, or other private
material can appear in model output. Use `--llm-cache-mode off` for no disk
cache, `readonly` to consume existing entries without writing, or a custom
`--llm-cache-dir`. Cache paths are protected by the shared storage policy. The
default location is the platform user-cache directory
(`%LOCALAPPDATA%/rag-pipeline/llm-cache` on Windows when available,
`$XDG_CACHE_HOME/rag-pipeline/llm-cache` on Linux when configured).

| Option | Purpose |
|--------|---------|
| `--llm-cache-mode readwrite|readonly|refresh|off` | Read/write policy; `refresh` bypasses a hit and replaces it after live success |
| `--llm-cache-dir PATH` | Override the machine-local response-cache directory |
| `--llm-events PATH` | Append one prompt-free JSONL event per logical request |
| `--llm-report PATH` | Atomically write a prompt-free aggregate run report, including on handled failures |
| `--llm-fallback ordered|none` | Use the provider chain or only its first configured provider |
| `--llm-failure-policy best-effort|strict` | Preserve feature-level fallbacks or fail when no provider returns output |
| `--max-llm-calls N` | Hard cap on logical provider callback dispatches during the run |
| `--max-llm-transport-attempts N` | Hard cap on physical provider transport admissions, including retries |
| `--max-llm-reserved-tokens N` | Hard cap on conservative prompt-plus-maximum-output token reservations |

```bash
# Reproducible generation with an aggregate report and explicit ceilings
python rag.py generate-questions \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  --llm-report output/Civil_procedure/llm-run.json \
  --max-llm-calls 50 --max-llm-transport-attempts 60 \
  --max-llm-reserved-tokens 250000

# Retry the preferred cloud provider even if a fallback result is cached
python rag.py brief \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  --llm-cache-mode refresh

# Require the selected first provider and fail on unavailable output
python rag.py query "What is the Erie doctrine?" --answer \
  --llm-fallback none --llm-failure-policy strict \
  --db output/Civil_procedure/Civil_procedure_chroma \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  --collection civil_procedure
```

Caching is chain-level: if Ollama or Gemini succeeds after the preferred cloud
provider fails, that successful fallback remains the warm result. Use
`--llm-cache-mode refresh` to retry the preferred provider and replace it.
Single-flight coalescing and budgets are process-local; separate processes do
not share admissions. Event logs and reports include stable request IDs,
operation labels, provider/model names, latency, fallback paths, cache status,
logical provider dispatches, underlying transport attempts/retries, and a
secret-safe category for each failed attempt. They omit prompts, responses,
credentials, raw endpoints, response bodies, and exception messages.

Token accounting uses provider-native fields when available: OpenAI-compatible
`usage`, Ollama's prompt/evaluation counts, and Gemini usage metadata. Reports
separate exact, estimated, and unavailable usage and retain cached-prompt and
reasoning-token breakdowns. Legacy/custom callbacks without native counts use
the conservative character estimate. Cache hits preserve the selected result's
token accounting but correctly report zero live provider or transport attempts.

Budget terminology is intentionally precise. `--max-llm-calls` counts logical
provider callback dispatches, while `--max-llm-transport-attempts` atomically
admits each physical HTTP or SDK call, including an adapter's internal retries.
Once the physical cap is exhausted, no additional transport begins and ordered
fallback stops instead of dispatching another provider. Reserved tokens use a
provider-neutral `characters / 4 + max output` estimate and accumulate for each
fallback dispatch. Cache hits and single-flight followers consume no logical
dispatch, token reservation, or physical transport admission.

The runtime admits the initial transport before invoking a provider callback.
Built-in adapters also use the request's `admit_transport_retry()` hook
immediately before every additional attempt. Custom provider callbacks with
their own hidden retry loops must do the same before each retry; otherwise the
physical ceiling cannot stop those extra calls. When a cap is active, a
structured callback that reports more attempts than it admitted fails closed
and stops fallback, with the contract violation exposed in the run report.
Gemini receives the configured timeout and has SDK retries disabled, so its
transport count remains explicit.

### DeepSeek V4 configuration

The shorter LLM option names and the existing cloud option names are aliases:

| Option | Equivalent option | Purpose |
|--------|-------------------|---------|
| `--llm-url URL` | `--cloud-url URL` | OpenAI-compatible API base URL |
| `--llm-model MODEL` | `--cloud-model MODEL` | Cloud model name |
| `--api-key KEY` | `--cloud-key KEY` | One-off, hand-entered cloud API key |
| `--thinking` | — | Enable supported model reasoning |
| `--no-thinking` | — | Explicitly disable supported model reasoning (the default) |

Selecting a `deepseek-*` model while the URL is still at its MiniMax default
automatically selects the official DeepSeek endpoint. Conversely, selecting the
official DeepSeek endpoint without changing the default model selects
`deepseek-v4-pro`. `--thinking` / `--no-thinking` controls DeepSeek's thinking
mode and is also forwarded to Ollama's `think` option.

Cloud API keys are resolved without reusing a provider-specific secret for an
unrelated host:

1. `--api-key` / `--cloud-key`, when supplied, always wins.
2. DeepSeek uses `DEEPSEEK_API_KEY`, then falls back to `CLOUD_API_KEY`.
3. MiniMax uses `MINIMAX_API_KEY`, then falls back to `CLOUD_API_KEY`.
4. Any other custom cloud endpoint uses only `CLOUD_API_KEY`.

```bash
# DeepSeek V4 Pro with thinking (set DEEPSEEK_API_KEY first)
python rag.py generate-questions \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  --llm-model deepseek-v4-pro --thinking

# DeepSeek V4 Flash with thinking explicitly disabled
python rag.py brief \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  --llm-url https://api.deepseek.com \
  --llm-model deepseek-v4-flash --no-thinking

# Use the default MiniMax cloud model (requires MINIMAX_API_KEY)
python rag.py chunk \
  --doc output/Civil_procedure/Civil_procedure.json \
  --out output/Civil_procedure/Civil_procedure_chunks.jsonl \
  --llm-classify --contextualize

# Configure the local Ollama fallback (unset cloud keys to use it first)
python rag.py chunk \
  --doc output/Civil_procedure/Civil_procedure.json \
  --out output/Civil_procedure/Civil_procedure_chunks.jsonl \
  --llm-classify --ollama-url http://127.0.0.1:11434 --ollama-model qwen3:30b

# Configure the Gemini fallback
python rag.py chunk \
  --doc output/Civil_procedure/Civil_procedure.json \
  --out output/Civil_procedure/Civil_procedure_chunks.jsonl \
  --llm-classify --gemini-key YOUR_KEY

# Custom OpenAI-compatible endpoint
python rag.py chunk \
  --doc output/Civil_procedure/Civil_procedure.json \
  --out output/Civil_procedure/Civil_procedure_chunks.jsonl \
  --llm-classify --cloud-url https://api.example.com/v1 \
  --cloud-model model-name --cloud-key KEY
```

### Adaptive Rate Limiting

The pipeline automatically handles API rate limits (429 errors) with an adaptive
throttle. Starting at `--llm-workers` (default 10), it halves active workers on
each 429, adds cooldown delays, and gradually recovers after 20 consecutive
successes. No manual intervention needed.

```bash
# Start with 10 parallel workers (default)
python rag.py full --pdf book.pdf --llm-classify --llm-workers 10

# Conservative start for strict rate limits
python rag.py full --pdf book.pdf --llm-classify --llm-workers 4
```

## Case Briefs

Generate structured case briefs from every `case_opinion` chunk. Each brief
extracts Facts, Issue, Holding, and Reasoning.

```bash
python rag.py brief \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  -o output/Civil_procedure/Civil_procedure_briefs.jsonl
```

Output (`output/Civil_procedure/Civil_procedure_briefs.jsonl`):
```json
{
  "case_name": "International Shoe Co. v. Washington",
  "facts": "International Shoe, a Delaware corporation with its principal place...",
  "issue": "Whether a state may exercise personal jurisdiction over a corporation...",
  "holding": "The Court held that a state may exercise jurisdiction over...",
  "reasoning": "The Court established the 'minimum contacts' framework...",
  "page_range": "pp.101-106",
  "chapter_num": 3
}
```

## Exam Question Generation

Generate law school exam-style questions from chapter content. For each chapter,
the LLM produces 5 questions: 2 issue-spotter hypotheticals, 2 doctrinal
questions, and 1 policy question.

```bash
python rag.py generate-questions \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  -o output/Civil_procedure/Civil_procedure_exam_questions.jsonl
```

Output (`output/Civil_procedure/Civil_procedure_exam_questions.jsonl`):
```json
{
  "question": "Plaintiff, a Texas resident, purchases a defective widget online...",
  "question_type": "issue_spotter",
  "chapter_num": 3,
  "chapter_title": "Personal Jurisdiction",
  "suggested_answer": "The key issue is whether the defendant has sufficient...",
  "source_chunks": [42, 43, 47]
}
```

## RAPTOR Summaries

Build a 3-level recursive summary tree (Recursive Abstractive Processing for
Tree-Organized Retrieval). Level 0 = raw chunks, Level 1 = section summaries
(~5-10 chunks clustered), Level 2 = chapter summaries.

```bash
python rag.py raptor \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl
python rag.py full --pdf book.pdf --raptor
```

RAPTOR nodes are written to a separate summary-tree JSON file. The pipeline
does not add those summaries to the vector index automatically.

## Export Formats

Single-file markdown is the default. `full --split-chapters` keeps that unified
file and additionally creates `Chapters/`; without the flag, no chapter
directory is created. On the standalone `export` command, `--split-chapters`
writes the chapter files instead of a unified markdown file.

```bash
# Markdown -- readable, with headings and formatting
python rag.py export \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  -o output/Civil_procedure/Civil_procedure.md --format markdown

# Split into one file per chapter (ideal for Claude Projects / NotebookLM)
python rag.py export \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  -o output/Civil_procedure/Civil_procedure.md \
  --format markdown --split-chapters

# Plain text + JSON metadata sidecar (for Anthropic citations API)
python rag.py export \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  -o output/Civil_procedure/Civil_procedure.txt --format plaintext

# Anki flashcards -- LLM-generated Q&A pairs as TSV
python rag.py export \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  -o output/Civil_procedure/Civil_procedure_flashcards.tsv --format flashcards

# Filter by content type
python rag.py export \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  -o output/Civil_procedure/Civil_procedure_cases.md \
  --include-types case_opinion notes_and_questions

# Filter by chapter
python rag.py export \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  -o output/Civil_procedure/Civil_procedure_selected.md --chapters 3 5 12

# Combine filters
python rag.py export \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  -o output/Civil_procedure/Civil_procedure.md \
  --chapters 3 --include-types case_opinion --split-chapters
```

### Using with Claude Projects / NotebookLM

Upload the exported markdown for clean, structured content. Split chapters give
best retrieval:

```bash
python rag.py export \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  -o output/Civil_procedure/Civil_procedure.md --split-chapters
```

Output:
```
output/Civil_procedure/Chapters/
  ch02_Subject_Matter_Jurisdiction.md
  ch03_Personal_Jurisdiction.md
  ch13_Special_Multiparty_Litigation.md
  front_matter.md
```

## AI Model Integration

### Embedding Models (`--embedding-model`)

Embeddings are computed at `index` time and stored. Chunking automatically caps
`--max-tokens` to a known model input limit; when `--contextualize` is enabled,
it also reserves room for the contextual prefix. The default 500-token chunk
budget fits Nomic v2's 512-token input after reserving its four-token
`search_document: ` prefix and two special tokens. Every new chunk records both
its raw `token_count` and its final `embedding_token_count` (context, retrieval
task prefix, and special tokens included).
Indexing recomputes final counts with the provider/model tokenizer where
available (using a conservative fallback), rejects oversized chunks, and sizes
API batches by aggregate tokens rather than record count. Legacy JSONL without
count fields is rechecked during indexing.

| Model | Type | Cost | Max Tokens | Best for |
|-------|------|------|-----------|----------|
| `nomic-ai/nomic-embed-text-v2-moe` | Local GPU | Free | 512 | **Default.** Best open-source; raw chunks are capped at 506 tokens. |
| `voyage-law-2` | Voyage API | ~$0.12/M tokens | 16000 | Legal-specific. Trained on case law. |
| `voyage-3-large` | Voyage API | ~$0.18/M tokens | 16000 | Best general Voyage model. |
| `text-embedding-3-large` | OpenAI API | $0.13/M tokens | 8191 | Best commercial general-purpose. |
| `embed-v4.0` | Cohere API | $0.10/M tokens | - | Multilingual. |
| `dunzhang/stella_en_400M_v5` | Local GPU | Free | 8192 | High quality (requires xformers). |
| `nlpaueb/legal-bert-base-uncased` | Local GPU | Free | 512 | Inventory only: legacy pickle weights are blocked. |

API models require env vars: `VOYAGE_API_KEY`, `OPENAI_API_KEY`, or `COHERE_API_KEY`.
The pipeline validates keys at startup before any heavy processing.

Reviewed local models are loaded only from byte-verified local directories;
runtime loaders never receive a Hub model ID. Nomic's external Python is copied
from its separately pinned code repository and its `auto_map` is deterministically
rewritten to local references before offline loading. BGE, BART, and Docling use
only selected safe weights. LegalBERT remains in the provenance inventory, but
its only PyTorch weight is pickle-based and therefore fails closed. An unknown
custom model also fails closed unless the operator explicitly sets
`RAG_ALLOW_UNPINNED_MODELS=1`; that escape hatch logs that provenance and byte
verification are disabled.

The first use synchronizes the selected files into
`~/.cache/rag-pipeline/model-artifacts` (override with
`RAG_MODEL_ARTIFACT_CACHE`). Subsequent loads rehash that isolated tree and run
offline. Delete a corrupt cache entry and rerun to synchronize it again; never
edit a published cache tree in place. Each process keeps one validated registry
snapshot so loader records and provenance cannot cross lock generations;
restart long-running processes after intentionally replacing the policy/lock.

### Cross-Encoder Reranker

Auto mode reranks vector-only results but leaves the calibrated hybrid ranking
intact. `--rerank` forces reranking for either retrieval mode. The pipeline
over-fetches 4x candidates by default, then BGE
(`BAAI/bge-reranker-v2-m3`) scores each query against a bounded representation
containing case, section, heading, chapter, context, and raw text. Returned text
remains the original chunk. Local models are lazy-loaded and cached by exact
model name, so switching rerankers cannot silently reuse the wrong model.

Additional reranker backends: Cohere (`cohere-rerank-*`, requires API key) and
Jina (`jina-reranker-*`, requires API key).

Use `--no-rerank` to disable it, `--reranker-model` to select a model, and
`--overfetch` to control candidate depth.

### LLM Features

LLM-enabled features try the configured OpenAI-compatible cloud endpoint first
(MiniMax M2.7-highspeed by default, or DeepSeek/custom when selected), then
local Ollama at `http://127.0.0.1:11434`, then Gemini when configured. See
[DeepSeek V4 configuration](#deepseek-v4-configuration) for model, key, and
thinking options. Ordinary chunking and TOC scaffold construction are
deterministic and make no LLM calls unless an LLM feature flag is supplied.

| Feature | Flag / Command | What it does |
|---------|---------------|--------------|
| Content classification | `--llm-classify` | Replaces regex type detection with LLM inference per chunk |
| Contextual retrieval | `--contextualize` | Generates 1-2 sentence context prefix per chunk (Anthropic pattern) |
| Neighbor assembly | `query --context-window N` | Adds manifest-bound adjacent evidence after ranking while preserving independent citations |
| Heading reconstruction | `--reconstruct-headings` | Infers full section paths for bare headings ("B", "III") |
| Quality scoring | `--quality-score` | Rates each chunk 1-5 for RAG usefulness |
| Answer generation | `--answer` (on query) | Produces source-cited answers and abstains when citations are unsupported |
| Case briefs | `brief` command | Structured Facts/Issue/Holding/Reasoning per case |
| Exam questions | `generate-questions` command | Issue-spotters, doctrinal, and policy questions |
| Flashcard export | `export --format flashcards` | Anki-compatible Q&A pairs |
| RAPTOR summaries | `raptor` command | 3-level recursive summary tree |
| TOC scaffold review | `--llm-scaffold` | Adds LLM layout analysis, hierarchy parsing, and validation to deterministic TOC parsing |

### Vector Database (`--db-backend`)

| Backend | Hybrid Search | Incremental | Best for |
|---------|--------------|-------------|----------|
| `chroma` (default) | BM25 + RRF (manual) | Manifest + hashes | Simple local use |
| `qdrant` | Native dense + sparse fusion | Manifest + hashes | Better hybrid, faster queries |

```bash
python rag.py full --pdf Civil_procedure.pdf --db-backend qdrant
python rag.py query "jurisdiction" --db-backend qdrant --hybrid \
  --db output/Civil_procedure/Civil_procedure_qdrant \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  --collection civil_procedure
```

### Incremental Re-indexing

The `index` command hashes each chunk's text and indexable metadata and stores
the hashes in an atomic, versioned manifest scoped to the database backend and
collection. The manifest also records its schema version, embedding model,
embedding dimension, validated model-artifact-lock SHA-256, exact source JSONL
SHA-256, source record count, exact table-row-child count, and the exact
schema-versioned quality-report SHA-256 pair. A compatible rerun embeds only changed/new
chunks, removes chunks no longer present, and skips unchanged chunks. Qdrant incremental runs scan payload-only
stable IDs before mutation and again after writes, refusing to advance the
manifest if points are missing, unexpected, duplicated, untracked, or returned
through a cyclic pagination sequence. Each audit is bounded by exact point
counts taken before and after its payload-only scroll, and rejects count drift,
premature termination, oversized/non-progressing pages, and repeated physical
point IDs. Use `--full-reindex` to recover from a physical collection/manifest
mismatch. Waited point deletes and upserts must also report Qdrant's
`completed` status before reconciliation or manifest commit can continue.
Chroma compatible runs perform the corresponding count-sandwiched, bounded
scan of API-visible document IDs and their `stable_id` metadata before any
incremental mutation, after deletions, and after all upserts. Changed durable
IDs are deleted and verified absent before replacement, so silent delete or
upsert no-ops cannot advance the manifest. Chroma delete batches respect the
client's advertised maximum size when available.

Before its first collection mutation, either backend creates a collection-
scoped recovery marker. The marker is removed only after all writes finish and
the atomic manifest replacement succeeds (including Qdrant's exact post-write
verification). A per-update token binds marker reuse and cleanup to the run that
created or safely replaced it, so a stale or concurrently replaced marker
cannot be declared clean. If a run is interrupted or fails after mutation
begins, queries, exact count inspection, and corpus-pinned evaluations fail
closed while the marker remains; the next `index` or `full --resume` run
rebuilds only that backend/collection and clears the marker after the recovered
index is committed. These identity scans detect count and set drift during
pagination; the recovery marker records crash state and is never used as a
mutex.

Every operation that opens a local vector client or relies on marker/manifest
consistency now takes a bounded, OS-backed exclusive lease for the canonical
database directory. The path-wide policy deliberately serializes reads as well
as writes for both backends because Qdrant local mode permits only one process
to open a database path. Artifact discovery and approximate directory-size
display remain best-effort filesystem diagnostics outside the lease; exact
collection counts are leased. The default wait is 30 seconds; use
`--db-lock-timeout SECONDS` on `index`, `query`, `info`, `storage`, `full`, or `batch`
(`0` means fail fast). Same-thread nested validation is reentrant, lock release
runs after client/manifest cleanup, and a killed process releases the kernel
lock. Persistent `.rag-locks/*.lock` sidecars are only locking inodes: their
existence never means a process owns the lease, and they must not be manually
deleted while operations may be active. These OS locks coordinate processes on
one filesystem host; a Dropbox-synchronized copy on another computer is a
separate concurrency domain. Windows extended drive/UNC spellings are
normalized, but equivalent drive-letter, administrative-UNC, and SUBST aliases
are not a supported way to access one live database. Use one path spelling and
do not move or rename a database directory while any operation may be active.
The lease timeout bounds only lock contention. The outer CLI/evaluation/menu
operation deadline bounds filesystem hydration, sentinel setup, and
storage-client calls. Web UI vector-client calls have their own isolated
deadlines; its best-effort artifact-size scan remains outside that boundary.

`full` and `batch` also serialize output-run allocation before choosing a book
suffix. When they regenerate chunks, they create the collection recovery marker
before work begins, publish the JSONL via atomic replacement, and retain the
vector lease until the matching index commits. A crash therefore exposes
neither partial JSONL nor an apparently clean old index paired with a new
corpus. Before any vector-client mutation, indexing validates the adjacent
quality report against one exact chunks snapshot; schema-v8 manifests bind the
validated schema-v4 report SHA-256 and attest the row-child count used to select
a safe candidate depth. Chroma hybrid search and opt-in neighbor
assembly compare both the chunks and quality-report SHA-256 values with the
manifest, parse and hash one exact file-handle snapshot, and refuse
cross-generation retrieval until reindexing. A legacy Chroma index
without a manifested source SHA-256 falls back to vector retrieval with a
warning instead of fusing unproven lexical data. Reranking begins only after the
retrieval lease and vector client are released, so model or API latency does not
block unrelated index access.

Conversion JSON/Markdown pairs, unified Markdown, chapter sets, RAPTOR trees,
and evaluation reports are published through flushed same-directory temporary
files. Versioned completion metadata binds resumable pipeline artifacts to the
exact source digest, record count, credential-free parameters, and output
hashes. Conversion and chunking parameters include the validated model-lock
digest, and chunking also records its tokenizer/classification/provider policy
without storing credentials. A hard kill before the final completion commit therefore leaves the
stage fail-closed: `full --resume` regenerates it instead of accepting a
truncated, stale, or partial artifact set. Legacy pipeline artifacts without
completion metadata regenerate once when resumed.

On Windows, transient synced-folder sharing/access failures (`winerror` 5, 32,
or 33) receive a small bounded retry only around replacement of the already
written, fsynced staging file. Every retry revalidates the parent identity,
destination leaf, staging identity and exact bytes, link count, and private
permissions after backoff. Owned-marker reads and exact artifact snapshot reads
separately retry only content-identical ctime churn. Artifact retries pin the
opened device, inode, size, mtime, and SHA-256 across attempts; any content
generation change still fails closed. Marker reads additionally pin link count,
schema, and ownership. Windows recomputes exact artifact hashes for every
verification because its `st_ctime` is a creation time rather than a safe
change counter; the bounded stat-keyed hash cache is used only on platforms
where ctime changes with file metadata or content.

The sequential background-upsert paths for both backends share teardown that
requests a worker stop, waits for completion, and attempts both executor and
progress closure; progress advances only after a write succeeds, and a
secondary cleanup error does not replace the original producer error. Chroma's
parallel API-embedding path likewise shuts down its executor and closes its
progress display before committing the manifest; an embedding or upsert error
takes precedence over either cleanup failure.

Every short-lived Chroma and Qdrant client used by indexing, search, and status
inspection is also closed deterministically. Indexing closes the client after
physical reconciliation but before manifest commit, so a cleanup failure keeps
the recovery marker and old manifest instead of reporting success while a
Windows database lock remains held. Operation errors take precedence over
secondary close errors. Chroma 1.5.2 is the minimum supported release because
it provides the public, reference-counted `close()` needed to release shared
local database handles without invalidating another live client. On Windows,
closing a filesystem-local Qdrant client is followed by one explicit garbage
collection to finalize unreachable SQLite cursors retained by the local client;
remote Qdrant clients and non-Windows platforms do not pay that cost. This
correctness trade-off can add a variable pause to a Windows local-mode request;
real-client CI keeps it observable, and it should be removed when the pinned
client release explicitly finalizes every persistence cursor.

If the model, model-artifact lock, vector dimension, or manifest schema
changes—or an older shared `chunk_hashes.json` sidecar is encountered—the
pipeline safely rebuilds only the requested collection. Sibling collections in the same database directory
are not deleted. The old sidecar is left in place for compatibility while the
rebuilt collection receives its own manifest. Manifest replacement is atomic,
so an interrupted write cannot leave partially written incremental state.
Chunk JSONL is parsed and schema-checked strictly before a collection can be
changed. `full --resume` always revalidates the manifest and hashes, and queries
refuse a model, model-lock generation, or vector dimension that conflicts with
an existing manifest.

The real-vector-client release rehearsal recreates the exact schema-5 manifest
field set emitted by the last integrated release, upgrades only the selected
collection to schema 8, and verifies exact IDs and hashes, sibling collection
and manifest preservation, a subsequent no-op, successful queries against both
collections, clean recovery-marker state, and immediate database-directory
removal on Windows and Linux for both Chroma and Qdrant.

```bash
python rag.py index \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  --db output/Civil_procedure/Civil_procedure_chroma \
  --collection civil_procedure                    # first run: indexes all
python rag.py index \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  --db output/Civil_procedure/Civil_procedure_chroma \
  --collection civil_procedure                    # unchanged chunks are skipped
python rag.py index --full-reindex \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  --db output/Civil_procedure/Civil_procedure_chroma \
  --collection civil_procedure                    # force complete rebuild
```

Use the same `--embedding-model` when querying the collection. Passing a new
model to `index` automatically triggers the safe collection rebuild described
above; `--full-reindex` remains available when an unconditional rebuild is
desired.

## Source-Preserving Chunking

Chunk preparation keeps Docling source items authoritative when flattened
chunk text would lose structure. Mixed prose/table chunks are separated;
tables are emitted once as Markdown with captions and nested footnotes; and
text merged across a front- or back-matter boundary is split by source page so
substantive content cannot be discarded with structural material. When a
Docling table cell omits text that is visibly present inside the source PDF's
table bounding box, the pipeline restores that table from the PDF while
ignoring information-equivalent token fusion such as `New York`/`NewYork`.
Large tables are row-packed under the embedding limit with their header row
repeated in each preserved parent chunk. Add `--table-children` to `chunk`,
`full`, or `batch` to derive one retrieval-only child for each data row in an
eligible source table with at least four rows across all of its preserved
fragments:

```bash
python rag.py full --pdf Civil_procedure.pdf --table-children
```

Every fragment of an eligible source table receives children, including a
fragment that is itself shorter than four rows. Each child repeats that
fragment's table caption, header, separator, and exactly one source row. The
parent text and durable ID stay unchanged; even identical source rows receive
distinct stable IDs and citations through their parent ID plus ordinal.
When row packing produces two byte-identical fragments, deterministic fragment
occurrence metadata preserves both through deduplication; occurrence zero keeps
the original parent ID and only later occurrences extend their identity.
Children inherit exact source/page/section provenance but have no ordinary
previous/next context links. Search overfetches when the manifest records
children, suppresses a parent only when a child from the same table was also
retrieved, and retains distinct sibling rows. Markdown/plaintext/flashcard
exports, question and brief generation, citation graphs, and RAPTOR trees use
only canonical records, so enabling the flag cannot duplicate published or
study material. Malformed tables fail closed and receive no children; bounded
per-table and per-corpus caps prevent record explosion. Quality attestation
also requires fragments from one source table to share an exact Markdown
schema and compares their aggregate row/column dimensions with the bound
Docling source matrix when native dimensions are available. PDF-recovered
tables are exempt from a defective native matrix; their replacement remains
bound to the hash-verified recovery PDF and conversion manifest.

## TOC-Based Hierarchy Detection

The pipeline extracts authoritative document structure from the Table of Contents
rather than relying solely on heading detection. Structure policy is selected
explicitly with `--structure-profile`; the default is
`us-law-casebook-v1`, and `roman-parts-book-v1` supports scholarly books whose
primary divisions are Roman-numbered Parts. A profile controls front/back-matter
labels, division patterns, TOC hierarchy, canonical titles, cross-references,
classification, quality checks, and export paths as one immutable policy.

The pipeline supports two TOC extraction methods:

1. **Column-position parsing**: Scans first 25 pages for TOC tables, maps column
   positions to heading depth (col 0 = chapter, col 1 = section, col 2 = sub).
2. **LLM-assisted parsing** (opt-in with `--llm-scaffold`): Sends TOC text to the
   configured provider in ~100-line batches for structured extraction and
   validation of levels, titles, and page numbers.

This produces section paths like `Chapter 3 > B. Federalism > 2. Specific Jurisdiction`
instead of flat `B` or `III`. In testing, TOC detection raised multi-level
section paths from 0% to 84%.

```bash
# Arabic-numbered U.S. casebooks (the default)
python rag.py chunk --doc output/Casebook/Casebook.json \
  --out output/Casebook/Casebook_chunks.jsonl \
  --structure-profile us-law-casebook-v1

# Roman-numbered Part-based scholarly books
python rag.py full --pdf Scholarly_book.pdf \
  --structure-profile roman-parts-book-v1
```

Profile selection is intentionally not guessed from document text. An unknown
profile is rejected by the CLI, and a selected profile that recognizes no
primary divisions fails before chunk publication. `batch` applies its one
explicit profile to every PDF, so group documents by layout family rather than
mixing publisher structures in one command. Profiles are reviewed code, not
arbitrary runtime JSON, and concurrent runs do not share mutable profile state.

## Scaffold-to-Markdown CLI

`scaffold_to_markdown.py` applies an existing JSON TOC scaffold to its source
PDF. Its argparse interface has two mutually exclusive modes:

```bash
# One book: both positional paths are required; -o/--out is an output file
python scaffold_to_markdown.py Book_scaffold.json Book.pdf -o Book.md

# Batch discovery: no positional paths; -o/--out is an output directory
python scaffold_to_markdown.py --all --root path/to/workspace \
  --out path/to/markdown
```

Without `-o`, each output is written beside its scaffold (using the scaffold's
source filename when available). `--all` recursively discovers
`*_scaffold.json` files beneath `--root` (the current directory by default),
prefers a case-insensitive exact PDF stem match, and falls back to a prefix
match. `--root` is valid only with `--all`, and `--all` cannot be combined with
the positional scaffold or PDF paths.

## Resume Capability

The `--resume` flag on `full` and `batch` commands skips completed stages after
validating their artifacts as follows:

| Stage | Checks for |
|-------|-----------|
| Convert | Schema-v2 immutable original/effective PDF binding, config/model lock, and exact JSON/Markdown/derived-PDF output hashes |
| Chunk | Schema-v3 exact Docling/conversion/recovery inputs, immutable structure-profile receipt, output hash, and strict JSONL schema |
| Quality | Schema-v4 chunk-input provenance plus exact Docling/chunks/parameters/retrieval-linkage/table-family binding and every required PASS check |
| Index | Clean schema-v8 manifest plus schema-v4 report binding, physical IDs/count, row-child count, and chunk hashes |
| Export | Source/config completion and output hash |
| Chapter export | Exact manifested chapter-file set and hashes |
| RAPTOR | Source/config-bound tree schema and statistics |

```bash
# Start a long pipeline
python rag.py full --pdf big_book.pdf --llm-classify --contextualize --raptor

# Interrupted? Resume where it left off
python rag.py full --pdf big_book.pdf --llm-classify --contextualize --raptor --resume

# Batch mode resumes per PDF
python rag.py batch *.pdf --resume
```

On failure, the pipeline prints a ready-to-paste resume command.

Conversion schema-v1 and chunk schema-v1/v2 completion files remain readable as
migration inputs but are never accepted as verified resume evidence. Schema-v1
or schema-v2 quality reports and pre-v6 index bindings are not accepted. A
schema-v3 quality report remains read-compatible only after deterministic
in-memory validation proves that its corpus contains no row children. A corpus
carrying quality evidence, source lineage, or any table-family metadata requires
a current schema-v4 report for indexing. Query-only compatibility accepts a
schema-v6/schema-v2 index with neighbor context off and a schema-v7/schema-v3
index with context on or off. Current indexing writes schema-v8/schema-v4
evidence. Migrate the whole artifact chain in order, using the same processing
flags, embedding model, and explicit structure profile as the original run:

```bash
# Rebuild conversion, chunks, and quality evidence when needed, then reconcile
# the collection and commit its new quality-report binding.
python rag.py full --pdf Book.pdf --resume \
  --structure-profile us-law-casebook-v1

# Optional conservative variant: replace the named vector collection outright.
python rag.py full --pdf Book.pdf --resume --full-reindex \
  --structure-profile us-law-casebook-v1
```

Do not query or export the old collection until this command finishes. Resume
keeps valid schema-v2 conversion evidence, rebuilds invalid or pre-v3 chunk
evidence under schema v3 and chunking policy v22, regenerates the schema-v4
quality report from the exact chunk-completion inputs, and then reconciles or
rebuilds an index whose
prior quality binding is incompatible. The chunk receipt records the selected
profile name, revision, schema, and canonical policy SHA-256 plus a
credential-free composite binding between that receipt and the complete
parameter digest. A changed, detached, or tampered policy forces re-chunking
instead of silently reusing different structure semantics. Deleting or
hand-editing only one manifest cannot migrate the chain and fails closed. Keep
the profile and any LLM/classification/context flags from the original command;
changing them intentionally creates a new parameter-bound generation.

Conversion streams one opened PDF generation into a private,
access-restricted scratch pathname, gives only that pathname to preprocessing
and Docling, and verifies both the staged copy and live source before committing
its manifest. Chunking captures one exact Docling JSON generation and, when PDF
table recovery is used, records the hash-verified PDF and conversion-manifest
identities in both the chunk and quality manifests. Standalone recovery should
name the source explicitly:

```bash
python rag.py chunk --doc output/Book/Book.json \
  --out output/Book/Book_chunks.jsonl --source-pdf Book.pdf \
  --structure-profile us-law-casebook-v1
```

`full` and `batch` pass their exact source PDF automatically. Snapshot copies
require free scratch space equal to the PDF size plus a 64 MiB margin. Normal
exit removes them immediately; startup and the storage command conservatively
remove only old, marker-owned trees whose exact process generation is no longer
alive. Cleanup pins the owned root and run directory, refuses device-boundary
crossings, nested/link-like entries, and multiply linked files, and preserves
the ownership marker whenever removal cannot be completed safely.

## Question Extraction

Extract individual Q&A pairs from "Notes and Questions" sections. Each question
is paired with the preceding chunk as context (usually the case it follows).

```bash
python rag.py extract-questions \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  -o output/Civil_procedure/Civil_procedure_questions.jsonl
```

Output (`output/Civil_procedure/Civil_procedure_questions.jsonl`):
```json
{
  "question": "Does Hanna overrule Byrd?",
  "context_chunk_index": 41,
  "context_text": "In Byrd v. Blue Ridge...",
  "context_case": "Byrd v. Blue Ridge Rural Electrical Cooperative, Inc",
  "chapter_num": 3,
  "section_path": "Chapter 3 > Erie Doctrine"
}
```

## Citation Graph

Parse case citations, statute references, and chapter cross-references from
all chunks into a structured graph.

```bash
python rag.py citations \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  -o output/Civil_procedure/Civil_procedure_citations.json
```

Output (`output/Civil_procedure/Civil_procedure_citations.json`):
```json
{
  "nodes": [
    {"id": "chunk_42", "type": "chunk", "label": "Chapter 3 > Minimum Contacts"},
    {"id": "international_shoe_co__v__washington", "type": "case", "label": "International Shoe Co. v. Washington", "year": "1945"},
    {"id": "28_u_s_c__1332", "type": "statute", "label": "28 U.S.C. 1332"}
  ],
  "edges": [
    {"source": "chunk_42", "target": "international_shoe_co__v__washington", "type": "cites_case"},
    {"source": "chunk_42", "target": "28_u_s_c__1332", "type": "cites_statute"}
  ],
  "stats": {"chunk_nodes": 1093, "case_nodes": 1446, "statute_nodes": 287, "total_edges": 2788}
}
```

## Chunk Metadata Schema

Each enriched chunk carries:

| Field | Type | Description |
|-------|------|-------------|
| `content_type` | str | `case_opinion`, `notes_and_questions`, `author_narrative`, `statutory_excerpt`, `table`, `footnote`, `chapter_introduction`, `structural` |
| `section_path` | str | Heading breadcrumb: `Chapter 3 > B. Federalism > 2. Specific Jurisdiction` |
| `chapter_num` | int/null | Chapter number (propagated forward through chunks) |
| `chapter_title` | str/null | Chapter title |
| `case_names` | list[str] | All case names detected in the chunk |
| `primary_case` | str/null | First case name |
| `page_range` | str | `pp.101-106` (from Docling provenance or estimated) |
| `cross_references` | list[str] | Chapter cross-refs: `Ch.12`, `Ch.12.F.2` |
| `context` | str | LLM-generated context prefix (with `--contextualize`) |
| `headings` | list[str] | Full heading hierarchy from Docling layout model |
| `quality_score` | int/null | LLM-rated usefulness 1-5 (with `--quality-score`) |
| `token_count` | int | Token count of the raw chunk before any contextual prefix |
| `embedding_token_count` | int | Token count of the exact contextualized, task-prefixed model input, including special tokens |
| `source_lineage_schema_version` | int | Version of the exact source-item lineage contract |
| `source_items` | list[object] | Deterministic Docling refs with labels, parent refs, pages, and optional bounding boxes |
| `retrieval_linkage_schema_version` | int | Version of the stable context-link contract |
| `stable_id` | str | Intrinsic `chunk_<digest>` identity; independent of output position and derived linkage |
| `context_parent_id` | str | Deterministic source/chapter context group; empty when no explicit chapter is safe to link |
| `previous_stable_id` | str | Immediate prior chunk in final published order within the same context parent, or empty |
| `next_stable_id` | str | Immediate following chunk in final published order within the same context parent, or empty |
| `table_retrieval_schema_version` | int | Version of the optional table parent/row-child contract |
| `retrieval_role` | str | `table_parent` or retrieval-only `table_child` when row expansion is enabled |
| `table_parent_stable_id` | str | Stable ID of the preserved whole-table parent |
| `table_fragment_occurrence` | int | Zero-based disambiguator present only when row packing yields otherwise identical source-table fragments |
| `table_child_index` | int | Zero-based source-row ordinal; present only on a row child |
| `table_child_count` | int | Exact number of row children attested for this table family |
| `table_source_row_count` | int | Exact row count across all preserved fragments sharing one source-table lineage |
| `table_source_fragment_count` | int | Exact number of preserved fragments sharing one source-table lineage |
| `chunk_index` | int | Positional index in output |

### Content Types

| Type | Detection | Example |
|------|-----------|---------|
| `case_opinion` | Judge names, procedural terms, holdings | *International Shoe Co. v. Washington* |
| `notes_and_questions` | "Notes and Questions" headings, numbered prompts | Discussion questions after cases |
| `author_narrative` | Default for expository text | Professor's analysis and commentary |
| `statutory_excerpt` | U.S.C., Rule, statute markers | Fed. R. Civ. P. 12(b)(6) text |
| `table` | Pipe/tab-delimited lines | Jurisdiction comparison charts |
| `footnote` | Numbered refs + citation density (Id., supra) | Case footnotes |
| `chapter_introduction` | Chapter heading + "Introduction" | Opening overview |
| `structural` | TOC, index, title pages | Filtered out automatically |

## Evaluation Harness

Measure retrieval quality across different configurations. Legacy query files
using `expected_keywords` remain supported. For reproducible information
retrieval metrics, use explicit finite judgments keyed by stable chunk or
source IDs stored in search results:

```json
{"query_id":"pj-1","query":"minimum contacts test","judgments":[{"chunk_id":"chunk_0123456789abcdef","relevance":3},{"chunk_id":"chunk_fedcba9876543210","relevance":1}],"expected_type":"case_opinion","corpus":{"sha256":"e5022230c3b28dc4fe547bf5e0d4ce88516f3f15fde7954964d76d4b530e86bd","record_count":728}}
```

Each judgment must contain exactly one `chunk_id` or `source_id` and a finite
`relevance` value from 0 through 100. Use one ID type consistently within a query.
Zero means not relevant; larger values express stronger relevance. A judged
query must contain at least one positive judgment.
`stable_id` and `source_file` returned by existing indexes are accepted as
compatibility aliases when matching results.

Judged sets may also declare subject/book labels, repeatable slice tags, and
query-specific metadata filters. An abstention case intentionally has no
positive judgment and succeeds only when the retriever returns no evidence:

```json
{"query_id":"property-filter","query":"elements of adverse possession","subject":"Property","book":"Property Mini Corpus","tags":["filter"],"filters":{"content_type":"doctrine","chapter_num":2},"judgments":[{"chunk_id":"chunk_c0efee95e1e2bb5b","relevance":3}],"corpus":{"sha256":"606ea787c06e32b8b0a8b0e31a96900c5e2996839e8ed384cda291e0693776a8","record_count":11}}
{"query_id":"property-abstain","query":"unsupported lithium royalty percentage","subject":"Property","book":"Property Mini Corpus","tags":["abstention","adversarial"],"expected_abstain":true,"corpus":{"sha256":"606ea787c06e32b8b0a8b0e31a96900c5e2996839e8ed384cda291e0693776a8","record_count":11}}
```

Allowed filters are `content_type` and `chapter_num`. Reports aggregate tagged,
subject, book, and difficulty slices under metric names such as
`slice/tag/citation/success@1`. Filter cases additionally report
`filter_compliance`; abstention cases report `abstention_accuracy` and
`false_answer_rate`.

Optional `grounding_case` fixtures pass an authored candidate answer through the
deterministic citation/quotation policy without calling an LLM. Their
`grounding_accuracy` measures policy-fixture behavior only—not model answer
quality. Legacy fixtures cover an unknown source citation. Schema-v2 fixtures
add ordered, human-reviewed claim labels: each non-empty answer line is one
explicit `answer_unit`, and `entailed_by` names the exhaustive set of stable
chunk IDs allowed to support it plus an anchor that must occur in the
model-visible excerpt. An empty `entailed_by` labels an unsupported claim.
These labels are corpus-pinned and must declare `review_status` as either
`approved` or `draft_requires_corpus_owner`. Any CLI run containing a schema-v2
case requires every query to declare the exact corpus SHA-256 and record count,
including exploratory runs against a live index.

```json
{"schema_version":2,"case_type":"supported_claim","answer":"A later purchaser must take without notice and record first [S1].","expected_abstained":false,"claim_judgments":[{"claim_id":"race-notice-elements","answer_unit":"A later purchaser must take without notice and record first [S1].","entailed_by":[{"source_id":"chunk_0b7678dcd47185e0","excerpt_contains":"taking without notice of the earlier interest and recording before the earlier claimant"}]}]}
```

The evaluator resolves query-local `S#` citations back to stable IDs and emits
micro-averaged `claim_citation_entailment_accuracy`,
`unsupported_claim_rate`, and `answer_abstention_accuracy`. It reports exact
claim/case denominators globally and per slice, rather than averaging cases
that contain different numbers of claims. `grounding_accuracy` remains the
backward-compatible case composite, so a perfect value now requires the
structural citation checks, every supported claim, no exposed unsupported
claim, the expected answer-abstention behavior, and any prompt-envelope check
to pass.

The checked-in CC0 suites include accepted supported answers as well as
withheld uncited, unknown-citation, and unsupported-quotation answers; an
always-abstaining implementation therefore cannot pass. Each subject also has
a source containing delimiter, role, question, sentinel, and fake-citation
strings. `prompt_injection_fixture_accuracy` reconstructs the answer prompt,
parses every exact one-line JSON source envelope, confirms the declared marker
never escaped its untrusted payload, and also requires the authored answer to
meet its expected abstention and claim labels. This is a serialization and
policy fixture, not evidence that a live model resists prompt injection or a
general semantic-entailment detector. Runtime quotation validation additionally
requires a quote to occur in evidence cited by that quote's own paragraph.
Source JSON escapes newline, next-line, and Unicode paragraph/line-separator
characters so adversarial source text cannot create a second envelope line.

Every ordinary slice metric is accompanied by its `/num_queries` denominator
plus a slice-level `total_queries`. Claim metrics additionally include
`/num_claims`, while answer and prompt metrics include `/num_cases`, so a mixed
slice cannot imply that every metric used every case.

```bash
# Single config with custom cutoffs and a detailed JSON report
python eval.py \
  --queries my_judged_queries.jsonl \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  --db output/Civil_procedure/Civil_procedure_chroma \
  --collection civil_procedure \
  --k 1 5 10 \
  --depth 100 \
  --json-report output/eval/current.json

# Preserve primary ranking metrics while serializing adjacent evidence
python eval.py \
  --queries my_judged_queries.jsonl \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  --db output/Civil_procedure/Civil_procedure_chroma \
  --collection civil_procedure \
  --context-window 1 \
  --context-max-characters 8000 \
  --context-segment-characters 1600

# Compare 4 configs side-by-side
python eval.py --compare \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  --db output/Civil_procedure/Civil_procedure_chroma \
  --collection civil_procedure

# Deterministic, network-free CI/reference suite (no vector DB or model)
python eval.py \
  --retriever bm25 \
  --queries evaluation/suites/property/queries.jsonl \
  --chunks evaluation/suites/property/chunks.jsonl \
  --k 1 3 5 --depth 10 \
  --json-report evaluation-reports/property.json \
  --baseline-report evaluation/baselines/property-bm25.json \
  --fail-under ndcg@3=0.95 \
  --fail-under abstention_accuracy=1 \
  --fail-under claim_citation_entailment_accuracy=1 \
  --fail-under answer_abstention_accuracy=1 \
  --fail-under prompt_injection_fixture_accuracy=1 \
  --fail-over false_answer_rate=0 \
  --fail-over unsupported_claim_rate=0 \
  --max-regression ndcg@3=0
```

`--retriever index` remains the default and exercises the real Chroma/Qdrant,
embedding, hybrid, and reranker path. `--retriever bm25` is deliberately a
lower-fidelity lexical adapter for deterministic, no-download regression tests;
its scores must not be presented as dense-retrieval quality. The checked-in
CC0 Property and Constitutional Law mini corpora are controlled calibration
fixtures, not substitutes for expert review of a full private textbook.
The index adapter also accepts the three context flags shown above, records
them in the report, and serializes supplementary segments without changing the
primary result list used for ranking metrics. Offline BM25 rejects nonzero
neighbor context because it has no manifested adjacency contract.
For explicit no-evidence behavior, the adapter removes a pinned stop-word set
and requires two distinct content-term matches for queries containing more than
two content terms; its implementation version and stop-word digest are recorded
in every baseline snapshot.

All query schemas report Success@k, MRR, and optional top-result type accuracy.
Queries with explicit judgments additionally report Recall@k, nDCG@k, and MAP;
those judged metrics are averaged only across judged queries. Detailed JSON
reports contain a schema version, retrieval configuration, aggregate metrics,
and per-query ranked-result identities and relevance matches. Schema v5 also
binds grounding-scorer version 2 and records the claim-level metrics above.
It retains schema v4's per-query retrieval latency plus p50/p95/max summaries,
sampled process RSS, Python `tracemalloc` peak, and bounded index/storage byte
counts. RSS is sampled
rather than a continuous peak, and `tracemalloc` excludes native allocations;
the report records both measurement methods.

Safety rates are serialized without three-decimal rounding so a single unsafe
answer in a large suite cannot become `0.0` (or a falsely perfect `1.0`) before
`--fail-under` or `--fail-over` is applied. Ranking metrics retain their compact
three-decimal report format. For schema-v2 suites, a
`--fail-under grounding_accuracy=1` release gate automatically expands to the
applicable named claim-entailment, unsupported-claim, answer-abstention, and
prompt-injection gates. Missing component metrics therefore fail closed even if
an older CI command names only the backward-compatible composite.

Summary detail is the default: query text, source previews, raw stable/source
IDs, local manifest paths, and storage paths are omitted or hashed so CI
artifacts do not publish private corpus text. Use `--report-detail full` only
for a deliberately protected local report when raw query text and 200-character
source previews are required for diagnosis.

Embedding query-token use is labeled as a characters/4 estimate. Monetary
values are never based on a hard-coded price table and remain `null` unless the
operator supplies the rate that applies to the run. A prompt-free aggregate
LLM runtime report can be included with its exact/estimated usage provenance:

```bash
python eval.py ... \
  --embedding-cost-per-million-tokens 0.10 \
  --llm-report output/llm-runtime-report.json \
  --llm-input-cost-per-million-tokens 1.00 \
  --llm-output-cost-per-million-tokens 4.00
```

The example numbers are placeholders, not current provider prices. Record the
provider/model, source, and effective date alongside a production run. The
report binds the LLM usage input by SHA-256 and labels every projected amount as
caller-supplied.

`--depth` controls the fixed retrieval depth used for MRR and MAP and must be at
least the largest `--k`. The included `eval_queries_judged.jsonl` contains 24
manually reviewed Civil Procedure queries pinned to the declared 728-record
chunks snapshot. Before scoring a pinned set, the evaluator verifies the chunks
SHA-256 and record count, requires a compatible collection-scoped manifest,
checks the manifest's exact stable-ID set and source fingerprint, and confirms
the physical vector count. A partial or stale index fails before metrics are
produced.

The following depth-20 calibration is historical (it predates index-manifest
schema 5 and model-artifact-lock provenance) and should be reproduced after the
private Civil Procedure index is rebuilt before it is used as a release gate:

| Configuration | MRR | Recall@10 | nDCG@10 | MAP |
|---------------|----:|----------:|--------:|----:|
| Vector only | 0.736 | 0.729 | 0.639 | 0.581 |
| Vector + BGE reranker | 0.773 | 0.833 | 0.711 | 0.593 |
| Calibrated hybrid | **0.822** | **0.882** | **0.768** | **0.670** |
| Calibrated hybrid + BGE | 0.751 | 0.875 | 0.706 | 0.595 |

These figures informed the Chroma defaults (`dense=0.5`, `lexical=1.0`,
`rrf-k=10`) and adaptive reranking policy for this corpus; use a separate judged
set before treating them as universal. The old machine-readable run is local
under ignored `output/` storage and is intentionally not a committed baseline.

`eval_queries_ethics_draft.jsonl` is a 14-query, 24-judgment calibration draft
pinned to the exact 1,715-record `Ethics_3` snapshot. Its slices distinguish
Rule 1.5(c)'s page-542 disclosure rule from the page-543 numerical calculation,
grade an irrelevant section-outline distractor, and cover rule tables, author
explanations, cases, cross-page chunks, metadata filters, abstention, and direct
queries for which outlines are relevant. Every row is marked
`draft_requires_corpus_owner`; do not use it as a release gate or committed
baseline until a corpus owner reviews the queries, stable IDs, and grades. The
evaluator enforces that distinction: `--fail-under`, `--fail-over`, and
baseline-regression checks reject any explicitly draft query.

`evaluation_review.py` makes the owner boundary explicit instead of relying on
an unaudited JSON edit. First, re-pin a still-draft set after a regenerated
corpus proves that every judged stable ID survives. Then produce a protected
full-detail comparison and combine its top candidates with the exact judged
passages in an owner-only packet:

```bash
python evaluation_review.py rebind \
  --queries eval_queries_ethics_draft.jsonl \
  --chunks output/Ethics_3/Ethics_3_chunks.jsonl \
  --declared-chunks-path output/Ethics_3/Ethics_3_chunks.jsonl \
  --out evaluation-reports/ethics-draft-current.jsonl

python eval.py \
  --queries evaluation-reports/ethics-draft-current.jsonl \
  --chunks output/Ethics_3/Ethics_3_chunks.jsonl \
  --db output/Ethics_3/Ethics_3_chroma \
  --collection ethics_3 --compare --k 1 3 5 10 --depth 20 \
  --report-detail full \
  --json-report evaluation-reports/ethics-draft-current.full.json

python evaluation_review.py prepare \
  --queries evaluation-reports/ethics-draft-current.jsonl \
  --chunks output/Ethics_3/Ethics_3_chunks.jsonl \
  --diagnostic-report evaluation-reports/ethics-draft-current.full.json \
  --candidate-depth 10 \
  --out evaluation-reports/ethics-owner-review.packet.json
```

The packet contains private query and evidence text and is published with the
same owner-only, link-aware storage policy as pipeline artifacts. Edit only each
`query_decision` and `judgment_reviews[].decision`, using `approve` or `reject`.
Approving a query also attests that its displayed unjudged retrieval candidates
were checked for missing evidence; an abstention approval attests that the
negative proposition was independently checked against the pinned corpus. A
rejection requires revising the draft and preparing a new packet.

Once every decision is `approve`, the corpus owner—not an automated agent—can
promote the set and issue a content-free receipt:

```bash
python evaluation_review.py finalize \
  --packet evaluation-reports/ethics-owner-review.packet.json \
  --queries evaluation-reports/ethics-draft-current.jsonl \
  --chunks output/Ethics_3/Ethics_3_chunks.jsonl \
  --diagnostic-report evaluation-reports/ethics-draft-current.full.json \
  --candidate-depth 10 \
  --approved-queries-out evaluation-reports/ethics-v1.jsonl \
  --receipt-out evaluation-reports/ethics-v1.review.json \
  --reviewer-id "CORPUS OWNER LABEL" \
  --reviewed-at 2026-07-24T18:30:00Z \
  --attestation "I reviewed every query and judgment against the pinned corpus"
```

The approved set is bound to the receipt by its exact bytes and a review batch
digest. The receipt stores only hashes, counts, coverage tags, and the review
time; it does not contain query text, corpus text, stable IDs, paths, or the
reviewer label. This is a provenance attestation, not an identity signature, so
a durable human PR review is still required. Receipt-bound queries cannot use
release thresholds without `--review-receipt`.

After review, create one approved schema-v1 release policy. The policy binds the
query set, corpus, review receipt, model-artifact lock, retrieval parameters,
and explicit Success@3, Recall@10, nDCG@10, MAP, abstention, filter, and
false-answer thresholds for all four modes. It deliberately owns those CLI
settings, so ambiguous manual overrides are rejected. Run each mode separately
to retain a schema-v5 single-run gate report:

```bash
python eval.py \
  --release-policy evaluation/policies/ethics-v1.json \
  --policy-mode vector \
  --review-receipt evaluation/reviews/ethics-v1.review.json \
  --queries evaluation/suites/ethics/queries.jsonl \
  --chunks output/Ethics_3/Ethics_3_chunks.jsonl \
  --db output/Ethics_3/Ethics_3_chroma --collection ethics_3 \
  --json-report evaluation-reports/ethics-v1-vector.json
```

Repeat with `vector_reranked`, `hybrid`, and `hybrid_reranked`. Do not create an
approved policy or choose final floors from draft judgments; derive them only
after the final owner-reviewed labels are fixed.

```bash
python eval.py \
  --queries eval_queries_ethics_draft.jsonl \
  --chunks output/Ethics_3/Ethics_3_chunks.jsonl \
  --db output/Ethics_3/Ethics_3_chroma \
  --collection ethics_3 \
  --compare --k 1 3 5 10 --depth 20 \
  --json-report evaluation-reports/ethics-draft-compare.json
```

The current schema-v5 clean-room diagnostic, against corpus SHA-256
`a56f145f09a6c97efac1ad622e478a735b9f23fb1d48d4807ae89edd4fd7a790`,
produced the following non-gating results. The
page-542 rule-specific query ranked its table first; the calculation query
retrieved all six graded page-542/page-543 chunks in the hybrid top six. Plain
hybrid's remaining misses were broad outline intents, which is evidence against
a blanket outline penalty.

| Draft configuration | Success@3 | nDCG@10 | MAP |
|---|---:|---:|---:|
| Vector only | 0.923 | 0.872 | 0.836 |
| Vector + BGE reranker | 1.000 | 0.986 | 0.977 |
| Hybrid | 0.923 | 0.874 | 0.862 |
| Hybrid + BGE reranker | 1.000 | 0.986 | 0.977 |

Use repeatable thresholds to make a single-configuration evaluation fail with
exit code 2 when quality is below a required floor:

```bash
python eval.py \
  --queries my_judged_queries.jsonl \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  --db output/Civil_procedure/Civil_procedure_chroma \
  --collection civil_procedure \
  --json-report output/eval/current.json \
  --fail-under recall@10=0.80 \
  --fail-under ndcg@10=0.70
```

Compare a run against a previous JSON report with repeatable regression limits:

Baseline regression requires every query to declare its exact corpus SHA-256
and record count, plus a compatible manifested index for `--retriever index`.
An unpinned starter set can use absolute `--fail-under`/`--fail-over` gates but
cannot be compared to a portable baseline.

```bash
python eval.py \
  --queries my_judged_queries.jsonl \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  --db output/Civil_procedure/Civil_procedure_chroma \
  --collection civil_procedure \
  --json-report output/eval/current.json \
  --baseline-report output/eval/baseline.json \
  --max-regression mrr=0.02 \
  --max-regression ndcg@10=0.03
```

Threshold and regression checks apply to single-configuration runs, not
`--compare`. `--fail-under` gates quality/safety floors, while `--fail-over`
gates upper bounds such as `false_answer_rate`. Baseline comparisons bind the
query digest, portable index snapshot fields, retrieval settings, and model
lock where applicable; absolute local manifest paths are intentionally excluded
from compatibility checks.

CI runs both checked-in offline suites, enforces absolute and zero-tolerance
baseline gates, and retains the redacted schema-v5 JSON reports for 30 days as
the `offline-retrieval-evaluation` artifact. The repository's
`eval_queries.jsonl` remains a ten-query keyword starter set, while
`eval_queries_judged.jsonl` is the 24-query private Civil Procedure calibration.
Create and expert-review a separate stable-ID set before calibrating any full
book; the controlled mini corpora only validate the evaluation machinery and
known adversarial cases.

## Web UI

Gradio web interface with Search, Export, Info, and local-only Jobs tabs.

```bash
pip install -r requirements-optional.txt
python ui.py \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  --db output/Civil_procedure/Civil_procedure_chroma \
  --collection civil_procedure             # http://localhost:7860

# Qdrant run
python ui.py --db-backend qdrant \
  --chunks output/Civil_procedure/Civil_procedure_chunks.jsonl \
  --db output/Civil_procedure/Civil_procedure_qdrant \
  --collection civil_procedure
```

Add `--share` to either complete command to create a public Gradio link. This
can expose private queries, retrieved passages, metadata, and exports to anyone
who obtains the link; do not use it for a sensitive corpus. The Jobs tab is not
registered in shared mode, and its callbacks refuse to touch private job state.
Search retrieval and Info's exact vector count execute in killable workers. Use
`--search-timeout SECONDS`, `--info-timeout SECONDS`, and
`--db-lock-timeout SECONDS` to tune their hard deadlines and local lease wait
independently.

**Search tab**: query box, content type/chapter filters, three-state retrieval
and reranker controls (Auto/forced/disabled), a Neighbor context slider from
zero to two, and formatted primary/neighbor results with source aliases. The UI
uses the default total and per-segment character budgets.

**Export tab**: single-file or split-chapter export with content type filters;
returns one file download or a ZIP archive for split chapters.

**Jobs tab (local only)**: submit a reindex of the configured corpus, poll
redacted state, and request attempt-bound cancel/resume operations. Use
`--job-root` and `--job-ready-timeout` to change its private store and launch
handshake deadline.

**Info tab**: pipeline status, chunk statistics, vector DB info.

## Content Processing

### Preprocessing

- **Text-layer safety check**: `convert`, `full`, and `batch` inspect page text
  coverage and replacement-character quality before deciding whether OCR is
  needed. OCR is enabled automatically for incomplete or low-quality text
  layers.
- **Safe background scan stripping**: Paper Capture PDFs may contain a
  full-page raster behind a usable text layer. Only images >1000px that cover at
  least 70% of a page are candidates, and only pages with reliable text are
  stripped. Mixed scan-only pages retain their pixels. Whenever OCR is enabled,
  preprocessing is skipped so OCR keeps the original source images.
- **Overrides**: `--ocr` forces OCR and `--no-ocr` disables it. Disabling OCR on
  a weak text layer emits a warning. `--no-preprocess` skips background-image
  stripping but does not disable automatic OCR selection.

```bash
# Default: inspect text quality and choose OCR automatically
python rag.py convert --pdf book.pdf

# Explicit overrides
python rag.py convert --pdf scanned_book.pdf --ocr
python rag.py convert --pdf born_digital_book.pdf --no-ocr
```

### Text Cleaning

Applied to all output (markdown export and chunks):

- Non-breaking spaces, smart quotes, em/en dashes normalized
- Ligatures expanded (fi, fl, ff, ffi, ffl)
- Replacement characters removed
- Watermark stripped (configurable regex via `--watermark`, or `""` to disable)
- Whitespace collapsed, paragraph breaks preserved
- UTF-8 output with latin-1 fallback for legacy files

### Deduplication

Trigram Jaccard similarity with a 20% length pre-filter. Default threshold 0.95
(configurable via `--dedup-threshold`). Also removes exact-match lines within a
5-line sliding window.

### Chapter Detection

Extracts chapter boundaries from DoclingDocument page headers using 4 regex
patterns covering multiple textbook formats:

- `Chapter 4  Limits on Personal Jurisdiction`
- `3 . PERSONAL JURISDICTION` (number + separator)
- `CHAPTER FIVE: The Federal Courts` (word numbers one-twenty)
- `Part III - Due Process` (Roman numerals i-xx)

Fallback: scans section headers if page headers are empty.

## GPU Setup

### RTX 5060 / Blackwell

This CUDA profile requires CPython 3.12-3.14 even though the portable CPU
profile supports CPython 3.10-3.14. The committed reproducibility lockfiles are
CPU-only; install the CUDA wheel first and then use the bounded direct
requirements for a GPU environment.

| Component | Minimum | Why |
|-----------|---------|-----|
| NVIDIA driver | 570+ | Blackwell hardware support |
| CUDA | 12.8+ | sm_120 kernel compilation |
| PyTorch | 2.7+ | First stable with sm_120 bins |
| PyTorch index | `cu128` | Must use cu128 wheels |

```bash
pip install "torch>=2.7,<3" --index-url https://download.pytorch.org/whl/cu128
python -c "import torch; print(torch.cuda.is_available(), torch.version.cuda)"
```

VRAM-aware batch sizing is automatic:

| VRAM | Batch size | GPUs |
|------|-----------|------|
| 8 GB | 8 | RTX 5060, RTX 4060 |
| 12 GB | 16 | RTX 5070, RTX 4070 |
| 16 GB | 32 | RTX 5060 Ti 16GB, RTX 4080 |
| 24 GB | 64 | RTX 4090 |
| >24 GB | 128 | RTX 5090, A100 |

Override with `--batch-size`. Falls back to CPU gracefully if no GPU.

## Troubleshooting

### `std::bad_alloc` during conversion

PDF has full-page background scans. The `convert` command auto-detects and
strips them only when a reliable text layer makes removal safe. If it does not
trigger, inspect the PDF first; image-only scans should use OCR rather than
manual image removal:

```bash
python rag.py preprocess --pdf book.pdf --analyze
python rag.py full --pdf book.pdf --ocr --force
```

### GPU OOM during conversion

Reduce batch size:
```bash
python rag.py convert --pdf book.pdf --batch-size 4
python rag.py convert --pdf book.pdf --batch-size 2
```

### Rate limiting (429 errors)

The adaptive throttle handles this automatically. To start more conservatively:
```bash
python rag.py full --pdf book.pdf --llm-classify --llm-workers 4
```

### Missing API key

API-based models validate keys at startup. Set the appropriate env var:
```bash
set VOYAGE_API_KEY=voy-...
set OPENAI_API_KEY=sk-...
set COHERE_API_KEY=...
set GEMINI_API_KEY=...
set MINIMAX_API_KEY=...
set DEEPSEEK_API_KEY=...
set CLOUD_API_KEY=...
```

### Encoding artifacts

The pipeline normalizes encoding automatically. Run the pipeline again to
produce a cleaned, newly numbered book directory; `--force` forces conversion
but does not overwrite or reuse the previous run directory.

### Vector store is busy

Another local process is indexing, querying, evaluating, or inspecting the same
database directory. Let it finish or raise `--db-lock-timeout`; use `0` only
when fail-fast behavior is preferable. A `.rag-locks` sidecar left after a
crash is harmless and should not be deleted—the operating-system lock, not the
file's existence, determines ownership. Always access a live database through
one stable path spelling; do not use drive/UNC/SUBST aliases or rename the
directory while another process may have it open. The timeout applies to lock
contention, not cloud-drive hydration or a storage call that has already begun.

### Operation exceeded its deadline

The supervised Windows Job Object or POSIX worker process group was terminated
with exit status `124`. For an interrupted index/full/batch run, keep the
recovery and artifact-completion metadata in place and rerun the same command
(or `full --resume`); incomplete stages regenerate and the indexer rebuilds the
affected collection before declaring it clean. Increase
`--operation-timeout` for an expected long model or storage operation. Do not
delete `.rag-locks` sidecars or `.updating.json` recovery markers manually.

### Stale index after re-chunking

The incremental indexer uses collection-scoped content hashes and validates the
manifest's schema, embedding model, model-artifact-lock digest, and vector
dimension. Changed chunks are re-embedded automatically; incompatible index
state safely rebuilds only the requested collection. Force an unconditional
rebuild with `--full-reindex` if needed.

## Security Notes

- Built-in local models, Docling layout/TableFormer, and RapidOCR ONNX files are
  commit/version pinned and raw-SHA-256 verified before loading. Remote Python is
  enabled only for the reviewed Nomic and Stella local bundles; the BGE reranker
  explicitly uses `trust_remote_code=False`. Reviewed remote Python is copied
  through a fresh process-private Transformers module cache so stale global
  cache entries cannot shadow the verified source tree.
- Unknown model IDs fail closed unless `RAG_ALLOW_UNPINNED_MODELS=1` is set.
  LegalBERT's pickle weight is inventoried but blocked; prefer safetensors.
- Model-card and package license fields are publisher-declared evidence, not a
  legal attestation. In particular, review LegalBERT's share-alike terms and
  RapidOCR model-data rights before distribution.
- API keys can come from environment variables or the interactive menu's hidden
  prompt; menu-entered keys are redacted from the displayed command, removed
  from child process arguments, scoped to the child environment, and not
  written to a configuration file.
- Direct CLI key flags (`--api-key`, `--cloud-key`, and `--gemini-key`) are
  supported, but their values can be visible in process listings and shell
  history. Prefer environment variables or the interactive menu.
- Input paths remain user-selected and are not a general-purpose sandbox.
  Managed sensitive outputs use the private, link-aware storage policy above.

## File Structure

```
rag.py                  # Stable command/API facade and pipeline orchestration
process_supervision.py  # Stdlib-only process containment and deadlines
retrieval_core.py       # Stdlib-only retrieval models and pure algorithms
table_retrieval_core.py # Stdlib-only table-row generation and family collapse
artifact_io.py          # Stdlib-only strict reads and atomic publication
chunking_core.py        # Stdlib-only text preparation and classification
document_profiles.py    # Immutable reviewed document-layout policy registry
quality_core.py         # Stdlib-only corpus quality reports and bindings
index_state.py          # Stdlib-only index manifests and compatibility policy
vector_lifecycle.py     # Stdlib-only guarded vector mutation and commit policy
llm_adapters.py         # Typed LLM provider transport adapters
llm_runtime.py          # Reproducible caching, fallback, budgets, and reports
cli_policy.py           # Stdlib-only CLI interpretation and serialization policy
ingestion_core.py       # Stdlib-only PDF inspection and stripping safety policy
model_artifacts.py      # Stdlib-only model lock, byte verification, and ML-BOM
operation_contracts.py  # Committed vector-index outcome contract
operational_metrics.py  # Content-free mutation and queue-pressure counters
operational_drills.py   # Hard-kill/synced-publication evidence drills
run_telemetry.py        # Correlated stage events, reports, and recovery
attempt_reporting.py    # Redacted manager-owned job-attempt outcome reports
storage_policy.py       # Owner-only DACL/mode and atomic publication policy
retention.py            # Ownership manifests and dry-run-first lifecycle plans
job_runtime.py          # Durable private job schemas, bindings, transitions, leases
job_manager.py          # Detached supervision, cancellation, and restart recovery
supervised_worker.py    # Gated same-PID bootstrap for pre-execution containment
service_contracts.py    # Dependency-free bounded/redacted local API contracts
service_runtime.py      # Qdrant search isolation and durable service job facade
service_api.py          # Authenticated loopback-only FastAPI/CLI adapter
service-openapi-v1.json # Committed static OpenAPI 3.1 contract snapshot
service-config.example.json # Credential-free private registry template
model-artifact-policy.json # Reviewed models, consumers, files, code, and licenses
model-artifacts.lock.json # Immutable revisions and per-file raw SHA-256 inventory
preprocess_pdf.py       # Standalone PDF preprocessing CLI facade
eval.py                 # Relevance/safety evaluation, gates, and reports
evaluation_review.py    # Private owner-review packets and portable receipts
evaluation_release.py   # Strict four-mode retrieval release-policy contract
evaluation_metrics.py   # Latency, memory, storage, usage, and cost measurements
offline_retrieval.py    # Deterministic no-model BM25 evaluation adapter
evaluation/suites/      # Pinned CC0 Property and Constitutional Law fixtures
evaluation/baselines/   # Portable offline regression baselines
eval_queries.jsonl      # Starter evaluation queries (10 CivPro)
eval_queries_judged.jsonl # Pinned 24-query private CivPro calibration
eval_queries_ethics_draft.jsonl # Pinned Ethics judgments awaiting owner review
ui.py                   # Gradio web UI (Search, Export, Info, local Jobs tabs)
scaffold_to_markdown.py # Apply an existing TOC scaffold to PDF text
requirements.txt        # Direct core dependencies
requirements-optional.txt # Direct optional dependencies
requirements-all.txt    # Aggregate core-plus-optional input
requirements-audit.txt  # Normalized CPU versions for advisory lookup
requirements-test.txt   # Exact CI/test tool pins
requirements-service.txt # Exact narrow local-service direct pins
requirements-smoke.txt  # Lightweight real-vector-store test input
requirements-security.txt # Exact audit/reporting tool pins
requirements-lock-tools.txt # Exact lockfile-generator pin
requirements-*.lock     # Universal exact CPU locks with SHA-256 hashes
dependency-license-policy.json # Denied licenses and reviewed exceptions
dependency-vulnerability-policy.json # Expiring advisory exceptions and audit skips
scripts/                # Repository-local convenience launchers
docs/                   # Maintained ADRs, governance proposals, and archived plans
tools/                  # Source/policy checks, lock refresh, and operational drills
.github/workflows/      # CI, dependency compatibility, and security automation
output/                 # Per-run book directories (auto-created)
```

## Dependencies

### Required (`requirements.txt`)

```
PyMuPDF>=1.24,<2                # PDF preprocessing and scaffold conversion
docling>=2.31,<3                # PDF layout detection and conversion
docling-core[chunking]>=2.70,<3 # HybridChunker and chunking extras
pypdfium2>=4.30,<6              # PDF page counting and conversion backend
sentence-transformers>=3.0,<6   # Local embedding models
einops>=0.7,<1                  # Reviewed Nomic model-code dependency
chromadb>=1.5.2,<2              # Default vector database; deterministic close()
FlagEmbedding>=1.3,<2           # BGE cross-encoder reranker
rank-bm25>=0.2,<0.3             # BM25 keyword search
tqdm>=4.66,<5                   # Progress bars
requests>=2.31,<3               # Cloud embedding and LLM HTTP calls
numpy>=1.26,<3                  # RAPTOR clustering
```

### Optional (`requirements-optional.txt`)

```
qdrant-client>=1.17,<2       # Qdrant vector DB backend
voyageai>=0.2,<1             # Voyage AI embeddings
openai>=1.0,<3               # OpenAI embeddings
cohere>=5.0,<6               # Cohere embeddings and reranking
google-genai>=1.68,<2        # Gemini fallback + timeout/retry controls
gradio>=6.0,<7               # Web UI
```

These are the project's direct declarations; transitive packages are omitted.
Lower bounds preserve the established feature floor; upper bounds cap the
admitted compatibility range. Dependabot proposes bounded updates weekly.

For reproducible CPU installs, use the committed universal lockfiles. They pin
the complete transitive graph, include SHA-256 artifact hashes, and carry Python
and platform markers for the supported CPython 3.10-3.14 range:

```bash
# In an activated virtual environment
pip install --require-hashes -r requirements-lock-tools.lock
uv pip install --torch-backend cpu --require-hashes \
  -r requirements-full.lock
```

Use `requirements-core.lock` instead for the core-only runtime. CUDA users
should follow the GPU setup above; the CPU locks deliberately cannot reproduce
a CUDA environment.

### Development and supply-chain checks

```bash
pip install --require-hashes -r requirements-test.lock
python tools/check_python_sources.py
python tools/check_dependency_policy.py
python tools/check_model_artifacts.py
python -m ruff check .
python -m pytest -q
```

To reproduce the full CPU development environment, install both exact locks:

```bash
pip install --require-hashes -r requirements-lock-tools.lock
uv pip install --torch-backend cpu --require-hashes \
  -r requirements-full.lock -r requirements-test.lock
```

Regenerate locks without changing compatible versions with
`python tools/refresh_locks.py`. Use `python tools/refresh_locks.py --upgrade`
for an intentional dependency refresh, then review and test the lockfile diff.
Dependabot can propose direct-input changes but cannot regenerate these custom
universal locks; refresh and commit the locks on each Dependabot dependency PR.

GitHub Actions runs that dependency-light suite across Python 3.10-3.14 and on
Windows, exercises real local Chroma and Qdrant clients on Linux and Windows,
installs the full locked CPU environment for every source PR, and separately
checks both runtime dependency sets. A scheduled
workflow audits the active Linux/Python 3.12 full development environment with
`pip-audit`, retains its JSON findings, a CycloneDX package SBOM, a CycloneDX
ML-BOM companion, and a dependency-license inventory, and enforces both
dependency policy files. Platform- and
Python-specific inactive branches in the universal locks are resolution-tested
but are not represented in that single-environment SBOM.

The model companion covers selected Hugging Face/Docling runtime files,
reviewed remote code, deterministic derived files, and RapidOCR's wheel-embedded
ONNX payloads as byte-hashed components with dependency edges. Offline schema
validation runs on every CI job; the scheduled security job also resolves each
immutable Hub commit, re-downloads ordinary source files and the pinned
RapidOCR wheel, checks installed package payloads, and retains the report.

```bash
# Fast, offline policy/lock validation
python tools/check_model_artifacts.py

# Reconfirm remote provenance and emit the companion ML-BOM
python tools/check_model_artifacts.py --verify-hub \
  --verify-installed-packages \
  --output-sbom model-artifact-sbom.json

# Intentional update: resolve reviewed mutable refs, hash bytes, then review diff
python tools/refresh_model_artifacts.py
```

As of 2026-07-21, every published ChromaDB 1.x release is affected by
`PYSEC-2026-311`/`CVE-2026-45829`, a critical pre-authentication code-injection
issue in Chroma's HTTP server, and no patched release exists. This pipeline uses
only the embedded, filesystem-local `chromadb.PersistentClient`; it does not
launch that HTTP server or accept remote collection model configuration. A
documented exception in `dependency-vulnerability-policy.json` expires on
2026-08-31 and makes that deployment constraint explicit. Do not expose a
Chroma server from this environment; use Qdrant for networked deployments and
remove the exception as soon as a fixed Chroma release is available.

PyMuPDF is dual-licensed under AGPL-3.0 or a commercial Artifex license. Its
time-bounded policy exception permits only private, filesystem-local evaluation
through 2026-08-31; no commercial basis has been recorded. This repository also
has no repository-wide `LICENSE` file. Distribution or hosted/network use is a
release blocker until the owner selects and records the applicable PyMuPDF and
repository licensing basis.

### PyTorch (install first)

```bash
pip install "torch>=2.7,<3" --index-url https://download.pytorch.org/whl/cu128
```

## Full Pipeline Example

```bash
# Maximum intelligence: all LLM features enabled
python rag.py full --pdf CivPro_Casebook.pdf \
  --structure-profile us-law-casebook-v1 \
  --llm-classify \
  --contextualize \
  --reconstruct-headings \
  --quality-score \
  --llm-scaffold \
  --raptor \
  --db-backend qdrant \
  --embedding-model voyage-law-2 \
  --llm-workers 10

# Then query with answer generation
python rag.py query "minimum contacts test" --answer --hybrid \
  --db-backend qdrant \
  --db output/CivPro_Casebook/CivPro_Casebook_qdrant \
  --chunks output/CivPro_Casebook/CivPro_Casebook_chunks.jsonl \
  --collection civpro_casebook \
  --embedding-model voyage-law-2

# Generate study materials
python rag.py brief \
  --chunks output/CivPro_Casebook/CivPro_Casebook_chunks.jsonl \
  -o output/CivPro_Casebook/CivPro_Casebook_briefs.jsonl
python rag.py generate-questions \
  --chunks output/CivPro_Casebook/CivPro_Casebook_chunks.jsonl \
  -o output/CivPro_Casebook/CivPro_Casebook_exam_questions.jsonl
python rag.py export --format flashcards \
  --chunks output/CivPro_Casebook/CivPro_Casebook_chunks.jsonl \
  -o output/CivPro_Casebook/CivPro_Casebook_flashcards.tsv
```
