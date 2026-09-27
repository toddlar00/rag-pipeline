"""Source-bound OCR comparison, coverage, and publication failure contracts."""

from __future__ import annotations

import copy
import hashlib
import json
import os

import pytest

import ocr_evaluation
import ocr_recovery
import ocr_recovery_comparison as comparison
import storage_policy
from tools import compare_ocr as cli


DIGEST = "a" * 64
EVIDENCE_DIGEST = "b" * 64
HEALTHY = "A sufficiently long synthetic paragraph that needs no heuristic OCR retry."


def _candidate(text="Alpha."):
    lines = [] if not text else [{
        "text": text, "score": 0.9,
        "box": [[0.0, 0.0], [80.0, 0.0], [80.0, 20.0], [0.0, 20.0]],
    }]
    return {
        "text": text, "lines": lines,
        "mean_confidence": 0.9 if lines else None,
        "raster": {"width": 100, "height": 100, "dpi": 300,
                   "coordinate_system": "rendered_image_pixels"},
        "engine": {"name": "rapidocr", "version": "synthetic",
                   "min_score": 0.0, "max_side": 6000},
    }


def _report(native=("Apha.",), *, candidates=None, max_pages=5,
            requested=(), evidence=None):
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
            return _candidate(value)

    result = ocr_recovery.build_recovery_report(
        Reader(), source_sha256=DIGEST,
        policy=ocr_recovery.RetryPolicy(max_pages=max_pages),
        requested_pages=requested, evidence=evidence)
    result["evidence_sha256"] = EVIDENCE_DIGEST if evidence is not None else None
    return result


def _references(*pages, digest=DIGEST):
    return {"schema_version": 1, "source_sha256": digest, "pages": [
        {"page_number": number, "reference": reference}
        for number, reference in (pages or ((1, "Alpha."),))
    ]}


def _mutate(payload, path, value):
    owner = payload
    for key in path[:-1]:
        owner = owner[key]
    owner[path[-1]] = value
    return payload


def _preprocessing_metadata(mode="contrast"):
    return {
        "schema_version": 1,
        "algorithm": "bounded-deskew-contrast-v1",
        "mode": mode,
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
            "thumbnail_max_side": 1000, "max_angle_degrees": 5.0,
            "angle_step_degrees": 0.25, "min_gain": 0.025,
            "low_percentile": 0.5, "high_percentile": 99.5,
        },
        "libraries": {"opencv": "4.13.0", "numpy": "2.4.3"},
    }


def _preprocessed_report(mode="contrast", **kwargs):
    report = _report(**kwargs)
    report["schema_version"] = 2
    report["retry_configuration"]["preprocessing"] = mode
    for page in report["pages"]:
        if page["candidate"] is not None:
            page["candidate"]["raster"]["coordinate_system"] = "preprocessed_image_pixels"
            page["candidate"]["preprocessing"] = _preprocessing_metadata(mode)
    return report


@pytest.fixture
def artifact_paths(tmp_path):
    recovery_path = tmp_path / "synthetic-recovery.json"
    reference_path = tmp_path / "synthetic-reference.json"
    output_path = tmp_path / "comparison.json"
    recovery_path.write_text(json.dumps(_report()), encoding="utf-8")
    reference_path.write_text(json.dumps(_references()), encoding="utf-8")
    return recovery_path, reference_path, output_path


def test_valid_emitted_report_comparison_is_paired_and_content_free():
    report = _report(native=("The tenant did waive notice.",),
                     candidates={1: "The tenant did not waive notice."})
    references = _references((1, "The tenant did not waive notice."))
    references["pages"][0]["critical_tokens"] = ["not"]
    result = comparison.compare_recovery_report(report, references)
    assert comparison.validate_recovery_report(report) is report
    assert result["source_sha256"] == DIGEST
    assert result["coverage"]["reference_pages"] == 1
    assert result["coverage"]["paired_pages"] == 1
    assert result["paired_pages"] == [{
        "page_number": 1, "record_id": "page-00001", "baseline_kind": "pdf_native"}]
    summary = result["comparison"]["summary"]
    assert summary["baseline"]["word"]["edit_distance"] == 1
    assert summary["retry"]["word"]["edit_distance"] == 0
    assert summary["delta"]["word"]["error_rate"] < 0
    assert result["requires_attention"] is False
    assert result["acceptance"] == "manual_review_required"
    assert result["canonical_extraction_modified"] is False
    encoded = json.dumps(result)
    assert "The tenant" not in encoded and '"not"' not in encoded
    assert "not whole-document accuracy" in result["scope"]
    assert "authenticity not verified" in result["reference_attestation"]


