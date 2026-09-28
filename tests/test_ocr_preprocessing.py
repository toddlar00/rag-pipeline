"""Synthetic-only deskew, contrast, and reversible geometry verification."""

from __future__ import annotations

import copy
import json
import math
import subprocess
import sys

import pytest

import ocr_preprocessing as preprocessing


@pytest.fixture
def libraries():
    return pytest.importorskip("cv2"), pytest.importorskip("numpy")


def _page(libraries, *, angle=0.0, foreground=0, background=255):
    cv2, np = libraries
    image = np.full((650, 900, 3), background, np.uint8)
    for row in range(10):
        cv2.putText(image, "The court did not waive notice. Rule 12 requires 30 days.",
                    (45, 65 + row * 55), cv2.FONT_HERSHEY_SIMPLEX, 0.72,
                    (foreground,) * 3, 1, cv2.LINE_AA)
    if angle:
        width, height, matrix, _ = preprocessing._rotation_geometry(900, 650, angle)
        image = cv2.warpAffine(image, np.asarray(matrix), (width, height),
                               borderValue=(background,) * 3)
    return image


def _process(image, *, mode="deskew", max_side=2000, max_pixels=4_000_000):
    return preprocessing.preprocess_image(image, mode=mode,
                                          max_side=max_side, max_pixels=max_pixels)


def _metadata(mode="contrast"):
    deskew_enabled = mode in ("deskew", "deskew-contrast")
    contrast_enabled = mode in ("contrast", "deskew-contrast")
    return {
        "schema_version": 1, "algorithm": "bounded-deskew-contrast-v1", "mode": mode,
        "original_raster": {"width": 100, "height": 100},
        "processed_raster": {"width": 100, "height": 100},
        "source_to_processed": [[1, 0, 0], [0, 1, 0]],
        "processed_to_source": [[1, 0, 0], [0, 1, 0]],
        "deskew": {"status": "skipped" if deskew_enabled else "disabled",
                   "reason": "blank_or_low_ink" if deskew_enabled else "mode_disabled",
                   "angle_degrees": 0.0, "estimated_angle_degrees": None, "gain": None},
        "contrast": {"status": "skipped" if contrast_enabled else "disabled",
                     "reason": "already_high_contrast" if contrast_enabled else "mode_disabled",
                     "low_level": 0 if contrast_enabled else None,
                     "high_level": 255 if contrast_enabled else None},
        "parameters": {"thumbnail_max_side": 1000, "max_angle_degrees": 5.0,
                       "angle_step_degrees": 0.25, "min_gain": 0.025,
                       "low_percentile": 0.5, "high_percentile": 99.5},
        "libraries": {"opencv": "synthetic", "numpy": "synthetic"},
    }


def _rotated_metadata(angle=1.0):
    result = _metadata("deskew")
    width, height, forward, inverse = preprocessing._rotation_geometry(100, 100, angle)
    result["processed_raster"] = {"width": width, "height": height}
    result["source_to_processed"] = forward
    result["processed_to_source"] = inverse
    result["deskew"] = {"status": "applied", "reason": "rotation_applied",
                        "angle_degrees": angle, "estimated_angle_degrees": angle, "gain": 0.2}
    return result


def _validate(metadata, **kwargs):
    raster = metadata["processed_raster"]
    return preprocessing.validate_metadata(
        metadata, width=raster["width"], height=raster["height"], **kwargs)


def test_module_and_validator_import_without_optional_image_libraries():
    code = (
        "import sys; import ocr_preprocessing as p; "
        "assert 'cv2' not in sys.modules and 'numpy' not in sys.modules; "
        "assert p.validate_mode('deskew') == 'deskew'"
    )
    completed = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize("mode", preprocessing.PREPROCESSING_MODES)
def test_all_modes_have_explicit_valid_metadata(mode):
    metadata = _metadata(mode)
    result = _validate(metadata)
    assert result == metadata and result is not metadata
    result["source_to_processed"][0][0] = 5
    assert metadata["source_to_processed"][0][0] == 1


