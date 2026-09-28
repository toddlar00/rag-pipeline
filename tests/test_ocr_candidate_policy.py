"""Synthetic-only candidate geometry and shared writer/import policy checks."""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

import ocr_recovery as recovery
import ocr_recovery_comparison as comparison
import ocr_recovery_runtime as runtime


DIGEST = "a" * 64
MODES = ("none", "deskew", "contrast", "deskew-contrast")
ZERO_AREA_BOXES = (
    [[1, 1], [1, 1], [1, 1], [1, 1]],
    [[0, 0], [10, 10], [20, 20], [30, 30]],
    [[0, 0], [20, 20], [0, 20], [20, 0]],
)


def _metadata(mode, width, height):
    return {
        "schema_version": 1, "algorithm": "bounded-deskew-contrast-v1", "mode": mode,
        "original_raster": {"width": width, "height": height},
        "processed_raster": {"width": width, "height": height},
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
        "libraries": {"opencv": "synthetic", "numpy": "synthetic"},
    }


def _candidate(policy, *, width=100, height=100, text="Recovered synthetic text"):
    lines = [{"text": text, "score": 0.9,
              "box": [[0, 0], [30, 0], [30, 20], [0, 20]]}] if text else []
    candidate = {
        "text": text, "lines": lines, "mean_confidence": 0.9 if lines else None,
        "raster": {"width": width, "height": height, "dpi": policy.dpi,
                   "coordinate_system": "rendered_image_pixels" if policy.preprocessing == "none"
                   else "preprocessed_image_pixels"},
        "engine": {"name": "rapidocr", "version": "synthetic",
                   "min_score": 0.0, "max_side": policy.max_side},
    }
    if policy.preprocessing != "none":
        candidate["preprocessing"] = _metadata(policy.preprocessing, width, height)
    return candidate


class _Reader:
    def __init__(self, candidates):
        self.candidates = candidates
        self.page_count = len(candidates)
        self.retry_calls = []
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True
        return False

    def native_text(self, number):
        return f"Original synthetic page {number}."

    def retry(self, number):
        self.retry_calls.append(number)
        return self.candidates[number - 1]


