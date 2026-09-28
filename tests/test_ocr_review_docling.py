"""Fixed Docling proposals through local review, drafts, and bound exports."""

import copy
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import ocr_docling
from ocr_review_drafts import build_draft, initial_state, restore_state
from ocr_review_runtime import ReviewWorkspace
from test_ocr_docling import document, recovery
from test_ocr_review_columns import _configure_download_cache


@pytest.fixture
def proposal_workspace(tmp_path):
    source = tmp_path / "synthetic-source.pdf"
    source.write_bytes(b"Generated input; these tests replace only the display renderer.")
    report = recovery(source=hashlib.sha256(source.read_bytes()).hexdigest())
    report_path = tmp_path / "recovery.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    digest = hashlib.sha256(report_path.read_bytes()).hexdigest()
    proposals = ocr_docling.build_docling_proposals(document(), report, recovery_sha256=digest,
                                                   docling_sha256="c" * 64, manifest_sha256="d" * 64,
                                                   effective_input_kind="original")
    proposal_path = tmp_path / "proposals.json"
    proposal_path.write_text(json.dumps(proposals), encoding="utf-8")
    return ReviewWorkspace(source, report_path, tmp_path, proposals_path=proposal_path)


def test_fixed_proposal_binding_and_review_exports(proposal_workspace):
    workspace = proposal_workspace
    assert workspace.proposals_sha256 == hashlib.sha256(workspace.proposals_path.read_bytes()).hexdigest()
    page = workspace.proposal_page(1)
    page["line_order"].clear()
    assert workspace.proposal_page(1)["line_order"] == [0, 1, 3, 2, 4, 5]
    review = workspace.docling_review([1])
    path = workspace.save("docling", review)
    assert json.loads(path.read_text(encoding="utf-8")) == review
    assert review["accuracy_verified"] is False
    assert review["canonical_extraction_modified"] is False


def test_proposals_reverified_at_final_publication(proposal_workspace, monkeypatch):
    import ocr_review_runtime

    workspace = proposal_workspace
    writer = ocr_review_runtime.storage_policy.atomic_write_private_json

    def mutate(path, payload, **kwargs):
        workspace.proposals_path.write_bytes(b"changed during staging")
        return writer(path, payload, **kwargs)

    monkeypatch.setattr(ocr_review_runtime.storage_policy, "atomic_write_private_json", mutate)
    with pytest.raises(RuntimeError, match="changed"):
        workspace.save("docling", workspace.docling_review([1]))
    assert not list(workspace.output_dir.glob("ocr-docling-*.json"))


def test_docling_draft_requires_exact_proposal_input_to_restore(proposal_workspace):
    workspace = proposal_workspace
    state = initial_state(workspace.document)
    state["docling_pages"] = [1]
    snapshot = build_draft(state, workspace.document, proposals=workspace.proposals,
                           proposals_sha256=workspace.proposals_sha256)
    path = workspace.save("draft", snapshot)
    with pytest.raises(ValueError, match="proposals differ"):
        ReviewWorkspace(workspace.pdf_path, workspace.recovery_path, workspace.output_dir, draft_path=path)
    loaded = ReviewWorkspace(workspace.pdf_path, workspace.recovery_path, workspace.output_dir,
                             draft_path=path, proposals_path=workspace.proposals_path)
    assert loaded.initial_draft["docling_pages"] == [1]
    assert loaded.initial_draft["line_order"] == [0, 1, 3, 2, 4, 5]
    assert "confirmed" not in loaded.initial_draft
    snapshot["proposals_sha256"] = "f" * 64
    with pytest.raises(ValueError, match="proposals differ"):
        restore_state(snapshot, workspace.document, proposals=workspace.proposals,
                       proposals_sha256=workspace.proposals_sha256)


@pytest.mark.parametrize("pages", [[True], [1, 1], [2], ["1"], [1] * 21])
def test_docling_draft_rejects_invalid_page_selections(proposal_workspace, pages):
    workspace = proposal_workspace
    state = initial_state(workspace.document)
    state["docling_pages"] = pages
    with pytest.raises(ValueError):
        build_draft(state, workspace.document, proposals=workspace.proposals,
                    proposals_sha256=workspace.proposals_sha256)


