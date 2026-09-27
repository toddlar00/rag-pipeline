"""Optional private Gradio controls for fixed, contained review executions.

The coordinator owns execution and artifact validation. This panel owns only
captured-view consent and bounded presentation; it never replaces the document.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
from uuid import uuid4

from ocr_hardscan import validate_recipe
from ocr_preprocessing import PREPROCESSING_MODES
from ocr_review import text_difference
from ocr_review_crop_ui import build_crop_review_panel


_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_DISPLAY_LIMIT = 100_000
_RESULT_LIMIT = 2 * 1024 * 1024
_STALE = "Execution view changed. Prepare again, inspect the scope and confirm again. No new OCR was started."
_FAILURE = "Run control failed. Inspect status before retrying; retained artifacts and the saved baseline are unchanged."


class _PanelState:
    """Small session-local mutable registry, never a draft or a report cache."""

    def __init__(self):
        self.lock = threading.RLock()
        self.generation = uuid4().hex
        self.pending = None
        self.runs = ()
        self.display_run = None
        self.result_generation = uuid4().hex

    def __deepcopy__(self, memo):
        # Gradio copies initial State separately for every new browser session.
        # Neither execution approval nor run capabilities migrate into a copy.
        return type(self)()

    def revoke(self):
        self.generation = uuid4().hex
        self.pending = None


def _identifier(value):
    if type(value) is not str or _ID.fullmatch(value) is None:
        raise ValueError("invalid run identifier")
    return value


def _json(value, *, maximum):
    encoder = json.JSONEncoder(ensure_ascii=False, sort_keys=True, allow_nan=False)
    parts, count = [], 0
    for part in encoder.iterencode(value):
        count += len(part.encode("utf-8"))
        if count > maximum:
            raise ValueError("run presentation exceeds its bound")
        parts.append(part)
    return "".join(parts)


def _text(value):
    if type(value) is not str or len(value) > _DISPLAY_LIMIT:
        raise ValueError("run text exceeds its display bound")
    return value


def _selection(workspace, state, context, operation, page_scope, pages, dpi,
               preprocessing, orientation, illumination, bow, bow_assumption):
    if (type(state) is not dict or type(context) is not str or not context
            or state.get("annotation_context") != context):
        raise ValueError("stale annotation view")
    page = state.get("page")
    if type(page) is not int or not 1 <= page <= workspace.document.page_count:
        raise ValueError("invalid current page")
    if operation not in ("pages", "regions", "hardscan") or type(dpi) is not int or dpi not in (300, 400):
        raise ValueError("invalid run configuration")
    if type(page_scope) is not str or page_scope not in ("current", "explicit"):
        raise ValueError("invalid page scope")
    if pages is None:
        pages = []
    if (type(pages) is not list or len(pages) > 20 or any(type(n) is not int
            or not 1 <= n <= workspace.document.page_count for n in pages) or len(set(pages)) != len(pages)):
        raise ValueError("invalid explicit page selection")
    if type(preprocessing) is not str or preprocessing not in PREPROCESSING_MODES:
        raise ValueError("invalid preprocessing")
    if (type(orientation) is not int or orientation not in (0, 90, 180, 270)
            or type(illumination) is not str or illumination not in ("none", "background-normalize-v1")
            or type(bow) not in (int, float) or not -.025 <= bow <= .025
            or type(bow_assumption) is not str or bow_assumption not in ("none", "parallel_horizontal_baselines")):
        raise ValueError("invalid hard-scan controls")
    recipe = {"orientation_clockwise": orientation, "illumination": illumination,
              "bow_fraction": bow, "bow_assumption": bow_assumption}
    if operation == "hardscan":
        recipe = validate_recipe(recipe)
    regions = state.get("regions")
    if type(regions) is not list or len(regions) > 20:
        raise ValueError("invalid draft regions")
    # Bind every displayed control and current draft scope, even when a control
    # does not apply to this route. Changing away and back still revokes consent.
    captured = {"context": context, "page": page, "regions": regions, "operation": operation,
                "page_scope": page_scope, "pages": pages, "dpi": dpi,
                "preprocessing": preprocessing, "recipe": recipe}
    fingerprint = hashlib.sha256(_json(captured, maximum=32_768).encode("utf-8")).hexdigest()
    if operation == "pages":
        selected = [page] if page_scope == "current" else sorted(pages)
        if not selected:
            raise ValueError("select at least one page")
        options = {"operation": operation, "pages": selected, "dpi": dpi, "preprocessing": preprocessing}
        scope = {"pages": selected}
    else:
        rows = workspace.region_plan(copy.deepcopy(regions))["regions"]
        if operation == "hardscan":
            rows = [{**row, "recipe": dict(recipe)} for row in rows]
        options = {"operation": operation, "regions": rows, "dpi": dpi, "preprocessing": "none"}
        scope = {"regions": rows}
    return fingerprint, options, scope


def _run_text(view):
    if type(view) is not dict or view.get("phase") not in ("starting", "running", "terminal"):
        raise ValueError("invalid run status")
    _identifier(view.get("run_id"))
    if view.get("operation") not in ("pages", "regions", "hardscan"):
        raise ValueError("invalid run operation")
    if view.get("process_status") not in ("not_started", "running", "completed", "cancelled", "timed_out",
                                           "failed", "cleanup_unconfirmed"):
        raise ValueError("invalid process status")
    if view.get("artifact_state") not in ("absent", "incomplete", "present_unverified", "verified_complete"):
        raise ValueError("invalid artifact status")
    calls = view.get("actual_calls")
    if calls is not None and (type(calls) is not int or not 0 <= calls <= 20):
        raise ValueError("invalid actual call count")
    if type(view.get("cancellation_requested")) is not bool or view.get("requires_attention") is not True:
        raise ValueError("invalid run attention state")
    # No exception string, filesystem path, or arbitrary backend notice is shown.
    return (f"Run {view['run_id']}: {view['phase']}; process {view['process_status']}; "
            f"last observed artifacts {view['artifact_state']}; cancellation requested {view['cancellation_requested']}; "
            f"last verified actual calls {'unknown' if calls is None else calls}. "
            "Status does not recheck files. Load verified results performs fresh verification. "
            "A completed process is not verified text or approval.")


def _candidate_text(record):
    if record is None:
        return None
    if type(record) is not dict:
        raise ValueError("invalid selected record")
    candidate = record.get("candidate")
    if candidate is None:
        return None
    if type(candidate) is not dict:
        raise ValueError("invalid selected candidate")
    return _text(candidate.get("text"))


def build_execution_panel(workspace, coordinator, *, review_state, annotation_view, pack_service=None, uncertainty_review=False):
    """Build inside Blocks. No paths, capabilities or callbacks come from JSON.

    Only a coordinator supplied by the fixed host enables this panel. Cancel and
    status do not depend on annotation consent, preparation or result queue work.
    """
    import gradio as gr

    def invalidate_run(session):
        with session.lock:
            session.revoke()
            coordinator.invalidate_pending()
        return "", False, "Scope or configuration changed. Prepare a new run."

    def change_route(operation, page_scope, session):
        cleared = invalidate_run(session)
        if operation not in ("pages", "regions", "hardscan") or page_scope not in ("current", "explicit"):
            raise gr.Error(_STALE)
        return (*cleared, gr.Radio(visible=operation == "pages"),
                gr.Dropdown(visible=operation == "pages" and page_scope == "explicit"),
                gr.Dropdown(visible=operation == "pages"), gr.Row(visible=operation == "hardscan"))

    def prepare_run(session, state, context, *controls):
        try:
            fingerprint, options, scope = _selection(workspace, state, context, *controls)
            with session.lock:
                session.revoke()
                generation = session.generation
            prepared = coordinator.prepare(**options)
            if (type(prepared) is not dict or prepared.get("status") != "awaiting_approval"
                    or prepared.get("operation") != options["operation"]
                    or prepared.get("selection") != scope or prepared.get("requires_attention") is not True):
                raise ValueError("prepared scope differs")
            intent, approval = _identifier(prepared.get("intent_id")), _identifier(prepared.get("approval_token"))
            preview = _json({"operation": prepared["operation"], "selection": prepared["selection"],
                             "configuration": prepared["configuration"],
                             "source_sha256": workspace.document.source_sha256,
                             "baseline_recovery_sha256": workspace.document.recovery_sha256}, maximum=32_768)
            with session.lock:
                if session.generation != generation:
                    raise ValueError("preparation was superseded")
                token = uuid4().hex
                session.pending = (token, fingerprint, intent, approval)
            return token, False, preview
        except Exception:
            # Revocation never approves or starts a replacement. Do not expose
            # private exception messages or pretend a concurrent run was undone.
            with session.lock:
                session.revoke()
            raise gr.Error(_STALE) from None

    def start_run(session, state, context, token, confirmed, *controls):
        try:
            with session.lock:
                fingerprint, _, _ = _selection(workspace, state, context, *controls)
                pending = session.pending
                if (confirmed is not True or type(token) is not str or pending is None
                        or token != pending[0] or fingerprint != pending[1]):
                    raise ValueError("stale execution confirmation")
                session.revoke()  # Single use even when start fails after handoff.
                recovered = False
                try:
                    view = coordinator.start(pending[2], pending[3], confirmed=True)
                except (Exception, KeyboardInterrupt):
                    # A startup exception can follow actual worker handoff.
                    # Recover only this consumed, already-approved intent; no
                    # client-supplied lookup or enumeration grants run access.
                    try:
                        view = coordinator.status(pending[2])
                    except (Exception, KeyboardInterrupt):
                        raise gr.Error(_FAILURE) from None
                    recovered = True
                run_id = _identifier(view.get("run_id"))
                if run_id != pending[2]:
                    raise ValueError("returned run differs from approved intent")
                message = _run_text(view)
                if recovered:
                    message = ("Start returned no normal response; recovered this approved run's last observed status. "
                               "OCR may already have executed. " + message)
                session.runs = (*session.runs[-7:], run_id)
                session.display_run = run_id
                session.result_generation = uuid4().hex
                choices = list(session.runs)
                result_token = session.result_generation
            return ("", False, gr.Dropdown(choices=choices, value=run_id), message, result_token,
                    gr.Dropdown(choices=[], value=None), "", "", "", "")
        except Exception:
            raise gr.Error(_FAILURE) from None

    def permitted_run(session, run_id):
        run_id = _identifier(run_id)
        with session.lock:
            if run_id not in session.runs:
                raise ValueError("run is outside this session")
        return run_id

    def poll_run(session, run_id):
        try:
            return _run_text(coordinator.status(permitted_run(session, run_id)))
        except Exception:
            raise gr.Error(_FAILURE) from None

    def cancel_run(session, run_id):
        try:
            return _run_text(coordinator.cancel(permitted_run(session, run_id)))
        except Exception:
            raise gr.Error(_FAILURE) from None

    def select_run(session, run_id, view_context):
        try:
            run_id = permitted_run(session, run_id)
            with session.lock:
                if type(view_context) is not str or view_context != session.result_generation:
                    raise ValueError("stale run selection")
                session.display_run = run_id
                session.result_generation = uuid4().hex
                token = session.result_generation
            return gr.Dropdown(choices=[], value=None), token, "", "", "", "Load this run's verified result choices."
        except Exception:
            raise gr.Error(_FAILURE) from None

    def result_generation(session, run_id, view_context):
        with session.lock:
            if (session.display_run != run_id or type(view_context) is not str
                    or view_context != session.result_generation):
                raise ValueError("selected run changed")
            return session.result_generation

    def require_result_generation(session, run_id, generation):
        if result_generation(session, run_id, generation) != generation:
            raise ValueError("result view changed during readback")

    def require_result_binding(result, run_id):
        _json(result, maximum=_RESULT_LIMIT)
        if (type(result) is not dict or result.get("run_id") != run_id
                or result.get("source_sha256") != workspace.document.source_sha256
                or result.get("baseline_recovery_sha256") != workspace.document.recovery_sha256
                or result.get("operation") not in ("pages", "regions", "hardscan")
                or result.get("requires_attention") is not True):
            raise ValueError("result generation differs")

    def load_results(session, run_id, view_context):
        try:
            run_id = permitted_run(session, run_id)
            generation = result_generation(session, run_id, view_context)
            result = coordinator.result(run_id)
            require_result_binding(result, run_id)
            if result.get("selected_item") is not None:
                raise ValueError("result binding differs")
            items = result["item_ids"]
            maximum = workspace.document.page_count if result["operation"] == "pages" else 20
            if type(items) is not list or len(items) > maximum:
                raise ValueError("result choices exceed bounds")
            choices, seen = [], set()
            for item in items:
                identifier = _identifier(item["item_id"])
                page = item["page_number"]
                if (identifier in seen or type(page) is not int or not 1 <= page <= workspace.document.page_count):
                    raise ValueError("invalid result choice")
                seen.add(identifier)
                label = f"Page {page} / {identifier}"
                choices.append((label, identifier))
            with session.lock:
                require_result_generation(session, run_id, generation)
                session.result_generation = uuid4().hex
                token = session.result_generation
            # The opaque token and choices are one response. A late load or
            # selection captured before it cannot reinstall an older view.
            return gr.Dropdown(choices=choices, value=None), token, "", "", "", (
                "Verified artifact bundle loaded. Select an item; verification does not approve its text.")
        except Exception:
            raise gr.Error(_FAILURE) from None

    def show_result(session, run_id, result_context, item_id):
        try:
            run_id = permitted_run(session, run_id)
            generation = result_generation(session, run_id, result_context)
            _identifier(item_id)
            result = coordinator.result(run_id, item_id=item_id)
            require_result_binding(result, run_id)
            selected = result["selected_item"]
            diagnostic = selected["diagnostic"]
            if diagnostic.get("item_id") != item_id:
                raise ValueError("selected diagnostic differs")
            candidate = _candidate_text(selected["report_record"])
            baseline = selected["baseline_page"]
            saved_candidate = _candidate_text(baseline)
            original = None if baseline is None else baseline.get("original_text")
            if original is not None:
                original = _text(original)
            baseline_text = saved_candidate if saved_candidate is not None else original
            operation = result["operation"]
            if operation not in ("pages", "regions", "hardscan"):
                raise ValueError("invalid result route")
            if operation == "pages" and saved_candidate is not None and candidate is not None:
                difference = text_difference(saved_candidate, candidate)
            else:
                difference = ("No paired page-candidate diff is available." if operation == "pages" else
                              "Crop comparison is unavailable: saved whole-page context is not a crop baseline or reference.")
            record = selected["report_record"]
            outcome = diagnostic["legacy_status"]
            if outcome not in ("not_selected", "deferred", "review_required", "empty_candidate", "retry_failed", "abstained"):
                raise ValueError("invalid selected outcome")
            page = diagnostic["page_number"]
            if (type(page) is not int or not 1 <= page <= workspace.document.page_count
                    or record is not None and (record.get("page_number") != page or record.get("status") != outcome)
                    or baseline is not None and baseline.get("page_number") != page
                    or (candidate is not None) != (outcome in ("review_required", "empty_candidate"))):
                raise ValueError("selected outcome or page differs")
            require_result_generation(session, run_id, generation)
            notice = (f"Run {run_id} / {operation} / {item_id}: {outcome}. "
                      f"Candidate {'unavailable' if candidate is None else 'empty' if not candidate.strip() else 'available'}. "
                      f"Saved context: {'OCR candidate' if saved_candidate is not None else 'original extraction' if original is not None else 'unavailable'}. "
                      "No reference-backed accuracy score. No canonical text was replaced and no correction was approved.")
            return baseline_text or "", candidate or "", difference, notice
        except Exception:
            raise gr.Error(_FAILURE) from None

    with gr.Tab("Run OCR review"):
        gr.Markdown("Prepare a bounded local run, inspect its exact scope, then confirm execution. "
                    "Only the host's fixed source and private output area are available. Results never replace the saved baseline. "
                    "One run coordinator is shared by this host; another tab can supersede preparation.")
        session_state = gr.State(_PanelState())
        with gr.Row():
            operation = gr.Radio(["pages", "regions", "hardscan"], value="pages", label="OCR operation")
            page_scope = gr.Radio(["current", "explicit"], value="current", label="Page scope (pages only)")
            pages = gr.Dropdown(list(range(1, workspace.document.page_count + 1)), value=[], multiselect=True,
                                max_choices=20, allow_custom_value=False, visible=False,
                                label="Explicit pages (at most 20)")
        with gr.Row():
            dpi = gr.Dropdown([300, 400], value=300, allow_custom_value=False, label="Run DPI")
            preprocessing = gr.Dropdown(list(PREPROCESSING_MODES), value="none", allow_custom_value=False,
                                       label="Page preprocessing (pages only)")
        gr.Markdown("Region operations use all currently stored draft crops, not an unadded rectangle. "
                    "Page preprocessing does not apply to crops. "
                    "Hard-scan controls below apply the same explicit recipe to every draft crop.")
        with gr.Row(visible=False) as hardscan_controls:
            orientation = gr.Dropdown([0, 90, 180, 270], value=0, label="Hard-scan clockwise orientation")
            illumination = gr.Dropdown(["none", "background-normalize-v1"], value="none", label="Hard-scan illumination")
            bow = gr.Number(value=0.0, minimum=-.025, maximum=.025, label="Hard-scan bow fraction")
            assumption = gr.Dropdown(["none", "parallel_horizontal_baselines"], value="none",
                                     label="Hard-scan bow applicability assumption")
        prepare = gr.Button("Prepare exact OCR scope")
        preview = gr.Textbox(label="Prepared scope and configuration", interactive=False, max_length=32_768)
        approval = gr.Checkbox(value=False, label="I reviewed this prepared scope and authorize this one OCR execution")
        prepared_view = gr.Textbox(value="", visible=False, interactive=False)
        start = gr.Button("Start confirmed OCR run")
        with gr.Row():
            runs = gr.Dropdown([], value=None, allow_custom_value=False, label="This session's runs (at most 8)")
            poll = gr.Button("Refresh run status")
            cancel = gr.Button("Cancel selected run", variant="stop")
            load = gr.Button("Load verified result choices")
        status = gr.Textbox(label="Run status", interactive=False)
        result_context = gr.Textbox(value="", visible=False, interactive=False)
        items = gr.Dropdown([], value=None, allow_custom_value=False, label="Verified result item")
        with gr.Row():
            baseline = gr.Textbox(label="Immutable saved page context (not crop ground truth)", interactive=False)
            candidate = gr.Textbox(label="New candidate (never automatically adopted)", interactive=False)
        difference = gr.Textbox(label="Text difference only; not an accuracy score", interactive=False)
        result_notice = gr.Textbox(label="Result scope and availability", interactive=False)
        controls = [operation, page_scope, pages, dpi, preprocessing, orientation, illumination, bow, assumption]
        pack_options = {} if pack_service is None else {"pack_service": pack_service}
        if uncertainty_review:
            pack_options["uncertainty"] = True
        build_crop_review_panel(workspace, coordinator, execution_state=session_state, review_state=review_state,
            annotation_view=annotation_view, runs=runs, items=items, result_context=result_context, controls=controls,
            **pack_options)

    private = {"api_visibility": "private", "show_progress": "hidden"}
    for control in [*controls[2:], annotation_view]:
        listener = control.change if control is annotation_view else control.input
        listener(invalidate_run, [session_state], [prepared_view, approval, preview], queue=False, **private)
    for control in (operation, page_scope):
        control.input(change_route, [operation, page_scope, session_state],
                      [prepared_view, approval, preview, page_scope, pages, preprocessing, hardscan_controls],
                      queue=False, **private)
    prepare.click(prepare_run, [session_state, review_state, annotation_view, *controls],
                  [prepared_view, approval, preview], concurrency_id="ocr-review-prepare", concurrency_limit=1, **private)
    start.click(start_run, [session_state, review_state, annotation_view, prepared_view, approval, *controls],
                [prepared_view, approval, runs, status, result_context,
                 items, baseline, candidate, difference, result_notice], queue=False, **private)
    poll.click(poll_run, [session_state, runs], [status], queue=False, **private)
    cancel.click(cancel_run, [session_state, runs], [status], queue=False, **private)
    load.click(load_results, [session_state, runs, result_context],
               [items, result_context, baseline, candidate, difference, result_notice],
               concurrency_id="ocr-review-results", concurrency_limit=1, **private)
    runs.input(select_run, [session_state, runs, result_context],
               [items, result_context, baseline, candidate, difference, result_notice],
               queue=False, **private)
    items.input(show_result, [session_state, runs, result_context, items], [baseline, candidate, difference, result_notice],
                concurrency_id="ocr-review-results", concurrency_limit=1, **private)
