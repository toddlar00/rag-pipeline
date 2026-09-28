# OCR evidence companion and pending scan/adoption work

Status: the opt-in text-evidence search path passed generated native integration
and the Phase 5 local frozen-source checkpoint; see
[configuration and honest response semantics](ai-evidence-access.md).
Separately authorized scan serving and exact occurrence-level adoption remain
pending. This document retains the design and acceptance requirements; its
historical pending descriptions below are not qualification claims.
The existing ordinary-search routes, v2 worker envelope and committed v1
OpenAPI remain unchanged. Ordinary search continues to return
retrieved text and relevance scores, not OCR correctness or scan permission.
This design belongs to the ongoing [improvement program](ocr-improvement-program.md).

## Proposed boundary

A separately versioned evidence-search capability will wrap the unchanged v1
search result and attach one ordered evidence entry per returned hit. It needs
its own fixed worker action and strict bounded response contract. No generic
URL, shell, filename, PDF, crop coordinates or administrative action is exposed
to the AI reader.

The proposed external shape distinguishes opaque generation/evidence handles,
exact manifested-record correspondence, `text_accuracy: not_verified`, an
explicit review scope, fixed issue codes and permitted scan-region handles.
Legacy evidence may be unavailable with a fixed reason and null handles.
Conflicting current-generation evidence must fail rather than degrade silently
to unknown. This is a proposal, not a new accepted service schema.

## Exact corpus correspondence

The isolated worker must hold the canonical vector-store lease and then the
existing sorted chunk-artifact leases in writer order: database, then chunks.
Search and evidence construction happen in that same process, where the
existing vector lease is reentrant. The host must not hold a lease that its
child needs to acquire.

Before public truncation, match every complete internal hit to exactly one full
manifested chunk row, including exact text and identity metadata. A short chunk
ID or matching page number alone is insufficient. Ordinary service searches use
`context_window=0`, so the existing context-row check is not sufficient for this
new capability without explicitly integrating it.

Bind exact index-manifest/chunks bytes, validated quality and chunk-completion
records, source-oracle provenance, Docling/conversion generations and full
record/text hashes. Reject a dirty or inconsistent index. Distinguish the hash
domains: index source means chunks JSONL; chunk-completion source means Docling;
OCR recovery source means the original PDF. Pin the evidence before releasing
leases; never infer those joins from similarly named digest fields.

## What uncertainty can honestly say

Structural quality/publication checks do not independently verify OCR against
pixels. An OCR report for the exact original PDF may establish related page
facts, such as an available candidate or a failed retry. A reference declaration
may establish that an operator supplied reviewed text. Neither establishes that
the candidate or correction was adopted into this exact chunk.

Initially such facts must be labeled related-page evidence with unmapped review
scope. Drafts and page checkboxes are not authentication. Chunk-level review
requires an explicit adoption mapping covering exact emitted text occurrences
and source scopes; partial mapping cannot mark the entire chunk reviewed.
Missing persisted confidence stays unknown. Retrieval relevance is never
reinterpreted as OCR confidence or correctness.

## Separate scan capability

Use an offline, bounded, private, create-only builder for pre-rendered scan
regions, with a completion manifest published last. Each registry entry binds
the corpus generation, full record and provenance atom, original PDF digest and
size, conversion/Docling bindings, physical page, original-display rectangle,
renderer policy and PNG digest/size/dimensions.

The serving process exposes only configured immutable registry entries, not
arbitrary rendering or a public static directory. Missing/ambiguous geometry,
multipage ownership, or preprocessing without a verified inverse coordinate
mapping yields no crop; it must not fall back to exposing the whole page.

Scans require both a separate image-scoped credential and an operator grant
binding allowed registry entries, expiry and revocation epoch. Existing reader
or admin credentials do not implicitly authorize scans. Opaque IDs are not
bearer authorization. Recheck authorization at response commitment; disclosed
bytes cannot later be recalled. Reject handles after corpus-generation change.

Retain authenticated literal-loopback transport, static errors, bounded bytes,
no redirects/proxies, and `no-store`/`nosniff` image responses. Exclude filesystem
paths and original filenames. Approval for local retrieval does not authorize
cloud disclosure of either text or images. Retrieved text and scan instructions
remain untrusted document data, never tool authority.

## Required verification before delivery

