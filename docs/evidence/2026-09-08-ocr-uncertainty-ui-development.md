# Visual crop uncertainty review development, 2026-09-08

The live and archive panels now expose explicit source-region uncertainty,
adjudication and versioned review/save workflows. A real generated-data browser
journey saved an unresolved declaration, reopened it without restoring approval,
resolved it explicitly, scored it and saved a linked revision. This is focused
development evidence, not full-project qualification, representative OCR
accuracy, human adjudication or completion of the 67-requirement program.

## Implemented behavior

- Explicit composition flags select the v2 panels. The authenticated launcher
  selects them only when its execution or crop-pack capability is enabled;
  default library builders retain their separate legacy event graph. Archive
  Save/Open does not enable OCR, and no new model or public API is introduced.
- The numeric overlay supports Browse/Annotate, pointer and two-corner keyboard
  rectangles, explicit whole-crop selection, cancellation and selection of an
  existing annotation. It projects retained source-coordinate boxes into the
  current raster without rewriting them when detail changes. No OCR or authored
  reference text enters HTML; plain controls collect the decisions and reason.
- Apply records an explicit authoring revision. Prepare binds the full raw
  transcription, critical entries, annotations, source image and captured form.
  An unresolved area makes the entire crop unscorable; it does not invent a
  partial-text denominator or silently exclude a region. Complete resolved
  references use the existing comparison semantics.
- Raw edits reset resolved annotations in a dirty draft, including edits away
  and back. Same-source reload preserves draft/history. Save creates a new
  immutable version; reopening and damaged-proof recovery restore declarations,
  never current consent or scoring authority.
- Fresh image delivery has its own token. Failed Image postprocessing cannot
  give a later edit authority to an unseen image. Captured form fingerprints
  catch changed fields even when their change event has not arrived. Old queued
  Score/Save requests cannot borrow a newer preparation or score.
- A short-lived, operation-owned **edit-only lease** lets the still-displayed
  pre-action token invalidate a running Apply/Prepare/Review/Save. Genuine edits
  revoke the action; no-ops preserve it. Business actions do not use the lease.
  Context changes and terminal paths clear it, and an older finalizer cannot
  erase a newer lease. This protects server-observed edits before the final
  commit check, not an unseen/delayed event or a revision already published.

The Gradio skill informed explicit private events and component binding. The
Playwright skill was used for actual browser interactions and visual checks,
separate from direct callbacks and installed-framework queue tests.

## Tests and real findings

The final combined UI/compatibility run passed **830 tests in 221.46 seconds**
(`60a8e8`), with no failures or skips. It includes the shared authoring helper,
editor/preview presentation, both v2 panels, actual Queue controls, in-flight
edit leases, legacy live/save/archive panels and both launcher suites. All 17
changed/new source and test files passed Ruff at `9b0d40`. This is not the full
repository suite or the nine copied-source gates.

The final live/lease generation passed **98 tests in 15.34 seconds** (`8be546`):
47 live-panel checks, 13 in-flight edit/result-correspondence checks and 38 lease
controls. The fixed editor plus existing preview helper passed **187 tests in
5.54 seconds** (`a2eb5b`). These are development suites, not OCR accuracy scores.
Other retained runs overlap and are not summed into a unique total.

Two real defects were preserved and repaired:

1. The first real browser run reproduced a listener-order bug: Browse Space did
   not scroll a detailed image when the overlay listener registered first.
   The editor now lets detail Space keydown reach the existing scroll handler,
   while retaining activation suppression. Both image IDs passed the repaired
   actual-browser checks.
2. Four deterministic red controls proved that edits carrying the still-displayed
   pre-Save token were ignored while Save was held, allowing the inert commit
   port to publish. The edit-only lease closes that race across slow actions.
   The original four failures remain in the red XML below. Follow-up controls
   cover reference/form edits, mode/selection, wrong images, no-ops, exceptions
   including BaseException, expiry and competing finalizers.

Review also corrected pre-test archive RGB identity and supplied-None admission
errors, added exact returned reference/comparison joins, and preserved valid
editor no-ops. The final live Save clears its lease inside the response lock;
its earlier pre-response cleanup variant is not the final producer below.

Earlier test-only failures remain retained: live assertions used the wrong
uncertainty-count key; archive fixtures had a wording assertion and an uninstalled
Dropdown choice; editor fixtures had excessive Windows test IDs, a Gradio JSON
wrapper/exception mismatch and an overbroad string sentinel. The second browser
driver initially selected the wrong JSON container; that checker-only failure
is separate from the real Space bug. No failure is reclassified as a green run.

