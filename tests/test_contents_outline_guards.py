"""Guards that keep contents-outline binding on documents that fail today.

Companion to ``test_contents_outline_retention``.  A contents row that another
Docling item claims can be a visual container alias today, and such a
document can pass every gate, so the row keeps today's output.  The remaining
tests pin the range scope, the serialized order, the lineage proof of a
binding, the fail-closed chunk shapes, and the production wiring.  Every
fixture is synthetic.
"""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

import heading_lineage
import rag
import source_fidelity_core
from test_contents_outline_retention import (
    _CHUNKS, ROW_0, ROW_0_TEXT, ROW_1, SUPPLEMENT, T, _bound_record,
    _contents_document, _docling, _failed_checks, _item, _lineage_issues,
    _outline, _publish,
)


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


def _aliased_row_document():
    """A contents row claimed by its title and by a visual table owner.

    The body reaches only the label-``table`` owner, whose cells repeat the
    row; the title lists the row as its child, so the row still directly
    follows the title in serialized order.
    """
    document = _contents_document()
    owner = _outline(ROW_1, [("A.", "SAMPLE UPDATE TOPIC"),
                             ("B.", "SECOND SAMPLE TOPIC")],
                     page=1, top=740, bottom=600)
    owner.update(label="table", children=[{"cref": ROW_0}])
    document["tables"][1] = owner
    document["texts"][0]["children"] = [{"cref": ROW_0}]
    document["body"]["children"] = [{"cref": ref} for ref in (
        T + "0", ROW_1, T + "1", T + "2", T + "3")]
    return document


_ALIAS_CHUNKS = (
    ("| A. | SAMPLE UPDATE TOPIC |\n|---|---|\n| B. | SECOND SAMPLE TOPIC |",
     ["TABLE OF CONTENTS"], [ROW_1, ROW_0]),
    _CHUNKS[2],
)


def _publish_prepared(document, pdf_path, chunks, *, ranges=()):
    """Mirror the chunk path, including prepared-chunk visual aliasing."""
    view = _docling(document)
    overrides = rag._recover_incomplete_table_markdown(
        view, pdf_path, structural_ranges=set(ranges))
    item_by_ref, parents, captions = rag._docling_lineage_catalog(view)
    prepared = [(text, headings, [item_by_ref[ref] for ref in refs], False)
                for text, headings, refs in chunks]
    oracles = rag._source_fidelity_oracles(
        view, rag.BoundSourceEnrichments(table_markdown_overrides=overrides),
        prepared_chunks=prepared)
    records = []
    for index, (text, headings, doc_items, _) in enumerate(prepared):
        record = rag.enrich_chunk(
            chunk_text=text, headings=headings, chunk_index=index,
            total_chunks=len(prepared), total_pages=2, doc_items=doc_items,
            source_file="update", structure_profile=SUPPLEMENT,
            source_items=rag._source_lineage_for_items(
                doc_items, item_by_ref=item_by_ref,
                parent_refs_by_child=parents,
                caption_refs_by_parent=captions, fidelity_oracles=oracles))
        tabular = any(rag._doc_item_label(item) == "table"
                      for item in doc_items)
        rag._apply_source_content_lock(record, "table" if tabular else "body")
        if (record["metadata"]["content_type"] == "structural"
                and not rag._retain_source_bound_structural_text(
                    record, doc_items)):
            continue
        record["metadata"].update(token_count=4, embedding_token_count=6)
        if tabular:
            record["metadata"].setdefault("table_rows", 1)
            record["metadata"].setdefault("table_cols", 2)
        records.append(record)
    return overrides, oracles, records


# -- Characterization: a visually aliased row passes today ------------------


def test_contents_row_aliased_by_a_visual_table_keeps_passing_output(
        pdf_path):
    # Today the row is its owner's container alias and every gate passes.
    # Rebinding it as a table oracle would lose the alias and fail the
    # document, so a row that another item claims is never bound.
    document = _aliased_row_document()

    overrides, oracles, records = _publish_prepared(
        document, pdf_path, _ALIAS_CHUNKS)

    assert overrides == {}
    assert oracles[ROW_0]["transform"] == "container_alias"
    assert oracles[ROW_0]["oracle_group_members"] == [ROW_1, ROW_0]
    assert len(records) == 2
    assert _lineage_issues(document, records) == {}
    assert _failed_checks(document, records, oracles) == []


# -- Scope guards ------------------------------------------------------------


