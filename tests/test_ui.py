from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import json

import pytest

import job_application
import job_manager
import job_runtime
import release_security
import ui


def test_search_uses_backend_bound_to_config(monkeypatch, tmp_path):
    observed = {}
    monkeypatch.setitem(ui._config, "db_path", tmp_path)
    monkeypatch.setitem(ui._config, "chunks_path", tmp_path / "chunks.jsonl")
    monkeypatch.setitem(ui._config, "db_backend", "chroma")
    monkeypatch.setitem(ui._config, "collection", "book")

    def fake_request(action, payload, **kwargs):
        observed.update(payload["config"])
        return {
            "hits": [], "effective_mode": "vector",
            "reranker_applied": False, "warnings": [],
        }

    monkeypatch.setattr(ui, "_supervised_vector_request", fake_request)

    ui.do_search("query", "All", "All", 5, False, False, "qdrant")

    assert observed["db_backend"] == "chroma"


def test_search_ui_maps_auto_modes_to_tristate(monkeypatch, tmp_path):
    observed = {}
    monkeypatch.setitem(ui._config, "db_path", tmp_path)
    monkeypatch.setitem(ui._config, "chunks_path", tmp_path / "chunks.jsonl")
    monkeypatch.setitem(ui._config, "db_backend", "chroma")
    monkeypatch.setitem(ui._config, "collection", "book")

    def fake_request(action, payload, **kwargs):
        observed.update(payload["options"])
        return {
            "hits": [], "effective_mode": "hybrid",
            "reranker_applied": False, "warnings": [],
        }

    monkeypatch.setattr(ui, "_supervised_vector_request", fake_request)

    ui.do_search(
        "query", "All", "All", 5,
        "Auto (recommended)", "Auto (recommended)")

    assert observed["hybrid"] is None
    assert observed["use_reranker"] is None


def test_info_uses_supervised_vector_worker(monkeypatch, tmp_path):
    observed = {}
    chunks = tmp_path / "chunks.jsonl"
    chunks.write_text("", encoding="utf-8")
    monkeypatch.setitem(ui._config, "db_path", tmp_path / "db")
    monkeypatch.setitem(ui._config, "chunks_path", chunks)
    monkeypatch.setitem(ui._config, "db_backend", "qdrant")
    monkeypatch.setitem(ui._config, "collection", "book")

    def fake_request(action, payload, **kwargs):
        observed.update(action=action, payload=payload, kwargs=kwargs)
        return {"count": 7}

    monkeypatch.setattr(ui, "_supervised_vector_request", fake_request)

    output = ui.do_info()

    assert observed["action"] == "info"
    assert observed["payload"]["config"]["db_backend"] == "qdrant"
    assert "- Points: 7" in output


def test_vector_worker_serializes_search_response(monkeypatch, tmp_path):
    monkeypatch.setattr(
        ui.rag, "search_index",
        lambda *args, **kwargs: SimpleNamespace(
            hits=[SimpleNamespace(
                text="answer", metadata={"content_type": "case_opinion"},
                score=0.75)],
            effective_mode="hybrid", reranker_applied=True,
            warnings=["fallback"],
        ),
    )
    request = {
        "action": "search",
        "query": "terms",
        "config": {
            "db_path": str(tmp_path / "db"),
            "chunks_path": str(tmp_path / "chunks.jsonl"),
            "db_backend": "chroma",
            "collection": "book",
            "embedding_model": "embedding",
            "db_lock_timeout": 1,
            "release_security": (
                release_security.ReleaseSecurityPolicy(
                    trusted_single_user_ui=True).provenance()),
        },
        "options": {
            "n_results": 5, "content_type": None, "chapter_num": None,
            "use_reranker": None, "hybrid": None,
        },
    }

    result = ui._execute_vector_request(request)

    assert result["hits"] == [{
        "text": "answer", "metadata": {"content_type": "case_opinion"},
        "score": 0.75,
    }]
    assert result["reranker_applied"] is True


