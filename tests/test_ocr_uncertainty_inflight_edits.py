"""Displayed pre-Save edit captures must invalidate an in-flight publication.

Real live/archive UI callbacks and retained pre-action browser payloads; bounded
thread barriers hold explicit service commit doubles. No browser, native render,
OCR or filesystem publication. No callback borrows the newer server-only token.
"""

import copy
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

import test_ocr_review_crop_uncertainty_live_ui as live
import test_ocr_review_crop_archive_uncertainty as archive


# Register the existing named fixtures without eagerly building both panels.
live_ui = live.ui
archive_ui = archive.ui
reports = archive.reports


@pytest.mark.parametrize("panel", ["live", "archive"])
@pytest.mark.parametrize("edit", ["reference", "annotation_form"])
def test_displayed_pre_save_edit_revokes_held_commit(request, panel, edit):
    ui = request.getfixturevalue(panel + "_ui")
    entered, release = threading.Event(), threading.Event()
    commits = []
    new_reference = "not liable!"

    if panel == "live":
        live.loaded(ui)
        live.add_uncertainty(ui)
        live.resolve(ui)
        live.score(ui, live.prepare(ui)[0])
        displayed_token = ui.token
        displayed_editor = copy.deepcopy(ui.editor)
        save_values = live.save_values(ui)
        save_fn = ui.functions["save_crop_reviewed"]

        def hold():
            entered.set()
            assert release.wait(5), "test release timed out"

        ui.service.hook = hold
        commits = ui.service.commits
        state = ui.crop
        if edit == "reference":
            edit_fn = ui.functions["crop_reference_edited"]
            edit_values = [state, displayed_token, new_reference, ui.critical, displayed_editor]
        else:
            form = ui.form.copy()
            form[3] = "new pending tentative reading"
            edit_fn = ui.functions["crop_uncertainty_fields_edited"]
            edit_values = [state, displayed_token, displayed_editor, *form]
    else:
        archive.unresolved(ui)
        archive.choose(ui)
        archive.apply(ui, "resolve", reading="not liable")
        archive.score(ui, archive.prepare(ui)[8])
        state = ui.state
        displayed_token, displayed_action, displayed_image = state.generation, state.action_token, ui.image_token
        save_values = [state, *archive.current(ui), *ui.form]
        save_fn = ui.functions["save_archive_reviewed"].fn

        def held_save(pack_id, *, expected_pack_sha256, verify_current, **values):
            assert not state.lock._is_owned()
            assert pack_id == archive.PACK and expected_pack_sha256 == archive.SHA
            assert verify_current() is True
            entered.set()
            assert release.wait(5), "test release timed out"
            assert verify_current() is True
            commits.append(copy.deepcopy(values))
            return {"pack_id": archive.OTHER, "manifest_sha256": "d" * 64,
                    "status": "verified_complete", "requires_attention": True}

        ui.service.save_revision_v2 = held_save
        if edit == "reference":
            edit_fn = ui.functions["archive_reference_edited"].fn
            edit_values = [state, displayed_token, new_reference, ui.critical, displayed_action, displayed_image]
        else:
            form = ui.form.copy()
            form[4] = "new pending tentative reading"
            edit_fn = ui.functions["annotation_form_edited"].fn
            edit_values = [state, displayed_token, displayed_action, displayed_image, *form]

    retained_journal = copy.deepcopy(state.uncertainty["journal"])
    with ThreadPoolExecutor(max_workers=2) as pool:
        trace = []
        future = pool.submit(lambda: live.drain_result(save_fn(*save_values), trace))
        assert entered.wait(5), "Save did not reach held commit"
        try:
            # These exact values were captured before Save began; they are still
            # the displayed values: the pending yield delivered status only.
            edited = pool.submit(edit_fn, *edit_values).result(timeout=2)
        finally:
            release.set()
        saved = future.result(timeout=5)
        assert len(trace) == 2 and "unconfirmed" in saved[2 if panel == "live" else 19]
    assert not commits, f"Save committed after displayed {panel} {edit}; edit={edited!r}, save={saved!r}"
    assert state.pending is None and state.last_score is None
    assert state.uncertainty["journal"] == retained_journal
    if edit == "reference":
        assert state.uncertainty["dirty"] is True
        assert state.uncertainty["annotations"][0]["status"] == "unresolved"
        assert state.reference_digest == live.live._raw_reference_digest(new_reference, ui.critical)
    else:
        assert state.uncertainty["dirty"] is False
        assert state.uncertainty["annotations"][0]["status"] == "resolved"


