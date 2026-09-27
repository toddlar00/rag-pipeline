"""Same-crop references and metrics using generated scalar-only report fixtures."""

import copy
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys

import pytest

import ocr_comparison
import ocr_crop_comparison as crops
import ocr_hardscan
import ocr_hardscan_io
import ocr_recovery
import ocr_regions
from test_ocr_regions import candidate


def _digest(payload):
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def _recipe(angle=0, bow=0.):
    return {"orientation_clockwise": angle, "illumination": "none", "bow_fraction": bow,
            "bow_assumption": "parallel_horizontal_baselines" if bow else "none"}


def _recovery(source="a" * 64):
    class Reader:
        page_count = 2

        def native_text(self, _page):
            return "PRIVATE_SAVED_PAGE_CONTEXT is not a crop reference and must never be scored as one."

        def retry(self, _page):
            raise RuntimeError("synthetic prior page failure")

    report = ocr_recovery.build_recovery_report(Reader(), source_sha256=source,
        policy=ocr_recovery.RetryPolicy(), requested_pages=(1, 2))
    report["evidence_sha256"] = None
    return report


def _report(text="not liable", *, operation="regions", dpi=300, bbox=None, region_id="a", page_number=1,
            source="a" * 64, state="available", recipe=None, rotation=0, crop_origin=(0., 0.), rows=None):
    bbox = [.101, .203, .797, .899] if bbox is None else bbox
    recipe = _recipe() if recipe is None else recipe
    recovery = _recovery(source)
    recovery_hash = _digest(recovery)
    plain = rows or [{"region_id": region_id, "page_number": page_number, "bbox": bbox}]
    plan = {"schema_version": 1, "source_sha256": source, "recovery_sha256": recovery_hash,
            "coordinate_system": "original_page_display_fraction", "regions": copy.deepcopy(plain)}
    if operation == "hardscan":
        plan.update(kind="ocr_hardscan_plan", approval="operator_approved")
        for row in plan["regions"]:
            row["recipe"] = copy.deepcopy(recipe)
        plan = ocr_hardscan.validate_plan(plan, source_sha256=source, recovery_sha256=recovery_hash, page_count=2)
    else:
        plan = ocr_regions.validate_region_plan(plan, source_sha256=source, recovery_sha256=recovery_hash, page_count=2)

    class Reader:
        page_count = 2

        def describe_region(self, _number, bounds):
            x0, y0 = math.floor(bounds[0] * dpi), math.floor(bounds[1] * dpi)
            x1, y1 = math.ceil(bounds[2] * dpi), math.ceil(bounds[3] * dpi)
            return {"page": {"display_rect_points": [0., 0., 72., 72.],
                "cropbox_points": [crop_origin[0], crop_origin[1], crop_origin[0] + 72., crop_origin[1] + 72.],
                "rotation_degrees": rotation},
                "raster": {"width": x1 - x0, "height": y1 - y0, "dpi": dpi,
                           "coordinate_system": "rendered_image_pixels"},
                "pixel_bounds": [x0, y0, x1, y1], "crop_to_page_fraction": [[1 / dpi, 0., x0 / dpi], [0., 1 / dpi, y0 / dpi]],
                "boundary_policy": "clip_to_page_bounds"}

        def retry_region(self, number, bounds):
            if state == "failed":
                raise RuntimeError("PRIVATE synthetic crop failure")
            geometry = self.describe_region(number, bounds)
            return {"geometry": geometry, "candidate": candidate(geometry["raster"], text)}

        def describe_hardscan(self, number, bounds, selected_recipe):
            geometry = self.describe_region(number, bounds)
            return {"geometry": geometry, "transform": ocr_hardscan.describe_transform(
                geometry["raster"]["width"], geometry["raster"]["height"], selected_recipe)}

        def retry_hardscan(self, number, bounds, selected_recipe):
            if state == "failed":
                raise RuntimeError("PRIVATE synthetic crop failure")
            value = self.describe_hardscan(number, bounds, selected_recipe)
            processing = {"status": "completed", "reason": None,
                          "libraries": {"opencv": "synthetic", "numpy": "synthetic"},
                          "illumination": {"status": "disabled", "reason": "mode_disabled",
                                           "background_low": None, "background_high": None, "ink_fraction": None}}
            if state == "abstained":
                processing.update(status="abstained", reason="non_text_pattern", illumination=None)
                output = None
            else:
                output = candidate({**value["transform"]["processed_raster"], "dpi": dpi,
                                    "coordinate_system": "hardscan_image_pixels"}, text)
            return {**value, "processing": processing, "candidate": output}

    builder = ocr_regions._build if operation == "regions" else ocr_hardscan_io._build
    report = builder(Reader(), recovery, plan, dpi=dpi)
    report["inputs"] = {"pdf_sha256": source, "recovery_sha256": recovery_hash, "plan_sha256": _digest(plan)}
    validator = ocr_regions.validate_region_review if operation == "regions" else ocr_hardscan_io.validate_report
    return validator(report)


