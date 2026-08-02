# First-Pass PDF Triage Scan Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only, LLM-free `python rag.py scan --pdf <path>` command
that prints a deterministic triage report card for a new PDF, per
`docs/superpowers/specs/2026-08-02-pdf-triage-scan-design.md`.

**Architecture:** Pure assessment policy (`PDFTriage` +
`assess_pdf_triage`) goes in `ingestion_core.py` beside the thresholds it
reuses; `rag.py` gains a pure renderer `_render_pdf_triage`, a `scan_pdf`
handler that gathers raw signals with PyMuPDF and the existing
`_analyze_pdf_images` pass, and the `scan` subparser + dispatch. Nothing is
written to disk; no existing command changes.

**Tech Stack:** Python 3.10–3.14 stdlib in `ingestion_core.py` (module must
keep importing nothing heavier — a subprocess test enforces that it never
loads `rag`/`pymupdf`); PyMuPDF only inside the `rag.py` handler via the
repo's function-local `import pymupdf` idiom; pytest.

## Global Constraints

- Work on a feature branch off `agent/pdf-triage-spec` (which is `main` +
  the spec commits); never commit to `main`.
- NEVER hand-edit `*.lock`, `benchmarks/phase-a0-*.json`, or
  `architecture-inventory.json` (refresh the inventory ONLY via
  `python tools/check_architecture_inventory.py --refresh`). Do not touch
  `.github/workflows/` or `ROADMAP.md`. Claim no hosted or Phase A0 result.
- `ingestion_core.py` imports: standard library + `typing` only.
  `tests/test_ingestion_core.py:75` runs a subprocess asserting the module
  never imports `rag`, `pymupdf`, or `tqdm` — keep it true.
- The card is console-only: `scan` must create no file (a test asserts the
  output directory stays empty).
- Exit codes: 0 whenever a card was printed (even for a broken text
  layer); 1 only when the PDF is missing/empty (`_require_file` path) or
  cannot be opened/read at all.
- Forecast values are exactly: `"strip-backgrounds"`, `"keep-for-ocr"`,
  `"no-preprocess"`, `"inspection-incomplete"`.
- Gates before the final commit: `python -m pytest -q`,
  `python -m ruff check .`, `python tools/check_python_sources.py`,
  `python tools/check_dependency_policy.py`,
  `python tools/check_model_artifacts.py`,
  `python tools/check_architecture_inventory.py` (after `--refresh`), and
  the three offline evaluation suites unchanged.

## Repository orientation (verified interfaces; trust the code if drifted)

- `rag._analyze_pdf_images(pdf_path: Path, min_dim: int = 1000) -> dict`
  (rag.py:6700-6723) returns `ingestion_core.PDFImageStats`
  (ingestion_core.py:55-66) with EXACTLY these keys: `total_pages: int`,
  `pages_with_large_images: int`, `pages_with_usable_text: int`,
  `large_image_pages_with_usable_text: int`, `text_chars: int`,
  `replacement_chars: int`, `unique_dims: set[str]`,
  `image_xrefs: set[int]`, `inspection_complete: bool`.
- `ingestion_core.PDFIngestionThresholds` (frozen dataclass,
  ingestion_core.py:41-50): `min_usable_page_chars=40`,
  `min_usable_text_page_ratio=0.60`, `min_usable_scan_text_ratio=1.00`,
  `max_replacement_char_ratio=0.02`,
  `min_background_image_page_coverage=0.70`; singleton
  `DEFAULT_PDF_INGESTION_THRESHOLDS` at :52.
- `ingestion_core.pdf_text_layer_is_usable(stats, *,
  large_image_pages_only=False, thresholds=...)` (ingestion_core.py:186)
  is the pipeline's OCR gate (`_prepare_conversion_input` calls the rag
  facade `_pdf_text_layer_is_usable(stats)` at rag.py:7271 with no
  kwargs).
- `preprocess_pdf` decision order (rag.py:6726-6887), which the forecast
  MUST mirror: (1) `stats.get("inspection_complete") is False` → no strip;
  (2) `ratio = stats["pages_with_large_images"] /
  max(stats["total_pages"], 1)`; `ratio < 0.1` → skip preprocessing;
  (3) `"large_image_pages_with_usable_text" in stats and
  stats["large_image_pages_with_usable_text"] == 0` → keep images for
  OCR; (4) otherwise strip. The 0.1 literal is inline in rag.py — the
  policy takes it as a defaulted parameter.
