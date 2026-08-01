# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> **Branch-status note (2026-07-24):** This checkout includes the unmerged
> draft milestone stack described in `ROADMAP.md`. Treat “Implemented (draft)”
> and “Integrated” as different states, and verify the current branch and PR
> base before assuming a feature is present on `main`.

## What this is

GPU-accelerated pipeline that converts law-school textbook PDFs into LLM-ready markdown, Chroma/Qdrant vector indexes, and study products (case briefs, exam questions, Anki flashcards), with hybrid retrieval and grounded, cited answers. `ROADMAP.md` tracks current milestone status and the ordered backlog; `INTEGRATION_AUDIT.md` records the historical #1-#28 integration findings.

## Commands

```bash
# Test environment (dependency-light, what CI uses on 3.10-3.14)
pip install --require-hashes -r requirements-test.lock

# Full suite (dependency-light subset when optional deps absent)
python -m pytest -q
# Historical plans contain point-in-time test counts; use the observed result,
# not an archived count, when reporting the current branch.

# Single test file / single test
python -m pytest tests/test_retrieval_core.py -q
python -m pytest tests/test_job_manager.py -k "test_name" -q

# Lint (ruff, target py310, E4/E7/E9/F only)
python -m ruff check .

# Policy gates CI runs alongside lint
python tools/check_python_sources.py
python tools/check_dependency_policy.py
python tools/check_model_artifacts.py

# Full CPU dev environment (exact hash-locked, Python 3.12)
pip install --require-hashes -r requirements-lock-tools.lock
uv pip install --torch-backend cpu --require-hashes -r requirements-full.lock -r requirements-test.lock

# Offline retrieval regression (network-free, thresholds + baseline gates)
python eval.py --retriever bm25 --queries evaluation/suites/property/queries.jsonl \
  --chunks evaluation/suites/property/chunks.jsonl --k 1 3 5 --depth 10
python eval.py --retriever bm25 \
  --queries evaluation/suites/constitutional_law/queries.jsonl \
  --chunks evaluation/suites/constitutional_law/chunks.jsonl \
  --k 1 3 5 --depth 10
python eval.py --retriever bm25 \
  --queries evaluation/suites/table_family/queries.jsonl \
  --chunks evaluation/suites/table_family/chunks.jsonl --k 1 3 5 --depth 10

# Regenerate lockfiles (never hand-edit *.lock)
python tools/refresh_locks.py            # same versions
python tools/refresh_locks.py --upgrade  # intentional refresh; review the diff

# Run the pipeline
python rag.py full --pdf path/to/source.pdf   # end-to-end, per-run output dir
python rag.py                                  # interactive menu
```

Pytest config lives in `pyproject.toml`: `testpaths=["tests"]`, `pythonpath=["."]`, asyncio plugin disabled. CI (`.github/workflows/ci.yml`) runs lint + compile gates, the offline evaluation suites, unit tests on Linux 3.10-3.14 and Windows 3.12, service-API tests on both OSes, real Chroma/Qdrant smoke, and a full locked CPU integration job.

## Architecture

### Facade + extracted policy modules

`rag.py` is the large stable command/API compatibility facade plus pipeline
orchestration. Deterministic policy is progressively extracted into typed,
dependency-light modules. Several are deliberately standard-library-only.
`rag.py` injects mutable collaborators (paths, telemetry, atomic writers,
leases, clients, and caches) and preserves established private-name seams for
consumers and tests. A pure extraction should remain behavior-preserving;
intentional hardening or schema migration must be identified and tested as
such rather than described as a transparent move.

Current policy/runtime modules include:

- `retrieval_core` and `table_retrieval_core` — grounding, rank fusion,
  stable adjacency, bounded context assembly, and table-family retrieval.
- `chunking_core`, `document_profiles`, and `quality_core` — deterministic
  chunk policy, immutable attested document layouts, and corpus-quality
  evidence.
- `artifact_io`, `index_state`, `vector_lifecycle`, and `resource_lease` —
  strict artifact I/O, manifest decisions, guarded vector reconciliation,
  verification/commit ordering, and canonical reentrant cross-process path
  leases.
- `cli_policy`, `ingestion_core`, `process_supervision`,
  `runtime_supervision`, and `operation_contracts` — CLI/resume policy,
  PDF-ingestion safety, generic process-tree containment/deadlines, the frozen
  production supervision binding, and committed operation outcomes.
- `attempt_reporting`, `operational_metrics`, `operational_drills`,
  `run_telemetry`, `storage_policy`, and `retention` — durable redacted
  operational evidence and private artifact lifecycle policy.
- `evaluation_inputs`, `evaluation_queries`, `evaluation_contract`, `evaluation_metrics`,
  `evaluation_review`, and `evaluation_release` — strict shared inputs,
  query/corpus/judgment validation, versioned retrieval/grounding semantics,
  metrics, and review-bound evaluation promotion. See the
  [evaluation input-contract ADR](docs/architecture/decisions/evaluation-input-contract.md)
  and the
  [table-family evaluation ADR](docs/architecture/decisions/table-family-evaluation.md).
- `endpoint_policy`, `llm_adapters`, `llm_runtime`, and `model_artifacts` —
  fail-closed URL attestation, provider-neutral LLM transport, and locked
  model-artifact verification. Custom public endpoints require HTTPS; HTTP is
  limited to canonical literal loopback addresses, redirects are refused, and
  endpoint validation precedes credential and cache access.

What deliberately remains inside `rag.py` includes top-level pipeline
orchestration, compatibility wiring, mutable embedding/reranking/BM25 caches,
and the physical Chroma/Qdrant adapters. Vector transaction policy, canonical
path-lease implementation, and process supervision no longer live there,
although their late-bound facade wrappers do.

