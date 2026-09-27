# Crop-pack Save/Open integration development checks, 2026-09-07

Status: local host, launcher and UI integration passed focused generated checks
and independent agent source review. The subsequent browser observation below
exercised real archive Save/Open and isolated original-PDF previews, but exposed
a selection-state failure. The separately pinned repair below passed 659 focused
tests and a fresh-host browser check. Architecture refresh and a fresh full-suite/all-gate
checkpoint are not established here. The [full OCR/AI program](../ocr-improvement-program.md) remains active;
this is neither representative accuracy nor release/correction approval.

The [usage and failure-recovery guide](../ocr-crop-review-packs.md) describes the
new fixed private directory option. Existing review without that option is
preserved. Archived review does not enable OCR execution, and packs remain
outside the AI reader and canonical extraction/index publication paths.

## Source identity

Dirty-worktree base HEAD: `e34103f70b676eacc8d55badb2468b8a10004ef4`;
HEAD tree: `af0a69ccde82b6b679580f07813495721978efe0`.
File blobs below are computed with `git hash-object`, not claims of a commit,
stored Git object, clean worktree or merged integration.

| Implementation | SHA-256 | Computed Git blob |
| --- | --- | --- |
| `ocr_crop_review_pack_io.py` | `894fc75df3b474a89a51984bea81dc3a127f1e9c0481d25d9ae3b692aa2438cb` | `df7bfa7a77884ff977cc27acb7f14b5a92efe572` |
| `ocr_review_crop_packs.py` | `0cd0c9ef70a8bf90dc154517bac18da51584e8c43b0a2f71bb26cc4d2d340ba0` | `d69ba4af9d28789cf963349ea52b6ba860399ae6` |
| `ocr_review_crop_ui.py` | `f15528eab9c8fa2e4a0981129218864bbf237df1a599527a3197fd2bdd742994` | `9a0a1c31b5751df8ee1b38e0ef83280363d709d5` |
| `ocr_review_crop_archive_ui.py` | `6a6c0aec18e2d84a975fb7ceb94cd977b589399c0235450d78f81cd04d37fc2e` | `1875c90500d7a641131041d540c39b3a5019195a` |
| `ocr_review_ui.py` | `855c1ea42fed121f0520f8a0369622380e3e50fc7a50525743b8a4f07566195d` | `23036c40dc697bf6176ae789066db3a02555a600` |
| `ocr_review_execution_ui.py` | `59fcdc3aabb3fde20f2c05dfd57b6ea394c33cb12e7360f1793cd8c6bb8df681` | `37dafeba9df49357d03bb908170cb4fe2845ec6b` |
| `tools/review_ocr.py` | `e8132dff6a5814c8a6593ea803a48afb9057f563214e45819df9d201fe38dcb4` | `a04f90c387c4dcb374ecd7ad0b3e8323b91b73aa` |

| Added/extended tests | SHA-256 | Computed Git blob |
| --- | --- | --- |
| `test_ocr_crop_review_pack_io.py` | `4570865c001d8986369bd1cbcceffa40fb2876080082f3a3ad8504dc31ae8d30` | `007207103bf44149a14c0504ec0593c23b82f209` |
| `test_ocr_review_crop_packs.py` | `93b33bbf05890b9cfd4a416938c6e22068c62bb28004b3b9841f88e943c3a988` | `713142b4af5b920c99ae3f516fd5c9536bc42808` |
| `test_ocr_review_crop_save_ui.py` | `615549d4a3aeb75c5c4c5cbf62391cbfdab16d06fcb9f93e80d610809191e9cc` | `c5c11528dab958ba2554f32e0be573ebb1c5fdd8` |
| `test_ocr_review_crop_archive_ui.py` | `a6df9c23aee3e40e6c839cdf5b7f399d10b6a8f11ddd24394dc5ff6a49bb6cfc` | `b777018e7d317d64a311c154e18e2e8cdf853aaa` |
| `test_review_ocr_crop_packs_cli.py` | `4b9a11ea5dfc7f4c236778719de75764b8bb44a4f37a1812cb3c3346bd7c4b60` | `31ad5431d3cb82ead115f5b91c1c3bd59aace739` |
| `test_ocr_review_crop_archive_integration.py` | `2dbbf32d88db7a866b2d1fdce13405f0ad3ddbd9da0d0fd1a2ccb7b8333cf908` | `8800d0bf3c6ecd6fec0244bf0e0fa61adcebcde3` |

