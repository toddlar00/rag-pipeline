# Source-detail magnification development, 2026-09-08 UTC

This is focused local development evidence, not a full qualification, release,
representative OCR benchmark or completion of the OCR/AI improvement program.
The preceding [crop-pack copied-source checkpoint](2026-09-07-ocr-crop-pack-qualification.md)
and its retained copy/helpers/evidence are unchanged; they do not cover this
later implementation. No original Git index/history, dependency or model change
was made for this increment. Only generated inputs were used.

## Implemented behavior

Fixed Fit/288-DPI/576-DPI profiles re-render the same physical source crop.
Fit preserves the old scale/rounding; detail uses fixed 4x/8x PDF-point scales.
The existing 1,400-side/1,960,000-pixel caps are checked on outward-rounded
extents before raster allocation. Oversized detail refuses without changing
scope, lowering density, resizing an old preview or invoking OCR.

Private worker protocol v2 binds the exact profile in both requests and results,
including failure results. Scope/reference/pack schemas remain unchanged.
Historical pack image dimensions are not a record of preview density.

Both crop panels have explicit detail selectors and separate draft-preserving
Reload actions. A genuine profile change revokes image, approval, metrics and
reviewed-save authority. Same-value/stale captures cannot borrow newer authority.
Raw partial fields and intervening edits are preserved through skipped value
updates. An actual source/pair/context/pack change forbids old draft reuse.
Rapid stale profile changes may require explicit Cancel resynchronization;
arbitrary-network-order last-choice-wins behavior is not claimed.

## Focused verification

Counts below are separate observations and overlap; do not add them.

| Scope | Result | Retained tool observation |
| --- | --- | --- |
| Five backend test files, including generated native and contained preview controls | 499 passed, 259.63s | 51fde0, session84250 terminal0 |
| Coordinator ownership/cancellation and explicit profile forwarding | 45 passed, 8.35s | f80b38 |
| Six live/archive UI test files, including the 56 new direct detail controls | 420 passed, 198.04s | 7a559c/e10bef, session79635 terminal0 |
| Subsequent wording-only initial Load/Open versus draft Reload guidance | 4 passed, 2.98s | 2fc970 |
| Preview helper and launcher controls, including real Image.postprocess and inert Node dispatch | 98 passed, 3.53s | 3b0d63 |
| Surrounding review UI/execution UI and launcher tests | 140 passed, 11.32s | f2b83d, session23423 terminal0 |

Ruff passed for the changed production/test subsets. The 420-case run held nine
selected UI/helper/test hashes unchanged. The later four-case notice check is
separate, not a single 424-case run. Gradio process_api/Queue controls use real
component/session handling with explicitly inert OCR/rendering/storage ports;
they do not prove browser transport ordering. Backend native checks are separate.
Independent source/test review covered the fixed rendering and profile protocol,
draft preservation, stale authority and Save-enabled controls.

Earlier development failures remain explicit: three legacy expectation failures;
archive Dropdown test setup that bypassed SessionState choice updates; and two
Image.postprocess injection/exception-shape expectations. They were corrected
without relaxing production preflight. A discovered test invoke wrapper now
forwards session keywords and requires the intended stale-view error, not a
TypeError. An intermediate failed combined run overlapped a root test-file edit
and is not a source-frozen result. None of its counts substitutes for the final
420-case observation.

## Real-browser presentation check and its limits

Private evidence is retained under
`output/playwright/ocr-crop-detail-viewer-development-v1` and `-v2`.
These were authenticated minimal Gradio viewers with fixed synthetic PIL
images, not the full archive/live workflow or native PDF density rendering.
Harness-only Image.select counters supplied both blocked-detail and successful
Fit activation controls. Production JavaScript reads no image URL, reference,
session token or server callback.

V1 exposed rounded-corner clipping and an inset focus outline covering source
edge pixels. Its screenshots and failed observations remain unchanged. V2
uses detail-only square corners and a focus ring outside the source pixels.
The browser owner verified both fixed IDs across two detail profiles and three
image shapes: short 311x28, tall 80x1200 and both-axis 1200x1100. Image rectangles
equalled natural dimensions in CSS pixels, bounded scrolling reached all
corners, and pointer/Enter/Space/synthetic clicks did not select or fullscreen.
Fit positive controls restored ordinary selection/sizing. Keyboard/Tab, native
wheel scrolling, unrelated text input and desktop/mobile-width viewport bounds
were separately exercised. Root inspected the retained V1 defect and V2 square
corner/external-focus screenshots.

