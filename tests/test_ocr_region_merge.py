"""Opt-in merge of interleaved same-label OCR layout regions.

Off by default: every default parameter set, digest, converter option and
conversion manifest stays byte-identical; the setting is recorded only when it
can affect OCR.  All geometry is synthetic.
"""

from contextlib import ExitStack
import itertools
import json
import logging
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest

import job_runtime
import rag
from test_ocr_angle_classifier import (
    _DEFAULT_SHAPES, _FIXED_LOCK, _binding, _capture_generation_options,
    _commit_fake_conversion, _parameters)
from test_pipeline_runner import _args, _index_outcome


_FLAG = "--ocr-merge-interleaved-regions"
_KEY = "ocr_merge_interleaved_regions"
_PITCH, _HEIGHT = 14, 12  # synthetic OCR line pitch and height, in points
_MERGE_PIPELINE = object()  # stands in for the merging pipeline class


def _box(l, t, r, b):  # noqa: E741 - Docling's own field names
    return SimpleNamespace(l=l, t=t, r=r, b=b)


def _line(index, top=None, *, left=72, right=540, height=_HEIGHT):
    top = 100 + _PITCH * index if top is None else top
    box = _box(left, top, right, top + height)
    return SimpleNamespace(
        index=index, rect=SimpleNamespace(to_bounding_box=lambda: box))


def _region(cid, label, lines, *, bbox=None):
    boxes = [line.rect.to_bounding_box() for line in lines]
    bbox = bbox or _box(min(b.l for b in boxes), min(b.t for b in boxes),
                        max(b.r for b in boxes), max(b.b for b in boxes))
    return SimpleNamespace(
        id=cid, label=SimpleNamespace(value=label), bbox=bbox,
        confidence=0.9, cells=list(lines), children=[])


def _split_paragraph(first_label="text", second_label="text"):
    # One paragraph, lines 0-5.  The regions overlap on lines 2-3 (26 pt):
    # Docling gave line 3 to the first region and line 2 to the second.
    return [_region(1, first_label, [_line(0), _line(1), _line(3)]),
            _region(2, second_label, [_line(2), _line(4), _line(5)])]


def _indexes(cluster):
    return [cell.index for cell in cluster.cells]


def _bounds(cluster):
    return (cluster.bbox.l, cluster.bbox.t, cluster.bbox.r, cluster.bbox.b)


def _same(actual, expected):
    assert [id(item) for item in actual] == [id(item) for item in expected]


@pytest.mark.parametrize("label", ["text", "list_item", "footnote"])
def test_split_paragraph_is_merged_into_top_to_bottom_order(label):
    first, second = _split_paragraph(label, label)
    picture = _region(9, "picture", [], bbox=_box(72, 300, 540, 400))

    merged = rag._merge_interleaved_ocr_regions([second, picture, first])

    _same(merged, [first, picture])
    assert (first.id, first.label.value, first.confidence) == (1, label, 0.9)
    assert _indexes(first) == [0, 1, 2, 3, 4, 5]
    assert _bounds(first) == (72, 100, 540, 182)


def test_base_is_the_region_whose_first_line_comes_first():
    later = _region(2, "text", [_line(2), _line(3), _line(4), _line(5)])
    earlier = _region(7, "text", [_line(0), _line(1), _line(3)])
    kept_line = earlier.cells[2]

    merged = rag._merge_interleaved_ocr_regions([later, earlier])

    _same(merged, [earlier])
    assert (earlier.id, _indexes(earlier)) == (7, [0, 1, 2, 3, 4, 5])
    assert earlier.cells[3] is kept_line


@pytest.mark.parametrize("touch", [None, 2, 4, 6])
def test_an_edge_touch_of_a_few_points_does_not_merge(touch):
    # With nothing to merge, the input itself is returned untouched, even
    # when it is not in reading order.
    upper = _region(1, "text", [_line(0), _line(1)])  # 100..126
    top = 140 if touch is None else 126 - touch
    lower = _region(2, "text", [_line(2, top), _line(3, top + _PITCH)])
    bbox, cells = upper.bbox, upper.cells
    clusters = [lower, upper]

    assert rag._merge_interleaved_ocr_regions(clusters) is clusters
    _same(clusters, [lower, upper])
    assert upper.bbox is bbox and upper.cells is cells
    assert (_indexes(upper), _indexes(lower)) == ([0, 1], [2, 3])
    assert rag._merge_interleaved_ocr_regions([]) == []


