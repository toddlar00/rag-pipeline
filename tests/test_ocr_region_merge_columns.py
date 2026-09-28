"""Column, geometry and wiring guards of the interleaved OCR region merge.

A line OCR reads across a column gutter must not pull two columns into one
region read line by line; a transitive group merges whatever its input order;
a cell diagonally outside a region never counts as inside it; and Docling's
real run context places the merging model on its layout-postprocess stage.
All geometry is synthetic.
"""

import itertools
from types import SimpleNamespace

import pytest

import rag
from test_ocr_region_merge import (
    _bounds, _box, _indexes, _line, _region, _same)


def _two_columns():
    """Lines 0-9 across both columns; line 4 is read across the gutter.

    Docling gave the gutter-crossing line to the left region, which then
    covers the right column's lines too.
    """
    left = [_line(0, 100, right=290), _line(2, 114, right=290),
            _line(4, 128, right=540), _line(5, 142, right=290),
            _line(7, 156, right=290)]
    right = [_line(1, 100, left=310), _line(3, 114, left=310),
             _line(6, 142, left=310), _line(8, 156, left=310)]
    return [_region(1, "text", left), _region(2, "text", right)]


def test_a_line_across_the_gutter_does_not_merge_two_columns():
    clusters = _two_columns()
    left, right = clusters
    assert rag._cell_inside_region(right.cells[0], left.bbox)

    assert rag._merge_interleaved_ocr_regions(clusters) is clusters
    assert (_indexes(left), _indexes(right)) == (
        [0, 2, 4, 5, 7], [1, 3, 6, 8])
    assert _bounds(left) == (72, 100, 540, 168)


@pytest.mark.parametrize(("lines", "expected"), [
    ([(72, 540)], (72, 540)),
    ([(72, 540), (72, 300), (80, 540)], (72, 540)),
    ([(72, 500), (90, 540)], (81, 520)),
    # A zero-width line has no extent and does not vote.
    ([(72, 540), (300, 300)], (72, 540)),
    ([(300, 300)], None),
])
def test_column_span_is_the_median_extent_of_positive_area_lines(
        lines, expected):
    region = _region(1, "text", [
        _line(index, left=left, right=right)
        for index, (left, right) in enumerate(lines)],
        bbox=_box(72, 100, 540, 200))

    assert rag._layout_column_span(region) == expected


@pytest.mark.parametrize(("second", "agree"), [
    ((72, 540), True), ((150, 540), True), ((72, 450), True),
    # A column inside a full-width span agrees: the narrower one decides.
    ((310, 540), True),
    # The narrower span must overlap by at least 80% of its width.
    ((400, 700), False), ((291, 700), False), ((72, 72), False),
])
def test_columns_agree_only_when_the_narrower_span_mostly_overlaps(
        second, agree):
    first = _region(1, "text", [_line(0), _line(1)])
    other = _region(2, "text", [
        _line(2, left=second[0], right=second[1]),
        _line(3, left=second[0], right=second[1])])

    assert rag._layout_columns_agree(first, other) is agree


@pytest.mark.parametrize("order", list(itertools.permutations(range(3))))
def test_interleaving_is_closed_transitively_in_any_input_order(order):
    regions = [_region(cid, "text", [_line(a), _line(b)])
               for cid, (a, b) in enumerate([(0, 2), (1, 4), (3, 5)], 1)]

    merged = rag._merge_interleaved_ocr_regions(
        [regions[position] for position in order])

    _same(merged, [regions[0]])
    assert _indexes(regions[0]) == [0, 1, 2, 3, 4, 5]
    assert _bounds(regions[0]) == (72, 100, 540, 182)


def test_a_cell_diagonally_outside_a_region_is_never_inside_it():
    # Up and to the left of the upper box, the negative overlaps multiply to
    # a large positive "area"; only a positive overlap on both axes counts.
    upper = _region(1, "text", [_line(0), _line(1)])  # 72..540 x 100..126
    stray = _line(4, 20, left=20, right=30, height=10)
    lower = _region(2, "text", [_line(2, 200), _line(3, 214), stray],
                    bbox=_box(72, 200, 540, 226))
    clusters = [upper, lower]

    assert not rag._cell_inside_region(stray, upper.bbox)
    assert rag._layout_columns_agree(upper, lower)
    assert rag._merge_interleaved_ocr_regions(clusters) is clusters


def test_docling_run_context_holds_the_merging_model(monkeypatch):
    """Only model construction is stubbed; Docling builds the real stages."""
    pytest.importorskip("docling")
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.pipeline.standard_pdf_pipeline import StandardPdfPipeline

    inner = SimpleNamespace(postprocess_layout=lambda conv_res, pages: [])

    def init_models(self):
        for name in ("preprocessing_model", "ocr_model", "layout_model",
                     "table_model", "assemble_model"):
            setattr(self, name, SimpleNamespace(name=name))
        self.layout_postprocessing_model = inner

    monkeypatch.setattr(StandardPdfPipeline, "_init_models", init_models)
    pipeline = rag._interleaved_region_merge_pipeline_cls()(
        pipeline_options=PdfPipelineOptions())

    stages = {stage.name: stage for stage in pipeline._create_run_ctx().stages
              if hasattr(stage, "name")}

    model = stages["layout_postprocess"].model
    assert model is pipeline.layout_postprocessing_model
    assert model.inner is inner