V2 helper26213 exited0 (8eac6a), browser closed (2cd812), and root read both
source snapshots plus shutdown (b77b71): all five selected hashes were unchanged,
private auth/cache removed, forbidden imports empty, no cleanup errors.
The helper source scope is point-in-time Python bytes, not loaded/native-byte
attestation. Two browser inspection mistakes were corrected as observation
errors, not product failures: command argument shape in V1 and computed outline
width/offset assumptions in V2. V2's retained `acceptance.md` also records the
auxiliary CLI/path/readback mistakes and same-image Fit label cleanup on focus.
Native assistive technology, other browsers,
actual production workflow integration and new full qualification remain pending.

## Actual archive workflow development probe

The independently reviewed create-only helper
`tmp/ocr_crop_detail_archive_browser_v1.py` ran once, session23542, terminal0
(`1620dc`). It used the actual archive panel/service and isolated PDF renderer,
the retained generated challenge PDF and the three previously saved synthetic
packs. The harness denied Save to protect that store and blocked parent OCR
execution imports. This is not a test of successful archive publication.

Browser artifacts are retained under
`output/playwright/ocr-crop-detail-archive-development-v1/`. The catalog refreshed
without selecting a pack or restoring approval. Opening entry1 restored its
saved transcription and fresh Fit crop, with current preparation, consent and
metrics empty. Root visually read the full Fit crop as `Synthetic OCR challenge`.

The actual short title crop produced Fit 311x28, 288-DPI 622x55 and 576-DPI
1242x108 pixels, matching an independent geometry-only calculation. Both detail
images displayed at natural CSS dimensions. The unfinished transcription
`Synthetic OCR chall` and exact raw critical field `OCR\n` survived profile
changes and explicit reloads; changes removed the old image and approval.
Root inspected the retained 288 viewport and both 576 horizontal endpoints.
Native ArrowRight reached the 576 viewport's far edge (scrollLeft/max 540),
with the source-specific keyboard guidance present (`0155ec`). All three packs
have this same short scope; they do not exercise an oversized-raster refusal.

The first probe (`35835a`) failed its expected actionable-refusal assertion:
Prepare rejected the trailing empty critical entry, but retained the prior
success status. Two backend tracebacks (`a664ea`) establish a generic
`gr.Error(_FAILED)` field-validation refusal, not a renderer failure. No
declaration or metrics were produced. This is an unresolved usability finding
in this exact source generation, not a passed end-to-end acceptance run.

After root inspected the fresh source, completing the transcription and critical
entry allowed a 576-DPI preparation. An observation script initially parsed
too soon because its broad DOM wait matched historical JSON (`6e1ef0`); the
retained field readback (`f77fcb`) showed the preparation did complete. The
separate resume script (`09b366`, session14022 terminal0) consumed that prepared
revision through the actual checkbox/Score controls. Its declaration bound
1242x108 pixels and the current profile, source, scope and pack. The historical
300/400-DPI candidates still both scored CER/WER0, exact match true and 1/1
critical occurrence; the resolution/rounded-edge warnings remained. This is
source-authored scoring of retained candidates, not new OCR or an accuracy gain.

Returning to Fit cleared current preparation, consent and metrics. Cancel and
explicit Fit reload preserved the completed draft without reviving approval.
The later 288/576/288 sequence followed by Cancel retained the text, resynced
the selector to 288, and left zero images, an unchecked confirmation and empty
preparation/metrics (`e6f94e`). It is one observed event sequence, not proof for
arbitrary transport ordering. Changing packs, draft-only recovery and Save
were not exercised in this probe.

The owned browser closed (`e6f94e`), then the owned stop file closed the helper
normally. Root read shutdown (`7db6ff`): both launcher/helper exit0, no failed
invariants or uncertain cleanup, no private caches or blocked parent modules,
ephemeral browser-auth removed, and the retained 105-entry pack tree unchanged.
The 252 selected input/producer hashes have identical before/after bytes
(SHA256 `6acd8e7370767c13708a23eb4c3bc75cc776b1e0bcd34d4bc70233b6991a4306`).
These are bounded point-in-time bindings, not continuous/native-byte attestation.
The failed probes and their scripts remain retained.

## Field-feedback fix and follow-up archive check

