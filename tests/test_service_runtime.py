import json
import os
import queue
import stat
import subprocess
import sys
import textwrap
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

import job_coordination
import job_manager
import job_runtime
import retention
import release_security
import retrieval_core
import service_contracts
import service_runtime
import service_runtime_binding
import storage_policy


_PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _config(tmp_path: Path) -> service_contracts.CorpusConfig:
    db_path = tmp_path / "qdrant"
    db_path.mkdir(exist_ok=True)
    chunks_path = tmp_path / "chunks.jsonl"
    chunks_path.write_text(
        '{"text":"holding","metadata":{"content_type":"case_opinion"}}\n',
        encoding="utf-8")
    return service_contracts.CorpusConfig(
        corpus_id="property",
        db_path=db_path.resolve(),
        chunks_path=chunks_path.resolve(),
        collection_name="property_collection",
        embedding_model="test-model",
        search_timeout_seconds=2,
        db_lock_timeout_seconds=1,
        reindex_timeout_seconds=30,
    )


def _empty_search_response(config, request, request_id):
    return {
        "schema_version": 1,
        "request_id": request_id,
        "corpus_id": config.corpus_id,
        "backend": "qdrant",
        "requested_mode": request.mode,
        "effective_mode": "vector",
        "reranker_applied": False,
        "warnings": [],
        "hits": [],
    }


def _runtime_binding(**changes):
    return replace(
        service_runtime_binding.default_service_runtime_binding(),
        **changes,
    )


def _coordination_binding(**changes):
    return replace(
        job_coordination.default_service_job_coordination_binding(),
        **changes,
    )


_SERVICE_HOLDER_SCRIPT = textwrap.dedent(
    """
    from pathlib import Path
    import sys

    import service_contracts
    import service_runtime

    assert "job_manager" not in sys.modules
    assert "rag" not in sys.modules

    root = Path(sys.argv[1]).resolve()
    config = service_contracts.CorpusConfig(
        corpus_id="property",
        db_path=(root / "qdrant").resolve(),
        chunks_path=(root / "chunks.jsonl").resolve(),
        collection_name="property_collection",
        embedding_model="test-model",
    )
    service = service_runtime.RagApplicationService(
        {config.corpus_id: config},
        job_root=root / "jobs",
        working_directory=root,
        output_root=root / "output",
        service_state_root=root / "service-state",
    )
    assert "job_manager" not in sys.modules
    assert "rag" not in sys.modules
    service.start()
    assert "job_manager" not in sys.modules
    assert "rag" not in sys.modules
    print("ACQUIRED", flush=True)
    sys.stdin.readline()
    service.close()
    """
)


def _launch_starting(store, job_id, *, ready_timeout):
    del ready_timeout
    execution = store.load_execution(job_id)
    if execution.status == "queued":
        summary = store.transition_job(
            job_id, "starting",
            attempt_token=execution.attempt_token,
            expected_revision=execution.revision)
    else:
        summary = store.get_job(job_id)
    return job_manager.LaunchResult(
        job_id=job_id,
        status=summary.status,
        attempt_number=summary.attempt_number,
        ready=True,
    )


def _service(tmp_path, *, search_runner=None, launcher=_launch_starting,
             job_binding=None, state_name="service-state", max_searches=2):
    config = _config(tmp_path)
    options = {}
    if search_runner is not None:
        options["search_runner"] = search_runner
    service = service_runtime.RagApplicationService(
        {config.corpus_id: config},
        job_root=tmp_path / "jobs",
        working_directory=tmp_path,
        output_root=tmp_path / "output",
        service_state_root=tmp_path / state_name,
        max_concurrent_searches=max_searches,
        job_coordination_binding=job_binding,
        launcher=launcher,
        **options,
    )
    service.start()
    return service


def _advance_failed(store, job_id):
    execution = store.load_execution(job_id)
    starting = store.transition_job(
        job_id, "starting", attempt_token=execution.attempt_token,
        expected_revision=execution.revision)
    running = store.transition_job(
        job_id, "running", attempt_token=execution.attempt_token,
        expected_revision=starting.revision)
    return store.transition_job(
        job_id, "failed", attempt_token=execution.attempt_token,
        expected_revision=running.revision)


def _submit_service_job(service, *, full_reindex=False, job_id=None):
    config = service.corpora["property"]
    request = service_contracts.ReindexRequest(full_reindex)
    return service.store.submit_job(
        "index", service._reindex_argv(config, request),
        timeout_seconds=config.reindex_timeout_seconds,
        job_id=job_id,
        working_directory=service.working_directory,
        output_root=service.output_root,
    )


def test_registry_loader_is_strict_private_and_resolves_relative_paths(
        tmp_path):
    config_path = tmp_path / "service.json"
    config_path.write_text(json.dumps({
        "schema_version": 1,
        "corpora": [{
            "corpus_id": "property",
            "backend": "qdrant",
            "db_path": "db",
            "chunks_path": "chunks.jsonl",
            "collection_name": "property",
            "embedding_model": "test-model",
        }],
    }), encoding="utf-8")

    registry = service_runtime.load_corpus_registry(config_path)

    assert registry["property"].db_path == (tmp_path / "db").resolve()
    assert registry["property"].chunks_path == (
        tmp_path / "chunks.jsonl").resolve()
    if os.name != "nt":
        assert stat.S_IMODE(config_path.stat().st_mode) == 0o600
    assert str(tmp_path) not in json.dumps(
        registry["property"].public_dict())


@pytest.mark.parametrize(
    "embedding_model",
    [
        "voyage-law-2",
        "text-embedding-3-small",
        "embed-english-v3.0",
        "cohere-embed-v4.0",
        "embo-01",
        "minimax-embedding-01",
    ],
)
def test_service_rejects_every_cloud_embedding_family_before_filesystem_setup(
        tmp_path, embedding_model):
    config = _config(tmp_path)
    cloud = service_contracts.CorpusConfig(
        corpus_id=config.corpus_id,
        db_path=config.db_path,
        chunks_path=config.chunks_path,
        collection_name=config.collection_name,
        embedding_model=embedding_model,
    )
    service_output = tmp_path / "not-created" / "output"

    with pytest.raises(
            release_security.ReleaseSecurityError,
            match="network-policy allow-cloud"):
        service_runtime.RagApplicationService(
            {cloud.corpus_id: cloud},
            job_root=tmp_path / "not-created" / "jobs",
            working_directory=tmp_path,
            output_root=service_output,
        )
    assert not service_output.parent.exists()


def test_service_worker_receipt_round_trips_opaque_policy(tmp_path):
    config = _config(tmp_path)
    request = service_contracts.SearchRequest("private query")
    policy = release_security.ReleaseSecurityPolicy.from_values(
        profile="development", network_policy="allow-cloud",
        cache_namespace="tenant-label",
        model_download_policy="allow-reviewed-sync",
    )

    parsed = service_runtime._parse_worker_request(
        service_runtime._search_request_payload(
            config, request, "req-1", security_policy=policy))

    assert parsed[3] == policy
    assert "tenant-label" not in json.dumps(
        service_runtime._search_request_payload(
            config, request, "req-1", security_policy=policy))


def test_service_reindex_job_pins_versioned_opaque_policy(tmp_path):
    config = _config(tmp_path)
    policy = release_security.ReleaseSecurityPolicy.from_values(
        cache_namespace="tenant-label")
    service = service_runtime.RagApplicationService(
        {config.corpus_id: config},
        job_root=tmp_path / "jobs",
        working_directory=tmp_path,
        output_root=tmp_path / "output",
        security_policy=policy,
    )

    argv = service._reindex_argv(
        config, service_contracts.ReindexRequest())

    assert "tenant-label" not in argv
    assert "--release-security-policy-version" in argv
    namespace_index = argv.index("--release-cache-namespace-id")
    assert argv[namespace_index + 1] == policy.cache_namespace_id


