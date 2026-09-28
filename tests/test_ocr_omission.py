"""Synthetic-only saved-layout omission diagnostics and partial human decisions."""

from __future__ import annotations

import copy
import hashlib
import json

import pytest

import ocr_docling
import ocr_omission as policy
import ocr_recovery
from test_ocr_docling import DIGESTS, PRIVATE, SOURCE, bbox, candidate, document, recovery


BINDINGS = {"recovery_sha256": DIGESTS["recovery_sha256"], "proposals_sha256": "e" * 64}


def context(value=None, doc=None, *, report=None, effective_input_kind="original"):
    report = recovery([candidate() if value is None else value]) if report is None else report
    proposals = ocr_docling.build_docling_proposals(document() if doc is None else doc, report,
        **DIGESTS, effective_input_kind=effective_input_kind)
    return report, proposals


def inspect(value=None, doc=None, **kwargs):
    return policy.build_omission_diagnostics(*context(value, doc, **kwargs), **BINDINGS)


def lines(value, selected):
    value["lines"] = [copy.deepcopy(value["lines"][index]) for index in selected]
    value["text"] = "\n".join(line["text"] for line in value["lines"])
    value["mean_confidence"] = .9 if value["lines"] else None
    return value


def region(report, ref, page=1):
    return next(r for r in report["pages"][page-1]["regions"] if r["ref"] == ref)


def test_all_line_geometry_is_not_a_text_completeness_attestation_and_no_text_leaks():
    report, proposals = context()
    before = copy.deepcopy((report, proposals))
    result = policy.build_omission_diagnostics(report, proposals, **BINDINGS)
    assert result["summary"]["has_nonempty_line_geometry"] == 4
    assert result["summary"]["attention_regions"] == 0
    assert result["summary"]["evaluated_pages"] == 1
    assert result["requires_attention"] is False
    assert result["scope"] == "saved_docling_regions_only_not_source_completeness"
    for key in ("accuracy_verified", "full_page_coverage_verified", "recognition_rerun", "canonical_extraction_modified"):
        assert result[key] is False
    assert PRIVATE not in json.dumps(result)
    assert (report, proposals) == before
    assert policy.validate_omission_diagnostics(result, report, proposals, **BINDINGS) == result
    result["pages"][0]["regions"][0]["bbox"][0] = 0
    assert proposals == before[1]


def test_high_confidence_long_text_elsewhere_does_not_hide_wholly_missing_column():
    value = lines(candidate(), [0, 1, 3, 5])
    value["lines"][1]["text"] = "Synthetic unrelated text " * 100
    value["text"] = "\n".join(line["text"] for line in value["lines"])
    result = inspect(value)
    right = region(result, "#/texts/2")
    assert right["status"] == "no_candidate_line_overlap"
    assert all(right[key] == 0 for key in policy._COUNTS)
    assert result["summary"]["attention_regions"] == 1
    assert result["requires_attention"] is True


def test_missing_one_line_inside_nonempty_region_is_explicitly_outside_this_detector():
    result = inspect(lines(candidate(), [0, 1, 2, 4, 5]))
    assert region(result, "#/texts/1")["status"] == "has_nonempty_line_geometry"
    assert region(result, "#/texts/1")["nonempty_contained_lines"] == 1
    assert result["full_page_coverage_verified"] is False


@pytest.mark.parametrize("bounds", [(5, 25, 35, 50), (40, 25, 45, 50), (10, 35, 40, 40)])
def test_boundary_crossing_or_exact_touch_is_uncertain_not_absent(bounds):
    doc = document()
    doc["texts"][1]["prov"][0]["bbox"] = bbox(bounds)
    result = inspect(doc=doc)
    found = region(result, "#/texts/1")
    assert found["status"] == "boundary_or_ambiguous_overlap"
    assert found["nonempty_boundary_overlap_lines"] == 2
    assert "unassigned_lines" in result["pages"][0]["proposal_reasons"]


@pytest.mark.parametrize("gap,status", [(1e-13, "boundary_or_ambiguous_overlap"),
                                      (2e-12, "no_candidate_line_overlap")])
