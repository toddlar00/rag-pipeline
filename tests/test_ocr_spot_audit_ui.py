"""Generated in-memory Gradio callbacks/queue; no host, PDF parser or OCR runs."""
import asyncio
import copy
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

import ocr_spot_audit as audit
import ocr_spot_audit_ui as panel
from ocr_review import ReviewDocument
from test_ocr_review import report


def document():
    value = report()
    original = value["pages"][0]
    value["page_count"] = 3
    value["selection"]["requested_pages"] = [1, 2, 3]
    value["pages"] = []
    for number in (1, 2, 3):
        row = copy.deepcopy(original)
        row["page_number"] = number
        row["candidate"]["mean_confidence"] = .99
        for line in row["candidate"]["lines"]:
            line["score"] = .99
        value["pages"].append(row)
    value["summary"].update(inspected=3, selected=3, review_required=3)
    return ReviewDocument(value, recovery_sha256="b" * 64)


@pytest.fixture
def factory(monkeypatch):
    monkeypatch.setenv("GRADIO_ANALYTICS_ENABLED", "False")
    gr = pytest.importorskip("gradio")
    Image = pytest.importorskip("PIL.Image")
    apps, allocated = [], []

    def make(initial=None, parent=None):
        doc = document()
        calls, saved, hooks = [], [], {}

        def verify():
            calls.append("verify")
            if "verify" in hooks:
                hooks["verify"]()

        def render(number):
            calls.append(("render", number))
            if "render" in hooks:
                return hooks["render"](number)
            result = Image.new("RGB", (40, 60), "white")
            allocated.append(result)
            return result

        def save(kind, payload):
            calls.append("save")
            if "save" in hooks:
                hooks["save"]()
            saved.append((kind, copy.deepcopy(payload)))
            return Path("C:/synthetic-private") / f"audit-{len(saved)}.json"

        workspace = NS(document=doc, initial_audit=initial, audit_sha256=parent,
                       verify_inputs=verify, render_original_page=render, save=save)
        with gr.Blocks(analytics_enabled=False) as app:
            panel.build_spot_audit_panel(workspace)
        apps.append(app)
        functions = {fn.fn.__name__: fn for fn in app.fns.values() if fn.fn is not None}
        event = functions["create_spot_audit"]
        outputs = event.outputs
        values = {component._id: component.value for component in outputs}
        for fn in functions.values():
            for component in fn.inputs:
                values.setdefault(component._id, component.value)
        return NS(app=app, functions=functions, state=panel._AuditState(), values=values, calls=calls,
                  saved=saved, hooks=hooks, workspace=workspace, outputs=outputs, gr=gr, Image=Image)

    yield make
    for app in apps:
        app.close()
    for image in allocated:
        image.close()


@pytest.fixture
def ui(factory):
    return factory()


def set_value(ui, label, value):
    matches = [component for component in ui.app.blocks.values() if getattr(component, "label", None) == label]
    assert len(matches) == 1
    ui.values[matches[0]._id] = value


def get(ui, index):
    return ui.values[ui.outputs[index]._id]


def apply(ui, result):
    for component, value in result.items():
        if type(component).__name__ == "State":
            continue
        if isinstance(value, dict) and value.get("__type__") == "update":
            if "value" not in value:
                continue
            value = value["value"]
        ui.values[component._id] = value
    return result


def captured(ui, name):
    return [None if type(component).__name__ == "State" else ui.values[component._id]
            for component in ui.functions[name].inputs]


def invoke(ui, name, payload=None):
    args = captured(ui, name) if payload is None else payload
    return apply(ui, ui.functions[name].fn(ui.state, *args[1:]))


def initialize(ui):
    invoke(ui, "create_spot_audit")
    assert ui.state.snapshot is not None
    return ui.state.snapshot["plan"]["selected_pages"]


def select(ui, number):
    ui.values[ui.outputs[5]._id] = number
    return invoke(ui, "select_spot_page")


def opened(ui):
    numbers = initialize(ui)
    select(ui, numbers[0])
    invoke(ui, "open_spot_page")
    assert get(ui, 1) is not None and get(ui, 4)
    return numbers


