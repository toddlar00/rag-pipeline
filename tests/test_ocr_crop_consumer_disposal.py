"""Pre-transfer ownership with inert services and installed Gradio callbacks.

No PDF, worker, OCR, browser, or model runs. Generated archive-policy fixtures
and synthetic PIL images exercise real service/callback cleanup. Two success
controls use installed Gradio PNG postprocessing, not its eventual disposal of
successfully transferred images. A close attempt does not prove disposal when
close itself raises.
"""

import pytest

import ocr_review_crop_archive_ui as archive
import ocr_review_crop_ui as live
from ocr_crop_review_runtime import CropPreviewError
from test_ocr_crop_review_pack import policy_pack  # noqa: F401
import test_ocr_review_crop_archive_ui as archive_fixtures
import test_ocr_review_crop_ui as live_fixtures
import test_ocr_review_crop_packs as service_fixtures


execution_ui = live_fixtures.execution_ui
live_ui = live_fixtures.ui
evidence = archive_fixtures.evidence
archive_ui = archive_fixtures.ui
service_case = service_fixtures.service_case


def raising(error):
    def fail(*_args, **_kwargs):
        raise error
    return fail


def owned_image(monkeypatch, *, close_error=None):
    image = pytest.importorskip("PIL.Image").new("RGB", (64, 32), (23, 45, 67))
    original_close, calls = image.close, []

    def close():
        calls.append("close")
        original_close()
        if close_error is not None:
            raise close_error

    monkeypatch.setattr(image, "close", close)
    return image, calls, original_close


