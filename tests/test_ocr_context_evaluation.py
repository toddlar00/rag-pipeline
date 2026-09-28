"""Synthetic occurrence ownership, strict inputs, coverage, and privacy."""

import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest

import ocr_context_evaluation as core


SOURCE, RECOVERY, REFERENCE = "a" * 64, "b" * 64, "c" * 64


def context(text="Alice owes 10 dollars.", *, identifier="ctx1", page=1, target="10", category="number"):
    start = text.index(target)
    return {"context_id": identifier, "page_number": page,
            "source_anchor": {"kind": "sentence", "bbox": [0.1, 0.2, 0.9, 0.3], "cell": None},
            "reference": text, "checks": [{"check_id": "check1", "category": category,
                                            "reference_span": [start, start + len(target)],
                                            "left_anchor": text[:start], "right_anchor": text[start + len(target):]}]}


def reference(*contexts, page_count=1):
    return {"schema_version": 1, "kind": "ocr_context_reference", "source_sha256": SOURCE,
            "page_count": page_count, "approval": "human_reviewed",
            "coordinate_system": "original_page_display_fraction", "contexts": list(contexts) or [context()]}


def correspondence(ref, predictions=None):
    predictions = {} if predictions is None else predictions
    return {"schema_version": 1, "kind": "ocr_context_correspondence", "source_sha256": SOURCE,
            "recovery_sha256": RECOVERY, "reference_sha256": REFERENCE, "approval": "human_reviewed",
            "offset_unit": "raw_unicode_code_points", "contexts": [
                {"context_id": c["context_id"], "status": "mapped", "candidate_span":
                 [0, len(predictions.get(c["page_number"], c["reference"]))]} for c in ref["contexts"]]}


def evaluate(ref=None, mapping=None, predictions=None, pages=None):
    ref = reference() if ref is None else ref
    predictions = {c["page_number"]: c["reference"] for c in ref["contexts"]} if predictions is None else predictions
    mapping = correspondence(ref, predictions) if mapping is None else mapping
    pages = [{"page_number": n, "status": "available", "text": t} for n, t in predictions.items()] if pages is None else pages
    return core.evaluate_context_checks(ref, mapping, source_sha256=SOURCE, recovery_sha256=RECOVERY,
                                        reference_sha256=REFERENCE, page_count=ref["page_count"], candidate_pages=pages)


def test_exact_localized_occurrence_without_output_text_or_claimed_page_accuracy():
    ref = reference()
    before = copy.deepcopy(ref)
    result = evaluate(ref)
    check = result["contexts"][0]["checks"][0]
    assert check == {"check_index": 1, "category": "number", "reference_span": [11, 13],
                     "candidate_span": [11, 13], "status": "passed", "reason": "exact_occurrence_match"}
    assert result["coverage"]["checks"] == {"total": 1, "evaluated": 1, "passed": 1, "failed": 0, "abstained": 0}
    assert result["requires_attention"] is False
    assert not result["policy"]["semantic_entailment_verified"]
    assert not result["coverage"]["pages"]["full_page_verification_claimed"]
    assert "Alice" not in json.dumps(result) and "ctx1" not in json.dumps(result)
    result["contexts"][0]["source_anchor"]["bbox"][0] = .4
    assert ref == before


@pytest.mark.parametrize("category,text,target,changed", [
    ("number", "Alice owes 10 dollars.", "10", "20"),
    ("unit", "Length is 10 mm.", "mm", "cm"),
    ("negation", "Alice is not liable.", "not ", ""),
    ("name", "Alice owes 10 dollars.", "Alice", "Bob"),
    ("footnote_identifier", "See note [12].", "12", "21"),
])
def test_each_critical_category_detects_changed_occurrence(category, text, target, changed):
    ref = reference(context(text, target=target, category=category))
    result = evaluate(ref, predictions={1: text.replace(target, changed)})
    assert result["coverage"]["checks"]["failed"] == 1
    assert result["category_totals"][category]["failed"] == 1
    assert result["requires_attention"]


