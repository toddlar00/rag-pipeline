"""Bounded crop-uncertainty authoring journal.

This module is not a reviewed-reference, scoring or pack-validation boundary.
Public functions return detached authoring data and never confer consent. The
prospective complete v2 reference envelope is budgeted but is not emitted.
Inputs here are objects; the later pack/IO boundary must perform bounded strict
raw JSON decoding with duplicate-key rejection before calling these functions.

The pure geometry contract is: validate_raster_view(payload, *,
scope) returns a canonical dict; selection_to_scope_bbox(selection, *,
raster_view, scope) returns the canonical nominal-scope fraction rectangle.
Both must be pure and bounded. No caller-supplied validators/callbacks are used.
Existing v1 functions and scoring math remain untouched.
"""

from __future__ import annotations

import hashlib
import json
import math
import re

import ocr_crop_comparison as crops
import ocr_evaluation
from ocr_crop_raster_view import selection_to_scope_bbox, validate_raster_view


MAX_BYTES = 256 * 1024
MAX_NODES = 100_000
MAX_DEPTH = 32
MAX_REVISIONS = 64
MAX_VIEWS = 64
MAX_ANNOTATIONS = 128
MAX_CHANGES = MAX_REVISIONS * MAX_ANNOTATIONS
MAX_RESET_VISITS = MAX_REVISIONS * MAX_ANNOTATIONS
MAX_OVERLAP_CHECKS = MAX_REVISIONS * MAX_ANNOTATIONS * (MAX_ANNOTATIONS - 1) // 2
MAX_READING_SCAN = 16 * 1024 * 1024
MAX_VIEW_BYTES = 4 * 1024
_SHA = re.compile(r"[0-9a-f]{64}")
_ANNOTATION_ID = re.compile(r"a[0-9]{6}")
_JOURNAL_KEYS = {"binding", "base", "base_declaration_sha256", "views", "revisions", "head_sha256"}
_BASE_KEYS = {"reference", "critical_tokens", "annotations"}
_BINDING_KEYS = {"reference_id", "scope", "anchor"}
_ANNOTATION_KEYS = {"annotation_id", "bbox", "kind", "status", "tentative_text", "raw_span", "resolution"}
_CHANGE_KEYS = {"action", "annotation", "selection", "view_sha256", "reason"}
_REVISION_KEYS = {"sequence", "previous_revision_sha256", "before_declaration_sha256",
                  "after_declaration_sha256", "reference", "critical_tokens", "reset_anchors",
                  "annotation_changes", "affected_annotation_ids", "reason", "revision_sha256"}


def _fail():
    raise ValueError("invalid crop uncertainty journal or binding")


def _fields(value, keys):
    if (type(value) is not dict or len(value) != len(keys)
            or any(type(key) is not str for key in value) or set(value) != keys):
        _fail()


def _annotation_shape(value):
    _fields(value, _ANNOTATION_KEYS)
    _text(value["annotation_id"], 7)
    _text(value["kind"], 16)
    _text(value["status"], 16)
    if type(value["bbox"]) is not list or len(value["bbox"]) != 4:
        _fail()
    if value["tentative_text"] is not None:
        _text(value["tentative_text"], 2_000)
    if value["raw_span"] is not None:
        span = value["raw_span"]
        _fields(span, {"reference_text_sha256", "start", "end"})
        _hex(span["reference_text_sha256"])
        if type(span["start"]) is not int or type(span["end"]) is not int:
            _fail()
    if value["resolution"] is not None:
        _fields(value["resolution"], {"decision", "reading"})
        _text(value["resolution"]["decision"], 32)
        _text(value["resolution"]["reading"], 20_000)