def test_competing_column_and_docling_orders_cannot_be_saved(proposal_workspace):
    workspace = proposal_workspace
    state = initial_state(workspace.document)
    state["docling_pages"] = [1]
    state["layouts"] = [workspace.document.layout_page(1, [0, .2, 1, .6], [.45, .2, .55, .6])]
    with pytest.raises(ValueError, match="competing"):
        build_draft(state, workspace.document, proposals=workspace.proposals,
                    proposals_sha256=workspace.proposals_sha256)


def test_real_gradio_proposal_preview_crop_draft_and_confirmed_export(proposal_workspace, monkeypatch):
    os.environ["GRADIO_ANALYTICS_ENABLED"] = "False"
    pytest.importorskip("gradio")
    Image = pytest.importorskip("PIL.Image")
    from ocr_review_ui import build_app

    workspace = proposal_workspace
    workspace.render = lambda _: Image.new("RGB", (100, 100), "white")
    _configure_download_cache(workspace, monkeypatch)
    app = build_app(workspace)
    try:
        funcs = {block.fn.__name__: block.fn for block in app.fns.values() if block.fn is not None}
        state = copy.deepcopy(next(block.value for block in app.blocks.values() if block.__class__.__name__ == "State"))
        _, state, confirmed, message = funcs["select_proposal_region"]("#/tables/0:cell-0", state, state["annotation_context"])
        assert state["selections"]["crop"] == pytest.approx([.05, .7, .95, .9])
        assert confirmed is False
        assert "Inspect" in message
        state, _, _, _ = funcs["add_region"](state, state["annotation_context"])
        assert state["regions"][0]["bbox"] == pytest.approx([.05, .7, .95, .9])
        _, state, proposed, diff, confirmed, column_confirmed, _ = funcs["preview_docling"](state)
        assert state["line_order"] == [0, 1, 3, 2, 4, 5]
        assert proposed.splitlines()[2].endswith("3")
        assert "--- original" in diff
        assert confirmed is False and column_confirmed is False
        returned = funcs["change_page"](1, state, "", state["annotation_context"])
        assert returned[7]["line_order"] == [0, 1, 3, 2, 4, 5]
        assert returned[-1] is False
        handler = next(block.fn for block in app.fns.values() if block.fn is not None
                       and any(getattr(component, "label", None) == "I reviewed all stored Docling page orders against the scan"
                               for component in block.inputs))
        with pytest.raises(Exception, match="Review action failed"):
            handler(False, state, state["column_context"])
        path, _ = handler(True, state, state["column_context"])
        assert json.loads(Path(path).read_text(encoding="utf-8"))["kind"] == "ocr_docling_review"
        state, draft_path, _ = funcs["save_draft"]("Unconfirmed text", state, state["annotation_context"])
        restored = ReviewWorkspace(workspace.pdf_path, workspace.recovery_path, workspace.output_dir,
                                    draft_path=draft_path, proposals_path=workspace.proposals_path)
        assert restored.initial_draft["line_order"] == state["line_order"]
        assert restored.initial_draft["references"] == []
        _, state, _, _ = funcs["select_rectangle"](state, "body", SimpleNamespace(index=[10, 20]), state["annotation_context"])
        assert state["docling_pages"] == []
        assert state["line_order"] is None
        assert funcs["refresh_panels"](state)[1] == "No text differences."
        with pytest.raises(Exception, match="Review action failed"):
            funcs["select_proposal_region"]("SYNTHETIC_SECRET", state, state["annotation_context"])
        workspace.proposals_path.write_bytes(b"changed after image was cached")
        for action in (lambda: funcs["preview_docling"](state),
                       lambda: funcs["select_proposal_region"]("#/texts/0", state, state["annotation_context"]),
                       lambda: funcs["change_page"](1, state, view_context=state["annotation_context"]),
                       lambda: funcs["refresh_panels"](state)):
            with pytest.raises(Exception, match="Review action failed"):
                action()
    finally:
        app.close()
