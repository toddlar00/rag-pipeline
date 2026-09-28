# Crop uncertainty pack/host development, 2026-09-08

The uncertainty backend now supports versioned private packs and explicit
coordinator/archive authoring and comparison APIs. Generated store/service
journeys preserve unresolved references through save, restart, resolution and
rescoring. This is focused development evidence, not full qualification, a
browser workflow, representative OCR accuracy or completion of the program.

## Focused checks

| Scope | Passed | Console duration | Evidence |
| --- | ---: | ---: | --- |
| Declaration recovery/append, existing journal and v2 comparison | 356 | 9.51 s | `3985c1`, declaration JUnit v3 below |
| Pure v2 pack policy | 90 | 7.55 s | `89963c`, policy JUnit v3 |
| Versioned storage and commit-time parent check | 67 | 21.63 s | `af989c`, storage JUnit v2 |
| Legacy pack storage | 105 | 31.50 s | `fddc83`, console only; source pins stable across run |
| Archive hosts, existing archive service and image disposal | 232 | 334.24 s | `e394bd`, host/service/disposal JUnit v1; before the final detached-output check |
| Four generated workflow cases plus 44 launcher cases | 48 | 40.56 s | `18dc8e`, workflow/CLI JUnit v2; before transient-lineage/recovery routing additions |
| Four workflows with transient-lineage/recovery assertions | 4 | 38.40 s | `e4d3e0`, workflow final JUnit v1; before the final detached-output check |
| Final targeted host author/compare/history/ownership checks | 83 | 162.59 s | `a71520`, final targeted JUnit v1; 16 cases deselected |
| Final four generated workflow cases | 4 | 38.76 s | `0ddc10`, workflow final JUnit v2 |

These rows are deliberately not summed: later workflow runs repeat earlier
cases, and the final narrow host correction is qualified separately below.
The 356 cases comprise 43 new declaration cases, 138 existing journal cases and
175 v2 comparison cases. Successful rows reported no failures or skips.
All use the qualified local Python 3.12 interpreter, `-I -B`, disabled automatic
pytest plugins and disabled pytest cache. Console durations include a small
amount of work outside the JUnit suite timer. Ruff passed for all 13 changed/new
source and test files at `680167` after the final narrow correction.

The final one-line comparison guard and authoring docstring correction were
followed by the 83 targeted host cases (including two new fault controls) and
four workflow cases. The earlier 232-case and 44-case launcher coverage is
retained at its stated generation, not relabeled as a final full-suite run.

## Implemented behavior

The journal adds report-free declaration replay and an append API returning its
detached journal/declaration/hash result under one cumulative replay budget.
The original journal-only append API retains its signature and behavior.
Recovery validates the declared source binding and complete bounded history;
it does not establish that declared report, recipe or raster hashes are true.
It does not invent a report, invoke a scorer or restore consent.

Pure v2 pack policy retains the fixed file tree and existing byte limits. Exact
integer versions select v1 or v2; booleans, floats and mixed schemas are refused.
Uncertainty is explicitly `{journal, annotations, dirty}`. Partial draft text
and line endings survive; a clean draft must match the replayed head, and dirty
annotations must match the deterministic reset state. Strict reviewed loading
uses actual archived proofs and v2 comparison. Unresolved references are
whole-crop unscorable with zero OCR scorer calls. Resolved references retain
unchanged full-text v1 metric semantics. No unknown span is guessed or masked.

Revision validation uses the actual strictly validated parent capture. It keeps
the exact source/pair/proof binding, base, revision prefix and prior views.
Explicit v1-to-v2 migration requires a valid genesis; an existing nonnull v2
history cannot be nullified, rebased, truncated or downgraded. Dirty-to-clean
transition requires an explicit reset in the appended suffix. No history
compaction, budget reset or new OCR execution is introduced. Parent hashes are
integrity joins, not authentication of a fabricated caller-created capture.

The store retains manifest-last, create-only publication, fixed-tree admission,
source checks and cleanup. Its new cheap parent check rechecks the catalog/root
and exact manifest/review bytes during each publication commit. It does not
replay every historical scorer per file. The archive host composes it with the
existing current-generation callback before and after that check.

