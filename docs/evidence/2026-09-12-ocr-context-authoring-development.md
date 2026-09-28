# OCR context-authoring development checkpoint — 2026-09-12

This records the implemented editor, passing focused V7, recovered browser
startup and the generated V12 author/Save pass. The first V12 restore failed at
a native uncheck; a separately admitted restore-only recovery then passed using
the actual saved draft. Requirement 20 and the broader 67-requirement program
retain their original acceptance scope; representative OCR fidelity is not
established by these generated cases.

## Implemented behavior

The reference tab supports source-bound sentence, region and cell contexts,
individual checks for numbers, units, negation, names and footnote identifiers,
and original-page rectangle selection. Native text selections join UTF-16 browser
ranges to raw Unicode offsets, preserving source newline offsets. Exact anchors
and ambiguity diagnostics remain visible. Candidate text supports native readonly
mouse selection and Copy. Numeric mapping controls also accept keyboard entry;
readonly keyboard-selection behavior was not established in the tested browser.
Explicit correspondence review remains separate from reference review.

The controller commits outgoing edits before changed context/check navigation.
Captured edit, display, context and check stamps guard queued actions. Changed
content revokes the relevant reviews and artifacts. Current-value dropdown echoes
return skips without overwriting pending fields; stale echoes still take the
existing refusal path. Result navigation reads bound artifacts without rescoring.
Manual draft save captures pending form content; restored drafts retain authored
content and selection while clearing session reviews, export bindings, result
choices and source-preview authority.

An exact repeat of the current candidate selection now preserves the existing
state, review stamps and rendered controls after source, form, display, native-span
and full-authoring validation. A real selection or pending-form change still
revokes the affected authority. The callback returns component skips for the
validated unchanged state, avoiding a needless render that could replace a
confirmation during correspondence review.

Exports keep a durable private original and expose a separately verified copy in
the launcher's owned Gradio cache. The shared download helper preserves binding
ownership, source checks and existing file boundaries. A late cache-copy failure
clears the browser link while retaining the newly saved artifact and telling the
operator to check the private output folder before exporting again. These are
implementation and focused-test claims; full native workflow coverage is described
separately below. The [admitted source generation](../../tmp/context_authoring_v1/focused-admitted-v7.json)
contains 627 maintained files, including 448 Python files, and 628 pins including
the original Git index. This count precedes these documentation changes.

## Retained focused and native evidence

- The download handoff passed **434 tests in 13 modules**, with 1,302 passing
  setup/call/teardown phases, Ruff passing, and no failures, skips or reported
  warnings. The [independent V4 verification](../../tmp/context_authoring_v1/independent-focused-v4-verification.json)
  joins collection, JUnit, phase reports and the admitted source. It includes the
  installed Gradio launched-file postprocessing guard, not a native browser claim.
- Navigation focused V5 retained **107 passes and one test-fixture failure**;
  Ruff passed. The copied second context reused the first context's mapped span,
  so the unchanged overlap guard correctly rejected the cohort. A one-line
  fixture correction marks that second correspondence ambiguous. The affected
  test then passed with Ruff in fresh V6. Independent [V5](../../tmp/context_authoring_v1/independent-focused-v5-verification.json)
  and [V6](../../tmp/context_authoring_v1/independent-focused-v6-verification.json)
  records establish **108 distinct passing identities across the two runs**;
  they do not relabel the failed V5 run as passing. These counts overlap earlier
  focused generations and are not summed with the 434 download tests.
- The applied duplicate-selection repair passed **122 distinct cases across both
  full selected modules**, with **366 passing setup/call/teardown phases**, no
  failures, errors or skips, and Ruff exit 0. The [independent focused V7 verification](../../tmp/context_authoring_v1/independent-focused-v7-verification.json)
  joins the actual receipt `0ef49db13e66255c891fdeb914b7f97b0e062d4cdc26e5cec4f3022985308957`,
  all six artifacts and all 628 admitted pins. Its 14 new parameterized cases
  preserve reviews, exports and validation on duplicate selections, and retain
  refusal/revocation for stale, malformed, changed-form or changed-source cases.
  These 122 cases overlap earlier focused runs and are not added to their totals.
- The [native navigation smoke](../../tmp/context_authoring_v1/dropdown-navigation-smoke-v1/independent-outcome-v1.json)
  passed 20 checkpoints across 19 actions, including six mouse and two
  ArrowDown/Enter selections. Distinct pending context text and check anchors
  survived navigation; changing context cleared the selected check naturally.
  The screenshot agrees with the final DOM. Twelve artifacts, all 628 source
  before/after bindings, four generated fixtures and nine cleanup checks were
  independently joined. The final console recorded zero errors and two warnings.
  This establishes the observed generated workflow, not race freedom or a native
  dropdown-clear capability that the installed component does not expose.