- Watermark: `rag.DEFAULT_WATERMARK` (rag.py:738-741),
  `rag._compile_watermark(pattern: str) -> Optional[re.Pattern]`
  (rag.py:758, `re.IGNORECASE`), CLI helper `add_watermark_flag(p)`
  (rag.py:30454-30456).
- PyMuPDF idiom: function-local `import pymupdf`, then
  `with pymupdf.open(str(pdf_path)) as doc:`; page text via
  `page.get_text("text") or ""`. `doc.metadata` (dict with `producer` /
  `creator` keys) and `doc.get_toc()` are NEW idioms in this repo — used
  only inside the `scan_pdf` handler.
- CLI: subparsers are created inside `rag.main` (rag.py:30345); the
  `full` block shows the `--pdf` shape (rag.py:30874-30875:
  `p_full.add_argument("--pdf", type=Path, required=True)`); the `info`
  block (rag.py:30755-30761) and its dispatch (rag.py:31210-31214) are
  the read-only template; the dispatch chain starts at rag.py:31087.
  `run_telemetry.stage_started(args.command)` fires for every command
  except `full`/`batch` (rag.py:31025-31026, :31071) — BEFORE wiring,
  check whether `run_telemetry.stage_started` validates stage names
  against a fixed set; if it does, extend that set with `"scan"`
  following its own pattern; if it accepts any string, change nothing.
- Test seams: build stats dicts as plain literals with only the keys the
  policy reads (pattern at tests/test_ingestion_core.py:106-136); the
  full nine-key builder `_analysis_stats(...)` lives at
  tests/test_ingestion_facades.py:79-90; fake PyMuPDF via
  `monkeypatch.setitem(sys.modules, "pymupdf",
  SimpleNamespace(open=...))` (tests/test_ingestion_facades.py:93-99);
  CLI console tests call `rag.main([...])` and assert on
  `capsys.readouterr().out` (tests/test_info.py:37-56).

## File structure

- Modify: `ingestion_core.py` — `PDFTriage` + `assess_pdf_triage` (pure).
- Modify: `rag.py` — `_render_pdf_triage`, `scan_pdf`, parser + dispatch.
- Modify: `tests/test_ingestion_core.py` — policy matrix tests.
- Create: `tests/test_scan_cli.py` — renderer/handler/CLI tests.

---

### Task 1: Triage policy in `ingestion_core`

**Files:**
- Modify: `ingestion_core.py` (append after `pdf_text_layer_is_usable`)
- Test: `tests/test_ingestion_core.py` (append)

**Interfaces:**
- Consumes: `PDFIngestionThresholds`,
  `DEFAULT_PDF_INGESTION_THRESHOLDS`, `pdf_text_layer_is_usable` (all
  existing).
- Produces (used by Tasks 2-3):
  - `SCANNER_FINGERPRINTS = ("paper capture", "abbyy")`
  - `PREPROCESS_STRIP_RATIO = 0.1`
  - `@dataclass(frozen=True) class PDFTriage` with fields exactly:
    `page_count: int`, `large_image_pages: int`,
    `text_layer_usable: bool`, `usable_pages: int`,
    `unusable_pages: int`, `preprocess_forecast: str`,
    `scanner_fingerprint: str | None`, `watermark_page_matches: int`,
    `sampled_pages: int`, `has_outline: bool`,
    `contents_page_found: bool`, `ocr_recommended: bool`,
    `sample_read_errors: int`
  - `assess_pdf_triage(stats: Mapping[str, object], *, producer: str,
    creator: str, watermark_page_matches: int, sampled_pages: int,
    has_outline: bool, contents_page_found: bool,
    sample_read_errors: int = 0,
    thresholds: PDFIngestionThresholds =
    DEFAULT_PDF_INGESTION_THRESHOLDS,
    strip_ratio: float = PREPROCESS_STRIP_RATIO) -> PDFTriage`

- [ ] **Step 1: Write the failing tests** (append to
  `tests/test_ingestion_core.py`; module already does
  `import ingestion_core`)

