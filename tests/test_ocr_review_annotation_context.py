"""Generated-only annotation consent and installed Gradio queue regressions.

Only rendering is stubbed; fixed source/recovery/proposal validation and private
publication are real. Captured requests are executed in queue capture order.
These checks are not proof of browser timing or authenticated human review.
"""
from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ocr_review_runtime import ReviewWorkspace
from test_ocr_review_columns import _block, _capture_queued, _process
from test_ocr_review_columns import ui as ui
from test_ocr_review_columns import workspace as workspace


LABELS = {
    "references": "Export the stored, reviewed references",
    "regions": "I reviewed these crops against the scan",
    "omission-review": "I reviewed these omission assessments against the scan",
}


def _export(ui, kind):
    return next(block for block in ui.app.fns.values() if block.fn is not None
                and any(getattr(component, "label", None) == LABELS[kind] for component in block.inputs))


def _button(ui, label):
    identifier = next(block._id for block in ui.app.blocks.values()
                      if type(block).__name__ == "Button" and block.value == label)
    dependency = next(item for item in ui.app.config["dependencies"]
                      if (identifier, "click") in map(tuple, item["targets"]))
    return ui.app.fns[dependency["id"]]


def _input(ui, label):
    identifier = next(block._id for block in ui.app.blocks.values() if getattr(block, "label", None) == label)
    dependency = next(item for item in ui.app.config["dependencies"]
                      if (identifier, "input") in map(tuple, item["targets"]))
    return ui.app.fns[dependency["id"]]


def _focus(ui, state, ref="#/texts/1"):
    return ui.functions["focus_omission"](ref, state, state["annotation_context"])[1]


def _crop(ui, state, ref="#/texts/1"):
    return ui.functions["select_proposal_region"](ref, state, state["annotation_context"])[1]


def _ready(ui, kind):
    state = copy.deepcopy(ui.state)
    if kind == "references":
        return ui.functions["add_reference"]("Synthetic original reference.", True, state, state["annotation_context"])[0]
    if kind == "regions":
        state = _crop(ui, state)
        return ui.functions["add_region"](state, state["annotation_context"])[0]
    state = _focus(ui, state)
    return ui.functions["assess_omission"]("#/texts/1", "false_alarm", state, state["annotation_context"])[0]


async def _replay(ui, initial, preceding, stale, *, synchronize):
    """Both requests capture the original token, before the first executes."""
    session_hash = "generated-annotation-context"
    session = ui.app.state_holder[session_hash]
    state_id = _block(ui, "preview").inputs[0]._id
    session[state_id] = copy.deepcopy(initial)
    events = [await _capture_queued(ui, block, inputs, session_hash=session_hash)
              for block, inputs in (preceding, stale)]
    response = await _process(ui, events[0].fn, events[0].data.data, session, session_hash)
    current = copy.deepcopy(session[state_id])
    assert current["annotation_context"] != initial["annotation_context"]
    if synchronize:
        sync = await _process(ui, _block(ui, "sync_annotation_context"), [None], session, session_hash)
        assert sync["data"] == [current["annotation_context"], False, False, False, False, ""]
    files = set(ui.workspace.output_dir.iterdir())
    with pytest.raises(Exception, match="annotation view changed"):
        await _process(ui, events[1].fn, events[1].data.data, session, session_hash)
    assert session[state_id] == current
    assert set(ui.workspace.output_dir.iterdir()) == files
    return session, current, response


@pytest.mark.parametrize("action", ["reference", "draft", "navigate"])
@pytest.mark.parametrize("synchronize", [False, True])
def test_queued_old_page_text_cannot_be_stored_saved_or_stashed_on_new_page(ui, action, synchronize):
    async def scenario():
        initial = ui.state
        token = initial["annotation_context"]
        action_block, args = {
            "reference": (_block(ui, "add_reference"), ["SYNTHETIC_OLD_PAGE", True, None, token]),
            "draft": (_block(ui, "save_draft"), ["SYNTHETIC_OLD_PAGE", None, token]),
            "navigate": (_block(ui, "change_page"), [1, None, "SYNTHETIC_OLD_PAGE", token]),
        }[action]
        _, current, response = await _replay(ui, initial,
            (_block(ui, "change_page"), [2, None, "", token]), (action_block, args), synchronize=synchronize)
        assert current["page"] == 2 and current["references"] == [] and current["reference_drafts"] == []
        assert response["data"][5] is False
    asyncio.run(scenario())


