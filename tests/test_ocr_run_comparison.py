"""Synthetic paired-run OCR accuracy, coverage, and publication tests."""

from __future__ import annotations

import copy
import hashlib
import itertools
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import ocr_comparison
import ocr_evaluation
import ocr_recovery
import ocr_run_comparison as runs
import storage_policy


DIGEST = "a" * 64
HEALTHY = "This synthetic paragraph contains enough text to avoid automatic OCR selection."
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _metadata(mode):
    return {
        "schema_version": 1, "algorithm": "bounded-deskew-contrast-v1", "mode": mode,
        "original_raster": {"width": 100, "height": 100},
        "processed_raster": {"width": 100, "height": 100},
        "source_to_processed": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
        "processed_to_source": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
        "deskew": {
            "status": "disabled" if mode == "contrast" else "skipped",
            "reason": "mode_disabled" if mode == "contrast" else "blank_or_low_ink",
            "angle_degrees": 0.0, "estimated_angle_degrees": None, "gain": None,
        },
        "contrast": {
            "status": "disabled" if mode == "deskew" else "skipped",
            "reason": "mode_disabled" if mode == "deskew" else "already_high_contrast",
            "low_level": None if mode == "deskew" else 0,
            "high_level": None if mode == "deskew" else 255,
        },
        "parameters": {
            "thumbnail_max_side": 1000, "max_angle_degrees": 5.0, "angle_step_degrees": 0.25,
            "min_gain": 0.025, "low_percentile": 0.5, "high_percentile": 99.5,
        },
        "libraries": {"opencv": "4.13.0", "numpy": "2.4.3"},
    }


def _candidate(text, *, dpi=300, mode="none"):
    lines = [] if not text else [{
        "text": text, "score": 0.9, "box": [[0.0, 0.0], [80.0, 0.0], [80.0, 20.0], [0.0, 20.0]],
    }]
    candidate = {
        "text": text, "lines": lines, "mean_confidence": 0.9 if lines else None,
        "raster": {"width": 100, "height": 100, "dpi": dpi,
                   "coordinate_system": "rendered_image_pixels" if mode == "none" else "preprocessed_image_pixels"},
        "engine": {"name": "rapidocr", "version": "synthetic", "min_score": 0.0, "max_side": 6000},
    }
    if mode != "none":
        candidate["preprocessing"] = _metadata(mode)
    return candidate


def _report(native=("original",), *, candidates=None, requested=(), max_pages=5, dpi=300, mode="none"):
    candidates = {} if candidates is None else candidates

    class Reader:
        page_count = len(native)

        def native_text(self, number):
            value = native[number - 1]
            if isinstance(value, BaseException):
                raise value
            return value

        def retry(self, number):
            value = candidates.get(number, "Alpha.")
            if isinstance(value, BaseException):
                raise value
            return _candidate(value, dpi=dpi, mode=mode)

    report = ocr_recovery.build_recovery_report(
        Reader(), source_sha256=DIGEST,
        policy=ocr_recovery.RetryPolicy(max_pages=max_pages, dpi=dpi, preprocessing=mode),
        requested_pages=requested)
    report["evidence_sha256"] = None
    return report


def _references(*pages):
    return {"schema_version": 1, "source_sha256": DIGEST, "pages": [
        {"page_number": number, "reference": text}
        for number, text in (pages or ((1, "Alpha."),))
    ]}


def _status_report(status):
    if status == "not_selected":
        return _report(native=(HEALTHY, HEALTHY))
    if status == "deferred":
        return _report(native=("prior one", "prior two"), max_pages=1)
    value = {"candidate_available": "Alpha.", "empty_candidate": "", "retry_failed": RuntimeError("private failure")}[status]
    return _report(native=(HEALTHY, "prior"), candidates={2: value})


def _mutate(payload, path, value):
    current = payload
    for key in path[:-1]:
        current = current[key]
    current[path[-1]] = value
    return payload