def test_fixed_reference_universe_retains_every_unpaired_reason_and_empty_pair():
    report = _report(
        native=("Alpha.", "Beta.", RuntimeError("UNAVAILABLE"), "Delta.", HEALTHY, "short", "short"),
        candidates={1: "Alpha.", 2: RuntimeError("FAILED"), 3: "Gamma.", 4: ""},
        max_pages=4)
    references = _references(
        (6, "Six."), (4, "Delta."), (2, "Beta."),
        (5, HEALTHY), (3, "Gamma."), (1, "Alpha."))
    result = comparison.compare_recovery_report(report, references)
    coverage = result["coverage"]
    assert coverage["reference_pages"] == 6
    assert coverage["paired_pages"] == 2
    assert coverage["unpaired_pages"] == [
        {"page_number": 2, "reasons": ["retry_failed"],
         "baseline_available": True, "retry_available": False},
        {"page_number": 3, "reasons": ["original_unavailable"],
         "baseline_available": False, "retry_available": True},
        {"page_number": 5, "reasons": ["not_selected"],
         "baseline_available": False, "retry_available": False},
        {"page_number": 6, "reasons": ["deferred"],
         "baseline_available": False, "retry_available": False},
    ]
    assert coverage["deferred_pages"] == [6, 7]
    assert coverage["unreferenced_deferred_pages"] == [7]
    assert coverage["empty_candidate_pages"] == [4]
    assert [p["page_number"] for p in result["paired_pages"]] == [1, 4]
    assert [r["id"] for r in result["comparison"]["records"]] == ["page-00001", "page-00004"]
    assert result["comparison"]["summary"]["baseline"]["record_count"] == 2
    assert result["comparison"]["summary"]["retry"]["record_count"] == 2
    assert result["comparison"]["records"][1]["retry"]["word"]["deletions"] == 1
    assert result["requires_attention"] is True


@pytest.mark.parametrize("report,reason", [
    (_report(candidates={1: RuntimeError("failure")}), ["retry_failed"]),
    (_report(native=(RuntimeError("failure"),)), ["original_unavailable"]),
    (_report(native=(RuntimeError("failure"),), candidates={1: RuntimeError("failure")}),
     ["original_unavailable", "retry_failed"]),
    (_report(native=(HEALTHY,)), ["not_selected"]),
])
def test_no_pair_has_null_comparison_and_never_calls_scorer(monkeypatch, report, reason):
    def forbidden(*args):
        pytest.fail("no paired rows must not be scored as blank strings")

    monkeypatch.setattr(comparison, "compare_ocr", forbidden)
    result = comparison.compare_recovery_report(report, _references())
    assert result["comparison"] is None
    assert result["paired_pages"] == []
    assert result["coverage"]["unpaired_pages"][0]["reasons"] == reason
    assert result["requires_attention"] is True


def test_missing_references_are_explicit_even_when_paired_page_improves():
    result = comparison.compare_recovery_report(
        _report(native=("Apha.", "Beta.")), _references())
    assert result["coverage"]["unreferenced_selected_pages"] == [2]
    assert result["comparison"]["summary"]["outcomes"]["improved"] == 1
    assert result["requires_attention"] is True


def test_genuine_empty_original_and_retry_are_available_not_missing():
    result = comparison.compare_recovery_report(
        _report(native=("",), candidates={1: ""}), _references((1, "")))
    assert result["coverage"]["paired_pages"] == 1
    assert result["coverage"]["unpaired_pages"] == []
    assert result["comparison"]["summary"]["baseline"]["character"]["error_rate"] is None
    assert result["comparison"]["summary"]["retry"]["character"]["error_rate"] is None
    assert result["comparison"]["summary"]["outcomes"]["unchanged"] == 1
    assert result["requires_attention"] is True


def test_regression_requires_attention_with_complete_coverage():
    result = comparison.compare_recovery_report(
        _report(native=("Alpha.",), candidates={1: "Apha."}), _references())
    assert result["coverage"]["unpaired_pages"] == []
    assert result["comparison"]["regression_detected"] is True
    assert result["requires_attention"] is True


