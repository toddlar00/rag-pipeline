# Native uncertainty workflow: partial run and initialization repair

Date: 2026-09-08. Development evidence only. The prior
[copied-source qualification](2026-09-08-ocr-uncertainty-qualification.md)
remains a separate, unchanged checkpoint. The source changes below have focused
checks, not a new full qualification or completed native uncertainty journey.

## Actual native run: clean comparison only

The authenticated production review launcher, actual source renderer and local
RapidOCR path were exercised against the fixed generated challenge PDF. The
harness admitted at most two UI-authorized one-region Start attempts, ordered
300 then 400 DPI; it did not manufacture preparation, consent or approvals.
Both actual calls completed. Evidence root:
[`output/playwright/ocr-uncertainty-native-live-v1`](../../output/playwright/ocr-uncertainty-native-live-v1).

| DPI | Run ID | Call receipt seconds | Clean normalized CER / WER |
| --- | --- | --- | --- |
| 300 | `8eb4c4789dde48768c73baa97cda252c` | 1.141 | 0% / 0% |
| 400 | `50980c0c30ce404aa898edc6b715ea6f` | 1.188 | 0% / 0% |

This is **one generated title crop**, page 1 / `region-0001`, not an eight-page
benchmark. The entered reference, `Synthetic OCR challenge`, has 23 characters
and three words; the critical entry `OCR` matched 1/1 occurrence for both runs.
The 400-DPI raw output has different line breaks; the measured scorer collapses
whitespace. Both candidates remain review-required. The UI retained effective
edge/resolution warnings and `canonical_extraction_modified=false`. There is
no accuracy gain, representative-document result, causal DPI comparison or
repeatable speed claim here. The count-only profiler also changes instrumentation.

The retained
[clean UI snapshot](../../output/playwright/ocr-uncertainty-native-live-v1/.playwright-cli/page-2026-09-08T15-48-15-698Z.yml)
binds the two actual raw report digests and shows one scored pair, no annotations
and no unscorable pair. The
[clean checkpoint](../../output/playwright/ocr-uncertainty-native-live-v1/counts-clean-reviewed.json)
records one `compare_ocr` and two `evaluate_ocr` exact Python code-object calls.
Counts inspect no arguments, authored text or results; they are not independent
human approval, nor evidence of the unresolved zero-scorer path.

The live Annotate control remained inert. No uncertainty markup was applied,
no unresolved review or Save completed, the private pack directory is empty,
and fresh-host reopen/resolution did not start. The
[root browser observation](../../output/playwright/ocr-uncertainty-native-live-v1/inert-editor-observation.json)
records the unchanged initial notice and button markup; its final DOM capture
was after the host deadline, without current component configuration. It alone
does not establish initialization cause.

Host session 44544 terminated with process exit 1 at the deadline. Retained
`shutdown.json` separately records launcher exit 0, helper exit 2 and the sole
failed invariant `score_checkpoints_retained`: both unresolved checkpoint files
are absent. It records two completed calls, unchanged selected sources/inputs,
restored profile hooks, auth removal, empty private caches and no cleanup
uncertainty. Browser closure was observed separately (`87ee67`). This failure
is retained, not relabeled successful because OCR or cleanup succeeded.

Independent retained audit `879d66` exited 0: all ten native bundle hashes,
manifest/execution/report joins, the two completion counts and clean request
binding matched. It did not rerun OCR, scoring or the production validator.
Details are in the ignored
[partial audit](../../tmp/ocr_uncertainty_native_live_v1_partial_audit.md).

## Real lazy-tab diagnosis, before the repair

The separate
[lazy-init-v1 diagnostic](../../output/playwright/ocr-uncertainty-editor-lazy-init-v1)
used the unchanged production editor/preview JS with generated 800-by-400 RGB
pixels, real Gradio lazy Tabs and a closed Accordion. Business callbacks were
numeric diagnostic substitutes: no PDF, OCR, journal, scorer, Save or approval.

1. Root clicked **Send skipped editor outputs while closed**, then opened the
   Live tab/Accordion, loaded the generated image and clicked Annotate. The
   control stayed at its initial notice with no pressed/disabled attributes or
   numeric acknowledgement. Retained red
   [snapshot](../../output/playwright/ocr-uncertainty-editor-lazy-init-v1/.playwright-cli/page-2026-09-08T16-02-28-437Z.yml)
   and [image](../../output/playwright/ocr-uncertainty-editor-lazy-init-v1/skip-before-mount-failure.png);
   browser terminal observation `788126`, DOM observation `281290`.
2. A fresh browser reload of the same host, **without sending the skip**, then
   opening/loading/clicking Annotate produced the pressed button, overlay,
   Annotate notice and exact numeric mode acknowledgement. Retained positive
   [snapshot](../../output/playwright/ocr-uncertainty-editor-lazy-init-v1/.playwright-cli/page-2026-09-08T16-03-57-891Z.yml)
   and [image](../../output/playwright/ocr-uncertainty-editor-lazy-init-v1/no-skip-positive-control.png);
   terminal observation `2af806`.