def test_committed_service_config_example_matches_registry_contract(tmp_path):
    source = Path(__file__).resolve().parents[1] / "service-config.example.json"
    config_path = tmp_path / "service.json"
    config_path.write_bytes(source.read_bytes())

    registry = service_runtime.load_corpus_registry(config_path)

    assert tuple(registry) == ("civil_procedure",)
    assert registry["civil_procedure"].collection_name == "civil_procedure"
    assert registry["civil_procedure"].embedding_model == (
        "text-embedding-3-small")


@pytest.mark.parametrize("mutator", [
    lambda payload: payload.update({"extra": True}),
    lambda payload: payload.update({"schema_version": 2}),
    lambda payload: payload.update({"corpora": []}),
    lambda payload: payload["corpora"].append(dict(payload["corpora"][0])),
    lambda payload: payload["corpora"][0].update({"backend": "chroma"}),
])
def test_registry_loader_rejects_unknown_versions_duplicates_and_chroma(
        tmp_path, mutator):
    payload = {
        "schema_version": 1,
        "corpora": [{
            "corpus_id": "property",
            "backend": "qdrant",
            "db_path": "db",
            "chunks_path": "chunks.jsonl",
            "collection_name": "property",
            "embedding_model": "test-model",
        }],
    }
    mutator(payload)
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(service_contracts.ServiceContractError):
        service_runtime.load_corpus_registry(path)


def test_registry_loader_rejects_lexical_symlink_before_resolution(tmp_path):
    target = tmp_path / "target.json"
    storage_policy.atomic_write_private_json(target, {
        "schema_version": 1,
        "corpora": [{
            "corpus_id": "property",
            "backend": "qdrant",
            "db_path": "db",
            "chunks_path": "chunks.jsonl",
            "collection_name": "property",
            "embedding_model": "test-model",
        }],
    })
    link = tmp_path / "service.json"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    with pytest.raises(storage_policy.StoragePolicyError):
        service_runtime.load_corpus_registry(link)


def test_service_start_reconciles_once_and_holds_single_instance(
        monkeypatch, tmp_path):
    import rag

    calls = []
    real_reconcile = service_runtime.RagApplicationService._reconcile_service_jobs

    def track_reconcile(self, **options):
        calls.append(self.store.root)
        return real_reconcile(self, **options)

    monkeypatch.setattr(
        service_runtime.RagApplicationService, "_reconcile_service_jobs",
        track_reconcile)
    first = _service(tmp_path)
    second = service_runtime.RagApplicationService(
        first.corpora,
        job_root=first.store.root,
        working_directory=tmp_path,
        output_root=first.output_root,
        service_state_root=first.service_state_root,
        launcher=_launch_starting,
    )
    try:
        assert calls == [first.store.root]
        assert first.healthy
        assert first.readiness()
        assert first._instance_lease.lock_path.parent == (
            first.service_state_root / ".rag-locks")
        assert first._instance_lease.lock_path == (
            rag._vector_store_lock_path(
                first.service_state_root / "instance"))
        assert not os.path.lexists(first.service_state_root / "instance")
        with pytest.raises(service_runtime.ServiceRuntimeError) as raised:
            second.start()
        assert raised.value.code == "service_unavailable"
    finally:
        first.close()

    second.start()
    second.close()


