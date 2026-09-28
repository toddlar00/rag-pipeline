"""Server-owned approvals and contained run lifecycle; no PDF/model loading.

The supervisor is a deterministic test seam. Page bundle controls retain real
request, orchestration, receipt, diagnostic and publication validators. Three
optional route controls use the established inert-session upstream fixture, not
native model constructors or OCR recognition.
"""

import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import threading
from types import SimpleNamespace as NS

import pytest

import ocr_detection_disposition_io as io
import ocr_recovery
import ocr_review_execution as execution
from ocr_review_runtime import ReviewWorkspace
from test_ocr_detection_disposition_io import case as case, FailedReader
from test_ocr_disposition_bundle_routes import routes as routes
from test_ocr_detection_disposition_runtime import stack as stack, harness as harness, upstream as upstream
from tools import diagnose_ocr_dispositions as cli


@pytest.fixture
def preview_roots(tmp_path_factory, monkeypatch):
    """Own an external storage parent without modifying shared OS-temp state."""
    import ocr_crop_preview_supervision as preview_backend

    parent = tmp_path_factory.mktemp("ocr-preview-storage")
    initial = parent.stat()
    # Replace only this module's tempfile capability, not tempfile.gettempdir
    # process-wide (Gradio/pytest may independently use that shared module).
    monkeypatch.setattr(preview_backend, "tempfile", NS(gettempdir=lambda: str(parent)))
    try:
        yield parent
    finally:
        current = parent.lstat()
        assert (current.st_dev, current.st_ino) == (initial.st_dev, initial.st_ino)
        assert not list(parent.iterdir()), "coordinator left owned preview roots; no cleanup was suppressed"


@pytest.fixture
def host(case, tmp_path, preview_roots):
    baseline = ocr_recovery.build_recovery_report(FailedReader(case.source),
        source_sha256=hashlib.sha256(case.source.read_bytes()).hexdigest(),
        policy=ocr_recovery.RetryPolicy(), requested_pages=(1, 3))
    baseline["evidence_sha256"] = None
    path = tmp_path / "recovery.json"
    path.write_text(json.dumps(baseline), encoding="utf-8")
    workspace = ReviewWorkspace(case.source, path, tmp_path)
    coordinator = execution.ReviewRunCoordinator(workspace)
    try:
        assert coordinator.preview_private_root.parent == preview_roots
        yield NS(coordinator=coordinator, workspace=workspace, case=case)
    finally:
        # Deliberate OCR-cleanup uncertainty is asserted by its owning test;
        # the independent preview_roots finalizer still requires actual removal.
        coordinator.close()


def _prepared(host, operation="pages"):
    if operation == "pages":
        return host.coordinator.prepare(pages=[3, 1])
    rows = [{"region_id": "a", "page_number": 1, "bbox": [0, 0, 1, 1]}]
    if operation == "hardscan":
        rows[0]["recipe"] = {"orientation_clockwise": 90, "illumination": "none", "bow_fraction": 0.,
                             "bow_assumption": "none"}
    return host.coordinator.prepare(operation=operation, regions=rows)


def _start(host, prepared=None):
    prepared = _prepared(host) if prepared is None else prepared
    view = host.coordinator.start(prepared["intent_id"], prepared["approval_token"], confirmed=True)
    return view["run_id"]


def _terminal(host, identifier):
    host.coordinator._runs[identifier].thread.join(15)
    view = host.coordinator.status(identifier)
    assert view["phase"] == "terminal", view
    return view


def _fake_worker(monkeypatch, *, code=3, publish=True):
    observed = []

    def supervise(script, args, **kwargs):
        observed.append((script, list(args), kwargs))
        kwargs["on_child_started"](object())
        if publish:
            assert cli.main(args) == 3
        return code

    monkeypatch.setenv("RAG_OCR_DISPOSITION_CHILD", "1")
    monkeypatch.setattr(execution.process_supervision, "_run_cli_with_deadline", supervise)
    return observed


@pytest.mark.parametrize("limits", [{"timeout_seconds": True}, {"timeout_seconds": 0},
    {"timeout_seconds": 3601}, {"max_history": False}, {"max_history": 0}, {"max_history": 33}])
def test_constructor_rejects_limits_without_thread_or_outputs(host, limits):
    with pytest.raises(ValueError):
        execution.ReviewRunCoordinator(host.workspace, **limits)
    assert not list(host.workspace.output_dir.glob("ocr-run-*"))


def test_constructor_requires_fixed_workspace():
    with pytest.raises(ValueError):
        execution.ReviewRunCoordinator(NS())


