"""Pure generated v2 reference/comparison controls; no pack or UI acceptance.

Reports use real scalar-only builders/validators. Journal helpers use the real
raster-view builder with independent synthetic 72-point square declarations,
not native rendering, OCR, models or proof that anyone inspected an image.
Scorer spies delegate unchanged; refusal sentinels never stand in for validators.
"""

import copy
from types import SimpleNamespace

import pytest

import ocr_comparison
import ocr_crop_comparison as crops
import ocr_crop_uncertainty_comparison as v2
import ocr_crop_uncertainty_journal as history
import ocr_evaluation
from test_ocr_crop_comparison import _recovery, _report
import test_ocr_crop_uncertainty_journal as jf


REASONS = ("whole_page_unsupported", "region_missing", "candidate_unavailable",
           "reference_required", "reference_uncertain", "metric_limit")
UNKNOWN = ("unknown_character_count", "unknown_word_count", "character_coverage", "word_coverage")


def _case(report=None):
    report = _report(bbox=[0., 0., 1., 1.]) if report is None else report
    value = SimpleNamespace(report=report, report_sha=jf._sha(report),
                             scope=crops.crop_scope(report, region_id="a"))
    value.view = jf._view(value)
    return value


@pytest.fixture
def case():
    return _case()


def _journal(case, state, text="not liable", tokens=None):
    value = jf._new(case, text, [] if tokens is None else tokens)
    if state == "zero":
        return value
    value = jf._append(case, value, jf._add(case))
    if state == "resolved":
        resolution = {"decision": "reading_confirmed", "reading": text} if text else {
            "decision": "not_text", "reading": ""}
        value = jf._transition(case, value, "resolve", status="resolved", resolution=resolution)
    elif state == "dismissed":
        value = jf._transition(case, value, "dismiss", status="dismissed")
    else:
        assert state == "unresolved"
    return value


def _build(case, value, confirmed=True):
    return v2.build_crop_reference_v2(case.report, anchor_report_sha256=case.report_sha,
                                       journal=value, confirmed=confirmed)


def _validate(case, value):
    return v2.validate_crop_reference_v2(value, anchor_report=case.report,
                                         anchor_report_sha256=case.report_sha)


def _compare(case, after, reference, *, before_id="a", after_id="a"):
    return v2.compare_crop_candidates_v2(case.report, after, reference,
        baseline_report_sha256=case.report_sha, retry_report_sha256=jf._sha(after),
        baseline_region_id=before_id, retry_region_id=after_id)


def _legacy(case, after, reference):
    return crops.compare_crop_candidates(case.report, after, reference,
        baseline_report_sha256=case.report_sha, retry_report_sha256=jf._sha(after),
        baseline_region_id="a", retry_region_id="a")


def _v1_reference(case, text, tokens):
    return crops.build_crop_reference(case.report, anchor_report_sha256=case.report_sha,
        anchor_region_id="a", reference=text, critical_tokens=tokens, confirmed=True)


def _ban_scoring(monkeypatch):
    calls = []

    def forbidden(*_args, **_kwargs):
        calls.append("forbidden")
        raise AssertionError("unscorable path reached OCR metrics")

    monkeypatch.setattr(crops, "compare_ocr", forbidden)
    monkeypatch.setattr(ocr_comparison, "compare_ocr", forbidden)
    monkeypatch.setattr(ocr_evaluation, "evaluate_ocr", forbidden)
    return calls