- Full-text mismatch despite matching short IDs, duplicates and post-match
  truncation; all conflicting generation/quality/source bindings and dirty index.
- Exact table-fragment and multipage source scopes, ambiguous-geometry abstention
  and corrected-page/whole-chunk false-positive controls.
- Missing, failed, deferred and unreviewed OCR states without fabricated success.
- Wrong token/corpus/grant, guessed IDs, expiry/revocation and traversal refusal.
- Registry/PNG mutation, oversized or incomplete artifacts, concurrent reindex
  and revocation, immutable capture and lease-order/process-deadline failures.
- Real generated local-service/client exercise and unchanged service v1 contract.

Representative evidence and real corpus/text/image-egress policy still require
owner-approved inputs and destinations. Generated local tests can proceed
without implying that approval.

## Concrete implementation sequence from source review

The first cohesive implementation now delivers actual opt-in
`POST /evidence/v1/corpora/{corpus_id}/search`, `PipelineReader.search_evidence`
and the fixed `query_pipeline.py search-evidence` operation through the real
contained worker. The implementation now returns exact manifested-record correspondence,
scoped physical-page/transform facts and, when explicitly configured, actual
related-page recovery states. Generated native integration and Phase 5 local
frozen-source qualification passed. Separately authorized
scan serving and occurrence-level correction adoption remain required later
capabilities, not removed from the full program.

Keep a strict dependency-free evidence contract, an injected evidence-search
implementation, and a separate supervised runtime. The existing child composition
shell supplies physical `rag` collaborators; the host must not acquire a new
transitive `rag` dependency. Extend the application service through an injected
evidence runner using the existing concurrency/shutdown accounting. Do not create
a service-runtime/evidence-runtime import cycle. Opt-in HTTP composition must
leave default service v1 routes, response schemas and committed OpenAPI unchanged.
Use separate explicit operator configuration rather than widening the closed
corpus registry or trusting paths found inside document text.

Anscombe's independent source inspection identified these integration traps:

- `_execute_search` truncates hits before returning. Evidence correspondence
  must precede this boundary, with the complete internal hit still available.
- `_load_index_manifest` is a tolerant, unbounded reader. This companion needs
  strict bounded snapshot parsing followed by existing compatibility checks.
- `_context_hit_matches_record` checks only seven fields. Compare type-strict
  canonical metadata against `_qdrant_payload(record, computed_id)` (excluding
  its separate text field), plus exact full text, to catch metadata-only forgery.
- Table children copy parent lineage/source fidelity while replacing text;
  existing quality validation checks their table-family derivation rather than
  ordinary record-attestation equality. Preserve that validation and label
  inherited geometry parent-table scope, never exact row geometry.
- Document-backed quality validation must reconstruct structural exclusions
  from `profile_from_provenance(chunk_completion.structure_profile)`,
  `_identify_book_sections(..., emit_log=False)` and `_book_structural_ranges`.
  Empty default exclusions can reject legitimate excluded front/back matter.
- Use `lineage_scope_indexes`/`lineage_scope_pages`, not every stored span or
  a continuous range between the first and last physical pages.

The generated end-to-end acceptance exercise must traverse local Qdrant,
contained worker, authenticated HTTP, reader and CLI, with differing actual
evidence states and unchanged ordinary v1 behavior. Include metadata-only
forgery, short-ID/full-text collision, boolean/numeric type confusion,
table-child/multipage scope, structural exclusions, stale artifacts and
wrong-original-PDF recovery reports in the negative controls. Generation labels
are neither scan permission nor attestation of the entire vector-store bytes.

Peirce independently proposed a generated acceptance graph with single-page
prose, two long records sharing the same public text prefix, a table parent and
four derived children including duplicate rows, a multipage item and scoped
fragment, and valid profile-excluded front matter. Its optional exact-source
recovery report must cover candidate, empty, failed, deferred and unselected
states. Portable tests may synthesize the artifact graph; a live integration
exercise must use actual generated pipeline artifacts and local Qdrant.

The key false-accuracy control is a generated scan containing `not liable`
while simulated Docling/chunk text omits `not`: structurally consistent hashes
and quality can still agree, and a related recovery candidate can restore it.
The companion must never label that correction adopted or the chunk OCR-correct.
This matrix is a pending implementation requirement, not a claim those new
tests or service routes already exist.
