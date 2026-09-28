"""Full review app -> coordinator -> strict bundles over inert OCR sessions.

Installed Gradio preprocessing/postprocessing, source snapshots, one-use intents,
worker CLI composition and receipt validators are real. Supervision, PDF pixels,
native inference and producer/model-verification observations are controlled
fixtures; these are not browser, containment or recognition-accuracy experiments.
"""

import asyncio
import copy
import json
import threading
from types import SimpleNamespace as NS

import pytest

from ocr_review_execution import ReviewRunCoordinator
from ocr_review_runtime import ReviewWorkspace
from test_ocr_detection_disposition_runtime import stack as stack, harness as harness, upstream as upstream
from test_ocr_disposition_bundle_routes import routes as routes
from test_ocr_review_execution import _fake_worker, preview_roots as preview_roots


CONTROLS = ["pages", "current", [], 300, "none", 0, "none", 0.0, "none"]


@pytest.fixture
def integrated(routes, tmp_path, monkeypatch, preview_roots):
    monkeypatch.setenv("GRADIO_ANALYTICS_ENABLED", "False")
    pytest.importorskip("gradio")
    Image = pytest.importorskip("PIL.Image")
    from ocr_review_ui import build_app

    workspace = ReviewWorkspace(routes.source, routes.recovery_path, tmp_path)
    monkeypatch.setattr(workspace, "render", lambda _page: Image.new("RGB", (64, 64), "white"))
    coordinator = ReviewRunCoordinator(workspace)
    app, host = None, None
    try:
        assert coordinator.preview_private_root.parent == preview_roots
        app = build_app(workspace, execution_coordinator=coordinator)
        blocks = {entry.fn.__name__: entry for entry in app.fns.values() if entry.fn is not None}
        review_id = blocks["prepare_run"].inputs[1]._id
        initial = copy.deepcopy(app.blocks[review_id].value)
        initial["page"] = 1
        initial["regions"] = [{"region_id": "same-crop", "page_number": 1, "bbox": [0., 0., 1., 1.]}]
        session_hash = "inert-full-review-integration"
        state = app.state_holder[session_hash]
        state[review_id] = copy.deepcopy(initial)
        observed = _fake_worker(monkeypatch)
        host = NS(app=app, blocks=blocks, state=state, session_hash=session_hash,
                  review_id=review_id, initial=initial, coordinator=coordinator,
                  workspace=workspace, routes=routes, observed=observed, expected_cleanup=True)
        yield host
    finally:
        try:
            expected = True if host is None else host.expected_cleanup
            assert coordinator.close()["cleanup_confirmed"] is expected
        finally:
            if app is not None:
                app.close()


async def invoke(host, name, data, *, state=None, session_hash=None):
    result = await host.app.process_api(host.blocks[name], inputs=data,
        state=host.state if state is None else state,
        session_hash=host.session_hash if session_hash is None else session_hash)
    return result["data"]


async def prepared(host, controls):
    response = await invoke(host, "prepare_run", [None, None, host.initial["annotation_context"], *controls])
    assert response[1] is False
    scope = json.loads(response[2])
    assert scope["source_sha256"] == host.workspace.document.source_sha256
    assert scope["baseline_recovery_sha256"] == host.workspace.document.recovery_sha256
    assert not host.observed
    assert not list(host.workspace.output_dir.glob("ocr-run-*"))
    return response[0], scope


async def started(host, token, controls):
    response = await invoke(host, "start_run",
        [None, None, host.initial["annotation_context"], token, True, *controls])
    assert response[:2] == ["", False]
    return response[2]["value"], response[4]


def terminal(host, run_id):
    run = host.coordinator._runs[run_id]
    run.thread.join(15)
    assert not run.thread.is_alive()
    view = host.coordinator.status(run_id)
    assert view["phase"] == "terminal", view
    return view


@pytest.mark.parametrize("operation", ["pages", "regions", "hardscan"])
def test_installed_full_app_all_routes_prepare_execute_verify_display(integrated, operation):
    host = integrated
    document = host.workspace.document
    before = host.workspace.recovery_path.read_bytes()
    controls = copy.deepcopy(CONTROLS)
    controls[0] = operation
    if operation == "hardscan":
        controls[5] = 90  # Explicit quarter turn; inert fixture has a square canvas.

    async def scenario():
        token, scope = await prepared(host, controls)
        if operation == "pages":
            assert scope["selection"] == {"pages": [1]}
        else:
            assert scope["selection"]["regions"][0]["bbox"] == [0., 0., 1., 1.]
            if operation == "hardscan":
                assert scope["selection"]["regions"][0]["recipe"]["orientation_clockwise"] == 90
        run_id, context = await started(host, token, controls)
        view = terminal(host, run_id)
        assert (view["process_status"], view["artifact_state"], view["actual_calls"]) == (
            "completed", "verified_complete", 1)
        assert len(host.observed) == 1 and host.routes.stack.raw.calls == 1
        assert "--worker" in host.observed[0][1]
        loaded = await invoke(host, "load_results", [None, run_id, context])
        choices = loaded[0]["choices"]
        assert len(choices) == (3 if operation == "pages" else 1)
        item_id = choices[0][1]
        shown = await invoke(host, "show_result", [None, run_id, loaded[1], item_id])
        result = host.coordinator.result(run_id, item_id=item_id)
        record = result["selected_item"]["report_record"]
        assert shown[1] == record["candidate"]["text"]
        assert shown[0] == host.initial_baseline_text
        assert "No reference-backed accuracy score" in shown[3]
        if operation != "pages":
            assert "Crop comparison is unavailable" in shown[2]
        with pytest.raises(Exception, match="Run control failed"):
            await started(host, token, controls)
        assert len(host.observed) == 1

    host.initial_baseline_text = document.page(1)["original_text"]
    asyncio.run(scenario())
    assert host.workspace.document is document
    assert host.workspace.recovery_path.read_bytes() == before
    assert host.state[host.review_id] == host.initial
    assert all(row["api_visibility"] == "private" for row in host.app.config["dependencies"])