def _report(reader, policy):
    report = recovery.build_recovery_report(reader, source_sha256=DIGEST, policy=policy)
    report["evidence_sha256"] = None
    return report


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("box", ZERO_AREA_BOXES, ids=("point", "collinear", "crossed"))
def test_snapshot_rejects_zero_area_like_runtime(mode, box):
    policy = recovery.RetryPolicy(preprocessing=mode)
    candidate = _candidate(policy)
    candidate["lines"][0]["box"] = copy.deepcopy(box)
    before = copy.deepcopy(candidate)
    output = SimpleNamespace(txts=[candidate["text"]], scores=[0.9], boxes=[box])
    with pytest.raises(ValueError):
        runtime._validated_lines(output, width=100, height=100, min_score=0.0)
    with pytest.raises(ValueError):
        recovery._candidate_snapshot(candidate, preprocessing=mode)
    assert candidate == before


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("violation", ("dpi", "engine_side", "width", "height", "area", "box"))
def test_writer_rejects_policy_violation_locally_and_continues(mode, violation):
    policy = recovery.RetryPolicy(preprocessing=mode, max_side=100, max_pixels=10_000)
    invalid = _candidate(policy, width=101 if violation == "width" else 100,
                         height=101 if violation == "height" else 100)
    if violation == "dpi":
        invalid["raster"]["dpi"] = 400
    elif violation == "engine_side":
        invalid["engine"]["max_side"] = 101
    elif violation == "area":
        policy = recovery.RetryPolicy(preprocessing=mode, max_side=100, max_pixels=9_999)
    elif violation == "box":
        invalid["lines"][0]["box"] = copy.deepcopy(ZERO_AREA_BOXES[0])
    valid = _candidate(policy, width=90, height=90)
    reader = _Reader([invalid, valid])
    originals = copy.deepcopy(reader.candidates)
    report = _report(reader, policy)
    failed, continued = report["pages"]
    assert failed["status"] == "retry_failed"
    assert failed["error_code"] == "retry_limit_or_validation"
    assert failed["candidate"] is None
    assert failed["original_text"] == "Original synthetic page 1."
    assert continued["status"] == "review_required"
    assert continued["candidate"] == valid
    assert reader.retry_calls == [1, 2]
    assert reader.candidates == originals
    assert report["summary"]["failed"] == report["summary"]["review_required"] == 1
    imported = json.loads(json.dumps(report))
    assert comparison.validate_recovery_report(imported) is imported


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("box", ZERO_AREA_BOXES, ids=("point", "collinear", "crossed"))
def test_import_rejects_zero_area_without_mutating_report(mode, box):
    policy = recovery.RetryPolicy(preprocessing=mode)
    report = _report(_Reader([_candidate(policy)]), policy)
    report["pages"][0]["candidate"]["lines"][0]["box"] = copy.deepcopy(box)
    before = copy.deepcopy(report)
    with pytest.raises(ValueError):
        comparison.validate_recovery_report(report)
    assert report == before


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("reverse", (False, True), ids=("forward", "reverse"))
def test_positive_area_accepts_both_box_windings_and_detaches(mode, reverse):
    policy = recovery.RetryPolicy(preprocessing=mode)
    candidate = _candidate(policy)
    if reverse:
        candidate["lines"][0]["box"].reverse()
    before = copy.deepcopy(candidate)
    output = SimpleNamespace(txts=[candidate["text"]], scores=[0.9],
                             boxes=[candidate["lines"][0]["box"]])
    assert runtime._validated_lines(output, width=100, height=100, min_score=0.0)
    detached = recovery._candidate_snapshot(candidate, preprocessing=mode)
    assert detached == candidate == before
    detached["lines"][0]["box"][0][0] = 99
    assert candidate == before


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(("options", "width", "height"), [
    ({}, 100, 100),
    ({"dpi": 72, "max_side": 32, "max_pixels": 1024}, 32, 32),
    ({"dpi": 400, "max_side": 64, "max_pixels": 2048}, 32, 64),
    ({"dpi": 600}, 5000, 5000),
    ({"max_pixels": 6000 * 40}, 6000, 40),
], ids=("default", "minimum", "custom_rectangle", "maximum_area", "maximum_side"))
@pytest.mark.parametrize("text", ("Recovered synthetic text", ""), ids=("text", "empty"))
def test_valid_default_and_exact_custom_limits_roundtrip(mode, options, width, height, text):
    policy = recovery.RetryPolicy(preprocessing=mode, **options)
    candidate = _candidate(policy, width=width, height=height, text=text)
    before = copy.deepcopy(candidate)
    report = _report(_Reader([candidate]), policy)
    assert report["schema_version"] == (1 if mode == "none" else 2)
    page = report["pages"][0]
    assert page["status"] == ("review_required" if text else "empty_candidate")
    assert page["error_code"] is None
    assert page["candidate"] == candidate
    imported = json.loads(json.dumps(report))
    assert comparison.validate_recovery_report(imported) is imported
    page["candidate"]["engine"]["version"] = "changed report only"
    assert candidate == before


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(("field", "value"), [
    ("dpi", 400), ("engine_side", 101), ("width", 101), ("height", 101), ("area", 9999),
])
@pytest.mark.parametrize("text", ("Recovered synthetic text", ""), ids=("text", "empty"))
def test_import_keeps_policy_mismatch_rejection_for_nonempty_and_empty_candidates(mode, field, value, text):
    policy = recovery.RetryPolicy(preprocessing=mode, max_side=100, max_pixels=10_000)
    report = _report(_Reader([_candidate(policy, text=text)]), policy)
    # The invalid empty candidate must not bypass policy checks just because
    # there are no detected lines or boxes to validate.
    candidate = report["pages"][0]["candidate"]
    if field == "engine_side":
        candidate["engine"]["max_side"] = value
    elif field == "area":
        report["retry_configuration"]["max_pixels"] = value
    else:
        candidate["raster"][field] = value
        if field in ("width", "height") and mode != "none":
            for raster in ("original_raster", "processed_raster"):
                candidate["preprocessing"][raster][field] = value
    before = copy.deepcopy(report)
    with pytest.raises(ValueError):
        comparison.validate_recovery_report(report)
    assert report == before


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("field", ("dpi", "engine_side"))
def test_writer_checks_settings_even_for_empty_candidates(mode, field):
    policy = recovery.RetryPolicy(preprocessing=mode)
    invalid = _candidate(policy, text="")
    if field == "dpi":
        invalid["raster"]["dpi"] = 400
    else:
        invalid["engine"]["max_side"] = 5000
    report = _report(_Reader([invalid]), policy)
    assert report["pages"][0]["status"] == "retry_failed"
    assert report["pages"][0]["error_code"] == "retry_limit_or_validation"
    assert comparison.validate_recovery_report(report) is report