def _coverage(result, *, state, reason, scored, annotations=0):
    counts = result["coverage"]
    assert result["schema_version"] == 2 and type(result["schema_version"]) is int
    assert result["reference_state"] == state
    assert result["reason"] == reason
    assert result["status"] == ("compared" if scored else "unavailable")
    assert counts["requested_pairs"] == 1
    assert counts["scored_pairs"] == scored
    assert counts["unscorable_pairs"] == 1 - scored
    assert counts["reference_occurrences"] == int(state != "none")
    assert counts["reference_unresolved_pairs"] == int(state == "unresolved")
    assert counts["unscorable_reasons"] == {key: int(key == reason) for key in REASONS}
    assert sum(counts["unscorable_reasons"].values()) == counts["unscorable_pairs"]
    assert counts["scored_pairs"] + counts["unscorable_pairs"] == 1
    assert all(type(number) is int for number in counts["unscorable_reasons"].values())
    assert all(type(counts[key]) is int for key in ("requested_pairs", "paired_candidates",
        "reference_occurrences", "scored_pairs", "unscorable_pairs", "reference_unresolved_pairs"))
    assert type(counts["scope_matched"]) is bool
    assert result["uncertainty"]["annotation_total"] == annotations
    assert all(result["uncertainty"][key] is None for key in UNKNOWN)
    assert (result["comparison"] is not None) == bool(scored)
    assert result["canonical_extraction_modified"] is False
    assert result["acceptance"] == "manual_review_required"


@pytest.mark.parametrize("state", ["none", "unresolved", "resolved", "dismissed", "zero"])
@pytest.mark.parametrize("candidate", ["available", "empty", "failed", "missing", "pages"])
def test_reference_candidate_dispatch_matrix(case, state, candidate, monkeypatch):
    reference = None if state == "none" else _build(case, _journal(case, state))
    if candidate == "pages":
        after = _recovery()
    else:
        options = {"bbox": [0., 0., 1., 1.]}
        if candidate == "failed":
            options["state"] = "failed"
        if candidate == "missing":
            options["region_id"] = "b"
        after = _report("" if candidate == "empty" else "not liable", **options)
    score_calls, legacy_calls = [], []
    legacy, scorer = crops.compare_crop_candidates, crops.compare_ocr
    scored = int(candidate in ("available", "empty") and state not in ("none", "unresolved"))

    def legacy_spy(*args, **kwargs):
        legacy_calls.append(copy.deepcopy(args[2]))
        return legacy(*args, **kwargs)

    def scorer_spy(*args, **kwargs):
        score_calls.append(copy.deepcopy(args))
        return scorer(*args, **kwargs)

    if scored:
        monkeypatch.setattr(crops, "compare_ocr", scorer_spy)
    else:
        score_calls = _ban_scoring(monkeypatch)
    monkeypatch.setattr(crops, "compare_crop_candidates", legacy_spy)
    result = _compare(case, after, reference)
    reason = {"pages": "whole_page_unsupported", "missing": "region_missing",
              "failed": "candidate_unavailable"}.get(candidate)
    if reason is None:
        reason = "reference_required" if state == "none" else "reference_uncertain" if state == "unresolved" else None
    expected_state = state if state in ("none", "unresolved") else "resolved"
    _coverage(result, state=expected_state, reason=reason, scored=scored,
              annotations=int(state not in ("none", "zero")))
    assert len(legacy_calls) == 1 and len(score_calls) == scored
    if state in ("none", "unresolved"):
        assert legacy_calls == [None]
    else:
        assert legacy_calls[0]["schema_version"] == 1
        assert "annotations" not in legacy_calls[0]
    assert result["coverage"]["paired_candidates"] == int(candidate in ("available", "empty"))
    assert result["coverage"]["scope_matched"] == (candidate not in ("missing", "pages"))
    if reference is not None:
        assert result["reference_sha256"] == reference["reference_sha256"]
        assert result["reference_id"] == reference["reference_id"]
    else:
        assert result["reference_sha256"] is result["reference_id"] is None
    if candidate == "empty":
        assert result["pair"]["retry"]["status"] == "empty_candidate"
        assert result["pair"]["retry"]["candidate_sha256"] is not None
    if candidate == "failed":
        assert result["pair"]["retry"]["candidate_sha256"] is None


@pytest.mark.parametrize("candidate", ["available", "failed", "missing", "pages"])
def test_unresolved_absent_critical_build_validate_compare_never_score(case, candidate, monkeypatch):
    forbidden = _ban_scoring(monkeypatch)
    value = _journal(case, "unresolved", "", ["OCR"])
    reference = _build(case, value)
    assert _validate(case, reference) == reference
    after = (_recovery() if candidate == "pages" else _report(
        bbox=[0., 0., 1., 1.], state="failed" if candidate == "failed" else "available",
        region_id="b" if candidate == "missing" else "a"))
    result = _compare(case, after, reference)
    assert result["comparison"] is None and forbidden == []
    assert result["reference_state"] == "unresolved"
    assert reference["reference"] == "" and reference["critical_tokens"] == ["OCR"]