def _reference(report, text="not liable", critical=None, region_id="a"):
    return crops.build_crop_reference(report, anchor_report_sha256=_digest(report), anchor_region_id=region_id,
        reference=text, critical_tokens=[] if critical is None else critical, confirmed=True)


def _compare(before, after, reference, *, before_id="a", after_id="a"):
    return crops.compare_crop_candidates(before, after, reference, baseline_report_sha256=_digest(before),
        retry_report_sha256=_digest(after), baseline_region_id=before_id, retry_region_id=after_id)


def test_different_dpi_same_requested_scope_reuses_exact_existing_metrics():
    before, after = _report("liable"), _report("not liable", dpi=400)
    reference = _reference(before, critical=["not liable"])
    result = _compare(before, after, reference)
    assert result["status"] == "compared" and result["coverage"]["scored_pairs"] == 1
    assert crops.crop_scope(before, region_id="a") == crops.crop_scope(after, region_id="a")
    assert before["regions"][0]["geometry"]["pixel_bounds"] != after["regions"][0]["geometry"]["pixel_bounds"]
    assert result["setting_differences"] == ["dpi"]
    assert {"resolution_differs", "requested_and_effective_edges_differ", "effective_edges_differ_between_runs"} <= set(result["warnings"])
    inputs = [{"schema_version": 1, "records": [{"id": reference["reference_id"], "reference": "not liable",
               "critical_tokens": ["not liable"], "prediction": prediction}]} for prediction in ("liable", "not liable")]
    assert result["comparison"] == ocr_comparison.compare_ocr(*inputs)


@pytest.mark.parametrize("before_operation,after_operation", [("regions", "hardscan"), ("hardscan", "regions"), ("hardscan", "hardscan")])
def test_same_physical_crop_compares_across_routes_and_quarter_turn_recipes(before_operation, after_operation):
    before = _report("liable", operation=before_operation)
    after = _report("not liable", operation=after_operation, dpi=400, recipe=_recipe(90))
    result = _compare(before, after, _reference(before))
    assert result["status"] == "compared"
    assert result["comparison"]["summary"]["outcomes"]["improved"] == 1
    assert result["run_settings"]["retry"]["dpi"] == 400
    assert "recipe_or_route_differs" in result["warnings"]


def test_nominal_and_edge_geometry_are_separate_no_silent_neighbor_filtering():
    before, after = _report("not liable"), _report("not liable liable", dpi=400)
    result = _compare(before, after, _reference(before, critical=["liable"]))
    assert result["comparison"]["regression_detected"] is True
    metrics = result["comparison"]["records"][0]["retry"]
    assert metrics["word"]["insertions"] == 1
    assert metrics["critical_occurrence_totals"]["extra_occurrences"] == 1
    assert result["edge_scopes"]["baseline"]["boundary_expansion"] is True
    assert result["scope"]["bbox"] == [.101, .203, .797, .899]


