"""Explicit v2 archive controls over real journals and inert service storage.

Generated reports, numeric raster declarations and PIL images only. Authoring,
reference validation and comparison are real; disk/history admission and source
rendering are named doubles. Installed process_api/Image.postprocess checks do
not establish browser paint order, pointer geometry or human inspection.
"""
import asyncio
import copy
import hashlib
import json
from types import SimpleNamespace as NS

import pytest

pytest.importorskip("PIL")

from PIL import Image

import ocr_crop_comparison as crops
import ocr_crop_raster_view as raster
import ocr_crop_uncertainty_comparison as comparison
import ocr_crop_uncertainty_journal as history
import ocr_review_crop_archive_ui as ui_module
import ocr_review_crop_uncertainty as authoring
from ocr_crop_review_runtime import CropPreviewError, CropRasterPreview
from test_ocr_crop_comparison import _report, _digest
from test_ocr_crop_uncertainty_workflow import journey as journey
from test_ocr_review_crop_uncertainty_live_ui import drain_result, process_result


PACK, OTHER, SHA = "a" * 32, "b" * 32, "c" * 64
FORM = [None, "add", "uncertain", False, "", "reading_confirmed", "", False, 0, 0, "explicit synthetic review"]


@pytest.fixture(scope="module")
def reports():
    return (_report("liable", bbox=[0., 0., 1., 1.]),
            _report("not liable", dpi=400, bbox=[0., 0., 1., 1.]))


@pytest.fixture
def ui(monkeypatch, reports):
    monkeypatch.setenv("GRADIO_ANALYTICS_ENABLED", "False")
    import gradio as gr

    before, after = copy.deepcopy(reports)
    scope = crops.crop_scope(before, region_id="a")
    workspace = NS(document=NS(source_sha256=scope["source_sha256"], recovery_sha256="e" * 64, page_count=scope["page_count"]))
    host = NS(workspace=workspace, state=ui_module._UncertaintyArchiveState(), scope=scope,
              before=before, after=after, calls=[], hooks={}, images=[], owners=[], saves=[],
              reference="not liable", critical="not liable", form=copy.deepcopy(FORM),
              draft={"reference": "not liable", "critical_tokens_text": "not liable"})

    def checkpoint(name, value=None):
        assert not host.state.lock._is_owned(), "host service called under UI lock"
        host.calls.append(name)
        if name in host.hooks:
            host.hooks[name](value)

    def catalog():
        checkpoint("catalog")
        return [{"pack_id": PACK, "manifest_sha256": SHA, "status": "present_unverified", "requires_attention": True},
                {"pack_id": OTHER, "manifest_sha256": "d" * 64, "status": "incomplete", "requires_attention": True}]

    def side(report):
        row = report["regions"][0]
        return {"region_id": "a", "report_sha256": _digest(report), "request_sha256": "f" * 64,
            "operation": "regions", "status": row["status"], "text": row["candidate"]["text"],
            "geometry": row["geometry"], "recipe": None, "configuration": report["retry_configuration"]}

    def opened(pack_id, *, preview_profile, cancel_requested):
        assert pack_id == PACK and not cancel_requested()
        checkpoint("open", cancel_requested)
        scale = {"fit": 2., "dpi288": 4., "dpi576": 8.}[preview_profile]
        width = int(72 * scale)
        image = Image.new("RGB", (width, width), (12, 23, 34))
        host.images.append(image)
        view = raster.build_raster_view(scope=scope, preview_profile=preview_profile,
            command_clip=[0., 0., 72., 72.], native_clip=[0., 0., 72., 72.],
            command_matrix=[scale, 0., 0., scale, 0., 0.], native_matrix=[scale, 0., 0., scale, 0., 0.],
            projected_rect=[0., 0., float(width), float(width)], pixel_rect=[0, 0, width, width],
            width=width, height=width, rgb_sha256=hashlib.sha256(image.tobytes()).hexdigest())
        owner = CropRasterPreview(image, view)
        host.owners.append(owner)
        value = {"pack_id": PACK, "pack_sha256": SHA, "scope": scope, "baseline": side(before), "retry": side(after),
            "draft": copy.deepcopy(host.draft), "historical_reviewed": None, "preview": owner,
            "validation_scope": "historical_local_declarations", "requires_attention": True, "canonical_extraction_modified": False}
        checkpoint("open_result", value)
        return value

    def recover(pack_id):
        assert pack_id == PACK
        checkpoint("recover")
        return {"draft": copy.deepcopy(host.draft), "scope": scope, "pack_sha256": SHA,
            "status": "unverified_saved_draft", "requires_attention": True, "canonical_extraction_modified": False}

    def author(pack_id, *, expected_pack_sha256, **args):
        assert pack_id == PACK and expected_pack_sha256 == SHA
        checkpoint("author", args)
        args = copy.deepcopy(args)
        journal = args.pop("journal")
        if journal is None:
            journal = history.build_crop_journal(before, anchor_report_sha256=_digest(before), anchor_region_id="a",
                                                reference=args["reference"], critical_tokens=args["critical_tokens"])
        result = history.append_crop_journal_declaration(journal, anchor_report=before,
                                                        anchor_report_sha256=_digest(before), **args)
        result.update(pack_sha256=SHA, requires_attention=True, canonical_extraction_modified=False)
        checkpoint("author_result", result)
        return result

    def compare(pack_id, *, expected_pack_sha256, journal, confirmed):
        assert pack_id == PACK and expected_pack_sha256 == SHA and confirmed is True
        checkpoint("compare", journal)
        reference = comparison.build_crop_reference_v2(before, anchor_report_sha256=_digest(before), journal=journal, confirmed=True)
        result = comparison.compare_crop_candidates_v2(before, after, reference,
            baseline_report_sha256=_digest(before), retry_report_sha256=_digest(after), baseline_region_id="a", retry_region_id="a")
        result = {"pack_sha256": SHA, "reference": reference, "comparison": result,
                  "requires_attention": True, "canonical_extraction_modified": False}
        checkpoint("compare_result", result)
        return result

    def save(pack_id, *, expected_pack_sha256, verify_current, **values):
        assert pack_id == PACK and expected_pack_sha256 == SHA
        checkpoint("save", verify_current)
        assert verify_current() is True
        host.saves.append(copy.deepcopy(values))
        checkpoint("commit", verify_current)
        assert verify_current() is True
        return {"pack_id": OTHER, "manifest_sha256": "d" * 64, "status": "verified_complete", "requires_attention": True}

    host.service = NS(catalog=catalog, open_with_view=opened, recover_draft_v2=recover,
                      author_v2=author, compare_v2=compare, save_revision_v2=save)
    app = gr.Blocks()
    try:
        with app:
            ui_module.build_crop_archive_panel(workspace, host.service, uncertainty=True)
        host.app = app
        host.functions = {item.fn.__name__: item for item in app.fns.values() if item.fn}
        yield host
    finally:
        app.close()
        for owner in host.owners:
            owner.close()


