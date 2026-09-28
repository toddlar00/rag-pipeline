"""Source-bound context authoring in the existing reference tab.

Draft content is separate from session-only review declarations and file bindings.
Captured view stamps guard queued actions; delayed input events can only revoke.
"""

from __future__ import annotations

import copy
import hashlib
import json
from uuid import uuid4

from ocr_context_authoring import (
    build_correspondence, build_reference, empty_authoring, preview_check,
    result_target, selection_span, validate_authoring,
)
from ocr_context_evaluation import MAX_CANDIDATE_CHARACTERS, _integer as _context_integer
from ocr_review import click_rectangle
from ocr_review_runtime import ReviewDownloadError, draw_overlay


def prepare_context_state(state):
    state.setdefault("context_authoring", empty_authoring())
    state["_context_ui"] = {"owner": None, "view": uuid4().hex, "display": uuid4().hex,
                            "reference_reviews": {}, "mapping_reviews": {},
                            "exports": {}, "source_view": None, "first_corner": None,
                            "result_choices": [], "result_summary": "", "pending_source_fields": {}}


def _selected(state):
    draft = state["context_authoring"]
    context = next((item for item in draft["contexts"] if item["context_id"] == draft["selected_context_id"]), None)
    check = None if context is None else next((item for item in context["checks"]
                                              if item["check_id"] == draft["selected_check_id"]), None)
    return context, check


def context_stamp(state):
    session, draft = state["_context_ui"], state["context_authoring"]
    return [session["view"], session["display"], draft["selected_context_id"] or "",
            draft["selected_check_id"] or ""]


def browser_selection_span(text, index, value):
    """Join native textarea newline normalization to the original raw offsets."""
    if type(text) is not str or len(text) > MAX_CANDIDATE_CHARACTERS:
        raise ValueError("The selected text exceeds the context authoring limit")
    if "\r" not in text:
        return selection_span(text, index, value)
    normalized, boundaries, offset = [], [0], 0
    while offset < len(text):
        character = text[offset]
        offset += 1
        if character == "\r":
            if offset < len(text) and text[offset] == "\n":
                offset += 1
            character = "\n"
        normalized.append(character)
        boundaries.append(offset)
    start, end = selection_span("".join(normalized), index, value)
    return [boundaries[start], boundaries[end]]


def _integer(value):
    if type(value) not in (int, float) or not float(value).is_integer():
        raise ValueError("Enter a whole-number offset")
    return int(value)


def _span(start, end):
    if start is None and end is None:
        return None
    return [_integer(start), _integer(end)]


def _preserve_displayed_newlines(value, original):
    # A native textarea cannot express a change in newline encoding alone.
    return original if value == original or value == original.replace("\r\n", "\n").replace("\r", "\n") else value