The [earlier two-context diagnostic](../../tmp/context_authoring_v1/dropdown-input-diagnostic-v1/independent-outcome-v1.json)
reproduced the visible navigation error. Its queue observer captured no requests,
so the exact duplicate backend-request sequence remains unproven. Installed
Dropdown code emits input on selection and blur; the value-change listener and
current-stamp no-op address the observed behavior while preserving keyboard use
and stale-action guards.

The later [correspondence-review diagnostic](../../tmp/context_authoring_v1/correspondence-review-diagnostic-v1/independent-outcome-v1.json)
completed its observation and **reproduced an application failure**. Fourteen
trusted native metadata events and five bounded DOM observations showed a repeated
candidate selection after the confirmation was checked. Twenty backend metadata
records showed an accepted repeat of the already-mapped span `0:20` replacing the
view stamps, followed by a confirmed review with a matching source view but stale
stamp; the review was refused. Metadata collection did not overflow and the
observer was restored. The records do not provide native-event-to-request IDs or
an exact cross-clock causal join. The requested immediate checkbox snapshot also
adds a timing observation between checking and clicking review.

The [applied repair](../../tmp/context_authoring_v1/duplicate-candidate-selection-proposal-v1/application-v1.json)
retains the full validation path while suppressing only the validated unchanged
selection's revocation, stamp replacement and render. Its focused pass is separate
from the diagnostic failure and from the pending full native workflow.

## Preserved failure history

Every failed attempt remains a failed retained attempt. A successful CLI process
exit only means that the driver returned its result; it does not make a failed UI
result pass.

| Attempt | Retained outcome and resulting evidence |
| --- | --- |
| [Full browser V1](../../tmp/context_authoring_v1/full-browser/browser-failure-verification-v1.json) | Stopped in the first context with a selection error; the candidate accessible-name mismatch was also independently observed. The sanitized evidence did not establish the selection error's exact cause. No export or restore. |
| [Full browser V2](../../tmp/context_authoring_v1/full-browser-v2/browser-author/attempt-outcome.json) | Two reference selections passed; the negation mouse selection collapsed instead of selecting the intended occurrence. Subsequent font-matched controls supported a browser-driver border-coordinate correction. |
| [Full browser V3](../../tmp/context_authoring_v1/full-browser-v3/independent-author-verification.json) | All nine reference spans and three source rectangles were authored. Reference publication succeeded, but Gradio rejected the original output path for browser delivery. Mapping, evaluation, draft save and restore were not reached. |
| [Full browser V4](../../tmp/context_authoring_v1/full-browser-v4/independent-author-verification.json) | The corrected handoff produced matching original/cache bytes. The driver looked for filename text inside the download anchor; the actual filename was in a sibling table cell. It stopped before a native download. |
| [Full browser V5](../../tmp/context_authoring_v1/full-browser-v5/independent-author-verification.json) | Native reference download matched the durable original. A context-switch error then stopped the workflow. The cache had already been removed before comparison, so no three-way original/cache/download equality is claimed. |
| [Full browser V6](../../tmp/context_authoring_v1/full-browser-v6/independent-outcome-v1.json) | Native reference download again matched its 3,794-byte durable original. Context navigation and candidate label/readonly checks were reached. Copy replaced the marker with the exact observed Windows CRLF representation, but the driver expected LF and failed. Source and DOM text remain LF; model offsets are unchanged. No mapping/evaluation/save/restore outcome is claimed. The cache was removed at the host deadline, so equality is again two-way. |
| [Full browser V7](../../tmp/context_authoring_v1/full-browser-v7/independent-outcome-v1.json) | Exact bounded CRLF Copy and the first candidate mapping `0:27` passed. ArrowRight did not collapse the readonly selection, so the driver stopped. The reference download, durable original and live cache matched. Later mapping, evaluation, Save and restore were not reached. |
| [Readonly keyboard diagnostic](../../tmp/context_authoring_v1/candidate-keyboard-diagnostic-v1/independent-outcome-v1.json) | Completed 90 trusted observations without overflow. Readonly ArrowRight and Control+End left selection `0:27`; the editable comparison collapsed to `27:27`. This observes a browser-specific readonly difference, not a general browser-engine cause or keyboard capability pass. |
| [Candidate gesture smoke](../../tmp/context_authoring_v1/candidate-gesture-smoke-v1/independent-outcome-v1.json) | Native measured end-click reached `39:39`; Shift+Home left it collapsed and the smoke failed before mapping. Its later manual-controls producer was held and unexecuted after the host deadline. Full V8 was also held and never admitted or run. |
| [Manual-controls smoke](../../tmp/context_authoring_v1/manual-controls-smoke-v1/independent-outcome-v1.json) | Six observations passed: keyboard numeric mapping `28:39`, caret and printable-input preservation, page-switch revocation, and page-2 native mouse/model span `0:20`. The final exact-end assertion failed because the native caret was `0:0`, not `20:20`. Later passive observations separately showed that this collapsed caret preserved the mapping and original readonly/focus/text contract. The failed smoke remains failed; no extra native probe was run. |
| [Full browser V9](../../tmp/context_authoring_v1/full-browser-v9/independent-outcome-v1.json) | All nine reference checks and three source contexts were authored; reference download/original/live-cache equality passed. Three candidate mouse/numeric mappings and the original collapsed-caret contract passed, as did the first two correspondence reviews. The third review failed visibly. Correspondence export, evaluation, Save and restore were not reached. The later bounded diagnostic above established the stale-review sequence without relabeling V9. |