def call(ui, name, *args):
    trace = []
    result = drain_result(ui.functions[name].fn(ui.state, *args), trace)
    if name.startswith("save_archive_"):
        ui.save_trace = trace
    return result


def selected(ui):
    call(ui, "refresh_crop_archives")
    call(ui, "select_crop_archive", PACK, ui.state.generation)


def loaded(ui):
    selected(ui)
    result = call(ui, "open_crop_archive", PACK, ui.state.generation, ui.reference, ui.critical, "fit")
    assert ui.state.loaded is not None, result[13]
    ui.image_token = result[14]
    return result


def current(ui):
    return (PACK, ui.state.generation, ui.reference, ui.critical,
            ui.state.action_token, ui.state.preview_profile, ui.image_token)


def prepare(ui):
    result = call(ui, "prepare_archive_reference", *current(ui), *ui.form)
    assert ui.state.pending is not None, result[13]
    return result


def score(ui, ticket=None):
    ticket = ui.state.pending["ticket"] if ticket is None else ticket
    result = call(ui, "score_archive_reference", PACK, ui.state.generation, ui.reference, ui.critical,
                  ticket, True, ui.state.action_token, ui.state.preview_profile, ui.image_token, *ui.form)
    assert ui.state.last_score is not None, result[13]
    return result


def event(ui, kind, **kwargs):
    import ocr_review_crop_uncertainty_editor as editor

    value = editor.editor_value(image_token=ui.image_token, action_token=ui.state.action_token,
        mode_token=ui.state.mode_token, mode=ui.state.mode,
        **authoring.overlay_spec(ui.state.uncertainty, scope=ui.scope, raster_view=ui.state.loaded["raster_view"]))
    value["command"] = {"kind": kind, "mode": None, "pixel_bbox": None, "ordinal": None, **kwargs}
    return call(ui, "editor_input", ui.state.generation, ui.state.action_token, ui.image_token, value)


def apply(ui, action="add", **updates):
    names = ["selected", "action", "kind", "tentative_present", "tentative", "decision", "reading", "anchored", "start", "end", "reason"]
    values = dict(zip(names, ui.form))
    values.update(action=action, **updates)
    ui.form = [values[key] for key in names]
    result = call(ui, "apply", *current(ui), *ui.form)
    assert "recorded" in result[13] and "not applied" not in result[13].lower(), result[13]
    return result


