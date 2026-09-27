"""Declared spatial work controls; no pixels, native runtime, OCR, or proof claims."""

import copy
import json

import pytest

import ocr_scan_omission as policy


LEGACY = {
    "algorithm": "dark-ink-component-hypotheses-v1", "dpi": 300,
    "coordinate_system": "original_displayed_page_raster_pixels",
    "pixel_digest_domain": "rag-pipeline:ocr-scan-gray8:v1", "render_annotations": True,
    "max_pages": 8, "max_side": 6000, "max_page_pixels": 25_000_000,
    "max_total_pixels": 100_000_000, "max_horizontal_runs_per_mask": 100_000,
    "max_components_per_page": 10_000, "max_pairs_per_page": 1_000_000,
    "max_total_pairs": 8_000_000, "max_regions_per_page": 5000,
    "dense_foreground_fraction": .30, "rule_length_pixels": 75,
    "glyph_min_height_pixels": 3, "glyph_max_height_pixels": 105, "glyph_min_area_pixels": 3,
    "glyph_max_width_to_height": 4., "glyph_max_height_ratio": 2.5,
    "neighbor_gap_in_max_heights": 1.25, "minimum_vertical_overlap": .45,
    "minimum_group_components": 3, "minimum_group_width_to_height": 2.,
    "threshold": "global_otsu_dark_foreground_no_blur_no_rescale",
    "connectivity": 8, "component_order": "top_left_then_size_then_area",
}
SPATIAL = LEGACY | {
    "algorithm": "dark-ink-spatial-component-hypotheses-v2",
    "neighbor_enumeration": "closed_aabb_grid_canonical_unique_pairs", "spatial_cell_pixels": 128,
    "max_index_entries_per_page": 100_000, "max_bucket_lookups_per_page": 140_000,
    "max_bucket_visits_per_page": 10_000_000, "max_total_index_entries": 800_000,
    "max_total_bucket_lookups": 1_120_000, "max_total_bucket_visits": 80_000_000,
}
OLD_KEYS = {"rule_horizontal_runs", "residual_horizontal_runs", "rule_component_count",
            "residual_component_count", "pair_tests"}
SPATIAL_KEYS = {"glyph_count", "spatial_phase", "spatial_index_entries",
                "spatial_bucket_lookups", "spatial_bucket_visits"}


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def observation(*, spatial=True, pages=(1,), page_count=3):
    records = []
    for number in pages:
        work = {"rule_horizontal_runs": 0, "residual_horizontal_runs": 30,
                "rule_component_count": 0, "residual_component_count": 3, "pair_tests": 3}
        if spatial:
            work.update(glyph_count=3, spatial_phase="complete", spatial_index_entries=6,
                        spatial_bucket_lookups=12, spatial_bucket_visits=3)
        records.append({"page_number": number,
            "geometry": {"width_points": 240., "height_points": 240., "rotation": 0,
                         "cropbox": [0., 0., 240., 240.]},
            "raster": {"width": 1000, "height": 1000, "dpi": 300, "format": "gray8", "pixel_sha256": "d" * 64},
            "discovery": {"status": "available", "reason": "bounded_pixel_discovery", "threshold": 128.,
                "threshold_foreground_pixels": 90, "foreground_accounting_complete": True, "work": work,
                "regions": [{"region_id": "scan-00001", "kind": "text_like",
                    "reason": "aligned_glyph_scale_components_not_text_proof", "bbox": [100, 100, 300, 120],
                    "component_count": 3, "foreground_pixels": 90}]}})
    return {"schema_version": 2 if spatial else 1, "kind": "ocr_scan_observation", "source_sha256": "a" * 64,
            "page_count": page_count, "requested_pages": list(pages),
            "configuration": copy.deepcopy(SPATIAL if spatial else LEGACY), "pages": records}


def discovery(value):
    return value["pages"][0]["discovery"]


def work(value):
    return discovery(value)["work"]


def early(value, *, status="unavailable", reason="render_failed"):
    page = value["pages"][0]
    if status == "unavailable":
        page["raster"] = None
    record = discovery(value)
    record.update(status=status, reason=reason, threshold=None, threshold_foreground_pixels=None,
                  foreground_accounting_complete=status != "unavailable", regions=[])
    record["work"] = {key: 0 if key == "pair_tests" else None for key in OLD_KEYS}
    record["work"].update(glyph_count=None, spatial_phase="not_started", spatial_index_entries=0,
                          spatial_bucket_lookups=0, spatial_bucket_visits=0)
    return value


