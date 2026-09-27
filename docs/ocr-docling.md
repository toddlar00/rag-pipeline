# Saved Docling layout proposals

This opt-in workflow uses already-saved Docling layout, table annotations, and
body-tree order to suggest review regions and a complete OCR line permutation.
It does not run conversion, open a PDF parser, load models, download anything,
change recovery v1/v2, or update canonical extraction/indexes.

```powershell
python tools/propose_ocr_layout.py `
  --pdf "approved-source.pdf" `
  --docling "output/approved-source.json" `
  --recovery "evaluation-reports/source-recovery.json" `
  --output "evaluation-reports/source-docling-proposals.json"
```

The saved JSON must have its matching, capture-verified **v3 conversion
completion** sidecar. The adapter invokes the existing facade's
`rag._load_conversion_source_binding` parser; it does not create or reinterpret
completion proof. Legacy, missing, malformed, or mismatched completion files
fail closed. Do not manufacture completion records for unverified conversions.
The completion's referenced Markdown or preprocessed PDF is not loaded: those
outputs are not used by this proposal workflow. The source bytes, saved Docling
JSON, recovery report, and completion itself are the four verified inputs.

Paths are fixed local operator inputs, never embedded-document instructions.
The source is hashed as bounded bytes, not parsed or rendered. Its digest and
size must match the conversion capture, and its digest must match recovery.
The exact Docling JSON digest/size must match the completion. SHA-256 digests
bind supplied artifacts; they do not independently establish document truth,
Docling accuracy, or authenticated approval of a locally supplied manifest.

## What is suggested

Docling stores reading order in the body tree, distinct from its text/table
arrays and furniture tree. The policy traverses child references in saved order
and retains text, headings, tables, pictures, and other content as normalized
review regions. It never sorts the text array and calls that reading order.
These representations follow the [Docling document contract](https://docling-project.github.io/docling/concepts/docling_document/).
Exact child objects with either `$ref` (aliased export) or `cref` (default
`model_dump` field name) are supported; mixed aliases and extra fields are
rejected. Both encodings are characterized against the installed Docling types.

Each region has a stable safe Docling reference, kind, normalized bounding box,
body/furniture membership, and proposal rank. Supported table annotations retain
row/column spans and optional cell boxes, without copying cell text. A heading
covering at least 65% of page width is highlighted as a possible spanning
heading; width is a geometric heuristic, not proof of its semantic role. Its
saved position relative to body regions is retained.

A complete line order is suggested only when every existing OCR line is wholly
contained in exactly one valid region. The policy preserves engine order within
each region, including every table. Furniture lines retain their exact original
engine slots; body order does not invent header/footer placement. No words,
scores, boxes, or table-internal order are corrected. Region geometry may remain
available for manual crop selection even if line-order proposal abstains.

## Geometry and abstention

The initial supported frame is an **original effective conversion input** and
an **unprocessed recovery candidate**. Both top-left and bottom-left Docling
boxes are converted using the saved page width/height. Page dimensions scaled
by saved recovery DPI must agree with the candidate raster within two pixels
for integer-raster rounding. This is consistency of saved geometry, not a fresh
PDF crop/rotation measurement or an attestation of identical rendered pixels.
An operator must check overlays against the source.

Preprocessed conversion inputs and all preprocessed recovery candidates
explicitly abstain, even if a recorded transform happens to be identity. No
unsupported crop/rotation/deskew mapping is inferred. Missing dimensions,
out-of-page boxes, unsupported origins, multipage items, unlocated/unattached
content, ambiguous line boxes, overlapping assignments, unmatched lines, or
invalid table structure likewise prevent a complete order. Failures remain
visible instead of becoming fabricated blank or perfect pages.

The report covers every selected recovery page, including failed/empty pages,
and separately lists deferred and unselected pages. A proposal is not a measured
accuracy improvement. Evaluate any reviewed derived text against approved fixed
references, including structural and critical-content checks.

## Artifacts and editor integration

The `ocr_docling_proposals` v1 artifact contains exact source/recovery/Docling/
completion digests, parameters, region/table geometry, proposed complete line
orders, coverage, and fixed abstention reasons. It contains no OCR/Docling source
text. It always declares `requires_attention: true`,
`acceptance: manual_review_required`, `canonical_extraction_modified: false`,
and `recognition_rerun: false`.

The pure `validate_docling_proposals(payload, *, recovery, recovery_sha256)`
checks editor imports against the complete current recovery, including raster
dimensions and a recomputed complete line assignment/permutation. Merely
supplying a matching-looking digest does not bypass those checks. The editor
must additionally snapshot and continuously verify its fixed proposal file.

After explicit operator confirmation, `build_docling_review(proposals, *,
recovery, recovery_sha256, proposals_sha256, confirmed_pages)` produces a
separate `ocr_docling_review` v1 artifact. Only complete proposed pages are
eligible. It preserves original text and derives proposed text only by the
bound line permutation. `validate_docling_review` rebuilds that exact text/order
and rejects edits or stronger accuracy/approval assertions. Operator review is
declared, not authenticated; canonical extraction remains unchanged. This
review artifact contains source text and must stay private. Region-only
suggestions require separate explicit confirmation into an existing region plan.

## Bounds, privacy, and execution results

Inputs are single-linked regular files with no symlink/reparse-point path
components. Limits are 256 MiB source, 64 MiB saved Docling JSON, 64 MiB recovery,
and 1 MiB completion; JSON nesting is bounded. Structural work is capped at
20,000 items, depth 64, 500 regions per page, 2,000 OCR lines per page, and 2,000
table cells per page/table. Output is private and create-only under a nonwaiting
path lease. Every input is rehashed inside the final publication callback, after
serialization and immediately before the no-clobber commit. Inputs are never
modified. This is not protection against a malicious writer able to change and
restore all files or against compromised local code.

The CLI prints aggregate counts and fixed guidance, never paths, source text,
arbitrary labels, or raw exception details. Exit **3** means a proposal report
was created and requires human review; **2** means invalid input/publication
failure; **130** means cancellation. There is no accuracy-success exit 0. A late
failure or cancellation can occur after publication: inspect the chosen output
before retrying, and choose a new output rather than overwriting an existing one.

Tests use synthetic JSON/source bytes, the real existing v3 completion parser,
and an actual `DoclingDocument` save/load export. They verify structure/geometry,
full-order coverage, privacy, races, cancellation, and create-only publication.
They do not prove real-document layout accuracy or that conversion/model loading
works in the ambient environment. No ambient configuration is silently changed
to work around missing Docling OCR APIs.
