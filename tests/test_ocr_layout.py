"""Synthetic-only conservative two-column layout review and scoring contracts."""

from __future__ import annotations

import copy
import itertools
import json
from pathlib import Path
import subprocess
import sys

import pytest

import ocr_layout as layout
import ocr_recovery


SOURCE_DIGEST = "a" * 64
RECOVERY_DIGEST = "b" * 64
MODES = ("none", "deskew", "contrast", "deskew-contrast")
HEALTHY = "A long synthetic native paragraph that needs no automatic OCR recovery."
ORDER = [0, 1, 3, 2, 4, 5]
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _line(text, left, top, right, bottom):
    return {"text": text, "score": 0.9,
            "box": [[left, top], [right, top], [right, bottom], [left, bottom]]}


def _lines():
    return [
        _line("HEADER", 20, 20, 980, 80),
        _line("Alice is liable.", 50, 250, 400, 280),
        _line("Bob is not liable.", 600, 250, 950, 280),
        _line("Alice has 14 days.", 50, 350, 400, 380),
        _line("Bob has 30 days.", 600, 350, 950, 380),
        _line("FOOTER", 20, 900, 980, 950),
    ]


def _metadata(mode):
    return {
        "schema_version": 1, "algorithm": "bounded-deskew-contrast-v1", "mode": mode,
        "original_raster": {"width": 1000, "height": 1000},
        "processed_raster": {"width": 1000, "height": 1000},
        "source_to_processed": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
        "processed_to_source": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
        "deskew": {"status": "disabled" if mode == "contrast" else "skipped",
                   "reason": "mode_disabled" if mode == "contrast" else "blank_or_low_ink",
                   "angle_degrees": 0.0, "estimated_angle_degrees": None, "gain": None},
        "contrast": {"status": "disabled" if mode == "deskew" else "skipped",
                     "reason": "mode_disabled" if mode == "deskew" else "already_high_contrast",
                     "low_level": None if mode == "deskew" else 0,
                     "high_level": None if mode == "deskew" else 255},
        "parameters": {"thumbnail_max_side": 1000, "max_angle_degrees": 5.0,
                       "angle_step_degrees": 0.25, "min_gain": 0.025,
                       "low_percentile": 0.5, "high_percentile": 99.5},
        "libraries": {"opencv": "synthetic", "numpy": "synthetic"},
    }


def _candidate(lines=None, *, mode="none"):
    lines = _lines() if lines is None else copy.deepcopy(lines)
    candidate = {
        "text": "\n".join(line["text"] for line in lines), "lines": lines,
        "mean_confidence": sum(line["score"] for line in lines) / len(lines) if lines else None,
        "raster": {"width": 1000, "height": 1000, "dpi": 300,
                   "coordinate_system": "rendered_image_pixels" if mode == "none" else "preprocessed_image_pixels"},
        "engine": {"name": "rapidocr", "version": "synthetic", "min_score": 0.0, "max_side": 6000},
    }
    if mode != "none":
        candidate["preprocessing"] = _metadata(mode)
    return candidate


def _report(candidates=None, *, native=None, mode="none", max_pages=20):
    candidates = [_candidate(mode=mode)] if candidates is None else candidates
    native = ["Original synthetic native text."] * len(candidates) if native is None else native

    class Reader:
        page_count = len(candidates)

        def native_text(self, number):
            value = native[number - 1]
            if value is None:
                raise RuntimeError("synthetic unavailable native text")
            return value

        def retry(self, number):
            value = candidates[number - 1]
            if isinstance(value, BaseException):
                raise value
            return value

    report = ocr_recovery.build_recovery_report(
        Reader(), source_sha256=SOURCE_DIGEST,
        policy=ocr_recovery.RetryPolicy(preprocessing=mode, max_pages=max_pages))
    report["evidence_sha256"] = None
    return report