@pytest.mark.parametrize("mode", [None, True, [], {}, "DESKEW", " deskew", "deskew-contrast ", 0])
def test_unknown_modes_are_not_coerced(mode):
    with pytest.raises(ValueError):
        preprocessing.validate_mode(mode)


@pytest.mark.parametrize("angle", [-4.0, -2.5, -1.0, 1.0, 2.5, 4.0])
def test_deskew_recovers_sign_and_angle_from_synthetic_text(libraries, angle):
    cv2, np = libraries
    source = _page(libraries, angle=angle)
    before = source.copy()
    processed, metadata = _process(source)
    deskew = metadata["deskew"]
    assert deskew["status"] == "applied"
    assert deskew["angle_degrees"] == pytest.approx(-angle, abs=0.25)
    assert deskew["gain"] >= 0.025
    assert processed.dtype == np.uint8 and processed.shape[2] == 3
    assert processed.flags.c_contiguous
    assert np.array_equal(source, before)
    assert not np.shares_memory(source, processed)
    assert processed[0, 0].tolist() == [255, 255, 255]
    _validate(metadata, max_side=2000, max_pixels=4_000_000)
    residual = preprocessing._estimate_deskew(cv2.cvtColor(processed, cv2.COLOR_BGR2GRAY), cv2, np)
    assert abs(residual["estimated_angle_degrees"] or 0) <= 0.25


def test_upright_text_does_not_get_resampled(libraries):
    _, np = libraries
    source = _page(libraries)
    processed, metadata = _process(source)
    assert metadata["deskew"]["status"] == "skipped"
    assert metadata["deskew"]["reason"] == "near_zero_angle"
    assert metadata["deskew"]["angle_degrees"] == 0
    assert np.array_equal(processed, source)


@pytest.mark.parametrize("angle", [-5.0, 5.0])
def test_boundary_optimum_is_skipped_instead_of_extrapolated(libraries, angle):
    _, np = libraries
    source = _page(libraries, angle=angle)
    processed, metadata = _process(source)
    assert metadata["deskew"]["status"] == "skipped"
    assert metadata["deskew"]["reason"] == "boundary_angle"
    assert metadata["deskew"]["angle_degrees"] == 0
    assert abs(metadata["deskew"]["estimated_angle_degrees"]) == 5
    assert np.array_equal(processed, source)


@pytest.mark.parametrize("value", [0, 128, 255])
def test_blank_and_uniform_pages_have_no_gratuitous_processing(libraries, value):
    _, np = libraries
    image = np.full((300, 400, 3), value, np.uint8)
    processed, metadata = _process(image, mode="deskew-contrast")
    assert metadata["deskew"]["status"] == "skipped"
    assert metadata["deskew"]["reason"] in ("blank_or_low_ink", "non_text_pattern")
    assert metadata["contrast"]["status"] == "skipped"
    assert metadata["contrast"]["reason"] == "insufficient_dynamic_range"
    assert np.array_equal(processed, image)


def test_sparse_low_ink_and_salt_noise_are_skipped(libraries):
    _, np = libraries
    low_ink = np.full((500, 500, 3), 255, np.uint8)
    low_ink[100:103, 100:103] = 0
    noise = np.full((500, 500, 3), 255, np.uint8)
    generator = np.random.default_rng(451)
    noise[generator.random((500, 500)) < 0.02] = 0
    for source, reason in ((low_ink, "blank_or_low_ink"), (noise, "non_text_pattern")):
        processed, metadata = _process(source)
        assert metadata["deskew"]["reason"] == reason
        assert metadata["deskew"]["angle_degrees"] == 0
        assert np.array_equal(processed, source)


def test_dense_noise_and_non_text_shapes_are_skipped(libraries):
    cv2, np = libraries
    noise = np.random.default_rng(119).integers(0, 256, (300, 400, 3), dtype=np.uint8)
    graphic = np.full((400, 400, 3), 255, np.uint8)
    cv2.rectangle(graphic, (90, 90), (280, 300), (0, 0, 0), -1)
    for image in (noise, graphic):
        processed, metadata = _process(image)
        assert metadata["deskew"]["reason"] == "non_text_pattern"
        assert np.array_equal(processed, image)