Test paths in the second table are under `tests/`. These pins were rechecked
after the focused run. They are not a complete frozen repository/index audit.

## Focused combined run

The qualified Python 3.12 environment completed **624 tests in 201.14 seconds**,
terminal exit 0 (`933922`, session `76020`). The exact selected files were:

```text
tests/test_ocr_crop_review_pack_io.py
tests/test_ocr_review_crop_packs.py
tests/test_review_ocr_crop_packs_cli.py
tests/test_review_ocr_execution_cli.py
tests/test_ocr_review_ui.py
tests/test_ocr_review_scan.py
tests/test_ocr_review_crop_ui.py
tests/test_ocr_review_execution_ui.py
tests/test_ocr_review_crop_save_ui.py
tests/test_ocr_review_crop_archive_ui.py
tests/test_ocr_review_crop_archive_integration.py
```

The selection comprises 105 storage, 149 host/launcher/existing-review controls,
243 live-crop/execution UI controls, 125 archive UI controls and two real
UI-callback/host/store/scorer integrations. Earlier overlapping counts must not
be added. Ruff passed on all seven implementation and six added/extended test
files (`b5f701`). The earlier 469-test backend record remains a separate scope.

The two integration cases use complete generated region and hard-scan archives,
not an archive validator double. They publish a reviewed revision, instantiate
a new host and session, reopen by newly discovered opaque ID, refuse restored
approval, and publish a separate unfinished draft with no old review attached.
All retained non-review payload bytes remain exact. Source verification and
image rendering are explicitly inert in these tests. Restart is within one
Python process, not the earlier backend's two-process observation.

## Review findings and corrections

Independent agent reviews covered the new raw revision read, host/launcher and
both UI state/queue boundaries at the listed generations. They do not constitute
human transcription adjudication or independent native/browser observations.

- Revision reads reuse the single bounded strict-read byte cohort, after full
  source, manifest, tree, identity and hash checks. Existing full-read and
  recovery-only result schemas are unchanged.
- Host shutdown now tracks and drains active archive work. Failure or
  cancellation during revocation, waiting or preview cleanup latches uncertainty.
  Fixed document/path generations are checked before and after workspace
  verification. The launcher revokes archive and OCR admission before draining
  the remaining bounded shutdown budget.
- Early UI attempts exposed tuple-to-strict-JSON projection errors; these were
  corrected without weakening the shared serializer.
- Server-only action counters were insufficient for an old queued Save after a
  new score on the same image and identical text. Actual installed Gradio
  `Queue.push`/`process_api` controls now retain the old client-captured token,
  prepare/score again without reloading, and reject the delayed Save/Score while
  preserving the exact newer score. Default live-panel output arities are
  unchanged; enabled live outputs rotate their view token, while archive outputs
  carry a separate non-State action token.
- Image/metric postprocessing failures cannot make prior client tokens authorize
  newly computed server state. These checks concern Gradio processing, not all
  browser transport, clipboard, IME or platform behavior.

The first root callback/store integration attempt failed because its test
invocation omitted the newly required edit-action token. Adding that captured
argument completed both flows (`6047ca`, two passed in 14.23 seconds); the final
624-test run includes the corrected tests. No production guard was relaxed.

## Subsequent browser observation: original generation

The exact production generation above was held unchanged for an authenticated
loopback Chrome session using the production launcher, host, store, scorer and
isolated PDF preview. OCR execution was disabled. No new OCR, models, dependency
installation, private corpus, canonical extraction or index publication was used.
The input was the existing generated challenge, not representative source data.

Retained artifacts are under
`output/playwright/ocr-crop-pack-ui-development-v1/`. The create-only helper
`tmp/ocr_crop_pack_browser_v1.py` has SHA-256
`83e970437dcb0c1ebcd50ede2ea61331ee3fefdfd9331918838dd31fbfebd53e`.
Its cooperative parent import guard refused OCR execution/runtime imports; that
is not an OS sandbox, proof about all child imports, or native binary attestation.
The 199-file before/after cohort contains 170 root/tools Python files, the helper,
the original saved pack's 26 files, and the generated PDF/recovery. Hashing began
after initial imports and seed copying, so this is a bounded browser-window
observation, not pre-import or continuous identity proof.

Observed sequence:

1. Refresh left selection blank. Explicit selection followed by Open initially
   raised the static stale-view error (`01-open-stale.png`). Cancel/clear then
   Open succeeded; no hidden automatic retry was used. Installed Gradio's
   dropdown emits input at option selection and blur, making duplicate selection
   requests a plausible race. Original request ordering/hidden tokens were not
   captured, so that diagnosis is an inference, not a reconstructed trace.
