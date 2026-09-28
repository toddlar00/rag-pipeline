"""Reproducible, bounded spot audits of retained apparently-good OCR candidates.

Sampling confidence is an engine declaration, not calibrated accuracy. Records
are editable historical operator declarations, not authenticated human review or
fresh approval. No source rendering, OCR, storage or canonical adoption occurs.
Only audit_summary calls the unchanged transcription scorer, on completed rows.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re

import ocr_evaluation
from ocr_review import ReviewDocument


MAX_PAGES = 10_000
MAX_SAMPLE = 100
MAX_TEXT = 20_000
MAX_NOTE = 2_000
MAX_AUTHORED_CHARS = 2_000_000
MAX_AUDIT_BYTES = 8 * 1024 * 1024
MAX_RANDOM_BLOCKS = 4096
ALGORITHM = "sha256-counter-rejection-fisher-yates-v1"
_DOMAIN = b"ocr-spot-audit-sha256-counter-rejection-fisher-yates-v1\0"
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_ROOT = {"schema_version", "kind", "source_sha256", "recovery_sha256", "page_count",
         "parent_audit_sha256", "plan", "records", "drafts", "manual_review_required",
         "canonical_extraction_modified"}
_PLAN = {"algorithm", "threshold", "seed", "requested_sample_size", "frame",
         "selected_pages", "inclusion_probability"}
_REASONS = ("unselected", "deferred", "failed", "empty", "long", "missing_confidence", "low_confidence")
_OUTCOMES = ("pending", "reviewed", "unresolved", "unavailable")


def _fail():
    raise ValueError("invalid or over-budget spot audit")


def _fields(value, expected):
    if (type(value) is not dict or len(value) != len(expected)
            or any(type(key) is not str for key in value) or set(value) != expected):
        _fail()
    return value


def _integer(value, lower, upper):
    if type(value) is not int or not lower <= value <= upper:
        _fail()
    return value


def _sha(value, *, nullable=False):
    if value is None and nullable:
        return value
    if type(value) is not str or _SHA.fullmatch(value) is None:
        _fail()
    return value


def _text(value, maximum, *, nonempty=False):
    if type(value) is not str or len(value) > maximum:
        _fail()
    try:
        value.encode("utf-8")
    except UnicodeError:
        _fail()
    if nonempty and not value.strip():
        _fail()
    return value


def _threshold(value):
    if (type(value) is not float or not math.isfinite(value) or not 0. <= value <= 1.
            or value == 0. and math.copysign(1., value) < 0):
        _fail()
    return value


def _byte_bound(value):
    # Account for the pretty private-JSON representation and its trailing LF.
    size = 1
    for piece in json.JSONEncoder(ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2).iterencode(value):
        size += len(piece.encode("utf-8"))
        if size > MAX_AUDIT_BYTES:
            _fail()


def _bound_document(document):
    if type(document) is not ReviewDocument:
        _fail()
    _integer(document.page_count, 1, MAX_PAGES)
    _sha(document.source_sha256)
    _sha(document.recovery_sha256)
    # Revalidate declared report consistency, not the host's file provenance.
    bound = ReviewDocument(document.recovery_snapshot(), recovery_sha256=document.recovery_sha256)
    if (bound.source_sha256 != document.source_sha256 or bound.page_count != document.page_count
            or document.page_numbers != bound.page_numbers):
        _fail()
    return bound


def _sample(eligible, size, seed):
    """Partial Fisher-Yates over ascending IDs; no modulo-biased range reduction.