def failed(*, phase, reason="stage_failed", glyph_count=3, entries=0, lookups=0, visits=0, pairs=0):
    value = observation()
    record = discovery(value)
    record.update(status="unavailable", reason=reason, foreground_accounting_complete=False,
                  regions=[], threshold_foreground_pixels=3 * glyph_count)
    work(value).update(rule_horizontal_runs=0, rule_component_count=0,
                       residual_horizontal_runs=glyph_count, residual_component_count=glyph_count,
                       glyph_count=glyph_count, spatial_phase=phase, spatial_index_entries=entries,
                       spatial_bucket_lookups=lookups, spatial_bucket_visits=visits, pair_tests=pairs)
    return value


def budget_failure(reason):
    if reason == "component_pair_limit":
        return failed(phase="querying", reason=reason, glyph_count=1415, entries=1415,
                      lookups=4242, visits=1_000_001, pairs=1_000_000)
    if reason == "bucket_visit_limit":
        return failed(phase="querying", reason=reason, glyph_count=2000, entries=20_000,
                      lookups=28_000, visits=10_000_000, pairs=1234)
    if reason == "region_limit":
        return failed(phase="grouping", reason=reason, glyph_count=5001, entries=5001, lookups=15_003)
    raise AssertionError("unknown test reason")


def partitioned():
    value = observation()
    record = discovery(value)
    glyph = record["regions"][0]
    glyph["region_id"] = "scan-00002"
    record["regions"] = [
        {"region_id": "scan-00001", "kind": "rule_like", "reason": "long_axis_morphology_not_semantic_nontext",
         "bbox": [10, 10, 150, 15], "component_count": 1, "foreground_pixels": 80},
        glyph,
        {"region_id": "scan-00003", "kind": "ambiguous_ink", "reason": "tiny_or_large_residual_component",
         "bbox": [500, 500, 501, 501], "component_count": 1, "foreground_pixels": 1},
    ]
    record["threshold_foreground_pixels"] = 171
    work(value).update(rule_horizontal_runs=1, rule_component_count=1,
                       residual_horizontal_runs=31, residual_component_count=4)
    return value


def test_default_recipe_and_legacy_work_contract_are_unchanged():
    assert canonical(policy.DEFAULT_CONFIGURATION) == canonical(LEGACY)
    assert canonical(policy.configuration_for_recipe()) == canonical(LEGACY)
    assert canonical(policy.configuration_for_recipe("legacy-v1")) == canonical(LEGACY)
    value = observation(spatial=False)
    # Preserve historically accepted v1 declared-work semantics, not v2 rules.
    work(value)["pair_tests"] = 0
    assert policy.validate_scan_observation(value) == value
    assert set(work(value)) == OLD_KEYS


@pytest.mark.parametrize("name,configuration", [("legacy-v1", LEGACY), ("spatial-v2", SPATIAL)])
def test_recipe_lookup_is_exact_and_detached(name, configuration):
    result = policy.configuration_for_recipe(name)
    assert canonical(result) == canonical(configuration)
    assert policy.recipe_for_configuration(result) == name
    result["dpi"] = 1
    assert canonical(policy.configuration_for_recipe(name)) == canonical(configuration)
    assert canonical(policy.DEFAULT_CONFIGURATION) == canonical(LEGACY)


@pytest.mark.parametrize("value", [None, True, 1, 2., "", "legacy", "spatial", "SPATIAL-V2", "spatial-v2 ", [], {}])
def test_unknown_or_non_string_recipe_is_rejected(value):
    with pytest.raises(ValueError):
        policy.configuration_for_recipe(value)


@pytest.mark.parametrize("value", [None, True, [], "legacy-v1", {}, {"algorithm": SPATIAL["algorithm"]}])
def test_configuration_requires_a_complete_known_recipe(value):
    with pytest.raises(ValueError):
        policy.recipe_for_configuration(value)


@pytest.mark.parametrize("field,value", [
    ("algorithm", LEGACY["algorithm"]), ("dpi", 300.), ("spatial_cell_pixels", 128.),
    ("render_annotations", 1), ("max_bucket_visits_per_page", 9_999_999),
    ("max_total_bucket_visits", 80_000_001), ("max_index_entries_per_page", True),
    ("neighbor_enumeration", "approximate_neighbors"), ("neighbor_gap_in_max_heights", 2.),
    ("glyph_max_height_pixels", 106), ("spatial_cell_pixels", float("nan")),
])
def test_spatial_recipe_rejects_hybrid_and_type_coerced_parameters(field, value):
    configuration = copy.deepcopy(SPATIAL)
    configuration[field] = value
    with pytest.raises(ValueError):
        policy.recipe_for_configuration(configuration)
    candidate = observation()
    candidate["configuration"] = configuration
    with pytest.raises(ValueError):
        policy.validate_scan_observation(candidate)