class ContextAuthoring:
    """One bounded controller; workspace owns all file and source verification."""

    def __init__(self, workspace):
        self.workspace = workspace
        self.document = workspace.document

    def require_view(self, state, stamp):
        if stamp != context_stamp(state):
            raise ValueError("The context view changed. Reload it, inspect it and confirm again")
        self.workspace.verify_inputs()

    def _revoke(self, state, *, source=False, mapping_only=False):
        session = state["_context_ui"]
        identifier = state["context_authoring"]["selected_context_id"]
        if not mapping_only:
            session["reference_reviews"].pop(identifier, None)
        session["mapping_reviews"].pop(identifier, None)
        session["exports"] = {key: value for key, value in session["exports"].items()
                              if mapping_only and key == "reference"}
        session["result_choices"], session["result_summary"] = [], ""
        session["view"] = uuid4().hex
        if source:
            session["source_view"] = session["first_corner"] = None

    def input(self, state, *, mapping_only=False):
        state = {**state, "_context_ui": copy.deepcopy(state["_context_ui"])}
        self._revoke(state, mapping_only=mapping_only)
        return state

    def sync(self, state, display):
        # Never issue a new edit stamp for a form whose structural response was lost.
        session = state["_context_ui"]
        return session["view"] if display == session["display"] else "", False, False, False, False

    def _commit(self, state, form):
        context, check = _selected(state)
        if context is None:
            return
        (text, category, start, end, left, right, mapping, candidate_start,
         candidate_end, kind, row, column, _page) = form
        before = copy.deepcopy(context)
        text = _preserve_displayed_newlines(text, context["reference"])
        changed_text = text != context["reference"]
        if changed_text:
            context["reference"] = text
            for item in context["checks"]:
                item.update(reference_span=None, left_anchor="", right_anchor="")
            context["correspondence"] = {"status": "unreviewed", "candidate_span": None}
        if check is not None:
            check["category"] = category
            if not changed_text:
                check.update(reference_span=_span(start, end),
                             left_anchor=_preserve_displayed_newlines(left, check["left_anchor"]),
                             right_anchor=_preserve_displayed_newlines(right, check["right_anchor"]))
        if not changed_text:
            context["correspondence"] = {"status": mapping, "candidate_span":
                                         _span(candidate_start, candidate_end) if mapping == "mapped" else None}
        pending = state["_context_ui"]["pending_source_fields"]
        source_changed = False
        if context["source_anchor"] is None:
            if type(kind) is not str or kind not in ("sentence", "region", "cell"):
                raise ValueError("Select a valid source kind")
            fields = {"kind": kind, "cell": {
                key: None if value is None else _context_integer(_integer(value), 1, 100_000)
                for key, value in (("row", row), ("column", column))}}
            source_changed = fields != pending.get(context["context_id"],
                {"kind": "sentence", "cell": {"row": 1, "column": 1}})
            pending[context["context_id"]] = fields
            if source_changed:
                context["correspondence"] = {"status": "unreviewed", "candidate_span": None}
        else:
            pending.pop(context["context_id"], None)
            context["source_anchor"].update(kind=kind, cell={"row": _integer(row), "column": _integer(column)}
                                             if kind == "cell" else None)
            if context["source_anchor"] != before["source_anchor"]:
                context["correspondence"] = {"status": "unreviewed", "candidate_span": None}
        if source_changed or context != before:
            self._revoke(state, mapping_only=not source_changed and all(
                context[key] == before[key] for key in context if key != "correspondence"))
            return True
        return False

    def _digest(self, context, *, mapping=False):
        content = context if mapping else {key: value for key, value in context.items() if key != "correspondence"}
        return hashlib.sha256(json.dumps([self.document.source_sha256, self.document.recovery_sha256, content],
                                         ensure_ascii=False, sort_keys=True, allow_nan=False).encode("utf-8")).hexdigest()

    def _reviewed(self, state, *, mapping=False):
        reviews = state["_context_ui"]["mapping_reviews" if mapping else "reference_reviews"]
        contexts = state["context_authoring"]["contexts"]
        if not contexts or any(reviews.get(item["context_id"]) != self._digest(item, mapping=mapping) for item in contexts):
            raise ValueError("Review every current context before exporting this cohort")

    def candidate_text(self, page):
        candidate = self.document.page(page)["candidate"]
        return None if candidate is None else candidate["text"]

    def _image(self, state):
        context, _ = _selected(state)
        original = self.workspace.render_original_page(context["page_number"])
        try:
            image = draw_overlay(original, None, region_outline=None if context["source_anchor"] is None
                                 else context["source_anchor"]["bbox"])
            state["_context_ui"]["source_view"] = [context["context_id"], context["page_number"], *original.size]
            return image
        finally:
            original.close()

    def transition(self, state, stamp, form, action, argument=None):
        self.require_view(state, stamp)
        incoming_state = state
        state = copy.deepcopy(state)
        session, draft = state["_context_ui"], state["context_authoring"]
        session["owner"] = session["owner"] or uuid4().hex
        context, _ = _selected(state)
        if context is not None and action != "page" and _integer(form[12]) != context["page_number"]:
            raise ValueError("Set the context page before acting on its changed page control")
        changed = self._commit(state, form)
        if changed and action in ("review_reference", "review_mapping", "export_reference", "export_mapping", "evaluate", "result"):
            raise ValueError("Store pending changes, inspect the updated context and confirm again")
        context, check = _selected(state)
        image, replace_image, file_path, notice = None, False, None, "Context draft stored."
        unchanged_selection = False
        try:
            if action == "new_context":
                context = {"context_id": "ctx-" + uuid4().hex, "page_number": state["page"],
                           "source_anchor": None, "reference": "", "checks": [],
                           "correspondence": {"status": "unreviewed", "candidate_span": None}}
                draft["contexts"].append(context)
                draft.update(selected_context_id=context["context_id"], selected_check_id=None)
                self._revoke(state, source=True)
                replace_image = True
            elif action in ("select_context", "delete_context"):
                if action == "delete_context":
                    self._revoke(state, source=True)
                    session["pending_source_fields"].pop(draft["selected_context_id"], None)
                    draft["contexts"] = [item for item in draft["contexts"] if item is not context]
                    argument = draft["contexts"][0]["context_id"] if draft["contexts"] else None
                draft.update(selected_context_id=argument or None, selected_check_id=None)
                session["source_view"] = session["first_corner"] = None
                replace_image = True
            elif context is None:
                if action != "store":
                    raise ValueError("Create or select a context first")
            elif action == "page":
                page = _integer(argument)
                if page != context["page_number"]:
                    session["pending_source_fields"].pop(context["context_id"], None)
                    context.update(page_number=page, source_anchor=None,
                                   correspondence={"status": "unreviewed", "candidate_span": None})
                    self._revoke(state, source=True)
                    replace_image = True
            elif action == "new_check":
                check = {"check_id": "check-" + uuid4().hex, "category": "number", "reference_span": None,
                         "left_anchor": "", "right_anchor": ""}
                context["checks"].append(check)
                draft["selected_check_id"] = check["check_id"]
                self._revoke(state)
            elif action == "select_check":
                draft["selected_check_id"] = argument or None
            elif action == "delete_check":
                context["checks"] = [item for item in context["checks"] if item is not check]
                draft["selected_check_id"] = None
                self._revoke(state)
            elif action == "reference_selection":
                if check is None:
                    raise ValueError("Create or select a check before selecting its reference occurrence")
                check["reference_span"] = browser_selection_span(context["reference"], *argument)
                proposal = preview_check(context, check["check_id"])
                check.update(left_anchor=proposal["proposed_left_anchor"], right_anchor=proposal["proposed_right_anchor"])
                self._revoke(state)
                notice = "Occurrence selected. Inspect the anchors and their exact-match diagnostic."
            elif action == "candidate_selection":
                candidate = self.candidate_text(context["page_number"])
                if candidate is None:
                    raise ValueError("This page has no saved candidate text")
                index, selected, displayed = argument
                if type(displayed) is not str or _preserve_displayed_newlines(displayed, candidate) != candidate:
                    raise ValueError("The displayed candidate differs from the saved source-bound text")
                correspondence = {"status": "mapped", "candidate_span":
                                  browser_selection_span(candidate, index, selected)}
                unchanged_selection = not changed and context["correspondence"] == correspondence
                context["correspondence"] = correspondence
                if not unchanged_selection:
                    self._revoke(state, mapping_only=True)
            elif action == "open_source":
                image, replace_image = self._image(state), True
                session["first_corner"] = None
                notice = "Original source page opened. Select two corners around this context."
            elif action == "source_selection":
                viewed = session["source_view"]
                if viewed is None or viewed[:2] != [context["context_id"], context["page_number"]]:
                    raise ValueError("Open the original source preview for this context first")
                first = session["first_corner"]
                if first is None:
                    # Validate even the first click through the same finite coordinate policy.
                    if not isinstance(argument, (list, tuple)) or len(argument) != 2:
                        raise ValueError("Select a point on the original source preview")
                    partner = [0 if coordinate else 1 for coordinate in argument]
                    click_rectangle(argument, partner, width=viewed[2], height=viewed[3])
                    session["first_corner"] = argument
                    notice = "First corner stored. Select the opposite corner."
                else:
                    context["source_anchor"] = {"kind": form[9], "bbox": click_rectangle(
                        first, argument, width=viewed[2], height=viewed[3]),
                                                "cell": {"row": _integer(form[10]), "column": _integer(form[11])}
                                                if form[9] == "cell" else None}
                    context["correspondence"] = {"status": "unreviewed", "candidate_span": None}
                    self._revoke(state)
                    session["first_corner"] = None
                    session["pending_source_fields"].pop(context["context_id"], None)
                    image, replace_image = self._image(state), True
                    notice = "Original source rectangle stored. Review the reference and correspondence."
            elif action in ("review_reference", "review_mapping"):
                if argument is not True or session["source_view"] is None or session["source_view"][:2] != [
                        context["context_id"], context["page_number"]]:
                    raise ValueError("Inspect the original source preview and explicitly confirm this context")
                single = {"contexts": [context], "selected_context_id": context["context_id"],
                          "selected_check_id": draft["selected_check_id"]}
                reference = build_reference(single, self.document, confirmed=True)
                mapping = action == "review_mapping"
                if mapping:
                    build_correspondence(single, self.document, reference, "0" * 64, confirmed=True)
                session["mapping_reviews" if mapping else "reference_reviews"][context["context_id"]] = self._digest(
                    context, mapping=mapping)
                notice = "Current correspondence reviewed." if mapping else "Current reference and checks reviewed."
            elif action in ("export_reference", "export_mapping"):
                if argument is not True:
                    raise ValueError("Explicitly confirm the current export cohort")
                self._reviewed(state)
                if action == "export_reference":
                    binding = self.workspace.save_context_reference(draft, confirmed=True, owner=session["owner"])
                    session["exports"] = {"reference": binding}
                else:
                    self._reviewed(state, mapping=True)
                    binding = self.workspace.save_context_correspondence(
                        draft, session["exports"].get("reference"), confirmed=True, owner=session["owner"])
                    session["exports"].update(mapping=binding)
                    session["exports"].pop("report", None)
                file_path, notice = str(binding.path), "Created a new source-bound context artifact."
                session["result_choices"], session["result_summary"] = [], ""
            elif action == "evaluate":
                exports = session["exports"]
                report, binding = self.workspace.evaluate_context_exports(exports.get("reference"), exports.get("mapping"),
                                                                          owner=session["owner"])
                exports["report"] = binding
                session["result_summary"] = json.dumps(report["coverage"], ensure_ascii=False, indent=2)
                session["result_choices"] = [
                    (f"{row['context_index']}.{check['check_index']} | Page {row['page_number']} | "
                     f"{check['category']} | {check['status']}: {check['reason']}",
                     f"{row['context_index']}:{check['check_index']}")
                    for row in report["contexts"] for check in row["checks"]]
                file_path, notice = str(binding.path), "Evaluation saved. Select a result ordinal to inspect its source."
            elif action == "result":
                if isinstance(argument, str):
                    if argument not in {value for _, value in session["result_choices"]}:
                        raise ValueError("Select a row from the current bound result")
                    argument = [int(value) for value in argument.split(":")]
                exports = session["exports"]
                bound = self.workspace.read_context_result(exports.get("reference"), exports.get("mapping"),
                                                           exports.get("report"), owner=session["owner"])
                target = result_target(bound["report"], bound["reference"], bound["correspondence"],
                                       {key: value for key, value in bound["bindings"].items() if key != "report_sha256"},
                                       *map(_integer, argument))
                draft.update(selected_context_id=target["context_id"], selected_check_id=target["check_id"])
                image, replace_image = self._image(state), True
                session["first_corner"] = None
                notice = json.dumps(target, ensure_ascii=False, indent=2)
            elif action != "store":
                raise ValueError("Unknown context authoring action")
            state["context_authoring"] = validate_authoring(draft, self.document)
            if unchanged_selection:
                return incoming_state, image, replace_image, file_path, notice
            session["view"], session["display"] = uuid4().hex, uuid4().hex
            return state, image, replace_image, file_path, notice
        except BaseException:
            if image is not None:
                image.close()
            raise