The first focused run also retained a Ruff F811 failure; its alias correction was
checked in a fresh passing run. The [earlier progress record](../../tmp/context_authoring_v1/progress-v2.json)
retains that distinction. The font-selection controls and resulting scope limits
are recorded in [progress V5](../../tmp/context_authoring_v1/progress-v5.json).

## Efficiency after the patches

The implementation reuses the existing evaluator, controller transition, private
writer and export bindings. The shared handoff copies already-saved bytes into
the owned download cache and avoids a further Gradio copy for that admitted cache
path. It retains streamed file rechecks; verification adds explicit-export I/O.
Dropdown handling adds seven production lines and avoids source hashing, state
copies and rendering for exact-current display echoes. Real changes and stale
submissions keep their original validation path.
The one-line fixture repair reran only its affected case and retained the other
107 passing identities. The new candidate-selection repair adds **nine production
lines and 446 bytes**; its regressions add 145 test lines. It avoids redundant
review revocation, stamp generation and render/update allocation for an exact
validated repeat. Source verification, state copying, form/display/span checks and
full-authoring validation still run. Native driver corrections use measured mouse
geometry, numeric keyboard controls and the observed Windows clipboard format;
they do not add a readonly keyboard-selection capability. No whole-pipeline
speedup or request-count reduction has been measured.

The user removed the inventory cap; per-patch efficiency and capability reviews
continue. Original Git index/history, the canonical inventory baseline and all
67 requirement/acceptance pairs remain preserved at the admitted checkpoint.

## Retained V10 and browser startup failures

The [independently verified V10 record](../../tmp/context_authoring_v1/full-browser-v10/independent-outcome-verification-v1.json)
retains both failed drivers and their completed evidence. Authoring nine checks
across three source contexts and reviewing all three candidate mappings completed. The original
author driver then read export status after a fixed 1.2-second wait and failed
before the completed correspondence-export notice appeared. A later passive
observation and the durable artifact established that export had completed. The
[detailed outcome](../../tmp/context_authoring_v1/full-browser-v10/attempt-outcome-v1.json)
also preserves two earlier preflight-only refusals.

A separately reviewed continuation used the same live session without replaying
its completed authoring or export actions. It downloaded correspondence and the
evaluation report. The actual generated evaluation covered **nine checks: five
passed, four failed and none abstained**. Native reference, correspondence and
evaluation downloads matched their durable originals and live cache copies;
independent verification later reread the downloads and originals against the
retained three-way comparison after the cache had been removed. These are three
completed exports, with no saved draft or fourth download.

The continuation failed when its fixed result-dropdown wait returned the previous
evaluation notice to a JSON parser. A later snapshot showed the correct failed
context/check, source region and candidate span `30:32`; this later observation
does not turn the continuation into a pass. Save and restore were not reached.
The host stopped at its deadline with all nine cleanup checks true, 628 source
pins and all four fixtures unchanged. The stop file was not accepted as the cause
of shutdown; the later close check reported that the browser was not open.

[Full V11](../../tmp/context_authoring_v1/full-browser-v11/independent-bootstrap-failure-v1.json)
failed during the initial 60-second CLI `open about:blank` call, before setup or
any application journey. No native download, draft or restore occurred. The host
closed by explicit stop with all nine cleanup checks true and source/fixtures
unchanged. Its [static startup diagnosis](../../tmp/context_authoring_v1/full-browser-v11/bootstrap-static-diagnosis-v1.json)
found a 60-second outer timeout around Playwright's default 180-second Chrome
launch timeout, an empty daemon stderr log and no registered session. Those facts
do not establish the underlying cause of V11's unlogged timeout.