def fields(ui, *, text="Source reference", note="", outcome="reviewed"):
    for index, value in ((6, text), (7, note), (8, outcome)):
        ui.values[ui.outputs[index]._id] = value
    return invoke(ui, "edit_spot_fields")


def confirm(ui):
    ui.values[ui.outputs[9]._id] = True
    return invoke(ui, "confirm_spot_review")


def reviewed(ui):
    opened(ui)
    fields(ui, text=ui.workspace.document.page(ui.state.page)["candidate"]["text"])
    confirm(ui)
    invoke(ui, "store_spot_outcome")
    assert any(row["outcome"] == "reviewed" for row in ui.state.snapshot["records"])


def state_pin(ui):
    return (copy.deepcopy(ui.state.snapshot), ui.state.context, ui.state.page, ui.state.image_token,
            ui.state.failed_open, ui.state.confirmed_digest, ui.state.fields_digest)


def test_private_serialized_wiring_and_no_candidate_in_initial_config(ui):
    deps = ui.app.config["dependencies"]
    assert all(row["api_visibility"] == "private" for row in deps)
    assert all(fn.concurrency_limit == 1 and fn.queue is True for fn in ui.functions.values())
    assert len({fn.concurrency_id for fn in ui.functions.values()}) == 1
    assert get(ui, 10) == "" and not ui.calls
    assert all(type(ui.outputs[n]).__name__ != "State" for n in (2, 3, 4))
    assert ui.functions["edit_spot_fields"].targets[0][1] == "change"
    assert ui.outputs[1].sources == [] and ui.outputs[1].buttons == []


def test_fixed_sample_no_resampling_and_fresh_session_seed(factory):
    first, second = factory(), factory()
    initialize(first)
    original = state_pin(first)
    set_value(first, "Requested sample size", 1)
    invoke(first, "create_spot_audit")
    assert state_pin(first) == original
    initialize(second)
    assert first.state.snapshot["plan"]["seed"] != second.state.snapshot["plan"]["seed"]
    assert copy.deepcopy(first.state).snapshot is None


@pytest.mark.parametrize("minimum", [0, 1, .95])
def test_threshold_integer_endpoints_from_number_component(ui, minimum):
    set_value(ui, "Minimum mean confidence", minimum)
    initialize(ui)
    assert type(ui.state.snapshot["plan"]["threshold"]) is float
    assert get(ui, 12)["sampled"] == (0 if minimum == 1 else 3)


@pytest.mark.parametrize("label,value", [("Minimum mean confidence", v) for v in (True, None, -.1, 1.1, float("nan"), float("inf"))]
                         + [("Requested sample size", v) for v in (True, 0, 101, 1.5)])
def test_bad_sample_settings_do_not_create(ui, label, value):
    set_value(ui, label, value)
    invoke(ui, "create_spot_audit")
    assert ui.state.snapshot is None and not ui.saved


def test_source_first_reveal_and_only_explicit_score_calls_scorer(ui, monkeypatch):
    scores = []
    original = audit.audit_summary
    monkeypatch.setattr(audit, "audit_summary", lambda *args, **kw: (scores.append(1), original(*args, **kw))[1])
    opened(ui)
    assert get(ui, 10) == "" and not scores
    fields(ui, text=ui.workspace.document.page(ui.state.page)["candidate"]["text"])
    confirm(ui)
    invoke(ui, "store_spot_outcome")
    assert get(ui, 10) and not scores and get(ui, 13) is None
    invoke(ui, "save_spot_audit")
    assert not scores and ui.saved and get(ui, 14).endswith("audit-1.json")
    invoke(ui, "score_spot_audit")
    assert scores == [1] and get(ui, 13)["counts"]["reviewed"] == 1
    assert get(ui, 13)["counts"]["pending"] == 2


