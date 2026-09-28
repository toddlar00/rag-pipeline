# Local OCR qualification checkpoint

This is local development evidence for the ongoing
[OCR and AI-access program](../ocr-improvement-program.md), not a release,
representative-corpus accuracy claim, or completion of that program.
The worktree is dirty; its base HEAD is
`e34103f70b676eacc8d55badb2468b8a10004ef4` (base tree
`af0a69ccde82b6b679580f07813495721978efe0`). The private tracked-file snapshots
identify the tested dirty source, not that base tree alone.

## Initial frozen gate run

The private `evaluation-reports/ocr-phase2-gates-v1/` directory contains
before/after tracked-file hashes, index-entry/status digests, JUnit results and
per-gate logs. All tracked inputs were unchanged throughout the run. The
qualified outside-workspace CPython 3.12.10 environment uses the unchanged
full/test/tool hash locks; no existing environment was modified.

- Full suite: **6,841 passed, 7 skipped, 8 failed**, 354.87 seconds reported by pytest.
- Ruff, tracked-source compilation, dependency policy, model policy and the
  architecture inventory checker all passed.
- The test-only architecture consumer expectation omitted the independently
  reviewed `ocr_docling_io` consumer; its one-line correction passed the focused
  test and checker without refreshing the baseline.
- Seven real-process failures exposed the Windows venv redirector PID mismatch:
  three detached-job tests, three Phase A0 tests and the worker identity test.
  Successful work did not justify accepting a mismatched ready marker.

Initial receipt SHA-256:
`c6e479b34925e93a335e2d906cc188cae9fb04c9223640068c54515d74c0ee5e`.

## Deliberate Windows correctness repair

The shared `process_supervision.python_worker_launch` uses the current CPython
base executable plus a child-only virtual-environment launcher identity.
Supervised workers, detached job managers and the controlled isolation-probe
descendant use it. See the
[supervision decision](../architecture/decisions/process-supervision-extraction.md).
No PID, birth, nonce, startup-gate, environment-scrub or cleanup checks were
relaxed. The caller's filtered environment remains the source for child settings.

Focused observations, which overlap and must not be summed into a suite count:

- Helper policy and actual child PID/executable/prefix/package-location checks:
  **15 passed** under the qualified environment.
- Original supervision/module/runtime tests plus the earlier 11-case helper
  revision: **79 passed, 3 skipped**; the original exact worker-PID assertion
  passed unchanged.
- Detached manager, jobs CLI and six new launch/identity tests: **71 passed**.
- Phase A0: **56 passed** without the fingerprint smoke, then the five-repetition
  fingerprint-bound smoke **1 passed** while tracked source/index were held stable.
- Peirce and Anscombe independently reviewed the shared launch mechanism;
  Peirce also verified a real malicious-launcher-override negative control.

Peirce approved the full 193-line architecture diff and all changed semantic
hashes for candidate
`4f180ea6a5d7e2fdbf15507add937f43760f31204f7ae183acbb23cfbd6283ec`.
Its independent qualified checker passes: 275 tracked Python files, 127 non-test
modules, 3,119 functions, 956 static/964 runtime facade bindings and 161 top-level/
26 nested patch seams. The change adds two test sources, one launch helper and
the inward job-coordination-to-supervision edge. The graph is acyclic; complete
`rag` owner/runtime contracts and existing signatures are unchanged. This
characterizes the intentional platform fix, not an ownership move. The approved
candidate was promoted before the replacement frozen-source run below.

## Generated runtime and browser observations

A new contained eight-page run after the launch repair completed successfully
with CLI exit 3 (review required), using the retained qualified installation
receipt and approved local RapidOCR models. Artifacts are private under
`evaluation-reports/ocr-qualified-execution-none-v3/`:

- Manifest SHA-256: `9ad2a95073adbeddf19de3100cef2ca4a47ff9c05c9126bb0b741b7e03f7f6db`.
- Execution receipt SHA-256: `6ee84e7fefb0c53265063e87b17105bfc553292020de7350923109696a1a6675`.
- Report SHA-256: `2266a95c2d623a191b81c8a8241730e2a8c82f7c0eabffd5ea9b5d985ca01ac4`.
- Paired comparison SHA-256: `0d20ae7fa3e1ad653d64ec0a394d1657749805e6b8cff2ab5ebe90c02334bb17`.

