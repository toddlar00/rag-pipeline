"""Opt-in native scan grouping; generated pixels/geometry only, no OCR/models."""
from __future__ import annotations

import builtins
import copy
import hashlib
import random
from types import SimpleNamespace

import pytest

import ocr_scan_runtime as runtime
from ocr_scan_omission import configuration_for_recipe, validate_scan_observation


@pytest.fixture
def native():
    return SimpleNamespace(cv2=pytest.importorskip("cv2"), np=pytest.importorskip("numpy"),
                           pdf=pytest.importorskip("pymupdf"))


def _config():
    return configuration_for_recipe("spatial-v2")


def _work(rows):
    result = runtime._empty("stage_failed", spatial=True)["work"]
    result["glyph_count"] = len(rows)
    return result


def _linked(left, right):
    x, y, width, height, _ = left
    xx, yy, other_width, other_height, _ = right
    return (max(height, other_height) <= 2.5 * min(height, other_height)
            and min(y + height, yy + other_height) - max(y, yy) >= .45 * min(height, other_height)
            and max(x, xx) - min(x + width, xx + other_width) <= 1.25 * max(height, other_height))


def _compare_edges(rows):
    rows = sorted(rows, key=lambda row: (row[1], row[0], row[3], row[2], row[4]))
    before = copy.deepcopy(rows)
    work = _work(rows)
    pairs = list(runtime._spatial_pairs(rows, configuration=_config(), work=work))
    expected = [(i, j) for i in range(len(rows)) for j in range(i + 1, len(rows)) if _linked(rows[i], rows[j])]
    actual = [(i, j) for i, j in pairs if _linked(rows[i], rows[j])]
    assert actual == expected
    assert pairs == sorted(set(pairs))
    assert work["spatial_phase"] == "grouping" and rows == before
    assert work["spatial_index_entries"] <= 10 * len(rows)
    assert work["spatial_bucket_lookups"] <= 14 * len(rows)
    assert work["spatial_bucket_visits"] <= 10 * len(rows) * (len(rows) - 1) // 2
    return actual


@pytest.mark.parametrize(("rows", "expected"), [
    ([], []), ([(0, 0, 4, 4, 3)], []),
    ([(10, 10, 8, 10, 30), (15, 10, 8, 10, 30)], [(0, 1)]),
    ([(0, 0, 4, 10, 3), (8, 0, 4, 25, 3)], [(0, 1)]),
    ([(0, 0, 4, 10, 3), (8, 0, 4, 26, 3)], []),
    ([(0, 0, 4, 20, 3), (8, 11, 4, 20, 3)], [(0, 1)]),
    ([(0, 0, 4, 20, 3), (8, 12, 4, 20, 3)], []),
    ([(0, 0, 4, 4, 3), (9, 0, 4, 4, 3)], [(0, 1)]),
    ([(0, 0, 4, 4, 3), (10, 0, 4, 4, 3)], []),
    ([(10, 20, 4, 4, 3), (19, 20, 4, 4, 3), (28, 20, 4, 4, 3)], [(0, 1), (1, 2)]),
    ([(128, 128, 12, 10, 3), (128, 128, 12, 10, 4)], [(0, 1)]),
    ([(1, 120, 420, 105, 3), (552, 120, 420, 105, 3)], [(0, 1)]),
    ([(1, 120, 420, 105, 3), (553, 120, 420, 105, 3)], []),
])
def test_exact_candidate_edges_include_boundaries_overlap_transitivity_and_identity(rows, expected):
    assert _compare_edges(rows) == expected


@pytest.mark.parametrize("seed", range(30))
def test_canonical_spatial_edges_match_generated_exhaustive_oracle(seed):
    rng = random.Random(seed)
    rows = []
    for _ in range(10 + seed):
        height = rng.choice((3, 4, 10, 20, 42, 105))
        width = rng.randint(1, 4 * height)
        rows.append((rng.randrange(0, 5500 if seed % 2 else 300), rng.randrange(0, 5500 if seed % 2 else 300),
                     width, height, 3))
    rng.shuffle(rows)
    _compare_edges(rows)


@pytest.mark.parametrize("row", [
    (True, 0, 4, 4, 3), (0, 0, 4, 106, 3), (0, 0, 13, 3, 3),
    (0, 0, 4, 4, 2), (5999, 0, 4, 4, 3), (0, 0, 4, 4, 17),
    [0, 0, 4, 4, 3], (0, 0, 4, 4), (0, 0, 4.0, 4, 3),
])
def test_spatial_index_rejects_broken_integer_glyph_assumptions(row):
    work = _work([row])
    with pytest.raises(ValueError):
        list(runtime._spatial_pairs([row], configuration=_config(), work=work))
    assert work["spatial_index_entries"] == 0


def _gray(native, height=300, width=600):
    return native.np.full((height, width), 255, native.np.uint8)


