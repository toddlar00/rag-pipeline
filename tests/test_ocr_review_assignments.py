"""Manual layout resolution through the real editor and versioned drafts."""

import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import ocr_docling
from ocr_review_drafts import build_draft, initial_state, restore_state
from ocr_review_runtime import ReviewWorkspace, draw_overlay
from test_ocr_docling import bbox, candidate, document, recovery
from test_ocr_review_columns import _block, _capture_queued, _configure_download_cache, _process
from test_ocr_review_columns import ui as _ui_fixture


@pytest.fixture
def workspace(tmp_path, navigation=False):
    source = tmp_path / "generated.pdf"
    source.write_bytes(b"Synthetic only; the UI renderer is replaced in these callback tests.")
    values = None
    if navigation:
        first = candidate()
        for index in (1, 3):
            first["lines"][index]["text"] = "Repeated generated Ω <line> " + "unverified text " * 12
        first["text"] = "\n".join(line["text"] for line in first["lines"])
        values = [first, candidate()]
    report = recovery(values, source=hashlib.sha256(source.read_bytes()).hexdigest())
    saved = tmp_path / "recovery.json"
    saved.write_text(json.dumps(report), encoding="utf-8")
    doc = document()
    if values is not None:
        doc["pages"]["2"] = {"page_no": 2, "size": {"width": 24, "height": 24}}
    doc["texts"][1]["prov"][0]["bbox"] = bbox((5, 25, 35, 50))
    proposals = ocr_docling.build_docling_proposals(doc, report,
        recovery_sha256=hashlib.sha256(saved.read_bytes()).hexdigest(), docling_sha256="c" * 64,
        manifest_sha256="d" * 64, effective_input_kind="original")
    proposal_path = tmp_path / "proposals.json"
    proposal_path.write_text(json.dumps(proposals), encoding="utf-8")
    return ReviewWorkspace(source, saved, tmp_path, proposals_path=proposal_path)


@pytest.fixture
def ui(tmp_path, monkeypatch):
    # Keep the one-argument workspace factory used by omission tests unchanged.
    yield from _ui_fixture.__wrapped__(workspace.__wrapped__(tmp_path, navigation=True), monkeypatch)


def snapshot(workspace, state):
    return build_draft(state, workspace.document, proposals=workspace.proposals,
                       proposals_sha256=workspace.proposals_sha256)


def restore(workspace, payload):
    return restore_state(payload, workspace.document, proposals=workspace.proposals,
                         proposals_sha256=workspace.proposals_sha256)


def complete_page(workspace):
    page = workspace.assignment_suggestion(1)
    for index in (1, 3):
        page["assignments"][index]["region_ref"] = "#/texts/1"
    return page


def test_partial_assignment_v2_draft_is_not_a_preview_or_approval(workspace):
    state = initial_state(workspace.document)
    state["assignment_pages"] = [workspace.assignment_suggestion(1)]
    draft = snapshot(workspace, state)
    assert draft["schema_version"] == 4
    loaded = restore(workspace, draft)
    assert loaded["assignment_pages"] == state["assignment_pages"]
    assert loaded["line_order"] is None
    assert loaded["assignment_preview_pages"] == []
    with pytest.raises(ValueError):
        workspace.assignment_review(state["assignment_pages"], [1])
    draft["assignment_preview_pages"] = [1]
    with pytest.raises(ValueError, match="cannot be reproduced"):
        restore(workspace, draft)


def test_complete_assignments_rebuild_order_and_require_matching_proposals(workspace):
    state = initial_state(workspace.document)
    state.update(assignment_pages=[complete_page(workspace)], assignment_preview_pages=[1])
    state["line_order"] = [999]  # Never trusted or persisted.
    draft = snapshot(workspace, state)
    loaded = restore(workspace, draft)
    assert loaded["line_order"] == [0, 1, 3, 2, 4, 5]
    assert "highlighted_line" not in draft
    assert "confirmed" not in loaded
    with pytest.raises(ValueError):
        restore_state(draft, workspace.document)
    path = workspace.save("assignment", workspace.assignment_review(state["assignment_pages"], [1]))
    review = json.loads(path.read_text(encoding="utf-8"))
    assert review["pages"][0]["line_order"] == loaded["line_order"]
    assert review["accuracy_verified"] is False
    assert review["canonical_extraction_modified"] is False


