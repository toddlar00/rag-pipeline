"""Installed Gradio save bindings with generated/inert host and storage ports.

These tests do not publish packs, replay archives, render PDFs, run OCR, or prove
browser transport ordering. The real private storage/service have separate tests.
"""

import asyncio
import copy
import json
import threading
from types import SimpleNamespace as NS

import pytest

import ocr_review_crop_ui as crop
import ocr_review_execution_ui as execution_panel
from ocr_crop_review_pack_io import CropReviewPackIOError
from test_ocr_review_crop_ui import common, loaded, prepared, score, block
import test_ocr_review_crop_ui as crop_fixtures
from test_ocr_review_execution_ui import CONTROLS, capture as capture_queue
import test_ocr_review_execution_ui as execution_fixtures

base_ui = crop_fixtures.ui


class SavePort:
    """Explicit inert service; callbacks execute under a simulated store lock."""

    def __init__(self):
        self.calls = []
        self.before = None
        self.after = None
        self.error = None
        self.lock = threading.Lock()
        self.result = {"pack_id": "a" * 32, "manifest_sha256": "b" * 64,
                       "status": "verified_complete", "requires_attention": True}

    def save_live(self, coordinator, *ids, **kwargs):
        saved = {key: copy.deepcopy(value) for key, value in kwargs.items() if key != "verify_current"}
        self.calls.append((coordinator, ids, saved))
        with self.lock:
            kwargs["verify_current"]()
            if self.before:
                self.before(kwargs)
            kwargs["verify_current"]()
            if self.error:
                raise self.error
            if self.after:
                self.after(kwargs)
        return copy.deepcopy(self.result)


@pytest.fixture
def service(monkeypatch):
    port = SavePort()
    original = execution_panel.build_crop_review_panel

    def enabled(*args, **kwargs):
        kwargs["pack_service"] = port
        return original(*args, **kwargs)

    monkeypatch.setattr(execution_panel, "build_crop_review_panel", enabled)
    return port


@pytest.fixture
def execution_ui(monkeypatch, service):
    # The reused fixture is an ordinary return fixture, with no hidden teardown.
    return execution_fixtures.ui.__wrapped__(monkeypatch)


@pytest.fixture
def ui(base_ui, service):
    base_ui.crop = crop._SaveCropState()
    base_ui.service = service
    return base_ui


def save(ui, reference, critical, *, reviewed=False, token=None, controls=CONTROLS):
    name = "save_crop_reviewed" if reviewed else "save_crop_draft"
    return ui.functions[name](*common(ui, token), reference, critical, *controls)


def scored(ui):
    item = prepared(ui)
    score(ui, item)
    assert ui.crop.last_score is not None
    return item


def test_enabled_save_components_are_explicit_private_and_have_no_metrics_or_paths_as_inputs(ui):
    names = ["save_crop_draft", "save_crop_reviewed"]
    for name in names:
        fn = block(ui, name)
        assert fn.concurrency_id == "ocr-review-crop-comparison" and fn.concurrency_limit == 1
        assert len(fn.inputs) == 17 and len(fn.outputs) == 3
        assert [type(value).__name__ for value in fn.outputs] == ["Textbox", "Checkbox", "Textbox"]
        labels = [getattr(value, "label", None) for value in fn.inputs]
        assert "Same-crop comparison and scope limitations" not in labels
        assert "Prepared reference and ordered critical entries" not in labels
        assert all(type(value).__name__ not in ("File", "HTML", "BrowserState") for value in fn.inputs)
    assert all(item["api_visibility"] == "private" for item in ui.app.config["dependencies"])
    assert not any(item.get("trigger_after") for item in ui.app.config["dependencies"])


@pytest.mark.parametrize("reference,critical", [("", ""), ("  raw\r\ntext  ", "one\n"),
    ("\x00\U0001f642", "\n\n"), ("draft", "one\r\ntwo")], ids=["empty", "partial", "unicode", "crlf"])
def test_draft_save_preserves_exact_partial_fields_without_reviewed_authority(ui, reference, critical):
    loaded(ui)
    result = save(ui, reference, critical)
    assert result[:2] == ("", False) and "unreviewed draft" in result[2]
    coordinator, ids, kwargs = ui.service.calls[0]
    assert coordinator is ui.coordinator and ids == ("run-A", "region-0001", "run-B", "region-0001")
    assert kwargs["reference"] == reference and kwargs["critical_tokens_text"] == critical
    assert kwargs["reviewed"] is None and kwargs["expected_pair_sha256"] == ui.crop.loaded["pair_sha256"]
    assert ui.crop.last_score is None and not ui.scores and not ui.coordinator.starts


