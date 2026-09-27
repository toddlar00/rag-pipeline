"""Private saved-crop controls, independent of live OCR-run authority.

Reopening restores authored text and explicitly historical evidence, never
consent. Session state has bounded identities, one preparation and one fresh
score; deepcopy starts blank. Store/service calls never hold a session lock.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
from uuid import uuid4

from ocr_crop_comparison import _bytes, validate_crop_scope
from ocr_crop_review_runtime import CropPreviewError, validate_preview_profile
from ocr_review_crop_ui_common import _PREVIEW_NOTICES, _ReferenceFieldError, _close_unreturned_image, _display, _fields, _image_identity, _metadata, _raw_reference_digest, _reference, _sha, _text
from ocr_review_crop_preview_ui import ARCHIVE_PREVIEW_ID, PREVIEW_CHOICES, preview_image_update
from ocr_review_save_feedback import SAVE_PENDING, SAVE_UNCONFIRMED, build_save_feedback


_ID = re.compile(r"[a-f0-9]{32}\Z")
_REGION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_LIMIT = 4 * 1024 * 1024
_NO_PACK = object()
_NO_PROFILE = object()
_STALE = "Saved crop view changed. Open the current entry, then prepare and confirm again. Any published revision is retained."
_FAILED = "Saved crop work is unavailable or changed. Existing packs and displayed authored text were not deleted. No automatic retry was attempted."
_READ_FAILED = "Saved crop read is unavailable or changed. This view was cleared; retained packs were not modified. No automatic retry was attempted."
_HISTORY = "Historical local declarations only; saved scores and preview hashes are not current approval or authenticated review."


class _Stale(ValueError):
    pass


class _ArchiveState:
    def __init__(self):
        self.lock = threading.RLock()
        self.generation = uuid4().hex
        self.revision = 0
        self.action = 0
        self.action_token = uuid4().hex
        self.catalog = {}
        self.selected = None
        self.loaded = None
        self.pending = None
        self.last_score = None
        self.reference_digest = _raw_reference_digest("", "")
        self.preview_profile = "fit"
        self.draft_scope = None

    def __deepcopy__(self, memo):
        return type(self)()

    def revoke(self, *, clear_view=False, clear_draft=True):
        self.generation = uuid4().hex
        self.revision += 1
        self.action += 1
        self.action_token = uuid4().hex
        self.pending = None
        self.last_score = None
        if clear_view:
            self.loaded = None
            if clear_draft:
                self.draft_scope = None
                self.reference_digest = _raw_reference_digest("", "")


def _identifier(value):
    if type(value) is not str or not _ID.fullmatch(value):
        raise _Stale()
    return value


def build_crop_archive_panel(workspace, service, *, uncertainty=False):
    """Build one private archive Tab in Blocks; the service is fixed by host."""
    if type(uncertainty) is not bool:
        raise ValueError("invalid archive UI version")
    if uncertainty:
        return _build_uncertainty_archive_panel(workspace, service)
    import gradio as gr

    document = workspace.document
    binding = (document.source_sha256, document.recovery_sha256, document.page_count)

    def current_workspace():
        if workspace.document is not document or (document.source_sha256, document.recovery_sha256, document.page_count) != binding:
            raise _Stale()

    def require(session, token, pack_id=_NO_PACK, *, loaded=False, scoreable=False):
        current_workspace()
        if type(token) is not str or token != session.generation:
            raise _Stale()
        if pack_id is not _NO_PACK:
            _identifier(pack_id)
            if pack_id != session.selected or pack_id not in session.catalog:
                raise _Stale()
        if loaded:
            value = session.loaded
            if (value is None or value["pack_id"] != session.selected
                    or value["pack_sha256"] != session.catalog[session.selected]["manifest_sha256"]
                    or (scoreable and value["mode"] != "full")):
                raise _Stale()
            return value

    def require_profile(session, profile, *, loaded=False):
        if validate_preview_profile(profile) != session.preview_profile:
            raise _Stale()
        if loaded and (session.loaded is None or session.loaded["preview_profile"] != profile):
            raise _Stale()

    def admit_action(session, token, pack_id, action_token, preview_profile, *, scoreable=True):
        """Consume only the admitted view's authority before semantic parsing."""
        with session.lock:
            loaded = copy.deepcopy(require(session, token, pack_id, loaded=True, scoreable=scoreable))
            require_profile(session, preview_profile, loaded=True)
            # The browser must capture the exact action revision it displayed,
            # not borrow a newer server score when an old queued request runs.
            if type(action_token) is not str or action_token != session.action_token:
                raise _Stale()
            pending, score = session.pending, session.last_score
            session.action += 1
            session.pending = session.last_score = None
            return loaded, pending, score, session.action

    def require_action(session, token, pack_id, loaded, action, *, scoreable=True):
        require(session, token, pack_id, loaded=True, scoreable=scoreable)
        require_profile(session, loaded["preview_profile"], loaded=True)
        if session.loaded != loaded or session.action != action:
            raise _Stale()

    def clear(session, message, *, preserve_draft=False):
        fields = (gr.skip(), gr.skip()) if preserve_draft else ("", "")
        return session.generation, None, "", "", "", "", *fields, "", False, "", "", session.action_token, message

    def archive_preview_profile_changed(session, token, profile):
        with session.lock:
            try:
                require(session, token)
            except _Stale:
                return tuple(gr.skip() for _ in range(14))
            try:
                profile = validate_preview_profile(profile)
            except Exception:
                session.revoke(clear_view=True, clear_draft=False)
                return clear(session, "Preview detail selection is invalid. Cancel to resync a valid selection; authored text remains.", preserve_draft=True)
            if profile == session.preview_profile:
                return tuple(gr.skip() for _ in range(14))
            session.preview_profile = profile
            session.revoke(clear_view=True, clear_draft=False)
            return clear(session, "Detail selection changed; no render was started. Reload detail explicitly. Authored text remains; approval was cleared.", preserve_draft=True)

    def refresh_crop_archives(session):
        with session.lock:
            session.revoke(clear_view=True)
            session.selected = None
            session.catalog = {}
            generation = session.generation
        try:
            current_workspace()
            rows = service.catalog()
            if type(rows) is not list or len(rows) > 128:
                raise ValueError("catalog exceeds bounds")
            catalog = {}
            for row in rows:
                row = _metadata(row)
                if row["pack_id"] in catalog:
                    raise ValueError("duplicate archive capability")
                catalog[row["pack_id"]] = row
            with session.lock:
                require(session, generation)
                session.catalog = catalog
                choices = [(f"Saved entry {i}: {row['status']}", key) for i, (key, row) in enumerate(catalog.items(), 1)]
                return gr.update(choices=choices, value=None), *clear(session, "Catalog refreshed. Nothing was opened or selected automatically.")
        except _Stale:
            raise gr.Error(_STALE) from None
        except Exception:
            with session.lock:
                require(session, generation)
                return gr.update(choices=[], value=None), *clear(session, _READ_FAILED)

    def select_crop_archive(session, pack_id, token):
        with session.lock:
            # A single dropdown option click may emit both input and blur.
            # Old captures must not clear a newer view, and an unchanged value
            # must not rotate its token or replay old display fields.
            try:
                require(session, token)
            except _Stale:
                return tuple(gr.skip() for _ in range(14))
            if ((pack_id is None and session.selected is None)
                    or (type(pack_id) is str and pack_id == session.selected and pack_id in session.catalog)):
                return tuple(gr.skip() for _ in range(14))
            session.revoke(clear_view=True)
            session.selected = None
            try:
                _identifier(pack_id)
                if pack_id not in session.catalog:
                    raise _Stale()
                session.selected = pack_id
                message = "Entry selected only. Open and verify it, or recover its authored draft explicitly."
            except Exception:
                message = "Select a catalog entry from this review session."
            return clear(session, message)

    def cancel_archive_preview(session, preview_profile="fit"):
        with session.lock:
            session.revoke(clear_view=True, clear_draft=False)
            try:
                session.preview_profile = validate_preview_profile(preview_profile)
            except Exception:
                return clear(session, "Preview cancelled; choose a valid detail profile and cancel again to resync. Authored text remains.", preserve_draft=True)
            return clear(session, "Preview cancellation requested and approval cleared. Authored text remains; detail selection is resynced. Load or reload explicitly. Cancellation is not proof of worker cleanup.", preserve_draft=True)

    def start_read(session, pack_id, token, preview_profile=_NO_PROFILE, *, draft=None):
        with session.lock:
            require(session, token, pack_id)
            if preview_profile is not _NO_PROFILE:
                require_profile(session, preview_profile)
            digest = _sha(session.catalog[pack_id]["manifest_sha256"])
            if draft is not None:
                content = _raw_reference_digest(*draft)
                marker = session.draft_scope
                if marker is None or marker["pack_id"] != pack_id or marker["pack_sha256"] != digest:
                    raise _Stale()
            session.revoke(clear_view=True, clear_draft=draft is None)
            if draft is not None:
                session.reference_digest = content
            return session.generation, digest

    def opened(value, pack_id, digest):
        _fields(value, {"pack_id", "pack_sha256", "scope", "baseline", "retry", "draft", "historical_reviewed", "image",
                        "validation_scope", "requires_attention", "canonical_extraction_modified"})
        # Never JSON-serialize an image or a path supplied in its place.
        view = {k: v for k, v in value.items() if k != "image"}
        _bytes(view, _LIMIT)
        if (value["pack_id"] != pack_id or value["pack_sha256"] != digest
                or value["validation_scope"] != "historical_local_declarations"
                or value["requires_attention"] is not True or value["canonical_extraction_modified"] is not False):
            raise ValueError("archive binding differs")
        scope = validate_crop_scope(value["scope"], source_sha256=binding[0], page_count=binding[2])
        _fields(value["draft"], {"reference", "critical_tokens_text"})
        content = _raw_reference_digest(value["draft"]["reference"], value["draft"]["critical_tokens_text"])
        for side in ("baseline", "retry"):
            row = value[side]
            _fields(row, {"region_id", "report_sha256", "request_sha256", "operation", "status", "text", "geometry", "recipe", "configuration"})
            if type(row["region_id"]) is not str or not _REGION.fullmatch(row["region_id"]):
                raise ValueError("invalid crop occurrence")
            _sha(row["report_sha256"])
            _sha(row["request_sha256"])
            if row["operation"] not in ("regions", "hardscan") or row["status"] not in ("review_required", "empty_candidate", "retry_failed", "abstained"):
                raise ValueError("invalid crop result")
            if row["text"] is not None:
                _text(row["text"])
            if (row["text"] is not None) != (row["status"] in ("review_required", "empty_candidate")):
                raise ValueError("invalid candidate availability")
        historical = value["historical_reviewed"]
        if historical is not None:
            _fields(historical, {"reference", "comparison", "source_image"})
        return scope, content, _image_identity(value["image"])

    def open_view(session, pack_id, token, preview_profile, *, draft=None):
        generation = None
        owned_image, transferred = None, False
        try:
            generation, digest = start_read(session, pack_id, token, preview_profile, draft=draft)
            with session.lock:
                marker = copy.deepcopy(session.draft_scope)

            def cancelled():
                with session.lock:
                    return session.generation != generation

            value = service.open(pack_id, preview_profile=preview_profile, cancel_requested=cancelled)
            owned_image = value["image"]
            scope, content, image = opened(value, pack_id, digest)
            if draft is not None and marker["scope"] != scope:
                raise ValueError("detail reload scope changed")
            details = _display({"scope": scope, "pack_sha256": digest, "fresh_image": image,
                "preview_profile": preview_profile,
                **{side: {k: v for k, v in value[side].items() if k != "text"} for side in ("baseline", "retry")}})
            history = "No historical reviewed comparison saved." if value["historical_reviewed"] is None else _HISTORY + "\n" + _display(value["historical_reviewed"], _LIMIT)
            with session.lock:
                require(session, generation, pack_id)
                require_profile(session, preview_profile)
                session.loaded = {"pack_id": pack_id, "pack_sha256": digest, "scope": scope, "image": tuple(image), "mode": "full", "preview_profile": preview_profile}
                session.draft_scope = {"pack_id": pack_id, "pack_sha256": digest, "scope": scope}
                if draft is None:
                    session.reference_digest = content
                fields = (gr.skip(), gr.skip()) if draft is not None else (value["draft"]["reference"], value["draft"]["critical_tokens_text"])
                output = (generation, preview_image_update(value["image"], preview_profile), value["baseline"]["text"] or "", value["retry"]["text"] or "", details,
                    history, *fields, "", False, "", "", session.action_token,
                    "Historical pack verified; original crop freshly rendered. " + ("Current authored text preserved." if draft is not None else "Authored text restored without consent.") + " Prepare and confirm before new scoring.")
            transferred = True
            return output
        except _Stale:
            raise gr.Error(_STALE) from None
        except Exception as error:
            with session.lock:
                if generation is not None and session.generation != generation:
                    raise gr.Error(_STALE) from None
                session.revoke(clear_view=True, clear_draft=draft is None)
                notice = _FAILED if draft is not None else _READ_FAILED
                if type(error) is CropPreviewError:
                    notice += " " + _PREVIEW_NOTICES.get(error.code, _PREVIEW_NOTICES["preview_unavailable"])
                return clear(session, notice, preserve_draft=draft is not None)
        finally:
            if not transferred:
                _close_unreturned_image(owned_image)

    def open_crop_archive(session, pack_id, token, preview_profile):
        return open_view(session, pack_id, token, preview_profile)

    def reload_archive_detail(session, pack_id, token, reference, critical, preview_profile):
        return open_view(session, pack_id, token, preview_profile, draft=(reference, critical))

    def recover_crop_archive_draft(session, pack_id, token):
        generation = None
        try:
            generation, digest = start_read(session, pack_id, token)
            value = service.recover_draft(pack_id)
            _bytes(value, 256 * 1024)
            _fields(value, {"draft", "scope", "pack_sha256", "status", "requires_attention", "canonical_extraction_modified"})
            if (value["pack_sha256"] != digest or value["status"] != "unverified_saved_draft"
                    or value["requires_attention"] is not True or value["canonical_extraction_modified"] is not False):
                raise ValueError("draft recovery binding differs")
            scope = validate_crop_scope(value["scope"], source_sha256=binding[0], page_count=binding[2])
            _fields(value["draft"], {"reference", "critical_tokens_text"})
            draft = value["draft"]
            content = _raw_reference_digest(draft["reference"], draft["critical_tokens_text"])
            with session.lock:
                require(session, generation, pack_id)
                session.loaded = {"pack_id": pack_id, "pack_sha256": digest, "scope": scope, "image": None, "mode": "draft", "preview_profile": session.preview_profile}
                session.draft_scope = {"pack_id": pack_id, "pack_sha256": digest, "scope": scope}
                session.reference_digest = content
                return (generation, None, "", "", _display(scope), "", draft["reference"], draft["critical_tokens_text"],
                    "", False, "", "", session.action_token, "Unverified authored draft recovered only. No crop image, candidate evidence or metrics were restored; this view cannot score.")
        except _Stale:
            raise gr.Error(_STALE) from None
        except Exception:
            with session.lock:
                if generation is not None and session.generation != generation:
                    raise gr.Error(_STALE) from None
                session.revoke(clear_view=True)
                return clear(session, _READ_FAILED)

    def archive_reference_edited(session, token, reference, critical, action_token):
        with session.lock:
            try:
                require(session, token)
                if type(action_token) is not str or action_token != session.action_token:
                    raise _Stale()
            except _Stale:
                return tuple(gr.skip() for _ in range(7))
            try:
                digest = _raw_reference_digest(reference, critical)
            except Exception:
                session.revoke()
                session.reference_digest = None
                return session.generation, "", False, "", "", session.action_token, "Reference fields exceed bounds; correct them and prepare again."
            if digest == session.reference_digest:
                return tuple(gr.skip() for _ in range(7))
            session.revoke()
            session.reference_digest = digest
            return session.generation, "", False, "", "", session.action_token, "Reference changed. Saved historical scores are not current; prepare and confirm again."

    def prepare_archive_reference(session, pack_id, token, reference, critical, action_token, preview_profile):
        try:
            loaded, _, _, action = admit_action(session, token, pack_id, action_token, preview_profile)
            try:
                reference, entries, content = _reference(reference, critical)
            except _ReferenceFieldError as error:
                with session.lock:
                    require_action(session, token, pack_id, loaded, action)
                    session.reference_digest = None
                    session.action_token = uuid4().hex
                    return "", False, "", "", session.action_token, str(error)
            description = _display({"pack_sha256": loaded["pack_sha256"], "scope": loaded["scope"], "image": list(loaded["image"]),
                "reference": reference, "critical_entries": entries, "edit_revision": session.revision, "preview_profile": preview_profile,
                "review": "fresh operator declaration only; no correction adoption"}, 256 * 1024)
            with session.lock:
                require_action(session, token, pack_id, loaded, action)
                session.reference_digest = content
                ticket = uuid4().hex
                session.pending = (ticket, token, session.revision, content, pack_id, loaded["pack_sha256"], loaded["image"], action)
                session.action_token = uuid4().hex
                return ticket, False, description, "", session.action_token, "Inspect this exact fresh crop, transcription and critical entries before confirming."
        except _Stale:
            raise gr.Error(_STALE) from None
        except Exception:
            raise gr.Error(_FAILED) from None

    def score_archive_reference(session, pack_id, token, reference, critical, ticket, confirmed, action_token, preview_profile):
        try:
            with session.lock:
                loaded = require(session, token, pack_id, loaded=True, scoreable=True)
                require_profile(session, preview_profile, loaded=True)
                pending = session.pending
                if (type(action_token) is not str or action_token != session.action_token
                        or (pending is not None and (type(ticket) is not str
                            or pending != (ticket, token, session.revision, pending[3], pack_id,
                                           loaded["pack_sha256"], loaded["image"], session.action)))):
                    raise _Stale()
                loaded, pending, _, action = admit_action(session, token, pack_id, action_token, preview_profile)
                # A current action still revokes in-flight work, including an
                # unconfirmed click. It never reaches field parsing or scoring.
                if confirmed is not True or type(ticket) is not str or pending is None:
                    raise _Stale()
            try:
                reference, entries, content = _reference(reference, critical)
            except _ReferenceFieldError as error:
                with session.lock:
                    require_action(session, token, pack_id, loaded, action)
                    session.reference_digest = None
                    session.action_token = uuid4().hex
                    return "", False, "", session.action_token, str(error)
            with session.lock:
                require_action(session, token, pack_id, loaded, action)
                expected = (ticket, token, session.revision, content, pack_id, loaded["pack_sha256"], loaded["image"], action - 1)
                if confirmed is not True or type(ticket) is not str or pending != expected:
                    raise _Stale()
                revision = session.revision
            result = service.compare(pack_id, expected_pack_sha256=loaded["pack_sha256"], reference=reference,
                                     critical_tokens=entries, confirmed=True)
            _bytes(result, _LIMIT)
            _fields(result, {"pack_sha256", "reference", "comparison", "requires_attention", "canonical_extraction_modified"})
            if (result["pack_sha256"] != loaded["pack_sha256"] or result["requires_attention"] is not True
                    or result["canonical_extraction_modified"] is not False or result["reference"]["reference"] != reference
                    or result["reference"]["critical_tokens"] != entries or result["reference"]["scope"] != loaded["scope"]
                    or result["comparison"]["source_sha256"] != binding[0] or result["comparison"]["scope"] != loaded["scope"]):
                raise ValueError("score correspondence differs")
            metrics = _display(result["comparison"])
            reviewed = {"reference": result["reference"], "comparison": result["comparison"],
                        "source_image": dict(zip(("width", "height", "rgb_sha256"), loaded["image"]))}
            _bytes(reviewed, _LIMIT)
            with session.lock:
                require_action(session, token, pack_id, loaded, action)
                if session.revision != revision or session.reference_digest != content or session.pending is not None:
                    raise _Stale()
                session.action_token = uuid4().hex
                session.last_score = {"generation": token, "content": content, "loaded": loaded, "action": action,
                                      "action_token": session.action_token, "reviewed": copy.deepcopy(reviewed)}
                return "", False, metrics, session.action_token, "Fresh reference-backed comparison for this crop only. You may explicitly save this reviewed revision once; nothing was adopted."
        except _Stale:
            raise gr.Error(_STALE) from None
        except Exception:
            raise gr.Error(_FAILED) from None

    def save(session, pack_id, token, reference, critical, action_token, preview_profile, *, reviewed):
        try:
            loaded, _, score, action = admit_action(session, token, pack_id, action_token, preview_profile, scoreable=reviewed)
            content = _raw_reference_digest(reference, critical)
            with session.lock:
                require_action(session, token, pack_id, loaded, action, scoreable=reviewed)
                saved_review = None
                if reviewed:
                    if (score is None or score["generation"] != token or score["content"] != content
                            or score["loaded"] != loaded or score["action"] != action - 1 or score["action_token"] != action_token):
                        raise _Stale()
                    saved_review = copy.deepcopy(score["reviewed"])
                elif content != session.reference_digest:
                    session.revoke()
                    session.reference_digest = content
                session.pending = session.last_score = None  # Consume on ATTEMPT, including failure/cancellation.
                generation = session.generation
                action = session.action

            def verify_current():
                with session.lock:
                    require_action(session, generation, pack_id, loaded, action, scoreable=reviewed)
                    if session.reference_digest != content:
                        raise _Stale()
                return True

            # Store owns its lock and calls verify_current -> session.lock.
            # Never invert that order by holding session.lock here.
            result = service.save_revision(pack_id, expected_pack_sha256=loaded["pack_sha256"], reference=reference,
                critical_tokens_text=critical, reviewed=saved_review, verify_current=verify_current)
            result = _metadata(result)
            if result["status"] != "verified_complete" or result["manifest_sha256"] is None:
                raise ValueError("save did not report verified completion")
            verify_current()
            with session.lock:
                require_action(session, generation, pack_id, loaded, action, scoreable=reviewed)
                session.action_token = uuid4().hex
                return generation, "", False, "", "", session.action_token, "New private revision saved. Refresh the catalog explicitly; reopening will not restore approval."
        except _Stale:
            raise gr.Error(_STALE) from None
        except Exception:
            raise gr.Error(_FAILED) from None

    def save_archive_draft(session, pack_id, token, reference, critical, action_token, preview_profile):
        return save(session, pack_id, token, reference, critical, action_token, preview_profile, reviewed=False)

    def save_archive_reviewed(session, pack_id, token, reference, critical, action_token, preview_profile):
        return save(session, pack_id, token, reference, critical, action_token, preview_profile, reviewed=True)

    with gr.Tab("Saved crop review packs"):
        gr.Markdown("Open private, previously saved crop work without running OCR. Historical scores are declarations, not approval. "
                    "No entry is auto-selected. Recover draft is text-only; new scoring needs a fresh original crop and confirmation.")
        state = gr.State(_ArchiveState())
        view = gr.Textbox(value="", visible=False, interactive=False)
        action_view = gr.Textbox(value="", visible=False, interactive=False)
        refresh = gr.Button("Refresh saved crop catalog")
        catalog = gr.Dropdown(choices=[], value=None, label="Saved crop entry", allow_custom_value=False)
        with gr.Row():
            open_button = gr.Button("Open pack and fresh original crop")
            recover = gr.Button("Recover authored draft only")
            cancel = gr.Button("Cancel archive preview and clear approval")
        preview_profile = gr.Dropdown(choices=PREVIEW_CHOICES, value="fit", allow_custom_value=False, label="Original archive crop detail")
        reload_detail = gr.Button("Reload archive detail, keeping my draft")
        gr.Markdown("Changing detail does not render or run OCR. After rapid changes, cancel to resync the selected profile, then reload explicitly. Authored text is kept; review must be prepared and confirmed again.")
        gr.Markdown("Fresh original requested crop (not processed OCR input)")
        image = gr.Image(label="Fresh original crop", show_label=False, type="pil", format="png", interactive=False, sources=[], buttons=[], elem_id=ARCHIVE_PREVIEW_ID)
        with gr.Row():
            baseline = gr.Textbox(label="Historical baseline OCR (empty/unavailable retained)", interactive=False)
            retry = gr.Textbox(label="Historical retry OCR (empty/unavailable retained)", interactive=False)
        details = gr.Textbox(label="Archive scope and recipe declarations (verify by opening pack)", interactive=False)
        historical = gr.Textbox(label="Historical saved comparison — NOT current approval", interactive=False)
        reference = gr.Textbox(value="", label="Your transcription (check a fresh original crop before scoring)", max_length=20_000, lines=5)
        critical = gr.Textbox(value="", label="Critical entries (one literal nonempty entry per line)", max_length=64 * 257, lines=3)
        prepare = gr.Button("Prepare exact archive reference revision")
        pending = gr.Textbox(value="", visible=False, interactive=False)
        confirmed = gr.Checkbox(value=False, label="I checked this fresh original crop and exact reference revision")
        prepared = gr.Textbox(label="Prepared fresh reference declaration", interactive=False)
        score = gr.Button("Score confirmed archive reference once")
        metrics = gr.Textbox(label="Fresh same-crop comparison (not canonical correction)", interactive=False)
        with gr.Row():
            save_draft = gr.Button("Save new draft revision")
            save_reviewed = gr.Button("Save new reviewed revision once")
        notice = gr.Textbox(label="Archive status", interactive=False)

    outputs = [view, image, baseline, retry, details, historical, reference, critical, pending, confirmed, prepared, metrics, action_view, notice]
    edit_outputs = [view, pending, confirmed, prepared, metrics, action_view, notice]
    private = {"api_visibility": "private", "show_progress": "hidden"}
    quick = {**private, "queue": False, "trigger_mode": "multiple"}
    slow = {**private, "concurrency_id": "ocr-crop-archive-review", "concurrency_limit": 1}
    refresh.click(refresh_crop_archives, [state], [catalog, *outputs], **slow)
    catalog.input(select_crop_archive, [state, catalog, view], outputs, **quick)
    cancel.click(cancel_archive_preview, [state, preview_profile], outputs, **quick)
    preview_profile.input(archive_preview_profile_changed, [state, view, preview_profile], outputs, **quick)
    common = [state, catalog, view]
    open_button.click(open_crop_archive, [*common, preview_profile], outputs, **slow)
    reload_detail.click(reload_archive_detail, [*common, reference, critical, preview_profile], outputs, **slow)
    recover.click(recover_crop_archive_draft, common, outputs, **slow)
    for component in (reference, critical):
        component.change(archive_reference_edited, [state, view, reference, critical, action_view], edit_outputs, **quick)
    prepare.click(prepare_archive_reference, [*common, reference, critical, action_view, preview_profile], [pending, confirmed, prepared, metrics, action_view, notice], **slow)
    score.click(score_archive_reference, [*common, reference, critical, pending, confirmed, action_view, preview_profile], [pending, confirmed, metrics, action_view, notice], **slow)
    save_draft.click(save_archive_draft, [*common, reference, critical, action_view, preview_profile], edit_outputs, **slow)
    save_reviewed.click(save_archive_reviewed, [*common, reference, critical, action_view, preview_profile], edit_outputs, **slow)