def test_swapped_values_in_one_sentence_have_same_bag_counts_but_two_failures():
    item = context("A=10; B=20.")
    item["checks"] = [
        {"check_id": "a", "category": "number", "reference_span": [2, 4], "left_anchor": "A=", "right_anchor": "; B="},
        {"check_id": "b", "category": "number", "reference_span": [8, 10], "left_anchor": "B=", "right_anchor": "."},
    ]
    prediction = "A=20; B=10."
    assert sorted(item["reference"]) == sorted(prediction)
    result = evaluate(reference(item), predictions={1: prediction})
    assert result["coverage"]["checks"]["failed"] == 2


def test_moved_negation_never_passes_on_unchanged_global_token_count():
    first = context("Alice is liable.", target="liable", category="negation")
    second = context("Bob is not liable.", identifier="ctx2", target="not ", category="negation")
    ref = reference(first, second)
    prediction = "Alice is not liable. Bob is liable."
    mapping = correspondence(ref)
    mapping["contexts"][0]["candidate_span"] = [0, 20]
    mapping["contexts"][1]["candidate_span"] = [21, len(prediction)]
    result = evaluate(ref, mapping, {1: prediction})
    assert result["coverage"]["checks"]["passed"] == 0
    assert result["coverage"]["checks"]["failed"] == 2


def test_repeated_equal_numbers_are_occurrence_bound_not_reused():
    first = context("A owes 10.", identifier="a")
    second = context("B owes 10.", identifier="b")
    ref = reference(first, second)
    prediction = "A owes 10. B owes 20."
    mapping = correspondence(ref)
    mapping["contexts"][0]["candidate_span"] = [0, 10]
    mapping["contexts"][1]["candidate_span"] = [11, 21]
    result = evaluate(ref, mapping, {1: prediction})
    assert result["coverage"]["checks"]["passed"] == 1
    assert result["coverage"]["checks"]["failed"] == 1


def test_swapped_cell_values_fail_reviewed_cell_correspondence():
    first, second = context("10", identifier="r1"), context("20", identifier="r2", target="20")
    for row, c in enumerate((first, second), 1):
        c["source_anchor"] = {"kind": "cell", "bbox": [.1, row*.1, .2, row*.1+.05], "cell": {"row": row, "column": 2}}
    ref = reference(first, second)
    mapping = correspondence(ref)
    mapping["contexts"][0]["candidate_span"] = [0, 2]
    mapping["contexts"][1]["candidate_span"] = [3, 5]
    result = evaluate(ref, mapping, {1: "20 10"})
    assert result["coverage"]["checks"]["failed"] == 2


@pytest.mark.parametrize("prediction,reason", [
    ("Alice owes 10 dollars. Alice owes 20 dollars.", "anchor_ambiguous"),
    ("Alice paid 10 dollars.", "anchor_missing"),
    (" dollars.Alice owes ", "anchor_order_changed"),
])
def test_missing_or_ambiguous_anchors_abstain(prediction, reason):
    result = evaluate(predictions={1: prediction})
    assert result["coverage"]["checks"]["abstained"] == 1
    assert result["contexts"][0]["checks"][0]["reason"] == reason


@pytest.mark.parametrize("status", ["missing", "ambiguous"])
@pytest.mark.parametrize("availability", ["available", "retry_failed", "deferred", "not_selected"])
def test_unresolved_and_unavailable_never_become_blank_passes(status, availability):
    ref = reference()
    mapping = correspondence(ref)
    mapping["contexts"][0].update(status=status, candidate_span=None)
    pages = [] if availability == "not_selected" else [
        {"page_number": 1, "status": availability, "text": "" if availability == "available" else None}]
    result = evaluate(ref, mapping, pages=pages)
    assert result["coverage"]["checks"] == {"total": 1, "evaluated": 0, "passed": 0, "failed": 0, "abstained": 1}
    assert result["requires_attention"]


