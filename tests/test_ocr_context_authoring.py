"""Generated partial authoring, exact Unicode ranges and bound result navigation."""

import copy

import pytest

import ocr_context_authoring as authoring
import ocr_context_evaluation as evaluation
from ocr_review import ReviewDocument
from test_ocr_review import missing_report, report as recovery_report


SOURCE, RECOVERY, REFERENCE, CORRESPONDENCE = (letter * 64 for letter in "abcd")
TEXT = "A owes 10 dollars."


class Document:
    source_sha256, recovery_sha256 = SOURCE, RECOVERY

    def __init__(self, pages=None, page_count=4):
        self.page_count = page_count
        self.pages = [{"page_number": 1, "status": "available", "text": TEXT}] if pages is None else pages

    def context_candidate_pages(self):
        return copy.deepcopy(self.pages)


def context(text=TEXT, *, identifier="ctx1", page=1):
    start = text.index("10")
    return {"context_id": identifier, "page_number": page,
            "source_anchor": {"kind": "sentence", "bbox": [.1, .2, .9, .3], "cell": None},
            "reference": text, "checks": [{"check_id": "check1", "category": "number",
                "reference_span": [start, start + 2], "left_anchor": text[:start], "right_anchor": text[start + 2:]}],
            "correspondence": {"status": "mapped", "candidate_span": [0, len(text)]}}


def draft(*contexts):
    return {"contexts": list(contexts) or [context()], "selected_context_id": None, "selected_check_id": None}


def exported(value=None, document=None):
    value, document = draft() if value is None else value, Document() if document is None else document
    reference = authoring.build_reference(value, document, confirmed=True)
    mapping = authoring.build_correspondence(value, document, reference, REFERENCE, confirmed=True)
    return reference, mapping


def evaluated(value=None, document=None):
    document = Document() if document is None else document
    reference, mapping = exported(value, document)
    bindings = dict(source_sha256=SOURCE, recovery_sha256=RECOVERY,
                    reference_sha256=REFERENCE, correspondence_sha256=CORRESPONDENCE)
    result = evaluation.evaluate_context_checks(reference, mapping, source_sha256=SOURCE,
        recovery_sha256=RECOVERY, reference_sha256=REFERENCE, page_count=document.page_count,
        candidate_pages=document.context_candidate_pages())
    result["inputs"] = {"pdf_sha256": SOURCE, "recovery_sha256": RECOVERY,
                        "reference_sha256": REFERENCE, "correspondence_sha256": CORRESPONDENCE}
    return result, reference, mapping, bindings


def test_empty_and_incomplete_drafts_detach_without_approvals():
    assert authoring.validate_authoring(authoring.empty_authoring(), Document()) == authoring.empty_authoring()
    c = context()
    c.update(source_anchor=None, reference="", checks=[], correspondence={"status": "unreviewed", "candidate_span": None})
    value = draft(c)
    value["selected_context_id"] = "ctx1"
    before = copy.deepcopy(value)
    result = authoring.validate_authoring(value, Document())
    assert result == before and "approval" not in result
    result["contexts"][0]["correspondence"]["status"] = "missing"
    assert value == before
    with pytest.raises(ValueError):
        authoring.build_reference(value, Document(), confirmed=True)


@pytest.mark.parametrize("location", ["root", "context", "check", "mapping", "anchor"])
def test_drafts_reject_unknown_fields_including_claimed_approval(location):
    value = draft()
    c = value["contexts"][0]
    target = {"root": value, "context": c, "check": c["checks"][0],
              "mapping": c["correspondence"], "anchor": c["source_anchor"]}[location]
    target["human_reviewed"] = True
    with pytest.raises(ValueError):
        authoring.validate_authoring(value, Document())


