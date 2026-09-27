"""Installed Gradio crop controls with deterministic artifact/renderer ports.

No PDF, server, browser, OCR or model is used. process_api tests cover actual
component/session input handling, not client-network response ordering.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from types import SimpleNamespace as NS

import pytest

import ocr_review_crop_ui as crop
import ocr_review_crop_ui_common as common_ui
from ocr_crop_comparison import _bytes, _digest
from test_ocr_review_execution_ui import CONTROLS, SOURCE, RECOVERY, capture as capture_queue
import test_ocr_review_execution_ui as execution_fixtures

execution_ui = execution_fixtures.ui


@pytest.mark.parametrize("detail", [False, True])
def test_raster_refusal_guidance_distinguishes_initial_load_and_bound_draft(ui, detail):
    from ocr_crop_review_runtime import CropPreviewError

    loaded(ui) if detail else captured(ui)
    ui.functions["crop_preview_profile_changed"](ui.crop, ui.crop.generation, "dpi576")

    def rejected():
        raise CropPreviewError(code="raster_limit")

    ui.before_render = rejected
    name = "reload_crop_detail" if detail else "load_crop_pair"
    fields = ("unfinished draft", "OCR\n") if detail else ()
    refused = ui.functions[name](*common(ui, profile="dpi576"), *fields, *CONTROLS)
    assert "otherwise use Load/Open" in refused[-1] and "copy unfinished text first" in refused[-1]
    assert ui.crop.loaded is None and (ui.crop.draft_scope is not None) is detail
    assert refused[7:9] == (({"__type__": "update"},) * 2 if detail else ("", ""))
    ui.before_render = None
    ui.functions["crop_preview_profile_changed"](ui.crop, ui.crop.generation, "fit")
    # Follow the distinct action named in the guidance; no automatic retry.
    accepted = ui.functions[name](*common(ui), *fields, *CONTROLS)
    assert accepted[3] is not None and ui.crop.pending is None and not ui.scores


def _scope():
    scope = {"source_sha256": SOURCE, "page_count": 30, "page_number": 1,
        "coordinate_system": "original_page_display_fraction", "bbox": [.1, .1, .9, .9],
        "page_geometry": {"display_rect_points": [0., 0., 100., 100.],
                          "cropbox_points": [0., 0., 100., 100.], "rotation_degrees": 0}}
    scope["scope_sha256"] = _digest(scope)
    return scope


def _pair_digest(value):
    value.pop("pair_sha256", None)
    value["pair_sha256"] = hashlib.sha256(_bytes(value, crop._LIMIT - 1) + b"\n").hexdigest()
    return value


@pytest.fixture
def ui(execution_ui, monkeypatch):
    from PIL import Image

    host = execution_ui
    host.crop = crop._CropState()
    host.session.runs = ("run-A", "run-B")
    host.session.display_run = "run-A"
    host.coordinator.known_runs.update(host.session.runs)
    host.coordinator.operation = "regions"
    host.coordinator.status_value = "completed"
    host.pairs, host.scores, host.renders, host.profiles = [], [], [], []
    host.before_pair = host.before_score = host.before_render = host.before_capture = None
    host.statuses = {"run-A": "review_required", "run-B": "review_required"}
    host.texts = {"run-A": "Baseline OCR, not a reference.", "run-B": "Retry OCR, not a reference."}

    def side(run_id, item_id):
        return {"run_id": run_id, "item_id": item_id, "region_id": "crop.1", "operation": "regions",
            "request_sha256": ("a" if run_id == "run-A" else "b") * 64,
            "report_sha256": ("c" if run_id == "run-A" else "d") * 64,
            "execution_sha256": "e" * 64, "disposition_sha256": "f" * 64,
            "status": host.statuses[run_id], "text": host.texts[run_id],
            "geometry": {}, "recipe": None, "configuration": {"dpi": 300}, "engine": {"name": "synthetic"}}

    def result(run_id, *, item_id=None):
        host.coordinator.results.append((run_id, item_id))
        if host.before_capture:
            host.before_capture()
        if item_id not in ("region-0001", "region-0002"):
            raise ValueError("unknown private item")
        s = side(run_id, item_id)
        return {"run_id": run_id, "operation": s["operation"], "source_sha256": SOURCE,
            "baseline_recovery_sha256": RECOVERY, "requires_attention": True,
            **{key: s[key] for key in ("request_sha256", "report_sha256", "execution_sha256", "disposition_sha256")},
            "selected_item": {"diagnostic": {"item_id": item_id, "region_id": s["region_id"],
                "page_number": 1, "legacy_status": s["status"]}, "report_record": {
                    "region_id": s["region_id"], "page_number": 1, "status": s["status"]}}}

    def pair(*arguments):
        host.pairs.append(arguments)
        if host.before_pair:
            host.before_pair()
        return _pair_digest({"source_sha256": SOURCE, "baseline_recovery_sha256": RECOVERY,
            "scope": _scope(), "baseline": side(*arguments[:2]), "retry": side(*arguments[2:]),
            "requires_attention": True, "reference_status": "not_supplied", "canonical_extraction_modified": False})

    def score(*arguments, **kwargs):
        host.scores.append((arguments, kwargs))
        if host.before_score:
            host.before_score()
        return {"pair_sha256": kwargs["expected_pair_sha256"], "requires_attention": True,
            "canonical_extraction_modified": False, "reference": {"reference": kwargs["reference"],
                "critical_tokens": kwargs["critical_tokens"], "scope": _scope()},
            "comparison": {"source_sha256": SOURCE, "status": "compared", "comparison": {"records": []}}}

    def render(scope, *, preview_profile, cancel_requested):
        assert preview_profile in {"fit", "dpi288", "dpi576"}
        assert callable(cancel_requested) and cancel_requested() is False
        host.renders.append(copy.deepcopy(scope))
        host.profiles.append(preview_profile)
        if host.before_render:
            host.before_render()
        return Image.new("RGB", (64, 32), (23, 45, 67))

    monkeypatch.setattr(host.coordinator, "result", result)
    monkeypatch.setattr(host.coordinator, "crop_pair", pair, raising=False)
    monkeypatch.setattr(host.coordinator, "compare_crops", score, raising=False)
    monkeypatch.setattr(host.coordinator, "render_crop_preview", render, raising=False)
    return host


def selected(ui, run="run-A", item="region-0001"):
    ui.session.display_run = run
    ui.session.result_generation = "result-" + run
    result = ui.functions["crop_select_item"](ui.crop, ui.session, run, item, ui.session.result_generation)
    return [ui.crop, ui.session, run, item, ui.session.result_generation, result[0]]


def captured(ui):
    first = ui.functions["capture_baseline_crop"](*selected(ui))
    assert "run-A" in first[1]
    second = ui.functions["capture_retry_crop"](*selected(ui, "run-B"))
    assert "run-A" in second[1] and "run-B" in second[2]
    return second


def common(ui, token=None, profile="fit"):
    return [ui.crop, ui.session, ui.review, ui.review["annotation_context"], token or ui.crop.generation, profile]


def loaded(ui):
    captured(ui)
    result = ui.functions["load_crop_pair"](*common(ui), *CONTROLS)
    assert result[3] is not None, result[-1]
    return result


def prepared(ui, reference="Human reference: not 12.", critical="not\n12."):
    loaded(ui)
    ui.functions["crop_reference_edited"](ui.crop, ui.crop.generation, reference, critical)
    result = ui.functions["prepare_crop_reference"](*common(ui), reference, critical, *CONTROLS)
    return NS(view=ui.crop.generation, ticket=result[0], reference=reference, critical=critical, output=result)


def score(ui, prepared, *, confirmed=True, **overrides):
    values = {"view": prepared.view, "reference": prepared.reference, "critical": prepared.critical,
              "ticket": prepared.ticket, "controls": CONTROLS}
    values.update(overrides)
    return ui.functions["score_crop_reference"](*common(ui, values["view"]), values["reference"], values["critical"],
        values["ticket"], confirmed, *values["controls"])


def block(ui, name):
    return next(entry for entry in ui.app.fns.values() if entry.fn and entry.fn.__name__ == name)


def test_flow_starts_empty_and_does_not_prefill_reference_or_adopt(ui):
    before = copy.deepcopy(ui.review)
    result = loaded(ui)
    assert result[4] == ui.texts["run-A"] and result[5] == ui.texts["run-B"]
    assert result[7:10] == ("", "", "") and result[10] is False and result[11:13] == ("", "")
    assert len(ui.pairs) == 2 and len(ui.renders) == 1 and not ui.scores
    assert ui.crop.loaded["image"][:2] == [64, 32]
    assert ui.review == before and set(vars(ui.crop)) == {
        "lock", "generation", "revision", "selected", "baseline", "retry", "loaded", "pending", "reference_digest", "preview_profile", "draft_scope"}


def test_reference_confirmation_is_separate_one_use_and_exact(ui):
    item = prepared(ui)
    assert item.output[1] is False and not ui.scores
    assert json.loads(item.output[2])["critical_entries"] == ["not", "12."]
    result = score(ui, item)
    assert result[:2] == ("", False) and json.loads(result[2])["status"] == "compared"
    assert ui.scores[0][1]["reference"] == item.reference
    with pytest.raises(Exception):
        score(ui, item)
    assert len(ui.scores) == 1 and not ui.coordinator.starts


@pytest.mark.parametrize("confirmed", [False, None, 0, 1, "true", [], {}])
def test_truthy_or_missing_confirmation_never_scores(ui, confirmed):
    item = prepared(ui)
    with pytest.raises(Exception):
        score(ui, item, confirmed=confirmed)
    assert not ui.scores


@pytest.mark.parametrize("changed", ["text", "critical", "order", "edit_back", "annotation", "page", "controls", "generation"])
def test_stale_reference_or_context_never_scores(ui, changed):
    item = prepared(ui)
    options = {}
    if changed == "text":
        options["reference"] = item.reference + "!"
    elif changed == "critical":
        options["critical"] = "12."
    elif changed == "order":
        options["critical"] = "12.\nnot"
    elif changed == "edit_back":
        ui.functions["crop_reference_edited"](ui.crop, ui.crop.generation, item.reference + "!", item.critical)
        ui.functions["crop_reference_edited"](ui.crop, ui.crop.generation, item.reference, item.critical)
    elif changed == "annotation":
        ui.review["annotation_context"] = "changed"
    elif changed == "page":
        ui.review["page"] = 2
    elif changed == "controls":
        options["controls"] = [*CONTROLS[:3], 400, *CONTROLS[4:]]
    else:
        ui.functions["crop_context_changed"](ui.crop)
    with pytest.raises(Exception):
        score(ui, item, **options)
    assert not ui.scores


@pytest.mark.parametrize("change", ["selected_run", "result_generation", "new_run", "away_and_back"])
def test_main_execution_change_before_crop_change_chain_still_rejects_score(ui, change):
    item = prepared(ui)
    old_generation = ui.crop.generation
    if change == "selected_run":
        ui.session.display_run = "run-A"
    elif change == "result_generation":
        ui.session.result_generation = "new-results"
    elif change == "new_run":
        ui.session.runs = (*ui.session.runs, "run-C")
    else:
        ui.session.display_run = "run-A"
        ui.session.result_generation = "intermediate"
        ui.session.display_run = "run-B"
        ui.session.result_generation = "returned-but-new-generation"
    # The asynchronous hidden-component .change callback has not run yet.
    assert ui.crop.generation == old_generation
    with pytest.raises(Exception, match="Crop review changed"):
        score(ui, item)
    assert not ui.scores


def test_navigation_preserves_both_pins_but_clears_every_reviewed_view(ui):
    loaded(ui)
    pins = copy.deepcopy((ui.crop.baseline, ui.crop.retry))
    result = ui.functions["crop_context_changed"](ui.crop)
    assert (ui.crop.baseline, ui.crop.retry) == pins and ui.crop.loaded is None
    assert result[3] is None and result[4:10] == ("",) * 6 and result[10] is False
    assert result[11:13] == ("", "")


def test_explicit_preview_cancel_clears_authority_without_host_shutdown(ui):
    item = prepared(ui)
    pins = copy.deepcopy((ui.crop.baseline, ui.crop.retry))
    previous = ui.crop.generation
    result = ui.functions["cancel_crop_preview"](ui.crop)
    assert result[0] != previous and (ui.crop.baseline, ui.crop.retry) == pins
    assert ui.crop.loaded is None and ui.crop.pending is None
    assert result[3] is None and result[4:7] == ("",) * 3
    assert result[7:9] == ({"__type__": "update"},) * 2 and result[9] == ""
    assert result[10:13] == (False, "", "")
    assert "not proof of worker cleanup" in result[-1]
    assert block(ui, "cancel_crop_preview").queue is False
    with pytest.raises(Exception, match="Crop review changed"):
        score(ui, item)
    assert not ui.scores


def test_preview_worker_receives_only_scope_and_this_view_cancellation(ui, monkeypatch):
    from PIL import Image

    captured(ui)
    callbacks = []

    def render(scope, *, preview_profile, cancel_requested):
        assert preview_profile == "fit"
        assert scope == _scope() and cancel_requested() is False
        callbacks.append(cancel_requested)
        unrelated = crop._CropState()
        ui.functions["cancel_crop_preview"](unrelated)
        assert cancel_requested() is False
        ui.functions["cancel_crop_preview"](ui.crop)
        assert cancel_requested() is True
        return Image.new("RGB", (64, 32))  # A late result is still refused.

    monkeypatch.setattr(ui.coordinator, "render_crop_preview", render)
    with pytest.raises(Exception, match="Crop review changed"):
        ui.functions["load_crop_pair"](*common(ui), *CONTROLS)
    assert len(callbacks) == 1 and ui.crop.loaded is None and ui.crop.pending is None


def test_cancel_before_delayed_load_refuses_without_worker_or_fallback(ui, monkeypatch):
    captured(ui)
    old_view = ui.crop.generation
    ui.functions["cancel_crop_preview"](ui.crop)
    monkeypatch.setattr(ui.coordinator, "render_crop_preview", lambda *_a, **_k: pytest.fail("stale view rendered"))
    with pytest.raises(Exception, match="Crop review changed"):
        ui.functions["load_crop_pair"](*common(ui, old_view), *CONTROLS)
    assert not ui.renders and ui.crop.loaded is None


@pytest.mark.parametrize("old_reference", ["old reference", "x" * 20_001, None])
def test_stale_prepare_cannot_revoke_newer_prepared_ticket(ui, old_reference):
    old = prepared(ui)
    ui.functions["crop_reference_edited"](ui.crop, ui.crop.generation, "New independent reference", "")
    current = ui.functions["prepare_crop_reference"](*common(ui), "New independent reference", "", *CONTROLS)
    pending = ui.crop.pending
    with pytest.raises(Exception, match="Crop review changed"):
        ui.functions["prepare_crop_reference"](*common(ui, old.view), old_reference, "", *CONTROLS)
    assert ui.crop.pending == pending and ui.crop.pending[0] == current[0]


def test_stale_item_capture_including_away_and_back_is_rejected(ui):
    stale = selected(ui, item="region-0001")
    selected(ui, item="region-0002")
    selected(ui, item="region-0001")
    with pytest.raises(Exception):
        ui.functions["capture_baseline_crop"](*stale)
    assert not ui.coordinator.results and ui.crop.baseline is None


def test_captured_item_must_match_server_recorded_selection(ui):
    args = selected(ui)
    args[3] = "region-0002"
    with pytest.raises(Exception):
        ui.functions["capture_baseline_crop"](*args)
    assert not ui.coordinator.results


def test_foreign_session_run_cannot_be_captured(ui):
    args = selected(ui)
    ui.session.runs = ()
    with pytest.raises(Exception):
        ui.functions["capture_baseline_crop"](*args)
    assert not ui.coordinator.results


def test_fresh_session_copy_has_no_runs_pins_view_or_ticket(ui):
    prepared(ui)
    other = copy.deepcopy(ui.crop)
    assert other.generation != ui.crop.generation and other.revision == 0
    assert all(getattr(other, field) is None for field in ("baseline", "retry", "selected", "loaded", "pending"))
    assert other.reference_digest == crop._raw_reference_digest("", "")
    assert copy.deepcopy(ui.session).runs == ()


@pytest.mark.parametrize("boundary", ["capture", "pair", "render", "score"])
def test_inflight_context_revocation_cannot_return_a_successful_old_view(ui, boundary):
    def revoke():
        ui.functions["crop_context_changed"](ui.crop)

    if boundary == "capture":
        args = selected(ui)
        ui.before_capture = revoke

        def action():
            return ui.functions["capture_baseline_crop"](*args)
    elif boundary in ("pair", "render"):
        captured(ui)
        setattr(ui, "before_" + boundary, revoke)

        def action():
            return ui.functions["load_crop_pair"](*common(ui), *CONTROLS)
    else:
        item = prepared(ui)
        ui.before_score = revoke

        def action():
            return score(ui, item)
    with pytest.raises(Exception, match="Crop review changed"):
        action()
    assert ui.crop.loaded is None and ui.crop.pending is None


def test_reference_edit_while_scoring_does_not_publish_stale_metrics(ui):
    item = prepared(ui)
    ui.before_score = lambda: ui.functions["crop_reference_edited"](ui.crop, ui.crop.generation, "Changed during score", "")
    with pytest.raises(Exception, match="Crop review changed"):
        score(ui, item)
    assert len(ui.scores) == 1 and ui.crop.pending is None


def test_failed_reload_clears_old_image_reference_and_scores(ui, monkeypatch):
    loaded(ui)

    def fail(*_a, **_k):
        raise RuntimeError("PRIVATE path or OCR text")

    monkeypatch.setattr(ui.coordinator, "render_crop_preview", fail)
    result = ui.functions["load_crop_pair"](*common(ui), *CONTROLS)
    assert result[3] is None and result[7:10] == ("", "", "") and result[10] is False
    assert result[12] == "" and "PRIVATE" not in result[-1] and ui.crop.loaded is None


@pytest.mark.parametrize("code", [*crop._PREVIEW_NOTICES, "PRIVATE_UNKNOWN_CODE"])
def test_failed_preview_displays_only_allowlisted_stage_notice(ui, monkeypatch, code):
    loaded(ui)

    def fail(*_a, **_k):
        raise crop.CropPreviewError(code=code)

    monkeypatch.setattr(ui.coordinator, "render_crop_preview", fail)
    result = ui.functions["load_crop_pair"](*common(ui), *CONTROLS)
    expected = crop._PREVIEW_NOTICES.get(code, crop._PREVIEW_NOTICES["preview_unavailable"])
    assert result[-1] == expected and "PRIVATE" not in result[-1] and result[3] is None


@pytest.mark.parametrize("status,text", [("retry_failed", None), ("abstained", None), ("empty_candidate", "")])
def test_failed_abstained_and_empty_candidate_display_remain_distinct(ui, status, text):
    ui.statuses["run-B"], ui.texts["run-B"] = status, text
    result = loaded(ui)
    assert result[5] == "" and status in result[-1]


@pytest.mark.parametrize("bad", ["\n", "one\n", "x\r\ny", "x" * 257, "\n".join(str(n) for n in range(65))])
def test_critical_entry_limits_refuse_before_scoring(ui, bad):
    loaded(ui)
    refused = ui.functions["prepare_crop_reference"](*common(ui), "reference", bad, *CONTROLS)
    assert refused[:4] == ("", False, "", "") and "Critical" in refused[4]
    assert not ui.scores and ui.crop.pending is None


def test_private_fast_invalidation_graph_and_image_without_upload_or_download(ui):
    assert all(dependency["api_visibility"] == "private" for dependency in ui.app.config["dependencies"])
    for name in ("crop_select_item", "crop_context_changed", "crop_reference_edited"):
        entries = [entry for entry in ui.app.fns.values() if entry.fn and entry.fn.__name__ == name]
        assert entries and all(entry.queue is False and entry.trigger_mode == "multiple" for entry in entries)
    for name in ("capture_baseline_crop", "capture_retry_crop", "load_crop_pair", "prepare_crop_reference", "score_crop_reference"):
        assert block(ui, name).concurrency_id == "ocr-review-crop-comparison"
    assert block(ui, "poll_run").queue is False and block(ui, "cancel_run").queue is False
    image = block(ui, "load_crop_pair").outputs[3]
    assert image.interactive is False and image.sources == [] and image.buttons == []
    assert all(type(component).__name__ != "State" for component in block(ui, "score_crop_reference").inputs[3:])
    assert not any(type(component).__name__ in ("File", "HTML", "BrowserState") for component in ui.app.blocks.values())


def test_original_crop_caption_is_outside_image_with_metadata_label_preserved(ui):
    image = block(ui, "load_crop_pair").outputs[3]
    label = "Original requested physical crop (not processed OCR input)"
    assert image.label == label and image.show_label is False
    assert image.height is None and image.width is None
    config = next(component for component in ui.app.config["components"] if component["id"] == image._id)
    assert config["props"]["label"] == label and config["props"]["show_label"] is False

    def preceding_sibling(node):
        children = node.get("children", [])
        for index, child in enumerate(children):
            if child["id"] == image._id:
                assert index > 0
                return children[index - 1]["id"]
            found = preceding_sibling(child)
            if found is not None:
                return found
        return None

    caption = ui.app.blocks[preceding_sibling(ui.app.config["layout"])]
    assert type(caption).__name__ == "Markdown" and caption.value == label
    assert not any(caption in entry.outputs for entry in ui.app.fns.values())


@pytest.mark.parametrize("size", [(311, 28), (191, 322)], ids=["short-one-line", "tall-crop"])
def test_short_and_tall_original_crop_pixels_survive_load_and_png_postprocessing(ui, monkeypatch, tmp_path, size):
    from PIL import Image

    pixels = bytes(index % 251 for index in range(size[0] * size[1] * 3))
    original = Image.frombytes("RGB", size, pixels)
    monkeypatch.setattr(ui.coordinator, "render_crop_preview", lambda _scope, **_k: original)
    result = loaded(ui)
    assert result[3]["value"].mode == "RGB" and result[3]["value"].size == size
    assert result[3]["value"].tobytes() == pixels
    assert ui.crop.loaded["image"] == crop._image_identity(original)
    assert result[7:10] == ("", "", "") and result[10] is False
    assert not ui.scores and not ui.coordinator.starts

    image = block(ui, "load_crop_pair").outputs[3]
    monkeypatch.setattr(image, "GRADIO_CACHE", str(tmp_path / "crop-image-cache"))
    processed = image.postprocess(result[3]["value"])
    with Image.open(processed.path) as delivered:
        assert delivered.format == "PNG" and delivered.mode == "RGB"
        assert delivered.size == size and delivered.tobytes() == pixels


def test_poll_and_cancel_are_available_during_pair_work(ui):
    captured(ui)

    def while_loading():
        assert "last observed" in ui.functions["poll_run"](ui.session, "run-B")
        ui.functions["cancel_run"](ui.session, "run-B")

    ui.before_pair = while_loading
    result = ui.functions["load_crop_pair"](*common(ui), *CONTROLS)
    assert result[3] is not None and ui.coordinator.cancels == ["run-B", "run-B"]


def test_actual_process_api_live_state_rejects_captured_stale_true(ui):
    item = prepared(ui)
    entry = block(ui, "score_crop_reference")
    session = ui.app.state_holder["crop-captured-stale"]
    session[entry.inputs[0]._id] = ui.crop
    session[entry.inputs[1]._id] = ui.session
    session[entry.inputs[2]._id] = ui.review
    captured = [None, None, None, ui.review["annotation_context"], item.view, "fit",
                item.reference, item.critical, item.ticket, True, *CONTROLS]
    ui.functions["crop_reference_edited"](ui.crop, ui.crop.generation, "Changed after captured score", "")

    async def scenario():
        with pytest.raises(Exception, match="Crop review changed"):
            await ui.app.process_api(entry, captured, state=session, session_hash="crop-captured-stale")

    asyncio.run(scenario())


@pytest.mark.parametrize("interruption", ["edit", "cancel"])
def test_detail_process_api_preserves_browser_draft_during_slow_render(ui, monkeypatch, interruption):
    import threading
    from PIL import Image

    loaded(ui)
    old = ui.crop.generation
    changed = ui.functions["crop_preview_profile_changed"](ui.crop, old, "dpi288")
    reload = block(ui, "reload_crop_detail")
    state = ui.app.state_holder["detail-interleaving-" + interruption]
    for component, value in zip(reload.inputs[:3], (ui.crop, ui.session, ui.review)):
        state[component._id] = value
    entered, release = threading.Event(), threading.Event()

    def render(scope, *, preview_profile, cancel_requested):
        assert scope == _scope() and preview_profile == "dpi288"
        entered.set()
        assert release.wait(10)
        assert cancel_requested() is (interruption == "cancel")
        return Image.new("RGB", (128, 64))

    monkeypatch.setattr(ui.coordinator, "render_crop_preview", render)

    async def scenario():
        task = asyncio.create_task(ui.app.process_api(reload,
            [None, None, None, ui.review["annotation_context"], changed[0], "dpi288", "partial α", "OCR\n", *CONTROLS], state=state))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            if interruption == "edit":
                # This payload carries the still-visible, older image token.
                edit = await ui.app.process_api(block(ui, "crop_reference_edited"),
                    [None, changed[0], "newer browser text", "OCR\nnot\n"], state=state)
                assert edit["data"] == [{"__type__": "update"}] * 6
            else:
                cancel = await ui.app.process_api(block(ui, "cancel_crop_preview"), [None, "dpi288"], state=state)
                assert cancel["data"][7:9] == [{"__type__": "update"}] * 2
        finally:
            release.set()
        if interruption == "cancel":
            with pytest.raises(Exception, match="Crop review changed"):
                await task
            assert ui.crop.loaded is None
        else:
            response = await task
            assert response["data"][7:9] == [{"__type__": "update"}] * 2
            assert response["data"][3]["value"]["path"] and ui.crop.loaded["preview_profile"] == "dpi288"
        assert ui.crop.pending is None and not ui.scores

    asyncio.run(scenario())


def test_detail_image_postprocess_failure_requires_explicit_cancel_and_fresh_reload(ui, monkeypatch):
    import gradio as gr

    loaded(ui)
    changed = ui.functions["crop_preview_profile_changed"](ui.crop, ui.crop.generation, "dpi576")
    reload = block(ui, "reload_crop_detail")
    state = ui.app.state_holder["detail-image-failure"]
    for component, value in zip(reload.inputs[:3], (ui.crop, ui.session, ui.review)):
        state[component._id] = value
    original = gr.Image.postprocess

    def fail(component, value):
        if value is not None:
            raise ValueError("inert detail image delivery failure")
        return original(component, value)

    async def scenario():
        monkeypatch.setattr(gr.Image, "postprocess", fail)
        payload = [None, None, None, ui.review["annotation_context"], changed[0], "dpi576", "unfinished", "OCR\n", *CONTROLS]
        with pytest.raises(Exception, match="inert detail image delivery failure"):
            await ui.app.process_api(reload, payload, state=state)
        assert ui.crop.loaded is not None and ui.crop.generation != changed[0]
        for name, args in [("crop_reference_edited", [None, changed[0], "unseen", ""]),
                           ("crop_preview_profile_changed", [None, changed[0], "dpi576"])]:
            result = await ui.app.process_api(block(ui, name), args, state=state)
            assert all(value == {"__type__": "update"} for value in result["data"])
        with pytest.raises(Exception, match="Crop review changed"):
            await ui.app.process_api(block(ui, "prepare_crop_reference"),
                [None, None, None, ui.review["annotation_context"], changed[0], "dpi576", "unseen", "", *CONTROLS], state=state)
        monkeypatch.setattr(gr.Image, "postprocess", original)
        reset = await ui.app.process_api(block(ui, "cancel_crop_preview"), [None, "dpi576"], state=state)
        assert ui.crop.loaded is None and reset["data"][7:9] == [{"__type__": "update"}] * 2
        payload[4] = reset["data"][0]
        delivered = await ui.app.process_api(reload, payload, state=state)
        assert delivered["data"][3]["value"]["path"] and delivered["data"][7:9] == [{"__type__": "update"}] * 2
        assert ui.crop.pending is None and not ui.scores

    asyncio.run(scenario())


@pytest.mark.parametrize("queued_name", ["load_crop_pair", "reload_crop_detail", "score_crop_reference"])
def test_real_queue_old_profile_action_cannot_consume_new_detail_preparation(ui, queued_name):
    item = prepared(ui)
    event_fn = block(ui, queued_name)
    payload = [None, None, None, ui.review["annotation_context"], item.view, "fit"]
    if queued_name != "load_crop_pair":
        payload += [item.reference, item.critical]
    if queued_name == "score_crop_reference":
        payload += [item.ticket, True]
    payload += CONTROLS
    state = ui.app.state_holder["queued-detail-" + queued_name]
    for component, value in zip(event_fn.inputs[:3], (ui.crop, ui.session, ui.review)):
        state[component._id] = value

    async def scenario():
        queued = await capture_queue(ui, event_fn, payload, "queued-detail-" + queued_name)
        assert queued.data.data == payload
        ui.functions["crop_preview_profile_changed"](ui.crop, item.view, "dpi288")
        ui.functions["reload_crop_detail"](*common(ui, profile="dpi288"), item.reference, item.critical, *CONTROLS)
        current = ui.functions["prepare_crop_reference"](*common(ui, profile="dpi288"), item.reference, item.critical, *CONTROLS)
        pending, count = ui.crop.pending, len(ui.renders)
        with pytest.raises(Exception, match="Crop review changed"):
            await ui.app.process_api(queued.fn, queued.data.data, state=state)
        assert ui.crop.pending == pending and pending[0] == current[0] and len(ui.renders) == count and not ui.scores

    asyncio.run(scenario())
    assert not ui.scores


def test_actual_process_api_prepared_confirmation_is_false_same_response(ui):
    loaded(ui)
    entry = block(ui, "prepare_crop_reference")
    session = ui.app.state_holder["crop-prepare"]
    session[entry.inputs[0]._id] = ui.crop
    session[entry.inputs[1]._id] = ui.session
    session[entry.inputs[2]._id] = ui.review

    async def scenario():
        response = await ui.app.process_api(entry,
            [None, None, None, ui.review["annotation_context"], ui.crop.generation, "fit", "Human reference", "", *CONTROLS],
            state=session, session_hash="crop-prepare")
        assert response["data"][0] and response["data"][1] is False
        assert "Human reference" in response["data"][2] and response["data"][3] == ""

    asyncio.run(scenario())


def test_real_queue_captured_confirmation_cannot_score_new_live_revision(ui):
    item = prepared(ui)
    entry = block(ui, "score_crop_reference")
    session_hash = "actual-crop-queue"
    state = ui.app.state_holder[session_hash]
    for component, value in zip(entry.inputs[:3], (ui.crop, ui.session, ui.review)):
        state[component._id] = value
    captured = [None, None, None, ui.review["annotation_context"], item.view, "fit",
                item.reference, item.critical, item.ticket, True, *CONTROLS]

    async def scenario():
        event = await capture_queue(ui, entry, captured, session_hash)
        assert event.data.data == captured and event.concurrency_id == "ocr-review-crop-comparison"
        ui.functions["crop_reference_edited"](ui.crop, ui.crop.generation, "New independent reference", "")
        current = ui.functions["prepare_crop_reference"](*common(ui), "New independent reference", "", *CONTROLS)
        pending = ui.crop.pending
        with pytest.raises(Exception, match="Crop review changed"):
            await ui.app.process_api(event.fn, event.data.data, state=state, session_hash=session_hash)
        assert ui.crop.pending == pending and pending[0] == current[0]

    asyncio.run(scenario())
    assert not ui.scores


def test_pair_generation_change_after_render_refuses_old_image(ui, monkeypatch):
    captured(ui)
    original = ui.coordinator.crop_pair
    count = 0

    def changed(*args):
        nonlocal count
        count += 1
        result = original(*args)
        if count == 2:
            result["retry"]["text"] = "Different fresh generation"
            _pair_digest(result)
        return result

    monkeypatch.setattr(ui.coordinator, "crop_pair", changed)
    result = ui.functions["load_crop_pair"](*common(ui), *CONTROLS)
    assert result[3] is None and ui.crop.loaded is None and len(ui.renders) == 1


@pytest.mark.parametrize("field", ["pair", "reference", "critical", "scope", "source"])
def test_mismatched_score_response_is_never_displayed(ui, monkeypatch, field):
    item = prepared(ui)
    original = ui.coordinator.compare_crops

    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        if field == "pair":
            result["pair_sha256"] = "0" * 64
        elif field == "scope":
            result["reference"]["scope"]["scope_sha256"] = "0" * 64
        elif field == "source":
            result["comparison"]["source_sha256"] = "0" * 64
        else:
            result["reference"]["reference" if field == "reference" else "critical_tokens"] = "unexpected"
        return result

    monkeypatch.setattr(ui.coordinator, "compare_crops", changed)
    with pytest.raises(Exception, match="Crop review is unavailable"):
        score(ui, item)
    assert ui.crop.pending is None


def test_whole_page_result_cannot_be_captured_as_crop(ui, monkeypatch):
    args = selected(ui)
    original = ui.coordinator.result

    def page(*args, **kwargs):
        result = original(*args, **kwargs)
        result["operation"] = "pages"
        return result

    monkeypatch.setattr(ui.coordinator, "result", page)
    result = ui.functions["capture_baseline_crop"](*args)
    assert result[1] == "Not captured" and ui.crop.baseline is None and not ui.pairs


@pytest.mark.parametrize("old_token", [None, "", "old", 1, True])
def test_stale_edit_does_not_revoke_new_ticket_or_mint_token(ui, old_token):
    prepared(ui)
    before = ui.crop.generation, ui.crop.revision, ui.crop.pending, copy.deepcopy(ui.crop.loaded), ui.crop.reference_digest
    result = ui.functions["crop_reference_edited"](ui.crop, old_token, None, "x" * 20_000)
    assert result == tuple({"__type__": "update"} for _ in range(6))
    assert (ui.crop.generation, ui.crop.revision, ui.crop.pending, ui.crop.loaded, ui.crop.reference_digest) == before


def test_image_postprocess_failure_cannot_be_reauthorized_by_reference_edit(ui, monkeypatch):
    import gradio as gr

    captured(ui)
    load = block(ui, "load_crop_pair")
    edit = block(ui, "crop_reference_edited")
    prepare = block(ui, "prepare_crop_reference")
    session_hash = "failed-crop-image-delivery"
    state = ui.app.state_holder[session_hash]
    for component, value in zip(load.inputs[:3], (ui.crop, ui.session, ui.review)):
        state[component._id] = value
    client_view = ui.crop.generation
    original_postprocess = gr.Image.postprocess

    def fail_image(_component, _value):
        raise RuntimeError("synthetic image cache delivery failed")

    monkeypatch.setattr(gr.Image, "postprocess", fail_image)

    async def scenario():
        with pytest.raises(Exception):
            await ui.app.process_api(load,
                [None, None, None, ui.review["annotation_context"], client_view, "fit", *CONTROLS],
                state=state, session_hash=session_hash)
        assert ui.crop.loaded is not None and ui.crop.generation != client_view
        # The callback completed, but Image postprocessing failed, so the
        # browser received neither the new image nor its paired view token.
        server_generation = ui.crop.generation
        skipped = await ui.app.process_api(edit, [None, client_view, "Unseen crop", ""],
                                          state=state, session_hash=session_hash)
        assert skipped["data"] == [{"__type__": "update"}] * 6
        with pytest.raises(Exception, match="Crop review changed"):
            await ui.app.process_api(prepare,
                [None, None, None, ui.review["annotation_context"], client_view, "fit",
                 "Transcription cannot authorize an unseen new crop", "", *CONTROLS],
                state=state, session_hash=session_hash)
        assert ui.crop.pending is None and ui.crop.generation == server_generation
        assert not ui.scores
        # Explicitly refreshing the selection clears that unseen server view;
        # a later successfully delivered image permits the normal workflow.
        reset = ui.functions["crop_context_changed"](ui.crop)
        assert ui.crop.loaded is None and reset[3] is None
        monkeypatch.setattr(gr.Image, "postprocess", original_postprocess)
        delivered = await ui.app.process_api(load,
            [None, None, None, ui.review["annotation_context"], reset[0], "fit", *CONTROLS],
            state=state, session_hash=session_hash)
        assert delivered["data"][3]["value"]["path"] and delivered["data"][10] is False
        edited = await ui.app.process_api(edit,
            [None, delivered["data"][0], "Freshly displayed independent reference", ""],
            state=state, session_hash=session_hash)
        ticket = await ui.app.process_api(prepare,
            [None, None, None, ui.review["annotation_context"], edited["data"][0], "fit",
             "Freshly displayed independent reference", "", *CONTROLS], state=state, session_hash=session_hash)
        assert ticket["data"][0] and ticket["data"][1] is False

    asyncio.run(scenario())


def test_reference_edit_listeners_capture_non_state_view_token(ui):
    entries = [entry for entry in ui.app.fns.values() if entry.fn and entry.fn.__name__ == "crop_reference_edited"]
    assert len(entries) == 2
    for entry in entries:
        assert [type(component).__name__ for component in entry.inputs] == ["State", "Textbox", "Textbox", "Textbox"]
        assert entry.inputs[1] is block(ui, "load_crop_pair").outputs[0]
        assert entry.inputs[2:] == block(ui, "load_crop_pair").outputs[7:9]
        assert entry.targets == [(entry.inputs[2]._id, "change")] or entry.targets == [(entry.inputs[3]._id, "change")]
        assert entry.queue is False and entry.trigger_mode == "multiple"


def _crop_snapshot(ui):
    return copy.deepcopy({key: value for key, value in vars(ui.crop).items() if key != "lock"})


def test_prepare_syncs_raw_marker_so_delayed_identical_change_preserves_ticket(ui):
    loaded(ui)
    reference, critical = "Independently entered reference", "entry\nsecond entry"
    output = ui.functions["prepare_crop_reference"](*common(ui), reference, critical, *CONTROLS)
    item = NS(view=ui.crop.generation, ticket=output[0], reference=reference, critical=critical)
    before = _crop_snapshot(ui)
    result = ui.functions["crop_reference_edited"](ui.crop, ui.crop.generation, reference, critical)
    assert result == tuple({"__type__": "update"} for _ in range(6))
    assert _crop_snapshot(ui) == before
    assert ui.crop.reference_digest == crop._raw_reference_digest(reference, critical)
    assert score(ui, item)[0:2] == ("", False) and len(ui.scores) == 1


def test_observed_edit_away_and_back_advances_revision_twice_without_reviving_approval(ui):
    item = prepared(ui)
    revision = ui.crop.revision
    first = ui.functions["crop_reference_edited"](ui.crop, item.view, item.reference + "!", item.critical)
    second = ui.functions["crop_reference_edited"](ui.crop, first[0], item.reference, item.critical)
    assert ui.crop.revision == revision + 2 and len({item.view, first[0], second[0]}) == 3
    assert first[1:5] == second[1:5] == ("", False, "", "")
    assert ui.crop.reference_digest == crop._raw_reference_digest(item.reference, item.critical)
    with pytest.raises(Exception, match="Crop review changed"):
        score(ui, item)
    assert ui.crop.pending is None and not ui.scores


@pytest.mark.parametrize("critical", ["\n", "one\n", "one\r\n", "   ", "x" * 257])
def test_partial_critical_edit_revokes_before_semantic_prepare_validation(ui, critical):
    item = prepared(ui)
    revision = ui.crop.revision
    result = ui.functions["crop_reference_edited"](ui.crop, item.view, item.reference, critical)
    assert result[1:5] == ("", False, "", "") and ui.crop.pending is None
    assert ui.crop.revision == revision + 1
    assert ui.crop.reference_digest == crop._raw_reference_digest(item.reference, critical)
    refused = ui.functions["prepare_crop_reference"](*common(ui), item.reference, critical, *CONTROLS)
    assert refused[:4] == ("", False, "", "") and "Critical" in refused[4]
    assert not ui.scores


@pytest.mark.parametrize("reference,critical", [
    (None, ""), (True, ""), ("x" * 20_001, ""), ("\ud800", ""),
    ("bounded", None), ("bounded", []), ("bounded", "x" * (64 * 257 + 1)), ("bounded", "\ud800"),
])
def test_invalid_current_raw_change_revokes_without_serializing_or_retaining_values(ui, monkeypatch, reference, critical):
    item = prepared(ui)
    loaded_before = copy.deepcopy(ui.crop.loaded)
    revision = ui.crop.revision
    hashes = []

    def must_not_encode(*args):
        hashes.append(args)
        raise AssertionError("invalid raw input reached serialization")

    monkeypatch.setattr(crop, "_hash", must_not_encode)
    monkeypatch.setattr(common_ui, "_hash", must_not_encode)
    result = ui.functions["crop_reference_edited"](ui.crop, item.view, reference, critical)
    assert result[0] != item.view and result[1:5] == ("", False, "", "")
    assert result[5] == "Reference fields are invalid or too large. Correct them, then prepare and confirm again."
    assert ui.crop.revision == revision + 1 and ui.crop.pending is None and ui.crop.reference_digest is None
    assert ui.crop.loaded == loaded_before and not hashes and not ui.scores
    # The helper now owns its globals; a valid value must reach this sentinel.
    with pytest.raises(AssertionError, match="invalid raw input reached serialization"):
        crop._raw_reference_digest("bounded", "")
    assert len(hashes) == 1


def test_raw_change_bounds_admit_maximum_control_character_strings_without_semantic_parse(ui):
    loaded(ui)
    reference, critical = "\0" * 20_000, "\0" * (64 * 257)
    result = ui.functions["crop_reference_edited"](ui.crop, ui.crop.generation, reference, critical)
    assert result[1:5] == ("", False, "", "")
    assert ui.crop.reference_digest == crop._raw_reference_digest(reference, critical)
    assert len(ui.crop.reference_digest) == 64 and not ui.scores


def test_stale_raw_change_checks_token_before_touching_either_value(ui, monkeypatch):
    item = prepared(ui)
    before = _crop_snapshot(ui)
    parsed = []

    def must_not_parse(*args):
        parsed.append(args)
        raise AssertionError("stale event parsed raw input")

    monkeypatch.setattr(crop, "_raw_reference_digest", must_not_parse)
    result = ui.functions["crop_reference_edited"](ui.crop, item.view + "stale", object(), object())
    assert result == tuple({"__type__": "update"} for _ in range(6))
    assert _crop_snapshot(ui) == before and not parsed


@pytest.mark.parametrize("reset", ["context", "load"])
def test_programmatic_empty_fields_after_reset_do_not_revoke_new_display(ui, reset):
    prepared(ui)
    if reset == "context":
        output = ui.functions["crop_context_changed"](ui.crop)
        assert ui.crop.loaded is None
    else:
        output = ui.functions["load_crop_pair"](*common(ui), *CONTROLS)
        assert output[3] is not None and ui.crop.loaded is not None
    assert output[7:9] == ("", "")
    assert ui.crop.reference_digest == crop._raw_reference_digest("", "")
    before = _crop_snapshot(ui)
    result = ui.functions["crop_reference_edited"](ui.crop, output[0], "", "")
    assert result == tuple({"__type__": "update"} for _ in range(6))
    assert _crop_snapshot(ui) == before


def test_process_api_burst_skips_stale_changes_then_fresh_edit_clears_scored_result(ui):
    item = prepared(ui)
    edit = block(ui, "crop_reference_edited")
    prepare = block(ui, "prepare_crop_reference")
    scorer = block(ui, "score_crop_reference")
    session_hash = "crop-change-burst"
    state = ui.app.state_holder[session_hash]
    for component, value in zip(prepare.inputs[:3], (ui.crop, ui.session, ui.review)):
        state[component._id] = value
    revision = ui.crop.revision
    final_reference = "Synthetic OCR challenge"

    async def scenario():
        first = None
        # Captured request payloads, not a claim about frontend dispatch timing.
        for length in range(1, 23):
            response = await ui.app.process_api(edit,
                [None, item.view, final_reference[:length], ""], state=state, session_hash=session_hash)
            if first is None:
                first = response["data"]
                assert first[1:5] == ["", False, "", ""]
            else:
                assert response["data"] == [{"__type__": "update"}] * 6
        assert ui.crop.revision == revision + 1 and ui.crop.pending is None
        prepared_response = await ui.app.process_api(prepare,
            [None, None, None, ui.review["annotation_context"], first[0], "fit", final_reference, "", *CONTROLS],
            state=state, session_hash=session_hash)
        ticket = prepared_response["data"][0]
        before = _crop_snapshot(ui)
        # A late old capture and a delayed identical current-value event must
        # leave the fresh ticket, token and displayed prepared fields alone.
        for token, text in ((item.view, "Earlier input"), (first[0], final_reference)):
            skipped = await ui.app.process_api(edit, [None, token, text, ""], state=state, session_hash=session_hash)
            assert skipped["data"] == [{"__type__": "update"}] * 6
            assert _crop_snapshot(ui) == before
        scored = await ui.app.process_api(scorer,
            [None, None, None, ui.review["annotation_context"], first[0], "fit", final_reference, "", ticket, True, *CONTROLS],
            state=state, session_hash=session_hash)
        assert scored["data"][2] and len(ui.scores) == 1
        changed = await ui.app.process_api(edit, [None, first[0], final_reference + "!", ""],
                                           state=state, session_hash=session_hash)
        assert changed["data"][1:5] == ["", False, "", ""]
        assert changed["data"][0] != first[0] and ui.crop.revision == revision + 2
        assert ui.crop.pending is None

    asyncio.run(scenario())
