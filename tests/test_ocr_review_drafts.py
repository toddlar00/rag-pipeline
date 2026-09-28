"""Review snapshots retain unfinished work without manufacturing confirmation."""

import copy

import pytest

from ocr_review import ReviewDocument
from ocr_review_drafts import (MAX_HISTORY, build_draft, initial_state, record_event,
                               reference_text, remember_reference, restore_state, validate_draft)
from test_ocr_review import missing_report, report


@pytest.fixture
def document():
    return ReviewDocument(report(), recovery_sha256="b" * 64)


def test_roundtrip_preserves_selections_and_recomputes_layout(document):
    state = initial_state(document)
    state["selections"] = {"body": [0, .1, 1, .8], "gutter": [.45, .1, .55, .8]}
    state["layouts"] = [document.layout_page(1, state["selections"]["body"], state["selections"]["gutter"])]
    state["regions"] = [{"region_id": "crop-1", "page_number": 1, "bbox": [.1, .2, .8, .9]}]
    state["first"] = [10, 20]
    state["line_order"] = [999]  # Not persisted; derive again from current report.
    remember_reference(state, "UNCONFIRMED <script>text</script>", document)
    record_event(state, "region_added")
    snapshot = build_draft(state, document)
    before = copy.deepcopy(snapshot)
    restored = restore_state(snapshot, document)
    assert restored["first"] is None
    assert restored["line_order"] == [0, 1, 3, 2, 4, 5]
    assert restored["regions"] == state["regions"]
    assert restored["selections"] == state["selections"]
    assert restored["references"] == []
    assert reference_text(restored).startswith("UNCONFIRMED")
    assert restored["event_count"] == 2
    assert restored["history"][-1]["action"] == "draft_loaded"
    assert snapshot == before
    assert snapshot["manual_review_required"] is True


def test_missing_candidate_drafts_can_restore_crops_and_transcription():
    document = ReviewDocument(missing_report(), recovery_sha256="b" * 64)
    state = initial_state(document)
    state["page"] = 2
    state["selections"] = {"crop": [.1, .2, .8, .9]}
    remember_reference(state, "Read from the original scan", document)
    restored = restore_state(build_draft(state, document), document)
    assert restored["page"] == 2
    assert restored["line_order"] is None
    assert reference_text(restored) == "Read from the original scan"


def test_region_insertion_order_is_preserved_for_cross_page_undo():
    document = ReviewDocument(missing_report(), recovery_sha256="b" * 64)
    state = initial_state(document)
    state["regions"] = [{"region_id": "crop-1", "page_number": 3, "bbox": [.1, .2, .8, .9]},
                        {"region_id": "crop-2", "page_number": 1, "bbox": [.1, .2, .8, .9]}]
    assert restore_state(build_draft(state, document), document)["regions"] == state["regions"]


@pytest.mark.parametrize("key,value", [("schema_version", True), ("schema_version", 5), ("kind", "approval"),
                                       ("source_sha256", "c" * 64), ("recovery_sha256", "c" * 64),
                                       ("page_count", True), ("page_count", 2), ("page", True), ("page", 0),
                                       ("manual_review_required", False), ("manual_review_required", 1),
                                       ("parent_draft_sha256", "bad"), ("selections", {"path": "secret"}),
                                       ("selections", {"crop": [0, 0, float("nan"), 1]}),
                                       ("layouts", None), ("regions", {}), ("references", "text"),
                                       ("reference_drafts", {}), ("event_count", True), ("event_count", -1),
                                       ("event_count", 10 ** 1000), ("event_count", 1), ("history", {})])
def test_malformed_or_wrong_bound_snapshots_are_rejected(document, key, value):
    snapshot = build_draft(initial_state(document), document)
    snapshot[key] = value
    with pytest.raises(ValueError):
        validate_draft(snapshot, document)


def test_unknown_and_missing_snapshot_fields_rejected(document):
    snapshot = build_draft(initial_state(document), document)
    snapshot["approved"] = True
    with pytest.raises(ValueError):
        validate_draft(snapshot, document)
    del snapshot["approved"]
    del snapshot["history"]
    with pytest.raises(ValueError):
        validate_draft(snapshot, document)


