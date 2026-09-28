"""Explicit host v2 paths over inert archives, journals and image-owner doubles.

The archive-policy boundary and live retained-bundle reader are named doubles;
the journal, reference, scorer, pack and private store remain real. These tests
do not claim native rendering, OCR, browser consent or full qualification.
"""

import copy
import threading
from types import SimpleNamespace as NS

import pytest

import ocr_crop_comparison as crops
import ocr_crop_uncertainty_comparison as comparison
import ocr_crop_uncertainty_journal as history
import ocr_crop_uncertainty_pack as policy_v2
import ocr_review_crop_packs as archive_host
import ocr_review_execution as execution
from ocr_crop_review_runtime import CropPreviewError, CropRasterPreview
from test_ocr_crop_comparison import _report
from test_ocr_crop_review_pack import policy_pack as policy_pack
from test_ocr_review_crop_packs import _workspace
import test_ocr_crop_uncertainty_journal as jf


@pytest.fixture
def context(tmp_path, policy_pack):
    options = policy_pack.options(before=_report("liable", bbox=[0., 0., 1., 1.]),
                                  after=_report("not liable", dpi=400, bbox=[0., 0., 1., 1.]))
    root = tmp_path / "packs"
    root.mkdir()
    workspace = _workspace(policy_pack.binding, [])
    service = archive_host.CropReviewPackService(workspace, root,
        render_preview=lambda *_a, **_k: pytest.fail("unexpected PIL-only render"),
        preview_private_root=tmp_path / "inert-preview")
    original = archive_host.packs.build_crop_review_pack(**options)
    saved = service._store.publish(original)
    evidence = service._store.read(saved["pack_id"])
    c = NS(service=service, workspace=workspace, options=options, saved=saved, original=original,
           report=evidence["baseline"]["report"], report_sha=evidence["manifest"]["pair"]["baseline"]["report_sha256"],
           scope=evidence["manifest"]["scope"], evidence=evidence)
    c.view = jf._view(c)
    c.journal = jf._new(c, "not liable", ["not liable"])
    c.ids = ("a" * 32, "baseline", "b" * 32, "retry")
    c.pair = {"pair_sha256": "d" * 64, "source_sha256": options["source_sha256"],
              "baseline_recovery_sha256": options["recovery_sha256"], "scope": c.scope,
              "baseline": evidence["manifest"]["pair"]["baseline"],
              "retry": evidence["manifest"]["pair"]["retry"]}
    coordinator = object.__new__(execution.ReviewRunCoordinator)
    coordinator._lock = threading.Lock()
    coordinator._closed = coordinator._cleanup_uncertain = False
    coordinator._runs = {}
    coordinator._pending = None
    c.reads, c.rechecks = [], []
    c.bundles = [evidence["baseline"], evidence["retry"]]

    def snapshot(*ids):
        assert ids == c.ids
        c.reads.append(True)
        return copy.deepcopy(c.pair), (), c.bundles

    coordinator._crop_pair_snapshot = snapshot
    coordinator._recheck_crop_pair = lambda *_args: c.rechecks.append(True)
    c.coordinator = coordinator
    yield c
    service.close()


def _author(c, target, **kwargs):
    options = dict(journal=None, reference="not liable", critical_tokens=["not liable"],
                   reset_anchors=False, annotation_changes=[], reason="explicit synthetic authoring")
    options.update(kwargs)
    if target == "live":
        return c.coordinator.author_crops_v2(*c.ids, expected_pair_sha256=c.pair["pair_sha256"], **options)
    return c.service.author_v2(c.saved["pack_id"], expected_pack_sha256=c.saved["manifest_sha256"], **options)


def _compare(c, target, **kwargs):
    options = dict(journal=c.journal, confirmed=True)
    options.update(kwargs)
    if target == "live":
        return c.coordinator.compare_crops_v2(*c.ids, expected_pair_sha256=c.pair["pair_sha256"], **options)
    return c.service.compare_v2(c.saved["pack_id"], expected_pack_sha256=c.saved["manifest_sha256"], **options)


def _uncertainty(c, journal=None, dirty=False):
    journal = c.journal if journal is None else journal
    replayed = history.replay_crop_journal(journal, anchor_report=c.report, anchor_report_sha256=c.report_sha)
    return {"journal": journal, "annotations": replayed["declaration"]["annotations"], "dirty": dirty}


