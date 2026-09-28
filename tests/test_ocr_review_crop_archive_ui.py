"""Installed Gradio archive callbacks with generated evidence and inert ports.

Pure crop/reference scoring is real; the service store/image is a deterministic
boundary double. No server, PDF decoder, OCR or model is launched. process_api
exercises captured inputs and live State, not browser-network ordering.
"""
from __future__ import annotations

import asyncio
import copy
import json
import threading
from types import SimpleNamespace as NS

import pytest

import ocr_crop_comparison as crops
import ocr_review_crop_archive_ui as archive_ui
from ocr_disposition_archive import validate_disposition_archive
from test_ocr_disposition_archive import archive_fixture
from test_ocr_review_execution_ui import capture as capture_queue


PACK = "a" * 32
OTHER = "b" * 32
PACK_SHA = "c" * 64
NEW_SHA = "d" * 64


@pytest.mark.parametrize("detail", [False, True])
def test_archive_raster_refusal_guidance_distinguishes_open_and_bound_draft(ui, detail):
    from ocr_crop_review_runtime import CropPreviewError

    loaded(ui) if detail else selected(ui)
    call(ui, "archive_preview_profile_changed", ui.state.generation, "dpi576")

    def rejected(_cancel):
        raise CropPreviewError(code="raster_limit")

    ui.hooks["open"] = rejected
    name = "reload_archive_detail" if detail else "open_crop_archive"
    fields = ("unfinished draft", "OCR\n") if detail else ()
    refused = call(ui, name, PACK, ui.state.generation, *fields, "dpi576")
    assert "otherwise use Load/Open" in refused[-1] and "copy unfinished text first" in refused[-1]
    assert ui.state.loaded is None and (ui.state.draft_scope is not None) is detail
    assert refused[6:8] == (({"__type__": "update"},) * 2 if detail else ("", ""))
    del ui.hooks["open"]
    call(ui, "archive_preview_profile_changed", ui.state.generation, "fit")
    accepted = call(ui, name, PACK, ui.state.generation, *fields, "fit")
    assert accepted[1] is not None and ui.state.pending is None and ui.state.last_score is None


def _comparison(before, after, reference, tokens):
    ref = crops.build_crop_reference(before["report"], anchor_report_sha256=before["artifact_sha256"]["report.json"],
        anchor_region_id="r00", reference=reference, critical_tokens=tokens, confirmed=True)
    result = crops.compare_crop_candidates(before["report"], after["report"], ref,
        baseline_report_sha256=before["artifact_sha256"]["report.json"], retry_report_sha256=after["artifact_sha256"]["report.json"],
        baseline_region_id="r00", retry_region_id="r00")
    return {"pack_sha256": PACK_SHA, "reference": ref, "comparison": result,
            "requires_attention": True, "canonical_extraction_modified": False}


@pytest.fixture(scope="module")
def evidence():
    before = validate_disposition_archive(**archive_fixture(text="synthetic region"))
    after = validate_disposition_archive(**archive_fixture(dpi=400, text="synthetic region!"))
    return before, after, _comparison(before, after, "historical authored draft", ["historical"])


@pytest.fixture
def ui(monkeypatch, evidence):
    monkeypatch.setenv("GRADIO_ANALYTICS_ENABLED", "False")
    gr = pytest.importorskip("gradio")
    from PIL import Image

    before, after, historical = copy.deepcopy(evidence)
    scope = crops.crop_scope(before["report"], region_id="r00")
    workspace = NS(document=NS(source_sha256=scope["source_sha256"], page_count=scope["page_count"], recovery_sha256="e" * 64))
    host = NS(workspace=workspace, scope=scope, state=archive_ui._ArchiveState(), hooks={}, calls=[], saves=[], images=[], profiles=[],
              catalog_value=[{"pack_id": PACK, "manifest_sha256": PACK_SHA, "status": "present_unverified", "requires_attention": True}])

    def checkpoint(name, value=None):
        assert not host.state.lock._is_owned(), f"service {name} called under UI lock"
        host.calls.append(name)
        hook = host.hooks.get(name)
        if hook:
            hook(value)

    host.historical = {"reference": historical["reference"], "comparison": historical["comparison"],
                       "source_image": {"width": 4, "height": 4, "rgb_sha256": "f" * 64}}

    def side(value):
        report = value["report"]
        row = report["regions"][0]
        return {"region_id": "r00", "report_sha256": value["artifact_sha256"]["report.json"],
            "request_sha256": value["request_sha256"], "operation": "regions", "status": row["status"],
            "text": row["candidate"]["text"], "geometry": row["geometry"], "recipe": None,
            "configuration": report["retry_configuration"]}

    host.view = {"pack_id": PACK, "pack_sha256": PACK_SHA, "scope": scope, "baseline": side(before), "retry": side(after),
        "draft": {"reference": "partial authored draft", "critical_tokens_text": "first\n"}, "historical_reviewed": host.historical,
        "validation_scope": "historical_local_declarations", "requires_attention": True, "canonical_extraction_modified": False}

    def catalog():
        checkpoint("catalog")
        return copy.deepcopy(host.catalog_value)

    def open_pack(pack_id, *, preview_profile, cancel_requested):
        assert preview_profile in {"fit", "dpi288", "dpi576"}
        assert pack_id == PACK and not cancel_requested()
        host.profiles.append(preview_profile)
        checkpoint("open", cancel_requested)
        image = Image.new("RGB", (64, 32), (12, 23, 34))
        host.images.append(image)
        result = {**copy.deepcopy(host.view), "image": image}
        checkpoint("open_result", result)
        return result

    def recover(pack_id):
        assert pack_id == PACK
        checkpoint("recover")
        result = {"draft": copy.deepcopy(host.view["draft"]), "scope": copy.deepcopy(scope), "pack_sha256": PACK_SHA,
            "status": "unverified_saved_draft", "requires_attention": True, "canonical_extraction_modified": False}
        checkpoint("recover_result", result)
        return result

    def compare(pack_id, *, expected_pack_sha256, reference, critical_tokens, confirmed):
        assert pack_id == PACK and expected_pack_sha256 == PACK_SHA and confirmed is True
        checkpoint("compare")
        result = _comparison(before, after, reference, critical_tokens)
        checkpoint("compare_result", result)
        return result

    def save(pack_id, *, expected_pack_sha256, reference, critical_tokens_text, reviewed, verify_current):
        assert pack_id == PACK and expected_pack_sha256 == PACK_SHA
        checkpoint("save", verify_current)
        assert verify_current() is True
        host.saves.append(copy.deepcopy({"reference": reference, "critical_tokens_text": critical_tokens_text, "reviewed": reviewed}))
        checkpoint("save_commit", verify_current)
        assert verify_current() is True
        result = {"pack_id": OTHER, "manifest_sha256": NEW_SHA, "status": "verified_complete", "requires_attention": True}
        checkpoint("save_result", result)
        return result

    host.service = NS(catalog=catalog, open=open_pack, recover_draft=recover, compare=compare, save_revision=save)
    app = gr.Blocks()
    try:
        with app:
            archive_ui.build_crop_archive_panel(workspace, host.service)
        host.app = app
        host.functions = {item.fn.__name__: item.fn for item in app.fns.values() if item.fn}
        yield host
    finally:
        app.close()
        for image in host.images:
            image.close()


