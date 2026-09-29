"""Generated context workflow and real Gradio queue checks; no OCR or models."""

import asyncio
import copy
import json

import pytest

from ocr_review_context_ui import ContextAuthoring, browser_selection_span, context_stamp, prepare_context_state
from ocr_review_drafts import build_draft, initial_state, restore_state
from test_ocr_context_authoring_io import environment as environment  # noqa: F401
from test_ocr_context_evaluation_io import inputs as inputs  # noqa: F401
from test_ocr_review_columns import _capture_queued, _process, ui as ui, workspace as workspace  # noqa: F401


def form_for(state):
    draft = state["context_authoring"]
    context = next((item for item in draft["contexts"] if item["context_id"] == draft["selected_context_id"]), None)
    check = None if context is None else next((item for item in context["checks"]
                                               if item["check_id"] == draft["selected_check_id"]), None)
    anchor = (context or {}).get("source_anchor") or state["_context_ui"]["pending_source_fields"].get(
        draft["selected_context_id"], {"kind": "sentence", "cell": None})
    cell = anchor["cell"] or {"row": 1, "column": 1}
    mapping = (context or {}).get("correspondence", {"status": "unreviewed", "candidate_span": None})
    return [(context or {}).get("reference", ""), (check or {}).get("category", "number"),
            *((check or {}).get("reference_span") or [None, None]),
            (check or {}).get("left_anchor", ""), (check or {}).get("right_anchor", ""),
            mapping["status"], *(mapping["candidate_span"] or [None, None]),
            anchor["kind"], cell["row"], cell["column"], (context or {}).get("page_number", state["page"])]


@pytest.fixture
def editor(environment, monkeypatch):
    workspace, draft = environment
    Image = pytest.importorskip("PIL.Image")
    rendered = []

    def original(page):
        rendered.append(page)
        return Image.new("RGB", (100, 80), "white")

    monkeypatch.setattr(workspace, "render_original_page", original)
    state = initial_state(workspace.document)
    state["context_authoring"] = copy.deepcopy(draft)
    prepare_context_state(state)
    return ContextAuthoring(workspace), state, rendered


def step(controller, state, action, argument=None, form=None):
    result = controller.transition(state, context_stamp(state), form_for(state) if form is None else form, action, argument)
    if result[1] is not None:
        result[1].close()
    return result[0]


def reviewed(controller, state):
    state = step(controller, state, "open_source")
    return step(controller, state, "review_reference", True)


def test_author_review_export_map_evaluate_and_repeated_focus(editor, monkeypatch):
    import ocr_context_evaluation_io

    controller, state, rendered = editor
    context = state["context_authoring"]["contexts"][0]
    context["correspondence"] = {"status": "unreviewed", "candidate_span": None}
    state = reviewed(controller, state)
    state = step(controller, state, "export_reference", True)
    reference = state["_context_ui"]["exports"]["reference"]
    text = context["reference"]
    state = step(controller, state, "candidate_selection", ([0, len(text)], text, text))
    assert state["_context_ui"]["exports"] == {"reference": reference}
    state = step(controller, state, "review_mapping", True)
    state = step(controller, state, "export_mapping", True)
    state = step(controller, state, "evaluate")
    exports = state["_context_ui"]["exports"]
    assert len(exports) == 3
    assert json.loads(exports["report"].path.read_bytes())["coverage"]["checks"]["passed"] == 1
    monkeypatch.setattr(ocr_context_evaluation_io, "evaluate_context_files", lambda *_a, **_k: pytest.fail("focus repeated scoring"))
    for _ in range(3):
        state = step(controller, state, "result", (1, 1))
        assert state["context_authoring"]["selected_check_id"] == context["checks"][0]["check_id"]
        assert state["_context_ui"]["exports"] == exports
    assert rendered == [1, 1, 1, 1]
    assert len(list(controller.workspace.output_dir.glob("ocr-context-*.json"))) == 3


@pytest.mark.parametrize("action", ["review_reference", "review_mapping", "export_reference", "export_mapping", "evaluate", "result"])
def test_pending_form_cannot_reuse_old_confirmation_or_exports(editor, action):
    controller, state, _ = editor
    state = reviewed(controller, state)
    before = copy.deepcopy(state)
    pending = form_for(state)
    pending[0] += " changed"
    with pytest.raises(ValueError, match="Store pending"):
        controller.transition(state, context_stamp(state), pending, action, True)
    assert state == before
    assert not list(controller.workspace.output_dir.glob("ocr-context-*.json"))


@pytest.mark.parametrize("action", ["store", "open_source", "review_reference", "export_reference", "reference_selection"])
def test_changed_page_control_cannot_act_on_previous_source_page(editor, action):
    controller, state, _ = editor
    before = copy.deepcopy(state)
    pending = form_for(state)
    pending[12] = 2
    with pytest.raises(ValueError, match="Set the context page"):
        controller.transition(state, context_stamp(state), pending, action, True)
    assert state == before


def test_input_revocation_does_not_copy_or_modify_persistent_draft(editor):
    controller, state, _ = editor
    before = copy.deepcopy(state)
    edited = controller.input(state, mapping_only=True)
    assert state == before
    assert edited["context_authoring"] is state["context_authoring"]
    assert edited["_context_ui"] is not state["_context_ui"]
    assert context_stamp(edited)[0] != context_stamp(state)[0]


def test_selection_limit_precedes_newline_expansion():
    with pytest.raises(ValueError, match="limit"):
        browser_selection_span("\r\n" * 50_001, [0, 0], "")


def test_selection_of_matching_substring_rejects_edits_elsewhere_in_candidate(editor):
    controller, state, _ = editor
    candidate = controller.candidate_text(1)
    start = candidate.index("10")
    before = copy.deepcopy(state)
    with pytest.raises(ValueError, match="displayed candidate differs"):
        step(controller, state, "candidate_selection", ([start, start + 2], "10", "X" + candidate[1:]))
    assert state == before