@pytest.mark.parametrize(("top", "merges"), [(102, True), (102.1, False)])
def test_a_line_must_lie_at_least_80_percent_inside(top, merges):
    upper = _region(1, "text", [_line(0, 100, height=10)])  # 100..110
    clusters = [upper, _region(2, "text", [_line(1, top, height=10)])]

    merged = rag._merge_interleaved_ocr_regions(clusters)

    assert (merged is clusters) is not merges
    assert _indexes(upper) == ([0, 1] if merges else [0])


@pytest.mark.parametrize(("first", "second", "parent"), [
    ("text", "list_item", None), ("text", "footnote", None),
    ("list_item", "footnote", None),
    ("text", "text", 0), ("text", "text", 1), ("footnote", "footnote", 0),
    *((label, label, None) for label in (
        "picture", "table", "form", "key_value_region", "document_index",
        "section_header", "caption", "page_header", "formula", "code")),
])
def test_ineligible_pairs_are_never_merged(first, second, parent):
    # Different labels, children, or any label but text/list_item/footnote.
    clusters = _split_paragraph(first, second)
    if parent is not None:
        clusters[parent].children = [SimpleNamespace(id=5)]

    assert rag._merge_interleaved_ocr_regions(clusters) is clusters
    assert [_indexes(c) for c in clusters] == [[0, 1, 3], [2, 4, 5]]


def test_interleaving_is_closed_transitively():
    def regions():  # boxes 100..140, 114..168 and 142..182
        return [_region(cid, "text", [_line(a), _line(b)])
                for cid, (a, b) in enumerate([(0, 2), (1, 4), (3, 5)], 1)]

    first, _, third = regions()
    pair = [first, third]
    assert rag._merge_interleaved_ocr_regions(pair) is pair  # not directly

    first, second, third = regions()
    merged = rag._merge_interleaved_ocr_regions([third, second, first])

    _same(merged, [first])
    assert _indexes(first) == [0, 1, 2, 3, 4, 5]
    assert _bounds(first) == (72, 100, 540, 182)


@pytest.mark.parametrize(("stray", "merges"), [
    (dict(), True), (dict(left=300, right=300), False), (dict(height=0), False)
])
def test_zero_area_lines_are_ignored(stray, merges):
    # The upper region's line 2 lies inside the lower one, which comes first.
    upper = _region(1, "text", [_line(0), _line(1), _line(2, 150, **stray)],
                    bbox=_box(72, 100, 540, 126))
    clusters = [_region(2, "text", [_line(3, 142), _line(4, 156)]), upper]

    merged = rag._merge_interleaved_ocr_regions(clusters)

    assert (merged is clusters) is not merges
    assert _indexes(upper) == ([0, 1, 2, 3, 4] if merges else [0, 1, 2])


def test_merged_pages_are_resorted_by_docling_reading_order_keys():
    # Docling's _sort_clusters(mode="id") keys; equal keys keep input order.
    first, second = _split_paragraph()
    later = _region(4, "text", [_line(10)])
    low, right, left, twin = (
        _region(cid, "picture", [], bbox=_box(*box)) for cid, box in
        enumerate([(72, 500, 540, 600), (300, 400, 540, 450),
                   (72, 400, 290, 450), (72, 400, 290, 450)], 5))

    merged = rag._merge_interleaved_ocr_regions(
        [low, later, twin, second, right, first, left])

    _same(merged, [first, later, twin, left, right, low])


class _ConverterBuilt(Exception):
    pass


