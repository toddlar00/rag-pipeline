# Crop uncertainty backend development, 2026-09-08

The backend now carries source-bound metadata from an actual preview render
through an opt-in worker protocol, and provides an explicit uncertainty-aware
reference/comparison API. **1,468 tests passed across three non-overlapping
focused suites**. This is a development checkpoint, not full qualification or
completion of the visual uncertainty workflow.

| Suite | Passed | Duration | Evidence |
| --- | ---: | ---: | --- |
| V1 comparison plus pure geometry, journal and v2 comparison | 745 | 9.50 s | Root terminal `546440`, retained JUnit |
| Existing runtime plus new same-render controls | 306 | 31.37 s | Independent implementation agent terminal `5b9b5b`; console result, no JUnit |
| Existing worker/controller plus new metadata controls | 417 | 155.27 s | Independent implementation agent terminal `0a2f77`; console result, no JUnit |

These runs reported no failures or skips. The pure combined suite has 94 legacy
comparison, 338 geometry, 138 journal and 175 v2 comparison cases. The native
runtime suite includes 103 new cases; the worker/controller suite includes 99.
Ruff passed for all six changed/new production modules and five new test files.
All commands used the qualified local Python 3.12 interpreter with `-I -B` and
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`. No full nine-gate run was repeated.

## Implemented behavior and evidence limits

`render_crop_scope_with_view()` and `CropPreviewController.render_with_view()`
return an owned `CropRasterPreview`: its metadata is retained as immutable bytes,
reads detach, and failed transfers preserve the image-close guard. The shared
native renderer keeps historical commands for ordinary PIL previews. The new
path additionally captures command/native matrix and clip, projected rectangle,
actual pixmap origin/extents and RGB identity. It checks the actual returned
PIL's mode, dimensions and bytes before transfer; the parent checks its decoded
PIL too. Metadata mismatches do not fall back or retry.

Ordinary protocol 2 retains its defaults, producer set, 4 KiB result limit and
PIL return. Explicit protocol 3 adds the raster declaration and its producer
binding, uses an 8 KiB result limit with a separate 4 KiB metadata bound, requires
metadata on success and null on failure, and rejects mixed versions. Selection
of protocol 3 happens in the trusted controller before hashing the request.

Generated in-process checks cover all three profiles, all intrinsic rotations,
cropped/uncropped pages, asymmetric/nonintegral clips, returned RGB parity,
numeric/pixmap/PIL mismatch controls and late-failure ownership. Twelve actual
isolated-child cases (three profiles by four rotations, with nonzero cropboxes)
matched both complete raster metadata bytes and RGB bytes against direct
metadata rendering and the legacy PIL renderer. Remaining new protocol tests
use explicitly inert image/geometry fixtures; they are not native evidence.
These results are not visual annotation, browser alignment, optical accuracy,
arbitrary-platform or total-process-memory qualification.

The new outward-only `ocr_crop_uncertainty_comparison.py` exposes separate v2
builder, validator and comparator entrypoints. They replay a complete journal
once, require exact outer/head/hash joins and retain a closed bounded envelope.
Unresolved references never become a v1 projection and never invoke
`compare_ocr` or `evaluate_ocr`. Candidate-failure reasons keep precedence while
uncertainty counts remain visible. Unknown character/word counts and character/
word coverage stay null; requested crop-pair coverage is counted explicitly.
Fully resolved references pass unchanged v1 validation and full-text
scoring, including empty-reference semantics and metric-limit handling.
Embedded metric bytes matched v1 across generated DPI/route, normalization,
critical-repeat and edge-insertion controls. No partial text is masked or guessed.

## Retained development failures and review corrections

- The earlier pure-suite Windows parameter-ID failure and unchanged-input repair
  remain in the [pure-component record](2026-09-08-ocr-uncertainty-pure-development.md).
- The first native suite reported 83 passes and six failures (`905622`). A test
  monkeypatch intercepted JM conversion during document setup, before its
  intended metadata call site. The fixture was scoped to that exact call site;
  production admission was not relaxed. The final native suite also includes
  the separately requested actual-PIL/RGB validation and failure controls.
- The first protocol lint check found a fixture-import name collision (`edce23`).
  The fixture alias was corrected before execution; the first pytest run passed.
- The first v2 comparator suite passed 132 cases (`641ef7`). Subsequent source
  review identified malformed outer scalar/hash fields being refused only after
  bounded journal replay. Cheap preflight was strengthened and 43 additional
  controls were added. All 175 passed (`744ca5`); original and final XML survive.

Root reviewed the final production deltas and new tests. Independent agent
review informed the comparison preflight and source/fixture checks. This is
agent review, not independently transcribed or human-adjudicated source evidence.

## Exact tested identities

Original base HEAD `e34103f70b676eacc8d55badb2468b8a10004ef4`, tree
`af0a69ccde82b6b679580f07813495721978efe0`, are unchanged; they do not identify
these dirty-worktree additions. Computed Git blobs below were not written.
The earlier pure record pins the unchanged geometry and journal sources/tests.

| File | SHA-256 | Computed Git blob |
| --- | --- | --- |
| `ocr_crop_uncertainty_comparison.py` | `ba04b01589ba4c5e83ab28c4528f868ca3d83a9c005c1baa99b665514080e5af` | `822b53bdd0463fa4752ddb6da32c5731c83d9f8a` |
| `ocr_crop_review_runtime.py` | `9931b9e4a98e07d3082ef8072ebde4f77ebc255220045a61df9fb10d4cf7be85` | `e28c952be891dc2d64b572dbc1c9985cb09a4959` |
| `ocr_crop_preview_worker.py` | `5024c2458a5030308b268d0c69798e308cf1335d011d1d2e6eaf396b9ff6054d` | `e78a9894ea0827aef6096bc3d2e8296e8420e3d8` |
| `ocr_crop_preview_supervision.py` | `07ba7cdead3b04942ae7f621f09510fac880efed15748614836340a8dd2b9b47` | `050e4e9f9d3d95efe5e1427514d296fb1a078214` |
| `tests/test_ocr_crop_uncertainty_comparison.py` | `bd4d46c11ef9da165edaa3be0f4affb3c0278867566051a9815bcab08fcd4681` | `597a80eaa8fdac3423e72d5a734397b01171ff96` |
| `tests/test_ocr_crop_raster_runtime.py` | `e023084021219f92dcb1899d298cbd2a1c7da90e639f742ccd0890f3f0dccd65` | `4564c19139a6c002f80faed80f253ae48fcd4e1d` |
| `tests/test_ocr_crop_preview_metadata.py` | `5601a94e2c8b75dbd865205cba1e15abbce516ec3f77932026f8fa59df8b546d` | `cb2f1fa836a0a66377ef7925d271a5f5bf12d3ed` |

Private JUnit SHA-256 values:

- `tmp/ocr_crop_uncertainty_comparison_focused_v1.xml`:
  `93bba8a86a870e29a3508c097d4f71433b08b5eb4ea8535abed935eb5d88eacd`.
- `tmp/ocr_crop_uncertainty_comparison_focused_v2.xml`:
  `f45aa1a1e026d297bfd562afe8e0cbd617a9582b1d624bddf3234c44a225333b`.
- `tmp/ocr_crop_uncertainty_pure_combined_v1.xml`:
  `5144bdc697a2b693303d1eb0c979048a88522f62d4a848b3c65c5c37738bbb17`.

Original raw index remains
`6a841daa2ac15f397750fc0f88f1bd6e93b78326823d2452e3ab14c88d0335f3`;
original architecture baseline remains
`8c9d9991aa6864131915b6f0392ae4bf4c3f9a4af5e8ae0757e78e9d47aa792e`.
Completed qualification copies/evidence and pinned qualification helpers remain
unchanged. This checkpoint is not covered by the earlier 12,127-test snapshot.

## Still required

The coordinator and archive service must carry the new metadata and journal
through their actual generation checks. Versioned pack creation, parent-prefix
continuity, dirty-draft handling, strict replay and damaged-proof recovery remain
unfinished. The explicit pointer/keyboard annotation editor, failed-image-delivery
and stale-event safeguards, live/archive consent, and generated save/reopen/
resolve/score journey are also unfinished. No new runtime entrypoint is exposed
as an AI write capability or a browser event by this checkpoint.

All [67 program requirements](../ocr-improvement-program.md) remain intact.
No OCR, private PDF, additional model acquisition, canonical correction adoption,
representative accuracy claim or release was performed or authorized here.