@pytest.mark.parametrize("options", [{"bbox": [.1, .203, .797, .899]}, {"bbox": [.101, .203, .8, .899]},
    {"page_number": 2}, {"rotation": 90}, {"crop_origin": (1., 0.)}, {"source": "b" * 64}])
def test_different_requested_scope_or_source_refuses_reference_reuse(options):
    before, after = _report(), _report(**options)
    with pytest.raises(ValueError, match="binding"):
        _compare(before, after, _reference(before))


def test_sub_tolerance_bbox_change_is_not_treated_as_same_scope():
    before, after = _report(), _report(bbox=[.101 + 1e-15, .203, .797, .899])
    with pytest.raises(ValueError):
        _compare(before, after, _reference(before))


def test_duplicate_rectangles_require_explicit_anchor_occurrence_not_geometry_guess():
    rows = [{"region_id": name, "page_number": 1, "bbox": [.1, .2, .8, .9]} for name in ("a", "b")]
    before, after = _report(rows=rows), _report(rows=rows, dpi=400)
    first, second = _reference(before), _reference(before, region_id="b")
    assert first["scope"] == second["scope"] and first["reference_id"] != second["reference_id"]
    with pytest.raises(ValueError):
        _compare(before, after, first, before_id="b")
    result = _compare(before, after, second, before_id="b", after_id="a")
    assert result["status"] == "compared"
    assert result["pair"]["baseline"]["region_id"] == "b" and result["pair"]["retry"]["region_id"] == "a"


@pytest.mark.parametrize("before_state,after_state", [("failed", "available"), ("available", "failed"), ("failed", "failed")])
def test_failed_crop_never_becomes_blank_prediction(before_state, after_state):
    before, after = _report(state=before_state), _report(state=after_state)
    result = _compare(before, after, _reference(before))
    assert result["reason"] == "candidate_unavailable" and result["comparison"] is None
    assert result["coverage"]["paired_candidates"] == result["coverage"]["scored_pairs"] == 0
    assert "retry_failed" in {side["status"] for side in result["pair"].values()}


@pytest.mark.parametrize("abstention", ["processing", "geometry"])
def test_hardscan_abstention_keeps_explicit_unavailable(abstention):
    before = _report()
    after = _report(operation="hardscan", state="abstained" if abstention == "processing" else "available",
                    recipe=_recipe(bow=.00001) if abstention == "geometry" else _recipe(bow=.01))
    assert after["regions"][0]["status"] == "abstained"
    result = _compare(before, after, _reference(before))
    assert result["pair"]["retry"]["status"] == "abstained" and result["comparison"] is None


def test_genuine_empty_candidate_is_scored_not_unavailable():
    before, after = _report("not liable"), _report("")
    result = _compare(before, after, _reference(before))
    assert result["status"] == "compared" and result["pair"]["retry"]["status"] == "empty_candidate"
    assert result["comparison"]["summary"]["delta"]["word"]["deletions"] == 2


def test_empty_reviewed_reference_retains_insertion_counts_with_null_rates():
    before, after = _report(""), _report("extra")
    result = _compare(before, after, _reference(before, text=""))
    assert result["comparison"]["records"][0]["delta"]["character"]["error_rate"] is None
    assert result["comparison"]["records"][0]["delta"]["character"]["insertions"] == 5
    assert result["comparison"]["regression_detected"] is True


def test_ordered_critical_entries_and_repeated_occurrence_regressions_remain_visible():
    before, after = _report("not liable not liable x"), _report("not liable liable")
    reference = _reference(before, text="not liable not liable", critical=["not liable", "liable"])
    result = _compare(before, after, reference)
    assert result["comparison"]["regression_detected"] is True
    assert result["comparison"]["records"][0]["delta"]["critical_tokens"][0]["missing_occurrences"] == 1
    assert result["comparison"]["critical_occurrence_semantics"]


