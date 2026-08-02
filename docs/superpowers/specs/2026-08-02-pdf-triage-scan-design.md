# First-Pass PDF Triage Scan Design

Date: 2026-08-02
Status: Approved by owner (Cade) in brainstorming session; pending spec review.

## Purpose

Give a new casebook PDF a fast, deterministic report card before any
conversion work: what kind of PDF it is, whether its text layer is usable,
what `preprocess` would decide, whether OCR will be needed, whether the
default watermark pattern fires, and whether a table of contents is
detectable — printed to the console in seconds, so surprises surface before
a long `full` run instead of in the middle of one.

## Owner decisions (2026-08-02)

1. Pain point: new-book triage (not per-page OCR decisioning, robustness,
   or speed — each remains available as a later sub-project).
2. Shape: console-only. No persisted artifact, no schema receipt, no JSON
   file. The card is advisory output; nothing downstream consumes it.
3. Command: a new read-only `rag.py scan` subcommand; LLM-free.

## Architecture

Deterministic assessment policy lives in `ingestion_core.py` beside the
text-usability thresholds it reuses; `rag.py` gains a thin `scan_pdf`
handler that gathers raw signals (PyMuPDF metadata, the existing
`_analyze_pdf_images` statistics, watermark matches, outline/TOC text
signals), passes them to the policy, and prints the rendered card. No file
is written; existing commands are unchanged.

## Command

`python rag.py scan --pdf <path>` (also accepts `--watermark` to override
the default pattern, mirroring existing flags; no other options). Read-only
and LLM-free. Exit code 0 whenever a card was printed — including cards
that report a broken text layer — and 1 only when the PDF cannot be opened
or read at all (missing, empty, encrypted-and-unreadable).

## Report card contents

1. **Document** — page count, file size, PyMuPDF `producer`/`creator`
   metadata, and a scanner fingerprint line when the metadata matches
   known OCR products (case-insensitive substring match for at least
   "Paper Capture" and "ABBYY"; unknown producers print verbatim).
2. **Page composition** — from one `_analyze_pdf_images` pass (reused, not
   reimplemented): total pages, pages with large background rasters (the
   existing `min_dim=1000` default), and the born-digital remainder.
3. **Text layer** — the verdict of the existing
   `ingestion_core.pdf_text_layer_is_usable` thresholds plus usable and
   unusable page counts, so the card can never disagree with what
   `preprocess` would conclude.
4. **Preprocess forecast** — which branch `preprocess_pdf` would take for
   this file (strip backgrounds to a text-only PDF / keep images for OCR /
   no preprocessing needed), computed through the same assessment seams
   without writing anything.
5. **Watermark** — number of pages (within the sampled range below) whose
   extracted text matches the active watermark pattern (default
   `DEFAULT_WATERMARK`), plus a hint to pass `--watermark` when the count
   is zero for a book that visibly carries one.
6. **TOC detectability** — whether the PDF has embedded outline bookmarks
   (`document.get_toc()` non-empty), and a heuristic scan of the first 30
   pages for a contents page (a page whose extracted text, after the
   existing OCR-artifact whitespace normalization, contains a line equal
   to "contents", "table of contents", or "summary of contents",
   case-insensitive). Both signals print independently.
7. **Verdict** — one suggested next command line: `python rag.py full
   --pdf <path>` with `--ocr` appended exactly when the preprocess
   forecast is the keep-images-for-OCR branch.

Sampling bound: watermark and contents-page scans read at most the first
30 pages plus 10 evenly spaced later pages, keeping the whole card
seconds-fast on 900-page books. The card states the sampled page count.

## New policy surface (`ingestion_core.py`)

- `@dataclass(frozen=True) PDFTriage` — fields: `page_count: int`,
  `large_image_pages: int`, `text_layer_usable: bool`,
  `usable_pages: int`, `unusable_pages: int`, `preprocess_forecast: str`
  (one of `"strip-backgrounds"`, `"keep-for-ocr"`, `"no-preprocess"`),
  `scanner_fingerprint: str | None`, `watermark_page_matches: int`,
  `sampled_pages: int`, `has_outline: bool`, `contents_page_found: bool`,
  `ocr_recommended: bool`.
- `assess_pdf_triage(stats: dict, *, producer: str, creator: str,
  watermark_page_matches: int, sampled_pages: int, has_outline: bool,
  contents_page_found: bool, thresholds: PDFIngestionThresholds)
  -> PDFTriage` — pure; derives the forecast from the same threshold
  helpers `preprocess_pdf` uses (`pdf_text_layer_is_usable` and the
  large-image ratio rule), so forecast and reality cannot drift.

`rag.py` additions: a `_render_pdf_triage(triage: PDFTriage, pdf_path:
Path) -> str` pure renderer, the `scan_pdf(pdf_path, *, watermark=...)`
handler, and the `scan` subparser + dispatch. The forecast threshold logic
is not duplicated in `rag.py`.

## Error handling

- Missing or empty PDF: the existing `_require_file` behavior (message +
  exit 1).
- PyMuPDF open failure or encrypted document: print a short card
  containing only the Document section and the failure reason; exit 1.
- Per-page text extraction errors during sampling: count the page as
  unreadable, continue, and note the count in the Text layer section;
  never abort the card for one bad page.

## Testing

- `tests/test_ingestion_core.py` additions: an accept/reject matrix for
  `assess_pdf_triage` — forecast selection across the threshold
  boundaries (usable text + high raster ratio → strip; unusable text +
  rasters → keep-for-OCR; low raster ratio → no-preprocess),
  fingerprint detection, and `ocr_recommended` coupling to the forecast.
- `tests/` CLI test: drive `rag.main(["scan", "--pdf", ...])` with
  monkeypatched `_analyze_pdf_images` and page-text sampling to assert
  card sections, exit codes, and that no file is created in the output
  directory.
- No real PDF fixtures are committed; synthetic stats dicts and
  monkeypatched extraction only (privacy policy).

## Out of scope

- Any persisted artifact or schema receipt (explicit owner decision).
- Per-page OCR decisioning, conversion robustness (OOM batching), and
  speed work — candidate later sub-projects.
- LLM-based TOC probing; `full` consuming the triage automatically.
- Changes to `preprocess_pdf`, conversion, or any existing command.
