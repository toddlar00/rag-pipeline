"""Tiny pinned arithmetic and synthetic geometry; no model/PDF/OCR work."""
from __future__ import annotations

import copy
import hashlib
import importlib
import importlib.metadata
import json
from pathlib import Path
import struct
import subprocess
import sys

import pytest

import ocr_disposition_geometry as geometry


def raster(*, dtype="float32", width=100, height=80, gw=96, gh=96, top=10):
    rh, rw = height / gh, width / gw
    return {"width": width, "height": height, "pixel_sha256": "a" * 64,
            "coordinate_system": "engine_input_bgr_uint8_pixels", "global_width": gw,
            "global_height": gh, "padded_width": gw, "padded_height": gh + 2 * top,
            "ratio_h": rh, "ratio_w": rw, "padding_top": top, "padding_left": 0,
            "operations": [{"kind": "preprocess", "ratio_h": rh, "ratio_w": rw},
                           {"kind": "padding_1", "top": top, "left": 0}],
            "detector_box_dtype": dtype}


def quad(x=1.0, y=1.0, width=2.0, height=2.0):
    return [[x, y], [x + width, y], [x + width, y + height], [x, y + height]]


def region_geometry(*, rotation=0, offset=0, width=100, height=80):
    page_width, page_height = 24.0, 19.2
    cw, ch = (page_height, page_width) if rotation in (90, 270) else (page_width, page_height)
    scale = 300 / 72
    return {"page": {"display_rect_points": [0., 0., page_width, page_height],
                     "cropbox_points": [11., 13., 11. + cw, 13. + ch], "rotation_degrees": rotation},
            "raster": {"width": width, "height": height, "dpi": 300,
                       "coordinate_system": "rendered_image_pixels"},
            "pixel_bounds": [offset, offset, width + offset, height + offset],
            "crop_to_page_fraction": [[1 / (scale * page_width), 0., (offset / scale) / page_width],
                                      [0., 1 / (scale * page_height), (offset / scale) / page_height]],
            "boundary_policy": "clip_to_page_bounds"}


def preprocessing(angle=1.0):
    import ocr_preprocessing as policy

    ow, oh, forward, inverse = policy._rotation_geometry(100, 80, angle)
    return {"schema_version": 1, "algorithm": "bounded-deskew-contrast-v1", "mode": "deskew",
            "original_raster": {"width": 100, "height": 80},
            "processed_raster": {"width": ow, "height": oh},
            "source_to_processed": forward, "processed_to_source": inverse,
            "deskew": {"status": "applied", "reason": "rotation_applied", "angle_degrees": angle,
                       "estimated_angle_degrees": angle, "gain": 0.05},
            "contrast": {"status": "disabled", "reason": "mode_disabled", "low_level": None, "high_level": None},
            "parameters": dict(policy._PARAMETERS), "libraries": {"opencv": "synthetic", "numpy": "synthetic"}}


@pytest.fixture
def numpy_reference():
    np = pytest.importorskip("numpy")
    pytest.importorskip("rapidocr")
    assert np.__version__ == "2.5.2"
    assert importlib.metadata.version("rapidocr") == "3.9.2"
    module = importlib.import_module("rapidocr.utils.process_img")
    path = Path(module.__file__)
    with path.open("rb") as stream:
        raw = stream.read(1024 * 1024 + 1)
    assert len(raw) <= 1024 * 1024
    assert hashlib.sha256(raw).hexdigest() == "abaf2ed615878f618a372cba6157bc6c41a494e05f6c41bf9f7c5c2ba1ee5772"
    return np, module.map_boxes_to_original


@pytest.mark.parametrize("dtype", ["float32", "float64"])
@pytest.mark.parametrize("dimensions", [(1800, 2400, 1800, 2400, 0), (5990, 736, 5990, 736, 380),
                                       (100, 80, 96, 96, 10), (99, 101, 288, 288, 0),
                                       (4095, 4095, 4096, 4096, 1), (12000, 8000, 32, 32, 0)])
def test_replay_matches_pinned_numpy_bit_patterns(numpy_reference, dtype, dimensions):
    np, reference = numpy_reference
    width, height, gw, gh, top = dimensions
    facts = raster(dtype=dtype, width=width, height=height, gw=gw, gh=gh, top=top)
    points = np.array([[[-0.0, 0.0], [gw / 3, top + gh / 7],
                        [gw, gh + 2 * top], [gw - 0.25, 0.1]]], dtype=dtype)
    source = points.tolist()[0]
    snapshot = copy.deepcopy((source, facts))
    operations = {operation["kind"]: {key: value for key, value in operation.items() if key != "kind"}
                  for operation in facts["operations"]}
    expected = reference(points.copy(), operations, height, width)
    actual = np.asarray([geometry.replay_engine_box(dtype=dtype, box=source, raster=facts)], dtype=dtype)
    assert actual.dtype == expected.dtype and actual.tobytes() == expected.tobytes()
    assert (source, facts) == snapshot


