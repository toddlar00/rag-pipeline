"""Generated historical declarations; no model, PDF or installed-runtime proof."""

import builtins
import copy
import hashlib
import importlib.metadata
import json
from pathlib import Path

import pytest

import model_artifacts
import ocr_detection_disposition as disposition
import ocr_disposition_archive as archive
import ocr_execution_receipt
import ocr_experiment_runtime
import ocr_hardscan
import ocr_hardscan_io
import ocr_regions
from test_ocr_detection_disposition import attach_call, complete, fixture
from test_ocr_recovery_comparison import _report
from test_ocr_regions import candidate, geometry


def _raw(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":")) + "\n").encode()


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _model_declarations():
    hub = {"model_id": "generated/model", "source_revision": "main", "hub_license": "apache-2.0",
           "spdx_license": "Apache-2.0", "aliases": [], "consumers": ["embedding"],
           "runtime_files": {"embedding": ["config.json"]}, "trust_remote_code": False}
    files = [{"path": role + ".onnx", "role": role} for role in ("classification", "detection", "recognition")]
    package = {"package": "rapidocr", "version": "3.9.2", "wheel_filename": "rapidocr-3.9.2-py3-none-any.whl",
               "spdx_license": "Apache-2.0", "consumers": ["docling_ocr"], "files": files}
    policy = {"schema_version": 2, "models": [hub], "package_models": [package]}
    locked_files = [{**f, "size": 100 + i, "content_sha256": str(i) * 64} for i, f in enumerate(files, 1)]
    lock = {"schema_version": 2, "source": "https://huggingface.co",
            "models": [{**hub, "revision": "1" * 40, "files": [{"path": "config.json", "size": 2,
                        "git_blob_sha1": "2" * 40, "content_sha256": "3" * 64}]}],
            "package_models": [{**package, "files": locked_files,
                "wheel_url": "https://files.pythonhosted.org/generated.whl", "wheel_size": 400, "wheel_sha256": "4" * 64}]}
    models = [{"id": f["role"], "sha256": f["content_sha256"], "bytes": f["size"]} for f in locked_files]
    return policy, lock, models