def test_source_hash_mismatch_fails_before_scoring(monkeypatch):
    def forbidden(*args):
        pytest.fail("unbound sources must not be scored")

    monkeypatch.setattr(comparison, "compare_ocr", forbidden)
    with pytest.raises(ValueError, match="digests differ"):
        comparison.compare_recovery_report(_report(), _references(digest="c" * 64))


@pytest.mark.parametrize("path,value", [
    (("schema_version",), True), (("schema_version",), 2),
    (("kind",), "arbitrary"), (("source_sha256",), "A" * 64),
    (("source_sha256",), None), (("evidence_sha256",), "invalid"),
    (("page_count",), True), (("page_count",), 0), (("page_count",), 5001),
    (("canonical_extraction_modified",), 0), (("accuracy_verified",), True),
    (("reading_order",), "verified"),
    (("selection", "requested_pages"), [1, 1]),
    (("selection", "requested_pages"), [2]),
    (("selection", "requested_pages"), [True]),
    (("selection", "requested_pages"), "1"),
    (("selection", "requested_pages"), [1]),
    (("selection", "order"), "severity"),
    (("selection", "max_pages"), 0), (("selection", "max_pages"), 21),
    (("selection", "min_chars"), True), (("selection", "min_chars"), 10001),
    (("retry_configuration", "dpi"), 71), (("retry_configuration", "dpi"), 601),
    (("retry_configuration", "max_pixels"), 25000001),
    (("retry_configuration", "max_side"), 6001),
    (("retry_configuration", "attempts_per_page"), 2),
    (("retry_configuration", "attempts_per_page"), True),
    (("pages",), {}), (("deferred_pages",), None),
    (("summary", "selected"), 0), (("summary", "selected"), True),
    (("summary", "inspected"), 2), (("summary", "failed"), 1),
    (("pages", 0, "page_number"), 0), (("pages", 0, "page_number"), True),
    (("pages", 0, "original_text"), None),
    (("pages", 0, "original_text"), "\ud800"),
    (("pages", 0, "original_error"), "native_extraction_failed"),
    (("pages", 0, "baseline_kind"), "arbitrary"),
    (("pages", 0, "baseline_kind"), "operator_evidence"),
    (("pages", 0, "low_grade"), "POOR"),
    (("pages", 0, "original_ocr_confidence"), 0.8),
    (("pages", 0, "reasons"), ["empty_text"]),
    (("pages", 0, "reasons"), []),
    (("pages", 0, "reasons"), ["short_text", "short_text"]),
    (("pages", 0, "reasons"), ["unknown"]),
    (("pages", 0, "reasons"), ["short_text", "requested"]),
    (("pages", 0, "reasons"), ["empty_text", "short_text"]),
    (("pages", 0, "status"), "accepted"),
    (("pages", 0, "status"), "empty_candidate"),
    (("pages", 0, "status"), "retry_failed"),
    (("pages", 0, "error_code"), "retry_runtime_failed"),
    (("pages", 0, "candidate"), None),
    (("pages", 0, "candidate", "text"), "forged"),
    (("pages", 0, "candidate", "raster", "dpi"), 400),
    (("pages", 0, "candidate", "raster", "width"), 6001),
    (("pages", 0, "candidate", "engine", "max_side"), 5000),
], ids=lambda value: type(value).__name__)
def test_recovery_header_page_summary_reason_and_raster_mutants_fail(path, value):
    with pytest.raises(ValueError):
        comparison.validate_recovery_report(_mutate(_report(), path, value))


@pytest.mark.parametrize("path", [(), ("selection",), ("retry_configuration",),
                                 ("summary",), ("pages", 0), ("pages", 0, "candidate")])
def test_unknown_recovery_fields_fail(path):
    report = _report()
    owner = report
    for key in path:
        owner = owner[key]
    owner["UNKNOWN_PRIVATE_FIELD"] = "PRIVATE_TEXT"
    with pytest.raises(ValueError):
        comparison.validate_recovery_report(report)


def test_raster_pixel_area_must_match_configured_budget():
    report = _report()
    report["retry_configuration"]["max_pixels"] = 9999
    with pytest.raises(ValueError, match="raster contradicts"):
        comparison.validate_recovery_report(report)


