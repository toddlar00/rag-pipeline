import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

import pytest

from ocr_recovery import ReportCleanupError
from tools import compare_ocr as cli


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PRIVATE_VALUE = "PRIVATE_PATH_OR_SOURCE_TEXT"
ARGS = [
    "--recovery", f"{PRIVATE_VALUE}_recovery.json",
    "--references", f"{PRIVATE_VALUE}_references.json",
    "--output", f"{PRIVATE_VALUE}_output.json",
]


def _report():
    return {
        "kind": "ocr_recovery_comparison",
        "schema_version": 1,
        "source_sha256": "a" * 64,
        "page_count": 2,
        "coverage": {
            "reference_pages": 2,
            "paired_pages": 2,
            "unpaired_pages": [],
            "unreferenced_selected_pages": [],
            "deferred_pages": [],
            "empty_candidate_pages": [],
        },
        "comparison": {
            "summary": {
                "record_count": 2,
                "outcomes": {
                    "improved": 1, "unchanged": 1, "regressed": 0, "mixed": 0,
                },
                "baseline": {
                    "character": {"error_rate": 0.125},
                    "word": {"error_rate": 0.25},
                },
                "retry": {
                    "character": {"error_rate": 0.025},
                    "word": {"error_rate": 0.05},
                },
            },
            "regression_detected": False,
        },
        "requires_attention": False,
        "canonical_extraction_modified": False,
        "acceptance": "manual_review_required",
        "private_extra": PRIVATE_VALUE,
    }


def test_cli_forwards_paths_and_prints_only_summary(monkeypatch, capsys):
    seen = []

    def compare(*args):
        seen.append(args)
        return _report()

    monkeypatch.setattr(cli, "compare_recovery_file", compare)
    assert cli.main(ARGS) == 0
    assert seen == [(Path(ARGS[1]), Path(ARGS[3]), Path(ARGS[5]))]
    captured = capsys.readouterr()
    assert "2 reference pages; 2 paired pages" in captured.out
    assert "CER 12.50% -> 2.50%; WER 25.00% -> 5.00%" in captured.out
    assert "1 improved, 1 unchanged, 0 regressed, 0 mixed" in captured.out
    assert "manual review" in captured.out
    assert "Original extraction unchanged" in captured.out
    assert PRIVATE_VALUE not in captured.out + captured.err
    assert captured.err == ""


def test_lazy_adapter_forwards_to_core(monkeypatch):
    from types import SimpleNamespace

    seen = []
    report = _report()

    def compare(*args):
        seen.append(args)
        return report

    monkeypatch.setitem(
        sys.modules, "ocr_recovery_comparison",
        SimpleNamespace(compare_recovery_file=compare))
    paths = (Path("recovery.json"), Path("references.json"), Path("output.json"))
    assert cli.compare_recovery_file(*paths) is report
    assert seen == [paths]


def test_cli_empty_reference_rates_are_not_zero(monkeypatch, capsys):
    report = _report()
    for variant in ("baseline", "retry"):
        for unit in ("character", "word"):
            report["comparison"]["summary"][variant][unit]["error_rate"] = None
    monkeypatch.setattr(cli, "compare_recovery_file", lambda *args: report)
    assert cli.main(ARGS) == 0
    captured = capsys.readouterr()
    assert captured.out.count("n/a (empty reference)") == 4
    assert "0.00%" not in captured.out
    assert PRIVATE_VALUE not in captured.out + captured.err


@pytest.mark.parametrize(("field", "label"), [
    ("unpaired_pages", "unpaired reference pages"),
    ("unreferenced_selected_pages", "selected pages without references"),
    ("deferred_pages", "deferred pages"),
    ("empty_candidate_pages", "empty retry candidates"),
])
def test_cli_coverage_attention_is_counted_without_details(
        monkeypatch, capsys, field, label):
    report = _report()
    report["coverage"][field] = [
        {"page_number": 3, "reasons": [PRIVATE_VALUE],
         "baseline_available": False, "retry_available": False}
        if field == "unpaired_pages" else 3,
    ]
    report["requires_attention"] = True
    monkeypatch.setattr(cli, "compare_recovery_file", lambda *args: report)
    assert cli.main(ARGS) == 3
    captured = capsys.readouterr()
    assert f"Coverage warning: 1 {label}." in captured.out
    assert PRIVATE_VALUE not in captured.out + captured.err


