# OCR stage diagnostics

This opt-in benchmark separates postprocessed detector geometry, ordinary
full-page OCR, and recognition on declared gold **line** crops. It does not
replace extraction, correct text, certify annotation completeness, or establish
representative OCR accuracy.

Use it to investigate whether a reviewed example has missing, merged, split,
weakly aligned, or out-of-order detections, and whether recognizing an exact
gold line crop produces a different error rate. Recognition-only crop results
and full-page results are not an additive causal decomposition: crop context,
normalization and classifier use differ, and detector-only/full-page calls are
separate invocations on the same original raster.

## Inputs and invocation

Use the qualified interpreter and its matching completed installation evidence:

```powershell
& $qualifiedPython tools/diagnose_ocr_stages.py `
  --pdf "generated/challenge.pdf" `
  --references "generated/stage-reference.json" `
  --installation "qualified/installation.json" `
  --output-dir "evaluation-reports/new-stage-bundle" `
  --timeout-seconds 600
```

These are illustrative paths, not a request to open a private document. Obtain
approval before supplying a real corpus. The output directory must not exist;
its parent must exist. The CLI runs a contained worker with native output
suppressed, fixed offline environment settings, and a 1–3600-second deadline.
The approved local RapidOCR package models are byte-verified through the
existing model policy. No engine, model, dependency or preprocessing change is
authorized by this command.

Exit 3 means a verified bundle requiring review, **not** an accuracy pass. Exit
2 means invalid input, failed execution or failed verification; 124 is timeout
and 130 is cancellation. Any late failure can leave a complete or incomplete
private bundle. Inspect it and choose a new directory before retrying. This
workflow has no checkpoint/resume behavior and never overwrites a generation.

## Gold reference contract

`ocr_stage_reference` schema 1 binds the exact original `source_sha256` and
`page_count`. Its coordinate system is `original_page_display_fraction`:
top-left-origin `[left, top, right, bottom]` fractions of the displayed page,
including its intrinsic quarter-turn rotation.

Each selected page contains observed-source-compatible
`geometry: {width_points, height_points, rotation}`, ordered `lines`, separate
`cells`, and an explicit `recognition_region_ids` list. A line has a globally
unique safe `region_id`, nonempty raw `text`, `bbox`, zero-based canonical
`order`, and nullable `cell_id`. A cell has a unique `cell_id`, `bbox`,
`table_id`, `row`, `column`, `row_span` and `column_span`. Lines assigned to a
cell must lie wholly inside it. Cells in the same table cannot overlap in
physical or grid geometry. A multiline cell needs separate reviewed line boxes
and order; the whole cell is not treated as one recognizer input. Empty cells
are permitted but cannot produce a perfect text score without reference text.

The scope is `operator_declared_complete_selected_pages`. Provenance is
`operator_declared` or `synthetic_generator`, neither authenticated approval.
Include headings, footers and other text when declaring a whole page complete.
There is deliberately no partial-page “complete” declaration in version 1.
Generator-position annotations can test mechanics but are not independently
reviewed, representative held-out evidence. Freeze gold before examining the
candidate output; use the existing cohort split policy for later held-out work.

## What is actually observed

The stage adapter renders each selected original page once at 300 DPI. It
validates actual page count/geometry before model calls and checks the rendered
RGB layout before producing a contiguous BGR array. Intrinsic rotations are
supported; deskew, contrast, nonlinear hard-scan mappings and effective-input
preprocessing are outside this recipe.

For each rendered page, the actual schedule is detector-only, full-page OCR,
then the explicitly selected gold line crops in reference order:

- Detector-only explicitly passes `use_det=True, use_cls=False, use_rec=False`.
  The observation is postprocessed, pre-recognition detector quadrilaterals,
  not raw logits. The installed engine still constructs rectified crops on
  this route, so elapsed time is not pure detector-kernel time.
- Full-page explicitly passes all three flags as true. This is a fresh
  observation, not an existing recovery report relabeled as detector evidence.
  RapidOCR discards boxes with blank recognized text in its ordinary output.
- Each gold crop is sliced at exact outward integer bounds from that same
  raster and passes `use_det=False, use_cls=False, use_rec=True`. This genuinely
  bypasses detection and classification at the public component boundary, but
  still includes RapidOCR image preprocessing and recognizer normalization.
  Recognition-only text does not acquire a fabricated crop-sized detection box.

All flags are supplied on every call because the installed engine remembers
previous flag values. Actual outer invocation IDs join exactly to the existing
execution receipt. Additional component wrappers count actual entry, completion
and failure of detector/classifier/recognizer calls. An outer call can complete
while a component failed and its exception was swallowed upstream; that work
remains unavailable. Explicit completed empty recognition retains the empty or
whitespace raw string and is not confused with a missing result.

The shared guard's setup and restoration are outside actual-call timing. Setup
failure has no call ID. A normally returned raw call followed by guard/output
validation or cleanup failure keeps its completed call evidence but cannot
yield an accepted candidate. With no failed component, this is recorded as
`unavailable` / `invalid_stage_output`; allocation-limit failures retain
`resource_limit`, and actual failed calls/components retain `stage_failed`.
The existing strict observation join remains unchanged. Every hook restoration
is attempted with recorder ownership reserved; cancellation propagates, and
failed restoration requires fresh guard or recorder ownership rather than a
silent retry through partially restored hooks.

Detector scores are deliberately omitted. The installed detector sorts boxes
without visibly applying the same permutation to its score list; this adapter
makes no per-box score-alignment or calibrated-confidence claim.

Page and crop `pixel_sha256` values are **array-content digests**, not file
hashes: SHA-256 of `ocr-stage-bgr-uint8-v1` followed by NUL, unsigned big-endian
32-bit width and height, then contiguous BGR uint8 bytes. Reference/source and
bundle artifact SHA-256 values, by contrast, bind exact file bytes.

## Bounds, scoring and abstentions

The fixed admission limits are 8 selected pages, 200 gold lines per page,
1,000 total lines, 64 selected recognition crops and 2,000 observed boxes per
page: at most 80 actual outer OCR calls. Full rasters are bounded to 6,000
pixels per side, 25 million pixels per page and 100 million aggregate pixels.
The entire page raster plan is admitted before rendering/model work. Rectified
detector crops are bounded before their allocation. The installed Global
minimum-side resize, vertical padding and detector minimum-side resize are also
preflighted arithmetically before native calls; a thin input can otherwise grow
dramatically despite a small original pixel count. Their effective settings
must match the fixed recipe. Recognition inputs, both
full-page and gold crops, must fit the fixed normalization recipe
`[3, 48, 320]`, batch size 6, and maximum normalized width 4,096. This guards
extremely thin/wide crops whose tensors could otherwise exceed pixel-based
expectations. These are resource limits, not learned accuracy thresholds.

Geometry matching uses convex quadrilateral/rectangle intersection, not text
similarity. It reports bilateral unique matches, missed or extra geometry,
possible merges/splits, weak matches and reading-order inversions with explicit
coverage. Separate cell containers group reviewed line assignments; this is not
an evaluation of predicted table structure. CER/WER uses existing metric
normalization and bounded alignment, separately for full pages, available gold
crops and exactly paired full-page lines/crops. Missing, unavailable,
ambiguous, unselected or over-budget work is exposed instead of silently being
dropped into a perfect score. Shared total alignment limits can cause an
explicit metric abstention while preserving observation coverage.

Geometry/candidate agreement does not establish correctness. A full-page line
may exist but have the wrong text. Gold-crop recognition may improve simply
because the crop provides a different input. Use these diagnostics to select
the next controlled experiment, not to claim every error has a unique stage
cause or that all source text was captured.

## Publication and independent readback

The private bundle contains `observation.json`, `execution.json`,
`diagnostics.json`, and the final `manifest.json` completion marker. It does not
retain PDF copies or rendered images. Source PDF bytes are read with bounded,
same-handle double-pass hashing and supplied as an immutable in-memory stream
to the renderer. All fixed inputs must be distinct, single-linked regular
files, with source/reference limits of 256 MiB/4 MiB. Link checks are not an OS
component-pinned no-follow security guarantee.

The manifest binds exact source/reference/installation/lock/model-policy hashes,
configuration and the conservatively captured interpreter/producer generation,
plus each output file hash. Every final publication rechecks inputs, generation,
previous output files and the new staged file against its intended bytes/size.
Outputs are create-only. Readback validates the unchanged execution-receipt
contract, joins actual calls, rebuilds all diagnostics, and rechecks artifacts
again before returning. These are local consistency checks, not signed evidence,
immutable ONNX-session byte attestation, or native-library/wheel attestation.

The read-only API supports a fresh process:

```python
from ocr_stage_io import stage_request, read_stage_completion

request = stage_request(pdf_path, reference_path, existing_output_dir,
                        installation_path=installation_path, readback=True)
bundle = read_stage_completion(existing_output_dir, request=request)
```

`readback=True` requires an existing safe directory and cannot execute or resume
OCR. `run_stage_bundle` is the worker primitive; callers needing containment
must use the CLI. A different immediate root/tools Python entrypoint is rejected
before OCR because the existing receipt names that entrypoint generically.
The fixed CLI, or an external API host without a captured first-party entrypoint,
preserves the exact producer binding. Changing the captured producer generation
or interpreter means old bundles cannot be verified as that new generation;
retain the original qualified environment/source checkout for reproduction.
