"""Synthetic geometry controls; no source pixels, OCR, reference scoring or IO."""

import copy
import hashlib
import json
import subprocess
import sys

import pytest

import ocr_column_suggestions as suggestions
import ocr_layout
from test_ocr_layout import HEALTHY, RECOVERY_DIGEST, _candidate, _line, _report


def lines(*, rows=6, columns=2):
    result = [_line("PRIVATE HEADER", 80, 50, 880, 90)]
    for row in range(rows):
        for column in range(columns):
            left = 80 + column * (470 if columns == 2 else 290)
            result.append(_line(f"PRIVATE {row} {column}", left, 200 + row * 70,
                                left + (250 if columns == 2 else 200), 230 + row * 70))
    result.append(_line("PRIVATE FOOTER", 80, 940, 380, 965))
    return result


def build(report=None, pages=None):
    return suggestions.build_column_suggestions(
        _report([_candidate(lines())]) if report is None else report,
        recovery_sha256=RECOVERY_DIGEST, requested_pages=[1] if pages is None else pages)


def validate(payload, report):
    return suggestions.validate_column_suggestions(payload, recovery=report, recovery_sha256=RECOVERY_DIGEST)


def test_inferred_geometry_emits_only_an_unconfirmed_complete_permutation():
    report = _report([_candidate(lines())])
    original = copy.deepcopy(report)
    result = build(report)
    page = result["pages"][0]
    assert page["status"] == "unconfirmed_hypothesis"
    assert page["reason"] == "geometry_two_column_hypothesis"
    assert page["line_order"] == [0, *range(1, 13, 2), *range(2, 13, 2), 13]
    assert page["band_counts"] == [1, 12, 1] and page["column_counts"] == [6, 6]
    assert page["plan"]["body_band"] != [0.13, 0.43]  # No calibration-fixture hand plan.
    assert page["plan"]["page_number"] == 1
    candidate = report["pages"][0]["candidate"]
    assert ocr_layout._proposal(candidate, page["plan"])[0] == page["line_order"]
    assert page["line_count"] == len(candidate["lines"])
    assert page["candidate_sha256"] == hashlib.sha256(
        result["candidate_digest_policy"]["domain"].encode() + b"\0" + suggestions._canonical(candidate)).hexdigest()
    assert result["candidate_digest_policy"]["is_file_digest"] is False
    for key in ("automatic_application_supported", "accuracy_verified", "full_page_coverage_verified",
                "canonical_extraction_modified", "recognition_rerun"):
        assert result[key] is False
    assert result["requires_attention"] is result["operator_confirmation_required"] is True
    assert result["semantic_role"] == "unknown" and result["acceptance"] == "manual_review_required"
    assert result["scope"] == "observed_candidate_geometry_only"
    assert "PRIVATE" not in json.dumps(result) and "synthetic" not in json.dumps(result)
    assert validate(result, report) == result
    assert report == original
    page["line_order"].reverse()
    page["plan"]["gutter"][0] = 0.01
    assert report == original


def test_already_column_ordered_remains_an_unconfirmed_not_accuracy_claim():
    original = lines()
    ordered = [original[index] for index in [0, *range(1, 13, 2), *range(2, 13, 2), 13]]
    page = build(_report([_candidate(ordered)]))["pages"][0]
    assert page["status"] == "unconfirmed_hypothesis"
    assert page["reason"] == "already_engine_ordered_hypothesis"
    assert page["line_order"] == list(range(14))


