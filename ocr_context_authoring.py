"""Partial, approval-free OCR context authoring using the evaluator's policy.

Only explicit export confirmations produce the existing operator-declaration
envelopes. Drafts, previews and result navigation neither authenticate review
nor evaluate text. All offsets refer to unnormalized Unicode code points.
"""

from __future__ import annotations

import copy

import ocr_context_evaluation as evaluation


_CONTEXT_FIELDS = {"context_id", "page_number", "source_anchor", "reference", "checks", "correspondence"}
_CHECK_FIELDS = {"check_id", "category", "reference_span", "left_anchor", "right_anchor"}
_BINDING_FIELDS = {"source_sha256", "recovery_sha256", "reference_sha256", "correspondence_sha256"}


def empty_authoring() -> dict:
    return {"contexts": [], "selected_context_id": None, "selected_check_id": None}


def _checks(context: dict) -> tuple[str, list[dict]]:
    text = evaluation._text(context["reference"], evaluation.MAX_CONTEXT_CHARACTERS)
    checks = context["checks"]
    if not isinstance(checks, list) or len(checks) > evaluation.MAX_CHECKS:
        raise ValueError("invalid draft OCR context check count")
    checked, identifiers = [], set()
    for check in checks:
        evaluation._fields(check, _CHECK_FIELDS)
        identifier = evaluation._identifier(check["check_id"])
        if identifier in identifiers or check["category"] not in evaluation.CATEGORIES:
            raise ValueError("invalid or duplicate draft OCR check")
        identifiers.add(identifier)
        span = check["reference_span"]
        checked.append({"check_id": identifier, "category": check["category"],
                        "reference_span": None if span is None else evaluation._span(span, len(text)),
                        "left_anchor": evaluation._text(check["left_anchor"], evaluation.MAX_ANCHOR_CHARACTERS),
                        "right_anchor": evaluation._text(check["right_anchor"], evaluation.MAX_ANCHOR_CHARACTERS)})
    return text, checked


def validate_authoring(value: object, document) -> dict:
    """Detach a closed partial draft; ambiguity is allowed, fabricated spans are not."""
    draft = evaluation._fields(value, {"contexts", "selected_context_id", "selected_check_id"})
    page_count = evaluation._integer(document.page_count, 1, 5000)
    contexts = draft["contexts"]
    if not isinstance(contexts, list) or len(contexts) > evaluation.MAX_CONTEXTS:
        raise ValueError("invalid draft OCR context count")
    result, identifiers, total_text, total_checks = [], set(), 0, 0
    for context in contexts:
        evaluation._fields(context, _CONTEXT_FIELDS)
        identifier = evaluation._identifier(context["context_id"])
        if identifier in identifiers:
            raise ValueError("duplicate draft OCR context identifier")
        identifiers.add(identifier)
        page = evaluation._integer(context["page_number"], 1, page_count)
        text, checks = _checks(context)
        total_text += len(text)
        total_checks += len(checks)
        if total_text > evaluation.MAX_REFERENCE_CHARACTERS or total_checks > evaluation.MAX_CHECKS:
            raise ValueError("draft OCR contexts exceed aggregate budget")
        anchor = context["source_anchor"]
        anchor = None if anchor is None else evaluation._source_anchor(anchor)
        mapping = evaluation._fields(context["correspondence"], {"status", "candidate_span"})
        status, span = mapping["status"], mapping["candidate_span"]
        if status not in ("unreviewed", "mapped", "missing", "ambiguous"):
            raise ValueError("invalid draft OCR correspondence status")
        if status == "mapped":
            span = evaluation._span(span, evaluation.MAX_CANDIDATE_CHARACTERS, empty=True)
            if span[1] - span[0] > evaluation.MAX_CONTEXT_CHARACTERS:
                raise ValueError("draft candidate context exceeds character budget")
        elif span is not None:
            raise ValueError("unresolved draft context has a fabricated span")
        result.append({"context_id": identifier, "page_number": page, "source_anchor": anchor,
                       "reference": text, "checks": checks,
                       "correspondence": {"status": status, "candidate_span": span}})
    mapped = [context for context in result if context["correspondence"]["status"] == "mapped"]
    if mapped:
        needed = {context["page_number"] for context in mapped}
        pages = evaluation._candidate_pages(
            [page for page in document.context_candidate_pages() if page["page_number"] in needed], page_count)
        for context in mapped:
            candidate = pages.get(context["page_number"])
            if candidate is None or candidate["status"] != "available":
                raise ValueError("mapped draft context has no available candidate")
            evaluation._span(context["correspondence"]["candidate_span"], len(candidate["text"]), empty=True)
    selected, check_id = draft["selected_context_id"], draft["selected_check_id"]
    if selected is not None:
        evaluation._identifier(selected)
        if selected not in identifiers:
            raise ValueError("selected OCR context is not in the draft")
    if check_id is not None:
        evaluation._identifier(check_id)
        context = next((item for item in result if item["context_id"] == selected), None)
        if context is None or check_id not in {item["check_id"] for item in context["checks"]}:
            raise ValueError("selected OCR check is not in the selected context")
    return {"contexts": result, "selected_context_id": selected, "selected_check_id": check_id}


