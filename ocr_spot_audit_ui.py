"""Opt-in source-first spot-audit panel; no OCR, model or canonical writes."""
from __future__ import annotations

import copy
import hashlib
import json
import secrets
import sys
import threading

import ocr_spot_audit as audit
from ocr_review_crop_ui_common import _close_unreturned_image, _image_identity


_STALE = "This action belongs to an older page or view. Wait for the current view; use Reset view if an output was lost."
_FAILED = "The audit action could not be verified. Typed text is retained. Check the fixed inputs and open the page again."


class _AuditState:
    def __init__(self):
        self.lock = threading.RLock()
        self.snapshot = None
        self.context = ""
        self.page = None
        self.image_token = ""
        self.failed_open = None
        self.confirmed_digest = None
        self.fields_digest = None

    def __deepcopy__(self, memo):
        return type(self)()

    def rotate(self, *, view=False):
        self.context = secrets.token_hex(32)
        self.confirmed_digest = None
        if view:
            self.image_token = ""
            self.failed_open = None


def _fields_digest(text, note, outcome):
    if (type(text) is not str or len(text) > 20_000 or type(note) is not str
            or len(note) > 2_000 or type(outcome) is not str
            or outcome not in {"reviewed", "unresolved", "unavailable"}):
        raise ValueError("invalid audit fields")
    raw = json.dumps([text, note, outcome], ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _coverage(snapshot):
    """Presentation only: never call the scorer while restoring or editing."""
    plan = snapshot["plan"]
    frame = plan["frame"]
    counts = {name: sum(row["outcome"] == name for row in snapshot["records"])
              for name in ("reviewed", "unresolved", "unavailable", "pending")}
    eligible = sum(row["eligible"] for row in frame)
    return {"universe": len(frame), "eligible": eligible, "excluded": len(frame) - eligible,
            "sampled": len(plan["selected_pages"]), "eligible_unselected": eligible - len(plan["selected_pages"]),
            **counts, "seed": plan["seed"], "threshold": plan["threshold"],
            "selected_pages": list(plan["selected_pages"]), "inclusion_probability": plan["inclusion_probability"],
            "manual_review_required": True, "canonical_extraction_modified": False}


def build_spot_audit_panel(workspace):
    """Build inside Blocks. All paths and source reads remain host-owned."""
    import gradio as gr

    document = workspace.document
    initial = copy.deepcopy(getattr(workspace, "initial_audit", None))
    parent = getattr(workspace, "audit_sha256", None)
    with gr.Tab("Random spot audit"):
        gr.Markdown("Review a fixed random sample of high-confidence candidates against original pages. "
                    "OCR text stays hidden in this lane until you explicitly store a reviewed or unresolved outcome. "
                    "Other panels may show OCR: this is source-first, not a blinded or independently adjudicated study.")
        session = gr.State(_AuditState())
        context = gr.Textbox(value="", visible=False)
        displayed_page = gr.Number(value=None, precision=0, visible=False)
        delivered_image = gr.Textbox(value="", visible=False)
        with gr.Row():
            threshold = gr.Number(value=.95, label="Minimum mean confidence", minimum=0, maximum=1)
            sample_size = gr.Number(value=10, precision=0, minimum=1, label="Requested sample size")
            create = gr.Button("Create / restore frozen sample")
        counts = gr.JSON(label="Frozen sample and coverage (not accuracy)")
        page = gr.Dropdown(choices=[], value=None, label="Sample page (draw order)", allow_custom_value=False)
        with gr.Row():
            open_page = gr.Button("Open original page")
            reset = gr.Button("Reset view (keep typed text)")
        image = gr.Image(type="pil", sources=[], buttons=[], interactive=False, format="png",
                         label="Original source page", height=600)
        text = gr.Textbox(label="Your source transcription / unfinished draft", lines=6, max_length=20_000)
        note = gr.Textbox(label="Review note (required for unresolved or unavailable; stored only by Store outcome)",
                          lines=2, max_length=2_000)
        outcome = gr.Radio(choices=["reviewed", "unresolved", "unavailable"], value="reviewed", label="Outcome")
        confirmed = gr.Checkbox(value=False, label="I checked this exact transcription against the displayed original page")
        store = gr.Button("Store outcome")
        candidate = gr.Textbox(value="", label="OCR candidate (revealed only after explicit reviewed/unresolved Store)",
                               interactive=False, lines=6)
        historical = gr.JSON(label="Stored outcomes (historical declarations, not restored approval)")
        with gr.Row():
            save = gr.Button("Save audit snapshot (including unfinished transcription)")
            score = gr.Button("Score stored reviewed references")
        metrics = gr.JSON(label="Explicit score of stored reviewed references only; drafts are not scored")
        saved_path = gr.Textbox(value="", label="New private audit snapshot path (restart with --audit)", interactive=False)
        status = gr.Markdown("Create or restore one frozen sample. Changing settings never resamples an existing session.")

    outputs = [session, image, context, displayed_page, delivered_image, page, text, note, outcome,
               confirmed, candidate, historical, counts, metrics, saved_path, status]
    options = {"api_visibility": "private", "concurrency_id": "spot-audit-" + secrets.token_hex(16),
               "concurrency_limit": 1, "trigger_mode": "once"}

    def response(state, notice, **changes):
        result = {session: state, status: notice}
        result.update({locals_map[key]: value for key, value in changes.items()})
        return result

    locals_map = {"image": image, "context": context, "displayed": displayed_page, "delivery": delivered_image,
                  "page": page, "text": text, "note": note, "outcome": outcome, "confirmed": confirmed,
                  "candidate": candidate, "history": historical, "counts": counts, "metrics": metrics, "saved": saved_path}

    def admitted(state, selected, displayed, captured):
        return (type(captured) is str and captured == state.context and state.snapshot is not None
                and type(selected) is int and selected == state.page and type(displayed) is int
                and displayed == state.page)

    def current(state):
        return audit.validate_audit(state.snapshot, document, parent_audit_sha256=parent)

    def changed(state, notice, **changes):
        return response(state, notice, context=state.context, confirmed=False, candidate="", metrics=None, saved="", **changes)

    def create_spot_audit(state, captured, minimum, requested):
        with state.lock:
            if type(captured) is not str or captured != state.context or state.snapshot is not None:
                return response(state, _STALE)
            try:
                workspace.verify_inputs()
                if type(minimum) not in (int, float) or not 0 <= minimum <= 1:
                    raise ValueError("invalid confidence threshold")
                value = (audit.validate_audit(initial, document, parent_audit_sha256=parent) if initial is not None else
                         audit.create_audit(document, threshold=float(minimum), sample_size=requested,
                                            seed=secrets.token_hex(32), parent_audit_sha256=parent))
                workspace.verify_inputs()
                state.snapshot = value
                state.rotate(view=True)
                return changed(state, "Frozen sample restored without image, confirmation or scores." if initial is not None else
                               "Sample frozen. Select a sampled page, then Open original page.", image=None, delivery="",
                               displayed=None, page=gr.update(choices=[(f"Page {n}", n) for n in value["plan"]["selected_pages"]], value=None),
                               text="", note="", outcome="reviewed", counts=_coverage(value), history=value["records"])
            except Exception:
                return response(state, _FAILED)

    def select_spot_page(state, selected, displayed, captured, raw_text):
        with state.lock:
            if (type(captured) is not str or captured != state.context or state.snapshot is None
                    or type(selected) is not int or selected not in state.snapshot["plan"]["selected_pages"]
                    or (state.page is not None and (type(displayed) is not int or displayed != state.page))):
                return response(state, _STALE)
            if selected == state.page:
                return response(state, "Page selection unchanged.")
            try:
                workspace.verify_inputs()
                value = current(state)
                if state.page is not None:
                    value = audit.remember_draft(value, document, page_number=state.page, text=raw_text)
                workspace.verify_inputs()
                state.snapshot, state.page = value, selected
                state.rotate(view=True)
                draft = next(row["text"] for row in value["drafts"] if row["page_number"] == selected)
                state.fields_digest = _fields_digest(draft, "", "reviewed")
                return changed(state, "Page selected. Open the original before reviewing.", image=None, delivery="",
                               displayed=selected, text=draft, note="", outcome="reviewed", counts=_coverage(value))
            except Exception:
                state.rotate(view=True)
                return changed(state, _FAILED, image=None, delivery="")

    def edit_spot_fields(state, selected, displayed, captured, raw_text, raw_note, choice):
        with state.lock:
            if not admitted(state, selected, displayed, captured):
                return response(state, _STALE)
            try:
                digest = _fields_digest(raw_text, raw_note, choice)
            except (ValueError, UnicodeError):
                digest = None
            if digest is not None and digest == state.fields_digest:
                return {session: state}
            state.fields_digest = digest  # A digest only; late field events never author another page's draft.
            state.rotate()
            return changed(state, "Fields changed; confirmation and scores cleared. Store outcomes explicitly.")

    def open_spot_page(state, selected, displayed, captured):
        rendered, transferred = None, False
        try:
            with state.lock:
                if not admitted(state, selected, displayed, captured):
                    return response(state, _STALE)
                state.rotate(view=True)
                try:
                    workspace.verify_inputs()
                    value = current(state)
                    try:
                        rendered = workspace.render_original_page(selected)
                        _image_identity(rendered)
                    except Exception:
                        workspace.verify_inputs()
                        state.snapshot, state.failed_open = value, selected
                        return changed(state, "Original preview unavailable. Inputs still verify; an unavailable outcome needs a note. "
                                       "Typed text is retained. Open again to retry explicitly.", image=None, delivery="")
                    workspace.verify_inputs()
                    state.snapshot = value
                    state.image_token = secrets.token_hex(32)
                    result = changed(state, "Original page opened. Transcribe from the source, then confirm and Store outcome.",
                                     image=rendered, delivery=state.image_token)
                except Exception:
                    state.failed_open = None
                    return changed(state, _FAILED, image=None, delivery="")
            transferred = True  # Only after the session lock's finalizer also succeeded.
            return result
        finally:
            if not transferred or sys.exc_info()[0] is not None:
                _close_unreturned_image(rendered)

    def reset_spot_view(state, selected, displayed):
        with state.lock:
            # Explicit loss recovery, never a way to recover a missing image-delivery token.
            if (state.snapshot is None or type(selected) is not int or selected != state.page
                    or type(displayed) is not int or displayed != state.page):
                return response(state, _STALE)
            state.rotate(view=True)
            return changed(state, "View reset; typed fields were not changed. Open the original page again.", image=None, delivery="")

    def confirm_spot_review(state, selected, displayed, captured, delivery, raw_text, raw_note, choice, checked):
        with state.lock:
            if not admitted(state, selected, displayed, captured):
                return response(state, _STALE)
            state.rotate()
            try:
                if type(checked) is not bool:
                    raise ValueError("invalid confirmation")
                if not checked:
                    return changed(state, "Confirmation cleared.")
                if (choice != "reviewed" or type(delivery) is not str or not delivery
                        or delivery != state.image_token):
                    raise ValueError("original page not delivered")
                workspace.verify_inputs()
                digest = _fields_digest(raw_text, raw_note, choice)
                workspace.verify_inputs()
                state.confirmed_digest = digest
                state.fields_digest = digest
                return response(state, "This exact transcription is confirmed for Store outcome.", context=state.context,
                                confirmed=True, metrics=None)
            except Exception:
                return changed(state, "Confirmation refused. Open the original and check the current reviewed transcription.")

    def store_spot_outcome(state, selected, displayed, captured, delivery, raw_text, raw_note, choice, checked):
        with state.lock:
            if not admitted(state, selected, displayed, captured):
                return response(state, _STALE)
            confirmation = state.confirmed_digest
            state.rotate()
            try:
                digest = _fields_digest(raw_text, raw_note, choice)
                if choice == "unavailable":
                    if state.failed_open != selected or delivery != "" or checked is not False:
                        raise ValueError("unavailable requires a failed original Open")
                elif (type(delivery) is not str or not delivery or delivery != state.image_token
                      or (choice == "reviewed" and (checked is not True or digest != confirmation))
                      or (choice == "unresolved" and checked is not False)):
                    raise ValueError("review requires current image and confirmation")
                workspace.verify_inputs()
                value = audit.remember_draft(current(state), document, page_number=selected, text=raw_text)
                value = audit.record_review(value, document, page_number=selected, outcome=choice,
                                            text=raw_text if choice == "reviewed" else None, note=raw_note, confirmed=checked)
                workspace.verify_inputs()
                state.snapshot = value
                revealed = document.page(selected)["candidate"]["text"] if choice in {"reviewed", "unresolved"} else ""
                return response(state, "Outcome stored in this session, not yet saved to disk. Scores require explicit Score.",
                                context=state.context, confirmed=False, candidate=revealed, metrics=None,
                                history=value["records"], counts=_coverage(value))
            except Exception:
                return changed(state, "Outcome not stored. Check the current original view, transcription, outcome, note and confirmation.")

    def save_spot_audit(state, selected, displayed, captured, raw_text):
        with state.lock:
            if (state.snapshot is None or type(captured) is not str or captured != state.context
                    or (state.page is not None and not admitted(state, selected, displayed, captured))):
                return response(state, _STALE)
            state.rotate()
            try:
                workspace.verify_inputs()
                value = current(state)
                if state.page is not None:
                    value = audit.remember_draft(value, document, page_number=state.page, text=raw_text)
                workspace.verify_inputs()
                state.snapshot = value
                destination = workspace.save("audit", copy.deepcopy(value))
                workspace.verify_inputs()
                return response(state, "A new private audit snapshot was saved, including unfinished transcription. "
                                "Notes/outcomes require Store outcome; no approval is restored on reopen.",
                                context=state.context, confirmed=False, candidate="", metrics=None,
                                saved=str(destination), counts=_coverage(value))
            except Exception:
                return changed(state, "Save could not be confirmed. A private snapshot may exist; no automatic retry. "
                               "Typed text is retained; check the fixed inputs.")

    def score_spot_audit(state, selected, displayed, captured):
        with state.lock:
            if (state.snapshot is None or type(captured) is not str or captured != state.context
                    or (state.page is not None and not admitted(state, selected, displayed, captured))):
                return response(state, _STALE)
            state.rotate()
            try:
                workspace.verify_inputs()
                result = audit.audit_summary(current(state), document)
                workspace.verify_inputs()
                return response(state, "Only stored reviewed references were scored. Unresolved/unavailable/pending and unsampled pages remain separate.",
                                context=state.context, confirmed=False, metrics=result)
            except Exception:
                return changed(state, "Scoring unavailable. Stored reviews and drafts are retained; no zero score was substituted.")

    capture = [session, page, displayed_page, context]
    create.click(create_spot_audit, [session, context, threshold, sample_size], outputs, **options)
    page.input(select_spot_page, [*capture, text], outputs, **options)
    open_page.click(open_spot_page, capture, outputs, **options)
    reset.click(reset_spot_view, [session, page, displayed_page], outputs, **options)
    for field in (text, note, outcome):
        field.change(edit_spot_fields, [*capture, text, note, outcome], outputs, **options)
    reference = [*capture, delivered_image, text, note, outcome, confirmed]
    confirmed.input(confirm_spot_review, reference, outputs, **options)
    store.click(store_spot_outcome, reference, outputs, **options)
    save.click(save_spot_audit, [*capture, text], outputs, **options)
    score.click(score_spot_audit, capture, outputs, **options)