The follow-up keeps raw fields and loaded images while returning specific,
static inline guidance for locally invalid reference fields. Empty reviewed
transcriptions remain valid. No automatic trimming or new scoring semantics
were introduced. Current view/profile/context/action/ticket checks precede
field classification; late refusals cannot clear a newer preparation. The
existing current-action cancellation of in-flight archive Save work remains
intact. Only typed local validation errors receive this feedback; unrelated
failures stay generic and interruption still propagates.

Independently reviewed production SHA256 bindings:

- `ocr_review_crop_ui.py`: `6f24fcb589291fd0af5d051ad8797e44348bc216f960e04b2f550f6a69e2a02e`
- `ocr_review_crop_archive_ui.py`: `e3b3141d590b0603e19d2d27f853944fdc9afab841b705726fca6e2702fb60e4`

Verification observations are separate runs, not the old generation's results:

| Scope | Result |
| --- | --- |
| New typed-feedback controls and existing live/save/archive UI tests | 435 passed, 49.43s (`795cc3`, session71214 terminal0) |
| New installed `process_api` feedback/recovery controls: live, Save-enabled live and archive | 12 passed, 3.84s (`e39bf9`) |
| Existing detail reload controls and live/archive integration files | 82 passed, 158.74s (`78e23c`, session21913 terminal0) |

Ruff passed. The first owner run (`7b8927`, session95086 terminal1) is retained:
436 passed and four failed. Three tests still expected the replaced generic
Prepare exception; a new empty-reference test used the wrong metric keys.
Their corrected assertions retain the intended safety/undefined-rate checks.
Five nonapplicable parameter combinations that returned without testing an
action were removed before the final 435-case run. The 12-case fixture and
token-recovery wiring received separate independent review. These are inert
port/component tests, not browser transport or OCR accuracy evidence.

The reviewed archive helper v2 ran once against the held generation, using
port7868 and new create-only output
`output/playwright/ocr-crop-detail-archive-development-v2/`. Its only changes
from the preserved helper v1 were output generation and port. Its Save denial,
generated inputs, parent no-OCR guard and cleanup ownership remained unchanged.

Actual browser results (`e69760`, session95951 terminal0) verified the same
Fit/288/576 natural and CSS dimensions as v1. At every profile, Prepare on
`Synthetic OCR chall` / `OCR\n` now displayed the exact blank-line guidance
inline, kept both raw fields and the same image URL, and left preparation,
consent and metrics empty. Root visually inspected the displayed guidance and
both fresh 576 source endpoints. Native horizontal scrolling reached the far
edge. This fixes the specific v1 usability finding on the tested archive flow.

The separate recovery probe (`cd84e7`, session4336 terminal0) corrected the
fields, prepared and explicitly confirmed the source-inspected 576 crop,
and scored without reloading its image. The retained candidates again produced
zero CER/WER with the original edge/resolution warnings. Returning to Fit and
explicitly reloading retained the completed draft without reviving approval.
The actual returned declaration/comparison is retained as
`browser-recovery-observation.json`; it was copied from the CLI result, not
recomputed as a substitute for browser observation.

Selecting a second pack cleared the previous draft and image; an unbound
Reload was refused. Draft-only recovery restored `Synthetic OCR chall` / `OCR`
with no image, preparation, consent or metrics. Its first probe (`42322a`,
session60924 terminal1) waited for the wrong capitalization/status substring;
the fresh field readback (`0831a7`) confirmed recovery had completed. A separate
resume probe (`c6e433`, session66419 terminal0) verified that this text-only
view could not Prepare, explicit Fit reload preserved its draft without
approval, and Cancel preserved that draft. Its actual return is retained as
`browser-pack-recovery-observation.json`. The earlier failed probe is unchanged.

Browser console reported zero errors/warnings and the owned browser closed
(`4d29e4`). Helper stdout (`28737a`) retains two expected stale-view refusals:
the unbound Reload and Prepare on a text-only recovered draft. These are not
the repaired critical-field feedback failure. The owned stop file closed the
helper, session58716 terminal0 (`1f9d21`). Root's shutdown read (`f4a77b`) reports
both exits0, no uncertain cleanup or failed invariant, zero private caches and
blocked parent execution imports, removed auth, and the unchanged 105-entry
pack tree. All 252 selected pins have byte-identical before/after snapshots
(SHA256 `b83a068010a119f5e37fcd62789e03f3141dcfa934a7aaf66e6b6315a002b513`).
No Save or new OCR call was made. All exercised packs still share one short
geometry; no oversized-detail or arbitrary transport-order claim is made.

