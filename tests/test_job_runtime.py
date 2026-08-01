from __future__ import annotations

import json
import multiprocessing
import os
import stat
import threading
from pathlib import Path

import pytest

import job_runtime
import storage_policy
from job_runtime import (
    JobAlreadyExistsError,
    JobBusyError,
    JobCorruptError,
    JobStateError,
    JobStore,
    JobValidationError,
)


def _transition_worker(root, job_id, attempt_token, target, revision,
                       start_event, result_queue):
    store = JobStore(Path(root))
    if not start_event.wait(10):
        result_queue.put(("timeout", target, ""))
        return
    try:
        summary = store.transition_job(
            job_id, target, attempt_token=attempt_token,
            expected_revision=revision, lease_timeout=5)
    except Exception as exc:  # process boundary reports the exact type
        result_queue.put(("error", target, type(exc).__name__))
    else:
        result_queue.put(("ok", target, summary.status))


def _lease_holder(root, job_id, ready_event, release_event, result_queue):
    store = JobStore(Path(root))
    try:
        with store.lease(job_id, timeout=5):
            result_queue.put("acquired")
            ready_event.set()
            if not release_event.wait(10):
                result_queue.put("release-timeout")
    except Exception as exc:  # process boundary reports the exact type
        result_queue.put(type(exc).__name__)


def _execution(store: JobStore, job_id: str):
    return store.load_execution(job_id)


def _advance_to_running(store: JobStore, job_id: str):
    execution = _execution(store, job_id)
    starting = store.transition_job(
        job_id, "starting", attempt_token=execution.attempt_token,
        expected_revision=execution.revision)
    running = store.transition_job(
        job_id, "running", attempt_token=execution.attempt_token,
        expected_revision=starting.revision)
    return execution.attempt_token, running


def _assert_private(path: Path, *, directory: bool) -> None:
    if os.name == "nt":
        assert storage_policy.windows_path_is_private(
            path, directory=directory)
    else:
        expected = (storage_policy.PRIVATE_DIRECTORY_MODE if directory
                    else storage_policy.PRIVATE_FILE_MODE)
        assert stat.S_IMODE(path.stat().st_mode) == expected


def test_job_clock_rejects_timestamps_outside_reportable_range(tmp_path):
    store = JobStore(
        tmp_path / "jobs",
        clock=lambda: job_runtime._MAX_TIMESTAMP + 1.0,
    )

    with pytest.raises(job_runtime.JobRuntimeError, match="invalid timestamp"):
        store.submit_job("export", [])


def test_submit_get_and_list_expose_only_redacted_summaries(tmp_path):
    root = tmp_path / "jobs"
    private_pdf = tmp_path / "Private Casebook.pdf"
    store = JobStore(root)

    submitted = store.submit_job(
        "full", ["--pdf", str(private_pdf)],
        timeout_seconds=600)
    loaded = store.get_job(submitted.job_id)

    assert loaded == submitted
    assert store.list_jobs() == [submitted]
    public = submitted.as_dict()
    rendered = json.dumps(public)
    assert public["command"] == "full"
    assert public["status"] == "queued"
    assert "argv" not in public
    assert "attempt_token" not in public
    assert str(private_pdf) not in rendered
    assert "--pdf" not in rendered

    execution = store.load_execution(submitted.job_id)
    assert execution.argv == ("--pdf", str(private_pdf))
    assert execution.timeout_seconds == 600
    assert str(private_pdf) not in repr(execution)
    assert execution.attempt_token not in repr(execution)


def test_submission_rejects_spec_larger_than_its_read_bound(tmp_path):
    store = JobStore(tmp_path / "jobs")

    with pytest.raises(
            JobValidationError, match="serialized job spec exceeds"):
        store.submit_job("export", ["\\" * (64 * 1024)])

    assert store.list_jobs() == []


@pytest.mark.skipif(os.name == "nt", reason="Windows forbids newline paths")
def test_submission_rejects_unpersistable_directory_text(tmp_path):
    store = JobStore(tmp_path / "jobs")
    invalid_working = tmp_path / "line\nbreak"
    invalid_working.mkdir()

    with pytest.raises(JobValidationError, match="path is invalid"):
        store.submit_job(
            "export", [], working_directory=invalid_working,
            output_root=tmp_path / "output")

    assert store.list_jobs() == []


def test_submit_creates_owner_only_root_job_and_documents(tmp_path):
    root = tmp_path / "private-jobs"
    store = JobStore(root)
    summary = store.submit_job("index", ["--chunks", "private.json"])
    store.request_cancel(summary.job_id)
    with store.lease(summary.job_id):
        pass

    job_dir = root / summary.job_id
    _assert_private(root, directory=True)
    _assert_private(job_dir, directory=True)
    for filename in (
            ".store.lock",):
        _assert_private(root / filename, directory=False)
    for filename in (
            "spec.json", "state.json", "cancel.a00000000000000000001.json",
            ".lock"):
        _assert_private(job_dir / filename, directory=False)