def call(ui, name, *args):
    if name == "select_crop_archive" and len(args) == 1:
        args = (*args, ui.state.generation)
    # Instantaneous direct-callback tests use the current client action token.
    # Queue/process_api controls below capture their explicit earlier token;
    # the production callbacks have no fallback to current server authority.
    sizes = {"prepare_archive_reference": 4, "score_archive_reference": 6,
             "save_archive_draft": 4, "save_archive_reviewed": 4, "archive_reference_edited": 3}
    if name in sizes and len(args) == sizes[name]:
        args = (*args, ui.state.action_token)
    profiled = {"open_crop_archive": 2, "prepare_archive_reference": 5, "score_archive_reference": 7,
                "save_archive_draft": 5, "save_archive_reviewed": 5}
    if name in profiled and len(args) == profiled[name]:
        args = (*args, "fit")  # Explicit legacy fixture selection, never server authority.
    return ui.functions[name](ui.state, *args)


def selected(ui):
    call(ui, "refresh_crop_archives")
    return call(ui, "select_crop_archive", PACK)


def loaded(ui):
    selected(ui)
    result = call(ui, "open_crop_archive", PACK, ui.state.generation)
    assert result[1] is not None, result[-1]
    return result


def prepared(ui):
    loaded(ui)
    reference, critical = "synthetic region", "synthetic"
    edited = call(ui, "archive_reference_edited", ui.state.generation, reference, critical)
    result = call(ui, "prepare_archive_reference", PACK, edited[0], reference, critical)
    return NS(token=edited[0], ticket=result[0], reference=reference, critical=critical, output=result)


def scored(ui):
    item = prepared(ui)
    result = call(ui, "score_archive_reference", PACK, item.token, item.reference, item.critical, item.ticket, True)
    assert result[:2] == ("", False) and json.loads(result[2])["status"] == "compared"
    assert ui.state.last_score
    return item, result


def block(ui, name):
    return next(item for item in ui.app.fns.values() if item.fn and item.fn.__name__ == name)


def raises_private(error):
    assert "private-secret" not in str(error.value) and "C:/" not in str(error.value)


def test_fixture_historical_seed_is_detached_per_session(ui, evidence):
    before = copy.deepcopy(evidence)
    assert ui.view["historical_reviewed"] is ui.historical
    ui.historical["reference"].clear()
    ui.historical["comparison"].clear()
    assert evidence == before


def test_catalog_is_explicit_no_automatic_open_selection_or_paths(ui):
    assert not ui.calls and not ui.state.catalog
    result = call(ui, "refresh_crop_archives")
    assert result[0]["choices"] == [("Saved entry 1: present_unverified", PACK)]
    assert result[0]["value"] is None and ui.state.selected is None
    assert result[2] is None and ui.calls == ["catalog"]
    assert ui.state.pending is ui.state.last_score is None


def test_open_restores_partial_text_and_explicit_historical_metrics_without_consent(ui):
    result = loaded(ui)
    assert result[0] == ui.state.generation and result[1]["value"].mode == "RGB"
    assert result[2:4] == ("synthetic region", "synthetic region!")
    assert "Historical local declarations" in result[5]
    assert result[6:8] == ("partial authored draft", "first\n")
    assert result[8:12] == ("", False, "", "")
    assert ui.state.pending is ui.state.last_score is None
    assert set(ui.state.loaded) == {"pack_id", "pack_sha256", "scope", "image", "mode", "preview_profile"}
    assert "reference" not in ui.state.loaded and "historical" not in ui.state.loaded
    assert "baseline" not in ui.state.loaded and "retry" not in ui.state.loaded


def test_deepcopy_and_restart_are_blank_capability_sessions(ui):
    scored(ui)
    fresh = copy.deepcopy(ui.state)
    assert fresh.generation != ui.state.generation and fresh.revision == 0
    assert fresh.catalog == {} and fresh.selected is fresh.loaded is fresh.pending is fresh.last_score is None
    ui.state = fresh
    result = loaded(ui)
    assert result[9] is False and result[11] == "" and ui.state.last_score is None


@pytest.mark.parametrize("pack_id", [OTHER, "../private.pdf", "A" * 32, None, True, {}, "a" * 33])
def test_exact_catalog_membership_denies_forged_id_before_service(ui, pack_id):
    selected(ui)
    ui.calls.clear()
    with pytest.raises(Exception):
        call(ui, "open_crop_archive", pack_id, ui.state.generation)
    assert not ui.calls


@pytest.mark.parametrize("mutation", [
    lambda rows: rows.append(copy.deepcopy(rows[0])),
    lambda rows: rows[0].update(pack_id="../elsewhere"),
    lambda rows: rows[0].update(manifest_sha256="bad"),
    lambda rows: rows[0].update(status="complete"),
    lambda rows: rows[0].update(requires_attention=False),
    lambda rows: rows[0].update(path="C:/private-secret"),
    lambda rows: rows.extend([rows[0]] * 128),
])
def test_catalog_malformed_data_is_not_retained(ui, mutation):
    mutation(ui.catalog_value)
    result = call(ui, "refresh_crop_archives")
    assert result[0]["choices"] == [] and not ui.state.catalog and result[2] is None
    assert "private-secret" not in str(result)


@pytest.mark.parametrize("field,value", [
    ("pack_id", OTHER), ("pack_sha256", NEW_SHA), ("validation_scope", "authenticated"),
    ("requires_attention", False), ("canonical_extraction_modified", True),
    ("image", "C:/private-secret"), ("draft", {"reference": "x", "critical_tokens_text": [], "extra": 1}),
])
def test_open_malformed_crossbound_result_clears_view(ui, field, value):
    selected(ui)
    ui.hooks["open_result"] = lambda result: result.update({field: value})
    result = call(ui, "open_crop_archive", PACK, ui.state.generation)
    assert result[1] is None and ui.state.loaded is None and result[9] is False
    assert "private-secret" not in str(result)


