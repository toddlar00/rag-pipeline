"""Model-free stage calls, exact raster geometry and adversarial resource cases."""

import copy
import hashlib
from types import SimpleNamespace as NS

import pytest

import ocr_stage_diagnostics as policy
import ocr_stage_runtime as runtime


np = pytest.importorskip("numpy")
SOURCE = b"generated-only stage source stand-in"
REFERENCE = "b" * 64


def reference():
    return {"schema_version": 1, "kind": "ocr_stage_reference", "source_sha256": hashlib.sha256(SOURCE).hexdigest(),
            "page_count": 1, "scope": "operator_declared_complete_selected_pages",
            "coordinate_system": "original_page_display_fraction", "annotation_provenance": "synthetic_generator",
            "pages": [{"page_number": 1, "geometry": {"width_points": 24, "height_points": 24, "rotation": 0},
                       "lines": [{"region_id": "line-1", "bbox": [.1, .1, .9, .2], "text": "SYNTHETIC",
                                  "order": 0, "cell_id": None}], "cells": [], "recognition_region_ids": ["line-1"]}]}


BOX = [[10., 10.], [90., 10.], [90., 20.], [10., 20.]]


class Component:
    rec_image_shape = [3, 48, 320]
    rec_batch_num = 6

    def __init__(self, result=None, error=None):
        self.result, self.error, self.calls = result, error, 0

    def __call__(self, *_args, **_kwargs):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


class Engine:
    def __init__(self, text="SYNTHETIC"):
        self.text_det = Component(NS(boxes=[BOX]))
        self.text_cls = Component()
        self.text_rec = Component(NS(txts=(text,)))
        self.inputs, self.flags = [], []

    def prepare(self, pixels, *, use_det, use_cls, use_rec):
        """The stage test double explicitly supports the guarded reader API."""
        assert pixels.ndim == 3 and pixels.shape[2] == 3
        assert all(type(value) is bool for value in (use_det, use_cls, use_rec))

    def __call__(self, pixels, *, use_det, use_cls, use_rec):
        self.inputs.append(pixels.copy())
        self.flags.append((use_det, use_cls, use_rec))
        detected = self.text_det(pixels) if use_det else None
        if use_cls:
            self.text_cls([pixels])
        recognized = self.text_rec(NS(img=[pixels])) if use_rec else None
        if use_det and use_rec:
            return NS(boxes=detected.boxes, txts=recognized.txts)
        return detected if use_det else recognized


class Reader(runtime.RapidOCRStageReader):
    engine_type = Engine
    render_error = None

    def __enter__(self):
        self._engine = self.engine_type()
        return self

    def close(self):
        self.recorder.observation = {"synthetic": True}

    def preflight(self, ref):
        return [{"gold": gold, "geometry": copy.deepcopy(gold["geometry"]), "reason": None,
                 "width": 100, "height": 100} for gold in ref["pages"]]

    def render(self, _plan):
        if self.render_error:
            raise self.render_error
        return np.arange(30_000, dtype=np.uint8).reshape(100, 100, 3)

    def _load_engine(self):
        return self._engine


def collect(ref=None, reader=Reader):
    return runtime.collect_stage_observation(SOURCE, ref or reference(), reference_sha256=REFERENCE,
                                              configuration=dict(policy.DEFAULT_CONFIGURATION), reader_factory=reader)


def test_complete_schedule_gold_is_exact_original_crop_and_roles_are_observed():
    observed, recorder = collect()
    assert policy.validate_stage_observation(observed, reference(), REFERENCE) == observed
    assert [call["stage"] for call in observed["calls"]] == ["detection", "full_page", "gold_crop"]
    assert [call["id"] for call in recorder.receipt_calls] == ["call-0001", "call-0002", "call-0003"]
    crop = observed["pages"][0]["gold_crops"][0]
    assert crop["raster_bbox"] == [10, 10, 90, 20]
    pixels = np.arange(30_000, dtype=np.uint8).reshape(100, 100, 3)
    assert crop["pixel_sha256"] == runtime.pixel_digest(pixels[10:20, 10:90].copy())
    assert observed["calls"][2]["roles"] == {
        "detection": {"attempted": 0, "completed": 0, "failed": 0},
        "classification": {"attempted": 0, "completed": 0, "failed": 0},
        "recognition": {"attempted": 1, "completed": 1, "failed": 0}}
    assert observed["canonical_extraction_modified"] is False