def test_vector_worker_reconstructs_release_security_policy(
        monkeypatch, tmp_path):
    observed = {}
    policy = release_security.ReleaseSecurityPolicy.from_values(
        profile="development", network_policy="allow-cloud",
        cache_namespace="tenant-a", trust_environment_network=True,
        trusted_single_user_ui=True,
    )

    def fake_search(*args, **kwargs):
        observed["policy"] = kwargs["security_policy"]
        return SimpleNamespace(
            hits=[], context_window=0, effective_mode="vector",
            reranker_applied=False, warnings=[])

    monkeypatch.setattr(ui.rag, "search_index", fake_search)
    request = {
        "action": "search", "query": "terms",
        "config": {
            "db_path": str(tmp_path / "db"),
            "chunks_path": str(tmp_path / "chunks.jsonl"),
            "db_backend": "chroma", "collection": "book",
            "embedding_model": "embedding", "db_lock_timeout": 1,
            "release_security": policy.provenance(),
        },
        "options": {
            "n_results": 5, "content_type": None, "chapter_num": None,
            "use_reranker": False, "hybrid": False,
        },
    }

    ui._execute_vector_request(request)

    assert observed["policy"] == policy


@pytest.mark.parametrize("receipt", [
    None,
    release_security.ReleaseSecurityPolicy().provenance(),
])
def test_vector_worker_rejects_missing_or_disabled_ui_policy_before_search(
        monkeypatch, tmp_path, receipt):
    monkeypatch.setattr(
        ui.rag, "search_index",
        lambda *_args, **_kwargs: pytest.fail(
            "untrusted worker receipt must fail before search"))
    config = {
        "db_path": str(tmp_path / "db"),
        "chunks_path": str(tmp_path / "chunks.jsonl"),
        "db_backend": "chroma", "collection": "book",
        "embedding_model": "embedding", "db_lock_timeout": 1,
    }
    if receipt is not None:
        config["release_security"] = receipt
    request = {
        "action": "search", "query": "terms", "config": config,
        "options": {
            "n_results": 5, "content_type": None, "chapter_num": None,
            "use_reranker": False, "hybrid": False,
        },
    }

    with pytest.raises(
            (KeyError, release_security.ReleaseSecurityError)):
        ui._execute_vector_request(request)


def test_vector_worker_serializes_context_and_alias_provenance(
        monkeypatch, tmp_path):
    alias = ui.rag.ContextSourceAlias(
        source_id="chunk_alias", metadata={"page_range": "9"})
    segment = ui.rag.ContextSegment(
        text="neighbor evidence", metadata={"page_range": "8"},
        source_id="chunk_neighbor", relation="next", distance=1,
        source_aliases=(alias,))
    hit = SimpleNamespace(
        text="primary evidence", metadata={"content_type": "case_opinion"},
        score=0.75, source_id="chunk_primary",
        source_aliases=[alias], context_segments=[segment])
    monkeypatch.setattr(
        ui.rag, "search_index",
        lambda *args, **kwargs: SimpleNamespace(
            hits=[hit], context_window=1, effective_mode="vector",
            reranker_applied=False, warnings=[]),
    )
    request = {
        "action": "search",
        "query": "terms",
        "config": {
            "db_path": str(tmp_path / "db"),
            "chunks_path": str(tmp_path / "chunks.jsonl"),
            "db_backend": "chroma",
            "collection": "book",
            "embedding_model": "embedding",
            "db_lock_timeout": 1,
            "release_security": (
                release_security.ReleaseSecurityPolicy(
                    trusted_single_user_ui=True).provenance()),
        },
        "options": {
            "n_results": 5, "content_type": None, "chapter_num": None,
            "use_reranker": None, "hybrid": None, "context_window": 1,
        },
    }

    result = ui._execute_vector_request(request)

    assert result["hits"][0]["source_id"] == "chunk_primary"
    assert result["hits"][0]["equivalent_sources"] == [{
        "source_id": "chunk_alias", "metadata": {"page_range": "9"},
    }]
    assert result["hits"][0]["context"][0] == {
        "text": "neighbor evidence",
        "metadata": {"page_range": "8"},
        "source_id": "chunk_neighbor",
        "relation": "next",
        "distance": 1,
        "equivalent_sources": [{
            "source_id": "chunk_alias", "metadata": {"page_range": "9"},
        }],
    }


