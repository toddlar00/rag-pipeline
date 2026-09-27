"""Private, explicit same-crop comparison controls for a fixed review host.

Default session state contains only bounded identities/revisions. Explicit pack
saving additionally retains one bounded successful comparison declaration until
revocation or one save attempt, never images, raw archives or execution tickets.
Tokens reject stale submissions and in-flight work before return;
they are neither human-review proof nor a browser transport-order guarantee.
"""
from __future__ import annotations

import hashlib
import json
import re
from uuid import uuid4

from ocr_crop_comparison import _bytes, validate_crop_scope
from ocr_crop_review_runtime import CropPreviewError, validate_preview_profile
from ocr_review_crop_preview_ui import LIVE_PREVIEW_ID, PREVIEW_CHOICES, preview_image_update
from ocr_review_crop_ui_common import (
    _ID as _ID, _LIMIT, _PREVIEW_NOTICES, _SHA, _CropState, _ReferenceFieldError,
    _SaveCropState, _Stale, _close_unreturned_image, _context, _display, _hash as _hash,
    _id, _image_identity, _raw_reference_digest, _reference, _sha, _text,
)


_REGION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_STALE = "Crop review changed. Select or load the current pair, then prepare and confirm the reference again."
_FAILED = "Crop review is unavailable or changed. Retained runs and the saved extraction are unchanged."