def _page_plan(number=1):
    return {"page_number": number, "body_band": [0.2, 0.8],
            "gutter": [0.45, 0.55], "order": "left_then_right"}


def _plan(*numbers):
    return {"schema_version": 1, "source_sha256": SOURCE_DIGEST,
            "recovery_sha256": RECOVERY_DIGEST, "coordinate_system": "candidate_raster_fraction",
            "pages": [_page_plan(number) for number in (numbers or (1,))]}


def _references(*pages):
    desired = "\n".join(_lines()[index]["text"] for index in ORDER)
    return {"schema_version": 1, "source_sha256": SOURCE_DIGEST,
            "pages": [{"page_number": number, "reference": text}
                      for number, text in (pages or ((1, desired),))]}


def _build(report=None, plan=None, references=None):
    return layout.build_layout_review(
        _report() if report is None else report, _plan() if plan is None else plan,
        recovery_sha256=RECOVERY_DIGEST, references=references)


def _mutate(payload, path, value):
    owner = payload
    for key in path[:-1]:
        owner = owner[key]
    owner[path[-1]] = value
    return payload


@pytest.mark.parametrize("mode", MODES)
def test_explicit_columns_reorder_only_text_and_full_line_permutation(mode):
    report = _report(mode=mode)
    plan = _plan()
    original_report, original_plan = copy.deepcopy(report), copy.deepcopy(plan)
    result = _build(report, plan)
    page = result["pages"][0]
    candidate = report["pages"][0]["candidate"]
    assert page["status"] == "reordered" and page["reason"] == "explicit_columns"
    assert page["original_status"] == "review_required" and page["planned"] is True
    assert page["original_text"] == candidate["text"]
    assert page["line_order"] == ORDER
    assert sorted(page["line_order"]) == list(range(len(candidate["lines"])))
    assert page["proposed_text"] == "\n".join(candidate["lines"][index]["text"] for index in ORDER)
    assert page["lines"] == candidate["lines"]
    assert page["raster"] == candidate["raster"]
    assert result["requires_attention"] is True
    assert result["comparison"] is None
    assert report == original_report and plan == original_plan
    page["lines"][0]["box"][0][0] = 99
    page["raster"]["width"] = 999
    page["line_order"].reverse()
    assert report == original_report and plan == original_plan


@pytest.mark.parametrize("mode", MODES)
def test_already_ordered_columns_are_unchanged(mode):
    original_lines = _lines()
    candidate = _candidate([original_lines[index] for index in ORDER], mode=mode)
    result = _build(_report([candidate], mode=mode))
    page = result["pages"][0]
    assert page["status"] == "unchanged" and page["reason"] == "already_ordered"
    assert page["line_order"] == list(range(6))
    assert page["proposed_text"] == page["original_text"] == candidate["text"]


@pytest.mark.parametrize("body_order", itertools.permutations((1, 2, 3, 4)))
def test_all_interleavings_preserve_engine_order_inside_each_column(body_order):
    original_lines = _lines()
    candidate = _candidate([original_lines[index] for index in (0, *body_order, 5)])
    page = _build(_report([candidate]))["pages"][0]
    if body_order.index(1) > body_order.index(3) or body_order.index(2) > body_order.index(4):
        assert page["status"] == "abstained"
        assert page["reason"] == "overlapping_or_reversed_lines"
        assert page["proposed_text"] == candidate["text"]
        assert page["line_order"] == list(range(6))
    else:
        assert page["status"] in {"reordered", "unchanged"}
        assert page["proposed_text"] == _references()["pages"][0]["reference"]
        assert sorted(page["line_order"]) == list(range(6))


@pytest.mark.parametrize("corner", range(4))
@pytest.mark.parametrize("reverse", (False, True))
def test_axis_geometry_is_independent_of_first_corner_and_winding(corner, reverse):
    lines = _lines()
    for line in lines:
        points = line["box"][corner:] + line["box"][:corner]
        line["box"] = points[::-1] if reverse else points
    page = _build(_report([_candidate(lines)]))["pages"][0]
    assert page["status"] == "reordered" and page["line_order"] == ORDER