def test_float32_scalar_cast_is_not_float64_multiply_then_serialization(numpy_reference):
    np, reference = numpy_reference
    facts = raster(top=0)
    found = []
    for value in np.linspace(0, 96, 257, dtype=np.float32):
        points = np.array([quad(float(value), 1.0, 0.0, 1.0)], dtype=np.float32)
        expected = reference(points.copy(), {"preprocess": {"ratio_h": facts["ratio_h"], "ratio_w": facts["ratio_w"]},
                                             "padding_1": {"top": 0, "left": 0}}, 80, 100)
        actual = geometry.replay_engine_box(dtype="float32", box=points.tolist()[0], raster=facts)
        assert np.asarray([actual], dtype=np.float32).tobytes() == expected.tobytes()
        naive = struct.unpack(">f", struct.pack(">f", float(value) * facts["ratio_w"]))[0]
        if actual[0][0] != naive:
            found.append(float(value))
    assert found, "control must expose float32 scalar conversion before multiplication"


@pytest.mark.parametrize("bits", [0x00000000, 0x80000000, 0x00000001, 0x007FFFFF, 0x00800000,
                                  0x3F800001, 0x3FFFFFFF, 0x40490FDB, 0x42BFFFFF])
def test_small_values_and_rounding_boundaries_match_numpy(numpy_reference, bits):
    np, reference = numpy_reference
    value = struct.unpack(">f", struct.pack(">I", bits))[0]
    facts = raster(top=0)
    points = np.array([quad(value, value, 0.0, 0.0)], dtype=np.float32)
    expected = reference(points.copy(), {"preprocess": {"ratio_h": facts["ratio_h"], "ratio_w": facts["ratio_w"]},
                                        "padding_1": {"top": 0, "left": 0}}, 80, 100)
    actual = geometry.replay_engine_box(dtype="float32", box=points.tolist()[0], raster=facts)
    assert np.asarray([actual], dtype=np.float32).tobytes() == expected.tobytes()


def test_duplicate_boxes_and_padding_collapse_are_preserved():
    facts = raster()
    box = quad(0, 0, 0, 1)
    result = geometry.replay_engine_box(dtype="float32", box=box, raster=facts)
    assert result == [[0.0, 0.0]] * 4
    assert geometry.replay_engine_box(dtype="float32", box=box, raster=facts) == result
    assert geometry.map_source_polygon(engine_box=result, mapping={"kind": "page", "raster": {"width": 100, "height": 80}})["reason"] == "no_positive_source_support"


@pytest.mark.parametrize("change", ["order", "partial", "null", "bool", "ratio", "padding", "dtype", "digest", "unknown"])
def test_replay_rejects_unobserved_hybrid_or_mismatched_raster(change):
    facts = raster()
    if change == "order":
        facts["operations"].reverse()
    elif change == "partial":
        facts["operations"].pop()
    elif change == "null":
        facts["operations"] = None
    elif change == "bool":
        facts["operations"][1]["left"] = False
    elif change == "ratio":
        facts["operations"][0]["ratio_w"] += 0.001
    elif change == "padding":
        facts["padding_top"] += 1
    elif change == "dtype":
        facts["detector_box_dtype"] = "float64"
    elif change == "digest":
        facts["pixel_sha256"] = "not-a-digest"
    else:
        facts["extra"] = 1
    with pytest.raises(geometry.DispositionGeometryError, match="disposition geometry is invalid"):
        geometry.replay_engine_box(dtype="float32", box=quad(), raster=facts)


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), -(10 ** 1000), 10 ** 1000, -1, 12001])
def test_invalid_coordinates_fail_statically(value):
    box = quad()
    box[0][0] = value
    with pytest.raises(geometry.DispositionGeometryError):
        geometry.replay_engine_box(dtype="float32", box=box, raster=raster())
    with pytest.raises(geometry.DispositionGeometryError):
        geometry.map_source_polygon(engine_box=box, mapping={"kind": "page", "raster": {"width": 100, "height": 80}})