def test_search_ui_renders_context_and_alias_provenance(monkeypatch, tmp_path):
    monkeypatch.setitem(ui._config, "db_path", tmp_path / "db")
    monkeypatch.setitem(ui._config, "chunks_path", tmp_path / "chunks.jsonl")
    monkeypatch.setitem(ui._config, "db_backend", "chroma")
    monkeypatch.setitem(ui._config, "collection", "book")
    monkeypatch.setattr(
        ui, "_supervised_vector_request",
        lambda *args, **kwargs: {
            "hits": [{
                "text": "primary evidence",
                "metadata": {
                    "content_type": "case_opinion", "page_range": "7"},
                "score": 0.75,
                "equivalent_sources": [{
                    "source_id": "chunk_primary_alias", "metadata": {}},
                ],
                "context": [{
                    "text": "neighbor evidence", "metadata": {},
                    "source_id": "chunk_neighbor", "relation": "next",
                    "distance": 1,
                    "equivalent_sources": [{
                        "source_id": "chunk_neighbor_alias", "metadata": {}},
                    ],
                }],
            }],
            "effective_mode": "vector",
            "reranker_applied": False,
            "warnings": [],
        },
    )

    rendered = ui.do_search(
        "terms", "All", "All", 5, False, False, 1)

    assert "Next context 1" in rendered
    assert "chunk_neighbor" in rendered
    assert "chunk_primary_alias" in rendered
    assert "chunk_neighbor_alias" in rendered


def test_supervised_ui_request_keeps_query_out_of_process_arguments(
        monkeypatch):
    observed = {}

    def fake_deadline(script, argv, **kwargs):
        observed.update(script=script, argv=argv, kwargs=kwargs)
        request_path = Path(argv[1])
        result_path = Path(argv[2])
        observed["request"] = __import__("json").loads(
            request_path.read_text(encoding="utf-8"))
        ui.rag._atomic_write_json(
            result_path, {"ok": True, "result": {"hits": []}})
        return 0

    monkeypatch.setattr(ui.rag, "_run_cli_with_deadline", fake_deadline)

    result = ui._supervised_vector_request(
        "search", {"query": "private search terms", "config": {}},
        timeout=2)

    assert result == {"hits": []}
    assert "private search terms" not in observed["argv"]
    assert observed["request"]["query"] == "private search terms"
    assert observed["kwargs"] == {"operation": "UI search", "timeout": 2}


def test_ui_hidden_worker_round_trip_uses_process_boundary():
    with pytest.raises(ui._VectorWorkerError, match="Unsupported UI vector"):
        ui._supervised_vector_request(
            "unsupported", {"config": {}}, timeout=15)


def test_split_export_uses_unique_directory_and_returns_archive(
        monkeypatch, tmp_path):
    chunks = tmp_path / "Book_chunks.jsonl"
    chunks.write_text("{}\n", encoding="utf-8")
    monkeypatch.setitem(ui._config, "chunks_path", chunks)
    export_id = "a" * 32
    monkeypatch.setattr(ui, "uuid4", lambda: SimpleNamespace(hex=export_id))

    def fake_export(chunks_path, out_path, **kwargs):
        assert kwargs["split_chapters"] is True
        chapters_dir = kwargs["chapters_dir"]
        chapters_dir.mkdir(parents=True)
        (chapters_dir / "ch01.md").write_text("# One", encoding="utf-8")

    monkeypatch.setattr(ui.rag, "export_markdown", fake_export)

    summary, archive = ui.do_export([], True, "", True)

    expected_root = tmp_path / "ui_exports" / export_id
    assert "ch01.md" in summary
    assert Path(archive).is_file()
    assert Path(archive).parent == expected_root
    marker = json.loads((
        expected_root / ui._UI_EXPORT_MARKER).read_text(encoding="utf-8"))
    assert marker["state"] == "complete"
    assert marker["ownership_token"] == export_id
    assert marker["artifacts"] == ["Chapters", "chapters.zip"]