def test_service_instance_lease_is_cross_process_and_crash_released(tmp_path):
    config = _config(tmp_path)
    holder = subprocess.Popen(
        [sys.executable, "-c", _SERVICE_HOLDER_SCRIPT, str(tmp_path)],
        cwd=_PROJECT_ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        ready_queue = queue.Queue()
        threading.Thread(
            target=lambda: ready_queue.put(holder.stdout.readline()),
            daemon=True,
        ).start()
        try:
            ready = ready_queue.get(timeout=15)
        except queue.Empty:
            holder.kill()
            holder.wait(timeout=5)
            pytest.fail(
                "service holder did not acquire: " + holder.stderr.read())
        assert ready.strip() == "ACQUIRED", (
            holder.stderr.read() if holder.poll() is not None else ready)

        contender = service_runtime.RagApplicationService(
            {config.corpus_id: config},
            job_root=tmp_path / "jobs",
            working_directory=tmp_path,
            output_root=tmp_path / "output",
            service_state_root=tmp_path / "service-state",
            launcher=_launch_starting,
        )
        with pytest.raises(service_runtime.ServiceRuntimeError) as raised:
            contender.start()
        assert raised.value.code == "service_unavailable"
        assert raised.value.fatal is True

        other_root = tmp_path / "other"
        other_root.mkdir()
        other_config = _config(other_root)
        independent = service_runtime.RagApplicationService(
            {other_config.corpus_id: other_config},
            job_root=other_root / "jobs",
            working_directory=other_root,
            output_root=other_root / "output",
            service_state_root=other_root / "service-state",
            launcher=_launch_starting,
        )
        independent.start()
        independent.close()

        holder.kill()
        holder.wait(timeout=5)

        successor = service_runtime.RagApplicationService(
            {config.corpus_id: config},
            job_root=tmp_path / "jobs",
            working_directory=tmp_path,
            output_root=tmp_path / "output",
            service_state_root=tmp_path / "service-state",
            launcher=_launch_starting,
        )
        successor.start()
        successor.close()
    finally:
        if holder.poll() is None:
            holder.kill()
            holder.wait(timeout=5)
        if holder.stdin is not None:
            holder.stdin.close()
        if holder.stdout is not None:
            holder.stdout.close()
        if holder.stderr is not None:
            holder.stderr.close()


def test_service_snapshots_one_complete_runtime_binding_at_construction(
        monkeypatch, tmp_path):
    events = []

    class FirstCleanupError(RuntimeError):
        pass

    class SecondCleanupError(RuntimeError):
        pass

    class Lease:
        def __init__(self, label, path):
            self.label = label
            self.path = path

        def __enter__(self):
            events.append(("enter", self.label, self.path))
            return self

        def __exit__(self, *_args):
            events.append(("exit", self.label, self.path))

    def first_supervisor(script_path, _argv, **_kwargs):
        events.append(("supervisor", script_path))
        raise FirstCleanupError("first cleanup policy")

    def first_predicate(model_name):
        events.append(("predicate", model_name))
        return False

    def unexpected(*_args, **_kwargs):
        pytest.fail("a later default binding generation was mixed in")

    first_worker = tmp_path / "first-worker.py"
    first_binding = _runtime_binding(
        worker_script_path=first_worker,
        supervisor=first_supervisor,
        cleanup_error_type=FirstCleanupError,
        instance_lease_factory=lambda path: Lease("first", path),
        is_api_embedding_model=first_predicate,
    )
    second_binding = _runtime_binding(
        worker_script_path=tmp_path / "second-worker.py",
        supervisor=unexpected,
        cleanup_error_type=SecondCleanupError,
        instance_lease_factory=unexpected,
        is_api_embedding_model=unexpected,
    )
    monkeypatch.setattr(
        service_runtime,
        "_default_service_runtime_binding",
        lambda: first_binding,
    )
    config = _config(tmp_path)
    service = service_runtime.RagApplicationService(
        {config.corpus_id: config},
        job_root=tmp_path / "jobs",
        working_directory=tmp_path,
        output_root=tmp_path / "output",
        service_state_root=tmp_path / "service-state",
        launcher=_launch_starting,
    )
    monkeypatch.setattr(
        service_runtime,
        "_default_service_runtime_binding",
        lambda: second_binding,
    )

    service.start()
    with pytest.raises(service_runtime.ServiceRuntimeError) as raised:
        service.search(
            config.corpus_id,
            service_contracts.SearchRequest("private query"),
            "req-binding-snapshot",
        )
    assert raised.value.code == "service_unavailable"
    assert raised.value.fatal is True
    service.close()

    instance_path = service.service_state_root / "instance"
    assert events == [
        ("predicate", config.embedding_model),
        ("enter", "first", instance_path),
        ("supervisor", first_worker),
        ("exit", "first", instance_path),
    ]


def test_service_snapshots_job_coordination_and_explicit_launcher_wins(
        monkeypatch, tmp_path):
    events = []

    class FirstCorrupt(job_manager.JobManagerCorruptError):
        pass

    class SecondCorrupt(job_manager.JobManagerCorruptError):
        pass

    def first_reconcile(store, job_id, **kwargs):
        events.append(("reconcile", store, job_id, kwargs))
        raise FirstCorrupt("first generation")

    def unexpected(*_args, **_kwargs):
        pytest.fail("a replaced coordination generation was mixed in")

    first_binding = _coordination_binding(
        launch_detached=unexpected,
        reconcile_job=first_reconcile,
        corrupt_error_type=FirstCorrupt,
    )
    second_binding = _coordination_binding(
        launch_detached=unexpected,
        reconcile_job=unexpected,
        corrupt_error_type=SecondCorrupt,
    )
    monkeypatch.setattr(
        service_runtime,
        "_default_service_job_coordination_binding",
        lambda: first_binding,
    )

    config = _config(tmp_path)

    def explicit_launch(store, job_id, *, ready_timeout):
        events.append(("launch", store, job_id, ready_timeout))
        return _launch_starting(
            store, job_id, ready_timeout=ready_timeout)

    service = service_runtime.RagApplicationService(
        {config.corpus_id: config},
        job_root=tmp_path / "jobs",
        working_directory=tmp_path,
        output_root=tmp_path / "output",
        service_state_root=tmp_path / "service-state",
        launcher=explicit_launch,
    )
    monkeypatch.setattr(
        service_runtime,
        "_default_service_job_coordination_binding",
        lambda: second_binding,
    )

    service.start()
    try:
        created = service.reindex(
            "property", service_contracts.ReindexRequest(),
            idempotency_key="coordination-snapshot-1234")
        current = service.store.get_job(created.job["job_id"])
        with pytest.raises(service_runtime.ServiceRuntimeError) as raised:
            service.cancel_job(
                current.job_id,
                attempt_number=current.attempt_number,
                revision=current.revision,
            )
        assert raised.value.fatal is True
        assert not service.healthy
    finally:
        service.close()

    assert service._job_coordination is first_binding
    assert events[0] == (
        "launch", service.store, created.job["job_id"],
        service.ready_timeout_seconds)
    assert events[1] == (
        "reconcile", service.store, created.job["job_id"], {})


def test_service_omitted_launcher_delegates_to_bound_generation_exactly(
        tmp_path):
    calls = []

    def bound_launch(store, job_id, *, ready_timeout):
        calls.append((store, job_id, ready_timeout))
        return _launch_starting(
            store, job_id, ready_timeout=ready_timeout)

    config = _config(tmp_path)
    service = service_runtime.RagApplicationService(
        {config.corpus_id: config},
        job_root=tmp_path / "jobs",
        working_directory=tmp_path,
        output_root=tmp_path / "output",
        service_state_root=tmp_path / "service-state",
        job_coordination_binding=_coordination_binding(
            launch_detached=bound_launch),
    )
    service.start()
    try:
        created = service.reindex(
            "property", service_contracts.ReindexRequest(),
            idempotency_key="bound-default-launch-1234")
    finally:
        service.close()

    assert calls == [(
        service.store,
        created.job["job_id"],
        service.ready_timeout_seconds,
    )]


def test_service_reconciliation_passes_exact_active_lease_and_queue_policy(
        tmp_path):
    calls = []

    def reconcile(store, job_id, **kwargs):
        calls.append((store, job_id, kwargs.copy()))
        lease = kwargs["lease"]
        assert lease.active
        store.load_execution(job_id, lease=lease)
        return store.get_job(job_id)

    service = _service(
        tmp_path,
        job_binding=_coordination_binding(reconcile_job=reconcile),
    )
    try:
        submitted = _submit_service_job(service)
        service._launching_job_ids.add(submitted.job_id)
        service._reconcile_service_jobs(fail_queued=True)
        service._launching_job_ids.clear()
        service._reconcile_service_jobs(fail_queued=True)
    finally:
        service.close()

    assert len(calls) == 2
    assert calls[0][0:2] == (service.store, submitted.job_id)
    assert calls[0][2]["fail_queued"] is False
    assert calls[1][2]["fail_queued"] is True
    assert calls[0][2]["lease"] is not calls[1][2]["lease"]
    assert not calls[0][2]["lease"].active
    assert not calls[1][2]["lease"].active


def test_service_reconciliation_skips_terminal_and_foreign_jobs(tmp_path):
    calls = []

    def reconcile(store, job_id, **kwargs):
        calls.append((store, job_id, kwargs.copy()))
        return store.get_job(job_id)

    service = _service(
        tmp_path,
        job_binding=_coordination_binding(reconcile_job=reconcile),
    )
    try:
        active = _submit_service_job(service)
        terminal = _submit_service_job(service)
        terminal = _advance_failed(service.store, terminal.job_id)
        foreign = service.store.submit_job(
            "export", ["--chunks", "private.jsonl"])

        results = service._reconcile_service_jobs(fail_queued=False)
    finally:
        service.close()

    assert [call[1] for call in calls] == [active.job_id]
    assert calls[0][2]["fail_queued"] is False
    assert not calls[0][2]["lease"].active
    assert {result.job_id for result in results} == {
        active.job_id, terminal.job_id}
    assert foreign.job_id not in {result.job_id for result in results}


def test_service_releases_instance_lease_after_startup_failure(
        monkeypatch, tmp_path):
    events = []

    class Lease:
        def __enter__(self):
            events.append("enter")
            return self

        def __exit__(self, exc_type, exc, traceback):
            events.append(("exit", exc_type, exc, traceback))

    config = _config(tmp_path)
    service = service_runtime.RagApplicationService(
        {config.corpus_id: config},
        job_root=tmp_path / "jobs",
        working_directory=tmp_path,
        output_root=tmp_path / "output",
        service_state_root=tmp_path / "service-state",
        launcher=_launch_starting,
        runtime_binding=_runtime_binding(
            instance_lease_factory=lambda _path: Lease()),
    )
    failure = RuntimeError("injected startup failure")
    monkeypatch.setattr(
        service,
        "_reconcile_service_jobs",
        lambda **_kwargs: (_ for _ in ()).throw(failure),
    )

    with pytest.raises(service_runtime.ServiceRuntimeError) as raised:
        service.start()

    assert raised.value.fatal is True
    assert events[0] == "enter"
    assert events[1][0:3] == ("exit", RuntimeError, failure)
    assert service._service_key not in service_runtime._ACTIVE_SERVICE_KEYS


def test_service_does_not_exit_an_instance_lease_that_failed_to_enter(
        tmp_path):
    events = []

    class EnterFailureLease:
        def __enter__(self):
            events.append("enter")
            raise RuntimeError("lease acquisition failed")

        def __exit__(self, *_args):
            events.append("exit")

    config = _config(tmp_path)
    service = service_runtime.RagApplicationService(
        {config.corpus_id: config},
        job_root=tmp_path / "jobs",
        working_directory=tmp_path,
        output_root=tmp_path / "output",
        service_state_root=tmp_path / "service-state",
        launcher=_launch_starting,
        runtime_binding=_runtime_binding(
            instance_lease_factory=lambda _path: EnterFailureLease()),
    )

    with pytest.raises(service_runtime.ServiceRuntimeError) as raised:
        service.start()

    assert raised.value.fatal is True
    assert events == ["enter"]
    assert service._service_key not in service_runtime._ACTIVE_SERVICE_KEYS


def test_default_service_job_root_is_dedicated_and_ready_timeout_is_bounded(
        tmp_path):
    config = _config(tmp_path)
    service = service_runtime.RagApplicationService(
        {config.corpus_id: config},
        working_directory=tmp_path,
        output_root=tmp_path / "output",
        service_state_root=tmp_path / "service-state",
        launcher=_launch_starting,
    )
    assert service.store.root.name == ".rag-service-jobs"

    with pytest.raises(service_contracts.ServiceContractError):
        service_runtime.RagApplicationService(
            {config.corpus_id: config},
            working_directory=tmp_path,
            output_root=tmp_path / "other-output",
            service_state_root=tmp_path / "other-state",
            ready_timeout_seconds=(
                service_contracts.MAX_READY_TIMEOUT_SECONDS + 1),
            launcher=_launch_starting,
        )


def test_unowned_nonempty_job_root_and_mismatched_service_state_fail_closed(
        tmp_path):
    config = _config(tmp_path)
    unowned = service_runtime.RagApplicationService(
        {config.corpus_id: config},
        job_root=tmp_path / "unowned-jobs",
        working_directory=tmp_path,
        output_root=tmp_path / "output",
        service_state_root=tmp_path / "unowned-state",
        launcher=_launch_starting,
    )
    unowned.store.submit_job(
        "export", ["--chunks", "foreign.jsonl"],
        working_directory=tmp_path, output_root=unowned.output_root)
    with pytest.raises(service_runtime.ServiceRuntimeError) as raised:
        unowned.start()
    assert raised.value.fatal is True

    first = _service(tmp_path, state_name="owned-state")
    first.close()
    mismatched = service_runtime.RagApplicationService(
        first.corpora,
        job_root=first.store.root,
        working_directory=tmp_path,
        output_root=first.output_root,
        service_state_root=tmp_path / "different-state",
        launcher=_launch_starting,
    )
    with pytest.raises(service_runtime.ServiceRuntimeError) as raised:
        mismatched.start()
    assert raised.value.fatal is True

    restarted = _service(tmp_path, state_name="owned-state")
    restarted.close()


def test_foreign_jobs_are_hidden_unmodified_and_skipped_across_restart(
        tmp_path):
    service = _service(tmp_path)
    foreign = service.store.submit_job(
        "export", ["--chunks", "C:/private/foreign.jsonl"],
        working_directory=tmp_path, output_root=service.output_root)
    foreign_lock = service.store.root / foreign.job_id / ".job.lock"
    try:
        assert service.list_jobs()["items"] == []
        assert service.reconcile_jobs() == []
        assert service.store.get_job(foreign.job_id).status == "queued"
        assert not foreign_lock.exists()
        for operation in (
                lambda: service.get_job(foreign.job_id),
                lambda: service.cancel_job(
                    foreign.job_id,
                    attempt_number=foreign.attempt_number,
                    revision=foreign.revision),
                lambda: service.resume_job(
                    foreign.job_id,
                    attempt_number=foreign.attempt_number,
                    revision=foreign.revision),
                lambda: service.deletion_plan(foreign.job_id),
                lambda: service.delete_job(
                    foreign.job_id,
                    attempt_number=foreign.attempt_number,
                    revision=foreign.revision),
        ):
            with pytest.raises(service_runtime.ServiceRuntimeError) as raised:
                operation()
            assert raised.value.code == "not_found"
        assert service.store.get_job(foreign.job_id).status == "queued"
        assert not foreign_lock.exists()
        assert service.healthy
    finally:
        service.close()

    restarted = _service(tmp_path)
    try:
        assert restarted.store.get_job(foreign.job_id).status == "queued"
        assert restarted.list_jobs()["items"] == []
        assert not foreign_lock.exists()
    finally:
        restarted.close()


def test_owner_marker_tamper_fails_fatal_and_marks_running_service_unhealthy(
        tmp_path):
    service = _service(tmp_path)
    storage_policy.atomic_write_private_json(
        service._job_root_owner_path,
        {"schema_version": 1, "kind": "tampered"},
    )
    try:
        with pytest.raises(service_runtime.ServiceRuntimeError) as raised:
            service.list_jobs()
        assert raised.value.code == "service_unavailable"
        assert raised.value.fatal is True
        assert not service.healthy
    finally:
        service.close()


def test_public_corpora_and_readiness_never_expose_paths(tmp_path):
    service = _service(tmp_path)
    try:
        rendered = json.dumps(service.public_corpora())
        assert "property" in rendered
        assert str(tmp_path) not in rendered
        service.corpora["property"].chunks_path.unlink()
        assert service.readiness() is False
    finally:
        service.close()


def test_retryable_reconcile_contention_does_not_poison_health(
        monkeypatch, tmp_path):
    service = _service(tmp_path)
    monkeypatch.setattr(
        service.store, "list_jobs",
        lambda: (_ for _ in ()).throw(
            job_runtime.JobBusyError("busy")))
    try:
        with pytest.raises(service_runtime.ServiceRuntimeError) as raised:
            service.reconcile_jobs()
        assert raised.value.fatal is False
        assert service.healthy
    finally:
        service.close()


def test_retryable_job_listing_contention_does_not_poison_health(
        monkeypatch, tmp_path):
    service = _service(tmp_path)
    monkeypatch.setattr(
        service.store, "list_jobs",
        lambda: (_ for _ in ()).throw(job_runtime.JobBusyError("busy")))
    try:
        with pytest.raises(service_runtime.ServiceRuntimeError) as raised:
            service.list_jobs()
        assert raised.value.code == "service_unavailable"
        assert raised.value.fatal is False
        assert service.healthy
    finally:
        service.close()


def test_expected_job_contention_does_not_poison_health(tmp_path):
    service = _service(
        tmp_path,
        job_binding=_coordination_binding(
            reconcile_job=lambda *_args, **_kwargs: (
                _ for _ in ()).throw(job_runtime.JobBusyError("busy"))),
    )
    submitted = _submit_service_job(service)
    try:
        with pytest.raises(service_runtime.ServiceRuntimeError) as raised:
            service.cancel_job(
                submitted.job_id,
                attempt_number=submitted.attempt_number,
                revision=submitted.revision)
        assert raised.value.code == "service_unavailable"
        assert raised.value.fatal is False
        assert service.healthy
    finally:
        service.close()


@pytest.mark.parametrize(
    ("failure_type", "code", "fatal", "healthy"),
    [
        (job_runtime.JobNotFoundError, "not_found", False, True),
        (job_runtime.JobBusyError, "service_unavailable", False, True),
        (job_runtime.JobStateError, "service_unavailable", False, True),
        (job_runtime.JobValidationError,
         "service_unavailable", False, True),
        (job_runtime.JobCorruptError,
         "service_unavailable", True, False),
        (job_manager.JobManagerCorruptError,
         "service_unavailable", True, False),
    ],
)
def test_expected_job_maps_every_coordination_error_stably(
        tmp_path, failure_type, code, fatal, healthy):
    def fail(*_args, **_kwargs):
        raise failure_type("private coordination detail")

    service = _service(
        tmp_path,
        job_binding=_coordination_binding(reconcile_job=fail),
    )
    submitted = _submit_service_job(service)
    try:
        with pytest.raises(service_runtime.ServiceRuntimeError) as raised:
            service.cancel_job(
                submitted.job_id,
                attempt_number=submitted.attempt_number,
                revision=submitted.revision,
            )
        assert raised.value.code == code
        assert raised.value.fatal is fatal
        assert service.healthy is healthy
        assert "private coordination detail" not in str(raised.value)
    finally:
        service.close()


def test_search_is_bounded_by_nonblocking_concurrency(tmp_path):
    entered = threading.Event()
    release = threading.Event()

    def runner(config, request, request_id):
        assert config.corpus_id == "property"
        assert request.query == "minimum contacts"
        entered.set()
        assert release.wait(timeout=5)
        return _empty_search_response(config, request, request_id)

    service = _service(
        tmp_path, search_runner=runner, max_searches=1)
    request = service_contracts.SearchRequest("minimum contacts")
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(service.search, "property", request, "req-1")
            assert entered.wait(timeout=2)
            with pytest.raises(service_runtime.ServiceRuntimeError) as raised:
                service.search("property", request, "req-2")
            assert raised.value.code == "concurrency_limited"
            release.set()
            assert first.result(timeout=2)["request_id"] == "req-1"
    finally:
        release.set()
        service.close()


def test_supervised_search_keeps_query_out_of_arguments_and_cleans_temp(
        monkeypatch, tmp_path):
    config = _config(tmp_path)
    request = service_contracts.SearchRequest(
        "minimum contacts secret query", mode="hybrid")
    observed = {}
    response = retrieval_core.SearchResponse(
        hits=[retrieval_core.SearchHit(
            text="holding", metadata={
                "content_type": "case_opinion",
                "source_file": "C:/private/book.pdf",
            }, score=0.9, source_id="chunk_abc")],
        backend="qdrant",
        requested_mode="hybrid",
        effective_mode="vector",
        reranker_applied=False,
        warnings=["Hybrid search failed at C:/private; used vector search."],
    )
    def run(script_path, argv, *, operation, timeout, **kwargs):
        observed.update({
            "script": script_path,
            "argv": list(argv),
            "operation": operation,
            "timeout": timeout,
            "temporary": Path(argv[1]).parent,
            "stdout_target": kwargs["stdout_target"],
            "stderr_target": kwargs["stderr_target"],
        })
        request_path = Path(argv[1])
        if os.name != "nt":
            assert stat.S_IMODE(request_path.stat().st_mode) == 0o600
            assert stat.S_IMODE(request_path.parent.stat().st_mode) == 0o700
        return service_runtime.search_worker_main(
            request_path, Path(argv[2]),
            search_index_fn=lambda *a, **k: response)

    binding = _runtime_binding(supervisor=run)
    result = service_runtime.supervised_search(
        config, request, "req-1", runtime_binding=binding)

    assert result["hits"][0]["source_id"] == "chunk_abc"
    assert result["warnings"] == ["hybrid_failed"]
    assert "source_file" not in json.dumps(result)
    assert request.query not in " ".join(map(str, observed["argv"]))
    assert observed["argv"][0] == service_runtime._SEARCH_WORKER_ACTION
    assert observed["script"] == binding.worker_script_path
    assert observed["operation"] == "service search"
    assert observed["timeout"] == config.search_timeout_seconds
    assert observed["stdout_target"] is observed["stderr_target"]
    assert observed["stdout_target"].name == os.devnull
    assert not observed["temporary"].exists()


@pytest.mark.parametrize(
    ("mode", "hybrid"),
    [("auto", None), ("vector", False), ("hybrid", True)],
)
def test_search_backend_receives_the_exact_bound_qdrant_contract(
        tmp_path, mode, hybrid):
    config = _config(tmp_path)
    request = service_contracts.SearchRequest(
        "minimum contacts",
        mode=mode,
        limit=7,
        filters=service_contracts.SearchFilters(
            content_type="case_opinion", chapter_num=3),
    )
    policy = release_security.ReleaseSecurityPolicy.from_values(
        profile="development", network_policy="allow-cloud")
    observed = {}

    def search_index(*args, **kwargs):
        observed.update(args=args, kwargs=kwargs)
        return retrieval_core.SearchResponse(
            hits=[],
            backend="qdrant",
            requested_mode=mode,
            effective_mode="vector",
            reranker_applied=False,
            warnings=[],
        )

    result = service_runtime._execute_search(
        config,
        request,
        "req-search-binding",
        search_index_fn=search_index,
        security_policy=policy,
    )

    assert observed["args"] == (request.query, config.db_path)
    assert observed["kwargs"] == {
        "db_backend": "qdrant",
        "n_results": 7,
        "content_type": "case_opinion",
        "chapter_num": 3,
        "collection_name": config.collection_name,
        "embedding_model": config.embedding_model,
        "use_reranker": False,
        "hybrid": hybrid,
        "chunks_path": config.chunks_path,
        "lock_timeout": config.db_lock_timeout_seconds,
        "security_policy": policy,
    }
    assert observed["kwargs"]["security_policy"] is policy
    assert result["requested_mode"] == mode


def test_search_worker_returns_only_stable_error_without_exception_text(
        tmp_path):
    config = _config(tmp_path)
    request_path = tmp_path / "request.json"
    result_path = tmp_path / "result.json"
    storage_policy.atomic_write_private_json(
        request_path,
        service_runtime._search_request_payload(
            config, service_contracts.SearchRequest("secret query"), "req-1"))
    def fail_search(*_args, **_kwargs):
        raise RuntimeError("C:/private/book.pdf API_KEY=secret")

    assert service_runtime.search_worker_main(
        request_path, result_path, search_index_fn=fail_search) == 1
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    encoded = json.dumps(payload)
    assert payload["error_code"] == "service_unavailable"
    assert "private" not in encoded
    assert "secret" not in encoded
    assert "RuntimeError" not in encoded


def test_worker_replaces_oversized_envelope_with_bounded_stable_error(
        tmp_path):
    result_path = tmp_path / "result.json"

    service_runtime._write_worker_envelope(
        result_path,
        {"private": "x" * service_runtime.MAX_SEARCH_RESULT_BYTES},
    )

    assert result_path.stat().st_size < service_runtime.MAX_SEARCH_RESULT_BYTES
    assert json.loads(result_path.read_text(encoding="utf-8")) == {
        "schema_version": service_runtime._INTERNAL_SCHEMA_VERSION,
        "kind": "service_search_result",
        "ok": False,
        "error_code": "service_unavailable",
    }


@pytest.mark.parametrize(
    "arguments",
    [None, [], ["_search_worker", "request.json", "result.json"]],
)
def test_service_runtime_script_fails_closed_after_worker_relocation(
        arguments):
    assert service_runtime.main(arguments) == 2


def test_supervised_search_maps_deadline_and_malformed_envelopes(
        tmp_path):
    config = _config(tmp_path)
    request = service_contracts.SearchRequest("query")
    with pytest.raises(service_runtime.ServiceRuntimeError) as raised:
        service_runtime.supervised_search(
            config, request, "req-1",
            runtime_binding=_runtime_binding(
                supervisor=lambda *a, **k: 124))
    assert raised.value.code == "deadline_exceeded"

    def malformed(_script, argv, **_kwargs):
        storage_policy.atomic_write_private_json(
            Path(argv[2]), {"private": "C:/secret"})
        return 0

    with pytest.raises((service_runtime.ServiceRuntimeError,
                        service_contracts.ServiceContractError)):
        service_runtime.supervised_search(
            config, request, "req-1",
            runtime_binding=_runtime_binding(supervisor=malformed))


def test_supervised_search_maps_actual_cleanup_failure_to_fatal_and_cleans(
        tmp_path):
    config = _config(tmp_path)
    request = service_contracts.SearchRequest("private query")
    binding = _runtime_binding()
    temporary_root = tmp_path / "search-tmp"

    def fail_cleanup(*_args, **_kwargs):
        raise binding.cleanup_error_type("verified cleanup failed")

    with pytest.raises(service_runtime.ServiceRuntimeError) as raised:
        service_runtime.supervised_search(
            config,
            request,
            "req-cleanup",
            temporary_root=temporary_root,
            runtime_binding=replace(binding, supervisor=fail_cleanup),
        )

    assert raised.value.code == "service_unavailable"
    assert raised.value.fatal is True
    assert list(temporary_root.iterdir()) == []


def test_supervised_search_rejects_valid_envelope_from_nonzero_worker(
        tmp_path):
    config = _config(tmp_path)
    request = service_contracts.SearchRequest("query")

    def nonzero(_script, argv, **_kwargs):
        storage_policy.atomic_write_private_json(
            Path(argv[2]), {
                "schema_version": service_runtime._INTERNAL_SCHEMA_VERSION,
                "kind": "service_search_result",
                "ok": True,
                "result": _empty_search_response(
                    config, request, "req-nonzero"),
            })
        return 1

    with pytest.raises(service_runtime.ServiceRuntimeError) as raised:
        service_runtime.supervised_search(
            config,
            request,
            "req-nonzero",
            runtime_binding=_runtime_binding(supervisor=nonzero),
        )

    assert raised.value.code == "service_unavailable"
    assert raised.value.fatal is False


def test_supervised_search_surfaces_and_marks_cleanup_failure(
        monkeypatch, tmp_path):
    config = _config(tmp_path)
    request = service_contracts.SearchRequest("private query")
    observed = {}

    def run(_script, argv, **_kwargs):
        result = _empty_search_response(config, request, "req-1")
        storage_policy.atomic_write_private_json(
            Path(argv[2]), {
                "schema_version": service_runtime._INTERNAL_SCHEMA_VERSION,
                "kind": "service_search_result",
                "ok": True,
                "result": result,
            })
        return 0

    real_remove = service_runtime._remove_search_directory

    def fail_remove(path, identity):
        observed.update(path=path, identity=identity)
        raise OSError("injected cleanup failure")

    monkeypatch.setattr(
        service_runtime, "_remove_search_directory", fail_remove)

    with pytest.raises(service_runtime.ServiceRuntimeError) as raised:
        service_runtime.supervised_search(
            config, request, "req-1",
            temporary_root=tmp_path / "search-tmp",
            runtime_binding=_runtime_binding(supervisor=run))

    assert raised.value.code == "service_unavailable"
    assert raised.value.fatal is True
    assert (observed["path"] / "request.json").is_file()
    real_remove(observed["path"], observed["identity"])


def test_start_removes_crash_left_private_search_directory(tmp_path):
    config = _config(tmp_path)
    service = service_runtime.RagApplicationService(
        {config.corpus_id: config},
        job_root=tmp_path / "jobs",
        working_directory=tmp_path,
        output_root=tmp_path / "output",
        service_state_root=tmp_path / "service-state",
        launcher=_launch_starting,
    )
    stale = storage_policy.ensure_private_directory(
        service.search_temporary_root / "request-crashed")
    storage_policy.atomic_write_private_text(
        stale / "request.json", "private query")

    service.start()
    try:
        assert not stale.exists()
        assert list(service.search_temporary_root.iterdir()) == []
    finally:
        service.close()


def test_reindex_is_structured_idempotent_and_detects_key_reuse(tmp_path):
    launches = []

    def launch(store, job_id, *, ready_timeout):
        launches.append((job_id, ready_timeout))
        return _launch_starting(store, job_id, ready_timeout=ready_timeout)

    service = _service(tmp_path, launcher=launch)
    try:
        first = service.reindex(
            "property", service_contracts.ReindexRequest(False),
            idempotency_key="request-1234")
        replay = service.reindex(
            "property", service_contracts.ReindexRequest(False),
            idempotency_key="request-1234")

        assert first.idempotent_replay is False
        assert replay.idempotent_replay is True
        assert replay.job["job_id"] == first.job["job_id"]
        assert len(launches) == 1
        execution = service.store.load_execution(first.job["job_id"])
        assert execution.command == "index"
        assert "--full-reindex" not in execution.argv
        assert execution.working_directory == tmp_path.resolve()
        assert execution.output_root == (tmp_path / "output").resolve()

        with pytest.raises(service_runtime.ServiceRuntimeError) as raised:
            service.reindex(
                "property", service_contracts.ReindexRequest(True),
                idempotency_key="request-1234")
        assert raised.value.code == "conflict"
        assert len(launches) == 1
    finally:
        service.close()


def test_queued_reindex_replay_uses_explicit_fail_queued_coordination(
        tmp_path):
    calls = []

    def reconcile(store, job_id, **kwargs):
        calls.append((store, job_id, kwargs.copy()))
        return store.get_job(job_id)

    service = _service(
        tmp_path,
        job_binding=_coordination_binding(reconcile_job=reconcile),
    )
    try:
        config = service.corpora["property"]
        request = service_contracts.ReindexRequest()
        job_id = service_contracts.job_id_for_idempotency(
            "property", "exact-replay-1234")
        service.store.submit_job(
            "index", service._reindex_argv(config, request),
            timeout_seconds=config.reindex_timeout_seconds,
            job_id=job_id,
            working_directory=service.working_directory,
            output_root=service.output_root,
        )

        result = service.reindex(
            "property", request,
            idempotency_key="exact-replay-1234")
    finally:
        service.close()

    assert result.idempotent_replay is True
    assert calls == [
        (service.store, job_id, {"fail_queued": True}),
    ]


def test_launch_failure_terminalizes_job_and_returns_stable_error(tmp_path):
    def fail_launch(*_args, **_kwargs):
        raise RuntimeError("private launch detail")

    service = _service(tmp_path, launcher=fail_launch)
    try:
        with pytest.raises(service_runtime.ServiceRuntimeError) as raised:
            service.reindex(
                "property", service_contracts.ReindexRequest(),
                idempotency_key="launch-failure-1234")

        assert raised.value.code == "service_unavailable"
        assert raised.value.fatal is False
        assert raised.value.job_id is not None
        assert service.store.get_job(raised.value.job_id).status == "failed"
        assert service.healthy
        assert "private launch detail" not in str(raised.value)
    finally:
        service.close()


def test_launch_failure_terminalization_failure_is_fatal_and_unhealthy(
        monkeypatch, tmp_path):
    def fail_launch(*_args, **_kwargs):
        raise RuntimeError("private launch detail")

    service = _service(tmp_path, launcher=fail_launch)
    monkeypatch.setattr(
        service.store,
        "transition_job",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            job_runtime.JobBusyError("private terminalization detail")),
    )
    try:
        with pytest.raises(service_runtime.ServiceRuntimeError) as raised:
            service.reindex(
                "property", service_contracts.ReindexRequest(),
                idempotency_key="terminalization-failure-1234")

        assert raised.value.code == "service_unavailable"
        assert raised.value.fatal is True
        assert raised.value.job_id is not None
        assert not service.healthy
        assert service.store.get_job(raised.value.job_id).status == "queued"
    finally:
        service.close()