@pytest.mark.parametrize("mutation", ["annotation", "page", "regions", "controls"])
def test_full_app_stale_confirmation_refuses_before_worker(integrated, mutation):
    host = integrated

    async def scenario():
        controls = copy.deepcopy(CONTROLS)
        token, _ = await prepared(host, controls)
        current = host.state[host.review_id]
        if mutation == "annotation":
            current["annotation_context"] = "changed-live-annotation"
        elif mutation == "page":
            current["page"] = 3
        elif mutation == "regions":
            current["regions"] = []
        else:
            controls[3] = 400
        with pytest.raises(Exception, match="Run control failed"):
            await started(host, token, controls)

    asyncio.run(scenario())
    assert not host.observed and host.routes.stack.raw.calls == 0
    assert not list(host.workspace.output_dir.glob("ocr-run-*"))


def test_full_app_fresh_result_readback_refuses_tampered_retained_bundle(integrated):
    host = integrated

    async def scenario():
        token, _ = await prepared(host, CONTROLS)
        run_id, context = await started(host, token, CONTROLS)
        assert terminal(host, run_id)["artifact_state"] == "verified_complete"
        loaded = await invoke(host, "load_results", [None, run_id, context])
        manifest = host.coordinator._runs[run_id].intent.output / "manifest.json"
        manifest.write_text("PRIVATE invalid replacement", encoding="utf-8")
        with pytest.raises(Exception, match="Run control failed") as caught:
            await invoke(host, "show_result", [None, run_id, loaded[1], loaded[0]["choices"][0][1]])
        assert "PRIVATE" not in str(caught.value)
        status = await invoke(host, "poll_run", [None, run_id])
        assert "Status does not recheck files" in status[0]
        assert host.routes.stack.raw.calls == 1

    asyncio.run(scenario())


def test_full_app_cancel_remains_available_during_preflight(integrated, monkeypatch):
    host = integrated
    entered, release = threading.Event(), threading.Event()
    original = host.coordinator._recheck_intent

    def blocked_preflight(intent):
        entered.set()
        assert release.wait(10)
        original(intent)

    async def scenario():
        token, _ = await prepared(host, CONTROLS)
        monkeypatch.setattr(host.coordinator, "_recheck_intent", blocked_preflight)
        run_id, _ = await started(host, token, CONTROLS)
        try:
            assert entered.wait(5)
            assert host.blocks["poll_run"].queue is False and host.blocks["cancel_run"].queue is False
            await invoke(host, "poll_run", [None, run_id])
            cancelled = await invoke(host, "cancel_run", [None, run_id])
            assert "cancellation requested True" in cancelled[0]
        finally:
            release.set()
        view = terminal(host, run_id)
        assert view["process_status"] == "cancelled" and view["actual_calls"] == 0
        assert not host.observed and host.routes.stack.raw.calls == 0

    asyncio.run(scenario())


def test_full_app_fresh_session_cannot_borrow_run_identifier(integrated):
    host = integrated

    async def scenario():
        token, _ = await prepared(host, CONTROLS)
        run_id, _ = await started(host, token, CONTROLS)
        terminal(host, run_id)
        other_hash = "other-inert-review-session"
        other_state = host.app.state_holder[other_hash]
        # Gradio rejects the foreign ID at the session-local dropdown before
        # the callback. Independently exercise the callback's own scope gate
        # too; the widget check is not our authorization boundary.
        with pytest.raises(Exception, match="not in the list of choices"):
            await invoke(host, "poll_run", [None, run_id], state=other_state, session_hash=other_hash)
        panel_id = host.blocks["poll_run"].inputs[0]._id
        with pytest.raises(Exception, match="Run control failed"):
            host.blocks["poll_run"].fn(other_state[panel_id], run_id)
        assert host.routes.stack.raw.calls == 1

    asyncio.run(scenario())


def test_full_app_retains_status_after_actual_thread_start_then_interrupt(integrated, monkeypatch):
    host = integrated
    original = threading.Thread.start

    def interrupted_start(thread):
        original(thread)
        if thread.name == "ocr-review-run":
            raise KeyboardInterrupt("synthetic interruption after thread handoff")

    async def scenario():
        token, _ = await prepared(host, CONTROLS)
        monkeypatch.setattr(threading.Thread, "start", interrupted_start)
        host.expected_cleanup = False
        response = await invoke(host, "start_run",
            [None, None, host.initial["annotation_context"], token, True, *CONTROLS])
        run_id = response[2]["value"]
        assert "recovered this approved run" in response[3]
        assert "OCR may already have executed" in response[3]
        assert response[5]["choices"] == [] and response[6:] == ["", "", "", ""]
        view = terminal(host, run_id)
        assert view["process_status"] == "cleanup_unconfirmed"
        assert "cleanup_unconfirmed" in (await invoke(host, "poll_run", [None, run_id]))[0]
        await invoke(host, "cancel_run", [None, run_id])
        assert len(host.coordinator._runs) == 1
        with pytest.raises(Exception, match="Execution view changed"):
            await invoke(host, "prepare_run", [None, None, host.initial["annotation_context"], *CONTROLS])

    asyncio.run(scenario())