def test_jobs_reindex_submits_only_the_configured_corpus(
        monkeypatch, tmp_path):
    chunks = tmp_path / "Private_chunks.jsonl"
    chunks.write_text("{}\n", encoding="utf-8")
    database = tmp_path / "Private_chroma"
    job_root = tmp_path / "jobs"
    monkeypatch.setitem(ui._config, "share", False)
    monkeypatch.setitem(ui._config, "chunks_path", chunks)
    monkeypatch.setitem(ui._config, "db_path", database)
    monkeypatch.setitem(ui._config, "db_backend", "chroma")
    monkeypatch.setitem(ui._config, "collection", "private_book")
    monkeypatch.setitem(ui._config, "embedding_model", "test-embedding")
    monkeypatch.setitem(ui._config, "db_lock_timeout", 7.0)
    monkeypatch.setitem(ui._config, "job_root", job_root)
    monkeypatch.setitem(ui._config, "job_ready_timeout", 2.0)
    monkeypatch.setitem(
        ui._config, "release_security_policy",
        release_security.ReleaseSecurityPolicy())
    observed = {}

    def fake_launch(store, job_id, **kwargs):
        observed.update(store=store, job_id=job_id, kwargs=kwargs)
        return SimpleNamespace(status="starting")

    binding = replace(
        job_application.default_job_application_binding(),
        launch_detached=fake_launch,
    )
    binding_resolutions = []
    monkeypatch.setattr(
        ui, "_default_job_application_binding",
        lambda: binding_resolutions.append(binding) or binding)

    rendered = ui.do_job_reindex(True)

    execution = observed["store"].load_execution(observed["job_id"])
    assert execution.command == "index"
    assert execution.argv == (
        "--chunks", str(chunks),
        "--db", str(database),
        "--db-backend", "chroma",
        "--collection", "private_book",
        "--embedding-model", "test-embedding",
        "--db-lock-timeout", "7.0",
        "--release-security-policy-version", "1",
        "--security-profile", "release",
        "--network-policy", "local-only",
        "--model-download-policy", "cache-only",
        "--full-reindex",
    )
    assert execution.timeout_seconds == ui.rag.DEFAULT_OPERATION_TIMEOUTS[
        "index"]
    assert observed["kwargs"] == {"ready_timeout": 2.0}
    assert binding_resolutions == [binding]
    assert observed["job_id"] in rendered
    assert str(chunks) not in rendered


def test_jobs_refresh_uses_a_fresh_store_current_root_and_one_binding_generation(
        monkeypatch, tmp_path):
    first_root = tmp_path / "jobs-a"
    second_root = tmp_path / "jobs-b"
    first_job_id = "a" * 32
    second_job_id = "b" * 32
    events = []
    stores = []

    def store_factory(root):
        store = SimpleNamespace(root=Path(root), ordinal=len(stores))
        stores.append(store)
        events.append(("store", store.root, store.ordinal))
        return store

    def reconcile_all(store):
        events.append(("reconcile_all", store.root, store.ordinal))
        job_id = first_job_id if store.root == first_root else second_job_id
        return [SimpleNamespace(
            job_id=job_id, command="index", status="queued",
            attempt_number=1,
        )]

    binding = replace(
        job_application.default_job_application_binding(),
        job_store_factory=store_factory,
        reconcile_all_jobs=reconcile_all,
    )
    generations = []

    def current_binding():
        generations.append(binding)
        return binding

    monkeypatch.setitem(ui._config, "share", False)
    monkeypatch.setitem(ui._config, "job_root", first_root)
    monkeypatch.setattr(ui, "_default_job_application_binding", current_binding)

    first = ui.do_jobs_refresh()
    ui._config["job_root"] = second_root
    second = ui.do_jobs_refresh()

    assert generations == [binding, binding]
    assert stores[0] is not stores[1]
    assert events == [
        ("store", first_root, 0),
        ("reconcile_all", first_root, 0),
        ("store", second_root, 1),
        ("reconcile_all", second_root, 1),
    ]
    assert first_job_id in first
    assert second_job_id in second


def test_jobs_cancel_requests_before_refresh_with_current_store_configuration(
        monkeypatch, tmp_path):
    initial_root = tmp_path / "jobs-before-cancel"
    refresh_root = tmp_path / "jobs-after-cancel"
    job_id = "c" * 32
    events = []
    stores = []

    class OperationStore:
        def request_cancel(self, selected_job_id):
            events.append(("request_cancel", selected_job_id))
            ui._config["job_root"] = refresh_root

    operation_store = OperationStore()
    refresh_store = SimpleNamespace(name="refresh")

    def store_factory(root):
        root = Path(root)
        store = operation_store if not stores else refresh_store
        stores.append(store)
        events.append(("store", root, store))
        return store

    def reconcile_all(store):
        events.append(("reconcile_all", store))
        return [SimpleNamespace(
            job_id=job_id, command="index", status="cancel_requested",
            attempt_number=1,
        )]

    binding = replace(
        job_application.default_job_application_binding(),
        job_store_factory=store_factory,
        reconcile_all_jobs=reconcile_all,
    )
    binding_resolutions = []
    monkeypatch.setitem(ui._config, "share", False)
    monkeypatch.setitem(ui._config, "job_root", initial_root)
    monkeypatch.setattr(
        ui, "_default_job_application_binding",
        lambda: binding_resolutions.append(binding) or binding,
    )

    rendered = ui.do_job_cancel(f"  {job_id}  ")

    assert binding_resolutions == [binding]
    assert events == [
        ("store", initial_root, operation_store),
        ("request_cancel", job_id),
        ("store", refresh_root, refresh_store),
        ("reconcile_all", refresh_store),
    ]
    assert job_id in rendered
    assert "cancel_requested" in rendered