def archive_fixture(operation="regions", *, dpi=300, text="synthetic region", state="candidate",
                    region_id="r00", bbox=None, installation=False):
    """Return validator kwargs for one entirely generated, valid crop bundle.

    Shared by pack tests: stable source/recovery/page geometry permits genuine
    300/400-DPI pairs; state is candidate, empty or unavailable (no raw call).
    All sessions/runtime/producer identities are expressly inert declarations.
    """
    assert operation in ("regions", "hardscan") and state in ("candidate", "empty", "unavailable")
    saved = _report()
    source = saved["source_sha256"]
    recovery_raw = _raw(saved)
    requested = [.1, .2, .8, .9] if bbox is None else bbox
    row = {"region_id": region_id, "page_number": 1, "bbox": requested}
    plan = {"schema_version": 1, "source_sha256": source, "recovery_sha256": _sha(recovery_raw),
            "coordinate_system": "original_page_display_fraction", "regions": [row]}
    recipe = {"orientation_clockwise": 0, "illumination": "none", "bow_fraction": 0.0, "bow_assumption": "none"}
    if operation == "hardscan":
        plan.update(kind="ocr_hardscan_plan", approval="operator_approved")
        row["recipe"] = recipe

    class Reader:
        page_count = 1

        def describe_region(self, _page, bbox):
            return geometry(bbox, dpi)

        def retry_region(self, page, bbox):
            if state == "unavailable":
                raise ValueError("generated preflight refusal")
            geo = self.describe_region(page, bbox)
            item = candidate(geo["raster"], "" if state == "empty" else text)
            item["engine"]["version"] = "3.9.2"
            return {"geometry": geo, "candidate": item}

        def describe_hardscan(self, page, bbox, recipe):
            geo = self.describe_region(page, bbox)
            return {"geometry": geo, "transform": ocr_hardscan.describe_transform(
                geo["raster"]["width"], geo["raster"]["height"], recipe)}

        def retry_hardscan(self, page, bbox, recipe):
            result = self.describe_hardscan(page, bbox, recipe)
            item = self.retry_region(page, bbox)["candidate"]
            item["raster"]["coordinate_system"] = "hardscan_image_pixels"
            return {**result, "candidate": item, "processing": {
                "status": "completed", "reason": None, "libraries": {"opencv": "synthetic", "numpy": "synthetic"},
                "illumination": {"status": "disabled", "reason": "mode_disabled", "background_low": None,
                                 "background_high": None, "ink_fraction": None}}}

    report = (ocr_regions._build if operation == "regions" else ocr_hardscan_io._build)(Reader(), saved, plan, dpi=dpi)
    report["inputs"] = {"pdf_sha256": source, "recovery_sha256": _sha(recovery_raw), "plan_sha256": _sha(_raw(plan))}
    policy, model_lock, models = _model_declarations()
    versions = {"numpy": "2.5.2", "onnxruntime": "1.24.3", "opencv-python": "5.0.0.93", "pymupdf": "1.27.2.2", "rapidocr": "3.9.2"}
    lock_raw = "".join(f"{name}=={version} --hash=sha256:{'4' * 64}\n" for name, version in versions.items()).encode()
    inputs = {"recovery.json": recovery_raw, "plan.json": _raw(plan),
              "requirements-full.lock": lock_raw, "requirements-test.lock": b"pytest==9.0.2 --hash=sha256:" + b"5" * 64 + b"\n",
              "requirements-lock-tools.lock": b"uv==0.10.7 --hash=sha256:" + b"6" * 64 + b"\n",
              "model-artifact-policy.json": _raw(policy), "model-artifacts.lock.json": _raw(model_lock)}
    sources = {name: "a" * 64 for name in ("ocr_detection_disposition_io.py", "ocr_execution_receipt.py", "tools/diagnose_ocr_dispositions.py")}
    generation = {"schema_version": 1, "kind": "ocr_disposition_producer", "upstream": dict(archive._UPSTREAM),
                  "identity": {"environment_sha256": "b" * 64, "python_sha256": "c" * 64, "base_python_sha256": "d" * 64,
                               "runtime_sha256": "e" * 64, "producer_sources": sources, "model_artifacts": models}}
    if installation:
        inputs["installation.json"] = _raw({"schema_version": 1, "kind": "ocr_locked_environment_installation",
            "recipe": "repository-full-cpu-hash-sync-v1", "installer_source_sha256": "f" * 64,
            "locks": {name: _sha(inputs[name]) for name in archive._LOCK_NAMES},
            "steps": {name: {"status": "complete", "exit_code": 0, "elapsed_seconds": 1., "log_sha256": "1" * 64, "log_bytes": 10}
                      for name in ("create_environment", "bootstrap", "verify_installer", "sync", "check", "inventory")},
            "status": "complete", "installed_under_hash_locks": True, "scope": archive._INSTALL_SCOPE,
            "environment_identity_sha256": "b" * 64, "python_executable_sha256": "c" * 64})
    config = {"operation": operation, "policy": {"dpi": dpi}, "requested_pages": []}
    request = {"schema_version": 1, "kind": "ocr_disposition_request", "operation": operation,
               "configuration": config, "recipe_id": disposition.RECIPE_ID, "limits": dict(disposition.LIMITS),
               "producer_generation_sha256": disposition.canonical_sha256(generation),
               "inputs": {archive._INPUT_KEYS[name]: _sha(raw) for name, raw in inputs.items()}}
    request["inputs"].update(source_sha256=source, configuration_sha256=disposition.canonical_sha256(config))
    report_raw = _raw(report)
    bundle = fixture(operation)
    bundle.request, bundle.report = request, report
    bundle.bindings.update(request_sha256=disposition.canonical_sha256(request), report_sha256=_sha(report_raw),
                           producer_generation_sha256=disposition.canonical_sha256(generation), source_sha256=source)
    bundle.observations["request_sha256"] = bundle.bindings["request_sha256"]
    snapshot = bundle.observations["attempts"][0]
    bundle.execution["calls"] = []
    if state == "unavailable":
        snapshot.update(dispatch=None, diagnostic_state="not_run", diagnostic_reason="no_raw_dispatch", settings=None,
                        raster=None, roles=None, stages=None, detection_count=None, detections=[], raw_output=None,
                        work=disposition.zero_work())
    else:
        attach_call(bundle, snapshot)
        complete(snapshot, report["regions"][0]["candidate"])
    observed = None if state == "unavailable" else {
        "engine": {"name": "rapidocr", "version": "3.9.2"}, "model_artifacts": models,
        "sessions": [{"role": m["id"], "execution_providers": ["CPUExecutionProvider"],
                      "thread_settings": {"intra_op": 2, "inter_op": 1}, "model_sha256": m["sha256"]} for m in models],
        "verification_scope": ocr_execution_receipt._SCOPE}
    effective = None if observed is None else {"engine": observed["engine"], "execution_providers": ["CPUExecutionProvider"],
        "thread_settings": {"intra_op": 2, "inter_op": 1}, "elapsed_seconds": .03,
        "model_artifacts": [{"id": m["id"], "sha256": m["sha256"]} for m in models]}
    runtime = {"schema_version": 1, "kind": "ocr_experiment_runtime", "lock_sha256": _sha(lock_raw),
        "environment": {"python_full_version": "3.12.9", "python_version": "3.12", "os_name": "nt", "sys_platform": "win32",
                        "platform_machine": "AMD64", "platform_python_implementation": "CPython"},
        "packages": [{"name": name, "required_version": versions[name], "installed_version": versions[name], "status": "version_match",
                      "loaded_versions": [versions[name].rsplit(".", 1)[0] if name == "opencv-python" else versions[name]],
                      "loaded_version_agrees": True} for name in sorted(versions)],
        "version_check_scope": "requested_packages_only", "package_versions_match": True,
        "effective_runtime": effective, "effective_runtime_source": "caller_observation" if effective else None,
        "locked_environment_verified": False,
        "artifact_verification": {"package_wheels": "not_verified", "model_files": "not_verified_by_capture", "loaded_native_libraries": "not_verified"},
        "requires_attention": True}
    calls = bundle.execution["calls"]
    execution = {"schema_version": 1, "kind": "ocr_execution_receipt", "operation": operation, "inputs": request["inputs"],
        "output_sha256": _sha(report_raw), "environment_identity_sha256": "b" * 64,
        "installation_sha256": _sha(inputs["installation.json"]) if installation else None,
        "installation_evidence": "verified_local_bindings" if installation else "not_supplied",
        "project_sources_at_observation": {"ocr_detection_disposition_io": "a" * 64, "entrypoint": "a" * 64},
        "runtime": runtime, "engine_observation": observed, "calls": calls,
        "summary": {"attempted": len(calls), "completed": len(calls), "failed": 0}, "elapsed_seconds": .03,
        "ocr_executed": bool(calls), "requires_attention": not calls or not installation, "scope": archive._RECEIPT_SCOPE}
    bundle.execution = execution
    receipt_raw = _raw(execution)
    bundle.bindings["execution_sha256"] = _sha(receipt_raw)
    diagnostic = disposition.build_disposition(bundle.observations, request=request, report=report, execution=execution,
                                               bindings=bundle.bindings, derivation=bundle.derivation)
    diagnostic_raw = _raw(diagnostic)
    manifest = {"schema_version": 1, "kind": "ocr_disposition_bundle", "request": request, "producer_generation": generation,
                "report_sha256": _sha(report_raw), "execution_sha256": _sha(receipt_raw), "disposition_sha256": _sha(diagnostic_raw),
                "requires_attention": True, "canonical_extraction_modified": False, "accuracy_verified": False, "scope": archive._SCOPE}
    return {"artifacts": {"manifest.json": _raw(manifest), "report.json": report_raw, "work/report.json": report_raw,
                          "execution.json": receipt_raw, "disposition.json": diagnostic_raw},
            "retained_inputs": inputs, "source_sha256": source, "page_count": 1}


