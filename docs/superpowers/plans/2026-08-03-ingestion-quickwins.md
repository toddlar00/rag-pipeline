# Ingestion Quick Wins Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the four zero-new-dependency ingestion upgrades of
`docs/superpowers/specs/2026-08-03-ingestion-quickwins-design.md`: CID
mojibake detection in the usability thresholds and scan card, per-region OCR
as the new `--ocr` default (with `--ocr-full-page` escape hatch and a
receipts-invalidating schema bump), an advisory PDF-bookmark cross-check for
the TOC scaffold, and surfacing Docling's native conversion confidence.

**Architecture:** Pure policy lands in `ingestion_core.py` (CID threshold
term, stats key, triage field); `rag.py` gains the OCR-mode wiring, the
bookmark comparison helper plus its chunk-stage hook, and the confidence
summary; telemetry is threaded as optional `telemetry=None` kwargs so
existing callers and tests are untouched. All four items follow existing
seams verified by recon (exact line references below; trust the code if
drifted).

**Tech Stack:** Pinned Docling 2.117.0 (`OcrMode` enum and
`RapidOcrOptions.mode` verified present; `ConversionResult.confidence` is a
`docling.datamodel.base_models.ConfidenceReport`), PyMuPDF, pytest. No new
dependencies.

## Global Constraints

- Branch off `agent/ingestion-quickwins-spec`; never commit to `main`.
- NEVER hand-edit `architecture-inventory.json` (tool refresh only), any
  `*.lock`, `benchmarks/`, `.github/workflows/`, or `ROADMAP.md`. Claim no
  hosted or Phase A0 result; the owner's machine runs the evidence ritual.
- `ingestion_core.py` stays standard-library-only (subprocess isolation
  test enforces it).
- Receipt-bound behavior fails closed; advisory signals (bookmark
  cross-check, confidence) degrade to logged notices and never fail a run.
- Telemetry metrics accept ONLY int/bool/None/finite-float values and
  identifier-safe keys (run_telemetry.py:102-127); never put strings,
  lists, or page-number lists into metrics — counts and means only.
- Log idiom: `log.warning("... %s", value)` multi-arg style for
  interpolated warnings (match rag.py:6714-6717); f-strings only where the
  surrounding code already uses them for `log.info`.
- Gates before final commit: `python -m pytest -q`, `python -m ruff
  check .`, `python tools/check_python_sources.py`,
  `python tools/check_dependency_policy.py`,
  `python tools/check_model_artifacts.py`,
  `python tools/check_architecture_inventory.py` (after `--refresh`), all
  three offline eval suites unchanged.

## Verified seam map (from recon; line numbers may drift — trust the code)

- `_conversion_parameters(*, batch_size_override, backend, auto_preprocess,
  ocr, watermark) -> dict` at rag.py:7064-7077; 8 keys incl. `"ocr"` and
  `"force_full_page_ocr": ocr is True`;
  `CONVERSION_COMPLETION_SCHEMA_VERSION = 2` at rag.py:117; digest via
  `_artifact_parameters_sha256`; call sites rag.py:7281, :29494,
  tools/benchmark_phase_a0.py:1115, tests/test_output_publication.py:242-325.
- `_configure_docling_model_artifacts(pipeline_options, *, include_ocr,
  force_full_page_ocr=False, security_policy=None) -> Path` at
  rag.py:7094-7141; builds `RapidOcrOptions(backend="onnxruntime", ...,
  force_full_page_ocr=force_full_page_ocr)`; sole production caller
  rag.py:7524-7527 inside `_convert_pdf_generation` (options built
  :7493-7520; tri-state OCR decision :7389-7410: True→forced full page,
  None→auto via `not _pdf_text_layer_is_usable(stats)`, False→off).
- `--ocr` defined by nested `add_ocr_flag(p)` (rag.py:30588-30594,
  BooleanOptionalAction, default None) on `p_conv`/`p_full`/`p_batch`;
  kwarg chain `convert_pdf`(:7229)→`_convert_pdf_locked`(:7268)→
  `_convert_pdf_generation`(:7374). Resume-command mapping at
  cli_policy.py:135-139.
- TOC scaffold acceptance: `_build_scaffold(...) -> list[dict]`
  (rag.py:3445-3458; entries carry `level`,`title`,`page`,`chapter_num`,
  `path`); hook at its single caller inside `chunk_document`
  (rag.py:19687-19696). Source-PDF access there ONLY via the
  `_open_docling_source_pdf_snapshot(doc_path, binding, *,
  explicit_source_pdf=None)` contextmanager (rag.py:10261) following the
  warn-and-continue precedent `_recover_bound_toc_cell_repairs`
  (rag.py:14641-14662); `conversion_binding` in scope at :19660-19664.
