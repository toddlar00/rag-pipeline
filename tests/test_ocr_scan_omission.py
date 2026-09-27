"""Declared synthetic scan observations; no pixel discovery or accuracy claims."""

import copy
import hashlib
import json

import pytest

import ocr_docling
import ocr_recovery
import ocr_scan_omission as policy


SOURCE = "a" * 64
RECOVERY = "b" * 64
PROPOSALS = "c" * 64


def observation(pages=(1,), *, page_count=3):
    records = []
    for number in pages:
        records.append({"page_number": number,
            "geometry": {"width_points": 240., "height_points": 240., "rotation": 0, "cropbox": [0., 0., 240., 240.]},
            "raster": {"width": 1000, "height": 1000, "dpi": 300, "format": "gray8", "pixel_sha256": "d" * 64},
            "discovery": {"status": "available", "reason": "bounded_pixel_discovery", "threshold": 128.,
                "threshold_foreground_pixels": 90, "foreground_accounting_complete": True,
                "work": {"rule_horizontal_runs": 0, "residual_horizontal_runs": 30,
                         "rule_component_count": 0, "residual_component_count": 3, "pair_tests": 3},
                "regions": [{"region_id": "scan-00001", "kind": "text_like",
                    "reason": "aligned_glyph_scale_components_not_text_proof", "bbox": [100, 100, 300, 120],
                    "component_count": 3, "foreground_pixels": 90}]}})
    return {"schema_version": 1, "kind": "ocr_scan_observation", "source_sha256": SOURCE,
            "page_count": page_count, "requested_pages": list(pages),
            "configuration": copy.deepcopy(policy.DEFAULT_CONFIGURATION), "pages": records}


def unavailable(page, reason="render_failed"):
    page["raster"] = None
    page["discovery"] = {"status": "unavailable", "reason": reason, "threshold": None,
        "threshold_foreground_pixels": None, "foreground_accounting_complete": False, "regions": [],
        "work": {"rule_horizontal_runs": None, "residual_horizontal_runs": None,
                 "rule_component_count": None, "residual_component_count": None, "pair_tests": 0}}


def recovery(box=None, *, text="Synthetic critical text: 14 days, not 40.", requested=(1,), failed=False):
    box = [90, 90, 310, 130] if box is None else box
    left, top, right, bottom = box
    candidate = {"text": text, "lines": [{"text": text, "score": .95,
        "box": [[left, top], [right, top], [right, bottom], [left, bottom]]}], "mean_confidence": .95,
        "raster": {"width": 1000, "height": 1000, "dpi": 300, "coordinate_system": "rendered_image_pixels"},
        "engine": {"name": "rapidocr", "version": "synthetic-no-execution", "min_score": 0., "max_side": 6000}}

    class Reader:
        page_count = 3

        def native_text(self, number):
            return "" if number in requested else "Synthetic healthy native text. " * 20

        def retry(self, number):
            if failed:
                raise RuntimeError("synthetic failure")
            return copy.deepcopy(candidate)

    report = ocr_recovery.build_recovery_report(Reader(), source_sha256=SOURCE,
        policy=ocr_recovery.RetryPolicy(), requested_pages=requested)
    report["evidence_sha256"] = None
    return report


def proposals(report, bounds=(150, 150, 220, 220)):
    left, top, right, bottom = bounds
    doc = {"schema_name": "DoclingDocument", "version": "1.0.0",
           "pages": {"1": {"page_no": 1, "size": {"width": 240., "height": 240.}}},
           "texts": [{"self_ref": "#/texts/0", "label": "text", "text": "Synthetic saved region",
                      "children": [], "prov": [{"page_no": 1, "bbox": {
                          "l": left, "t": top, "r": right, "b": bottom, "coord_origin": "TOPLEFT"}}]}],
           "tables": [], "pictures": [], "groups": [],
           "body": {"self_ref": "#/body", "children": [{"$ref": "#/texts/0"}]},
           "furniture": {"self_ref": "#/furniture", "children": []}}
    return ocr_docling.build_docling_proposals(doc, report, recovery_sha256=RECOVERY,
        docling_sha256="e" * 64, manifest_sha256="f" * 64, effective_input_kind="original")


