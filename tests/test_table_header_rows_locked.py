"""Drift guard: stacked table headers through the locked docling-core export.

docling-core 2.99, the locked floor, joins the leading run of column-header
rows into the single header row that GitHub Markdown allows.
``quality_core._source_table_dimensions`` expects the published data rows
through ``table_retrieval_core.source_table_header_row_count``, a
standard-library port of that rule.  These tests replay the synthetic
``TableData`` grids of ``test_table_header_rows.py`` through the real
serializer and the pipeline's table normalization.  A serializer change that
moves rows into or out of the Markdown header then fails here instead of in
a private book's quality gate.  They run only where docling-core is
installed.  All text is synthetic.
"""

import pytest

import quality_core
import rag
import table_retrieval_core as tables
from test_table_header_rows import SOURCE_HEADER_ROW_SHAPES, _source_cell


# A flagged full-width title over blank flagged sub-header rows flattens to
# the title in every column, so rag also promotes it to a table preamble.
# The oracle does not model that second step and stays fail-closed.
_FAIL_CLOSED_RESIDUAL = "flagged-title-over-empty-sub-header-row"
_TITLE = [_source_cell(0, 0, "Title", cols=2)]
_BODY = [_source_cell(row, col, f"{row}.{col}")
         for row in (2, 3) for col in (0, 1)]
_PUBLICATION_SHAPES = [
    pytest.param(*shape[1:4], id=shape[0])
    for shape in SOURCE_HEADER_ROW_SHAPES
    if shape[0] != _FAIL_CLOSED_RESIDUAL
] + [
    pytest.param(1, 3, [
        _source_cell(0, col, text)
        for col, text in enumerate(("Interview", "Research", "Filing"))],
        id="headerless-single-row"),
    pytest.param(4, 2, _TITLE + [
        _source_cell(1, 0, "Low"), _source_cell(1, 1, "High")] + _BODY,
        id="full-width-title"),
    pytest.param(4, 2, [
        _source_cell(0, 0, "Title", header=True, cols=2),
        _source_cell(1, 0, "Low", header=True),
        _source_cell(1, 1, "High", header=True)] + _BODY,
        id="flagged-title-over-sub-headers"),
    pytest.param(4, 2, _TITLE + [
        _source_cell(1, 0, "Low", header=True),
        _source_cell(1, 1, "High", header=True)] + _BODY,
        id="title-over-flagged-row"),
]


def _table(num_rows, num_cols, cells):
    doc = pytest.importorskip("docling_core.types.doc")
    document = doc.DoclingDocument(name="synthetic")
    table = document.add_table(data=doc.TableData(
        num_rows=num_rows, num_cols=num_cols,
        table_cells=[doc.TableCell(**cell) for cell in cells]))
    return document, table


def _exported_data_rows(markdown):
    lines = [line for line in markdown.strip().splitlines() if line.strip()]
    separators = [index for index, line in enumerate(lines)
                  if tables._is_separator_row(line)]
    assert len(separators) == 1
    return len(lines) - separators[0] - 1


def _oracle_and_publication(num_rows, num_cols, cells):
    document, table = _table(num_rows, num_cols, cells)
    expected = quality_core._source_table_dimensions(
        document.export_to_dict())[table.self_ref]
    published = tables.markdown_table_dimensions(
        rag._normalized_source_table_markdown(document, table))
    return expected, published


@pytest.mark.parametrize(
    ("num_rows", "num_cols", "cells", "header_rows"),
    [shape[1:] for shape in SOURCE_HEADER_ROW_SHAPES],
    ids=[shape[0] for shape in SOURCE_HEADER_ROW_SHAPES],
)
def test_header_row_port_matches_the_locked_markdown_export(
        num_rows, num_cols, cells, header_rows):
    document, table = _table(num_rows, num_cols, cells)
    exported = document.export_to_dict()["tables"][0]["data"]

    assert tables.source_table_header_row_count(
        exported["num_rows"], exported["num_cols"],
        exported["table_cells"]) == header_rows
    assert _exported_data_rows(
        table.export_to_markdown(doc=document)) == num_rows - header_rows


@pytest.mark.parametrize(
    ("num_rows", "num_cols", "cells"), _PUBLICATION_SHAPES)
def test_native_row_expectation_matches_the_published_table(
        num_rows, num_cols, cells):
    expected, published = _oracle_and_publication(num_rows, num_cols, cells)

    # A header-only export has no data row and so is not a strict table.
    assert published == (expected if expected[0] else None)


@pytest.mark.parametrize("sub_headers", [
    (("", ""),), ((" ", "  "),), (("-", "."),), (("Title", "Title"),),
    (("", ""), ("Title", " ")),
], ids=("blank", "whitespace", "punctuation-only", "repeated-title",
        "three-header-rows"))
def test_bare_title_sub_headers_under_a_flagged_title_stay_fail_closed(
        sub_headers):
    cells = [_source_cell(0, 0, "Title", header=True, cols=2)]
    for row, texts in enumerate(sub_headers, start=1):
        cells += [_source_cell(row, col, text, header=True)
                  for col, text in enumerate(texts)]
    header_rows = 1 + len(sub_headers)
    cells += [_source_cell(header_rows + row, col, f"{row}.{col}")
              for row in range(3) for col in (0, 1)]

    expected, published = _oracle_and_publication(header_rows + 3, 2, cells)

    assert expected == (3, 2)
    assert published == (2, 2)


def test_distinct_sub_headers_under_a_flagged_title_are_not_promoted():
    cells = [_source_cell(0, 0, "Title", header=True, cols=2)]
    cells += [_source_cell(1, col, text, header=True)
              for col, text in enumerate(("Title:", "Title."))]
    cells += [_source_cell(2 + row, col, f"{row}.{col}")
              for row in range(3) for col in (0, 1)]

    assert _oracle_and_publication(5, 2, cells) == ((3, 2), (3, 2))