- Telemetry: `RunTelemetry.stage_observation(stage, *, metrics=None)`
  (run_telemetry.py:685); production example rag.py:31600-31603; neither
  `convert_pdf` nor `chunk_document` currently accepts a telemetry object —
  this plan threads optional kwargs (default None).
- CID insertion point: ingestion_core.py:442-452 (`text =
  page.get_text("text") or ""` … `replacement_chars`); `PDFImageStats`
  keys at :55-66; `PDFIngestionThresholds` at :41-50; rag constants at
  rag.py:314-318 + `_pdf_ingestion_thresholds` :709-718. Parity gate:
  tests/test_ingestion_facades.py:361-386 requires `preprocess_pdf.analyze_pdf`
  (preprocess_pdf.py:78-90) to carry any new stats key.
- Scan card: `PDFTriage` (ingestion_core.py:228-244, 13 fields),
  `assess_pdf_triage` (:271-300), `_render_pdf_triage` Text-layer section
  rag.py:6753-6777 (insert after the `sample_read_errors` line :6758-6760),
  `scan_pdf` stats flow :6843-6850, `_stats` test helper
  tests/test_scan_cli.py:112-120.
- Docling confidence (probed on the pinned 2.117.0): `result.confidence`
  is `ConfidenceReport(parse_score, layout_score, table_score, ocr_score,
  pages: dict[int, PageConfidenceScores])` with computed `mean_grade`,
  `low_grade`, `mean_score`, `low_score`; grades are
  `QualityGrade.{POOR,FAIR,GOOD,EXCELLENT,UNSPECIFIED}`; scores are NaN
  when unspecified — every consumer must NaN-guard (`math.isnan`).
- Conversion wiring test template:
  tests/test_ingestion_safety.py:336-435
  (`test_explicit_ocr_alone_forces_full_page_rapidocr`; fake docling via
  sys.modules ModuleType stubs :384-392, `capture_options` attribute
  monkeypatch :403-413 — its keyword-only signature MUST be extended in
  the same change as any `_configure_docling_model_artifacts` kwarg);
  CLI tri-state test :442-457; RapidOCR matrix
  tests/test_model_artifacts.py:1278-1318; parameters-digest template
  tests/test_output_publication.py:318-336.

## File structure

- Modify: `ingestion_core.py` (CID threshold/stats/usability + triage field)
- Modify: `preprocess_pdf.py` (stats parity)
- Modify: `rag.py` (constants, thresholds fn, OCR mode wiring, parameters
  + schema bump, CLI flags, bookmark helper + hook, confidence summary,
  scan card line)
- Modify: `cli_policy.py` (resume mapping for `--ocr-full-page`)
- Modify: `tools/benchmark_phase_a0.py` + gate-pinned tests only where
  their own failure messages instruct (schema-version references)
- Tests: `tests/test_ingestion_core.py`, `tests/test_scan_cli.py`,
  `tests/test_ingestion_safety.py`, `tests/test_model_artifacts.py`,
  `tests/test_output_publication.py`, `tests/test_scaffold_structure.py`,
  new `tests/test_conversion_confidence.py`

---

### Task 1: CID mojibake detection policy

**Files:**
- Modify: `ingestion_core.py:41-66` (threshold field, stats key),
  `:172-183` (per-page term), `:425-452` (gathering)
- Modify: `preprocess_pdf.py:78-90` (parity)
- Modify: `rag.py:314-318` + `:709-718` (constant + thresholds fn)
- Test: `tests/test_ingestion_core.py`, `tests/test_ingestion_facades.py`
  (only if its parity fixture needs the new key), `tests/test_scan_cli.py`
  (`_stats` helper)

**Interfaces:**
- Produces: `PDFIngestionThresholds.max_cid_char_ratio: float = 0.02`;
  `cid_suspect_ratio(text: str) -> float` (module-level pure helper:
  count of U+FFFD plus Private-Use-Area U+E000–U+F8FF characters over
  `max(len(text), 1)`); `pdf_page_text_is_usable` additionally requires
  `cid_suspect_ratio(text) <= thresholds.max_cid_char_ratio`; new
  `PDFImageStats` key `cid_garbled_pages: int` (pages whose
  `cid_suspect_ratio` exceeds the threshold), gathered in
  `analyze_pdf_document` and initialized to 0; rag constant
  `_MAX_CID_CHAR_RATIO = 0.02` wired into `_pdf_ingestion_thresholds`.