@pytest.fixture
def case():
    return archive_fixture()


def _alter(case, slot, path, value, *, retained=False, rehash=True):
    target = case["retained_inputs" if retained else "artifacts"]
    data = json.loads(target[slot])
    owner = data
    for key in path[:-1]:
        owner = owner[key]
    owner[path[-1]] = value
    target[slot] = _raw(data)
    if not retained and rehash and slot != "manifest.json":
        manifest = json.loads(case["artifacts"]["manifest.json"])
        manifest[{"report.json": "report_sha256", "execution.json": "execution_sha256", "disposition.json": "disposition_sha256"}[slot]] = _sha(target[slot])
        case["artifacts"]["manifest.json"] = _raw(manifest)
        if slot == "report.json":
            case["artifacts"]["work/report.json"] = target[slot]


def rebind_declarations(case):
    """Recompute envelope hashes/tickets without repairing submitted semantics.

    This prevents a stale downstream digest from masking a missing historical
    consistency check in negative tests. It is not a production trust helper.
    """
    artifacts, inputs = case["artifacts"], case["retained_inputs"]
    manifest = json.loads(artifacts["manifest.json"])
    request = manifest["request"]
    request["inputs"] = {archive._INPUT_KEYS[name]: _sha(raw) for name, raw in inputs.items()}
    request["inputs"].update(source_sha256=case["source_sha256"], configuration_sha256=disposition.canonical_sha256(request["configuration"]))
    request["producer_generation_sha256"] = disposition.canonical_sha256(manifest["producer_generation"])
    execution = json.loads(artifacts["execution.json"])
    execution["inputs"] = copy.deepcopy(request["inputs"])
    execution["output_sha256"] = _sha(artifacts["report.json"])
    execution["installation_sha256"] = request["inputs"].get("installation_sha256")
    artifacts["execution.json"] = _raw(execution)
    diagnostic = json.loads(artifacts["disposition.json"])
    bindings = {"source_sha256": case["source_sha256"], "request_sha256": disposition.canonical_sha256(request),
                "report_sha256": _sha(artifacts["report.json"]), "execution_sha256": _sha(artifacts["execution.json"]),
                "producer_generation_sha256": request["producer_generation_sha256"]}
    diagnostic["bindings"] = bindings
    for item in diagnostic["items"]:
        dispatch = item["dispatch"]
        if dispatch is not None:
            dispatch["ticket_sha256"] = disposition.canonical_sha256({"request_sha256": bindings["request_sha256"],
                "item_id": item["item_id"], "attempt_index": item["attempt_index"], "call_id": dispatch["call_id"],
                "dispatch_ordinal": dispatch["ordinal"]})
    artifacts["disposition.json"] = _raw(diagnostic)
    manifest.update(report_sha256=bindings["report_sha256"], execution_sha256=bindings["execution_sha256"],
                    disposition_sha256=_sha(artifacts["disposition.json"]))
    artifacts["manifest.json"] = _raw(manifest)