@pytest.mark.parametrize("change", [
    lambda d: d.update(selected_context_id="absent"),
    lambda d: d.update(selected_check_id="check1"),
    lambda d: d.update(selected_context_id="ctx1", selected_check_id="absent"),
    lambda d: d["contexts"].append(copy.deepcopy(d["contexts"][0])),
    lambda d: d["contexts"][0]["checks"].append(copy.deepcopy(d["contexts"][0]["checks"][0])),
    lambda d: d["contexts"][0].update(page_number=True),
    lambda d: d["contexts"][0].update(context_id="bad id"),
    lambda d: d["contexts"][0].update(reference="\ud800"),
    lambda d: d["contexts"][0]["checks"][0].update(reference_span=[False, 2]),
    lambda d: d["contexts"][0]["checks"][0].update(reference_span=[1, 100]),
    lambda d: d["contexts"][0]["checks"][0].update(category="meaning"),
    lambda d: d["contexts"][0]["correspondence"].update(status="missing"),
    lambda d: d["contexts"][0]["source_anchor"].update(bbox=[False, 0, 1, 1]),
])
def test_invalid_partial_fields_fail_closed(change):
    value = draft()
    change(value)
    with pytest.raises(ValueError):
        authoring.validate_authoring(value, Document())


@pytest.mark.parametrize("budget", ["contexts", "checks", "reference", "candidate", "total_candidate", "anchor"])
def test_real_aggregate_and_individual_bounds(budget):
    value, document = draft(), Document()
    if budget == "contexts":
        value = draft(*(context(identifier=str(i)) for i in range(257)))
    elif budget == "checks":
        value = draft(context(identifier="a"), context(identifier="b"))
        for c in value["contexts"]:
            c["checks"] = [{"check_id": str(i), "category": "number", "reference_span": None,
                            "left_anchor": "", "right_anchor": ""} for i in range(513)]
    elif budget == "reference":
        value = draft(*(context(identifier=str(i)) for i in range(51)))
        for c in value["contexts"]:
            c.update(reference="x" * 20000, checks=[], correspondence={"status": "unreviewed", "candidate_span": None})
    elif budget == "candidate":
        document.pages[0]["text"] = "x" * 100001
    elif budget == "total_candidate":
        document = Document([{"page_number": i, "status": "available", "text": "x" * 100000}
                             for i in range(1, 22)], page_count=21)
    else:
        value["contexts"][0]["checks"][0]["left_anchor"] = "x" * 257
    with pytest.raises(ValueError):
        if budget == "total_candidate":
            reference = authoring.build_reference(value, document, confirmed=True)
            authoring.build_correspondence(value, document, reference, REFERENCE, confirmed=True)
        else:
            authoring.validate_authoring(value, document)


@pytest.mark.parametrize("status", ["unreviewed", "missing", "ambiguous"])
def test_unmapped_and_empty_drafts_never_materialize_candidate_pages(monkeypatch, status):
    document = Document()
    monkeypatch.setattr(document, "context_candidate_pages", lambda: pytest.fail("draft read unused candidates"))
    assert authoring.validate_authoring(authoring.empty_authoring(), document) == authoring.empty_authoring()
    value = draft()
    value["contexts"][0]["correspondence"] = {"status": status, "candidate_span": None}
    assert authoring.validate_authoring(value, document) == value


def test_large_unrelated_candidate_does_not_block_draft_but_full_export_remains_bounded():
    document = Document([{"page_number": 1, "status": "available", "text": TEXT},
                         {"page_number": 2, "status": "available", "text": "x" * 100001}])
    assert authoring.validate_authoring(draft(), document) == draft()
    reference = authoring.build_reference(draft(), document, confirmed=True)
    with pytest.raises(ValueError, match="text length"):
        authoring.build_correspondence(draft(), document, reference, REFERENCE, confirmed=True)


@pytest.mark.parametrize("utf16,selected,expected", [
    ([2, 4], "😀", [2, 3]), ([4, 6], "e\u0301", [3, 5]), ([6, 8], "\r\n", [5, 7]),
    ([8, 9], "Z", [7, 8]), ([0, 10], " A😀e\u0301\r\nZ ", [0, 9]),
    ([4, 4], "", [3, 3]), ([0, 1], " ", [0, 1]),
])
def test_utf16_selection_preserves_astral_combining_crlf_and_spaces(utf16, selected, expected):
    assert authoring.selection_span(" A😀e\u0301\r\nZ ", utf16, selected) == expected
    assert authoring.selection_span("", [0, 0], "") == [0, 0]


