"""Source/plan binding and bounded region review, using fake PDF/OCR readers."""

import copy
import hashlib
import itertools
import json
import math
import os
from pathlib import Path

import pytest

import ocr_recovery
import ocr_regions as regions
import storage_policy


SOURCE = b"synthetic PDF stand-in: no private document"
SOURCE_HASH = hashlib.sha256(SOURCE).hexdigest()


def geometry(bbox, dpi=300):
    x0, y0 = math.floor(bbox[0] * dpi), math.floor(bbox[1] * dpi)
    x1, y1 = math.ceil(bbox[2] * dpi), math.ceil(bbox[3] * dpi)
    return {
        "page": {"display_rect_points": [0.0, 0.0, 72.0, 72.0],
                 "cropbox_points": [0.0, 0.0, 72.0, 72.0], "rotation_degrees": 0},
        "raster": {"width": x1-x0, "height": y1-y0, "dpi": dpi, "coordinate_system": "rendered_image_pixels"},
        "pixel_bounds": [x0, y0, x1, y1],
        "crop_to_page_fraction": [[1/dpi, 0.0, x0/dpi], [0.0, 1/dpi, y0/dpi]],
        "boundary_policy": "clip_to_page_bounds",
    }


def candidate(raster, text="synthetic region"):
    width, height = raster["width"], raster["height"]
    lines = [] if not text else [{"text": text, "score": 0.9,
                                 "box": [[0.0, 0.0], [width, 0.0], [width, height], [0.0, height]]}]
    return {"text": text, "lines": lines, "mean_confidence": 0.9 if lines else None,
            "raster": dict(raster), "engine": {"name": "rapidocr", "version": "synthetic",
                                               "min_score": 0.0, "max_side": 6000}}


class PageReader:
    page_count = 2

    def native_text(self, _number):
        return "This is a synthetic saved page context with enough native text."

    def retry(self, _number):
        return candidate(geometry([0, 0, 1, 1])["raster"], "saved page candidate")


class RegionReader:
    page_count = 2
    events = None
    hook = None

    def __init__(self, path, *, dpi, max_pixels, max_side):
        assert Path(path).read_bytes() == SOURCE
        assert max_pixels == 25_000_000 and max_side == 6000
        self.dpi = dpi

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.events.append("close")

    def describe_region(self, number, bbox):
        self.events.append(("describe", number))
        return geometry(bbox, self.dpi)

    def retry_region(self, number, bbox):
        self.events.append(("retry", number))
        if self.hook is not None:
            self.hook(number)
        description = geometry(bbox, self.dpi)
        return {"geometry": description, "candidate": candidate(description["raster"])}


@pytest.fixture
def files(tmp_path, monkeypatch):
    pdf, recovery, plan, output = [tmp_path / name for name in ("source.pdf", "recovery.json", "plan.json", "regions.json")]
    pdf.write_bytes(SOURCE)
    saved = ocr_recovery.build_recovery_report(
        PageReader(), source_sha256=SOURCE_HASH, policy=ocr_recovery.RetryPolicy(), requested_pages=(1, 2))
    saved["evidence_sha256"] = None
    recovery.write_text(json.dumps(saved), encoding="utf-8")
    planned = {"schema_version": 1, "source_sha256": SOURCE_HASH,
               "recovery_sha256": hashlib.sha256(recovery.read_bytes()).hexdigest(),
               "coordinate_system": "original_page_display_fraction",
               "regions": [{"region_id": "p1-r1", "page_number": 1, "bbox": [0.1, 0.2, 0.8, 0.9]}]}
    plan.write_text(json.dumps(planned), encoding="utf-8")
    monkeypatch.setattr(RegionReader, "events", [])
    monkeypatch.setattr(RegionReader, "hook", None)
    return {"paths": [pdf, recovery, plan, output], "plan": planned, "recovery": saved}


def run(files, **kwargs):
    return regions.recover_regions(*files["paths"], reader_factory=RegionReader, **kwargs)


def write_plan(files):
    files["paths"][2].write_text(json.dumps(files["plan"]), encoding="utf-8")


def validate_plan(plan, files):
    return regions.validate_region_plan(plan, source_sha256=SOURCE_HASH,
                                        recovery_sha256=files["plan"]["recovery_sha256"], page_count=2)