@pytest.mark.parametrize("side,status,text", [("baseline", "empty_candidate", ""), ("retry", "retry_failed", None), ("retry", "abstained", None)])
def test_empty_and_unavailable_candidates_remain_distinct_in_details(ui, side, status, text):
    ui.view[side].update(status=status, text=text)
    result = loaded(ui)
    assert json.loads(result[4])[side]["status"] == status
    assert result[2 if side == "baseline" else 3] == ""


def test_draft_recovery_has_no_image_candidates_history_or_score_authority(ui):
    selected(ui)
    result = call(ui, "recover_crop_archive_draft", PACK, ui.state.generation)
    assert result[1:4] == (None, "", "") and result[5] == ""
    assert result[6:8] == ("partial authored draft", "first\n")
    assert result[8:12] == ("", False, "", "") and ui.state.loaded["mode"] == "draft"
    with pytest.raises(Exception):
        call(ui, "prepare_archive_reference", PACK, result[0], "reference", "")
    with pytest.raises(Exception):
        call(ui, "score_archive_reference", PACK, result[0], "reference", "", "forged", True)
    assert "compare" not in ui.calls and "open" not in ui.calls
    saved = call(ui, "save_archive_draft", PACK, result[0], "partial authored draft", "first\n")
    assert "saved" in saved[-1] and ui.saves[0]["reviewed"] is None


@pytest.mark.parametrize("field,value", [("pack_sha256", NEW_SHA), ("status", "verified_complete"), ("canonical_extraction_modified", True), ("image", "forged")])
def test_recovery_rejects_added_proof_or_wrong_binding(ui, field, value):
    selected(ui)
    ui.hooks["recover_result"] = lambda result: result.update({field: value})
    result = call(ui, "recover_crop_archive_draft", PACK, ui.state.generation)
    assert result[1] is None and ui.state.loaded is None and result[6] == ""


def test_fresh_scoring_is_real_pure_reference_comparison_not_historical_reuse(ui):
    item, result = scored(ui)
    score = ui.state.last_score
    assert score["reviewed"]["reference"]["reference"] == item.reference
    assert score["reviewed"]["reference"] != ui.historical["reference"]
    assert score["reviewed"]["source_image"]["width"] == 64
    assert score["reviewed"]["source_image"]["rgb_sha256"] != ui.historical["source_image"]["rgb_sha256"]
    assert json.loads(result[2])["run_settings"]["retry"]["dpi"] == 400
    with pytest.raises(Exception):
        call(ui, "score_archive_reference", PACK, item.token, item.reference, item.critical, item.ticket, True)
    assert ui.calls.count("compare") == 1


@pytest.mark.parametrize("confirmed", [False, None, 1, "true"])
def test_scoring_requires_exact_fresh_checkbox(ui, confirmed):
    item = prepared(ui)
    with pytest.raises(Exception):
        call(ui, "score_archive_reference", PACK, item.token, item.reference, item.critical, item.ticket, confirmed)
    assert "compare" not in ui.calls


@pytest.mark.parametrize("changed", ["reference", "critical", "ticket", "token", "id"])
def test_stale_or_forged_scoring_fields_fail_before_service(ui, changed):
    item = prepared(ui)
    values = [PACK, item.token, item.reference, item.critical, item.ticket, True]
    index = {"id": 0, "token": 1, "reference": 2, "critical": 3, "ticket": 4}[changed]
    values[index] += "x"
    with pytest.raises(Exception):
        call(ui, "score_archive_reference", *values)
    assert "compare" not in ui.calls


@pytest.mark.parametrize("reference,critical", [("different", "synthetic"), ("synthetic region", ""), ("synthetic region", "one\n"), (None, ""), ("x" * 20_001, "")])
def test_edits_clear_current_metrics_approval_and_last_score(ui, reference, critical):
    item, _ = scored(ui)
    result = call(ui, "archive_reference_edited", item.token, reference, critical)
    assert result[0] != item.token and result[1:5] == ("", False, "", "")
    assert ui.state.pending is ui.state.last_score is None


def test_identical_programmatic_change_preserves_ticket_stale_change_skips(ui):
    item = prepared(ui)
    pending = ui.state.pending
    for token, text in [(item.token, item.reference), ("stale", "changed")]:
        result = call(ui, "archive_reference_edited", token, text, item.critical)
        assert result == ({"__type__": "update"},) * 7
        assert ui.state.pending == pending


def test_current_edit_away_and_back_never_restores_approval(ui):
    item = prepared(ui)
    first = call(ui, "archive_reference_edited", item.token, item.reference + "!", item.critical)
    second = call(ui, "archive_reference_edited", first[0], item.reference, item.critical)
    assert second[0] != item.token and ui.state.pending is None


@pytest.mark.parametrize("boundary", ["catalog", "open", "recover", "compare", "save"])
def test_backend_failure_is_sanitized_and_does_not_restore_authority(ui, boundary):
    if boundary in ("compare", "save"):
        item = prepared(ui) if boundary == "compare" else scored(ui)[0]
    else:
        selected(ui)
    def fail(_value):
        raise ValueError("C:/private-secret error details")
    ui.hooks[boundary] = fail
    if boundary == "catalog":
        result = call(ui, "refresh_crop_archives")
    elif boundary in ("open", "recover"):
        result = call(ui, "open_crop_archive" if boundary == "open" else "recover_crop_archive_draft", PACK, ui.state.generation)
    else:
        with pytest.raises(Exception) as error:
            if boundary == "compare":
                call(ui, "score_archive_reference", PACK, item.token, item.reference, item.critical, item.ticket, True)
            else:
                call(ui, "save_archive_reviewed", PACK, item.token, item.reference, item.critical)
        raises_private(error)
        assert ui.state.pending is ui.state.last_score is None
        return
    assert "private-secret" not in str(result)


@pytest.mark.parametrize("boundary", ["open", "recover", "compare", "save"])
@pytest.mark.parametrize("error_type", [KeyboardInterrupt, SystemExit])
def test_cancellation_propagates_original_identity_and_consumes_authority(ui, boundary, error_type):
    item = prepared(ui) if boundary == "compare" else scored(ui)[0] if boundary == "save" else None
    if item is None:
        selected(ui)
    error = error_type()
    def fail(_value):
        raise error
    ui.hooks[boundary] = fail
    with pytest.raises(error_type) as observed:
        if boundary == "compare":
            call(ui, "score_archive_reference", PACK, item.token, item.reference, item.critical, item.ticket, True)
        elif boundary == "save":
            call(ui, "save_archive_reviewed", PACK, item.token, item.reference, item.critical)
        else:
            call(ui, "open_crop_archive" if boundary == "open" else "recover_crop_archive_draft", PACK, ui.state.generation)
    assert observed.value is error and ui.state.pending is ui.state.last_score is None


