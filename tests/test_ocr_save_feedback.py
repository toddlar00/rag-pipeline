"""Save feedback is presentation, not publication or new review authority.

Real Gradio callbacks/process_api and pure journal/comparison methods use the
existing generated-report fixtures. Storage/native rendering are explicit
fixture ports. Node tests use a tiny DOM double, not browser accessibility QA.
Each Save is consumed through its ORIGINAL iterator; no retry finishes a Save.
"""

import asyncio
import copy
import inspect
import json
import os
import shutil
import subprocess

import pytest

import ocr_review_save_feedback as feedback
import test_ocr_review_crop_uncertainty_live_ui as live
import test_ocr_review_crop_archive_uncertainty as archive


live_ui = live.ui
archive_ui = archive.ui
reports = archive.reports
SKIP = {"__type__": "update"}


class SaveCase:
    def __init__(self, kind, ui):
        self.kind, self.ui = kind, ui
        self.state = ui.crop if kind == "live" else ui.state
        self.index = 2 if kind == "live" else 19
        self.count = 7 if kind == "live" else 20

    def ready(self):
        (live.loaded if self.kind == "live" else archive.loaded)(self.ui)
        self.review()

    def review(self):
        if self.kind == "live":
            live.score(self.ui, live.prepare(self.ui)[0])
        else:
            archive.score(self.ui, archive.prepare(self.ui)[8])

    def entry(self, reviewed=True):
        suffix = "reviewed" if reviewed else "draft"
        return live.block(self.ui, "save_crop_" + suffix) if self.kind == "live" else self.ui.functions["save_archive_" + suffix]

    def args(self):
        return live.save_values(self.ui) if self.kind == "live" else [self.state, *archive.current(self.ui), *self.ui.form]

    def start(self, args=None, reviewed=True):
        result = self.entry(reviewed).fn(*(self.args() if args is None else args))
        assert inspect.isgenerator(result)
        return result

    def calls(self):
        return self.ui.service.calls if self.kind == "live" else [name for name in self.ui.calls if name == "save"]

    def committed(self):
        return self.ui.service.commits if self.kind == "live" else self.ui.saves

    def hook(self, callback):
        if self.kind == "live":
            self.ui.service.hook = callback
        else:
            self.ui.hooks["save"] = lambda _verify: callback()

    def assert_status_only(self, result, message):
        assert len(result) == self.count and result[self.index] == message
        assert tuple(result[:self.index]) + tuple(result[self.index + 1:]) == (SKIP,) * (self.count - 1)

    def change(self, kind, captured):
        if kind == "cancel":
            if self.kind == "live":
                return self.ui.functions["cancel_crop_preview"](self.state, self.ui.profile)
            return archive.call(self.ui, "cancel_archive_preview", "fit")
        if self.kind == "live":
            token, editor = captured[4], captured[8]
            if kind == "reference":
                self.ui.reference = "partial edited transcription"
                result = self.ui.functions["crop_reference_edited"](
                    self.state, token, self.ui.reference, self.ui.critical, editor)
            else:
                self.ui.form[-1] = "a new pending form reason"
                result = self.ui.functions["crop_uncertainty_fields_edited"](
                    self.state, token, editor, *self.ui.form)
            live.accept(self.ui, result, "changed")
            return result
        if kind == "reference":
            self.ui.reference = "partial edited transcription"
            return archive.call(self.ui, "archive_reference_edited", captured[2],
                self.ui.reference, self.ui.critical, captured[5], captured[7])
        self.ui.form[-1] = "a new pending form reason"
        return archive.call(self.ui, "annotation_form_edited", captured[2], captured[5], captured[7], *self.ui.form)


@pytest.fixture(params=["live", "archive"])
def case(request):
    return SaveCase(request.param, request.getfixturevalue(request.param + "_ui"))