def test_tied_projection_scores_choose_zero_and_skip(libraries, monkeypatch):
    _, np = libraries
    source = _page(libraries)
    monkeypatch.setattr(np, "dot", lambda *args: 1.0)
    # Use one fixed projection sum to make all objective values exact ties.
    actual_count = np.count_nonzero

    def constant_projection(array, axis=None):
        if axis == 1:
            return np.ones(array.shape[0], dtype=np.int64)
        return actual_count(array, axis=axis)

    monkeypatch.setattr(np, "count_nonzero", constant_projection)
    processed, metadata = _process(source)
    assert metadata["deskew"]["reason"] == "near_zero_angle"
    assert metadata["deskew"]["estimated_angle_degrees"] == 0
    assert np.array_equal(processed, source)


@pytest.mark.parametrize("best,other,reason", [
    (101.0, 95.0, "insufficient_gain"),
    (120.0, 119.5, "ambiguous_angle"),
])
def test_weak_or_competing_projection_peaks_are_not_applied(libraries, monkeypatch, best, other, reason):
    _, np = libraries
    source = _page(libraries)
    index = 0

    def projection_objective(left, right):
        nonlocal index
        score = {12: best, 20: 100.0, 28: other}.get(index, 95.0)
        index += 1
        return score * float(left.sum())

    monkeypatch.setattr(np, "dot", projection_objective)
    processed, metadata = _process(source)
    assert metadata["deskew"]["reason"] == reason
    assert metadata["deskew"]["status"] == "skipped"
    assert metadata["deskew"]["angle_degrees"] == 0
    assert np.array_equal(processed, source)


@pytest.mark.parametrize("kind", ["side", "pixels"])
def test_expansion_budget_is_checked_before_full_resolution_warp(libraries, monkeypatch, kind):
    cv2, np = libraries
    source = _page(libraries, angle=2.5)
    full_warps = []
    warp = cv2.warpAffine

    def checked_warp(image, *args, **kwargs):
        if image.ndim == 3:
            full_warps.append(image.shape)
        return warp(image, *args, **kwargs)

    monkeypatch.setattr(cv2, "warpAffine", checked_warp)
    settings = {"max_side": max(source.shape[:2])} if kind == "side" else {
        "max_pixels": source.shape[0] * source.shape[1]}
    processed, metadata = _process(source, **settings)
    assert metadata["deskew"]["reason"] == "expanded_raster_limit"
    assert metadata["deskew"]["angle_degrees"] == 0
    assert metadata["deskew"]["estimated_angle_degrees"] == pytest.approx(-2.5, abs=0.25)
    assert full_warps == []
    assert np.array_equal(processed, source)


def test_contrast_stretches_histogram_levels_without_mutating_source(libraries):
    _, np = libraries
    source = np.full((120, 160, 3), 220, np.uint8)
    source[:, :80] = 160
    before = source.copy()
    processed, metadata = _process(source, mode="contrast")
    assert metadata["contrast"] == {
        "status": "applied", "reason": "stretch_applied", "low_level": 160, "high_level": 220}
    assert processed.min() == 0 and processed.max() == 255
    assert np.array_equal(processed[..., 0], processed[..., 1])
    assert np.array_equal(processed[..., 1], processed[..., 2])
    assert np.array_equal(source, before)
    assert metadata["source_to_processed"] == [[1, 0, 0], [0, 1, 0]]


def test_contrast_preserves_faint_text_order_and_does_not_erode_its_signal(libraries):
    cv2, np = libraries
    source = _page(libraries, foreground=190, background=230)
    processed, metadata = _process(source, mode="contrast")
    assert metadata["contrast"]["status"] == "applied"
    before = cv2.cvtColor(source, cv2.COLOR_BGR2GRAY)
    after = cv2.cvtColor(processed, cv2.COLOR_BGR2GRAY)
    assert int(after.max()) - int(after.min()) > int(before.max()) - int(before.min())
    # Every faint foreground pixel stays distinct from the uniform background,
    # including antialiased thin strokes near the background's luminance.
    assert np.array_equal(before < before.max(), after < after.max())


