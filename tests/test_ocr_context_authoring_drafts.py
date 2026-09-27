"""Context drafts retain incomplete work; never export or restore approval."""

import copy
import hashlib
import json

import pytest

from ocr_context_authoring import empty_authoring
from ocr_review import ReviewDocument
import ocr_review_drafts as drafts
import ocr_review_runtime as runtime
from test_ocr_review import missing_report


@pytest.fixture
def document():
    return ReviewDocument(missing_report(), recovery_sha256="b" * 64)


def unfinished(page=2):
    return {"contexts": [{"context_id": "draft-context", "page_number": page,
                          "source_anchor": None, "reference": "Unfinished 🙂 Cafe\u0301 <text>",
                          "checks": [{"check_id": "draft-check", "category": "name", "reference_span": None,
                                      "left_anchor": "", "right_anchor": ""}],
                          "correspondence": {"status": "unreviewed", "candidate_span": None}}],
            "selected_context_id": "draft-context", "selected_check_id": "draft-check"}


def test_partial_roundtrip_preserves_exact_text_and_detaches_every_level(document):
    state = drafts.initial_state(document)
    state["context_authoring"] = unfinished()
    snapshot = drafts.build_draft(state, document)
    original = copy.deepcopy(snapshot)
    restored = drafts.restore_state(snapshot, document)
    assert snapshot["schema_version"] == 4
    assert restored["context_authoring"] == state["context_authoring"]
    assert restored["context_authoring"]["contexts"][0]["reference"] == "Unfinished 🙂 Cafe\u0301 <text>"
    assert restored["context_authoring"]["contexts"][0]["source_anchor"] is None
    assert restored["context_authoring"]["contexts"][0]["correspondence"]["status"] == "unreviewed"
    restored["context_authoring"]["contexts"][0]["checks"].clear()
    state["context_authoring"]["contexts"][0]["reference"] = "different"
    assert snapshot == original


def test_ambiguous_anchors_and_explicit_missing_mapping_stay_drafts(document):
    state = drafts.initial_state(document)
    state["context_authoring"] = unfinished()
    context = state["context_authoring"]["contexts"][0]
    context.update(reference="A10 A10", source_anchor={"kind": "region", "bbox": [.1, .2, .8, .4], "cell": None})
    context["checks"][0].update(reference_span=[1, 3], left_anchor="A", right_anchor=" A")
    context["correspondence"] = {"status": "missing", "candidate_span": None}
    restored = drafts.restore_state(drafts.build_draft(state, document), document)
    assert restored["context_authoring"] == state["context_authoring"]
    assert restored["references"] == []


def test_ordinary_and_unmapped_snapshots_do_not_read_unrelated_candidate_text(document, monkeypatch):
    monkeypatch.setattr(document, "context_candidate_pages",
                        lambda: pytest.fail("ordinary draft materialized unrelated candidate text"))
    state = drafts.initial_state(document)
    assert drafts.build_draft(state, document)["context_authoring"] == empty_authoring()
    for status in ("unreviewed", "missing", "ambiguous"):
        state["context_authoring"] = unfinished()
        state["context_authoring"]["contexts"][0]["correspondence"]["status"] = status
        snapshot = drafts.build_draft(state, document)
        assert drafts.restore_state(snapshot, document)["context_authoring"] == state["context_authoring"]


def test_transient_owner_reviews_exports_and_results_never_survive_snapshot(document):
    state = drafts.initial_state(document)
    state["context_authoring"] = unfinished()
    transient = {"context_owner": "server-only", "context_view_token": "old-view",
                 "context_reference_reviews": {"draft-context": "f" * 64},
                 "context_mapping_reviews": {"draft-context": "f" * 64},
                 "context_exports": {"reference": object()}, "context_result": {"passed": True}}
    state.update(transient)
    snapshot = drafts.build_draft(state, document)
    restored = drafts.restore_state(snapshot, document)
    assert not set(transient) & set(snapshot)
    assert not set(transient) & set(restored)
    assert snapshot["manual_review_required"] is True
    assert restored["context_authoring"] == state["context_authoring"]


@pytest.mark.parametrize("field", ["approved", "owner", "exports", "result", "view_token"])
def test_partial_authoring_cannot_smuggle_approval_or_runtime_fields(document, field):
    state = drafts.initial_state(document)
    state["context_authoring"] = unfinished()
    state["context_authoring"][field] = True
    with pytest.raises(ValueError):
        drafts.build_draft(state, document)


