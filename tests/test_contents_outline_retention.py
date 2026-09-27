"""Contents-outline rows under a source-proven contents title.

A supplement prints its "TABLE OF CONTENTS" as Docling ``document_index``
tables whose provenance charspans are empty.  Today every such row either is
dropped as structural (the eligible row is unrepresented and the title owns
nothing) or, if retained as plain text, cannot map one token to its page.
Both fail a gate, so binding the rows' exact cell text as an opaque table
oracle and publishing that text changes only failing output.  Every fixture
is synthetic; no private textbook text is used.
"""

from types import SimpleNamespace

import pytest

import heading_lineage
import quality_core
import rag
import source_fidelity_core
from test_quality_core import _build


SUPPLEMENT = "us-law-casebook-supplement-v1"
T = "#/texts/"
ROW_0, ROW_1 = "#/tables/0", "#/tables/1"
ROW_0_TEXT = "A. SAMPLE UPDATE TOPIC\nB. SECOND SAMPLE TOPIC"
ROW_1_TEXT = "C. THIRD SAMPLE TOPIC"
_ISSUE_FIELDS = (
    "missing_refs", "unattached_refs", "missing_direct_refs",
    "duplicate_direct_refs", "invalid_binding_records",
    "scope_conflict_records", "display_binding_records",
)
_INPUT_BINDINGS = {
    "docling_json": {"name": "book.json", "size": 50, "sha256": "a" * 64},
    "conversion_manifest": None,
    "table_recovery": None,
}


def _item(ref, label, text, *, page, top, bottom=None, layer="body",
          charspan=None):
    return {
        "self_ref": ref, "label": label, "content_layer": layer,
        "text": text, "orig": text, "parent": {"cref": "#/body"},
        "children": [],
        "prov": [{
            "page_no": page,
            "bbox": {"l": 70, "t": top, "r": 430,
                     "b": top - 15 if bottom is None else bottom,
                     "coord_origin": "BOTTOMLEFT"},
            "charspan": list(charspan or (0, len(text))),
        }],
    }


def _outline(ref, rows, *, page, top, bottom, charspan=(0, 0),
             layer="body"):
    item = _item(ref, "document_index", "", page=page, top=top,
                 bottom=bottom, layer=layer, charspan=charspan)
    del item["text"], item["orig"]
    item["data"] = {"num_rows": len(rows), "num_cols": 2, "table_cells": [
        {"text": text, "start_row_offset_idx": row,
         "end_row_offset_idx": row + 1, "start_col_offset_idx": column,
         "end_col_offset_idx": column + 1}
        for row, cells in enumerate(rows)
        for column, text in enumerate(cells)
    ]}
    return item


def _contents_document(*, title="TABLE OF CONTENTS", between=None,
                       page_label="1", row_charspan=(0, 0),
                       row_layer="body", title_layer="body",
                       title_label="section_header"):
    """Title, row p1, page label, row p2, then the first lettered section."""
    texts = [
        _item(T + "0", title_label, title, page=1, top=760,
              layer=title_layer),
        _item(T + "1", "page_footer", page_label, page=1, top=40,
              layer="furniture"),
        _item(T + "2", "section_header", "A. SAMPLE UPDATE TOPIC", page=2,
              top=680),
        _item(T + "3", "text", "Synthetic update body for the first topic.",
              page=2, top=640),
    ]
    tables = [
        _outline(ROW_0, [("A.", "SAMPLE UPDATE TOPIC"),
                         ("B.", "SECOND SAMPLE TOPIC")],
                 page=1, top=740, bottom=600, charspan=row_charspan,
                 layer=row_layer),
        _outline(ROW_1, [("C.", "THIRD SAMPLE TOPIC")], page=2, top=780,
                 bottom=740, layer=row_layer),
    ]
    order = [T + "0", ROW_0, T + "1", ROW_1, T + "2", T + "3"]
    if between is not None:
        label, text, before = between
        texts.append(_item(T + "4", label, text, page=1, top=750))
        order.insert(order.index(before), T + "4")
    return {
        "texts": texts, "tables": tables, "pictures": [], "groups": [],
        "key_value_items": [], "form_items": [],
        "body": {"self_ref": "#/body",
                 "children": [{"cref": ref} for ref in order]},
        "pages": {str(page): {"page_no": page,
                              "size": {"width": 500, "height": 800}}
                  for page in (1, 2)},
    }


