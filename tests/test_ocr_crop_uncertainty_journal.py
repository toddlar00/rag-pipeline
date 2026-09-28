"""Pure authoring journal controls, not UI acceptance.

Imports target the production modules; fixtures remain generated declarations.
Reports are generated scalar-only fixtures. Raster metadata is an independently
declared exact-scale 72-point square passed through the REAL pure builder. It
does not prove a PDF was rendered or a person saw/approved an image. No native
renderer, OCR, model, browser, storage or consent flow is exercised here.

Lowered-budget controls isolate cumulative counters; they are not maximum-size
performance evidence. Parent-pack prefix continuity, raw duplicate-key decoding,
dirty UI state and final v2 scoring/coverage remain separate integration work.
"""

import copy
import hashlib
import json
from types import SimpleNamespace

import pytest

import ocr_comparison
import ocr_crop_comparison as crops
import ocr_crop_raster_view as raster
import ocr_crop_uncertainty_journal as journal
import ocr_evaluation
from test_ocr_crop_comparison import _report


def _raw(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


def _sha(value):
    return hashlib.sha256(_raw(value)).hexdigest()


def _text_sha(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@pytest.fixture(autouse=True)
def scorer_guard(monkeypatch):
    originals = (crops.compare_ocr, ocr_comparison.compare_ocr, ocr_evaluation.evaluate_ocr)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("pure journal called an OCR scorer")

    monkeypatch.setattr(crops, "compare_ocr", forbidden)
    monkeypatch.setattr(ocr_comparison, "compare_ocr", forbidden)
    monkeypatch.setattr(ocr_evaluation, "evaluate_ocr", forbidden)
    return originals


@pytest.fixture
def case():
    report = _report(bbox=[0., 0., 1., 1.])
    result = SimpleNamespace(report=report, report_sha=_sha(report),
                             scope=crops.crop_scope(report, region_id="a"))
    result.view = _view(result)
    return result


def _view(case, profile="fit", serial=0):
    # Independent declaration, not the implementation's _expected_geometry.
    scale = {"fit": 2., "dpi288": 4., "dpi576": 8.}[profile]
    side = int(72 * scale)
    return raster.build_raster_view(scope=case.scope, preview_profile=profile,
        command_clip=[0., 0., 72., 72.], native_clip=[0., 0., 72., 72.],
        command_matrix=[scale, 0., 0., scale, 0., 0.],
        native_matrix=[scale, 0., 0., scale, 0., 0.],
        projected_rect=[0., 0., float(side), float(side)],
        pixel_rect=[0, 0, side, side], width=side, height=side,
        rgb_sha256=_text_sha(f"synthetic raster declaration {serial}"))


def _new(case, reference="", tokens=None):
    return journal.build_crop_journal(case.report, anchor_report_sha256=case.report_sha,
        anchor_region_id="a", reference=reference, critical_tokens=[] if tokens is None else tokens)


def _replay(case, value):
    return journal.replay_crop_journal(value, anchor_report=case.report,
                                      anchor_report_sha256=case.report_sha)


def _row(case, value, number=1):
    return _replay(case, value)["declaration"]["annotations"][number - 1]


def _selection(pixels):
    return {"kind": "raster_edges", "pixel_bbox": list(pixels)}


def _add(case, number=1, pixels=(0, 0, 36, 36), *, view=None, kind="uncertain", tentative=None):
    view = case.view if view is None else view
    selection = _selection(pixels)
    row = {"annotation_id": f"a{number:06d}", "bbox": raster.selection_to_scope_bbox(
        selection, raster_view=view, scope=case.scope), "kind": kind, "status": "unresolved",
        "tentative_text": tentative, "raw_span": None, "resolution": None}
    return _change("add", row, view, selection)


def _change(action, row, view, selection=None):
    return {"action": action, "annotation": copy.deepcopy(row), "selection": selection,
            "view_sha256": view["view_sha256"], "reason": "authored synthetic action"}


def _append(case, value, *changes, reference=None, tokens=None, reset=False, views=None):
    if views is None:
        known = {view["view_sha256"] for view in value["views"]}
        requested = {change["view_sha256"] for change in changes}
        views = [case.view] if case.view["view_sha256"] in requested - known else []
    return journal.append_crop_journal(value, anchor_report=case.report,
        anchor_report_sha256=case.report_sha, reference=reference, critical_tokens=tokens,
        reset_anchors=reset, annotation_changes=list(changes), reason="authored synthetic revision",
        views=views)


def _transition(case, value, action, *, number=1, view=None, **fields):
    view = case.view if view is None else view
    row = _row(case, value, number)
    row.update(fields)
    return _append(case, value, _change(action, row, view))


def _resolved(case, reference="foo"):
    value = _append(case, _new(case, reference), _add(case))
    return _transition(case, value, "resolve", status="resolved",
                       resolution={"decision": "reading_confirmed", "reading": reference})


def _projection(binding, state):
    return {"schema_version": 2, "kind": "ocr_crop_reference", **binding, **state}


def _rehash(value):
    """Forge internally consistent hashes WITHOUT enforcing semantic transitions.

    Tests use this independent after-image interpreter so an invalid change is
    not rejected merely because its checksum was forgotten. Sequence numbers,
    action order and affected IDs are deliberately NOT repaired here.
    """
    value = copy.deepcopy(value)
    state = copy.deepcopy(value["base"])
    value["base_declaration_sha256"] = _sha(_projection(value["binding"], state))
    previous = _sha({key: value[key] for key in ("binding", "base", "base_declaration_sha256")})
    for revision in value["revisions"]:
        revision["previous_revision_sha256"] = previous
        revision["before_declaration_sha256"] = _sha(_projection(value["binding"], state))
        for key in ("reference", "critical_tokens"):
            if revision[key] is not None:
                state[key] = copy.deepcopy(revision[key])
        if revision["reset_anchors"]:
            for row in state["annotations"]:
                row["raw_span"] = None
                if row["status"] == "resolved":
                    row.update(status="unresolved", resolution=None)
        for change in revision["annotation_changes"]:
            after = copy.deepcopy(change["annotation"])
            number = int(after["annotation_id"][1:])
            if change["action"] == "add":
                state["annotations"].append(after)
            else:
                state["annotations"][number - 1] = after
        revision["after_declaration_sha256"] = _sha(_projection(value["binding"], state))
        revision["revision_sha256"] = _sha({key: item for key, item in revision.items()
                                             if key != "revision_sha256"})
        previous = revision["revision_sha256"]
    value["head_sha256"] = previous
    return value


def test_genesis_preserves_empty_text_and_absent_critical_without_consent(case):
    value = _new(case, "", ["OCR"])
    result = _replay(case, value)
    assert value["base"] == {"reference": "", "critical_tokens": ["OCR"], "annotations": []}
    assert value["views"] == value["revisions"] == []
    assert set(result) == {"journal", "declaration", "declaration_sha256"}
    assert set(result["declaration"]) == {"schema_version", "kind", "reference_id", "scope",
        "anchor", "reference", "critical_tokens", "annotations"}
    assert result["declaration"]["schema_version"] == 2
    assert result["declaration_sha256"] == value["base_declaration_sha256"]
    assert value["head_sha256"] == _sha({key: value[key] for key in
        ("binding", "base", "base_declaration_sha256")})
    v1 = crops.build_crop_reference(case.report, anchor_report_sha256=case.report_sha,
        anchor_region_id="a", reference="", critical_tokens=[], confirmed=True)
    assert value["binding"] == {key: v1[key] for key in ("reference_id", "scope", "anchor")}
    with pytest.raises(ValueError):
        crops.validate_crop_reference(result["declaration"], anchor_report=case.report,
                                      anchor_report_sha256=case.report_sha)


@pytest.mark.parametrize("profile,side", [("fit", 144), ("dpi288", 288), ("dpi576", 576)])
def test_actual_pure_geometry_builder_binds_independently_declared_scales(case, profile, side):
    view = _view(case, profile)
    change = _add(case, pixels=(0, 0, side // 4, side // 4), view=view)
    value = _append(case, _new(case), change, views=[view])
    assert value["views"] == [view]
    assert view["width"] == view["height"] == side
    assert _row(case, value)["bbox"] == [0., 0., .25, .25]


def test_add_resolve_dismiss_reopen_retains_lifetime_row(case):
    value = _append(case, _new(case, "foo"), _add(case, kind="illegible", tentative="f?o"))
    original = _row(case, value)
    value = _transition(case, value, "resolve", status="resolved",
        resolution={"decision": "reading_confirmed", "reading": "foo"})
    value = _transition(case, value, "dismiss", status="dismissed", resolution=None)
    assert _row(case, value)["status"] == "dismissed"
    value = _transition(case, value, "reopen", status="unresolved", resolution=None)
    assert _row(case, value) == original
    assert len(value["revisions"]) == 4
    assert [item["affected_annotation_ids"] for item in value["revisions"]] == [["a000001"]] * 4


def test_absent_critical_entry_is_valid_in_unresolved_authoring_history(case):
    value = _append(case, _new(case, "", ["OCR"]), _add(case, kind="illegible"))
    declaration = _replay(case, value)["declaration"]
    assert declaration["reference"] == "" and declaration["critical_tokens"] == ["OCR"]
    assert declaration["annotations"][0]["status"] == "unresolved"
    # This is authoring syntax, not final reviewed-reference eligibility.
    with pytest.raises(ValueError):
        crops.build_crop_reference(case.report, anchor_report_sha256=case.report_sha,
            anchor_region_id="a", reference="", critical_tokens=["OCR"], confirmed=True)


def test_not_text_resolution_and_unresolved_zero_length_anchor(case):
    value = _append(case, _new(case), _add(case))
    span = {"reference_text_sha256": _text_sha(""), "start": 0, "end": 0}
    value = _transition(case, value, "anchor", raw_span=span)
    assert _row(case, value)["raw_span"] == span
    value = _transition(case, value, "resolve", status="resolved", raw_span=None,
                        resolution={"decision": "not_text", "reading": ""})
    assert _row(case, value)["resolution"] == {"decision": "not_text", "reading": ""}
    assert _replay(case, value)["declaration"]["reference"] == ""


def test_atomic_text_reset_then_resolution_validates_only_completed_state(case):
    value = _resolved(case)
    row = _row(case, value)
    row.update(raw_span={"reference_text_sha256": _text_sha("bar"), "start": 0, "end": 3},
               resolution={"decision": "reading_confirmed", "reading": "bar"})
    updated = _append(case, value, _change("resolve", row, case.view), reference="bar", reset=True)
    assert _row(case, updated) == row
    assert updated["revisions"][-1]["affected_annotation_ids"] == ["a000001"]
    assert _replay(case, updated)["declaration"]["reference"] == "bar"
    assert _replay(case, value)["declaration"]["reference"] == "foo"


def test_reset_does_not_reactivate_dismissed_row_and_tracks_only_changed_rows(case):
    value = _resolved(case)
    value = _append(case, value, _add(case, 2, (50, 0, 70, 20)))
    value = _transition(case, value, "dismiss", number=2, status="dismissed")
    updated = _append(case, value, reference="bar", reset=True)
    rows = _replay(case, updated)["declaration"]["annotations"]
    assert [row["status"] for row in rows] == ["unresolved", "dismissed"]
    assert all(row["raw_span"] is None and row["resolution"] is None for row in rows)
    assert updated["revisions"][-1]["affected_annotation_ids"] == ["a000001"]


def test_noop_and_explicit_observed_aba_have_distinct_history(case):
    value = _new(case, "foo", ["foo"])
    same = _append(case, value, reference="foo", tokens=["foo"])
    assert same == value and same is not value
    dirty = _append(case, value, reference="foo", tokens=["foo"], reset=True)
    revision = dirty["revisions"][0]
    assert revision["reference"] is revision["critical_tokens"] is None
    assert revision["before_declaration_sha256"] == revision["after_declaration_sha256"]
    assert revision["affected_annotation_ids"] == []
    assert dirty["head_sha256"] != value["head_sha256"]
    assert _append(case, dirty) == dirty


@pytest.mark.parametrize("replacement", [{"reference": "bar"}, {"tokens": ["new"]}])
def test_actual_authored_change_requires_explicit_reset(case, replacement):
    value = _new(case, "foo")
    with pytest.raises(ValueError):
        _append(case, value, **replacement)
    changed = _append(case, value, reset=True, **replacement)
    assert len(changed["revisions"]) == 1


def test_ordered_critical_change_resets_and_preserves_prior_history_exactly(case):
    value = _append(case, _new(case, "foo bar", ["foo", "bar"]), _add(case))
    original = copy.deepcopy(value)
    updated = _append(case, value, tokens=["bar", "foo"], reset=True)
    assert updated["base"] == original["base"]
    assert updated["base_declaration_sha256"] == original["base_declaration_sha256"]
    assert updated["revisions"][:-1] == original["revisions"]
    assert updated["views"] == original["views"]
    assert _replay(case, updated)["declaration"]["critical_tokens"] == ["bar", "foo"]
    assert value == original


def test_immutable_input_and_detached_results_on_success_and_refusal(case):
    value = _new(case, "foo")
    change = _add(case, tentative="f?o")
    before = copy.deepcopy((case.report, case.view, value, change))
    updated = _append(case, value, change)
    assert (case.report, case.view, value, change) == before
    snapshot = copy.deepcopy(updated)
    projected = _replay(case, updated)
    projected["declaration"]["annotations"][0]["tentative_text"] = "detached"
    projected["journal"]["views"][0]["rgb_sha256"] = "f" * 64
    assert updated == snapshot
    with pytest.raises(ValueError):
        _append(case, updated, change)
    assert updated == snapshot and (case.report, case.view, value, change) == before


@pytest.mark.parametrize("path", [("base_declaration_sha256",), ("head_sha256",),
    ("revisions", 1, "previous_revision_sha256"), ("revisions", 1, "before_declaration_sha256"),
    ("revisions", 1, "after_declaration_sha256"), ("revisions", 1, "revision_sha256")])
def test_every_digest_join_is_checked(case, path):
    value = _resolved(case)
    assert _rehash(value) == value
    target = value
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = "0" * 64
    with pytest.raises(ValueError):
        _replay(case, value)


@pytest.mark.parametrize("attack", ["sequence", "boolean_sequence", "order", "truncate", "affected", "noop"])
def test_full_chain_order_affected_set_and_nonempty_revision(case, attack):
    value = _resolved(case)
    if attack == "sequence":
        value["revisions"][1]["sequence"] = 1
        value = _rehash(value)
    elif attack == "boolean_sequence":
        value["revisions"][0]["sequence"] = True
        value = _rehash(value)
    elif attack == "order":
        value["revisions"].reverse()
    elif attack == "truncate":
        value["revisions"].pop()  # Retained head is not silently rewritten.
    elif attack == "affected":
        value["revisions"][1]["affected_annotation_ids"] = []
        value = _rehash(value)
    else:
        value["revisions"][1]["annotation_changes"] = []
        value["revisions"][1]["affected_annotation_ids"] = []
        value = _rehash(value)
    with pytest.raises(ValueError):
        _replay(case, value)


@pytest.mark.parametrize("field,new", [("reference_id", "other"),
    ("anchor", {"report_sha256": "b" * 64, "region_id": "a", "record_sha256": "c" * 64,
                "operation": "regions", "recipe_sha256": None})])
def test_binding_tamper_rejects_even_with_recomputed_entire_chain(case, field, new):
    value = _resolved(case)
    value["binding"][field] = new
    with pytest.raises(ValueError):
        _replay(case, _rehash(value))


@pytest.mark.parametrize("other", ["source", "occurrence", "report", "scope", "hash"])
def test_replay_requires_exact_external_report_and_occurrence_binding(case, other):
    value = _resolved(case)
    changes = {"source": {"source": "b" * 64}, "occurrence": {"region_id": "b"},
               "report": {"text": "different candidate"}, "scope": {"bbox": [0., 0., .5, 1.]},
               "hash": {}}
    report = _report(**({"bbox": [0., 0., 1., 1.]} | changes[other]))
    digest = "b" * 64 if other == "hash" else _sha(report)
    with pytest.raises(ValueError):
        journal.replay_crop_journal(value, anchor_report=report, anchor_report_sha256=digest)


@pytest.mark.parametrize("status,action", [("resolved", "resolve"), ("unresolved", "reopen"),
    *[("dismissed", action) for action in ("move", "reclassify", "edit_tentative", "anchor", "resolve", "dismiss")]])
def test_forbidden_source_statuses_refuse_without_mutation(case, status, action):
    value = _append(case, _new(case, "foo"), _add(case))
    if status == "resolved":
        value = _resolved(case)
    elif status == "dismissed":
        value = _transition(case, value, "dismiss", status="dismissed")
    saved = copy.deepcopy(value)
    row = _row(case, value)
    if action == "move":
        selection = _selection((40, 0, 60, 20))
        row["bbox"] = raster.selection_to_scope_bbox(selection, raster_view=case.view, scope=case.scope)
    else:
        selection = None
    if action in ("move", "reclassify", "edit_tentative", "reopen"):
        row.update(status="unresolved", raw_span=None, resolution=None)
    if action == "reclassify":
        row["kind"] = "illegible"
    if action == "edit_tentative":
        row["tentative_text"] = "changed"
    if action == "anchor":
        row["raw_span"] = {"reference_text_sha256": _text_sha("foo"), "start": 0, "end": 3}
    if action == "resolve":
        row.update(status="resolved", resolution={"decision": "reading_confirmed", "reading": "foo"})
    with pytest.raises(ValueError):
        _append(case, value, _change(action, row, case.view, selection))
    assert value == saved


@pytest.mark.parametrize("action", ["move", "reclassify", "edit_tentative"])
def test_allowed_resolved_edit_explicitly_clears_resolution_and_anchor(case, action):
    value = _resolved(case)
    value = _transition(case, value, "anchor",
        raw_span={"reference_text_sha256": _text_sha("foo"), "start": 0, "end": 3})
    row = _row(case, value)
    row.update(status="unresolved", raw_span=None, resolution=None)
    selection = None
    if action == "move":
        selection = _selection((40, 0, 60, 20))
        row["bbox"] = raster.selection_to_scope_bbox(selection, raster_view=case.view, scope=case.scope)
    elif action == "reclassify":
        row["kind"] = "illegible"
    else:
        row["tentative_text"] = "new tentative"
    updated = _append(case, value, _change(action, row, case.view, selection))
    assert _row(case, updated) == row


@pytest.mark.parametrize("field,new", [("kind", "illegible"), ("tentative_text", "smuggled"),
    ("bbox", [0., 0., .2, .2])])
def test_resolve_rejects_forbidden_after_image_changes_even_with_valid_hashes(case, field, new):
    value = _resolved(case)
    value["revisions"][-1]["annotation_changes"][0]["annotation"][field] = new
    with pytest.raises(ValueError):
        _replay(case, _rehash(value))


@pytest.mark.parametrize("action", ["move", "reclassify", "edit_tentative", "anchor"])
def test_unchanged_action_is_not_a_revision(case, action):
    value = _append(case, _new(case), _add(case))
    selection = _selection((0, 0, 36, 36)) if action == "move" else None
    with pytest.raises(ValueError):
        _append(case, value, _change(action, _row(case, value), case.view, selection))


def test_overlap_edge_contact_atomic_swap_and_dismissed_reactivation(case):
    value = _append(case, _new(case), _add(case))
    with pytest.raises(ValueError):
        _append(case, value, _add(case, 2, (20, 0, 50, 36)))
    value = _append(case, value, _add(case, 2, (36, 0, 72, 36)))
    rows = [_row(case, value, number) for number in (1, 2)]
    selections = [_selection((36, 0, 72, 36)), _selection((0, 0, 36, 36))]
    rows[0]["bbox"], rows[1]["bbox"] = rows[1]["bbox"], rows[0]["bbox"]
    value = _append(case, value, *[_change("move", row, case.view, selection)
                                 for row, selection in zip(rows, selections)])
    assert _replay(case, value)["declaration"]["annotations"] == rows
    value = _transition(case, value, "dismiss", status="dismissed")
    value = _append(case, value, _add(case, 3, (36, 0, 72, 36)))
    with pytest.raises(ValueError):
        _transition(case, value, "reopen", status="unresolved")
    assert [row["annotation_id"] for row in _replay(case, value)["declaration"]["annotations"]] == [
        "a000001", "a000002", "a000003"]


@pytest.mark.parametrize("attack", ["skip_id", "reuse_tombstone", "duplicate", "reverse", "remove_action"])
def test_lifetime_id_and_order_attacks(case, attack):
    value = _append(case, _new(case), _add(case))
    if attack == "skip_id":
        changes = [_add(case, 3, (40, 0, 60, 20))]
    elif attack == "reuse_tombstone":
        value = _transition(case, value, "dismiss", status="dismissed")
        changes = [_add(case)]
    elif attack == "remove_action":
        changes = [_change("remove", _row(case, value), case.view)]
    else:
        changes = [_add(case, 2, (40, 0, 60, 20)), _add(case, 3, (80, 0, 100, 20))]
        changes = list(reversed(changes)) if attack == "reverse" else [changes[0], changes[0]]
    with pytest.raises(ValueError):
        _append(case, value, *changes)


def test_raw_span_uses_python_codepoints_not_utf16_or_normalized_text(case):
    text = "A😀e\u0301 Z"
    value = _append(case, _new(case, text), _add(case))
    span = {"reference_text_sha256": _text_sha(text), "start": 1, "end": 2}
    value = _transition(case, value, "resolve", status="resolved", raw_span=span,
                         resolution={"decision": "reading_confirmed", "reading": "😀"})
    assert _row(case, value)["raw_span"] == span
    assert _replay(case, value)["declaration"]["reference"] == text


def test_resolved_anchor_removal_keeps_explicit_resolution(case):
    value = _resolved(case)
    value = _transition(case, value, "anchor",
        raw_span={"reference_text_sha256": _text_sha("foo"), "start": 0, "end": 3})
    value = _transition(case, value, "anchor", raw_span=None)
    row = _row(case, value)
    assert row["status"] == "resolved" and row["raw_span"] is None
    assert row["resolution"] == {"decision": "reading_confirmed", "reading": "foo"}


@pytest.mark.parametrize("span", [
    {"reference_text_sha256": "0" * 64, "start": 0, "end": 3},
    {"reference_text_sha256": _text_sha("foo"), "start": True, "end": 3},
    {"reference_text_sha256": _text_sha("foo"), "start": -1, "end": 3},
    {"reference_text_sha256": _text_sha("foo"), "start": 0, "end": 4},
    {"reference_text_sha256": _text_sha("foo"), "start": 3, "end": 2},
    {"reference_text_sha256": _text_sha("foo"), "start": 0, "end": 2},
])
def test_resolved_span_rejects_stale_or_wrong_exact_slice(case, span):
    value = _resolved(case)
    with pytest.raises(ValueError):
        _transition(case, value, "anchor", raw_span=span)


@pytest.mark.parametrize("resolution", [{"decision": "reading_confirmed", "reading": ""},
    {"decision": "reading_confirmed", "reading": "missing"},
    {"decision": "not_text", "reading": "foo"}, {"decision": "guess", "reading": "foo"},
    {"decision": True, "reading": "foo"}])
def test_resolution_is_explicit_and_never_inserts_its_reading(case, resolution):
    value = _append(case, _new(case, "foo"), _add(case))
    with pytest.raises(ValueError):
        _transition(case, value, "resolve", status="resolved", resolution=resolution)
    assert _replay(case, value)["declaration"]["reference"] == "foo"


@pytest.mark.parametrize("attack", ["missing", "duplicate", "unused", "unsorted", "altered", "wrong_selection"])
def test_view_registry_and_selection_join(case, attack):
    value = _append(case, _new(case), _add(case))
    if attack == "wrong_selection":
        value["revisions"][0]["annotation_changes"][0]["selection"]["pixel_bbox"] = [0, 0, 20, 20]
        value = _rehash(value)
    elif attack == "missing":
        value["views"] = []
    elif attack == "duplicate":
        value["views"].append(copy.deepcopy(case.view))
    elif attack in ("unused", "unsorted"):
        extra = _view(case, serial=1)
        if attack == "unsorted":
            row = _row(case, value)
            row["tentative_text"] = "new"
            value = _append(case, value, _change("edit_tentative", row, extra), views=[extra])
            assert len(value["views"]) == 2
            value["views"].reverse()
        else:
            value["views"].append(extra)
            value["views"].sort(key=lambda item: item["view_sha256"])
    else:
        value["views"][0]["rgb_sha256"] = "f" * 64
    with pytest.raises(ValueError):
        _replay(case, value)


def test_append_never_replaces_retained_views_or_accepts_unused_views(case):
    value = _append(case, _new(case), _add(case))
    with pytest.raises(ValueError):
        _append(case, value, reset=True, views=[case.view])
    with pytest.raises(ValueError):
        _append(case, value, views=[_view(case, serial=1)])
    assert value["views"] == [case.view]


@pytest.mark.parametrize("path", [(), ("binding",), ("binding", "anchor"), ("base",),
    ("revisions", 0), ("revisions", 0, "annotation_changes", 0),
    ("revisions", 0, "annotation_changes", 0, "annotation")])
def test_closed_schema_rejects_unknown_fields(case, path):
    value = _append(case, _new(case), _add(case))
    target = value
    for part in path:
        target = target[part]
    target["unexpected"] = None
    with pytest.raises(ValueError):
        _replay(case, value)


@pytest.mark.parametrize("raw", [None, True, "raw JSON is not this object API", [], {}, ()])
def test_public_replay_rejects_wrong_root_type_or_cardinality(case, raw):
    with pytest.raises(ValueError):
        _replay(case, raw)


def test_exact_integer_and_float_types_not_bool_or_noncanonical_coordinates(case):
    change = _add(case)
    for wrong in ([0, 0., .25, .25], [-0., 0., .25, .25], [False, 0., .25, .25]):
        altered = copy.deepcopy(change)
        altered["annotation"]["bbox"] = wrong
        with pytest.raises(ValueError):
            _append(case, _new(case), altered)
    with pytest.raises(ValueError):
        _append(case, _new(case), reset=1)


def test_actual_64_revision_limit_and_noop_at_full_history(case):
    value = _new(case)
    for _ in range(64):
        value = _append(case, value, reset=True)
    assert len(value["revisions"]) == 64
    assert _replay(case, value)["journal"] == value
    assert _append(case, value) == value
    with pytest.raises(ValueError):
        _append(case, value, reset=True)


def test_actual_128_lifetime_ids_include_dismissed_rows(case):
    changes = [_add(case, number + 1, ((number % 16) * 8, (number // 16) * 8,
                                      (number % 16) * 8 + 4, (number // 16) * 8 + 4))
               for number in range(128)]
    value = _append(case, _new(case), *changes)
    assert len(_replay(case, value)["declaration"]["annotations"]) == 128
    value = _transition(case, value, "dismiss", status="dismissed")
    with pytest.raises(ValueError):
        _append(case, value, _add(case, 129, (132, 132, 136, 136)))
    with pytest.raises(ValueError):
        _append(case, _new(case), *changes, changes[-1])


@pytest.mark.parametrize("reference,tokens", [("x" * 20_001, []), ("\u0344" * 10_001, []),
    ("\ud800", []), (False, []), ("", [str(index) for index in range(65)]),
    ("", ["x" * 257]), ("", ["\u0344" * 129]), ("", [""]), ("", [" \n"]),
    ("", ["é", "e\u0301"]), ("", ["\udfff"]), ("", [1]), ("", ("foo",))],
    ids=["raw-reference-limit", "normalized-reference-limit", "reference-surrogate",
         "reference-bool", "token-count", "raw-token-limit", "normalized-token-limit",
         "empty-token", "whitespace-token", "normalized-duplicate", "token-surrogate",
         "token-integer", "token-tuple"])
def test_raw_and_normalized_text_token_bounds(case, reference, tokens):
    with pytest.raises(ValueError):
        _new(case, reference, tokens)


def test_individual_raw_text_and_token_maxima_are_not_blanket_refused(case):
    value = _new(case, "x" * 20_000, [f"{index:03d}" + "y" * 253 for index in range(64)])
    declaration = _replay(case, value)["declaration"]
    assert len(declaration["reference"]) == 20_000
    assert len(declaration["critical_tokens"]) == 64
    assert all(len(token) == 256 for token in declaration["critical_tokens"])


@pytest.mark.parametrize("field,oversize", [("tentative_text", "x" * 2001),
    ("reason", "x" * 513), ("reason", " \n")],
    ids=["tentative-limit", "reason-limit", "blank-reason"])
def test_authored_scalar_bounds(case, field, oversize):
    change = _add(case)
    target = change["annotation"] if field == "tentative_text" else change
    target[field] = oversize
    with pytest.raises(ValueError):
        _append(case, _new(case), change)


def test_revision_reason_and_resolution_reading_bounds(case):
    value = _new(case)
    with pytest.raises(ValueError):
        journal.append_crop_journal(value, anchor_report=case.report,
            anchor_report_sha256=case.report_sha, annotation_changes=[], reason="x" * 513)
    value = _append(case, value, _add(case))
    with pytest.raises(ValueError):
        _transition(case, value, "resolve", status="resolved",
            resolution={"decision": "reading_confirmed", "reading": "x" * 20_001})


def test_complete_future_envelope_budget_counts_duplicated_head_and_base(case):
    # Each authored scalar is independently within its raw/normalized bound.
    reference = "😀" * 20_000
    tokens = [f"{index:03d}" + "😀" * 253 for index in range(64)]
    assert len(reference) == 20_000 and all(len(token) == 256 for token in tokens)
    with pytest.raises(ValueError):
        _new(case, reference, tokens)


@pytest.mark.parametrize("text", ["\x00", "😀", '"', "\\", "\n", "é"])
def test_private_encoder_exact_escaped_utf8_boundary(text):
    expected = _raw(text)
    assert journal._bytes(text, maximum=len(expected)) == expected
    with pytest.raises(ValueError):
        journal._bytes(text, maximum=len(expected) - 1)


@pytest.mark.parametrize("encode", [crops._bytes, journal._bytes], ids=["comparison", "journal"])
def test_nested_encoder_bytes_remain_exact(encode):
    value = {"z": "é\n", "a": [True, False, None, -0.0, {"quoted": '"'}]}
    expected = b'{"a":[true,false,null,-0.0,{"quoted":"\\\""}],"z":"\xc3\xa9\\n"}'
    assert encode(value, len(expected)) == expected


@pytest.mark.parametrize("encode,early", [(crops._bytes, False), (journal._bytes, True)],
                         ids=["comparison", "journal"])
@pytest.mark.parametrize("error_type", [ValueError, TypeError, RuntimeError])
@pytest.mark.parametrize("value", [None, float("nan")], ids=["finite", "nonfinite"])
def test_encoder_failure_stage_and_translation(encode, early, error_type, value, monkeypatch):
    error, calls = error_type("generated encoder fault"), []

    def reject(**_kwargs):
        calls.append(True)
        raise error

    monkeypatch.setattr(json, "JSONEncoder", reject)
    preflight = early and value != value
    translated = preflight or error_type is TypeError or (not early and error_type is ValueError)
    with pytest.raises(ValueError if translated else error_type) as caught:
        encode(value, 100)
    assert bool(calls) is not preflight
    assert (caught.value is error) is not translated


def test_journal_type_rejection_does_not_compare_with_float():
    class NonJsonType(type):
        def __eq__(cls, other):
            if other is float:
                raise RuntimeError("unexpected float comparison")
            return False

    class NonJson(metaclass=NonJsonType):
        pass

    with pytest.raises(ValueError, match="invalid crop uncertainty journal"):
        journal._bytes(NonJson())


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), 1 << 65, (1,), {1: "key"},
    {"x" * 129: None}, "\ud800", object()])
def test_private_preflight_rejects_unbounded_or_non_json_values(bad):
    with pytest.raises(ValueError):
        journal._bytes(bad)


def test_private_depth_and_node_limits_before_encoding(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("encoder reached before preflight refusal")

    monkeypatch.setattr(journal.json, "JSONEncoder", forbidden)
    nested = None
    for _ in range(33):
        nested = [nested]
    with pytest.raises(ValueError):
        journal._bytes(nested)
    with pytest.raises(ValueError):
        journal._bytes([None] * 100_000)


@pytest.mark.parametrize("payload", [{}, {"a": 1, "b": 2}, {1: 1}])
def test_private_fields_cardinality_and_exact_key_type_precede_set_allocation(payload, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("set allocation reached before cheap field refusal")

    monkeypatch.setattr(journal, "set", forbidden, raising=False)
    with pytest.raises(ValueError):
        journal._fields(payload, {"a"})


def test_view_byte_preflight_precedes_geometry_validation(case, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("oversize view reached geometry validator")

    view = copy.deepcopy(case.view)
    view["padding"] = "x" * 4096
    monkeypatch.setattr(journal, "validate_raster_view", forbidden)
    with pytest.raises(ValueError):
        _append(case, _new(case), _add(case), views=[view])


def test_declared_default_lifetime_budgets():
    assert (journal.MAX_REVISIONS, journal.MAX_VIEWS, journal.MAX_ANNOTATIONS) == (64, 64, 128)
    assert journal.MAX_CHANGES == journal.MAX_RESET_VISITS == 8192
    assert journal.MAX_OVERLAP_CHECKS == 64 * 128 * 127 // 2
    assert journal.MAX_READING_SCAN == 16 * 1024 * 1024
    assert (journal.MAX_BYTES, journal.MAX_VIEW_BYTES) == (256 * 1024, 4096)
    assert (journal.MAX_DEPTH, journal.MAX_NODES) == (32, 100_000)


def test_lowered_view_budget_is_lifetime_not_new_suffix(case, monkeypatch):
    value = _append(case, _new(case), _add(case))
    extra = _view(case, serial=1)
    row = _row(case, value)
    row["tentative_text"] = "new"
    monkeypatch.setattr(journal, "MAX_VIEWS", 1)
    assert _replay(case, value)["journal"] == value
    with pytest.raises(ValueError):
        _append(case, value, _change("edit_tentative", row, extra), views=[extra])


def test_lowered_change_budget_counts_replay_plus_new_suffix(case, monkeypatch):
    value = _append(case, _new(case), _add(case))
    row = _row(case, value)
    row["tentative_text"] = "new"
    monkeypatch.setattr(journal, "MAX_CHANGES", 1)
    assert _replay(case, value)["journal"] == value
    with pytest.raises(ValueError):
        _append(case, value, _change("edit_tentative", row, case.view))


def test_lowered_reset_visit_budget_counts_unchanged_and_dismissed_rows(case, monkeypatch):
    value = _append(case, _new(case), _add(case), _add(case, 2, (40, 0, 60, 20)))
    value = _transition(case, value, "dismiss", number=2, status="dismissed")
    value = _append(case, value, reset=True)
    monkeypatch.setattr(journal, "MAX_RESET_VISITS", 3)
    assert _replay(case, value)["journal"] == value
    with pytest.raises(ValueError):
        _append(case, value, reset=True)


def test_lowered_overlap_budget_counts_every_atomic_state(case, monkeypatch):
    value = _append(case, _new(case), _add(case), _add(case, 2, (40, 0, 60, 20)))
    value = _append(case, value, reset=True)
    monkeypatch.setattr(journal, "MAX_OVERLAP_CHECKS", 2)
    assert _replay(case, value)["journal"] == value
    with pytest.raises(ValueError):
        _append(case, value, reset=True)


def test_lowered_reading_scan_budget_is_shared_across_history(case, monkeypatch):
    value = _resolved(case, "foo")  # One 3+3 scan at the resolved state.
    row = _row(case, value)
    row["raw_span"] = {"reference_text_sha256": _text_sha("foo"), "start": 0, "end": 3}
    change = _change("anchor", row, case.view)
    monkeypatch.setattr(journal, "MAX_READING_SCAN", 11)
    assert _replay(case, value)["journal"] == value
    with pytest.raises(ValueError):
        _append(case, value, change)
    monkeypatch.setattr(journal, "MAX_READING_SCAN", 12)
    extended = _append(case, value, change)
    assert _row(case, extended)["raw_span"] == row["raw_span"]


def test_journal_replay_does_not_change_explicit_legacy_v1_metric_bytes(case, scorer_guard, monkeypatch):
    before = _report("liable", bbox=[0., 0., 1., 1.])
    after = _report("not liable", dpi=400, bbox=[0., 0., 1., 1.])
    local = SimpleNamespace(report=before, report_sha=_sha(before), scope=case.scope)
    raw_reference, tokens = "  not \nliable ", ["not liable"]

    def legacy_score(reference, critical):
        with monkeypatch.context() as enabled:
            enabled.setattr(crops, "compare_ocr", scorer_guard[0])
            enabled.setattr(ocr_comparison, "compare_ocr", scorer_guard[1])
            enabled.setattr(ocr_evaluation, "evaluate_ocr", scorer_guard[2])
            ref = crops.build_crop_reference(before, anchor_report_sha256=_sha(before),
                anchor_region_id="a", reference=reference, critical_tokens=critical, confirmed=True)
            return crops.compare_crop_candidates(before, after, ref,
                baseline_report_sha256=_sha(before), retry_report_sha256=_sha(after),
                baseline_region_id="a", retry_region_id="a")

    expected = legacy_score(raw_reference, tokens)
    value = _new(local, raw_reference, tokens)  # All scorer sentinels restored here.
    declaration = _replay(local, value)["declaration"]
    assert declaration["annotations"] == []
    actual = legacy_score(declaration["reference"], declaration["critical_tokens"])
    assert _raw(actual) == _raw(expected)
    assert actual["comparison"]["summary"]["outcomes"]["improved"] == 1
