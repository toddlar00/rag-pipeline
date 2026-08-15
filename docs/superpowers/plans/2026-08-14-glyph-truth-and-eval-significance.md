# Glyph-Truth Triage and Evaluation Significance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the CID mojibake detector with a glyph-level texttrace
pass and recall fixture, add an invisible-text OCR-overlay triage signal,
and add deterministic paired-bootstrap significance to `eval.py --compare`.

**Architecture:** `ingestion_core` gains a dependency-free
`page_glyph_stats` policy fold plus an injectable `page_glyph_stats_fn` on
both per-page loops (`analyze_pdf_document`,
`plan_background_image_removals`); `rag.py` surfaces the new counters on
the scan card; `evaluation_metrics` gains a stdlib paired bootstrap wired
behind a new `eval.py --bootstrap` flag in compare mode.

**Tech Stack:** Python stdlib only; PyMuPDF (already pinned) exercised in
fixture tests through the same guarded import style as existing tests.

## Global Constraints

- Zero new dependencies; no dependency version movement; no lockfile edits.
- No receipt/schema version changes: `REPORT_SCHEMA_VERSION` (evaluation
  contract) stays 6; conversion/chunk/quality schemas untouched.
- Default `eval.py` output (with and without `--compare`) must stay
  byte-identical when `--bootstrap` is absent.
- `ingestion_core.py` stays standard-library-only and protocol-based; fakes
  without `get_texttrace` must behave exactly as today.
- Existing thresholds keep their values; `max_cid_char_ratio` (0.02) also
  governs the glyph channel; new `min_invisible_text_share` defaults 0.50.
- Ruff (E4/E7/E9/F, py310) and all policy gates must stay green; follow
  79-col style used by the touched modules.
- Texttrace char tuples are `(unicode, glyph, origin, bbox)`; span dicts
  carry `type` (3 = invisible) — verified by probe on 2026-08-14.

---

### Task 1: `ingestion_core` glyph-truth machinery

**Files:**
- Modify: `ingestion_core.py` (thresholds ~line 41, stats TypedDict ~56,
  new dataclass/typedef near `PageTextUsableFn` ~143, default fn near
  `_default_text_usable` ~405, `analyze_pdf_document` loop ~455-491,
  `plan_background_image_removals` loop ~533-541, `PDFTriage` ~243,
  `assess_pdf_triage` ~299)
- Test: `tests/test_ingestion_core.py`

**Interfaces:**
- Produces: `PageGlyphStats(total_glyphs, unmapped_glyphs, notdef_glyphs,
  invisible_glyphs)` frozen dataclass; `page_glyph_stats(page) ->
  PageGlyphStats | None`; `PageGlyphStatsFn` TypeAlias; threshold field
  `min_invisible_text_share: float = 0.50`; stats key
  `invisible_text_pages: int`; `PDFTriage.invisible_text_pages: int = 0`;
  keyword param `page_glyph_stats_fn` on `analyze_pdf_document` and
  `plan_background_image_removals`.

- [ ] **Step 1: Write failing unit tests** (append to
  `tests/test_ingestion_core.py`; reuse its existing `FakePage`,
  `FakeDocument`, `_triage_stats`, `_assess` helpers — check their actual
  signatures first and adapt constructor calls):

