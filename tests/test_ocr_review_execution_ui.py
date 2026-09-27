"""Installed Gradio controls with deterministic coordinators; no native OCR."""

from __future__ import annotations

import asyncio
import copy
import threading
from types import SimpleNamespace

import pytest

import ocr_review_execution_ui as panel
from ocr_regions import validate_region_plan


SOURCE = "a" * 64
RECOVERY = "b" * 64
CONTROLS = ["pages", "current", [], 300, "none", 0, "none", 0.0, "none"]


class Coordinator:
    def __init__(self):
        self.prepares = []
        self.starts = []
        self.cancels = []
        self.results = []
        self.revocations = 0
        self.prepared = None
        self.before_prepare = None
        self.before_result = None
        self.status_value = "running"
        self.outcome = "review_required"
        self.text = "New candidate; do not adopt."
        self.baseline_text = "Saved original candidate."
        self.known_runs = set()

    def prepare(self, **options):
        self.prepares.append(copy.deepcopy(options))
        if self.before_prepare:
            self.before_prepare()
        token = str(len(self.prepares))
        self.prepared = {"intent_id": "run-" + token, "approval_token": "approval-" + token,
                         "intent_sha256": "c" * 64, "status": "awaiting_approval",
                         "operation": options["operation"],
                         "selection": {key: options[key] for key in ("pages", "regions") if key in options},
                         "configuration": {"dpi": options["dpi"], "preprocessing": options["preprocessing"]},
                         "requires_attention": True}
        return copy.deepcopy(self.prepared)

    def invalidate_pending(self):
        self.revocations += 1
        self.prepared = None

    def start(self, intent_id, approval_token, *, confirmed):
        assert self.prepared and self.prepared["intent_id"] == intent_id
        assert self.prepared["approval_token"] == approval_token and confirmed is True
        self.starts.append((intent_id, approval_token))
        self.operation = self.prepared["operation"]
        self.prepared = None
        self.known_runs.add(intent_id)
        return self.status(intent_id)

    def status(self, run_id):
        if run_id not in self.known_runs:
            raise ValueError("unknown run")
        return {"run_id": run_id, "operation": getattr(self, "operation", "pages"),
                "phase": "terminal" if self.status_value != "running" else "running",
                "process_status": self.status_value,
                "artifact_state": "verified_complete" if self.status_value == "completed" else "incomplete",
                "cancellation_requested": bool(self.cancels), "actual_calls": None,
                "summary": None, "notice": "NEVER_SHOW_PRIVATE_BACKEND_NOTICE", "requires_attention": True}

    def cancel(self, run_id):
        self.cancels.append(run_id)
        return self.status(run_id)

    def result(self, run_id, *, item_id=None):
        self.results.append((run_id, item_id))
        if self.before_result:
            self.before_result()
        if self.status_value != "completed":
            raise ValueError("PRIVATE_PATH_AND_TEXT")
        available = self.outcome in ("review_required", "empty_candidate")
        row = {"page_number": 1, "status": self.outcome,
               "candidate": {"text": self.text} if available else None}
        baseline = {"page_number": 1, "candidate": {"text": self.baseline_text}, "original_text": "Native context."}
        selected = None if item_id is None else {
            "diagnostic": {"item_id": "page-00001", "page_number": 1, "legacy_status": self.outcome},
            "report_record": None if self.outcome in ("deferred", "not_selected") else row,
            "baseline_page": baseline}
        return {"run_id": run_id, "operation": self.operation, "request_sha256": "d" * 64,
                "source_sha256": SOURCE, "baseline_recovery_sha256": RECOVERY, "report_sha256": "e" * 64,
                "execution_sha256": "f" * 64, "disposition_sha256": "c" * 64, "summary": {},
                "item_ids": [{"item_id": "page-00001", "page_number": 1, "region_id": None}],
                "selected_item": selected, "requires_attention": True}


