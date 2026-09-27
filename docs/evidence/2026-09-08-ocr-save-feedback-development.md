# Long-Save feedback — development verification

Observation date: 2026-09-08. This is a later, focused development check, not a
replacement for the [v4 frozen qualification](2026-09-08-ocr-uncertainty-repair-qualification.md).
That checkpoint does not qualify these subsequent changes. No representative
OCR accuracy, throughput, release, hosted CI or complete-program claim is made.

## Change and source identity

Both uncertainty-aware v2 panels now distinguish browser request acknowledgement,
admitted saving/verifying, checked success, and unconfirmed completion. A dedicated
polite, atomic status region stays separate from the progress indicator. Fixed
client presentation events preserve the original Save arguments and do not mint
approval. Identical repeated refusals update correctly through the HTML component's
own value. Initial external-guide fetches failed; the Gradio skill, installed
pinned API/source and subsequently retrieved official guide content informed
implementation and test review. Playwright guided the browser checks.

Save still consumes the existing one-use authorization before host work. The
pending yield returns no new editor/image/action capability. Resumption rechecks
the captured view before the same host call; admitted archive declarations are
bounded, detached copies. Normal live cleanup and terminal selection share their
existing lock, with owner-conditional cleanup on iterator close or abrupt unwind.
No new busy state, automatic retry, approval restoration or rollback promise was
introduced. Legacy-v1 Save behavior was not changed.

Original HEAD remains `e34103f70b676eacc8d55badb2468b8a10004ef4`, tree
`af0a69ccde82b6b679580f07813495721978efe0`. Raw index SHA-256 remains
`6a841daa2ac15f397750fc0f88f1bd6e93b78326823d2452e3ab14c88d0335f3`.
These are working-tree changes, not a new commit. Computed blob IDs below do not
claim staging or object publication.

| File | SHA-256 | Computed Git blob |
| --- | --- | --- |
| `ocr_review_save_feedback.py` | `cb9a03bbb79772be58d837f6a477e97a092cd0d1d24b3c61dab75df7c40e014a` | `43d81e920a56cf8e5e91050f37e30c752dfaca6a` |
| `ocr_review_crop_uncertainty_live_ui.py` | `3c948e0ffffc4e3daba2fbcd6ead6a5f45f6231033ce43042e4746864b36f54e` | `a8f84a0782aa87062c148f77aeb077e74701c4bc` |
| `ocr_review_crop_archive_ui.py` | `ffe31efdbd86a1004bd44600de7edbb3286379e910180c591e7729a04023562d` | `f82f5f8bd0c5d2a882ac00d92604788a3b3f492f` |
| `tests/test_ocr_save_feedback.py` | `3e2f8c0b5f7550e4239a3d63be301524270604c32221ca8698a8198626e512e0` | `c2bf211fd620cbb42a0597501a9f97280e8f6fbd` |

Five existing v2 test adapters were also updated to consume the same original
Save iterator; they never reinvoke Save to finish it. Their exact identities are
in the retained source maps. Independent source review found and rechecked the
detached-declaration, repeated-message and cleanup-order fixes. Separate fixture
review found no remaining blocker. Neither review substituted for execution.

## Focused execution

One run passed **482 tests, zero failures and zero skips**, with no pytest
warnings reported. Pytest reported 286.55 seconds; the parent observed 290.172
seconds. Ruff passed on the three production files and six changed test files.
This was the original working tree, using the qualified Python 3.12 interpreter
with `-I -B`, explicit test files, no pytest cache provider and no third-party
plugin autoload. No Git command or architecture-baseline update was run by the
test driver. Its deadlines control the direct child, not a process-tree sandbox.

The eight-file cohort includes both v2 panels, queue/in-flight/edit-lease controls,
host integration and legacy archive regression tests. New controls cover pending
before host entry, captured arguments, stale/newer approval isolation, iterator
closure, edits/Cancel before resumption, host/receipt faults, abrupt exceptions,
and an actual Gradio pending-status postprocessing failure with zero host calls.
That last control explicitly closes the captured original iterator; it does not
claim that browser disconnection automatically stops a running host operation.
JavaScript tests use a declared small DOM/props double, separately from the browser.

Create-only evidence is retained under the local application-data directory
`rag-pipeline/ocr-save-feedback-focused-v1`:

| Artifact | SHA-256 |
| --- | --- |
| Driver `ocr_save_feedback_focused_v1.py` | `575a1da2992fbd813b770628455cd0ef99ee7300cb071465c52e999919a06112` |
| `receipt.json` | `481f8aa7147394dae092152f3e4d4f436925027e839e4d38651b31afe2273aba` |
| `source-before.json` and `source-after.json` | `308de08c9997970efd9d50bc4a3b7b47247ac012e6df266558ccda9dd5f0f0e7` |
| `pytest.xml` | `ae4e025cb7dc88e5d6ab54f56fb156ad2162b23d44a7f9e39d5425bb0c0f295f` |
| `pytest.log` | `aa6453233bf4a4668afb574fd181f96d355b15455c5b379886a467e40913fbf3` |

