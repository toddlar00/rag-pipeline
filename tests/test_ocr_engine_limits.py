"""Dependency-light native-allocation contracts; no pixels or models loaded."""

import copy
from dataclasses import FrozenInstanceError
import math
from pathlib import Path
import random
import struct
import subprocess
import sys

import pytest

import ocr_engine_limits as limits


@pytest.mark.parametrize("side,pixels", [(32, 1), (6000, 25_000_000), (12000, 100_000_000)])
def test_dynamic_reader_limits_are_preserved(side, pixels):
    policy = limits.EngineLimits(max_side=side, max_pixels=pixels)
    assert (policy.max_side, policy.max_pixels) == (side, pixels)
    with pytest.raises(FrozenInstanceError):
        policy.max_side = 100


@pytest.mark.parametrize("field", ["max_side", "max_pixels"])
@pytest.mark.parametrize("value", [True, False, "6000", 6000.0, None, -1, 0, 10**1000])
def test_limit_configuration_is_type_strict_and_bounded(field, value):
    with pytest.raises(limits.EngineAllocationLimit):
        limits.EngineLimits(**{field: value})


@pytest.mark.parametrize("kwargs", [{"max_side": 31}, {"max_side": 12001}, {"max_pixels": 100_000_001}])
def test_limit_endpoints_rejected(kwargs):
    with pytest.raises(limits.EngineAllocationLimit):
        limits.EngineLimits(**kwargs)


def test_mutated_frozen_limits_are_revalidated():
    policy = limits.EngineLimits()
    object.__setattr__(policy, "max_side", True)
    with pytest.raises(limits.EngineAllocationLimit):
        limits.image_dimensions((1, 1, 3), limits=policy)


@pytest.mark.parametrize("value", [{}, 6000, object()])
def test_limits_cannot_be_a_duck_typed_override(value):
    with pytest.raises(limits.EngineAllocationLimit):
        limits.image_dimensions((1, 1, 3), limits=value)


@pytest.mark.parametrize("shape", [None, "123", {}, b"123", [], [1, 2], [1, 2, 3, 4],
                                  [1, 2, 1], [1, 2, True], [True, 2, 3], [1., 2, 3],
                                  [0, 1, 3], [1, -1, 3], [1, 6001, 3], [5001, 5000, 3]])
def test_image_shape_requires_positive_bounded_integer_hwc3(shape):
    with pytest.raises(limits.EngineAllocationLimit):
        limits.image_dimensions(shape)


def test_image_shape_custom_budget_and_no_mutation():
    shape = [12000, 1, 3]
    assert limits.image_dimensions(shape, limits=limits.EngineLimits(12000, 100_000_000)) == (1, 12000)
    assert shape == [12000, 1, 3]
    with pytest.raises(limits.EngineAllocationLimit):
        limits.image_dimensions(shape, limits=limits.EngineLimits(12000, 11999))


class Endless:
    def __init__(self):
        self.calls = 0

    def __len__(self):
        return 0  # An invalid length hint must not defeat the iteration bound.

    def __iter__(self):
        return self

    def __next__(self):
        self.calls += 1
        return 1


@pytest.mark.parametrize("maximum", [0, 1, 4, limits.MAX_BOXES])
def test_sequence_stops_after_at_most_maximum_plus_one(maximum):
    source = Endless()
    with pytest.raises(limits.EngineAllocationLimit):
        limits.bounded_sequence(source, maximum=maximum)
    assert source.calls == maximum + 1


def test_sequence_does_not_use_a_length_hint_or_mutate_input():
    class NoLength(list):
        def __len__(self):
            raise AssertionError("untrusted length used")

    value = NoLength([1, 2, 3])
    assert limits.bounded_sequence(value, maximum=3) == (1, 2, 3)


@pytest.mark.parametrize("maximum", [True, -1, 2001, 3.0])
def test_sequence_bound_is_strict(maximum):
    with pytest.raises(limits.EngineAllocationLimit):
        limits.bounded_sequence([], maximum=maximum)


@pytest.mark.parametrize("value", [None, "private", b"private", bytearray(b"private"),
                                  memoryview(b"private"), {"private": 1}, 1])