def test_region_plan_is_detached_and_sorted_without_changing_coordinate_contract(files):
    plan = files["plan"]
    plan["regions"] += [{"region_id": "a", "page_number": 1, "bbox": [0, 0, 1, 1]}]
    result = validate_plan(plan, files)
    assert [r["region_id"] for r in result["regions"]] == ["a", "p1-r1"]
    result["regions"][0]["bbox"][0] = 0.2
    assert plan["regions"][1]["bbox"][0] == 0


@pytest.mark.parametrize("bbox", [None, [], [0, 0, 1], [True, 0, 1, 1], [0, 0, float("nan"), 1],
                                  [0, 0, float("inf"), 1], [0, 0, 10**1000, 1], [-0.1, 0, 1, 1],
                                  [0, 0, 1.1, 1], [0.5, 0, 0.4, 1], [0, 1, 1, 1], ["0", 0, 1, 1]])
def test_invalid_region_bounds_fail_closed(files, bbox):
    files["plan"]["regions"][0]["bbox"] = bbox
    with pytest.raises(ValueError):
        validate_plan(files["plan"], files)


@pytest.mark.parametrize("field,value", [("region_id", ""), ("region_id", "../unsafe"), ("region_id", "x"*65),
                                         ("region_id", "\u6cd5"), ("page_number", True), ("page_number", 0),
                                         ("page_number", 3), ("page_number", 1.0)])
def test_invalid_region_ids_and_page_numbers(files, field, value):
    files["plan"]["regions"][0][field] = value
    with pytest.raises(ValueError):
        validate_plan(files["plan"], files)


@pytest.mark.parametrize("mutation", ["extra", "version_bool", "wrong_source", "wrong_recovery", "candidate_coordinates", "empty", "duplicate", "too_many"])
def test_plan_header_count_and_source_contracts(files, mutation):
    plan = copy.deepcopy(files["plan"])
    if mutation == "extra":
        plan["extra"] = "private"
    elif mutation == "version_bool":
        plan["schema_version"] = True
    elif mutation.startswith("wrong_"):
        plan["source_sha256" if mutation == "wrong_source" else "recovery_sha256"] = "b"*64
    elif mutation == "candidate_coordinates":
        plan["coordinate_system"] = "candidate_raster_fraction"
    elif mutation == "empty":
        plan["regions"] = []
    elif mutation == "duplicate":
        plan["regions"] *= 2
    else:
        plan["regions"] = [{**plan["regions"][0], "region_id": str(i)} for i in range(21)]
    with pytest.raises(ValueError):
        validate_plan(plan, files)


@pytest.mark.parametrize("dpi", [300, 400])
def test_private_source_bound_report_preserves_context_and_coordinate_mapping(files, dpi):
    before = [path.read_bytes() for path in files["paths"][:3]]
    result = run(files, dpi=dpi)
    assert regions.validate_region_review(result) == result
    assert regions.load_region_review(files["paths"][3]) == result
    assert result["kind"] == "ocr_region_review" and result["requires_attention"] is True
    assert result["summary"] == {"requested": 1, "planned_pixels": int(0.7*dpi)**2,
                                 "candidates": 1, "empty": 0, "failed": 0}
    assert result["page_contexts"][0]["recovery_page"] == files["recovery"]["pages"][0]
    assert result["regions"][0]["candidate"]["text"] == "synthetic region"
    assert result["regions"][0]["page_boxes"][0][0] == pytest.approx([0.1, 0.2])
    assert result["regions"][0]["page_boxes"][0][2] == pytest.approx([0.8, 0.9])
    assert RegionReader.events == [("describe", 1), ("retry", 1), "close"]
    assert [path.read_bytes() for path in files["paths"][:3]] == before
    output = files["paths"][3]
    if os.name == "nt":
        assert storage_policy.windows_path_is_private(output, directory=False)
    else:
        assert output.stat().st_mode & 0o777 == 0o600
    assert result["inputs"] == {"pdf_sha256": SOURCE_HASH,
                                "recovery_sha256": hashlib.sha256(before[1]).hexdigest(),
                                "plan_sha256": hashlib.sha256(before[2]).hexdigest()}


def test_worker_report_binding_checks_current_inputs_without_pdf_or_ocr(files):
    report = run(files)
    events = list(RegionReader.events)
    pdf, recovery, plan, _output = files["paths"]
    checked = regions.verify_region_review_request(
        report, pdf_path=pdf, recovery_path=recovery, plan_path=plan, dpi=300)
    assert checked == report and checked is not report
    assert RegionReader.events == events


