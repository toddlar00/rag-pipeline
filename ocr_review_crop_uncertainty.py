"""Bounded shared uncertainty authoring helpers, with no UI authority.

Inputs containing journals or declarations come only from the trusted host's
validated results or private draft recovery, never from browser form fields.
These helpers do not replay reports/history, score, render, publish or manage
session/image/action tokens. The callback must admit its captured tokens before
parsing human commands, call the real author host, then recheck ownership before
installing its detached output. Hash consistency is not authenticated provenance.
"""

from __future__ import annotations

import hashlib
import json

import ocr_crop_raster_view as raster
import ocr_crop_uncertainty_journal as history
import ocr_evaluation


_FIELDS = {"journal", "annotations", "dirty"}
_COMMANDS = {
    "add": {"action", "selection", "kind", "tentative_text"},
    "move": {"action", "annotation_id", "selection"},
    "reclassify": {"action", "annotation_id", "kind"},
    "edit_tentative": {"action", "annotation_id", "tentative_text"},
    "anchor": {"action", "annotation_id", "span"},
    "resolve": {"action", "annotation_id", "decision", "reading", "span"},
    "reopen": {"action", "annotation_id"},
    "dismiss": {"action", "annotation_id"},
}


def _fail():
    raise ValueError("Uncertainty action is invalid or changed. Keep the draft, select the current annotation and apply a valid action again.")


def _copy(value):
    return json.loads(history._bytes(value))


def _hash(value):
    return hashlib.sha256(history._bytes(value)).hexdigest()


def _id(value):
    if (type(value) is not str or len(value) != 7 or value[0] != "a"
            or not value[1:].isascii() or not value[1:].isdigit()
            or not 1 <= int(value[1:]) <= history.MAX_ANNOTATIONS):
        _fail()
    return int(value[1:])


def _checked(value):
    history._fields(value, _FIELDS)
    if (type(value["dirty"]) is not bool or type(value["annotations"]) is not list
            or len(value["annotations"]) > history.MAX_ANNOTATIONS):
        _fail()
    if value["journal"] is None:
        if value["annotations"]:
            _fail()
    else:
        history._journal_shape(value["journal"])
    for index, row in enumerate(value["annotations"], 1):
        history._annotation_shape(row)
        if (_id(row["annotation_id"]) != index or type(row["kind"]) is not str
                or row["kind"] not in ("uncertain", "illegible") or type(row["status"]) is not str
                or row["status"] not in ("unresolved", "resolved", "dismissed")):
            _fail()
    return _copy(value)


def _reset(rows):
    result = []
    for original in rows:
        row = {**original, "raw_span": None}
        if row["status"] == "resolved":
            row.update(status="unresolved", resolution=None)
        result.append(row)
    return result


def _head(journal):
    # Bounded authored-field projection only, not an alternative history replay.
    # Complete syntax/transition/reading/overlap checks remain in the host.
    result = {key: journal["base"][key] for key in ("reference", "critical_tokens")}
    for revision in journal["revisions"]:
        for key in result:
            if revision[key] is not None:
                result[key] = revision[key]
    return result


def _clean(value):
    if value["dirty"] or value["journal"] is None:
        _fail()
    journal = value["journal"]
    authored = _head(journal)
    declaration = {"schema_version": 2, "kind": "ocr_crop_reference", **journal["binding"],
                   **authored, "annotations": value["annotations"]}
    expected = (journal["revisions"][-1]["after_declaration_sha256"] if journal["revisions"]
                else journal["base_declaration_sha256"])
    if _hash(declaration) != expected:
        _fail()
    return authored


def empty_uncertainty():
    """Fresh draft, not a genesis or a reference declaration."""
    return {"journal": None, "annotations": [], "dirty": False}


def restore_uncertainty(draft):
    """Detach already validated v1/v2 raw draft state without inventing history."""
    if type(draft) is not dict or len(draft) not in (2, 3):
        _fail()
    fields = {"reference", "critical_tokens_text"}
    history._fields(draft, fields if len(draft) == 2 else fields | {"uncertainty"})
    history._text(draft["reference"], 20_000)
    history._text(draft["critical_tokens_text"], 64 * 257)
    return empty_uncertainty() if len(draft) == 2 else _checked(draft["uncertainty"])


