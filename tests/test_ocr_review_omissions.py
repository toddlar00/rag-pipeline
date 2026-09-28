"""Omission assessments remain partial, source-bound, and distinct from accuracy."""

import copy
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from ocr_review_drafts import build_draft, initial_state, record_event, restore_state
from ocr_review_runtime import ReviewWorkspace
from test_ocr_review_assignments import workspace as _workspace_fixture
from test_ocr_review_columns import _configure_download_cache


@pytest.fixture
def workspace(tmp_path):
    return _workspace_fixture.__wrapped__(tmp_path)


def draft(workspace, state):
    return build_draft(state, workspace.document, proposals=workspace.proposals,
                       proposals_sha256=workspace.proposals_sha256)


def restore(workspace, payload):
    return restore_state(payload, workspace.document, proposals=workspace.proposals,
                         proposals_sha256=workspace.proposals_sha256)


def decision(ref="#/texts/1", value="suspected_missing_text"):
    return {"page_number": 1, "region_ref": ref, "decision": value}


def test_v3_preserves_assessments_not_diagnostics_or_ephemeral_state(workspace):
    state = initial_state(workspace.document)
    state.update(omission_decisions=[decision()], omission_region_ref="#/texts/1")
    record_event(state, "omission_assessed")
    payload = draft(workspace, state)
    assert payload["schema_version"] == 4
    assert payload["omission_decisions"] == [decision()]
    assert "diagnostics" not in payload and "omission_region_ref" not in payload
    path = workspace.save("draft", payload)
    loaded = ReviewWorkspace(workspace.pdf_path, workspace.recovery_path, workspace.output_dir,
                             draft_path=path, proposals_path=workspace.proposals_path)
    assert loaded.initial_draft["omission_decisions"] == [decision()]
    assert loaded.initial_draft.get("omission_region_ref") is None
    assert "confirmed" not in loaded.initial_draft
    assert loaded.omission_diagnostics() == workspace.omission_diagnostics()


@pytest.mark.parametrize("version", [1, 2])
def test_old_drafts_keep_exact_schemas_and_upgrade_without_assessments(workspace, version):
    payload = draft(workspace, initial_state(workspace.document))
    payload["schema_version"] = version
    del payload["context_authoring"]
    del payload["omission_decisions"]
    if version == 1:
        del payload["assignment_pages"], payload["assignment_preview_pages"]
    state = restore(workspace, payload)
    assert state["omission_decisions"] == []
    assert draft(workspace, state)["schema_version"] == 4
    payload["omission_decisions"] = []
    with pytest.raises(ValueError):
        restore(workspace, payload)
    del payload["omission_decisions"]
    payload.update(event_count=1, history=[{"sequence": 1, "page_number": 1, "action": "omission_assessed"}])
    with pytest.raises(ValueError, match="history"):
        restore(workspace, payload)


@pytest.mark.parametrize("decisions", [None, [decision("unknown")], [decision(), decision()],
                                      [decision(value="accurate")], [decision()] * 10_001])
def test_invalid_draft_assessments_are_rejected(workspace, decisions):
    state = initial_state(workspace.document)
    state["omission_decisions"] = decisions
    with pytest.raises(ValueError):
        draft(workspace, state)


def test_assessments_require_fixed_proposals(workspace):
    state = initial_state(workspace.document)
    state["omission_decisions"] = [decision()]
    with pytest.raises(ValueError, match="bound proposals"):
        build_draft(state, workspace.document)


def test_partial_review_retains_original_warnings_and_cannot_claim_accuracy(workspace):
    diagnostics = workspace.omission_diagnostics()
    with pytest.raises(ValueError, match="confirmation"):
        workspace.omission_review([decision()], confirmed=False)
    review = workspace.omission_review([decision(value="false_alarm")], confirmed=True)
    assert review["diagnostics"] == diagnostics
    assert review["summary"]["recorded_decisions"] == 1
    assert review["summary"]["unresolved_regions"] == diagnostics["summary"]["saved_regions"] - 1
    assert review["accuracy_verified"] is False
    assert review["full_page_coverage_verified"] is False
    assert review["canonical_extraction_modified"] is False
    for kind, value in (("omission-diagnostics", diagnostics), ("omission-review", review)):
        first = workspace.save(kind, value)
        second = workspace.save(kind, value)
        assert first != second
        assert json.loads(first.read_text(encoding="utf-8")) == value
        forged = copy.deepcopy(value)
        forged["accuracy_verified"] = True
        with pytest.raises(ValueError):
            workspace.save(kind, forged)


@pytest.mark.parametrize("kind", ["omission-diagnostics", "omission-review"])
def test_final_publication_rechecks_inputs(workspace, monkeypatch, kind):
    import ocr_review_runtime

    value = (workspace.omission_diagnostics() if kind == "omission-diagnostics" else
             workspace.omission_review([decision()], confirmed=True))
    writer = ocr_review_runtime.storage_policy.atomic_write_private_json

    def mutate(path, payload, **kwargs):
        workspace.proposals_path.write_bytes(b"Generated mismatch during staging")
        return writer(path, payload, **kwargs)

    monkeypatch.setattr(ocr_review_runtime.storage_policy, "atomic_write_private_json", mutate)
    with pytest.raises(RuntimeError, match="changed"):
        workspace.save(kind, value)
    assert not list(workspace.output_dir.glob(f"ocr-{kind}-*.json"))