@pytest.mark.parametrize("panel", ["live", "archive"])
@pytest.mark.parametrize("stage", ["prepare", "review"])
@pytest.mark.parametrize("edit", ["reference", "annotation_form"])
def test_displayed_pre_action_edit_prevents_late_prepare_or_review(request, panel, stage, edit):
    ui = request.getfixturevalue(panel + "_ui")
    entered, release = threading.Event(), threading.Event()

    def barrier(*_args):
        entered.set()
        assert release.wait(5), "test release timed out"

    if panel == "live":
        live.loaded(ui)
        live.add_uncertainty(ui)
        live.resolve(ui)
        ticket = live.prepare(ui)[0] if stage == "review" else None
        state = ui.crop
        captured_token, captured_editor = ui.token, copy.deepcopy(ui.editor)
        if stage == "review":
            function, values = ui.functions["score_crop_reference"], live.score_values(ui, ticket)
            ui.score_hook = barrier
        else:
            function = ui.functions["prepare_crop_reference"]
            values = [*live.common(ui), ui.reference, ui.critical, captured_editor, *ui.form, *live.CONTROLS]
            ui.author_hook = barrier
        if edit == "reference":
            edit_fn = ui.functions["crop_reference_edited"]
            edits = [state, captured_token, "not liable!", ui.critical, captured_editor]
        else:
            form = ui.form.copy()
            form[3] = "changed tentative form"
            edit_fn = ui.functions["crop_uncertainty_fields_edited"]
            edits = [state, captured_token, captured_editor, *form]
    else:
        archive.unresolved(ui)
        archive.choose(ui)
        archive.apply(ui, "resolve", reading="not liable")
        ticket = archive.prepare(ui)[8] if stage == "review" else None
        state = ui.state
        token, action, image = state.generation, state.action_token, ui.image_token
        if stage == "review":
            function = ui.functions["score_archive_reference"].fn
            values = [state, archive.PACK, token, ui.reference, ui.critical, ticket, True,
                      action, "fit", image, *ui.form]
            ui.hooks["compare"] = barrier
        else:
            function = ui.functions["prepare_archive_reference"].fn
            values = [state, *archive.current(ui), *ui.form]
            ui.hooks["author"] = barrier
        if edit == "reference":
            edit_fn = ui.functions["archive_reference_edited"].fn
            edits = [state, token, "not liable!", ui.critical, action, image]
        else:
            form = ui.form.copy()
            form[4] = "changed tentative form"
            edit_fn = ui.functions["annotation_form_edited"].fn
            edits = [state, token, action, image, *form]
    retained = copy.deepcopy(state.uncertainty["journal"])
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(function, *values)
        assert entered.wait(5), "host author/compare did not reach barrier"
        try:
            result = pool.submit(edit_fn, *edits).result(timeout=2)
        finally:
            release.set()
        try:
            result_after = pending.result(timeout=5)
        except Exception as error:
            result_after = type(error).__name__
    assert state.pending is None and state.last_score is None, (result, result_after)
    assert state.uncertainty["journal"] == retained
    assert state.uncertainty["dirty"] is (edit == "reference")
    assert state.uncertainty["annotations"][0]["status"] == ("unresolved" if edit == "reference" else "resolved")


def test_live_returned_annotation_array_must_match_reviewed_journal(live_ui):
    ui = live_ui
    live.loaded(ui)
    live.add_uncertainty(ui)
    live.resolve(ui)
    ticket = live.prepare(ui)[0]
    before = copy.deepcopy(ui.crop.uncertainty)
    ui.mutate_score = lambda result: result["reference"].update(annotations=[])
    returned = live.score(ui, ticket)
    assert returned[:3] == ("", False, "")
    assert ui.crop.last_score is None and ui.crop.uncertainty == before
    assert not ui.service.calls
