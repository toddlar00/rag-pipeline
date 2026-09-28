"""Strict hard-scan recipes, analytic geometry and bounded in-memory experiments."""

import math
import subprocess
import sys

import pytest

import ocr_hardscan as policy


def recipe(angle=0, bow=0., illumination="none"):
    return {"orientation_clockwise": angle, "bow_fraction": bow, "illumination": illumination,
            "bow_assumption": "parallel_horizontal_baselines" if bow else "none"}


def plan():
    return {"schema_version": 1, "kind": "ocr_hardscan_plan", "source_sha256": "a"*64,
            "recovery_sha256": "b"*64, "coordinate_system": policy.COORDINATES,
            "approval": "operator_approved", "regions": [
                {"region_id": "r1", "page_number": 1, "bbox": [0., 0., 1., 1.], "recipe": recipe()}]}


def validate(value):
    return policy.validate_plan(value, source_sha256="a"*64, recovery_sha256="b"*64, page_count=2)


def test_plan_is_strict_sorted_and_detached():
    value = plan()
    value["regions"].insert(0, {"region_id": "r2", "page_number": 2, "bbox": [0, 0, 1, 1], "recipe": recipe(90)})
    result = validate(value)
    assert [region["page_number"] for region in result["regions"]] == [1, 2]
    result["regions"][0]["recipe"]["orientation_clockwise"] = 180
    assert value["regions"][1]["recipe"]["orientation_clockwise"] == 0


@pytest.mark.parametrize("key,value", [("orientation_clockwise", True), ("orientation_clockwise", 45),
                                       ("orientation_clockwise", 90.), ("orientation_clockwise", -90),
                                       ("illumination", "clahe"), ("illumination", []),
                                       ("bow_fraction", True), ("bow_fraction", "0"),
                                       ("bow_fraction", 10**1000), ("bow_fraction", float("nan")),
                                       ("bow_fraction", float("inf")), ("bow_fraction", .026),
                                       ("bow_assumption", "automatic"), ("extra", "private")])
def test_invalid_recipe_fails_closed(key, value):
    item = recipe()
    item[key] = value
    with pytest.raises(ValueError):
        policy.validate_recipe(item)


@pytest.mark.parametrize("mutant", ["source", "recovery", "approval", "version", "kind", "coordinates",
                                    "empty", "many", "duplicate", "page", "bbox_bool", "extra"])
def test_plan_rejects_wrong_binding_or_shape(mutant):
    value = plan()
    if mutant in ("source", "recovery"):
        value[mutant+"_sha256"] = "c"*64
    elif mutant == "approval":
        value["approval"] = "inferred"
    elif mutant == "version":
        value["schema_version"] = True
    elif mutant == "kind":
        value["kind"] = "ocr_region_plan"
    elif mutant == "coordinates":
        value["coordinate_system"] = "candidate_raster_fraction"
    elif mutant == "empty":
        value["regions"] = []
    elif mutant == "many":
        value["regions"] *= 21
    elif mutant == "duplicate":
        value["regions"] *= 2
    elif mutant == "page":
        value["regions"][0]["page_number"] = 3
    elif mutant == "bbox_bool":
        value["regions"][0]["bbox"][0] = False
    else:
        value["extra"] = 1
    with pytest.raises(ValueError):
        validate(value)


@pytest.mark.parametrize("angle", [0, 90, 180, 270])
@pytest.mark.parametrize("bow", [-.025, -.01, 0., .01, .025])
def test_transform_has_analytic_inverse_and_preserves_entire_source(angle, bow):
    value = policy.describe_transform(500, 300, recipe(angle, bow))
    assert value["eligibility"] == "ready"
    assert policy.validate_transform(value, width=500, height=300, recipe=recipe(angle, bow)) == value
    output = value["processed_raster"]
    for x in (0, 50.5, 125, 250, 499.5, 500):
        for y in (0, 50, 150.5, 299.5, 300):
            mapped = policy.source_to_processed([x, y], value)
            assert 0 <= mapped[0] <= output["width"]
            assert -1e-10 <= mapped[1] <= output["height"]
            assert policy.processed_to_source(mapped, value) == pytest.approx([x, y], abs=1e-10)


@pytest.mark.parametrize("angle,expected", [(0, [0., 0.]), (90, [300., 0.]),
                                          (180, [500., 300.]), (270, [0., 500.])])
def test_quarter_turn_top_left_edge_has_independent_expected_location(angle, expected):
    value = policy.describe_transform(500, 300, recipe(angle))
    assert policy.source_to_processed([0, 0], value) == expected


@pytest.mark.parametrize("reason,width,height,bow,limits", [
    ("bow_below_pixel_resolution", 500, 300, .0001, {}),
    ("bow_slope_limit", 100, 600, .025, {}),
    ("expanded_raster_limit", 1000, 6000, .01, {}),
    ("expanded_raster_limit", 500, 300, .01, {"max_pixels": 150000}),
])
def test_geometry_abstention_precedes_image_allocation(reason, width, height, bow, limits):
    value = policy.describe_transform(width, height, recipe(0, bow), **limits)
    assert value["eligibility"] == "abstained" and value["reason"] == reason