def test_reviewed_save_uses_only_bounded_server_score_and_exact_image_binding_once(ui):
    item = scored(ui)
    binding, raw = ui.crop.last_score
    assert type(raw) is bytes and len(raw) <= crop._LIMIT
    assert binding[:3] == (ui.crop.generation, ui.crop.revision,
                            crop._raw_reference_digest(item.reference, item.critical))
    result = save(ui, item.reference, item.critical, reviewed=True)
    assert "historical reviewed result" in result[2]
    declaration = ui.service.calls[0][2]["reviewed"]
    assert declaration == json.loads(raw)
    assert set(declaration) == {"reference", "comparison", "source_image"}
    assert declaration["source_image"] == {"width": 64, "height": 32,
                                            "rgb_sha256": ui.crop.loaded["image"][2]}
    assert ui.crop.last_score is None and ui.crop.pending is None
    again = save(ui, item.reference, item.critical, reviewed=True)
    assert "not confirmed" in again[2] and len(ui.service.calls) == 1
    assert len(ui.scores) == 1 and not ui.coordinator.starts


def test_draft_after_score_cannot_inherit_historical_review_or_leave_pending_approval(ui):
    item = scored(ui)
    result = save(ui, item.reference, item.critical)
    assert "unreviewed draft" in result[2] and ui.service.calls[0][2]["reviewed"] is None
    assert ui.crop.last_score is None
    save(ui, item.reference, item.critical, reviewed=True)
    assert len(ui.service.calls) == 1


@pytest.mark.parametrize("fault", ["text", "critical", "critical-order", "image", "pair", "scope", "pins"])
def test_reviewed_save_requires_exact_scored_generation_and_consumes_on_current_mismatch(ui, fault):
    item = scored(ui)
    reference, critical = item.reference, item.critical
    if fault == "text":
        reference += "!"
    elif fault == "critical":
        critical = "not"
    elif fault == "critical-order":
        critical = "12.\nnot"
    elif fault == "image":
        ui.crop.loaded["image"][2] = "0" * 64
    elif fault == "pair":
        ui.crop.loaded["pair_sha256"] = "0" * 64
    elif fault == "scope":
        ui.crop.loaded["scope_sha256"] = "0" * 64
    else:
        ui.crop.loaded["pins"][0]["region_id"] = "other"
        ui.crop.baseline["region_id"] = "other"
    result = save(ui, reference, critical, reviewed=True)
    assert "not confirmed" in result[2] and not ui.service.calls
    assert ui.crop.last_score is None


@pytest.mark.parametrize("action", ["edit", "edit-back", "context", "cancel", "load", "prepare", "bad-prepare", "bad-score"])
def test_current_mutations_revoke_saved_score(ui, action):
    item = scored(ui)
    if action in ("edit", "edit-back"):
        ui.functions["crop_reference_edited"](ui.crop, ui.crop.generation, item.reference + "!", item.critical)
        if action == "edit-back":
            ui.functions["crop_reference_edited"](ui.crop, ui.crop.generation, item.reference, item.critical)
    elif action == "context":
        ui.functions["crop_context_changed"](ui.crop)
    elif action == "cancel":
        ui.functions["cancel_crop_preview"](ui.crop)
    elif action == "load":
        ui.functions["load_crop_pair"](*common(ui), *CONTROLS)
    elif action in ("prepare", "bad-prepare"):
        critical = item.critical if action == "prepare" else "partial\n"
        try:
            ui.functions["prepare_crop_reference"](*common(ui), item.reference, critical, *CONTROLS)
        except Exception:
            assert action == "bad-prepare"
    else:
        output = ui.functions["prepare_crop_reference"](*common(ui), item.reference, item.critical, *CONTROLS)
        item = NS(view=output[-1], ticket=output[0], reference=item.reference, critical=item.critical)
        refused = score(ui, item, reference=object())
        assert refused[:3] == ("", False, "") and "transcription" in refused[3]
    assert ui.crop.last_score is None and not ui.service.calls


@pytest.mark.parametrize("field", ["annotation", "page", "controls", "execution-run", "execution-generation", "eviction"])
def test_live_context_changes_refuse_save_before_listener_invalidation(ui, field):
    item = scored(ui)
    controls = CONTROLS
    if field == "annotation":
        ui.review["annotation_context"] = "changed"
    elif field == "page":
        ui.review["page"] = 2
    elif field == "controls":
        controls = [*CONTROLS[:3], 400, *CONTROLS[4:]]
    elif field == "execution-run":
        ui.session.display_run = "run-A"
    elif field == "execution-generation":
        ui.session.result_generation = "new"
    else:
        ui.session.runs = ("run-B",)
    with pytest.raises(Exception):
        save(ui, item.reference, item.critical, reviewed=True, controls=controls)
    assert not ui.service.calls