@pytest.mark.parametrize("state", ["zero", "resolved", "dismissed"])
def test_final_no_unresolved_state_requires_critical_occurrence(case, state, monkeypatch):
    _ban_scoring(monkeypatch)
    value = _journal(case, state, "not liable", ["OCR"])
    assert history.replay_crop_journal(value, anchor_report=case.report,
        anchor_report_sha256=case.report_sha)["declaration"]["critical_tokens"] == ["OCR"]
    with pytest.raises(ValueError):
        _build(case, value)


def test_mixed_annotation_counts_are_independent_of_primary_candidate_failure(case, monkeypatch):
    _ban_scoring(monkeypatch)
    value = jf._new(case, "not liable")
    additions = [jf._add(case, number, (32 * (number - 1), 0, 32 * (number - 1) + 20, 20),
                        kind="illegible" if number == 2 else "uncertain") for number in range(1, 5)]
    value = jf._append(case, value, *additions)
    value = jf._transition(case, value, "resolve", number=3, status="resolved",
        resolution={"decision": "reading_confirmed", "reading": "not liable"})
    value = jf._transition(case, value, "dismiss", number=4, status="dismissed")
    result = _compare(case, _report(state="failed", bbox=[0., 0., 1., 1.]), _build(case, value))
    _coverage(result, state="unresolved", reason="candidate_unavailable", scored=0, annotations=4)
    assert result["uncertainty"] == {"annotation_total": 4, "unresolved_uncertain": 1,
        "unresolved_illegible": 1, "resolved": 1, "dismissed": 1, **dict.fromkeys(UNKNOWN)}


PARITY = [
    ("liable", "not liable", "not liable", ["not liable"]),
    ("", "extra", "", []),
    ("not liable", "not liable extra", "not liable", []),
    ("not liable not liable x", "not liable liable", "not liable not liable", ["not liable", "liable"]),
    ("café", "cafe\u0301", "  cafe\u0301 \n", ["café"]),
]


@pytest.mark.parametrize("before_text,after_text,text,tokens", PARITY,
                         ids=["improved", "empty-reference", "edge-insertion", "critical-repeat", "nfc"])
@pytest.mark.parametrize("state", ["zero", "resolved", "dismissed"])
@pytest.mark.parametrize("route", ["regions", "hardscan"])
def test_complete_embedded_v1_metric_bytes_are_unchanged(before_text, after_text, text, tokens, state, route):
    case = _case(_report(before_text, bbox=[0., 0., 1., 1.]))
    after = _report(after_text, dpi=400, bbox=[0., 0., 1., 1.], operation=route)
    v1_reference = _v1_reference(case, text, tokens)
    expected = _legacy(case, after, v1_reference)
    reference = _build(case, _journal(case, state, text, tokens))
    result = _compare(case, after, reference)
    assert jf._raw(result["comparison"]) == jf._raw(expected["comparison"])
    assert result["reference_id"] == expected["reference_id"]
    assert result["reference_sha256"] != expected["reference_sha256"]
    outer_changes = {"schema_version", "reference_sha256", "coverage", "reference_state",
                     "uncertainty", "scope_notice"}
    assert {key: value for key, value in result.items() if key not in outer_changes} == {
        key: value for key, value in expected.items() if key not in outer_changes}
    assert set(result) == set(expected) | {"reference_state", "uncertainty"}
    _coverage(result, state="resolved", reason=None, scored=1, annotations=int(state != "zero"))
    if text == "":
        record = result["comparison"]["records"][0]
        assert record["baseline"]["character"]["error_rate"] is None
        assert record["retry"]["character"]["error_rate"] is None
        assert record["delta"]["character"]["insertions"] == 5
    if after_text == "not liable extra":
        assert result["comparison"]["records"][0]["retry"]["word"]["insertions"] == 1


