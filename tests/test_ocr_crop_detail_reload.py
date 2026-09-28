"""Direct installed-Gradio callbacks with explicitly inert preview/service ports.

These controls prove returned update semantics and admitted server generations,
not browser rendering, network response order, native OCR or recognition quality.
The companion process_api tests exercise component application separately.
"""

from __future__ import annotations

import copy
from types import SimpleNamespace as NS

import pytest

from ocr_crop_review_runtime import CropPreviewError
import test_ocr_review_crop_ui as live
import test_ocr_review_crop_archive_ui as archive
import test_ocr_review_crop_save_ui as saved_live


execution_ui = live.execution_ui
live_ui = live.ui
evidence = archive.evidence
archive_ui = archive.ui
SKIP = {"__type__": "update"}
REFERENCE = "synthetic region"
CRITICAL = "synthetic"


@pytest.fixture(params=("live", "archive"))
def viewer(request, monkeypatch):
    kind = request.param
    host = request.getfixturevalue(kind + "_ui")
    if kind == "live":
        live.loaded(host)
        state = host.crop
        image_index, fields_index = 3, 7
        owner, renderer = host.coordinator, "render_crop_preview"

        def change(profile, token):
            return host.functions["crop_preview_profile_changed"](state, token, profile)

        def reload(profile, token, reference, critical):
            return host.functions["reload_crop_detail"](
                *live.common(host, token, profile), reference, critical, *live.CONTROLS)

        def cancel(profile):
            return host.functions["cancel_crop_preview"](state, profile)

        def edit(token, reference, critical):
            return host.functions["crop_reference_edited"](state, token, reference, critical)

        def prepare(profile):
            return host.functions["prepare_crop_reference"](
                *live.common(host, state.generation, profile), REFERENCE, CRITICAL, *live.CONTROLS)

        def score(profile, ticket):
            return host.functions["score_crop_reference"](
                *live.common(host, state.generation, profile), REFERENCE, CRITICAL, ticket, True, *live.CONTROLS)

        def clear_binding():
            return host.functions["crop_context_changed"](state)
    else:
        archive.loaded(host)
        state = host.state
        image_index, fields_index = 1, 6
        owner, renderer = host.service, "open"

        def change(profile, token):
            return host.functions["archive_preview_profile_changed"](state, token, profile)

        def reload(profile, token, reference, critical):
            return host.functions["reload_archive_detail"](
                state, archive.PACK, token, reference, critical, profile)

        def cancel(profile):
            return host.functions["cancel_archive_preview"](state, profile)

        def edit(token, reference, critical):
            return host.functions["archive_reference_edited"](
                state, token, reference, critical, state.action_token)

        def prepare(profile):
            return host.functions["prepare_archive_reference"](
                state, archive.PACK, state.generation, REFERENCE, CRITICAL, state.action_token, profile)

        def score(profile, ticket):
            return host.functions["score_archive_reference"](
                state, archive.PACK, state.generation, REFERENCE, CRITICAL,
                ticket, True, state.action_token, profile)

        def clear_binding():
            return host.functions["select_crop_archive"](state, None, state.generation)

    calls = []
    underlying = getattr(owner, renderer)

    def observed(*args, **kwargs):
        calls.append(kwargs["preview_profile"])
        return underlying(*args, **kwargs)

    monkeypatch.setattr(owner, renderer, observed)
    return NS(kind=kind, host=host, state=state, change=change, reload=reload,
              cancel=cancel, edit=edit, prepare=prepare, score=score,
              clear_binding=clear_binding, calls=calls, owner=owner,
              renderer=renderer, observed=observed,
              image_index=image_index, fields_index=fields_index)


def _fields(viewer, result):
    return result[viewer.fields_index:viewer.fields_index + 2]


def _state(state):
    return copy.deepcopy({key: value for key, value in vars(state).items() if key != "lock"})


@pytest.mark.parametrize("profile", ["dpi288", "dpi576"])
def test_genuine_profile_change_clears_review_without_rendering_or_erasing_draft(viewer, profile):
    v = viewer
    v.edit(v.state.generation, REFERENCE, CRITICAL)
    v.prepare("fit")
    assert v.state.pending is not None
    marker = copy.deepcopy(v.state.draft_scope)
    digest = v.state.reference_digest
    generation = v.state.generation
    result = v.change(profile, generation)
    assert v.state.generation != generation and v.state.preview_profile == profile
    assert v.state.pending is v.state.loaded is None
    assert v.state.draft_scope == marker and v.state.reference_digest == digest
    assert result[v.image_index] is None and _fields(v, result) == (SKIP, SKIP)
    assert result[v.fields_index + 2:v.fields_index + 6] == ("", False, "", "")
    assert v.calls == []


