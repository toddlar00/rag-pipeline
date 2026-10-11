"""A chapter TOC row whose leaders OCR dropped must not fuse with the next row.

Fresh OCR of a scanned table of contents can omit the dot leaders, leaving a
one-cell row such as "Chapter 1: Overview 6" with a single space before the
page. The ordinary parse needs at least two leader or space characters before
a trailing page, so the row has no page, is parked as a pending chapter label,
and is joined to the next row: "Chapter 1: Overview 6 A. Origins". That title
can never bind to a source heading, so the document fails heading-lineage
validation.

Only when the ordinary parse leaves such a fused chapter title (a bare page,
then an outline-labelled entry, with that page no later than the fused entry's
own page) is the TOC parsed again. In that parse exactly the chapter rows that
produced those titles read their single-spaced trailing number as the page.
Every other table of contents parses exactly as before. All text here is
synthetic.
"""

import rag
from test_scaffold_structure import _cell, _table


def _parse(cells, page=5):
    return rag._parse_toc_tables({"tables": [_table(page, cells)]}, page, page)


def test_a_leaderless_chapter_row_is_not_fused_with_the_next_section_row():
    cells = [_cell("Chapter 1: Overview 6", 0, 0),
             _cell("A. Origins of Widgets . . 6", 1, 0),
             _cell("B. Sources of Widget Law . . 8", 2, 0)]

    assert _parse(cells) == [
        {"level": 1, "title": "Chapter 1: Overview", "page": 6},
        {"level": 2, "title": "A. Origins of Widgets", "page": 6},
        {"level": 2, "title": "B. Sources of Widget Law", "page": 8},
    ]


def test_only_the_chapter_rows_that_fused_are_split():
    # Chapter 2's title ends in a number but continues on the next line, so it
    # never produced a fused "<page> <outline label>" title and keeps joining.
    cells = [_cell("Chapter 1: Overview 6", 0, 0),
             _cell("A. Origins of Widgets . . 6", 1, 0),
             _cell("Chapter 2: Claims Under Rule 12", 2, 0),
             _cell("and Related Motions . . 40", 3, 0)]

    assert _parse(cells) == [
        {"level": 1, "title": "Chapter 1: Overview", "page": 6},
        {"level": 2, "title": "A. Origins of Widgets", "page": 6},
        {"level": 1,
         "title": "Chapter 2: Claims Under Rule 12 and Related Motions",
         "page": 40},
    ]


def test_a_wrapped_chapter_title_ending_in_a_number_parses_as_before():
    cells = [_cell("Chapter 7: Claims Under Section 1983", 0, 0),
             _cell("and Related Statutes . . 120", 1, 0)]

    assert _parse(cells) == [{
        "level": 1,
        "title": "Chapter 7: Claims Under Section 1983 and Related Statutes",
        "page": 120,
    }]


def test_a_number_later_than_the_fused_entry_page_is_not_a_page():
    # "1983 A. Remedies . . 120": 1983 cannot be the page of a chapter whose
    # first section starts on p. 120, so the fused title is left as it is.
    cells = [_cell("Chapter 7: Statutes of 1983", 0, 0),
             _cell("A. Remedies . . 120", 1, 0)]

    assert _parse(cells) == [{
        "level": 1,
        "title": "Chapter 7: Statutes of 1983 A. Remedies",
        "page": 120,
    }]


def test_a_fused_section_level_title_does_not_trigger_a_second_parse():
    cells = [_cell("Chapter 1: Overview . . 6", 0, 0),
             _cell("A. Origins 6 1. Early Widgets . . 7", 1, 0)]

    assert _parse(cells) == [
        {"level": 1, "title": "Chapter 1: Overview", "page": 6},
        {"level": 2, "title": "A. Origins 6 1. Early Widgets", "page": 7},
    ]


def test_dot_leader_chapter_rows_parse_as_before():
    cells = [_cell("Chapter 1: Overview . . . . 6", 0, 0),
             _cell("A. Origins of Widgets . . 6", 1, 0)]

    assert _parse(cells) == [
        {"level": 1, "title": "Chapter 1: Overview", "page": 6},
        {"level": 2, "title": "A. Origins of Widgets", "page": 6},
    ]
