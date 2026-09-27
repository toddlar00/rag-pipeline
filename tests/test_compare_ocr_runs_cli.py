"""Direct candidate-comparison CLI contracts with synthetic, model-free inputs."""

import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from ocr_recovery import ReportCleanupError
from tools import compare_ocr as cli


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PRIVATE = "PRIVATE_INPUT_TEXT_OR_PATH"
CRITICAL_PRIVATE = "PRIVATE_CRITICAL_TRANSCRIPTION"
ARGS = [
    "--baseline-recovery", f"{PRIVATE}_baseline.json",
    "--recovery", f"{PRIVATE}_retry.json",
    "--references", f"{PRIVATE}_references.json",
    "--output", f"{PRIVATE}_comparison.json",
]


def _coverage_side():
    return {
        "selected_pages": [1, 2], "failed_pages": [], "deferred_pages": [],
        "empty_candidate_pages": [], "unreferenced_selected_pages": [],
        "unreferenced_deferred_pages": [],
    }


def _report():
    return {
        "schema_version": 1, "kind": "ocr_run_comparison", "source_sha256": "a" * 64,
        "coverage": {
            "reference_pages": 2, "paired_pages": 2, "unpaired_pages": [],
            "baseline": _coverage_side(), "retry": _coverage_side(),
        },
        "paired_pages": [
            {"page_number": 1, "record_id": "page-00001"},
            {"page_number": 2, "record_id": "page-00002"},
        ],
        "comparison": {
            "summary": {
                "record_count": 2,
                "outcomes": {"improved": 1, "unchanged": 1, "regressed": 0, "mixed": 0},
                "baseline": {"character": {"error_rate": 0.12}, "word": {"error_rate": 0.24}},
                "retry": {"character": {"error_rate": 0.02}, "word": {"error_rate": 0.04}},
            },
            "regression_detected": False,
        },
        "run_settings": {
            "baseline": {"selection": {"private": PRIVATE}, "retry_configuration": {"private": PRIVATE}},
            "retry": {"selection": {"private": PRIVATE}, "retry_configuration": {"private": PRIVATE}},
        },
        "setting_differences": [],
        "confounders": {
            "evidence_binding_differs": False, "paired_engine_settings_differ": False,
            "paired_engine_difference_pages": [],
            "paired_preprocessing_libraries_differ": False,
            "paired_preprocessing_library_difference_pages": [],
        },
        "requires_attention": False,
        "canonical_extraction_modified": False,
        "acceptance": "manual_review_required",
        "private_extra": PRIVATE,
    }


def test_direct_mode_forwards_four_paths_and_does_not_use_native_baseline(monkeypatch, capsys):
    calls = []

    def compare(*args):
        calls.append(args)
        return _report()

    monkeypatch.setattr(cli, "compare_recovery_run_files", compare)
    monkeypatch.setattr(cli, "compare_recovery_file", lambda *args: pytest.fail("native-baseline mode used"))
    assert cli.main(ARGS) == 0
    assert calls == [tuple(Path(ARGS[index]) for index in (1, 3, 5, 7))]
    captured = capsys.readouterr()
    assert "baseline recovery candidate -> retry recovery candidate" in captured.out
    assert "2 reference pages; 2 paired pages" in captured.out
    assert "CER 12.00% -> 2.00%; WER 24.00% -> 4.00%" in captured.out
    assert "1 improved, 1 unchanged, 0 regressed, 0 mixed" in captured.out
    assert "manual review" in captured.out
    assert PRIVATE not in captured.out + captured.err
    assert captured.err == ""


def test_direct_adapter_lazily_forwards_to_run_comparison_core(monkeypatch):
    calls = []
    expected = _report()

    def compare(*args):
        calls.append(args)
        return expected

    monkeypatch.setitem(sys.modules, "ocr_run_comparison", SimpleNamespace(compare_recovery_run_files=compare))
    paths = tuple(Path(ARGS[index]) for index in (1, 3, 5, 7))
    assert cli.compare_recovery_run_files(*paths) is expected
    assert calls == [paths]


def test_settings_and_confounder_notices_are_content_free_and_do_not_require_attention(monkeypatch, capsys):
    report = _report()
    report["setting_differences"] = ["retry_configuration.preprocessing", "retry_configuration.dpi"]
    report["confounders"] = {
        "evidence_binding_differs": True, "paired_engine_settings_differ": True,
        "paired_engine_difference_pages": [1],
        "paired_preprocessing_libraries_differ": True,
        "paired_preprocessing_library_difference_pages": [1],
    }
    monkeypatch.setattr(cli, "compare_recovery_run_files", lambda *args: report)
    assert cli.main(ARGS) == 0
    captured = capsys.readouterr()
    lower = captured.out.lower()
    assert "setting" in lower and "differ" in lower
    assert "evidence" in lower
    assert "engine" in lower
    assert "preprocessing library versions differ on 1 paired pages" in lower
    assert PRIVATE not in captured.out + captured.err


