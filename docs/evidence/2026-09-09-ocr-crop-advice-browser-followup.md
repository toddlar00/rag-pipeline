# Crop-advice browser follow-up

Observation date: 2026-09-09 UTC. This is generated-fixture development
evidence, separate from the [native and partial browser record](2026-09-09-ocr-crop-advice-native-browser.md).
That earlier record and its failed/partial runs remain unchanged. The preflight
repairs below changed only development harnesses. A later narrow production
repair changes the archive annotation selector to user-input-only dispatch;
its focused regression and fresh v6 browser observations are recorded below.
The original architecture baseline/index and inventory cap remain unchanged.
This is not a full qualification or OCR-accuracy result.

## Retained preflight failures and read-policy repair

The v3 harness (`tmp/ocr_crop_advice_archive_browser_v3.py`, SHA-256
`655fed793396f4f05c830358628d8777abda4906d48c7a098481e0938860704d`)
terminated with shell exit 1 before production imports or browser startup.
Its field diagnostics identified only opened-handle `ctime_ns` changing for
`ai_pipeline_client.py`, from `1788914454427266000` to `1788918415325063600`;
the other six handle fields were equal. The intended v3 run directory remained
absent. The failure is retained in
`output/playwright/ocr-crop-advice-archive-v3-preflight-failure.json`.

The new v4 harness (SHA-256
`3ff63b28bdc567eab63e70c2d2f1a676ebc3633f31340572aa7804d52b2f30fc`)
treated this field as observational only for immediate root/tools Python and
five exact root lock/policy files, requiring equal bounded rereads and exact
pathname metadata. Eleven mock-only stdlib tests passed in 0.044 seconds;
`output/playwright/ocr-crop-advice-browser-v4-read-checks.log` retains that run.
The test script SHA-256 is
`171e99d94dfd78fe3de0f344747e7f94063dacfb9c96b6964cf279bc35d24713`.

The v4 launch then terminated with shell exit 1 at its fixed-input preflight,
before creating its run directory. Its 16 diagnostic rows were already full
of source-code handle-ctime observations; the particular failing fixed input
was not recorded. The complete command output remains in
`output/playwright/ocr-crop-advice-archive-v4-preflight.log`.
A separate read-only diagnostic of all six fixed inputs found matching expected
SHA-256 values, two equal byte reads and unchanged full pathname metadata.
Five of those inputs exhibited only opened-handle ctime drift during those
separate observations. This does not retrospectively identify the original
failing input or make v4 a pass. The diagnostic is retained in
`output/playwright/ocr-crop-advice-archive-v4-fixed-input-diagnostic.json`.