def build_crop_review_panel(workspace, coordinator, *, execution_state, review_state, annotation_view,
                            runs, items, result_context, controls=(), pack_service=None, uncertainty=False):
    """Build inside the execution Tab; only fixed host-owned capabilities enter."""
    if type(uncertainty) is not bool:
        raise ValueError("invalid uncertainty UI selection")
    if uncertainty:
        from ocr_review_crop_uncertainty_live_ui import build_uncertainty_live_panel

        return build_uncertainty_live_panel(workspace, coordinator, execution_state=execution_state,
            review_state=review_state, annotation_view=annotation_view, runs=runs, items=items,
            result_context=result_context, controls=controls, pack_service=pack_service)
    import gradio as gr

    def labels(session):
        def label(pin):
            return "Not captured" if pin is None else f"{pin['run_id']} / {pin['item_id']} / {pin['operation']}"
        return label(session.baseline), label(session.retry)

    def cleared(session, notice, *, preserve_draft=False):
        if not preserve_draft:
            session.reference_digest = _raw_reference_digest("", "")
        fields = (gr.skip(), gr.skip()) if preserve_draft else ("", "")
        return (session.generation, *labels(session), None, "", "", "", *fields, "", False, "", "", notice)

    def require_view(session, token):
        if type(token) is not str or token != session.generation:
            raise _Stale()

    def require_profile(session, profile):
        if validate_preview_profile(profile) != session.preview_profile:
            raise _Stale()

    def crop_preview_profile_changed(session, token, profile):
        with session.lock:
            try:
                require_view(session, token)
            except _Stale:
                return tuple(gr.skip() for _ in range(14))
            try:
                profile = validate_preview_profile(profile)
            except Exception:
                session.revoke(clear_view=True, clear_draft=False)
                return cleared(session, "Preview detail selection is invalid. Cancel to resync a valid selection; authored text remains.", preserve_draft=True)
            if profile == session.preview_profile:
                return tuple(gr.skip() for _ in range(14))
            session.preview_profile = profile
            session.revoke(clear_view=True, clear_draft=False)
            return cleared(session, "Detail selection changed; no render was started. Reload detail explicitly. Authored text remains; approval was cleared.", preserve_draft=True)

    def permitted(execution, run_id):
        _id(run_id)
        with execution.lock:
            if run_id not in execution.runs:
                raise _Stale()

    def selection(execution, run_id, item_id, context):
        permitted(execution, run_id)
        _id(item_id)
        with execution.lock:
            if execution.display_run != run_id or context != execution.result_generation:
                raise _Stale()
        return run_id, item_id, context

    def crop_context_changed(session):
        with session.lock:
            session.revoke(clear_view=True)
            session.selected = None
            return cleared(session, "Context changed. Captured crop identities remain; load their pair again.")

    def cancel_crop_preview(session, preview_profile="fit"):
        # Per-view revocation, not a host-wide shutdown or process-ID command.
        # The running worker's cancellation callback observes this generation.
        with session.lock:
            session.revoke(clear_view=True, clear_draft=False)
            try:
                session.preview_profile = validate_preview_profile(preview_profile)
            except Exception:
                return cleared(session, "Preview cancelled; choose a valid detail profile and cancel again to resync. Authored text remains.", preserve_draft=True)
            return cleared(session, "Preview cancellation requested; any displayed review approval was cleared. "
                           "Authored text remains. Detail selection is resynced; load or reload explicitly. Cancellation is not proof of worker cleanup.", preserve_draft=True)

    def crop_select_item(session, execution, run_id, item_id, context):
        with session.lock:
            session.revoke(clear_view=True)
            session.selected = None
            try:
                session.selected = selection(execution, run_id, item_id, context)
                notice = "Current item selected. Capture it explicitly as a baseline or retry crop."
            except Exception:
                notice = "Select a current verified crop item before capturing it."
            return cleared(session, notice)

    def result_pin(result, run_id, item_id):
        _bytes(result, _LIMIT)
        if (type(result) is not dict or result.get("run_id") != run_id
                or result.get("operation") not in ("regions", "hardscan")
                or result.get("source_sha256") != workspace.document.source_sha256
                or result.get("baseline_recovery_sha256") != workspace.document.recovery_sha256
                or result.get("requires_attention") is not True):
            raise ValueError("not a bound crop result")
        selected = result["selected_item"]
        diagnostic, record = selected["diagnostic"], selected["report_record"]
        if (diagnostic["item_id"] != item_id or type(record) is not dict
                or record["region_id"] != diagnostic["region_id"]
                or record["page_number"] != diagnostic["page_number"]
                or record["status"] != diagnostic["legacy_status"]):
            raise ValueError("selected crop record differs")
        if type(record["region_id"]) is not str or _REGION.fullmatch(record["region_id"]) is None:
            raise ValueError("invalid region identifier")
        pin = {"run_id": run_id, "item_id": item_id, "operation": result["operation"],
               "region_id": record["region_id"]}
        pin.update({key: _sha(result[key]) for key in
                    ("request_sha256", "report_sha256", "execution_sha256", "disposition_sha256")})
        return pin

    def capture(session, execution, run_id, item_id, context, token, side):
        generation = None
        try:
            chosen = selection(execution, run_id, item_id, context)
            with session.lock:
                require_view(session, token)
                if session.selected != chosen:
                    raise _Stale()
                session.revoke(clear_view=True)
                setattr(session, side, None)
                generation = session.generation
            pin = result_pin(coordinator.result(run_id, item_id=item_id), run_id, item_id)
            selection(execution, run_id, item_id, context)
            with session.lock:
                require_view(session, generation)
                if session.selected != chosen:
                    raise _Stale()
                setattr(session, side, pin)
                return cleared(session, f"{side.title()} crop captured by fresh artifact verification. Load the pair to inspect it.")
        except _Stale:
            raise gr.Error(_STALE) from None
        except Exception:
            with session.lock:
                if generation is not None and session.generation != generation:
                    raise gr.Error(_STALE) from None
                session.revoke(clear_view=True)
                return cleared(session, _FAILED)

    def capture_baseline_crop(session, execution, run_id, item_id, context, token):
        return capture(session, execution, run_id, item_id, context, token, "baseline")

    def capture_retry_crop(session, execution, run_id, item_id, context, token):
        return capture(session, execution, run_id, item_id, context, token, "retry")

    def pair_pins(session, execution):
        if session.baseline is None or session.retry is None:
            raise ValueError("capture two crop occurrences first")
        for pin in (session.baseline, session.retry):
            permitted(execution, pin["run_id"])
        return dict(session.baseline), dict(session.retry)

    def pair_arguments(pins):
        return pins[0]["run_id"], pins[0]["item_id"], pins[1]["run_id"], pins[1]["item_id"]

    def checked_pair(pair, pins):
        _bytes(pair, _LIMIT)
        if (type(pair) is not dict or pair.get("source_sha256") != workspace.document.source_sha256
                or pair.get("baseline_recovery_sha256") != workspace.document.recovery_sha256
                or pair.get("requires_attention") is not True or pair.get("canonical_extraction_modified") is not False
                or pair.get("reference_status") != "not_supplied"):
            raise ValueError("invalid crop pair")
        digest = _sha(pair["pair_sha256"])
        # Coordinator pair identities use its compact private-writer format,
        # including the final LF; nominal scope hashes intentionally do not.
        pair_bytes = _bytes({key: value for key, value in pair.items() if key != "pair_sha256"}, _LIMIT - 1)
        if hashlib.sha256(pair_bytes + b"\n").hexdigest() != digest:
            raise ValueError("pair digest differs")
        scope = validate_crop_scope(pair["scope"], source_sha256=workspace.document.source_sha256,
                                    page_count=workspace.document.page_count)
        for side, pin in zip(("baseline", "retry"), pins):
            if any(pair[side].get(key) != value for key, value in pin.items()):
                raise ValueError("captured crop generation changed")
            status, text = pair[side]["status"], pair[side]["text"]
            if status not in ("review_required", "empty_candidate", "retry_failed", "abstained"):
                raise ValueError("invalid crop outcome")
            if text is not None:
                _text(text)
            if (text is not None) != (status in ("review_required", "empty_candidate")):
                raise ValueError("crop availability differs")
        return scope

    def load_pair_view(session, execution, state, annotation, token, preview_profile, values, *, draft=None):
        generation = None
        image, transferred = None, False
        try:
            current_context = _context(workspace, state, annotation, values, execution)
            with session.lock:
                require_view(session, token)
                require_profile(session, preview_profile)
                pins = pair_pins(session, execution)
                marker = session.draft_scope
                if draft is not None:
                    content = _raw_reference_digest(*draft)
                    if marker is None or marker["pins"] != pins or marker["context"] != current_context:
                        raise _Stale()
                session.revoke(clear_view=True, clear_draft=draft is None)
                if draft is not None:
                    session.reference_digest = content
                generation = session.generation
            pair = coordinator.crop_pair(*pair_arguments(pins))
            scope = checked_pair(pair, pins)
            if draft is not None and (marker["pair_sha256"] != pair["pair_sha256"] or marker["scope_sha256"] != scope["scope_sha256"]):
                raise ValueError("detail reload crop binding changed")

            def preview_cancelled():
                with session.lock:
                    return session.generation != generation

            image = coordinator.render_crop_preview(scope, preview_profile=preview_profile, cancel_requested=preview_cancelled)
            image_identity = _image_identity(image)
            fresh = coordinator.crop_pair(*pair_arguments(pins))
            checked_pair(fresh, pins)
            if fresh["pair_sha256"] != pair["pair_sha256"]:
                raise ValueError("pair changed during original preview")
            with session.lock:
                require_view(session, generation)
                require_profile(session, preview_profile)
                if (pair_pins(session, execution) != pins
                        or _context(workspace, state, annotation, values, execution) != current_context):
                    raise _Stale()
                session.loaded = {"pair_sha256": pair["pair_sha256"], "scope_sha256": scope["scope_sha256"],
                    "image": image_identity, "pins": pins, "context": current_context, "preview_profile": preview_profile}
                session.draft_scope = {key: session.loaded[key] for key in ("pair_sha256", "scope_sha256", "pins", "context")}
                if draft is None:
                    session.reference_digest = _raw_reference_digest("", "")
                details = _display({"scope": scope, "pair_sha256": pair["pair_sha256"],
                    "preview_profile": preview_profile,
                    "original_preview": {"width": image.width, "height": image.height, "rgb_binding": image_identity[2]},
                    **{side: {key: value for key, value in pair[side].items() if key != "text"}
                       for side in ("baseline", "retry")}})
                states = "; ".join(f"{side}: {pair[side]['status']}" for side in ("baseline", "retry"))
                fields = (gr.skip(), gr.skip()) if draft is not None else ("", "")
                output = (session.generation, *labels(session), preview_image_update(image, preview_profile), pair["baseline"]["text"] or "",
                    pair["retry"]["text"] or "", details, *fields, "", False, "", "",
                    states + ". Same requested original crop only; " + ("authored text preserved. Prepare and confirm again." if draft is not None else "enter your own reference below. No accuracy score yet."))
            # The completed output and session-lock exit precede ownership
            # transfer; Image.postprocess still needs this exact usable image.
            transferred = True
            return output
        except _Stale:
            raise gr.Error(_STALE) from None
        except Exception as error:
            with session.lock:
                if generation is not None and session.generation != generation:
                    raise gr.Error(_STALE) from None
                session.revoke(clear_view=True, clear_draft=draft is None)
                notice = (_PREVIEW_NOTICES.get(error.code, _PREVIEW_NOTICES["preview_unavailable"])
                          if type(error) is CropPreviewError else _FAILED)
                return cleared(session, notice, preserve_draft=draft is not None)
        finally:
            if not transferred:
                _close_unreturned_image(image)

    def load_crop_pair(session, execution, state, annotation, token, preview_profile, *values):
        return load_pair_view(session, execution, state, annotation, token, preview_profile, values)

    def reload_crop_detail(session, execution, state, annotation, token, preview_profile, reference, critical, *values):
        return load_pair_view(session, execution, state, annotation, token, preview_profile, values, draft=(reference, critical))

    def crop_reference_edited(session, captured_view, raw_reference, raw_critical):
        with session.lock:
            try:
                require_view(session, captured_view)
            except _Stale:
                return tuple(gr.skip() for _ in range(6))
            try:
                content = _raw_reference_digest(raw_reference, raw_critical)
            except Exception:
                session.revoke(clear_view=False)
                session.reference_digest = None
                return (session.generation, "", False, "", "",
                        "Reference fields are invalid or too large. Correct them, then prepare and confirm again.")
            if content == session.reference_digest:
                return tuple(gr.skip() for _ in range(6))
            session.revoke(clear_view=False)
            session.reference_digest = content
            return session.generation, "", False, "", "", "Reference revision changed. Prepare and confirm it again."

    def loaded_view(session, execution, state, annotation, token, values, preview_profile):
        require_view(session, token)
        require_profile(session, preview_profile)
        loaded = session.loaded
        if (loaded is None or loaded["preview_profile"] != preview_profile
                or loaded["context"] != _context(workspace, state, annotation, values, execution)
                or loaded["pins"] != pair_pins(session, execution)):
            raise _Stale()
        return loaded

    def prepare_crop_reference(session, execution, state, annotation, token, preview_profile, reference, critical, *values):
        try:
            with session.lock:
                loaded = loaded_view(session, execution, state, annotation, token, values, preview_profile)
                if pack_service is not None:
                    # Client-captured action generations distinguish two
                    # preparations even on the same image with identical text.
                    session.revoke(clear_view=False)
                session.pending = None
                generation, revision = session.generation, session.revision
                try:
                    reference, entries, content = _reference(reference, critical)
                except _ReferenceFieldError as error:
                    loaded_view(session, execution, state, annotation, generation, values, preview_profile)
                    if session.revision != revision or session.pending is not None:
                        raise _Stale()
                    session.reference_digest = None
                    output = ("", False, "", "", str(error))
                    return output if pack_service is None else (*output, generation)
                # A delayed change for these exact already-prepared fields
                # must not revoke its new ticket. No raw text is retained.
                session.reference_digest = content
                prepared = _display({"pair_sha256": loaded["pair_sha256"], "source_sha256": workspace.document.source_sha256,
                    "scope_sha256": loaded["scope_sha256"], "image": loaded["image"], "reference": reference,
                    "critical_entries": entries, "edit_revision": session.revision, "preview_profile": preview_profile,
                    "review": "operator declaration only; one requested crop, no canonical adoption"}, 256 * 1024)
                ticket = uuid4().hex
                session.pending = (ticket, session.generation, session.revision, content, loaded["pair_sha256"],
                                   tuple(loaded["image"]))
                output = (ticket, False, prepared, "",
                          "Inspect this exact reference revision and original crop before confirming.")
                return output if pack_service is None else (*output, session.generation)
        except _Stale:
            raise gr.Error(_STALE) from None
        except Exception:
            raise gr.Error(_FAILED) from None

    def score_crop_reference(session, execution, state, annotation, token, preview_profile, reference, critical,
                             prepared_token, confirmed, *values):
        try:
            with session.lock:
                loaded = dict(loaded_view(session, execution, state, annotation, token, values, preview_profile))
                pending = session.pending
                if (confirmed is not True or type(prepared_token) is not str or pending is None
                        or pending != (prepared_token, session.generation, session.revision, pending[3],
                                       loaded["pair_sha256"], tuple(loaded["image"]))):
                    raise _Stale()
                # Admit exact current authority before classifying field errors.
                # A malformed obsolete request must not erase a newer ticket.
                try:
                    reference, entries, content = _reference(reference, critical)
                except _ReferenceFieldError as error:
                    loaded_view(session, execution, state, annotation, token, values, preview_profile)
                    if session.pending is not pending:
                        raise _Stale()
                    session.pending = None
                    if pack_service is not None:
                        session.revoke(clear_view=False)
                    session.reference_digest = None
                    output = ("", False, "", str(error))
                    return output if pack_service is None else (*output, session.generation)
                expected = (prepared_token, session.generation, session.revision, content,
                            loaded["pair_sha256"], tuple(loaded["image"]))
                if confirmed is not True or type(prepared_token) is not str or session.pending != expected:
                    raise _Stale()
                session.pending = None  # One use, including failed or interrupted scoring.
                if pack_service is not None:
                    session.revoke(clear_view=False)
                    save_revision = session.save_revision
                generation, revision = session.generation, session.revision
            result = coordinator.compare_crops(*pair_arguments(loaded["pins"]),
                expected_pair_sha256=loaded["pair_sha256"], reference=reference, critical_tokens=entries, confirmed=True)
            _bytes(result, _LIMIT)
            if (result.get("pair_sha256") != loaded["pair_sha256"] or result.get("requires_attention") is not True
                    or result.get("canonical_extraction_modified") is not False
                    or result["reference"]["reference"] != reference or result["reference"]["critical_tokens"] != entries
                    or result["reference"]["scope"]["scope_sha256"] != loaded["scope_sha256"]
                    or result["comparison"]["source_sha256"] != workspace.document.source_sha256):
                raise ValueError("comparison binding differs")
            metrics = _display(result["comparison"])
            with session.lock:
                loaded_view(session, execution, state, annotation, generation, values, preview_profile)
                if session.revision != revision or session.pending is not None:
                    raise _Stale()
                if pack_service is not None:
                    if session.save_revision != save_revision:
                        raise _Stale()
                    image_width, image_height, image_digest = loaded["image"]
                    declaration = _bytes({"reference": result["reference"], "comparison": result["comparison"],
                        "source_image": {"width": image_width, "height": image_height,
                                         "rgb_sha256": image_digest}}, _LIMIT)
                    session.last_score = (save_binding(session, loaded, content), declaration)
            output = ("", False, metrics, "Reference-backed result for this one requested crop only. "
                "Check unavailable metrics, edge differences and recipe confounders. Confidence is not accuracy; no text was adopted.")
            return output if pack_service is None else (*output, generation)
        except _Stale:
            raise gr.Error(_STALE) from None
        except Exception:
            raise gr.Error(_FAILED) from None

    def save_view_bytes(loaded):
        # The existing loaded view stores its two pins as a tuple; serialize
        # their canonical list form without weakening the JSON-only encoder.
        return _bytes({**loaded, "pins": list(loaded["pins"])}, 32_768)

    def save_binding(session, loaded, content):
        return session.generation, session.revision, content, hashlib.sha256(save_view_bytes(loaded)).hexdigest()

    def save_crop(session, execution, state, annotation, token, preview_profile, reference, critical, values, *, reviewed):
        admitted = False

        def failed(notice):
            if admitted:
                with session.lock:
                    if session.save_revision == save_revision and session.generation == token:
                        try:
                            loaded_view(session, execution, state, annotation, token, values, preview_profile)
                        except Exception:
                            pass
                        else:
                            return "", False, notice
            return gr.skip(), gr.skip(), notice

        try:
            with session.lock:
                if pack_service is None or type(session) is not _SaveCropState:
                    raise _Stale()
                loaded = loaded_view(session, execution, state, annotation, token, values, preview_profile)
                saved = session.last_score
                # Consume save authority even when current submitted text is
                # invalid or storage fails. A stale event never reaches here.
                session.invalidate_save()
                session.pending = None
                admitted = True
                save_revision = session.save_revision
                content = _raw_reference_digest(reference, critical)
                binding = save_binding(session, loaded, content)
                if reviewed and (saved is None or saved[0] != binding):
                    raise _Stale()
                declaration = json.loads(saved[1]) if reviewed else None
                loaded = json.loads(save_view_bytes(loaded))
                marker = session.reference_digest

            def verify_current():
                # Storage owns its lock when invoking this callback. No path
                # below calls storage while holding session/context locks.
                with session.lock:
                    current = loaded_view(session, execution, state, annotation, token, values, preview_profile)
                    if (session.save_revision != save_revision or session.reference_digest != marker
                            or save_binding(session, current, content) != binding):
                        raise _Stale()
                return True

            metadata = pack_service.save_live(coordinator, *pair_arguments(loaded["pins"]),
                expected_pair_sha256=loaded["pair_sha256"], reference=reference,
                critical_tokens_text=critical, reviewed=declaration, verify_current=verify_current)
            verify_current()
            if (type(metadata) is not dict or len(metadata) != 4
                    or any(type(key) is not str for key in metadata) or set(metadata) != {
                    "pack_id", "manifest_sha256", "status", "requires_attention"}
                    or type(metadata["pack_id"]) is not str
                    or re.fullmatch(r"[0-9a-f]{32}", metadata["pack_id"]) is None
                    or type(metadata["manifest_sha256"]) is not str
                    or _SHA.fullmatch(metadata["manifest_sha256"]) is None
                    or metadata["status"] != "verified_complete" or metadata["requires_attention"] is not True):
                raise ValueError("invalid pack save metadata")
            return "", False, ("Saved private " + ("historical reviewed result" if reviewed else "unreviewed draft")
                + " pack " + metadata["pack_id"] + ". Reopening requires fresh source review; no approval was restored.")
        except _Stale:
            if not admitted:
                raise gr.Error(_STALE) from None
            return failed("Save was not confirmed for this view. Retained partial or complete data may exist; "
                "nothing was retried. Prepare and score again before saving a reviewed result.")
        except Exception as error:
            from ocr_crop_review_pack_io import CropReviewPackIOError

            notice = ("Save cleanup is unconfirmed. Retained data was not removed; do not retry automatically."
                      if type(error) is CropReviewPackIOError and error.code == "cleanup_uncertain" else
                      "Save was not confirmed. Retained partial or complete data may exist; nothing was retried.")
            return failed(notice)

    def save_crop_draft(session, execution, state, annotation, token, preview_profile, reference, critical, *values):
        return save_crop(session, execution, state, annotation, token, preview_profile, reference, critical, values, reviewed=False)

    def save_crop_reviewed(session, execution, state, annotation, token, preview_profile, reference, critical, *values):
        return save_crop(session, execution, state, annotation, token, preview_profile, reference, critical, values, reviewed=True)

    with gr.Accordion("Compare two retained crop runs", open=False):
        gr.Markdown("Select an item above and capture each side explicitly. Both must be crop runs of the same requested "
                    "original source rectangle. Saved whole-page context is not a crop reference. "
                    "Preview files use this authenticated host's private temporary cache, not per-session file secrecy.")
        crop_state = gr.State(_CropState() if pack_service is None else _SaveCropState())
        crop_view = gr.Textbox(value="", visible=False, interactive=False)
        with gr.Row():
            capture_before = gr.Button("Capture selected item as baseline crop")
            capture_after = gr.Button("Capture selected item as retry crop")
        with gr.Row():
            baseline_pin = gr.Textbox(value="Not captured", label="Captured baseline crop", interactive=False)
            retry_pin = gr.Textbox(value="Not captured", label="Captured retry crop", interactive=False)
        with gr.Row():
            load_pair = gr.Button("Load original crop and verified pair")
            cancel_preview = gr.Button("Cancel crop preview and clear approval")
        preview_profile = gr.Dropdown(choices=PREVIEW_CHOICES, value="fit", allow_custom_value=False, label="Original crop detail")
        reload_detail = gr.Button("Reload detail, keeping my draft")
        gr.Markdown("Changing detail does not render or run OCR. After rapid changes, cancel to resync the selected profile, then reload explicitly. Authored text is kept; review must be prepared and confirmed again.")
        gr.Markdown("Original requested physical crop (not processed OCR input)")
        original = gr.Image(label="Original requested physical crop (not processed OCR input)", type="pil",
                            format="png", interactive=False, sources=[], buttons=[], show_label=False, elem_id=LIVE_PREVIEW_ID)
        with gr.Row():
            before_text = gr.Textbox(label="Baseline crop OCR (may be empty or unavailable)", interactive=False)
            after_text = gr.Textbox(label="Retry crop OCR (may be empty or unavailable)", interactive=False)
        details = gr.Textbox(label="Exact crop, recipes and image binding", interactive=False)
        reference = gr.Textbox(value="", label="Your transcription of this original crop", max_length=20_000,
                               lines=5, placeholder="Starts empty. Transcribe from the original crop, not from OCR.")
        critical = gr.Textbox(value="", label="Critical entries, one nonempty literal entry per line (optional)",
                              max_length=64 * 257, lines=3)
        prepare_reference = gr.Button("Prepare exact reference revision")
        prepared = gr.Textbox(label="Prepared reference and ordered critical entries", interactive=False)
        prepared_token = gr.Textbox(value="", visible=False, interactive=False)
        confirmed = gr.Checkbox(value=False, label="I checked this exact original crop, transcription and critical entries")
        score = gr.Button("Score this confirmed crop reference once")
        metrics = gr.Textbox(label="Same-crop comparison and scope limitations", interactive=False)
        notice = gr.Textbox(label="Crop review status", interactive=False)
        if pack_service is not None:
            with gr.Row():
                save_draft = gr.Button("Save draft")
                save_reviewed = gr.Button("Save reviewed result")
            save_notice = gr.Textbox(label="Private crop pack save status (last attempt)", interactive=False)

    full_outputs = [crop_view, baseline_pin, retry_pin, original, before_text, after_text, details,
                    reference, critical, prepared_token, confirmed, prepared, metrics, notice]
    private = {"api_visibility": "private", "show_progress": "hidden"}
    quick = {**private, "queue": False, "trigger_mode": "multiple"}
    slow = {**private, "concurrency_id": "ocr-review-crop-comparison", "concurrency_limit": 1}
    items.input(crop_select_item, [crop_state, execution_state, runs, items, result_context], full_outputs, **quick)
    for component in [runs, *controls]:
        component.input(crop_context_changed, [crop_state], full_outputs, **quick)
    for component in [result_context, annotation_view]:
        component.change(crop_context_changed, [crop_state], full_outputs, **quick)
    capture_inputs = [crop_state, execution_state, runs, items, result_context, crop_view]
    capture_before.click(capture_baseline_crop, capture_inputs, full_outputs, **slow)
    capture_after.click(capture_retry_crop, capture_inputs, full_outputs, **slow)
    common = [crop_state, execution_state, review_state, annotation_view, crop_view, preview_profile]
    load_pair.click(load_crop_pair, [*common, *controls], full_outputs, **slow)
    reload_detail.click(reload_crop_detail, [*common, reference, critical, *controls], full_outputs, **slow)
    preview_profile.input(crop_preview_profile_changed, [crop_state, crop_view, preview_profile], full_outputs, **quick)
    cancel_preview.click(cancel_crop_preview, [crop_state, preview_profile], full_outputs, **quick)
    for component in (reference, critical):
        component.change(crop_reference_edited, [crop_state, crop_view, reference, critical],
                        [crop_view, prepared_token, confirmed, prepared, metrics, notice], **quick)
    prepare_outputs = [prepared_token, confirmed, prepared, metrics, notice]
    score_outputs = [prepared_token, confirmed, metrics, notice]
    if pack_service is not None:
        prepare_outputs.append(crop_view)
        score_outputs.append(crop_view)
    prepare_reference.click(prepare_crop_reference, [*common, reference, critical, *controls], prepare_outputs, **slow)
    score.click(score_crop_reference, [*common, reference, critical, prepared_token, confirmed, *controls],
                score_outputs, **slow)
    if pack_service is not None:
        save_inputs = [*common, reference, critical, *controls]
        save_outputs = [prepared_token, confirmed, save_notice]
        save_draft.click(save_crop_draft, save_inputs, save_outputs, **slow)
        save_reviewed.click(save_crop_reviewed, save_inputs, save_outputs, **slow)