def test_missing_ids_and_whole_page_inputs_are_explicit_not_implicit_scope_extraction():
    before, after = _report(), _report(region_id="b")
    result = _compare(before, after, _reference(before))
    assert result["reason"] == "region_missing" and result["coverage"]["scope_matched"] is False
    missing = _compare(before, after, None, before_id="missing", after_id="b")
    assert missing["pair"]["baseline"]["status"] == "region_missing"
    for pages_first in (True, False):
        inputs = (_recovery(), before) if pages_first else (before, _recovery())
        result = _compare(*inputs, None)
        assert result["reason"] == "whole_page_unsupported" and result["comparison"] is None
    with pytest.raises(ValueError):
        crops.crop_scope(_recovery(), region_id="a")


def test_without_reviewed_reference_even_identical_candidates_have_no_accuracy_score():
    report = _report()
    result = _compare(report, report, None)
    assert result["reason"] == "reference_required" and result["comparison"] is None
    assert result["coverage"] == {"requested_pairs": 1, "scope_matched": True, "paired_candidates": 1,
                                  "reference_occurrences": 0, "scored_pairs": 0}


def test_edited_reference_retains_occurrence_but_changes_revision_digest():
    report = _report()
    first, second = _reference(report), _reference(report, text="not LIABLE")
    assert first["reference_id"] == second["reference_id"]
    assert first["reference_sha256"] != second["reference_sha256"]
    first["reference"] = "not LIABLE"
    with pytest.raises(ValueError):
        crops.validate_crop_reference(first, anchor_report=report, anchor_report_sha256=_digest(report))


@pytest.mark.parametrize("confirmation", [False, 1, "true", None])
def test_reference_requires_explicit_exact_boolean_review(confirmation):
    report = _report()
    with pytest.raises(ValueError):
        crops.build_crop_reference(report, anchor_report_sha256=_digest(report), anchor_region_id="a",
            reference="not liable", critical_tokens=[], confirmed=confirmation)


@pytest.mark.parametrize("text,critical", [("not liable", ["absent"]), ("not liable", ["not", "not"]),
    ("not liable", [""]), ("not liable", ["not"] * 65), ("x" * 20001, []), ("\ud800", []),
    ("not liable", [True]), ("not liable", ("not",)), (True, [])])
def test_reference_uses_existing_text_and_critical_phrase_limits(text, critical):
    with pytest.raises(ValueError):
        _reference(_report(), text=text, critical=critical)


@pytest.mark.parametrize("field", ["reference_id", "scope", "anchor", "review", "reference_sha256", "critical_tokens", "extra", "schema_version"])
def test_reference_exact_rebuild_rejects_forged_or_hybrid_fields(field):
    report = _report()
    reference = _reference(report, critical=["not liable", "liable"])
    if field == "scope":
        reference[field]["bbox"][0] = .102
    elif field == "anchor":
        reference[field]["recipe_sha256"] = "f" * 64
    elif field == "schema_version":
        reference[field] = True
    elif field == "critical_tokens":
        reference[field].reverse()
    else:
        reference[field] = "PRIVATE forged value"
    with pytest.raises(ValueError, match="binding"):
        crops.validate_crop_reference(reference, anchor_report=report, anchor_report_sha256=_digest(report))


def test_complete_report_is_validated_including_unselected_other_occurrence():
    rows = [{"region_id": name, "page_number": 1, "bbox": [.1, .2, .8, .9]} for name in ("a", "b")]
    before, after = _report(rows=rows), _report(rows=rows)
    reference = _reference(before)
    after["regions"][1]["candidate"]["raster"]["width"] += 1
    with pytest.raises(ValueError):
        _compare(before, after, reference)