@pytest.mark.parametrize("options", [
    {}, {"pages": []}, {"pages": [True]}, {"pages": [1.]}, {"pages": [4]}, {"pages": [1, 1]},
    {"pages": [1] * 21}, {"pages": (1,)}, {"pages": [1], "regions": []},
    {"pages": [1], "dpi": True}, {"pages": [1], "preprocessing": "PRIVATE"},
    {"operation": "PRIVATE", "pages": [1]}, {"operation": "regions", "regions": []},
    {"operation": "regions", "pages": [1], "regions": []},
    {"operation": "regions", "regions": [{}]},
    {"operation": "regions", "dpi": 301, "regions": [{}]},
    {"operation": "hardscan", "regions": [{"region_id": "a", "page_number": 1, "bbox": [0, 0, 1, 1]}]},
])
def test_invalid_selection_cannot_reach_generation_or_publish(host, monkeypatch, options):
    monkeypatch.setattr(io, "capture_producer_generation", lambda **_kw: pytest.fail("invalid input reached generation"))
    with pytest.raises(ValueError, match="preparation failed"):
        host.coordinator.prepare(**options)
    assert not list(host.workspace.output_dir.glob("ocr-run-*"))


@pytest.mark.parametrize("operation", ["pages", "regions", "hardscan"])
def test_prepare_exact_detached_selection_no_publication(host, operation):
    prepared = _prepared(host, operation)
    pending = host.coordinator._pending
    assert not list(host.workspace.output_dir.glob("ocr-run-*"))
    assert prepared["status"] == "awaiting_approval" and prepared["requires_attention"] is True
    assert prepared["configuration"]["operation"] == operation
    assert not any(str(path) in json.dumps(prepared) for path in (host.workspace.pdf_path, host.workspace.output_dir))
    if operation == "pages":
        assert prepared["selection"] == {"pages": [1, 3]}
        assert prepared["configuration"]["policy"]["max_pages"] == 2
    else:
        assert json.loads(pending.plan_bytes)["source_sha256"] == host.workspace.document.source_sha256
        assert json.loads(pending.plan_bytes)["recovery_sha256"] == host.workspace.document.recovery_sha256
    prepared["selection"].clear()
    prepared["configuration"].clear()
    host.coordinator._recheck_intent(pending)


def test_supersession_invalidation_and_failed_prepare_revoke_old_approval(host):
    first = _prepared(host)
    second = _prepared(host)
    with pytest.raises(ValueError):
        _start(host, first)
    host.coordinator.invalidate_pending()
    with pytest.raises(ValueError):
        _start(host, second)
    third = _prepared(host)
    with pytest.raises(ValueError):
        host.coordinator.prepare(pages=[])
    with pytest.raises(ValueError):
        _start(host, third)


@pytest.mark.parametrize("confirmation", [False, 1, "true", None])
def test_exact_boolean_consent_required(host, confirmation):
    prepared = _prepared(host)
    with pytest.raises(ValueError):
        host.coordinator.start(prepared["intent_id"], prepared["approval_token"], confirmed=confirmation)


