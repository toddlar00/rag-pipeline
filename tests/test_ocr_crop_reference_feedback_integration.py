"""Installed component response handling with explicitly inert crop ports.

No server, browser, PDF renderer, model or OCR is run. These checks establish
postprocessed inline feedback and client-delivered token recovery, not network
ordering or recognition accuracy. Source loading uses existing direct fixtures.
"""

import asyncio
import json

import pytest

import test_ocr_review_crop_archive_ui as archive
import test_ocr_review_crop_save_ui as saved
import test_ocr_review_crop_ui as live


archive_ui = archive.ui
evidence = archive.evidence
live_ui = live.ui


@pytest.fixture
def execution_ui(monkeypatch, mode):
    if mode == "live_enabled":
        port = saved.service.__wrapped__(monkeypatch)
        host = saved.execution_ui.__wrapped__(monkeypatch, port)
        host.feedback_save_port = port
        return host
    return live.execution_ui.__wrapped__(monkeypatch)


@pytest.mark.parametrize("mode", ["archive", "live", "live_enabled"])
@pytest.mark.parametrize("operation", ["prepare", "score"])
@pytest.mark.parametrize("critical", ["OCR\n", "OCR\n\nchallenge"])
def test_inline_feedback_is_delivered_and_correctable_without_reload(request, mode, operation, critical):
    host = request.getfixturevalue("archive_ui" if mode == "archive" else "live_ui")
    is_archive = mode == "archive"
    enabled = mode == "live_enabled"
    if enabled:
        host.crop = live.crop._SaveCropState()
    block = archive.block if is_archive else live.block
    session_hash = "reference-feedback-" + mode
    state = host.app.state_holder[session_hash]
    reference = "Synthetic OCR challenge"

    async def invoke(name, supplied):
        event = block(host, name)
        assert len(supplied) == len(event.inputs)
        payload = []
        for component, value in zip(event.inputs, supplied):
            if type(component).__name__ == "State":
                state[component._id] = value
                payload.append(None)
            else:
                payload.append(value)
        reply = await host.app.process_api(event, payload, state=state, session_hash=session_hash)
        return reply["data"]

    async def scenario():
        if is_archive:
            # Dropdown choices must be installed through component postprocessing.
            await invoke("refresh_crop_archives", [host.state])
            view = archive.loaded(host)
            client_view, client_action = view[0], view[12]
            owner = host.state
        else:
            view = live.loaded(host)
            client_view, client_action = view[0], None
            owner = host.crop
        loaded_identity = owner.loaded
        image_identity = owner.loaded["image"][:]
        calls_before = list(host.calls if is_archive else host.renders)

        def prepare_args(raw):
            if is_archive:
                return [owner, archive.PACK, client_view, reference, raw, client_action, "fit"]
            return [*live.common(host, client_view), reference, raw, *live.CONTROLS]

        def score_args(raw, ticket):
            if is_archive:
                return [owner, archive.PACK, client_view, reference, raw, ticket, True, client_action, "fit"]
            return [*live.common(host, client_view), reference, raw, ticket, True, *live.CONTROLS]

        prepare_name = "prepare_archive_reference" if is_archive else "prepare_crop_reference"
        score_name = "score_archive_reference" if is_archive else "score_crop_reference"
        if operation == "score":
            ready = await invoke(prepare_name, prepare_args("OCR"))
            ticket = ready[0]
            if is_archive:
                client_action = ready[4]
            elif enabled:
                client_view = ready[5]
            refused = await invoke(score_name, score_args(critical, ticket))
            notice_index, token_index = (4, 3) if is_archive else (3, 4)
            assert refused[:3] == ["", False, ""]
        else:
            refused = await invoke(prepare_name, prepare_args(critical))
            notice_index, token_index = (5, 4) if is_archive else (4, 5)
            assert refused[:4] == ["", False, "", ""]
        assert "blank lines" in refused[notice_index]
        assert "Critical entries" in refused[notice_index]
        assert reference not in refused[notice_index]
        assert owner.pending is None and owner.loaded is loaded_identity
        assert owner.loaded["image"] == image_identity
        assert (host.calls if is_archive else host.renders) == calls_before
        assert not (host.saves if is_archive else host.scores)
        if enabled:
            assert not host.feedback_save_port.calls and owner.last_score is None
        event = block(host, score_name if operation == "score" else prepare_name)
        start = 3 if is_archive else 6
        raw_fields = block(host, prepare_name).inputs[start:start + 2]
        assert {component._id for component in raw_fields}.isdisjoint(component._id for component in event.outputs)
        assert all(type(component).__name__ != "Image" for component in event.outputs)
        if is_archive:
            client_action = refused[token_index]
        elif enabled:
            client_view = refused[token_index]
        recovered = await invoke(prepare_name, prepare_args("OCR"))
        assert recovered[0] and recovered[1] is False and recovered[3] == ""
        declaration = json.loads(recovered[2])
        assert declaration["reference"] == reference and declaration["critical_entries"] == ["OCR"]
        assert owner.loaded is loaded_identity
        if is_archive:
            client_action = recovered[4]
        elif enabled:
            client_view = recovered[5]
        scored = await invoke(score_name, score_args("OCR", recovered[0]))
        assert scored[0] == "" and scored[1] is False
        assert json.loads(scored[2])["status"] == "compared"
        assert owner.pending is None
        assert (host.calls.count("compare") if is_archive else len(host.scores)) == 1
        assert (host.profiles if is_archive else host.renders) == (["fit"] if is_archive else calls_before)

    asyncio.run(scenario())
