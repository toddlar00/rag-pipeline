"""Inward crop-review UI state and validation helpers; no panel or host imports.

These bounded helpers are shared by legacy, uncertainty and archive panels.
Importing them does not build a UI, render a source, or grant review authority.
"""
from __future__ import annotations

import hashlib
import re
import struct
import threading
from uuid import uuid4

from ocr_crop_comparison import _bytes


_LIMIT = 2 * 1024 * 1024
_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_PREVIEW_NOTICES = {
    "scope_validation": "The requested crop scope does not match the fixed source declarations.",
    "input_verification": "The fixed source or saved review inputs could not be verified.",
    "input_changed": "Fixed inputs no longer match the accepted generation. Restart with matching fixed artifacts.",
    "pdf_render": "The original PDF crop could not be rendered.",
    "geometry_mismatch": "Original page or raster geometry did not match the declared crop.",
    "raster_limit": "This detail exceeds the bounded raster limits. Choose a lower detail or Fit. Use Reload detail for a draft already bound to this crop; otherwise use Load/Open. Load/Open can clear or replace typed text; copy unfinished text first. No render is retried automatically.",
    "preview_unavailable": "The original crop preview is unavailable.",
    "preview_timeout": "The crop preview worker reached its deadline. No image was accepted; select and load again if needed.",
    "preview_cancelled": "Crop preview was cancelled. Load the current pair to try again.",
    "preview_busy": "Another crop preview is running. Wait for it or cancel its view before loading this pair.",
    "cleanup_unconfirmed": "Preview cleanup could not be confirmed. Further work is blocked; close the review host and verify worker cleanup.",
}


class _Stale(ValueError):
    pass


class _ReferenceFieldError(ValueError):
    """Only local typed-field refusals; never expose values or backend errors."""

    _NOTICES = {
        "reference_type": "Enter the transcription as text; an empty transcription is allowed.",
        "reference_size": "Keep the transcription within 20,000 characters.",
        "reference_unicode": "The transcription contains invalid Unicode. Replace the invalid characters.",
        "critical_type": "Enter Critical entries as text, or leave the field empty.",
        "critical_size": "Keep Critical entries within 16,448 characters in total.",
        "critical_unicode": "Critical entries contain invalid Unicode. Replace the invalid characters.",
        "critical_count": "Use at most 64 Critical entries, one per line.",
        "critical_blank": "Remove blank lines from Critical entries, including the final empty line. Leave the field empty if none are needed.",
        "critical_entry_size": "Keep each Critical entry within 256 characters.",
        "critical_carriage_return": "Critical entries contain carriage returns. Re-enter the line breaks without carriage returns; text is not changed automatically.",
    }

    def __init__(self, code):
        self.code = code
        super().__init__(self._NOTICES[code] + " Then prepare and confirm again.")


class _CropState:
    def __init__(self):
        self.lock = threading.RLock()
        self.generation = uuid4().hex
        self.revision = 0
        self.selected = None
        self.baseline = None
        self.retry = None
        self.loaded = None
        self.pending = None
        self.reference_digest = _raw_reference_digest("", "")
        self.preview_profile = "fit"
        self.draft_scope = None

    def __deepcopy__(self, memo):
        return type(self)()

    def revoke(self, *, clear_view, clear_draft=True):
        self.generation = uuid4().hex
        self.revision += 1
        self.pending = None
        if clear_view:
            self.loaded = None
            if clear_draft:
                self.draft_scope = None
                self.reference_digest = _raw_reference_digest("", "")


class _SaveCropState(_CropState):
    """Enabled-only save authority; at most one <=2 MiB serialized declaration.

    The successful server result must survive until an explicit Save click.
    Keeping bounded immutable bytes prevents client metrics from being treated
    as that result. Image pixels and full OCR/archive reports are never cached.
    Deepcopy still creates a fresh, empty per-session state via the base hook.
    """

    def __init__(self):
        super().__init__()
        self.last_score = None
        self.save_revision = 0

    def invalidate_save(self):
        self.last_score = None
        self.save_revision += 1

    def revoke(self, *, clear_view, clear_draft=True):
        super().revoke(clear_view=clear_view, clear_draft=clear_draft)
        self.invalidate_save()


def _id(value):
    if type(value) is not str or _ID.fullmatch(value) is None:
        raise ValueError("invalid crop identifier")
    return value