@pytest.fixture
def run_paths(tmp_path):
    baseline = tmp_path / "baseline.json"
    retry = tmp_path / "retry.json"
    references = tmp_path / "references.json"
    output = tmp_path / "comparison.json"
    for path, payload in zip((baseline, retry, references), (
        _report(candidates={1: "Apha."}), _report(), _references(),
    )):
        path.write_text(json.dumps(payload), encoding="utf-8")
    return baseline, retry, references, output


def test_candidate_text_is_compared_in_both_runs_instead_of_original_text():
    baseline = _report(native=("Alpha.",), candidates={1: "Apha."})
    retry = _report(native=("wrong prior text",), candidates={1: "Alpha."})
    result = runs.compare_recovery_runs(baseline, retry, _references())
    metrics = result["comparison"]["records"][0]
    assert metrics["baseline"]["character"]["edit_distance"] == 1
    assert metrics["retry"]["character"]["edit_distance"] == 0
    assert metrics["outcome"] == "improved"
    assert result["schema_version"] == 1 and result["kind"] == "ocr_run_comparison"
    assert result["paired_pages"] == [{"page_number": 1, "record_id": "page-00001"}]
    assert result["requires_attention"] is False
    assert result["acceptance"] == "manual_review_required"
    assert result["canonical_extraction_modified"] is False
    assert "not whole-document accuracy" in result["scope"]


def test_candidates_remain_available_when_native_originals_failed():
    baseline = _report(native=(RuntimeError("native failed"),), candidates={1: "Apha."})
    retry = _report(native=(RuntimeError("native failed"),), candidates={1: "Alpha."})
    result = runs.compare_recovery_runs(baseline, retry, _references())
    assert result["coverage"]["paired_pages"] == 1
    assert result["coverage"]["unpaired_pages"] == []
    assert result["comparison"]["summary"]["outcomes"]["improved"] == 1


@pytest.mark.parametrize(("baseline_status", "retry_status"), itertools.product(
    ("candidate_available", "empty_candidate", "retry_failed", "deferred", "not_selected"), repeat=2))
def test_every_two_sided_availability_combination_is_explicit(baseline_status, retry_status):
    result = runs.compare_recovery_runs(
        _status_report(baseline_status), _status_report(retry_status), _references((2, "Alpha.")))
    available = {"candidate_available", "empty_candidate"}
    paired = baseline_status in available and retry_status in available
    assert result["coverage"]["reference_pages"] == 1
    assert result["coverage"]["paired_pages"] == int(paired)
    assert result["coverage"]["unpaired_pages"] == ([] if paired else [{
        "page_number": 2, "baseline_status": baseline_status, "retry_status": retry_status,
    }])
    assert (result["comparison"] is None) is not paired
    if not paired or "empty_candidate" in (baseline_status, retry_status):
        assert result["requires_attention"] is True


def test_true_empty_candidates_are_scored_not_replaced_or_dropped():
    result = runs.compare_recovery_runs(
        _report(candidates={1: ""}), _report(candidates={1: "Alpha."}), _references())
    metrics = result["comparison"]["records"][0]
    assert metrics["baseline"]["word"]["deletions"] == 1
    assert metrics["retry"]["word"]["edit_distance"] == 0
    assert result["coverage"]["baseline"]["empty_candidate_pages"] == [1]
    assert result["requires_attention"] is True


def test_empty_references_keep_null_rates_and_insertion_regressions():
    result = runs.compare_recovery_runs(
        _report(candidates={1: ""}), _report(candidates={1: "invented"}), _references((1, "")))
    metrics = result["comparison"]["records"][0]
    assert metrics["delta"]["word"]["error_rate"] is None
    assert metrics["delta"]["word"]["insertions"] == 1
    assert result["comparison"]["regression_detected"] is True
    json.dumps(result, allow_nan=False)