def test_identical_table_and_prose_geometry_is_not_classified_by_text_or_confidence():
    prose = _candidate(lines())
    table_lines = copy.deepcopy(prose["lines"])
    for index, line in enumerate(table_lines):
        line["text"] = f"ROW {index // 2} VALUE 30 not 14"
        line["score"] = index / len(table_lines)
    table = _candidate(table_lines)
    first = build(_report([prose]))
    second = build(_report([table]))
    first_page, second_page = first["pages"][0], second["pages"][0]
    assert first_page["candidate_sha256"] != second_page["candidate_sha256"]
    assert {key: value for key, value in first_page.items() if key != "candidate_sha256"} == {
        key: value for key, value in second_page.items() if key != "candidate_sha256"}
    assert second["semantic_role"] == "unknown" and second["automatic_application_supported"] is False
    assert second_page["line_order"] != list(range(len(table_lines)))
    # A row-major table's true order can be identity: the hypothesis is not a
    # measured improvement and must never become an automatic plan/application.
    assert second["accuracy_verified"] is False


def test_jointly_unobserved_printed_line_cannot_be_certified_present():
    observed = lines()
    del observed[5]
    result = build(_report([_candidate(observed)]))
    page = result["pages"][0]
    assert page["status"] == "unconfirmed_hypothesis"
    assert sorted(page["line_order"]) == list(range(13))
    assert result["full_page_coverage_verified"] is False
    assert result["accuracy_verified"] is False


def test_repeated_text_lines_retain_every_distinct_engine_position():
    repeated = lines()
    for line in repeated:
        line["text"] = "Repeated important text"
    page = build(_report([_candidate(repeated)]))["pages"][0]
    assert len(page["line_order"]) == len(set(page["line_order"])) == len(repeated)
    assert sorted(page["line_order"]) == list(range(len(repeated)))


@pytest.mark.parametrize("change,reason", [
    ("three_columns", "not_exactly_two_disjoint_x_components"),
    ("spanning_body", "not_exactly_two_disjoint_x_components"),
    ("one_column", "not_exactly_two_disjoint_x_components"),
    ("reversed", "overlapping_or_reversed_lines"),
    ("overlap", "overlapping_or_reversed_lines"),
    ("duplicate_geometry", "overlapping_or_reversed_lines"),
    ("noncontiguous", "noncontiguous_body"),
    ("skew", "ambiguous_geometry"),
    ("concave", "ambiguous_geometry"),
    ("multiple_bodies", "no_unique_dense_body_band"),
    ("too_few_lines", "no_unique_dense_body_band"),
    ("weak_column", "insufficient_column_support"),
    ("narrow_gutter", "gutter_support_too_narrow"),
])
def test_ambiguous_geometry_abstains_without_a_plan_or_partial_permutation(change, reason):
    observed = lines()
    if change == "three_columns":
        observed = lines(columns=3)
    elif change == "spanning_body":
        observed.insert(7, _line("SPANNING", 80, 375, 820, 400))
    elif change == "one_column":
        observed = [observed[0], *observed[1:-1:2], observed[-1]]
    elif change == "reversed":
        observed[1], observed[3] = observed[3], observed[1]
    elif change == "overlap":
        observed[3]["box"] = [[80, 215], [330, 215], [330, 245], [80, 245]]
    elif change == "duplicate_geometry":
        observed[3]["box"] = copy.deepcopy(observed[1]["box"])
    elif change == "noncontiguous":
        observed[0], observed[1] = observed[1], observed[0]
    elif change == "skew":
        observed[1]["box"] = [[80, 200], [330, 230], [330, 260], [80, 230]]
    elif change == "concave":
        observed[1]["box"] = [[80, 200], [330, 200], [100, 210], [80, 230]]
    elif change == "multiple_bodies":
        for line in observed[7:-1]:
            for point in line["box"]:
                point[1] += 200
    elif change == "too_few_lines":
        observed = lines(rows=2)
    elif change == "weak_column":
        observed = [line for index, line in enumerate(observed) if index not in {6, 8, 10, 12}]
    else:
        for index in range(2, 13, 2):
            for point in observed[index]["box"]:
                point[0] -= 200
    page = build(_report([_candidate(observed)]))["pages"][0]
    assert page["status"] == "abstained" and page["reason"] == reason
    assert page["plan"] is page["line_order"] is None