@pytest.fixture
def ui(monkeypatch):
    monkeypatch.setenv("GRADIO_ANALYTICS_ENABLED", "False")
    gr = pytest.importorskip("gradio")
    document = SimpleNamespace(page_count=30, source_sha256=SOURCE, recovery_sha256=RECOVERY)
    workspace = SimpleNamespace(document=document)
    workspace.region_plan = lambda rows: validate_region_plan({"schema_version": 1, "source_sha256": SOURCE,
        "recovery_sha256": RECOVERY, "coordinate_system": "original_page_display_fraction", "regions": rows},
        source_sha256=SOURCE, recovery_sha256=RECOVERY, page_count=30)
    review = {"page": 1, "annotation_context": "initial-annotation", "regions": []}
    coordinator = Coordinator()
    with gr.Blocks() as app:
        state = gr.State(copy.deepcopy(review))
        context = gr.Textbox(value=review["annotation_context"], visible=False)
        assert panel.build_execution_panel(workspace, coordinator, review_state=state, annotation_view=context) is None
    functions = {block.fn.__name__: block.fn for block in app.fns.values() if block.fn is not None}
    return SimpleNamespace(app=app, functions=functions, coordinator=coordinator, workspace=workspace,
                           review=review, review_component=state, annotation_component=context,
                           session=panel._PanelState())


def prepare(ui, controls=None):
    return ui.functions["prepare_run"](ui.session, ui.review, ui.review["annotation_context"], *(controls or CONTROLS))


def start(ui, token, controls=None, confirmed=True):
    return ui.functions["start_run"](ui.session, ui.review, ui.review["annotation_context"], token,
                                     confirmed, *(controls or CONTROLS))


def ready(ui, controls=None):
    token, checked, preview = prepare(ui, controls)
    assert checked is False and "baseline_recovery_sha256" in preview
    result = start(ui, token, controls)
    assert result[0:2] == ("", False)
    return "run-1"


def block(ui, name):
    return next(entry for entry in ui.app.fns.values() if entry.fn and entry.fn.__name__ == name)


def test_private_endpoints_distinct_queues_and_responsive_controls(ui):
    assert all(row["api_visibility"] == "private" for row in ui.app.config["dependencies"])
    for name in ("start_run", "poll_run", "cancel_run", "invalidate_run", "select_run"):
        assert block(ui, name).queue is False
    assert block(ui, "prepare_run").concurrency_id == "ocr-review-prepare"
    assert block(ui, "load_results").concurrency_id == "ocr-review-results"
    assert all(not row.get("trigger_after") for row in ui.app.config["dependencies"])
    assert not any(type(component).__name__ in ("File", "HTML", "BrowserState") for component in ui.app.blocks.values())
    checkboxes = [component for component in ui.app.blocks.values() if type(component).__name__ == "Checkbox"]
    assert len(checkboxes) == 2  # Independent execution and crop-reference consent.
    assert all(component.value is False for component in checkboxes)


def test_every_config_and_annotation_change_only_revokes(ui):
    deps = ui.app.config["dependencies"]
    invalidations = [entry for entry in ui.app.fns.values() if entry.fn and entry.fn.__name__ in ("invalidate_run", "change_route")]
    assert len(invalidations) == 10
    prepare(ui)
    assert ui.functions["invalidate_run"](ui.session) == ("", False, "Scope or configuration changed. Prepare a new run.")
    assert ui.session.pending is None and not ui.coordinator.starts
    for entry in invalidations:
        assert [type(component).__name__ for component in entry.outputs[:3]] == ["Textbox", "Checkbox", "Textbox"]
    assert any((ui.annotation_component._id, "change") in map(tuple, row["targets"]) for row in deps)


def test_one_use_start_keeps_baseline_and_review_state_immutable(ui):
    original = copy.deepcopy(ui.review)
    document = ui.workspace.document
    token, _, _ = prepare(ui)
    start(ui, token)
    with pytest.raises(Exception, match="Run control failed"):
        start(ui, token)
    assert len(ui.coordinator.starts) == 1
    assert ui.review == original and ui.workspace.document is document
    assert "NEVER_SHOW" not in ui.functions["poll_run"](ui.session, "run-1")


@pytest.mark.parametrize("confirmed", [False, None, 0, 1, "true"])
def test_explicit_boolean_confirmation_required(ui, confirmed):
    token, _, _ = prepare(ui)
    with pytest.raises(Exception, match="Run control failed"):
        start(ui, token, confirmed=confirmed)
    assert not ui.coordinator.starts


