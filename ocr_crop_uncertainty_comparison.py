"""Explicit v2 crop references and whole-crop uncertainty-aware comparisons.

This pure outward adapter preserves the v1 APIs and scoring math. An unresolved
reference is never projected into v1 and never reaches a scorer. Journal replay
proves internal declaration consistency, not ancestry, source authenticity or
human consent. The host must bind actual input bytes, displayed view and action.
Raw JSON decoding, parent continuity, storage and UI authority belong elsewhere.
"""

from __future__ import annotations

import hashlib
import json

import ocr_crop_comparison as crops
import ocr_crop_raster_view as raster
import ocr_crop_uncertainty_journal as history


MAX_REFERENCE_BYTES = 256 * 1024
MAX_RESULT_BYTES = 2 * 1024 * 1024
_REFERENCE_KEYS = frozenset(("schema_version", "kind", "reference_id", "scope", "anchor",
    "reference", "critical_tokens", "annotations", "declaration_sha256", "journal",
    "review", "reference_sha256"))
_REASONS = ("whole_page_unsupported", "region_missing", "candidate_unavailable",
            "reference_required", "reference_uncertain", "metric_limit")


def _fail():
    raise ValueError("invalid uncertain crop reference or comparison binding")


def _bytes(value):
    # Includes exact primitive/depth/node/UTF-8 preflight before copying.
    return history._bytes(value, maximum=MAX_REFERENCE_BYTES)


def _uncertainty(annotations):
    return {
        "annotation_total": len(annotations),
        "unresolved_uncertain": sum(row["status"] == "unresolved" and row["kind"] == "uncertain"
                                    for row in annotations),
        "unresolved_illegible": sum(row["status"] == "unresolved" and row["kind"] == "illegible"
                                    for row in annotations),
        "resolved": sum(row["status"] == "resolved" for row in annotations),
        "dismissed": sum(row["status"] == "dismissed" for row in annotations),
        "unknown_character_count": None, "unknown_word_count": None,
        "character_coverage": None, "word_coverage": None,
    }


def _from_journal(journal, anchor_report, anchor_report_sha256):
    replayed = history.replay_crop_journal(journal, anchor_report=anchor_report,
                                         anchor_report_sha256=anchor_report_sha256)
    declaration = replayed["declaration"]
    counts = _uncertainty(declaration["annotations"])
    unresolved = bool(counts["unresolved_uncertain"] + counts["unresolved_illegible"])
    projection = None
    if not unresolved:
        # This is unchanged full v1 validation, not an empty-prediction score.
        # Historical reading-presence checks already ran once in journal replay;
        # do not rescan that history or reset its cumulative work budget here.
        projection = crops.build_crop_reference(anchor_report,
            anchor_report_sha256=anchor_report_sha256,
            anchor_region_id=declaration["anchor"]["region_id"],
            reference=declaration["reference"], critical_tokens=declaration["critical_tokens"],
            confirmed=True)
    reference = {**declaration, "declaration_sha256": replayed["declaration_sha256"],
                 "journal": replayed["journal"], "review": "operator_checked_requested_crop"}
    reference["reference_sha256"] = hashlib.sha256(_bytes(reference)).hexdigest()
    return json.loads(_bytes(reference)), projection, counts


def build_crop_reference_v2(anchor_report, *, anchor_report_sha256, journal, confirmed):
    """Bind one explicitly confirmed journal head, resolved or unscorable.

    ``confirmed`` is a declaration supplied by the trusted host, not proof that
    a person saw a scan. No OCR text is supplied or inserted by this builder.
    Resolved heads must pass the complete legacy critical-occurrence validation.
    """
    if type(confirmed) is not bool or confirmed is not True:
        _fail()
    return _from_journal(journal, anchor_report, anchor_report_sha256)[0]