def test_full_requested_coverage_distinguishes_missing_empty_deferred_and_unselected():
    report = _report([_candidate(lines()), RuntimeError("unavailable"), _candidate([]),
                      _candidate(lines()), _candidate(lines())],
                     native=["short"] * 4 + [HEALTHY], max_pages=3)
    result = build(report, [5, 3, 1, 4, 2])
    assert [page["page_number"] for page in result["pages"]] == [1, 2, 3, 4, 5]
    assert [page["candidate_state"] for page in result["pages"]] == [
        "review_required", "retry_failed", "empty_candidate", "deferred", "not_selected"]
    assert result["summary"] == {"source_pages": 5, "requested_pages": 5, "suggested_pages": 1,
                                 "abstained_pages": 0, "unavailable_pages": 3, "empty_candidate_pages": 1}
    assert result["coverage"] == {"requested_pages": [1, 2, 3, 4, 5], "unrequested_source_pages": [],
                                  "suggested_pages": [1], "abstained_pages": [], "unavailable_pages": [2, 4, 5],
                                  "empty_candidate_pages": [3]}
    for index in (1, 3, 4):
        assert result["pages"][index]["candidate_sha256"] is None
        assert result["pages"][index]["line_count"] is None
        assert result["pages"][index]["plan"] is result["pages"][index]["line_order"] is None
    assert result["pages"][2]["line_count"] == 0 and result["pages"][2]["candidate_sha256"] is not None
    subset = build(report, [1, 4])
    assert subset["coverage"]["unrequested_source_pages"] == [2, 3, 5]


@pytest.mark.parametrize("mode", ["deskew", "contrast", "deskew-contrast"])
def test_valid_preprocessed_candidates_abstain_explicitly_even_with_identity_transform(mode):
    report = _report([_candidate(lines(), mode=mode)], mode=mode)
    result = build(report)
    page = result["pages"][0]
    assert page["status"] == "abstained" and page["reason"] == "unsupported_preprocessed_candidate"
    assert page["candidate_coordinate_system"] == "preprocessed_image_pixels"
    assert page["plan"] is page["line_order"] is None
    assert validate(result, report) == result


@pytest.mark.parametrize("requested", [None, (), {}, [], [True], [1.0], ["1"], [0], [2], [1, 1], [1] * 21])
def test_requested_page_contract_is_bounded_and_type_strict(requested):
    with pytest.raises(ValueError):
        suggestions.build_column_suggestions(_report(), recovery_sha256=RECOVERY_DIGEST, requested_pages=requested)