@pytest.mark.parametrize("change", ["page", "annotation", "regions", "dpi", "recipe", "pages", "operation", "scope", "preprocessing"])
def test_captured_stale_configuration_rejected_before_any_start(ui, change):
    token, _, _ = prepare(ui)
    controls = copy.deepcopy(CONTROLS)
    if change == "page":
        ui.review["page"] = 2
    elif change == "annotation":
        ui.review["annotation_context"] = "new-annotation"
    elif change == "regions":
        ui.review["regions"] = [{"region_id": "crop-1", "page_number": 1, "bbox": [.1, .1, .4, .4]}]
    else:
        index, value = {"dpi": (3, 400), "recipe": (5, 90), "pages": (2, [2]), "operation": (0, "regions"),
                        "scope": (1, "explicit"), "preprocessing": (4, "deskew")}[change]
        controls[index] = value
    with pytest.raises(Exception, match="Run control failed"):
        start(ui, token, controls)
    assert not ui.coordinator.starts


@pytest.mark.parametrize("operation", ["regions", "hardscan"])
def test_current_draft_crops_and_explicit_hardscan_recipe_are_prepared(ui, operation):
    ui.review["regions"] = [{"region_id": "crop-2", "page_number": 2, "bbox": [.2, .3, .7, .8]},
                            {"region_id": "crop-1", "page_number": 1, "bbox": [.1, .1, .5, .5]}]
    controls = copy.deepcopy(CONTROLS)
    controls[0] = operation
    if operation == "hardscan":
        controls[5:] = [90, "background-normalize-v1", .015, "parallel_horizontal_baselines"]
    ready(ui, controls)
    requested = ui.coordinator.prepares[0]
    assert [r["region_id"] for r in requested["regions"]] == ["crop-1", "crop-2"]
    assert requested["regions"][0]["bbox"] == [.1, .1, .5, .5]
    if operation == "hardscan":
        assert requested["regions"][0]["recipe"] == {"orientation_clockwise": 90,
            "illumination": "background-normalize-v1", "bow_fraction": .015,
            "bow_assumption": "parallel_horizontal_baselines"}


@pytest.mark.parametrize("changes", [{0: "regions"}, {0: "hardscan"}, {2: list(range(1, 22))}, {2: [1, 1]},
                                     {2: [True]}, {3: True}, {5: 45}, {7: .026}, {4: "download-model"}])
def test_invalid_scope_never_reaches_preparation(ui, changes):
    controls = copy.deepcopy(CONTROLS)
    for index, value in changes.items():
        controls[index] = value
    with pytest.raises(Exception, match="Execution view changed"):
        prepare(ui, controls)
    assert not ui.coordinator.prepares and not ui.coordinator.starts


def test_explicit_pages_are_sorted_and_capped(ui):
    controls = copy.deepcopy(CONTROLS)
    controls[1:3] = ["explicit", [3, 1, 2]]
    ready(ui, controls)
    assert ui.coordinator.prepares[0]["pages"] == [1, 2, 3]


def test_prepare_superseded_during_hashing_cannot_restore_approval(ui):
    ui.coordinator.before_prepare = lambda: ui.functions["invalidate_run"](ui.session)
    with pytest.raises(Exception, match="Execution view changed"):
        prepare(ui)
    assert ui.session.pending is None and not ui.coordinator.starts


def test_state_copy_is_new_session_without_approval_or_runs(ui):
    ready(ui)
    fresh = copy.deepcopy(ui.session)
    assert fresh.pending is None and fresh.runs == () and fresh.generation != ui.session.generation


def test_cancel_and_status_usable_during_slow_preparation_and_after_navigation(ui):
    run_id = ready(ui)
    entered, release = threading.Event(), threading.Event()
    errors = []

    def slow_prepare():
        entered.set()
        assert release.wait(5)

    def prepare_thread():
        try:
            prepare(ui)
        except Exception as exc:
            errors.append(type(exc))

    ui.coordinator.before_prepare = slow_prepare
    thread = threading.Thread(target=prepare_thread)
    thread.start()
    try:
        assert entered.wait(5)
        ui.review["page"] = 2
        ui.review["annotation_context"] = "navigated"
        assert "running" in ui.functions["poll_run"](ui.session, run_id)
        assert "True" in ui.functions["cancel_run"](ui.session, run_id)
        assert ui.coordinator.cancels == [run_id]
        ui.functions["invalidate_run"](ui.session)
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive() and errors


