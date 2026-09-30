"""Host adapter controls with generated archives and explicitly inert previews.

Workspace byte verification/rendering are doubles; these are not PDF/native or
browser observations. Most cases use the named archive-policy double. The two
complete historical cases instead run all production archive/report validators.
"""

import copy
import hashlib
from pathlib import Path
import threading
from types import SimpleNamespace as NS

import pytest

import ocr_crop_review_pack as policy
import ocr_review_crop_packs as host
from ocr_crop_review_pack_io import CropReviewPackIOError
from ocr_crop_review_runtime import CropPreviewError
from ocr_review_runtime import ReviewWorkspace
from test_ocr_crop_review_pack import policy_pack  # noqa: F401
from test_ocr_disposition_archive import archive_fixture


def _workspace(binding, checks):
    workspace = object.__new__(ReviewWorkspace)
    workspace.document = NS(**binding)
    workspace.pdf_path = Path("synthetic-source.pdf").absolute()
    workspace.recovery_path = Path("synthetic-recovery.json").absolute()
    workspace.output_dir = Path("synthetic-output").absolute()
    workspace.verify_inputs = lambda: checks.append("inputs")
    return workspace


@pytest.fixture
def service_case(tmp_path, policy_pack):  # noqa: F811
    root = tmp_path / "packs"
    root.mkdir()
    checks, renders = [], []
    workspace = _workspace(policy_pack.binding, checks)
    image = pytest.importorskip("PIL.Image").new("RGB", (30, 20), "white")

    def render(scope, *, cancel_requested, preview_profile="fit"):
        assert preview_profile == "fit"
        assert cancel_requested() is False
        renders.append(copy.deepcopy(scope))
        return image.copy()

    service = host.CropReviewPackService(workspace, root, render_preview=render,
                                        preview_private_root=tmp_path / "inert-private-preview")
    pack = policy.build_crop_review_pack(**policy_pack.options(reviewed=True))
    saved = service._store.publish(pack)
    result = NS(service=service, workspace=workspace, renders=renders, checks=checks, root=root,
                saved=saved, pack=pack, options=policy_pack.options, image=image)
    yield result
    service.close()


def test_open_fresh_selected_view_is_detached_and_has_no_raw_archive_or_execution_ticket(service_case):
    c = service_case
    view = c.service.open(c.saved["pack_id"])
    assert set(view) == {"pack_id", "pack_sha256", "scope", "baseline", "retry", "draft",
                         "historical_reviewed", "image", "validation_scope", "requires_attention",
                         "canonical_extraction_modified"}
    assert view["pack_sha256"] == c.saved["manifest_sha256"]
    assert view["image"].mode == "RGB" and c.renders == [view["scope"]]
    assert view["draft"] == {"reference": "not liable", "critical_tokens_text": "not liable"}
    assert view["baseline"]["text"] == "liable" and view["retry"]["text"] == "not liable"
    assert view["historical_reviewed"] is not None
    assert view["validation_scope"] == "historical_local_declarations"
    assert view["requires_attention"] is True and view["canonical_extraction_modified"] is False
    assert set(view["baseline"]) == {"region_id", "report_sha256", "request_sha256", "operation",
                                     "status", "text", "geometry", "recipe", "configuration"}
    view["draft"]["reference"] = "detached edit"
    assert c.service.open(c.saved["pack_id"])["draft"]["reference"] == "not liable"


@pytest.mark.parametrize("profile", [None, "fit", "dpi288", "dpi576"])
def test_archive_preview_profile_is_transient_and_forwards_without_changing_saved_scope(service_case, monkeypatch, profile):
    """Named archive-policy double with real service/store; inert image only."""
    c = service_case
    original = c.service.open(c.saved["pack_id"])
    before = c.service._store.read(c.saved["pack_id"])
    calls = []
    def render(scope, *, cancel_requested, preview_profile):
        assert not cancel_requested()
        calls.append((copy.deepcopy(scope), preview_profile))
        return c.image.copy()
    monkeypatch.setattr(c.service, "_render", render)
    kwargs = {} if profile is None else {"preview_profile": profile}
    actual = c.service.open(c.saved["pack_id"], **kwargs)
    assert calls == [(original["scope"], "fit" if profile is None else profile)]
    assert {key: value for key, value in actual.items() if key != "image"} == {
        key: value for key, value in original.items() if key != "image"}
    assert c.service._store.read(c.saved["pack_id"]) == before
    assert actual["requires_attention"] is True and actual["canonical_extraction_modified"] is False