def test_cancel_while_opening_signals_callback_and_rejects_late_image(ui):
    selected(ui)
    def cancel(callback):
        assert callback() is False
        result = call(ui, "cancel_archive_preview")
        assert result[1] is None and callback() is True
    ui.hooks["open"] = cancel
    with pytest.raises(Exception, match="Saved crop view changed"):
        call(ui, "open_crop_archive", PACK, ui.state.generation)
    assert ui.state.loaded is None and len(ui.images) == 1


@pytest.mark.parametrize("boundary", ["open_result", "recover_result", "compare_result", "save_commit", "save_result"])
def test_stale_backend_returns_cannot_commit_after_reset(ui, boundary):
    item = scored(ui)[0] if boundary.startswith("save") else prepared(ui) if boundary == "compare_result" else None
    if item is None:
        selected(ui)
    ui.hooks[boundary] = lambda value: call(ui, "cancel_archive_preview")
    with pytest.raises(Exception, match="Saved crop view changed"):
        if boundary.startswith("save"):
            call(ui, "save_archive_reviewed", PACK, item.token, item.reference, item.critical)
        elif boundary == "compare_result":
            call(ui, "score_archive_reference", PACK, item.token, item.reference, item.critical, item.ticket, True)
        else:
            call(ui, "open_crop_archive" if boundary == "open_result" else "recover_crop_archive_draft", PACK, ui.state.generation)
    assert ui.state.loaded is ui.state.pending is ui.state.last_score is None


@pytest.mark.parametrize("field", ["pack_sha256", "reference", "scope", "canonical_extraction_modified"])
def test_forged_service_score_binding_never_becomes_save_authority(ui, field):
    item = prepared(ui)
    def mutate(result):
        if field == "reference":
            result["reference"]["reference"] += "changed"
        elif field == "scope":
            result["comparison"]["scope"]["page_number"] = 999
        else:
            result[field] = NEW_SHA if field == "pack_sha256" else True
    ui.hooks["compare_result"] = mutate
    with pytest.raises(Exception):
        call(ui, "score_archive_reference", PACK, item.token, item.reference, item.critical, item.ticket, True)
    assert ui.state.last_score is None


def test_save_reviewed_uses_only_host_score_consumes_and_does_not_auto_select(ui):
    item, _ = scored(ui)
    expected = copy.deepcopy(ui.state.last_score["reviewed"])
    result = call(ui, "save_archive_reviewed", PACK, item.token, item.reference, item.critical)
    assert ui.saves == [{"reference": item.reference, "critical_tokens_text": item.critical, "reviewed": expected}]
    assert result[1:5] == ("", False, "", "")
    assert ui.state.selected == PACK and OTHER not in ui.state.catalog and ui.state.last_score is None
    with pytest.raises(Exception):
        call(ui, "save_archive_reviewed", PACK, item.token, item.reference, item.critical)
    assert len(ui.saves) == 1


@pytest.mark.parametrize("reference", ["changed", None, "x" * 20_001], ids=["changed", "wrong-type", "oversize"])
def test_reviewed_save_attempt_consumes_even_changed_or_malformed_reference(ui, reference):
    item, _ = scored(ui)
    with pytest.raises(Exception):
        call(ui, "save_archive_reviewed", PACK, item.token, reference, item.critical)
    assert ui.state.last_score is None and not ui.saves


def test_save_draft_never_carries_historical_or_fresh_metrics(ui):
    item, _ = scored(ui)
    result = call(ui, "save_archive_draft", PACK, item.token, "unconfirmed partial", "entry\n")
    assert result[0] != item.token and ui.saves[0]["reviewed"] is None
    assert ui.saves[0]["critical_tokens_text"] == "entry\n" and ui.state.last_score is None


def test_store_guard_can_acquire_session_lock_from_another_thread(ui):
    item, _ = scored(ui)
    def store_holds_lock(callback):
        errors = []
        result = []
        def verify():
            try:
                result.append(callback())
            except BaseException as error:
                errors.append(error)
        worker = threading.Thread(target=verify)
        worker.start()
        worker.join(2)
        assert not worker.is_alive(), "UI held session lock while store invoked guard"
        assert result == [True] and not errors
    ui.hooks["save"] = store_holds_lock
    call(ui, "save_archive_reviewed", PACK, item.token, item.reference, item.critical)
    assert len(ui.saves) == 1


def test_edit_during_store_guard_rejects_publication_without_deadlock(ui):
    item, _ = scored(ui)
    def edit(callback):
        result = call(ui, "archive_reference_edited", item.token, "changed while saving", "")
        assert result[0] != item.token
        callback()
    ui.hooks["save"] = edit
    with pytest.raises(Exception, match="Saved crop view changed"):
        call(ui, "save_archive_reviewed", PACK, item.token, item.reference, item.critical)
    assert not ui.saves and ui.state.last_score is None


def test_workspace_replacement_refuses_current_reference_authority(ui):
    item = prepared(ui)
    ui.workspace.document = copy.deepcopy(ui.workspace.document)
    with pytest.raises(Exception):
        call(ui, "score_archive_reference", PACK, item.token, item.reference, item.critical, item.ticket, True)
    assert "compare" not in ui.calls


def test_all_events_private_slow_serialized_and_edits_cancel_not_queued(ui):
    for event in ui.app.fns.values():
        if not event.fn:
            continue
        assert event.api_visibility == "private"
        if event.fn.__name__ in {"select_crop_archive", "cancel_archive_preview", "archive_reference_edited", "archive_preview_profile_changed"}:
            assert event.queue is False and event.trigger_mode == "multiple"
        else:
            assert event.concurrency_id == "ocr-crop-archive-review" and event.concurrency_limit == 1
    for name in ("save_archive_draft", "save_archive_reviewed", "score_archive_reference"):
        assert all(component.label not in {"Fresh same-crop comparison (not canonical correction)", "Historical saved comparison — NOT current approval"}
                   for component in block(ui, name).inputs)
    for event in ui.app.fns.values():
        if event.fn and event.fn.__name__ == "archive_reference_edited":
            assert event.targets[0][1] == "change"
            assert type(event.inputs[1]).__name__ == "Textbox"
            assert event.inputs[1] is block(ui, "open_crop_archive").outputs[0]


def test_actual_process_api_captured_old_checkbox_cannot_score_live_new_state(ui):
    event = block(ui, "score_archive_reference")
    state = ui.app.state_holder["archive-stale"]
    state[event.inputs[0]._id] = ui.state
    async def run():
        await ui.app.process_api(block(ui, "refresh_crop_archives"), [None], state=state, session_hash="archive-stale")
        item = prepared(ui)
        captured = [None, PACK, item.token, item.reference, item.critical, item.ticket, True, ui.state.action_token, "fit"]
        call(ui, "archive_reference_edited", item.token, "changed", "")
        with pytest.raises(Exception, match="Saved crop view changed"):
            await ui.app.process_api(event, captured, state=state, session_hash="archive-stale")
    asyncio.run(run())
    assert "compare" not in ui.calls