def dirty_uncertainty(value):
    """Record a genuine observed edit; A→B→A never clears this dirty latch."""
    result = _checked(value)
    result["annotations"] = _reset(result["annotations"])
    result["dirty"] = True
    return _checked(result)


def install_author_result(result, *, scope):
    """Install the exact host-authored head after callback ownership rechecks."""
    if type(result) is not dict or len(result) != 6:
        _fail()
    prefix = "pair_sha256" if "pair_sha256" in result else "pack_sha256"
    history._fields(result, {prefix, "journal", "declaration", "declaration_sha256",
                            "requires_attention", "canonical_extraction_modified"})
    history._hex(result[prefix])
    history._hex(result["declaration_sha256"])
    if result["requires_attention"] is not True or result["canonical_extraction_modified"] is not False:
        _fail()
    scope = raster._scope(scope)
    declaration = result["declaration"]
    history._fields(declaration, {"schema_version", "kind", "reference_id", "scope", "anchor",
                                  "reference", "critical_tokens", "annotations"})
    if (type(declaration["schema_version"]) is not int or declaration["schema_version"] != 2
            or type(declaration["kind"]) is not str or declaration["kind"] != "ocr_crop_reference"
            or declaration["scope"] != scope):
        _fail()
    history._authored(declaration["reference"], declaration["critical_tokens"])
    value = _checked({"journal": result["journal"], "annotations": declaration["annotations"], "dirty": False})
    authored = _clean(value)
    expected = {"schema_version": 2, "kind": "ocr_crop_reference", **value["journal"]["binding"],
                **authored, "annotations": value["annotations"]}
    if _hash(declaration) != result["declaration_sha256"] or history._bytes(declaration) != history._bytes(expected):
        _fail()
    return value


def uncertainty_digest(value):
    """Digest bounded authoring state, never a session or approval token."""
    return _hash(_checked(value))


def _span(value, reference):
    if value is None:
        return None
    if (type(value) is not list or len(value) != 2 or any(type(item) is not int for item in value)
            or not 0 <= value[0] <= value[1] <= len(reference)):
        _fail()
    return {"reference_text_sha256": hashlib.sha256(reference.encode("utf-8")).hexdigest(),
            "start": value[0], "end": value[1]}


def _after(command, rows, *, reference, scope, view):
    action = command["action"]
    selection = None
    if action == "add":
        number = len(rows) + 1
        if number > history.MAX_ANNOTATIONS:
            _fail()
        row = {"annotation_id": f"a{number:06d}", "bbox": None, "kind": command["kind"],
               "status": "unresolved", "tentative_text": command["tentative_text"], "raw_span": None, "resolution": None}
    else:
        number = _id(command["annotation_id"])
        if number > len(rows):
            _fail()
        row = dict(rows[number - 1])
    if action in ("add", "move"):
        selection = command["selection"]
        row["bbox"] = raster.selection_to_scope_bbox(selection, raster_view=view, scope=scope)
    if action in ("move", "reclassify", "edit_tentative"):
        row.update(status="unresolved", raw_span=None, resolution=None)
    if action == "reclassify":
        row["kind"] = command["kind"]
    if action == "edit_tentative":
        row["tentative_text"] = command["tentative_text"]
    if action == "anchor":
        row["raw_span"] = _span(command["span"], reference)
    if action == "resolve":
        row.update(status="resolved", resolution={"decision": command["decision"], "reading": command["reading"]},
                   raw_span=_span(command["span"], reference))
    if action in ("reopen", "dismiss"):
        row.update(status="unresolved" if action == "reopen" else "dismissed", raw_span=None, resolution=None)
    if row["kind"] not in ("uncertain", "illegible") or type(row["kind"]) is not str:
        _fail()
    history._annotation_shape(row)
    if row["tentative_text"] is not None:
        history._text(row["tentative_text"], 2_000)
    if action == "resolve":
        if (type(command["decision"]) is not str or command["decision"] not in ("reading_confirmed", "not_text")
                or type(command["reading"]) is not str or len(command["reading"]) > 20_000
                or command["decision"] == "not_text" and (command["reading"] != "" or row["raw_span"] is not None)):
            _fail()
    return number, row, selection


