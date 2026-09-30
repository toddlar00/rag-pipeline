"""Docling Markdown header rows and the native table row-count oracle.

docling-core 2.99 joins a leading run of column-header rows into one Markdown
header row.  ``table_retrieval_core.source_table_header_row_count`` ports
that count, and ``quality_core._source_table_dimensions`` expects
``num_rows - H`` data rows whenever it is not one.  These tests are
dependency-light; ``test_table_header_rows_locked.py`` replays every shape
here through the real serializer.  All text is synthetic.
"""

import copy

import pytest

import quality_core
import table_retrieval_core as tables
from test_quality_core import _build, _record, _source_item


def _source_cell(row: int, col: int, text: str = "", *, header: bool = False,
                 rows: int = 1, cols: int = 1,
                 row_header: bool = False) -> dict:
    """One exported Docling ``TableCell`` dictionary."""
    return {
        "start_row_offset_idx": row, "end_row_offset_idx": row + rows,
        "start_col_offset_idx": col, "end_col_offset_idx": col + cols,
        "row_span": rows, "col_span": cols, "text": text,
        "column_header": header, "row_header": row_header,
    }


def _flagged_row(row: int, *texts: str) -> list[dict]:
    return [_source_cell(row, col, text, header=True)
            for col, text in enumerate(texts)]


def _data_row(row: int, *texts: str) -> list[dict]:
    return [_source_cell(row, col, text) for col, text in enumerate(texts)]


def _cell_at(row: int, col: int, text: str, *, header: bool = False,
             end_row: int, end_col: int) -> dict:
    """A cell with explicit end offsets (inverted or past the table)."""
    return {**_source_cell(row, col, text, header=header),
            "end_row_offset_idx": end_row, "end_col_offset_idx": end_col}


_WORKSHEET_LABELS = ("1.", "Base"), ("2.", "Stress")


def _worksheet_cells(data_rows: int) -> list[dict]:
    """A synthetic 8-column worksheet whose column headers span two rows."""
    cells = [
        _source_cell(0, 0, "Scenario", header=True, cols=2),
        _source_cell(0, 2, "Year 1", header=True, cols=2),
        _source_cell(0, 4, "Year 2", header=True, cols=2),
        _source_cell(0, 6, "Year 3", header=True, cols=2),
        _source_cell(1, 0, "Label", header=True, cols=2),
    ] + [_source_cell(1, col, ("Low", "High")[col % 2], header=True)
         for col in range(2, 8)]
    for offset, labels in enumerate(_WORKSHEET_LABELS[:data_rows]):
        row = 2 + offset
        cells += [_source_cell(row, col, text, row_header=True)
                  for col, text in enumerate(labels)]
        cells += [_source_cell(row, col, "$____") for col in range(2, 8)]
    return cells