@pytest.mark.parametrize("mutant", ["matrix", "bool", "parameters", "size", "reason", "bow", "extra"])
def test_transform_validator_rejects_arbitrary_invertible_or_forged_maps(mutant):
    value = policy.describe_transform(500, 300, recipe(90, .01))
    if mutant == "matrix":
        value["orientation_inverse"][0][2] += 1
    elif mutant == "bool":
        value["orientation_forward"][0][0] = False
    elif mutant == "parameters":
        value["parameters"]["max_edge_segments"] = 500
    elif mutant == "size":
        value["processed_raster"]["width"] += 1
    elif mutant == "reason":
        value["reason"] = "private"
    elif mutant == "bow":
        value["bow"]["amplitude_pixels"] += 1
    else:
        value["extra"] = None
    with pytest.raises(ValueError):
        policy.validate_transform(value, width=500, height=300, recipe=recipe(90, .01))


@pytest.mark.parametrize("angle", [0, 90, 180, 270])
def test_nonlinear_polygon_contains_curved_edge_and_meets_analytic_error_bound(angle):
    value = policy.describe_transform(800, 600, recipe(angle, .025))
    width = value["processed_raster"]["width"]
    box = [[0., 50.], [width, 50.], [width, 100.], [0., 100.]]
    polygon = policy.source_polygon(box, value)
    assert len(polygon) > 4 and len(polygon) <= 128
    amplitude = abs(value["bow"]["amplitude_pixels"])
    segments = math.ceil(math.sqrt(amplitude/.5))
    assert len(polygon) == 2*segments+2
    # Independent quadratic interpolation bound, not inverse-roundtrip alone.
    for index in range(segments):
        left, right = polygon[index], polygon[index+1]
        true_mid = policy.processed_to_source([(index+.5)*width/segments, 50.], value)
        distance = math.dist(true_mid, [(left[0]+right[0])/2, (left[1]+right[1])/2])
        assert distance <= .5+1e-10


def _segment_distance(point, left, right):
    dx, dy = right[0]-left[0], right[1]-left[1]
    denominator = dx*dx+dy*dy
    fraction = (max(0., min(1., ((point[0]-left[0])*dx+(point[1]-left[1])*dy)/denominator))
                if denominator else 0.)
    return math.dist(point, [left[0]+fraction*dx, left[1]+fraction*dy])


@pytest.mark.parametrize("angle", [0, 90, 180, 270])
@pytest.mark.parametrize("bow", [-.025, .025])
@pytest.mark.parametrize("fractional_page", [False, True])
def test_diagonal_edges_crossing_padding_and_physical_page_keep_half_pixel_bound(angle, bow, fractional_page):
    value = policy.describe_transform(800, 600, recipe(angle, bow))
    output = value["processed_raster"]
    width, height = output["width"], output["height"]
    clip = [.8, .7, 799.1, 599.4] if fractional_page else [0., 0., 800., 600.]
    box = [[0., 0.], [width, 100.], [width, height-100.], [0., height]]
    polygon = policy.source_polygon(box, value, source_clip=clip)
    assert len(polygon) <= 128
    for edge in range(4):
        left, right = box[edge], box[(edge+1) % 4]
        for step in range(101):
            fraction = step/100
            expected = policy.processed_to_source([left[0]+fraction*(right[0]-left[0]),
                                                    left[1]+fraction*(right[1]-left[1])], value)
            expected = [max(clip[0], min(clip[2], expected[0])), max(clip[1], min(clip[3], expected[1]))]
            distance = min(_segment_distance(expected, polygon[index], polygon[(index+1) % len(polygon)])
                           for index in range(len(polygon)))
            assert distance <= .5+1e-8


@pytest.mark.parametrize("angle", [0, 90, 180, 270])
@pytest.mark.parametrize("bow", [-.025, .025])
def test_padding_only_ocr_box_cannot_be_claimed_as_source_text(angle, bow):
    value = policy.describe_transform(800, 600, recipe(angle, bow))
    width = value["processed_raster"]["width"]
    left = 0 if bow > 0 else width*.45
    box = [[left, 0.], [left+20, 0.], [left+20, 1.], [left, 1.]]
    with pytest.raises(ValueError, match="source support"):
        policy.source_polygon(box, value)


@pytest.fixture
def imaging():
    return pytest.importorskip("cv2"), pytest.importorskip("numpy")