## Actual live workflow and retained intermittent refusal

The independently reviewed create-only helper
`tmp/ocr_crop_detail_live_browser_v1.py` (SHA256
`0404c45c7f11f7e18feb33e79de29bba3fe027d1068da026e463a1e71a217dc6`)
ran once in the qualified isolated interpreter, session85123. Its additional
harness restriction admitted at most two production UI Start attempts, ordered
300 then 400 DPI, for the fixed generated title crop. Both prepared scopes were
read before checking their initially unchecked authorization boxes. No prior
archive was turned into live-run authority, no model was downloaded, and no
private source was opened.

The two completed runs are `8e7d4ecb3a484d2592a797a6dcf4d399` (300 DPI) and
`f8a662fccafb4809a6bef644bf825e00` (400 DPI). The UI reported terminal verified
completion with one actual call each; the helper subsequently reconstructed
each fixed request and strictly reread the five-artifact bundles, plans and
dispatch tickets after launcher shutdown. Attempt counts are not substituted
for completed-call evidence. Output is retained under
`output/playwright/ocr-crop-detail-live-development-v1/`.

The first Load pair did **not** succeed: the actual status was
"Fixed inputs no longer match the accepted generation. Restart with matching
fixed artifacts." (`63c549`, retained `initial-input-changed-refusal.png`).
The fields probe stopped at its initial no-image wait (`2ffae7`, session99092
terminal1), before editing fields. Current readback of all four fixed inputs
matched their expected hashes. One explicit Load retry against the same pair
then succeeded (`fb2f58`), without changing sources or rerunning OCR. The original
refusal is retained as an unexplained intermittent reliability observation,
not declared fixed by the retry.

A second fields-probe attempt filled the unfinished draft but waited for the
wrong notice substring, `Reference changed`, instead of the actual
`Reference revision changed` (`83357f`, session23597 terminal1). That is an
observation-script defect, separate from the first production preview refusal.
Fresh field readback (`38e5c0`) established the resumed state. The separate
resume probe (`67c8f4`, session66314 terminal0) verified:

- Fit 311x28, 288 DPI 622x55 and 576 DPI 1242x108, with matching image/CSS
  dimensions for the inspected title crop.
- Exact inline blank-critical-line guidance at all three profiles; raw
  `Synthetic OCR chall` / `OCR\n` and the currently loaded image remained
  unchanged by refusal, with preparation, consent and metrics empty.
