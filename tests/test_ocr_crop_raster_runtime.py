"""Same-render metadata: generated PDFs and inert ownership fault controls.

No private PDF, OCR, model, worker/server/browser launch or annotation UI. Native
RGB parity is byte evidence, not visual/optical annotation acceptance.
"""

from copy import deepcopy
import hashlib
import json
import sys
from types import SimpleNamespace

import pytest

import ocr_crop_comparison as comparison
import ocr_crop_raster_view as raster
import ocr_crop_review_runtime as runtime
from ocr_review_runtime import ReviewWorkspace
import test_ocr_crop_review_runtime as existing


def scope_from_raw(raw, bbox=(.073, .121, .813, .857)):
    import pymupdf
    with pymupdf.open(stream=raw, filetype="pdf") as document:
        page = document[0]
        scope = {"source_sha256": hashlib.sha256(raw).hexdigest(), "page_count": len(document),
                 "page_number": 1, "coordinate_system": "original_page_display_fraction",
                 "bbox": list(bbox), "page_geometry": {"display_rect_points": list(page.rect),
                    "cropbox_points": list(page.cropbox), "rotation_degrees": page.rotation}}
    return existing._rehash(scope)


@pytest.fixture(scope="module", params=[(rotation, cropped) for rotation in (0, 90, 180, 270)
                                       for cropped in (False, True)])
def native_case(request):
    pytest.importorskip("PIL")
    rotation, cropped = request.param
    raw = existing._source(rotation=rotation, cropped=cropped, annotations=True)
    return raw, scope_from_raw(raw)


@pytest.fixture(scope="module")
def small_native():
    pytest.importorskip("PIL")
    raw = existing._source(cropped=False, width=72, height=72)
    return raw, scope_from_raw(raw, (0., 0., 1., 1.))


@pytest.fixture(scope="module")
def wide_native():
    pytest.importorskip("PIL")
    raw = existing._source(cropped=False, width=2048, height=1024)
    return raw, scope_from_raw(raw, (.0001, .0003, .83, .94))


@pytest.mark.parametrize("profile", runtime.PREVIEW_PROFILES)
def test_generated_profiles_rotation_cropbox_asymmetry_same_render_parity(native_case, monkeypatch, profile):
    import pymupdf
    from PIL import Image
    raw, scope = native_case
    before = deepcopy(scope)
    calls, allocations = [], []
    get_pixmap, frombytes = pymupdf.Page.get_pixmap, Image.frombytes

    def observed(page, **kwargs):
        pixmap = get_pixmap(page, **kwargs)
        calls.append((list(kwargs["clip"]), list(kwargs["matrix"]),
                      [pixmap.x, pixmap.y, pixmap.x + pixmap.width, pixmap.y + pixmap.height],
                      hashlib.sha256(pixmap.samples_mv).hexdigest()))
        assert kwargs["alpha"] is False and kwargs["annots"] is True
        return pixmap

    def allocated(*args, **kwargs):
        image = frombytes(*args, **kwargs)
        allocations.append(image)
        return image

    monkeypatch.setattr(pymupdf.Page, "get_pixmap", observed)
    monkeypatch.setattr(Image, "frombytes", allocated)
    ordinary = runtime._render_pdf(raw, scope, preview_profile=profile)
    result = None
    try:
        calls.clear()
        allocations.clear()
        result = runtime._render_pdf_with_view(raw, scope, preview_profile=profile)
        assert type(result) is runtime.CropRasterPreview
        assert len(calls) == len(allocations) == 1
        assert result.image is allocations[0]
        assert result.image.mode == "RGB" and not result.image.info
        assert result.image.size == ordinary.size
        assert result.image.tobytes() == ordinary.tobytes()
        view = result.raster_view
        assert view["command_clip"] == calls[0][0] and view["command_matrix"] == calls[0][1]
        assert view["pixel_rect"] == calls[0][2]
        assert view["rgb_sha256"] == calls[0][3] == hashlib.sha256(result.image.tobytes()).hexdigest()
        assert view["source_sha256"] == hashlib.sha256(raw).hexdigest()
        assert view["width"] == ordinary.width and view["height"] == ordinary.height
        assert raster.validate_raster_view(view, scope=scope) == view
        assert result.raster_view_bytes == comparison._bytes(view, 4096)
        assert len(result.raster_view_bytes) <= 4096
        view["native_clip"][0] = -123.0
        assert result.raster_view["native_clip"][0] != -123.0 and scope == before
    finally:
        ordinary.close()
        if result is not None:
            result.close()


