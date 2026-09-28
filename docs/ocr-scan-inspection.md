# Independent source-pixel inspection

This local, model-free workflow discovers bounded dark-ink hypotheses directly
from original PDF page pixels. Saved OCR, Docling proposals and references do
not guide discovery or mask out areas. Optional saved geometry is compared only
after discovery. The result is a review aid, not verified text or a certificate
that a page is complete.

Independent native, historical-reader and browser review plus the
[Phase 7 local qualification](evidence/2026-09-07-ocr-scan-review-qualification.md)
passed. Generated-fixture checks do not establish representative omission
recall or false-alarm rates. Use generated fixtures until the owner approves
representative sources and reference/annotation retention.
That checkpoint covers the default `legacy-v1` recipe. The opt-in `spatial-v2`
extension below has separate
[Phase 8 local qualification](evidence/2026-09-07-ocr-spatial-scan-qualification.md):
all nine gates, 8,960 passing tests and independently reviewed generated
native/CLI/historical-replay evidence. Phase 7 alone does not qualify v2;
neither checkpoint establishes representative accuracy or a total memory bound.

## Run

Use the qualified locked Python environment; do not install or download models.
The command checks the existing PyMuPDF, OpenCV and NumPy package versions
against the full lock. It does not load an OCR recognizer.

```powershell
python tools/inspect_ocr_scan.py `
  --pdf output/pdf/ocr-challenge-v1-verified/challenge.pdf `
  --pages 1 2 3 4 5 6 7 8 `
  --output-dir evaluation-reports/my-new-scan-bundle
```

The parent directory must exist; the bundle directory must be new. Optional
`--recovery` and `--proposals` bind existing saved comparison inputs; proposals
require their exact recovery. Optional `--installation` verifies existing local
installation evidence, without acquiring models or changing the environment.

### Opt-in dense-page recipe

The default remains `--recipe legacy-v1`, including its historical all-pairs
admission limit. To select the new recipe explicitly, add `--recipe spatial-v2`
and use a new output directory:

```powershell
python tools/inspect_ocr_scan.py `
  --pdf output/pdf/ocr-challenge-v1-verified/challenge.pdf `
  --pages 1 2 3 4 5 6 7 8 `
  --recipe spatial-v2 `
  --output-dir evaluation-reports/my-new-spatial-scan-bundle
```

Both recipes use the same original raster, dark-Otsu threshold, morphology,
component admission and final glyph-group classification. Spatial v2 uses a
128-pixel grid to enumerate candidate neighbors in the same canonical pair
order. It does not run recognition, improve the source image, establish that
ink is text, or treat a newly available dense page as accurate OCR.

The intentional admission change is that v2 limits actual predicate calls,
not every possible unordered glyph pair. Each page remains bounded at 10,000
components, 100,000 index entries, 140,000 bucket lookups, 10 million bucket
visits, one million predicate calls and 5,000 output regions. Source raster and
eight-page cohort limits remain unchanged. These work counts are not a total
native-process memory bound.

An exact configuration identifies each recipe. Observation schema 1 accepts
only legacy v1; observation schema 2 accepts only spatial v2. V2 adds the
classified glyph count, spatial work counters and the stage reached. Exhaustion
retains measured work with an unavailable page, empty regions and incomplete
foreground accounting; it never publishes partially grouped regions as success.
Unknown, mixed or edited recipes are refused, including during fresh readback.
The existing diagnostic and bundle schemas remain version 1 and bind the entire
versioned observation. Historical v1 bundles are not rewritten or upgraded.
Work-counter consistency does not authenticate execution or source pixels.

The CLI supervises the native worker with a default 600-second deadline
(`--timeout-seconds` accepts 1 through 3600). Native output is suppressed and
failures use static messages. Exit **3** means a completed, independently read
back manual-review bundle, even if there are no warnings. Exit 2 is failure,
124 is deadline and 130 is cancellation. An interrupted operation can leave a
complete or incomplete private directory: inspect it and choose a new output
directory for another attempt. Do not delete earlier evidence blindly.

The fixed files are `scan.json` (observations), `diagnostics.json` (saved-box
comparison) and `manifest.json` (published last as the completion marker).
Fresh completion readback rechecks exact source, optional saved inputs, recipe,
interpreter and explicitly listed producer/lock generations, plus all artifact
bytes and derived diagnostics. Recorded package versions and local hashes are
not loaded-native-byte attestation or authenticated proof of execution.

## Bounds and interpretation

- At most eight explicitly selected pages, from a source of at most 5,000
  pages and 256 MiB. Unrequested, failed and unavailable work stays explicit.
- Fixed 300 DPI grayscale, displayed CropBox and intrinsic quarter-turn
  rotation, including rendered PDF annotations. No rescaling or preprocessing
  guesses before discovery. Annotations can add or obscure ink.
- At most 6,000 pixels per side, 25 million per page and 100 million across
  the requested cohort. Cohort raster admission precedes every render.
- Horizontal-run, component, pair and output-region budgets precede expensive
  later work. A limit retains an unavailable page, not a truncated success.
- Successful observations account for all foreground pixels from the declared
  global dark-Otsu threshold, including rule-like and ambiguous components.
  Threshold-blank does **not** mean there is no text. Pale, colored and reversed
  text remain important unmeasured limitations.
- Text-like grouping is not semantic text detection. Aligned non-text blocks
  can trigger it; tiny or isolated text may remain ambiguous. Overlap with an
  OCR box is not proof that the right characters were recognized.
- Preprocessed or incompatible saved coordinates cause comparison abstention;
  they never erase the independently observed ink regions. All source roles
  and transcription accuracy remain unverified.

## Use in the review editor

Add `--scan-bundle evaluation-reports/my-new-scan-bundle` to the authenticated
[review editor](ocr-review.md) launch. This path is fixed by the operator and
blocked from direct file serving. No browser action can choose another file.

The historical reader verifies the complete bundle and original diagnostic
bindings, but does not require today's producer code, locks or installed
environment to match historical declarations. If the bundle originally used
saved OCR/proposals, those exact inputs must be available in the workspace.
A source-only bundle can instead be compared with the current saved inputs in
a **separately rebuilt** review diagnostic; original artifacts are unchanged.

The independent panel is a separate original-page grayscale preview. Before
exposing an overlay or crop, it reproduces the declared 300-DPI gray raster,
checks its exact pixel digest and geometry, then makes a display thumbnail.
It never draws original-space hypotheses on a preprocessed candidate canvas.
An unreproducible raster disables that panel's crop actions without turning it
into a blank-page success.

Select a hypothesis, inspect its original scan outline and explicitly add it
to the crop plan. Rule-like and ambiguous regions remain available for review.
This only adds a draft crop: no OCR runs, text changes or approval follows.
The existing separate crop-export confirmation still applies. Captured page,
region focus and annotation generation must match; stale queued actions fail
without mutation or export. Save a review draft to retain the crop; restart
restores neither transient scan focus nor approval.

Source and all three bundle files are rechecked on review actions. A pixel
digest match establishes correspondence with this rendered source, not the
correctness of the region classification, a human review, or complete OCR.