def _save(c, **kwargs):
    options = dict(expected_pack_sha256=c.saved["manifest_sha256"], reference="not liable",
                   critical_tokens_text="not liable", uncertainty=_uncertainty(c), verify_current=lambda: True)
    options.update(kwargs)
    return c.service.save_revision_v2(c.saved["pack_id"], **options)


@pytest.mark.parametrize("target", ["live", "archive"])
def test_author_genesis_and_append_returns_detached_projection_without_consent(context, target, monkeypatch):
    c = context
    monkeypatch.setattr(crops, "compare_ocr", lambda *_a, **_k: pytest.fail("author scored"))
    first = _author(c, target)
    assert first["journal"] == c.journal and first["declaration"]["annotations"] == []
    assert set(first) == {"pair_sha256" if target == "live" else "pack_sha256", "journal", "declaration",
                          "declaration_sha256", "requires_attention", "canonical_extraction_modified"}
    second = _author(c, target, journal=first["journal"], annotation_changes=[jf._add(c)], views=[c.view])
    assert second["declaration"]["annotations"][0]["status"] == "unresolved"
    assert first["journal"] == c.journal
    second["declaration"]["annotations"].clear()
    assert len(history.replay_crop_journal(second["journal"], anchor_report=c.report,
               anchor_report_sha256=c.report_sha)["declaration"]["annotations"]) == 1
    assert not c.coordinator._runs


@pytest.mark.parametrize("target", ["live", "archive"])
@pytest.mark.parametrize("state", ["zero", "unresolved", "empty"])
def test_compare_preserves_resolved_math_or_explicit_unscorable_state(context, target, state, monkeypatch):
    c = context
    value = c.journal
    if state == "unresolved":
        value = jf._append(c, value, jf._add(c))
        monkeypatch.setattr(crops, "compare_ocr", lambda *_a, **_k: pytest.fail("unresolved reference scored"))
    elif state == "empty":
        value = jf._new(c, "", [])
    result = _compare(c, target, journal=value)
    expected = comparison.compare_crop_candidates_v2(c.report, c.evidence["retry"]["report"], result["reference"],
        baseline_report_sha256=c.report_sha, retry_report_sha256=c.pair["retry"]["report_sha256"],
        baseline_region_id="a", retry_region_id="a")
    assert result["comparison"] == expected
    assert result["requires_attention"] is True and result["canonical_extraction_modified"] is False
    assert (expected["comparison"] is None) is (state == "unresolved")
    if state == "unresolved":
        assert expected["reason"] == "reference_uncertain"
    assert not c.coordinator._runs


@pytest.mark.parametrize("target", ["live", "archive"])
@pytest.mark.parametrize("confirmed", [False, None, 1, "true"])
def test_confirmation_refused_before_any_pair_or_parent_read(context, target, confirmed, monkeypatch):
    c = context
    monkeypatch.setattr(c.coordinator, "_crop_pair_snapshot", lambda *_a: pytest.fail("read before authority"))
    monkeypatch.setattr(c.service, "_read", lambda *_a: pytest.fail("read before authority"))
    with pytest.raises(ValueError):
        _compare(c, target, confirmed=confirmed, journal={"malformed": True})


@pytest.mark.parametrize("target", ["live", "archive"])
@pytest.mark.parametrize("operation", ["author", "compare"])
@pytest.mark.parametrize("field", ["region_id", "report_sha256"])
def test_wrong_occurrence_refuses_before_heavy_journal_work(context, target, operation, field, monkeypatch):
    c = context
    value = copy.deepcopy(c.journal)
    value["binding"]["anchor"][field] = "different" if field == "region_id" else "0" * 64
    monkeypatch.setattr(history, "append_crop_journal_declaration", lambda *_a, **_k: pytest.fail("wrong occurrence append"))
    monkeypatch.setattr(comparison, "build_crop_reference_v2", lambda *_a, **_k: pytest.fail("wrong occurrence review"))
    with pytest.raises(ValueError):
        (_author if operation == "author" else _compare)(c, target, journal=value)