@pytest.mark.parametrize("action", ["save", "score", "prepare", "edit"])
def test_stale_old_events_do_not_revoke_newer_save_authority(ui, action):
    old = prepared(ui)
    item = scored(ui)
    prior = ui.crop.last_score
    if action == "save":
        with pytest.raises(Exception):
            save(ui, old.reference, old.critical, reviewed=True, token=old.view)
    elif action == "score":
        with pytest.raises(Exception):
            score(ui, old)
    elif action == "prepare":
        with pytest.raises(Exception):
            ui.functions["prepare_crop_reference"](*common(ui, old.view), "old", "", *CONTROLS)
    else:
        ui.functions["crop_reference_edited"](ui.crop, old.view, "old", "")
    assert ui.crop.last_score == prior
    assert "historical reviewed result" in save(ui, item.reference, item.critical, reviewed=True)[2]


def test_save_and_callback_never_hold_session_lock_while_entering_storage(ui):
    item = scored(ui)
    observed = []

    def before(kwargs):
        def acquire():
            with ui.crop.lock:
                observed.append(True)
        worker = threading.Thread(target=acquire)
        worker.start()
        worker.join(1)
        assert not worker.is_alive(), "session lock held while entering storage"
        # Repeated actual callback invocations do not consume/restore authority.
        assert kwargs["verify_current"]() is True

    ui.service.before = before
    assert "historical reviewed result" in save(ui, item.reference, item.critical, reviewed=True)[2]
    assert observed == [True] and ui.crop.last_score is None


@pytest.mark.parametrize("moment", ["inside-storage", "after-storage"])
@pytest.mark.parametrize("mutation", ["edit", "cancel", "prepare", "execution"])
def test_storage_callback_and_postcheck_refuse_mid_save_drift(ui, moment, mutation):
    item = scored(ui)

    def change(_kwargs):
        if mutation == "edit":
            ui.functions["crop_reference_edited"](ui.crop, ui.crop.generation, "changed", "")
        elif mutation == "cancel":
            ui.functions["cancel_crop_preview"](ui.crop)
        elif mutation == "prepare":
            ui.functions["prepare_crop_reference"](*common(ui), item.reference, item.critical, *CONTROLS)
        else:
            ui.session.result_generation = "new result"

    setattr(ui.service, "before" if moment == "inside-storage" else "after", change)
    result = save(ui, item.reference, item.critical, reviewed=True)
    assert "not confirmed" in result[2]
    assert result[:2] == ({"__type__": "update"}, {"__type__": "update"})
    assert len(ui.service.calls) == 1 and ui.crop.last_score is None


@pytest.mark.parametrize("error", [ValueError("PRIVATE path/token"), CropReviewPackIOError("cleanup_uncertain")],
                         ids=["ordinary", "cleanup"])
def test_save_failure_is_static_retains_no_save_authority_and_never_retries(ui, error):
    item = scored(ui)
    ui.service.error = error
    result = save(ui, item.reference, item.critical, reviewed=True)
    assert "PRIVATE" not in result[2] and "not" in result[2]
    if type(error) is CropReviewPackIOError:
        assert "cleanup is unconfirmed" in result[2]
    assert ui.crop.last_score is None and len(ui.service.calls) == 1
    save(ui, item.reference, item.critical, reviewed=True)
    assert len(ui.service.calls) == 1


def test_cancellation_propagates_after_consuming_save_authority(ui):
    item = scored(ui)
    interruption = KeyboardInterrupt("cancel")
    ui.service.error = interruption
    with pytest.raises(KeyboardInterrupt) as caught:
        save(ui, item.reference, item.critical, reviewed=True)
    assert caught.value is interruption and ui.crop.last_score is None
    assert len(ui.service.calls) == 1


@pytest.mark.parametrize("bad", [None, {}, {"status": "PRIVATE"},
    {"pack_id": "PRIVATE_PATH", "manifest_sha256": "b" * 64, "status": "verified_complete", "requires_attention": True}])
def test_malformed_service_result_never_becomes_success_or_leaks_content(ui, bad):
    item = scored(ui)
    ui.service.result = bad
    result = save(ui, item.reference, item.critical, reviewed=True)
    assert "not confirmed" in result[2] and "PRIVATE" not in result[2]
    assert ui.crop.last_score is None