## Actual browser evidence

The editor-only evidence is retained under
`output/playwright/ocr-crop-uncertainty-editor-development-v2/`:

- `acceptance.md`, SHA-256
  `f1b62ed0588be65baec6406ee2d8a60ef5263d1d757cdf0f9f0728ea4cb22cfc`.
- Eight cases: both image IDs, each with Fit/wide, Fit/tall letterboxing, and
  288/576 detail presentation with both-axis scrolling. Pointer geometry,
  keyboard corners, Escape/Tab, whole-scope selection and Browse scrolling were
  exercised; no latent selection or fullscreen activation occurred. Generated
  profile presentation is not proof of new native DPI rendering.
- The preceding v1 defect, both driver generations, screenshots, numeric
  acknowledgements and selected-source evidence are retained. Both helpers
  stopped, removed auth/cache and left no port-7866 listener.

The complete archive browser journey is retained under
`output/playwright/ocr-crop-uncertainty-archive-development-v1/`:

- Production private UI, real historical validators, journal/reference/comparison
  policy, service and immutable store; only source-byte verification and generated
  RGB rendering are inert ports. No private PDF, model or OCR runtime was used.
- The unresolved review reported `scored_pairs=0`, `unscorable_pairs=1`,
  `comparison=null`. Its observed service call invoked neither `compare_ocr` nor
  `evaluate_ocr`. After explicit resolution the review reported one scored pair
  and invoked one comparator/two evaluator calls. These counters are scoped to
  this generated unreviewed-seed journey; other parents may replay historical
  metrics during verification.
- The saved chain is v1 draft → v2 historical unscorable declaration → v2
  historical scored declaration. Reopening the unresolved child restored its
  annotation with the confirmation box unchecked and current metrics empty.
- `.playwright-cli/unresolved-reviewed.png` and `resolved-reviewed.png` were
  visually inspected. The whole-crop overlay changed from red unresolved to
  green resolved. The long plain-text technical declarations are retained;
  this is not a claim of polished information density or usability qualification.
- Browser close `5b237f`; helper session 15614 terminal 0 at `189d32`.
  `shutdown.json` SHA-256
  `509eec3baa59fa57afec15705de49065bc2c0a611bd128fcd5063333b957797d`
  reports equal selected-source hashes, no forbidden imports, removed private
  auth/cache and no cleanup failures. Port 7867 had no remaining listener.
  A pre-auth DOM password-form advisory remains in the first console file;
  the final authenticated console query returned zero messages/errors/warnings.

These observations do not establish native PDF rendering, user consent,
all-platform accessibility, arbitrary transport ordering, IME/touch behavior,
eventual framework image disposal or representative OCR accuracy.

The separate independent browser audit passed (`0722d3`, `3ab154`, `254271`).
It performed three complete production fixed-path reads, both parent-continuity
checks and 78-file/190,734-byte rechecks; all 24 original archive/input slots and
the one-revision journal prefix remain intact. Both current-metrics snapshots
exactly match their saved comparisons. The reopen snapshot has the unresolved
annotation, unchecked confirmation and empty prepared/current-metrics fields.
All 26 selected sources match before/after/current reads. Auth absence and no
listener were independently checked; cache removal remains the helper's
observation because its randomized pathname was not retained.

Within the archive browser run directory, the create-only audit records are
`browser-audit-v1.md`, SHA-256
`7fd34e36c1109fbd14f770c896e4677ef63ce1b31aefcda11798d20346d83ce1`,
and `browser-audit-v1.json`, SHA-256
`12362e5322ff0cbb61cc38ad9c6bd10f761e6729595753463f2ff4d0643ac173`.

## Retained test receipts

Paths below are relative to `tmp/`. Historical runs are identified by their
scope/generation, not relabeled as final full-project qualification.