@pytest.mark.parametrize("entry", [{"page_number": 1, "text": "x" * 20_001},
                                   {"page_number": True, "text": "text"}, {"page_number": 2, "text": "text"},
                                   {"page_number": 1, "text": None}, {"page_number": 1, "text": "\ud800"},
                                   {"page_number": 1, "text": "text", "confirmed": True}])
def test_unconfirmed_reference_text_is_bounded_and_not_confirmation(document, entry):
    snapshot = build_draft(initial_state(document), document)
    snapshot["reference_drafts"] = [entry]
    with pytest.raises(ValueError):
        validate_draft(snapshot, document)


def test_duplicate_reference_drafts_rejected(document):
    snapshot = build_draft(initial_state(document), document)
    snapshot["reference_drafts"] = [{"page_number": 1, "text": "a"}] * 2
    with pytest.raises(ValueError):
        validate_draft(snapshot, document)


def test_total_text_budget_is_not_only_a_per_page_budget():
    value = missing_report()
    value["page_count"] = 101
    value["summary"]["inspected"] = 101
    document = ReviewDocument(value, recovery_sha256="b" * 64)
    snapshot = build_draft(initial_state(document), document)
    snapshot["reference_drafts"] = [{"page_number": n, "text": "x" * 20_000} for n in range(1, 102)]
    with pytest.raises(ValueError, match="total text"):
        validate_draft(snapshot, document)


def test_blanks_do_not_consume_slots_but_can_shadow_a_stored_reference(document):
    state = initial_state(document)
    remember_reference(state, "", document)
    assert state["reference_drafts"] == []
    state["references"] = [{"page_number": 1, "reference": "old"}]
    remember_reference(state, "", document)
    assert reference_text(state) == ""
    assert state["references"][0]["reference"] == "old"


def test_history_is_bounded_and_truncation_is_explicit_in_event_count(document):
    state = initial_state(document)
    for _ in range(MAX_HISTORY + 5):
        record_event(state, "selection_cleared")
    snapshot = build_draft(state, document)
    assert snapshot["event_count"] == MAX_HISTORY + 5
    assert len(snapshot["history"]) == MAX_HISTORY
    assert snapshot["history"][0]["sequence"] == 6
    assert snapshot["history"][-1]["sequence"] == MAX_HISTORY + 5


def test_terminal_history_snapshot_can_still_be_reopened(document):
    state = initial_state(document)
    state["event_count"] = 1_000_000_000
    state["history"] = [{"sequence": n, "action": "draft_saved", "page_number": 1}
                        for n in range(1_000_000_000 - MAX_HISTORY + 1, 1_000_000_001)]
    restored = restore_state(build_draft(state, document), document)
    assert restored["event_count"] == 1_000_000_000
    assert restored["history"] == state["history"]
    with pytest.raises(ValueError, match="invalid review event"):
        record_event(restored, "region_added")


@pytest.mark.parametrize("key,value", [("sequence", True), ("sequence", 2), ("action", []),
                                       ("action", "approved"), ("page_number", True), ("page_number", 2)])
def test_history_rejects_forged_shape_or_unknown_actions(document, key, value):
    state = initial_state(document)
    record_event(state, "draft_saved")
    snapshot = build_draft(state, document)
    snapshot["history"][0][key] = value
    with pytest.raises(ValueError):
        validate_draft(snapshot, document)


def test_unreproducible_or_candidate_less_layout_rejected(document):
    snapshot = build_draft(initial_state(document), document)
    snapshot["layouts"] = [document.layout_page(1, [0, .1, 1, .8], [.3, .1, .7, .8])]
    with pytest.raises(ValueError, match="reproduced"):
        validate_draft(snapshot, document)
    missing = ReviewDocument(missing_report(), recovery_sha256="b" * 64)
    snapshot = build_draft(initial_state(missing), missing)
    snapshot["selections"] = {"body": [0, .1, 1, .8]}
    with pytest.raises(ValueError, match="no OCR candidate"):
        validate_draft(snapshot, missing)