@pytest.mark.parametrize("name", ["legacy-v1", "spatial-v2"])
@pytest.mark.parametrize("change", ["extra", "missing"])
def test_recipe_fields_are_closed(name, change):
    value = policy.configuration_for_recipe(name)
    if change == "extra":
        value["trusted"] = True
    else:
        del value["algorithm"]
    with pytest.raises(ValueError):
        policy.recipe_for_configuration(value)


@pytest.mark.parametrize("spatial,version", [(False, 2), (True, 1), (True, True), (True, 2.), (True, 3)])
def test_observation_schema_is_exclusive_to_its_recipe(spatial, version):
    value = observation(spatial=spatial)
    value["schema_version"] = version
    with pytest.raises(ValueError):
        policy.validate_scan_observation(value)


def test_spatial_observation_is_detached_and_diagnostic_schema_stays_one():
    value = partitioned()
    before = copy.deepcopy(value)
    result = policy.validate_scan_observation(value)
    report = policy.build_scan_omission_report(value)
    assert result == value and report["schema_version"] == 1
    assert report["summary"]["requested_pages"] == 1
    assert report["summary"]["text_like_regions"] == report["summary"]["rule_like_regions"] == report["summary"]["ambiguous_ink_regions"] == 1
    assert report["observation_authenticity_verified"] is report["accuracy_verified"] is report["full_page_coverage_verified"] is False
    assert report["recognition_rerun"] is report["canonical_extraction_modified"] is False
    assert report["requires_attention"] is report["operator_review_required"] is True
    assert policy.validate_scan_omission_report(report, value) == report
    result["configuration"]["dpi"] = 1
    result["pages"][0]["discovery"]["work"]["glyph_count"] = 0
    report["pages"][0]["regions"][0]["bbox"][0] = 0
    assert value == before


@pytest.mark.parametrize("field", sorted(SPATIAL_KEYS))
@pytest.mark.parametrize("action", ["missing_v2", "extra_v1"])
def test_recipe_specific_work_shape_is_closed(field, action):
    value = observation(spatial=action == "missing_v2")
    if action == "missing_v2":
        del work(value)[field]
    else:
        work(value)[field] = work(observation())[field]
    with pytest.raises(ValueError):
        policy.validate_scan_observation(value)


@pytest.mark.parametrize("field", ["glyph_count", "spatial_index_entries", "spatial_bucket_lookups", "spatial_bucket_visits", "pair_tests"])
@pytest.mark.parametrize("value", [True, 1., -1, float("nan"), float("inf"), 10 ** 1000])
def test_spatial_counters_reject_non_integer_or_unbounded_values(field, value):
    candidate = observation()
    work(candidate)[field] = value
    with pytest.raises(ValueError):
        policy.validate_scan_observation(candidate)


@pytest.mark.parametrize("phase", [None, True, 1, "", "done", "not_started", "indexing", "querying", "grouping"])
def test_available_requires_complete_enumeration(phase):
    value = observation()
    work(value)["spatial_phase"] = phase
    with pytest.raises(ValueError):
        policy.validate_scan_observation(value)


@pytest.mark.parametrize("field,value", [
    ("glyph_count", None), ("glyph_count", 0), ("glyph_count", 2), ("glyph_count", 4),
    ("spatial_index_entries", 2), ("spatial_index_entries", 31),
    ("spatial_bucket_lookups", 8), ("spatial_bucket_lookups", 43),
    ("spatial_bucket_visits", 2), ("spatial_bucket_visits", 31),
    ("pair_tests", 1), ("pair_tests", 4),
])
def test_complete_work_respects_count_geometry_and_group_union_bounds(field, value):
    candidate = observation()
    work(candidate)[field] = value
    with pytest.raises(ValueError):
        policy.validate_scan_observation(candidate)