def test_new_session_never_inherits_serialized_score_or_tokens(ui):
    scored(ui)
    fresh = copy.deepcopy(ui.crop)
    assert type(fresh) is crop._SaveCropState
    assert fresh.last_score is None and fresh.loaded is None and fresh.pending is None
    assert fresh.generation != ui.crop.generation and fresh.save_revision == 0


def test_process_api_save_uses_server_state_and_no_client_metrics(ui):
    from gradio.state_holder import SessionState

    item = scored(ui)
    fn = block(ui, "save_crop_reviewed")
    session = SessionState(ui.app)
    supplied = [*common(ui), item.reference, item.critical, *CONTROLS]
    values = []
    for component, value in zip(fn.inputs, supplied):
        if type(component).__name__ == "State":
            session[component._id] = value
            values.append(None)
        else:
            values.append(value)
    response = asyncio.run(ui.app.process_api(fn, values, state=session, explicit_call=True))
    assert response["data"][:2] == ["", False]
    assert "historical reviewed result" in response["data"][2]
    assert len(ui.service.calls) == 1 and ui.crop.last_score is None


@pytest.mark.parametrize("reference,critical", [(None, ""), ("x" * 20_001, ""),
    ("draft", "x" * (64 * 257 + 1)), ("\ud800", "")],
    ids=["wrong-type", "long-reference", "long-critical", "surrogate"])
def test_current_invalid_raw_save_consumes_prior_score_before_any_service_call(ui, reference, critical):
    scored(ui)
    result = save(ui, reference, critical, reviewed=True)
    assert result[:2] == ("", False) and "not confirmed" in result[2]
    assert ui.crop.last_score is None and ui.crop.pending is None and not ui.service.calls


def test_no_loaded_image_cannot_save_even_an_empty_draft(ui):
    with pytest.raises(Exception, match="Crop review changed"):
        save(ui, "", "")
    assert not ui.service.calls


def test_failed_backend_score_cannot_leave_save_authority(ui):
    item = prepared(ui)

    def fail():
        raise ValueError("PRIVATE backend score detail")

    ui.before_score = fail
    with pytest.raises(Exception) as caught:
        score(ui, item)
    assert "PRIVATE" not in str(caught.value) and ui.crop.last_score is None
    save(ui, item.reference, item.critical, reviewed=True)
    assert not ui.service.calls


@pytest.mark.parametrize("action", ["draft-save", "failed-prepare"])
def test_late_score_cannot_rearm_after_competing_save_revision(ui, action):
    item = prepared(ui)

    def change():
        if action == "draft-save":
            assert "unreviewed draft" in save(ui, item.reference, item.critical)[2]
        else:
            refused = ui.functions["prepare_crop_reference"](*common(ui), item.reference, "partial\n", *CONTROLS)
            assert refused[:4] == ("", False, "", "") and "blank lines" in refused[4]

    ui.before_score = change
    with pytest.raises(Exception, match="Crop review changed"):
        score(ui, item)
    assert ui.crop.last_score is None


def test_actual_queue_old_save_cannot_consume_new_live_score(ui):
    old = scored(ui)
    entry = block(ui, "save_crop_reviewed")
    session_hash = "actual-crop-save-queue"
    state = ui.app.state_holder[session_hash]
    for component, value in zip(entry.inputs[:3], (ui.crop, ui.session, ui.review)):
        state[component._id] = value
    captured = [None, None, None, ui.review["annotation_context"], old.view, "fit",
                old.reference, old.critical, *CONTROLS]

    async def scenario():
        event = await capture_queue(ui, entry, captured, session_hash)
        assert event.data.data == captured
        fresh = scored(ui)
        authority = ui.crop.last_score
        with pytest.raises(Exception, match="Crop review changed"):
            await ui.app.process_api(event.fn, event.data.data, state=state, session_hash=session_hash)
        assert ui.crop.last_score == authority and not ui.service.calls
        assert "historical reviewed result" in save(ui, fresh.reference, fresh.critical, reviewed=True)[2]

    asyncio.run(scenario())
    assert len(ui.service.calls) == 1


def test_failure_notice_stays_static_if_current_context_is_itself_malformed(ui):
    item = scored(ui)

    def change(_kwargs):
        ui.review["regions"] = [object()]

    ui.service.before = change
    result = save(ui, item.reference, item.critical, reviewed=True)
    assert "not confirmed" in result[2]
    assert result[:2] == ({"__type__": "update"}, {"__type__": "update"})
    assert ui.crop.last_score is None and len(ui.service.calls) == 1