def test_cli_no_paired_pages_needs_attention(monkeypatch, capsys):
    report = _report()
    report["coverage"]["paired_pages"] = 0
    report["comparison"] = None
    report["requires_attention"] = True
    monkeypatch.setattr(cli, "compare_recovery_file", lambda *args: report)
    assert cli.main(ARGS) == 3
    captured = capsys.readouterr()
    assert "No paired pages to score" in captured.out
    assert "CER" not in captured.out
    assert PRIVATE_VALUE not in captured.out + captured.err


def test_cli_regression_needs_attention(monkeypatch, capsys):
    report = _report()
    report["comparison"]["regression_detected"] = True
    report["comparison"]["summary"]["outcomes"] = {
        "improved": 0, "unchanged": 0, "regressed": 1, "mixed": 1,
    }
    report["requires_attention"] = True
    monkeypatch.setattr(cli, "compare_recovery_file", lambda *args: report)
    assert cli.main(ARGS) == 3
    captured = capsys.readouterr()
    assert "1 regressed, 1 mixed" in captured.out
    assert "regression detected" in captured.out
    assert PRIVATE_VALUE not in captured.out + captured.err


@pytest.mark.parametrize("error", [
    ValueError(f"source hash mismatch {PRIVATE_VALUE}"),
    ValueError(f"reference schema invalid {PRIVATE_VALUE}"),
    OSError(PRIVATE_VALUE),
    RuntimeError(PRIVATE_VALUE),
    ImportError(PRIVATE_VALUE),
])
def test_cli_input_and_publication_errors_are_private(monkeypatch, capsys, error):
    def fail(*args):
        raise error

    monkeypatch.setattr(cli, "compare_recovery_file", fail)
    assert cli.main(ARGS) == 2
    captured = capsys.readouterr()
    assert "could not publish a report" in captured.err
    assert "matching source hashes" in captured.err
    assert "docs/ocr-accuracy.md" in captured.err
    assert PRIVATE_VALUE not in captured.out + captured.err
    assert captured.out == ""


@pytest.mark.parametrize(("error", "expected"), [
    (FileExistsError(PRIVATE_VALUE), "Choose a new --output path"),
    (ReportCleanupError(PRIVATE_VALUE),
     "was created, but temporary-file cleanup failed"),
])
def test_cli_distinguishes_output_conflict_and_post_commit_cleanup(
        monkeypatch, capsys, error, expected):
    def fail(*args):
        raise error

    monkeypatch.setattr(cli, "compare_recovery_file", fail)
    assert cli.main(ARGS) == 2
    captured = capsys.readouterr()
    assert expected in captured.err
    assert "docs/ocr-accuracy.md" in captured.err
    assert PRIVATE_VALUE not in captured.out + captured.err


def test_cli_preserves_cancellation(monkeypatch):
    def cancel(*args):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "compare_recovery_file", cancel)
    with pytest.raises(KeyboardInterrupt):
        cli.main(ARGS)


@pytest.mark.parametrize("args", [[], ARGS + [f"--{PRIVATE_VALUE}"]])
def test_cli_argument_errors_do_not_echo_values(capsys, args):
    with pytest.raises(SystemExit) as exc:
        cli.main(args)
    assert exc.value.code == 2
    captured = capsys.readouterr()
    assert "arguments are invalid" in captured.err
    assert PRIVATE_VALUE not in captured.out + captured.err


def test_real_help_is_available_without_loading_ocr_dependencies():
    result = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "tools" / "compare_ocr.py"), "--help"],
        capture_output=True, text=True, timeout=30, cwd=PROJECT_ROOT, check=False)
    assert result.returncode == 0, result.stderr
    for option in ("--recovery", "--references", "--output"):
        assert option in result.stdout
    assert "must not exist" in result.stdout
    result = subprocess.run(
        [sys.executable, "-c",
         "import sys; from tools import compare_ocr; "
         "assert not ({'ocr_recovery_comparison', 'ocr_recovery', 'rag', "
         "'torch', 'numpy', 'docling', 'rapidocr', 'pymupdf'} & set(sys.modules))"],
        capture_output=True, text=True, timeout=30, cwd=PROJECT_ROOT, check=False)
    assert result.returncode == 0, result.stderr


def _run_real_comparison(recovery: Path, references: Path, output: Path):
    return subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "tools" / "compare_ocr.py"),
         "--recovery", str(recovery), "--references", str(references),
         "--output", str(output)],
        capture_output=True, text=True, timeout=30, cwd=PROJECT_ROOT, check=False)