@pytest.mark.parametrize("mutation", ["duplicate", "overlap", "out_of_order", "missing_requested", "unused_capacity", "deferred_reason"])
def test_selection_union_is_unique_ordered_and_accounts_for_requested_pages(mutation):
    report = _report(native=("one", "two", "three", "four"), max_pages=2, requested=(3,))
    if mutation == "duplicate":
        report["pages"][1] = copy.deepcopy(report["pages"][0])
    elif mutation == "overlap":
        report["deferred_pages"][0]["page_number"] = 1
    elif mutation == "out_of_order":
        report["pages"].reverse()
    elif mutation == "missing_requested":
        report["selection"]["requested_pages"] = [2, 3]
    elif mutation == "unused_capacity":
        report["selection"]["max_pages"] = 3
    else:
        report["deferred_pages"][0]["reasons"] = ["empty_text", "corrupt_text"]
    with pytest.raises(ValueError):
        comparison.validate_recovery_report(report)


def test_valid_requested_order_can_differ_from_natural_page_order():
    report = _report(native=("one", "two", "three", "four"), max_pages=2, requested=(3,))
    assert [p["page_number"] for p in report["pages"]] == [3, 1]
    assert comparison.validate_recovery_report(report) is report


@pytest.mark.parametrize("mutation", ["error", "candidate", "status"])
def test_failed_retry_cannot_acquire_blank_candidate_or_success_state(mutation):
    report = _report(candidates={1: RuntimeError("failed")})
    if mutation == "error":
        report["pages"][0]["error_code"] = None
    elif mutation == "candidate":
        report["pages"][0]["candidate"] = _candidate("")
    else:
        report["pages"][0]["status"] = "empty_candidate"
    with pytest.raises(ValueError):
        comparison.validate_recovery_report(report)


def test_valid_operator_evidence_is_bound_and_keeps_unknown_confidence_null():
    evidence = {"schema_version": 1, "source_sha256": DIGEST, "pages": [{
        "page_number": 1, "text": "Apha.", "low_grade": "POOR", "ocr_confidence": None}]}
    report = _report(evidence=evidence)
    result = comparison.compare_recovery_report(report, _references())
    assert result["paired_pages"][0]["baseline_kind"] == "operator_evidence"
    assert report["pages"][0]["original_ocr_confidence"] is None
    report["evidence_sha256"] = None
    with pytest.raises(ValueError, match="evidence binding"):
        comparison.validate_recovery_report(report)


@pytest.mark.parametrize("path,value", [
    (("schema_version",), False), (("schema_version",), 2),
    (("source_sha256",), "A" * 64), (("pages",), []), (("pages",), {}),
    (("pages", 0, "page_number"), True), (("pages", 0, "page_number"), 0),
    (("pages", 0, "page_number"), 2),
    (("pages", 0, "reference"), None), (("pages", 0, "reference"), "\ud800"),
    (("pages", 0, "reference"), "x" * (ocr_evaluation.MAX_TEXT_CHARACTERS + 1)),
    (("pages", 0, "critical_tokens"), ["not in reference"]),
    (("pages", 0, "critical_tokens"), ["Alpha.", "Alpha."]),
    (("pages", 0, "critical_tokens"), [""]),
    (("pages", 0, "critical_tokens"), True),
], ids=lambda value: type(value).__name__)
def test_reference_contract_reuses_strict_scorer_limits(path, value):
    with pytest.raises(ValueError):
        comparison.validate_references(_mutate(_references(), path, value), page_count=1)


def test_reference_duplicates_unknown_fields_and_record_cap_fail():
    duplicate = _references((1, "Alpha."), (1, "Alpha."))
    too_many = _references(*((n, "Alpha.") for n in range(1, 258)))
    unknown = _references()
    unknown["pages"][0]["prediction"] = "unauthorized side"
    for payload in (duplicate, too_many, unknown):
        with pytest.raises(ValueError):
            comparison.validate_references(payload, page_count=5000)


@pytest.mark.parametrize("page_count", [True, 0, 5001, 1.0, "1"])
def test_reference_validator_requires_bounded_integer_page_count(page_count):
    with pytest.raises(ValueError):
        comparison.validate_references(_references(), page_count=page_count)


def test_comparison_respects_scoring_text_limit_and_does_not_drop_long_pairs():
    report = _report(native=("x" * 20001,), requested=(1,))
    with pytest.raises(ValueError, match="at most 20000"):
        comparison.compare_recovery_report(report, _references())