@pytest.mark.parametrize("version", [1, 2, 3])
def test_legacy_versions_keep_their_closed_schema_and_upgrade_without_context_authority(document, version):
    snapshot = drafts.build_draft(drafts.initial_state(document), document)
    snapshot["schema_version"] = version
    del snapshot["context_authoring"]
    if version < 3:
        del snapshot["omission_decisions"]
    if version < 2:
        del snapshot["assignment_pages"]
        del snapshot["assignment_preview_pages"]
    before = copy.deepcopy(snapshot)
    assert drafts.validate_draft(snapshot, document) == before
    restored = drafts.restore_state(snapshot, document)
    assert snapshot == before
    assert restored["context_authoring"] == empty_authoring()
    upgraded = drafts.build_draft(restored, document)
    assert upgraded["schema_version"] == 4
    assert upgraded["context_authoring"] == empty_authoring()
    snapshot["context_authoring"] = empty_authoring()
    with pytest.raises(ValueError, match="fields"):
        drafts.validate_draft(snapshot, document)


def test_context_history_records_its_page_not_the_other_editor_page(document):
    state = drafts.initial_state(document)
    state["page"] = 1
    state["context_authoring"] = unfinished(page=3)
    drafts.record_event(state, "context_stored", page_number=3)
    drafts.record_event(state, "draft_saved")
    snapshot = drafts.build_draft(state, document)
    assert snapshot["history"] == [{"sequence": 1, "action": "context_stored", "page_number": 3},
                                    {"sequence": 2, "action": "draft_saved", "page_number": 1}]
    assert drafts.restore_state(snapshot, document)["context_authoring"]["contexts"][0]["page_number"] == 3
    snapshot["schema_version"] = 3
    del snapshot["context_authoring"]
    with pytest.raises(ValueError, match="history"):
        drafts.validate_draft(snapshot, document)


@pytest.mark.parametrize("page", [True, 0, 5001, 1.0, "1"])
def test_invalid_explicit_history_page_does_not_consume_an_event(document, page):
    state = drafts.initial_state(document)
    before = copy.deepcopy(state)
    with pytest.raises(ValueError, match="event"):
        drafts.record_event(state, "context_check_stored", page_number=page)
    assert state == before


def test_context_reference_and_both_anchors_share_the_existing_total_text_budget():
    recovery = missing_report()
    recovery["page_count"] = 100
    recovery["summary"]["inspected"] = 100
    document = ReviewDocument(recovery, recovery_sha256="b" * 64)
    state = drafts.initial_state(document)
    state["reference_drafts"] = [{"page_number": n, "text": "x" * 19_990} for n in range(1, 101)]
    state["context_authoring"] = unfinished()
    context = state["context_authoring"]["contexts"][0]
    context["reference"] = "x" * 488
    context["checks"][0].update(left_anchor="L" * 256, right_anchor="R" * 256)
    snapshot = drafts.build_draft(state, document)  # 1,999,000 + 488 + 256 + 256.
    assert snapshot["context_authoring"] == state["context_authoring"]
    context["reference"] += "x"
    with pytest.raises(ValueError, match="total text"):
        drafts.build_draft(state, document)


def test_draft_save_reopen_and_actual_byte_limit_include_partial_context(tmp_path, monkeypatch):
    pdf = tmp_path / "source.pdf"
    pdf.write_bytes(b"generated source; not parsed by this test")
    recovery = missing_report()
    recovery["source_sha256"] = hashlib.sha256(pdf.read_bytes()).hexdigest()
    saved = tmp_path / "recovery.json"
    saved.write_text(json.dumps(recovery), encoding="utf-8")
    workspace = runtime.ReviewWorkspace(pdf, saved, tmp_path)
    state = drafts.initial_state(workspace.document)
    state["context_authoring"] = unfinished()
    payload = drafts.build_draft(state, workspace.document)
    serialized = (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    monkeypatch.setattr(runtime, "MAX_DRAFT_BYTES", len(serialized) - 1)
    with pytest.raises(runtime.ReviewDraftSizeError):
        workspace.save("draft", payload)
    assert not list(tmp_path.glob("ocr-draft-*.json"))
    monkeypatch.setattr(runtime, "MAX_DRAFT_BYTES", len(serialized))
    path = workspace.save("draft", payload)
    assert path.read_bytes() == serialized
    reopened = runtime.ReviewWorkspace(pdf, saved, tmp_path, draft_path=path)
    assert reopened.initial_draft["context_authoring"] == state["context_authoring"]
    assert reopened.draft_sha256 == hashlib.sha256(serialized).hexdigest()
    changed = json.loads(saved.read_bytes())
    changed["source_sha256"] = "f" * 64
    saved.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ValueError, match="binding"):
        runtime.ReviewWorkspace(pdf, saved, tmp_path, draft_path=path)