```python
def _triage_stats(*, total=10, large=8, usable=8, usable_large=8,
                  text_chars=10_000, replacement_chars=0,
                  inspection_complete=True):
    return {
        "total_pages": total,
        "pages_with_large_images": large,
        "pages_with_usable_text": usable,
        "large_image_pages_with_usable_text": usable_large,
        "text_chars": text_chars,
        "replacement_chars": replacement_chars,
        "inspection_complete": inspection_complete,
        "unique_dims": set(),
        "image_xrefs": set(),
    }


def _assess(stats, **overrides):
    values = {
        "producer": "", "creator": "", "watermark_page_matches": 0,
        "sampled_pages": 0, "has_outline": False,
        "contents_page_found": False,
    }
    values.update(overrides)
    return ingestion_core.assess_pdf_triage(stats, **values)


def test_triage_forecast_mirrors_preprocess_branch_order():
    assert _assess(_triage_stats(
        inspection_complete=False)).preprocess_forecast == (
            "inspection-incomplete")
    assert _assess(_triage_stats(
        large=0, usable_large=0)).preprocess_forecast == "no-preprocess"
    assert _assess(_triage_stats(
        usable=0, usable_large=0)).preprocess_forecast == "keep-for-ocr"
    assert _assess(_triage_stats()).preprocess_forecast == (
        "strip-backgrounds")


def test_triage_forecast_ratio_boundary_matches_preprocess_literal():
    # ratio < 0.1 skips; exactly 0.1 does not (preprocess uses "<").
    below = _triage_stats(total=100, large=9, usable=100, usable_large=9)
    at = _triage_stats(total=100, large=10, usable=100, usable_large=10)
    assert _assess(below).preprocess_forecast == "no-preprocess"
    assert _assess(at).preprocess_forecast == "strip-backgrounds"


def test_triage_ocr_recommendation_binds_to_text_layer_gate():
    usable = _triage_stats()
    unusable = _triage_stats(usable=2, usable_large=2)
    assert _assess(usable).ocr_recommended is False
    assert _assess(usable).text_layer_usable is True
    assert _assess(unusable).ocr_recommended is True
    assert _assess(unusable).text_layer_usable is False
    # The two verdicts come from different seams and may disagree: a
    # low-raster book with a globally unusable text layer still forecasts
    # no-preprocess while recommending OCR.
    edge = _triage_stats(total=100, large=5, usable=10, usable_large=5)
    triage = _assess(edge)
    assert triage.preprocess_forecast == "no-preprocess"
    assert triage.ocr_recommended is True


def test_triage_counts_and_scanner_fingerprint():
    triage = _assess(
        _triage_stats(total=12, large=7, usable=9),
        producer="Adobe Acrobat 9.0 Paper Capture Plug-in",
        watermark_page_matches=3, sampled_pages=40, has_outline=True,
        contents_page_found=True)
    assert triage.page_count == 12
    assert triage.large_image_pages == 7
    assert triage.usable_pages == 9
    assert triage.unusable_pages == 3
    assert triage.scanner_fingerprint == "paper capture"
    assert triage.watermark_page_matches == 3
    assert triage.sampled_pages == 40
    assert triage.has_outline is True
    assert triage.contents_page_found is True


def test_triage_fingerprint_from_creator_and_absent():
    assert _assess(_triage_stats(),
                   creator="ABBYY FineReader 15").scanner_fingerprint == (
        "abbyy")
    assert _assess(_triage_stats(),
                   producer="LaTeX with hyperref").scanner_fingerprint is (
        None)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_ingestion_core.py -q -k triage`
Expected: FAIL with `AttributeError: ... assess_pdf_triage`.

- [ ] **Step 3: Implement** (append to `ingestion_core.py`; `dataclass`
  and `Mapping` are already imported at the top of the module — verify and
  extend the existing import lines rather than adding duplicates)