def test_file_comparison_pins_exact_input_digests_and_writes_private_metrics(artifact_paths):
    recovery_path, reference_path, output_path = artifact_paths
    original_inputs = [path.read_bytes() for path in (recovery_path, reference_path)]
    result = comparison.compare_recovery_file(*artifact_paths)
    assert result["inputs"] == {
        "recovery_sha256": hashlib.sha256(original_inputs[0]).hexdigest(),
        "references_sha256": hashlib.sha256(original_inputs[1]).hexdigest()}
    assert json.loads(output_path.read_text(encoding="utf-8")) == result
    assert [path.read_bytes() for path in (recovery_path, reference_path)] == original_inputs
    assert "Apha." not in output_path.read_text(encoding="utf-8")
    assert "Alpha." not in output_path.read_text(encoding="utf-8")
    if os.name != "nt":
        assert output_path.stat().st_mode & 0o077 == 0


@pytest.mark.parametrize("alias", ["inputs", "output_recovery", "output_reference"])
def test_comparison_paths_must_be_distinct_before_mutation(artifact_paths, alias):
    recovery_path, reference_path, output_path = artifact_paths
    before = {p: p.read_bytes() for p in (recovery_path, reference_path)}
    if alias == "inputs":
        reference_path = recovery_path
    elif alias == "output_recovery":
        output_path = recovery_path
    else:
        output_path = reference_path
    with pytest.raises(ValueError, match="distinct"):
        comparison.compare_recovery_file(recovery_path, reference_path, output_path)
    assert all(path.read_bytes() == raw for path, raw in before.items())


def test_hardlink_input_alias_is_rejected(artifact_paths, tmp_path):
    recovery_path, _, output_path = artifact_paths
    alias_path = tmp_path / "hardlink.json"
    try:
        os.link(recovery_path, alias_path)
    except OSError:
        pytest.skip("hard links unavailable")
    with pytest.raises(ValueError, match="distinct"):
        comparison.compare_recovery_file(recovery_path, alias_path, output_path)
    assert not output_path.exists()


@pytest.mark.parametrize("target", [0, 1, 2])
def test_symlink_input_or_output_is_rejected(artifact_paths, tmp_path, target):
    paths = list(artifact_paths)
    link_path = tmp_path / "symbolic.json"
    try:
        link_path.symlink_to(paths[target])
    except OSError:
        pytest.skip("symbolic links unavailable")
    paths[target] = link_path
    with pytest.raises(storage_policy.StoragePolicyError):
        comparison.compare_recovery_file(*paths)
    assert not artifact_paths[2].exists()


def test_existing_output_is_never_overwritten(artifact_paths, monkeypatch):
    output = artifact_paths[2]
    output.write_bytes(b"existing report")
    monkeypatch.setattr(comparison, "compare_recovery_report", lambda *args: pytest.fail("must not compare"))
    with pytest.raises(FileExistsError):
        comparison.compare_recovery_file(*artifact_paths)
    assert output.read_bytes() == b"existing report"


def test_uncooperative_publication_race_does_not_clobber(artifact_paths, monkeypatch):
    actual_publish = ocr_recovery._publish_new_report

    def race(temporary, destination):
        destination.write_bytes(b"concurrent owner")
        actual_publish(temporary, destination)

    monkeypatch.setattr(ocr_recovery, "_publish_new_report", race)
    with pytest.raises(FileExistsError):
        comparison.compare_recovery_file(*artifact_paths)
    output = artifact_paths[2]
    assert output.read_bytes() == b"concurrent owner"
    assert not list(output.parent.glob(f".{output.name}.*.tmp"))


@pytest.mark.parametrize("target", [0, 1])
def test_changed_input_during_comparison_blocks_publication(artifact_paths, monkeypatch, target):
    actual_compare = comparison.compare_recovery_report

    def mutate(*args):
        result = actual_compare(*args)
        path = artifact_paths[target]
        path.write_bytes(path.read_bytes() + b"\n")
        return result

    monkeypatch.setattr(comparison, "compare_recovery_report", mutate)
    with pytest.raises(RuntimeError, match="changed before publication"):
        comparison.compare_recovery_file(*artifact_paths)
    assert not artifact_paths[2].exists()


@pytest.mark.parametrize("target", [0, 1])
@pytest.mark.parametrize("raw", [
    b'{"PRIVATE_FIELD":1,"PRIVATE_FIELD":2}', b'{"a":NaN}', b'{"a":Infinity}',
    b'[]', b'\xff', b'{"a":', b'{"a":1e100000}',
])
def test_malformed_json_is_bounded_and_sanitized(artifact_paths, target, raw):
    artifact_paths[target].write_bytes(raw)
    with pytest.raises(ValueError) as caught:
        comparison.compare_recovery_file(*artifact_paths)
    assert "PRIVATE_" not in str(caught.value)
    assert str(artifact_paths[target]) not in str(caught.value)
    assert not artifact_paths[2].exists()


