"""Drift guard: stacked table headers through the locked docling-core export.

docling-core 2.99, the locked floor, joins the leading run of column-header
rows into the single header row that GitHub Markdown allows.
``quality_core._source_table_dimensions`` expects the published data rows
through ``table_retrieval_core.source_table_header_row_count``, a
standard-library port of that rule.  These tests replay synthetic
``TableData`` grids through the real serializer and the pipeline's table
normalization.  A serializer change that moves rows into or out of the
Markdown header then fails here instead of in a private book's quality gate.
They run only where docling-core is installed.  All text is synthetic.
"""

import pytest

import quality_core
import rag
import table_retrieval_core as tables
from test_quality_core import _stacked_header_worksheet
from test_table_retrieval_core import SOURCE_HEADER_ROW_SHAPES, _source_cell


# A flagged full-width title over a blank flagged sub-header row flattens to
# the title in every column, so rag also promotes it to a table preamble.
# The oracle does not model that second step and stays fail-closed.
_FAIL_CLOSED_RESIDUAL = "flagged-title-over-empty-sub-header-row"
_SHAPES = {shape[0]: shape[1:] for shape in SOURCE_HEADER_ROW_SHAPES}


def _grid(label, num_rows, num_cols, cells):
    return pytest.param(num_rows, num_cols, cells, id=label)


def _worksheet(data_rows):
    data = _stacked_header_worksheet(data_rows)["data"]
    return data["num_rows"], data["num_cols"], data["table_cells"]


_TITLE = [_source_cell(0, 0, "Title", cols=2)]
_BODY = [_source_cell(row, col, f"{row}.{col}")
         for row in (2, 3) for col in (0, 1)]
_PUBLICATION_SHAPES = [
    _grid(label, *shape[:3]) for label, shape in _SHAPES.items()
    if label != _FAIL_CLOSED_RESIDUAL
] + [
    _grid("worksheet-8-columns", *_worksheet(2)),
    _grid("worksheet-page-break-continuation", *_worksheet(1)),
    _grid("headerless-single-row", 1, 3, [
        _source_cell(0, col, text)
        for col, text in enumerate(("Interview", "Research", "Filing"))]),
    _grid("full-width-title", 4, 2, _TITLE + [
        _source_cell(1, 0, "Low"), _source_cell(1, 1, "High")] + _BODY),
    _grid("flagged-title-over-sub-headers", 4, 2, [
        _source_cell(0, 0, "Title", header=True, cols=2),
        _source_cell(1, 0, "Low", header=True),
        _source_cell(1, 1, "High", header=True)] + _BODY),
    _grid("title-over-flagged-row", 4, 2, _TITLE + [
        _source_cell(1, 0, "Low", header=True),
        _source_cell(1, 1, "High", header=True)] + _BODY),
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


def test_blank_sub_headers_under_a_flagged_title_stay_fail_closed():
    expected, published = _oracle_and_publication(
        *_SHAPES[_FAIL_CLOSED_RESIDUAL][:3])

    assert expected == (2, 3)
    assert published == (1, 3)