@pytest.mark.parametrize("argv", [
    ["--cloud-key", "top-secret"],
    ["--api-key=top-secret"],
    ["--GEMINI-KEY", "top-secret"],
    ["--cloud-k", "top-secret"],
    ["--cloud-keyx", "top-secret"],
    ["--cloud_key", "top-secret"],
    ["--clod-key", "top-secret"],
    ["--access-token=top-secret"],
])
def test_submit_rejects_secret_bearing_arguments(tmp_path, argv):
    store = JobStore(tmp_path / "jobs")
    with pytest.raises(JobValidationError, match="credential"):
        store.submit_job("full", argv)
    assert not any(path.name != ".store.lock" for path in store.root.iterdir())


def test_submit_allows_sensitive_words_in_non_option_values(tmp_path):
    store = JobStore(tmp_path / "jobs")
    state = store.submit_job("full", ["--pdf", "draft-key.pdf"])

    assert state.job_id


@pytest.mark.parametrize("argv", [
    ["--cloud-url", "http://api.deepseek.com"],
    ["--cloud-url=https://user:secret@gateway.example/v1"],
    ["--llm-url", "https://gateway.example/v1?token=secret"],
    ["--ollama-url", "http://localhost:11434"],
    ["--cloud-u", "https://gateway.example/v1"],
    ["--cloud-urlx", "https://user:secret@gateway.example/v1"],
    ["--cloud_url", "https://user:secret@gateway.example/v1"],
    ["--", "--cloud-url", "https://user:secret@gateway.example/v1"],
    ["--", "--llm-url=https://gateway.example/v1?token=secret"],
    ["positional", "https://user:secret@gateway.example/v1"],
])
def test_submit_rejects_unsafe_endpoint_before_publishing_spec(tmp_path, argv):
    store = JobStore(tmp_path / "jobs")

    with pytest.raises(JobValidationError, match="endpoint"):
        store.submit_job("full", argv)

    assert not any(path.name != ".store.lock" for path in store.root.iterdir())


def test_submit_canonicalizes_safe_endpoint_arguments(tmp_path):
    store = JobStore(tmp_path / "jobs")

    summary = store.submit_job("full", [
        "--cloud-url=HTTPS://API.DEEPSEEK.COM:443/v1/",
        "--ollama-url", "http://127.0.0.1:11434/",
    ])

    assert store.load_execution(summary.job_id).argv == (
        "--cloud-url=https://api.deepseek.com/v1",
        "--ollama-url", "http://127.0.0.1:11434",
    )


@pytest.mark.parametrize("argv", [
    ["--run-id", "caller-owned"],
    ["--run-r", "private/report.json"],
    ["--operation-timeout=1"],
    ["--resume-run", "Book"],
])
def test_submit_rejects_manager_owned_arguments(tmp_path, argv):
    store = JobStore(tmp_path / "jobs")
    with pytest.raises(JobValidationError, match="manager owns"):
        store.submit_job("full", argv)


@pytest.mark.parametrize("command", ["jobs", "query", "info", "storage"])
def test_submit_rejects_nested_or_non_whitelisted_commands(tmp_path, command):
    store = JobStore(tmp_path / "jobs")
    with pytest.raises(JobValidationError):
        store.submit_job(command, [])


def test_submit_validates_arguments_timeout_and_duplicate_id(tmp_path):
    store = JobStore(tmp_path / "jobs")
    job_id = "a" * 32
    store.submit_job("convert", ["--pdf", "book.pdf"], job_id=job_id)

    with pytest.raises(JobAlreadyExistsError):
        store.submit_job("convert", [], job_id=job_id)
    with pytest.raises(JobValidationError):
        store.submit_job("convert", ["line\nbreak"])
    with pytest.raises(JobValidationError):
        store.submit_job("convert", [], timeout_seconds=float("inf"))
    with pytest.raises(JobValidationError):
        store.get_job("../outside")


