from pathlib import Path
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
import json
import threading

import pytest

import job_runtime
import rag
from operation_contracts import IndexOutcome
from run_telemetry import RunTelemetry


def _index_outcome(backend="chroma"):
    return IndexOutcome(
        backend=backend, disposition="unchanged", total_records=0,
        changed_records=0, unchanged_records=0, removed_records=0,
        upserted_records=0, batch_count=0, physical_count=0,
        committed=True)


def _args(**overrides):
    values = {
        "collection": None,
        "db_backend": "chroma",
        "embedding_model": "test-embedding",
        "full_reindex": False,
        "batch_size": None,
        "backend": "auto",
        "force": False,
        "no_preprocess": False,
        "max_tokens": 512,
        "min_words": 10,
        "dedup_threshold": 0.9,
        "llm_classify": False,
        "zeroshot_classify": False,
        "contextualize": False,
        "reconstruct_headings": False,
        "quality_score": False,
        "cloud_url": "https://example.test/v1",
        "cloud_model": "test-model",
        "cloud_key": "test-key",
        "ollama_url": "",
        "ollama_model": "",
        "gemini_key": "",
        "llm_workers": 2,
        "thinking": False,
        "split_chapters": False,
        "raptor": False,
        "db_lock_timeout": rag.DEFAULT_DB_LOCK_TIMEOUT,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _paths(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(rag, "OUTPUT_DIR", tmp_path / "output")
    monkeypatch.setattr(rag, "_converted_outputs_complete", lambda *a, **k: True)
    monkeypatch.setattr(rag, "_chunks_complete", lambda *a, **k: True)
    monkeypatch.setattr(rag, "_unified_export_complete", lambda *a, **k: True)
    monkeypatch.setattr(rag, "_split_export_complete", lambda *a, **k: True)
    monkeypatch.setattr(rag, "_raptor_output_complete", lambda *a, **k: True)
    return rag._output_paths_for_name("Book")


def test_shared_runner_passes_distinct_per_run_conversion_artifacts(
        monkeypatch, tmp_path):
    paths = _paths(monkeypatch, tmp_path)
    calls = []

    monkeypatch.setattr(
        rag, "convert_pdf",
        lambda *args, **kwargs: calls.append(("convert", args, kwargs)))
    monkeypatch.setattr(
        rag, "chunk_document",
        lambda *args, **kwargs: calls.append(("chunk", args, kwargs)))
    def record_index(*args, **kwargs):
        calls.append(("index", args, kwargs))
        return _index_outcome()

    monkeypatch.setattr(rag, "_index_chunks_for_backend", record_index)
    monkeypatch.setattr(
        rag, "export_markdown",
        lambda *args, **kwargs: calls.append(("export", args, kwargs)))

    result = rag._run_pipeline_stages(
        Path("Book.pdf"), paths, _args(), resume=False, watermark=None)

    convert = next(call for call in calls if call[0] == "convert")
    assert convert[2]["preprocessed_output"] == paths["preprocessed"]
    assert convert[2]["markdown_output"] == paths["converted_markdown"]
    assert paths["converted_markdown"] != paths["export"]
    assert [call[0] for call in calls] == ["convert", "chunk", "index", "export"]
    active_token = next(
        call for call in calls if call[0] == "index")[2][
            "_active_update_token"]
    assert isinstance(active_token, str) and active_token
    marker_path = rag._index_update_marker_path(
        paths["chroma"], backend="chroma", collection_name="book")
    assert json.loads(marker_path.read_text(encoding="utf-8"))[
        "owner_token"] == active_token
    assert result["collection"] == "book"
    assert result["db_dir"] == paths["chroma"]


def test_shared_runner_honors_an_explicit_default_named_collection(
        monkeypatch, tmp_path):
    paths = _paths(monkeypatch, tmp_path)
    monkeypatch.setattr(rag, "convert_pdf", lambda *args, **kwargs: None)
    monkeypatch.setattr(rag, "chunk_document", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        rag, "_index_chunks_for_backend",
        lambda *args, **kwargs: _index_outcome())
    monkeypatch.setattr(rag, "export_markdown", lambda *args, **kwargs: None)

    result = rag._run_pipeline_stages(
        Path("Book.pdf"), paths,
        _args(collection=rag.DEFAULT_COLLECTION),
        resume=False, watermark=None,
    )

    assert result["collection"] == rag.DEFAULT_COLLECTION


def test_shared_runner_honors_split_chapters_flag(monkeypatch, tmp_path):
    paths = _paths(monkeypatch, tmp_path)
    exports = []
    monkeypatch.setattr(rag, "convert_pdf", lambda *args, **kwargs: None)
    monkeypatch.setattr(rag, "chunk_document", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        rag, "_index_chunks_for_backend",
        lambda *args, **kwargs: _index_outcome())
    monkeypatch.setattr(
        rag, "export_markdown",
        lambda *args, **kwargs: exports.append((args, kwargs)))

    rag._run_pipeline_stages(
        Path("Book.pdf"), paths, _args(split_chapters=True),
        resume=False, watermark=None)

    assert len(exports) == 2
    assert "split_chapters" not in exports[0][1]
    assert exports[1][1]["split_chapters"] is True
    assert exports[1][1]["chapters_dir"] == paths["chapters_dir"]


def test_resume_revalidates_index_and_rebuilds_missing_chapter_exports(
        monkeypatch, tmp_path):
    paths = _paths(monkeypatch, tmp_path)
    paths["doc"].parent.mkdir(parents=True, exist_ok=True)
    paths["doc"].write_text("{}", encoding="utf-8")
    paths["chunks"].write_text(
        '{"text":"complete","metadata":{}}\n', encoding="utf-8")
    paths["export"].write_text("complete", encoding="utf-8")
    paths["chroma"].mkdir()

    monkeypatch.setattr(
        rag, "convert_pdf",
        lambda *args, **kwargs: pytest.fail("convert should have resumed"))
    monkeypatch.setattr(
        rag, "chunk_document",
        lambda *args, **kwargs: pytest.fail("chunk should have resumed"))
    index_calls = []
    def record_index(*args, **kwargs):
        index_calls.append((args, kwargs))
        return _index_outcome()

    monkeypatch.setattr(rag, "_index_chunks_for_backend", record_index)
    exports = []
    monkeypatch.setattr(
        rag, "export_markdown",
        lambda *args, **kwargs: exports.append((args, kwargs)))
    monkeypatch.setattr(
        rag, "_split_export_complete",
        lambda *args, **kwargs: bool(exports))

    rag._run_pipeline_stages(
        Path("Book.pdf"), paths, _args(split_chapters=True),
        resume=True, watermark=None)

    assert len(exports) == 1
    assert exports[0][1]["split_chapters"] is True
    assert len(index_calls) == 1
    assert index_calls[0][1]["embedding_model"] == "test-embedding"
    assert index_calls[0][1]["_active_update_token"] is None


def test_resume_rechunks_when_completion_is_missing(monkeypatch, tmp_path):
    paths = _paths(monkeypatch, tmp_path)
    paths["doc"].parent.mkdir(parents=True, exist_ok=True)
    paths["doc"].write_text("{}", encoding="utf-8")
    paths["chunks"].write_text(
        '{"text":"stale","metadata":{}}\n', encoding="utf-8")
    state = {"complete": False, "calls": 0}

    monkeypatch.setattr(
        rag, "_chunks_complete",
        lambda *args, **kwargs: state["complete"])
    monkeypatch.setattr(
        rag, "convert_pdf",
        lambda *args, **kwargs: pytest.fail("conversion should resume"))

    def rechunk(*_args, **_kwargs):
        state["calls"] += 1
        paths["chunks"].write_text(
            '{"text":"fresh","metadata":{}}\n', encoding="utf-8")
        state["complete"] = True

    monkeypatch.setattr(rag, "chunk_document", rechunk)
    monkeypatch.setattr(
        rag, "_index_chunks_for_backend",
        lambda *args, **kwargs: _index_outcome())
    monkeypatch.setattr(rag, "export_markdown", lambda *args, **kwargs: None)

    rag._run_pipeline_stages(
        Path("Book.pdf"), paths, _args(), resume=True, watermark=None)

    assert state["calls"] == 1
    assert "fresh" in paths["chunks"].read_text(encoding="utf-8")


def test_pipeline_chunk_failure_leaves_dirty_marker(monkeypatch, tmp_path):
    paths = _paths(monkeypatch, tmp_path)
    monkeypatch.setattr(rag, "convert_pdf", lambda *args, **kwargs: None)
    chunk_error = RuntimeError("injected chunk failure")
    monkeypatch.setattr(
        rag, "chunk_document",
        lambda *args, **kwargs: (_ for _ in ()).throw(chunk_error))
    monkeypatch.setattr(
        rag, "_index_chunks_for_backend",
        lambda *args, **kwargs: pytest.fail("failed chunks must not index"))

    with pytest.raises(rag._PipelineStageError) as raised:
        rag._run_pipeline_stages(
            Path("Book.pdf"), paths, _args(),
            resume=False, watermark=None)

    assert raised.value.cause is chunk_error
    marker_path = rag._index_update_marker_path(
        paths["chroma"], backend="chroma", collection_name="book")
    assert marker_path.is_file()


def test_pipeline_job_lock_prevents_duplicate_run_allocation(
        monkeypatch, tmp_path):
    monkeypatch.setattr(rag, "OUTPUT_DIR", tmp_path / "output")
    first_entered = threading.Event()
    release_first = threading.Event()
    observed_paths = []

    def fake_stages(_pdf, paths, _args, **_kwargs):
        observed_paths.append(paths["doc"].parent.name)
        paths["doc"].parent.mkdir(parents=True, exist_ok=True)
        if len(observed_paths) == 1:
            first_entered.set()
            assert release_first.wait(timeout=2)
        return {"collection": "book", "db_dir": paths["chroma"]}

    monkeypatch.setattr(rag, "_run_pipeline_stages", fake_stages)
    args = _args(db_lock_timeout=0.05)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(
            rag._run_pipeline_job, Path("Book.pdf"), args,
            resume=False, watermark=None)
        assert first_entered.wait(timeout=2)

        with pytest.raises(rag._PipelineStageError) as raised:
            rag._run_pipeline_job(
                Path("Book.pdf"), args,
                resume=False, watermark=None)
        assert raised.value.stage == "concurrency"
        assert isinstance(raised.value.cause, rag.VectorStoreBusyError)

        release_first.set()
        first_paths, _ = first.result(timeout=2)

    second_paths, _ = rag._run_pipeline_job(
        Path("Book.pdf"), _args(db_lock_timeout=1),
        resume=False, watermark=None)

    assert first_paths["doc"].parent.name == "Book"
    assert second_paths["doc"].parent.name == "Book_2"
    assert observed_paths == ["Book", "Book_2"]


def test_pipeline_job_commits_an_owned_run_manifest(monkeypatch, tmp_path):
    output_root = tmp_path / "output"
    monkeypatch.setattr(rag, "OUTPUT_DIR", output_root)
    observed = {}

    def fake_stages(_pdf, paths, _args, **_kwargs):
        observed["paths"] = paths
        return {"collection": "book", "db_dir": paths["chroma"]}

    monkeypatch.setattr(rag, "_run_pipeline_stages", fake_stages)

    paths, _ = rag._run_pipeline_job(
        Path("Book.pdf"), _args(), resume=False, watermark=None)

    manifest = json.loads((
        paths["doc"].parent / ".rag-run.json").read_text(encoding="utf-8"))
    assert manifest["state"] == "complete"
    assert manifest["run_name"] == "Book"
    assert manifest["job_scope"] == "Book"
    assert manifest["owned_siblings"] == ["Book_preprocessed.pdf"]
    assert {record["backend"] for record in manifest["vector_stores"]} == {
        "chroma", "qdrant"}


def test_pipeline_allocation_callback_is_durable_and_lease_scoped(
        monkeypatch, tmp_path):
    output_root = tmp_path / "output"
    monkeypatch.setattr(rag, "OUTPUT_DIR", output_root)
    events = []

    def record_allocation(run_name):
        assert (output_root / run_name / ".rag-run.json").is_file()
        events.append(("allocated", run_name))

    def fake_stages(_pdf, paths, _args, **_kwargs):
        events.append(("stages", paths["doc"].parent.name))
        return {"collection": "book", "db_dir": paths["chroma"]}

    monkeypatch.setattr(rag, "_run_pipeline_stages", fake_stages)

    rag._run_pipeline_job(
        Path("Book.pdf"), _args(), resume=False, watermark=None,
        on_run_allocated=record_allocation)

    assert events == [("allocated", "Book"), ("stages", "Book")]


def test_exact_resume_binding_does_not_drift_to_a_later_run(
        monkeypatch, tmp_path):
    output_root = tmp_path / "output"
    monkeypatch.setattr(rag, "OUTPUT_DIR", output_root)
    observed = []

    def fake_stages(_pdf, paths, _args, **_kwargs):
        observed.append(paths["doc"].parent.name)
        return {"collection": "book", "db_dir": paths["chroma"]}

    monkeypatch.setattr(rag, "_run_pipeline_stages", fake_stages)

    first, _ = rag._run_pipeline_job(
        Path("Book.pdf"), _args(), resume=False, watermark=None)
    second, _ = rag._run_pipeline_job(
        Path("Book.pdf"), _args(), resume=False, watermark=None)
    resumed, _ = rag._run_pipeline_job(
        Path("Book.pdf"), _args(), resume=True, watermark=None,
        exact_run_name=first["doc"].parent.name)

    assert first["doc"].parent.name == "Book"
    assert second["doc"].parent.name == "Book_2"
    assert resumed["doc"].parent.name == "Book"
    assert observed == ["Book", "Book_2", "Book"]


@pytest.mark.parametrize("run_name", ["Other", "Book_1", "../Book", ""])
def test_exact_resume_binding_rejects_unrelated_or_unsafe_names(
        monkeypatch, tmp_path, run_name):
    monkeypatch.setattr(rag, "OUTPUT_DIR", tmp_path / "output")

    with pytest.raises(ValueError, match="does not match"):
        rag._run_pipeline_job(
            Path("Book.pdf"), _args(), resume=True, watermark=None,
            exact_run_name=run_name)


def test_exact_resume_binding_requires_an_existing_run(monkeypatch, tmp_path):
    monkeypatch.setattr(rag, "OUTPUT_DIR", tmp_path / "output")

    with pytest.raises(FileNotFoundError, match="does not exist"):
        rag._run_pipeline_job(
            Path("Book.pdf"), _args(), resume=True, watermark=None,
            exact_run_name="Book")


def test_background_binding_plan_allocates_once_then_resumes_exact_run(
        monkeypatch, tmp_path):
    output_root = tmp_path / "output"
    jobs = job_runtime.JobStore(tmp_path / "jobs")
    pdf = tmp_path / "Book.pdf"
    summary = jobs.submit_job("full", ["--pdf", str(pdf)])
    execution = jobs.load_execution(summary.job_id)
    context = job_runtime.JobWorkerContext(jobs, execution)
    monkeypatch.setattr(rag, "OUTPUT_DIR", output_root)
    monkeypatch.setattr(
        rag, "_run_pipeline_stages",
        lambda _pdf, paths, _args, **_kwargs: {
            "collection": "book", "db_dir": paths["chroma"]})

    resume, exact, callback, binding = rag._background_pipeline_plan(
        context, pdf, 1, fallback_resume=True)
    assert (resume, exact, binding) == (False, None, None)
    first, _ = rag._run_pipeline_job(
        pdf, _args(), resume=resume, watermark=None,
        exact_run_name=exact, on_run_allocated=callback)
    jobs.mark_pipeline_binding(
        summary.job_id, attempt_token=execution.attempt_token,
        item_index=1, input_path=pdf, status="failed")

    later, _ = rag._run_pipeline_job(
        pdf, _args(), resume=False, watermark=None)
    resume, exact, callback, binding = rag._background_pipeline_plan(
        context, pdf, 1, fallback_resume=True)
    resumed, _ = rag._run_pipeline_job(
        pdf, _args(), resume=resume, watermark=None,
        exact_run_name=exact, on_run_allocated=callback)

    assert first["doc"].parent.name == "Book"
    assert later["doc"].parent.name == "Book_2"
    assert binding is not None and binding.run_name == "Book"
    assert resumed["doc"].parent.name == "Book"


def test_background_batch_revalidates_completed_exact_checkpoint(
        monkeypatch, tmp_path):
    jobs = job_runtime.JobStore(tmp_path / "jobs")
    pdf = tmp_path / "Book.pdf"
    pdf.write_bytes(b"changed source must be revalidated")
    summary = jobs.submit_job("batch", [str(pdf)])
    execution = jobs.load_execution(summary.job_id)
    jobs.bind_pipeline_run(
        summary.job_id, attempt_token=execution.attempt_token,
        item_index=1, input_path=pdf, run_name="Book")
    jobs.mark_pipeline_binding(
        summary.job_id, attempt_token=execution.attempt_token,
        item_index=1, input_path=pdf, status="complete")
    starting = jobs.transition_job(
        summary.job_id, "starting", attempt_token=execution.attempt_token)
    jobs.transition_job(
        summary.job_id, "running", attempt_token=execution.attempt_token,
        expected_revision=starting.revision)
    monkeypatch.setenv(job_runtime.JOB_ROOT_ENV, str(jobs.root))
    monkeypatch.setenv(job_runtime.JOB_ID_ENV, summary.job_id)
    monkeypatch.setenv(
        job_runtime.JOB_ATTEMPT_TOKEN_ENV, execution.attempt_token)
    output_root = tmp_path / "output"
    monkeypatch.setattr(rag, "OUTPUT_DIR", output_root)
    observed = {}

    def fake_pipeline(_pdf, _args, **kwargs):
        observed.update(kwargs)
        paths = rag._output_paths_for_name("Book")
        return paths, {
            "collection": "book",
            "db_dir": paths["chroma"],
        }

    monkeypatch.setattr(rag, "_run_pipeline_job", fake_pipeline)

    rag.main(["batch", str(pdf)])

    assert observed["resume"] is True
    assert observed["exact_run_name"] == "Book"


def test_pipeline_job_marks_manifest_failed_without_masking_error(
        monkeypatch, tmp_path):
    output_root = tmp_path / "output"
    monkeypatch.setattr(rag, "OUTPUT_DIR", output_root)
    failure = RuntimeError("injected stage failure")
    monkeypatch.setattr(
        rag, "_run_pipeline_stages",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(failure))

    with pytest.raises(RuntimeError) as raised:
        rag._run_pipeline_job(
            Path("Book.pdf"), _args(), resume=False, watermark=None)

    assert raised.value is failure
    manifest = json.loads((
        output_root / "Book" / ".rag-run.json").read_text(encoding="utf-8"))
    assert manifest["state"] == "failed"


def test_resume_does_not_claim_a_legacy_unowned_run(monkeypatch, tmp_path):
    output_root = tmp_path / "output"
    run_root = output_root / "Book"
    run_root.mkdir(parents=True)
    (run_root / "legacy.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(rag, "OUTPUT_DIR", output_root)
    monkeypatch.setattr(
        rag, "_run_pipeline_stages",
        lambda _pdf, paths, _args, **_kwargs: {
            "collection": "book", "db_dir": paths["chroma"]})

    paths, _ = rag._run_pipeline_job(
        Path("Book.pdf"), _args(), resume=True, watermark=None)

    assert paths["doc"].parent == run_root
    assert not (run_root / ".rag-run.json").exists()
    assert (run_root / "legacy.json").read_text(encoding="utf-8") == "{}"


def test_runner_reports_the_failing_stage_including_system_exit(
        monkeypatch, tmp_path):
    paths = _paths(monkeypatch, tmp_path)
    monkeypatch.setattr(rag, "convert_pdf", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        rag, "chunk_document",
        lambda *args, **kwargs: (_ for _ in ()).throw(SystemExit(2)))

    with pytest.raises(rag._PipelineStageError) as error:
        rag._run_pipeline_stages(
            Path("Book.pdf"), paths, _args(), resume=False, watermark=None)

    assert error.value.stage == "chunk"
    assert isinstance(error.value.cause, SystemExit)


def test_convert_derives_a_preprocessed_path_from_its_output(
        monkeypatch, tmp_path):
    pdf = tmp_path / "source.pdf"
    pdf.write_bytes(b"pdf")
    doc_output = tmp_path / "run" / "book.json"
    observed = {}

    monkeypatch.setattr(
        rag, "_analyze_pdf_images",
        lambda path: {
            "total_pages": 1,
            "pages_with_large_images": 1,
            "unique_dims": set(),
            "image_xrefs": set(),
        },
    )

    class StopAfterPreprocess(Exception):
        pass

    def fake_preprocess(input_path, output_path, **kwargs):
        observed["output"] = output_path
        raise StopAfterPreprocess

    monkeypatch.setattr(rag, "preprocess_pdf", fake_preprocess)

    with pytest.raises(StopAfterPreprocess):
        rag.convert_pdf(pdf, doc_output)

    assert observed["output"] == doc_output.with_name("book_preprocessed.pdf")


def test_resume_command_preserves_pipeline_options():
    args = _args(
        split_chapters=True,
        llm_scaffold=True,
        contextualize=True,
        thinking=True,
        cloud_url="https://api.deepseek.com",
        cloud_model="deepseek-v4-pro",
        cloud_key="deepseek-secret",
        gemini_key="gemini-secret",
        max_tokens=1024,
        min_words=5,
        backend="auto",
        db_lock_timeout=7,
        operation_timeout=99,
        max_llm_transport_attempts=23,
    )

    command = rag._build_resume_cmd(Path("My Book.pdf"), args)

    assert '--pdf "My Book.pdf" --resume' in command
    assert "--split-chapters" in command
    assert "--llm-scaffold" in command
    assert "--contextualize" in command
    assert "--thinking" in command
    assert "--cloud-url https://api.deepseek.com" in command
    assert "--cloud-model deepseek-v4-pro" in command
    assert "--max-tokens 1024" in command
    assert "--min-words 5" in command
    assert "--backend auto" in command
    assert "--db-lock-timeout 7" in command
    assert "--operation-timeout 99" in command
    assert "--max-llm-transport-attempts 23" in command
    assert "deepseek-secret" not in command
    assert "gemini-secret" not in command
    assert "--cloud-key" not in command
    assert "--api-key" not in command
    assert "--gemini-key" not in command


def test_shared_runner_forwards_thinking_and_provider_to_all_llm_stages(
        monkeypatch, tmp_path):
    paths = _paths(monkeypatch, tmp_path)
    calls = {}

    monkeypatch.setattr(rag, "convert_pdf", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        rag,
        "chunk_document",
        lambda *args, **kwargs: calls.setdefault("chunk", kwargs),
    )
    monkeypatch.setattr(
        rag, "_index_chunks_for_backend",
        lambda *args, **kwargs: _index_outcome())
    monkeypatch.setattr(
        rag,
        "export_markdown",
        lambda *args, **kwargs: calls.setdefault("export", kwargs),
    )
    monkeypatch.setattr(
        rag,
        "build_raptor_tree",
        lambda *args, **kwargs: calls.setdefault("raptor", kwargs),
    )
    args = _args(
        raptor=True,
        thinking=True,
        cloud_url="https://api.deepseek.com",
        cloud_model="deepseek-v4-pro",
        cloud_key="typed-key",
        llm_workers=4,
    )

    rag._run_pipeline_stages(
        Path("Book.pdf"), paths, args, resume=False, watermark=None)

    assert set(calls) == {"chunk", "export", "raptor"}
    for kwargs in calls.values():
        assert kwargs["thinking"] is True
        assert kwargs["cloud_url"] == "https://api.deepseek.com"
        assert kwargs["cloud_model"] == "deepseek-v4-pro"
        assert kwargs["cloud_key"] == "typed-key"
        assert kwargs["llm_workers"] == 4


def test_resume_validators_reject_partial_json_artifacts(tmp_path):
    doc = tmp_path / "book.json"
    chunks = tmp_path / "chunks.jsonl"
    doc.write_text('{"partial":', encoding="utf-8")
    chunks.write_text(
        '{"text":"valid","metadata":{}}\n{"text":', encoding="utf-8")

    assert rag._json_file_is_valid(doc) is False
    assert rag._chunk_record_count(chunks) is None


def test_split_export_rejects_non_markdown_formats(tmp_path):
    with pytest.raises(ValueError, match="only for markdown"):
        rag.export_markdown(
            tmp_path / "chunks.jsonl",
            tmp_path / "book.txt",
            split_chapters=True,
            format="plaintext",
        )


def test_pipeline_runner_emits_scoped_stage_and_index_outcomes(
        monkeypatch, tmp_path):
    paths = _paths(monkeypatch, tmp_path)
    monkeypatch.setattr(rag, "convert_pdf", lambda *args, **kwargs: None)
    monkeypatch.setattr(rag, "chunk_document", lambda *args, **kwargs: None)
    monkeypatch.setattr(rag, "export_markdown", lambda *args, **kwargs: None)
    outcome = IndexOutcome(
        backend="chroma", disposition="updated", total_records=4,
        changed_records=1, unchanged_records=3, removed_records=1,
        upserted_records=1, batch_count=1, physical_count=4,
        committed=True)
    monkeypatch.setattr(
        rag, "_index_chunks_for_backend",
        lambda *args, **kwargs: outcome)
    telemetry = RunTelemetry(
        "batch", run_id="batch-run",
        events_path=tmp_path / "events.jsonl",
        report_path=tmp_path / "report.json")
    telemetry.start()

    result = rag._run_pipeline_stages(
        Path("PRIVATE_BOOK.pdf"), paths, _args(), resume=False,
        watermark=None, telemetry=telemetry, stage_scope="item_1")
    report = telemetry.finish()

    assert result["index_outcome"] == outcome
    assert report["stages"]["item_1.convert"]["completed"] == 1
    assert report["stages"]["item_1.index"]["completed"] == 1
    events = (tmp_path / "events.jsonl").read_text(encoding="utf-8")
    assert '"changed_records":1' in events
    assert "PRIVATE_BOOK" not in events