def test_duplicate_profile_preserves_prepared_and_scored_authority(viewer):
    v = viewer
    v.edit(v.state.generation, REFERENCE, CRITICAL)
    ticket = v.prepare("fit")[0]
    before = _state(v.state)
    assert v.change("fit", v.state.generation) == (SKIP,) * 14
    assert _state(v.state) == before
    v.score("fit", ticket)
    before = _state(v.state)
    assert v.change("fit", v.state.generation) == (SKIP,) * 14
    assert _state(v.state) == before and not v.calls
    v.change("dpi288", v.state.generation)
    assert v.state.loaded is v.state.pending is None
    if v.kind == "archive":
        assert v.state.last_score is None


@pytest.mark.parametrize("profile", ["fit", "dpi288", "dpi576"])
@pytest.mark.parametrize("outcome", ["success", "raster_limit", "preview_cancelled"])
def test_detail_reload_preserves_semantically_partial_fields_on_success_or_refusal(viewer, monkeypatch, profile, outcome):
    v = viewer
    marker = copy.deepcopy(v.state.draft_scope)
    v.change(profile, v.state.generation)
    if outcome != "success":
        def refuse(*_args, **kwargs):
            v.calls.append(kwargs["preview_profile"])
            raise CropPreviewError(code=outcome)
        monkeypatch.setattr(v.owner, v.renderer, refuse)
    # Empty reference with a trailing incomplete critical entry cannot Prepare,
    # but is intentionally a valid bounded raw draft for detail-only reload.
    result = v.reload(profile, v.state.generation, "", "unfinished\n")
    assert _fields(v, result) == (SKIP, SKIP)
    assert v.state.draft_scope == marker and v.calls == [profile]
    assert v.state.pending is None
    if outcome == "success":
        update = result[v.image_index]
        assert update["value"].mode == "RGB"
        assert update["elem_classes"] == ([] if profile == "fit" else ["ocr-preview-detail"])
        assert v.state.loaded["preview_profile"] == profile
    else:
        assert result[v.image_index] is None and v.state.loaded is None
    assert result[v.fields_index + 2:v.fields_index + 6] == ("", False, "", "")


def test_reload_returns_no_stale_authored_values_after_an_intervening_edit(viewer, monkeypatch):
    v = viewer
    v.change("dpi288", v.state.generation)
    captured_token = v.state.generation
    observed_edits = []

    def while_rendering(*args, **kwargs):
        # A real client still has the preceding token while native work is in
        # flight. Its partial new text must never be overwritten by this return.
        observed_edits.append(v.edit(captured_token, "newer partial draft", "newer\n"))
        return v.observed(*args, **kwargs)

    monkeypatch.setattr(v.owner, v.renderer, while_rendering)
    result = v.reload("dpi288", captured_token, "older draft", "older\n")
    assert _fields(v, result) == (SKIP, SKIP)
    assert observed_edits == [(SKIP,) * (6 if v.kind == "live" else 7)]
    assert v.state.loaded["preview_profile"] == "dpi288" and v.state.pending is None
    assert v.calls == ["dpi288"]


def test_cancel_during_reload_keeps_draft_and_refuses_late_image(viewer, monkeypatch):
    v = viewer
    marker = copy.deepcopy(v.state.draft_scope)
    v.change("dpi576", v.state.generation)
    cancellations = []

    def during(*args, **kwargs):
        result = v.observed(*args, **kwargs)
        cancellations.append(v.cancel("dpi576"))
        assert kwargs["cancel_requested"]() is True
        return result

    monkeypatch.setattr(v.owner, v.renderer, during)
    with pytest.raises(Exception, match="changed"):
        v.reload("dpi576", v.state.generation, "draft", "partial\n")
    assert len(cancellations) == 1 and _fields(v, cancellations[0]) == (SKIP, SKIP)
    assert v.state.draft_scope == marker and v.state.loaded is v.state.pending is None
    assert v.calls == ["dpi576"]


def test_stale_profile_aba_requires_explicit_resync_without_borrowing_new_view(viewer):
    v = viewer
    old = v.state.generation
    v.change("dpi288", old)
    before = _state(v.state)
    assert v.change("fit", old) == (SKIP,) * 14
    assert _state(v.state) == before
    with pytest.raises(Exception, match="changed"):
        v.reload("fit", v.state.generation, "draft", "")
    assert _state(v.state) == before and v.calls == []
    cleared = v.cancel("fit")
    assert _fields(v, cleared) == (SKIP, SKIP)
    result = v.reload("fit", v.state.generation, "draft", "")
    assert _fields(v, result) == (SKIP, SKIP) and v.calls == ["fit"]
    assert v.state.loaded["preview_profile"] == "fit" and v.state.pending is None


@pytest.mark.parametrize("action", ["prepare", "score"])
@pytest.mark.parametrize("profile", ["dpi288", "dpi576"])
def test_mismatched_action_profile_does_not_consume_current_ticket(viewer, action, profile):
    v = viewer
    v.edit(v.state.generation, REFERENCE, CRITICAL)
    ticket = v.prepare("fit")[0]
    before = _state(v.state)
    with pytest.raises(Exception, match="changed"):
        v.prepare(profile) if action == "prepare" else v.score(profile, ticket)
    assert _state(v.state) == before and not v.calls
    v.score("fit", ticket)


