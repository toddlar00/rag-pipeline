"""In-flight editor notifications use only a bounded, expiring edit lease.

The imported fixtures use generated reports, real journal/comparison logic and
explicit inert preview/storage ports. Thread barriers exercise server callback
ordering with the OLD displayed tokens, not tokens sampled after admission.
No browser transport, native OCR/rendering, or filesystem commit is asserted.
Raw-text/form change regressions live in test_ocr_uncertainty_inflight_edits.
"""

import copy
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

import test_ocr_review_crop_archive_uncertainty as archive
import test_ocr_review_crop_uncertainty_live_ui as live
from test_ocr_review_crop_archive_uncertainty import reports as reports


SKIP = {"__type__": "update"}
archive_ui = archive.ui
live_ui = live.ui


class Panel:
    def __init__(self, kind, ui):
        self.kind, self.ui = kind, ui
        self.state = ui.crop if kind == "live" else ui.state
        self.editor = None

    def open(self):
        if self.kind == "live":
            live.loaded(self.ui)
            live.command(self.ui, "mode", mode="annotate")
            self.editor = copy.deepcopy(self.ui.editor)
        else:
            archive.loaded(self.ui)
            self.editor = archive.event(self.ui, "mode", mode="annotate")[15]

    def adopt_edit(self, result):
        if self.kind == "live":
            live.accept(self.ui, result, "changed")
            self.editor = copy.deepcopy(self.ui.editor)
        else:
            assert result[0] == self.state.generation
            assert result[12] == self.state.action_token
            self.editor = copy.deepcopy(result[15])

    def capture(self, kind="mode", *, mode="browse", bbox=None):
        payload = copy.deepcopy(self.editor)
        payload["command"] = {"kind": kind, "mode": mode if kind == "mode" else None,
                              "pixel_bbox": bbox, "ordinal": None} if kind else None
        if self.kind == "live":
            return [self.ui.token, payload]
        return [self.state.generation, self.state.action_token, self.ui.image_token, payload]

    def edit(self, captured):
        if self.kind == "live":
            return self.ui.functions["crop_uncertainty_editor_input"](self.state, *captured)
        return archive.call(self.ui, "editor_input", *captured)

    def prepare(self):
        if self.kind == "live":
            result = live.prepare(self.ui)
            self.editor = copy.deepcopy(self.ui.editor)
            return result
        result = archive.prepare(self.ui)
        self.editor = copy.deepcopy(result[15])
        return result

    def score(self):
        if self.kind == "live":
            result = live.score(self.ui, self.state.pending[0])
            self.editor = copy.deepcopy(self.ui.editor)
            return result
        result = archive.score(self.ui)
        self.editor = copy.deepcopy(result[15])
        return result

    def stage(self, operation):
        if operation == "apply":
            self.adopt_edit(self.edit(self.capture("whole_scope")))
        elif operation in ("score", "save"):
            self.prepare()
            if operation == "save":
                self.score()

    def perform(self, operation):
        if operation == "apply":
            return live.apply(self.ui) if self.kind == "live" else archive.apply(self.ui)
        if operation == "prepare":
            return self.prepare()
        if operation == "score":
            return self.score()
        if self.kind == "live":
            return live.save(self.ui)
        return archive.call(self.ui, "save_archive_reviewed", *archive.current(self.ui), *self.ui.form)

    def hook(self, operation, callback):
        if self.kind == "live":
            if operation == "save":
                self.ui.service.hook = callback
            else:
                setattr(self.ui, "score_hook" if operation == "score" else "author_hook", callback)
        else:
            key = "compare" if operation == "score" else "save" if operation == "save" else "author"
            self.ui.hooks[key] = lambda _value: callback()

    def commits(self):
        return self.ui.service.commits if self.kind == "live" else self.ui.saves

    def assert_skips(self, result):
        assert result == (SKIP,) * (9 if self.kind == "live" else 19)

    def assert_refused(self, future, operation):
        if operation == "save":
            result = future.result(timeout=5)
            assert "unconfirmed" in result[2 if self.kind == "live" else 19]
        else:
            with pytest.raises(Exception):
                future.result(timeout=5)