def test_shared_policy_still_checks_original_preprocessing_canvas_when_output_fits():
    policy = recovery.RetryPolicy(preprocessing="deskew")
    invalid = _candidate(policy, width=5981, height=498)
    invalid["preprocessing"].update({
        "original_raster": {"width": 6001, "height": 1},
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
    before = copy.deepcopy(invalid)
    # The broad structural ceiling accepts the real expanded affine geometry.
    assert recovery._candidate_snapshot(invalid, preprocessing="deskew") == invalid
    with pytest.raises(ValueError):
        recovery._validate_candidate_policy(invalid, policy)
    report = _report(_Reader([invalid, _candidate(policy)]), policy)
    assert report["pages"][0]["error_code"] == "retry_limit_or_validation"
    assert report["pages"][1]["status"] == "review_required"
    assert comparison.validate_recovery_report(report) is report
    forged = _report(_Reader([_candidate(policy)]), policy)
    forged["pages"][0]["candidate"] = invalid
    with pytest.raises(ValueError):
        comparison.validate_recovery_report(forged)
    assert invalid == before


@pytest.mark.parametrize("mode", MODES)
def test_writer_and_importer_delegate_to_shared_policy_on_detached_candidates(mode, monkeypatch):
    policy = recovery.RetryPolicy(preprocessing=mode, dpi=400, max_side=100, max_pixels=10_000)
    original = _candidate(policy)
    original_before = copy.deepcopy(original)
    actual = recovery._validate_candidate_policy
    calls = []

    def observe(candidate, observed_policy):
        calls.append((candidate, observed_policy))
        return actual(candidate, observed_policy)

    monkeypatch.setattr(recovery, "_validate_candidate_policy", observe)
    report = _report(_Reader([original]), policy)
    assert len(calls) == 1
    assert calls[0][0] is not original
    assert calls[0][1] == policy
    calls.clear()
    assert comparison.validate_recovery_report(report) is report
    assert len(calls) == 1
    assert calls[0][0] is not report["pages"][0]["candidate"]
    assert calls[0][1] == policy
    assert original == original_before


@pytest.mark.parametrize("mode", MODES)
def test_shared_policy_failure_is_local_and_malformed_candidate_never_reaches_it(mode, monkeypatch):
    policy = recovery.RetryPolicy(preprocessing=mode)
    malformed = _candidate(policy)
    malformed["unexpected"] = "synthetic"
    rejected = _candidate(policy, text="Rejected synthetic candidate")
    accepted = _candidate(policy)
    actual = recovery._validate_candidate_policy
    calls = []

    def reject(candidate, observed_policy):
        calls.append(candidate["text"])
        actual(candidate, observed_policy)
        if candidate["text"] == rejected["text"]:
            raise ValueError("synthetic policy rejection")

    monkeypatch.setattr(recovery, "_validate_candidate_policy", reject)
    report = _report(_Reader([malformed, rejected, accepted]), policy)
    assert calls == [rejected["text"], accepted["text"]]
    assert [page["error_code"] for page in report["pages"]] == [
        "retry_limit_or_validation", "retry_limit_or_validation", None,
    ]
    assert [page["original_text"] for page in report["pages"]] == [
        f"Original synthetic page {number}." for number in (1, 2, 3)
    ]
    assert comparison.validate_recovery_report(report) is report


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("violation", ("dpi", "box"))
def test_published_failure_report_is_accepted_by_strict_import(mode, violation, tmp_path, monkeypatch):
    # Arbitrary synthetic bytes exercise snapshot/publication only. The fake
    # reader never parses a PDF, imports image libraries, or loads a model.
    source = tmp_path / "synthetic-source.bin"
    output = tmp_path / "review.json"
    source_bytes = b"synthetic candidate policy source generation"
    source.write_bytes(source_bytes)
    policy = recovery.RetryPolicy(preprocessing=mode)
    invalid = _candidate(policy)
    if violation == "dpi":
        invalid["raster"]["dpi"] = 400
    else:
        invalid["lines"][0]["box"] = copy.deepcopy(ZERO_AREA_BOXES[0])
    reader = _Reader([invalid, _candidate(policy)])
    before = copy.deepcopy(reader.candidates)
    actual_snapshot = recovery.artifact_io.immutable_file_snapshot

    def snapshot(path, **kwargs):
        return actual_snapshot(path, scratch_root=tmp_path / "scratch", **kwargs)

    def factory(path, **options):
        assert options == {
            "dpi": policy.dpi, "max_side": policy.max_side, "max_pixels": policy.max_pixels,
            **({"preprocessing": mode} if mode != "none" else {}),
        }
        return reader

    monkeypatch.setattr(recovery.artifact_io, "immutable_file_snapshot", snapshot)
    report = recovery.recover_pdf(source, output, policy=policy, reader_factory=factory)
    published = json.loads(output.read_text(encoding="utf-8"))
    assert published == report
    assert comparison.validate_recovery_report(published) is published
    assert published["pages"][0]["status"] == "retry_failed"
    assert published["pages"][0]["error_code"] == "retry_limit_or_validation"
    assert published["pages"][1]["status"] == "review_required"
    assert published["pages"][0]["original_text"] == "Original synthetic page 1."
    assert reader.closed and reader.retry_calls == [1, 2]
    assert reader.candidates == before
    assert source.read_bytes() == source_bytes