Diagnostic lifecycle terminal `226374` was exit 0, browser closed `6de627`.
Shutdown records two generated loads, one skipped batch and one Live Annotate
mode command; selected pins were unchanged, blocked native/business modules
absent, auth removed and owned cache absent. Lifecycle success does not turn
the preceding red browser interaction into a passing workflow.

The installed Gradio HTML update path can replace nested custom props on a
skip before lazy mount. The old script obtained its fixed image ID from those
props and could return before wiring controls. The narrow repair inserts the
server-selected, JSON-encoded image ID directly into each component's script,
admitting only the two existing fixed image IDs. Numeric value parsing and
image/action/mode-token admission are not loosened. Six focused regressions
failed before the repair; all 139 editor tests passed after it. The new tests
combine real `process_api` skip serialization with an explicitly inert Node
geometry/event port. They are not browser mount acceptance.

## Fixed-source browser regression

The separate [lazy-init-v2 diagnostic](../../output/playwright/ocr-uncertainty-editor-lazy-init-v2)
used the repaired production editor. After the same skipped-before-mount
sequence, both Live and Archive initialized with properly disabled controls
until Load, then accepted explicit Annotate commands. Live keyboard selection
returned `[400,200,410,210]`; mouse dragging returned `[120,80,280,160]`.
Archive keyboard selection returned `[351,200,361,210]` in an 800-by-900 browser
viewport with detail presentation. Its “576 DPI” selector only changes the
presentation of generated pixels here; no native DPI render was performed.

Root observed the exact numeric acknowledgements (`439fbe`, `57a5ca`,
`8915f9`), working Live Browse/Annotate after tab re-entry (`37b0ab`), and another
successful mode round trip after a second skipped batch (`0a96c0`). Browser
console command reported zero current messages/errors/warnings; the retained
log also contains one verbose password-form DOM diagnostic, with no errors or
warnings. Retained images include
[Live pointer](../../output/playwright/ocr-uncertainty-editor-lazy-init-v2/skip-fixed-live-pointer.png),
[Archive keyboard detail](../../output/playwright/ocr-uncertainty-editor-lazy-init-v2/skip-fixed-archive-keyboard-detail.png)
and [repeated skip/re-entry](../../output/playwright/ocr-uncertainty-editor-lazy-init-v2/repeated-skip-live-reentry.png).

The owned host exited 0 (`66625e`), and the named browser was closed (`7675b5`).
Shutdown records two generated loads, two skipped batches and nine numeric
commands, unchanged selected pins, no blocked imports, removed auth and cache,
and no cleanup uncertainty. Its SHA-256 is
`55f325c8cb6a1facd21fbb128a256aaed1194feb6c2018c0950203c7762c638a`.
The earlier no-skip positive used old source; it is not this fixed-source green
control. These diagnostics establish the repair in real lazy layout, not the
entire production app or the native uncertainty → review → Save → fresh-host
reopen → resolve journey. That complete journey still needs a fresh run.

Independent retained review (`798d76`, `9c93d3`, `a510d0`) matched all eight
selected pins against current files, reconstructed both fixed scripts, checked
the nine commands, and visually inspected the three screenshots. Before/after
source maps both hash to
`42dab713ed8c8702333f3c69d1f0e31dd447e51fef64cb498048244b23001e5f`.
Separately, six host/queue/edit-lease compatibility files passed **268 tests**
in 236.53 seconds (`0ab26a`, exit 0). All 11 selected producer/test pins remained
unchanged; readback `73f4a9` verified zero failures/errors/skips. These use
generated/inert ports, not new native OCR or browser business workflows.

## Separate inherited-environment hardening

Review found that an isolated parent does not itself scrub inherited Python
startup variables from a child launched with `-u`. The native helper already
had a harness-only pre-import scrub; its record found no removed selector names.
That run is not evidence of actual inherited contamination or of the later
production repair.

The shared supervisor now removes inherited `PYTHON*`, `_PYTHON*` and
`__PYVENV_LAUNCHER__` names case-insensitively and seeds no-bytecode,
no-user-site and UTF-8 defaults. Explicit trusted host overrides remain
supported, including benchmark-owned import/hash settings. Parent environment
is not mutated. This is a startup-selector policy, not an all-environment scrub
or OS sandbox. Fourteen controls failed before the change and passed afterward;
three use harmless generated standard-library children, not OCR/model inputs.
Separate compatibility checks passed 77 with three platform skips, plus two
explicit benchmark-environment tests. These counts overlap no claimed new full
suite and are not OCR accuracy results.

## Reproduction and generation pins

Tests used the qualified local Python 3.12 interpreter with `-I -B` and the
pytest cache provider disabled. Environment checks explicitly disabled plugin
autoload; root's editor commands did not set that option. Rows below retain
separate generations/scopes; do not sum
red and green runs or describe the compatibility selection as full qualification.
Root observed editor red terminal `7d5243` exit 1 and green/Ruff `14cc34` exit 0;
environment terminals were `f5af7d`, `fd9b7d`, `8ae086`, `6d0231` respectively.
Times below are retained XML suite durations, not outer shell durations.