def test_explicit_flags_every_call_and_engine_input_cannot_mutate_shared_pixels():
    engine, recorder = Engine(), runtime.StageRecorder()
    pixels = np.ones((100, 100, 3), dtype=np.uint8)
    original = pixels.copy()
    for stage in ("gold_crop", "detection", "full_page", "gold_crop"):
        recorder.invoke(engine, pixels, stage=stage, page_number=1, region_id="line-1" if stage == "gold_crop" else None)
    assert engine.flags == [(False, False, True), (True, False, False), (True, True, True), (False, False, True)]
    engine.inputs[0][:] = 0
    assert np.array_equal(pixels, original)
    assert isinstance(engine.text_det, Component)  # Wrappers do not accumulate.


@pytest.mark.parametrize("text,status", [("", "empty"), (" \t\n", "empty"), ("not", "available")])
def test_gold_empty_recognition_is_observed_and_keeps_raw_text(text, status):
    recorder = runtime.StageRecorder()
    reader = Reader(SOURCE, recorder=recorder)
    reader._engine = Engine(text)
    result = reader.stage(np.zeros((20, 100, 3), dtype=np.uint8), stage="gold_crop", page_number=1, region_id="line-1")
    assert result["status"] == status and result["text"] == text and result["reason"] is None


@pytest.mark.parametrize("texts", [None, (), (None,), ("a", "b"), ("x" * 4097,)])
def test_unknown_or_invalid_recognition_never_becomes_known_empty(texts):
    recorder = runtime.StageRecorder()
    reader = Reader(SOURCE, recorder=recorder)
    reader._engine = Engine()
    reader._engine.text_rec.result = NS(txts=texts, scores=[1.])
    result = reader.stage(np.zeros((20, 100, 3), dtype=np.uint8), stage="gold_crop", page_number=1, region_id="line-1")
    assert result["status"] == "unavailable" and result["text"] is None


def test_swallowed_component_failure_is_not_promoted_by_outer_completion():
    class Swallows(Engine):
        def __call__(self, pixels, **flags):
            try:
                return super().__call__(pixels, **flags)
            except ValueError:
                return NS(txts=None, scores=[1.])
    reader = Reader(SOURCE, recorder=runtime.StageRecorder())
    reader._engine = Swallows()
    reader._engine.text_rec.error = ValueError("PRIVATE error")
    result = reader.stage(np.zeros((20, 100, 3), dtype=np.uint8), stage="gold_crop", page_number=1, region_id="line-1")
    assert result["status"] == "unavailable" and result["reason"] == "stage_failed"
    assert reader.recorder.calls[0]["status"] == "completed"
    assert reader.recorder.calls[0]["roles"]["recognition"]["failed"] == 1
    assert "PRIVATE" not in repr(result) + repr(reader.recorder.calls)


def test_cancellation_propagates_and_original_components_are_restored():
    engine, recorder = Engine(), runtime.StageRecorder()
    engine.text_rec.error = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        recorder.invoke(engine, np.zeros((20, 100, 3), dtype=np.uint8), stage="gold_crop", page_number=1, region_id="line-1")
    assert isinstance(engine.text_rec, Component) and recorder._active is None
    assert recorder.calls[0]["status"] == "failed"


def test_render_failure_retains_all_requested_work_as_unavailable():
    class Fails(Reader):
        render_error = ValueError("PRIVATE render failure")
    observed, recorder = collect(reader=Fails)
    page = observed["pages"][0]
    assert page["raster"] is None and not recorder.calls
    assert page["geometry"] == reference()["pages"][0]["geometry"]
    assert page["gold_crops"][0]["reason"] == "render_failed"