@pytest.mark.parametrize(("change", "reason"), [
    ("straddles_body_top", "body_boundary_crossed"),
    ("straddles_body_bottom", "body_boundary_crossed"),
    ("crosses_gutter", "gutter_crossed"), ("inside_gutter", "gutter_crossed"),
    ("header_inside_body_sequence", "noncontiguous_body"),
    ("footer_inside_body_sequence", "noncontiguous_body"),
    ("missing_column", "insufficient_columns"), ("one_line_column", "insufficient_columns"),
    ("vertical_overlap", "overlapping_or_reversed_lines"),
    ("concave", "ambiguous_geometry"), ("rotated", "ambiguous_geometry"),
])
def test_any_ambiguous_line_abstains_for_the_whole_page(change, reason):
    lines = _lines()
    if change == "straddles_body_top":
        lines[1] = _line(lines[1]["text"], 50, 180, 400, 220)
    elif change == "straddles_body_bottom":
        lines[1] = _line(lines[1]["text"], 50, 780, 400, 820)
    elif change == "crosses_gutter":
        lines[1] = _line(lines[1]["text"], 50, 250, 700, 280)
    elif change == "inside_gutter":
        lines[1] = _line(lines[1]["text"], 470, 250, 530, 280)
    elif change == "header_inside_body_sequence":
        lines[0], lines[1] = lines[1], lines[0]
    elif change == "footer_inside_body_sequence":
        lines[4], lines[5] = lines[5], lines[4]
    elif change == "missing_column":
        lines = [lines[index] for index in (0, 1, 3, 5)]
    elif change == "one_line_column":
        del lines[4]
    elif change == "vertical_overlap":
        lines[3] = _line(lines[3]["text"], 50, 270, 400, 310)
    elif change == "concave":
        lines[1]["box"] = [[50, 250], [400, 250], [150, 270], [50, 280]]
    else:
        lines[1]["box"] = [[50, 260], [250, 220], [400, 280], [200, 320]]
    candidate = _candidate(lines)
    report = _report([candidate])
    assert report["pages"][0]["status"] == "review_required"
    result = _build(report)
    page = result["pages"][0]
    assert page["status"] == "abstained" and page["reason"] == reason
    assert page["original_text"] == page["proposed_text"] == candidate["text"]
    assert page["line_order"] == list(range(len(lines)))
    assert page["lines"] == lines
    assert result["requires_attention"] is True


@pytest.mark.parametrize(("axis", "offset", "expected"), [
    ("horizontal", 30.0, "reordered"), ("horizontal", 30.001, "abstained"),
    ("vertical", 7.5, "reordered"), ("vertical", 7.501, "abstained"),
])
def test_near_axis_thresholds_are_explicit_and_conservative(axis, offset, expected):
    lines = _lines()
    lines[1]["box"] = (
        [[50, 250], [350, 250 + offset], [350, 280 + offset], [50, 280]]
        if axis == "horizontal" else
        [[50, 250], [350, 250], [350 + offset, 280], [50 + offset, 280]])
    page = _build(_report([_candidate(lines)]))["pages"][0]
    assert page["status"] == expected
    if expected == "abstained":
        assert page["reason"] == "ambiguous_geometry"


def test_exact_body_and_gutter_boundaries_are_not_treated_as_overlap():
    lines = _lines()
    lines[0] = _line("HEADER", 20, 100, 980, 200)
    lines[1] = _line("Alice is liable.", 50, 200, 450, 230)
    lines[2] = _line("Bob is not liable.", 550, 200, 950, 230)
    lines[3] = _line("Alice has 14 days.", 50, 770, 450, 800)
    lines[4] = _line("Bob has 30 days.", 550, 770, 950, 800)
    lines[5] = _line("FOOTER", 20, 800, 980, 950)
    assert _build(_report([_candidate(lines)]))["pages"][0]["line_order"] == ORDER