@pytest.mark.parametrize("kind", LABELS)
@pytest.mark.parametrize("synchronize", [False, True])
def test_queued_export_cannot_approve_newer_collection_but_fresh_confirmation_can(ui, kind, synchronize):
    async def scenario():
        initial = _ready(ui, kind)
        if kind == "regions":
            initial = _crop(ui, initial, "#/texts/2")
        elif kind == "omission-review":
            initial = _focus(ui, initial)
        token = initial["annotation_context"]
        preceding = {
            "references": (_block(ui, "add_reference"), ["Synthetic replacement reference.", True, None, token]),
            "regions": (_block(ui, "add_region"), [None, token]),
            "omission-review": (_block(ui, "assess_omission"), ["#/texts/1", "suspected_missing_text", None, token]),
        }[kind]
        export = _export(ui, kind)
        session, current, _ = await _replay(ui, initial, preceding, (export, [True, None, token]), synchronize=synchronize)
        assert not list(ui.workspace.output_dir.glob(f"ocr-{kind}-*.json"))
        fresh = await _capture_queued(ui, export, [True, None, current["annotation_context"]],
                                      session_hash="generated-annotation-context")
        await _process(ui, fresh.fn, fresh.data.data, session, "generated-annotation-context")
        path, = ui.workspace.output_dir.glob(f"ocr-{kind}-*.json")
        report = json.loads(path.read_bytes())
        key = {"references": "pages", "regions": "regions", "omission-review": "decisions"}[kind]
        state_key = {"references": "references", "regions": "regions", "omission-review": "omission_decisions"}[kind]
        assert report[key] == current[state_key] != initial[state_key]
    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["regions", "omission-review"])
@pytest.mark.parametrize("synchronize", [False, True])
def test_undo_and_reset_revoke_captured_collection_export(ui, kind, synchronize):
    async def scenario():
        initial = _ready(ui, kind)
        if kind == "regions":
            initial = _crop(ui, initial, "#/texts/2")
            initial = ui.functions["add_region"](initial, initial["annotation_context"])[0]
            action = _block(ui, "undo_region")
            args = [None, initial["annotation_context"]]
        else:
            initial = _focus(ui, initial)
            action = _button(ui, "Reset assessment to unresolved")
            args = ["#/texts/1", None, initial["annotation_context"]]
        _, current, _ = await _replay(ui, initial, (action, args),
            (_export(ui, kind), [True, None, initial["annotation_context"]]), synchronize=synchronize)
        key = "regions" if kind == "regions" else "omission_decisions"
        assert len(current[key]) == len(initial[key]) - 1
        path, _ = _export(ui, kind).fn(True, current, current["annotation_context"])
        report = json.loads(Path(path).read_bytes())
        assert report["regions" if kind == "regions" else "decisions"] == current[key]
        if kind == "omission-review":
            assert report["full_page_coverage_verified"] is False
    asyncio.run(scenario())


@pytest.mark.parametrize("action", ["add", "undo"])
@pytest.mark.parametrize("synchronize", [False, True])
def test_crop_actions_cannot_use_a_later_selected_rectangle(ui, action, synchronize):
    async def scenario():
        initial = _crop(ui, _ready(ui, "regions"))
        token = initial["annotation_context"]
        _, current, response = await _replay(ui, initial,
            (_block(ui, "select_proposal_region"), ["#/texts/2", None, token]),
            (_block(ui, "add_region" if action == "add" else "undo_region"), [None, token]), synchronize=synchronize)
        assert response["data"][2] is False
        assert current["regions"] == initial["regions"]
    asyncio.run(scenario())


@pytest.mark.parametrize("synchronize", [False, True])
def test_stale_omission_reset_cannot_remove_newer_assessment(ui, synchronize):
    async def scenario():
        initial = _focus(ui, _ready(ui, "omission-review"))
        token = initial["annotation_context"]
        _, current, _ = await _replay(ui, initial,
            (_block(ui, "assess_omission"), ["#/texts/1", "suspected_missing_text", None, token]),
            (_button(ui, "Reset assessment to unresolved"), ["#/texts/1", None, token]), synchronize=synchronize)
        assert current["omission_decisions"][0]["decision"] == "suspected_missing_text"
        current = _focus(ui, current)
        reset = _button(ui, "Reset assessment to unresolved").fn("#/texts/1", current, current["annotation_context"])[0]
        assert reset["omission_decisions"] == []
    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["reference", "decision"])