def test_invalid_sequence_errors_are_static(value):
    with pytest.raises(limits.EngineAllocationLimit) as caught:
        limits.bounded_sequence(value, maximum=3)
    assert "private" not in str(caught.value)


def test_sequence_cancellation_is_not_swallowed():
    class Cancel:
        def __iter__(self):
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        limits.bounded_sequence(Cancel(), maximum=3)


def test_iterator_failure_is_redacted_without_hiding_cancellation():
    class Broken:
        def __iter__(self):
            return self

        def __next__(self):
            raise RuntimeError("private source value")

    with pytest.raises(limits.EngineAllocationLimit) as caught:
        limits.bounded_sequence(Broken(), maximum=3)
    assert "private" not in str(caught.value)


@pytest.mark.parametrize("width,height,detection,expected", [
    (1800, 2400, True, {"global": [1800, 2400], "padded": [1800, 2400], "detector": [1792, 2400]}),
    (100, 10, False, {"global": [288, 32]}),
    (100, 10, True, {"global": [288, 32], "padded": [288, 72], "detector": [2944, 736]}),
    (240, 30, True, {"global": [240, 30], "padded": [240, 60], "detector": [2944, 736]}),
    (240, 31, True, {"global": [240, 31], "padded": [240, 31], "detector": [5696, 736]}),
    (256, 32, True, {"global": [256, 32], "padded": [256, 32], "detector": [5888, 736]}),
    (257, 32, True, {"global": [257, 32], "padded": [257, 64], "detector": [2944, 736]}),
    (1, 1, False, {"global": [32, 32]}),
])
def test_exact_upstream_global_padding_and_detector_rounding(width, height, detection, expected):
    assert limits.working_dimensions(width, height, detection=detection) == expected


@pytest.mark.parametrize("dimensions", [(736, 736, (736, 736)), (752, 784, (768, 768)),
                                        (720, 960, (736, 992)), (32, 256, (736, 5888))])
def test_detector_minimum_and_bankers_rounding(dimensions):
    width, height, expected = dimensions
    assert limits.detector_dimensions(width, height) == expected


@pytest.mark.parametrize("value", [1, 0, None, "true"])
def test_detection_flag_must_be_exact_boolean(value):
    with pytest.raises(limits.EngineAllocationLimit):
        limits.working_dimensions(100, 100, detection=value)


@pytest.mark.parametrize("width,height", [(6000, 1), (1, 6000), (5020, 4980), (6000, 736)])
def test_intermediate_expansion_is_rejected_even_for_admitted_source(width, height):
    assert limits.image_dimensions((height, width, 3)) == (width, height)
    with pytest.raises(limits.EngineAllocationLimit):
        limits.working_dimensions(width, height, detection=True)


def test_custom_maximum_is_not_silently_replaced_by_stage_defaults():
    policy = limits.EngineLimits(12000, 100_000_000)
    assert limits.working_dimensions(9000, 9000, detection=True, limits=policy) == {
        "global": [9000, 9000], "padded": [9000, 9000], "detector": [8992, 8992]}
    with pytest.raises(limits.EngineAllocationLimit):
        limits.working_dimensions(9000, 9000, detection=True)


def box(width, height):
    return [[0, 0], [width, 0], [width, height], [0, height]]


@pytest.mark.parametrize("points", [box(6000, 10), box(100, 300),
                                   [[2000, 0], [4000, 2000], [2000, 4000], [0, 2000]]])
def test_rectification_is_an_upper_bound_and_does_not_mutate(points):
    before = copy.deepcopy(points)
    shape, = limits.crop_shapes([points], width=6000, height=4000)
    assert shape[0] >= int(max(math.dist(points[0], points[1]), math.dist(points[2], points[3])))
    assert shape[1] >= int(max(math.dist(points[0], points[3]), math.dist(points[1], points[2])))
    assert points == before


@pytest.mark.parametrize("points", [[], [[1, 2]] * 3, box(0, 1), box(.9, 1),
                                   [[0, 0], [10, 0], [5, 0], [0, 10]],
                                   [[0, 0], [10, 10], [0, 10], [10, 0]],
                                   [[0, 0], [10, 0], [3, 3], [0, 10]],
                                   box(101, 10), box(10, 101), box(True, 1),
                                   box(float("nan"), 1), box(float("inf"), 1), box(10**1000, 1)])