@pytest.mark.parametrize("retry_kind", ["available", "failed", "missing", "pages"])
def test_legacy_none_call_bytes_remain_identical_after_v2_operations(case, retry_kind, monkeypatch):
    _ban_scoring(monkeypatch)
    after = _recovery() if retry_kind == "pages" else _report(bbox=[0., 0., 1., 1.],
        state="failed" if retry_kind == "failed" else "available", region_id="b" if retry_kind == "missing" else "a")
    expected = jf._raw(_legacy(case, after, None))
    _compare(case, after, None)
    _compare(case, after, _build(case, _journal(case, "unresolved")))
    assert jf._raw(_legacy(case, after, None)) == expected


@pytest.mark.parametrize("state", ["none", "unresolved", "resolved"])
def test_real_candidate_metric_limit_remains_candidate_available(case, state, monkeypatch):
    after = _report("x" * 20_001, bbox=[0., 0., 1., 1.])
    reference = None if state == "none" else _build(case, _journal(case, state))
    calls, original = [], crops.compare_ocr

    def spy(*args, **kwargs):
        calls.append(copy.deepcopy(args))
        return original(*args, **kwargs)

    if state == "resolved":
        monkeypatch.setattr(crops, "compare_ocr", spy)
    else:
        calls = _ban_scoring(monkeypatch)
    result = _compare(case, after, reference)
    reason = {"none": "reference_required", "unresolved": "reference_uncertain", "resolved": "metric_limit"}[state]
    _coverage(result, state=state, reason=reason, scored=0, annotations=int(state != "none"))
    assert len(calls) == int(state == "resolved")
    if calls:
        assert calls[0][1]["records"][0]["prediction"] == "x" * 20_001
    assert result["coverage"]["paired_candidates"] == 1
    assert all(side["status"] == "candidate_available" for side in result["pair"].values())


@pytest.mark.parametrize("state", ["unresolved", "resolved"])
@pytest.mark.parametrize("after_kind", ["available", "missing", "pages"])
def test_wrong_selected_baseline_never_becomes_unavailable_coverage(case, state, after_kind, monkeypatch):
    reference = _build(case, _journal(case, state))
    _ban_scoring(monkeypatch)
    after = _recovery() if after_kind == "pages" else _report(bbox=[0., 0., 1., 1.],
        region_id="b" if after_kind == "missing" else "a")
    with pytest.raises(ValueError):
        _compare(case, after, reference, before_id="missing")


def test_same_scope_different_existing_baseline_occurrence_is_not_interchangeable(monkeypatch):
    rows = [{"region_id": name, "page_number": 1, "bbox": [0., 0., 1., 1.]} for name in ("a", "b")]
    case = _case(_report(rows=rows))
    reference = _build(case, _journal(case, "unresolved"))
    _ban_scoring(monkeypatch)
    with pytest.raises(ValueError):
        _compare(case, _report(bbox=[0., 0., 1., 1.]), reference, before_id="b")


@pytest.mark.parametrize("options", [{"source": "b" * 64}, {"page_number": 2},
    {"bbox": [0., 0., .9, 1.]}, {"rotation": 90}, {"crop_origin": (1., 0.)}],
    ids=["source", "page", "bbox", "rotation", "cropbox"])
@pytest.mark.parametrize("state", ["none", "unresolved", "resolved"])
def test_source_or_physical_scope_mismatch_is_refusal(case, options, state, monkeypatch):
    reference = None if state == "none" else _build(case, _journal(case, state))
    _ban_scoring(monkeypatch)
    after = _report(**({"bbox": [0., 0., 1., 1.]} | options))
    with pytest.raises(ValueError):
        _compare(case, after, reference)


def test_failed_baseline_keeps_uncertainty_and_actual_candidate_state(monkeypatch):
    case = _case(_report(state="failed", bbox=[0., 0., 1., 1.]))
    _ban_scoring(monkeypatch)
    reference = _build(case, _journal(case, "unresolved", "", ["OCR"]))
    result = _compare(case, _report(bbox=[0., 0., 1., 1.]), reference)
    _coverage(result, state="unresolved", reason="candidate_unavailable", scored=0, annotations=1)
    assert result["pair"]["baseline"]["candidate_sha256"] is None