```python
SCANNER_FINGERPRINTS = ("paper capture", "abbyy")
PREPROCESS_STRIP_RATIO = 0.1


@dataclass(frozen=True)
class PDFTriage:
    """Deterministic first-pass report-card verdicts for one PDF."""

    page_count: int
    large_image_pages: int
    text_layer_usable: bool
    usable_pages: int
    unusable_pages: int
    preprocess_forecast: str
    scanner_fingerprint: str | None
    watermark_page_matches: int
    sampled_pages: int
    has_outline: bool
    contents_page_found: bool
    ocr_recommended: bool
    sample_read_errors: int


def _scanner_fingerprint(producer: str, creator: str) -> str | None:
    haystack = f"{producer} {creator}".lower()
    for fingerprint in SCANNER_FINGERPRINTS:
        if fingerprint in haystack:
            return fingerprint
    return None


def _preprocess_forecast(stats: Mapping[str, object], *,
                         strip_ratio: float) -> str:
    """Mirror preprocess_pdf's branch order without side effects."""
    if stats.get("inspection_complete") is False:
        return "inspection-incomplete"
    total = int(stats.get("total_pages", 0) or 0)
    large = int(stats.get("pages_with_large_images", 0) or 0)
    ratio = large / max(total, 1)
    if ratio < strip_ratio:
        return "no-preprocess"
    if ("large_image_pages_with_usable_text" in stats
            and stats["large_image_pages_with_usable_text"] == 0):
        return "keep-for-ocr"
    return "strip-backgrounds"


def assess_pdf_triage(
        stats: Mapping[str, object], *, producer: str, creator: str,
        watermark_page_matches: int, sampled_pages: int,
        has_outline: bool, contents_page_found: bool,
        sample_read_errors: int = 0,
        thresholds: PDFIngestionThresholds = (
            DEFAULT_PDF_INGESTION_THRESHOLDS),
        strip_ratio: float = PREPROCESS_STRIP_RATIO) -> PDFTriage:
    """Assess one analyzed PDF into the first-pass report-card verdicts."""
    page_count = int(stats.get("total_pages", 0) or 0)
    usable_pages = int(stats.get("pages_with_usable_text", 0) or 0)
    text_layer_usable = pdf_text_layer_is_usable(
        stats, thresholds=thresholds)
    return PDFTriage(
        page_count=page_count,
        large_image_pages=int(
            stats.get("pages_with_large_images", 0) or 0),
        text_layer_usable=text_layer_usable,
        usable_pages=usable_pages,
        unusable_pages=max(page_count - usable_pages, 0),
        preprocess_forecast=_preprocess_forecast(
            stats, strip_ratio=strip_ratio),
        scanner_fingerprint=_scanner_fingerprint(producer, creator),
        watermark_page_matches=watermark_page_matches,
        sampled_pages=sampled_pages,
        has_outline=has_outline,
        contents_page_found=contents_page_found,
        ocr_recommended=not text_layer_usable,
        sample_read_errors=sample_read_errors,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_ingestion_core.py -q`
Expected: all PASS (including the pre-existing isolation subprocess test —
the new code must not add imports).

- [ ] **Step 5: Lint and commit**

```bash
python -m ruff check ingestion_core.py tests/test_ingestion_core.py
git add ingestion_core.py tests/test_ingestion_core.py
git commit -m "Add deterministic PDF triage assessment policy"
```

---

### Task 2: Card renderer in `rag.py`

**Files:**
- Modify: `rag.py` (new function near `preprocess_pdf`, rag.py:6726)
- Test: `tests/test_scan_cli.py` (create)

**Interfaces:**
- Consumes: `ingestion_core.PDFTriage` (Task 1); `rag` already imports the
  module as `_ingestion_core`.
- Produces: `rag._render_pdf_triage(triage: _ingestion_core.PDFTriage,
  pdf_path: Path, *, file_size: int, producer: str, creator: str,
  open_error: str | None = None) -> str` — the complete card text. With
  `open_error` set, only the Document section plus the error line is
  rendered.

- [ ] **Step 1: Write the failing tests** (`tests/test_scan_cli.py`)

