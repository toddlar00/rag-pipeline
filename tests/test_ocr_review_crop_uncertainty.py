"""Shared helper controls over real generated journal/raster declarations.

These are pure helper tests, not browser event/token, native rendering or human
consent proof. The host stand-in below calls the real journal builder/appender;
no validator or scorer is replaced in positive authoring paths.
"""

import copy
import hashlib

import pytest

import ocr_comparison
import ocr_crop_comparison as crops
import ocr_crop_uncertainty_journal as journal
import ocr_evaluation
import ocr_review_crop_uncertainty as helper
import test_ocr_crop_uncertainty_journal as jf
from test_ocr_crop_uncertainty_journal import case as case


@pytest.fixture(autouse=True)
def no_scorers(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("shared authoring helper called a scorer")
    monkeypatch.setattr(crops, "compare_ocr", forbidden)
    monkeypatch.setattr(ocr_comparison, "compare_ocr", forbidden)
    monkeypatch.setattr(ocr_evaluation, "evaluate_ocr", forbidden)


def host(case, value, *, prefix="pair_sha256"):
    """Actual journal host behavior using an inert report, no callback double."""
    value = copy.deepcopy(value)
    current = value.pop("journal")
    if current is None:
        current = jf._new(case, value["reference"], value["critical_tokens"])
    response = journal.append_crop_journal_declaration(current, anchor_report=case.report,
        anchor_report_sha256=case.report_sha, **value)
    return {prefix: "c" * 64, **response, "requires_attention": True, "canonical_extraction_modified": False}


def install(case, value, *, prefix="pair_sha256"):
    response = jf._replay(case, value)
    return helper.install_author_result({prefix: "c" * 64, **response,
        "requires_attention": True, "canonical_extraction_modified": False}, scope=case.scope)


def state(case, status="unresolved", text="foo", tokens=None):
    value = jf._new(case, text, [] if tokens is None else tokens)
    if status == "none":
        return install(case, value)
    value = jf._append(case, value, jf._add(case, tentative="raw <text>"))
    if status == "resolved":
        value = jf._transition(case, value, "resolve", status="resolved",
            resolution={"decision": "reading_confirmed", "reading": text})
    elif status == "dismissed":
        value = jf._transition(case, value, "dismiss", status="dismissed")
    return install(case, value)


def arguments(case, current=None, *, commands=None, reference="foo", tokens=None, view=None):
    return helper.author_arguments(helper.empty_uncertainty() if current is None else current,
        reference=reference, critical_tokens=[] if tokens is None else tokens,
        scope=case.scope, raster_view=case.view if view is None else view,
        commands=[] if commands is None else commands, reason="Human supplied reason")


def add(pixels=None, *, kind="uncertain", tentative=None):
    return {"action": "add", "selection": {"kind": "whole_scope" if pixels is None else "raster_edges",
        "pixel_bbox": None if pixels is None else list(pixels)}, "kind": kind, "tentative_text": tentative}


def command(action, **fields):
    return {"action": action, "annotation_id": "a000001", **fields}


@pytest.mark.parametrize("prefix", ["pair_sha256", "pack_sha256"])
def test_genesis_install_matches_real_host_and_detaches(case, prefix):
    request = arguments(case)
    assert request["journal"] is None and request["views"] == []
    response = host(case, request, prefix=prefix)
    result = helper.install_author_result(response, scope=case.scope)
    assert result == {"journal": response["journal"], "annotations": [], "dirty": False}
    result["journal"]["base"]["reference"] = "changed"
    assert response["journal"]["base"]["reference"] == "foo"
    assert helper.empty_uncertainty() == {"journal": None, "annotations": [], "dirty": False}


def test_restore_v1_and_v2_partial_drafts_does_not_invent_genesis(case):
    assert helper.restore_uncertainty({"reference": "raw", "critical_tokens_text": "OCR\n"}) == helper.empty_uncertainty()
    current = helper.dirty_uncertainty(state(case, "resolved"))
    draft = {"reference": "unfinished", "critical_tokens_text": "OCR\n", "uncertainty": current}
    answer = helper.restore_uncertainty(draft)
    assert answer == current and answer is not current
    answer["annotations"][0]["kind"] = "illegible"
    assert current["annotations"][0]["kind"] == "uncertain"


@pytest.mark.parametrize("status", ["unresolved", "resolved", "dismissed"])
def test_dirty_reset_is_idempotent_and_preserves_tombstones_and_history(case, status):
    current = state(case, status)
    original = copy.deepcopy(current)
    changed = helper.dirty_uncertainty(current)
    assert helper.dirty_uncertainty(changed) == changed
    assert changed["journal"] == current["journal"] and current == original
    assert changed["dirty"] is True
    assert changed["annotations"][0]["status"] == ("dismissed" if status == "dismissed" else "unresolved")
    assert changed["annotations"][0]["raw_span"] is None
    assert changed["annotations"][0]["resolution"] is None
    with pytest.raises(ValueError):
        helper.prepare_policy(changed, reference="foo", critical_tokens=[])


def test_observed_aba_commits_real_reset_and_does_not_edit_or_compact_history(case):
    current = state(case, "resolved")
    dirty = helper.dirty_uncertainty(current)
    request = arguments(case, dirty)  # raw text back to original; dirty remains
    assert request["reset_anchors"] is True and request["journal"] == current["journal"]
    returned = host(case, request)
    answer = helper.install_author_result(returned, scope=case.scope)
    assert answer["dirty"] is False and answer["annotations"][0]["status"] == "unresolved"
    assert answer["journal"]["revisions"][:-1] == current["journal"]["revisions"]


def test_missed_edit_listener_detected_by_exact_committed_raw_fields(case):
    current = state(case, "resolved")
    request = arguments(case, current, reference="bar", tokens=["bar"])
    assert request["reset_anchors"] is True
    answer = helper.install_author_result(host(case, request), scope=case.scope)
    assert answer["annotations"][0]["status"] == "unresolved"
    assert answer["journal"]["revisions"][-1]["reference"] == "bar"


@pytest.mark.parametrize("action,fields,expected", [
    ("move", {"selection": {"kind": "raster_edges", "pixel_bbox": [36, 36, 72, 72]}}, "unresolved"),
    ("reclassify", {"kind": "illegible"}, "unresolved"),
    ("edit_tentative", {"tentative_text": "alternative"}, "unresolved"),
    ("anchor", {"span": [0, 3]}, "unresolved"),
    ("resolve", {"decision": "reading_confirmed", "reading": "foo", "span": [0, 3]}, "resolved"),
    ("resolve", {"decision": "not_text", "reading": "", "span": None}, "resolved"),
    ("dismiss", {}, "dismissed"),
    ("reopen", {}, "unresolved"),
], ids=["move", "kind", "tentative", "anchor", "reading", "not-text", "dismiss", "reopen"])
def test_all_human_actions_build_afterimages_accepted_by_real_host(case, action, fields, expected):
    current = state(case, "resolved" if action == "reopen" else "unresolved")
    before = copy.deepcopy(current)
    request = arguments(case, current, commands=[command(action, **fields)])
    assert request["annotation_changes"][0]["view_sha256"] == case.view["view_sha256"]
    assert request["views"] == []  # retained same-view registry member
    answer = helper.install_author_result(host(case, request), scope=case.scope)
    assert answer["annotations"][0]["status"] == expected and current == before
    assert answer["journal"]["revisions"][-1]["reason"] == "Human supplied reason"


def test_add_batch_allocates_ids_and_view_once_then_sorts_actions(case):
    request = arguments(case, commands=[add([0, 0, 36, 36]), add([36, 0, 72, 36], kind="illegible")])
    assert [item["annotation"]["annotation_id"] for item in request["annotation_changes"]] == ["a000001", "a000002"]
    assert request["views"] == [case.view]
    current = helper.install_author_result(host(case, request), scope=case.scope)
    edited = arguments(case, current, commands=[
        {"action": "dismiss", "annotation_id": "a000002"}, command("dismiss")])
    assert [item["annotation"]["annotation_id"] for item in edited["annotation_changes"]] == ["a000001", "a000002"]
    assert len(helper.install_author_result(host(case, edited), scope=case.scope)["journal"]["revisions"]) == 2


def test_add_after_dismissed_tombstone_never_reuses_id(case):
    current = state(case, "dismissed")
    request = arguments(case, current, commands=[add([72, 72, 108, 108])])
    assert request["annotation_changes"][0]["annotation"]["annotation_id"] == "a000002"
    answer = helper.install_author_result(host(case, request), scope=case.scope)
    assert [row["status"] for row in answer["annotations"]] == ["dismissed", "unresolved"]


def test_text_reset_then_explicit_resolution_is_atomic(case):
    current = state(case, "resolved")
    request = arguments(case, current, reference="bar", commands=[
        command("resolve", decision="reading_confirmed", reading="bar", span=[0, 3])])
    assert request["reset_anchors"] is True
    answer = helper.install_author_result(host(case, request), scope=case.scope)
    assert answer["annotations"][0]["resolution"]["reading"] == "bar"
    assert helper.prepare_policy(answer, reference="bar", critical_tokens=[])["scorable"] is True


def test_unicode_offsets_are_explicit_codepoints_with_server_raw_hash(case):
    current = state(case, text="A🙂e\u0301")
    request = arguments(case, current, reference="A🙂e\u0301", commands=[
        command("resolve", decision="reading_confirmed", reading="🙂", span=[1, 2])])
    span = request["annotation_changes"][0]["annotation"]["raw_span"]
    assert span == {"reference_text_sha256": hashlib.sha256("A🙂e\u0301".encode()).hexdigest(), "start": 1, "end": 2}
    assert helper.install_author_result(host(case, request), scope=case.scope)["annotations"][0]["status"] == "resolved"


@pytest.mark.parametrize("status", ["none", "unresolved", "resolved", "dismissed"])
def test_prepare_policy_is_validation_only_and_exact_counts(case, status):
    current = state(case, status)
    result = helper.prepare_policy(current, reference="foo", critical_tokens=[])
    assert result == {"scorable": status != "unresolved", "reason": "reference_uncertain" if status == "unresolved" else None,
        "annotation_total": int(status != "none"), "unresolved": int(status == "unresolved"),
        "resolved": int(status == "resolved"), "dismissed": int(status == "dismissed")}


def test_uncertain_empty_and_absent_critical_never_enter_legacy_final_validation(case, monkeypatch):
    current = state(case, text="", tokens=["OCR"])
    monkeypatch.setattr(ocr_evaluation, "_validate", lambda *_a, **_k: pytest.fail("unresolved entered final reference validation"))
    assert helper.prepare_policy(current, reference="", critical_tokens=["OCR"])["scorable"] is False


def test_no_uncertainty_requires_full_legacy_critical_occurrence_and_empty_allowed(case):
    current = state(case, "none", text="", tokens=["OCR"])
    with pytest.raises(ValueError):
        helper.prepare_policy(current, reference="", critical_tokens=["OCR"])
    current = state(case, "none", text="")
    assert helper.prepare_policy(current, reference="", critical_tokens=[])["scorable"] is True


@pytest.mark.parametrize("profile,scale", [("fit", 1), ("dpi288", 2), ("dpi576", 4)])
def test_overlay_numeric_only_preserves_bbox_across_profiles(case, profile, scale):
    current = state(case)
    before = copy.deepcopy(current)
    result = helper.overlay_spec(current, scope=case.scope, raster_view=jf._view(case, profile))
    assert result == {"width": 144 * scale, "height": 144 * scale,
        "requested_bbox": [0., 0., 144. * scale, 144. * scale],
        "rectangles": [{"ordinal": 1, "pixel_bbox": [0., 0., 36. * scale, 36. * scale], "state": 0}]}
    assert "raw <text>" not in repr(result) and current == before


@pytest.mark.parametrize("fault", ["extra", "afterimage", "client-id", "wrong-id", "duplicate", "bool-span", "outside", "null-pixels"])
def test_closed_command_refusals_before_host(case, fault):
    current = state(case)
    commands = [command("anchor", span=[0, 1])]
    if fault == "extra":
        commands[0]["extra"] = True
    elif fault == "afterimage":
        commands = [{"action": "resolve", "annotation": current["annotations"][0]}]
    elif fault == "client-id":
        commands = [{**add(), "annotation_id": "a000128"}]
    elif fault == "wrong-id":
        commands[0]["annotation_id"] = "a000002"
    elif fault == "duplicate":
        commands *= 2
    elif fault == "bool-span":
        commands[0]["span"] = [False, True]
    elif fault == "outside":
        commands = [add([-1, 0, 1, 2])]
    else:
        commands = [add()]
        commands[0]["selection"]["kind"] = "raster_edges"
    before = copy.deepcopy(current)
    with pytest.raises(ValueError):
        arguments(case, current, commands=commands)
    assert current == before


@pytest.mark.parametrize("action", ["dismiss", "reopen", "reclassify", "resolve"])
def test_final_host_retains_status_noop_and_reading_authority(case, action):
    current = state(case, "dismissed" if action in ("dismiss", "reclassify") else "unresolved")
    fields = {"kind": "illegible"} if action == "reclassify" else {"decision": "reading_confirmed", "reading": "absent", "span": None} if action == "resolve" else {}
    request = arguments(case, current, commands=[command(action, **fields)])
    with pytest.raises(ValueError):
        host(case, request)


def test_overlap_is_refused_by_real_host_not_silently_adjusted(case):
    request = arguments(case, commands=[add([0, 0, 36, 36]), add([18, 18, 54, 54])])
    with pytest.raises(ValueError):
        host(case, request)


@pytest.mark.parametrize("fault", ["dirty", "text", "tokens", "annotation", "null"])
def test_prepare_requires_clean_exact_installed_head(case, fault):
    current = state(case)
    text, tokens = "foo", []
    if fault == "dirty":
        current = helper.dirty_uncertainty(current)
    elif fault == "text":
        text = "changed"
    elif fault == "tokens":
        tokens = ["foo"]
    elif fault == "annotation":
        current["annotations"] = []
    else:
        current = helper.empty_uncertainty()
    with pytest.raises(ValueError):
        helper.prepare_policy(current, reference=text, critical_tokens=tokens)


@pytest.mark.parametrize("fault", ["head", "declaration", "scope", "attention", "extra"])
def test_host_result_installation_rechecks_bounded_declaration_bindings(case, fault):
    response = host(case, arguments(case, commands=[add()]))
    if fault == "head":
        response["journal"]["revisions"][-1]["after_declaration_sha256"] = "f" * 64
    elif fault == "declaration":
        response["declaration"]["annotations"] = []
    elif fault == "scope":
        response["declaration"]["scope"]["scope_sha256"] = "f" * 64
    elif fault == "attention":
        response["requires_attention"] = False
    else:
        response["image"] = None
    with pytest.raises(ValueError):
        helper.install_author_result(response, scope=case.scope)


@pytest.mark.parametrize("fault", ["commands", "reason", "tentative", "surrogate", "state-extra", "dirty-type"])
def test_bounds_and_exact_types(case, fault):
    current = helper.empty_uncertainty()
    kwargs = dict(reference="foo", critical_tokens=[], scope=case.scope, raster_view=case.view, commands=[], reason="reason")
    if fault == "commands":
        kwargs["commands"] = [add()] * 129
    elif fault == "reason":
        kwargs["reason"] = "x" * 513
    elif fault == "tentative":
        kwargs["commands"] = [add(tentative="x" * 2001)]
    elif fault == "surrogate":
        kwargs["reference"] = "\ud800"
    elif fault == "state-extra":
        current["authority"] = True
    else:
        current["dirty"] = 1
    with pytest.raises(ValueError):
        helper.author_arguments(current, **kwargs)


def test_new_profile_is_registered_only_when_an_action_uses_it(case):
    current = state(case)
    view = jf._view(case, "dpi288")
    assert arguments(case, current, view=view)["views"] == []
    request = arguments(case, current, view=view, commands=[command("dismiss")])
    assert request["views"] == [view]
    installed = helper.install_author_result(host(case, request), scope=case.scope)
    assert len(installed["journal"]["views"]) == 2


def test_digest_covers_dirty_annotations_and_complete_history(case):
    current = state(case)
    first = helper.uncertainty_digest(current)
    assert first == helper.uncertainty_digest(copy.deepcopy(current))
    assert first != helper.uncertainty_digest(helper.dirty_uncertainty(current))
    changed = copy.deepcopy(current)
    changed["journal"]["revisions"][0]["reason"] = "different declared reason"
    assert first != helper.uncertainty_digest(changed)