### Process supervision layering (not duplication)

`process_supervision.py` owns the standard-library-only deadline loop, Windows
job containment, POSIX/Windows start gates, verified termination, and generic
entrypoint routing. `runtime_supervision.py` binds that core to the stable
pipeline script, deadline map, environment/timing policy, cleanup exception,
and telemetry callbacks as one frozen capability. `job_coordination.py`
snapshots that binding per operation and no longer imports `rag.py`;
`job_manager.py` is the compatible import/executable facade, and `rag.py`
retains late-bound wrappers for direct compatibility callers and tests.
`supervised_worker.py` remains the contained child's gate-wait bootstrap.

The first-party import graph is acyclic, but R8 is not complete.
`service_runtime.py` now snapshots a frozen host binding and the isolated
`service_search_worker.py` composes physical retrieval, so the host no longer
imports `rag`. It also snapshots the frozen launch/reconcile/integrity binding
from `job_coordination_contracts.py`; it does not import or transitively load
the `job_manager` shell. `job_application.py` provides one frozen store,
launch, reconciliation, and manager-error generation to the `rag.py jobs`
command and local UI. No production Python consumer imports `job_manager.py`;
detached execution still invokes that stable executable facade. Preserve every
binding's atomic snapshot rule. `service_http.py` now owns the FastAPI adapter
behind a structural runtime port, and dependency-light
`application_composition.py` is the lazy outer root for the production service
role. `service_api.py` remains its compatible token/CLI/import facade. R8 still
requires separating narrow pipeline implementation ownership from `rag.py`
before CLI or UI composition can move to that root. See the
[process-supervision ADR](docs/architecture/decisions/process-supervision-extraction.md),
the [runtime binding ADR](docs/architecture/decisions/runtime-supervision-binding.md),
the [service-host binding ADR](docs/architecture/decisions/service-host-binding.md),
the [service job-coordination ADR](docs/architecture/decisions/service-job-coordination-binding.md),
the [job-application binding ADR](docs/architecture/decisions/job-application-binding.md),
and the [service application-composition ADR](docs/architecture/decisions/service-application-composition.md).

### Evaluation contract layering

`evaluation_inputs.py` owns the strict snapshot, JSON-object, digest, and
corpus-binding helpers. `evaluation_release.py` imports that leaf directly;
`evaluation_review.py` re-exports the former private helper names for
compatibility. Do not route release policy back through the review application
or weaken the tracked-source architecture gate. `evaluation_queries.py` owns
the query schema, corpus pins, judged-ID/table-family validation, and grounding
evidence binding. `eval.py` preserves the former private names as
object-identical aliases and retains the legacy JSONL loader plus raw-byte
digest cache; `evaluation_review.py` calls the query domain directly. Do not
replace the legacy loader with the strict review parser without a separate
input-policy decision. The evaluation graph and the full tracked first-party
graph are acyclic; any new first-party import cycle is a test failure.

### Vector stores and safety invariants

Chroma is the default backend and Qdrant is optional; both do
manifest-validated incremental indexing and return the committed
`IndexOutcome`. `vector_lifecycle.py` owns their shared guarded reconciliation
and publication policy, while `rag.py` retains physical backend operations.
Commands that can open a vector store run in an isolated worker process under
a hard deadline; cross-process access is coordinated by leases
(`VectorStoreBusyError` when busy). Publication is fail-closed and atomic;
retention/deletion is ownership-manifest-based and dry-run-first. Preserve
these invariants in any change — much of the test suite is failure injection
against them.

### Service layer

`service_contracts.py` is the dependency-free v1 contract. Both
`service_runtime.py` (Qdrant search isolation and durable jobs) and
`service_http.py` (authenticated loopback FastAPI adapter) depend inward on
that contract, not on each other. `application_composition.py` lazily binds
them with the frozen host and job-coordination capabilities; `service_api.py`
is the compatible executable/import facade. `service_search_worker.py` remains
the only child composition shell that imports both the runtime contract and
`rag.py`; `job_manager.py` remains the intentional detached-job shell.
`service-openapi-v1.json` is the committed static contract snapshot.

## Conventions

- Milestone status follows `ROADMAP.md`: “Implemented (draft)” requires behavior
  implemented, failure-injected, covered by the full suite and static checks,
  exercised against the relevant real optional client where practical,
  independently reviewed with durable evidence, and published as a mergeable
  draft PR. It becomes “Integrated” only after merge to `main`.
- Refactors of established behavior are strictly behavior-preserving: write characterization tests against current behavior first; log discovered defects in `ROADMAP.md` as follow-ups instead of fixing them in the same change.
- Dependencies are hash-locked. Direct pins live in `requirements*.txt`; the universal `*.lock` files are generated only by `tools/refresh_locks.py`. Model artifacts are pinned in `model-artifact-policy.json` / `model-artifacts.lock.json` and byte-verified before loading; unknown model IDs fail closed.
- Root-level `*.pdf` files are private inputs and are Git-ignored. Treat text
  derived from them as sensitive and use synthetic or pinned CC0 fixtures by
  default. Do not introduce new source excerpts into committed artifacts while
  the owner's policy for corpus-derived evaluation metadata remains unresolved;
  existing artifacts are not precedent for expanding that scope. See the
  [private-source policy proposal](docs/governance/private-source-documentation-policy-proposal.md).
- This working copy lives in a Dropbox-synced folder on Windows; transient sync interference with atomic replace/markers is a known hazard (ROADMAP P0).