def _validated(payload, anchor_report, anchor_report_sha256):
    # Cheap closed-shape checks before whole-envelope traversal or allocation.
    history._fields(payload, _REFERENCE_KEYS)
    if (type(payload["schema_version"]) is not int or payload["schema_version"] != 2
            or type(payload["kind"]) is not str or payload["kind"] != "ocr_crop_reference"
            or type(payload["review"]) is not str or payload["review"] != "operator_checked_requested_crop"):
        _fail()
    history._authored(payload["reference"], payload["critical_tokens"])
    for key in ("declaration_sha256", "reference_sha256"):
        history._hex(payload[key])
    identifier = payload["reference_id"]
    if type(identifier) is not str or len(identifier) != 69 or not identifier.startswith("crop-"):
        _fail()
    history._hex(identifier[5:])
    anchor = payload["anchor"]
    history._fields(anchor, {"region_id", "report_sha256", "record_sha256", "operation", "recipe_sha256"})
    crops._id(anchor["region_id"])
    for key in ("report_sha256", "record_sha256", "recipe_sha256"):
        history._hex(anchor[key])
    if type(anchor["operation"]) is not str or anchor["operation"] not in ("regions", "hardscan"):
        _fail()
    raster._scope(payload["scope"])
    annotations = payload["annotations"]
    if type(annotations) is not list or len(annotations) > history.MAX_ANNOTATIONS:
        _fail()
    for annotation in annotations:
        history._annotation_shape(annotation)
        raster._vector(annotation["bbox"], 4, canonical=True)
    history._fields(payload["journal"], history._JOURNAL_KEYS)
    raw = _bytes(payload)
    expected, projection, counts = _from_journal(payload["journal"], anchor_report,
                                                anchor_report_sha256)
    if raw != _bytes(expected):
        _fail()
    return expected, projection, counts


def validate_crop_reference_v2(payload, *, anchor_report, anchor_report_sha256):
    """Rebuild the exact v2 envelope from one bounded replay of its full journal."""
    return _validated(payload, anchor_report, anchor_report_sha256)[0]


def compare_crop_candidates_v2(baseline_report, retry_report, crop_reference, *,
                              baseline_report_sha256, retry_report_sha256,
                              baseline_region_id, retry_region_id):
    """Compare complete candidates or retain an explicit whole-pair abstention.

    Accept only None or a v2 reference. Unsupported/missing/unavailable candidate
    reasons take precedence, but uncertainty counts remain independently visible.
    Unknown text lengths and character/word coverage stay null, never guessed.
    """
    reference, projection = None, None
    counts = _uncertainty([])
    state = "none"
    if crop_reference is not None:
        reference, projection, counts = _validated(crop_reference, baseline_report,
                                                   baseline_report_sha256)
        if reference["anchor"]["region_id"] != baseline_region_id:
            _fail()
        state = "unresolved" if projection is None else "resolved"
    # Exactly one legacy comparison call. None dispatch validates both complete
    # reports and retains occurrence/edge/setting diagnostics without invoking
    # compare_ocr or evaluate_ocr. Only a fully resolved v1 projection may score.
    result = crops.compare_crop_candidates(baseline_report, retry_report, projection,
        baseline_report_sha256=baseline_report_sha256,
        retry_report_sha256=retry_report_sha256, baseline_region_id=baseline_region_id,
        retry_region_id=retry_region_id)
    if reference is not None and reference["scope"] != result["scope"]:
        _fail()
    result.update(schema_version=2, reference_state=state, uncertainty=counts,
        reference_id=None if reference is None else reference["reference_id"],
        reference_sha256=None if reference is None else reference["reference_sha256"])
    if state == "unresolved" and result["reason"] == "reference_required":
        result["reason"] = "reference_uncertain"
    scored = result["coverage"]["scored_pairs"]
    result["coverage"].update(reference_occurrences=int(reference is not None),
        unscorable_pairs=1 - scored, reference_unresolved_pairs=int(state == "unresolved"),
        unscorable_reasons={reason: int(result["reason"] == reason) for reason in _REASONS})
    result["scope_notice"] = ("one requested physical crop only; when scored, extra edge text remains scored; "
                              "not whole-page accuracy or causal attribution")
    return json.loads(crops._bytes(result, MAX_RESULT_BYTES))
