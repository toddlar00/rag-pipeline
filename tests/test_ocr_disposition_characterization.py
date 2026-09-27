"""Pinned upstream disposition characterization; no model/session constructors.

Proposed after design-v2 SHA256
2a81b626e8a872d5e3f30015e7943651d306f31c09dfaadfd0d4dbe7261cc11d.
These tests characterize existing RapidOCR 3.9.2 behavior, including unsafe or
ambiguous inputs; they do not prescribe the admission rules of a future ledger.
Only tiny arrays and actual Python methods are used. Renderer/crop resize and
visualizer construction are replaced with inert callbacks. No OCR model runs.
"""
from __future__ import annotations

from dataclasses import fields
import hashlib
import importlib
import importlib.metadata
from types import SimpleNamespace as NS

import pytest


_PINS = {
    "main.py": "c2ae17098dde838ac3d2933eec5b218d5c4404ded9fe5d32df7c24fd0e54aa39",
    "ch_ppocr_det/main.py": "a56c0f51fd6a8c03a5abf2f0a5843b382b8f3257d1bfacf8d5ac6305d5e63024",
    "ch_ppocr_det/utils.py": "01d25a0b1bbdcdd4aba70a23ae96714c5408df93b295c43ca194952e279adb9e",
    "ch_ppocr_cls/utils.py": "bcadd799e971fe42a2935c2568ad9a0299e24b6e8c55f57abced42eaac324cd9",
    "ch_ppocr_rec/typings.py": "02b88178edab41b275d14bf8f61ce7148c13328f79f330b78f7c3cb77f7c4737",
    "utils/utils.py": "86f8687db6424714be5a6fbd283f6e3b7b198691fb292efd12622b5b40197582",
    "utils/output.py": "8467bbb4b3c140d1f1e8f214043baa7449a1d30b853af3da24fe26238a436314",
    "utils/process_img.py": "abaf2ed615878f618a372cba6157bc6c41a494e05f6c41bf9f7c5c2ba1ee5772",
}


@pytest.fixture
def upstream(monkeypatch):
    try:
        distribution = importlib.metadata.distribution("rapidocr")
    except importlib.metadata.PackageNotFoundError:
        pytest.skip("optional pinned RapidOCR package is not installed")
    # A present but different package is a recharacterization requirement, not
    # a silently passing or skipped substitute for the qualified source.
    assert distribution.version == "3.9.2"
    for name, expected in _PINS.items():
        path = distribution.locate_file("rapidocr/" + name)
        with path.open("rb") as stream:
            raw = stream.read(1024 * 1024 + 1)
        assert 0 < len(raw) <= 1024 * 1024
        assert hashlib.sha256(raw).hexdigest() == expected, name
    np = pytest.importorskip("numpy")
    pytest.importorskip("rapidocr")
    main = importlib.import_module("rapidocr.main")
    detector = importlib.import_module("rapidocr.ch_ppocr_det.main")
    utils = importlib.import_module("rapidocr.utils.utils")
    process = importlib.import_module("rapidocr.utils.process_img")

    def forbidden(*args, **kwargs):
        raise AssertionError("inert characterization must not construct model engines")

    for component in (main.RapidOCR, main.TextDetector, main.TextClassifier, main.TextRecognizer):
        monkeypatch.setattr(component, "__init__", forbidden)
    monkeypatch.setattr(detector, "get_engine", forbidden)
    return NS(np=np, main=main, detector=detector, utils=utils, process=process)


def _box(x, y, width=2, height=2):
    return [[x, y], [x + width, y], [x + width, y + height], [x, y + height]]


