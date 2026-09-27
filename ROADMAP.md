# Improvement Roadmap

- **Status:** Live scheduling authority
- **Current as of:** 2026-08-21
- **Baseline:** `main` and `origin/main` at
  `1e79540c9e3fbbb31f5eabdac35d608d68b7588f` (tree
  `96ff1600afc3aa1fcefac3d515bbdaf258f7c7b9`)
- **Active implementation plan:**
  [`docs/superpowers/plans/2026-08-18-next-improvement-program.md`](docs/superpowers/plans/2026-08-18-next-improvement-program.md)
- **Point-in-time evidence:** [`docs/evidence/`](docs/evidence/README.md)

This file contains current status, authorization, dependencies, blockers, next
actions, and acceptance gates. The former multi-thousand-line R0-R12 chronology
is preserved byte-for-byte in the
[`443dce4` roadmap snapshot](docs/evidence/roadmap-through-2026-08-18-443dce4.md).
Its pending/frozen statements are historical and are not live instructions.

For policy and work authorization, use this authority order:

1. approved governance decisions;
2. maintained architecture decisions, machine-readable policy, and schemas;
3. this live roadmap;
4. operator documentation; and
5. implementation plans and historical evidence.

Runtime source and executable tests establish observed behavior and expose
drift. They cannot override an owner decision or authorize work.

An implementation plan may refine execution details but cannot infer an owner
decision, weaken a maintained policy, authorize private-source disclosure, or
publish a release.

## Active status and constraints

### Local OCR development qualification (2026-09-06)

The user-requested [OCR and AI-access improvement program](docs/ocr-improvement-program.md)
remains active in the dirty local worktree; it does not supersede the integrated
milestones or grant release/private-corpus approval. A frozen Windows CPython
3.12.10 full-lock run observed 6,841 passing tests, seven skips and eight failures.
One failure was a stale architecture consumer expectation; the other seven
exposed Windows venv redirector PID mismatches in supervised workers, detached
manager handshakes and Phase A0 parent/descendant traces.

The qualification repair uses a shared same-interpreter launch helper, following
CPython's base-executable/child-only launcher mechanism. This is an intentional
Windows correctness repair, not a behavior-preserving extraction: exact PID,
birth identity, nonce, startup-gate and isolation checks remain enforced.
Dependencies, model locks and existing environments are unchanged. The reviewed
replacement architecture snapshot and complete frozen-source gate run passed:
6,871 tests passed, seven platform-specific skips, all static gates and three
offline retrieval baseline suites passed. The
[local qualification record](docs/evidence/2026-09-06-ocr-local-qualification.md)
retains exact source/receipt hashes and limits. Representative OCR accuracy,
remaining program features and owner policy decisions are still outstanding;
this local checkpoint is not hosted CI, release or whole-program completion.

Subsequent omission/editor, cleanup-audit and page-checkpoint work passed the
separate 7,232-test Phase 3 frozen local checkpoint and all nine gates. The later
stage-diagnostic modules and deliberately bounded Windows artifact-verification
read now pass Phase 4: 7,495 tests, seven platform-specific skips, all nine gates
and independent frozen-source/artifact verification. The actual generated stage
run verified 54 calls and exposed 15 column-order inversions despite exact
per-line recognition. These are local synthetic mechanics, not representative
accuracy gains. The full program, including the latest suggested extensions,
remains active. Subsequent opt-in AI text-evidence search now has local
worker/HTTP/client integration and focused independent review. The generated
native run passed cached-model indexing, contained search, authenticated HTTP
and fresh CLI (12 indexed records, 11 hits). Phase 5 then passed all nine local
gates: 7,952 tests passed, seven platform-specific skips, all 7,959 collected
cases matched JUnit, and 445 tracked files stayed frozen through independent
verification. The [2026-09-07 record](docs/evidence/2026-09-07-ai-evidence-local-qualification.md)
retains its controlled-environment scope, exact identities and preserved failed
fixture/preflight diagnoses. No representative accuracy claim follows.
Integration also exposed and repaired the
pre-existing HTTP start/close thread-affinity hazard with explicit owner-thread
and cancellation regressions. Scan access, exact correction adoption and the
remaining accuracy outcomes are still pending; Phase 4 does not cover this new
implementation. See [evidence-search usage](docs/ai-evidence-access.md) and its
[additive architecture decision](docs/architecture/decisions/service-evidence-companion.md).

The unconfirmed source-bound column-layout suggestion now has its own
[Phase 6 local checkpoint](docs/evidence/2026-09-07-ocr-column-review-qualification.md):
8,230 passing tests, seven skips and all nine gates, with independent retained
browser review. Operator prose classification and separate preview/export
approval remain required. The prior boxes-only prototype's table-control
regression remains evidence against automatic adoption, not evidence to erase.

A later actual installed-Gradio queue probe separately reproduced stale
reference, crop and omission actions using a changed page/annotation generation.
The annotation-token/focus repair is an intentional correctness change, not a
behavior-preserving refactor; its preserved pre-fix evidence and 49 focused
controls received independent review. Bounded source-only pixel inspection,
the separate original-page panel and this annotation repair now passed their
own [Phase 7 local checkpoint](docs/evidence/2026-09-07-ocr-scan-review-qualification.md):
8,652 tests passed, seven platform skips, six warnings, all nine selected gates,
exact 8,659-node collection/JUnit correspondence and 468 unchanged tracked files
through independent verification. Generated native/browser evidence is not
representative accuracy or complete-source proof. At that checkpoint, the
separate 323-case spatial grouping prototype was geometry-only evidence.
The subsequent explicit `spatial-v2` production recipe now has its own
[Phase 8 local checkpoint](docs/evidence/2026-09-07-ocr-spatial-scan-qualification.md):
8,960 tests passed, seven platform skips, six warnings and all nine gates;
all 8,967 collected nodes match JUnit and 471 tracked files/index stayed frozen
through independent audit. Generated native checks preserve all eight pages'
regions/IDs/pixels, the default output remains byte-identical on the retained
challenge, and actual CLI/fresh/historical replay passed. Dense-page availability
and lower predicate counts are not representative OCR accuracy or guaranteed
memory reductions; retained non-text false positives remain explicit. Legacy
remains default. The complete OCR/AI improvement program remains active, including
approved reference inputs, AI scan serving, alternative readings, pale/reversed
text, crop-edge safety, annotation-layer diagnostics and publication/rollback.