def test_source_only_observation_is_detached_and_never_certifies_transcription():
    original = observation()
    before = copy.deepcopy(original)
    validated = policy.validate_scan_observation(original)
    report = policy.build_scan_omission_report(original)
    assert report["coverage"] == {"source_pages": 3, "requested_pages": [1], "unrequested_pages": [2, 3]}
    assert report["pages"][0]["candidate_state"] == "not_supplied"
    assert report["pages"][0]["ocr_comparison_state"] == "not_supplied"
    assert report["summary"]["ocr_unevaluated_regions"] == 1
    assert report["summary"]["text_like_regions"] == 1
    assert report["requires_attention"] is report["operator_review_required"] is True
    for key in ("full_page_coverage_verified", "accuracy_verified", "recognition_rerun",
                "canonical_extraction_modified", "observation_authenticity_verified"):
        assert report[key] is False
    assert policy.validate_scan_omission_report(report, original) == report
    validated["pages"][0]["discovery"]["regions"][0]["bbox"][0] = 0
    report["pages"][0]["regions"][0]["bbox"][0] = 0
    assert original == before


def test_jointly_absent_saved_regions_do_not_remove_independent_scan_findings():
    scan = observation()
    saved = recovery([600, 600, 900, 650])
    layout = proposals(saved)
    before = copy.deepcopy((scan, saved, layout))
    result = policy.build_scan_omission_report(scan, recovery=saved, recovery_sha256=RECOVERY,
                                              proposals=layout, proposals_sha256=PROPOSALS)
    region = result["pages"][0]["regions"][0]
    assert region["ocr_status"] == "no_line_overlap"
    assert region["layout_status"] == "no_saved_region_overlap"
    assert region["potential_ocr_omission"] is region["unmatched_in_both_saved_inputs"] is True
    assert result["summary"]["potential_ocr_omissions"] == 1
    assert result["summary"]["unmatched_in_both_saved_inputs"] == 1
    assert (scan, saved, layout) == before
    assert "Synthetic" not in json.dumps(result)


@pytest.mark.parametrize("box,expected", [
    ([90, 90, 310, 130], "contained_in_nonempty_line_box"),
    ([90, 90, 150, 130], "partial_or_ambiguous_overlap"),
    ([300, 100, 310, 130], "partial_or_ambiguous_overlap"),
    ([310, 100, 320, 130], "no_line_overlap"),
])
def test_nonempty_box_overlap_does_not_imply_correct_recognized_text(box, expected):
    result = policy.build_scan_omission_report(observation(), recovery=recovery(box), recovery_sha256=RECOVERY)
    region = result["pages"][0]["regions"][0]
    assert region["ocr_status"] == expected
    assert region["semantic_role"] == "unknown" and region["transcription_verified"] is False
    assert result["full_page_coverage_verified"] is False


def test_empty_ocr_text_is_not_nonempty_coverage():
    result = policy.build_scan_omission_report(observation(), recovery=recovery(text=" "), recovery_sha256=RECOVERY)
    page = result["pages"][0]
    assert page["candidate_state"] == "empty"
    assert page["regions"][0]["ocr_status"] == "empty_text_overlap_only"
    assert page["regions"][0]["potential_ocr_omission"] is True


def test_skewed_nonempty_quadrilateral_does_not_supply_containment():
    saved = recovery()
    saved["pages"][0]["candidate"]["lines"][0]["box"][0][0] += 5
    result = policy.build_scan_omission_report(observation(), recovery=saved, recovery_sha256=RECOVERY)
    assert result["pages"][0]["regions"][0]["ocr_status"] == "partial_or_ambiguous_overlap"