The driver also retained exact commands, exit codes, Ruff output and per-artifact
sizes/hashes. Source, driver and interpreter hashes were unchanged on readback.
This is not full collection/qualification or continuous loaded-byte attestation.

Independent retained-artifact/document review matched the reported test counts,
timings, source/blob identities and focused/browser artifact hashes. It also
confirmed the unchanged 67 requirement/acceptance pairs. No checks were rerun
for that read-only audit.

## Generated browser and real-storage observations

A private literal-loopback host used actual v2 controls, authoring/comparison,
pack service and immutable store. Source verification, PIL pixels and live
retained-result readback/capture were explicit generated ports. No native PDF,
OCR or model acquisition ran. Every capture/Open, text entry, preparation,
confirmation, review and Save used the production browser controls.

Four service calls reached the controlled, bounded pre-store-lock hold:

| Attempt | Panel | Action while held | Storage delegation | New manifest commits | Verified return |
| --- | --- | --- | --- | --- | --- |
| 1 | Archive, reviewed | Edit transcription, Cancel, then release | 1; real store refused stale authority | 0 | No |
| 2 | Archive, reviewed | Explicit reload and fresh review, then release | 1 | 1 | Yes |
| 3 | Live, reviewed | Queue another Save, then deliberately refuse held call | 0 | 0 | No |
| 4 | Live, reviewed | Explicit Cancel/reload and fresh review, then release | 1 | 1 | Yes |

The queued stale request did not produce another service entry. Totals were four
service entries, three storage delegations, two manifest commits and two verified
returns. Strict shutdown readback found the original generated seed and exactly
two complete new v2 packs. Attempt 1 retained the edited browser text and reported
unconfirmed; attempts 2 and 4 displayed checked success.

The browser showed requested → pending → terminal transitions in both panels.
Two consecutive pre-admission archive refusals each returned from requested to
the identical unconfirmed message. Both mounted status roots had `role=status`,
`aria-live=polite` and `aria-atomic=true`, remained connected across updates/tab
switches, and stayed fully opaque during held work. Screenshots showed the local
progress bar without dimming the status. Archive Save was also activated with
Enter on the focused production button. These are DOM, visual and activation
observations, not proof of audible screen-reader announcements or a fully
keyboard-only workflow.

The initial archive preparation omitted its required reason and was refused;
a subsequent stale Review raised the existing error. Explicit reason entry,
Cancel/reload and fresh preparation recovered. A few automation commands using
a partial snapshot had missing-reference errors and were corrected with a fresh
snapshot; no success is inferred from those commands. No service call resulted
from those early refused attempts. The retained server output includes that
expected stale Review exception, although browser console inspection at the end
reported zero warnings/errors.

Create-only browser evidence is under `output/playwright/ocr-save-feedback-v1`:

| Artifact | SHA-256 |
| --- | --- |
| Host `tmp/ocr_save_feedback_browser_v1.py` | `4f7acf09185ef2bbe753f4a17f2579074b820cf7efcee4351ab9056acc44e10f` |
| `source-before.json` and `source-after.json` | `0380d103b8a0bb96f9fa14c598702dc17e2095c20eacc4eed262ae1ad391ff56` |
| `save-observations.json` | `90701655e7ca52d23da8eb2dac91a78dadb46527ecf1cbf6e41bb21e03c8edf0` |
| `saved-pack-summary.json` | `3d782fdf54f3f9b59f98ecd86a0f6b137e17afb7ddb6ac1394dc262e057138ed` |
| `shutdown.json` | `f7d75b175cbee3de440a7aace619f308a610a9777de6697d1967e71467a93e72` |

The `.playwright-cli` subdirectory retains snapshots and screenshots, including
archive pending/success at `20-02-03-391Z` / `20-03-57-184Z` and live
pending/success at `20-07-26-449Z` / `20-09-14-752Z` on this observation date.
The owned browser closed and the host exited zero after explicit stop. Service
drainage, unchanged producer hashes, absence of native modules, removal of the
temporary browser authentication file and disposal of its private cache were
confirmed. Generated packs and observations remain retained.

## Remaining scope

Fresh full qualification of this later source generation remains pending. The
artificial hold is not a Save-performance benchmark. Browser disconnects,
universal transport ordering, synchronous-host cancellation and actual assistive
technology behavior are not established by this check. An unconfirmed outcome is
never proof that storage rolled back or that nothing was published. Inspect the
catalog before retrying; recover stale views through explicit Cancel/reload and
fresh review. All 67 program requirement/acceptance pairs and the existing corpus,
retention and AI-access authority limits remain in force.