All eight generated pages had unchanged measured outcomes relative to the
earlier diagnostic baseline: CER 4.34%, WER 5.75%, no improvements or regressions.
One recorded selection setting differs; this is not an isolated-setting or
held-out accuracy experiment. The sole non-exact page remains the generated
two-column ordering case. Runtime receipts record observations, not signed
provenance or native/loaded-byte attestation.

The actual browser reopened the retained v2 draft at page 6 with the corrected
left-column-first order. All review checkboxes were unchecked, and attempting
review export without fresh confirmation produced a refusal. The scan, numbered
OCR boxes, original/proposed text and diff were visually inspected. The fixed
loopback QA server shut down normally and the owned browser session was closed.
Only previously generated fixtures were used; no private PDF or corpus was opened.

## Replacement frozen gate result

The qualified CPython 3.12.10 run in
`evaluation-reports/ocr-phase2-gates-v2/` passed all nine recorded gates:

- **6,871 passed, 7 skipped, 6 warnings**, 316.53 seconds reported by pytest.
  JUnit records 6,878 total cases, zero failures and zero errors. The seven skips
  are explicit Windows/POSIX filesystem or process-platform differences, not
  unavailable OCR dependencies.
- Ruff, compilation of all 275 tracked Python files, dependency policy,
  model-artifact policy and architecture inventory all passed.
- The Property, Constitutional Law and table-family offline BM25 suites passed
  the checked-in CI's thresholds and baseline no-regression checks. These are
  existing retrieval regressions, not the pending paired OCR-to-answer study.

Every recorded gate observed unchanged tracked-file content, index entries and
tracked dirty status. Before/after source snapshot SHA-256 is identical:
`d32dfeb30c3ff8c6994c2f4f92c712419b7229d409acfe50487351e31aea0514`.
The completed receipt SHA-256 is
`d569319d65ef13505084df21b217a4c5634f6bf4d0099407a5a77b14d7c87924`.
Logs are digest-bound by that receipt. JUnit evidence is retained separately;
the original runner did not include a JUnit-file hash in its receipt.

Post-gate edits only record results in this note, the program checklist, the
roadmap and the supervision decision; no Python, dependency/model lock or
inventory changes were made after the passing run. This is a passing local
Windows checkpoint, not a hosted platform matrix, signed/native/loaded-byte
attestation, representative-corpus qualification, or release.

The full improvement goal remains active. All remaining program requirements
and source, retention and AI-egress choices remain in the program checklist.

## Subsequent omission, audit and checkpoint implementation

The earlier passing source checkpoint does not cover the subsequent Python
changes in this section. Their targeted tests and actual generated-fixture
workflows are verified; a fresh frozen-source full run is required.

- Saved-region omission diagnostics and strict IO/CLI: 155 focused tests.
  Editor/runtime/draft v3 integration: 134 focused tests. Peirce independently
  approved the editor and final browser artifacts, including fresh-confirmation
  refusal, exact draft restart, assessment export and reset. The browser receipt
  at `evaluation-reports/ocr-editor-phase2/omission-browser-mechanics-v1.json`
  has SHA-256
  `d5f8fa2a25f14d7244eb7862c3cb639555e63122b4b2e886bc2ebdc7e0896d70`.
  These checks compare saved regions and candidate-line geometry, not pixels;
  ambiguous overlap is a warning, not proof of missing or accurate text.
- Core normalization audit: 331 focused/compatibility tests, including an
  unchanged pre-instrumentation 542-case output fingerprint and exact reversible
  rule edits. The generated mechanics receipt at
  `evaluation-reports/cleanup-audit-v1/mechanics-receipt.json` has SHA-256
  `c97e16f6938d3665712ab07dab6245af940bf3fac8a1efcfb8d75d0c732cef66`.
  Source-aware wrappers and chunk-level split/merge/dedup are excluded; this
  replay does not establish content loss in the production pipeline.