def _relate(path, field, value):
    def apply(document):
        collection, index = path
        document[collection][index][field] = value
    return apply


@pytest.mark.parametrize(("relate", "expected"), [
    (_relate(("texts", 0), "children", [{"cref": ROW_0}]), set()),
    (_relate(("tables", 0), "parent", {"cref": T + "0"}), set()),
    (_relate(("texts", 3), "footnotes", [{"cref": ROW_1}]), {ROW_0}),
    (_relate(("tables", 1), "captions", [{"cref": T + "3"}]), {ROW_0}),
], ids=["child-of-title", "item-parent", "footnote-of-text",
        "owns-caption"])
def test_rows_with_docling_item_relationships_are_never_bound(
        pdf_path, relate, expected):
    # Today an isolated row can only be dropped or carry a plain oracle, and
    # both fail; a row related to another item may instead be a container
    # alias or carry a caption, so it keeps today's output.
    document = _contents_document()
    relate(document)

    overrides, _, _, _ = _publish(document, pdf_path, ranges=())

    assert set(overrides) == expected


def _move_row_0_to_page_2(document):
    document["tables"][0]["prov"][0]["page_no"] = 2


def _row_0_on_both_pages(document):
    span = dict(document["tables"][0]["prov"][0], page_no=2)
    document["tables"][0]["prov"].append(span)


@pytest.mark.parametrize(("change", "ranges", "expected"), [
    (_move_row_0_to_page_2, [(1, 1)], set()),
    (_move_row_0_to_page_2, [(3, 9)], {ROW_0, ROW_1}),
    (_row_0_on_both_pages, [(2, 2)], set()),
    (_row_0_on_both_pages, [(3, 9)], {ROW_0, ROW_1}),
], ids=["title-in-range", "title-outside", "row-page-in-range",
        "row-pages-outside"])
def test_title_and_every_row_page_must_lie_outside_the_ranges(
        pdf_path, change, ranges, expected):
    document = _contents_document()
    change(document)

    overrides, _, _, _ = _publish(document, pdf_path, ranges=ranges)

    assert set(overrides) == expected


def test_document_index_outside_the_tables_collection_ends_the_run(
        pdf_path):
    # Only a table-collection row can receive a table-markdown override.
    document = _contents_document(between=("document_index", "", ROW_0))
    document["texts"][-1]["data"] = {"table_cells": [{
        "text": "A. SAMPLE UPDATE TOPIC", "start_row_offset_idx": 0,
        "start_col_offset_idx": 0}]}

    overrides, _, _, _ = _publish(document, pdf_path, ranges=())

    assert overrides == {}


def test_serialized_order_expands_item_children_and_appends_orphans():
    document = _contents_document()
    document["texts"] += [
        _item(T + "4", "text", "Nested note.", page=1, top=700),
        _item(T + "5", "text", "Orphan note.", page=2, top=600),
    ]
    document["texts"][0]["children"] = [{"cref": ROW_0}]
    document["tables"][0]["children"] = [{"cref": T + "4"}]
    document["groups"] = [{"self_ref": "#/groups/0", "children": [
        {"cref": T + "2"}, {"cref": T + "3"}]}]
    document["body"]["children"] = [{"cref": ref} for ref in (
        T + "0", T + "1", ROW_1, "#/groups/0")]
    items, groups = heading_lineage._catalog(document)

    order = rag._docling_serialized_refs(_docling(document))

    assert order == [T + "0", ROW_0, T + "4", T + "1", ROW_1, T + "2",
                     T + "3", T + "5"]
    assert order == heading_lineage._ordered_source_refs(
        document, items, groups, ())


# -- Retention: the lineage proof and the fail-closed chunk shapes ----------


def test_one_column_plain_oracle_is_not_mistaken_for_a_binding():
    # A one-column outline's plain oracle already equals its contents text;
    # only the table transform written by the recovery proves the binding.
    document = _contents_document()
    document["tables"][0] = _outline(
        ROW_0, [("A. SAMPLE UPDATE TOPIC",), ("B. SECOND SAMPLE TOPIC",)],
        page=1, top=740, bottom=600)
    view = _docling(document)
    item_by_ref, parents, captions = rag._docling_lineage_catalog(view)
    oracles = rag._source_fidelity_oracles(
        view, rag.BoundSourceEnrichments())
    row = item_by_ref[ROW_0]
    record = {"text": "A. SAMPLE UPDATE TOPIC", "metadata": {
        "content_type": "structural", "content_source": "body",
        "headings": ["TABLE OF CONTENTS"],
        "source_items": rag._source_lineage_for_items(
            [row], item_by_ref=item_by_ref, parent_refs_by_child=parents,
            caption_refs_by_parent=captions, fidelity_oracles=oracles)}}

    assert oracles[ROW_0]["transform"] == "plain"
    assert oracles[ROW_0]["oracle_text_sha256"] == (
        source_fidelity_core.text_sha256(ROW_0_TEXT))
    assert rag._retain_source_bound_structural_text(record, [row]) is False
    assert record["text"] == "A. SAMPLE UPDATE TOPIC"