@pytest.mark.parametrize("text,span,selected", [
    ("a😀b", [1, 2], "😀"), ("a😀b", [2, 3], "😀"), ("a😀b", [3, 1], "😀"),
    ("a😀b", [0, 5], "a😀b"), ("a😀b", [False, 1], "a"),
    ("e\u0301", [0, 2], "é"), ("\r\n", [0, 2], "\n"),
    ("10 10", [0, 2], "20"), ("x", [0.0, 1], "x"), ("\ud800", [0, 1], "\ud800"),
])
def test_selection_refuses_surrogate_splits_normalization_and_forged_values(text, span, selected):
    with pytest.raises(ValueError):
        authoring.selection_span(text, span, selected)


def test_preview_keeps_draft_and_reports_current_and_proposed_anchor_ambiguity(monkeypatch):
    c = context("aaa10.")
    c["checks"][0]["left_anchor"] = "aa"
    value = draft(c)
    assert authoring.validate_authoring(value, Document()) == value
    monkeypatch.setattr(evaluation, "evaluate_context_checks", lambda *a, **k: pytest.fail("preview scored"))
    before = copy.deepcopy(c)
    view = authoring.preview_check(c, "check1")
    assert view["selected_text"] == "10" and view["reason"] == "anchor_ambiguous" and not view["valid"]
    assert view["proposed_left_anchor"] == "aaa" and view["proposed_reason"] is None
    assert c == before
    with pytest.raises(ValueError, match="anchors"):
        authoring.build_reference(value, Document(), confirmed=True)
    c = context("X" * 300 + "10" + "Y" * 300)
    c["checks"][0].update(left_anchor="", right_anchor="")
    view = authoring.preview_check(c, "check1")
    assert len(view["proposed_left_anchor"]) == len(view["proposed_right_anchor"]) == 256
    assert view["proposed_reason"] == "anchor_ambiguous"


@pytest.mark.parametrize("confirmation", [False, None, 1, "true"])
def test_both_builders_require_literal_confirmation(confirmation):
    reference = authoring.build_reference(draft(), Document(), confirmed=True)
    with pytest.raises(ValueError, match="confirm"):
        authoring.build_reference(draft(), Document(), confirmed=confirmation)
    with pytest.raises(ValueError, match="confirm"):
        authoring.build_correspondence(draft(), Document(), reference, REFERENCE, confirmed=confirmation)


def test_correspondence_reuses_full_validation_without_scoring_and_rejects_reuse(monkeypatch):
    value = draft(context(identifier="a"), context(identifier="b"))
    reference = authoring.build_reference(value, Document(), confirmed=True)
    monkeypatch.setattr(evaluation, "evaluate_context_checks", lambda *a, **k: pytest.fail("export scored"))
    with pytest.raises(ValueError, match="overlap|reuse"):
        authoring.build_correspondence(value, Document(), reference, REFERENCE, confirmed=True)
    value["contexts"][1]["correspondence"] = {"status": "missing", "candidate_span": None}
    mapping = authoring.build_correspondence(value, Document(), reference, REFERENCE, confirmed=True)
    assert [c["context_id"] for c in mapping["contexts"]] == ["a", "b"]
    assert mapping["approval"] == "human_reviewed" and mapping["reference_sha256"] == REFERENCE
    reference["contexts"].reverse()
    with pytest.raises(ValueError, match="current draft"):
        authoring.build_correspondence(value, Document(), reference, REFERENCE, confirmed=True)


@pytest.mark.parametrize("availability", ["retry_failed", "deferred", "not_selected"])
def test_unavailable_mapping_stays_explicit_and_never_uses_reference_as_candidate(availability):
    pages = [] if availability == "not_selected" else [{"page_number": 1, "status": availability, "text": None}]
    document, value = Document(pages), draft()
    with pytest.raises(ValueError, match="available candidate"):
        authoring.validate_authoring(value, document)
    value["contexts"][0]["correspondence"] = {"status": "missing", "candidate_span": None}
    result, reference, mapping, bindings = evaluated(value, document)
    target = authoring.result_target(result, reference, mapping, bindings, 1, 1)
    assert target["candidate_span"] is None and target["reason"] == availability
    assert target["status"] == "abstained"