def test_touching_vertical_spans_are_disjoint_and_keep_engine_order():
    lines = _lines()
    lines[3] = _line(lines[3]["text"], 50, 280, 400, 310)
    lines[4] = _line(lines[4]["text"], 600, 280, 950, 310)
    page = _build(_report([_candidate(lines)]))["pages"][0]
    assert page["status"] == "reordered" and page["line_order"] == ORDER


@pytest.mark.parametrize(("scale_x", "scale_y"), [(2, 1), (1, 2), (2, 3)])
def test_plan_fractions_use_each_candidates_current_width_and_height(scale_x, scale_y):
    candidate = _candidate()
    for line in candidate["lines"]:
        for point in line["box"]:
            point[0] *= scale_x
            point[1] *= scale_y
    candidate["raster"]["width"] *= scale_x
    candidate["raster"]["height"] *= scale_y
    page = _build(_report([candidate]))["pages"][0]
    assert page["line_order"] == ORDER and page["status"] == "reordered"


@pytest.mark.parametrize("count", (2000, 2001))
def test_line_budget_boundary_abstains_without_truncating_any_content(count):
    lines = [
        _line(f"{'Left' if column == 0 else 'Right'} {row}",
              50 if column == 0 else 600, 201 + row * 0.5,
              400 if column == 0 else 950, 201.25 + row * 0.5)
        for row in range(1000) for column in range(2)
    ]
    if count == 2001:
        lines.append(_line("Extra left line", 50, 750, 400, 760))
    candidate = _candidate(lines)
    page = _build(_report([candidate]))["pages"][0]
    assert len(page["lines"]) == len(page["line_order"]) == count
    assert sorted(page["line_order"]) == list(range(count))
    if count == 2000:
        assert page["status"] == "reordered"
    else:
        assert page["status"] == "abstained" and page["reason"] == "line_limit"
        assert page["proposed_text"] == candidate["text"]
        assert page["line_order"] == list(range(count))


@pytest.mark.parametrize("planned_empty", (False, True))
def test_empty_candidates_are_available_blanks_not_missing_pages(planned_empty):
    report = _report([_candidate(), _candidate([])])
    result = _build(report, _plan(2 if planned_empty else 1), _references((1, _candidate()["text"]), (2, "")))
    page = result["pages"][1]
    assert page["original_status"] == "empty_candidate"
    assert page["status"] == ("abstained" if planned_empty else "unchanged")
    assert page["reason"] == ("empty_candidate" if planned_empty else "not_requested")
    assert page["original_text"] == page["proposed_text"] == ""
    assert page["line_order"] == page["lines"] == []
    assert page["raster"] == report["pages"][1]["candidate"]["raster"]
    assert result["reference_coverage"]["paired_pages"] == 2
    assert result["coverage"]["empty_candidate_pages"] == [2]
    assert result["requires_attention"] is True


def test_unplanned_candidates_preserve_engine_text_even_if_geometry_is_ambiguous():
    candidate = _candidate()
    candidate["lines"][1]["box"] = [[50, 250], [400, 250], [150, 270], [50, 280]]
    report = _report([candidate, _candidate()])
    page = _build(report, _plan(2))["pages"][0]
    assert page["planned"] is False
    assert page["status"] == "unchanged" and page["reason"] == "not_requested"
    assert page["original_text"] == page["proposed_text"] == candidate["text"]
    assert page["line_order"] == list(range(6))


def test_available_candidate_does_not_require_native_original_extraction():
    report = _report(native=[None])
    assert report["pages"][0]["original_text"] is None
    result = _build(report, references=_references())
    assert result["pages"][0]["original_text"] == report["pages"][0]["candidate"]["text"]
    assert result["reference_coverage"]["paired_pages"] == 1
    assert result["comparison"]["summary"]["outcomes"]["improved"] == 1