Block i is SHA256(domain || 32-byte seed || unsigned 8-byte big-endian i),
starting at i=0. Take its first eight bytes as unsigned big-endian x. For
range r, reject x >= 2**64 - (2**64 % r); otherwise use x % r. A rejection
consumes a block. Swap j with j+draw for j=0..n-1; retain that prefix in order.
The n/N inclusion probability is the nominal uniform-bitstream design, not a
cryptographic proof about SHA256, independence of seed choice or population.
"""
    values, counter = list(eligible), 0
    prefix = _DOMAIN + bytes.fromhex(seed)
    for index in range(min(size, len(values))):
        remaining = len(values) - index
        limit = (1 << 64) - ((1 << 64) % remaining)
        while True:
            if counter >= MAX_RANDOM_BLOCKS:
                _fail()
            value = int.from_bytes(hashlib.sha256(prefix + counter.to_bytes(8, "big")).digest()[:8], "big")
            counter += 1
            if value < limit:
                break
        other = index + value % remaining
        values[index], values[other] = values[other], values[index]
    return values[:size]


def _plan(document, threshold, sample_size, seed):
    _threshold(threshold)
    _integer(sample_size, 1, MAX_SAMPLE)
    _sha(seed)
    frame, eligible = [], []
    for number in document.page_numbers:
        page = document.page(number)
        candidate, mean = page["candidate"], None
        if candidate is not None and candidate["mean_confidence"] is not None:
            mean = float(candidate["mean_confidence"])
            if not math.isfinite(mean) or not 0. <= mean <= 1.:
                _fail()
            if mean == 0.:
                mean = 0.
        reason = {"not_selected": "unselected", "deferred": "deferred", "retry_failed": "failed"}.get(page["status"])
        if reason is None:
            if candidate is None:
                _fail()
            text = candidate["text"]
            reason = ("empty" if not text.strip() else "long" if len(text) > MAX_TEXT
                      else "missing_confidence" if mean is None
                      else "low_confidence" if mean < threshold else None)
        if reason is None:
            eligible.append(number)
        frame.append({"page_number": number, "eligible": reason is None, "reason": reason,
                      "mean_confidence": mean})
    selected = _sample(eligible, sample_size, seed)
    return {"algorithm": ALGORITHM, "threshold": threshold, "seed": seed,
            "requested_sample_size": sample_size, "frame": frame, "selected_pages": selected,
            "inclusion_probability": {"numerator": len(selected), "denominator": len(eligible)}}


def _validated(payload, document, parent):
    root = _fields(payload, _ROOT)
    if (type(root["schema_version"]) is not int or root["schema_version"] != 1
            or type(root["kind"]) is not str or root["kind"] != "ocr_spot_audit"
            or root["manual_review_required"] is not True or root["canonical_extraction_modified"] is not False):
        _fail()
    _sha(root["source_sha256"])
    _sha(root["recovery_sha256"])
    _sha(root["parent_audit_sha256"], nullable=True)
    _sha(parent, nullable=True)
    _integer(root["page_count"], 1, MAX_PAGES)
    if root["parent_audit_sha256"] != parent:
        _fail()
    plan = _fields(root["plan"], _PLAN)
    if type(plan["algorithm"]) is not str or plan["algorithm"] != ALGORITHM:
        _fail()
    _threshold(plan["threshold"])
    _sha(plan["seed"])
    _integer(plan["requested_sample_size"], 1, MAX_SAMPLE)
    frame, selected = plan["frame"], plan["selected_pages"]
    if type(frame) is not list or len(frame) != root["page_count"]:
        _fail()
    for index, row in enumerate(frame, 1):
        _fields(row, {"page_number", "eligible", "reason", "mean_confidence"})
        _integer(row["page_number"], index, index)
        if type(row["eligible"]) is not bool:
            _fail()
        if row["reason"] is not None and (type(row["reason"]) is not str or row["reason"] not in _REASONS):
            _fail()
        mean = row["mean_confidence"]
        if mean is not None:
            _threshold(mean)
    if type(selected) is not list or len(selected) > MAX_SAMPLE:
        _fail()
    for number in selected:
        _integer(number, 1, root["page_count"])
    if len(set(selected)) != len(selected):
        _fail()
    fraction = _fields(plan["inclusion_probability"], {"numerator", "denominator"})
    _integer(fraction["numerator"], 0, MAX_SAMPLE)
    _integer(fraction["denominator"], 0, root["page_count"])
    total = 0
    for key, fields in (("records", {"page_number", "outcome", "text", "note"}),
                        ("drafts", {"page_number", "text"})):
        rows = root[key]
        if type(rows) is not list or len(rows) != len(selected):
            _fail()
        for number, row in zip(selected, rows, strict=True):
            _fields(row, fields)
            _integer(row["page_number"], number, number)
            if key == "drafts":
                total += len(_text(row["text"], MAX_TEXT))
            else:
                _record(row["outcome"], row["text"], row["note"])
                total += len(row["text"] or "") + len(row["note"])
            if total > MAX_AUTHORED_CHARS:
                _fail()
    _byte_bound(root)
    bound = _bound_document(document)
    if (root["source_sha256"] != bound.source_sha256 or root["recovery_sha256"] != bound.recovery_sha256
            or root["page_count"] != bound.page_count):
        _fail()
    if plan != _plan(bound, plan["threshold"], plan["requested_sample_size"], plan["seed"]):
        _fail()
    return copy.deepcopy(root), bound


def _record(outcome, text, note):
    if type(outcome) is not str or outcome not in _OUTCOMES:
        _fail()
    _text(note, MAX_NOTE, nonempty=outcome in ("unresolved", "unavailable"))
    if outcome == "reviewed":
        _text(text, MAX_TEXT)
    elif text is not None or outcome == "pending" and note != "":
        _fail()


def create_audit(document: ReviewDocument, *, threshold: float, sample_size: int,
                 seed: str, parent_audit_sha256: str | None = None) -> dict:
    """Create a pending plan; caller/host owns seed choice and actual file pins."""
    _threshold(threshold)
    _integer(sample_size, 1, MAX_SAMPLE)
    _sha(seed)
    _sha(parent_audit_sha256, nullable=True)
    bound = _bound_document(document)
    plan = _plan(bound, threshold, sample_size, seed)
    payload = {"schema_version": 1, "kind": "ocr_spot_audit", "source_sha256": bound.source_sha256,
        "recovery_sha256": bound.recovery_sha256, "page_count": bound.page_count,
        "parent_audit_sha256": parent_audit_sha256, "plan": plan,
        "records": [{"page_number": page, "outcome": "pending", "text": None, "note": ""}
                    for page in plan["selected_pages"]],
        "drafts": [{"page_number": page, "text": ""} for page in plan["selected_pages"]],
        "manual_review_required": True, "canonical_extraction_modified": False}
    _byte_bound(payload)
    return payload


def validate_audit(payload, document: ReviewDocument, *, parent_audit_sha256=None) -> dict:
    """Require exact declared expected parent; hashes do not authenticate lineage."""
    return _validated(payload, document, parent_audit_sha256)[0]


def _own_parent(payload):
    # Editing a declaration is not a host's actual retained-parent admission.
    return _fields(payload, _ROOT)["parent_audit_sha256"]


def record_review(payload, document: ReviewDocument, *, page_number: int, outcome: str,
                  text: str | None, note: str, confirmed: bool) -> dict:
    _integer(page_number, 1, MAX_PAGES)
    _record(outcome, text, note)
    if type(confirmed) is not bool or confirmed is not (outcome == "reviewed"):
        _fail()
    result, _bound = _validated(payload, document, _own_parent(payload))
    if page_number not in result["plan"]["selected_pages"]:
        _fail()
    index = result["plan"]["selected_pages"].index(page_number)
    result["records"][index] = {"page_number": page_number, "outcome": outcome, "text": text, "note": note}
    return validate_audit(result, document, parent_audit_sha256=result["parent_audit_sha256"])


def remember_draft(payload, document: ReviewDocument, *, page_number: int, text: str) -> dict:
    """Retain unfinished text separately; never promote or replace a reviewed row."""
    _integer(page_number, 1, MAX_PAGES)
    _text(text, MAX_TEXT)
    result, _bound = _validated(payload, document, _own_parent(payload))
    if page_number not in result["plan"]["selected_pages"]:
        _fail()
    index = result["plan"]["selected_pages"].index(page_number)
    result["drafts"][index]["text"] = text
    return validate_audit(result, document, parent_audit_sha256=result["parent_audit_sha256"])


def audit_summary(payload, document: ReviewDocument) -> dict:
    """Explicit scoring only. Budget/normalization failures return no partial score."""
    value, bound = _validated(payload, document, _own_parent(payload))
    plan = value["plan"]
    eligible = plan["inclusion_probability"]["denominator"]
    counts = {"universe": bound.page_count, "eligible": eligible, "excluded": bound.page_count - eligible,
              "sampled": len(plan["selected_pages"]), "eligible_unselected": eligible - len(plan["selected_pages"])}
    counts.update({outcome: sum(row["outcome"] == outcome for row in value["records"]) for outcome in _OUTCOMES})
    records = [{"id": f"page-{row['page_number']:05d}", "reference": row["text"],
                "prediction": bound.page(row["page_number"])["candidate"]["text"]}
               for row in value["records"] if row["outcome"] == "reviewed"]
    metrics = ocr_evaluation.evaluate_ocr({"schema_version": 1, "records": records}) if records else None
    return {"schema_version": 1, "kind": "ocr_spot_audit_summary", "source_sha256": bound.source_sha256,
        "recovery_sha256": bound.recovery_sha256, "counts": counts,
        "exclusion_reasons": {reason: sum(row["reason"] == reason for row in plan["frame"]) for reason in _REASONS},
        "inclusion_probability": copy.deepcopy(plan["inclusion_probability"]), "metrics": metrics,
        "sampling_scope": "Nominal uniform-without-replacement design over eligible retained candidates only; seed choice is not independently verified.",
        "metric_scope": "Completed reviewed subset only; unresolved, unavailable, pending and excluded pages are not scored. Not representative population accuracy.",
        "confidence_scope": "Engine confidence is an eligibility declaration, not calibrated accuracy.",
        "reference_attestation": "Historical operator-declared scan review; identity and independent human review are not authenticated. No fresh approval is restored.",
        "manual_review_required": True, "canonical_extraction_modified": False}
