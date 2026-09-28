# Explicit hard-scan OCR experiments

This opt-in workflow creates a separate private review report. It never changes
the PDF, saved page OCR, canonical extraction, or an index. Existing recovery
v1/v2 and region v1 formats remain unchanged. Every successful command exits 3
because independent candidates still require manual review.

It supports three deliberately limited operations:

- An explicitly selected clockwise quarter turn: 0, 90, 180, or 270 degrees.
  There is no automatic orientation selection or four-run confidence contest.
- Conservative uneven-background luminance normalization. This is not CLAHE,
  thresholding, sharpening, or guaranteed recovery of faint characters.
- An operator-declared one-dimensional quadratic bow correction. This handles
  a shared vertical bend across otherwise parallel horizontal baselines. It is
  **not general page dewarping**, perspective correction, or recovery of hidden
  text at a fold.

The order is fixed: orientation, bow, illumination. Recipes are frozen before
recognition, not chosen by examining reference error rates or model confidence.
The existing RapidOCR text-crop 0/180 classifier is not evidence of correct
global page orientation.

## Create a bound plan

First inspect the original scan. All crop fractions refer to its original
displayed PDF page, including intrinsic rotation and cropbox. They never refer
to a previous deskewed candidate canvas. `[0, 0, 1, 1]` selects the full page.

Use `create_plan` to bind recipes to a strict saved recovery report without
manually copying hashes. Choose new output names; existing files are refused.
This API reads only the saved recovery JSON, not a PDF or an OCR engine.

```python
from pathlib import Path
from ocr_hardscan_io import create_plan

create_plan(
    Path("evaluation-reports/page-recovery.json"),
    Path("evaluation-reports/hardscan-plan.json"),
    [{
        "region_id": "page-12-sideways",
        "page_number": 12,
        "bbox": [0, 0, 1, 1],
        "recipe": {
            "orientation_clockwise": 90,
            "illumination": "none",
            "bow_fraction": 0.0,
            "bow_assumption": "none",
        },
    }],
    confirmed=True,  # only after reviewing the recipe against the scan
)
```

Confirmation is a caller declaration, not authenticated proof of who reviewed
the page. Plans permit 1..20 uniquely identified crops; IDs and page numbers are
validated and sorted. Full strict fields are:

```json
{
  "schema_version": 1,
  "kind": "ocr_hardscan_plan",
  "source_sha256": "<64 lowercase hex characters>",
  "recovery_sha256": "<exact saved recovery file SHA-256>",
  "coordinate_system": "original_page_display_fraction",
  "approval": "operator_approved",
  "regions": [
    {
      "region_id": "page-12-sideways",
      "page_number": 12,
      "bbox": [0, 0, 1, 1],
      "recipe": {
        "orientation_clockwise": 90,
        "illumination": "none",
        "bow_fraction": 0.0,
        "bow_assumption": "none"
      }
    }
  ]
}
```

`illumination` is either `none` or `background-normalize-v1`. A nonzero
`bow_fraction` requires `bow_assumption: "parallel_horizontal_baselines"`.
Never assert that assumption for tables, independently curved columns,
perspective distortion, occlusion, or a complex photographed book gutter.

## Run and inspect

```powershell
python tools/retry_ocr_hardscan.py --pdf source.pdf --recovery evaluation-reports/page-recovery.json --plan evaluation-reports/hardscan-plan.json --output evaluation-reports/hardscan-review.json --dpi 300 --timeout-seconds 120
```

The only supported render DPIs are 300 and 400; there is no silent downscale.
The optional whole-worker timeout is recommended. Startup/cleanup time is
additional. Supervised native output is suppressed; the parent validates the
completed report against the requested DPI and current source/recovery/plan
generations before printing counts.

Exit statuses: 3 means a review report was created; 2 means invalid inputs,
dependency/publication failure, or postcommit cleanup trouble; 124 means the
worker deadline; 130 means cancellation. After any interrupted operation,
inspect the output before retrying: a complete report or private temporary
files may remain. Timeout is not a successful partial result.

The report kind is `ocr_hardscan_review`, schema 1. It contains:

- The exact plan, input hashes, and one complete strict saved recovery report
  as baseline context. That context is not crop ground truth.
- Original crop geometry, PDF display/cropbox/rotation evidence, and fixed
  transform metadata for each planned region.
- Raw OCR-engine text and quadrilaterals in `hardscan_image_pixels`, plus
  separately mapped source polylines in original-page-display fractions.
- Explicit `review_required`, `empty_candidate`, `retry_failed`, or `abstained`
  status. Failures/abstentions never substitute an invented empty OCR result.
- Image-processing/library observations and fixed reasons for skipped lighting
  normalization or bow abstention. Engine order is preserved; this workflow
  does not infer columns, tables, or reading order.

Use `ocr_hardscan_io.load_report` for strict loading. Do not feed this distinct
format into recovery v1/v2 comparison tools or the existing affine-only review
editor. A dedicated consumer must honor nonlinear source polylines. For manual
inspection, compare the original scan, raw candidate text, recipe and mapping;
do not infer acceptance from a high mean OCR score.

## Exact geometry and bounded resources

Coordinates use pixel edges: an array sample at index `(x, y)` has center
`(x+0.5, y+0.5)`. Quarter turns are exact pixel permutations, with validated
forward/inverse matrices. For clockwise 90 degrees, edge coordinates transform
as `(x', y') = (height-y, x)`. PDF intrinsic rotation remains separately recorded.

After orientation, let the raster be `W` by `H`, `u=x/W`, and
`A=bow_fraction*H`. The only bow model is:

