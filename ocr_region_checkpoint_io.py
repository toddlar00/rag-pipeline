"""Leased crop journals with fixed profiles, consumed attempts and origin receipts."""

from __future__ import annotations

import copy
import os
from pathlib import Path
import time

import artifact_io
import ocr_checkpoint as common
from ocr_checkpoint_io import _Journal, _json, _read_segments
from ocr_checkpoint_runtime import bounded_snapshot as _snapshot, capture_identity
from ocr_execution_receipt import ExecutionRecorder, capture_execution_receipt, validate_execution_receipt
import ocr_recovery
from ocr_recovery_comparison import validate_recovery_report
import ocr_region_checkpoint as core
import ocr_regions
from resource_lease import PathLease
import storage_policy


ROOT = Path(__file__).resolve().parent
_INPUT_LIMITS = {
    "source_sha256": 256 * 1024 * 1024,
    "dependency_full_sha256": 8 * 1024 * 1024,
    "dependency_test_sha256": 8 * 1024 * 1024,
    "dependency_tools_sha256": 8 * 1024 * 1024,
    "model_policy_sha256": 8 * 1024 * 1024,
    "model_lock_sha256": 8 * 1024 * 1024,
    "recovery_sha256": ocr_regions.MAX_RECOVERY_BYTES,
    "plan_sha256": ocr_regions.MAX_PLAN_BYTES,
    "installation_sha256": 64 * 1024,
}



def _profile(operation: str):
    """Select one of two fixed crop cores before filesystem or runtime work."""
    if type(operation) is not str:
        raise ValueError("invalid crop checkpoint operation")
    if operation == "regions":
        return core, "region"
    if operation == "hardscan":
        import ocr_hardscan_checkpoint
        return ocr_hardscan_checkpoint, "hardscan"
    raise ValueError("invalid crop checkpoint operation")


def _input_paths(specs: list, request: dict, *, profile: str = "regions") -> dict:
    _, label = _profile(profile)
    paths = {}
    for path, key, limit in specs:
        if key in paths or key not in _INPUT_LIMITS or type(limit) is not int or limit != _INPUT_LIMITS[key]:
            raise ValueError(f"{label} checkpoint input specification differs")
        paths[key] = Path(path).absolute()
    if set(paths) != set(request["inputs"]) - {"configuration_sha256", "identity_sha256"}:
        raise ValueError(f"{label} checkpoint input specifications are incomplete")
    return paths


def _read_inputs(specs: list, request: dict, *, profile: str = "regions") -> tuple[dict, dict]:
    _, label = _profile(profile)
    paths = _input_paths(specs, request, profile=profile)
    values = {}
    for key in ("recovery_sha256", "plan_sha256"):
        raw, digest = _snapshot(paths[key], _INPUT_LIMITS[key])
        if digest != request["inputs"][key]:
            raise RuntimeError(f"{label} checkpoint input changed")
        values[key] = _json(raw, limit=_INPUT_LIMITS[key])
    recovery = validate_recovery_report(values["recovery_sha256"])
    if recovery["source_sha256"] != request["inputs"]["source_sha256"]:
        raise ValueError(f"{label} checkpoint recovery belongs to a different source")
    if profile == "regions":
        validate_plan = ocr_regions.validate_region_plan
    else:
        from ocr_hardscan import validate_plan
    plan = validate_plan(
        values["plan_sha256"], source_sha256=recovery["source_sha256"],
        recovery_sha256=request["inputs"]["recovery_sha256"], page_count=recovery["page_count"])
    return recovery, plan