```python
def _trace_char(codepoint, glyph=1):
    return (codepoint, glyph, (0.0, 0.0), (0.0, 0.0, 1.0, 1.0))


def _trace_span(chars, span_type=0):
    return {"type": span_type, "chars": tuple(chars)}


class FakeGlyphPage(FakePage):
    def __init__(self, *args, trace=None, trace_error=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._trace = list(trace or [])
        self._trace_error = trace_error

    def get_texttrace(self):
        if self._trace_error is not None:
            raise self._trace_error
        return self._trace


def test_page_glyph_stats_counts_channels():
    page = FakeGlyphPage(0, "irrelevant", [], trace=[
        _trace_span([_trace_char(0x41), _trace_char(0xFFFD)]),
        _trace_span([_trace_char(0xE000), _trace_char(0x42, glyph=0)]),
        _trace_span([_trace_char(0x43)], span_type=3),
    ])
    stats = ingestion_core.page_glyph_stats(page)
    assert stats == ingestion_core.PageGlyphStats(
        total_glyphs=5, unmapped_glyphs=2, notdef_glyphs=1,
        invisible_glyphs=1)


def test_page_glyph_stats_without_texttrace_is_none():
    assert ingestion_core.page_glyph_stats(
        FakePage(0, "text", [])) is None


def test_analyze_counts_glyph_garbled_page_once():
    clean_text = "clean readable text " * 5
    document = FakeDocument([
        FakeGlyphPage(0, clean_text, [], trace=[
            _trace_span([_trace_char(0x41, glyph=0)] * 60
                        + [_trace_char(0x42)] * 40),
        ]),
        FakeGlyphPage(1, "�" * 100, [], trace=[
            _trace_span([_trace_char(0xFFFD)] * 100),
        ]),
        FakeGlyphPage(2, clean_text, [], trace=[
            _trace_span([_trace_char(0x41)] * 100),
        ]),
    ])
    analysis = ingestion_core.analyze_pdf_document(document)
    assert analysis.stats["cid_garbled_pages"] == 2
    assert analysis.stats["pages_with_usable_text"] == 1
    assert analysis.stats["inspection_complete"] is True


def test_glyph_ratio_boundary_is_not_garbled():
    page = FakeGlyphPage(0, "clean readable text " * 5, [], trace=[
        _trace_span([_trace_char(0x41, glyph=0)] * 2
                    + [_trace_char(0x42)] * 98),
    ])
    analysis = ingestion_core.analyze_pdf_document(FakeDocument([page]))
    assert analysis.stats["cid_garbled_pages"] == 0
    assert analysis.stats["pages_with_usable_text"] == 1


def test_invisible_share_counts_at_threshold():
    trace = [_trace_span([_trace_char(0x41)] * 50, span_type=3),
             _trace_span([_trace_char(0x42)] * 50)]
    page = FakeGlyphPage(0, "clean readable text " * 5, [], trace=trace)
    analysis = ingestion_core.analyze_pdf_document(FakeDocument([page]))
    assert analysis.stats["invisible_text_pages"] == 1
    assert analysis.stats["pages_with_usable_text"] == 1
    assert analysis.stats["cid_garbled_pages"] == 0


def test_invisible_share_below_threshold_not_counted():
    trace = [_trace_span([_trace_char(0x41)] * 49, span_type=3),
             _trace_span([_trace_char(0x42)] * 51)]
    page = FakeGlyphPage(0, "clean readable text " * 5, [], trace=trace)
    analysis = ingestion_core.analyze_pdf_document(FakeDocument([page]))
    assert analysis.stats["invisible_text_pages"] == 0


def test_texttrace_failure_degrades_with_issue():
    page = FakeGlyphPage(
        0, "clean readable text " * 5, [],
        trace_error=RuntimeError("trace failed"))
    analysis = ingestion_core.analyze_pdf_document(FakeDocument([page]))
    assert analysis.stats["cid_garbled_pages"] == 0
    assert analysis.stats["pages_with_usable_text"] == 1
    assert analysis.stats["inspection_complete"] is True
    assert any(
        issue.stage == "text" and "trace failed" in issue.detail
        for issue in analysis.issues)


def test_strip_plan_vetoes_glyph_garbled_page():
    page = FakeGlyphPage(0, "clean readable text " * 5, [], trace=[
        _trace_span([_trace_char(0x41, glyph=0)] * 60
                    + [_trace_char(0x42)] * 40),
    ])
    plan = ingestion_core.plan_background_image_removals(
        FakeDocument([page]))
    assert plan.pages[0].usable_text is False


def test_triage_carries_invisible_text_pages():
    stats = _triage_stats()
    stats["invisible_text_pages"] = 3
    assert _assess(stats).invisible_text_pages == 3
    assert _assess(_triage_stats()).invisible_text_pages == 0
```

- [ ] **Step 2: Run the new tests, confirm they fail** with
  `AttributeError: ... 'page_glyph_stats'` / TypeError / KeyError:
  `python -m pytest tests/test_ingestion_core.py -q -k "glyph or invisible"`

- [ ] **Step 3: Implement in `ingestion_core.py`:**

3a. Threshold field (after `max_cid_char_ratio`):
```python
    min_invisible_text_share: float = 0.50
```

3b. Stats key in `PDFImageStats` (after `cid_garbled_pages`):
```python
    invisible_text_pages: int
```
and in the literal dict inside `analyze_pdf_document`:
```python
        "invisible_text_pages": 0,
```