@pytest.mark.parametrize("profile", [None, True, 288, [], {}, "dpi144", "FIT", "dpi288 "])
def test_archive_invalid_preview_profile_precedes_evidence_source_callback_and_render(service_case, monkeypatch, profile):
    c = service_case
    def forbidden(*_a, **_k):
        pytest.fail("invalid profile reached evidence/source/callback/render")
    monkeypatch.setattr(c.service, "_read", forbidden)
    monkeypatch.setattr(c.service, "_render", forbidden)
    monkeypatch.setattr(c.workspace, "verify_inputs", forbidden)
    with pytest.raises(CropPreviewError) as caught:
        c.service.open(c.saved["pack_id"], preview_profile=profile, cancel_requested=forbidden)
    assert caught.value.code == "scope_validation"
    assert not c.service._active and not c.service._uncertain


@pytest.mark.parametrize("profile", ["dpi288", "dpi576"])
def test_archive_detail_refusal_has_no_fit_fallback_or_saved_state_change(service_case, monkeypatch, profile):
    c = service_case
    before = c.service._store.read(c.saved["pack_id"])
    calls = []
    def render(_scope, *, cancel_requested, preview_profile):
        assert not cancel_requested()
        calls.append(preview_profile)
        raise CropPreviewError(code="raster_limit")
    monkeypatch.setattr(c.service, "_render", render)
    with pytest.raises(CropPreviewError) as caught:
        c.service.open(c.saved["pack_id"], preview_profile=profile)
    assert caught.value.code == "raster_limit" and calls == [profile]
    assert c.service._store.read(c.saved["pack_id"]) == before


def test_recover_draft_skips_damaged_evidence_and_never_renders_or_returns_metrics(service_case):
    c = service_case
    folder = c.service._store._entries[c.saved["pack_id"]].path
    (folder / "baseline" / "artifacts" / "report.json").write_bytes(b"generated broken proof")
    with pytest.raises(CropReviewPackIOError):
        c.service.open(c.saved["pack_id"])
    recovered = c.service.recover_draft(c.saved["pack_id"])
    assert set(recovered) == {"draft", "scope", "pack_sha256", "status", "requires_attention",
                              "canonical_extraction_modified"}
    assert recovered["status"] == "unverified_saved_draft" and not c.renders


@pytest.mark.parametrize("when", ["before_read", "during_render", "after_readback"])
def test_cancelled_open_preserves_allowlisted_code_without_accepting_image(service_case, monkeypatch, when):
    c = service_case
    cancelled = [when == "before_read"]
    original_read = c.service._read
    reads = []

    def read(*args):
        reads.append(args)
        value = original_read(*args)
        if when == "after_readback" and len(reads) == 2:
            cancelled[0] = True
        return value

    def render(_scope, *, cancel_requested, preview_profile="fit"):
        assert preview_profile == "fit"
        assert not cancel_requested()
        if when == "during_render":
            cancelled[0] = True
        return c.image.copy()

    monkeypatch.setattr(c.service, "_read", read)
    monkeypatch.setattr(c.service, "_render", render)
    with pytest.raises(CropPreviewError) as caught:
        c.service.open(c.saved["pack_id"], cancel_requested=lambda: cancelled[0])
    assert caught.value.code == "preview_cancelled"
    assert not reads if when == "before_read" else len(reads) == 2


@pytest.mark.parametrize("mutation", ["source", "archive", "declaration"])
def test_change_during_render_refuses_open(service_case, monkeypatch, mutation):
    c = service_case

    def render(*_args, **_kwargs):
        if mutation == "source":
            c.workspace.verify_inputs = lambda: (_ for _ in ()).throw(ValueError("changed source"))
        elif mutation == "declaration":
            c.workspace.document.source_sha256 = "d" * 64
        else:
            folder = c.service._store._entries[c.saved["pack_id"]].path
            (folder / "review.json").write_bytes(b"generated changed reference")
        return c.image.copy()

    monkeypatch.setattr(c.service, "_render", render)
    with pytest.raises(ValueError):
        c.service.open(c.saved["pack_id"])


@pytest.mark.parametrize("confirmed", [False, None, 1, "true"])
def test_archived_review_does_not_authorize_new_comparison(service_case, confirmed):
    c = service_case
    with pytest.raises(ValueError):
        c.service.compare(c.saved["pack_id"], expected_pack_sha256=c.saved["manifest_sha256"],
                          reference="not liable", critical_tokens=[], confirmed=confirmed)


