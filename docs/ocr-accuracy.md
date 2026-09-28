# OCR accuracy: measurement and targeted retries

These opt-in tools add an independent transcription benchmark and bounded
page-level OCR candidates. They do **not** change `rag.py` conversion defaults,
replace Docling pages, update indexes, or bypass publication/source-fidelity
gates. Confidence is a review signal, not measured accuracy.

## Quick start

Run the dependency-light scorer on deliberately imperfect synthetic text:

```powershell
python tools/evaluate_ocr.py --input evaluation/ocr/synthetic.json --output evaluation-reports/ocr-synthetic.json
```

Retry known problem pages at 300 DPI, or use 400 DPI for a separate comparison:

```powershell
python tools/retry_ocr.py --pdf source.pdf --pages 3 7 --dpi 300 --max-pages 5 --output output/ocr-review-300.json
python tools/retry_ocr.py --pdf source.pdf --pages 3 7 --dpi 400 --max-pages 5 --output output/ocr-review-400.json
```

Page numbers are **one-based PDF page numbers**, not printed textbook labels.
`--pages` prioritizes those pages; other suspicious pages can use remaining
slots. Omit it to select empty, short, or visibly corrupt native extraction.
Requested pages come first in ascending order, then suspicious pages in PDF
order. This deterministic policy is not a learned severity ranking.

Each selected page gets one OCR attempt. The default cap is 5 pages (maximum
20); unprocessed candidates remain in `deferred_pages`. The tool inspects at
most 5,000 pages and rejects larger documents. Each raster is limited to
25 million pixels and a 6,000-pixel side **before allocation**. Oversized pages
fail visibly rather than silently reducing DPI. These are work/resource bounds,
not a wall-clock deadline for native PDF parsing or model inference.

The runtime loads only the locally installed, byte-verified RapidOCR models
approved by `model-artifacts.lock.json`. It does not download models or change
dependencies. It increases RapidOCR's whole-image side limit to preserve the
requested raster resolution, and records the effective configuration.

## Optional worker deadline

Pixel and page caps bound work, but cannot interrupt a stalled native PDF or
OCR call. Add an execution deadline when running an interactive retry:

```powershell
python tools/retry_ocr.py --pdf source.pdf --pages 3 7 --timeout-seconds 600 --output output/ocr-review-bounded.json
```

`--timeout-seconds` accepts a finite number from 1 to 86,400. It runs the entire
recovery command in a contained worker process, including native extraction,
rendering and model inference. The parent prints a start notice, suppresses
untrusted worker output, and validates a completed report and its requested
settings before printing the usual summary. The timeout option is not forwarded
to the worker, so it cannot recursively launch itself. Omitting it keeps direct
execution and existing report schemas unchanged.

This is a **worker execution deadline**, not an exact end-to-end command runtime
cap: containment/startup and process-tree termination grace are additional.
Timeout returns `124`; supervised Ctrl+C cancellation returns `130`. Neither
status is converted into an empty candidate, a per-page failure or partial
success. A worker can be stopped after it has published a complete report:
**inspect `--output` before retrying**, even after timeout or cancellation.
Other worker, cleanup or report-validation failures return `2`; uncertain
cleanup must be investigated before retrying. A completed worker's `0` or `3`
is preserved only when its validated report agrees with that status and the
requested settings.

Parent read-back checks report consistency, settings, evidence presence and
worker status; it is not an independent source/evidence authenticity check or
a fresh verification of the current PDF.

Forced termination can bypass private snapshot/staging cleanup. Private
temporary files may remain; do not wildcard-delete neighboring report files.
The existing snapshot janitor is ownership-checked and age-gated, not an
immediate-erasure guarantee. Deadline handling does not modify canonical
extraction or automatically accept any OCR candidate.

## Opt-in image experiments

Use `--preprocess deskew`, `--preprocess contrast`, or
`--preprocess deskew-contrast` to experiment on selected page rasters. The
default is `--preprocess none`: existing behavior and v1 report fields remain
unchanged. These options do not edit the PDF, native text or canonical Docling
extraction, and do not turn an OCR candidate into an accepted correction.