@pytest.mark.parametrize("limit", ["MAX_RECOVERY_BYTES", "MAX_REFERENCE_BYTES"])
def test_input_size_budgets_fail_before_publication(artifact_paths, monkeypatch, limit):
    monkeypatch.setattr(comparison, limit, 10)
    with pytest.raises(ValueError, match="bounded strict"):
        comparison.compare_recovery_file(*artifact_paths)
    assert not artifact_paths[2].exists()


@pytest.mark.parametrize("error", [
    ValueError("PRIVATE_TEXT"), OSError("PRIVATE_PATH"), RuntimeError("PRIVATE_DETAILS"),
    ImportError("PRIVATE_PACKAGE"), FileExistsError("PRIVATE_OUTPUT"),
    ocr_recovery.ReportCleanupError("PRIVATE_STAGING"),
])
def test_cli_sanitizes_all_expected_external_errors(monkeypatch, capsys, error):
    def fail(*args):
        raise error

    monkeypatch.setattr(cli, "compare_recovery_file", fail)
    assert cli.main(["--recovery", "PRIVATE_RECOVERY.json", "--references", "PRIVATE_REFERENCE.json",
                     "--output", "PRIVATE_OUTPUT.json"]) == 2
    captured = capsys.readouterr()
    assert "PRIVATE_" not in captured.out + captured.err
    assert "OCR comparison" in captured.err
    if isinstance(error, ocr_recovery.ReportCleanupError):
        assert "was created" in captured.err


def test_cli_end_to_end_prints_counts_not_transcriptions(artifact_paths, capsys):
    recovery_path, reference_path, output_path = artifact_paths
    assert cli.main(["--recovery", str(recovery_path), "--references", str(reference_path),
                     "--output", str(output_path)]) == 0
    captured = capsys.readouterr()
    assert "1 reference pages; 1 paired pages" in captured.out
    assert "Alpha." not in captured.out and "Apha." not in captured.out
    assert str(recovery_path) not in captured.out + captured.err
    assert "manual review" in captured.out


def test_cancellation_during_comparison_does_not_publish(artifact_paths, monkeypatch):
    def cancel(*args):
        raise KeyboardInterrupt()

    monkeypatch.setattr(comparison, "compare_recovery_report", cancel)
    with pytest.raises(KeyboardInterrupt):
        comparison.compare_recovery_file(*artifact_paths)
    assert not artifact_paths[2].exists()


@pytest.mark.parametrize("mode", ["deskew", "contrast", "deskew-contrast"])
def test_v2_preprocessed_report_has_same_comparison_contract_as_v1(mode):
    original = _report()
    processed = _preprocessed_report(mode)
    before = copy.deepcopy(processed)
    assert comparison.validate_recovery_report(processed) is processed
    result = comparison.compare_recovery_report(processed, _references())
    assert result == comparison.compare_recovery_report(original, _references())
    assert result["schema_version"] == 1
    assert result["comparison"]["summary"]["outcomes"]["improved"] == 1
    assert result["acceptance"] == "manual_review_required"
    assert "preprocessing" not in result
    assert "recovery_configuration" not in result
    assert processed == before


@pytest.mark.parametrize("mode", ["deskew", "contrast", "deskew-contrast"])
def test_emitted_v2_writer_report_is_accepted_by_comparison(mode):
    expected = _preprocessed_report(mode)

    class Reader:
        page_count = 1

        def native_text(self, number):
            return "Apha."

        def retry(self, number):
            return expected["pages"][0]["candidate"]

    emitted = ocr_recovery.build_recovery_report(
        Reader(), source_sha256=DIGEST,
        policy=ocr_recovery.RetryPolicy(preprocessing=mode))
    emitted["evidence_sha256"] = None
    assert emitted == expected
    assert comparison.validate_recovery_report(emitted) is emitted


