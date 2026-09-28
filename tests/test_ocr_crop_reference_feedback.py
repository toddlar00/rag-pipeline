"""Typed field feedback and authority controls with inert Gradio host ports.

No server, browser, PDF, OCR, storage publication or model is used. Archive
positive scoring uses the existing pure comparison fixture. These direct
callbacks complement separate installed process_api integration controls.
"""
from __future__ import annotations

import copy
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

import ocr_review_crop_archive_ui as archive
import ocr_review_crop_ui as live
import ocr_review_execution_ui as execution
import test_ocr_review_crop_archive_ui as archive_fixtures
import test_ocr_review_crop_save_ui as save_fixtures
import test_ocr_review_crop_ui as live_fixtures
import test_ocr_review_execution_ui as execution_fixtures
from test_ocr_review_execution_ui import CONTROLS


evidence = archive_fixtures.evidence
archive_host = archive_fixtures.ui


@pytest.fixture(params=[False, True], ids=["live", "live-save"])
def live_host(request, monkeypatch):
    port = save_fixtures.SavePort() if request.param else None
    original = execution.build_crop_review_panel

    def panel(*args, **kwargs):
        return original(*args, **kwargs, pack_service=port)

    monkeypatch.setattr(execution, "build_crop_review_panel", panel)
    host = execution_fixtures.ui.__wrapped__(monkeypatch)
    host = live_fixtures.ui.__wrapped__(host, monkeypatch)
    host.crop = live._SaveCropState() if port else live._CropState()
    host.port = port
    try:
        yield host
    finally:
        host.app.close()


def snapshot(state):
    return copy.deepcopy({key: value for key, value in vars(state).items() if key != "lock"})


def prepare_live(host, reference="synthetic region", critical="synthetic", *, token=None):
    return host.functions["prepare_crop_reference"](
        *live_fixtures.common(host, token), reference, critical, *CONTROLS)


def score_live(host, ticket, reference="synthetic region", critical="synthetic", *, token=None, confirmed=True):
    return host.functions["score_crop_reference"](
        *live_fixtures.common(host, token), reference, critical, ticket, confirmed, *CONTROLS)


def prepare_archive(host, reference="synthetic region", critical="synthetic", *, token=None, action=None):
    return host.functions["prepare_archive_reference"](host.state, archive_fixtures.PACK,
        token or host.state.generation, reference, critical, action or host.state.action_token, "fit")


def score_archive(host, ticket, reference="synthetic region", critical="synthetic", *, token=None, action=None, confirmed=True):
    return host.functions["score_archive_reference"](host.state, archive_fixtures.PACK,
        token or host.state.generation, reference, critical, ticket, confirmed, action or host.state.action_token, "fit")


BAD_FIELDS = [
    (None, "", "reference_type"), (True, "", "reference_type"),
    ("x" * 20_001, "", "reference_size"), ("\ud800", "", "reference_unicode"),
    ("", None, "critical_type"), ("", 1, "critical_type"),
    ("", "x" * 16_449, "critical_size"), ("", "\udfff", "critical_unicode"),
    ("", "\n".join("entry" for _ in range(65)), "critical_count"),
    ("", "OCR\n", "critical_blank"), ("", "OCR\n\nword", "critical_blank"),
    ("", " \t\u2003", "critical_blank"), ("", "x" * 257, "critical_entry_size"),
    ("", "OCR\r\nword", "critical_carriage_return"),
]


@pytest.mark.parametrize("reference,critical,code", BAD_FIELDS, ids=[x[2] + str(i) for i, x in enumerate(BAD_FIELDS)])
def test_typed_syntax_refusals_are_static_bounded_and_do_not_echo_input(reference, critical, code):
    with pytest.raises(live._ReferenceFieldError) as caught:
        live._reference(reference, critical)
    assert caught.value.code == code
    assert str(caught.value) == live._ReferenceFieldError._NOTICES[code] + " Then prepare and confirm again."
    assert len(str(caught.value)) < 260