# (id, num_rows, num_cols, table_cells, header rows docling-core 2.99 joins).
SOURCE_HEADER_ROW_SHAPES = (
    ("no-flags", 3, 2, _data_row(0, "Rule", "Value") + _data_row(1, "A", "1")
     + _data_row(2, "B", "2"), 1),
    ("no-cells", 2, 2, [], 1),
    ("one-header-row", 3, 2, _flagged_row(0, "Rule", "Value")
     + _data_row(1, "A", "1") + _data_row(2, "B", "2"), 1),
    ("two-stacked-rows-with-column-spans", 4, 8, _worksheet_cells(2), 2),
    ("stacked-rows-continued-on-next-page", 3, 8, _worksheet_cells(1), 2),
    ("three-stacked-rows", 5, 2,
     _flagged_row(0, "Group", "Group") + _flagged_row(1, "Plan", "Plan")
     + _flagged_row(2, "Low", "High") + _data_row(3, "1", "2")
     + _data_row(4, "3", "4"), 3),
    ("all-rows-flagged", 2, 2,
     _flagged_row(0, "Low", "High") + _flagged_row(1, "Min", "Max"), 2),
    ("flags-start-below-row-0", 3, 2, _data_row(0, "Note", "Detail")
     + _flagged_row(1, "Low", "High") + _data_row(2, "1", "2"), 0),
    ("row-0-header-spans-into-body-row", 3, 2,
     [_source_cell(0, 0, "Group", header=True, rows=2),
      _source_cell(0, 1, "Total"), _source_cell(1, 1, "7")]
     + _data_row(2, "A", "8"), 0),
    ("row-0-flag-beside-body-text", 3, 2,
     [_source_cell(0, 0, "Rule", header=True), _source_cell(0, 1, "Value")]
     + _data_row(1, "A", "1") + _data_row(2, "B", "2"), 1),
    ("rows-under-a-vertical-header-span", 3, 2,
     [_source_cell(0, 0, "Label", header=True, rows=3),
      _source_cell(0, 1, "Value", header=True),
      _source_cell(1, 1, ""), _source_cell(2, 1, "")], 1),
    ("leading-column-label-in-header-row", 3, 3,
     [_source_cell(0, 0, "Plan", header=True),
      _source_cell(0, 1, "Years", header=True, cols=2),
      _source_cell(1, 0, "Label"), _source_cell(1, 1, "Low", header=True),
      _source_cell(1, 2, "High", header=True)]
     + _data_row(2, "1.", "5", "9"), 2),
    ("leading-column-is-first-populated-column", 3, 3,
     [_source_cell(0, 1, "Years", header=True, cols=2),
      _source_cell(1, 1, "Label"), _source_cell(1, 2, "Low", header=True),
      _source_cell(2, 1, "1."), _source_cell(2, 2, "5")], 2),
    ("unflagged-label-column-left-of-all-flags", 3, 2,
     [_source_cell(0, 0, "Plan"), _source_cell(0, 1, "Year", header=True),
      _source_cell(1, 0, "Label"), _source_cell(1, 1, "Low", header=True)]
     + _data_row(2, "1", "2"), 2),
    ("whitespace-only-unflagged-cell", 3, 2, _flagged_row(0, "Low", "High")
     + [_source_cell(1, 0, "Sub", header=True), _source_cell(1, 1, "  ")]
     + _data_row(2, "1", "2"), 2),
    ("flagged-title-over-empty-sub-header-row", 4, 3,
     [_source_cell(0, 0, "Title", header=True, cols=3)]
     + _flagged_row(1, "", "", "") + _data_row(2, "1", "2", "3")
     + _data_row(3, "4", "5", "6"), 2),
    ("empty-row-ends-header-block", 4, 2, _flagged_row(0, "Low", "High")
     + _flagged_row(2, "Min", "Max") + _data_row(3, "1", "2"), 1),
    ("row-header-row-below-header", 3, 2, _flagged_row(0, "Low", "High")
     + [_source_cell(1, col, text, row_header=True)
        for col, text in enumerate(("Min", "Max"))]
     + _data_row(2, "1", "2"), 1),
    ("later-unflagged-cell-overwrites-header", 3, 2,
     _flagged_row(0, "Low", "High") + [_source_cell(0, 1, "Note")]
     + _flagged_row(1, "Min", "Max") + _data_row(2, "1", "2"), 0),
    ("later-header-overwrites-body-text", 3, 2,
     [_source_cell(0, 0, "Low", header=True), _source_cell(0, 1, "Note"),
      _source_cell(0, 1, "High", header=True)]
     + _flagged_row(1, "Min", "Max") + _data_row(2, "1", "2"), 2),
    ("header-spans-clipped-at-table-edge", 3, 2,
     [_source_cell(0, 0, "Title", header=True, cols=5),
      _source_cell(1, 0, "Label", header=True, rows=9),
      _source_cell(1, 1, "Low", header=True), _source_cell(2, 1, "2")], 2),
    ("flags-only-on-cells-outside-the-grid", 3, 2,
     _data_row(0, "Note", "Detail") + _data_row(1, "1", "2")
     + _data_row(2, "3", "4")
     + [_cell_at(5, 0, "Past", header=True, end_row=6, end_col=2),
        _cell_at(2, 0, "Inverted", header=True, end_row=1, end_col=2)], 1),
)