@pytest.mark.parametrize("operation", ["pages", "regions", "hardscan"])
@pytest.mark.parametrize("outcome", ["review_required", "empty_candidate", "retry_failed", "abstained", "deferred", "not_selected"])
def test_result_states_stay_distinct_without_crop_page_comparison(ui, operation, outcome):
    run_id = ready(ui)
    ui.coordinator.status_value = "completed"
    ui.coordinator.operation = operation
    ui.coordinator.outcome = outcome
    ui.coordinator.text = "" if outcome == "empty_candidate" else "New candidate; do not adopt."
    loaded = ui.functions["load_results"](ui.session, run_id, ui.session.result_generation)
    assert loaded[1] == ui.session.result_generation and loaded[2:5] == ("", "", "")
    before, after, diff, notice = ui.functions["show_result"](ui.session, run_id, loaded[1], "page-00001")
    assert before == "Saved original candidate." and outcome in notice
    assert "No reference-backed accuracy" in notice
    if outcome in ("review_required", "empty_candidate"):
        assert after == ui.coordinator.text
    else:
        assert after == "" and "Candidate unavailable" in notice
    if operation != "pages":
        assert "not a crop baseline or reference" in diff
    elif outcome == "review_required":
        assert "-Saved original candidate." in diff and "+New candidate" in diff


def test_unverified_result_is_not_loaded_and_errors_are_static(ui):
    run_id = ready(ui)
    with pytest.raises(Exception, match="Run control failed") as caught:
        ui.functions["load_results"](ui.session, run_id, ui.session.result_generation)
    assert "PRIVATE" not in str(caught.value)


def test_status_explicitly_discloses_cached_observation_not_fresh_verification(ui):
    run_id = ready(ui)
    ui.coordinator.status_value = "completed"
    text = ui.functions["poll_run"](ui.session, run_id)
    assert "last observed artifacts verified_complete" in text
    assert "last verified actual calls unknown" in text
    assert "Status does not recheck files" in text and "fresh verification" in text


def test_selected_text_bound_retains_original_and_never_truncates_as_complete(ui):
    run_id = ready(ui)
    ui.coordinator.status_value = "completed"
    ui.coordinator.text = "x" * 100_001
    with pytest.raises(Exception, match="Run control failed"):
        ui.functions["show_result"](ui.session, run_id, ui.session.result_generation, "page-00001")
    assert ui.workspace.document.recovery_sha256 == RECOVERY


def test_cancel_or_result_cannot_choose_another_sessions_arbitrary_run(ui):
    for function, extra in (("poll_run", []), ("cancel_run", []), ("load_results", ["other-token"]),
                            ("show_result", ["other-run", "page-00001"])):
        with pytest.raises(Exception, match="Run control failed"):
            ui.functions[function](ui.session, "other-run", *extra)
    assert not ui.coordinator.cancels and not ui.coordinator.results


def test_stale_result_readback_cannot_overwrite_new_run_selection(ui):
    run_id = ready(ui)
    ui.coordinator.status_value = "completed"
    token, _, _ = prepare(ui)
    start(ui, token)
    ui.functions["select_run"](ui.session, run_id, ui.session.result_generation)
    captured = ui.session.result_generation
    ui.coordinator.before_result = lambda: ui.functions["select_run"](ui.session, "run-2", ui.session.result_generation)
    with pytest.raises(Exception, match="Run control failed"):
        ui.functions["load_results"](ui.session, run_id, captured)


def test_inactive_hardscan_cross_field_assumption_does_not_block_pages(ui):
    controls = copy.deepcopy(CONTROLS)
    controls[7] = .01
    ready(ui, controls)
    assert len(ui.coordinator.starts) == 1


@pytest.mark.parametrize("operation,scope,visible", [
    ("pages", "current", [True, False, True, False]),
    ("pages", "explicit", [True, True, True, False]),
    ("regions", "explicit", [False, False, False, False]),
    ("hardscan", "explicit", [False, False, False, True]),
])
def test_route_visibility_is_dynamic_and_only_revokes(ui, operation, scope, visible):
    prepare(ui)
    result = ui.functions["change_route"](operation, scope, ui.session)
    assert result[:2] == ("", False)
    assert [component.visible for component in result[3:]] == visible
    assert ui.session.pending is None and not ui.coordinator.starts and len(ui.coordinator.prepares) == 1