def test_enabled_prepare_and_score_emit_distinct_client_view_with_same_response(ui):
    loaded(ui)
    initial = ui.crop.generation
    prepared_output = ui.functions["prepare_crop_reference"](*common(ui, initial), "same reference", "", *CONTROLS)
    assert len(prepared_output) == 6 and prepared_output[-1] == ui.crop.generation != initial
    item = NS(view=prepared_output[-1], ticket=prepared_output[0], reference="same reference", critical="")
    score_output = score(ui, item)
    assert len(score_output) == 5 and score_output[-1] == ui.crop.generation != item.view
    prepare_fn, score_fn, save_fn = (block(ui, name) for name in
        ("prepare_crop_reference", "score_crop_reference", "save_crop_reviewed"))
    assert prepare_fn.outputs[-1] is score_fn.outputs[-1] is save_fn.inputs[4]
    assert type(save_fn.inputs[4]).__name__ == "Textbox" and not save_fn.inputs[4].visible
    assert len(ui.renders) == 1


@pytest.mark.parametrize("obsolete_action", ["save", "score"])
def test_real_queue_same_loaded_same_text_old_action_preserves_new_score(ui, obsolete_action):
    loaded(ui)  # The ONLY load/render in this scenario.
    reference, critical = "Identical independently entered text", "text"
    prepare_fn = ui.functions["prepare_crop_reference"]
    first = prepare_fn(*common(ui), reference, critical, *CONTROLS)
    first_item = NS(view=first[-1], ticket=first[0], reference=reference, critical=critical)
    first_score = score(ui, first_item)
    name = "save_crop_reviewed" if obsolete_action == "save" else "score_crop_reference"
    entry = block(ui, name)
    captured = [None, None, None, ui.review["annotation_context"],
                first_score[-1] if obsolete_action == "save" else first[-1], "fit", reference, critical]
    if obsolete_action == "score":
        captured.extend([first[0], True])
    captured.extend(CONTROLS)
    session_hash = "same-loaded-action-" + obsolete_action
    state = ui.app.state_holder[session_hash]
    for component, value in zip(entry.inputs[:3], (ui.crop, ui.session, ui.review)):
        state[component._id] = value

    async def scenario():
        event = await capture_queue(ui, entry, captured, session_hash)
        assert event.data.data == captured
        # Deliberately avoid prepared()/scored(): those helpers reload the pair.
        second = prepare_fn(*common(ui, first_score[-1]), reference, critical, *CONTROLS)
        second_item = NS(view=second[-1], ticket=second[0], reference=reference, critical=critical)
        second_score = score(ui, second_item)
        authority = ui.crop.last_score
        assert authority is not None and len(ui.renders) == 1 and len(ui.pairs) == 2
        assert len({first[-1], first_score[-1], second[-1], second_score[-1]}) == 4
        with pytest.raises(Exception, match="Crop review changed"):
            await ui.app.process_api(event.fn, event.data.data, state=state, session_hash=session_hash)
        assert ui.crop.last_score is authority and not ui.service.calls and len(ui.scores) == 2
        result = save(ui, reference, critical, reviewed=True, token=second_score[-1])
        assert "historical reviewed result" in result[2]

    asyncio.run(scenario())
    assert len(ui.service.calls) == 1 and len(ui.scores) == 2 and len(ui.renders) == 1


@pytest.mark.parametrize("reviewed", [False, True])
def test_real_queue_old_profile_save_cannot_consume_new_detail_review(ui, reviewed):
    item = scored(ui)
    event = block(ui, "save_crop_reviewed" if reviewed else "save_crop_draft")
    payload = [None, None, None, ui.review["annotation_context"], ui.crop.generation, "fit", item.reference, item.critical, *CONTROLS]
    state = ui.app.state_holder["queued-profile-save-" + str(reviewed)]
    for component, value in zip(event.inputs[:3], (ui.crop, ui.session, ui.review)):
        state[component._id] = value

    async def scenario():
        queued = await capture_queue(ui, event, payload, "queued-profile-save-" + str(reviewed))
        assert queued.data.data == payload
        ui.functions["crop_preview_profile_changed"](ui.crop, ui.crop.generation, "dpi288")
        ui.functions["reload_crop_detail"](*common(ui, profile="dpi288"), item.reference, item.critical, *CONTROLS)
        ticket = ui.functions["prepare_crop_reference"](*common(ui, profile="dpi288"), item.reference, item.critical, *CONTROLS)
        ui.functions["score_crop_reference"](*common(ui, profile="dpi288"), item.reference, item.critical, ticket[0], True, *CONTROLS)
        authority = ui.crop.last_score
        assert authority is not None
        with pytest.raises(Exception, match="Crop review changed"):
            await ui.app.process_api(queued.fn, queued.data.data, state=state)
        assert ui.crop.last_score is authority and not ui.service.calls

    asyncio.run(scenario())