@pytest.mark.parametrize("side", ["baseline", "retry"])
@pytest.mark.parametrize(("field", "label"), [
    ("failed_pages", "failed candidates"),
    ("deferred_pages", "deferred pages"),
    ("empty_candidate_pages", "empty candidates"),
    ("unreferenced_selected_pages", "selected pages without references"),
])
def test_direct_mode_reports_coverage_for_each_side(monkeypatch, capsys, side, field, label):
    report = _report()
    report["coverage"][side][field] = [3]
    report["requires_attention"] = True
    monkeypatch.setattr(cli, "compare_recovery_run_files", lambda *args: report)
    assert cli.main(ARGS) == 3
    captured = capsys.readouterr()
    assert f"Coverage warning: 1 {side} {label}." in captured.out
    assert PRIVATE not in captured.out + captured.err


def test_direct_mode_no_pairs_is_unscored_and_requires_attention(monkeypatch, capsys):
    report = _report()
    report["coverage"]["paired_pages"] = 0
    report["coverage"]["unpaired_pages"] = [
        {"page_number": 1, "baseline_status": "candidate_available", "retry_status": "retry_failed"},
        {"page_number": 2, "baseline_status": "not_selected", "retry_status": "candidate_available"},
    ]
    report["paired_pages"] = []
    report["comparison"] = None
    report["requires_attention"] = True
    monkeypatch.setattr(cli, "compare_recovery_run_files", lambda *args: report)
    assert cli.main(ARGS) == 3
    captured = capsys.readouterr()
    assert "No paired pages to score" in captured.out
    assert "2 unpaired reference pages" in captured.out
    assert "CER" not in captured.out
    assert PRIVATE not in captured.out + captured.err


def test_direct_mode_empty_reference_rates_remain_unavailable(monkeypatch, capsys):
    report = _report()
    for side in ("baseline", "retry"):
        for unit in ("character", "word"):
            report["comparison"]["summary"][side][unit]["error_rate"] = None
    monkeypatch.setattr(cli, "compare_recovery_run_files", lambda *args: report)
    assert cli.main(ARGS) == 0
    captured = capsys.readouterr()
    assert captured.out.count("n/a (empty reference)") == 4
    assert "0.00%" not in captured.out


@pytest.mark.parametrize("error", [
    ValueError(PRIVATE), RuntimeError(PRIVATE), OSError(PRIVATE), ImportError(PRIVATE),
    FileExistsError(PRIVATE), ReportCleanupError(PRIVATE),
])
def test_direct_mode_errors_are_private_and_distinguish_publication_state(monkeypatch, capsys, error):
    def fail(*args):
        raise error

    monkeypatch.setattr(cli, "compare_recovery_run_files", fail)
    assert cli.main(ARGS) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert PRIVATE not in captured.err
    assert "docs/ocr-accuracy.md" in captured.err
    if isinstance(error, ReportCleanupError):
        assert "was created, but temporary-file cleanup failed" in captured.err
    elif isinstance(error, FileExistsError):
        assert "Choose a new --output path" in captured.err
    else:
        assert "could not publish a report" in captured.err


def test_direct_mode_preserves_cancellation(monkeypatch):
    def cancel(*args):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "compare_recovery_run_files", cancel)
    with pytest.raises(KeyboardInterrupt):
        cli.main(ARGS)


def test_direct_mode_help_and_module_import_do_not_load_comparison_or_ocr_dependencies():
    result = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "tools" / "compare_ocr.py"), "--help"],
        capture_output=True, text=True, timeout=30, cwd=PROJECT_ROOT, check=False)
    assert result.returncode == 0, result.stderr
    assert "--baseline-recovery" in result.stdout
    assert "candidate" in result.stdout.lower()
    result = subprocess.run(
        [sys.executable, "-c", "import sys; from tools import compare_ocr; "
         "assert not ({'ocr_run_comparison', 'ocr_recovery_comparison', 'ocr_recovery', "
         "'rag', 'torch', 'numpy', 'docling', 'rapidocr', 'pymupdf'} & set(sys.modules))"],
        capture_output=True, text=True, timeout=30, cwd=PROJECT_ROOT, check=False)
    assert result.returncode == 0, result.stderr