def unresolved(ui):
    loaded(ui)
    event(ui, "mode", mode="annotate")
    event(ui, "whole_scope")
    apply(ui)
    assert ui.state.uncertainty["annotations"][0]["status"] == "unresolved"


def choose(ui, index=1):
    event(ui, "annotation", ordinal=index)
    ui.form[0] = ui.state.annotation_id


def test_fresh_session_and_deepcopy_never_restore_capabilities_or_consent(ui):
    assert ui.state.loaded is None and ui.state.catalog == {} and ui.state.image_token == ""
    assert ui.state.uncertainty == authoring.empty_uncertainty()
    loaded(ui)
    prepare(ui)
    fresh = copy.deepcopy(ui.state)
    assert fresh.catalog == {} and fresh.pending is None and fresh.image_token == ""
    assert fresh.generation != ui.state.generation and fresh.uncertainty == authoring.empty_uncertainty()


def test_explicit_v2_events_private_and_plain_fields_change_multiple(ui):
    for fn in ui.app.fns.values():
        assert fn.api_visibility == "private"
    event_fn = ui.functions["annotation_form_edited"]
    assert event_fn.queue is False and event_fn.trigger_mode == "multiple"
    assert all(target[1] == "change" for target in event_fn.targets)
    open_fn = ui.functions["open_crop_archive"]
    assert len(open_fn.outputs) == 19
    image = open_fn.outputs[1]
    assert image.show_label is False and image.interactive is False and image.sources == [] and image.buttons == []
    assert image.elem_id == "ocr-archive-source-preview"


def test_annotation_selector_input_and_ten_form_change_registrations_are_exact(ui):
    registrations = [fn for fn in ui.app.fns.values()
                     if fn.fn is not None and fn.fn.__name__ == "annotation_form_edited"]
    assert len(registrations) == 11 and len({fn.fn for fn in registrations}) == 1
    inputs = registrations[0].inputs
    selector, fields = inputs[4], inputs[5:]
    assert selector.label == "Annotation to change" and len(fields) == 10
    assert [field.label for field in fields] == [
        "Explicit annotation action", "Annotation kind", "Record tentative text (not reference truth)",
        "Tentative text", "Resolution decision",
        "Confirmed reading (must occur in transcription); leave empty for not_text",
        "Explicit raw transcription span (optional; Unicode codepoints, not browser UTF-16)",
        "Span start (inclusive)", "Span end (exclusive)", "Explicit reason for this revision"]
    targets = [tuple(target) for fn in registrations for target in fn.targets]
    assert len(targets) == 11
    assert set(targets) == {(selector._id, "input"), *((field._id, "change") for field in fields)}
    for fn in registrations:
        assert fn.inputs == inputs and fn.queue is False and fn.trigger_mode == "multiple"
        assert fn.api_visibility == "private"


@pytest.mark.parametrize("reviewed", [False, True])
def test_selector_input_handler_revokes_current_authority_and_identical_repeat_skips(ui, reviewed):
    # Restore a real resolved journal with no selected annotation, then model
    # the selector input using the same token-bound handler as its registration.
    unresolved(ui)
    choose(ui)
    apply(ui, "resolve", reading="not liable")
    ui.draft = {"reference": ui.reference, "critical_tokens_text": ui.critical,
                "uncertainty": copy.deepcopy(ui.state.uncertainty)}
    ui.form[0] = None
    loaded(ui)
    prepare(ui)
    if reviewed:
        assert json.loads(score(ui)[11])["coverage"]["scored_pairs"] == 1
    else:
        assert ui.state.pending is not None
    before = (ui.state.action_token, ui.state.action, copy.deepcopy(ui.state.uncertainty), list(ui.calls))
    ui.form[0] = ui.state.uncertainty["annotations"][0]["annotation_id"]
    result = call(ui, "annotation_form_edited", ui.state.generation, before[0], ui.image_token, *ui.form)
    assert "Annotation form changed; not applied." in result[13]
    assert ui.state.pending is None and ui.state.last_score is None
    assert ui.state.action_token != before[0] and ui.state.action == before[1] + 1
    assert ui.state.annotation_id == ui.form[0] and ui.state.uncertainty == before[2] and ui.calls == before[3]
    current_action = ui.state.action_token
    repeated = call(ui, "annotation_form_edited", ui.state.generation, current_action, ui.image_token, *ui.form)
    assert repeated == ({"__type__": "update"},) * 19
    assert ui.state.action_token == current_action and ui.state.action == before[1] + 1