def test_transition_legality_attempt_binding_and_idempotence(tmp_path):
    store = JobStore(tmp_path / "jobs")
    initial = store.submit_job("full", ["--pdf", "book.pdf"])
    execution = _execution(store, initial.job_id)

    with pytest.raises(JobStateError, match="illegal"):
        store.transition_job(
            initial.job_id, "succeeded",
            attempt_token=execution.attempt_token)
    with pytest.raises(JobStateError, match="stale attempt"):
        store.transition_job(
            initial.job_id, "starting", attempt_token="f" * 32)

    with store.lease(initial.job_id) as lease:
        starting = store.transition_job(
            initial.job_id, "starting",
            attempt_token=execution.attempt_token,
            expected_revision=0, lease=lease)
        repeated = store.transition_job(
            initial.job_id, "starting",
            attempt_token=execution.attempt_token,
            expected_revision=0, lease=lease)

    assert starting.status == "starting"
    assert starting.revision == 1
    assert repeated == starting

    running = store.transition_job(
        initial.job_id, "running", attempt_token=execution.attempt_token,
        expected_revision=starting.revision)
    succeeded = store.transition_job(
        initial.job_id, "succeeded", attempt_token=execution.attempt_token,
        expected_revision=running.revision)
    repeated_terminal = store.transition_job(
        initial.job_id, "succeeded", attempt_token=execution.attempt_token,
        expected_revision=running.revision)

    assert succeeded.terminal
    assert repeated_terminal == succeeded
    with pytest.raises(JobStateError, match="illegal"):
        store.transition_job(
            initial.job_id, "failed",
            attempt_token=execution.attempt_token)


def test_cancel_marker_is_required_and_bound_to_exact_attempt(tmp_path):
    store = JobStore(tmp_path / "jobs")
    initial = store.submit_job("batch", ["one.pdf", "two.pdf"])
    first = _execution(store, initial.job_id)
    spec_path = store.root / initial.job_id / "spec.json"
    immutable_spec = spec_path.read_bytes()

    assert not store.is_cancel_requested(initial.job_id, first.attempt_token)
    with pytest.raises(JobStateError, match="matching cancel marker"):
        store.transition_job(
            initial.job_id, "cancel_requested",
            attempt_token=first.attempt_token)

    assert store.request_cancel(initial.job_id) == initial
    assert store.request_cancel(initial.job_id) == initial
    assert store.is_cancel_requested(initial.job_id, first.attempt_token)
    requested_at = store.cancel_requested_at(
        initial.job_id, first.attempt_token)
    assert requested_at is not None
    requested = store.transition_job(
        initial.job_id, "cancel_requested",
        attempt_token=first.attempt_token)
    cancelled = store.transition_job(
        initial.job_id, "cancelled", attempt_token=first.attempt_token,
        expected_revision=requested.revision)
    assert not store.is_cancel_requested(initial.job_id, first.attempt_token)
    assert store.cancel_requested_at(
        initial.job_id, first.attempt_token) == requested_at

    resumed = store.prepare_resume(
        initial.job_id, expected_revision=cancelled.revision)
    second = _execution(store, initial.job_id)
    assert resumed.status == "queued"
    assert resumed.attempt_number == 2
    assert second.attempt_token != first.attempt_token
    assert not store.is_cancel_requested(initial.job_id, first.attempt_token)
    assert not store.is_cancel_requested(initial.job_id, second.attempt_token)
    assert store.cancel_requested_at(initial.job_id, first.attempt_token) is None
    assert store.cancel_requested_at(initial.job_id, second.attempt_token) is None
    assert spec_path.read_bytes() == immutable_spec
    with pytest.raises(JobStateError, match="not resumable"):
        store.prepare_resume(initial.job_id)


def test_cancel_preconditions_bind_request_to_current_attempt(tmp_path):
    store = JobStore(tmp_path / "jobs")
    submitted = store.submit_job("full", ["--pdf", "Book.pdf"])

    cancelled = store.request_cancel(
        submitted.job_id,
        expected_revision=submitted.revision,
        expected_attempt_number=submitted.attempt_number)

    assert cancelled == submitted
    execution = store.load_execution(submitted.job_id)
    assert store.is_cancel_requested(
        submitted.job_id, execution.attempt_token)
    with pytest.raises(JobStateError, match="cancel revision is stale"):
        store.request_cancel(
            submitted.job_id,
            expected_revision=submitted.revision + 1,
            expected_attempt_number=submitted.attempt_number)
    with pytest.raises(JobStateError, match="cancel targets a stale attempt"):
        store.request_cancel(
            submitted.job_id,
            expected_revision=submitted.revision,
            expected_attempt_number=submitted.attempt_number + 1)


def test_delayed_cancel_preconditions_cannot_cancel_resumed_attempt(tmp_path):
    store = JobStore(tmp_path / "jobs")
    submitted = store.submit_job("full", ["--pdf", "Book.pdf"])
    first_token, running = _advance_to_running(store, submitted.job_id)
    first_failed = store.transition_job(
        submitted.job_id, "failed", attempt_token=first_token,
        expected_revision=running.revision)
    resumed = store.prepare_resume(
        submitted.job_id, expected_revision=first_failed.revision)
    second = store.load_execution(submitted.job_id)

    with pytest.raises(JobStateError, match="cancel revision is stale"):
        store.request_cancel(
            submitted.job_id,
            expected_revision=first_failed.revision,
            expected_attempt_number=first_failed.attempt_number)
    with pytest.raises(JobStateError, match="cancel targets a stale attempt"):
        store.request_cancel(
            submitted.job_id,
            expected_revision=resumed.revision,
            expected_attempt_number=first_failed.attempt_number)

    job_dir = store.root / submitted.job_id
    assert not (job_dir / "cancel.a00000000000000000002.json").exists()
    assert not store.is_cancel_requested(
        submitted.job_id, second.attempt_token)

    current = store.request_cancel(
        submitted.job_id,
        expected_revision=resumed.revision,
        expected_attempt_number=resumed.attempt_number)
    assert current == resumed
    assert store.is_cancel_requested(
        submitted.job_id, second.attempt_token)