def _sha(value):
    if type(value) is not str or _SHA.fullmatch(value) is None:
        raise ValueError("invalid crop binding")
    return value


def _text(value, maximum=100_000):
    if type(value) is not str or len(value) > maximum:
        raise ValueError("crop text exceeds display limit")
    value.encode("utf-8")
    return value


def _hash(value, maximum=_LIMIT):
    return hashlib.sha256(_bytes(value, maximum)).hexdigest()


def _display(value, maximum=_LIMIT):
    return _bytes(value, maximum).decode("utf-8")


def _context(workspace, state, annotation, controls, execution):
    if (type(state) is not dict or type(annotation) is not str or not annotation
            or state.get("annotation_context") != annotation
            or type(state.get("page")) is not int or not 1 <= state["page"] <= workspace.document.page_count
            or type(state.get("regions")) is not list or len(state["regions"]) > 20 or len(controls) != 9):
        raise _Stale()
    with execution.lock:
        if type(execution.runs) is not tuple or len(execution.runs) > 8:
            raise _Stale()
        execution_view = {"run": execution.display_run, "result_generation": execution.result_generation,
                          "runs": list(execution.runs)}
    return _hash({"annotation": annotation, "page": state["page"], "regions": state["regions"],
                  "controls": list(controls), "execution_view": execution_view}, 32_768)


def _raw_reference_digest(reference, critical):
    # Partial critical entries are normal while editing. Bound raw strings
    # before encoding; semantic validation belongs to Prepare and Score.
    _text(reference, 20_000)
    _text(critical, 64 * 257)
    return _hash([reference, critical], 256 * 1024)


def _reference(reference, critical):
    # Preserve exact authored strings, including an intentionally empty
    # transcription. Draft editing/saving continues to accept partial lines.
    for name, value, maximum in (("reference", reference, 20_000), ("critical", critical, 64 * 257)):
        if type(value) is not str:
            raise _ReferenceFieldError(name + "_type")
        if len(value) > maximum:
            raise _ReferenceFieldError(name + "_size")
        try:
            value.encode("utf-8")
        except UnicodeError:
            raise _ReferenceFieldError(name + "_unicode") from None
    entries = [] if critical == "" else critical.split("\n")
    if len(entries) > 64:
        raise _ReferenceFieldError("critical_count")
    if "\r" in critical:
        raise _ReferenceFieldError("critical_carriage_return")
    if any(not item.strip() for item in entries):
        raise _ReferenceFieldError("critical_blank")
    if any(len(item) > 256 for item in entries):
        raise _ReferenceFieldError("critical_entry_size")
    return reference, entries, _raw_reference_digest(reference, critical)


def _image_identity(image):
    from PIL import Image

    if (not isinstance(image, Image.Image) or image.mode != "RGB"
            or not 1 <= image.width <= 1400 or not 1 <= image.height <= 1400):
        raise ValueError("invalid original crop preview")
    digest = hashlib.sha256(b"ocr-crop-review-rgb-v1\0" + struct.pack("!II", image.width, image.height))
    digest.update(image.tobytes())
    return [image.width, image.height, digest.hexdigest()]


def _close_unreturned_image(image):
    """Close a rejected callback image, never an output owned by Gradio.

    The caller already selected a failure/cleared response. Even a secondary
    disposal cancellation must not replace that outcome or its primary unwind.
    Framework postprocessing and successful-output disposal remain separate.
    """
    if image is not None:
        try:
            image.close()
        except BaseException:
            pass


_ARCHIVE_ID = re.compile(r"[a-f0-9]{32}\Z")


def _fields(value, fields):
    if (type(value) is not dict or len(value) != len(fields)
            or any(type(key) is not str for key in value) or set(value) != set(fields)):
        raise ValueError("invalid archive view")


def _metadata(row):
    _fields(row, {"pack_id", "manifest_sha256", "status", "requires_attention"})
    if type(row["pack_id"]) is not str or not _ARCHIVE_ID.fullmatch(row["pack_id"]):
        raise ValueError("invalid catalog capability")
    if row["manifest_sha256"] is not None:
        _sha(row["manifest_sha256"])
    if row["status"] not in {"not_created", "incomplete", "present_unverified", "verified_complete", "cleanup_uncertain"} or row["requires_attention"] is not True:
        raise ValueError("invalid archive catalog state")
    return dict(row)