@pytest.mark.parametrize("reference,critical", [
    ("", ""), ("  raw\r\ntranscription  ", " OCR \nword"),
    ("\x00\U0001f642", "\U0001f642"), ("x" * 20_000, "x" * 256),
    ("", "\n".join("x" * 256 for _ in range(64))),
], ids=["empty", "exact-whitespace", "unicode", "max-reference-entry", "max-entries"])
def test_valid_syntax_keeps_exact_raw_strings_and_existing_limits(reference, critical):
    result = live._reference(reference, critical)
    assert result == (reference, [] if critical == "" else critical.split("\n"), live._raw_reference_digest(reference, critical))


def test_partial_draft_digest_remains_permissive_and_no_semantic_rule_is_invented():
    assert live._raw_reference_digest("draft", "OCR\n")
    # Downstream reference semantics still enforce occurrence/uniqueness.
    assert live._reference("", "not-in-reference\nnot-in-reference")[1] == ["not-in-reference"] * 2


@pytest.mark.parametrize("critical", ["OCR\n", "OCR\n\nword", "OCR\r\nword", "x" * 257])
def test_live_prepare_inline_refusal_keeps_loaded_view_and_recovers_without_reload(live_host, critical):
    host = live_host
    image = live_fixtures.loaded(host)[3]["value"]
    loaded = host.crop.loaded
    previous = prepare_live(host)
    score_live(host, previous[0])
    token = host.crop.generation
    calls = (len(host.renders), len(host.pairs), len(host.scores))
    refused = prepare_live(host, "Synthetic OCR chall", critical)
    assert refused[:4] == ("", False, "", "") and "Critical" in refused[4]
    assert len(refused) == (6 if host.port else 5)
    assert host.crop.loaded is loaded and image.size == (64, 32)
    assert host.crop.pending is None and host.crop.reference_digest is None
    assert calls == (len(host.renders), len(host.pairs), len(host.scores))
    if host.port:
        assert refused[5] == host.crop.generation != token
        assert host.crop.last_score is None and not host.port.calls
    corrected = prepare_live(host, token=refused[5] if host.port else token)
    scored = score_live(host, corrected[0], token=corrected[5] if host.port else token)
    assert scored[:2] == ("", False) and json.loads(scored[2])["status"] == "compared"
    assert len(host.renders) == calls[0] and len(host.pairs) == calls[1]


@pytest.mark.parametrize("reference,critical", [(None, ""), ("synthetic region", "OCR\n"), ("\ud800", "")], ids=["type", "blank", "unicode"])
def test_live_current_ticket_field_refusal_consumes_without_scoring(live_host, reference, critical):
    host = live_host
    live_fixtures.loaded(host)
    prepared = prepare_live(host)
    token = host.crop.generation
    refused = score_live(host, prepared[0], reference, critical)
    assert refused[:3] == ("", False, "") and "prepare and confirm again" in refused[3]
    assert len(refused) == (5 if host.port else 4)
    assert host.crop.pending is None and host.crop.loaded is not None and not host.scores
    if host.port:
        assert refused[4] == host.crop.generation != token and host.crop.last_score is None
    with pytest.raises(Exception, match="Crop review changed"):
        score_live(host, prepared[0])
    fresh = prepare_live(host)
    assert score_live(host, fresh[0])[2] and len(host.scores) == 1


@pytest.mark.parametrize("action,fault", [(action, fault)
    for action in ("prepare", "score")
    for fault in ("token", "profile", "annotation", "ticket", "unchecked")
    if action == "score" or fault not in ("ticket", "unchecked")])
def test_live_invalid_authority_precedes_parser_without_touching_newer_pending(live_host, monkeypatch, action, fault):
    host = live_host
    live_fixtures.loaded(host)
    prepared = prepare_live(host)
    args = live_fixtures.common(host)
    ticket, confirmed = prepared[0], True
    if fault == "token":
        args[4] += "stale"
    elif fault == "profile":
        args[5] = "dpi288"
    elif fault == "annotation":
        args[3] += "stale"
    elif fault == "ticket":
        ticket += "stale"
    else:
        confirmed = False
    before = snapshot(host.crop)

    def forbidden(*_):
        raise AssertionError("obsolete request reached field parser")

    monkeypatch.setattr(live, "_reference", forbidden)
    fields = [object(), object(), *([ticket, confirmed] if action == "score" else []), *CONTROLS]
    with pytest.raises(Exception, match="Crop review changed"):
        host.functions[f"{action}_crop_reference"](*args, *fields)
    assert snapshot(host.crop) == before and not host.scores