@pytest.mark.parametrize("index,value", [(6, "edited"), (7, "new note"), (8, "unresolved")])
def test_every_field_edit_invalidates_confirmation_and_metrics_without_adopting_draft(ui, index, value):
    opened(ui)
    fields(ui)
    confirm(ui)
    prior = copy.deepcopy(ui.state.snapshot)
    token = ui.state.context
    ui.values[ui.outputs[index]._id] = value
    invoke(ui, "edit_spot_fields")
    assert ui.state.snapshot == prior and ui.state.context != token
    assert ui.state.confirmed_digest is None and get(ui, 9) is False and get(ui, 13) is None
    assert get(ui, 10) == ""


def test_duplicate_programmatic_field_changes_preserve_new_confirmation(ui):
    opened(ui)
    fields(ui)
    confirm(ui)
    before = state_pin(ui)
    invoke(ui, "edit_spot_fields")
    assert state_pin(ui) == before and get(ui, 9) is True


@pytest.mark.parametrize("mutation", ["no_open", "checkbox_only", "changed_text", "wrong_image", "wrong_page", "wrong_context"])
def test_forged_review_context_never_records(ui, mutation):
    if mutation == "no_open":
        select(ui, initialize(ui)[0])
    else:
        opened(ui)
    fields(ui)
    if mutation not in {"checkbox_only", "no_open"}:
        confirm(ui)
    else:
        ui.values[ui.outputs[9]._id] = True
    args = captured(ui, "store_spot_outcome")
    if mutation == "changed_text":
        args[5] = "different from confirmation"
    if mutation == "wrong_image":
        args[4] = "0" * 64
    if mutation == "wrong_page":
        args[2] = 100
    if mutation == "wrong_context":
        args[3] = "0" * 64
    invoke(ui, "store_spot_outcome", args)
    assert all(row["outcome"] == "pending" for row in ui.state.snapshot["records"])
    assert not ui.saved and get(ui, 10) == ""


def test_navigation_captures_draft_but_late_old_page_edit_cannot_move_it(ui):
    pages = opened(ui)
    fields(ui, text="unfinished\n  raw text")
    old = captured(ui, "edit_spot_fields")
    select(ui, pages[1])
    original = state_pin(ui)
    invoke(ui, "edit_spot_fields", old)
    assert state_pin(ui) == original
    assert next(row["text"] for row in ui.state.snapshot["drafts"] if row["page_number"] == pages[0]) == "unfinished\n  raw text"
    assert get(ui, 1) is None and get(ui, 4) == "" and get(ui, 10) == ""
    select(ui, pages[0])
    assert get(ui, 6) == "unfinished\n  raw text" and get(ui, 9) is False


@pytest.mark.parametrize("choice", ["unresolved", "unavailable"])
def test_non_scoreable_outcomes_require_note_and_correct_preview_state(ui, choice):
    if choice == "unavailable":
        select(ui, initialize(ui)[0])
        ui.hooks["render"] = lambda _: (_ for _ in ()).throw(ValueError("private render detail"))
        invoke(ui, "open_spot_page")
    else:
        opened(ui)
    fields(ui, text="unfinished", note="", outcome=choice)
    invoke(ui, "store_spot_outcome")
    assert all(row["outcome"] == "pending" for row in ui.state.snapshot["records"])
    fields(ui, text="unfinished", note="Cannot read this page", outcome=choice)
    invoke(ui, "store_spot_outcome")
    record = next(row for row in ui.state.snapshot["records"] if row["page_number"] == ui.state.page)
    assert record["outcome"] == choice and record["text"] is None
    invoke(ui, "score_spot_audit")
    assert get(ui, 13)["metrics"] is None and get(ui, 13)["counts"][choice] == 1