@pytest.mark.parametrize("failed,requested,page,expected", [
    (True, (1,), 1, "retry_failed"), (False, (1,), 3, "not_selected"),
])
def test_missing_candidates_do_not_suppress_scan_observations(failed, requested, page, expected):
    result = policy.build_scan_omission_report(observation((page,)),
        recovery=recovery(requested=requested, failed=failed), recovery_sha256=RECOVERY)
    record = result["pages"][0]
    assert record["scan_status"] == "available" and record["candidate_state"] == expected
    assert record["ocr_comparison_state"] == "candidate_unavailable"
    assert len(record["regions"]) == 1


def test_geometry_mismatch_abstains_only_from_saved_correspondence():
    scan = observation()
    saved = recovery()
    saved["pages"][0]["candidate"]["raster"]["width"] = 1100
    result = policy.build_scan_omission_report(scan, recovery=saved, recovery_sha256=RECOVERY)
    assert result["pages"][0]["ocr_comparison_state"] == "geometry_mismatch"
    assert result["summary"]["text_like_regions"] == 1


def test_global_comparison_work_limit_keeps_all_independent_findings(monkeypatch):
    monkeypatch.setattr(policy, "MAX_COMPARISONS", 1)
    scan = observation((1, 2))
    result = policy.build_scan_omission_report(scan, recovery=recovery(requested=(1, 2)), recovery_sha256=RECOVERY)
    assert [page["ocr_comparison_state"] for page in result["pages"]] == ["work_limit", "work_limit"]
    assert result["summary"]["text_like_regions"] == 2
    assert result["summary"]["ocr_unevaluated_regions"] == 2


@pytest.mark.parametrize("kind,reason", [("rule_like", "long_axis_morphology_not_semantic_nontext"),
                                        ("ambiguous_ink", "tiny_or_large_residual_component")])
def test_rule_and_ambiguous_ink_are_not_silently_deleted_or_certified_nontext(kind, reason):
    scan = observation()
    scan["pages"][0]["discovery"]["regions"][0].update(kind=kind, reason=reason)
    result = policy.build_scan_omission_report(scan, recovery=recovery([600, 600, 900, 650]), recovery_sha256=RECOVERY)
    region = result["pages"][0]["regions"][0]
    assert region["kind"] == kind and region["ocr_status"] == "no_line_overlap"
    assert region["potential_ocr_omission"] is False and region["semantic_role"] == "unknown"
    assert result["requires_attention"] is True


def test_threshold_blank_is_not_a_blank_source_certification():
    scan = observation()
    discovery = scan["pages"][0]["discovery"]
    discovery.update(status="blank_at_threshold", reason="no_dark_otsu_foreground",
                     threshold=0, threshold_foreground_pixels=0, regions=[])
    discovery["work"] = {key: 0 if key == "pair_tests" else None for key in policy._WORK_KEYS}
    result = policy.build_scan_omission_report(scan)
    assert result["summary"]["blank_at_threshold_pages"] == 1
    assert result["requires_attention"] is True and result["full_page_coverage_verified"] is False


def test_dense_ink_is_preserved_without_fabricated_components():
    scan = observation()
    discovery = scan["pages"][0]["discovery"]
    discovery.update(status="ambiguous", reason="dense_foreground_not_text_classified", threshold_foreground_pixels=500_000)
    discovery["regions"][0].update(kind="ambiguous_ink", reason="dense_foreground", bbox=[0, 0, 1000, 1000],
                                  foreground_pixels=500_000, component_count=None)
    discovery["work"] = {key: 0 if key == "pair_tests" else None for key in policy._WORK_KEYS}
    result = policy.build_scan_omission_report(scan)
    assert result["summary"]["ambiguous_pages"] == result["summary"]["ambiguous_ink_regions"] == 1