def selection_span(text: object, utf16_range: object, selected_text: object) -> list[int]:
    """Convert an exact half-open UTF-16 selection, rejecting split surrogates."""
    text = evaluation._text(text, evaluation.MAX_CANDIDATE_CHARACTERS)
    selected_text = evaluation._text(selected_text, evaluation.MAX_CANDIDATE_CHARACTERS)
    if not isinstance(utf16_range, (list, tuple)):
        raise ValueError("invalid OCR text selection range")
    start, end = evaluation._span(list(utf16_range), 2 * len(text), empty=True)
    found, offset = {}, 0
    for index in range(len(text) + 1):
        if offset in (start, end):
            found[offset] = index
        if offset >= end or index == len(text):
            break
        offset += 2 if ord(text[index]) > 0xFFFF else 1
    if start not in found or end not in found:
        raise ValueError("OCR selection is outside text or splits a surrogate pair")
    span = [found[start], found[end]]
    if text[slice(*span)] != selected_text:
        raise ValueError("OCR selection value differs from its exact range")
    return span


def preview_check(context: object, check_id: object) -> dict:
    """Describe current and proposed exact anchors without scoring or repairing."""
    context = evaluation._fields(context, _CONTEXT_FIELDS)
    text, checks = _checks(context)
    evaluation._identifier(check_id)
    check = next((item for item in checks if item["check_id"] == check_id), None)
    if check is None:
        raise ValueError("choose an existing OCR context check")
    span = check["reference_span"]
    located, reason = evaluation.locate_context_span(text, check["left_anchor"], check["right_anchor"])
    selected, left, right = None, "", ""
    proposal, proposal_reason = None, "reference_span_missing"
    if span is None:
        reason = "reference_span_missing"
    else:
        start, end = span
        selected = text[start:end]
        left = text[max(0, start - evaluation.MAX_ANCHOR_CHARACTERS):start]
        right = text[end:end + evaluation.MAX_ANCHOR_CHARACTERS]
        proposal, proposal_reason = evaluation.locate_context_span(text, left, right)
        if not selected.strip():
            reason = "reference_occurrence_whitespace"
        elif reason is None and located != span:
            reason = "reference_span_mismatch"
    return {"check_id": check_id, "reference_span": span, "selected_text": selected,
            "proposed_left_anchor": left, "proposed_right_anchor": right,
            "located_span": located, "reason": reason, "valid": reason is None,
            "proposed_located_span": proposal, "proposed_reason": proposal_reason}


def _reference(draft: dict, document) -> dict:
    reference = {"schema_version": 1, "kind": "ocr_context_reference",
                 "source_sha256": document.source_sha256, "page_count": document.page_count,
                 "approval": "human_reviewed", "coordinate_system": "original_page_display_fraction",
                 "contexts": [{key: value for key, value in context.items() if key != "correspondence"}
                              for context in draft["contexts"]]}
    return evaluation.validate_reference(reference, source_sha256=document.source_sha256,
                                         page_count=document.page_count)


def build_reference(authoring: object, document, *, confirmed: bool) -> dict:
    """Build only after explicit operator confirmation; this is not authentication."""
    if confirmed is not True:
        raise ValueError("confirm the current OCR context references against the source")
    return _reference(validate_authoring(authoring, document), document)


def build_correspondence(authoring: object, document, reference: object, reference_sha256: str,
                         *, confirmed: bool) -> dict:
    """Bind a complete mapping to the exact current exported reference cohort."""
    if confirmed is not True:
        raise ValueError("confirm the current OCR context correspondence")
    draft = validate_authoring(authoring, document)
    current = _reference(draft, document)
    reference = evaluation.validate_reference(reference, source_sha256=document.source_sha256,
                                               page_count=document.page_count)
    if current != reference:
        raise ValueError("exported OCR context reference differs from the current draft")
    mapping = {"schema_version": 1, "kind": "ocr_context_correspondence",
               "source_sha256": document.source_sha256, "recovery_sha256": document.recovery_sha256,
               "reference_sha256": reference_sha256, "approval": "human_reviewed",
               "offset_unit": "raw_unicode_code_points",
               "contexts": [{"context_id": context["context_id"], **context["correspondence"]}
                            for context in draft["contexts"]]}
    return evaluation.validate_context_inputs(
        reference, mapping, source_sha256=document.source_sha256,
        recovery_sha256=document.recovery_sha256, reference_sha256=reference_sha256,
        page_count=document.page_count, candidate_pages=document.context_candidate_pages())[1]


