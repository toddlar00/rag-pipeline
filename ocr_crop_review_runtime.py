"""Bounded previews of a declared original-source crop, never OCR candidates.

The nominal scope is shared with the pure crop comparison adapter. Rendering
uses fresh immutable source bytes, retains intrinsic PDF rotation/cropbox, and
does not replay OCR preprocessing or change any report. Raster limits bound the
returned preview allocation, not all memory used by a PDF decoder.
"""

from __future__ import annotations

import hashlib
import json
import math
import stat
import threading

import artifact_io
from ocr_crop_comparison import _bytes, validate_crop_scope
from ocr_review_runtime import MAX_PDF_BYTES, MAX_REPORT_BYTES, ReviewWorkspace
import storage_policy


MAX_PREVIEW_SIDE = 1400
MAX_PREVIEW_PIXELS = MAX_PREVIEW_SIDE * MAX_PREVIEW_SIDE
PREVIEW_PROFILES = ("fit", "dpi288", "dpi576")
CROP_PREVIEW_ERROR_CODES = frozenset({
    "scope_validation", "input_verification", "input_changed", "pdf_render",
    "geometry_mismatch", "raster_limit", "preview_unavailable",
    "preview_timeout", "preview_cancelled", "preview_busy", "cleanup_unconfirmed",
})


class CropPreviewError(ValueError):
    """Static failure text plus a bounded, non-sensitive failure-stage code.

    ``scope_validation`` is a rejected declaration; ``input_verification`` is
    a failed input/access check without a known cause. ``input_changed`` needs
    an observed identity/digest/binding mismatch. ``pdf_render`` covers PDF
    decoding/rendering or invalid RGB output; ``geometry_mismatch`` covers
    observed page/raster geometry disagreement. ``raster_limit`` is the fixed
    preview dimension/pixel guard, not total decoder-memory isolation.
    ``preview_unavailable`` is the unknown-stage fallback. None of these codes
    explains an earlier failure or attests to source/reviewer authenticity.
    This error also carries fixed-worker lifecycle codes when called through
    the separate supervised preview controller. The public renderer below
    remains in-process and does not itself provide a decoder deadline.
    """

    def __init__(self, *, code="preview_unavailable"):
        self._code = (code if type(code) is str and len(code) <= 32 and code in CROP_PREVIEW_ERROR_CODES
                      else "preview_unavailable")
        super().__init__("original crop preview unavailable or inputs changed")

    @property
    def code(self):
        return self._code


class CropRasterPreview:
    """Own one PIL plus immutable, already-validated same-render metadata.

    Trusted render/protocol callers validate the declaration before constructing
    this owner and retain their image guard if construction fails. This object
    grants no render or annotation authority by itself. Metadata reads detach;
    closing makes at most one underlying close attempt, including on failure.
    """

    __slots__ = ("_image", "_raster_view_bytes", "_close_lock", "_closed")

    def __init__(self, image, raster_view):
        from ocr_crop_raster_view import MAX_RASTER_VIEW_BYTES

        raw = _bytes(raster_view, MAX_RASTER_VIEW_BYTES)
        self._close_lock = threading.Lock()
        self._closed = False
        self._raster_view_bytes = raw
        self._image = image

    @property
    def image(self):
        return self._image

    @property
    def raster_view_bytes(self):
        return self._raster_view_bytes

    @property
    def raster_view(self):
        return json.loads(self._raster_view_bytes)

    def close(self):
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
        self._image.close()


def _fail(code):
    raise CropPreviewError(code=code)


def validate_preview_profile(value):
    """Admit only fixed display profiles, never an arbitrary scale or OCR recipe."""
    if type(value) is not str or value not in PREVIEW_PROFILES:
        _fail("scope_validation")
    return value


def _snapshot(path, limit):
    """Bounded opened-file bytes plus link-aware path generation checks."""
    storage_policy.assert_no_link_components(path)
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or not 0 < before.st_size <= limit:
        _fail("input_verification")
    raw, digest, fingerprint = artifact_io._read_index_artifact_snapshot(path, max_bytes=limit)
    storage_policy.assert_no_link_components(path)
    after = path.lstat()
    if (not stat.S_ISREG(after.st_mode) or after.st_nlink != 1
            or artifact_io._artifact_content_identity(before) != fingerprint[:4]
            or artifact_io._artifact_content_identity(after) != fingerprint[:4]):
        _fail("input_changed")
    if not 0 < len(raw) <= limit:
        _fail("input_verification")
    return raw, digest, fingerprint


def _binding(workspace):
    return (id(workspace.document), workspace.pdf_path, workspace.recovery_path,
            workspace.output_dir, workspace.document.source_sha256,
            workspace.document.recovery_sha256, workspace.document.page_count)