@pytest.mark.parametrize("reviewed", [False, True], ids=["draft", "reviewed"])
def test_pending_precedes_host_and_terminal_preserves_exact_admitted_arguments(case, reviewed):
    case.ready()
    if not reviewed:
        case.ui.reference, case.ui.critical = "partial authored text", "OCR\n"
    captured = case.args()
    old_score = case.state.last_score
    raw = (case.ui.reference, case.ui.critical)
    old_history = copy.deepcopy(case.state.uncertainty["journal"])
    gen = case.start(captured, reviewed)
    assert case.state.last_score is old_score and not case.calls()
    pending = next(gen)
    case.assert_status_only(pending, feedback.SAVE_PENDING)
    assert not case.calls() and not case.committed()
    assert case.state.edit_lease is not None and case.state.pending is case.state.last_score is None

    def host():
        assert not case.state.lock._is_owned()
        assert pending[case.index] == feedback.SAVE_PENDING and case.state.edit_lease is not None

    case.hook(host)
    result = next(gen)
    with pytest.raises(StopIteration):
        next(gen)
    assert len(case.calls()) == len(case.committed()) == 1
    committed = case.committed()[0]
    assert (committed["reference"], committed["critical_tokens_text"]) == raw
    assert committed["uncertainty"]["journal"] == old_history
    if reviewed:
        expected = json.loads(old_score[2]) if case.kind == "live" else old_score["reviewed"]
        assert committed["reviewed"] == expected
    else:
        assert committed["reviewed"] is None and committed["uncertainty"]["dirty"] is True
    assert "saved" in result[case.index].lower() and case.state.edit_lease is None
    assert case.state.pending is case.state.last_score is None
    assert len(result) == case.count


@pytest.mark.parametrize("after_pending", [False, True], ids=["unstarted", "pending"])
def test_close_never_enters_host_or_restores_consumed_authority(case, after_pending):
    case.ready()
    old_score = case.state.last_score
    gen = case.start()
    if after_pending:
        case.assert_status_only(next(gen), feedback.SAVE_PENDING)
    gen.close()
    assert not case.calls() and not case.committed() and case.state.edit_lease is None
    assert case.state.last_score is (None if after_pending else old_score)
    with pytest.raises(StopIteration):
        next(gen)


@pytest.mark.parametrize("edit", ["reference", "form", "cancel"])
def test_resume_after_displayed_edit_or_cancel_refuses_before_any_host_call(case, edit):
    case.ready()
    captured = case.args()
    old_history = copy.deepcopy(case.state.uncertainty["journal"])
    gen = case.start(captured)
    case.assert_status_only(next(gen), feedback.SAVE_PENDING)
    case.change(edit, captured)
    case.assert_status_only(next(gen), feedback.SAVE_UNCONFIRMED)
    with pytest.raises(StopIteration):
        next(gen)
    assert not case.calls() and not case.committed() and case.state.edit_lease is None
    assert case.state.uncertainty["journal"] == old_history
    assert case.state.pending is case.state.last_score is None


def test_duplicate_pending_attempt_preserves_original_lease_and_never_enters_host(case):
    case.ready()
    captured = case.args()
    first = case.start(captured)
    case.assert_status_only(next(first), feedback.SAVE_PENDING)
    lease = case.state.edit_lease
    duplicate = case.start(captured)
    case.assert_status_only(next(duplicate), feedback.SAVE_UNCONFIRMED)
    with pytest.raises(StopIteration):
        next(duplicate)
    assert case.state.edit_lease is lease and not case.calls()
    first.close()
    assert case.state.edit_lease is None and not case.committed()


def test_stale_malformed_save_preserves_fresh_score_and_checks_tokens_before_parsing(case):
    case.ready()
    captured = case.args()
    case.review()
    fresh = case.state.last_score
    # Replace raw fields with non-serializable objects, retaining the old token.
    if case.kind == "live":
        captured[6:8] = [object(), object()]
    else:
        captured[3:5] = [object(), object()]
    result = live.drain_result(case.start(captured))
    case.assert_status_only(result, feedback.SAVE_UNCONFIRMED)
    assert case.state.last_score is fresh and not case.calls()


def test_closing_old_pending_generator_cannot_clear_newer_save_lease(case):
    case.ready()
    captured = case.args()
    first = case.start(captured)
    next(first)
    case.change("form", captured)
    case.review()
    second = case.start()
    case.assert_status_only(next(second), feedback.SAVE_PENDING)
    lease = case.state.edit_lease
    first.close()
    assert case.state.edit_lease is lease and lease is not None
    assert not case.calls()
    second.close()
    assert case.state.edit_lease is None and not case.committed()


@pytest.mark.parametrize("failure", ["ordinary", "incomplete", "malformed"])
def test_expected_storage_or_receipt_failure_reports_unconfirmed_without_private_details(case, failure):
    case.ready()
    calls = []

    def refuse(*_args, verify_current, **_kwargs):
        calls.append(True)
        assert verify_current() is True
        if failure == "ordinary":
            raise RuntimeError("SECRET-private-storage-failure")
        return {} if failure == "malformed" else {
            "pack_id": "e" * 32, "manifest_sha256": None, "status": "incomplete", "requires_attention": True}

    if case.kind == "live":
        case.ui.service.save_live_v2 = refuse
    else:
        case.ui.service.save_revision_v2 = refuse
    trace = []
    result = live.drain_result(case.start(), trace)
    assert len(trace) == 2 and calls == [True]
    assert result[case.index] == feedback.SAVE_UNCONFIRMED
    assert "SECRET" not in str(result) and not case.committed()
    assert case.state.edit_lease is None and case.state.pending is case.state.last_score is None
    if case.kind == "archive":
        case.assert_status_only(result, feedback.SAVE_UNCONFIRMED)