def _checkpoint_request(pdf_path: Path, output_dir: Path, *, profile: str, operation: str = "regions",
                              policy: ocr_recovery.RetryPolicy | None = None,
                              requested_pages: tuple[int, ...] = (), evidence_path: Path | None = None,
                              installation_path: Path | None = None, recovery_path: Path | None = None,
                              plan_path: Path | None = None, resume: bool = False) -> tuple:
    """Read-only exact region inputs/runtime preflight for a new or resumed generation."""
    checkpoint, label = _profile(profile)
    if operation != profile or type(resume) is not bool:
        raise ValueError(f"invalid {label} checkpoint operation or resume flag")
    policy = ocr_recovery.RetryPolicy() if policy is None else policy
    if not isinstance(policy, ocr_recovery.RetryPolicy):
        raise ValueError(f"invalid {label} checkpoint retry policy")
    if (not isinstance(requested_pages, tuple) or requested_pages or evidence_path is not None
            or recovery_path is None or plan_path is None
            or policy != ocr_recovery.RetryPolicy(dpi=policy.dpi)):
        raise ValueError(f"{label} checkpoints accept only DPI and recovery and plan inputs")
    configuration = checkpoint.configuration(policy.dpi)
    source, output = Path(pdf_path).absolute(), Path(output_dir).absolute()
    specs = [(source, "source_sha256", _INPUT_LIMITS["source_sha256"])]
    for filename, key in (("requirements-full.lock", "dependency_full_sha256"),
                           ("requirements-test.lock", "dependency_test_sha256"),
                           ("requirements-lock-tools.lock", "dependency_tools_sha256"),
                           ("model-artifact-policy.json", "model_policy_sha256"),
                           ("model-artifacts.lock.json", "model_lock_sha256")):
        specs.append((ROOT / filename, key, _INPUT_LIMITS[key]))
    for path, key in ((recovery_path, "recovery_sha256"), (plan_path, "plan_sha256"),
                       (installation_path, "installation_sha256")):
        if path is not None:
            specs.append((Path(path).absolute(), key, _INPUT_LIMITS[key]))
    storage_policy.assert_no_link_components(output)
    if not output.parent.is_dir() or (resume and not output.is_dir()) or (not resume and output.exists()):
        raise ValueError(f"{label} checkpoint requires a new directory or an explicit existing resume directory")
    inputs = {}
    for index, (path, key, limit) in enumerate(specs):
        if path.is_relative_to(output) or any(os.path.samefile(path, previous[0]) for previous in specs[:index]):
            raise ValueError(f"{label} checkpoint inputs must be distinct and outside the generation")
        inputs[key] = _snapshot(path, limit)[1]
    identity = capture_identity(installation_path=installation_path)
    inputs.update(configuration_sha256=common.digest(configuration), identity_sha256=common.digest(identity))
    request = checkpoint.validate_request({"schema_version": 1, "kind": "ocr_region_checkpoint_request" if profile == "regions" else "ocr_hardscan_checkpoint_request",
                                     "operation": profile, "inputs": inputs,
                                     "configuration": configuration, "identity": identity})
    _read_inputs(specs, request, profile=profile)
    if resume:
        previous = checkpoint.validate_request(_json(_snapshot(output / "request.json", 128 * 1024)[0]))
        if not common.same(previous, request):
            raise ValueError(f"{label} checkpoint generation differs from current inputs or runtime")
    return source, output, policy, specs, request


def region_checkpoint_request(pdf_path: Path, output_dir: Path, *, operation: str = "regions",
                              policy: ocr_recovery.RetryPolicy | None = None,
                              requested_pages: tuple[int, ...] = (), evidence_path: Path | None = None,
                              installation_path: Path | None = None, recovery_path: Path | None = None,
                              plan_path: Path | None = None, resume: bool = False) -> tuple:
    """Read-only exact region inputs/runtime preflight for a new or resumed generation."""
    return _checkpoint_request(pdf_path, output_dir, profile="regions", operation=operation, policy=policy,
                               requested_pages=requested_pages, evidence_path=evidence_path,
                               installation_path=installation_path, recovery_path=recovery_path,
                               plan_path=plan_path, resume=resume)