def test_genuine_mapped_empty_candidate_is_failed_not_unavailable():
    result = evaluate(predictions={1: ""})
    assert result["coverage"]["checks"]["failed"] == 1
    assert result["contexts"][0]["checks"][0]["reason"] == "empty_candidate_context"
    assert result["coverage"]["pages"]["empty_candidate_pages"] == [1]


def test_whitespace_only_candidate_is_empty_but_whitespace_changes_are_not_normalized():
    result = evaluate(predictions={1: " \t\n"})
    assert result["coverage"]["checks"]["failed"] == 1
    assert result["coverage"]["pages"]["empty_candidate_pages"] == [1]
    assert evaluate(predictions={1: "Alice owes  10 dollars."})["coverage"]["checks"]["failed"] == 1


def test_whitespace_only_reference_occurrence_cannot_pass_as_critical_evidence():
    with pytest.raises(ValueError):
        evaluate(reference(context("A  B", target="  ")))


def test_reference_anchor_repeated_even_with_overlap_is_invalid():
    item = context("aaa10.")
    item["checks"][0]["left_anchor"] = "aa"
    with pytest.raises(ValueError):
        evaluate(reference(item))


def test_checks_can_be_in_a_reviewed_region_and_correspondence_order_is_detached():
    ref = reference(context(identifier="b", page=2), context(identifier="a", page=1), page_count=2)
    ref["contexts"][0]["source_anchor"]["kind"] = "region"
    mapping = correspondence(ref)
    mapping["contexts"].reverse()
    saved = copy.deepcopy(mapping)
    result = evaluate(ref, mapping)
    assert [row["page_number"] for row in result["contexts"]] == [2, 1]
    assert result["coverage"]["checks"]["passed"] == 2
    assert mapping == saved


@pytest.mark.parametrize("raw", [None, [], {}, {"page_count": True}])
def test_public_correspondence_validator_rejects_invalid_reference_with_value_error(raw):
    with pytest.raises(ValueError):
        core.validate_correspondence(correspondence(reference()), reference=raw, source_sha256=SOURCE,
                                     recovery_sha256=RECOVERY, reference_sha256=REFERENCE)


def test_coverage_never_calls_one_checked_context_a_verified_page():
    ref = reference(page_count=4)
    result = evaluate(ref, pages=[{"page_number": 1, "status": "available", "text": "Alice owes 10 dollars."},
                                  {"page_number": 2, "status": "available", "text": "other"},
                                  {"page_number": 3, "status": "deferred", "text": None}])
    assert result["coverage"]["pages"] == {
        "source_page_count": 4, "with_reference_contexts": [1], "without_reference_contexts_count": 3,
        "unchecked_selected_pages": [2], "unchecked_deferred_pages": [3],
        "failed_candidate_pages": [], "empty_candidate_pages": [], "full_page_verification_claimed": False}
    assert result["requires_attention"]


def test_unicode_offsets_are_raw_code_points_with_no_normalization():
    ref = reference(context("\U0001f642 Caf\u00e9 owes 10.", target="Caf\u00e9", category="name"))
    result = evaluate(ref, predictions={1: "\U0001f642 Cafe\u0301 owes 10."})
    check = result["contexts"][0]["checks"][0]
    assert check["reference_span"] == [2, 6]
    assert check["candidate_span"] == [2, 7]
    assert check["status"] == "failed"


@pytest.mark.parametrize("path,value", [
    (("schema_version",), True), (("page_count",), True), (("approval",), "approved"),
    (("source_sha256",), "A"*64), (("kind",), "other"), (("contexts",), []),
    (("contexts", 0, "page_number"), True), (("contexts", 0, "context_id"), "private/path"),
    (("contexts", 0, "reference"), "\ud800"), (("contexts", 0, "reference"), "x"*20_001),
    (("contexts", 0, "source_anchor", "bbox", 0), True),
    (("contexts", 0, "source_anchor", "bbox", 0), float("nan")),
    (("contexts", 0, "source_anchor", "bbox", 0), 10**1000),
    (("contexts", 0, "source_anchor", "cell"), {}),
    (("contexts", 0, "checks", 0, "reference_span"), [True, 13]),
    (("contexts", 0, "checks", 0, "reference_span"), [11, 11]),
    (("contexts", 0, "checks", 0, "category"), "semantic"),
    (("contexts", 0, "checks", 0, "left_anchor"), "owes"),
    (("contexts", 0, "checks", 0, "right_anchor"), "x"*257),
])
def test_strict_reference_mutants(path, value):
    ref = reference()
    target = ref
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValueError):
        evaluate(ref)


