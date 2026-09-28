# Source-crop advice — native probe and archive-browser development

Observation date: 2026-09-09 UTC. The bounded generated-PDF native probe
completed successfully. Archive attempts retained a startup failure and a
preflight failure; a later strict attempt displayed Fit advice but did not
complete the browser journey or its final source-readback gate. These are
development observations, not full qualification or an OCR accuracy benchmark.
The separate [779-case focused record](2026-09-09-ocr-crop-advice-development.md)
retains the implementation and test/source pins. The earlier
[full review qualification](2026-09-08-ocr-review-qualification.md) describes
its older frozen generation and does not qualify this feature.

## Completed native probe

Artifacts are retained under `output/pdf/ocr-crop-advice-native-v1/`.
Root ran the PDF skill's create marker once (`640afd`), then the separately
pinned Python 3.14.3 / ReportLab 4.2.5 author mode (`3c0024`, exit 0). It produced
one 5,903-byte, nine-page PDF. Qualified Python 3.12.10 `-I -B` then consumed the
externally pinned PDF/author record and called production
`_render_pdf_with_view` and `build_crop_advice` (`67d197`, exit 0). Its observed
3.437-second command duration is not an OCR timing or measured speedup.

The fixed matrix used Fit, 288-DPI and 576-DPI crops on normal, reversed,
faint, nontext-rule, fractional blank, intrinsic 90/180/270-degree rotation,
and nonzero-CropBox pages; separate cases covered full-page Fit, deliberate
text truncation and oversized detail refusal. Actual counts were **30 render
invocations, 29 returned images, one `raster_limit` refusal, and 29 owner-close
attempts returning successfully**. No wider proposal was rendered, no fallback
was selected, and no OCR, model or browser was invoked by this probe.

| Observed control | Retained result |
| --- | --- |
| Fractional blank, Fit / 288 | 160×80 / 320×160; all four bands retain partial continuous geometry despite zero missing pixel centers; uniform ambiguity remains explicit. |
| Fractional blank, 576 | 641×321; no geometric loss, but still uniform ambiguity. |
| Rule touching the page edge | Left padding margin is zero; low/high edge counts also occur for this nontext control and are not clipping proof. |
| Rule at 576 | Original 1392×320 crop succeeds; wider-scope proposal is unavailable under the same profile's raster bound. |
| Full-page Fit / full-page 576 | Fit proposal unchanged, all four margins zero; 576 refuses before returning an image. |
| Nonzero CropBox | Observed native cropbox `[12,6,204,198]`, display rectangle `[0,0,192,192]`; scope and view bind that same actual geometry. |

Independent retained-only readback (`db44e4`, `bdffc6`, `e58efe`, exit 0) checked
all **71 artifacts: 29 raw RGB files, 29 JSON records and 13 original PNGs**.
Every listed size/hash and filename matched. All 29 successful records agreed
on PDF/page/scope/profile/RGB/view/advice bindings, including five canonical
digests per record and RGB byte lengths. The helper's separate Counter/array/
pixel-center arithmetic checked the actual RGB histogram, percentiles,
adjacent differences and edge accounting without calling measurement internals.
The auditor did not rerun that measurement or the renderer. All 190 captured
root/tools Python and pyproject paths still matched the author/render source
maps; the retained helper matched its current bytes. This bounded source map
is not a full qualification cohort or continuous native-code attestation.

Root visually inspected all nine Poppler 144-DPI page PNGs and all 13 original
crop PNGs: expected text polarity/faintness, rule/blank areas, rotations and
CropBox effects were visible; the deliberate cut truncated text ends. Poppler
completed (`7cea47`, exit 0) with missing-display-font warnings for Symbol and
ArialUnicode. Those warnings are retained as a limitation; the visible Helvetica
fixture output was usable. This was AI-agent visual review, not independent
human adjudication or evidence that advice improves operator accuracy.

| Native retained artifact | SHA256 |
| --- | --- |
| `generated-source.pdf` | `a03bfdf77f79871aa4c6d0d0685432f203197a580d5cb78bb028729a46aa5f24` |
| `author.json` | `c4a7fa3a3008d1c47964f42a5de8db4b9fe18e90802225be438e567197286bb4` |
| `render-receipt.json` | `7f660d8e802f93760b1cbcfd7e003cde784430e1f0afed0bdadcbd5d3a99c3f7` |
| `ocr_crop_advice_native_v1.py` retained helper | `8faaf46733b44c64ad6bc0a5c7a9e7f6e44f18fd791d73457901619761495240` |