@pytest.fixture(params=["live", "archive"])
def panel(request):
    ui = request.getfixturevalue(request.param + "_ui")
    value = Panel(request.param, ui)
    value.open()
    return value


def barrier():
    entered, release = threading.Event(), threading.Event()

    def wait():
        entered.set()
        assert release.wait(5), "inert host barrier was not released"

    return entered, release, wait


@pytest.mark.parametrize("operation", ["apply", "prepare", "score", "save"])
@pytest.mark.parametrize("edit", ["mode", "selection"])
def test_old_displayed_editor_event_invalidates_held_operation(panel, operation, edit):
    panel.stage(operation)
    captured = panel.capture() if edit == "mode" else panel.capture("rectangle", bbox=[0, 0, 36, 36])
    old_history = copy.deepcopy(panel.state.uncertainty)
    image_token, loaded = panel.state.image_token, panel.state.loaded
    entered, release, wait = barrier()
    panel.hook(operation, wait)
    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(panel.perform, operation)
        try:
            assert entered.wait(5)
            lease = panel.state.edit_lease
            assert lease is not None
            old_action = captured[0] if panel.kind == "live" else captured[1]
            current_action = panel.state.generation if panel.kind == "live" else panel.state.action_token
            assert old_action != current_action
            edit_future = pool.submit(panel.edit, captured)
            result = edit_future.result(timeout=2)
            assert result != (SKIP,) * len(result)
            assert panel.state.edit_lease is None
            if edit == "mode":
                assert panel.state.mode == "browse" and panel.state.selection is None
            else:
                assert panel.state.selection == {"kind": "raster_edges", "pixel_bbox": [0, 0, 36, 36]}
            assert panel.state.pending is None and panel.state.last_score is None
        finally:
            release.set()
        panel.assert_refused(future, operation)
    assert panel.state.edit_lease is None and not panel.commits()
    assert panel.state.uncertainty == old_history
    assert panel.state.loaded is loaded and panel.state.image_token == image_token


@pytest.mark.parametrize("wrong", ["missing-image", "different-image", "different-mode-token"])
def test_wrong_editor_identity_cannot_borrow_or_consume_active_lease(panel, wrong):
    captured = panel.capture()
    if wrong == "different-mode-token":
        captured[-1]["mode_token"] = "0" * 32
    else:
        bad = None if wrong == "missing-image" else "0" * 32
        captured[-1]["image_token"] = bad
        if panel.kind == "archive":
            captured[2] = bad
    entered, release, wait = barrier()
    panel.hook("prepare", wait)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(panel.prepare)
        try:
            assert entered.wait(5)
            lease = panel.state.edit_lease
            assert lease is not None
            panel.assert_skips(panel.edit(captured))
            assert panel.state.edit_lease is lease and panel.state.mode == "annotate"
        finally:
            release.set()
        future.result(timeout=5)
    assert panel.state.pending is not None and panel.state.edit_lease is None


@pytest.mark.parametrize("outcome", ["success", "ordinary-error", "keyboard-interrupt", "system-exit"])
def test_lease_expires_on_every_host_exit_and_old_editor_cannot_revive_it(panel, outcome):
    captured = panel.capture()
    problem = {"success": None, "ordinary-error": RuntimeError("private inert host failure"),
               "keyboard-interrupt": KeyboardInterrupt(), "system-exit": SystemExit(23)}[outcome]

    def host():
        assert panel.state.edit_lease is not None
        if problem is not None:
            raise problem

    panel.hook("prepare", host)
    if outcome == "success":
        panel.prepare()
    elif outcome == "ordinary-error":
        # Invoke actual callback: the imported archive convenience helper
        # intentionally asserts successful preparation and is not a failure API.
        if panel.kind == "live":
            result = live.prepare(panel.ui)
            assert result[0] == "" and "private inert" not in result[4]
        else:
            result = archive.call(panel.ui, "prepare_archive_reference", *archive.current(panel.ui), *panel.ui.form)
            assert result[8] == "" and "private inert" not in result[13]
    else:
        with pytest.raises(type(problem)) as raised:
            panel.prepare()
        assert raised.value is problem
    assert panel.state.edit_lease is None
    pending = panel.state.pending
    panel.assert_skips(panel.edit(captured))
    assert panel.state.pending is pending and panel.state.edit_lease is None
    assert panel.state.mode == "annotate" and panel.state.selection is None