def _dimensions(width, height):
    if (type(width) is not int or type(height) is not int
            or not 1 <= width <= MAX_PREVIEW_SIDE or not 1 <= height <= MAX_PREVIEW_SIDE
            or width * height > MAX_PREVIEW_PIXELS):
        _fail("raster_limit")


def _render_pdf(raw, scope, *, preview_profile="fit"):
    return _render_pdf_core(raw, scope, preview_profile=preview_profile, with_view=False)


def _render_pdf_with_view(raw, scope, *, preview_profile="fit"):
    return _render_pdf_core(raw, scope, preview_profile=preview_profile, with_view=True)


def _build_raster_view(**kwargs):
    # The legacy PIL/protocol-2 route must not acquire a new behavior dependency.
    from ocr_crop_raster_view import build_raster_view

    return build_raster_view(**kwargs)


def _render_pdf_core(raw, scope, *, preview_profile, with_view):
    preview_profile = validate_preview_profile(preview_profile)
    import pymupdf
    from PIL import Image

    image = None
    result = None
    stage = "pdf_render"
    try:
        with pymupdf.open(stream=raw, filetype="pdf") as document:
            if not document.is_pdf or document.needs_pass:
                _fail("pdf_render")
            if len(document) != scope["page_count"]:
                _fail("geometry_mismatch")
            page = document[scope["page_number"] - 1]
            stage = "geometry_mismatch"
            rect = page.rect
            actual = {"display_rect_points": [float(value) for value in rect],
                      "cropbox_points": [float(value) for value in page.cropbox],
                      "rotation_degrees": page.rotation}
            if actual != scope["page_geometry"] or not all(math.isfinite(value) for value in rect):
                _fail("geometry_mismatch")
            if not math.isfinite(rect.width) or not math.isfinite(rect.height) or min(rect.width, rect.height) <= 0:
                _fail("geometry_mismatch")
            left, top, right, bottom = scope["bbox"]
            clip = pymupdf.Rect(rect.x0 + left * rect.width, rect.y0 + top * rect.height,
                                rect.x0 + right * rect.width, rect.y0 + bottom * rect.height)
            if (not all(math.isfinite(value) for value in clip)
                    or not math.isfinite(clip.width) or not math.isfinite(clip.height)
                    or min(clip.width, clip.height) <= 0):
                _fail("geometry_mismatch")
            # Fit retains its exact historical scale/rounding. Detail is a fresh
            # source render at the selected density, not a resized Fit bitmap.
            # Every profile is checked using outward-rounded raster extents before
            # get_pixmap; an oversized detail refuses, with no silent Fit fallback.
            scale = (min(2., (MAX_PREVIEW_SIDE - 2) / max(clip.width, clip.height))
                     if preview_profile == "fit" else 4. if preview_profile == "dpi288" else 8.)
            if not math.isfinite(scale) or scale <= 0:
                _fail("raster_limit")
            matrix = pymupdf.Matrix(scale, scale)
            projected = clip * matrix
            if not all(math.isfinite(value) for value in projected):
                _fail("geometry_mismatch")
            pixels = projected.irect
            _dimensions(pixels.width, pixels.height)
            if with_view:
                # Capture the actual command and native parameters, not a later
                # reconstruction from image dimensions or mutable PIL.info.
                native_rect = pymupdf.JM_rect_from_py(clip)
                native_matrix = pymupdf.JM_matrix_from_py(matrix)
                captured = {
                    "command_clip": list(clip), "command_matrix": list(matrix),
                    "native_clip": [getattr(native_rect, key) for key in ("x0", "y0", "x1", "y1")],
                    "native_matrix": [getattr(native_matrix, key) for key in ("a", "b", "c", "d", "e", "f")],
                    "projected_rect": list(projected),
                }
            stage = "pdf_render"
            pixmap = page.get_pixmap(matrix=matrix, clip=clip, colorspace=pymupdf.csRGB,
                                     alpha=False, annots=True)
            _dimensions(pixmap.width, pixmap.height)
            if (pixmap.width != pixels.width or pixmap.height != pixels.height
                    or pixmap.x != pixels.x0 or pixmap.y != pixels.y0):
                _fail("geometry_mismatch")
            if pixmap.n != 3 or pixmap.stride != pixmap.width * 3:
                _fail("pdf_render")
            samples = pixmap.samples_mv
            if samples.nbytes != pixmap.width * pixmap.height * 3:
                _fail("pdf_render")
            # The owned RGB bytes detach the result from the closed PDF/pixmap.
            image = Image.frombytes("RGB", (pixmap.width, pixmap.height), bytes(samples))
            if with_view:
                image_size = image.size
                if (type(image.mode) is not str or image.mode != "RGB"
                        or type(image_size) is not tuple or len(image_size) != 2
                        or any(type(value) is not int for value in image_size)
                        or image_size != (pixmap.width, pixmap.height)):
                    _fail("pdf_render")
                # Size/mode preflight bounds the genuine PIL byte allocation;
                # verify the returned image, not solely its Pixmap input bytes.
                rgb = image.tobytes()
                if type(rgb) is not bytes or len(rgb) != samples.nbytes:
                    _fail("pdf_render")
                rgb_sha256 = hashlib.sha256(rgb).hexdigest()
                if rgb_sha256 != hashlib.sha256(samples).hexdigest():
                    _fail("pdf_render")
                del rgb
                stage = "geometry_mismatch"
                view = _build_raster_view(
                    scope=scope, preview_profile=preview_profile, **captured,
                    pixel_rect=[pixmap.x, pixmap.y, pixmap.x + pixmap.width, pixmap.y + pixmap.height],
                    width=pixmap.width, height=pixmap.height, rgb_sha256=rgb_sha256)
                result = CropRasterPreview(image, view)
                stage = "pdf_render"
    except BaseException as failure:
        if image is not None:
            try:
                image.close()
            except BaseException:
                pass  # Disposal must not replace the active failure/cancellation.
        if with_view and isinstance(failure, Exception) and not isinstance(failure, CropPreviewError):
            raise CropPreviewError(code=stage) from None
        raise
    # The document context must exit successfully before ownership transfers.
    return result if with_view else image


