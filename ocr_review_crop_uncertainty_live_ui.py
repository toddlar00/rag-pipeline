"""Explicit uncertainty-aware live crop panel; legacy panel remains separate.

Only bounded identities, draft journal and review bytes enter session state.
The independent image token is emitted only in a fresh image response, never
minted by an edit or an annotation action. A returned token is not human proof.
"""
from __future__ import annotations

import copy
import hashlib
import json
from uuid import uuid4

from ocr_crop_comparison import _bytes, validate_crop_scope
from ocr_crop_raster_view import validate_raster_view
from ocr_crop_review_runtime import CropPreviewError, CropRasterPreview, validate_preview_profile
from ocr_review_crop_preview_ui import LIVE_PREVIEW_ID, PREVIEW_CHOICES, preview_image_update
from ocr_review_crop_ui_common import (
    _LIMIT, _PREVIEW_NOTICES, _ReferenceFieldError, _SaveCropState, _Stale,
    _close_unreturned_image, _context, _display, _hash, _id, _image_identity as _image_identity,
    _metadata, _raw_reference_digest, _reference, _sha, _text,
)
from ocr_review_crop_uncertainty import (
    author_arguments, dirty_uncertainty, empty_uncertainty, install_author_result,
    overlay_spec, prepare_policy, uncertainty_digest,
)
from ocr_review_crop_uncertainty_editor import admit_editor, build_uncertainty_editor, editor_value
from ocr_review_save_feedback import SAVE_PENDING, SAVE_UNCONFIRMED, build_save_feedback


_STALE = "Crop view changed. Wait for the current view, then prepare and confirm again."
_FAILED = "Crop action was refused. Keep your draft; check fields, region overlap and history limits. Nothing was adopted or retried."


class _AuthorFieldError(ValueError):
    pass


def _form_digest(action, identifier, kind, tentative, decision, reading, reason):
    for value, maximum in ((action, 32), (kind, 16), (tentative, 2000),
                            (decision, 32), (reading, 20_000), (reason, 512)):
        _text(value, maximum)
    if identifier is not None:
        _text(identifier, 7)
    return _hash([action, identifier, kind, tentative, decision, reading, reason], 256 * 1024)


class _LiveState(_SaveCropState):
    def __init__(self):
        super().__init__()
        self.uncertainty = empty_uncertainty()
        self.lineage = None
        self.image_token = None
        self.image_seen = False
        self.mode = "browse"
        self.selection = None
        self.annotation_id = None
        self.fields_digest = None
        self.edit_lease = None

    def revoke(self, *, clear_view, clear_draft=True):
        # A context invalidation is not permission to erase an authored lineage.
        super().revoke(clear_view=clear_view, clear_draft=clear_draft and self.lineage is None)
        self.edit_lease = None
        if clear_view:
            self.image_token = None
            self.image_seen = False
            self.mode = "browse"
            self.selection = None