def test_gold_extreme_aspect_ratio_is_not_sent_to_recognizer():
    ref = reference()
    ref["pages"][0]["lines"][0]["bbox"] = [0, 0, 1, .001]
    observed, recorder = collect(ref)
    crop = observed["pages"][0]["gold_crops"][0]
    assert crop["reason"] == "resource_limit" and crop["call_id"] is None
    assert len(recorder.calls) == 2


def test_runtime_recognition_guard_catches_thin_crops_before_component_call():
    engine, recorder = Engine(), runtime.StageRecorder()
    with pytest.raises(ValueError):
        recorder.invoke(engine, np.zeros((1, 6000, 3), dtype=np.uint8), stage="gold_crop", page_number=1, region_id="line-1")
    assert engine.text_rec.calls == 0
    assert not recorder.calls


def test_detector_crop_budget_checked_before_engine_allocates_crop_arrays():
    engine, recorder = Engine(), runtime.StageRecorder()
    huge = [[0., 0.], [3000., 0.], [3000., 3000.], [0., 3000.]]
    engine.text_det.result = NS(boxes=[huge] * 12)
    with pytest.raises(ValueError):
        recorder.invoke(engine, np.zeros((3000, 3000, 3), dtype=np.uint8), stage="detection", page_number=1)
    assert recorder.calls[0]["roles"]["detection"]["completed"] == 1


@pytest.mark.parametrize("mutate", [lambda r: r.update(source_sha256="a" * 64),
                                    lambda r: r["pages"][0].update(geometry=None)])
def test_invalid_reference_or_source_rejected_before_reader(mutate):
    ref = reference()
    mutate(ref)
    with pytest.raises(ValueError):
        collect(ref, reader=lambda *_args, **_kwargs: pytest.fail("reader must not open"))


@pytest.mark.parametrize("value", [False, 1, {}, {**policy.DEFAULT_CONFIGURATION, "dpi": True},
                                   {**policy.DEFAULT_CONFIGURATION, "max_normalized_recognition_width": 1000000}])
def test_invalid_runtime_configuration_precedes_model_work(value):
    with pytest.raises((ValueError, TypeError)):
        runtime.collect_stage_observation(SOURCE, reference(), reference_sha256=REFERENCE,
                                          configuration=value, reader_factory=lambda *_a, **_k: pytest.fail("no reader"))


@pytest.mark.parametrize("bad", [None, [], [BOX] * 2001, [[[0, 0], [1, 1], [0, 1], [1, 0]]],
                                 [[[0, 0], [1, 0], [1, float("nan")], [0, 1]]]])
def test_bad_detector_output_does_not_emit_geometry(bad):
    if bad is None:
        with pytest.raises(ValueError):
            runtime._boxes(object(), 100, 100)
    elif bad == []:
        assert runtime._boxes(NS(boxes=bad), 100, 100) == []
    else:
        with pytest.raises(ValueError):
            runtime._boxes(NS(boxes=bad), 100, 100)