2. The freshly rendered crop visibly reads `Synthetic OCR challenge`. Its
   displayed source identity is 311 by 28 RGB pixels, digest
   `0b50e5d90358189b4e1e15d47112a77ba878755e16a38b870b4e571b2b71833c`.
   Saved text and explicitly historical comparison returned; the current
   confirmation, preparation and comparison were empty (`02-open-fresh.png`).
3. Prepare, browser confirmation and Score produced a fresh comparison. This
   confirmation was performed by the automated generated-data check, not a
   human reviewer. Both candidates have normalized CER/WER 0 and the critical
   entry `OCR` occurs once. This demonstrates no accuracy improvement. Baseline
   and retry retain different raw whitespace and declared DPI/edge geometry.
4. Save reviewed produced a new immutable child. After a second fresh score,
   changing the text to unfinished `Synthetic OCR chall` cleared the current
   score. Save reviewed then refused the stale authority. Explicit Save draft
   succeeded without carrying the previous review (`03` and `04` screenshots).
5. Reopening the original seed again required fresh approval (`05` screenshot;
   despite its filename, this is not the new child). Browser reload returned a
   blank session (`06`). Explicit refresh/selection/Open then reopened the new
   reviewed child with historical evidence and no restored approval (`07`).
   Text-only recovery of the new draft restored the unfinished text and `OCR`,
   with no image, candidates, history, preparation or metrics (`08`).

| Retained pack directory suffix | Manifest SHA-256 | Expected relationship |
| --- | --- | --- |
| `dd82a0a3e6fe4690aedb85361f82b7fc` | `1e20a5cd3b6a7fc1b57daee9aa75c0880feca4aee2ea95f838322d3a3dcd100b` | Copied original generated seed |
| `5ffe59df9e6341dea2e5c1b0a42a2089` | `69d71ffcf8b0a61d41bb530f65b42b3c97d729ea247f4745ec6ef7f54ae65da7` | Reviewed child of seed |
| `c1e6b38472dc46dd927f191d9b0bee90` | `676b5a4c635a0da6ceefe4e7b4c858fa9de7ca505d33010061b803062d94418d` | Separate draft child of seed |

Selected browser snapshots under `.playwright-cli/`:

| Snapshot | SHA-256 | Observation |
| --- | --- | --- |
| `page-2026-09-07T21-13-09-172Z.yml` | `b48fa1c0542341dd40e936435d5f45517880a6170ce1c73d87dac485525a44ec` | Fresh score before reviewed Save |
| `page-2026-09-07T21-16-43-602Z.yml` | `698567b17e401ab995a753b34d116d804901dd89141ed3a12518226dd1389f95` | Blank/unselected session after reload and catalog refresh; the earlier `21-16-21-437Z` snapshot contains only Loading |
| `page-2026-09-07T21-17-04-084Z.yml` | `ca7f8729a99ede97a28831e552f1b26fa70c2c5c7697fe1ec4d34c10e5e94b5c` | New reviewed child reopened after browser reload |
| `page-2026-09-07T21-17-44-682Z.yml` | `ae079db57ce2afd47f3d30fa8082f9ad952151bef070fe5c1299ce9785d84bec` | Text-only partial draft recovery |

The browser was explicitly closed and the helper stopped normally. Server
session `20428` reached terminal exit 0 (`912fa3`). `source-before.json` and
`source-after.json` are byte-identical, SHA-256
`38210ea85f5305905f010ada610b61b42c3bcce7bec33cc7f37241e2cffffe20`.
`shutdown.json`, SHA-256
`981de60ac15096673f39124649a6a7e7a9f3b3bfee6fcafe955ffdbb3f62605b`,
reports unchanged pins, zero remaining `ocr-review-cache-*` Gradio display
caches under the run directory, no guarded parent imports, no launcher cleanup
uncertainty and removal of the helper-owned ephemeral
authentication file. All three packs and browser evidence were retained.
Pack existence and self-reported completion alone are not independent validation;
the following checks separately establish retained readback and visual state.