def test_a_table_with_a_plain_cell_oracle_is_not_a_contents_row():
    # A table whose Markdown export is unavailable falls back to its cells
    # joined by newlines; a one-column table then matches its row text, yet
    # only a document_index row can be a bound contents row.
    document = _contents_document()
    document["tables"][1] = _outline(
        ROW_1, [("C. THIRD SAMPLE TOPIC",)], page=2, top=780, bottom=740)
    document["tables"][1]["label"] = "table"
    view = _docling(document)
    item_by_ref, parents, captions = rag._docling_lineage_catalog(view)
    oracles = rag._source_fidelity_oracles(
        view, rag.BoundSourceEnrichments())
    items = [item_by_ref[ROW_1], item_by_ref[T + "3"]]
    record = {"text": "C. THIRD SAMPLE TOPIC Body.", "metadata": {
        "content_type": "structural", "content_source": "table",
        "source_items": rag._source_lineage_for_items(
            items, item_by_ref=item_by_ref, parent_refs_by_child=parents,
            caption_refs_by_parent=captions, fidelity_oracles=oracles)}}

    assert oracles[ROW_1]["transform"] == "table"
    assert oracles[ROW_1]["oracle_text_sha256"] == (
        source_fidelity_core.text_sha256("C. THIRD SAMPLE TOPIC"))
    assert rag._retain_source_bound_structural_text(record, items) is True
    assert record["text"] == "C. THIRD SAMPLE TOPIC Body."


def test_bound_row_with_lineage_only_companion_fails_closed():
    # The chunk's items are the row alone, but its lineage also names another
    # source item (for example an attached caption); that shape fails closed.
    row = _docling(_contents_document()).tables[0]
    record = _bound_record(ROW_0, ROW_0_TEXT, extra=((T + "3", "text"),))

    with pytest.raises(RuntimeError, match="contents outline row"):
        rag._retain_source_bound_structural_text(record, [row])


def test_contents_row_split_across_chunks_cannot_pass(pdf_path):
    # Retention sees one chunk at a time, so each piece of a row that the
    # chunker split publishes the whole bound row; the duplicate output
    # then fails source fidelity instead of publishing silently.
    document = _contents_document()
    split = (
        ("A., 1 = SAMPLE UPDATE TOPIC.", ["TABLE OF CONTENTS"], [ROW_0]),
        ("B., 1 = SECOND SAMPLE TOPIC", ["TABLE OF CONTENTS"], [ROW_0]),
        *_CHUNKS[1:],
    )

    _, oracles, records, dropped = _publish(
        document, pdf_path, ranges=(), chunks=split)

    assert dropped == []
    assert [record["text"] for record in records[:2]] == [
        ROW_0_TEXT, ROW_0_TEXT]
    assert "source_token_fidelity" in _failed_checks(
        document, records, oracles)


# -- Production wiring --------------------------------------------------------


def test_bound_source_enrichments_pass_the_structural_ranges(
        monkeypatch, pdf_path):
    calls = []

    @contextmanager
    def snapshot(*_args, **_kwargs):
        yield SimpleNamespace(
            pdf=SimpleNamespace(path=pdf_path), source_path=pdf_path,
            manifest_input=lambda: None)

    def recover(dl_doc, path, **kwargs):
        calls.append((path, kwargs))
        return {}

    monkeypatch.setattr(rag, "_open_docling_source_pdf_snapshot", snapshot)
    monkeypatch.setattr(rag, "_recover_incomplete_table_markdown", recover)

    enrichments = rag._recover_bound_source_enrichments(
        _docling(_contents_document()), pdf_path.with_suffix(".json"), None,
        structural_ranges={(3, 9)})

    assert calls == [(pdf_path, {"structural_ranges": {(3, 9)}})]
    assert enrichments.table_markdown_overrides == {}