def test_process_control_exception_is_not_swallowed_as_launch_failure(
        tmp_path):
    def interrupt(*_args, **_kwargs):
        raise KeyboardInterrupt()

    service = _service(tmp_path, launcher=interrupt)
    try:
        with pytest.raises(KeyboardInterrupt):
            service.reindex(
                "property", service_contracts.ReindexRequest(),
                idempotency_key="interrupt-launch-1234")
        jobs = service.store.list_jobs()
        assert len(jobs) == 1
        assert jobs[0].status == "queued"
        assert service.healthy
        assert service._launching_job_ids == set()
    finally:
        service.close()


def test_reindex_replay_maps_manager_corruption_to_fatal_unavailability(
        tmp_path):
    service = _service(
        tmp_path,
        job_binding=_coordination_binding(
            reconcile_job=lambda *_args, **_kwargs: (
                _ for _ in ()).throw(
                    job_manager.JobManagerCorruptError("corrupt"))),
    )
    config = service.corpora["property"]
    request = service_contracts.ReindexRequest()
    job_id = service_contracts.job_id_for_idempotency(
        "property", "corrupt-replay-1234")
    service.store.submit_job(
        "index", service._reindex_argv(config, request),
        timeout_seconds=config.reindex_timeout_seconds,
        job_id=job_id,
        working_directory=service.working_directory,
        output_root=service.output_root,
    )
    try:
        with pytest.raises(service_runtime.ServiceRuntimeError) as raised:
            service.reindex(
                "property", request,
                idempotency_key="corrupt-replay-1234")
        assert raised.value.code == "service_unavailable"
        assert raised.value.fatal is True
        assert not service.healthy
    finally:
        service.close()