def test_float32_input_is_not_silently_rounded_and_no_iteration_of_arbitrary_values():
    with pytest.raises(geometry.DispositionGeometryError):
        geometry.replay_engine_box(dtype="float32", box=quad(0.1), raster=raster())

    class Forbidden:
        def __iter__(self):
            raise AssertionError("arbitrary iterator must not run")

    with pytest.raises(geometry.DispositionGeometryError):
        geometry.map_source_polygon(engine_box=Forbidden(), mapping=None)


def test_null_states_remain_explicit():
    assert geometry.map_source_polygon(engine_box=None, mapping=None) == {
        "state": "unavailable", "reason": "engine_mapping_unobserved", "polygon": None,
        "coordinate_system": "original_page_display_fraction", "edge_model": None, "max_error_source_pixels": None}
    assert geometry.map_source_polygon(engine_box=quad(), mapping=None)["reason"] == "report_geometry_unavailable"


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_region_rotation_and_nonzero_cropbox_do_not_double_rotate_display_mapping(rotation):
    descriptor = {"kind": "region", "geometry": region_geometry(rotation=rotation)}
    frozen = copy.deepcopy(descriptor)
    result = geometry.map_source_polygon(engine_box=quad(0, 0, 100, 80), mapping=descriptor)
    assert result["state"] == "available" and result["edge_model"] == "linear"
    corners = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]
    # Boundary intersection can rotate the first vertex, never its winding.
    assert result["polygon"] in [corners[index:] + corners[:index] for index in range(4)]
    assert geometry.map_source_polygon(engine_box=quad(0, 0, 100, 80), mapping=descriptor) == result
    assert descriptor == frozen


def test_true_affine_intersection_can_be_a_triangle_not_clamped_quad():
    descriptor = {"kind": "region", "geometry": region_geometry(offset=-1)}
    result = geometry.map_source_polygon(engine_box=[[0, 0], [2, 0], [2, 0.5], [0, 2]], mapping=descriptor)
    assert result["state"] == "available" and len(result["polygon"]) == 3
    assert max(point[0] for point in result["polygon"]) == pytest.approx(1 / 300)
    assert max(point[1] for point in result["polygon"]) == pytest.approx(1 / 320)
    assert result["max_error_source_pixels"] == 0.0


def test_clamping_would_invent_support_for_disjoint_polygon_beyond_source_corner():
    descriptor = {"kind": "region", "geometry": region_geometry(offset=-1)}
    result = geometry.map_source_polygon(engine_box=[[0.6, 0.6], [1.4, 0.1], [1.8, 0.6], [0.1, 1.4]], mapping=descriptor)
    assert result["reason"] == "no_positive_source_support" and result["polygon"] is None


def test_exact_affine_tangency_does_not_gain_area_from_intermediate_rounding():
    projected = region_geometry(offset=-1, height=100)
    projected["page"]["display_rect_points"] = [0., 0., 24., 24.]
    projected["page"]["cropbox_points"] = [11., 13., 35., 37.]
    projected["crop_to_page_fraction"][1] = [0., 0.01, -0.01]
    result = geometry.map_source_polygon(engine_box=[[0.5, 0.5], [1.5, 0], [2, 0.5], [0, 1.5]],
                                         mapping={"kind": "region", "geometry": projected})
    assert result["reason"] == "no_positive_source_support" and result["polygon"] is None


def test_preprocessed_expanded_canvas_is_intersected_with_original_raster():
    metadata = preprocessing()
    result = geometry.map_source_polygon(engine_box=quad(0, 0, **metadata["processed_raster"]),
                                         mapping={"kind": "preprocessed_page", "metadata": metadata})
    assert result["state"] == "available" and len(result["polygon"]) == 4
    assert {tuple(point) for point in result["polygon"]} == {(0., 0.), (1., 0.), (1., 1.), (0., 1.)}
    absent = geometry.map_source_polygon(engine_box=quad(0, 0, 0.01, 0.01),
                                         mapping={"kind": "preprocessed_page", "metadata": metadata})
    assert absent["reason"] == "no_positive_source_support"


