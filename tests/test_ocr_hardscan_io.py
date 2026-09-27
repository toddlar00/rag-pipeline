"""Private hard-scan report lifecycle using synthetic stand-ins, never PDF/model inputs."""

import copy
import hashlib
import itertools
import json
import math
import os
from pathlib import Path

import pytest

import ocr_hardscan as policy
import ocr_hardscan_io as workflow
import ocr_recovery
import storage_policy


SOURCE = b"synthetic hard-scan PDF stand-in"
SOURCE_HASH = hashlib.sha256(SOURCE).hexdigest()


def recipe(angle=0, bow=0.):
    return {"orientation_clockwise": angle, "bow_fraction": bow, "illumination": "none",
            "bow_assumption": "parallel_horizontal_baselines" if bow else "none"}


def candidate(raster, text="synthetic candidate"):
    width, height = raster["width"], raster["height"]
    return {"text": text, "lines": [{"text": text, "score": .9,
                                      "box": [[0., 0.], [width, 0.], [width, height], [0., height]]}] if text else [],
            "mean_confidence": .9 if text else None, "raster": raster,
            "engine": {"name": "rapidocr", "version": "synthetic", "min_score": 0., "max_side": 6000}}


class PageReader:
    page_count = 2

    def native_text(self, _page):
        return "Synthetic native context retained for review, never region ground truth."

    def retry(self, _page):
        return candidate({"width": 300, "height": 300, "dpi": 300, "coordinate_system": "rendered_image_pixels"})


class HardReader:
    page_count = 2
    events = []
    hook = None

    def __init__(self, path, *, dpi, max_pixels, max_side):
        assert Path(path).read_bytes() == SOURCE
        assert (max_pixels, max_side) == (25_000_000, 6000)
        self.dpi = dpi

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.events.append("close")

    def describe_hardscan(self, page, bbox, selected_recipe):
        self.events.append(("describe", page))
        x0, y0 = math.floor(bbox[0]*self.dpi), math.floor(bbox[1]*self.dpi)
        x1, y1 = math.ceil(bbox[2]*self.dpi), math.ceil(bbox[3]*self.dpi)
        geometry = {
            "page": {"display_rect_points": [0., 0., 72., 72.], "cropbox_points": [0., 0., 72., 72.], "rotation_degrees": 0},
            "raster": {"width": x1-x0, "height": y1-y0, "dpi": self.dpi, "coordinate_system": "rendered_image_pixels"},
            "pixel_bounds": [x0, y0, x1, y1],
            "crop_to_page_fraction": [[1/self.dpi, 0., x0/self.dpi], [0., 1/self.dpi, y0/self.dpi]],
            "boundary_policy": "clip_to_page_bounds"}
        return {"geometry": geometry, "transform": policy.describe_transform(x1-x0, y1-y0, selected_recipe)}

    def retry_hardscan(self, page, bbox, selected_recipe):
        result = self.describe_hardscan(page, bbox, selected_recipe)
        self.events.append(("retry", page))
        if self.hook is not None:
            self.hook(page)
        dimensions = result["transform"]["processed_raster"]
        result.update(processing={"status": "completed", "reason": None, "libraries": {"opencv": "synthetic", "numpy": "synthetic"},
                                  "illumination": {"status": "disabled", "reason": "mode_disabled", "background_low": None,
                                                   "background_high": None, "ink_fraction": None}},
                      candidate=candidate({**dimensions, "dpi": self.dpi, "coordinate_system": "hardscan_image_pixels"}))
        return result


@pytest.fixture
def files(tmp_path, monkeypatch):
    paths = [tmp_path/name for name in ("source.pdf", "recovery.json", "plan.json", "output.json")]
    paths[0].write_bytes(SOURCE)
    recovery = ocr_recovery.build_recovery_report(PageReader(), source_sha256=SOURCE_HASH,
                                                  requested_pages=(1, 2), policy=ocr_recovery.RetryPolicy())
    recovery["evidence_sha256"] = None
    paths[1].write_text(json.dumps(recovery), encoding="utf-8")
    planned = workflow.create_plan(paths[1], paths[2], [
        {"region_id": "r1", "page_number": 1, "bbox": [.1, .2, .8, .9], "recipe": recipe()}], confirmed=True)
    monkeypatch.setattr(HardReader, "events", [])
    monkeypatch.setattr(HardReader, "hook", None)
    return {"paths": paths, "plan": planned, "recovery": recovery}