def test_default_visibility_shows_only_current_page_controls(ui):
    by_label = {getattr(component, "label", None): component for component in ui.app.blocks.values()}
    assert by_label["Page scope (pages only)"].visible is True
    assert by_label["Explicit pages (at most 20)"].visible is False
    assert by_label["Page preprocessing (pages only)"].visible is True
    container = block(ui, "change_route").outputs[-1]
    assert container.visible is False

    def descendant_ids(node):
        return {node._id} | {identifier for child in getattr(node, "children", []) for identifier in descendant_ids(child)}

    contained = descendant_ids(container)
    for label in ("Hard-scan clockwise orientation", "Hard-scan illumination", "Hard-scan bow fraction",
                  "Hard-scan bow applicability assumption"):
        assert by_label[label]._id in contained
    for label in ("Run DPI", "Page preprocessing (pages only)", "Page scope (pages only)"):
        assert by_label[label]._id not in contained


def test_inactive_page_preprocessing_is_bound_but_not_applied_to_crops(ui):
    controls = copy.deepcopy(CONTROLS)
    controls[0], controls[4] = "regions", "deskew"
    ui.review["regions"] = [{"region_id": "crop", "page_number": 1, "bbox": [.1, .1, .5, .5]}]
    token, _, _ = prepare(ui, controls)
    changed = copy.deepcopy(controls)
    changed[4] = "none"
    with pytest.raises(Exception, match="Run control failed"):
        start(ui, token, changed)
    start(ui, token, controls)
    assert ui.coordinator.prepares[0]["preprocessing"] == "none"


def test_hardscan_still_requires_explicit_bow_applicability(ui):
    controls = copy.deepcopy(CONTROLS)
    controls[0], controls[7] = "hardscan", .01
    ui.review["regions"] = [{"region_id": "crop", "page_number": 1, "bbox": [.1, .1, .5, .5]}]
    with pytest.raises(Exception, match="Execution view changed"):
        prepare(ui, controls)
    assert not ui.coordinator.prepares


@pytest.mark.parametrize("action", ["load_results", "show_result", "select_run"])
def test_old_result_token_cannot_reactivate_prior_run(ui, action):
    run_id = ready(ui)
    ui.coordinator.status_value = "completed"
    old_token = ui.session.result_generation
    ui.functions["load_results"](ui.session, run_id, old_token)
    current = ui.session.result_generation
    assert current != old_token
    args = [ui.session, run_id, old_token]
    if action == "show_result":
        args.append("page-00001")
    calls = len(ui.coordinator.results)
    with pytest.raises(Exception, match="Run control failed"):
        ui.functions[action](*args)
    assert ui.session.result_generation == current and len(ui.coordinator.results) == calls


def test_show_result_change_during_readback_does_not_repaint_old_selection(ui):
    run_id = ready(ui)
    ui.coordinator.status_value = "completed"
    old_token = ui.session.result_generation
    ui.coordinator.before_result = lambda: ui.functions["select_run"](ui.session, run_id, ui.session.result_generation)
    with pytest.raises(Exception, match="Run control failed"):
        ui.functions["show_result"](ui.session, run_id, old_token, "page-00001")
    assert ui.session.result_generation != old_token


async def capture(ui, function, data, session_hash):
    from fastapi import Request
    from gradio.data_classes import PredictBodyInternal
    request = Request({"type": "http", "method": "POST", "path": "/gradio_api/queue/join", "root_path": "",
                       "headers": [], "query_string": b"", "scheme": "http", "server": ("127.0.0.1", 7860),
                       "client": ("127.0.0.1", 1)})
    index = next(index for index, entry in ui.app.fns.items() if entry is function)
    body = PredictBodyInternal(data=copy.deepcopy(data), fn_index=index, session_hash=session_hash, request=request)
    accepted, event_id, status = await ui.app._queue.push(body, request, None)
    assert accepted and status == "success"
    return ui.app._queue.event_ids_to_events[event_id]


