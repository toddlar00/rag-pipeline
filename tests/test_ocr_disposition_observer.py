"""Bounded inward observer controls; no model, PDF or native OCR execution."""
from __future__ import annotations

import copy
import hashlib
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

import ocr_disposition_observer as observer
from ocr_detection_disposition_runtime import DispositionRecorder


def _frame():
    binding = observer.DispatchBinding("a" * 64)
    reader = NS(min_score=0.)
    frame = binding.begin("page-00001", reader, 0.)
    return binding, reader, frame


def test_dependency_light_inward_import_has_no_outward_or_native_edges():
    code = "import ocr_disposition_observer,sys; assert not ({'numpy','rapidocr','cv2','ocr_engine_guard','ocr_execution_receipt','ocr_detection_disposition_runtime','ocr_detection_disposition'} & sys.modules.keys())"
    result = subprocess.run([sys.executable, "-B", "-c", code], cwd=Path(__file__).resolve().parents[1], capture_output=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("operation", [None, True, 1, "page", "", [], "pages\n"])
def test_closed_operation_admission(operation):
    with pytest.raises(ValueError):
        DispositionRecorder(operation, "a" * 64)


@pytest.mark.parametrize("digest", [None, True, 0, "", "A" * 64, "a" * 63, "a" * 65, "a" * 64 + "\n"])
def test_strict_request_digest(digest):
    with pytest.raises(ValueError):
        DispositionRecorder("pages", digest)


@pytest.mark.parametrize("kind,limit", [("detections", "detections_per_call"), ("codepoints", "codepoints_per_call"),
                                      ("raster_bytes", "raster_bytes_per_call")])
def test_work_precharged_before_copy_and_no_refund_on_disable(kind, limit):
    binding, _, frame = _frame()
    cap = observer._CAPS[kind][0]
    field = observer._FIELDS[kind]
    frame.charge(kind, cap)
    assert binding.totals[kind] == frame.work[field] == cap
    with pytest.raises(observer._BudgetExceeded):
        frame.charge(kind, 1)
    assert frame.work["exhausted"] == {"limit": limit, "used_before": cap, "requested_units": 1}
    frame.disable("budget_exhausted")
    assert binding.totals[kind] == frame.work[field] == cap


@pytest.mark.parametrize("kind", ["detections", "codepoints", "raster_bytes"])
def test_total_budget_consumed_in_actual_capture_order(kind):
    binding, _, frame = _frame()
    total = observer._CAPS[kind][1]
    binding.totals[kind] = total - 1
    frame.charge(kind, 1)
    with pytest.raises(observer._BudgetExceeded):
        frame.charge(kind, 1)
    assert frame.work["exhausted"] == {"limit": kind + "_total", "used_before": total, "requested_units": 1}
    assert frame.work[observer._FIELDS[kind]] == 1


def test_oversized_detection_count_stops_before_array_copy():
    np = pytest.importorskip("numpy")
    _, _, frame = _frame()
    # This ndarray is only64KiB and must never be detached after admission fails.
    frame.emit("detector", NS(boxes=np.zeros((1001, 4, 2), np.float64)))
    assert frame.disabled and frame.reason == "budget_exhausted"
    assert frame._detached_boxes is None and frame.detections == []
    assert frame.work["detections_reserved"] == 0
    assert frame.work["exhausted"] == {"limit": "detections_per_call", "used_before": 0, "requested_units": 1001}


def test_single_text_cap_precedes_utf8_encoding():
    _, _, frame = _frame()
    with pytest.raises(observer._BudgetExceeded):
        frame._text("\ud800" * 4097, .5)
    assert frame.work["codepoints_charged"] == 0
    assert frame.work["exhausted"]["limit"] == "codepoints_per_text"


@pytest.mark.parametrize("kind", ["reader", "owner", "ordinal", "repeat", "closed"])
def test_foreign_repeated_or_expired_ticket_is_never_committed(kind):
    binding, reader, frame = _frame()
    owner, guard = object(), object()
    if kind == "closed":
        binding.end(frame)
    if kind == "owner":
        binding.receipt_owner = object()
    if kind == "repeat":
        binding.reserve(owner, reader, guard, 1)
    ticket = binding.reserve(owner, object() if kind == "reader" else reader, guard, True if kind == "ordinal" else 1)
    assert ticket is None and frame.ticket.raw_status is None
    assert frame.snapshot()["dispatch"] is None


def test_committed_ticket_snapshot_is_bound_to_attempt_not_output_content():
    binding, reader, frame = _frame()
    ticket = binding.reserve(object(), reader, object(), 1)
    frame.disable("unsupported_recipe")
    ticket.raw_status = "completed"  # fixed recorder seam simulated, no OCR claim
    binding.end(frame)
    value = frame.snapshot()
    assert value["dispatch"]["ticket_sha256"] == observer.canonical_sha256({
        "request_sha256": "a" * 64, "item_id": "page-00001", "attempt_index": 1,
        "call_id": "call-0001", "dispatch_ordinal": 1})
    assert value["diagnostic_state"] == "unavailable" and value["detection_count"] is None
    assert frame.reader is None and ticket.guard is None


@pytest.mark.parametrize("mutation", ["deep_bbox", "bool_page", "extra_row", "duplicate_id", "reverse", "many", "nonfinite"])
def test_bounded_fixed_region_selectors(mutation):
    row = {"region_id": "a", "page_number": 1, "bbox": [0., 0., 1., 1.]}
    rows = [row]
    if mutation == "deep_bbox":
        row["bbox"][0] = {"nested": {"not": "a number"}}
    elif mutation == "bool_page":
        row["page_number"] = True
    elif mutation == "extra_row":
        row["hidden"] = {"unbounded": [None] * 10}
    elif mutation == "duplicate_id":
        rows.append(copy.deepcopy(row))
    elif mutation == "reverse":
        rows = [{**row, "region_id": "b"}, row]
    elif mutation == "many":
        rows = [{**row, "region_id": str(i)} for i in range(21)]
    else:
        row["bbox"][0] = float("nan")
    with pytest.raises(ValueError):
        DispositionRecorder("regions", "a" * 64, {"regions": rows})


def test_installed_recipe_requires_all_fixed_file_bytes(monkeypatch):
    class Distribution:
        version = "3.9.2"
        def locate_file(self, path):
            assert path in {"rapidocr/" + name for name in observer._PINS}
            class File:
                def open(self, mode):
                    import io
                    return io.BytesIO(b"unexpected installed source")
            return File()
    monkeypatch.setattr(observer.importlib.metadata, "distribution", lambda package: Distribution())
    monkeypatch.setattr(observer.importlib.metadata, "version", lambda package: "2.5.2")
    assert observer.verified_recipe() is False


@pytest.mark.parametrize("dtype", ["float32", "float64"])
def test_dtype_faithful_nonidentity_remap_matches_actual_pinned_helper(dtype):
    np = pytest.importorskip("numpy")
    pytest.importorskip("rapidocr")
    from rapidocr.utils.process_img import map_boxes_to_original
    assert observer.verified_recipe()
    _, _, frame = _frame()
    original = np.array([[[1., 1.], [21., 1.], [21., 20.], [1., 20.]]], dtype=dtype)
    frame.raster = {"width": 63, "height": 19, "global_width": 96, "global_height": 32,
        "padded_width": 96, "padded_height": 60, "ratio_w": 63/96, "ratio_h": 19/32,
        "padding_top": 14, "padding_left": 0, "operations": None, "detector_box_dtype": dtype,
        "coordinate_system": "engine_input_bgr_uint8_pixels", "pixel_sha256": "a" * 64}
    frame.settings = {"engine_text_score": 0., "reader_min_score": 0., "classification_threshold": .9}
    frame.emit("detector", NS(boxes=original, scores=[.9]))
    for name in ("crops", "classifier", "recognizer"):
        frame.stages[name].update(state="completed", input_count=1, output_count=1)
    frame.detections[0]["recognition"] = {"state": "completed", "text_kind": "nonempty",
        "text_sha256": hashlib.sha256(b"x").hexdigest(), "codepoints": 1, "score": .7}
    record = {"preprocess": {"ratio_h": 19/32, "ratio_w": 63/96}, "padding_1": {"top": 14, "left": 0}}
    before = original.copy()
    frame.emit("formatter_entry", NS(boxes=original, scores=[.9]), NS(txts=("x",), scores=[.7]), record)
    assert not frame.disabled
    actual = map_boxes_to_original(original.copy(), record, 19, 63)
    assert frame.detections[0]["engine_input_box"] == actual[0].tolist()
    assert np.array_equal(original, before)  # diagnostic never remaps live boxes


def test_reversed_operation_order_disables_instead_of_reordering_it():
    _, _, frame = _frame()
    frame.raster = {"ratio_h": 1., "ratio_w": 1., "padding_top": 0}
    frame.emit("formatter_entry", NS(), NS(), {"padding_1": {"top": 0, "left": 0},
                                               "preprocess": {"ratio_h": 1., "ratio_w": 1.}})
    assert frame.disabled and frame.reason == "lineage_invalid"


@pytest.mark.parametrize("box", [
    [[1., 1.], [2., 2.], [1., 2.], [2., 1.]],
    [[1., 1.], [2., 2.], [3., 3.], [4., 4.]],
    [[1., 1.], [1., 1.], [1., 1.], [1., 1.]],
])
def test_zero_signed_area_detector_geometry_disables_instead_of_surviving_to_leaf(box):
    np = pytest.importorskip("numpy")
    _, _, frame = _frame()
    frame.raster = {"padded_width": 8, "padded_height": 8, "detector_box_dtype": None}
    frame.emit("detector", NS(boxes=np.array([box], np.float32)))
    assert frame.disabled and frame.reason == "lineage_invalid"
    assert frame.detection_count is None and frame.detections == []
