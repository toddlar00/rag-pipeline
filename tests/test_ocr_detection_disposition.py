"""Model-free full-cohort lineage policy; synthetic observations are not OCR proof."""

import copy
import json
import math
from types import SimpleNamespace

import pytest

import ocr_detection_disposition as policy
import ocr_disposition_geometry as geometry
import ocr_hardscan_io
import ocr_regions
from test_ocr_hardscan_io import HardReader, recipe
from test_ocr_recovery_comparison import DIGEST, HEALTHY, _preprocessed_report, _report
from test_ocr_regions import RegionReader


DERIVATION = policy.DispositionDerivation(geometry.replay_engine_box, geometry.map_source_polygon)
NONE_BOXES = {"state": "none", "dtype": None, "shape": None}


def boxes(count, dtype="float32"):
    return {"state": "array", "dtype": dtype, "shape": [count, 4, 2]}


def role(state="completed", sessions=1):
    if state == "unobserved":
        return dict(state=state, **dict.fromkeys(("attempted", "completed", "failed", "session_attempted", "session_completed", "session_failed")))
    return {"state": state, "attempted": int(state != "not_run"), "completed": int(state == "completed"),
            "failed": int(state == "failed"), "session_attempted": sessions,
            "session_completed": sessions if state == "completed" else 0,
            "session_failed": sessions if state == "failed" else 0}


def stage(state="not_run", count=None, output=None, before=None, after=None):
    return {"state": state, "input_count": count, "output_count": output,
            "box_input": copy.deepcopy(before), "box_output": copy.deepcopy(after)}


def fixture(operation="pages", *, saved=None, region_count=1, bow=0.0):
    """Real legacy builders plus an explicitly inert receipt projection port.

    IO tests own the complete receipt validator/file generation. This leaf's
    independent receipt view has the exact fields it consumes, not model proof.
    """
    saved = _report() if saved is None else saved
    report = saved
    if operation != "pages":
        selected = [{"region_id": f"r{i:02d}", "page_number": 1, "bbox": [.1, .2, .8, .9]}
                    for i in range(region_count)]
        plan = {"schema_version": 1, "source_sha256": DIGEST, "recovery_sha256": "b" * 64,
                "coordinate_system": "original_page_display_fraction", "regions": selected}
        reader = (RegionReader if operation == "regions" else HardReader).__new__(RegionReader if operation == "regions" else HardReader)
        reader.page_count, reader.dpi, reader.events, reader.hook = saved["page_count"], 300, [], None
        if operation == "hardscan":
            plan.update(kind="ocr_hardscan_plan", approval="operator_approved")
            for item in selected:
                item["recipe"] = recipe(bow=bow)
        report = (ocr_regions._build if operation == "regions" else ocr_hardscan_io._build)(reader, saved, plan, dpi=300)
        report["inputs"] = {"pdf_sha256": DIGEST, "recovery_sha256": "b" * 64, "plan_sha256": "c" * 64}
        if operation == "hardscan":
            ocr_hardscan_io.validate_report(report)
        else:
            ocr_regions.validate_region_review(report)
    configuration = {"operation": operation, "policy": {"dpi": 300}, "requested_pages": []}
    if operation == "pages":
        configuration["policy"].update(max_pages=report["selection"]["max_pages"], min_chars=report["selection"]["min_chars"],
                                       max_pixels=report["retry_configuration"]["max_pixels"], max_side=report["retry_configuration"]["max_side"],
                                       preprocessing=report["retry_configuration"].get("preprocessing", "none"))
        configuration["requested_pages"] = report["selection"]["requested_pages"][:]
    inputs = dict.fromkeys(("source_sha256", "dependency_full_sha256", "dependency_test_sha256", "dependency_tools_sha256",
                           "model_policy_sha256", "model_lock_sha256"), DIGEST)
    inputs["configuration_sha256"] = policy.canonical_sha256(configuration)
    if operation != "pages":
        inputs.update(recovery_sha256="b" * 64, plan_sha256="c" * 64)
    request = {"schema_version": 1, "kind": "ocr_disposition_request", "operation": operation,
               "configuration": configuration, "inputs": inputs, "recipe_id": policy.RECIPE_ID,
               "limits": dict(policy.LIMITS), "producer_generation_sha256": "d" * 64}
    bindings = {"request_sha256": policy.canonical_sha256(request), "report_sha256": "e" * 64,
                "execution_sha256": "f" * 64, "producer_generation_sha256": "d" * 64, "source_sha256": DIGEST}
    rows = report["pages"] if operation == "pages" else report["regions"]
    attempts = [row for row in rows if operation != "hardscan" or row["transform"]["eligibility"] == "ready"]
    execution = {"schema_version": 1, "kind": "ocr_execution_receipt", "operation": operation,
                 "inputs": copy.deepcopy(inputs), "output_sha256": bindings["report_sha256"], "calls": []}
    observations = {"schema_version": 1, "kind": "ocr_disposition_observations", "operation": operation,
                    "request_sha256": bindings["request_sha256"], "attempts": []}
    bundle = SimpleNamespace(request=request, report=report, execution=execution, bindings=bindings, observations=observations,
                             derivation=DERIVATION)
    for i, row in enumerate(attempts, 1):
        identifier = f"page-{row['page_number']:05d}" if operation == "pages" else f"region-{rows.index(row)+1:04d}"
        snapshot = {"item_id": identifier, "attempt_index": i, "dispatch": None,
                    "diagnostic_state": "not_run", "diagnostic_reason": "no_raw_dispatch", "settings": None, "raster": None,
                    "roles": None, "stages": None, "detection_count": None, "detections": [], "raw_output": None, "work": policy.zero_work()}
        observations["attempts"].append(snapshot)
        if row["candidate"] is not None:
            attach_call(bundle, snapshot)
            complete(snapshot, row["candidate"])
    return bundle


def attach_call(bundle, snapshot, status="completed"):
    index = len(bundle.execution["calls"]) + 1
    call = {"id": f"call-{index:04d}", "status": status, "elapsed_seconds": .01}
    bundle.execution["calls"].append(call)
    ticket = {"request_sha256": bundle.bindings["request_sha256"], "item_id": snapshot["item_id"],
              "attempt_index": snapshot["attempt_index"], "call_id": call["id"], "dispatch_ordinal": index}
    snapshot["dispatch"] = {"call_id": call["id"], "ordinal": index, "ticket_sha256": policy.canonical_sha256(ticket), "raw_status": status}