@pytest.mark.parametrize("action", ["prepare", "score"])
def test_live_field_error_cannot_clear_reentrant_newer_preparation(live_host, monkeypatch, action):
    host = live_host
    live_fixtures.loaded(host)
    first = prepare_live(host)
    original = live._reference
    replacement = []

    def supersede(reference, critical):
        if critical == "OCR\n":
            replacement.append(prepare_live(host, "new independent text", ""))
        return original(reference, critical)

    monkeypatch.setattr(live, "_reference", supersede)
    with pytest.raises(Exception, match="Crop review changed"):
        if action == "prepare":
            prepare_live(host, critical="OCR\n")
        else:
            score_live(host, first[0], critical="OCR\n")
    assert host.crop.pending[0] == replacement[0][0] and not host.scores


@pytest.mark.parametrize("action", ["prepare", "score"])
@pytest.mark.parametrize("error_type", [ValueError, RuntimeError, MemoryError, KeyboardInterrupt])
def test_live_unknown_parser_failure_is_not_typed_feedback(live_host, monkeypatch, action, error_type):
    host = live_host
    live_fixtures.loaded(host)
    prepared = prepare_live(host)
    error = error_type("PRIVATE field internals")

    def failed(*_):
        raise error

    monkeypatch.setattr(live, "_reference", failed)
    with pytest.raises(BaseException) as caught:
        prepare_live(host) if action == "prepare" else score_live(host, prepared[0])
    if error_type is KeyboardInterrupt:
        assert caught.value is error
    else:
        assert str(caught.value).strip("'") == live._FAILED
    assert not host.scores


@pytest.mark.parametrize("action", ["prepare", "score"])
def test_archive_field_refusal_returns_new_action_and_preserves_raw_draft_and_image(archive_host, action):
    host = archive_host
    opened = archive_fixtures.loaded(host)
    raw_draft = copy.deepcopy(host.view["draft"])
    loaded = host.state.loaded
    first = prepare_archive(host)
    if action == "prepare":
        score_archive(host, first[0])
    token, action_token = host.state.generation, host.state.action_token
    calls = list(host.calls)
    refused = (prepare_archive(host, "Synthetic OCR chall", "OCR\n") if action == "prepare"
               else score_archive(host, first[0], "Synthetic OCR chall", "OCR\n"))
    assert refused[:4 if action == "prepare" else 3] == (("", False, "", "") if action == "prepare" else ("", False, ""))
    assert "Remove blank lines" in refused[-1] and len(refused) == (6 if action == "prepare" else 5)
    assert refused[-2] == host.state.action_token != action_token
    assert host.state.generation == token and host.state.loaded is loaded
    assert host.state.pending is host.state.last_score is None and host.state.reference_digest is None
    assert host.calls == calls and not host.saves and host.view["draft"] == raw_draft
    assert opened[1]["value"] is host.images[0]
    new = prepare_archive(host, action=refused[-2])
    result = score_archive(host, new[0], action=new[-2])
    assert json.loads(result[2])["status"] == "compared" and len(host.images) == 1


@pytest.mark.parametrize("action,fault", [(action, fault)
    for action in ("prepare", "score")
    for fault in ("token", "action", "profile", "pack", "ticket")
    if action == "score" or fault != "ticket"])