def run(files, **kwargs):
    return workflow.recover_hardscan(*files["paths"], reader_factory=HardReader, **kwargs)


def write_plan(files):
    files["paths"][2].write_text(json.dumps(files["plan"]), encoding="utf-8")


@pytest.mark.parametrize("angle", [0, 90, 180, 270])
@pytest.mark.parametrize("bow", [0., .02, -.02])
def test_source_bound_private_report_retains_context_and_true_nonlinear_polygons(files, angle, bow):
    files["plan"]["regions"][0]["recipe"] = recipe(angle, bow)
    write_plan(files)
    before = [path.read_bytes() for path in files["paths"][:3]]
    result = run(files)
    assert workflow.validate_report(result) == result
    assert workflow.load_report(files["paths"][3]) == result
    assert result["baseline_recovery"] == files["recovery"]
    assert result["summary"]["candidates"] == 1 and result["requires_attention"] is True
    item = result["regions"][0]
    assert len(item["source_polygons"][0]) > 4 if bow else len(item["source_polygons"][0]) == 4
    assert all(0 <= coordinate <= 1 for polygon in item["source_polygons"] for point in polygon for coordinate in point)
    pdf, recovery, plan, output = files["paths"]
    assert workflow.verify_request(result, pdf_path=pdf, recovery_path=recovery, plan_path=plan, dpi=300) == result
    assert [path.read_bytes() for path in files["paths"][:3]] == before
    if os.name == "nt":
        assert storage_policy.windows_path_is_private(output, directory=False)
    else:
        assert output.stat().st_mode & 0o777 == 0o600


def test_all_planned_geometry_precedes_any_retry(files):
    files["plan"]["regions"].append({"region_id": "r2", "page_number": 2, "bbox": [0, 0, 1, 1], "recipe": recipe(90)})
    write_plan(files)
    run(files)
    assert HardReader.events[:2] == [("describe", 1), ("describe", 2)]
    assert HardReader.events[-1] == "close"


def test_aggregate_budget_rejects_before_any_retry(files, monkeypatch):
    monkeypatch.setattr(policy, "MAX_TOTAL_PIXELS", 100)
    with pytest.raises(ValueError, match="aggregate"):
        run(files)
    assert not any(isinstance(event, tuple) and event[0] == "retry" for event in HardReader.events)
    assert not files["paths"][3].exists()


def test_geometry_abstention_does_not_invoke_retry(files):
    files["plan"]["regions"][0]["recipe"] = recipe(0, .00001)
    write_plan(files)
    result = run(files)
    assert result["summary"]["abstained"] == 1 and result["regions"][0]["processing"] is None
    assert HardReader.events == [("describe", 1), "close"]


def test_image_gate_abstention_is_not_empty_ocr(files, monkeypatch):
    actual = HardReader.retry_hardscan

    def abstain(self, *args):
        result = actual(self, *args)
        result["candidate"] = None
        result["processing"].update(status="abstained", reason="non_text_pattern", illumination=None)
        return result

    files["plan"]["regions"][0]["recipe"] = recipe(0, .02)
    write_plan(files)
    monkeypatch.setattr(HardReader, "retry_hardscan", abstain)
    report = run(files)
    assert report["summary"]["abstained"] == 1 and report["summary"]["empty"] == 0
    assert report["regions"][0]["candidate"] is None


@pytest.mark.parametrize("error,code", [(ValueError, "retry_limit_or_validation"), (ImportError, "retry_runtime_unavailable"),
                                       (RuntimeError, "retry_runtime_failed")])
def test_failed_retry_preserves_unavailability_and_sanitizes_details(files, monkeypatch, error, code):
    def fail(*_args):
        raise error("PRIVATE_EXCEPTION")

    monkeypatch.setattr(HardReader, "hook", fail)
    result = run(files)
    item = result["regions"][0]
    assert item["candidate"] is None and item["processing"] is None and item["error_code"] == code
    assert result["summary"]["failed"] == 1 and result["summary"]["empty"] == 0
    assert "PRIVATE_EXCEPTION" not in json.dumps(result)


def test_actual_empty_candidate_is_retained(files, monkeypatch):
    actual = HardReader.retry_hardscan

    def blank(self, *args):
        result = actual(self, *args)
        result["candidate"] = candidate(result["candidate"]["raster"], "")
        return result

    monkeypatch.setattr(HardReader, "retry_hardscan", blank)
    report = run(files)
    assert report["summary"]["empty"] == 1 and report["regions"][0]["source_polygons"] == []