def test_empty_candidate_is_a_mapped_failure_and_unreviewed_draft_cannot_export():
    value, document = draft(), Document([{"page_number": 1, "status": "available", "text": ""}])
    value["contexts"][0]["correspondence"]["candidate_span"] = [0, 0]
    result, reference, mapping, bindings = evaluated(value, document)
    target = authoring.result_target(result, reference, mapping, bindings, 1, 1)
    assert target["candidate_span"] == [0, 0] and target["status"] == "failed"
    value["contexts"][0]["correspondence"]["status"] = "unreviewed"
    value["contexts"][0]["correspondence"]["candidate_span"] = None
    assert authoring.validate_authoring(value, document) == value
    with pytest.raises(ValueError):
        authoring.build_correspondence(value, document, reference, REFERENCE, confirmed=True)


def test_equal_occurrences_jump_by_exported_ordinal_without_scoring(monkeypatch):
    value = draft(context(identifier="first"), context(identifier="second", page=2))
    value["contexts"][1]["source_anchor"]["bbox"] = [.2, .4, .8, .5]
    document = Document([{"page_number": i, "status": "available", "text": TEXT} for i in (1, 2)])
    result, reference, mapping, bindings = evaluated(value, document)
    monkeypatch.setattr(evaluation, "evaluate_context_checks", lambda *a, **k: pytest.fail("focus scored"))
    target = authoring.result_target(result, reference, mapping, bindings, 2, 1)
    assert target["context_id"] == "second" and target["check_id"] == "check1" and target["page_number"] == 2
    assert target["source_anchor"]["bbox"] == [.2, .4, .8, .5] and target["candidate_span"] == [7, 9]
    target["source_anchor"]["bbox"][0] = .3
    assert reference["contexts"][1]["source_anchor"]["bbox"][0] == .2
    result["contexts"].reverse()
    with pytest.raises(ValueError, match="ordinal"):
        authoring.result_target(result, reference, mapping, bindings, 2, 1)


@pytest.mark.parametrize("field", ["pdf_sha256", "recovery_sha256", "reference_sha256", "correspondence_sha256"])
def test_result_focus_refuses_any_stale_input_digest(field):
    result, reference, mapping, bindings = evaluated()
    result["inputs"][field] = "e" * 64
    with pytest.raises(ValueError, match="binding"):
        authoring.result_target(result, reference, mapping, bindings, 1, 1)


@pytest.mark.parametrize("context_index,check_index", [(True, 1), (1, False), (0, 1), (1, 0), (2, 1), (1, 2)])
def test_result_focus_refuses_nonexistent_or_boolean_ordinals(context_index, check_index):
    with pytest.raises(ValueError):
        authoring.result_target(*evaluated(), context_index, check_index)


@pytest.mark.parametrize("corruption", ["context_span", "reference_span", "candidate_span", "category"])
def test_result_focus_refuses_forged_occurrence_fields(corruption):
    result, reference, mapping, bindings = evaluated()
    row, check = result["contexts"][0], result["contexts"][0]["checks"][0]
    if corruption == "context_span":
        row["candidate_context_span"][0] = False
    elif corruption == "reference_span":
        check["reference_span"] = [1, 3]
    elif corruption == "candidate_span":
        check["candidate_span"] = [0, 100]
    else:
        check["category"] = "unit"
    with pytest.raises(ValueError):
        authoring.result_target(result, reference, mapping, bindings, 1, 1)


def test_review_document_adapter_exposes_only_saved_candidate_text_and_detaches():
    recovery = recovery_report()
    recovery["pages"][0]["original_text"] = "Unrelated native fallback."
    recovery["pages"][0]["reasons"] = ["requested", "short_text"]
    document = ReviewDocument(recovery, recovery_sha256=RECOVERY)
    pages = document.context_candidate_pages()
    assert pages == [{"page_number": 1, "status": "available", "text": recovery["pages"][0]["candidate"]["text"]}]
    pages[0]["text"] = "Changed output"
    assert document.context_candidate_pages()[0]["text"] != pages[0]["text"]
    document = ReviewDocument(missing_report(), recovery_sha256=RECOVERY)
    assert document.context_candidate_pages() == [
        {"page_number": 1, "status": "retry_failed", "text": None},
        {"page_number": 2, "status": "deferred", "text": None},
    ]