```text
forward: x' = x
         y' = y - 4*A*u*(1-u) + max(0,A)
inverse: x  = x'
         y  = y' + 4*A*(x'/W)*(1-x'/W) - max(0,A)
```

Its Jacobian determinant is one; the inverse is analytic, not a guessed inverse
mesh. The expanded white canvas is `W` by `H+ceil(abs(A))`, retaining the source
footprint. This geometric property does not make interpolation lossless or
prove that the page actually follows the assumed physical model.

The absolute bow fraction is at most 0.025 and maximum slope is 0.25. Requested
nonzero corrections below half a pixel, excessive slope, or oversized expanded
canvases abstain. A conservative thumbnail text-like-component gate also
abstains on blank/low-ink and non-text-like images. This gate cannot reliably
classify all tables, diagrams, columns, or curved-page applicability; the
operator declaration remains essential.

Straight OCR edges generally map to curves. Source polylines split first at
analytic intersections with the source crop and physical page boundaries, then
subdivide using the quadratic interpolation bound: at most 32 segments per edge
and at most 0.5 original-crop-pixel approximation error. Splitting before clipping
avoids unaccounted interpolation kinks at padded edges. Padding-only detections
with no positive-area source support are rejected as a validation failure.
Do not replace these polylines with only four transformed corners.

Each original and processed raster is limited to 6,000 pixels per side and
25 million pixels. All regions are preflighted before any rendering/model call.
Aggregate planned work counts original plus eligible processed pixels and is
limited to 100 million. This is a conservative work budget, not actual rendered
pixels or an operating-system memory quota. Nonlinear maps and luminance float
work use 128-row strips; there are no full-resolution float displacement maps.
Source/output images and OCR model memory still consume additional RAM.

Illumination uses a thumbnail of at most 768 pixels per side, a fixed 31-pixel
closing/blur background estimate, 10th/90th background histogram percentiles,
and a maximum 2x gain. It skips low-ink or unreliable estimates, nearly uniform
backgrounds, overly dark backgrounds, and excessive proposed gain. Applied
normalization produces grayscale replicated to BGR: color information can be
lost, and clipping/resampling are not reversible. Review colored annotations,
fine punctuation, small print and faint text explicitly.

The runtime uses existing byte-verified RapidOCR models and bounded session
threads, with no downloads or global OpenCV thread-setting changes. Input,
plan, and recovery hashes are rechecked immediately before private create-only
publication. Input JSON limits are 64 MiB for recovery and 1 MiB for plans;
output is capped at 128 MiB. OCR output is capped at 1,000 lines per region;
mapped geometry is limited to 20,000 vertices per region and 100,000 across a
report before retaining expanded output. New files and existing closed schemas
are separate.

## Evidence and limits

Synthetic tests cover quarter-turn physical content and intrinsic PDF rotation,
analytic inverse/expanded-canvas geometry, curved-edge approximation, source
immutability, illumination gradients, abstention, malformed metadata, budgets,
supervision, stale results and publication races. Generated challenge experiments
must record fixed recipes, original input hashes, complete case coverage,
CER/WER and critical-token results, including unchanged or regressed cases.

Known synthetic deformation parameters are an operator/oracle-assisted
mechanics experiment, not automatic parameter estimation or held-out book
accuracy. Keep related synthetic variants in the same document-family split.
Do not tune recipes on held-out references or promote canonical text from these
reports. Actual engine observations can be captured before reader close through
`execution_observation()` and bound to the resulting report using
`ocr_execution_receipt.capture_execution_receipt`; these are local consistency
receipts, not signed model-session attestation or accuracy certification.

### Generated mechanics check, 2026-09-06

The ambient verified RapidOCR 3.9.2 runtime completed a two-crop generated-PDF
workflow: two candidates, no empty/failed/abstained results, 17,280,000 planned
input-plus-output pixels. The supervised CLI exited 3 and its report exactly
matched the API report. The source PDF, saved recovery and frozen references
were unchanged. Actual receipts recorded two workflow calls and six paired
image-experiment calls, all completed, with CPU execution providers on all three
constructed ONNX sessions. This ambient environment is not the locked runtime.

Three recipes were fixed before their paired OCR calls. All images came from
generated challenge page 1; there were no private document inputs. The bow
distortion used independent integer column placement rather than the correction
remapper. Parameters were known synthetic deformations, not automatically
estimated from an unknown page.

| Generated condition | Baseline CER | Corrected CER | Baseline WER | Corrected WER |
| --- | ---: | ---: | ---: | ---: |
| Sideways 90-degree page | 77.76% | 0% | 95.12% | 0% |
| Faint text with uneven lighting | 0% | 0% | 0% | 0% |
| Shared quadratic bow | 0% | 0% | 0% | 0% |

The sideways improvement includes restored reading order. All 15 critical-token
occurrences were present on both sides; occurrence counts alone did not expose
the sideways ordering error. Lighting and bow produced no measured accuracy
gain in these examples. Their transformed images were visually inspected for
retained text and expected lighting/baseline changes. These checks establish
mechanics, not representative accuracy or permission to apply recipes broadly.

Local ignored evidence is in `evaluation-reports/ocr-hardscan-v2/`: the review,
execution receipts, source-bound plan and paired image metrics. Review/CLI SHA-256:
`a70222ca5af26576133a904526d3c36d5cd9f558172f6a8d5a1ce73fecfc09a4`.
Paired metrics SHA-256:
`e8f3c36edbfe29fe6b7a61bb09645a939695c1251909c0c892f4b703a1dba996`.