def test_dpi_recipe_and_context_declarations_do_not_leak_engine_version_or_text():
    before, after = _report("PRIVATE_RECOGNIZED_TEXT"), _report("other")
    after["regions"][0]["candidate"]["engine"]["version"] = "PRIVATE_ENGINE"
    result = _compare(before, after, _reference(before, text="PRIVATE_REFERENCE"))
    encoded = json.dumps(result)
    assert "PRIVATE" not in encoded
    assert result["confounders"]["engine_settings_differ"] is True
    assert result["canonical_extraction_modified"] is False and result["requires_attention"] is True


def test_metric_budget_refusal_retains_real_candidate_availability(monkeypatch):
    before, after = _report("alpha"), _report("omega")
    reference = _reference(before, text="other")
    monkeypatch.setattr(ocr_comparison, "MAX_COMPARISON_ALIGNMENT_CELLS", 1)
    result = _compare(before, after, reference)
    assert result["reason"] == "metric_limit" and result["comparison"] is None
    assert result["coverage"]["paired_candidates"] == 1
    assert all(side["status"] == "candidate_available" for side in result["pair"].values())


def test_candidate_text_over_metric_limit_is_not_truncated_or_reclassified():
    before, after = _report(), _report("x" * 20001)
    assert after["regions"][0]["status"] == "review_required"
    result = _compare(before, after, _reference(before))
    assert result["reason"] == "metric_limit" and result["coverage"]["paired_candidates"] == 1


def test_all_inputs_and_returned_scope_reference_are_detached():
    before, after = _report(), _report(dpi=400)
    reference = _reference(before)
    originals = copy.deepcopy((before, after, reference))
    result = _compare(before, after, reference)
    result["scope"]["bbox"].clear()
    result["run_settings"]["baseline"].clear()
    assert (before, after, reference) == originals
    scope = crops.crop_scope(before, region_id="a")
    scope["bbox"].clear()
    assert before == originals[0]


@pytest.mark.parametrize("bad", [None, True, "f" * 63, "F" * 64, "PRIVATE/path"])
def test_caller_digest_must_be_strict_hex(bad):
    report = _report()
    with pytest.raises(ValueError):
        crops.build_crop_reference(report, anchor_report_sha256=bad, anchor_region_id="a",
                                  reference="not liable", critical_tokens=[], confirmed=True)


def test_canonical_scope_validator_is_renderer_ready_and_detached():
    report = _report(rotation=90, crop_origin=(10., 20.))
    scope = crops.crop_scope(report, region_id="a")
    expected = {key: value for key, value in scope.items() if key != "scope_sha256"}
    assert scope["scope_sha256"] == _digest(expected)
    normalized = crops.validate_crop_scope(scope, source_sha256=report["source_sha256"], page_count=2)
    assert normalized == scope and normalized is not scope
    normalized["bbox"][0] += .001
    assert normalized != scope


@pytest.mark.parametrize("fault", ["source", "page_count", "page_number", "coordinate_system", "bbox_bool", "bbox_nan",
    "bbox_order", "rotation_bool", "rotation", "dimensions", "digest", "extra", "numeric_not_canonical"])
def test_standalone_scope_validator_rejects_wrong_geometry_or_digest(fault):
    scope = crops.crop_scope(_report(), region_id="a")
    if fault == "source":
        scope["source_sha256"] = "b" * 64
    elif fault in ("page_count", "page_number"):
        scope[fault] = True
    elif fault == "coordinate_system":
        scope[fault] = "candidate_raster_fraction"
    elif fault.startswith("bbox_"):
        scope["bbox"][0] = {"bbox_bool": True, "bbox_nan": float("nan"), "bbox_order": 1.}[fault]
    elif fault in ("rotation", "rotation_bool"):
        scope["page_geometry"]["rotation_degrees"] = True if fault == "rotation_bool" else 45
    elif fault == "dimensions":
        scope["page_geometry"]["cropbox_points"][2] += 1
    elif fault == "numeric_not_canonical":
        scope["page_geometry"]["cropbox_points"][0] = 0
    elif fault == "digest":
        scope["scope_sha256"] = "b" * 64
    else:
        scope["extra"] = "PRIVATE"
    with pytest.raises(ValueError):
        crops.validate_crop_scope(scope, source_sha256="a" * 64, page_count=2)