def test_reindex_replay_contention_is_retryable_without_poisoning_health(
        tmp_path):
    service = _service(
        tmp_path,
        job_binding=_coordination_binding(
            reconcile_job=lambda *_args, **_kwargs: (
                _ for _ in ()).throw(job_runtime.JobBusyError("busy"))),
    )
    config = service.corpora["property"]
    request = service_contracts.ReindexRequest()
    job_id = service_contracts.job_id_for_idempotency(
        "property", "busy-replay-1234")
    service.store.submit_job(
        "index", service._reindex_argv(config, request),
        timeout_seconds=config.reindex_timeout_seconds,
        job_id=job_id,
        working_directory=service.working_directory,
        output_root=service.output_root,
    )
    try:
        with pytest.raises(service_runtime.ServiceRuntimeError) as raised:
            service.reindex(
                "property", request,
                idempotency_key="busy-replay-1234")
        assert raised.value.code == "service_unavailable"
        assert raised.value.fatal is False
        assert service.healthy
    finally:
        service.close()


@pytest.mark.parametrize(
    ("failure_type", "code", "fatal", "healthy"),
    [
        (job_runtime.JobNotFoundError, "not_found", False, True),
        (job_runtime.JobStateError, "conflict", False, True),
        (job_runtime.JobValidationError,
         "service_unavailable", False, True),
        (job_runtime.JobCorruptError,
         "service_unavailable", True, False),
    ],
)
def test_reindex_replay_maps_remaining_coordination_errors(
        tmp_path, failure_type, code, fatal, healthy):
    def fail(*_args, **_kwargs):
        raise failure_type("private replay detail")

    service = _service(
        tmp_path,
        job_binding=_coordination_binding(reconcile_job=fail),
    )
    config = service.corpora["property"]
    request = service_contracts.ReindexRequest()
    job_id = service_contracts.job_id_for_idempotency(
        "property", "mapped-replay-1234")
    service.store.submit_job(
        "index", service._reindex_argv(config, request),
        timeout_seconds=config.reindex_timeout_seconds,
        job_id=job_id,
        working_directory=service.working_directory,
        output_root=service.output_root,
    )
    try:
        with pytest.raises(service_runtime.ServiceRuntimeError) as raised:
            service.reindex(
                "property", request,
                idempotency_key="mapped-replay-1234")
        assert raised.value.code == code
        assert raised.value.fatal is fatal
        assert service.healthy is healthy
        assert "private replay detail" not in str(raised.value)
    finally:
        service.close()