def test_detector_reorders_boxes_but_leaves_score_positions_unpermuted(upstream):
    np, module = upstream.np, upstream.detector
    # Duplicate anchors with different widths expose tie stability. The last
    # box is an exact duplicate: it must retain multiplicity, not a content ID.
    boxes = np.array([_box(40, 30), _box(20, 1), _box(20, 1, width=3),
                      _box(5, 0), _box(90, 9), _box(0, 19), _box(20, 1)], dtype=np.float32)
    scores = [0.11, 0.22, 0.33, 0.44, 0.55, 0.66, 0.77]
    expected_order = [3, 1, 2, 6, 4, 5, 0]
    frozen_boxes, frozen_scores = boxes.copy(), list(scores)
    detector = module.TextDetector.__new__(module.TextDetector)
    session_calls = []
    prediction = object()

    def preprocess_factory(max_side):
        assert max_side == 40
        return lambda image: image

    def session(image):
        session_calls.append(image.shape)
        return prediction

    def postprocess(value, shape):
        assert value is prediction and shape == (40, 40)
        return boxes, scores

    detector.get_preprocess, detector.session, detector.postprocess_op = preprocess_factory, session, postprocess
    output = detector(np.zeros((40, 40, 3), dtype=np.uint8))
    assert session_calls == [(40, 40, 3)]
    np.testing.assert_array_equal(output.boxes, boxes[expected_order])
    np.testing.assert_array_equal(boxes, frozen_boxes)
    assert output.scores is scores and output.scores == frozen_scores
    assert output.scores != [scores[index] for index in expected_order]
    assert len(output) == len(boxes) == 7
    assert np.array_equal(output.boxes[1], output.boxes[3])
    # IDs are scoped to returned ordinal positions; duplicate values stay two
    # observations. This assertion does not claim a score-to-box correspondence.
    returned = [(f"d{index + 1:04d}", box.tolist()) for index, box in enumerate(output.boxes)]
    assert returned[1][0] != returned[3][0] and returned[1][1] == returned[3][1]
    assert output.boxes.dtype == np.float32


@pytest.mark.parametrize("default_reason", ["preprocess_none", "empty_postprocess"])
def test_detector_default_output_does_not_identify_whether_session_ran(upstream, default_reason):
    np, module = upstream.np, upstream.detector
    detector = module.TextDetector.__new__(module.TextDetector)
    calls = []

    def factory(_):
        return lambda image: None if default_reason == "preprocess_none" else image

    def session(image):
        calls.append(image.shape)
        return object()

    def postprocess(prediction, shape):
        return np.empty((0, 4, 2), dtype=np.float32), []

    detector.get_preprocess, detector.session, detector.postprocess_op = factory, session, postprocess
    result = detector(np.zeros((8, 8, 3), dtype=np.uint8))
    assert result.boxes is result.scores is result.img is None and result.elapse == 0.0
    assert len(result) == 0
    assert len(calls) == int(default_reason == "empty_postprocess")


@pytest.mark.parametrize("container", [list, tuple])
@pytest.mark.parametrize("index_kind", ["list", "tuple", "array", "empty"])
def test_filter_sequence_data_is_always_a_list_in_requested_order(upstream, container, index_kind):
    np = upstream.np
    data = container(["first", "second", "third"])
    indices = {"list": [2, 0, 2], "tuple": (2, 0, 2),
               "array": np.array([2, 0, 2], dtype=np.int64), "empty": []}[index_kind]
    result = upstream.utils.filter_by_indices(data, indices)
    assert type(result) is list
    assert result == ([] if index_kind == "empty" else ["third", "first", "third"])
    assert list(data) == ["first", "second", "third"]


@pytest.mark.parametrize("index_kind", ["list", "array", "empty_list", "empty_array"])
def test_filter_ndarray_row_indices_preserve_dtype_and_trailing_shape(upstream, index_kind):
    np = upstream.np
    data = np.arange(6, dtype=np.int16).reshape(3, 2)
    frozen = data.copy()
    indices = {"list": [2, 0, 2], "array": np.array([2, 0, 2], dtype=np.int64),
               "empty_list": [], "empty_array": np.array([], dtype=np.int64)}[index_kind]
    output = upstream.utils.filter_by_indices(data, indices)
    assert output.dtype == data.dtype
    np.testing.assert_array_equal(output, data[[2, 0, 2]] if not index_kind.startswith("empty") else data[:0])
    assert output.shape == ((0, 2) if index_kind.startswith("empty") else (3, 2))
    assert not np.shares_memory(output, data)
    np.testing.assert_array_equal(data, frozen)