def _contrast_metadata():
    return {
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


def _fixture_payloads():
    folder = PROJECT_ROOT / "evaluation" / "ocr"
    retry = json.loads((folder / "comparison-recovery.json").read_bytes())
    references = json.loads((folder / "comparison-references.json").read_bytes())
    baseline = copy.deepcopy(retry)
    for index, page in enumerate(baseline["pages"]):
        text = page["original_text"]
        page["candidate"]["text"] = text
        page["candidate"]["lines"][0]["text"] = text
        # Native extraction intentionally differs from both compared candidates.
        page["original_text"] = f"{PRIVATE}_{index}"
    for index, page in enumerate(retry["pages"]):
        page["original_text"] = f"{PRIVATE}_{index}"
        page["candidate"]["raster"]["coordinate_system"] = "preprocessed_image_pixels"
        page["candidate"]["preprocessing"] = _contrast_metadata()
    retry["schema_version"] = 2
    retry["retry_configuration"]["preprocessing"] = "contrast"
    for run in (baseline, retry):
        for page in run["pages"]:
            page["candidate"]["text"] += " " + CRITICAL_PRIVATE
            page["candidate"]["lines"][0]["text"] += " " + CRITICAL_PRIVATE
    for page in references["pages"]:
        page["reference"] += " " + CRITICAL_PRIVATE
        page["critical_tokens"].append(CRITICAL_PRIVATE)
    return baseline, retry, references


def _write_inputs(tmp_path, baseline, retry, references):
    paths = []
    for name, payload in (("baseline", baseline), ("retry", retry), ("references", references)):
        path = tmp_path / f"{PRIVATE}_{name}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        paths.append(path)
    return (*paths, tmp_path / f"{PRIVATE}_comparison.json")


def _run_cli(paths):
    baseline, retry, references, output = paths
    return subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "tools" / "compare_ocr.py"),
         "--baseline-recovery", str(baseline), "--recovery", str(retry),
         "--references", str(references), "--output", str(output)],
        capture_output=True, text=True, timeout=30, cwd=PROJECT_ROOT, check=False)


def _assert_content_free(result, paths, baseline, retry, references):
    public = result.stdout + result.stderr
    assert PRIVATE not in public
    assert CRITICAL_PRIVATE not in public
    for path in paths:
        assert str(path) not in public
    for run in (baseline, retry):
        for page in run["pages"]:
            if page["original_text"] is not None:
                assert page["original_text"] not in public
            if page["candidate"] is not None:
                assert page["candidate"]["text"] not in public
                assert page["candidate"]["engine"]["version"] not in public
    for page in references["pages"]:
        assert page["reference"] not in public


def test_real_cli_compares_v1_to_v2_candidates_and_detects_page_regression(tmp_path):
    baseline, retry, references = _fixture_payloads()
    paths = _write_inputs(tmp_path, baseline, retry, references)
    originals = [path.read_bytes() for path in paths[:3]]
    result = _run_cli(paths)

    assert result.returncode == 3, result.stderr
    assert result.stderr == ""
    assert "baseline recovery candidate -> retry recovery candidate" in result.stdout
    assert "1 improved, 0 unchanged, 1 regressed, 0 mixed" in result.stdout
    assert "regression detected" in result.stdout
    _assert_content_free(result, paths, baseline, retry, references)
    published = paths[3].read_bytes()
    report = json.loads(published)
    assert report["kind"] == "ocr_run_comparison"
    assert report["coverage"]["paired_pages"] == 2
    assert report["requires_attention"] is True
    assert report["comparison"]["regression_detected"] is True
    summary = report["comparison"]["summary"]
    # The baseline is the first run's candidate, not either run's native canary.
    assert summary["baseline"]["character"]["edit_distance"] == 4
    assert summary["retry"]["character"]["edit_distance"] == 1
    assert summary["retry"]["character"]["error_rate"] < summary["baseline"]["character"]["error_rate"]
    assert report["comparison"]["records"][1]["outcome"] == "regressed"
    assert report["inputs"] == dict(zip(
        ("baseline_recovery_sha256", "retry_recovery_sha256", "references_sha256"),
        (hashlib.sha256(raw).hexdigest() for raw in originals),
    ))
    serialized = published.decode("utf-8")
    assert PRIVATE not in serialized
    assert CRITICAL_PRIVATE not in serialized
    for page in references["pages"]:
        assert page["reference"] not in serialized
        for critical in page["critical_tokens"]:
            assert json.dumps(critical) not in serialized
    assert "retry_configuration.preprocessing" in report["setting_differences"]

    repeated = _run_cli(paths)
    assert repeated.returncode == 2
    assert "Choose a new --output path" in repeated.stderr
    assert paths[3].read_bytes() == published
    assert [path.read_bytes() for path in paths[:3]] == originals
    _assert_content_free(repeated, paths, baseline, retry, references)


