# OCR engine allocation safeguards

Local implementation in progress; this change is intentional safety hardening,
not a behavior-preserving extraction or a representative accuracy improvement.
The existing page, region, hard-scan and stage readers now share a pure shape
policy and an instance-local adapter for the pinned RapidOCR 3.9.2 recipe.
The final guard and raw-dispatch accounting repair passed generated native
comparison, independent review and the
[Phase 9 local checkpoint](evidence/2026-09-07-ocr-engine-guard-qualification.md):
9,387 tests and all nine selected gates. This is local reliability and
compatibility evidence, not representative OCR accuracy or total-memory safety.

## What is checked

- Original BGR image shape and the configured reader raster limits.
- RapidOCR's minimum-side enlargement, vertical padding and detector rounding.
- Detector quadrilaterals before rectification, individual crop dimensions and
  aggregate crop pixels.
- Actual crops before classifier copies and recognizer normalization, including
  the sorted six-image batch shapes and session input tensors.
- Final crop remapping before resize, including crops rotated by rectification.

The default per-image limits remain 6,000 pixels per side and 25 million pixels.
Page and region readers retain their existing configurable limits up to 12,000
per side and 100 million pixels. Hard-scan retains its stricter existing policy;
the shared leaf does not expand its supported settings. Aggregate crop pixels
are capped at 100 million. Recognition normalization retains the stage recipe's
conservative ceiling of 4,096 pixels wide; classifier and recognizer batches
remain six. No DPI, model, score threshold or preprocessing setting is silently
changed to make an input fit.

These are bounds on selected image and input-tensor allocations. They are **not
a total process-memory guarantee**: model weights, decoder work, arbitrary
model outputs and other native intermediates remain outside this contract.
Existing process containment and deadlines still matter.

## Failure behavior

Unprocessed page and region dimensions are checked before model loading or
rendering. Hard-scan uses its declared processed geometry before rendering.
For page preprocessing, the additional OCR check uses the actual validated
post-transform dimensions: deskew can change whether a thin raster is admitted.
Existing source/preprocessing raster checks remain in force.

Each reader calls the adapter's `prepare` before entering an actual-call
recorder. The guard repeats validation and finishes installing its hooks before
handing a single-use, same-thread raw-dispatch closure to the known recorder.
Rejection during either preparation or setup is not an OCR invocation. An
exception escaping raw dispatch records a failed call. If raw dispatch returns
normally but the guard subsequently rejects a suppressed allocation failure or
cannot restore hooks, the raw call remains completed and its candidate fails.
Neither case becomes an empty successful candidate. Timings include in-call
checks, but exclude guard preparation/setup/restoration.
Region and hard-scan planning descriptions remain available; allocation checks
belong inside each retry so one failed item need not abort its peers.

Allocation failures are `ValueError`-derived and use the existing per-item
`retry_limit_or_validation` classification. Stage diagnostics use
`resource_limit` and retain a call ID when a call actually began. Unsupported
engine settings are separate runtime failures. The adapter restores its local
hooks after each call; a restoration failure makes that engine unusable rather
than allowing another call through a partially restored instance.

## Conservative boundary abstention

Rectification uses a small float32 rounding margin before any native warp.
This can reject a near-cap crop whose exact native dimensions would fit. One
reviewed float32 quadrilateral produces an exact 6,000 by 10 crop, while the
conservative upper width is 6,001. That abstention is intentional and covered by
a regression test. Do not describe this policy as accepting every previously
within-cap input; unchanged-output claims apply only to inputs it admits and
to the comparison evidence actually collected.

## Evidence so far

The pre-change shape probe intercepted oversized requests before native
allocation. For example, the installed Global helper requested 180,000 by 32
pixels for a 6,000 by 1 input. This is an observed allocation request, not an
out-of-memory experiment or an allocation that was allowed to occur.

The generated-fixture native baseline completed 12 actual cached-engine calls:
eight ordinary pages, two regions and two explicit hard-scan orientations.
Complete candidates, exact BGR input digests and model/session observations are
retained in the local create-only bundle:

- `evaluation-reports/ocr-engine-guard-native-prechange-v1/manifest.json`
- Manifest SHA-256: `31202cbb9b08c8d4c256a44af77e9d886890a2c7eb5c63c011ad4210a3f0089c`

Focused tests cover malformed shapes, bounded iteration, preflight versus
actual-call accounting, transformed rasters and source identity coverage.
The integration fixture explicitly checks its requested PDF dimensions:
MuPDF replaces a sub-point page dimension with a 1 by 1-point page, which is
not a valid substitute for a thin-page test. Corrected tests use one-point
thin pages and verify the geometry before exercising the reader.