def test_filter_ndarray_tuple_is_multiaxis_not_a_row_selection(upstream):
    np = upstream.np
    data = np.arange(6, dtype=np.int16).reshape(3, 2)
    value = upstream.utils.filter_by_indices(data, (2, 0))
    assert type(value) is np.int16 and value == 4
    unchanged_view = upstream.utils.filter_by_indices(data, ())
    np.testing.assert_array_equal(unchanged_view, data)
    assert unchanged_view.shape == (3, 2) and np.shares_memory(unchanged_view, data)


def test_filter_none_index_is_new_axis_for_array_but_rejected_for_sequence(upstream):
    np = upstream.np
    data = np.arange(6, dtype=np.int16).reshape(3, 2)
    output = upstream.utils.filter_by_indices(data, None)
    assert output.shape == (1, 3, 2) and output.dtype == np.int16
    assert np.shares_memory(output, data)
    for sequence in (list(range(3)), tuple(range(3))):
        with pytest.raises(TypeError):
            upstream.utils.filter_by_indices(sequence, None)


@pytest.mark.parametrize("indices", [None, [], (), [0]])
def test_filter_none_data_is_never_an_empty_success(upstream, indices):
    with pytest.raises(TypeError):
        upstream.utils.filter_by_indices(None, indices)


def test_filter_boolean_indices_differ_between_array_and_sequence(upstream):
    np = upstream.np
    mask = [True, False, True]
    np.testing.assert_array_equal(upstream.utils.filter_by_indices(np.array([10, 20, 30]), mask), [10, 30])
    assert upstream.utils.filter_by_indices([10, 20, 30], mask) == [20, 10, 20]


def test_output_defaults_are_not_component_execution_or_confidence_evidence(upstream):
    main = upstream.main
    det, cls, rec, ocr = main.TextDetOutput(), main.TextClsOutput(), main.TextRecOutput(), main.RapidOCROutput()
    assert {field.name: getattr(det, field.name) for field in fields(det)} == {
        "img": None, "boxes": None, "scores": None, "elapse": 0.0}
    assert {field.name: getattr(cls, field.name) for field in fields(cls)} == {
        "img_list": None, "cls_res": None, "elapse": None}
    assert rec.imgs is rec.txts is rec.elapse is rec.viser is None
    assert rec.scores == [1.0] and rec.word_results == (("", 1.0, None),)
    assert ocr.img is ocr.boxes is ocr.txts is ocr.scores is ocr.viser is None
    assert ocr.elapse_list == [] and ocr.elapse == 0
    assert ocr.word_results == (("", 1.0, None),)
    assert [len(value) for value in (det, cls, rec, ocr)] == [0, 0, 0, 0]
    rec.scores.append(0.25)
    assert main.TextRecOutput().scores == [1.0]


@pytest.mark.parametrize("dtype", ["float32", "float64"])
def test_remap_mutates_before_clipping_then_returns_dtype_preserving_copy(upstream, dtype):
    np = upstream.np
    boxes = np.array([[[-2, -3], [2, -3], [12, 13], [-2, 13]]], dtype=getattr(np, dtype))
    record = {"preprocess": {"ratio_h": 2.0, "ratio_w": 3.0}, "padding_1": {"top": 1, "left": 2}}
    output = upstream.process.map_boxes_to_original(boxes, record, 20, 25)
    np.testing.assert_array_equal(boxes, [[[-12, -8], [0, -8], [30, 24], [-12, 24]]])
    np.testing.assert_array_equal(output, [[[0, 0], [0, 0], [25, 20], [0, 20]]])
    assert output.dtype == boxes.dtype and output.shape == boxes.shape == (1, 4, 2)
    assert not np.shares_memory(output, boxes)
    # Clipped/collapsed vertices and cardinality are retained; not a proof of
    # physical support or a license to discard the detection identity.
    assert np.array_equal(output[0, 0], output[0, 1])