The live coordinator and archive service explicitly expose metadata previews,
v2 authoring and v2 comparison, with selected-pair/source checks around work.
Metadata previews must be the exact owned `CropRasterPreview` type; failed
transfers remain under the image-close guard, without fallback. The opt-in OCR
launcher shares the new preview method with the archive service; this does not
enable OCR. Archive v1 APIs refuse v2 history instead of projecting it away.
`recover_draft_v2` preserves bounded uncertainty/history from damaged-proof
packs without candidates, scores, images, view tickets or restored consent.
The existing v1 UI already rejected extra draft fields; this API split is not
evidence of an earlier silent-loss bug in that UI.

Archive author/compare admission cheaply checks parent continuity on supplied
history before current authoring/scoring. Completed authoring also checks its
detached output. Final review identified the same detached-output check was
needed after reference construction in comparison: another actor or fault seam
could change the original mutable input between admission and copying. The
final correction rechecks the detached reviewed journal before the comparator,
without another full replay. Authoring itself does not score the current draft,
but strict archive reading may replay historical scored parent metrics.

## What the generated journeys establish

Four cases cover regions and hardscan routes through two journeys each. They
use complete real pure archive validators, actual private store/service code,
generated images and explicitly inert workspace verification/render ports.
They are not native renderer, worker, browser or independently transcribed OCR
observations. Raising sentinels guard model lookup/installation/runtime capture
and inappropriate scoring after fixture construction.

- Mark a whole crop unresolved, obtain unscorable review, migrate v1 to v2,
  save, start a new catalog/service, reopen at another preview profile, and
  refuse comparison without fresh confirmation. Preserve history and proofs.
- Refuse null/rebased transient history and v2-to-v1 downgrade. Explicitly
  resolve with a fresh view, then perform full-text scoring and append a child
  pack with unchanged parent prefix and proof bytes.
- Preserve dirty away-and-back edits; refuse clean saving without the required
  reset, and accept an explicit reset while leaving unresolved state unscorable.
- Damage generated proof bytes, refuse strict open and v1 recovery, then recover
  the v2 draft/history only. Recovery and unresolved scoring call no OCR scorer.

## Retained failures and unavailable observations

- The first declaration run had 353 passes and one test failure (`912c8b`). A
  lowered monkeypatched limit did not change a Python captured default. The test
  was replaced with an actual 256 KiB prospective-envelope overflow. Production
  limits were not relaxed. The next run passed 354; the final append-result API
  added two cases, yielding the 356-pass run above.
- The first policy run passed 67. Its expanded run had 89 passes and one fixture
  failure: a fake 20,001-character line hit the existing 4,096-character line cap
  before the intended scoring-work limit. The fixture now uses a legal line and
  a longer reference to exercise the actual 25-million-cell limit. The final
  90-case run passed without weakening production admission.
- The first new storage process (`58235`) emitted initial progress but its
  terminal observation was unavailable after context handoff. An explicit poll
  confirmed that process handle missing before a fresh run. It is neither a
  pass nor a failure in this record. The fresh retained-XML run passed all 67.
- The first host run had 50 passes and 12 failures (`bc4e4e`, console only). The
  fake renderer recorded image transfer before invoking cancellation admission;
  cancellation actually raised before transfer. Test boundary ordering was
  corrected. Additional exact-owner-type controls were then added. Initial test
  lint findings (two lambda assignments) were corrected too.
- A five-file compatibility run had 359 passes and two failures (`54c438`). An
  old fake controller lacked the newly explicit `render_with_view` method. The
  summary survived, although its middle traceback was truncated. A two-case
  retained red recheck (`5f60d7`) captured that failure before the inert method
  was added to the fixture. Assertions and production fallback were not weakened.
  The affected three-file suite subsequently passed all 232.
- Initial workflow/CLI checks passed 48 (`7288a1`); the repeated 48 and later four
  cases above are later source generations, not additional distinct coverage.

