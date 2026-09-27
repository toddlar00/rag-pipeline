# Opt-in service evidence and thread-owned HTTP lifecycle

- Status: local implementation with generated native integration and Phase 5 frozen-source qualification; not a release or R8 ownership move
- Date: 2026-09-06
- Scope: user-requested OCR/AI-access improvement program

## Decision

Add explicitly configured `POST /evidence/v1/corpora/{corpus_id}/search`
alongside, not inside, service v1. Dependency-light `service_evidence_contracts`
imports only `service_contracts` and performs no IO. Public schemas remain
closed. A separate operator configuration is a strict nonempty subset of the
existing corpus registry, with optional explicit Docling/recovery paths. No
document-provided filename grants access.

`service_evidence_search` owns bounded exact artifact capture and source scope
policy. It imports inward policy leaves, never `rag`, and receives one frozen
`EvidenceSearchBinding`. Only `service_search_worker` supplies physical facade
callbacks. The worker holds the database lease before the canonical chunk
leases, validates the manifested record's full text and metadata before public
truncation, and rechecks captured generations before returning. Full record
digests and exact provenance scope are correlation evidence, not OCR correctness
or an attestation of every vector-store byte.

`service_evidence_runtime` imports the existing host runtime's private-file,
deadline and cleanup support. The host runtime imports only the evidence
contract, preventing a transport/runtime cycle. New fixed child action
`_evidence_search_worker` accepts a schema-1 private request wrapping the
unchanged search schema-2 request. Success includes a domain-separated digest
of the complete private request; parent and worker independently validate
corpus/request identity, mode, limit, filters and the closed public companion.
The 256 KiB private request and existing 2 MiB result bounds are explicit.
No private query or source configuration appears in command arguments.

`service_evidence_http` imports only the base/evidence contracts in the
first-party graph. The lazy `application_composition` root injects the existing
reader dependency and bounded JSON reader in one frozen `EvidenceHttpBinding`;
the new adapter does not import `service_http` or construct a parallel app.
Existing middleware, credentials, errors, capacity and lifecycle remain shared.
The optional runner/installer/HTTP-binding triple in
`ServiceApplicationComposition` is complete or absent; existing custom
compositions and omitted factory arguments retain their old behavior.

The runtime captures an immutable evidence allowlist and matching corpus
configs, runner, release policy, temporary root and host binding. Evidence
search shares the v1 semaphore and shutdown accounting. Cold importing the root
stays standard-library-only. Resolving it loads neither `rag`, the physical
evidence implementation, nor vector/model clients.

## Explicit lifecycle hardening

Integration reproduced a pre-existing HTTP lifecycle bug: independent
`asyncio.to_thread(runtime.start)` and `runtime.close` calls can select different
pool threads, but the canonical service instance lease is thread-owned. This
change intentionally fixes that behavior rather than calling it a transparent
extraction. One per-lifespan single-worker executor now owns start and close.
Each call receives its own copied context, retaining context-variable behavior.
Cancellation drains owned work before propagation; a successfully completed
cancelled startup is closed on its owner thread. A failed start retains the
existing contract that the runtime performs its own partial-start rollback.
Ordinary exceptions retain static startup/shutdown text; BaseException identity
and cleanup-failure precedence remain tested. The executor is not global and
is joined only after submitted lifecycle work completes.

## Preserved boundaries and remaining work

The service v1 schema, internal ordinary-search v2 envelope, corpus config v1,
committed OpenAPI bytes, authentication policy, admin authority, recovery
formats and `rag` facade ownership remain unchanged. The new companion cannot
authorize scans, adopt OCR corrections, render files, discover arbitrary paths,
download models or authorize cloud disclosure. Default apps do not mount its
route. Related-page OCR status remains distinct from exact occurrence-level
review/adoption. Preprocessed conversion pages never acquire original-PDF page
authority through equal page numbers.

The [usage contract](../../ai-evidence-access.md) and
[remaining scan/adoption design](../../ai-evidence-access-design.md) describe
the full boundary. Generated portable tests are not representative accuracy
evidence. Native cached-model execution, reviewed inventory and the complete
Phase 5 frozen-source checkpoint passed; the
[2026-09-07 record](../../evidence/2026-09-07-ai-evidence-local-qualification.md)
binds their exact artifacts, controlled test environment and limitations.

## Verification sources

- Contracts/client/CLI: `tests/test_service_evidence_contracts.py`,
  `tests/test_ai_pipeline_client.py`, `tests/test_query_pipeline_cli.py`.
- Exact source policy: `tests/test_service_evidence_search.py` and generated
  `tests/service_evidence_fixtures.py` artifact graph.
- Transport, composition and HTTP: `tests/test_service_evidence_runtime.py`,
  `tests/test_service_evidence_composition.py`, `tests/test_service_evidence_http.py`.
- Real local Qdrant/HTTP/client/CLI with synthetic vectors:
  `tests/test_service_evidence_integration.py`.
- Thread ownership, cancellation and real lease successor acquisition:
  `tests/test_service_http.py`; existing live-service and v1 tests remain gates.
- Exact inward edges, only-child physical composition and cold imports:
  `tests/test_architecture.py` and the separately reviewed inventory candidate.
