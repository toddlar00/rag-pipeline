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
remains active on a draft-PR branch; it does not supersede the integrated
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

### Casebook excerpts and source-order fixes (2026-09-25, draft PR, not integrated)

A private 18-excerpt casebook course corpus (page-bounded scans with no table
of contents) exposed these gaps. The work is on a draft-PR branch only;
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
  (now a typed `decorative_glyph` exclusion under quality schema 13, see
  "Decorative square glyph exclusion" below); boundary merge
  joins a bullet list line inline (still true for every document that passes;
  a join that fails `source_token_fidelity` is now separated by a one-time
  replay unless the order replay takes precedence, see "Bullet list after a
  lead-in at boundary merge" below); the token splitter breaks after `v.`;
  duplicate-line allowances do not cover multi-item entries; the architecture
  inventory was refreshed with review in `b8fd9cc` and `0e399d2`.

### Casebook supplement, audit scope and OCR findings (2026-09-26, draft PR, not integrated)

Same status as the section above: draft-PR branch only, no completed hosted
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
  clean OCR words into letter-spaced layer tokens (h16 p19; retried from the
  content stream when the result fails the gate, see "Letter-spaced text-layer
  retry" below); relaxing the
  one-native-block recovery gate (h03) would also rebuild h18's p20 pair from
  its misread layer (a naive relaxation; the gate-driven one implemented
  under "Multi-block overlapping-group recovery" below leaves h18
  unchanged); quote-led cross-page footnote continuations are demoted
  into body flow (h17 p13); contents records keep a pre-swap raw
  `token_count`, `table_recovered_from_pdf` and case names, and split contents
  rows are caught only by the gate; a printed range across a page-label gap
  renders as one span (h07 `pp.535-559`).

### OCR angle-classifier override (2026-09-26, draft PR, not integrated)

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
  - The flagged h04 has since been published end to end by a `full` job.
  - The architecture inventory was refreshed with review in `b8fd9cc`.

### Multi-block overlapping-group recovery (2026-09-26, draft PR, not integrated)

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
    one test patch target. It was refreshed with review in `b8fd9cc`.

### Bullet list after a lead-in at boundary merge (2026-09-27, draft PR, not integrated)

This has the same status as the sections above. It implements the
owner-approved h16 list-join fix (`#/texts/57`) as a gate-driven replay. No
schema or policy version changed.

- **Defect.** Boundary repair joins a same-heading sentence continuation
  inline, as `left + " " + right`. When the right record opens with a source
  list item's punctuation marker, for example a bullet after a lead-in that
  ends `for example,`, the marker lands mid-line. The fidelity audit accepts
  such a marker only at line start, so h16 fails `source_token_fidelity`
  (13700/13701, `#/texts/57` uncovered). A rule that separated every such join
  was refuted in review: it changed synthetic documents that pass every gate
  today, such as an ineligible opening bullet or a split item whose tail opens
  with an in-text dash.
- **Observation.** The first pass is the previous pipeline and only observes.
  It maps lineage items to their canonical punctuation marker
  (`_source_punctuation_list_marker`). It records a boundary in
  `inline_list_joins` only when all of these hold:
  - the right record's first lineage item has a marker;
  - the lstripped right body opens with that marker, followed by horizontal
    whitespace or its end (`_opening_list_marker_ref`);
  - the join is actually performed.

  Recording never changes output.
- **Trigger.** The quality-publication failure sink records a list request
  only when the first pass's published report fails `source_token_fidelity`
  and names a recorded item in `source_coverage_issues`
  (`_list_boundary_replay_refs`). The unchanged gate failure then propagates.
  No output-side issue is required: a marker is not a lexical token, so h16's
  `output_coverage_issues` is empty.
- **Replay.** `chunk_document` replays the chunk once, under the same lease
  and outside the failure's handler. It first records a
  `chunk_list_boundary_replay` observation whose `list_boundary_items` metric
  counts the named items, so run reports show a replay even when it fails.
  The replay reruns the whole pipeline. Only a boundary opened by a named
  item changes: it joins as a separate block (`left + "\n\n" + right`) and
  must still meet every other join condition, including the token limit. A
  separated join that does not fit leaves the records separate, which also
  keeps the marker at line start. The replay gets no sink, so it never
  replays again, and a failing replay raises the plain, unchained gate
  `RuntimeError`.
- **One dispatcher, h03 precedence (limitation).** A chunk replays at most
  once. When the same failed report also names a deferred native group,
  today's order replay runs verbatim and the list request is dropped, so a
  document that needs both repairs stays failing. This is fail-closed; no
  such document exists in h26 or in the review corpora. A combined replay is
  not provably failing-only: today's order-only replay may pass with the item
  still joined inline, because another line-start marker can cover it, and
  adding separations could change that passing output. Ruling this out would
  mean re-deriving the audit's token alignment over the records the order
  replay changes. Any future gate-driven replay (for example for the h16
  U+25A0 glyph or the p19 letter-spaced run) must join this dispatcher under
  the same at-most-one-replay and precedence rules. Stacking replays would
  break the failing-only argument.
- **Scope.** By construction, output changes only when the unchanged first
  pass fails `source_token_fidelity` on an observed item and its report names
  no deferred group. That first pass's failing output is today's final
  output, and today's run exits with the gate failure. All of the following
  ran the identical `rag.py` (`5625478f…`) over every earlier review corpus.
  The designer and one design reviewer each ran the 770-job sweep; that
  reviewer alone ran the other three checks. The other design reviewer ran
  the real runs, the mutants and a both-trigger probe. An implementation
  reviewer re-ran the three sweeps against the repo copy with the same counts,
  plus a 770-job order-trigger composition with no problems.
  - 770 synthetic jobs: all 529 that pass today are byte-identical, including
    182 that observe an inline join. The 213 list replays are all on
    documents that fail today.
  - 2,448 jobs at 13 more budgets and 688 jobs from 86 new documents: every
    document that passes today is identical.
  - A 200,000-case differential fuzz of the coalescer's first pass found no
    difference.
  - 36 documents with an injected order trigger: final artifacts identical.
- **h16.** The staged conversion, chunked against its staged PDF, replays
  once. Only record 14's text changes, from ` - ` to a blank line before the
  bullet, plus its `stable_id` and `source_fidelity` and its neighbours'
  linked ids. It still has 88 records, and oracles are identical.
  `source_token_fidelity` passes (13701/13701, no issues). h16 still fails
  `eligible_source_items_represented` (the U+25A0 glyph) and
  `normalization_invariants` (p19), so it is not READY. The output is
  byte-identical to the reviewed prototype's.
- **Preservation.** The 17 READY runs (h01-h15 with h03_3, h04_3 and h06_4,
  plus h17_3 and the Summer 2026 Update) re-chunk byte-identical to their
  publications: chunks, quality, oracles and chunking receipts. h03_3 and
  h03_2 still run the order replay once. h04_2 and h18_2 are byte-identical to
  the previous full-tree verification, and tort law's pre-gate records are
  identical (2,868).
- **Operations.** `rag.py full --resume` treats a failed chunk set as complete
  (`_chunks_complete` ignores the quality report). It then skips chunking, so
  no replay runs. The order replay behaves the same way. Re-chunk h16 without
  `--resume`. A replayed document is chunked twice, including HybridChunker,
  enrichment and any enabled LLM calls, and its warnings repeat. A replay
  killed part-way can leave a mixed artifact set, which the artifact bindings
  detect. Targeting uses rag's canonical marker, while the audit applies NFKC;
  a compatibility-form glyph may therefore go untargeted. That affects only
  whether a failing document gets fixed.
- **Evidence.**
  - `tests/test_list_boundary_characterization.py` (5 tests) passes on the
    pre-change code. It pins the default inline join, a period-ended lead-in,
    and three documents published inline in one pass: two ineligible opening
    bullets, and a split item whose tail opens with an in-text dash (real
    HybridChunker, word tokenizer, 44-token budget).
  - `tests/test_list_boundary_replay.py` (40 tests) pins the trigger, the
    publication hook, the dispatcher, the lease, the observation, the
    coalescer and the marker predicate. It also pins that a request carries
    only the named items when more joins were observed, that a multi-item
    request reaches the replay whole, that a pass that publishes never
    replays, and that a named item that is not the record's first lineage
    item does not separate. `tests/test_list_boundary_replay_locked.py`
    (7 tests) drives the real locked pass. It includes a real-audit case
    where both triggers fire, and a two-list document where both joins are
    observed but only the named one is separated; the other list's
    ineligible opening bullet stays inline. All 47 fail on the pre-change
    code; the behavioural failure is the propagated `source_token_fidelity`
    gate error. `tests/test_native_group_order_replay.py` gains one
    assertion.
  - Focused related suites (23 files): 1,064 passed, 4 skipped. The one
    failure, `test_guided_ocr_review_has_exact_inward_and_host_only_boundaries`,
    is unrelated: the dirty worktree's OCR guided-review modules are
    untracked. Ruff is clean.
  - Mutation: 61 mutants (the design's 24, a design reviewer's 17, an
    implementation reviewer's 19 and one for the dispatcher's success
    branch). 56 are killed. The 5 survivors are equivalent. One reads the
    whole record instead of its footnote-stripped body, and one drops a
    strip of already stripped cores. The other three are equivalent in
    production: the fidelity check is only ever `pass` or `fail`, report
    issues are always strings, and `chunk_document` always passes both
    replay sinks together.
  - The 6 wiring mutants are killed only by the end-to-end tests. Those tests
    `importorskip` `docling_core`, so in CI they run only in the
    full-integration lane.
- **Follow-ups (not fixed).**
  - The architecture inventory drift now also covers the two new private
    helpers, the new keyword parameters of `_coalesce_chunk_boundaries`,
    `_publish_quality_report_or_request_order_replay` and
    `_chunk_document_locked`, and the three new test modules and their patch
    targets. It was refreshed with review in `b8fd9cc`.
  - h16 is still blocked on the glyph fix. The p19 fix is in the next
    section; the glyph fix is in "Decorative square glyph exclusion" below.

### Letter-spaced text-layer retry (2026-09-27, draft PR, not integrated)

This has the same status as the sections above. It implements the
owner-approved h16 p19 fix (`#/texts/179`, record 49). No schema or policy
version changed. It is not a replay: it runs inside the first pass, so the
one-dispatcher rule above does not apply to it.

- **Defect.** h16 p19 lies in a scanner OCR text layer that places each glyph
  of three short words separately, with real space glyphs only between words.
  PyMuPDF's default words are then single letters, and native split-word
  repair re-splits Docling's text into isolated letters. Record 49 fails
  `normalization_invariants` (`ocr_gibberish`), and the `#/texts/179`
  `native_repair` oracle has 52 tokens.
- **Retry.** `_recover_native_text_repairs` computes every item's repair
  exactly as before. The per-item pipeline moved into
  `_repair_native_text_item`, verbatim except for two mechanical
  substitutions; its AST equals the reviewed prototype's. A retry is computed
  only when the caller passes `letter_spaced_retries` and the item passes
  `_letter_spaced_layer_retry_applies`: label `text` or `list_item`, body
  layer, exactly one provenance, its page outside the structural ranges, and
  today's final text matching quality's exact `ocr_gibberish` pattern. The
  pipeline then reruns over native words rebuilt from the content stream
  (`_content_stream_letter_words`, PyMuPDF `TEXT_INHIBIT_SPACES`). A run of
  same-line single ASCII-letter words merges only when:
  - exactly one stream word covers its first letter and spells exactly its
    letters;
  - every gap inside it is narrower than every stream-word gap on that line,
    so the line needs a second stream word;
  - one span style owns all of its letters (`_letter_run_has_uniform_style`):
    each letter's center lies in exactly one span that contains the letter,
    no owning span is flagged superscript, and the spans share one font, a
    size within max(0.75, 8%) and a baseline within 12% of the size. The
    baseline test compares span origins. MuPDF keeps a lowered letter in its
    line's span, so a lowered letter is measured only when its span splits
    (a font, size or color change); it still has to lie inside the one
    stream word that spells the run. Such a word bypasses the generic
    bbox-overlap style check, which leaks into the previous line because
    scanner word boxes are taller than the line pitch.

  The retry is recorded only when its text no longer matches the pattern and
  its concatenated lexemes equal today's (`_native_lexical_key`). Its edits
  are localized and deduplicated as today's are; the retry's own OCR
  fallback, an edit that recurs in the source, or a duplicate edit asks for
  a rebuild.
- **Exemptions.** `_letter_spaced_retry_exempt_refs`, computed once per
  document and only when an item passes the gate, excludes text that can
  publish where the detector does not apply: items related to a picture or
  table anywhere in their ancestry (parent, enclosing groups, or a caption,
  footnote or child relation), members of a synthetic aligned-list table
  (published as a pipe table), and running section banners (never
  published). This closes the direct-parent-only gap the design review found.
  Aligned-list membership depends only on geometry, so it is computed without
  overrides; chunking later drops layouts whose items are reserved, so the
  exempt set covers every published pipe-table member. The walk goes upward
  only: a text whose own child is a picture, or that shares a non-body group
  with one, is not exempt (the co-chunk residual below).
- **Commit.** `_recover_bound_source_enrichments` commits the retries
  (`_apply_letter_spaced_layer_retries`) after all four native group stages,
  so each stage decides on today's overrides. A ref named by a recovered group
  or by a deferred multi-block group is skipped: a group oracle replaces its
  members' overrides, and a deferred member keeps today's text in the first
  pass, so the retry cannot change whether the order replay runs. A failed
  native stage drops any partial retries. The committed rebuild flag is
  today's OR the retry's.
- **Scope.** A retried item is quality-eligible, and today it publishes its
  own override, because no group names it. A document can therefore change
  only if today it:
  - publishes the letter run in a gated record, failing
    `normalization_invariants`;
  - drops the item, failing `eligible_source_items_represented`; or
  - misses the oracle's letter tokens, failing `source_token_fidelity`.

  Residual, not decidable when enrichment runs:
  - a record typed `figure` or `table` by co-chunked content: a merged picture
    or table item, or pipe or tab lines from other text. The exposure exists:
    the July `output/Ethics_5` chunks have 141 body text refs co-chunked with
    `#/tables` items that the exemption does not cover (for example record
    1333, `#/texts/3479`). None of them trips the gate, so nothing changes;
    only the conjunction of such a record with a gated letter run is
    unobserved. A review scan of 28 conversions found no text with a picture
    child or sharing a non-body group with a picture. All 155 figure or table
    text refs in tort law are covered through the ancestry walk;
  - a chunk boundary that splits a four- or five-letter run into pieces
    shorter than four letters (not observed);
  - an exception in the retry path (the exemption set or the stream-word
    read) would fail an explicit-source chunk. That is not limited to failing
    documents: the gate reads an item's own repaired text, so it also admits
    items that pass today because a group replaces their text. h15
    `#/texts/32` is gated, and h15_3 is READY. None occurs in the verified
    corpus.
- **Correction.** The design cited h15 `#/texts/32` as the reason commits
  follow the group stages. That was wrong: its native words (a letter, a
  two-letter piece and a letter)
  hold no run of two single letters, so no retry is computed whether or not
  its group claims it. Commits follow the group stages because those stages
  must decide on today's overrides and a group oracle replaces its members'
  overrides.
- **h16.** The staged conversion, chunked against its staged PDF, replays
  once (list boundary) and now fails only `eligible_source_items_represented`
  (`#/texts/14`, the U+25A0 glyph). Against the list-replay output:
  - only record 49's text changes: a two-letter word and two function words,
    all letter-spaced, are rejoined. Its non-space characters are identical;
  - record 49's token counts, `stable_id`, `source_fidelity` and
    `#/texts/179` lineage hashes change with it. Records 48 and 50 change
    only their linked ids. There are still 88 records;
  - the `#/texts/179` oracle goes from 52 to 47 tokens and stays
    `native_repair`. No other oracle changes;
  - `normalization_invariants` passes, and `source_token_fidelity` stays at
    13701/13701.

  A repeat run is byte-identical.
- **Preservation.** The 17 READY runs (h01-h15 with h03_3, h04_3 and h06_4,
  plus h17_3 and the Summer 2026 Update) re-chunk byte-identical to their
  publications: chunks, quality, oracles and chunking receipts. h03_3 and
  h03_2 still run the order replay once. h18_2, h04_2 and h03_2 are
  byte-identical to the previous full-tree verification.
- **Evidence.**
  - A native-stage replay loaded the pre-change `rag.py` beside this one over
    31 bound conversions: Contracts_2/4/5, Criminal Law_2, Ethics and
    Ethics_2-5, tort law, and 21 h26 runs (every READY run plus h03_2, h04_2,
    h16_2 and h18_2). Default repairs, edits and rebuild refs are identical
    for all 31. With retries requested and no range or claim narrowing, the
    only retry is h16 `#/texts/179`. h04_2's five gated items and h15
    `#/texts/32` get none, because no letter run merges. Contracts' 27
    letter-spaced hits are headings or furniture, which the gate excludes.
    Tort law has no hit, so its output cannot change.
  - `tests/test_letter_spaced_text_layer.py` (47 tests: gate, exemptions,
    commit rules and production wiring) and
    `tests/test_letter_spaced_merge_proofs.py` (22 tests: the merge proofs)
    use synthetic PDFs built with PyMuPDF. On the pre-change code 61 of the
    69 fail. Most fail because the new interface is missing. The two wiring
    tests that commit a retry fail on the defect itself (the letter run). The
    8 that pass pin today's behaviour: the default output, including the OCR
    fallback's rebuild, a group claim at each of the four stages, a deferred
    member, and a failed native stage. The wiring tests drive the real
    `_recover_bound_source_enrichments` through a snapshot stand-in, as
    `tests/test_contents_outline_guards.py` does.
  - Review fix-up: the first mutation claim overstated what was pinned. An
    independent review left 15 of its 24 mutants and 13 of 14 more alive,
    among them the gate's input (today's text, not the source), the
    footnote and child relations, the retry's rebuild rule, the lexeme
    comparison with today's text, and the font, superscript, baseline,
    single-owner and single-word-line merge proofs. Tests now pin each of
    them, plus the `list_item` and `footnote` labels and a form-item
    ancestor. The review also found that the old letter test
    (`"A" <= upper() <= "Z"`) admitted non-ASCII letters whose upper case
    starts with A-Z, such as U+00DF; the test is now `isascii()` and
    `isalpha()` (RED first: only that case failed on the previous code). It
    removes merges, except where a stream word's box also covers a
    neighbouring non-ASCII letter that its text does not spell (not
    observed), and the retries of the three conversions with gated items
    (h04_2, h15_3, h16_2) are unchanged.
  - Second review fix-up (tests and this section only; no production code
    changed): the quantifier of the gap proof was not pinned. With
    `max(word_gaps)` in place of `min(word_gaps)` every test still passed,
    because the wide-gap fixture's word gaps are all equal, and on a
    justified line (one wide word gap, ordinary gaps elsewhere) the retry
    committed a glued two-word form that clears the gate with today's lexemes.
    A negative case whose inner gap lies between the line's narrowest and
    widest word gap now fails on that mutant and passes on the production
    code. Tests also now pin a key-value-item ancestor, the `caption` and
    `code` labels, digit and punctuation runs, a retry that adds an override
    today's repair lacks, today's rebuild flag surviving a retry that asks
    for none, the retry's own fallback rebuild, duplicate retry edits, and
    the merged word's box.
  - 149 mutants of the production code, not the prototype, ran on a private
    copy: the implementation's 34, the first reviews' 24 and 13 (the
    review's non-ASCII mutant is replaced by two on the new predicate), one
    that drops both owner checks together, and the second reviews' 48 and 27
    (several repeat earlier ones). 142 are killed. The 7 survivors are
    equivalent: recomputing the exemptions for every gated item changes
    only cost; a single-letter run is rejected by three
    redundant checks (run length, span length, non-empty letter gaps), so
    dropping either of the first two changes nothing; the same-line run
    check (two equivalent mutants) is redundant because a merged span must
    lie inside one stream word and spell exactly that word, and a stream
    word never crosses a MuPDF line, which PyMuPDF segments the same way with
    and without `TEXT_INHIBIT_SPACES` (argued, not tested); the retry's own
    gate needs no structural ranges, because the item already passed the
    gate with them; and retrying with the unmerged words when nothing merges
    reproduces today's failing text, which the commit rules reject.
  - After the fix-ups, the h16 target and all preservation runs re-chunk
    byte-identical to the results above. Focused related suites (17 files):
    958 passed. Ruff and `tools/check_python_sources.py` are clean.
- **Follow-ups (not fixed).**
  - The architecture inventory drift now also covers nine new private `rag`
    bindings (`_ContentStreamLetterWord`, `_LetterSpacedLayerRetry`,
    `_letter_run_has_uniform_style`, `_content_stream_letter_words`,
    `_letter_spaced_layer_retry_applies`, `_letter_spaced_retry_exempt_refs`,
    `_native_lexical_key`, `_repair_native_text_item`,
    `_apply_letter_spaced_layer_retries`), the two new keyword parameters of
    `_recover_native_text_repairs`, and, once tracked, the two new test
    modules and their patch targets. It was refreshed with review in `b8fd9cc`.
  - Footnote items are not retried; nothing in the corpus needs it.
  - The merge proofs depend on PyMuPDF 1.28.2's `TEXT_INHIBIT_SPACES` word
    segmentation and on its span splitting and superscript flag; the
    synthetic tests catch a change.
  - `_content_stream_letter_words` re-reads the page's stream words for each
    gated item; the corpus has seven gated items, so this is not cached.
  - Gate-silent text remains in h16: the p19 `Auer` reading-order scramble
    (records 49/50), the garbled p1 scanner layer (record 0) and many word
    splits from the text layer (for example `#/texts/173`). An owner decision
    is needed before h16 publishes at this text quality.

### Decorative square glyph exclusion (2026-09-27, draft PR, not integrated)

This has the same status as the sections above. It implements the
owner-approved h16 `#/texts/14` fix as a typed quality exclusion. It is the
owner-approved exception to the rule above: the quality report schema goes
from 12 to 13 and the index manifest schema from 9 to 10, with a legacy query
binding `(9, 12)`. No other schema or policy version changed. It is not a
replay, and chunking never calls the new predicate, so no chunk output can
change.

- **Defect.** h16 `#/texts/14` is a `text` item made only of U+25A0 in the
  page 2 margin. Normalization removes square-only lines, so its prepared
  chunk is empty and is dropped with its lineage. Quality kept the item
  eligible, so `eligible_source_items_represented` failed (`missing_refs`
  [`#/texts/14`]). h14 `#/texts/24` passed only because its square shared a
  chunk with other text.
- **Exclusion.** `chunking_core.normalization_erases_decorative_squares`
  mirrors `_normalize_text` for a whole text. After the no-break-space rule,
  every line must be blank or hold only U+25A0 with spaces or tabs, and at
  least one line must hold a square. It is public, like the banner and
  misclassified-heading predicates that quality already calls.
  `quality_core._source_exclusion_reason` returns `decorative_glyph` for a
  `text` item whose raw `text` (else `orig`) matches, right after the
  section-marker branch. A ref bound by a trusted source oracle stays
  eligible: `source_inventory` takes `source_oracle_refs`, and both the
  report build and the source-backed validation pass the validated registry
  refs. A registry-bound repair replaces the text, so it is emitted and keeps
  its oracle-token coverage.
  - The inventory tests the unstripped text, as the chunker sees it. Python's
    `str.strip()` also removes em spaces, ideographic spaces and carriage
    returns, which the square rule keeps, so testing stripped text would
    exclude squares the chunker emits.
  - Still eligible, so they fail closed as today: a square next to any other
    character (U+25A1, a list marker, an em or ideographic space, a carriage
    return); text that is erased only with help from another rule (U+200B,
    U+FFFD, an A-J marker line, a bare digit line); square-only `list_item`,
    `caption` and `section_header` items.
  - Precedence is unchanged: every earlier reason still wins.
  - Scope: across the design's 41 and its review's 67 Docling JSONs, the
    exclusion matches only h14_3 `#/texts/24`, h16_2 `#/texts/14` and four
    items in the July `Criminal Law_2` conversion (its report is schema 5 and
    already stale). Tort law has no U+25A0.
- **Versions (owner decision).**
  - `QUALITY_REPORT_SCHEMA_VERSION` 12 -> 13. Without the bump, the stored h14
    report would fail source-backed validation with a misleading "fidelity
    does not match source", while every other v12 report stayed valid.
  - `INDEX_MANIFEST_SCHEMA_VERSION` 9 -> 10, and `(9, 12)` is appended to
    `_LEGACY_QUERY_SCHEMA_BINDINGS` and `_CONTEXT_QUERY_SCHEMA_BINDINGS`.
    Manifest 10 keeps manifest 9's payload; only the quality binding moves.
  - `_CURRENT_PAYLOAD_LEGACY_MANIFEST_VERSIONS = (9,)` keeps every query
    check a manifest-9 index had while current. That covers the
    embedding-input policy and table-child checks
    (`index_state._query_manifest_dimension_impl(current_payload_schema_versions=...)`)
    and its table-family depth and collapse (`_indexed_table_child_count`).
    The legacy branch would otherwise skip them, in ordinary and context
    queries alike. None of the 18 private manifest-9 indexes (the 17 READY
    h26 runs and tort law) has table children, but the Git-ignored
    synthetic smoke corpora `evaluation-reports/ai-evidence-native-smoke-v1`
    and `-v2` hold Qdrant indexes at manifest 9, bound to quality 12, with 4
    table children each. They are historical evidence that no test reads;
    without this list they would lose that depth and collapse.
  - The context binding matters only for a manifest-9 index with a null
    quality binding (a corpus outside the quality contract), which keeps
    answering context queries. Context assembly validates the adjacent quality
    report first, and it must now be v13, so a v12-bound corpus is refused
    before the manifest is read. The 7 -> 8 bump set the precedent with
    `(7, 3)`. The "old manifest" case in `tests/test_index_state.py` now uses
    8, the newest version with no binding.
- **Consequences.** The change is forward-only: code from before it rejects
  v13 reports and manifest 10.
  - Every stored v12 report is rejected ("unsupported corpus quality report
    schema"). The 17 READY h26 receipts pin quality 12 and manifest 9, so
    `info` shows them STALE, and AI project export and publication refuse
    them until each run is rebuilt.
  - `full --resume` keeps the conversion and chunk receipts, which bind no
    quality, and regenerates the v13 report. It then rebuilds the whole
    collection, because a manifest-9 index is no longer reusable
    (`schema_version changed (9 -> 10)`). That is a full re-embed. Last, it
    republishes the receipt. h16 still needs a fresh chunk without `--resume`
    (see the list-replay operations above).
  - Ordinary queries on a manifest-9 index bound to quality 12 answer as
    before: CLI `query`, `search_index` without context, and ordinary service
    search. Context-window queries, corpus-pinned evaluation (its index check
    is current-only) and publication refuse them.
  - For the 17 READY h26 runs (h01-h15 with h03_3, h04_3 and h06_4, plus
    h17_3 and the Summer 2026 Update) that refusal is new. A context-window
    query on a scratch copy of h14 returned 5 hits with the pre-change code
    and now fails with "unsupported corpus quality report schema"; the other
    16 carry the same quality-12 binding. Their rebuild restores it. Tort
    law's context-window query already failed (below).
  - The service evidence companion stays current-only (exact manifest match
    and a current quality proof). It now refuses manifest-9 indexes, as it
    already refuses `(6, 2)` and `(7, 3)`. The service is Qdrant-only. The
    only Qdrant indexes found here (h26 data, `output/` and this repository)
    are the two synthetic smoke corpora above, which it now refuses. No
    private corpus is served.
- **Tort law.** `output/tort law` is a Chroma index, manifest 9 bound to
  quality 12, with 2,721 records, no table children and embedding policy 1.
  - The same four queries ran once with the pre-edit code snapshot and once
    with this tree, one process at a time (`rag.py query`, local-only,
    cache-only, `nomic-ai/nomic-embed-text-v2-moe`, `-n 10`). Auto (hybrid),
    `--vector-only --no-rerank` and `--hybrid --no-rerank` return identical
    parsed JSON: the same ranked stable ids, scores and modes. The bytes
    differ only in metadata key order. `--context-window 1` fails before and
    after with the same error (`chunk completion header or inputs are
    invalid`), because its July chunk receipt is stale.
  - The service evidence search never admitted it, before or after:
    `CorpusConfig` refuses the Chroma backend, a Qdrant configuration
    conflicts with the manifest's backend, and its quality proof fails on the
    same stale chunk receipt.
  - It was already STALE (schema-2 conversion manifest, split-URL structural
    failure). Its quality report and index binding are now stale as well, so
    publishing it again needs a full reconversion, a v13 report and a full
    re-embed, then a new NotebookLM export.
  - These queries ran against the live `output/tort law` index. Every
    `rag.py query` rewrites the bytes of Chroma's `chroma.sqlite3` (same
    size), with the pre-edit code too; one more snapshot query confirmed it.
    The index manifest, chunks, report and every other file are unchanged.
    Future smokes should query scratch copies, as the review did.
- **h16.** The staged conversion, chunked against its staged PDF, replays
  once (list boundary) and exits 0. All 31 checks pass at quality schema 13,
  with 88 records. Chunks, oracles and the chunking receipt are
  byte-identical to the letter-spaced fix-up 2 run, the tree just before this
  change. The report changes only here:
  - status goes from fail to pass, and so does
    `eligible_source_items_represented` (observed 1 -> 0);
  - eligible items go from 169 to 168, `coverage_ppm` from 994082 to 1000000,
    and `missing_refs` from [`#/texts/14`] to [];
  - `decorative_glyph: 1` is added, and the fidelity
    `source_descriptor_root_sha256` and `evidence_sha256` change.

  The gates now pass. The gate-silent text noted in the previous section still
  needs an owner decision before h16 publishes.
- **Preservation.** The 17 READY runs (h01-h15 with h03_3, h04_3 and h06_4,
  plus h17_3 and the Summer 2026 Update) were re-chunked against their
  labeled PDFs. Chunks, oracles and chunking receipts are byte-identical to
  their publications. The reports differ only in `schema_version`, except
  h14, which still passes: eligible and represented items go from 80 to 79,
  `decorative_glyph: 1` is added, and the fidelity descriptor-root and
  evidence digests change. h03_3 still replays the order recovery once.
  h18_2, h03_2 and h04_2 match the letter-spaced fix-up 2 run except for
  `schema_version`. h18 still fails only `normalization_invariants` and
  stays blocked.
- **Evidence.**
  - `tests/test_decorative_glyph_exclusion.py` had 32 tests in the first
    round. On the pre-change tree 26 failed, 21 of them because the predicate
    is missing. The 6 that passed are three emitted-square
    characterizations, the previous-schema rejection, the no-key case and the
    registry-bound report test. The erased-only test also asserts that each
    item stays eligible. Invisible and look-alike characters are written as
    `\u` escapes.
  - Review fix-up 1 made it 37 tests. The predicate table now has 20 cases
    (7 erased, 13 kept), and each case also runs `source_inventory`: the
    item is `decorative_glyph` exactly when normalization erases it, and
    otherwise stays eligible unless it is blank. A new exhaustive test does
    the same for all 11,110 texts of length 1-4 over a ten-symbol alphabet,
    and the typed-inventory test gains an em space before a square and a
    square before CRLF. They kill the mutant that passes the stripped text
    to the predicate (7 failures). The first-round files, with
    `tests/test_quality_core.py` and `tests/test_index_state.py` (166
    tests), let it survive.
  - The registry-bound build-and-validate test binds a square-only item to a
    `native_repair` oracle. It fails when the build call site drops
    `source_oracle_refs` (eligible 1, not 2), when the validation call site
    drops it ("fidelity does not match source"), and with the unguarded
    predicate.
  - `tests/test_quality_v13_query_binding.py` had 20 tests. 4 fail on the
    pre-change tree: the version and binding pins, manifest 10 requiring
    quality 13, new manifests at 10, and a manifest-9 index being rebuilt
    rather than reused. The other 16 pass there and pin manifest 9's
    behaviour: queries with and without context, on both backends, with and
    without a quality binding; its policy, table-child and quality-binding
    refusals; its table-family depth; and the refusal of the unbound
    versions 8 and 11. All six mutants are killed: no current-payload set, a
    current-only table count, no context binding, no legacy binding, and a
    current-only policy or table-child check.
  - Review fix-up 1 made it 25 tests: the refusal and table-depth tests now
    run for ordinary and context-window queries. All 18 manifest-9 cases pass
    unchanged on the pre-change tree. They kill the mutant that keeps the
    current-payload checks for ordinary queries only (3 failures: the
    policy, table-child count and unbound table-children refusals in
    context mode), which the same 166 tests let survive.
  - Two existing tests changed on purpose: the "old manifest" case in
    `tests/test_index_state.py` (9 -> 8) and the migration marker in
    `tests/test_vector_store_lock_release.py` (`5->9` -> `5->10`).
  - Focused related suites (62 files): 2,823 passed, 1 skipped. The one
    failure, `test_guided_ocr_review_has_exact_inward_and_host_only_boundaries`,
    is the unrelated untracked-OCR-module failure; the architecture inventory
    test fails only on the stale baseline. Ruff and
    `tools/check_python_sources.py` are clean.
- **Follow-ups (not fixed).**
  - Rebuild the 17 READY runs once, after all of today's changes: a v13
    report, a full re-embed and a new receipt each. Chunk h16 fresh before
    publishing it.
  - The architecture inventory drift now also covers the new public
    `chunking_core.normalization_erases_decorative_squares`, the new keyword
    parameters `quality_core.source_inventory(source_oracle_refs)`,
    `quality_core._source_exclusion_reason(source_oracle_bound)` and
    `index_state._query_manifest_dimension_impl(current_payload_schema_versions)`,
    the new `rag` binding `_CURRENT_PAYLOAD_LEGACY_MANIFEST_VERSIONS`, the
    changed version and binding values, and, once tracked, the two new test
    modules. It was refreshed with review in `b8fd9cc`.
  - `validate_quality_report` still does not recompute
    `excluded_items_by_reason`. The gap predates this change; the counts are
    informational, and eligibility stays bound through the fidelity
    descriptor root.
  - A square-only caption, which the chunker can also erase, still fails
    closed; none exists in the scanned conversions.

### Footnote placement by observed geometry (2026-09-27, draft PR, not integrated)

This has the same status as the sections above. It adds a third
gate-driven replay to the one dispatcher, for the image-only h18 route. No
schema or policy version changed.

- **h18 route.** The page-labeled h18 is one scanned image per page under
  an invisible, badly misread scanner text layer. A private image-only
  derivative (only that render-mode-3 layer removed; page count, labels,
  image streams and 1x/2x renders verified identical) converts with
  `--ocr-no-angle-classifier`, as h04 does, with perfect source fidelity
  (10,325/10,325 tokens). Its one gate failure was
  `same_page_reading_order` on one page.
- **Defect.** Sidecar placement compares a footnote's page box with each
  body record's. A text-free omission row printed among that page's notes
  was chunked into the body record that continues onto the next page. Its
  tokenless entry widened the record's box into the upper notes, so their
  geometry was declared invalid. The semantic fallback then applied the
  page-continuation rule and placed them after the next page's body, and
  after the lower notes. The fidelity audit registers no occurrence for a
  plain tokenless entry, so placement disagreed with the audit's own
  geometry.
- **Trigger.** The publication failure sink records a footnote request only
  when the first pass's report fails `same_page_reading_order` with a
  violation naming, as `before_ref` or `after_ref`, a string ref the first
  pass published in a footnote sidecar (`_footnote_placement_replay_pages`).
  The request is the set of those violations' pages. The unchanged gate
  failure then propagates.
- **Replay.** `chunk_document` replays the chunk once, under the same lease
  and outside the failure's handler, after recording a
  `chunk_footnote_placement_replay` observation (`footnote_pages`). Only on
  the named pages, `_reorder_footnote_sidecars` builds boxes from the
  lineage the audit observes (`_record_observed_source_box`: entries with
  source tokens and zero-width opaque recoveries). A body record the audit
  does not observe on that page no longer constrains the placement; a note
  it does not observe keeps its full box; malformed lineage still fails
  closed to the semantic fallback. The replay gets no sink and raises the
  plain, unchained gate `RuntimeError` when it fails.
- **Precedence (limitation).** The order replay, then the list replay, take
  precedence. A footnote request beside either is dropped, so a document
  that needs two repairs stays failing (fail-closed; none exists in h26).
- **Scope.** Output changes only when the unchanged first pass fails
  `same_page_reading_order` naming a footnote sidecar and names no deferred
  group or uncovered list join. With no named page, placement runs the
  previous code path.
- **h18.** The replay passes every gate. Against the first pass, only
  records 7-12 on that page move: the two upper notes now follow the
  passage, before the lower notes. Every record's text and lineage is
  identical. The omission row stays a paragraph of the body record, as in
  the first pass. The derivative was published by a `full` job with this
  replay, and then again with `--ocr-merge-interleaved-regions` and the
  split-item retraction below, which superseded that first run; the whole
  course (18 excerpts and the Summer 2026 Update) is now READY.
- **Preservation.** All 18 READY h26 runs (h01-h17 and the Summer 2026
  Update) re-chunk byte-identical to their publications: chunks, oracles,
  and quality reports with no structural change. h03 and h16 still take
  their own replays.
- **Evidence.**
  - `tests/test_footnote_placement_geometry.py` (27 tests) pins the
    placement: the unchanged first pass, the replay on named pages only, a
    one-token entry, a note's own tokenless row, an unobserved body and
    note, zero-width opaque entries, and three malformed-lineage cases.
  - `tests/test_footnote_placement_replay.py` (21 tests) pins the trigger
    (including malformed and unhashable refs), the publication hook, the
    dispatcher, precedence, the lease and the observation.
  - `tests/test_footnote_placement_replay_locked.py` (5 tests) drives the
    real locked pass: a synthetic two-page document whose first pass alone
    fails exactly as h18 did, its replay passing, a single pass without the
    row, and injected failures showing that only refs published in footnote
    sidecars request a replay. The replay tests fail on the pre-change
    code, where the behavioural failure is the propagated gate error.
  - `tests/test_list_boundary_replay.py` and
    `tests/test_native_group_order_replay.py` pop the new first-pass sink.
  - An independent review found no path that changes a passing document;
    its 30 mutants left 6 non-equivalent survivors, all now killed (with 2
    more), and one equivalent (the check-name filter). A 780-variant
    malformed-shape fuzz found no replay-only exception.
- **Follow-ups (not fixed).**
  - The omission row belongs to a footnote's quoted text but stays in body
    flow: no note on that page opens with a number (OCR dropped the
    markers), so no footnote lane is established. It carries no words.
  - `_footnote_slot_boxes` also ignores zero-width opaque entries, which
    the audit registers; at worst a slot is left in its order and stays
    reportable.
  - The OCR text of h18 has gate-silent defects of its own (the fidelity
    oracle is the OCR): a page-by-page audit of this route found 7
    line-level reading-order swaps, all from pairs of same-label layout
    regions overlapping by several lines, ~9 word misreads and ~40
    run-together words. See "OCR interleaved-region merge" for the swaps.
  - The architecture inventory drift covers the new helpers, the new
    keyword parameters and the new test modules.

### OCR interleaved-region merge (2026-09-27, draft PR, not integrated)

This has the same status as the sections above. It adds an opt-in conversion
setting for a gate-silent reading-order defect in scanned excerpts. No schema
or policy version changed.

- **Defect.** Docling 2.121's layout model can split a scanned excerpt's
  paragraph into two overlapping layout regions with the same label (`text`,
  `list_item` or `footnote`), overlapping by several lines. OCR reads each
  line once, and line indexes run top to bottom. But
  `LayoutPostprocessor._assign_cells_to_clusters` gives each line to the
  region that covers most of it (ties go to the first), and
  `_remove_overlapping_clusters` merges only above 0.8 IoU or containment. So
  lines in the overlap go to whichever region wins, each region sorts its own
  lines, and a line jumps one or two lines in the published text. No quality
  gate sees it, because the OCR text is its own fidelity oracle. h18 has 7
  such pairs (PDF pages 7, 9, 11, 15, 18, 19 and 21; 26-97 pt of overlap).
- **Setting.** `--ocr-merge-interleaved-regions` (`convert`, `full`,
  `batch`, off by default) applies whenever OCR runs. It does not enable OCR,
  `--no-ocr` normalizes it away, and it composes with `--ocr-full-page` and
  `--ocr-no-angle-classifier`. Only when it is set does
  `"ocr_merge_interleaved_regions": true` enter the parameters digest and the
  v3 conversion manifest. The strict loader accepts that field only as JSON
  `true`, alone or beside `"ocr_angle_classifier": false`.
  `ConversionSourceBinding` carries it. Resume refuses a manifest whose record
  contradicts its digest, and the resume command keeps the flag. OCR Docling
  retry proposals refuse flagged conversions, because retry OCR never merges
  regions.
- **Wiring.** With the flag off, the converter is built exactly as before,
  with no `pipeline_cls`. With it on, `PdfFormatOption` gets a subclass of
  Docling's default `StandardPdfPipeline`, built by
  `_interleaved_region_merge_pipeline_cls()`. After `super().__init__`, it
  wraps that instance's layout postprocessing model. The wrapper passes each
  page's final clusters through `_merge_interleaved_ocr_regions`, a
  dependency-light function that operates on duck-typed clusters. Nothing is
  monkeypatched globally. A page with nothing to merge keeps its original
  prediction object. The page `layout_score` is computed before the merge
  and is unchanged.
- **Scope.** Two clusters interleave when they share one of the three
  labels, neither has children, their typical lines share one column, and at
  least 80% of a line assigned to one lies inside the other's box. Columns
  agree when the regions' median line extents overlap by at least 80% of the
  narrower one (`_layout_columns_agree`), so a line OCR reads across a column
  gutter never joins two columns line by line. The setting is meant for
  single-column scans. Zero-area lines are skipped. Groups close
  transitively. A group keeps the member whose first line index is lowest
  (its id, label and confidence). It gets the union box and every member's
  lines, deduplicated and sorted by index. Clusters are then re-sorted by
  Docling's own `_sort_clusters(mode="id")` key. Pictures, tables, wrappers,
  other labels and edge touches of a few points never merge. Clusters that
  are not merged come back as the same objects.
- **Evidence.**
  - `tests/test_ocr_region_merge.py` has 91 tests, all on synthetic geometry.
    One more is in `tests/test_ocr_docling_io.py`, and two new cases are in
    the unknown-root-field test of `tests/test_source_snapshot_integrity.py`.
    On the pre-change sources, 88 of these 94 fail. The 6 that pass are
    characterizations: resume commands and background argv without the
    flag, and unknown root fields rejected beside the new field.
  - The mutants are killed: a strict `> 0.8`, no label check, a one-way
    containment check, no re-sort, the first member as base, children
    allowed, merging without OCR, and no resume check. Removing the zero-area
    guard is an equivalent mutant: a zero-area line has no positive overlap.
  - Default digests are pinned by the angle-classifier goldens, with the
    setting unset and set to `False`. A flag-off conversion of h18
    (`--ocr-no-angle-classifier` only) with this tree is byte-identical to
    the 2026-09-26 trial (JSON, Markdown, and manifest `bc09538b…`).
  - A flagged h18 conversion (`--ocr-no-angle-classifier
    --ocr-merge-interleaved-regions`) merged the 7 pairs: 201 items became
    194, and no same-label pair overlaps by more than 8 pt. The 10 edge
    touches (0.3-5.7 pt) are unchanged. The body text keeps the same
    multiset of 10,258 whitespace tokens, and every other-label item is
    identical. A recording rerun reproduced the JSON byte for byte. It showed
    each merged region's 5-32 OCR lines in non-decreasing top order. In each,
    the lines of the two source regions interleave in 4 to 6 runs.
  - An independent review approved with changes. It showed that a line read
    across a column gutter could merge a two-column page line by line; the
    column guard above fixes that. `tests/test_ocr_region_merge_columns.py`
    (21 tests) pins the gutter case, the median column span, the agreement
    threshold, transitive merging in all six input orders, a cell diagonally
    outside a box, and Docling's real run context holding the merging model
    on its `layout_postprocess` stage. With the guard, the flagged h18
    conversion is byte-identical to the one above (JSON and Markdown).
  - A page-by-page re-audit of the chunked, flagged h18 against the scans
    found all 7 line swaps of the unflagged conversion in order and no new
    defect: the whole-document word multiset is identical, 63 records with
    the same pages and headings, 9 records changed at the fixed locations.
- **Follow-ups (not fixed).**
  - Code from before this change rejects a flagged manifest.
  - The merge applies to every page of a conversion that runs OCR. A page
    whose lines come from a text layer is ordered by content-stream index,
    not position. h18 is image-only, so no such page exists there.
  - Chunking the flagged h18 exposed a chunk-preparation duplicate; see
    "Split item beside a per-item whole publication" below.
  - The architecture inventory needs a reviewed `--refresh`: the new private
    helpers, the changed signatures and the new test module drift from it.
  - The OCR retry and disposition tools have no merged-region route.

### Split item beside a per-item whole publication (2026-09-27, draft PR, not integrated)

This has the same status as the sections above. It fixes a chunk-preparation
duplicate that the flagged h18 conversion exposed. No schema or policy
version changed.

- **Defect.** HybridChunker can cite one long source item from two or more
  raw chunks. In `_prepare_source_preserving_chunks`, an ordinary raw chunk
  that holds only that item publishes its chunk-local slice (both the source
  rebuild and the retained-text paths keep the slice for a split item). A
  later raw chunk that takes a per-item path (one also holding a
  synthetic-layout item or a source table, one crossing a heading or
  structural boundary, or a wholesale native rebuild) publishes the item
  whole. The whole item then repeated the slices, and the fidelity audit
  rejects repeated source tokens, so `source_token_fidelity` failed. The
  flagged h18 hit it on the one list item the merge lengthened past the chunk
  budget, beside a synthetic layout.
- **Fix.** `append_chunk_slice` appends both slice paths' entries and records
  a lone split item's slice when it has source tokens. Each of the four
  whole-item paths calls `retract_split_slices(ref)` first, which removes
  that item's recorded slices from `prepared` by identity, inside the main
  loop and before any post-loop reordering. The whole item takes the later
  chunk's place.
- **Scope.** The retraction fires only when the previous output held both a
  slice with at least one source token and the whole item. The audit's
  alignment claims each source position once and moves forward, so such a
  repeat fails `source_token_fidelity`; an independent review found only
  contrived exceptions (orphan recovery detaching a slice whose exact text is
  another unrepresented item). A later slice of an item already published
  whole does not occur in the reproductions and is unchanged.
- **Evidence.**
  - `tests/test_split_item_layout_duplicate.py` (6 tests) drives the real
    locked pass with a stubbed layout detector: a note split in two and in
    three beside a layout item, a note beside a source table, a second split
    note outside any layout keeping its slices, and two characterizations
    where the note never shares a raw chunk with the layout. The 4
    behavioural tests fail on the pre-change code with the propagated gate
    error; the characterizations pass on both.
  - Mutation (review plus follow-up): retracting only the first or last
    slice, retracting every ref, and dropping the retraction on the layout or
    table path are killed. Dropping the lexical-token or single-item guard
    survives: where either would matter, the output fails the gate either
    way. Plain recording on the source-rebuild slice path survives: no
    synthetic document reaches it with a later whole publication.
  - All 19 READY h26 runs re-chunk byte-identical (chunks, oracles, quality
    reports), including `h18-agency-inaction_3` against its derived input.
  - The flagged h18 now passes every gate: 63 records, the long note split
    once, no repeat.
- **Follow-ups (not fixed).**
  - The heading-crossing and wholesale-rebuild call sites are covered by
    inspection only.
  - A split item's whole publication lands at the later chunk's position,
    after any sidecars of the retracted slice's chunk; the geometry gate
    fails closed if that ever misorders a page.

### Test-suite time (2026-09-28, integrated)

The dependency-light suite has about 15,000 tests. On PR #116 the Windows
unit lane needed 29.7 minutes (its limit was raised from 20 to 60) and the
Linux unit lanes used 9 to 12.5 of their 15 minutes. Two stacked draft PRs
shorten them without changing pipeline behavior.

- **[PR #117](https://github.com/toddlar00/rag-pipeline/pull/117)** (on
  #116). The repository architecture inventory is built once per test
  session. The inventory builder indexes scope-owned AST nodes once (one
  build 40.1 -> 27.0 s on Windows, output byte-identical). Hash-lock
  parsing is cached by content. A new `--shard INDEX/COUNT` option selects
  every COUNT-th test after `-k`, `-m` and `--deselect`, and the Windows
  unit lane runs as three shards. OCR execution receipts reuse a source file's digest
  while its `lstat` identity is unchanged and its last change is older than
  a 2-second racy window; the link-component refusal still runs on every
  call. Hosted Windows shards took 7m44s to 8m47s. Merged as `e15448b`.
- **[PR #118](https://github.com/toddlar00/rag-pipeline/pull/118)** (merged as
  `3b430bf`). pytest-xdist 3.8.0 and execnet 2.1.2 join the
  hash-locked test toolchain (test/audit tooling domain). The Linux unit
  matrix and the Windows shards run `python -m pytest -q -n auto` (the
  shards add `--shard N/3`). Six
  entrypoint-admission tests patch `__main__.__file__` with
  `raising=False`, because an xdist worker's `__main__` has no `__file__`.
  Locally with four workers the Linux light suite takes 88 s (about 325 s
  serially) and the Windows light suite 585 s (about 1,515 s). Hosted on
  #118, the Linux unit lanes took 3m43s to 4m34s and the Windows shards
  4m09s to 4m39s. Seven OCR review test modules now skip without gradio,
  which the dependency workflow's core-lock job exposed.
- **Follow-ups (not done).** The full CPU environment, service and
  vector-store lanes stay serial; per-worker model loading in the full
  environment is unmeasured. Without psutil, `-n auto` counts logical CPUs.

### Corpus audit follow-ups (2026-09-28, integrated)

An independent, read-only audit of the private h26 casebook corpus compared
every page of its 19 published runs (382 pages) with the page scans. It
verified 202 substantive defects. Every publication gate still passed,
because the gates' source oracle is the converted text itself. Excerpts
published from a scanner OCR text layer averaged about one substantive
defect per 1.4 pages. `h18-agency-inaction` had none in 21 pages; it was
read by fresh layout-aware OCR of an image-only derivative, using
`--ocr-no-angle-classifier --ocr-merge-interleaved-regions`.

- **Fixed in [PR #119](https://github.com/toddlar00/rag-pipeline/pull/119)
  (merged as `ab6e159`): `--rerank` in the locked environment.** FlagEmbedding
  1.4.0 scores pairs through `tokenizer.prepare_for_model`, which the
  locked Transformers 5.8.1 tokenizers lack. Reranking therefore logged a
  failure, kept the hybrid order and still exited 0. #119 raises
  FlagEmbedding to 1.4.2, which ships a Transformers v4/v5 compatibility
  layer, and adds a full-environment guard test.
- **Corpus outcome (private data, not in this repository).** 17 other
  excerpts were re-read by the h18 route, and 7 of them again with
  whole-page OCR. 11 now publish fresh-OCR runs: they are READY, the drop
  check below finds nothing missing, and no audited defect scores worse. On
  those 11, 81 of 99 text-measurable audited defects now match the scan,
  against 2 before. The other 6 keep their scanner-layer runs for the two
  reasons below.
- **Follow-ups (not fixed).**
  - *Silent OCR text loss.* On curved or skewed page areas, fresh OCR can
    return no text for lines that no layout region (region OCR) or no OCR
    detection (whole-page OCR) covers. 4 of 17 re-read excerpts lost 7 to
    27 printed lines on one or two pages, yet every gate passed. A
    geometric check found them by comparing each scanner-layer line box
    with the conversion's region boxes. An OCR coverage gate should compare
    printed-line geometry from an independent detector, or from an existing
    text layer, with the conversion regions and fail closed on uncovered
    text.
  - *Source-token changes on OCR input.* Two of 17 re-read excerpts failed
    `source_token_fidelity` in both OCR modes. The causes were a same-heading
    merge that removed a hyphen between two source items, the known
    fused-term spellings applied to source-bound text, and one folio misread
    as `1` and labeled body text. The first two are fixed by the section
    below; the folio case remains open.
  - *Printed-page discontinuities.* A chunk can join text across a gap
    between two excerpt ranges. It then cites a span that includes the
    missing pages and inherits the earlier chapter's heading path. A
    non-consecutive printed label should end the chunk and the inherited
    heading context.
  - *Query layer.* There is no corpus-wide query mode. Hybrid RRF scores
    and the vector-only fallback's cosine scores, which are used silently
    when BM25 finds nothing, are not comparable across runs. Search does not
    check READY or superseded status. Opening a Chroma run rewrites its
    SQLite file, and the persisted HNSW index is empty until first open.
  - *Run and job bindings.* `_pipeline_run_is_ready(output_root, ...)`
    resolves artifact paths from the module-level output root, not its
    argument, so a different root reads as not READY (fail-closed).
    Durable-job input bindings hash the input path, not the input bytes.
  - *TOC glyph leaders (private tort-law casebook).* The casebook's table
    of contents has dot leaders that extraction returned as a control
    character plus a run of U+FFFD. `_parse_toc_tables` now splits those
    "title<glyph leaders>page" chapter rows in a second parse. That parse
    runs only when the ordinary one leaves a fused chapter title (at least
    three glyphs, a page number, then more text), and it takes the book
    from 2,753 lineage issues to 6.
    - Once the split runs, a summary-table chapter entry can carry an
      earlier or equal page than the detailed-contents entry, and so win
      primary dedup with its wording.
    - A glyph chapter row that the ordinary parse discards, for example
      before a roman-numbered row, stays discarded unless the gate fires.
    - The remaining 6 issues come from a separate reading-order defect on
      one page. Docling put the page's lower block before its upper block,
      and the existing source reading-order repair did not recover it. A
      detached soft hyphen (one item with zero lexical tokens) sat beside
      a lower-block paragraph but was captured with the upper block, and
      that made the blocks overlap. A follow-up pull request addresses
      it. When only such glyphs captured with the upper block stop the
      rotation, both the repair and its lineage mirror now rotate the page
      intact. The captured order must also invert a horizontally
      overlapping pair of text items. Only that page changes in the book,
      its 6 records bind, and the 19 READY h26 runs are unchanged.
      With the rotation fixed, the casebook's chunking reaches the corpus
      quality gate. That gate surfaces two separate defects the rotation
      had masked, and both are follow-ups. One is source-token fidelity on
      printed pp. 389–390, where two source items share one output record.
      The other is a table's source-native row-count mismatch on
      pp. 678–679, which spans two table records.
  - *Durable-job manager writes on Windows.* The advisory heartbeat now
    tolerates transient replace failures (WinError 5, 32 or 33) for up to
    60 seconds. The manager's other writes still fail on the first such
    error: the child-started and cancellation runtime writes, the attempt
    report, the ready marker, and the terminal runtime write. When a
    handle is held across worker exit, the terminal write's
    `PermissionError` escapes `run_job` after the store has recorded
    `succeeded`, and reconciliation repairs the report later.
  - *Born-digital structure.* The born-digital supplement shows the same
    heading and opinion attribution errors, so attribution is a structure
    problem, not only an OCR one.

### Fresh-OCR token fidelity (2026-09-28, integrated)

Merged through [PR #121](https://github.com/toddlar00/rag-pipeline/pull/121)
at `602bec1`.

The corpus audit's re-read showed three places where the pipeline changed
the lexical tokens of fresh-OCR text. Each failed publication, although the
source was read correctly. No schema or policy version changed.

- **Merge join.** `_coalesce_chunk_boundaries` read a lowercase continuation
  after a line-end hyphen as one wrapped word, so `Cabinet-` + `level`
  became `Cabinetlevel`. When either record is source-bound, the join now
  keeps the hyphen (`Cabinet-level`), which preserves both source tokens.
  Text without lineage keeps the old join.
- **Fused-term spellings.** Normalization rewrote `threejudge` as
  `three-judge` even in source-bound text whose OCR item read `threejudge`.
  `chunking_core._normalize_text` gains `repair_fused_terms` (default on).
  The source-bound chunk normalizer (`_normalize_source_chunk_text`,
  including its duplicate-line allowance keys) turns it off through a
  context variable, so no patchable normalization seam changes its
  signature. The variable holds a per-call scope that closes on return,
  so a context copied during the call cannot revive it. A replacement core
  with the established signature keeps working.
- **Publication checks.** The structural publication check and the
  quality report's `normalization_invariants` both rejected every known
  fused term. Both now skip source-bound records, for which the fidelity
  audit decides: a spelling the source attests is kept, and one the
  pipeline fused fails fidelity. No READY run contains such a term, so no
  report changes.
- **Receipt digest cache (review follow-up from #117).** A digest is cached
  only when the opened handle's content identity (device, inode, size,
  mtime) matches the `lstat` identity observed before the read. The `lstat`
  identity also includes ctime, which is compared only between `lstat`
  calls because Windows `lstat` and `fstat` report different ctime
  semantics. A read whose handle is not the observed file is retried, and
  after three such reads the receipt fails instead of binding other
  bytes.
- **Evidence.** `tests/test_ocr_token_fidelity_fixes.py` (14 tests; the
  behavioural ones fail on the base code) and three new receipt-cache tests.
  An independent review found that the allowance keys still used the
  repaired spelling, which dropped a source-attested repeated line; that
  case, the copied-context revival and the retry now have tests that fail on
  the reviewed head. All 19 READY h26 runs re-chunk byte-identical: chunks,
  oracles and quality reports. The previously failing excerpt with the merge
  join now fails only on its folio item. The fused-term excerpt now passes
  all five publication gates.
- **Follow-ups (not fixed).**
  - A folio misread by OCR (here `4` read as `1`) and labeled body text is
    stripped as a page number, so `source_token_fidelity` fails on it. The
    mislabeled-page-number detector deliberately requires the dominant
    printed-page offset, and a test pins an off-offset number as body text.
    Accepting a one-digit misread in the folio lane of a page without its
    own folio would change that fail-closed policy, so it is an owner
    decision.
  - The reranker still fails open: on any exception it logs, keeps the
    hybrid order and exits 0.
  - `build_save_feedback` imports gradio before it validates its panel
    argument, so that argument's validation test needs gradio.
  - Two other source-bound paths still apply the fused-term spellings: the
    chapter summary-of-contents table and the native-edit fallback in
    chunk text repair. Both fail closed at fidelity.
  - `normalization_invariants` changed meaning without a quality schema
    bump. A verifier older than this change rejects a READY report whose
    source-bound text keeps an attested fused spelling, so such runs
    belong to this code or later.
  - No test covers one source item that the chunker splits at a line-end
    hyphen and whose two slices a merge joins again.
  - The fused-term skip follows the call path, not the record's lineage: a
    record whose computed lineage is empty keeps an OCR fused spelling and
    is then rejected by the lineage-free check (fail-closed; such records
    already fail `chunks_have_source_lineage`).

### Dependency and supply-chain series (2026-09-29, integrated)

Nine stacked one-domain pull requests superseded Dependabot #111, #114, #115
and #120 (and part of #108) and cleared the networked supply-chain jobs. Each
regenerated its locks with `tools/refresh_locks.py`, passed the full locked
suites on Windows and Linux and its own Phase A0 pair, and they merged
history-preserving, in order, through `1cffc58`.

- [#122](https://github.com/toddlar00/rag-pipeline/pull/122): qdrant-client
  1.19.1, and onnxruntime 1.30.0 on CPython 3.11+.
- [#123](https://github.com/toddlar00/rag-pipeline/pull/123): gradio 6.28.0
  and uvicorn 0.54.0. Gradio 6.28 keeps streaming diff state only for calls
  that carry an event id and percent-encodes file URLs, so two OCR-review test
  harnesses were adapted without weakening their assertions.
- [#124](https://github.com/toddlar00/rag-pipeline/pull/124): uv 0.12.20 (the
  pinned lock resolver) and ruff 0.16.9.
- [#125](https://github.com/toddlar00/rag-pipeline/pull/125): torch 2.14.0,
  torchvision 0.29.0, sentence-transformers 6.1.0 and tqdm 4.70.1.
- [#126](https://github.com/toddlar00/rag-pipeline/pull/126): docling-core
  2.99.0 alone, which lifts a macOS transformers cap.
- [#127](https://github.com/toddlar00/rag-pipeline/pull/127),
  [#128](https://github.com/toddlar00/rag-pipeline/pull/128) and
  [#129](https://github.com/toddlar00/rag-pipeline/pull/129): transformers
  5.16.1, datasets 5.0.1 and aiohttp 3.14.3 (ML/runtime), h2 4.4.1 (vector
  stores) and cryptography 50.0.1 (provider transport) became governed direct
  inputs to take their advisory fixes.
- [#130](https://github.com/toddlar00/rag-pipeline/pull/130): the expired
  license and vulnerability exceptions were renewed through 2026-12-27, and
  four unpatched advisories were accepted until then. Both supply-chain jobs
  pass at `1cffc58`.
- Beyond the unit suites, the ML and PDF changes were checked against private
  READY runs: fixed queries (plain and reranked) returned identical results,
  and re-converting three audited readings reproduced their chunk text
  exactly.
- **Held back.**
  - *docling 2.130 (#108).* Its checkpoint passed the suites and pair, but
    re-converting audited private readings changed their text: 9 measurable
    audit findings got worse and 1 better, mostly reading order. docling
    stays at 2.121.0 until the owner accepts or rejects that change.
  - *numpy 2.5.3.* `ocr_disposition_observer.verified_recipe` activates only
    on rapidocr 3.9.2 with numpy 2.5.2; on 2.5.3, 229 of its tests error.
    Moving numpy first needs that recipe re-verified.
  - *transformers 5.17+.* 5.17.0 removed
    `PreTrainedModel.get_extended_attention_mask`, which the pinned Nomic
    embedding code calls; the unit suites passed while every real embedding
    failed. The requirement is capped `<5.17`, and
    `tests/test_embedding_runtime_compat.py` guards it. Lifting the cap needs
    a Nomic code revision, or a reviewed runtime transform, that no longer
    calls the helper.
  - *Google GenAI 2 (#88)* stays parked on its recorded owner decision.
- **Follow-ups (not fixed).**
  - On macOS only (Tier 3), the universal locks pair docling-ibm-models 3.13.2
    with transformers 5.16.1; 3.13.3 caps transformers below 5.9 on darwin for
    an MPS issue.
  - Accelerate PYSEC-2026-3804 has no fix: both upstream fix pull requests were
    closed unmerged, and 1.15.0 leaves the loader unchanged. OSV marks 1.14.0 as
    the last affected release, so a bump to 1.15.0 would silence pip-audit
    without remediating anything.
  - Every renewed or accepted exception expires on 2026-12-27. Renew or
    remove each before then.
  - The unit suites never load real models or convert real PDFs, so an ML or
    PDF dependency change also needs a real-model query check and a
    re-conversion check.
    - `tests/test_embedding_runtime_compat.py` now pins the Transformers
      names the pinned Nomic code uses: every name its remote-code files
      import and every inherited `PreTrainedModel` helper they call. It pins
      names only, not call signatures or output fields. Wherever the pinned
      files are in the local model cache, the test re-derives that list from
      the byte-verified files, and the derivation fails on module imports and
      computed attribute names it cannot resolve.
    - A CI job that downloads the pinned models and runs a real embed,
      query and conversion is still an owner decision. It means about 2 GB
      of model downloads or cache, plus changes to the security-pinned
      workflows.
  - Before #123, the Linux Phase A0 environments were synchronized with uv
    0.11.31 rather than the pinned uv. The hash-locked lock union alone
    determines the installed set; pairs from #123 on use the pinned uv on
    both platforms.

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
checkpoint `8891e1b` completed the same cycle: gate-only child `1533164`
passed the hosted CI lane, received the external exact-SHA record on
[PR #107](https://github.com/toddlar00/rag-pipeline/pull/107), and merged
through history-preserving `e34103f` (two networked supply-chain
vulnerability/SBOM jobs outside CI failed at that head). The OCR-program
and casebook-excerpt checkpoint `1ae8502` then changed Python source (no
dependency or model lock); its gate-only child `766feaf` passed both hosted
Phase A0 cells on [PR #116](https://github.com/toddlar00/rag-pipeline/pull/116), but that head's dependency-light unit lanes
failed at collection. Source `a15232d` (their test guards and the casebook
fixes, still no lock change) superseded it before promotion. Source
`a15232d` then passed both hosted Phase A0 cells on [PR
#116](https://github.com/toddlar00/rag-pipeline/pull/116) as well, but its
Python 3.10/3.11 unit lanes each failed one import-guard test and its
Windows unit lane exceeded the workflow's 20-minute timeout; the test-only
source `8aa08b2` superseded it before promotion. Source `8aa08b2` fixed
those lanes, and its hosted run passed every CI lane except the Windows unit
lane, which again reached the 20-minute timeout with no test failure. On the
owner's decision, source `29ea76b` raises that limit to 60 minutes (the live
workflow, its reviewed fixture and the two pinned workflow hashes) and
supersedes the `8aa08b2` pair before promotion.
The `29ea76b` pair (gate-only child `ed0a0d3`) then passed the hosted CI
promotion gate on [PR
#116](https://github.com/toddlar00/rag-pipeline/pull/116); the separate
networked vulnerability/SBOM jobs failed at that head. The stacked test-time
economy source `365de1c` changes Python source and CI configuration (the
Windows unit lane runs as three shards) but no dependency or model lock, and
supersedes that pair for its own pull request. The `365de1c` pair (gate-only
child `b33a433`) passed the hosted CI promotion gate on [PR
#117](https://github.com/toddlar00/rag-pipeline/pull/117). The stacked
pytest-xdist source `c31f4c9` adds pytest-xdist 3.8.0 and execnet 2.1.2 to
`requirements-test.lock` and `requirements-smoke.lock` (test/audit tooling
domain) and runs the dependency-light unit lanes with `-n auto`; it
supersedes the `365de1c` pair for its own pull request. The `c31f4c9` pair
(gate-only child `52a400c`) passed the hosted CI promotion gate on [PR
#118](https://github.com/toddlar00/rag-pipeline/pull/118). Its lock change
also triggered the dependency workflow's core-lock job, which failed because
seven OCR review test modules imported gradio unconditionally. The test-only
source `ff5e64f` guards those imports and supersedes the `c31f4c9` pair. The
`ff5e64f` pair (gate-only child `419e901`) passed the hosted CI promotion
gate on [PR #118](https://github.com/toddlar00/rag-pipeline/pull/118). The
stacked FlagEmbedding source `515ed91` (ML/runtime domain) moves
FlagEmbedding from 1.4.0 to 1.4.2 in `requirements-core.lock` and
`requirements-full.lock`, so local reranking works with Transformers 5, and
supersedes the `ff5e64f` pair for its own pull request. On that pair's
hosted run (gate-only child `c804d7b`, [PR
#119](https://github.com/toddlar00/rag-pipeline/pull/119)) one test in the
Linux 3.12 unit lane failed on a one-second access-time tick in a
whole-`lstat` comparison. The test-only source `ddbef38` compares identity
without the access time and supersedes the `515ed91` pair. The `ddbef38`
pair (gate-only child `43d7a09`) passed the hosted CI promotion gate on [PR
#119](https://github.com/toddlar00/rag-pipeline/pull/119), and #116 to #119
merged into `main` in order (`0a04207`, `e15448b`, `3b430bf`, `ab6e159`).
The fresh-OCR token fidelity source `e8b3205`, on that `main`, keeps source
tokens through chunk merges and normalization and hardens the receipt digest
cache; it changes no dependency or model lock and supersedes the `ddbef38`
pair. An independent review of the `e8b3205` pair's pull request
([#121](https://github.com/toddlar00/rag-pipeline/pull/121)) found that
duplicate-line allowance keys still used the repaired fused spelling; the
review-fix source `1ba9415` corrects that, closes the per-call scope and
retries mismatched receipt reads, and supersedes the `e8b3205` pair.
The `1ba9415` pair (gate-only child `6be805d`) passed the hosted CI
promotion gate on [PR
#121](https://github.com/toddlar00/rag-pipeline/pull/121), which merged into
`main` as `602bec1`. On that `main`, the stacked vector-stores dependency
source `db030f1` (superseding Dependabot #114: qdrant-client 1.19.1, and
onnxruntime 1.30.0 for CPython 3.11+) opens a series of one-domain
dependency pull requests and supersedes the `1ba9415` pair for its own pull
request.
The `db030f1` pair (gate-only child `33397de`) passed the hosted CI
promotion gate on [PR
#122](https://github.com/toddlar00/rag-pipeline/pull/122); its Linux
environment was synchronized with uv 0.11.31 rather than the pinned 0.12.5,
although the hash-locked lock union alone determines the installed set. The
stacked Service/UI dependency source `eccc146` (superseding Dependabot #115:
gradio 6.28.0 and uvicorn 0.54.0) adapts two test harness assumptions to
Gradio 6.28 (an event id for streamed `process_api` runs and decoded file
URLs), synchronizes both platforms with the pinned uv 0.12.5, and supersedes
the `db030f1` pair for its own pull request.
The stacked test-audit tooling dependency source `c11099b` (superseding
Dependabot #111: uv 0.12.20, the pinned lock resolver, and ruff 0.16.9)
moves the OCR installer's uv check, its documentation and the
static-security ruff pin with the locks, and supersedes the `eccc146` pair
for its own pull request.
The `c11099b` pair (gate-only child `f113e00`) passed the hosted CI
promotion gate on [PR
#124](https://github.com/toddlar00/rag-pipeline/pull/124). A PDF/Docling
checkpoint (Dependabot #108: docling 2.130.0) passed its suites and pair but
was withdrawn before promotion because re-converting audited private
readings with it worsened their reading order; docling stays at 2.121.0. The
stacked ML/runtime dependency source `e579090` (superseding Dependabot #120:
torch 2.14.0, torchvision 0.29.0, sentence-transformers 6.1.0 and tqdm
4.70.1) keeps numpy at 2.5.2, which the OCR disposition observer's verified
recipe requires, and supersedes the `c11099b` pair for its own pull request.
The `e579090` pair (gate-only child `2bb7a54`) passed the hosted CI
promotion gate on [PR
#125](https://github.com/toddlar00/rag-pipeline/pull/125). The stacked
docling-core dependency source `13f3b5d` moves docling-core alone from
2.92.0 to 2.99.0 (docling stays at 2.121.0), lifting the macOS transformers
cap that blocks the transformers advisory fix, and supersedes the `e579090`
pair for its own pull request.
The `13f3b5d` pair (gate-only child `d0800ea`) passed the hosted CI
promotion gate on [PR
#126](https://github.com/toddlar00/rag-pipeline/pull/126). The stacked
ML/runtime advisory-promotion source `8c12135` promotes transformers,
datasets and aiohttp to governed direct inputs to take their advisory fixes
(transformers 5.16.1, capped below 5.17 because 5.17 removed a helper the
pinned Nomic embedding code calls; datasets 5.0.1; aiohttp 3.14.3), adds a
guard test and refreshes the inventory, and supersedes the `13f3b5d` pair
for its own pull request.
The stacked vector-stores h2-promotion source `21d66eb` promotes h2 to a
governed direct input to take its advisory fix (4.4.1) and supersedes the
`8c12135` pair for its own pull request.
The stacked provider-transport cryptography-promotion source `3e23b50`
promotes cryptography to a governed direct input to take its advisory fix
(50.0.1) and supersedes the `21d66eb` pair for its own pull request.
The stacked supply-chain renewal source `01adb4f` (owner-authorized on
2026-09-28) renews the expired license and vulnerability exceptions through
2026-12-27, records time-boxed acceptances for the unpatched accelerate and
chromadb advisories, changes no dependency or lock, and supersedes the
`3e23b50` pair for its own pull request.
The `01adb4f` pair (gate-only child `125cb6a`) passed every hosted check on
[PR #130](https://github.com/toddlar00/rag-pipeline/pull/130), including
both networked supply-chain jobs, and #122 to #130 merged into `main` in
order (`e8b7e41`, `ac997dc`, `94c2c47`, `049a57e`, `0a3b7f1`, `ca9c6df`,
`a18e7fc`, `18e9221`, `1cffc58`). The post-merge documentation source
`da13c4c` records the integrated series and its follow-ups in the README and
ROADMAP, changes no dependency or lock, and supersedes the `01adb4f` pair.
The `da13c4c` pair (gate-only child `fab0d70`) passed the hosted CI
promotion gate on [PR
#135](https://github.com/toddlar00/rag-pipeline/pull/135), which merged into
`main` as `9eb6f94`. The failing-only TOC glyph-leader fix source `e7b60cd`
splits table-of-contents chapter rows whose dot leaders were extracted as
control characters or U+FFFD, only when the ordinary parse leaves a fused
chapter title; it changes no dependency or lock and supersedes the `da13c4c`
pair.
The `e7b60cd` pair (gate-only child `2643774`) passed the hosted CI
promotion gate on [PR
#138](https://github.com/toddlar00/rag-pipeline/pull/138), which merged into
`main` as `a2c5629`. The job-heartbeat hardening source `da6540d` rebases
the reviewed heartbeat change of [PR
#137](https://github.com/toddlar00/rag-pipeline/pull/137) (an independent
review approved it with nits, all addressed) onto that merge; it tolerates
transient Windows replace failures of the advisory heartbeat for a bounded
time, changes no dependency or lock and supersedes the `e7b60cd` pair.
The `da6540d` pair (gate-only child `92c92cf`) passed the hosted CI
promotion gate on [PR
#137](https://github.com/toddlar00/rag-pipeline/pull/137), which merged into
`main` as `634c38b`. The test-only source `28ba800` rebases the Nomic
contract test of [PR
#136](https://github.com/toddlar00/rag-pipeline/pull/136) onto that merge;
it pins the Transformers names the pinned Nomic embedding code uses in
`tests/test_embedding_runtime_compat.py`, changes no dependency or lock, and
supersedes the `da6540d` pair.
An independent review of the `28ba800` pair's pull request
([#136](https://github.com/toddlar00/rag-pipeline/pull/136)) approved it
with nits. The review-fix source `8e0b2a6` makes the contract derivation
fail closed on module imports and computed attribute names, tracks
definitions per class, re-derives over every pinned remote-code file, adds a
dependency-free derivation test, and supersedes the `28ba800` pair.
The `8e0b2a6` pair (gate-only child `3062ab3`) passed the hosted CI
promotion gate on [PR
#136](https://github.com/toddlar00/rag-pipeline/pull/136), which merged into
`main` as `2f07510`. The vector-stores promotion source `0333947` makes
oauthlib a governed direct input and moves it from 3.3.1 to 4.0.0
(CVE-2026-49264 and CVE-2026-49265) in the core, full and smoke locks, and
supersedes the `8e0b2a6` pair.
Its Windows/Linux
pair (independent local same-platform comparisons passed) is that branch's
candidate; its hosted checks and exact-SHA record are pending, and the
four merged pull requests carry theirs as PR comments.

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