3c. After the `DeleteImageFn`/`ProgressPagesFn` typedef block:
```python
@dataclass(frozen=True)
class PageGlyphStats:
    """Glyph-level decode and visibility counts from one page's trace."""

    total_glyphs: int
    unmapped_glyphs: int
    notdef_glyphs: int
    invisible_glyphs: int


PageGlyphStatsFn: TypeAlias = Callable[
    [PDFPageLike], "PageGlyphStats | None"
]
```

3d. Public fold + private default near `_default_text_usable`:
```python
def page_glyph_stats(page: PDFPageLike) -> PageGlyphStats | None:
    """Fold one page's text trace into glyph decode/visibility counts.

    Pages without a ``get_texttrace`` method report ``None`` so existing
    protocol fakes and degraded objects keep today's behavior exactly.
    Char tuples are ``(unicode, glyph, origin, bbox)``; span type 3 marks
    invisible text.
    """
    get_texttrace = getattr(page, "get_texttrace", None)
    if get_texttrace is None:
        return None
    total = unmapped = notdef = invisible = 0
    for span in get_texttrace():
        if not isinstance(span, Mapping):
            raise TypeError("texttrace span is not a mapping")
        chars = span.get("chars") or ()
        span_invisible = span.get("type") == 3
        for char in chars:
            codepoint = int(char[0])
            glyph = int(char[1])
            total += 1
            if codepoint == 0xFFFD or 0xE000 <= codepoint <= 0xF8FF:
                unmapped += 1
            if glyph == 0:
                notdef += 1
            if span_invisible:
                invisible += 1
    return PageGlyphStats(
        total_glyphs=total, unmapped_glyphs=unmapped,
        notdef_glyphs=notdef, invisible_glyphs=invisible)


def _glyph_page_verdicts(
        page: PDFPageLike, glyph_stats_fn: PageGlyphStatsFn,
        thresholds: PDFIngestionThresholds,
        issues: list[PDFInspectionIssue]) -> tuple[bool, bool]:
    """Return (glyph_garbled, invisible_overlay); failures degrade."""
    try:
        glyph_stats = glyph_stats_fn(page)
    except Exception as exc:
        issues.append(_issue(page, "text", exc))
        return False, False
    if glyph_stats is None or glyph_stats.total_glyphs <= 0:
        return False, False
    decode_failures = (
        glyph_stats.unmapped_glyphs + glyph_stats.notdef_glyphs)
    garbled = (
        decode_failures / glyph_stats.total_glyphs
        > thresholds.max_cid_char_ratio)
    invisible = (
        glyph_stats.invisible_glyphs / glyph_stats.total_glyphs
        >= thresholds.min_invisible_text_share)
    return garbled, invisible
```

3e. `analyze_pdf_document`: add keyword param
`page_glyph_stats_fn: PageGlyphStatsFn | None = None`, resolve
`glyph_stats_fn = page_glyph_stats_fn or page_glyph_stats`, and replace
the current text/CID fold with:
```python
            stats["text_chars"] += len(text)
            stats["replacement_chars"] += text.count("�")
            glyph_garbled, invisible_overlay = _glyph_page_verdicts(
                page, glyph_stats_fn, thresholds, issues)
            text_garbled = (
                cid_suspect_ratio(text) > thresholds.max_cid_char_ratio)
            if text_garbled or glyph_garbled:
                stats["cid_garbled_pages"] += 1
            if glyph_garbled:
                usable_text = False
            if invisible_overlay:
                stats["invisible_text_pages"] += 1
            if usable_text:
                stats["pages_with_usable_text"] += 1
```

3f. `plan_background_image_removals`: same new keyword param and resolve;
after the text usability try/except add:
```python
            glyph_garbled, _invisible_overlay = _glyph_page_verdicts(
                page, glyph_stats_fn, thresholds, issues)
            if glyph_garbled:
                usable_text = False
```

3g. `PDFTriage`: add field `invisible_text_pages: int = 0`;
`assess_pdf_triage` return gains:
```python
        invisible_text_pages=int(
            stats.get("invisible_text_pages", 0) or 0),
```

- [ ] **Step 4: Run the module's full test file; all pass:**
  `python -m pytest tests/test_ingestion_core.py -q`

- [ ] **Step 5: Commit** `feat: add glyph-truth decode and invisibility
  terms to ingestion policy`

### Task 2: Scan-card and facade surfacing

**Files:**
- Modify: `rag.py` (`_render_pdf_triage` ~6799; threshold constants near
  `_MAX_CID_CHAR_RATIO`; `_pdf_ingestion_thresholds` ~710)