def test_candidate_selection_accepts_native_newlines_but_keeps_raw_offsets(editor, monkeypatch):
    controller, state, _ = editor
    monkeypatch.setattr(controller, "candidate_text", lambda _page: "prefix\r\n10 end")
    # Validation still uses the real saved candidate; only the display adapter is
    # replaced to exercise the full-value join before its independently tested policy.
    selected = step(controller, state, "candidate_selection", ([7, 9], "10", "prefix\n10 end"))
    assert selected["context_authoring"]["contexts"][0]["correspondence"]["candidate_span"] == [8, 10]


def test_unchanged_native_lf_roundtrip_preserves_authored_crlf_and_exact_spans(editor):
    controller, state, _ = editor
    context = state["context_authoring"]["contexts"][0]
    context["reference"] = "prefix\r\n10 end"
    context["checks"][0].update(reference_span=[8, 10], left_anchor="prefix\r\n", right_anchor=" end")
    context["correspondence"] = {"status": "unreviewed", "candidate_span": None}
    pending = form_for(state)
    pending[0] = "prefix\n10 end"
    pending[4] = "prefix\n"
    stored = step(controller, state, "store", form=pending)
    assert stored["context_authoring"] == state["context_authoring"]
    selected = step(controller, stored, "reference_selection", ([7, 9], "10"), form=pending)
    assert selected["context_authoring"] == state["context_authoring"]
    pending[0] = "changed\n10 end"
    edited = step(controller, selected, "store", form=pending)
    assert edited["context_authoring"]["contexts"][0]["reference"] == pending[0]
    assert edited["context_authoring"]["contexts"][0]["checks"][0]["reference_span"] is None


def test_late_input_only_revokes_and_sync_cannot_rebind_a_lost_form(editor):
    controller, state, _ = editor
    state = reviewed(controller, state)
    old_stamp = context_stamp(state)
    old_draft = copy.deepcopy(state["context_authoring"])
    state = controller.input(state)
    assert state["context_authoring"] == old_draft
    assert state["_context_ui"]["reference_reviews"] == {}
    with pytest.raises(ValueError, match="view changed"):
        controller.transition(state, old_stamp, form_for(state), "store")
    assert controller.sync(state, old_stamp[1]) == (context_stamp(state)[0], False, False, False, False)
    state = step(controller, state, "new_context")
    state = controller.input(state)  # Old input arriving after the context switch.
    assert controller.sync(state, old_stamp[1]) == ("", False, False, False, False)
    assert state["context_authoring"]["contexts"][0] == old_draft["contexts"][0]
    assert state["context_authoring"]["contexts"][1]["reference"] == ""


def test_switch_and_save_preserve_pending_unicode_text_without_old_spans_or_approvals(editor):
    controller, state, _ = editor
    state = reviewed(controller, state)
    first = state["context_authoring"]["selected_context_id"]
    check_id = state["context_authoring"]["selected_check_id"]
    pending = form_for(state)
    pending[0] = "  Unfinished 😀 e\u0301\r\nreference"
    state = step(controller, state, "new_context", form=pending)
    original = state["context_authoring"]["contexts"][0]
    assert original["context_id"] == first and original["checks"][0]["check_id"] == check_id
    assert original["reference"] == pending[0]
    assert original["checks"][0]["reference_span"] is None
    assert original["correspondence"] == {"status": "unreviewed", "candidate_span": None}
    snapshot = build_draft(state, controller.document)
    loaded = restore_state(snapshot, controller.document)
    assert "_context_ui" not in snapshot and "_context_ui" not in loaded
    prepare_context_state(loaded)
    assert loaded["context_authoring"] == state["context_authoring"]
    assert loaded["_context_ui"]["owner"] is None and loaded["_context_ui"]["reference_reviews"] == {}
    assert context_stamp(loaded) != context_stamp(state)


@pytest.mark.parametrize("first,second,expected", [
    ([0, 0], [100, 80], [0., 0., 1., 1.]),
    ([0, 40], [100, 80], [0., .5, 1., 1.]),
    ([100, 80], [25, 20], [.25, .25, 1., 1.]),
])
def test_original_source_corners_include_page_edges(editor, first, second, expected):
    controller, state, rendered = editor
    state = step(controller, state, "open_source")
    state = step(controller, state, "source_selection", first)
    assert rendered == [1]
    state = step(controller, state, "source_selection", second)
    assert state["context_authoring"]["contexts"][0]["source_anchor"]["bbox"] == expected
    assert state["context_authoring"]["contexts"][0]["correspondence"]["status"] == "unreviewed"
    assert rendered == [1, 1]


@pytest.mark.parametrize("text,index,value,span", [
    ("A😀e\u0301Z\r\nBΩ", [1, 3], "😀", [1, 2]),
    ("A😀e\u0301Z\r\nBΩ", [3, 5], "e\u0301", [2, 4]),
    ("A😀e\u0301Z\r\nBΩ", [0, 9], "A😀e\u0301Z\nBΩ", [0, 9]),
    ("x\r\ny\rz", [2, 5], "y\nz", [3, 6]),
    ("\r\n", [1, 1], "", [2, 2]),
])
def test_native_unicode_newline_ranges_join_to_exact_raw_source(text, index, value, span):
    assert browser_selection_span(text, index, value) == span


@pytest.mark.parametrize("index,value", [([2, 3], "😀"), ([3, 1], ""), ([1, 3], "x"), ([0, 99], "")])
def test_native_selection_rejects_surrogate_splits_reversal_forgery_and_bounds(index, value):
    with pytest.raises(ValueError):
        browser_selection_span("A😀\r\nB", index, value)


def button(ui, label):
    component = next(item for item in ui.app.blocks.values() if type(item).__name__ == "Button" and item.value == label)
    dependency = next(item for item in ui.app.config["dependencies"] if (component._id, "click") in map(tuple, item["targets"]))
    return ui.app.fns[dependency["id"]]