def test_no_paired_pages_does_not_invoke_scorer(monkeypatch):
    monkeypatch.setattr(runs, "compare_ocr", lambda *args: pytest.fail("no empty benchmark scoring"))
    result = runs.compare_recovery_runs(_status_report("deferred"), _status_report("not_selected"), _references((2, "Alpha.")))
    assert result["comparison"] is None
    assert result["requires_attention"] is True


def test_reference_order_is_canonical_and_candidate_coverage_is_not_narrowed():
    baseline = _report(native=("short", "short", "short"), max_pages=2, requested=(2,))
    retry = _report(native=("short", "short", "short"), max_pages=2, requested=(3,))
    references = _references((3, "Alpha."), (1, "Alpha."), (2, "Alpha."))
    result = runs.compare_recovery_runs(baseline, retry, references)
    assert result["paired_pages"] == [{"page_number": 1, "record_id": "page-00001"}]
    assert result["coverage"]["unpaired_pages"] == [
        {"page_number": 2, "baseline_status": "candidate_available", "retry_status": "deferred"},
        {"page_number": 3, "baseline_status": "deferred", "retry_status": "candidate_available"},
    ]
    assert result["coverage"]["baseline"]["selected_pages"] == [1, 2]
    assert result["coverage"]["retry"]["selected_pages"] == [1, 3]
    references["pages"].reverse()
    assert runs.compare_recovery_runs(baseline, retry, references) == result


@pytest.mark.parametrize("side", ["baseline", "retry"])
def test_unreferenced_selected_failed_and_deferred_pages_remain_visible(side):
    complete = _report(native=("short", HEALTHY, HEALTHY))
    incomplete = _report(native=("short", "short", "short"), candidates={2: RuntimeError("failed")}, max_pages=2)
    pair = (incomplete, complete) if side == "baseline" else (complete, incomplete)
    result = runs.compare_recovery_runs(*pair, _references())
    coverage = result["coverage"][side]
    assert coverage["selected_pages"] == [1, 2]
    assert coverage["failed_pages"] == [2]
    assert coverage["unreferenced_selected_pages"] == [2]
    assert coverage["unreferenced_deferred_pages"] == [3]
    assert coverage["deferred_pages"] == [3]
    assert result["requires_attention"] is True


def test_per_page_and_critical_regressions_survive_improving_aggregate_metrics():
    baseline = _report(native=("short", "short"), candidates={1: "x y z", 2: "not"})
    retry = _report(native=("short", "short"), candidates={1: "alpha beta gamma", 2: "no"})
    references = _references((1, "alpha beta gamma"), (2, "not"))
    references["pages"][1]["critical_tokens"] = ["not"]
    result = runs.compare_recovery_runs(baseline, retry, references)
    comparison = result["comparison"]
    assert comparison["summary"]["delta"]["character"]["error_rate"] < 0
    assert comparison["summary"]["delta"]["word"]["error_rate"] < 0
    assert comparison["records"][1]["delta"]["critical_tokens"][0]["missing_occurrences"] == 1
    assert comparison["regression_detected"] is True
    assert result["requires_attention"] is True


@pytest.mark.parametrize(("baseline_mode", "retry_mode"), itertools.product(
    ("none", "deskew", "contrast", "deskew-contrast"), repeat=2))
def test_v1_and_v2_modes_are_supported_and_settings_are_normalized(baseline_mode, retry_mode):
    result = runs.compare_recovery_runs(_report(mode=baseline_mode), _report(mode=retry_mode), _references())
    settings = result["run_settings"]
    assert settings["baseline"]["retry_configuration"]["preprocessing"] == baseline_mode
    assert settings["retry"]["retry_configuration"]["preprocessing"] == retry_mode
    assert result["setting_differences"] == ([] if baseline_mode == retry_mode else ["retry_configuration.preprocessing"])
    assert result["requires_attention"] is False


