# Opt-in source evidence for AI search

Status: local generated integration and frozen-source qualification passed.
This is an additive reader capability, not representative OCR validation,
scan access, or permission to
publish corrections. The broader [OCR improvement program](ocr-improvement-program.md)
remains incomplete.

## Enable explicitly

Keep the existing closed corpus registry and reader/admin credentials. Add a
separate owner-private JSON file listing a nonempty subset of its corpus IDs:

```json
{
  "schema_version": 1,
  "kind": "service_evidence_config",
  "corpora": [
    {
      "corpus_id": "synthetic",
      "docling_path": "generated/docling.json",
      "recovery_path": "generated/recovery.json"
    }
  ]
}
```

These paths are examples, not existing inputs. Relative paths are interpreted
against this configuration file. Both path fields are required but nullable.
Recovery requires explicit Docling configuration. Only the configured paths
and canonical pipeline sidecars are considered; source filenames found inside
document text never authorize opening a PDF. A missing or conflicting configured
artifact fails the request, rather than silently returning weaker evidence.

Append `--evidence-config path/to/evidence-config.json` to the existing
`python service_api.py serve ...` invocation. Without that flag the evidence
route is absent. This does not change the corpus registry schema, credentials,
ordinary search behavior, or the committed `/v1/openapi.json` document.

With the reader token provided through the existing named secret environment
variable, an AI host can send a bounded JSON request through stdin:

```powershell
'{"query":"fictional notice example","limit":5,"mode":"vector"}' |
    python tools/query_pipeline.py search-evidence --corpus synthetic
```

The fixed endpoint is `POST /evidence/v1/corpora/{corpus_id}/search`.
The Python equivalent is `PipelineReader.search_evidence(corpus_id, payload)`.
It accepts the same query, mode, limit and filters as ordinary search. It cannot
request arbitrary paths, crop coordinates, rendering, jobs or administrative
actions. See [reader setup and disclosure rules](ai-pipeline-access.md).

Embedded callers explicitly configure `RagApplicationService` with
`evidence_configs` and a captured `evidence_runner`, then wrap it with
`create_app(..., evidence_enabled=True)`. The lazy production root's
`create_service_application(..., evidence_configs=...)` wires both together.
Custom compositions must provide the complete evidence runner/HTTP installer/
HTTP binding triple; partial capabilities fail before runtime construction.

## Interpret the response accurately

The strict companion contains exactly `schema_version`, `kind`, `search`,
`generation` and `evidence`. `search` is the existing v1 response. Each ordered
evidence entry corresponds to one complete manifested record, matched before
public text/metadata truncation. It includes full-record and text digests,
an opaque generation-bound evidence ID, source scope, related-page OCR state,
and explicit uncertainty. Digests support correlation, not authorization or
attestation of every byte of the vector database.

- `text_accuracy` is always `not_verified`; `review_scope` is always `unmapped`.
  Structural quality and a related recovery candidate do not prove that this
  chunk contains correct or adopted OCR text.
- `record_source_items` identifies the record's scoped source items.
  `table_parent_source_items` identifies inherited parent-table scope, never
  exact geometry for a derived row. No coordinates or pixels are returned.
- `original_pdf` page space requires a verified conversion whose effective
  input was the original PDF. Preprocessed conversion-input page numbers must
  not be joined to original-PDF recovery pages by numerical coincidence.
- A configured, exactly source-bound recovery report may yield related-page
  `review_required`, `empty_candidate`, `retry_failed`, `deferred` or
  `not_selected` states. Those are not chunk-level review or adoption states.
- Legacy records without bound source proof explicitly report unavailable
  provenance only when all canonical proof sidecars are absent. Present quality,
  chunk-completion or source-oracle sidecars require valid bound proof; dangling
  links, malformed leftovers and late-appearing proof cannot bypass validation.
  This strict companion admission rule does not change ordinary legacy search.

The public generation lists captured manifest, chunks and available quality,
completion, oracle, Docling, conversion and recovery digests. The original PDF
digest stays private inside the opaque generation calculation. Paths, original
filenames, raw source items and OCR candidate text are not added to the response.
The returned search excerpt itself can still be private document content.

Exact matching also includes embedding-token metadata. Normal current chunk
publication stores final counts before hashing; indexing recomputes them. If an
older artifact lacks counts or was produced using a different estimator, the
indexed record may legitimately differ from its raw JSONL source. This companion
currently refuses that mismatch rather than ignoring a metadata field or
pretending the records are identical. Ordinary search remains available. An
operator can regenerate approved chunks with the intended verified tokenizer
and republish/reindex explicitly; the reader never changes source artifacts.
Supporting a separately attested indexing derivation remains additional work.

## Boundaries and failure behavior

Reader authentication, literal-loopback host/peer checks, secure headers and
static errors come from the existing app. Evidence and ordinary search share
one capacity limit and shutdown accounting. A separate fixed child action
performs retrieval and evidence capture under database-then-chunk leases; the
host does not import `rag` or model/vector clients. Query/configuration cross
the process boundary in private files, not command arguments. The new worker
success binds the full private request and is independently checked against
request identity, mode, limit and filters.

Public requests remain capped at 64 KiB. The private request envelope is capped
at 256 KiB, companion annotations at 96,000 bytes, the complete public response
at 2,000,000 bytes and transport at the existing 2 MiB. Oversized evidence fails
explicitly; uncertainty is not dropped to fit. Process deadlines, verified
cleanup and bounded client deadlines still apply. There are no hidden retries.

Integration uncovered a pre-existing thread-affinity defect in HTTP lifecycle
startup/shutdown. A dedicated per-lifespan owner thread now acquires/releases
the service lease; cancellation drains owned work before propagating. This is
explicit lifecycle hardening, not a transparent refactor or new authority.

## Verification scope

Focused tests cover strict contracts, full-text/metadata mismatches, every
captured artifact generation, table and multipage scope, original versus
preprocessed page spaces, false OCR-adoption claims, malformed private worker
responses, authentication, capacity, cancellation, unchanged OpenAPI, and cold
host imports. A portable integration uses physical Qdrant with deterministic
synthetic vectors through authenticated HTTP, the reader and a fresh CLI process.
A separate generated native run now passed actual cached-model embeddings,
Qdrant indexing, default production composition, contained search workers,
authenticated HTTP and fresh reader/CLI requests: 12 indexed records and 11
returned hits. The [2026-09-07 evidence record](evidence/2026-09-07-ai-evidence-local-qualification.md)
binds the receipt and distinguishes retained artifact checks from transient
producer observations. Phase 5 passed all nine local gates: 7,952 tests passed,
seven platform-specific cases skipped, and every collected case matched JUnit.
All 445 tracked files stayed unchanged through independent verification.
Neither run measures OCR accuracy on representative books.

The [design and remaining scan/adoption requirements](ai-evidence-access-design.md)
remain authoritative. Separately authorized scan serving, occurrence-level
correction adoption, approved representative inputs and paired answer-quality
evaluation are not delivered by this text-evidence companion.