def test_jobs_resume_orders_reconcile_prepare_launch_and_refresh_exactly(
        monkeypatch, tmp_path):
    initial_root = tmp_path / "jobs-before-resume"
    refresh_root = tmp_path / "jobs-after-resume"
    job_id = "d" * 32
    events = []
    stores = []

    class OperationStore:
        def prepare_resume(self, selected_job_id, **kwargs):
            events.append(("prepare_resume", selected_job_id, kwargs))

    operation_store = OperationStore()
    refresh_store = SimpleNamespace(name="refresh")

    def store_factory(root):
        root = Path(root)
        store = operation_store if not stores else refresh_store
        stores.append(store)
        events.append(("store", root, store))
        return store

    def reconcile(store, selected_job_id):
        events.append(("reconcile", store, selected_job_id))
        ui._config["job_ready_timeout"] = 8.75
        return SimpleNamespace(revision=17)

    def launch(store, selected_job_id, **kwargs):
        events.append(("launch", store, selected_job_id, kwargs))
        ui._config["job_root"] = refresh_root
        return SimpleNamespace(status="starting")

    def reconcile_all(store):
        events.append(("reconcile_all", store))
        return [SimpleNamespace(
            job_id=job_id, command="index", status="starting",
            attempt_number=2,
        )]

    binding = replace(
        job_application.default_job_application_binding(),
        job_store_factory=store_factory,
        launch_detached=launch,
        reconcile_job=reconcile,
        reconcile_all_jobs=reconcile_all,
    )
    binding_resolutions = []
    monkeypatch.setitem(ui._config, "share", False)
    monkeypatch.setitem(ui._config, "job_root", initial_root)
    monkeypatch.setitem(ui._config, "job_ready_timeout", 1.25)
    monkeypatch.setattr(
        ui, "_default_job_application_binding",
        lambda: binding_resolutions.append(binding) or binding,
    )

    rendered = ui.do_job_resume(f" {job_id} ")

    assert binding_resolutions == [binding]
    assert events == [
        ("store", initial_root, operation_store),
        ("reconcile", operation_store, job_id),
        (
            "prepare_resume", job_id,
            {"expected_revision": 17},
        ),
        (
            "launch", operation_store, job_id,
            {"ready_timeout": 8.75},
        ),
        ("store", refresh_root, refresh_store),
        ("reconcile_all", refresh_store),
    ]
    assert job_id in rendered
    assert "starting" in rendered


@pytest.mark.parametrize(
    ("operation", "expected"),
    [
        ("refresh", "Job status unavailable (SensitiveJobFailure)."),
        (
            "cancel",
            "Could not request cancellation (SensitiveJobFailure).",
        ),
        ("resume", "Could not resume job (SensitiveJobFailure)."),
    ],
)
def test_job_action_errors_expose_only_type_names(
        monkeypatch, tmp_path, operation, expected):
    private_message = str(tmp_path / "Private Ethics.pdf") + " token=secret"

    class SensitiveJobFailure(RuntimeError):
        pass

    failure = SensitiveJobFailure(private_message)

    class Store:
        def request_cancel(self, _job_id):
            if operation == "cancel":
                raise failure

        def prepare_resume(self, _job_id, **_kwargs):
            raise AssertionError("resume must fail during reconciliation")

    def reconcile_all(_store):
        if operation == "refresh":
            raise failure
        return []

    def reconcile(_store, _job_id):
        if operation == "resume":
            raise failure
        raise AssertionError("unexpected single-job reconciliation")

    binding = replace(
        job_application.default_job_application_binding(),
        job_store_factory=lambda _root: Store(),
        reconcile_job=reconcile,
        reconcile_all_jobs=reconcile_all,
    )
    monkeypatch.setitem(ui._config, "share", False)
    monkeypatch.setitem(ui._config, "job_root", tmp_path / "jobs")
    monkeypatch.setattr(
        ui, "_default_job_application_binding", lambda: binding)

    rendered = {
        "refresh": ui.do_jobs_refresh,
        "cancel": lambda: ui.do_job_cancel("e" * 32),
        "resume": lambda: ui.do_job_resume("f" * 32),
    }[operation]()

    assert rendered == expected
    assert private_message not in rendered


