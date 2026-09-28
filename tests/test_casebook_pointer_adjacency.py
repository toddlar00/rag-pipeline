"""Fail-closed guards around casebook-supplement pointer joins.

A pointer may join its lettered section title only when the title owns no
content of its own, either in serialized order or on the printed page, so a
join can never re-parent a document that passes lineage today.  Every
fixture is synthetic; no private textbook text is used.
"""

import pytest

import heading_lineage
import quality_core
import rag
from test_casebook_pointer_headings import (
    T, _ISSUE_FIELDS, _composites, _document, _item, _page_turn, _record,
    _run,
)


TITLE = "B. SAMPLE TOPIC"
POINTER = "CHAPTER 2.B: SAMPLE"


def _ordered(items, order, *, tables=()):
    """Serialize ``items`` and ``tables`` in an explicit body order."""
    document = _document([*items, *tables])
    document["texts"] = list(items)
    document["tables"] = list(tables)
    document["body"] = {"children": [{"cref": ref} for ref in order]}
    return document


def _box(ref, label, *, top, bottom, text=""):
    item = _item(ref, label, text, top=top)
    item["prov"][0]["bbox"]["b"] = bottom
    return item


def _source_record(ref, label):
    record = _record(ref)
    record["metadata"]["source_items"][0]["label"] = label
    if label == "table":
        record["metadata"]["content_source"] = "table"
    return record


def _audit(document, records):
    """Assign source-heading paths, then attach and independently audit."""
    rag._assign_source_heading_paths(
        document, records, structural_ranges=set(),
        excluded_heading_refs=set())
    heading_lineage.attach_heading_bindings(document, records)
    audit = heading_lineage.audit_heading_bindings(document, records)
    paths = [record["metadata"]["section_path"] for record in records]
    return paths, {field: audit[field] for field in _ISSUE_FIELDS
                   if audit[field]}


def _text_printed_in_the_gap():
    # Serialized after the pointer but printed between title and pointer:
    # the scope walk rolls it back onto the section title today.
    return _ordered([
        _item(T + "0", "text", "Earlier body.", top=760),
        _item(T + "1", "section_header", TITLE, top=700),
        _item(T + "2", "section_header", POINTER, top=640, left=90),
        _item(T + "3", "text", "Between text.", top=680),
        _item(T + "4", "text", "Update body.", top=600),
    ], [T + "0", T + "1", T + "2", T + "3", T + "4"]), [
        _record(T + "0"), _record(T + "3"), _record(T + "4")]


def _table_printed_in_the_gap():
    # Serialized before the title but printed below it: the table binds to
    # the heading printed above it today.
    return _ordered([
        _item(T + "0", "text", "Earlier body.", top=780),
        _item(T + "1", "section_header", TITLE, top=740),
        _item(T + "2", "section_header", POINTER, top=640, left=90),
        _item(T + "3", "text", "Update body.", top=600),
    ], [T + "0", "#/tables/0", T + "1", T + "2", T + "3"],
        tables=[_box("#/tables/0", "table", top=700, bottom=660)]), [
        _record(T + "0"), _source_record("#/tables/0", "table"),
        _record(T + "3")]


def _text_beside_the_title():
    # Printed on the title line, serialized after the pointer.
    return _ordered([
        _item(T + "0", "text", "Earlier body.", top=760),
        _item(T + "1", "section_header", TITLE, top=700, right=200),
        _item(T + "2", "section_header", POINTER, top=680, left=90),
        _item(T + "3", "text", "Margin note.", top=700, left=300),
        _item(T + "4", "text", "Update body.", top=640),
    ], [T + "0", T + "1", T + "2", T + "3", T + "4"]), [
        _record(T + "0"), _record(T + "3"), _record(T + "4")]


def _text_above_a_next_page_pointer():
    # The pointer tops the next page; a note printed above it is serialized
    # after it and rolls back onto the section title today.
    return _ordered([
        _item(T + "0", "text", "Earlier body.", page=1, top=300),
        _item(T + "1", "section_header", TITLE, page=1, top=100),
        _item(T + "2", "page_footer", "7", page=1, top=40,
              layer="furniture"),
        _item(T + "3", "section_header", POINTER, page=2, top=700, left=90),
        _item(T + "4", "text", "Running note.", page=2, top=760),
        _item(T + "5", "text", "Update body.", page=2, top=640),
    ], [T + "0", T + "1", T + "2", T + "3", T + "4", T + "5"]), [
        _record(T + "0"), _record(T + "4"), _record(T + "5")]