@pytest.mark.parametrize("mismatch", ["dpi", "source", "recovery", "plan_digest", "page_context"])
def test_supervised_cli_rejects_valid_but_different_worker_report(files, monkeypatch, capsys, mismatch):
    from tools import retry_ocr_regions as cli

    report = run(files, dpi=400 if mismatch == "dpi" else 300)
    if mismatch == "source":
        report["source_sha256"] = report["plan"]["source_sha256"] = report["inputs"]["pdf_sha256"] = "b" * 64
    elif mismatch == "recovery":
        report["recovery_sha256"] = report["plan"]["recovery_sha256"] = report["inputs"]["recovery_sha256"] = "b" * 64
    elif mismatch == "plan_digest":
        report["inputs"]["plan_sha256"] = "b" * 64
    elif mismatch == "page_context":
        report["page_contexts"][0]["recovery_page"]["original_text"] = "Different synthetic baseline context."
    assert regions.validate_region_review(report) == report
    monkeypatch.setattr(cli, "run_with_deadline", lambda *_args, **_kwargs: 3)
    monkeypatch.setattr(cli, "load_region_review", lambda _path: report)
    pdf, recovery, plan, output = files["paths"]
    assert cli.main(["--pdf", str(pdf), "--recovery", str(recovery), "--plan", str(plan),
                     "--output", str(output), "--timeout-seconds", "2"]) == 2
    captured = capsys.readouterr()
    assert "requested," not in captured.out and "could not complete" in captured.err
    assert str(pdf.parent) not in captured.out + captured.err


@pytest.mark.parametrize("input_index", [0, 1, 2])
def test_worker_report_binding_rejects_changed_input_generations(files, input_index):
    report = run(files)
    changed = files["paths"][input_index]
    changed.write_bytes(changed.read_bytes() + b" ")
    pdf, recovery, plan, _output = files["paths"]
    with pytest.raises((RuntimeError, ValueError)):
        regions.verify_region_review_request(
            report, pdf_path=pdf, recovery_path=recovery, plan_path=plan, dpi=300)


@pytest.mark.parametrize("input_index", [1, 2])
def test_worker_report_binding_rechecks_json_after_source_hash(files, monkeypatch, input_index):
    report = run(files)
    actual = regions.artifact_io.hash_file_generation

    def changed(*args, **kwargs):
        result = actual(*args, **kwargs)
        path = files["paths"][input_index]
        path.write_bytes(path.read_bytes() + b" ")
        return result

    monkeypatch.setattr(regions.artifact_io, "hash_file_generation", changed)
    pdf, recovery, plan, _output = files["paths"]
    with pytest.raises(RuntimeError, match="changed"):
        regions.verify_region_review_request(
            report, pdf_path=pdf, recovery_path=recovery, plan_path=plan, dpi=300)


def test_all_geometry_preflight_precedes_any_retry_and_context_is_deduplicated(files):
    files["plan"]["regions"] += [{"region_id": "p1-r2", "page_number": 1, "bbox": [0, 0, 0.1, 0.1]}]
    write_plan(files)
    report = run(files)
    assert len(report["page_contexts"]) == 1
    assert RegionReader.events == [("describe", 1), ("describe", 1), ("retry", 1), ("retry", 1), "close"]


def test_aggregate_pixel_limit_rejects_plan_before_ocr(files, monkeypatch):
    monkeypatch.setattr(regions, "MAX_TOTAL_PIXELS", 10)
    with pytest.raises(ValueError, match="aggregate"):
        run(files)
    assert RegionReader.events == [("describe", 1), "close"]
    assert not files["paths"][3].exists()


@pytest.mark.parametrize("dpi", [True, "300", 300.0, 72, 600, 0])
def test_dpi_is_strict_before_reading_or_models(files, dpi):
    with pytest.raises(ValueError, match="DPI"):
        run(files, dpi=dpi)
    assert RegionReader.events == []


@pytest.mark.parametrize("error,code", [(ValueError("private"), "retry_limit_or_validation"),
                                       (ImportError("private"), "retry_runtime_unavailable"),
                                       (RuntimeError("private"), "retry_runtime_failed")])
def test_retry_failures_are_not_fabricated_empty_candidates(files, monkeypatch, error, code):
    def fail(*_args):
        raise error

    monkeypatch.setattr(RegionReader, "hook", fail)
    report = run(files)
    item = report["regions"][0]
    assert item["status"] == "retry_failed" and item["candidate"] is None and item["page_boxes"] is None
    assert item["error_code"] == code
    assert "private" not in json.dumps(report)
    assert report["summary"]["failed"] == 1 and report["summary"]["empty"] == 0