@pytest.mark.parametrize("entries,lookups,visits,pairs", [(3, 9, 3, 3), (30, 42, 30, 3), (6, 12, 2, 2)])
def test_complete_work_accepts_inclusive_bounds(entries, lookups, visits, pairs):
    value = observation()
    work(value).update(spatial_index_entries=entries, spatial_bucket_lookups=lookups,
                       spatial_bucket_visits=visits, pair_tests=pairs)
    assert policy.validate_scan_observation(value) == value


def test_coarse_bucket_visits_without_predicate_calls_are_not_falsely_rejected():
    value = observation()
    record = discovery(value)
    record["regions"] = [{"region_id": f"scan-{i + 1:05d}", "kind": "ambiguous_ink",
        "reason": "isolated_or_non_line_components", "bbox": [100 + i * 100, 100, 104 + i * 100, 110],
        "component_count": 1, "foreground_pixels": 30} for i in range(3)]
    work(value).update(pair_tests=0, spatial_bucket_visits=2)
    assert policy.validate_scan_observation(value) == value


def test_zero_glyph_rule_only_page_completes_without_spatial_work():
    value = partitioned()
    record = discovery(value)
    record["regions"] = record["regions"][:1]
    record["threshold_foreground_pixels"] = 80
    work(value).update(residual_component_count=0, residual_horizontal_runs=0, glyph_count=0,
                       pair_tests=0, spatial_index_entries=0, spatial_bucket_lookups=0, spatial_bucket_visits=0)
    assert policy.validate_scan_observation(value) == value
    for field in ("pair_tests", "spatial_index_entries", "spatial_bucket_lookups", "spatial_bucket_visits"):
        changed = copy.deepcopy(value)
        work(changed)[field] = 1
        with pytest.raises(ValueError):
            policy.validate_scan_observation(changed)


@pytest.mark.parametrize("alteration", ["rule_count", "residual_count", "rule_not_singleton", "tiny_not_singleton", "group_count"])
def test_available_component_partition_cannot_be_relabelled_or_rebalanced(alteration):
    value = partitioned()
    record = discovery(value)
    if alteration == "rule_count":
        work(value).update(rule_component_count=2, rule_horizontal_runs=2, residual_component_count=3)
    elif alteration == "residual_count":
        work(value).update(rule_component_count=0, residual_component_count=5)
    elif alteration == "rule_not_singleton":
        record["regions"][0]["component_count"] = 2
        record["regions"][1]["component_count"] = 2
        record["regions"][1].update(kind="ambiguous_ink", reason="isolated_or_non_line_components")
        work(value).update(rule_component_count=2, rule_horizontal_runs=2, residual_component_count=3, glyph_count=2)
    elif alteration == "tiny_not_singleton":
        record["regions"][2]["component_count"] = 2
        record["regions"][1]["component_count"] = 2
        record["regions"][1].update(kind="ambiguous_ink", reason="isolated_or_non_line_components")
        work(value)["glyph_count"] = 2
    else:
        record["regions"][1]["component_count"] = 4
        record["regions"][2]["component_count"] = 0
    with pytest.raises(ValueError):
        policy.validate_scan_observation(value)


@pytest.mark.parametrize("status,reason", [("unavailable", "render_failed"),
    ("blank_at_threshold", "no_dark_otsu_foreground"), ("ambiguous", "dense_foreground_not_text_classified")])
def test_early_states_have_no_fabricated_glyph_or_spatial_work(status, reason):
    value = early(observation(), status=status, reason=reason)
    record = discovery(value)
    if status == "blank_at_threshold":
        record.update(threshold=0, threshold_foreground_pixels=0)
    elif status == "ambiguous":
        record.update(threshold=128., threshold_foreground_pixels=500_000,
            regions=[{"region_id": "scan-00001", "kind": "ambiguous_ink", "reason": "dense_foreground",
                      "bbox": [0, 0, 1000, 1000], "component_count": None, "foreground_pixels": 500_000}])
    assert policy.validate_scan_observation(value) == value
    for field, wrong in (("glyph_count", 0), ("spatial_phase", "complete"), ("spatial_index_entries", 1),
                         ("spatial_bucket_lookups", 1), ("spatial_bucket_visits", 1), ("pair_tests", 1)):
        changed = copy.deepcopy(value)
        work(changed)[field] = wrong
        with pytest.raises(ValueError):
            policy.validate_scan_observation(changed)


