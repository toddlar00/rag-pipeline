"""Sanitized CLI contracts for explicit, saved-candidate layout reviews."""

from __future__ import annotations

import copy
import builtins
from pathlib import Path
import subprocess
import sys
import textwrap
from types import SimpleNamespace

import pytest

from ocr_recovery import ReportCleanupError
from tools import reorder_ocr as cli


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PRIVATE = "PRIVATE_SOURCE_PATH_OR_TEXT_\u03b4"
ARGS = ["--recovery", PRIVATE + ".json", "--plan", PRIVATE + "-plan.json",
        "--output", PRIVATE + "-review.json"]


def _report(*, attention=False, comparison=True):
    return {
        "summary": {"review_pages": 5, "planned_pages": 3, "reordered": 1,
                    "unchanged": 2, "abstained": 1, "unavailable": 1},
        "requires_attention": attention,
        "coverage": {"deferred_pages": [], "empty_candidate_pages": [], "failed_pages": []},
        "reference_coverage": {
            "reference_pages": 5, "paired_pages": 5, "unpaired_pages": [],
            "unreferenced_selected_pages": [], "unreferenced_planned_pages": [],
        } if comparison else None,
        "comparison": {
            "summary": {
                "baseline": {"character": {"error_rate": 0.12}, "word": {"error_rate": 0.24}},
                "retry": {"character": {"error_rate": 0.02}, "word": {"error_rate": 0.04}},
            },
            "regression_detected": False, "private": PRIVATE,
        } if comparison else None,
        "pages": [{"original_text": PRIVATE, "proposed_text": PRIVATE}],
        "private": PRIVATE,
    }


@pytest.mark.parametrize("with_references", [False, True])
def test_cli_forwards_only_paths_and_prints_counts_not_text(monkeypatch, capsys, with_references):
    report = _report(comparison=with_references)
    original = copy.deepcopy(report)
    calls = []

    def review(*args, **kwargs):
        calls.append((args, kwargs))
        return report

    monkeypatch.setattr(cli, "review_layout_files", review)
    arguments = ARGS + (["--references", PRIVATE + "-refs.json"] if with_references else [])
    assert cli.main(arguments) == (0 if with_references else 3)
    assert calls == [((Path(ARGS[1]), Path(ARGS[3]), Path(ARGS[5])), {
        "reference_path": Path(PRIVATE + "-refs.json") if with_references else None,
    })]
    assert report == original
    output = capsys.readouterr()
    assert "5 review pages; 3 planned; 1 reordered, 2 unchanged, 1 abstained, 1 unavailable" in output.out
    assert "Explicit plans are not table classification" in output.out
    assert "Manual review is required" in output.out
    assert "Original OCR and canonical extraction unchanged" in output.out
    assert PRIVATE not in output.out + output.err
    assert output.err == ""
    if with_references:
        assert "CER 12.00% -> 2.00%; WER 24.00% -> 4.00%" in output.out
    else:
        assert "accuracy is unverified" in output.out
        assert "CER" not in output.out


def test_cli_helper_lazily_forwards_to_the_io_layer(monkeypatch):
    expected, seen = _report(), []

    def review(*args, **kwargs):
        seen.append((args, kwargs))
        return expected

    monkeypatch.setitem(sys.modules, "ocr_layout_io", SimpleNamespace(review_layout_files=review))
    paths = tuple(Path(value) for value in ("recovery", "plan", "output", "references"))
    assert cli.review_layout_files(*paths[:3], reference_path=paths[3]) is expected
    assert seen == [(paths[:3], {"reference_path": paths[3]})]


@pytest.mark.parametrize(("references", "attention", "expected"), [
    (False, False, 3), (False, True, 3), (True, False, 0), (True, True, 3),
])
def test_reference_and_attention_exit_contract(monkeypatch, references, attention, expected):
    monkeypatch.setattr(cli, "review_layout_files", lambda *_args, **_kwargs: _report(
        attention=attention, comparison=references))
    assert cli.main(ARGS + (["--references", "references.json"] if references else [])) == expected


