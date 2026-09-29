"""TOC dot leaders extracted as control characters or U+FFFD must not fuse rows.

Some PDF text layers encode a table of contents' dot leaders in a symbol font
that extraction returns as a backspace plus a run of U+FFFD. The parser then
finds no page number in a one-cell "Chapter N · title<glyphs>57" row, parks it
as a pending chapter label, and joins it to the next row. The fused title
("Chapter 2 · title<glyphs>57 Summary of Contents<glyphs>") then heads every
chunk of that chapter and can never bind to a source heading, so the document
fails heading-lineage validation.

The split runs only as a second parse, when the ordinary parse leaves such a
fused chapter title; every other table of contents parses exactly as before.
All text here is synthetic.
"""

import rag
from test_scaffold_structure import _cell, _table

GLYPHS = "\b" + "�" * 12


def _parse(cells, page=5):
    return rag._parse_toc_tables({"tables": [_table(page, cells)]}, page, page)


def test_a_glyph_leader_chapter_row_is_not_fused_with_the_next_row():
    summary_row = [_cell("Summary of Contents\b " + "�" * 9, 1, 0),
                   _cell("57", 1, 1)]
    cells = [_cell("Chapter 2 · Widget Safety" + GLYPHS + "57", 0, 0, col_end=2),
             *summary_row]

    entries = _parse(cells)

    # The summary row parses exactly as it would on its own.
    alone = [_cell(cell["text"], 0, cell["start_col_offset_idx"])
             for cell in summary_row]
    assert entries == [
        {"level": 1, "title": "Chapter 2 · Widget Safety", "page": 57},
        *_parse(alone),
    ]


def test_dot_leader_chapter_rows_parse_as_before():
    cells = [_cell("Chapter 2 · Widget Safety . . . . . . 57", 0, 0, col_end=2),
             _cell("Summary of Contents", 1, 0),
             _cell("57", 1, 1)]

    assert _parse(cells) == [
        {"level": 1, "title": "Chapter 2 · Widget Safety", "page": 57},
        {"level": 3, "title": "Summary of Contents", "page": 57},
    ]


def test_glyphs_in_section_rows_with_a_page_column_parse_as_before():
    cells = [_cell("Chapter 2 · Widget Safety", 0, 0), _cell("57", 0, 1),
             _cell("A. Inspection" + GLYPHS, 1, 0), _cell("58", 1, 1)]

    assert _parse(cells) == [
        {"level": 1, "title": "Chapter 2 · Widget Safety", "page": 57},
        {"level": 2, "title": "A. Inspection" + GLYPHS, "page": 58},
    ]


def test_a_fused_copy_dropped_by_primary_dedup_leaves_the_parse_unchanged():
    # A clean summary entry for Chapter 2 wins primary dedup over the fused
    # detailed-contents copy, so the ordinary parse has no fused chapter title
    # and the split does not run.
    summary = _table(5, [_cell("Chapter 2 · Widget Safety", 0, 0),
                         _cell("57", 0, 1)])
    detailed = _table(6, [
        _cell("Chapter 2 · Widget Safety" + GLYPHS + "57", 0, 0, col_end=2),
        _cell("Summary of Contents", 1, 0), _cell("57", 1, 1)])

    entries = rag._parse_toc_tables({"tables": [summary, detailed]}, 5, 6)

    assert entries == [
        {"level": 1, "title": "Chapter 2 · Widget Safety", "page": 57},
    ]