The author executable SHA is
`cce21c0e8710e304273e98ac4b2b0f5aceb639acbcd2343cbaa5c4e81619c45b`;
the qualified renderer executable SHA is
`0b471133e110cfb53a061cad528ce8e517d7b9ac41a0a396c39ad795a487fc14`.
The author record retains the two ReportLab source pins. Before its first
ReportLab import, the helper blocked optional settings/mod hooks, redirected
USERPROFILE/XDG configuration to its owned empty directory and scrubbed Python/
ReportLab selectors; environment, import path and finder state were restored.
This is a harness-only configuration policy, not a sandbox or attestation of
every installed library. The helper-only path check follows the existing
symlink/junction policy without rejecting cloud-file tags alone.

## Archive-browser v1: retained startup failure

`output/playwright/ocr-crop-advice-archive-v1/` remains unchanged. Root's host
command ended exit 1 (`3cf59d`); `shutdown.json` reports launcher exit 2 and
helper exit 2, `stop_reason: not_launched`, and failed invariants
`launcher_returned_success`, `owned_app_not_running`, `operator_stop_received`.
These distinct recorded statuses are preserved, not recast as a clean browser
run or proof of a lingering process.

The root launch set TEMP to the RUN directory inside the repository, which is
incompatible with production `_temporary_parent`'s excluded-root checks
(`dfbab2`; source readback `520a87`). The first exception was not captured, so
this is an identified configuration incompatibility, **not a proven first
exception trace**. No successful browser-advice workflow was established.
Root readback `f7ba43` and retained hashes confirm unchanged source/input bytes,
copied pack and original parent inventories. Shutdown records auth removal;
it does not establish external cache absence or cleanup of unknown history.

The v1 helper SHA is
`6b9eb355ecb93c6a5095aec84d19c4c4b1ed80e5fc024753ee185b39b6364f67`;
shutdown SHA is
`b3cd2b1bdba163c67a6a635846c39ae66e793494cdadef1be9813cf818238fca`.
Source-before/after both hash to
`d0e69794fe7d4cf8748c21f36076b30f75af4cac16a94e35285c6c7780575a71`;
copied and original parent inventories before/after both retain
`26f2b756d2c098da8b5c279128a778218031ac064736d29e0eeb2c2231cfce67`.

## Archive-browser v2: preflight failure and partial browser observation

`tmp/ocr_crop_advice_archive_browser_v2.py` is prepared at SHA
`47bfc113f0f1aef56e6a89acdd07903c17193a875910f76271b8bf76f15f659b`,
targeting fresh `output/playwright/ocr-crop-advice-archive-v2/` and an external
temporary parent. Its first invocation (`cf80e9`, exit 1, 0.312 seconds) refused
the before/after opened-handle metadata comparison during `code_inventory`,
before creating RUN or starting the application. The exact changed field was
not captured. No browser session or shutdown receipt exists for that invocation.

Read-only checks (`ce6709`, `a60f38`) found all 189 selected source/lock hashes
still equal to the expected `d0e69794...575a71` map; a second byte read agreed.
Path/handle ctime representations differed, but within-channel drift was not
reproduced. A separately bounded 1,890-read check (`090801`, 3.109 seconds) also
observed no within-handle change. This does not identify the earlier changed
field or establish a repair. No guard was relaxed. One further startup attempt
with identical strict helper/source pins and still-absent RUN opened the host
(`941b5b`, session 30465). No v1 result or later observation erases either failure.

The second invocation used the fresh external temporary parent
`%LOCALAPPDATA%/rag-pipeline/ocr-crop-advice-browser-temp-v2`.
It preserved the production renderer, isolated preview worker and authenticated
literal-loopback launcher. Root used the cached Playwright CLI with a fresh
named browser session. An initial wrong relative authentication-state path
failed (`9f9358`), then the correct owned file loaded (`6c8ca5`); credentials
were not printed. Refresh, selection of the sole saved entry, and Open displayed
a fresh Fit crop (`1cf1af`) with advice in the details and caveats in status.
No confirmation, annotation, Prepare, Review, Score, Save or OCR action ran.

