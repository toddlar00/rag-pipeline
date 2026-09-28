"""Synthetic stage artifacts; not execution receipts or reviewed source claims."""

import copy
import json

import pytest

import ocr_stage_diagnostics as stage


SOURCE, REFERENCE, OBSERVATION = "a" * 64, "b" * 64, "c" * 64


def reference():
    return {"schema_version": 1, "kind": "ocr_stage_reference", "source_sha256": SOURCE, "page_count": 2,
            "scope": "operator_declared_complete_selected_pages", "coordinate_system": "original_page_display_fraction",
            "annotation_provenance": "synthetic_generator", "pages": [{"page_number": 1,
                "geometry": {"width_points": 24, "height_points": 24, "rotation": 0},
                "lines": [{"region_id": "line-1", "bbox": [.1, .1, .4, .2], "text": "Alice is liable.", "order": 0, "cell_id": None},
                          {"region_id": "line-2", "bbox": [.1, .4, .4, .5], "text": "Bob is not liable.", "order": 1, "cell_id": None}],
                "cells": [], "recognition_region_ids": ["line-1", "line-2"]}]}


def quad(bbox):
    x0, y0, x1, y1 = bbox
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def call(index, name, region=None):
    flags = {"detection": (True, False, False), "full_page": (True, True, True), "gold_crop": (False, False, True)}[name]
    return {"id": f"call-{index:04d}", "stage": name, "page_number": 1, "region_id": region,
            **dict(zip(("use_det", "use_cls", "use_rec"), flags)), "status": "completed",
            "roles": {role: {"attempted": int(enabled), "completed": int(enabled), "failed": 0}
                      for role, enabled in zip(("detection", "classification", "recognition"), flags)}}


def observation(ref=None):
    ref = ref or reference()
    lines = ref["pages"][0]["lines"]
    boxes = [quad([round(value * 100) for value in line["bbox"]]) for line in lines]
    calls = [call(1, "detection"), call(2, "full_page")]
    crops = []
    for index, line in enumerate(lines, 3):
        if line["region_id"] not in ref["pages"][0]["recognition_region_ids"]:
            continue
        calls.append(call(index, "gold_crop", line["region_id"]))
        crops.append({"region_id": line["region_id"], "status": "available", "reason": None,
                      "call_id": calls[-1]["id"], "raster_bbox": [round(value * 100) for value in line["bbox"]],
                      "pixel_sha256": "e" * 64, "text": line["text"]})
    if not lines:
        for role in ("classification", "recognition"):
            calls[1]["roles"][role] = {"attempted": 0, "completed": 0, "failed": 0}
    return {"schema_version": 1, "kind": "ocr_stage_observation", "source_sha256": SOURCE,
            "reference_sha256": REFERENCE, "page_count": 2, "configuration": dict(stage.DEFAULT_CONFIGURATION),
            "pages": [{"page_number": 1, "geometry": dict(ref["pages"][0]["geometry"]),
                       "raster": {"width": 100, "height": 100, "pixel_sha256": "d" * 64},
                       "detection": {"status": "available" if boxes else "empty", "reason": None, "call_id": "call-0001", "boxes": boxes},
                       "full_page": {"status": "available" if boxes else "empty", "reason": None, "call_id": "call-0002",
                                     "lines": [{"text": line["text"], "box": box} for line, box in zip(lines, boxes)]},
                       "gold_crops": crops}], "calls": calls, "canonical_extraction_modified": False, "requires_attention": True}


def evaluate(ref=None, obs=None):
    ref = ref or reference()
    return stage.evaluate_stage_diagnostics(ref, obs or observation(ref), REFERENCE, OBSERVATION)


def test_exact_geometry_recognition_and_full_coverage_are_separate():
    result = evaluate()
    assert result["coverage"] == {"source_pages": 2, "selected_pages": [1], "unselected_page_count": 1,
                                  "reference_lines": 2, "selected_gold_crops": 2, "available_gold_crops": 2,
                                  "paired_full_and_gold_crops": 2, "available_full_pages": 1}
    for key in ("detection", "retained_full_page_lines"):
        graph = result["pages"][0][key]
        assert graph["matched_lines"] == 2
        assert graph["unambiguous_line_recall"] == graph["unambiguous_box_precision"] == 1
        assert graph["order_comparable_pairs"] == 1
    for metric in result["metrics"].values():
        assert metric["metrics"]["summary"]["character"]["edit_distance"] == 0
    assert result["requires_attention"] is True
    assert "Alice" not in json.dumps(result)