The shared internal-engine allocation guard and intentional raw-dispatch
accounting repair subsequently passed the
[Phase 9 local checkpoint](docs/evidence/2026-09-07-ocr-engine-guard-qualification.md):
9,387 tests, seven platform skips, six warnings and all nine selected gates.
Independent verification matched every one of 9,394 collected JUnit identities
and all 482 frozen tracked files/logical index bindings. The final native replay
preserves complete candidates across eight pages, two regions and two hard-scan
calls. Setup failures consume no raw call ID; completed raw dispatch remains
distinct from accepted output. Conservative allocation abstentions and the
absence of a total-memory guarantee remain explicit. Same-call text-loss tracing, representative
accuracy and the remaining OCR/AI program are not completed by this checkpoint.

The subsequent opt-in same-call disposition companion now has its own
[Phase 10 local checkpoint](docs/evidence/2026-09-07-ocr-disposition-qualification.md):
9,958 tests passed, seven platform skips, six warnings and all nine gates.
Independent retained-artifact verification matched all 9,965 collected JUnit
identities and the complete frozen source/logical-index bindings. The original
native outer pixel comparison and first full gate run failed and remain
preserved; separate zero-new-OCR posthoc verification validates the 12 retained
page/region/hard-scan candidates. The first gate failure required only an exact
architecture-test assertion repair. The independent audit's completed record
was rechecked, but its original console exit status was not retained. No
representative accuracy or source-completeness claim follows. Unified guided
review, approved reference inputs, adaptive retries, independent-engine checks,
AI scan evidence and correction publication/rollback remain active work.

### Casebook excerpts and source-order fixes (2026-09-25, local, uncommitted)

A private 18-excerpt casebook course corpus (page-bounded scans with no table
of contents) exposed these gaps. The work is in the dirty local worktree only;
it has no PR, hosted CI or independent release review, so it is not
"Implemented (draft)".

- **Excerpt profile.** `us-law-casebook-excerpt-v1` (`scaffold_source =
  "source_headings"`) skips the TOC gate and scaffold, excludes no pages as
  front/back matter at every recompute site, builds no running-header chapter
  map, and derives each record's section path from its exact source-heading
  occurrence stack (heading lineage `level_overrides`). Existing profile
  digests are unchanged and now pinned by literal golden values.
  `--llm-scaffold` and `--split-chapters` are rejected for it at parse time.
- **Worker stdio.** Supervised workers are windowless only when the parent has
  no console window, and then receive the parent's stdio explicitly (unusable
  streams go to `DEVNULL`); before, direct CLI output of isolated commands was
  silently lost.
- **Source-order fixes** (each changes only output that fails the gates today):
  no numbered-edge relocation between lineage-bound records at boundary
  merge; one-line plain source items attest split duplicate lines; footnote
  sidecar slots follow the audit's same-page geometry; the fidelity audit no
  longer lets an erased A-J marker take an eligible item's token; a
  native-rebuilt paragraph split across raw chunks is emitted once (generic
  and layout branches).
- **Evidence.** Focused suites pass; the full suite ran 14,787 passed and 3
  failed (two from untracked OCR modules/test helper, one the stale
  architecture inventory). Re-chunking the corpus kept all 11 READY excerpts
  byte-identical through all six fixes, and the full tort-law casebook's
  records before the structural gate were identical (2,868) with and without
  them; both runs had been reported as stopped for low memory but completed
  (checked 2026-09-26). A July-era casebook conversion
  (conversion manifest schema 2) cannot be re-bound to its PDF and fails a
  split-URL structural check on current code, independent of these fixes.
- **Follow-ups (not fixed):** an excerpt ending on a bare heading fails
  lineage (needs a typed exception and policy decision); marker-only levels can
  re-parent book sections under opinion-internal `A.`/`B.`; a native rebuild
  first emitted as a generic-branch slice is still claimed twice (fail-closed);
  native text-group recovery rejects a paragraph split across two text-layer
  blocks; a text item made only of U+25A0 is dropped with its lineage
  (exclusion needs an owner decision and quality schema bump); boundary merge
  joins a bullet list line inline; the token splitter breaks after `v.`;
  duplicate-line allowances do not cover multi-item entries; the architecture
  inventory needs a reviewed `--refresh`.

### Casebook supplement, audit scope and OCR findings (2026-09-26, local, uncommitted)

Same status as the section above: dirty local worktree only, no PR or hosted
CI. Each change was independently and adversarially reviewed and changes only
output that fails the gates today; no schema or policy version changed.

- **Fidelity audit scope.** The greedy alignment fallback could lend the next
  cited item's token to an omitted out-of-scope page view of a scoped plain
  item (one token produced all five h17 issues). A scoped realignment now runs
  only when the unchanged alignment already contains an out-of-scope position,
  and the exclusion and the scope check share one predicate.
- **Supplement profile.** Additive `us-law-casebook-supplement-v1` (excerpt
  behavior, honest provenance for standalone casebook supplements). Existing
  digests are unchanged; the excerpt digest is now pinned.
- **Casebook pointer headings.** A case-sensitive `CHAPTER n.X…:`,
  `CHAPTER n, SECTION X:` or `CHAPTER n IN GENERAL/GENERALLY` heading that
  directly follows a bodiless, stack-opening lettered section heading (in
  serialized and printed order, with only bare page labels between; same-line
  title fragments fold in) joins that section's node, and proven lettered heads
  get section level so `I.` is a peer of `H.`.
- **Contents outlines.** `document_index` rows with no item relationships that
  directly follow a `Contents`/`Table of Contents` heading outside structural
  ranges and are dropped as structural today are published as their exact cell
  text with a table oracle; a mixed chunk fails closed. Structural ranges are
  now passed to the enrichment recovery.
- **Evidence.** Full suite 14,924 passed, 7 skipped and the same 3 failures as
  above (the inventory drift now also covers the new private helpers). A
  final-code re-chunk kept the 11 READY excerpts byte-identical to their
  publications (chunks, quality, oracles, receipts); h01, h12 and h17 pass;
  h03/h04/h16/h18 fail unchanged; the Summer 2026 Update passes 31/31 with no
  lineage issues; tort-law pre-gate records are identical.