def test_v2_applied_rotation_and_contrast_are_accepted_with_processed_coordinates():
    report = _preprocessed_report("deskew-contrast")
    candidate = report["pages"][0]["candidate"]
    candidate["raster"].update({"width": 102, "height": 102})
    metadata = candidate["preprocessing"]
    metadata.update({
        "processed_raster": {"width": 102, "height": 102},
        "source_to_processed": [
            [0.9998476951563913, 0.01745240643728351, 0.1349949203162596],
            [-0.01745240643728351, 0.9998476951563913, 1.880235564044611],
        ],
        "processed_to_source": [
            [0.9998476951563913, -0.01745240643728351, -0.10215972467449129],
            [0.01745240643728351, 0.9998476951563913, -1.8823051812774096],
        ],
        "deskew": {"status": "applied", "reason": "rotation_applied",
                   "angle_degrees": 1.0, "estimated_angle_degrees": 1.0, "gain": 0.1},
        "contrast": {"status": "applied", "reason": "stretch_applied",
                     "low_level": 40, "high_level": 220},
    })
    result = comparison.compare_recovery_report(report, _references())
    assert result["coverage"]["paired_pages"] == 1
    assert result["comparison"]["summary"]["outcomes"]["improved"] == 1


def test_v1_report_never_loads_preprocessing_validation(monkeypatch):
    import ocr_preprocessing

    def forbidden(*args, **kwargs):
        pytest.fail("v1 reports must retain the original validation path")

    monkeypatch.setattr(ocr_preprocessing, "validate_metadata", forbidden)
    result = comparison.compare_recovery_report(_report(), _references())
    assert result["coverage"]["paired_pages"] == 1


@pytest.mark.parametrize("mode", ["none", "automatic", "contrast ", None, True, [], {}])
def test_v2_requires_an_enabled_exact_preprocessing_mode(mode):
    report = _preprocessed_report()
    report["retry_configuration"]["preprocessing"] = mode
    with pytest.raises(ValueError, match="enabled preprocessing"):
        comparison.validate_recovery_report(report)


@pytest.mark.parametrize("hybrid", [
    "v1_with_v2_configuration", "v1_with_v2_candidate", "v2_with_v1_configuration",
    "v2_with_v1_candidate", "v2_missing_metadata", "v2_with_original_coordinates",
])
def test_report_version_hybrids_fail_closed(hybrid):
    report = _report() if hybrid.startswith("v1") else _preprocessed_report()
    if hybrid == "v1_with_v2_configuration":
        report["retry_configuration"]["preprocessing"] = "contrast"
    elif hybrid == "v1_with_v2_candidate":
        report["pages"][0]["candidate"] = _preprocessed_report()["pages"][0]["candidate"]
    elif hybrid == "v2_with_v1_configuration":
        report["retry_configuration"].pop("preprocessing")
    elif hybrid == "v2_with_v1_candidate":
        report["pages"][0]["candidate"] = _candidate()
    elif hybrid == "v2_missing_metadata":
        report["pages"][0]["candidate"].pop("preprocessing")
    else:
        report["pages"][0]["candidate"]["raster"]["coordinate_system"] = "rendered_image_pixels"
    with pytest.raises(ValueError):
        comparison.validate_recovery_report(report)


@pytest.mark.parametrize(("path", "value"), [
    (("mode",), "deskew"), (("schema_version",), True),
    (("algorithm",), "PRIVATE_ALGORITHM"),
    (("original_raster", "width"), True), (("original_raster", "height"), 12001),
    (("processed_raster", "width"), 101),
    (("source_to_processed", 0, 2), 5.0),
    (("processed_to_source", 1, 1), 0.0),
    (("source_to_processed", 0, 0), float("nan")),
    (("processed_to_source", 0, 0), float("inf")),
    (("deskew", "angle_degrees"), 0.5),
    (("contrast", "status"), "PRIVATE_STATUS"),
    (("parameters", "min_gain"), 0.0),
])
def test_forged_v2_preprocessing_metadata_is_rejected(path, value):
    report = _preprocessed_report()
    _mutate(report["pages"][0]["candidate"]["preprocessing"], path, value)
    with pytest.raises(ValueError) as error:
        comparison.validate_recovery_report(report)
    assert "PRIVATE_" not in str(error.value)


@pytest.mark.parametrize("target", ["top", "candidate", "metadata"])
def test_unknown_v2_fields_are_not_silently_accepted(target):
    report = _preprocessed_report()
    owner = report if target == "top" else report["pages"][0]["candidate"]
    if target == "metadata":
        owner = owner["preprocessing"]
    owner["PRIVATE_FIELD"] = "PRIVATE_VALUE"
    with pytest.raises(ValueError) as error:
        comparison.validate_recovery_report(report)
    assert "PRIVATE_" not in str(error.value)