@pytest.mark.parametrize("reviewed", [False, True])
def test_stale_selector_input_preserves_new_prepared_or_scored_authority_by_identity(ui, reviewed):
    unresolved(ui)
    choose(ui)
    apply(ui, "resolve", reading="not liable")
    ui.draft = {"reference": ui.reference, "critical_tokens_text": ui.critical,
                "uncertainty": copy.deepcopy(ui.state.uncertainty)}
    ui.form[0] = None
    loaded(ui)
    old_action = ui.state.action_token
    prepare(ui)
    if reviewed:
        assert json.loads(score(ui)[11])["coverage"]["scored_pairs"] == 1
    pending, last_score, current_action = ui.state.pending, ui.state.last_score, ui.state.action_token
    before, calls = copy.deepcopy(ui.state.uncertainty), list(ui.calls)
    selected_id = ui.state.uncertainty["annotations"][0]["annotation_id"]
    result = call(ui, "annotation_form_edited", ui.state.generation, old_action,
                  ui.image_token, selected_id, *ui.form[1:])
    assert result == ({"__type__": "update"},) * 19
    assert ui.state.pending is pending and ui.state.last_score is last_score
    assert ui.state.action_token == current_action and ui.state.annotation_id is None
    assert ui.state.uncertainty == before and ui.calls == calls


def test_image_delivery_token_distinct_and_never_reissued_by_actions(ui):
    result = loaded(ui)
    assert result[14] == ui.state.image_token and result[14] not in (result[0], result[12])
    assert result[15]["image_token"] == result[14]
    assert result[1]["value"].tobytes() == ui.images[-1].tobytes()
    prepared = prepare(ui)
    assert prepared[14] == {"__type__": "update"} and prepared[15]["image_token"] == ui.image_token
    assert ui.state.loaded["raster_view"]["rgb_sha256"] == hashlib.sha256(ui.images[-1].tobytes()).hexdigest()
    assert "preview" not in ui.state.loaded and "image" not in ui.state.loaded


@pytest.mark.parametrize("critical", ["", "not liable"])
def test_no_annotations_resolved_score_uses_full_reference_and_one_use_save(ui, critical):
    ui.critical = critical
    loaded(ui)
    # Open restores the declared draft, then actual captured field content is authored.
    prepare(ui)
    result = score(ui)
    metrics = json.loads(result[11])
    assert metrics["schema_version"] == 2 and metrics["coverage"]["scored_pairs"] == 1
    before = ui.state.action_token
    saved = call(ui, "save_archive_reviewed", *current(ui), *ui.form)
    assert "saved" in saved[13] and len(ui.saves) == 1
    assert ui.saves[0]["reviewed"]["source_image"] == ui.state.loaded["raster_view"]
    refused = call(ui, "save_archive_reviewed", PACK, ui.state.generation, ui.reference, ui.critical,
                  before, "fit", ui.image_token, *ui.form)
    assert refused[:19] == ({"__type__": "update"},) * 19 and "unconfirmed" in refused[19]
    assert len(ui.saves) == 1


def test_unresolved_review_zero_score_coverage_and_explicit_resolution(ui, monkeypatch):
    unresolved(ui)
    original = copy.deepcopy(ui.state.uncertainty["journal"])
    with monkeypatch.context() as patch:
        patch.setattr(crops, "compare_ocr", lambda *_a, **_kw: pytest.fail("unresolved reference scored"))
        prepare(ui)
        result = score(ui)
        metrics = json.loads(result[11])
        assert metrics["reason"] == "reference_uncertain" and metrics["comparison"] is None
        assert metrics["coverage"]["scored_pairs"] == 0 and metrics["coverage"]["reference_unresolved_pairs"] == 1
        assert metrics["uncertainty"]["character_coverage"] is None
        call(ui, "save_archive_reviewed", *current(ui), *ui.form)
    choose(ui)
    apply(ui, "resolve", reading="not liable")
    assert ui.state.uncertainty["journal"]["revisions"][:1] == original["revisions"]
    prepare(ui)
    assert json.loads(score(ui)[11])["coverage"]["scored_pairs"] == 1


@pytest.mark.parametrize("action,changes", [
    ("reclassify", {"kind": "illegible"}), ("edit_tentative", {"tentative_present": True, "tentative": "maybe"}),
    ("anchor", {"anchored": True, "start": 0, "end": 3}), ("resolve", {"decision": "not_text"}),
    ("dismiss", {}), ("move", {}),
])
def test_explicit_annotation_commands_append_without_score(ui, action, changes):
    unresolved(ui)
    choose(ui)
    if action == "move":
        event(ui, "rectangle", pixel_bbox=[1., 2., 50., 60.])
    apply(ui, action, **changes)
    assert len(ui.state.uncertainty["journal"]["revisions"]) == 2
    assert "compare" not in ui.calls and not ui.saves