def test_new_comparison_recomputes_unchanged_scorer_and_checks_current_evidence(service_case, monkeypatch):
    c = service_case
    result = c.service.compare(c.saved["pack_id"], expected_pack_sha256=c.saved["manifest_sha256"],
                               reference="not liable", critical_tokens=["not liable"], confirmed=True)
    assert result["comparison"] == c.options(reviewed=True)["reviewed"]["comparison"]
    assert result["reference"] == c.options(reviewed=True)["reviewed"]["reference"]
    original = host._comparison

    def compare(*args):
        value = original(*args)
        c.workspace.document.recovery_sha256 = "c" * 64
        return value

    monkeypatch.setattr(host, "_comparison", compare)
    with pytest.raises(ValueError):
        c.service.compare(c.saved["pack_id"], expected_pack_sha256=c.saved["manifest_sha256"],
                          reference="not liable", critical_tokens=[], confirmed=True)


def test_revision_preserves_raw_evidence_but_drops_old_review_and_keeps_partial_draft(service_case):
    c = service_case
    guards = []
    saved = c.service.save_revision(c.saved["pack_id"], expected_pack_sha256=c.saved["manifest_sha256"],
        reference="  unfinished\n", critical_tokens_text="\npartial\n", verify_current=lambda: guards.append(True))
    child = c.service._store.read_for_revision(saved["pack_id"])
    assert child["evidence"]["manifest"]["parent_pack_sha256"] == c.saved["manifest_sha256"]
    assert child["evidence"]["review"]["reviewed"] is None
    assert child["evidence"]["review"]["draft"] == {
        "reference": "  unfinished\n", "critical_tokens_text": "\npartial\n"}
    assert saved["pack_id"] != c.saved["pack_id"] and len(guards) > 25
    for name, raw in c.pack["files"].items():
        if name != "review.json":
            assert child["pack"]["files"][name] == raw
    assert c.service._store.read(c.saved["pack_id"])["review"]["reviewed"] is not None


def test_revision_accepts_only_matching_recomputed_review(service_case):
    c = service_case
    reviewed = c.options(reviewed=True)["reviewed"]
    saved = c.service.save_revision(c.saved["pack_id"], expected_pack_sha256=c.saved["manifest_sha256"],
        reference="not liable", critical_tokens_text="not liable", reviewed=reviewed, verify_current=lambda: True)
    assert c.service._store.read(saved["pack_id"])["review"]["reviewed"] == reviewed
    reviewed["reference"]["reference"] = "forged"
    with pytest.raises(ValueError):
        c.service.save_revision(c.saved["pack_id"], expected_pack_sha256=c.saved["manifest_sha256"],
            reference="not liable", critical_tokens_text="not liable", reviewed=reviewed, verify_current=lambda: True)
    assert len(c.service.catalog()) == 2


@pytest.mark.parametrize("when", [1, 2, 3, 8])
def test_save_guard_refusal_prevents_verified_publication_without_autoretry(service_case, when):
    c = service_case
    calls = []

    def guard():
        calls.append(True)
        return len(calls) < when

    with pytest.raises(ValueError):
        c.service.save_revision(c.saved["pack_id"], expected_pack_sha256=c.saved["manifest_sha256"],
            reference="draft", critical_tokens_text="", verify_current=guard)
    assert len(calls) == when
    assert all(row["status"] != "verified_complete" for row in c.service.catalog()
               if row["pack_id"] != c.saved["pack_id"])


@pytest.mark.parametrize("action", ["open", "recover", "compare", "save"])
def test_unknown_path_like_id_or_stale_binding_cannot_select_an_archive(service_case, action):
    c = service_case
    for key in (str(c.root), "pack-" + c.saved["pack_id"], "f" * 32):
        with pytest.raises(ValueError):
            if action == "open":
                c.service.open(key)
            elif action == "recover":
                c.service.recover_draft(key)
            elif action == "compare":
                c.service.compare(key, expected_pack_sha256=c.saved["manifest_sha256"],
                                  reference="", critical_tokens=[], confirmed=True)
            else:
                c.service.save_revision(key, expected_pack_sha256=c.saved["manifest_sha256"],
                    reference="", critical_tokens_text="", verify_current=lambda: True)
    with pytest.raises(ValueError):
        c.service.save_revision(c.saved["pack_id"], expected_pack_sha256="e" * 64,
            reference="", critical_tokens_text="", verify_current=lambda: True)