def test_declared_roundoff_guard_never_promotes_containment_and_does_not_bridge_larger_gaps(gap, status):
    value = lines(candidate(), [0, 1, 2, 4, 5])
    x = 45 + gap * 100
    value["lines"][1]["box"] = [[x, 30], [48, 30], [48, 35], [x, 35]]
    result = inspect(value)
    found = region(result, "#/texts/1")
    assert found["status"] == status
    assert found["nonempty_contained_lines"] == 0
    assert result["parameters"]["overlap_roundoff_fraction"] == 1e-12


@pytest.mark.parametrize("box", [
    [[10, 30], [40, 39], [40, 44], [10, 35]],  # significant skew
    [[10, 30], [40, 30], [20, 32], [10, 35]],  # concavity
    [[8, 31], [30, 28], [43, 32], [15, 38]],   # unsupported quadrilateral
])
def test_unsupported_line_geometry_is_a_warning_even_when_aabb_is_inside(box):
    value = lines(candidate(), [0, 1, 2, 4, 5])
    value["lines"][1]["box"] = box
    result = inspect(value)
    found = region(result, "#/texts/1")
    assert found["status"] == "boundary_or_ambiguous_overlap"
    assert found["ambiguous_geometry_overlap_lines"] == 1
    assert found["nonempty_contained_lines"] == 0


@pytest.mark.parametrize("text", ["", " ", "\t\n", "\u2003\u00a0"])
def test_empty_or_unicode_whitespace_line_text_does_not_count_as_recognized_text(text):
    value = candidate()
    for index in (2, 4):
        value["lines"][index]["text"] = text
    value["text"] = "\n".join(line["text"] for line in value["lines"])
    right = region(inspect(value), "#/texts/2")
    assert right["status"] == "empty_text_only"
    assert right["empty_text_overlap_lines"] == 2
    assert right["nonempty_contained_lines"] == 0


def test_empty_candidate_can_have_evaluated_known_regions_but_failed_cannot():
    empty = lines(candidate(), [])
    result = inspect(empty)
    assert result["pages"][0]["candidate_state"] == "empty"
    assert result["pages"][0]["layout_state"] == "supported"
    assert result["summary"]["no_candidate_line_overlap"] == 4
    failed = inspect(report=recovery([RuntimeError(PRIVATE)]))
    assert failed["pages"][0]["candidate_state"] == "retry_failed"
    assert failed["pages"][0]["layout_state"] == "candidate_unavailable"
    assert failed["pages"][0]["regions"] == []
    assert failed["summary"]["no_candidate_line_overlap"] == 0
    assert failed["summary"]["unevaluated_pages"] == 1


def test_all_source_pages_include_failed_deferred_and_not_selected_without_fabricated_regions():
    class Reader:
        page_count = 4

        def native_text(self, number):
            return "Synthetic native text is already sufficiently long. " * 3 if number == 4 else ""

        def retry(self, number):
            if number == 2:
                raise RuntimeError(PRIVATE)
            return candidate()

    report = ocr_recovery.build_recovery_report(Reader(), source_sha256=SOURCE,
        policy=ocr_recovery.RetryPolicy(max_pages=2))
    report["evidence_sha256"] = None
    result = inspect(report=report)
    assert [p["candidate_state"] for p in result["pages"]] == ["available", "retry_failed", "deferred", "not_selected"]
    assert all(p["regions"] == [] for p in result["pages"][1:])
    assert result["summary"]["source_pages"] == 4
    assert result["summary"]["unevaluated_pages"] == 3
    assert result["requires_attention"] is True


def test_missing_saved_regions_is_not_proof_of_no_text():
    doc = document()
    doc["texts"] = []
    doc["tables"] = []
    doc["body"]["children"] = []
    result = inspect(doc=doc)
    assert result["pages"][0]["layout_state"] == "no_saved_regions"
    assert result["summary"]["target_regions"] == 0
    assert result["requires_attention"] is True


def test_picture_only_cohort_is_unevaluated_and_never_called_text_complete():
    doc = document()
    picture = copy.deepcopy(doc["texts"][0])
    picture.update(self_ref="#/pictures/0", label="picture", children=[])
    doc.update(texts=[], tables=[], pictures=[picture])
    doc["body"]["children"] = [{"$ref": "#/pictures/0"}]
    result = inspect(doc=doc)
    assert result["pages"][0]["layout_state"] == "no_target_regions"
    found = region(result, "#/pictures/0")
    assert found["status"] == "not_target_region"
    assert all(found[key] is None for key in policy._COUNTS)
    assert result["requires_attention"] is True