- Test: `tests/test_scan_cli.py`

**Interfaces:**
- Consumes: `PDFTriage.invisible_text_pages` (Task 1).
- Produces: card line `invisible (OCR-overlay) text: N pages`;
  `_MIN_INVISIBLE_TEXT_SHARE = 0.50` module constant.

- [ ] **Step 1: Failing test** (append near the existing garbled-line test
  in `tests/test_scan_cli.py`, following its PDFTriage construction
  helper):

```python
def test_card_renders_invisible_text_line_when_present():
    triage = _make_triage(invisible_text_pages=3)
    card = rag._render_pdf_triage(
        triage, Path("book.pdf"), file_size=1024,
        producer="", creator="")
    assert "invisible (OCR-overlay) text: 3 pages" in card


def test_card_omits_invisible_text_line_when_zero():
    card = rag._render_pdf_triage(
        _make_triage(), Path("book.pdf"), file_size=1024,
        producer="", creator="")
    assert "invisible (OCR-overlay)" not in card
```
(Adapt `_make_triage` to that file's existing triage-construction helper;
if none exists, construct `ingestion_core.PDFTriage` with required fields
copied from an existing test.)

- [ ] **Step 2: Run; confirm failure** (`TypeError` unknown kwarg or
  missing line): `python -m pytest tests/test_scan_cli.py -q -k invisible`

- [ ] **Step 3: Implement:** in `_render_pdf_triage` after the
  `garbled (CID) pages` block:
```python
    if triage.invisible_text_pages:
        lines.append(
            "  invisible (OCR-overlay) text: "
            f"{triage.invisible_text_pages} pages")
```
Add `_MIN_INVISIBLE_TEXT_SHARE = 0.50` beside `_MAX_CID_CHAR_RATIO` and
pass `min_invisible_text_share=_MIN_INVISIBLE_TEXT_SHARE` in
`_pdf_ingestion_thresholds`.

- [ ] **Step 4: Run the file's tests:**
  `python -m pytest tests/test_scan_cli.py -q`

- [ ] **Step 5: Commit** `feat: surface invisible OCR-overlay pages on the
  scan card`

### Task 3: Real-PDF recall fixtures

**Files:**
- Create: `tests/test_glyph_truth_fixtures.py`

**Interfaces:**
- Consumes: `ingestion_core.analyze_pdf_document`, `cid_suspect_ratio`,
  `page_glyph_stats`, default thresholds.

- [ ] **Step 1: Write the fixture module and tests** (probe-verified on
  2026-08-14: extraction of the broken-cmap page yields
  `'e����\n'`, so the text channel fires while the
  trace channel shows substitute-font unicodes — assert the union):