| XML | Result and scope | SHA-256 |
| --- | --- | --- |
| ocr_uncertainty_ui_combined_final_v1.xml | Final combined UI/compatibility: 830 passed | 0e96304bc206d63d9f38764a4d15588ad95b2b2c323ef70412dcd0e6d2f4f77f |
| ocr_uncertainty_final_live_lease_v1.xml | Final live/lease generation: 98 passed | 3f511eb7209e7caf6a3ed44e60861daec8c8c2c57c42417e91c0698d6aba2af1 |
| ocr_crop_uncertainty_editor_preview_v4.xml | Fixed editor plus preview helper: 187 passed | f7ec6f707cb2af9b8dd377437c79acb4738e5caa5b57dc19ac997fbaa7821b37 |
| ocr_uncertainty_inflight_edits_red_v1.xml | Original Save race: 4 failed | 261233d7e896cd95714f40bbc2b0eedafc063bab0b3dffb288b45aca4c35f220 |
| ocr_uncertainty_inflight_edits_green_v2.xml | Initial lease repair: 13 passed | f10e66dfcfbf03b25f1dfe150850b8aba42e3f28bfd70db99b904048e639d66a |
| ocr_uncertainty_ui_postlease_v1.xml | Pre-final-noop live plus archive/Queue/launcher: 164 passed | eec957f73af12f361f6fbdd5ee5f4965e8c3bddd3045628c6ce4a48c7204ee1b |
| ocr_uncertainty_live_noop_v1.xml | Before final Save-lock cleanup: 60 passed | 62987851398ac2dbcebd842ebe0c49b2dcb5037b68cc2878c351dcd550eead10 |
| ocr_archive_uncertainty_ui_legacy_v3.xml | Before lease repair: 59 new plus 169 existing archive checks passed | 8a96b0b4a5753e7a40b4d7b3fa2776d2a0fa1784afe4bdea4998d76ef222869c |
| ocr_review_crop_archive_uncertainty_queue_v1.xml | Before lease repair: 12 actual Queue controls passed | a6029750abc18cf75fd22b38a116d96d35bf6122ffd30d5a9abcd4e21849e86c |

The independent review matrix at
`tmp/ocr_uncertainty_ui_independent_review_v1.md`, SHA-256
`9b30641ec0c8a01ba0e3bf6d3212611cb070a495a4a8e5fa576384e7e0241255`,
records the earlier point-in-time producers and red/green evidence. Final live
source and the 98-case result were separately reviewed by Peirce; the earlier
matrix is not silently rewritten to describe a later producer.

The combined run used the qualified local Python 3.12 interpreter, `-I -B`,
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, and `pytest -q -p no:cacheprovider`, with these
15 explicit files (all paths relative to `tests/`):

```text
test_ocr_review_crop_uncertainty.py
test_ocr_review_crop_uncertainty_editor.py
test_ocr_review_crop_preview_ui.py
test_ocr_review_crop_uncertainty_live_ui.py
test_ocr_review_crop_archive_uncertainty.py
test_ocr_review_crop_archive_uncertainty_queue.py
test_ocr_uncertainty_inflight_edits.py
test_ocr_uncertainty_edit_lease.py
test_ocr_review_crop_ui.py
test_ocr_review_crop_ui_integration.py
test_ocr_review_crop_save_ui.py
test_ocr_review_crop_archive_ui.py
test_ocr_review_crop_archive_integration.py
test_review_ocr_execution_cli.py
test_review_ocr_crop_packs_cli.py
```

The `--junitxml` destination was `tmp/ocr_uncertainty_ui_combined_final_v1.xml`.
Independent XML readback at `9b0d40` found 830 tests, zero failures/errors/skips
and suite time 221.438 seconds. Console time includes a small additional overhead.

## Final implementation identities

These are current worktree bytes, not a new commit. SHA-256 and Git blob values
were read at `93d0f8`; `git hash-object --no-filters` was used without `-w`.