@pytest.mark.parametrize("kwargs", [
    {"phase": "indexing", "entries": 1},
    {"phase": "querying", "entries": 6, "lookups": 3, "visits": 1, "pairs": 1},
    {"phase": "grouping", "entries": 6, "lookups": 12, "visits": 3, "pairs": 3},
])
def test_failed_stage_retains_bounded_work_but_never_partial_regions(kwargs):
    value = failed(**kwargs)
    assert policy.validate_scan_observation(value) == value
    report = policy.build_scan_omission_report(value)
    assert report["summary"]["unavailable_pages"] == 1
    assert report["pages"][0]["regions"] == []
    assert report["pages"][0]["ocr_comparison_state"] == "scan_unavailable"
    discovery(value)["regions"] = discovery(observation())["regions"]
    with pytest.raises(ValueError):
        policy.validate_scan_observation(value)


@pytest.mark.parametrize("kwargs", [
    {"phase": "indexing", "entries": 1, "lookups": 1},
    {"phase": "indexing", "entries": 1, "visits": 1},
    {"phase": "indexing", "entries": 1, "pairs": 1},
    {"phase": "querying", "entries": 2, "lookups": 3},
    {"phase": "grouping", "entries": 6, "lookups": 8},
    {"phase": "not_started", "entries": 0},
])
def test_failed_phase_cannot_skip_preceding_work_or_fabricate_later_work(kwargs):
    with pytest.raises(ValueError):
        policy.validate_scan_observation(failed(**kwargs))


@pytest.mark.parametrize("reason", ["component_pair_limit", "bucket_visit_limit", "region_limit"])
def test_genuine_budget_failure_is_unavailable_and_not_an_empty_success(reason):
    value = budget_failure(reason)
    assert policy.validate_scan_observation(value) == value
    report = policy.build_scan_omission_report(value)
    assert report["pages"][0]["scan_status"] == "unavailable"
    assert report["pages"][0]["scan_reason"] == reason
    assert report["pages"][0]["regions"] == []
    assert report["summary"]["unavailable_pages"] == 1
    assert report["summary"]["blank_at_threshold_pages"] == 0
    assert report["requires_attention"] is True


@pytest.mark.parametrize("reason", ["component_pair_limit", "bucket_visit_limit", "region_limit"])
@pytest.mark.parametrize("phase", ["not_started", "indexing", "complete"])
def test_budget_failure_phase_must_match_interruption(reason, phase):
    value = budget_failure(reason)
    work(value)["spatial_phase"] = phase
    with pytest.raises(ValueError):
        policy.validate_scan_observation(value)


@pytest.mark.parametrize("reason,counter", [("component_pair_limit", "pair_tests"), ("bucket_visit_limit", "spatial_bucket_visits")])
def test_budget_exhaustion_requires_exact_measured_cap(reason, counter):
    value = budget_failure(reason)
    work(value)[counter] -= 1
    with pytest.raises(ValueError):
        policy.validate_scan_observation(value)


def test_pair_limit_must_have_an_unprocessed_candidate_not_just_an_exact_cap():
    value = budget_failure("component_pair_limit")
    work(value)["spatial_bucket_visits"] = 1_000_000
    with pytest.raises(ValueError):
        policy.validate_scan_observation(value)


@pytest.mark.parametrize("reason", ["component_pair_limit", "bucket_visit_limit"])
def test_counter_limits_cannot_claim_more_distinct_or_repeated_pairs_than_exist(reason):
    value = budget_failure(reason)
    work(value).update(glyph_count=1414, residual_component_count=1414, residual_horizontal_runs=1414,
                       spatial_index_entries=1414, spatial_bucket_lookups=4242)
    with pytest.raises(ValueError):
        policy.validate_scan_observation(value)


def test_region_limit_requires_possible_over_limit_groups_after_full_queries():
    value = budget_failure("region_limit")
    work(value).update(glyph_count=5000, residual_component_count=5000, residual_horizontal_runs=5000,
                       spatial_index_entries=5000, spatial_bucket_lookups=15_000)
    with pytest.raises(ValueError):
        policy.validate_scan_observation(value)


@pytest.mark.parametrize("reason", ["index_entry_limit", "bucket_lookup_limit", "spatial_index_limit"])
def test_unreachable_defensive_guard_does_not_add_an_unauthorized_reason(reason):
    value = failed(phase="indexing", entries=1, reason=reason)
    with pytest.raises(ValueError):
        policy.validate_scan_observation(value)