def test_reopen_retains_tombstone_id_and_history(ui):
    unresolved(ui)
    choose(ui)
    apply(ui, "dismiss")
    apply(ui, "reopen")
    assert ui.state.uncertainty["annotations"][0]["annotation_id"] == "a000001"
    assert ui.state.uncertainty["annotations"][0]["status"] == "unresolved"
    assert len(ui.state.uncertainty["journal"]["revisions"]) == 3


def test_selection_and_mode_are_pending_only_and_clear_current_review(ui):
    loaded(ui)
    prepare(ui)
    calls = ui.calls.count("author")
    event(ui, "mode", mode="annotate")
    assert ui.state.pending is None
    event(ui, "rectangle", pixel_bbox=[2., 3., 40., 50.])
    assert ui.state.uncertainty["annotations"] == [] and ui.calls.count("author") == calls
    result = call(ui, "prepare_archive_reference", *current(ui), *ui.form)
    assert ui.state.pending is None and ui.calls.count("author") == calls
    assert "pending selection" in result[13]
    event(ui, "cancel")
    prepare(ui)


def test_rectangle_in_browse_and_malformed_current_command_never_authors(ui):
    loaded(ui)
    prepare(ui)
    event(ui, "rectangle", pixel_bbox=[1., 2., 40., 50.])
    assert ui.state.pending is None and ui.state.selection is None
    assert ui.calls.count("author") == 1


@pytest.mark.parametrize("profile", ["fit", "dpi288", "dpi576"])
def test_same_entry_open_reload_preserves_unsaved_source_history_and_raw_partial_fields(ui, profile):
    unresolved(ui)
    retained = copy.deepcopy(ui.state.uncertainty)
    ui.reference, ui.critical = "partial", "OCR\n"
    call(ui, "archive_reference_edited", ui.state.generation, ui.reference, ui.critical, ui.state.action_token, ui.image_token)
    dirty = copy.deepcopy(ui.state.uncertainty)
    call(ui, "archive_preview_profile_changed", ui.state.generation, profile)
    result = call(ui, "open_crop_archive", PACK, ui.state.generation, ui.reference, ui.critical, profile)
    assert ui.state.uncertainty == dirty and ui.state.uncertainty["journal"] == retained["journal"]
    assert result[6:8] == ({"__type__": "update"},) * 2
    assert ui.state.pending is None and ui.state.last_score is None
    assert result[14] != ui.image_token
    assert ui.state.loaded["raster_view"]["preview_profile"] == profile


def test_failed_reload_preserves_history_draft_and_no_old_image_authority(ui):
    unresolved(ui)
    saved = copy.deepcopy(ui.state.uncertainty)
    ui.hooks["open"] = lambda _cancel: (_ for _ in ()).throw(CropPreviewError(code="raster_limit"))
    result = call(ui, "reload_archive_detail", PACK, ui.state.generation, ui.reference, ui.critical, "fit")
    assert result[1] is None and result[14] == "" and result[15] is None
    assert ui.state.uncertainty == saved and ui.state.loaded is None
    with pytest.raises(Exception):
        call(ui, "prepare_archive_reference", *current(ui), *ui.form)


def test_raw_aba_reopens_resolved_anchors_and_does_not_resurrect_approval(ui):
    unresolved(ui)
    choose(ui)
    apply(ui, "resolve", reading="not liable", anchored=True, start=0, end=10)
    prepare(ui)
    old = ui.state.pending["ticket"]
    score(ui)
    journal = copy.deepcopy(ui.state.uncertainty["journal"])
    for raw in ("not liable!", "not liable"):
        call(ui, "archive_reference_edited", ui.state.generation, raw, ui.critical, ui.state.action_token, ui.image_token)
    row = ui.state.uncertainty["annotations"][0]
    assert ui.state.uncertainty["dirty"] and row["status"] == "unresolved" and row["raw_span"] is None and row["resolution"] is None
    assert ui.state.uncertainty["journal"] == journal and ui.state.last_score is None
    with pytest.raises(Exception):
        call(ui, "score_archive_reference", PACK, ui.state.generation, ui.reference, ui.critical,
             old, True, ui.state.action_token, "fit", ui.image_token, *ui.form)
    prepare(ui)
    assert ui.state.uncertainty["journal"]["revisions"][-1]["reset_anchors"] is True


