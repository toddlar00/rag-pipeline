"""Real all-route bundle composition over inert, pinned upstream formatting.

PDF parsing/rendering and model verification are explicit synthetic boundaries.
Raw dispatch, formatter, sessions, recorders, report/receipt/lineage validators,
source snapshots and private publication are the production implementations.
These are correspondence controls, not recognition-accuracy experiments.
"""

import copy
import hashlib
import json
import math
from pathlib import Path
import sys
from types import SimpleNamespace as NS

import pytest

import model_artifacts
import ocr_detection_disposition_io as workflow
import ocr_disposition_observer as observer
import ocr_execution_receipt as receipts
import ocr_hardscan as hardscan
import ocr_hardscan_io
import ocr_hardscan_runtime
import ocr_recovery
import ocr_recovery_runtime
import ocr_region_runtime
import ocr_regions
from test_ocr_detection_disposition_runtime import stack as stack, harness as harness, upstream as upstream


def _recipe(angle=0, bow=0.0):
    return {"orientation_clockwise": angle, "illumination": "none", "bow_fraction": bow,
            "bow_assumption": "parallel_horizontal_baselines" if bow else "none"}


def _write(path, payload):
    raw = (json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def routes(stack, tmp_path, monkeypatch):
    source = tmp_path / "source.pdf"
    source.write_bytes(b"generated all-route PDF stand-in; never parsed")
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    generation = {"schema_version": 1, "kind": "ocr_disposition_producer", "upstream": {"synthetic": "a" * 64},
                  "identity": {"environment_sha256": receipts.environment_identity(Path(sys.prefix)),
                               "producer_sources": {"ocr_detection_disposition_io.py": "b" * 64}}}
    monkeypatch.setattr(workflow, "capture_producer_generation", lambda **_kw: copy.deepcopy(generation))
    monkeypatch.setattr(receipts, "_loaded_source_digests", lambda: {"ocr_detection_disposition_io": "b" * 64})
    models, model_paths, verification = [], {}, []
    for index, (role, attribute) in enumerate(sorted(receipts._ROLES.items()), 1):
        models.append(NS(role=role, content_sha256=str(index) * 64, size=100 + index))
        model_paths[role] = tmp_path / (role + ".onnx")
        component = getattr(stack.raw, attribute)
        component.session.session = NS(get_providers=lambda: ["CPUExecutionProvider"],
            get_session_options=lambda: NS(intra_op_num_threads=2, inter_op_num_threads=1))
    monkeypatch.setattr(model_artifacts, "package_model", lambda _name: NS(version="3.9.2", files=models))

    def verify(package, consumer):
        assert (package, consumer) == ("rapidocr", "docling_ocr")
        verification.append(True)
        return dict(model_paths)

    monkeypatch.setattr(model_artifacts, "verified_installed_package_model", verify)
    settings = NS(modes={}, closes=0, attempts=[], descriptions=[])

    class Reader(ocr_recovery_runtime.RapidOCRPageReader):
        def __enter__(self):
            assert self.path.read_bytes() == source.read_bytes()
            return self

        @property
        def page_count(self):
            return 3

        def native_text(self, page):
            return "Generated healthy native text with enough characters to remain unselected." if page == 2 else ""

        def close(self):
            settings.closes += 1
            super().close()

        def _load_engine(self):
            if self._engine is None:
                self._engine = stack.guard
                self._engine_version = "3.9.2"
                self._verified_model_paths = dict(model_paths)
            return self._engine

        def _candidate(self, page, *, coordinate_system="rendered_image_pixels"):
            settings.attempts.append(page)
            mode = settings.modes.get(page)
            if mode == "preflight":
                raise ValueError("synthetic no-dispatch preflight refusal")
            engine = self._load_engine()
            engine.prepare(stack.pixels)
            if mode == "raw_failure":
                stack.raw.text_rec.session.error = RuntimeError("PRIVATE inert session failure")
            try:
                result = engine(stack.pixels)
            finally:
                stack.raw.text_rec.session.error = None
            height, width = stack.pixels.shape[:2]
            lines = ocr_recovery_runtime._validated_lines(result, width=width, height=height, min_score=self.min_score)
            scores = [line["score"] for line in lines]
            candidate = {"text": "\n".join(line["text"] for line in lines), "lines": lines,
                "mean_confidence": sum(scores) / len(scores) if scores else None,
                "raster": {"width": width, "height": height, "dpi": self.dpi, "coordinate_system": coordinate_system},
                "engine": {"name": "rapidocr", "version": self._engine_version,
                           "min_score": self.min_score, "max_side": self.max_side}}
            if mode == "orchestrator_reject":
                candidate["engine"]["max_side"] -= 1
            return candidate

        def retry(self, page):
            return self._candidate(page)

        def describe_region(self, page, bbox):
            settings.descriptions.append(page)
            scale = self.dpi / 72
            side = 64 / scale
            bounds = [math.floor(bbox[0] * 64), math.floor(bbox[1] * 64),
                      math.ceil(bbox[2] * 64), math.ceil(bbox[3] * 64)]
            return {"page": {"display_rect_points": [0., 0., side, side],
                              "cropbox_points": [0., 0., side, side], "rotation_degrees": 0},
                    "raster": {"width": bounds[2] - bounds[0], "height": bounds[3] - bounds[1],
                               "dpi": self.dpi, "coordinate_system": "rendered_image_pixels"},
                    "pixel_bounds": bounds,
                    "crop_to_page_fraction": [[1 / 64, 0., bounds[0] / 64], [0., 1 / 64, bounds[1] / 64]],
                    "boundary_policy": "clip_to_page_bounds"}

        def retry_region(self, page, bbox):
            return {"geometry": self.describe_region(page, bbox), "candidate": self._candidate(page)}

        def describe_hardscan(self, page, bbox, recipe):
            geometry = self.describe_region(page, bbox)
            return {"geometry": geometry, "transform": hardscan.describe_transform(
                geometry["raster"]["width"], geometry["raster"]["height"], recipe)}

        def retry_hardscan(self, page, bbox, recipe):
            result = self.describe_hardscan(page, bbox, recipe)
            processing = {"status": "completed", "reason": None,
                          "libraries": {"opencv": "synthetic", "numpy": "synthetic"},
                          "illumination": {"status": "disabled", "reason": "mode_disabled",
                              "background_low": None, "background_high": None, "ink_fraction": None}}
            if settings.modes.get(page) == "processing_abstention":
                processing.update(status="abstained", reason="non_text_pattern", illumination=None)
                candidate = None
            else:
                candidate = self._candidate(page, coordinate_system="hardscan_image_pixels")
            return {**result, "processing": processing, "candidate": candidate}

    for module, name in ((ocr_recovery_runtime, "RapidOCRPageReader"), (ocr_region_runtime, "RapidOCRRegionReader"),
                         (ocr_hardscan_runtime, "RapidOCRHardScanReader")):
        monkeypatch.setattr(module, name, Reader)

    class Baseline:
        page_count = 3

        def native_text(self, page):
            return f"Generated saved native context for page {page}, never a reference transcription."

        def retry(self, _page):
            raise RuntimeError("synthetic saved earlier failure")

    baseline = ocr_recovery.build_recovery_report(Baseline(), source_sha256=source_hash,
                                                  policy=ocr_recovery.RetryPolicy(), requested_pages=(1, 3))
    baseline["evidence_sha256"] = None
    recovery_path = tmp_path / "recovery.json"
    recovery_hash = _write(recovery_path, baseline)

    def prepare(operation, *, rows=None):
        output = tmp_path / "bundle"
        options = {"operation": operation}
        if operation == "pages":
            options.update(requested_pages=(1, 3))
        else:
            rows = rows or [{"region_id": "a", "page_number": 1, "bbox": [0., 0., 1., 1.]},
                            {"region_id": "b", "page_number": 3, "bbox": [0., 0., 1., 1.]}]
            rows = copy.deepcopy(rows)
            plan = {"schema_version": 1, "source_sha256": source_hash, "recovery_sha256": recovery_hash,
                    "coordinate_system": "original_page_display_fraction", "regions": rows}
            if operation == "hardscan":
                plan.update(kind="ocr_hardscan_plan", approval="operator_approved")
                for row in rows:
                    row.setdefault("recipe", _recipe())
            plan_path = tmp_path / "plan.json"
            _write(plan_path, plan)
            options.update(recovery_path=recovery_path, plan_path=plan_path)
        request = workflow.disposition_request(source, output, **options)
        return NS(output=output, options=options, request=request)

    return NS(stack=stack, source=source, source_hash=source_hash, generation=generation, settings=settings,
              prepare=prepare, recovery=baseline, recovery_path=recovery_path, verification=verification)


def _run(routes, prepared):
    result = workflow.run_disposition_bundle(routes.source, prepared.output, **prepared.options)
    assert workflow.read_disposition_completion(prepared.output, request=prepared.request) == result
    assert (prepared.output / "report.json").read_bytes() == (prepared.output / "work/report.json").read_bytes()
    return result


@pytest.mark.parametrize("operation", ["pages", "regions", "hardscan"])
def test_actual_success_joins_complete_receipts_and_physical_scope(routes, operation):
    prepared = routes.prepare(operation)
    result = _run(routes, prepared)
    diagnostic = result["disposition"]
    assert routes.stack.raw.calls == 2
    assert [(call["id"], call["status"]) for call in result["execution"]["calls"]] == [
        ("call-0001", "completed"), ("call-0002", "completed")]
    assert len(result["execution"]["engine_observation"]["sessions"]) == 3
    assert routes.verification and routes.settings.closes == 1
    selected = [item for item in diagnostic["items"] if item["dispatch"] is not None]
    assert len(selected) == diagnostic["summary"]["diagnostics_complete"] == 2
    assert diagnostic["summary"]["joined_candidate_lines"] == 2
    for item in selected:
        assert item["candidate"]["state"] == "accepted"
        record = item["diagnostic"]["detections"][0]
        assert record["candidate_disposition"] == "accepted" and record["candidate_index"] == 1
        assert record["physical"]["state"] == "available"
        assert all(item["diagnostic"]["roles"][role]["session_completed"] == 1 for role in receipts._ROLES)
    if operation == "hardscan":
        assert [item["diagnostic"]["detections"][0]["physical"]["polygon"] for item in selected] == [
            row["source_polygons"][0] for row in result["report"]["regions"]]
    assert "PRIVATE" not in json.dumps(result)


@pytest.mark.parametrize("operation", ["pages", "regions", "hardscan"])
def test_diagnostic_only_fault_does_not_reject_healthy_final_candidates(routes, monkeypatch, operation):
    original = observer.DispositionFrame._event

    def fault(self, event, values):
        if event == "formatter_return":
            raise RuntimeError("PRIVATE diagnostic-only failure")
        return original(self, event, values)

    monkeypatch.setattr(observer.DispositionFrame, "_event", fault)
    result = _run(routes, routes.prepare(operation))
    summary = result["disposition"]["summary"]
    assert summary["accepted_candidates"] == summary["diagnostics_unavailable"] == 2
    assert summary["rejected_candidates"] == summary["joined_candidate_lines"] == 0
    rows = result["report"]["pages" if operation == "pages" else "regions"]
    assert [row["candidate"]["text"] for row in rows] == ["hello", "hello"]
    assert [call["status"] for call in result["execution"]["calls"]] == ["completed", "completed"]
    assert routes.stack.raw.calls == 2 and "PRIVATE" not in json.dumps(result)


@pytest.mark.parametrize("operation", ["pages", "regions", "hardscan"])
@pytest.mark.parametrize("mode", ["preflight", "raw_failure", "orchestrator_reject"])
def test_peer_failure_keeps_actual_dispatch_and_candidate_acceptance_separate(routes, operation, mode):
    routes.settings.modes[1] = mode
    result = _run(routes, routes.prepare(operation))
    attempts = [item for item in result["disposition"]["items"] if item["attempt_index"] is not None]
    first, later = attempts
    assert first["candidate"]["state"] == "rejected" and later["candidate"]["state"] == "accepted"
    assert later["diagnostic"]["state"] == "complete"
    if mode == "preflight":
        assert first["dispatch"] is None and first["diagnostic"]["state"] == "not_run"
        assert [call["id"] for call in result["execution"]["calls"]] == ["call-0001"]
    else:
        assert first["dispatch"]["raw_status"] == ("failed" if mode == "raw_failure" else "completed")
        assert first["diagnostic"]["state"] == ("partial" if mode == "raw_failure" else "complete")
        assert first["diagnostic"]["detections"][0]["candidate_index"] is None
        assert [call["id"] for call in result["execution"]["calls"]] == ["call-0001", "call-0002"]
    assert routes.stack.raw.calls == len(result["execution"]["calls"])


@pytest.mark.parametrize("early", ["detector", "crops"])
def test_caught_early_failure_then_failed_formatter_keeps_bound_partial_and_healthy_peer(routes, monkeypatch, early):
    """Actual upstream default fallback is neither zero detections nor a lost page.

    Both failure injections apply only to the first real dispatch. The unchanged
    orchestrator continues page three through the same guard and publishes a
    strict full receipt/diagnostic bundle, then the original request reads it.
    """
    stack = routes.stack
    original_crop = stack.harness.Raw.crop_text_regions
    original_formatter = stack.harness.Raw.build_final_output
    if early == "detector":
        stack.raw.text_det.error = stack.main.RapidOCRError("PRIVATE generated detector failure")
    else:
        def crop_failed_once(self, *args, **kwargs):
            if self.calls == 1:
                raise stack.main.RapidOCRError("PRIVATE generated crop failure")
            return original_crop(self, *args, **kwargs)
        monkeypatch.setattr(stack.harness.Raw, "crop_text_regions", crop_failed_once)

    def formatter_failed_once(self, *args, **kwargs):
        if self.calls == 1:
            self.text_det.error = None
            raise RuntimeError("PRIVATE generated formatter failure")
        return original_formatter(self, *args, **kwargs)

    monkeypatch.setattr(stack.harness.Raw, "build_final_output", formatter_failed_once)
    result = _run(routes, routes.prepare("pages"))
    assert routes.settings.attempts == [1, 3] and stack.raw.calls == 2
    assert [(call["id"], call["status"]) for call in result["execution"]["calls"]] == [
        ("call-0001", "failed"), ("call-0002", "completed")]
    first_row, later_row = result["report"]["pages"]
    assert (first_row["page_number"], first_row["status"], first_row["candidate"]) == (1, "retry_failed", None)
    assert first_row["original_text"] == ""
    assert (later_row["page_number"], later_row["status"], later_row["candidate"]["text"]) == (3, "review_required", "hello")
    diagnostic = result["disposition"]
    assert [item["page_number"] for item in diagnostic["items"]] == [1, 2, 3]
    assert diagnostic["attempt_order"] == diagnostic["dispatch_order"] == ["page-00001", "page-00003"]
    first, unselected, later = diagnostic["items"]
    assert first["candidate"]["state"] == "rejected" and first["dispatch"]["raw_status"] == "failed"
    partial = first["diagnostic"]
    assert partial["state"] == "partial" and partial["mapping"] is None
    assert partial["detection_count"] == (None if early == "detector" else 1)
    assert partial["stages"]["detector"]["state"] == ("failed" if early == "detector" else "completed")
    assert partial["stages"]["crops"]["state"] == ("not_run" if early == "detector" else "failed")
    assert partial["stages"]["formatter"] == {
        "state": "failed", "input_count": 0, "output_count": None,
        "box_input": {"state": "none", "dtype": None, "shape": None}, "box_output": None,
    }
    assert all(record["crop_state"] == "unobserved" and record["recognition"]["state"] == "not_run"
               and record["candidate_index"] is None for record in partial["detections"])
    assert unselected["dispatch"] is None and unselected["diagnostic"]["state"] == "not_run"
    assert later["candidate"]["state"] == "accepted" and later["diagnostic"]["state"] == "complete"
    assert later["dispatch"]["call_id"] == "call-0002"
    assert "PRIVATE" not in json.dumps(result)


@pytest.mark.parametrize("operation", ["pages", "regions", "hardscan"])
@pytest.mark.parametrize("empty_kind", ["detector", "blank_recognition"])
def test_true_empty_and_blank_removed_paths_both_keep_actual_calls(routes, operation, empty_kind):
    if empty_kind == "detector":
        routes.stack.raw.text_det.boxes = None
    else:
        routes.stack.texts[:] = [" \t"]
    result = _run(routes, routes.prepare(operation))
    assert routes.stack.raw.calls == 2 and result["execution"]["summary"]["completed"] == 2
    summary = result["disposition"]["summary"]
    assert summary["diagnostics_complete"] == 2 and summary["joined_candidate_lines"] == 0
    assert summary["known_detections"] == (0 if empty_kind == "detector" else 2)
    assert summary["removed_blank"] == (0 if empty_kind == "detector" else 2)
    assert summary["accepted_candidates"] == 2


@pytest.mark.parametrize("operation", ["pages", "regions", "hardscan"])
def test_entire_no_dispatch_cohort_is_complete_coverage_not_empty_ocr_success(routes, operation):
    routes.settings.modes.update({1: "preflight", 3: "preflight"})
    result = _run(routes, routes.prepare(operation))
    assert result["execution"]["calls"] == [] and result["execution"]["engine_observation"] is None
    assert routes.stack.raw.calls == 0
    summary = result["disposition"]["summary"]
    assert summary["diagnostics_not_run"] == summary["covered_items"]
    assert summary["rejected_candidates"] == 2 and summary["accepted_candidates"] == 0
    assert summary["detections_unknown_items"] == summary["covered_items"]


@pytest.mark.parametrize("abstention", ["preflight", "processing"])
def test_hardscan_abstention_does_not_steal_later_plan_ticket(routes, abstention):
    rows = [{"region_id": "a", "page_number": 1, "bbox": [0., 0., 1., 1.],
             "recipe": _recipe(bow=.001 if abstention == "preflight" else .01)},
            {"region_id": "b", "page_number": 3, "bbox": [0., 0., 1., 1.], "recipe": _recipe()}]
    if abstention == "processing":
        routes.settings.modes[1] = "processing_abstention"
    result = _run(routes, routes.prepare("hardscan", rows=rows))
    first, later = result["disposition"]["items"]
    assert result["report"]["regions"][0]["status"] == "abstained"
    assert first["candidate"]["state"] == "abstained" and first["dispatch"] is None
    assert (first["attempt_index"] is None) is (abstention == "preflight")
    assert later["dispatch"]["call_id"] == "call-0001" and routes.stack.raw.calls == 1


@pytest.mark.parametrize("operation", ["regions", "hardscan"])
def test_same_page_identical_selectors_bind_separate_actual_calls(routes, operation):
    rows = [{"region_id": name, "page_number": 1, "bbox": [0., 0., 1., 1.]} for name in ("a", "b")]
    result = _run(routes, routes.prepare(operation, rows=rows))
    assert result["disposition"]["dispatch_order"] == ["region-0001", "region-0002"]
    assert [item["dispatch"]["call_id"] for item in result["disposition"]["items"]] == ["call-0001", "call-0002"]


def _replace_report(prepared, result, report):
    """Rebind byte hashes; expected external request remains untouched."""
    digest = _write(prepared.output / "report.json", report)
    _write(prepared.output / "work/report.json", report)
    manifest = copy.deepcopy(result["manifest"])
    manifest["report_sha256"] = digest
    _write(prepared.output / "manifest.json", manifest)


@pytest.mark.parametrize("operation", ["regions", "hardscan"])
@pytest.mark.parametrize("fault", ["plan", "context"])
def test_valid_report_with_rebound_hashes_cannot_change_saved_plan_or_context(routes, operation, fault):
    prepared = routes.prepare(operation)
    result = _run(routes, prepared)
    report = copy.deepcopy(result["report"])
    if fault == "plan":
        report["plan"]["regions"][0]["region_id"] = "different"
        report["regions"][0]["region_id"] = "different"
    elif operation == "regions":
        report["page_contexts"][0]["recovery_page"]["original_text"] += " altered"
    else:
        report["baseline_recovery"]["pages"][0]["original_text"] += " altered"
    (ocr_regions.validate_region_review if operation == "regions" else ocr_hardscan_io.validate_report)(report)
    _replace_report(prepared, result, report)
    with pytest.raises(ValueError, match="requested input generations|requested generation"):
        workflow.read_disposition_completion(prepared.output, request=prepared.request)


def test_valid_hardscan_recipe_change_is_not_accepted_from_rebound_report(routes):
    prepared = routes.prepare("hardscan")
    result = _run(routes, prepared)
    report = copy.deepcopy(result["report"])
    row = report["regions"][0]
    row["recipe"] = report["plan"]["regions"][0]["recipe"] = _recipe(angle=90)
    row["transform"] = hardscan.describe_transform(64, 64, row["recipe"])
    row["source_polygons"] = ocr_hardscan_io._source_polygons(row["candidate"], row["geometry"], row["transform"], vertex_budget=128)
    ocr_hardscan_io.validate_report(report)
    _replace_report(prepared, result, report)
    with pytest.raises(ValueError, match="requested generation"):
        workflow.read_disposition_completion(prepared.output, request=prepared.request)


@pytest.mark.parametrize("fault", ["call_id", "call_status", "output_digest"])
def test_receipt_artifact_rebinding_does_not_fabricate_matching_raw_dispatch(routes, monkeypatch, fault):
    prepared = routes.prepare("pages")
    result = _run(routes, prepared)
    receipt = copy.deepcopy(result["execution"])
    if fault == "call_id":
        receipt["calls"][0]["id"] = "call-0099"
    elif fault == "call_status":
        receipt["calls"][0]["status"] = "failed"
        receipt["summary"].update(completed=1, failed=1)
    else:
        receipt["output_sha256"] = "f" * 64
    # The mutated receipt is independently valid; the failure must be its
    # request/lineage correspondence, not a malformed receipt or stale hash.
    receipts.validate_execution_receipt(receipt)
    digest = _write(prepared.output / "execution.json", receipt)
    manifest = copy.deepcopy(result["manifest"])
    manifest["execution_sha256"] = digest
    diagnostic = copy.deepcopy(result["disposition"])
    diagnostic["bindings"]["execution_sha256"] = digest
    manifest["disposition_sha256"] = _write(prepared.output / "disposition.json", diagnostic)
    _write(prepared.output / "manifest.json", manifest)
    entered = []
    validate = workflow.validate_disposition

    def observed_validation(*args, **kwargs):
        entered.append(True)
        return validate(*args, **kwargs)

    monkeypatch.setattr(workflow, "validate_disposition", observed_validation)
    with pytest.raises(ValueError):
        workflow.read_disposition_completion(prepared.output, request=prepared.request)
    assert bool(entered) is (fault != "output_digest")