- Mechanism note (spec fidelity): the spec names `rawdict` glyph
  inspection; this plan implements the same decode-correctness goal via
  PUA/replacement counting in the already-extracted page text — PyMuPDF
  maps unmapped glyphs into exactly those ranges — avoiding a second
  per-page extraction and a Protocol change. If calibration on a real
  garbled fixture shows this misses broken-cmap pages, escalate before
  substituting rawdict.

- [ ] **Step 1: Write the failing tests** (append to
  `tests/test_ingestion_core.py`)

```python
def test_cid_suspect_ratio_counts_replacement_and_pua():
    assert ingestion_core.cid_suspect_ratio("") == 0.0
    assert ingestion_core.cid_suspect_ratio("clean text") == 0.0
    assert ingestion_core.cid_suspect_ratio("ab��") == 0.5
    pua = ""
    assert ingestion_core.cid_suspect_ratio(pua) == 1.0


def test_page_text_usability_rejects_dense_mojibake():
    dense_garbled = "" * 200
    dense_clean = "a" * 200
    assert not ingestion_core.pdf_page_text_is_usable(dense_garbled)
    assert ingestion_core.pdf_page_text_is_usable(dense_clean)
    lenient = ingestion_core.PDFIngestionThresholds(max_cid_char_ratio=1.0)
    assert ingestion_core.pdf_page_text_is_usable(
        dense_garbled, thresholds=lenient)


def test_analyze_pdf_document_counts_cid_garbled_pages():
    document = FakeDocument([
        FakePage("clean readable text " * 5, []),
        FakePage("" * 100, []),
    ])
    analysis = ingestion_core.analyze_pdf_document(document)
    assert analysis.stats["cid_garbled_pages"] == 1
    assert analysis.stats["pages_with_usable_text"] == 1
```

