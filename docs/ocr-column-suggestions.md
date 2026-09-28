# Unconfirmed OCR column suggestions

This opt-in tool proposes a body band, gutter and left-then-right line order
from saved OCR quadrilaterals. It does **not** recognize text, classify a page
as prose, approve a correction, or modify canonical extraction. A suggestion
is an unconfirmed geometry hypothesis, including when it matches engine order.

Status: implemented locally with independent core/IO/CLI, UI and browser-artifact
review. The [Phase 6 frozen-source local checkpoint](evidence/2026-09-07-ocr-column-review-qualification.md)
passed all nine gates: 8,230 tests passed, seven skipped and six warnings.
Complete collection/JUnit correspondence and unchanged tracked sources were
independently verified. This is not hosted CI, a release or representative OCR
accuracy; synthetic improvements do not establish general accuracy.

## Export a suggestion report

Run from the repository root, using the exact original source and its saved
recovery report:

```console
python tools/suggest_ocr_columns.py --pdf source.pdf --recovery recovery.json --pages 1 6 7 --output suggestions-v1.json
```

`--pages` is mandatory: supply 1 to 20 distinct, one-based page numbers within
the source's declared page count. There is no default all-pages selection,
range syntax, or reference-text input. The source is hashed only; this command
does not parse or render its PDF, load models, rerun OCR, or download anything.

The output parent must already exist and the output must not exist. Inputs
and output must be distinct, regular, single-linked files; link components
are rejected. Limits are 256 MiB for the source, 64 MiB for strict recovery
v1/v2 JSON, and 1 MiB for the private suggestion report. A busy output lease
fails without waiting. Input bytes and file identities, the output directory
generation, and staged report bytes are checked again before create-only
publication. These pathname checks are not a component-pinned operating-system
guarantee against a hostile local actor.

Exit codes:

- `3`: the report was created; manual review is always required, even if every
  requested page abstained or no line order would change.
- `2`: invalid arguments, input/binding failure, busy output, or publication
  failure. An existing output is never overwritten.
- `130`: cancellation.

A late cancellation or cleanup failure can leave a complete report present.
Inspect the selected output before retrying, and choose a new output path;
do not assume a nonzero exit means nothing was published. Console messages
contain aggregate counts and fixed notices, not OCR text or input paths.

## Bindings and coverage

The `ocr_column_suggestions` schema-v1 report binds the exact source SHA-256
and recovery-file SHA-256. Each available candidate also has a domain-separated
canonical candidate digest; it is not a file digest. These bindings establish
correspondence to the supplied bytes, not source authenticity or OCR accuracy.
The report contains geometry, indexes, digests and fixed statuses, not OCR text.

Every requested page appears exactly once, in page-number order, with one of:

- `unconfirmed_hypothesis`: a complete permutation of its observed lines.
- `abstained`: geometry is ambiguous, unsupported or exceeds the work budget.
- `unavailable`: the candidate failed, was deferred, or was not selected for OCR.
- `empty_candidate`: an actual empty candidate, distinct from missing evidence.

Coverage also lists every unrequested source page. Complete requested-page
accounting does not imply complete source-text coverage. Failed or unavailable
pages are not silently removed or replaced with fabricated blank text.

Inference currently requires original rendered-image coordinates, a unique
dense body band, exactly two separated horizontal components, and at least
three observed lines per column. The existing conservative permutation guard
rejects crossing/ambiguous geometry and overlapping or reversed within-column
order. Preprocessed-coordinate candidates explicitly abstain. There is a
2,000-line per-page limit and a shared requested-cohort geometry-work budget.

## Review before using a plan

The local review workflow separates suggesting, explicitly classifying the
page as prose, previewing its order against the source, and exporting a review.
The CLI report is an audit artifact, not an approved layout plan or an
automatic adoption instruction. The source and candidate generation must still
match when a suggestion is used.

Pending suggestions are transient UI state. Drafts retain only explicitly
previewed plans, not pending suggestions or prose confirmation. Restarting a
draft does not restore approval. Preview/export remains separate from changing
canonical text; this feature does not publish OCR corrections into ingestion
or an AI index.

Reading-order actions also carry a per-view context token, separate from
Gradio's live server `State`. Tests using the installed Gradio `Queue.push`
and `process_api` reproduced the risk: an older captured checkbox value can
otherwise be paired with a newer page/order state for preview or export.
The initial view has a token; every order transition rotates it. Manual/column
preview and all three order exports (layout, Docling and assignments) submit
the displayed token and reject a stale or missing one. Successful view sync
returns the new token and four cleared confirmation boxes together. Drafts
omit this transient token, and it is not authenticated proof of human review.

This protection does not yet claim coverage of every annotation action.
Reference, crop and omission approval-context queue audits/hardening remain
pending. The same captured-input/live-state mechanism warrants testing those
paths, but individual failures there have not been reproduced. The local
checkpoint qualifies the implemented reading-order scope, not those pending
annotation audits or authenticated human consent.

## What the evidence does—and does not—show

On the known generated calibration fixture, a page-6 hypothesis reduces 15
observed order inversions to zero while retaining all eight recovery pages in
the comparison. That is not held-out or representative accuracy evidence.

The negative controls are essential:

- Geometrically identical prose and row-major table lines receive the same
  hypothesis. Applying it helps the prose example but damages the table's
  correct order. Geometry cannot supply the required prose classification.
- A scripted line absent from the observed candidates remains missing after
  reordering, even though every observed line is preserved. This is an omission
  control, not evidence that independent OCR engines actually missed that line.

References were used only after hypothesis generation in that feasibility
experiment. Its full-page CER/WER and order scores are hypothetical outcomes,
not accepted corrections; its zero/null critical-occurrence totals do not
validate critical phrases. Before broader accuracy claims, evaluate a frozen,
approved held-out cohort with tables, columns, furniture and omissions, retain
all requested/unavailable pages, and measure harmful suggestions as well as
improvements and abstentions. See [OCR accuracy](ocr-accuracy.md) and
[benchmark contracts](ocr-benchmark.md).