```python
"""Recall fixtures for the CID decode-correctness detector.

The broken-cmap construction is a minimal Type0/Identity-H font without
ToUnicode: plain-text extraction yields replacement characters (text
channel), while the glyph trace reports substitute-font codepoints —
the union detector must flag the page. Invisible-overlay pages are built
with PyMuPDF render_mode=3.
"""
from __future__ import annotations

import pytest

import ingestion_core

pymupdf = pytest.importorskip("pymupdf")


def broken_cmap_pdf_bytes() -> bytes:
    content = b"BT /F1 12 Tf 72 720 Td <00480065006C006C006F> Tj ET"
    objs = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        (b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]"
         b"/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>"),
        (b"<</Length " + str(len(content)).encode() + b">>stream\n"
         + content + b"\nendstream"),
        (b"<</Type/Font/Subtype/Type0/BaseFont/Bogus"
         b"/Encoding/Identity-H/DescendantFonts[6 0 R]>>"),
        (b"<</Type/Font/Subtype/CIDFontType2/BaseFont/Bogus"
         b"/CIDSystemInfo<</Registry(Adobe)/Ordering(Identity)"
         b"/Supplement 0>>/FontDescriptor 7 0 R/CIDToGIDMap/Identity>>"),
        (b"<</Type/FontDescriptor/FontName/Bogus/Flags 4"
         b"/FontBBox[0 0 1000 1000]/ItalicAngle 0/Ascent 800"
         b"/Descent -200/CapHeight 700/StemV 80>>"),
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += str(number).encode() + b" 0 obj" + body + b"endobj\n"
    xref_at = len(out)
    out += b"xref\n0 " + str(len(objs) + 1).encode() + b"\n"
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += ("%010d 00000 n \n" % offset).encode()
    out += (b"trailer<</Size " + str(len(objs) + 1).encode()
            + b"/Root 1 0 R>>\nstartxref\n" + str(xref_at).encode()
            + b"\n%%EOF\n")
    return bytes(out)


def test_broken_cmap_page_is_cid_garbled_end_to_end():
    with pymupdf.open(
            stream=broken_cmap_pdf_bytes(), filetype="pdf") as doc:
        text = doc[0].get_text("text")
        assert ingestion_core.cid_suspect_ratio(text) > (
            ingestion_core.DEFAULT_PDF_INGESTION_THRESHOLDS
            .max_cid_char_ratio)
        analysis = ingestion_core.analyze_pdf_document(doc, 1000)
    assert analysis.stats["cid_garbled_pages"] == 1
    assert analysis.stats["pages_with_usable_text"] == 0


def test_broken_cmap_trace_channel_shape():
    with pymupdf.open(
            stream=broken_cmap_pdf_bytes(), filetype="pdf") as doc:
        stats = ingestion_core.page_glyph_stats(doc[0])
    assert stats is not None
    assert stats.total_glyphs >= 1
    assert stats.invisible_glyphs == 0


def test_invisible_overlay_page_is_counted():
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_text(
            (72, 72), ("hidden ocr overlay text " * 8).strip(),
            fontsize=11, render_mode=3)
        stats = ingestion_core.page_glyph_stats(page)
        assert stats is not None
        assert stats.invisible_glyphs == stats.total_glyphs > 0
        analysis = ingestion_core.analyze_pdf_document(doc, 1000)
    assert analysis.stats["invisible_text_pages"] == 1
    assert analysis.stats["cid_garbled_pages"] == 0


def test_visible_text_page_is_not_counted_invisible():
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_text(
            (72, 72), ("plainly visible text " * 8).strip(), fontsize=11)
        analysis = ingestion_core.analyze_pdf_document(doc, 1000)
    assert analysis.stats["invisible_text_pages"] == 0
```

- [ ] **Step 2: Run:** `python -m pytest tests/test_glyph_truth_fixtures.py -q`
  Expected first run: PASS for text-channel assertions only if Task 1 is
  merged; otherwise KeyError/AttributeError — Task 1 must precede.

- [ ] **Step 3: Commit** `test: prove CID recall on a broken-cmap fixture
  and invisible-overlay counting`

### Task 4: `evaluation_metrics.paired_bootstrap`

**Files:**
- Modify: `evaluation_metrics.py` (add `import random`; new constants and
  function after `percentile`)
- Test: `tests/test_evaluation_metrics.py` (append; create only if absent)

**Interfaces:**
- Produces: `PAIRED_BOOTSTRAP_DEFAULT_RESAMPLES = 2000`,
  `PAIRED_BOOTSTRAP_DEFAULT_SEED = 20260814`,
  `paired_bootstrap(baseline_values, candidate_values, *, resamples,
  seed, confidence=0.95) -> dict` with keys `pairs, resamples, seed,
  confidence, mean_delta, ci_low, ci_high, p_value`.

- [ ] **Step 1: Failing tests:**

```python
def test_paired_bootstrap_is_deterministic():
    baseline = [0.0, 0.0, 1.0, 0.0, 1.0, 0.0]
    candidate = [1.0, 1.0, 1.0, 0.0, 1.0, 1.0]
    first = evaluation_metrics.paired_bootstrap(baseline, candidate)
    second = evaluation_metrics.paired_bootstrap(baseline, candidate)
    assert first == second
    assert first["pairs"] == 6
    assert first["mean_delta"] == pytest.approx(4 / 6, abs=1e-6)
    assert first["ci_low"] <= first["mean_delta"] <= first["ci_high"]


def test_paired_bootstrap_identical_inputs_are_null():
    result = evaluation_metrics.paired_bootstrap(
        [0.5, 0.25, 1.0], [0.5, 0.25, 1.0])
    assert result["mean_delta"] == 0.0
    assert result["ci_low"] == 0.0 and result["ci_high"] == 0.0
    assert result["p_value"] == 1.0


def test_paired_bootstrap_rejects_bad_inputs():
    with pytest.raises(ValueError):
        evaluation_metrics.paired_bootstrap([1.0], [1.0, 0.0])
    with pytest.raises(ValueError):
        evaluation_metrics.paired_bootstrap([], [])
    with pytest.raises(ValueError):
        evaluation_metrics.paired_bootstrap([float("nan")], [0.0])
    with pytest.raises(ValueError):
        evaluation_metrics.paired_bootstrap(
            [1.0], [0.0], resamples=0)
    with pytest.raises(ValueError):
        evaluation_metrics.paired_bootstrap(
            [1.0], [0.0], confidence=1.0)
```