def _mixed(native):
    gray = _gray(native)
    gray[30:32, 10:590] = 0
    gray[200:202, 10:590] = 0
    for x in (20, 50, 80, 110, 140):
        gray[80:100, x:x + 9] = 0
    gray[180:182, 25:28] = 0
    gray[185:187, 165:168] = 0
    native.cv2.circle(gray, (420, 120), 40, 0, -1)
    return gray


def _discover(gray, native, recipe="spatial-v2", **changes):
    return runtime._discover(gray, configuration=configuration_for_recipe(recipe) | changes,
                             cv2=native.cv2, np=native.np)


def _same_discovery(legacy, spatial):
    assert {key: value for key, value in spatial.items() if key != "work"} == {
        key: value for key, value in legacy.items() if key != "work"}
    for key, value in legacy["work"].items():
        if key != "pair_tests":
            assert spatial["work"][key] == value
    assert len(legacy["work"]) == 5 and len(spatial["work"]) == 10


@pytest.mark.parametrize("case", ["blank", "solid", "mixed", "tiny", "rule"])
def test_complete_native_regions_order_ids_and_ink_accounting_match_v1(native, case):
    gray = _mixed(native) if case == "mixed" else _gray(native)
    if case == "solid":
        gray[:] = 0
    elif case == "tiny":
        gray[50:52, 20:23] = 0
        gray[90:92, 80:83] = 0
    elif case == "rule":
        gray[25:27, 10:590] = 0
    before = gray.copy()
    legacy, spatial = _discover(gray, native, "legacy-v1"), _discover(gray, native)
    _same_discovery(legacy, spatial)
    assert _discover(gray, native) == spatial and native.np.array_equal(gray, before)
    assert spatial["threshold_foreground_pixels"] == sum(row["foreground_pixels"] for row in spatial["regions"])
    if case in {"blank", "solid"}:
        assert spatial["work"]["glyph_count"] is None
        assert spatial["work"]["spatial_phase"] == "not_started"
    else:
        assert spatial["work"]["spatial_phase"] == "complete"
        if case in {"tiny", "rule"}:
            assert spatial["work"]["glyph_count"] == 0
            assert all(spatial["work"][key] == 0 for key in (
                "pair_tests", "spatial_index_entries", "spatial_bucket_lookups", "spatial_bucket_visits"))


@pytest.mark.parametrize("seed", range(15))
def test_native_generated_component_masks_preserve_whole_discovery(native, seed):
    rng = random.Random(seed)
    gray = _gray(native)
    for _ in range(40):
        x, y = rng.randrange(20, 550), rng.randrange(20, 250)
        width, height = rng.randrange(1, 15), rng.randrange(1, 35)
        gray[y:y + height, x:x + width] = 0
    if seed % 2:
        gray[10:12, 10:590] = 0
    _same_discovery(_discover(gray, native, "legacy-v1"), _discover(gray, native))