def many_lines(count):
    return [_line("x", 50 if index % 2 == 0 else 600, index // 2,
                  350 if index % 2 == 0 else 900, index // 2 + 0.4) for index in range(count)]


def test_line_admission_boundary_preserves_all_lines_or_abstains():
    maximum = build(_report([_candidate(many_lines(2000))]))["pages"][0]
    assert maximum["status"] == "unconfirmed_hypothesis"
    assert len(maximum["line_order"]) == 2000
    observed = many_lines(2001)
    for point in observed[-1]["box"]:
        point[1] -= 0.5
    report = _report([_candidate(observed)])
    page = build(report)["pages"][0]
    assert page["status"] == "abstained" and page["reason"] == "line_limit"
    assert page["line_count"] == 2001 and page["line_order"] is None


def test_work_budget_preflights_whole_requested_cohort_before_geometry(monkeypatch):
    report = _report([_candidate(lines()), _candidate(lines())])
    count = len(lines())
    per_page = 12 * count + 3 * count * count.bit_length()
    monkeypatch.setattr(suggestions, "MAX_GEOMETRY_WORK", per_page)
    assert build(report, [1])["pages"][0]["status"] == "unconfirmed_hypothesis"
    monkeypatch.setattr(suggestions, "_infer", lambda *_a: pytest.fail("over-budget geometry entered"))
    result = build(report, [1, 2])
    assert all(page["reason"] == "geometry_work_limit" for page in result["pages"])
    assert all(page["plan"] is page["line_order"] is None for page in result["pages"])


def test_output_byte_bound_is_exact(monkeypatch):
    report = _report([_candidate(lines())])
    result = build(report)
    size = len(suggestions._canonical(result))
    monkeypatch.setattr(suggestions, "MAX_OUTPUT_BYTES", size)
    assert build(report) == result
    monkeypatch.setattr(suggestions, "MAX_OUTPUT_BYTES", size - 1)
    with pytest.raises(ValueError):
        build(report)


@pytest.mark.parametrize("mutation", ["bool_version", "unknown_field", "bad_count", "bad_status", "approved",
    "missing_page", "duplicate_line", "partial_order", "changed_plan", "wrong_candidate", "wrong_source",
    "wrong_recovery", "bool_page", "float_order", "type_gutter", "deep_extra", "cycle", "preprocessed_spoof"])
def test_strict_rebuild_rejects_forged_fields_and_arbitrary_nested_payload(mutation):
    report = _report([_candidate(lines())])
    payload = build(report)
    page = payload["pages"][0]
    if mutation == "bool_version":
        payload["schema_version"] = True
    elif mutation == "unknown_field":
        payload["prose"] = True
    elif mutation == "bad_count":
        payload["summary"]["suggested_pages"] = True
    elif mutation == "bad_status":
        page["status"] = "approved"
    elif mutation == "approved":
        payload["operator_confirmation_required"] = False
    elif mutation == "missing_page":
        payload["pages"] = []
    elif mutation == "duplicate_line":
        page["line_order"][1] = page["line_order"][0]
    elif mutation == "partial_order":
        page["line_order"].pop()
    elif mutation == "changed_plan":
        page["plan"]["body_band"][0] += 0.01
    elif mutation == "wrong_candidate":
        page["candidate_sha256"] = "0" * 64
    elif mutation == "wrong_source":
        payload["source_sha256"] = "0" * 64
    elif mutation == "wrong_recovery":
        payload["recovery_sha256"] = "0" * 64
    elif mutation == "bool_page":
        page["page_number"] = True
    elif mutation == "float_order":
        page["line_order"][0] = 0.0
    elif mutation == "type_gutter":
        page["plan"]["gutter"] = tuple(page["plan"]["gutter"])
    elif mutation == "deep_extra":
        nested = []
        for _ in range(2000):
            nested = [nested]
        page["private_extra"] = nested
    elif mutation == "cycle":
        page["plan"] = payload
    else:
        page["candidate_coordinate_system"] = "preprocessed_image_pixels"
    with pytest.raises(ValueError):
        validate(payload, report)


@pytest.mark.parametrize("mutation", ["outside_raster", "bool_coordinate", "nan_coordinate", "text_mismatch",
                                     "unknown_candidate_field", "invalid_preprocessing", "bad_summary"])
def test_malformed_recovery_never_becomes_a_weak_geometry_suggestion(mutation):
    report = _report([_candidate(lines())])
    candidate = report["pages"][0]["candidate"]
    if mutation == "outside_raster":
        candidate["lines"][1]["box"][0][0] = 1001
    elif mutation == "bool_coordinate":
        candidate["lines"][1]["box"][0][0] = True
    elif mutation == "nan_coordinate":
        candidate["lines"][1]["box"][0][0] = float("nan")
    elif mutation == "text_mismatch":
        candidate["text"] = "not the exact observed text"
    elif mutation == "unknown_candidate_field":
        candidate["approved"] = True
    elif mutation == "invalid_preprocessing":
        candidate["raster"]["coordinate_system"] = "preprocessed_image_pixels"
    else:
        report["summary"]["selected"] = True
    with pytest.raises(ValueError):
        build(report)


def test_pure_policy_import_does_not_load_ocr_or_models():
    completed = subprocess.run([sys.executable, "-B", "-c", (
        "import sys; import ocr_column_suggestions; "
        "assert not {'rag','pymupdf','cv2','numpy','torch','rapidocr','onnxruntime'} & set(sys.modules)"
    )], capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stderr