def test_real_gradio_selectdata_injection_and_private_shared_queue(ui):
    gr = pytest.importorskip("gradio")
    from gradio.helpers import special_args

    selected = [block for block in ui.app.fns.values() if block.fn and block.fn.__name__ == "context_selection"]
    assert len(selected) == 3
    for block in selected:
        data = [None, *context_stamp(ui.state), *form_for(ui.state)]
        if len(block.inputs) == len(data) + 1:
            data.append("")
        event = gr.SelectData(None, {"index": [1, 3], "value": "😀"})
        injected, _, event_index, _ = special_args(block.fn, data, event_data=event)
        assert event_index == 1 and isinstance(injected[1], gr.SelectData)
        assert injected[1].index == [1, 3]
        assert block.concurrency_id == "ocr-review" and block.concurrency_limit == 1
    assert len([block for block in ui.app.blocks.values() if type(block).__name__ == "State"]) == 1
    assert button(ui, "Save review draft").fn.__name__ == "store_context_for_draft"
    candidate = next(block for block in ui.app.blocks.values() if getattr(block, "elem_id", None) == "ocr-context-candidate")
    assert candidate.interactive is True
    hooks = [item for item in ui.app.config["dependencies"] if item.get("js") and "field.readOnly = true" in item["js"]]
    assert len(hooks) == 3 and all(item["backend_fn"] is False and item["inputs"] == [] for item in hooks)


def test_real_queue_rejects_captured_old_context_save_after_switch(ui):
    async def scenario():
        session_hash = "generated-context-stale-save"
        session = ui.app.state_holder[session_hash]
        create = button(ui, "New context")
        save = button(ui, "Save review draft")
        state_id = create.inputs[0]._id
        session[state_id] = copy.deepcopy(ui.state)
        captured = [None, *context_stamp(ui.state), *form_for(ui.state)]
        first = await _capture_queued(ui, create, captured, session_hash=session_hash)
        late = await _capture_queued(ui, save, captured, session_hash=session_hash)
        await _process(ui, first.fn, first.data.data, session, session_hash)
        current = copy.deepcopy(session[state_id])
        before_files = set(ui.workspace.output_dir.iterdir())
        with pytest.raises(Exception, match="Draft was not saved"):
            await _process(ui, late.fn, late.data.data, session, session_hash)
        assert session[state_id] == current
        assert set(ui.workspace.output_dir.iterdir()) == before_files

    asyncio.run(scenario())


def test_candidate_unchanged_or_stale_input_is_noop_and_current_edit_restores_saved_text(ui):
    gr = pytest.importorskip("gradio")

    controller = ContextAuthoring(ui.workspace)
    state = step(controller, ui.state, "new_context")
    candidate = controller.candidate_text(state["context_authoring"]["contexts"][0]["page_number"])
    before = copy.deepcopy(state)
    restore = ui.functions["restore_saved_candidate"]
    assert restore(state, context_stamp(state)[1], candidate) == [gr.skip()] * 9
    assert restore(state, "old display", "forged") == [gr.skip()] * 9
    assert state == before
    repaired = restore(state, context_stamp(state)[1], "forged")
    assert repaired[0]["context_authoring"] == state["context_authoring"]
    assert repaired[1] == candidate and repaired[2] != context_stamp(state)[0]
    assert repaired[3:7] == [False] * 4
    assert state == before


def test_pending_source_fields_survive_switch_and_corners_then_revoke_completed_reviews(editor):
    controller, state, _ = editor
    context = state["context_authoring"]["contexts"][0]
    identifier = context["context_id"]
    context["source_anchor"] = None
    pending = form_for(state)
    pending[9:12] = ["cell", 2, 3]
    state = step(controller, state, "store", form=pending)
    assert state["context_authoring"]["contexts"][0]["source_anchor"] is None
    state = step(controller, state, "open_source")
    state = step(controller, state, "source_selection", [10, 20])
    assert form_for(state)[9:12] == ["cell", 2, 3]
    old_stamp = context_stamp(state)
    state = step(controller, state, "new_context")
    assert state["_context_ui"]["first_corner"] is None
    before = copy.deepcopy(state)
    with pytest.raises(ValueError, match="view changed"):
        controller.transition(state, old_stamp, pending, "source_selection", [90, 60])
    assert state == before
    state = step(controller, state, "select_context", identifier)
    assert form_for(state)[9:12] == ["cell", 2, 3]
    saved = build_draft(state, controller.document)
    restored = restore_state(saved, controller.document)
    prepare_context_state(restored)
    assert "_context_ui" not in saved
    assert restored["_context_ui"]["pending_source_fields"] == {}
    assert restored["context_authoring"]["contexts"][0]["source_anchor"] is None
    state = step(controller, state, "open_source")
    state = step(controller, state, "source_selection", [10, 20])
    state = step(controller, state, "source_selection", [90, 60])
    anchor = state["context_authoring"]["contexts"][0]["source_anchor"]
    assert anchor == {"kind": "cell", "bbox": [.1, .25, .9, .75], "cell": {"row": 2, "column": 3}}
    assert identifier not in state["_context_ui"]["pending_source_fields"]
    assert build_draft(state, controller.document)["context_authoring"]["contexts"][0]["source_anchor"] == anchor
    pending = form_for(state)
    pending[6] = "missing"
    state = step(controller, state, "store", form=pending)
    state = step(controller, state, "review_reference", True)
    state = step(controller, state, "review_mapping", True)
    assert identifier in state["_context_ui"]["reference_reviews"]
    assert identifier in state["_context_ui"]["mapping_reviews"]
    pending = form_for(state)
    pending[11] = 4
    state = step(controller, state, "store", form=pending)
    assert state["context_authoring"]["contexts"][0]["source_anchor"]["cell"]["column"] == 4
    assert state["context_authoring"]["contexts"][0]["correspondence"]["status"] == "unreviewed"
    assert state["_context_ui"]["reference_reviews"] == state["_context_ui"]["mapping_reviews"] == {}