def _state(journal: _Journal, recovery: dict, region_plan: dict, *, profile: str = "regions") -> dict:
    checkpoint, label = _profile(profile)
    used_vertices = 0
    names = journal.scan()
    request, request_digest = journal.read("request.json")
    if not common.same(checkpoint.validate_request(request), journal.request):
        raise ValueError(f"{label} checkpoint request changed")
    segments, segment_names = _read_segments(journal, names, request_digest)
    checkpoint.validate_segment_order(segments)
    starts, results, used = {}, {}, {"request.json"} | segment_names
    plan, plan_digest = None, None
    if "plan.json" in names:
        plan, plan_digest = journal.read("plan.json")
        checkpoint.validate_plan(plan, request, request_digest, recovery=recovery, region_plan=region_plan)
        used.add("plan.json")
        missing_start, pending, prior_origin = False, False, 0
        for entry in plan["regions"]:
            ordinal = entry["ordinal"]
            name = f"region-{ordinal:04d}.start.json"
            if name not in names:
                missing_start = True
                continue
            if missing_start or pending:
                raise ValueError(f"{label} checkpoint starts contradict sequential commit order")
            start, start_digest = journal.read(name)
            checkpoint.validate_start(start, request_sha256=request_digest, checkpoint_plan_sha256=plan_digest,
                                entry=entry, segments=segments)
            if start["origin_segment"] < prior_origin:
                raise ValueError(f"{label} checkpoint origin segments contradict item order")
            prior_origin = start["origin_segment"]
            starts[ordinal] = (start, start_digest)
            used.add(name)
            result_name = f"region-{ordinal:04d}.result.json"
            if result_name in names:
                result, result_digest = journal.read(result_name)
                bounds = {} if profile == "regions" else {"vertex_budget":
                    request["configuration"]["retry_configuration"]["max_total_vertices"] - used_vertices}
                usage = checkpoint.validate_result(result, request=request, plan=plan, checkpoint_plan_sha256=plan_digest,
                                             entry=entry, start=start, start_sha256=start_digest,
                                             segments=segments, **bounds)
                if result["receipt"] is not None:
                    validate_execution_receipt(result["receipt"], installation_path=journal.installation_path)
                if profile == "hardscan":
                    used_vertices += usage
                prior_origin = max(prior_origin, result["sealed_by_segment"])
                results[ordinal] = (result, result_digest)
                used.add(result_name)
            else:
                pending = True
                if segments[start["origin_segment"]]["status"] == "finished":
                    raise ValueError(f"finished {label} checkpoint segment has an unresolved attempt")
    if names - used - {"report.json", "manifest.json"}:
        raise ValueError(f"{label} checkpoint contains out-of-sequence records")
    finished = [number for number, segment in segments.items() if segment["status"] == "finished"]
    if finished and (plan is None or len(results) != len(plan["regions"])
                     or any(start["origin_segment"] > finished[0] for start, _ in starts.values())
                     or any(result["sealed_by_segment"] > finished[0] for result, _ in results.values())):
        raise ValueError(f"{label} checkpoint work contradicts a finished segment")
    state = {"request_sha256": request_digest, "plan": plan, "checkpoint_plan_sha256": plan_digest,
             "segments": segments, "starts": starts, "results": results}
    if profile == "hardscan":
        state["used_vertices"] = used_vertices
    return state


def _completion(journal: _Journal, state: dict, recovery: dict, region_plan: dict, *, profile: str = "regions") -> tuple[dict, dict]:
    checkpoint, label = _profile(profile)
    if (state["plan"] is None or len(state["results"]) != len(state["plan"]["regions"])
            or not state["segments"] or any(segment["status"] == "open" for segment in state["segments"].values())):
        raise ValueError(f"{label} checkpoint generation is incomplete")
    report = checkpoint.assemble_report(journal.request, state["plan"], recovery, region_plan,
                                  [state["results"][entry["ordinal"]][0]["region"]
                                   for entry in state["plan"]["regions"]])
    return checkpoint.completion(journal.request, state, report), report


def _read_completion(journal: _Journal, state: dict, recovery: dict, region_plan: dict, *, profile: str = "regions") -> tuple[dict, dict]:
    checkpoint, label = _profile(profile)
    expected, report = _completion(journal, state, recovery, region_plan, profile=profile)
    saved_report, _ = journal.read("report.json")
    saved, _ = journal.read("manifest.json")
    if (not checkpoint.report_same(saved_report, report)
            or common.json_bytes(saved, limit=checkpoint.MAX_MANIFEST_BYTES)
            != common.json_bytes(expected, limit=checkpoint.MAX_MANIFEST_BYTES)):
        raise ValueError(f"{label} checkpoint completion contradicts committed records")
    if journal.pins["report.json"] != expected["report_sha256"]:
        raise ValueError(f"{label} checkpoint report bytes differ from completion")
    paths = _input_paths(journal.specs, journal.request, profile=profile)
    if profile == "regions":
        verify_request = ocr_regions.verify_region_review_request
    else:
        from ocr_hardscan_io import verify_request
    verify_request(
        saved_report, pdf_path=paths["source_sha256"], recovery_path=paths["recovery_sha256"],
        plan_path=paths["plan_sha256"], dpi=journal.request["configuration"]["retry_configuration"]["dpi"])
    journal.recheck()
    return saved, saved_report


