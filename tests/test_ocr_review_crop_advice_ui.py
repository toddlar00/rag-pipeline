"""Source-advice presentation over installed Gradio and generated crop fixtures.

The existing fixtures name their inert renderer, retained-input and publication
ports explicitly. These tests perform no OCR, native PDF, browser or real Save.
Process-api controls cover Image postprocessing, not paint/network ordering.
"""

import asyncio
import copy
import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace as NS

import pytest

import ocr_crop_advice as advice
import test_ocr_review_crop_archive_uncertainty as archive
import test_ocr_review_crop_uncertainty_live_ui as live
import test_ocr_review_crop_archive_ui as legacy_archive
import test_ocr_review_crop_ui as legacy_live


live_ui = live.ui
archive_ui = archive.ui
reports = archive.reports
legacy_live_ui = legacy_live.ui
execution_ui = legacy_live.execution_ui
legacy_archive_ui = legacy_archive.ui
evidence = legacy_archive.evidence
SKIP = {"__type__": "update"}


@pytest.fixture(params=("live", "archive"))
def panel(request, monkeypatch, tmp_path):
    name = request.param
    ui = request.getfixturevalue(name + "_ui")
    state = ui.crop if name == "live" else ui.state
    result = NS(name=name, ui=ui, state=state, observations=[], hook=None,
                details_index=6 if name == "live" else 4,
                image_index=3 if name == "live" else 1)
    target = ui.coordinator if name == "live" else ui.service
    method = "render_crop_preview_with_view" if name == "live" else "open_with_view"
    original = getattr(target, method)

    def render(*args, **kwargs):
        value = original(*args, **kwargs)
        owner = value if name == "live" else value["preview"]
        image = owner.image
        raw = image.tobytes()
        observed = NS(owner=owner, raw=raw, reads=0, closes=0)
        result.observations.append(observed)
        read, close = image.tobytes, image.close

        def counted_read():
            observed.reads += 1
            return read()

        def counted_close():
            observed.closes += 1
            close()

        # Count only the UI's serialization after the fixture built its owner.
        monkeypatch.setattr(image, "tobytes", counted_read)
        monkeypatch.setattr(image, "close", counted_close)
        if result.hook:
            result.hook(observed)
        return value

    monkeypatch.setattr(target, method, render)
    if name == "live":
        live.capture(ui)
    else:
        archive.selected(ui)
        image = ui.functions["open_crop_archive"].outputs[1]
        monkeypatch.setattr(image, "GRADIO_CACHE", str(tmp_path / "archive-images"))
    return result


def load(panel, *, reload=False):
    ui = panel.ui
    if panel.name == "live":
        return live.load(ui, reload=reload)
    result = archive.call(ui, "reload_archive_detail" if reload else "open_crop_archive",
        archive.PACK, ui.state.generation, ui.reference, ui.critical, ui.state.preview_profile)
    if type(result[14]) is str:
        ui.image_token = result[14]
    return result


def cancel(panel, profile="fit"):
    if panel.name == "live":
        return live.invoke(panel.ui, "cancel_crop_preview", [panel.state, profile], "full")
    return archive.call(panel.ui, "cancel_archive_preview", profile)


def action_counts(panel):
    ui = panel.ui
    if panel.name == "live":
        return len(ui.authors), len(ui.scores), len(ui.service.calls), len(ui.service.commits)
    return tuple(ui.calls.count(name) for name in ("author", "compare", "save", "commit"))


def observe_advice(panel, monkeypatch):
    calls = []
    original = advice.build_crop_advice

    def build(rgb, *, scope, raster_view):
        assert not panel.state.lock._is_owned(), "measurement held the session lock"
        before = copy.deepcopy((scope, raster_view))
        result = original(rgb, scope=scope, raster_view=raster_view)
        assert (scope, raster_view) == before
        calls.append((rgb, before, copy.deepcopy(result)))
        return result

    monkeypatch.setattr(advice, "build_crop_advice", build)
    return calls


@pytest.mark.parametrize("profile", ["fit", "dpi288", "dpi576"])
@pytest.mark.parametrize("reload", [False, True])
def test_exact_single_rgb_capture_advice_only_details_and_status(panel, monkeypatch, profile, reload):
    if reload:
        load(panel)
    cancel(panel, profile)
    if panel.name == "live":
        panel.ui.profile = profile
    calls = observe_advice(panel, monkeypatch)
    before = action_counts(panel)
    result = load(panel, reload=reload)
    assert len(result) == (17 if panel.name == "live" else 19)
    assert panel.state.loaded is not None and len(calls) == 1
    observed = panel.observations[-1]
    rgb, (scope, view), built = calls[0]
    assert rgb is observed.raw or rgb == observed.raw
    assert observed.reads == 1 and observed.closes == 0
    assert view["rgb_sha256"] == hashlib.sha256(rgb).hexdigest()
    assert view["preview_profile"] == profile and panel.state.loaded["scope"] == scope
    assert json.loads(result[panel.details_index])["advice"] == built
    assert advice.crop_advice_text(built) in result[13]
    assert result[panel.image_index]["value"] is observed.owner.image
    expected = ({"pair_sha256", "pins", "context", "scope", "raster_view", "preview_profile"}
                if panel.name == "live" else
                {"pack_id", "pack_sha256", "scope", "raster_view", "mode", "preview_profile", "pair"})
    assert set(panel.state.loaded) == expected
    assert "advice" not in vars(panel.state) and panel.state.pending is panel.state.last_score is None
    assert action_counts(panel) == before == (0, 0, 0, 0)
    if panel.name == "live":
        assert panel.state.image_seen is False