def test_only_recoverable_terminal_states_can_prepare_resume(tmp_path):
    store = JobStore(tmp_path / "jobs")
    summary = store.submit_job("raptor", ["--chunks", "book.json"])
    token, running = _advance_to_running(store, summary.job_id)
    failed = store.transition_job(
        summary.job_id, "failed", attempt_token=token,
        expected_revision=running.revision)

    resumed = store.prepare_resume(
        summary.job_id, expected_revision=failed.revision)
    assert resumed.attempt_number == 2
    assert resumed.revision == failed.revision + 1
    assert resumed.created_at == summary.created_at


@pytest.mark.parametrize("mutation", [
    "invalid-json", "unknown-field", "unsupported-schema", "boolean-schema",
    "oversize",
])
def test_state_parsing_fails_closed(tmp_path, mutation):
    store = JobStore(tmp_path / "jobs")
    summary = store.submit_job("index", ["--chunks", "book.json"])
    state_path = store.root / summary.job_id / "state.json"

    if mutation == "invalid-json":
        storage_policy.atomic_write_private_text(state_path, "{")
    elif mutation == "oversize":
        storage_policy.atomic_write_private_text(state_path, "x" * (40 * 1024))
    else:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
        if mutation == "unknown-field":
            payload["unexpected"] = True
        elif mutation == "boolean-schema":
            payload["schema_version"] = True
        else:
            payload["schema_version"] = 999
        storage_policy.atomic_write_private_json(state_path, payload)

    with pytest.raises(JobCorruptError):
        store.get_job(summary.job_id)
    with pytest.raises(JobCorruptError):
        store.list_jobs()


def test_spec_digest_detects_valid_json_mutation(tmp_path):
    store = JobStore(tmp_path / "jobs")
    summary = store.submit_job("convert", ["--pdf", "one.pdf"])
    spec_path = store.root / summary.job_id / "spec.json"
    payload = json.loads(spec_path.read_text(encoding="utf-8"))
    payload["argv"][-1] = "other.pdf"
    storage_policy.atomic_write_private_json(spec_path, payload)

    with pytest.raises(JobCorruptError, match="immutable spec"):
        store.get_job(summary.job_id)


def test_unreleased_v1_job_layout_is_explicitly_unsupported(tmp_path):
    store = JobStore(tmp_path / "jobs")
    summary = store.submit_job("convert", ["--pdf", "one.pdf"])
    spec_path = store.root / summary.job_id / "spec.json"
    payload = json.loads(spec_path.read_text(encoding="utf-8"))
    payload["schema_version"] = 1
    for field in (
            "working_directory", "working_directory_device",
            "working_directory_inode", "output_root", "output_root_device",
            "output_root_inode"):
        payload.pop(field)
    storage_policy.atomic_write_private_json(spec_path, payload)

    with pytest.raises(JobCorruptError, match="job spec schema is unsupported"):
        store.get_job(summary.job_id)


def test_corrupt_cancel_marker_is_not_silently_ignored(tmp_path):
    store = JobStore(tmp_path / "jobs")
    summary = store.submit_job("full", ["--pdf", "book.pdf"])
    execution = _execution(store, summary.job_id)
    store.request_cancel(summary.job_id)
    cancel_path = (
        store.root / summary.job_id / "cancel.a00000000000000000001.json")
    payload = json.loads(cancel_path.read_text(encoding="utf-8"))
    payload["attempt_token"] = "0" * 32
    storage_policy.atomic_write_private_json(cancel_path, payload)

    with pytest.raises(JobCorruptError, match="conflicts"):
        store.is_cancel_requested(summary.job_id, execution.attempt_token)


def test_cancel_marker_attempt_must_match_its_filename(tmp_path):
    store = JobStore(tmp_path / "jobs")
    summary = store.submit_job("full", ["--pdf", "book.pdf"])
    first_token, running = _advance_to_running(store, summary.job_id)
    failed = store.transition_job(
        summary.job_id, "failed", attempt_token=first_token,
        expected_revision=running.revision)
    store.prepare_resume(summary.job_id, expected_revision=failed.revision)
    second = _execution(store, summary.job_id)
    store.request_cancel(summary.job_id)
    cancel_path = (
        store.root / summary.job_id / "cancel.a00000000000000000002.json")
    payload = json.loads(cancel_path.read_text(encoding="utf-8"))
    payload["attempt_number"] = 1
    storage_policy.atomic_write_private_json(cancel_path, payload)

    with pytest.raises(JobCorruptError, match="does not match its filename"):
        store.is_cancel_requested(summary.job_id, second.attempt_token)