def test_intended_setting_differences_are_visible_without_becoming_failure():
    baseline = _report(requested=(1,), max_pages=2)
    retry = _report(dpi=400, max_pages=3, mode="contrast")
    result = runs.compare_recovery_runs(baseline, retry, _references())
    assert result["setting_differences"] == [
        "selection.requested_pages", "selection.max_pages", "retry_configuration.dpi",
        "retry_configuration.preprocessing",
    ]
    assert result["requires_attention"] is False


def test_settings_are_detached_and_inputs_are_not_mutated():
    baseline, retry, references = _report(requested=(1,)), _report(), _references()
    original = copy.deepcopy((baseline, retry, references))
    result = runs.compare_recovery_runs(baseline, retry, references)
    assert (baseline, retry, references) == original
    result["run_settings"]["baseline"]["selection"]["requested_pages"].append(999)
    result["run_settings"]["retry"]["retry_configuration"]["dpi"] = 72
    assert (baseline, retry, references) == original


def test_evidence_and_engine_differences_are_disclosed_without_arbitrary_strings():
    baseline, retry = _report(), _report()
    retry["evidence_sha256"] = "b" * 64
    retry["pages"][0]["candidate"]["engine"]["version"] = "PRIVATE_VERSION"
    retry["pages"][0]["candidate"]["engine"]["min_score"] = 0.25
    result = runs.compare_recovery_runs(baseline, retry, _references())
    assert result["confounders"] == {
        "evidence_binding_differs": True,
        "paired_engine_settings_differ": True,
        "paired_engine_difference_pages": [1],
        "paired_preprocessing_libraries_differ": False,
        "paired_preprocessing_library_difference_pages": [],
    }
    assert "PRIVATE_VERSION" not in json.dumps(result)
    assert result["requires_attention"] is False


def test_unpaired_engine_profiles_are_not_presented_as_paired_confounders():
    baseline = _report(candidates={1: RuntimeError("failed")})
    retry = _report()
    retry["pages"][0]["candidate"]["engine"]["version"] = "PRIVATE_VERSION"
    result = runs.compare_recovery_runs(baseline, retry, _references())
    assert result["confounders"]["paired_engine_difference_pages"] == []
    assert result["confounders"]["paired_engine_settings_differ"] is False
    assert "PRIVATE_VERSION" not in json.dumps(result)


def test_paired_preprocessing_library_changes_are_flagged_without_version_strings():
    baseline, retry = _report(mode="contrast"), _report(mode="contrast")
    retry["pages"][0]["candidate"]["preprocessing"]["libraries"]["opencv"] = "PRIVATE_LIBRARY"
    result = runs.compare_recovery_runs(baseline, retry, _references())
    assert result["confounders"]["paired_preprocessing_libraries_differ"] is True
    assert result["confounders"]["paired_preprocessing_library_difference_pages"] == [1]
    assert result["setting_differences"] == []
    assert result["requires_attention"] is False
    assert "PRIVATE_LIBRARY" not in json.dumps(result)


@pytest.mark.parametrize("unpaired", [False, True])
def test_preprocessing_library_profiles_are_compared_only_for_two_paired_v2_candidates(unpaired):
    baseline = (_report(mode="contrast", candidates={1: RuntimeError("failed")}) if unpaired else _report())
    retry = _report(mode="contrast")
    retry["pages"][0]["candidate"]["preprocessing"]["libraries"]["opencv"] = "PRIVATE_LIBRARY"
    result = runs.compare_recovery_runs(baseline, retry, _references())
    assert result["confounders"]["paired_preprocessing_libraries_differ"] is False
    assert result["confounders"]["paired_preprocessing_library_difference_pages"] == []
    assert "PRIVATE_LIBRARY" not in json.dumps(result)


