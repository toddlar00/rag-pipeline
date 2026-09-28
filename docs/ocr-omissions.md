# Saved-layout OCR omission warnings

This opt-in check finds **potentially empty saved Docling regions**, including
text, headings and tables. It can expose a dropped column or footnote even when
the rest of a page has long, high-confidence OCR text. It does not inspect scan
ink, run OCR, repair text, or prove that all source text was captured.

The input is an original source PDF (hashed only), a strict saved OCR recovery
v1/v2 report, and fixed source/recovery-bound [Docling proposals](ocr-docling.md).
No model, PDF parser, canonical output, or private corpus is needed for the
diagnostic computation. Inputs are untrusted evidence, not authenticated truth.

```powershell
python tools/inspect_ocr_omissions.py `
  --pdf output/pdf/ocr-challenge-v1-verified/challenge.pdf `
  --recovery evaluation-reports/ocr-challenge-v1/none.json `
  --proposals evaluation-reports/ocr-docling-v1/proposals.json `
  --output evaluation-reports/ocr-omissions-v1/diagnostics.json
```

These are generated synthetic artifact paths, not bundled required inputs. Use
an existing exact-matching set or generate proposals for the selected recovery
report first. The output must be new. The CLI prints fixed labels and aggregate
counts, never paths, OCR text, Docling text, confidence values or exception
details. Exit `0` means no warnings **within this saved-region scope**, `3`
means warnings or unevaluated/incomplete coverage, `2` means an input/publication
error, and `130` means cancellation. A late failure or cancellation can follow
publication: inspect the output before retrying with a new path.

## What the diagnostic means

Every source page is listed. Candidate state distinguishes `available`, `empty`,
`retry_failed`, `deferred`, and `not_selected`. A failed/missing candidate cannot
be treated as an empty OCR result. Saved proposals currently retain regions only
for selected candidate pages; no regions are invented for other pages.

| Region status | Meaning |
| --- | --- |
| `no_candidate_line_overlap` | No saved OCR line box potentially overlaps this known region. Review the scan for missing text. |
| `empty_text_only` | Overlapping line records contain only empty/whitespace text. |
| `boundary_or_ambiguous_overlap` | Nonempty text has only boundary-crossing or unsupported-quadrilateral geometry here. This is uncertain, not proof of absence. |
| `has_nonempty_line_geometry` | At least one nonempty line has supported geometry wholly inside the region. This does **not** establish completeness or correct recognition. |
| `not_target_region` | Saved picture/other region, retained but not assessed as text. |
| `unevaluated` | A saved target region exists, but its candidate or computation is unsupported/bounded out. Counts are null, not fabricated zeros. |

The geometry uses fractions of the current candidate raster. It inherits the
Docling proposal validator's original-input, non-preprocessed candidate and page
geometry compatibility checks (including its declared two-pixel raster-rounding
tolerance). Unsupported preprocessed mappings are explicitly unevaluated; this
tool does not relax or reconstruct those transforms. Valid saved regions on an
abstained layout-order page can still be inspected: uncertain line order does
not by itself make a source-bound region unusable.

OCR quadrilaterals use the existing conservative rectangle classifier. Concave,
high-skew and other unsupported shapes remain ambiguous when their axis-aligned
bounding box overlaps a region. Boundary touches count as uncertain potential
hits. A declared `1e-12` fraction guard applies only when testing separation to
avoid a false absence claim caused by numeric conversion roundoff; containment
is never expanded. Multiple overlapping regions can count the same line. Counts
are not unique source coverage, polygon intersection measurements or confidence
calibration. Confidence is deliberately not used to dismiss an empty region.

Body and furniture warnings are counted separately. Furniture can contain real
footnotes or captions, so it is not silently excluded. Pictures can contain text
but are outside this first detector's target kinds. Missing or invalid Docling
provenance, omitted/unattached items, region limits and unavailable table
structure remain explicit incomplete-layout warnings.