def test_restart_terminalizes_reindex_crash_gap_for_explicit_resume(tmp_path):
    first = _service(tmp_path)
    config = first.corpora["property"]
    job_id = service_contracts.job_id_for_idempotency(
        "property", "restart-gap-1234")
    submitted = first.store.submit_job(
        "index",
        first._reindex_argv(config, service_contracts.ReindexRequest()),
        timeout_seconds=config.reindex_timeout_seconds,
        job_id=job_id,
        working_directory=first.working_directory,
        output_root=first.output_root,
    )
    assert submitted.status == "queued"
    first.close()

    second = _service(tmp_path)
    try:
        recovered = second.store.get_job(job_id)
        assert recovered.status == "failed"
        assert recovered.attempt_number == 1
        assert recovered.status in job_runtime.RESUMABLE_JOB_STATUSES
    finally:
        second.close()


def test_restart_terminalizes_resume_crash_gap_without_auto_launch(tmp_path):
    first = _service(tmp_path)
    submitted = _submit_service_job(first)
    failed = _advance_failed(first.store, submitted.job_id)
    resumed = first.store.prepare_resume(
        submitted.job_id, expected_revision=failed.revision)
    assert resumed.status == "queued"
    assert resumed.attempt_number == 2
    first.close()

    second = _service(tmp_path)
    try:
        recovered = second.store.get_job(submitted.job_id)
        assert recovered.status == "failed"
        assert recovered.attempt_number == 2
        assert recovered.status in job_runtime.RESUMABLE_JOB_STATUSES
    finally:
        second.close()