| File | SHA-256 | Computed Git blob |
| --- | --- | --- |
| ocr_review_crop_uncertainty.py | 2398dc7c6213938db615d2a8873e958dcddf458328e4e58ff5c22ab85f7ebd85 | 2cae3400fe8afa879ef7f19787a022908f87e6b0 |
| ocr_review_crop_uncertainty_editor.py | 83c28f28cae72ad10ca4e6f7baf3b62fffdfe772a34d272e7de7bea4667588c0 | 817c9f1807a6feb26113e7fdf07b0b8d37d30fbb |
| ocr_review_crop_uncertainty_live_ui.py | f1db2c5b7175333a8a70ef2475d32594665ec049961e7217135e8373156029e1 | ea9a4466178dcbdff7a76f086ece54c60c2a3761 |
| ocr_review_crop_archive_ui.py | 0367e79465cae52a1fa19feff4d64c7d273a234c201f4c88c77de00ad1da8df2 | 90518a946a49a9c53eb3a7dc08970530ec32a8b2 |
| ocr_review_crop_ui.py | 714b3dc8fff5b2012474fd7a227e36499750d4ce6ac43f5235ce74a430fd9cdb | 1279e689eed356b86a1a566b351e35d6fd8c0aaf |
| ocr_review_execution_ui.py | e8c17eebaf067b9f7330e9659c4fb9fd9a0dd427e85735392404ec90ead4ee3e | 363cd9c9e7f935bf59e9742b19e6b66b81e6b849 |
| ocr_review_ui.py | b0ea55abdeb52bcb2273257cd4c1d9febf4154f58ec4e39ff6e0b4eefde98053 | 8d66eca6436edb813cb8accc7f855d748be9b3cb |
| tools/review_ocr.py | 08fdac897d1a25a83f40f3173f4da2fa3cb12c217e2a2200ec3e8e18950da902 | 4383f5bab515fca27f2562c4b8510f997d55ff64 |
| tests/test_ocr_review_crop_uncertainty.py | dcedf7aff0fe602f6eb802ec0ae6c12fc04d04f83d3132855262dbf60219eb05 | 5a43411f025ca8e38017251d62f6e63394393a23 |
| tests/test_ocr_review_crop_uncertainty_editor.py | a0b565d647a877ece22c9390b17918a6a4f4cdd337252502c97fa086ba7aa9ee | bfa30dd4e8379c96c13e0a6423def203a1f7fa4a |
| tests/test_ocr_review_crop_uncertainty_live_ui.py | 7766e9f381d47a15b5c99759305a88138f7c6233f11ddaff421ee296020929cc | a1147b6be8dc01a373f39db579f9b345b96f8dec |
| tests/test_ocr_review_crop_archive_uncertainty.py | d250e12c222ded9d9faf6a8f3b53637ecfb751125dc682488a074d5f77ed6b0b | 9f788158a7db2dd71963d3ac7643c6c1e6079a59 |
| tests/test_ocr_review_crop_archive_uncertainty_queue.py | edfed5cf50aa464b91f3720f0455008bbdcff3c4a6a29761e2835b860b52dd1a | a195186f64e030f6a9dba9feed5d22640ef87c7d |
| tests/test_ocr_uncertainty_inflight_edits.py | 5ce94bd03b3f3c0499f4c9d5affbb9929eadf693677af16ce45b7f9b693facba | a176e808d0c6220b1053bb4e34a2bf3b4bb20365 |
| tests/test_ocr_uncertainty_edit_lease.py | 5e736292c11729eedc8fca6a16c2d0337d093f27fb339ea3f9ba1537df7d1cea | 95619b4e1fc4d9c9d96455ed43f9383432d7655a |
| tests/test_review_ocr_execution_cli.py | d164a839f6ad6ea65575a8621fd0781b052c6b34184bcd5e1b479b43beb04981 | 985925c869dde99f004c72ab9b3ffac351b4e40a |
| tests/test_review_ocr_crop_packs_cli.py | 0bf089ef411419c718ed8d18936fc143fddc6f1fc343cdc25f8e508f385ce7ea | 96a37424d1293a904daf46061640d59360bd620d |

Original HEAD `e34103f70b676eacc8d55badb2468b8a10004ef4` and tree
`af0a69ccde82b6b679580f07813495721978efe0` remain unchanged. Raw index SHA-256:
`6a841daa2ac15f397750fc0f88f1bd6e93b78326823d2452e3ab14c88d0335f3`.
Architecture baseline SHA-256:
`8c9d9991aa6864131915b6f0392ae4bf4c3f9a4af5e8ae0757e78e9d47aa792e`.
Readback `7788d2` verified these pins. No index/history write, architecture
baseline increase, original-corpus change or replacement of prior evidence.

## Remaining acceptance

Full copied-source architecture/static/security/test/retrieval qualification
has not been rerun for this feature. The earlier 12,127-test/nine-gate checkpoint
does not qualify these new bytes. Read-only qualification preflight identified
a static import cycle: `ocr_review_crop_ui` dispatches to the new live builder,
which imports helpers from `ocr_review_crop_ui` and save metadata from the archive
builder (itself importing the crop module). Lazy imports avoid an immediate
runtime import failure but do not satisfy the acyclic architecture requirement.
This needs an inward shared-helper boundary before the next copied-source
freeze; it must not be hidden with a changed graph expectation or relaxed guard.
The 830 passing UI tests do not establish architecture conformance.
End-to-end native live preview/save, broader
platform/accessibility and presentation polish remain distinct from the generated
archive journey. Representative owner-approved held-out accuracy, independent
adjudication/engines and all other pending program outcomes remain in scope.
The 67 requirement names and acceptance columns are unchanged; their joined
SHA-256 remains `1b4a1f4a0e61011413337007bc2efbcdba3626d678d5f22b6dfc920081d7d698`.