def test_nonintegral_fit_keeps_historical_scale_and_exact_rgb(wide_native):
    raw, scope = wide_native
    ordinary = runtime._render_pdf(raw, scope)
    result = None
    try:
        result = runtime._render_pdf_with_view(raw, scope)
        view = result.raster_view
        clip = view["command_clip"]
        scale = min(2., 1398 / max(clip[2] - clip[0], clip[3] - clip[1]))
        assert view["command_matrix"][0] == scale
        assert view["command_matrix"] != view["native_matrix"]
        assert result.image.size == ordinary.size
        assert result.image.tobytes() == ordinary.tobytes()
    finally:
        ordinary.close()
        if result is not None:
            result.close()


def test_pil_compatibility_domain_remains_broader_than_metadata_domain(small_native):
    raw, original_scope = small_native
    scope = deepcopy(original_scope)
    scope["bbox"][0] = 2.0 ** -160
    existing._rehash(scope)
    ordinary = runtime._render_pdf(raw, scope)
    try:
        assert ordinary.size == (144, 144)
        with pytest.raises(runtime.CropPreviewError) as caught:
            runtime._render_pdf_with_view(raw, scope)
        assert caught.value.code == "geometry_mismatch"
        assert ordinary.getpixel((1, 1)) == (255, 0, 0)
    finally:
        ordinary.close()


@pytest.mark.parametrize("profile", ["dpi288", "dpi576"])
def test_oversized_metadata_detail_refuses_without_render_or_fit_fallback(wide_native, monkeypatch, profile):
    import pymupdf
    raw, scope = wide_native
    monkeypatch.setattr(pymupdf.Page, "get_pixmap", lambda *_a, **_k: pytest.fail("oversized render"))
    with pytest.raises(runtime.CropPreviewError) as caught:
        runtime._render_pdf_with_view(raw, scope, preview_profile=profile)
    assert caught.value.code == "raster_limit"


@pytest.mark.parametrize("site", ["JM_rect_from_py", "JM_matrix_from_py"])
@pytest.mark.parametrize("failure_type", [RuntimeError, MemoryError, KeyboardInterrupt, SystemExit])
def test_native_numeric_failure_is_static_and_cancellation_keeps_identity(small_native, monkeypatch, site, failure_type):
    import pymupdf
    raw, scope = small_native
    failure = failure_type("PRIVATE numeric values")
    original = getattr(pymupdf, site)
    injections = []

    def fail(*args, **kwargs):
        if sys._getframe(1).f_code is runtime._render_pdf_core.__code__:
            injections.append(True)
            raise failure
        return original(*args, **kwargs)

    monkeypatch.setattr(pymupdf, site, fail)
    monkeypatch.setattr(pymupdf.Page, "get_pixmap", lambda *_a, **_k: pytest.fail("failed capture rendered"))
    expected = runtime.CropPreviewError if isinstance(failure, Exception) else failure_type
    with pytest.raises(expected) as caught:
        runtime._render_pdf_with_view(raw, scope)
    assert injections == [True]
    if isinstance(failure, Exception):
        assert caught.value.code == "geometry_mismatch" and "PRIVATE" not in str(caught.value)
        assert caught.value.__cause__ is None and caught.value.__suppress_context__
    else:
        assert caught.value is failure


class OwnedImage:
    def __init__(self, failure=None):
        self.closes, self.failure = 0, failure
        self.mode, self.size, self.raw, self.byte_reads = "RGB", (144, 144), b"", 0

    def close(self):
        self.closes += 1
        if self.failure is not None:
            raise self.failure

    def tobytes(self):
        self.byte_reads += 1
        return self.raw


def inert_frombytes(image):
    def allocated(mode, size, raw):
        image.mode, image.size, image.raw = mode, size, raw
        return image
    return allocated


