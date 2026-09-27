"""Deadline CLI contracts without starting OCR, opening PDFs, or loading models."""

import copy
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from tools import retry_ocr as cli


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PRIVATE = "PRIVATE_DEADLINE_SOURCE"
BASE_ARGS = ["--pdf", f"{PRIVATE}.pdf", "--output", f"{PRIVATE}.json"]
ARGS = BASE_ARGS + ["--timeout-seconds", "10"]


def _report(*, failed=False, requested=(), max_pages=5, dpi=300, preprocessing="none", evidence=False):
    report = json.loads((PROJECT_ROOT / "evaluation" / "ocr" / "comparison-recovery.json").read_bytes())
    report["selection"].update(requested_pages=sorted(set(requested)), max_pages=max_pages)
    report["retry_configuration"]["dpi"] = dpi
    report["evidence_sha256"] = "b" * 64 if evidence else None
    if preprocessing != "none":
        report["schema_version"] = 2
        report["retry_configuration"]["preprocessing"] = preprocessing
    numbers = sorted(set(requested)) if len(set(requested)) == 2 else [1, 2]
    report["page_count"] = max(numbers)
    report["summary"]["inspected"] = max(numbers)
    for number, page in zip(numbers, report["pages"]):
        page["page_number"] = number
        page["reasons"] = (["requested"] if number in requested else []) + ["short_text"]
        page["original_text"] = PRIVATE
        page["candidate"]["text"] = PRIVATE + "_CANDIDATE"
        page["candidate"]["lines"][0]["text"] = PRIVATE + "_CANDIDATE"
        page["candidate"]["raster"]["dpi"] = dpi
        if preprocessing == "contrast":
            page["candidate"]["raster"]["coordinate_system"] = "preprocessed_image_pixels"
            page["candidate"]["preprocessing"] = {
                "schema_version": 1, "algorithm": "bounded-deskew-contrast-v1", "mode": "contrast",
                "original_raster": {"width": 100, "height": 100},
                "processed_raster": {"width": 100, "height": 100},
                "source_to_processed": [[1, 0, 0], [0, 1, 0]],
                "processed_to_source": [[1, 0, 0], [0, 1, 0]],
                "deskew": {"status": "disabled", "reason": "mode_disabled", "angle_degrees": 0.0,
                           "estimated_angle_degrees": None, "gain": None},
                "contrast": {"status": "skipped", "reason": "already_high_contrast", "low_level": 0, "high_level": 255},
                "parameters": {"thumbnail_max_side": 1000, "max_angle_degrees": 5.0,
                               "angle_step_degrees": 0.25, "min_gain": 0.025,
                               "low_percentile": 0.5, "high_percentile": 99.5},
                "libraries": {"opencv": "synthetic", "numpy": "synthetic"},
            }
    if failed:
        report["pages"][0].update(status="retry_failed", candidate=None, error_code="retry_runtime_failed")
        report["summary"].update(failed=1, review_required=1)
    return report