```powershell
python tools/retry_ocr.py --pdf source.pdf --pages 3 7 --preprocess none --output output/ocr-plain.json
python tools/retry_ocr.py --pdf source.pdf --pages 3 7 --preprocess deskew-contrast --output output/ocr-cleanup.json
python tools/compare_ocr.py --baseline-recovery output/ocr-plain.json --recovery output/ocr-cleanup.json --references private-references.json --output evaluation-reports/ocr-cleanup-vs-plain.json
```

Keep the same reference pages, DPI, and selection settings when testing cleanup.
With `--baseline-recovery`, the command compares both reports' **candidate text**
directly on one paired cohort; their `original_text` is not scored. Without that
option, the existing original-versus-retry behavior remains unchanged. Supply
source-bound `--evidence` when the intended original baseline is a prior OCR
transcription. No cleanup is enabled or selected based solely on engine
confidence.

Deskew searches a bounded thumbnail for a small correction angle, with explicit
skip reasons for weak or ambiguous evidence. It is not page orientation
detection, perspective correction or dewarping. An accepted correction rotates
the full-resolution image onto an expanded white canvas. Both input and output
must fit the existing side/pixel budgets; an expansion that would exceed them
is skipped, never cropped or silently downscaled. Already aligned pages avoid
unnecessary rotation/resampling.

The current heuristic searches -5 to +5 degrees in 0.25-degree steps on a
thumbnail no larger than 1,000 pixels per side. Boundary estimates, corrections
smaller than 0.5 degrees, and weak or ambiguous projection peaks are skipped.
The recorded `gain` measures horizontal ink alignment, **not OCR accuracy or
confidence**. Contrast uses the original raster's 0.5th/99.5th percentile levels,
before any white padding is added. Parameters are fixed and recorded so runs
can be compared without hidden adaptive settings.

Contrast cleanup is a global grayscale percentile stretch, intended as an
experiment for faint scans. It is not selective shadow removal, denoising,
sharpening or reconstruction. It can lose color information or amplify noise;
near-uniform or unsuitable intensity ranges are skipped. In combined mode the
recorded transformations identify exactly which operations were applied.
Keep the original scan available for review, especially thin punctuation,
footnotes, numbers, diagrams and colored annotations.

Enabled experiments emit **v2 recovery reports**, including the requested mode
in `retry_configuration.preprocessing` and a `candidate.preprocessing` record
for every successful candidate, even when all cleanup steps were skipped. That
record contains original/processed raster sizes, applied angle, step statuses
and reasons, algorithm/parameter identifiers, library versions, and forward
and inverse affine matrices. Failed retries remain explicit failures, not
fabricated processed candidates. Current comparison tools strictly validate
both v1 and v2; older v1-only consumers must not reinterpret v2 reports.

V2 boxes use `preprocessed_image_pixels`. To map a point back to the original
rendered raster, apply `processed_to_source` to `[x, y, 1]`; the forward matrix
is `source_to_processed`. These are image-pixel coordinates, not PDF coordinates
or source-fidelity attestations. Points in added white padding can map outside
the original raster; do not silently clamp or promote such coordinates into
canonical provenance.