- [ ] **Step 2: Run; expect AttributeError.**

- [ ] **Step 3: Implement** exactly:

```python
PAIRED_BOOTSTRAP_DEFAULT_RESAMPLES = 2000
PAIRED_BOOTSTRAP_DEFAULT_SEED = 20260814


def paired_bootstrap(
        baseline_values: Iterable[float],
        candidate_values: Iterable[float], *,
        resamples: int = PAIRED_BOOTSTRAP_DEFAULT_RESAMPLES,
        seed: int = PAIRED_BOOTSTRAP_DEFAULT_SEED,
        confidence: float = 0.95) -> dict:
    """Percentile bootstrap of the mean per-query delta.

    Deltas are candidate minus baseline, paired per query.  The p-value is
    the two-sided bootstrap achieved significance level: twice the smaller
    share of resampled mean deltas at or beyond zero, capped at 1.  The
    seeded generator makes every field deterministic for a given input.
    """
    baseline = [float(value) for value in baseline_values]
    candidate = [float(value) for value in candidate_values]
    if len(baseline) != len(candidate):
        raise ValueError(
            "paired bootstrap requires equal-length value lists")
    if not baseline:
        raise ValueError("paired bootstrap requires at least one pair")
    if not all(math.isfinite(value) for value in baseline + candidate):
        raise ValueError("paired bootstrap values must be finite")
    if (isinstance(resamples, bool) or not isinstance(resamples, int)
            or resamples < 1):
        raise ValueError("resamples must be a positive integer")
    if (isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not 0 < float(confidence) < 1):
        raise ValueError("confidence must be strictly between 0 and 1")
    deltas = [after - before for before, after in zip(baseline, candidate)]
    observed = sum(deltas) / len(deltas)
    rng = random.Random(seed)
    count = len(deltas)
    means = []
    for _ in range(resamples):
        total = 0.0
        for _ in range(count):
            total += deltas[rng.randrange(count)]
        means.append(total / count)
    alpha = (1.0 - float(confidence)) / 2.0
    lower_tail = sum(1 for mean in means if mean <= 0.0)
    upper_tail = sum(1 for mean in means if mean >= 0.0)
    p_value = min(1.0, 2.0 * min(lower_tail, upper_tail) / resamples)
    return {
        "pairs": count,
        "resamples": resamples,
        "seed": seed,
        "confidence": float(confidence),
        "mean_delta": round(observed, 6),
        "ci_low": round(percentile(means, alpha), 6),
        "ci_high": round(percentile(means, 1.0 - alpha), 6),
        "p_value": round(p_value, 6),
    }
```
(`import random` joins the stdlib import block.)

- [ ] **Step 4: Run the test file; pass.**
- [ ] **Step 5: Commit** `feat: add deterministic paired bootstrap to
  evaluation metrics`

### Task 5: `eval.py --bootstrap` compare wiring

**Files:**
- Modify: `eval.py` (argparse after `--compare` ~1682; validation beside
  the bm25/compare conflict check ~1848; compare block ~2050-2115; new
  helpers above the compare section)
- Test: `tests/test_eval_bootstrap.py` (new; or append to the existing
  eval CLI test file if one covers compare — check first)

**Interfaces:**
- Consumes: `evaluation_metrics.paired_bootstrap` and its constants
  (Task 4); per-config `query_details` entries with `query_index`,
  `query_sha256`, `metrics` (existing).
- Produces: `--bootstrap` flag; report key `significance` (compare mode,
  flag only); helpers `_paired_metric_values`, `_compare_significance`,
  `_print_compare_significance`, `_BOOTSTRAP_BASELINE_LABEL`.

- [ ] **Step 1: Failing tests** (pure-helper level plus arg validation):