- Explicit detail reload retained the partial draft and cleared approval.
- Keyboard scrolling reached the 576-DPI right edge (54/54 CSS pixels in this
  wider live panel; not the archive panel's 540-pixel scroll extent).

Root inspected both fresh 576-DPI source endpoints before authoring the complete
reference and confirming it. The separate score/recovery probe (`73ece3`,
session74575 terminal0) corrected the fields without reloading the image,
prepared and confirmed the exact revision, and returned the actual UI comparison.
It then verified Fit invalidation, explicit Fit reload and Cancel preserving
the authored text without restoring approval. The returned declaration and
comparison were copied from that CLI result, not recomputed, into
`browser-score-observation.json` (SHA256
`0308f8ea09dde05a227c8b7aa481fa711350c6407044c2bea6a3e144b512db53`).
Both predictions had zero normalized CER/WER and 1/1 critical occurrence;
resolution/edge warnings and manual review remained required. The declaration
binds the actual 1242x108 RGB image at
`d64eab636380addfa0ae97da93315a49f15a61f09191dd9e23db61e9fa61fe72`.
This short crop is not a representative accuracy gain or a structural score.

The browser reported zero console errors/warnings and closed (`36efbe`). The
host's bounded launch loop ended and session85123 returned terminal0 (`4f199a`).
Shutdown readback (`326c9d`) reports both exits0, exactly two verified completed
calls, no uncertain cleanup or failed invariant, removed auth and empty private
caches. All 181 selected producer/input pins matched current bytes. Before/after
pin files are identical at SHA256
`9dd74b9f044ae6978d68a452879ea3cc2e4b906364878236b04e076b888669e4`.
No Save was performed; the helper, browser and all probe sessions are terminal.

Peirce independently reconstructed both fixed requests and dispatch bindings,
verified each five-artifact bundle and current 181-file pin set, and replayed
the complete pair/reference/comparison exactly (`f9bc75`, terminal0). His
nine-PNG review (`c39c08`, terminal0) found no new visual blocker. PNGs capture
UI containers, not independently reconstructed raw raster bytes. His first
audit-only import-denial guard stopped required installed-model-file verification
(`52bf06`); the unchanged strict reader then completed with its required package
metadata import, without inference/model construction or rendering. That audit
refusal is not an evidence mismatch or a passing first audit.

Read-only diagnosis (`ffa5da`) found a possible metadata-only refusal path:
cross-read preview comparisons retain handle ctime in their fingerprints. Four
double-read rounds over the eight exact producer files and fixed source/recovery
found unchanged bytes and content identities; no temporal handle-ctime change
was caught. Path-versus-handle ctime differences alone do not establish the
cause. Existing tests deliberately require strict ctime-only rejection, and
neither those guards nor production behavior were relaxed. The first refusal's
cause, and the earlier standalone refusal's cause, remain unproven.

## Actual isolated-worker raster refusal and recovery

Root fully read the helper authored by Socrates before executing it once:
`tmp/check_crop_detail_refusal_v1.py`, SHA256
`aae211b7c722bcbdffb4a714cd6e97cd8b77fc293d42912512e45b85a42bc75e`.
It used the existing generated PDF and a new **unscored** full-page source
declaration, not invented crop-pack or live-run authority. No PDF was authored,
no OCR was called, and no local renderer fallback was used. Four actual
`CropPreviewController` requests completed (session49376 terminal0, `3a1f95`):

| Request | Actual outcome |
| --- | --- |
| Fit | RGB 864x1152 returned |
| 288 DPI | `raster_limit`; projected 1728x2304, no image returned |
| 576 DPI | `raster_limit`; projected 3456x4608, no image returned |
| Fit again | RGB 864x1152; bytes identical to the first Fit |

Both detail requests exceed the side and pixel limits simultaneously; this is
not isolated testing of either limit or instrumentation of native allocations.
The identical raw-RGB SHA256 is
`19ffb4d43d6a2cdd6280b049e92c16016823d603b3514b64489beba413ac2b3b`.
Private staging was empty after every returned request; close succeeded and
removed the owned private root. All selected pins remained unchanged, with
identical before/after files at
`8368fcc0933541b8a1dc74341b1545684bc6ea20d59bf0ccefe420cc1c3fe0b5`.
The actual observation in `output/pdf/ocr-crop-detail-refusal-v1/observation.json`
has SHA256 `c510dd05fffd38e89710638a1d1a3f1c21573e3c51ba64f5ab2b22b16373237d`.
This supplies real isolated-renderer refusal/recovery evidence, not oversized
live/archive UI draft-preservation evidence or total decoder-memory proof.

Socrates independently checked all 179 selected current file hashes, byte
lengths and path identities and exact equality of the four attempt files with
the observation (`1efb22`, terminal0). His read-only review found no correction
needed in this renderer section or the following architecture section; no
OCR, rendering or tests were rerun during that audit.

## Architecture controls prepared for a fresh qualification cohort

The test-only update to `tests/test_architecture.py` (SHA256
`11503221271c02518d0b5091d8aab1fc54e821dd72081fac441879ce8c7b3073`)
pins the five intended first-party preview import edges and reciprocal inbound
sets, updates the archive raw-import expectation, and adds the presentation
helper to existing isolated heavy-import-denial and raw lazy-dependency controls.
Root reviewed the complete 18-added/6-removed-line diff against the immutable
previous qualified copy and checked the corresponding production import sites.
No source discovery, unknown-edge, cycle or transitive-boundary check was relaxed.

One focused run (`205968`, terminal1) returned **24 passed, 1 failed, 22
deselected in 3.43s**; Ruff passed. The exact tracked-cohort admission check
correctly refuses six modules still absent from the original index, including
the new preview helper and five prior crop-pack modules. The original index
and architecture baseline were not changed. This is not a passing graph gate:
complete graph verification requires the new explicitly admitted isolated copy,
fresh inventory/collection, and all nine qualification gates. The old qualified
copy and its evidence remain unchanged; historical or focused test counts are
not the new full-suite count.

## Remaining acceptance

The observed live and archive detail/reference flows now have actual browser
evidence, including the retained first live refusal and successful explicit
retry. Diagnose that intermittent refusal without guessing its cause or
weakening generation checks. Oversized UI recovery remains separate from the
completed isolated-controller check. Fresh full-suite, reviewed architecture
and retrieval qualification remain outstanding. None of these checks establishes
representative accuracy gains or completion of the wider improvement program.