def test_true_empty_region_is_preserved_and_flagged(files, monkeypatch):
    def empty(self, number, bbox):
        description = geometry(bbox, self.dpi)
        return {"geometry": description, "candidate": candidate(description["raster"], "")}

    monkeypatch.setattr(RegionReader, "retry_region", empty)
    report = run(files)
    assert report["summary"]["empty"] == 1
    assert report["regions"][0]["candidate"]["text"] == ""
    assert report["regions"][0]["page_boxes"] == []


@pytest.mark.parametrize("input_index", [0, 1, 2])
def test_every_source_generation_is_rechecked_before_publication(files, monkeypatch, input_index):
    def change(*_args):
        path = files["paths"][input_index]
        path.write_bytes(path.read_bytes() + b" ")

    monkeypatch.setattr(RegionReader, "hook", change)
    with pytest.raises(RuntimeError):
        run(files)
    assert not files["paths"][3].exists()
    assert RegionReader.events[-1] == "close"


@pytest.mark.parametrize("input_index", [0, 1, 2])
def test_late_input_change_after_private_serialization_prevents_commit(files, monkeypatch, input_index):
    actual = regions.storage_policy.atomic_write_private_json
    changed = files["paths"][input_index]
    new_bytes = changed.read_bytes() + b" "
    reached_commit = []

    def staging(path, payload, **kwargs):
        commit = kwargs["replace_fn"]

        def change_then_commit(temporary, destination):
            assert temporary.exists() and not destination.exists()
            changed.write_bytes(new_bytes)
            reached_commit.append(True)
            return commit(temporary, destination)

        kwargs["replace_fn"] = change_then_commit
        return actual(path, payload, **kwargs)

    monkeypatch.setattr(regions.storage_policy, "atomic_write_private_json", staging)
    with pytest.raises(RuntimeError):
        run(files)
    assert reached_commit == [True]
    assert not files["paths"][3].exists()
    assert changed.read_bytes() == new_bytes
    assert RegionReader.events[-1] == "close"


def test_cancellation_propagates_and_does_not_publish(files, monkeypatch):
    def cancel(*_args):
        raise KeyboardInterrupt

    monkeypatch.setattr(RegionReader, "hook", cancel)
    with pytest.raises(KeyboardInterrupt):
        run(files)
    assert not files["paths"][3].exists()
    assert RegionReader.events[-1] == "close"


def test_wrong_pdf_is_rejected_before_reader_is_constructed(files):
    files["paths"][0].write_bytes(b"other synthetic source")
    with pytest.raises(RuntimeError):
        run(files)
    assert RegionReader.events == []


@pytest.mark.parametrize("left,right", list(itertools.combinations(range(4), 2)))
def test_hardlink_aliases_are_rejected_before_reader(files, left, right):
    alias = files["paths"][right].with_name("alias")
    os.link(files["paths"][left], alias)
    files["paths"][right] = alias
    with pytest.raises(ValueError, match="distinct"):
        run(files)
    assert RegionReader.events == []


def test_existing_or_racing_output_is_never_overwritten(files, monkeypatch):
    def occupy(*_args):
        files["paths"][3].write_bytes(b"other report")

    monkeypatch.setattr(RegionReader, "hook", occupy)
    with pytest.raises(FileExistsError):
        run(files)
    assert files["paths"][3].read_bytes() == b"other report"
    RegionReader.events.clear()
    with pytest.raises(FileExistsError):
        run(files)
    assert RegionReader.events == []


def test_output_byte_budget_refuses_oversized_review_before_publication(files, monkeypatch):
    monkeypatch.setattr(regions, "MAX_REPORT_BYTES", 100)
    with pytest.raises(ValueError, match="output byte budget"):
        run(files)
    assert not files["paths"][3].exists()


@pytest.mark.parametrize("mutation", ["version", "kind", "attention", "summary", "mapping", "status", "plan", "inputs", "context", "page_boxes"])
def test_report_loader_rejects_inconsistent_contracts(files, mutation):
    report = run(files)
    if mutation == "version":
        report["schema_version"] = True
    elif mutation == "kind":
        report["kind"] = "ocr_recovery_review"
    elif mutation == "attention":
        report["requires_attention"] = False
    elif mutation == "summary":
        report["summary"]["requested"] = True
    elif mutation == "mapping":
        report["regions"][0]["geometry"]["crop_to_page_fraction"][0][2] += 0.1
    elif mutation == "status":
        report["regions"][0]["status"] = []
    elif mutation == "plan":
        report["regions"][0]["bbox"][0] += 0.1
    elif mutation == "inputs":
        report["inputs"]["pdf_sha256"] = "b" * 64
    elif mutation == "context":
        report["page_contexts"][0]["status"] = []
    else:
        report["regions"][0]["page_boxes"][0][0][0] = True
    with pytest.raises(ValueError):
        regions.validate_region_review(report)