```python
def _detail(index, sha, **metrics):
    return {"query_index": index, "query_sha256": sha,
            "metrics": metrics}


def test_paired_metric_values_match_by_identity():
    baseline = [_detail(1, "a", mrr=1.0), _detail(2, "b", mrr=0.0),
                _detail(3, "c", mrr=0.5)]
    candidate = [_detail(3, "c", mrr=1.0), _detail(1, "a", mrr=0.0),
                 _detail(2, "MISMATCH", mrr=1.0)]
    base_values, cand_values, excluded = eval_module._paired_metric_values(
        baseline, candidate, "mrr")
    assert base_values == [1.0, 0.5]
    assert cand_values == [0.0, 1.0]
    assert excluded == 1


def test_compare_significance_shape_and_determinism():
    reports = [
        {"label": "Vector only", "query_details": [
            _detail(1, "a", mrr=0.0), _detail(2, "b", mrr=0.0)]},
        {"label": "Hybrid (BM25+vector)", "query_details": [
            _detail(1, "a", mrr=1.0), _detail(2, "b", mrr=1.0)]},
        {"label": "Broken", "error": {"type": "RuntimeError"}},
    ]
    block = eval_module._compare_significance(reports, ["mrr", "absent"])
    assert block["baseline"] == "Vector only"
    [comparison] = block["comparisons"]
    assert comparison["candidate"] == "Hybrid (BM25+vector)"
    assert comparison["metrics"]["mrr"]["mean_delta"] == 1.0
    assert comparison["metrics"]["absent"]["unavailable"]
    assert block == eval_module._compare_significance(
        reports, ["mrr", "absent"])


def test_bootstrap_requires_compare(tmp_path, capsys):
    chunks = tmp_path / "chunks.jsonl"
    chunks.write_text("", encoding="utf-8")
    with pytest.raises(SystemExit):
        eval_module.main([
            "--queries", str(chunks), "--chunks", str(chunks),
            "--bootstrap"])
```
(Adapt the third test to the module's actual entry signature — check how
existing eval CLI tests invoke it and mirror that; the assertion is that
`--bootstrap` without `--compare` exits with the standard usage error
before any evaluation runs.)

- [ ] **Step 2: Run; expect AttributeError/SystemExit-mismatch failures.**

- [ ] **Step 3: Implement:**

3a. Argparse, directly after the `--compare` argument:
```python
    parser.add_argument(
        "--bootstrap", action="store_true",
        help=("With --compare: deterministic paired-bootstrap "
              "significance of each configuration against the "
              "vector-only baseline"))
```

3b. Beside the existing bm25/compare conflict validation, matching its
error style exactly:
```python
    if args.bootstrap and not args.compare:
        parser.error("--bootstrap requires --compare")
```
(If that block uses a different rejection idiom, mirror it.)

3c. Helpers above the compare section:
```python
_BOOTSTRAP_BASELINE_LABEL = "Vector only"


def _paired_metric_values(
        baseline_details: list, candidate_details: list,
        metric: str) -> tuple[list[float], list[float], int]:
    """Pair per-query metric values by query identity, not order."""
    candidate_by_index = {}
    for detail in candidate_details:
        if isinstance(detail, dict) and "query_index" in detail:
            candidate_by_index[detail["query_index"]] = detail
    baseline_values: list[float] = []
    candidate_values: list[float] = []
    excluded = 0
    for detail in baseline_details:
        if not isinstance(detail, dict) or "query_index" not in detail:
            excluded += 1
            continue
        partner = candidate_by_index.get(detail["query_index"])
        if (partner is None or detail.get("query_sha256")
                != partner.get("query_sha256")):
            excluded += 1
            continue
        base_value = (detail.get("metrics") or {}).get(metric)
        cand_value = (partner.get("metrics") or {}).get(metric)
        if any(isinstance(value, bool)
               or not isinstance(value, (int, float))
               for value in (base_value, cand_value)):
            excluded += 1
            continue
        baseline_values.append(float(base_value))
        candidate_values.append(float(cand_value))
    return baseline_values, candidate_values, excluded


def _compare_significance(reports: list, metrics: list) -> dict:
    """Bootstrap every successful configuration against the baseline."""
    baseline = next(
        (item for item in reports
         if item.get("label") == _BOOTSTRAP_BASELINE_LABEL
         and "error" not in item),
        None)
    block = {
        "baseline": _BOOTSTRAP_BASELINE_LABEL,
        "method": (
            "paired percentile bootstrap of the mean per-query delta"),
        "resamples": (
            evaluation_metrics.PAIRED_BOOTSTRAP_DEFAULT_RESAMPLES),
        "seed": evaluation_metrics.PAIRED_BOOTSTRAP_DEFAULT_SEED,
        "comparisons": [],
    }
    if baseline is None:
        block["unavailable"] = "baseline configuration failed"
        return block
    baseline_details = baseline.get("query_details") or []
    for item in reports:
        if item is baseline or "error" in item:
            continue
        comparison = {"candidate": item.get("label"), "metrics": {}}
        for metric in metrics:
            baseline_values, candidate_values, excluded = (
                _paired_metric_values(
                    baseline_details,
                    item.get("query_details") or [], metric))
            if not baseline_values:
                comparison["metrics"][metric] = {
                    "unavailable": "no shared per-query values",
                    "excluded_pairs": excluded,
                }
                continue
            result = evaluation_metrics.paired_bootstrap(
                baseline_values, candidate_values)
            result["excluded_pairs"] = excluded
            comparison["metrics"][metric] = result
        block["comparisons"].append(comparison)
    return block


def _print_compare_significance(block: dict) -> None:
    print(
        f"\nPaired bootstrap vs {block['baseline']} "
        f"({block['resamples']} resamples, seed {block['seed']})")
    if "unavailable" in block:
        print(f"  unavailable: {block['unavailable']}")
        return
    for comparison in block["comparisons"]:
        print(f"  {comparison['candidate']}")
        for metric, result in comparison["metrics"].items():
            if "unavailable" in result:
                print(
                    f"    {metric:<14s} unavailable "
                    f"({result['unavailable']})")
                continue
            marker = " *" if result["p_value"] < 0.05 else ""
            print(
                f"    {metric:<14s} delta {result['mean_delta']:+.3f} "
                f"[{result['ci_low']:+.3f}, {result['ci_high']:+.3f}] "
                f"p={result['p_value']:.3f}{marker}")
```