def test_selected_plus_planned_union_and_reference_unavailability_are_explicit():
    report = _report(
        [_candidate(), ValueError("synthetic failure"), _candidate(), _candidate(), _candidate()],
        native=["prior", "prior", "prior", HEALTHY, HEALTHY], max_pages=2)
    result = _build(report, _plan(4, 3, 2), _references(*[(number, "reference") for number in range(1, 6)]))
    assert [page["page_number"] for page in result["pages"]] == [1, 2, 3, 4]
    assert [page["page_number"] for page in result["plan"]["pages"]] == [2, 3, 4]
    for page, status in zip(result["pages"][1:], ("retry_failed", "deferred", "not_selected")):
        assert page["status"] == "unavailable"
        assert page["original_status"] == page["reason"] == status
        assert all(page[key] is None for key in ("original_text", "proposed_text", "line_order", "raster", "lines"))
    assert result["summary"] == {
        "review_pages": 4, "planned_pages": 3, "reordered": 0, "unchanged": 1, "abstained": 0, "unavailable": 3,
    }
    assert result["coverage"] == {
        "selected_pages": [1, 2], "requested_pages": [2, 3, 4],
        "planned_unavailable_pages": [
            {"page_number": 2, "status": "retry_failed"},
            {"page_number": 3, "status": "deferred"},
            {"page_number": 4, "status": "not_selected"},
        ], "deferred_pages": [3], "failed_pages": [2], "empty_candidate_pages": [],
    }
    assert result["reference_coverage"] == {
        "reference_pages": 5, "paired_pages": 1,
        "unpaired_pages": [
            {"page_number": 2, "status": "retry_failed"}, {"page_number": 3, "status": "deferred"},
            {"page_number": 4, "status": "not_selected"}, {"page_number": 5, "status": "not_selected"},
        ], "unreferenced_selected_pages": [], "unreferenced_planned_pages": [],
    }
    assert result["requires_attention"] is True


def test_unplanned_deferred_pages_still_require_attention():
    report = _report([_candidate(), _candidate()], max_pages=1)
    result = _build(report, references=_references())
    assert result["summary"]["unavailable"] == 0
    assert result["coverage"]["deferred_pages"] == [2]
    assert result["requires_attention"] is True


def test_references_score_every_available_page_including_unplanned_abstained_and_empty(monkeypatch):
    ambiguous = _lines()
    ambiguous[1] = _line(ambiguous[1]["text"], 50, 250, 700, 280)
    candidates = [_candidate(), _candidate(ambiguous), _candidate(), _candidate([])]
    references = _references((1, _references()["pages"][0]["reference"]),
                             (2, candidates[1]["text"]), (3, candidates[2]["text"]), (4, ""))
    original_references = copy.deepcopy(references)
    actual_compare = layout.compare_ocr
    calls = []

    def observe(before, after):
        calls.append((copy.deepcopy(before), copy.deepcopy(after)))
        return actual_compare(before, after)

    monkeypatch.setattr(layout, "compare_ocr", observe)
    result = _build(_report(candidates), _plan(4, 2, 1), references)
    assert len(calls) == 1
    before, after = calls[0]
    assert [record["id"] for record in before["records"]] == [f"page-{number:05d}" for number in range(1, 5)]
    assert [record["id"] for record in after["records"]] == [record["id"] for record in before["records"]]
    assert [record["prediction"] for record in before["records"]] == [candidate["text"] for candidate in candidates]
    assert after["records"][0]["prediction"] == references["pages"][0]["reference"]
    assert [record["prediction"] for record in after["records"][1:]] == [candidate["text"] for candidate in candidates[1:]]
    assert result["comparison"]["summary"]["record_count"] == result["reference_coverage"]["paired_pages"] == 4
    assert result["summary"]["abstained"] == 2
    assert references == original_references