@pytest.mark.parametrize("target", ["live", "archive"])
@pytest.mark.parametrize("boundary", ["author", "reference", "score"])
def test_late_source_or_history_refusal_never_returns_authored_or_scored_output(context, target, boundary, monkeypatch):
    c = context
    module, name = (history, "append_crop_journal_declaration") if boundary == "author" else (
        comparison, "build_crop_reference_v2" if boundary == "reference" else "compare_crop_candidates_v2")
    original = getattr(module, name)
    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        if target == "live":
            c.coordinator._closed = True
        else:
            c.workspace.document.source_sha256 = "0" * 64
        return result
    monkeypatch.setattr(module, name, changed)
    if boundary == "reference":
        monkeypatch.setattr(comparison, "compare_crop_candidates_v2", lambda *_a, **_k: pytest.fail("scored after refusal"))
    with pytest.raises(ValueError):
        (_author if boundary == "author" else _compare)(c, target)


class _Image:
    def __init__(self, failure=None):
        self.closes = 0
        self.failure = failure

    def close(self):
        self.closes += 1
        if self.failure:
            raise self.failure

    def tobytes(self):
        assert self.closes == 0
        return b"synthetic RGB"


def _preview(c, monkeypatch, target, *, failure=None, after=None):
    image = _Image(failure)
    owner = CropRasterPreview(image, c.view)
    calls = []
    def render(scope, *, cancel_requested, preview_profile):
        assert scope == c.scope and not cancel_requested()
        calls.append(preview_profile)
        if after:
            after()
        return owner
    if target == "live":
        c.coordinator._preview = NS(cleanup_uncertain=False, render_with_view=render,
            render=lambda *_a, **_k: pytest.fail("metadata fell back to PIL"))
        def method(**kwargs):
            return c.coordinator.render_crop_preview_with_view(c.scope, **kwargs)
    else:
        monkeypatch.setattr(c.service, "_render_with_view", render)
        def method(**kwargs):
            return c.service.open_with_view(c.saved["pack_id"], **kwargs)["preview"]
    return image, owner, calls, method


@pytest.mark.parametrize("target", ["live", "archive"])
@pytest.mark.parametrize("profile", ["fit", "dpi288", "dpi576"])
def test_metadata_success_transfers_owner_without_close_or_legacy_fallback(context, monkeypatch, target, profile):
    image, owner, calls, method = _preview(context, monkeypatch, target)
    result = method(preview_profile=profile)
    assert result is owner and result.image.tobytes() == b"synthetic RGB" and image.closes == 0
    assert calls == [profile]
    result.raster_view.clear()
    assert result.raster_view == context.view
    result.close()
    result.close()
    assert image.closes == 1


@pytest.mark.parametrize("target", ["live", "archive"])
@pytest.mark.parametrize("kind", ["cancel", "interrupt", "system_exit"])
@pytest.mark.parametrize("secondary", [None, KeyboardInterrupt])
def test_late_refusal_closes_metadata_owner_once_preserving_cancellation(context, monkeypatch, target, kind, secondary):
    c = context
    failure = {"interrupt": KeyboardInterrupt("primary"), "system_exit": SystemExit("primary")}.get(kind)
    image, owner, calls, method = _preview(c, monkeypatch, target,
        failure=None if secondary is None else secondary("secondary"))
    def cancelled():
        if calls and failure is not None:
            raise failure
        return bool(calls)
    with pytest.raises(CropPreviewError if failure is None else type(failure)) as caught:
        method(cancel_requested=cancelled)
    if failure is None:
        assert caught.value.code == "preview_cancelled"
    else:
        assert caught.value is failure
    assert image.closes == 1 and calls == ["fit"]
    owner.close()
    assert image.closes == 1


@pytest.mark.parametrize("target", ["live", "archive"])
def test_metadata_owner_survives_until_outer_bookkeeping_finishes(context, monkeypatch, target):
    c = context
    failure = KeyboardInterrupt("late bookkeeping")
    image, owner, calls, method = _preview(c, monkeypatch, target, failure=SystemExit("secondary"))
    if target == "live":
        original = c.coordinator._preview
        class Preview:
            count = 0
            render_with_view = original.render_with_view
            @property
            def cleanup_uncertain(self):
                self.count += 1
                if self.count == 3:
                    raise failure
                return False
        # Static function remains unbound, preserving the injected signature.
        Preview.render_with_view = staticmethod(original.render_with_view)
        monkeypatch.setattr(c.coordinator, "_preview", Preview())
        with pytest.raises(KeyboardInterrupt) as caught:
            method()
    else:
        condition = c.service._condition
        class Condition:
            def __enter__(self):
                return condition.__enter__()
            def __exit__(self, *args):
                return condition.__exit__(*args)
            def notify_all(self):
                raise failure
        with monkeypatch.context() as local:
            local.setattr(c.service, "_condition", Condition())
            with pytest.raises(KeyboardInterrupt) as caught:
                method()
    assert caught.value is failure and image.closes == 1 and calls == ["fit"]
    owner.close()
    assert image.closes == 1