def test_v1_draft_loads_without_inventing_assignments(workspace):
    draft = snapshot(workspace, initial_state(workspace.document))
    draft["schema_version"] = 1
    del draft["context_authoring"]
    del draft["assignment_pages"], draft["assignment_preview_pages"], draft["omission_decisions"]
    loaded = restore(workspace, draft)
    assert loaded["assignment_pages"] == loaded["assignment_preview_pages"] == []
    upgraded = snapshot(workspace, loaded)
    assert upgraded["schema_version"] == 4
    assert upgraded["references"] == draft["references"]
    # Exact old schema is retained: no silently accepted new fields/actions.
    draft["assignment_pages"] = []
    with pytest.raises(ValueError):
        restore(workspace, draft)


@pytest.mark.parametrize("key,value", [("assignment_pages", None), ("assignment_pages", [{}] * 21),
    ("assignment_preview_pages", [True]), ("assignment_preview_pages", [1, 1]),
    ("assignment_preview_pages", [2]), ("assignment_preview_pages", [1] * 21)])
def test_assignment_draft_bounds(workspace, key, value):
    draft = snapshot(workspace, initial_state(workspace.document))
    draft[key] = value
    with pytest.raises(ValueError):
        restore(workspace, draft)


def test_competing_assignment_and_column_previews_rejected(workspace):
    state = initial_state(workspace.document)
    state.update(assignment_pages=[complete_page(workspace)], assignment_preview_pages=[1])
    state["layouts"] = [workspace.document.layout_page(1, [0, .2, 1, .6], [.45, .2, .55, .6])]
    with pytest.raises(ValueError, match="competing"):
        snapshot(workspace, state)


def test_assignment_publication_rechecks_fixed_inputs(workspace, monkeypatch):
    import ocr_review_runtime

    review = workspace.assignment_review([complete_page(workspace)], [1])
    writer = ocr_review_runtime.storage_policy.atomic_write_private_json
    def mutate(path, value, **kwargs):
        workspace.proposals_path.write_bytes(b"changed at staging")
        return writer(path, value, **kwargs)
    monkeypatch.setattr(ocr_review_runtime.storage_policy, "atomic_write_private_json", mutate)
    with pytest.raises(RuntimeError, match="changed"):
        workspace.save("assignment", review)
    assert not list(workspace.output_dir.glob("ocr-assignment-*.json"))