def _read_checkpoint_completion(output_dir: Path, *, profile: str, request: dict, specs: list,
                                      installation_path: Path | None = None) -> dict:
    """Validate all origin records and exact completion without opening a PDF reader."""
    checkpoint, label = _profile(profile)
    request = checkpoint.validate_request(request)
    _input_paths(specs, request, profile=profile)
    with PathLease(output_dir, backend="ocr-checkpoint", collection_name="generation", operation="read",
                   timeout=0, resource_description=f"OCR {label} checkpoint generation"):
        journal = _Journal(Path(output_dir), specs, request, installation_path, profile=profile)
        journal.recheck()
        recovery, region_plan = _read_inputs(specs, request, profile=profile)
        state = _state(journal, recovery, region_plan, profile=profile)
        return _read_completion(journal, state, recovery, region_plan, profile=profile)[0]


def read_region_checkpoint_completion(output_dir: Path, *, request: dict, specs: list,
                                      installation_path: Path | None = None) -> dict:
    """Validate all origin records and exact completion without opening a PDF reader."""
    return _read_checkpoint_completion(output_dir, profile="regions", request=request, specs=specs,
                                       installation_path=installation_path)


def _save_result(journal: _Journal, state: dict, entry: dict, start: dict, start_digest: str,
                 number: int, outcome: dict, receipt: dict | None, result_state: str, *, profile: str = "regions") -> None:
    checkpoint, _ = _profile(profile)
    ordinal = entry["ordinal"]
    result = {"schema_version": 1, "kind": "ocr_region_checkpoint_result" if profile == "regions" else "ocr_hardscan_checkpoint_result",
              "request_sha256": state["request_sha256"], "checkpoint_plan_sha256": state["checkpoint_plan_sha256"],
              "ordinal": ordinal, "region_id": entry["region_id"], "page_number": entry["page_number"],
              "origin_segment": start["origin_segment"], "start_sha256": start_digest,
              "state": result_state, "sealed_by_segment": number, "region": outcome, "receipt": receipt}
    bounds = {} if profile == "regions" else {"vertex_budget":
        journal.request["configuration"]["retry_configuration"]["max_total_vertices"] - state["used_vertices"]}
    usage = checkpoint.validate_result(result, request=journal.request, plan=state["plan"],
                                 checkpoint_plan_sha256=state["checkpoint_plan_sha256"], entry=entry,
                                 start=start, start_sha256=start_digest, segments=state["segments"], **bounds)
    if receipt is not None:
        validate_execution_receipt(receipt, installation_path=journal.installation_path)
    digest = journal.put(f"region-{ordinal:04d}.result.json", result)
    state["starts"][ordinal] = (start, start_digest)
    state["results"][ordinal] = (result, digest)
    if profile == "hardscan":
        state["used_vertices"] += usage


def _seal_interrupted(journal: _Journal, state: dict, number: int, *, profile: str = "regions") -> None:
    checkpoint, _ = _profile(profile)
    if state["plan"] is None:
        return
    for entry in state["plan"]["regions"]:
        ordinal = entry["ordinal"]
        if ordinal in state["starts"] and ordinal not in state["results"]:
            start, start_digest = state["starts"][ordinal]
            outcome = checkpoint.failed_region(entry) if profile == "regions" else checkpoint.interrupted_region(entry)
            _save_result(journal, state, entry, start, start_digest, number,
                         outcome, None, "interrupted", profile=profile)