The separate [about:blank bootstrap probe](../../tmp/context_authoring_v1/browser-bootstrap-diagnostic-v1/independent-outcome-v1.json)
used a 30-second Chrome launch timeout and browser debug logging. It still timed
out at the 60-second outer bound, without a snapshot or application journey.
Its retained daemon log shows Chrome failing to register a message-only window
class and reporting that Windows had run out of resources to start Chrome. Two
cleanup `taskkill` attempts also timed out. A later task-specific process check
found the exact probe daemon and logged Chrome process absent. The separate
[runtime clarification](../../tmp/context_authoring_v1/browser-bootstrap-diagnostic-v1/independent-runtime-join-v2.json)
records that no user or identified browser/daemon process was explicitly
terminated; subprocess timeouts stopped CLI/diagnostic children and one read-only
query was cancelled. Process absence is distinct from the CLI's browser-not-open
close response. The probe establishes no narrower resource diagnosis and does
not retroactively prove V11's cause.

## Browser recovery and V12 author/Save

After the [observed reboot](../../tmp/context_authoring_v1/progress-v19.json), a
fresh bounded `about:blank` probe's open, snapshot and close commands all
succeeded. The [independent recovery verification](../../tmp/context_authoring_v1/browser-bootstrap-recovery-v1/independent-verification-v1.json)
joined those command receipts, all 628 source/index pins and four fixtures
without drift. This establishes successful startup at that checkpoint; it does
not identify the earlier exhausted Windows resource or retrospectively explain
V11's timeout. The earlier failed probe remains retained.

The [independently verified V12 author run](../../tmp/context_authoring_v1/full-browser-v12/independent-author-outcome-v1.json)
**passed through Save**. It authored three contexts and nine checks, reviewed
the candidate mappings, exported reference and correspondence inputs, evaluated
them and navigated to the failed check's source. The generated evaluation had
the expected **nine checks: five passed, four failed and none abstained**.
These deliberate generated outcomes test the workflow, not representative OCR
or semantic accuracy.

All four actual browser downloads—reference, correspondence, evaluation and
draft—matched their durable originals and live cache copies. The
[retained comparison](../../tmp/context_authoring_v1/full-browser-v12/native-artifact-comparison-v1.json)
records the joins made before cache cleanup; independent review later checked
the downloads and originals against that comparison. The saved draft retained
the pending anchor and unconfirmed page text, selected context/check and mapped
span. Its closed schema retained no review or evaluation authority. The author
host stopped explicitly, with all nine cleanup checks true and source/fixtures
unchanged. Focused V7 results were reused because the application source had
not changed.

## V12 restore failure and verified recovery

The separate [V12 restore admission](../../tmp/context_authoring_v1/full-browser-v12/restore-admission.json)
bound the actual newly saved draft. Its [retained driver result](../../tmp/context_authoring_v1/full-browser-v12/browser-restore/restore-result.txt)
passed the pending-field, context/check selection and reference/candidate-span
assertions. All six confirmations were initially unchecked; evaluation coverage
and result selection were empty, and context-source review authority had not
been restored. An attempted reference export without fresh review displayed
`Context action failed.`, and the driver confirmed that no reference download
link appeared.

**The V12 restore attempt failed.** Its following native uncheck operation
timed out because the component's status overlay intercepted pointer events.
The [final snapshot](../../tmp/context_authoring_v1/full-browser-v12/browser-restore/final-snapshot.txt)
retains the component Error overlays. The result stayed `passed: false` at
`restart-export-refusal`; reaching the earlier assertions does not convert the
attempt into a completed restore pass or establish the final unchecked state.
The [independent failure verification](../../tmp/context_authoring_v1/full-browser-v12/independent-restore-failure-v1.json)
confirmed that the owned output folder was empty, the actual saved draft and
source/fixtures were unchanged, and all nine host cleanup checks passed after
explicit stop.

A fresh, separately admitted [restore-only recovery passed](../../tmp/context_authoring_v1/restore-recovery-v1/independent-outcome-v1.json)
using that same actual V12 draft. Its [completed driver result](../../tmp/context_authoring_v1/restore-recovery-v1/browser-restore/restore-result.txt)
retained the pending fields, context/check selection and reference/candidate
spans, with all six confirmations initially unchecked and no restored
evaluation, result or context-source review authority. Reference export without
fresh review was refused and produced no output. The driver then used the
affected component's native **Clear** control and completed the original native
uncheck; both actions were explicitly observed before `restore-complete`.