def test_live_capture_uses_only_explicit_pair_and_preserves_source_binding(service_case):
    c = service_case
    calls = []
    options = c.options()

    def capture(*ids, **kwargs):
        calls.append((ids, kwargs))
        return {"pair": {"pair_sha256": "d" * 64, **{key: options[key] for key in ["source_sha256"]},
                "baseline_recovery_sha256": options["recovery_sha256"],
                "baseline": {"region_id": "a"}, "retry": {"region_id": "a"}},
                "baseline": options["baseline"], "retry": options["retry"]}

    saved = c.service.save_live(NS(capture_crop_archives=capture), "run-a", "item-a", "run-b", "item-b",
        expected_pair_sha256="d" * 64, reference="", critical_tokens_text="\n", verify_current=lambda: True)
    assert calls == [(('run-a', 'item-a', 'run-b', 'item-b'), {'expected_pair_sha256': 'd' * 64})]
    assert c.service._store.read(saved["pack_id"])["manifest"]["parent_pack_sha256"] is None
    assert c.service._store.read(saved["pack_id"])["review"]["reviewed"] is None
    with pytest.raises(ValueError):
        c.service.save_live(NS(capture_crop_archives=capture), "run-a", "item-a", "run-b", "item-b",
            expected_pair_sha256="e" * 64, reference="", critical_tokens_text="", verify_current=lambda: True)


def test_close_revokes_reads_without_closing_injected_preview(service_case):
    c = service_case
    assert c.service.close() is True and c.service.close() is True
    for action in (c.service.catalog, lambda: c.service.open(c.saved["pack_id"]),
                   lambda: c.service.recover_draft(c.saved["pack_id"])):
        with pytest.raises(ValueError):
            action()


@pytest.mark.parametrize("field", ["document", "pdf_path", "recovery_path", "output_dir"])
def test_equal_declarations_do_not_allow_replacing_fixed_workspace_generation(service_case, field):
    c = service_case
    original = getattr(c.workspace, field)
    replacement = copy.deepcopy(original) if field == "document" else original.with_name("replacement")
    setattr(c.workspace, field, replacement)
    with pytest.raises(ValueError):
        c.service.catalog()


def test_post_workspace_verification_rechecks_declared_generation(service_case):
    c = service_case
    c.workspace.verify_inputs = lambda: setattr(c.workspace, "document", copy.deepcopy(c.workspace.document))
    with pytest.raises(ValueError):
        c.service.catalog()


@pytest.mark.parametrize("timeout", [-1, 41, 10**1000, True, None, "1", float("inf"), float("nan")])
def test_close_deadline_is_bounded_without_revocation_on_invalid_argument(service_case, timeout):
    with pytest.raises(ValueError):
        service_case.service.close(timeout_seconds=timeout)
    assert service_case.service.catalog()


def test_close_waits_for_read_exit_without_holding_service_or_store_lock(service_case, monkeypatch):
    c = service_case
    entered, release, waiting = threading.Event(), threading.Event(), threading.Event()
    errors, shutdown = [], []
    original_read = c.service._store.read
    original_wait = c.service._condition.wait_for

    def held_read(key):
        entered.set()
        assert release.wait(5)
        return original_read(key)

    def wait(predicate, timeout):
        waiting.set()
        return original_wait(predicate, timeout)

    def opening():
        try:
            c.service.open(c.saved["pack_id"])
        except BaseException as error:
            errors.append(error)

    monkeypatch.setattr(c.service._store, "read", held_read)
    monkeypatch.setattr(c.service._condition, "wait_for", wait)
    worker = threading.Thread(target=opening)
    closer = threading.Thread(target=lambda: shutdown.append(c.service.close(timeout_seconds=5)))
    worker.start()
    try:
        assert entered.wait(5)
        with pytest.raises(ValueError):
            c.service.catalog()  # One admitted operation bounds retained cohorts.
        closer.start()
        assert waiting.wait(5) and not shutdown
    finally:
        release.set()
        worker.join(6)
        if closer.ident is not None:
            closer.join(6)
    assert not worker.is_alive() and not closer.is_alive()
    assert shutdown == [True] and len(errors) == 1 and isinstance(errors[0], ValueError)


