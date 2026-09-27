"""Exact, occurrence-bound OCR checks; no normalization or semantic inference.

This pure standard-library policy consumes reviewed references and explicit
candidate correspondence. Approval and physical locations are operator claims,
not authenticated attestations. Passing selected checks never verifies a page.
"""

from __future__ import annotations

import math
import re


MAX_CONTEXTS = 256
MAX_CHECKS = 1024
MAX_CONTEXT_CHARACTERS = 20_000
MAX_REFERENCE_CHARACTERS = 1_000_000
MAX_CANDIDATE_CHARACTERS = 100_000
MAX_TOTAL_CANDIDATE_CHARACTERS = 2_000_000
MAX_ANCHOR_CHARACTERS = 256
CATEGORIES = ("number", "unit", "negation", "name", "footnote_identifier")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}")
_SHA = re.compile(r"[0-9a-f]{64}")


def _fields(value: object, names: set[str]) -> dict:
    if not isinstance(value, dict) or set(value) != names:
        raise ValueError("invalid OCR context fields")
    return value


def _integer(value: object, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError("invalid OCR context integer")
    return value


def _digest(value: object) -> str:
    if not isinstance(value, str) or _SHA.fullmatch(value) is None:
        raise ValueError("invalid OCR context digest")
    return value


def _identifier(value: object) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise ValueError("invalid OCR context identifier")
    return value


def _text(value: object, maximum: int, *, empty: bool = True) -> str:
    if not isinstance(value, str) or len(value) > maximum or not empty and not value:
        raise ValueError("invalid OCR context text length")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise ValueError("invalid OCR context Unicode") from None
    return value


def _span(value: object, length: int, *, empty: bool = False) -> list[int]:
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError("invalid OCR context span")
    start, end = (_integer(n, 0, length) for n in value)
    if start > end or not empty and start == end:
        raise ValueError("invalid OCR context span order")
    return [start, end]


def _source_anchor(value: object) -> dict:
    item = _fields(value, {"kind", "bbox", "cell"})
    if item["kind"] not in ("sentence", "region", "cell"):
        raise ValueError("invalid OCR source anchor kind")
    box = item["bbox"]
    if not isinstance(box, list) or len(box) != 4:
        raise ValueError("invalid OCR context box")
    result = []
    for coordinate in box:
        if type(coordinate) not in (int, float):
            raise ValueError("invalid OCR context coordinate")
        try:
            coordinate = float(coordinate)
        except OverflowError:
            raise ValueError("invalid OCR context coordinate") from None
        if not math.isfinite(coordinate) or not 0 <= coordinate <= 1:
            raise ValueError("invalid OCR context coordinate")
        result.append(coordinate)
    if result[0] >= result[2] or result[1] >= result[3]:
        raise ValueError("invalid OCR context box order")
    cell = item["cell"]
    if item["kind"] == "cell":
        _fields(cell, {"row", "column"})
        cell = {key: _integer(cell[key], 1, 100_000) for key in ("row", "column")}
    elif cell is not None:
        raise ValueError("non-cell context has cell coordinates")
    return {"kind": item["kind"], "bbox": result, "cell": cell}


def _unique_anchor(text: str, anchor: str) -> tuple[int | None, str | None]:
    start = text.find(anchor)
    if start < 0:
        return None, "anchor_missing"
    if text.find(anchor, start + 1) >= 0:
        return None, "anchor_ambiguous"
    return start, None


def _locate(text: str, left: str, right: str) -> tuple[list[int] | None, str | None]:
    start, end = 0, len(text)
    if left:
        position, reason = _unique_anchor(text, left)
        if reason:
            return None, reason
        start = position + len(left)
    if right:
        position, reason = _unique_anchor(text, right)
        if reason:
            return None, reason
        end = position
    if start > end:
        return None, "anchor_order_changed"
    return [start, end], None


def locate_context_span(text: object, left_anchor: object,
                        right_anchor: object) -> tuple[list[int] | None, str | None]:
    """Diagnose bounded raw flanking anchors without evaluating a check."""
    return _locate(_text(text, MAX_CONTEXT_CHARACTERS),
                   _text(left_anchor, MAX_ANCHOR_CHARACTERS),
                   _text(right_anchor, MAX_ANCHOR_CHARACTERS))


def validate_reference(payload: object, *, source_sha256: str, page_count: int) -> dict:
    """Detach a bounded reference whose exact flanking anchors locate each check."""
    _digest(source_sha256)
    _integer(page_count, 1, 5000)
    reference = _fields(payload, {"schema_version", "kind", "source_sha256", "page_count",
                                  "approval", "coordinate_system", "contexts"})
    _integer(reference["schema_version"], 1, 1)
    _integer(reference["page_count"], 1, 5000)
    if (reference["kind"] != "ocr_context_reference" or reference["approval"] != "human_reviewed"
            or _digest(reference["source_sha256"]) != source_sha256 or reference["page_count"] != page_count
            or reference["coordinate_system"] != "original_page_display_fraction"):
        raise ValueError("OCR context reference binding or contract differs")
    contexts = reference["contexts"]
    if not isinstance(contexts, list) or not 1 <= len(contexts) <= MAX_CONTEXTS:
        raise ValueError("invalid OCR context count")
    results, identifiers, total_text, total_checks = [], set(), 0, 0
    for context in contexts:
        _fields(context, {"context_id", "page_number", "source_anchor", "reference", "checks"})
        identifier = _identifier(context["context_id"])
        if identifier in identifiers:
            raise ValueError("duplicate OCR context identifier")
        identifiers.add(identifier)
        page = _integer(context["page_number"], 1, page_count)
        text = _text(context["reference"], MAX_CONTEXT_CHARACTERS, empty=False)
        total_text += len(text)
        checks = context["checks"]
        if not isinstance(checks, list) or not 1 <= len(checks) <= MAX_CHECKS:
            raise ValueError("invalid OCR context check count")
        total_checks += len(checks)
        if total_checks > MAX_CHECKS or total_text > MAX_REFERENCE_CHARACTERS:
            raise ValueError("OCR context reference exceeds aggregate budget")
        checked, check_ids, spans = [], set(), []
        for check in checks:
            _fields(check, {"check_id", "category", "reference_span", "left_anchor", "right_anchor"})
            check_id = _identifier(check["check_id"])
            if check_id in check_ids or check["category"] not in CATEGORIES:
                raise ValueError("invalid or duplicate OCR context check")
            check_ids.add(check_id)
            span = _span(check["reference_span"], len(text))
            if not text[slice(*span)].strip():
                raise ValueError("critical OCR reference occurrence cannot be whitespace-only")
            if any(span[0] < other[1] and other[0] < span[1] for other in spans):
                raise ValueError("OCR context reference checks overlap")
            spans.append(span)
            left = _text(check["left_anchor"], MAX_ANCHOR_CHARACTERS)
            right = _text(check["right_anchor"], MAX_ANCHOR_CHARACTERS)
            located, reason = _locate(text, left, right)
            if reason or located != span:
                raise ValueError("reference anchors do not uniquely flank their occurrence")
            checked.append({"check_id": check_id, "category": check["category"],
                            "reference_span": span, "left_anchor": left, "right_anchor": right})
        results.append({"context_id": identifier, "page_number": page,
                        "source_anchor": _source_anchor(context["source_anchor"]),
                        "reference": text, "checks": checked})
    return {**reference, "contexts": results}


def validate_correspondence(payload: object, *, reference: dict, source_sha256: str,
                            recovery_sha256: str, reference_sha256: str) -> dict:
    """Require exactly one explicit decision per reviewed reference context."""
    reference = validate_reference(reference, source_sha256=source_sha256,
                                   page_count=reference.get("page_count") if isinstance(reference, dict) else None)
    mapping = _fields(payload, {"schema_version", "kind", "source_sha256", "recovery_sha256",
                                "reference_sha256", "approval", "offset_unit", "contexts"})
    _integer(mapping["schema_version"], 1, 1)
    if (mapping["kind"] != "ocr_context_correspondence" or mapping["approval"] != "human_reviewed"
            or mapping["offset_unit"] != "raw_unicode_code_points"):
        raise ValueError("invalid OCR context correspondence contract")
    for key, digest in (("source_sha256", source_sha256), ("recovery_sha256", recovery_sha256),
                        ("reference_sha256", reference_sha256)):
        if _digest(mapping[key]) != _digest(digest):
            raise ValueError("OCR context correspondence digest differs")
    contexts = mapping["contexts"]
    if not isinstance(contexts, list) or len(contexts) != len(reference["contexts"]):
        raise ValueError("OCR correspondence must cover every reference context")
    expected = {context["context_id"] for context in reference["contexts"]}
    found = {}
    for item in contexts:
        _fields(item, {"context_id", "status", "candidate_span"})
        identifier = _identifier(item["context_id"])
        if identifier not in expected or identifier in found:
            raise ValueError("unknown or duplicate OCR correspondence context")
        status = item["status"]
        if status not in ("mapped", "missing", "ambiguous"):
            raise ValueError("invalid OCR correspondence status")
        span = item["candidate_span"]
        if status == "mapped":
            span = _span(span, MAX_CANDIDATE_CHARACTERS, empty=True)
            if span[1] - span[0] > MAX_CONTEXT_CHARACTERS:
                raise ValueError("candidate context exceeds character budget")
        elif span is not None:
            raise ValueError("unresolved OCR correspondence has a fabricated span")
        found[identifier] = {"context_id": identifier, "status": status, "candidate_span": span}
    return {**mapping, "contexts": [found[context["context_id"]] for context in reference["contexts"]]}


def _candidate_pages(payload: object, page_count: int) -> dict:
    if not isinstance(payload, list) or len(payload) > page_count:
        raise ValueError("invalid OCR candidate page collection")
    pages, total = {}, 0
    for item in payload:
        _fields(item, {"page_number", "status", "text"})
        page = _integer(item["page_number"], 1, page_count)
        if page in pages or item["status"] not in ("available", "retry_failed", "deferred"):
            raise ValueError("duplicate or invalid OCR candidate page")
        text = item["text"]
        if item["status"] == "available":
            text = _text(text, MAX_CANDIDATE_CHARACTERS)
            total += len(text)
            if total > MAX_TOTAL_CANDIDATE_CHARACTERS:
                raise ValueError("OCR candidate text exceeds aggregate budget")
        elif text is not None:
            raise ValueError("unavailable OCR candidate has fabricated text")
        pages[page] = {"status": item["status"], "text": text}
    return pages


def validate_context_inputs(reference: object, correspondence: object, *, source_sha256: str,
                            recovery_sha256: str, reference_sha256: str, page_count: int,
                            candidate_pages: object) -> tuple[dict, dict, dict]:
    """Detach the evaluator's complete inputs in its existing validation order."""
    reference = validate_reference(reference, source_sha256=source_sha256, page_count=page_count)
    correspondence = validate_correspondence(
        correspondence, reference=reference, source_sha256=source_sha256,
        recovery_sha256=recovery_sha256, reference_sha256=reference_sha256)
    pages = _candidate_pages(candidate_pages, page_count)
    occupied = {}
    for context, mapping in zip(reference["contexts"], correspondence["contexts"]):
        if mapping["status"] != "mapped":
            continue
        page = context["page_number"]
        source = pages.get(page)
        if source is None or source["status"] != "available":
            raise ValueError("mapped OCR context has no available candidate")
        span = _span(mapping["candidate_span"], len(source["text"]), empty=True)
        for other in occupied.setdefault(page, []):
            if span == other or span[0] < other[1] and other[0] < span[1]:
                raise ValueError("OCR candidate contexts overlap or reuse an occurrence")
        occupied[page].append(span)
    return reference, correspondence, pages


def evaluate_context_checks(reference: object, correspondence: object, *, source_sha256: str,
                            recovery_sha256: str, reference_sha256: str, page_count: int,
                            candidate_pages: object) -> dict:
    """Measure a fixed reviewed cohort, retaining every failure and abstention.

    Candidate correspondence is a human assertion, not inferred alignment.
    Only exact spans between unique anchors can pass. Missing evidence cannot.
    """
    reference, correspondence, pages = validate_context_inputs(
        reference, correspondence, source_sha256=source_sha256,
        recovery_sha256=recovery_sha256, reference_sha256=reference_sha256,
        page_count=page_count, candidate_pages=candidate_pages)
    results, totals = [], {"passed": 0, "failed": 0, "abstained": 0}
    category_totals = {category: {"passed": 0, "failed": 0, "abstained": 0} for category in CATEGORIES}
    for context_index, (context, mapping) in enumerate(zip(reference["contexts"], correspondence["contexts"]), 1):
        page = pages.get(context["page_number"])
        unavailable = "not_selected" if page is None else page["status"] if page["status"] != "available" else None
        reason = unavailable or ("context_" + mapping["status"] if mapping["status"] != "mapped" else None)
        checks = []
        for check_index, check in enumerate(context["checks"], 1):
            status, check_reason, located = "abstained", reason, None
            if reason is None:
                start, end = mapping["candidate_span"]
                text = page["text"][start:end]
                if not text.strip():
                    status, check_reason, located = "failed", "empty_candidate_context", [start, end]
                else:
                    span, check_reason = _locate(text, check["left_anchor"], check["right_anchor"])
                    if span is not None:
                        expected = context["reference"][slice(*check["reference_span"])]
                        status = "passed" if text[slice(*span)] == expected else "failed"
                        check_reason = "exact_occurrence_match" if status == "passed" else "occurrence_differs"
                        located = [start + span[0], start + span[1]]
            totals[status] += 1
            category_totals[check["category"]][status] += 1
            checks.append({"check_index": check_index, "category": check["category"],
                           "reference_span": list(check["reference_span"]), "candidate_span": located,
                           "status": status, "reason": check_reason})
        context_status = "failed" if any(c["status"] == "failed" for c in checks) else (
            "abstained" if any(c["status"] == "abstained" for c in checks) else "passed")
        results.append({"context_index": context_index, "page_number": context["page_number"],
                        "source_anchor": context["source_anchor"], "correspondence_status": mapping["status"],
                        "candidate_context_span": mapping["candidate_span"], "status": context_status,
                        "checks": checks})
    reference_pages = {context["page_number"] for context in reference["contexts"]}
    total_checks = sum(totals.values())
    coverage = {
        "contexts": {"total": len(results),
                     **{state: sum(r["status"] == state for r in results) for state in totals},
                     "mapped": sum(m["status"] == "mapped" for m in correspondence["contexts"])},
        "checks": {"total": total_checks, "evaluated": totals["passed"] + totals["failed"], **totals},
        "pages": {"source_page_count": page_count, "with_reference_contexts": sorted(reference_pages),
                  "without_reference_contexts_count": page_count - len(reference_pages),
                  "unchecked_selected_pages": sorted(n for n, p in pages.items()
                                                     if n not in reference_pages and p["status"] != "deferred"),
                  "unchecked_deferred_pages": sorted(n for n, p in pages.items()
                                                     if n not in reference_pages and p["status"] == "deferred"),
                  "failed_candidate_pages": sorted(n for n, p in pages.items() if p["status"] == "retry_failed"),
                  "empty_candidate_pages": sorted(n for n, p in pages.items()
                                                  if p["status"] == "available" and not p["text"].strip()),
                  "full_page_verification_claimed": False},
    }
    return {
        "schema_version": 1, "kind": "ocr_context_evaluation", "source_sha256": source_sha256,
        "recovery_sha256": recovery_sha256, "reference_sha256": reference_sha256,
        "page_count": page_count, "contexts": results, "coverage": coverage,
        "category_totals": category_totals,
        "requires_attention": bool(totals["failed"] or totals["abstained"]
                                   or coverage["pages"]["unchecked_selected_pages"]
                                   or coverage["pages"]["unchecked_deferred_pages"]),
        "policy": {"name": "exact_flanking_occurrence_v1", "offset_unit": "raw_unicode_code_points",
                   "normalization": "none", "scope": "reviewed_context_checks_only",
                   "approval_semantics": "operator_claims_not_authenticated",
                   "semantic_entailment_verified": False, "physical_correspondence_verified": False,
                   "full_page_verification_claimed": False},
        "canonical_extraction_modified": False,
    }