@pytest.mark.parametrize("synchronize", [False, True])
def test_captured_confirmations_cannot_survive_content_input_events(ui, kind, synchronize):
    async def scenario():
        initial = ui.state if kind == "reference" else _focus(ui, ui.state)
        token = initial["annotation_context"]
        label = "Human-checked reference text for this page" if kind == "reference" else "Omission assessment"
        stale = ((_block(ui, "add_reference"), ["Synthetic edited text.", True, None, token]) if kind == "reference" else
                 (_block(ui, "assess_omission"), ["#/texts/1", "false_alarm", None, token]))
        _, current, _ = await _replay(ui, initial, (_input(ui, label), [None]), stale, synchronize=synchronize)
        assert current["references"] == [] and current["omission_decisions"] == []
        assert current["reference_drafts"] == []
    asyncio.run(scenario())


def test_omission_assessment_requires_current_focus_even_with_current_token(ui):
    first = _focus(ui, ui.state)
    current = _focus(ui, first, "#/texts/2")
    before = copy.deepcopy(current)
    for ref in ("#/texts/1", None, "#/texts/5"):
        with pytest.raises(Exception, match="Review action failed"):
            ui.functions["assess_omission"](ref, "false_alarm", current, current["annotation_context"])
        assert current == before
    accepted = ui.functions["assess_omission"]("#/texts/2", "false_alarm", current, current["annotation_context"])[0]
    assert accepted["omission_decisions"][0]["region_ref"] == "#/texts/2"
    assert accepted["omission_region_ref"] is None


@pytest.mark.parametrize("synchronize", [False, True])
def test_captured_assessment_cannot_follow_another_omission_focus(ui, synchronize):
    async def scenario():
        initial = _focus(ui, ui.state)
        token = initial["annotation_context"]
        _, current, _ = await _replay(ui, initial,
            (_block(ui, "focus_omission"), ["#/texts/2", None, token]),
            (_block(ui, "assess_omission"), ["#/texts/1", "false_alarm", None, token]), synchronize=synchronize)
        assert current["omission_region_ref"] == "#/texts/2" and current["omission_decisions"] == []
    asyncio.run(scenario())


def test_process_api_injects_image_event_separately_from_annotation_context(ui):
    import gradio as gr

    async def scenario():
        session_hash = "generated-real-image-event"
        session = ui.app.state_holder[session_hash]
        block = _block(ui, "select_rectangle")
        state_id = block.inputs[0]._id
        session[state_id] = copy.deepcopy(ui.state)
        token = ui.state["annotation_context"]
        first = await ui.app.process_api(block, inputs=[None, "crop", token], state=session, session_hash=session_hash,
            event_data=gr.SelectData(None, {"index": [10, 20], "value": None}))
        assert first["data"][3] is False and session[state_id]["first"] == [10, 20]
        current = copy.deepcopy(session[state_id])
        with pytest.raises(Exception, match="annotation view changed"):
            await ui.app.process_api(block, inputs=[None, "crop", token], state=session, session_hash=session_hash,
                event_data=gr.SelectData(None, {"index": [90, 80], "value": None}))
        assert session[state_id] == current
        token = current["annotation_context"]
        await ui.app.process_api(block, inputs=[None, "crop", token], state=session, session_hash=session_hash,
            event_data=gr.SelectData(None, {"index": [90, 80], "value": None}))
        assert session[state_id]["first"] is None and session[state_id]["selections"]["crop"] == [.1, .2, .9, .8]
    asyncio.run(scenario())