def _changes_shape(changes):
    if type(changes) is not list or len(changes) > MAX_ANNOTATIONS:
        _fail()
    for change in changes:
        _fields(change, _CHANGE_KEYS)
        _text(change["action"], 32)
        _text(change["reason"], 512, nonblank=True)
        _hex(change["view_sha256"])
        _annotation_shape(change["annotation"])
        if change["selection"] is not None:
            _fields(change["selection"], {"kind", "pixel_bbox"})
            _text(change["selection"]["kind"], 32)
            rectangle = change["selection"]["pixel_bbox"]
            if rectangle is not None and (type(rectangle) is not list or len(rectangle) != 4):
                _fail()


def _journal_shape(value):
    """Reject closed-container/cardinality faults before detached copying."""
    _fields(value, _JOURNAL_KEYS)
    _fields(value["binding"], _BINDING_KEYS)
    _fields(value["binding"]["anchor"], {"report_sha256", "region_id", "record_sha256", "operation", "recipe_sha256"})
    _fields(value["base"], _BASE_KEYS)
    _authored(value["base"]["reference"], value["base"]["critical_tokens"])
    _hex(value["base_declaration_sha256"])
    _hex(value["head_sha256"])
    if (type(value["base"]["annotations"]) is not list or value["base"]["annotations"]
            or type(value["base"]["critical_tokens"]) is not list or len(value["base"]["critical_tokens"]) > 64
            or type(value["views"]) is not list or len(value["views"]) > MAX_VIEWS
            or type(value["revisions"]) is not list or len(value["revisions"]) > MAX_REVISIONS):
        _fail()
    for revision in value["revisions"]:
        _fields(revision, _REVISION_KEYS)
        if type(revision["sequence"]) is not int or type(revision["reset_anchors"]) is not bool:
            _fail()
        for key in ("previous_revision_sha256", "before_declaration_sha256", "after_declaration_sha256", "revision_sha256"):
            _hex(revision[key])
        _text(revision["reason"], 512, nonblank=True)
        if revision["reference"] is not None:
            _text(revision["reference"], 20_000)
        tokens = revision["critical_tokens"]
        if tokens is not None and (type(tokens) is not list or len(tokens) > 64):
            _fail()
        if tokens is not None:
            _authored("", tokens)
        identifiers = revision["affected_annotation_ids"]
        if type(identifiers) is not list or len(identifiers) > MAX_ANNOTATIONS:
            _fail()
        _changes_shape(revision["annotation_changes"])