def test_eight_page_cohort_preserves_each_failure_and_aggregate_work_bounds():
    value = observation(pages=tuple(range(1, 9)), page_count=8)
    template = budget_failure("bucket_visit_limit")["pages"][0]
    for page in value["pages"]:
        page["discovery"] = copy.deepcopy(template["discovery"])
    validated = policy.validate_scan_observation(value)
    assert sum(page["discovery"]["work"]["spatial_bucket_visits"] for page in validated["pages"]) == 80_000_000
    report = policy.build_scan_omission_report(value)
    assert report["summary"]["unavailable_pages"] == 8
    assert report["coverage"]["requested_pages"] == list(range(1, 9))
    assert all(page["regions"] == [] for page in report["pages"])
    value["pages"][0]["discovery"]["work"]["spatial_bucket_visits"] += 1
    with pytest.raises(ValueError):
        policy.validate_scan_observation(value)


def component_failure(reason="stage_failed", foreground=90):
    value = observation()
    raster = copy.deepcopy(value["pages"][0]["raster"])
    early(value, reason=reason)
    value["pages"][0]["raster"] = raster
    discovery(value).update(threshold=128., threshold_foreground_pixels=foreground)
    return value


@pytest.mark.parametrize("foreground", [0, 300_001])
def test_component_counters_cannot_follow_a_blank_or_dense_threshold_branch(foreground):
    value = component_failure(foreground=foreground)
    work(value).update(rule_horizontal_runs=0, rule_component_count=0)
    with pytest.raises(ValueError):
        policy.validate_scan_observation(value)


def test_disjoint_mask_run_counts_cannot_exceed_total_foreground_support():
    value = component_failure(foreground=5)
    work(value).update(rule_horizontal_runs=3, rule_component_count=1,
                       residual_horizontal_runs=3, residual_component_count=1)
    with pytest.raises(ValueError):
        policy.validate_scan_observation(value)


@pytest.mark.parametrize("phase", ["indexing", "querying", "grouping"])
def test_classified_glyphs_require_minimum_pixel_support_even_when_unavailable(phase):
    value = failed(phase=phase, entries=6 if phase != "indexing" else 0,
                   lookups=12 if phase == "grouping" else 0)
    discovery(value)["threshold_foreground_pixels"] = 8
    with pytest.raises(ValueError):
        policy.validate_scan_observation(value)


def test_classified_support_includes_rule_and_non_glyph_singletons():
    value = failed(phase="indexing")
    discovery(value)["threshold_foreground_pixels"] = 10
    work(value).update(rule_component_count=1, rule_horizontal_runs=1,
                       residual_component_count=4, residual_horizontal_runs=4)
    with pytest.raises(ValueError):
        policy.validate_scan_observation(value)


@pytest.mark.parametrize("prefix", ["rule", "residual"])
def test_measured_run_excess_before_component_allocation_is_honest_failure(prefix):
    value = component_failure("horizontal_run_limit", foreground=100_001)
    if prefix == "residual":
        work(value).update(rule_horizontal_runs=0, rule_component_count=0)
    work(value)[prefix + "_horizontal_runs"] = 100_001
    assert policy.validate_scan_observation(value) == value
    assert work(value)[prefix + "_component_count"] is None
    work(value)[prefix + "_horizontal_runs"] -= 1
    with pytest.raises(ValueError):
        policy.validate_scan_observation(value)


@pytest.mark.parametrize("combined", [False, True])
def test_measured_component_excess_is_preserved_without_claiming_classification(combined):
    value = component_failure("component_limit", foreground=11_000 if combined else 10_001)
    if combined:
        work(value).update(rule_horizontal_runs=6000, rule_component_count=6000,
                           residual_horizontal_runs=5000, residual_component_count=5000)
    else:
        work(value).update(rule_horizontal_runs=10_001, rule_component_count=10_001)
    assert policy.validate_scan_observation(value) == value
    assert work(value)["glyph_count"] is None and work(value)["spatial_phase"] == "not_started"


def test_eligible_group_cannot_be_downgraded_to_ambiguous_in_v2():
    value = observation()
    discovery(value)["regions"][0].update(kind="ambiguous_ink", reason="isolated_or_non_line_components")
    with pytest.raises(ValueError):
        policy.validate_scan_observation(value)


def test_v2_successful_group_support_cannot_be_less_than_admitted_glyph_area():
    value = observation()
    discovery(value)["threshold_foreground_pixels"] = 8
    discovery(value)["regions"][0]["foreground_pixels"] = 8
    work(value)["residual_horizontal_runs"] = 3
    with pytest.raises(ValueError):
        policy.validate_scan_observation(value)