def test_atomic_state_publication_failure_preserves_prior_generation(
        monkeypatch, tmp_path):
    store = JobStore(tmp_path / "jobs")
    initial = store.submit_job("index", ["--chunks", "book.json"])
    execution = _execution(store, initial.job_id)
    original = job_runtime.storage_policy.atomic_write_private_json

    def fail_state(path, payload, **kwargs):
        if Path(path).name == "state.json":
            raise OSError("injected publication failure")
        return original(path, payload, **kwargs)

    monkeypatch.setattr(
        job_runtime.storage_policy, "atomic_write_private_json", fail_state)
    with pytest.raises(OSError, match="injected"):
        store.transition_job(
            initial.job_id, "starting",
            attempt_token=execution.attempt_token)
    monkeypatch.setattr(
        job_runtime.storage_policy, "atomic_write_private_json", original)

    assert store.get_job(initial.job_id) == initial


def test_cross_process_lease_excludes_a_second_manager(tmp_path):
    store = JobStore(tmp_path / "jobs")
    summary = store.submit_job("full", ["--pdf", "book.pdf"])
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    results = context.Queue()
    process = context.Process(
        target=_lease_holder,
        args=(str(store.root), summary.job_id, ready, release, results))
    process.start()
    try:
        assert ready.wait(10)
        assert results.get(timeout=2) == "acquired"
        with pytest.raises(JobBusyError):
            with store.lease(summary.job_id, timeout=0.1):
                pass
    finally:
        release.set()
        process.join(timeout=10)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
    assert process.exitcode == 0


def test_concurrent_terminal_transitions_publish_one_valid_winner(tmp_path):
    store = JobStore(tmp_path / "jobs")
    summary = store.submit_job("full", ["--pdf", "book.pdf"])
    token, running = _advance_to_running(store, summary.job_id)
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    results = context.Queue()
    processes = [
        context.Process(
            target=_transition_worker,
            args=(str(store.root), summary.job_id, token, target,
                  running.revision, start, results))
        for target in ("succeeded", "failed")
    ]
    for process in processes:
        process.start()
    start.set()
    outcomes = [results.get(timeout=15) for _ in processes]
    for process in processes:
        process.join(timeout=10)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)

    assert sum(outcome[0] == "ok" for outcome in outcomes) == 1
    assert sum(outcome[0] == "error" for outcome in outcomes) == 1
    assert {outcome[2] for outcome in outcomes if outcome[0] == "error"} == {
        "JobStateError"}
    final = store.get_job(summary.job_id)
    assert final.status in {"succeeded", "failed"}
    assert final.revision == running.revision + 1
    assert all(process.exitcode == 0 for process in processes)


def test_unexpected_root_entry_and_hardlinked_document_fail_closed(tmp_path):
    store = JobStore(tmp_path / "jobs")
    summary = store.submit_job("convert", ["--pdf", "book.pdf"])
    unexpected = store.root / "notes.txt"
    storage_policy.atomic_write_private_text(unexpected, "not a job")
    with pytest.raises(JobCorruptError, match="unexpected entry"):
        store.list_jobs()
    unexpected.unlink()

    if os.name != "nt":
        state_path = store.root / summary.job_id / "state.json"
        os.link(state_path, store.root / summary.job_id / "state-copy.json")
        with pytest.raises(JobCorruptError, match="bounded private file"):
            store.get_job(summary.job_id)


def test_exact_pipeline_binding_round_trip_is_private_and_immutable(tmp_path):
    store = JobStore(tmp_path / "jobs")
    private_pdf = tmp_path / "Private Casebook.pdf"
    summary = store.submit_job("full", ["--pdf", str(private_pdf)])
    execution = store.load_execution(summary.job_id)

    binding = store.bind_pipeline_run(
        summary.job_id, attempt_token=execution.attempt_token,
        item_index=1, input_path=private_pdf,
        run_name="Private Casebook")
    loaded = store.get_pipeline_binding(
        summary.job_id, item_index=1, input_path=private_pdf)

    assert loaded == binding
    assert binding.status == "allocated"
    bindings_path = store.root / summary.job_id / "bindings.json"
    rendered = bindings_path.read_text(encoding="utf-8")
    assert str(private_pdf) not in rendered
    assert job_runtime.pipeline_input_sha256(private_pdf) in rendered
    _assert_private(bindings_path, directory=False)
    _assert_private(
        store.root / summary.job_id / ".bindings.lock", directory=False)

    failed = store.mark_pipeline_binding(
        summary.job_id, attempt_token=execution.attempt_token,
        item_index=1, input_path=private_pdf, status="failed")
    assert failed.status == "failed"
    reset = store.bind_pipeline_run(
        summary.job_id, attempt_token=execution.attempt_token,
        item_index=1, input_path=private_pdf,
        run_name="Private Casebook")
    assert reset.status == "allocated"
    completed = store.mark_pipeline_binding(
        summary.job_id, attempt_token=execution.attempt_token,
        item_index=1, input_path=private_pdf, status="complete")
    assert completed.status == "complete"
    with pytest.raises(JobStateError, match="immutable"):
        store.mark_pipeline_binding(
            summary.job_id, attempt_token=execution.attempt_token,
            item_index=1, input_path=private_pdf, status="failed")