def pure_view():
    scope = {"source_sha256": "a" * 64, "page_count": 1, "page_number": 1,
             "coordinate_system": "original_page_display_fraction", "bbox": [0., 0., 1., 1.],
             "page_geometry": {"display_rect_points": [0., 0., 72., 72.],
                               "cropbox_points": [0., 0., 72., 72.], "rotation_degrees": 0}}
    existing._rehash(scope)
    view = raster.build_raster_view(scope=scope, preview_profile="fit", command_clip=[0., 0., 72., 72.],
        command_matrix=[2., 0., 0., 2., 0., 0.], native_clip=[0., 0., 72., 72.],
        native_matrix=[2., 0., 0., 2., 0., 0.], projected_rect=[0., 0., 144., 144.],
        pixel_rect=[0, 0, 144, 144], width=144, height=144, rgb_sha256="b" * 64)
    return scope, view


@pytest.mark.parametrize("failure_type", [None, RuntimeError, KeyboardInterrupt, SystemExit])
def test_owner_detaches_metadata_and_closes_at_most_once(failure_type):
    _, view = pure_view()
    failure = failure_type("secondary close") if failure_type else None
    image = OwnedImage(failure)
    owner = runtime.CropRasterPreview(image, view)
    saved = owner.raster_view_bytes
    assert type(saved) is bytes and json.loads(saved) == view
    assert owner.image is image
    view["native_clip"][0] = 100.0
    owner.raster_view["native_clip"][0] = 200.0
    assert owner.raster_view_bytes == saved and owner.raster_view["native_clip"][0] == 0.0
    for key in ("image", "raster_view", "raster_view_bytes"):
        with pytest.raises(AttributeError):
            setattr(owner, key, None)
    if failure:
        with pytest.raises(failure_type) as caught:
            owner.close()
        assert caught.value is failure
    else:
        owner.close()
    owner.close()
    assert image.closes == 1


def test_constructor_failure_does_not_take_callers_image_ownership():
    image = OwnedImage()
    with pytest.raises(ValueError):
        runtime.CropRasterPreview(image, {"oversized": "x" * 4097})
    assert image.closes == 0
    image.close()
    assert image.closes == 1


@pytest.mark.parametrize("site", ["native_rect", "native_matrix", "builder", "owner", "document_exit"])
@pytest.mark.parametrize("close_type", [None, RuntimeError, KeyboardInterrupt, SystemExit])
def test_late_metadata_refusal_closes_raw_image_once(small_native, monkeypatch, site, close_type):
    import pymupdf
    from PIL import Image
    raw, scope = small_native
    image = OwnedImage(close_type("secondary close") if close_type else None)
    allocations, renders, opened = [], [], []
    real_frombytes, real_pixmap, real_open = Image.frombytes, pymupdf.Page.get_pixmap, pymupdf.open

    def allocated(*args, **kwargs):
        # Native samples exist, but disposal counting uses an inert PIL owner.
        allocations.append((args, kwargs))
        return inert_frombytes(image)(*args, **kwargs)

    def rendered(page, **kwargs):
        renders.append(kwargs)
        return real_pixmap(page, **kwargs)

    def fail(*_args, **_kwargs):
        raise ValueError("PRIVATE metadata failure")

    if site in {"native_rect", "native_matrix"}:
        name = "JM_rect_from_py" if site == "native_rect" else "JM_matrix_from_py"
        original = getattr(pymupdf, name)
        keys = ("x0", "y0", "x1", "y1") if site == "native_rect" else ("a", "b", "c", "d", "e", "f")

        def changed(*args, **kwargs):
            value = original(*args, **kwargs)
            if sys._getframe(1).f_code is not runtime._render_pdf_core.__code__:
                return value  # Other document/pixmap conversions stay real.
            result = {key: getattr(value, key) for key in keys}
            result[keys[0]] += .25
            return SimpleNamespace(**result)

        monkeypatch.setattr(pymupdf, name, changed)
    elif site == "builder":
        monkeypatch.setattr(runtime, "_build_raster_view", fail)
    elif site == "owner":
        monkeypatch.setattr(runtime, "CropRasterPreview", fail)
    else:
        class ExitFailure:
            def __init__(self, *args, **kwargs):
                self.document = real_open(*args, **kwargs)
                opened.append(self.document)

            def __enter__(self):
                return self.document.__enter__()

            def __exit__(self, *args):
                self.document.__exit__(*args)
                raise ValueError("PRIVATE document exit")

        monkeypatch.setattr(pymupdf, "open", ExitFailure)
    monkeypatch.setattr(Image, "frombytes", allocated)
    monkeypatch.setattr(pymupdf.Page, "get_pixmap", rendered)
    with pytest.raises(runtime.CropPreviewError) as caught:
        runtime._render_pdf_with_view(raw, scope)
    assert caught.value.code == ("pdf_render" if site == "document_exit" else "geometry_mismatch")
    assert image.closes == len(allocations) == len(renders) == 1
    assert all(document.is_closed for document in opened)
    assert real_frombytes is not Image.frombytes


