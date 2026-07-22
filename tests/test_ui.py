from pathlib import Path
from types import SimpleNamespace
import json

import pytest

import job_manager
import job_runtime
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
    observed = {}

    def fake_launch(store, job_id, **kwargs):
        observed.update(store=store, job_id=job_id, kwargs=kwargs)
        return SimpleNamespace(status="starting")

    monkeypatch.setattr(job_manager, "launch_detached", fake_launch)

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
        "--full-reindex",
    )
    assert execution.timeout_seconds == ui.rag.DEFAULT_OPERATION_TIMEOUTS[
        "index"]
    assert observed["kwargs"] == {"ready_timeout": 2.0}
    assert observed["job_id"] in rendered
    assert str(chunks) not in rendered


def test_jobs_controls_are_disabled_without_touching_storage_when_shared(
        monkeypatch):
    monkeypatch.setitem(ui._config, "share", True)
    monkeypatch.setattr(
        ui.job_runtime, "JobStore",
        lambda *_args, **_kwargs: pytest.fail(
            "shared UI must not access the private job store"))

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