@pytest.mark.parametrize("boundary", ["readback", "verify", "callback"])
@pytest.mark.parametrize("error_type", [ValueError, KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("close_error_type", [None, RuntimeError, KeyboardInterrupt])
def test_service_late_failure_closes_once_preserving_primary(
        service_case, monkeypatch, boundary, error_type, close_error_type):
    c = service_case
    error = error_type("primary private failure")
    secondary = None if close_error_type is None else close_error_type("secondary disposal")
    image, closes, _ = owned_image(monkeypatch, close_error=secondary)
    rendered = []
    read, verify = c.service._read, c.service._verify

    def render(*_args, **_kwargs):
        rendered.append(True)
        return image

    def readback(*args):
        if boundary == "readback" and rendered:
            raise error
        return read(*args)

    def verification():
        if boundary == "verify" and rendered:
            raise error
        return verify()

    def cancelled():
        if boundary == "callback" and rendered:
            raise error
        return False

    monkeypatch.setattr(c.service, "_render", render)
    monkeypatch.setattr(c.service, "_read", readback)
    monkeypatch.setattr(c.service, "_verify", verification)
    with pytest.raises(error_type) as caught:
        c.service.open(c.saved["pack_id"], cancel_requested=cancelled)
    assert caught.value is error
    assert closes == ["close"] and rendered == [True]
    assert not c.service._active


def test_service_late_true_cancellation_closes_without_return(service_case, monkeypatch):
    c = service_case
    image, closes, _ = owned_image(monkeypatch)
    rendered = []

    def render(*_args, **_kwargs):
        rendered.append(True)
        return image

    monkeypatch.setattr(c.service, "_render", render)
    with pytest.raises(CropPreviewError) as caught:
        c.service.open(c.saved["pack_id"], cancel_requested=lambda: bool(rendered))
    assert caught.value.code == "preview_cancelled" and closes == ["close"]


@pytest.mark.parametrize("boundary", ["notify", "exit"])
@pytest.mark.parametrize("error_type", [RuntimeError, KeyboardInterrupt, SystemExit])
def test_service_wrapper_finalizer_disposes_returned_image(service_case, monkeypatch, boundary, error_type):
    c = service_case
    error = error_type("condition finalizer")
    image, closes, _ = owned_image(monkeypatch, close_error=KeyboardInterrupt("secondary"))
    monkeypatch.setattr(c.service, "_render", lambda *_a, **_k: image)
    condition = c.service._condition

    class Condition:
        exits = 0

        def __enter__(self):
            return condition.__enter__()

        def __exit__(self, *args):
            result = condition.__exit__(*args)
            self.exits += 1
            if boundary == "exit" and self.exits == 2:
                raise error
            return result

        def notify_all(self):
            if boundary == "notify":
                raise error
            condition.notify_all()

    # Undo before the owning fixture's normal service.close teardown.
    with monkeypatch.context() as local:
        local.setattr(c.service, "_condition", Condition())
        with pytest.raises(error_type) as caught:
            c.service.open(c.saved["pack_id"])
        assert caught.value is error
    assert closes == ["close"] and not c.service._active


def test_service_success_transfers_usable_image(service_case, monkeypatch):
    c = service_case
    image, closes, release = owned_image(monkeypatch)
    monkeypatch.setattr(c.service, "_render", lambda *_a, **_k: image)
    view = c.service.open(c.saved["pack_id"])
    try:
        assert view["image"] is image and len(image.tobytes()) == 64 * 32 * 3
        assert closes == [] and not c.service._active
    finally:
        release()


@pytest.mark.parametrize("route", ["live", "archive"])
@pytest.mark.parametrize("boundary", ["identity", "display", "update", "stale", "profile"])
@pytest.mark.parametrize("detail", [False, True])
def test_ui_late_rejection_closes_once_and_preserves_clear_or_draft(
        request, monkeypatch, route, boundary, detail):
    ui = request.getfixturevalue(route + "_ui")
    module = live if route == "live" else archive
    fixture = live_fixtures if route == "live" else archive_fixtures
    if detail:
        initial = fixture.loaded(ui)
        initial[3 if route == "live" else 1]["value"].close()
    elif route == "live":
        fixture.captured(ui)
    else:
        fixture.selected(ui)
    image, closes, _ = owned_image(monkeypatch, close_error=SystemExit("secondary"))
    calls = []

    def render(*_args, **_kwargs):
        calls.append("render")
        return image

    if route == "live":
        monkeypatch.setattr(ui.coordinator, "render_crop_preview", render)
    else:
        original_open = ui.service.open

        def opened(*args, **kwargs):
            value = original_open(*args, **kwargs)
            value["image"].close()
            value["image"] = render()
            return value

        monkeypatch.setattr(ui.service, "open", opened)
    if boundary in ("identity", "display", "update"):
        name = {"identity": "_image_identity", "display": "_display", "update": "preview_image_update"}[boundary]
        original = getattr(module, name)

        def fail(*args, **kwargs):
            if boundary != "update" or args[0] is not None:
                raise ValueError("private post-render failure")
            return original(*args, **kwargs)

        monkeypatch.setattr(module, name, fail)
    else:
        original = module._image_identity

        def changed(value):
            result = original(value)
            state = ui.crop if route == "live" else ui.state
            if boundary == "stale":
                state.generation = "changed-after-render"
            else:
                state.preview_profile = "dpi576"
            return result

        monkeypatch.setattr(module, "_image_identity", changed)

    def invoke():
        fields = ("raw partial draft", "OCR\n") if detail else ()
        if route == "live":
            name = "reload_crop_detail" if detail else "load_crop_pair"
            return ui.functions[name](*fixture.common(ui), *fields, *fixture.CONTROLS)
        name = "reload_archive_detail" if detail else "open_crop_archive"
        return fixture.call(ui, name, fixture.PACK, ui.state.generation, *fields, "fit")

    if boundary in ("stale", "profile"):
        with pytest.raises(Exception, match="changed"):
            invoke()
    else:
        result = invoke()
        assert result[3 if route == "live" else 1] is None
        fields = result[7:9] if route == "live" else result[6:8]
        assert fields == (({"__type__": "update"},) * 2 if detail else ("", ""))
        state = ui.crop if route == "live" else ui.state
        assert state.pending is None and state.loaded is None
    assert calls == ["render"] and closes == ["close"]


@pytest.mark.parametrize("route", ["live", "archive"])
@pytest.mark.parametrize("error_type", [KeyboardInterrupt, SystemExit])
def test_ui_cancellation_identity_survives_secondary_disposal(request, monkeypatch, route, error_type):
    ui = request.getfixturevalue(route + "_ui")
    module = live if route == "live" else archive
    fixture = live_fixtures if route == "live" else archive_fixtures
    fixture.captured(ui) if route == "live" else fixture.selected(ui)
    image, closes, _ = owned_image(monkeypatch, close_error=KeyboardInterrupt("secondary"))
    error = error_type("primary cancellation")
    if route == "live":
        monkeypatch.setattr(ui.coordinator, "render_crop_preview", lambda *_a, **_k: image)
    else:
        monkeypatch.setattr(ui.service, "open", lambda *_a, **_k: {**ui.view, "image": image})
    monkeypatch.setattr(module, "_image_identity", raising(error))
    with pytest.raises(error_type) as caught:
        if route == "live":
            ui.functions["load_crop_pair"](*fixture.common(ui), *fixture.CONTROLS)
        else:
            fixture.call(ui, "open_crop_archive", fixture.PACK, ui.state.generation)
    assert caught.value is error and closes == ["close"]


@pytest.mark.parametrize("route", ["live", "archive"])
def test_ui_success_transfers_same_usable_image(request, monkeypatch, tmp_path, route):
    ui = request.getfixturevalue(route + "_ui")
    fixture = live_fixtures if route == "live" else archive_fixtures
    fixture.captured(ui) if route == "live" else fixture.selected(ui)
    image, closes, release = owned_image(monkeypatch)
    if route == "live":
        monkeypatch.setattr(ui.coordinator, "render_crop_preview", lambda *_a, **_k: image)
        result = ui.functions["load_crop_pair"](*fixture.common(ui), *fixture.CONTROLS)
    else:
        monkeypatch.setattr(ui.service, "open", lambda *_a, **_k: {**ui.view, "image": image})
        result = fixture.call(ui, "open_crop_archive", fixture.PACK, ui.state.generation)
    try:
        assert result[3 if route == "live" else 1]["value"] is image
        assert len(image.tobytes()) == 64 * 32 * 3 and closes == []
        component_id = "ocr-live-source-preview" if route == "live" else "ocr-archive-source-preview"
        component = next(item for item in ui.app.blocks.values() if item.elem_id == component_id)
        monkeypatch.setattr(component, "GRADIO_CACHE", str(tmp_path))
        processed = component.postprocess(image)
        with pytest.importorskip("PIL.Image").open(processed.path) as delivered:
            assert delivered.format == "PNG" and delivered.mode == "RGB" and delivered.size == (64, 32)
            assert delivered.tobytes() == bytes((23, 45, 67)) * (64 * 32)
        # This verifies usable transfer through real postprocessing, not that
        # Gradio disposes the original PIL object; the test caller still owns it.
        assert closes == []
    finally:
        release()


@pytest.mark.parametrize("route", ["live", "archive"])
@pytest.mark.parametrize("error_type", [ValueError, KeyboardInterrupt, SystemExit])
def test_ui_output_lock_exit_failure_is_still_before_image_transfer(request, monkeypatch, route, error_type):
    ui = request.getfixturevalue(route + "_ui")
    fixture = live_fixtures if route == "live" else archive_fixtures
    fixture.captured(ui) if route == "live" else fixture.selected(ui)
    image, closes, _ = owned_image(monkeypatch, close_error=KeyboardInterrupt("secondary"))
    state = ui.crop if route == "live" else ui.state
    lock, error = state.lock, error_type("output lock exit")

    class Lock:
        failed = False

        def __enter__(self):
            return lock.__enter__()

        def __exit__(self, *args):
            result = lock.__exit__(*args)
            if state.loaded is not None and not self.failed:
                self.failed = True
                raise error
            return result

    monkeypatch.setattr(state, "lock", Lock())
    if route == "live":
        monkeypatch.setattr(ui.coordinator, "render_crop_preview", lambda *_a, **_k: image)
    else:
        monkeypatch.setattr(ui.service, "open", lambda *_a, **_k: {**ui.view, "image": image})

    def invoke():
        if route == "live":
            return ui.functions["load_crop_pair"](*fixture.common(ui), *fixture.CONTROLS)
        return fixture.call(ui, "open_crop_archive", fixture.PACK, ui.state.generation)

    if error_type is ValueError:
        result = invoke()
        assert result[3 if route == "live" else 1] is None
        assert state.loaded is None and state.pending is None
    else:
        with pytest.raises(error_type) as caught:
            invoke()
        assert caught.value is error
    assert state.lock.failed and closes == ["close"]


@pytest.mark.parametrize("route", ["live", "archive"])
def test_failed_inner_transfer_is_not_closed_again_by_ui(request, monkeypatch, route):
    ui = request.getfixturevalue(route + "_ui")
    fixture = live_fixtures if route == "live" else archive_fixtures
    fixture.captured(ui) if route == "live" else fixture.selected(ui)
    image, closes, _ = owned_image(monkeypatch)

    def fail(*_args, **_kwargs):
        image.close()  # The inert inner owner never returns its image.
        raise CropPreviewError(code="input_changed")

    if route == "live":
        monkeypatch.setattr(ui.coordinator, "render_crop_preview", fail)
        result = ui.functions["load_crop_pair"](*fixture.common(ui), *fixture.CONTROLS)
    else:
        monkeypatch.setattr(ui.service, "open", fail)
        result = fixture.call(ui, "open_crop_archive", fixture.PACK, ui.state.generation)
    assert result[3 if route == "live" else 1] is None and closes == ["close"]