def test_context_or_pack_change_cannot_reuse_previous_draft_binding(viewer):
    v = viewer
    v.change("dpi288", v.state.generation)
    result = v.clear_binding()
    assert _fields(v, result) == ("", "") and v.state.draft_scope is None
    with pytest.raises(Exception, match="changed"):
        v.reload("dpi288", v.state.generation, "preceding scope draft", "")
    assert v.calls == [] and v.state.loaded is None


@pytest.mark.parametrize("profile", [None, True, "288"])
def test_invalid_selector_revokes_authority_but_keeps_bound_draft(viewer, profile):
    v = viewer
    v.edit(v.state.generation, REFERENCE, CRITICAL)
    v.prepare("fit")
    marker = copy.deepcopy(v.state.draft_scope)
    result = v.change(profile, v.state.generation)
    assert _fields(v, result) == (SKIP, SKIP) and result[v.image_index] is None
    assert v.state.pending is v.state.loaded is None and v.state.draft_scope == marker
    assert v.calls == []
    v.cancel("fit")
    v.reload("fit", v.state.generation, REFERENCE, CRITICAL)
    assert v.calls == ["fit"] and v.state.loaded["preview_profile"] == "fit"


@pytest.mark.parametrize("action", ["save_archive_draft", "save_archive_reviewed"])
@pytest.mark.parametrize("profile", ["dpi288", "dpi576"])
def test_archive_save_profile_mismatch_cannot_consume_newer_score(archive_ui, action, profile):
    host = archive_ui
    item, _ = archive.scored(host)
    state = host.state
    before = _state(state)
    calls = list(host.calls)
    arguments = (state, archive.PACK, state.generation, item.reference, item.critical, state.action_token)
    with pytest.raises(Exception, match="changed"):
        host.functions[action](*arguments, profile)
    assert _state(state) == before and host.calls == calls and not host.saves
    host.functions[action](*arguments, "fit")
    assert len(host.saves) == 1 and state.last_score is None


@pytest.fixture
def live_save_ui(monkeypatch):
    # Reuse the Save-enabled fixture's explicit inert ports without changing
    # the default live fixture used by the two-panel controls above.
    port = saved_live.service.__wrapped__(monkeypatch)
    host = saved_live.execution_ui.__wrapped__(monkeypatch, port)
    host = live.ui.__wrapped__(host, monkeypatch)
    host = saved_live.ui.__wrapped__(host, port)
    try:
        yield host
    finally:
        host.app.close()


@pytest.mark.parametrize("action", ["save_crop_draft", "save_crop_reviewed"])
@pytest.mark.parametrize("profile", ["dpi288", "dpi576"])
def test_live_save_profile_mismatch_cannot_consume_newer_score(live_save_ui, action, profile):
    host = live_save_ui
    item = saved_live.scored(host)
    before = _state(host.crop)
    with pytest.raises(Exception, match="changed"):
        host.functions[action](*live.common(host, profile=profile),
                               item.reference, item.critical, *live.CONTROLS)
    assert _state(host.crop) == before and not host.service.calls
    host.functions[action](*live.common(host, profile="fit"),
                           item.reference, item.critical, *live.CONTROLS)
    assert len(host.service.calls) == 1 and host.crop.last_score is None


@pytest.mark.parametrize("profile", ["dpi288", "dpi576"])
def test_live_detail_reload_requires_a_new_score_before_reviewed_save(live_save_ui, profile):
    host = live_save_ui
    item = saved_live.scored(host)
    before = _state(host.crop)
    assert host.functions["crop_preview_profile_changed"](
        host.crop, host.crop.generation, "fit") == (SKIP,) * 14
    assert _state(host.crop) == before
    host.functions["crop_preview_profile_changed"](host.crop, host.crop.generation, profile)
    assert host.crop.last_score is host.crop.pending is host.crop.loaded is None
    reloaded = host.functions["reload_crop_detail"](
        *live.common(host, profile=profile), item.reference, item.critical, *live.CONTROLS)
    assert reloaded[7:9] == (SKIP, SKIP) and host.crop.loaded["preview_profile"] == profile
    result = host.functions["save_crop_reviewed"](
        *live.common(host, profile=profile), item.reference, item.critical, *live.CONTROLS)
    assert "not confirmed" in result[-1] and not host.service.calls
    prepared = host.functions["prepare_crop_reference"](
        *live.common(host, profile=profile), item.reference, item.critical, *live.CONTROLS)
    host.functions["score_crop_reference"](
        *live.common(host, profile=profile), item.reference, item.critical, prepared[0], True, *live.CONTROLS)
    host.functions["save_crop_reviewed"](
        *live.common(host, profile=profile), item.reference, item.critical, *live.CONTROLS)
    assert len(host.service.calls) == 1 and host.crop.last_score is None
    declaration = host.service.calls[0][2]["reviewed"]
    assert set(declaration) == {"reference", "comparison", "source_image"}
    assert declaration["source_image"]["rgb_sha256"] == host.crop.loaded["image"][2]