@pytest.mark.parametrize("angle,position", [(0, (1, 2)), (90, (2, 4)), (180, (4, 6)), (270, (6, 1))])
def test_quarter_turn_moves_distinctive_physical_pixel_exactly_without_resampling(imaging, angle, position):
    from ocr_hardscan_runtime import process_image

    _cv2, np = imaging
    image = np.full((6, 9, 3), 255, np.uint8)
    image[1, 2] = [11, 29, 73]
    before = image.copy()
    result, evidence, transform = process_image(image, recipe=recipe(angle))
    assert result[position].tolist() == [11, 29, 73]
    assert np.count_nonzero(np.any(result != 255, axis=2)) == 1
    assert np.array_equal(image, before) and not np.shares_memory(image, result)
    assert result.flags.c_contiguous and evidence["status"] == "completed"
    mapped = policy.source_to_processed([2.5, 1.5], transform)
    assert mapped == [position[1]+.5, position[0]+.5]


def text_image(imaging):
    cv2, np = imaging
    image = np.full((360, 600, 3), 255, np.uint8)
    for index in range(6):
        cv2.putText(image, "Synthetic record 17: not 40 days.", (25, 50+45*index),
                    cv2.FONT_HERSHEY_SIMPLEX, .7, (0, 0, 0), 1, cv2.LINE_AA)
    return image


@pytest.mark.parametrize("bow", [-.02, .02])
def test_bow_changes_physical_mark_by_declared_curve_and_white_expansion(imaging, bow):
    from ocr_hardscan_runtime import process_image

    cv2, np = imaging
    image = text_image(imaging)
    cv2.rectangle(image, (295, 309), (305, 319), (0, 0, 255), -1)
    before = image.copy()
    result, evidence, transform = process_image(image, recipe=recipe(0, bow))
    assert evidence["status"] == "completed" and np.array_equal(image, before)
    assert result.shape == (368, 600, 3)
    x, y = policy.source_to_processed([300.5, 314.5], transform)
    assert result[round(y-.5), round(x-.5), 2] > 240
    assert result[round(y-.5), round(x-.5), :2].max() < 10
    assert result[0, 0].tolist() == [255, 255, 255]


@pytest.mark.parametrize("pattern", ["white", "black", "noise"])
def test_bow_abstains_on_blank_or_non_text_without_returning_image(imaging, pattern):
    from ocr_hardscan_runtime import process_image

    _cv2, np = imaging
    image = (np.random.default_rng(4).integers(0, 256, (360, 600, 3), dtype=np.uint8)
             if pattern == "noise" else np.full((360, 600, 3), 255 if pattern == "white" else 0, np.uint8))
    before = image.copy()
    result, evidence, _transform = process_image(image, recipe=recipe(0, .02))
    assert result is None and evidence["status"] == "abstained"
    assert np.array_equal(image, before)


def test_illumination_reduces_synthetic_background_gradient_without_mutating_input(imaging):
    from ocr_hardscan_runtime import process_image

    _cv2, np = imaging
    clean = text_image(imaging)
    field = np.linspace(.55, 1., clean.shape[1], dtype=np.float32)
    image = np.rint(clean*field[None, :, None]).astype(np.uint8)
    before = image.copy()
    result, evidence, transform = process_image(image, recipe=recipe(illumination="background-normalize-v1"))
    assert evidence["illumination"]["status"] == "applied"
    assert np.ptp(result[10, 40:-40, 0]) < np.ptp(image[10, 40:-40, 0])/4
    assert np.array_equal(image, before) and result.shape == image.shape
    assert policy.processed_to_source([100, 200], transform) == [100, 200]
    assert result.min() == 0


@pytest.mark.parametrize("pattern,expected", [("blank", "blank_or_low_ink"),
                                             ("uniform", "already_uniform_background")])
def test_illumination_does_not_gratuitously_change_clean_or_blank_image(imaging, pattern, expected):
    from ocr_hardscan_runtime import process_image

    _cv2, np = imaging
    image = np.full((360, 600, 3), 200, np.uint8) if pattern == "blank" else text_image(imaging)
    result, evidence, _ = process_image(image, recipe=recipe(illumination="background-normalize-v1"))
    assert evidence["illumination"]["reason"] == expected and np.array_equal(result, image)


@pytest.mark.parametrize("value", [None, "private", [], True])
def test_invalid_images_are_rejected(imaging, value):
    from ocr_hardscan_runtime import process_image

    with pytest.raises(ValueError):
        process_image(value, recipe=recipe())


def test_budget_abstention_never_calls_native_rotation(imaging, monkeypatch):
    from ocr_hardscan_runtime import process_image

    cv2, np = imaging
    image = np.full((300, 500, 3), 255, np.uint8)
    monkeypatch.setattr(cv2, "rotate", lambda *_args: pytest.fail("native rotation ran"))
    result, evidence, transform = process_image(image, recipe=recipe(180, .02), max_pixels=150000)
    assert result is None and evidence is None and transform["reason"] == "expanded_raster_limit"


def test_import_is_dependency_light():
    code = "import sys,ocr_hardscan; assert not {'cv2','numpy','rapidocr','pymupdf','rag'}.intersection(sys.modules)"
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr
