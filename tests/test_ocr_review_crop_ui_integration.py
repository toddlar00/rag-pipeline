"""Full Gradio crop flow with actual coordinator/bundles and inert OCR.

Installed component preprocessing/postprocessing, session state, fresh bundle
verification and reference scoring are real. The inherited fixtures replace
PDF parsing, pixels, supervision, native inference and producer/model
observations. Explicit callback sequencing is not a browser queue-race test.
"""

import asyncio
import copy
import json
import threading
from types import SimpleNamespace as NS

import pytest

from test_ocr_review_execution_integration import (
    CONTROLS, integrated as integrated, invoke as invoke_execution, started, terminal,
    routes as routes, stack as stack, harness as harness, upstream as upstream,
    preview_roots as preview_roots,
)


async def invoke(host, name, values, *, state=None, session_hash=None):
    # Legacy full-app scenarios select Fit explicitly. New detail scenarios
    # pass their profile themselves; no current server profile is borrowed.
    profiled = {"load_crop_pair": 14, "prepare_crop_reference": 16, "score_crop_reference": 18}
    if name in profiled and len(values) == profiled[name]:
        values = [*values[:5], "fit", *values[5:]]
    if name == "cancel_crop_preview" and len(values) == 1:
        values = [*values, "fit"]
    return await invoke_execution(host, name, values, state=state, session_hash=session_hash)


@pytest.fixture
def crop_host(integrated, monkeypatch):
    from PIL import Image

    host = integrated
    host.renders = []
    host.profiles = []

    def original_crop(scope, *, preview_profile, cancel_requested):
        assert preview_profile in {"fit", "dpi288", "dpi576"}
        assert callable(cancel_requested)
        assert scope["source_sha256"] == host.workspace.document.source_sha256
        assert scope["bbox"] == [0., 0., 1., 1.]
        host.renders.append(copy.deepcopy(scope))
        host.profiles.append(preview_profile)
        return Image.new("RGB", (64, 64), (35, 75, 115))

    monkeypatch.setattr(host.coordinator, "render_crop_preview", original_crop)
    return host


async def make_crop(host, *, operation="regions"):
    controls = copy.deepcopy(CONTROLS)
    controls[0] = operation
    if operation == "hardscan":
        controls[5] = 90
    prepared = await invoke(host, "prepare_run", [None, None, host.initial["annotation_context"], *controls])
    run_id, result_context = await started(host, prepared[0], controls)
    assert terminal(host, run_id)["artifact_state"] == "verified_complete"
    # process_api does not synthesize browser .change/.input events.
    await invoke(host, "crop_context_changed", [None])
    loaded = await invoke(host, "load_results", [None, run_id, result_context])
    item_id = loaded[0]["choices"][0][1]
    selected = await invoke(host, "crop_select_item", [None, None, run_id, item_id, loaded[1]])
    return NS(run_id=run_id, item_id=item_id, result_context=loaded[1],
              view=selected[0], controls=controls)


async def capture(host, side, selected):
    return await invoke(host, "capture_" + side + "_crop",
        [None, None, selected.run_id, selected.item_id, selected.result_context, selected.view])


async def loaded_pair(host, *, operation="regions"):
    baseline = await make_crop(host)
    await capture(host, "baseline", baseline)
    retry = await make_crop(host, operation=operation)
    pinned = await capture(host, "retry", retry)
    loaded = await invoke(host, "load_crop_pair",
        [None, None, None, host.initial["annotation_context"], pinned[0], *retry.controls])
    assert loaded[7:10] == ["", "", ""] and loaded[10] is False
    assert loaded[11:13] == ["", ""]
    assert baseline.run_id in loaded[1] and retry.run_id in loaded[2]
    assert len(host.renders) == 1 and len(host.observed) == 2
    return NS(baseline=baseline, retry=retry, loaded=loaded)


async def prepared_reference(host, pair, *, reference="Independent generated reference.", critical=""):
    # Explicitly dispatch .change before Prepare. Neither OCR pane supplies
    # this reference; intentionally mismatching text must produce real errors.
    edited = await invoke(host, "crop_reference_edited", [None, pair.loaded[0], reference, critical])
    view = edited[0]
    assert edited[1:5] == ["", False, "", ""]
    prepared = await invoke(host, "prepare_crop_reference",
        [None, None, None, host.initial["annotation_context"], view, reference, critical, *pair.retry.controls])
    assert prepared[0] and prepared[1] is False and prepared[3] == ""
    assert reference in prepared[2]
    return NS(view=view, ticket=prepared[0], reference=reference, critical=critical)


async def scored(host, pair, reference, *, confirmed=True, **overrides):
    values = {"view": reference.view, "reference": reference.reference,
              "critical": reference.critical, "ticket": reference.ticket,
              "context": host.initial["annotation_context"], "controls": pair.retry.controls}
    values.update(overrides)
    return await invoke(host, "score_crop_reference",
        [None, None, None, values["context"], values["view"], values["reference"], values["critical"],
         values["ticket"], confirmed, *values["controls"]])


@pytest.mark.parametrize("operation", ["regions", "hardscan"])
def test_full_app_crop_pair_reference_score_uses_two_real_bundles(crop_host, operation):
    host = crop_host
    before = host.workspace.recovery_path.read_bytes()
    document = host.workspace.document

    async def scenario():
        pair = await loaded_pair(host, operation=operation)
        assert pair.loaded[3]["value"]["path"]  # Actual installed Gradio Image postprocessing.
        reference = await prepared_reference(host, pair)
        output = await scored(host, pair, reference)
        assert output[:2] == ["", False]
        metrics = json.loads(output[2])
        assert metrics["canonical_extraction_modified"] is False
        assert metrics["requires_attention"] is True
        assert metrics["comparison"]["records"][0]["baseline"]["character"]["edit_distance"] > 0
        with pytest.raises(Exception):
            await scored(host, pair, reference)
        assert len(host.observed) == host.routes.stack.raw.calls == 2

    asyncio.run(scenario())
    assert host.workspace.document is document and host.workspace.recovery_path.read_bytes() == before
    assert host.state[host.review_id] == host.initial


@pytest.mark.parametrize("confirmed", [False, None, 1, "true"])
def test_full_app_crop_exact_confirmation_is_not_a_truthy_value(crop_host, confirmed):
    host = crop_host

    async def scenario():
        pair = await loaded_pair(host)
        reference = await prepared_reference(host, pair)
        entry = host.blocks["score_crop_reference"]
        # Gradio may coerce checkbox values. Exercise the actual callback to
        # verify that its own approval gate accepts only exact True.
        state_values = [host.state[component._id] for component in entry.inputs[:3]]
        with pytest.raises(Exception):
            entry.fn(*state_values, host.initial["annotation_context"], reference.view, "fit",
                reference.reference, reference.critical, reference.ticket, confirmed, *pair.retry.controls)
        assert len(host.observed) == 2

    asyncio.run(scenario())


@pytest.mark.parametrize("mutation", ["reference", "critical", "away_and_back", "annotation", "controls",
                                      "run_without_change_listener", "result_without_change_listener"])
def test_full_app_crop_stale_reference_confirmation_cannot_score(crop_host, mutation, monkeypatch):
    host = crop_host

    async def scenario():
        pair = await loaded_pair(host)
        reference = await prepared_reference(host, pair)
        overrides = {}
        if mutation == "reference":
            overrides["reference"] = reference.reference + " changed"
        elif mutation == "critical":
            overrides["critical"] = "new critical phrase"
        elif mutation == "away_and_back":
            edited = await invoke(host, "crop_reference_edited",
                [None, reference.view, reference.reference + " changed", reference.critical])
            await invoke(host, "crop_reference_edited",
                [None, edited[0], reference.reference, reference.critical])
        elif mutation == "annotation":
            host.state[host.review_id]["annotation_context"] = "different-live-annotation"
        elif mutation == "run_without_change_listener":
            await invoke(host, "select_run", [None, pair.baseline.run_id, pair.retry.result_context])
        elif mutation == "result_without_change_listener":
            await invoke(host, "load_results", [None, pair.retry.run_id, pair.retry.result_context])
        else:
            controls = copy.deepcopy(pair.retry.controls)
            controls[3] = 400
            overrides["controls"] = controls
        monkeypatch.setattr(host.coordinator, "compare_crops", lambda *_a, **_k: pytest.fail("stale reference scored"))
        with pytest.raises(Exception):
            await scored(host, pair, reference, **overrides)
        assert host.routes.stack.raw.calls == 2

    asyncio.run(scenario())


def test_full_app_crop_tampered_bundle_refuses_after_reference_preparation(crop_host):
    host = crop_host

    async def scenario():
        pair = await loaded_pair(host)
        reference = await prepared_reference(host, pair)
        manifest = host.coordinator._runs[pair.baseline.run_id].intent.output / "manifest.json"
        manifest.write_text("PRIVATE corrupt retained completion", encoding="utf-8")
        with pytest.raises(Exception) as caught:
            await scored(host, pair, reference)
        assert "PRIVATE" not in str(caught.value)
        # Comparison failure does not remove the already-approved run handle.
        await invoke(host, "poll_run", [None, pair.retry.run_id])
        await invoke(host, "cancel_run", [None, pair.retry.run_id])

    asyncio.run(scenario())


def test_full_app_crop_other_session_cannot_reuse_reference_ticket(crop_host):
    host = crop_host

    async def scenario():
        pair = await loaded_pair(host)
        reference = await prepared_reference(host, pair)
        other_hash = "different-crop-review-session"
        other_state = host.app.state_holder[other_hash]
        other_state[host.review_id] = copy.deepcopy(host.initial)
        with pytest.raises(Exception, match="Crop review changed"):
            await invoke(host, "score_crop_reference",
                [None, None, None, host.initial["annotation_context"], reference.view,
                 reference.reference, reference.critical, reference.ticket, True, *pair.retry.controls],
                state=other_state, session_hash=other_hash)
        assert len(host.observed) == 2

    asyncio.run(scenario())


def test_full_app_crop_edit_during_actual_scoring_refuses_old_result(crop_host, monkeypatch):
    host = crop_host
    original_compare = host.coordinator.compare_crops
    observed_results = []
    captured_views = []

    def edited_after_backend_score(*args, **kwargs):
        result = original_compare(*args, **kwargs)
        observed_results.append(result["pair_sha256"])
        edit = host.blocks["crop_reference_edited"]
        edit.fn(host.state[edit.inputs[0]._id], captured_views[0], "Changed after real scoring", "")
        return result

    async def scenario():
        pair = await loaded_pair(host)
        reference = await prepared_reference(host, pair)
        captured_views.append(reference.view)
        monkeypatch.setattr(host.coordinator, "compare_crops", edited_after_backend_score)
        with pytest.raises(Exception, match="Crop review changed"):
            await scored(host, pair, reference)
        assert len(observed_results) == 1  # Real scoring finished, UI result refused.
        crop_state_id = host.blocks["score_crop_reference"].inputs[0]._id
        assert host.state[crop_state_id].pending is None
        assert host.routes.stack.raw.calls == 2

    asyncio.run(scenario())


def test_full_app_crop_failed_image_delivery_cannot_mint_reference_authority(crop_host, monkeypatch):
    import gradio as gr

    host = crop_host

    async def scenario():
        baseline = await make_crop(host)
        await capture(host, "baseline", baseline)
        retry = await make_crop(host)
        pinned = await capture(host, "retry", retry)
        captured_view = pinned[0]

        original_postprocess = gr.Image.postprocess

        def failed_postprocess(_self, _value):
            if _value is None:
                return original_postprocess(_self, _value)
            raise RuntimeError("synthetic image postprocessing failure")

        monkeypatch.setattr(gr.Image, "postprocess", failed_postprocess)
        # Gradio's update-value path propagates this specific postprocess
        # failure directly; it does not wrap it as ComponentProcessingError.
        with pytest.raises(RuntimeError, match="synthetic image postprocessing failure"):
            await invoke(host, "load_crop_pair",
                [None, None, None, host.initial["annotation_context"], captured_view, *retry.controls])
        crop_state_id = host.blocks["load_crop_pair"].inputs[0]._id
        state = host.state[crop_state_id]
        assert state.loaded is not None  # Backend render/readback succeeded.
        assert state.generation != captured_view and state.pending is None
        # Gradio did not deliver the new image response or its hidden token.
        # A subsequent text edit must not mint access to that unseen view.
        before = (state.generation, state.revision, state.loaded, state.pending)
        skipped = await invoke(host, "crop_reference_edited",
            [None, captured_view, "Independently typed text", ""])
        assert skipped == [{"__type__": "update"}] * 6
        assert (state.generation, state.revision, state.loaded, state.pending) == before
        with pytest.raises(Exception):
            await invoke(host, "prepare_crop_reference",
                [None, None, None, host.initial["annotation_context"], captured_view,
                 "Independently typed text", "", *retry.controls])
        assert state.pending is None and host.routes.stack.raw.calls == 2

    asyncio.run(scenario())


def test_full_app_programmatic_empty_changes_and_delayed_same_reference_preserve_view(crop_host):
    host = crop_host

    async def scenario():
        pair = await loaded_pair(host)
        entry = host.blocks["crop_reference_edited"]
        state = host.state[entry.inputs[0]._id]
        original = (state.generation, state.revision, state.loaded, state.pending)
        for _ in range(2):
            skipped = await invoke(host, "crop_reference_edited", [None, pair.loaded[0], "", ""])
            assert skipped == [{"__type__": "update"}] * 6
        assert (state.generation, state.revision, state.loaded, state.pending) == original
        # Preparation can arrive before the corresponding same-value .change.
        # Admitting it must synchronize the raw marker, not erase its new ticket
        # when that delayed notification arrives.
        text = "Independent reference prepared before change notification"
        prepared = await invoke(host, "prepare_crop_reference",
            [None, None, None, host.initial["annotation_context"], pair.loaded[0], text, "", *pair.retry.controls])
        pending = state.pending
        skipped = await invoke(host, "crop_reference_edited", [None, pair.loaded[0], text, ""])
        assert skipped == [{"__type__": "update"}] * 6 and state.pending == pending
        reference = NS(view=pair.loaded[0], ticket=prepared[0], reference=text, critical="")
        assert json.loads((await scored(host, pair, reference))[2])["status"] == "compared"
        assert host.routes.stack.raw.calls == 2

    asyncio.run(scenario())


def test_full_app_change_burst_skips_stale_values_then_fresh_edit_revokes(crop_host, monkeypatch):
    host = crop_host

    async def scenario():
        pair = await loaded_pair(host)
        reference = await prepared_reference(host, pair)
        state = host.state[host.blocks["crop_reference_edited"].inputs[0]._id]
        old_revision = state.revision
        # Concurrent actual process_api calls use the same captured client view;
        # this does not pretend to dispatch real browser DOM events.
        responses = await asyncio.gather(*(invoke(host, "crop_reference_edited",
            [None, reference.view, "Changed reference " + str(index), ""])
            for index in range(22)))
        changed = [response for response in responses if isinstance(response[0], str)]
        assert len(changed) == 1 and state.revision == old_revision + 1
        assert sum(response == [{"__type__": "update"}] * 6 for response in responses) == 21
        assert state.pending is None
        view = changed[0][0]
        newest = "Last actual field value"
        prepared = await invoke(host, "prepare_crop_reference",
            [None, None, None, host.initial["annotation_context"], view, newest, "", *pair.retry.controls])
        old_reference = NS(view=view, ticket=prepared[0], reference=newest, critical="")
        # Partial critical input is not yet a valid scored reference, but must
        # still invalidate approval immediately rather than fail before revoke.
        edited = await invoke(host, "crop_reference_edited", [None, view, newest, "partially typed\n"])
        assert edited[1:5] == ["", False, "", ""] and state.pending is None
        monkeypatch.setattr(host.coordinator, "compare_crops", lambda *_a, **_k: pytest.fail("edited reference scored"))
        with pytest.raises(Exception):
            await scored(host, pair, old_reference)
        assert host.routes.stack.raw.calls == 2

    asyncio.run(scenario())


def test_full_app_reload_empty_change_notifications_do_not_revoke_new_crop(crop_host):
    host = crop_host

    async def scenario():
        pair = await loaded_pair(host)
        reference = await prepared_reference(host, pair, critical="independent")
        reloaded = await invoke(host, "load_crop_pair",
            [None, None, None, host.initial["annotation_context"], reference.view, *pair.retry.controls])
        assert reloaded[3]["value"]["path"] and reloaded[7:10] == ["", "", ""]
        for _ in range(2):
            skipped = await invoke(host, "crop_reference_edited", [None, reloaded[0], "", ""])
            assert skipped == [{"__type__": "update"}] * 6
        pair.loaded = reloaded
        current = await prepared_reference(host, pair)
        assert json.loads((await scored(host, pair, current))[2])["status"] == "compared"
        assert len(host.renders) == 2 and host.routes.stack.raw.calls == 2

    asyncio.run(scenario())


@pytest.mark.parametrize("cancel_action", ["crop_context_changed", "cancel_crop_preview"])
def test_full_app_crop_slow_preview_keeps_controls_responsive_and_refuses_changed_view(crop_host, monkeypatch, cancel_action):
    host = crop_host
    original_render = host.coordinator.render_crop_preview
    entered, release = threading.Event(), threading.Event()

    def blocked_render(scope, *, preview_profile, cancel_requested):
        assert preview_profile == "fit"
        assert cancel_requested() is False
        entered.set()
        assert release.wait(10)
        assert cancel_requested() is True
        return original_render(scope, preview_profile=preview_profile, cancel_requested=cancel_requested)

    async def scenario():
        baseline = await make_crop(host)
        await capture(host, "baseline", baseline)
        retry = await make_crop(host)
        pinned = await capture(host, "retry", retry)
        monkeypatch.setattr(host.coordinator, "render_crop_preview", blocked_render)
        loading = asyncio.create_task(invoke(host, "load_crop_pair",
            [None, None, None, host.initial["annotation_context"], pinned[0], *retry.controls]))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            assert host.blocks["cancel_run"].queue is False and host.blocks["poll_run"].queue is False
            await invoke(host, "poll_run", [None, retry.run_id])
            cancelled = await invoke(host, "cancel_run", [None, retry.run_id])
            # These paired runs are terminal. Cancel is intentionally a no-op,
            # but its request must still complete while rendering is held.
            assert retry.run_id in cancelled[0] and "process completed" in cancelled[0]
            assert host.blocks[cancel_action].queue is False
            cleared = await invoke(host, cancel_action, [None])
            assert cleared[3] is None and cleared[9] == ""
            assert cleared[7:9] == ([{"__type__": "update"}] * 2 if cancel_action == "cancel_crop_preview" else ["", ""])
            assert cleared[10:13] == [False, "", ""]
        finally:
            release.set()
        with pytest.raises(Exception, match="Crop review changed"):
            await loading
        crop_state_id = host.blocks["load_crop_pair"].inputs[0]._id
        state = host.state[crop_state_id]
        assert state.loaded is None and state.pending is None
        assert host.routes.stack.raw.calls == 2

    asyncio.run(scenario())


@pytest.mark.parametrize("profile", ["dpi288", "dpi576"])
def test_full_app_detail_reload_preserves_partial_draft_and_never_repeats_ocr(crop_host, profile):
    host = crop_host

    async def scenario():
        pair = await loaded_pair(host)
        partial, critical = "unfinished independent α\n", "OCR\n"
        edited = await invoke(host, "crop_reference_edited", [None, pair.loaded[0], partial, critical])
        changed = await invoke(host, "crop_preview_profile_changed", [None, edited[0], profile])
        assert changed[3] is None and changed[7:9] == [{"__type__": "update"}] * 2
        assert len(host.renders) == 1 and host.routes.stack.raw.calls == 2
        loaded = await invoke(host, "reload_crop_detail",
            [None, None, None, host.initial["annotation_context"], changed[0], profile, partial, critical, *pair.retry.controls])
        assert loaded[3]["value"]["path"] and loaded[7:9] == [{"__type__": "update"}] * 2
        assert loaded[10:13] == [False, "", ""] and host.renders[0] == host.renders[1]
        assert host.profiles == ["fit", profile]
        # Complete the partial fields explicitly; no saved OCR text becomes a reference.
        reference = "Independent generated reference."
        prepared = await invoke(host, "prepare_crop_reference",
            [None, None, None, host.initial["annotation_context"], loaded[0], profile, reference, "", *pair.retry.controls])
        result = await invoke(host, "score_crop_reference",
            [None, None, None, host.initial["annotation_context"], loaded[0], profile, reference, "", prepared[0], True, *pair.retry.controls])
        assert json.loads(result[2])["requires_attention"] is True
        assert len(host.observed) == host.routes.stack.raw.calls == 2

    asyncio.run(scenario())