```python
"""Renderer, handler, and CLI tests for rag.py scan."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import ingestion_core
import rag


def _triage(**overrides):
    values = {
        "page_count": 100, "large_image_pages": 90,
        "text_layer_usable": True, "usable_pages": 95,
        "unusable_pages": 5, "preprocess_forecast": "strip-backgrounds",
        "scanner_fingerprint": "paper capture",
        "watermark_page_matches": 4, "sampled_pages": 40,
        "has_outline": True, "contents_page_found": True,
        "ocr_recommended": False, "sample_read_errors": 0,
    }
    values.update(overrides)
    return ingestion_core.PDFTriage(**values)


def test_render_card_contains_all_sections_and_verdict():
    card = rag._render_pdf_triage(
        _triage(), Path("book.pdf"), file_size=12_345_678,
        producer="Adobe Paper Capture", creator="Scanner")
    for expected in (
        "book.pdf", "100 pages", "Adobe Paper Capture",
        "scanner fingerprint: paper capture",
        "90 of 100 pages carry large background images",
        "usable text layer: yes (95 of 100 pages)",
        "preprocess forecast: strip-backgrounds",
        "watermark matches: 4 of 40 sampled pages",
        "outline bookmarks: yes", "contents page found: yes",
        "python rag.py full --pdf book.pdf",
    ):
        assert expected in card
    assert "--ocr" not in card


def test_render_card_ocr_verdict_and_incomplete_caution():
    card = rag._render_pdf_triage(
        _triage(text_layer_usable=False, ocr_recommended=True,
                preprocess_forecast="inspection-incomplete"),
        Path("scan.pdf"), file_size=1, producer="", creator="")
    assert "python rag.py full --pdf scan.pdf --ocr" in card
    assert "inspection was incomplete" in card


def test_render_card_open_error_short_form():
    card = rag._render_pdf_triage(
        None, Path("broken.pdf"), file_size=17, producer="", creator="",
        open_error="cannot open: encrypted")
    assert "broken.pdf" in card
    assert "cannot open: encrypted" in card
    assert "preprocess forecast" not in card
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_scan_cli.py -q`
Expected: FAIL with `AttributeError: ... _render_pdf_triage`.

- [ ] **Step 3: Implement** (add to `rag.py` directly above
  `preprocess_pdf`)

```python
def _render_pdf_triage(triage, pdf_path: Path, *, file_size: int,
                       producer: str, creator: str,
                       open_error: str | None = None) -> str:
    """Render the console report card for one scanned PDF."""
    lines = [f"PDF triage: {pdf_path.name}", ""]
    lines.append("Document")
    size_mb = file_size / (1024 * 1024)
    if triage is not None:
        lines.append(f"  {triage.page_count} pages · {size_mb:.1f} MB")
    else:
        lines.append(f"  {size_mb:.1f} MB")
    if producer:
        lines.append(f"  producer: {producer}")
    if creator:
        lines.append(f"  creator: {creator}")
    if open_error is not None:
        lines.append(f"  ERROR: {open_error}")
        return "\n".join(lines) + "\n"
    if triage.scanner_fingerprint is not None:
        lines.append(
            f"  scanner fingerprint: {triage.scanner_fingerprint}")
    lines.append("")
    lines.append("Page composition")
    lines.append(
        f"  {triage.large_image_pages} of {triage.page_count} pages "
        "carry large background images")
    lines.append("")
    lines.append("Text layer")
    usable_text = "yes" if triage.text_layer_usable else "no"
    lines.append(
        f"  usable text layer: {usable_text} "
        f"({triage.usable_pages} of {triage.page_count} pages)")
    if triage.sample_read_errors:
        lines.append(
            f"  unreadable sampled pages: {triage.sample_read_errors}")
    lines.append("")
    lines.append(f"  preprocess forecast: {triage.preprocess_forecast}")
    if triage.preprocess_forecast == "inspection-incomplete":
        lines.append(
            "  caution: PDF inspection was incomplete; verify the file "
            "before a full run")
    lines.append(
        f"  watermark matches: {triage.watermark_page_matches} of "
        f"{triage.sampled_pages} sampled pages")
    outline = "yes" if triage.has_outline else "no"
    contents = "yes" if triage.contents_page_found else "no"
    lines.append(f"  outline bookmarks: {outline}")
    lines.append(f"  contents page found: {contents}")
    lines.append("")
    suggestion = f"python rag.py full --pdf {pdf_path}"
    if triage.ocr_recommended:
        suggestion += " --ocr"
    lines.append(f"Suggested next step: {suggestion}")
    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_scan_cli.py -q`
Expected: all PASS.

- [ ] **Step 5: Lint and commit**

```bash
python -m ruff check rag.py tests/test_scan_cli.py
git add rag.py tests/test_scan_cli.py
git commit -m "Add PDF triage card renderer"
```

---

### Task 3: `scan_pdf` handler