@pytest.mark.parametrize(("ocr", "usable_text", "merge", "wired", "warns"), [
    (None, False, False, False, False),  # default: Docling's own class
    (None, False, True, True, False),    # auto-enabled OCR, merged
    (True, True, True, True, False),     # --ocr
    (None, True, True, False, True),     # auto keeps OCR off: no effect
    (False, False, True, False, True),   # --no-ocr wins
])
def test_generation_wires_the_merging_pipeline_only_when_ocr_runs(
        monkeypatch, tmp_path, caplog, ocr, usable_text, merge, wired,
        warns):
    observed, _ = _capture_generation_options(
        monkeypatch, usable_text=usable_text)
    monkeypatch.setattr(rag, "_configure_docling_model_artifacts",
                        lambda *_args, **_kwargs: Path("artifacts"))
    monkeypatch.setattr(
        rag, "_interleaved_region_merge_pipeline_cls",
        lambda: _MERGE_PIPELINE)

    def converter(*, format_options):
        observed.update(format_options["pdf"])
        raise _ConverterBuilt

    module = ModuleType("docling.document_converter")
    module.DocumentConverter, module.PdfFormatOption = converter, dict
    monkeypatch.setitem(sys.modules, "docling.document_converter", module)
    source = tmp_path / "book.pdf"
    source.write_bytes(b"pdf")
    original = rag.ConversionInputBinding(
        kind="original", name=source.name, sha256="0" * 64,
        size=source.stat().st_size)

    with ExitStack() as snapshots, pytest.raises(_ConverterBuilt), \
            caplog.at_level(logging.INFO, logger="rag"):
        rag._convert_pdf_generation(
            source, tmp_path / "book.json", snapshot_stack=snapshots,
            original_input=original, auto_preprocess=True, ocr=ocr,
            ocr_merge_interleaved_regions=merge)

    assert observed.pop("pipeline_options").do_ocr is (
        ocr is True or ocr is None and not usable_text)
    assert observed == ({"pipeline_cls": _MERGE_PIPELINE} if wired else {})
    assert ("interleaved regions merged" in caplog.text) is wired
    assert (f"{_FLAG} has no effect" in caplog.text) is warns


def test_merging_pipeline_wraps_only_the_layout_postprocessing_output(
        monkeypatch):
    pytest.importorskip("docling")
    from docling.datamodel.base_models import Cluster, LayoutPrediction
    from docling.document_converter import PdfFormatOption
    from docling.pipeline.standard_pdf_pipeline import StandardPdfPipeline
    from docling_core.types.doc import BoundingBox, CoordOrigin, DocItemLabel
    from docling_core.types.doc.page import BoundingRectangle, TextCell

    # The flag-off converter keeps this default class.
    assert (PdfFormatOption.model_fields["pipeline_cls"].default
            is StandardPdfPipeline)

    def cluster(cid, indexes):
        boxes = [BoundingBox(l=72, t=100 + 14 * i, r=540, b=112 + 14 * i)
                 for i in indexes]
        cells = [TextCell(index=i, rect=BoundingRectangle.from_bounding_box(
            box), text="x", orig="x", from_ocr=True, confidence=1.0)
            for i, box in zip(indexes, boxes)]
        return Cluster(id=cid, label=DocItemLabel.TEXT, cells=cells,
                       bbox=BoundingBox.enclosing_bbox(boxes))

    split = LayoutPrediction(
        clusters=[cluster(1, [0, 1, 3]), cluster(2, [2, 4, 5])])
    clean = LayoutPrediction(clusters=[cluster(3, [0, 1])])
    seen = []

    class Postprocessor:  # stands in for Docling's own finalized output
        def postprocess_layout(self, conv_res, pages):
            seen.append((conv_res, pages))
            return [split, clean]

    def init(self, pipeline_options):
        self.layout_postprocessing_model = Postprocessor()

    monkeypatch.setattr(StandardPdfPipeline, "__init__", init)
    pipeline_cls = rag._interleaved_region_merge_pipeline_cls()
    assert issubclass(pipeline_cls, StandardPdfPipeline)
    pipeline = pipeline_cls(pipeline_options=object())  # as Docling calls
    pages = [SimpleNamespace(predictions=SimpleNamespace(layout=None))
             for _ in range(2)]

    assert list(pipeline.layout_postprocessing_model("run", pages)) == pages

    assert seen == [("run", pages)]
    merged, untouched = (page.predictions.layout for page in pages)
    assert untouched is clean
    assert isinstance(merged, LayoutPrediction) and merged is not split
    [region] = merged.clusters
    assert (region.id, _indexes(region)) == (1, [0, 1, 2, 3, 4, 5])
    assert isinstance(region.bbox, BoundingBox)
    assert region.bbox.as_tuple() == (72, 100, 540, 182)
    assert region.bbox.coord_origin == CoordOrigin.TOPLEFT


@pytest.fixture
def pinned_lock(monkeypatch):
    monkeypatch.setattr(
        rag, "_model_artifact_lock_sha256", lambda: _FIXED_LOCK)