def test_installed_public_rapidocr_routes_without_constructing_any_model():
    rapidocr = pytest.importorskip("rapidocr")
    from rapidocr.ch_ppocr_cls.utils import TextClsOutput
    from rapidocr.ch_ppocr_det.utils import TextDetOutput
    from rapidocr.ch_ppocr_rec.typings import TextRecOutput

    engine = object.__new__(rapidocr.RapidOCR)
    engine.load_img = lambda image: image
    engine.cfg = NS(Global=NS(use_preprocess_img=False, use_vertical_padding=False, text_score=0., font_path=None),
                    Rec=NS(lang_type="en"))
    engine.return_word_box, engine.return_single_char_box, engine.text_score = False, False, 0.
    engine.text_det = Component(TextDetOutput(boxes=np.array([BOX], dtype=np.float32), scores=[.9]))
    engine.text_cls = Component(TextClsOutput(img_list=[np.zeros((10, 80, 3), dtype=np.uint8)]))
    engine.text_rec = Component(TextRecOutput(imgs=[np.zeros((10, 80, 3), dtype=np.uint8)], txts=("",),
                                            scores=(.9,), word_results=((),)))
    recorder, pixels = runtime.StageRecorder(), np.zeros((100, 100, 3), dtype=np.uint8)
    det, _ = recorder.invoke(engine, pixels, stage="detection", page_number=1)
    assert len(det.boxes) == 1 and engine.text_rec.calls == 0
    rec, _ = recorder.invoke(engine, pixels[10:20, 10:90].copy(), stage="gold_crop", page_number=1, region_id="line-1")
    assert rec.txts == ("",) and engine.text_det.calls == 1 and engine.text_cls.calls == 0
    full, _ = recorder.invoke(engine, pixels, stage="full_page", page_number=1)
    assert full.boxes is None  # Blank recognition removes a detected box upstream.
    assert recorder.calls[-1]["roles"]["recognition"]["completed"] == 1
    assert recorder.recognition_returned["call-0003"] is True


@pytest.mark.parametrize("dpi", [72, 96, 150, 180, 300, 400, 599, 600])
@pytest.mark.parametrize("dimensions", [(24, 48), (100.1, 200.2), (612, 792), (79.91999816894531, 25.19999885559082),
                                       (300.239999, 411.120003)])
def test_pure_raster_dimensions_match_installed_mupdf_float_geometry(dpi, dimensions):
    pymupdf = pytest.importorskip("pymupdf")
    rect = pymupdf.Rect(0, 0, *dimensions)
    expected = (rect * pymupdf.Matrix(dpi / 72, dpi / 72)).irect
    actual = policy.raster_dimensions({"width_points": rect.width, "height_points": rect.height, "rotation": 0}, dpi)
    assert tuple(actual) == (expected.width, expected.height)


@pytest.mark.parametrize("width,height,detection", [(6000, 1, True), (6000, 1, False), (1, 6000, False),
                                                   (32, 6000, True), (5999, 29, True), (5999, 29, False)])
def test_implicit_preprocess_expansions_rejected_without_native_work(width, height, detection):
    with pytest.raises(runtime.StageResourceLimit):
        runtime.working_dimensions(width, height, detection=detection)


@pytest.mark.parametrize("width,height", [(6000, 1), (32, 6000), (1, 6000)])
def test_unsafe_page_never_loads_engine_or_records_invocation(width, height, monkeypatch):
    reader = Reader(SOURCE, recorder=runtime.StageRecorder())
    monkeypatch.setattr(reader, "_load_engine", lambda: pytest.fail("unsafe dimensions must not load engine"))
    result = reader.stage(np.zeros((height, width, 3), dtype=np.uint8), stage="detection", page_number=1)
    assert result["status"] == "unavailable" and result["reason"] == "resource_limit"
    assert result["call_id"] is None and not reader.recorder.calls


@pytest.mark.parametrize("width,height", [(100, 100), (80, 10), (1000, 100), (612, 792), (20, 15)])
def test_working_dimensions_match_native_resize_padding_without_model_calls(width, height):
    pytest.importorskip("rapidocr")
    from rapidocr.utils.process_img import resize_image_within_bounds, apply_vertical_padding
    from rapidocr.ch_ppocr_det.utils import DetPreProcess

    expected = runtime.working_dimensions(width, height, detection=True)
    pixels = np.zeros((height, width, 3), dtype=np.uint8)
    resized, _, _ = resize_image_within_bounds(pixels, 30, 6000)
    padded, _ = apply_vertical_padding(resized, {}, 8, 30)
    detector = DetPreProcess(736, "min").resize(padded)
    assert expected == {"global": [resized.shape[1], resized.shape[0]],
                        "padded": [padded.shape[1], padded.shape[0]],
                        "detector": [detector.shape[1], detector.shape[0]]}