def test_processing_outcomes_are_not_reported_as_experiment_setting_changes():
    baseline, retry = _report(mode="contrast"), _report(mode="contrast")
    retry["pages"][0]["candidate"]["preprocessing"]["contrast"] = {
        "status": "applied", "reason": "stretch_applied", "low_level": 40, "high_level": 200,
    }
    result = runs.compare_recovery_runs(baseline, retry, _references())
    assert result["setting_differences"] == []
    assert result["confounders"]["paired_preprocessing_libraries_differ"] is False
    assert result["requires_attention"] is False


@pytest.mark.parametrize("target", ["retry", "reference"])
def test_source_hash_mismatches_fail_before_scoring(monkeypatch, target):
    baseline, retry, references = _report(), _report(), _references()
    (retry if target == "retry" else references)["source_sha256"] = "b" * 64
    monkeypatch.setattr(runs, "compare_ocr", lambda *args: pytest.fail("must not score mismatched sources"))
    with pytest.raises(ValueError, match="source digest"):
        runs.compare_recovery_runs(baseline, retry, references)


def test_page_count_mismatch_is_rejected_even_with_matching_source_hash():
    with pytest.raises(ValueError, match="matching PDF page counts"):
        runs.compare_recovery_runs(_report(), _report(native=("short", HEALTHY)), _references())


def test_reference_pages_must_be_in_shared_document_range():
    with pytest.raises(ValueError, match="reference page number"):
        runs.compare_recovery_runs(_report(), _report(), _references((2, "Alpha.")))


@pytest.mark.parametrize("side", [0, 1])
@pytest.mark.parametrize(("path", "value"), [
    (("schema_version",), True), (("schema_version",), 3),
    (("canonical_extraction_modified",), True),
    (("summary", "selected"), 99),
    (("pages", 0, "candidate", "text"), "PRIVATE_FORGED_TEXT"),
    (("pages", 0, "candidate", "mean_confidence"), float("nan")),
    (("pages", 0, "candidate", "raster", "coordinate_system"), "preprocessed_image_pixels"),
])
def test_complete_both_report_contracts_are_validated(side, path, value):
    reports = [_report(), _report()]
    _mutate(reports[side], path, value)
    with pytest.raises(ValueError) as error:
        runs.compare_recovery_runs(*reports, _references())
    assert "PRIVATE_FORGED_TEXT" not in str(error.value)


@pytest.mark.parametrize("side", [0, 1])
def test_unreferenced_malformed_candidates_still_fail_validation(side):
    reports = [_report(native=("short", "short")), _report(native=("short", "short"))]
    reports[side]["pages"][1]["candidate"]["PRIVATE_FIELD"] = "PRIVATE_VALUE"
    with pytest.raises(ValueError) as error:
        runs.compare_recovery_runs(*reports, _references())
    assert "PRIVATE_" not in str(error.value)


def test_v2_metadata_mismatch_is_not_hidden_by_other_valid_run():
    retry = _report(mode="contrast")
    retry["pages"][0]["candidate"]["preprocessing"]["source_to_processed"][0][2] = 10
    with pytest.raises(ValueError):
        runs.compare_recovery_runs(_report(), retry, _references())


def test_shared_alignment_budget_is_not_reset_for_each_candidate_run(monkeypatch):
    monkeypatch.setattr(ocr_comparison, "MAX_COMPARISON_ALIGNMENT_CELLS", 19)
    monkeypatch.setattr(ocr_evaluation, "evaluate_ocr", lambda *args: pytest.fail("must preflight both sides"))
    with pytest.raises(ValueError, match="combined alignment"):
        runs.compare_recovery_runs(
            _report(candidates={1: "xyz"}), _report(candidates={1: "uvw"}), _references((1, "abc")))


def test_reports_never_include_original_candidate_reference_or_critical_text():
    baseline = _report(native=("PRIVATE_ORIGINAL",), candidates={1: "PRIVATE_BASELINE"})
    retry = _report(native=("PRIVATE_ORIGINAL",), candidates={1: "PRIVATE_RETRY"})
    references = _references((1, "PRIVATE_REFERENCE"))
    references["pages"][0]["critical_tokens"] = ["PRIVATE_REFERENCE"]
    assert "PRIVATE_" not in json.dumps(runs.compare_recovery_runs(baseline, retry, references))