def _execute_unstarted(journal: _Journal, state: dict, recovery: dict, region_plan: dict,
                       source: Path, dpi: int, number: int, segment_digest: str, reader_factory, *, profile: str = "regions") -> None:
    checkpoint, label = _profile(profile)
    if reader_factory is None:
        if profile == "regions":
            from ocr_region_runtime import RapidOCRRegionReader
            reader_factory = RapidOCRRegionReader
        else:
            from ocr_hardscan_runtime import RapidOCRHardScanReader
            reader_factory = RapidOCRHardScanReader
    recorder = ExecutionRecorder()
    with artifact_io.immutable_file_snapshot(
            source, snapshot_name="source.pdf", expected_sha256=journal.request["inputs"]["source_sha256"]) as snapshot:
        with recorder.reader_factory(reader_factory)(
                snapshot.path, dpi=dpi, max_pixels=ocr_regions.MAX_PIXELS, max_side=ocr_regions.MAX_SIDE) as reader:
            # One complete cohort description per segment; retry keeps its own local geometry check.
            if profile == "regions":
                geometries, total = ocr_regions._describe_regions(reader, recovery, region_plan, dpi=dpi)
                plan = {"schema_version": 1, "kind": "ocr_region_checkpoint_plan",
                        "request_sha256": state["request_sha256"], "page_count": recovery["page_count"],
                        "coordinate_system": region_plan["coordinate_system"], "planned_pixels": total,
                        "regions": [{**copy.deepcopy(item), "ordinal": ordinal, "geometry": geometry}
                                    for ordinal, (item, geometry) in enumerate(zip(region_plan["regions"], geometries), 1)]}
            else:
                import ocr_hardscan_io as hardscan
                records = hardscan._describe_hardscan_regions(reader, recovery, region_plan, dpi=dpi)
                plan = {"schema_version": 1, "kind": "ocr_hardscan_checkpoint_plan",
                        "request_sha256": state["request_sha256"], "page_count": recovery["page_count"],
                        "coordinate_system": region_plan["coordinate_system"],
                        "planned_pixels": hardscan._planned_pixels(records),
                        "regions": [{**item, "ordinal": ordinal} for ordinal, item in enumerate(records, 1)]}
            checkpoint.validate_plan(plan, journal.request, state["request_sha256"], recovery=recovery, region_plan=region_plan)
            if state["plan"] is None:
                snapshot.verify()
                state["checkpoint_plan_sha256"] = journal.put("plan.json", plan)
                state["plan"] = plan
            elif not common.same(plan, state["plan"]):
                raise ValueError(f"{label} checkpoint geometry changed after its saved preflight")
            for entry in state["plan"]["regions"]:
                ordinal = entry["ordinal"]
                if ordinal in state["results"]:
                    continue
                if ordinal in state["starts"]:
                    raise ValueError(f"{label} checkpoint unresolved attempt was not sealed")
                journal.recheck()
                snapshot.verify()
                start = {"schema_version": 1, "kind": "ocr_region_checkpoint_start" if profile == "regions" else "ocr_hardscan_checkpoint_start",
                         "request_sha256": state["request_sha256"],
                         "checkpoint_plan_sha256": state["checkpoint_plan_sha256"], "ordinal": ordinal,
                         "region_id": entry["region_id"], "page_number": entry["page_number"],
                         "origin_segment": number, "segment_sha256": segment_digest}
                checkpoint.validate_start(start, request_sha256=state["request_sha256"],
                                    checkpoint_plan_sha256=state["checkpoint_plan_sha256"],
                                    entry=entry, segments=state["segments"])
                start_digest = journal.put(f"region-{ordinal:04d}.start.json", start)
                journal.recheck()
                snapshot.verify()
                before, started = len(recorder.calls), time.monotonic()
                if profile == "regions":
                    outcome = ocr_regions._retry_region(
                        reader, region_plan["regions"][ordinal - 1], entry["geometry"], dpi=dpi)
                else:
                    outcome = copy.deepcopy({key: value for key, value in entry.items() if key != "ordinal"})
                    hardscan._retry_hardscan_region(reader, outcome, dpi=dpi, vertex_budget=
                        journal.request["configuration"]["retry_configuration"]["max_total_vertices"] - state["used_vertices"])
                observation = reader.execution_observation() if reader._engine is not None else None
                calls = [{**call, "id": f"call-{index:04d}"} for index, call in enumerate(recorder.calls[before:], 1)]
                receipt = capture_execution_receipt(
                    operation=profile, inputs={**journal.request["inputs"],
                        "checkpoint_request_sha256": state["request_sha256"],
                        "checkpoint_plan_sha256": state["checkpoint_plan_sha256"],
                        ("region_start_sha256" if profile == "regions" else "hardscan_start_sha256"): start_digest},
                    output_sha256=checkpoint.outcome_digest(outcome), observation=observation, calls=calls,
                    elapsed_seconds=time.monotonic() - started, installation_path=journal.installation_path)
                snapshot.verify()
                _save_result(journal, state, entry, start, start_digest, number, outcome, receipt, "committed",
                             profile=profile)
        snapshot.verify()