@pytest.mark.parametrize("index", [0, 1, 2])
def test_source_generations_rechecked_at_actual_commit(files, monkeypatch, index):
    actual = workflow.storage_policy.atomic_write_private_json
    changed = files["paths"][index]
    replacement = changed.read_bytes()+b" "

    def staged(path, payload, **kwargs):
        commit = kwargs["replace_fn"]

        def mutate(temporary, destination):
            assert temporary.exists() and not destination.exists()
            changed.write_bytes(replacement)
            return commit(temporary, destination)

        kwargs["replace_fn"] = mutate
        return actual(path, payload, **kwargs)

    monkeypatch.setattr(workflow.storage_policy, "atomic_write_private_json", staged)
    with pytest.raises(RuntimeError):
        run(files)
    assert not files["paths"][3].exists() and changed.read_bytes() == replacement


def test_cancellation_closes_reader_and_never_publishes(files, monkeypatch):
    def cancel(*_args):
        raise KeyboardInterrupt

    monkeypatch.setattr(HardReader, "hook", cancel)
    with pytest.raises(KeyboardInterrupt):
        run(files)
    assert HardReader.events[-1] == "close" and not files["paths"][3].exists()


@pytest.mark.parametrize("left,right", itertools.combinations(range(4), 2))
def test_aliasing_any_two_inputs_or_output_is_rejected(files, left, right):
    alias = files["paths"][right].with_name("alias")
    os.link(files["paths"][left], alias)
    files["paths"][right] = alias
    with pytest.raises(ValueError, match="distinct"):
        run(files)
    assert HardReader.events == []


def test_create_only_output_rejects_existing_or_racing_writers(files, monkeypatch):
    def occupy(*_args):
        files["paths"][3].write_bytes(b"other generation")

    monkeypatch.setattr(HardReader, "hook", occupy)
    with pytest.raises(FileExistsError):
        run(files)
    assert files["paths"][3].read_bytes() == b"other generation"
    HardReader.events.clear()
    with pytest.raises(FileExistsError):
        run(files)
    assert HardReader.events == []


@pytest.mark.parametrize("mutant", ["summary", "attention", "candidate_bool", "coordinate", "mapping", "matrix",
                                    "context", "configuration", "processing", "status", "version", "extra"])
def test_report_is_strict_and_does_not_trust_forged_metadata(files, mutant):
    report = run(files)
    item = report["regions"][0]
    if mutant == "summary":
        report["summary"]["candidates"] = True
    elif mutant == "attention":
        report["requires_attention"] = False
    elif mutant == "candidate_bool":
        item["candidate"]["raster"]["width"] = True
    elif mutant == "coordinate":
        item["candidate"]["raster"]["coordinate_system"] = "rendered_image_pixels"
    elif mutant == "mapping":
        item["source_polygons"][0][0][0] += .01
    elif mutant == "matrix":
        item["transform"]["orientation_inverse"][0][0] = False
    elif mutant == "context":
        report["baseline_recovery"]["page_count"] = True
    elif mutant == "configuration":
        report["retry_configuration"]["min_score"] = .5
    elif mutant == "processing":
        item["processing"]["illumination"]["status"] = "applied"
    elif mutant == "status":
        item["status"] = []
    elif mutant == "version":
        report["schema_version"] = True
    else:
        report["extra"] = "private"
    with pytest.raises(ValueError):
        workflow.validate_report(report)


@pytest.mark.parametrize("mutant", ["dpi", "plan_digest", "context"])
def test_supervised_request_binding_rejects_structurally_valid_stale_report(files, mutant):
    report = run(files, dpi=400 if mutant == "dpi" else 300)
    if mutant == "plan_digest":
        report["inputs"]["plan_sha256"] = "b"*64
    elif mutant == "context":
        report["baseline_recovery"]["pages"][0]["original_text"] = "A different synthetic baseline context with enough characters to preserve selection reasons."
    assert workflow.validate_report(report) == report
    pdf, recovery, plan, _output = files["paths"]
    with pytest.raises(ValueError):
        workflow.verify_request(report, pdf_path=pdf, recovery_path=recovery, plan_path=plan, dpi=300)


