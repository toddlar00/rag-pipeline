"""Local review UI; optional contained OCR, no arbitrary paths or publication control."""

from __future__ import annotations

import copy
from functools import lru_cache
import json
import os
from uuid import uuid4

from ocr_review import click_rectangle, text_difference
from ocr_review_drafts import build_draft, initial_state, record_event, reference_text, remember_reference
from ocr_review_runtime import ReviewDownloadError, ReviewDraftSizeError, draw_overlay
from ocr_review_context_ui import mount_context_authoring, prepare_context_state
from ocr_recovery import ReportCleanupError


def build_app(workspace, *, execution_coordinator=None, pack_service=None, uncertainty_review=False, spot_audit=False):
    if type(uncertainty_review) is not bool:
        raise ValueError("invalid uncertainty UI selection")
    if type(spot_audit) is not bool:
        raise ValueError("invalid spot audit UI selection")
    os.environ["GRADIO_ANALYTICS_ENABLED"] = "False"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["DO_NOT_TRACK"] = "1"
    import gradio as gr

    document = workspace.document

    def rotate_annotation_context(state):
        state["annotation_context"] = uuid4().hex

    def rotate_column_context(state):
        # Every reading-order view, including untouched manual/Docling flows,
        # needs a fresh context. Missing context must never bypass review.
        state["column_context"] = uuid4().hex
        # Changes to the displayed order/selection also revoke scan annotation
        # consent, without making annotation edits alter reading-order plans.
        rotate_annotation_context(state)

    def require_annotation_context(state, view_context):
        expected = state.get("annotation_context")
        matched = type(expected) is str and bool(expected) and type(view_context) is str and view_context == expected
        if not matched:
            raise gr.Error("Review action failed. The page or annotation view changed. Wait for the updated view, "
                           "inspect it and confirm again. No artifact was created and no canonical text was changed.")

    def selected_assignment_text(state):
        index = state.get("highlighted_line")
        if index is None:
            return ""
        candidate = document.page(state["page"])["candidate"]
        if type(index) is not int or candidate is None or not 0 <= index < len(candidate["lines"]):
            raise ValueError("invalid selected OCR line")
        return candidate["lines"][index]["text"]

    def sync_annotation_context(state):
        # State is resolved at execution; the hidden non-State token was
        # captured by the client. Publish the token, ALL annotation resets and
        # selected occurrence text together. A delayed sync never grants consent.
        try:
            getattr(workspace, "verify_inputs", lambda: None)()
            return state.get("annotation_context") or "", False, False, False, False, selected_assignment_text(state)
        except Exception:
            raise safe_error() from None

    def annotation_input(state):
        # Late textbox/dropdown input events only revoke. They never stash
        # captured text or decisions against the newer live page.
        try:
            getattr(workspace, "verify_inputs", lambda: None)()
            state = copy.deepcopy(state)
            rotate_annotation_context(state)
            return state
        except Exception:
            raise safe_error() from None

    def require_column_context(state, view_context):
        expected = state.get("column_context")
        matched = type(expected) is str and bool(expected) and type(view_context) is str and view_context == expected
        if not matched:
            raise gr.Error("Review action failed. The page or reading-order view changed. Wait for the updated view, "
                           "inspect it and confirm again. No artifact was created and no canonical text was changed.")

    def sync_column_context(state):
        # A non-State client input binds captured checkbox values to their
        # displayed context. Gradio resolves State at execution, not enqueue.
        # Until this response arrives the old client token is rejected; this
        # response delivers the current token and ALL order resets together.
        try:
            getattr(workspace, "verify_inputs", lambda: None)()
            return state.get("column_context") or "", False, False, False, False
        except Exception:
            raise safe_error() from None

    @lru_cache(maxsize=2)
    def base_image(number):
        return workspace.render(number)

    def page_notice(number):
        notice = document.page_notice(number)
        candidate = document.page(number)["candidate"]
        if candidate is not None and len(candidate["lines"]) > 2000:
            notice += " OCR box overlay unavailable: more than 2,000 lines. No partial overlay is shown; scan and text remain reviewable."
        return notice

    def canvas(state):
        getattr(workspace, "verify_inputs", lambda: None)()
        region_outline = None
        if state.get("assignment_region_ref") is not None:
            page = workspace.proposal_page(state["page"])
            region_outline = next(r["bbox"] for r in page["regions"] if r["ref"] == state["assignment_region_ref"])
        if state.get("omission_region_ref") is not None:
            region_outline = next((bounds for _, ref, bounds in proposal_regions(state["page"])
                                   if ref == state["omission_region_ref"]), None)
        candidate = document.page(state["page"])["candidate"]
        overlay_available = candidate is not None and len(candidate["lines"]) <= 2000
        selections, line_order = state["selections"], state["line_order"]
        if state.get("column_suggestion") is not None:
            plan = workspace.column_suggestion_plan(state["page"], state["column_suggestion"])
            body, gutter = plan["body_band"], plan["gutter"]
            # Display-only bounds: neither draft selections nor stored orders
            # may acquire an unconfirmed hypothesis by drawing or saving it.
            selections = {**selections, "body": [0, body[0], 1, body[1]],
                          "gutter": [gutter[0], body[0], gutter[1], body[1]]}
            line_order = None
        image = draw_overlay(base_image(state["page"]), candidate if overlay_available else None,
                             selections=selections, line_order=line_order if overlay_available else None,
                             highlighted_line=state.get("highlighted_line") if overlay_available else None,
                             region_outline=region_outline)
        if state["first"] is not None:
            from PIL import ImageDraw
            x, y = state["first"]
            ImageDraw.Draw(image).ellipse((x - 5, y - 5, x + 5, y + 5), fill="#C52A48")
        return image

    choices = document.review_choices()
    initial = copy.deepcopy(getattr(workspace, "initial_draft", None)) or initial_state(document)
    initial["column_suggestion"] = None
    initial.update(scan_region_id=None, scan_region_page=None)
    rotate_column_context(initial)
    first_text = document.page_text(initial["page"])
    first_proposed = first_text
    if initial["line_order"] is not None:
        lines = document.page(initial["page"])["candidate"]["lines"]
        first_proposed = "\n".join(lines[n]["text"] for n in initial["line_order"])

    def safe_error():
        return gr.Error("Review action failed. Check the selection and source files; no canonical text was changed.")

    def proposal_page(number):
        return getattr(workspace, "proposal_page", lambda n: None)(number)

    def proposal_regions(number):
        page = proposal_page(number)
        if page is None or page["page_geometry"] is None:
            return []
        if any(reason in page["reasons"] for reason in ("effective_input_preprocessed", "candidate_preprocessed",
                                                        "missing_page_geometry", "page_geometry_mismatch")):
            return []
        choices = []
        for region in page["regions"]:
            choices.append((f"{region['kind']} {region['ref']}", region["ref"], region["bbox"]))
            for index, cell in enumerate((region["table"] or {}).get("cells", [])):
                if cell["bbox"] is not None:
                    label = (f"{region['ref']} cell rows {cell['row_start'] + 1}-{cell['row_end']}, "
                             f"columns {cell['column_start'] + 1}-{cell['column_end']}")
                    choices.append((label, f"{region['ref']}:cell-{index}", cell["bbox"]))
        return choices

    def proposal_notice(number):
        page = proposal_page(number)
        if page is None:
            return "No Docling proposals for this page. Load a bound artifact with the operator's --proposals option."
        return (f"Docling: {page['status']}. {len(page['regions'])} region(s); "
                f"{len(page['spanning_heading_refs'])} spanning heading(s). "
                f"Reasons: {', '.join(page['reasons']) or 'complete order proposed'}. Human review is required.")

    def refresh_panels(state):
        try:
            getattr(workspace, "verify_inputs", lambda: None)()
            candidate = document.page(state["page"])["candidate"]
            original = document.page_text(state["page"])
            proposed = ("\n".join(candidate["lines"][n]["text"] for n in state["line_order"])
                        if candidate is not None and state["line_order"] is not None
                        and state.get("column_suggestion") is None else original)
            return proposed, text_difference(original, proposed) if candidate is not None else "No candidate to compare."
        except Exception:
            raise safe_error() from None

    def change_page(number, state, text=None, view_context=None):
        require_annotation_context(state, view_context)
        try:
            document.page(number)
            state = copy.deepcopy(state)
            if text is not None:
                remember_reference(state, text, document)
            state.update(page=number, selections={}, first=None, line_order=None, highlighted_line=None,
                         assignment_region_ref=None, omission_region_ref=None, column_suggestion=None,
                         scan_region_id=None, scan_region_page=None)
            rotate_column_context(state)
            layout = next((p for p in state["layouts"] if p["page_number"] == number), None)
            if layout is not None:
                body, gutter = layout["body_band"], layout["gutter"]
                state["selections"] = {"body": [0, body[0], 1, body[1]],
                                       "gutter": [gutter[0], body[0], gutter[1], body[1]]}
                review = document.preview_layout([layout])
                state["line_order"] = next(p["line_order"] for p in review["pages"] if p["page_number"] == number)
            if number in state["docling_pages"]:
                state["line_order"] = proposal_page(number)["line_order"]
            if number in state["assignment_preview_pages"]:
                result = workspace.assignment_preview(state["assignment_pages"])
                state["line_order"] = next(p["line_order"] for p in result["pages"] if p["page_number"] == number)
            reference = reference_text(state)
            text = document.page_text(number)
            proposed, difference = refresh_panels(state)
            return (canvas(state), text, proposed, difference, reference, False, False, state, page_notice(number),
                    gr.update(choices=[(label, key) for label, key, _ in proposal_regions(number)], value=None),
                    proposal_notice(number), False)
        except Exception:
            raise safe_error() from None

    def select_rectangle(state, target, evt, view_context=None):
        require_annotation_context(state, view_context)
        try:
            if target not in {"body", "gutter", "crop"}:
                raise ValueError("invalid tool")
            if target != "crop" and document.page(state["page"])["candidate"] is None:
                raise ValueError("layout requires OCR boxes")
            state = copy.deepcopy(state)
            state["column_suggestion"] = None
            rotate_column_context(state)
            if target in {"body", "gutter"}:
                state["layouts"] = [p for p in state["layouts"] if p["page_number"] != state["page"]]
                state["docling_pages"] = [n for n in state["docling_pages"] if n != state["page"]]
                state["line_order"] = None
                state["assignment_preview_pages"] = [n for n in state["assignment_preview_pages"] if n != state["page"]]
            state["highlighted_line"] = None
            state["assignment_region_ref"] = None
            state["omission_region_ref"] = None
            if state["first"] is None:
                # Validate even a first click before keeping it in session state.
                point = list(evt.index)
                image = base_image(state["page"])
                click_rectangle(point, [image.width if point[0] == 0 else 0,
                                        image.height if point[1] == 0 else 0], width=image.width, height=image.height)
                state["first"] = point
                message = "First corner selected. Click the opposite corner."
            else:
                image = base_image(state["page"])
                state["selections"][target] = click_rectangle(state["first"], evt.index,
                                                              width=image.width, height=image.height)
                state["first"] = None
                message = "Rectangle drawn. Preview the layout or add this crop to the retry plan."
            return canvas(state), state, message, False
        except Exception:
            raise safe_error() from None

    select_rectangle.__annotations__["evt"] = gr.SelectData

    def clear_selection(state, view_context=None):
        require_annotation_context(state, view_context)
        try:
            state = copy.deepcopy(state)
            state.update(selections={}, first=None, line_order=None, highlighted_line=None,
                         assignment_region_ref=None, omission_region_ref=None, column_suggestion=None)
            rotate_column_context(state)
            state["layouts"] = [p for p in state["layouts"] if p["page_number"] != state["page"]]
            state["docling_pages"] = [n for n in state["docling_pages"] if n != state["page"]]
            state["assignment_preview_pages"] = [n for n in state["assignment_preview_pages"] if n != state["page"]]
            record_event(state, "selection_cleared")
            text = document.page_text(state["page"])
            difference = ("No candidate to compare." if document.page(state["page"])["candidate"] is None
                          else "No text differences.")
            return (canvas(state), state, text, difference,
                    "Selection and this page's layout plan cleared. Stored crops and references are retained.", False)
        except Exception:
            raise safe_error() from None

    def undo_region(state, view_context=None):
        require_annotation_context(state, view_context)
        try:
            getattr(workspace, "verify_inputs", lambda: None)()
            state = copy.deepcopy(state)
            state["regions"] = state["regions"][:-1]
            rotate_annotation_context(state)
            record_event(state, "region_removed")
            return state, json.dumps(state["regions"], indent=2), False, "Removed the last stored crop; review the remaining plan."
        except Exception:
            raise safe_error() from None

    def suggest_columns(state):
        try:
            report = workspace.column_suggestion(state["page"])
            page = report["pages"][0]
            state = copy.deepcopy(state)
            state.update(column_suggestion=None, first=None, highlighted_line=None,
                         assignment_region_ref=None, omission_region_ref=None)
            rotate_column_context(state)
            if page["status"] == "unconfirmed_hypothesis":
                state["column_suggestion"] = report
                message = ("Unconfirmed column bounds shown; numbering and text remain in original engine order. "
                           "Inspect the scan: geometry cannot distinguish prose from a table or detect missing text. "
                           "Confirm prose, then click Preview column order; or dismiss to restore previous work.")
            else:
                message = (f"No column suggestion: {page['status']} ({page['reason']}). "
                           "Stored work is retained. Inspect the scan or draw a manual layout; no order was applied.")
            return canvas(state), state, *refresh_panels(state), False, False, message
        except Exception:
            raise safe_error() from None

    def dismiss_column_suggestion(state):
        try:
            state = copy.deepcopy(state)
            state["column_suggestion"] = None
            rotate_column_context(state)
            return (canvas(state), state, *refresh_panels(state), False, False,
                    "Column suggestion dismissed. Previous selections and preview are restored; review again before export.")
        except Exception:
            raise safe_error() from None

    def preview(state, prose_confirmed=False, view_context=None):
        require_column_context(state, view_context)
        if state.get("column_suggestion") is not None and prose_confirmed is not True:
            raise gr.Error("Review action failed. Inspect the suggested bounds and confirm left-to-right two-column prose, not a table, "
                           "before previewing. No layout was stored and no canonical text was changed.")
        try:
            state = copy.deepcopy(state)
            if state.get("column_suggestion") is not None:
                plan = workspace.column_suggestion_plan(state["page"], state["column_suggestion"])
                body, gutter = plan["body_band"], plan["gutter"]
                state["selections"].update(body=[0, body[0], 1, body[1]],
                                            gutter=[gutter[0], body[0], gutter[1], body[1]])
            page_plan = document.layout_page(state["page"], state["selections"].get("body"),
                                             state["selections"].get("gutter"))
            plans = [p for p in state["layouts"] if p["page_number"] != state["page"]] + [page_plan]
            result = document.preview_layout(plans)
            page = next(p for p in result["pages"] if p["page_number"] == state["page"])
            # Abstentions are visible but cannot be mistaken for an accepted edit.
            state["layouts"] = (plans if page["status"] != "abstained" else
                                [p for p in state["layouts"] if p["page_number"] != state["page"]])
            state["line_order"] = page["line_order"]
            state["docling_pages"] = [n for n in state["docling_pages"] if n != state["page"]]
            state["assignment_preview_pages"] = [n for n in state["assignment_preview_pages"] if n != state["page"]]
            state["highlighted_line"] = None
            state["column_suggestion"] = None
            rotate_column_context(state)
            record_event(state, "layout_previewed")
            return (canvas(state), state, page["proposed_text"],
                    text_difference(page["original_text"], page["proposed_text"]), False,
                    f"Layout: {page['status']} ({page['reason']}). Review numbered lines against the scan.")
        except Exception:
            raise safe_error() from None

    def add_region(state, view_context=None):
        require_annotation_context(state, view_context)
        try:
            getattr(workspace, "verify_inputs", lambda: None)()
            state = copy.deepcopy(state)
            bounds = document.source_rectangle(state["page"], state["selections"].get("crop"))
            regions = state["regions"] + [{"region_id": f"region-{len(state['regions']) + 1:04d}",
                                            "page_number": state["page"], "bbox": bounds}]
            plan = workspace.region_plan(regions)
            # Keep insertion order for Undo even though the exported validator
            # canonicalizes by page/ID. Cross-page edits must undo the last add.
            state["regions"] = regions
            rotate_annotation_context(state)
            record_event(state, "region_added")
            return state, json.dumps(plan, indent=2), False, f"Added crop. {len(regions)} of 20 region slots used."
        except Exception:
            raise safe_error() from None

    def add_reference(text, confirmed, state, view_context=None):
        require_annotation_context(state, view_context)
        try:
            getattr(workspace, "verify_inputs", lambda: None)()
            state = copy.deepcopy(state)
            pages = [p for p in state["references"] if p["page_number"] != state["page"]]
            pages.append({"page_number": state["page"], "reference": text})
            state["references"] = document.references(pages, confirmed=confirmed)["pages"]
            remember_reference(state, text, document)
            rotate_annotation_context(state)
            record_event(state, "reference_stored")
            return state, False, f"Stored reviewed reference for this page; {len(pages)} page(s) ready to export.", False
        except Exception:
            raise safe_error() from None

    def save_draft(text, state, view_context=None):
        require_annotation_context(state, view_context)
        try:
            state = copy.deepcopy(state)
            remember_reference(state, text, document)
            rotate_annotation_context(state)
            record_event(state, "draft_saved")
            payload = build_draft(state, document, parent_draft_sha256=getattr(workspace, "draft_sha256", None),
                                  proposals=getattr(workspace, "proposals", None),
                                  proposals_sha256=getattr(workspace, "proposals_sha256", None))
            path = workspace.save("draft", payload)
            return state, str(workspace.prepare_download(path, download.GRADIO_CACHE)), (f"Created {path.name}. Resume with the operator's --draft option. "
                                      "Unconfirmed text remains a draft; pending column suggestions are not saved. "
                                      "All export confirmations reset on restart.")
        except ReviewDownloadError as exc:
            return state, None, str(exc)
        except ReviewDraftSizeError:
            raise gr.Error("Draft exceeds the 16 MiB restart limit and was not saved. Keep this session open to export "
                           "smaller plans or references, then start a smaller review batch. Existing artifacts are unchanged.") from None
        except ReportCleanupError:
            return state, None, "Draft was created, but staging cleanup failed. Check the private output folder before retrying."
        except Exception:
            raise safe_error() from None

    def select_proposal_region(key, state, view_context=None):
        require_annotation_context(state, view_context)
        try:
            state = copy.deepcopy(state)
            bounds = next(bounds for _, ref, bounds in proposal_regions(state["page"]) if ref == key)
            state["selections"]["crop"] = list(bounds)
            state["first"] = None
            state["omission_region_ref"] = None
            state["column_suggestion"] = None
            rotate_column_context(state)
            return canvas(state), state, False, "Suggested crop shown. Inspect it against the scan before adding it to the plan."
        except Exception:
            raise safe_error() from None

    def preview_docling(state):
        try:
            state = copy.deepcopy(state)
            page = proposal_page(state["page"])
            if page is None or page["status"] != "proposed":
                raise ValueError("no complete proposed order")
            state["layouts"] = [p for p in state["layouts"] if p["page_number"] != state["page"]]
            state["docling_pages"] = sorted(set(state["docling_pages"] + [state["page"]]))
            state["assignment_preview_pages"] = [n for n in state["assignment_preview_pages"] if n != state["page"]]
            state["highlighted_line"] = None
            state["line_order"] = page["line_order"]
            state["selections"] = {key: value for key, value in state["selections"].items() if key == "crop"}
            state["first"] = None
            state["column_suggestion"] = None
            rotate_column_context(state)
            record_event(state, "docling_previewed")
            proposed, difference = refresh_panels(state)
            return canvas(state), state, proposed, difference, False, False, "Docling order previewed. Check every numbered region before confirming export."
        except Exception:
            raise safe_error() from None

    def change_tool(state, view_context=None):
        require_annotation_context(state, view_context)
        try:
            state = copy.deepcopy(state)
            state["first"] = None
            state["column_suggestion"] = None
            rotate_column_context(state)
            return canvas(state), state
        except Exception:
            raise safe_error() from None

    def assignment_panels(state):
        try:
            getattr(workspace, "verify_inputs", lambda: None)()
            current = next((p for p in state["assignment_pages"] if p["page_number"] == state["page"]), None)
            regions = (proposal_page(state["page"]) or {}).get("regions", [])
            targets = [("Unresolved - keep drafting", "__unresolved__"),
                       ("Explicitly retain original engine slot", "__retain__")]
            targets += [(f"{r['kind']} {r['ref']}", r["ref"]) for r in regions]
            choices = []
            notice = "Start assignments to resolve unmatched lines. This does not approve text or change OCR boxes."
            if current is not None:
                result = workspace.assignment_preview(state["assignment_pages"])
                page = next(p for p in result["pages"] if p["page_number"] == state["page"])
                lines = document.page(state["page"])["candidate"]["lines"]
                choices = [(f"Original line {d['line_index'] + 1} [{d['relation']}]: "
                            f"{lines[d['line_index']]['text'][:96]}", d["line_index"]) for d in page["line_diagnostics"]]
                notice = (f"Assignments: {page['status']}; {len(page['unresolved_lines'])} unresolved; "
                          f"{len(page['retained_engine_slots'])} retained engine slots. "
                          "Red highlights the original line; orange shows its selected target region. "
                          "Complete means no unresolved assignments, not accurate OCR.")
            selected = state.get("highlighted_line") if current is not None else None
            assignment = next((a for a in current["assignments"] if a["line_index"] == selected), None) if current else None
            target = None if assignment is None else (
                assignment["region_ref"] or ("__retain__" if assignment["retain_engine_slot"] else "__unresolved__"))
            return gr.update(choices=choices, value=selected), gr.update(choices=targets, value=target), notice, False
        except Exception:
            raise safe_error() from None

    def assignment_order_panel(state):
        try:
            getattr(workspace, "verify_inputs", lambda: None)()
            current = next((p for p in state["assignment_pages"] if p["page_number"] == state["page"]), None)
            if current is None:
                return gr.update(choices=[], value=None)
            regions = {r["ref"]: r for r in proposal_page(state["page"])["regions"] if r["tree"] == "body"}
            order = current["body_region_order"]
            order = list(regions) if order is None else order
            return gr.update(choices=[(f"{i + 1}: {regions[ref]['kind']} {ref}", ref) for i, ref in enumerate(order)], value=None)
        except Exception:
            raise safe_error() from None

    def focus_assignment_region(ref, state, view_context=None):
        require_annotation_context(state, view_context)
        try:
            state = copy.deepcopy(state)
            rotate_annotation_context(state)
            state["omission_region_ref"] = None
            regions = (proposal_page(state["page"]) or {}).get("regions", [])
            if ref is not None and ref not in {"__unresolved__", "__retain__"}:
                if not isinstance(ref, str) or ref not in {r["ref"] for r in regions}:
                    raise ValueError("unknown assignment region")
                state["assignment_region_ref"] = ref
            else:
                state["assignment_region_ref"] = None
            return canvas(state), state
        except Exception:
            raise safe_error() from None

    def move_assignment_region(ref, direction, state):
        try:
            state = copy.deepcopy(state)
            state["column_suggestion"] = None
            rotate_column_context(state)
            current = next(p for p in state["assignment_pages"] if p["page_number"] == state["page"])
            regions = [r["ref"] for r in proposal_page(state["page"])["regions"] if r["tree"] == "body"]
            if type(direction) is not int or direction not in (-1, 0, 1):
                raise ValueError("invalid region move")
            order = list(regions) if current["body_region_order"] is None else list(current["body_region_order"])
            if direction == 0:
                current["body_region_order"] = None
            else:
                if type(ref) is not str or ref not in order:
                    raise ValueError("choose a body region to move")
                index = order.index(ref)
                other = index + direction
                if not 0 <= other < len(order):
                    raise ValueError("region is already at that boundary")
                order[index], order[other] = order[other], order[index]
                current["body_region_order"] = order
            workspace.assignment_preview(state["assignment_pages"])
            state["assignment_preview_pages"] = [n for n in state["assignment_preview_pages"] if n != state["page"]]
            state["layouts"] = [p for p in state["layouts"] if p["page_number"] != state["page"]]
            state["docling_pages"] = [n for n in state["docling_pages"] if n != state["page"]]
            state.update(line_order=None, first=None, highlighted_line=None, assignment_region_ref=ref if direction else None)
            record_event(state, "assignment_regions_reordered")
            return canvas(state), state, *refresh_panels(state), "Region order changed in this draft. Preview and review again before exporting."
        except Exception:
            raise safe_error() from None

    def start_assignments(state):
        try:
            state = copy.deepcopy(state)
            state["column_suggestion"] = None
            rotate_column_context(state)
            if not any(p["page_number"] == state["page"] for p in state["assignment_pages"]):
                page = workspace.assignment_suggestion(state["page"])
                state["assignment_pages"].append(page)
            workspace.assignment_preview(state["assignment_pages"])
            state["assignment_preview_pages"] = [n for n in state["assignment_preview_pages"] if n != state["page"]]
            state["layouts"] = [p for p in state["layouts"] if p["page_number"] != state["page"]]
            state["docling_pages"] = [n for n in state["docling_pages"] if n != state["page"]]
            state.update(line_order=None, first=None, highlighted_line=None)
            state["selections"] = {k: v for k, v in state["selections"].items() if k == "crop"}
            record_event(state, "assignments_started")
            return canvas(state), state, *refresh_panels(state), "Assignments opened. Inspect each unresolved line and choose its region."
        except Exception:
            raise safe_error() from None

    def focus_assignment(index, state, view_context=None):
        require_annotation_context(state, view_context)
        try:
            state = copy.deepcopy(state)
            rotate_annotation_context(state)
            state["omission_region_ref"] = None
            state["assignment_region_ref"] = None
            if index is None:
                state["highlighted_line"] = None
                return canvas(state), state, gr.update(value=None)
            if type(index) is not int:
                raise ValueError("invalid line index")
            current = next(p for p in state["assignment_pages"] if p["page_number"] == state["page"])
            item = next(a for a in current["assignments"] if a["line_index"] == index)
            state["highlighted_line"] = index
            state["assignment_region_ref"] = item["region_ref"]
            target = item["region_ref"] or ("__retain__" if item["retain_engine_slot"] else "__unresolved__")
            return canvas(state), state, gr.update(value=target)
        except Exception:
            raise safe_error() from None

    def next_unresolved_assignment(state, view_context=None):
        require_annotation_context(state, view_context)
        try:
            current = next((p for p in state["assignment_pages"] if p["page_number"] == state["page"]), None)
            unresolved = []
            if current is not None:
                result = workspace.assignment_preview(state["assignment_pages"])
                page = next(p for p in result["pages"] if p["page_number"] == state["page"])
                unresolved = page["unresolved_lines"]
            previous = state.get("highlighted_line")
            following = next((index for index in unresolved if previous is None or index > previous), None)
            wrapped = following is None and bool(unresolved)
            index = unresolved[0] if wrapped else following
            image, state, target = focus_assignment(index, state, view_context)
            if current is None:
                notice = "Start line assignments on this page before navigating unresolved lines."
            elif index is None:
                notice = "No unresolved line assignments on this page. Preview and explicit review are still required."
            else:
                notice = (f"Original line {index + 1} selected. " + ("Wrapped to the first unresolved line. " if wrapped else "")
                          + page_notice(state["page"]))
            return image, state, target, gr.update(value=index), notice
        except Exception:
            raise safe_error() from None

    def assign_line(index, target, state):
        try:
            if type(index) is not int or not isinstance(target, str):
                raise ValueError("choose a line and its assignment")
            state = copy.deepcopy(state)
            state["column_suggestion"] = None
            rotate_column_context(state)
            current = next(p for p in state["assignment_pages"] if p["page_number"] == state["page"])
            item = next(a for a in current["assignments"] if a["line_index"] == index)
            item.update(region_ref=None if target in {"__retain__", "__unresolved__"} else target,
                        retain_engine_slot=target == "__retain__")
            workspace.assignment_preview(state["assignment_pages"])
            state["assignment_preview_pages"] = [n for n in state["assignment_preview_pages"] if n != state["page"]]
            state["layouts"] = [p for p in state["layouts"] if p["page_number"] != state["page"]]
            state["docling_pages"] = [n for n in state["docling_pages"] if n != state["page"]]
            state.update(line_order=None, first=None, highlighted_line=index, assignment_region_ref=item["region_ref"])
            record_event(state, "line_assigned")
            return canvas(state), state, *refresh_panels(state), "Line assignment saved in this draft. Preview the complete order before review."
        except Exception:
            raise safe_error() from None

    def preview_assignments(state):
        try:
            state = copy.deepcopy(state)
            state["column_suggestion"] = None
            rotate_column_context(state)
            result = workspace.assignment_preview(state["assignment_pages"])
            page = next(p for p in result["pages"] if p["page_number"] == state["page"])
            if page["status"] != "complete":
                return canvas(state), state, *refresh_panels(state), "Unresolved lines remain. Assign or explicitly retain every line before preview."
            state["assignment_preview_pages"] = sorted(set(state["assignment_preview_pages"] + [state["page"]]))
            state["layouts"] = [p for p in state["layouts"] if p["page_number"] != state["page"]]
            state["docling_pages"] = [n for n in state["docling_pages"] if n != state["page"]]
            state.update(line_order=page["line_order"], first=None, highlighted_line=None)
            record_event(state, "assignments_previewed")
            return canvas(state), state, *refresh_panels(state), "Complete assignment order previewed. Check every numbered line and the text differences."
        except Exception:
            raise safe_error() from None

    def export(kind, confirmed, state, view_context=None):
        if kind in {"layout", "docling", "assignment"}:
            require_column_context(state, view_context)
        else:
            require_annotation_context(state, view_context)
        if confirmed is not True:
            raise gr.Error("Review action failed. Check the review confirmation above this export button after inspecting the scan. "
                           "No artifact was created and no canonical text was changed.")
        if kind in {"layout", "docling", "assignment"} and state.get("column_suggestion") is not None:
            raise gr.Error("Review action failed. Preview or dismiss the pending column suggestion before exporting "
                           "any reading order. No artifact was created and no canonical text was changed.")
        try:
            if kind == "layout":
                payload = document.layout_plan(state["layouts"])
            elif kind == "regions":
                payload = workspace.region_plan(state["regions"])
            elif kind == "docling":
                payload = workspace.docling_review(state["docling_pages"])
            elif kind == "assignment":
                pages = [p for p in state["assignment_pages"] if p["page_number"] in state["assignment_preview_pages"]]
                payload = workspace.assignment_review(pages, state["assignment_preview_pages"])
            elif kind == "omission-review":
                payload = workspace.omission_review(state.get("omission_decisions", []), confirmed=confirmed)
            else:
                payload = document.references(state["references"], confirmed=True)
            path = workspace.save(kind, payload)
            return str(workspace.prepare_download(path, download.GRADIO_CACHE)), f"Created {path.name}. Source, OCR report and canonical extraction are unchanged."
        except ReviewDownloadError as exc:
            return None, str(exc)
        except ReportCleanupError:
            return None, ("Export was created, but staging cleanup failed. Check the configured private output folder "
                          "before retrying. Source and canonical text are unchanged.")
        except Exception:
            raise safe_error() from None

    def omission_data():
        # Optional test/application ports without this capability must show
        # unavailable evidence, never an invented all-clear diagnostic.
        return getattr(workspace, "omission_diagnostics", lambda: None)()

    def omission_panels(state):
        try:
            diagnostics = omission_data()
            if diagnostics is None:
                return (gr.update(choices=[], value=None), "Omission checks unavailable: load fixed Docling proposals. "
                        "Source completeness is unknown.", gr.update(value=None), False)
            page = next(p for p in diagnostics["pages"] if p["page_number"] == state["page"])
            decisions = {(d["page_number"], d["region_ref"]): d["decision"]
                         for d in state.get("omission_decisions", [])}
            choices = [(f"{r['kind']} {r['ref']} [{r['status'].replace('_', ' ')}] — "
                        f"{decisions.get((state['page'], r['ref']), 'unresolved')}", r["ref"]) for r in page["regions"]]
            summary = diagnostics["summary"]
            notice = (f"Page {state['page']}: candidate {page['candidate_state']}; geometry {page['layout_state']}. "
                      f"All source pages: {summary['evaluated_pages']} geometry-evaluated, "
                      f"{summary['unevaluated_pages']} unevaluated. "
                      f"{summary['attention_regions']} region warning(s): "
                      f"{summary['no_candidate_line_overlap']} no overlap, "
                      f"{summary['empty_text_only']} empty text only, "
                      f"{summary['boundary_or_ambiguous_overlap']} boundary/ambiguous. Including "
                      f"{summary['furniture_attention_regions']} in furniture; "
                      f"{summary['incomplete_layout_pages']} page(s) with incomplete saved layout. "
                      f"{len(decisions)} assessment(s) stored in this draft. "
                      "Checks cover saved regions only. Line overlap does not prove that all text or table cells were read. "
                      f"Page reasons: {', '.join(page['proposal_reasons']) or 'none recorded'}.")
            return gr.update(choices=choices, value=None), notice, gr.update(value=None), False
        except Exception:
            raise safe_error() from None

    def focus_omission(ref, state, view_context=None):
        require_annotation_context(state, view_context)
        try:
            state = copy.deepcopy(state)
            rotate_annotation_context(state)
            state.update(omission_region_ref=None, highlighted_line=None, assignment_region_ref=None, first=None)
            if ref is None:
                return canvas(state), state, gr.update(value=None), False, page_notice(state["page"])
            diagnostics = omission_data()
            page = next(p for p in diagnostics["pages"] if p["page_number"] == state["page"])
            region = next(r for r in page["regions"] if r["ref"] == ref)
            state["omission_region_ref"] = ref
            decision = next((d["decision"] for d in state.get("omission_decisions", [])
                             if d["page_number"] == state["page"] and d["region_ref"] == ref), None)
            located = any(key == ref for _, key, _ in proposal_regions(state["page"]))
            notice = (f"Saved region: {region['status']}. "
                      + ("Orange outlines the selected region. " if located else
                         "This region cannot be located safely on the current preview; no outline or suggested crop. ")
                      + "Inspect the scan; overlap is not proof of complete transcription.")
            return canvas(state), state, gr.update(value=decision), False, notice
        except Exception:
            raise safe_error() from None

    def assess_omission(ref, decision, state, view_context=None, *, reset=False):
        require_annotation_context(state, view_context)
        try:
            from ocr_omission import validate_omission_decisions

            if decision is None and reset is not True:
                raise ValueError("choose an assessment before storing")
            if ref is None or ref != state.get("omission_region_ref"):
                raise ValueError("inspect the selected omission region before assessing")
            state = copy.deepcopy(state)
            diagnostics = omission_data()
            # Validate the selected identity even on reset, without inventing
            # a persisted decision for unresolved work.
            selected = {"page_number": state["page"], "region_ref": ref,
                        "decision": "false_alarm" if decision is None else decision}
            validate_omission_decisions([selected], diagnostics)
            decisions = [d for d in state.get("omission_decisions", [])
                         if (d["page_number"], d["region_ref"]) != (state["page"], ref)]
            if decision is not None:
                decisions.append(selected)
            state["omission_decisions"] = validate_omission_decisions(decisions, diagnostics)
            state["omission_region_ref"] = None
            rotate_annotation_context(state)
            record_event(state, "omission_reset" if decision is None else "omission_assessed")
            return state, False, "Assessment updated in the draft. Original warnings remain; review again before export."
        except Exception:
            raise safe_error() from None

    def export_omission_diagnostics():
        try:
            payload = omission_data()
            if payload is None:
                raise ValueError("no fixed omission inputs")
            path = workspace.save("omission-diagnostics", payload)
            return str(workspace.prepare_download(path, download.GRADIO_CACHE)), f"Created {path.name}. Geometry diagnostics only; no review or accuracy approval."
        except ReviewDownloadError as exc:
            return None, str(exc)
        except ReportCleanupError:
            return None, "Diagnostics were created, but staging cleanup failed. Check the private output folder before retrying."
        except Exception:
            raise safe_error() from None

    def scan_panel_values(state, *, require_image=False):
        # This image is a replay of original displayed source pixels, not the
        # possibly processed OCR candidate canvas. Never share its overlays.
        page = getattr(workspace, "scan_page", lambda number: None)(state["page"])
        if page is None:
            if require_image:
                raise ValueError("no fixed scan observation")
            return None, gr.update(choices=[], value=None), (
                "Independent source-pixel evidence unavailable: no fixed scan bundle was loaded. "
                "Saved-region omission checks are separate. Source completeness is unknown."), state["page"]
        if type(page["page_number"]) is not int or page["page_number"] != state["page"]:
            raise ValueError("scan evidence differs from the displayed page")
        choices = [(f"{region['region_id']}: {region['kind']} — OCR {region['ocr_status']}; "
                    f"layout {region['layout_status']}", region["region_id"]) for region in page["regions"]]
        notice = (f"Page {state['page']}: declared source-pixel observation {page['scan_status']} "
                  f"({page['scan_reason']}). Current saved candidate: {page['candidate_state']}; "
                  f"OCR-box comparison: {page['ocr_comparison_state']}; "
                  f"saved-layout comparison: {page['layout_comparison_state']}. "
                  f"{len(choices)} retained hypotheses, including rule-like and ambiguous ink. "
                  "These are thresholded-pixel hypotheses, not proof of text, accurate transcription or complete coverage. "
                  "Box overlap is not recognition; unmatched regions may be non-text.")
        image = workspace.scan_render(state["page"])
        if image is None:
            if require_image:
                raise ValueError("original scan pixels unavailable")
            return None, gr.update(choices=choices, value=None), notice + " No verified original-pixel preview is available.", state["page"]
        image = image.convert("RGB")
        selected = state.get("scan_region_id") if state.get("scan_region_page") == state["page"] else None
        if selected is not None:
            region = next(item for item in page["regions"] if item["region_id"] == selected)
            from PIL import ImageDraw

            left, top, right, bottom = region["bbox"]
            ImageDraw.Draw(image).rectangle((left * image.width, top * image.height,
                                            right * image.width, bottom * image.height), outline="#D87000", width=3)
            potential = ("unknown" if region["ocr_status"] == "unevaluated"
                         else str(region["potential_ocr_omission"]).lower())
            unmatched = ("unknown" if "unevaluated" in {region["ocr_status"], region["layout_status"]}
                         else str(region["unmatched_in_both_saved_inputs"]).lower())
            notice += (f" Selected {region['region_id']}: potential OCR omission {potential}; "
                       f"unmatched in both saved inputs {unmatched}. "
                       "Orange marks this hypothesis on the separate original scan only.")
        return image, gr.update(choices=choices, value=selected), notice, state["page"]

    def scan_panels(state):
        try:
            return scan_panel_values(state)
        except Exception:
            # A failed pixel replay must remain visible without replacing the
            # normal OCR editor with a fabricated blank scan or empty success.
            return None, gr.update(choices=[], value=None), (
                "Independent source-pixel evidence unavailable: the fixed bundle or original-pixel replay could not be verified. "
                "No scan region can be added from this view. Source completeness remains unknown."), state["page"]

    def require_scan_page(page_number, state):
        if type(page_number) is not int or page_number != state["page"]:
            raise ValueError("scan page differs from current review")

    def focus_scan_region(page_number, region_id, state, view_context=None):
        require_annotation_context(state, view_context)
        try:
            require_scan_page(page_number, state)
            state = copy.deepcopy(state)
            state.update(scan_region_id=region_id, scan_region_page=page_number if region_id is not None else None)
            if region_id is not None:
                if type(region_id) is not str:
                    raise ValueError("invalid scan region identity")
                workspace.scan_region(page_number, region_id)
            panel = scan_panel_values(state, require_image=region_id is not None)
            rotate_annotation_context(state)
            return panel[0], state, panel[2]
        except Exception:
            raise safe_error() from None

    def add_scan_region(page_number, region_id, state, view_context=None):
        require_annotation_context(state, view_context)
        try:
            require_scan_page(page_number, state)
            if (type(region_id) is not str or region_id != state.get("scan_region_id")
                    or page_number != state.get("scan_region_page")):
                raise ValueError("inspect this exact scan region before adding")
            bounds = workspace.scan_region(page_number, region_id)
            # The adapter supplies original display fractions after exact gray
            # pixel replay. Do NOT invert an OCR preprocessing transform here.
            state = copy.deepcopy(state)
            regions = state["regions"] + [{"region_id": f"region-{len(state['regions']) + 1:04d}",
                                            "page_number": page_number, "bbox": copy.deepcopy(bounds)}]
            plan = workspace.region_plan(regions)
            state["regions"] = regions
            rotate_annotation_context(state)
            record_event(state, "region_added")
            return state, json.dumps(plan, indent=2), False, (
                f"Added original-scan hypothesis crop. {len(regions)} of 20 region slots used. "
                "Review the crop plan before export; no OCR ran and no text was corrected.")
        except Exception:
            raise safe_error() from None

    prepare_context_state(initial)
    with gr.Blocks(title="OCR Review", analytics_enabled=False, delete_cache=(300, 3600)) as app:
        state = gr.State(initial)
        column_view = gr.Textbox(value=initial["column_context"], visible=False, label="Reading-order view context")
        annotation_view = gr.Textbox(value=initial["annotation_context"], visible=False, label="Annotation view context")
        gr.Markdown("# OCR Review\nCompare the scan, check reading order, and export explicit review plans.")
        gr.Markdown("**Private local review.** OCR text is untrusted evidence. Exports do not change canonical text or indexes. "
                    "Only use sources approved for local annotation retention.")
        with gr.Row():
            draft_save = gr.Button("Save review draft")
            gr.Markdown("Snapshots retain unfinished reference text, stored annotations and the last 512 activity entries. "
                        "They are not approval receipts. Load a fixed snapshot at startup with `--draft`.")
        with gr.Row():
            page_selector = gr.Dropdown(choices=choices, value=initial["page"], label="Page - recovery needs first",
                                        info="All source pages are available. This ordering is not an accuracy score.")
            tool = gr.Radio([("Retry crop", "crop"), ("Column body", "body"), ("Column gutter", "gutter")],
                            value="crop", label="Draw with two corner clicks")
            clear = gr.Button("Clear selection")
        status = gr.Textbox(value=page_notice(initial["page"]),
                            label="Review status", interactive=False)
        with gr.Row():
            with gr.Column(scale=3):
                scan = gr.Image(value=canvas(initial), type="pil", format="png", interactive=False, height=680,
                                label="Scan with OCR boxes when available", buttons=["fullscreen"])
                gr.Markdown("The scan replays saved deskew/contrast settings; it is not a byte-identical raster attestation. "
                            "Deskewed crop envelopes are mapped back to the original displayed page.")
            with gr.Column(scale=2):
                original = gr.Textbox(value=first_text, label="Available extraction (candidate or saved baseline)", lines=8, interactive=False)
                proposed = gr.Textbox(value=first_proposed, label="Proposed reading order", lines=8, interactive=False)
                difference = gr.Textbox(value=("No candidate to compare." if document.page(initial["page"])["candidate"] is None
                                                else text_difference(first_text, first_proposed)),
                                        label="Text differences", lines=5, interactive=False)
        with gr.Tab("Reference transcription"):
            gr.Markdown("Transcribe from the scan. OCR is not ground truth. References start blank intentionally.")
            reference = gr.Textbox(value=reference_text(initial), label="Human-checked reference text for this page",
                                    lines=8, max_length=20_000)
            reference_confirm = gr.Checkbox(label="I checked every line against the scan")
            reference_add = gr.Button("Store reviewed page reference")
            reference_export_confirm = gr.Checkbox(label="Export the stored, reviewed references")
            reference_export = gr.Button("Export reference manifest")
            context_save, context_inputs, context_outputs_for_save = mount_context_authoring(gr, workspace, state, initial, app=app)
        with gr.Tab("Region retries"):
            gr.Markdown("Draw a crop and add it. This exports a plan; it does not run OCR. Maximum 20 regions.")
            region_add = gr.Button("Add selected crop")
            region_undo = gr.Button("Undo last crop")
            region_preview = gr.Textbox(value=json.dumps(initial["regions"], indent=2),
                                        label="Generated region plan", lines=8, interactive=False)
            region_confirm = gr.Checkbox(label="I reviewed these crops against the scan")
            region_export = gr.Button("Export region plan")
        with gr.Tab("Two-column reading order"):
            gr.Markdown("Suggest bounds from this page's saved OCR boxes, or draw the body band and column gap manually. "
                        "A suggestion is not proof of prose or complete OCR: a table can have identical geometry. "
                        "Spanning headings, overlapping lines and ambiguous geometry may cause abstention. "
                        "Pending suggestions are display-only and are not saved in drafts. Previous work is retained until preview.")
            with gr.Row():
                column_suggest = gr.Button("Suggest column bounds")
                column_dismiss = gr.Button("Dismiss column suggestion")
            column_prose = gr.Checkbox(value=False,
                label="I checked the suggested bounds: left-to-right two-column prose, not a table",
                info="Required before previewing a suggestion. Check headings and every line against the scan. "
                     "This does not confirm text accuracy or authorize export.")
            layout_preview = gr.Button("Preview column order")
            layout_confirm = gr.Checkbox(label="I reviewed the numbered order and text differences")
            layout_export = gr.Button("Export layout plan")
        with gr.Tab("Docling suggestions"):
            proposal_status = gr.Textbox(value=proposal_notice(initial["page"]), label="Source-bound layout suggestions",
                                         lines=3, interactive=False)
            proposal_selector = gr.Dropdown(choices=[(label, key) for label, key, _ in proposal_regions(initial["page"])],
                                            value=None, label="Suggested region or table cell")
            proposal_select = gr.Button("Show suggested crop")
            docling_preview = gr.Button("Preview Docling reading order")
            docling_confirm = gr.Checkbox(label="I reviewed all stored Docling page orders against the scan")
            docling_export = gr.Button("Export reviewed Docling orders")
        with gr.Tab("Resolve layout"):
            gr.Markdown("Assign unmatched OCR lines to saved Docling regions. Unique containment supplies initial suggestions, "
                        "not approvals. Furniture keeps its original engine slot. No line can be dropped or rewritten. "
                        "Use explicit slot retention only after checking the scan. Table cell order is not inferred.")
            assignment_start = gr.Button("Start or continue line assignments")
            assignment_next = gr.Button("Next unresolved line on this page")
            line_update, target_update, assignment_text, _ = assignment_panels(initial)
            assignment_status = gr.Textbox(value=assignment_text, label="Assignment coverage", lines=3, interactive=False)
            assignment_line = gr.Dropdown(choices=line_update["choices"], value=None, label="Original OCR line to inspect")
            assignment_text_view = gr.Textbox(value=selected_assignment_text(initial), lines=6, interactive=False,
                                              label="Selected original OCR line (unverified)")
            assignment_target = gr.Dropdown(choices=target_update["choices"], value=None, label="Assign to saved region or retain slot")
            assignment_apply = gr.Button("Apply line assignment")
            assignment_order = gr.Dropdown(choices=assignment_order_panel(initial)["choices"], value=None,
                                           label="Body regions in reading order")
            with gr.Row():
                assignment_up = gr.Button("Move region earlier")
                assignment_down = gr.Button("Move region later")
                assignment_reset = gr.Button("Restore saved Docling region order")
            assignment_preview = gr.Button("Preview resolved layout")
            assignment_confirm = gr.Checkbox(label="I reviewed every previewed assignment page against the scan")
            assignment_export = gr.Button("Export reviewed line assignments")
        with gr.Tab("Omission checks"):
            gr.Markdown("Look for missing text in saved regions. These checks do not inspect scan pixels and cannot find "
                        "regions missed by both OCR and Docling. Assessments never remove diagnostic warnings. "
                        "Partial reviews are allowed; unresolved regions remain explicit.")
            omission_choices, omission_notice, _, _ = omission_panels(initial)
            omission_status = gr.Textbox(value=omission_notice, label="Omission evidence and limitations", lines=5, interactive=False)
            omission_region = gr.Dropdown(choices=omission_choices["choices"], value=None, label="Saved region to check for missing text")
            omission_decision = gr.Dropdown(choices=[("Suspected missing text", "suspected_missing_text"),
                ("Not text", "not_text"), ("False alarm after scan inspection", "false_alarm")],
                value=None, label="Omission assessment", info="Unset means unresolved, not accepted.")
            with gr.Row():
                omission_apply = gr.Button("Store omission assessment")
                omission_reset = gr.Button("Reset assessment to unresolved")
                omission_crop = gr.Button("Show this region as a retry crop")
            omission_confirm = gr.Checkbox(label="I reviewed these omission assessments against the scan", value=False)
            omission_export = gr.Button("Export reviewed omission assessments")
            omission_diagnostics_export = gr.Button("Export unreviewed geometry diagnostics")
        with gr.Tab("Independent scan evidence"):
            gr.Markdown("Inspect hypotheses discovered from original source pixels, independently of OCR and saved layout. "
                        "This separate canvas is never the processed OCR image. All hypothesis kinds remain visible; "
                        "a blank threshold result or overlapping box does not establish complete transcription. "
                        "Selecting a region does not approve it. Adding one only drafts a retry crop; it runs no OCR.")
            scan_image, scan_choices, scan_notice, scan_page_number = scan_panels(initial)
            source_scan = gr.Image(value=scan_image, type="pil", interactive=False, format="png", sources=[],
                                   label="Verified original source scan — independent pixel hypotheses", buttons=[])
            source_scan_status = gr.Textbox(value=scan_notice, label="Independent scan evidence and limitations", lines=5,
                                           interactive=False)
            source_scan_page = gr.Number(value=scan_page_number, precision=0, visible=False,
                                         label="Original scan evidence page")
            source_scan_region = gr.Dropdown(choices=scan_choices["choices"], value=None,
                                            label="Original scan hypothesis to inspect")
            source_scan_add = gr.Button("Add selected scan region to crop plan")
        download = gr.File(label="New private artifact", interactive=False)
        event = {"api_visibility": "private", "concurrency_limit": 1, "concurrency_id": "ocr-review"}
        context_outputs = [column_view, column_prose, layout_confirm, docling_confirm, assignment_confirm]
        annotation_outputs = [annotation_view, reference_confirm, reference_export_confirm, region_confirm, omission_confirm, assignment_text_view]
        page_selector.change(change_page, [page_selector, state, reference, annotation_view],
                             [scan, original, proposed, difference, reference, reference_confirm,
                              layout_confirm, state, status, proposal_selector, proposal_status, docling_confirm], **event).success(
            assignment_panels, [state], [assignment_line, assignment_target, assignment_status, assignment_confirm], **event).success(
            assignment_order_panel, [state], [assignment_order], **event).success(
            omission_panels, [state], [omission_region, omission_status, omission_decision, omission_confirm], **event).success(
            scan_panels, [state], [source_scan, source_scan_region, source_scan_status, source_scan_page], **event).success(
            sync_column_context, [state], context_outputs, **event).success(
            sync_annotation_context, [state], annotation_outputs, **event)
        draft_save.click(context_save, context_inputs, context_outputs_for_save, **event).success(
            save_draft, [reference, state, annotation_view], [state, download, status], **event).success(
            sync_annotation_context, [state], annotation_outputs, **event)
        scan.select(select_rectangle, [state, tool, annotation_view], [scan, state, status, layout_confirm], **event).success(
            refresh_panels, [state], [proposed, difference], **event).success(
            lambda: (False, False, False, False), outputs=[docling_confirm, assignment_confirm, omission_confirm, column_prose], **event).success(
            sync_column_context, [state], context_outputs, **event).success(
            sync_annotation_context, [state], annotation_outputs, **event)
        clear.click(clear_selection, [state, annotation_view], [scan, state, proposed, difference, status, layout_confirm], **event).success(
            lambda: (False, False, False, False), outputs=[docling_confirm, assignment_confirm, omission_confirm, column_prose], **event).success(
            sync_column_context, [state], context_outputs, **event).success(
            sync_annotation_context, [state], annotation_outputs, **event)
        for button, action in ((column_suggest, suggest_columns), (column_dismiss, dismiss_column_suggestion)):
            button.click(action, [state], [scan, state, proposed, difference, layout_confirm, column_prose, status], **event).success(
                sync_column_context, [state], context_outputs, **event).success(
                sync_annotation_context, [state], annotation_outputs, **event)
        layout_preview.click(preview, [state, column_prose, column_view],
                             [scan, state, proposed, difference, layout_confirm, status], **event).success(
            sync_column_context, [state], context_outputs, **event).success(
            sync_annotation_context, [state], annotation_outputs, **event)
        proposal_select.click(select_proposal_region, [proposal_selector, state, annotation_view], [scan, state, region_confirm, status], **event).success(
            refresh_panels, [state], [proposed, difference], **event).success(
            lambda: (False, False, False, False, False),
            outputs=[omission_confirm, column_prose, layout_confirm, docling_confirm, assignment_confirm], **event).success(
            sync_column_context, [state], context_outputs, **event).success(
            sync_annotation_context, [state], annotation_outputs, **event)
        docling_preview.click(preview_docling, [state],
                              [scan, state, proposed, difference, docling_confirm, layout_confirm, status], **event).success(
            sync_column_context, [state], context_outputs, **event).success(
            sync_annotation_context, [state], annotation_outputs, **event)
        assignment_line.input(focus_assignment, [assignment_line, state, annotation_view], [scan, state, assignment_target], **event).success(
            sync_annotation_context, [state], annotation_outputs, **event)
        assignment_next.click(next_unresolved_assignment, [state, annotation_view],
                              [scan, state, assignment_target, assignment_line, status], **event).success(
            sync_annotation_context, [state], annotation_outputs, **event)
        assignment_target.input(focus_assignment_region, [assignment_target, state, annotation_view], [scan, state], **event).success(
            sync_annotation_context, [state], annotation_outputs, **event)
        assignment_order.input(focus_assignment_region, [assignment_order, state, annotation_view], [scan, state], **event).success(
            sync_annotation_context, [state], annotation_outputs, **event)
        for button, action, inputs in ((assignment_start, start_assignments, [state]),
                                       (assignment_apply, assign_line, [assignment_line, assignment_target, state]),
                                       (assignment_preview, preview_assignments, [state]),
                                       (assignment_up, lambda ref, s: move_assignment_region(ref, -1, s), [assignment_order, state]),
                                       (assignment_down, lambda ref, s: move_assignment_region(ref, 1, s), [assignment_order, state]),
                                       (assignment_reset, lambda s: move_assignment_region(None, 0, s), [state])):
            button.click(action, inputs, [scan, state, proposed, difference, status], **event).success(
                assignment_panels, [state], [assignment_line, assignment_target, assignment_status, assignment_confirm], **event).success(
                assignment_order_panel, [state], [assignment_order], **event).success(
                sync_column_context, [state], context_outputs, **event).success(
                sync_annotation_context, [state], annotation_outputs, **event)
        assignment_export.click(lambda confirmed, s, context=None: export("assignment", confirmed, s, context),
                                [assignment_confirm, state, column_view], [download, status], **event)
        docling_export.click(lambda confirmed, s, context=None: export("docling", confirmed, s, context),
                             [docling_confirm, state, column_view], [download, status], **event)
        region_add.click(add_region, [state, annotation_view], [state, region_preview, region_confirm, status], **event).success(
            sync_annotation_context, [state], annotation_outputs, **event)
        region_undo.click(undo_region, [state, annotation_view], [state, region_preview, region_confirm, status], **event).success(
            sync_annotation_context, [state], annotation_outputs, **event)
        reference_add.click(add_reference, [reference, reference_confirm, state, annotation_view],
                            [state, reference_confirm, status, reference_export_confirm], **event).success(
            sync_annotation_context, [state], annotation_outputs, **event)
        layout_export.click(lambda confirmed, s, context=None: export("layout", confirmed, s, context),
                            [layout_confirm, state, column_view], [download, status], **event)
        region_export.click(lambda confirmed, s, context=None: export("regions", confirmed, s, context),
                            [region_confirm, state, annotation_view], [download, status], **event)
        reference_export.click(lambda confirmed, s, context=None: export("references", confirmed, s, context),
                               [reference_export_confirm, state, annotation_view], [download, status], **event)
        reference.input(annotation_input, [state], [state], **event).success(
            sync_annotation_context, [state], annotation_outputs, **event)
        column_prose.input(lambda: False, outputs=[layout_confirm], **event)
        tool.change(change_tool, [state, annotation_view], [scan, state], **event).success(
            refresh_panels, [state], [proposed, difference], **event).success(
            lambda: (False, False, False, False),
            outputs=[column_prose, layout_confirm, docling_confirm, assignment_confirm], **event).success(
            sync_column_context, [state], context_outputs, **event).success(
            sync_annotation_context, [state], annotation_outputs, **event)
        omission_region.input(focus_omission, [omission_region, state, annotation_view],
                               [scan, state, omission_decision, omission_confirm, status], **event).success(
            sync_annotation_context, [state], annotation_outputs, **event)
        omission_apply.click(assess_omission, [omission_region, omission_decision, state, annotation_view],
                              [state, omission_confirm, status], **event).success(
            omission_panels, [state], [omission_region, omission_status, omission_decision, omission_confirm], **event).success(
            sync_annotation_context, [state], annotation_outputs, **event)
        omission_reset.click(lambda ref, s, context=None: assess_omission(ref, None, s, context, reset=True),
                              [omission_region, state, annotation_view], [state, omission_confirm, status], **event).success(
            omission_panels, [state], [omission_region, omission_status, omission_decision, omission_confirm], **event).success(
            sync_annotation_context, [state], annotation_outputs, **event)
        omission_crop.click(select_proposal_region, [omission_region, state, annotation_view], [scan, state, region_confirm, status], **event).success(
            refresh_panels, [state], [proposed, difference], **event).success(
            lambda: (False, False, False, False, False),
            outputs=[omission_confirm, column_prose, layout_confirm, docling_confirm, assignment_confirm], **event).success(
            sync_column_context, [state], context_outputs, **event).success(
            sync_annotation_context, [state], annotation_outputs, **event)
        omission_decision.input(annotation_input, [state], [state], **event).success(
            sync_annotation_context, [state], annotation_outputs, **event)
        omission_export.click(lambda confirmed, s, context=None: export("omission-review", confirmed, s, context),
                               [omission_confirm, state, annotation_view], [download, status], **event)
        omission_diagnostics_export.click(export_omission_diagnostics, outputs=[download, status], **event)
        source_scan_region.input(focus_scan_region, [source_scan_page, source_scan_region, state, annotation_view],
                                  [source_scan, state, source_scan_status], **event).success(
            sync_annotation_context, [state], annotation_outputs, **event)
        source_scan_add.click(add_scan_region, [source_scan_page, source_scan_region, state, annotation_view],
                              [state, region_preview, region_confirm, status], **event).success(
            sync_annotation_context, [state], annotation_outputs, **event)
        if execution_coordinator is not None:
            from ocr_review_execution_ui import build_execution_panel

            pack_options = {} if pack_service is None else {"pack_service": pack_service}
            if uncertainty_review:
                pack_options["uncertainty_review"] = True
            build_execution_panel(workspace, execution_coordinator, review_state=state,
                                  annotation_view=annotation_view, **pack_options)
        if pack_service is not None:
            from ocr_review_crop_archive_ui import build_crop_archive_panel

            build_crop_archive_panel(workspace, pack_service, **({"uncertainty": True} if uncertainty_review else {}))
        if spot_audit:
            from ocr_spot_audit_ui import build_spot_audit_panel

            build_spot_audit_panel(workspace)
    return app