def test_actual_process_api_burst_and_fill_invalidate_without_restoring_score(ui):
    event = block(ui, "archive_reference_edited")
    state = ui.app.state_holder["archive-edits"]
    state[event.inputs[0]._id] = ui.state
    async def run():
        await ui.app.process_api(block(ui, "refresh_crop_archives"), [None], state=state, session_hash="archive-edits")
        item = prepared(ui)
        first = None
        captured_action = ui.state.action_token
        for text in ("s", "sy", "syn", "synthetic"):
            result = await ui.app.process_api(event, [None, item.token, text, "", captured_action], state=state, session_hash="archive-edits")
            if first is None:
                first = result["data"]
                assert first[1:5] == ["", False, "", ""]
            else:
                assert result["data"] == [{"__type__": "update"}] * 7
        prepared_result = await ui.app.process_api(block(ui, "prepare_archive_reference"),
            [None, PACK, first[0], item.reference, item.critical, first[5], "fit"], state=state, session_hash="archive-edits")
        ticket = prepared_result["data"][0]
        assert prepared_result["data"][1] is False
        score_result = await ui.app.process_api(block(ui, "score_archive_reference"),
            [None, PACK, first[0], item.reference, item.critical, ticket, True, prepared_result["data"][4], "fit"], state=state, session_hash="archive-edits")
        assert ui.state.last_score
        changed = await ui.app.process_api(event, [None, first[0], item.reference + "!", item.critical, score_result["data"][3]], state=state, session_hash="archive-edits")
        assert changed["data"][1:5] == ["", False, "", ""] and ui.state.last_score is None
    asyncio.run(run())


def test_actual_image_postprocess_failure_does_not_deliver_a_scoreable_token(ui, monkeypatch):
    import gradio as gr
    opener = block(ui, "open_crop_archive")
    state = ui.app.state_holder["archive-undelivered"]
    state[opener.inputs[0]._id] = ui.state
    def fail(_self, _image):
        raise RuntimeError("private-secret image postprocess")
    async def run():
        await ui.app.process_api(block(ui, "refresh_crop_archives"), [None], state=state, session_hash="archive-undelivered")
        selected(ui)
        token = ui.state.generation
        monkeypatch.setattr(gr.Image, "postprocess", fail)
        with pytest.raises(Exception):
            await ui.app.process_api(opener, [None, PACK, token, "fit"], state=state, session_hash="archive-undelivered")
        assert ui.state.loaded is not None and ui.state.generation != token
        with pytest.raises(Exception, match="Saved crop view changed"):
            await ui.app.process_api(block(ui, "prepare_archive_reference"), [None, PACK, token, "unseen crop", "", ui.state.action_token, "fit"],
                                    state=state, session_hash="archive-undelivered")
    asyncio.run(run())
    assert ui.state.pending is None and "compare" not in ui.calls


@pytest.mark.parametrize("operation", ["prepare", "score"])
@pytest.mark.parametrize("bad", [None, "one\n", False], ids=["invalid-reference", "partial-critical", "unchecked"])
def test_invalid_current_action_consumes_prior_score_before_semantic_parse(ui, operation, bad):
    item, _ = scored(ui)
    reference = None if bad is None else item.reference
    critical = bad if type(bad) is str else item.critical
    if operation == "prepare":
        refused = call(ui, "prepare_archive_reference", PACK, item.token, reference, critical if bad is not False else "\n")
        assert refused[:4] == ("", False, "", "") and "prepare and confirm again" in refused[-1]
    else:
        with pytest.raises(Exception):
            call(ui, "score_archive_reference", PACK, item.token, reference, critical, item.ticket, bad if bad is False else True)
    assert ui.state.last_score is ui.state.pending is None
    with pytest.raises(Exception):
        call(ui, "save_archive_reviewed", PACK, item.token, item.reference, item.critical)
    assert not ui.saves


@pytest.mark.parametrize("action", ["prepare", "score", "save-draft", "save-reviewed"])
def test_stale_action_cannot_consume_current_newer_score(ui, action):
    item, _ = scored(ui)
    score = copy.deepcopy(ui.state.last_score)
    before = ui.state.action
    with pytest.raises(Exception):
        if action == "prepare":
            call(ui, "prepare_archive_reference", PACK, "stale", None, None)
        elif action == "score":
            call(ui, "score_archive_reference", PACK, "stale", None, None, "bad", True)
        else:
            call(ui, "save_archive_draft" if action == "save-draft" else "save_archive_reviewed", PACK, "stale", None, None)
    assert ui.state.last_score == score and ui.state.action == before


@pytest.mark.parametrize("action", ["prepare", "score", "save"])
def test_identical_content_new_action_revokes_inflight_save_guard(ui, action):
    item, _ = scored(ui)
    original_action = ui.state.action
    def supersede(callback):
        if action == "prepare":
            call(ui, "prepare_archive_reference", PACK, item.token, item.reference, item.critical)
        elif action == "score":
            with pytest.raises(Exception):
                call(ui, "score_archive_reference", PACK, item.token, item.reference, item.critical, item.ticket, False)
        else:
            ui.hooks.pop("save")
            call(ui, "save_archive_draft", PACK, item.token, item.reference, item.critical)
        assert ui.state.generation == item.token and ui.state.action > original_action + 1
        callback()
    ui.hooks["save"] = supersede
    with pytest.raises(Exception, match="Saved crop view changed"):
        call(ui, "save_archive_reviewed", PACK, item.token, item.reference, item.critical)
    assert len(ui.saves) == (1 if action == "save" else 0)
    assert ui.state.last_score is None
    if action == "prepare":
        assert ui.state.pending is not None  # Old failure cannot erase the new preparation.


@pytest.mark.parametrize("action", ["prepare", "save"])
def test_identical_content_action_revokes_inflight_score_commit(ui, action):
    item = prepared(ui)
    def supersede(_result):
        if action == "prepare":
            call(ui, "prepare_archive_reference", PACK, item.token, item.reference, item.critical)
        else:
            call(ui, "save_archive_draft", PACK, item.token, item.reference, item.critical)
    ui.hooks["compare_result"] = supersede
    with pytest.raises(Exception, match="Saved crop view changed"):
        call(ui, "score_archive_reference", PACK, item.token, item.reference, item.critical, item.ticket, True)
    assert ui.state.last_score is None
    if action == "prepare":
        assert ui.state.pending is not None