def test_furniture_is_visible_and_can_be_a_missing_footnote_false_positive():
    doc = document()
    doc["texts"][0]["children"].remove({"$ref": "#/tables/0"})
    doc["furniture"]["children"] = [{"$ref": "#/tables/0"}]
    result = inspect(lines(candidate(), [0, 1, 2, 3, 4]), doc)
    assert region(result, "#/tables/0")["tree"] == "furniture"
    assert result["summary"]["furniture_attention_regions"] == 1
    assert result["summary"]["body_attention_regions"] == 0


def test_overlapping_regions_do_not_sum_to_unique_source_coverage_or_hide_ambiguity_reasons():
    doc = document()
    doc["texts"][1]["prov"][0]["bbox"] = bbox((5, 25, 95, 50))
    result = inspect(doc=doc)
    assert region(result, "#/texts/1")["nonempty_contained_lines"] == 4
    assert region(result, "#/texts/2")["nonempty_contained_lines"] == 2
    assert "ambiguous_line_assignment" in result["pages"][0]["proposal_reasons"]
    assert result["full_page_coverage_verified"] is False


@pytest.mark.parametrize("mutation", [
    lambda d: d["texts"][1].update(prov=[]),
    lambda d: d["texts"][1]["prov"][0]["bbox"].update(l=-1),
    lambda d: d["texts"][1]["prov"].append(copy.deepcopy(d["texts"][1]["prov"][0])),
    lambda d: d["texts"][0]["children"].pop(),
    lambda d: d["tables"][0]["data"].update(table_cells=[]),
])
def test_incomplete_saved_layout_evidence_remains_attention_even_when_remaining_regions_have_lines(mutation):
    doc = document()
    mutation(doc)
    result = inspect(doc=doc)
    assert result["summary"]["incomplete_layout_pages"] == 1
    assert result["requires_attention"] is True


@pytest.mark.parametrize("mutation,state", [
    (lambda d: d["pages"].clear(), "missing_or_mismatched_geometry"),
    (lambda d: d["pages"]["1"]["size"].update(width=48), "missing_or_mismatched_geometry"),
])
def test_missing_or_mismatched_page_geometry_never_compares_regions(mutation, state):
    doc = document()
    mutation(doc)
    result = inspect(doc=doc)
    assert result["pages"][0]["layout_state"] == state
    assert result["pages"][0]["regions"] == []
    assert result["summary"]["no_candidate_line_overlap"] == 0
    assert result["requires_attention"] is True


def test_effective_preprocessing_is_explicitly_unsupported():
    result = inspect(effective_input_kind="preprocessed")
    assert result["pages"][0]["layout_state"] == "unsupported_preprocessed"
    assert result["pages"][0]["regions"] == []
    assert result["requires_attention"] is True


def test_identity_v2_preprocessing_still_requires_unsupported_geometry_abstention():
    from test_ocr_layout import _metadata

    report = recovery()
    report["schema_version"] = 2
    report["retry_configuration"]["preprocessing"] = "contrast"
    value = report["pages"][0]["candidate"]
    value["raster"]["coordinate_system"] = "preprocessed_image_pixels"
    value["preprocessing"] = _metadata("contrast")
    value["preprocessing"]["original_raster"] = {"width": 100, "height": 100}
    value["preprocessing"]["processed_raster"] = {"width": 100, "height": 100}
    result = inspect(report=report)
    assert result["pages"][0]["candidate_state"] == "available"
    assert result["pages"][0]["layout_state"] == "unsupported_preprocessed"
    assert result["pages"][0]["regions"] == []
    assert result["summary"]["no_candidate_line_overlap"] == 0


def test_geometry_abstention_cannot_smuggle_saved_regions_into_diagnostics():
    report, proposals = context(effective_input_kind="preprocessed")
    proposals["pages"][0]["regions"] = context()[1]["pages"][0]["regions"]
    proposals["summary"]["regions"] = 4
    with pytest.raises(ValueError):
        policy.build_omission_diagnostics(report, proposals, **BINDINGS)