def author_arguments(value, *, reference, critical_tokens, scope, raster_view, commands, reason):
    """Build exact host author kwargs from closed human commands, not afterimages.

    Add assigns lifetime IDs. Commands can form one <=128-action atomic batch;
    offsets are Python code points, never implicit browser UTF-16 offsets. The
    real host is the final transition/no-op/reading/overlap/budget authority.
    """
    value = _checked(value)
    authored = history._authored(reference, critical_tokens)
    history._text(reason, 512, nonblank=True)
    scope = raster._scope(scope)
    view = raster.validate_raster_view(raster_view, scope=scope)
    if type(commands) is not list or len(commands) > history.MAX_ANNOTATIONS:
        _fail()
    for command in commands:
        if type(command) is not dict or type(command.get("action")) is not str or command["action"] not in _COMMANDS:
            _fail()
        history._fields(command, _COMMANDS[command["action"]])
    commands = _copy(commands)
    journal = value["journal"]
    if journal is not None and journal["binding"]["scope"] != scope:
        _fail()
    if journal is not None and not value["dirty"]:
        _clean(value)
    reset = value["dirty"] or journal is not None and _head(journal) != authored
    rows = _reset(value["annotations"]) if reset else value["annotations"]
    changes, touched = [], set()
    for command in commands:
        number, row, selection = _after(command, rows, reference=reference, scope=scope, view=view)
        if number in touched:
            _fail()
        touched.add(number)
        # New additions count immediately; existing after-images deliberately do
        # not execute here. One action per ID makes their order independent.
        if command["action"] == "add":
            rows.append(row)
        changes.append({"action": command["action"], "annotation": row,
                        "selection": selection, "view_sha256": view["view_sha256"], "reason": reason})
    changes.sort(key=lambda change: change["annotation"]["annotation_id"])
    known = set() if journal is None else {item["view_sha256"] for item in journal["views"]}
    request = {"journal": journal, **authored, "reset_anchors": bool(reset), "annotation_changes": changes,
               "reason": reason, "views": [view] if changes and view["view_sha256"] not in known else []}
    return _copy(request)


def prepare_policy(value, *, reference, critical_tokens):
    """Predict policy from installed author state without evaluating a candidate."""
    value = _checked(value)
    authored = history._authored(reference, critical_tokens)
    if _clean(value) != authored:
        _fail()
    counts = {key: sum(row["status"] == key for row in value["annotations"])
              for key in ("unresolved", "resolved", "dismissed")}
    if not counts["unresolved"]:
        # Exact unchanged validation envelope used by crops._reference. This is
        # neither a fake report nor a score of an invented empty prediction.
        ocr_evaluation._validate({"schema_version": 1, "records": [{"id": "reference-validation",
            "reference": reference, "prediction": "", "critical_tokens": critical_tokens}]})
    return {"scorable": not bool(counts["unresolved"]),
            "reason": "reference_uncertain" if counts["unresolved"] else None,
            "annotation_total": len(value["annotations"]), **counts}


def overlay_spec(value, *, scope, raster_view):
    """Project numeric geometry only; never mutate stored boxes or render text."""
    value = _checked(value)
    scope = raster._scope(scope)
    view = raster.validate_raster_view(raster_view, scope=scope)
    if value["journal"] is not None and value["journal"]["binding"]["scope"] != scope:
        _fail()
    states = {"unresolved": 0, "resolved": 1, "dismissed": 2}
    rectangles = [{"ordinal": _id(row["annotation_id"]),
        "pixel_bbox": raster.scope_bbox_to_raster_bbox(row["bbox"], raster_view=view, scope=scope),
        "state": states[row["status"]]} for row in value["annotations"]]
    return {"width": view["width"], "height": view["height"],
            "requested_bbox": raster.scope_bbox_to_raster_bbox(scope["bbox"], raster_view=view, scope=scope),
            "rectangles": rectangles}