Independent verification joined the command receipts, unchanged source and four
fixtures, unchanged original saved draft, empty owned output and all nine host
cleanup checks after explicit stop. The source and focused V7 evidence remained
unchanged; no authoring or OCR work was replayed. This completes the bounded
generated author/Save/fresh-restore workflow. The failed V12 restore remains a
separate failed attempt. At that recovery checkpoint, final documentation and
source admission still preceded fresh copied-source qualification; the subsequent
qualification is recorded below. Broader OCR/AI outcomes remain pending.

## Qualification scope

The completed chunk-deduplication [copied-source qualification](../source-cleanup-audit.md#copied-source-qualification)
covered an earlier 621-file snapshot. It does not qualify this context-authoring
source generation. Before the qualification recorded below, the exact final
source had not passed a fresh copied-source nine-gate qualification. The
[retained qualification plan](../../tmp/context_authoring_v1/next-qualification-plan.md)
kept that work separate from focused tests and native generated-source evidence.

These runs use retained generated fixtures, without new OCR/model execution,
private-corpus evaluation, model acquisition, release or expanded AI authority.
Representative references and reviewer/source-retention policy remain separate
prerequisites for semantic-fidelity claims.

## 2026-09-13 copied-source qualification

The subsequent frozen context-authoring copy **passed all nine local gates**:
**14,592 tests passed, seven skipped, six warnings, and no failures or errors**.
The [independent retained-artifact verification](../../tmp/ocr_context_authoring_uncapped_qualification_v1/independent-verifier-outcome-v1.json)
matched all **14,599 collected test identities** to JUnit, checked all **28
artifacts**, and passed all **40 retrieval predicates** (12 constitutional-law,
12 property and 16 table-family). The required real Zettlr case passed. The
verifier completed once with exit 0 and empty stderr, without replaying any gate.

The [source freeze](../../tmp/ocr_context_authoring_uncapped_qualification_v1/source-freeze.json)
contains **628 files: 448 Python files, 520 originally tracked files and 108
admissions**. Both separately generated inventories are byte-identical at
**1,990,368 bytes**. Independent semantic review records **191 modules and 618
import edges, acyclic**; the edge total includes 609 static and nine retained
dynamic import edges. The reviewed inventory was installed only in the copied
worktree. Original baseline, index and history remained unchanged, as did both
repositories' required source and Git-object bindings through verification.

Retained external artifacts below are relative to
`%LOCALAPPDATA%/rag-pipeline/ocr-context-authoring-uncapped-qualification-v1/evidence`.
The source freeze and independent review use the repository links above.

| Record | SHA-256 |
| --- | --- |
| Source freeze | `7c340e479fa048a3242455e3802e6607038f18d0b555d6c333338aff2b653bc1` |
| `architecture-candidate-v1.json` and `architecture-candidate-v2.json` | `d5b3b47b9c44d2b9799906675a4abf05b056ae0fdc66b19fcca759bad391e81c` |
| `finalization.json` | `6ac5a1dd9465eb427466d29f78c87b5e4a04bbde95c0cb117d94cd34de759fe3` |
| `provenance.json` | `73a1d0465111bd2839d93e51d84eb9bedc71defc2a5d77edd89690e761947a05` |
| `gates-v1/receipt.json` | `4969e0baee8d75f0fecf96176e72be0042f3de839e0a9b136739ac326311027c` |
| `gates-v1/verification-addendum-v1.json` | `9b37522fb30808844dd5712785fdfff8a45a45d9a9b184760b4b1b3d73deb943` |
| Independent retained-artifact verification | `42dd3535847c9ad66f00a7d72a0c13ff1826396c8e89dd106c2c45c2a53169b4` |

The retained verifier recorded four nongating Windows handle-ctime observations,
with repeated checks on two paths: the original `.git/index` and `docs/source-cleanup-audit.md`. Required
path/handle identity and double-read content checks passed under the admitted
policy. These observations are not content drift or a waiver, and do not provide
continuous metadata or loaded-byte attestation.

This qualifies the copied source; it does not assert that the original checkout's
default tracked-source gates or hosted CI passed. These closing documentation
changes follow the qualified snapshot and preserve all 67 requirement/acceptance
pairs. The failed V12 restore above remains failed, alongside its separately
verified recovery. Generated workflow evidence and local qualification do not
establish representative OCR accuracy, semantic fidelity or completion of the
broader OCR/AI program.