def test_pipeline_bindings_reject_drift_conflicts_and_stale_attempts(tmp_path):
    store = JobStore(tmp_path / "jobs")
    book = tmp_path / "Book.pdf"
    other = tmp_path / "Other.pdf"
    summary = store.submit_job("batch", [str(book), str(other)])
    first = store.load_execution(summary.job_id)
    store.bind_pipeline_run(
        summary.job_id, attempt_token=first.attempt_token,
        item_index=1, input_path=book, run_name="Book_2")

    with pytest.raises(JobStateError, match="another input"):
        store.get_pipeline_binding(
            summary.job_id, item_index=1, input_path=other)
    with pytest.raises(JobStateError, match="another exact"):
        store.bind_pipeline_run(
            summary.job_id, attempt_token=first.attempt_token,
            item_index=1, input_path=book, run_name="Book_3")
    with pytest.raises(JobValidationError, match="does not match"):
        store.bind_pipeline_run(
            summary.job_id, attempt_token=first.attempt_token,
            item_index=2, input_path=other, run_name="Book")

    starting = store.transition_job(
        summary.job_id, "starting", attempt_token=first.attempt_token)
    running = store.transition_job(
        summary.job_id, "running", attempt_token=first.attempt_token,
        expected_revision=starting.revision)
    failed = store.transition_job(
        summary.job_id, "failed", attempt_token=first.attempt_token,
        expected_revision=running.revision)
    store.prepare_resume(summary.job_id, expected_revision=failed.revision)
    second = store.load_execution(summary.job_id)

    with pytest.raises(JobStateError, match="stale attempt"):
        store.bind_pipeline_run(
            summary.job_id, attempt_token=first.attempt_token,
            item_index=1, input_path=book, run_name="Book_2")
    resumed = store.bind_pipeline_run(
        summary.job_id, attempt_token=second.attempt_token,
        item_index=1, input_path=book, run_name="Book_2")
    assert resumed.status == "allocated"


def test_pipeline_binding_growth_preserves_last_loadable_file(tmp_path):
    store = JobStore(tmp_path / "jobs")
    summary = store.submit_job("batch", ["Book.pdf"])
    execution = store.load_execution(summary.job_id)
    _spec, state = store._load_record(summary.job_id)
    run_name = "r" * 1000
    input_path = f"{run_name}.pdf"
    input_sha256 = job_runtime.pipeline_input_sha256(input_path)
    items = []
    crossing_index = None
    for item_index in range(1, 513):
        candidate = job_runtime.PipelineRunBinding(
            item_index=item_index, input_sha256=input_sha256,
            run_name=run_name, status="allocated")
        proposed = job_runtime._StoredBindings(
            job_id=summary.job_id, spec_sha256=state.spec_sha256,
            revision=2, items=tuple([*items, candidate]))
        if len(job_runtime._canonical_json_bytes(
                job_runtime._bindings_payload(proposed))) > (
                job_runtime._MAX_BINDINGS_BYTES):
            crossing_index = item_index
            break
        items.append(candidate)
    assert items and crossing_index is not None
    stored = job_runtime._StoredBindings(
        job_id=summary.job_id, spec_sha256=state.spec_sha256,
        revision=1, items=tuple(items))
    bindings_path = store.root / summary.job_id / "bindings.json"
    storage_policy.atomic_write_private_json(
        bindings_path, job_runtime._bindings_payload(stored))
    original = bindings_path.read_bytes()

    with pytest.raises(
            JobValidationError, match="pipeline bindings exceed"):
        store.bind_pipeline_run(
            summary.job_id, attempt_token=execution.attempt_token,
            item_index=crossing_index, input_path=input_path,
            run_name=run_name)

    assert bindings_path.read_bytes() == original
    assert store.get_pipeline_binding(
        summary.job_id, item_index=items[-1].item_index,
        input_path=input_path) == items[-1]
    assert store.get_pipeline_binding(
        summary.job_id, item_index=crossing_index,
        input_path=input_path) is None