def render_crop_scope(workspace, scope, *, preview_profile="fit"):
    """Return a fresh original-display crop image after exact input rechecks.

    ``workspace`` must be an actual fixed-path ReviewWorkspace. The client
    cannot supply a path, renderer, arbitrary transform/DPI, or model. Only
    fixed Fit/288-DPI/576-DPI profiles are admitted, within the same pixel caps.
    The physical scope is unchanged. More rendered pixels do not restore detail
    absent from an embedded low-resolution scan. A scope checksum binds the
    declared rectangle; it is not authenticated human correspondence.
    Normal failures are static and private; cancellation is never converted to
    a local preview failure. No image is returned after observed generation drift.
    This compatibility API renders in-process; the browser uses the separately
    owned fixed-worker controller instead.
    """
    preview_profile = validate_preview_profile(preview_profile)
    return _render_crop_scope(workspace, scope,
        lambda raw, checked: _render_pdf(raw, checked, preview_profile=preview_profile))


def render_crop_scope_with_view(workspace, scope, *, preview_profile="fit"):
    """Return an owned same-render image/declaration after normal input rechecks.

    This opt-in metadata path additionally requires the pure annotation geometry
    domain. It never falls back to the broader compatibility PIL-only path and
    does not prove human inspection or invert raster sampling. Like the public
    PIL API, this function is in-process; browser callers use fixed supervision.
    """
    preview_profile = validate_preview_profile(preview_profile)
    return _render_crop_scope(workspace, scope,
        lambda raw, checked: _render_pdf_with_view(raw, checked, preview_profile=preview_profile))


def _render_crop_scope(workspace, scope, render_pdf):
    """Shared checks around a trusted host-owned renderer, never a browser port."""
    stage = "input_verification"
    image = None
    transferred = False
    try:
        if type(workspace) is not ReviewWorkspace:
            _fail("input_verification")
        binding = _binding(workspace)
        directory_identity = workspace._directory_generation()
        stage = "scope_validation"
        scope = validate_crop_scope(scope, source_sha256=binding[4], page_count=binding[6])
        stage = "input_verification"
        source, source_digest, source_identity = _snapshot(binding[1], MAX_PDF_BYTES)
        recovery, recovery_digest, recovery_identity = _snapshot(binding[2], MAX_REPORT_BYTES)
        del recovery
        if source_digest != binding[4] or recovery_digest != binding[5]:
            _fail("input_changed")
        workspace.verify_inputs()
        if _binding(workspace) != binding:
            _fail("input_changed")
        stage = "pdf_render"
        image = render_pdf(source, scope)
        del source
        stage = "input_verification"
        workspace.verify_inputs()
        if _binding(workspace) != binding:
            _fail("input_changed")
        for path, limit, expected_digest, expected_identity in (
                (binding[1], MAX_PDF_BYTES, source_digest, source_identity),
                (binding[2], MAX_REPORT_BYTES, recovery_digest, recovery_identity)):
            raw, digest, identity = _snapshot(path, limit)
            del raw
            if digest != expected_digest or identity != expected_identity:
                _fail("input_changed")
        if _binding(workspace) != binding or workspace._directory_generation() != directory_identity:
            _fail("input_changed")
        transferred = True
        return image
    except CropPreviewError as failure:
        raise CropPreviewError(code=failure.code) from None
    except Exception:
        raise CropPreviewError(code=stage) from None
    finally:
        if image is not None and not transferred:
            try:
                image.close()
            except BaseException:
                pass  # Preserve the primary static failure or cancellation.