Independent fresh-interpreter readback completed with terminal exit 0 (`75b2ef`,
session `23664`). The stdout-only auditor
`tmp/audit_ocr_crop_pack_browser_v2.py`, SHA-256
`e6fe20cf595bd6744fed9acc066032dd406dc1c7353a7e65d68325b186974698`,
rechecked 289 retained artifact hashes and the entire 199-file source cohort,
including the exact current root/tools producer path set. Original plus all three
saved packs passed the real strict historical validators. The copied seed was
byte-identical; both revisions preserved all 24 inherited archive payloads and
referenced the seed parent. The reviewed comparison exactly matched the complete
JSON parsed from the pre-Save browser snapshot. The draft's raw partial text and
critical entry were exact and its reviewed field was null. Browser states were
also checked against retained review/draft declarations. This audit launched no
OCR/render/browser, read no authentication state and wrote no artifact or ACL.
It does not independently replay image pixels or authenticate human attention.

The preserved first auditor (`tmp/audit_ocr_crop_pack_browser_v1.py`, SHA-256
`0dbdffe0491d90f1eec09288bb1ea1cc55215b38ed5423521666eb3d89410aef`)
exited 1 (`5cdbe1`) because it wrongly required the draft-only declared-scope
pane to be empty. Recovery intentionally retains declared scope. The v2 auditor
requires exact equality with that scope while still requiring empty historical
candidate/metric panes. No production validation was weakened by this repair.

Independent read-only visual review rehashed and inspected screenshots `02`–`08`
(`1b3a1c`). It confirmed the readable generated crop, editable restored fields,
unchecked confirmation/empty fresh metrics on both reopened packs, blank browser
reload, and image/candidate/metric-free draft recovery. It does not establish
host restart, damaged-proof recovery, native image provenance, hidden-token
ordering or general device/platform usability. It also identified misleading
static full-verification/fresh-crop field labels in draft-only mode for correction.
Large raw historical JSON remains a usability limitation of this local UI.

## Selection repair: later source generation

Two added regression controls failed against the original generation (`a8e565`):
duplicate same-ID selection responses reproduced Open's stale-view refusal,
and a duplicate arriving during an active Open cancelled that preview. These
controlled Gradio processing observations establish a reachable race, not the
unrecorded original browser request ordering.

The later handler takes the browser-captured non-State view token as its third
argument. Stale captures return only skip updates without mutation. A valid
unchanged selection also skips, preserving the current image, preparation or
score. A genuine current selection change still clears those fields and revokes
authority. Explicit Cancel remains the way to reset the same selected entry.
Draft-safe neutral labels no longer imply that text-only recovery has rendered a
fresh image or verified the full archive.

| Later changed file | SHA-256 | Computed Git blob |
| --- | --- | --- |
| `ocr_review_crop_archive_ui.py` | `1dda8dbf60b2cf4630e42fc3b4a8fecfed53d25ee4ec080c458bcd90bb37bd33` | `aca7bde4191c3c497d64b83c731ca16dfa67594d` |
| `tests/test_ocr_review_crop_archive_ui.py` | `9b16bb22f4b9c96e554356badd1610d2678e890bf82f392313b895c31326b983` | `6ab6d99a59b8233922536afa5f17b292e0812e70` |
| `tests/test_ocr_review_crop_archive_integration.py` | `a1400069a1af3125d3603977efe2884942f66d00c93880812d72b4a1b8e9b89b` | `da68840f819dcc9e097e844e6e64503cfdeebb98` |

The owning agent's archive suite passed **160 tests in 19.60 seconds** (`c2bc88`).
That includes the two original red controls and additional stale/duplicate,
malformed-input and empty programmatic-change controls. Actual Gradio queued
stale different-ID/None captures preserve a newer scored view. These are still
processing-layer controls with inert service/image boundaries. The root
integration test now supplies the newly required captured selection token;
other production files retain the original table's identities. Ruff passed on
the three changed files (`8a71b3`). Independent source review approved the exact
UI/test pins (`2d4d10`). The root then reran the same 11-file focused selection:
**659 passed in 206.17 seconds**, terminal exit 0 (`79b34d`, session `20244`).
This replaces, rather than adds to, the earlier 624 count. Its archive subset is
now 160 instead of 125; all other selected subsets remain the same.

## Fresh-host browser check of the repair

`output/playwright/ocr-crop-pack-ui-development-v2/` belongs to a new qualified
Python host process and named Chrome session, not just a browser reload. The
reviewed create-only helper `tmp/ocr_crop_pack_browser_v2.py` has SHA-256
`2ea085c8b01326093d0cb066321d8ff4cefcf7258d5adc00e56f84113739471c`.
It reopened the three existing v1 packs with production components and OCR
execution disabled. No Save action was taken in this check; the UI's Save
capability was not disabled. The original v1 save/readback evidence remains
separate from this later UI generation's fresh-host observation.