def test_worker_context_requires_complete_matching_manager_environment(tmp_path):
    store = JobStore(tmp_path / "jobs")
    summary = store.submit_job("full", ["--pdf", "Book.pdf"])
    execution = store.load_execution(summary.job_id)
    environment = {
        job_runtime.JOB_ROOT_ENV: str(store.root),
        job_runtime.JOB_ID_ENV: summary.job_id,
        job_runtime.JOB_ATTEMPT_TOKEN_ENV: execution.attempt_token,
    }

    assert job_runtime.load_worker_context("full", environ={}) is None
    context = job_runtime.load_worker_context("full", environ=environment)
    assert context is not None
    assert context.execution.job_id == summary.job_id
    assert context.execution.attempt_token == execution.attempt_token
    with pytest.raises(JobValidationError, match="incomplete"):
        job_runtime.load_worker_context(
            "full", environ={job_runtime.JOB_ROOT_ENV: str(store.root)})
    with pytest.raises(JobStateError, match="command"):
        job_runtime.load_worker_context("batch", environ=environment)
    stale_environment = dict(environment)
    stale_environment[job_runtime.JOB_ATTEMPT_TOKEN_ENV] = "f" * 32
    with pytest.raises(JobStateError, match="stale"):
        job_runtime.load_worker_context(
            "full", environ=stale_environment)


def test_non_pipeline_jobs_cannot_create_exact_run_bindings(tmp_path):
    store = JobStore(tmp_path / "jobs")
    summary = store.submit_job("index", ["--chunks", "Book.jsonl"])
    execution = store.load_execution(summary.job_id)

    with pytest.raises(JobStateError, match="only to full and batch"):
        store.bind_pipeline_run(
            summary.job_id, attempt_token=execution.attempt_token,
            item_index=1, input_path="Book.pdf", run_name="Book")


def test_cancel_publication_finishes_before_a_new_attempt_can_be_created(
        monkeypatch, tmp_path):
    store = JobStore(tmp_path / "jobs")
    submitted = store.submit_job("full", ["--pdf", "Book.pdf"])
    first, running = _advance_to_running(store, submitted.job_id)
    entered = threading.Event()
    release = threading.Event()
    errors = []
    original = storage_policy.atomic_write_private_json

    def delayed_write(path, payload, *args, **kwargs):
        if (payload.get("kind") == "job_cancel_request"
                and payload.get("attempt_number") == 1):
            entered.set()
            assert release.wait(10)
        return original(path, payload, *args, **kwargs)

    monkeypatch.setattr(
        storage_policy, "atomic_write_private_json", delayed_write)

    def request_first_cancel():
        try:
            store.request_cancel(submitted.job_id)
        except Exception as exc:  # surfaced after joining the race
            errors.append(exc)

    thread = threading.Thread(target=request_first_cancel)
    thread.start()
    assert entered.wait(10)
    mutation_done = threading.Event()
    mutation_results = []

    def finish_resume_and_cancel_again():
        try:
            failed = store.transition_job(
                submitted.job_id, "failed", attempt_token=first,
                expected_revision=running.revision)
            resumed = store.prepare_resume(
                submitted.job_id, expected_revision=failed.revision)
            second = store.load_execution(submitted.job_id)
            store.request_cancel(submitted.job_id)
            mutation_results.append((resumed, second))
        except Exception as exc:
            errors.append(exc)
        finally:
            mutation_done.set()

    mutation_thread = threading.Thread(target=finish_resume_and_cancel_again)
    mutation_thread.start()
    assert not mutation_done.wait(0.2)
    release.set()
    thread.join(timeout=10)
    mutation_thread.join(timeout=10)

    assert not thread.is_alive()
    assert not mutation_thread.is_alive()
    assert not errors
    resumed, second = mutation_results[0]
    assert resumed.attempt_number == 2
    assert store.is_cancel_requested(
        submitted.job_id, second.attempt_token)
    job_dir = store.root / submitted.job_id
    assert (job_dir / "cancel.a00000000000000000001.json").is_file()
    assert (job_dir / "cancel.a00000000000000000002.json").is_file()


def test_orphaned_attempt_is_terminal_but_never_resumable(tmp_path):
    store = JobStore(tmp_path / "jobs")
    submitted = store.submit_job("index", ["--chunks", "Book.jsonl"])
    token, running = _advance_to_running(store, submitted.job_id)
    orphaned = store.transition_job(
        submitted.job_id, "orphaned", attempt_token=token,
        expected_revision=running.revision)

    assert orphaned.terminal
    with pytest.raises(JobStateError, match="not resumable"):
        store.prepare_resume(submitted.job_id)