def _bytes(value, maximum=MAX_BYTES):
    """Preflight scalar allocations and cumulative encoded size before copying."""
    try:
        crops._preflight_json(value, maximum, max_nodes=MAX_NODES, max_depth=MAX_DEPTH,
            max_key_characters=128, max_scalar_characters=20_000, max_integer_bits=64,
            finite_floats=True, fail=_fail)
        pieces, size = [], 0
        encoder = json.JSONEncoder(sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        for fragment in encoder.iterencode(value):
            raw = fragment.encode("utf-8")
            size += len(raw)
            if size > maximum:
                _fail()
            pieces.append(raw)
        return b"".join(pieces)
    except (TypeError, UnicodeError, OverflowError, RecursionError):
        _fail()


def _copy(value, maximum=MAX_BYTES):
    return json.loads(_bytes(value, maximum))


def _hash(value):
    return hashlib.sha256(_bytes(value)).hexdigest()


def _hex(value):
    if type(value) is not str or _SHA.fullmatch(value) is None:
        _fail()
    return value


def _text(value, maximum, *, nonblank=False):
    if type(value) is not str or len(value) > maximum:
        _fail()
    try:
        value.encode("utf-8")
    except UnicodeError:
        _fail()
    if nonblank and not value.strip():
        _fail()
    return value


def _authored(reference, critical_tokens):
    """V1 text/token syntax only; no occurrence check and no metric calls."""
    _text(reference, 20_000)
    if type(critical_tokens) is not list or len(critical_tokens) > 64:
        _fail()
    try:
        ocr_evaluation._text(reference, label="reference", maximum=20_000)
        normalized = set()
        for raw in critical_tokens:
            _text(raw, 256)
            token = ocr_evaluation._text(raw, label="critical entry", maximum=256)
            if not token or token in normalized:
                _fail()
            normalized.add(token)
    except (TypeError, UnicodeError, OverflowError, RecursionError):
        _fail()
    return {"reference": reference, "critical_tokens": list(critical_tokens)}


def _binding(anchor_report, report_sha256, region_id):
    # The existing v1 helper establishes scope, occurrence ID and full anchor
    # once per public call. Empty text/tokens avoid imposing occurrence rules
    # on uncertain authoring data; this helper performs validation, not scoring.
    bound = crops.build_crop_reference(anchor_report, anchor_report_sha256=report_sha256,
        anchor_region_id=region_id, reference="", critical_tokens=[], confirmed=True)
    return {key: bound[key] for key in ("reference_id", "scope", "anchor")}


def _projection(binding, state):
    return {"schema_version": 2, "kind": "ocr_crop_reference", **binding,
            "reference": state["reference"], "critical_tokens": state["critical_tokens"],
            "annotations": state["annotations"]}


class _Budget:
    def __init__(self):
        self.changes = 0
        self.reset_visits = 0
        self.overlap_checks = 0
        self.reading_scan = 0

    def reading(self, reference, reading):
        self.reading_scan += len(reference) + len(reading)
        if self.reading_scan > MAX_READING_SCAN:
            _fail()
        if reading not in reference:
            _fail()


def _bbox(value, scope):
    if type(value) is not list or len(value) != 4:
        _fail()
    result = []
    for item in value:
        if type(item) not in (int, float):
            _fail()
        try:
            item = float(item)
        except OverflowError:
            _fail()
        if not math.isfinite(item):
            _fail()
        result.append(0.0 if item == 0.0 else item)
    bounds = scope["bbox"]
    if (not bounds[0] <= result[0] < result[2] <= bounds[2]
            or not bounds[1] <= result[1] < result[3] <= bounds[3]
            or _bytes(result) != _bytes(value)):
        _fail()
    return result


def _annotation(value, state, scope, budget, reference_text_sha256):
    _fields(value, _ANNOTATION_KEYS)
    identifier = value["annotation_id"]
    if (type(identifier) is not str or _ANNOTATION_ID.fullmatch(identifier) is None
            or not 1 <= int(identifier[1:]) <= MAX_ANNOTATIONS
            or type(value["kind"]) is not str or value["kind"] not in ("uncertain", "illegible")
            or type(value["status"]) is not str or value["status"] not in ("unresolved", "resolved", "dismissed")):
        _fail()
    _bbox(value["bbox"], scope)
    if value["tentative_text"] is not None:
        _text(value["tentative_text"], 2_000)
    span = value["raw_span"]
    if span is not None:
        _fields(span, {"reference_text_sha256", "start", "end"})
        if (type(span["start"]) is not int or type(span["end"]) is not int
                or not 0 <= span["start"] <= span["end"] <= len(state["reference"])
                or _hex(span["reference_text_sha256"]) != reference_text_sha256):
            _fail()
    resolution = value["resolution"]
    if value["status"] != "resolved":
        if resolution is not None or value["status"] == "dismissed" and span is not None:
            _fail()
        return
    _fields(resolution, {"decision", "reading"})
    reading = _text(resolution["reading"], 20_000)
    if resolution["decision"] == "reading_confirmed" and type(resolution["decision"]) is str:
        if not reading:
            _fail()
        budget.reading(state["reference"], reading)
        if span is not None and state["reference"][span["start"]:span["end"]] != reading:
            _fail()
    elif resolution["decision"] == "not_text" and type(resolution["decision"]) is str:
        if reading != "" or span is not None:
            _fail()
    else:
        _fail()


def _state(state, scope, budget):
    _fields(state, _BASE_KEYS)
    _authored(state["reference"], state["critical_tokens"])
    rows = state["annotations"]
    if type(rows) is not list or len(rows) > MAX_ANNOTATIONS:
        _fail()
    # One raw-text hash per atomic state, not once per annotation/span.
    reference_text_sha256 = hashlib.sha256(state["reference"].encode("utf-8")).hexdigest()
    for number, row in enumerate(rows, 1):
        _annotation(row, state, scope, budget, reference_text_sha256)
        if row["annotation_id"] != f"a{number:06d}":
            _fail()
    active = [row["bbox"] for row in rows if row["status"] != "dismissed"]
    for index, a in enumerate(active):
        for b in active[index + 1:]:
            budget.overlap_checks += 1
            if budget.overlap_checks > MAX_OVERLAP_CHECKS:
                _fail()
            if max(a[0], b[0]) < min(a[2], b[2]) and max(a[1], b[1]) < min(a[3], b[3]):
                _fail()


def _views(payload, scope):
    if type(payload) is not list or len(payload) > MAX_VIEWS:
        _fail()
    result, order = {}, []
    for value in payload:
        _bytes(value, MAX_VIEW_BYTES)
        validated = validate_raster_view(value, scope=scope)
        if type(validated) is not dict or _bytes(validated, MAX_VIEW_BYTES) != _bytes(value, MAX_VIEW_BYTES):
            _fail()
        pin = _hex(validated["view_sha256"])
        if pin in result:
            _fail()
        result[pin] = validated
        order.append(pin)
    if order != sorted(order):
        _fail()
    return result


def _changes(state, reference, tokens, reset, changes, views, scope, budget, used_views):
    if type(reset) is not bool or type(changes) is not list or len(changes) > MAX_ANNOTATIONS:
        _fail()
    if reference is not None:
        _text(reference, 20_000)
        if reference == state["reference"]:
            _fail()  # Canonical unchanged replacement is null.
    if tokens is not None:
        _authored(state["reference"] if reference is None else reference, tokens)
        if tokens == state["critical_tokens"]:
            _fail()
    if (reference is not None or tokens is not None) and not reset:
        _fail()
    if reference is None and tokens is None and not reset and not changes:
        _fail()
    result = {"reference": state["reference"] if reference is None else reference,
              "critical_tokens": state["critical_tokens"] if tokens is None else list(tokens),
              "annotations": [dict(row) for row in state["annotations"]]}
    affected = set()
    if reset:
        for row in result["annotations"]:
            budget.reset_visits += 1
            if budget.reset_visits > MAX_RESET_VISITS:
                _fail()
            if row["raw_span"] is not None or row["status"] == "resolved":
                affected.add(row["annotation_id"])
            row["raw_span"] = None
            if row["status"] == "resolved":
                row["status"], row["resolution"] = "unresolved", None
    last_id = ""
    for change in changes:
        budget.changes += 1
        if budget.changes > MAX_CHANGES:
            _fail()
        _fields(change, _CHANGE_KEYS)
        _text(change["reason"], 512, nonblank=True)
        after = change["annotation"]
        _fields(after, _ANNOTATION_KEYS)
        identifier = after["annotation_id"]
        if (type(identifier) is not str or _ANNOTATION_ID.fullmatch(identifier) is None
                or identifier <= last_id):
            _fail()
        last_id = identifier
        number = int(identifier[1:])
        if not 1 <= number <= MAX_ANNOTATIONS:
            _fail()
        action = change["action"]
        if type(action) is not str:
            _fail()
        pin = _hex(change["view_sha256"])
        if pin not in views:
            _fail()
        used_views.add(pin)
        if action in ("add", "move"):
            _fields(change["selection"], {"kind", "pixel_bbox"})
            mapped = selection_to_scope_bbox(change["selection"], raster_view=views[pin], scope=scope)
            if _bytes(mapped) != _bytes(after["bbox"]):
                _fail()
        elif change["selection"] is not None:
            _fail()
        rows = result["annotations"]
        if action == "add":
            if (number != len(rows) + 1 or after["status"] != "unresolved"
                    or after["raw_span"] is not None or after["resolution"] is not None):
                _fail()
            rows.append(dict(after))
        else:
            if number > len(rows):
                _fail()
            before = rows[number - 1]
            expected = dict(before)
            status = before["status"]
            if action in ("move", "reclassify", "edit_tentative"):
                key = {"move": "bbox", "reclassify": "kind", "edit_tentative": "tentative_text"}[action]
                if status not in ("unresolved", "resolved") or _bytes(after[key]) == _bytes(before[key]):
                    _fail()
                expected.update({key: after[key], "status": "unresolved", "raw_span": None, "resolution": None})
            elif action == "anchor":
                if status not in ("unresolved", "resolved") or _bytes(after["raw_span"]) == _bytes(before["raw_span"]):
                    _fail()
                expected["raw_span"] = after["raw_span"]
            elif action == "resolve":
                if status != "unresolved":
                    _fail()
                expected.update(status="resolved", resolution=after["resolution"], raw_span=after["raw_span"])
            elif action == "reopen":
                if status not in ("resolved", "dismissed"):
                    _fail()
                expected.update(status="unresolved", raw_span=None, resolution=None)
            elif action == "dismiss":
                if status not in ("unresolved", "resolved"):
                    _fail()
                expected.update(status="dismissed", raw_span=None, resolution=None)
            else:
                _fail()
            if _bytes(expected) != _bytes(after):
                _fail()
            rows[number - 1] = dict(after)
        affected.add(identifier)
    _state(result, scope, budget)
    return result, sorted(affected)


def _finish(journal, binding, state, used_views):
    if set(used_views) != {view["view_sha256"] for view in journal["views"]}:
        _fail()
    declaration = _projection(binding, state)
    declaration_sha = _hash(declaration)
    # Budget precisely the future closed envelope, including the final hash.
    # This object is not returned or a declaration that an operator confirmed.
    prospective = {**declaration, "declaration_sha256": declaration_sha,
                   "journal": journal, "review": "operator_checked_requested_crop"}
    prospective["reference_sha256"] = _hash(prospective)
    _bytes(prospective)
    return {"journal": _copy(journal), "declaration": _copy(declaration),
            "declaration_sha256": declaration_sha}


def _replay(payload, anchor_report, report_sha256):
    _journal_shape(payload)
    journal = _copy(payload)
    _fields(journal, _JOURNAL_KEYS)
    _fields(journal["binding"], _BINDING_KEYS)
    supplied_binding = journal["binding"]
    anchor = supplied_binding["anchor"]
    _fields(anchor, {"report_sha256", "region_id", "record_sha256", "operation", "recipe_sha256"})
    binding = _binding(anchor_report, report_sha256, anchor["region_id"])
    if _bytes(supplied_binding) != _bytes(binding):
        _fail()
    return _replay_bound(journal, binding)


def _replay_bound(journal, binding):
    """Replay preflighted history after its caller's explicit binding check."""
    state = journal["base"]
    _fields(state, _BASE_KEYS)
    if type(state["annotations"]) is not list or state["annotations"]:
        _fail()
    budget = _Budget()
    _state(state, binding["scope"], budget)
    before_hash = _hash(_projection(binding, state))
    if _hex(journal["base_declaration_sha256"]) != before_hash:
        _fail()
    previous = _hash({"binding": binding, "base": state, "base_declaration_sha256": before_hash})
    views = _views(journal["views"], binding["scope"])
    revisions = journal["revisions"]
    if type(revisions) is not list or len(revisions) > MAX_REVISIONS:
        _fail()
    used_views = set()
    for sequence, revision in enumerate(revisions, 1):
        _fields(revision, _REVISION_KEYS)
        _text(revision["reason"], 512, nonblank=True)
        if (type(revision["sequence"]) is not int or revision["sequence"] != sequence
                or _hex(revision["previous_revision_sha256"]) != previous
                or _hex(revision["before_declaration_sha256"]) != before_hash):
            _fail()
        state, affected = _changes(state, revision["reference"], revision["critical_tokens"],
            revision["reset_anchors"], revision["annotation_changes"], views, binding["scope"], budget, used_views)
        before_hash = _hash(_projection(binding, state))
        if (_hex(revision["after_declaration_sha256"]) != before_hash
                or _bytes(revision["affected_annotation_ids"]) != _bytes(affected)):
            _fail()
        previous = _hash({key: value for key, value in revision.items() if key != "revision_sha256"})
        if _hex(revision["revision_sha256"]) != previous:
            _fail()
    if _hex(journal["head_sha256"]) != previous:
        _fail()
    return journal, binding, state, budget, used_views


def build_crop_journal(anchor_report, *, anchor_report_sha256, anchor_region_id,
                       reference, critical_tokens):
    """Start explicit authoring genesis; no annotations, metrics or consent."""
    _authored(reference, critical_tokens)
    authored = _copy({"reference": reference, "critical_tokens": critical_tokens})
    binding = _binding(anchor_report, anchor_report_sha256, anchor_region_id)
    state = {**authored, "annotations": []}
    declaration_sha = _hash(_projection(binding, state))
    journal = {"binding": binding, "base": state, "base_declaration_sha256": declaration_sha,
               "views": [], "revisions": []}
    journal["head_sha256"] = _hash({key: journal[key] for key in ("binding", "base", "base_declaration_sha256")})
    return _finish(journal, binding, state, set())["journal"]


def replay_crop_journal(journal, *, anchor_report, anchor_report_sha256):
    """Validate complete bounded history and detach its current declaration.

    Critical occurrence-in-reference and consent are deliberately not validated
    here; the later resolved-reference adapter must perform unchanged v1 checks.
    """
    checked, binding, state, _budget, used_views = _replay(journal, anchor_report, anchor_report_sha256)
    return _finish(checked, binding, state, used_views)


def replay_crop_journal_declaration(journal, *, scope, anchor_report_sha256,
                                   anchor_region_id):
    """Replay an unverified saved declaration without report or image access.

    Only the declared scope, occurrence identity, hash chain and authoring
    semantics are checked. Record/recipe digests and raster observations are
    declarations, NOT proof of a retained report, rendering or source review.
    Pack draft recovery supplies these expected pins from its fixed manifest.
    Strict pack validation and current review must still use the report-bound
    ``replay_crop_journal``; this seam never grants consent or score authority.
    The same complete-history budgets apply, with no suffix repair or trimming.
    """
    _hex(anchor_report_sha256)
    crops._id(anchor_region_id)
    _fields(scope, {"source_sha256", "page_count", "page_number", "coordinate_system",
                    "bbox", "page_geometry", "scope_sha256"})
    _bytes(scope)
    checked_scope = crops.validate_crop_scope(scope, source_sha256=scope["source_sha256"],
                                              page_count=scope["page_count"])
    _journal_shape(journal)
    binding = journal["binding"]
    _text(binding["reference_id"], 69)
    anchor = binding["anchor"]
    for key in ("report_sha256", "record_sha256", "recipe_sha256"):
        _hex(anchor[key])
    crops._id(anchor["region_id"])
    if (type(anchor["operation"]) is not str or anchor["operation"] not in ("regions", "hardscan")
            or anchor["report_sha256"] != anchor_report_sha256
            or anchor["region_id"] != anchor_region_id
            or _bytes(binding["scope"]) != _bytes(checked_scope)):
        _fail()
    if anchor["operation"] == "regions" and anchor["recipe_sha256"] != _hash(None):
        _fail()
    expected = {"scope": checked_scope, "anchor": anchor}
    expected["reference_id"] = "crop-" + _hash(expected)
    if _bytes(binding) != _bytes(expected):
        _fail()
    checked, binding, state, _budget, used_views = _replay_bound(_copy(journal), _copy(expected))
    return _finish(checked, binding, state, used_views)


def append_crop_journal(journal, *, anchor_report, anchor_report_sha256,
                        reference=None, critical_tokens=None, reset_anchors=False,
                        annotation_changes, reason, views=()):
    """Append one atomic authored revision; unchanged content returns unchanged.

    Caller changes are complete typed after-images, never injected behavior.
    New views are explicit; old registry entries/revisions/base are retained.
    Cross-pack parent continuity and session-observed dirty/ABA authority belong
    to the later fixed host integration, not this unauthenticated pure function.
    """
    return append_crop_journal_declaration(journal, anchor_report=anchor_report,
        anchor_report_sha256=anchor_report_sha256, reference=reference, critical_tokens=critical_tokens,
        reset_anchors=reset_anchors, annotation_changes=annotation_changes, reason=reason, views=views)["journal"]


def append_crop_journal_declaration(journal, *, anchor_report, anchor_report_sha256,
                                    reference=None, critical_tokens=None, reset_anchors=False,
                                    annotation_changes, reason, views=()):
    """Append and return the detached journal/head under one shared work budget.

    Hosts need the current annotation array without replaying the entire result
    a second time. This is the same authoring transaction as append_crop_journal,
    including its no-op semantics, report binding and lack of review authority.
    """
    if type(views) is tuple and views == ():
        views = []
    if type(views) is not list or len(views) > MAX_VIEWS or type(reset_anchors) is not bool:
        _fail()
    _changes_shape(annotation_changes)
    _text(reason, 512, nonblank=True)
    if reference is not None:
        _text(reference, 20_000)
    if critical_tokens is not None and (type(critical_tokens) is not list or len(critical_tokens) > 64):
        _fail()
    request = _copy({"reference": reference, "critical_tokens": critical_tokens,
                     "reset_anchors": reset_anchors, "annotation_changes": annotation_changes,
                     "reason": reason, "views": views})
    if (type(request["reset_anchors"]) is not bool or type(request["annotation_changes"]) is not list
            or len(request["annotation_changes"]) > MAX_ANNOTATIONS):
        _fail()
    _text(request["reason"], 512, nonblank=True)
    checked, binding, state, budget, used_views = _replay(journal, anchor_report, anchor_report_sha256)
    # Validate the input's complete prospective envelope before extending it.
    previous_result = _finish(checked, binding, state, used_views)
    supplied = _views(request["views"], binding["scope"])
    retained = {view["view_sha256"]: view for view in checked["views"]}
    if set(supplied).intersection(retained) or len(retained) + len(supplied) > MAX_VIEWS:
        _fail()
    retained.update(supplied)
    text, tokens = request["reference"], request["critical_tokens"]
    if text is not None:
        _text(text, 20_000)
        if text == state["reference"]:
            text = None
    if tokens is not None:
        _authored(state["reference"] if text is None else text, tokens)
        if tokens == state["critical_tokens"]:
            tokens = None
    if text is None and tokens is None and not request["reset_anchors"] and not request["annotation_changes"]:
        if supplied:
            _fail()
        return previous_result
    if len(checked["revisions"]) == MAX_REVISIONS:
        _fail()
    next_state, affected = _changes(state, text, tokens, request["reset_anchors"], request["annotation_changes"],
        retained, binding["scope"], budget, used_views)
    revision = {"sequence": len(checked["revisions"]) + 1,
                "previous_revision_sha256": checked["head_sha256"],
                "before_declaration_sha256": _hash(_projection(binding, state)),
                "after_declaration_sha256": _hash(_projection(binding, next_state)),
                "reference": text, "critical_tokens": tokens, "reset_anchors": request["reset_anchors"],
                "annotation_changes": request["annotation_changes"], "affected_annotation_ids": affected,
                "reason": request["reason"]}
    revision["revision_sha256"] = _hash(revision)
    result = {**checked, "views": [retained[pin] for pin in sorted(retained)],
              "revisions": [*checked["revisions"], revision], "head_sha256": revision["revision_sha256"]}
    return _finish(result, binding, next_state, used_views)