def test_draft_encoded_limit_matches_actual_restart_bytes(workspace, monkeypatch):
    import ocr_review_runtime

    state = initial_state(workspace.document)
    state.update(assignment_pages=[complete_page(workspace)], assignment_preview_pages=[1])
    state["reference_drafts"] = [{"page_number": 1, "text": "\0" * 200 + "é😀"}]
    draft = snapshot(workspace, state)
    encoded = (json.dumps(draft, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    monkeypatch.setattr(ocr_review_runtime, "MAX_DRAFT_BYTES", len(encoded) - 1)
    with pytest.raises(ocr_review_runtime.ReviewDraftSizeError):
        workspace.save("draft", draft)
    assert not list(workspace.output_dir.glob("ocr-draft-*.json"))
    monkeypatch.setattr(ocr_review_runtime, "MAX_DRAFT_BYTES", len(encoded))
    path = workspace.save("draft", draft)
    assert path.read_bytes() == encoded
    loaded = ReviewWorkspace(workspace.pdf_path, workspace.recovery_path, workspace.output_dir,
                             draft_path=path, proposals_path=workspace.proposals_path)
    assert loaded.initial_draft["line_order"] == [0, 1, 3, 2, 4, 5]


@pytest.mark.parametrize("index", [True, -1, 6, "1"])
def test_highlight_requires_real_original_line(workspace, index):
    Image = pytest.importorskip("PIL.Image")
    with pytest.raises(ValueError, match="highlighted"):
        draw_overlay(Image.new("RGB", (100, 100)), workspace.document.page(1)["candidate"], highlighted_line=index)


def test_real_gradio_partial_resolution_preview_draft_and_export(workspace, monkeypatch):
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
        _, state, _, _, _ = funcs["start_assignments"](state)
        _, unchanged, _, _, notice = funcs["preview_assignments"](state)
        assert "Unresolved" in notice and unchanged["assignment_preview_pages"] == []
        _, state, target = funcs["focus_assignment"](1, state, state["annotation_context"])
        assert state["highlighted_line"] == 1 and target["value"] == "__unresolved__"
        for index in (1, 3):
            _, state, _, _, _ = funcs["assign_line"](index, "#/texts/1", state)
        _, state, proposed, _, _ = funcs["preview_assignments"](state)
        assert state["assignment_preview_pages"] == [1]
        assert state["line_order"] == [0, 1, 3, 2, 4, 5]
        assert proposed.splitlines()[2].endswith("3")
        handler = next(block.fn for block in app.fns.values() if block.fn is not None and any(
            getattr(component, "label", None) == "I reviewed every previewed assignment page against the scan"
            for component in block.inputs))
        with pytest.raises(Exception, match="Review action failed"):
            handler(False, state, state["column_context"])
        path, _ = handler(True, state, state["column_context"])
        assert Path(path).exists()
        saved_state, draft_path, _ = funcs["save_draft"]("Unconfirmed", state, state["annotation_context"])
        loaded = ReviewWorkspace(workspace.pdf_path, workspace.recovery_path, workspace.output_dir,
                                 draft_path=draft_path, proposals_path=workspace.proposals_path)
        assert loaded.initial_draft["line_order"] == state["line_order"]
        assert loaded.initial_draft["references"] == []
        assert funcs["change_page"](1, state, "Unconfirmed", state["annotation_context"])[7]["line_order"] == state["line_order"]
        earlier = next(block.fn for block in app.fns.values() if block.fn is not None
                       and any(getattr(c, "label", None) == "Body regions in reading order" for c in block.inputs)
                       and block.fn.__name__ == "<lambda>")
        _, moved, _, _, _ = earlier("#/texts/2", state)
        assert moved["assignment_preview_pages"] == []
        assert moved["assignment_pages"][0]["body_region_order"] == ["#/texts/0", "#/texts/2", "#/texts/1", "#/tables/0"]
        _, moved, _, _, _ = funcs["preview_assignments"](moved)
        assert moved["line_order"] == [0, 2, 4, 1, 3, 5]
        assert restore(workspace, snapshot(workspace, moved))["line_order"] == moved["line_order"]
        image, focused = funcs["focus_assignment_region"]("#/texts/2", moved, moved["annotation_context"])
        assert focused["assignment_region_ref"] == "#/texts/2" and image.size == (100, 100)
        with pytest.raises(Exception, match="Review action failed"):
            earlier("#/texts/0", state)
        before = copy.deepcopy(saved_state)
        with pytest.raises(Exception, match="Review action failed"):
            funcs["assign_line"](1, "SYNTHETIC_UNKNOWN_REGION", saved_state)
        assert saved_state == before
        _, state, _, _, _ = funcs["assign_line"](1, "__unresolved__", state)
        assert state["assignment_preview_pages"] == [] and state["line_order"] is None
        with pytest.raises(Exception, match="Review action failed"):
            handler(True, state, state["column_context"])
        workspace.proposals_path.write_bytes(b"changed after cached preview")
        for action in (lambda: funcs["assignment_panels"](state),
                       lambda: funcs["focus_assignment"](1, state, state["annotation_context"]),
                       lambda: funcs["assign_line"](1, "__retain__", state),
                       lambda: funcs["preview_assignments"](state)):
            with pytest.raises(Exception, match="Review action failed"):
                action()
    finally:
        app.close()


def test_next_unresolved_uses_original_occurrences_wraps_and_preserves_draft(ui, monkeypatch):
    import ocr_review_ui

    state = ui.functions["start_assignments"](ui.state)[1]
    original = copy.deepcopy(state)
    before = snapshot(ui.workspace, state)
    candidate_before = ui.workspace.document.page(1)["candidate"]
    files = {path.name: path.read_bytes() for path in ui.workspace.output_dir.iterdir() if path.is_file()}
    rotations = []

    def token():
        rotations.append(len(rotations) + 1)
        return SimpleNamespace(hex=f"generated-navigation-{len(rotations)}")

    monkeypatch.setattr(ocr_review_ui, "uuid4", token)
    for turn, expected in enumerate((1, 3, 1), 1):
        _, state, target, line, notice = ui.functions["next_unresolved_assignment"](state, state["annotation_context"])
        assert state["highlighted_line"] == line["value"] == expected
        assert target["value"] == "__unresolved__" and state["assignment_region_ref"] is None
        assert ui.overlays[-1]["highlighted_line"] == expected and ui.overlays[-1]["region_outline"] is None
        assert ("Wrapped to the first unresolved line." in notice) == (turn == 3)
        selected = candidate_before["lines"][expected]["text"]
        assert len(selected) > 96 and selected == candidate_before["lines"][1]["text"] == candidate_before["lines"][3]["text"]
        assert ui.functions["sync_annotation_context"](state) == (state["annotation_context"], False, False, False, False, selected)
        assert rotations == list(range(1, turn + 1))  # Sync adds no second rotation.
        assert snapshot(ui.workspace, state) == before
    assert original["highlighted_line"] is None
    assert ui.workspace.document.page(1)["candidate"] == candidate_before
    assert {path.name: path.read_bytes() for path in ui.workspace.output_dir.iterdir() if path.is_file()} == files


def test_navigation_requires_started_page_and_never_marks_completion_reviewed(ui):
    _, state, target, line, notice = ui.functions["next_unresolved_assignment"](ui.state, ui.state["annotation_context"])
    assert "Start line assignments" in notice and state["assignment_pages"] == []
    assert line["value"] is target["value"] is state["highlighted_line"] is None
    state = ui.functions["start_assignments"](state)[1]
    for index, assignment in ((1, "#/texts/1"), (3, "__retain__")):
        state = ui.functions["assign_line"](index, assignment, state)[1]
        _, state, target = ui.functions["focus_assignment"](index, state, state["annotation_context"])
        assert target["value"] == assignment
        expected_region = "#/texts/1" if index == 1 else None
        assert state["assignment_region_ref"] == expected_region
        assert (ui.overlays[-1]["region_outline"] is None) == (expected_region is None)
        panels = ui.functions["assignment_panels"](state)
        assert panels[0]["value"] == index and panels[1]["value"] == assignment
    before = snapshot(ui.workspace, state)
    _, state, target, line, notice = ui.functions["next_unresolved_assignment"](state, state["annotation_context"])
    assert "No unresolved line assignments" in notice and "Preview and explicit review are still required" in notice
    assert line["value"] is target["value"] is state["highlighted_line"] is state["assignment_region_ref"] is None
    assert ui.functions["sync_annotation_context"](state) == (state["annotation_context"], False, False, False, False, "")
    assert snapshot(ui.workspace, state) == before
    assert state["assignment_preview_pages"] == [] and state["line_order"] is None


async def _start_navigation_session(ui, session_hash):
    session = ui.app.state_holder[session_hash]
    block = _block(ui, "start_assignments")
    state_id = block.inputs[0]._id
    session[state_id] = copy.deepcopy(ui.state)
    for expected in ("start_assignments", "assignment_panels", "assignment_order_panel",
                     "sync_column_context", "sync_annotation_context"):
        assert block.fn.__name__ == expected
        response = await _process(ui, block, [None], session, session_hash)
        if expected == "assignment_panels":
            assert [value for _, value in response["data"][0]["choices"]] == list(range(6))
        key = next(key for key, value in ui.app.fns.items() if value is block)
        children = [item for item in ui.app.config["dependencies"] if item["trigger_after"] == key]
        if expected != "sync_annotation_context":
            assert len(children) == 1 and children[0]["trigger_only_on_success"] is True
            block = ui.app.fns[children[0]["id"]]
        else:
            assert not children
    initial = copy.deepcopy(session[state_id])
    assert response["data"] == [initial["annotation_context"], False, False, False, False, ""]
    return session, state_id, initial


@pytest.mark.parametrize("transition,synchronize", [("page", False), ("page", True), ("focus", False), ("focus", True)])
def test_queued_navigation_rejects_captured_previous_context_without_mutation(ui, transition, synchronize):
    async def scenario():
        actions = [("next_unresolved_assignment", []), ("focus_assignment", [3]),
                   ("focus_assignment_region", ["#/texts/2"])]
        if transition == "page":
            # These inputs remain valid after page refresh, so the handler must reject the old token.
            actions += [("focus_assignment", [None]), ("focus_assignment_region", ["__retain__"])]
        for attempt, (action, captured) in enumerate(actions):
            session_hash = f"generated-navigation-{action}-{attempt}"
            session, state_id, initial = await _start_navigation_session(ui, session_hash)
            token = initial["annotation_context"]
            prior = (_block(ui, "change_page"), [2, None, "", token]) if transition == "page" else (
                _block(ui, "focus_assignment"), [1, None, token])
            events = [await _capture_queued(ui, block, inputs, session_hash=session_hash)
                      for block, inputs in (prior, (_block(ui, action), [*captured, None, token]))]
            await _process(ui, events[0].fn, events[0].data.data, session, session_hash)
            if transition == "page":
                for name in ("assignment_panels", "assignment_order_panel", "omission_panels", "scan_panels", "sync_column_context"):
                    refreshed = await _process(ui, _block(ui, name), [None], session, session_hash)
                    if name == "assignment_panels":
                        assert refreshed["data"][0]["choices"] == []
                        assert [value for _, value in refreshed["data"][1]["choices"]] == ["__unresolved__", "__retain__"]
            current = copy.deepcopy(session[state_id])
            assert current["annotation_context"] != token
            assert current["page"] == (2 if transition == "page" else 1)
            if synchronize:
                sync = await _process(ui, _block(ui, "sync_annotation_context"), [None], session, session_hash)
                selected = "" if transition == "page" else ui.workspace.document.page(1)["candidate"]["lines"][1]["text"]
                assert sync["data"] == [current["annotation_context"], False, False, False, False, selected]
            render_count, overlay_count = len(ui.renders), len(ui.overlays)
            files = set(ui.workspace.output_dir.iterdir())
            # Removed choices fail in Gradio; valid shared choices must reach the stale-context guard.
            message = "not in the list of choices" if transition == "page" and captured in ([3], ["#/texts/2"]) else "annotation view changed"
            with pytest.raises(Exception, match=message):
                await _process(ui, events[1].fn, events[1].data.data, session, session_hash)
            assert session[state_id] == current
            assert (len(ui.renders), len(ui.overlays)) == (render_count, overlay_count)
            assert set(ui.workspace.output_dir.iterdir()) == files
    asyncio.run(scenario())


def test_navigation_wiring_is_private_user_input_then_one_atomic_success_sync(ui):
    dependencies = ui.app.config["dependencies"]
    navigation = _block(ui, "next_unresolved_assignment")
    for key, block in ui.app.fns.items():
        if block.fn is None or block.fn.__name__ not in {"next_unresolved_assignment", "focus_assignment", "focus_assignment_region"}:
            continue
        dependency = next(item for item in dependencies if item["id"] == key)
        assert {target[1] for target in dependency["targets"]} == ({"click"} if block is navigation else {"input"})
        assert dependency["api_visibility"] == "private" and dependency["trigger_after"] is None
        assert block.concurrency_limit == 1 and block.concurrency_id == "ocr-review"
        assert block.inputs[-1].label == "Annotation view context"
        children = [item for item in dependencies if item["trigger_after"] == key]
        assert len(children) == 1 and children[0]["trigger_only_on_success"] is True
        sync = ui.app.fns[children[0]["id"]]
        assert sync.fn.__name__ == "sync_annotation_context"
        assert [component.label for component in sync.outputs] == ["Annotation view context",
            "I checked every line against the scan", "Export the stored, reviewed references",
            "I reviewed these crops against the scan", "I reviewed these omission assessments against the scan",
            "Selected original OCR line (unverified)"]
        assert sync.outputs[-1].interactive is False
        for bad in (None, "", "generated-stale-token"):
            args = [] if block is navigation else [1 if block.fn.__name__ == "focus_assignment" else "#/texts/1"]
            with pytest.raises(Exception, match="annotation view changed"):
                block.fn(*args, ui.state, bad)
    assert navigation.outputs[2].label == "Assign to saved region or retain slot"
    assert navigation.outputs[3].label == "Original OCR line to inspect"

    async def scenario():
        session_hash = "generated-current-navigation"
        session, state_id, initial = await _start_navigation_session(ui, session_hash)
        event = await _capture_queued(ui, navigation, [None, initial["annotation_context"]], session_hash=session_hash)
        response = await _process(ui, event.fn, event.data.data, session, session_hash)
        current = copy.deepcopy(session[state_id])
        assert current["highlighted_line"] == response["data"][3]["value"] == 1
        assert response["data"][2]["value"] == "__unresolved__"
        sync = await _process(ui, _block(ui, "sync_annotation_context"), [None], session, session_hash)
        selected = ui.workspace.document.page(1)["candidate"]["lines"][1]["text"]
        assert sync["data"] == [current["annotation_context"], False, False, False, False, selected]
        assert current == session[state_id] and current["annotation_context"] != initial["annotation_context"]
    asyncio.run(scenario())