def test_retained_ocr_line_absence_is_not_called_a_detector_miss():
    obs = observation()
    obs["pages"][0]["full_page"]["lines"].pop()
    result = evaluate(obs=obs)
    assert result["pages"][0]["detection"]["no_overlap_lines"] == 0
    assert result["pages"][0]["retained_full_page_lines"]["no_overlap_lines"] == 1
    assert result["coverage"]["paired_full_and_gold_crops"] == 1
    assert result["metrics"]["gold_crop_recognition"]["metrics"]["summary"]["record_count"] == 2


def test_order_errors_do_not_change_matched_crop_recognition():
    obs = observation()
    obs["pages"][0]["full_page"]["lines"].reverse()
    result = evaluate(obs=obs)
    assert result["pages"][0]["retained_full_page_lines"]["order_inversions"] == 1
    assert result["metrics"]["full_pages"]["metrics"]["summary"]["character"]["edit_distance"] > 0
    assert result["metrics"]["paired_full_page_lines"]["metrics"]["summary"]["character"]["edit_distance"] == 0


def test_large_merged_box_never_forces_correct_detection_matches():
    obs = observation()
    obs["pages"][0]["detection"]["boxes"] = [quad([5, 5, 90, 90])]
    result = evaluate(obs=obs)["pages"][0]["detection"]
    assert result["merge_candidates"] == 1
    assert result["no_overlap_lines"] == 0
    assert result["matched_lines"] == 0
    assert all(line["status"] == "merge_or_ambiguous" for line in result["lines"])


def test_split_extra_and_weak_geometry_are_explicit():
    obs = observation()
    obs["pages"][0]["detection"]["boxes"] = [quad([10, 10, 25, 20]), quad([25, 10, 40, 20]),
                                                 quad([10, 40, 15, 50]), quad([80, 80, 90, 90])]
    result = evaluate(obs=obs)["pages"][0]["detection"]
    assert result["split_candidates"] == 1
    assert result["unmatched_boxes"] == 1
    assert result["weak_geometry_pairs"] == 1
    assert result["matched_lines"] == 0


def test_convex_quad_uses_polygon_overlap_not_bounding_envelope():
    # This diamond's envelope intersects the top-left rectangle, but its ink
    # polygon does not; envelope-only geometry would create a false edge.
    assert stage._intersection_area([[50, 0], [100, 50], [50, 100], [0, 50]], [0, 0, 20, 20]) == 0
    assert stage._intersection_area(quad([0, 0, 100, 100]), [10, 20, 30, 40]) == pytest.approx(400)
    assert stage._intersection_area(list(reversed(quad([0, 0, 100, 100]))), [10, 20, 30, 40]) == pytest.approx(400)


def test_failed_gold_crop_is_unavailable_not_blank_or_successful():
    obs = observation()
    crop = obs["pages"][0]["gold_crops"][1]
    crop.update(status="unavailable", reason="stage_failed", text=None)
    obs["calls"][3]["status"] = "failed"
    obs["calls"][3]["roles"]["recognition"].update(completed=0, failed=1)
    result = evaluate(obs=obs)
    assert result["coverage"]["available_gold_crops"] == 1
    assert result["pages"][0]["gold_crops"][1]["recognition"]["metrics"] is None


def test_actually_empty_gold_crop_counts_deletions():
    obs = observation()
    obs["pages"][0]["gold_crops"][1].update(status="empty", text=" \n")
    result = evaluate(obs=obs)
    assert result["coverage"]["available_gold_crops"] == 2
    assert result["metrics"]["gold_crop_recognition"]["metrics"]["summary"]["character"]["deletions"] > 0


def test_swallowed_role_failure_does_not_become_successful_empty_ocr():
    obs = observation()
    crop = obs["pages"][0]["gold_crops"][1]
    crop.update(status="unavailable", reason="stage_failed", text=None)
    obs["calls"][3]["roles"]["recognition"].update(completed=0, failed=1)
    # RapidOCR's outer call may return normally after catching a role error.
    assert obs["calls"][3]["status"] == "completed"
    assert evaluate(obs=obs)["coverage"]["available_gold_crops"] == 1
    crop.update(status="empty", reason=None, text="")
    with pytest.raises(ValueError):
        evaluate(obs=obs)


def test_render_failure_preserves_coverage_without_fabricated_geometry():
    obs = observation()
    page = obs["pages"][0]
    page["geometry"] = page["raster"] = None
    for name, key in (("detection", "boxes"), ("full_page", "lines")):
        page[name].update(status="unavailable", reason="render_failed", call_id=None, **{key: []})
    for crop in page["gold_crops"]:
        crop.update(status="unavailable", reason="render_failed", call_id=None, raster_bbox=None, pixel_sha256=None, text=None)
    obs["calls"] = []
    result = evaluate(obs=obs)
    assert result["coverage"]["reference_lines"] == 2
    assert result["pages"][0]["detection"]["no_overlap_lines"] is None
    assert result["metrics"]["full_pages"]["status"] == "unavailable"