@pytest.mark.parametrize("kind", ["narrow_range", "outliers", "high_contrast"])
def test_contrast_skips_tiny_ranges_outliers_and_already_strong_images(libraries, kind):
    _, np = libraries
    source = np.full((100, 100, 3), 128, np.uint8)
    if kind == "narrow_range":
        source[:, :50] = 135
    elif kind == "outliers":
        source[0, 0] = 0
        source[-1, -1] = 255
    else:
        source[:, :50] = 0
        source[:, 50:] = 255
    processed, metadata = _process(source, mode="contrast")
    assert metadata["contrast"]["status"] == "skipped"
    assert metadata["contrast"]["reason"] == (
        "already_high_contrast" if kind == "high_contrast" else "insufficient_dynamic_range")
    assert np.array_equal(processed, source)


def test_combined_mode_measures_contrast_before_white_rotation_border(libraries):
    _, np = libraries
    source = _page(libraries, angle=2.5, foreground=170, background=220)
    processed, metadata = _process(source, mode="deskew-contrast")
    assert metadata["deskew"]["status"] == "applied"
    assert metadata["contrast"]["status"] == "applied"
    assert metadata["contrast"]["high_level"] == 220
    assert processed[0, 0].tolist() == [255, 255, 255]
    assert np.array_equal(source[0, 0], [220, 220, 220])


def test_none_returns_independent_contiguous_identity_copy(libraries):
    _, np = libraries
    source = np.arange(60 * 80 * 3, dtype=np.uint8).reshape(60, 80, 3)[:, ::2]
    assert not source.flags.c_contiguous
    result, metadata = _process(source, mode="none")
    assert result.flags.c_contiguous
    assert not np.shares_memory(source, result)
    assert np.array_equal(source, result)
    assert metadata["deskew"]["status"] == metadata["contrast"]["status"] == "disabled"


@pytest.mark.parametrize("shape,dtype", [
    ((20, 20), "uint8"), ((20, 20, 4), "uint8"), ((20, 20, 1), "uint8"),
    ((0, 20, 3), "uint8"), ((20, 0, 3), "uint8"),
    ((20, 20, 3), "float32"), ((20, 20, 3), "bool"),
])
def test_invalid_image_shape_or_type_fails(libraries, shape, dtype):
    _, np = libraries
    with pytest.raises(ValueError):
        _process(np.zeros(shape, dtype=dtype))


@pytest.mark.parametrize("limits", [
    {"max_side": True}, {"max_side": 0}, {"max_side": 12001},
    {"max_pixels": 0}, {"max_pixels": 100000001}, {"max_pixels": 1.0},
    {"max_side": 19}, {"max_pixels": 399},
])
def test_invalid_or_exceeded_input_budgets_fail_before_image_processing(libraries, monkeypatch, limits):
    cv2, np = libraries
    monkeypatch.setattr(cv2, "cvtColor", lambda *args: pytest.fail("input limits must precede image work"))
    with pytest.raises(ValueError):
        _process(np.zeros((20, 20, 3), np.uint8), **limits)


@pytest.mark.parametrize("angle", [-4.75, -1.0, 0.0, 1.0, 4.75])
def test_expanded_transform_covers_source_corners_and_has_true_inverse(angle):
    width, height = 901, 37
    ow, oh, forward, inverse = preprocessing._rotation_geometry(width, height, angle)
    for x, y in ((0, 0), (width, 0), (0, height), (width, height)):
        px = forward[0][0] * x + forward[0][1] * y + forward[0][2]
        py = forward[1][0] * x + forward[1][1] * y + forward[1][2]
        assert -1e-10 <= px <= ow + 1e-10
        assert -1e-10 <= py <= oh + 1e-10
        rx = inverse[0][0] * px + inverse[0][1] * py + inverse[0][2]
        ry = inverse[1][0] * px + inverse[1][1] * py + inverse[1][2]
        assert math.isclose(rx, x, abs_tol=1e-10)
        assert math.isclose(ry, y, abs_tol=1e-10)


@pytest.mark.parametrize("angle", [-4.75, -1, 1, 4.75])
def test_declared_nonzero_geometry_roundtrips_in_metadata(angle):
    payload = _rotated_metadata(angle)
    assert _validate(payload) == payload