def test_real_render_preserves_unfinished_source_controls_and_clears_page_and_deleted_context(ui, monkeypatch):
    gr = pytest.importorskip("gradio")

    Image = pytest.importorskip("PIL.Image")
    monkeypatch.setattr(ui.workspace, "render_original_page", lambda _page: Image.new("RGB", (100, 80), "white"))

    def invoke(state, label, fields=None, *extra):
        result = button(ui, label).fn(state, *context_stamp(state), *(fields or form_for(state)), *extra)
        if isinstance(result[26], Image.Image):
            result[26].close()
        return result

    result = invoke(ui.state, "New context")
    fields = result[7:20]
    fields[9:12] = ["region", 2, 3]
    result = invoke(result[0], "Store context changes", fields)
    assert result[16:19] == ["region", 2, 3]
    result = invoke(result[0], "Open original source preview", result[7:20])
    assert result[16:19] == ["region", 2, 3]
    image = next(item for item in ui.app.blocks.values() if getattr(item, "label", None) == "Original source for context")
    dependency = next(item for item in ui.app.config["dependencies"] if (image._id, "select") in map(tuple, item["targets"]))
    source = ui.app.fns[dependency["id"]]
    result = source.fn(result[0], gr.SelectData(None, {"index": [10, 20], "value": None}),
                       *context_stamp(result[0]), *result[7:20])
    assert result[16:19] == ["region", 2, 3]
    assert result[0]["context_authoring"]["contexts"][0]["source_anchor"] is None
    fields = result[7:20]
    fields[9:12] = ["cell", None, 3]
    result = invoke(result[0], "Store context changes", fields)
    assert result[16:19] == ["cell", None, 3]
    fields = result[7:20]
    fields[12] = 2
    result = invoke(result[0], "Set context page", fields, 2)
    assert result[16:20] == ["sentence", 1, 1, 2]
    assert result[0]["_context_ui"]["pending_source_fields"] == {}
    fields = result[7:20]
    fields[9:12] = ["cell", 2, 3]
    result = invoke(result[0], "Store context changes", fields)
    result = invoke(result[0], "Open original source preview", result[7:20])
    for corner in ([10, 20], [90, 60]):
        result = source.fn(result[0], gr.SelectData(None, {"index": corner, "value": None}),
                           *context_stamp(result[0]), *result[7:20])
        assert result[16:19] == ["cell", 2, 3]
        if isinstance(result[26], Image.Image):
            result[26].close()
    anchor = result[0]["context_authoring"]["contexts"][0]["source_anchor"]
    assert anchor == {"kind": "cell", "bbox": [.1, .25, .9, .75], "cell": {"row": 2, "column": 3}}
    assert result[0]["_context_ui"]["pending_source_fields"] == {}
    result = invoke(result[0], "New context", result[7:20])
    fields = result[7:20]
    fields[9:12] = ["region", 2, 3]
    result = invoke(result[0], "Store context changes", fields)
    result = invoke(result[0], "Delete context", result[7:20])
    assert len(result[0]["context_authoring"]["contexts"]) == 1
    assert result[0]["_context_ui"]["pending_source_fields"] == {}


@pytest.mark.parametrize("field,value", [(9, "paragraph"), (10, True), (10, 0), (11, 100_001), (11, 1.5)])
def test_invalid_pending_source_fields_are_rejected_without_fabricating_geometry(editor, field, value):
    controller, state, _ = editor
    state["context_authoring"]["contexts"][0]["source_anchor"] = None
    before = copy.deepcopy(state)
    fields = form_for(state)
    fields[9] = "cell"
    fields[field] = value
    with pytest.raises(ValueError):
        step(controller, state, "store", form=fields)
    assert state == before


@pytest.mark.parametrize("label,index,value,no_check,stale", [
    ("Context reference text", [0, 7], "pending", True, False),
    ("Context reference text", [0, 7], "pending", False, "before_check"),
    ("Context reference text", [1, 1], "", False, False),
    ("Context reference text", [1, 1], "", False, True),
    ("Saved candidate text for context selection", [1, 1], "", False, False),
    ("Saved candidate text for context selection", [1, 1], "", False, True),
])
def test_native_caret_and_no_check_events_leave_pending_form_and_session_untouched(ui, monkeypatch, label, index, value, no_check, stale):
    gr = pytest.importorskip("gradio")

    controller = ContextAuthoring(ui.workspace)
    state = step(controller, ui.state, "new_context")
    if not no_check:
        state = step(controller, state, "new_check")
    fields = form_for(state)
    fields[0] = "pending reference 10"
    stamps = context_stamp(state)
    if stale:
        stamps[0] = "old capture"
    if stale == "before_check":
        stamps[3] = ""
    component = next(item for item in ui.app.blocks.values() if getattr(item, "label", None) == label)
    dependency = next(item for item in ui.app.config["dependencies"] if (component._id, "select") in map(tuple, item["targets"]))
    block = ui.app.fns[dependency["id"]]
    values = [*stamps, *fields]
    if label.startswith("Saved candidate"):
        values.append(controller.candidate_text(1))
    event = gr.SelectData(None, {"index": index, "value": value})
    identifier = state["context_authoring"]["selected_context_id"]
    state["_context_ui"]["reference_reviews"][identifier] = "a" * 64
    state["_context_ui"]["mapping_reviews"][identifier] = "b" * 64
    before = copy.deepcopy(state)
    expected = [gr.skip()] * len(block.outputs)
    with monkeypatch.context() as scoped:
        scoped.setattr(ui.workspace, "verify_inputs", lambda: pytest.fail("irrelevant selection verified sources"))
        scoped.setattr(copy, "deepcopy", lambda *_a, **_k: pytest.fail("irrelevant selection copied state"))
        assert block.fn(state, event, *values) == expected
    assert state == before
    assert fields[0] == "pending reference 10"