def test_one_use_shared_approval_and_busy_state(host, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def supervise(_script, _args, **kwargs):
        kwargs["on_child_started"](object())
        entered.set()
        assert release.wait(5)
        return 2

    monkeypatch.setattr(execution.process_supervision, "_run_cli_with_deadline", supervise)
    prepared = _prepared(host)
    identifier = _start(host, prepared)
    assert entered.wait(5)
    try:
        with pytest.raises(ValueError):
            _start(host, prepared)
        with pytest.raises(ValueError):
            _prepared(host)
        host.coordinator.invalidate_pending()
        assert host.coordinator.status(identifier)["process_status"] == "running"
    finally:
        release.set()
    assert _terminal(host, identifier)["actual_calls"] is None
    with pytest.raises(ValueError):
        _start(host, prepared)


def test_slow_prepare_cannot_restore_revoked_pending(host, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = host.coordinator._selection
    errors = []

    def selection(*args):
        entered.set()
        assert release.wait(5)
        return original(*args)

    monkeypatch.setattr(host.coordinator, "_selection", selection)

    def prepare():
        try:
            _prepared(host)
        except ValueError:
            errors.append(True)

    thread = threading.Thread(target=prepare)
    thread.start()
    assert entered.wait(5)
    host.coordinator.invalidate_pending()
    release.set()
    thread.join(5)
    assert errors == [True] and host.coordinator._pending is None


@pytest.mark.parametrize("fault", ["source_bytes", "source_inode", "recovery_inode", "producer", "document"])
def test_changed_generation_after_consent_refuses_before_supervisor(host, monkeypatch, fault):
    prepared = _prepared(host)
    if fault == "source_bytes":
        host.workspace.pdf_path.write_bytes(b"PRIVATE changed source")
    elif fault in ("source_inode", "recovery_inode"):
        target = host.workspace.pdf_path if fault == "source_inode" else host.workspace.recovery_path
        replacement = target.with_suffix(".replacement")
        replacement.write_bytes(target.read_bytes())
        replacement.replace(target)
    elif fault == "producer":
        host.case.generation["upstream"]["fixture"] = "c" * 64
    else:
        host.workspace.document = copy.deepcopy(host.workspace.document)
    monkeypatch.setattr(execution.process_supervision, "_run_cli_with_deadline", lambda *_a, **_k: pytest.fail("dispatched"))
    view = _terminal(host, _start(host, prepared))
    assert view["process_status"] == "failed" and view["actual_calls"] == 0
    assert "PRIVATE" not in json.dumps(view)


def test_cancel_before_preflight_does_not_spawn(host, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = host.coordinator._recheck_intent
    prepared = _prepared(host)

    def check(intent):
        entered.set()
        assert release.wait(5)
        original(intent)

    monkeypatch.setattr(host.coordinator, "_recheck_intent", check)
    monkeypatch.setattr(execution.process_supervision, "_run_cli_with_deadline", lambda *_a, **_k: pytest.fail("spawned"))
    identifier = _start(host, prepared)
    assert entered.wait(5)
    assert host.coordinator.cancel(identifier)["cancellation_requested"] is True
    release.set()
    view = _terminal(host, identifier)
    assert view["process_status"] == "cancelled" and view["actual_calls"] == 0


def test_cancel_at_child_started_refuses_before_gate_release(host, monkeypatch):
    released = []

    def supervise(_script, _args, **kwargs):
        assert not kwargs["cancel_requested"]()
        run_id = host.coordinator._active
        host.coordinator.cancel(run_id)
        kwargs["on_child_started"](object())
        released.append(True)
        return 3

    monkeypatch.setattr(execution.process_supervision, "_run_cli_with_deadline", supervise)
    view = _terminal(host, _start(host))
    assert view["process_status"] == "cancelled" and view["actual_calls"] == 0
    assert not released


@pytest.mark.parametrize("code,status", [(2, "failed"), (124, "timed_out"), (130, "cancelled"), (3, "completed")])
def test_exit_code_without_completion_never_fabricates_success_or_zero_calls(host, monkeypatch, code, status):
    _fake_worker(monkeypatch, code=code, publish=False)
    view = _terminal(host, _start(host))
    assert view["process_status"] == status
    assert view["artifact_state"] == "absent" and view["actual_calls"] is None
    with pytest.raises(ValueError):
        host.coordinator.result(view["run_id"])


@pytest.mark.parametrize("code,status", [(3, "completed"), (124, "timed_out"), (130, "cancelled"), (2, "failed")])
def test_actual_no_call_bundle_readback_is_separate_from_process_status(host, monkeypatch, code, status):
    observed = _fake_worker(monkeypatch, code=code)
    baseline = host.workspace.document.recovery_snapshot()
    view = _terminal(host, _start(host))
    assert view["process_status"] == status and view["artifact_state"] == "verified_complete"
    assert view["actual_calls"] == 0
    result = host.coordinator.result(view["run_id"], item_id="page-00001")
    assert result["selected_item"]["report_record"]["status"] == "retry_failed"
    assert result["selected_item"]["baseline_page"] == baseline["pages"][0]
    assert result["summary"]["raw_calls"] == 0 and len(result["item_ids"]) == 3
    assert host.workspace.document.recovery_snapshot() == baseline
    script, args, kwargs = observed[0]
    assert script == execution._SCRIPT and "--worker" in args
    assert any(arg.startswith("--expected-request-sha256=") for arg in args)
    assert kwargs["stdout_target"] == kwargs["stderr_target"] == subprocess.DEVNULL
    assert kwargs["environment_overrides"]["HF_HUB_OFFLINE"] == "1"
    assert kwargs["environment_overrides"]["PYTHONNOUSERSITE"] == "1"
    assert all("PID" not in key and "path" not in key for key in result)
    result["summary"].clear()
    assert host.coordinator.result(view["run_id"])["summary"]


def test_cleanup_uncertainty_blocks_new_runs_even_with_complete_artifact(host, monkeypatch):
    observed = _fake_worker(monkeypatch)
    worker = execution.process_supervision._run_cli_with_deadline

    def supervise(*args, **kwargs):
        worker(*args, **kwargs)
        raise execution.process_supervision._SupervisorCleanupError("PRIVATE cleanup")

    monkeypatch.setattr(execution.process_supervision, "_run_cli_with_deadline", supervise)
    view = _terminal(host, _start(host))
    assert observed and view["process_status"] == "cleanup_unconfirmed"
    assert view["artifact_state"] == "verified_complete" and view["actual_calls"] == 0
    assert host.coordinator.result(view["run_id"])["summary"]["raw_calls"] == 0
    with pytest.raises(ValueError):
        _prepared(host)
    assert host.coordinator.close() == {"closed": True, "cleanup_confirmed": False, "notice": "cleanup_unconfirmed"}


def test_current_result_readback_rejects_changed_retained_artifact(host, monkeypatch):
    _fake_worker(monkeypatch)
    identifier = _start(host)
    assert _terminal(host, identifier)["artifact_state"] == "verified_complete"
    (host.coordinator._runs[identifier].intent.output / "report.json").write_bytes(b"PRIVATE forged result")
    with pytest.raises(ValueError, match="unavailable"):
        host.coordinator.result(identifier)


def test_result_has_explicit_serialized_display_budget(host, monkeypatch):
    _fake_worker(monkeypatch)
    identifier = _start(host)
    _terminal(host, identifier)
    monkeypatch.setattr(execution, "MAX_RESULT_BYTES", 16)
    with pytest.raises(ValueError, match="display bound"):
        host.coordinator.result(identifier)


def test_bounded_history_never_deletes_artifacts(host, monkeypatch):
    host.coordinator._max_history = 1
    _fake_worker(monkeypatch)
    first = _start(host)
    _terminal(host, first)
    output = host.coordinator._runs[first].intent.output
    second = _start(host)
    _terminal(host, second)
    with pytest.raises(ValueError):
        host.coordinator.status(first)
    assert (output / "manifest.json").is_file()
    assert len(host.coordinator._runs) == 1


def test_close_signals_running_supervisor_without_holding_state_lock(host, monkeypatch):
    entered = threading.Event()

    def supervise(_script, _args, **kwargs):
        kwargs["on_child_started"](object())
        entered.set()
        run = host.coordinator._runs[host.coordinator._active]
        assert run.cancel.wait(5)
        return 130

    monkeypatch.setattr(execution.process_supervision, "_run_cli_with_deadline", supervise)
    identifier = _start(host)
    assert entered.wait(5)
    assert host.coordinator.close() == {"closed": True, "cleanup_confirmed": True, "notice": "closed"}
    assert host.coordinator.status(identifier)["process_status"] == "cancelled"
    with pytest.raises(ValueError):
        _prepared(host)


def test_close_reports_unfinished_thread_and_latches_uncertainty(host, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def supervise(_script, _args, **kwargs):
        kwargs["on_child_started"](object())
        entered.set()
        assert release.wait(5)
        return 130

    monkeypatch.setattr(execution.process_supervision, "_run_cli_with_deadline", supervise)
    monkeypatch.setattr(execution, "CLOSE_TIMEOUT_SECONDS", 0.)
    identifier = _start(host)
    assert entered.wait(5)
    try:
        assert host.coordinator.close()["cleanup_confirmed"] is False
    finally:
        release.set()
    _terminal(host, identifier)
    assert host.coordinator.close()["cleanup_confirmed"] is False


@pytest.mark.parametrize("failure", [RuntimeError, KeyboardInterrupt])
def test_thread_constructor_failure_consumes_approval_without_orphaning_active(host, monkeypatch, failure):
    prepared = _prepared(host)

    def constructor(**_kwargs):
        raise failure("PRIVATE constructor failure")

    monkeypatch.setattr(execution.threading, "Thread", constructor)
    if failure is KeyboardInterrupt:
        with pytest.raises(KeyboardInterrupt):
            _start(host, prepared)
        identifier = prepared["intent_id"]
    else:
        identifier = _start(host, prepared)
    view = host.coordinator.status(identifier)
    assert view["phase"] == "terminal" and view["process_status"] == "failed"
    assert view["actual_calls"] == 0 and host.coordinator._active is None
    assert host.coordinator.close()["cleanup_confirmed"] is True
    with pytest.raises(ValueError):
        _start(host, prepared)


@pytest.mark.parametrize("launched", [False, True])
@pytest.mark.parametrize("failure", [RuntimeError, KeyboardInterrupt])
def test_thread_start_failure_retains_possible_handoff_and_blocks_more_runs(host, monkeypatch, launched, failure):
    original = execution.threading.Thread.start
    execute = host.coordinator._execute
    released = threading.Event()
    prepared = _prepared(host)
    called = []

    def held_execute(run):
        assert released.wait(5)
        execute(run)

    def start(thread):
        if launched:
            original(thread)
        raise failure("PRIVATE failure after possible launch")

    monkeypatch.setattr(execution.threading.Thread, "start", start)
    monkeypatch.setattr(host.coordinator, "_execute", held_execute)
    monkeypatch.setattr(execution.process_supervision, "_run_cli_with_deadline", lambda *_a, **_k: called.append(True))
    if failure is KeyboardInterrupt:
        with pytest.raises(KeyboardInterrupt):
            _start(host, prepared)
        identifier = prepared["intent_id"]
    else:
        identifier = _start(host, prepared)
    run = host.coordinator._runs[identifier]
    assert run.thread is not None and run.cancel.is_set() and run.start_uncertain
    released.set()
    if launched:
        run.thread.join(5)
        assert not run.thread.is_alive()
    assert host.coordinator.status(identifier)["process_status"] == "cleanup_unconfirmed"
    assert not called
    with pytest.raises(ValueError):
        _prepared(host)
    assert host.coordinator.close()["cleanup_confirmed"] is False


def test_cancel_during_successful_strict_readback_keeps_completed_with_warning(host, monkeypatch):
    _fake_worker(monkeypatch)
    entered, release = threading.Event(), threading.Event()
    original = host.coordinator._inspect_artifact

    def inspect(run):
        entered.set()
        assert release.wait(5)
        return original(run)

    monkeypatch.setattr(host.coordinator, "_inspect_artifact", inspect)
    identifier = _start(host)
    assert entered.wait(10)
    host.coordinator.cancel(identifier)
    release.set()
    view = _terminal(host, identifier)
    assert view["process_status"] == "completed" and view["artifact_state"] == "verified_complete"
    assert view["notice"] == "completed_after_cancellation_request"
    assert view["cancellation_requested"] is True


@pytest.mark.parametrize("fault", ["plan", "source", "directory", "staging"])
def test_crop_plan_publication_is_exact_no_clobber_and_rechecks_generation(host, monkeypatch, fault):
    prepared = _prepared(host, "regions")
    plan_path = host.coordinator._pending.plan_path
    original = execution.storage_policy.atomic_write_private

    def write(path, writer, **kwargs):
        commit = kwargs["replace_fn"]

        def changed(temporary, destination):
            if fault == "plan":
                destination.write_bytes(b"existing competing plan")
            elif fault == "source":
                host.workspace.pdf_path.write_bytes(b"PRIVATE changed source")
            elif fault == "directory":
                monkeypatch.setattr(host.coordinator, "_directory", (-1, -1))
            else:
                temporary.write_bytes(b"PRIVATE changed staged bytes")
            return commit(temporary, destination)

        kwargs["replace_fn"] = changed
        return original(path, writer, **kwargs)

    monkeypatch.setattr(execution.storage_policy, "atomic_write_private", write)
    monkeypatch.setattr(execution.process_supervision, "_run_cli_with_deadline", lambda *_a, **_k: pytest.fail("spawned"))
    view = _terminal(host, _start(host, prepared))
    assert view["process_status"] == "failed" and view["actual_calls"] == 0
    if fault == "plan":
        assert plan_path.read_bytes() == b"existing competing plan"
    else:
        assert not plan_path.exists()


@pytest.mark.parametrize("failure", [ocr_recovery.ReportCleanupError, execution._PlanCleanupError])
def test_post_commit_plan_cleanup_uncertainty_blocks_dispatch_and_new_runs(host, monkeypatch, failure):
    prepared = _prepared(host, "regions")
    plan = host.coordinator._pending.plan_path
    original = execution._publish_new_report

    def publish(temporary, destination):
        original(temporary, destination)
        raise failure("PRIVATE cleanup failure")

    monkeypatch.setattr(execution, "_publish_new_report", publish)
    monkeypatch.setattr(execution.process_supervision, "_run_cli_with_deadline", lambda *_a, **_k: pytest.fail("spawned"))
    view = _terminal(host, _start(host, prepared))
    assert plan.exists() and view["artifact_state"] == "incomplete"
    assert view["process_status"] == "cleanup_unconfirmed" and view["actual_calls"] is None
    with pytest.raises(ValueError):
        _prepared(host)


def test_actual_request_must_equal_preapproved_configuration(host, monkeypatch):
    prepared = _prepared(host)
    original = host.coordinator._request

    def changed(intent):
        request = original(intent)
        request.payload["configuration"]["requested_pages"] = [1]
        return request

    monkeypatch.setattr(host.coordinator, "_request", changed)
    monkeypatch.setattr(execution.process_supervision, "_run_cli_with_deadline", lambda *_a, **_k: pytest.fail("spawned"))
    view = _terminal(host, _start(host, prepared))
    assert view["process_status"] == "failed" and view["actual_calls"] == 0


def test_pending_output_path_cannot_be_rebound_to_another_private_directory(host, monkeypatch):
    from dataclasses import replace

    prepared = _prepared(host)
    host.coordinator._pending = replace(host.coordinator._pending, output=host.workspace.output_dir / "other")
    monkeypatch.setattr(execution.process_supervision, "_run_cli_with_deadline", lambda *_a, **_k: pytest.fail("spawned"))
    assert _terminal(host, _start(host, prepared))["process_status"] == "failed"
    assert not (host.workspace.output_dir / "other").exists()


def test_readback_page_count_must_match_immutable_workspace(host, monkeypatch):
    _fake_worker(monkeypatch)
    original = io.read_disposition_completion

    def changed(*args, **kwargs):
        bundle = original(*args, **kwargs)
        bundle["report"]["page_count"] += 1
        return bundle

    monkeypatch.setattr(io, "read_disposition_completion", changed)
    view = _terminal(host, _start(host))
    assert view["artifact_state"] == "present_unverified" and view["actual_calls"] is None


def test_close_during_slow_prepare_cannot_leave_an_approved_intent(host, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = host.coordinator._selection
    failures = []

    def selection(*args):
        entered.set()
        assert release.wait(5)
        return original(*args)

    def prepare():
        try:
            _prepared(host)
        except ValueError:
            failures.append(True)

    monkeypatch.setattr(host.coordinator, "_selection", selection)
    thread = threading.Thread(target=prepare)
    thread.start()
    assert entered.wait(5)
    assert host.coordinator.close()["cleanup_confirmed"] is True
    release.set()
    thread.join(5)
    assert not thread.is_alive() and failures == [True]
    assert host.coordinator._pending is None
    assert not list(host.workspace.output_dir.glob("ocr-run-*"))


def test_cancel_after_gate_handoff_does_not_claim_zero_actual_calls(host, monkeypatch):
    entered = threading.Event()

    def supervise(_script, _args, **kwargs):
        kwargs["on_child_started"](object())
        entered.set()
        active = host.coordinator._runs[host.coordinator._active]
        assert active.cancel.wait(5)
        assert kwargs["cancel_requested"]()
        return 130

    monkeypatch.setattr(execution.process_supervision, "_run_cli_with_deadline", supervise)
    identifier = _start(host)
    assert entered.wait(5)
    assert host.coordinator.cancel(identifier)["actual_calls"] is None
    view = _terminal(host, identifier)
    assert view["process_status"] == "cancelled" and view["actual_calls"] is None


def test_result_in_flight_cannot_return_after_history_eviction(host, monkeypatch):
    _fake_worker(monkeypatch)
    host.coordinator._max_history = 1
    first = _start(host)
    _terminal(host, first)
    entered, release = threading.Event(), threading.Event()
    original = host.coordinator._read_bundle
    failures = []

    def read(run):
        if run.intent.identifier == first:
            entered.set()
            assert release.wait(10)
        return original(run)

    def result():
        try:
            host.coordinator.result(first)
        except ValueError:
            failures.append(True)

    monkeypatch.setattr(host.coordinator, "_read_bundle", read)
    thread = threading.Thread(target=result)
    thread.start()
    assert entered.wait(5)
    second = _start(host)
    _terminal(host, second)
    release.set()
    thread.join(5)
    assert not thread.is_alive() and failures == [True]
    assert first not in host.coordinator._runs


def test_changed_source_without_any_bundle_is_absent_not_fabricated_present(host, monkeypatch):
    prepared = _prepared(host)
    host.workspace.pdf_path.write_bytes(b"changed after preparation")
    monkeypatch.setattr(execution.process_supervision, "_run_cli_with_deadline", lambda *_a, **_k: pytest.fail("spawned"))
    view = _terminal(host, _start(host, prepared))
    assert view["artifact_state"] == "absent" and view["actual_calls"] == 0


@pytest.mark.parametrize("identifier", [None, 1, True, "x" * 1000, "../private", "A" * 32])
def test_status_and_cancel_reject_unbounded_or_unknown_ids(host, identifier):
    for method in (host.coordinator.status, host.coordinator.cancel):
        with pytest.raises(ValueError):
            method(identifier)


def test_unknown_result_item_is_not_silently_defaulted(host, monkeypatch):
    _fake_worker(monkeypatch)
    identifier = _start(host)
    _terminal(host, identifier)
    with pytest.raises(ValueError):
        host.coordinator.result(identifier, item_id="PRIVATE unknown")


@pytest.mark.parametrize("operation", ["pages", "regions", "hardscan"])
def test_real_inert_all_route_bundle_matches_preapproved_request_and_baseline(
        routes, monkeypatch, tmp_path, preview_roots, operation):
    workspace = ReviewWorkspace(routes.source, routes.recovery_path, tmp_path)
    coordinator = execution.ReviewRunCoordinator(workspace)
    try:
        assert coordinator.preview_private_root.parent == preview_roots
        host = NS(coordinator=coordinator, workspace=workspace)
        observed = _fake_worker(monkeypatch)
        before = workspace.document.recovery_snapshot()
        prepared = _prepared(host, operation)
        expected_plan = coordinator._pending.plan_bytes
        if operation == "hardscan":
            # The established inert fixture keeps a square64x64 raster, so the
            # explicit quarter-turn recipe preserves its exact admitted shape.
            assert prepared["selection"]["regions"][0]["recipe"]["orientation_clockwise"] == 90
        identifier = _start(host, prepared)
        view = _terminal(host, identifier)
        assert view["artifact_state"] == "verified_complete", view
        assert view["actual_calls"] == (2 if operation == "pages" else 1)
        run = coordinator._runs[identifier]
        assert execution._encoded(run.request.payload) == run.intent.expected_payload_bytes
        if expected_plan is not None:
            assert run.intent.plan_path.read_bytes() == expected_plan
            assert run.intent.output not in run.intent.plan_path.parents
            assert any(arg.startswith("--plan=") for arg in observed[0][1])
        result = coordinator.result(identifier)
        item = result["item_ids"][0]["item_id"]
        selected = coordinator.result(identifier, item_id=item)["selected_item"]
        assert selected["diagnostic"]["candidate"]["state"] == "accepted"
        assert selected["report_record"]["candidate"] is not None
        assert workspace.document.recovery_snapshot() == before
    finally:
        assert coordinator.close()["cleanup_confirmed"] is True


def test_import_is_model_and_ui_dependency_free():
    code = "import sys; import ocr_review_execution; assert not any(n in sys.modules for n in ('rapidocr','pymupdf','cv2','numpy','gradio','onnxruntime'))"
    result = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")


@pytest.mark.parametrize("profile", [None, "fit", "dpi288", "dpi576"])
def test_coordinator_preview_profile_forwards_only_to_fixed_controller_without_ocr_authority(host, monkeypatch, profile):
    """Inert image/controller seam; no PDF raster or model execution."""
    prepared = _prepared(host)
    pending = host.coordinator._pending
    image, scope, calls = object(), {"declared": "inert scope"}, []
    def render(actual_scope, *, cancel_requested, preview_profile):
        assert not cancel_requested()
        assert host.coordinator._lock.acquire(blocking=False)
        host.coordinator._lock.release()
        calls.append((actual_scope, preview_profile))
        return image
    monkeypatch.setattr(host.coordinator._preview, "render", render)
    monkeypatch.setattr(execution.process_supervision, "_run_cli_with_deadline",
                        lambda *_a, **_k: pytest.fail("preview dispatched OCR"))
    kwargs = {} if profile is None else {"preview_profile": profile}
    assert host.coordinator.render_crop_preview(scope, **kwargs) is image
    assert calls == [(scope, "fit" if profile is None else profile)]
    assert host.coordinator._pending is pending and pending.identifier == prepared["intent_id"]
    assert not host.coordinator._runs


@pytest.mark.parametrize("profile", [None, True, 288, [], {}, "dpi144", "FIT", "dpi576 "])
def test_coordinator_invalid_preview_profile_refuses_before_callback_or_controller(host, monkeypatch, profile):
    from ocr_crop_review_runtime import CropPreviewError
    def forbidden(*_a, **_k):
        pytest.fail("invalid profile reached callback, inputs or controller")
    monkeypatch.setattr(host.coordinator._preview, "render", forbidden)
    monkeypatch.setattr(host.workspace, "verify_inputs", forbidden)
    with pytest.raises(CropPreviewError) as caught:
        host.coordinator.render_crop_preview({}, preview_profile=profile, cancel_requested=forbidden)
    assert caught.value.code == "scope_validation"
    assert not host.coordinator._runs and not host.coordinator._cleanup_uncertain


@pytest.mark.parametrize("profile", ["dpi288", "dpi576"])
def test_coordinator_detail_failure_never_retries_fit_or_dispatches_ocr(host, monkeypatch, profile):
    from ocr_crop_review_runtime import CropPreviewError
    calls = []
    def render(_scope, *, cancel_requested, preview_profile):
        assert not cancel_requested()
        calls.append(preview_profile)
        raise CropPreviewError(code="raster_limit")
    monkeypatch.setattr(host.coordinator._preview, "render", render)
    monkeypatch.setattr(execution.process_supervision, "_run_cli_with_deadline",
                        lambda *_a, **_k: pytest.fail("preview failure dispatched OCR"))
    with pytest.raises(CropPreviewError) as caught:
        host.coordinator.render_crop_preview({}, preview_profile=profile)
    assert caught.value.code == "raster_limit" and calls == [profile]
    assert not host.coordinator._runs and not host.coordinator._cleanup_uncertain


class _OwnedCoordinatorPreview:
    def __init__(self, close_failure=None):
        self.closes = 0
        self.close_failure = close_failure

    def close(self):
        self.closes += 1
        if self.close_failure is not None:
            raise self.close_failure

    def tobytes(self):
        assert self.closes == 0
        return b"RGB"


@pytest.mark.parametrize("failure_kind", ["cancelled", "cleanup", "ordinary", "typed", "interrupt", "exit"])
@pytest.mark.parametrize("close_type", [None, RuntimeError, KeyboardInterrupt, SystemExit])
def test_coordinator_disposes_after_late_refusal_without_replacing_primary(
        host, monkeypatch, failure_kind, close_type):
    from ocr_crop_review_runtime import CropPreviewError
    image = _OwnedCoordinatorPreview(None if close_type is None else close_type("secondary image close"))
    failure = {"ordinary": OSError("PRIVATE late callback"),
               "typed": CropPreviewError(code="input_changed"),
               "interrupt": KeyboardInterrupt("primary cancellation"),
               "exit": SystemExit("primary exit")}.get(failure_kind)
    calls = []

    def render(*_args, **_kwargs):
        calls.append("render")
        if failure_kind == "cleanup":
            patch.setattr(host.coordinator._preview, "_uncertain", True)
        return image

    def cancel_requested():
        if not calls:
            return False
        if failure is not None:
            raise failure
        return failure_kind == "cancelled"

    expected = type(failure) if failure_kind in {"interrupt", "exit"} else CropPreviewError
    # Restore the injected preview latch before the real host fixture closes
    # its empty owned staging root; the coordinator's propagated latch remains.
    with monkeypatch.context() as patch:
        patch.setattr(host.coordinator._preview, "render", render)
        with pytest.raises(expected) as caught:
            host.coordinator.render_crop_preview({}, cancel_requested=cancel_requested)
    assert calls == ["render"] and image.closes == 1
    if failure_kind in {"interrupt", "exit"}:
        assert caught.value is failure
    else:
        assert caught.value.code == {"cancelled": "preview_cancelled", "cleanup": "cleanup_unconfirmed",
                                     "ordinary": "preview_unavailable", "typed": "input_changed"}[failure_kind]
    assert not host.coordinator._runs
    assert host.coordinator._cleanup_uncertain is (failure_kind == "cleanup")


@pytest.mark.parametrize("failure_type", [RuntimeError, KeyboardInterrupt, SystemExit])
def test_coordinator_finalizer_failure_keeps_image_until_transfer(host, monkeypatch, failure_type):
    """The wrapper still owns the image while its existing finalizer runs."""
    image = _OwnedCoordinatorPreview(SystemExit("secondary image close"))
    failure = failure_type("primary cleanup-latch read")
    reads = []
    calls = []

    class Preview:
        @property
        def cleanup_uncertain(self):
            reads.append(True)
            if len(reads) == 3:  # Admission, late success check, then finalizer.
                raise failure
            return False

        def render(self, *_args, **_kwargs):
            calls.append("render")
            return image

    with monkeypatch.context() as patch:
        patch.setattr(host.coordinator, "_preview", Preview())
        with pytest.raises(failure_type) as caught:
            host.coordinator.render_crop_preview({})
    assert caught.value is failure and image.closes == 1
    assert len(reads) == 3 and calls == ["render"]


def test_coordinator_does_not_close_image_owned_by_failing_inner_renderer(host, monkeypatch):
    from ocr_crop_review_runtime import CropPreviewError
    image = _OwnedCoordinatorPreview()
    calls = []

    def render(*_args, **_kwargs):
        calls.append("render")
        image.close()
        raise CropPreviewError(code="input_changed")

    monkeypatch.setattr(host.coordinator._preview, "render", render)
    with pytest.raises(CropPreviewError) as caught:
        host.coordinator.render_crop_preview({})
    assert caught.value.code == "input_changed" and image.closes == 1 and calls == ["render"]


@pytest.mark.parametrize("profile", ["fit", "dpi288", "dpi576"])
def test_coordinator_success_transfers_image_usable_after_finalizer(host, monkeypatch, profile):
    image = _OwnedCoordinatorPreview()
    calls = []

    def render(*_args, **kwargs):
        calls.append(kwargs["preview_profile"])
        return image

    monkeypatch.setattr(host.coordinator._preview, "render", render)
    result = host.coordinator.render_crop_preview({}, preview_profile=profile)
    assert result is image and image.tobytes() == b"RGB" and image.closes == 0
    assert calls == [profile]
    result.close()
    assert image.closes == 1


@pytest.mark.parametrize("join_required", [False, True])
def test_close_bounds_rounded_remaining_time_before_real_preview_cleanup(host, monkeypatch, join_required):
    # Same-tick readings straddling a binary precision boundary round upward.
    now = 131071.999
    assert (now + 40.) - now > 40.
    joins = []
    if join_required:
        thread = NS(join=joins.append, is_alive=lambda: False)
        host.coordinator._runs["uncertain-start"] = NS(thread=thread, start_uncertain=True)
    with monkeypatch.context() as patch:
        # Replace the coordinator capability only; pytest and the real preview
        # controller retain their actual clocks, locks and directory cleanup.
        patch.setattr(execution, "time", NS(monotonic=lambda: now))
        result = host.coordinator.close()
    assert result == {"closed": True, "cleanup_confirmed": True, "notice": "closed"}
    assert joins == ([40.] if join_required else [])
    assert not host.coordinator.preview_private_root.exists()