def test_serialization_failure_of_prepare_leaves_no_ticket_or_score(ui, monkeypatch):
    item, _ = scored(ui)
    def fail(*args):
        raise ValueError("private-secret bounded display failure")
    monkeypatch.setattr(archive_ui, "_display", fail)
    with pytest.raises(Exception) as error:
        call(ui, "prepare_archive_reference", PACK, item.token, item.reference, item.critical)
    raises_private(error)
    assert ui.state.pending is ui.state.last_score is None


def test_history_between_three_and_four_mib_is_bounded_but_displayable(ui):
    # A service-projection boundary case, not a claim this altered metric object
    # satisfies the backend's independent full historical validator.
    ui.view["historical_reviewed"]["comparison"]["bounded_projection"] = ["x" * 99_000] * 33
    result = loaded(ui)
    assert len(result[5]) > 3 * 1024 * 1024 and len(result[5]) < 4 * 1024 * 1024
    assert ui.state.last_score is None


def test_oversize_history_is_refused_before_retaining_or_displaying(ui):
    selected(ui)
    ui.view["historical_reviewed"]["comparison"]["bounded_projection"] = ["x" * 99_000] * 44
    result = call(ui, "open_crop_archive", PACK, ui.state.generation)
    assert result[1] is None and result[5] == "" and ui.state.loaded is None


@pytest.mark.parametrize("queued_name", ["save_archive_reviewed", "save_archive_draft", "score_archive_reference"])
def test_actual_queued_old_action_cannot_borrow_or_consume_new_same_view_score(ui, queued_name):
    event_fn = block(ui, queued_name)
    session_hash = "archive-same-view-" + queued_name
    state = ui.app.state_holder[session_hash]
    state[event_fn.inputs[0]._id] = ui.state

    async def scenario():
        await ui.app.process_api(block(ui, "refresh_crop_archives"), [None], state=state, session_hash=session_hash)
        item = prepared(ui)
        if queued_name.startswith("save"):
            first_score = call(ui, "score_archive_reference", PACK, item.token, item.reference, item.critical, item.ticket, True)
            captured = [None, PACK, item.token, item.reference, item.critical, first_score[3], "fit"]
        else:
            captured = [None, PACK, item.token, item.reference, item.critical, item.ticket, True, item.output[4], "fit"]
        queued = await capture_queue(ui, event_fn, captured, session_hash)
        assert queued.data.data == captured and queued.concurrency_id == "ocr-crop-archive-review"

        # No reload, no text/critical change and no image-view generation change.
        new_preparation = call(ui, "prepare_archive_reference", PACK, item.token, item.reference, item.critical)
        new_score = call(ui, "score_archive_reference", PACK, item.token, item.reference, item.critical, new_preparation[0], True)
        assert ui.state.generation == item.token
        assert len({captured[-2], new_preparation[4], new_score[3]}) == 3
        latest = copy.deepcopy(ui.state.last_score)
        action = ui.state.action
        call_count = len(ui.calls)
        with pytest.raises(Exception, match="Saved crop view changed"):
            await ui.app.process_api(queued.fn, queued.data.data, state=state, session_hash=session_hash)
        assert ui.state.last_score == latest and ui.state.action == action and len(ui.calls) == call_count
        assert not ui.saves
        # The exact newly emitted nonce, not the rejected capture, may save.
        saved = await ui.app.process_api(block(ui, "save_archive_reviewed"),
            [None, PACK, item.token, item.reference, item.critical, new_score[3], "fit"], state=state, session_hash=session_hash)
        assert "saved" in saved["data"][-1] and len(ui.saves) == 1
    asyncio.run(scenario())


@pytest.mark.parametrize("name", ["prepare_archive_reference", "score_archive_reference", "save_archive_draft", "save_archive_reviewed"])
@pytest.mark.parametrize("captured", [None, "", "forged", 1])
def test_wrong_action_capture_never_consumes_newer_authority_or_parses_values(ui, name, captured, monkeypatch):
    item, _ = scored(ui)
    latest = copy.deepcopy(ui.state.last_score)
    action = ui.state.action
    def fail(*_args):
        raise AssertionError("stale action reached semantic parsing")
    monkeypatch.setattr(archive_ui, "_reference", fail)
    monkeypatch.setattr(archive_ui, "_raw_reference_digest", fail)
    args = [PACK, item.token, None, None]
    if name == "score_archive_reference":
        args.extend([item.ticket, True])
    args.append(captured)
    with pytest.raises(Exception, match="Saved crop view changed"):
        call(ui, name, *args)
    assert ui.state.last_score == latest and ui.state.action == action


def test_same_view_stale_edit_capture_cannot_erase_new_score(ui):
    item = prepared(ui)
    prior_action = item.output[4]
    result = call(ui, "score_archive_reference", PACK, item.token, item.reference, item.critical, item.ticket, True)
    latest = copy.deepcopy(ui.state.last_score)
    assert prior_action != result[3] and ui.state.generation == item.token
    skipped = call(ui, "archive_reference_edited", item.token, "older content", "", prior_action)
    assert skipped == ({"__type__": "update"},) * 7 and ui.state.last_score == latest


def test_action_nonce_is_non_state_emitted_with_exact_preparation_and_metrics(ui):
    opener = block(ui, "open_crop_archive")
    prepare = block(ui, "prepare_archive_reference")
    scorer = block(ui, "score_archive_reference")
    action = opener.outputs[12]
    assert type(action).__name__ == "Textbox" and action.visible is False and action.value == ""
    assert prepare.outputs[4] is scorer.outputs[3] is action
    assert prepare.inputs[-2] is scorer.inputs[-2] is action
    for name in ("save_archive_draft", "save_archive_reviewed"):
        assert block(ui, name).inputs[-2] is action
    assert block(ui, "archive_reference_edited").inputs[-1] is action
    fresh = copy.deepcopy(ui.state)
    assert fresh.action_token != ui.state.action_token


def test_score_postprocess_failure_cannot_authorize_save_using_previous_client_nonce(ui, monkeypatch):
    import gradio as gr
    event = block(ui, "score_archive_reference")
    session_hash = "archive-undelivered-score"
    state = ui.app.state_holder[session_hash]
    state[event.inputs[0]._id] = ui.state
    original = gr.Textbox.postprocess
    metrics = event.outputs[2]
    def fail(component, value):
        if component._id == metrics._id:
            raise ValueError("synthetic failed metric delivery")
        return original(component, value)
    async def scenario():
        await ui.app.process_api(block(ui, "refresh_crop_archives"), [None], state=state, session_hash=session_hash)
        item = prepared(ui)
        old = item.output[4]
        monkeypatch.setattr(gr.Textbox, "postprocess", fail)
        with pytest.raises(Exception, match="synthetic failed metric delivery"):
            await ui.app.process_api(event, [None, PACK, item.token, item.reference, item.critical, item.ticket, True, old, "fit"],
                                    state=state, session_hash=session_hash)
        latest = copy.deepcopy(ui.state.last_score)
        assert latest and latest["action_token"] != old
        with pytest.raises(Exception, match="Saved crop view changed"):
            await ui.app.process_api(block(ui, "save_archive_reviewed"),
                [None, PACK, item.token, item.reference, item.critical, old, "fit"], state=state, session_hash=session_hash)
        assert ui.state.last_score == latest and not ui.saves
    asyncio.run(scenario())