Root reviewed the production deltas and test boundaries. Separate agents
reviewed journal declaration replay and host lineage/ownership, including the
final detached-output correction (`34be58`). These are agent
reviews, not human adjudication or independent corpus ground truth.

## Exact identities and retained JUnit

The table pins the final dirty source, not a commit or a full-qualified snapshot.
The 232-case host run and first four-case final workflow run used service SHA
`0c340ba8776ddabf6ecfec27e483128790610c64f8ba584a6a6a7a645afe6421`
(computed blob `6f5b05d8355b78602b9d80490b09275112ee53fc`) and host-test SHA
`8ecb854075fe29d4c1cc68d0790431d5fd1db9a197fbd73f145d5a1049ede333`
(computed blob `bfa38dc7768ad3fd6debeef9fdce4dafcdac2fd9`). Only that service
guard/docstring and its two new controls changed for the final 83-plus-four
checks; the other listed files stayed fixed.

| File | SHA-256 | Computed Git blob |
| --- | --- | --- |
| `ocr_crop_uncertainty_journal.py` | `fa69efe9435b4b9ee3089d1b1a918e1f2eb642a3018c4744637aeef5b330b329` | `becf914c7afd1a5f438d34630eecee59388eaa54` |
| `ocr_crop_uncertainty_pack.py` | `5108e3df2d807ac71e58a31063a49c0934142a4f08a9ea743e0492ad8f418640` | `09a994ee569474f6c5c3dceeb797cc39e9c58bcd` |
| `ocr_crop_review_pack_io.py` | `ad407cf7be7ed35e849d8456c44625d7938e35be0d27fcb8ad6d52d175f05b5c` | `915f94bf02ab910dc567c8ca5500d597ca6ce1c8` |
| `ocr_review_execution.py` | `c62c3f32f0ab52eeb811c65f02cdb987c3d14622317a0b0ade8c2cc415c50830` | `26abc04b4ceee0b38aaf4eb5aa0104d8360ef78b` |
| `ocr_review_crop_packs.py` | `16846030bcc8117ab678ef47f73acb634479666782a0b9ff5b26b9c0d30459ed` | `94c2459f0780bef09519165944cedaef43f761ad` |
| `tools/review_ocr.py` | `63ffb0016e5f128c2e694b224953d686bad5767ab3b3a593f6ae871cf795005d` | `31c448bea2c02c509893d14ce6e4432e63c0c328` |
| `tests/test_ocr_crop_journal_declaration.py` | `083f3e3a81d32e51ba1ef5cbffee84aa0d07f8a2eed210cbf4135672105e6e41` | `96d4f8ba20cb2af75f582c942cf73fd62828e5fb` |
| `tests/test_ocr_crop_uncertainty_pack.py` | `08853492661e50f37aa8071e0b2d963890d6aa1375e0e400ac32cbf2a4157cc2` | `578edebc9fec11d3411d553fc649838f2dbaad3c` |
| `tests/test_ocr_crop_uncertainty_pack_io.py` | `15844fc9c72f8ba6802ee7849856eddfcbad8f2d8b0ca5455fc5c8fdaf05dc8e` | `da7f7f7e72b9093b2663ef29e416992db1ab0ac2` |
| `tests/test_ocr_review_uncertainty_hosts.py` | `61f35d15d39a7490d004523a2340461dfd5b8decc77b76c15fbb5b478c1d43cd` | `354e324ca3d4b0be6d5a4b946162e5eb009882bb` |
| `tests/test_ocr_review_crop_packs.py` | `e467fa09def81d0b6e1fb707c8768ef25dcaa69780f83eaf3f27220551683331` | `e9ca2c2f3020537409c82d829f63cfc2ab3fd735` |
| `tests/test_review_ocr_crop_packs_cli.py` | `8ce87a325d4e3fd20df671982db14bc36a56322701dd1f60de9e0d0b77381e1f` | `23ab9e2dd03bb837ebf36905fe1f1ce35a67b33d` |
| `tests/test_ocr_crop_uncertainty_workflow.py` | `b086d7c07f0693c6e8c0198d5cd3a620e33ef513beed5d43dfdb95b53e8795f1` | `d13c55e224991380c7ef60fd19ed9fb52761b582` |

