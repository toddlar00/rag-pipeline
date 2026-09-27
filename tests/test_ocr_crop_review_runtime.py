"""Generated original PDF crop previews; no OCR, models, server or private PDFs."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from types import SimpleNamespace

import pytest

import ocr_crop_comparison as comparison
import ocr_crop_review_runtime as runtime
from ocr_recovery import RetryPolicy, build_recovery_report
from ocr_review_runtime import ReviewWorkspace
from test_ocr_review import report


def _source(*, rotation=0, cropped=True, width=200, height=120, pages=1, annotations=False):
    pymupdf = pytest.importorskip("pymupdf")
    with pymupdf.open() as document:
        for _ in range(pages):
            page = document.new_page(width=width, height=height)
            assert (page.rect.width, page.rect.height) == (width, height)
            for bounds, color in (
                    ((0, 0, width / 2, height / 2), (1, 0, 0)),
                    ((width / 2, 0, width, height / 2), (0, 1, 0)),
                    ((0, height / 2, width / 2, height), (0, 0, 1)),
                    ((width / 2, height / 2, width, height), (1, 1, 0))):
                page.draw_rect(pymupdf.Rect(bounds), fill=color, color=None)
            if annotations:
                annotation = page.add_rect_annot(pymupdf.Rect(45, 30, 90, 60))
                annotation.set_colors(stroke=(0, 0, 0), fill=(0, 0, 0))
                annotation.update()
            if cropped:
                page.set_cropbox(pymupdf.Rect(20, 10, width - 20, height - 10))
            page.set_rotation(rotation)
        return document.tobytes()


def _workspace(tmp_path, **kwargs):
    source = tmp_path / "generated-original.pdf"
    source.write_bytes(_source(**kwargs))
    candidate = report()["pages"][0]["candidate"]

    class Reader:
        page_count = kwargs.get("pages", 1)

        def native_text(self, number):
            return ""

        def retry(self, number):
            return candidate

    recovery = build_recovery_report(Reader(), source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        policy=RetryPolicy(), requested_pages=tuple(range(1, Reader.page_count + 1)))
    recovery["evidence_sha256"] = None
    saved = tmp_path / "generated-recovery.json"
    saved.write_text(json.dumps(recovery), encoding="utf-8")
    return ReviewWorkspace(source, saved, tmp_path)


def _scope(workspace, *, bbox=(.1, .1, .4, .4), number=1):
    pymupdf = pytest.importorskip("pymupdf")
    with pymupdf.open(stream=workspace.pdf_path.read_bytes(), filetype="pdf") as document:
        page = document[number - 1]
        scope = {"source_sha256": workspace.document.source_sha256,
            "page_count": workspace.document.page_count, "page_number": number,
            "coordinate_system": "original_page_display_fraction", "bbox": list(bbox),
            "page_geometry": {"display_rect_points": list(page.rect), "cropbox_points": list(page.cropbox),
                              "rotation_degrees": page.rotation}}
    return _rehash(scope)


def _rehash(scope):
    scope.pop("scope_sha256", None)
    scope["scope_sha256"] = comparison._digest(scope)
    return scope


@pytest.fixture
def workspace(tmp_path):
    return _workspace(tmp_path)


@pytest.mark.parametrize("rotation,color", [(0, (255, 0, 0)), (90, (0, 0, 255)),
                                            (180, (255, 255, 0)), (270, (0, 255, 0))])
@pytest.mark.parametrize("cropped", [False, True])
def test_intrinsic_rotation_and_cropbox_select_original_display_quadrant(tmp_path, rotation, color, cropped):
    workspace = _workspace(tmp_path, rotation=rotation, cropped=cropped)
    scope = _scope(workspace)
    before = workspace.pdf_path.read_bytes(), workspace.recovery_path.read_bytes(), copy.deepcopy(scope)
    image = runtime.render_crop_scope(workspace, scope)
    assert image.mode == "RGB" and image.format is None
    assert image.getpixel((image.width // 2, image.height // 2)) == color
    assert image.width <= 1400 and image.height <= 1400 and image.width * image.height <= 1_960_000
    assert (workspace.pdf_path.read_bytes(), workspace.recovery_path.read_bytes(), scope) == before
    assert not image.info  # No source paths or PDF metadata are returned.


@pytest.mark.parametrize("bbox,color", [((.1, .1, .4, .4), (255, 0, 0)),
                                       ((.6, .1, .9, .4), (0, 255, 0)),
                                       ((.1, .6, .4, .9), (0, 0, 255)),
                                       ((.6, .6, .9, .9), (255, 255, 0))])
def test_requested_crop_not_whole_page_or_processed_candidate(workspace, monkeypatch, bbox, color):
    def forbidden(*args, **kwargs):
        pytest.fail("candidate/preprocessed renderer must not be used")

    monkeypatch.setattr(workspace, "render", forbidden)
    image = runtime.render_crop_scope(workspace, _scope(workspace, bbox=bbox))
    assert image.getpixel((image.width // 2, image.height // 2)) == color
    assert image.getpixel((2, 2)) == color


def test_large_original_page_preflights_small_preview_before_native_allocation(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path, cropped=False, width=14400, height=7200)
    pymupdf = pytest.importorskip("pymupdf")
    original = pymupdf.Page.get_pixmap
    seen = []

    def observed(page, **kwargs):
        bounds = (kwargs["clip"] * kwargs["matrix"]).irect
        assert 1 <= bounds.width <= 1400 and 1 <= bounds.height <= 1400
        assert bounds.width * bounds.height <= runtime.MAX_PREVIEW_PIXELS
        assert kwargs["alpha"] is False and kwargs["annots"] is True
        seen.append(bounds)
        return original(page, **kwargs)

    monkeypatch.setattr(pymupdf.Page, "get_pixmap", observed)
    image = runtime.render_crop_scope(workspace, _scope(workspace, bbox=(0., 0., 1., 1.)))
    assert len(seen) == 1 and image.size == (1398, 699)


def test_second_physical_page_supported(tmp_path):
    workspace = _workspace(tmp_path, pages=2, rotation=90)
    image = runtime.render_crop_scope(workspace, _scope(workspace, number=2))
    assert image.getpixel((10, 10)) == (0, 0, 255)


def test_each_render_opens_fresh_document_from_immutable_bytes(workspace, monkeypatch):
    pymupdf = pytest.importorskip("pymupdf")
    scope = _scope(workspace)
    original = pymupdf.open
    opened = []

    def observed(*args, **kwargs):
        assert not args and type(kwargs["stream"]) is bytes and kwargs["filetype"] == "pdf"
        document = original(*args, **kwargs)
        opened.append(document)
        return document

    monkeypatch.setattr(pymupdf, "open", observed)
    a = runtime.render_crop_scope(workspace, scope)
    b = runtime.render_crop_scope(workspace, scope)
    assert len(opened) == 2 and opened[0] is not opened[1]
    assert all(document.is_closed for document in opened)
    assert a is not b and a.tobytes() == b.tobytes()


@pytest.mark.parametrize("mutate", [
    lambda s: s.update(extra=True), lambda s: s.pop("bbox"),
    lambda s: s.update(scope_sha256="0" * 64), lambda s: s.update(source_sha256="0" * 64),
    lambda s: s.update(page_count=True), lambda s: s.update(page_count=5001),
    lambda s: s.update(page_number=True), lambda s: s.update(page_number=0),
    lambda s: s.update(page_number=2), lambda s: s.update(coordinate_system="processed_candidate"),
    lambda s: s.update(bbox=[0., 0., float("nan"), 1.]),
    lambda s: s.update(bbox=[0., 0., float("inf"), 1.]),
    lambda s: s.update(bbox=[0., 0., 10 ** 1000, 1.]),
    lambda s: s.update(bbox=[False, 0., 1., 1.]), lambda s: s.update(bbox=[0., 0., 2., 1.]),
    lambda s: s.update(bbox=[0., 0., 0., 1.]),
    lambda s: s["page_geometry"].update(rotation_degrees=True),
])
def test_malformed_scope_refuses_before_pdf_native_work(workspace, monkeypatch, mutate):
    scope = _scope(workspace)
    mutate(scope)
    monkeypatch.setattr(runtime, "_render_pdf", lambda *_a, **_k: pytest.fail("invalid scope entered native work"))
    with pytest.raises(runtime.CropPreviewError, match="original crop preview") as captured:
        runtime.render_crop_scope(workspace, scope)
    assert captured.value.code == "scope_validation"


@pytest.mark.parametrize("field,value", [("rotation_degrees", 90),
    ("display_rect_points", [0., 0., 161., 100.]), ("cropbox_points", [21., 10., 181., 110.])])
def test_rehashed_but_incorrect_actual_geometry_refuses_before_render(workspace, monkeypatch, field, value):
    pymupdf = pytest.importorskip("pymupdf")
    scope = _scope(workspace)
    scope["page_geometry"][field] = value
    _rehash(scope)
    monkeypatch.setattr(pymupdf.Page, "get_pixmap", lambda *_a, **_k: pytest.fail("geometry mismatch rendered"))
    with pytest.raises(runtime.CropPreviewError):
        runtime.render_crop_scope(workspace, scope)


@pytest.mark.parametrize("value", [None, SimpleNamespace(), object()])
def test_arbitrary_workspace_or_path_provider_not_accepted(value):
    with pytest.raises(runtime.CropPreviewError):
        runtime.render_crop_scope(value, {})


@pytest.mark.parametrize("field", ["pdf_path", "recovery_path"])
def test_changed_input_before_preview_refuses_before_render(workspace, monkeypatch, field):
    scope = _scope(workspace)
    getattr(workspace, field).write_bytes(b"changed private input")
    monkeypatch.setattr(runtime, "_render_pdf", lambda *_a, **_k: pytest.fail("changed input rendered"))
    with pytest.raises(runtime.CropPreviewError) as failure:
        runtime.render_crop_scope(workspace, scope)
    assert str(workspace.pdf_path) not in str(failure.value)
    assert failure.value.code == "input_changed"


@pytest.mark.parametrize("field", ["pdf_path", "recovery_path"])
@pytest.mark.parametrize("same_bytes", [False, True])
def test_input_generation_change_during_render_never_returns_image(workspace, monkeypatch, field, same_bytes):
    scope = _scope(workspace)
    original = runtime._render_pdf

    def changed(raw, current, *, preview_profile="fit"):
        image = original(raw, current)
        path = getattr(workspace, field)
        before = path.stat().st_ino
        replacement = path.with_name("replacement.bin")
        replacement.write_bytes(path.read_bytes() if same_bytes else b"changed")
        os.replace(replacement, path)
        assert path.stat().st_ino != before
        return image

    monkeypatch.setattr(runtime, "_render_pdf", changed)
    with pytest.raises(runtime.CropPreviewError):
        runtime.render_crop_scope(workspace, scope)


@pytest.mark.parametrize("limit_name,field", [("MAX_PDF_BYTES", "pdf_path"),
                                            ("MAX_REPORT_BYTES", "recovery_path")])
def test_oversized_input_refuses_before_read(workspace, monkeypatch, limit_name, field):
    scope = _scope(workspace)
    monkeypatch.setattr(runtime, limit_name, getattr(workspace, field).stat().st_size - 1)
    monkeypatch.setattr(runtime, "_render_pdf", lambda *_a, **_k: pytest.fail("oversized input rendered"))
    with pytest.raises(runtime.CropPreviewError):
        runtime.render_crop_scope(workspace, scope)


def test_hardlinked_source_refuses(workspace):
    scope = _scope(workspace)
    os.link(workspace.pdf_path, workspace.pdf_path.with_name("source-alias.pdf"))
    with pytest.raises(runtime.CropPreviewError):
        runtime.render_crop_scope(workspace, scope)


def test_preflight_pixel_cap_refuses_before_native_allocation(workspace, monkeypatch):
    pymupdf = pytest.importorskip("pymupdf")
    scope = _scope(workspace)
    monkeypatch.setattr(runtime, "MAX_PREVIEW_PIXELS", 1)
    monkeypatch.setattr(pymupdf.Page, "get_pixmap", lambda *_a, **_k: pytest.fail("overbudget native allocation"))
    with pytest.raises(runtime.CropPreviewError) as captured:
        runtime.render_crop_scope(workspace, scope)
    assert captured.value.code == "raster_limit"


def test_unrepresentably_thin_scope_refuses_before_native_allocation(workspace, monkeypatch):
    pymupdf = pytest.importorskip("pymupdf")
    scope = _scope(workspace, bbox=(0., 0., 1e-310, 1.))
    monkeypatch.setattr(pymupdf.Page, "get_pixmap", lambda *_a, **_k: pytest.fail("zero raster allocated"))
    with pytest.raises(runtime.CropPreviewError):
        runtime.render_crop_scope(workspace, scope)


@pytest.mark.parametrize("field,value,code", [("width", 1401, "raster_limit"),
    ("height", 0, "raster_limit"), ("n", 4, "pdf_render"), ("stride", 1, "pdf_render"),
    ("x", -99, "geometry_mismatch"), ("y", -99, "geometry_mismatch")])
def test_actual_pixmap_metadata_checked_before_copy(workspace, monkeypatch, field, value, code):
    pymupdf = pytest.importorskip("pymupdf")
    scope = _scope(workspace)

    def bad_map(_page, **kwargs):
        pixels = (kwargs["clip"] * kwargs["matrix"]).irect

        class Map:
            width, height, n = pixels.width, pixels.height, 3
            stride, x, y = pixels.width * 3, pixels.x0, pixels.y0

            @property
            def samples_mv(self):
                pytest.fail("invalid pixmap samples accessed")

        value_map = Map()
        setattr(value_map, field, value)
        return value_map

    monkeypatch.setattr(pymupdf.Page, "get_pixmap", bad_map)
    with pytest.raises(runtime.CropPreviewError) as captured:
        runtime.render_crop_scope(workspace, scope)
    assert captured.value.code == code


def test_actual_sample_size_checked_before_image_copy(workspace, monkeypatch):
    pymupdf = pytest.importorskip("pymupdf")
    scope = _scope(workspace)

    def bad_map(_page, **kwargs):
        p = (kwargs["clip"] * kwargs["matrix"]).irect
        return SimpleNamespace(width=p.width, height=p.height, x=p.x0, y=p.y0, n=3,
                               stride=p.width * 3, samples_mv=memoryview(b""))

    monkeypatch.setattr(pymupdf.Page, "get_pixmap", bad_map)
    with pytest.raises(runtime.CropPreviewError) as captured:
        runtime.render_crop_scope(workspace, scope)
    assert captured.value.code == "pdf_render"


@pytest.mark.parametrize("failure", [KeyboardInterrupt, SystemExit])
def test_cancellation_propagates_and_document_closes(workspace, monkeypatch, failure):
    pymupdf = pytest.importorskip("pymupdf")
    scope = _scope(workspace)
    original = pymupdf.open
    opened = []
    error = failure("synthetic cancellation")

    def observed(*args, **kwargs):
        document = original(*args, **kwargs)
        opened.append(document)
        return document

    def cancel(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(pymupdf, "open", observed)
    monkeypatch.setattr(pymupdf.Page, "get_pixmap", cancel)
    with pytest.raises(failure) as captured:
        runtime.render_crop_scope(workspace, scope)
    assert captured.value is error and opened[0].is_closed


def test_normal_native_failure_is_static_and_closes_document(workspace, monkeypatch):
    pymupdf = pytest.importorskip("pymupdf")
    scope = _scope(workspace)
    original = pymupdf.open
    opened = []

    def observed(*args, **kwargs):
        document = original(*args, **kwargs)
        opened.append(document)
        return document

    def fail(*_args, **_kwargs):
        raise RuntimeError("synthetic private PDF text and path must not escape")

    monkeypatch.setattr(pymupdf, "open", observed)
    monkeypatch.setattr(pymupdf.Page, "get_pixmap", fail)
    with pytest.raises(runtime.CropPreviewError) as captured:
        runtime.render_crop_scope(workspace, scope)
    assert "private PDF" not in str(captured.value)
    assert captured.value.code == "pdf_render"
    assert opened[0].is_closed


def test_workspace_document_replacement_during_render_refuses(workspace, monkeypatch):
    scope = _scope(workspace)
    original = runtime._render_pdf

    def changed(raw, current, *, preview_profile="fit"):
        image = original(raw, current)
        workspace.document = copy.deepcopy(workspace.document)
        return image

    monkeypatch.setattr(runtime, "_render_pdf", changed)
    with pytest.raises(runtime.CropPreviewError):
        runtime.render_crop_scope(workspace, scope)


def test_actual_page_count_mismatch_refuses_before_raster(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path, pages=2)
    value = report()
    value["source_sha256"] = workspace.document.source_sha256
    workspace.recovery_path.write_text(json.dumps(value), encoding="utf-8")
    workspace = ReviewWorkspace(workspace.pdf_path, workspace.recovery_path, tmp_path)
    scope = _scope(workspace)
    pymupdf = pytest.importorskip("pymupdf")
    monkeypatch.setattr(pymupdf.Page, "get_pixmap", lambda *_a, **_k: pytest.fail("wrong page count rasterized"))
    with pytest.raises(runtime.CropPreviewError) as captured:
        runtime.render_crop_scope(workspace, scope)
    assert captured.value.code == "geometry_mismatch"


@pytest.mark.parametrize("field", ["pdf_path", "recovery_path"])
def test_replacement_during_initial_opened_snapshot_refuses(workspace, monkeypatch, field):
    scope = _scope(workspace)
    original = runtime.artifact_io._read_index_artifact_snapshot
    target = getattr(workspace, field)

    def replaced(path, **kwargs):
        if path == target:
            replacement = path.with_name("opened-replacement.bin")
            replacement.write_bytes(path.read_bytes())
            os.replace(replacement, path)
        return original(path, **kwargs)

    monkeypatch.setattr(runtime.artifact_io, "_read_index_artifact_snapshot", replaced)
    monkeypatch.setattr(runtime, "_render_pdf", lambda *_a, **_k: pytest.fail("replaced source generation rendered"))
    with pytest.raises(runtime.CropPreviewError) as captured:
        runtime.render_crop_scope(workspace, scope)
    assert captured.value.code == "input_changed"


def test_output_directory_generation_recheck_is_preserved(workspace, monkeypatch):
    scope = _scope(workspace)
    original = runtime._render_pdf

    def changed(raw, current, *, preview_profile="fit"):
        image = original(raw, current)
        monkeypatch.setattr(workspace, "_directory_generation", lambda: (-1, -1))
        return image

    monkeypatch.setattr(runtime, "_render_pdf", changed)
    with pytest.raises(runtime.CropPreviewError):
        runtime.render_crop_scope(workspace, scope)


def test_scope_detached_before_render(workspace, monkeypatch):
    scope = _scope(workspace)
    original = runtime._render_pdf

    def changed(raw, current, *, preview_profile="fit"):
        scope["bbox"][:] = [.6, .6, .9, .9]
        assert current["bbox"] == [.1, .1, .4, .4]
        return original(raw, current)

    monkeypatch.setattr(runtime, "_render_pdf", changed)
    image = runtime.render_crop_scope(workspace, scope)
    assert image.getpixel((10, 10)) == (255, 0, 0)


def test_final_check_cancellation_not_converted_to_preview_error(workspace, monkeypatch):
    scope = _scope(workspace)
    original = workspace.verify_inputs
    count = 0
    error = KeyboardInterrupt("cancel after raster")

    def checked():
        nonlocal count
        count += 1
        if count == 2:
            raise error
        original()

    monkeypatch.setattr(workspace, "verify_inputs", checked)
    with pytest.raises(KeyboardInterrupt) as captured:
        runtime.render_crop_scope(workspace, scope)
    assert captured.value is error


def test_directory_change_during_final_snapshot_refuses(workspace, monkeypatch):
    scope = _scope(workspace)
    original = runtime._snapshot
    count = 0

    def changed(path, limit):
        nonlocal count
        result = original(path, limit)
        count += 1
        if count == 4:
            monkeypatch.setattr(workspace, "_directory_generation", lambda: (-1, -1))
        return result

    monkeypatch.setattr(runtime, "_snapshot", changed)
    with pytest.raises(runtime.CropPreviewError) as captured:
        runtime.render_crop_scope(workspace, scope)
    assert count == 4
    assert captured.value.code == "input_changed"


@pytest.mark.parametrize("code", sorted(runtime.CROP_PREVIEW_ERROR_CODES))
def test_failure_codes_keep_exact_static_exception_text(code):
    failure = runtime.CropPreviewError(code=code)
    assert isinstance(failure, ValueError)
    assert failure.code == code
    assert failure.args == ("original crop preview unavailable or inputs changed",)
    assert str(failure) == "original crop preview unavailable or inputs changed"
    with pytest.raises(AttributeError):
        failure.code = "private replacement"


@pytest.mark.parametrize("code", [None, True, [], {}, object(), "private path and PDF text", "x" * 100_000],
                         ids=["null", "bool", "list", "dict", "object", "unknown", "oversized"])
def test_unknown_constructor_code_is_bounded_static_fallback(code):
    failure = runtime.CropPreviewError(code=code)
    assert failure.code == "preview_unavailable"
    assert failure.args == ("original crop preview unavailable or inputs changed",)
    assert vars(failure) == {"_code": "preview_unavailable"}


def test_error_code_allowlist_is_fixed_and_default_is_unknown():
    assert runtime.CROP_PREVIEW_ERROR_CODES == frozenset({
        "scope_validation", "input_verification", "input_changed", "pdf_render",
        "geometry_mismatch", "raster_limit", "preview_unavailable",
        "preview_timeout", "preview_cancelled", "preview_busy", "cleanup_unconfirmed",
    })
    assert runtime.CropPreviewError().code == "preview_unavailable"


def test_unknown_code_does_not_invoke_external_string_hash_or_equality():
    class Hostile:
        def __str__(self):
            pytest.fail("external code must not be stringified")

        def __hash__(self):
            pytest.fail("external code must not be hashed")

        def __eq__(self, _other):
            pytest.fail("external code must not be compared")

    class StringSubclass(str):
        def __hash__(self):
            pytest.fail("code string subclass must not be hashed")

    assert runtime.CropPreviewError(code=Hostile()).code == "preview_unavailable"
    assert runtime.CropPreviewError(code=StringSubclass("pdf_render")).code == "preview_unavailable"


@pytest.fixture
def inert_preview(monkeypatch):
    """No PDF authoring/import: isolate ordinary error and cancellation routing."""
    workspace = object.__new__(ReviewWorkspace)
    workspace.document = SimpleNamespace(source_sha256="a" * 64, recovery_sha256="b" * 64, page_count=1)
    workspace.pdf_path, workspace.recovery_path, workspace.output_dir = "source", "recovery", "output"
    monkeypatch.setattr(workspace, "_directory_generation", lambda: (1, 2))
    monkeypatch.setattr(workspace, "verify_inputs", lambda: None)
    monkeypatch.setattr(runtime, "validate_crop_scope", lambda scope, **_kwargs: scope)
    monkeypatch.setattr(runtime, "_snapshot", lambda path, _limit:
        (b"source" if path == "source" else b"recovery", ("a" if path == "source" else "b") * 64, (1, 2, 3)))
    monkeypatch.setattr(runtime, "_render_pdf", lambda *_args, **_kwargs: object())
    return workspace


@pytest.mark.parametrize("site,code", [("validate_crop_scope", "scope_validation"),
    ("_snapshot", "input_verification"), ("verify_inputs", "input_verification"),
    ("_render_pdf", "pdf_render")])
@pytest.mark.parametrize("late", [False, True])
def test_ordinary_failures_report_observed_stage_without_private_details(inert_preview, monkeypatch, site, code, late):
    target = inert_preview if site == "verify_inputs" else runtime
    original = getattr(target, site)
    calls = 0
    threshold = 3 if site == "_snapshot" and late else 2 if site == "verify_inputs" and late else 1

    def fail(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == threshold:
            raise OSError("private filename and PDF text: do not publish")
        return original(*args, **kwargs)

    monkeypatch.setattr(target, site, fail)
    with pytest.raises(runtime.CropPreviewError) as captured:
        runtime.render_crop_scope(inert_preview, {})
    assert captured.value.code == code
    assert captured.value.args == ("original crop preview unavailable or inputs changed",)
    assert captured.value.__cause__ is None and captured.value.__suppress_context__
    assert calls == threshold  # No retry or extra render after refusal.


@pytest.mark.parametrize("site", ["validate_crop_scope", "_snapshot", "verify_inputs", "_render_pdf"])
@pytest.mark.parametrize("exception", [KeyboardInterrupt, SystemExit])
def test_stage_codes_do_not_convert_or_repeat_cancellation(inert_preview, monkeypatch, site, exception):
    error = exception("cancel this exact operation")
    calls = []

    def fail(*_args, **_kwargs):
        calls.append(site)
        raise error

    monkeypatch.setattr(inert_preview if site == "verify_inputs" else runtime, site, fail)
    with pytest.raises(exception) as captured:
        runtime.render_crop_scope(inert_preview, {})
    assert captured.value is error and calls == [site]


def test_fallback_crop_error_remains_unknown_not_guessed_from_outer_stage(inert_preview, monkeypatch):
    def fail(*_args, **_kwargs):
        raise runtime.CropPreviewError(code="private unsupported stage")

    monkeypatch.setattr(runtime, "_render_pdf", fail)
    with pytest.raises(runtime.CropPreviewError) as captured:
        runtime.render_crop_scope(inert_preview, {})
    assert captured.value.code == "preview_unavailable"


def test_actual_pdf_decode_failure_has_render_stage(workspace, monkeypatch):
    pymupdf = pytest.importorskip("pymupdf")
    scope = _scope(workspace)

    def fail(**_kwargs):
        raise ValueError("private PDF decoder detail")

    monkeypatch.setattr(pymupdf, "open", fail)
    with pytest.raises(runtime.CropPreviewError) as captured:
        runtime.render_crop_scope(workspace, scope)
    assert captured.value.code == "pdf_render"


def test_valid_declared_scope_with_different_actual_cropbox_is_geometry_mismatch(workspace, monkeypatch):
    pymupdf = pytest.importorskip("pymupdf")
    scope = _scope(workspace)
    scope["page_geometry"]["cropbox_points"] = [21., 10., 181., 110.]
    _rehash(scope)
    comparison.validate_crop_scope(scope, source_sha256=workspace.document.source_sha256, page_count=1)
    monkeypatch.setattr(pymupdf.Page, "get_pixmap", lambda *_a, **_kw: pytest.fail("mismatched geometry rendered"))
    with pytest.raises(runtime.CropPreviewError) as captured:
        runtime.render_crop_scope(workspace, scope)
    assert captured.value.code == "geometry_mismatch"


@pytest.mark.parametrize("profile", ["fit", "dpi288", "dpi576"])
def test_preview_profile_accepts_only_closed_exact_strings(profile):
    assert runtime.validate_preview_profile(profile) == profile


@pytest.mark.parametrize("value", [None, True, 288, 4., [], {}, "", "Fit", "dpi144", "dpi288 ",
                                    "dpi576/../../private", "x" * 100_000],
                         ids=["none", "bool", "int", "float", "list", "dict", "empty", "case",
                              "unknown", "space", "path", "oversized"])
def test_invalid_profile_refuses_before_workspace_or_native_access(monkeypatch, value):
    def forbidden(*_args, **_kwargs):
        pytest.fail("invalid profile reached IO or rendering")
    monkeypatch.setattr(runtime, "_snapshot", forbidden)
    monkeypatch.setattr(runtime, "_render_pdf", forbidden)
    with pytest.raises(runtime.CropPreviewError) as caught:
        runtime.render_crop_scope(object(), {}, preview_profile=value)
    assert caught.value.code == "scope_validation"


def test_profile_subclass_and_hostile_object_are_not_coerced_or_hashed():
    class Hostile:
        def __str__(self):
            pytest.fail("profile string coercion")
        def __hash__(self):
            pytest.fail("profile hash")
        def __eq__(self, _other):
            pytest.fail("profile equality")
    class StringSubclass(str):
        __hash__ = Hostile.__hash__
        __eq__ = Hostile.__eq__
    for value in (Hostile(), StringSubclass("fit")):
        with pytest.raises(runtime.CropPreviewError) as caught:
            runtime.validate_preview_profile(value)
        assert caught.value.code == "scope_validation"


def test_private_renderer_validates_profile_before_optional_native_import(monkeypatch):
    import builtins
    original = builtins.__import__
    def checked(name, *args, **kwargs):
        if name.split(".")[0] in {"pymupdf", "PIL"}:
            pytest.fail("invalid private-render profile imported native dependencies")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", checked)
    with pytest.raises(runtime.CropPreviewError) as caught:
        runtime._render_pdf(b"not a PDF", {}, preview_profile="unknown")
    assert caught.value.code == "scope_validation"


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("cropped", [False, True])
@pytest.mark.parametrize("profile,scale", [("fit", 2.), ("dpi288", 4.), ("dpi576", 8.)])
def test_profile_matches_independent_original_pdf_raster_geometry(tmp_path, rotation, cropped, profile, scale):
    """Actual generated PDF raster comparison, not OCR or visual-browser proof."""
    pymupdf = pytest.importorskip("pymupdf")
    workspace = _workspace(tmp_path, rotation=rotation, cropped=cropped, annotations=True)
    scope = _scope(workspace, bbox=(.073, .121, .813, .857))
    before = workspace.pdf_path.read_bytes(), workspace.recovery_path.read_bytes(), copy.deepcopy(scope)
    with pymupdf.open(stream=before[0], filetype="pdf") as document:
        page = document[0]
        rect = page.rect
        left, top, right, bottom = scope["bbox"]
        clip = pymupdf.Rect(rect.x0 + left * rect.width, rect.y0 + top * rect.height,
                            rect.x0 + right * rect.width, rect.y0 + bottom * rect.height)
        expected = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), clip=clip,
                                  colorspace=pymupdf.csRGB, alpha=False, annots=True)
        unannotated = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), clip=clip,
                                     colorspace=pymupdf.csRGB, alpha=False, annots=False)
        assert expected.samples != unannotated.samples
        image = runtime.render_crop_scope(workspace, scope, preview_profile=profile)
        assert image.mode == "RGB" and image.size == (expected.width, expected.height)
        assert image.tobytes() == expected.samples and not image.info
        if profile == "fit":
            implicit = runtime.render_crop_scope(workspace, scope)
            assert implicit.size == image.size and implicit.tobytes() == image.tobytes()
    assert (workspace.pdf_path.read_bytes(), workspace.recovery_path.read_bytes(), scope) == before


@pytest.mark.parametrize("profile,pixels", [("fit", 144), ("dpi288", 288), ("dpi576", 576)])
def test_72_point_source_crop_uses_requested_exact_resolution(tmp_path, monkeypatch, profile, pixels):
    pymupdf = pytest.importorskip("pymupdf")
    workspace = _workspace(tmp_path, cropped=False, width=72, height=72)
    scope = _scope(workspace, bbox=(0., 0., 1., 1.))
    original = pymupdf.Page.get_pixmap
    calls = []
    def observed(page, **kwargs):
        calls.append(kwargs)
        return original(page, **kwargs)
    monkeypatch.setattr(pymupdf.Page, "get_pixmap", observed)
    image = runtime.render_crop_scope(workspace, scope, preview_profile=profile)
    assert image.size == (pixels, pixels) and len(calls) == 1
    assert tuple(calls[0]["matrix"]) == (pixels / 72, 0., 0., pixels / 72, 0., 0.)
    assert calls[0]["alpha"] is False and calls[0]["annots"] is True


@pytest.mark.parametrize("profile,scale", [("dpi288", 4), ("dpi576", 8)])
def test_detail_uses_projected_irect_cap_without_downscaling_or_native_attempt(tmp_path, monkeypatch, profile, scale):
    pymupdf = pytest.importorskip("pymupdf")
    workspace = _workspace(tmp_path, cropped=False, width=400, height=40)
    # The nominal width fits; outward rounding of the nonzero origin adds the
    # disallowed pixel. A nominal-width-only check would wrongly admit this.
    scope = _scope(workspace, bbox=(.1 / 400, .1, (1400 / scale + .05) / 400, .9))
    clip = pymupdf.Rect(.1, 4., 1400 / scale + .05, 36.)
    assert clip.width * scale < 1400
    assert (clip * pymupdf.Matrix(scale, scale)).irect.width == 1401
    monkeypatch.setattr(pymupdf.Page, "get_pixmap", lambda *_a, **_k: pytest.fail("overlimit native allocation"))
    with pytest.raises(runtime.CropPreviewError) as caught:
        runtime.render_crop_scope(workspace, scope, preview_profile=profile)
    assert caught.value.code == "raster_limit"


@pytest.mark.parametrize("profile,scale", [("dpi288", 4), ("dpi576", 8)])
def test_detail_exact_1400_boundary_is_admitted(tmp_path, profile, scale):
    workspace = _workspace(tmp_path, cropped=False, width=1400 / scale, height=20)
    image = runtime.render_crop_scope(workspace, _scope(workspace, bbox=(0., 0., 1., 1.)),
                                      preview_profile=profile)
    assert image.size == (1400, 20 * scale)


@pytest.mark.parametrize("profile", ["dpi288", "dpi576"])
def test_detail_pixel_budget_preflight_has_no_native_attempt(workspace, monkeypatch, profile):
    pymupdf = pytest.importorskip("pymupdf")
    scope = _scope(workspace)
    monkeypatch.setattr(runtime, "MAX_PREVIEW_PIXELS", 1)
    monkeypatch.setattr(pymupdf.Page, "get_pixmap", lambda *_a, **_k: pytest.fail("overbudget native allocation"))
    with pytest.raises(runtime.CropPreviewError) as caught:
        runtime.render_crop_scope(workspace, scope, preview_profile=profile)
    assert caught.value.code == "raster_limit"


class _OwnedPreview:
    """Inert owned image; explicitly count disposal rather than relying on GC."""

    def __init__(self, close_failure=None):
        self.close_count = 0
        self.close_failure = close_failure

    def close(self):
        self.close_count += 1
        if self.close_failure is not None:
            raise self.close_failure

    def tobytes(self):
        assert self.close_count == 0
        return b"RGB"


def _late_preview_check(monkeypatch, workspace, image, site, action):
    """Inject only after the trusted renderer transfers its single image."""
    target, name, ordinal = {
        "verify": (workspace, "verify_inputs", 1),
        "binding": (runtime, "_binding", 1),
        "source": (runtime, "_snapshot", 1),
        "recovery": (runtime, "_snapshot", 2),
        "final_binding": (runtime, "_binding", 2),
        "directory": (workspace, "_directory_generation", 1),
    }[site]
    original = getattr(target, name)
    observed = SimpleNamespace(renders=0, late_checks=0, injected=0)

    def render(*_args, **_kwargs):
        observed.renders += 1
        return image

    def checked(*args, **kwargs):
        value = original(*args, **kwargs)
        if observed.renders:
            observed.late_checks += 1
            if observed.late_checks == ordinal:
                observed.injected += 1
                return action(value)
        return value

    monkeypatch.setattr(runtime, "_render_pdf", render)
    monkeypatch.setattr(target, name, checked)
    return observed


@pytest.mark.parametrize("site", ["verify", "binding", "source", "recovery", "final_binding", "directory"])
@pytest.mark.parametrize("failure_kind", ["typed", "ordinary", "interrupt", "exit"])
def test_late_preview_failure_disposes_once_and_preserves_routing(inert_preview, monkeypatch, site, failure_kind):
    image = _OwnedPreview()
    failure = {"typed": runtime.CropPreviewError(code="geometry_mismatch"),
               "ordinary": OSError("PRIVATE late read"),
               "interrupt": KeyboardInterrupt("cancel exact render"),
               "exit": SystemExit("exit exact render")}[failure_kind]

    def fail(_value):
        raise failure

    observed = _late_preview_check(monkeypatch, inert_preview, image, site, fail)
    expected = runtime.CropPreviewError if isinstance(failure, Exception) else type(failure)
    with pytest.raises(expected) as caught:
        runtime.render_crop_scope(inert_preview, {})
    assert image.close_count == 1 and observed.renders == observed.injected == 1
    if isinstance(failure, Exception):
        assert caught.value.code == ("geometry_mismatch" if failure_kind == "typed" else "input_verification")
        assert caught.value.__cause__ is None and caught.value.__suppress_context__
    else:
        assert caught.value is failure


@pytest.mark.parametrize("site", ["binding", "source", "recovery", "final_binding", "directory"])
def test_observed_late_generation_mismatch_disposes_once(inert_preview, monkeypatch, site):
    image = _OwnedPreview()

    def changed(value):
        if site in {"source", "recovery"}:
            return value[0], value[1], (-1, -1, -1)
        return (-1, *value[1:])

    observed = _late_preview_check(monkeypatch, inert_preview, image, site, changed)
    with pytest.raises(runtime.CropPreviewError) as caught:
        runtime.render_crop_scope(inert_preview, {})
    assert caught.value.code == "input_changed"
    assert image.close_count == 1 and observed.renders == observed.injected == 1


@pytest.mark.parametrize("failure_type", [OSError, KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("close_type", [RuntimeError, KeyboardInterrupt, SystemExit])
def test_disposal_failure_cannot_replace_late_preview_failure(inert_preview, monkeypatch, failure_type, close_type):
    image = _OwnedPreview(close_type("secondary disposal failure"))
    failure = failure_type("primary late failure")

    def fail(_value):
        raise failure

    observed = _late_preview_check(monkeypatch, inert_preview, image, "verify", fail)
    expected = runtime.CropPreviewError if failure_type is OSError else failure_type
    with pytest.raises(expected) as caught:
        runtime.render_crop_scope(inert_preview, {})
    assert image.close_count == 1 and observed.renders == 1
    if failure_type is OSError:
        assert caught.value.code == "input_verification"
    else:
        assert caught.value is failure


@pytest.mark.parametrize("profile", runtime.PREVIEW_PROFILES)
def test_successful_preview_transfers_usable_image_without_closing(inert_preview, monkeypatch, profile):
    image = _OwnedPreview()
    observed = _late_preview_check(monkeypatch, inert_preview, image, "directory", lambda value: value)
    result = runtime.render_crop_scope(inert_preview, {}, preview_profile=profile)
    assert result is image and image.tobytes() == b"RGB" and image.close_count == 0
    assert observed.renders == 1
    result.close()
    assert image.close_count == 1


@pytest.mark.parametrize("failure_type", [OSError, KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("close_raises", [False, True])
def test_pdf_context_exit_failure_disposes_untransferred_image(workspace, monkeypatch, failure_type, close_raises):
    """Actual generated raster plus an injected document-exit failure."""
    pymupdf = pytest.importorskip("pymupdf")
    image_module = pytest.importorskip("PIL.Image")
    scope = _scope(workspace)
    original_open = pymupdf.open
    opened = []
    image = _OwnedPreview(SystemExit("secondary close") if close_raises else None)
    failure = failure_type("primary document exit")

    class FailingExit:
        def __init__(self, *args, **kwargs):
            self.document = original_open(*args, **kwargs)
            opened.append(self.document)

        def __enter__(self):
            return self.document.__enter__()

        def __exit__(self, *args):
            self.document.__exit__(*args)
            raise failure

    monkeypatch.setattr(pymupdf, "open", FailingExit)
    monkeypatch.setattr(image_module, "frombytes", lambda *_a, **_k: image)
    expected = runtime.CropPreviewError if failure_type is OSError else failure_type
    with pytest.raises(expected) as caught:
        runtime.render_crop_scope(workspace, scope)
    assert len(opened) == 1 and opened[0].is_closed
    assert image.close_count == 1  # The outer renderer never acquired ownership.
    if failure_type is OSError:
        assert caught.value.code == "pdf_render"
    else:
        assert caught.value is failure
