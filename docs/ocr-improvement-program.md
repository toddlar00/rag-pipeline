# OCR improvement program

User-requested local development scope (2026-09-06): implement the eight OCR
improvements below and permit AI to harness the pipeline. This checklist tracks
the whole request; delivery of one component does not complete it. It does not
authorize release, remote hosting, private-source disclosure, or an architecture
ownership move otherwise held by `ROADMAP.md`.

Current size policy: the inventory cap was removed at the user's request after
the earlier efficiency checkpoint. Per-patch efficiency and capability reviews continue,
along with canonical baseline and architectural drift checks. The completed
qualification artifacts retain their original source and cap records.

Previous completed copied-source qualification: [chunk-deduplication observation](source-cleanup-audit.md#copied-source-qualification)
passed all nine gates: **14,377 tests passed, seven skipped and six warnings**.
Independent verification matched 14,384 collected identities, 621 source files,
28 artifacts and 40 retrieval predicates. The reviewed inventory is
**1,972,968 bytes** under the uncapped policy. Original baseline/index/history
remain unchanged. This qualifies that earlier frozen local implementation; remaining
cleanup stages, merges and representative semantic evidence are still pending.
These documentation updates follow the qualified source checkpoint and preserve
all 67 requirement/acceptance pairs.

Previous completed copied-source qualification: [context authoring](evidence/2026-09-12-ocr-context-authoring-development.md#2026-09-13-copied-source-qualification)
passed all nine gates: **14,592 tests passed, seven skipped and six warnings**,
with no failures or errors. Independent verification matched all 14,599 collected
identities, 628 source files, 28 artifacts and 40 retrieval predicates. The
**1,990,368-byte** inventory covers 191 modules and 618 import edges, with no
cycles. This qualifies the frozen local context-authoring implementation after
the generated author/Save/restore workflow. Original baseline/index/history and
all 67 requirement/acceptance pairs remain preserved; representative semantic
fidelity and the broader program remain pending.

Latest completed copied-source qualification: [hard-scan checkpoints](evidence/2026-09-14-ocr-hardscan-checkpoint-qualification.md)
passed all nine gates: **14,658 tests passed, seven skipped and six warnings**,
with no failures or errors. Independent retained verification matched all 14,665
collected identities, 28 artifacts and 40 retrieval predicates; the real Zettlr
validation case passed. The frozen cohort has 637 source files, 455 Python files,
and a **2,010,977-byte** inventory covering 195 modules and 646 import edges, acyclic.
The failed first run and the separately measured timeout repair remain in the
[development history](evidence/2026-09-13-ocr-hardscan-checkpoint-development.md).
Closing documentation follows the frozen source; original baseline/index/history,
all 67 requirement/acceptance pairs and the other 66 State rows remain preserved.
Annotation proposals and representative OCR fidelity remain outside this result;
the broader program is unfinished.

Previous completed copied-source qualification: [region checkpoints](evidence/2026-09-13-ocr-region-checkpoint-qualification.md)
passed all nine gates: **14,626 tests passed, seven skipped and six warnings**,
with no failures or errors. Independent verification matched all 14,633 collected
identities, 28 retained artifacts and 40 retrieval predicates; the real Zettlr
validation case passed.
The frozen cohort has 632 source files, 452 Python files, and a 2,001,767-byte
inventory covering 193 modules and 634 import edges, acyclic. This phase follows
337 distinct focused cases and generated native interruption/resume/reopen.
The closing documentation follows that snapshot; all 67 requirement/acceptance
pairs and earlier qualification evidence remain preserved. Representative OCR
fidelity, hard-scan checkpoints and the broader program remain pending.

Previous copied-source qualification: [efficiency within the existing cap](evidence/2026-09-10-efficiency-development.md#2026-09-11-follow-up)
passed all nine gates: **14,347 tests passed, seven skipped and six warnings**.
Independent verification matched 14,354 collected identities, 619 files and
40 retrieval predicates. At that checkpoint, the inventory was **1,967,654 bytes**,
**111 bytes** below the then-current 1,967,765-byte cap. Five code patches reduce
net maintained Python by 95 lines and non-test definitions by ten. Generated next-unresolved
navigation now includes exact selected text and reference retention. These
postqualification document updates preserve all 67 pairs; broader OCR outcomes
remain pending.

Earlier reporting-speed development: [shared percentile sorts and candidate
evidence reuse](../tmp/speed_improvements_v1/completion.md) passed 445 focused
tests and Ruff. Generated local measurements show about 1.5x faster large
token-summary calculations and 2x faster large LLM reports; packet preparation
shows a modest repeated-candidate gain and no clear single-appearance benefit.
Independent validation, custom integer comparison behavior and evidence
ownership remain covered. These changes are included in the [later qualification](evidence/2026-09-10-efficiency-development.md#2026-09-11-follow-up);
they do not establish whole-pipeline performance
or representative OCR accuracy. All 67 requirement/acceptance pairs remain intact.

Earlier efficiency development: [bounded Git verification and archive-test seed
reuse](evidence/2026-09-10-efficiency-development.md) passed 591 focused tests,
Ruff and 20 inert snapshot checks. Two measured batches replace 615 Git launches
for the same retained source cohort, with an explicit allocation-peak tradeoff.
Application implementations are unchanged. A smaller UI fixture was rejected
after measurement; additional validator/backend/CLI extraction was deferred
because preserving its distinctions left little net saving. This adds a small
maintained tooling/test component; it does not shrink the codebase or constitute
a new full-suite qualification. All requirement/acceptance/State rows remain
unchanged by this efficiency pass.

Previous passing full qualification: [codebase consolidation](evidence/2026-09-10-codebase-consolidation-qualification.md)
passed all nine local copied-source gates: **14,275 tests passed, seven skipped,
six warnings and no failures/errors**. Independent verification matched all
14,282 collected identities, 615 source files, 28 artifacts and 40 retrieval
predicates; the real Zettlr case and formerly failing combined-preview case
passed. The earlier failed attempt remains unexplained and unwaived.

Shared JSON preflight and bounded file snapshots preserve the reviewed
comparison/journal and omission/checkpoint/cleanup behavior. Independent review
and 1,389 focused tests passed with one Windows skip; lint passed. Production
decreases 81 lines and permanent regressions add 41. Including the approved
guard-comment adjustment, the net reduction is **40 maintained Python lines
and 942 bytes**, with no added production modules or first-party import edges.
At that checkpoint, the user-approved inventory cap was **1,967,765 bytes**,
exactly 27 above the prior cap with no extra headroom. Original baseline/index/history remain unchanged.
These documentation updates follow the frozen checkpoint. The [further
efficiency review](../tmp/codebase_efficiency_followup_2026-09-10.md) records
proposals and unmeasured runtime opportunities; the broader OCR/AI outcomes
remain pending.

Earlier development: the [source-aware cleanup observer](evidence/2026-09-09-source-cleanup-observer-development.md)
passed independent source/test review, lint and 495 focused checks, including
106 new observer checks. All 24 prechange generated cases matched on default
and observed paths. Fresh architecture review passed under the user-approved
**1,967,738-byte cap**, with zero additional headroom. The [full copied-source
qualification attempt](evidence/2026-09-09-source-cleanup-qualification-failure.md)
**failed**: 14,259 tests passed, one combined-mode preview launcher test failed,
and seven skipped; all eight other gates passed. Independent retained-failure
verification matched all 14,267 collected identities, 614 source files, 28
artifacts and 40 retrieval predicates. A separate single-test diagnostic passed
with normal shutdown; the original failure remains unexplained and unwaived.

Previous passing full qualification: [Crop-advice copied-source qualification](evidence/2026-09-09-ocr-crop-advice-qualification.md)
**passed**: 14,154 tests, seven skips, six warnings and all nine local gates.
Independent retained verification matched all 14,161 collected identities,
607 source files, 28 artifacts and 40 retrieval predicates; the real Zettlr
case passed. The user approved the exact inventory cap increase from 1,952,138
to **1,957,857 bytes**, with zero additional headroom. Only the guard's numeric
cap/comments changed in the original source before qualification; the copy
received that guard and the reviewed baseline. Original baseline/index/history
and both Git indexes remain unchanged. These documentation updates follow the
frozen checkpoint and are not retroactively part of its source snapshot.

The earlier [Save-feedback/spot-audit qualification](evidence/2026-09-08-ocr-review-qualification.md)
and [failed v1 attempt](evidence/2026-09-08-ocr-review-qualification-failure.md)
remain immutable, including their separate 77-case architecture repair and
13,980-test passing checkpoint. Earlier [native-repair qualification](evidence/2026-09-08-ocr-uncertainty-repair-qualification.md),
[failed repair attempt](evidence/2026-09-08-ocr-uncertainty-repair-qualification-failure.md)
and [guarded CLI repair](evidence/2026-09-08-phase-a0-guard-repair-development.md)
retain their historical evidence. The [Save-feedback](evidence/2026-09-08-ocr-save-feedback-development.md)
and [spot-audit development](evidence/2026-09-08-ocr-spot-audit-development.md)
records retain their focused/browser results and cache-cleanup limitation.

Crop-advice [focused development](evidence/2026-09-09-ocr-crop-advice-development.md)
records 779 overlapping checks for bounded rendered-pixel observations and a
non-executing wider-region proposal. [Native/partial browser evidence](evidence/2026-09-09-ocr-crop-advice-native-browser.md)
records 29 generated previews and one expected refusal. The [browser follow-up](evidence/2026-09-09-ocr-crop-advice-browser-followup.md)
retains the selector repair, 289 passing cases, draft-preserving Fit/288/576/
Cancel/Fit journey, original truncated collection log and subsequent exact
collection/JUnit audit. Earlier failed/partial attempts remain unchanged.
The later [v7 navigation record](../tmp/ocr_crop_advice_navigation_v7.md) closes
the specific generated 576-DPI horizontal right-edge gap: in the 800x900
viewport, ArrowRight moved 32 pixels, End reached 540, Home returned to zero
and Tab left the preview. Five captured source/review/draft fields stayed
byte-identical; separate retained browser and host audits passed.

Fresh inventory review and full copied-source qualification are complete for
the preceding crop-advice generation. The separate [generated crop-edge paired experiment](evidence/2026-09-09-ocr-crop-edge-paired-experiment.md)
completed ten actual OCR calls, seven admitted scope evaluations and five
fixed-target comparisons; three partial original scopes remain unscorable.
Padding recovered both single-text cases, while both duplicate controls still
returned neighboring text. The nontext control stayed empty. These generated
measurements retain the difference between wider-scope transcription accuracy
and the fixed task target; they do not authorize crop adoption.
Broader navigation/accessibility, region/token localization error rates,
representative warning usefulness and all other outstanding OCR/AI outcomes
remain pending. This is not an original
default tracked-source pass, representative accuracy gain, speedup, human
adjudication or new corpus/AI authority. All 67 requirement/acceptance pairs
remain intact; the complete program is unfinished.

## Required outcomes and evidence

| Requirement | Acceptance evidence | State |
| --- | --- | --- |
| Visual review and annotation | Bound scan, OCR boxes, line order and text differences; visual region selection; validated plans and reviewed references without manual hashes | Independently reviewed local implementation; generated browser flows cover failed-page crops, draft restart, explicit line assignment, region reorder and review export; local frozen-source qualification passed |
| Representative held-out accuracy | Approved examples across books, fonts, scan conditions and layouts; document-family-separated development/evaluation cohorts; frozen references and full coverage | In progress; owner-approved representative inputs outstanding |
| Region OCR | Bounded crop retries, explicit source/report binding, page-coordinate mapping and retained context; failures cannot replace canonical text | Independently reviewed; synthetic browser-plan to real RapidOCR crop verified |
| Layout-assisted proposals | Existing Docling layout/table information yields source-bound region/order suggestions, including spanning headings; human confirmation required | Policy/CLI independently reviewed; real locked Docling conversion yields regions but conservatively abstains on order; independently reviewed assignment/region-order controls verified in generated browser workflow |
| Structural metrics | Reading-order, table-cell assignment and missing-region errors reported separately from CER/WER; missing observations cannot pass | Strict APIs/CLI and synthetic tests; integration with reviewed real correspondences pending |
| Independent OCR cross-check | Second byte-pinned local engine on identical references; disagreement queue without automatic majority correction | Pending |
| Hard-scan recovery | Optional orientation, curved-page correction and uneven-illumination recovery; each separately measured, bounded and provenance-preserving | Independently reviewed opt-in workflow; generated API/CLI matching and orientation gain measured; lighting/bow preserve synthetic accuracy but show no gain; representative evidence pending |
| Reproducibility | Effective runtime/model/configuration manifests for experiments and an actual committed-lock runtime validation | Fresh strict hash-lock Python 3.12 installation, actual 8-page execution receipts and local frozen-source qualification passed; Windows venv worker identity repaired and independently reviewed; not native-binary attestation |
| AI access | Reader-only machine interface over authenticated local service; bounded cited results, static failures, no token disclosure/admin capability or implicit cloud egress | Reader client and synthetic live-service/CLI tests; approved real-corpus access decision pending |

## Additional requested improvements

The continued implementation goal also includes the subsequent six suggestions
and adaptive retry suggestions from the latest assessment. They extend the
original scope; they do not replace its pending outcomes.

| Requirement | Acceptance evidence | State |
| --- | --- | --- |
| Coverage-aware review | Failed, empty, deferred and unselected pages remain reviewable; explainable prioritization without fabricated success or confidence-as-accuracy | Implemented with original-page previews and focused tests; calibrated risk ranking still requires approved error labels |
| Resumable review | Exact-source draft snapshots, unfinished versus reviewed text separation, decision history and reset confirmations | Browser verified failed-page crop/transcription and omission-assessment restart; v3 adds partial omission assessments while retaining strict v1/v2 reading; encoded save/reload byte bounds independently verified, including full oversized-draft rejection |
| OCR progress checkpoints | Completed page work survives interruption in a distinct incomplete format; bounded resume validates every input/recipe/model generation | Page adapter independently reviewed; 497 focused/compatibility tests pass; real contained generated-fixture interruption/resume independently verified: page 1 preserved, page 2 explicitly interrupted without another attempt, pages 3–8 resumed. [Region checkpoints](ocr-checkpoints.md#generated-region-checkpoint-evidence-2026-09-13) are implemented; 337 distinct focused cases and lint passed. Generated native CLI interruption/resume/reopen returned 130/3/3, preserved region 1, sealed region 2 interrupted and resumed region 3; independent retained native verification passed. [Region copied-source qualification](evidence/2026-09-13-ocr-region-checkpoint-qualification.md): all nine gates passed (14,626 passed, seven skipped, six warnings); independent verification accepted all 14,633 identities, 28 artifacts and 40 retrieval predicates. [Hard-scan checkpoint development](evidence/2026-09-13-ocr-hardscan-checkpoint-development.md) extends the consumed-attempt journal to approved recipe plans, retained transforms, origin receipts and a vertex budget preserved across resume. 587 distinct focused cases and lint passed, independently verified; generated native cancellation/resume/reopen returned 130/3/3, preserved crop 1, sealed crop 2 interrupted without another attempt and resumed crop 3. Independent native verification accepted the origin receipts and unchanged completed journal. The first [hard-scan copied-source qualification](evidence/2026-09-14-ocr-hardscan-checkpoint-qualification.md) failed; its timeout repair has two expected prechange failures, two postchange passes and 606 focused passes. Fresh V2 qualification passed all nine gates: 14,658 passed, seven skipped, six warnings and no failures/errors. Independent retained verification accepted all 14,665 identities, 637 source files, 28 artifacts and 40 retrieval predicates. The earlier generated native scope remains separate; representative OCR fidelity and the broader program remain pending |
| Meaning-sensitive checks | Numbers, units, negations and footnote references bound to reviewed sentence/region/cell occurrences; swapped-value negative controls | Independent policy/IO/CLI review approved; generated actual CLI catches 3/3 deliberately swapped/moved occurrences despite unchanged token bags; reference/correspondence authoring remains explicit |
| AI uncertainty and scan evidence | Reader results retain explicit OCR review/unknown state and restricted source-generation/physical-page/region evidence without arbitrary file access | Opt-in text-evidence worker/HTTP/client passed generated native integration and Phase 5 local frozen-source qualification; [usage](ai-evidence-access.md) distinguishes exact record scope from unverified OCR and related-page candidates; scan serving and occurrence-level adoption remain pending; ordinary v1 remains unchanged |
| Paired downstream evaluation | Isolated OCR-variant corpora with fixed questions and retrieval settings; text errors, judged retrieval and citation-supported answer outcomes reported separately | Pending; approved reference/judgment policy required for representative claims |
| Adaptive retry suggestions | Diagnose region conditions, propose suitable resolution/segmentation/preprocessing within a fixed work budget; calibrated abstention and explicit review | Pending; current hard-scan recipes remain operator supplied; do not silently choose higher-confidence OCR as truth |

## Further assessment adopted by the continued implementation goal

These additions came from inspection of the current implementation, not from
an assumption that the pending outcomes above are complete.

| Requirement | Acceptance evidence | State |
| --- | --- | --- |
| Silent-omission detection | Highlight expected text regions with no observed OCR lines; distinguish unavailable geometry, non-text regions and ambiguous coverage; measure false alarms and omissions separately | Saved-region API/IO/CLI and partial editor assessments independently reviewed; 155 focused omission tests and 134 editor/draft tests pass; generated browser draft/restart/export/reset verified. Independent pixel hypotheses have the separate Phase 7 checkpoint below; representative false-alarm/omission rates and full-source coverage remain unestablished |
| Cleanup fidelity audit | Before/after evidence for normalization, furniture removal and deduplication; adversarial meaningful repetitions, punctuation and identifiers; separate cleanup-induced edits from OCR errors | Default core-normalization replay retains its 331-check/542-case checkpoint. [Source-aware wrapper observations](evidence/2026-09-09-source-cleanup-observer-development.md) passed independent source/test review and 495 focused checks, including 106 new checks and exact default/observed agreement with 24 prechange generated cases. Committed, discarded and auxiliary passes retain reversible edits and explicit bounded abstention. The [earlier full qualification failed](evidence/2026-09-09-source-cleanup-qualification-failure.md) at one preview launcher test; its cause remains unexplained and unwaived. The subsequent [consolidation generation passed](evidence/2026-09-10-codebase-consolidation-qualification.md) 14,275 tests and all nine gates, including that case, with independent retained verification. Local [chunk-deduplication observations](source-cleanup-audit.md#separate-chunk-deduplication-observation) now bind consumed text occurrences to actual kept/removed dispositions. Across two frozen generations, 496 distinct focused checks passed (30 new observer cases), with lint and independent retained verification; the first wrapper receipt preserves its resolved IPv6 test-name mapping failure. The subsequent [uncapped copied-source qualification](source-cleanup-audit.md#copied-source-qualification) passed all nine gates with 14,377 passes and independent verification of 14,384 identities, 621 source files, 28 artifacts and 40 retrieval predicates. Other later cleanup stages, merges and representative semantic evidence remain pending |
| Book-specific terminology warnings | Approved recurring names, citations and glyph-confusion patterns produce source-linked review warnings; held-out error recall and false alarms measured; no silent corrections | Pending; approved vocabulary and reviewed calibration examples required for representative claims |
| Visual context-check authoring | Select critical text and scan regions in the editor; generate exact spans/bindings, show anchor ambiguity and jump back from failed checks; explicit review remains mandatory | Implemented; generated-source authoring, four native artifact downloads, evaluation/result navigation and draft restart are recorded in the [development checkpoint](evidence/2026-09-12-ocr-context-authoring-development.md). [Fresh copied-source qualification](evidence/2026-09-12-ocr-context-authoring-development.md#2026-09-13-copied-source-qualification) passed all nine gates with independent verification; representative semantic fidelity remains pending |
| Independent reference adjudication | Independently transcribe critical passages, retain disagreement and adjudication evidence, and freeze references before evaluation | Pending; reviewer/source-retention decisions required |
| Mixed-content region routing | Preserve valid native text while detecting and testing image-only text regions; no duplicate, omitted or silently replaced text; source-coordinate and generation bindings retained | Pending; current page-level suspicion checks do not prove image-region coverage |
| Independent scan-based omission detection | Inspect bounded source pixels for text-like areas absent from both saved layout and OCR; false alarms and unresolved image/non-text regions remain explicit | [Implemented locally](ocr-scan-inspection.md); independent native/historical-reader/browser review and [Phase 7 qualification](evidence/2026-09-07-ocr-scan-review-qualification.md) passed: 8,652 tests, seven skips, six warnings and all nine selected gates. The generated source-only scan retained 503 hypotheses, all unevaluated against OCR/layout; representative recall/false alarms and complete-source claims remain unestablished |
| OCR-tolerant retrieval | Optional bounded secondary lexical candidates for damaged names/words, exact-match preference and unchanged canonical text; paired recall and false-positive evaluation | Pending; existing legal-token BM25/hybrid retrieval remains unchanged |
| Stage-specific OCR diagnosis | Reviewed line/cell regions distinguish detection omissions, merge/split and order errors from recognition; gold-crop comparisons declare whether detection was truly bypassed | Independently reviewed policy/runtime/IO/CLI; 235 stage-only tests; real contained generated run and fresh strict readback verified 54 calls, 48/48 gold crops/paired lines and 18 cell containers; exposes 15 column-order inversions despite exact per-line text; Phase 4 full qualification passed; representative reviewed correspondences remain pending |
| Source-bound inferred column suggestions | Geometry-only hypotheses bind the exact source/recovery and requested cohort, preserve every observed line, retain abstentions and unavailable pages, and require explicit prose classification plus source review before preview | [Implemented locally](ocr-column-suggestions.md); independent core/IO/CLI, UI and retained-browser review passed; [Phase 6 local qualification](evidence/2026-09-07-ocr-column-review-qualification.md) passed all nine gates with 8,230 tests passing, seven skipped and six warnings; no automatic correction or representative accuracy claim |
| Faster review navigation | Keyboard navigation, next unresolved issue and linked scan/text highlighting; calibrated priorities require held-out error labels | [Locally qualified generated navigation](evidence/2026-09-10-efficiency-development.md#2026-09-11-follow-up): next unresolved selection, original line identity, exact selected text, linked highlights and reference retention passed focused/browser checks and full copied-source qualification. Calibrated priorities, representative review-effort evidence and broader keyboard/accessibility coverage remain pending |

## Latest suggested extensions

The resumed implementation goal also retains these suggestions. Existing rows
already cover scan-backed AI answers, book-wide terminology/critical-value
warnings and adaptive retries; the extensions below do not replace their
acceptance evidence or silently authorize new model acquisition or corpus use.

| Requirement | Acceptance evidence | State |
| --- | --- | --- |
| Small-text rescue | Detect tiny footnotes/captions, select bounded higher-resolution regions, preserve coordinates and measure paired errors including omissions | Pending extension of region OCR and adaptive retry policy; fixed-resolution crop workflows already exist |
| Language-aware recognition | Approved region-level language/script model selection, mixed-language/manual override, byte-pinned assets and held-out script-specific measurements | Pending; no additional model downloads or unsupported automatic language claims |
| Cross-page reconstruction | Paragraph, continued-table and footnote links retain exact source occurrences; ambiguous links abstain and original text/page references remain available | Pending OCR-specific extension; existing chunk/source provenance is not proof of correct cross-page reconstruction |
| Reviewed correction feedback | Explicitly approved corrections form error clusters and regression cases without contaminating frozen document-family-held-out evaluation | Pending; resumable drafts and reviewed references alone are not an implemented learning loop |
| Quality-versus-cost controls | Bounded fast/balanced/thorough profiles expose actual calls, time, raster work and available memory observations alongside measured paired quality | Pending; current bounds, manual recipes and receipts do not establish an adaptive quality/cost tradeoff |
| Unified guided review | A source-bound diagnose/retry/compare/scan/approval workflow with clear state, failure recovery and no hidden canonical adoption | [Local development workflow](ocr-review.md#opt-in-ocr-execution-development-preview): opt-in three-route coordinator/panel plus same-crop source-preview/reference/scoring controls. The value-change generation passed 595 focused checks, including 21 full-app cases with explicitly inert rendering/inference/supervision/model observations. Generated browser v1's label failure and v2's typing/input-only edit defects remain retained; v3 observed two fresh actual calls, source-authored scoring, zero typing errors, and fill/delete/critical-clear/reload safeguards after independent source/test review. [Retained checks and limits](ocr-crop-comparison.md#retained-development-checks-2026-09-07) distinguish actual UI observations, native readback and earlier standalone preview attempts; the original standalone v1 refusal remains unexplained. The separate [Phase 11 checkpoint](evidence/2026-09-07-ocr-guided-review-qualification.md) passed 10,508 tests and all nine local gates with reviewed architecture and exact collection/source verification. Broader platform/clipboard/IME coverage, linked controls, export/restart, representative evidence and the wider approval workflow remain pending; Phase 10 does not qualify this later integration |
| Explicit correction publication and rollback | Preview exact affected text occurrences/chunks/retrieval, require scoped approval against old/new generations, retain original and rebuild/invalidate affected index state with rollback | Pending; review reports and text-evidence search deliberately do not adopt corrections |
| Typography-sensitive fidelity | Reviewed spatial/context checks for minus signs, decimal points, superscripts, footnote markers and value/unit relationships; location-sensitive negative controls | Pending extension beyond existing occurrence-bound critical-text checks; not established by low aggregate CER/WER |
| Statistical promotion gates | Paired document-family uncertainty intervals, worst-category floors and critical-error vetoes on frozen approved held-out data | Pending extension of current deterministic per-record regression checks; synthetic improvements do not qualify release |
| Index-derived metadata correspondence | Bind raw source records to the exact indexing transformation, including recomputed embedding token counts, without ignoring metadata or inventing identity | Pending extension; strict companion currently refuses differing raw/indexed metadata, including legacy absent counts; ordinary search remains available |
| Visible-page versus native-text verification | Compare bounded rendered regions with their existing PDF text occurrences; distinguish a usable-looking text layer from verified glyph/text correspondence, preserving both candidates and ambiguous alignments | Pending extension beyond current replacement-character, unmapped-glyph and text-coverage triage; approved references required for measured error detection |
| Perturbation stability checks | Compare source-bound candidates under small declared padding/resolution changes; retain transforms, work budgets and unstable occurrences without treating agreement as correctness | Pending; current manual recipes and run comparisons do not implement automatic stability probes or calibrated warning thresholds |
| High-confidence spot audits | Reproducible random samples from apparently good results alongside risk-selected review; retain sampling probabilities, coverage and independently reviewed errors | [Local opt-in implementation](ocr-spot-audits.md) with full-page eligibility/exclusions, retained nominal sampling probabilities, separate reviewed/unresolved/unavailable/pending outcomes and source-bound private restart. [Development checks](evidence/2026-09-08-ocr-spot-audit-development.md): 419 passing focused/compatibility tests, lint and generated actual browser/real-storage round trip; confidence remains an eligibility label, not accuracy. After the preserved [failed v1 attempt](evidence/2026-09-08-ocr-review-qualification-failure.md) and independently audited 77-case test-only architecture repair, [fresh v2 copied-source qualification](evidence/2026-09-08-ocr-review-qualification.md) passed 13,980 tests and all nine gates, with exact 13,987-node/598-file/28-artifact verification and 40 retrieval predicates. Independent human-reviewed errors, representative inference, broader accessibility/usability and the retained cache-cleanup limitation remain pending |
| Scan-quality and rescan advice | Bounded, source-bound blur/resolution/contrast and edge-clipping diagnostics yield actionable operator advice; measure warning usefulness and paired rescans on approved examples | [Locally qualified](evidence/2026-09-09-ocr-crop-advice-qualification.md): bounded exact-RGB luminance, adjacent-difference and edge-band observations plus explicit ambiguity/rescan guidance passed 14,154 tests and all nine copied-source gates, with exact 14,161-node/607-file/28-artifact verification and 40 retrieval predicates. [Focused development](evidence/2026-09-09-ocr-crop-advice-development.md), [native checks](evidence/2026-09-09-ocr-crop-advice-native-browser.md) and [archive profile/draft/Cancel follow-up](evidence/2026-09-09-ocr-crop-advice-browser-followup.md) retain separate overlapping evidence and earlier failures. The [v7 generated 576-DPI horizontal navigation check](../tmp/ocr_crop_advice_navigation_v7.md) passed with preserved captured fields and final source/pack checks. These annotation-on appearance observations do not establish diagnosed blur/acquisition resolution, warning usefulness, paired-rescan gains or representative accuracy. Broader navigation/accessibility and measured usefulness remain pending; no automatic source replacement |
| Document-integrity warnings | Flag suspected duplicate pages, suspicious printed-page-number gaps and broken paragraph/table continuations with exact page evidence, ambiguity and false-alarm measurements | Pending; intentional repetition, numbering changes and cross-page layout can resemble defects; warnings must not claim missing pages or automatically delete/reconstruct content |
| Recognizer script and symbol coverage | Compare explicitly reviewed script/symbol requirements with pinned recognizer capabilities and measure unsupported/confused glyph outcomes on approved references | Pending; character-set/model metadata alone does not establish recognition accuracy, and unexpected scripts/symbols must not silently trigger model downloads or unsupported accuracy claims |
| Approval-context correspondence | Captured confirmations bind the displayed source/page/annotation generation, including queued events; stale actions fail without mutation or export, and drafts restore no approval | Reading-order scope qualified in Phase 6. The separately preserved actual installed-Gradio annotation queue probe, intentional token/focus repair and new scan controls now have independent review, 49 annotation/37 scan focused cases, retained browser restart/export checks and [Phase 7 qualification](evidence/2026-09-07-ocr-scan-review-qualification.md). Tokens are not authenticated human approval; retained browser timing is not deterministic queue-race evidence |

The latest assessment also adds these requirements without treating the pending
measurement, guided navigation or correction-publication outcomes as complete:

| Requirement | Acceptance evidence | State |
| --- | --- | --- |
| Complementary pale/reversed-text discovery | Predeclared bounded contrast/polarity variants, retained disagreement, region recall and non-text false alarms on approved examples | Pending; current independent pixel recipe accounts only for its dark-Otsu foreground |
| Retained alternative readings | Source-occurrence-bound word/glyph hypotheses with exact engine/recipe identity, ambiguity-preserving review and no silent candidate adoption | Pending; side-by-side run comparison does not itself preserve word/glyph alternatives |
| Document consistency warnings | Explicitly scoped totals, identifiers, bracket and value/unit checks with source evidence, negative controls and reviewed false-alarm rates | Pending extension of existing meaning-sensitive checks; warnings must not rewrite source text |
| Formula and code structure fidelity | Approved bounded specialist routes preserve fractions/subscripts/indentation and measure structural as well as text errors | Pending; no new model acquisition or capability/accuracy claims from upstream documentation alone |
| Private autosave and crash recovery | Opt-in bounded source-bound snapshots, visible unsaved status, interruption/restart controls and cleared approvals | Pending; requires explicit retention settings; current draft save is manual |

The subsequent assessment adds the following concrete extensions. Region and
hard-scan resume remain part of OCR progress checkpoints above. Scan-first
transcription extends independent reference adjudication; keyboard navigation,
linked highlighting and random good-result audits retain their existing rows.

| Requirement | Acceptance evidence | State |
| --- | --- | --- |
| Dense-page scan grouping | Exact spatial-neighbor enumeration preserves admitted region semantics; explicit versioned changes to dense-page admission; paired availability, work and memory measurements | Locally implemented and independently reviewed: opt-in spatial-v2 passed [Phase 8 qualification](evidence/2026-09-07-ocr-spatial-scan-qualification.md), 8,960 tests and all nine gates. Generated native comparison preserves all eight pages' geometry, pixels, regions, IDs and foreground; retained default output remains byte-identical. Actual CLI/fresh/historical replay passed. Two physical 1,600-component cases become available, including retained nontext false positives; eight isolated workers supply scoped memory observations, not a guarantee. Legacy remains default; representative recall/false alarms and accuracy remain unestablished |
| Crop-edge safety | Source-bound edge-touch warnings and bounded padding alternatives; paired character/region errors and negative controls for duplicated neighboring text | [Locally qualified](evidence/2026-09-09-ocr-crop-advice-qualification.md): four inward pixel-band observations and an explicit new-scope proposal bounded to two points per side passed the 14,154-test/nine-gate copied-source checkpoint. Partial/missing coverage remains visible; the proposal implies no execution, reference reuse or approval. The separate [generated paired experiment](evidence/2026-09-09-ocr-crop-edge-paired-experiment.md) completed ten one-attempt OCR calls with exact native BGR/dispatch joins, seven admitted scope scores and three partial-null exclusions. Five fixed-target comparisons found three improved and two unchanged count outcomes; both padded duplicate controls still contain extra neighboring text despite matching their wider-scope references. Actual boxes retain mixed horizontal detections and separate vertical target/neighbor association; shape-dependent engine padding/resizing prevents a glyph-strip-only causal claim. [Native checks](evidence/2026-09-09-ocr-crop-advice-native-browser.md), [archive follow-up](evidence/2026-09-09-ocr-crop-advice-browser-followup.md) and [v7 horizontal navigation](../tmp/ocr_crop_advice_navigation_v7.md) remain separate evidence. Region/token localization error rates, broader navigation, human adjudication and representative benefit remain pending; no automatic crop adoption |
| Annotation-layer diagnostics | Separately bound annotation-on/off rasters retain the visible source and label overlay differences; generated stamps/highlights and reviewed false-alarm measurements | Pending; the current inspection recipe deliberately includes PDF annotations and never silently removes markup |
| Short-symbol review | A distinct source-bound review lane for isolated digits, decimal/minus signs and markers; contextual ambiguity retained; recall and false alarms against speckles, rules and pictures | Pending extension of typography/small-text work; fewer than three components remain ambiguous in the current scan classifier |

The latest recommendation also retains reading-order repair, independent
scan-based omissions, small-print rescue and the complete approval/publication/
rollback workflow already listed above. None is completed merely by shipping
the AI reader or by passing synthetic integration checks.

The subsequent assessment adds two experimental checks without replacing the
existing size-aware retry, critical-symbol, guided-review or representative
measurement requirements:

| Requirement | Acceptance evidence | State |
| --- | --- | --- |
| Overlapping-tile and long-line safety | Bounded oversized-region experiments retain source-coordinate ownership and explicitly measure omitted or duplicated characters at tile/line joins, with negative controls and no automatic canonical replacement | Pending; existing bounded region OCR is not a measured overlapping-tile reconstruction workflow |
| OCR execution repeatability | Identical pinned inputs and recipes exercised across repeated runs, batch sizes and page order; exact text/geometry differences and runtime variability retained separately from accuracy, without tuning on frozen evaluation families | Pending extension of reproducibility; pinned versions and source hashes alone do not prove inference-order or batch-size invariance |

The latest assessment adds concrete observability, resource-safety and review
extensions. They supplement existing crop-edge, false-alarm, multilingual and
representative-evaluation work; none is completed by the spatial checkpoint.

| Requirement | Acceptance evidence | State |
| --- | --- | --- |
| Missing-text disposition accounting | Bound each observed detection through recognized, empty, filtered, failed or retained outcomes; reconcile observed counts and preserve unobserved/ambiguous stages without fabricated identity | [Locally qualified](ocr-detection-disposition.md) through [Phase 10](evidence/2026-09-07-ocr-disposition-qualification.md): strict three-route companion, geometry, observer and private bundle/CLI; 9,958 passing tests and all nine gates with independent verification. Separate zero-new-OCR posthoc checks preserve all 12 retained native candidates and 166 detection/line joins. The failed original outer pixel comparison and first full gate run remain recorded. Healthy generated native coverage does not demonstrate blank/filter/failure paths, complete text capture or accuracy; inert-session/model-free controls cover partial failures and healthy peers. Default minimum score is already zero |
| Shared internal-engine allocation safeguards | Preflight actual resize/padding/rectification/recognizer tensor shapes across ordinary, region and hard-scan retries before native allocation; retain per-item failure and unchanged admitted outputs | [Locally qualified](ocr-engine-allocation.md) through [Phase 9](evidence/2026-09-07-ocr-engine-guard-qualification.md): all four readers share the guard; setup failures consume no raw call ID and completed raw calls remain distinct from accepted candidates. The accounting repair has 34 existing-API and 53 observer/lifecycle controls; final generated native replay preserves all 12 calls' complete candidate bytes. Independent review and all nine gates passed with 9,387 tests, seven skips and six warnings. Conservative cap-boundary abstentions are explicit; selected allocation bounds do not guarantee total process memory or representative accuracy |
| Word/symbol-linked review | Source-occurrence-bound within-line geometry supports precise scan jumps and critical checks; measure localization errors and review effort, retaining original lines and abstaining on ambiguous alignment | Pending extension of line-level review and typography/context checks; optional upstream boxes alone do not establish correct alignment |
| Engine-capacity warnings | Observe bounded candidate-limit and work-exhaustion evidence, with clear unavailable/truncation-risk states and negative controls; distinguish a completed operation from complete source capture | Pending extension; current scan limits already yield explicit unavailable pages, but this is not end-to-end recognizer/detector saturation accounting |
| Non-text false-alarm qualification | Retain decorations, repeated marks, speckles, rules and pictures alongside real small/isolated text; measure both false alarms and missed text before changing review prioritization or classification | Pending representative extension; generated spatial evidence explicitly retains 32 false-positive text-like groups and does not justify automatic suppression |
| Script-appropriate scoring | Explicit reviewed language/script scoring profiles supplement unchanged baseline CER/WER, with known-edit controls and declared units/normalization | Pending; whitespace-based WER is not a suitable word segmentation claim for every script, and normalization must not conceal meaningful errors |

The subsequent recommendations retain these priorities and add one targeted
experiment; they do not establish gains from upstream image-processing advice.

| Requirement | Acceptance evidence | State |
| --- | --- | --- |
| Degradation-specific preprocessing experiments | Bounded local-threshold and conservative noise-cleanup variants retain original pixels/candidates; paired omission, punctuation, thin-stroke and non-text controls on approved references | Pending extension of adaptive/hard-scan recovery; no automatic filter selection or replacement, and no assumption that Tesseract preprocessing advice establishes RapidOCR gains |

The latest recommendation also makes review effort an explicit acceptance
outcome. It extends existing navigation and calibrated prioritization rather
than treating fewer visible warnings as improved OCR accuracy.

| Requirement | Acceptance evidence | State |
| --- | --- | --- |
| Review-effort qualification | Consequential errors found per review minute, false alerts per page and random unflagged-page misses; grouped overlapping evidence preserves every unresolved source occurrence | Pending extension of guided review and non-text false-alarm qualification; generated region counts alone do not establish reviewer usefulness, and grouping must not silently dismiss warnings |

The subsequent assessment prioritizes finishing guided crop comparison and
independent reference authoring before representative evaluation. It retains
the existing small-text, independent-engine, adaptive-retry, meaning-sensitive
and downstream-evaluation requirements, and adds these concrete refinements:

| Requirement | Acceptance evidence | State |
| --- | --- | --- |
| Source-resolution review magnification | Bounded re-rendered scan details retain the exact physical scope and reveal small glyphs without claiming that enlarging a preview recovers lost source information | [Locally qualified](evidence/2026-09-08-ocr-crop-detail-qualification.md): fixed Fit/288-DPI/576-DPI renders, profile-bound worker v2, presentation, draft-preserving reload and field feedback passed 11,839 tests and all nine copied-source gates with independent verification of 545 files and 40 retrieval predicates. The [development record](evidence/2026-09-08-ocr-crop-detail-development.md) separately retains generated archive/live browser flows, actual oversized-controller refusal/Fit recovery and prior failures. Later [image-ownership hardening](evidence/2026-09-08-ocr-preview-image-ownership-qualification.md) passed 12,127 tests and all nine gates with independent 548-file verification. The first live-preview refusal remains unexplained; broader platform/accessibility, eventual framework disposal and oversized UI draft recovery remain outstanding. No representative accuracy gain is established |
| Explicit reference uncertainty | Reviewed illegible/uncertain spans and adjudication history retain exact source scope; scored and unscorable coverage remain separate, with no invented transcription or hidden exclusions | [Locally qualified](evidence/2026-09-08-ocr-uncertainty-qualification.md): 13,709 tests and all nine copied-source gates passed with independent retained-artifact verification of 580 files, 13,716 collected identities and 40 retrieval predicates. The user-approved bounded inventory cap and exact reproducible architecture review are recorded separately from original index/history, which remain unchanged. [Backend development](evidence/2026-09-08-ocr-uncertainty-backend-development.md) and [pack/host development](evidence/2026-09-08-ocr-uncertainty-pack-host-development.md) retain same-render metadata, bounded history, whole-crop abstention, versioned storage and recovery. [Visual UI development](evidence/2026-09-08-ocr-uncertainty-ui-development.md) and the [dependency refactor](evidence/2026-09-08-ocr-uncertainty-ui-refactor-development.md) retain their scoped Queue/browser/regression evidence and prior failures. After the later initialization repair, the [generated native round trip](evidence/2026-09-08-ocr-uncertainty-native-roundtrip.md) completed two actual OCR calls, unresolved review/Save without additional scoring, fresh-host Open without restored approval or scoring, and explicit resolution/confirmed child Save with the original parent unchanged. The later repairs now pass [fresh v4 full qualification](evidence/2026-09-08-ocr-uncertainty-repair-qualification.md): 13,749 tests, all nine gates and independent verification of 586 files and 40 retrieval predicates; broader accessibility/usability remain pending. No representative accuracy gain or independent human adjudication is established; baseline CER/WER semantics remain unchanged |
| Actionable preview diagnostics | Allowlisted failure-stage codes distinguish observed input changes, scope/geometry failures, rendering failures and raster bounds without private exception details or guessed historical causes | Independently reviewed seven renderer codes and 102 focused renderer tests were included in the 595-check guided-review run and [Phase 11 qualification](evidence/2026-09-07-ocr-guided-review-qualification.md). [Phase 12](evidence/2026-09-07-ocr-preview-isolation-qualification.md) separately qualifies four host-only lifecycle codes and the fixed same-crop worker; no new retry or guard relaxation. Earlier standalone refusals remain preserved, without guessed causes; broader platform/renderer and total-memory qualification remains pending |
| Isolated preview resource safety | Fixed-source preview workers have bounded deadlines and verified cancellation/cleanup; decoder resource observations and admitted image bounds are reported separately | [Locally qualified in Phase 12](evidence/2026-09-07-ocr-preview-isolation-qualification.md): fixed same-crop worker/controller, private host-temporary staging and per-view cancellation; 639 focused checks, 10,697 full-suite passes and all nine gates with independent verification. Generated success and live post-start-gate cancellation have separate retained readback and scoped worker-memory observations. Initial native failures and audit-time metadata observations remain preserved. Other renderers remain in-process; active-decoder cancellation, total-memory limits, general performance and representative accuracy are not established |

## Working rules

The later [same-crop image-ownership hardening](evidence/2026-09-08-ocr-preview-image-ownership-qualification.md)
is locally qualified across rendering, supervision, coordinator, private pack
loading and live/archive callbacks: 12,127 tests and all nine copied-source gates
passed with independent verification of 548 files, 12,134 collected test identities
and 40 retrieval predicates. Failed transfers attempt to close once without
replacing the primary failure; successful outputs remain usable. The separate
development record preserves scoped failures and repairs. Eventual framework disposal, failed-close recovery,
total-memory bounds and the historical intermittent preview refusal remain
unresolved. Explicit reference uncertainty and every other outstanding outcome
above remain in scope.

The [2026-09-06 local qualification record](evidence/2026-09-06-ocr-local-qualification.md)
records the passing 7,495-test Phase 4 Windows checkpoint, all nine static/test
and offline retrieval gates, and independent artifact verification. This covers
the stage diagnostics and bounded shared-reader verification change in addition
to the earlier Phase 3 work. It does not complete the outstanding outcomes above
or establish representative OCR accuracy. Documentation-only result updates
follow the frozen source checkpoint; new implementation needs its own evidence.

The [2026-09-07 AI evidence checkpoint](evidence/2026-09-07-ai-evidence-local-qualification.md)
extends local qualification through the text-evidence service and explicit HTTP
lifecycle repair: 7,952 passing tests, seven platform-specific skips, exact
collection/JUnit coverage and all nine gates. Its separate offline column-layout
prototype has a measured known-fixture gain and a table-control regression;
at that checkpoint, the unconfirmed source-bound suggestion feature remained
future implementation, not a completed accuracy outcome. The subsequent
[local column-suggestion implementation](ocr-column-suggestions.md) and
reading-order confirmation-context fix have their own
[Phase 6 local checkpoint](evidence/2026-09-07-ocr-column-review-qualification.md):
8,230 passing tests, seven platform-specific skips, six warnings, all nine gates
and independent collection-to-JUnit/frozen-source verification. Independent
retained-browser review checked 41 bindings and the exact 14-line generated
permutation. Phase 5 does not qualify these later changes; Phase 6 does not
establish representative accuracy or complete the broader program.

The [Phase 7 scan-review checkpoint](evidence/2026-09-07-ocr-scan-review-qualification.md)
adds independently reviewed source-pixel inspection, original-raster crop review
and annotation-context safeguards. All 8,659 collected tests match JUnit:
8,652 passed, seven platform-specific skips and six deprecation warnings. All
nine selected gates and 468 tracked-file/source/index bindings passed independent
verification. The eight-page generated scan and failed-page browser workflow
do not establish representative accuracy. The separate spatial-grouping probe
was not part of that checkpoint's qualified production behavior.

The subsequent [Phase 8 spatial checkpoint](evidence/2026-09-07-ocr-spatial-scan-qualification.md)
qualifies the explicit `spatial-v2` recipe within generated local scope: 8,960
passing tests, seven platform skips, six warnings and all nine selected gates;
all 8,967 collected nodes match JUnit and all 471 tracked-file/source/index
bindings stayed frozen through independent audit. Actual native, CLI and
historical-reader checks retain default compatibility, bounded availability,
work and memory observations, including non-text false positives. Neither
representative OCR accuracy nor the remaining AI-access/publication outcomes
are established; the full pending program stays intact.

The subsequent [Phase 9 engine-guard checkpoint](evidence/2026-09-07-ocr-engine-guard-qualification.md)
qualifies selected allocation safeguards and raw-dispatch accounting: 9,387
passing tests, seven platform skips, six warnings and all nine selected gates.
All 9,394 collected identities and 482 tracked-source/logical-index bindings
passed independent verification. Generated native replay preserves complete
candidates across 12 actual calls; it does not establish accuracy gains,
total-memory safety or missing-text disposition. New diagnostic implementation
follows that frozen checkpoint and needs separate evidence.

The subsequent [Phase 10 disposition checkpoint](evidence/2026-09-07-ocr-disposition-qualification.md)
qualifies the opt-in three-route companion within local generated scope:
9,958 passing tests, seven platform skips, six warnings and all nine gates;
9,965 collected identities and all 498 tracked-source/logical-index bindings
passed independent retained verification. Original native outer-comparison and
full v1 gate failures are preserved, with separate successful posthoc evidence
and a test-only architecture assertion repair. The audit's original console
exit status is unavailable; its completed record was independently rechecked.
Diagnostics do not establish representative accuracy, complete capture or
correction approval. The full pending program remains unchanged; the next
integration connects diagnosis, bounded retries and same-scope comparison to
the review workflow without replacing its baseline or expanding AI authority.

The subsequent [Phase 11 guided-review checkpoint](evidence/2026-09-07-ocr-guided-review-qualification.md)
qualifies the opt-in coordinator, same-crop reference/scoring, original-source
preview and value-change safeguards within local generated scope: 10,508 passing
tests, seven platform skips, six warnings and all nine selected gates. All
10,515 collected identities and 514 tracked-source/logical-index bindings passed
independent verification. Architecture refresh and finite inventory sizing were
independently reviewed. Retained browser v3 checks cover two actual native calls
and specific edit/reload/refusal controls, not all clipboard/IME/platform paths.
Earlier failed browser/preview generations remain preserved. Durable crop-review
packs/restart, source-detail magnification, uncertainty and isolated decoding
remained next steps at that checkpoint; the broader accuracy, downstream and
AI/publication program was not completed by it.

The subsequent [Phase 12 isolated-preview checkpoint](evidence/2026-09-07-ocr-preview-isolation-qualification.md)
qualifies the fixed same-crop worker/controller, private host-temporary staging
and per-view cancellation: 10,697 passing tests, seven platform skips, six
warnings and all nine selected gates. All 10,704 collected identities and 520
tracked-file content/logical-index bindings passed independent verification.
Generated native success and live post-start-gate cancellation have retained
readback and bounded worker-memory observations, not active-decoder-entry proof,
a total-memory cap or a new whole-browser run. Earlier failures and audit-only
metadata observations remain explicit. Durable review packs, magnification,
reference uncertainty and the broader program remained pending at that checkpoint. Documentation-only
result updates follow the frozen checkpoint and do not qualify new implementation.

Subsequent [crop-pack development](evidence/2026-09-07-ocr-crop-pack-ui-development.md)
adds private manual draft/reviewed Save, historical Open and fresh-source
confirmation without reconstructing live OCR authority. The later generation
passes 659 focused tests after a browser-discovered duplicate-selection repair.
Generated archive Save/Open, fresh-host reopen and independent retained-byte
checks have separate recorded scopes. Those development checks preceded the
copied-source checkpoint below; no representative accuracy, automatic correction/
publication or completion of the full program is claimed.

The [crop-review pack copied-source checkpoint](evidence/2026-09-07-ocr-crop-pack-qualification.md)
now qualifies private Save/Open/revision integration through 11,476 passing
tests, seven platform skips, six warnings and all nine local gates. Independent
retained verification matched all 11,483 collected identities, 40 current CI
retrieval predicates and the complete 538-file copied cohort against the 520
tracked original files plus 18 admitted additions. Only the copy received the
reviewed architecture baseline; the original Git index/history were not changed.
This does not qualify the original default tracked-source inventory, hosted CI,
representative OCR accuracy or release. Earlier preparation, v1 gate and audit
checker failures remain explicit. These documentation updates follow the frozen
checkpoint. Source-detail magnification, reference uncertainty and every other
outstanding requirement above remain in scope; the full goal is not complete.

The later [crop-detail copied-source checkpoint](evidence/2026-09-08-ocr-crop-detail-qualification.md)
passed 11,839 tests, seven skips, six warnings and all nine local gates, with
exact collection/JUnit, 545-source and 40-predicate independent verification.
Its failed v1 metadata-only copied-index transition remains preserved; v2 used
new starting provenance and a complete rerun, not composed test results.
Original Git index/history and architecture baseline remain unchanged.
Separate source review found missing explicit image disposal on late preview
failures; that resource-resilience hardening takes priority over reference
uncertainty. Neither issue, representative accuracy nor the full program is
completed by this checkpoint. These documentation updates follow its freeze.

The subsequent [image-ownership copied-source checkpoint](evidence/2026-09-08-ocr-preview-image-ownership-qualification.md)
qualifies those late-failure close paths through 12,127 passing tests, seven skips,
six warnings and all nine local gates. Independent verification/audit matches
all 12,134 collected identities, 548 source files and 40 retrieval predicates.
The fresh copy's own stat cache was primed before freezing; original index/history,
old copies and the architecture size guard remain unchanged. Reference uncertainty,
representative accuracy and every other pending requirement remain in scope.
New implementation and these documentation updates follow that frozen checkpoint.

The subsequent [reference-uncertainty copied-source checkpoint](evidence/2026-09-08-ocr-uncertainty-qualification.md)
qualifies uncertainty retention, authoring and the shared UI dependency refactor
through 13,709 passing tests, seven skips, six warnings and all nine local gates.
Independent retained-artifact verification matches 13,716 collected identities,
580 source files and 40 retrieval predicates. Two fresh inventory generations
exactly match the previously reviewed candidate; the explicitly approved size
cap is 1,952,138 bytes, with 13,605 bytes of headroom. Only the copy received the
new baseline; original Git index/history and baseline remain unchanged. Native
live end-to-end uncertainty, broader accessibility/usability, independent human
adjudication and representative accuracy remain pending. All 67 requirements
remain in scope. These documentation updates follow the frozen checkpoint.

The later [native workflow development check](evidence/2026-09-08-ocr-uncertainty-native-development.md)
completed two real OCR calls and a clean comparison for one generated title crop,
but could not complete annotation/Save/reopen because the editor was inert.
A real lazy-tab reproduction isolated a skipped-update initialization failure;
the fixed editor passes 139 focused tests and browser keyboard/pointer,
archive/detail and tab-return checks. Separate child-environment hardening has
focused regression and compatibility evidence. These later repairs are not
covered by the earlier full qualification. At that checkpoint the complete native uncertainty
journey and all other outstanding requirements remained pending; neither a new
representative accuracy benchmark nor broader AI/corpus authority is implied.

The subsequent [native round trip](evidence/2026-09-08-ocr-uncertainty-native-roundtrip.md)
completed the bounded generated-fixture annotation/review/Save/fresh-host
Open/resolve/confirmed-child-Save journey. Both actual OCR calls and both host
lifecycles passed retained readback. Unresolved review and Save added no scorer
calls; fresh Open restored no approval and made zero scorer calls. Explicit
resolution retained the annotation coordinates and original parent pack.
The final resolved 23-character title comparison has zero normalized CER/WER at
both DPIs, not a representative accuracy gain. At that checkpoint, a fresh full
qualification of the repaired source generation and all other outcomes remained next;
all 67 requirement/acceptance pairs and the original AI-access limits are retained.

The subsequent [fresh repair qualification attempt](evidence/2026-09-08-ocr-uncertainty-repair-qualification-failure.md)
failed: 13,727 tests passed, two Phase A0 `cli_info_empty` probe tests failed,
seven skipped and six warnings. All eight other gates and 40 retrieval predicates
passed. Independent retained-failure audit matched all 13,736 collected/JUnit
identities and 584 source/Git bindings. The unchanged-cap architecture candidate
was independently reviewed and exactly reproduced; original index/history and
baseline remained unchanged. Benchmark guard propagation to the nested supervised
CLI was then a code-backed hypothesis, not confirmed inner-process telemetry. A bounded
diagnostic, scoped repair and passing fresh qualification remained next. The native
round trip is separate evidence; the full 67-requirement goal is not complete.

The subsequent [diagnostic and scoped repair](evidence/2026-09-08-phase-a0-guard-repair-development.md)
confirmed the generated guard-bootstrap mechanism without weakening production
scrubbing. The new [v4 full qualification](evidence/2026-09-08-ocr-uncertainty-repair-qualification.md)
then passed 13,749 tests, all nine local gates and independent verification of
13,756 collected identities, 586 source bindings and 40 retrieval predicates.
Seven existing platform skips and six warnings remain explicit; all 77 Phase A0
benchmark tests passed. This supersedes the pending full-qualification status,
not the failed v3 evidence or broader OCR/AI acceptance requirements. At that
checkpoint, clearer long-Save feedback remained next; no new representative
accuracy gain or expanded corpus/AI authority is established.

The later [long-Save feedback development check](evidence/2026-09-08-ocr-save-feedback-development.md)
passed 482 focused tests and lint. Generated browser checks used real storage to
observe pending/success, edit/Cancel refusal, queued duplicate rejection and
explicit recovery. Request, processing and unconfirmed statuses are now distinct;
one-use approval and storage checks remain intact. Fresh full qualification of
these later changes and broader assistive-technology coverage remain pending.

The subsequent [Save-feedback and spot-audit copied-source checkpoint](evidence/2026-09-08-ocr-review-qualification.md)
passed 13,980 tests, seven skips, six warnings and all nine local gates.
Independent retained verification matched 13,987 collected identities, all 598
source files, 28 artifacts and 40 retrieval predicates. The earlier three-failure
attempt is immutable; its test-only architecture repair passed 77 focused cases
before this fresh complete run. Only the copy received the reviewed architecture
baseline; the original index/history/baseline and approved size cap are unchanged.
The shorter serial run is not an established speedup. Representative accuracy,
independent human review, broader usability, private-cache cleanup and every
other pending OCR/AI outcome remain outstanding. These documentation-only
updates follow the frozen qualification; all 67 requirement/acceptance pairs
are preserved.

- Preserve all previous work, established recovery v1/v2 formats and defaults.
  New experiments have distinct strict versioned contracts.
- Use generated fixtures until the owner identifies approved representative
  sources and reference-retention policy. Synthetic mechanics are not evidence
  of generalized book accuracy.
- Treat OCR/model output and retrieved source text as untrusted data, never as
  instructions or approval. AI may propose; review does not publish canonical
  extraction changes or alter indexes.
- Keep local listeners literal-loopback-only. No tunnels, hosted connectors,
  automatic model downloads, global AI configuration changes or public release.
- Source-bound private artifacts are create-only, bounded and reverified before
  publication. Do not overwrite earlier experiments.
- Exercise failure paths and real available optional clients, independently
  review changes, refresh architecture evidence under its ADR, then freeze
  tracked sources for the complete suite and policy gates.
- Record outstanding outcomes honestly. A green local test suite is neither
  locked-runtime validation nor production/corpus qualification.

## Initial inspection

The existing service v1 already supplies authenticated corpus discovery and
bounded search with a reader token, separate from administrative jobs. An AI
client should use that surface, not add arbitrary shell or filesystem routes.

The ambient Python 3.14 OCR runtime is not the committed locked runtime. The
installed Docling options also lack a mode used by the current conversion
configuration. These are capability-preflight and validation requirements,
not grounds to silently change locks or claim a successful conversion.

Current measured evidence remains the eight-page generated calibration suite
and an operator-guided column-order correction. It is not held-out evidence.
