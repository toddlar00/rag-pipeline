"""Strict source-bound review snapshots; drafts are never approval receipts.

History is a bounded, operator-editable activity trail, not authenticated audit
evidence. Reloading a draft always requires fresh export confirmation.
"""

from __future__ import annotations

import copy

from evaluation_inputs import _hex_digest
from ocr_context_authoring import empty_authoring, validate_authoring
from ocr_regions import validate_region_plan
from ocr_review import ReviewDocument, rectangle


MAX_HISTORY = 512
MAX_REFERENCE_CHARS = 2_000_000
_ACTIONS = frozenset({"region_added", "region_removed", "reference_stored", "layout_previewed",
                      "selection_cleared", "draft_saved", "draft_loaded", "docling_previewed"})
_ASSIGNMENT_ACTIONS = frozenset({"assignments_started", "line_assigned", "assignments_previewed", "assignment_regions_reordered"})
_OMISSION_ACTIONS = frozenset({"omission_assessed", "omission_reset"})
_CONTEXT_ACTIONS = frozenset({"context_stored", "context_removed", "context_check_stored"})
_FIELDS = frozenset({"schema_version", "kind", "source_sha256", "recovery_sha256", "parent_draft_sha256",
                     "page_count", "page", "selections", "layouts", "regions", "references",
                     "reference_drafts", "history", "event_count", "manual_review_required",
                     "proposals_sha256", "docling_pages"})
_V2_FIELDS = _FIELDS | {"assignment_pages", "assignment_preview_pages"}
_V3_FIELDS = _V2_FIELDS | {"omission_decisions"}
_V4_FIELDS = _V3_FIELDS | {"context_authoring"}


def initial_state(document: ReviewDocument) -> dict:
    return {"page": document.review_choices()[0][1], "selections": {}, "first": None,
            "line_order": None, "layouts": [], "regions": [], "references": [],
            "reference_drafts": [], "history": [], "event_count": 0, "docling_pages": [],
            "assignment_pages": [], "assignment_preview_pages": [], "omission_decisions": [],
            "context_authoring": empty_authoring()}


def _fields(value: object, fields: set | frozenset) -> dict:
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("invalid review draft fields")
    return value


def _reference_drafts(value: object, document: ReviewDocument) -> list[dict]:
    if not isinstance(value, list) or len(value) > 256:
        raise ValueError("review drafts support at most 256 reference pages")
    seen, size = set(), 0
    for item in value:
        _fields(item, {"page_number", "text"})
        number, text = item["page_number"], item["text"]
        document.page(number)
        if number in seen or not isinstance(text, str) or len(text) > 20_000:
            raise ValueError("invalid reference draft")
        text.encode("utf-8", errors="strict")
        seen.add(number)
        size += len(text)
    if size > MAX_REFERENCE_CHARS:
        raise ValueError("reference drafts exceed the total text budget")
    return copy.deepcopy(value)