@pytest.mark.parametrize("phase", ["before_open", "after_render", "failed_render"])
def test_changed_fixed_inputs_never_enable_unavailable_or_save(ui, phase):
    select(ui, initialize(ui)[0])
    if phase == "before_open":
        ui.hooks["verify"] = lambda: (_ for _ in ()).throw(ValueError("private source"))
    else:
        def render(_):
            ui.hooks["verify"] = lambda: (_ for _ in ()).throw(ValueError("private source"))
            if phase == "failed_render":
                raise ValueError("private render")
            return ui.Image.new("RGB", (10, 10))
        ui.hooks["render"] = render
    invoke(ui, "open_spot_page")
    assert ui.state.failed_open is None and get(ui, 1) is None and get(ui, 4) == ""
    fields(ui, note="unavailable", outcome="unavailable")
    invoke(ui, "store_spot_outcome")
    invoke(ui, "save_spot_audit")
    assert not ui.saved and all(row["outcome"] == "pending" for row in ui.state.snapshot["records"])
    assert "private source" not in get(ui, 15)


def test_restore_retains_historical_records_and_drafts_not_approval_or_candidate(factory, monkeypatch):
    source = factory()
    reviewed(source)
    fields(source, text="unfinished next revision")
    invoke(source, "save_spot_audit")
    initial = copy.deepcopy(source.saved[-1][1])
    initial["parent_audit_sha256"] = "c" * 64
    monkeypatch.setattr(audit, "audit_summary", lambda *_: pytest.fail("restore must not score"))
    restored = factory(initial, "c" * 64)
    initialize(restored)
    assert restored.state.snapshot == initial and restored.state.image_token == ""
    assert get(restored, 1) is None and get(restored, 9) is False and get(restored, 10) == "" and get(restored, 13) is None
    select(restored, source.state.page)
    assert get(restored, 6) == "unfinished next revision"
    confirm(restored)
    assert restored.state.confirmed_digest is None


def test_wrong_restore_ancestry_refused_without_resampling(factory):
    initial = audit.create_audit(document(), threshold=.95, sample_size=2, seed="a" * 64)
    ui = factory(initial, "b" * 64)
    invoke(ui, "create_spot_audit")
    assert ui.state.snapshot is None and not ui.saved


@pytest.mark.parametrize("failure", [ValueError, RuntimeError])
def test_score_failure_retains_reviews_without_zero_substitution(ui, monkeypatch, failure):
    reviewed(ui)
    before = copy.deepcopy(ui.state.snapshot)
    monkeypatch.setattr(audit, "audit_summary", lambda *_: (_ for _ in ()).throw(failure("private detail")))
    invoke(ui, "score_spot_audit")
    assert ui.state.snapshot == before and get(ui, 13) is None
    assert "Scoring unavailable" in get(ui, 15) and "private detail" not in get(ui, 15)


def test_save_failure_clears_prior_path_and_preserves_raw_draft(ui):
    opened(ui)
    fields(ui, text="draft raw\n")
    invoke(ui, "save_spot_audit")
    assert get(ui, 14)
    ui.hooks["save"] = lambda: (_ for _ in ()).throw(ValueError("private destination"))
    invoke(ui, "save_spot_audit")
    assert get(ui, 14) == "" and get(ui, 6) == "draft raw\n" and len(ui.saved) == 1
    assert "may exist" in get(ui, 15)


@pytest.mark.parametrize("failure", [RuntimeError("lock finalizer"), KeyboardInterrupt(), SystemExit(7)])
def test_open_lock_finalizer_failure_closes_untransferred_image_once(ui, failure):
    select(ui, initialize(ui)[0])
    image = ui.Image.new("RGB", (10, 10))
    closes = []
    image.close = lambda: closes.append(1)
    ui.hooks["render"] = lambda _: image

    class BrokenLock:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            raise failure

    ui.state.lock = BrokenLock()
    with pytest.raises(type(failure)) as caught:
        invoke(ui, "open_spot_page")
    assert caught.value is failure and closes == [1]


def test_successful_open_image_is_not_closed_and_reset_never_reissues_it(ui):
    select(ui, initialize(ui)[0])
    image = ui.Image.new("RGB", (10, 10))
    closes = []
    image.close = lambda: closes.append(1)
    ui.hooks["render"] = lambda _: image
    invoke(ui, "open_spot_page")
    assert get(ui, 1) is image and not closes
    fields(ui, text="keep partial")
    invoke(ui, "reset_spot_view")
    assert get(ui, 1) is None and get(ui, 4) == "" and get(ui, 6) == "keep partial"


