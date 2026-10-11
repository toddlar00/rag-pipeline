"""A heading must not be dropped because a same-text heading follows on its page.

``_filter_same_page_future_headings`` drops a chunk's heading when every
same-page heading with that text lies below the chunk: page-level scaffold
assignment and some chunker stacks attach the next ``Notes & Questions`` or
case heading to the final paragraph of the preceding case. It matches by
text only, so a short opinion-part marker such as ``A`` or ``V`` that governs
the chunk from an earlier page was dropped whenever the same marker recurred
below the chunk on the chunk's own page (``II`` > ``A`` ... ``III`` > ``A``).
The occurrence-bound lineage audit still placed the earlier marker in the
record's scope, so the record failed heading-lineage validation.

A heading whose text matches the nearest source heading before the chunk in
body order is now kept: that occurrence, not the same-text heading below, is
the one the chunk's stack carries. All text is synthetic.
"""

import heading_lineage
import rag
from test_heading_lineage import _document, _item


def _one_page_metadata(page=12):
    return {
        "page_start": page,
        "page_end": page,
        "source_items": [{
            "ref": "#/texts/7",
            "spans": [{
                "page": page,
                "bbox": [70.0, 600.0, 470.0, 700.0],
                "origin": "BOTTOMLEFT",
            }],
        }],
    }


def _identity(text):
    return rag._source_heading_identity(text)


def test_a_marker_governing_from_an_earlier_page_is_kept():
    # The chunk sits above a second "A" on page 12; the "A" its stack carries
    # opened on page 11, so it is the nearest heading before the chunk.
    spans = {(12, _identity("A")): [(500.0, 512.0)]}
    headings = ["Orion Systems v. Delta Labs", "II", "A"]

    kept = rag._filter_same_page_future_headings(
        headings, _one_page_metadata(), spans, {12: 800.0},
        governing_identity=_identity("A"))

    assert kept == headings


def test_without_governing_evidence_the_marker_is_still_dropped():
    spans = {(12, _identity("A")): [(500.0, 512.0)]}

    kept = rag._filter_same_page_future_headings(
        ["Orion Systems v. Delta Labs", "II", "A"], _one_page_metadata(),
        spans, {12: 800.0})

    assert kept == ["Orion Systems v. Delta Labs", "II"]


def test_a_future_heading_is_dropped_when_another_heading_governs():
    spans = {(12, _identity("Notes & Questions")): [(500.0, 512.0)]}

    kept = rag._filter_same_page_future_headings(
        ["Orion Systems v. Delta Labs", "Notes & Questions"],
        _one_page_metadata(), spans, {12: 800.0},
        governing_identity=_identity("Orion Systems v. Delta Labs"))

    assert kept == ["Orion Systems v. Delta Labs"]


def test_nearest_preceding_heading_refs_follow_body_order():
    document = _document([
        _item("#/texts/0", "section_header", "II", page=11, top=700),
        _item("#/texts/1", "section_header", "A", page=11, top=650),
        _item("#/texts/2", "text", "First body", page=11, top=600),
        _item("#/texts/3", "text", "Second body", page=12, top=700),
        _item("#/texts/4", "section_header", "III", page=12, top=520),
        _item("#/texts/5", "section_header", "A", page=12, top=500),
        _item("#/texts/6", "text", "Third body", page=12, top=450),
    ])

    nearest = heading_lineage.nearest_preceding_heading_refs(document)

    assert nearest == {
        "#/texts/0": "#/texts/0",
        "#/texts/1": "#/texts/1",
        "#/texts/2": "#/texts/1",
        "#/texts/3": "#/texts/1",
        "#/texts/4": "#/texts/4",
        "#/texts/5": "#/texts/5",
        "#/texts/6": "#/texts/5",
    }


def test_a_running_heading_ends_the_nearest_heading(monkeypatch):
    # A running section header between a heading and later items can resume
    # an ancestor, and the lineage audit then supersedes that heading (a
    # running_ancestor_resumption), so it no longer proves what governs them.
    document = _document([
        _item("#/texts/0", "section_header", "Notes & Questions", page=11),
        _item("#/texts/1", "list_item", "First note", page=11, top=650),
        _item("#/texts/2", "section_header", "Section 10.02", page=12,
              top=760),
        _item("#/texts/3", "text", "Continuing body", page=12, top=700),
        _item("#/texts/4", "section_header", "Notes & Questions", page=12,
              top=500),
        _item("#/texts/5", "list_item", "Next note", page=12, top=450),
    ])
    monkeypatch.setattr(
        heading_lineage, "running_section_heading_refs",
        lambda _document: frozenset({"#/texts/2"}))

    nearest = heading_lineage.nearest_preceding_heading_refs(document)

    assert nearest["#/texts/1"] == "#/texts/0"
    assert "#/texts/2" not in nearest and "#/texts/3" not in nearest
    assert nearest["#/texts/5"] == "#/texts/4"


def test_excluded_headings_do_not_govern():
    document = _document([
        _item("#/texts/0", "section_header", "A", page=11, top=700),
        _item("#/texts/1", "section_header", "Demoted cite", page=11, top=650),
        _item("#/texts/2", "text", "Body", page=11, top=600),
    ])

    nearest = heading_lineage.nearest_preceding_heading_refs(
        document, excluded_heading_refs={"#/texts/1"})

    assert nearest["#/texts/2"] == "#/texts/0"
    assert "#/texts/1" not in nearest.values()
