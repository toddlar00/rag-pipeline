"""Pure generated-record spot-audit contracts; no source, OCR, model or UI IO."""

import copy
import hashlib
import json
import math

import pytest

import ocr_evaluation
import ocr_spot_audit as audit
from ocr_review import ReviewDocument
from test_ocr_recovery_comparison import HEALTHY, _report


SEED = "0" * 64


def document_for(texts=("Alpha.", "Beta.", "Gamma."), *, scores=None):
    """Genuine validated report from the existing explicitly inert page reader."""
    count = len(texts)
    report = _report(native=("original",) * count, candidates=dict(enumerate(texts, 1)),
        requested=tuple(range(1, count + 1)), max_pages=count)
    for page, score in zip(report["pages"], scores or [.99] * count, strict=True):
        if page["candidate"] is not None:
            page["candidate"]["mean_confidence"] = score
            for line in page["candidate"]["lines"]:
                line["score"] = score
    raw = json.dumps(report, sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8")
    return ReviewDocument(report, recovery_sha256=hashlib.sha256(raw).hexdigest())


@pytest.fixture
def document():
    return document_for()


def new(document, **changes):
    return audit.create_audit(document, **{"threshold": .95, "sample_size": 3, "seed": SEED, **changes})


def reviewed(payload, document, *, text="Alpha.", page=None, note=""):
    return audit.record_review(payload, document, page_number=page or payload["plan"]["selected_pages"][0],
        outcome="reviewed", text=text, note=note, confirmed=True)


def mixed_document():
    report = _report(native=("short",) * 7 + (HEALTHY, "short"), max_pages=7,
        requested=tuple(range(1, 8)), candidates={1: "Alpha", 2: "Beta", 3: "Gamma",
            4: RuntimeError("inert failed candidate"), 5: "", 6: "x" * 20_001, 7: "Delta"})
    for page in report["pages"]:
        if page["candidate"] is not None:
            mean = {1: .99, 2: .95, 3: .94, 5: None, 6: .99, 7: .8}[page["page_number"]]
            page["candidate"]["mean_confidence"] = mean
            for line in page["candidate"]["lines"]:
                line["score"] = mean
    raw = json.dumps(report, sort_keys=True).encode()
    return ReviewDocument(report, recovery_sha256=hashlib.sha256(raw).hexdigest())


def test_complete_frame_exclusions_equality_threshold_and_sample_counts():
    document = mixed_document()
    value = new(document, sample_size=100)
    frame = value["plan"]["frame"]
    assert [row["page_number"] for row in frame] == list(range(1, 10))
    assert [row["reason"] for row in frame] == [None, None, "low_confidence", "failed", "empty", "long", "low_confidence", "unselected", "deferred"]
    assert [row["eligible"] for row in frame] == [True, True] + [False] * 7
    assert set(value["plan"]["selected_pages"]) == {1, 2}
    assert value["plan"]["inclusion_probability"] == {"numerator": 2, "denominator": 2}
    summary = audit.audit_summary(value, document)
    assert summary["counts"] == {"universe": 9, "eligible": 2, "excluded": 7, "sampled": 2,
        "eligible_unselected": 0, "pending": 2, "reviewed": 0, "unresolved": 0, "unavailable": 0}
    assert summary["metrics"] is None
    assert summary["exclusion_reasons"] == {"unselected": 1, "deferred": 1, "failed": 1,
        "empty": 1, "long": 1, "missing_confidence": 0, "low_confidence": 2}


def test_fixed_sha_counter_fisher_yates_vector_and_reconstruction():
    # Independently computed from the documented raw SHA blocks (stdlib-only),
    # not from the production selector. The first block is be90ec58...e3b0493.
    document = document_for(tuple(f"Line {i}" for i in range(12)))
    value = new(document, sample_size=6)
    assert value["plan"]["selected_pages"] == [12, 3, 4, 1, 9, 5]
    assert value["plan"]["inclusion_probability"] == {"numerator": 6, "denominator": 12}
    assert audit.validate_audit(value, document) == value == new(document, sample_size=6)
    assert audit.audit_summary(value, document)["counts"]["eligible_unselected"] == 6
    assert new(document, sample_size=6, seed="1" * 64)["plan"]["selected_pages"] != value["plan"]["selected_pages"]


def test_unbiased_range_rejects_top_value_consumes_counter_and_has_finite_budget(monkeypatch):
    class Block:
        def __init__(self, x):
            self.x = x

        def digest(self):
            return self.x.to_bytes(8, "big") + bytes(24)

    calls = []
    values = iter([(1 << 64) - 1, 5])

    def digest(data):
        calls.append(data)
        return Block(next(values))

    monkeypatch.setattr(audit.hashlib, "sha256", digest)
    assert audit._sample([1, 2, 3], 1, SEED) == [3]
    assert [int.from_bytes(data[-8:], "big") for data in calls] == [0, 1]
    assert calls[0][:-8] == b"ocr-spot-audit-sha256-counter-rejection-fisher-yates-v1\0" + bytes(32)
    monkeypatch.setattr(audit, "MAX_RANDOM_BLOCKS", 2)
    calls.clear()

    def reject(data):
        calls.append(data)
        return Block((1 << 64) - 1)

    monkeypatch.setattr(audit.hashlib, "sha256", reject)
    with pytest.raises(ValueError, match="over-budget"):
        audit._sample([1, 2, 3], 1, SEED)
    assert len(calls) == 2


@pytest.mark.parametrize("text,score", [("", None), ("Alpha", .1)], ids=["empty", "low-confidence"])
def test_zero_eligible_is_empty_not_a_pass_or_division_by_zero(text, score, monkeypatch):
    document = document_for((text,), scores=(score,))
    monkeypatch.setattr(ocr_evaluation, "evaluate_ocr", lambda *_: pytest.fail("empty sample scored"))
    value = new(document)
    assert value["plan"]["selected_pages"] == value["records"] == value["drafts"] == []
    assert value["plan"]["inclusion_probability"] == {"numerator": 0, "denominator": 0}
    assert audit.validate_audit(value, document) == value
    summary = audit.audit_summary(value, document)
    assert summary["counts"]["excluded"] == 1 and summary["metrics"] is None
    assert summary["manual_review_required"] is True and summary["canonical_extraction_modified"] is False


@pytest.mark.parametrize("threshold", [None, True, 0, 1, "0.95", [], -.01, 1.01, math.nan, math.inf, -0.0],
                         ids=["none", "bool", "int-zero", "int-one", "string", "list", "negative", "above-one", "nan", "inf", "minus-zero"])
def test_threshold_exact_finite_float(document, threshold):
    with pytest.raises(ValueError):
        new(document, threshold=threshold)


@pytest.mark.parametrize("sample_size", [True, 0, -1, 101, 1., None, "1"])
def test_sample_budget_exact_integer(document, sample_size):
    with pytest.raises(ValueError):
        new(document, sample_size=sample_size)


@pytest.mark.parametrize("seed", [None, 12, "0" * 63, "0" * 65, "A" * 64, "g" * 64, "0" * 63 + "\n"])
def test_seed_fixed_lower_hex(document, seed):
    with pytest.raises(ValueError):
        new(document, seed=seed)


def test_declared_parent_exact_binding_but_not_authenticated_ancestry(document):
    value = new(document, parent_audit_sha256="a" * 64)
    assert audit.validate_audit(value, document, parent_audit_sha256="a" * 64) == value
    for expected in (None, "b" * 64, "A" * 64, True):
        with pytest.raises(ValueError):
            audit.validate_audit(value, document, parent_audit_sha256=expected)
    updated = reviewed(value, document)
    assert updated["parent_audit_sha256"] == "a" * 64
    assert audit.audit_summary(updated, document)["manual_review_required"] is True


def test_review_draft_are_detached_and_neither_replaces_the_other(document):
    original = new(document)
    before = copy.deepcopy(original)
    page = original["plan"]["selected_pages"][0]
    typed = audit.remember_draft(original, document, page_number=page, text="unfinished\n")
    complete = reviewed(typed, document, text="reviewed scan text")
    changed = audit.remember_draft(complete, document, page_number=page, text="new unfinished text")
    assert original == before and typed["records"] == before["records"]
    assert complete["drafts"] == typed["drafts"]
    assert changed["records"] == complete["records"] and changed["drafts"][0]["text"] == "new unfinished text"
    detached = audit.validate_audit(changed, document)
    detached["records"][0]["text"] = "local mutation"
    assert changed["records"][0]["text"] == "reviewed scan text"


def test_four_outcomes_coverage_and_only_completed_scoring(document, monkeypatch):
    document = document_for(("Alpha", "Beta", "Gamma", "Delta"))
    value = new(document, sample_size=4)
    selected = value["plan"]["selected_pages"]
    value = reviewed(value, document, text="", page=selected[0])
    for page, outcome in zip(selected[1:3], ("unresolved", "unavailable"), strict=True):
        value = audit.record_review(value, document, page_number=page, outcome=outcome,
                                   text=None, note="cannot verify this page", confirmed=False)
    before = copy.deepcopy(value)
    calls = []
    scorer = ocr_evaluation.evaluate_ocr

    def observe(payload):
        calls.append(copy.deepcopy(payload))
        return scorer(payload)

    monkeypatch.setattr(ocr_evaluation, "evaluate_ocr", observe)
    summary = audit.audit_summary(value, document)
    assert {key: summary["counts"][key] for key in ("reviewed", "unresolved", "unavailable", "pending")} == dict.fromkeys(("reviewed", "unresolved", "unavailable", "pending"), 1)
    expected = {"schema_version": 1, "records": [{"id": f"page-{selected[0]:05d}", "reference": "",
        "prediction": document.page(selected[0])["candidate"]["text"]}]}
    assert calls == [expected] and summary["metrics"] == scorer(expected)
    assert value == before and summary["counts"]["sampled"] == 4
    assert "Completed reviewed subset only" in summary["metric_scope"]


@pytest.mark.parametrize("reference", ["", "Alpha", "A l p h a", "\u00c5ngstr\u00f6m", "  A\nB  ", "not liable"])
def test_complete_unchanged_metric_byte_parity_including_empty_reference(reference):
    document = document_for(("Alpha",))
    value = reviewed(new(document), document, text=reference)
    summary = audit.audit_summary(value, document)
    expected = ocr_evaluation.evaluate_ocr({"schema_version": 1,
        "records": [{"id": "page-00001", "reference": reference, "prediction": "Alpha"}]})
    def canonical(payload):
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    assert canonical(summary["metrics"]) == canonical(expected)


def test_creation_validation_editing_restore_and_unresolved_summary_never_score(document, monkeypatch):
    monkeypatch.setattr(ocr_evaluation, "evaluate_ocr", lambda *_: pytest.fail("implicit/unresolved scoring"))
    value = new(document)
    value = audit.validate_audit(json.loads(json.dumps(value)), document)
    for number in value["plan"]["selected_pages"]:
        value = audit.remember_draft(value, document, page_number=number, text="unfinished")
        value = audit.record_review(value, document, page_number=number, outcome="unresolved",
                                   text=None, note="unreadable glyph", confirmed=False)
    assert audit.audit_summary(value, document)["metrics"] is None
    value = reviewed(value, document)
    assert audit.validate_audit(value, document) == value  # Persisted reviewed data also never scores.


@pytest.mark.parametrize("changes", [
    {"confirmed": False}, {"confirmed": 1}, {"confirmed": "true"}, {"text": None},
    {"text": "\ud800"}, {"text": "x" * 20_001}, {"note": "x" * 2001},
    {"outcome": "unresolved", "text": None, "note": "", "confirmed": False},
    {"outcome": "unavailable", "text": None, "note": "  \n", "confirmed": False},
    {"outcome": "unresolved", "text": "guess", "note": "unclear", "confirmed": False},
    {"outcome": "unavailable", "text": None, "note": "unclear", "confirmed": True},
    {"outcome": "pending", "text": None, "note": "not empty", "confirmed": False},
    {"outcome": "complete"}, {"outcome": []}, {"page_number": True}, {"page_number": 10_001},
], ids=["unconfirmed-review", "bool-int", "bool-string", "missing-text", "surrogate", "long-text", "long-note",
        "empty-unresolved", "blank-unavailable", "guessed-unresolved", "confirmed-unavailable", "pending-note",
        "unknown-outcome", "list-outcome", "bool-page", "outside-page"])
def test_invalid_record_transitions_are_atomic(document, changes):
    value = new(document)
    before = copy.deepcopy(value)
    args = {"page_number": value["plan"]["selected_pages"][0], "outcome": "reviewed", "text": "ok", "note": "", "confirmed": True, **changes}
    with pytest.raises(ValueError):
        audit.record_review(value, document, **args)
    assert value == before


def test_reset_to_pending_retains_draft_and_cannot_score(document, monkeypatch):
    value = reviewed(new(document), document)
    page = value["plan"]["selected_pages"][0]
    value = audit.remember_draft(value, document, page_number=page, text="partial")
    reset = audit.record_review(value, document, page_number=page, outcome="pending", text=None, note="", confirmed=False)
    assert reset["drafts"] == value["drafts"] and reset["records"][0]["text"] is None
    monkeypatch.setattr(ocr_evaluation, "evaluate_ocr", lambda *_: pytest.fail("pending scored"))
    assert audit.audit_summary(reset, document)["metrics"] is None


@pytest.mark.parametrize("path,value", [
    (("schema_version",), True), (("kind",), "other"), (("source_sha256",), "b" * 64),
    (("recovery_sha256",), "c" * 64), (("page_count",), 4), (("manual_review_required",), False),
    (("canonical_extraction_modified",), True), (("plan", "algorithm"), "other"),
    (("plan", "threshold"), .1), (("plan", "seed"), "1" * 64),
    (("plan", "inclusion_probability", "numerator"), True),
    (("plan", "inclusion_probability", "denominator"), 1),
    (("plan", "frame", 0, "eligible"), False), (("plan", "frame", 0, "reason"), "failed"),
    (("plan", "frame", 0, "mean_confidence"), .1), (("records", 0, "page_number"), False),
    (("records", 0, "text"), "smuggled text"), (("drafts", 0, "text"), "\udfff"),
], ids=["version-bool", "kind", "source", "recovery", "page-count", "approval", "canonical", "algorithm",
        "threshold", "seed", "numerator", "denominator", "eligible", "reason", "confidence", "record-page", "pending-text", "draft-surrogate"])
def test_plan_or_record_tampering_rejected(document, path, value):
    payload = new(document)
    node = payload
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    # A changed threshold that retains an identical frame is a valid new plan,
    # not authenticated provenance. Use a threshold that actually changes it.
    if path == ("plan", "threshold"):
        node[path[-1]] = 1.
    with pytest.raises(ValueError):
        audit.validate_audit(payload, document)


@pytest.mark.parametrize("mutation", ["extra-root", "missing-root", "extra-plan", "extra-frame", "reorder-frame",
    "duplicate-selected", "reorder-record", "remove-draft", "extra-record", "tuple-frame"])
def test_closed_schema_and_complete_ordered_membership(document, mutation):
    value = new(document)
    if mutation == "extra-root":
        value["metrics"] = {"passed": True}
    elif mutation == "missing-root":
        del value["records"]
    elif mutation == "extra-plan":
        value["plan"]["cached_score"] = 0.
    elif mutation == "extra-frame":
        value["plan"]["frame"][0]["text"] = "unexpected"
    elif mutation == "reorder-frame":
        value["plan"]["frame"].reverse()
    elif mutation == "duplicate-selected":
        value["plan"]["selected_pages"][1] = value["plan"]["selected_pages"][0]
    elif mutation == "reorder-record":
        value["records"].reverse()
    elif mutation == "remove-draft":
        value["drafts"].pop()
    elif mutation == "extra-record":
        value["records"][0]["confirmed"] = True
    else:
        value["plan"]["frame"] = tuple(value["plan"]["frame"])
    with pytest.raises(ValueError):
        audit.validate_audit(value, document)


def test_edited_or_excluded_page_cannot_enter_selected_records(document):
    value = new(document, sample_size=1)
    outside = next(page for page in document.page_numbers if page not in value["plan"]["selected_pages"])
    with pytest.raises(ValueError):
        reviewed(value, document, page=outside)
    with pytest.raises(ValueError):
        audit.remember_draft(value, document, page_number=outside, text="not sampled")


def test_text_and_serialized_byte_budgets_fail_atomically(document, monkeypatch):
    value = new(document)
    before = copy.deepcopy(value)
    with monkeypatch.context() as patch:
        patch.setattr(audit, "MAX_AUTHORED_CHARS", 3)
        with pytest.raises(ValueError):
            reviewed(value, document, text="four")
    with monkeypatch.context() as patch:
        encoded = (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()
        patch.setattr(audit, "MAX_AUDIT_BYTES", len(encoded))
        assert audit.validate_audit(value, document) == value
        patch.setattr(audit, "MAX_AUDIT_BYTES", len(encoded) - 1)
        with pytest.raises(ValueError):
            audit.validate_audit(value, document)
    with pytest.raises(ValueError):
        audit.remember_draft(value, document, page_number=value["plan"]["selected_pages"][0], text="x" * 20_001)
    assert value == before


def test_alignment_budget_failure_has_no_partial_clean_summary_or_mutation(monkeypatch):
    document = document_for(("x" * 6000,))
    value = reviewed(new(document), document, text="y" * 6000)
    before = copy.deepcopy(value)
    with pytest.raises(ValueError, match="alignment"):
        audit.audit_summary(value, document)
    assert value == before


def test_normalization_expansion_failure_preserves_review_and_draft():
    document = document_for(("Alpha",))
    value = reviewed(new(document), document, text="\u0344" * 20_000)
    before = copy.deepcopy(value)
    with pytest.raises(ValueError, match="normalized"):
        audit.audit_summary(value, document)
    assert value == before


def test_bounds_and_binding_preflight_before_document_copy(document, monkeypatch):
    value = new(document)
    value["records"][0]["note"] = "x" * 2001
    monkeypatch.setattr(ReviewDocument, "recovery_snapshot", lambda *_: pytest.fail("oversized payload reached document copy"))
    with pytest.raises(ValueError):
        audit.validate_audit(value, document)


def test_document_binding_and_declared_universe_cannot_be_substituted(document):
    value = new(document)
    changed = document_for(("different", "Beta.", "Gamma."))
    with pytest.raises(ValueError):
        audit.validate_audit(value, changed)
    document.page_count = audit.MAX_PAGES + 1
    with pytest.raises(ValueError):
        new(document)
