# Glyph-Truth Triage and Evaluation Significance Design

Date: 2026-08-14
Status: Designed and implemented in an autonomous goal session; validated by
independent subagent review and the full repository gate battery. The owner
review gate is the merge decision, as with the 2026-08-02 scan cycle.
Origin: fresh GitHub scan (2026-08-14) on top of the 2026-08-03 cross-project
survey's recorded research backlog. The scan refreshed upstream movement and
the deferred-candidate list; the selected bundle below is the highest-value
subset implementable at current pins with zero new dependencies.

## Fresh scan findings (2026-08-14)

Upstream movement since the 2026-08-03 survey:

- **Docling 2.118.0-2.120.1** (2.120.1 released 2026-08-14): native PDF
  heading-level inference from font weight, slant, and case (#3984), layout
  label-map normalization fix (#3936), RapidOCR refactor covering all PP-OCR
  languages (#3863), threaded docling-parse shape-rendering fix (#3947).
  Heading-level inference materially strengthens the case for the next
  pdf-docling one-domain PR (Dependabot #91): it gives the TOC scaffold a
  third deterministic cross-check source beyond PDF bookmarks. Not usable at
  the pinned 2.117; recorded for the domain-PR backlog, not this cycle.
- **PyMuPDF 1.28.2**: `find_tables` layout/speed upgrades, markdown
  illegal-UTF-8 hardening. pdf-docling domain-PR candidate.
- **qdrant-client 1.19.0** (2026-08-04): Vector-stores domain-PR candidate.
- **sentence-transformers 5.7.0** (2026-08-06, correctness fixes): supersedes
  the recorded 5.6.1 ML/runtime domain target.
- **rank_bm25 remains unmaintained** (last release 2022): reinforces the
  deferred fusion/BM25 cycle's need to own scoring policy in-repo before any
  behavior change; that cycle additionally needs significance tooling to be
  decidable — provided by item 3 below.

Considered and rejected for this cycle: (a) the BM25/fusion scoring upgrade —
it spans three sparse-retrieval implementations (rank_bm25 hybrid, Qdrant
sparse, offline eval BM25) plus the versioned evaluation contract and
review-bound baseline promotion, and should not precede significance tooling;
(b) any dependency-domain movement — already an ordered ROADMAP backlog with
its own hosted-evidence protocol.

## Purpose

Three zero-new-dependency upgrades, each grounded in the surveyed projects
and closing a recorded gap:

1. A glyph-level decode-correctness pass (PyMuPDF `get_texttrace()`) that
   completes the CID mojibake detector and adds an invisible-text
   (OCR-overlay) provenance signal to triage. (Sources: MinerU classify
   logic; marker/surya provenance heuristics; ROADMAP CID-recall follow-up.)
2. A synthetic broken-cmap fixture that validates the detector's
   true-positive recall — the ROADMAP-recorded precondition for describing
   the detector as fully implementing the 2026-08-03 spec's
   decode-correctness term.
3. Deterministic paired-bootstrap significance for `eval.py --compare`,
   the first slice of the deferred evaluation-robustness pack, making future
   retrieval upgrades statistically decidable.

## Design

### 1. Glyph-truth pass (`get_texttrace`)

- `ingestion_core.analyze_pdf_document` and
  `plan_background_image_removals` already accept injectable per-page
  functions; both gain `page_glyph_stats_fn` following the same pattern,
  so the strip planner applies the same decode-correctness veto to its
  per-page usable-text verdicts and preprocess cannot strip backgrounds
  from glyph-garbled pages. The dependency-free default resolves
  `getattr(page, "get_texttrace", None)`; when absent (existing fakes,
  degraded objects) the glyph pass is skipped and behavior is exactly
  today's. The facade passes real PyMuPDF pages, which have it.
- Per page the pass folds texttrace spans into four counts: total glyphs,
  unmapped glyphs (unicode U+FFFD), `.notdef` glyphs (glyph id 0), and
  invisible-text glyphs (span `type == 3`).
- Decode-correctness extension: a page whose decode-failed glyph share
  exceeds the existing `max_cid_char_ratio` threshold is CID-garbled even
  when its plain-text extraction looks clean. A glyph counts as one
  decode failure when it is unmapped (U+FFFD or PUA), `.notdef`
  (glyph id 0), or both — counted once, never summed across channels, so
  the shared 0.02 threshold keeps the text channel's per-character
  calibration (review finding I1 corrected the original summed formula
  here). The page increments the existing
  `cid_garbled_pages` stat exactly once (union with the text-based term) and
  is excluded from `pages_with_usable_text` — the same
  verdict-changing-by-design semantics the 2026-08-03 spec approved for the
  text-based term.
- Invisible-text provenance (advisory): a page whose invisible share
  `invisible / total glyphs` is at least the new
  `PDFIngestionThresholds.min_invisible_text_share` (default 0.50)
  increments a new `invisible_text_pages` stat. No usability or forecast
  change this round; the counter tells the operator the text layer is an
  OCR overlay (Paper Capture/ABBYY signature), informing `--ocr` decisions.
- Surfacing: `PDFTriage` gains `invisible_text_pages: int = 0`; the scan
  card's Text layer section adds `invisible (OCR-overlay) text: N pages`
  when N > 0. The existing `garbled (CID) pages` line now reflects the
  union detector.
- Failure isolation: a raising texttrace degrades that page to text-only
  detection and records a page-level inspection issue (prefixed
  `glyph trace:` so it is not mistaken for a text-extraction failure); it
  does not flip `inspection_complete` (the text pass still ran; the glyph
  pass is an additional term, and a hard incomplete would change
  preprocess forecasts disproportionately).
- Scope and cost notes: clip-only text (Tr 7) produces no trace spans and
  is outside both counts — Tr 3 is the common OCR-overlay mode. The trace
  pass roughly triples per-page text-inspection time (~2.8x measured on a
  200-page synthetic PDF), paid by both loops; injecting
  `page_glyph_stats_fn` is the opt-out for cost-sensitive callers.

### 2. Broken-cmap recall fixture

- A test-local generator builds a minimal single-page PDF whose text is
  addressed through a Type0/Identity-H font without ToUnicode, with the
  CID payload selecting the failure channel (probe-confirmed under the
  pinned PyMuPDF line): CIDs beyond the substitute font's range extract
  as wrong-but-valid codepoints while the glyph trace reports
  `(U+FFFD, glyph 0)` — the case only the new glyph pass catches; PUA
  CIDs fire the original text channel; low CIDs that map to plausible
  Latin letters are asserted as the residual known miss. The generator is
  pure bytes, deterministic, and committed as test code — no binary
  fixture, respecting the private-source policy's synthetic-fixture
  default.
- Tests assert: the unmapped-CID page passes `cid_suspect_ratio` yet is
  counted in `cid_garbled_pages` and fails usability (glyph channel
  recall); the PUA page fires the text channel; the plausible-Latin page
  stays uncounted and is recorded as the open follow-up.
- Real-corpus false-positive re-probe: rerun the page-level union detector
  across the local private casebook PDFs (the population behind the recorded
  3,838-page zero-hit probe) and record counts-only results in the ROADMAP
  follow-up closure. No corpus text is committed.

### 3. Paired bootstrap significance for eval compare

- `evaluation_metrics.paired_bootstrap` (stdlib-only, like the module):
  given paired per-query metric values for configurations A and B, a fixed
  seed, and a resample count, returns the observed mean delta, the
  percentile bootstrap confidence interval of the mean delta, and the
  two-sided bootstrap p-value (achieved significance level of the null
  delta, with (b+1)/(B+1) smoothing so the floor is 1/(resamples+1)),
  all deterministic for a given seed. The significance block records its
  `comparison_count` and declares the markers as uncorrected per-metric
  tests, both in the JSON and as a printed footer — readers of a default
  compare run (3 candidates x 6 metrics) must weigh the multiplicity.
- `eval.py --compare --bootstrap`: after the existing four-configuration
  run, for each non-baseline configuration versus the `Vector only`
  baseline and each displayed metric, pair per-query values from
  `query_details` (matched by query identity, not order alone), compute the
  bootstrap block (default 2000 resamples, fixed documented seed), print a
  compact delta table with CIs and significance markers, and attach an
  additive `significance` object to the compare JSON report.
- Without `--bootstrap`, output and the JSON report are byte-identical to
  today's; `REPORT_SCHEMA_VERSION` does not change, and
  `evaluation_review`'s compare-report consumption (which reads only the
  established keys) is unaffected. Queries with errors or missing paired
  values exclude the pair and record the exclusion count in the block.

## Error handling summary

Item 1's glyph term changes usability verdicts by design through the same
threshold machinery as the approved text term; its infrastructure failures
degrade to the text-only detector with recorded issues. Item 2 is test-only.
Item 3 is opt-in and cannot alter default outputs; inside `--bootstrap` it
fails closed on malformed pairing (a config without per-query details yields
an explicit `unavailable` marker rather than a fabricated number).

## Testing

- Item 1: fake-page texttrace matrices in `tests/test_ingestion_core.py`
  (clean page, unmapped-heavy page, notdef-heavy page, invisible-overlay
  page, boundary at both thresholds, absent-`get_texttrace` degrade,
  raising-texttrace issue path, no-double-count union case, strip-plan
  glyph veto); scan-card rendering tests for the new line and `PDFTriage`
  field.
- Item 2: fixture-generation probe test with real PyMuPDF (import guarded
  the same way as existing real-PyMuPDF tests), detector-union assertion,
  analysis-loop and card integration.
- Item 3: unit tests for determinism (same seed, same result), a known
  hand-computable case, degenerate inputs (empty pairs, identical pairs),
  exclusion accounting; CLI tests that `--bootstrap` requires compare mode,
  that compare output without the flag is unchanged, and that the flag adds
  the block (driven through a stubbed `evaluate`).
- Full repository gate battery green (pytest, ruff, compile, policy gates,
  offline eval suites unchanged, architecture inventory refresh).

## Out of scope (recorded research backlog, refreshed)

- BM25/fusion scoring cycle (now explicitly sequenced after this cycle's
  significance tooling), contextual retrieval, remaining evaluation
  robustness items (hard negatives, α-nDCG, RGB counterfactual suites,
  LegalBench IRAC slices).
- XY-Cut++ reading order, footnote-region classification/linking, olmOCR
  anchoring/error budgets, per-block OCR repair, graphics-op density
  pre-filter, PyMuPDF `get_layout()` header/footer classification.
- Confidence-based gating, receipt schema changes, any new dependency, and
  all dependency-version movement (ordered one-domain PR backlog; targets
  refreshed above).