@pytest.mark.parametrize("classifier", [True, False])
@pytest.mark.parametrize("shape", sorted(_DEFAULT_SHAPES))
def test_merge_is_recorded_only_when_ocr_may_run(
        pinned_lock, shape, classifier):
    # Unset or False keeps the golden digests; --no-ocr normalizes it away.
    kwargs, golden = _DEFAULT_SHAPES[shape]
    recorded = kwargs["ocr"] is not False
    for full_page in ((kwargs["ocr_full_page"],) if recorded
                      else (False, True)):
        shaped = dict(kwargs, ocr_full_page=full_page,
                      ocr_angle_classifier=classifier)
        plain = rag._conversion_parameters(**shaped)
        merged = rag._conversion_parameters(**shaped, **{_KEY: True})
        assert rag._conversion_parameters(**shaped, **{_KEY: False}) == plain
        assert _KEY not in plain
        assert (rag._artifact_parameters_sha256(plain) == golden) is (
            classifier or not recorded)
        assert merged == ({**plain, _KEY: True} if recorded else plain)


class _StopAfterParse(Exception):
    pass


@pytest.mark.parametrize("command", ["convert", "full", "batch"])
@pytest.mark.parametrize(("flags", "merge", "classifier"), [
    ([], False, True),
    ([_FLAG], True, True),
    (["--ocr-full-page", _FLAG], True, True),
    (["--ocr-no-angle-classifier", _FLAG], True, False),
    (["--no-ocr", _FLAG], True, True),
])
def test_cli_commands_forward_the_merge_choice(
        monkeypatch, tmp_path, command, flags, merge, classifier):
    source = tmp_path / "book.pdf"
    source.write_bytes(b"pdf")
    observed = {}

    def convert(*_args, **kwargs):
        observed.update(kwargs)
        raise _StopAfterParse

    def job(_pdf, args, **_kwargs):
        observed[_KEY] = getattr(args, _KEY)
        raise _StopAfterParse

    monkeypatch.setattr(rag, "convert_pdf", convert)
    monkeypatch.setattr(rag, "_run_pipeline_job", job)
    pdf_args = [str(source)] if command == "batch" else ["--pdf", str(source)]

    with pytest.raises(_StopAfterParse):
        rag.main([command, *pdf_args, *flags])

    assert observed[_KEY] is merge
    if command == "convert":
        assert observed["ocr_angle_classifier"] is classifier


@pytest.mark.parametrize("flag", [None, False, True])
@pytest.mark.parametrize(("resume", "reusable"), [
    (False, False), (True, False), (True, True)])
def test_pipeline_stages_bind_the_merge_to_conversion_and_publication(
        monkeypatch, tmp_path, flag, resume, reusable):
    monkeypatch.setattr(rag, "OUTPUT_DIR", tmp_path / "output")
    seen = {"complete": []}

    def complete(*_args, parameters, **_kwargs):
        seen["complete"].append(parameters)
        return reusable or "convert" in seen

    def publish(*_args, conversion_parameters, **_kwargs):
        seen["publication"] = conversion_parameters
        return {"gates": [{"name": name, "status": "pass"} for name
                          in rag._publication_core.PUBLICATION_GATE_NAMES]}

    monkeypatch.setattr(rag, "_converted_outputs_complete", complete)
    for name in ("_chunks_complete", "_quality_report_complete",
                 "_unified_export_complete", "_split_export_complete",
                 "_raptor_output_complete"):
        monkeypatch.setattr(rag, name, lambda *_a, **_k: True)
    monkeypatch.setattr(rag, "_publish_pipeline_publication", publish)
    monkeypatch.setattr(
        rag, "convert_pdf", lambda *_a, **k: seen.setdefault("convert", k))
    monkeypatch.setattr(rag, "chunk_document", lambda *_a, **_k: None)
    monkeypatch.setattr(rag, "export_markdown", lambda *_a, **_k: None)
    monkeypatch.setattr(
        rag, "_index_chunks_for_backend", lambda *_a, **_k: _index_outcome())
    args = _args(batch_size=2, backend="pypdfium2", no_preprocess=True,
                 **({} if flag is None else {_KEY: flag}))

    rag._run_pipeline_stages(
        Path("Book.pdf"), rag._output_paths_for_name("Book"), args,
        resume=resume, watermark=None)

    expected = rag._conversion_parameters(
        batch_size_override=2, backend="pypdfium2", auto_preprocess=False,
        ocr=None, ocr_full_page=False, watermark=None, **{_KEY: bool(flag)})
    assert ("convert" in seen) is not reusable  # reusable: [SKIP] convert
    if not reusable:
        assert seen["convert"][_KEY] is bool(flag)
    assert seen["publication"] == expected
    assert seen["complete"] and all(
        parameters == expected for parameters in seen["complete"])