def test_close_during_guarded_commit_cannot_claim_clean_and_late_cleanup_is_retained(service_case, monkeypatch):
    import ocr_crop_review_pack_io as store_io

    c = service_case
    entered, release = threading.Event(), threading.Event()
    failures = []
    original = store_io._publish_new_report

    def held_commit(temporary, destination):
        entered.set()
        assert release.wait(5)
        original(temporary, destination)
        c.service._store._cleanup_uncertain = True  # Explicit late-cleanup fault double.

    def saving():
        try:
            c.service.save_revision(c.saved["pack_id"], expected_pack_sha256=c.saved["manifest_sha256"],
                reference="draft", critical_tokens_text="", verify_current=lambda: True)
        except BaseException as error:
            failures.append(error)

    monkeypatch.setattr(store_io, "_publish_new_report", held_commit)
    worker = threading.Thread(target=saving)
    worker.start()
    try:
        assert entered.wait(5)
        assert c.service.close(timeout_seconds=0) is False
        assert worker.is_alive()
    finally:
        release.set()
        worker.join(6)
    assert not worker.is_alive() and failures
    assert c.service.close(timeout_seconds=0) is False
    assert len(list(c.root.iterdir())) == 2  # Original and partial child retained.


def test_cancelled_save_releases_active_slot_and_preserves_exception_identity(service_case):
    c = service_case
    cancellation = KeyboardInterrupt()

    def cancel():
        raise cancellation

    with pytest.raises(KeyboardInterrupt) as caught:
        c.service.save_revision(c.saved["pack_id"], expected_pack_sha256=c.saved["manifest_sha256"],
            reference="", critical_tokens_text="", verify_current=cancel)
    assert caught.value is cancellation and len(c.service.catalog()) == 1


@pytest.mark.parametrize("where", ["request", "drain", "preview_close"])
@pytest.mark.parametrize("kind", [RuntimeError, KeyboardInterrupt])
def test_any_shutdown_prelude_failure_latches_uncertainty_and_preserves_exception(service_case, monkeypatch, where, kind):
    c = service_case
    failure = kind("synthetic shutdown failure")

    def fail(*_a, **_kw):
        raise failure

    preview = NS(request_close=fail if where == "request" else lambda: None,
                 close=fail if where == "preview_close" else lambda **_kw: True)
    monkeypatch.setattr(c.service, "_owned_preview", preview)
    original_wait = c.service._condition.wait_for
    if where == "drain":
        monkeypatch.setattr(c.service._condition, "wait_for", fail)
    with pytest.raises(kind) as caught:
        c.service.close(timeout_seconds=0)
    assert caught.value is failure and c.service._uncertain is True
    preview.request_close = lambda: None
    preview.close = lambda **_kw: True
    monkeypatch.setattr(c.service._condition, "wait_for", original_wait)
    assert c.service.close(timeout_seconds=0) is False


def test_nonblocking_request_close_revokes_without_claiming_drain(service_case):
    c = service_case
    assert c.service.request_close() is None
    with pytest.raises(ValueError):
        c.service.catalog()
    assert c.service.close(timeout_seconds=0) is True


@pytest.mark.parametrize("clean", [True, False])
def test_owned_preview_is_closed_and_cleanup_failure_is_visible(tmp_path, monkeypatch, policy_pack, clean):  # noqa: F811
    import ocr_crop_preview_supervision

    calls = []
    preview = NS(render=lambda *_a, **_kw: None, private_root=tmp_path / "inert-owned-preview",
                 render_with_view=lambda *_a, **_kw: None,
                 request_close=lambda: calls.append("request"),
                 close=lambda **_kw: calls.append("close") or clean)
    monkeypatch.setattr(ocr_crop_preview_supervision, "CropPreviewController", lambda _workspace: preview)
    service = host.CropReviewPackService(_workspace(policy_pack.binding, []), tmp_path)
    assert service.preview_private_root == preview.private_root
    assert service.close() is clean and calls == ["request", "close"]