@pytest.mark.parametrize("index", [4, 6, 10])
def test_form_change_without_listener_cannot_score_or_save_prior_declaration(ui, index):
    loaded(ui)
    prepare(ui)
    ui.form[index] += "changed"
    with pytest.raises(Exception):
        score(ui)
    assert ui.state.last_score is None and "compare" not in ui.calls
    prepare(ui)
    score(ui)
    ui.form[index] += "again"
    refused = call(ui, "save_archive_reviewed", *current(ui), *ui.form)
    assert refused[:19] == ({"__type__": "update"},) * 19 and "unconfirmed" in refused[19]
    assert not ui.saves


def test_stale_form_and_edit_events_leave_newer_pending_ticket_exact(ui):
    loaded(ui)
    old = ui.state.action_token
    prepare(ui)
    saved = copy.deepcopy(ui.state.pending)
    for name, args in [
        ("annotation_form_edited", (ui.state.generation, old, ui.image_token, *ui.form)),
        ("archive_reference_edited", (ui.state.generation, None, object(), old, ui.image_token)),
    ]:
        assert call(ui, name, *args) == ({"__type__": "update"},) * 19
        assert ui.state.pending == saved


def test_identical_delayed_form_change_does_not_erase_prepare(ui):
    loaded(ui)
    prepare(ui)
    pending = copy.deepcopy(ui.state.pending)
    result = call(ui, "annotation_form_edited", ui.state.generation, ui.state.action_token, ui.image_token, *ui.form)
    assert result == ({"__type__": "update"},) * 19 and ui.state.pending == pending


@pytest.mark.parametrize("bad", [None, "OCR\n", "OCR\n\n"])
def test_current_typed_refusal_preserves_image_rawdraft_and_recovers(ui, bad):
    loaded(ui)
    reference, critical = (None, "") if bad is None else (ui.reference, bad)
    result = call(ui, "prepare_archive_reference", PACK, ui.state.generation, reference, critical,
        ui.state.action_token, "fit", ui.image_token, *ui.form)
    assert ui.state.pending is None and ui.state.loaded is not None
    assert result[1] == result[6] == result[7] == {"__type__": "update"}
    assert "transcription" in result[13] or "Critical" in result[13]
    prepare(ui)
    score(ui)


@pytest.mark.parametrize("method", ["prepare_archive_reference", "apply", "score_archive_reference", "save_archive_reviewed"])
def test_stale_or_undelivered_token_rejected_before_malformed_fields(ui, method):
    loaded(ui)
    prepare(ui)
    retained = copy.deepcopy(ui.state.pending)
    if method == "score_archive_reference":
        args = (PACK, ui.state.generation, object(), object(), retained["ticket"], True, ui.state.action_token, "fit", "", *ui.form)
    else:
        args = (PACK, ui.state.generation, object(), object(), ui.state.action_token, "fit", "", *ui.form)
    if method == "save_archive_reviewed":
        refused = call(ui, method, *args)
        assert refused[:19] == ({"__type__": "update"},) * 19 and "unconfirmed" in refused[19]
    else:
        with pytest.raises(Exception):
            call(ui, method, *args)
    assert ui.state.pending == retained and "compare" not in ui.calls


@pytest.mark.parametrize("during", ["author", "author_result", "compare", "compare_result", "commit"])
def test_slow_action_context_change_cannot_install_or_publish_current_success(ui, during):
    loaded(ui)
    if during.startswith("compare") or during == "commit":
        prepare(ui)
    if during == "commit":
        score(ui)

    def cancel(_value):
        call(ui, "cancel_archive_preview", "fit")

    ui.hooks[during] = cancel
    if during == "commit":
        refused = call(ui, "save_archive_reviewed", *current(ui), *ui.form)
        assert refused[:19] == ({"__type__": "update"},) * 19 and "unconfirmed" in refused[19]
    else:
        with pytest.raises(Exception):
            if during.startswith("author"):
                prepare(ui)
            else:
                score(ui)
    assert ui.state.loaded is None and ui.state.pending is None and ui.state.last_score is None


def test_draft_recovery_preserves_v2_dirty_journal_without_image_or_consent(ui):
    unresolved(ui)
    ui.draft = {"reference": "partial", "critical_tokens_text": "OCR\n",
                "uncertainty": authoring.dirty_uncertainty(ui.state.uncertainty)}
    saved = copy.deepcopy(ui.draft)
    selected(ui)
    result = call(ui, "recover_crop_archive_draft", PACK, ui.state.generation)
    assert result[1] is None and result[2:4] == ("", "") and result[11] == "" and result[14] == ""
    assert ui.state.uncertainty == saved["uncertainty"] and ui.state.pending is None
    assert ui.state.loaded["mode"] == "draft"
    ui.reference, ui.critical, ui.image_token = "partial", "OCR\n", ""
    call(ui, "save_archive_draft", *current(ui), *ui.form)
    assert ui.saves[-1]["uncertainty"] == saved["uncertainty"] and ui.saves[-1]["reviewed"] is None