class _UncertaintyArchiveState(_ArchiveState):
    """Bounded declarations only; no images, reports, or restored consent."""

    def __init__(self):
        super().__init__()
        self.uncertainty = {"journal": None, "annotations": [], "dirty": False}
        self.image_token = ""
        self.mode = "browse"
        self.mode_token = uuid4().hex
        self.selection = None
        self.annotation_id = None
        self.form_digest = None
        self.edit_lease = None

    def revoke(self, *, clear_view=False, clear_draft=True):
        super().revoke(clear_view=clear_view, clear_draft=clear_draft)
        self.edit_lease = None
        if clear_view:
            self.image_token = ""
            self.mode = "browse"
            self.mode_token = uuid4().hex
            self.selection = None
            if clear_draft:
                self.uncertainty = {"journal": None, "annotations": [], "dirty": False}
                self.annotation_id = None
                self.form_digest = None


def _build_uncertainty_archive_panel(workspace, service):
    """Explicit v2 controls; the default v1 event graph above stays unchanged.

    All callbacks return the same 14-component prefix, plus five editor fields.
    The independent image token is issued ONLY with a successful Image output.
    Later responses may acknowledge a captured image token but never mint one.
    Transport ordering after a response has returned is not an attested boundary.
    """
    import gradio as gr
    import ocr_crop_advice as advice
    import ocr_review_crop_uncertainty as authoring
    import ocr_review_crop_uncertainty_editor as overlay
    from PIL import Image
    from ocr_crop_raster_view import selection_to_scope_bbox, validate_raster_view
    from ocr_crop_review_runtime import CropRasterPreview

    document = workspace.document
    binding = (document.source_sha256, document.recovery_sha256, document.page_count)
    size = 19

    def skips():
        return tuple(gr.skip() for _ in range(size))

    def require(session, token, pack_id=_NO_PACK, *, action=_NO_PACK, image=_NO_PACK, profile=_NO_PROFILE, full=False):
        if (workspace.document is not document or
                (document.source_sha256, document.recovery_sha256, document.page_count) != binding
                or type(token) is not str or token != session.generation):
            raise _Stale()
        if pack_id is not _NO_PACK:
            _identifier(pack_id)
            if pack_id != session.selected or pack_id not in session.catalog:
                raise _Stale()
        if action is not _NO_PACK and (type(action) is not str or action != session.action_token):
            raise _Stale()
        if image is not _NO_PACK and (type(image) is not str or image != session.image_token):
            raise _Stale()
        if profile is not _NO_PROFILE and validate_preview_profile(profile) != session.preview_profile:
            raise _Stale()
        loaded = session.loaded
        if full or pack_id is not _NO_PACK and loaded is not None:
            if (loaded is None or loaded["pack_id"] != session.selected
                    or loaded["pack_sha256"] != session.catalog[session.selected]["manifest_sha256"]
                    or full and (loaded["mode"] != "full" or not session.image_token
                                 or loaded["preview_profile"] != session.preview_profile)):
                raise _Stale()
        return loaded

    def touch(session):
        session.action += 1
        session.action_token = uuid4().hex
        session.pending = session.last_score = None
        session.edit_lease = None

    def require_edit(session, token, action_token, image_token, *, full=False):
        # The captured pre-action token may invalidate only its still-running
        # operation. Business actions never borrow this temporary edit lease.
        lease = session.edit_lease
        leased = (lease is not None and type(token) is str and type(action_token) is str
            and type(image_token) is str and (token, action_token, image_token) == lease[:3]
            and session.action_token == lease[3] and session.loaded is lease[4]
            and session.mode_token == lease[5])
        return require(session, token, action=session.action_token if leased else action_token,
                       image=image_token, full=full)

    def finish_action(session, action):
        with session.lock:
            if session.edit_lease is not None and session.edit_lease[3] == action:
                session.edit_lease = None

    def summary(session):
        return _display({"dirty": session.uncertainty["dirty"],
            "annotations": session.uncertainty["annotations"],
            "pending_selection": session.selection,
            "notice": "Source-bound operator declarations, not OCR corrections or approval."}, 256 * 1024)

    def response(session, notice, *, captured_image=None, clear_image=False, clear_text=False, values=None):
        result = list(skips())
        for index, value in {0: session.generation, 8: "", 9: False, 10: "", 11: "",
                12: session.action_token, 13: notice, 16: summary(session),
                17: gr.update(choices=[(f"{i}: {row['kind']} / {row['status']}", row["annotation_id"])
                    for i, row in enumerate(session.uncertainty["annotations"], 1)], value=session.annotation_id),
                18: session.mode}.items():
            result[index] = value
        if clear_image:
            for index in (1, 15):
                result[index] = None
            for index in (2, 3, 4, 5, 14):
                result[index] = ""
        elif captured_image and captured_image == session.image_token and session.loaded is not None:
            result[15] = overlay.editor_value(image_token=captured_image, action_token=session.action_token,
                mode_token=session.mode_token, mode=session.mode,
                **authoring.overlay_spec(session.uncertainty, scope=session.loaded["scope"],
                                        raster_view=session.loaded["raster_view"]))
        if clear_text:
            result[6] = result[7] = ""
        if values:
            for index, value in values.items():
                result[index] = value
        return tuple(result)

    def raw_edit(session, reference, critical):
        digest = _raw_reference_digest(reference, critical)
        if digest != session.reference_digest:
            session.uncertainty = authoring.dirty_uncertainty(session.uncertainty)
            session.reference_digest = digest
            session.revision += 1
        return digest

    def refresh_crop_archives(session):
        with session.lock:
            session.revoke(clear_view=True)
            session.catalog, session.selected = {}, None
            token = session.generation
        try:
            rows = service.catalog()
            if type(rows) is not list or len(rows) > 128:
                raise ValueError()
            catalog = {}
            for row in rows:
                row = _metadata(row)
                if row["pack_id"] in catalog:
                    raise ValueError()
                catalog[row["pack_id"]] = row
            with session.lock:
                require(session, token)
                session.catalog = catalog
                return (gr.update(choices=[(f"Saved entry {i}: {row['status']}", key)
                    for i, (key, row) in enumerate(catalog.items(), 1)], value=None),
                    *response(session, "Catalog refreshed; select and open explicitly.", clear_image=True, clear_text=True))
        except _Stale:
            raise gr.Error(_STALE) from None
        except Exception:
            with session.lock:
                require(session, token)
                return gr.update(choices=[], value=None), *response(session, _READ_FAILED, clear_image=True, clear_text=True)

    def select_crop_archive(session, pack_id, token):
        with session.lock:
            try:
                require(session, token)
            except _Stale:
                return skips()
            if ((pack_id is None and session.selected is None) or
                    type(pack_id) is str and pack_id == session.selected and pack_id in session.catalog):
                return skips()
            session.revoke(clear_view=True)
            session.selected = None
            if type(pack_id) is str and _ID.fullmatch(pack_id) and pack_id in session.catalog:
                session.selected = pack_id
            return response(session, "Open the selected entry explicitly. Prior local draft and approval were cleared.", clear_image=True, clear_text=True)

    def archive_preview_profile_changed(session, token, profile):
        with session.lock:
            try:
                require(session, token)
            except _Stale:
                return skips()
            try:
                profile = validate_preview_profile(profile)
            except Exception:
                session.revoke(clear_view=True, clear_draft=False)
                return response(session, "Invalid detail choice; cancel to resync. Draft and history remain.", clear_image=True)
            if profile == session.preview_profile:
                return skips()
            session.preview_profile = profile
            session.revoke(clear_view=True, clear_draft=False)
            return response(session, "Detail changed; reload explicitly. Draft and source-coordinate history remain; approval was cleared.", clear_image=True)

    def cancel_archive_preview(session, profile="fit"):
        with session.lock:
            session.revoke(clear_view=True, clear_draft=False)
            try:
                session.preview_profile = validate_preview_profile(profile)
            except Exception:
                pass
            return response(session, "Preview cancellation requested; no cleanup guarantee. Draft and history remain. Reload explicitly.", clear_image=True)

    def open_crop_archive(session, pack_id, token, reference, critical, preview_profile):
        owner, transferred, generation = None, False, None
        preserve = False
        try:
            with session.lock:
                require(session, token, pack_id, profile=preview_profile)
                digest = _sha(session.catalog[pack_id]["manifest_sha256"])
                marker = session.draft_scope
                preserve = marker is not None and marker["pack_id"] == pack_id and marker["pack_sha256"] == digest
                if preserve:
                    raw_edit(session, reference, critical)
                session.revoke(clear_view=True, clear_draft=not preserve)
                generation = session.generation

            def cancelled():
                with session.lock:
                    return session.generation != generation

            value = service.open_with_view(pack_id, preview_profile=preview_profile, cancel_requested=cancelled)
            if type(value) is dict:
                owner = value.get("preview")
            if type(owner) is not CropRasterPreview:
                raise ValueError()
            _fields(value, {"pack_id", "pack_sha256", "scope", "baseline", "retry", "draft", "historical_reviewed", "preview",
                "validation_scope", "requires_attention", "canonical_extraction_modified"})
            _bytes({k: v for k, v in value.items() if k != "preview"}, _LIMIT)
            if (value["pack_id"] != pack_id or value["pack_sha256"] != digest
                    or value["validation_scope"] != "historical_local_declarations"
                    or value["requires_attention"] is not True or value["canonical_extraction_modified"] is not False):
                raise ValueError()
            scope = validate_crop_scope(value["scope"], source_sha256=binding[0], page_count=binding[2])
            view = validate_raster_view(owner.raster_view, scope=scope)
            image = owner.image
            if (not isinstance(image, Image.Image) or image.mode != "RGB"
                    or not 1 <= image.width <= 1400 or not 1 <= image.height <= 1400):
                raise ValueError("invalid original crop preview")
            rgb = image.tobytes()
            if (type(rgb) is not bytes or len(rgb) != image.width * image.height * 3
                    or image.size != (view["width"], view["height"])
                    or hashlib.sha256(rgb).hexdigest() != view["rgb_sha256"]
                    or view["preview_profile"] != preview_profile or preserve and marker["scope"] != scope):
                raise ValueError()
            restored = authoring.restore_uncertainty(value["draft"])
            content = _raw_reference_digest(value["draft"]["reference"], value["draft"]["critical_tokens_text"])
            for side in ("baseline", "retry"):
                row = value[side]
                _fields(row, {"region_id", "report_sha256", "request_sha256", "operation", "status", "text", "geometry", "recipe", "configuration"})
                if (type(row["region_id"]) is not str or not _REGION.fullmatch(row["region_id"])
                        or row["operation"] not in ("regions", "hardscan")
                        or row["status"] not in ("review_required", "empty_candidate", "retry_failed", "abstained")):
                    raise ValueError()
                _sha(row["report_sha256"])
                _sha(row["request_sha256"])
                if row["text"] is not None:
                    _text(row["text"])
                if (row["text"] is not None) != (row["status"] in ("review_required", "empty_candidate")):
                    raise ValueError()
            crop_advice = advice.build_crop_advice(rgb, scope=scope, raster_view=view)
            advice_notice = advice.crop_advice_text(crop_advice)
            with session.lock:
                require(session, generation, pack_id, profile=preview_profile)
                if not preserve:
                    session.uncertainty, session.reference_digest = restored, content
                session.loaded = {"pack_id": pack_id, "pack_sha256": digest, "scope": scope,
                    "raster_view": view, "mode": "full", "preview_profile": preview_profile,
                    "pair": {side: {key: value[side][key] for key in ("report_sha256", "region_id")} for side in ("baseline", "retry")}}
                session.draft_scope = {"pack_id": pack_id, "pack_sha256": digest, "scope": scope}
                session.image_token = uuid4().hex
                changes = {1: preview_image_update(owner.image, preview_profile), 2: value["baseline"]["text"] or "",
                    3: value["retry"]["text"] or "", 4: _display({"scope": scope, "raster_view": view, "advice": crop_advice}),
                    5: _HISTORY + "\n" + _display(value["historical_reviewed"], _LIMIT), 14: session.image_token}
                if not preserve:
                    changes.update({6: value["draft"]["reference"], 7: value["draft"]["critical_tokens_text"]})
                output = response(session, "Fresh original crop loaded. Local history retained; no approval restored. Explicitly apply changes, then prepare and confirm. " + advice_notice,
                    captured_image=session.image_token, values=changes)
            transferred = True
            return output
        except _Stale:
            raise gr.Error(_STALE) from None
        except Exception as error:
            with session.lock:
                if generation is None or session.generation != generation:
                    raise gr.Error(_STALE) from None
                session.revoke(clear_view=True, clear_draft=not preserve)
                notice = _READ_FAILED
                if type(error) is CropPreviewError:
                    notice += " " + _PREVIEW_NOTICES.get(error.code, _PREVIEW_NOTICES["preview_unavailable"])
                return response(session, notice, clear_image=True, clear_text=not preserve)
        finally:
            if not transferred:
                _close_unreturned_image(owner)

    def reload_archive_detail(session, pack_id, token, reference, critical, preview_profile):
        return open_crop_archive(session, pack_id, token, reference, critical, preview_profile)

    def recover_crop_archive_draft(session, pack_id, token):
        generation = None
        try:
            with session.lock:
                require(session, token, pack_id)
                # Recovery is not an implicit reset of a currently authored head.
                if session.draft_scope is not None:
                    touch(session)
                    return response(session, "Local draft/history already retained. Reload the image, or explicitly select another entry before recovery.")
                digest = _sha(session.catalog[pack_id]["manifest_sha256"])
                session.revoke(clear_view=True)
                generation = session.generation
            value = service.recover_draft_v2(pack_id)
            _bytes(value, _LIMIT)
            _fields(value, {"draft", "scope", "pack_sha256", "status", "requires_attention", "canonical_extraction_modified"})
            if (value["pack_sha256"] != digest or value["status"] != "unverified_saved_draft"
                    or value["requires_attention"] is not True or value["canonical_extraction_modified"] is not False):
                raise ValueError()
            scope = validate_crop_scope(value["scope"], source_sha256=binding[0], page_count=binding[2])
            restored = authoring.restore_uncertainty(value["draft"])
            content = _raw_reference_digest(value["draft"]["reference"], value["draft"]["critical_tokens_text"])
            with session.lock:
                require(session, generation, pack_id)
                session.uncertainty, session.reference_digest = restored, content
                session.loaded = {"pack_id": pack_id, "pack_sha256": digest, "scope": scope,
                    "mode": "draft", "preview_profile": session.preview_profile}
                session.draft_scope = {"pack_id": pack_id, "pack_sha256": digest, "scope": scope}
                return response(session, "Unverified draft and complete uncertainty history recovered only; no image, candidates, scores or approval.",
                    clear_image=True, values={4: _display(scope), 6: value["draft"]["reference"], 7: value["draft"]["critical_tokens_text"]})
        except _Stale:
            raise gr.Error(_STALE) from None
        except Exception:
            with session.lock:
                if generation is None or session.generation != generation:
                    raise gr.Error(_STALE) from None
                session.revoke(clear_view=True)
                return response(session, _READ_FAILED, clear_image=True, clear_text=True)

    def archive_reference_edited(session, token, reference, critical, action_token, image_token):
        with session.lock:
            try:
                require_edit(session, token, action_token, image_token)
            except _Stale:
                return skips()
            try:
                if _raw_reference_digest(reference, critical) == session.reference_digest:
                    return skips()
                raw_edit(session, reference, critical)
            except Exception:
                session.uncertainty = authoring.dirty_uncertainty(session.uncertainty)
                session.reference_digest = None
                session.revision += 1
            touch(session)
            return response(session, "Raw reference edited. Anchors/resolutions are reset in the dirty draft, including edits away and back. Apply or prepare an explicit revision.", captured_image=image_token)

    def editor_input(session, token, action_token, image_token, value):
        with session.lock:
            try:
                loaded = require_edit(session, token, action_token, image_token, full=True)
                if (type(value) is not dict or any(type(value.get(key)) is not str or value[key] != expected
                        for key, expected in (("image_token", session.image_token), ("action_token", action_token), ("mode_token", session.mode_token)))):
                    raise _Stale()
            except _Stale:
                return skips()
            try:
                command = overlay.admit_editor(value, image_token=session.image_token,
                    action_token=action_token, mode_token=session.mode_token)
            except (ValueError, TypeError):
                touch(session)
                return response(session, "Invalid current annotation command; approval cleared. No annotation was recorded.", captured_image=image_token)
            if command is None:
                return skips()
            try:
                kind = command["kind"]
                if kind == "mode":
                    if command["mode"] == session.mode:
                        return skips()
                    session.mode = command["mode"]
                    session.mode_token = uuid4().hex
                    session.selection = None
                else:
                    if session.mode != "annotate":
                        raise ValueError()
                    if kind in ("rectangle", "whole_scope"):
                        selected = {"kind": "raster_edges" if kind == "rectangle" else "whole_scope", "pixel_bbox": command["pixel_bbox"]}
                        selection_to_scope_bbox(selected, raster_view=loaded["raster_view"], scope=loaded["scope"])
                        session.selection = selected
                    elif kind == "annotation":
                        rows = session.uncertainty["annotations"]
                        if command["ordinal"] > len(rows):
                            raise ValueError()
                        session.annotation_id = rows[command["ordinal"] - 1]["annotation_id"]
                        session.selection = None
                    else:
                        session.selection = None
                touch(session)
                return response(session, "Selection/mode acknowledged only. Apply an explicit action to record history; approval cleared.", captured_image=image_token)
            except Exception:
                touch(session)
                return response(session, "Selection is outside the declared source crop or invalid. No annotation was recorded.", captured_image=image_token)

    def form_bytes(selected, form):
        if len(form) != 10:
            raise ValueError()
        return hashlib.sha256(_bytes([selected, *form], 64 * 1024)).hexdigest()

    def annotation_form_edited(session, token, action_token, image_token, selected, *form):
        with session.lock:
            try:
                require_edit(session, token, action_token, image_token)
            except _Stale:
                return skips()
            try:
                raw = form_bytes(selected, form)
                if raw == session.form_digest:
                    return skips()
                if selected is not None and selected not in {row["annotation_id"] for row in session.uncertainty["annotations"]}:
                    raise ValueError()
                session.annotation_id, session.form_digest = selected, raw
            except Exception:
                session.form_digest = None
            touch(session)
            return response(session, "Annotation form changed; not applied. Current review and one-use save authority were cleared.", captured_image=image_token)

    def admit(session, token, pack_id, action_token, image_token, profile, *, full=True):
        with session.lock:
            loaded = copy.deepcopy(require(session, token, pack_id, action=action_token,
                                           image=image_token, profile=profile, full=full))
            if loaded is None:
                raise _Stale()
            touch(session)
            session.edit_lease = (token, action_token, image_token, session.action_token, session.loaded, session.mode_token)
            return loaded, session.action_token

    def still(session, token, pack_id, loaded, action, image_token, *, full=True):
        require(session, token, pack_id, action=action, image=image_token,
                profile=loaded["preview_profile"], full=full)
        if session.loaded != loaded:
            raise _Stale()

    def author(session, pack_id, token, reference, critical, action_token, profile, image_token, reason, commands, *, prepare, form):
        loaded = action = None
        try:
            loaded, action = admit(session, token, pack_id, action_token, image_token, profile)
            reference, entries, content = _reference(reference, critical)
            form_content = form_bytes(form[0], form[1:])
            with session.lock:
                still(session, token, pack_id, loaded, action, image_token)
                if prepare and session.selection is not None:
                    raise ValueError("pending selection")
                raw_edit(session, reference, critical)
                value = copy.deepcopy(session.uncertainty)
            args = authoring.author_arguments(value, reference=reference, critical_tokens=entries,
                scope=loaded["scope"], raster_view=loaded["raster_view"], commands=commands, reason=reason)
            result = service.author_v2(pack_id, expected_pack_sha256=loaded["pack_sha256"], **args)
            if result["pack_sha256"] != loaded["pack_sha256"]:
                raise ValueError()
            installed = authoring.install_author_result(result, scope=loaded["scope"])
            policy = authoring.prepare_policy(installed, reference=reference, critical_tokens=entries) if prepare else None
            with session.lock:
                still(session, token, pack_id, loaded, action, image_token)
                if session.reference_digest != content:
                    raise _Stale()
                session.uncertainty = installed
                session.selection = None
                session.form_digest = form_content
                values = {}
                if prepare:
                    ticket = uuid4().hex
                    session.pending = {"ticket": ticket, "content": content, "uncertainty": authoring.uncertainty_digest(installed),
                        "image_token": image_token, "action_token": action, "loaded": loaded, "mode_token": session.mode_token,
                        "form": form_content}
                    values = {8: ticket, 10: _display({"declaration": result["declaration"], "policy": policy,
                        "raster_view": loaded["raster_view"], "notice": "Confirm complete source-bound declaration, including unresolved regions."}, _LIMIT)}
                finish_action(session, action)
                return response(session, "Prepared unchecked declaration; unresolved source regions make the whole crop unscorable." if prepare
                    else "Explicit annotation/reference revision recorded. No score or approval was created.", captured_image=image_token, values=values)
        except _Stale:
            raise gr.Error(_STALE) from None
        except Exception as error:
            with session.lock:
                if loaded is None:
                    raise gr.Error(_STALE) from None
                still(session, token, pack_id, loaded, action, image_token)
                message = str(error) if type(error) is _ReferenceFieldError else "Action not applied: check the explicit reason, pending selection, annotation action and reading. Draft/history remain; correct and try explicitly."
                finish_action(session, action)
                return response(session, message, captured_image=image_token)
        finally:
            finish_action(session, action)

    def prepare_archive_reference(session, pack_id, token, reference, critical, action_token, profile, image_token, selected, *form):
        # Parsing the captured form happens only after current-view admission.
        with session.lock:
            require(session, token, pack_id, action=action_token, image=image_token, profile=profile, full=True)
        return author(session, pack_id, token, reference, critical, action_token, profile, image_token,
                      form[-1] if len(form) == 10 else None, [], prepare=True, form=(selected, *form))

    def apply(session, pack_id, token, reference, critical, action_token, profile, image_token,
              selected, command, kind, tentative_present, tentative, decision, reading, anchored, start, end, reason):
        try:
            with session.lock:
                require(session, token, pack_id, action=action_token, image=image_token, profile=profile, full=True)
                if session.mode != "annotate" or selected != session.annotation_id:
                    raise _Stale()
                if type(command) is not str or command not in ("add", "move", "reclassify", "edit_tentative", "anchor", "resolve", "reopen", "dismiss"):
                    raise ValueError()
                if type(tentative_present) is not bool or type(anchored) is not bool:
                    raise ValueError()
                item = {"action": command}
                if command != "add":
                    item["annotation_id"] = selected
                if command in ("add", "move"):
                    item["selection"] = copy.deepcopy(session.selection)
                if command in ("add", "reclassify"):
                    item["kind"] = kind
                if command in ("add", "edit_tentative"):
                    item["tentative_text"] = tentative if tentative_present else None
                if command in ("anchor", "resolve"):
                    item["span"] = [start, end] if anchored else None
                if command == "resolve":
                    item.update(decision=decision, reading=reading)
            return author(session, pack_id, token, reference, critical, action_token, profile, image_token, reason, [item], prepare=False,
                form=(selected, command, kind, tentative_present, tentative, decision, reading, anchored, start, end, reason))
        except _Stale:
            raise gr.Error(_STALE) from None
        except Exception:
            with session.lock:
                require(session, token, pack_id, action=action_token, image=image_token, profile=profile, full=True)
                touch(session)
                return response(session, "Invalid annotation form. No action recorded; correct the current form.", captured_image=image_token)

    def score_archive_reference(session, pack_id, token, reference, critical, ticket, confirmed, action_token, profile, image_token, selected, *form):
        loaded = action = None
        try:
            with session.lock:
                require(session, token, pack_id, action=action_token, image=image_token, profile=profile, full=True)
                pending = session.pending
                if pending is not None and (type(ticket) is not str or ticket != pending["ticket"] or pending["action_token"] != action_token):
                    raise _Stale()
                loaded, action = admit(session, token, pack_id, action_token, image_token, profile)
                if confirmed is not True or pending is None:
                    raise _Stale()
            reference, entries, content = _reference(reference, critical)
            form_content = form_bytes(selected, form)
            with session.lock:
                still(session, token, pack_id, loaded, action, image_token)
                if (pending["loaded"] != loaded or pending["image_token"] != image_token
                        or pending["mode_token"] != session.mode_token or pending["content"] != content
                        or pending["form"] != form_content
                        or pending["uncertainty"] != authoring.uncertainty_digest(session.uncertainty)):
                    raise _Stale()
                value = copy.deepcopy(session.uncertainty)
            authoring.prepare_policy(value, reference=reference, critical_tokens=entries)
            result = service.compare_v2(pack_id, expected_pack_sha256=loaded["pack_sha256"], journal=value["journal"], confirmed=True)
            _bytes(result, _LIMIT)
            _fields(result, {"pack_sha256", "reference", "comparison", "requires_attention", "canonical_extraction_modified"})
            if (result["pack_sha256"] != loaded["pack_sha256"] or result["requires_attention"] is not True
                    or result["canonical_extraction_modified"] is not False or result["reference"]["journal"] != value["journal"]
                    or type(result["reference"]["schema_version"]) is not int or result["reference"]["schema_version"] != 2
                    or result["reference"]["kind"] != "ocr_crop_reference"
                    or result["reference"]["annotations"] != value["annotations"] or result["reference"]["reference"] != reference
                    or result["reference"]["critical_tokens"] != entries or result["reference"]["scope"] != loaded["scope"]
                    or result["comparison"]["source_sha256"] != binding[0] or result["comparison"]["scope"] != loaded["scope"]):
                raise ValueError()
            metrics = result["comparison"]
            if (type(metrics["schema_version"]) is not int or metrics["schema_version"] != 2
                    or metrics["kind"] != "ocr_crop_comparison" or type(metrics["page_count"]) is not int
                    or metrics["page_count"] != binding[2] or metrics["requires_attention"] is not True
                    or metrics["canonical_extraction_modified"] is not False
                    or metrics["reference_id"] != result["reference"]["reference_id"]
                    or metrics["reference_sha256"] != _sha(result["reference"]["reference_sha256"])
                    or any(metrics["pair"][side][key] != loaded["pair"][side][key]
                           for side in ("baseline", "retry") for key in ("report_sha256", "region_id"))):
                raise ValueError()
            reviewed = {"reference": result["reference"], "comparison": result["comparison"], "source_image": loaded["raster_view"]}
            with session.lock:
                still(session, token, pack_id, loaded, action, image_token)
                if content != session.reference_digest or value != session.uncertainty:
                    raise _Stale()
                session.last_score = {"content": content, "uncertainty": authoring.uncertainty_digest(value),
                    "loaded": loaded, "action_token": action, "reviewed": copy.deepcopy(reviewed), "form": form_content}
                finish_action(session, action)
                return response(session, "Fresh reviewed declaration retained once for explicit Save. Unresolved references remain unscorable; nothing adopted.",
                    captured_image=image_token, values={11: _display(result["comparison"], _LIMIT)})
        except _Stale:
            raise gr.Error(_STALE) from None
        except Exception as error:
            with session.lock:
                if loaded is None:
                    raise gr.Error(_STALE) from None
                still(session, token, pack_id, loaded, action, image_token)
                finish_action(session, action)
                return response(session, str(error) if type(error) is _ReferenceFieldError else _FAILED, captured_image=image_token)
        finally:
            finish_action(session, action)

    def save(session, pack_id, token, reference, critical, action_token, profile, image_token, selected, form, *, reviewed):
        action = None
        try:
            with session.lock:
                require(session, token, pack_id, action=action_token, image=image_token, profile=profile, full=reviewed)
                score = session.last_score
                loaded, action = admit(session, token, pack_id, action_token, image_token, profile, full=reviewed)
                content = _raw_reference_digest(reference, critical)
                form_content = form_bytes(selected, form)
                if reviewed:
                    if (score is None or score["action_token"] != action_token or score["loaded"] != loaded
                            or score["content"] != content or score["form"] != form_content
                            or score["uncertainty"] != authoring.uncertainty_digest(session.uncertainty)):
                        raise _Stale()
                else:
                    raw_edit(session, reference, critical)
                value = copy.deepcopy(session.uncertainty)
                declaration = json.loads(_bytes(score["reviewed"], _LIMIT)) if reviewed else None

            def verify_current():
                with session.lock:
                    still(session, token, pack_id, loaded, action, image_token, full=reviewed)
                    if session.reference_digest != content or session.uncertainty != value:
                        raise _Stale()
                return True

            # Retain the old displayed edit lease, but send no new capability.
            yield (*skips(), SAVE_PENDING)
            verify_current()
            result = service.save_revision_v2(pack_id, expected_pack_sha256=loaded["pack_sha256"], reference=reference,
                critical_tokens_text=critical, uncertainty=value, reviewed=declaration,
                verify_current=verify_current)
            result = _metadata(result)
            if result["status"] != "verified_complete" or result["manifest_sha256"] is None:
                raise ValueError()
            verify_current()
            with session.lock:
                still(session, token, pack_id, loaded, action, image_token, full=reviewed)
                finish_action(session, action)
                message = "New immutable v2 revision saved. Refresh explicitly; no restored approval or automatic adoption."
                terminal = (*response(session, message, captured_image=image_token), message)
        except _Stale:
            terminal = (*skips(), SAVE_UNCONFIRMED)
        except Exception:
            terminal = (*skips(), SAVE_UNCONFIRMED)
        finally:
            finish_action(session, action)
        yield terminal

    def save_archive_draft(session, pack_id, token, reference, critical, action_token, profile, image_token, selected, *form):
        yield from save(session, pack_id, token, reference, critical, action_token, profile, image_token, selected, form, reviewed=False)

    def save_archive_reviewed(session, pack_id, token, reference, critical, action_token, profile, image_token, selected, *form):
        yield from save(session, pack_id, token, reference, critical, action_token, profile, image_token, selected, form, reviewed=True)

    with gr.Tab("Saved crop review packs"):
        gr.Markdown("Explicit uncertainty-aware review. Historical declarations are not approval. Unknown source text makes the whole crop unscorable; no guessed partial accuracy.")
        state = gr.State(_UncertaintyArchiveState())
        view = gr.Textbox(value="", visible=False, interactive=False)
        action_view = gr.Textbox(value="", visible=False, interactive=False)
        image_ticket = gr.Textbox(value="", visible=False, interactive=False)
        refresh = gr.Button("Refresh saved crop catalog")
        catalog = gr.Dropdown(choices=[], value=None, label="Saved crop entry", allow_custom_value=False)
        with gr.Row():
            open_button = gr.Button("Open pack and fresh original crop")
            recover = gr.Button("Recover authored draft and uncertainty only")
            cancel = gr.Button("Cancel archive preview and clear approval")
        profile = gr.Dropdown(choices=PREVIEW_CHOICES, value="fit", allow_custom_value=False, label="Original archive crop detail")
        reload_button = gr.Button("Reload archive detail, keeping my draft and history")
        gr.Markdown("Fresh original requested crop. Detail changes require explicit reload; saved source rectangles are never re-authored by zooming.")
        image = gr.Image(label="Fresh original crop", show_label=False, type="pil", format="png", interactive=False, sources=[], buttons=[], elem_id=ARCHIVE_PREVIEW_ID)
        editor = overlay.build_uncertainty_editor(ARCHIVE_PREVIEW_ID)
        mode = gr.Textbox(value="browse", label="Admitted image mode", interactive=False)
        with gr.Row():
            baseline = gr.Textbox(label="Historical baseline OCR", interactive=False)
            retry = gr.Textbox(label="Historical retry OCR", interactive=False)
        details = gr.Textbox(label="Archive source, raster declarations and nonexecuting source advice", interactive=False)
        historical = gr.Textbox(label="Historical comparison — NOT approval", interactive=False)
        reference = gr.Textbox(value="", label="Your transcription", max_length=20_000, lines=5)
        critical = gr.Textbox(value="", label="Critical entries (one literal nonempty entry per line)", max_length=64 * 257, lines=3)
        annotation_summary = gr.Textbox(label="Committed annotations and pending selection", interactive=False)
        selector = gr.Dropdown(choices=[], value=None, label="Annotation to change", allow_custom_value=False)
        command = gr.Dropdown(choices=["add", "move", "reclassify", "edit_tentative", "anchor", "resolve", "reopen", "dismiss"], value="add", label="Explicit annotation action", allow_custom_value=False)
        kind = gr.Dropdown(choices=["uncertain", "illegible"], value="uncertain", label="Annotation kind", allow_custom_value=False)
        tentative_present = gr.Checkbox(value=False, label="Record tentative text (not reference truth)")
        tentative = gr.Textbox(value="", label="Tentative text", max_length=2_000)
        decision = gr.Dropdown(choices=["reading_confirmed", "not_text"], value="reading_confirmed", label="Resolution decision", allow_custom_value=False)
        reading = gr.Textbox(value="", label="Confirmed reading (must occur in transcription); leave empty for not_text", max_length=20_000)
        anchored = gr.Checkbox(value=False, label="Explicit raw transcription span (optional; Unicode codepoints, not browser UTF-16)")
        with gr.Row():
            start = gr.Number(value=0, precision=0, label="Span start (inclusive)")
            end = gr.Number(value=0, precision=0, label="Span end (exclusive)")
        reason = gr.Textbox(value="", label="Explicit reason for this revision", max_length=512)
        apply_button = gr.Button("Apply annotation action and record revision")
        prepare = gr.Button("Prepare exact archive declaration (raw text only)")
        pending = gr.Textbox(value="", visible=False, interactive=False)
        confirmed = gr.Checkbox(value=False, label="I reviewed this original source and complete declaration, including unresolved areas")
        prepared = gr.Textbox(label="Prepared fresh declaration and scoring policy", interactive=False)
        score = gr.Button("Review confirmed declaration once (score only if resolved)")
        metrics = gr.Textbox(label="Fresh same-crop comparison or explicit unscorable coverage", interactive=False)
        with gr.Row():
            save_draft = gr.Button("Save new v2 draft revision")
            save_reviewed = gr.Button("Save new v2 reviewed revision once")
        save_notice, save_progress, save_requested = build_save_feedback("archive")
        notice = gr.Textbox(label="Archive status", interactive=False)

    outputs = [view, image, baseline, retry, details, historical, reference, critical, pending, confirmed,
               prepared, metrics, action_view, notice, image_ticket, editor, annotation_summary, selector, mode]
    private = {"api_visibility": "private", "show_progress": "hidden"}
    quick = {**private, "queue": False, "trigger_mode": "multiple"}
    slow = {**private, "concurrency_id": "ocr-crop-archive-review", "concurrency_limit": 1}
    common = [state, catalog, view, reference, critical]
    current = [*common, action_view, profile, image_ticket]
    form = [command, kind, tentative_present, tentative, decision, reading, anchored, start, end, reason]
    refresh.click(refresh_crop_archives, [state], [catalog, *outputs], **slow)
    catalog.input(select_crop_archive, [state, catalog, view], outputs, **quick)
    profile.input(archive_preview_profile_changed, [state, view, profile], outputs, **quick)
    cancel.click(cancel_archive_preview, [state, profile], outputs, **quick)
    open_button.click(open_crop_archive, [*common, profile], outputs, **slow)
    reload_button.click(reload_archive_detail, [*common, profile], outputs, **slow)
    recover.click(recover_crop_archive_draft, [state, catalog, view], outputs, **slow)
    for component in (reference, critical):
        component.change(archive_reference_edited, [state, view, reference, critical, action_view, image_ticket], outputs, **quick)
    editor.input(editor_input, [state, view, action_view, image_ticket, editor], outputs, **quick)
    # Server responses also update this selector. Only an operator selection
    # should invoke the form invalidator and replace the current action notice.
    selector.input(annotation_form_edited, [state, view, action_view, image_ticket, selector, *form], outputs, **quick)
    for component in form:
        component.change(annotation_form_edited, [state, view, action_view, image_ticket, selector, *form], outputs, **quick)
    apply_button.click(apply, [*current, selector, *form], outputs, **slow)
    prepare.click(prepare_archive_reference, [*current, selector, *form], outputs, **slow)
    score.click(score_archive_reference, [*common, pending, confirmed, action_view, profile, image_ticket, selector, *form], outputs, **slow)
    save_outputs = [*outputs, save_notice]
    save_events = {**slow, "show_progress": "full", "show_progress_on": [save_progress], "js": save_requested}
    save_draft.click(save_archive_draft, [*current, selector, *form], save_outputs, **save_events)
    save_reviewed.click(save_archive_reviewed, [*current, selector, *form], save_outputs, **save_events)