@pytest.mark.parametrize("path,value", [
    (("schema_version",), True), (("schema_version",), 2),
    (("mode",), "arbitrary"), (("algorithm",), "unreviewed"),
    (("original_raster", "width"), False), (("original_raster", "height"), 0),
    (("processed_raster", "width"), 101),
    (("source_to_processed",), [[1, 0, 0]]),
    (("source_to_processed", 0), [1, 0]),
    (("source_to_processed", 0, 0), True),
    (("source_to_processed", 0, 1), float("nan")),
    (("source_to_processed", 0, 2), float("inf")),
    (("source_to_processed", 1, 2), 10 ** 1000),
    (("processed_to_source", 0, 2), 0.01),
    (("deskew", "status"), "applied"),
    (("deskew", "reason"), "near_zero_angle"),
    (("deskew", "angle_degrees"), 1),
    (("deskew", "estimated_angle_degrees"), 0),
    (("deskew", "gain"), 0.5),
    (("contrast", "status"), "applied"),
    (("contrast", "reason"), "stretch_applied"),
    (("contrast", "low_level"), True),
    (("contrast", "high_level"), 256),
    (("parameters", "thumbnail_max_side"), True),
    (("parameters", "max_angle_degrees"), 10),
    (("parameters", "min_gain"), float("nan")),
    (("libraries", "opencv"), "PRIVATE/path"),
    (("libraries", "numpy"), "x" * 65),
    (("libraries", "numpy"), "\ud800"),
], ids=lambda item: type(item).__name__)
def test_metadata_rejects_malformed_transform_status_and_parameter_fields(path, value):
    payload = _metadata()
    owner = payload
    for key in path[:-1]:
        owner = owner[key]
    owner[path[-1]] = value
    with pytest.raises(ValueError):
        _validate(payload)


@pytest.mark.parametrize("path", [(), ("original_raster",), ("processed_raster",),
                                 ("deskew",), ("contrast",), ("parameters",), ("libraries",)])
def test_unknown_metadata_fields_fail(path):
    payload = _metadata()
    owner = payload
    for key in path:
        owner = owner[key]
    owner["unknown"] = "PRIVATE_TEXT"
    with pytest.raises(ValueError) as error:
        _validate(payload)
    assert "PRIVATE_TEXT" not in str(error.value)


def test_arbitrary_invertible_affine_map_is_not_accepted_as_deskew():
    payload = _metadata()
    payload["source_to_processed"] = [[1, 0, 2], [0, 1, 3]]
    payload["processed_to_source"] = [[1, 0, -2], [0, 1, -3]]
    with pytest.raises(ValueError, match="disagrees with rotation"):
        _validate(payload)


@pytest.mark.parametrize("patch", [
    {"angle_degrees": 5, "estimated_angle_degrees": 5},
    {"angle_degrees": 0.25, "estimated_angle_degrees": 0.25},
    {"angle_degrees": 0.1}, {"estimated_angle_degrees": -1},
    {"gain": None}, {"gain": 0.024}, {"gain": -0.1}, {"gain": float("nan")},
])
def test_applied_rotation_requires_measured_interior_angle_and_gain(patch):
    payload = _rotated_metadata()
    payload["deskew"].update(patch)
    with pytest.raises(ValueError):
        _validate(payload)


def test_metadata_output_and_resource_limits_are_enforced():
    payload = _metadata()
    with pytest.raises(ValueError):
        preprocessing.validate_metadata(payload, width=99, height=100)
    with pytest.raises(ValueError):
        _validate(payload, max_side=99)
    with pytest.raises(ValueError):
        _validate(payload, max_pixels=9999)
    with pytest.raises(ValueError):
        _validate(payload, max_pixels=True)


def test_validation_detaches_all_nested_mutable_metadata():
    payload = _rotated_metadata()
    original = copy.deepcopy(payload)
    validated = _validate(payload)
    payload["parameters"]["min_gain"] = 1
    payload["original_raster"]["width"] = 1
    payload["source_to_processed"][0][2] = 1
    assert validated == original
    assert json.loads(json.dumps(validated, allow_nan=False)) == original