def test_remap_uses_reverse_record_insertion_order_not_sorted_operation_names(upstream):
    np = upstream.np
    boxes = np.array([_box(4, 5)], dtype=np.float32)
    padding = {"top": 1, "left": 2}
    preprocess = {"ratio_h": 2.0, "ratio_w": 3.0}
    normal = upstream.process.map_boxes_to_original(
        boxes.copy(), {"preprocess": preprocess, "padding_1": padding}, 50, 50)
    reversed_record = upstream.process.map_boxes_to_original(
        boxes.copy(), {"padding_1": padding, "preprocess": preprocess}, 50, 50)
    np.testing.assert_array_equal(normal[0, 0], [6, 8])
    np.testing.assert_array_equal(reversed_record[0, 0], [10, 9])


def test_float32_remap_rounds_each_inplace_operation_not_only_final_serialization(upstream):
    np = upstream.np
    boxes = np.array([_box(1.5, 1.5)], dtype=np.float32)
    # A deliberately tiny fractional offset is a pure arithmetic control, not
    # a claim that a real padding record contains fractional offsets.
    record = {"preprocess": {"ratio_h": 1.25, "ratio_w": 1.25},
              "padding_1": {"top": 2 ** -24, "left": 2 ** -24}}
    actual32 = upstream.process.map_boxes_to_original(boxes.copy(), record, 20, 20)
    actual64 = upstream.process.map_boxes_to_original(boxes.astype(np.float64), record, 20, 20)
    assert actual32.dtype == np.float32 and actual64.dtype == np.float64
    assert float(actual32[0, 0, 0]) == 1.875
    assert float(actual64[0, 0, 0]) == (1.5 - 2 ** -24) * 1.25
    assert actual32[0, 0, 0] != actual64.astype(np.float32)[0, 0, 0]


@pytest.fixture
def formatter(upstream, monkeypatch):
    np, main = upstream.np, upstream.main
    engine = main.RapidOCR.__new__(main.RapidOCR)
    engine.text_score, engine.return_word_box = 0.5, False
    engine.cfg = NS(Global=NS(text_score=0.5, font_path=None), Rec=NS(lang_type="en"))
    events, indices, crops_seen = [], [], []
    visualizer = object()
    monkeypatch.setattr(main, "VisRes", lambda **options: visualizer)

    def remap_crops(crops, ratio_h, ratio_w):
        assert ratio_h == ratio_w == 1.0
        crops_seen.append(len(crops))
        return crops

    monkeypatch.setattr(main, "map_img_to_original", remap_crops)
    original_indices = main.filter_by_indices

    def indexed(data, selected):
        indices.append(tuple(selected))
        return original_indices(data, selected)

    monkeypatch.setattr(main, "filter_by_indices", indexed)

    def freeze(output):
        return {"boxes": output.boxes.copy(), "txts": tuple(output.txts), "scores": tuple(output.scores),
                "word_results": output.word_results}

    def filtered(output):
        before = freeze(output)
        result = main.RapidOCR.filter_by_text_score(engine, output)
        events.append({"before": before, "after": freeze(result), "same_object": result is output})
        return result

    engine.filter_by_text_score = filtered

    def execute(texts, scores, *, detector_scores=True):
        count = len(texts)
        crops = [np.zeros((2, 2, 3), dtype=np.uint8) for _ in texts]
        boxes = np.array([_box(1 + 3 * index, 1) for index in range(count)], dtype=np.float32)
        det = main.TextDetOutput(boxes=boxes, scores=[0.9] * count if detector_scores else None)
        cls = main.TextClsOutput()
        rec = main.TextRecOutput(imgs=crops, txts=tuple(texts), scores=list(scores), word_results=[None] * count)
        result = engine.build_final_output(np.zeros((8, 32, 3), dtype=np.uint8), det, cls, rec,
                                          crops, {"preprocess": {"ratio_h": 1.0, "ratio_w": 1.0}})
        return NS(result=result, det=det, cls=cls, rec=rec, original_boxes=boxes)

    return NS(engine=engine, execute=execute, events=events, indices=indices, crops_seen=crops_seen)