V5 explicitly admits only the helper, qualified interpreter, fixed generated
inputs, selected code/locks, 52 exact original/copied pack-member paths derived
from the pinned inventory, and 12 fixed owned report/control names. Other paths,
including Git indexes, are refused before identity/open. Every admitted Windows
read now requires two equal bounded byte reads. Only opened-handle ctime is
observational; device, inode, mode, link count, size, mtime and full pathname
metadata including pathname ctime remain strict, as do all expected content pins.
The first metadata refusal is retained separately from the 16-row observation
cap. This is an explicit development-harness policy change, not a production
guard change or a claim about the cause of drift. Python documents Windows
ctime as platform-specific and deprecated in 3.12; that does not diagnose
these observations. [Python stat documentation](https://docs.python.org/3.12/library/os.html#os.stat_result)

The v5 helper SHA-256 is
`ee214c1ed5054ba1fe068063a46e0b43fdbb521172093fd8b2480c2b53513812`.
Independent AI review, not human adjudication, approved its complete source and
diff. The visible-action browser driver SHA-256 is
`322ca907b118fce4d940085a0028409721d894905dd9342a38abbb671bf4b3bd`.
Its intended journey is Fit, partial draft, 288-DPI Reload, 576-DPI Reload,
Cancel, then Fit Reload. It never checks approval or invokes Prepare, Review,
Score, Save, OCR or wider-region rendering. Raw visible declaration/annotation
JSON is retained for subsequent independent checks; parsed convenience values
alone do not preserve all Python numeric representations.

## V5 execution: clean harness shutdown, failed initial browser check

The v5 read-policy script
`tmp/ocr_crop_advice_browser_v5_read_checks.py` (SHA-256
`a4a8037410aec4537d2f66048a7e9994d59d8fa59d1ae001879a9615968ae4fa`)
passed all 11 mock-only tests in 0.049 seconds. Its retained log is
`output/playwright/ocr-crop-advice-browser-v5-read-checks.log`.

The actual v5 host reached authenticated readiness. The fresh named Chrome
session loaded the private auth state, reloaded, and was inspected before the
driver ran. The driver terminated with exit 1 at `initial-admission`, with
`failure_code: catalog-not-refreshed` and an empty observations list. Its only
business action was Refresh. No archive Open, profile change, draft edit,
Cancel, approval, scoring, Save or OCR action was reached. The retained DOM
showed an unsolicited annotation-form-change notice instead of the catalog
completion text the driver awaited. This alone does not identify which listener
fired. No v3/v4 browser journey occurred either.

Root then issued the exact create-only stop control. Host session 62945 ended
exit 0; the launcher and helper both returned 0, and the owned named browser was
explicitly closed. The shutdown SHA-256 is
`6e0a0a059f9c3273fde3588ef8cf05eb1e18df20ec3db57ed17b6feca122194d`.
All ten recorded shutdown checks passed, including selected code/input and
original/copied pack invariance, auth removal, stopped app and explicit stop.
The 54 metadata observations comprise 16 retained rows and 38 omitted details;
all retained rows are ctime-only observations and the first-refusal field is
null. These counts are checks, not distinct mutations or diagnosed causes.

Independent AI readback verified all 189 selected source/code-lock paths against
both maps, the six fixed inputs, all 26 files in each named original/copied pack
(1,160,687 bytes each), and the four identical pack inventories. Protected
original index, architecture baseline and size guard remained unchanged.
`output/playwright/ocr-crop-advice-archive-v5/` retains the command logs, initial
and failed DOM snapshots, failure screenshot, input/source maps and shutdown.
Root visually inspected the failure screenshot. Its top-level
`vibe_edit_history` was not entered, read or deleted; no complete-cache-cleanup
claim is made beyond the specific shutdown predicates.

The v5 browser interaction remains a failure despite the clean harness shutdown.
Later success does not reclassify that attempt.

## Selector feedback repair and focused regression

The uncertainty-enabled archive builder registered the annotation selector's
`.change` listener alongside the ten ordinary form controls. Server responses
also update that selector. Installed Gradio event documentation distinguishes
`.change` (user or function updates) from `.input` (user updates only). Refresh
clears the form digest and emits an empty selector update; a programmatic
selector event could therefore invoke the form invalidator and overwrite the
catalog notice. This source-level feedback path is established; the retained
v5 browser snapshot does not prove which event actually dispatched.

Only the selector registration now uses `.input`. The ten other form listeners,
raw-reference listeners, legacy builder, callback inputs/outputs and authority
checks remain unchanged. Independent AI source/test review verified the exact
inverse repair reproduces the previous production SHA-256. Five new test cases
cover the exact listener graph, captured selector-input handler invalidation, identical-repeat
no-op and stale-event preservation of newer prepared/scored authority.

The new wiring case first failed against the old production registration
(one failure, 2.60 seconds), then passed after the repair. The three complete
archive/advice test files subsequently passed **289 cases in 50.21 seconds**,
with zero failures/errors/skips. These are 169 legacy archive, 64 uncertainty
and 56 advice cases, not 289 additional cases beyond the earlier overlapping
779-check development selection. Scoped Ruff returned `All checks passed!`;
its two-file command scope is retained in root tool result `8601d8`, not encoded
in the short Ruff log itself.

Artifacts are under
`%LOCALAPPDATA%/rag-pipeline/ocr-crop-advice-event-development-v1`.
Independent retained audit found that `collection.log` itself was truncated by
the tool-output capture: it contains only 188 exact matching IDs plus one
corrupted/spliced line. It cannot prove collection/JUnit coverage and remains
unchanged. The green JUnit independently contains 289 unique passing cases.
A subsequent collection-only audit captured child stdout/stderr directly to
create-only files. Its 289 unique collected IDs exactly match the preserved
passing JUnit identity multiset. Collection reported 0.67 seconds; the child
took 0.922 seconds and the helper recorded 3.75 seconds including pin checks.
The command and helper exited 0 without timeout, errors or handle-ctime
observations. This is a source-bound subsequent collection, not recovery of
the original collection bytes, continuous source attestation or a test rerun.

The audit's `collection-audit-v1/` retains its exact command, helper, 56,145-byte
stdout, empty stderr, complete node list, source-before/after maps and receipt.
All 189 selected sources, three test files, configuration, interpreter and green
JUnit pins match before and after. The receipt SHA-256 is
`3918bcea58f097ea08d65abb1fbb7b20635495dde498b5163017d46981ef1b9d`;
helper `tmp/ocr_crop_advice_event_collection_audit_v1.py` is
`19a002cd628ed20f46f98b0c342beef94f493dab0a775657a92bec3608feb6b1`.
The full node-list SHA-256 is
`7d2d23a12b2381a76fe5d0a9ccbce1c8709aa0d27d3259344c226d142280fd93`;
the direct stdout SHA-256 is
`0727b1c1c0a13e2c68b9f89a594a07bd34c4c9e628c95a428adcd7d024b025e5`.
Independent retained readback verified the exact ordered node list, JUnit
identity multiset, all six new artifact hashes/sizes and all current source
pins; all six original log/XML artifacts, including the truncated collection,
remain unchanged. No helper or test was rerun for that independent audit.

| Artifact | SHA-256 |
| --- | --- |
| `ocr_review_crop_archive_ui.py` | `fb2789a33a6a13b8e69a9830d9f471eb8903e1007dac1463aa35f333d727194e` |
| `tests/test_ocr_review_crop_archive_uncertainty.py` | `a796f938dc3d6de7c9f9b34a2a86889581802cdf83859b4ff1ba1bbde33d7aed` |
| `tests/test_ocr_review_crop_archive_ui.py` | `835b8301046d6f343cfe210dcc09496ca57934c0cf0a0c90739da78a86b5d676` |
| `tests/test_ocr_review_crop_advice_ui.py` | `904d26b1bf7184a4e2c0b4164fc75f5fb2fa20be358f1ebd1197ec0cb43bc360` |
| Original truncated `collection.log` | `6f962b246adaa2f41548c6a412f0f4c2a1b088b05374b1fd361e133a5bd66e07` |
| `green.log` | `b6a039e4e4cad0b266048f5a8e59d861232fc77578c33cf1a80444ee07b5b2e4` |
| `green.xml` | `e6e8a794801e1b9cc1bbad2fca80a8c0aa6e92ec3ec338b6cd298326e392c531` |
| `red.log` | `4ef3d3f7a5eb369cc4775bad9d05b53077386367052ebfe6d3a3ba6287597578` |
| `red.xml` | `382dcc271f77bf32a03e23af21ee604a892f73f2af16008f88564dc3875a6de4` |
| `ruff.log` | `82b3e6a6c090a57601d22943bd23fca9218d1031dbe5a7b754092f9a156b4f18` |

## V6 generated archive journey and shutdown

The fresh helper/driver differ from their reviewed v5 versions only in their
generation header/output name. The host reached authenticated readiness; root
read its fresh DOM snapshot before executing the visible-action driver once.
It completed Fit, partial transcription/critical-entry edits, 288-DPI Reload,
576-DPI Reload, Cancel and Fit Reload. Five screenshots and raw browser details
remain under `output/playwright/ocr-crop-advice-archive-v6/`.

| Observed phase | Natural RGB dimensions | Draft dirty |
| --- | --- | --- |
| Fit | 311 x 28 | false |
| 288 DPI | 622 x 55 | true |
| 576 DPI | 1242 x 108 | true |
| Cancel | image/advice/history display cleared | draft retained |
| Fit after Cancel | 311 x 28 | true |

All loaded observations bind the same generated source and physical scope
`85b9a1309317f2f2bbbea53a4c56ce887e3f7c278774e12cc536bc6cdf57bf77`.
The final raw transcription remains `Synthetic OCR chall` and the critical
field remains `OCR\n`, including its deliberately unfinished trailing line.
The driver checks exact historical display preservation on reload, unchanged
committed annotation projection, no pending selection, unchecked consent and
empty prepared/current scoring outputs. Cancel clears the displayed source
evidence without discarding the draft; explicit Fit Reload restores it. No
Prepare, Review, Score, Save, new annotation, OCR or wider-region render occurs.

Root visually inspected all five screenshots. Fit and 288-DPI title displays
are visible; at 576 DPI the image exceeds the visible horizontal panel extent.
The existing detail CSS deliberately uses natural-size images in a scrollable
viewport, with arrow/Home/End keyboard handling. The v6 driver does not exercise
that navigation. It establishes loaded natural dimensions, not right-edge reachability or
decoded-browser RGB identity. It does not prove every race/network order,
complete journal-state equivalence, human adjudication or accessibility.
The final observation is at 15,199 ms; this single UI-journey duration is not a
test-suite speedup measurement.

Independent read-only stdlib audit parsed the raw declaration strings, not the
JavaScript convenience objects, and reproduced scope, view, domain-prefixed
pixel-observation, padding/proposed-scope and advice hashes for all four loaded
views. Histogram/percentile/gradient internal counts reconcile; rational endpoint
checks keep each proposed margin within two points and each proposed raster
within the unchanged caps. No proposed raster was rendered. Initial/final Fit
canonical declarations match exactly. The logged result and embedded executed
driver match their separate retained files after canonical JSON comparison and
line-ending/trailing-newline normalization, respectively. Artifact SHA-256 pins
remain hashes of the exact retained bytes. The first audit
snippet used `p5` instead of the declared `p05` field and failed; corrected
read-only inspection passed. This was an audit-script error, not a product run.

The exact per-phase history, raw draft and unchecked/empty scoring claims rely
on the retained driver's assertions, supported by captured annotation summaries
and final history/draft values; separate per-phase snapshots of every field
were not retained. The audit does not recompute measurements from decoded
browser pixels or replay native geometry. The final historical display hash is
`3799811141b1faedbcc77a1a3ed9e5514adfe8f9afacdd2920a8c81b602348c9`.

After the driver returned exit 0, root issued the exact stop control. Host
session 44201 returned exit 0; launcher/helper both returned 0. The owned named
browser `cropadvicearchivev6` was then closed. Both handles are terminal; no
restart or repeat is required. All ten shutdown predicates are true, with no
failed invariants, cleanup uncertainties, metadata rows or omitted observations;
the first-refusal field is null.

Independent retained host audit verified both 189-entry source maps against all
current selected bytes, both six-input maps, and all 26 files/1,160,687 bytes in
each named original/copied pack. All four pack inventories equal the original
external pin; original index, architecture baseline and size guard are unchanged.
These combine the reviewed helper's in-run checks with independent point-in-time
readback, not continuous metadata stability or loaded-code attestation. Root's
separate external temp-parent enumeration was empty. The run's top-level
`vibe_edit_history` and unexpected cache/history contents were not entered,
read or deleted; no universal cleanup claim follows.

| V6 artifact | SHA-256 |
| --- | --- |
| Helper `tmp/ocr_crop_advice_archive_browser_v6.py` | `e30f308e20fb83b631a0761b657f593bb13cbabff86c970f6d14d5440e418e02` |
| Driver `tmp/ocr_crop_advice_browser_journey_v6.js` | `89161393666f5aa961f539375bb736241cfa279d400ace442d26b4a7b2d96f31` |
| `browser-journey.json` | `c82117416d59f19bf9f9774f42b5ef8ece5c9cd86a2ebd96d862aff532419572` |
| `browser-driver.log` | `0b5cf03f3b790a7282140054a96d24e09523679f5f6ec119e978820a51d6f34d` |
| `shutdown.json` | `42e7248ac7b1ec19c60f788388d23cf8db4d20549497eeabe75aa71786d8cd2b` |
| `ready.json` | `868730b42376c93db22cad92e90ef73261bc5bd9bcb510e9de6b4ffadd9489f2` |
| Each source map | `aad0dcab2efec6dfe5fb9881603658c2dec8a90834f4eeac56b0b5a1ca5fd7b4` |
| Each input map | `3a1bb1875256a0a9acc68fb28d1e97fb2a1601895d13e18c96c2428a278218b1` |
| Each original/copied pack inventory | `26f2b756d2c098da8b5c279128a778218031ac064736d29e0eeb2c2231cfce67` |

No full-suite run, parallel/GPU pilot, representative accuracy measurement,
private-corpus access or new AI authority is established by this follow-up.
Fresh admitted-cohort inventory review and full qualification remain pending
under the unchanged 1,952,138-byte cap.