@pytest.mark.parametrize(
    ("row_count", "column_count", "cells", "expected"),
    [shape[1:] for shape in SOURCE_HEADER_ROW_SHAPES],
    ids=[shape[0] for shape in SOURCE_HEADER_ROW_SHAPES],
)
def test_header_row_count_ports_docling_header_rows(
        row_count, column_count, cells, expected):
    snapshot = copy.deepcopy(cells)

    assert tables.source_table_header_row_count(
        row_count, column_count, cells) == expected
    assert tables.source_table_header_row_count(
        row_count, column_count, tuple(cells)) == expected
    assert cells == snapshot


def _patched(**fields) -> list[dict]:
    return [{**_source_cell(0, 0, "A", header=True), **fields},
            _source_cell(1, 0, "1")]


# Negative offsets index from the grid's end in docling-core, or raise.
NEGATIVE_OFFSET_CELLS = [
    pytest.param(_patched(**{field: value}), id=f"{field}={value}")
    for field, value in (
        ("start_row_offset_idx", -10), ("start_row_offset_idx", -1),
        ("end_row_offset_idx", -1), ("start_col_offset_idx", -10),
        ("end_col_offset_idx", -1))
]


@pytest.mark.parametrize(("row_count", "column_count", "cells"), [
    (True, 2, []), (2, None, []), (-1, 2, []), (2, 2, None),
    (2, 2, "cells"), (2, 2, ["cell"]),
    (2, 2, _patched(end_col_offset_idx=None)),
    (2, 2, _patched(start_row_offset_idx=False)),
    (2, 2, _patched(end_row_offset_idx=1.0)),
    (2, 2, _patched(end_row_offset_idx="1")),
    (2, 2, [{key: value for key, value in _source_cell(0, 0, "A").items()
             if key != "start_col_offset_idx"}]),
    (2, 2, _patched(text=None)),
    (2, 2, _patched(column_header="yes")),
    (2, 2, _patched(column_header=1)),
    *(pytest.param(2, 2, *param.values, id=param.id)
      for param in NEGATIVE_OFFSET_CELLS),
])
def test_header_row_count_rejects_values_outside_its_strict_types(
        row_count, column_count, cells):
    assert tables.source_table_header_row_count(
        row_count, column_count, cells) is None


def test_header_row_count_defaults_a_missing_flag_to_false():
    cells = _flagged_row(0, "Low", "High") + [
        {key: value for key, value in cell.items() if key != "column_header"}
        for cell in _flagged_row(1, "Min", "Max")]

    assert tables.source_table_header_row_count(3, 2, cells) == 1


def _dimensions(num_rows: int, num_cols: int, cells: list[dict]) -> tuple:
    document = {"tables": [{
        "self_ref": "#/tables/0",
        "data": {"num_rows": num_rows, "num_cols": num_cols,
                 "table_cells": copy.deepcopy(cells)},
    }]}
    return quality_core._source_table_dimensions(document)["#/tables/0"]


_TITLE = [_source_cell(0, 0, "Title", cols=2)]
_FLAGGED_TITLE = [_source_cell(0, 0, "Title", header=True, cols=2)]
_BODY = _data_row(2, "1", "2") + _data_row(3, "3", "4")