@pytest.mark.parametrize("label,index,value,stale", [
    ("Context reference text", [True, True], "", False),
    ("Context reference text", [2, 1], "A", False),
    ("Context reference text", [0, 0], "A", False),
    ("Context reference text", [0, 1], "forged", False),
    ("Context reference text", [0, 1], "A", True),
    ("Saved candidate text for context selection", [0, 1], "Z", True),
])
def test_actionable_or_malformed_text_selection_keeps_existing_refusal_guards(ui, label, index, value, stale):
    gr = pytest.importorskip("gradio")

    controller = ContextAuthoring(ui.workspace)
    state = step(controller, ui.state, "new_context")
    fields = form_for(state)
    fields[0] = "ABC"
    state = step(controller, state, "store", form=fields)
    state = step(controller, state, "new_check")
    component = next(item for item in ui.app.blocks.values() if getattr(item, "label", None) == label)
    dependency = next(item for item in ui.app.config["dependencies"] if (component._id, "select") in map(tuple, item["targets"]))
    block = ui.app.fns[dependency["id"]]
    stamps = context_stamp(state)
    if stale:
        stamps[0] = "old capture"
    values = [*stamps, *form_for(state)]
    if label.startswith("Saved candidate"):
        values.append(controller.candidate_text(1))
        value = controller.candidate_text(1)[0]
    before = copy.deepcopy(state)
    with pytest.raises(gr.Error, match="Context selection failed"):
        block.fn(state, gr.SelectData(None, {"index": index, "value": value}), *values)
    assert state == before


def test_native_source_image_zero_coordinates_still_store_first_corner(ui, monkeypatch):
    gr = pytest.importorskip("gradio")

    Image = pytest.importorskip("PIL.Image")
    monkeypatch.setattr(ui.workspace, "render_original_page", lambda _page: Image.new("RGB", (100, 80), "white"))
    controller = ContextAuthoring(ui.workspace)
    state = step(controller, ui.state, "new_context")
    state = step(controller, state, "open_source")
    image = next(item for item in ui.app.blocks.values() if getattr(item, "label", None) == "Original source for context")
    dependency = next(item for item in ui.app.config["dependencies"] if (image._id, "select") in map(tuple, item["targets"]))
    block = ui.app.fns[dependency["id"]]
    result = block.fn(state, gr.SelectData(None, {"index": [0, 0], "value": None}),
                      *context_stamp(state), *form_for(state))
    assert result[0]["_context_ui"]["first_corner"] == [0, 0]
    assert result[0]["context_authoring"]["contexts"][0]["source_anchor"] is None
    assert "First corner stored" in result[28]
    assert state["_context_ui"]["first_corner"] is None


def test_launched_context_download_survives_skips_and_clears_on_late_copy_failure(editor, monkeypatch, tmp_path):
    from pathlib import Path
    import tempfile
    from types import SimpleNamespace
    from urllib.parse import unquote

    monkeypatch.setenv("GRADIO_ANALYTICS_ENABLED", "False")
    gr = pytest.importorskip("gradio")
    from gradio.context import LocalContext
    from gradio.exceptions import InvalidPathError
    from gradio.processing_utils import _check_allowed
    import ocr_review_runtime as runtime
    import storage_policy
    from ocr_review_context_ui import mount_context_authoring

    controller, initial, _ = editor
    workspace = controller.workspace
    source_before = workspace.pdf_path.read_bytes(), workspace.recovery_path.read_bytes()
    expected_context = {key: copy.deepcopy(value) for key, value in initial["context_authoring"]["contexts"][0].items()
                        if key != "correspondence"}
    cache = storage_policy.ensure_private_directory(workspace.output_dir / "ocr-review-cache-generated")
    monkeypatch.setenv("GRADIO_TEMP_DIR", str(cache))
    cwd, system_temp = tmp_path / "separate-cwd", tmp_path / "separate-system-temp"
    cwd.mkdir()
    system_temp.mkdir()
    monkeypatch.chdir(cwd)
    monkeypatch.setattr(tempfile, "tempdir", str(system_temp))
    created = []
    save = workspace.save_context_reference

    def record_export(*args, **kwargs):
        binding = save(*args, **kwargs)
        created.append(binding)
        return binding

    monkeypatch.setattr(workspace, "save_context_reference", record_export)
    with gr.Blocks() as app:
        state_component = gr.State(copy.deepcopy(initial))
        mount_context_authoring(gr, workspace, state_component, initial, app=app)
    panel = SimpleNamespace(app=app)
    export = button(panel, "Export context reference")
    download = next(item for item in export.outputs if getattr(item, "label", None) == "New context artifact")
    file_index = export.outputs.index(download)
    notice_index = next(index for index, item in enumerate(export.outputs)
                        if getattr(item, "label", None) == "Context review status")
    app.allowed_paths = []
    app.blocked_paths = [str(workspace.pdf_path), str(workspace.recovery_path)]
    # Exercise the installed postprocessing guard, without a port or HTTP/auth claim.
    app.has_launched = True

    async def scenario():
        session_hash = "generated-context-download"
        session = app.state_holder[session_hash]
        session[state_component._id] = copy.deepcopy(initial)
        token = LocalContext.blocks.set(app)

        async def invoke(label, *extra):
            current = session[state_component._id]
            return await app.process_api(button(panel, label),
                inputs=[None, *context_stamp(current), *form_for(current), *extra],
                state=session, session_hash=session_hash)

        try:
            first_skip = await invoke("Store context changes")
            assert first_skip["data"][file_index] == gr.skip()
            first_clone = session[download._id]
            assert first_clone is not download
            await invoke("Open original source preview")
            await invoke("Review current context reference", True)
            originals, copies = {}, {}
            for number in (1, 2):
                if number == 2:
                    prior_component = session[download._id]
                    skipped = await invoke("Store context changes")
                    assert skipped["data"][file_index] == gr.skip()
                    assert session[download._id] is not prior_component
                response = await invoke("Export context reference", True)
                assert len(created) == number
                binding = created[-1]
                retained = binding.path.read_bytes()
                assert binding.path.parent == workspace.output_dir
                assert json.loads(retained)["contexts"] == [expected_context]
                assert session[state_component._id]["_context_ui"]["exports"] == {"reference": binding}
                payload = response["data"][file_index]
                copied = Path(payload["path"])
                assert copied == cache / binding.path.name and copied != binding.path
                assert copied.read_bytes() == retained
                # Gradio 6.28 percent-encodes the path in file URLs.
                assert unquote(payload["url"]).endswith(str(copied))
                assert "Created a new source-bound context artifact" in response["data"][notice_index]
                with pytest.raises(InvalidPathError):
                    _check_allowed(binding.path, False)
                _check_allowed(copied, False)
                originals[binding.path], copies[copied] = retained, retained
            assert len(originals) == len(copies) == 2
            before = copy.deepcopy(session[state_component._id])
            publish = runtime._publish_new_report
            failed_copies = []

            def fail_cache_publication(temporary, target):
                if Path(target).parent == cache:
                    failed_copies.append(Path(target))
                    raise OSError("generated private failure detail")
                publish(temporary, target)

            with monkeypatch.context() as scoped:
                scoped.setattr(runtime, "_publish_new_report", fail_cache_publication)
                failed = await invoke("Export context reference", True)
            assert len(created) == 3 and failed_copies == [cache / created[-1].path.name]
            assert failed["data"][file_index] is None
            assert "artifact was saved" in failed["data"][notice_index]
            assert "private output folder" in failed["data"][notice_index]
            assert "generated private failure detail" not in failed["data"][notice_index]
            current = session[state_component._id]
            assert current["context_authoring"] == before["context_authoring"]
            assert current["_context_ui"]["owner"] == before["_context_ui"]["owner"]
            assert current["_context_ui"]["reference_reviews"] == before["_context_ui"]["reference_reviews"]
            assert current["_context_ui"]["exports"] == {"reference": created[-1]}
            assert json.loads(created[-1].path.read_bytes())["contexts"] == [expected_context]
            assert set(workspace.output_dir.glob("ocr-context-*.json")) == {binding.path for binding in created}
            assert set(cache.glob("ocr-context-*.json")) == set(copies)
            assert all(path.read_bytes() == raw for path, raw in {**originals, **copies}.items())
            assert source_before == (workspace.pdf_path.read_bytes(), workspace.recovery_path.read_bytes())
        finally:
            LocalContext.blocks.reset(token)

    try:
        asyncio.run(scenario())
    finally:
        app.has_launched = False
        app.close()