@pytest.mark.parametrize("token", [None, "", "previous", 0, 1, True, False, [], {}])
def test_all_annotation_adoption_routes_reject_missing_stale_or_malformed_context(ui, token):
    state = _focus(ui, _crop(ui, _ready(ui, "references")))
    before = copy.deepcopy(state)
    files = set(ui.workspace.output_dir.iterdir())
    actions = [lambda: ui.functions["add_reference"]("SYNTHETIC_PRIVATE", True, state, token),
        lambda: ui.functions["save_draft"]("SYNTHETIC_PRIVATE", state, token),
        lambda: ui.functions["change_page"](2, state, "SYNTHETIC_PRIVATE", token),
        lambda: ui.functions["add_region"](state, token), lambda: ui.functions["undo_region"](state, token),
        lambda: ui.functions["clear_selection"](state, token), lambda: ui.functions["change_tool"](state, token),
        lambda: ui.functions["select_rectangle"](state, "crop", SimpleNamespace(index=[10, 20]), token),
        lambda: ui.functions["select_proposal_region"]("#/texts/2", state, token),
        lambda: ui.functions["focus_omission"]("#/texts/2", state, token),
        lambda: ui.functions["assess_omission"]("#/texts/1", "false_alarm", state, token),
        lambda: _button(ui, "Reset assessment to unresolved").fn("#/texts/1", state, token)]
    actions += [lambda kind=kind: _export(ui, kind).fn(True, state, token) for kind in LABELS]
    for action in actions:
        with pytest.raises(Exception, match="annotation view changed") as error:
            action()
        assert "SYNTHETIC_PRIVATE" not in str(error.value)
        assert state == before and set(ui.workspace.output_dir.iterdir()) == files


def test_initial_annotation_token_and_all_real_event_bindings_are_nonstate(ui):
    client = next(block for block in ui.app.blocks.values() if getattr(block, "label", None) == "Annotation view context")
    assert client.stateful is False and client.visible is False and client.value == ui.state["annotation_context"]
    assert type(client.value) is str and len(client.value) == 32 and client.value != ui.state["column_context"]
    names = {"change_page", "select_rectangle", "clear_selection", "undo_region", "add_region", "add_reference",
             "save_draft", "select_proposal_region", "change_tool", "focus_omission", "assess_omission"}
    for name in names:
        blocks = [block for block in ui.app.fns.values() if block.fn is not None and block.fn.__name__ == name]
        assert blocks and all(block.inputs[-1]._id == client._id for block in blocks)
    for kind in LABELS:
        assert _export(ui, kind).inputs[-1]._id == client._id
    assert _button(ui, "Reset assessment to unresolved").inputs[-1]._id == client._id
    focus = _block(ui, "focus_omission")
    event = next(item for item in ui.app.config["dependencies"] if ui.app.fns[item["id"]] is focus)
    assert [target[1] for target in event["targets"]] == ["input"]


def test_sync_is_atomic_all_false_and_every_ancestor_is_success_only(ui):
    labels = ["Annotation view context", "I checked every line against the scan",
              "Export the stored, reviewed references", "I reviewed these crops against the scan",
              "I reviewed these omission assessments against the scan", "Selected original OCR line (unverified)"]
    dependencies = {item["id"]: item for item in ui.app.config["dependencies"]}
    syncs = [(key, block) for key, block in ui.app.fns.items()
             if block.fn is not None and block.fn.__name__ == "sync_annotation_context"]
    assert len(syncs) >= 20
    for key, block in syncs:
        assert [item.label for item in block.outputs] == labels
        assert [type(item).__name__ for item in block.inputs] == ["State"]
        assert block.fn(ui.state) == (ui.state["annotation_context"], False, False, False, False, "")
        assert dependencies[key]["trigger_after"] is not None
        while dependencies[key]["trigger_after"] is not None:
            assert dependencies[key]["trigger_only_on_success"] is True
            key = dependencies[key]["trigger_after"]
    for label in ("Human-checked reference text for this page", "Omission assessment"):
        handler = _input(ui, label)
        assert handler.fn.__name__ == "annotation_input" and [type(item).__name__ for item in handler.inputs] == ["State"]
        assert [type(item).__name__ for item in handler.outputs] == ["State"]


def test_failed_sync_preserves_mutation_and_rejects_old_token_until_successful_sync(ui, monkeypatch):
    prior = _ready(ui, "references")
    current = ui.functions["add_reference"]("Synthetic replacement.", True, prior, prior["annotation_context"])[0]
    before = copy.deepcopy(current)
    original = ui.workspace.verify_inputs

    def fail():
        raise OSError("SYNTHETIC_PRIVATE_PATH")

    monkeypatch.setattr(ui.workspace, "verify_inputs", fail)
    with pytest.raises(Exception, match="Review action failed") as error:
        ui.functions["sync_annotation_context"](current)
    assert "SYNTHETIC_PRIVATE" not in str(error.value) and current == before
    with pytest.raises(Exception, match="annotation view changed"):
        _export(ui, "references").fn(True, current, prior["annotation_context"])
    monkeypatch.setattr(ui.workspace, "verify_inputs", original)
    token, *resets, selected = ui.functions["sync_annotation_context"](current)
    assert resets == [False] * 4 and selected == ""
    _export(ui, "references").fn(True, current, token)