def test_malformed_degenerate_concave_or_outside_crops_reject(points):
    with pytest.raises(limits.EngineAllocationLimit):
        limits.crop_shapes([points], width=100, height=100)


def test_crop_count_is_bounded_before_any_quad_validation():
    source = Endless()
    with pytest.raises(limits.EngineAllocationLimit):
        limits.crop_shapes(source, width=100, height=100)
    assert source.calls == limits.MAX_BOXES + 1


def test_crop_aggregate_upper_bounds_are_checked():
    assert limits.crop_shapes([box(5000, 5000)] * 4, width=5000, height=5000) == ((5000, 5000),) * 4
    with pytest.raises(limits.EngineAllocationLimit):
        limits.crop_shapes([box(5000, 5000)] * 5, width=5000, height=5000)


def test_float32_norm_rounding_near_integer_is_conservatively_covered():
    def f32(value):
        return struct.unpack("f", struct.pack("f", value))[0]

    def norm(a, b):
        x, y = f32(a[0] - b[0]), f32(a[1] - b[1])
        return int(f32(math.sqrt(f32(f32(x * x) + f32(y * y)))))

    rng = random.Random(401)
    for _ in range(400):
        width, height = f32(rng.uniform(2, 2000)), f32(rng.uniform(2, 2000))
        skew = f32(rng.uniform(-.5, .5))
        points = [[2., 2.], [width + 2., 2. + skew], [width + 2., height + 2. + skew], [2., height + 2.]]
        points = [[f32(x), f32(y)] for x, y in points]
        actual = (max(norm(points[0], points[1]), norm(points[2], points[3])),
                  max(norm(points[0], points[3]), norm(points[1], points[2])))
        upper, = limits.crop_shapes([points], width=2100, height=2100)
        assert all(a <= b for a, b in zip(actual, upper))


def test_near_cap_float32_quad_intentionally_abstains_conservatively():
    # The upstream float32 norm floors this measured warp to 6000 x 10.
    # Our pre-allocation upper bound is 6001, so this former boundary case
    # intentionally abstains; admitted inputs are never silently downsampled.
    points = [[0., 0.], [5990., 363.1652526855469],
              [5990., 373.1652526855469], [0., 10.]]
    assert limits.working_dimensions(5990, 736, detection=True) == {
        "global": [5990, 736], "padded": [5990, 1496], "detector": [5984, 1504],
    }
    with pytest.raises(limits.EngineAllocationLimit):
        limits.crop_shapes([points], width=5990, height=1496)


@pytest.mark.parametrize("role", ["crops", "classification", "recognition"])
def test_empty_crop_cohort_remains_empty(role):
    assert limits.validate_crop_shapes([], role=role) == {"shapes": (), "pixels": 0, "batches": (), "tensor_elements": 0}


def test_actual_recognition_batches_follow_sorted_aspect_ratio_and_batch_six():
    source = [(48, w, 3) for w in (4096, 100, 500, 200, 700, 300, 600)]
    before = source.copy()
    result = limits.validate_crop_shapes(source, role="recognition")
    assert result["shapes"] == tuple(source)
    assert result["pixels"] == sum(h * w for h, w, _ in source)
    assert result["batches"] == ((6, 3, 48, 700), (1, 3, 48, 4096))
    assert result["tensor_elements"] == 6 * 3 * 48 * 700 + 3 * 48 * 4096
    assert source == before


def test_recognition_keeps_existing_conservative_ceil_width_ceiling():
    assert limits.validate_crop_shapes([(48, 4096, 3)], role="recognition")["batches"] == ((1, 3, 48, 4096),)
    with pytest.raises(limits.EngineAllocationLimit):
        limits.validate_crop_shapes([(47, 4011, 3)], role="recognition")


def test_recognition_tensor_uses_upstream_truncation_not_conservative_ceil():
    assert limits.validate_crop_shapes([(47, 500, 3)], role="recognition")["batches"] == ((1, 3, 48, 510),)


def test_classifier_has_fixed_approved_v4_shape_and_batch_six():
    result = limits.validate_crop_shapes([(1, 6000, 3)] * 7, role="classification")
    assert result["batches"] == ((6, 3, 48, 192), (1, 3, 48, 192))
    assert result["tensor_elements"] == 7 * 3 * 48 * 192