The post-change replay completed the same 12 calls through the real guard, with
12 explicit pre-counter preparations. All three complete candidate JSON files
are byte-identical to their pre-change counterparts. This includes text, scores,
boxes, crop/transform geometry and exact BGR input bindings. Model/session
configuration and runtime metadata also match. Measured durations are retained
separately and excluded from equality checks, not an accuracy or speedup claim.

- `evaluation-reports/ocr-engine-guard-native-postchange-v1/manifest.json`
- Manifest SHA-256: `f542127cf12d5afb94270dbfb1921c6135d253c4f4fc7d96e68334a8bc1d8e78`
- Comparison SHA-256: `60166a3313f2af8e12fb56bda0ccc150b42329add067887e09b105f9f01cbbf8`

Independent code review exposed and closed a cleanup/cancellation composition
defect in the new adapter: an ordinary hook-restoration error could mask an
active `KeyboardInterrupt` or `SystemExit`. Cancellation now remains cancellation,
all restorations are attempted, and an incompletely restored guard is poisoned.
These new tests do not retrospectively qualify the earlier guard draft.

Anscombe independently audited both helpers and 60 bound files, including the
seven post-change artifacts, all six baseline artifacts, current producer and
installed-helper pins, source/model files and execution-receipt bindings. The
audit compared complete candidate bytes and runtime/session fields directly;
it did not use the producer's pass booleans as proof. Ordinary candidates retain
118 lines across eight records; region and hard-scan candidates each retain 24
lines across two records. This read-only audit did not rerender pixels or rerun
OCR. Prepare ordering and input immutability are assertions of the reviewed
producer, not a separately retained native trace.

The shared policy was independently reviewed by Peirce, the adapter by Anscombe,
and reader wiring by Socrates. A combined qualified-Python run passed 570 focused
OCR/legacy/identity tests; 22 architecture-boundary tests also passed. Region,
hard-scan and page cohort tests cover per-item failures, later successful peers,
unchanged source/context, truthful invocation counts and cancellation without
publication. These selected tests are not the complete repository suite.

A broader run of all OCR-named test modules subsequently passed 4,580 tests
with five deprecation warnings. Late independent failure injection nevertheless
found a missing case: guard setup can fail before raw RapidOCR dispatch while
the existing timing/stage wrapper still records one failed call. The candidate
is rejected, but `ocr_executed`/call provenance would be overstated. At that
checkpoint the repair and its regression controls remained pending; neither
those passing test counts nor that healthy native comparison closed the gap.

The subsequent three-file accounting fix moves known guard observers to raw
dispatch without changing report/receipt schemas or generic callable behavior.
Before the fix, new existing-API regression cases produced 18 expected failures
and 16 passing controls. After the fix, all 34 pass: partial setup before/after
setter mutation, changed repeated preparation, raw/role failures, cancellation,
post-return cleanup, and complete strict stage-cohort readback. Completed raw
calls with post-return rejection and no failed roles use the existing stage
`invalid_stage_output` reason; the strict join is not relaxed. A healthy later
peer retains its distinct call ID, while poisoned ownership keeps later work
explicitly unavailable without more dispatches. A combined 12-file run passed
690 tests. A further 53 model-free observer/lifecycle controls pass, covering
omitted, duplicate, escaped and foreign-thread dispatch, cancellation
suppression/replacement, budgets, and stage-probe partial installation and
restoration. The final combined 14-file run passes 765 tests, including
architecture boundaries. Peirce independently reviewed the applied three-file
fix and all 34 existing-API regression cases; Anscombe independently reviewed
the same production delta and reran the then-current 87 accounting/observer/
session-observation controls. No strict stage schema or join was weakened.
The final native v2 replay subsequently completed all 12 calls through the
production recorder, preserving all three complete candidate files byte for
byte. Root and Peirce independently checked the retained artifacts and receipt
mapping. Its manifest SHA-256 is
`95ff97681b38356252a9db2c2903da487461907d6b46baa5665e720801c07af3`.
After independently reviewed architecture promotion, Phase 9 passed 9,387 tests,
seven platform skips, six warnings and all nine selected gates; all 9,394
collected identities and all 482 frozen tracked files/index bindings passed
independent verification. The
[qualification record](evidence/2026-09-07-ocr-engine-guard-qualification.md)
retains exact hashes and scope. Earlier native bundles and the 4,580-test result
remain bound to their earlier revision, not retrospectively to this repair.

The [OCR improvement program](ocr-improvement-program.md) retains the outstanding
representative accuracy, missing-text
accounting, correction publication and AI-access requirements. No canonical
text or index is changed by this guard.