@pytest.mark.parametrize("branch", ["all_blank", "all_score_filtered"])
def test_distinct_completed_formatter_paths_converge_on_default_output(upstream, formatter, branch):
    np = upstream.np
    texts, scores = (["", " \t", "\u2003"], [0.9, 0.8, 0.7]) if branch == "all_blank" else (["first", "second"], [0.1, 0.49])
    observed = formatter.execute(texts, scores)
    assert formatter.crops_seen == [len(texts)]
    assert formatter.indices == ([()] * 7 if branch == "all_blank" else [(0, 1)] * 7)
    assert len(formatter.events) == 1 and formatter.events[0]["same_object"]
    event = formatter.events[0]
    assert event["before"]["boxes"].shape == ((0, 4, 2) if branch == "all_blank" else (2, 4, 2))
    assert event["before"]["boxes"].dtype == np.float32
    assert event["before"]["txts"] == (() if branch == "all_blank" else tuple(texts))
    assert event["after"]["boxes"].shape == (0,) and event["after"]["boxes"].dtype == np.float64
    assert event["after"]["txts"] == event["after"]["scores"] == event["after"]["word_results"] == ()
    result = observed.result
    assert result.boxes is result.txts is result.scores is result.img is result.viser is None
    assert result.elapse_list == [] and result.word_results == (("", 1.0, None),) and len(result) == 0


def test_blank_then_score_filter_retains_equal_threshold_and_duplicate_text_positions(upstream, formatter):
    np = upstream.np
    observed = formatter.execute(["", "\u2003", " same ", "same", "same"], [1.0, 1.0, 0.49, 0.5, 0.75])
    assert formatter.indices == [(2, 3, 4)] * 7
    assert formatter.events[0]["before"]["txts"] == (" same ", "same", "same")
    assert observed.result.txts == ("same", "same") and observed.result.scores == (0.5, 0.75)
    np.testing.assert_array_equal(observed.result.boxes, observed.original_boxes[[3, 4]])
    assert len(observed.result) == 2 and observed.result.boxes.dtype == np.float32
    # Falsy word metadata is not retained one-for-one when word boxes are off.
    assert observed.result.word_results == ()


def test_surrounding_whitespace_survives_at_and_above_score_threshold(upstream, formatter):
    texts = (" \tkept\u00a0 ", "\u2003also kept\n")
    observed = formatter.execute(texts, [0.5, 0.75])
    assert formatter.indices == [(0, 1)] * 7
    assert formatter.events[0]["before"]["txts"] == texts
    assert formatter.events[0]["after"]["txts"] == texts
    assert observed.result.txts == texts
    assert observed.result.scores == (0.5, 0.75)
    assert all(text != text.strip() for text in observed.result.txts)


