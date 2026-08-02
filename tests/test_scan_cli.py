"""Renderer, handler, and CLI tests for rag.py scan."""

from pathlib import Path

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