def test_missing_metadata_port_refuses_without_legacy_render(context):
    with pytest.raises(ValueError):
        context.service.open_with_view(context.saved["pack_id"])


@pytest.mark.parametrize("target", ["live", "archive"])
@pytest.mark.parametrize("kind", ["pil_only", "none", "owner_subclass"])
def test_wrong_metadata_return_type_refuses_inside_disposal_guard(context, monkeypatch, target, kind):
    c = context
    image = _Image(KeyboardInterrupt("secondary close"))
    if kind == "owner_subclass":
        class Subclass(CropRasterPreview):
            pass
        returned = Subclass(image, c.view)
    else:
        returned = image if kind == "pil_only" else None
    calls = []
    def render(*_args, **_kwargs):
        calls.append(True)
        return returned
    if target == "live":
        c.coordinator._preview = NS(cleanup_uncertain=False, render_with_view=render,
            render=lambda *_a, **_k: pytest.fail("fallback"))
        with pytest.raises(CropPreviewError) as caught:
            c.coordinator.render_crop_preview_with_view(c.scope)
    else:
        monkeypatch.setattr(c.service, "_render_with_view", render)
        with pytest.raises(CropPreviewError) as caught:
            c.service.open_with_view(c.saved["pack_id"])
    assert caught.value.code == "preview_unavailable" and calls == [True]
    assert image.closes == (0 if kind == "none" else 1)


@pytest.mark.parametrize("target", ["live", "archive"])
def test_metadata_failure_before_transfer_is_not_closed_again_or_retried(context, monkeypatch, target):
    c = context
    image = _Image()
    owner = CropRasterPreview(image, c.view)
    calls = []
    def render(*_a, **_k):
        calls.append(True)
        owner.close()
        raise CropPreviewError(code="input_changed")
    if target == "live":
        c.coordinator._preview = NS(cleanup_uncertain=False, render_with_view=render,
            render=lambda *_a, **_k: pytest.fail("fallback"))
        with pytest.raises(CropPreviewError) as caught:
            c.coordinator.render_crop_preview_with_view(c.scope)
    else:
        monkeypatch.setattr(c.service, "_render_with_view", render)
        with pytest.raises(CropPreviewError) as caught:
            c.service.open_with_view(c.saved["pack_id"])
    assert caught.value.code == "input_changed" and calls == [True] and image.closes == 1


@pytest.mark.parametrize("target", ["live", "archive"])
def test_authoring_observed_aba_reset_changes_history_even_when_text_matches(context, target):
    c = context
    first = _author(c, target, journal=c.journal)
    after = _author(c, target, journal=first["journal"], reset_anchors=True)
    assert after["declaration"] == first["declaration"]
    assert after["declaration_sha256"] == first["declaration_sha256"]
    assert after["journal"]["head_sha256"] != first["journal"]["head_sha256"]
    assert after["journal"]["revisions"][-1]["reset_anchors"] is True


@pytest.mark.parametrize("target", ["live", "archive"])
@pytest.mark.parametrize("operation", ["author", "compare"])
def test_changed_expected_pair_or_parent_precedes_journal_replay(context, target, operation, monkeypatch):
    c = context
    if target == "live":
        snapshot = c.coordinator._crop_pair_snapshot
        def changed(*args):
            view, runs, bundles = snapshot(*args)
            view["pair_sha256"] = "0" * 64
            return view, runs, bundles
        monkeypatch.setattr(c.coordinator, "_crop_pair_snapshot", changed)
    else:
        c.saved["manifest_sha256"] = "0" * 64
    monkeypatch.setattr(history, "append_crop_journal_declaration", lambda *_a, **_k: pytest.fail("stale append"))
    monkeypatch.setattr(comparison, "build_crop_reference_v2", lambda *_a, **_k: pytest.fail("stale review"))
    with pytest.raises(ValueError):
        (_author if operation == "author" else _compare)(c, target)