def navigation(ui, label):
    component = next(item for item in ui.app.blocks.values() if getattr(item, "label", None) == label)
    dependencies = [item for item in ui.app.config["dependencies"]
                    if any(target == component._id for target, _ in item["targets"])]
    assert len(dependencies) == 1
    dependency = dependencies[0]
    assert list(map(tuple, dependency["targets"])) == [(component._id, "change")]
    assert dependency["trigger_mode"] == "once"
    block = ui.app.fns[dependency["id"]]
    assert block.concurrency_id == "ocr-review" and block.concurrency_limit == 1
    return block


def test_navigation_echoes_preserve_pending_forms_without_source_checks_or_state_copies(ui, monkeypatch):
    gr = pytest.importorskip("gradio")

    controller = ContextAuthoring(ui.workspace)
    state = step(controller, ui.state, "new_context")
    state = step(controller, state, "new_check")
    identifier = state["context_authoring"]["selected_context_id"]
    state["_context_ui"]["reference_reviews"][identifier] = "a" * 64
    state["_context_ui"]["mapping_reviews"][identifier] = "b" * 64
    for current in (state, ui.state):
        for label, key in (("Context to edit", "selected_context_id"),
                           ("Context check to edit", "selected_check_id"),
                           ("Context evaluation result to inspect", None)):
            block = navigation(ui, label)
            argument = None if key is None else current["context_authoring"][key]
            fields = form_for(current)
            fields[0], fields[12] = "Uncommitted 😀 reference", 2
            before, pending = copy.deepcopy(current), copy.deepcopy(fields)
            stamps = context_stamp(current)
            with monkeypatch.context() as scoped:
                scoped.setattr(ui.workspace, "verify_inputs", lambda: pytest.fail("navigation echo verified sources"))
                scoped.setattr(copy, "deepcopy", lambda *_a, **_k: pytest.fail("navigation echo copied state"))
                assert block.fn(current, *stamps, *fields, argument) == [gr.skip()] * len(block.outputs)
                for index in range(4):
                    stale = stamps.copy()
                    stale[index] = "old captured value"
                    with pytest.raises(gr.Error, match="Context action failed"):
                        block.fn(current, *stale, *fields, argument)
            assert current == before and fields == pending


def test_changed_and_cleared_navigation_commits_outgoing_context_and_check_forms(ui):
    controller = ContextAuthoring(ui.workspace)
    state = step(controller, ui.state, "new_context")
    first = state["context_authoring"]["selected_context_id"]
    state = step(controller, state, "new_context")
    second = state["context_authoring"]["selected_context_id"]
    state = step(controller, state, "new_check")
    first_check = state["context_authoring"]["selected_check_id"]
    state = step(controller, state, "new_check")
    contexts, checks = navigation(ui, "Context to edit"), navigation(ui, "Context check to edit")
    fields = form_for(state)
    fields[1] = "unit"
    response = checks.fn(state, *context_stamp(state), *fields, first_check)
    state = response[0]
    assert state["context_authoring"]["contexts"][1]["checks"][1]["category"] == "unit"
    assert state["context_authoring"]["selected_check_id"] == first_check
    fields = form_for(state)
    fields[1] = "name"
    response = checks.fn(state, *context_stamp(state), *fields, None)
    state = response[0]
    assert state["context_authoring"]["contexts"][1]["checks"][0]["category"] == "name"
    assert state["context_authoring"]["selected_check_id"] is None
    assert state["context_authoring"]["selected_context_id"] == second
    fields = form_for(state)
    fields[0] = "Pending second context Ω"
    response = contexts.fn(state, *context_stamp(state), *fields, first)
    state = response[0]
    assert state["context_authoring"]["contexts"][1]["reference"] == fields[0]
    assert state["context_authoring"]["selected_context_id"] == first
    assert response[6]["value"] is None and response[22:26] == [False] * 4
    fields = form_for(state)
    fields[0] = "Pending first context 😀"
    response = contexts.fn(state, *context_stamp(state), *fields, None)
    state = response[0]
    assert state["context_authoring"]["contexts"][0]["reference"] == fields[0]
    assert state["context_authoring"]["selected_context_id"] is None
    assert state["context_authoring"]["selected_check_id"] is None
    assert response[5]["value"] is None and response[7] == "" and response[26] is None