def complete(snapshot, candidate, records=None, engine_threshold=0.0, reader_threshold=None, dtype="float32"):
    width, height = (candidate["raster"][key] for key in ("width", "height"))
    dims = policy.working_dimensions(width, height, detection=True)
    global_w, global_h = dims["global"]
    padded_w, padded_h = dims["padded"]
    raster = {"width": width, "height": height, "pixel_sha256": DIGEST, "coordinate_system": "engine_input_bgr_uint8_pixels",
              "global_width": global_w, "global_height": global_h, "padded_width": padded_w, "padded_height": padded_h,
              "ratio_h": height/global_h, "ratio_w": width/global_w, "padding_top": (padded_h-global_h)//2,
              "padding_left": 0, "detector_box_dtype": dtype}
    raster["operations"] = [{"kind": "preprocess", "ratio_h": raster["ratio_h"], "ratio_w": raster["ratio_w"]},
                            {"kind": "padding_1", "top": raster["padding_top"], "left": 0}]
    source = candidate["lines"] if records is None else records
    detections = []
    for i, line in enumerate(source, 1):
        # Our synthetic inputs use identity Global mapping and zero padding.
        detector_box = copy.deepcopy(line["box"])
        mapped = geometry.replay_engine_box(dtype=dtype, box=detector_box, raster=raster)
        rec = policy._line_fingerprint(line["text"], line["score"], mapped)
        rec.pop("box")
        detections.append({"id": f"d{i:04d}", "ordinal": i, "detector_box": detector_box, "detector_score": None,
                           "detector_score_alignment": "upstream_alignment_unavailable", "crop_state": "completed",
                           "classification": {"state": "completed", "label": "0", "score": .95, "rotated_180": False},
                           "recognition": {"state": "completed", **rec}, "engine_input_box": mapped, "engine_mapping": "verified"})
    count = len(detections)
    if not count:
        raster["detector_box_dtype"] = None
    retained = [d for d in detections if d["recognition"]["text_kind"] == "nonempty" and d["recognition"]["score"] >= engine_threshold]
    nonblank = sum(d["recognition"]["text_kind"] == "nonempty" for d in detections)
    final_boxes = boxes(len(retained), dtype) if retained else NONE_BOXES
    stages = {name: stage() for name in policy._STAGES}
    stages["detector"] = stage("completed", output=count, after=boxes(count, dtype) if count else NONE_BOXES)
    stages["formatter"] = stage("completed", count, len(retained), boxes(count, dtype) if count else NONE_BOXES, final_boxes)
    if count:
        for name in ("crops", "classifier", "recognizer"):
            stages[name] = stage("completed", count, count)
        stages["score_filter"] = stage("completed", nonblank, len(retained), boxes(nonblank, dtype),
                                      boxes(len(retained), dtype) if retained else {"state": "array", "dtype": "float64", "shape": [0]})
    snapshot.update(diagnostic_state="complete", diagnostic_reason=None,
                    settings={"use_det": True, "use_cls": True, "use_rec": True, "return_word_box": False,
                              "return_single_char_box": False, "engine_text_score": engine_threshold,
                              "reader_min_score": candidate["engine"]["min_score"] if reader_threshold is None else reader_threshold,
                              "classification_threshold": .9}, raster=raster,
                    roles={"detection": role(), **{name: role("completed" if count else "not_run", (count+5)//6)
                                                   for name in ("classification", "recognition")}},
                    stages=stages, detection_count=count, detections=detections,
                    raw_output={"boxes": copy.deepcopy(final_boxes), "lines": [policy._observed_line(d) for d in retained]},
                    work={"detections_reserved": count, "codepoints_charged": sum(len(line["text"]) for line in source),
                          "vertices_charged": 0, "raster_bytes_hashed": width*height*3, "exhausted": None})


def build(bundle):
    return policy.build_disposition(bundle.observations, **arguments(bundle))


def arguments(bundle):
    return {key: getattr(bundle, key) for key in ("request", "report", "execution", "bindings", "derivation")}


def validate(bundle, result):
    return policy.validate_disposition(result, **arguments(bundle))


def mutate(value, path, changed):
    for part in path[:-1]:
        value = value[part]
    value[path[-1]] = changed


@pytest.mark.parametrize("operation", ["pages", "regions", "hardscan"])
def test_all_routes_use_real_final_report_and_exact_detached_join(operation):
    bundle = fixture(operation)
    before = copy.deepcopy(bundle.__dict__)
    result = build(bundle)
    assert validate(bundle, result) == result
    assert result["summary"]["joined_candidate_lines"] == 1
    assert result["items"][0]["candidate"]["join"] == "verified_ordered"
    assert result["items"][0]["diagnostic"]["detections"][0]["physical"]["state"] == "available"
    assert bundle.__dict__ == before
    text = bundle.report["pages" if operation == "pages" else "regions"][0]["candidate"]["text"]
    assert text not in json.dumps(result)
    result["items"][0]["diagnostic"]["detections"][0]["detector_box"][0][0] = 1
    assert bundle.observations["attempts"][0]["detections"][0]["detector_box"][0][0] == 0


def test_full_page_coverage_differs_from_requested_first_attempts_and_call_order():
    bundle = fixture(saved=_report(native=("a", "b", HEALTHY, "d", "e"), candidates={1: ValueError("private")}, max_pages=2, requested=(5,)))
    result = build(bundle)
    assert validate(bundle, result) == result
    assert result["attempt_order"] == ["page-00005", "page-00001"]
    assert result["dispatch_order"] == ["page-00005"]
    assert [item["selection"] for item in result["items"]] == ["selected", "deferred", "not_selected", "deferred", "selected"]
    assert result["summary"]["covered_items"] == 5


def test_explicit_requested_page_deferred_kept_in_full_cohort():
    bundle = fixture(saved=_report(native=("a", "b"), max_pages=1, requested=(1, 2)))
    result = build(bundle)
    assert validate(bundle, result) == result
    assert result["items"][1]["explicitly_requested"] is True
    assert result["items"][1]["attempt_index"] is None


@pytest.mark.parametrize("operation", ["regions", "hardscan"])
def test_same_page_regions_keep_distinct_ordinal_call_identity(operation):
    bundle = fixture(operation, region_count=2)
    result = build(bundle)
    assert validate(bundle, result) == result
    assert result["attempt_order"] == ["region-0001", "region-0002"]
    assert result["items"][0]["dispatch"]["ticket_sha256"] != result["items"][1]["dispatch"]["ticket_sha256"]


@pytest.mark.parametrize("kind", ["no_detection", "all_blank", "all_filtered"])
def test_distinct_empty_routes_do_not_use_default_output_as_proof(kind):
    bundle = fixture(saved=_report(candidates={1: ""}))
    snapshot = bundle.observations["attempts"][0]
    candidate = bundle.report["pages"][0]["candidate"]
    records = [] if kind == "no_detection" else [{"text": " \t" if kind == "all_blank" else "below", "score": .2,
                                                 "box": [[0., 0.], [80., 0.], [80., 20.], [0., 20.]]}]
    complete(snapshot, candidate, records, engine_threshold=.5 if kind == "all_filtered" else 0.)
    if kind == "all_filtered":
        # Nonzero filtering is a reconciliation arithmetic control, not the
        # public v1 invocation recipe (which deliberately binds threshold0).
        assert policy._complete({"detection_count": snapshot["detection_count"], "settings": snapshot["settings"],
                                 "stages": snapshot["stages"], "roles": snapshot["roles"], "raster": snapshot["raster"],
                                 "detections": snapshot["detections"]}) == ["removed_score"]
        assert build(bundle)["items"][0]["diagnostic"]["reason"] == "unsupported_recipe"
        return
    result = build(bundle)
    assert validate(bundle, result) == result
    assert result["summary"]["known_detections"] == len(records)
    assert result["summary"]["removed_blank"] == int(kind == "all_blank")
    assert result["summary"]["removed_score"] == int(kind == "all_filtered")
    assert result["items"][0]["candidate"]["join"] == "verified_ordered"


def test_engine_and_reader_thresholds_are_distinct_and_equality_is_retained():
    bundle = fixture()
    candidate = bundle.report["pages"][0]["candidate"]
    candidate["engine"]["min_score"] = .9
    line = candidate["lines"][0]
    records = [{**line, "text": "", "score": .1}, {**line, "text": "below engine", "score": .49},
               {**line, "text": "below reader", "score": .5}, line]
    complete(bundle.observations["attempts"][0], candidate, records, engine_threshold=.5)
    # Exercise the internal reconciliation arithmetic explicitly; this is NOT
    # a supported v1 external request and cannot be published as complete.
    snapshot = bundle.observations["attempts"][0]
    item = policy._context(bundle.request, bundle.report, bundle.execution, bundle.bindings)[2][0]
    diag = {"state": "complete", "reason": None, **{key: snapshot[key] for key in
            ("settings", "raster", "roles", "stages", "detection_count", "detections")},
            "mapping": policy._mapping("pages", bundle.report["pages"][0])}
    policy._reconcile(diag, item, bundle.report["pages"][0], DERIVATION, raw_output=snapshot["raw_output"])
    records = diag["detections"]
    assert [d["terminal"] for d in records] == ["removed_blank", "removed_score", "retained", "retained"]
    assert [d["candidate_disposition"] for d in records] == ["not_retained", "not_retained", "reader_score_filtered", "accepted"]
    assert [d["candidate_index"] for d in records] == [None, None, None, 1]


@pytest.mark.parametrize("operation", ["pages", "regions", "hardscan"])
def test_final_orchestrator_rejection_overrides_successful_reader_lineage(operation):
    bundle = fixture(operation)
    row = bundle.report["pages" if operation == "pages" else "regions"][0]
    row.update(status="retry_failed", candidate=None, error_code="retry_limit_or_validation")
    if operation == "regions":
        row["page_boxes"] = None
    if operation == "hardscan":
        row.update(source_polygons=None, processing=None)
    result = build(bundle)
    assert validate(bundle, result) == result
    record = result["items"][0]["diagnostic"]["detections"][0]
    assert record["terminal"] == "retained" and record["candidate_disposition"] == "candidate_unavailable"
    assert record["candidate_index"] is None


@pytest.mark.parametrize("mutation", ["source", "request", "generation", "report", "operation", "policy"])
def test_independent_bindings_and_policy_changes_fail(mutation):
    bundle = fixture()
    if mutation == "policy":
        bundle.report["retry_configuration"]["dpi"] = 400
    elif mutation == "operation":
        bundle.execution["operation"] = "regions"
    else:
        bundle.bindings[{"source": "source_sha256", "request": "request_sha256", "generation": "producer_generation_sha256", "report": "report_sha256"}[mutation]] = "0" * 64
    with pytest.raises(ValueError):
        build(bundle)


@pytest.mark.parametrize("path,value", [
    (("schema_version",), True), (("schema_version",), 1.0), (("attempts", 0, "attempt_index"), True),
    (("attempts", 0, "dispatch", "ordinal"), 2), (("attempts", 0, "dispatch", "ticket_sha256"), "0"*64),
    (("attempts", 0, "stages", "detector", "box_output"), None),
    (("attempts", 0, "stages", "detector", "box_output"), NONE_BOXES),
    (("attempts", 0, "stages", "formatter", "box_input"), None),
    (("attempts", 0, "roles", "detection", "session_completed"), 0),
    (("attempts", 0, "roles", "classification", "session_attempted"), 2),
    (("attempts", 0, "roles", "recognition", "session_failed"), 1),
    (("attempts", 0, "detections", 0, "ordinal"), True),
    (("attempts", 0, "detections", 0, "detector_score"), .9),
    (("attempts", 0, "detections", 0, "recognition", "codepoints"), 4097),
    (("attempts", 0, "detections", 0, "recognition", "score"), float("nan")),
    (("attempts", 0, "work", "vertices_charged"), 4),
    (("attempts", 0, "work", "codepoints_charged"), 0),
    (("attempts", 0, "raster", "operations"), []),
    (("attempts", 0, "raster", "width"), True),
])
def test_malformed_snapshot_fields_fail_without_repair(path, value):
    bundle = fixture()
    mutate(bundle.observations, path, copy.deepcopy(value))
    with pytest.raises(ValueError):
        build(bundle)


@pytest.mark.parametrize("path,value", [
    (("summary", "raw_calls"), 0), (("summary", "known_detections"), 0),
    (("items", 0, "candidate", "join"), "unavailable"),
    (("items", 0, "diagnostic", "detections", 0, "candidate_index"), 2),
    (("items", 0, "diagnostic", "detections", 0, "terminal"), "removed_score"),
    (("items", 0, "diagnostic", "detections", 0, "engine_input_box", 0, 0), 1.0),
    (("items", 0, "diagnostic", "detections", 0, "physical", "polygon", 0, 0), .1),
    (("items", 0, "work", "detections_reserved"), 0),
    (("items", 0, "dispatch", "raw_status"), "failed"),
])
def test_submitted_contradictions_are_rejected_not_disabled(path, value):
    bundle = fixture()
    result = build(bundle)
    mutate(result, path, value)
    with pytest.raises(ValueError):
        validate(bundle, result)


def test_unselected_item_cannot_forge_extra_dispatch_or_work():
    bundle = fixture(saved=_report(native=("a", HEALTHY)))
    result = build(bundle)
    result["items"][1]["dispatch"] = copy.deepcopy(result["items"][0]["dispatch"])
    result["items"][1]["diagnostic"] = copy.deepcopy(result["items"][0]["diagnostic"])
    result["summary"] = policy._summary(result["items"], result["attempt_order"], result["dispatch_order"])
    with pytest.raises(ValueError):
        validate(bundle, result)


def test_failed_raw_call_cannot_supply_accepted_candidate_even_with_unavailable_ledger():
    bundle = fixture()
    snapshot = bundle.observations["attempts"][0]
    bundle.execution["calls"][0]["status"] = snapshot["dispatch"]["raw_status"] = "failed"
    snapshot.update(diagnostic_state="unavailable", diagnostic_reason="callback_failed", roles=None, stages=None,
                    detection_count=None, detections=[], raw_output=None)
    with pytest.raises(ValueError):
        build(bundle)


@pytest.mark.parametrize("fault", ["raw_output", "remap", "candidate_text", "candidate_order"])
def test_builder_lineage_fault_disables_diagnostic_without_touching_candidate(fault):
    bundle = fixture()
    snapshot = bundle.observations["attempts"][0]
    if fault == "raw_output":
        snapshot["raw_output"]["lines"][0]["text_sha256"] = "0" * 64
    elif fault == "remap":
        snapshot["detections"][0]["engine_input_box"][0][0] = 1.0
    elif fault == "candidate_text":
        bundle.report["pages"][0]["candidate"]["lines"][0]["text"] = "DIFFERENT"
    else:
        snapshot["raw_output"]["lines"].append(copy.deepcopy(snapshot["raw_output"]["lines"][0]))
        snapshot["raw_output"]["boxes"] = boxes(2)
    before = copy.deepcopy(bundle.report)
    result = build(bundle)
    assert result["items"][0]["diagnostic"]["reason"] == "lineage_invalid"
    assert result["items"][0]["candidate"]["state"] == "accepted"
    assert bundle.report == before
    assert validate(bundle, result) == result


@pytest.mark.parametrize("error", [KeyboardInterrupt, SystemExit])
def test_internal_derivation_cancellation_is_not_ordinary_ledger_failure(error):
    bundle = fixture()
    def cancel(**_kwargs):
        raise error()
    bundle.derivation = policy.DispositionDerivation(cancel, geometry.map_source_polygon)
    with pytest.raises(error):
        build(bundle)


def test_ordinary_mapper_failure_is_unavailable_and_candidate_remains_joined():
    bundle = fixture()
    def fail(**_kwargs):
        raise RuntimeError("PRIVATE")
    bundle.derivation = policy.DispositionDerivation(geometry.replay_engine_box, fail)
    result = build(bundle)
    assert result["items"][0]["diagnostic"]["detections"][0]["physical"] == policy._physical("mapping_failed")
    assert result["items"][0]["candidate"]["join"] == "verified_ordered"
    bundle.derivation = DERIVATION
    assert validate(bundle, result) == result


def test_dependency_light_import_does_not_import_models_or_filesystem_readers():
    import subprocess
    import sys
    completed = subprocess.run([sys.executable, "-c", "import sys; import ocr_detection_disposition; "
                               "assert not any(n in sys.modules for n in ['numpy','cv2','fitz','rapidocr','ocr_recovery','ocr_regions'])"],
                               capture_output=True, timeout=20)
    assert completed.returncode == 0, completed.stderr.decode()


@pytest.mark.parametrize("operation,count", [("pages", 1), ("pages", 5000), ("regions", 20), ("hardscan", 20)])
def test_worst_case_pre_ocr_envelope_is_proven_in_fixed_writer_format(operation, count):
    reserve = policy.reserve_envelope(operation, item_count=count)
    item = policy._maximal_envelope_item()
    assert len((json.dumps(item, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n").encode()) <= 2048
    assert reserve == 65536 + count*2048 <= 16777216
    assert policy.reserve_envelope("pages") == 10305536


@pytest.mark.parametrize("operation,count", [("pages", 0), ("pages", 5001), ("regions", 21), ("hardscan", True), ("pages", 1.0), ("unknown", 1)])
def test_invalid_reservation_rejected_before_observation(operation, count):
    with pytest.raises(ValueError):
        policy.reserve_envelope(operation, item_count=count)


def test_preprocessed_page_maps_back_to_original_coordinates():
    bundle = fixture(saved=_preprocessed_report())
    result = build(bundle)
    assert validate(bundle, result) == result
    assert result["items"][0]["diagnostic"]["mapping"]["kind"] == "preprocessed_page"


@pytest.mark.parametrize("bow", [.02, -.02])
def test_nonlinear_source_polygon_exact_final_report_join(bow):
    bundle = fixture("hardscan", bow=bow)
    result = build(bundle)
    assert validate(bundle, result) == result
    assert result["items"][0]["diagnostic"]["detections"][0]["physical"]["polygon"] == bundle.report["regions"][0]["source_polygons"][0]


def reject_candidate(bundle, index=0):
    row = bundle.report["pages" if bundle.request["operation"] == "pages" else "regions"][index]
    row.update(status="retry_failed", candidate=None, error_code="retry_runtime_failed")


def partial_recognition_failure(bundle, *, raw_status="failed"):
    snapshot = bundle.observations["attempts"][0]
    reject_candidate(bundle)
    bundle.execution["calls"][0]["status"] = snapshot["dispatch"]["raw_status"] = raw_status
    snapshot.update(diagnostic_state="partial", diagnostic_reason="ocr_path_incomplete", raw_output=None)
    snapshot["roles"]["recognition"] = role("failed")
    snapshot["stages"]["recognizer"] = stage("failed", snapshot["detection_count"])
    snapshot["stages"]["formatter"] = stage("unobserved", snapshot["detection_count"], before=boxes(snapshot["detection_count"]))
    snapshot["stages"]["score_filter"] = stage()
    for record in snapshot["detections"]:
        record["recognition"] = dict(state="unobserved", **dict.fromkeys(("text_kind", "text_sha256", "codepoints", "score")))
        record.update(engine_mapping="unobserved", engine_input_box=None)
    return snapshot


@pytest.mark.parametrize("raw_status", ["failed", "completed"])
def test_partial_actual_failure_keeps_ids_and_unknown_not_predicted_filtering(raw_status):
    bundle = fixture()
    partial_recognition_failure(bundle, raw_status=raw_status)
    result = build(bundle)
    assert validate(bundle, result) == result
    item = result["items"][0]
    assert item["diagnostic"]["state"] == "partial"
    assert item["diagnostic"]["detections"][0]["terminal"] == "unresolved"
    assert item["diagnostic"]["detections"][0]["physical"]["reason"] == "engine_mapping_unobserved"
    assert item["candidate"]["state"] == "rejected"


def test_unknown_detector_is_not_fabricated_zero():
    bundle = fixture()
    snapshot = partial_recognition_failure(bundle)
    snapshot.update(detection_count=None, detections=[])
    snapshot["roles"] = {"detection": role("failed"), "classification": role("not_run", 0), "recognition": role("not_run", 0)}
    snapshot["stages"] = {name: stage() for name in policy._STAGES}
    snapshot["stages"]["detector"] = stage("failed")
    result = build(bundle)
    assert validate(bundle, result) == result
    assert result["summary"]["detections_unknown_items"] == 1
    assert result["items"][0]["diagnostic"]["detection_count"] is None


@pytest.mark.parametrize("name", ["classification", "recognition"])
@pytest.mark.parametrize("sessions", [0, 1, 3, 167])
def test_complete_seven_detections_requires_exactly_two_sessions(name, sessions):
    bundle = fixture()
    candidate = bundle.report["pages"][0]["candidate"]
    candidate["lines"] *= 7
    candidate["text"] = "\n".join(line["text"] for line in candidate["lines"])
    snapshot = bundle.observations["attempts"][0]
    complete(snapshot, candidate)
    snapshot["roles"][name] = role(sessions=sessions)
    with pytest.raises(ValueError):
        build(bundle)


def test_duplicate_boxes_and_text_keep_distinct_occurrence_indices():
    bundle = fixture()
    candidate = bundle.report["pages"][0]["candidate"]
    candidate["lines"] *= 7
    candidate["text"] = "\n".join(line["text"] for line in candidate["lines"])
    complete(bundle.observations["attempts"][0], candidate)
    result = build(bundle)
    assert validate(bundle, result) == result
    assert [record["candidate_index"] for record in result["items"][0]["diagnostic"]["detections"]] == list(range(1, 8))


def test_no_call_failure_between_actual_calls_has_no_receipt_gap():
    bundle = fixture(saved=_report(native=("a", "b", "c"), candidates={2: ImportError("private")}))
    result = build(bundle)
    assert validate(bundle, result) == result
    assert result["attempt_order"] == ["page-00001", "page-00002", "page-00003"]
    assert result["dispatch_order"] == ["page-00001", "page-00003"]
    assert result["items"][1]["dispatch"] is None
    assert result["items"][2]["dispatch"]["ordinal"] == 2


def test_hardscan_preflight_abstention_has_coverage_but_no_attempt():
    bundle = fixture("hardscan", bow=.0001)
    assert bundle.report["regions"][0]["transform"]["eligibility"] == "abstained"
    result = build(bundle)
    assert validate(bundle, result) == result
    assert result["attempt_order"] == [] and result["items"][0]["candidate"]["state"] == "abstained"


def test_post_preflight_processing_abstention_has_attempt_but_no_raw_call():
    bundle = fixture("hardscan")
    row = bundle.report["regions"][0]
    row.update(status="abstained", candidate=None, source_polygons=None)
    snapshot = bundle.observations["attempts"][0]
    snapshot.update(dispatch=None, diagnostic_state="not_run", diagnostic_reason="no_raw_dispatch", settings=None, raster=None,
                    roles=None, stages=None, detection_count=None, detections=[], raw_output=None, work=policy.zero_work())
    bundle.execution["calls"] = []
    result = build(bundle)
    assert validate(bundle, result) == result
    assert result["attempt_order"] == ["region-0001"] and result["dispatch_order"] == []


@pytest.mark.parametrize("kind", ["missing", "extra", "reordered"])
def test_attempt_snapshot_universe_is_exact(kind):
    bundle = fixture(saved=_report(native=("a", "b")))
    if kind == "missing":
        bundle.observations["attempts"].pop()
    elif kind == "extra":
        bundle.observations["attempts"].append(copy.deepcopy(bundle.observations["attempts"][0]))
    else:
        bundle.observations["attempts"].reverse()
    with pytest.raises(ValueError):
        build(bundle)


def test_preflight_capture_totals_reject_before_any_geometry_work():
    bundle = fixture(saved=_report(native=("x",) * 11, max_pages=20))
    for snapshot in bundle.observations["attempts"]:
        snapshot["work"]["codepoints_charged"] = 100000
    def forbidden(**_kwargs):
        pytest.fail("geometry must not run for over-budget capture")
    bundle.derivation = policy.DispositionDerivation(forbidden, forbidden)
    with pytest.raises(ValueError):
        build(bundle)


def test_discarded_geometry_work_is_not_reset_after_join_fault():
    bundle = fixture()
    bundle.observations["attempts"][0]["raw_output"]["lines"][0]["text_sha256"] = "0"*64
    result = build(bundle)
    assert result["items"][0]["work"]["vertices_charged"] == 4
    assert result["items"][0]["diagnostic"]["detections"] == []
    assert validate(bundle, result) == result


def test_fallback_preserves_retained_fields_without_sharing_mutable_state():
    item = build(fixture())["items"][0]
    before = copy.deepcopy(item)
    fallback = policy._fallback(item, "budget_exhausted")
    assert item == before
    assert fallback["candidate"] == {**before["candidate"], "join": "unavailable"}
    assert fallback["diagnostic"]["reason"] == "budget_exhausted"
    assert fallback["diagnostic"]["detections"] == []
    for key in item.keys() - {"candidate", "diagnostic"}:
        assert fallback[key] == before[key]
    fallback["candidate"]["line_count"] = 0
    fallback["dispatch"]["ordinal"] = 0
    fallback["work"]["vertices_charged"] += 1
    fallback["diagnostic"]["detections"].append({})
    assert item == before


def test_full_five_thousand_page_fallback_envelope_fits_before_outcomes():
    bundle = fixture(saved=_report(native=(HEALTHY,)*5000))
    result = build(bundle)
    assert validate(bundle, result) == result
    assert result["summary"]["covered_items"] == 5000
    assert policy._preflight(result) < policy.reserve_envelope("pages")


@pytest.mark.parametrize("bad", [object(), (1, 2), {1: "bad"}, "\ud800", 10**1000, float("inf")])
def test_plain_data_preflight_has_no_object_hooks_or_unicode_coercion(bad):
    bundle = fixture()
    bundle.observations["attempts"][0]["settings"] = bad
    with pytest.raises(ValueError):
        build(bundle)


def test_cycle_and_deep_nested_values_fail_statically():
    cycle = []
    cycle.append(cycle)
    deep = []
    for _ in range(34):
        deep = [deep]
    for value in (cycle, deep):
        bundle = fixture()
        bundle.observations["extra"] = value
        with pytest.raises(ValueError):
            build(bundle)


def test_physical_vertex_limit_retains_lineage_with_proven_work_refusal():
    bundle = fixture()
    def limited(**_kwargs):
        return policy._physical("mapping_limit")
    bundle.derivation = policy.DispositionDerivation(geometry.replay_engine_box, limited)
    result = build(bundle)
    assert result["items"][0]["candidate"]["join"] == "verified_ordered"
    assert result["items"][0]["work"]["exhausted"] == {"limit": "polygon_vertices", "used_before": 0, "requested_units": 129}
    assert validate(bundle, result) == result
    result["items"][0]["work"]["exhausted"] = None
    with pytest.raises(ValueError):
        validate(bundle, result)


def test_serialized_reservation_rejects_invented_order_certificate():
    bundle = fixture()
    result = build(bundle)
    item = result["items"][0]
    item["diagnostic"] = policy._unavailable({}, "budget_exhausted")
    item["candidate"]["join"] = "unavailable"
    item["work"]["exhausted"] = {"limit": "serialized_bytes", "used_before": 16777216, "requested_units": 1}
    result["summary"] = policy._summary(result["items"], result["attempt_order"], result["dispatch_order"])
    with pytest.raises(ValueError):
        validate(bundle, result)


@pytest.mark.parametrize("name,value", [("engine_text_score", .01), ("reader_min_score", .01), ("classification_threshold", .8)])
def test_nonmatching_recipe_disables_builder_but_cannot_validate_complete(name, value):
    bundle = fixture()
    result = build(bundle)
    result["items"][0]["diagnostic"]["settings"][name] = value
    with pytest.raises(ValueError):
        validate(bundle, result)
    bundle.observations["attempts"][0]["settings"][name] = value
    result = build(bundle)
    assert result["items"][0]["diagnostic"]["reason"] == "unsupported_recipe"
    assert result["items"][0]["candidate"]["state"] == "accepted"
    assert validate(bundle, result) == result


def test_low_reader_score_collapsed_box_invalidates_entire_candidate_join():
    bundle = fixture()
    candidate = bundle.report["pages"][0]["candidate"]
    candidate["engine"]["min_score"] = .5
    line = candidate["lines"][0]
    # Direct arithmetic seam: keep an otherwise valid first line but make an
    # excluded second output collapse to zero area after original-raster clip.
    complete(bundle.observations["attempts"][0], candidate, [line, {**line, "score": .1}])
    snapshot = bundle.observations["attempts"][0]
    snapshot["detections"][1]["engine_input_box"] = [[0., 0.]]*4
    snapshot["raw_output"]["lines"][1]["box"] = [[0., 0.]]*4
    def replay(*, box, **_kwargs):
        return copy.deepcopy(box)
    item = policy._context(bundle.request, bundle.report, bundle.execution, bundle.bindings)[2][0]
    diag = {"state": "complete", "reason": None, **{key: snapshot[key] for key in
            ("settings", "raster", "roles", "stages", "detection_count", "detections")}, "mapping": None}
    snapshot["detections"][1]["detector_box"] = [[0., 0.]]*4
    with pytest.raises(policy._LineageFailure):
        policy._reconcile(diag, item, bundle.report["pages"][0], policy.DispositionDerivation(replay, geometry.map_source_polygon),
                          raw_output=snapshot["raw_output"])


def test_derivation_operand_mutation_and_invalid_result_never_publish_geometry():
    bundle = fixture()
    def mutate_input(*, engine_box, mapping):
        mapping["raster"]["width"] = 1
        return geometry.map_source_polygon(engine_box=engine_box, mapping={"kind": "page", "raster": {"width": 100, "height": 100}})
    bundle.derivation = policy.DispositionDerivation(geometry.replay_engine_box, mutate_input)
    result = build(bundle)
    assert result["items"][0]["diagnostic"]["detections"][0]["physical"]["reason"] == "mapping_failed"
    assert bundle.report["pages"][0]["candidate"]["raster"]["width"] == 100


def test_affine_positive_triangle_is_allowed_not_forced_four_vertices():
    bundle = fixture()
    def triangle(**_kwargs):
        return {"state": "available", "reason": None, "polygon": [[0., 0.], [.5, 0.], [0., .5]],
                "coordinate_system": "original_page_display_fraction", "edge_model": "linear", "max_error_source_pixels": 0.}
    bundle.derivation = policy.DispositionDerivation(geometry.replay_engine_box, triangle)
    result = build(bundle)
    assert validate(bundle, result) == result
    assert result["items"][0]["work"]["vertices_charged"] == 3


@pytest.mark.parametrize("malformed", [None, {"state": "none", "dtype": "float32", "shape": None},
                                     {"state": "array", "dtype": "float32", "shape": [0]},
                                     {"state": "array", "dtype": "float32", "shape": [0, 4, 2]}])
def test_empty_detector_requires_observed_none_not_default_or_array(malformed):
    bundle = fixture(saved=_report(candidates={1: ""}))
    bundle.observations["attempts"][0]["stages"]["detector"]["box_output"] = malformed
    with pytest.raises(ValueError):
        build(bundle)


def test_complete_all_blank_requires_successful_filter_and_float64_empty_shape():
    bundle = fixture(saved=_report(candidates={1: ""}))
    snapshot = bundle.observations["attempts"][0]
    complete(snapshot, bundle.report["pages"][0]["candidate"],
             [{"text": " ", "score": .4, "box": [[0., 0.], [80., 0.], [80., 20.], [0., 20.]]}])
    snapshot["stages"]["score_filter"]["box_output"] = boxes(0)
    with pytest.raises(ValueError):
        build(bundle)


@pytest.mark.parametrize("path", [("roles", "classification"), ("roles", "recognition"), ("stages", "crops"), ("stages", "score_filter")])
def test_missing_successful_stage_cannot_be_inferred_from_final_output(path):
    bundle = fixture()
    snapshot = bundle.observations["attempts"][0]
    snapshot[path[0]][path[1]] = role("not_run", 0) if path[0] == "roles" else stage()
    with pytest.raises(ValueError):
        build(bundle)


def test_reservation_exact_byte_boundary_and_maximal_fallback_does_not_borrow_slots(monkeypatch):
    """Stub only the optional ledger byte size to exercise cap-1 arithmetic.

    Actual maximal fallback/top UTF8 sizes are checked by the real encoder;
    huge fake strings are not admitted as if they were valid schema fields.
    """
    bundle = fixture(saved=_report(native=("a", "b")))
    original = policy._preflight
    reserve = policy.reserve_envelope("pages", item_count=2)
    remaining = policy.LIMITS["max_serialized_bytes"] - reserve
    def measured(value, maximum=16777216, **kwargs):
        actual = original(value, maximum, **kwargs)
        if type(value) is dict and set(value) == policy._ITEM_KEYS and value["diagnostic"]["state"] == "complete":
            return policy._ITEM_SLOT + (remaining - 1 if value["page_number"] == 1 else 2)
        return actual
    monkeypatch.setattr(policy, "_preflight", measured)
    result = build(bundle)
    assert result["items"][0]["diagnostic"]["state"] == "complete"
    fallback = result["items"][1]
    assert fallback["candidate"]["state"] == "accepted" and fallback["candidate"]["join"] == "unavailable"
    assert fallback["diagnostic"]["reason"] == "budget_exhausted"
    assert fallback["work"]["exhausted"] == {"limit": "serialized_bytes", "used_before": 16777215, "requested_units": 2}
    assert original(fallback) <= policy._ITEM_SLOT
    assert validate(bundle, result) == result
    assert len(result["items"]) == 2


@pytest.mark.parametrize("discard_first", [False, True])
def test_per_call_and_global_geometry_capacity_counts_discarded_work(discard_first):
    bundle = fixture(saved=_report(native=("a",)*7, max_pages=20))
    for row, snapshot in zip(bundle.report["pages"], bundle.observations["attempts"]):
        candidate = row["candidate"]
        candidate["lines"] *= 200
        candidate["text"] = "\n".join(line["text"] for line in candidate["lines"])
        complete(snapshot, candidate)
    vertices = [[.5+.4*math.cos(i*2*math.pi/128), .5+.4*math.sin(i*2*math.pi/128)] for i in range(128)]
    calls = []
    def maximum_polygon(**_kwargs):
        calls.append(1)
        return {"state": "available", "reason": None, "polygon": copy.deepcopy(vertices),
                "coordinate_system": "original_page_display_fraction", "edge_model": "linear", "max_error_source_pixels": 0.}
    bundle.derivation = policy.DispositionDerivation(geometry.replay_engine_box, maximum_polygon)
    if discard_first:
        bundle.observations["attempts"][0]["raw_output"]["lines"][0]["text_sha256"] = "0"*64
    result = build(bundle)
    assert len(calls) == 781  # <=100k vertices, never allocate a forbidden polygon.
    assert [item["work"]["vertices_charged"] for item in result["items"]] == [19968]*5+[128, 0]
    assert result["items"][5]["work"]["exhausted"] == {"limit": "vertices_total", "used_before": 99968, "requested_units": 128}
    if discard_first:
        assert result["items"][0]["diagnostic"]["reason"] == "lineage_invalid"
    calls.clear()
    assert validate(bundle, result) == result
    assert len(calls) == (625 if discard_first else 781)


def test_raw_output_must_be_null_on_no_call_or_failed_dispatch():
    bundle = fixture(saved=_report(candidates={1: ValueError("private")}))
    bundle.observations["attempts"][0]["raw_output"] = {"boxes": NONE_BOXES, "lines": []}
    with pytest.raises(ValueError):
        build(bundle)


@pytest.mark.parametrize("method", ["builder", "validator"])
def test_caller_data_is_not_mutated_even_when_rejected(method):
    bundle = fixture()
    result = build(bundle)
    target = bundle.observations if method == "builder" else result
    target["private_unknown"] = "PRIVATE"
    before = copy.deepcopy(target)
    with pytest.raises(ValueError) as caught:
        build(bundle) if method == "builder" else validate(bundle, result)
    assert target == before
    assert "PRIVATE" not in str(caught.value)


def test_unavailable_raster_work_cannot_be_reset_or_forge_budget_arithmetic():
    bundle = fixture()
    snapshot = bundle.observations["attempts"][0]
    snapshot.update(diagnostic_state="unavailable", diagnostic_reason="budget_exhausted", roles=None, stages=None,
                    detection_count=None, detections=[], raw_output=None)
    snapshot["work"]["exhausted"] = {"limit": "codepoints_per_text", "used_before": 0, "requested_units": 4097}
    result = build(bundle)
    assert validate(bundle, result) == result
    result["items"][0]["work"]["raster_bytes_hashed"] = 0
    with pytest.raises(ValueError):
        validate(bundle, result)


def test_removed_empty_unicode_and_nul_fingerprints_do_not_leak_raw_text():
    bundle = fixture()
    candidate = bundle.report["pages"][0]["candidate"]
    text = "名字\x00e\u0301"
    candidate["text"] = candidate["lines"][0]["text"] = text
    complete(bundle.observations["attempts"][0], candidate)
    result = build(bundle)
    assert validate(bundle, result) == result
    rec = result["items"][0]["diagnostic"]["detections"][0]["recognition"]
    assert rec["codepoints"] == len(text) and text not in json.dumps(result, ensure_ascii=False)


@pytest.mark.parametrize("method,component", [("classifier", "classification"), ("recognizer", "recognition")])
@pytest.mark.parametrize("component_state", ["not_run", "completed", "failed", "unobserved"])
def test_broader_method_failure_does_not_fabricate_actual_component_failure(method, component, component_state):
    bundle = fixture()
    snapshot = partial_recognition_failure(bundle)
    snapshot["roles"][component] = role(component_state, 0 if component_state == "not_run" else 1)
    snapshot["stages"][method] = stage("failed", 1)
    for record in snapshot["detections"]:
        if method == "classifier":
            record["classification"] = dict(state="unobserved", **dict.fromkeys(("label", "score", "rotated_180")))
            record["recognition"] = dict(state="not_run", **dict.fromkeys(("text_kind", "text_sha256", "codepoints", "score")))
    if method == "classifier":
        snapshot["roles"]["recognition"] = role("not_run", 0)
        snapshot["stages"]["recognizer"] = stage()
    result = build(bundle)
    assert validate(bundle, result) == result
    diagnostic = result["items"][0]["diagnostic"]
    assert diagnostic["stages"][method]["state"] == "failed"
    assert diagnostic["roles"][component]["state"] == component_state
    assert diagnostic["detections"][0][component]["state"] == "unobserved"
    assert diagnostic["detections"][0]["terminal"] == "unresolved"


@pytest.mark.parametrize("method,component", [("classifier", "classification"), ("recognizer", "recognition"), ("detector", "detection")])
@pytest.mark.parametrize("component_state", ["completed", "failed"])
def test_not_run_method_cannot_hide_known_actual_component_attempt(method, component, component_state):
    bundle = fixture()
    snapshot = partial_recognition_failure(bundle)
    snapshot["roles"][component] = role(component_state)
    snapshot["stages"][method] = stage()
    with pytest.raises(ValueError):
        build(bundle)


@pytest.mark.parametrize("component_state", ["not_run", "completed", "unobserved"])
def test_detector_failed_stage_still_requires_actual_failed_detector_role(component_state):
    bundle = fixture()
    snapshot = partial_recognition_failure(bundle)
    snapshot.update(detection_count=None, detections=[])
    snapshot["roles"] = {"detection": role(component_state, 0 if component_state == "not_run" else 1),
                         "classification": role("not_run", 0), "recognition": role("not_run", 0)}
    snapshot["stages"] = {name: stage() for name in policy._STAGES}
    snapshot["stages"]["detector"] = stage("failed")
    with pytest.raises(ValueError):
        build(bundle)


def test_detector_guard_pre_entry_refusal_keeps_method_and_role_not_run():
    bundle = fixture()
    snapshot = partial_recognition_failure(bundle)
    snapshot.update(detection_count=None, detections=[])
    snapshot["roles"] = {name: role("not_run", 0) for name in policy._ROLES}
    snapshot["stages"] = {name: stage() for name in policy._STAGES}
    result = build(bundle)
    assert validate(bundle, result) == result
    assert result["summary"]["known_detections"] == 0
    assert result["summary"]["detections_unknown_items"] == 1


@pytest.mark.parametrize("error", [RuntimeError, MemoryError])
def test_persistent_mapper_failure_can_build_and_read_back_without_whole_bundle_failure(error):
    bundle = fixture()
    calls = []
    def fail(**_kwargs):
        calls.append(1)
        raise error("PRIVATE")
    bundle.derivation = policy.DispositionDerivation(geometry.replay_engine_box, fail)
    result = build(bundle)
    assert len(calls) == 1 and result["items"][0]["work"]["vertices_charged"] == 128
    assert validate(bundle, result) == result
    assert len(calls) == 1  # Negative physical result makes no positive claim.
    result["items"][0]["diagnostic"]["detections"][0]["engine_input_box"][0][0] = 1.
    with pytest.raises(ValueError):
        validate(bundle, result)


@pytest.mark.parametrize("error", [KeyboardInterrupt, SystemExit])
def test_mapper_cancellation_is_not_ordinary_geometry_failure(error):
    bundle = fixture()
    positive = build(bundle)
    def cancel(**_kwargs):
        raise error()
    bundle.derivation = policy.DispositionDerivation(geometry.replay_engine_box, cancel)
    with pytest.raises(error):
        build(bundle)
    with pytest.raises(error):
        validate(bundle, positive)


def test_failed_geometry_does_not_mask_changed_source_mapping():
    bundle = fixture()
    def fail(**_kwargs):
        raise RuntimeError()
    bundle.derivation = policy.DispositionDerivation(geometry.replay_engine_box, fail)
    result = build(bundle)
    result["items"][0]["diagnostic"]["mapping"]["raster"]["width"] = 99
    with pytest.raises(ValueError):
        validate(bundle, result)


def test_thousand_failed_mappers_stop_at_reserved_capacity_and_preserve_spent_work():
    bundle = fixture()
    candidate = bundle.report["pages"][0]["candidate"]
    candidate["lines"] *= 1000
    candidate["text"] = "\n".join(line["text"] for line in candidate["lines"])
    complete(bundle.observations["attempts"][0], candidate)
    calls = []
    def fail(**_kwargs):
        calls.append(1)
        raise MemoryError()
    bundle.derivation = policy.DispositionDerivation(geometry.replay_engine_box, fail)
    result = build(bundle)
    assert len(calls) == 156
    item = result["items"][0]
    assert item["work"]["vertices_charged"] == 19968
    assert item["work"]["exhausted"] == {"limit": "vertices_per_call", "used_before": 19968, "requested_units": 128}
    assert item["candidate"]["join"] == "verified_ordered"
    assert validate(bundle, result) == result and len(calls) == 156


def test_actual_max_detection_cohort_high_precision_input_is_distinct_from_output_budget():
    bundle = fixture(saved=_report(native=("x",)*20+(HEALTHY,)*4980, max_pages=20))
    quad = [[1.2345678901234567e-300, 2.2345678901234567e-300],
            [80.12345678901235, 3.2345678901234567e-300],
            [80.12345678901235, 20.123456789012348], [1.2345678901234567e-300, 20.123456789012348]]
    for row, snapshot in zip(bundle.report["pages"], bundle.observations["attempts"]):
        candidate = row["candidate"]
        line = {"text": "x"*50, "score": .9234567890123456, "box": quad}
        candidate.update(lines=[line]*1000, text="\n".join([line["text"]]*1000), mean_confidence=line["score"])
        complete(snapshot, candidate, dtype="float64")
    measured = policy._preflight(bundle.observations, policy.MAX_OBSERVATION_BYTES, maximum_nodes=policy.MAX_OBSERVATION_NODES)
    assert 16777216 < measured <= policy.observation_representation_bound() <= policy.MAX_OBSERVATION_BYTES
    calls = []
    def unavailable(**_kwargs):
        calls.append(1)
        return policy._physical("no_positive_source_support")
    bundle.derivation = policy.DispositionDerivation(geometry.replay_engine_box, unavailable)
    result = build(bundle)
    assert len(result["items"]) == 5000 and result["summary"]["accepted_candidates"] == 20
    assert result["summary"]["diagnostics_unavailable"] > 0  # Actual byte fallback, no size stub.
    assert policy._preflight(result) <= 16777216
    assert len(calls) <= 781
    assert validate(bundle, result) == result


def failed_default_formatter(bundle, known_detector):
    snapshot = partial_recognition_failure(bundle)
    snapshot["stages"] = {name: stage() for name in policy._STAGES}
    snapshot["roles"] = {name: role("not_run", 0) for name in policy._ROLES}
    snapshot["work"]["codepoints_charged"] = 0
    if known_detector:
        snapshot["roles"]["detection"] = role()
        snapshot["stages"]["detector"] = stage("completed", output=1, after=boxes(1))
        snapshot["stages"]["crops"] = stage("failed", 1)
        for detection in snapshot["detections"]:
            detection.update(crop_state="unobserved")
            detection["classification"] = dict(state="not_run", **dict.fromkeys(("label", "score", "rotated_180")))
            detection["recognition"] = dict(state="not_run", **dict.fromkeys(("text_kind", "text_sha256", "codepoints", "score")))
    else:
        snapshot["roles"]["detection"] = role("failed")
        snapshot["stages"]["detector"] = stage("failed")
        snapshot.update(detection_count=None, detections=[])
        snapshot["raster"]["detector_box_dtype"] = None
        snapshot["work"]["detections_reserved"] = 0
    snapshot["stages"]["formatter"] = stage("failed", 0, before=NONE_BOXES)
    return snapshot


@pytest.mark.parametrize("known_detector", [False, True])
def test_partial_failed_formatter_preserves_actual_default_operand_independently_of_prior_detection(known_detector):
    bundle = fixture()
    failed_default_formatter(bundle, known_detector)
    result = build(bundle)
    assert validate(bundle, result) == result
    diagnostic = result["items"][0]["diagnostic"]
    assert diagnostic["state"] == "partial" and diagnostic["detection_count"] == (1 if known_detector else None)
    assert diagnostic["stages"]["formatter"] == stage("failed", 0, before=NONE_BOXES)
    assert result["summary"]["retained"] == 0 and result["summary"]["joined_candidate_lines"] == 0


@pytest.mark.parametrize("known_detector", [False, True])
@pytest.mark.parametrize("field,changed", [("input_count", None), ("input_count", True), ("input_count", 1),
                                          ("box_input", None), ("box_input", boxes(0)),
                                          ("box_input", {"state": "array", "dtype": "float64", "shape": [0]}),
                                          ("output_count", 0), ("box_output", NONE_BOXES), ("state", "completed")])
def test_failed_default_formatter_does_not_admit_unknown_or_fake_empty_operands(known_detector, field, changed):
    bundle = fixture()
    snapshot = failed_default_formatter(bundle, known_detector)
    snapshot["stages"]["formatter"][field] = copy.deepcopy(changed)
    with pytest.raises(ValueError):
        build(bundle)


@pytest.mark.parametrize("completed_prefix", ["crops", "classifier", "recognizer"])
def test_later_method_failure_cannot_claim_the_detector_operand_was_discarded(completed_prefix):
    bundle = fixture()
    if completed_prefix == "recognizer":
        snapshot = bundle.observations["attempts"][0]
        reject_candidate(bundle)
        bundle.execution["calls"][0]["status"] = snapshot["dispatch"]["raw_status"] = "failed"
        snapshot.update(diagnostic_state="partial", diagnostic_reason="ocr_path_incomplete", raw_output=None)
        snapshot["stages"]["score_filter"] = stage()
    else:
        snapshot = partial_recognition_failure(bundle)
        if completed_prefix == "crops":
            snapshot["roles"]["classification"] = role("failed")
            snapshot["stages"]["classifier"] = stage("failed", 1)
            snapshot["roles"]["recognition"] = role("not_run", 0)
            snapshot["stages"]["recognizer"] = stage()
            record = snapshot["detections"][0]
            record["classification"] = dict(state="unobserved", **dict.fromkeys(("label", "score", "rotated_180")))
            record["recognition"]["state"] = "not_run"
    snapshot["stages"]["formatter"] = stage("failed", 1, before=boxes(1))
    original = build(bundle)
    assert validate(bundle, original) == original  # This history retains det_res.
    snapshot["stages"]["formatter"] = stage("failed", 0, before=NONE_BOXES)
    with pytest.raises(ValueError):
        build(bundle)
    original["items"][0]["diagnostic"]["stages"]["formatter"] = stage("failed", 0, before=NONE_BOXES)
    with pytest.raises(ValueError):
        validate(bundle, original)


def test_failed_formatter_after_genuinely_empty_detector_keeps_known_zero():
    bundle = fixture(saved=_report(candidates={1: ""}))
    snapshot = bundle.observations["attempts"][0]
    reject_candidate(bundle)
    bundle.execution["calls"][0]["status"] = snapshot["dispatch"]["raw_status"] = "failed"
    snapshot.update(diagnostic_state="partial", diagnostic_reason="ocr_path_incomplete", raw_output=None)
    snapshot["stages"]["formatter"] = stage("failed", 0, before=NONE_BOXES)
    result = build(bundle)
    assert validate(bundle, result) == result
    assert result["items"][0]["diagnostic"]["detection_count"] == 0
    assert result["summary"]["detections_unknown_items"] == 0