def test_v2_transform_validation_uses_report_resource_limits(monkeypatch):
    import ocr_preprocessing

    report = _preprocessed_report()
    report["retry_configuration"].update({"max_side": 100, "max_pixels": 10000})
    report["pages"][0]["candidate"]["engine"]["max_side"] = 100
    actual_validate = ocr_preprocessing.validate_metadata
    calls = []

    def observe(payload, **kwargs):
        calls.append(kwargs)
        return actual_validate(payload, **kwargs)

    monkeypatch.setattr(ocr_preprocessing, "validate_metadata", observe)
    comparison.validate_recovery_report(report)
    assert {"width": 100, "height": 100, "max_side": 100, "max_pixels": 10000} in calls


@pytest.mark.parametrize(("field", "limit"), [("max_side", 99), ("max_pixels", 9999)])
def test_v2_rasters_must_fit_configured_resource_bounds(field, limit):
    report = _preprocessed_report()
    report["retry_configuration"][field] = limit
    if field == "max_side":
        report["pages"][0]["candidate"]["engine"]["max_side"] = limit
    with pytest.raises(ValueError):
        comparison.validate_recovery_report(report)


def test_v2_original_raster_must_fit_policy_even_if_processed_raster_fits():
    report = _preprocessed_report("deskew")
    candidate = report["pages"][0]["candidate"]
    candidate["raster"].update({"width": 5981, "height": 498})
    candidate["preprocessing"].update({
        "original_raster": {"width": 6001, "height": 1},
        "processed_raster": {"width": 5981, "height": 498},
        "source_to_processed": [
            [0.9965655024977614, 0.08280820751220434, 0.2638056517107741],
            [-0.08280820751220434, 0.9965655024977614, 496.96774388912024],
        ],
        "processed_to_source": [
            [0.9965655024977614, -0.08280820751220434, 40.89010845098339],
            [0.08280820751220434, 0.9965655024977614, -495.2827546871897],
        ],
        "deskew": {"status": "applied", "reason": "rotation_applied",
                   "angle_degrees": 4.75, "estimated_angle_degrees": 4.75, "gain": 0.1},
    })
    # Geometry satisfies the candidate's broad schema ceiling, but its source
    # canvas exceeds the report's 6000-pixel policy even though its output fits.
    assert ocr_recovery._candidate_snapshot(candidate, preprocessing="deskew") == candidate
    with pytest.raises(ValueError, match="raster width"):
        comparison.validate_recovery_report(report)


def test_failed_v2_candidates_require_no_transform_and_remain_unpaired():
    report = _preprocessed_report(candidates={1: RuntimeError("PRIVATE_FAILURE")})
    assert report["pages"][0]["candidate"] is None
    result = comparison.compare_recovery_report(report, _references())
    assert result["comparison"] is None
    assert result["coverage"]["unpaired_pages"] == [{
        "page_number": 1, "reasons": ["retry_failed"],
        "baseline_available": True, "retry_available": False,
    }]
    assert result["requires_attention"] is True
    assert "PRIVATE_" not in json.dumps(result)


def test_empty_v2_candidate_is_scored_as_empty_not_lost_from_cohort():
    report = _preprocessed_report(candidates={1: ""})
    result = comparison.compare_recovery_report(report, _references())
    assert result["coverage"]["paired_pages"] == 1
    assert result["coverage"]["empty_candidate_pages"] == [1]
    assert result["comparison"]["records"][0]["retry"]["word"]["deletions"] == 1
    assert result["requires_attention"] is True


def test_v2_file_comparison_retains_exact_experiment_digest_without_text(artifact_paths):
    recovery_path, reference_path, output_path = artifact_paths
    report = _preprocessed_report(native=("PRIVATE_ORIGINAL",), candidates={1: "PRIVATE_CANDIDATE"})
    references = _references((1, "PRIVATE_REFERENCE"))
    raw = json.dumps(report).encode("utf-8")
    recovery_path.write_bytes(raw)
    reference_path.write_text(json.dumps(references), encoding="utf-8")
    result = comparison.compare_recovery_file(*artifact_paths)
    assert result["inputs"]["recovery_sha256"] == hashlib.sha256(raw).hexdigest()
    assert recovery_path.read_bytes() == raw
    assert "PRIVATE_" not in output_path.read_text(encoding="utf-8")
    assert json.loads(output_path.read_text(encoding="utf-8"))["schema_version"] == 1