def test_no_paired_reference_pages_are_reported_without_zero_scores(monkeypatch, capsys):
    report = _report(attention=True, comparison=False)
    report["reference_coverage"] = {
        "reference_pages": 1, "paired_pages": 0,
        "unpaired_pages": [{"page_number": 1, "status": PRIVATE}],
        "unreferenced_selected_pages": [], "unreferenced_planned_pages": [],
    }
    monkeypatch.setattr(cli, "review_layout_files", lambda *_args, **_kwargs: report)
    assert cli.main(ARGS + ["--references", "references.json"]) == 3
    output = capsys.readouterr()
    assert "No paired pages to score" in output.out
    assert "Reference coverage: 0 paired of 1 reference pages" in output.out
    assert "Coverage warning: 1 unpaired reference pages" in output.out
    assert PRIVATE not in output.out + output.err
    assert "CER" not in output.out


@pytest.mark.parametrize(("section", "field", "label"), [
    ("reference_coverage", "unpaired_pages", "unpaired reference pages"),
    ("reference_coverage", "unreferenced_selected_pages", "selected pages without references"),
    ("reference_coverage", "unreferenced_planned_pages", "planned pages without references"),
    ("coverage", "deferred_pages", "deferred pages"),
    ("coverage", "empty_candidate_pages", "empty candidate pages"),
    ("coverage", "failed_pages", "failed candidate pages"),
])
def test_coverage_gaps_print_counts_not_arbitrary_reason_values(
        monkeypatch, capsys, section, field, label):
    report = _report(attention=True)
    report[section][field] = [{"page_number": 1, "reason": PRIVATE}]
    monkeypatch.setattr(cli, "review_layout_files", lambda *_args, **_kwargs: report)
    assert cli.main(ARGS + ["--references", "references.json"]) == 3
    output = capsys.readouterr()
    assert f"Coverage warning: 1 {label}." in output.out
    assert "Attention: inspect coverage" in output.out
    assert PRIVATE not in output.out + output.err


def test_abstention_guidance_is_static_and_does_not_render_plan_or_page_reasons(monkeypatch, capsys):
    report = _report(attention=True, comparison=False)
    report["pages"][0].update(reason=PRIVATE, plan={"private": PRIVATE})
    monkeypatch.setattr(cli, "review_layout_files", lambda *_args, **_kwargs: report)
    assert cli.main(ARGS) == 3
    output = capsys.readouterr()
    assert "Abstention guidance: inspect the explicit plan and line geometry" in output.out
    assert PRIVATE not in output.out + output.err


def test_empty_reference_rates_and_regression_warning_remain_honest(monkeypatch, capsys):
    report = _report(attention=True)
    report["comparison"]["regression_detected"] = True
    report["comparison"]["summary"]["baseline"]["character"]["error_rate"] = None
    report["comparison"]["summary"]["retry"]["word"]["error_rate"] = None
    monkeypatch.setattr(cli, "review_layout_files", lambda *_args, **_kwargs: report)
    assert cli.main(ARGS + ["--references", "references.json"]) == 3
    output = capsys.readouterr()
    assert "CER n/a (empty reference) -> 2.00%" in output.out
    assert "WER 24.00% -> n/a (empty reference)" in output.out
    assert "regression detected" in output.out
    assert PRIVATE not in output.out + output.err


@pytest.mark.parametrize("arguments", [
    [], ["--recovery", PRIVATE], ARGS + [PRIVATE], ARGS + ["--references"],
    ARGS + ["--" + PRIVATE], ARGS + ["--pdf", PRIVATE],
    ARGS + ["--preprocess", "contrast"], ARGS + ["--ref", PRIVATE],
    ARGS + ["--plan"],
])
def test_bad_arguments_are_private_and_do_not_invoke_io(monkeypatch, capsys, arguments):
    monkeypatch.setattr(cli, "review_layout_files", lambda *_args, **_kwargs: pytest.fail("IO ran"))
    monkeypatch.setattr(sys, "argv", [PRIVATE + "/program.py"])
    with pytest.raises(SystemExit) as captured:
        cli.main(arguments)
    assert captured.value.code == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert "usage: reorder_ocr.py" in output.err
    assert "OCR layout arguments are invalid" in output.err
    assert "--help" in output.err
    assert PRIVATE not in output.err