@pytest.mark.parametrize("nontext", [False, True])
def test_physically_separate_1600_components_avoid_old_pair_preflight_abstention(native, nontext):
    gray = _gray(native, 200 if nontext else 1320, 250 if nontext else 1320)
    for i in range(1600):
        if nontext:
            x, y = 10 + (i % 50) * 4, 10 + (i // 50) * 5
            gray[y:y + 3, x:x + 1] = 0
        else:
            native.cv2.putText(gray, "E", (15 + (i % 40) * 32, 20 + (i // 40) * 32),
                               native.cv2.FONT_HERSHEY_SIMPLEX, .45, 0, 1, native.cv2.LINE_8)
    before = hashlib.sha256(gray.tobytes()).hexdigest()
    legacy, spatial = _discover(gray, native, "legacy-v1"), _discover(gray, native)
    assert legacy["reason"] == "component_pair_limit" and legacy["work"]["pair_tests"] == 0
    assert legacy["work"]["residual_component_count"] == 1600
    assert spatial["status"] == "available" and spatial["work"]["glyph_count"] == 1600
    assert spatial["work"]["spatial_phase"] == "complete"
    assert spatial["threshold_foreground_pixels"] == legacy["threshold_foreground_pixels"]
    assert sum(item["component_count"] for item in spatial["regions"]) == 1600
    assert sum(item["foreground_pixels"] for item in spatial["regions"]) == spatial["threshold_foreground_pixels"]
    assert spatial["work"]["pair_tests"] < 1_000_000
    assert hashlib.sha256(gray.tobytes()).hexdigest() == before
    if nontext:
        # Physically realizable false-positive control remains visible.
        assert any(item["kind"] == "text_like" for item in spatial["regions"])


@pytest.mark.parametrize(("change", "reason", "phase", "counter"), [
    ({"max_index_entries_per_page": 1}, "stage_failed", "indexing", "spatial_index_entries"),
    ({"max_bucket_lookups_per_page": 1}, "stage_failed", "querying", "spatial_bucket_lookups"),
    ({"max_bucket_visits_per_page": 1}, "bucket_visit_limit", "querying", "spatial_bucket_visits"),
    ({"max_pairs_per_page": 1}, "component_pair_limit", "querying", "pair_tests"),
    ({"max_regions_per_page": 0}, "region_limit", "grouping", None),
])
def test_budget_precharge_retains_phase_and_counters_but_no_partial_regions(native, change, reason, phase, counter):
    result = _discover(_mixed(native), native, **change)
    assert result["status"] == "unavailable" and result["reason"] == reason
    assert result["regions"] == [] and result["foreground_accounting_complete"] is False
    assert result["work"]["spatial_phase"] == phase
    if counter:
        assert result["work"][counter] == 1


@pytest.mark.parametrize("recipe", [None, True, 1, "spatial", "SPATIAL-V2", [], {}])
def test_recipe_admission_precedes_all_optional_native_imports(monkeypatch, recipe):
    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name in {"cv2", "numpy", "pymupdf"}:
            pytest.fail("native import occurred before recipe validation")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    with pytest.raises(ValueError):
        runtime.collect_scan_observation(b"generated non-PDF", requested_pages=[1], recipe=recipe)


def _pdf(native):
    with native.pdf.open() as document:
        page = document.new_page(width=144, height=144)
        page.insert_text((15, 30), "Generated text 30", fontsize=8)
        page.draw_line((10, 90), (134, 90))
        return document.tobytes()


def test_full_generated_pdf_default_is_exact_v1_and_spatial_observation_is_strict_v2(native):
    source = _pdf(native)
    before = hashlib.sha256(source).hexdigest()
    default = runtime.collect_scan_observation(source, requested_pages=[1])
    legacy = runtime.collect_scan_observation(source, requested_pages=[1], recipe="legacy-v1")
    spatial = runtime.collect_scan_observation(source, requested_pages=[1], recipe="spatial-v2")
    assert default == legacy and legacy["schema_version"] == 1 and spatial["schema_version"] == 2
    assert spatial["pages"][0]["raster"] == legacy["pages"][0]["raster"]
    assert spatial["pages"][0]["geometry"] == legacy["pages"][0]["geometry"]
    _same_discovery(legacy["pages"][0]["discovery"], spatial["pages"][0]["discovery"])
    assert validate_scan_observation(spatial) == spatial
    assert hashlib.sha256(source).hexdigest() == before


def test_spatial_failure_before_raster_has_unstarted_work_and_cancellation_propagates(native, monkeypatch):
    source = _pdf(native)

    def fail(*_args, **_kwargs):
        raise RuntimeError("PRIVATE_RENDER_DETAILS")

    monkeypatch.setattr(native.pdf.Page, "get_pixmap", fail)
    result = runtime.collect_scan_observation(source, requested_pages=[1], recipe="spatial-v2")
    assert result["pages"][0]["discovery"] == runtime._empty("render_failed", spatial=True)
    assert validate_scan_observation(result) == result

    def cancel(*_args, **_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(native.pdf.Page, "get_pixmap", cancel)
    with pytest.raises(KeyboardInterrupt):
        runtime.collect_scan_observation(source, requested_pages=[1], recipe="spatial-v2")


def test_cancel_during_spatial_query_propagates_instead_of_partial_result(native, monkeypatch):
    def cancel(*_args, **_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(runtime.bisect, "bisect_right", cancel)
    with pytest.raises(KeyboardInterrupt):
        _discover(_mixed(native), native)


def test_parent_allocation_failure_is_valid_indexing_abstention_not_cohort_failure(native, monkeypatch):
    with native.pdf.open(stream=_pdf(native), filetype="pdf") as document:
        document.new_page(width=144, height=144)
        source = document.tobytes()

    def fail_parent(value):
        if type(value) is range:
            raise MemoryError("PRIVATE_ALLOCATION_DETAILS")
        return builtins.list(value)

    original_discover = runtime._discover

    def injected(gray, **kwargs):
        with monkeypatch.context() as local:
            local.setattr(runtime, "list", fail_parent, raising=False)
            return original_discover(gray, **kwargs)

    monkeypatch.setattr(runtime, "_discover", injected)
    result = runtime.collect_scan_observation(source, requested_pages=[1, 2], recipe="spatial-v2")
    first = result["pages"][0]["discovery"]
    assert first["reason"] == "stage_failed" and first["regions"] == []
    assert first["work"]["glyph_count"] > 0 and first["work"]["spatial_phase"] == "indexing"
    assert first["work"]["spatial_index_entries"] == 0
    assert result["pages"][1]["discovery"]["status"] == "blank_at_threshold"
    assert validate_scan_observation(result) == result