def validate_draft(payload: object, document: ReviewDocument, *, proposals: dict | None = None,
                   proposals_sha256: str | None = None) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("invalid review draft fields")
    version = payload.get("schema_version")
    fields = {1: _FIELDS, 2: _V2_FIELDS, 3: _V3_FIELDS, 4: _V4_FIELDS}.get(version) if type(version) is int else None
    draft = _fields(payload, fields or _FIELDS)
    if (type(version) is not int or version not in (1, 2, 3, 4)
            or draft["kind"] != "ocr_review_draft" or draft["manual_review_required"] is not True):
        raise ValueError("unsupported review draft contract")
    if (draft["source_sha256"] != document.source_sha256
            or draft["recovery_sha256"] != document.recovery_sha256
            or type(draft["page_count"]) is not int or draft["page_count"] != document.page_count):
        raise ValueError("review draft input binding differs")
    if draft["parent_draft_sha256"] is not None:
        _hex_digest(draft["parent_draft_sha256"], label="parent review draft digest")
    if draft["proposals_sha256"] != proposals_sha256 or (proposals is None) != (proposals_sha256 is None):
        raise ValueError("review draft proposals differ from loaded input")
    docling_pages = draft["docling_pages"]
    if not isinstance(docling_pages, list) or len(docling_pages) > 20:
        raise ValueError("invalid saved Docling page selections")
    if proposals is not None:
        from ocr_docling import build_docling_review, validate_docling_proposals

        _hex_digest(proposals_sha256, label="loaded proposals digest")
        validate_docling_proposals(proposals, recovery=document.recovery_snapshot(), recovery_sha256=document.recovery_sha256)
        if docling_pages:
            build_docling_review(proposals, recovery=document.recovery_snapshot(), recovery_sha256=document.recovery_sha256,
                                 proposals_sha256=proposals_sha256, confirmed_pages=docling_pages)
    elif docling_pages:
        raise ValueError("saved Docling selections require the bound proposals")
    assignment_pages = draft.get("assignment_pages", [])
    assignment_preview_pages = draft.get("assignment_preview_pages", [])
    if (not isinstance(assignment_pages, list) or len(assignment_pages) > 20
            or not isinstance(assignment_preview_pages, list) or len(assignment_preview_pages) > 20
            or any(type(number) is not int for number in assignment_preview_pages)
            or len(set(assignment_preview_pages)) != len(assignment_preview_pages)):
        raise ValueError("invalid saved layout assignments")
    if assignment_pages:
        from ocr_layout_assignment import build_assignment_plan, preview_assignment_plan

        if proposals is None:
            raise ValueError("saved assignments require bound proposals")
        bindings = {"recovery": document.recovery_snapshot(), "recovery_sha256": document.recovery_sha256,
                    "proposals": proposals, "proposals_sha256": proposals_sha256}
        preview = preview_assignment_plan(build_assignment_plan(assignment_pages, **bindings), **bindings)
        complete = {p["page_number"] for p in preview["pages"] if p["status"] == "complete"}
        if not set(assignment_preview_pages) <= complete:
            raise ValueError("saved assignment preview cannot be reproduced")
    elif assignment_preview_pages:
        raise ValueError("saved assignment preview requires assignments")
    omission_decisions = draft.get("omission_decisions", [])
    if not isinstance(omission_decisions, list):
        raise ValueError("invalid saved omission assessments")
    if omission_decisions:
        from ocr_omission import build_omission_diagnostics, validate_omission_decisions

        if proposals is None:
            raise ValueError("saved omission assessments require bound proposals")
        diagnostics = build_omission_diagnostics(document.recovery_snapshot(), proposals,
            recovery_sha256=document.recovery_sha256, proposals_sha256=proposals_sha256)
        validate_omission_decisions(omission_decisions, diagnostics)
    page = document.page(draft["page"])
    selections = draft["selections"]
    if not isinstance(selections, dict) or set(selections) - {"body", "gutter", "crop"}:
        raise ValueError("invalid review draft selections")
    for name, bounds in selections.items():
        rectangle(bounds)
        if name != "crop" and page["candidate"] is None:
            raise ValueError("draft layout selection has no OCR candidate")
    for key in ("layouts", "regions", "references"):
        if not isinstance(draft[key], list):
            raise ValueError("invalid review draft collection")
    if (set(assignment_preview_pages) & set(docling_pages)
            or any(isinstance(p, dict) and p.get("page_number") in docling_pages + assignment_preview_pages
                   for p in draft["layouts"])):
        raise ValueError("a page cannot have two competing saved orders")
    if draft["layouts"]:
        result = document.preview_layout(draft["layouts"])
        selected = {entry["page_number"] for entry in draft["layouts"]}
        if any(p["status"] in {"abstained", "unavailable"} for p in result["pages"] if p["page_number"] in selected):
            raise ValueError("draft layout cannot be reproduced")
    if draft["regions"]:
        validate_region_plan({"schema_version": 1, "source_sha256": document.source_sha256,
                              "recovery_sha256": document.recovery_sha256,
                              "coordinate_system": "original_page_display_fraction", "regions": draft["regions"]},
                             source_sha256=document.source_sha256, recovery_sha256=document.recovery_sha256,
                             page_count=document.page_count)
    if draft["references"]:
        document.references(draft["references"], confirmed=True)
    reference_drafts = _reference_drafts(draft["reference_drafts"], document)
    reference_strings = [text for p in draft["references"] for text in [p["reference"], *p.get("critical_tokens", [])]]
    for text in reference_strings:
        text.encode("utf-8", errors="strict")
    total = sum(map(len, reference_strings)) + sum(len(p["text"]) for p in reference_drafts)
    if version >= 4:
        contextual = validate_authoring(draft["context_authoring"], document)
        total += sum(len(context["reference"]) + sum(
            len(check["left_anchor"]) + len(check["right_anchor"]) for check in context["checks"])
            for context in contextual["contexts"])
    if total > MAX_REFERENCE_CHARS:
        raise ValueError("review draft exceeds the total text budget")
    history, count = draft["history"], draft["event_count"]
    if (type(count) is not int or not 0 <= count <= 1_000_000_000 or not isinstance(history, list)
            or len(history) != min(count, MAX_HISTORY)):
        raise ValueError("invalid review history size")
    for sequence, event in enumerate(history, count - len(history) + 1):
        _fields(event, {"sequence", "action", "page_number"})
        if (type(event["sequence"]) is not int or event["sequence"] != sequence
                or not isinstance(event["action"], str)
                or event["action"] not in (_ACTIONS | (_ASSIGNMENT_ACTIONS if version >= 2 else set())
                                          | (_OMISSION_ACTIONS if version >= 3 else set())
                                          | (_CONTEXT_ACTIONS if version >= 4 else set()))):
            raise ValueError("invalid review activity history")
        document.page(event["page_number"])
    return copy.deepcopy(draft)