- Page checkpoints: 497 focused/compatibility tests and independent review.
  The real supervised generated eight-page experiment returned 130 on
  cancellation and 3 on resume. Page 1 was reused byte-for-byte, page 2 stayed
  interrupted with no candidate/receipt or rerun, and pages 3–8 recorded six
  actual completed calls in the new segment. Peirce independently reconstructed
  the request and strict completion with the qualified runtime and verified all
  input, journal and origin-receipt hashes. The observation at
  `evaluation-reports/ocr-checkpoint-smoke-v1/observation.json` has SHA-256
  `1ab3dae4cff2529febfd42ecc4694d9d00f3f9c0d8571f5ed56c6e71695a741a`.
  Unknown engine work on the interrupted page is not counted as zero work.

These overlapping focused counts must not be added into a full-suite count.
Only generated or explicitly byte-only scripted fixtures were used; these are
mechanics, safety and compatibility observations, not representative accuracy.

Socrates reviewed the complete keyed architecture diff and independently
regenerated candidate
`65674c66594a83cedf67ce00200e715d92c2128585247a1d96d5491fc63cc7a3`:
293 tracked Python files, 136 non-test modules, 3,199 functions and an acyclic
373-edge graph. The intended observer, same-module recovery factoring,
omission/editor/draft changes and opt-in checkpoint CLI are characterized;
facade ownership/runtime contracts, 956 static/964 runtime bindings and
161 top-level/26 nested patch seams are unchanged. The nine added module
records increase canonical size from 1,597,230 to 1,629,862 bytes. The approved
test size guard changes from 1,600,000 to 1,700,000 bytes (70,138 bytes of
headroom); canonical equality/drift gates and the separate 4 MiB runtime-probe
output cap are unchanged. The latter is not a baseline-parser size limit.

## Phase 3 frozen gate result

The fresh qualified CPython 3.12.10 run in
`evaluation-reports/ocr-phase3-gates-v1/` passed all nine gates:

- **7,232 passed, 7 skipped, 6 warnings**, 395.14 seconds reported by pytest.
  JUnit independently records 7,239 cases, zero failures and zero errors. The
  seven skips remain explicit platform differences, not missing OCR packages.
- Ruff, compilation of 293 tracked Python files, dependency/model policies and
  the independently approved canonical architecture inventory all passed.
- All three offline BM25 suites passed their existing CI thresholds and
  baseline no-regression checks. Socrates independently recomputed those gates
  from the saved metrics and verified their query/chunk/baseline bindings.

All 420 tracked file snapshots, Git index entries and tracked dirty status were
unchanged throughout the run. Before/after snapshot SHA-256:
`f279059aecc2a4781f8bfd7a3b9c52814606ca52f3e57bd0011e4018fc0163e8`.
Receipt SHA-256:
`5614728baf0478ae26b164fdcc807467a680f29f289c6c4924fe31c454efdf6e`.
All nine log hashes and corresponding per-gate result records were independently
verified against that receipt.

The original receipt did not include hashes for JUnit or the three retrieval
JSON reports. A separate create-only `verification-addendum-v1.json` binds their
independently verified current hashes without rewriting the original receipt;
its SHA-256 is
`11134062d4dee8942e8565da0cc6aeab154eeeea9b91704531946736a284c1c5`.
This addendum is subsequent artifact verification, not a claim the original
receipt already contained those bindings.

Stage-specific OCR diagnostics development began **after** this passing source
freeze. The Phase 3 result covers the omission, cleanup and checkpoint work,
not the later stage-diagnostic Python changes. Those need their own focused,
real-runtime, independent-review and complete frozen-source checks. The whole
program remains active; representative accuracy and approved real-corpus AI
access are not established by any of these local development results.

## Stage diagnostics and bounded snapshot verification

The separate stage workflow observes postprocessed detector boxes before
recognition filtering, retained full-page lines, and recognition-only crops of
declared gold lines. It does not modify existing recovery formats or canonical
extraction. Socrates independently approved the pure policy and runtime/IO/CLI;
the combined stage and artifact-source-snapshot rerun passed 325 tests, with
scoped Ruff clean. The stage-only tests comprise 72 policy tests and 163
runtime/IO/CLI tests; these overlap the combined count, not additional tests.

Review closed a real native-allocation hazard before model execution: thin
rasters can expand during RapidOCR's internal minimum-side resize, padding and
detector preprocessing. Admission now mirrors and checks the pinned integer
rounding/dimensions before engine entry, with actual moderate-size OpenCV
characterization and arithmetic-only rejection of extreme dimensions. Staged
output bytes are checked against their intended digest/size before create-only
publication, and fixed CLI error/help text avoids incidental program paths.