@pytest.mark.parametrize("role", ["crops", "classification", "recognition"])
def test_actual_crops_enforce_count_channels_and_total_pixel_caps(role):
    with pytest.raises(limits.EngineAllocationLimit):
        limits.validate_crop_shapes([(5000, 5000, 3)] * 5, role=role)
    with pytest.raises(limits.EngineAllocationLimit):
        limits.validate_crop_shapes([(1, 1, 3)] * 2001, role=role)
    with pytest.raises(limits.EngineAllocationLimit):
        limits.validate_crop_shapes([(1, 1, 3)], role=role, expected_count=2)
    with pytest.raises(limits.EngineAllocationLimit):
        limits.validate_crop_shapes([(1, 1, 3)], role=role, expected_count=True)


@pytest.mark.parametrize("shape,role", [((1, 3, 736, 736), "detection"), ((6, 3, 48, 192), "classification"),
                                        ((6, 3, 48, 4096), "recognition")])
def test_actual_normalized_tensors_are_bounded_and_match_expected(shape, role):
    assert limits.validate_tensor_shape(shape, role=role, expected=shape) == shape
    with pytest.raises(limits.EngineAllocationLimit):
        limits.validate_tensor_shape(shape, role=role, expected=(True, *shape[1:]))


@pytest.mark.parametrize("shape,role", [((2, 3, 736, 736), "detection"), ((1, 3, 735, 736), "detection"),
    ((1, 3, 5024, 5024), "detection"), ((7, 3, 48, 192), "classification"),
    ((1, 3, 80, 160), "classification"), ((1, 3, 48, 193), "classification"),
    ((1, 3, 48, 4097), "recognition"), ((1, 3, 48, 319), "recognition"),
    ((0, 3, 48, 320), "recognition"), ((1, 1, 48, 320), "recognition"),
    ((True, 3, 48, 320), "recognition"), ((1., 3, 48, 320), "recognition"),
    ((1, 3, 48), "recognition"), ((1, 3, 48, 320, 1), "recognition"),
    ((1, 3, 48, 320), "unsupported")])
def test_invalid_tensor_shapes_reject_before_handoff(shape, role):
    with pytest.raises(limits.EngineAllocationLimit):
        limits.validate_tensor_shape(shape, role=role)


def test_final_remap_uses_banker_round_and_preserves_count():
    assert limits.remapped_crop_shapes([(7, 5, 3), (3, 11, 3)], ratio_h=.5, ratio_w=.5) == ((4, 2, 3), (2, 6, 3))


def test_final_remap_checks_rotated_crops_in_their_actual_orientation():
    assert limits.remapped_crop_shapes([(20, 300, 3)], ratio_h=2, ratio_w=.5) == ((40, 150, 3),)


@pytest.mark.parametrize("ratio", [True, False, "1", None, 0, -1, float("nan"), float("inf"), 10**1000, 12001])
def test_remap_ratio_is_strict_and_finite(ratio):
    with pytest.raises(limits.EngineAllocationLimit):
        limits.remapped_crop_shapes([(10, 10, 3)], ratio_h=ratio, ratio_w=1)


def test_final_remap_rejects_zero_dimension_side_and_aggregate_expansion():
    for shapes, rh, rw in [([(1, 1, 3)], .1, 1), ([(10, 4000, 3)], 1, 2),
                          ([(2500, 5000, 3)] * 5, 2, 1)]:
        with pytest.raises(limits.EngineAllocationLimit):
            limits.remapped_crop_shapes(shapes, ratio_h=rh, ratio_w=rw)


def test_fresh_import_is_standard_library_only():
    root = Path(__file__).resolve().parents[1]
    script = """
import importlib.abc, sys
sys.path.insert(0, sys.argv[1])
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if fullname.split('.')[0] in {'numpy','cv2','pymupdf','fitz','rapidocr','onnxruntime','torch','rag'}:
            raise AssertionError('unexpected runtime import')
sys.meta_path.insert(0, Block())
import ocr_engine_limits
assert ocr_engine_limits.working_dimensions(100, 100, detection=False) == {'global':[100,100]}
"""
    result = subprocess.run([sys.executable, "-I", "-c", script, str(root)], capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr.decode()