@pytest.mark.parametrize("expected", [(True, "a" * 64), (0, "a" * 64), (5001, "a" * 64), (2, True), (2, "b" * 64)])
def test_scope_expected_bindings_are_strict(expected):
    scope = crops.crop_scope(_report(), region_id="a")
    with pytest.raises(ValueError):
        crops.validate_crop_scope(scope, source_sha256=expected[1], page_count=expected[0])


def test_bounded_primitive_input_and_output_before_copy_or_return(monkeypatch):
    report = _report()
    reference = _reference(report)
    monkeypatch.setattr(crops, "MAX_RESULT_BYTES", 16)
    with pytest.raises(ValueError):
        _compare(report, report, reference)
    monkeypatch.setattr(crops, "MAX_JSON_NODES", 2)
    with pytest.raises(ValueError):
        crops.crop_scope(report, region_id="a")


def test_deep_or_cyclic_json_is_rejected_statically():
    cyclic = {}
    cyclic["recursive"] = cyclic
    with pytest.raises(ValueError, match="binding"):
        crops.crop_scope(cyclic, region_id="a")


@pytest.mark.parametrize("value", ["x" * 100001, {"x" * 129: 1}, 1 << 5000], ids=["text", "key", "integer"])
def test_oversized_scalar_and_key_fail_before_encoder_or_copy(monkeypatch, value):
    monkeypatch.setattr(crops.json, "JSONEncoder", lambda **_kw: pytest.fail("oversized scalar reached encoder"))
    with pytest.raises(ValueError):
        crops._bytes(value, crops.MAX_REPORT_BYTES)


@pytest.mark.parametrize("text,limit", [("\x00" * 10, 61), ("é" * 10, 21), ("😀" * 10, 41), ('"' * 10, 21),
                                       ("a" * 20, 21), ("\ud800", 100)])
def test_escaped_and_utf8_fragment_is_bounded_before_encoder(monkeypatch, text, limit):
    monkeypatch.setattr(crops.json, "JSONEncoder", lambda **_kw: pytest.fail("oversized encoded scalar reached encoder"))
    with pytest.raises(ValueError):
        crops._bytes(text, limit)


@pytest.mark.parametrize("text", ["\x00" * 10, "é" * 10, "😀" * 10, '"' * 10, "a" * 20, "\n\t\b\f\r\\"])
def test_exact_encoded_scalar_boundary_is_accepted(text):
    raw = json.dumps(text, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    assert crops._bytes(text, len(raw)) == raw


def test_aggregate_scalar_budget_fails_before_encoder(monkeypatch):
    monkeypatch.setattr(crops.json, "JSONEncoder", lambda **_kw: pytest.fail("aggregate oversized strings reached encoder"))
    with pytest.raises(ValueError):
        crops._bytes(["a" * 20, "b" * 20], 43)


def test_physical_scope_normalizes_signed_zero_without_equating_distinct_occurrences():
    before, after = _report(bbox=[0., 0., 1., 1.]), _report(bbox=[-0., -0., 1., 1.], crop_origin=(-0., -0.))
    first, second = crops.crop_scope(before, region_id="a"), crops.crop_scope(after, region_id="a")
    assert first == second and first["scope_sha256"] == second["scope_sha256"]
    assert "-0.0" not in json.dumps(second)
    assert _compare(before, after, _reference(before))["status"] == "compared"


def test_import_does_not_load_native_ocr_or_ui_dependencies():
    result = subprocess.run([sys.executable, "-c", "import sys; import ocr_crop_comparison; "
        "assert not any(name in sys.modules for name in ('numpy','cv2','pymupdf','rapidocr','onnxruntime','gradio'))"],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
