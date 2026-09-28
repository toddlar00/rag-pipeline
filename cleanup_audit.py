"""Bounded observations of core normalization, not a pipeline correctness verdict.

Selected recovery spans are exact raw Unicode-code-point intervals. Edit spans
belong to successive normalization states, never PDF geometry or original text
coordinates. Recovery claims are validated, not authenticated.
"""

from __future__ import annotations

from collections import Counter
import hashlib
import re
import unicodedata

import chunking_core
from ocr_recovery_comparison import validate_recovery_report


MAX_ENTRIES = 64
MAX_FRAGMENT_CHARS = 4096
MAX_TOTAL_CHARS = 65_536
MAX_STEP_CHARS = 16_384
MAX_ENTRY_TRACE_CHARS = 65_536
MAX_TOTAL_TRACE_CHARS = 262_144
PROFILE = "core_normalization_v1"
RULE_IDS = tuple("unicode_u" + code for code in (
    "00a0", "2018", "2019", "201c", "201d", "2013", "2014",
    "fb01", "fb02", "fb00", "fb03", "fb04", "fffd")) + (
    "safe_zero_width", "private_use_digits", "editorial_boilerplate",
    "section_marker_line", "decorative_square_line", "perma_url", "bare_perma_url",
    "spaced_url", "spaced_hyphen", "bracketed_contraction", "known_fused_term",
    "sentence_space", "horizontal_whitespace", "blank_lines", "strip_headers_footers",
    "dedup_nearby_lines", "outer_whitespace")
RISK_IDS = (
    "punctuation_or_symbol_changed", "numeric_sequence_changed",
    "word_or_identifier_changed", "repetition_reduced", "non_whitespace_removed",
    "all_text_removed", "empty_input")
EXCLUSIONS = (
    "docling_extraction", "rag_source_aware_normalization_wrappers",
    "source_attested_repairs", "chunk_deduplication_splitting_merging",
    "markdown_publication", "retrieval_indexing", "source_completeness",
    "semantic_correctness")


def _fields(value: object, keys: set[str]) -> dict:
    if type(value) is not dict or set(value) != keys:
        raise ValueError("invalid cleanup audit fields")
    return value