def _forbid_direct_recovery(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("deadline branch bypassed the supervisor")

    monkeypatch.setattr(cli, "recover_pdf", unexpected)


@pytest.mark.parametrize("failed", [False, True])
def test_supervised_success_loads_report_and_preserves_summary_exit(monkeypatch, capsys, failed):
    report = _report(failed=failed)
    expected = 3 if failed else 0
    calls = []
    _forbid_direct_recovery(monkeypatch)

    def supervise(argv, *, timeout):
        calls.append((argv, timeout))
        return expected

    def load(path):
        calls.append(path)
        return report

    monkeypatch.setattr(cli, "run_retry_with_deadline", supervise)
    monkeypatch.setattr(cli, "load_supervised_report", load)
    assert cli.main(ARGS) == expected
    child_args, timeout = calls[0]
    assert timeout == 10.0
    assert child_args == [
        f"--pdf={PRIVATE}.pdf", f"--output={PRIVATE}.json",
        "--dpi", "300", "--max-pages", "5", "--preprocess", "none",
    ]
    assert calls[1] == Path(f"{PRIVATE}.json")
    captured = capsys.readouterr()
    assert "OCR review:" in captured.out
    assert "Original extraction unchanged" in captured.out
    assert PRIVATE not in captured.out + captured.err
    if failed:
        assert "Failure guidance:" in captured.out


def test_child_arguments_use_known_values_and_preserve_leading_dash_paths(monkeypatch):
    _forbid_direct_recovery(monkeypatch)
    calls = []
    monkeypatch.setattr(sys, "argv", ["retry_ocr.py", "--timeout-seconds", "999", PRIVATE])
    monkeypatch.setenv("RAG_OCR_SUPERVISED", "1")
    monkeypatch.setenv("RAG_OCR_WORKER", "1")

    def supervise(argv, *, timeout):
        calls.append((argv, timeout))
        return 0

    monkeypatch.setattr(cli, "run_retry_with_deadline", supervise)
    monkeypatch.setattr(cli, "load_supervised_report", lambda path: _report(
        requested=(3, 7), max_pages=2, dpi=400, preprocessing="contrast", evidence=True))
    assert cli.main([
        "--pdf=-source.pdf", "--output=-report.json", "--evidence=-evidence.json",
        "--pages", "7", "3", "--dpi", "400", "--max-pages", "2",
        "--preprocess", "contrast", "--timeout-seconds", "12.5",
    ]) == 0
    child_args, timeout = calls[0]
    assert timeout == 12.5
    assert child_args[:2] == ["--pdf=-source.pdf", "--output=-report.json"]
    assert "--evidence=-evidence.json" in child_args
    assert child_args[child_args.index("--pages") + 1:] == ["7", "3"]
    for flag, value in (("--dpi", "400"), ("--max-pages", "2"), ("--preprocess", "contrast")):
        assert child_args[child_args.index(flag) + 1] == value
    assert not any(value.startswith("--timeout") for value in child_args)
    assert PRIVATE not in child_args


def test_supervisor_adapter_is_lazy_and_forwards_exact_values(monkeypatch):
    calls = []

    def supervise(argv, *, timeout):
        calls.append((argv, timeout))
        return 124

    monkeypatch.setitem(sys.modules, "ocr_recovery_supervision", SimpleNamespace(run_retry_with_deadline=supervise))
    argv = ["--pdf=source.pdf", "--output=report.json"]
    assert cli.run_retry_with_deadline(argv, timeout=9.5) == 124
    assert calls == [(argv, 9.5)]


@pytest.mark.parametrize("timeout", ["0", "-1", "0.5", "86401", "nan", "inf", "-inf", PRIVATE])
def test_invalid_timeout_is_rejected_before_worker_or_output_read(monkeypatch, capsys, timeout):
    _forbid_direct_recovery(monkeypatch)
    monkeypatch.setattr(cli, "run_retry_with_deadline", lambda *args, **kwargs: pytest.fail("invalid timeout started work"))
    monkeypatch.setattr(cli, "load_supervised_report", lambda *args: pytest.fail("invalid timeout read output"))
    with pytest.raises(SystemExit) as error:
        cli.main(BASE_ARGS + [f"--timeout-seconds={timeout}"])
    assert error.value.code == 2
    captured = capsys.readouterr()
    assert "OCR retry arguments are invalid" in captured.err
    assert PRIVATE not in captured.out + captured.err


@pytest.mark.parametrize("code", [124, 130])
def test_abort_codes_do_not_load_or_present_an_output_as_success(monkeypatch, capsys, code):
    _forbid_direct_recovery(monkeypatch)
    monkeypatch.setattr(cli, "run_retry_with_deadline", lambda *args, **kwargs: code)
    monkeypatch.setattr(cli, "load_supervised_report", lambda *args: pytest.fail("aborted worker output was loaded"))
    assert cli.main(ARGS) == code
    captured = capsys.readouterr()
    public = captured.out + captured.err
    assert "inspect" in public.lower()
    assert "--output" in public
    assert "OCR review:" not in public
    assert "candidates" not in captured.out.lower()
    assert PRIVATE not in public


def test_raw_keyboard_interrupt_returns_cancelled_without_loading_output(monkeypatch, capsys):
    _forbid_direct_recovery(monkeypatch)

    def cancel(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "run_retry_with_deadline", cancel)
    monkeypatch.setattr(cli, "load_supervised_report", lambda *args: pytest.fail("cancelled output was read"))
    assert cli.main(ARGS) == 130
    captured = capsys.readouterr()
    public = captured.out + captured.err
    assert "inspect" in public.lower()
    assert "OCR review:" not in public
    assert PRIVATE not in public


@pytest.mark.parametrize("stage", ["load_report", "print_report"])
def test_late_parent_cancellation_preserves_existing_output_and_returns_cancelled(
        tmp_path, monkeypatch, capsys, stage):
    report = _report()
    output = tmp_path / f"{PRIVATE}_existing_report.json"
    output.write_text(json.dumps(report), encoding="utf-8")
    existing = output.read_bytes()
    _forbid_direct_recovery(monkeypatch)
    monkeypatch.setattr(cli, "run_retry_with_deadline", lambda *args, **kwargs: 0)

    def cancel(*args, **kwargs):
        raise KeyboardInterrupt(PRIVATE)

    monkeypatch.setattr(
        cli, "load_supervised_report", cancel if stage == "load_report" else lambda path: report)
    if stage == "print_report":
        monkeypatch.setattr(cli, "_print_report", cancel)

    assert cli.main([
        "--pdf", f"{PRIVATE}.pdf", "--output", str(output), "--timeout-seconds", "10",
    ]) == 130

    captured = capsys.readouterr()
    public = captured.out + captured.err
    assert "inspect" in public.lower()
    assert "--output" in public
    assert PRIVATE not in public
    assert "OCR review:" not in public
    assert output.read_bytes() == existing


@pytest.mark.parametrize("code", [1, 2, -1, 7, 255])
def test_other_worker_exit_codes_fail_safely_without_loading_report(monkeypatch, capsys, code):
    _forbid_direct_recovery(monkeypatch)
    monkeypatch.setattr(cli, "run_retry_with_deadline", lambda *args, **kwargs: code)
    monkeypatch.setattr(cli, "load_supervised_report", lambda *args: pytest.fail("failed worker output was loaded"))
    assert cli.main(ARGS) == 2
    captured = capsys.readouterr()
    assert "OCR review:" not in captured.out
    assert PRIVATE not in captured.out + captured.err


@pytest.mark.parametrize("error", [ValueError(PRIVATE), OSError(PRIVATE), RuntimeError(PRIVATE), ImportError(PRIVATE)])
def test_supervision_exceptions_do_not_expose_details(monkeypatch, capsys, error):
    _forbid_direct_recovery(monkeypatch)

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(cli, "run_retry_with_deadline", fail)
    monkeypatch.setattr(cli, "load_supervised_report", lambda *args: pytest.fail("failed supervisor output was read"))
    assert cli.main(ARGS) == 2
    captured = capsys.readouterr()
    assert PRIVATE not in captured.out + captured.err
    assert "OCR review:" not in captured.out


@pytest.mark.parametrize(("code", "failed"), [(0, True), (3, False)])
def test_worker_code_must_match_validated_report_summary(monkeypatch, capsys, code, failed):
    _forbid_direct_recovery(monkeypatch)
    monkeypatch.setattr(cli, "run_retry_with_deadline", lambda *args, **kwargs: code)
    monkeypatch.setattr(cli, "load_supervised_report", lambda path: _report(failed=failed))
    assert cli.main(ARGS) == 2
    captured = capsys.readouterr()
    assert "OCR review:" not in captured.out
    assert PRIVATE not in captured.out + captured.err
    assert captured.err


@pytest.mark.parametrize("difference", [
    "dpi", "max_pages", "requested_pages", "min_chars", "max_pixels", "max_side", "preprocessing", "evidence",
])
def test_report_settings_must_match_the_requested_supervised_operation(monkeypatch, capsys, difference):
    report = _report()
    if difference == "dpi":
        report = _report(dpi=400)
    elif difference == "max_pages":
        report = _report(max_pages=6)
    elif difference == "requested_pages":
        report = _report(requested=(1, 2))
    elif difference == "min_chars":
        report["selection"]["min_chars"] = 41
    elif difference == "max_pixels":
        report["retry_configuration"]["max_pixels"] = 20_000_000
    elif difference == "max_side":
        report["retry_configuration"]["max_side"] = 5000
        for page in report["pages"]:
            page["candidate"]["engine"]["max_side"] = 5000
    elif difference == "preprocessing":
        report = _report(preprocessing="contrast")
    else:
        report = _report(evidence=True)
    # These reports are internally valid, but belong to different settings.
    from ocr_recovery_comparison import validate_recovery_report

    validate_recovery_report(report)
    _forbid_direct_recovery(monkeypatch)
    monkeypatch.setattr(cli, "run_retry_with_deadline", lambda *args, **kwargs: 0)
    monkeypatch.setattr(cli, "load_supervised_report", lambda path: report)
    assert cli.main(ARGS) == 2
    captured = capsys.readouterr()
    assert "requested settings and exit status" in captured.err
    assert "OCR review:" not in captured.out
    assert PRIVATE not in captured.out + captured.err


def test_report_cannot_omit_requested_evidence_binding(monkeypatch, capsys):
    _forbid_direct_recovery(monkeypatch)
    monkeypatch.setattr(cli, "run_retry_with_deadline", lambda *args, **kwargs: 0)
    monkeypatch.setattr(cli, "load_supervised_report", lambda path: _report())
    assert cli.main(ARGS + ["--evidence", f"{PRIVATE}_evidence.json"]) == 2
    captured = capsys.readouterr()
    assert "OCR review:" not in captured.out
    assert PRIVATE not in captured.out + captured.err


def test_parent_matches_deterministic_unique_requested_pages(monkeypatch):
    _forbid_direct_recovery(monkeypatch)
    monkeypatch.setattr(cli, "run_retry_with_deadline", lambda *args, **kwargs: 0)
    monkeypatch.setattr(cli, "load_supervised_report", lambda path: _report(requested=(1, 2)))
    assert cli.main(ARGS + ["--pages", "2", "1", "2"]) == 0


@pytest.mark.parametrize("error", [ValueError(PRIVATE), FileNotFoundError(PRIVATE), RuntimeError(PRIVATE)])
def test_success_code_without_valid_output_is_not_presented_as_success(monkeypatch, capsys, error):
    _forbid_direct_recovery(monkeypatch)
    monkeypatch.setattr(cli, "run_retry_with_deadline", lambda *args, **kwargs: 0)

    def fail(path):
        raise error

    monkeypatch.setattr(cli, "load_supervised_report", fail)
    assert cli.main(ARGS) == 2
    captured = capsys.readouterr()
    assert "OCR review:" not in captured.out
    assert PRIVATE not in captured.out + captured.err


def test_report_loader_reads_bounded_snapshot_and_checks_schema(monkeypatch):
    import ocr_recovery_comparison

    calls = []
    report = _report()

    def load(path, **kwargs):
        calls.append((path, kwargs))
        return report, "a" * 64

    monkeypatch.setattr(ocr_recovery_comparison, "_load", load)
    loaded = cli.load_supervised_report(Path("report.json"))
    assert loaded == report
    assert calls[0][0] == Path("report.json")
    assert calls[0][1]["limit"] == ocr_recovery_comparison.MAX_RECOVERY_BYTES


@pytest.mark.parametrize("mutation", ["kind", "schema", "summary", "candidate", "unknown_field"])
def test_actual_invalid_report_is_rejected_after_worker_claims_success(tmp_path, monkeypatch, capsys, mutation):
    report = copy.deepcopy(_report())
    if mutation == "kind":
        report["kind"] = "ocr_run_comparison"
    elif mutation == "schema":
        report["schema_version"] = 999
    elif mutation == "summary":
        report["summary"]["selected"] = 99
    elif mutation == "candidate":
        report["pages"][0]["candidate"]["text"] = "mismatch"
    else:
        report[PRIVATE] = PRIVATE
    output = tmp_path / f"{PRIVATE}.json"
    output.write_text(json.dumps(report), encoding="utf-8")
    before = output.read_bytes()
    _forbid_direct_recovery(monkeypatch)
    monkeypatch.setattr(cli, "run_retry_with_deadline", lambda *args, **kwargs: 0)

    assert cli.main(["--pdf", f"{PRIVATE}.pdf", "--output", str(output), "--timeout-seconds", "10"]) == 2

    captured = capsys.readouterr()
    assert "OCR review:" not in captured.out
    assert PRIVATE not in captured.out + captured.err
    assert output.read_bytes() == before


def test_default_branch_does_not_call_supervisor_or_report_loader(monkeypatch):
    calls = []

    def recover(*args, **kwargs):
        calls.append((args, kwargs))
        return _report()

    monkeypatch.setattr(cli, "recover_pdf", recover)
    monkeypatch.setattr(cli, "run_retry_with_deadline", lambda *args, **kwargs: pytest.fail("default used supervisor"))
    monkeypatch.setattr(cli, "load_supervised_report", lambda *args: pytest.fail("default reread its report"))
    assert cli.main(BASE_ARGS) == 0
    assert calls[0][0] == (Path(BASE_ARGS[1]), Path(BASE_ARGS[3]))
    assert calls[0][1]["requested_pages"] == ()
    assert calls[0][1]["evidence_path"] is None


def test_real_invalid_deadline_exits_before_reading_private_source():
    result = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "tools" / "retry_ocr.py"),
         *BASE_ARGS, "--timeout-seconds", "nan"],
        capture_output=True, text=True, timeout=30, cwd=PROJECT_ROOT, check=False)
    assert result.returncode == 2
    assert PRIVATE not in result.stdout + result.stderr
    assert "OCR retry arguments are invalid" in result.stderr
    assert "OCR review:" not in result.stdout


def test_real_help_explains_opt_in_deadline_and_abort_codes():
    result = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "tools" / "retry_ocr.py"), "--help"],
        capture_output=True, text=True, timeout=30, cwd=PROJECT_ROOT, check=False)
    assert result.returncode == 0, result.stderr
    assert "--timeout-seconds" in result.stdout
    assert "86400" in result.stdout
    assert "124" in result.stdout
    assert "130" in result.stdout