async def api(ui, name, payload=None, *, session_hash="spot-audit"):
    event = ui.functions[name]
    session = ui.app.state_holder[session_hash]
    session[event.inputs[0]._id] = ui.state
    result = await ui.app.process_api(event, payload or captured(ui, name), state=session, session_hash=session_hash)
    apply(ui, dict(zip(event.outputs, result["data"])))
    return result


def test_real_process_api_image_failure_then_edit_confirm_cannot_mint_delivery(ui, monkeypatch):
    from gradio.exceptions import ComponentProcessingError

    original = ui.gr.Image.postprocess
    failure = RuntimeError("inert postprocess failure")

    def fail(component, value):
        if value is not None:
            raise failure
        return original(component, value)

    async def scenario():
        await api(ui, "create_spot_audit")
        ui.values[ui.outputs[5]._id] = ui.state.snapshot["plan"]["selected_pages"][0]
        await api(ui, "select_spot_page")
        old_context = get(ui, 2)
        monkeypatch.setattr(ui.gr.Image, "postprocess", fail)
        with pytest.raises(ComponentProcessingError, match="inert postprocess failure") as caught:
            await api(ui, "open_spot_page")
        assert caught.value.__cause__ is failure
        assert ui.state.image_token and get(ui, 4) == "" and get(ui, 2) == old_context
        ui.values[ui.outputs[6]._id] = "typed after failed image"
        await api(ui, "edit_spot_fields")
        ui.values[ui.outputs[9]._id] = True
        await api(ui, "confirm_spot_review")
        assert ui.state.confirmed_digest is None and get(ui, 4) == ""
        await api(ui, "reset_spot_view")
        await api(ui, "edit_spot_fields")
        ui.values[ui.outputs[9]._id] = True
        await api(ui, "confirm_spot_review")
        assert ui.state.confirmed_digest is None and get(ui, 4) == "" and get(ui, 6) == "typed after failed image"
        monkeypatch.setattr(ui.gr.Image, "postprocess", original)
        await api(ui, "open_spot_page")
        assert get(ui, 4) and get(ui, 1)["path"]

    asyncio.run(scenario())


@pytest.mark.parametrize("name", ["store_spot_outcome", "save_spot_audit", "score_spot_audit"])
def test_actual_queue_stale_action_cannot_mutate_or_score_new_page(ui, monkeypatch, name):
    from fastapi import Request
    from gradio.data_classes import PredictBodyInternal

    monkeypatch.setattr(audit, "audit_summary", lambda *_: pytest.fail("stale Score reached scorer"))

    async def scenario():
        await api(ui, "create_spot_audit")
        pages = ui.state.snapshot["plan"]["selected_pages"]
        ui.values[ui.outputs[5]._id] = pages[0]
        await api(ui, "select_spot_page")
        await api(ui, "open_spot_page")
        ui.values[ui.outputs[6]._id] = "Source reference"
        await api(ui, "edit_spot_fields")
        ui.values[ui.outputs[9]._id] = True
        await api(ui, "confirm_spot_review")
        event = ui.functions[name]
        data = captured(ui, name)
        request = Request({"type": "http", "method": "POST", "path": "/gradio_api/queue/join",
                           "root_path": "", "headers": [], "query_string": b"", "scheme": "http",
                           "server": ("127.0.0.1", 7860), "client": ("127.0.0.1", 1)})
        index = next(index for index, fn in ui.app.fns.items() if fn is event)
        accepted, identifier, status = await ui.app._queue.push(PredictBodyInternal(
            data=copy.deepcopy(data), fn_index=index, session_hash="spot-audit", request=request), request, None)
        assert accepted and status == "success"
        queued = ui.app._queue.event_ids_to_events[identifier]
        assert queued.data.data == data
        ui.values[ui.outputs[5]._id] = pages[1]
        await api(ui, "select_spot_page")
        before = state_pin(ui)
        await api(ui, name, queued.data.data)
        assert state_pin(ui) == before and not ui.saved

    asyncio.run(scenario())