3d. In the compare block, keep `query_details` on a local before building
each report entry (it already does), and after the config loop insert:
```python
        significance = None
        if args.bootstrap:
            significance = _compare_significance(
                reports, display_metrics)
            _print_compare_significance(significance)
```
then add to the report dict construction:
```python
        if significance is not None:
            report["significance"] = significance
```

- [ ] **Step 4: Run the new tests plus the existing eval test files; all
  pass. Also assert unchanged default behavior by running one offline
  suite before/after and diffing output:**
  `python eval.py --retriever bm25 --queries evaluation/suites/property/queries.jsonl --chunks evaluation/suites/property/chunks.jsonl --k 1 3 5 --depth 10`

- [ ] **Step 5: Commit** `feat: add paired-bootstrap significance to eval
  compare mode`

### Task 6: Gates, evidence, docs

**Files:**
- Modify: `ROADMAP.md` (narrative addition + CID follow-up closure +
  refreshed dependency-domain targets), architecture inventory via its
  refresh tool, spec status line.

- [ ] Step 1: Full battery with the locked interpreter:
  `python -m pytest -q`, `python -m ruff check .`,
  `python tools/check_python_sources.py`,
  `python tools/check_dependency_policy.py`,
  `python tools/check_model_artifacts.py`, all three offline eval suites,
  architecture-inventory refresh tool (regenerate, do not hand-edit).
- [ ] Step 2: Real-corpus false-positive probe: run the union detector
  over the local private casebook PDFs; record counts-only results.
- [ ] Step 3: Whole-branch subagent review; fix wave; re-run gates.
- [ ] Step 4: ROADMAP/docs update, history-preserving merge to `main`.

## Self-review notes

- Spec coverage: item 1 → Tasks 1-2; item 2 → Task 3; item 3 → Tasks 4-5;
  scan-findings recording → Task 6. Union-once, boundary, degrade,
  strip-plan veto, determinism, identity pairing, flag gating all have
  explicit tests.
- The Task 3 fixture asserts the union through the text channel and the
  trace channel's shape; `.notdef` and unmapped trace channels are covered
  by Task 1's synthetic matrices — recorded honestly in the ROADMAP
  closure wording.
- Names used across tasks: `page_glyph_stats`, `PageGlyphStats`,
  `PageGlyphStatsFn`, `page_glyph_stats_fn`, `invisible_text_pages`,
  `min_invisible_text_share`, `paired_bootstrap`,
  `_compare_significance`, `_paired_metric_values`,
  `_print_compare_significance`, `_BOOTSTRAP_BASELINE_LABEL` — consistent.
