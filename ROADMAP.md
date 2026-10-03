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
was integrated through PR #116 (`0a04207`); it does not supersede the integrated
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

### Casebook excerpts and source-order fixes (2026-09-25, integrated)

Merged through [PR #116](https://github.com/toddlar00/rag-pipeline/pull/116)
at `0a04207`.

A private 18-excerpt casebook course corpus (page-bounded scans with no table
of contents) exposed these gaps.

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

### Casebook supplement, audit scope and OCR findings (2026-09-26, integrated)

Merged through [PR #116](https://github.com/toddlar00/rag-pipeline/pull/116)
at `0a04207`.

Each change was independently and adversarially reviewed and changes only
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

### OCR angle-classifier override (2026-09-26, integrated)

Merged through [PR #116](https://github.com/toddlar00/rag-pipeline/pull/116)
at `0a04207`.

It implements the first owner-approved follow-up above. No schema or policy
version changed.

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

### Multi-block overlapping-group recovery (2026-09-26, integrated)

Merged through [PR #116](https://github.com/toddlar00/rag-pipeline/pull/116)
at `0a04207`.

It implements the owner-approved h03 recovery-gate fix. No schema or policy
version changed.

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

### Bullet list after a lead-in at boundary merge (2026-09-27, integrated)

Merged through [PR #116](https://github.com/toddlar00/rag-pipeline/pull/116)
at `0a04207`.

It implements the owner-approved h16 list-join fix (`#/texts/57`) as a
gate-driven replay. No schema or policy version changed.

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

### Letter-spaced text-layer retry (2026-09-27, integrated)

Merged through [PR #116](https://github.com/toddlar00/rag-pipeline/pull/116)
at `0a04207`.

It implements the owner-approved h16 p19 fix (`#/texts/179`, record 49). No
schema or policy version changed. It is not a replay: it runs inside the
first pass, so the one-dispatcher rule above does not apply to it.

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

### Decorative square glyph exclusion (2026-09-27, integrated)

Merged through [PR #116](https://github.com/toddlar00/rag-pipeline/pull/116)
at `0a04207`.

It implements the owner-approved h16 `#/texts/14` fix as a typed quality
exclusion. It is the owner-approved exception to the rule above: the quality
report schema goes from 12 to 13 and the index manifest schema from 9 to 10,
with a legacy query binding `(9, 12)`. No other schema or policy version
changed. It is not a replay, and chunking never calls the new predicate, so
no chunk output can change.

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

### Footnote placement by observed geometry (2026-09-27, integrated)

Merged through [PR #116](https://github.com/toddlar00/rag-pipeline/pull/116)
at `0a04207`.

It adds a third gate-driven replay to the one dispatcher, for the image-only
h18 route. No schema or policy version changed.

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
  - The architecture inventory drift covered the new helpers, the new
    keyword parameters and the new test modules. It was refreshed with
    review in `a15232d`.

### OCR interleaved-region merge (2026-09-27, integrated)

Merged through [PR #116](https://github.com/toddlar00/rag-pipeline/pull/116)
at `0a04207`.

It adds an opt-in conversion setting for a gate-silent reading-order defect
in scanned excerpts. No schema or policy version changed.

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
  - The new private helpers, the changed signatures and the new test modules
    drifted from the architecture inventory. It was refreshed with review in
    `a15232d`.
  - The OCR retry and disposition tools have no merged-region route.

### Split item beside a per-item whole publication (2026-09-27, integrated)

Merged through [PR #116](https://github.com/toddlar00/rag-pipeline/pull/116)
at `0a04207`.

It fixes a chunk-preparation duplicate that the flagged h18 conversion
exposed. No schema or policy version changed.

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
Linux unit lanes used 9 to 12.5 of their 15 minutes. Two stacked pull requests,
since merged (#117 and #118), shorten them without changing pipeline behavior.

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
- **Fixed since (private tort-law casebook).**
  - *TOC glyph leaders (private tort-law casebook).* The casebook's table
    of contents has dot leaders that extraction returned as a control
    character plus a run of U+FFFD. Since
    [PR #138](https://github.com/toddlar00/rag-pipeline/pull/138) (merged as
    `a2c5629`), `_parse_toc_tables` splits those
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
      one page (PDF p. 954). Docling put the page's lower block before its
      upper block, and the existing source reading-order repair did not
      recover it. A detached soft hyphen (one item with zero lexical
      tokens) sat beside a lower-block paragraph but was captured with the
      upper block, and that made the blocks overlap.
      [PR #140](https://github.com/toddlar00/rag-pipeline/pull/140) (merged
      as `6baf14b`) fixes it. When only such glyphs captured with the upper
      block stop the rotation, both the repair and its lineage mirror now
      rotate the page intact. The captured order must also invert a
      horizontally overlapping pair of text items. Only that page changes
      in the book, its 6 records bind, and the 19 READY h26 runs are
      unchanged.
      With the rotation fixed, the casebook's chunking reaches the corpus
      quality gate. That gate surfaced two separate defects the rotation
      had masked, and both are now fixed. One was source-token fidelity on
      PDF pp. 389–390, with two uncovered source items and one output
      record (#146, below). The other was a table's source-native row-count
      mismatch on PDF pp. 678–679, across two table records (#145, below).
  - *Stacked table headers (private tort-law casebook).*
    - **Defect.** `#/tables/37` and `#/tables/38` (pp. 678-679) are one
      worksheet with two header bands, split across a page. Both failed
      `source_native_row_count_mismatch`.
    - **Cause.** docling-core 2.99 (#126) joins stacked column-header rows
      into one Markdown header. The native row-count oracle in
      `quality_core._source_table_dimensions` still subtracted exactly one
      header row.
    - **Fix.** Merged through
      [PR #145](https://github.com/toddlar00/rag-pipeline/pull/145) as
      `ce73b61`. `table_retrieval_core.source_table_header_row_count` ports
      docling-core's header-row count. When that count H is not 1, the
      oracle expects `num_rows - H` rows. Cells that fall outside the
      strict types it models, including negative offsets, keep the old
      rule.
    - **Scope.** Only failing expectations change, and no schema or policy
      version moves.
    - **Caveat.** Chunks that docling-core < 2.99 wrote for a table with H
      other than 1 would now fail a fresh quality build. None of the 19
      READY h26 runs has such a table.
  - *Soft-hyphen item seam (private tort-law casebook).* The native text
    layer spells some line-end hyphens as U+002D U+00AD. Where Docling
    ended a list item there and the next paragraph continued the word,
    the chunker joined the two items with a line break. Source-bound
    normalization's `spaced_hyphen` rule joins a plain `x-` line-break `y`
    item seam as `x-y`, but the soft hyphen blocked it. The fidelity audit
    then deleted the soft hyphen and dehyphenated across the break, reading
    one token that neither item owns: 1 output and 2 source coverage
    issues. Since
    [PR #146](https://github.com/toddlar00/rag-pipeline/pull/146) (merged as
    `8cc89b0`), `_join_soft_hyphen_item_seams` rewrites exactly such a
    seam between consecutive text-bearing items to `x-y`, keeping both
    tokens. It also reads the first item's ending from its Docling text,
    because a native-repair override drops the soft hyphen (the override
    must still end in a letter and a hyphen) while the chunk keeps the
    Docling ending. It is failing-only: it fires only when the fused token
    is not a token of any source text, marker or oracle in the document,
    and it skips a pair that shares one recovery oracle. The book has one
    other item that ends this way, and a native recovery group already
    rebuilds its pair; no READY h26 run has one.
    - Follow-up (not fixed): other item seams that the audit fuses but
      normalization leaves split, and that therefore fail the same way.
      Neither h26 nor the casebook has one.
      - Non-ASCII letters. `_SPACED_HYPHEN_RE` matches only ASCII letters
        and digits, but the audit dehyphenates between any Unicode letters
        (`é-` line-break `y`).
      - U+FF0D and U+FE63. NFKC turns both into `-`, so the audit fuses
        them even without a soft hyphen, but `spaced_hyphen` does not join
        them.
      - A soft hyphen before the hyphen (`x` U+00AD `-` line-break `y`).
      - A space between soft hyphens (`x-` U+00AD space U+00AD line-break
        `y`).
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
  - *Flagged title over bare sub-headers.* Consider a flagged full-width
    title row over flagged sub-header rows whose cells are blank,
    whitespace or punctuation only, or repeat the title. Its Markdown
    header joins to the bare title, and rag promotes that to a preamble,
    so the table publishes one data row fewer than the oracle expects.
    It stays fail-closed as a row-count mismatch.
  - *One-column title rows.* `_source_table_dimensions` lacks rag's
    `columns >= 2` guard on full-width title promotion. A one-column table
    in the tort-law casebook (PDF p.881) would therefore fail the row
    check. For now it is published as body text, so the check does not
    run on it.
  - *Durable-job manager writes on Windows.* The advisory heartbeat now
    tolerates transient replace failures (WinError 5, 32 or 33) for up to
    60 seconds. Before #161, the manager's other writes failed on the
    first such error: the child-started and cancellation runtime writes,
    the attempt report, the ready marker, and the terminal runtime write.
    When a handle was held across worker exit, the terminal write's
    `PermissionError` escaped `run_job` after the store had recorded
    `succeeded`, and reconciliation repaired the report later. The branch
    `agent/imp-windows-transient-replace-tolerance` resolved this
    follow-up: it merged into `main` through [PR
    #161](https://github.com/toddlar00/rag-pipeline/pull/161) as `d27af15`,
    where its own Phase A0 pair at source `9e4d7be` passed the hosted CI
    promotion gate (see "Research improvement pass (2026-09-30,
    integrated)"). It retries only transient replace errors of these writes:
    within one 3-second budget until the ready marker is published, 2
    seconds for the cancellation evidence and 8 seconds for each later
    write. Store commit writes, reconciliation writes and launcher-side
    reads stay uncovered.
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

### Post-series fixes (2026-09-29 to 2026-09-30, integrated)

Seven code and dependency pull requests merged after the dependency series
and its documentation follow-up #135 (`9eb6f94`), in this order,
through `8cc89b0`. Four remove the private tort-law casebook's remaining
publication blockers; the others harden durable jobs, pin the Nomic
embedding code's Transformers names and clear two new advisories.

- [#138](https://github.com/toddlar00/rag-pipeline/pull/138) (`a2c5629`):
  TOC glyph-leader split. The private tort-law casebook's contents rows whose
  dot leaders were extracted as glyphs fused into a chapter title that could
  never bind to a source heading. `_parse_toc_tables` now splits them in a
  second parse that runs only when the ordinary one leaves such a title,
  which takes the book from 2,753 heading-lineage issues to 6.
- [#137](https://github.com/toddlar00/rag-pipeline/pull/137) (`634c38b`):
  job-heartbeat transient replace tolerance. When a scanner or indexer
  briefly held the attempt's `runtime.json`, one failed advisory heartbeat
  write made the durable job kill a healthy worker and report
  `permission_denied`. Transient Windows replace failures (WinError 5, 32 or
  33) of the heartbeat are now tolerated for up to 60 seconds; the manager's
  other writes were a follow-up under "Corpus audit follow-ups", which the
  fix merged through #161 resolved.
- [#136](https://github.com/toddlar00/rag-pipeline/pull/136) (`2f07510`):
  Nomic Transformers name-contract test.
  `tests/test_embedding_runtime_compat.py` pins every Transformers name the
  pinned Nomic remote code imports or inherits, so a release that removes
  one, as 5.17 did, fails the unit suites. The review fix makes the
  derivation fail closed on module imports and computed attribute names and
  re-derive over every pinned remote-code file.
- [#139](https://github.com/toddlar00/rag-pipeline/pull/139) (`123bedb`):
  oauthlib 4.0.0 promotion for CVE-2026-49264 and CVE-2026-49265. oauthlib
  became a governed direct input of the vector-stores domain, and only its
  lock records moved. This is supply-chain hygiene, because nothing loads
  oauthlib at runtime; both supply-chain jobs are green again.
- [#140](https://github.com/toddlar00/rag-pipeline/pull/140) (`6baf14b`):
  tokenless-glyph wrap rotation (PDF p. 954). The single-wrap repair and its
  lineage mirror now rotate a page intact when only detached zero-token
  glyphs captured with the upper block break the run-disjointness proof
  (`heading_lineage.tokenless_glyph_rotation_admitted`). It is failing-only
  as evidenced, not proven, and `HEADING_LINEAGE_POLICY` was deliberately not
  bumped: quality requires an exact policy match, so a bump would invalidate
  every published report.
- [#145](https://github.com/toddlar00/rag-pipeline/pull/145) (`ce73b61`):
  stacked Markdown table-header row-count oracle. docling-core 2.99 (#126)
  joins stacked column-header rows into one Markdown header, but the native
  row-count oracle still subtracted one header row, a regression that failed
  two casebook tables. The oracle now counts header rows as docling-core
  does; this is a check-only correction, and no schema or policy version
  moved.
- [#146](https://github.com/toddlar00/rag-pipeline/pull/146) (`8cc89b0`):
  soft-hyphen item-seam join (PDF pp. 389–390). An item seam that the
  fidelity audit would fuse into a token no source item owns is joined as
  `x-y`. The first version never fired in a real chunk run: it read the
  item's ending from the native-repair override, which drops the soft
  hyphen, and the in-memory replay on published text that validated it never
  took the real chunk path. Lesson: such a replay can miss the real chunk
  path, so a chunking fix is confirmed with a real chunk run.
- Every pull request had an independent review, passed the full locked
  suites on Windows and Linux and its own Phase A0 pair, and carries an
  exact-SHA record comment. All merged history-preserving.
- **Tort-law casebook.** With #140, #145 and #146, a real chunk run of the
  private casebook passes the whole corpus quality gate; the only warning is
  `canonical_text_duplicates`. All 19 READY h26 runs re-chunk byte-identical.
  A durable `full` re-run of the casebook from `8cc89b0`, in the new locked
  environment, then published a READY run with the same chunks and a passing
  quality gate (only the `canonical_text_duplicates` warning). Replacing
  the older live index with it is left to the owner.
- **Private h26 excerpts reprocessed on `8cc89b0`.**
  - All 18 textbook excerpts were reprocessed from the exact inputs their
    READY receipts bound, using their published OCR options, in the new
    locked environment `ocr-locked-py312-qualified-v3`. That environment was
    installed by `tools/install_ocr_environment.py` from these locks and
    differs from v2 only in oauthlib.
  - Every new run is READY and has identical chunk text, metadata and Docling
    Markdown. The only differences are `recovery_sha256`, which binds the run's
    own manifest, and float32 noise in one table bbox.
  - No new dropped line and no audited finding worse (235 of 235 unchanged).
    The fixed query smoke matches exactly, plain and reranked.
  - The superseded runs were removed with the reviewed removal script, so
    the corpus again has exactly 19 READY runs, one per reading.
- **Dependabot.** #141 to #144 were closed with explanations:
  - #141: google-genai 2 (the parked #88 owner decision) and a no-op
    cryptography floor;
  - #142: docling 2.130, held back for its worse audited reading order
    until the owner decides (see "Held back" above);
  - #143: a no-op chromadb floor;
  - #144: numpy 2.5.3, which breaks the OCR observer recipe and CPython
    3.10/3.11 resolution; transformers 5.17, which breaks the Nomic
    embedding; and a no-op einops floor.

  Closing a grouped Dependabot PR does not ignore versions, so they may
  reopen. The strict `dependabot.yml` policy
  (`tools/check_dependency_policy.py`) deliberately rejects any field beyond
  package-ecosystem, directory, schedule, groups, commit-message and
  open-pull-requests-limit.
- **Owner decision (open).** Whether to add governed Dependabot `ignore`
  rules, with reasons and expiries like the vulnerability exceptions. Until
  then, a reopened proposal is closed again with its recorded reason.
- **Follow-ups (not fixed).**
  - Hosted Windows runners intermittently fail OCR review UI tests: two 15-s
    Node subprocess timeouts in `tests/test_ocr_review_crop_preview_ui.py` /
    `tests/test_ocr_review_crop_uncertainty_editor.py`, and once
    `test_opt_in_launcher_installs_only_fixed_preview_script_and_preserves_security[archive]`
    with the launcher's generic exit 2. Each passed on re-run and locally.
    #148 (`3aede7d`) raised those Node subprocess ceilings to 60 seconds.
    The branch `agent/imp-review-ocr-close-deadline-clamp` fixes the likely
    cause of the exit 2: Windows monotonic-clock rounding can push the
    remaining close time just above its 40-second limit. No stderr was
    retained from the hosted run, so that cause is not proven. The fix
    merged into `main` through [PR
    #151](https://github.com/toddlar00/rag-pipeline/pull/151) as `584c4a5`
    (see "Research improvement pass (2026-09-30, integrated)").
  - Local Windows full suites under `-n 8` intermittently fail a
    `tests/test_ocr_hardscan_io.py` test with a transient `PermissionError`
    (WinError 32) while `artifact_io.immutable_file_snapshot` unlinks
    `.rag-snapshot-owner.json` in the shared temp scratch root. It was seen
    in the full suites at `3af52f8` (#150) and at `18de7d6`, neither of
    which touches `artifact_io.py` or that test module. It is pre-existing:
    that module alone under `-n 8` on the owner's Windows host failed in 3
    of 24 runs at `3aede7d`, 1 of 12 at each of `f0513b7`, `19b9ef9` and
    `e91d42f`, and 3 of 12 at both `main` `139e2dc` and `18de7d6`. Until
    #165, the snapshot's owner-file cleanup had no bounded transient-error
    retry, the same class of gap as the durable-job manager's transient
    replace writes (see "Corpus audit follow-ups"). The cause is now
    diagnosed: every snapshot first runs `cleanup_stale_snapshot_directories`
    over that shared root, and another process's janitor briefly reads each
    live run's owner marker with a plain `open()`, which on Windows never
    grants `FILE_SHARE_DELETE`, so the owner's unlink fails with WinError
    32. Concurrent pipeline processes that share the default root can hit
    the same race; POSIX is unaffected. It also failed the first attempt of
    `main`'s push run at `1c348da` (see "Phase A0"). A bounded owner-side
    retry merged into `main` through the robustness follow-up [PR
    #165](https://github.com/toddlar00/rag-pipeline/pull/165) as `717ac9e`
    (source `6e7a1e7`), and the deeper shared-delete janitor read remains
    an open follow-up (see "Phase A0").

### Research improvement pass (2026-09-30, integrated)

Eighteen branches `agent/imp-*`, each based on `main` `3aede7d` and pushed,
were prepared, and each was independently reviewed.
`agent/imp-static-security-s324-noqa` merged into `main` through [PR
#150](https://github.com/toddlar00/rag-pipeline/pull/150) as `76f98d6`; its
Phase A0 pair passed the hosted CI promotion gate there, and the separate
networked vulnerability/SBOM jobs failed at that head on three new urllib3
advisories. A separate one-domain provider-transport promotion of urllib3
to 2.8.0, which clears them, merged into `main` through [PR
#152](https://github.com/toddlar00/rag-pipeline/pull/152) as `25f9ba1`; its
Phase A0 pair at source `aab0326` passed the hosted CI promotion gate there,
and the networked vulnerability/SBOM jobs passed at that head. The
three hosted-flake fix branches (`agent/imp-a0-capture-cleanup-retry`,
`agent/imp-review-ocr-close-deadline-clamp` and
`agent/imp-posix-sigterm-test-deflake`) merged together into `main` through
[PR #151](https://github.com/toddlar00/rag-pipeline/pull/151) as `584c4a5`;
their shared Phase A0 pair at source `6b97fc5` passed the hosted CI
promotion gate there. The three query-path speed branches
(`agent/imp-reranker-single-pass`,
`agent/imp-legal-tokenizer-literal-guards` and
`agent/imp-strict-parse-nonfinite-flag`) merged together into `main` through
[PR #157](https://github.com/toddlar00/rag-pipeline/pull/157) as `d3754d5`;
their shared Phase A0 pair at source `18a70ab` passed the hosted CI
promotion gate there. The two model-load speed branches
(`agent/imp-model-verify-readinto-hash` and
`agent/imp-embedding-skip-random-init`) merged together into `main` through
[PR #158](https://github.com/toddlar00/rag-pipeline/pull/158) as `cc39af5`;
their shared Phase A0 pair at source `8ce1a8d` passed the hosted CI
promotion gate there. The four ingestion speed branches
(`agent/imp-chunk-dedup-bitset`, `agent/imp-pdf-enrichment-word-cache`,
`agent/imp-quality-attestation-single-pass` and
`agent/imp-embedding-token-counter-cache`) merged together into `main`
through [PR #159](https://github.com/toddlar00/rag-pipeline/pull/159) as
`139e2dc`; their shared Phase A0 pair at source `6fc0c36` passed the hosted
CI promotion gate there. `agent/imp-docling-conversion-hardening` merged
into `main` through [PR
#160](https://github.com/toddlar00/rag-pipeline/pull/160) as `4994191`; its
Phase A0 pair at source `18de7d6` passed the hosted CI promotion gate there.
`agent/imp-windows-transient-replace-tolerance` merged into `main` through
[PR #161](https://github.com/toddlar00/rag-pipeline/pull/161) as
`d27af15`; its Phase A0 pair at source `9e4d7be` passed the hosted CI
promotion gate there. The CI-validator and secret-scan branches
(`agent/imp-ci-validator-yaml-anchors` and
`agent/imp-secret-scan-history-batch`) merged together into `main` through
[PR #162](https://github.com/toddlar00/rag-pipeline/pull/162) as
`40994b4`; their shared Phase A0 pair at source `e706fd2` passed the hosted
CI promotion gate there, and the networked vulnerability/SBOM jobs passed
at that head. The last one, `agent/imp-lexical-accuracy-toolkit`, merged
into `main` from `agent/lexical-accuracy-toolkit-pr` through [PR
#163](https://github.com/toddlar00/rag-pipeline/pull/163) as `6303d8b`; its
Phase A0 pair at source `5d22bb9` passed the hosted CI promotion gate there,
and the networked vulnerability/SBOM jobs passed at that head. All 18 are
now integrated through history-preserving pull-request merges, and the pass
is complete. The integration branch
`agent/research-improvements` merged all 18 only to validate the
combination and was not itself merged; the branches landed through the
pull requests above. Branch names below omit the `agent/imp-` prefix.

- **Method.** Codebase mapping and external research produced 78
  candidates. A synthesis pass ranked 32, and one adversarial verifier per
  candidate checked the top 14 before any implementation; 13 survived. A
  second round added the lexical evaluation toolkit, and a third round
  verified and built four more. On every branch, characterization or
  differential tests were written before the change, and an independent
  adversarial review approved the result, with fixes and re-review where
  needed (the workflow-validator branch took three rounds).
- **Speed (A1 performance work).** All ten leave pipeline artifacts,
  rankings and scores unchanged.
  - `reranker-single-pass`: scores rerank pairs in one forward pass,
    without FlagEmbedding 1.4.2's discarded batch-size probe. `--rerank`
    scoring is 1.9-2.1x faster with float.hex-identical scores. It merged
    together with `legal-tokenizer-literal-guards` and
    `strict-parse-nonfinite-flag` through PR #157 as `d3754d5`, with their
    shared Phase A0 pair at source `18a70ab`, which passed the hosted CI
    promotion gate.
  - `chunk-dedup-bitset`: an exact size-ratio bound and integer-bitset
    Jaccard in `chunking_core._deduplicate_chunks`. 42-55x at 2,831
    synthetic chunks (184 s to 3.3-4.4 s). Deduplication takes about 54 s
    of the private tort-law casebook's chunk stage; the saving there is
    expected, not measured. It merged together with
    `pdf-enrichment-word-cache`, `quality-attestation-single-pass` and
    `embedding-token-counter-cache` through PR #159 as `139e2dc`, with their
    shared Phase A0 pair at source `6fc0c36`, which passed the hosted CI
    promotion gate.
  - `pdf-enrichment-word-cache`: extracts each page's sorted native words
    once per enrichment call instead of 6-7 times. 29-34 s off each
    enrichment pass on the tort-law casebook, for about 139 MB more peak
    memory. It merged through the same pull request (#159).
  - `embedding-token-counter-cache`: caches the verified token-counter
    tokenizer and counts in batches. A stage simulation drops from 23-26 s
    to 2.2-2.7 s, for about 250 MB more resident memory. It merged through
    the same pull request (#159).
  - `legal-tokenizer-literal-guards`: literal guards and literal-first
    regex forms in `_legal_search_tokens`. 2.2-2.3x, identical over a
    280,000-string differential fuzz; about 1 s of CPU off each cold Chroma
    hybrid query. It merged through the same pull request (#157).
  - `quality-attestation-single-pass`: attests each record once and reads
    the oracle registry once per binding. 35-45% (about 0.7-1.5 s) off each
    quality binding on the index, search and publication paths. It merged
    through the same pull request as `chunk-dedup-bitset` (#159).
  - `model-verify-readinto-hash`: hashes model bundles with `readinto` into
    a reused buffer. 1.4-1.55x, about 0.4-0.8 s per model load. It merged
    together with `embedding-skip-random-init` through PR #158 as
    `cc39af5`, with their shared Phase A0 pair at source `8ce1a8d`, which
    passed the hosted CI promotion gate.
  - `embedding-skip-random-init`: skips the 211 discarded `torch.nn.init`
    calls when the pinned Nomic model is built, behind a fail-closed
    checkpoint-header guard. 1.47 s off each cold embedder load, with
    bitwise-identical embeddings. It merged through the same pull request
    (#158).
  - `strict-parse-nonfinite-flag`: a path-free non-finite scan ahead of the
    strict JSONL parser's metadata walk. 23-31% off each parse on
    synthetic corpora (19-21%, about 50-70 ms, on the largest real corpus).
    It merged through the same pull request (#157).
  - `secret-scan-history-batch`: the secret scanner's history mode reads
    blobs through one `git cat-file --batch`. Windows blob reading drops
    from 106 s to 1.1 s (about 6-7x end to end), with identical findings.
    It merged together with `ci-validator-yaml-anchors` through PR #162 as
    `40994b4`, with their shared Phase A0 pair at source `e706fd2`, which
    passed the hosted CI promotion gate.
- **Robustness and correctness.** No published artifact changes.
  - `static-security-s324-noqa`: retires the only static-security
    suppression (S324, expiring 2026-11-30) by using
    `md5(..., usedforsecurity=False)`, which gives byte-identical sparse
    indices, so no index migration is needed. It also closes a fail-open in
    which inline `# noqa`, ruff range suppressions and project ruff
    configuration could hide blocking findings: the gate now runs
    `ruff --isolated --ignore-noqa`. It merged through PR #150 as
    `76f98d6` with its Phase A0 pair at source `3af52f8`, which passed the
    hosted CI promotion gate (the separate networked vulnerability/SBOM jobs
    failed at that head on three new urllib3 advisories).
  - `docling-conversion-hardening`: a Docling PARTIAL_SUCCESS, failure or
    success-with-errors result now raises
    `DoclingConversionIncompleteError` instead of publishing. Docling 2.121
    swallows stage errors and returns an empty page that the READY gates
    could not see; an injected failure reproduced this. It never occurred in
    67 logged conversions, but such a run now stops instead of publishing.
    The branch also pins Docling's ambient environment knobs
    (`OMP_NUM_THREADS` and `DOCLING_*` changed output bytes) and stops the
    DEBUG log flood. It merged through PR #160 as `4994191`, with its own
    Phase A0 pair at source `18de7d6`, which passed the hosted CI promotion
    gate.
  - `review-ocr-close-deadline-clamp`: clamps the crop archive's remaining
    close time (see the hosted-runner follow-up above). It merged together
    with `a0-capture-cleanup-retry` and `posix-sigterm-test-deflake` through
    PR #151 as `584c4a5`, with their shared Phase A0 pair at source
    `6b97fc5`, which passed the hosted CI promotion gate.
  - `a0-capture-cleanup-retry`: the Phase A0 contained runner retries
    transient Windows errors when it removes its capture directory, so a
    WinError 32 no longer masks the primary error. This was the hosted
    "contained-runner regex" flake: 11 of 640 stressed runs failed before,
    0 of 640 after. It merged through the same pull request.
  - `posix-sigterm-test-deflake`: test-only; removes a startup race in
    `test_posix_escalation_kills_descendant_that_ignores_sigterm` (see
    Phase A0 below). It merged through the same pull request.
  - `ci-validator-yaml-anchors`: closes workflow-validator F4 by rejecting
    YAML anchors, aliases, tags and merge keys that hid unpinned actions or
    write-all permissions. It also rejects fake block-scalar headers, and
    its review rounds closed the block-scalar and Unicode-whitespace
    fail-opens found along the way. About 2.7 million PyYAML-oracle fuzzed
    documents found no new fail-open against `3aede7d` in any shape GitHub
    accepts; all 27 newly accepted unsafe documents write the checkout's
    `with:` as a sequence, which GitHub rejects (see the follow-ups below).
    It merged through the same pull request as `secret-scan-history-batch`
    (#162).
  - `windows-transient-replace-tolerance`: the durable-job manager's
    transient-write follow-up (see "Corpus audit follow-ups"). On
    `3aede7d`, holds of 0.5-10 s on `runtime.json` or `attempt.report.json`
    failed the job or raised after `succeeded` in 32 of 32 trials. With the
    branch, holds within the budgets (0.5-2 s before the ready marker,
    0.5-5 s after it) succeed in 29 of 29; a 5 s pre-ready hold and a 10 s
    terminal hold still fail once their 3 s and 8 s budgets run out (after
    3.48 s and about 8.35 s). It merged through PR #161 as `d27af15`, with
    its own Phase A0 pair at source `9e4d7be`, which passed the hosted CI
    promotion gate.
- **Accuracy tooling.** `lexical-accuracy-toolkit` adds
  `eval.py --retriever lexical`, which scores through the production BM25
  leg (`rag._bm25_search`); the existing `--retriever bm25` is an offline
  fixture adapter that diverges from production. It also adds an opt-in
  `function-words-v1` query policy that drops pure function words from the
  query only, keeps modals and negation, and needs no re-index. The default
  stays `none`, bitwise identical over 8,000 fuzzed calls. On public
  yardsticks run locally and never committed, nDCG@10 moves from 0.225 to
  0.293 on Legal RAG Bench (p=0.0004), by +0.011 on SciFact (p=0.098) and
  by -0.009 on LegalBench-RAG-mini (p=0.08). It merged through PR #163 as
  `6303d8b`, with its own Phase A0 pair at source `5d22bb9`, which passed
  the hosted CI promotion gate. It adds measurement tooling and the opt-in
  policy only: no production CLI, UI or service flag and no default
  changes, and adopting the policy or adding CI lexical baselines remains
  the owner's call.
- **Integration validation at `092ef8c`.**
  - Full locked suites: Windows 16,234 passed and 8 skipped; Linux (WSL)
    16,152 passed and 90 skipped; none failed. `main` `3aede7d` gave 15,555
    passed and 8 skipped on Windows.
  - The ruff, Python-source, static-security, dependency-policy,
    model-artifact, CI-security and architecture-inventory gates pass, with
    0 static-security suppressions (was 1).
  - The three CC0 evaluation suites pass against their committed
    baselines, and `--retriever bm25` results are unchanged.
  - Private data, counts only: a fixed query smoke on three published h26
    runs, plain and reranked, returns the same order with a maximum score
    delta of 0. A `full` run with the published arguments on three published
    h26 readings is READY. Its Docling JSON and Markdown, conversion
    manifest, chunks, quality report and publication receipt are
    byte-identical to the base code's, and 413 of 413 stored embeddings are
    bitwise identical; only timestamps, UUIDs, lock names and the scratch
    path differ. The scratch copies were deleted.
- **Integration (complete).** Each branch landed through a pull request
  with a Phase A0 source/evidence pair (the three hosted-flake fix
  branches, the three query-path speed branches, the two model-load speed
  branches, the four ingestion speed branches and the CI-validator and
  secret-scan branches each shared one stacked pull request and pair; the
  static-security, Docling hardening, job-manager transient-write and
  lexical accuracy toolkit branches each merged with their own). The
  combined query-path, model-load and ingestion pull requests
  departed from the A1 owner-decision row's condition that each slice is
  its own pull request with its own Phase A0 source/evidence pair; they
  merged as #157, #158 and #159. The CI-validator and secret-scan pull
  request departed from it in the same way, because
  `secret-scan-history-batch` is one of the ten speed branches; it merged
  as #162. Every branch
  changes `architecture-inventory.json`, so after each merge the next
  branch was rebased and its inventory refreshed. Twelve branches change
  paths that `ci-security-ownership.json` owns or governs (every `rag.py`,
  `retrieval_core.py` or `model_artifacts.py` change, and the CI-validator,
  secret-scan and static-security tools), so they also needed owned-path
  review. The A1 authorization covers the ten speed branches; the opt-in
  query policy landed default-off, and whether to adopt it before the
  deferred retrieval-experiments phase remains the owner's call.
- **Owner decisions examined, not changed** (see the owner-decision table).
  None of the accuracy candidates was measured on the owner's private judged
  sets.
  - Reranking hybrid results by default: Legal RAG Bench nDCG@10 0.282 to
    0.386 (p=0.0013). The README's "reranking hurts hybrid" result was
    measured at 80 candidates, while production reranks 20, and
    `reranker-single-pass` halves the cost.
  - `function-words-v1` as the default query policy (numbers above).
  - Fusion weights: public yardsticks favor a dense weight at least equal
    to the sparse one, which conflicts with the recorded private
    calibration; the defaults are dense 0.5 and sparse 1.0.
  - Qdrant hybrid (service path): its sparse leg is raw TF×IDF, not BM25
    (SciFact nDCG@10 0.489, against 0.670 for the BM25 leg), and its fusion
    ignores `--rrf-k` and the weights. No published run uses Qdrant; a fix
    needs a versioned re-index for service users only.
  - A CUDA build of the pinned torch for the local GPU: the only
    order-of-magnitude indexing lever found (estimated, not measured), but
    it needs a new hash-locked runtime variant and a re-index of every
    published run.
  - Globally length-sorted embedding batches: 16-24% less encode time, but
    vectors change by about 1e-7.
  - A warm per-corpus search worker: removes a 35-40 s cold start per query,
    but changes the isolation model.
  - An opt-in `--allow-partial-conversion` escape hatch for the Docling
    fail-closed behavior (proposed, not built).
- **Follow-ups found (not fixed).**
  - Job manager (also at `3aede7d`): when a cancellation-evidence write
    fails, `run_job` raises `ValueError` from `attempt_reporting` after
    `interrupted` and the terminal runtime have committed. With
    `windows-transient-replace-tolerance` this takes a non-transient failure
    or transient ones past the 2 s cancellation budget; that branch does not
    fix it.
  - Embedding (also at `3aede7d`): the `EMBEDDING_MAX_TOKENS` check raises
    after the model is cached, so a retry skips it (fail-open on retry).
    The token counter's bare `except` turns a tampered or missing bundle
    into estimated counts, which can trigger a silent full re-embed (owner
    call).
  - Reranker: since `reranker-single-pass` landed (#157),
    `reranker_scoring.py` replays FlagEmbedding 1.4.2 internals, so every
    FlagEmbedding bump must re-verify it during the lock refresh.
  - Workflow-validator gaps that also exist at `3aede7d`: a line-start BOM,
    `persist-credentials` nested under `env:`, `_yaml_scalar` applying
    Python escapes to quoted path filters (`\x5f`, `\N`), multi-line quoted
    scalars, explicit `?` keys, a `uses:` value on the next line, and flow
    mappings inside flow sequences. `ci-validator-yaml-anchors` also newly
    accepts a `persist-credentials: false` line under a checkout `with:`
    written as a sequence (a `- note: |` entry above it), which `3aede7d`
    rejected. GitHub rejects that shape, so it is not exploitable. Binding
    `persist-credentials` to a direct child of the checkout's `with:`
    mapping would close both it and the `env:` gap.
  - Secret scanner (also at `3aede7d`): rev-list output is split with
    `splitlines()`, so file names containing U+2028 or similar separators
    are truncated; blobs that a ref names directly are skipped; and the #106
    concurrency-group cancellation is unaddressed.
  - Static-security gate: with `static-security-s324-noqa`,
    `ruff --isolated` still honours `.gitignore` and ruff's default
    excludes, so a force-added tracked file under `build/` or `.venv/`
    escapes.
  - Evaluation: the toolkit merged through #163 without a CI step, so
    adding `eval.py --retriever lexical` steps and baselines to CI remains
    a follow-up for the owner. The production
    lexical leg fails the CC0 abstention cases, which CI runs only through
    the offline adapter. `function-words-v1` also drops
    enumerators ("Article I") and the "in" of fixed phrases, and the Qdrant
    sparse query leg applies no lexical policy.
  - Quality (also at `3aede7d`): `validate_quality_report` re-validates the
    oracle registry, and `source_oracle_root_sha256` re-canonicalizes every
    oracle.
  - Governance: `tests/test_legal_tokenizer_differential.py` is not in
    `ci-risk-policy.json`, so it falls in the unknown group and runs the
    full lane.
  - README: it calls pypdfium2 the conversion backend, but Docling uses
    `DoclingParseDocumentBackend` and `--backend` is only recorded.

### Dependency upgrade series (2026-10-02, proposed)

Three stacked one-domain pull requests on `main` `717ac9e` refresh
dependencies under the dependency-domain policy, each with locks
regenerated by `tools/refresh_locks.py` under the pinned uv 0.12.20, and
each carrying its own Phase A0 pair. The first two's hosted checks are
green, the third one's are pending, and none is merged or integrated.
They merge in this order:

- Provider transport ([PR
  #166](https://github.com/toddlar00/rag-pipeline/pull/166), source
  `0b1bbe7`, gate-only child `488c848`): cryptography
  50.0.1 to 50.0.2, lock-only in `requirements-full.lock`. The release
  rebuilds its wheels against OpenSSL 4.0.3 and names no CVE. Its hosted
  checks are green, and it is not merged.
- Service/UI ([PR
  #167](https://github.com/toddlar00/rag-pipeline/pull/167), sources
  `a377dcb` and `f4204f8`, gate-only child `43a93e1`, stacked on #166):
  fastapi 0.142.2 (the service exact pin), gradio 6.29.1 and starlette
  1.7.0, with gradio-client 2.7.2 in the full lock and a new
  opentelemetry-api 1.45.0 in the service lock that fastapi 0.142
  requires. It keeps FastAPI 0.142's native OpenTelemetry off at every app
  the repository launches (the service app and the two Gradio UIs),
  restoring the pre-upgrade behaviour. Its hosted checks are green, and it
  is not merged.
- ML/runtime (the stacked ML/runtime pull request, source `718ee9f`, with
  its own Phase A0 pair, stacked on #167; hosted checks pending): torch
  2.14.0 to 2.14.1 and torchvision 0.29.0 to 0.29.1 in
  `requirements-core.lock` and `requirements-full.lock`, with the matching
  `requirements-audit.txt` versions and `+cpu` pip-audit skip records;
  numpy stays 2.5.2 and transformers 5.16.1. Real-model checks on local
  published runs (fixed queries, plain and reranked; scratch
  re-conversions with the published jobs' arguments; and their stored
  embeddings) found no output change.

Held back unchanged, for the reasons recorded under "Held back" above:
docling (2.132.0 is now available but not evaluated; 2.130 and the 2.131
evaluation in #149 made audited readings worse), numpy 2.5.3, transformers
5.17 and Google GenAI 2 (#88).

Follow-ups found by the Service/UI pull request (not fixed there; both
are outside its domain, and the hardening pull request below proposes a
fix for the first):

- Chroma's own OpenTelemetry trigger: in supervised Chroma workers,
  `CHROMA_API_IMPL=chromadb.api.segment.SegmentAPI` together with
  `CHROMA_OTEL_GRANULARITY`, read from the environment or from a `.env`
  file in the working directory, makes Chroma install a global
  OpenTelemetry SDK tracer provider with a gRPC exporter. Today the client
  then fails only because no lock contains `hnswlib`. It predates the
  upgrade (`main` already locks chromadb 1.5.9 with the SDK and the gRPC
  exporter) and belongs to the `vector_runtime` owner. Proposed fixed, not
  integrated, by `808031a` in the hardening pull request below. That pass
  found a wider hole in the same settings path:
  `CHROMA_API_IMPL=chromadb.api.fastapi.FastAPI` turned the persistent
  client into an HTTP client for `CHROMA_SERVER_HOST`, which would send
  chunk text and embeddings off the machine, and
  `CHROMA_PRODUCT_TELEMETRY_IMPL` or `CHROMA_TELEMETRY_IMPL` named a class
  that Chroma imports and constructs.
- An optional `tools/check_dependency_policy.py` rule rejecting
  `opentelemetry-exporter-otlp-proto-http`,
  `opentelemetry-instrumentation*` and `opentelemetry-distro`, as defence
  in depth: without the telemetry setting, only the HTTP exporter's
  absence from every lock stops FastAPI's environment-driven OTLP export,
  and nothing enforces that absence.

Follow-up found by the ML/runtime pull request (not fixed there; it
predates the upgrade, and `artifact_io.py` is unchanged since `main`
`717ac9e`; the hardening pull request below proposes a fix):

- `artifact_io._read_index_artifact_snapshot_once` calls
  `handle.read(max_bytes + 1)`, which preallocates `max_bytes + 1` bytes
  (256 MiB for the 268,435,456-byte limit) on every bounded snapshot read,
  even for tiny files. At `718ee9f` it caused a transient `MemoryError`
  under `-n 8` on Windows, at setup of
  `tests/test_ocr_context_authoring_io.py::test_commit_rechecks_source_and_predecessor_after_serialization[source]`;
  at the same commit, that module alone and together with
  `tests/test_ocr_review_runtime.py` under `-n 8` then passed. Bound the
  read by the file's stat size (keeping the post-read size and
  fingerprint checks) or read in chunks. Proposed fixed, not integrated,
  by `8b41db7` in the hardening pull request below.
  `quality_core.read_quality_report` had the same pattern with its 16 MiB
  limit, and the same commit fixes it.

### Resource-safety and environment-isolation hardening (2026-10-02, proposed)

The hardening pull request (branch
`agent/robustness-hardening-2026-10`, stacked on the ML/runtime pull
request [#168](https://github.com/toddlar00/rag-pipeline/pull/168)) fixes
the two follow-ups above and the sibling holes found while fixing them.
It changes no dependency, lock, model-artifact lock or dependency policy.
Its source checkpoint is the review follow-up commit in the last bullet
below. Its Phase A0 pair, hosted checks and exact-SHA record are pending,
and none of its changes is merged or integrated. Its source commits:

- Bounded reads (`8b41db7`; behavior-preserving): `artifact_io`'s bounded
  snapshot reader and `quality_core.read_quality_report` size the first
  read from the opened file's size and follow growth in blocks, never
  past the limit. Before, one `read(limit + 1)` made CPython preallocate
  the whole limit: under tracemalloc an 8-byte artifact peaked at
  16,787,398 B with a 16 MiB limit. It now peaks at about 10 KB. The
  bytes, the exceptions and their order are unchanged.
- Chroma settings (`808031a`; intentional fail-closed hardening): the
  settings that every Chroma open shares no longer read a working-directory
  `.env`. Explicit values pin the API implementation (`RustBindingsAPI`),
  both telemetry implementations and the OpenTelemetry endpoint, headers
  and granularity; apart from the existing `anonymized_telemetry=False`,
  each pin is Chroma 1.5.9's own default. Each pin is read back after
  construction, failing closed on a mismatch. Behavior change: `CHROMA_*`
  variables no longer affect those settings. The client's settings read no
  `.env` for any setting, so a store whose sha256 migration hash
  algorithm came from a `.env` must set `MIGRATIONS_HASH_ALGORITHM` in the
  process environment. `import chromadb` still parses a working-directory
  `.env` for its unused module-level defaults, so a malformed value there
  makes the import fail, as before. In a clean environment the settings
  equal the former ones.
- Gemini client (`7fdffad`; intentional fail-closed hardening):
  `_load_gemini_client` passes google-genai 1.75's `DebugConfig` with the
  client mode, replay directory and replay ID set to `None`. Before caching
  a client, it rejects one that still reports a test mode or a replay
  client. Before, `GOOGLE_GENAI_CLIENT_MODE` set to record, replay or auto
  selected the SDK's test-only replay client. In record mode, and in auto
  mode without a replay file, that client printed each request, including
  the `x-goog-api-key` header and the prompt, to standard output, and wrote
  the prompt and response to a plaintext replay file; otherwise it answered
  from a local replay file. Behavior change: `GOOGLE_GENAI_CLIENT_MODE`,
  `GOOGLE_GENAI_REPLAYS_DIRECTORY` and `GOOGLE_GENAI_REPLAY_ID` are
  ignored. An installed SDK without `DebugConfig` fails with
  `configuration_error` before any request. The GenAI 2 decision (#88) is
  untouched.
- Gradio launches (`a6393fa`; intentional fail-closed hardening): gradio
  6.29.1 reads every unset launch argument from the environment, so
  `ui.main` and `tools/review_ocr.main` now pass `run_history=False`,
  `ssr_mode=False` and `root_path=""`, and `ui.main` also
  `mcp_server=False`. Before, both UIs served Gradio's run-history page.
  They also registered `/gradio_api/run-history/*` routes whose Hugging
  Face bucket upload uses the host's saved Hugging Face login for a
  browser on loopback. `GRADIO_MCP_SERVER` would expose the local UI's
  search and job handlers as MCP tools, and `GRADIO_SSR_MODE` started a
  Node front proxy. A full-URL `GRADIO_ROOT_PATH` sent the browser's API
  calls, queries included, to another origin. Gradio also treats an
  explicit `allowed_paths=[]` as unset and serves every directory in
  `GRADIO_ALLOWED_PATHS`. Both launchers therefore refuse a non-empty
  value before building anything, without echoing it. Behavior change:
  both UIs lose the run-history page, and Gradio deletes the runs it saved
  in the browser for these apps. The four variables are ignored (the review
  UI already ignored `GRADIO_MCP_SERVER`), and a non-empty
  `GRADIO_ALLOWED_PATHS` stops either UI from starting.
- Review follow-ups (the commit after `3d43b1d`, which added this entry;
  fail-closed for one variable, otherwise behavior-preserving): both
  launchers also refuse `GRADIO_LOCAL_DEV_MODE` when it is set at all, even
  to an empty value, before building anything and without echoing it. With
  it set, gradio 6.29.1 adds the `null` origin, with credentials, to its
  CORS allow list whatever `strict_cors` says, so a sandboxed or `file:`
  page could read either UI's responses. Behavior change: neither UI starts
  until it is unset. The same commit stops both bounded-read loops at an
  empty block, measures the two tracemalloc tests against a plain read of
  the same file so a large filesystem block size cannot fail them, pins the
  quality reader's wrapping of an `fstat` failure, keeps a developer
  shell's `GRADIO_ALLOWED_PATHS` or `GRADIO_LOCAL_DEV_MODE` from failing
  unrelated launch tests, rewords the Gemini drift error, and corrects the
  wording the reviews flagged.

Each commit carries its characterization tests, regression tests that
fail on its parent and pass on it, and its architecture-inventory
refresh. The three hardening commits and the review follow-ups also
update the release-security ADR, and the Gradio commit and the review
follow-ups also update the README and `docs/ocr-review.md`. The
real-Chroma, real-GenAI and real-Gradio tests skip in the
dependency-light lanes. `full-integration` (Linux) and the local Phase A0
full suites run them. `rag.py` belongs to `vector_runtime`, so the
Chroma and Gemini commits and the review follow-ups need owned-path
review. No governance path changes, and `ui.py` places the pull request
in the `service` risk group, so hosted CI runs the full job set. The
authorization basis is the same as for the merged robustness follow-ups
#150-#165: decision-neutral defect and ADR-conformance fixes. It is not
an A1 performance slice and selects no owner decision.

One option is offered to the owner and not chosen: also pinning Chroma's
`migrations`, `migrations_hash_algorithm` and `allow_reset` (see the
release-security ADR). They still follow the unprefixed `MIGRATIONS`,
`MIGRATIONS_HASH_ALGORITHM` and `ALLOW_RESET` process variables, and a
pinned hash algorithm could lock out a store deliberately created with
another one.

Follow-ups that this pass found or re-examined but did not fix (all
open):

- Durable-job and model fail-closed hardening, intended as the next pull
  request. It has three parts:
  - Route reconciliation's post-transition runtime and attempt-report
    writes, and the terminal-report repair writes, through
    `job_coordination`'s bounded transient-replace retry. These are the
    reconciliation writes still uncovered under "Corpus audit follow-ups".
  - Make the terminal attempt report constructible when a
    cancellation-evidence write fails. Characterize every cancel, timeout
    and manager-error report first.
  - Cache the embedding model only after its `EMBEDDING_MAX_TOKENS`
    check.

  The last two are recorded under "Research improvement pass".
- A test-only privacy-assertion pass:
  - About 20 negative path checks over JSON text, besides the job-manager
    case under "Phase A0", can never fail on Windows, because JSON escapes
    backslashes. Examples are in `tests/test_job_runtime.py`,
    `tests/test_service_runtime.py`, `tests/test_service_api.py` and
    `tests/test_jobs_cli.py`. A probe leaked a private path into
    `CorpusConfig.public_dict` and `JobSummary.as_dict`, and all four
    targeted tests still passed on Windows.
  - The codepoint-leak check in `tests/test_ocr_detection_disposition.py`
    can never fail on any platform, because JSON always escapes its NUL.
  - The fix is one shared helper that checks the raw, JSON-escaped and
    parsed forms, with mutation self-tests. Triage separately any
    Windows-only leak it exposes.
- A security-tooling pass on governance paths, with owned-path review. It
  covers the workflow-validator, secret-scanner and static-security gaps
  under "Research improvement pass", and adds a PyYAML-oracle
  differential test and temporary-repository fixtures. The secret-scanner
  and static-security gaps are latent today: no ref names a blob, and the
  gate checks every tracked Python file.
- The stale-scratch janitor's marker read and the Windows WinError 5 at
  `MoveFileEx` (both under "Phase A0") each need their own review. The
  #165 retry remains the janitor's backstop. NTFS probes showed the
  replace fix needs two halves. `os.replace` fails against any open
  reader, even one opened with `FILE_SHARE_DELETE`. A POSIX-semantics
  rename succeeds only against such a share-delete reader.
- The split-export and AI-export completeness checks in `rag.py` read the
  publication and AI-export receipts and the split manifest whole. They
  check the 4 MiB and 2 MiB bounds only afterwards, or not at all. On a
  40 MiB manifest, `_split_export_complete` peaked at 80 MiB before
  returning False. Route these reads through the bounded snapshot reader
  after characterizing their current error text. This pull request makes
  that reader cheap.
- ONNX Runtime's Windows TraceLogging telemetry is never turned off. No
  first-party code calls `onnxruntime.disable_telemetry_events()`,
  although Docling's RapidOCR and the OCR runtimes create ONNX Runtime
  sessions. The release-security ADR's telemetry row does not list ONNX
  Runtime. The fix is a telemetry-inventory change with an ADR row.
- The post-SIGKILL confirmation window (under "Phase A0") stays a
  separate, characterized change to the frozen supervision core.
- `model_artifacts` reads Hugging Face Hub and PyPI metadata responses
  with `response.read(limit + 1)`, the same pattern, with 10 MiB and 5 MiB
  limits. CPython's `http.client` clips that request to a declared
  `Content-Length` and reads chunked bodies chunk by chunk. Only a body
  delimited by connection close reserves the whole limit. This code
  belongs to the `model_supply_chain` owner.
- `GRADIO_NUM_WORKERS` set to 1 or more starts loopback static-file worker
  processes for the local UI, which has no authentication. Gradio starts
  none when authentication is set, so the review UI is unaffected. The
  workers bind `127.0.0.1` and apply the same allowed and blocked paths,
  but each answers every origin, its upload route included, with
  `Access-Control-Allow-Origin: *`, so the `GRADIO_LOCAL_DEV_MODE` refusal
  does not cover them. Pinning `num_workers=0` in `ui.main` is a small
  follow-up.
- Optional, as a governance change to `ci.yml`: extend
  `vector-store-smoke`'s `-k` selection so the real-Chroma
  hostile-configuration test also runs on hosted Windows. The Windows
  unit lanes there are dependency-light.
- These stay open and unchanged:
  - The optional OTLP dependency-policy rule above. It is a new policy
    rule and needs agreement first.
  - The oracle-root recompute under "Research improvement pass". It is an
    A1 performance slice for its own pull request.
  - Three latent or opportunistic items already recorded:
    `_pipeline_run_is_ready`'s path resolution ("Corpus audit
    follow-ups"), `build_save_feedback`'s gradio import order ("Fresh-OCR
    token fidelity"), and the README's pypdfium2 note ("Research
    improvement pass"). The README note should ride with a source change,
    because a documentation-only merge trips the recorded Phase A0
    push-run issue.

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
The `0333947` pair (gate-only child `63b22e2`) passed the hosted CI
promotion gate on [PR
#139](https://github.com/toddlar00/rag-pipeline/pull/139), which merged into
`main` as `123bedb`. The tokenless-glyph rotation fix source `c2d7b57` lets
the single-wrap reading-order repair and its lineage mirror rotate a page
intact when only detached zero-token glyphs captured with the upper block
break the run-disjointness proof; it changes no dependency or lock and
supersedes the `0333947` pair.
The `c2d7b57` pair (gate-only child `193e202`) passed the hosted CI
promotion gate on [PR
#140](https://github.com/toddlar00/rag-pipeline/pull/140), which merged into
`main` as `6baf14b`. The table header-row oracle fix source `09340ed` makes
the native table row-count oracle count stacked Markdown header rows the way
docling-core 2.99 exports them; it changes no dependency or lock and
supersedes the `c2d7b57` pair.
The `09340ed` pair (gate-only child `1353ffa`) passed the hosted CI
promotion gate on [PR
#145](https://github.com/toddlar00/rag-pipeline/pull/145), which merged into
`main` as `ce73b61`. The soft-hyphen item-seam fix source `a7847ce` joins a
cited item that ends in a hyphen plus soft hyphen to the next cited item
exactly where the fidelity audit would otherwise fuse the two items'
boundary tokens; it changes no dependency or lock and supersedes the
`09340ed` pair.
The `a7847ce` pair (gate-only child `f7dd3fe`) passed the hosted CI
promotion gate on [PR
#146](https://github.com/toddlar00/rag-pipeline/pull/146), which merged into
`main` as `8cc89b0`. The documentation-only source `578a13a` records the
integrated post-series fixes, relabels the sections integrated by #116,
brings the README dependency blocks up to the declared requirements and
records the private excerpt reprocessing; it changes no code, dependency or
lock and supersedes the `a7847ce` pair.
An independent accuracy review of the `578a13a` pair's pull request
([#147](https://github.com/toddlar00/rag-pipeline/pull/147)) approved it
with nits. The documentation source `f3ec23a` addresses them, records the
published private tort-law re-run and supersedes the `578a13a` pair.
The `f3ec23a` pair (gate-only child `df8623e`) passed the hosted CI
promotion gate on [PR
#147](https://github.com/toddlar00/rag-pipeline/pull/147), which merged into
`main` as `9fff7bb`. The test-only source `e5966e1` makes the heartbeat
failure-budget tests independent of runner speed and raises the Node
subprocess ceilings of three OCR review UI tests; it changes no pipeline
behavior, dependency or lock and supersedes the `f3ec23a` pair.
The `e5966e1` pair (gate-only child `b93eb1f`) passed the hosted CI
promotion gate on [PR
#148](https://github.com/toddlar00/rag-pipeline/pull/148), which merged into
`main` as `3aede7d`. The static-security source `3af52f8` retires the only
static-security suppression (S324 on the non-cryptographic MD5
sparse-vector token hash, expiring 2026-11-30) by marking that hash
`usedforsecurity=False`, which keeps its digests byte-identical, so no
index migration or Qdrant re-index is needed. It runs ruff with
`--isolated --ignore-noqa`, so inline, file-level and range suppression
comments and project ruff configuration cannot hide findings, covers every
blocking rule (including S604 and S606) with positive and negative
fixtures, removes the dead `evaluation/` exemption and pins the Hub Git
blob identity to `git hash-object` values. It changes no pipeline output,
dependency or lock and supersedes the `e5966e1` pair.
The `3af52f8` pair (gate-only child `f0513b7`) passed the hosted CI
promotion gate on [PR
#150](https://github.com/toddlar00/rag-pipeline/pull/150), which merged into
`main` as `76f98d6`; the separate networked vulnerability/SBOM jobs failed at
that head on three new urllib3 advisories. The stacked hosted-flake fix source
`6b97fc5` makes three hosted-CI reliability fixes: the Phase A0 contained
runner retries transient Windows errors when it removes its capture
directory, so a WinError 32 no longer masks the primary error; the OCR
review launcher and the crop-pack service clamp the remaining close time
they hand on to the 40-second close bound, which Windows monotonic-clock
rounding could exceed; and the POSIX SIGTERM-escalation test holds its
deadline until the grandchild has installed SIG_IGN and written its first
heartbeat. It changes no pipeline output, dependency or lock and supersedes
the `3af52f8` pair for its own pull request.
The `6b97fc5` pair (gate-only child `f5af79a`) passed the hosted CI
promotion gate on [PR
#151](https://github.com/toddlar00/rag-pipeline/pull/151), which merged into
`main` as `584c4a5`. The provider-transport promotion source `aab0326` makes
urllib3 a governed direct input and moves it from 2.7.0 to 2.8.0
(CVE-2026-97687, CVE-2026-97688 and CVE-2026-97689) in the core, full, test,
service, smoke and security locks, and supersedes the `6b97fc5` pair.
The `aab0326` pair (gate-only child `19b9ef9`) passed the hosted CI
promotion gate on [PR
#152](https://github.com/toddlar00/rag-pipeline/pull/152), which merged into
`main` as `25f9ba1`; the networked vulnerability/SBOM jobs passed at that
head. The stacked query-path speedup source `18a70ab` makes three
output-identical query-path speedups: local reranking scores each pool
without FlagEmbedding 1.4.2's discarded batch-size probe forward pass (one
pass instead of two for pools of up to 128 pairs), with float.hex-identical
scores; the legal lexical
analyzer skips each pass whose required literal is absent from the current
string and puts the leading literal first in its slow patterns; and the
strict chunks parser runs a path-free non-finite scan before its field-path
walk, with identical records and error text. It changes no pipeline output,
dependency or lock and supersedes the `aab0326` pair for its own pull
request.
The `18a70ab` pair (gate-only child `e91d42f`) passed the hosted CI
promotion gate on [PR
#157](https://github.com/toddlar00/rag-pipeline/pull/157), which merged into
`main` as `d3754d5`; the networked vulnerability/SBOM jobs passed at that
head. The stacked model-load speedup source `8ce1a8d` makes two
output-identical model-load speedups: model-artifact verification hashes
each file with `readinto` into one reused 1 MiB buffer instead of
allocating a new block per read, with identical sizes, digests and error
text; and a verified load of the pinned Nomic embedding model skips the
`torch.nn.init` calls that its remote code runs before the verified
checkpoint overwrites them, and caches the model only after a
fail-closed proof that the checkpoint header declares every transformer
state-dict entry at its exact shape and that no parameter lies outside the
transformer, with bitwise-identical weights and embeddings. A checkpoint
that omits a parameter now fails closed instead of silently keeping random
weights. It changes no pipeline output, dependency or lock and supersedes
the `18a70ab` pair for its own pull request.
The `8ce1a8d` pair (gate-only child `33d6a26`) passed the hosted CI
promotion gate on [PR
#158](https://github.com/toddlar00/rag-pipeline/pull/158), which merged into
`main` as `cc39af5`; the networked vulnerability/SBOM jobs passed at that
head. The stacked ingestion speedup source `6fc0c36` makes four
output-identical ingestion and indexing speedups: chunk near-duplicate
removal skips each pair whose trigram-count ratio is already below the
threshold and compares the rest with integer bitsets, with identical
keep/remove decisions and audit stream; source enrichment extracts each
page's sorted native words and the hard-hyphen attestations once per
enrichment call through a snapshot-bound, fail-closed cache that is
cleared when the native text-group stages finish; quality validation
attests each record once, and the index quality binding reads and verifies
the source-oracle registry once instead of twice when it loads the chunk
completion itself; and the embedding token counter keeps one verified
tokenizer resident, still re-verifying its bundle on every call, and counts
in fixed slices of 256 texts, with identical counts. If the registry
sidecar is replaced between what used to be two reads, the binding now
proceeds with the first verified snapshot instead of failing with "source
oracle registry file binding changed". It changes no pipeline output,
dependency or lock and supersedes the `8ce1a8d` pair for its own pull
request.
The `6fc0c36` pair (gate-only child `0d7b128`) passed the hosted CI
promotion gate on [PR
#159](https://github.com/toddlar00/rag-pipeline/pull/159), which merged into
`main` as `139e2dc`; the networked vulnerability/SBOM jobs passed at that
head. The stacked Docling conversion hardening source `18de7d6` makes PDF
conversion fail closed on incomplete results and pins Docling's runtime
knobs against the ambient environment: a Docling result that is not a clean
success, including Docling 2.121's PARTIAL_SUCCESS with the failed pages
re-added empty, now raises `DoclingConversionIncompleteError` with a
bounded, content-free summary before any Docling JSON, Markdown or
conversion manifest is written, instead of publishing; conversion passes
explicit accelerator options (4 threads, CPU or CUDA) and runs inside
Docling's scoped default settings, so `OMP_NUM_THREADS` and `DOCLING_*`
variables no longer change its output or write debug renders; the inert
`DOCLING_PDF_BACKEND` write is removed and the log names the backend
Docling actually uses; and the progress bar counts Docling's profiling
records while their DEBUG flood is dropped at INFO, with `--verbose` output
unchanged. In the default environment, complete conversions keep
byte-identical artifacts and receipt digests. It changes no dependency or
lock and supersedes the `6fc0c36` pair for its own pull request.
The `18de7d6` pair (gate-only child `6b2e3f9`) passed the hosted CI
promotion gate on [PR
#160](https://github.com/toddlar00/rag-pipeline/pull/160), which merged into
`main` as `4994191`; the networked vulnerability/SBOM jobs passed at that
head. The stacked job-manager transient-write tolerance source `9e4d7be`
makes the durable-job manager retry transient Windows replace failures of
its state writes: a write whose two-path atomic replace fails with WinError
5, 32 or 33, exactly as `storage_policy.is_transient_replace_error` accepts,
is retried as a fresh atomic write that repeats every storage identity and
DACL check, with backoff of 0.05, 0.1, 0.25, 0.5 and then 1.0 s until a
monotonic deadline, while any other error still propagates at once; the
writes before the ready marker share 3 s (within the UI's default 5 s
`--job-ready-timeout`), the two cancellation-evidence writes share 2 s, and
each later write (the terminal runtime and attempt report, the pre-launch
cancellation's terminal writes and the closed worker-log cap) has its own
8 s. A held `runtime.json` or `attempt.report.json` therefore no longer
fails a healthy job, or makes `run_job` raise `PermissionError` after the
store has committed `succeeded`, while the hold fits those budgets. Store
commit writes are not retried, the heartbeat keeps its own tolerance, and
the success path makes the same writes in the same order. It changes no
pipeline output, dependency or lock and supersedes the `18de7d6` pair for
its own pull request.
The `9e4d7be` pair (gate-only child `209767c`) passed the hosted CI
promotion gate on [PR
#161](https://github.com/toddlar00/rag-pipeline/pull/161), which merged into
`main` as `d27af15`. The stacked CI and secret-scan tooling source
`e706fd2` makes two fail-closed security-tooling changes. The workflow
security validator (`tools/check_ci_security.py`) now rejects YAML
anchors, aliases, tags and merge keys at every node start, which could
hide unpinned actions and write-all permissions from its line-based
checks; rejects every block-scalar header it cannot blank, and measures a
compact `- key: |` body from the key column; and rejects non-space
indentation, non-ASCII whitespace after a structural line's indentation
and line breaks other than LF and CR, where Python's string handling and
YAML read a line differently. The tracked workflows and the rendered
bootstrap keep identical structural lines and still validate cleanly. The
secret scanner's history mode (`tools/check_secrets.py`) reads the objects
`git rev-list` lists through one lock-step `git cat-file --batch` process
instead of two git processes per object, with identical documents and
findings; a framing fault, a missing or ambiguous reply, trailing output
or a non-zero exit now fails the scan with an error, where the old reader
silently skipped a vanished object. It changes no pipeline output,
dependency or lock and supersedes the `9e4d7be` pair for its own pull
request.
The `e706fd2` pair (gate-only child `78777a8`) passed the hosted CI
promotion gate on [PR
#162](https://github.com/toddlar00/rag-pipeline/pull/162), which merged into
`main` as `40994b4`; the networked vulnerability/SBOM jobs passed at that
head. The stacked lexical accuracy toolkit source `5d22bb9` adds
evaluation-only lexical tooling and changes no default.
`eval.py --retriever lexical` scores each query through the production
lexical leg (`rag._bm25_search`), pinned to the chunks snapshot SHA-256 it
validated, instead of the offline fixture adapter that `--retriever bm25`
uses; its reports record the scorer, the rank-bm25 version and the lexical
query policy, and lexical baselines are strict and never compare silently
with offline-BM25 or index reports. An opt-in, default-off query policy,
`function-words-v1` (a versioned, frozen 49-word function-word list in
`retrieval_core` that keeps modals, negation and conditionals), filters
only the query tokens, keeping order and duplicates and falling back to the
unfiltered tokens when nothing would remain, so no chunk or index changes.
`rag._bm25_search` gains a keyword-only `query_policy` that defaults to
`none` and is validated before any I/O, and only
`eval.py --lexical-query-policy` selects another; the default path is
bitwise identical over 8,000 fuzzed comparisons, and the offline-BM25 and
index report shapes and committed baselines are unchanged. It changes no
pipeline output, dependency or lock and supersedes the `e706fd2` pair for
its own pull request.
The `5d22bb9` pair (gate-only child `9b243c3`) passed the hosted CI
promotion gate on [PR
#163](https://github.com/toddlar00/rag-pipeline/pull/163), which merged into
`main` as `6303d8b`; the networked vulnerability/SBOM jobs passed at that
head. The status-only [PR
#164](https://github.com/toddlar00/rag-pipeline/pull/164) then merged into
`main` as `1c348da` without changing the pair. The robustness follow-up
source `6e7a1e7`, on `main` after #164, fixes three causes of intermittent
test failures, two of them product races. Snapshot cleanup
(`artifact_io._PinnedSnapshotDirectory.unlink_regular`) now retries an
unlink that Windows refuses with WinError 5, 32 or 33 after 0.01, 0.05 and
0.15 s, re-validating the pinned directory and the entry's type, link
count and identity before each retry and re-raising the last attempt's
error unchanged if the hold persists, so cleanup still fails closed: each
snapshot first runs a stale-scratch janitor over the shared scratch root,
and another process's janitor briefly reading a live run's owner marker
without `FILE_SHARE_DELETE` made the owner's unlink fail with WinError 32.
The job-manager test's check that the private attempt report omits the
worker PID now decodes the report and compares its keys and values
structurally instead of searching its JSON text for the PID's digits,
which also matched inside timestamps. The strict job-document reader
(`job_runtime._read_private_json`) re-reads, at most four more times
within about 62 ms, when it observes the signature of a legitimate atomic
replace, so an unleased `get_job` or `load_execution` no longer reports a
racing state transition as `JobCorruptError`; every other violation,
including an in-place change the open descriptor shows during the read,
still fails closed at once, and a replace that persists through every
re-read raises the same error. It changes no pipeline output, dependency
or lock and supersedes the `5d22bb9` pair.
The `6e7a1e7` pair (gate-only child `91db280`) passed the hosted CI
promotion gate on [PR
#165](https://github.com/toddlar00/rag-pipeline/pull/165), which merged into
`main` as `717ac9e`. The provider-transport cryptography 50.0.2 source
`0b1bbe7`, on `main` after #165, supersedes the `6e7a1e7` pair and is the
first of three stacked one-domain dependency upgrades (provider transport,
then Service/UI, then ML/runtime). It is lock-only: under the pinned uv
0.12.20, `tools/refresh_locks.py --upgrade-package cryptography` moves
cryptography from 50.0.1 to 50.0.2 in `requirements-full.lock` and changes
no other record, and a second plain regeneration is byte-stable.
`requirements-optional.txt` already allows `cryptography>=50.0.0,<51`, so
no manifest changes. cryptography 50.0.2 rebuilds its Windows, macOS and
Linux wheels against OpenSSL 4.0.3 (50.0.1 bundled 4.0.2) and names no
CVE. It changes no Python source, pipeline output or model lock.
The `0b1bbe7` pair (gate-only child `488c848`) passed the hosted CI
promotion gate on [PR
#166](https://github.com/toddlar00/rag-pipeline/pull/166), whose hosted
checks are green; that pull request is pending and not merged. The stacked
Service/UI source `f4204f8` supersedes the `0b1bbe7` pair for its own pull
request and is the second of the three stacked one-domain dependency
upgrades. Its first commit, `a377dcb`, moves the exact fastapi pin in
`requirements-service.txt` from 0.141.1 to 0.142.2, while gradio stays
within the `>=6.28.0,<7` range of `requirements-optional.txt`. Under the
pinned uv 0.12.20, `tools/refresh_locks.py --upgrade-package fastapi
--upgrade-package gradio --upgrade-package starlette` moves fastapi to
0.142.2 and starlette from 1.3.1 to 1.7.0 in `requirements-service.lock`
and `requirements-full.lock`, moves gradio from 6.28.0 to 6.29.1 and
gradio-client from 2.7.1 to 2.7.2 in `requirements-full.lock`, and adds
opentelemetry-api 1.45.0, the API package that fastapi 0.142 requires (no
SDK or exporter), to `requirements-service.lock`; no other version moves,
and a second plain regeneration is byte-stable. fastapi 0.142 instruments
every FastAPI app by default and, at lifespan startup, adds OTLP exporters
chosen by `OTEL_*` variables; 0.141.1 had none of this. Its second commit,
`f4204f8`, therefore passes FastAPI's `telemetry` argument with
auto-configuration, tracing, metrics, logs and operation spans all off at
the service app (`service_http.create_app`) and, through Gradio's
`app_kwargs`, at the UI launches in `ui.main` and `tools/review_ocr.main`,
restoring the pre-upgrade behaviour; it adds a FastAPI row to the
release-security policy and six new or updated tests that fail before the
change and pass after it. OSV lists no advisory for the replaced or new
versions. It changes no pipeline output or model lock.
The `f4204f8` pair (gate-only child `43a93e1`) passed the hosted CI
promotion gate on [PR
#167](https://github.com/toddlar00/rag-pipeline/pull/167), whose hosted
checks are green; that pull request is stacked on #166, whose hosted
checks are also green, and neither is merged. The stacked ML/runtime
source `718ee9f` supersedes the `f4204f8` pair for its own pull request
and is the last of the three stacked one-domain dependency upgrades. Under
the pinned uv 0.12.20, `tools/refresh_locks.py --upgrade-package torch
--upgrade-package torchvision` moves torch from 2.14.0 to 2.14.1 and
torchvision from 0.29.0 to 0.29.1 (the `+cpu` records and the macOS
records) in `requirements-core.lock` and `requirements-full.lock` and
changes no other record, and a second plain regeneration is byte-stable.
`requirements-audit.txt` moves the normalized versions that pip-audit
checks for the `+cpu` wheels to torch 2.14.1 and torchvision 0.29.1, and
`dependency-vulnerability-policy.json` moves the two `+cpu`
`allowed_skips` (version and expected reason pattern) to the same
versions, keeping their reasons and expiries. numpy stays 2.5.2 (the OCR
observer recipe pin) and transformers 5.16.1 (below 5.17 for the Nomic
embedding). OSV lists no advisory for torch 2.14.0 or torchvision 0.29.0.
Real-model checks on local published runs found no output change: fixed
queries on three runs, plain and reranked, kept the same results, order
and recorded scores; scratch re-conversions of three runs with their
published jobs' arguments (batch jobs narrowed to the one PDF) reached
READY with identical chunk text (38, 50 and 325 chunks); and their stored
embeddings were bitwise equal to the published ones. It changes no Python source or model lock.
Its Windows/Linux
pair (independent local same-platform comparisons passed) is that branch's
candidate; its hosted checks and exact-SHA record are pending, and the
merged pull requests carry theirs as PR comments.

Four operational follow-ups from the post-merge `main` push runs are
recorded; two remain open, and two were fixed through the robustness
follow-up pull request described below, merged as #165 (`717ac9e`):
a documentation-only merge passes its fast-lane pull-request run but then
fails the forced-heavy `main` push run's ancestor-bound Phase A0 delta
check until the next source checkpoint supersedes the baseline (observed at
`48b47fd`; cured by the `0703dde` checkpoint; a fix path — extending the
gate-only allowed set or classifier-aware push handling — is a
security-owned workflow change requiring its own review); a new hosted
flake in
`tests/test_job_manager.py::test_resumed_attempt_runs_from_bound_submission_directory`,
which failed once in the Python 3.11 Linux unit job of the push run at
`76f98d6` (#150's merge) with `job_runtime.JobCorruptError: job state
changed while being read` (the reader saw the state file change mid-read;
#150 does not touch the job manager, and no later push run through
`40994b4` failed the test), diagnosed as a product race and fixed through
#165; a hosted failure of
`tests/test_job_manager.py::test_run_job_success_persists_private_attempt_and_exact_worker_env`
in the Python 3.11 Linux unit job of the first push run at `cc39af5`
(#158's merge; run 36905319851, later cancelled; a second push run on that
SHA passed), where the check that the worker PID `2932` is absent from the
private attempt report failed and the assertion output shows those digits
inside a float value (`...878534.3229322`), diagnosed as a test-only
false positive and fixed through #165; and
`test_posix_escalation_kills_descendant_that_ignores_sigterm` showed one
cleanup-confirmation flake on a busy hosted runner at `b3c7cf7` (tree
identical to the fully green pull-request run; rerun requested), and the
same cleanup-confirmation failure recurred in the Python 3.14 Linux unit
job of the push run at `d27af15` (#161's merge). The
branch `agent/imp-posix-sigterm-test-deflake`, merged through PR #151 as
`584c4a5`, removes that test's separate startup race but does not address
this cleanup-confirmation flake.
The only diagnosed reproduction was one of two cleanup-confirmation failures
in a variant with a 1.0 s grace: a SIGKILLed grandchild held in
uninterruptible (D-state) sleep 2.66 s past the confirmation window. Two
more appeared on the unchanged test under host load and were not diagnosed.
The proposed fix is a production change in `process_supervision.py` that
separates the post-SIGKILL confirmation window from the SIGTERM grace.

The robustness follow-up source `6e7a1e7` fixes the `JobCorruptError` and
worker-PID failures and the Windows snapshot-cleanup flake recorded under
"Post-series fixes", together in one pull request stacked on `main`
`1c348da` with its own Phase A0 pair. That pair passed the hosted CI
promotion gate on [PR
#165](https://github.com/toddlar00/rag-pipeline/pull/165), and all three
fixes merged into `main` as `717ac9e`. That
flake also failed the Python 3.12 Windows unit shard 2/3 in the first
attempt of the push run at `1c348da` (#164's merge; run 36940890769; the
re-run attempt passed). Each fix was diagnosed with evidence, written
test-first and approved by an independent adversarial review, the
snapshot and job-state fixes after one fix round:
- `JobCorruptError`: `JobStore.get_job` and `load_execution` read
  `spec.json` and `state.json` without the store lease, while every writer
  publishes them by atomic replace, so an unleased reader that overlapped
  a legitimate transition failed closed. Taking the lease in these readers
  would self-deadlock: it is non-reentrant, and `list_jobs`,
  `reconcile_job` and `prepare_delete` already call them under it. Outside
  tests, one lost race marks the service unhealthy until it restarts, and
  `launch_detached`'s post-ready `get_job` can report a successful launch
  as failed. The reader now re-reads on replace signatures only, including
  ext4's reuse of an inode number across consecutive replaces. In a Linux
  harness the unchanged reader failed 4,439 of 521,610 unleased `get_job`
  reads; under 30 CPU burners the final reader had 0 errors in 3,744,311
  reads, where the first-round fix still had 20 in 1,574,432.
- Worker PID: the substring check over the report's JSON text matched the
  PID's digits inside `time.time()` floats or the hex job ID, while the
  report never held the PID. On recorded reports, false failures fall from
  11 of 3,000 (Linux) and 1 of 400 (Windows) to 0.
- Snapshot cleanup: on the owner's Windows host,
  `tests/test_ocr_hardscan_io.py` under `-n 8` failed 5 of 48 runs before
  the retry and 0 of 96 after it.

These fixes found four further follow-ups, all open:
- The stale-scratch janitor could read owner markers with
  `FILE_SHARE_DELETE` on Windows. That deeper fix needs its own review: a
  bounded retry cannot outlast a continuously held handle, which still
  fails closed, while on filesystems without POSIX delete semantics the
  shared-delete read would only move the failure to `rmdir`.
- `job_coordination._read_private_json` (runtime heartbeats, attempt
  reports and the ready marker) and `service_runtime._read_private_json`
  may have the same unleased-reader replace race; neither was
  investigated.
- On Windows, writers can hit WinError 5 at `MoveFileEx` while readers
  hold the target open, exhausting `storage_policy`'s replace retry (about
  210 ms). It is pre-existing, and the re-read neither causes nor fixes it.
- In the same job-manager test, the `environment_marker` and root
  substring checks can never fail on Windows, because JSON escapes
  backslashes; the Linux lanes still enforce them.

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
| Dependabot `ignore` rules for rejected versions | The strict `dependabot.yml` policy admits no `ignore` field, so closed grouped proposals (#141-#144) may reopen | Add no `ignore` rule and do not widen the policy; close a reopened proposal with its recorded reason. |
| Retrieval accuracy and runtime defaults examined by the 2026-09-30 research pass: reranking hybrid results by default, `function-words-v1` as the default query policy, fusion weights, the Qdrant sparse/fusion defects, a CUDA runtime, length-sorted embedding batches, a warm search worker, and a Docling `--allow-partial-conversion` escape hatch | Measured or estimated by that pass, not on the owner's private judged sets (see "Research improvement pass"); none selected | Keep every current default and adopt none of these changes; the Docling fail-closed fix, merged through PR #160, is a robustness change outside this row. Change a default only through its own owner decision and pull request, with a versioned re-index where vectors or indexes change. |

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
| Broader R7 coverage/typing/lint | No | Recorded authorization after the preceding release-readiness gates in the active plan |
| A1 performance work | Yes; authorized by the owner on 2026-09-30 | Each slice is its own pull request with an independent review, output-identity evidence (pipeline artifacts, rankings and scores unchanged) and its own Phase A0 source/evidence pair, plus owned-path review where it touches owned or governance paths. It covers output-identical performance optimizations such as the ten prepared speed branches; the Phase 3 A1a/A1b/A1c capacity benchmarks and timing budgets keep their Phase 3 entry condition. It authorizes nothing else: not R7 or other Phase 3 coverage/typing/lint ratchets, accuracy-default changes, Phase 1 product work, Phase 2 qualification or release, Phase 4 ownership work or any other owner gate. |
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
(workflow-syntax validator anchor/alias rejection) was a checker-hardening
follow-up recorded in the promotion evidence record. The branch
`agent/imp-ci-validator-yaml-anchors` closed it: it merged into `main`
together with `agent/imp-secret-scan-history-batch` through [PR
#162](https://github.com/toddlar00/rag-pipeline/pull/162) as `40994b4`,
where their shared Phase A0 pair at source `e706fd2` passed the hosted CI
promotion gate (see "Research improvement pass (2026-09-30, integrated)").
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
`retrieval_core.py`, whose suppression expires on 2026-11-30). The separate
versioned index-migration follow-up once planned to replace it is superseded
by the branch `agent/imp-static-security-s324-noqa`, which merged through
PR #150 as `76f98d6`:
`md5(..., usedforsecurity=False)` keeps the sparse indices byte-identical,
so the suppression retires without an index migration (see "Research
improvement pass (2026-09-30, integrated)"). This completes the Task 0.6-0.8
gate sequence, while
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
