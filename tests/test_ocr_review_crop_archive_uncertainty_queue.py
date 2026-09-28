"""Actual Gradio queue capture over generated archive-UI fixture ports.

Opening uses installed process_api and Image.postprocess. The queued old action
is later dispatched against a new preparation/review on the SAME loaded image,
view, raw text and form. Journal/reference/comparison code is real; the imported
fixture explicitly doubles archive IO, native rendering and publication. This
does not prove browser transport ordering, native pixels or storage durability.
"""

import asyncio
import copy
import json

import pytest

import ocr_comparison
import ocr_crop_comparison as crops
import ocr_evaluation
from test_ocr_review_crop_archive_uncertainty import (
    PACK,
    apply,
    call,
    current,
    event,
    prepare,
    reports as reports,
    score,
    ui as ui,
)
from test_ocr_review_crop_uncertainty_live_ui import process_result


def score_inputs(ui):
    return [None, PACK, ui.state.generation, ui.reference, ui.critical,
            ui.state.pending["ticket"], True, ui.state.action_token,
            ui.state.preview_profile, ui.image_token, *ui.form]


@pytest.mark.parametrize("policy", ["resolved", "unresolved"])
@pytest.mark.parametrize("fresh_state", ["prepared", "reviewed"])
@pytest.mark.parametrize("old_action", ["score", "draft-save", "reviewed-save"])
def test_actual_queued_old_action_cannot_consume_same_image_fresh_authority(
        ui, monkeypatch, tmp_path, policy, fresh_state, old_action):
    from fastapi import Request
    pytest.importorskip("gradio")
    from gradio.data_classes import PredictBodyInternal

    if policy == "unresolved":
        def forbidden(*_a, **_k):
            pytest.fail("unresolved archive flow invoked a scorer")

        monkeypatch.setattr(crops, "compare_ocr", forbidden)
        monkeypatch.setattr(ocr_comparison, "compare_ocr", forbidden)
        monkeypatch.setattr(ocr_evaluation, "evaluate_ocr", forbidden)
    image = ui.functions["open_crop_archive"].outputs[1]
    monkeypatch.setattr(image, "GRADIO_CACHE", str(tmp_path / "archive-images"))
    session_hash = "archive-uncertainty-queued-action"
    state = ui.app.state_holder[session_hash]
    state[ui.functions["open_crop_archive"].inputs[0]._id] = ui.state

    async def scenario():
        # Install actual dropdown choices, then open once through actual
        # postprocessing. No subsequent helper calls Open/Load/Refresh.
        await ui.app.process_api(ui.functions["refresh_crop_archives"], [None],
                                 state=state, session_hash=session_hash)
        await ui.app.process_api(ui.functions["select_crop_archive"],
            [None, PACK, ui.state.generation], state=state, session_hash=session_hash)
        opened = await ui.app.process_api(ui.functions["open_crop_archive"],
            [None, PACK, ui.state.generation, ui.reference, ui.critical, "fit"],
            state=state, session_hash=session_hash)
        ui.image_token = opened["data"][14]
        assert ui.image_token and ui.image_token == ui.state.image_token
        assert opened["data"][1]["value"]["path"]
        if policy == "unresolved":
            event(ui, "mode", mode="annotate")
            event(ui, "whole_scope")
            apply(ui)
        prepare(ui)
        if old_action == "score":
            name = "score_archive_reference"
            captured = score_inputs(ui)
        else:
            score(ui)
            name = "save_archive_draft" if old_action == "draft-save" else "save_archive_reviewed"
            captured = [None, *current(ui), *ui.form]
        captured = copy.deepcopy(captured)
        entry = ui.functions[name]
        index = next(index for index, candidate in ui.app.fns.items() if candidate is entry)
        request = Request({"type": "http", "method": "POST", "path": "/gradio_api/queue/join",
            "root_path": "", "headers": [], "query_string": b"", "scheme": "http",
            "server": ("127.0.0.1", 7860), "client": ("127.0.0.1", 1)})
        accepted, event_id, status = await ui.app._queue.push(PredictBodyInternal(
            data=copy.deepcopy(captured), fn_index=index, session_hash=session_hash, request=request), request, None)
        assert accepted and status == "success"
        queued = ui.app._queue.event_ids_to_events[event_id]
        assert queued.data.data == captured

        initial_loaded = ui.state.loaded
        initial_view, initial_image = ui.state.generation, ui.image_token
        initial_mode = ui.state.mode_token
        initial_journal = copy.deepcopy(ui.state.uncertainty["journal"])
        old_action_token = ui.state.action_token
        original_form = copy.deepcopy(ui.form)
        prepare(ui)  # Explicit same-image preparation; no fresh delivery.
        assert ui.state.action_token != old_action_token
        if fresh_state == "reviewed":
            fresh_result = score(ui)
            assert json.loads(fresh_result[11])["coverage"]["scored_pairs"] == int(policy == "resolved")
            authority = ui.state.last_score
            retained = copy.deepcopy(authority)
        else:
            authority = ui.state.pending
            retained = copy.deepcopy(authority)
        calls = list(ui.calls)
        if old_action != "score":
            trace = []
            refused = await process_result(ui.app, queued.fn, queued.data.data,
                state=state, session_hash=session_hash, trace=trace)
            assert len(trace) == 1 and "unconfirmed" in refused["data"][19]
        else:
            with pytest.raises(Exception, match="Saved crop view changed"):
                await ui.app.process_api(queued.fn, queued.data.data, state=state, session_hash=session_hash)

        # Current action token is captured from the browser input, not sampled
        # from mutable State when this delayed old request is finally executed.
        assert (ui.state.last_score if fresh_state == "reviewed" else ui.state.pending) is authority
        assert authority == retained and ui.calls == calls and not ui.saves
        assert ui.state.loaded is initial_loaded
        assert ui.state.generation == initial_view and ui.image_token == initial_image == ui.state.image_token
        assert ui.state.mode_token == initial_mode and ui.form == original_form
        assert ui.state.uncertainty["journal"] == initial_journal
        assert ui.calls.count("open") == 1 and len(ui.images) == 1
        if fresh_state == "prepared":
            fresh_result = score(ui)
            assert json.loads(fresh_result[11])["coverage"]["scored_pairs"] == int(policy == "resolved")
        expected = copy.deepcopy(ui.state.last_score["reviewed"])
        saved = call(ui, "save_archive_reviewed", *current(ui), *ui.form)
        assert "revision saved" in saved[13] and len(ui.saves) == 1
        assert ui.saves[0]["reviewed"] == expected
        assert ui.state.last_score is None and ui.state.pending is None

    asyncio.run(scenario())