| Private JUnit path | SHA-256 |
| --- | --- |
| `tmp/ocr_crop_journal_declaration_focused_v1.xml` | `99f943abbed282c3df6d4a820f519f5d35966cf9b8b639d84cd4156d9c27c4f8` |
| `tmp/ocr_crop_journal_declaration_focused_v2.xml` | `c341572305e9e295f9579f1d63022d7c060fcd71540147d01333bf7d0800444e` |
| `tmp/ocr_crop_journal_declaration_focused_v3.xml` | `a7f51af5489a135ce85e4b510e27f498c82fae7cf8e23f37552b2d0b64a2583a` |
| `tmp/ocr_crop_uncertainty_pack_v1.xml` | `6dcb0f217ec696a279b08a79ab986d22bb8bcdacc148fbb9646af6c0e9715765` |
| `tmp/ocr_crop_uncertainty_pack_v2.xml` | `4a427fb408275f26266f86f598e96accb8fd3749080c817b788734c57c7122bd` |
| `tmp/ocr_crop_uncertainty_pack_v3.xml` | `94056b3baf7862941da6d4a8f05f3133224df5b5bee5baba8f9bc0eae38fe040` |
| `tmp/ocr_crop_uncertainty_pack_io_v2.xml` | `54fd1c2e016564c9d7ca1694693c0f4cf64ae7c3d3db277fb6107b6ecbf9b8cb` |
| `tmp/ocr_uncertainty_hosts_legacy_fixture_red_v1.xml` | `89da582e537d3076bf570cc330e67039fdc02878385e1d623be85f00d1857eae` |
| `tmp/ocr_uncertainty_hosts_service_disposal_v1.xml` | `53f09ae53452229ae571831e1773fdf1c488de5c3d73e2fc7c9e9cce7436d616` |
| `tmp/ocr_crop_uncertainty_workflow_cli_v1.xml` | `c3fbce0831a769cfc3779dc3a90f39a7c0c7574473da5fa4ca1cf34c4e6162f6` |
| `tmp/ocr_crop_uncertainty_workflow_cli_v2.xml` | `f8bb1d82d221598ebe6cf2f9ea6d10375320ccd80273a402eb899ea098107b90` |
| `tmp/ocr_crop_uncertainty_workflow_final_v1.xml` | `02c6f6d9c2cb287ffcdffaeaf6f33dd3e1158d86052eb87f982fbf0aac62155a` |
| `tmp/ocr_uncertainty_hosts_final_targeted_v1.xml` | `e787e11551755d1e3c89e00b13172f12beb7512c6cf52a3c1a4898c566005b8d` |
| `tmp/ocr_crop_uncertainty_workflow_final_v2.xml` | `9c615622c7ff6d7a77d57448f93018d52b47423386200e725a4fb2e5c3430246` |

Original base HEAD `e34103f70b676eacc8d55badb2468b8a10004ef4` and tree
`af0a69ccde82b6b679580f07813495721978efe0` do not identify these dirty-worktree
changes. Original raw index remains
`6a841daa2ac15f397750fc0f88f1bd6e93b78326823d2452e3ab14c88d0335f3`;
the architecture baseline remains
`8c9d9991aa6864131915b6f0392ae4bf4c3f9a4af5e8ae0757e78e9d47aa792e`.
No Git objects were written by computing blob identities. Earlier completed
qualification copies/evidence remain historical and do not qualify this source.

## Still required

The explicit pointer/keyboard annotation editor, real image-plane alignment,
fresh view/action tokens, stale-event and failed-image-delivery safeguards, and
live/archive consent handling remain unfinished. Current browser panels remain
v1-only. Actual Queue checks and the generated browser save/reopen/resolve/score
journey are still needed, followed by full integrated architecture/qualification
checks. No full nine-gate run was repeated for this partial backend step.

All [67 program requirements](../ocr-improvement-program.md) retain their names
and acceptance evidence. No private PDF, model acquisition, new OCR execution,
canonical correction adoption, representative accuracy gain, AI write
capability or release was authorized or established by this checkpoint.