| Retained XML under `tmp/` | Outcome | Seconds | SHA-256 |
| --- | --- | --- | --- |
| `ocr_editor_skip_props_red_v1.xml` | 6 failed | 2.964 | `9c130ceba95dd0ff5f71857827d0382520735e0b18b3e35a7bf591aa6fb1e754` |
| `ocr_editor_skip_props_green_v1.xml` | 139 passed | 5.536 | `476edbd1d50b98c5eff46f3f5c56781e013dd2b7871c12f578615cfae39c1942` |
| `ocr_editor_fixed_identity_host_compat_v1.xml` | 268 passed | 236.504 | `0e522e42f6f7d66f066170e8431b87706f1c568a88f72d8a70492d57b77bd5b9` |
| `ocr_process_environment_red_v1.xml` | 14 failed | 0.733 | `01b016c0b7e29a6ac1a32899fbc2bc175bef43143e4e225b3090ce5d1e1cc47a` |
| `ocr_process_environment_green_v1.xml` | 14 passed | 0.310 | `49ce0ea722626e75833406dbfc4021d6799edd535f5037132680f58959fca517` |
| `ocr_process_environment_compat_v1.xml` | 77 passed, 3 skipped | 8.025 | `cf478db56d0df89a44387be24dc90fdb2fd398374420d5ea05eb0a139371680a` |
| `ocr_process_environment_explicit_compat_v1.xml` | 2 passed | 0.209 | `c173fb127e4c11b2bce523c4ef6f7ef5e99034fd4df004cd61c5a36411fb066b` |

| Selected source/helper | SHA-256 |
| --- | --- |
| `ocr_review_crop_uncertainty_editor.py` (fixed) | `3184ad8cdedd56ebe36bb1bf2fd52f5d7c41d142c6629568c4a6487fb3fbc08d` |
| `tests/test_ocr_review_crop_uncertainty_editor.py` | `543b7f2478da363685c61cb4a8f97ae7cd6ad18db7bfa65802bd263e2030851a` |
| `process_supervision.py` | `dbec069eab58ac8e07441ada1c37c914f86cddcc3380a439e94ce14d877ddafc` |
| `tests/test_process_supervision_environment.py` | `b61d86a9fada468b94f2373852201c0afc527d6600b534b937cf88a2a56160f4` |
| `tmp/ocr_uncertainty_native_live_v1.py` (executed) | `75a8e1fef8551faf5a36ec57c7467b0f3c93997a3153f25ebdb315ffabbab3fc` |
| `tmp/ocr_uncertainty_editor_lazy_init_v1.py` (old editor diagnostic) | `43196feee993dd44d5f826bccd23d3e870236ad10fbe30c64a62c9aa62e844cf` |
| `tmp/ocr_uncertainty_editor_lazy_init_v2.py` (fixed editor diagnostic) | `0ef3f7d509e31aa72474321e6d71e474126deddf920a7d3bc4abba0415d51349` |

Native report hashes are
`3862a8f7a36d6cc9ef5a79cfc63bbf301efe4a58a76abcf40a2fe0b3d335d12a`
(300) and `07d35a83f927c99d85a8edbd27337382ebd0dff9c2b102eb57c47878e09daa74`
(400). Native shutdown SHA is
`032b8af7e31c0dd741fd25c55c2bc4a20afa46c9dbb3ec070594f413f152ccfe`;
clean snapshot SHA is
`db21ba690694ea23f886d7a8798da34380a1f8ff2b3e8d66bf09d676810cf038`.
Native before/after source-map bytes both hash to
`3999ab048d42244bc982012b75439558635d1061b1a3523ee78e51ad18eaed19`.
The lazy diagnostic used old editor
`83c28f28cae72ad10ca4e6f7baf3b62fffdfe772a34d272e7de7bea4667588c0`;
its shutdown hashes to
`82c00ecde3855f6625ea38a1084fc9421b4f86df1548c5be698b34835c88d4a6`
and both source maps to
`762a68d88498252923528db00676c88506d9be14d5ec3f83c614dc271d101f9e`.
These pins distinguish generations, not continuous source/environment attestation.

The original HEAD remains `e34103f70b676eacc8d55badb2468b8a10004ef4`, tree
`af0a69ccde82b6b679580f07813495721978efe0`. The original raw index remains
`6a841daa2ac15f397750fc0f88f1bd6e93b78326823d2452e3ab14c88d0335f3`.
These identities were rechecked read-only (`8205d3`); they do not identify the
new dirty-worktree source by themselves. Neither the approved inventory cap
nor the earlier qualification record was changed by these repairs.

Computed Git blob identities, without writing Git objects (`6f139b`), are
`b237c69d237047756c8a0f61d4151c1e74324509` for the editor,
`f30419a0cfd3441aa21d71493905d1e0c66fc794` for its tests,
`cc62fe355da39d4d46d1fb79aa83bc148eb07856` for the supervisor and
`1d1e30046622f59fbf32551536318e56e0e83eff` for the new environment tests.