def test_author_action_uses_one_cumulative_history_replay(context, monkeypatch):
    c = context
    original = history._replay
    calls = []
    def replay(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)
    monkeypatch.setattr(history, "_replay", replay)
    _author(c, "live", journal=c.journal, annotation_changes=[jf._add(c)], views=[c.view])
    assert calls == [True]


def test_v1_upgrade_and_v2_revision_keep_raw_archives_and_parent_history(context):
    c = context
    saved = _save(c)
    snapshot = c.service._store.read_for_revision(saved["pack_id"])
    assert snapshot["evidence"]["manifest"]["schema_version"] == 2
    assert snapshot["evidence"]["manifest"]["parent_pack_sha256"] == c.saved["manifest_sha256"]
    assert snapshot["evidence"]["review"]["reviewed"] is None
    assert snapshot["evidence"]["review"]["draft"]["uncertainty"] == _uncertainty(c)
    for key, raw in c.original["files"].items():
        if key != "review.json":
            assert snapshot["pack"]["files"][key] == raw
    child = c.service.save_revision_v2(saved["pack_id"], expected_pack_sha256=saved["manifest_sha256"],
        reference="not liable", critical_tokens_text="not liable", uncertainty=_uncertainty(c),
        verify_current=lambda: True)
    assert c.service._store.read(child["pack_id"])["manifest"]["parent_pack_sha256"] == saved["manifest_sha256"]
    assert len(c.service.catalog()) == 3


@pytest.mark.parametrize("operation", ["open", "compare", "save_revision"])
def test_old_api_explicitly_refuses_v2_parent_without_stripping_uncertainty(context, operation):
    c = context
    saved = _save(c)
    with pytest.raises(ValueError):
        if operation == "open":
            c.service.open(saved["pack_id"])
        elif operation == "compare":
            c.service.compare(saved["pack_id"], expected_pack_sha256=saved["manifest_sha256"],
                              reference="", critical_tokens=[], confirmed=True)
        else:
            c.service.save_revision(saved["pack_id"], expected_pack_sha256=saved["manifest_sha256"],
                reference="", critical_tokens_text="", verify_current=lambda: True)
    assert len(c.service.catalog()) == 2


@pytest.mark.parametrize("boundary", ["builder", "commit"])
def test_parent_authored_mutation_before_commit_never_publishes_complete_child(context, monkeypatch, boundary):
    c = context
    folder = c.service._store._entries[c.saved["pack_id"]].path
    mutated = []
    def mutate():
        (folder / "review.json").write_bytes(b"synthetic changed parent")
        mutated.append(True)
    if boundary == "builder":
        original = policy_v2.build_crop_review_pack_v2
        def builder(**kwargs):
            result = original(**kwargs)
            mutate()
            return result
        monkeypatch.setattr(policy_v2, "build_crop_review_pack_v2", builder)
    else:
        original = c.service._store.check_revision_parent
        checks = []
        def check(*args, **kwargs):
            checks.append(True)
            if len(checks) == 5:
                mutate()
            return original(*args, **kwargs)
        monkeypatch.setattr(c.service._store, "check_revision_parent", check)
    with pytest.raises(ValueError):
        _save(c)
    assert mutated == [True]
    assert not any(row["status"] == "verified_complete" for row in c.service.catalog()
                   if row["pack_id"] != c.saved["pack_id"])


def test_revision_continuity_receives_the_one_actual_raw_snapshot(context, monkeypatch):
    c = context
    original_read = c.service._store.read_for_revision
    original_validate = policy_v2.validate_crop_pack_revision_v2
    snapshots, validations = [], []
    def read(*args):
        result = original_read(*args)
        snapshots.append(result)
        return result
    def validate(parent, child):
        assert parent is snapshots[0]
        assert parent["manifest_bytes"] == archive_host.packs.encode_manifest(c.original["manifest"])
        validations.append(True)
        return original_validate(parent, child)
    monkeypatch.setattr(c.service._store, "read_for_revision", read)
    monkeypatch.setattr(policy_v2, "validate_crop_pack_revision_v2", validate)
    _save(c)
    assert len(snapshots) == 1 and validations == [True]