@pytest.mark.parametrize("status", ["unavailable", "ambiguous"])
def test_expected_advisory_nonjudgments_do_not_refuse_valid_preview(panel, monkeypatch, status):
    # Explicit API/presentation double: pure quality status semantics have their
    # own tests. Here uncertainty is data, never an invented zero or exception.
    value = {"test_advisory_status": status}
    text = "Source rendering advice only: " + status + "; inspect the original."
    monkeypatch.setattr(advice, "build_crop_advice", lambda *_a, **_k: copy.deepcopy(value))
    monkeypatch.setattr(advice, "crop_advice_text", lambda item: text if item == value else pytest.fail("changed advice"))
    result = load(panel)
    assert result[panel.image_index]["value"] is panel.observations[-1].owner.image
    assert json.loads(result[panel.details_index])["advice"] == value
    assert text in result[13] and panel.observations[-1].closes == 0
    assert panel.state.pending is panel.state.last_score is None and action_counts(panel) == (0, 0, 0, 0)


@pytest.mark.parametrize("fault", ["hash", "bytearray", "short", "long", "mode", "zero_side", "large_side"])
def test_invalid_image_binding_refuses_before_advice_and_closes_once(panel, monkeypatch, fault):
    def corrupt(observed):
        image = observed.owner.image
        if fault == "mode":
            monkeypatch.setattr(image, "_mode", "L")
        elif fault in ("zero_side", "large_side"):
            monkeypatch.setattr(image, "_size", (0 if fault == "zero_side" else 1401, image.height))
        else:
            bad = (bytes([observed.raw[0] ^ 1]) + observed.raw[1:] if fault == "hash" else
                   bytearray(observed.raw) if fault == "bytearray" else
                   observed.raw[:-1] if fault == "short" else observed.raw + b"x")
            monkeypatch.setattr(image, "tobytes", lambda: bad)

    panel.hook = corrupt
    monkeypatch.setattr(advice, "build_crop_advice", lambda *_a, **_k: pytest.fail("invalid RGB reached advice"))
    result = load(panel)
    assert result[panel.image_index] is None and result[panel.details_index] == ""
    assert panel.state.loaded is None and not panel.state.image_token
    assert panel.observations[-1].closes == 1 and action_counts(panel) == (0, 0, 0, 0)