def _namespace(value):
    if isinstance(value, dict):
        return SimpleNamespace(**{
            key: _namespace(child) for key, child in value.items()})
    if isinstance(value, list):
        return [_namespace(child) for child in value]
    return value


def _docling(document):
    """A Docling-shaped object view of one serialized document."""
    view = _namespace({key: value for key, value in document.items()
                       if key != "pages"})
    view.pages = {int(key): _namespace(page)
                  for key, page in document["pages"].items()}
    return view


@pytest.fixture
def pdf_path(tmp_path):
    pymupdf = pytest.importorskip("pymupdf")
    path = tmp_path / "update.pdf"
    pdf = pymupdf.open()
    for _ in range(2):
        pdf.new_page(width=500, height=800)
    pdf.save(path)
    pdf.close()
    return path


_CHUNKS = (
    ("A., 1 = SAMPLE UPDATE TOPIC. B., 1 = SECOND SAMPLE TOPIC",
     ["TABLE OF CONTENTS"], [ROW_0]),
    ("C., 1 = THIRD SAMPLE TOPIC", ["TABLE OF CONTENTS"], [ROW_1]),
    ("Synthetic update body for the first topic.",
     ["A. SAMPLE UPDATE TOPIC"], [T + "3"]),
)


def _publish(document, pdf_path, *, ranges=None, chunks=_CHUNKS,
             retain_all=False):
    """Mirror source enrichment and per-chunk enrichment for ``chunks``.

    ``ranges=None`` is a caller that does not know the structural ranges.
    ``retain_all`` models a classifier that keeps every row as prose today.
    """
    view = _docling(document)
    kwargs = {} if ranges is None else {"structural_ranges": set(ranges)}
    overrides = rag._recover_incomplete_table_markdown(
        view, pdf_path, **kwargs)
    oracles = rag._source_fidelity_oracles(
        view, rag.BoundSourceEnrichments(table_markdown_overrides=overrides))
    item_by_ref, parents, captions = rag._docling_lineage_catalog(view)
    records, dropped = [], []
    for index, (text, headings, refs) in enumerate(chunks):
        doc_items = [item_by_ref[ref] for ref in refs]
        record = rag.enrich_chunk(
            chunk_text=text, headings=headings, chunk_index=index,
            total_chunks=len(chunks), total_pages=2, doc_items=doc_items,
            source_file="update", structure_profile=SUPPLEMENT,
            source_items=rag._source_lineage_for_items(
                doc_items, item_by_ref=item_by_ref,
                parent_refs_by_child=parents,
                caption_refs_by_parent=captions, fidelity_oracles=oracles))
        rag._apply_source_content_lock(record, "body")
        if retain_all:
            record["metadata"]["content_type"] = "author_narrative"
        if (record["metadata"]["content_type"] == "structural"
                and not rag._retain_source_bound_structural_text(
                    record, doc_items)):
            dropped.append(refs)
            continue
        record["metadata"].update(token_count=4, embedding_token_count=6)
        records.append(record)
    return overrides, oracles, records, dropped


def _lineage_issues(document, records):
    rag._assign_source_heading_paths(
        document, records, structural_ranges=set(),
        excluded_heading_refs=set())
    heading_lineage.attach_heading_bindings(document, records)
    audit = heading_lineage.audit_heading_bindings(document, records)
    return {field: audit[field] for field in _ISSUE_FIELDS if audit[field]}


def _failed_checks(document, records, oracles):
    registry = source_fidelity_core.build_source_oracle_registry(
        oracles={ref: dict(oracle) for ref, oracle in oracles.items()
                 if oracle["transform"]
                 in source_fidelity_core.OPAQUE_TRANSFORMS},
        input_bindings=quality_core._source_oracle_upstream_inputs(
            _INPUT_BINDINGS))
    report = _build(records, document, source_oracle_registry=registry)
    return sorted(check["name"] for check in report["checks"]
                  if check["status"] == "fail")