def test_file_comparison_binds_three_exact_inputs_and_writes_private_report(run_paths):
    before = [path.read_bytes() for path in run_paths[:3]]
    result = runs.compare_recovery_run_files(*run_paths)
    assert result["inputs"] == dict(zip(
        ("baseline_recovery_sha256", "retry_recovery_sha256", "references_sha256"),
        (hashlib.sha256(raw).hexdigest() for raw in before),
    ))
    assert [path.read_bytes() for path in run_paths[:3]] == before
    assert json.loads(run_paths[3].read_text(encoding="utf-8")) == result
    assert "Apha." not in run_paths[3].read_text(encoding="utf-8")
    if os.name != "nt":
        assert run_paths[3].stat().st_mode & 0o077 == 0


def test_equal_reports_in_distinct_files_compare_unchanged(run_paths):
    run_paths[1].write_bytes(run_paths[0].read_bytes())
    result = runs.compare_recovery_run_files(*run_paths)
    assert result["comparison"]["summary"]["outcomes"]["unchanged"] == 1
    assert result["inputs"]["baseline_recovery_sha256"] == result["inputs"]["retry_recovery_sha256"]


@pytest.mark.parametrize(("first", "second"), itertools.combinations(range(4), 2))
def test_all_four_paths_must_be_distinct(run_paths, first, second):
    paths = list(run_paths)
    before = [path.read_bytes() for path in run_paths[:3]]
    paths[second] = paths[first]
    with pytest.raises(ValueError, match="distinct"):
        runs.compare_recovery_run_files(*paths)
    assert [path.read_bytes() for path in run_paths[:3]] == before
    assert not run_paths[3].exists()


@pytest.mark.parametrize("input_index", [0, 1, 2])
def test_hardlink_alias_to_any_input_is_refused(run_paths, input_index):
    try:
        os.link(run_paths[input_index], run_paths[3])
    except OSError:
        pytest.skip("hard links unavailable")
    before = run_paths[input_index].read_bytes()
    with pytest.raises(ValueError, match="distinct"):
        runs.compare_recovery_run_files(*run_paths)
    assert run_paths[input_index].read_bytes() == before


@pytest.mark.parametrize("index", range(4))
def test_symlink_inputs_and_output_are_refused(run_paths, tmp_path, index):
    paths = list(run_paths)
    link = tmp_path / "linked.json"
    try:
        link.symlink_to(paths[index])
    except OSError:
        pytest.skip("symbolic links unavailable")
    paths[index] = link
    with pytest.raises(storage_policy.StoragePolicyError):
        runs.compare_recovery_run_files(*paths)
    assert not run_paths[3].exists()


def test_existing_output_is_refused_before_comparison(run_paths, monkeypatch):
    run_paths[3].write_bytes(b"existing output")
    monkeypatch.setattr(runs, "compare_recovery_runs", lambda *args: pytest.fail("must not compare"))
    with pytest.raises(FileExistsError):
        runs.compare_recovery_run_files(*run_paths)
    assert run_paths[3].read_bytes() == b"existing output"


def test_final_publication_is_no_clobber_under_uncooperative_race(run_paths, monkeypatch):
    publish = ocr_recovery._publish_new_report

    def race(temporary, destination):
        destination.write_bytes(b"concurrent owner")
        publish(temporary, destination)

    monkeypatch.setattr(ocr_recovery, "_publish_new_report", race)
    with pytest.raises(FileExistsError):
        runs.compare_recovery_run_files(*run_paths)
    assert run_paths[3].read_bytes() == b"concurrent owner"
    assert not list(run_paths[3].parent.glob(f".{run_paths[3].name}.*.tmp"))