def test_concurrent_idempotent_reindex_launches_exactly_once(tmp_path):
    launch_entered = threading.Event()
    release_launch = threading.Event()
    launches = []

    def launch(store, job_id, *, ready_timeout):
        launches.append(job_id)
        launch_entered.set()
        assert release_launch.wait(timeout=5)
        return _launch_starting(store, job_id, ready_timeout=ready_timeout)

    service = _service(tmp_path, launcher=launch)
    request = service_contracts.ReindexRequest()
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(
                service.reindex, "property", request,
                idempotency_key="concurrent-1234")
            assert launch_entered.wait(timeout=2)
            second = pool.submit(
                service.reindex, "property", request,
                idempotency_key="concurrent-1234")
            replay = second.result(timeout=2)
            release_launch.set()
            created = first.result(timeout=2)
        assert created.idempotent_replay is False
        assert replay.idempotent_replay is True
        assert created.job["job_id"] == replay.job["job_id"]
        assert len(launches) == 1
    finally:
        release_launch.set()
        service.close()


def test_job_listing_is_redacted_cursor_paginated_and_read_only(tmp_path):
    service = _service(
        tmp_path,
        job_binding=_coordination_binding(
            reconcile_job=lambda *_args, **_kwargs: pytest.fail(
                "GET listing must not reconcile")),
    )
    try:
        ids = []
        for _index in range(3):
            summary = _submit_service_job(service)
            ids.append(summary.job_id)
        first = service.list_jobs(limit=2)
        assert len(first["items"]) == 2
        assert first["next_cursor"]
        cursor = service_contracts.decode_job_cursor(first["next_cursor"])
        second = service.list_jobs(limit=2, cursor=cursor)
        assert len(second["items"]) == 1
        rendered = json.dumps([first, second])
        assert "C:/private" not in rendered
        assert "argv" not in rendered
        assert {item["job_id"] for item in first["items"] + second["items"]} == set(ids)
    finally:
        service.close()


def test_stale_cancel_cannot_target_resumed_attempt(tmp_path):
    service = _service(tmp_path)
    try:
        submitted = _submit_service_job(service)
        first_failed = _advance_failed(service.store, submitted.job_id)
        resumed = service.store.prepare_resume(
            submitted.job_id, expected_revision=first_failed.revision)

        with pytest.raises(service_runtime.ServiceRuntimeError) as raised:
            service.cancel_job(
                submitted.job_id,
                attempt_number=first_failed.attempt_number,
                revision=first_failed.revision)
        assert raised.value.code == "precondition_failed"
        second = service.store.load_execution(submitted.job_id)
        assert second.attempt_number == resumed.attempt_number
        assert not service.store.is_cancel_requested(
            submitted.job_id, second.attempt_token)

        accepted = service.cancel_job(
            submitted.job_id,
            attempt_number=resumed.attempt_number,
            revision=resumed.revision)
        assert accepted.job["attempt_number"] == resumed.attempt_number
        assert service.store.is_cancel_requested(
            submitted.job_id, second.attempt_token)
    finally:
        service.close()


def test_resume_has_one_exact_winner_and_launches_new_attempt(tmp_path):
    launches = []

    def launch(store, job_id, *, ready_timeout):
        launches.append(job_id)
        return _launch_starting(store, job_id, ready_timeout=ready_timeout)

    service = _service(tmp_path, launcher=launch)
    try:
        submitted = _submit_service_job(service)
        failed = _advance_failed(service.store, submitted.job_id)
        resumed = service.resume_job(
            submitted.job_id,
            attempt_number=failed.attempt_number,
            revision=failed.revision)
        assert resumed.job["attempt_number"] == 2
        assert resumed.job["status"] == "starting"
        assert launches == [submitted.job_id]

        with pytest.raises(service_runtime.ServiceRuntimeError) as raised:
            service.resume_job(
                submitted.job_id,
                attempt_number=failed.attempt_number,
                revision=failed.revision)
        assert raised.value.code == "precondition_failed"
        assert launches == [submitted.job_id]
    finally:
        service.close()


def test_deletion_plan_is_redacted_and_delete_requires_exact_etag(tmp_path):
    service = _service(tmp_path)
    try:
        submitted = _submit_service_job(service)
        failed = _advance_failed(service.store, submitted.job_id)
        plan = service.deletion_plan(submitted.job_id)
        rendered = json.dumps(plan)
        assert plan["apply_required"] is True
        assert plan["candidate_count"] == 1
        assert "private" not in rendered
        assert str(tmp_path) not in rendered
        assert "fingerprint" not in rendered

        with pytest.raises(service_runtime.ServiceRuntimeError) as raised:
            service.delete_job(
                submitted.job_id,
                attempt_number=failed.attempt_number,
                revision=failed.revision + 1)
        assert raised.value.code == "precondition_failed"
        assert service.store.get_job(submitted.job_id).status == "failed"

        outcome = service.delete_job(
            submitted.job_id,
            attempt_number=failed.attempt_number,
            revision=failed.revision)
        assert outcome["deleted"] is True
        assert outcome["job_id"] == submitted.job_id
        with pytest.raises(job_runtime.JobNotFoundError):
            service.store.get_job(submitted.job_id)
    finally:
        service.close()