@pytest.mark.parametrize("fault,error_type,match", [
    ("reader_error", RuntimeError, "geometry unavailable"),
    ("invalid_geometry", ValueError, "raster"),
])
def test_later_geometry_failure_aborts_all_retries(files, monkeypatch, fault, error_type, match):
    files["plan"]["regions"].append(
        {"region_id": "p2-r1", "page_number": 2, "bbox": [0, 0, 0.1, 0.1]})
    write_plan(files)
    describe = RegionReader.describe_region

    def fail_second_geometry(self, number, bbox):
        result = describe(self, number, bbox)
        if number == 2:
            if fault == "reader_error":
                raise RuntimeError("geometry unavailable")
            result["raster"]["width"] = 0
        return result

    monkeypatch.setattr(RegionReader, "describe_region", fail_second_geometry)
    with pytest.raises(error_type, match=match):
        run(files)
    assert RegionReader.events == [("describe", 1), ("describe", 2), "close"]
    assert not files["paths"][3].exists()


@pytest.mark.parametrize("fault", [
    "result_fields", "geometry_drift", "candidate_shape", "candidate_policy", "candidate_geometry",
])
def test_retry_result_validation_stays_local_and_context_follows_all_attempts(files, monkeypatch, fault):
    files["plan"]["regions"].append(
        {"region_id": "p2-r1", "page_number": 2, "bbox": [0, 0, 0.1, 0.1]})
    write_plan(files)
    retry = RegionReader.retry_region
    context = regions._context

    def invalid_first_result(self, number, bbox):
        result = retry(self, number, bbox)
        if number == 1:
            if fault == "result_fields":
                result["unexpected"] = True
            elif fault == "geometry_drift":
                result["geometry"]["pixel_bounds"][0] += 1
            elif fault == "candidate_shape":
                result["candidate"]["lines"][0]["score"] = float("nan")
            elif fault == "candidate_policy":
                result["candidate"]["engine"]["max_side"] = 3000
            else:
                result["candidate"]["raster"]["width"] += 1
        return result

    def observe_context(recovery, numbers):
        RegionReader.events.append(("context", tuple(sorted(numbers))))
        return context(recovery, numbers)

    monkeypatch.setattr(RegionReader, "retry_region", invalid_first_result)
    monkeypatch.setattr(regions, "_context", observe_context)
    report = run(files)
    assert RegionReader.events == [
        ("describe", 1), ("describe", 2), ("retry", 1), ("retry", 2),
        ("context", (1, 2)), "close",
    ]
    assert [(item["region_id"], item["status"], item["error_code"])
            for item in report["regions"]] == [
        ("p1-r1", "retry_failed", "retry_limit_or_validation"),
        ("p2-r1", "review_required", None),
    ]
    assert report["regions"][0]["candidate"] is None
    assert report["regions"][0]["page_boxes"] is None
    assert report["regions"][0]["geometry"] == geometry([0.1, 0.2, 0.8, 0.9])
    assert report["summary"] == {
        "requested": 2, "planned_pixels": 45000, "candidates": 1, "empty": 0, "failed": 1,
    }
    assert [item["page_number"] for item in report["page_contexts"]] == [1, 2]
    assert regions.load_region_review(files["paths"][3]) == report


def test_page_box_mapping_error_propagates_before_later_retry_or_context(files, monkeypatch):
    files["plan"]["regions"].append(
        {"region_id": "p2-r1", "page_number": 2, "bbox": [0, 0, 0.1, 0.1]})
    write_plan(files)
    error = ValueError("coordinate transform failed")

    def fail_mapping(_candidate, _geometry):
        RegionReader.events.append("page_boxes")
        raise error

    def unexpected_context(_recovery, _numbers):
        raise AssertionError("context assembled after mapping error")

    monkeypatch.setattr(regions, "_page_boxes", fail_mapping)
    monkeypatch.setattr(regions, "_context", unexpected_context)
    with pytest.raises(ValueError, match="coordinate transform failed") as caught:
        run(files)
    assert caught.value is error
    assert RegionReader.events == [
        ("describe", 1), ("describe", 2), ("retry", 1), "page_boxes", "close",
    ]
    assert not files["paths"][3].exists()