The shared Windows artifact reader received a deliberate resilience fix,
**not** a behavior-preserving extraction. Verification now reads at most the
captured length plus one byte rather than following a growing file to EOF.
The default unlimited first read remains unchanged. A first read exceeding an
explicit byte limit now raises `ValueError` before verification, even if a later
verification would have failed differently. Success bytes/digests/fingerprints,
same-size mutation rejection and ctime-only retry semantics remain unchanged.
Anscombe recorded characterization before editing and twelve failing hardening
cases; the final focused compatibility runs passed 341 tests. Socrates reviewed
the diff and independently reran the shared-reader cases with the stage suite.

Reviewed production SHA-256 values:

- `ocr_stage_diagnostics.py`:
  `9bd1d91d502a9b8a22733ea4a4e79c9312c82ae7060ac1f9d91b0c42347afcf6`
- `ocr_stage_runtime.py`:
  `b7a88352b9bc2fbc1a9409985f8571b4ee165a1db027bb03e43215a6f17061ee`
- `ocr_stage_io.py`:
  `7a8e5eb012c72d04d8747a8485204ea19addd48df674b2d93b3656616d1675c4`
- `tools/diagnose_ocr_stages.py`:
  `9fdbac2939a646ff7b757dd1fda63953c97a4c73293b1a05d28669b13658c4a0`
- `artifact_io.py`:
  `6ff22efa83c5bdb1e9c4be4aead73853990a81aeccabbcfb40a88d849cc9f3fb`

### Authored reference and actual generated run

The existing synthetic challenge PDF remains unchanged at SHA-256
`fb337c41515e517f09750a58f8935a8f927fa7a19085099438f28b5e4ffe7e6f`.
The separate reference in `evaluation-reports/ocr-stage-reference-v2/reference.json`
has SHA-256
`2d5a7988e6bf18aca798259f3a9fa8597ada5782a90aa878612a6aceaae180f9`.
It includes all authored titles, body lines and footers on pages 1, 6 and 7:
48 lines and 18 table-cell containers. Gold text came from fixed authored drawing
primitives, not OCR. Anscombe independently inspected all 48 crops and full pages
and checked pixel containment at Poppler 180 DPI and runtime PyMuPDF 300 DPI.
This is synthetic-generator provenance, not independent human transcription of
representative documents. The earlier failed reference-builder attempt left an
empty v1 directory, which was preserved; no prior artifact was overwritten.

The contained qualified-runtime CLI published
`evaluation-reports/ocr-stage-smoke-v1/` with 3/3 full pages and 48/48 gold crops
available, all 48 paired to full-page lines. Independent strict readback and
artifact verification by Socrates passed in a fresh qualified process, with
all original artifacts rechecked before create-only addendum publication:
`evaluation-reports/ocr-stage-smoke-v1/verification-addendum-v1.json`, SHA-256
`58a7ef77ee56e4b5914b19a5149c0e8685b02d95752583669e0b070ddf2f3db7`.
The original bundle manifest SHA-256 is
`be416de96c6aff915caea868837bc120537cb15b9fc970df7b1413ba9a1c7031`.

The execution receipt records 54 completed outer calls (3 detector-only, 3
full-page, 48 gold crops), and actual role completions of 6 detection,
3 classification and 51 recognition calls, with no failures. All three
byte-verified RapidOCR 3.9.2 models used CPU sessions with two intra-op and one
inter-op thread. Recorded execution time was 19.234 seconds. The original
PowerShell invocation did not propagate `LASTEXITCODE`: the shell reported 1,
and the native CLI exit is not independently recorded. The completed bundle,
printed parent verification and fresh readback establish completion; no native
exit-3 observation is fabricated or extra OCR rerun performed.

All 48 gold crops and all 48 geometrically paired full-page lines have exact
normalized text (891 character units and 164 words across the line records).
Every declared line had an unambiguous detector/full-output match; no missing,
split, merged, weak or extra geometry was observed in this fixture. The two-
column page nevertheless has 15 reading-order inversions among 91 comparable
pairs, with 147 character and 33 word edits in the complete page sequence.
Its CER/WER are 50.865%/64.706%; complete pages 1 and 7 are exact. Across the
three full-page records, CER/WER are 15.705%/20.122% (936 character units,
164 words). The differing character denominator includes normalized separators
between lines in page-level records; paired-line scores do not include them.
All 18 cell-container text scores are exact in declared gold order, not evidence
of predicted table-structure or within-cell reading-order accuracy.