def test_jobs_reindex_spawn_failure_does_not_strand_queued_job(
        monkeypatch, tmp_path):
    chunks = tmp_path / "Private_chunks.jsonl"
    chunks.write_text("{}\n", encoding="utf-8")
    job_root = tmp_path / "jobs"
    monkeypatch.setitem(ui._config, "share", False)
    monkeypatch.setitem(ui._config, "chunks_path", chunks)
    monkeypatch.setitem(ui._config, "db_path", tmp_path / "Private_chroma")
    monkeypatch.setitem(ui._config, "db_backend", "chroma")
    monkeypatch.setitem(ui._config, "collection", "private_book")
    monkeypatch.setitem(ui._config, "embedding_model", "test-embedding")
    monkeypatch.setitem(ui._config, "db_lock_timeout", 7.0)
    monkeypatch.setitem(ui._config, "job_root", job_root)
    monkeypatch.setitem(ui._config, "job_ready_timeout", 0.1)
    monkeypatch.setattr(
        job_manager.subprocess, "Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            OSError("injected spawn failure")))

    rendered = ui.do_job_reindex(False)

    jobs = job_runtime.JobStore(job_root).list_jobs()
    assert "JobManagerLaunchError" in rendered
    assert len(jobs) == 1
    assert jobs[0].status == "failed"


def test_jobs_controls_are_disabled_without_touching_storage_when_shared(
        monkeypatch):
    monkeypatch.setitem(ui._config, "share", True)
    monkeypatch.setattr(
        ui, "_default_job_application_binding",
        lambda: pytest.fail(
            "shared UI must not resolve a job-application binding"))
    monkeypatch.setattr(
        ui, "_job_store",
        lambda *_args, **_kwargs: pytest.fail(
            "shared UI must not access the private job store"))
    monkeypatch.setattr(
        ui.job_runtime, "JobStore",
        lambda *_args, **_kwargs: pytest.fail(
            "shared UI must not construct durable storage directly"))

    assert "disabled" in ui.do_jobs_refresh().lower()
    assert "disabled" in ui.do_job_reindex(False).lower()
    assert "disabled" in ui.do_job_cancel("a" * 32).lower()
    assert "disabled" in ui.do_job_resume("a" * 32).lower()


def test_jobs_refresh_redacts_private_arguments(monkeypatch, tmp_path):
    store = job_runtime.JobStore(tmp_path / "jobs")
    private_pdf = tmp_path / "Private Casebook.pdf"
    submitted = store.submit_job("full", ["--pdf", str(private_pdf)])
    monkeypatch.setitem(ui._config, "share", False)
    monkeypatch.setitem(ui._config, "job_root", store.root)

    rendered = ui.do_jobs_refresh()

    assert submitted.job_id in rendered
    assert "queued" in rendered
    assert str(private_pdf) not in rendered
    assert "--pdf" not in rendered


def test_main_binds_literal_loopback_and_disables_public_sharing(
        monkeypatch, tmp_path):
    observed = {}

    class App:
        def launch(self, **kwargs):
            observed.update(kwargs)

    monkeypatch.setattr(ui, "build_app", lambda: App())

    ui.main([
        "--chunks", str(tmp_path / "chunks.jsonl"),
        "--db", str(tmp_path / "db"),
        "--collection", "book",
        "--port", "8877",
        "--trust-local-user",
    ])

    assert observed == {
        "server_name": "127.0.0.1",
        "server_port": 8877,
        "share": False,
        "enable_monitoring": False,
    }
    assert ui._config["share"] is False


def test_main_requires_explicit_trusted_single_user_boundary(
        monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(
        ui, "build_app", lambda: pytest.fail(
            "untrusted UI must not be built"))

    with pytest.raises(SystemExit):
        ui.main([
            "--chunks", str(tmp_path / "chunks.jsonl"),
            "--db", str(tmp_path / "db"),
            "--collection", "book",
        ])

    assert "requires --trust-local-user" in capsys.readouterr().err


def test_main_rejects_removed_share_flag_before_building_app(
        monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(
        ui, "build_app", lambda: pytest.fail(
            "rejected public-sharing option must not build the app"))

    with pytest.raises(SystemExit):
        ui.main([
            "--chunks", str(tmp_path / "chunks.jsonl"),
            "--db", str(tmp_path / "db"),
            "--collection", "book",
            "--share",
        ])

    assert "unrecognized arguments: --share" in capsys.readouterr().err