@pytest.mark.parametrize("exception", [KeyboardInterrupt, SystemExit])
def test_baseexception_from_host_propagates_identity_and_expires_lease(case, exception):
    case.ready()
    failure = exception("inert cancellation")

    def fail():
        raise failure

    case.hook(fail)
    gen = case.start()
    next(gen)
    with pytest.raises(exception) as raised:
        next(gen)
    assert raised.value is failure and case.state.edit_lease is None
    assert not case.committed() and case.state.pending is case.state.last_score is None


def test_archive_reviewed_declaration_is_detached_before_pending_yield(archive_ui):
    case = SaveCase("archive", archive_ui)
    case.ready()
    original_score = case.state.last_score
    admitted = copy.deepcopy(original_score["reviewed"])
    gen = case.start()
    next(gen)
    original_score["reviewed"]["comparison"]["reference_id"] = "replacement-not-admitted"
    result = next(gen)
    with pytest.raises(StopIteration):
        next(gen)
    if case.committed():
        assert case.committed()[0]["reviewed"] == admitted
    else:
        assert result[19] == feedback.SAVE_UNCONFIRMED


def test_actual_pending_status_postprocess_failure_cannot_reach_publication(case, monkeypatch):
    """Retain/close the original iterator explicitly; no framework cleanup claim."""
    pytest.importorskip("gradio")
    from gradio.exceptions import ComponentProcessingError

    ui = case.ui
    state = ui.api_state if case.kind == "live" else ui.app.state_holder["save-feedback-archive"]
    session_hash = "uncertainty-live" if case.kind == "live" else "save-feedback-archive"
    if case.kind == "archive":
        state[case.entry().inputs[0]._id] = case.state
        asyncio.run(live.process_result(ui.app, ui.functions["refresh_crop_archives"], [None],
            state=state, session_hash=session_hash))
    case.ready()
    entry = case.entry()
    inputs = live.api_inputs(ui, entry, case.args()) if case.kind == "live" else [None, *case.args()[1:]]
    observed = []
    original = ui.app.call_function

    async def observe(*args, **kwargs):
        answer = await original(*args, **kwargs)
        observed.append(answer["iterator"])
        return answer

    failure = RuntimeError("inert pending status postprocess failure")

    def fail_pending(value):
        assert value == feedback.SAVE_PENDING
        raise failure

    monkeypatch.setattr(ui.app, "call_function", observe)
    monkeypatch.setattr(entry.outputs[case.index], "postprocess", fail_pending)
    try:
        with pytest.raises(ComponentProcessingError, match="inert pending status postprocess failure") as raised:
            asyncio.run(ui.app.process_api(entry, inputs, state=state, session_hash=session_hash))
        assert raised.value.__cause__ is failure
        assert len(observed) == 1 and observed[0] is not None
        assert not case.calls() and not case.committed()
    finally:
        for iterator in observed:
            iterator.iterator.close()
    assert case.state.edit_lease is None and case.state.pending is case.state.last_score is None


def test_save_wiring_progress_is_blank_separate_private_and_not_a_button_disable(case):
    gr = pytest.importorskip("gradio")

    ui = case.ui
    for reviewed in (False, True):
        entry = case.entry(reviewed)
        assert inspect.isgeneratorfunction(entry.fn) and len(entry.outputs) == case.count
        status = entry.outputs[case.index]
        assert isinstance(status, gr.HTML)
        assert status.elem_id == "ocr-" + case.kind + "-save-status"
        index = next(index for index, value in ui.app.fns.items() if value is entry)
        dependency = next(value for value in ui.app.config["dependencies"] if value["id"] == index)
        assert dependency["show_progress"] == "full" and dependency["api_visibility"] == "private"
        progress_ids = dependency["show_progress_on"]
        assert len(progress_ids) == 1 and progress_ids[0] not in dependency["outputs"]
        progress = ui.app.blocks[progress_ids[0]]
        assert progress.elem_id == status.elem_id + "-progress" and progress.value == ""
        assert "return args;" in dependency["js"]
        assert all(ui.app.blocks[target[0]].interactive is not False for target in dependency["targets"])