def test_failed_read_notice_accurately_describes_cleared_fields(ui):
    loaded(ui)
    ui.hooks["open"] = lambda _: (_ for _ in ()).throw(ValueError("private-secret"))
    result = call(ui, "open_crop_archive", PACK, ui.state.generation)
    assert result[6:8] == ("", "")
    assert "view was cleared" in result[-1] and "retained packs were not modified" in result[-1]


def test_actual_duplicate_selection_responses_preserve_one_openable_generation(ui):
    """Selection + blur duplicate, with the earlier response delivered last.

    The installed dropdown's option handler emits input and then blur emits
    input again. This models those two captured payloads through process_api,
    not a claim to reproduce browser transport scheduling deterministically.
    """
    session_hash = "archive-duplicate-selection"
    selector = block(ui, "select_crop_archive")
    state = ui.app.state_holder[session_hash]
    state[selector.inputs[0]._id] = ui.state
    async def scenario():
        refreshed = await ui.app.process_api(block(ui, "refresh_crop_archives"), [None], state=state, session_hash=session_hash)
        captured = [None, PACK, refreshed["data"][1]]
        first = await ui.app.process_api(selector, copy.deepcopy(captured), state=state, session_hash=session_hash)
        second = await ui.app.process_api(selector, copy.deepcopy(captured), state=state, session_hash=session_hash)
        # An older identical selection response must not become unusable solely
        # because a duplicate event ran. A real different selection still must.
        opened = await ui.app.process_api(block(ui, "open_crop_archive"), [None, PACK, first["data"][0], "fit"],
                                         state=state, session_hash=session_hash)
        assert opened["data"][1] is not None
        assert second["data"] == [{"__type__": "update"}] * len(selector.outputs)
        assert ui.calls.count("open") == 1
    asyncio.run(scenario())


def test_duplicate_selection_during_open_does_not_cancel_same_pack_preview(ui):
    selected(ui)
    captured = ui.state.generation
    observed = []
    def duplicate(callback):
        assert callback() is False
        result = call(ui, "select_crop_archive", PACK, captured)
        observed.append(callback())
        assert result == ({"__type__": "update"},) * len(block(ui, "select_crop_archive").outputs)
    ui.hooks["open"] = duplicate
    result = call(ui, "open_crop_archive", PACK, ui.state.generation)
    assert observed == [False], "same-ID blur/input duplicate cancelled the active original preview"
    assert result[1] is not None and ui.state.loaded["pack_id"] == PACK


def archive_state_snapshot(ui):
    return copy.deepcopy({key: value for key, value in vars(ui.state).items() if key != "lock"})


@pytest.mark.parametrize("phase", ["selected", "loaded", "prepared", "scored", "draft"])
def test_current_same_id_selection_is_idempotent_without_replaying_view(ui, phase):
    if phase == "selected":
        selected(ui)
    elif phase == "loaded":
        loaded(ui)
    elif phase == "prepared":
        prepared(ui)
    elif phase == "scored":
        scored(ui)
    else:
        selected(ui)
        call(ui, "recover_crop_archive_draft", PACK, ui.state.generation)
    before = archive_state_snapshot(ui)
    calls = len(ui.calls)
    result = call(ui, "select_crop_archive", PACK, ui.state.generation)
    assert result == ({"__type__": "update"},) * 14
    assert archive_state_snapshot(ui) == before and len(ui.calls) == calls


@pytest.mark.parametrize("captured", [None, "", "stale", True])
@pytest.mark.parametrize("pack_id", [PACK, OTHER, None, "../private-secret", {}])
def test_stale_selection_capture_skips_without_consuming_new_authority(ui, captured, pack_id):
    scored(ui)
    before = archive_state_snapshot(ui)
    result = call(ui, "select_crop_archive", pack_id, captured)
    assert result == ({"__type__": "update"},) * 14
    assert archive_state_snapshot(ui) == before


@pytest.mark.parametrize("new_id", [OTHER, None])
def test_current_changed_selection_clears_view_and_authority(ui, new_id):
    ui.catalog_value.append({"pack_id": OTHER, "manifest_sha256": NEW_SHA, "status": "present_unverified", "requires_attention": True})
    item, _ = scored(ui)
    result = call(ui, "select_crop_archive", new_id, item.token)
    assert result[0] != item.token and result[1] is None and result[6:8] == ("", "")
    assert result[8:12] == ("", False, "", "")
    assert ui.state.selected == new_id and ui.state.loaded is ui.state.pending is ui.state.last_score is None


def test_repeated_current_none_after_catalog_refresh_is_idempotent(ui):
    call(ui, "refresh_crop_archives")
    before = archive_state_snapshot(ui)
    result = call(ui, "select_crop_archive", None, ui.state.generation)
    assert result == ({"__type__": "update"},) * 14 and archive_state_snapshot(ui) == before


@pytest.mark.parametrize("queued_id", [OTHER, None])
def test_actual_queue_stale_selection_cannot_clear_new_scored_view(ui, queued_id):
    ui.catalog_value.append({"pack_id": OTHER, "manifest_sha256": NEW_SHA, "status": "present_unverified", "requires_attention": True})
    selector = block(ui, "select_crop_archive")
    session_hash = "archive-old-selection-" + str(queued_id)
    state = ui.app.state_holder[session_hash]
    state[selector.inputs[0]._id] = ui.state
    async def scenario():
        refreshed = await ui.app.process_api(block(ui, "refresh_crop_archives"), [None], state=state, session_hash=session_hash)
        captured = [None, queued_id, refreshed["data"][1]]
        queued = await capture_queue(ui, selector, captured, session_hash)
        assert queued.data.data == captured
        scored(ui)
        latest = archive_state_snapshot(ui)
        result = await ui.app.process_api(queued.fn, queued.data.data, state=state, session_hash=session_hash)
        assert result["data"] == [{"__type__": "update"}] * 14
        assert archive_state_snapshot(ui) == latest and not ui.saves
    asyncio.run(scenario())