def test_real_gradio_assessment_crop_reset_draft_and_export(workspace, monkeypatch):
    os.environ["GRADIO_ANALYTICS_ENABLED"] = "False"
    pytest.importorskip("gradio")
    Image = pytest.importorskip("PIL.Image")
    from ocr_review_ui import build_app

    workspace.render = lambda _: Image.new("RGB", (100, 100), "white")
    _configure_download_cache(workspace, monkeypatch)
    app = build_app(workspace)
    try:
        funcs = {block.fn.__name__: block.fn for block in app.fns.values() if block.fn is not None}
        state = copy.deepcopy(next(block.value for block in app.blocks.values() if block.__class__.__name__ == "State"))
        choices, notice, selected, confirmed = funcs["omission_panels"](state)
        assert choices["choices"] and "saved regions only" in notice
        assert selected["value"] is None and confirmed is False
        _, state, choice, confirmed, notice = funcs["focus_omission"]("#/texts/1", state, state["annotation_context"])
        assert state["omission_region_ref"] == "#/texts/1" and "Orange" in notice
        assert choice["value"] is None and confirmed is False
        before = copy.deepcopy(state)
        for ref, value in (("unknown", "not_text"), ("#/texts/1", "accurate"), ("#/texts/1", None)):
            with pytest.raises(Exception, match="Review action failed"):
                funcs["assess_omission"](ref, value, state, state["annotation_context"])
            assert state == before
        state, confirmed, _ = funcs["assess_omission"]("#/texts/1", "suspected_missing_text", state, state["annotation_context"])
        assert state["omission_decisions"] == [decision()] and confirmed is False
        assert "suspected_missing_text" in funcs["omission_panels"](state)[0]["choices"][1][0]
        review_handler = next(block.fn for block in app.fns.values() if block.fn is not None and any(
            getattr(c, "label", None) == "I reviewed these omission assessments against the scan" for c in block.inputs))
        for value in (False, 1, "true", None):
            with pytest.raises(Exception, match="Review action failed"):
                review_handler(value, state, state["annotation_context"])
        path, _ = review_handler(True, state, state["annotation_context"])
        assert json.loads(Path(path).read_text(encoding="utf-8"))["decisions"] == [decision()]
        path, notice = funcs["export_omission_diagnostics"]()
        assert "no review or accuracy approval" in notice
        assert json.loads(Path(path).read_text(encoding="utf-8"))["kind"] == "ocr_omission_diagnostics"
        _, state, _, _ = funcs["select_proposal_region"]("#/texts/1", state, state["annotation_context"])
        assert state["selections"]["crop"] == pytest.approx([.05, .25, .35, .5])
        assert state["omission_region_ref"] is None
        saved_state, path, _ = funcs["save_draft"]("Unconfirmed generated text", state, state["annotation_context"])
        restored = ReviewWorkspace(workspace.pdf_path, workspace.recovery_path, workspace.output_dir,
                                  draft_path=path, proposals_path=workspace.proposals_path)
        assert restored.initial_draft["omission_decisions"] == [decision()]
        _, state, _, _, _ = funcs["focus_omission"]("#/texts/1", state, state["annotation_context"])
        state, confirmed, _ = funcs["assess_omission"]("#/texts/1", None, state, state["annotation_context"], reset=True)
        assert state["omission_decisions"] == [] and confirmed is False
        assert state["history"][-1]["action"] == "omission_reset"
        assert saved_state["omission_decisions"] == [decision()]
        workspace.proposals_path.write_bytes(b"Changed generated input after preview")
        for action in (lambda: funcs["omission_panels"](state),
                       lambda: funcs["focus_omission"]("#/texts/1", state, state["annotation_context"]),
                       lambda: funcs["assess_omission"]("#/texts/1", "not_text", state, state["annotation_context"]),
                       funcs["export_omission_diagnostics"]):
            with pytest.raises(Exception, match="Review action failed"):
                action()
    finally:
        app.close()


def test_over_limit_overlay_abstains_without_hiding_the_scan_or_text(monkeypatch):
    os.environ["GRADIO_ANALYTICS_ENABLED"] = "False"
    pytest.importorskip("gradio")
    Image = pytest.importorskip("PIL.Image")
    import ocr_review_ui
    from ocr_review import ReviewDocument
    from test_ocr_review import report

    value = report()
    candidate = value["pages"][0]["candidate"]
    candidate["lines"] = [copy.deepcopy(candidate["lines"][0]) for _ in range(2001)]
    candidate["text"] = "\n".join(line["text"] for line in candidate["lines"])
    document = ReviewDocument(value, recovery_sha256="b" * 64)
    workspace = SimpleNamespace(document=document, render=lambda _: Image.new("RGB", (100, 100), "white"))
    draw = ocr_review_ui.draw_overlay
    observed = []

    def overlay(image, candidate, **options):
        observed.append(candidate)
        return draw(image, candidate, **options)

    monkeypatch.setattr(ocr_review_ui, "draw_overlay", overlay)
    app = ocr_review_ui.build_app(workspace)
    try:
        funcs = {block.fn.__name__: block.fn for block in app.fns.values() if block.fn is not None}
        state = copy.deepcopy(next(block.value for block in app.blocks.values() if block.__class__.__name__ == "State"))
        image, original, _, _, _, _, _, state, notice, *_ = funcs["change_page"](1, state, view_context=state["annotation_context"])
        assert image.size == (100, 100)
        assert original == candidate["text"]
        assert "more than 2,000 lines" in notice and "No partial overlay" in notice
        assert observed and all(item is None for item in observed)
        assert "completeness is unknown" in funcs["omission_panels"](state)[1]
        assert "more than 2,000 lines" in funcs["focus_omission"](None, state, state["annotation_context"])[-1]
    finally:
        app.close()