def _table_below_a_page_bottom_title():
    # The pointer tops the next page; a table printed below the title on its
    # page is serialized before the title and binds to it today.
    return _ordered([
        _item(T + "0", "text", "Earlier body.", page=1, top=300),
        _item(T + "1", "section_header", TITLE, page=1, top=140),
        _item(T + "2", "page_footer", "7", page=1, top=40,
              layer="furniture"),
        _item(T + "3", "section_header", POINTER, page=2, top=760, left=90),
        _item(T + "4", "text", "Update body.", page=2, top=700),
    ], [T + "0", "#/tables/0", T + "1", T + "2", T + "3", T + "4"],
        tables=[_box("#/tables/0", "table", top=110, bottom=60)]), [
        _record(T + "0"), _source_record("#/tables/0", "table"),
        _record(T + "4")]


@pytest.mark.parametrize("layout", [
    _text_printed_in_the_gap, _table_printed_in_the_gap,
    _text_beside_the_title, _text_above_a_next_page_pointer,
    _table_below_a_page_bottom_title,
])
def test_printed_gap_content_keeps_todays_passing_paths(layout):
    document, records = layout()

    paths, issues = _audit(document, records)

    assert paths == ["", TITLE, POINTER]
    assert issues == {}
    assert heading_lineage._casebook_pointer_runs(document) == {}


@pytest.mark.parametrize("gap_item", ["text", "table"])
def test_printed_gap_content_keeps_a_later_roman_heading_nested(gap_item):
    # Without a proven pointer run "I." stays the Roman numeral it is today.
    items = [
        _item(T + "0", "section_header", "H. FIRST UPDATE", top=790),
        _item(T + "1", "text", "H body.", top=770),
        _item(T + "2", "section_header", "I. SECOND UPDATE", top=740),
        _item(T + "3", "section_header", "CHAPTER 7 GENERALLY", top=640,
              left=90),
        _item(T + "4", "text", "Update body.", top=600),
    ]
    if gap_item == "text":
        items.append(_item(T + "5", "text", "Between text.", top=700))
        gap_ref, order = T + "5", [
            T + "0", T + "1", T + "2", T + "3", T + "5", T + "4"]
        document = _ordered(items, order)
    else:
        gap_ref, order = "#/tables/0", [
            T + "0", T + "1", "#/tables/0", T + "2", T + "3", T + "4"]
        document = _ordered(items, order, tables=[
            _box(gap_ref, "table", top=700, bottom=660)])
    records = [
        _record(T + "1"), _source_record(gap_ref, gap_item),
        _record(T + "4")]

    paths, issues = _audit(document, records)

    assert paths == [
        "H. FIRST UPDATE", "H. FIRST UPDATE → I. SECOND UPDATE",
        "CHAPTER 7 GENERALLY"]
    assert issues == {}
    assert heading_lineage._casebook_pointer_runs(document) == {}


def _pointer_two_pages_later():
    return _document([
        _item(T + "0", "text", "Earlier body.", page=1, top=300),
        _item(T + "1", "section_header", TITLE, page=1, top=100),
        _item(T + "2", "page_footer", "7", page=1, top=40, layer="furniture"),
        _item(T + "3", "page_footer", "8", page=2, top=40, layer="furniture"),
        _item(T + "4", "section_header", POINTER, page=3, top=760, left=90),
        _item(T + "5", "text", "Update body.", page=3, top=700),
    ]), [T + "0", T + "5"]


def _pointer_printed_above_the_title():
    return _ordered([
        _item(T + "0", "text", "Earlier body.", top=760),
        _item(T + "1", "section_header", TITLE, top=640),
        _item(T + "2", "section_header", POINTER, top=700, left=90),
        _item(T + "3", "text", "Update body.", top=600),
    ], [T + "0", T + "1", T + "2", T + "3"]), [T + "0", T + "3"]


def _pointer_split_over_two_boxes():
    pointer = _item(T + "2", "section_header", POINTER, page=1, top=70,
                    left=90)
    pointer["prov"].append(_item(
        T + "2", "section_header", POINTER, page=2, top=760)["prov"][0])
    return _ordered([
        _item(T + "0", "text", "Earlier body.", page=1, top=300),
        _item(T + "1", "section_header", TITLE, page=1, top=100),
        pointer,
        _item(T + "3", "text", "Update body.", page=2, top=700),
    ], [T + "0", T + "1", T + "2", T + "3"]), [T + "0", T + "3"]


def _item_overlapping_the_gap():
    # A side column printed from above the title line into the gap.
    return _document([
        _box(T + "0", "text", top=720, bottom=660, text="Side column."),
        _item(T + "1", "section_header", TITLE, top=700, right=280),
        _item(T + "2", "section_header", POINTER, top=640, left=90),
        _item(T + "3", "text", "Update body.", top=600),
    ]), [T + "0", T + "3"]