def build_uncertainty_live_panel(workspace, coordinator, *, execution_state, review_state,
                                 annotation_view, runs, items, result_context, controls=(), pack_service=None):
    import gradio as gr
    import ocr_crop_advice as advice
    from PIL import Image

    def editor(session, *, newly_loaded=False):
        if session.loaded is None or not session.image_token or not (newly_loaded or session.image_seen):
            return None
        return editor_value(image_token=session.image_token, action_token=session.generation,
            mode_token=session.generation, mode=session.mode,
            **overlay_spec(session.uncertainty, scope=session.loaded["scope"], raster_view=session.loaded["raster_view"]))

    def annotations(session):
        value = session.uncertainty
        rows = value["annotations"]
        summary = _display({"dirty": value["dirty"], "annotations": rows,
            "history_revisions": 0 if value["journal"] is None else len(value["journal"]["revisions"]),
            "policy": "Any unresolved area makes the entire requested crop unscorable."}, 256 * 1024)
        choices = [(f"{row['annotation_id']}: {row['kind']}, {row['status']}", row["annotation_id"]) for row in rows]
        return summary, gr.update(choices=choices, value=session.annotation_id)

    def extras(session, *, newly_loaded=False):
        return editor(session, newly_loaded=newly_loaded), *annotations(session)

    def full(session, notice, *, image=None, pair=None, details="", preserve=True, newly_loaded=False):
        def label(pin):
            return "Not captured" if pin is None else f"{pin['run_id']} / {pin['item_id']} / {pin['operation']}"
        fields = (gr.skip(), gr.skip()) if preserve else ("", "")
        return (session.generation, label(session.baseline), label(session.retry), image,
            "" if pair is None else pair["baseline"]["text"] or "",
            "" if pair is None else pair["retry"]["text"] or "", details,
            *fields, "", False, "", "", notice, *extras(session, newly_loaded=newly_loaded))

    def require(session, token):
        if type(token) is not str or token != session.generation:
            raise _Stale()

    def image_admission(session, value, *, generation=None):
        # Token preflight precedes traversal, shape checks and coordinates.
        expected = session.generation if generation is None else generation
        if (type(value) is not dict or type(value.get("image_token")) is not str
                or not session.image_token or value["image_token"] != session.image_token
                or value.get("action_token") != expected
                or value.get("mode_token") != expected or session.loaded is None):
            raise _Stale()
        session.image_seen = True

    def edit_admission(session, token, captured=None, *, image_required=True):
        # Slow actions consume the displayed token before returning a new one.
        # Only invalidating edits may use that old token while the action runs.
        # This never authorizes Apply/Prepare/Score/Save or an unseen image.
        lease = session.edit_lease
        leased = (lease is not None and type(token) is str and token == lease[0]
            and session.generation == lease[1] and session.image_token == lease[2]
            and session.loaded is lease[3] and session.mode == lease[4])
        if not leased:
            require(session, token)
        if image_required and session.loaded is not None:
            image_admission(session, captured, generation=token if leased else None)

    def begin_action(session, token):
        session.revoke(clear_view=False)
        generation = session.generation
        session.edit_lease = (token, generation, session.image_token, session.loaded, session.mode)
        return generation

    def finish_action(session, generation):
        with session.lock:
            if session.edit_lease is not None and session.edit_lease[1] == generation:
                session.edit_lease = None

    def selected(execution, run, item, context):
        _id(run)
        _id(item)
        with execution.lock:
            if run not in execution.runs or execution.display_run != run or execution.result_generation != context:
                raise _Stale()
        return run, item, context

    def pin(result, run, item):
        _bytes(result, _LIMIT)
        if (type(result) is not dict or result.get("run_id") != run
                or result.get("operation") not in ("regions", "hardscan")
                or result.get("source_sha256") != workspace.document.source_sha256
                or result.get("baseline_recovery_sha256") != workspace.document.recovery_sha256
                or result.get("requires_attention") is not True):
            raise ValueError("invalid crop result")
        chosen = result["selected_item"]
        diagnostic, record = chosen["diagnostic"], chosen["report_record"]
        if (diagnostic["item_id"] != item or record["region_id"] != diagnostic["region_id"]
                or record["page_number"] != diagnostic["page_number"] or record["status"] != diagnostic["legacy_status"]):
            raise ValueError("changed crop occurrence")
        from ocr_crop_comparison import _id as region_id
        region_id(record["region_id"])
        return {"run_id": run, "item_id": item, "operation": result["operation"], "region_id": record["region_id"],
            **{key: _sha(result[key]) for key in ("request_sha256", "report_sha256", "execution_sha256", "disposition_sha256")}}

    def pins(session, execution):
        if session.baseline is None or session.retry is None:
            raise _Stale()
        with execution.lock:
            if any(row["run_id"] not in execution.runs for row in (session.baseline, session.retry)):
                raise _Stale()
        return copy.deepcopy([session.baseline, session.retry])

    def arguments(pair_pins):
        return tuple(pair_pins[side][key] for side in (0, 1) for key in ("run_id", "item_id"))

    def checked_pair(value, pair_pins):
        _bytes(value, _LIMIT)
        if (value.get("source_sha256") != workspace.document.source_sha256
                or value.get("baseline_recovery_sha256") != workspace.document.recovery_sha256
                or value.get("requires_attention") is not True or value.get("canonical_extraction_modified") is not False
                or value.get("reference_status") != "not_supplied"):
            raise ValueError("invalid pair")
        _sha(value["pair_sha256"])
        payload = _bytes({key: item for key, item in value.items() if key != "pair_sha256"}, _LIMIT - 1)
        if hashlib.sha256(payload + b"\n").hexdigest() != value["pair_sha256"]:
            raise ValueError("invalid pair hash")
        scope = validate_crop_scope(value["scope"], source_sha256=workspace.document.source_sha256,
                                    page_count=workspace.document.page_count)
        for name, row in zip(("baseline", "retry"), pair_pins):
            side = value[name]
            if any(side.get(key) != item for key, item in row.items()):
                raise ValueError("changed pair")
            if side["status"] not in ("review_required", "empty_candidate", "retry_failed", "abstained"):
                raise ValueError("invalid candidate status")
            if side["text"] is not None:
                _text(side["text"])
            if (side["text"] is not None) != (side["status"] in ("review_required", "empty_candidate")):
                raise ValueError("invalid candidate availability")
        return scope

    def loaded(session, execution, state, annotation, token, profile, values, captured):
        require(session, token)
        image_admission(session, captured)
        if (validate_preview_profile(profile) != session.preview_profile
                or session.loaded["preview_profile"] != profile
                or session.loaded["pins"] != pins(session, execution)
                or session.loaded["context"] != _context(workspace, state, annotation, values, execution)):
            raise _Stale()
        return copy.deepcopy(session.loaded)

    def crop_context_changed(session):
        with session.lock:
            session.revoke(clear_view=True)
            session.selected = None
            return full(session, "Context changed. Draft history is retained but not authorized; reload the exact pair.")

    def crop_select_item(session, execution, run, item, context):
        with session.lock:
            session.revoke(clear_view=True)
            session.selected = None
            try:
                session.selected = selected(execution, run, item, context)
            except Exception:
                return full(session, "Select a current crop item before capturing it.")
            return full(session, "Capture this verified crop as baseline or retry explicitly.")

    def capture(session, execution, run, item, context, token, side):
        try:
            with session.lock:
                require(session, token)
                if session.selected != selected(execution, run, item, context):
                    raise _Stale()
                session.revoke(clear_view=True)
                generation = session.generation
                setattr(session, side, None)
            result = pin(coordinator.result(run, item_id=item), run, item)
            with session.lock:
                require(session, generation)
                selected(execution, run, item, context)
                setattr(session, side, result)
                return full(session, "Crop captured. Load the original pair to review it.")
        except _Stale:
            raise gr.Error(_STALE) from None
        except Exception:
            raise gr.Error(_FAILED) from None

    def capture_baseline_crop(session, execution, run, item, context, token):
        return capture(session, execution, run, item, context, token, "baseline")

    def capture_retry_crop(session, execution, run, item, context, token):
        return capture(session, execution, run, item, context, token, "retry")

    def change_profile(session, token, profile):
        with session.lock:
            try:
                edit_admission(session, token, image_required=False)
            except _Stale:
                return (gr.skip(),) * 17
            try:
                profile = validate_preview_profile(profile)
            except Exception:
                session.revoke(clear_view=True, clear_draft=False)
                return full(session, "Invalid detail profile. Cancel to resync; your draft remains.")
            if profile == session.preview_profile:
                return (gr.skip(),) * 17
            session.preview_profile = profile
            session.revoke(clear_view=True, clear_draft=False)
            return full(session, "Detail changed. Reload explicitly; annotations retain their original physical scope.")

    change_profile.__name__ = "crop_preview_profile_changed"

    def cancel_crop_preview(session, profile):
        with session.lock:
            session.revoke(clear_view=True, clear_draft=False)
            try:
                session.preview_profile = validate_preview_profile(profile)
            except Exception:
                return full(session, "Cancelled. Select a valid profile and cancel again to resync.")
            return full(session, "Preview cancellation requested. Draft history remains; no consent or image authority remains.")

    def load_view(session, execution, state, annotation, token, profile, reference, critical, values):
        generation = None
        owner, transferred = None, False
        try:
            with session.lock:
                require(session, token)
                if validate_preview_profile(profile) != session.preview_profile:
                    raise _Stale()
                pair_pins = pins(session, execution)
                context = _context(workspace, state, annotation, values, execution)
                raw = _raw_reference_digest(reference, critical)
                session.revoke(clear_view=True, clear_draft=False)
                generation = session.generation
            pair = coordinator.crop_pair(*arguments(pair_pins))
            scope = checked_pair(pair, pair_pins)

            def cancelled():
                with session.lock:
                    return session.generation != generation

            owner = coordinator.render_crop_preview_with_view(scope, preview_profile=profile, cancel_requested=cancelled)
            if type(owner) is not CropRasterPreview:
                raise ValueError("invalid metadata owner")
            image = owner.image
            if (not isinstance(image, Image.Image) or image.mode != "RGB"
                    or not 1 <= image.width <= 1400 or not 1 <= image.height <= 1400):
                raise ValueError("invalid original crop preview")
            view = validate_raster_view(owner.raster_view, scope=scope)
            rgb = image.tobytes()
            if (type(rgb) is not bytes or len(rgb) != image.width * image.height * 3
                    or view["preview_profile"] != profile or (view["width"], view["height"]) != image.size
                    or view["rgb_sha256"] != hashlib.sha256(rgb).hexdigest()):
                raise ValueError("image declaration mismatch")
            crop_advice = advice.build_crop_advice(rgb, scope=scope, raster_view=view)
            advice_notice = advice.crop_advice_text(crop_advice)
            fresh = coordinator.crop_pair(*arguments(pair_pins))
            checked_pair(fresh, pair_pins)
            if fresh["pair_sha256"] != pair["pair_sha256"]:
                raise ValueError("pair changed during preview")
            with session.lock:
                require(session, generation)
                if pins(session, execution) != pair_pins or context != _context(workspace, state, annotation, values, execution):
                    raise _Stale()
                same = session.lineage == pair["pair_sha256"]
                if not same:
                    session.uncertainty = empty_uncertainty()
                    session.annotation_id = None
                    session.reference_digest = _raw_reference_digest("", "")
                elif raw != session.reference_digest:
                    session.uncertainty = dirty_uncertainty(session.uncertainty)
                    session.reference_digest = raw
                session.lineage = pair["pair_sha256"]
                session.loaded = {"pair_sha256": pair["pair_sha256"], "pins": pair_pins, "context": context,
                    "scope": scope, "raster_view": view, "preview_profile": profile}
                session.image_token = uuid4().hex
                session.image_seen = False
                details = _display({"scope": scope, "preview_profile": profile, "raster_view": view,
                    "pair_sha256": pair["pair_sha256"], "advice": crop_advice})
                output = full(session, "Original crop loaded. Browse or explicitly annotate uncertainty; prepare and confirm before review. " + advice_notice,
                    image=preview_image_update(image, profile), pair=pair, details=details, preserve=same, newly_loaded=True)
            transferred = True
            return output
        except _Stale:
            raise gr.Error(_STALE) from None
        except Exception as error:
            with session.lock:
                if generation is not None and generation != session.generation:
                    raise gr.Error(_STALE) from None
                session.revoke(clear_view=True, clear_draft=False)
                message = _PREVIEW_NOTICES.get(error.code, _FAILED) if type(error) is CropPreviewError else _FAILED
                return full(session, message)
        finally:
            if not transferred:
                _close_unreturned_image(owner)

    def load_crop_pair(session, execution, state, annotation, token, profile, reference, critical, *values):
        return load_view(session, execution, state, annotation, token, profile, reference, critical, values)

    def reload_crop_detail(session, execution, state, annotation, token, profile, reference, critical, *values):
        return load_view(session, execution, state, annotation, token, profile, reference, critical, values)

    def changed_result(session, message):
        return session.generation, "", False, "", "", message, *extras(session)

    def crop_reference_edited(session, token, reference, critical, captured):
        with session.lock:
            try:
                edit_admission(session, token, captured)
            except _Stale:
                return (gr.skip(),) * 9
            try:
                raw = _raw_reference_digest(reference, critical)
            except Exception:
                raw = None
            if raw is not None and raw == session.reference_digest:
                return (gr.skip(),) * 9
            session.uncertainty = dirty_uncertainty(session.uncertainty)
            session.reference_digest = raw
            session.revoke(clear_view=False)
            return changed_result(session, "Text edited. Resolved areas are reopened; Apply or Prepare explicitly records the reset.")

    def crop_uncertainty_fields_edited(session, token, captured, *fields):
        with session.lock:
            try:
                edit_admission(session, token, captured)
                if session.loaded is None:
                    raise _Stale()
            except _Stale:
                return (gr.skip(),) * 9
            try:
                digest = _form_digest(*fields)
            except Exception:
                digest = None
            if digest is not None and digest == session.fields_digest:
                return (gr.skip(),) * 9
            session.fields_digest = digest
            identifier = fields[1] if len(fields) == 7 else None
            if identifier is None or any(row["annotation_id"] == identifier for row in session.uncertainty["annotations"]):
                session.annotation_id = identifier
            session.revoke(clear_view=False)
            return changed_result(session, "Annotation controls changed. Apply records an explicit decision; prior approval is cleared.")

    def crop_uncertainty_editor_input(session, token, captured):
        with session.lock:
            try:
                edit_admission(session, token, captured)
                if session.loaded is None:
                    raise _Stale()
            except _Stale:
                return (gr.skip(),) * 9
            try:
                command = admit_editor(captured, image_token=session.image_token,
                    action_token=token, mode_token=token)
                if command is None or command["kind"] == "mode" and command["mode"] == session.mode:
                    return (gr.skip(),) * 9
                session.revoke(clear_view=False)
                kind = command["kind"]
                if kind == "mode" and command["mode"] in ("browse", "annotate"):
                    if command["pixel_bbox"] is not None or command["ordinal"] is not None:
                        raise ValueError("invalid mode command")
                    session.mode, session.selection = command["mode"], None
                elif kind in ("rectangle", "whole_scope") and session.mode == "annotate":
                    if command["mode"] is not None or command["ordinal"] is not None:
                        raise ValueError("invalid selection command")
                    selection = {"kind": "raster_edges" if kind == "rectangle" else "whole_scope", "pixel_bbox": command["pixel_bbox"]}
                    from ocr_crop_raster_view import selection_to_scope_bbox
                    selection_to_scope_bbox(selection, scope=session.loaded["scope"], raster_view=session.loaded["raster_view"])
                    session.selection = selection
                elif kind == "annotation" and session.mode == "annotate":
                    ordinal = command["ordinal"]
                    if (type(ordinal) is not int or not 1 <= ordinal <= 128
                            or command["mode"] is not None or command["pixel_bbox"] is not None):
                        raise ValueError("invalid annotation selection")
                    identifier = f"a{ordinal:06d}"
                    if not any(row["annotation_id"] == identifier for row in session.uncertainty["annotations"]):
                        raise ValueError("annotation absent")
                    session.annotation_id = identifier
                elif kind == "cancel" and all(command[key] is None for key in ("mode", "pixel_bbox", "ordinal")):
                    session.selection = None
                else:
                    raise ValueError("invalid annotation command")
                return changed_result(session, "Selection is pending, not authored. Choose an action and reason, then Apply.")
            except Exception:
                session.revoke(clear_view=False)
                session.selection = None
                return changed_result(session, "Selection refused. Keep inside the requested crop, not the raster rounding border; no box was clamped.")

    def human_command(session, action, identifier, kind, tentative, decision, reading):
        if type(tentative) is not str or type(reading) is not str:
            raise _AuthorFieldError("Enter tentative and confirmed readings as text; neither is filled from OCR.")
        if action in ("add", "move") and session.selection is None:
            raise _AuthorFieldError("Select a source rectangle or the entire requested crop before Apply.")
        if action == "add":
            return {"action": action, "selection": session.selection, "kind": kind, "tentative_text": tentative or None}
        if action == "move":
            return {"action": action, "annotation_id": identifier, "selection": session.selection}
        if action == "reclassify":
            return {"action": action, "annotation_id": identifier, "kind": kind}
        if action == "edit_tentative":
            return {"action": action, "annotation_id": identifier, "tentative_text": tentative or None}
        if action == "resolve":
            return {"action": action, "annotation_id": identifier, "decision": decision,
                "reading": reading, "span": None}
        if action in ("reopen", "dismiss"):
            return {"action": action, "annotation_id": identifier}
        raise ValueError("unknown authoring action")

    def author(current, observed_raw, snapshot, reference, critical, reason, commands):
        reference, entries, raw = _reference(reference, critical)
        if type(reason) is not str or not reason.strip() or len(reason) > 512:
            raise _AuthorFieldError("Enter a reason of 1–512 characters before Apply or Prepare; your draft remains unchanged.")
        if raw != observed_raw:
            current = dirty_uncertainty(current)
        request = author_arguments(current, reference=reference, critical_tokens=entries,
            scope=snapshot["scope"], raster_view=snapshot["raster_view"], commands=commands, reason=reason)
        result = coordinator.author_crops_v2(*arguments(snapshot["pins"]),
            expected_pair_sha256=snapshot["pair_sha256"], **request)
        if result.get("pair_sha256") != snapshot["pair_sha256"]:
            raise ValueError("author pair changed")
        return install_author_result(result, scope=snapshot["scope"]), raw, entries

    def current_after(session, generation, snapshot, execution, state, annotation, profile, values):
        require(session, generation)
        if (session.loaded != snapshot or pins(session, execution) != snapshot["pins"]
                or _context(workspace, state, annotation, values, execution) != snapshot["context"]
                or session.preview_profile != profile):
            raise _Stale()

    def apply_crop_uncertainty(session, execution, state, annotation, token, profile, reference, critical,
                               captured, action, identifier, kind, tentative, decision, reading, reason, *values):
        generation = None
        try:
            with session.lock:
                snapshot = loaded(session, execution, state, annotation, token, profile, values, captured)
                if session.mode != "annotate":
                    raise _Stale()
                generation = begin_action(session, token)
                _form_digest(action, identifier, kind, tentative, decision, reading, reason)
                command = human_command(session, action, identifier, kind, tentative, decision, reading)
                current, observed_raw = copy.deepcopy(session.uncertainty), session.reference_digest
            authored, raw, _ = author(current, observed_raw, snapshot, reference, critical, reason, [command])
            with session.lock:
                current_after(session, generation, snapshot, execution, state, annotation, profile, values)
                session.uncertainty, session.reference_digest = authored, raw
                session.selection = None
                session.annotation_id = authored["annotations"][-1]["annotation_id"] if action == "add" else identifier
                finish_action(session, generation)
                return changed_result(session, "Authoring decision recorded. Review the full reference, then prepare and confirm anew.")
        except _Stale:
            raise gr.Error(_STALE) from None
        except Exception as error:
            with session.lock:
                if generation is None or session.generation != generation:
                    raise gr.Error(_STALE) from None
                finish_action(session, generation)
                return changed_result(session, str(error) if type(error) in (_ReferenceFieldError, _AuthorFieldError) else _FAILED)
        finally:
            finish_action(session, generation)

    def prepare_crop_reference(session, execution, state, annotation, token, profile, reference, critical,
                               captured, action, identifier, kind, tentative, decision, reading, reason, *values):
        generation = None
        try:
            with session.lock:
                snapshot = loaded(session, execution, state, annotation, token, profile, values, captured)
                generation = begin_action(session, token)
                if session.selection is not None:
                    raise _AuthorFieldError("Apply or cancel the pending selected region before preparing the full reference.")
                form_digest = _form_digest(action, identifier, kind, tentative, decision, reading, reason)
                current, observed_raw = copy.deepcopy(session.uncertainty), session.reference_digest
            authored, raw, entries = author(current, observed_raw, snapshot, reference, critical, reason, [])
            with session.lock:
                current_after(session, generation, snapshot, execution, state, annotation, profile, values)
                # Save an authored checkpoint even when final review eligibility fails.
                session.uncertainty, session.reference_digest = authored, raw
                policy = prepare_policy(authored, reference=reference, critical_tokens=entries)
                content = _hash([raw, uncertainty_digest(authored), form_digest])
                session.fields_digest = form_digest
                ticket = uuid4().hex
                session.pending = (ticket, generation, content, session.image_token)
                prepared = _display({"reference": reference, "critical_tokens": entries,
                    "annotations": authored["annotations"], "scope": snapshot["scope"], "policy": policy,
                    "journal_head": authored["journal"]["head_sha256"]}, 256 * 1024)
                finish_action(session, generation)
                return ticket, False, prepared, "", "Check the complete source-bound declaration, including every unresolved area.", generation, *extras(session)
        except _Stale:
            raise gr.Error(_STALE) from None
        except Exception as error:
            with session.lock:
                if generation is None or session.generation != generation:
                    raise gr.Error(_STALE) from None
                finish_action(session, generation)
                return "", False, "", "", str(error) if type(error) in (_ReferenceFieldError, _AuthorFieldError) else _FAILED, generation, *extras(session)
        finally:
            finish_action(session, generation)

    def score_crop_reference(session, execution, state, annotation, token, profile, reference, critical,
                             ticket, confirmed, captured, action, identifier, kind, tentative, decision, reading, reason, *values):
        generation = None
        try:
            with session.lock:
                snapshot = loaded(session, execution, state, annotation, token, profile, values, captured)
                pending = session.pending
                if (confirmed is not True or type(ticket) is not str or pending is None
                        or pending[0] != ticket or pending[1] != token or pending[3] != session.image_token):
                    raise _Stale()
                generation = begin_action(session, token)
                _, entries, raw = _reference(reference, critical)
                form_digest = _form_digest(action, identifier, kind, tentative, decision, reading, reason)
                content = _hash([raw, uncertainty_digest(session.uncertainty), form_digest])
                if pending[2] != content:
                    if raw != session.reference_digest:
                        session.uncertainty = dirty_uncertainty(session.uncertainty)
                    raise ValueError("changed prepared text")
                prepare_policy(session.uncertainty, reference=reference, critical_tokens=entries)
                journal = copy.deepcopy(session.uncertainty["journal"])
                expected_annotations = copy.deepcopy(session.uncertainty["annotations"])
            result = coordinator.compare_crops_v2(*arguments(snapshot["pins"]),
                expected_pair_sha256=snapshot["pair_sha256"], journal=journal, confirmed=True)
            _bytes(result, _LIMIT)
            if (result.get("pair_sha256") != snapshot["pair_sha256"]
                    or result.get("requires_attention") is not True or result.get("canonical_extraction_modified") is not False
                    or result["reference"]["journal"] != journal or result["reference"]["reference"] != reference
                    or result["reference"]["annotations"] != expected_annotations
                    or result["reference"]["critical_tokens"] != entries
                    or result["reference"]["scope"] != snapshot["scope"]
                    or result["comparison"]["source_sha256"] != workspace.document.source_sha256):
                raise ValueError("comparison join changed")
            reviewed, comparison = result["reference"], result["comparison"]
            if (type(reviewed["schema_version"]) is not int or reviewed["schema_version"] != 2
                    or reviewed["kind"] != "ocr_crop_reference"
                    or type(comparison["schema_version"]) is not int or comparison["schema_version"] != 2
                    or comparison["kind"] != "ocr_crop_comparison" or comparison["scope"] != snapshot["scope"]
                    or type(comparison["page_count"]) is not int or comparison["page_count"] != workspace.document.page_count
                    or comparison["reference_id"] != reviewed["reference_id"]
                    or comparison["reference_sha256"] != reviewed["reference_sha256"]
                    or comparison["requires_attention"] is not True or comparison["canonical_extraction_modified"] is not False):
                raise ValueError("comparison occurrence changed")
            for name, row in zip(("baseline", "retry"), snapshot["pins"]):
                if any(comparison["pair"][name][key] != row[key] for key in ("report_sha256", "region_id")):
                    raise ValueError("comparison selected pair changed")
            with session.lock:
                current_after(session, generation, snapshot, execution, state, annotation, profile, values)
                session.last_score = (generation, content, _bytes({"reference": result["reference"],
                    "comparison": result["comparison"], "source_image": snapshot["raster_view"]}, _LIMIT))
                finish_action(session, generation)
                return "", False, _display(result["comparison"]), "Review recorded. Unresolved areas abstain for the whole crop; no canonical text was adopted.", generation, *extras(session)
        except _Stale:
            raise gr.Error(_STALE) from None
        except Exception as error:
            with session.lock:
                if generation is None or session.generation != generation:
                    raise gr.Error(_STALE) from None
                finish_action(session, generation)
                return "", False, "", str(error) if type(error) is _ReferenceFieldError else _FAILED, generation, *extras(session)
        finally:
            finish_action(session, generation)

    def save(session, execution, state, annotation, token, profile, reference, critical, captured, form_fields, values, *, reviewed):
        generation = None
        try:
            try:
                with session.lock:
                    snapshot = loaded(session, execution, state, annotation, token, profile, values, captured)
                    saved = session.last_score
                    generation = begin_action(session, token)
                    raw = _raw_reference_digest(reference, critical)
                    if raw != session.reference_digest:
                        session.uncertainty = dirty_uncertainty(session.uncertainty)
                        session.reference_digest = raw
                    uncertainty = copy.deepcopy(session.uncertainty)
                    content = _hash([raw, uncertainty_digest(uncertainty), _form_digest(*form_fields)])
                    if reviewed and (saved is None or saved[:2] != (token, content)):
                        raise ValueError("reviewed save no longer current")
                    declaration = json.loads(saved[2]) if reviewed else None

                def verify_current():
                    with session.lock:
                        current_after(session, generation, snapshot, execution, state, annotation, profile, values)
                        if session.reference_digest != raw or session.uncertainty != uncertainty:
                            raise _Stale()
                    return True

                # No fresh image/editor/action token is delivered by this yield.
                # The existing edit-only lease can still invalidate this attempt.
                yield (gr.skip(), gr.skip(), SAVE_PENDING, gr.skip(), gr.skip(), gr.skip(), gr.skip())
                verify_current()
                metadata = pack_service.save_live_v2(coordinator, *arguments(snapshot["pins"]),
                    expected_pair_sha256=snapshot["pair_sha256"], reference=reference, critical_tokens_text=critical,
                    uncertainty=uncertainty, reviewed=declaration, verify_current=verify_current)
                verify_current()
                checked = _metadata(metadata)
                if checked["status"] != "verified_complete" or checked["manifest_sha256"] is None:
                    raise ValueError("save incomplete")
                message = "Saved a new private " + ("reviewed declaration" if reviewed else "draft") + "; history remains in this live session. Reopen requires fresh confirmation."
            except _Stale:
                message = SAVE_UNCONFIRMED
            except Exception:
                message = SAVE_UNCONFIRMED
            with session.lock:
                finish_action(session, generation)
                if generation is not None and generation == session.generation:
                    result = ("", False, message, generation, *extras(session))
                else:
                    result = (gr.skip(), gr.skip(), message, gr.skip(), gr.skip(), gr.skip(), gr.skip())
            yield result
        finally:
            # Also covers a waiting iterator being closed. Never yield while
            # unwinding GeneratorExit or infer that running storage rolled back.
            finish_action(session, generation)

    def save_crop_draft(session, execution, state, annotation, token, profile, reference, critical, captured,
                        action, identifier, kind, tentative, decision, reading, reason, *values):
        yield from save(session, execution, state, annotation, token, profile, reference, critical, captured,
            (action, identifier, kind, tentative, decision, reading, reason), values, reviewed=False)

    def save_crop_reviewed(session, execution, state, annotation, token, profile, reference, critical, captured,
                           action, identifier, kind, tentative, decision, reading, reason, *values):
        yield from save(session, execution, state, annotation, token, profile, reference, critical, captured,
            (action, identifier, kind, tentative, decision, reading, reason), values, reviewed=True)

    with gr.Accordion("Compare two retained crop runs", open=False):
        gr.Markdown("Capture the same original crop from each run. Author your own reference; mark uncertain regions explicitly. Any unresolved area makes the entire crop unscorable.")
        session_component = gr.State(_LiveState())
        token_component = gr.Textbox(value="", visible=False, interactive=False)
        with gr.Row():
            capture_before = gr.Button("Capture selected item as baseline crop")
            capture_after = gr.Button("Capture selected item as retry crop")
        with gr.Row():
            baseline = gr.Textbox(value="Not captured", label="Captured baseline crop", interactive=False)
            retry = gr.Textbox(value="Not captured", label="Captured retry crop", interactive=False)
        with gr.Row():
            load = gr.Button("Load original crop and verified pair")
            cancel = gr.Button("Cancel crop preview and clear approval")
        profile = gr.Dropdown(choices=PREVIEW_CHOICES, value="fit", allow_custom_value=False, label="Original crop detail")
        reload = gr.Button("Reload detail, keeping my draft")
        gr.Markdown("Original requested physical crop (not processed OCR input)")
        original = gr.Image(type="pil", format="png", interactive=False, sources=[], buttons=[], show_label=False,
            label="Original requested physical crop (not processed OCR input)", elem_id=LIVE_PREVIEW_ID)
        editor_component = build_uncertainty_editor(LIVE_PREVIEW_ID)
        with gr.Row():
            before = gr.Textbox(label="Baseline crop OCR (not ground truth)", interactive=False)
            after = gr.Textbox(label="Retry crop OCR (not ground truth)", interactive=False)
        details_component = gr.Textbox(label="Exact crop, image binding and nonexecuting source advice", interactive=False)
        reference_component = gr.Textbox(value="", label="Your transcription of this original crop", lines=5, max_length=20_000)
        critical_component = gr.Textbox(value="", label="Critical entries, one nonempty literal entry per line (optional)", lines=3, max_length=64 * 257)
        summary = gr.Textbox(label="Uncertainty annotations and history", interactive=False)
        with gr.Row():
            action_component = gr.Dropdown([("Add selected area", "add"), ("Move / resize selected annotation", "move"),
                ("Change uncertainty kind", "reclassify"), ("Edit tentative text", "edit_tentative"),
                ("Resolve selected annotation", "resolve"), ("Reopen selected annotation", "reopen"),
                ("Dismiss with reason (history retained)", "dismiss")], value="add", label="Annotation action", allow_custom_value=False)
            annotation_component = gr.Dropdown([], value=None, label="Selected annotation", allow_custom_value=False)
            kind_component = gr.Radio(["uncertain", "illegible"], value="uncertain", label="Uncertainty kind")
        tentative_component = gr.Textbox(value="", label="Tentative text (not ground truth, optional)", max_length=2000)
        decision_component = gr.Radio([("Reading confirmed", "reading_confirmed"), ("Not text", "not_text")], value="reading_confirmed", label="Resolution decision")
        reading_component = gr.Textbox(value="", label="Confirmed reading (must already occur verbatim in your full transcription; empty for Not text)", max_length=20_000)
        reason_component = gr.Textbox(value="", label="Reason for this authoring / preparation action", max_length=512)
        apply = gr.Button("Apply annotation decision")
        prepare = gr.Button("Prepare exact reference revision")
        prepared_component = gr.Textbox(label="Prepared reference, uncertainty and scoring policy", interactive=False)
        ticket_component = gr.Textbox(value="", visible=False, interactive=False)
        confirmation = gr.Checkbox(value=False, label="I checked this exact original crop, transcription, critical entries and uncertainty annotations")
        score = gr.Button("Record confirmed review once (score only if resolved)")
        metrics_component = gr.Textbox(label="Same-crop comparison and scope limitations", interactive=False)
        notice = gr.Textbox(label="Crop review status", interactive=False)
        if pack_service is not None:
            with gr.Row():
                save_draft = gr.Button("Save draft")
                save_reviewed = gr.Button("Save reviewed result")
            save_notice, save_progress, save_requested = build_save_feedback("live")

    extra_outputs = [editor_component, summary, annotation_component]
    all_outputs = [token_component, baseline, retry, original, before, after, details_component,
        reference_component, critical_component, ticket_component, confirmation, prepared_component, metrics_component, notice, *extra_outputs]
    changed_outputs = [token_component, ticket_component, confirmation, prepared_component, metrics_component, notice, *extra_outputs]
    private = {"api_visibility": "private", "show_progress": "hidden"}
    quick = {**private, "queue": False, "trigger_mode": "multiple"}
    slow = {**private, "concurrency_id": "ocr-review-crop-comparison", "concurrency_limit": 1}
    items.input(crop_select_item, [session_component, execution_state, runs, items, result_context], all_outputs, **quick)
    for component in [runs, *controls]:
        component.input(crop_context_changed, [session_component], all_outputs, **quick)
    for component in [result_context, annotation_view]:
        component.change(crop_context_changed, [session_component], all_outputs, **quick)
    capture_inputs = [session_component, execution_state, runs, items, result_context, token_component]
    capture_before.click(capture_baseline_crop, capture_inputs, all_outputs, **slow)
    capture_after.click(capture_retry_crop, capture_inputs, all_outputs, **slow)
    common = [session_component, execution_state, review_state, annotation_view, token_component, profile]
    fields = [reference_component, critical_component]
    load.click(load_crop_pair, [*common, *fields, *controls], all_outputs, **slow)
    reload.click(reload_crop_detail, [*common, *fields, *controls], all_outputs, **slow)
    profile.input(change_profile, [session_component, token_component, profile], all_outputs, **quick)
    cancel.click(cancel_crop_preview, [session_component, profile], all_outputs, **quick)
    for component in fields:
        component.change(crop_reference_edited, [session_component, token_component, *fields, editor_component], changed_outputs, **quick)
    action_fields = [action_component, annotation_component, kind_component, tentative_component, decision_component, reading_component, reason_component]
    for component in action_fields:
        component.change(crop_uncertainty_fields_edited, [session_component, token_component, editor_component, *action_fields], changed_outputs, **quick)
    editor_component.input(crop_uncertainty_editor_input, [session_component, token_component, editor_component], changed_outputs, **quick)
    apply.click(apply_crop_uncertainty, [*common, *fields, editor_component, *action_fields, *controls], changed_outputs, **slow)
    prepare.click(prepare_crop_reference, [*common, *fields, editor_component, *action_fields, *controls],
        [ticket_component, confirmation, prepared_component, metrics_component, notice, token_component, *extra_outputs], **slow)
    score.click(score_crop_reference, [*common, *fields, ticket_component, confirmation, editor_component, *action_fields, *controls],
        [ticket_component, confirmation, metrics_component, notice, token_component, *extra_outputs], **slow)
    if pack_service is not None:
        save_inputs = [*common, *fields, editor_component, *action_fields, *controls]
        save_outputs = [ticket_component, confirmation, save_notice, token_component, *extra_outputs]
        save_events = {**slow, "show_progress": "full", "show_progress_on": [save_progress], "js": save_requested}
        save_draft.click(save_crop_draft, save_inputs, save_outputs, **save_events)
        save_reviewed.click(save_crop_reviewed, save_inputs, save_outputs, **save_events)