def test_empty_annotated_page_has_null_denominators_and_explicit_insertions():
    ref = reference()
    ref["pages"][0].update(lines=[], recognition_region_ids=[])
    obs = observation(ref)
    result = evaluate(ref, obs)
    assert result["pages"][0]["detection"]["unambiguous_line_recall"] is None
    assert result["metrics"]["full_pages"]["metrics"]["summary"]["character"]["error_rate"] is None
    obs["pages"][0]["full_page"].update(status="available", lines=[{"text": "noise", "box": quad([1, 1, 5, 5])}])
    obs["calls"][1] = call(2, "full_page")
    result = evaluate(ref, obs)
    assert result["metrics"]["full_pages"]["metrics"]["summary"]["character"]["insertions"] == 5


def test_cells_are_containers_not_extra_detector_targets():
    ref = reference()
    ref["pages"][0]["cells"] = [{"cell_id": "cell-1", "bbox": [.05, .05, .45, .55], "table_id": "table-1",
                                 "row": 0, "column": 0, "row_span": 1, "column_span": 1}]
    for line in ref["pages"][0]["lines"]:
        line["cell_id"] = "cell-1"
    result = evaluate(ref)
    assert result["pages"][0]["detection"]["reference_lines"] == 2
    assert result["pages"][0]["detection"]["merge_candidates"] == 0
    assert result["pages"][0]["cells"][0]["text"]["metrics"]["summary"]["exact_match_count"] == 1


@pytest.mark.parametrize("edit", [
    lambda x: x.update(schema_version=True), lambda x: x.update(page_count=True), lambda x: x.update(source_sha256="bad"),
    lambda x: x.update(extra=1), lambda x: x.update(scope="partially_annotated"),
    lambda x: x["pages"][0]["geometry"].update(rotation=True),
    lambda x: x["pages"][0]["geometry"].update(width_points=float("inf")),
    lambda x: x["pages"][0]["lines"][0].update(order=True),
    lambda x: x["pages"][0]["lines"][0].update(order=1),
    lambda x: x["pages"][0]["lines"][0].update(text="  "),
    lambda x: x["pages"][0]["lines"][0].update(text="\ud800"),
    lambda x: x["pages"][0]["lines"][0].update(text="a" * 4097),
    lambda x: x["pages"][0]["lines"][0].update(bbox=[0, 0, 1e-320, 1e-320]),
    lambda x: x["pages"][0]["lines"][0].update(bbox=[0, 0, True, 1]),
    lambda x: x["pages"][0]["lines"][0].update(cell_id="missing"),
    lambda x: x["pages"][0]["lines"][1].update(region_id="line-1"),
    lambda x: x["pages"][0].update(recognition_region_ids=["line-1", "line-1"]),
    lambda x: x["pages"][0].update(recognition_region_ids=["missing"]),
    lambda x: x["pages"].append(copy.deepcopy(x["pages"][0])),
])
def test_rejects_invalid_reference(edit):
    ref = reference()
    edit(ref)
    with pytest.raises(ValueError):
        stage.validate_stage_reference(ref)


@pytest.mark.parametrize("edit", [
    lambda x: x.update(schema_version=True), lambda x: x.update(reference_sha256="a" * 64),
    lambda x: x.update(source_sha256="b" * 64), lambda x: x.update(page_count=True),
    lambda x: x.update(canonical_extraction_modified=0), lambda x: x.update(requires_attention=1),
    lambda x: x["configuration"].update(gold_crop_mode="full_pipeline"),
    lambda x: x["configuration"].update(dpi=72),
    lambda x: x["configuration"].update(max_normalized_recognition_width=4096.0),
    lambda x: x["pages"][0].update(page_number=True),
    lambda x: x["pages"][0]["geometry"].update(rotation=90),
    lambda x: x["pages"][0]["raster"].update(width=6001),
    lambda x: x["pages"][0]["raster"].update(pixel_sha256="no"),
    lambda x: x["pages"][0]["detection"].update(boxes=[]),
    lambda x: x["pages"][0]["detection"].update(boxes=[[[0, 0], [40, 40], [40, 0], [0, 40]]]),
    lambda x: x["pages"][0]["detection"].update(boxes=[quad([-1, 1, 5, 5])]),
    lambda x: x["pages"][0]["gold_crops"][0].update(call_id="call-0004"),
    lambda x: x["pages"][0]["gold_crops"][0].update(status="unavailable", reason="stage_failed", text=""),
    lambda x: x["pages"][0]["gold_crops"][0].update(raster_bbox=[10, 10, 41, 20]),
    lambda x: x["pages"][0]["gold_crops"][0].update(pixel_sha256=None),
    lambda x: x["pages"][0]["gold_crops"].pop(),
    lambda x: x["calls"][2].update(use_det=True),
    lambda x: x["calls"][2].update(status="failed"),
    lambda x: x["calls"][2].update(id="call-0040"),
    lambda x: x["calls"][2]["roles"]["recognition"].update(attempted=0, completed=0),
    lambda x: x["calls"][2]["roles"]["recognition"].update(attempted=True),
    lambda x: x["calls"][2]["roles"]["detection"].update(attempted=1, completed=1),
    lambda x: x["calls"].append(call(5, "detection")),
])
def test_rejects_forged_or_inconsistent_observation(edit):
    obs = observation()
    edit(obs)
    with pytest.raises(ValueError):
        stage.validate_stage_observation(obs, reference(), REFERENCE)