The displayed declaration binds page 1 and the original bbox to a 311×28 RGB
Fit view. Source, scope, profile, dimensions, view digest and RGB digest agree
between the raster declaration and advice binding (`ee8dd0`). This is not an
attestation of decoded browser pixels, which also display annotation overlays.
The confirmation checkbox remained unchecked; the fresh prepared declaration
and fresh comparison fields were empty. No partial-text edit, detail-profile
change, draft-preserving Reload or Cancel journey was exercised.

The 900-second interaction deadline ended the host (`c4874b`, shell exit 1).
Shutdown records launcher exit 0, helper exit 2, app not running, auth removed,
unchanged original/copied pack bytes and identities, no new OCR-run names and
no launcher-cache names. It also records `source_readback_unconfirmed`, failed
`selected_code_and_inputs_unchanged`, and no explicit operator stop.
There are **no source-after/input-after artifacts**, so this is not a passing
browser qualification or a clean final source-readback gate. A later separate
root byte-hash observation (`d01a70`) still matched all 189 expected source/lock
pins; it does not retroactively satisfy the failed helper check.

Root captured the still-displayed Fit image and full page after host shutdown,
then closed only this named browser (`ee8dd0`). Both screenshots were visually
inspected (`d0faf2`); the crop and advice were visible. CLI session 99382 ended
exit 0 (`a2d060`); host 30465 is terminal and was not restarted. Root observed
the external temporary parent empty with its original identity (`d01a70`). An
unexpected top-level `vibe_edit_history` directory appeared in RUN; its contents
were not inspected or deleted. No complete private-cache/history cleanup claim
is made. The unexercised journeys and final-readback diagnosis remain pending.

Independent retained-only audit (`cec3c8`, `ca1b22`, `6548da`) confirmed the Fit
declarations, 256-bin histogram summing to 8,708 pixels, displayed 5th/95th
percentiles 1/255, and gradient pair counts 8,680/8,397. Displayed historical
review equals the saved review, with one unresolved annotation, two revisions,
no pending selection and no Save request. All four original/copied before/after
inventories match; each named pack's 26 files (1,160,687 bytes) matches its
declared hashes. This audit neither reran the renderer nor inspected history
contents, and does not repair the failed final source-readback gate.

| Browser-v2 retained artifact / declaration | SHA256 |
| --- | --- |
| `ready.json` | `1a3284eb0434781b0dde6fb01d00d2bc9ba8d1741ed336c6e786e98bbfd76825` |
| `shutdown.json` | `f985b717b77c34ab78ed58c235adbb4898d4604a89333bd4c4862383f5681a99` |
| `browser/crop-fit.png` | `e3181a101f52cef2a16183f91d914415b76748193dc3e030083621447ad25c1e` |
| `browser/fit-page.png` | `b949509e3f13c2320426dae4eb6339d9e5a425692d1a46d0abd1fdb7719c82ab` |
| Retained Fit browser snapshot | `e5d25ec4f9dcb1519d578aeaf7a3ca25d372c54d57212f0a3af3fe01bd620505` |
| Displayed raster view | `ddab0dcac121011f14f220161f95de4685ee11c76a7f4586df769d0dc64e1a80` |
| Displayed RGB declaration | `46b7e975e0a23128b92cef158c9e718efda277f554b94b5430812f9481891b5e` |
| Displayed advice | `7970045c8585c139c93b3021c16aa494c90156b9e860cc89b7aee0afd5e877d7` |

The PDF and Playwright skills guided generated visual checks and browser
observations; Gradio guidance informed the local authenticated launcher and
unchanged event contract. No production source changed during these probes.
Root `d01a70` reverified original HEAD/tree, raw index, architecture baseline
and size guard against the preceding focused record; all remained unchanged.
Original HEAD remains `e34103f70b676eacc8d55badb2468b8a10004ef4`, tree
`af0a69ccde82b6b679580f07813495721978efe0`. No inventory generation, cap increase,
original baseline installation, index update, commit or remote publication ran.

## Limits

The measurements concern annotation-on rendered appearance, including rules,
backgrounds and overlays; an `available` status is not a quality pass. Pixel
extremes do not prove clipped text, and preview DPI is not source acquisition
resolution. Exact declared padding margins stay within two source points;
native clipping/raster rounding can differ. A proposal remains a new scope
requiring explicit selection and its own reference/approval, not an automatic
render, retry or reference transfer. There is no new CER/WER, representative
corpus, human-review, AI-reader authority, full qualification or speedup result.