def test_submission_directories_are_immutable_private_bindings(tmp_path):
    working = tmp_path / "working"
    working.mkdir()
    output = working / "private-output"
    store = JobStore(tmp_path / "jobs")
    submitted = store.submit_job(
        "export", ["--chunks", "Book.jsonl"],
        working_directory=working, output_root=output)
    execution = store.load_execution(submitted.job_id)

    assert execution.working_directory == working.resolve()
    assert execution.output_root == output.resolve()
    assert str(working) not in json.dumps(submitted.as_dict())
    job_runtime.validate_execution_directories(execution)

    held_directory = (
        os.open(output, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        if os.name != "nt" else None)
    try:
        output.rmdir()
        storage_policy.ensure_private_directory(output)
        with pytest.raises(JobStateError, match="output root identity changed"):
            job_runtime.validate_execution_directories(execution)
    finally:
        if held_directory is not None:
            os.close(held_directory)


def test_prepare_delete_blocks_resume_and_rejects_unsafe_states(tmp_path):
    store = JobStore(tmp_path / "jobs")
    submitted = store.submit_job("export", ["--chunks", "Book.jsonl"])
    token, running = _advance_to_running(store, submitted.job_id)
    with pytest.raises(JobStateError, match="cannot be deleted"):
        store.prepare_delete(submitted.job_id)
    failed = store.transition_job(
        submitted.job_id, "failed", attempt_token=token,
        expected_revision=running.revision)

    deleting = store.prepare_delete(submitted.job_id)

    assert deleting.status == "deleting"
    assert deleting.revision == failed.revision + 1
    with pytest.raises(JobStateError, match="not resumable"):
        store.prepare_resume(submitted.job_id)


def test_delete_preconditions_reject_stale_attempt_and_allow_current(tmp_path):
    store = JobStore(tmp_path / "jobs")
    submitted = store.submit_job("export", ["--chunks", "Book.jsonl"])
    first_token, first_running = _advance_to_running(
        store, submitted.job_id)
    first_failed = store.transition_job(
        submitted.job_id, "failed", attempt_token=first_token,
        expected_revision=first_running.revision)
    store.prepare_resume(
        submitted.job_id, expected_revision=first_failed.revision)
    second_token, second_running = _advance_to_running(
        store, submitted.job_id)
    second_failed = store.transition_job(
        submitted.job_id, "failed", attempt_token=second_token,
        expected_revision=second_running.revision)

    with pytest.raises(JobStateError, match="delete revision is stale"):
        store.prepare_delete(
            submitted.job_id,
            expected_revision=first_failed.revision,
            expected_attempt_number=first_failed.attempt_number)
    assert store.get_job(submitted.job_id) == second_failed
    with pytest.raises(JobStateError, match="delete targets a stale attempt"):
        store.prepare_delete(
            submitted.job_id,
            expected_revision=second_failed.revision,
            expected_attempt_number=first_failed.attempt_number)
    assert store.get_job(submitted.job_id) == second_failed

    deleting = store.prepare_delete(
        submitted.job_id,
        expected_revision=second_failed.revision,
        expected_attempt_number=second_failed.attempt_number)
    assert deleting.status == "deleting"
    assert deleting.revision == second_failed.revision + 1
    with pytest.raises(JobStateError, match="delete revision is stale"):
        store.prepare_delete(
            submitted.job_id,
            expected_revision=second_failed.revision,
            expected_attempt_number=second_failed.attempt_number)


@pytest.mark.parametrize(("argument", "value"), [
    ("expected_revision", True),
    ("expected_revision", -1),
    ("expected_revision", job_runtime._MAX_COUNTER + 1),
    ("expected_revision", 1.0),
    ("expected_revision", "1"),
    ("expected_attempt_number", True),
    ("expected_attempt_number", 0),
    ("expected_attempt_number", -1),
    ("expected_attempt_number", job_runtime._MAX_COUNTER + 1),
    ("expected_attempt_number", 1.0),
    ("expected_attempt_number", "1"),
])
@pytest.mark.parametrize("mutation", ["cancel", "delete"])
def test_cancel_and_delete_validate_optimistic_preconditions(
        tmp_path, mutation, argument, value):
    store = JobStore(tmp_path / "jobs")
    submitted = store.submit_job("export", ["--chunks", "Book.jsonl"])
    if mutation == "delete":
        token, running = _advance_to_running(store, submitted.job_id)
        before = store.transition_job(
            submitted.job_id, "failed", attempt_token=token,
            expected_revision=running.revision)
        operation = store.prepare_delete
    else:
        before = submitted
        operation = store.request_cancel

    with pytest.raises(JobValidationError, match=argument):
        operation(submitted.job_id, **{argument: value})

    assert store.get_job(submitted.job_id) == before
    assert not any(
        path.name.startswith("cancel.a")
        for path in (store.root / submitted.job_id).iterdir())