def _run_checkpointed(pdf_path: Path, output_dir: Path, *, profile: str, resume: bool = False,
                             reader_factory=None, **options) -> dict:
    """Execute unstarted regions only; completed or interrupted attempts are retained."""
    _, label = _profile(profile)
    if profile == "regions":
        source, output, policy, specs, request = region_checkpoint_request(
            pdf_path, output_dir, resume=resume, **options)
    else:
        source, output, policy, specs, request = _checkpoint_request(
            pdf_path, output_dir, profile=profile, resume=resume, **{"operation": "hardscan", **options})
    installation = options.get("installation_path")
    with PathLease(output, backend="ocr-checkpoint", collection_name="generation",
                   operation="resume" if resume else "execute", timeout=0,
                   resource_description=f"OCR {label} checkpoint generation"):
        if not resume:
            output.mkdir(exist_ok=False)
            storage_policy.enforce_private_path(output, directory=True)
        journal = _Journal(output, specs, request, installation, profile=profile)
        journal.recheck()
        recovery, region_plan = _read_inputs(specs, request, profile=profile)
        if not resume:
            journal.put("request.json", request)
        state = _state(journal, recovery, region_plan, profile=profile)
        if (output / "manifest.json").exists():
            manifest, report = _read_completion(journal, state, recovery, region_plan, profile=profile)
            return {"manifest": manifest, "report": report}
        if (output / "report.json").exists() or (
                state["plan"] is not None and state["segments"]
                and len(state["results"]) == len(state["plan"]["regions"])
                and all(segment["status"] != "open" for segment in state["segments"].values())):
            manifest, report = _completion(journal, state, recovery, region_plan, profile=profile)
            journal.put("report.json", report, adopt=True)
            journal.put("manifest.json", manifest)
            return {"manifest": manifest, "report": report}
        number = len(state["segments"]) + 1
        common.integer(number, 1, common.MAX_SEGMENTS)
        for previous, segment in state["segments"].items():
            if segment["status"] == "open":
                journal.put(f"segment-{previous:04d}.end.json", {
                    "schema_version": 1, "kind": "ocr_checkpoint_segment_end", "segment_number": previous,
                    "request_sha256": state["request_sha256"], "status": "interrupted"})
        segment_digest = journal.put(f"segment-{number:04d}.start.json", {
            "schema_version": 1, "kind": "ocr_checkpoint_segment_start",
            "request_sha256": state["request_sha256"], "segment_number": number})
        state = _state(journal, recovery, region_plan, profile=profile)
        _seal_interrupted(journal, state, number, profile=profile)
        if state["plan"] is None or len(state["results"]) != len(state["plan"]["regions"]):
            _execute_unstarted(journal, state, recovery, region_plan, source, policy.dpi,
                               number, segment_digest, reader_factory, profile=profile)
        journal.put(f"segment-{number:04d}.end.json", {
            "schema_version": 1, "kind": "ocr_checkpoint_segment_end",
            "request_sha256": state["request_sha256"], "segment_number": number, "status": "finished"})
        state = _state(journal, recovery, region_plan, profile=profile)
        manifest, report = _completion(journal, state, recovery, region_plan, profile=profile)
        journal.put("report.json", report)
        journal.put("manifest.json", manifest)
        return {"manifest": manifest, "report": report}


def run_checkpointed_regions(pdf_path: Path, output_dir: Path, *, resume: bool = False,
                             reader_factory=None, **options) -> dict:
    """Execute unstarted regions only; completed or interrupted attempts are retained."""
    return _run_checkpointed(pdf_path, output_dir, profile="regions", resume=resume,
                             reader_factory=reader_factory, **options)