This diagnostic result supports order review for the observed synthetic column
case; it does not demonstrate a recognition improvement or an additive causal
decomposition. Shared alignment used 461,643 of 20,000,000 permitted cells;
no metric abstained. Five unselected source pages remain explicitly outside
this stage cohort. Independent architecture and fresh complete frozen-source
qualification remain separate gates.

### Independently reviewed architecture refresh

Socrates reviewed the full keyed candidate diff against the Phase 3 baseline
and independently regenerated it with the qualified interpreter. The approved
candidate SHA-256 is
`dcaa42fbd9227af2da53d89012f06bc4e82ce784fa790bb030431b0ca88cf18f`.
Only tracked-source and source-architecture sections change: four additive stage
production/CLI modules, five tests and the explicit artifact-reader hardening.
The existing artifact-reader signatures/imports and all facade source/runtime,
consumer, hash-domain and interpreter-normalization sections are unchanged.
There are 302 tracked Python sources, 140 non-test modules, 3,266 functions,
263 classes and 387 acyclic edges (14 added inward stage edges). Static/runtime
bindings remain 956/964, with 623 runtime callables and 161/26 patch seams.
Canonical size is 1,651,150 bytes; the existing 1,700,000-byte test guard remains
unchanged with 48,850 bytes of headroom. No ownership migration or contract
relaxation is authorized by this characterization refresh.

## Phase 4 frozen gate result

The new qualified CPython 3.12.10 run in
`evaluation-reports/ocr-phase4-gates-v1/` passed all nine gates, including the
reviewed stage diagnostics and bounded artifact-verification change:

- **7,495 passed, 7 skipped, 6 warnings**, 390.86 seconds reported by pytest
  (395.797 seconds including launcher overhead in the gate runner).
- Ruff, compilation of 302 tracked Python sources, dependency/model policies
  and the exact independently approved architecture inventory passed.
- Property, Constitutional Law and Table Family offline BM25 suites passed
  every existing CI threshold and baseline no-regression check.

All 431 tracked file snapshots, HEAD, Git index entries and tracked dirty status
were unchanged before/after every gate. Each per-gate freeze flag is true,
independently checked rather than inferred only from the aggregate success flag.
Before/after snapshot SHA-256:
`355f94c6b431b4795b34c5d1d153c7a69267c503fd16bec9a366eacfea7b8769`.
The receipt SHA-256 is
`71b17cb2017bf294d0dbafe5e2a44fa73d0d8f092ce54e9b457f272e90f5077c`.
Unlike the earlier Phase 3 runner, this receipt directly binds JUnit and all
three retrieval JSON reports, and retains an exact `gate-helper.py` copy.
Archived/live helper SHA-256 at verification:
`33bd8d30b77e6a7ba8afe4a74cf4ea637e241ea97c8252461ac88a1078eabe58`.

Socrates independently verified all 26 original evidence files, exact result
record/receipt equality, JUnit's 7,502 cases with zero failures/errors, the
qualified executable/helper hashes and current source/index agreement through
final create-only verification publication. The seven skips are explicit
platform/permission applicability cases, not missing OCR dependencies. The six
warnings are five pytest iterator deprecations and one Starlette deprecation.
He independently recomputed all retrieval thresholds and baseline regressions,
and verified the frozen query/chunk/baseline bindings (8/8/6 queries), without
rerunning retrieval or OCR. The separate `verification-addendum-v1.json` has
SHA-256
`46f1637a3c10eb94b0fbbba9cf3ce9ab714e707cbe564dc0a47694e3b282e93c`;
original receipts and artifacts remain unchanged.

Post-gate edits only update documentation of these results and the pending AI
companion test design. No tracked Python, dependency/model lock or inventory
changes followed this passing freeze. This is local Windows development
qualification, not hosted CI, representative OCR accuracy, native/loaded-byte
attestation, canonical text adoption or release. AI evidence search, restricted
scans and the remaining full program requirements remain pending.