def _int(value: object, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError("invalid cleanup audit integer")
    return value


def _digest(value: object) -> str:
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("invalid cleanup audit digest")
    return value


def _hash(text: str) -> str:
    try:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()
    except UnicodeError:
        raise ValueError("invalid cleanup audit Unicode") from None


def validate_cleanup_plan(payload: object, *, source_sha256: str,
                          recovery_sha256: str, page_count: int) -> dict:
    """Validate recipe structure; availability and span bounds need recovery text."""
    _int(page_count, 1, 5000)
    source_sha256, recovery_sha256 = _digest(source_sha256), _digest(recovery_sha256)
    plan = _fields(payload, {"schema_version", "kind", "source_sha256", "recovery_sha256",
                             "profile", "offset_unit", "entries"})
    if (_int(plan["schema_version"], 1, 1) != 1 or plan["kind"] != "cleanup_audit_plan"
            or plan["profile"] != PROFILE or plan["offset_unit"] != "raw_unicode_code_points"
            or _digest(plan["source_sha256"]) != source_sha256
            or _digest(plan["recovery_sha256"]) != recovery_sha256):
        raise ValueError("cleanup audit plan binding or profile differs")
    entries = plan["entries"]
    if type(entries) is not list or not 1 <= len(entries) <= MAX_ENTRIES:
        raise ValueError("cleanup audit entry budget is invalid")
    result, previous, total = [], None, 0
    for value in entries:
        item = _fields(value, {"page_number", "candidate_span"})
        page = _int(item["page_number"], 1, page_count)
        span = item["candidate_span"]
        if span is not None:
            if type(span) is not list or len(span) != 2:
                raise ValueError("invalid cleanup audit span")
            start, end = (_int(n, 0, 100_000) for n in span)
            if end < start or end - start > MAX_FRAGMENT_CHARS:
                raise ValueError("cleanup audit fragment exceeds bounds")
            total += end - start
            span = [start, end]
        if previous is not None:
            prior_page, prior_span = previous
            if (page < prior_page or (page == prior_page and (
                    span is None or prior_span is None or span[0] < prior_span[1]
                    or span[0] == span[1] or prior_span[0] == prior_span[1]))):
                raise ValueError("cleanup audit spans overlap, repeat or are unordered")
        previous = page, span
        result.append({"page_number": page, "candidate_span": span})
    if total > MAX_TOTAL_CHARS:
        raise ValueError("cleanup audit selected-text budget exceeded")
    return {**plan, "entries": result}


def _risks(before: str, after: str) -> list[str]:
    flags = set()

    def punctuation(text: str) -> Counter:
        return Counter(c for c in text if unicodedata.category(c)[0] in "PS")

    if punctuation(before) != punctuation(after):
        flags.add("punctuation_or_symbol_changed")
    if re.findall(r"[+-]?\d+(?:[.,]\d+)*", before) != re.findall(r"[+-]?\d+(?:[.,]\d+)*", after):
        flags.add("numeric_sequence_changed")
    if re.findall(r"\w+(?:[-'./]\w+)*", before) != re.findall(r"\w+(?:[-'./]\w+)*", after):
        flags.add("word_or_identifier_changed")
    old_lines = Counter(line.strip() for line in before.splitlines() if line.strip())
    new_lines = Counter(line.strip() for line in after.splitlines() if line.strip())
    if any(count > 1 and new_lines[line] < count for line, count in old_lines.items()):
        flags.add("repetition_reduced")
    if sum(not c.isspace() for c in after) < sum(not c.isspace() for c in before):
        flags.add("non_whitespace_removed")
    if before.strip() and not after.strip():
        flags.add("all_text_removed")
    return [flag for flag in RISK_IDS if flag in flags]


class _TraceBudget(Exception):
    pass


def _audit_fragment(text: str, remaining_trace: int) -> tuple[dict, int]:
    _hash(text)
    edits, trace_chars, previous_rule, state = [], 0, -1, text

    def observe(rule: str, before: str, after: str) -> None:
        nonlocal trace_chars, previous_rule, state
        if (rule not in RULE_IDS or RULE_IDS.index(rule) <= previous_rule
                or before != state or type(after) is not str or before == after):
            raise RuntimeError("cleanup audit observed an unsupported rule sequence")
        if len(after) > MAX_STEP_CHARS:
            raise _TraceBudget()
        start, old_end, new_end = 0, len(before), len(after)
        while start < min(old_end, new_end) and before[start] == after[start]:
            start += 1
        while old_end > start and new_end > start and before[old_end - 1] == after[new_end - 1]:
            old_end -= 1
            new_end -= 1
        removed, inserted = before[start:old_end], after[start:new_end]
        cost = len(removed) + len(inserted)
        if trace_chars + cost > min(MAX_ENTRY_TRACE_CHARS, remaining_trace):
            raise _TraceBudget()
        edits.append({"step": len(edits) + 1, "rule_id": rule,
                      "before_sha256": _hash(before), "after_sha256": _hash(after),
                      "before_span": [start, old_end], "after_span": [start, new_end],
                      "removed": removed, "inserted": inserted, "risks": _risks(before, after)})
        trace_chars += cost
        previous_rule, state = RULE_IDS.index(rule), after

    try:
        output = chunking_core._normalize_text(text, audit_hook=observe)
    except _TraceBudget:
        return {"status": "abstained", "reason": "trace_budget", "original_text": text,
                "normalized_text": None, "original_sha256": _hash(text), "normalized_sha256": None,
                "edits": [], "risks": []}, 0
    if output != state:
        raise RuntimeError("cleanup audit normalization escaped observation")
    risks = {flag for edit in edits for flag in edit["risks"]}
    if not text.strip():
        risks.add("empty_input")
    return {"status": "changed" if edits else "unchanged", "reason": None,
            "original_text": text, "normalized_text": output, "original_sha256": _hash(text),
            "normalized_sha256": _hash(output), "edits": edits,
            "risks": [flag for flag in RISK_IDS if flag in risks]}, trace_chars


def build_cleanup_audit(recovery: object, plan: object, *, recovery_sha256: str) -> dict:
    """Replay only default core normalization over exact selected OCR text spans.

    Nothing is accepted as correct or written back. Hash agreement binds inputs;
    it is not an authenticated statement about their origin or source coverage.
    """
    recovery = validate_recovery_report(recovery)
    plan = validate_cleanup_plan(plan, source_sha256=recovery["source_sha256"],
                                 recovery_sha256=recovery_sha256, page_count=recovery["page_count"])
    pages = {page["page_number"]: page for page in recovery["pages"]}
    deferred = {page["page_number"] for page in recovery["deferred_pages"]}
    results, used, selected_chars = [], 0, 0
    spans_by_page: dict[int, list] = {}
    for ordinal, entry in enumerate(plan["entries"], 1):
        number, span = entry["page_number"], entry["candidate_span"]
        page = pages.get(number)
        candidate = None if page is None else page["candidate"]
        spans_by_page.setdefault(number, []).append(span)
        if candidate is None:
            if span is not None:
                raise ValueError("unavailable cleanup input cannot supply a text span")
            reason = "retry_failed" if page else "deferred" if number in deferred else "not_selected"
            result = {"status": "abstained", "reason": reason, "original_text": None,
                      "normalized_text": None, "original_sha256": None, "normalized_sha256": None,
                      "edits": [], "risks": []}
        else:
            text = candidate["text"]
            if span is None or span[1] > len(text) or (span[0] == span[1] and text):
                raise ValueError("cleanup input span does not select available candidate text")
            fragment = text[span[0]:span[1]]
            selected_chars += len(fragment)
            result, cost = _audit_fragment(fragment, MAX_TOTAL_TRACE_CHARS - used)
            used += cost
        results.append({"entry_number": ordinal, **entry, **result})
    fully_selected = []
    for number, spans in spans_by_page.items():
        candidate = (pages.get(number) or {}).get("candidate")
        if (candidate is not None and spans[0][0] == 0 and spans[-1][1] == len(candidate["text"])
                and all(left[1] == right[0] for left, right in zip(spans, spans[1:]))):
            fully_selected.append(number)
    audited_pages = sorted({item["page_number"] for item in results if item["status"] != "abstained"})
    summary = {status: sum(item["status"] == status for item in results)
               for status in ("changed", "unchanged", "abstained")}
    summary.update({"entries": len(results), "changed_steps": sum(len(item["edits"]) for item in results),
                    "risk_entries": sum(bool(item["risks"]) for item in results)})
    coverage = {"source_page_count": recovery["page_count"], "selected_pages": sorted(spans_by_page),
                "audited_pages": audited_pages, "fully_selected_candidate_pages": fully_selected,
                "partially_selected_candidate_pages": sorted(
                    number for number in spans_by_page if number in pages
                    and pages[number]["candidate"] is not None and number not in fully_selected),
                "unselected_recovery_pages": sorted(set(pages) - set(spans_by_page)),
                "deferred_pages": sorted(deferred),
                "unselected_source_page_count": recovery["page_count"] - len(spans_by_page),
                "selected_characters": selected_chars, "retained_trace_characters": used}
    return {"schema_version": 1, "kind": "cleanup_fidelity_audit", "source_sha256": recovery["source_sha256"],
            "recovery_sha256": recovery_sha256, "profile": PROFILE,
            "normalization_policy_version": chunking_core.NORMALIZATION_POLICY_VERSION,
            "offset_unit": "raw_unicode_code_points", "edit_coordinates": "successive_rule_states_not_source_coordinates",
            "edit_contract": "one exact reversible replacement envelope per changed rule; not a minimal edit alignment",
            "scope": "selected recovery candidate fragments through default core normalization only",
            "excluded_stages": list(EXCLUSIONS), "risk_contract": "heuristic review warnings; neither errors nor verified meaning",
            "limits": {"entries": MAX_ENTRIES, "fragment_characters": MAX_FRAGMENT_CHARS,
                       "total_characters": MAX_TOTAL_CHARS, "step_characters": MAX_STEP_CHARS,
                       "entry_trace_characters": MAX_ENTRY_TRACE_CHARS, "total_trace_characters": MAX_TOTAL_TRACE_CHARS},
            "entries": results, "summary": summary, "coverage": coverage,
            "canonical_extraction_modified": False, "accuracy_verified": False,
            "source_completeness_verified": False,
            "requires_attention": bool(summary["changed"] or summary["abstained"] or summary["risk_entries"]
                                       or len(fully_selected) != recovery["page_count"])}