def _bound_record(ref, oracle_text, *, extra=(), content_type="structural"):
    """A record whose lineage binds ``ref`` to a table-transform oracle."""
    return {"text": "A., 1 = SAMPLE", "metadata": {
        "content_type": content_type, "content_source": "body",
        "headings": ["TABLE OF CONTENTS"],
        "source_items": [
            {"ref": ref, "label": "document_index", "transform": "table",
             "oracle_text_sha256": source_fidelity_core.text_sha256(
                 oracle_text)},
            *({"ref": other, "label": label, "transform": "plain"}
              for other, label in extra),
        ],
    }}


# -- Characterization: behavior that must not change ------------------------


def test_summary_of_contents_recovery_is_unchanged(tmp_path):
    pymupdf = pytest.importorskip("pymupdf")
    authored = [
        (0, "§10.01 Input Acquisition"), (1, "A. Sensor Inventory"),
        (2, "1. Fixed Sensors"), (2, "2. Portable Sensors"),
        (1, "B. Sampling Plans"), (0, "§10.02 Record Validation"),
        (1, "A. Required Fields"), (2, "1. Device Identifier"),
        (1, "B. Consistency Checks"), (0, "§10.03 Export Packaging"),
    ]
    path = tmp_path / "summary.pdf"
    pdf = pymupdf.open()
    page = pdf.new_page(width=504, height=738)
    for index, (depth, text) in enumerate(authored):
        page.insert_text(({0: 72, 1: 98, 2: 114}[depth], 222 + index * 13),
                         text, fontsize=8)
    pdf.save(path)
    pdf.close()
    chapter = _item(T + "0", "section_header", "Chapter 10 Telemetry",
                    page=1, top=657)
    summary = _item(T + "1", "section_header", "Summary of Contents",
                    page=1, top=543)
    table = _item(ROW_0, "document_index", "", page=1, top=523,
                  bottom=300, charspan=(0, 0))
    del table["text"], table["orig"]
    table["data"] = {"num_rows": 10, "num_cols": 1, "table_cells": [
        {"text": text, "start_row_offset_idx": row,
         "start_col_offset_idx": 0}
        for row, text in enumerate(("§10.01 Input Acquisition",
                                    "§10.02 Record Validation"))]}
    table["prov"][0]["bbox"]["r"] = 357
    view = _namespace({"texts": [chapter, summary], "tables": [table],
                       "groups": [], "body": {"children": []}})
    expected = {ROW_0: "\n".join(
        "  " * depth + f"- {text}" for depth, text in authored)}

    assert rag._recover_incomplete_table_markdown(view, path) == expected


@pytest.mark.parametrize("others", [(), ((T + "3", "text"),)])
def test_summary_of_contents_retention_keeps_its_recovered_text(others):
    summary_text = "- §10.01 Inputs\n  - A. Sensors"
    record = _bound_record(ROW_0, summary_text, extra=others)
    record["text"] = summary_text
    record["metadata"].update(chapter_num=10,
                              headings=["Summary of Contents"])
    view = _docling(_contents_document())
    items = {item.self_ref: item for item in [*view.texts, *view.tables]}

    assert rag._retain_source_bound_structural_text(
        record, [items[ROW_0], *(items[ref] for ref, _ in others)]) is True
    assert record["text"] == summary_text
    assert record["metadata"]["content_type"] == "author_narrative"


def test_without_structural_ranges_contents_rows_are_still_dropped(pdf_path):
    # Today's outcome, and every caller that cannot prove the ranges: the
    # rows are structural and dropped, so the eligible rows are
    # unrepresented and the contents title owns no record.
    document = _contents_document()

    overrides, oracles, records, dropped = _publish(document, pdf_path)

    assert overrides == {}
    assert {oracles[ref]["transform"] for ref in (ROW_0, ROW_1)} == {"plain"}
    assert dropped == [[ROW_0], [ROW_1]]
    assert _lineage_issues(document, records) == {
        field: [T + "0"] for field in (
            "missing_refs", "unattached_refs", "missing_direct_refs")}
    failed = _failed_checks(document, records, oracles)
    assert {"eligible_source_items_represented",
            "section_heading_attachment"} <= set(failed)