@pytest.mark.parametrize("layout", [
    _pointer_two_pages_later, _pointer_printed_above_the_title,
    _pointer_split_over_two_boxes, _item_overlapping_the_gap,
])
def test_pointer_not_printed_just_below_its_title_keeps_todays_failure(
        layout):
    document, body = layout()

    paths, issues, _ = _run(document, body)

    assert paths == ["", POINTER]
    assert issues["missing_refs"] == [T + "1"]
    assert heading_lineage._casebook_pointer_runs(document) == {}


def test_running_heading_above_a_next_page_pointer_keeps_adjacency():
    # Running page headings are furniture, never content of the title.
    document = _document([
        _item(T + "0", "section_header", "SAMPLE SUPPLEMENT", page=1,
              top=790),
        _item(T + "1", "text", "Earlier body.", page=1, top=700),
        _item(T + "2", "section_header", TITLE, page=1, top=100),
        _item(T + "3", "page_footer", "7", page=1, top=40, layer="furniture"),
        _item(T + "4", "section_header", "SAMPLE SUPPLEMENT", page=2,
              top=790),
        _item(T + "5", "section_header", POINTER, page=2, top=740, left=90),
        _item(T + "6", "text", "Update body.", page=2, top=700),
    ])

    paths, issues, _ = _run(document, [T + "1", T + "6"])

    assert paths == ["", f"{TITLE} → {POINTER}"]
    assert issues == {}
    assert heading_lineage._casebook_pointer_runs(document) == {
        T + "5": (T + "2",)}


@pytest.mark.parametrize("footer, bare_label", [
    ("7", True), ("xii", True), ("Page 7", True), ("", True),
    ("1. 2", False), ("1 2 3 4 5 6", False),
])
def test_only_unpublished_page_labels_bridge_a_page_turn(footer, bare_label):
    document = _page_turn(footer)
    # The lineage exemption is exactly quality's never-published footer.
    assert quality_core._looks_substantive_page_footer(
        footer, canonical_frequency=1) is not bare_label

    if bare_label:
        paths, issues, _ = _run(document, [T + "0", T + "4"])
        assert paths == ["", "C. SAMPLE TOPIC → CHAPTER 2.A.1.F.: SAMPLE"]
    else:
        # A published footer is content of the section title, as today.
        paths, issues, _ = _run(document, [T + "0", T + "2", T + "4"])
        assert paths == ["", "C. SAMPLE TOPIC", "CHAPTER 2.A.1.F.: SAMPLE"]
        assert heading_lineage._casebook_pointer_runs(document) == {}
    assert issues == {}


def test_title_after_a_non_body_item_does_not_open_a_pointer_run():
    document = _document([
        _item(T + "0", "section_header", "SAMPLE HEADING", top=760),
        _item(T + "1", "formula", "", top=740),
        _item(T + "2", "section_header", "B. SAMPLE UPDATE", top=700),
        _item(T + "3", "section_header", POINTER, top=680, left=90),
        _item(T + "4", "text", "Update body.", top=640),
    ])

    paths, issues, _ = _run(document, [T + "4"])

    assert paths == [POINTER]
    assert issues["missing_refs"] == [T + "0", T + "2"]
    assert heading_lineage._casebook_pointer_runs(document) == {}


def test_title_fragment_on_another_page_is_not_folded():
    document = _ordered([
        _item(T + "0", "section_header", "H. SAMPLE V. AGENCY", page=1,
              top=100, right=200),
        _item(T + "1", "section_header", "& SECOND PHRASE", page=2,
              top=100, left=202, right=390),
        _item(T + "2", "section_header", "CHAPTER 6, SECTION F: SAMPLE",
              page=2, top=80, left=90),
        _item(T + "3", "text", "Update body.", page=2, top=40),
    ], [T + "0", T + "1", T + "2", T + "3"])

    _, issues, _ = _run(document, [T + "3"])

    assert issues["missing_refs"] == [T + "0", T + "1"]
    assert heading_lineage._casebook_pointer_runs(document) == {}


def test_pointer_run_after_a_structural_range_matches_the_proof():
    ranges = ((2, 2),)
    document = _document([
        _item(T + "0", "section_header", "INTRODUCTION", page=1, top=700),
        _item(T + "1", "text", "Intro body.", page=1, top=680),
        _item(T + "2", "section_header", "CLOSING HEADING", page=1, top=100),
        _item(T + "3", "text", "Range text.", page=2, top=700),
        _item(T + "4", "section_header", "B. SAMPLE UPDATE", page=3,
              top=700),
        _item(T + "5", "section_header", POINTER, page=3, top=680, left=90),
        _item(T + "6", "text", "Update body.", page=3, top=640),
    ])

    paths, issues, audit = _run(document, [T + "1", T + "6"], ranges=ranges)

    assert paths == ["INTRODUCTION", f"B. SAMPLE UPDATE → {POINTER}"]
    # Only the bodiless heading closed by the range fails, as it does today.
    assert issues == {
        "missing_refs": [T + "2"], "unattached_refs": [T + "2"],
        "missing_direct_refs": [T + "2"]}
    proof = heading_lineage._casebook_pointer_runs(
        document, structural_ranges=ranges)
    assert proof == {T + "5": (T + "4",)}
    assert _composites(audit) == proof