def test_snapshot_results_do_not_alias_mutable_inputs():
    ref, obs = reference(), observation()
    ref_copy, obs_copy = copy.deepcopy(ref), copy.deepcopy(obs)
    checked = stage.validate_stage_observation(obs, ref, REFERENCE)
    checked["pages"][0]["gold_crops"][0]["text"] = "changed"
    evaluate(ref, obs)
    assert ref == ref_copy and obs == obs_copy


def test_alignment_budget_abstains_instead_of_suppressing_coverage():
    obs = observation()
    for crop in obs["pages"][0]["gold_crops"]:
        crop["text"] = "z" * 4096
    ref = reference()
    for line in ref["pages"][0]["lines"]:
        line["text"] = "a" * 4096
    result = evaluate(ref, obs)
    assert result["coverage"]["available_gold_crops"] == 2
    assert result["metrics"]["gold_crop_recognition"]["reason"] == "alignment_limit"


def test_geometry_work_bound_checked_before_pairing(monkeypatch):
    monkeypatch.setattr(stage, "MAX_PAIR_COMPARISONS", 1)
    with pytest.raises(ValueError, match="pair-work"):
        evaluate()


@pytest.mark.parametrize("counts,valid", [((1, 0, 0), True), ((1, 1, 1), True), ((1, 1, 0), False), ((1, 0, 1), False)])
def test_empty_full_page_requires_a_complete_allowed_route(counts, valid):
    obs = observation()
    obs["pages"][0]["full_page"].update(status="empty", lines=[])
    for role, count in zip(("detection", "classification", "recognition"), counts):
        obs["calls"][1]["roles"][role].update(attempted=count, completed=count)
    if valid:
        stage.validate_stage_observation(obs, reference(), REFERENCE)
    else:
        with pytest.raises(ValueError, match="component route"):
            stage.validate_stage_observation(obs, reference(), REFERENCE)


def test_all_localized_and_aggregate_metrics_share_one_budget(monkeypatch):
    ref, obs = reference(), observation()
    for line in ref["pages"][0]["lines"]:
        line["text"] = "a" * 4096
    for line in obs["pages"][0]["full_page"]["lines"]:
        line["text"] = "b" * 4096
    for crop in obs["pages"][0]["gold_crops"]:
        crop["text"] = "b" * 4096
    charged = []

    def counted(payload):
        cells = sum(len(item["reference"]) * len(item["prediction"])
                    + len(item["reference"].split()) * len(item["prediction"].split()) for item in payload["records"])
        charged.append(cells)
        return {"stubbed_cost_only": cells}

    monkeypatch.setattr(stage, "evaluate_ocr", counted)
    result = evaluate(ref, obs)
    assert sum(charged) == result["alignment_work"]["reserved_cells"] <= stage.MAX_ALIGNMENT_CELLS
    assert len(charged) == 1
    assert result["pages"][0]["gold_crops"][1]["recognition"]["reason"] == "alignment_limit"
    assert result["metrics"]["gold_crop_recognition"]["reason"] == "alignment_limit"
    assert result["coverage"]["available_gold_crops"] == 2


@pytest.mark.parametrize("width,height,dpi,expected", [
    (24, 24, 300, (100, 100)), (432, 576, 300, (1800, 2400)),
    (612, 792, 72, (612, 792)), (24.00001, 48.00001, 300, (100, 200)),
])
def test_float32_renderer_dimension_contract(width, height, dpi, expected):
    assert stage.raster_dimensions({"width_points": width, "height_points": height, "rotation": 0}, dpi) == expected


def test_called_crop_cannot_exceed_recognition_width_recipe():
    ref, obs = reference(), observation()
    ref["pages"][0]["lines"][0]["bbox"] = [0, .1, 1, .101]
    obs["pages"][0]["gold_crops"][0]["raster_bbox"] = [0, 10, 100, 11]
    with pytest.raises(ValueError, match="normalized recognition width"):
        stage.validate_stage_observation(obs, ref, REFERENCE)