def test_late_input_and_sync_can_only_revoke_without_stashing_old_text_or_restoring_consent(ui):
    old = _ready(ui, "references")
    current = ui.functions["change_page"](2, old, "", old["annotation_context"])[7]
    before = copy.deepcopy(current)
    current = ui.functions["annotation_input"](current)
    assert current["annotation_context"] != before["annotation_context"]
    assert {key: value for key, value in current.items() if key != "annotation_context"} == {
        key: value for key, value in before.items() if key != "annotation_context"}
    for _ in range(3):
        assert ui.functions["sync_annotation_context"](current) == (
            current["annotation_context"], False, False, False, False, "")
    with pytest.raises(Exception, match="annotation view changed"):
        _export(ui, "references").fn(True, current, old["annotation_context"])


def test_annotation_edits_do_not_change_column_consent_context_or_saved_order(ui):
    initial = copy.deepcopy(ui.state)
    initial["selections"] = {"body": [0, .2, 1, .7], "gutter": [.45, .2, .55, .7]}
    initial = ui.functions["preview"](initial, False, initial["column_context"])[1]
    current = ui.functions["add_reference"]("Synthetic checked reference.", True, initial, initial["annotation_context"])[0]
    assert current["column_context"] == initial["column_context"]
    assert current["layouts"] == initial["layouts"] and current["line_order"] == initial["line_order"]
    assert current["annotation_context"] != initial["annotation_context"]


@pytest.mark.parametrize("field", ["pdf_path", "recovery_path", "proposals_path", "draft_path"])
def test_sync_and_mutations_recheck_fixed_inputs_even_after_scan_cache(ui, field):
    state = _focus(ui, _crop(ui, _ready(ui, "references")))
    before = copy.deepcopy(state)
    getattr(ui.workspace, field).write_bytes(b"SYNTHETIC_PRIVATE_CHANGED_GENERATION")
    for action in (lambda: ui.functions["sync_annotation_context"](state),
                   lambda: ui.functions["annotation_input"](state),
                   lambda: ui.functions["add_region"](state, state["annotation_context"]),
                   lambda: ui.functions["add_reference"]("Generated", True, state, state["annotation_context"]),
                   lambda: ui.functions["assess_omission"]("#/texts/1", "false_alarm", state, state["annotation_context"]),
                   lambda: _export(ui, "references").fn(True, state, state["annotation_context"])):
        with pytest.raises(Exception, match="Review action failed") as error:
            action()
        assert "SYNTHETIC_PRIVATE" not in str(error.value) and state == before


def test_restart_retains_draft_work_but_never_old_context_or_approval(ui, monkeypatch):
    state = _ready(ui, "references")
    old_token = state["annotation_context"]
    _, path, _ = ui.functions["save_draft"]("Synthetic unfinished transcription.", state, old_token)
    payload = json.loads(Path(path).read_bytes())
    assert "annotation_context" not in payload and "column_context" not in payload
    loaded = ReviewWorkspace(ui.workspace.pdf_path, ui.workspace.recovery_path, ui.workspace.output_dir,
                             proposals_path=ui.workspace.proposals_path, draft_path=Path(path))
    monkeypatch.setattr(loaded, "render", ui.workspace.render)
    from ocr_review_ui import build_app
    app = build_app(loaded)
    try:
        state = copy.deepcopy(next(block.value for block in app.blocks.values() if type(block).__name__ == "State"))
        assert state["references"] == payload["references"]
        assert state["annotation_context"] != old_token
        assert all(block.value is False for block in app.blocks.values() if type(block).__name__ == "Checkbox")
        restored = SimpleNamespace(app=app)
        with pytest.raises(Exception, match="annotation view changed"):
            _export(restored, "references").fn(True, state, old_token)
        with pytest.raises(Exception, match="review confirmation"):
            _export(restored, "references").fn(False, state, state["annotation_context"])
    finally:
        app.close()