**Files:**
- Modify: `rag.py` (below `_render_pdf_triage`)
- Test: `tests/test_scan_cli.py` (append)

**Interfaces:**
- Consumes: `_require_file` (rag.py:747), `_analyze_pdf_images`
  (rag.py:6700), `_ingestion_core.assess_pdf_triage`,
  `_pdf_ingestion_thresholds` (rag.py:709), `_compile_watermark`
  (rag.py:758), `_render_pdf_triage` (Task 2), function-local
  `import pymupdf`.
- Produces: `rag.scan_pdf(pdf_path: Path, *,
  watermark: str = DEFAULT_WATERMARK, min_dim: int = 1000) -> None` —
  prints the card via `print()`; `raise SystemExit(1)` after printing a
  short card when PyMuPDF cannot open/read the document; returns normally
  otherwise.

Behavioral requirements:

1. `_require_file(pdf_path, "PDF file")` first (its failure path already
   exits 1).
2. Open with the repo idiom: `import pymupdf` inside the function, then
   `pymupdf.open(str(pdf_path))` in a `try`. On any exception, or when
   `doc.needs_pass` is true (encrypted), render the short card
   (`triage=None`, `open_error=str(exc)` or `"encrypted PDF"`), print
   it, and `raise SystemExit(1)`.
3. From the open document, inside `try/finally: doc.close()`: read
   `metadata = doc.metadata or {}` (`producer = metadata.get("producer",
   "")`, `creator = metadata.get("creator", "")`),
   `has_outline = bool(doc.get_toc())`, and the sampled pages: indices
   `range(0, min(30, page_count))` plus 10 evenly spaced indices from
   `range(30, page_count)` when `page_count > 30` (step
   `max((page_count - 30) // 10, 1)`, capped at 10 indices, deduplicated,
   sorted). For each sampled page: `text = page.get_text("text") or ""`;
   count a watermark match when the compiled pattern
   (`_compile_watermark(watermark)`) is non-None and
   `pattern.search(text)`; detect a contents page when any line of the
   page, collapsed with `re.sub(r"\s+", " ", line).strip().casefold()`,
   equals `"contents"`, `"table of contents"`, or
   `"summary of contents"`. A page whose text extraction raises is
   counted as sampled, increments a `sample_read_errors` counter that is
   forwarded to `assess_pdf_triage`, and is otherwise skipped (no crash).
4. After closing the document: `stats = _analyze_pdf_images(pdf_path,
   min_dim)` (this reopens the file itself), then
   `triage = _ingestion_core.assess_pdf_triage(stats,
   producer=producer, creator=creator,
   watermark_page_matches=watermark_matches,
   sampled_pages=len(sampled_indices), has_outline=has_outline,
   contents_page_found=contents_found,
   sample_read_errors=sample_read_errors,
   thresholds=_pdf_ingestion_thresholds())`.
5. `print(_render_pdf_triage(triage, pdf_path,
   file_size=pdf_path.stat().st_size, producer=producer,
   creator=creator), end="")` and return.
6. No file writes anywhere in the code path.

- [ ] **Step 1: Write the failing tests** (append to
  `tests/test_scan_cli.py`)