def _assert_private_comparison_output(result, recovery, references, paths):
    public_text = result.stdout + result.stderr
    for path in paths:
        assert str(path) not in public_text
        assert path.name not in public_text
    for page in recovery["pages"]:
        for transcription in (page["original_text"], page["candidate"]["text"]):
            assert transcription not in public_text
    for page in references["pages"]:
        assert page["reference"] not in public_text
        for critical in page.get("critical_tokens", []):
            assert re.search(r"(?<!\w)" + re.escape(critical) + r"(?!\w)", public_text) is None


def test_real_cli_compares_committed_fixture_and_preserves_existing_output(tmp_path):
    recovery_path = PROJECT_ROOT / "evaluation" / "ocr" / "comparison-recovery.json"
    references_path = PROJECT_ROOT / "evaluation" / "ocr" / "comparison-references.json"
    output = tmp_path / f"{PRIVATE_VALUE}_comparison.json"
    recovery_raw, references_raw = recovery_path.read_bytes(), references_path.read_bytes()
    recovery, references = json.loads(recovery_raw), json.loads(references_raw)

    result = _run_real_comparison(recovery_path, references_path, output)

    assert result.returncode == 3, result.stderr
    assert result.stderr == ""
    assert "2 reference pages; 2 paired pages" in result.stdout
    assert "1 improved, 0 unchanged, 1 regressed, 0 mixed" in result.stdout
    assert "regression detected" in result.stdout
    _assert_private_comparison_output(
        result, recovery, references, (recovery_path, references_path, output))
    published_bytes = output.read_bytes()
    report = json.loads(published_bytes)
    assert report["inputs"] == {
        "recovery_sha256": hashlib.sha256(recovery_raw).hexdigest(),
        "references_sha256": hashlib.sha256(references_raw).hexdigest(),
    }
    assert report["source_sha256"] == recovery["source_sha256"] == references["source_sha256"]
    assert report["requires_attention"] is True
    assert report["canonical_extraction_modified"] is False
    assert report["acceptance"] == "manual_review_required"
    comparison = report["comparison"]
    summary = comparison["summary"]
    assert summary["retry"]["character"]["error_rate"] < summary["baseline"]["character"]["error_rate"]
    assert comparison["regression_detected"] is True
    assert [record["outcome"] for record in comparison["records"]] == ["improved", "regressed"]
    assert comparison["records"][1]["regression_detected"] is True
    serialized = published_bytes.decode("utf-8")
    for page in recovery["pages"]:
        assert page["original_text"] not in serialized
        assert page["candidate"]["text"] not in serialized
    for page in references["pages"]:
        assert page["reference"] not in serialized
        for critical in page.get("critical_tokens", []):
            assert json.dumps(critical) not in serialized
    for path in (recovery_path, references_path, output):
        assert path.name not in serialized

    repeated = _run_real_comparison(recovery_path, references_path, output)

    assert repeated.returncode == 2
    assert repeated.stdout == ""
    assert "Choose a new --output path" in repeated.stderr
    assert output.read_bytes() == published_bytes
    assert recovery_path.read_bytes() == recovery_raw
    assert references_path.read_bytes() == references_raw
    _assert_private_comparison_output(
        repeated, recovery, references, (recovery_path, references_path, output))


def test_real_cli_rejects_references_for_different_source_without_output(tmp_path):
    recovery_path = PROJECT_ROOT / "evaluation" / "ocr" / "comparison-recovery.json"
    committed_references = PROJECT_ROOT / "evaluation" / "ocr" / "comparison-references.json"
    recovery = json.loads(recovery_path.read_bytes())
    references = json.loads(committed_references.read_bytes())
    references["source_sha256"] = "f" * 64
    references["pages"][0]["reference"] = PRIVATE_VALUE
    references["pages"][0]["critical_tokens"] = [PRIVATE_VALUE]
    references_path = tmp_path / f"{PRIVATE_VALUE}_mismatched_references.json"
    references_path.write_text(json.dumps(references), encoding="utf-8")
    references_raw = references_path.read_bytes()
    output = tmp_path / f"{PRIVATE_VALUE}_unpublished.json"

    result = _run_real_comparison(recovery_path, references_path, output)

    assert result.returncode == 2
    assert result.stdout == ""
    assert "matching source hashes" in result.stderr
    assert "docs/ocr-accuracy.md" in result.stderr
    assert not output.exists()
    assert references_path.read_bytes() == references_raw
    _assert_private_comparison_output(
        result, recovery, references, (recovery_path, references_path, output))