@pytest.mark.parametrize(("ranges", "failure"), [
    (None, "source_geometry_complete"),
    ((), "source_token_fidelity"),
], ids=["today", "bound-oracle"])
def test_contents_rows_kept_as_prose_fail_a_source_gate(
        pdf_path, ranges, failure):
    # The alternative today: a classifier that kept the rows as prose.  The
    # empty charspans cannot map one row token to a page.  With the bound
    # table oracle the undropped serialization still fails, never silently.
    document = _contents_document()

    _, oracles, records, dropped = _publish(
        document, pdf_path, ranges=ranges, retain_all=True)

    assert dropped == []
    assert _lineage_issues(document, records) == {}
    failed = _failed_checks(document, records, oracles)
    assert failure in failed
    assert "eligible_source_items_represented" not in failed


def test_unbound_document_index_sharing_a_chunk_with_body_is_retained():
    view = _docling(_contents_document())
    record = {"text": "A., 1 = SAMPLE. Body.", "metadata": {
        "content_type": "structural", "content_source": "body",
        "source_items": [
            {"ref": ROW_0, "label": "document_index", "transform": "plain"},
            {"ref": T + "3", "label": "text", "transform": "plain"}]}}

    assert rag._retain_source_bound_structural_text(
        record, [view.tables[0], view.texts[3]]) is True
    assert record["text"] == "A., 1 = SAMPLE. Body."


# -- The fix: exact contents rows under their title --------------------------


def test_contents_rows_publish_exact_cell_text_under_their_title(pdf_path):
    document = _contents_document()

    overrides, oracles, records, dropped = _publish(
        document, pdf_path, ranges=())

    assert overrides == {ROW_0: ROW_0_TEXT, ROW_1: ROW_1_TEXT}
    for ref, text in overrides.items():
        assert oracles[ref]["transform"] == "table"
        assert oracles[ref]["oracle_text_sha256"] == (
            source_fidelity_core.text_sha256(text))
        assert oracles[ref]["oracle_lexical_tokens"] == list(
            source_fidelity_core.lexical_tokens(
                source_fidelity_core.source_item_text(
                    next(table for table in document["tables"]
                         if table["self_ref"] == ref))))
    assert dropped == []
    assert [record["text"] for record in records[:2]] == [
        ROW_0_TEXT, ROW_1_TEXT]
    assert {record["metadata"]["content_type"]
            for record in records} == {"author_narrative"}
    assert _lineage_issues(document, records) == {}
    assert [record["metadata"]["section_path"] for record in records] == [
        "TABLE OF CONTENTS", "TABLE OF CONTENTS", "A. SAMPLE UPDATE TOPIC"]
    assert _failed_checks(document, records, oracles) == []


@pytest.mark.parametrize("title", ["Contents", "CONTENTS",
                                   "Table of Contents"])
def test_contents_title_forms(pdf_path, title):
    overrides, _, _, _ = _publish(
        _contents_document(title=title), pdf_path, ranges=())

    assert set(overrides) == {ROW_0, ROW_1}


@pytest.mark.parametrize("label", ["ii", "Page 2", ""])
def test_bare_page_labels_do_not_break_the_outline(pdf_path, label):
    overrides, _, _, _ = _publish(
        _contents_document(page_label=label), pdf_path, ranges=())

    assert set(overrides) == {ROW_0, ROW_1}


@pytest.mark.parametrize(("case", "expected"), [
    ({"title": "Summary of Contents"}, set()),
    ({"title": "Contents of This Volume"}, set()),
    ({"title_label": "text"}, set()),
    ({"title_layer": "furniture"}, set()),
    ({"row_layer": "furniture"}, set()),
    ({"row_charspan": (0, 40)}, set()),
    ({"page_label": "Copyright 2026 Sample Press"}, {ROW_0}),
    ({"between": ("text", "Preface.", ROW_0)}, set()),
    ({"between": ("text", "Interleaved note.", ROW_1)}, {ROW_0}),
], ids=["summary-title", "other-title", "title-not-heading",
        "title-furniture", "row-furniture", "mappable-row-charspan",
        "substantive-footer", "text-before-rows", "text-between-rows"])