@pytest.mark.parametrize("failure_type", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("site", ["builder", "owner"])
def test_metadata_after_allocation_cancellation_not_replaced(small_native, monkeypatch, site, failure_type):
    from PIL import Image
    raw, scope = small_native
    image = OwnedImage(SystemExit("secondary disposal"))
    primary = failure_type("primary cancellation")

    def fail(*_args, **_kwargs):
        raise primary

    monkeypatch.setattr(Image, "frombytes", inert_frombytes(image))
    monkeypatch.setattr(runtime, "_build_raster_view" if site == "builder" else "CropRasterPreview", fail)
    with pytest.raises(failure_type) as caught:
        runtime._render_pdf_with_view(raw, scope)
    assert caught.value is primary and image.closes == 1


@pytest.mark.parametrize("field", ["x", "y", "width", "height", "n", "stride", "samples"])
def test_actual_pixmap_mismatch_refuses_before_pil_allocation(small_native, monkeypatch, field):
    import pymupdf
    from PIL import Image
    raw, scope = small_native
    original = pymupdf.Page.get_pixmap
    calls = []

    def changed(page, **kwargs):
        actual = original(page, **kwargs)
        calls.append(actual)
        result = {name: getattr(actual, name) for name in ("x", "y", "width", "height", "n", "stride")}
        result["samples_mv"] = actual.samples_mv
        if field == "samples":
            result["samples_mv"] = result["samples_mv"][:-1]
        else:
            result[field] += 1
        return SimpleNamespace(**result)

    monkeypatch.setattr(pymupdf.Page, "get_pixmap", changed)
    monkeypatch.setattr(Image, "frombytes", lambda *_a, **_k: pytest.fail("invalid pixmap allocated PIL"))
    with pytest.raises(runtime.CropPreviewError) as caught:
        runtime._render_pdf_with_view(raw, scope)
    assert caught.value.code == ("geometry_mismatch" if field in {"x", "y", "width", "height"} else "pdf_render")
    assert len(calls) == 1


def inert_workspace(monkeypatch):
    workspace = object.__new__(ReviewWorkspace)
    workspace.document = SimpleNamespace(source_sha256="a" * 64, recovery_sha256="b" * 64, page_count=1)
    workspace.pdf_path, workspace.recovery_path, workspace.output_dir = "source", "recovery", "output"
    monkeypatch.setattr(workspace, "_directory_generation", lambda: (1, 2))
    monkeypatch.setattr(workspace, "verify_inputs", lambda: None)
    monkeypatch.setattr(runtime, "_snapshot", lambda path, _limit:
        (b"source" if path == "source" else b"recovery", ("a" if path == "source" else "b") * 64, (1, 2, 3)))
    return workspace


@pytest.mark.parametrize("site", ["verify", "binding", "source", "recovery", "directory"])
@pytest.mark.parametrize("kind", ["mismatch", "ordinary", "interrupt"])
def test_metadata_owner_remains_visible_to_outer_input_cleanup(monkeypatch, site, kind):
    workspace = inert_workspace(monkeypatch)
    scope, view = pure_view()
    image = OwnedImage(SystemExit("secondary close"))
    owner = runtime.CropRasterPreview(image, view)
    state = SimpleNamespace(rendered=False, injected=False)
    primary = OSError("PRIVATE late input") if kind == "ordinary" else KeyboardInterrupt("cancel")

    def rendered(_raw, checked, *, preview_profile):
        assert checked == scope and preview_profile == "dpi288"
        state.rendered = True
        return owner

    target, name = {"verify": (workspace, "verify_inputs"), "binding": (runtime, "_binding"),
                    "source": (runtime, "_snapshot"), "recovery": (runtime, "_snapshot"),
                    "directory": (workspace, "_directory_generation")}[site]
    original = getattr(target, name)

    def changed(*args, **kwargs):
        value = original(*args, **kwargs)
        matches = site not in {"source", "recovery"} or args[0] == site
        if state.rendered and not state.injected and matches:
            state.injected = True
            if kind != "mismatch":
                raise primary
            if site == "verify":
                workspace.document = deepcopy(workspace.document)
            elif site in {"source", "recovery"}:
                return value[0], value[1], (9, 9, 9)
            else:
                return (-1, *value[1:])
        return value

    monkeypatch.setattr(runtime, "_render_pdf_with_view", rendered)
    monkeypatch.setattr(target, name, changed)
    expected = KeyboardInterrupt if kind == "interrupt" else runtime.CropPreviewError
    with pytest.raises(expected) as caught:
        runtime.render_crop_scope_with_view(workspace, scope, preview_profile="dpi288")
    assert state.rendered and state.injected and image.closes == 1
    owner.close()
    assert image.closes == 1
    if kind == "interrupt":
        assert caught.value is primary
    else:
        assert caught.value.code == ("input_changed" if kind == "mismatch" else "input_verification")


def test_public_metadata_success_transfers_same_open_owner(monkeypatch):
    workspace = inert_workspace(monkeypatch)
    scope, view = pure_view()
    image = OwnedImage()
    owner = runtime.CropRasterPreview(image, view)
    calls = []

    def rendered(raw, checked, *, preview_profile):
        calls.append((raw, checked, preview_profile))
        return owner

    monkeypatch.setattr(runtime, "_render_pdf_with_view", rendered)
    result = runtime.render_crop_scope_with_view(workspace, scope)
    assert result is owner and image.closes == 0 and len(calls) == 1
    assert calls[0] == (b"source", scope, "fit")
    result.close()
    assert image.closes == 1


def test_legacy_pil_render_never_imports_or_invokes_raster_view(small_native, monkeypatch):
    import builtins
    raw, scope = small_native
    original = builtins.__import__

    def checked(name, *args, **kwargs):
        if name == "ocr_crop_raster_view":
            pytest.fail("PIL compatibility path acquired metadata dependency")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", checked)
    monkeypatch.setattr(runtime, "_build_raster_view", lambda **_k: pytest.fail("legacy metadata validation"))
    image = runtime._render_pdf(raw, scope)
    try:
        assert image.mode == "RGB" and image.size == (144, 144)
    finally:
        image.close()


@pytest.mark.parametrize("fault", ["mode", "size", "size-bool", "size-huge", "byte-type", "byte-length", "rgb"])
@pytest.mark.parametrize("close_raises", [False, True])
def test_returned_pil_binding_refuses_wrong_mode_shape_or_rgb_and_closes_once(
        small_native, monkeypatch, fault, close_raises):
    from PIL import Image
    raw, scope = small_native
    image = OwnedImage(SystemExit("secondary close") if close_raises else None)

    def malformed(mode, size, pixels):
        inert_frombytes(image)(mode, size, pixels)
        if fault == "mode":
            image.mode = "L"
        elif fault == "size":
            image.size = (size[0] + 1, size[1])
        elif fault == "size-bool":
            image.size = (True, size[1])
        elif fault == "size-huge":
            image.size = (1 << 4096, size[1])
        elif fault == "byte-type":
            image.raw = bytearray(pixels)
        elif fault == "byte-length":
            image.raw = pixels[:-1]
        else:
            image.raw = bytes([pixels[0] ^ 1]) + pixels[1:]
        return image

    monkeypatch.setattr(Image, "frombytes", malformed)
    monkeypatch.setattr(runtime, "_build_raster_view", lambda **_k: pytest.fail("invalid PIL acquired view"))
    with pytest.raises(runtime.CropPreviewError) as caught:
        runtime._render_pdf_with_view(raw, scope)
    assert caught.value.code == "pdf_render" and image.closes == 1
    assert image.byte_reads == (0 if fault in {"mode", "size", "size-bool", "size-huge"} else 1)