def mount_context_authoring(gr, workspace, state_component, initial, *, app):
    """Mount in the initially rendered reference tab; keep one main server State."""
    controller = ContextAuthoring(workspace)
    event = {"api_visibility": "private", "concurrency_limit": 1, "concurrency_id": "ocr-review"}
    gr.Markdown("### Context checks\nTranscribe a source sentence, region or cell, then select each occurrence to check. "
                "Review numbers, units, negation, names and footnote identifiers in context. "
                "A saved draft retains unfinished work; review confirmations are session-only.")
    stamps = [gr.Textbox(value=value, visible=False, label="Context " + name)
              for value, name in zip(context_stamp(initial), ("edit stamp", "display stamp", "displayed identifier", "displayed check"))]
    with gr.Row():
        contexts = gr.Dropdown(choices=[], label="Context to edit")
        add_context = gr.Button("New context")
        delete_context = gr.Button("Delete context")
        reload_view = gr.Button("Reload context view")
    with gr.Row():
        page = gr.Number(value=initial["page"], precision=0, minimum=1, maximum=workspace.document.page_count,
                         label="Context source page")
        set_page = gr.Button("Set context page")
        open_source = gr.Button("Open original source preview")
    source = gr.Image(type="pil", interactive=False, format="png", sources=[], label="Original source for context",
                      buttons=["fullscreen"])
    with gr.Row():
        kind = gr.Dropdown(choices=["sentence", "region", "cell"], value="sentence", label="Context source kind")
        row = gr.Number(value=1, precision=0, minimum=1, label="Cell row")
        column = gr.Number(value=1, precision=0, minimum=1, label="Cell column")
    text = gr.Textbox(label="Context reference text", lines=5, max_length=20_000)
    with gr.Row():
        checks = gr.Dropdown(choices=[], label="Context check to edit")
        add_check = gr.Button("New context check")
        delete_check = gr.Button("Delete context check")
    category = gr.Dropdown(choices=["number", "unit", "negation", "name", "footnote_identifier"],
                           value="number", label="Context check category")
    with gr.Row():
        start = gr.Number(precision=0, label="Reference occurrence start")
        end = gr.Number(precision=0, label="Reference occurrence end (exclusive)")
    gr.Markdown("Select an occurrence in the reference text. Offsets are raw Unicode code points; "
                "these fields also allow keyboard correction. Inspect proposed anchors before review.")
    left = gr.Textbox(label="Exact left anchor", max_length=256)
    right = gr.Textbox(label="Exact right anchor", max_length=256)
    diagnostic = gr.Textbox(label="Context anchor diagnostic", lines=5, interactive=False)
    candidate = gr.Textbox(label="Saved candidate text for context selection", lines=6, interactive=True,
                           elem_id="ocr-context-candidate", buttons=["copy"])
    # Gradio disables noninteractive Textboxes, which prevents keyboard selection.
    # Native readOnly preserves selection; every selection still binds full text server-side.
    readonly_js = """() => {
        const field = document.querySelector('#ocr-context-candidate textarea');
        if (field) {
            field.readOnly = true;
            field.setAttribute('aria-readonly', 'true');
            const label = field.closest('label');
            if (label) {
                field.id = 'ocr-context-candidate-input';
                label.htmlFor = field.id;
            }
        }
        return [];
    }"""
    for trigger in (app.load, candidate.change, candidate.focus):
        trigger(fn=None, js=readonly_js, queue=False, api_visibility="private")
    mapping = gr.Dropdown(choices=["unreviewed", "mapped", "missing", "ambiguous"], value="unreviewed",
                          label="Context correspondence status")
    with gr.Row():
        candidate_start = gr.Number(precision=0, label="Candidate context start")
        candidate_end = gr.Number(precision=0, label="Candidate context end (exclusive)")
    store = gr.Button("Store context changes")
    reference_confirm = gr.Checkbox(label="I checked this context reference and every check against the original source")
    review_reference = gr.Button("Review current context reference")
    mapping_confirm = gr.Checkbox(label="I checked this context correspondence against the source and saved candidate")
    review_mapping = gr.Button("Review current context correspondence")
    reference_cohort = gr.Checkbox(label="Export all current reviewed context references")
    export_reference = gr.Button("Export context reference")
    mapping_cohort = gr.Checkbox(label="Export all current reviewed context correspondence")
    export_mapping = gr.Button("Export context correspondence")
    evaluate = gr.Button("Evaluate exported context checks")
    result_summary = gr.Textbox(label="Context evaluation coverage", lines=6, interactive=False)
    result_selector = gr.Dropdown(choices=[], label="Context evaluation result to inspect")
    with gr.Row():
        context_ordinal = gr.Number(value=1, minimum=1, precision=0, label="Result context ordinal")
        check_ordinal = gr.Number(value=1, minimum=1, precision=0, label="Result check ordinal")
        result = gr.Button("Show context result source")
    download = gr.File(label="New context artifact", interactive=False)
    notice = gr.Textbox(label="Context review status", lines=5, interactive=False)
    form = [text, category, start, end, left, right, mapping, candidate_start, candidate_end, kind, row, column, page]
    confirmations = [reference_confirm, mapping_confirm, reference_cohort, mapping_cohort]
    outputs = [state_component, *stamps, contexts, checks, *form, candidate, diagnostic,
               *confirmations, source, download, notice, result_summary, result_selector]

    def render(state, image=None, replace_image=False, file_path=None, message="Context view reloaded."):
        produced_file = file_path is not None
        if produced_file:
            session = state["_context_ui"]
            binding = next((value for value in session["exports"].values() if str(value.path) == file_path), None)
            try:
                file_path = str(workspace.prepare_download(binding, download.GRADIO_CACHE, owner=session["owner"]))
            except ReviewDownloadError as exc:
                file_path, message = None, str(exc)
        context, check = _selected(state)
        draft = state["context_authoring"]
        span = (check or {}).get("reference_span") or [None, None]
        correspondence = (context or {}).get("correspondence", {"status": "unreviewed", "candidate_span": None})
        candidate_span = correspondence["candidate_span"] or [None, None]
        anchor = (context or {}).get("source_anchor") or state["_context_ui"]["pending_source_fields"].get(
            draft["selected_context_id"], {"kind": "sentence", "cell": None})
        cell = anchor["cell"] or {"row": 1, "column": 1}
        selected_page = initial["page"] if context is None else context["page_number"]
        candidate_text = None if context is None else controller.candidate_text(selected_page)
        diagnostic_text = "" if check is None else json.dumps(preview_check(context, check["check_id"]), ensure_ascii=False, indent=2)
        return [state, *context_stamp(state),
                gr.update(choices=[(f"{index}. Page {item['page_number']}", item["context_id"])
                                   for index, item in enumerate(draft["contexts"], 1)], value=draft["selected_context_id"]),
                gr.update(choices=[] if context is None else [(f"{index}. {item['category']}", item["check_id"])
                          for index, item in enumerate(context["checks"], 1)], value=draft["selected_check_id"]),
                "" if context is None else context["reference"], (check or {}).get("category", "number"),
                *span, (check or {}).get("left_anchor", ""), (check or {}).get("right_anchor", ""),
                correspondence["status"], *candidate_span, anchor["kind"], cell["row"], cell["column"], selected_page,
                candidate_text or "", diagnostic_text, False, False, False, False,
                image if replace_image else gr.skip(), file_path if produced_file else gr.skip(),
                message + (" This page has no saved candidate text." if context is not None and candidate_text is None else ""),
                state["_context_ui"]["result_summary"], gr.update(choices=state["_context_ui"]["result_choices"], value=None)]

    def apply_and_render(state, stamp, form, action, argument):
        response = controller.transition(state, stamp, form, action, argument)
        if response[0] is state:
            return [gr.skip() for _ in outputs]
        try:
            return render(*response)
        except BaseException:
            if response[1] is not None:
                response[1].close()
            raise

    def callback(action):
        def context_action(state, *values):
            try:
                argument = values[17:]
                argument = argument[0] if len(argument) == 1 else argument or None
                stamp = list(values[:4])
                draft = state["context_authoring"]
                if stamp == context_stamp(state) and (
                        action == "select_context" and argument == draft["selected_context_id"]
                        or action == "select_check" and argument == draft["selected_check_id"]
                        or action == "result" and argument is None):
                    return [gr.skip() for _ in outputs]
                return apply_and_render(state, stamp, list(values[4:17]), action, argument)
            except Exception:
                raise gr.Error("Context action failed. Check the current view, source and required fields, then try again. "
                               "Previously created artifacts are retained.") from None
        return context_action

    inputs = [state_component, *stamps, *form]
    for button, action, extra in (
        (add_context, "new_context", []), (delete_context, "delete_context", []),
        (set_page, "page", [page]), (open_source, "open_source", []), (add_check, "new_check", []),
        (delete_check, "delete_check", []), (store, "store", []),
        (review_reference, "review_reference", [reference_confirm]), (review_mapping, "review_mapping", [mapping_confirm]),
        (export_reference, "export_reference", [reference_cohort]), (export_mapping, "export_mapping", [mapping_cohort]),
        (evaluate, "evaluate", []), (result, "result", [context_ordinal, check_ordinal]),
    ):
        button.click(callback(action), [*inputs, *extra], outputs, **event)
    contexts.change(callback("select_context"), [*inputs, contexts], outputs, trigger_mode="once", **event)
    checks.change(callback("select_check"), [*inputs, checks], outputs, trigger_mode="once", **event)
    result_selector.change(callback("result"), [*inputs, result_selector], outputs, trigger_mode="once", **event)

    def select_callback(action):
        def context_selection(state, evt, *values):
            try:
                index = evt.index
                if (action != "source_selection" and (type(index) is list or type(index) is tuple) and len(index) == 2
                        and all(type(offset) is int for offset in index) and 0 <= index[0] <= index[1]
                        and type(evt.value) is str):
                    # Native caret/fill events are not occurrence or mapping decisions.
                    if (index[0] == index[1] and evt.value == "" or action == "reference_selection"
                            and (state["context_authoring"]["selected_check_id"] is None or values[3] == "")):
                        return [gr.skip() for _ in outputs]
                argument = index if action == "source_selection" else (index, evt.value)
                if action == "candidate_selection":
                    argument = (*argument, values[17])
                return apply_and_render(state, list(values[:4]), list(values[4:17]), action, argument)
            except Exception:
                raise gr.Error("Context selection failed. Reload the current view and select an exact occurrence or source rectangle.") from None
        context_selection.__annotations__["evt"] = gr.SelectData
        return context_selection

    for component, action in ((text, "reference_selection"), (candidate, "candidate_selection"), (source, "source_selection")):
        component.select(select_callback(action), [*inputs, candidate] if component is candidate else inputs, outputs, **event)

    def sync_context_input(state, display):
        return (*controller.sync(state, display), "", gr.update(choices=[], value=None))

    for component in form:
        revoke = (lambda state: controller.input(state, mapping_only=True)) if component in (
            mapping, candidate_start, candidate_end) else controller.input
        component.input(revoke, [state_component], [state_component], **event).success(
            sync_context_input, [state_component, stamps[1]], [stamps[0], *confirmations, result_summary, result_selector], **event)

    def restore_saved_candidate(state, display, submitted):
        try:
            if display != state["_context_ui"]["display"]:
                return [gr.skip() for _ in range(9)]
            workspace.verify_inputs()
            context, _ = _selected(state)
            value = "" if context is None else controller.candidate_text(context["page_number"]) or ""
            if type(submitted) is str and _preserve_displayed_newlines(submitted, value) == value:
                return [gr.skip() for _ in range(9)]
            state = controller.input(state, mapping_only=True)
            return [state, value, *sync_context_input(state, display)]
        except Exception:
            raise gr.Error("Candidate display could not be restored. Reload the context view and check the fixed source.") from None

    candidate.input(restore_saved_candidate, [state_component, stamps[1], candidate],
                    [state_component, candidate, stamps[0], *confirmations, result_summary, result_selector], **event)

    def reload_context_view(state):
        workspace.verify_inputs()
        state = copy.deepcopy(state)
        session = state["_context_ui"]
        session["view"], session["display"] = uuid4().hex, uuid4().hex
        session["source_view"] = session["first_corner"] = None
        return render(state, replace_image=True)

    reload_view.click(reload_context_view, [state_component], outputs, **event)
    # Populate restored drafts without rendering a PDF or granting review.
    initial_values = render(initial)
    for component, value in zip(outputs[1:], initial_values[1:]):
        if component not in (source, download):
            if isinstance(value, dict) and value.get("__type__") == "update":
                for key in ("choices", "value"):
                    if key in value:
                        setattr(component, key, value[key])
            else:
                component.value = value

    def store_context_for_draft(state, *values):
        try:
            updated = controller.transition(state, list(values[:4]), list(values[4:]), "store")[0]
            return render(updated, message="Context changes captured for the review draft.")
        except Exception:
            raise gr.Error("Draft was not saved. Reload the context view and check its pending fields.") from None

    return store_context_for_draft, inputs, outputs