def test_contents_rows_need_every_guard(pdf_path, case, expected):
    # Each unproven layout keeps today's (failing) output for its rows.
    overrides, _, _, _ = _publish(
        _contents_document(**case), pdf_path, ranges=())

    assert set(overrides) == expected


@pytest.mark.parametrize(("ranges", "expected"), [
    ([(1, 1)], set()),
    ([(2, 2)], {ROW_0}),
    ([(3, 9)], {ROW_0, ROW_1}),
])
def test_contents_rows_inside_structural_ranges_are_untouched(
        pdf_path, ranges, expected):
    overrides, _, _, _ = _publish(
        _contents_document(), pdf_path, ranges=ranges)

    assert set(overrides) == expected


def test_serialized_order_mirrors_heading_lineage():
    document = _contents_document(between=("text", "Preface.", ROW_1))
    document["groups"] = [{"self_ref": "#/groups/0", "children": [
        {"cref": T + "2"}, {"cref": T + "3"}]}]
    children = document["body"]["children"]
    children[children.index({"cref": T + "2"}):] = [{"cref": "#/groups/0"}]
    document["texts"][0]["children"] = [{"cref": ROW_0}]
    items, groups = heading_lineage._catalog(document)

    assert rag._docling_serialized_refs(_docling(document)) == (
        heading_lineage._ordered_source_refs(document, items, groups, ()))


def test_punctuation_only_row_is_not_bound(pdf_path):
    document = _contents_document()
    for cell in document["tables"][1]["data"]["table_cells"]:
        cell["text"] = "...."

    overrides, _, _, _ = _publish(document, pdf_path, ranges=())

    assert set(overrides) == {ROW_0}


def test_cell_whitespace_collapses_without_changing_tokens(pdf_path):
    document = _contents_document()
    document["tables"][0]["data"]["table_cells"][1]["text"] = (
        "  SAMPLE\n  UPDATE   TOPIC ")

    overrides, _, _, _ = _publish(document, pdf_path, ranges=())

    assert overrides[ROW_0] == ROW_0_TEXT


def test_bound_contents_row_is_published_only_on_the_dropped_path():
    row = _docling(_contents_document()).tables[0]
    kept = _bound_record(ROW_0, ROW_0_TEXT)
    prose = _bound_record(ROW_0, ROW_0_TEXT, content_type="author_narrative")
    unbound = _bound_record(ROW_0, "A. SAMPLE UPDATE TOPIC")
    detached = _bound_record(ROW_0, ROW_0_TEXT)

    assert rag._retain_source_bound_structural_text(kept, [row]) is True
    assert kept["text"] == ROW_0_TEXT
    assert kept["metadata"]["content_type"] == "author_narrative"
    assert rag._retain_source_bound_structural_text(prose, [row]) is False
    assert prose["text"] == "A., 1 = SAMPLE"
    assert rag._retain_source_bound_structural_text(unbound, [row]) is False
    assert unbound["metadata"]["content_type"] == "structural"
    # Boundary repair has no source items to re-derive the exact text from.
    assert rag._retain_source_bound_structural_text(detached, None) is False
    assert detached["text"] == "A., 1 = SAMPLE"


@pytest.mark.parametrize("others", [
    ((T + "3", "text"),), ((T + "2", "section_header"),)])
def test_bound_contents_row_sharing_a_chunk_fails_closed(others):
    view = _docling(_contents_document())
    items = {item.self_ref: item for item in [*view.texts, *view.tables]}
    record = _bound_record(ROW_0, ROW_0_TEXT, extra=others)

    with pytest.raises(RuntimeError, match="contents outline row"):
        rag._retain_source_bound_structural_text(
            record, [items[ROW_0], *(items[ref] for ref, _ in others)])


def test_two_bound_contents_rows_in_one_chunk_fail_closed():
    view = _docling(_contents_document())
    record = _bound_record(ROW_0, ROW_0_TEXT)
    record["metadata"]["source_items"].append({
        "ref": ROW_1, "label": "document_index", "transform": "table",
        "oracle_text_sha256": source_fidelity_core.text_sha256(ROW_1_TEXT)})

    with pytest.raises(RuntimeError, match="contents outline row"):
        rag._retain_source_bound_structural_text(record, view.tables)