def test_render_failure_keeps_requested_page_and_explicit_unevaluated_state():
    scan = observation((1, 2))
    unavailable(scan["pages"][1])
    result = policy.build_scan_omission_report(scan)
    assert result["coverage"]["requested_pages"] == [1, 2]
    assert result["summary"]["unavailable_pages"] == 1
    assert result["pages"][1]["ocr_comparison_state"] == "scan_unavailable"


@pytest.mark.parametrize("path,value", [
    (("schema_version",), True), (("kind",), "other"), (("source_sha256",), "bad"),
    (("page_count",), False), (("page_count",), 5001), (("requested_pages",), []),
    (("requested_pages",), [True]), (("requested_pages",), [1, 1]), (("requested_pages",), [2]),
    (("configuration", "dpi"), 400), (("pages", 0, "page_number"), True),
    (("pages", 0, "geometry", "width_points"), 10 ** 1000),
    (("pages", 0, "geometry", "width_points"), float("nan")),
    (("pages", 0, "geometry", "height_points"), float("inf")),
    (("pages", 0, "geometry", "rotation"), 45),
    (("pages", 0, "geometry", "cropbox"), [0, 0, 0, 10]),
    (("pages", 0, "raster", "width"), True), (("pages", 0, "raster", "width"), 6001),
    (("pages", 0, "raster", "format"), "rgb8"), (("pages", 0, "raster", "dpi"), 400),
    (("pages", 0, "raster", "pixel_sha256"), "bad"),
    (("pages", 0, "discovery", "status"), "complete"),
    (("pages", 0, "discovery", "reason"), "private runtime error"),
    (("pages", 0, "discovery", "threshold"), 256),
    (("pages", 0, "discovery", "foreground_accounting_complete"), 1),
    (("pages", 0, "discovery", "threshold_foreground_pixels"), 89),
    (("pages", 0, "discovery", "work", "pair_tests"), 1_000_001),
    (("pages", 0, "discovery", "work", "residual_horizontal_runs"), 2),
    (("pages", 0, "discovery", "work", "residual_component_count"), 4),
    (("pages", 0, "discovery", "regions", 0, "region_id"), "scan-00002"),
    (("pages", 0, "discovery", "regions", 0, "bbox"), [100, 100, 1001, 120]),
    (("pages", 0, "discovery", "regions", 0, "bbox"), [100, 100, 300, 100]),
    (("pages", 0, "discovery", "regions", 0, "component_count"), 2),
    (("pages", 0, "discovery", "regions", 0, "component_count"), None),
])
def test_invalid_observation_fields_fail_with_bounded_static_error(path, value):
    scan = observation()
    target = scan
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValueError):
        policy.validate_scan_observation(scan)


@pytest.mark.parametrize("path", [(), ("configuration",), ("pages", 0),
    ("pages", 0, "geometry"), ("pages", 0, "raster"), ("pages", 0, "discovery"),
    ("pages", 0, "discovery", "work"), ("pages", 0, "discovery", "regions", 0)])
def test_cyclic_extra_fields_are_rejected_before_deep_serialization(path):
    scan = observation()
    target = scan
    for key in path:
        target = target[key]
    target["extra"] = target
    with pytest.raises(ValueError):
        policy.validate_scan_observation(scan)


def test_cohort_rejection_cannot_coexist_with_rendered_success():
    scan = observation((1, 2))
    unavailable(scan["pages"][1], "cohort_pixel_limit")
    with pytest.raises(ValueError, match="cohort"):
        policy.validate_scan_observation(scan)
    unavailable(scan["pages"][0], "cohort_pixel_limit")
    scan["pages"][0]["geometry"] = None
    assert policy.validate_scan_observation(scan) == scan


