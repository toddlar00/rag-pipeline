import json

import pytest

import rag


def test_cli_correlates_a_standalone_stage_without_persisting_paths(
        monkeypatch, tmp_path):
    private_chunks = tmp_path / "PRIVATE_BOOK_CANARY.jsonl"
    private_output = tmp_path / "PRIVATE_OUTPUT_CANARY.jsonl"
    events_path = tmp_path / "telemetry" / "events.jsonl"
    report_path = tmp_path / "telemetry" / "report.json"
    calls = []
    monkeypatch.setattr(
        rag, "extract_questions",
        lambda chunks, output: calls.append((chunks, output)))

    rag.main([
        "extract-questions", "--chunks", str(private_chunks),
        "--out", str(private_output), "--run-id", "run-cli-123",
        "--run-events", str(events_path),
        "--run-report", str(report_path),
    ])

    assert calls == [(private_chunks, private_output)]
    report = json.loads(report_path.read_text(encoding="utf-8"))
    combined = events_path.read_text(encoding="utf-8") + json.dumps(report)
    assert report["run_id"] == "run-cli-123"
    assert report["status"] == "succeeded"
    assert report["stages"]["extract-questions"]["completed"] == 1
    assert "PRIVATE_BOOK_CANARY" not in combined
    assert "PRIVATE_OUTPUT_CANARY" not in combined


def test_cli_uses_supervisor_run_id_for_llm_correlation(
        monkeypatch, tmp_path):
    llm_report = tmp_path / "llm.json"
    run_report = tmp_path / "run.json"
    monkeypatch.setenv(rag._RUN_ID_ENV, "supervisor-run-7")
    monkeypatch.setattr(rag, "generate_briefs", lambda *_args, **_kwargs: None)

    rag.main([
        "brief", "--llm-report", str(llm_report),
        "--run-report", str(run_report),
    ])

    assert json.loads(llm_report.read_text(encoding="utf-8"))["run_id"] == (
        "supervisor-run-7")
    assert json.loads(run_report.read_text(encoding="utf-8"))["run_id"] == (
        "supervisor-run-7")


def test_cli_finalizes_telemetry_when_setup_validation_fails(
        monkeypatch, tmp_path):
    report_path = tmp_path / "run.json"
    monkeypatch.setattr(
        rag, "_compile_watermark",
        lambda _value: (_ for _ in ()).throw(ValueError("PRIVATE_SETUP")))
    monkeypatch.setattr(
        rag._llm_runtime, "write_report",
        lambda: pytest.fail("LLM reporting must not run before configuration"))

    with pytest.raises(ValueError, match="PRIVATE_SETUP"):
        rag.main([
            "convert", "--pdf", str(tmp_path / "book.pdf"),
            "--out", str(tmp_path / "out"),
            "--run-report", str(report_path),
        ])

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert report["stages"]["convert"]["failed"] == 1
    assert "PRIVATE_SETUP" not in report_path.read_text(encoding="utf-8")


def test_cli_closes_active_stage_when_cancelled(monkeypatch, tmp_path):
    report_path = tmp_path / "run.json"
    monkeypatch.setattr(
        rag, "extract_questions",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(KeyboardInterrupt()))

    with pytest.raises(SystemExit) as raised:
        rag.main([
            "extract-questions", "--run-report", str(report_path),
        ])

    assert raised.value.code == 130
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "cancelled"
    assert report["stages"]["extract-questions"]["cancelled"] == 1


def test_batch_with_missing_input_reports_partial_completion(tmp_path):
    report_path = tmp_path / "run.json"

    rag.main([
        "batch", str(tmp_path / "missing.pdf"),
        "--run-report", str(report_path),
    ])

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "partial"
    assert report["stages"]["item_1.input"]["failed"] == 1
    assert report["stages"]["batch"]["completed"] == 1


def test_cli_rejects_cross_family_telemetry_output_aliases(
        monkeypatch, tmp_path):
    output = tmp_path / "shared.json"
    monkeypatch.setattr(
        rag, "generate_briefs",
        lambda *_args, **_kwargs: pytest.fail("command must not start"))

    with pytest.raises(SystemExit) as raised:
        rag.main([
            "brief", "--run-report", str(output),
            "--llm-report", str(output),
        ])

    assert raised.value.code == 2
    assert not output.exists()