def test_concurrent_duplicate_delete_has_one_winner_and_stays_healthy(
        tmp_path):
    service = _service(tmp_path)
    submitted = _submit_service_job(service)
    failed = _advance_failed(service.store, submitted.job_id)
    barrier = threading.Barrier(2)

    def remove():
        barrier.wait(timeout=5)
        try:
            outcome = service.delete_job(
                submitted.job_id,
                attempt_number=failed.attempt_number,
                revision=failed.revision)
            return "deleted", outcome["deleted"]
        except service_runtime.ServiceRuntimeError as exc:
            return "error", exc.code

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [future.result(timeout=5) for future in (
                pool.submit(remove), pool.submit(remove))]
        assert results.count(("deleted", True)) == 1
        assert results.count(("error", "not_found")) == 1
        assert service.healthy
    finally:
        service.close()


def test_active_job_deletion_plan_is_conflict_without_poisoning_health(
        tmp_path):
    service = _service(tmp_path)
    submitted = _submit_service_job(service)
    try:
        with pytest.raises(service_runtime.ServiceRuntimeError) as raised:
            service.deletion_plan(submitted.job_id)
        assert raised.value.code == "conflict"
        assert raised.value.fatal is False
        assert service.healthy
    finally:
        service.close()


@pytest.mark.parametrize(
    ("failure_type", "code", "fatal", "healthy"),
    [
        (job_runtime.JobNotFoundError, "not_found", False, True),
        (job_runtime.JobStateError, "conflict", False, True),
        (job_runtime.JobBusyError, "service_unavailable", False, True),
        (job_runtime.JobValidationError,
         "service_unavailable", False, True),
        (job_runtime.JobCorruptError,
         "service_unavailable", True, False),
        (job_manager.JobManagerCorruptError,
         "service_unavailable", True, False),
    ],
)
def test_deletion_plan_maps_every_coordination_error_stably(
        tmp_path, failure_type, code, fatal, healthy):
    def fail(*_args, **_kwargs):
        raise failure_type("private deletion detail")

    service = _service(
        tmp_path,
        job_binding=_coordination_binding(reconcile_job=fail),
    )
    submitted = _submit_service_job(service)
    _advance_failed(service.store, submitted.job_id)
    try:
        with pytest.raises(service_runtime.ServiceRuntimeError) as raised:
            service.deletion_plan(submitted.job_id)
        assert raised.value.code == code
        assert raised.value.fatal is fatal
        assert service.healthy is healthy
        assert "private deletion detail" not in str(raised.value)
    finally:
        service.close()


@pytest.mark.parametrize(("cause", "code", "fatal", "healthy"), [
    (job_runtime.JobBusyError("busy"),
     "service_unavailable", False, True),
    (job_runtime.JobNotFoundError("vanished"), "not_found", False, True),
    (job_runtime.JobStateError("changed"), "conflict", False, True),
    (job_runtime.JobCorruptError("corrupt"),
     "service_unavailable", True, False),
])
def test_deletion_plan_classifies_wrapped_job_failures(
        monkeypatch, tmp_path, cause, code, fatal, healthy):
    service = _service(tmp_path)
    submitted = _submit_service_job(service)
    _advance_failed(service.store, submitted.job_id)

    def fail_plan(*_args, **_kwargs):
        raise retention.RetentionError("wrapped job failure") from cause

    monkeypatch.setattr(
        service_runtime.retention, "plan_background_job_deletion", fail_plan)
    try:
        with pytest.raises(service_runtime.ServiceRuntimeError) as raised:
            service.deletion_plan(submitted.job_id)
        assert raised.value.code == code
        assert raised.value.fatal is fatal
        assert service.healthy is healthy
    finally:
        service.close()


def test_delete_apply_contention_is_retryable_without_poisoning_health(
        monkeypatch, tmp_path):
    service = _service(tmp_path)
    submitted = _submit_service_job(service)
    failed = _advance_failed(service.store, submitted.job_id)

    def fail_apply(_plan):
        raise retention.RetentionError("wrapped job failure") from (
            job_runtime.JobBusyError("busy"))

    monkeypatch.setattr(
        service_runtime.retention, "apply_retention_plan", fail_apply)
    try:
        with pytest.raises(service_runtime.ServiceRuntimeError) as raised:
            service.delete_job(
                submitted.job_id,
                attempt_number=failed.attempt_number,
                revision=failed.revision)
        assert raised.value.code == "service_unavailable"
        assert raised.value.fatal is False
        assert service.healthy
    finally:
        service.close()


def test_retryable_search_failure_does_not_poison_service_readiness(
        tmp_path):
    attempts = []

    def fail(_config, _request, _request_id):
        attempts.append(True)
        raise service_runtime.ServiceRuntimeError("service_unavailable")

    service = _service(tmp_path, search_runner=fail)
    request = service_contracts.SearchRequest("query")
    try:
        with pytest.raises(service_runtime.ServiceRuntimeError):
            service.search("property", request, "req-1")
        assert service.healthy
        with pytest.raises(service_runtime.ServiceRuntimeError):
            service.search("property", request, "req-2")
        assert attempts == [True, True]
    finally:
        service.close()


def test_fatal_search_failure_marks_service_unready_and_refuses_more(tmp_path):
    attempts = []

    def fail(_config, _request, _request_id):
        attempts.append(True)
        raise service_runtime.ServiceRuntimeError(
            "service_unavailable", fatal=True)

    service = _service(tmp_path, search_runner=fail)
    request = service_contracts.SearchRequest("query")
    try:
        with pytest.raises(service_runtime.ServiceRuntimeError):
            service.search("property", request, "req-1")
        assert not service.healthy
        with pytest.raises(service_runtime.ServiceRuntimeError):
            service.search("property", request, "req-2")
        assert attempts == [True]
    finally:
        service.close()


def test_injected_search_runner_cannot_bypass_public_response_allowlist(
        tmp_path):
    def leak(config, request, request_id):
        result = _empty_search_response(config, request, request_id)
        result["db_path"] = "C:/private/qdrant"
        return result

    service = _service(tmp_path, search_runner=leak)
    try:
        with pytest.raises(service_runtime.ServiceRuntimeError) as raised:
            service.search(
                "property", service_contracts.SearchRequest("query"),
                "req-1")
        assert raised.value.fatal is True
        assert not service.healthy
    finally:
        service.close()


def test_service_search_uses_real_local_qdrant_contract(
        monkeypatch, tmp_path):
    pytest.importorskip("qdrant_client")
    import rag

    chunks_path = tmp_path / "real-chunks.jsonl"
    chunks_path.write_text(json.dumps({
        "text": "Minimum contacts support personal jurisdiction.",
        "metadata": {
            "chunk_index": 0,
            "content_type": "case_opinion",
            "chapter_num": 2,
        },
    }) + "\n", encoding="utf-8")
    db_path = tmp_path / "real-qdrant"
    monkeypatch.setattr(
        rag, "_embed_texts",
        lambda texts, *_args, **_kwargs: [
            [1.0, 0.5, 0.25, 0.125] for _text in texts])
    monkeypatch.setattr(
        rag, "_validate_embedding_token_counts",
        lambda *_args, **_kwargs: None)
    rag.index_chunks_qdrant(
        chunks_path,
        db_path,
        collection_name="service_parity",
        embedding_model="service-test-model",
    )
    config = service_contracts.CorpusConfig(
        corpus_id="property",
        db_path=db_path.resolve(),
        chunks_path=chunks_path.resolve(),
        collection_name="service_parity",
        embedding_model="service-test-model",
    )

    result = service_runtime._execute_search(
        config,
        service_contracts.SearchRequest(
            "minimum contacts", mode="vector",
            filters=service_contracts.SearchFilters(chapter_num=2)),
        "req-real-qdrant",
        search_index_fn=rag.search_index,
    )

    assert result["backend"] == "qdrant"
    assert result["effective_mode"] == "vector"
    assert len(result["hits"]) == 1
    assert result["hits"][0]["text"].startswith("Minimum contacts")
    assert result["hits"][0]["metadata"]["chapter_num"] == 2