(`FakeDocument`/`FakePage` already exist at the top of this test file —
match their constructor signatures when writing the test; adjust the calls
if the fakes take different arguments, keeping the assertions.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_ingestion_core.py -q -k cid`
Expected: FAIL with `AttributeError: ... cid_suspect_ratio`.

- [ ] **Step 3: Implement.** In `ingestion_core.py`: add the field to the
  frozen dataclass (after `max_replacement_char_ratio`):

```python
    max_cid_char_ratio: float = 0.02
```

Add the pure helper near `pdf_page_text_is_usable`:

```python
def cid_suspect_ratio(text: str) -> float:
    """Share of characters that decode as unmapped-glyph placeholders."""
    if not text:
        return 0.0
    suspect = sum(
        1 for character in text
        if character == "�" or "" <= character <= ""
    )
    return suspect / len(text)
```

Extend `pdf_page_text_is_usable`'s return expression with
`and cid_suspect_ratio(text) <= thresholds.max_cid_char_ratio`. In
`analyze_pdf_document`: initialize `"cid_garbled_pages": 0` beside the
other counters and, in the per-page text block (where `replacement_chars`
accumulates), add:

```python
            if (cid_suspect_ratio(text)
                    > thresholds.max_cid_char_ratio):
                stats["cid_garbled_pages"] += 1
```

Add `cid_garbled_pages: int` to the `PDFImageStats` TypedDict. In
`preprocess_pdf.py`'s `analyze_pdf`, carry the key through unchanged (it
arrives via `analyze_pdf_document`; verify the parity test passes). In
`rag.py`: add `_MAX_CID_CHAR_RATIO = 0.02` beside the other five constants
and pass `max_cid_char_ratio=_MAX_CID_CHAR_RATIO` in
`_pdf_ingestion_thresholds`. Update `tests/test_scan_cli.py`'s `_stats`
helper to include `"cid_garbled_pages": 0`.

- [ ] **Step 4: Run the affected suites**

Run: `python -m pytest tests/test_ingestion_core.py tests/test_ingestion_facades.py tests/test_scan_cli.py tests/test_ingestion_safety.py -q`
Expected: all PASS (the facades parity test proves preprocess_pdf carries
the new key).

- [ ] **Step 5: Lint and commit**

```bash
python -m ruff check ingestion_core.py preprocess_pdf.py rag.py tests/test_ingestion_core.py tests/test_scan_cli.py
git add ingestion_core.py preprocess_pdf.py rag.py tests/test_ingestion_core.py tests/test_scan_cli.py tests/test_ingestion_facades.py
git commit -m "Detect CID-garbled text layers in page usability"
```

---

### Task 2: Scan card CID line

**Files:**
- Modify: `ingestion_core.py:228-300` (`PDFTriage` + `assess_pdf_triage`)
- Modify: `rag.py:6753-6761` (`_render_pdf_triage` Text-layer section)
- Test: `tests/test_ingestion_core.py`, `tests/test_scan_cli.py`

**Interfaces:**
- Consumes: Task 1's `cid_garbled_pages` stats key.
- Produces: `PDFTriage.cid_garbled_pages: int = 0` (appended with a
  default, keeping existing positional constructions valid);
  `assess_pdf_triage` populates it via
  `int(stats.get("cid_garbled_pages", 0) or 0)`; the card prints
  `  garbled (CID) pages: N` immediately after the
  `unreadable sampled pages` conditional and only when N > 0.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ingestion_core.py`:

```python
def test_triage_carries_cid_garbled_pages():
    stats = _triage_stats()
    stats["cid_garbled_pages"] = 4
    assert _assess(stats).cid_garbled_pages == 4
    assert _assess(_triage_stats()).cid_garbled_pages == 0
```

Append to `tests/test_scan_cli.py` (its `_triage` helper builds
`PDFTriage` from a values dict — add `"cid_garbled_pages": 0` to that
helper's defaults in the same edit):

```python
def test_render_card_shows_cid_garbled_line_only_when_present():
    with_garbled = rag._render_pdf_triage(
        _triage(cid_garbled_pages=3), Path("book.pdf"), file_size=1,
        producer="", creator="")
    without = rag._render_pdf_triage(
        _triage(), Path("book.pdf"), file_size=1, producer="", creator="")
    assert "garbled (CID) pages: 3" in with_garbled
    assert "garbled (CID)" not in without
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_ingestion_core.py tests/test_scan_cli.py -q -k cid`
Expected: FAIL (`TypeError` on the unknown field / missing card line).

- [ ] **Step 3: Implement.** Append `cid_garbled_pages: int = 0` as the
  last `PDFTriage` field; populate it in `assess_pdf_triage`'s return. In
  `_render_pdf_triage`, after the `sample_read_errors` conditional:

```python
    if triage.cid_garbled_pages:
        lines.append(
            f"  garbled (CID) pages: {triage.cid_garbled_pages}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_ingestion_core.py tests/test_scan_cli.py -q`
Expected: all PASS.

- [ ] **Step 5: Lint and commit**

```bash
python -m ruff check ingestion_core.py rag.py tests/test_ingestion_core.py tests/test_scan_cli.py
git add ingestion_core.py rag.py tests/test_ingestion_core.py tests/test_scan_cli.py
git commit -m "Surface CID-garbled pages on the scan card"
```

---

### Task 3: Per-region OCR default with `--ocr-full-page`

**Files:**
- Modify: `rag.py` — `add_ocr_flag` region (:30588-30594) + the three
  parsers, dispatch kwargs for convert/full/batch, `convert_pdf` →
  `_convert_pdf_locked` → `_convert_pdf_generation` chain (`ocr_full_page:
  bool = False` kwarg), `_configure_docling_model_artifacts` (new
  `ocr_mode` selection), `_conversion_parameters` (+`"ocr_mode"` key),
  `CONVERSION_COMPLETION_SCHEMA_VERSION` 2 → 3 (rag.py:117)
- Modify: `cli_policy.py:135-139` (resume mapping emits `--ocr-full-page`)
- Modify: `tools/benchmark_phase_a0.py:1115,:1135` and
  `tests/test_output_publication.py` schema references ONLY as their own
  failures instruct
- Test: `tests/test_ingestion_safety.py`, `tests/test_model_artifacts.py`,
  `tests/test_output_publication.py`, `tests/test_cli_resume.py` (or
  wherever cli_policy's resume mapping is tested — locate with
  `grep -rn "no-ocr" tests/`)

**Interfaces:**
- Produces the new semantics:
  - `--ocr` (True) → OCR enabled, `RapidOcrOptions.mode =
    OcrMode.PDF_AWARE_LAYOUT_REGIONS`, `force_full_page_ocr=False`.
  - `--ocr --ocr-full-page` (or `--ocr-full-page` alone, which implies
    `--ocr`) → OCR enabled, `mode = OcrMode.FULL_PAGE`,
    `force_full_page_ocr=True` (legacy behavior).
  - `None` (auto) with unusable text layer → OCR enabled with
    `mode = OcrMode.PDF_AWARE_LAYOUT_REGIONS` (auto now benefits too).
  - `--no-ocr` → unchanged.
  - `_configure_docling_model_artifacts` gains keyword-only
    `ocr_full_page: bool = False` replacing the old `force_full_page_ocr`
    call-through semantics: internally it sets both `mode` and
    `force_full_page_ocr` per the table above. Import `OcrMode` inside the
    function beside the existing `RapidOcrOptions` import.
  - `_conversion_parameters` gains keyword-only `ocr_full_page: bool =
    False` and emits `"ocr_mode": "off" if ocr is False else ("full-page"
    if ocr_full_page else "pdf-aware-layout-regions")` while keeping the
    existing `"ocr"` key and changing `"force_full_page_ocr"` to
    `ocr_full_page` (no longer `ocr is True`).
  - Schema constant bumps to 3 — all existing conversion receipts fail
    closed by design.

- [ ] **Step 1: Write/extend the failing tests.** In
  `tests/test_ingestion_safety.py`, extend the parametrized matrix at
  :336-343 and `capture_options` (:403-413) — the capture callback's
  signature becomes `capture_options(options, *, include_ocr,
  ocr_full_page, security_policy)` and records `options.ocr_options.mode`
  once configured (capture AFTER the RapidOcr wiring by monkeypatching
  nothing extra: assert on the recorded kwargs plus, in
  `tests/test_model_artifacts.py`'s matrix at :1278-1318, on the real
  `options.ocr_options.mode`/`force_full_page_ocr` values). New matrix
  rows (ocr, ocr_full_page, usable_text → expected_enabled,
  expected_full_page):

```python
    (True, False, True, True, False),    # --ocr → region mode
    (True, True, True, True, True),      # --ocr --ocr-full-page → legacy
    (None, False, False, True, False),   # auto-enable → region mode
    (False, False, False, False, False), # --no-ocr
```

  In `tests/test_model_artifacts.py` extend the
  `_configure_docling_model_artifacts` parametrization to assert:

```python
    # ocr_full_page=False
    assert options.ocr_options.mode is OcrMode.PDF_AWARE_LAYOUT_REGIONS
    assert options.ocr_options.force_full_page_ocr is False
    # ocr_full_page=True
    assert options.ocr_options.mode is OcrMode.FULL_PAGE
    assert options.ocr_options.force_full_page_ocr is True
```

  (import `OcrMode` from `docling.datamodel.pipeline_options` inside the
  test, guarded by the file's existing docling-availability pattern). In
  `tests/test_output_publication.py`, copy the digest-invalidation
  template at :318-336 into a new
  `test_conversion_parameters_invalidate_pre_ocr_mode_receipts` asserting
  a parameters dict WITHOUT `ocr_mode` hashes differently from the current
  one. In `tests/test_ingestion_safety.py`'s CLI test region (:442-457),
  add `--ocr-full-page` forwarding cases (flag alone implies OCR; flag
  plus `--ocr` identical). Locate the cli_policy resume-mapping test via
  `grep -rn "no-ocr" tests/` and add the `--ocr-full-page` round-trip
  case there.

- [ ] **Step 2: Run to verify failures**

Run: `python -m pytest tests/test_ingestion_safety.py tests/test_model_artifacts.py tests/test_output_publication.py -q -k "ocr or conversion_parameters"`
Expected: new/extended cases FAIL (unknown kwarg / missing flag / missing
key).

- [ ] **Step 3: Implement** exactly per the Interfaces block: CLI helper
  below `add_ocr_flag`:

```python
    def add_ocr_full_page_flag(p):
        p.add_argument(
            "--ocr-full-page",
            action="store_true",
            help="Force legacy whole-page OCR instead of layout-aware "
                 "region OCR (implies --ocr)",
        )
```

  applied to the same three parsers; dispatch normalizes
  `ocr = True if getattr(args, "ocr_full_page", False) and args.ocr is None
  else args.ocr` before forwarding both kwargs. Thread `ocr_full_page`
  through the three-function conversion chain; in
  `_convert_pdf_generation`'s decision block keep the tri-state logic but
  set `force_full_page` from `ocr_full_page` only. In
  `_configure_docling_model_artifacts`:

```python
        from docling.datamodel.pipeline_options import (
            OcrMode, RapidOcrOptions)
        ...
        mode = (OcrMode.FULL_PAGE if ocr_full_page
                else OcrMode.PDF_AWARE_LAYOUT_REGIONS)
        pipeline_options.ocr_options = RapidOcrOptions(
            ...,  # existing arguments unchanged
            mode=mode,
            force_full_page_ocr=ocr_full_page,
        )
```

  Update `_conversion_parameters` and bump
  `CONVERSION_COMPLETION_SCHEMA_VERSION = 3`; follow every resulting test
  or tool failure message to its schema-reference site
  (tools/benchmark_phase_a0.py and test_output_publication's pinned
  values) and update the reference — never weaken an assertion. Update
  cli_policy.py:135-139 so resume commands emit `--ocr-full-page` when the
  stored parameters carry `"ocr_mode": "full-page"`.

- [ ] **Step 4: Run the affected suites**

Run: `python -m pytest tests/test_ingestion_safety.py tests/test_model_artifacts.py tests/test_output_publication.py tests/test_cli_run_telemetry.py -q`
Expected: all PASS. Then run whichever test file Step 1's grep located for
cli_policy resume mapping.

- [ ] **Step 5: Lint and commit**

```bash
python -m ruff check rag.py cli_policy.py tools/benchmark_phase_a0.py
git add rag.py cli_policy.py tools/benchmark_phase_a0.py tests/
git commit -m "Default --ocr to layout-aware region OCR

--ocr now selects OcrMode.PDF_AWARE_LAYOUT_REGIONS; the legacy forced
whole-page behavior moves behind --ocr-full-page. Conversion parameters
record ocr_mode and the completion schema bumps to 3 so existing
receipts fail closed."
```

---

### Task 4: Advisory bookmark cross-check for the TOC scaffold

**Files:**
- Modify: `rag.py` — new pure helper `_bookmark_scaffold_warnings` near
  `_build_scaffold`, hook inside `chunk_document` after
  `_repair_bare_scaffold_section_markers` (rag.py:19687-19696), optional
  `telemetry` kwarg on `chunk_document` threaded from its call sites that
  already hold a `RunTelemetry` (locate them via
  `grep -n "chunk_document(" rag.py` — pass `telemetry=telemetry` in
  `_run_full_pipeline` and `telemetry=run_telemetry` in `main`'s chunk
  dispatch if a handle exists there; otherwise leave that site as None)
- Test: `tests/test_scaffold_structure.py`

**Interfaces:**
- Produces:

```python
def _bookmark_scaffold_warnings(
        outline: list[tuple[int, str, int]],
        scaffold: list[dict]) -> list[str]
```

  Pure comparison; normalization for both sides is
  `re.sub(r"\s+", " ", title).strip().casefold()`. Rules (each producing
  one warning string naming both sides):
  - a bookmark title matching a scaffold title but with different `level`
    → `"bookmark level mismatch: {title!r} bookmark level {n} vs
    scaffold level {m}"`
  - a level-1 bookmark title with no scaffold title match →
    `"bookmark chapter missing from scaffold: {title!r}"`
  - a scaffold `level == 1` title with no bookmark title match →
    `"scaffold chapter missing from bookmarks: {title!r}"`
  Empty outline → empty list (callers skip at zero cost).
- Hook behavior in `chunk_document`: open the source PDF via
  `_open_docling_source_pdf_snapshot(doc_path, conversion_binding)`
  following the `_recover_bound_toc_cell_repairs` warn-and-continue
  precedent (any failure or a None snapshot → single
  `log.warning("Bookmark cross-check skipped: %s", reason)` and continue);
  read `doc.get_toc()`; call the helper; emit each warning via
  `log.warning("%s", warning)`; if a telemetry handle is present, emit ONE
  `telemetry.stage_observation("chunk", metrics={
  "bookmark_entries": len(outline), "bookmark_warnings": len(warnings)})`
  — counts only, never titles.

- [ ] **Step 1: Write the failing tests** (append to
  `tests/test_scaffold_structure.py`)

```python
def test_bookmark_scaffold_warnings_matrix():
    scaffold = [
        {"level": 1, "title": "Chapter 1  The Courts", "page": 1,
         "chapter_num": 1, "path": "Chapter 1 The Courts"},
        {"level": 2, "title": "A. Jurisdiction", "page": 3,
         "chapter_num": 1, "path": "Chapter 1 > A. Jurisdiction"},
    ]
    outline = [
        (1, "Chapter 1 The Courts", 1),
        (1, "A. Jurisdiction", 3),
        (1, "Chapter 9 Remedies", 200),
    ]
    warnings = rag._bookmark_scaffold_warnings(outline, scaffold)
    assert any("level mismatch" in w and "A. Jurisdiction" in w
               for w in warnings)
    assert any("missing from scaffold" in w and "Chapter 9 Remedies" in w
               for w in warnings)
    assert rag._bookmark_scaffold_warnings([], scaffold) == []


def test_bookmark_scaffold_warnings_flags_scaffold_only_chapters():
    scaffold = [{"level": 1, "title": "Chapter 2 Contracts", "page": 30,
                 "chapter_num": 2, "path": "Chapter 2 Contracts"}]
    outline = [(1, "Chapter 1 Torts", 1)]
    warnings = rag._bookmark_scaffold_warnings(outline, scaffold)
    assert any("missing from bookmarks" in w and "Chapter 2 Contracts" in w
               for w in warnings)
    assert any("missing from scaffold" in w and "Chapter 1 Torts" in w
               for w in warnings)
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_scaffold_structure.py -q -k bookmark`
Expected: FAIL with `AttributeError`.

- [ ] **Step 3: Implement the pure helper**

```python
def _bookmark_scaffold_warnings(outline, scaffold):
    """Advisory comparison of PDF bookmarks against the accepted scaffold."""
    def _normalize(title):
        return re.sub(r"\s+", " ", str(title)).strip().casefold()

    warnings: list[str] = []
    if not outline:
        return warnings
    scaffold_levels = {}
    for entry in scaffold:
        scaffold_levels.setdefault(
            _normalize(entry.get("title", "")), int(entry.get("level", 0)))
    outline_titles = set()
    for level, title, _page in outline:
        key = _normalize(title)
        outline_titles.add(key)
        if key in scaffold_levels:
            if scaffold_levels[key] != int(level):
                warnings.append(
                    f"bookmark level mismatch: {title!r} bookmark level "
                    f"{int(level)} vs scaffold level {scaffold_levels[key]}")
        elif int(level) == 1:
            warnings.append(
                f"bookmark chapter missing from scaffold: {title!r}")
    for entry in scaffold:
        if int(entry.get("level", 0)) != 1:
            continue
        if _normalize(entry.get("title", "")) not in outline_titles:
            warnings.append(
                "scaffold chapter missing from bookmarks: "
                f"{entry.get('title')!r}")
    return warnings
```

Then wire the hook per the Interfaces block (read
`_recover_bound_toc_cell_repairs` first and copy its snapshot/except
shape exactly), and add a wiring test in the same test file that calls the
hook path with a fake snapshot contextmanager monkeypatched to yield a
fake doc whose `get_toc()` returns a mismatching outline, asserting the
warning lands in `caplog.text`.

- [ ] **Step 4: Run the affected suites**

Run: `python -m pytest tests/test_scaffold_structure.py -q`
Expected: all PASS.

- [ ] **Step 5: Lint and commit**

```bash
python -m ruff check rag.py tests/test_scaffold_structure.py
git add rag.py tests/test_scaffold_structure.py
git commit -m "Cross-check the TOC scaffold against PDF bookmarks"
```

---

### Task 5: Docling confidence surfacing

**Files:**
- Modify: `rag.py` — new pure helper `_confidence_summary(confidence) ->
  tuple[dict, list[int]]` near the conversion code; consumption inside
  `_convert_pdf_generation` right after the Docling `convert()` result is
  obtained (locate `result = ` / `conv_res` in the :7530-7600 region);
  optional `telemetry: _run_telemetry.RunTelemetry | None = None` kwarg
  threaded `convert_pdf` → `_convert_pdf_locked` →
  `_convert_pdf_generation`, passed from `_run_full_pipeline` (which holds
  `telemetry`) and from `main`'s convert dispatch (which holds
  `run_telemetry`)
- Test: `tests/test_conversion_confidence.py` (create)

**Interfaces:**
- Produces:

```python
def _confidence_summary(confidence) -> tuple[dict, list[int]]:
    """Numeric conversion-confidence metrics plus low-grade page numbers."""
```

  Returns `(metrics, poor_pages)` where `metrics` contains ONLY telemetry-
  safe values: `{"confidence_pages": int, "confidence_poor_pages": int,
  "confidence_mean_score": float | None, "confidence_low_score":
  float | None}` (scores NaN-guarded via `math.isnan` → None) and
  `poor_pages` lists page numbers whose per-page `low_grade` name is
  `"POOR"`. Absent/None `confidence` or missing attributes → `({}, [])`
  (advisory skip; single `log.info("Docling confidence not available")`).
- Consumption: after conversion, `metrics, poor_pages =
  _confidence_summary(getattr(result, "confidence", None))`; when metrics
  are non-empty: one `log.info` summary line (mean/low grade names +
  scores), a `log.warning("Docling low-confidence pages: %s",
  poor_pages[:20])` only when `poor_pages`, and — when `telemetry` is not
  None — `telemetry.stage_observation("convert", metrics=metrics)`.

- [ ] **Step 1: Write the failing tests** (`tests/test_conversion_confidence.py`)

```python
"""Tests for advisory Docling confidence surfacing."""

import math
from types import SimpleNamespace

import rag


def _page(low="GOOD"):
    return SimpleNamespace(low_grade=SimpleNamespace(name=low))


def _confidence(mean_score=0.8, low_score=0.4, pages=None):
    return SimpleNamespace(
        mean_score=mean_score, low_score=low_score,
        mean_grade=SimpleNamespace(name="GOOD"),
        low_grade=SimpleNamespace(name="FAIR"),
        pages=pages if pages is not None else {0: _page(), 1: _page()},
    )


def test_confidence_summary_maps_scores_and_poor_pages():
    metrics, poor = rag._confidence_summary(_confidence(
        pages={0: _page("POOR"), 1: _page("GOOD"), 2: _page("POOR")}))
    assert metrics["confidence_pages"] == 3
    assert metrics["confidence_poor_pages"] == 2
    assert metrics["confidence_mean_score"] == 0.8
    assert poor == [0, 2]


def test_confidence_summary_nan_guards_scores():
    metrics, poor = rag._confidence_summary(
        _confidence(mean_score=math.nan, low_score=math.nan, pages={}))
    assert metrics["confidence_mean_score"] is None
    assert metrics["confidence_low_score"] is None
    assert metrics["confidence_pages"] == 0
    assert poor == []


def test_confidence_summary_absent_is_empty():
    assert rag._confidence_summary(None) == ({}, [])
    assert rag._confidence_summary(object()) == ({}, [])
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_conversion_confidence.py -q`
Expected: FAIL with `AttributeError: ... _confidence_summary`.

- [ ] **Step 3: Implement** the helper (NaN-guard with `math.isnan` on
  float scores; wrap attribute access in `try/except AttributeError`
  returning `({}, [])`), thread the `telemetry` kwarg through the three
  conversion functions with default `None`, and add the consumption block
  per the Interfaces section. Add a wiring test to the same file:
  monkeypatch the Docling conversion seam the existing
  `tests/test_ingestion_safety.py` conversion tests use, attach a fake
  `confidence` to the faked result, and assert the summary reaches
  `caplog.text` and that a recording fake telemetry object receives one
  `stage_observation("convert", ...)` call with the expected metric keys.

- [ ] **Step 4: Run the affected suites**

Run: `python -m pytest tests/test_conversion_confidence.py tests/test_ingestion_safety.py -q`
Expected: all PASS (the threaded kwarg must not disturb existing
conversion tests — default None).

- [ ] **Step 5: Lint and commit**

```bash
python -m ruff check rag.py tests/test_conversion_confidence.py
git add rag.py tests/test_conversion_confidence.py
git commit -m "Surface Docling conversion confidence advisorily"
```

---

### Task 6: Full-suite integration and gates

**Files:**
- Modify: `architecture-inventory.json` (tool-generated ONLY); gate-pinned
  expectation lists only per their own failure instructions

- [ ] **Step 1:** `python -m pytest -q` — anticipated legitimate failures:
  the architecture-inventory currency test (fix ONLY via
  `python tools/check_architecture_inventory.py --refresh`) and any
  structure/scaffold gate pinning CLI flags or module shape (extend
  expected lists per the failure messages; never weaken). Any other
  failure is a regression in this plan's code.
- [ ] **Step 2:** Re-run the full gate battery from Global Constraints,
  including the three offline eval suites (must be unchanged — ingestion
  changes cannot perturb retrieval math). Record observed counts.
- [ ] **Step 3:** Final commit:

```bash
git add -A
git commit -m "Integrate ingestion quick wins with repository gates

python -m pytest -q: <observed counts>; ruff, compile, dependency,
model-artifact, and architecture-inventory gates green; offline
evaluation suites unchanged."
```

- [ ] **Step 4:** Report branch, final SHA, observed counts, any gate
  expectation diffs, and the CID-threshold calibration note (whether 0.02
  held or was adjusted, with the probe evidence). Do NOT open a PR,
  regenerate benchmarks, or edit ROADMAP.md — the owner's machine performs
  the evidence and candidate ritual.

---

## Deviations and escalation

- Line references drift; trust the code, keep the behavioral requirement,
  note differences in the report.
- If the pinned Docling rejects `RapidOcrOptions.mode` at construction
  (API drift vs the probe), STOP and report — do not silently fall back
  to full-page (spec's fail-closed rule for item 1).
- If PUA-based CID detection misses a known-garbled fixture during
  calibration, escalate before substituting rawdict (Task 1 mechanism
  note).
- Never commit with a failing gate.
