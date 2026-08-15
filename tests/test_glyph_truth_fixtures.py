"""Recall fixtures for the CID decode-correctness detector.

Each fixture is a minimal Type0/Identity-H font without ToUnicode built
deterministically in-test (no committed binary). Probed channel behavior
under the pinned PyMuPDF line:

- CIDs above the substitute font's range extract as wrong-but-valid
  codepoints (the raw CID values), so the text channel stays silent while
  the glyph trace reports ``(U+FFFD, glyph 0)`` — only the glyph-level
  pass catches the page. This is the recall case the 2026-08-03 cycle
  left open.
- CIDs in the PUA range (0xF000+) extract as PUA characters, firing the
  original text channel.
- Low CIDs that the substitute font can map extract as plausible Latin
  mojibake that neither channel can distinguish from real text; that
  residual miss is asserted here as a characterization and recorded in
  the ROADMAP as an open follow-up.
"""
from __future__ import annotations

import pytest

import ingestion_core

pymupdf = pytest.importorskip("pymupdf")

# CIDs the substitute font cannot map: text extracts as the raw CID
# codepoints (wrong but valid), the trace reports unmapped .notdef glyphs.
UNMAPPED_HIGH_CIDS = b"010002000300123423453456"
# CIDs in the Private Use Area: text extraction itself shows PUA chars.
PUA_CIDS = b"F000F100F200F300F400F500"
# CIDs the substitute font maps to plausible Latin letters (from "Hello").
PLAUSIBLE_LOW_CIDS = b"00480065006C006C006F"


def broken_cmap_pdf_bytes(cid_hex: bytes) -> bytes:
    content = b"BT /F1 12 Tf 72 720 Td <" + cid_hex + b"> Tj ET"
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


def _default_threshold() -> float:
    return (ingestion_core.DEFAULT_PDF_INGESTION_THRESHOLDS
            .max_cid_char_ratio)


def test_unmapped_glyphs_are_caught_only_by_the_glyph_channel():
    pdf = broken_cmap_pdf_bytes(UNMAPPED_HIGH_CIDS)
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        text = doc[0].get_text("text")
        # The text channel is blind here: extraction yields wrong-but-
        # valid codepoints, not replacement or PUA characters.
        assert ingestion_core.cid_suspect_ratio(text) <= (
            _default_threshold())
        stats = ingestion_core.page_glyph_stats(doc[0])
        assert stats is not None
        assert stats.notdef_glyphs > 0
        assert stats.unmapped_glyphs > 0
        analysis = ingestion_core.analyze_pdf_document(doc, 1000)
    assert analysis.stats["cid_garbled_pages"] == 1
    assert analysis.stats["pages_with_usable_text"] == 0


def test_pua_extraction_is_caught_by_the_text_channel():
    pdf = broken_cmap_pdf_bytes(PUA_CIDS)
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        text = doc[0].get_text("text")
        assert ingestion_core.cid_suspect_ratio(text) > (
            _default_threshold())
        analysis = ingestion_core.analyze_pdf_document(doc, 1000)
    assert analysis.stats["cid_garbled_pages"] == 1


def test_plausible_latin_mojibake_remains_a_recorded_miss():
    # Characterization of the residual gap: when the substitute font maps
    # broken CIDs onto ordinary Latin letters, neither channel can call
    # the page garbled without a plausibility model (ROADMAP follow-up).
    pdf = broken_cmap_pdf_bytes(PLAUSIBLE_LOW_CIDS)
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        analysis = ingestion_core.analyze_pdf_document(doc, 1000)
    assert analysis.stats["cid_garbled_pages"] == 0


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
    assert analysis.stats["pages_with_usable_text"] == 1