@pytest.mark.parametrize(("ocr", "flag", "expected"), [
    (None, True, True), (True, True, True), (None, False, False),
    (None, None, False),   # arguments from before the flag existed
    (False, True, False),  # --no-ocr wins, as for --ocr-full-page
])
def test_resume_command_preserves_the_merge_choice(ocr, flag, expected):
    command = rag._build_resume_cmd(Path("Book.pdf"), SimpleNamespace(
        ocr=ocr, ocr_full_page=False,
        **({} if flag is None else {_KEY: flag})))

    assert (_FLAG in command) is expected


def test_background_jobs_pass_the_flag_through_unchanged():
    arguments = ["--pdf", "book.pdf", _FLAG]

    assert job_runtime._validate_argv(arguments) == tuple(arguments)
    canonical = rag._canonical_background_security_argv(
        "convert", list(arguments))
    assert canonical[:3] == arguments


_LEGACY_ROOT = {"schema_version", "stage", "source_sha256", "source_name",
                "source_record_count", "parameters_sha256", "outputs",
                "source", "effective_input"}


@pytest.mark.parametrize("classifier", [True, False])
@pytest.mark.parametrize(("ocr", "merge"), [
    (None, False), (None, True), (True, True), (False, True)])
def test_conversion_threads_and_records_the_merge(
        monkeypatch, tmp_path, ocr, merge, classifier):
    observed = {}
    source, document, markdown, manifest = _commit_fake_conversion(
        monkeypatch, tmp_path, observed, ocr=ocr,
        ocr_angle_classifier=classifier, **{_KEY: merge})
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    binding = _binding(document)
    recorded = merge and ocr is not False
    unclassified = not classifier and ocr is not False

    assert observed[_KEY] is merge
    assert set(payload) == _LEGACY_ROOT | {
        key for key, flagged in (("ocr_angle_classifier", unclassified),
                                 (_KEY, recorded)) if flagged}
    assert payload.get(_KEY, True) is True
    assert binding.ocr_merge_interleaved_regions is recorded
    assert binding.ocr_angle_classifier is not unclassified
    committed = _parameters(
        ocr=ocr, ocr_angle_classifier=classifier, **{_KEY: merge})
    for other, merged in itertools.product((True, False), repeat=2):
        parameters = _parameters(
            ocr=ocr, ocr_angle_classifier=other, **{_KEY: merged})
        assert rag._converted_outputs_complete(
            source, document, markdown,
            parameters=parameters) is (parameters == committed)


@pytest.mark.parametrize("digest_merge", [False, True])
def test_manifest_record_that_contradicts_its_digest_is_not_resumable(
        monkeypatch, tmp_path, digest_merge):
    # The digest and outputs still match; only the readable record differs.
    kwargs = {_KEY: True} if digest_merge else {}
    source, document, markdown, manifest = _commit_fake_conversion(
        monkeypatch, tmp_path, ocr=None, **kwargs)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    if digest_merge:
        del payload[_KEY]
    else:
        payload[_KEY] = True
    rag._atomic_write_json(manifest, payload)
    parameters = _parameters(ocr=None, **kwargs)
    binding = _binding(document)

    assert binding.ocr_merge_interleaved_regions is not digest_merge
    assert rag._fixed_artifacts_complete(
        manifest, stage="conversion", source_sha256=binding.source_sha256,
        source_record_count=None, parameters=parameters,
        outputs={"docling_json": document, "docling_markdown": markdown},
        source_name=binding.source_name,
        schema_version=rag.CONVERSION_COMPLETION_SCHEMA_VERSION)
    assert not rag._converted_outputs_complete(
        source, document, markdown, parameters=parameters)


@pytest.mark.parametrize("value", [False, None, "true", 1])
def test_manifest_loader_rejects_invalid_merge_values(
        monkeypatch, tmp_path, value):
    _, document, _, manifest = _commit_fake_conversion(
        monkeypatch, tmp_path, ocr=None, **{_KEY: True})
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload[_KEY] = value
    rag._atomic_write_json(manifest, payload)

    with pytest.raises(ValueError, match="OCR override"):
        _binding(document)