@pytest.mark.parametrize("index", [0, 1, 2])
def test_each_input_is_reverified_after_comparison(run_paths, monkeypatch, index):
    compare = runs.compare_recovery_runs

    def mutate(*args):
        result = compare(*args)
        run_paths[index].write_bytes(run_paths[index].read_bytes() + b"\n")
        return result

    monkeypatch.setattr(runs, "compare_recovery_runs", mutate)
    with pytest.raises(RuntimeError, match="changed before publication"):
        runs.compare_recovery_run_files(*run_paths)
    assert not run_paths[3].exists()


@pytest.mark.parametrize("index", [0, 1, 2])
@pytest.mark.parametrize("raw", [
    b'{"PRIVATE_FIELD":1,"PRIVATE_FIELD":2}', b'{"bad":NaN}', b'{"bad":1e9999}', b'[]', b'\xff',
])
def test_every_input_uses_bounded_strict_json_without_echoing_fields(run_paths, index, raw):
    run_paths[index].write_bytes(raw)
    with pytest.raises(ValueError) as error:
        runs.compare_recovery_run_files(*run_paths)
    assert "PRIVATE_" not in str(error.value)
    assert str(run_paths[index]) not in str(error.value)
    assert not run_paths[3].exists()


@pytest.mark.parametrize("limit", ["MAX_RECOVERY_BYTES", "MAX_REFERENCE_BYTES"])
def test_input_size_limits_fail_before_scoring(run_paths, monkeypatch, limit):
    monkeypatch.setattr(runs, limit, 10)
    monkeypatch.setattr(runs, "compare_recovery_runs", lambda *args: pytest.fail("must not compare"))
    with pytest.raises(ValueError, match="bounded strict"):
        runs.compare_recovery_run_files(*run_paths)
    assert not run_paths[3].exists()


def test_post_comparison_size_failure_is_redacted(run_paths, monkeypatch):
    compare = runs.compare_recovery_runs

    def mutate(*args):
        result = compare(*args)
        run_paths[1].write_bytes(b"x" * (len(run_paths[1].read_bytes()) + 1))
        return result

    run_paths[0].write_bytes(run_paths[1].read_bytes())
    monkeypatch.setattr(runs, "MAX_RECOVERY_BYTES", len(run_paths[1].read_bytes()))
    monkeypatch.setattr(runs, "compare_recovery_runs", mutate)
    with pytest.raises(ValueError, match="invalid bounded artifact") as error:
        runs.compare_recovery_run_files(*run_paths)
    assert str(run_paths[1]) not in str(error.value)
    assert not run_paths[3].exists()


def test_atomic_writer_and_post_commit_cleanup_error_semantics_are_preserved(run_paths, monkeypatch):
    calls = []

    def fail(path, result, **kwargs):
        calls.append((path, result, kwargs))
        raise ocr_recovery.ReportCleanupError("private cleanup details")

    monkeypatch.setattr(storage_policy, "atomic_write_private_json", fail)
    with pytest.raises(ocr_recovery.ReportCleanupError):
        runs.compare_recovery_run_files(*run_paths)
    assert calls[0][0] == run_paths[3]
    assert calls[0][2] == {"indent": 2, "replace_fn": ocr_recovery._publish_new_report}


def test_cancellation_prevents_publication(run_paths, monkeypatch):
    def cancel(*args):
        raise KeyboardInterrupt()

    monkeypatch.setattr(runs, "compare_recovery_runs", cancel)
    with pytest.raises(KeyboardInterrupt):
        runs.compare_recovery_run_files(*run_paths)
    assert not run_paths[3].exists()


def test_import_does_not_load_images_models_or_rag():
    result = subprocess.run(
        [sys.executable, "-c", "import sys; import ocr_run_comparison; assert not ({'rag','torch','numpy','docling','rapidocr','pymupdf','requests','cv2'} & set(sys.modules))"],
        cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