- The new session began blank with no entry selected (`01-fresh-host-blank.png`).
  Explicit refresh/select/Open succeeded on its first attempt, reopening the
  reviewed child with the same readable fresh crop, historical text/comparison,
  unchecked confirmation and empty current metrics (`02-reviewed-fresh-host.png`).
- After fresh Prepare/automated generated-data confirmation/Score, reselecting
  the same catalog entry preserved the comparison instead of clearing it
  (`03-same-selection-keeps-score.png`). This one browser sequence is not a
  deterministic adversarial transport-order test; the separate queued controls
  cover captured stale events.
- Selecting the draft entry cleared the current comparison. Explicit text-only
  recovery restored `Synthetic OCR chall` and `OCR`, with neutral labels and no
  image, candidates or approval (`04-draft-fresh-host.png`). Full Open then
  rendered the original crop and restored candidates alongside that unfinished
  text, without a historical reviewed result or current consent
  (`05-draft-open-fresh-crop.png`).

The host's 251-file cohort contains 170 root/tools Python files, this helper,
the fixed PDF/recovery and all 78 files in the three retained packs. The complete
bounded pack tree has 105 file/directory entries. Before/after JSON is identical,
SHA-256 `de2835c4dff8d0358ef0a37806a91edf306ac02dbd89dca812003e27a70b7e15`.
`shutdown.json` has SHA-256
`61aebe12acf6af867b9b7892fa0625f4eb48602912963a7e2140122af0a6981b`;
it records unchanged source hashes/tree, empty invariant failures, no guarded
parent imports, zero `ocr-review-cache-*` Gradio display caches under the run
directory, and removed helper-owned ephemeral
authentication. Both launcher and helper report exit 0; server session `59549`
reached terminal exit 0 (`c32391`). A failed observed invariant in this helper
would produce a nonzero helper result, preserving the launcher result separately.
As before, this is not continuous identity, a child-process sandbox, loaded
native-binary attestation or representative accuracy qualification.
The cache count does not independently enumerate an external private preview
staging root; renderer cleanup is reported through the launcher's bounded close.

Independent read-only v2 verification completed at terminal exit 0 (`245947`):
all 251 current hashes and the exact producer/helper/input/pack-file path set
matched; all 105 retained pack-tree names/types matched. The checker initially
refused its own POSIX-only path normalization against Windows map keys
(`2754ae`); correcting that read-only interpretation established the final result
without changing any source or evidence. A separate snapshot/image readback
completed at terminal exit 0 (`822876`) and inspected all five screenshots.
The fresh score and post-reselection metric strings are exactly equal: 7,781
UTF-8 bytes, SHA-256
`f1ef1327da5f36e7a287b56b6cd7051b77bcdb1e3f2aa1e693cbeb9e253a8456`.
The prepared declaration also matches, with confirmation unchecked after scoring
in both views. Empty/neutral draft-only state and full draft Open without
historical/current approval were independently confirmed. Unchanged final files
corroborate the retained no-Save outcome, not continuous absence of transient
operations. No blocking issue was found within this generated workflow scope.

| V2 snapshot under `.playwright-cli/` | SHA-256 |
| --- | --- |
| `page-2026-09-07T21-30-06-800Z.yml` | `678c40c3b32f530bc4769e34875150c9d01a0b2030febfd18f63a883ac4562f9` |
| `page-2026-09-07T21-30-29-006Z.yml` | `d259bc0a7b44b37aec66c843fe2c9b2f603e82db739c8135acda200c9b028fc7` |
| `page-2026-09-07T21-31-19-885Z.yml` | `31bb0ecaf0dc168b5fa25c6fac8c831b27c530d9d513e918ea11db967fac79e8` |

## Remaining qualification

The architecture inventory still belongs to the earlier
Phase 12 checkpoint and has not been refreshed for these additions. No new full
suite, all-gate run, representative accuracy result, publication/rollback flow,
private-corpus permission or expanded AI authority is claimed here.
The default inventory and tracked-source gates omit the new untracked modules;
the real Git index was not mutated for this work. A blanket alternate-index
environment is also unsafe for the unchanged test suite, whose temporary Git
repositories run their own add operations. Any later qualification must explicitly
cover the new source/test cohort without silently changing the user's index or
mislabeling a preview inventory as the default tracked baseline.