## Review assessments, without erasing warnings

The policy exposes:

```python
build_omission_diagnostics(recovery, proposals, *, recovery_sha256, proposals_sha256)
validate_omission_diagnostics(payload, recovery, proposals, *, recovery_sha256, proposals_sha256)
validate_omission_decisions(decisions, diagnostics)
build_omission_review(recovery, proposals, *, recovery_sha256, proposals_sha256, decisions, confirmed)
validate_omission_review(payload, recovery, proposals, *, recovery_sha256, proposals_sha256)
```

Each optional decision has exactly `page_number`, `region_ref`, and `decision`:
`suspected_missing_text`, `not_text`, or `false_alarm`. Only known saved regions
are accepted, with no duplicate decisions. Removing a decision restores its
unresolved state. `validate_omission_decisions` validates this mapping against a
derived diagnostic; it is not an authenticity validator for an imported
diagnostic. Use the full rebuild validator with fixed inputs for imported data.

An `ocr_omission_review` v1 requires explicit `confirmed=True`. Partial
assessments, including zero recorded decisions, are allowed; unresolved total
and warning-region counts stay explicit. A false-alarm assessment never changes
the embedded immutable warning or asserts full-page/source verification. The
confirmation is a local operator declaration, not authenticated approval. Review
reports always retain `requires_attention=True`, `accuracy_verified=False`,
`full_page_coverage_verified=False`, `recognition_rerun=False`, and
`canonical_extraction_modified=False`.

The review embeds the exact rebuilt diagnostic and its `diagnostics_content_sha256`.
This is **not a file hash**: it hashes the ASCII domain
`rag-pipeline:ocr-omission-diagnostics:v1`, a NUL separator, and UTF-8 sorted-key,
compact JSON (`ensure_ascii=False`, nonfinite values disallowed). Both diagnostic
and review validators rebuild all fields and compare types, counts, geometry,
bindings and declarations strictly; extra fields and forged booleans are rejected.

## Bounds and publication

Policy bounds are 5,000 source-page states, 20 selected candidate pages, 2,000 OCR
lines per evaluated page, 500 saved regions per page, 20,000,000 line-region
pairs for the complete supported cohort, and 10,000 unique decisions. A line
limit leaves saved regions visible but unevaluated. The work budget is checked
before geometry work; it never silently truncates a partially evaluated cohort.
Pages without saved regions or without target kinds remain unevaluated.

The file adapter, `ocr_omission_io.inspect_omission_files`, accepts exactly the
three fixed inputs and a new output. Source bytes are streamed and hashed, not
retained; source/recovery/proposals are bounded at 256/64/32 MiB respectively.
Both passes of every same-handle snapshot enforce the byte limit and compare
content/identity. JSON uses the existing strict parser (including nesting,
duplicate-key and finite-number checks). Inputs and output must be distinct,
single-linked regular files with no link components. A nonwaiting output lease
and private, atomic create-only publication preserve existing artifacts. All
three input digests and path constraints are rechecked in the final commit
callback, after serialization. This detects ordinary source-generation races;
it is not a claim of component-pinned no-follow protection against a hostile
local filesystem administrator.

Synthetic-only tests cover omitted columns, high-confidence text elsewhere,
whitespace, touching/skew/concave geometry, furniture, unavailable cohorts,
incomplete layouts, strict rebuilt reviews, bounded double reads, changed inputs
at commit, no-clobber behavior and CLI privacy/cancellation. They validate these
mechanics, not a representative document-level accuracy improvement.

## Remaining blind spots

A region containing one recognized line can still be mostly missing. An entire
area omitted by both OCR and Docling is invisible to this check. Table-cell
completeness, mathematical notation, labels inside pictures, reading-order
correctness and semantic correctness need separate evaluation. Future independent
scan-ink/component coverage could help find shared omissions, but would require
its own source geometry, image/compute bounds, ruling/illustration exclusions,
false-positive review and held-out validation. It is not implemented here.
