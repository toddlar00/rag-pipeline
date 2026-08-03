# Ingestion Quick Wins Design

Date: 2026-08-03
Status: Approved by owner (Cade) in brainstorming session; pending spec review.
Origin: cross-project research survey (Docling releases, MinerU, marker/surya,
unstructured, olmOCR, GROBID, PyMuPDF4LLM) for material improvements
applicable to this pipeline; the owner selected the ingestion quick-wins
bundle first. Deferred alternatives recorded under Out of scope.

## Purpose

Four zero-new-dependency ingestion accuracy upgrades for scanned law-school
casebooks, each borrowed from a surveyed open-source project and implemented
inside the existing Docling/PyMuPDF/RapidOCR stack:

1. Layout-aware per-region OCR as the new `--ocr` default.
2. PDF-bookmark heading levels as a deterministic cross-check for the LLM
   TOC scaffold.
3. A CID/unmapped-glyph ratio detector that catches mojibake text layers
   the density thresholds cannot see.
4. Surfacing Docling's native per-page conversion confidence scores.

## Owner decisions (2026-08-03)

1. Bundle contents: all four items above (confidence integration included).
2. OCR default: `--ocr` now means layout-aware per-region OCR
   (`PDF_AWARE_LAYOUT_REGIONS`); the previous force-full-page behavior
   remains reachable via a new explicit `--ocr-full-page` flag. The
   conversion-parameters schema records the mode and version-bumps so
   existing receipts fail closed and rebuilds adopt the change explicitly.

## Design

### 1. Per-region OCR mode (source: Docling v2.116+ `OcrMode`)

- The conversion pipeline options currently set
  `RapidOcrOptions(..., force_full_page_ocr=...)` with a binary whole-page
  choice. Change: when OCR is requested, set the pinned Docling 2.117
  `OcrMode.PDF_AWARE_LAYOUT_REGIONS`, which runs layout detection first and
  OCRs only regions that need it while trusting the existing text layer
  elsewhere on the page. `--ocr-full-page` selects the old
  `force_full_page_ocr=True` behavior.
- The credential-free conversion parameters dict (the one that today
  carries `{"ocr": ..., "force_full_page_ocr": ...}`) gains
  `"ocr_mode": "pdf-aware-layout-regions" | "full-page" | "off"` and its
  version constant bumps. Receipt validation already fails closed on
  parameter changes; no new mechanism is needed.
- If the pinned Docling/RapidOCR combination rejects the mode at runtime
  (API drift), conversion fails closed with a clear message rather than
  silently falling back to full-page.

### 2. Bookmark heading cross-check (source: Docling #3688; PyMuPDF outline)

- Where the source PDF carries an outline/bookmark tree, read it via the
  established PyMuPDF idiom (`doc.get_toc()`, precedented by `scan_pdf`)
  into a normalized list of `(level, title, page)`.
- After the LLM TOC scaffold/verification completes, compare verified TOC
  entries against the bookmark tree: title matches (whitespace-collapsed,
  casefolded) with mismatched levels, bookmark titles absent from the
  scaffold, and scaffold chapter titles absent from the bookmarks each
  produce a recorded warning naming both sides. Advisory only: warnings go
  to the conversion log and run telemetry; no hard failure (publisher
  bookmarks can themselves be wrong), no receipt schema change, no change
  to scaffold content.
- PDFs without an outline skip the cross-check at zero cost.

### 3. CID/unmapped-glyph ratio detector (source: MinerU classify logic)

- New rag-side per-page statistic gathered during the existing image/text
  analysis pass using PyMuPDF `page.get_text("rawdict")`: the ratio of
  characters that decode to the Unicode replacement character, PUA
  codepoints, or `.notdef`-style glyph references, over total characters.
- New additive `PDFIngestionThresholds` field `max_cid_char_ratio` (default
  chosen during implementation from a probe of a known-good and a
  known-garbled fixture page; the existing replacement-char ratio default
  0.02 is the starting point) and a new stats key `cid_garbled_pages`.
  `pdf_page_text_is_usable` gains the decode-correctness term; pages that
  are dense but mojibake now fail usability, steering preprocess and OCR
  decisions correctly for broken-cmap ABBYY/Paper Capture scans.
- The `scan` report card's Text layer section adds
  `garbled (CID) pages: N` when N > 0, and `PDFTriage` gains the
  corresponding field.

### 4. Docling confidence surfacing (source: Docling `ConversionResult.confidence`)

- After conversion, read the per-page confidence grades
  (`layout_score`, `ocr_score`, `parse_score`, `low_grade`) that the
  pinned Docling already computes and the pipeline currently discards.
- Emit: a log summary line (mean scores, count of low-grade pages), a
  warning listing low-grade page numbers (bounded to the first 20), and a
  redacted run-telemetry event with the aggregate numbers. Advisory only —
  no receipt binding, no threshold gating this round; gating on confidence
  is an explicit follow-up once real-corpus distributions are observed.
- If the attribute is absent at runtime (API drift), skip with a single
  log notice — never fail conversion over missing advisory data.

## Error handling summary

Receipt-bound behavior (item 1) fails closed on any mismatch or API drift.
Advisory signals (items 2 and 4) degrade to logged notices and never fail a
run. Item 3 changes usability verdicts by design — that is its purpose —
through the same threshold machinery preprocess already trusts.

## Testing

- Item 1: conversion-option wiring tests with a monkeypatched Docling
  options object (mode selected per flag combination; parameters dict and
  version constant asserted; fail-closed path on a rejecting stub).
- Item 2: pure comparison-policy tests over synthetic bookmark trees vs
  synthetic scaffold entries (match, level-mismatch, missing-either-side,
  no-outline skip); wiring test asserting warnings reach the log.
- Item 3: threshold matrix tests in `tests/test_ingestion_core.py`
  (dense-but-garbled fails, clean passes, boundary at the ratio), stats
  gathering tests with a fake rawdict page, and scan-card tests for the
  new line and `PDFTriage` field.
- Item 4: mapping tests from a fake confidence object to the summary/warning
  outputs, including the absent-attribute skip path.
- Full repository gate battery green (pytest, ruff, compile, policies,
  inventory refresh via tool, offline eval suites unchanged).

## Out of scope (recorded research backlog)

- Contextual retrieval (Anthropic index-time chunk context), fusion/BM25F
  scoring upgrades, and the evaluation robustness pack (hard negatives,
  paired bootstrap, α-nDCG, RGB counterfactual suites, LegalBench IRAC
  slices) — each a candidate for its own later cycle.
- XY-Cut++ reading order, footnote-region classification/linking, olmOCR
  document anchoring and error budgets, per-block OCR repair, graphics-op
  density pre-filter, PyMuPDF `get_layout()` header/footer classification.
- Any confidence-based gating (item 4 stays advisory), receipt schema
  changes beyond the conversion-parameters version bump, and any new
  dependency.