@pytest.mark.parametrize("correct_reference", (False, True))
def test_reference_improvement_or_regression_never_automatically_accepts(correct_reference):
    references = _references() if correct_reference else _references((1, _candidate()["text"]))
    result = _build(references=references)
    assert result["comparison"]["regression_detected"] is not correct_reference
    assert result["requires_attention"] is not correct_reference
    assert result["acceptance"] == "manual_review_required"
    assert result["canonical_extraction_modified"] is False
    assert result["recognition_rerun"] is False


def test_reference_gaps_are_not_hidden_by_scoring_only_the_reordered_page():
    result = _build(_report([_candidate(), _candidate()]), _plan(1, 2), _references())
    assert result["reference_coverage"]["unreferenced_selected_pages"] == [2]
    assert result["reference_coverage"]["unreferenced_planned_pages"] == [2]
    assert result["comparison"]["summary"]["record_count"] == 1
    assert result["requires_attention"] is True


def test_no_available_reference_pairs_produce_no_comparison_and_attention():
    report = _report([ValueError("synthetic failure")])
    result = _build(report, references=_references())
    assert result["comparison"] is None
    assert result["reference_coverage"]["paired_pages"] == 0
    assert result["requires_attention"] is True


@pytest.mark.parametrize(("path", "value"), [
    (("schema_version",), True), (("schema_version",), 2),
    (("source_sha256",), "c" * 64), (("recovery_sha256",), "c" * 64),
    (("source_sha256",), "PRIVATE_SOURCE"), (("coordinate_system",), "rendered_image_pixels"),
    (("pages",), None), (("pages",), []), (("pages",), [_page_plan()] * 21),
    (("pages",), [_page_plan(), _page_plan()]), (("pages", 0), []),
    (("pages", 0, "page_number"), True), (("pages", 0, "page_number"), 0),
    (("pages", 0, "page_number"), 2), (("pages", 0, "page_number"), 1.0),
    (("pages", 0, "order"), "right_then_left"), (("pages", 0, "order"), ["left_then_right"]),
])
def test_plan_schema_bindings_and_page_numbers_are_strict(path, value):
    plan = _mutate(_plan(), path, value)
    before = copy.deepcopy(plan)
    with pytest.raises(ValueError) as error:
        _build(plan=plan)
    assert "PRIVATE_" not in str(error.value)
    assert plan == before


@pytest.mark.parametrize("field", ("body_band", "gutter"))
@pytest.mark.parametrize("invalid", [None, (), [0.1], [0.1, 0.5, 0.9], ["0.1", 0.9],
                                    [True, 0.9], [0.1, False], [-0.1, 0.9], [0.1, 1.1],
                                    [0.8, 0.2], [0.2, 0.2], [float("nan"), 0.9],
                                    [0.1, float("inf")], [float("-inf"), 0.9], [0, 10 ** 1000]],
                         ids=("null", "tuple", "short", "long", "string", "bool_low", "bool_high",
                              "negative", "over_one", "reversed", "equal", "nan", "inf", "negative_inf", "huge_int"))
def test_plan_fraction_pairs_reject_invalid_values_without_coercion(field, invalid):
    plan = _plan()
    plan["pages"][0][field] = invalid
    with pytest.raises(ValueError):
        _build(plan=plan)


@pytest.mark.parametrize("gutter", ([0, 0.55], [0.45, 1]))
def test_gutter_must_be_strictly_inside_the_current_raster(gutter):
    plan = _plan()
    plan["pages"][0]["gutter"] = gutter
    with pytest.raises(ValueError):
        _build(plan=plan)


def test_body_band_can_cover_entire_raster_without_headers_or_footers():
    plan = _plan()
    plan["pages"][0]["body_band"] = [0, 1]
    result = _build(_report([_candidate(_lines()[1:5])]), plan)
    assert result["pages"][0]["status"] == "reordered"
    assert result["pages"][0]["line_order"] == [0, 2, 1, 3]