def test_archive_stale_malformed_fields_cannot_mutate_newer_preparation(archive_host, monkeypatch, action, fault):
    host = archive_host
    archive_fixtures.loaded(host)
    prepared = prepare_archive(host)
    pack, token, action_token, profile, ticket = archive_fixtures.PACK, host.state.generation, host.state.action_token, "fit", prepared[0]
    if fault == "token":
        token += "stale"
    elif fault == "action":
        action_token += "stale"
    elif fault == "profile":
        profile = "dpi288"
    elif fault == "pack":
        pack = archive_fixtures.OTHER
    else:
        ticket += "stale"
    before = snapshot(host.state)

    def forbidden(*_):
        raise AssertionError("obsolete request reached field parser")

    monkeypatch.setattr(archive, "_reference", forbidden)
    fields = [pack, token, object(), object(), *([ticket, True] if action == "score" else []), action_token, profile]
    with pytest.raises(Exception, match="Saved crop view changed"):
        host.functions[f"{action}_archive_reference"](host.state, *fields)
    assert snapshot(host.state) == before and "compare" not in host.calls


@pytest.mark.parametrize("action", ["prepare", "score"])
def test_archive_delayed_typed_failure_cannot_overwrite_newer_action(archive_host, monkeypatch, action):
    host = archive_host
    archive_fixtures.loaded(host)
    first = prepare_archive(host)
    entered, release = threading.Event(), threading.Event()
    original = archive._reference

    def held(reference, critical):
        if critical == "OCR\n":
            entered.set()
            assert release.wait(10)
        return original(reference, critical)

    monkeypatch.setattr(archive, "_reference", held)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(prepare_archive, host, "old", "OCR\n") if action == "prepare" else pool.submit(score_archive, host, first[0], "old", "OCR\n")
        try:
            assert entered.wait(5)
            current = prepare_archive(host, "new independent text", "")
            before = snapshot(host.state)
        finally:
            release.set()
        with pytest.raises(Exception, match="Saved crop view changed"):
            future.result(timeout=10)
    assert snapshot(host.state) == before and host.state.pending[0] == current[0]
    assert "compare" not in host.calls


@pytest.mark.parametrize("action", ["prepare", "score"])
@pytest.mark.parametrize("error_type", [ValueError, RuntimeError, MemoryError, KeyboardInterrupt])
def test_archive_unknown_failure_is_not_classified_as_field_feedback(archive_host, monkeypatch, action, error_type):
    host = archive_host
    archive_fixtures.loaded(host)
    prepared = prepare_archive(host)
    error = error_type("PRIVATE field internals")

    def failed(*_):
        raise error

    monkeypatch.setattr(archive, "_reference", failed)
    with pytest.raises(BaseException) as caught:
        prepare_archive(host) if action == "prepare" else score_archive(host, prepared[0])
    if error_type is KeyboardInterrupt:
        assert caught.value is error
    else:
        assert str(caught.value).strip("'") == archive._FAILED
    assert "compare" not in host.calls


def test_archive_empty_reviewed_reference_remains_valid_with_undefined_rates(archive_host):
    host = archive_host
    archive_fixtures.loaded(host)
    prepared = prepare_archive(host, "", "")
    result = score_archive(host, prepared[0], "", "")
    assert host.state.last_score["reviewed"]["reference"]["reference"] == ""
    row = json.loads(result[2])["comparison"]["records"][0]
    for side in ("baseline", "retry"):
        for unit in ("character", "word"):
            assert row[side][unit]["reference_units"] == 0
            assert row[side][unit]["error_rate"] is None
            assert row[side][unit]["insertions"] > 0


def test_archive_current_unconfirmed_action_supersedes_inflight_save_before_parser(archive_host, monkeypatch):
    host = archive_host
    item, _ = archive_fixtures.scored(host)
    original_action = host.state.action

    def forbidden(*_):
        raise AssertionError("unconfirmed action reached field parser")

    monkeypatch.setattr(archive, "_reference", forbidden)

    def current_score(_verify):
        with pytest.raises(Exception, match="Saved crop view changed"):
            score_archive(host, item.ticket, object(), object(), confirmed=False)

    host.hooks["save"] = current_score
    with pytest.raises(Exception, match="Saved crop view changed"):
        archive_fixtures.call(host, "save_archive_reviewed", archive_fixtures.PACK, item.token, item.reference, item.critical)
    assert host.state.action == original_action + 2 and host.state.last_score is None and not host.saves