def test_real_cli_failed_and_missing_candidates_do_not_become_perfect_empty_scores(tmp_path):
    baseline, retry, references = _fixture_payloads()
    retry["selection"]["requested_pages"] = [1]
    retry["pages"] = retry["pages"][:1]
    retry["pages"][0].update(status="retry_failed", candidate=None, error_code="retry_runtime_failed")
    retry["summary"].update(selected=1, failed=1, review_required=0)
    paths = _write_inputs(tmp_path, baseline, retry, references)
    result = _run_cli(paths)

    assert result.returncode == 3, result.stderr
    assert "No paired pages to score" in result.stdout
    assert "CER" not in result.stdout
    assert "2 unpaired reference pages" in result.stdout
    report = json.loads(paths[3].read_bytes())
    assert report["comparison"] is None
    assert report["coverage"]["paired_pages"] == 0
    assert report["coverage"]["unpaired_pages"] == [
        {"page_number": 1, "baseline_status": "candidate_available", "retry_status": "retry_failed"},
        {"page_number": 2, "baseline_status": "candidate_available", "retry_status": "not_selected"},
    ]
    assert report["coverage"]["retry"]["failed_pages"] == [1]
    assert report["requires_attention"] is True
    _assert_content_free(result, paths, baseline, retry, references)


def test_real_cli_configuration_difference_alone_does_not_require_attention(tmp_path):
    baseline, retry, references = _fixture_payloads()
    for before, after in zip(baseline["pages"], retry["pages"]):
        after["candidate"]["text"] = before["candidate"]["text"]
        after["candidate"]["lines"][0]["text"] = before["candidate"]["lines"][0]["text"]
    paths = _write_inputs(tmp_path, baseline, retry, references)
    result = _run_cli(paths)

    assert result.returncode == 0, result.stderr
    report = json.loads(paths[3].read_bytes())
    assert report["setting_differences"] == ["retry_configuration.preprocessing"]
    assert report["comparison"]["summary"]["outcomes"]["unchanged"] == 2
    assert report["requires_attention"] is False
    assert "setting" in result.stdout.lower()
    _assert_content_free(result, paths, baseline, retry, references)


def test_real_cli_rejects_mismatched_run_source_hash_without_publication(tmp_path):
    baseline, retry, references = _fixture_payloads()
    retry["source_sha256"] = "f" * 64
    paths = _write_inputs(tmp_path, baseline, retry, references)
    result = _run_cli(paths)

    assert result.returncode == 2
    assert not paths[3].exists()
    assert result.stdout == ""
    assert "matching source hashes" in result.stderr
    _assert_content_free(result, paths, baseline, retry, references)


def test_real_cli_committed_example_compares_candidates_with_unavailable_originals(tmp_path):
    folder = PROJECT_ROOT / "evaluation" / "ocr"
    paths = (
        folder / "comparison-baseline-recovery.json",
        folder / "comparison-recovery.json",
        folder / "comparison-references.json",
        tmp_path / f"{PRIVATE}_committed_example.json",
    )
    baseline, retry, references = [json.loads(path.read_bytes()) for path in paths[:3]]
    assert all(page["original_text"] is None for page in baseline["pages"])

    result = _run_cli(paths)

    assert result.returncode == 3, result.stderr
    assert "2 reference pages; 2 paired pages" in result.stdout
    assert "1 improved, 0 unchanged, 1 regressed, 0 mixed" in result.stdout
    report = json.loads(paths[3].read_bytes())
    assert report["coverage"]["paired_pages"] == 2
    assert report["coverage"]["unpaired_pages"] == []
    summary = report["comparison"]["summary"]
    assert summary["baseline"]["character"]["edit_distance"] == 4
    assert summary["retry"]["character"]["edit_distance"] == 1
    assert summary["retry"]["character"]["error_rate"] < summary["baseline"]["character"]["error_rate"]
    assert summary["outcomes"]["regressed"] == 1
    assert report["comparison"]["regression_detected"] is True
    assert report["requires_attention"] is True
    _assert_content_free(result, paths, baseline, retry, references)