def test_empty_selected_cohort_still_lists_every_source_page():
    class Reader:
        page_count = 5000

        def native_text(self, _number):
            return "Synthetic native text is sufficiently long to remain unselected."

        def retry(self, _number):
            raise AssertionError("unselected pages must not run OCR")

    report = ocr_recovery.build_recovery_report(Reader(), source_sha256=SOURCE,
        policy=ocr_recovery.RetryPolicy(max_pages=20))
    report["evidence_sha256"] = None
    result = inspect(report=report)
    assert len(result["pages"]) == 5000
    assert result["summary"]["not_selected_pages"] == 5000
    assert result["summary"]["unevaluated_pages"] == 5000
    assert result["summary"]["evaluated_pages"] == 0
    assert result["requires_attention"] is True


def test_more_than_ui_line_limit_retains_regions_but_no_fabricated_zero_counts():
    value = candidate()
    value["lines"] = [copy.deepcopy(value["lines"][1]) for _ in range(2001)]
    value["text"] = "\n".join(line["text"] for line in value["lines"])
    result = inspect(value)
    assert result["pages"][0]["layout_state"] == "line_limit"
    assert result["summary"]["unevaluated_regions"] == 4
    assert all(r[key] is None for r in result["pages"][0]["regions"] for key in policy._COUNTS)


def test_work_budget_preflights_before_any_geometry_and_abstains_whole_cohort(monkeypatch):
    report, proposals = context()
    monkeypatch.setattr(policy, "MAX_PAIR_COMPARISONS", 23)

    def forbidden(*_args):
        raise AssertionError("line geometry must not run after budget failure")

    monkeypatch.setattr(policy, "_line_geometry", forbidden)
    result = policy.build_omission_diagnostics(report, proposals, **BINDINGS)
    assert result["pages"][0]["layout_state"] == "work_limit"
    assert result["summary"]["unevaluated_regions"] == 4


@pytest.mark.parametrize("name,value", [("MAX_PAGES", 0), ("MAX_REGIONS", 3)])
def test_admission_bounds_are_enforced(monkeypatch, name, value):
    report, proposals = context()
    monkeypatch.setattr(policy, name, value)
    with pytest.raises(ValueError):
        policy.build_omission_diagnostics(report, proposals, **BINDINGS)


@pytest.mark.parametrize("mutation", [
    lambda d: d.update(schema_version=True),
    lambda d: d.update(source_sha256="f" * 64),
    lambda d: d.update(proposals_sha256="f" * 64),
    lambda d: d.update(accuracy_verified=True),
    lambda d: d["summary"].update(evaluated_pages=True),
    lambda d: d["summary"].update(attention_regions=999),
    lambda d: d["pages"][0].update(layout_state="complete"),
    lambda d: d["pages"][0]["regions"][0].update(nonempty_contained_lines=True),
    lambda d: d["pages"][0]["regions"][0].update(ref="#/texts/99"),
    lambda d: d["pages"][0]["regions"][0]["bbox"].__setitem__(0, 0),
    lambda d: d.update(extra=PRIVATE),
    lambda d: d.update(extra=d),
])
def test_full_rebuild_validator_rejects_forged_types_fields_geometry_and_cycles(mutation):
    report, proposals = context()
    result = policy.build_omission_diagnostics(report, proposals, **BINDINGS)
    mutation(result)
    with pytest.raises(ValueError) as caught:
        policy.validate_omission_diagnostics(result, report, proposals, **BINDINGS)
    assert PRIVATE not in str(caught.value)


@pytest.mark.parametrize("binding", ["recovery_sha256", "proposals_sha256"])
@pytest.mark.parametrize("bad", [None, True, "", "F" * 64, "f" * 63])
def test_digest_contract_is_required(binding, bad):
    with pytest.raises(ValueError):
        policy.build_omission_diagnostics(*context(), **{**BINDINGS, binding: bad})


def test_partial_confirmed_assessment_preserves_warnings_unresolved_counts_and_content_digest():
    report, proposals = context(lines(candidate(), [0, 1, 3]))
    decision = {"page_number": 1, "region_ref": "#/texts/2", "decision": "false_alarm"}
    result = policy.build_omission_review(report, proposals, **BINDINGS, decisions=[decision], confirmed=True)
    assert result["summary"]["recorded_decisions"] == 1
    assert result["summary"]["unresolved_regions"] == 3
    assert result["summary"]["unresolved_attention_regions"] == 1
    assert result["diagnostics"]["summary"]["attention_regions"] == 2
    assert region(result["diagnostics"], "#/texts/2")["status"] == "no_candidate_line_overlap"
    raw = json.dumps(result["diagnostics"], sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                     allow_nan=False).encode("utf-8")
    assert result["diagnostics_content_sha256"] == hashlib.sha256(
        b"rag-pipeline:ocr-omission-diagnostics:v1\0" + raw).hexdigest()
    assert result["diagnostics_digest_policy"]["is_file_digest"] is False
    assert result["requires_attention"] is True
    assert result["operator_review"] == "declared_by_local_operator_not_authenticated"
    assert PRIVATE not in json.dumps(result)
    assert policy.validate_omission_review(result, report, proposals, **BINDINGS) == result
    decision["decision"] = "suspected_missing_text"
    assert result["decisions"][0]["decision"] == "false_alarm"