@pytest.mark.parametrize(("num_rows", "num_cols", "cells", "expected"), [
    # One Markdown header row (H == 1): the established rule, unchanged.
    (4, 2, _flagged_row(0, "Low", "High") + _data_row(1, "0", "0") + _BODY,
     (3, 2)),
    (4, 2, _data_row(0, "Low", "High") + _data_row(1, "0", "0") + _BODY,
     (3, 2)),
    (1, 3, _data_row(0, "Interview", "Research", "Investigation"), (1, 3)),
    (4, 2, _TITLE + _data_row(1, "Low", "High") + _BODY, (2, 2)),
    (4, 2, _FLAGGED_TITLE + _data_row(1, "Low", "High") + _BODY, (2, 2)),
    # Cells docling-core's TableCell would reject keep the established rule.
    (4, 2, [{"column_header": True}] * 2 + _flagged_row(1, "Low", "High"),
     (3, 2)),
    # Stacked headers (H >= 2): only the joined rows are excluded.
    (4, 8, _worksheet_cells(2), (2, 8)),
    (3, 8, _worksheet_cells(1), (1, 8)),
    (5, 2, _flagged_row(0, "Plan", "Plan") + _flagged_row(1, "Low", "High")
     + _flagged_row(2, "Min", "Max") + _data_row(3, "1", "2")
     + _data_row(4, "3", "4"), (2, 2)),
    # A flagged title over flagged sub-headers: old and new values coincide.
    (4, 2, _FLAGGED_TITLE + _flagged_row(1, "Low", "High") + _BODY, (2, 2)),
    # Flags starting below row 0 (H == 0): a blank header over every row.
    (4, 2, _data_row(0, "Note", "Detail") + _flagged_row(1, "Low", "High")
     + _BODY, (4, 2)),
    (4, 2, _TITLE + _flagged_row(1, "Low", "High") + _BODY, (4, 2)),
], ids=(
    "one-header-row", "no-flags", "headerless-single-row",
    "full-width-title", "flagged-full-width-title", "malformed-cells",
    "worksheet", "page-break-continuation", "three-stacked-rows",
    "flagged-title-over-sub-headers", "flags-start-below-row-0",
    "title-over-flagged-row",
))
def test_source_table_dimensions_exclude_docling_header_rows(
        num_rows, num_cols, cells, expected):
    assert _dimensions(num_rows, num_cols, cells) == expected


@pytest.mark.parametrize("cells", NEGATIVE_OFFSET_CELLS)
def test_negative_offsets_keep_the_established_rule_without_raising(cells):
    # The pre-fix oracle gave (1, 2): one header row, no promoted title.
    assert _dimensions(2, 2, cells) == (1, 2)


def _worksheet_table(data_rows: int) -> dict:
    table = _source_item("#/tables/0", 3, label="table", text="")
    table["data"] = {"num_rows": 2 + data_rows, "num_cols": 8,
                     "table_cells": _worksheet_cells(data_rows)}
    return table


def _worksheet_record(data_rows: int, *, flattened: bool = True) -> dict:
    """docling-core >= 2.99 joins both header bands into one Markdown row."""
    if flattened:
        header = ["Scenario - Label"] * 2 + [
            f"Year {year} - {band}" for year in (1, 2, 3)
            for band in ("Low", "High")]
        rows = []
    else:  # docling-core < 2.99 published the second band as data.
        header = ["Scenario"] * 2 + [
            f"Year {year}" for year in (1, 2, 3) for _band in (0, 1)]
        rows = [["Label", "Label"] + ["Low", "High"] * 3]
    rows += [[*labels] + ["$____"] * 6
             for labels in _WORKSHEET_LABELS[:data_rows]]
    text = "\n".join("| " + " | ".join(cells) + " |"
                     for cells in (header, ["---"] * 8, *rows))
    record = _record(0, "#/tables/0", 3, text=text,
                     content_type="table", content_source="table")
    record["metadata"].update(table_rows=len(rows), table_cols=8)
    return record


@pytest.mark.parametrize(
    "data_rows", (2, 1), ids=("worksheet", "page-break-continuation"))
def test_stacked_column_header_rows_are_not_native_data_rows(data_rows):
    report = _build([_worksheet_record(data_rows)],
                    {"tables": [_worksheet_table(data_rows)]})

    assert report["status"] == "pass"
    assert report["table_retrieval"]["issues"] == {}


@pytest.mark.parametrize(
    "data_rows", (2, 1), ids=("worksheet", "page-break-continuation"))
def test_stacked_header_band_published_as_data_row_is_rejected(data_rows):
    report = _build([_worksheet_record(data_rows, flattened=False)],
                    {"tables": [_worksheet_table(data_rows)]})

    assert report["status"] == "fail"
    assert report["table_retrieval"]["issues"] == {
        "source_native_row_count_mismatch": [0]}