@pytest.mark.parametrize("mutation", ["annotation", "page", "regions", "config"])
def test_installed_queue_old_confirmation_with_new_live_state_cannot_execute(ui, mutation):
    async def scenario():
        session_hash = "synthetic-run-consent"
        session = ui.app.state_holder[session_hash]
        prepare_block = block(ui, "prepare_run")
        session[prepare_block.inputs[0]._id] = ui.session
        session[ui.review_component._id] = copy.deepcopy(ui.review)
        prepared = await ui.app.process_api(prepare_block,
            inputs=[None, None, ui.review["annotation_context"], *CONTROLS], state=session, session_hash=session_hash)
        token = prepared["data"][0]
        action = block(ui, "start_run")
        queued = await capture(ui, action, [None, None, ui.review["annotation_context"], token, True, *CONTROLS], session_hash)
        current = copy.deepcopy(ui.review)
        if mutation == "annotation":
            current["annotation_context"] = "fresh-annotation"
        elif mutation == "page":
            current["page"] = 2
        elif mutation == "regions":
            current["regions"] = [{"region_id": "crop", "page_number": 1, "bbox": [.1, .1, .2, .2]}]
        else:
            await ui.app.process_api(block(ui, "invalidate_run"), inputs=[None], state=session, session_hash=session_hash)
        session[ui.review_component._id] = current
        with pytest.raises(Exception, match="Run control failed"):
            await ui.app.process_api(action, inputs=queued.data.data, state=session, session_hash=session_hash)
        assert ui.coordinator.starts == []
        assert session[ui.review_component._id] == current
    asyncio.run(scenario())


@pytest.mark.parametrize("action", ["load_results", "show_result"])
def test_installed_queue_old_result_request_cannot_reactivate_after_new_run_load(ui, action):
    async def scenario():
        session_hash = "synthetic-result-consent"
        session = ui.app.state_holder[session_hash]
        preparation = block(ui, "prepare_run")
        session[preparation.inputs[0]._id] = ui.session
        session[ui.review_component._id] = copy.deepcopy(ui.review)

        async def invoke(name, data):
            return await ui.app.process_api(block(ui, name), inputs=data, state=session, session_hash=session_hash)

        async def launch():
            prepared = await invoke("prepare_run", [None, None, ui.review["annotation_context"], *CONTROLS])
            return await invoke("start_run", [None, None, ui.review["annotation_context"], prepared["data"][0], True, *CONTROLS])

        first = await launch()
        ui.coordinator.status_value = "completed"
        loaded_a = await invoke("load_results", [None, "run-1", first["data"][4]])
        old_data = [None, "run-1", loaded_a["data"][1]]
        if action == "show_result":
            old_data.append("page-00001")
        old_request = await capture(ui, block(ui, action), old_data, session_hash)
        second = await launch()
        loaded_b = await invoke("load_results", [None, "run-2", second["data"][4]])
        latest = loaded_b["data"][1]
        calls = len(ui.coordinator.results)
        with pytest.raises(Exception, match="Run control failed"):
            await invoke(action, old_request.data.data)
        assert ui.session.display_run == "run-2" and ui.session.result_generation == latest
        assert len(ui.coordinator.results) == calls
    asyncio.run(scenario())