@pytest.mark.parametrize("panel", [None, True, 1, [], "LIVE", "unknown"])
def test_feedback_target_allowlist_is_exact(panel):
    # build_save_feedback imports gradio before it validates the panel.
    pytest.importorskip("gradio")
    with pytest.raises(ValueError, match="invalid Save feedback panel"):
        feedback.build_save_feedback(panel)


@pytest.mark.parametrize("panel", ["live", "archive"])
def test_feedback_template_is_escaped_and_live_region_is_not_progress(panel):
    gr = pytest.importorskip("gradio")

    with gr.Blocks() as app:
        status, progress, script = feedback.build_save_feedback(panel)
    try:
        assert status.html_template == '<span data-ocr-save-message>{{value}}</span>'
        assert "{{{value}}}" not in status.html_template
        assert 'setAttribute("role", "status")' in status.js_on_load
        assert 'setAttribute("aria-live", "polite")' in status.js_on_load
        assert 'setAttribute("aria-atomic", "true")' in status.js_on_load
        assert progress.value == "" and progress.js_on_load is None
        assert status.value == "No Save request in this view." and status.autoscroll is False
        assert 'message.dispatchEvent(new Event("ocr-save-requested", {bubbles: true}))' in script
        assert "props.value = " in status.js_on_load
        assert ".textContent" not in script and ".innerHTML" not in script
    finally:
        app.close()


@pytest.mark.parametrize("panel", ["live", "archive"])
def test_actual_client_hook_preserves_each_argument_identity_and_only_fixed_panel_text(panel):
    gr = pytest.importorskip("gradio")

    with gr.Blocks() as app:
        status, _progress, script = feedback.build_save_feedback(panel)
    app.close()
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for the actual client hook control")
    harness = r"""
const assert = require('node:assert/strict');
const {script, id, mount, terminal} = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
const attributes = {}, listeners = {}, changes = [];
const props = new Proxy({value:'old'}, {set(target,key,value) {
 assert.equal(key,'value'); changes.push(value); target[key]=value; return true;}});
const element = {setAttribute(key,value) {attributes[key]=value;},
 addEventListener(name,listener) {assert.equal(name,'ocr-save-requested'); listeners[name]=listener;}};
new Function('element','props',mount)(element,props);
assert.deepEqual(attributes, {role:'status','aria-live':'polite','aria-atomic':'true'});
const message = {dispatchEvent(event) {
 assert.equal(event.type,'ocr-save-requested'); assert.equal(event.bubbles,true);
 listeners[event.type](event); assert.equal(event.cancelBubble,true); return true;}};
const unrelated = {textContent:'keep'};
const queries = [];
global.document = {getElementById(value) {queries.push(value); assert.equal(value,id);
 return {querySelector(selector) {assert.equal(selector,'[data-ocr-save-message]'); return message;}};}};
const callback = eval('(' + script + ')');
const values = [Object.freeze({token:'unchanged'}), Object.freeze(['raw']), null, false, '<script>text</script>'];
const answer = callback(...values);
assert.equal(answer.length, values.length);
values.forEach((value,index) => assert.strictEqual(answer[index], value));
assert.deepEqual(queries,[id]);
const requested = 'Save requested. Waiting or processing; completion is not yet confirmed.';
assert.equal(props.value,requested);
assert.equal(unrelated.textContent,'keep');
// The HTML-owned value itself changes on every dispatch. Two identical server
// refusals can therefore restore the terminal message after either request.
props.value = terminal;
callback(...values);
assert.equal(props.value,requested);
props.value = terminal;
assert.equal(props.value,terminal);
assert.deepEqual(changes,[requested,terminal,requested,terminal]);
assert.deepEqual(attributes, {role:'status','aria-live':'polite','aria-atomic':'true'});
global.document = {getElementById() {return null;}};
const missing = callback(...values);
values.forEach((value,index) => assert.strictEqual(missing[index],value));
assert.equal(props.value,terminal);
global.document = {getElementById() {return {querySelector() {return null;}};}};
const unmounted = callback(...values);
values.forEach((value,index) => assert.strictEqual(unmounted[index],value));
assert.equal(props.value,terminal);
process.stdout.write('fixed-text-original-arguments-ok');
"""
    environment = {key: value for key, value in os.environ.items() if not key.upper().startswith("NODE")}
    result = subprocess.run([node, "-e", harness], input=json.dumps({"script": script, "id": status.elem_id,
        "mount": status.js_on_load, "terminal": feedback.SAVE_UNCONFIRMED}),
        # A ceiling, not an expectation: Node can take over 15 s to start on
        # busy hosted Windows runners.
        capture_output=True, text=True, encoding="utf-8", timeout=60, env=environment, check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "fixed-text-original-arguments-ok"