@pytest.mark.parametrize("exception", [
    ValueError, OSError, RuntimeError, ImportError, PermissionError, ModuleNotFoundError,
])
def test_expected_failure_details_never_escape(monkeypatch, capsys, exception):
    def fail(*_args, **_kwargs):
        raise exception(PRIVATE)

    monkeypatch.setattr(cli, "review_layout_files", fail)
    assert cli.main(ARGS) == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert "could not complete" in output.err
    assert "may already exist" in output.err
    assert PRIVATE not in output.err
    assert "Traceback" not in output.err


@pytest.mark.parametrize(("exception", "expected"), [(ImportError, 2), (KeyboardInterrupt, 130)])
def test_dependency_failure_before_review_is_sanitized(monkeypatch, capsys, exception, expected):
    actual_import = builtins.__import__

    def fail_dependency(name, *args, **kwargs):
        if name == "ocr_recovery":
            raise exception(PRIVATE)
        return actual_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fail_dependency)
    monkeypatch.setattr(cli, "review_layout_files", lambda *_args, **_kwargs: pytest.fail("IO ran"))
    assert cli.main(ARGS) == expected
    output = capsys.readouterr()
    assert output.out == ""
    assert PRIVATE not in output.err
    assert "Traceback" not in output.err
    assert ("could not complete" if expected == 2 else "was cancelled") in output.err


@pytest.mark.parametrize(("exception", "expected", "guidance"), [
    (FileExistsError, 2, "output exists"),
    (ReportCleanupError, 2, "was created, but temporary-file cleanup failed"),
    (KeyboardInterrupt, 130, "was cancelled"),
])
def test_special_terminal_outcomes_are_distinct_and_private(
        monkeypatch, capsys, exception, expected, guidance):
    def fail(*_args, **_kwargs):
        raise exception(PRIVATE)

    monkeypatch.setattr(cli, "review_layout_files", fail)
    assert cli.main(ARGS) == expected
    output = capsys.readouterr()
    assert output.out == ""
    assert guidance in output.err
    assert "--output" in output.err
    assert PRIVATE not in output.err


def test_late_cancellation_preserves_completed_artifact_and_does_not_claim_absence(
        monkeypatch, tmp_path, capsys):
    output = tmp_path / "review.json"

    def publish_then_cancel(_recovery, _plan, output_path, **_kwargs):
        output_path.write_bytes(b"synthetic completed review")
        raise KeyboardInterrupt(PRIVATE)

    monkeypatch.setattr(cli, "review_layout_files", publish_then_cancel)
    assert cli.main(["--recovery", "r.json", "--plan", "p.json", "--output", str(output)]) == 130
    assert output.read_bytes() == b"synthetic completed review"
    captured = capsys.readouterr()
    assert "completed report may already exist" in captured.err
    assert PRIVATE not in captured.out + captured.err


def test_help_is_lazy_and_does_not_import_json_runtime_or_ocr_dependencies():
    program = textwrap.dedent("""\
        import sys
        from tools import reorder_ocr

        try:
            reorder_ocr.main(['--help'])
        except SystemExit as error:
            assert error.code == 0
        else:
            raise AssertionError('help did not exit')
        forbidden = {
            'ocr_layout_io', 'ocr_layout', 'ocr_recovery', 'ocr_recovery_runtime',
            'rag', 'pymupdf', 'fitz', 'cv2', 'numpy', 'rapidocr', 'onnxruntime',
        }
        assert not forbidden.intersection(sys.modules)
        """)
    result = subprocess.run(
        [sys.executable, "-c", program], cwd=PROJECT_ROOT,
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr
    for option in ("--recovery", "--plan", "--output", "--references"):
        assert option in result.stdout
    assert result.stderr == ""


def test_real_argument_error_is_sanitized():
    result = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "tools" / "reorder_ocr.py"), *ARGS, "--" + PRIVATE],
        cwd=PROJECT_ROOT, stdin=subprocess.DEVNULL, capture_output=True, text=True,
        timeout=30, check=False)
    assert result.returncode == 2
    assert result.stdout == ""
    assert "OCR layout arguments are invalid" in result.stderr
    assert PRIVATE not in result.stderr
    assert "Traceback" not in result.stderr