@pytest.mark.parametrize("angle", [0, 90, 180, 270])
@pytest.mark.parametrize("bow", [-0.01, 0.0, 0.01])
def test_hardscan_reuses_exact_existing_clip_matrix_and_nonlinear_polygon(angle, bow):
    import ocr_hardscan as hardscan
    from ocr_hardscan_io import _source_polygons

    region = region_geometry(offset=-1)
    recipe = {"orientation_clockwise": angle, "illumination": "none", "bow_fraction": bow,
              "bow_assumption": "parallel_horizontal_baselines" if bow else "none"}
    transform = hardscan.describe_transform(100, 80, recipe)
    box = quad(0, 0, **transform["processed_raster"])
    mapping = {"kind": "hardscan", "geometry": region, "transform": transform}
    frozen = copy.deepcopy((box, mapping))
    expected = _source_polygons({"lines": [{"box": box}]}, region, transform, vertex_budget=128)[0]
    actual = geometry.map_source_polygon(engine_box=box, mapping=mapping)
    assert actual["state"] == "available" and actual["polygon"] == expected
    assert actual["edge_model"] == "sampled_nonlinear_inverse" and actual["max_error_source_pixels"] == 0.5
    assert (box, mapping) == frozen


@pytest.mark.parametrize("fault", ["nan", "long", "limit", "unsupported", "cancel"])
def test_hardscan_dependency_refusals_are_bounded_and_cancellation_propagates(monkeypatch, fault):
    import ocr_hardscan as hardscan

    transform = hardscan.describe_transform(100, 80, {"orientation_clockwise": 0, "illumination": "none",
                                                    "bow_fraction": 0., "bow_assumption": "none"})
    mapping = {"kind": "hardscan", "geometry": region_geometry(), "transform": transform}

    def injected(*args, **kwargs):
        if fault == "nan":
            return [[float("nan"), 0], [1, 0], [1, 1], [0, 1]]
        if fault == "long":
            return [[0, 0]] * 129
        if fault == "limit":
            raise ValueError("hard-scan source polygon exceeds mapping budget")
        if fault == "cancel":
            raise KeyboardInterrupt
        raise ValueError("synthetic private detail must not escape")

    monkeypatch.setattr(hardscan, "source_polygon", injected)
    if fault == "cancel":
        with pytest.raises(KeyboardInterrupt):
            geometry.map_source_polygon(engine_box=quad(), mapping=mapping)
    elif fault in ("nan", "unsupported"):
        with pytest.raises(geometry.DispositionGeometryError, match="^disposition geometry is invalid$"):
            geometry.map_source_polygon(engine_box=quad(), mapping=mapping)
    else:
        assert geometry.map_source_polygon(engine_box=quad(), mapping=mapping)["reason"] == "mapping_limit"


def test_affine_vertex_limit_never_publishes_successful_prefix(monkeypatch):
    monkeypatch.setattr(geometry, "MAX_VERTICES", 3)
    result = geometry.map_source_polygon(engine_box=quad(), mapping={"kind": "page", "raster": {"width": 100, "height": 80}})
    assert result["reason"] == "mapping_limit" and result["polygon"] is None


@pytest.mark.parametrize("fault", ["unknown", "deep", "large", "rotation", "matrix", "metadata"])
def test_malformed_mapping_is_not_repaired_to_unavailable(fault):
    mapping = {"kind": "region", "geometry": region_geometry()}
    if fault == "unknown":
        mapping["extra"] = 1
    elif fault == "deep":
        mapping["geometry"] = [[[[[[[[[[[0]]]]]]]]]]]
    elif fault == "large":
        mapping["geometry"] = [0] * 1000
    elif fault == "rotation":
        mapping["geometry"]["page"]["rotation_degrees"] = True
    elif fault == "matrix":
        mapping["geometry"]["crop_to_page_fraction"][0][0] *= 2
    else:
        mapping = {"kind": "preprocessed_page", "metadata": preprocessing()}
        mapping["metadata"]["processed_to_source"][0][2] += 0.1
    with pytest.raises(geometry.DispositionGeometryError):
        geometry.map_source_polygon(engine_box=quad(), mapping=mapping)


def test_nonconvex_quad_is_not_a_polygon_claim():
    with pytest.raises(geometry.DispositionGeometryError):
        geometry.map_source_polygon(engine_box=[[0, 0], [2, 2], [2, 0], [0, 2]],
                                    mapping={"kind": "page", "raster": {"width": 100, "height": 80}})


def test_import_and_page_mapping_are_dependency_light():
    code = """
import json,sys
import ocr_disposition_geometry as g
r = g.map_source_polygon(engine_box=[[0,0],[1,0],[1,1],[0,1]],mapping={'kind':'page','raster':{'width':2,'height':2}})
assert r['state']=='available'
assert not any(name in sys.modules for name in ('numpy','cv2','pymupdf','rapidocr','onnxruntime'))
print(json.dumps({'dependency_light':True}))
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) == {"dependency_light": True}