- **OCR retry findings (no code change).** The retry tools are diagnostic only;
  re-conversion is the only publishable OCR route. Garbled OCR lines come from
  RapidOCR's text-direction classifier (`use_cls`, on by default and set by
  neither `rag.py` nor the retry tools), which flips clean upright lines;
  without it an h04 region-mode conversion passes every gate and drops no line.
  Full-page OCR of scanner-text-layer excerpts adds gate-silent line
  interleaving (h18).
- **Follow-ups (not fixed; owner decisions):** an opt-in `use_cls=False`
  conversion parameter (in the digest only when non-default; the retry and
  disposition contracts treat it as unsupported); gate-silent reading-order
  scrambles from overlapping or out-of-order layout items (h04 pp.4/6/7, h18)
  and gate-silent scanner-layer misreads (h18); fused-native repair re-splits
  clean OCR words into letter-spaced layer tokens (h16 p19); relaxing the
  one-native-block recovery gate (h03) would also rebuild h18's p20 pair from
  its misread layer (a naive relaxation; the gate-driven one implemented
  under "Multi-block overlapping-group recovery" below leaves h18
  unchanged); quote-led cross-page footnote continuations are demoted
  into body flow (h17 p13); contents records keep a pre-swap raw
  `token_count`, `table_recovered_from_pdf` and case names, and split contents
  rows are caught only by the gate; a printed range across a page-label gap
  renders as one span (h07 `pp.535-559`).

### OCR angle-classifier override (2026-09-26, local, uncommitted)

This has the same status as the sections above. It implements the first
owner-approved follow-up above. No schema or policy version changed.

- **Flag.** `--ocr-no-angle-classifier` (`convert`, `full`, `batch`, off by
  default) builds RapidOCR with `use_cls=False` whenever OCR runs. It does not
  enable OCR, `--no-ocr` normalizes it away, and it composes with
  `--ocr-full-page`.
- **Recording.** Only when the flag is set does it add
  `"ocr_angle_classifier": false` to the parameters digest and to the v3
  conversion manifest. The strict loader accepts that field only as JSON
  `false`. Resume refuses a manifest whose record contradicts its digest.
  The resume command keeps the flag. OCR Docling retry proposals refuse
  flagged conversions, because retry OCR always runs the classifier.
- **Evidence.** Default parameter digests are unchanged, including the
  `bd9ee649…` digest on every h26 receipt. A default conversion of h07 is
  byte-identical to its READY run (JSON, Markdown and receipt). A default
  conversion of h04 is byte-identical to the pre-change code. Re-chunks of
  h02 and the Summer 2026 Update are byte-identical to their publications.
  A flagged conversion of the labeled h04 reproduces the reviewed no-classifier
  Docling JSON (`551c65be…`) and passes 31/31 quality checks.
- **Use.** Apply the flag per PDF (h04 only) and never in a batch rebuild. The
  flag is recorded as requested even when OCR does not run, so a batch-wide
  flag would rewrite every text-layer receipt and force reconversion.
- **Follow-ups (not fixed).**
  - Code from before this change rejects a flagged manifest.
  - The OCR retry and disposition tools have no no-classifier route.
  - h04 keeps its 3 accepted gate-silent scrambles (pp.4/6/7).
  - No end-to-end `full` publish of the flagged h04 has run yet.
  - The architecture inventory needs a reviewed `--refresh`.

### Multi-block overlapping-group recovery (2026-09-26, local, uncommitted)

This has the same status as the sections above. It implements the
owner-approved h03 recovery-gate fix. No schema or policy version changed.

- **Change.** Overlapping-group recovery used to reject every group whose
  union holds more than one native text block. One-block groups keep their
  exact previous path. A group with several blocks is now deferred when two
  text-layer proofs hold:
  - The text layer shows one ordered run: every contributing block lies
    wholly inside the union, stream order equals geometric order, and line
    centers descend.
  - The lexeme multiset is identical. Such groups never use the late-fragment
    or native-equivalence path.

  The first chunking pass never recovers a deferred group, so it is the
  previous pipeline. Its quality publication gets a failure sink. When the
  published report fails `same_page_reading_order` on an edge whose
  `before_ref` is a deferred member on the group's page, the pass records
  those members, then lets the unchanged gate failure propagate. The
  `before_ref` is the item that must come first: the upper item of a vertical
  edge, or the last left-lane item of a two-column transition.
  `chunk_document` then replays the chunk once, outside the first failure's
  handler but under the same path-wide chunk-output lease, so no other writer
  can publish between the two passes. The replay admits exactly the named
  groups, gets no sink (so it never replays again), and skips the telemetry
  observations the first pass already recorded.
- **Failures keep their type.** Every gate failure, replayed or not, is still
  the previous plain `RuntimeError` with the same message, so run reports and
  events keep recording `error_type` `RuntimeError`. A fix-up-1 draft raised a
  private subclass, which `run_telemetry` records as `Exception`; on h18 with
  `--run-report`, the pre-change code and this code both record
  `RuntimeError` (category `internal_error`).
- **Replays are recorded.** Before it replays, `chunk_document` records one
  `chunk_order_replay` run-telemetry observation. Its
  `reading_order_violations` metric counts the named members. Run reports
  and events therefore show every replay, including one that fails, and
  `--quiet` hides only the INFO log line. A one-pass chunk records none.
  Stage names are open identifiers, so no telemetry schema changed.
- **Why the gate itself.** An earlier draft admitted a group when Docling's
  `iterate_items()` order put a member after lower same-lane text. Review
  showed that chunk preparation can already publish a detached member beside
  its host, so that draft changed synthetic documents that pass every gate.
  It was removed.
- **Scope.** By construction, output changes only when the unchanged first
  pass fails `same_page_reading_order`. Verified on the 14 READY excerpts, the
  Summer 2026 Update, h04, h16 and h18 (none replays), and on the 12 review
  synthetic cases, re-run on the final code. All 12 are byte-identical to the
  pre-change function, including the three the draft changed.
  A second 30-case review matrix, also re-run on the final code, shows the
  changed side. 5 synthetic documents that failed only
  `same_page_reading_order` before now replay once and pass: stacked groups
  with a detached late member, including two whose `texts` order differs
  from their body order. The other 25 are byte-identical to the pre-change
  functions: 19 that pass and 6 that still fail.