def test_duplicate_selection_and_recovery_never_reset_current_journal(ui):
    unresolved(ui)
    prepare(ui)
    journal = copy.deepcopy(ui.state.uncertainty)
    assert call(ui, "select_crop_archive", PACK, ui.state.generation) == ({"__type__": "update"},) * 19
    call(ui, "recover_crop_archive_draft", PACK, ui.state.generation)
    assert ui.state.uncertainty == journal and ui.state.pending is None and "recover" not in ui.calls


def test_actual_failed_image_postprocess_cannot_mint_delivery_via_current_edit_or_prepare(ui, monkeypatch):
    import gradio as gr

    selected(ui)
    event_fn = ui.functions["open_crop_archive"]
    state = ui.app.state_holder["unseen-archive-v2"]
    state[event_fn.inputs[0]._id] = ui.state
    def fail(_self, _image):
        raise RuntimeError("private postprocess failure")

    async def run():
        await ui.app.process_api(ui.functions["refresh_crop_archives"], [None],
            state=state, session_hash="unseen-archive-v2")
        call(ui, "select_crop_archive", PACK, ui.state.generation)
        old = ui.state.generation
        with monkeypatch.context() as patch:
            patch.setattr(gr.Image, "postprocess", fail)
            with pytest.raises(RuntimeError):
                await ui.app.process_api(event_fn, [None, PACK, old, ui.reference, ui.critical, "fit"],
                    state=state, session_hash="unseen-archive-v2")
        assert ui.state.loaded is not None and ui.state.image_token
        for token in (old, ui.state.generation):
            for missing in (None, ""):
                changed = call(ui, "archive_reference_edited", token, "new", "", ui.state.action_token, missing)
                assert changed == ({"__type__": "update"},) * 19
                with pytest.raises(Exception):
                    call(ui, "prepare_archive_reference", PACK, token, ui.reference, ui.critical,
                         ui.state.action_token, "fit", missing, *ui.form)
        assert ui.state.pending is None and "author" not in ui.calls
        result = await ui.app.process_api(event_fn, [None, PACK, ui.state.generation, ui.reference, ui.critical, "fit"],
            state=state, session_hash="unseen-archive-v2")
        ui.image_token = result["data"][14]
        assert ui.image_token == ui.state.image_token
        prepare(ui)
    asyncio.run(run())


@pytest.mark.parametrize("late", ["source", "malformed_result", "cancel"])
def test_open_late_refusal_closes_exact_owner_and_never_returns_image(ui, late, monkeypatch):
    selected(ui)
    closes = []
    original = CropRasterPreview.close

    def close(owner):
        closes.append(owner)
        original(owner)

    monkeypatch.setattr(CropRasterPreview, "close", close)

    def hook(value):
        if late == "source":
            ui.workspace.document.source_sha256 = "f" * 64
        elif late == "cancel":
            call(ui, "cancel_archive_preview", "fit")
        else:
            value["pack_sha256"] = "d" * 64

    ui.hooks["open_result"] = hook
    try:
        result = call(ui, "open_crop_archive", PACK, ui.state.generation, ui.reference, ui.critical, "fit")
        assert result[1] is None
    except Exception:
        pass
    assert closes == ui.owners
    with pytest.raises(ValueError):
        ui.images[-1].tobytes()


@pytest.mark.parametrize("field", ["action", "image", "profile"])
def test_none_captured_authority_never_skips_current_check(ui, field):
    loaded(ui)
    prepare(ui)
    pending = copy.deepcopy(ui.state.pending)
    args = list(current(ui))
    args[{"action": 4, "profile": 5, "image": 6}[field]] = None
    with pytest.raises(Exception):
        call(ui, "prepare_archive_reference", *args, *ui.form)
    assert ui.state.pending == pending and ui.calls.count("author") == 1


@pytest.mark.parametrize("path,bad", [
    (("comparison", "schema_version"), True), (("comparison", "kind"), "wrong"),
    (("comparison", "page_count"), True), (("comparison", "requires_attention"), False),
    (("comparison", "canonical_extraction_modified"), True), (("comparison", "reference_id"), "foreign"),
    (("comparison", "reference_sha256"), "f" * 64),
    (("comparison", "pair", "baseline", "report_sha256"), "f" * 64),
    (("comparison", "pair", "retry", "region_id"), "foreign"),
    (("reference", "schema_version"), True), (("reference", "kind"), "wrong"),
])
def test_wrong_host_comparison_identity_cannot_display_or_save(ui, path, bad):
    loaded(ui)
    prepare(ui)

    def mutate(value):
        target = value
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = bad

    ui.hooks["compare_result"] = mutate
    result = call(ui, "score_archive_reference", PACK, ui.state.generation, ui.reference, ui.critical,
        ui.state.pending["ticket"], True, ui.state.action_token, "fit", ui.image_token, *ui.form)
    assert result[11] == "" and ui.state.last_score is None and not ui.saves