@pytest.mark.parametrize("operation", ["regions", "hardscan"])
@pytest.mark.parametrize("dpi", [300, 400])
@pytest.mark.parametrize("state", ["candidate", "empty", "unavailable"])
@pytest.mark.parametrize("installation", [False, True])
def test_generated_valid_archive_all_crop_states(operation, dpi, state, installation):
    case = archive_fixture(operation, dpi=dpi, state=state, installation=installation)
    result = archive.validate_disposition_archive(**case)
    assert set(result) == {"manifest", "report", "execution", "disposition", "artifact_sha256", "retained_input_sha256", "request_sha256", "validation_scope"}
    assert result["validation_scope"] == "historical_local_declarations"
    assert result["artifact_sha256"] == {k: _sha(v) for k, v in case["artifacts"].items()}
    assert result["execution"]["summary"]["attempted"] == (0 if state == "unavailable" else 1)
    assert result["report"]["retry_configuration"]["dpi"] == dpi
    assert result["disposition"]["items"][0]["region_id"] == "r00"


def test_validator_has_no_runtime_model_or_filesystem_dependency(case, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("historical validator attempted live work")
    monkeypatch.setattr(importlib.metadata, "version", forbidden)
    monkeypatch.setattr(importlib.metadata, "distributions", forbidden)
    monkeypatch.setattr(model_artifacts, "load_model_artifact_registry", forbidden)
    monkeypatch.setattr(model_artifacts, "verified_installed_package_model", forbidden)
    monkeypatch.setattr(ocr_execution_receipt, "capture_execution_receipt", forbidden)
    monkeypatch.setattr(ocr_execution_receipt, "validate_installation_evidence", forbidden)
    monkeypatch.setattr(ocr_experiment_runtime, "capture_runtime_manifest", forbidden)
    monkeypatch.setattr(Path, "open", forbidden)
    monkeypatch.setattr(builtins, "open", forbidden)
    assert archive.validate_disposition_archive(**case)["execution"]["runtime"]["environment"]["python_full_version"] == "3.12.9"


@pytest.mark.parametrize("kind", [KeyboardInterrupt, SystemExit])
def test_cancellation_is_not_reclassified(case, monkeypatch, kind):
    signal = kind("cancel")
    def cancel(*_args, **_kwargs):
        raise signal
    monkeypatch.setattr(archive, "validate_recovery_report", cancel)
    with pytest.raises(kind) as error:
        archive.validate_disposition_archive(**case)
    assert error.value is signal


@pytest.mark.parametrize("collection", ["artifacts", "retained_inputs"])
@pytest.mark.parametrize("fault", ["dict_subclass", "string_subclass", "none", "huge_slot_count"])
def test_byte_map_preflight_rejects_nonplain_or_excessive_containers(case, collection, fault):
    class D(dict):
        pass
    class S(str):
        pass
    if fault == "dict_subclass":
        case[collection] = D(case[collection])
    elif fault == "string_subclass":
        name = next(iter(case[collection]))
        case[collection][S(name)] = case[collection].pop(name)
    elif fault == "none":
        case[collection] = None
    else:
        case[collection] = {str(i): b"{}" for i in range(100)}
    with pytest.raises(ValueError):
        archive.validate_disposition_archive(**case)


@pytest.mark.parametrize("collection,slot", [
    *[("artifacts", name) for name in archive.ARTIFACT_LIMITS],
    *[("retained_inputs", name) for name in archive.INPUT_LIMITS],
])
def test_individual_byte_limits_precede_decoding(collection, slot, monkeypatch):
    case = archive_fixture(installation=True)
    attribute = "ARTIFACT_LIMITS" if collection == "artifacts" else "INPUT_LIMITS"
    monkeypatch.setattr(archive, attribute, {**getattr(archive, attribute), slot: 1})
    monkeypatch.setattr(archive, "_decode", lambda *_a: pytest.fail("individual byte limit checked too late"))
    with pytest.raises(ValueError):
        archive.validate_disposition_archive(**case)


def test_detached_results_cannot_modify_the_retained_archive(case):
    before = copy.deepcopy(case)
    result = archive.validate_disposition_archive(**case)
    result["manifest"]["request"]["inputs"].clear()
    result["report"]["regions"][0]["candidate"]["text"] = "modified"
    result["execution"]["calls"].clear()
    assert case == before
    assert archive.validate_disposition_archive(**case)["report"]["regions"][0]["candidate"]["text"] == "synthetic region"


@pytest.mark.parametrize("collection", ["artifacts", "retained_inputs"])
@pytest.mark.parametrize("fault", ["missing", "extra", "path", "bytearray", "empty"])
def test_exact_byte_slot_contract(case, collection, fault):
    value = case[collection]
    name = next(iter(value))
    if fault == "missing":
        value.pop(name)
    elif fault == "extra":
        value["unexpected.json"] = b"{}"
    elif fault == "path":
        value[name] = Path("not-read")
    elif fault == "bytearray":
        value[name] = bytearray(value[name])
    else:
        value[name] = b""
    with pytest.raises(ValueError, match="historical OCR crop archive"):
        archive.validate_disposition_archive(**case)


@pytest.mark.parametrize("slot", list(archive.ARTIFACT_LIMITS))
def test_each_raw_artifact_is_bound(case, slot):
    case["artifacts"][slot] += b" "
    if slot == "manifest.json":
        # Manifest whitespace itself is legitimate: its raw hash changes.
        result = archive.validate_disposition_archive(**case)
        assert result["artifact_sha256"][slot] == _sha(case["artifacts"][slot])
    else:
        with pytest.raises(ValueError):
            archive.validate_disposition_archive(**case)


@pytest.mark.parametrize("slot", [k for k in archive.INPUT_LIMITS if k != "installation.json"])
def test_each_raw_input_is_request_bound(case, slot):
    case["retained_inputs"][slot] += b" "
    with pytest.raises(ValueError):
        archive.validate_disposition_archive(**case)


@pytest.mark.parametrize("value", [True, 0, 5001, 1.0, "1", None])
def test_host_page_count_is_exact_and_bounded(case, value):
    case["page_count"] = value
    with pytest.raises(ValueError):
        archive.validate_disposition_archive(**case)


@pytest.mark.parametrize("value", ["b" * 64, "A" * 64, "a" * 63, None, True])
def test_host_source_binding(case, value):
    case["source_sha256"] = value
    with pytest.raises(ValueError):
        archive.validate_disposition_archive(**case)


@pytest.mark.parametrize("path,value", [
    (("schema_version",), True), (("kind",), "ocr_scan_bundle"), (("accuracy_verified",), True),
    (("request", "operation"), "pages"), (("request", "recipe_id"), "future-v2"),
    (("request", "configuration", "policy", "dpi"), 350), (("request", "configuration", "policy", "dpi"), 300.0),
    (("request", "configuration", "requested_pages"), [1]), (("request", "limits", "max_calls"), 21),
    (("request", "inputs", "source_sha256"), "b" * 64), (("request", "producer_generation_sha256"), "b" * 64),
    (("producer_generation", "schema_version"), True), (("producer_generation", "kind"), "future"),
    (("producer_generation", "upstream", "rapidocr/main.py"), "b" * 64),
    (("producer_generation", "identity", "model_artifacts", 0, "bytes"), True),
])
def test_closed_manifest_request_and_generation_contract(case, path, value):
    _alter(case, "manifest.json", path, value)
    with pytest.raises(ValueError):
        archive.validate_disposition_archive(**case)


@pytest.mark.parametrize("path,value", [
    (("schema_version",), True), (("operation",), "hardscan"), (("output_sha256",), "b" * 64),
    (("environment_identity_sha256",), "c" * 64), (("installation_evidence",), "verified_local_bindings"),
    (("summary", "attempted"), True), (("summary", "completed"), 0), (("ocr_executed",), False),
    (("calls", 0, "id"), "call-0002"), (("calls", 0, "elapsed_seconds"), 1.0),
    (("requires_attention",), False), (("project_sources_at_observation", "entrypoint"), "b" * 64),
    (("engine_observation", "engine", "version"), "3.8.1"),
    (("engine_observation", "sessions", 0, "model_sha256"), "b" * 64),
    (("engine_observation", "model_artifacts", 0, "bytes"), 999),
    (("runtime", "lock_sha256"), "b" * 64), (("runtime", "locked_environment_verified"), True),
    (("runtime", "environment", "python_version"), "3.13"),
    (("runtime", "packages", 0, "status"), "not_installed_or_unreadable"),
    (("runtime", "packages", 0, "required_version"), "99"),
    (("runtime", "packages", 0, "loaded_version_agrees"), False),
    (("runtime", "effective_runtime", "thread_settings", "intra_op"), 3),
])
def test_rebound_receipt_cannot_contradict_retained_declarations(case, path, value):
    _alter(case, "execution.json", path, value)
    with pytest.raises(ValueError):
        archive.validate_disposition_archive(**case)


@pytest.mark.parametrize("operation", ["regions", "hardscan"])
@pytest.mark.parametrize("fault", ["page_count", "dpi", "plan", "context", "candidate"])
def test_report_rebound_digest_cannot_break_original_context(operation, fault):
    case = archive_fixture(operation)
    if fault == "page_count":
        path, value = ("page_count",), 2
    elif fault == "dpi":
        path, value = ("retry_configuration", "dpi"), 400
    elif fault == "plan":
        path, value = ("plan", "regions", 0, "region_id"), "other"
    elif fault == "context":
        path = ("page_contexts", 0, "recovery_page", "original_text") if operation == "regions" else ("baseline_recovery", "pages", 0, "original_text")
        value = "altered saved page context"
    else:
        path, value = ("regions", 0, "candidate", "text"), "different"
    _alter(case, "report.json", path, value)
    with pytest.raises(ValueError):
        archive.validate_disposition_archive(**case)


@pytest.mark.parametrize("path,value", [
    (("bindings", "request_sha256"), "b" * 64), (("items", 0, "region_id"), "other"),
    (("items", 0, "dispatch", "call_id"), "call-0002"),
    (("items", 0, "diagnostic", "detections", 0, "candidate_index"), 2),
    (("items", 0, "diagnostic", "detections", 0, "physical", "polygon"), [[0., 0.], [1., 0.], [1., 1.]]),
])
def test_disposition_rebound_digest_cannot_forge_joins(case, path, value):
    _alter(case, "disposition.json", path, value)
    with pytest.raises(ValueError):
        archive.validate_disposition_archive(**case)


@pytest.mark.parametrize("raw", [b'{"schema_version":1,"schema_version":1}', b'{"x":NaN}', b'{"x":1e400}',
    b'{"x":"\\ud800"}', b'{"x":' + b'[' * 34 + b'0' + b']' * 34 + b'}', b'[]', b'\xff'])
def test_invalid_json_is_static(case, raw):
    case["artifacts"]["manifest.json"] = raw
    with pytest.raises(ValueError) as error:
        archive.validate_disposition_archive(**case)
    assert str(error.value) == "invalid or contradictory historical OCR crop archive"


def test_aggregate_raw_limit_before_decode(case, monkeypatch):
    monkeypatch.setattr(archive, "MAX_TOTAL_BYTES", 10)
    monkeypatch.setattr(archive, "_decode", lambda *args: pytest.fail("decoded before aggregate admission"))
    with pytest.raises(ValueError):
        archive.validate_disposition_archive(**case)


@pytest.mark.parametrize("key", [False, True])
def test_scalar_and_key_limits(case, monkeypatch, key):
    monkeypatch.setattr(archive, "MAX_KEY" if key else "MAX_STRING", 10)
    case["artifacts"]["manifest.json"] = _raw({"x" * 11 if key else "x": "\x00" * 11})
    with pytest.raises(ValueError):
        archive.validate_disposition_archive(**case)


def test_shared_decoded_node_budget(case, monkeypatch):
    monkeypatch.setattr(archive, "MAX_NODES", 100)
    with pytest.raises(ValueError):
        archive.validate_disposition_archive(**case)


@pytest.mark.parametrize("requested", [True, False])
def test_installation_slot_presence_exactly_matches_request(requested):
    case = archive_fixture(installation=requested)
    if requested:
        case["retained_inputs"].pop("installation.json")
    else:
        case["retained_inputs"]["installation.json"] = archive_fixture(installation=True)["retained_inputs"]["installation.json"]
    with pytest.raises(ValueError):
        archive.validate_disposition_archive(**case)


@pytest.mark.parametrize("operation", ["regions", "hardscan"])
def test_supported_source_scope_survives_different_dpi(operation):
    import ocr_crop_comparison
    first = archive.validate_disposition_archive(**archive_fixture(operation, dpi=300))
    second = archive.validate_disposition_archive(**archive_fixture(operation, dpi=400))
    assert ocr_crop_comparison.crop_scope(first["report"], region_id="r00") == ocr_crop_comparison.crop_scope(second["report"], region_id="r00")
    assert first["report"]["regions"][0]["geometry"]["raster"] != second["report"]["regions"][0]["geometry"]["raster"]


def test_fixture_rebinding_itself_is_valid(case):
    before = archive.validate_disposition_archive(**case)
    rebind_declarations(case)
    assert archive.validate_disposition_archive(**case) == before


def test_runtime_upgrade_and_changed_producer_inventory_are_historical(case, monkeypatch):
    manifest = json.loads(case["artifacts"]["manifest.json"])
    identity = manifest["producer_generation"]["identity"]
    identity.update(environment_sha256="1" * 64, python_sha256="2" * 64, base_python_sha256="3" * 64, runtime_sha256="4" * 64)
    identity["producer_sources"] = {name: "5" * 64 for name in identity["producer_sources"]}
    identity["producer_sources"]["historical_only_module.py"] = "6" * 64
    case["artifacts"]["manifest.json"] = _raw(manifest)
    receipt = json.loads(case["artifacts"]["execution.json"])
    receipt["environment_identity_sha256"] = "1" * 64
    receipt["project_sources_at_observation"] = {name: "5" * 64 for name in receipt["project_sources_at_observation"]}
    case["artifacts"]["execution.json"] = _raw(receipt)
    rebind_declarations(case)
    monkeypatch.setattr(ocr_experiment_runtime, "DEFAULT_OCR_PACKAGES", ("future-package",))
    monkeypatch.setattr(importlib.metadata, "version", lambda *_: (_ for _ in ()).throw(importlib.metadata.PackageNotFoundError()))
    result = archive.validate_disposition_archive(**case)
    assert result["execution"]["environment_identity_sha256"] == "1" * 64
    assert len(result["execution"]["runtime"]["packages"]) == 5


@pytest.mark.parametrize("slot,path,value", [
    ("model-artifact-policy.json", ("schema_version",), 2.0),
    ("model-artifacts.lock.json", ("schema_version",), 2.0),
    ("model-artifacts.lock.json", ("models", 0, "revision"), "main"),
    ("model-artifacts.lock.json", ("package_models", 0, "files", 0, "size"), True),
    ("model-artifacts.lock.json", ("package_models", 0, "files", 0, "content_sha256"), "b" * 64),
    ("model-artifacts.lock.json", ("package_models", 0, "files", 0, "role"), "unrecognized"),
    ("model-artifacts.lock.json", ("package_models", 0, "version"), "3.8.1"),
    ("model-artifact-policy.json", ("package_models", 0, "version"), "3.8.1"),
    ("model-artifacts.lock.json", ("package_models", 0, "wheel_sha256"), "b" * 64),
])
def test_model_policy_lock_and_runtime_cross_joins_after_full_hash_rebinding(case, slot, path, value, monkeypatch):
    _alter(case, slot, path, value, retained=True)
    rebind_declarations(case)
    monkeypatch.setattr(archive, "validate_disposition", lambda *_a, **_k: pytest.fail("invalid model declarations reached lineage"))
    with pytest.raises(ValueError):
        archive.validate_disposition_archive(**case)


@pytest.mark.parametrize("path,value", [
    (("steps", "sync", "exit_code"), True), (("steps", "inventory", "log_bytes"), -1),
    (("steps", "check", "status"), "failed"), (("environment_identity_sha256",), "c" * 64),
    (("python_executable_sha256",), "b" * 64), (("locks", "requirements-full.lock"), "b" * 64),
    (("installed_under_hash_locks",), 1), (("schema_version",), True),
])
def test_installation_declarations_rebound_but_contradictory(path, value, monkeypatch):
    case = archive_fixture(installation=True)
    _alter(case, "installation.json", path, value, retained=True)
    rebind_declarations(case)
    monkeypatch.setattr(archive, "validate_disposition", lambda *_a, **_k: pytest.fail("invalid installation reached lineage"))
    with pytest.raises(ValueError):
        archive.validate_disposition_archive(**case)


@pytest.mark.parametrize("path,value", [
    (("summary", "attempted"), True), (("calls", 0, "elapsed_seconds"), 1.),
    (("engine_observation", "sessions", 0, "model_sha256"), "b" * 64),
    (("runtime", "packages", 0, "status"), "not_installed_or_unreadable"),
    (("runtime", "packages", 0, "loaded_versions"), ["99"]),
    (("runtime", "effective_runtime", "elapsed_seconds"), .01),
    (("project_sources_at_observation", "entrypoint"), "f" * 64),
])
def test_complete_rebinding_does_not_hide_receipt_semantic_refusals(case, path, value, monkeypatch):
    _alter(case, "execution.json", path, value)
    rebind_declarations(case)
    monkeypatch.setattr(archive, "validate_disposition", lambda *_a, **_k: pytest.fail("invalid receipt reached lineage"))
    with pytest.raises(ValueError):
        archive.validate_disposition_archive(**case)


def test_approved_wheel_hash_on_different_package_does_not_bind_rapidocr(case):
    raw = case["retained_inputs"]["requirements-full.lock"]
    case["retained_inputs"]["requirements-full.lock"] = raw.replace(b"rapidocr==3.9.2 --hash=sha256:" + b"4" * 64,
                                                                 b"rapidocr==3.9.2 --hash=sha256:" + b"5" * 64)
    _alter(case, "execution.json", ("runtime", "lock_sha256"), _sha(case["retained_inputs"]["requirements-full.lock"]))
    rebind_declarations(case)
    with pytest.raises(ValueError):
        archive.validate_disposition_archive(**case)


def test_supported_hash_lock_continuation_and_environment_marker(case):
    raw = case["retained_inputs"]["requirements-full.lock"]
    raw = raw.replace(b"rapidocr==3.9.2 --hash=", b"rapidocr==3.9.2 ; python_version == '3.12' \\\n    --hash=")
    case["retained_inputs"]["requirements-full.lock"] = raw
    _alter(case, "execution.json", ("runtime", "lock_sha256"), _sha(raw))
    rebind_declarations(case)
    assert archive.validate_disposition_archive(**case)["execution"]["runtime"]["lock_sha256"] == _sha(raw)


@pytest.mark.parametrize("fault", ["physical", "logical", "lines", "unterminated", "unhashed", "unknown_marker"])
def test_hostile_lock_refused_before_lineage(case, monkeypatch, fault):
    if fault == "physical":
        monkeypatch.setattr(archive, "MAX_LOCK_LINE", 50)
    elif fault == "logical":
        monkeypatch.setattr(archive, "MAX_LOCK_RECORD", 50)
    elif fault == "lines":
        monkeypatch.setattr(archive, "MAX_LOCK_LINES", 2)
    elif fault == "unterminated":
        case["retained_inputs"]["requirements-full.lock"] = b"rapidocr==3.9.2 \\\n"
    elif fault == "unhashed":
        case["retained_inputs"]["requirements-full.lock"] = b"rapidocr==3.9.2\n"
    else:
        case["retained_inputs"]["requirements-full.lock"] = b"rapidocr==3.9.2 ; future_variable == 'x' --hash=sha256:" + b"4" * 64 + b"\n"
    rebind_declarations(case)
    monkeypatch.setattr(archive, "validate_disposition", lambda *_a, **_k: pytest.fail("invalid lock reached lineage"))
    with pytest.raises(ValueError):
        archive.validate_disposition_archive(**case)


def test_long_continuations_are_joined_once_before_shared_parser(case, monkeypatch):
    raw = b"rapidocr==3.9.2 \\\n" + (b" \\\n" * 1000) + b" --hash=sha256:" + b"4" * 64 + b"\n"
    normalized = archive._normalized_lock(raw)
    assert normalized.count(b"\n") == 1
    assert b"\\" not in normalized
    assert archive._lock_records(normalized) == {"rapidocr": [("3.9.2", "")]}


@pytest.mark.parametrize("raw", [b'{"x":"' + b"x" * 70 + b'"}', b'{"x":[' + b'0,' * 30 + b'0]}'])
def test_predecode_shape_budget_precedes_json_decoder(case, monkeypatch, raw):
    case["artifacts"]["manifest.json"] = raw
    monkeypatch.setattr(archive, "MAX_STRING", 10)
    monkeypatch.setattr(archive, "MAX_NODES", 10)
    monkeypatch.setattr(archive, "_strict_json_bytes", lambda *_a, **_k: pytest.fail("oversized JSON reached decoder"))
    with pytest.raises(ValueError):
        archive.validate_disposition_archive(**case)