def test_selection_captures_existing_non_state_view_and_draft_labels_are_neutral(ui):
    selector = block(ui, "select_crop_archive")
    opener = block(ui, "open_crop_archive")
    assert selector.inputs[2] is opener.outputs[0]
    assert type(selector.inputs[2]).__name__ == "Textbox" and selector.inputs[2].visible is False
    assert selector.targets[0][1] == "input" and selector.queue is False
    assert opener.outputs[4].label == "Archive scope and recipe declarations (verify by opening pack)"
    assert opener.outputs[6].label == "Your transcription (check a fresh original crop before scoring)"


@pytest.mark.parametrize("phase", ["refresh", "select"])
def test_programmatic_empty_text_changes_after_selection_reset_are_noop(ui, phase):
    if phase == "refresh":
        response = call(ui, "refresh_crop_archives")[1:]
    else:
        response = selected(ui)
    before = archive_state_snapshot(ui)
    result = call(ui, "archive_reference_edited", response[0], response[6], response[7], response[12])
    assert result == ({"__type__": "update"},) * 7 and archive_state_snapshot(ui) == before


@pytest.mark.parametrize("interruption", ["edit", "cancel"])
def test_archive_detail_process_api_slow_render_keeps_browser_fields(ui, interruption):
    event = block(ui, "reload_archive_detail")
    state = ui.app.state_holder["archive-detail-slow-" + interruption]
    state[event.inputs[0]._id] = ui.state
    entered, release = threading.Event(), threading.Event()

    def hold(cancel):
        entered.set()
        assert release.wait(10)
        assert cancel() is (interruption == "cancel")

    async def scenario():
        await ui.app.process_api(block(ui, "refresh_crop_archives"), [None], state=state)
        loaded(ui)
        changed = call(ui, "archive_preview_profile_changed", ui.state.generation, "dpi288")
        ui.hooks["open"] = hold
        task = asyncio.create_task(ui.app.process_api(event,
            [None, PACK, changed[0], "unfinished α", "OCR\n", "dpi288"], state=state))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            if interruption == "edit":
                result = await ui.app.process_api(block(ui, "archive_reference_edited"),
                    [None, changed[0], "newer browser text", "OCR\nnot\n", changed[12]], state=state)
                assert result["data"] == [{"__type__": "update"}] * 7
            else:
                result = await ui.app.process_api(block(ui, "cancel_archive_preview"), [None, "dpi288"], state=state)
                assert result["data"][6:8] == [{"__type__": "update"}] * 2
        finally:
            release.set()
        if interruption == "cancel":
            with pytest.raises(Exception, match="Saved crop view changed"):
                await task
            assert ui.state.loaded is None
        else:
            result = await task
            assert result["data"][1]["value"]["path"] and result["data"][6:8] == [{"__type__": "update"}] * 2
            assert ui.state.loaded["preview_profile"] == "dpi288"
        assert ui.state.pending is None and ui.state.last_score is None and "compare" not in ui.calls

    asyncio.run(scenario())


def test_archive_detail_postprocess_failure_does_not_release_unseen_image_authority(ui, monkeypatch):
    import gradio as gr

    event = block(ui, "reload_archive_detail")
    state = ui.app.state_holder["archive-detail-delivery"]
    state[event.inputs[0]._id] = ui.state
    original = gr.Image.postprocess

    def fail(component, value):
        if value is not None:
            raise RuntimeError("inert archive detail delivery failure")
        return original(component, value)

    async def scenario():
        await ui.app.process_api(block(ui, "refresh_crop_archives"), [None], state=state)
        loaded(ui)
        changed = call(ui, "archive_preview_profile_changed", ui.state.generation, "dpi576")
        monkeypatch.setattr(gr.Image, "postprocess", fail)
        payload = [None, PACK, changed[0], "unfinished", "OCR\n", "dpi576"]
        with pytest.raises(Exception, match="inert archive detail delivery failure"):
            await ui.app.process_api(event, payload, state=state)
        assert ui.state.loaded is not None and ui.state.generation != changed[0]
        for name, args in [("archive_reference_edited", [None, changed[0], "unseen", "", changed[12]]),
                           ("archive_preview_profile_changed", [None, changed[0], "dpi576"])]:
            result = await ui.app.process_api(block(ui, name), args, state=state)
            assert all(value == {"__type__": "update"} for value in result["data"])
        with pytest.raises(Exception, match="Saved crop view changed"):
            await ui.app.process_api(block(ui, "prepare_archive_reference"),
                [None, PACK, changed[0], "unseen", "", changed[12], "dpi576"], state=state)
        monkeypatch.setattr(gr.Image, "postprocess", original)
        reset = await ui.app.process_api(block(ui, "cancel_archive_preview"), [None, "dpi576"], state=state)
        assert ui.state.loaded is None and reset["data"][6:8] == [{"__type__": "update"}] * 2
        payload[2] = reset["data"][0]
        delivered = await ui.app.process_api(event, payload, state=state)
        assert delivered["data"][1]["value"]["path"] and delivered["data"][6:8] == [{"__type__": "update"}] * 2
        assert ui.state.pending is None and ui.state.last_score is None

    asyncio.run(scenario())


@pytest.mark.parametrize("queued_name", ["open_crop_archive", "reload_archive_detail", "score_archive_reference", "save_archive_reviewed"])
def test_archive_real_queue_old_profile_action_preserves_new_detail_score(ui, queued_name):
    event = block(ui, queued_name)
    state = ui.app.state_holder["archive-queued-profile-" + queued_name]
    state[event.inputs[0]._id] = ui.state

    async def scenario():
        await ui.app.process_api(block(ui, "refresh_crop_archives"), [None], state=state)
        item, _ = scored(ui)
        payload = [None, PACK, item.token]
        if queued_name != "open_crop_archive":
            payload += [item.reference, item.critical]
        if queued_name == "score_archive_reference":
            payload += [item.ticket, True]
        if queued_name in {"score_archive_reference", "save_archive_reviewed"}:
            payload += [ui.state.action_token]
        payload += ["fit"]
        queued = await capture_queue(ui, event, payload, "archive-queued-profile-" + queued_name)
        assert queued.data.data == payload
        call(ui, "archive_preview_profile_changed", ui.state.generation, "dpi288")
        call(ui, "reload_archive_detail", PACK, ui.state.generation, item.reference, item.critical, "dpi288")
        token = ui.state.generation
        ticket = call(ui, "prepare_archive_reference", PACK, token, item.reference, item.critical, ui.state.action_token, "dpi288")
        call(ui, "score_archive_reference", PACK, token, item.reference, item.critical, ticket[0], True, ui.state.action_token, "dpi288")
        authority, count = copy.deepcopy(ui.state.last_score), len(ui.calls)
        with pytest.raises(Exception, match="Saved crop view changed"):
            await ui.app.process_api(queued.fn, queued.data.data, state=state)
        assert ui.state.last_score == authority and len(ui.calls) == count and not ui.saves

    asyncio.run(scenario())