@pytest.mark.parametrize("journey", ["regions", "hardscan"], indirect=True)
def test_actual_archive_store_service_process_api_unresolved_save_reopen_resolve_score(journey, monkeypatch):
    """Real historical validators/store/host; only source and raster port inert."""
    import gradio as gr

    monkeypatch.setenv("GRADIO_ANALYTICS_ENABLED", "False")
    service = journey["service"]
    app = gr.Blocks()
    with app:
        ui_module.build_crop_archive_panel(service._workspace, service, uncertainty=True)
    functions = {item.fn.__name__: item for item in app.fns.values() if item.fn}
    outputs = functions["open_crop_archive"].outputs
    state = app.state_holder["real-archive-v2"]
    session = copy.deepcopy(functions["open_crop_archive"].inputs[0].value)
    state[functions["open_crop_archive"].inputs[0]._id] = session
    displayed = {component._id: copy.deepcopy(component.value) for component in outputs}
    form = copy.deepcopy(FORM)

    async def send(name, *args):
        fn = functions[name]
        trace = []
        result = await process_result(app, fn, [None, *args], state=state,
                                      session_hash="real-archive-v2", trace=trace)
        if name.startswith("save_archive_"):
            assert len(trace) == 2 and "not yet confirmed" in trace[0][19]
        for component, value in zip(fn.outputs, result["data"]):
            if type(value) is dict and value.get("__type__") == "update":
                if "value" in value:
                    displayed[component._id] = value["value"]
            else:
                displayed[component._id] = value
        return result

    def shown(index):
        return displayed[outputs[index]._id]

    def args(pack):
        return (pack, shown(0), shown(6), shown(7), shown(12), "fit", shown(14))

    async def command(kind, **changes):
        value = copy.deepcopy(shown(15))
        value["command"] = {"kind": kind, "mode": None, "pixel_bbox": None, "ordinal": None, **changes}
        await send("editor_input", shown(0), shown(12), shown(14), value)

    async def run():
        await send("refresh_crop_archives")
        pack = journey["saved"]["pack_id"]
        await send("select_crop_archive", pack, shown(0))
        await send("open_crop_archive", pack, shown(0), "", "", "fit")
        assert shown(14) and shown(6) == "not liable" and shown(9) is False
        await command("mode", mode="annotate")
        await command("whole_scope")
        await send("apply", *args(pack), *form)
        assert session.uncertainty["annotations"][0]["status"] == "unresolved", shown(13)
        await send("prepare_archive_reference", *args(pack), *form)
        assert shown(8) and shown(9) is False
        await send("score_archive_reference", pack, shown(0), shown(6), shown(7), shown(8), True,
            shown(12), "fit", shown(14), *form)
        assert json.loads(shown(11))["reason"] == "reference_uncertain"
        await send("save_archive_reviewed", *args(pack), *form)
        rows = service.catalog()
        child = next(row for row in rows if row["pack_id"] != pack)
        retained = service._store.read(child["pack_id"])
        assert retained["manifest"]["schema_version"] == 2
        assert retained["manifest"]["review_state"] == "historical_unscorable_declaration"
        first_head = retained["review"]["draft"]["uncertainty"]["journal"]
        await send("refresh_crop_archives")
        pack = child["pack_id"]
        await send("select_crop_archive", pack, shown(0))
        await send("open_crop_archive", pack, shown(0), "", "", "fit")
        assert session.uncertainty["journal"] == first_head and not session.pending and shown(9) is False
        await command("mode", mode="annotate")
        await command("annotation", ordinal=1)
        form[0], form[1], form[6] = "a000001", "resolve", "not liable"
        await send("apply", *args(pack), *form)
        assert session.uncertainty["annotations"][0]["status"] == "resolved", shown(13)
        await send("prepare_archive_reference", *args(pack), *form)
        await send("score_archive_reference", pack, shown(0), shown(6), shown(7), shown(8), True,
            shown(12), "fit", shown(14), *form)
        assert json.loads(shown(11))["coverage"]["scored_pairs"] == 1
        assert session.uncertainty["journal"]["revisions"][:len(first_head["revisions"])] == first_head["revisions"]
        await send("save_archive_reviewed", *args(pack), *form)
        assert len(service.catalog()) == 3
    try:
        asyncio.run(run())
    finally:
        app.close()