def test_installed_start_clears_previously_shown_results_atomically(ui):
    async def scenario():
        session_hash = "synthetic-clear-old-results"
        session = ui.app.state_holder[session_hash]
        session[block(ui, "prepare_run").inputs[0]._id] = ui.session
        session[ui.review_component._id] = copy.deepcopy(ui.review)

        async def invoke(name, data):
            return await ui.app.process_api(block(ui, name), inputs=data, state=session, session_hash=session_hash)

        async def launch():
            prepared = await invoke("prepare_run", [None, None, ui.review["annotation_context"], *CONTROLS])
            return await invoke("start_run", [None, None, ui.review["annotation_context"], prepared["data"][0], True, *CONTROLS])

        first = await launch()
        ui.coordinator.status_value = "completed"
        loaded = await invoke("load_results", [None, "run-1", first["data"][4]])
        shown = await invoke("show_result", [None, "run-1", loaded["data"][1], "page-00001"])
        assert all(shown["data"])
        assert "Run run-1" in shown["data"][3]
        old_result_token = ui.session.result_generation
        result_calls = list(ui.coordinator.results)
        second = await launch()
        data = second["data"]
        assert len(data) == 10 and data[:2] == ["", False]
        assert data[2]["value"] == "run-2" and data[4] != old_result_token
        assert data[5]["choices"] == [] and data[5]["value"] is None
        assert data[6:] == ["", "", "", ""]
        assert block(ui, "start_run").outputs[5:] == [block(ui, "load_results").outputs[0],
                                                    *block(ui, "show_result").outputs]
        assert ui.coordinator.results == result_calls  # No implicit load/approval.
        assert ui.session.display_run == "run-2" and ui.session.result_generation == data[4]
        # The now-empty dropdown rejects the old item even before our token
        # guard. Separate stale-token tests exercise the callback itself.
        with pytest.raises(Exception, match="not in the list of choices"):
            await invoke("show_result", [None, "run-1", old_result_token, "page-00001"])
        assert ui.coordinator.results == result_calls
    asyncio.run(scenario())


@pytest.mark.parametrize("error_type", [RuntimeError, KeyboardInterrupt])
def test_startup_exception_recovers_only_consumed_approved_run(ui, monkeypatch, error_type):
    token, _, _ = prepare(ui)
    original = ui.coordinator.start
    lookups = []
    original_status = ui.coordinator.status

    def uncertain_start(*args, **kwargs):
        original(*args, **kwargs)
        ui.coordinator.status_value = "cleanup_unconfirmed"
        raise error_type("PRIVATE_HANDOFF_ERROR")

    def status(run_id):
        lookups.append(run_id)
        return original_status(run_id)

    monkeypatch.setattr(ui.coordinator, "start", uncertain_start)
    monkeypatch.setattr(ui.coordinator, "status", status)
    result = start(ui, token)
    assert ui.session.runs == ("run-1",) and ui.session.pending is None
    assert result[:2] == ("", False) and result[5].choices == [] and result[6:] == ("", "", "", "")
    assert "cleanup_unconfirmed" in result[3] and "OCR may already have executed" in result[3]
    assert "PRIVATE" not in result[3] and set(lookups) == {"run-1"}
    assert "cleanup_unconfirmed" in ui.functions["poll_run"](ui.session, "run-1")
    ui.functions["cancel_run"](ui.session, "run-1")
    assert ui.coordinator.cancels == ["run-1"]
    with pytest.raises(Exception, match="Run control failed"):
        start(ui, token)
    assert len(ui.coordinator.starts) == 1


@pytest.mark.parametrize("error_type", [RuntimeError, KeyboardInterrupt])
def test_startup_exception_without_matching_retained_run_grants_no_capability(ui, monkeypatch, error_type):
    token, _, _ = prepare(ui)
    lookups = []

    def failed_start(*args, **kwargs):
        raise error_type("PRIVATE_START_ERROR")

    def status(run_id):
        lookups.append(run_id)
        raise ValueError("PRIVATE_MISSING_RUN")

    monkeypatch.setattr(ui.coordinator, "start", failed_start)
    monkeypatch.setattr(ui.coordinator, "status", status)
    with pytest.raises(Exception, match="Run control failed") as caught:
        start(ui, token)
    assert "PRIVATE" not in str(caught.value)
    assert lookups == ["run-1"] and ui.session.pending is None and ui.session.runs == ()


@pytest.mark.parametrize("recover", [False, True])
def test_mismatched_start_or_recovery_view_cannot_grant_another_run(ui, monkeypatch, recover):
    token, _, _ = prepare(ui)
    view = {"run_id": "other-run", "operation": "pages", "phase": "running", "process_status": "running",
            "artifact_state": "absent", "cancellation_requested": False, "actual_calls": None,
            "requires_attention": True}

    def start_other(*args, **kwargs):
        if recover:
            raise RuntimeError("PRIVATE_FAILURE")
        return view

    monkeypatch.setattr(ui.coordinator, "start", start_other)
    monkeypatch.setattr(ui.coordinator, "status", lambda run_id: view)
    with pytest.raises(Exception, match="Run control failed"):
        start(ui, token)
    assert ui.session.pending is None and ui.session.runs == ()