Deskew is a recognized OCR input-quality consideration, but improvement depends
on the page and engine; test it against reviewed references. See the
[Tesseract quality guidance](https://tesseract-ocr.github.io/tessdoc/ImproveQuality.html)
and [OpenCV affine transformation documentation](https://docs.opencv.org/4.13.0/da/d54/group__imgproc__transform.html).

## Review report

Reports contain the exact PDF SHA-256, input evidence SHA-256 if supplied,
selection reasons, original extraction, candidate text, line confidence and
pixel boxes, render dimensions, engine version, failures and deferred pages.
V1 boxes are in rendered-image pixel coordinates; v2 experiment boxes are in
preprocessed-image pixel coordinates, **not PDF coordinates**.
Candidate lines retain engine order; multi-column reading order, table cells
and heading structure are not validated or reconstructed.

The default original is `pdf_native` text, not the existing Docling output.
Optional evidence can supply a prior transcription and poor-page quality
grades. No candidate is automatically accepted, even when confidence is high.
Candidates with empty text remain unresolved. A failed retry preserves its
original text; an unavailable native extraction is explicitly marked null.
Unknown confidence remains null rather than being treated as zero.

Publication uses a private immutable PDF snapshot, checks the current source
again before writing, and atomically creates a **new** report. Existing outputs
are refused. The output filesystem must support hard links for the no-clobber
commit; unsupported filesystems fail closed. Reports from cooperating processes
also share an output-path lease. A source/evidence change prevents publication.
These reports are review artifacts, not source-provenance or READY receipts.

Reports contain source-derived text: keep them in ignored `output/` or another
approved private location. Terminal summaries do not print source text or
third-party exception details. Do not commit private PDFs, reference
transcriptions, recovery reports, or corpus-derived fixtures.

Candidate producers and report importers enforce the same declared DPI,
engine-side limit and raster budgets. Zero-area OCR boxes are rejected. Invalid
candidate metadata remains a local retry failure with its original extraction
preserved; it is not published as a usable candidate. These checks validate
report consistency, not transcription accuracy or source provenance.

Exit codes: `0` means a report was written without failures, empty candidates or
deferred pages; `3` means a report was written with unresolved work; `2` means
validation/runtime/publication failed. **Exit 0 does not mean OCR is accurate.**
If staging cleanup fails after the report was created, exit `2` explicitly
tells you to inspect the existing output before retrying.
If nothing was selected, use `--pages` for a known problem page. A clean-looking
but incorrect text overlay can evade the heuristic.

The CLI prints fixed recovery guidance for known failure categories without
echoing source paths or exception text. `retry_limit_or_validation` covers both
raster limits and invalid candidate metadata; when a 400-DPI raster is too
large, try a separate 300-DPI run, but do not assume every validation failure is
a size problem. `retry_runtime_unavailable` points to missing dependencies;
`retry_runtime_failed` requires checking the local model/runtime setup. Keep
the failed report and choose a new output path for each attempt.

## Optional source-bound evidence

`--evidence private-evidence.json` accepts this strict JSON object:

```json
{
  "schema_version": 1,
  "source_sha256": "<64 lowercase SHA-256 hexadecimal characters>",
  "pages": [
    {
      "page_number": 3,
      "text": "Prior extraction for this page.",
      "low_grade": "POOR",
      "ocr_confidence": null
    }
  ]
}
```

Compute the exact source hash locally, for example with
`(Get-FileHash -LiteralPath source.pdf -Algorithm SHA256).Hash.ToLowerInvariant()`.
`page_number` and `text` are required; `low_grade` and `ocr_confidence` are
optional. Grades are `POOR`, `FAIR`, `GOOD`, `EXCELLENT` or null. Confidence is a
finite number in 0..1 or null; convert unavailable/NaN scores to null explicitly.
For Docling enum objects, use `grade.name` (uppercase), mapping `UNSPECIFIED` to
null. Numeric OCR confidence is recorded only; it does not trigger retries.
`low_grade: "POOR"` does trigger selection, even when the text looks clean.
Duplicate pages, unknown fields, mismatched hashes and out-of-range pages fail.
Evidence is bounded to 16 MiB, 5,000 page records and 100,000 characters per page.

Evidence is supplied by the operator, not independently attested by this tool.
Only attach text/grades from that exact PDF generation. In Docling runtimes
supporting it, page Markdown can be exported using
`doc.export_to_markdown(page_no=N, traverse_pictures=True)`; traversing pictures
includes full-page OCR child text. Do not use document concatenation/filtering
as a transparent replacement: it can remap page/item identities.

## Build a useful benchmark

Start with 30-50 approved, manually transcribed pages or smaller regions,
covering clean scans, faint scans, skew, small footnotes, columns, citations,
tables and figures. Verify the reference against pixels, not another OCR
engine. Keep a held-out set for subsequent tuning. No private corpus benchmark
is included: `evaluation/ocr/synthetic.json` only verifies scoring behavior and
deliberately contains errors; it is not an engine-accuracy baseline.

For each candidate, place the human reference and `candidate.text` into the
following schema. Use stable opaque IDs rather than sensitive filenames.
The automatic comparison below removes this copying for recovery reports.
For standalone transcription experiments, compare the original and each retry
in separate files with identical IDs and references. Do not compare raw average
engine confidence as accuracy.

```json
{
  "schema_version": 1,
  "records": [
    {
      "id": "approved-page-003",
      "reference": "The tenant did not waive notice. Rule 12 requires 30 days.",
      "prediction": "The tenant did waive notice. Rule I2 requires 30 days.",
      "critical_tokens": ["not", "12", "30"]
    }
  ]
}
```

The scorer reports per-record and micro-averaged CER/WER as
`(substitutions + deletions + insertions) / reference units`, plus exact match.
Rates can exceed 100% and are null for an empty reference; insertion counts and
exact-match results still reveal hallucinated text on empty pages. Characters
are Unicode code points, not grapheme clusters; words are whitespace-delimited.
NFC normalization and whitespace collapsing are applied; case, punctuation and
word order remain significant. Formatting-only whitespace errors are therefore
not measured. Ambiguous optimal alignments use deterministic edit tie-breaking.

Critical entries are exact contiguous whitespace-token sequences, including
punctuation, and must occur in the reference. Their recall compares occurrence
counts; it **does not prove correct position/context**, and overlapping phrases
can count the same words more than once. Inspect negation, citations and numbers
in context even if counts match. The current benchmark does not separately
score table-cell structure, omitted visual regions or layout reading order;
transcription omissions/reordering contribute to CER/WER.

Use contextual critical phrases where meaning depends on who did what. For
example, a reference saying `Alice is liable. Bob is not liable.` and a retry
saying `Alice is not liable. Bob is liable.` both contain one `not`. Counting
only that word will miss the changed context; critical entries such as
`Bob is not liable.` and `Alice is liable.` reveal the missing phrases. This
still does not replace human review. Line/table regrouping that changes only
whitespace is outside CER/WER's normalization contract and needs visual checks.

Metrics contain counts, opaque IDs and the input digest, not transcription or
critical-token text. Input files remain sensitive. Strict parsing rejects
duplicates and nonfinite numbers. Limits: 16 MiB input, 256 records, 20,000
characters per text, 64 critical entries per record. Exact alignment has a
25-million-cell per-record and 250-million-cell per-suite budget after trimming
equal ends; split large/error-heavy pages into regions if rejected.

## Automatically compare original and retry

Create one private, human-reviewed reference manifest for the exact PDF:

```json
{
  "schema_version": 1,
  "source_sha256": "<64 lowercase SHA-256 hexadecimal characters>",
  "pages": [
    {
      "page_number": 3,
      "reference": "The tenant did not waive notice.",
      "critical_tokens": ["not"]
    }
  ]
}
```

`critical_tokens` is optional. Pages must be unique one-based PDF page numbers;
references reuse the scorer's 256-record, 20,000-character, and critical-phrase
limits. The manifest's source digest must match the recovery report. Keep the
same reference pages when comparing experiments; do not remove difficult pages
after seeing results.

```powershell
python tools/compare_ocr.py --recovery output/ocr-review-300.json --references private-references.json --output evaluation-reports/ocr-comparison-300.json
python tools/compare_ocr.py --recovery output/ocr-review-400.json --references private-references.json --output evaluation-reports/ocr-comparison-400.json
```

Each command scores the original extraction and that report's retry directly
against the references. It does not run OCR, open the PDF, or correct anything.
Inspect `coverage` before comparing experiment reports: paired page lists must
match for a fair 300-versus-400 comparison. The original's `baseline_kind`
distinguishes native PDF text from operator-supplied prior OCR.

The reference pages define a fixed evaluation scope. Baseline and retry
headline metrics use **the same paired pages**, with a mapping in `paired_pages`.
Failed retries, missing originals, deferred and unselected pages remain listed
in `coverage.unpaired_pages`, including availability of each side. A failed
retry is never fabricated as empty text; a genuine empty candidate is scored
as empty text. Selected pages lacking references and all deferred/empty pages
are also listed. With no paired pages, `comparison` is null, not a perfect score.
This measures a selected sample, **not whole-document accuracy**.

Per-page outcomes are `improved`, `unchanged`, `regressed`, or `mixed`, using
character edits, word edits and each critical phrase's missing/extra counts.
`unchanged` means those counts are unchanged, not that both transcriptions are
identical.
Any worsening in those metrics on any page or critical phrase sets `regression_detected`,
even if the overall CER decreases. This catches tradeoffs such as fixing a
citation while newly omitting `not`. All deltas are **retry minus baseline**:
negative error deltas are better, positive exact-match deltas are better.
Empty-reference rate deltas remain null; edit counts still expose hallucinations.
Critical occurrence counts still do not prove correct position or context.

The comparison checks the recovery report's schema, page identities, status,
selection/reasons, raster limits, and recomputed summary; it never trusts a
supplied summary alone. Both input snapshots are hash-bound and rechecked before
private, atomic, create-only publication. Output files must not exist and must
be distinct from both inputs, including hard-link aliases. Metrics include no
transcription or critical-token text. Input limits are 64 MiB for the recovery
report and 16 MiB for references. Combined scoring work across both sides is
capped at 250 million alignment cells, with the existing per-record limits.

Exit `0` means a comparison was written without flagged coverage gaps or
regressions; `3` means the written comparison needs attention (regression,
unpaired/unreferenced selected pages, deferred/empty candidates, or no pairs).
Exit `2` means input or publication failure. Post-commit staging cleanup failure
is distinguished explicitly. **No exit code approves an OCR correction.**
Matching declared hashes do not authenticate the supplied references/report or
reverify the current PDF. Human source review and the canonical publication
gates remain required.

Try the fully synthetic contract example (no PDF or model needed):

```powershell
python tools/compare_ocr.py --recovery evaluation/ocr/comparison-recovery.json --references evaluation/ocr/comparison-references.json --output evaluation-reports/ocr-comparison-example.json
```

It deliberately improves one page but worsens a number on another: expect exit
`3` and a regression flag despite lower aggregate CER. The all-zero digest and
synthetic engine version are fixture identities, not claims about a real PDF.

## Compare two OCR runs directly

Add `--baseline-recovery` to compare plain versus processed OCR, or 300 versus
400 DPI, without manually copying candidate text or comparing unrelated sample
averages:

```powershell
python tools/compare_ocr.py --baseline-recovery output/ocr-review-300.json --recovery output/ocr-review-400.json --references private-references.json --output evaluation-reports/ocr-400-vs-300.json
```

The first report supplies baseline **candidates**; `--recovery` supplies retry
**candidates**. Both recovery reports are fully validated (v1, v2 or one of each),
must declare the same PDF hash and page count, and must match the reference
manifest's source hash. Native or prior `original_text` is not used for scoring,
so unavailable native extraction does not exclude a valid candidate pair.

The reference manifest remains the fixed evaluation scope. A page is scored
only when both runs have real candidate records. `coverage.unpaired_pages`
records each side's status: `candidate_available`, `empty_candidate`,
`retry_failed`, `deferred` or `not_selected`. A genuine empty candidate is scored
as empty text and flagged for review; a failed or absent retry is never replaced
with invented blank text. `coverage.baseline` and `coverage.retry` separately
list selected, failed, deferred, empty and unreferenced pages. No paired pages
means null metrics and exit `3`, not a perfect score. Missing difficult pages
cannot disappear from coverage when headline metrics improve.

Direct comparisons emit schema-v1 `ocr_run_comparison` reports, distinct from
the existing `ocr_recovery_comparison` kind. `paired_pages` maps page numbers to
metric record IDs; the nested `comparison` reuses CER/WER, per-page and critical
phrase regression flags, with **retry minus baseline** deltas and the same
combined scoring-work limits. Any regression, coverage gap, deferred or empty
candidate requires attention (exit `3`). Valid, complete comparisons without
these flags exit `0`; input/publication failures exit `2`. Neither exit code
accepts a correction or measures whole-document accuracy.

`run_settings` records each run's selection and retry settings, with v1
preprocessing normalized to `none`. `setting_differences` identifies changed
settings. Content-free `confounders` flags identify differing evidence bindings,
paired-page engine settings, and preprocessing library versions where both
paired candidates have v2 metadata. They report booleans/page numbers, not
arbitrary engine strings or transcription. Differences are allowed and shown
as review notices, not automatic failures: changing DPI or preprocessing is
the experiment. These notices are not an exhaustive reproducibility check;
unknown runtime differences can remain. A better score alone does not establish
which setting caused the change. Keep other conditions fixed and check the
held-out set before promoting an experiment.

The three input snapshots are hash-bound in `inputs` and rechecked before
private, atomic, create-only publication. All three inputs and the output must
be distinct paths/files, including hard-link aliases. Limits are 64 MiB per
recovery report and 16 MiB for references. The command runs no OCR, opens no PDF,
changes no input and emits no source transcription. Declared source hashes
still do not authenticate inputs or reverify the current PDF.

Try a fully synthetic direct comparison:

```powershell
python tools/compare_ocr.py --baseline-recovery evaluation/ocr/comparison-baseline-recovery.json --recovery evaluation/ocr/comparison-recovery.json --references evaluation/ocr/comparison-references.json --output evaluation-reports/ocr-run-comparison-example.json
```

Expect exit `3`: one candidate improves while another changes `30` to `80`,
despite lower aggregate CER. The baseline fixture deliberately has no native
extraction; its candidate text is still compared. These fixtures test the
comparison contract, not actual OCR engine accuracy.

## Reproducible synthetic scan challenge

Generate an actual image-only PDF, with references authored before rendering or
OCR, using the existing locked PyMuPDF, OpenCV and NumPy dependencies:

```powershell
# Create output/pdf first if that parent does not exist. The leaf must be new.
python tools/build_ocr_challenge.py --output-dir output/pdf/ocr-challenge-v1
```

The builder accepts no source document, custom text or external font. It does
not load an OCR engine, download models or change pipeline settings. Imports and
`--help` work without the image libraries. The eight fixed cases are:

| PDF page | Case | Controlled difference / declared reading order |
| --- | --- | --- |
| 1 | Clean | 12-point body text, black on white |
| 2 | Positive skew | Same text rotated +3 degrees |
| 3 | Negative skew | Same text rotated -3 degrees |
| 4 | Faint | Same text, grayscale ink 200 on background 230 |
| 5 | Small print | Same body text at 7 points; header/footer unchanged |
| 6 | Columns | Left column top-to-bottom, then right column |
| 7 | Table | Each row left-to-right, then the next row |
| 8 | Skew and faint | Same clean text with +3 degrees and faint tones |

Every reference includes the visible header and footer. Contextual critical
phrases include negation and row-specific numbers. All pages start as fixed
432-by-576-point layouts and are rasterized at 180 DPI. Retry rendering at 300
DPI does not invent additional source detail. The built-in Helvetica appearance
is tied to the recorded MuPDF renderer, with no external font-file dependency.

The new directory contains `challenge.pdf`, source-bound `references.json`, and
`manifest.json`. The manifest records the recipe/generator, PDF and reference
hashes, library versions, rendering parameters and case-to-page mapping. It is
written last, after bounded hash rechecks of the PDF and references. Observed
links/junctions and existing outputs are refused. A build failure or cancellation
can leave a partial directory; inspect it and use a new name rather than
overwriting or recursively deleting it. Exit `0` means the fixture was created,
`2` means build failure and `130` means cancellation. A manifest is a
completion-time consistency record, not a tamper-proof multi-file transaction.

The recipe is deterministic within the recorded environment. PDF serialization
suppresses fresh document IDs using PyMuPDF's documented
[`no_new_id` option](https://pymupdf.readthedocs.io/en/latest/document.html#Document.save).
Exact bytes across library upgrades or platforms are not promised; retain the
generated PDF and references to compare all modes on identical input. Record
the installed model-lock hash separately when running experiments.

Run all four modes with every page explicitly selected. The normal five-page
default would truncate this cohort, so **keep `--max-pages 8`**:

```powershell
$ocrFixture = "output/pdf/ocr-challenge-v1"
foreach ($ocrMode in @("none", "deskew", "contrast", "deskew-contrast")) {
    python tools/retry_ocr.py --pdf "$ocrFixture/challenge.pdf" --pages 1 2 3 4 5 6 7 8 --max-pages 8 --dpi 300 --timeout-seconds 120 --preprocess $ocrMode --output "$ocrFixture/$ocrMode.json"
    if ($LASTEXITCODE -notin @(0, 3)) { throw "OCR run failed; inspect the output before retrying." }
}
foreach ($ocrMode in @("deskew", "contrast", "deskew-contrast")) {
    python tools/compare_ocr.py --baseline-recovery "$ocrFixture/none.json" --recovery "$ocrFixture/$ocrMode.json" --references "$ocrFixture/references.json" --output "$ocrFixture/compare-$ocrMode.json"
    if ($LASTEXITCODE -notin @(0, 3)) { throw "OCR comparison failed; inspect the inputs." }
}
```

Comparison exit `3` preserves review findings; it is not a reason to exclude a
hard page. Inspect paired coverage, per-page CER/WER, critical-phrase counts,
configuration differences and skipped preprocessing steps. No improvement
threshold is built in and the fixture is not automatically tuned after seeing
results. Columns and tables measure transcription plus declared ordering;
whitespace-normalized CER/WER do not validate table structure or cell geometry.
This small ASCII synthetic set is a diagnostic challenge, not a held-out test
or evidence of real-textbook/multilingual accuracy.

The [recorded local observation](../evaluation/ocr/challenge-observation-v1.json)
on 2026-09-06 produced the same result in all four modes: 8/8 paired pages,
no failed/empty/deferred pages, 7 exact matches, CER 4.34% (147/3,386 characters)
and WER 5.75% (33/574 words). The two-column page alone had CER 50.87% because
the OCR output interleaved columns rather than following the declared order.
All 36 critical phrase occurrences were retained, illustrating that phrase
counts alone do not validate reading order. There was no measured preprocessing
gain on this cohort, and no post-result fixture or preprocessing tuning.
Comparison exit `0` / `requires_attention: false` means no *relative regression
or coverage gap*, not that this existing ordering error is acceptable.

This observation used the existing Windows Python 3.14.3 environment and local
RapidOCR 3.9.2, PyMuPDF 1.25.1, OpenCV 4.13.0 and NumPy 2.3.5. Those installed
libraries are not a verification of the committed locked environment; no
dependencies or model bytes were changed. The observation records the source,
references, recipe, generator and model-lock hashes, not a portable accuracy
threshold or a new CI acceptance gate. This finding motivated the separate,
operator-guided line-order experiment below; default OCR behavior is unchanged.

## Explicit column-order review

When the OCR words are correct but two columns are interleaved, propose an
ordering correction from the **saved OCR line boxes**, without rerunning OCR:

```powershell
python tools/reorder_ocr.py --recovery output/ocr-plain.json --plan private-column-plan.json --references private-references.json --output output/ocr-column-review.json
```

This creates a distinct `ocr_layout_review` artifact, not another recovery run.
It cannot be passed to `compare_ocr.py` as recovery JSON; its optional
`--references` comparison scores original candidate text versus the proposed
order directly. It does not open PDFs, load models, change recognition text,
modify either input, replace canonical extraction, or automatically accept a
correction. The new report contains sensitive original/proposed text and line
geometry, and is published privately to a new output path.

The plan declares only the pages to reorder. Example structure (replace both
digest placeholders with real lowercase SHA-256 values):

```json
{
  "schema_version": 1,
  "source_sha256": "<source_sha256 from the recovery report>",
  "recovery_sha256": "<SHA-256 of the exact recovery JSON bytes>",
  "coordinate_system": "candidate_raster_fraction",
  "pages": [
    {
      "page_number": 6,
      "body_band": [0.13, 0.43],
      "gutter": [0.40, 0.50],
      "order": "left_then_right"
    }
  ]
}
```

Obtain the exact report hash with
`Get-FileHash -LiteralPath output/ocr-plain.json -Algorithm SHA256` and lowercase
its hexadecimal value. Even reformatting that report changes its binding.
Plans allow 1..20 unique one-based pages. `body_band` is a top/bottom fraction
of the **current candidate raster height**; `gutter` is a left/right fraction
of its width. Values must be finite and strictly increasing; the body may
touch 0/1, but the gutter must be strictly inside them. The example coordinates
are for synthetic challenge page 6 only, not a template for arbitrary scans.
Inspect the relevant scan and saved boxes before declaring your own regions.
For preprocessed v2 reports these are *processed-canvas* fractions, not original
PDF coordinates. Each report requires its own hash-bound plan.

The algorithm keeps headers and footers outside the declared body in engine
order, then groups body lines from the left column before the right column.
Within-column engine order is preserved, not repaired. It requires at least
two lines per column and consistent nonoverlapping vertical spans. It abstains
on the whole page for a box crossing a body boundary or gutter, interleaved
header/footer groups, missing columns, overlaps/reversed lines, unclear box
geometry, or more than 2,000 lines. Convex, near-axis box checks use fixed edge
slope bounds recorded in `parameters`; they do not infer text orientation or
document semantics. Boundary-touching boxes may be assigned when they do not
cross a boundary. Unplanned pages keep their original order.

Each result retains `lines` in original order, original/proposed text, the
unchanged candidate raster declaration, and a **zero-based `line_order`**
permutation. No line is dropped, duplicated, merged, rewritten or rescored.
Use the bound recovery report for original engine/preprocessing metadata.
Failed, deferred and unselected planned pages remain explicitly unavailable,
not fabricated empty predictions; true empty candidates are scored as empty
text and flagged. Any ambiguous planned page keeps its original text.

With references, all available referenced candidates are compared, including
unplanned and abstained pages. Reference gaps, unreferenced selected/planned
pages, failed/empty/deferred candidates, abstentions, and per-page or critical
phrase regressions remain visible and require attention. Without references,
accuracy is unmeasured and exit `3` is expected. Exit `0` only means a comparison
was written without detected relative regression/coverage issues; it is **not
approval of the proposed order**. Invalid input/publication returns `2` and
cancellation returns `130`. Inspect output after any late error or cancellation;
a completed report or private staging file may already exist.

Input sizes are bounded at 64 MiB recovery, 1 MiB plan and 16 MiB references;
the existing reference and alignment-work limits also apply. All inputs and
output must be distinct, including hard-link aliases. Link-aware snapshots
and input hash rechecks precede private create-only publication. Hash bindings
check supplied-artifact consistency, not source or operator authenticity.

**This is not automatic column/table detection.** A two-column table can satisfy
the same geometry as prose. A wrong operator plan can therefore propose a wrong
order even when geometric checks pass. Only explicitly declared left-then-right
columns are supported; multi-column, right-to-left and mixed-band layouts need
separate review. Do not apply the example plan to every page.

The [recorded layout observation](../evaluation/ocr/layout-observation-v1.json)
binds the exact saved reports, plans and derived reviews. On the already
diagnosed eight-page synthetic cohort, separate plans bound to
each of the four saved runs reordered only page 6. All four derived reviews
then matched 8/8 references: CER 4.34% to 0% and WER 5.75% to 0%, with the other
seven pages and every original line/score/box unchanged. No OCR was rerun.
This is an operator-guided correction of the known calibration case, not a
held-out result, recognition improvement, or evidence that arbitrary tables
and columns are understood. The original preprocessing observation and
reference transcriptions remain unchanged.

## Reviewing uncertainty without guessing

The uncertainty-aware crop panels are available when the authenticated review
launcher enables crop execution or an existing private `--crop-review-pack-dir`.
The pack-directory option enables Save/Open, not OCR execution. Existing saved
declarations never restore current approval.

1. Open a fresh original crop. Choose **Annotate uncertainty**, then drag a
   rectangle or explicitly select the entire requested crop. Keyboard users can
   focus the overlay, move with arrows (Shift moves ten pixels), and mark two
   corners with Enter/Space. Escape cancels; Tab leaves the overlay.
2. Choose an uncertainty kind, enter a reason and **Apply**. A selected rectangle
   is only pending until Apply. Tentative text is a hypothesis, not reference
   truth. Do not invent missing words to make the reference scorable.
3. Prepare the complete declaration, inspect it, then confirm and Review.
   **Any unresolved region makes the whole crop unscorable.** Coverage remains
   visible and the review can still be saved; absent scores are not zero errors.
4. To resolve an annotation, select it, choose Resolve, supply the explicit
   decision/reading and reason, and Apply. Prepare and confirm again before
   scoring. A confirmed reading must already appear in your full transcription;
   no OCR text is copied into it automatically.

Save creates a new immutable revision. Reopening restores history and draft text,
but needs fresh inspection and confirmation. Text edits, including changes away
and back, reopen resolved annotations in a dirty draft; Apply or Prepare records
the reset. Detail reload preserves source-coordinate annotations and authored
text. An edit processed by the server before an in-flight action's final commit
check invalidates that action; it cannot undo an already-published revision.

The [development evidence](evidence/2026-09-08-ocr-uncertainty-ui-development.md)
includes focused checks and a generated browser save/reopen/resolve journey.
It does not establish representative OCR accuracy or full-project qualification.

## Next experiments

Use the same references to compare 300 versus 400 DPI, then selective deskew,
contrast cleanup or a genuinely independent engine. Retain the original scan
and transcription for every experiment. Promote a change only after measuring
the held-out set and checking critical-token context and provenance. These tools
supply measurement and reviewable retries; they do not claim an
accuracy improvement on real textbooks.
