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