@pytest.mark.parametrize("dpi", [True, 300., 0, 72, 600, "300"])
def test_invalid_dpi_precedes_any_input_or_runtime_work(files, dpi):
    with pytest.raises(ValueError):
        run(files, dpi=dpi)
    assert HardReader.events == []


def test_report_size_limit_is_enforced_before_publication(files, monkeypatch):
    monkeypatch.setattr(workflow, "MAX_REPORT_BYTES", 10)
    with pytest.raises(ValueError, match="output byte budget"):
        run(files)
    assert not files["paths"][3].exists()


def test_candidate_line_budget_precedes_source_polygon_generation(files, monkeypatch):
    actual = HardReader.retry_hardscan

    def many(self, *args):
        result = actual(self, *args)
        result["candidate"]["lines"] *= 1001
        return result

    monkeypatch.setattr(HardReader, "retry_hardscan", many)
    monkeypatch.setattr(policy, "source_polygon", lambda *_args, **_kwargs: pytest.fail("mapping ran"))
    result = run(files)
    assert result["summary"]["failed"] == 1 and result["regions"][0]["candidate"] is None


@pytest.mark.parametrize("limit_name", ["MAX_VERTICES_PER_REGION", "MAX_TOTAL_VERTICES"])
def test_source_vertex_budgets_fail_explicitly_without_retaining_candidate(files, monkeypatch, limit_name):
    monkeypatch.setattr(policy, limit_name, 3)
    result = run(files)
    assert result["summary"]["failed"] == 1 and result["regions"][0]["source_polygons"] is None


def test_aggregate_vertex_budget_preserves_successful_prior_regions_and_marks_later_failure(files, monkeypatch):
    files["plan"]["regions"].append({"region_id": "r2", "page_number": 2, "bbox": [0, 0, 1, 1], "recipe": recipe()})
    write_plan(files)
    monkeypatch.setattr(policy, "MAX_TOTAL_VERTICES", 4)
    result = run(files)
    assert result["summary"]["candidates"] == result["summary"]["failed"] == 1
    assert result["regions"][0]["candidate"] is not None and result["regions"][1]["candidate"] is None


def test_plan_builder_requires_confirmation_without_reading_inputs(tmp_path):
    with pytest.raises(ValueError, match="confirmation"):
        workflow.create_plan(tmp_path/"missing", tmp_path/"output", [], confirmed=False)


def test_plan_builder_does_not_overwrite_and_rechecks_recovery_during_publication(files, monkeypatch):
    with pytest.raises(FileExistsError):
        workflow.create_plan(files["paths"][1], files["paths"][2], files["plan"]["regions"], confirmed=True)
    actual = workflow.storage_policy.atomic_write_private_json
    output = files["paths"][2].with_name("another-plan.json")

    def staging(path, payload, **kwargs):
        files["paths"][1].write_bytes(files["paths"][1].read_bytes()+b" ")
        return actual(path, payload, **kwargs)

    monkeypatch.setattr(workflow.storage_policy, "atomic_write_private_json", staging)
    with pytest.raises(RuntimeError):
        workflow.create_plan(files["paths"][1], output, copy.deepcopy(files["plan"]["regions"]), confirmed=True)
    assert not output.exists()


@pytest.mark.parametrize("failure", ["reader", "transform"])
def test_characterization_late_preflight_failure_prevents_every_retry(files, monkeypatch, failure):
    files["plan"]["regions"].append({"region_id": "r2", "page_number": 2,
                                      "bbox": [0, 0, 1, 1], "recipe": recipe(90)})
    write_plan(files)
    before = [path.read_bytes() for path in files["paths"][:3]]
    actual = HardReader.describe_hardscan

    def describe(self, page, bbox, selected_recipe):
        result = actual(self, page, bbox, selected_recipe)
        if page == 2:
            if failure == "reader":
                raise RuntimeError("PRIVATE_PREFLIGHT_FAILURE")
            result["transform"]["processed_raster"]["width"] += 1
        return result

    monkeypatch.setattr(HardReader, "describe_hardscan", describe)
    error, message = ((RuntimeError, "PRIVATE_PREFLIGHT_FAILURE") if failure == "reader"
                      else (ValueError, "transform differs"))
    with pytest.raises(error, match=message):
        run(files)
    assert HardReader.events == [("describe", 1), ("describe", 2), "close"]
    assert not files["paths"][3].exists()
    assert [path.read_bytes() for path in files["paths"][:3]] == before


