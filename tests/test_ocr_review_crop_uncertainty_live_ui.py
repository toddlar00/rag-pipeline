"""Live uncertainty callbacks over generated reports and explicit inert ports.

The coordinator's public v2 author/review methods, journal, raster declaration
validator and complete comparator are real. Retained-bundle IO, native preview
and publication are named doubles. Installed Gradio process_api/postprocessing
and queue capture are exercised, not a browser, OCR, native PDF, real storage,
human consent, transport ordering or eventual framework image disposal.
"""

import asyncio
import copy
import hashlib
import inspect
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace as NS

import pytest

import ocr_comparison
import ocr_crop_comparison as crops
import ocr_crop_raster_view as raster
import ocr_evaluation
import ocr_review_crop_uncertainty_live_ui as live
import ocr_review_execution as execution
from ocr_crop_review_runtime import CropRasterPreview
from ocr_review_execution_ui import _PanelState
from test_ocr_crop_comparison import _report
from test_ocr_review_execution_ui import capture as capture_queue


CONTROLS = ["pages", "current", [], 300, "none", 0, "none", 0.0, "none"]
FORM = ["add", None, "uncertain", "", "reading_confirmed", "", "explicit test reason"]
SKIP = {"__type__": "update"}


def raw(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


def sha(value):
    return hashlib.sha256(raw(value)).hexdigest()


def block(ui, name):
    return next(entry for entry in ui.app.fns.values() if entry.fn and entry.fn.__name__ == name)


class InertSavePort:
    """Commit-admission double, not proof of archive capture or filesystem IO."""

    def __init__(self):
        self.calls, self.commits = [], []
        self.hook = None

    def save_live_v2(self, coordinator, *ids, **kwargs):
        assert coordinator is self.ui.coordinator and ids == self.ui.ids
        self.calls.append(kwargs)
        assert kwargs["verify_current"]() is True
        if self.hook:
            self.hook()
        assert kwargs["verify_current"]() is True
        self.commits.append(copy.deepcopy({k: v for k, v in kwargs.items() if k != "verify_current"}))
        return {"pack_id": "c" * 32, "manifest_sha256": "d" * 64,
                "status": "verified_complete", "requires_attention": True}


@pytest.fixture
def ui(monkeypatch, tmp_path):
    monkeypatch.setenv("GRADIO_ANALYTICS_ENABLED", "False")
    gr = pytest.importorskip("gradio")
    image_module = pytest.importorskip("PIL.Image")
    reports = [_report("liable", bbox=[0., 0., 1., 1.]),
               _report("not liable", dpi=400, bbox=[0., 0., 1., 1.])]
    ids = ("a" * 32, "baseline", "b" * 32, "retry")
    scope = crops.crop_scope(reports[0], region_id="a")
    workspace = NS(document=NS(source_sha256=scope["source_sha256"], page_count=2,
        recovery_sha256=reports[0]["inputs"]["recovery_sha256"]))
    sides, results = [], {}
    for index, report in enumerate(reports):
        run, item = ids[index * 2:index * 2 + 2]
        row = report["regions"][0]
        pin = {"run_id": run, "item_id": item, "operation": "regions", "region_id": "a",
            "report_sha256": sha(report), "request_sha256": str(index + 1) * 64,
            "execution_sha256": str(index + 3) * 64, "disposition_sha256": str(index + 5) * 64}
        sides.append({**pin, "status": row["status"], "text": row["candidate"]["text"],
            "geometry": row["geometry"], "recipe": None, "configuration": report["retry_configuration"],
            "engine": row["candidate"]["engine"]})
        results[run] = {**pin, "source_sha256": workspace.document.source_sha256,
            "baseline_recovery_sha256": workspace.document.recovery_sha256, "requires_attention": True,
            "selected_item": {"diagnostic": {"item_id": item, "region_id": "a", "page_number": 1,
                "legacy_status": row["status"]}, "report_record": row}}
    pair = {"source_sha256": workspace.document.source_sha256,
        "baseline_recovery_sha256": workspace.document.recovery_sha256, "scope": scope,
        "baseline": sides[0], "retry": sides[1], "requires_attention": True,
        "reference_status": "not_supplied", "canonical_extraction_modified": False}
    pair["pair_sha256"] = hashlib.sha256(raw(pair) + b"\n").hexdigest()
    coordinator = object.__new__(execution.ReviewRunCoordinator)
    coordinator._lock = threading.Lock()
    coordinator._closed = coordinator._cleanup_uncertain = False
    coordinator._runs, coordinator._pending = {}, None
    service = InertSavePort()
    value = NS(gr=gr, images=image_module, workspace=workspace, coordinator=coordinator, service=service,
        reports=reports, pair=pair, ids=ids, owners=[], reads=[], rechecks=[], authors=[], scores=[],
        author_hook=None, score_hook=None, render_hook=None, mutate_score=None,
        profile="fit", reference="not liable", critical="not liable", form=FORM.copy(), editor=None)
    service.ui = value

    def snapshot(*selected):
        assert selected == ids
        value.reads.append(selected)
        return copy.deepcopy(value.pair), (), [{"report": copy.deepcopy(report)} for report in reports]

    # Inert retained-input seams. Public author/compare admission and rechecks
    # still execute; no fake report or substituted journal/metric validator.
    coordinator._crop_pair_snapshot = snapshot
    coordinator._recheck_crop_pair = lambda *_a: value.rechecks.append(True)
    coordinator.result = lambda run, *, item_id: copy.deepcopy(results[run])
    original_author = coordinator.author_crops_v2
    original_score = coordinator.compare_crops_v2

    def author(*args, **kwargs):
        value.authors.append(copy.deepcopy(kwargs))
        if value.author_hook:
            value.author_hook()
        return original_author(*args, **kwargs)

    def score(*args, **kwargs):
        value.scores.append(copy.deepcopy(kwargs))
        if value.score_hook:
            value.score_hook()
        result = original_score(*args, **kwargs)
        if value.mutate_score:
            value.mutate_score(result)
        return result

    def preview(selected_scope, *, preview_profile, cancel_requested):
        assert selected_scope == scope and cancel_requested() is False
        if value.render_hook:
            value.render_hook()
        scale = {"fit": 2., "dpi288": 4., "dpi576": 8.}[preview_profile]
        side = int(72 * scale)
        image = image_module.new("RGB", (side, side), (17, 29, 43))
        view = raster.build_raster_view(scope=scope, preview_profile=preview_profile,
            command_clip=[0., 0., 72., 72.], native_clip=[0., 0., 72., 72.],
            command_matrix=[scale, 0., 0., scale, 0., 0.], native_matrix=[scale, 0., 0., scale, 0., 0.],
            projected_rect=[0., 0., float(side), float(side)], pixel_rect=[0, 0, side, side],
            width=side, height=side, rgb_sha256=hashlib.sha256(image.tobytes()).hexdigest())
        owner = CropRasterPreview(image, view)
        value.owners.append(owner)
        return owner

    coordinator.author_crops_v2, coordinator.compare_crops_v2 = author, score
    coordinator.render_crop_preview_with_view = preview
    review = {"page": 1, "regions": [], "annotation_context": "fixed-annotation"}
    with gr.Blocks(analytics_enabled=False) as app:
        execution_state = gr.State(_PanelState())
        review_state = gr.State(review)
        annotation = gr.Textbox(value=review["annotation_context"])
        runs = gr.Dropdown([ids[0], ids[2]], value=ids[0])
        items = gr.Dropdown([ids[1], ids[3]], value=ids[1])
        context = gr.Textbox(value="result-A")
        controls = [gr.Dropdown([item], value=item) if type(item) is str else
                    gr.CheckboxGroup([], value=[]) if type(item) is list else
                    gr.Number(value=item) for item in CONTROLS]
        live.build_uncertainty_live_panel(workspace, coordinator, execution_state=execution_state,
            review_state=review_state, annotation_view=annotation, runs=runs, items=items,
            result_context=context, controls=controls, pack_service=service)
    value.app, value.review = app, review
    value.crop, value.execution = live._LiveState(), _PanelState()
    value.execution.runs = (ids[0], ids[2])
    value.token = value.crop.generation
    value.functions = {entry.fn.__name__: entry.fn for entry in app.fns.values() if entry.fn}
    value.api_state = app.state_holder["uncertainty-live"]
    monkeypatch.setattr(block(value, "load_crop_pair").outputs[3], "GRADIO_CACHE", str(tmp_path / "images"))
    yield value
    for owner in value.owners:
        owner.close()
    app.close()


def common(ui):
    return [ui.crop, ui.execution, ui.review, ui.review["annotation_context"], ui.token, ui.profile]


def accept(ui, result, kind):
    indices = {"full": (0, 14), "changed": (0, 6), "prepare": (5, 6), "score": (4, 5), "save": (3, 4)}
    token_index, editor_index = indices[kind]
    if type(result[token_index]) is str:
        ui.token = result[token_index]
    incoming = result[editor_index]
    if incoming != SKIP:
        ui.editor = copy.deepcopy(incoming)
    return result


def api_inputs(ui, entry, values):
    inputs = []
    for component, value in zip(entry.inputs, values, strict=True):
        if isinstance(component, ui.gr.State):
            ui.api_state[component._id] = value
            inputs.append(None)
        else:
            inputs.append(copy.deepcopy(value))
    return inputs


def drain_result(result, trace=None):
    """Consume one original callback iterator, never reinvoke the callback."""
    if not inspect.isgenerator(result):
        return result
    trace = [] if trace is None else trace
    start = len(trace)
    try:
        for _ in range(4):
            try:
                trace.append(next(result))
            except StopIteration:
                assert len(trace) > start, "Save did not yield a response"
                return trace[-1]
        pytest.fail("Save exceeded the bounded pending/terminal response contract")
    finally:
        result.close()


async def process_result(app, entry, inputs, *, state, session_hash, trace=None):
    """Follow the actual process_api iterator, retaining every full-value yield."""
    trace = [] if trace is None else trace
    iterator = None
    try:
        for _ in range(4):
            result = await app.process_api(entry, inputs, state=state,
                session_hash=session_hash, iterator=iterator, simple_format=True)
            if not result["is_generating"]:
                if iterator is not None:
                    # Gradio returns its cached final values, not FINISHED_ITERATING.
                    assert result["data"] == trace[-1]
                return result
            trace.append(result["data"])
            following = result["iterator"]
            assert following is not None
            if iterator is not None:
                assert following is iterator, "process_api replaced the original Save iterator"
            iterator = following
        pytest.fail("process_api Save exceeded its bounded response contract")
    except BaseException:
        if iterator is not None:
            # A first-yield postprocess fault exposes no iterator to this caller;
            # its dedicated control retains the original call_function result.
            try:
                await iterator.aclose(timeout=1)
            except BaseException:
                pass
        raise


def invoke(ui, name, values, kind, *, api=False):
    trace = []
    if api:
        entry = block(ui, name)
        inputs = api_inputs(ui, entry, values)
        result = asyncio.run(process_result(ui.app, entry, inputs, state=ui.api_state,
            session_hash="uncertainty-live", trace=trace))["data"]
    else:
        result = drain_result(ui.functions[name](*values), trace)
    if kind == "save":
        ui.save_trace = trace
    return accept(ui, result, kind)


def capture(ui, *, api=False):
    for index, name in enumerate(("capture_baseline_crop", "capture_retry_crop")):
        run, item = ui.ids[index * 2:index * 2 + 2]
        context = f"result-{index}"
        ui.execution.display_run, ui.execution.result_generation = run, context
        invoke(ui, "crop_select_item", [ui.crop, ui.execution, run, item, context], "full", api=api)
        invoke(ui, name, [ui.crop, ui.execution, run, item, context, ui.token], "full", api=api)


def load(ui, *, api=False, reload=False):
    return invoke(ui, "reload_crop_detail" if reload else "load_crop_pair",
        [*common(ui), "" if not reload else ui.reference, "" if not reload else ui.critical, *CONTROLS], "full", api=api)


def loaded(ui, *, api=False):
    capture(ui, api=api)
    result = load(ui, api=api)
    assert ui.crop.loaded is not None and ui.editor["image_token"] == ui.crop.image_token
    assert ui.crop.pending is None and ui.crop.last_score is None
    return result


def command(ui, kind, *, mode=None, bbox=None, ordinal=None, api=False):
    captured = copy.deepcopy(ui.editor)
    captured["command"] = {"kind": kind, "mode": mode, "pixel_bbox": bbox, "ordinal": ordinal}
    return invoke(ui, "crop_uncertainty_editor_input", [ui.crop, ui.token, captured], "changed", api=api)


def apply(ui, *, api=False):
    return invoke(ui, "apply_crop_uncertainty", [*common(ui), ui.reference, ui.critical,
        ui.editor, *ui.form, *CONTROLS], "changed", api=api)


def prepare(ui, *, api=False):
    return invoke(ui, "prepare_crop_reference", [*common(ui), ui.reference, ui.critical,
        ui.editor, *ui.form, *CONTROLS], "prepare", api=api)


def score_values(ui, ticket, confirmed=True):
    return [*common(ui), ui.reference, ui.critical, ticket, confirmed, ui.editor, *ui.form, *CONTROLS]


def score(ui, ticket, *, api=False, confirmed=True):
    return invoke(ui, "score_crop_reference", score_values(ui, ticket, confirmed), "score", api=api)


def save_values(ui):
    return [*common(ui), ui.reference, ui.critical, ui.editor, *ui.form, *CONTROLS]


def save(ui, *, reviewed=True, api=False):
    return invoke(ui, "save_crop_reviewed" if reviewed else "save_crop_draft", save_values(ui), "save", api=api)


def add_uncertainty(ui, *, api=False):
    command(ui, "mode", mode="annotate", api=api)
    command(ui, "rectangle", bbox=[0, 0, 36, 36], api=api)
    result = apply(ui, api=api)
    assert len(ui.crop.uncertainty["annotations"]) == 1, result
    assert ui.crop.uncertainty["annotations"][0]["status"] == "unresolved"
    return result


def resolve(ui, *, api=False):
    ui.form = ["resolve", "a000001", "uncertain", "", "reading_confirmed", "not liable", "reading inspected"]
    result = apply(ui, api=api)
    assert ui.crop.uncertainty["annotations"][0]["status"] == "resolved", result
    return result


def forbid_scorers(monkeypatch):
    def forbidden(*_a, **_k):
        pytest.fail("unresolved or author-only operation invoked a scorer")
    monkeypatch.setattr(crops, "compare_ocr", forbidden)
    monkeypatch.setattr(ocr_comparison, "compare_ocr", forbidden)
    monkeypatch.setattr(ocr_evaluation, "evaluate_ocr", forbidden)


@pytest.mark.parametrize("api", [False, True], ids=["direct", "installed-process-api"])
def test_complete_unresolved_review_save_then_resolve_and_score(ui, monkeypatch, api):
    original_reports = copy.deepcopy(ui.reports)
    loaded(ui, api=api)
    with monkeypatch.context() as guard:
        forbid_scorers(guard)
        add_uncertainty(ui, api=api)
        authored = copy.deepcopy(ui.crop.uncertainty["journal"])
        prepared = prepare(ui, api=api)
        assert prepared[0] and prepared[1] is False
        assert json.loads(prepared[2])["policy"]["scorable"] is False
        assert not ui.scores
        result = score(ui, prepared[0], api=api)
        comparison = json.loads(result[2])
        assert comparison["reason"] == "reference_uncertain" and comparison["comparison"] is None
        assert comparison["coverage"]["scored_pairs"] == 0
        assert comparison["uncertainty"]["unresolved_uncertain"] == 1
        assert comparison["uncertainty"]["unresolved_illegible"] == 0
        assert result[:2] == (["", False] if api else ("", False))
        saved = save(ui, api=api)
        assert "Saved a new private reviewed" in saved[2]
        assert ui.service.commits[-1]["reviewed"]["comparison"] == comparison
        assert ui.service.commits[-1]["uncertainty"]["journal"] == authored
        resolve(ui, api=api)
    prepared = prepare(ui, api=api)
    result = score(ui, prepared[0], api=api)
    comparison = json.loads(result[2])
    assert comparison["coverage"]["scored_pairs"] == 1 and comparison["comparison"] is not None
    assert comparison["uncertainty"]["resolved"] == 1
    assert ui.crop.uncertainty["journal"]["revisions"][:len(authored["revisions"])] == authored["revisions"]
    assert ui.reports == original_reports and ui.coordinator._runs == {}
    assert len(ui.scores) == 2 and ui.rechecks


def test_pending_selection_blocks_prepare_without_author_or_score(ui):
    loaded(ui)
    command(ui, "mode", mode="annotate")
    command(ui, "whole_scope")
    result = prepare(ui)
    assert result[:4] == ("", False, "", "") and "pending selected region" in result[4]
    assert not ui.authors and not ui.scores and ui.crop.pending is None
    command(ui, "cancel")
    assert prepare(ui)[0]


def test_observed_dirty_aba_reopens_and_detail_reload_retains_complete_lineage(ui):
    loaded(ui)
    add_uncertainty(ui)
    resolve(ui)
    score(ui, prepare(ui)[0])
    history_before = copy.deepcopy(ui.crop.uncertainty["journal"])
    for text in ("not liable!", "not liable"):
        result = invoke(ui, "crop_reference_edited", [ui.crop, ui.token, text, ui.critical, ui.editor], "changed")
        assert result[1:5] == ("", False, "", "")
    assert ui.crop.uncertainty["dirty"] is True
    assert ui.crop.uncertainty["annotations"][0]["status"] == "unresolved"
    assert ui.crop.uncertainty["journal"] == history_before
    assert ui.crop.last_score is None
    invoke(ui, "crop_preview_profile_changed", [ui.crop, ui.token, "dpi288"], "full")
    ui.profile = "dpi288"
    result = load(ui, reload=True)
    assert result[7:9] == (SKIP, SKIP)
    assert ui.crop.loaded["raster_view"]["width"] == 288
    assert ui.crop.uncertainty["journal"] == history_before and ui.crop.uncertainty["dirty"]
    prepared = prepare(ui)
    assert prepared[0] and ui.crop.uncertainty["dirty"] is False
    revisions = ui.crop.uncertainty["journal"]["revisions"]
    assert revisions[:-1] == history_before["revisions"] and revisions[-1]["reset_anchors"] is True
    assert json.loads(prepared[2])["policy"]["scorable"] is False


def test_partial_raw_draft_save_never_inherits_reviewed_authority(ui):
    loaded(ui)
    add_uncertainty(ui)
    score(ui, prepare(ui)[0])
    retained = copy.deepcopy(ui.crop.uncertainty["journal"])
    ui.critical = "not liable\n"
    result = save(ui, reviewed=False)
    assert "Saved a new private draft" in result[2]
    committed = ui.service.commits[0]
    assert committed["critical_tokens_text"] == ui.critical and committed["reviewed"] is None
    assert committed["uncertainty"]["dirty"] is True and committed["uncertainty"]["journal"] == retained
    assert ui.crop.last_score is None


@pytest.mark.parametrize("field", range(7), ids=["action", "annotation", "kind", "tentative", "decision", "reading", "reason"])
def test_undelivered_form_change_cannot_use_prepared_ticket(ui, field):
    loaded(ui)
    ticket = prepare(ui)[0]
    ui.form[field] = ["dismiss", "a000001", "illegible", "changed", "not_text", "changed", "another reason"][field]
    result = score(ui, ticket)
    assert result[:3] == ("", False, "") and ui.crop.last_score is None
    assert not ui.scores


@pytest.mark.parametrize("field", range(7), ids=["action", "annotation", "kind", "tentative", "decision", "reading", "reason"])
def test_undelivered_form_change_cannot_save_previous_review(ui, field):
    loaded(ui)
    score(ui, prepare(ui)[0])
    ui.form[field] = ["dismiss", "a000001", "illegible", "changed", "not_text", "changed", "another reason"][field]
    assert "unconfirmed" in save(ui)[2]
    assert not ui.service.calls and ui.crop.last_score is None


@pytest.mark.parametrize("confirmed", [False, None, 1, "true"], ids=["false", "null", "integer", "string"])
def test_confirmation_is_exact_and_wrong_ticket_does_not_consume_current(ui, confirmed):
    loaded(ui)
    ticket = prepare(ui)[0]
    pending = ui.crop.pending
    with pytest.raises(ui.gr.Error, match="Crop view changed"):
        score(ui, ticket, confirmed=confirmed)
    assert ui.crop.pending is pending and not ui.scores


@pytest.mark.parametrize("which", ["edit", "fields", "editor", "prepare", "score", "save", "apply"])
def test_stale_malformed_inputs_are_refused_before_parse_and_preserve_new_authority(ui, which):
    loaded(ui)
    old_token, old_editor = ui.token, copy.deepcopy(ui.editor)
    ticket = prepare(ui)[0]
    score(ui, ticket)
    authority = ui.crop.last_score
    if which in ("edit", "fields", "editor"):
        name, values = {
            "edit": ("crop_reference_edited", [ui.crop, old_token, object(), object(), old_editor]),
            "fields": ("crop_uncertainty_fields_edited", [ui.crop, old_token, old_editor, object()]),
            "editor": ("crop_uncertainty_editor_input", [ui.crop, old_token, {"command": object()}]),
        }[which]
        assert ui.functions[name](*values) == (SKIP,) * 9
    else:
        base = [ui.crop, ui.execution, ui.review, ui.review["annotation_context"], old_token, object(), object(), object()]
        names = {"prepare": "prepare_crop_reference", "score": "score_crop_reference",
                 "save": "save_crop_reviewed", "apply": "apply_crop_uncertainty"}
        tail = [ticket, True, old_editor] if which == "score" else [old_editor]
        if which == "save":
            result = drain_result(ui.functions[names[which]](*base, *tail, *FORM, *CONTROLS))
            assert "unconfirmed" in result[2]
            assert result[:2] + result[3:] == (SKIP,) * 6
        else:
            with pytest.raises(ui.gr.Error, match="Crop view changed"):
                ui.functions[names[which]](*base, *tail, *FORM, *CONTROLS)
    assert ui.crop.last_score is authority and len(ui.scores) == 1 and not ui.service.calls


@pytest.mark.parametrize("path", [
    ("pair_sha256",), ("reference", "scope", "page_number"),
    ("comparison", "scope", "page_number"), ("comparison", "source_sha256"),
    ("comparison", "reference_id"), ("comparison", "reference_sha256"),
    ("comparison", "pair", "baseline", "report_sha256"),
    ("comparison", "pair", "retry", "region_id"), ("comparison", "schema_version"),
    ("comparison", "canonical_extraction_modified"),
], ids=["pair", "reference-scope", "metric-scope", "source", "reference-id", "reference-sha",
        "baseline-report", "retry-region", "schema", "canonical-flag"])
def test_wrong_returned_comparison_binding_cannot_be_displayed_or_saved(ui, path):
    loaded(ui)
    ticket = prepare(ui)[0]

    def change(result):
        node = result
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = True if path[-1] == "canonical_extraction_modified" else 1 if path[-1] == "schema_version" else "wrong"

    ui.mutate_score = change
    result = score(ui, ticket)
    assert result[:3] == ("", False, "") and ui.crop.last_score is None
    assert len(ui.scores) == 1 and not ui.service.calls


@pytest.mark.parametrize("stage", ["author", "score", "save"])
def test_cancel_while_external_callback_runs_has_no_lock_inversion_or_late_authority(ui, stage):
    loaded(ui)
    ticket = prepare(ui)[0] if stage != "author" else None
    if stage == "save":
        score(ui, ticket)
    entered, release = threading.Event(), threading.Event()

    def barrier():
        entered.set()
        assert release.wait(5), "callback failed to release outside session lock"

    if stage == "save":
        ui.service.hook = barrier
    else:
        setattr(ui, stage + "_hook", barrier)
    function = (lambda: prepare(ui)) if stage == "author" else (lambda: score(ui, ticket)) if stage == "score" else (lambda: save(ui))
    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(function)
        assert entered.wait(5)
        cancellation = pool.submit(ui.functions["cancel_crop_preview"], ui.crop, ui.profile)
        try:
            cancelled = cancellation.result(timeout=2)
        finally:
            release.set()
        assert cancelled[9:13] == ("", False, "", "")
        if stage == "save":
            assert "unconfirmed" in future.result(timeout=5)[2]
        else:
            with pytest.raises(ui.gr.Error, match="Crop view changed"):
                future.result(timeout=5)
    assert ui.crop.loaded is None and ui.crop.image_token is None
    assert ui.crop.pending is None and ui.crop.last_score is None and not ui.service.commits


def test_actual_postprocess_failure_cannot_mint_unseen_image_authority(ui, monkeypatch):
    capture(ui, api=True)
    old_token, old_editor = ui.token, ui.editor
    original = ui.gr.Image.postprocess

    def fail_image(*_a, **_k):
        raise RuntimeError("inert image postprocess fault")

    monkeypatch.setattr(ui.gr.Image, "postprocess", fail_image)
    with pytest.raises(Exception, match="inert image postprocess fault"):
        load(ui, api=True)
    assert ui.crop.loaded is not None and ui.crop.image_seen is False
    assert ui.token == old_token and ui.editor is old_editor is None
    assert ui.functions["crop_reference_edited"](ui.crop, old_token, ui.reference, ui.critical, old_editor) == (SKIP,) * 9
    with pytest.raises(ui.gr.Error, match="Crop view changed"):
        prepare(ui, api=True)
    assert not ui.authors and not ui.scores
    monkeypatch.setattr(ui.gr.Image, "postprocess", original)
    invoke(ui, "cancel_crop_preview", [ui.crop, ui.profile], "full", api=True)
    load(ui, api=True)
    assert prepare(ui, api=True)[0] and ui.crop.image_seen


@pytest.mark.parametrize("old_action", ["score", "save"], ids=["queued-old-score", "queued-old-save"])
def test_actual_queue_same_image_same_text_old_action_preserves_new_score(ui, old_action):
    loaded(ui, api=True)
    image_token = ui.crop.image_token
    ticket = prepare(ui, api=True)[0]
    if old_action == "save":
        score(ui, ticket, api=True)
    name = "score_crop_reference" if old_action == "score" else "save_crop_reviewed"
    entry = block(ui, name)
    captured = api_inputs(ui, entry, score_values(ui, ticket) if old_action == "score" else save_values(ui))

    async def scenario():
        event = await capture_queue(ui, entry, captured, "uncertainty-live")
        assert event.data.data == captured
        score(ui, prepare(ui)[0])  # Same loaded image and raw form; no load/helper recapture.
        authority = ui.crop.last_score
        if old_action == "save":
            trace = []
            result = await process_result(ui.app, event.fn, event.data.data,
                state=ui.api_state, session_hash="uncertainty-live", trace=trace)
            assert len(trace) == 1 and "unconfirmed" in result["data"][2]
        else:
            with pytest.raises(Exception, match="Crop view changed"):
                await ui.app.process_api(event.fn, event.data.data, state=ui.api_state, session_hash="uncertainty-live")
        assert ui.crop.last_score is authority and ui.crop.image_token == image_token
        assert not ui.service.calls

    asyncio.run(scenario())


def test_private_wiring_caption_and_new_session_have_no_restored_authority(ui):
    assert all(dep["api_visibility"] == "private" for dep in ui.app.config["dependencies"])
    for name in ("crop_reference_edited", "crop_uncertainty_fields_edited", "crop_uncertainty_editor_input"):
        entries = [entry for entry in ui.app.fns.values() if entry.fn and entry.fn.__name__ == name]
        assert entries and all(entry.queue is False and entry.trigger_mode == "multiple" for entry in entries)
    for name in ("prepare_crop_reference", "score_crop_reference", "save_crop_reviewed"):
        assert all(not isinstance(component, ui.gr.State) for component in block(ui, name).inputs[3:])
    image = block(ui, "load_crop_pair").outputs[3]
    assert image.show_label is False and image.interactive is False and image.sources == [] and image.buttons == []
    loaded(ui)
    score(ui, prepare(ui)[0])
    fresh = copy.deepcopy(ui.crop)
    assert fresh.loaded is fresh.pending is fresh.last_score is fresh.image_token is None
    assert fresh.uncertainty == {"journal": None, "annotations": [], "dirty": False}
    assert fresh.generation != ui.crop.generation and fresh.lineage is None