@pytest.mark.parametrize("path,value", [
    (("schema_version",), True), (("source_sha256",), "d"*64), (("recovery_sha256",), "d"*64),
    (("reference_sha256",), "d"*64), (("approval",), False), (("offset_unit",), "bytes"),
    (("contexts",), []), (("contexts", 0, "context_id"), "unknown"),
    (("contexts", 0, "status"), "pass"), (("contexts", 0, "candidate_span"), [True, 5]),
    (("contexts", 0, "candidate_span"), [0, 100001]), (("contexts", 0, "candidate_span"), [0, 20001]),
    (("contexts", 0, "candidate_span"), [0, 500]),
])
def test_strict_correspondence_mutants(path, value):
    mapping = correspondence(reference())
    target = mapping
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValueError):
        evaluate(mapping=mapping)


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "unknown", "reuse", "overlap"])
def test_complete_mapping_and_no_candidate_context_reuse(mutation):
    ref = reference(context(identifier="a"), context(identifier="b"))
    mapping = correspondence(ref)
    if mutation == "missing":
        mapping["contexts"].pop()
    elif mutation == "duplicate":
        mapping["contexts"][1]["context_id"] = "a"
    elif mutation == "unknown":
        mapping["contexts"][1]["context_id"] = "c"
    elif mutation == "overlap":
        mapping["contexts"][1]["candidate_span"] = [1, 4]
    with pytest.raises(ValueError):
        evaluate(ref, mapping)


@pytest.mark.parametrize("mutation", ["duplicate_context", "duplicate_check", "overlap_check", "unknown_top", "unknown_check"])
def test_reference_duplicate_and_unknown_fields(mutation):
    ref = reference()
    if mutation == "duplicate_context":
        ref["contexts"].append(copy.deepcopy(ref["contexts"][0]))
    elif mutation in ("duplicate_check", "overlap_check"):
        check = copy.deepcopy(ref["contexts"][0]["checks"][0])
        if mutation == "overlap_check":
            check["check_id"] = "other"
        ref["contexts"][0]["checks"].append(check)
    elif mutation == "unknown_top":
        ref["extra"] = True
    else:
        ref["contexts"][0]["checks"][0]["expected"] = "10"
    with pytest.raises(ValueError):
        evaluate(ref)


@pytest.mark.parametrize("budget", ["MAX_CONTEXTS", "MAX_CHECKS", "MAX_REFERENCE_CHARACTERS", "MAX_TOTAL_CANDIDATE_CHARACTERS"])
def test_aggregate_budgets_fail_closed(monkeypatch, budget):
    monkeypatch.setattr(core, budget, 0)
    with pytest.raises(ValueError):
        evaluate()


@pytest.mark.parametrize("pages", [
    [{"page_number": True, "status": "available", "text": "x"}],
    [{"page_number": 1, "status": "retry_failed", "text": ""}],
    [{"page_number": 1, "status": "deferred", "text": "fabricated"}],
    [{"page_number": 1, "status": "available", "text": "x"*100001}],
    [{"page_number": 1, "status": "retry_failed", "text": None}],
    [],
])
def test_invalid_candidate_state_and_mapped_unavailable(pages):
    with pytest.raises(ValueError):
        evaluate(pages=pages)


def test_core_imports_only_standard_library():
    script = "import sys; import ocr_context_evaluation; assert not any(n in sys.modules for n in ('rag','numpy','cv2','pymupdf','rapidocr','ocr_recovery','ocr_evaluation'))"
    result = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1], capture_output=True)
    assert result.returncode == 0, result.stderr.decode()