@pytest.mark.parametrize("options", [
    {"recovery_sha256": RECOVERY}, {"recovery": recovery()},
    {"proposals_sha256": PROPOSALS}, {"proposals": {}}, {"proposals": {}, "proposals_sha256": PROPOSALS},
])
def test_optional_comparison_bindings_are_not_partial(options):
    with pytest.raises(ValueError):
        policy.build_scan_omission_report(observation(), **options)


def test_forged_rebound_summary_flags_and_extra_fields_are_rejected():
    scan = observation()
    report = policy.build_scan_omission_report(scan)
    for path in (("summary", "text_like_regions"), ("coverage", "source_pages"),
                 ("pages", 0, "page_number"), ("accuracy_verified",)):
        changed = copy.deepcopy(report)
        target = changed
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = True
        with pytest.raises(ValueError):
            policy.validate_scan_omission_report(changed, scan)
    report["extra"] = report
    with pytest.raises(ValueError):
        policy.validate_scan_omission_report(report, scan)


def test_observation_digest_is_domain_separated_and_changes_with_pixel_identity():
    scan = observation()
    result = policy.build_scan_omission_report(scan)
    digest_policy = result["observation_digest_policy"]
    assert digest_policy["is_file_digest"] is False
    expected = hashlib.sha256(digest_policy["domain"].encode() + b"\0" + policy._canonical(scan)).hexdigest()
    assert result["observation_content_sha256"] == expected
    scan["pages"][0]["raster"]["pixel_sha256"] = "e" * 64
    assert policy.build_scan_omission_report(scan)["observation_content_sha256"] != expected


@pytest.mark.parametrize("constant", ["MAX_OBSERVATION_BYTES", "MAX_REPORT_BYTES"])
def test_encoded_artifact_bounds_are_enforced(monkeypatch, constant):
    monkeypatch.setattr(policy, constant, 32)
    with pytest.raises(ValueError, match="byte budget"):
        policy.build_scan_omission_report(observation())


def test_valid_preprocessed_candidate_abstains_without_discarding_pixel_findings():
    from test_ocr_preprocessing_integration import _Reader, _processed_candidate

    saved = ocr_recovery.build_recovery_report(_Reader(_processed_candidate()), source_sha256=SOURCE,
        policy=ocr_recovery.RetryPolicy(dpi=72, preprocessing="contrast"), requested_pages=(1,))
    saved["evidence_sha256"] = None
    scan = observation(page_count=1)
    scan["pages"][0]["geometry"] = {
        "width_points": 72., "height_points": 36., "rotation": 0, "cropbox": [0., 0., 72., 36.]}
    scan["pages"][0]["raster"].update(width=300, height=150)
    scan["pages"][0]["discovery"]["regions"][0]["bbox"] = [10, 10, 60, 20]
    result = policy.build_scan_omission_report(scan, recovery=saved, recovery_sha256=RECOVERY)
    page = result["pages"][0]
    assert page["candidate_state"] == "available"
    assert page["ocr_comparison_state"] == "unsupported_preprocessing"
    assert len(page["regions"]) == 1 and page["regions"][0]["ocr_status"] == "unevaluated"
    assert result["summary"]["text_like_regions"] == result["summary"]["ocr_unevaluated_regions"] == 1


def test_deferred_page_is_not_an_empty_or_successful_ocr_candidate():
    candidate = recovery()["pages"][0]["candidate"]

    class Reader:
        page_count = 3

        def native_text(self, number):
            return ""

        def retry(self, number):
            return copy.deepcopy(candidate)

    saved = ocr_recovery.build_recovery_report(Reader(), source_sha256=SOURCE,
        policy=ocr_recovery.RetryPolicy(max_pages=1), requested_pages=(1, 2))
    saved["evidence_sha256"] = None
    result = policy.build_scan_omission_report(observation((2,)), recovery=saved, recovery_sha256=RECOVERY)
    page = result["pages"][0]
    assert page["candidate_state"] == "deferred" and page["ocr_comparison_state"] == "candidate_unavailable"
    assert page["scan_status"] == "available" and len(page["regions"]) == 1
    assert page["regions"][0]["potential_ocr_omission"] is False
    assert result["summary"]["ocr_unevaluated_regions"] == 1