def test_composed_formatter_maps_mutated_boxes_before_blank_and_score_filters(
        upstream, formatter, monkeypatch):
    np, main = upstream.np, upstream.main
    boxes = np.array([_box(1, 1), _box(3, 4), _box(6, 4)], dtype=np.float32)
    frozen_detector_boxes = boxes.copy()
    crops = [np.zeros((2, 2, 3), dtype=np.uint8) for _ in range(3)]
    det = main.TextDetOutput(boxes=boxes, scores=[0.9, 0.8, 0.7])
    rec = main.TextRecOutput(imgs=crops, txts=("", " kept ", "discard"),
                             scores=[0.9, 0.5, 0.49], word_results=[None] * 3)
    resize_requests = []

    def inert_crop_remap(images, ratio_h, ratio_w):
        # Observe the actual formatter request without resizing crop images.
        assert images is crops
        resize_requests.append((len(images), ratio_h, ratio_w))
        return images

    monkeypatch.setattr(main, "map_img_to_original", inert_crop_remap)
    result = formatter.engine.build_final_output(
        np.zeros((24, 48, 3), dtype=np.uint8), det, main.TextClsOutput(), rec, crops,
        {"preprocess": {"ratio_h": 2.0, "ratio_w": 3.0},
         "padding_1": {"top": 2, "left": 0}})
    # The real map mutates the original detector array before creating its
    # clipped return; later filters rebind fields rather than undo that mutation.
    expected_mutated = np.array([
        [[3, -2], [9, -2], [9, 2], [3, 2]],
        [[9, 4], [15, 4], [15, 8], [9, 8]],
        [[18, 4], [24, 4], [24, 8], [18, 8]],
    ], dtype=np.float32)
    np.testing.assert_array_equal(frozen_detector_boxes, [_box(1, 1), _box(3, 4), _box(6, 4)])
    np.testing.assert_array_equal(boxes, expected_mutated)
    assert resize_requests == [(3, 2.0, 3.0)]
    assert formatter.indices == [(1, 2)] * 7
    assert len(formatter.events) == 1 and formatter.events[0]["same_object"]
    event = formatter.events[0]
    np.testing.assert_array_equal(event["before"]["boxes"], expected_mutated[[1, 2]])
    assert event["before"]["txts"] == (" kept ", "discard")
    np.testing.assert_array_equal(det.boxes, expected_mutated[[1, 2]])
    np.testing.assert_array_equal(event["after"]["boxes"], expected_mutated[[1]])
    np.testing.assert_array_equal(result.boxes, expected_mutated[[1]])
    assert result.txts == (" kept ",) and result.scores == (0.5,)
    assert boxes.dtype == det.boxes.dtype == result.boxes.dtype == np.float32
    assert not np.shares_memory(boxes, frozen_detector_boxes)
    assert not np.shares_memory(boxes, det.boxes)
    assert not np.shares_memory(boxes, result.boxes)


def test_zero_width_space_is_not_removed_by_upstream_strip(upstream, formatter):
    observed = formatter.execute(["\u200b", " \t\u00a0"], [0.5, 1.0])
    assert formatter.indices == [(0,)] * 7
    assert observed.result.txts == ("\u200b",)


def test_missing_detector_scores_skips_blank_removal_not_empty_recognition(upstream, formatter):
    observed = formatter.execute([""], [0.5], detector_scores=False)
    assert formatter.indices == []
    assert len(formatter.events) == 1
    assert formatter.events[0]["before"]["txts"] == ("",)
    assert observed.result.txts == ("",) and len(observed.result) == 1


def test_default_outputs_skip_filter_branch_entirely(upstream, formatter):
    main, np = upstream.main, upstream.np
    result = formatter.engine.build_final_output(
        np.zeros((8, 8, 3), dtype=np.uint8), main.TextDetOutput(), main.TextClsOutput(),
        main.TextRecOutput(), [], {"preprocess": {"ratio_h": 1.0, "ratio_w": 1.0}})
    assert result.boxes is result.txts is result.scores is None
    assert formatter.indices == formatter.events == formatter.crops_seen == []


def test_default_recognition_word_fields_with_two_texts_raise_not_empty_success(upstream, formatter):
    main, np = upstream.main, upstream.np
    crops = [np.zeros((2, 2, 3), dtype=np.uint8) for _ in range(2)]
    det = main.TextDetOutput(boxes=np.array([_box(1, 1), _box(4, 1)], dtype=np.float32), scores=[0.9, 0.9])
    rec = main.TextRecOutput(imgs=crops, txts=("one", "two"))  # Defaults have one score/word entry.
    with pytest.raises(IndexError):
        formatter.engine.build_final_output(
            np.zeros((8, 8, 3), dtype=np.uint8), det, main.TextClsOutput(), rec, crops,
            {"preprocess": {"ratio_h": 1.0, "ratio_w": 1.0}})
    assert formatter.events == []


def test_score_filter_zip_silently_shortens_mismatched_arrays(upstream, formatter):
    main, np = upstream.main, upstream.np
    output = main.RapidOCROutput(
        boxes=np.array([_box(1, 1), _box(4, 1)], dtype=np.float32), txts=("first", "second"),
        scores=(0.9,), word_results=(None, None))
    result = main.RapidOCR.filter_by_text_score(formatter.engine, output)
    assert result is output and result.txts == ("first",) and result.scores == (0.9,)
    assert len(result.boxes) == 1