@pytest.mark.parametrize("changed", ["pair", "source", "recovery", None])
def test_live_save_keeps_explicit_capture_binding_and_never_invents_parent(context, changed):
    c = context
    captured = {"pair": copy.deepcopy(c.pair), "baseline": c.options["baseline"], "retry": c.options["retry"]}
    if changed:
        captured["pair"][{"pair": "pair_sha256", "source": "source_sha256",
                          "recovery": "baseline_recovery_sha256"}[changed]] = "0" * 64
    calls = []
    def capture(*ids, **kwargs):
        assert ids == c.ids and kwargs == {"expected_pair_sha256": c.pair["pair_sha256"]}
        calls.append(True)
        return captured
    def save():
        return c.service.save_live_v2(NS(capture_crop_archives=capture), *c.ids,
            expected_pair_sha256=c.pair["pair_sha256"], reference="not liable", critical_tokens_text="not liable",
            uncertainty=_uncertainty(c), verify_current=lambda: True)
    if changed:
        with pytest.raises(ValueError):
            save()
        assert len(c.service.catalog()) == 1
    else:
        saved = save()
        assert c.service._store.read(saved["pack_id"])["manifest"]["parent_pack_sha256"] is None
    assert calls == [True] and not c.coordinator._runs


@pytest.mark.parametrize("operation", ["author", "compare"])
@pytest.mark.parametrize("attack", ["none", "genesis", "base", "truncated", "missing_view", "changed_view"])
def test_saved_v2_history_cannot_be_erased_before_transient_authoring_or_scoring(context, monkeypatch, operation, attack):
    c = context
    journal = jf._append(c, c.journal, jf._add(c))
    c.saved = _save(c, uncertainty=_uncertainty(c, journal))
    value = copy.deepcopy(journal)
    if attack == "none":
        value = None
    elif attack == "genesis":
        value = copy.deepcopy(c.journal)
    elif attack == "base":
        value["base"]["reference"] = "rewritten base"
    elif attack == "truncated":
        value["revisions"].clear()
    elif attack == "missing_view":
        value["views"].clear()
    else:
        value["views"][0]["rgb_sha256"] = "0" * 64
    monkeypatch.setattr(history, "append_crop_journal_declaration", lambda *_a, **_k: pytest.fail("erased history authored"))
    monkeypatch.setattr(comparison, "build_crop_reference_v2", lambda *_a, **_k: pytest.fail("erased history reviewed"))
    monkeypatch.setattr(crops, "compare_ocr", lambda *_a, **_k: pytest.fail("unresolved parent scored"))
    with pytest.raises(ValueError):
        (_author if operation == "author" else _compare)(c, "archive", journal=value)
    assert len(c.service.catalog()) == 2


@pytest.mark.parametrize("operation", ["author", "compare"])
def test_dirty_parent_cannot_return_clean_transient_state_without_appended_reset(context, operation, monkeypatch):
    c = context
    c.saved = _save(c, uncertainty=_uncertainty(c, dirty=True))
    if operation == "compare":
        monkeypatch.setattr(comparison, "build_crop_reference_v2", lambda *_a, **_k: pytest.fail("dirty journal reviewed"))
    with pytest.raises(ValueError):
        (_author if operation == "author" else _compare)(c, "archive", journal=c.journal)
    assert c.service._store.read(c.saved["pack_id"])["review"]["draft"]["uncertainty"]["dirty"] is True


def test_dirty_parent_author_can_append_reset_then_compare_without_silent_restore(context):
    c = context
    c.saved = _save(c, uncertainty=_uncertainty(c, dirty=True))
    after = _author(c, "archive", journal=c.journal, reset_anchors=True)
    assert after["journal"]["revisions"][-1]["reset_anchors"] is True
    assert after["journal"]["head_sha256"] != c.journal["head_sha256"]
    result = _compare(c, "archive", journal=after["journal"])
    assert result["reference"]["journal"] == after["journal"]