```python
class _FakePage:
    def __init__(self, text):
        self._text = text

    def get_text(self, _kind):
        return self._text


class _FakeDoc:
    def __init__(self, pages, *, metadata=None, toc=None,
                 needs_pass=False):
        self._pages = [_FakePage(text) for text in pages]
        self.metadata = metadata or {}
        self._toc = toc or []
        self.needs_pass = needs_pass

    def __len__(self):
        return len(self._pages)

    def __getitem__(self, index):
        return self._pages[index]

    def get_toc(self):
        return self._toc

    def close(self):
        pass


def _install_fake_pymupdf(monkeypatch, doc):
    monkeypatch.setitem(
        sys.modules, "pymupdf", SimpleNamespace(open=lambda _p: doc))


def _stats(total, large, usable, usable_large):
    return {
        "total_pages": total, "pages_with_large_images": large,
        "pages_with_usable_text": usable,
        "large_image_pages_with_usable_text": usable_large,
        "text_chars": 1000, "replacement_chars": 0,
        "inspection_complete": True, "unique_dims": set(),
        "image_xrefs": set(),
    }


def test_scan_pdf_prints_card_and_writes_nothing(
        monkeypatch, tmp_path, capsys):
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"%PDF-fake")
    doc = _FakeDoc(
        ["CONTENTS\nChapter 1 ... 1", "Copyright © 2024 Carolina "
         "Academic Press, LLC. All rights reserved. Do not post or "
         "distribute.", "ordinary page"],
        metadata={"producer": "Paper Capture Plug-in", "creator": "x"},
        toc=[[1, "Chapter 1", 1]])
    _install_fake_pymupdf(monkeypatch, doc)
    monkeypatch.setattr(
        rag, "_analyze_pdf_images", lambda path, min_dim: _stats(
            3, 3, 3, 3))
    before = sorted(tmp_path.iterdir())
    rag.scan_pdf(pdf)
    out = capsys.readouterr().out
    assert "PDF triage: book.pdf" in out
    assert "scanner fingerprint: paper capture" in out
    assert "watermark matches: 1 of 3 sampled pages" in out
    assert "outline bookmarks: yes" in out
    assert "contents page found: yes" in out
    assert "preprocess forecast: strip-backgrounds" in out
    assert sorted(tmp_path.iterdir()) == before


def test_scan_pdf_encrypted_prints_short_card_and_exits_1(
        monkeypatch, tmp_path, capsys):
    pdf = tmp_path / "locked.pdf"
    pdf.write_bytes(b"%PDF-fake")
    _install_fake_pymupdf(
        monkeypatch, _FakeDoc(["x"], needs_pass=True))
    with pytest.raises(SystemExit) as excinfo:
        rag.scan_pdf(pdf)
    assert excinfo.value.code == 1
    out = capsys.readouterr().out
    assert "locked.pdf" in out and "encrypted" in out


def test_scan_pdf_bad_page_counts_as_sampled(
        monkeypatch, tmp_path, capsys):
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"%PDF-fake")

    class _BadPage:
        def get_text(self, _kind):
            raise RuntimeError("mupdf page error")

    doc = _FakeDoc(["fine"])
    doc._pages.append(_BadPage())
    _install_fake_pymupdf(monkeypatch, doc)
    monkeypatch.setattr(
        rag, "_analyze_pdf_images", lambda path, min_dim: _stats(
            2, 0, 2, 0))
    rag.scan_pdf(pdf)
    out = capsys.readouterr().out
    assert "2 sampled pages" in out
    assert "unreadable sampled pages: 1" in out
    assert "preprocess forecast: no-preprocess" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_scan_cli.py -q -k scan_pdf`
Expected: FAIL with `AttributeError: ... scan_pdf`.

- [ ] **Step 3: Implement per the behavioral requirements above.** The
  sampling-index helper is part of this step:

```python
def _triage_sample_indices(page_count: int) -> list[int]:
    """First 30 pages plus up to 10 evenly spaced later pages."""
    indices = list(range(min(page_count, 30)))
    if page_count > 30:
        step = max((page_count - 30) // 10, 1)
        indices.extend(range(30, page_count, step)[:10])
    return sorted(dict.fromkeys(indices))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_scan_cli.py -q`
Expected: all PASS.

- [ ] **Step 5: Lint and commit**

```bash
python -m ruff check rag.py tests/test_scan_cli.py
git add rag.py tests/test_scan_cli.py
git commit -m "Add read-only scan_pdf triage handler"
```

---

### Task 4: CLI parser, dispatch, and telemetry check

**Files:**
- Modify: `rag.py` (parser block near `info` at rag.py:30755; dispatch
  chain near rag.py:31210)
- Test: `tests/test_scan_cli.py` (append)

**Interfaces:**
- Consumes: `scan_pdf` (Task 3), `add_watermark_flag` (rag.py:30454).
- Produces: the `scan` subcommand.

- [ ] **Step 1: Write the failing tests** (append)

```python
def test_scan_cli_dispatch_forwards_arguments(monkeypatch, tmp_path):
    captured = {}

    def fake_scan(pdf_path, **kwargs):
        captured["pdf"] = pdf_path
        captured.update(kwargs)

    monkeypatch.setattr(rag, "scan_pdf", fake_scan)
    rag.main(["scan", "--pdf", str(tmp_path / "b.pdf"),
              "--watermark", "custom"])
    assert captured["pdf"] == tmp_path / "b.pdf"
    assert captured["watermark"] == "custom"


def test_scan_cli_requires_pdf_argument(capsys):
    with pytest.raises(SystemExit) as excinfo:
        rag.main(["scan"])
    assert excinfo.value.code == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_scan_cli.py -q -k cli`