@pytest.mark.parametrize("confirmed", [False, None, 1, "true"], ids=["false", "null", "integer", "string"])
def test_confirmation_exact_true_is_required_before_journal_replay(case, confirmed, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("unconfirmed declaration reached journal replay")

    monkeypatch.setattr(history, "replay_crop_journal", forbidden)
    with pytest.raises(ValueError):
        _build(case, None, confirmed)


@pytest.mark.parametrize("attack", ["extra", "missing", "nonstring-key", "version-bool", "version-one",
    "version-string", "kind", "review", "oversize-scalar", "oversize-total", "depth", "nonfinite", "surrogate"])
def test_outer_preflight_refuses_before_replay(case, attack, monkeypatch):
    reference = _build(case, _journal(case, "unresolved"))
    if attack == "extra":
        reference["extra"] = None
    elif attack == "missing":
        del reference["reference"]
    elif attack == "nonstring-key":
        reference[1] = reference.pop("reference")
    elif attack.startswith("version-"):
        reference["schema_version"] = {"version-bool": True, "version-one": 1, "version-string": "2"}[attack]
    elif attack == "kind":
        reference["kind"] = "different"
    elif attack == "review":
        reference["review"] = "automatic"
    elif attack == "oversize-scalar":
        reference["reference"] = "x" * 20_001
    elif attack == "oversize-total":
        reference["journal"]["base"]["critical_tokens"] = ["x" * 20_000] * 14
    elif attack == "depth":
        nested = None
        for _ in range(33):
            nested = [nested]
        reference["journal"]["base"]["annotations"] = nested
    elif attack == "nonfinite":
        reference["reference"] = float("nan")
    else:
        reference["reference"] = "\ud800"

    preflight_calls, preflight = [], v2._bytes

    def byte_spy(*args, **kwargs):
        preflight_calls.append(1)
        return preflight(*args, **kwargs)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("invalid outer envelope reached journal replay")

    monkeypatch.setattr(v2, "_bytes", byte_spy)
    monkeypatch.setattr(history, "replay_crop_journal", forbidden)
    with pytest.raises(ValueError):
        _validate(case, reference)
    if attack in ("oversize-total", "depth"):
        assert preflight_calls == [1]  # Not merely a wrong-root-field refusal.


PREFLIGHT_FIELDS = [
    pytest.param(("reference_sha256",), "x", id="reference-hash-short"),
    pytest.param(("reference_sha256",), "F" * 64, id="reference-hash-uppercase"),
    pytest.param(("declaration_sha256",), False, id="declaration-hash-bool"),
    pytest.param(("reference_id",), 1, id="occurrence-id-int"),
    pytest.param(("reference",), 1, id="reference-int"),
    pytest.param(("critical_tokens",), "foo", id="critical-container-string"),
    pytest.param(("critical_tokens",), (), id="critical-container-tuple"),
    pytest.param(("critical_tokens",), [1], id="critical-entry-int"),
    pytest.param(("critical_tokens",), [""], id="critical-empty"),
    pytest.param(("critical_tokens",), [" \n"], id="critical-blank"),
    pytest.param(("critical_tokens",), ["é", "e\u0301"], id="critical-normalized-duplicate"),
    pytest.param(("critical_tokens",), ["x" * 257], id="critical-entry-limit"),
    pytest.param(("critical_tokens",), [str(number) for number in range(65)], id="critical-count-limit"),
    pytest.param(("annotations",), {}, id="annotation-container-dict"),
    pytest.param(("annotations",), (), id="annotation-container-tuple"),
    pytest.param(("annotations",), [None], id="annotation-null-row"),
    pytest.param(("annotations", 0, "annotation_id"), True, id="annotation-id-bool"),
    pytest.param(("annotations", 0, "kind"), 1, id="annotation-kind-int"),
    pytest.param(("annotations", 0, "status"), None, id="annotation-status-null"),
    pytest.param(("annotations", 0, "tentative_text"), False, id="tentative-bool"),
    pytest.param(("annotations", 0, "bbox"), None, id="annotation-bbox-null"),
    pytest.param(("annotations", 0, "bbox"), [False, 0., .25, .25], id="annotation-bbox-bool"),
    pytest.param(("annotations", 0, "raw_span"), [], id="raw-span-list"),
    pytest.param(("annotations", 0, "raw_span"),
                 {"reference_text_sha256": "a" * 64, "start": True, "end": 1}, id="raw-span-offset-bool"),
    pytest.param(("annotations", 0, "resolution"), [], id="resolution-list"),
    pytest.param(("annotations", 0, "resolution"),
                 {"decision": [], "reading": "foo"}, id="resolution-decision-list"),
    pytest.param(("anchor",), None, id="anchor-null"),
    pytest.param(("anchor",), [], id="anchor-list"),
    pytest.param(("anchor", "report_sha256"), "bad", id="anchor-report-hash"),
    pytest.param(("anchor", "record_sha256"), True, id="anchor-record-hash-bool"),
    pytest.param(("anchor", "recipe_sha256"), None, id="anchor-recipe-hash-null"),
    pytest.param(("anchor", "region_id"), 1, id="anchor-region-id-int"),
    pytest.param(("anchor", "operation"), [], id="anchor-operation-list"),
    pytest.param(("scope",), None, id="scope-null"),
    pytest.param(("scope",), [], id="scope-list"),
    pytest.param(("scope", "scope_sha256"), "bad", id="scope-hash"),
    pytest.param(("scope", "source_sha256"), False, id="scope-source-hash-bool"),
    pytest.param(("scope", "page_count"), True, id="scope-page-count-bool"),
    pytest.param(("scope", "bbox"), [False, 0., 1., 1.], id="scope-bbox-bool"),
    pytest.param(("journal",), None, id="journal-null"),
    pytest.param(("journal",), [], id="journal-list"),
    pytest.param(("journal",), {}, id="journal-fields-missing"),
]


@pytest.mark.parametrize("path,bad", PREFLIGHT_FIELDS)
def test_outer_scalar_container_and_hash_preflight_precedes_full_replay(case, path, bad, monkeypatch):
    reference = _build(case, _journal(case, "unresolved"))
    target = reference
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = copy.deepcopy(bad)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("malformed outer scalar/container/hash reached full journal replay")

    monkeypatch.setattr(history, "replay_crop_journal", forbidden)
    with pytest.raises(ValueError):
        _validate(case, reference)


def test_outer_annotation_count_preflight_precedes_full_replay(case, monkeypatch):
    reference = _build(case, _journal(case, "unresolved"))
    reference["annotations"] *= 129

    def forbidden(*_args, **_kwargs):
        raise AssertionError("oversized outer annotation array reached full journal replay")

    monkeypatch.setattr(history, "replay_crop_journal", forbidden)
    with pytest.raises(ValueError):
        _validate(case, reference)


@pytest.mark.parametrize("field", ["reference", "critical_tokens", "annotations", "declaration_sha256",
    "reference_sha256", "reference_id", "scope", "anchor", "journal"])
def test_outer_declaration_and_hash_joins_are_rebuilt_from_real_history(case, field):
    reference = _build(case, _journal(case, "unresolved"))
    if field in ("declaration_sha256", "reference_sha256"):
        reference[field] = "0" * 64
    elif field == "reference":
        reference[field] = "altered"
    elif field == "critical_tokens":
        reference[field] = ["missing"]
    elif field == "annotations":
        reference[field][0]["tentative_text"] = "forged"
    elif field == "reference_id":
        reference[field] = "crop-" + "0" * 64
    elif field == "scope":
        reference[field]["bbox"][2] = .5
    elif field == "anchor":
        reference[field]["region_id"] = "b"
    else:
        reference[field]["head_sha256"] = "0" * 64
    with pytest.raises(ValueError):
        _validate(case, reference)


def test_rehashing_forged_top_level_text_does_not_replace_journal_head(case):
    reference = _build(case, _journal(case, "unresolved"))
    reference["reference"] = "forged but rehashed"
    projection_keys = ("schema_version", "kind", "reference_id", "scope", "anchor", "reference",
                       "critical_tokens", "annotations")
    reference["declaration_sha256"] = jf._sha({key: reference[key] for key in projection_keys})
    reference["reference_sha256"] = jf._sha({key: value for key, value in reference.items()
                                               if key != "reference_sha256"})
    with pytest.raises(ValueError):
        _validate(case, reference)


def test_null_journal_is_not_a_reviewed_reference(case):
    reference = _build(case, _journal(case, "zero"))
    reference["journal"] = None
    with pytest.raises(ValueError):
        _validate(case, reference)
    with pytest.raises(ValueError):
        _build(case, None)


def test_versions_are_explicit_and_cannot_be_downgraded_by_dropping_fields(case):
    old = _v1_reference(case, "not liable", [])
    new = _build(case, _journal(case, "unresolved"))
    after = _report(bbox=[0., 0., 1., 1.])
    with pytest.raises(ValueError):
        _validate(case, old)
    with pytest.raises(ValueError):
        _compare(case, after, old)
    with pytest.raises(ValueError):
        crops.validate_crop_reference(new, anchor_report=case.report, anchor_report_sha256=case.report_sha)
    with pytest.raises(ValueError):
        _legacy(case, after, new)
    stripped = {key: new[key] for key in old}
    stripped["schema_version"] = 1
    with pytest.raises(ValueError):
        crops.validate_crop_reference(stripped, anchor_report=case.report, anchor_report_sha256=case.report_sha)


@pytest.mark.parametrize("operation", ["build", "validate", "compare"])
@pytest.mark.parametrize("state", ["unresolved", "resolved"])
def test_one_complete_journal_replay_per_public_call(case, operation, state, monkeypatch):
    value = _journal(case, state)
    reference = _build(case, value)
    calls, original = [], history.replay_crop_journal

    def spy(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(history, "replay_crop_journal", spy)
    if operation == "build":
        _build(case, value)
    elif operation == "validate":
        _validate(case, reference)
    else:
        _compare(case, _report(bbox=[0., 0., 1., 1.]), reference)
    assert calls == [1]


def test_final_reference_reuses_exact_shared_reading_scan_budget(case, monkeypatch):
    value = _journal(case, "resolved", "foo")
    monkeypatch.setattr(history, "MAX_READING_SCAN", 6)
    reference = _build(case, value)
    assert _validate(case, reference) == reference
    monkeypatch.setattr(history, "MAX_READING_SCAN", 5)
    with pytest.raises(ValueError):
        _validate(case, reference)


def test_observed_aba_keeps_declaration_but_changes_reference_envelope(case):
    value = _journal(case, "zero")
    first = _build(case, value)
    second = _build(case, jf._append(case, value, reset=True))
    assert first["reference_id"] == second["reference_id"]
    assert first["declaration_sha256"] == second["declaration_sha256"]
    assert first["reference_sha256"] != second["reference_sha256"]
    assert first["journal"]["head_sha256"] != second["journal"]["head_sha256"]


def test_builder_validator_and_comparator_leave_inputs_and_other_results_detached(case):
    value = _journal(case, "resolved")
    original_journal = copy.deepcopy(value)
    reference = _build(case, value)
    validated = _validate(case, reference)
    after = _report(dpi=400, bbox=[0., 0., 1., 1.])
    snapshots = copy.deepcopy((case.report, after, reference, value))
    result = _compare(case, after, reference)
    validated["annotations"][0]["tentative_text"] = "detached"
    validated["journal"]["views"][0]["rgb_sha256"] = "f" * 64
    result["scope"]["bbox"].clear()
    result["coverage"]["unscorable_reasons"].clear()
    assert (case.report, after, reference, value) == snapshots
    assert value == original_journal


def test_augmented_result_size_is_checked_after_adding_v2_fields(case, monkeypatch):
    reference = _build(case, _journal(case, "unresolved"))
    after = _report(bbox=[0., 0., 1., 1.])
    result = _compare(case, after, reference)
    exact = len(jf._raw(result))
    monkeypatch.setattr(v2, "MAX_RESULT_BYTES", exact)
    assert _compare(case, after, reference) == result
    monkeypatch.setattr(v2, "MAX_RESULT_BYTES", exact - 1)
    with pytest.raises(ValueError):
        _compare(case, after, reference)