def test_later_suffix_reset_satisfies_transient_dirty_parent_continuity(context):
    c = context
    c.saved = _save(c, uncertainty=_uncertainty(c, dirty=True))
    appended = jf._append(c, c.journal, jf._add(c))
    reset = jf._append(c, appended, reset=True)
    result = _compare(c, "archive", journal=reset)
    assert result["comparison"]["reason"] == "reference_uncertain"
    assert result["reference"]["journal"]["revisions"][0]["reset_anchors"] is False
    assert result["reference"]["journal"]["revisions"][1]["reset_anchors"] is True


def test_v2_null_journal_parent_still_allows_explicit_genesis(context):
    c = context
    pack = policy_v2.build_crop_review_pack_v2(**c.options,
        uncertainty={"journal": None, "annotations": [], "dirty": False})
    c.saved = c.service._store.publish(pack)
    result = _author(c, "archive")
    assert result["journal"] == c.journal and result["declaration"]["annotations"] == []


@pytest.mark.parametrize("boundary", ["readback", "verify"])
def test_archive_metadata_late_input_failure_disposes_returned_owner(context, monkeypatch, boundary):
    c = context
    image, owner, calls, method = _preview(c, monkeypatch, "archive", failure=KeyboardInterrupt("secondary"))
    failure = ValueError("primary retained-input refusal")
    original = c.service._read if boundary == "readback" else c.service._verify
    def changed(*args, **kwargs):
        if calls:
            raise failure
        return original(*args, **kwargs)
    monkeypatch.setattr(c.service, "_read" if boundary == "readback" else "_verify", changed)
    with pytest.raises(ValueError) as caught:
        method()
    assert caught.value is failure and image.closes == 1 and calls == ["fit"]
    owner.close()
    assert image.closes == 1


def test_recovery_version_is_explicit_and_v2_history_survives_damaged_proof(context, monkeypatch):
    c = context
    journal = jf._append(c, c.journal, jf._add(c))
    saved = _save(c, uncertainty=_uncertainty(c, journal))
    folder = c.service._store._entries[saved["pack_id"]].path
    (folder / "baseline" / "artifacts" / "report.json").write_bytes(b"synthetic damaged proof")
    monkeypatch.setattr(archive_host.packs, "_archives", lambda *_a, **_k: pytest.fail("recovery read proof"))
    monkeypatch.setattr(crops, "compare_ocr", lambda *_a, **_k: pytest.fail("recovery scored"))
    with pytest.raises(ValueError):
        c.service.recover_draft(saved["pack_id"])
    recovered = c.service.recover_draft_v2(saved["pack_id"])
    assert recovered["draft"]["uncertainty"] == _uncertainty(c, journal)
    assert recovered["status"] == "unverified_saved_draft"
    assert set(recovered) == {"draft", "scope", "pack_sha256", "status", "requires_attention",
                              "canonical_extraction_modified"}
    assert c.service.recover_draft_v2(c.saved["pack_id"]) == c.service.recover_draft(c.saved["pack_id"])


@pytest.mark.parametrize("attack", ["mutated_input", "returned_head"])
def test_compare_rechecks_detached_journal_before_scoring(context, monkeypatch, attack):
    c = context
    journal = jf._append(c, c.journal, jf._add(c))
    c.saved = _save(c, uncertainty=_uncertainty(c, journal))
    supplied = copy.deepcopy(journal)
    original = comparison.build_crop_reference_v2
    calls = []
    def build(*args, **kwargs):
        calls.append("builder")
        if attack == "mutated_input":
            kwargs["journal"].clear()
            kwargs["journal"].update(copy.deepcopy(c.journal))
        else:
            kwargs["journal"] = copy.deepcopy(c.journal)
        # Both are fully valid for the source/occurrence in isolation, but
        # discard the saved parent's unresolved annotation/history prefix.
        return original(*args, **kwargs)
    monkeypatch.setattr(comparison, "build_crop_reference_v2", build)
    monkeypatch.setattr(comparison, "compare_crop_candidates_v2", lambda *_a, **_k: pytest.fail("rebased head compared"))
    monkeypatch.setattr(crops, "compare_ocr", lambda *_a, **_k: pytest.fail("unresolved parent scored"))
    with pytest.raises(ValueError):
        _compare(c, "archive", journal=supplied)
    assert calls == ["builder"]
    retained = c.service._store.read(c.saved["pack_id"])["review"]["draft"]["uncertainty"]["journal"]
    assert retained == journal and len(c.service.catalog()) == 2