Expected: the dispatch test FAILS (argparse rejects the unknown `scan`
command with exit code 2 before `fake_scan` runs).

- [ ] **Step 3: Check the telemetry stage gate.** Read
  `run_telemetry.stage_started` (find it with
  `grep -n "def stage_started" run_telemetry.py`). If it validates stage
  names against a fixed collection, add `"scan"` to that collection
  following its own pattern (and note the diff in the final report); if it
  accepts arbitrary names, change nothing.

- [ ] **Step 4: Implement the parser and dispatch.** Parser block, placed
  immediately after the `info` block (rag.py:30761):

```python
    # scan
    p_scan = sub.add_parser(
        "scan", help="Read-only first-pass triage report for a PDF")
    p_scan.add_argument("--pdf", type=Path, required=True)
    add_watermark_flag(p_scan)
```

Dispatch, in the `elif` chain (after the `info` dispatch at
rag.py:31210-31214):

```python
    elif args.command == "scan":
        scan_pdf(args.pdf, watermark=args.watermark)
```

CAUTION: `rag.main` compiles `args.watermark` at rag.py:31034-31035 for
commands that have the flag; `scan` passes the raw pattern string to
`scan_pdf`, which compiles internally — do not also consume the shared
compiled `wm` variable.

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_scan_cli.py -q`
Expected: all PASS.

- [ ] **Step 6: Lint and commit**

```bash
python -m ruff check rag.py tests/test_scan_cli.py
git add rag.py tests/test_scan_cli.py
git commit -m "Wire the scan CLI command"
```

---

### Task 5: Full-suite integration and gates

**Files:**
- Modify: `architecture-inventory.json` (tool-generated ONLY)
- Possibly modify: gate-pinned test expectation lists (only per each
  gate's own failure instruction; never weaken a gate)

- [ ] **Step 1: Full suite**

Run: `python -m pytest -q`
Expected legitimate failures:
`tests/test_architecture_inventory.py::test_repository_baseline_is_current_and_canonical`
(fix via `python tools/check_architecture_inventory.py --refresh`), and
possibly structure/scaffold gates that pin the CLI command list or module
shape — extend their expected lists with `scan` / the new functions per
the failure messages' own instructions. Any other failure is a regression
in the new code.

- [ ] **Step 2: Gates**

```bash
python tools/check_architecture_inventory.py --refresh
python tools/check_architecture_inventory.py
python -m pytest -q
python -m ruff check .
python tools/check_python_sources.py
python tools/check_dependency_policy.py
python tools/check_model_artifacts.py
python eval.py --retriever bm25 --queries evaluation/suites/property/queries.jsonl --chunks evaluation/suites/property/chunks.jsonl --k 1 3 5 --depth 10
python eval.py --retriever bm25 --queries evaluation/suites/constitutional_law/queries.jsonl --chunks evaluation/suites/constitutional_law/chunks.jsonl --k 1 3 5 --depth 10
python eval.py --retriever bm25 --queries evaluation/suites/table_family/queries.jsonl --chunks evaluation/suites/table_family/chunks.jsonl --k 1 3 5 --depth 10
```

Expected: everything green; record observed pytest counts.

- [ ] **Step 3: Final commit and handoff**

```bash
git add -A
git commit -m "Integrate the scan triage command with repository gates

python -m pytest -q: <observed counts>; ruff, compile, dependency,
model-artifact, and architecture-inventory gates green; offline
evaluation suites unchanged."
```

Report: branch name, final SHA, observed counts, any gate expectation
diffs, and the telemetry-stage finding from Task 4 Step 3. Do NOT open a
PR, regenerate benchmarks, or edit ROADMAP.md — the owner's machine
performs the evidence and candidate ritual.

---

## Deviations and escalation

- Line references may drift; trust the code and keep the behavioral
  requirement, noting differences in the final report.
- If `page.get_text` or `doc.get_toc` behave differently than assumed
  under the installed PyMuPDF, adapt the handler (not the policy) and say
  so.
- Never commit with a failing gate.