def test_empty_confirmed_decisions_are_an_explicit_partial_assessment_not_full_review():
    result = policy.build_omission_review(*context(), **BINDINGS, decisions=[], confirmed=True)
    assert result["summary"]["unresolved_regions"] == 4
    assert result["summary"]["recorded_decisions"] == 0
    assert result["requires_attention"] is True


@pytest.mark.parametrize("confirmed", [False, None, 1, "true", [], {}])
def test_confirmation_is_exact_true(confirmed):
    with pytest.raises(ValueError):
        policy.build_omission_review(*context(), **BINDINGS, decisions=[], confirmed=confirmed)


@pytest.mark.parametrize("decision", [
    {}, {"page_number": True, "region_ref": "#/texts/0", "decision": "not_text"},
    {"page_number": 2, "region_ref": "#/texts/0", "decision": "not_text"},
    {"page_number": 1, "region_ref": "#/texts/999", "decision": "not_text"},
    {"page_number": 1, "region_ref": "#/texts/0", "decision": PRIVATE},
    {"page_number": 1, "region_ref": ["#/texts/0"], "decision": "not_text"},
    {"page_number": 1, "region_ref": "#/texts/0", "decision": "not_text", "text": PRIVATE},
])
def test_unknown_unbounded_or_forged_decisions_fail_without_private_error_details(decision):
    with pytest.raises(ValueError) as caught:
        policy.validate_omission_decisions([decision], inspect())
    assert PRIVATE not in str(caught.value)


def test_decisions_are_unique_sorted_and_bounded(monkeypatch):
    diagnostic = inspect()
    first = {"page_number": 1, "region_ref": "#/texts/0", "decision": "suspected_missing_text"}
    second = {"page_number": 1, "region_ref": "#/tables/0", "decision": "not_text"}
    assert policy.validate_omission_decisions([first, second], diagnostic) == [second, first]
    with pytest.raises(ValueError):
        policy.validate_omission_decisions([first, first], diagnostic)
    monkeypatch.setattr(policy, "MAX_DECISIONS", 3)
    with pytest.raises(ValueError):
        policy.validate_omission_decisions([], diagnostic)


@pytest.mark.parametrize("mutation", [
    lambda d: d.update(schema_version=True), lambda d: d.update(page_count=True),
    lambda d: d["pages"][0].update(page_number=True),
    lambda d: d["pages"].append(d["pages"][0]),
    lambda d: d["pages"][0]["regions"].append(d["pages"][0]["regions"][0]),
    lambda d: d["pages"][0]["regions"][0].update(ref="arbitrary/path"),
])
def test_decision_helper_checks_bounded_region_identity_but_is_not_an_authenticity_validator(mutation):
    diagnostic = inspect()
    mutation(diagnostic)
    with pytest.raises(ValueError):
        policy.validate_omission_decisions([], diagnostic)


@pytest.mark.parametrize("mutation", [
    lambda d: d.update(confirmed=1),
    lambda d: d.update(diagnostics_content_sha256="f" * 64),
    lambda d: d["diagnostics_digest_policy"].update(is_file_digest=True),
    lambda d: d["summary"].update(recorded_decisions=False),
    lambda d: d["diagnostics"]["summary"].update(attention_regions=100),
    lambda d: d.update(accuracy_verified=True),
    lambda d: d.update(extra=d),
])
def test_review_validator_rebuilds_embedded_diagnostics_and_all_claims(mutation):
    report, proposals = context()
    result = policy.build_omission_review(report, proposals, **BINDINGS, decisions=[], confirmed=True)
    mutation(result)
    with pytest.raises(ValueError):
        policy.validate_omission_review(result, report, proposals, **BINDINGS)