@pytest.mark.parametrize(("readings", "expected"), [((1000., 1001., 1002.5), 7.5), ((1000., 1001., 1020.), 0.)])
def test_owned_preview_close_receives_remaining_service_budget(tmp_path, monkeypatch, policy_pack,  # noqa: F811
                                                                readings, expected):
    import ocr_crop_preview_supervision

    seen = []
    preview = NS(render=lambda *_a, **_kw: None, private_root=tmp_path / "inert-owned-preview",
                 render_with_view=lambda *_a, **_kw: None, request_close=lambda: None,
                 close=lambda *, timeout_seconds: seen.append(timeout_seconds) or True)
    monkeypatch.setattr(ocr_crop_preview_supervision, "CropPreviewController", lambda _workspace: preview)
    service = host.CropReviewPackService(_workspace(policy_pack.binding, []), tmp_path)
    clock = iter(readings)
    # Replace the service clock capability only, after construction.
    monkeypatch.setattr(host, "time", NS(monotonic=lambda: next(clock)))
    assert service.close(timeout_seconds=10) is True
    assert seen == [expected] and service._uncertain is False


def test_service_close_budget_never_exceeds_owned_preview_bound():
    import ocr_crop_preview_supervision

    assert host.MAX_CLOSE_SECONDS <= ocr_crop_preview_supervision.CLOSE_TIMEOUT_SECONDS


@pytest.mark.parametrize("timeout", [None, 40.])
def test_owned_preview_close_is_bounded_when_rounded_remaining_time_exceeds_budget(tmp_path, monkeypatch,
                                                                                   policy_pack, timeout):  # noqa: F811
    import ocr_crop_preview_supervision

    scratch = tmp_path / "host-temp"
    scratch.mkdir()
    monkeypatch.setattr(ocr_crop_preview_supervision.tempfile, "gettempdir", lambda: str(scratch))
    workspace = _workspace(policy_pack.binding, [])
    workspace.output_dir = tmp_path / "review-output"
    workspace.output_dir.mkdir()
    (tmp_path / "packs").mkdir()
    # The real owned controller keeps its actual clock, bound check and cleanup.
    service = host.CropReviewPackService(workspace, tmp_path / "packs")
    controller, seen = service._owned_preview, []
    preview_root, real_close = controller.private_root, controller.close
    assert preview_root.parent == scratch and preview_root.is_dir()
    monkeypatch.setattr(controller, "close", lambda **kw: seen.append(kw) or real_close(**kw))
    # Same-tick readings straddling a binary precision boundary round upward.
    now = 131071.999
    assert (now + 40.) - now > 40.
    # Replace the service clock capability only, after construction.
    monkeypatch.setattr(host, "time", NS(monotonic=lambda: now))
    assert service.close(**({} if timeout is None else {"timeout_seconds": timeout})) is True
    assert seen == [{"timeout_seconds": 40.}]
    assert service._uncertain is False and not preview_root.exists()


@pytest.mark.parametrize("operation", ["regions", "hardscan"])
def test_complete_generated_historical_archives_open_compare_revise_without_native_runtime(tmp_path, operation):
    before = archive_fixture(operation, text="before")
    after = archive_fixture(operation, text="after", dpi=400, state="unavailable")
    binding = {"source_sha256": before["source_sha256"], "page_count": before["page_count"],
               "recovery_sha256": hashlib.sha256(before["retained_inputs"]["recovery.json"]).hexdigest()}
    sides = {side: {key: value[key] for key in ("artifacts", "retained_inputs")}
             for side, value in (("baseline", before), ("retry", after))}
    pack = policy.build_crop_review_pack(**sides, **binding, baseline_region_id="r00", retry_region_id="r00",
                                         reference="draft", critical_tokens_text="")
    image = pytest.importorskip("PIL.Image").new("RGB", (40, 20), "white")
    service = host.CropReviewPackService(_workspace(binding, []), tmp_path,
        render_preview=lambda *_a, **_kw: image.copy(), preview_private_root=tmp_path / "inert-preview")
    try:
        saved = service._store.publish(pack)
        view = service.open(saved["pack_id"])
        assert view["retry"]["text"] is None and view["historical_reviewed"] is None
        score = service.compare(saved["pack_id"], expected_pack_sha256=saved["manifest_sha256"],
                                reference="before", critical_tokens=[], confirmed=True)
        reviewed = {"reference": score["reference"], "comparison": score["comparison"],
                    "source_image": {"width": 40, "height": 20, "rgb_sha256": "f" * 64}}
        child = service.save_revision(saved["pack_id"], expected_pack_sha256=saved["manifest_sha256"],
            reference="before", critical_tokens_text="", reviewed=reviewed, verify_current=lambda: True)
        reopened = service.open(child["pack_id"])
        assert reopened["historical_reviewed"] == reviewed and reopened["requires_attention"] is True
    finally:
        assert service.close() is True