def result_target(report: object, reference: object, correspondence: object, bindings: object,
                  context_index: object, check_index: object) -> dict:
    """Resolve exact bound report ordinals; never score again or infer glyph boxes."""
    bindings = evaluation._fields(bindings, _BINDING_FIELDS)
    for value in bindings.values():
        evaluation._digest(value)
    report = evaluation._fields(report, {
        "schema_version", "kind", "source_sha256", "recovery_sha256", "reference_sha256", "page_count",
        "contexts", "coverage", "category_totals", "requires_attention", "policy",
        "canonical_extraction_modified", "inputs"})
    evaluation._integer(report["schema_version"], 1, 1)
    if report["kind"] != "ocr_context_evaluation" or report["canonical_extraction_modified"] is not False:
        raise ValueError("invalid OCR context result contract")
    inputs = evaluation._fields(report["inputs"], {
        "pdf_sha256", "recovery_sha256", "reference_sha256", "correspondence_sha256"})
    for key in _BINDING_FIELDS:
        if inputs["pdf_sha256" if key == "source_sha256" else key] != bindings[key]:
            raise ValueError("OCR context result input binding differs")
        if key != "correspondence_sha256" and report[key] != bindings[key]:
            raise ValueError("OCR context result binding differs")
    reference = evaluation.validate_reference(reference, source_sha256=bindings["source_sha256"],
                                               page_count=report["page_count"])
    correspondence = evaluation.validate_correspondence(
        correspondence, reference=reference, source_sha256=bindings["source_sha256"],
        recovery_sha256=bindings["recovery_sha256"], reference_sha256=bindings["reference_sha256"])
    rows = report["contexts"]
    if not isinstance(rows, list) or len(rows) != len(reference["contexts"]):
        raise ValueError("OCR context result cohort differs")
    for ordinal, (row, context, mapping) in enumerate(zip(rows, reference["contexts"], correspondence["contexts"]), 1):
        evaluation._fields(row, {"context_index", "page_number", "source_anchor", "correspondence_status",
                                 "candidate_context_span", "status", "checks"})
        if (evaluation._integer(row["context_index"], 1, len(rows)) != ordinal
                or evaluation._integer(row["page_number"], 1, report["page_count"]) != context["page_number"]
                or evaluation._source_anchor(row["source_anchor"]) != context["source_anchor"]
                or row["correspondence_status"] != mapping["status"]
                or row["candidate_context_span"] != mapping["candidate_span"]):
            raise ValueError("OCR context result ordinal or location differs")
        if row["candidate_context_span"] is not None:
            evaluation._span(row["candidate_context_span"], evaluation.MAX_CANDIDATE_CHARACTERS, empty=True)
        checks = row["checks"]
        if not isinstance(checks, list) or len(checks) != len(context["checks"]):
            raise ValueError("OCR context result check cohort differs")
        for number, (check, expected) in enumerate(zip(checks, context["checks"]), 1):
            evaluation._fields(check, {"check_index", "category", "reference_span", "candidate_span", "status", "reason"})
            if (evaluation._integer(check["check_index"], 1, len(checks)) != number
                    or check["category"] != expected["category"]
                    or evaluation._span(check["reference_span"], len(context["reference"])) != expected["reference_span"]
                    or check["status"] not in ("passed", "failed", "abstained")):
                raise ValueError("OCR context result check differs")
            evaluation._text(check["reason"], 64, empty=False)
            span = check["candidate_span"]
            if span is not None:
                span = evaluation._span(span, evaluation.MAX_CANDIDATE_CHARACTERS, empty=True)
                if mapping["status"] != "mapped" or not mapping["candidate_span"][0] <= span[0] <= span[1] <= mapping["candidate_span"][1]:
                    raise ValueError("OCR result span lies outside its mapped context")
            elif check["status"] != "abstained":
                raise ValueError("evaluated OCR result has no candidate occurrence")
        status = "failed" if any(check["status"] == "failed" for check in checks) else (
            "abstained" if any(check["status"] == "abstained" for check in checks) else "passed")
        if row["status"] != status:
            raise ValueError("OCR context result status differs")
    index = evaluation._integer(context_index, 1, len(rows)) - 1
    selected = evaluation._integer(check_index, 1, len(rows[index]["checks"])) - 1
    context, check = reference["contexts"][index], rows[index]["checks"][selected]
    return copy.deepcopy({"context_id": context["context_id"], "check_id": context["checks"][selected]["check_id"],
                          "page_number": context["page_number"], "source_anchor": context["source_anchor"],
                          "reference_span": check["reference_span"],
                          "candidate_context_span": rows[index]["candidate_context_span"],
                          "candidate_span": check["candidate_span"], "status": check["status"], "reason": check["reason"]})