@pytest.mark.parametrize("field,value", [("source_sha256", "9" * 64), ("page_count", 4)])
def test_different_recovery_source_identity_or_page_count_fails_closed(field, value):
    saved = recovery()
    saved[field] = value
    if field == "page_count":
        saved["summary"]["inspected"] = value
    with pytest.raises(ValueError, match="source generations differ"):
        policy.build_scan_omission_report(observation(), recovery=saved, recovery_sha256=RECOVERY)


@pytest.mark.parametrize("path", [("kind",), ("pages", 0, "raster", "format"),
                                   ("pages", 0, "discovery", "regions", 0, "region_id")])
def test_discriminators_require_exact_builtin_string_types(path):
    class StringSubclass(str):
        pass

    scan = observation()
    target = scan
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = StringSubclass(target[path[-1]])
    with pytest.raises(ValueError):
        policy.validate_scan_observation(scan)


@pytest.mark.parametrize("line_count,state", [(2000, "supported"), (2001, "line_limit")])
def test_saved_line_admission_is_bounded_and_does_not_truncate(line_count, state):
    saved = recovery(text="S")
    candidate = saved["pages"][0]["candidate"]
    candidate["lines"] = [copy.deepcopy(candidate["lines"][0]) for _ in range(line_count)]
    candidate["text"] = "\n".join(line["text"] for line in candidate["lines"])
    result = policy.build_scan_omission_report(observation(), recovery=saved, recovery_sha256=RECOVERY)
    page = result["pages"][0]
    assert page["ocr_comparison_state"] == state and len(page["regions"]) == 1
    counts = page["regions"][0]["ocr_overlap_counts"]
    if state == "supported":
        assert counts["containing_nonempty_boxes"] == line_count
    else:
        assert set(counts.values()) == {None} and page["regions"][0]["ocr_status"] == "unevaluated"


@pytest.mark.parametrize("box", [
    [300 + 5e-10, 100, 310, 130],  # Roundoff only suppresses an absence claim.
    [100 + 5e-10, 90, 310, 130],   # It must never promote near-containment.
])
def test_roundoff_does_not_create_absence_or_false_containment(box):
    result = policy.build_scan_omission_report(observation(), recovery=recovery(box), recovery_sha256=RECOVERY)
    region = result["pages"][0]["regions"][0]
    assert region["ocr_status"] == "partial_or_ambiguous_overlap"
    assert region["ocr_overlap_counts"]["containing_nonempty_boxes"] == 0
    assert region["potential_ocr_omission"] is False and region["transcription_verified"] is False


@pytest.mark.parametrize("bounds,status", [
    ((20, 20, 80, 40), "contained_in_saved_region"),
    ((72, 24, 96, 48), "partial_overlap"),
])
def test_saved_layout_overlap_cannot_supply_missing_ocr_text(bounds, status):
    saved = recovery([600, 600, 900, 650])
    layout = proposals(saved, bounds=bounds)
    result = policy.build_scan_omission_report(observation(), recovery=saved, recovery_sha256=RECOVERY,
                                              proposals=layout, proposals_sha256=PROPOSALS)
    region = result["pages"][0]["regions"][0]
    assert region["layout_status"] == status and region["ocr_status"] == "no_line_overlap"
    assert region["potential_ocr_omission"] is True and region["unmatched_in_both_saved_inputs"] is False
    assert region["transcription_verified"] is False


def test_proposal_cannot_join_a_different_saved_recovery_generation():
    saved = recovery()
    layout = proposals(saved)
    with pytest.raises(ValueError):
        policy.build_scan_omission_report(observation(), recovery=saved, recovery_sha256="9" * 64,
                                          proposals=layout, proposals_sha256=PROPOSALS)