def remember_reference(state: dict, text: str, document: ReviewDocument) -> None:
    """Save unconfirmed text separately from stored, reviewed references."""
    drafts = [p for p in state["reference_drafts"] if p["page_number"] != state["page"]]
    # An explicitly cleared value must shadow any previously stored reference.
    if text != "" or any(p["page_number"] == state["page"] for p in state["references"]):
        drafts.append({"page_number": state["page"], "text": text})
    state["reference_drafts"] = _reference_drafts(drafts, document)


def reference_text(state: dict) -> str:
    for key, field in (("reference_drafts", "text"), ("references", "reference")):
        for entry in state[key]:
            if entry["page_number"] == state["page"]:
                return entry[field]
    return ""


def record_event(state: dict, action: str, *, page_number: int | None = None) -> None:
    count = state["event_count"]
    if (type(count) is not int or not 0 <= count < 1_000_000_000
            or action not in _ACTIONS | _ASSIGNMENT_ACTIONS | _OMISSION_ACTIONS | _CONTEXT_ACTIONS
            or page_number is not None and (type(page_number) is not int or not 1 <= page_number <= 5000)):
        raise ValueError("invalid review event")
    state["event_count"] = count + 1
    state["history"] = (state["history"] + [{"sequence": count + 1, "action": action,
                                           "page_number": state["page"] if page_number is None else page_number}])[-MAX_HISTORY:]


def build_draft(state: dict, document: ReviewDocument, *, parent_draft_sha256: str | None = None,
                proposals: dict | None = None, proposals_sha256: str | None = None) -> dict:
    return validate_draft({"schema_version": 4, "kind": "ocr_review_draft",
                           "source_sha256": document.source_sha256, "recovery_sha256": document.recovery_sha256,
                           "parent_draft_sha256": parent_draft_sha256, "page_count": document.page_count,
                           "proposals_sha256": proposals_sha256,
                           "manual_review_required": True,
                           "omission_decisions": state.get("omission_decisions", []),
                           "context_authoring": state.get("context_authoring", empty_authoring()),
                           **{key: state[key] for key in ("page", "selections", "layouts", "regions", "references",
                                                         "reference_drafts", "history", "event_count", "docling_pages",
                                                         "assignment_pages", "assignment_preview_pages")}},
                          document, proposals=proposals, proposals_sha256=proposals_sha256)


def restore_state(payload: object, document: ReviewDocument, *, proposals: dict | None = None,
                   proposals_sha256: str | None = None) -> dict:
    draft = validate_draft(payload, document, proposals=proposals, proposals_sha256=proposals_sha256)
    state = {key: draft[key] for key in ("page", "selections", "layouts", "regions", "references",
                                       "reference_drafts", "history", "event_count", "docling_pages")}
    state.update(assignment_pages=copy.deepcopy(draft.get("assignment_pages", [])),
                 assignment_preview_pages=copy.deepcopy(draft.get("assignment_preview_pages", [])),
                 omission_decisions=copy.deepcopy(draft.get("omission_decisions", [])),
                 context_authoring=copy.deepcopy(draft.get("context_authoring", empty_authoring())))
    state.update(first=None, line_order=None)
    if any(p["page_number"] == state["page"] for p in state["layouts"]):
        result = document.preview_layout(state["layouts"])
        state["line_order"] = next(p["line_order"] for p in result["pages"] if p["page_number"] == state["page"])
    if state["page"] in state["docling_pages"]:
        state["line_order"] = copy.deepcopy(next(p["line_order"] for p in proposals["pages"] if p["page_number"] == state["page"]))
    if state["page"] in state["assignment_preview_pages"]:
        from ocr_layout_assignment import build_assignment_plan, preview_assignment_plan

        bindings = {"recovery": document.recovery_snapshot(), "recovery_sha256": document.recovery_sha256,
                    "proposals": proposals, "proposals_sha256": proposals_sha256}
        preview = preview_assignment_plan(build_assignment_plan(state["assignment_pages"], **bindings), **bindings)
        state["line_order"] = next(p["line_order"] for p in preview["pages"] if p["page_number"] == state["page"])
    # A valid terminal-history snapshot remains viewable/exportable. New edits
    # fail the explicit event budget instead of making startup impossible.
    if state["event_count"] < 1_000_000_000:
        record_event(state, "draft_loaded")
    return state