@pytest.mark.parametrize("target", ("top", "page"))
@pytest.mark.parametrize("mutation", ("extra", "missing"))
def test_plan_unknown_and_missing_fields_fail_closed(target, mutation):
    plan = _plan()
    owner = plan if target == "top" else plan["pages"][0]
    if mutation == "extra":
        owner["PRIVATE_FIELD"] = "PRIVATE_VALUE"
    else:
        del owner["schema_version" if target == "top" else "gutter"]
    with pytest.raises(ValueError) as error:
        _build(plan=plan)
    assert "PRIVATE_" not in str(error.value)


@pytest.mark.parametrize("digest", (None, True, "", "PRIVATE_DIGEST", "c" * 64))
def test_supplied_recovery_snapshot_digest_must_be_valid_and_match_plan(digest):
    with pytest.raises(ValueError) as error:
        layout.build_layout_review(_report(), _plan(), recovery_sha256=digest)
    assert "PRIVATE_" not in str(error.value)


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(("path", "value"), [
    (("kind",), "PRIVATE_KIND"), (("summary", "selected"), 99),
    (("pages", 0, "candidate", "text"), "PRIVATE_TEXT"),
    (("pages", 0, "candidate", "raster", "dpi"), 400),
    (("pages", 0, "candidate", "lines", 0, "box"), [[1, 1]] * 4),
])
def test_entire_recovery_schema_is_still_strict_before_layout_processing(mode, path, value):
    report = _mutate(_report(mode=mode), path, value)
    with pytest.raises(ValueError) as error:
        _build(report)
    assert "PRIVATE_" not in str(error.value)


@pytest.mark.parametrize("mutation", ("source", "duplicate_page", "critical_not_in_reference", "unknown_field"))
def test_optional_reference_contract_is_validated_not_silently_intersected(mutation):
    references = _references()
    if mutation == "source":
        references["source_sha256"] = "c" * 64
    elif mutation == "duplicate_page":
        references["pages"].append(copy.deepcopy(references["pages"][0]))
    elif mutation == "critical_not_in_reference":
        references["pages"][0]["critical_tokens"] = ["PRIVATE_UNKNOWN"]
    else:
        references["PRIVATE_FIELD"] = "PRIVATE_VALUE"
    with pytest.raises(ValueError) as error:
        _build(references=references)
    assert "PRIVATE_" not in str(error.value)


def test_scoring_budget_or_cancellation_propagates_without_fabricated_review(monkeypatch):
    for error in (ValueError("synthetic alignment budget"), KeyboardInterrupt()):
        def fail(*args):
            raise error

        monkeypatch.setattr(layout, "compare_ocr", fail)
        with pytest.raises(type(error)):
            _build(references=_references())


def test_result_and_plan_are_detached_and_json_finite_without_console_output(capsys):
    plan = _plan()
    references = _references()
    original = copy.deepcopy(plan)
    result = _build(plan=plan, references=references)
    json.dumps(result, allow_nan=False)
    result["plan"]["pages"][0]["gutter"][0] = 0.1
    result["parameters"]["max_lines_per_page"] = 1
    assert plan == original
    assert _build()["parameters"]["max_lines_per_page"] == 2000
    assert capsys.readouterr() == ("", "")


def test_module_import_has_no_pdf_model_or_facade_dependency():
    result = subprocess.run(
        [sys.executable, "-c", (
            "import sys; import ocr_layout; "
            "forbidden={'rag','ocr_recovery_runtime','pymupdf','fitz','rapidocr','onnxruntime','cv2','numpy'}; "
            "assert not forbidden.intersection(sys.modules)"
        )], cwd=PROJECT_ROOT, stdin=subprocess.DEVNULL, capture_output=True, text=True,
        timeout=30, check=False)
    assert result.returncode == 0, result.stderr