@pytest.mark.parametrize("stage", ["measurement", "formatting"])
@pytest.mark.parametrize("exception", [ValueError, KeyboardInterrupt, SystemExit])
def test_advice_failure_uses_existing_refusal_or_preserves_cancellation(panel, monkeypatch, stage, exception):
    error = exception("inert advisory failure")

    def fail(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(advice, "build_crop_advice" if stage == "measurement" else "crop_advice_text", fail)
    if exception is ValueError:
        result = load(panel)
        assert result[panel.image_index] is None and result[panel.details_index] == ""
        assert "inert advisory failure" not in result[13]
    else:
        with pytest.raises(exception) as caught:
            load(panel)
        assert caught.value is error
    assert panel.state.loaded is None and not panel.state.image_token
    assert panel.observations[-1].closes == 1 and action_counts(panel) == (0, 0, 0, 0)


def test_detail_reload_retains_exact_history_partial_fields_and_revokes_approval(panel):
    ui = panel.ui
    load(panel)
    if panel.name == "live":
        live.add_uncertainty(ui)
        live.prepare(ui)
    else:
        archive.event(ui, "mode", mode="annotate")
        archive.event(ui, "whole_scope")
        archive.apply(ui)
        archive.prepare(ui)
    assert panel.state.pending is not None
    history = copy.deepcopy(panel.state.uncertainty)
    scope = copy.deepcopy(panel.state.loaded["scope"])
    old_image = panel.state.image_token
    before = action_counts(panel)
    ui.reference, ui.critical = "unfinished transcription", "OCR\n"
    result = load(panel, reload=True)
    fields = result[7:9] if panel.name == "live" else result[6:8]
    assert fields == (SKIP, SKIP)
    assert panel.state.uncertainty["journal"] == history["journal"]
    assert panel.state.uncertainty["annotations"] == history["annotations"]
    assert panel.state.uncertainty["dirty"] is True
    assert panel.state.loaded["scope"] == scope and panel.state.image_token != old_image
    assert panel.state.pending is panel.state.last_score is None and action_counts(panel) == before
    assert "advice" in json.loads(result[panel.details_index])


@pytest.mark.parametrize("change", ["cancel", "profile_cancel", "source", "stale_profile_event"])
def test_held_measurement_cannot_install_after_invalidating_change(panel, monkeypatch, change):
    entered, release = threading.Event(), threading.Event()
    original = advice.build_crop_advice
    captured = panel.state.generation

    def held(*args, **kwargs):
        assert not panel.state.lock._is_owned()
        entered.set()
        assert release.wait(5), "advice release not received"
        return original(*args, **kwargs)

    monkeypatch.setattr(advice, "build_crop_advice", held)
    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(load, panel)
        try:
            assert entered.wait(5), "advice was not reached"
            if change in ("cancel", "profile_cancel"):
                update = pool.submit(cancel, panel, "dpi288" if change == "profile_cancel" else "fit")
                update.result(timeout=2)
            elif change == "source":
                panel.ui.workspace.document.source_sha256 = "f" * 64
            elif panel.name == "live":
                result = panel.ui.functions["crop_preview_profile_changed"](panel.state, captured, "dpi288")
                assert result == (SKIP,) * 17
            else:
                result = archive.call(panel.ui, "archive_preview_profile_changed", captured, "dpi288")
                assert result == (SKIP,) * 19
        finally:
            release.set()
        if change == "stale_profile_event":
            result = future.result(timeout=5)
            assert panel.state.loaded["preview_profile"] == "fit"
            assert result[panel.image_index] is not None and panel.observations[-1].closes == 0
        elif panel.name == "live" and change == "source":
            result = future.result(timeout=5)
            assert result[panel.image_index] is None
        else:
            with pytest.raises(Exception, match="view changed"):
                future.result(timeout=5)
        if change != "stale_profile_event":
            assert panel.state.loaded is None and not panel.state.image_token
            assert panel.observations[-1].closes == 1
    assert action_counts(panel) == (0, 0, 0, 0)


def test_actual_image_postprocess_failure_does_not_make_advice_into_image_authority(panel, monkeypatch):
    ui = panel.ui
    old = panel.state.generation
    measured = observe_advice(panel, monkeypatch)

    def fail(*_a, **_k):
        raise RuntimeError("inert advisory image postprocess failure")

    if panel.name == "live":
        with monkeypatch.context() as patch:
            patch.setattr(ui.gr.Image, "postprocess", fail)
            with pytest.raises(RuntimeError, match="inert advisory image"):
                live.load(ui, api=True)
        assert ui.token == old and ui.editor is None and panel.state.image_seen is False
        for token in (old, panel.state.generation):
            assert ui.functions["crop_reference_edited"](panel.state, token, "edit", "", None) == (SKIP,) * 9
        with pytest.raises(ui.gr.Error):
            live.prepare(ui)
    else:
        import gradio as gr

        api_state = ui.app.state_holder["advice-unseen-archive"]
        entry = ui.functions["open_crop_archive"]
        api_state[entry.inputs[0]._id] = panel.state

        async def run():
            await ui.app.process_api(ui.functions["refresh_crop_archives"], [None],
                state=api_state, session_hash="advice-unseen-archive")
            archive.call(ui, "select_crop_archive", archive.PACK, panel.state.generation)
            with monkeypatch.context() as patch:
                patch.setattr(gr.Image, "postprocess", fail)
                with pytest.raises(RuntimeError, match="inert advisory image"):
                    await ui.app.process_api(entry, [None, archive.PACK, panel.state.generation,
                        ui.reference, ui.critical, "fit"], state=api_state, session_hash="advice-unseen-archive")

        asyncio.run(run())
        for token in (old, panel.state.generation):
            assert archive.call(ui, "archive_reference_edited", token, "edit", "", panel.state.action_token, "") == (SKIP,) * 19
            with pytest.raises(Exception):
                archive.call(ui, "prepare_archive_reference", archive.PACK, token, ui.reference,
                    ui.critical, panel.state.action_token, "fit", "", *ui.form)
    assert len(measured) == 1 and panel.state.loaded is not None
    assert panel.state.pending is panel.state.last_score is None and action_counts(panel) == (0, 0, 0, 0)


@pytest.mark.parametrize("name", ["live", "archive"])
def test_legacy_pil_routes_never_call_advice(request, monkeypatch, name):
    monkeypatch.setattr(advice, "build_crop_advice", lambda *_a, **_k: pytest.fail("legacy route acquired advice"))
    ui = request.getfixturevalue("legacy_" + name + "_ui")
    fixture = legacy_live if name == "live" else legacy_archive
    fixture.loaded(ui)
    assert (ui.crop if name == "live" else ui.state).loaded is not None