def test_identical_raw_notification_preserves_lease_until_a_real_editor_edit(panel):
    # A no-op notification differs from Socrates's actual raw/form edits: it
    # must keep the short lease alive so a later genuine edit can still cancel.
    panel.prepare()
    captured = panel.capture()
    entered, release, wait = barrier()
    panel.hook("prepare", wait)
    if panel.kind == "live":
        raw_args = [panel.ui.token, panel.ui.reference, panel.ui.critical, copy.deepcopy(panel.editor)]
    else:
        raw_args = [panel.state.generation, panel.ui.reference, panel.ui.critical,
                    panel.state.action_token, panel.ui.image_token]
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(panel.prepare)
        try:
            assert entered.wait(5)
            lease = panel.state.edit_lease
            if panel.kind == "live":
                result = panel.ui.functions["crop_reference_edited"](panel.state, *raw_args)
            else:
                result = archive.call(panel.ui, "archive_reference_edited", *raw_args)
            panel.assert_skips(result)
            assert panel.state.edit_lease is lease
            assert panel.edit(captured) != (SKIP,) * (9 if panel.kind == "live" else 19)
            assert panel.state.edit_lease is None and panel.state.mode == "browse"
        finally:
            release.set()
        panel.assert_refused(future, "prepare")


@pytest.mark.parametrize("noop", ["no-command", "same-mode"])
def test_valid_editor_noop_preserves_inflight_lease_and_later_edit_cancels(panel, noop):
    unchanged = panel.capture(None) if noop == "no-command" else panel.capture(mode="annotate")
    changed = panel.capture()
    entered, release, wait = barrier()
    panel.hook("prepare", wait)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(panel.prepare)
        try:
            assert entered.wait(5)
            lease = panel.state.edit_lease
            panel.assert_skips(panel.edit(unchanged))
            assert panel.state.edit_lease is lease and panel.state.mode == "annotate"
            assert panel.edit(changed) != (SKIP,) * (9 if panel.kind == "live" else 19)
            assert panel.state.edit_lease is None and panel.state.mode == "browse"
        finally:
            release.set()
        panel.assert_refused(future, "prepare")


def test_older_finally_cannot_clear_a_newer_inflight_lease(panel):
    entered1, release1, wait1 = barrier()
    entered2, release2, wait2 = barrier()
    router_lock = threading.Lock()
    calls = []

    def routed_host():
        with router_lock:
            calls.append(True)
            number = len(calls)
        (wait1 if number == 1 else wait2)()

    panel.hook("prepare", routed_host)
    old_edit = panel.capture()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(panel.prepare)
        second = None
        try:
            assert entered1.wait(5)
            first_lease = panel.state.edit_lease
            panel.adopt_edit(panel.edit(old_edit))
            assert panel.state.mode == "browse" and panel.state.edit_lease is None
            second_edit = panel.capture(mode="annotate")
            second = pool.submit(panel.prepare)
            assert entered2.wait(5)
            second_lease = panel.state.edit_lease
            assert second_lease is not None and second_lease is not first_lease
            release1.set()
            panel.assert_refused(first, "prepare")
            assert panel.state.edit_lease is second_lease
            panel.adopt_edit(panel.edit(second_edit))
            assert panel.state.mode == "annotate" and panel.state.edit_lease is None
        finally:
            release1.set()
            release2.set()
        if second is not None:
            panel.assert_refused(second, "prepare")
    assert len(calls) == 2 and panel.state.pending is panel.state.last_score is None
    assert panel.state.edit_lease is None and not panel.commits()