def test_real_queue_same_target_navigation_rejects_an_old_capture_after_switch(ui):
    async def scenario():
        session_hash = "generated-context-stale-navigation"
        session = ui.app.state_holder[session_hash]
        create, select = button(ui, "New context"), navigation(ui, "Context to edit")
        state_id = create.inputs[0]._id
        session[state_id] = copy.deepcopy(ui.state)
        identifiers = []
        for _ in range(2):
            current = session[state_id]
            await _process(ui, create, [None, *context_stamp(current), *form_for(current)], session, session_hash)
            identifiers.append(session[state_id]["context_authoring"]["selected_context_id"])
        current = session[state_id]
        captured = [None, *context_stamp(current), *form_for(current), identifiers[0]]
        first = await _capture_queued(ui, select, captured, session_hash=session_hash)
        late = await _capture_queued(ui, select, captured, session_hash=session_hash)
        await _process(ui, first.fn, first.data.data, session, session_hash)
        before = copy.deepcopy(session[state_id])
        assert before["context_authoring"]["selected_context_id"] == identifiers[0]
        with pytest.raises(Exception, match="Context action failed"):
            await _process(ui, late.fn, late.data.data, session, session_hash)
        assert session[state_id] == before

    asyncio.run(scenario())


def test_dropdown_switch_preserves_reference_binding_and_repeated_result_focus(editor, monkeypatch):
    from types import SimpleNamespace

    gr = pytest.importorskip("gradio")
    import ocr_context_evaluation_io
    from ocr_review_context_ui import mount_context_authoring
    from test_ocr_review_columns import _configure_download_cache

    controller, state, rendered = editor
    workspace = controller.workspace
    contexts = state["context_authoring"]["contexts"]
    duplicate = copy.deepcopy(contexts[0])
    duplicate["context_id"] += "-second"
    duplicate["correspondence"] = {"status": "ambiguous", "candidate_span": None}
    contexts.append(duplicate)
    first, second = (context["context_id"] for context in contexts)
    for identifier in (first, second):
        state = step(controller, state, "select_context", identifier)
        state = reviewed(controller, state)
        state = step(controller, state, "review_mapping", True)
    state = step(controller, state, "export_reference", True)
    reference = state["_context_ui"]["exports"]["reference"]
    retained = reference.path.read_bytes()
    _configure_download_cache(workspace, monkeypatch)
    monkeypatch.setenv("GRADIO_ANALYTICS_ENABLED", "False")
    with gr.Blocks() as app:
        state_component = gr.State(copy.deepcopy(state))
        mount_context_authoring(gr, workspace, state_component, state, app=app)
    panel = SimpleNamespace(app=app)
    select_context = navigation(panel, "Context to edit")
    select_result = navigation(panel, "Context evaluation result to inspect")
    try:
        owner = state["_context_ui"]["owner"]
        response = select_context.fn(state, *context_stamp(state), *form_for(state), first)
        state = response[0]
        assert state["context_authoring"]["selected_context_id"] == first
        assert state["context_authoring"]["selected_check_id"] is None
        assert state["_context_ui"]["source_view"] is None
        assert state["_context_ui"]["exports"]["reference"] is reference
        assert state["_context_ui"]["owner"] == owner and response[27] == gr.skip()
        state = step(controller, state, "export_mapping", True)
        state = step(controller, state, "evaluate")
        exports = state["_context_ui"]["exports"].copy()
        files = {binding.path: binding.path.read_bytes() for binding in exports.values()}
        monkeypatch.setattr(ocr_context_evaluation_io, "evaluate_context_files",
                            lambda *_a, **_k: pytest.fail("result navigation repeated scoring"))
        before_renders = len(rendered)
        for _ in range(2):
            response = select_result.fn(state, *context_stamp(state), *form_for(state), "1:1")
            state = response[0]
            response[26].close()
            assert state["context_authoring"]["selected_context_id"] == first
            assert state["context_authoring"]["selected_check_id"] == contexts[0]["checks"][0]["check_id"]
            assert response[-1]["value"] is None and response[27] == gr.skip()
            assert all(state["_context_ui"]["exports"][key] is binding for key, binding in exports.items())
            assert select_result.fn(state, *context_stamp(state), *form_for(state), None) == [gr.skip()] * len(response)
        assert len(rendered) == before_renders + 2
        assert reference.path.read_bytes() == retained
        assert set(workspace.output_dir.glob("ocr-context-*.json")) == set(files)
        assert all(path.read_bytes() == raw for path, raw in files.items())
    finally:
        app.close()



def test_duplicate_candidate_selection_keeps_reviews_exports_and_required_validation(editor, monkeypatch):
    import ocr_review_context_ui as context_ui

    controller, state, rendered = editor
    state = reviewed(controller, state)
    for action in ("review_mapping", "export_reference", "export_mapping"):
        state = step(controller, state, action, True)
    state = step(controller, state, "evaluate")
    candidate = controller.candidate_text(1)
    stamps, fields, before = context_stamp(state), form_for(state), copy.deepcopy(state)
    exports = state["_context_ui"]["exports"].copy()
    files = {binding.path: binding.path.read_bytes() for binding in exports.values()}
    rendered_before = list(rendered)
    calls = []
    verify, validate = controller.workspace.verify_inputs, context_ui.validate_authoring

    def verified():
        calls.append("verify")
        return verify()

    def validated(*args):
        calls.append("validate")
        return validate(*args)

    with monkeypatch.context() as scoped:
        scoped.setattr(controller.workspace, "verify_inputs", verified)
        scoped.setattr(context_ui, "validate_authoring", validated)
        scoped.setattr(controller, "_revoke", lambda *_a, **_k: pytest.fail("duplicate revoked review"))
        scoped.setattr(context_ui, "uuid4", lambda: pytest.fail("duplicate replaced stamps"))
        response = controller.transition(state, stamps, fields, "candidate_selection", ([0, len(candidate)], candidate, candidate))
    assert response[0] is state and response[1:] == (None, False, None, "Context draft stored.")
    assert calls == ["verify", "validate"] and state == before and context_stamp(state) == stamps
    assert all(state["_context_ui"]["exports"][key] is binding for key, binding in exports.items())
    assert rendered == rendered_before and set(controller.workspace.output_dir.glob("ocr-context-*.json")) == set(files)
    assert all(path.read_bytes() == raw for path, raw in files.items())