def test_twenty_planned_pages_are_supported_without_dropping_unplanned_selected_pages():
    report = _report([_candidate() for _ in range(40)], max_pages=20)
    plan = _plan(*range(40, 20, -1))
    result = _build(report, plan)
    assert result["summary"] == {
        "review_pages": 40, "planned_pages": 20, "reordered": 0,
        "unchanged": 20, "abstained": 0, "unavailable": 20,
    }
    assert [page["page_number"] for page in result["pages"]] == list(range(1, 41))
    assert result["coverage"]["deferred_pages"] == list(range(21, 41))
    assert result["requires_attention"] is True


def test_invalid_unplanned_candidate_is_not_hidden_by_the_explicit_page_plan():
    report = _report([_candidate(), _candidate()])
    report["pages"][1]["candidate"]["engine"]["max_side"] = 5000
    with pytest.raises(ValueError):
        _build(report, _plan(1))


def test_one_page_regression_is_visible_even_when_total_character_error_improves():
    long_lines = _lines()
    for line in long_lines[1:5]:
        line["text"] = " ".join([line["text"]] * 5)
    candidates = [_candidate(long_lines), _candidate()]
    desired = "\n".join(long_lines[index]["text"] for index in ORDER)
    result = _build(_report(candidates), _plan(1, 2), _references((1, desired), (2, candidates[1]["text"])))
    assert result["comparison"]["summary"]["delta"]["character"]["edit_distance"] < 0
    assert result["comparison"]["records"][0]["outcome"] == "improved"
    assert result["comparison"]["records"][1]["regression_detected"] is True
    assert result["comparison"]["regression_detected"] is True
    assert result["requires_attention"] is True


def test_contextual_critical_phrases_are_forwarded_without_any_token_rewriting():
    references = _references()
    references["pages"][0]["critical_tokens"] = ["Alice is liable.", "Bob is not liable.", "Bob has 30 days."]
    result = _build(references=references)
    record = result["comparison"]["records"][0]
    assert len(record["delta"]["critical_tokens"]) == 3
    assert all(entry["missing_occurrences"] == entry["extra_occurrences"] == 0
               for entry in record["delta"]["critical_tokens"])
    assert [line["text"] for line in result["pages"][0]["lines"]] == [line["text"] for line in _lines()]


@pytest.mark.parametrize("argument", ("source_sha256", "recovery_sha256"))
@pytest.mark.parametrize("invalid", (None, True, 123, "", "PRIVATE_DIGEST", "g" * 64, "a" * 63, "a" * 65))
def test_public_plan_validator_rejects_invalid_expected_digests(argument, invalid):
    expected = {"source_sha256": SOURCE_DIGEST, "recovery_sha256": RECOVERY_DIGEST, "page_count": 1}
    expected[argument] = invalid
    plan = _plan()
    # Matching malformed declarations must not bypass expected-argument checks.
    plan[argument] = invalid
    before = copy.deepcopy(plan)
    with pytest.raises(ValueError) as error:
        layout.validate_layout_plan(plan, **expected)
    assert "PRIVATE_" not in str(error.value)
    assert plan == before


@pytest.mark.parametrize("page_count", (None, True, False, 0, -1, 5001, 1.0, "1", float("nan"), float("inf")))
def test_public_plan_validator_rejects_invalid_expected_page_count(page_count):
    with pytest.raises(ValueError):
        layout.validate_layout_plan(
            _plan(), source_sha256=SOURCE_DIGEST, recovery_sha256=RECOVERY_DIGEST,
            page_count=page_count)


@pytest.mark.parametrize("page_count", (1, 5000))
def test_public_plan_validator_accepts_exact_page_count_boundaries(page_count):
    plan = _plan(page_count)
    before = copy.deepcopy(plan)
    result = layout.validate_layout_plan(
        plan, source_sha256=SOURCE_DIGEST, recovery_sha256=RECOVERY_DIGEST,
        page_count=page_count)
    assert result["pages"][0]["page_number"] == page_count
    result["pages"][0]["body_band"][0] = 0.1
    assert plan == before
