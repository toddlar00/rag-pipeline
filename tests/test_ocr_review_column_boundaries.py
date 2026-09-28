"""Adversarial consent/replay boundaries over generated, renderer-free inputs."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import ocr_column_suggestions
import ocr_recovery
from ocr_review_runtime import ReviewWorkspace
from test_ocr_review_columns import _candidate, _configure_download_cache, _export, _manual_preview, _suggest
from test_ocr_review_columns import ui as ui
from test_ocr_review_columns import workspace as workspace


def _block(ui, name):
    return next(block for block in ui.app.fns.values()
                if block.fn is not None and block.fn.__name__ == name)


def _prose_checkbox(ui):
    return next(component for component in _block(ui, "preview").inputs
                if type(component).__name__ == "Checkbox")


def _descendants(ui, parent):
    dependencies = ui.app.config["dependencies"]
    found = {parent}
    while True:
        added = {item["id"] for item in dependencies if item["trigger_after"] in found}
        if added <= found:
            return found
        found |= added


@pytest.fixture
def no_hypothesis_ui(tmp_path, monkeypatch):
    """Real fixed snapshots produce each status; only scan display is replaced."""
    monkeypatch.setenv("GRADIO_ANALYTICS_ENABLED", "False")
    pytest.importorskip("gradio")
    Image = pytest.importorskip("PIL.Image")
    import ocr_review_ui
    apps = []

    def make(kind):
        directory = tmp_path / kind
        directory.mkdir()
        source = directory / "synthetic-source.bin"
        source.write_bytes(b"Generated fixed source bytes; tests never parse or render a PDF.")
        retry_calls = []

        class Reader:
            page_count = 2

            def native_text(self, number):
                if kind == "not_selected" and number == 2:
                    return "Synthetic already-readable native content. " * 4
                return ""

            def retry(self, number):
                retry_calls.append(number)
                if kind == "retry_failed" and number == 1:
                    raise RuntimeError("Synthetic failure; not an actual OCR invocation.")
                candidate = _candidate()
                if kind == "empty_candidate" and number == 1:
                    candidate.update(text="", lines=[], mean_confidence=None)
                elif kind == "abstained" and number == 1:
                    # Two complete lines per column support a manual v1 plan,
                    # but do not satisfy the inferred proposal's denser cohort.
                    candidate["lines"] = [candidate["lines"][index] for index in (0, 1, 2, 3, 4, 7)]
                    candidate["text"] = "\n".join(line["text"] for line in candidate["lines"])
                    candidate["mean_confidence"] = sum(line["score"] for line in candidate["lines"]) / 6
                return candidate

        policy = ocr_recovery.RetryPolicy(max_pages=1 if kind == "deferred" else 2)
        requested = (1,) if kind == "not_selected" else (1, 2)
        recovery = ocr_recovery.build_recovery_report(Reader(),
            source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
            policy=policy, requested_pages=requested)
        recovery["evidence_sha256"] = None
        recovery_path = directory / "recovery.json"
        recovery_path.write_text(json.dumps(recovery), encoding="utf-8")
        fixed = ReviewWorkspace(source, recovery_path, directory)
        monkeypatch.setattr(fixed, "render", lambda _number: Image.new("RGB", (100, 100), "white"))
        _configure_download_cache(fixed, monkeypatch)
        app = ocr_review_ui.build_app(fixed)
        apps.append(app)
        functions = {block.fn.__name__: block.fn for block in app.fns.values() if block.fn is not None}
        state = copy.deepcopy(next(block.value for block in app.blocks.values() if type(block).__name__ == "State"))
        number = 2 if kind in {"deferred", "not_selected"} else 1
        if state["page"] != number:
            state = functions["change_page"](number, state, view_context=state["annotation_context"])[7]
        return SimpleNamespace(app=app, functions=functions, state=state, workspace=fixed,
                               retry_calls=retry_calls, kind=kind)

    yield make
    for app in apps:
        app.close()


@pytest.mark.parametrize("name", ["suggest_columns", "dismiss_column_suggestion"])
def test_suggestion_buttons_reset_the_actual_both_consent_components(ui, name):
    block = _block(ui, name)
    prose = _prose_checkbox(ui)
    order = next(component for component in ui.app.blocks.values()
                 if getattr(component, "label", "") == "I reviewed the numbered order and text differences")
    assert block.outputs[4]._id == order._id
    assert block.outputs[5]._id == prose._id
    pending = _suggest(ui)[1]
    result = block.fn(pending)
    assert result[4] is False and result[5] is False


@pytest.mark.parametrize("name", [
    "change_page", "select_rectangle", "clear_selection", "preview", "select_proposal_region",
    "preview_docling", "start_assignments", "assign_line", "preview_assignments", "change_tool",
])
def test_competing_routes_have_a_real_prose_reset_and_never_chain_preview(ui, name):
    """Check actual component wiring, not only False values returned by handlers."""
    prose = _prose_checkbox(ui)
    roots = [key for key, block in ui.app.fns.items()
             if block.fn is not None and block.fn.__name__ == name]
    assert roots
    for root in roots:
        children = _descendants(ui, root) - {root}
        resets = [ui.app.fns[key] for key in children
                  if any(component._id == prose._id for component in ui.app.fns[key].outputs)]
        assert resets
        for block in resets:
            if block.fn.__name__ == "sync_column_context":
                assert [type(component).__name__ for component in block.inputs] == ["State"]
                values = block.fn(copy.deepcopy(ui.state))
            else:
                assert not block.inputs
                values = block.fn()
            values = values if isinstance(values, tuple) else (values,)
            assert values[next(i for i, component in enumerate(block.outputs)
                               if component._id == prose._id)] is False
        assert all(ui.app.fns[key].fn.__name__ != "preview" for key in children)


def test_prose_checkbox_changes_invalidate_order_consent_without_preview(ui):
    prose = _prose_checkbox(ui)
    dependencies = [item for item in ui.app.config["dependencies"]
                    if any(key == prose._id for key, _ in item["targets"])]
    assert dependencies
    for item in dependencies:
        block = ui.app.fns[item["id"]]
        assert not block.inputs
        assert block.fn() is False
        assert [component.label for component in block.outputs] == [
            "I reviewed the numbered order and text differences"]
        assert all(ui.app.fns[key].fn.__name__ != "preview"
                   for key in _descendants(ui, item["id"]))


def test_pending_save_preserves_only_prior_manual_plan_and_restart_is_unconfirmed(ui):
    original = _manual_preview(ui)
    pending = _suggest(ui, original)[1]
    original_snapshot = copy.deepcopy(original)
    pending_snapshot = copy.deepcopy(pending)
    _, path, _ = ui.functions["save_draft"]("", pending, pending["annotation_context"])
    saved = json.loads(Path(path).read_text(encoding="utf-8"))
    assert saved["layouts"] == original["layouts"]
    assert saved["selections"] == original["selections"]
    assert "column_suggestion" not in saved
    assert "column_context" not in saved
    assert "prose_confirmed" not in saved and "confirmed" not in saved
    assert original == original_snapshot and pending == pending_snapshot
    reloaded = ReviewWorkspace(ui.workspace.pdf_path, ui.workspace.recovery_path, ui.workspace.output_dir,
        draft_path=Path(path), proposals_path=ui.workspace.proposals_path)
    assert reloaded.initial_draft["layouts"] == original["layouts"]
    assert reloaded.initial_draft["line_order"] == original["line_order"]
    assert reloaded.initial_draft.get("column_suggestion") is None
    assert reloaded.initial_draft.get("column_context") is None
    import ocr_review_ui
    reloaded.render = ui.workspace.render
    app = ocr_review_ui.build_app(reloaded)
    try:
        assert all(component.value is False for component in app.blocks.values()
                   if type(component).__name__ == "Checkbox")
        restored = copy.deepcopy(next(component.value for component in app.blocks.values()
                                      if type(component).__name__ == "State"))
        assert restored["column_context"] != pending["column_context"]
        assert restored["column_context"] != original["column_context"]
        for label in ("I reviewed the numbered order and text differences",
                      "I reviewed all stored Docling page orders against the scan",
                      "I reviewed every previewed assignment page against the scan"):
            export = _export(SimpleNamespace(app=app), label)
            with pytest.raises(Exception, match="view changed"):
                export(True, restored, original["column_context"])
            with pytest.raises(Exception, match="review confirmation"):
                export(False, restored, restored["column_context"])
    finally:
        app.close()


def test_valid_but_larger_cohort_cannot_be_replayed_as_current_page_hypothesis(ui):
    recovery = ui.workspace.document.recovery_snapshot()
    report = ocr_column_suggestions.build_column_suggestions(recovery,
        recovery_sha256=ui.workspace.document.recovery_sha256, requested_pages=[1, 2])
    ocr_column_suggestions.validate_column_suggestions(report, recovery=recovery,
        recovery_sha256=ui.workspace.document.recovery_sha256)
    state = _manual_preview(ui)
    state["column_suggestion"] = report
    before = copy.deepcopy(state)
    files = set(ui.workspace.output_dir.iterdir())
    with pytest.raises(Exception, match="Review action failed"):
        ui.functions["preview"](state, True, state.get("column_context"))
    assert state == before and set(ui.workspace.output_dir.iterdir()) == files


@pytest.mark.parametrize("field", [
    "recovery_sha256", "candidate_sha256", "line_order", "plan", "accuracy_verified",
    "operator_confirmation_required", "line_count", "requested_pages",
])
def test_forged_pending_report_cannot_reuse_an_existing_plan_or_approval(ui, field):
    state = _suggest(ui, _manual_preview(ui))[1]
    report = state["column_suggestion"]
    page = report["pages"][0]
    if field == "recovery_sha256":
        report[field] = "f" * 64
    elif field == "candidate_sha256":
        page[field] = "f" * 64
    elif field == "line_order":
        page[field] = list(range(page["line_count"]))
    elif field == "plan":
        page[field]["gutter"] = [.40, .45]
    elif field == "accuracy_verified":
        report[field] = True
    elif field == "operator_confirmation_required":
        report[field] = False
    elif field == "line_count":
        page[field] = float(page[field])
    else:
        report["coverage"][field] = [True]
    before = copy.deepcopy(state)
    files = set(ui.workspace.output_dir.iterdir())
    with pytest.raises(Exception, match="Review action failed"):
        ui.functions["preview"](state, True, state["column_context"])
    with pytest.raises(Exception, match="pending column suggestion"):
        _export(ui, "I reviewed the numbered order and text differences")(True, state, state["column_context"])
    assert state == before and set(ui.workspace.output_dir.iterdir()) == files


@pytest.mark.parametrize("phase", ["layout_page", "preview_layout", "draw_overlay"])
def test_mid_preview_failure_preserves_pending_and_prior_manual_state(ui, monkeypatch, phase):
    state = _suggest(ui, _manual_preview(ui))[1]
    before = copy.deepcopy(state)
    files = set(ui.workspace.output_dir.iterdir())
    calls = []

    def fail(*_args, **_kwargs):
        calls.append(phase)
        raise ValueError("private-source-and-operator-text")

    if phase == "draw_overlay":
        import ocr_review_ui
        monkeypatch.setattr(ocr_review_ui, phase, fail)
    else:
        monkeypatch.setattr(ui.workspace.document, phase, fail)
    with pytest.raises(Exception, match="Review action failed") as error:
        ui.functions["preview"](state, True, state["column_context"])
    assert "private-source-and-operator-text" not in str(error.value)
    assert calls == [phase]
    assert state == before and set(ui.workspace.output_dir.iterdir()) == files


def test_source_change_after_plan_rebuild_cannot_publish_preview_state(ui, monkeypatch):
    state = _suggest(ui, _manual_preview(ui))[1]
    before = copy.deepcopy(state)
    preview = ui.workspace.document.preview_layout

    def changed(plans):
        report = preview(plans)
        ui.workspace.pdf_path.write_bytes(b"External generation replacement; not a PDF.")
        return report

    monkeypatch.setattr(ui.workspace.document, "preview_layout", changed)
    with pytest.raises(Exception, match="Review action failed"):
        ui.functions["preview"](state, True, state["column_context"])
    assert state == before
    assert ui.workspace.pdf_path.read_bytes() == b"External generation replacement; not a PDF."
    assert not list(ui.workspace.output_dir.glob("ocr-layout-*.json"))


@pytest.mark.parametrize(("kind", "status", "candidate_state"), [
    ("abstained", "abstained", "review_required"),
    ("empty_candidate", "empty_candidate", "empty_candidate"),
    ("retry_failed", "unavailable", "retry_failed"),
    ("deferred", "unavailable", "deferred"),
    ("not_selected", "unavailable", "not_selected"),
])
def test_genuine_nonhypothesis_pages_never_materialize_a_layout(
        no_hypothesis_ui, kind, status, candidate_state):
    case = no_hypothesis_ui(kind)
    report = case.workspace.column_suggestion(case.state["page"])
    assert report["pages"][0]["status"] == status
    assert report["pages"][0]["candidate_state"] == candidate_state
    assert report["pages"][0]["plan"] is None and report["pages"][0]["line_order"] is None
    if kind in {"deferred", "not_selected"}:
        assert case.retry_calls == [1]
    before = copy.deepcopy(case.state)
    files = {path: path.read_bytes() if path.is_file() else None for path in case.workspace.output_dir.iterdir()}
    result = _suggest(case)
    state = result[1]
    assert state.get("column_suggestion") is None
    assert isinstance(state["column_context"], str) and len(state["column_context"]) == 32
    assert state["layouts"] == [] and state["line_order"] is None and state["selections"] == {}
    assert status in result[6] and "No column suggestion" in result[6]
    assert result[2] == case.workspace.document.page_text(state["page"])
    assert case.state == before
    with pytest.raises(ValueError, match="no column hypothesis"):
        case.workspace.column_suggestion_plan(state["page"], report)
    with pytest.raises(Exception, match="Review action failed"):
        case.functions["preview"](state, True, state["column_context"])
    with pytest.raises(Exception, match="Review action failed"):
        _export(case, "I reviewed the numbered order and text differences")(True, state, state["column_context"])
    assert {path: path.read_bytes() if path.is_file() else None for path in case.workspace.output_dir.iterdir()} == files


def test_genuine_inference_abstention_preserves_prior_manual_preview_without_approval(no_hypothesis_ui):
    case = no_hypothesis_ui("abstained")
    manual = _manual_preview(case)
    assert manual["layouts"] and manual["line_order"] == [0, 1, 3, 2, 4, 5]
    before = copy.deepcopy(manual)
    result = _suggest(case, manual)
    state = result[1]
    assert "abstained" in result[6]
    assert state.get("column_suggestion") is None
    for key in ("layouts", "selections", "line_order", "history", "event_count"):
        assert state[key] == manual[key]
    candidate = case.workspace.document.page(state["page"])["candidate"]
    assert result[2] == "\n".join(candidate["lines"][index]["text"] for index in manual["line_order"])
    assert result[2] != candidate["text"]
    assert result[4] is False and result[5] is False
    assert manual == before
    with pytest.raises(Exception, match="review confirmation"):
        _export(case, "I reviewed the numbered order and text differences")(False, state, state["column_context"])
    _, draft_path, _ = case.functions["save_draft"]("", state, state["annotation_context"])
    draft = json.loads(Path(draft_path).read_text(encoding="utf-8"))
    assert draft["layouts"] == manual["layouts"] and draft["selections"] == manual["selections"]
    assert "column_suggestion" not in draft
    assert "column_context" not in draft
    exported, _ = _export(case, "I reviewed the numbered order and text differences")(True, state, state["column_context"])
    assert json.loads(Path(exported).read_text(encoding="utf-8"))["pages"] == manual["layouts"]


def _button_block(ui, label):
    component = next(block for block in ui.app.blocks.values()
                     if type(block).__name__ == "Button" and block.value == label)
    event = next(item for item in ui.app.config["dependencies"]
                 if (component._id, "click") in [tuple(target) for target in item["targets"]])
    return ui.app.fns[event["id"]]


@pytest.mark.parametrize("action", [
    "suggest_columns", "dismiss_column_suggestion", "change_page", "select_rectangle_body",
    "select_rectangle_crop", "clear_selection", "preview", "select_proposal_region", "preview_docling",
    "change_tool", "start_assignments", "assign_line", "preview_assignments", "move_earlier", "move_later", "move_reset",
])
def test_every_order_transition_rotates_sticky_context_without_mutating_prior_state(ui, action):
    state = copy.deepcopy(ui.state)
    if action in {"assign_line", "preview_assignments", "move_earlier", "move_later", "move_reset"}:
        state = ui.functions["start_assignments"](state)[1]
    state = _suggest(ui, state)[1]
    before = copy.deepcopy(state)
    old_context = state["column_context"]
    if action == "change_page":
        current = ui.functions[action](2, state, view_context=state["annotation_context"])[7]
    elif action.startswith("select_rectangle_"):
        current = ui.functions["select_rectangle"](state, action.removeprefix("select_rectangle_"),
                                                    SimpleNamespace(index=[10, 20]), state["annotation_context"])[1]
    elif action == "preview":
        current = ui.functions[action](state, True, old_context)[1]
    elif action == "select_proposal_region":
        ref = ui.workspace.proposal_page(1)["regions"][0]["ref"]
        current = ui.functions[action](ref, state, state["annotation_context"])[1]
    elif action == "assign_line":
        current = ui.functions[action](1, "__retain__", state)[1]
    elif action.startswith("move_"):
        refs = [region["ref"] for region in ui.workspace.proposal_page(1)["regions"] if region["tree"] == "body"]
        label = {"move_earlier": "Move region earlier", "move_later": "Move region later",
                 "move_reset": "Restore saved Docling region order"}[action]
        function = _button_block(ui, label).fn
        current = (function(state) if action == "move_reset" else
                   function(refs[1 if action == "move_earlier" else 0], state))[1]
    elif action in {"change_tool", "clear_selection"}:
        current = ui.functions[action](state, state["annotation_context"])[1]
    else:
        current = ui.functions[action](state)[1]
    assert state == before
    context = current["column_context"]
    assert type(context) is str and len(context) == 32 and context != old_context
    assert int(context, 16) >= 0
    assert ui.functions["sync_column_context"](current) == (context, False, False, False, False)
    with pytest.raises(Exception, match="view changed"):
        ui.functions["preview"](current, True, old_context)
    with pytest.raises(Exception, match="view changed"):
        _export(ui, "I reviewed the numbered order and text differences")(True, current, None)


def test_client_context_is_nonstate_and_synchronized_with_all_order_confirmations(ui):
    preview = _block(ui, "preview")
    client = preview.inputs[2]
    assert type(client).__name__ == "Textbox" and client.stateful is False
    assert client.visible is False and client.value == ui.state["column_context"]
    assert type(client.value) is str and len(client.value) == 32
    labels = ["I reviewed the numbered order and text differences",
              "I reviewed all stored Docling page orders against the scan",
              "I reviewed every previewed assignment page against the scan"]
    for label in labels:
        export = next(block for block in ui.app.fns.values() if block.fn is not None
                      and any(getattr(component, "label", None) == label for component in block.inputs))
        assert export.inputs[2]._id == client._id
    syncs = [block for block in ui.app.fns.values()
             if block.fn is not None and block.fn.__name__ == "sync_column_context"]
    assert syncs
    for sync in syncs:
        assert [component._id for component in sync.outputs] == [
            client._id, _prose_checkbox(ui)._id,
            *[next(component._id for component in ui.app.blocks.values()
                   if getattr(component, "label", None) == label) for label in labels]]
        assert [type(component).__name__ for component in sync.inputs] == ["State"]
        untouched = copy.deepcopy(ui.state)
        assert sync.fn(untouched) == (ui.state["column_context"], False, False, False, False)
        assert untouched == ui.state


def test_every_context_sync_path_requires_success_of_original_view_transition(ui):
    dependencies = {item["id"]: item for item in ui.app.config["dependencies"]}
    syncs = [key for key, block in ui.app.fns.items()
             if block.fn is not None and block.fn.__name__ == "sync_column_context"]
    assert syncs
    for key in syncs:
        depth = 0
        while dependencies[key]["trigger_after"] is not None:
            # A failed callback/postprocess must not yield a fresh client nonce
            # through an unconditional .then or a later unconditional link.
            assert dependencies[key]["trigger_only_on_success"] is True
            key = dependencies[key]["trigger_after"]
            depth += 1
        assert depth >= 1
        assert ui.app.fns[key].fn.__name__ != "sync_column_context"


def test_late_sync_only_resets_to_current_context_and_never_restores_approval(ui):
    first = _suggest(ui)[1]
    changed = ui.functions["change_page"](2, first, view_context=first["annotation_context"])[7]
    current = _suggest(ui, changed)[1]
    before = copy.deepcopy(current)
    for _ in range(3):
        assert ui.functions["sync_column_context"](current) == (
            current["column_context"], False, False, False, False)
    assert current == before and current["layouts"] == []
    with pytest.raises(Exception, match="view changed"):
        ui.functions["preview"](current, True, first["column_context"])


def test_manual_order_consent_is_context_bound_before_any_column_suggestion(ui):
    state = copy.deepcopy(ui.state)
    state["selections"] = {"body": [0, .2, 1, .7], "gutter": [.45, .2, .55, .7]}
    original_context = state["column_context"]
    with pytest.raises(Exception, match="view changed"):
        ui.functions["preview"](state)
    result = ui.functions["preview"](state, False, original_context)
    reviewed = result[1]
    assert reviewed["column_context"] != original_context and result[4] is False
    assert reviewed["column_suggestion"] is None and reviewed["layouts"]
    export = _export(ui, "I reviewed the numbered order and text differences")
    with pytest.raises(Exception, match="view changed"):
        export(True, reviewed, original_context)
    with pytest.raises(Exception, match="review confirmation"):
        export(False, reviewed, reviewed["column_context"])
    path, _ = export(True, reviewed, reviewed["column_context"])
    assert json.loads(Path(path).read_text(encoding="utf-8"))["pages"] == reviewed["layouts"]