def test_tampered_display_cannot_join_a_section_nested_in_the_audit():
    document = _document([
        _item(T + "0", "section_header", "INTRODUCTION", top=780),
        _item(T + "1", "text", "Intro body.", top=760),
        _item(T + "2", "section_header", "B. SAMPLE UPDATE", top=700),
        _item(T + "3", "section_header", POINTER, top=680, left=90),
        _item(T + "4", "text", "Update body.", top=640),
    ])
    assert _run(document, [T + "1", T + "4"])[1] == {}
    records = [_record(T + "1"), _record(T + "4")]
    records[0]["metadata"].update(
        section_path="INTRODUCTION", headings=["INTRODUCTION"])
    displays = ["INTRODUCTION", "B. SAMPLE UPDATE", POINTER]
    records[1]["metadata"].update(
        section_path=" → ".join(displays), headings=displays)

    audit = heading_lineage.audit_heading_bindings(document, records)

    # The display nests the section below INTRODUCTION, so the section is
    # not the bottom of the stack and the pointer stays a division.
    assert audit["source_scope_paths"] == [[T + "0"], [T + "3"]]
    assert audit["missing_refs"] == [T + "2"]
    assert audit["display_binding_records"] == [1]


def test_toc_scaffold_display_over_pointer_runs_still_fails_closed():
    # A TOC scaffold names the pointer as its chapter.  Joining pointers to
    # their sections must not make such displays bind.
    document = _document([
        _item(T + "0", "section_header", "G. SAMPLE UPDATE", top=780),
        _item(T + "1", "section_header", "CHAPTER 6 IN GENERAL", top=760,
              left=90),
        _item(T + "2", "text", "G body.", top=720),
        _item(T + "3", "section_header", "H. SECOND UPDATE", top=600),
        _item(T + "4", "section_header", "CHAPTER 6, SECTION F: SAMPLE",
              top=580, left=90),
        _item(T + "5", "text", "H body.", top=540),
    ])
    records = [_record(T + "2"), _record(T + "5")]
    for record in records:
        record["metadata"].update(
            section_path="Chapter 6 In General",
            headings=["Chapter 6 In General"])

    heading_lineage.attach_heading_bindings(document, records)
    audit = heading_lineage.audit_heading_bindings(document, records)

    assert audit["display_binding_records"] == [0, 1]
    assert audit["missing_refs"] == [T + "0", T + "3", T + "4"]


def test_contents_outline_rows_stay_eligible_so_dropping_them_fails_closed():
    # No exclusion reason covers a contents outline, so its rows are
    # eligible source items: dropping them fails representation and leaves
    # the contents title unowned, while published rows attach to it.
    table = _box("#/tables/0", "document_index", top=740, bottom=640)
    table["data"] = {"num_rows": 2, "num_cols": 1, "table_cells": [
        {"text": text, "start_row_offset_idx": row,
         "end_row_offset_idx": row + 1, "start_col_offset_idx": 0,
         "end_col_offset_idx": 1}
        for row, text in enumerate(["A. Sample Topic 1", "B. Next Topic 5"])
    ]}
    document = _ordered([
        _item(T + "0", "section_header", "TABLE OF CONTENTS", top=780),
        _item(T + "1", "section_header", "A. SAMPLE TOPIC", top=600),
        _item(T + "2", "text", "Section body.", top=560),
    ], [T + "0", "#/tables/0", T + "1", T + "2"], tables=[table])

    _, eligible, exclusions, _ = quality_core.source_inventory(
        document, structural_ranges=())
    dropped = _audit(document, [_record(T + "2")])
    kept = _audit(document, [
        _source_record("#/tables/0", "document_index"), _record(T + "2")])

    assert "#/tables/0" in eligible
    assert not any("contents" in reason for reason in exclusions)
    assert dropped == (["A. SAMPLE TOPIC"], {
        "missing_refs": [T + "0"], "unattached_refs": [T + "0"],
        "missing_direct_refs": [T + "0"]})
    assert kept == (["TABLE OF CONTENTS", "A. SAMPLE TOPIC"], {})