def test_characterization_canonical_mixed_outcomes_detach_saved_report(files, monkeypatch):
    requested = [
        {"region_id": name, "page_number": 1, "bbox": [index / 4, 0, (index + 1) / 4, .25],
         "recipe": recipe(0, .00001 if name == "A" else .02 if name == "a" else 0.)}
        for index, name in enumerate(("A", "a", "y", "z"))
    ]
    files["plan"]["regions"] = list(reversed(requested))
    write_plan(files)
    names = {tuple(region["bbox"]): region["region_id"] for region in requested}
    events, delivered, parsed_inputs = [], [], []
    actual_describe = HardReader.describe_hardscan
    actual_retry = HardReader.retry_hardscan
    actual_inputs = workflow._inputs

    def describe(self, page, bbox, selected_recipe):
        events.append(("describe", names[tuple(bbox)]))
        return actual_describe(self, page, bbox, selected_recipe)

    def retry(self, page, bbox, selected_recipe):
        name = names[tuple(bbox)]
        events.append(("retry", name))
        result = actual_retry(self, page, bbox, selected_recipe)
        if name == "a":
            result["candidate"] = None
            result["processing"].update(status="abstained", reason="non_text_pattern", illumination=None)
        else:
            result["candidate"] = candidate(result["candidate"]["raster"], "" if name == "z" else "kept y")
        delivered.append(result)
        return result

    def inputs(*args):
        result = actual_inputs(*args)
        parsed_inputs.append(result)
        return result

    monkeypatch.setattr(HardReader, "describe_hardscan", describe)
    monkeypatch.setattr(HardReader, "retry_hardscan", retry)
    monkeypatch.setattr(workflow, "_inputs", inputs)
    report = run(files)
    assert events[:4] == [("describe", name) for name in ("A", "a", "y", "z")]
    assert [name for event, name in events if event == "retry"] == ["a", "y", "z"]
    assert [row["region_id"] for row in report["regions"]] == ["A", "a", "y", "z"]
    geometry, image, kept, empty = report["regions"]
    assert [row["status"] for row in report["regions"]] == [
        "abstained", "abstained", "review_required", "empty_candidate"]
    assert geometry["abstention_reason"] == "bow_below_pixel_resolution" and geometry["processing"] is None
    assert image["abstention_reason"] == "non_text_pattern" and image["processing"]["status"] == "abstained"
    assert geometry["candidate"] is image["candidate"] is None
    assert kept["candidate"]["text"] == "kept y" and empty["candidate"]["text"] == ""
    assert empty["source_polygons"] == []
    assert {key: report["summary"][key] for key in ("requested", "candidates", "empty", "failed", "abstained")} == {
        "requested": 4, "candidates": 1, "empty": 1, "failed": 0, "abstained": 2}
    saved = files["paths"][3].read_bytes()
    encoded = (json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    assert saved == encoded and HardReader.events[-1] == "close"
    # Mutate the actual parsed input/reader objects after publication, not copies
    # made by the test. The returned and persisted reports must stay detached.
    parsed_inputs[0][0]["pages"][0]["original_text"] = "MUTATED_CONTEXT"
    parsed_inputs[0][1]["regions"][0]["bbox"][0] = .123
    delivered[0]["processing"]["reason"] = "MUTATED_PROCESSING"
    delivered[1]["candidate"]["lines"][0]["text"] = "MUTATED_CANDIDATE"
    delivered[1]["geometry"]["page"]["rotation_degrees"] = 90
    assert (json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode() == saved
    assert files["paths"][3].read_bytes() == saved


@pytest.mark.parametrize("failure,code", [
    ("processing", "retry_limit_or_validation"),
    ("candidate", "retry_runtime_failed"),
    ("mapping", "retry_runtime_unavailable"),
    ("interrupt", None),
])
def test_characterization_middle_failure_preserves_exception_boundary_and_order(files, monkeypatch, failure, code):
    requested = [{"region_id": name, "page_number": 1, "bbox": [index / 3, 0, (index + 1) / 3, 1],
                  "recipe": recipe()} for index, name in enumerate(("first", "middle", "third"))]
    files["plan"]["regions"] = requested
    write_plan(files)
    names = {tuple(region["bbox"]): region["region_id"] for region in requested}
    events = []
    actual_describe = HardReader.describe_hardscan
    actual_retry = HardReader.retry_hardscan
    actual_candidate = workflow._candidate
    actual_mapping = workflow._source_polygons

    def describe(self, page, bbox, selected_recipe):
        events.append(("describe", names[tuple(bbox)]))
        return actual_describe(self, page, bbox, selected_recipe)

    def retry(self, page, bbox, selected_recipe):
        name = names[tuple(bbox)]
        events.append(("retry", name))
        result = actual_retry(self, page, bbox, selected_recipe)
        result["candidate"] = candidate(result["candidate"]["raster"], name)
        if name == "middle":
            if failure == "interrupt":
                raise KeyboardInterrupt("PRIVATE_STAGE_DETAILS")
            if failure == "processing":
                result["processing"]["PRIVATE_STAGE_DETAILS"] = True
        return result

    def checked_candidate(payload, *args, **kwargs):
        if payload["text"] == "middle" and failure == "candidate":
            raise RuntimeError("PRIVATE_STAGE_DETAILS")
        return actual_candidate(payload, *args, **kwargs)

    def mapped(payload, *args, **kwargs):
        if payload["text"] == "middle" and failure == "mapping":
            raise ImportError("PRIVATE_STAGE_DETAILS")
        return actual_mapping(payload, *args, **kwargs)

    monkeypatch.setattr(HardReader, "describe_hardscan", describe)
    monkeypatch.setattr(HardReader, "retry_hardscan", retry)
    monkeypatch.setattr(workflow, "_candidate", checked_candidate)
    monkeypatch.setattr(workflow, "_source_polygons", mapped)
    if failure == "interrupt":
        with pytest.raises(KeyboardInterrupt, match="PRIVATE_STAGE_DETAILS"):
            run(files)
        assert [name for event, name in events if event == "retry"] == ["first", "middle"]
        assert not files["paths"][3].exists()
    else:
        report = run(files)
        first, middle, third = report["regions"]
        assert first["candidate"]["text"] == "first" and third["candidate"]["text"] == "third"
        assert middle["status"] == "retry_failed" and middle["error_code"] == code
        assert all(middle[key] is None for key in ("candidate", "processing", "source_polygons", "abstention_reason"))
        assert report["summary"]["candidates"] == 2 and report["summary"]["failed"] == 1
        assert "PRIVATE_STAGE_DETAILS" not in json.dumps(report)
        assert [name for event, name in events if event == "retry"] == ["first", "middle", "third"]
    assert events[:3] == [("describe", name) for name in ("first", "middle", "third")]
    assert HardReader.events[-1] == "close"


def test_characterization_vertex_failure_does_not_spend_budget_or_skip_later_retry(files, monkeypatch):
    requested = [{"region_id": name, "page_number": 1, "bbox": [index / 4, 0, (index + 1) / 4, 1],
                  "recipe": recipe()} for index, name in enumerate(("A", "b", "m", "z"))]
    files["plan"]["regions"] = list(reversed(requested))
    write_plan(files)
    names = {tuple(region["bbox"]): region["region_id"] for region in requested}
    attempted = []
    actual = HardReader.retry_hardscan

    def retry(self, page, bbox, selected_recipe):
        name = names[tuple(bbox)]
        attempted.append(name)
        result = actual(self, page, bbox, selected_recipe)
        result["candidate"] = candidate(result["candidate"]["raster"], "" if name == "b" else name)
        if name == "m":
            result["candidate"]["lines"] *= 2
            result["candidate"]["text"] = "m\nm"
        return result

    monkeypatch.setattr(HardReader, "retry_hardscan", retry)
    monkeypatch.setattr(policy, "MAX_TOTAL_VERTICES", 8)
    report = run(files)
    assert attempted == ["A", "b", "m", "z"]
    first, empty, failed, last = report["regions"]
    assert [row["status"] for row in report["regions"]] == [
        "review_required", "empty_candidate", "retry_failed", "review_required"]
    assert first["candidate"]["text"] == "A" and last["candidate"]["text"] == "z"
    assert empty["source_polygons"] == []
    assert failed["error_code"] == "retry_limit_or_validation" and failed["candidate"] is None
    assert failed["source_polygons"] is None
    assert sum(len(polygon) for row in (first, last) for polygon in row["source_polygons"]) == 8
    assert {key: report["summary"][key] for key in ("candidates", "empty", "failed")} == {
        "candidates": 2, "empty": 1, "failed": 1}