- **Cost.** A document that replays is chunked twice, including HybridChunker,
  enrichment and any enabled LLM calls, which run again. The replay also
  repeats the first pass's warnings (bookmark cross-check, QC flags). The
  first pass publishes its failing artifacts as before. The replay then
  republishes each file atomically, in the usual order: chunks, oracles,
  chunking receipt, quality report. A replay killed part-way can therefore
  leave a mixed set, which the artifact bindings detect, as with any
  interrupted re-chunk. The first pass now also evaluates the text-layer
  proofs for multi-block groups it used to skip. An exception there would
  fail an explicit-source chunk; none occurs in the verified corpus.
- **h03.** The staged conversion is chunked against its staged PDF.
  - With the replay disabled, the first pass is byte-identical to rc5 and fails
    only `same_page_reading_order` (p4 `#/texts/42` published after the lower
    `#/texts/39`).
  - The replay admits p4 [`#/texts/37`, `#/texts/42`] in text-layer order. It
    exits 0 and passes all 31 checks.
  - Records go from 30 to 32: page 4's two records become four smaller ones
    ([#36] 125 tokens, [#37,#42] 104, [#38,#39] 75, [#40,#41,#46] 330),
    because the recovered group is one indivisible oracle. The other 28
    records keep their text, stable ids and metadata, except for positional
    fields: `chunk_index` moves by two for the 17 later records, record 10
    changes its `next_stable_id`, and record 15 changes its
    `previous_stable_id`.
  - The output is byte-identical to the draft's and to fix-ups 1 and 2.
  - A scratch conversion of the labeled PDF has the same parameters digest
    (`bd9ee649…`) and the same content. Chunked against the labeled PDF, it
    replays the same way and passes 31/31 with the same 32 record texts.
- **Preservation.** The 14 READY excerpts and the Summer 2026 Update were
  re-chunked against their labeled PDFs. Chunks, quality, oracles and chunking
  receipts are byte-identical to their publications.
- **h18 stays blocked.** It fails only `normalization_invariants`
  (split_hyphen record 62), so it never replays, and it is byte-identical to
  rc5. The containment proof would also exclude both of its multi-block pairs
  (p17, p20), because their text-layer blocks continue past the group. h04
  and h16 are also byte-identical to rc5.
- **Tort law.** The tort-law evidence is only a direct-call simulation on its
  schema-2 conversion. That conversion cannot be re-bound on current code:
  `--source-pdf` fails verification, and without it this stage never runs.
- **Evidence.**
  - `tests/test_overlapping_native_block_runs.py` (15 tests) pins the
    recovery proofs, including the pre-existing section-heading gate that
    multi-block groups now reach. `tests/test_native_group_order_replay.py`
    (21 tests) pins the replay: the named violations, the publication hook,
    `chunk_document` (one lease held across both passes, and the replay
    observation), and the real `_chunk_document_locked` run end to end on
    a one-paragraph document with a stand-in tokenizer and chunker. One more
    test is in `tests/test_output_publication.py`.
  - Baselines for these 37 tests, with each `rag.py` loaded whole:
    - Against the pre-change code, 33 fail. The 4 that pass are
      characterizations: the fixture shape, emission order alone admitting
      nothing, and `chunk_document` running one pass for a success and for a
      plain gate failure.
    - Against the fix-up-1 code, 20 fail: every test of the new replay
      interface, including the exact-`RuntimeError` checks. The 17 that pass
      are the recovery-proof tests, the heading-gate pin and the two one-pass
      `chunk_document` characterizations.
    - Against the fix-up-2 code, only the 2 replay-observation tests fail.
      The lease pin passes there, because that code already held the lease.
      It fails when the replay runs after the lease is released, when the
      replay re-acquires the lease, and when there is no lease at all.
  - Each of 39 mutants turns its intended test red. They cover the admission
    clauses, deferral, both text-layer proofs, the heading gate, the named
    violations, the publication hook and its sink, the locked pass's
    forwarding of violations, deferred groups and requests, the replay
    control in `chunk_document`, including its lease and its observation, and
    the exact gate error type.
  - On h03, `--run-report` records 6 events instead of 5. The extra event is
    the `chunk_order_replay` observation (1 violation), and the run succeeds.
    h18's report is unchanged: 5 events, no replay, `error_type`
    `RuntimeError`.
- **Open question (owner).** h03 still carries a gate-silent candidate
  disorder at p4 [#33,#34] (record 10), where a citation's reporter precedes
  its case name. Publish h03 with it, as with h04's accepted scrambles, or
  hold it for a broader fix?
- **Follow-ups (not fixed).**
  - Candidate gate-silent paragraph scrambles, found by a contiguity heuristic
    and not confirmed:
    - 32 multi-block groups in 14 READY h26 runs, plus tort law p361;
    - h13 p5 may be side-by-side text, and h01 p7's lexeme multisets differ.

    `same_page_reading_order` creates no edge between vertically overlapping
    boxes, so no gate sees these candidates and none replays.
  - An admitted group publishes the text layer's text, so the text layer's
    punctuation and case replace Docling's. The multiset proof compares
    NFKC-casefolded lexemes only. h03 still passes `source_token_fidelity`
    and `normalization_invariants`.
  - The architecture inventory drift now also covers the two new private
    replay helpers, the new keyword parameters of
    `_publish_corpus_quality_report_locked` and `_chunk_document_locked`, and
    one test patch target. It needs a reviewed `--refresh`.

### Integrated convergence

- **R1:** [PR #44](https://github.com/toddlar00/rag-pipeline/pull/44)
  merged on 2026-08-01 at validated head `ed2995e` (merge `2d8e4f9`).
- **R2 convergence:**
  [PR #76](https://github.com/toddlar00/rag-pipeline/pull/76) merged at
  `ddcbe89` (merge `471381c`), and the first policy-compliant PDF/Docling
  domain update merged through
  [PR #83](https://github.com/toddlar00/rag-pipeline/pull/83).
- Those merges close the old convergence state. They do **not** close the
  broader dependency, vulnerability, licensing, qualification, packaging, or
  release milestones.

### Phase A0

A0a is integrated. An earlier completed hosted A0b technical checkpoint binds
clean pre-gate source `4551c50970a07ac120d192802bcc692e39e3ece6` to
evidence head `443dce4`. Both hosted Windows and Linux Phase A0 jobs passed at
that exact head, and retained evidence artifacts were present when audited.
That pair remains historical evidence; see its
[exact-head evidence record](docs/evidence/2026-08-18-main-443dce4.md).

The completed Task 0.2 hosted checkpoint is evidence head
`aa7a23bbb0179beab001261da43529d670ee5720`, the direct gate-only child of
clean source `b07e3270881376cf60586c363e6285722562c7f5` (tree
`b287ef0452ffd9d35d7ec361d5e7a03b50f01258`). Its separate Windows and Linux
CPython 3.12.13 reports each cover the authoritative 9x5 scenario set, share
the exact source, lock, and scenario contract, and pass a local same-platform
comparison. Both hosted Phase A0 jobs passed at that exact head with retained
artifacts, the external exact-SHA review record is posted on
[PR #93](https://github.com/toddlar00/rag-pipeline/pull/93), and
history-preserving merge `d142065` integrated it on `main`; see the
[promotion evidence record](docs/evidence/2026-08-21-ci-promotion-seed-d142065.md).
The pre-activation hardening checkpoint `376750d` then changed Python test
source and the security-owned fixture, and its regenerated Windows/Linux
pair completed the same cycle: gate-only evidence child `f8fc95b` passed all
hosted force-full checks with retained Phase A0 artifacts, received the
external exact-SHA record on
[PR #94](https://github.com/toddlar00/rag-pipeline/pull/94), and merged
through history-preserving `f3bcb91`. That `376750d` pair supersedes the
`b07e327` pair as the completed hosted checkpoint; see the
[closure evidence record](docs/evidence/2026-08-21-task-0-2-closure-1e79540.md).
The one-domain vector-stores dependency checkpoint `0703dde` then changed
the four mapped core/full/service/smoke locks and completed the same
cycle: gate-only child `fa71ff3` passed the hosted full lane, received the
external exact-SHA record on
[PR #97](https://github.com/toddlar00/rag-pipeline/pull/97), and merged
through history-preserving `b3c7cf7`. The one-domain ML/runtime checkpoint
`6f65acb` completed the same cycle: gate-only child `78a99c2` passed the
hosted full lane, received the external exact-SHA record on
[PR #98](https://github.com/toddlar00/rag-pipeline/pull/98), and merged
through history-preserving `d27d85c`. The one-domain Service/UI checkpoint
`248c57a` completed the same cycle: gate-only child `4945878` passed the
hosted full lane, received the external exact-SHA record on
[PR #99](https://github.com/toddlar00/rag-pipeline/pull/99), and merged
through history-preserving `f1dae3b`. The one-domain test-audit tooling
checkpoint `ca3886c` completed the same cycle (moving the pinned lock
resolver to uv 0.12.5): gate-only child `a129f37` passed the hosted full
lane, received the external exact-SHA record on
[PR #100](https://github.com/toddlar00/rag-pipeline/pull/100), and merged
through history-preserving `0ec2639`. The one-domain PDF/Docling
checkpoint `ed1f370` completed the same cycle: gate-only child `33c61ff`
passed the hosted full lane, received the external exact-SHA record on
[PR #101](https://github.com/toddlar00/rag-pipeline/pull/101), and merged
through history-preserving `39f6c9e`, completing the Dependabot
supersession queue. The Task 0.6 Node supply-chain ownership checkpoint
`5f45757` completed the same cycle: gate-only child `344e873` passed the
hosted full lane, received the external exact-SHA record on
[PR #102](https://github.com/toddlar00/rag-pipeline/pull/102), and merged
through history-preserving `893c4a0`. The Task 0.7 secret-scan checkpoint
`e055bd0` completed the same cycle: gate-only child `32153e9` passed the
hosted full lane, received the external exact-SHA record on
[PR #106](https://github.com/toddlar00/rag-pipeline/pull/106), and merged
through history-preserving `376277c`. The Task 0.8 static-security
checkpoint `8891e1b` has since changed Python gate source and workflows
(no dependency or model lock), so its regenerated Windows/Linux pair
(independent local same-platform comparisons passed) supersedes the
`e055bd0` pair as the current candidate; its hosted checks and external
exact-SHA record are pending on the Task 0.8 pull request.

Two operational follow-ups from the post-merge `main` push runs are open:
a documentation-only merge passes its fast-lane pull-request run but then
fails the forced-heavy `main` push run's ancestor-bound Phase A0 delta
check until the next source checkpoint supersedes the baseline (observed at
`48b47fd`; cured by the `0703dde` checkpoint; a fix path — extending the
gate-only allowed set or classifier-aware push handling — is a
security-owned workflow change requiring its own review); and
`test_posix_escalation_kills_descendant_that_ignores_sigterm` showed one
cleanup-confirmation flake on a busy hosted runner at `b3c7cf7` (tree
identical to the fully green pull-request run; rerun requested).

No submitted exact-head human review or separate owner authorization for the
R8c-6 ownership move was found. Technical A0 success is therefore not that
authorization. Any later source or lock checkpoint must follow the maintained
[Phase A0 policy](docs/architecture/decisions/phase-a0-benchmark-policy.md)
rather than reusing a report for a different source identity.

### Security and dependency state

At `443dce4`, the CI and dependency-compatibility workflows passed, while the
supply-chain workflow failed both audit jobs. It reported unaccepted
`aiohttp`, `cryptography`, and `h2` advisories. Existing Chroma, Torch,
Torchvision, PyMuPDF, and FlagEmbedding policy records are time-bounded through
2026-08-31.

The validated remediation branch `agent/transitive-advisory-locks` is parked at
`1138398`, has no pull request or hosted checks, and is seven commits behind
`main`. Its four-lock delta is transitive-only, which the maintained
[dependency-domain policy](docs/architecture/decisions/dependency-compatibility-domains.md)
rejects. Do not merge it until the owner selects and the repository first
implements the applicable policy path.

### Release posture

There is no release candidate and no authorization to tag, publish a package,
or advertise a production support tier. The currently enforced default
security posture remains local-only, loopback-only, trusted single-user
operation with reviewed local model artifacts. Existing cloud/provider paths
and three locked remote-model-code bundles are capabilities, not
release-qualified support.

## Owner decisions and fail-closed interim rules

| Decision | Current state | Interim rule until an owner records a decision |
| --- | --- | --- |
| Private-source documentation and evaluation metadata ([issue #45](https://github.com/toddlar00/rag-pipeline/issues/45)) | Neither strict non-derivation nor controlled private evaluation metadata is approved | Introduce no new private-source excerpt or private-derived artifact class. Do not add or expand tracked/retained copies of authored private-corpus queries, judgments, or owner diagnostics. Existing occurrences await the selected data-class audit and are not precedent. Use generated/CC0 fixtures and content-free receipts. |
| R0C cloud/provider and remote-model-code support ([issue #48](https://github.com/toddlar00/rag-pipeline/issues/48)) | Owner support tier is unresolved | Treat these paths as **unsupported and unqualified for release**. Keep the release default local-only. This fail-closed posture is not an inferred permanent owner decision. |
| Transitive advisory remediation | Direct promotion, a reviewer-bound exception mechanism, or narrow time-boxed acceptances remain available under the ADR | Select none automatically. Keep the lock branch parked and the supply-chain gate red until the chosen path is reviewed and implemented. |
| PyMuPDF distribution basis and repository code license | No approved distribution basis and no repository license decision are recorded | Private filesystem-local evaluation only; no package or release publication. |
| Private retrieval/answer qualification | Corpus-owner judgments, family aliases, floors, and promotion statistics remain unresolved | No production-qualified corpus or generalized quality claim. Portable generated/CC0 suites validate mechanics only. |
| First version and platform/support tiers | Not selected | Development-only identity; no release tag or Tier-1 claim. |

Owner decisions are gates, not checkboxes an implementation agent may infer.
A technical change may prepare a bounded decision mechanism, but it must pause
before choosing or claiming the owner's result.

## Current work authorization

The archived “Immediate change freeze” described a pre-R1 repository state and
is superseded by this table. Superseding stale wording does not silently
authorize broader work. Anything not allowed below remains held.

| Work | May start now? | Gate |
| --- | --- | --- |
| Task 0.1 status/evidence reconciliation | Yes; ongoing documentation duty | Documentation and content-free evidence only; do not select an owner policy |
| Task 0.2 trusted CI promotion classifier | Complete (seed #93, hardening #94, activation #95) | Base-branch-trusted evaluation, fail-closed heavy selection, and exact-SHA aggregate/manual promotion evidence — all live on `main` |
| Tasks 0.3-0.4 transitive policy and lock remediation | Decision preparation only | Owner selects the ADR path before its mechanism or lock delta is integrated |
| Task 0.5 licensing/distribution | Owner decision records only | Both dispositions must be approved before packaging eligibility |
| Tasks 0.6-0.8 Node, secret, and static-security gates | Yes in sequence after their prerequisites | Pinned tools, synthetic canaries, redaction, security ownership, and no private source retention |
| Phase 1 installable packaging/product work | No | All Phase 0 gates plus explicit product-work and distribution authorization |
| Phase 2 private qualification and release | No | Private-source choice, owner-reviewed labels/floors, licensing, packaging, and exact-head release gates |
| Broader R7 coverage/typing/lint and A1 performance work | No | Recorded authorization after the preceding release-readiness gates in the active plan |
| R8c-6/R9 ownership and decomposition | No | Recorded owner/reviewer, current guard evidence, A1 prerequisite for the affected slice, bounded manifest, and tested rollback |
| Deferred run catalog, unified commands, observability, retrieval explanations, and experiments | No | Reach their ordered phase and satisfy its privacy, qualification, and compatibility gates |

Decision-neutral, release-blocking Phase 0 work may use synthetic/CC0 inputs.
Each owner-gated stream pauses at its decision boundary. A source-changing gate
slice refreshes the architecture inventory and, where required by the Phase A0
policy, establishes a new source/evidence pair.

## Active execution order

The detailed checklists and adversarial acceptance cases live in the
[next improvement program](docs/superpowers/plans/2026-08-18-next-improvement-program.md).
Execute it in this order:

| Sequence | Outcome | Entry condition | Exit condition |
| ---: | --- | --- | --- |
| 0.1 | One live roadmap and append-only evidence ledger | Current docs-only slice | No contradictory live status; old history preserved; owner gates explicit; no new private-derived content |
| 0.2 | Deterministic CI promotion | Task 0.1 mechanical acceptance | Trusted, rename-aware risk decision; required heavy jobs cannot be self-skipped; aggregate result is exact-SHA promotable |
| 0.3-0.5 | Valid dependency and distribution policy | Relevant owner decisions | Reviewed transitive mechanism/path; urgent advisories resolved or narrowly accepted; license/distribution decisions enforced |
| 0.6-0.8 | Complete repository supply-chain ownership | CI promotion gate | Node audit/SBOM, full-tree secret scan, and narrow Python static scan pass with pinned tools and redacted artifacts |
| Phase 1 | Installable development candidate | All Phase 0 exits and product-work authorization | Deterministic privacy-safe wheel, stable commands, version/schema registry, executable docs, external-cwd child-role tests |
| Phase 2 | Qualified first release | Packaging candidate plus owner/private-source decisions | Frozen 24-cell evidence, distinct readiness/qualification states, owner-approved statistical promotion, signed exact-head release record |
| Phase 3 | Risk-based guard and A1 capacity evidence | First release and recorded authorization | Branch/subprocess coverage, typed safety leaves, warning/lint ratchets, and stable platform-specific performance budgets |
| Phase 4 | Safe runtime ownership and decomposition | Phase 3 evidence plus explicit R8/R9 authorization | Characterized facade, bounded ownership slices, privacy-safe service telemetry, per-slice rollback and exact evidence |
| Deferred | Product simplification and retrieval experiments | Phase 4 and applicable qualification gates | Each product/quality change proves its own privacy, usability, and promotion contract |

Task 0.2's seed evidence head `E`
(`aa7a23bbb0179beab001261da43529d670ee5720`, direct gate-only child of clean
source `S = b07e3270881376cf60586c363e6285722562c7f5`) passed all hosted
force-full checks at its exact head, received the external exact-SHA review
record on [PR #93](https://github.com/toddlar00/rag-pipeline/pull/93), and was
integrated by history-preserving merge
`d142065cdcc890e2ad64057e0c9f627a079f93d6`; see the
[promotion evidence record](docs/evidence/2026-08-21-ci-promotion-seed-d142065.md).
The live CI workflow is the reviewed force-full bootstrap rendering, and the
future active workflow remains preserved as a security-owned fixture.
Task 0.2 is complete. The hardening checkpoint (executable fixture-validator
and event-identity coverage, the multiline-output guard, and the refreshed
`376750d` Phase A0 pair) merged through
[PR #94](https://github.com/toddlar00/rag-pipeline/pull/94) at `f3bcb91`,
and the workflow-only activation checkpoint merged through
[PR #95](https://github.com/toddlar00/rag-pipeline/pull/95) at `1e79540`
after its hosted activation run: the trusted classifier routed the change to
the full lane, all eight execution jobs tested synthetic candidate
`18caf7d0885c483da5e3096f2cc112e4cf55f2d3`, and the promotion aggregate
accepted that exact candidate. Classification is live; every execution job
tests the bound candidate; the aggregate is exact-SHA promotable. F4
(workflow-syntax validator anchor/alias rejection) remains an open
checker-hardening follow-up recorded in the promotion evidence record.
The next actions, in order: supersede the open Dependabot group PRs with
ordered, policy-compliant one-domain PRs — each with regenerated locks,
installed-lock testing, domain gates, and its own Phase A0 source/evidence
pair. The vector-stores supersession of PR #90 merged through
[PR #97](https://github.com/toddlar00/rag-pipeline/pull/97) at `b3c7cf7`
(qdrant-client 1.19.0, onnxruntime 1.29.0 for CPython 3.11+; the proposed
chromadb floor bump was dropped as a no-op the domain policy rejects). The
ML/runtime supersession of PR #92 merged through
[PR #98](https://github.com/toddlar00/rag-pipeline/pull/98) at `d27d85c`
(tqdm 4.70.0, numpy 2.5.2 for CPython 3.12+, sentence-transformers 5.7.0;
the einops and FlagEmbedding floor bumps were dropped as no-ops). The
Service/UI supersession of PR #87 merged through
[PR #99](https://github.com/toddlar00/rag-pipeline/pull/99) at `f1dae3b`
(fastapi 0.141.1, uvicorn 0.52.4 — deliberately past Dependabot's stale
0.52.3 target — and gradio 6.25.0). The test-audit supersession of PR #89
merged through
[PR #100](https://github.com/toddlar00/rag-pipeline/pull/100) at `0ec2639`
(uv 0.12.5, pip 26.2.1, ruff 0.16.3). The PDF/Docling supersession of
[PR #91](https://github.com/toddlar00/rag-pipeline/pull/91) is in flight
at checkpoint `ed1f370` (PyMuPDF 1.28.2, docling 2.121.0, docling-core
2.92.0, pypdfium2 5.13.0), completing the Dependabot queue. Task 0.6
merged through
[PR #102](https://github.com/toddlar00/rag-pipeline/pull/102) at
`893c4a0`: the npm ecosystem is Dependabot-managed, the exact
`package-lock.json` is audited against the GHSA-keyed expiring
`node-vulnerability-policy.json` with a content-free identity envelope,
and a normalized byte-stable CycloneDX SBOM is retained. Task 0.7 merged through
[PR #106](https://github.com/toddlar00/rag-pipeline/pull/106) at
`376277c`: the repository-owned redaction-by-construction secret scanner
covers every pull request (unfiltered workflow) and the full reachable
history (first hosted history scan passed; the entire 1,450-blob history
verified credential-free), with suppression only through narrow expiring
records and follow-ups F1-F3 (filename-echo channel, documented value-gate
bounds, unquoted-format coverage) recorded on the PR for a hardening
slice. Task 0.8's static-security slice is now in flight: the hash-locked
ruff 0.16.3 runs a reviewed thirteen-rule flake8-bandit blocking subset
through `tools/check_static_security.py` and the dedicated unfiltered
`.github/workflows/static-security.yml` workflow, with declared test-scope
exemptions for deliberate rejection-path constructs, content-free
rule/path/line findings, and exactly one counted expiring suppression
(the non-cryptographic MD5 sparse-vector token index in
`retrieval_core.py`, whose replacement is a separate versioned
index-migration follow-up). This completes the Task 0.6-0.8 gate
sequence, while
[PR #88](https://github.com/toddlar00/rag-pipeline/pull/88) (Google GenAI 2)
stays parked on its recorded owner decision; then begin Task 0.6's Node
audit/SBOM gate. Task 0.3's activation prerequisite is now met, but it
still stops immediately for the transitive-policy owner choice, which
remains unrecorded.

## Acceptance gates by phase

### Phase 0: gate trust

- CI classification runs only base-branch-trusted evaluator code/data, uses
  exact event SHAs and a NUL-delimited rename-aware diff, and sends unknown or
  policy/workflow changes to the full lane. Documentation-only fast decisions
  require verified regular Git blobs; type changes, links, gitlinks, and
  unverifiable modes run full.
- The trusted files land first under the exact force-full seed topology. Only a
  later workflow-only checkpoint may activate classification, after which all
  execution jobs test the bound synthetic-merge or merge-group candidate rather
  than an unmerged pull-request head.
- An `if: always()` aggregate inspects every required job result and fails on
  failure, cancellation, timeout, classifier failure, or unexpected skip. If
  repository rulesets remain unavailable, an exact-SHA manual or merge-queue
  record substitutes for no required check.
- Dependency exceptions are narrow, reviewer-bound, expiring, and incapable of
  hiding direct or unrelated package changes.
- Node manifests and every scanner input/workflow are security-owned. Secret
  and static scans use exact tool pins, synthetic positive/negative fixtures,
  full tracked-tree PR scope, redacted console/artifacts, and no open-ended
  suppression baseline.
- Deterministic SBOM bytes bind normalized inputs/tool/build epoch. Live
  vulnerability conclusions instead record scanner, scan time, and advisory
  database identity.

### Phase 1: installable candidate

- One development version appears consistently in Python, CLI, service, and
  package metadata without coupling product and artifact-schema versions.
- Wheels are built from clean sources with a normalized epoch, contain only an
  allowlisted inventory, and are installed with `--no-index` from
  hash-verified inputs (or exact preinstalled locks plus `--no-deps`).
- Windows and Linux each reproduce their own wheel bytes; cross-OS inventory
  and metadata agree. Entry points and every physical worker role run from a
  working directory outside the checkout.
- Executable operator docs contain no repository-only command dependency and
  no private path, credential, or generated content.

### Phase 2: qualification and release

- Structure-ready, retrieval-qualified, grounded-answer-qualified, and
  production-qualified remain separate states with exact corpus, profile,
  model, index, label, owner, expiry, and invalidation identities.
- The owner freezes one label generation before inspecting calibration. A
  predeclared estimator/test, interval or alpha, multiplicity correction,
  minimum effect/effective sample size or power, tie policy, and explicit
  non-promoting `inconclusive` outcome govern promotion.
- Every release gate binds one clean commit/tree, locks, runtime/platform,
  schema registry, migration/rollback evidence, licenses, security results,
  qualification state, and signed review. No tag or publication precedes it.

### Phase 3: guardrails and capacity

- Coverage includes fresh subprocess branches through a test-only activation
  that production/release workers reject. Reports use relative paths and omit
  source-bearing HTML or absolute paths.
- Warning fingerprints use category, owning distribution/module, and stable
  message with separate OS/Python budgets. Unknown first-party warnings fail
  immediately; known third-party counts may only ratchet downward.
- Timing gates activate only after the named number of independent Tier-1 runs
  demonstrates the predeclared percentile, outlier, variability, and
  platform-specific budget formula. Dirty, partial, non-finite, wrong-lock, or
  wrong-platform reports fail closed.

### Phase 4: safe evolution

- Characterization freezes public names, signatures, mutation seams, import
  order, CLI/worker routing, artifacts, errors, and cleanup precedence before
  ownership moves.
- Every slice names an owner/reviewer, source commit/tree, bounded files and
  capabilities, rollback procedure/triggers, and exact acceptance evidence.
- Service observability, when reached, remains local first-party telemetry with
  no exporter/network egress. It uses a private bounded sink, template routes,
  capped cardinality/retention, authenticated access, redaction canaries, and
  explicit sink-failure semantics. Correlation IDs are never metric labels.

## Integrated foundation

The following foundation is already on `main` and must be preserved:

- deterministic ingestion, chunk/publication evidence, hybrid retrieval,
  evaluation, Chroma/Qdrant lifecycle controls, CLI/UI, durable jobs, and the
  authenticated loopback service;
- R0A/R0B endpoint, credential, model-artifact, egress, local-UI, and transport
  controls;
- architecture/facade inventory, CI-security ownership, dependency domains,
  process supervision, service/job/evaluation composition seams, and Phase A0;
- table-family and context-aware retrieval correctness, portable generated/CC0
  evaluation suites, evidence-grounded answer checks, and private calibration
  mechanics whose owner decisions remain open; and
- historical PR #1-#83 implementation and validation narratives, preserved in
  the [roadmap evidence snapshot](docs/evidence/roadmap-through-2026-08-18-443dce4.md).

“Integrated” means present on `main`; it does not by itself mean
release-qualified, owner-approved, or supported for remote/multi-user use.

## Enduring constraints

- Preserve `rag.py` public names, mutation seams, CLI behavior, artifact bytes,
  schemas, error precedence, and process-isolation contracts unless a task
  explicitly versions and tests a behavior change.
- Keep the service and UI literal-loopback-only. Remote or multi-user operation
  requires a separate threat model, authentication/authorization, TLS/proxy,
  rate/tenant isolation, audit retention, and distribution approval.
- Treat workers/extensions as trusted code. Process supervision is not a
  sandbox.
- Change one dependency compatibility domain per pull request. Do not hand-edit
  generated locks or combine a policy introduction with the dependency delta
  that consumes it.
- Give every persisted artifact a version, strict bounds, canonical
  serialization, atomic publication, privacy classification, and malformed,
  stale, and future-version tests.
- Use characterization and differential tests before moving established code.
  Keep refactors separate from discovered behavior fixes.
- Do not claim semantic entailment, production corpus quality, secure erasure,
  public-network safety, or a support tier beyond the evidence actually
  reviewed.

## Volatile architecture metrics

Do not copy current module, function, binding, or hotspot counts into this live
roadmap. The machine-readable authority is
[`architecture-inventory.json`](architecture-inventory.json). Validate it and
print the current aggregate summary with:

```console
python tools/check_architecture_inventory.py
```

Point-in-time counts belong in a commit/tree-bound
[evidence record](docs/evidence/2026-08-18-main-443dce4.md). A richer generated
hotspot summary is deferred until a source-changing checkpoint is authorized;
adding that Python tool now would itself invalidate the paired source baseline.

## Non-goals

- Do not infer owner approvals, fabricate relevance judgments, or derive
  thresholds from a small portable suite.
- Do not merge broad dependency upgrades, extend exceptions silently, or use a
  transitive-only lock change to bypass the domain policy.
- Do not use project-wide typing, a vanity coverage percentage, or a permanent
  suppression inventory as a proxy for risk reduction.
- Do not perform a broad `rag.py` rewrite before the ordered guard,
  characterization, ownership, and rollback gates.
- Do not rewrite Git history, delete provenance, publish a release, or expose a
  listener beyond the approved local boundary without the corresponding owner
  decision.

## Completion rule

A technical milestone is complete only when its behavior is implemented,
failure-injected, covered by proportionate tests and static checks, exercised
against relevant real optional clients where practical, independently
reviewed, and bound to an exact commit/tree. Integration additionally requires
a merge or an owner-signed exact-SHA manual promotion record.

Release readiness is separate. It also requires every applicable privacy,
corpus-owner, dependency, vulnerability, license, migration, rollback,
platform, and publication gate. A green implementation or hosted workflow
cannot satisfy an unresolved human decision.