def test_duplicate_candidate_callback_skips_outputs_without_staling_captured_review(editor, monkeypatch):
    from types import SimpleNamespace

    gr = pytest.importorskip("gradio")
    import ocr_review_context_ui as context_ui
    from test_ocr_review_columns import _configure_download_cache

    controller, state, _ = editor
    state = reviewed(controller, state)
    candidate = controller.candidate_text(1)
    _configure_download_cache(controller.workspace, monkeypatch)
    monkeypatch.setenv("GRADIO_ANALYTICS_ENABLED", "False")
    with gr.Blocks() as app:
        state_component = gr.State(copy.deepcopy(state))
        context_ui.mount_context_authoring(gr, controller.workspace, state_component, state, app=app)
    panel = SimpleNamespace(app=app)
    field = next(item for item in app.blocks.values() if getattr(item, "elem_id", None) == "ocr-context-candidate")
    selection = next(block for block in app.fns.values() if block.fn and block.fn.__name__ == "context_selection"
                     and block.inputs[-1] is field)
    review = button(panel, "Review current context correspondence")
    stamps, fields = context_stamp(state), form_for(state)
    event = gr.SelectData(None, {"index": [0, len(candidate)], "value": candidate})

    async def scenario():
        session_hash = "generated-duplicate-candidate-review"
        session = app.state_holder[session_hash]
        session[state_component._id] = state
        captured = await _capture_queued(panel, review, [None, *stamps, *fields, True], session_hash=session_hash)
        with monkeypatch.context() as scoped:
            scoped.setattr(context_ui, "preview_check", lambda *_a, **_k: pytest.fail("duplicate rendered the form"))
            assert selection.fn(state, event, *stamps, *fields, candidate) == [gr.skip()] * len(selection.outputs)
            response = await app.process_api(selection, inputs=[None, *stamps, *fields, candidate], state=session,
                                             session_hash=session_hash, event_data=event)
        assert response["data"][1:] == [gr.skip()] * (len(selection.outputs) - 1)
        assert session[state_component._id] is state and context_stamp(state) == stamps
        assert response["data"][22:26] == [gr.skip()] * 4  # Pending native confirmations receive no replacement value.
        await _process(panel, captured.fn, captured.data.data, session, session_hash)
        current = session[state_component._id]
        assert context_stamp(current) != stamps
        identifier = current["context_authoring"]["selected_context_id"]
        assert current["_context_ui"]["mapping_reviews"][identifier] == controller._digest(
            current["context_authoring"]["contexts"][0], mapping=True)

    try:
        asyncio.run(scenario())
    finally:
        app.close()


@pytest.mark.parametrize("pending_reference", [False, True])
def test_candidate_selection_changed_form_or_span_still_commits_and_revokes(editor, pending_reference):
    controller, state, _ = editor
    state = reviewed(controller, state)
    for action in ("review_mapping", "export_reference", "export_mapping"):
        state = step(controller, state, action, True)
    candidate = controller.candidate_text(1)
    before, stamps, fields = copy.deepcopy(state), context_stamp(state), form_for(state)
    reference_binding = state["_context_ui"]["exports"]["reference"]
    if pending_reference:
        fields[0] += " changed"
        start, end = 0, len(candidate)
    else:
        start, end = candidate.index("10"), candidate.index("10") + 2
    updated = step(controller, state, "candidate_selection", ([start, end], candidate[start:end], candidate), form=fields)
    context = updated["context_authoring"]["contexts"][0]
    assert updated is not state and context_stamp(updated) != stamps and state == before
    assert context["correspondence"] == {"status": "mapped", "candidate_span": [start, end]}
    assert updated["_context_ui"]["mapping_reviews"] == {}
    if pending_reference:
        assert context["reference"] == fields[0] and context["checks"][0]["reference_span"] is None
        assert updated["_context_ui"]["reference_reviews"] == {} and updated["_context_ui"]["exports"] == {}
    else:
        assert updated["_context_ui"]["reference_reviews"] == state["_context_ui"]["reference_reviews"]
        assert updated["_context_ui"]["exports"] == {"reference": reference_binding}


@pytest.mark.parametrize("case", ["view", "display", "context", "check", "displayed", "selected", "index", "page", "sibling", "source"])
def test_duplicate_candidate_selection_still_rejects_stale_forged_or_invalid_inputs(editor, monkeypatch, case):
    controller, state, _ = editor
    state = reviewed(controller, state)
    candidate = controller.candidate_text(1)
    stamps, fields = context_stamp(state), form_for(state)
    argument = [[0, len(candidate)], candidate, candidate]
    if case in ("view", "display", "context", "check"):
        stamps[("view", "display", "context", "check").index(case)] = "old captured value"
    elif case == "displayed":
        argument[2] += " forged"
    elif case == "selected":
        argument[1] += " forged"
    elif case == "index":
        argument[0][0] = True
    elif case == "page":
        fields[12] = 2
    elif case == "sibling":
        sibling = copy.deepcopy(state["context_authoring"]["contexts"][0])
        sibling.update(context_id="malformed_sibling", correspondence={"status": "unreviewed", "candidate_span": None}, unexpected=True)
        state["context_authoring"]["contexts"].append(sibling)
    else:
        def refuse_source():
            raise RuntimeError("generated source verification refusal")
        monkeypatch.setattr(controller.workspace, "verify_inputs", refuse_source)
    before = copy.deepcopy(state)
    with pytest.raises(RuntimeError if case == "source" else ValueError):
        controller.transition(state, stamps, fields, "candidate_selection", argument)
    assert state == before
