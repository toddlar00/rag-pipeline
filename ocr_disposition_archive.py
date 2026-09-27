"""Pure historical crop-bundle consistency, never live execution admission.

The caller supplies bounded retained bytes and independently verifies the PDF.
Producer, interpreter, installation logs and loaded native/model bytes are only
historical local declarations. No files, installed packages or models are read.
Archive validation restores neither a live run nor an operator approval.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from types import MappingProxyType

from evaluation_inputs import _strict_json_bytes
from model_artifacts import validate_model_artifact_lock, validate_package_model_artifact_lock
from ocr_checkpoint import validate_identity
from ocr_detection_disposition import DispositionDerivation, LIMITS, RECIPE_ID, validate_disposition
from ocr_disposition_geometry import map_source_polygon, replay_engine_box
from ocr_execution_receipt import validate_engine_observation
from ocr_experiment_runtime import _PIN, _lock_records, _marker_applies, _name
import ocr_hardscan
import ocr_hardscan_io
from ocr_recovery_comparison import validate_recovery_report
import ocr_regions


MAX_TOTAL_BYTES = 384 * 1024 * 1024
MAX_NODES = 4_000_000
MAX_DEPTH = 32
MAX_STRING = 100_000
MAX_KEY = 128
ARTIFACT_LIMITS = MappingProxyType({
    "manifest.json": 512 * 1024, "report.json": 128 * 1024 * 1024,
    "execution.json": 4 * 1024 * 1024, "disposition.json": 16 * 1024 * 1024,
    "work/report.json": 128 * 1024 * 1024,
})
INPUT_LIMITS = MappingProxyType({
    "recovery.json": 64 * 1024 * 1024, "plan.json": 1024 * 1024,
    "requirements-full.lock": 8 * 1024 * 1024,
    "requirements-test.lock": 8 * 1024 * 1024,
    "requirements-lock-tools.lock": 8 * 1024 * 1024,
    "model-artifact-policy.json": 8 * 1024 * 1024,
    "model-artifacts.lock.json": 8 * 1024 * 1024,
    "installation.json": 64 * 1024,
})
_INPUT_KEYS = {
    "recovery.json": "recovery_sha256", "plan.json": "plan_sha256",
    "requirements-full.lock": "dependency_full_sha256",
    "requirements-test.lock": "dependency_test_sha256",
    "requirements-lock-tools.lock": "dependency_tools_sha256",
    "model-artifact-policy.json": "model_policy_sha256",
    "model-artifacts.lock.json": "model_lock_sha256",
    "installation.json": "installation_sha256",
}
_LOCK_NAMES = ("requirements-full.lock", "requirements-test.lock", "requirements-lock-tools.lock")
_V1_PACKAGES = ("numpy", "onnxruntime", "opencv-python", "pymupdf", "rapidocr")
MAX_LOCK_LINE = 32 * 1024
MAX_LOCK_RECORD = 256 * 1024
MAX_LOCK_LINES = 100_000
_SHA = re.compile(r"[a-f0-9]{64}")
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9.!+_-]{0,127}")
_SAFE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_SCOPE = "Local same-call lineage, not authenticated execution, complete-source verification, accuracy or correction approval."
_RECEIPT_SCOPE = "Local execution and evidence bindings, not signed artifact attestation or accuracy certification. Source hashes describe disk snapshots at observation, not loaded-byte attestation. Constructed sessions do not prove every role executed."
_INSTALL_SCOPE = "Installer execution and dependency consistency; not installed wheel, build-environment, or native-library byte attestation."
_DERIVATION = DispositionDerivation(replay_engine_box, map_source_polygon)
# Frozen characterized recipe, not a lookup in the currently installed package.
_UPSTREAM = MappingProxyType({
    "rapidocr/main.py": "c2ae17098dde838ac3d2933eec5b218d5c4404ded9fe5d32df7c24fd0e54aa39",
    "rapidocr/ch_ppocr_det/main.py": "a56c0f51fd6a8c03a5abf2f0a5843b382b8f3257d1bfacf8d5ac6305d5e63024",
    "rapidocr/ch_ppocr_det/utils.py": "01d25a0b1bbdcdd4aba70a23ae96714c5408df93b295c43ca194952e279adb9e",
    "rapidocr/ch_ppocr_cls/main.py": "a48b3197d5588f035668f5adfc6d60006fcd851049a840ad03718c57cead2c45",
    "rapidocr/ch_ppocr_cls/utils.py": "bcadd799e971fe42a2935c2568ad9a0299e24b6e8c55f57abced42eaac324cd9",
    "rapidocr/ch_ppocr_rec/main.py": "84b7a55a8972d14a92800b66facc73976b8d0b06bd8888e551414dbec8d6d326",
    "rapidocr/ch_ppocr_rec/typings.py": "02b88178edab41b275d14bf8f61ce7148c13328f79f330b78f7c3cb77f7c4737",
    "rapidocr/utils/utils.py": "86f8687db6424714be5a6fbd283f6e3b7b198691fb292efd12622b5b40197582",
    "rapidocr/utils/output.py": "8467bbb4b3c140d1f1e8f214043baa7449a1d30b853af3da24fe26238a436314",
    "rapidocr/utils/process_img.py": "abaf2ed615878f618a372cba6157bc6c41a494e05f6c41bf9f7c5c2ba1ee5772",
    "rapidocr-3.9.2.dist-info/METADATA": "39bcad37352500bddff188407efff244c0ddbefb348d1a097b0731a6cbf476a8",
})


def _fail():
    raise ValueError("invalid or contradictory historical OCR crop archive")


def _fields(value, keys):
    if type(value) is not dict or len(value) != len(keys) or set(value) != set(keys):
        _fail()
    return value


def _sha(value):
    if type(value) is not str or not _SHA.fullmatch(value):
        _fail()
    return value


def _integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        _fail()
    return value


def _safe(value, pattern=_SAFE):
    if type(value) is not str or not pattern.fullmatch(value):
        _fail()
    return value


def _elapsed(value):
    if type(value) not in (int, float) or not 0 <= value <= 86400 or not math.isfinite(value):
        _fail()
    return float(value)


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")


def _digest(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _same(actual, expected):
    if _canonical(actual) != _canonical(expected):
        _fail()


def _byte_map(value, limits, *, optional=()):
    if (type(value) is not dict or not len(limits) - len(optional) <= len(value) <= len(limits)
            or any(type(name) is not str for name in value)
            or not set(limits) - set(optional) <= set(value) <= set(limits)):
        _fail()
    for name, raw in value.items():
        if type(raw) is not bytes or not 0 < len(raw) <= limits[name]:
            _fail()


def _hashes(value):
    return {name: hashlib.sha256(raw).hexdigest() for name, raw in value.items()}


def _decode(raw, limit, remaining):
    # Count raw structural tokens and cap individual encoded strings BEFORE
    # json.loads can allocate a large container/scalar. The byte ceiling alone
    # is not an object-count or nesting bound. Shared budget is charged below.
    depth = token_count = scalar_bytes = 0
    quoted = escaped = False
    for char in raw:
        if quoted:
            scalar_bytes += 1
            if scalar_bytes > MAX_STRING * 6 + 2:
                _fail()
            if escaped:
                escaped = False
            elif char == 92:
                escaped = True
            elif char == 34:
                quoted = False
            continue
        if char == 34:
            quoted, scalar_bytes = True, 0
            token_count += 1
        elif char in (91, 123):
            depth += 1
            token_count += 1
            if depth > MAX_DEPTH:
                _fail()
        elif char in (93, 125):
            depth -= 1
        elif char in (44, 58):
            token_count += 1
        if token_count > remaining[0] * 2:
            _fail()
    value = _strict_json_bytes(raw, label="historical crop archive", max_bytes=limit)

    def walk(item, level):
        remaining[0] -= 1
        if remaining[0] < 0 or level > MAX_DEPTH:
            _fail()
        if type(item) is dict:
            if len(item) > remaining[0]:
                _fail()
            for key, child in item.items():
                if type(key) is not str or len(key) > MAX_KEY:
                    _fail()
                key.encode("utf-8")
                remaining[0] -= 1
                walk(child, level + 1)
        elif type(item) is list:
            if len(item) > remaining[0]:
                _fail()
            for child in item:
                walk(child, level + 1)
        elif type(item) is str:
            if len(item) > MAX_STRING:
                _fail()
            item.encode("utf-8")
        elif type(item) is int and item.bit_length() > 64:
            _fail()
        elif item is not None and type(item) not in (bool, int, float):
            _fail()

    walk(value, 0)
    return value


def _normalized_lock(raw):
    """Bound and join continuations once before the shared legacy parser.

    Its repeated string concatenation is safe on these already joined records;
    the retained/raw hash continues to describe the ORIGINAL lock bytes.
    """
    text = raw.decode("utf-8-sig")
    if sum(text.count(char) for char in "\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029") > MAX_LOCK_LINES:
        _fail()
    lines, fragments, size, output = 0, [], 0, []
    for raw_line in text.splitlines():
        lines += 1
        if lines > MAX_LOCK_LINES or len(raw_line) > MAX_LOCK_LINE:
            _fail()
        line = raw_line.split("#", 1)[0].strip()
        if not line or (not fragments and line.startswith(("--index-url ", "--extra-index-url "))):
            continue
        continued = line.endswith("\\")
        fragment = line[:-1].rstrip() if continued else line
        size += len(fragment) + 1
        if size > MAX_LOCK_RECORD:
            _fail()
        fragments.append(fragment)
        if not continued:
            output.append(" ".join(fragments))
            if len(output) > 4096:
                _fail()
            fragments, size = [], 0
    if fragments or not output:
        _fail()
    return ("\n".join(output) + "\n").encode("utf-8")


def _runtime(value, *, lock_raw, lock_sha256, effective):
    keys = {"schema_version", "kind", "lock_sha256", "environment", "packages", "version_check_scope",
            "package_versions_match", "effective_runtime", "effective_runtime_source", "locked_environment_verified",
            "artifact_verification", "requires_attention"}
    _fields(value, keys)
    environment = _fields(value["environment"], {"python_full_version", "python_version", "os_name", "sys_platform",
                                                "platform_machine", "platform_python_implementation"})
    for entry in environment.values():
        _safe(entry)
    if (re.fullmatch(r"\d+\.\d+\.\d+", environment["python_full_version"]) is None
            or environment["python_version"] != ".".join(environment["python_full_version"].split(".")[:2])):
        _fail()
    records = _lock_records(lock_raw)
    packages = value["packages"]
    if type(packages) is not list or len(packages) != len(_V1_PACKAGES):
        _fail()
    expected_packages = []
    for name, package in zip(_V1_PACKAGES, packages):
        _fields(package, {"name", "required_version", "installed_version", "status", "loaded_versions", "loaded_version_agrees"})
        expected, supported = [], True
        for version, marker in records.get(name, []):
            try:
                if _marker_applies(marker, environment):
                    expected.append(version)
            except ValueError:
                supported = False
        installed = package["installed_version"]
        if installed is not None:
            _safe(installed, _VERSION)
        versions = package["loaded_versions"]
        if type(versions) is not list or len(versions) > 4:
            _fail()
        for version in versions:
            _safe(version, _VERSION)
        if versions != sorted(set(versions)):
            _fail()
        pin = expected[0] if len(expected) == 1 else None
        if not supported:
            status = "unsupported_marker"
        elif len(expected) > 1:
            status = "ambiguous_pin"
        elif not expected:
            status = "not_locked_for_environment"
        elif installed is None:
            status = "not_installed_or_unreadable"
        else:
            status = "version_match" if installed == pin else "version_mismatch"
        agrees = None if not versions or pin is None else all(
            version == pin or (name == "opencv-python" and len(pin.split(".")) == 4
                               and version == pin.rsplit(".", 1)[0]) for version in versions)
        expected_packages.append({"name": name, "required_version": pin, "installed_version": installed,
                                  "status": status, "loaded_versions": versions, "loaded_version_agrees": agrees})
    matches = all(p["status"] == "version_match" and p["loaded_version_agrees"] is not False for p in expected_packages)
    expected = {"schema_version": 1, "kind": "ocr_experiment_runtime", "lock_sha256": lock_sha256,
                "environment": environment, "packages": expected_packages, "version_check_scope": "requested_packages_only",
                "package_versions_match": matches, "effective_runtime": effective,
                "effective_runtime_source": "caller_observation" if effective else None,
                "locked_environment_verified": False,
                "artifact_verification": {"package_wheels": "not_verified", "model_files": "not_verified_by_capture",
                                          "loaded_native_libraries": "not_verified"}, "requires_attention": True}
    _same(value, expected)
    return expected


def _installation(value, identity, hashes):
    _fields(value, {"schema_version", "kind", "recipe", "installer_source_sha256", "locks", "steps", "status",
                    "installed_under_hash_locks", "scope", "environment_identity_sha256", "python_executable_sha256"})
    _same({key: value[key] for key in ("schema_version", "kind", "recipe", "status", "installed_under_hash_locks", "scope")},
          {"schema_version": 1, "kind": "ocr_locked_environment_installation", "recipe": "repository-full-cpu-hash-sync-v1",
           "status": "complete", "installed_under_hash_locks": True, "scope": _INSTALL_SCOPE})
    if (value["environment_identity_sha256"] != identity["environment_sha256"]
            or value["python_executable_sha256"] != identity["python_sha256"]):
        _fail()
    _sha(value["installer_source_sha256"])
    _same(value["locks"], {name: hashes[name] for name in _LOCK_NAMES})
    steps = _fields(value["steps"], {"create_environment", "bootstrap", "verify_installer", "sync", "check", "inventory"})
    for step in steps.values():
        _fields(step, {"status", "exit_code", "elapsed_seconds", "log_sha256", "log_bytes"})
        if step["status"] != "complete":
            _fail()
        _integer(step["exit_code"], 0, 0)
        _elapsed(step["elapsed_seconds"])
        _integer(step["log_bytes"], 0, 16 * 1024 * 1024)
        _sha(step["log_sha256"])


def _models(policy, lock):
    _integer(policy.get("schema_version"), 2, 2)
    _integer(lock.get("schema_version"), 2, 2)
    validate_model_artifact_lock(lock, policy_data=policy)
    packages = validate_package_model_artifact_lock(lock, policy_data=policy)
    matches = [p for p in packages if p.package == "rapidocr"]
    if len(matches) != 1 or matches[0].version != "3.9.2" or "docling_ocr" not in matches[0].consumers:
        _fail()
    package = matches[0]
    models = sorted([{"id": f.role, "sha256": f.content_sha256, "bytes": f.size} for f in package.files], key=lambda m: m["id"])
    if [m["id"] for m in models] != ["classification", "detection", "recognition"]:
        _fail()
    for model in models:
        _integer(model["bytes"], 1, 64 * 1024 * 1024)
    return models, package.wheel_sha256


def _wheel_lock(raw, *, environment, wheel_sha256):
    """Join the declared package-model wheel to its exact applicable lock row.

    The complete hash-lock grammar has already been validated by _lock_records;
    this bounded second pass retains hashes that its version projection omits.
    It never fetches or verifies a wheel.
    """
    matched = False
    for raw_line in raw.decode("utf-8-sig").splitlines():
        requirement, *hashes = raw_line.strip().split(" --hash=")
        pin = _PIN.fullmatch(requirement.strip())
        if pin and _name(pin[1]) == "rapidocr" and pin[2] == "3.9.2" and _marker_applies((pin[3] or "").strip(), environment):
            matched |= "sha256:" + wheel_sha256 in [entry.strip() for entry in hashes]
    if not matched:
        _fail()


def _execution(value, *, request, generation, report_sha, hashes, inputs, models, wheel_sha256, installation):
    _fields(value, {"schema_version", "kind", "operation", "inputs", "output_sha256", "environment_identity_sha256",
                    "installation_sha256", "installation_evidence", "project_sources_at_observation", "runtime",
                    "engine_observation", "calls", "summary", "elapsed_seconds", "ocr_executed", "requires_attention", "scope"})
    elapsed = _elapsed(value["elapsed_seconds"])
    calls = value["calls"]
    if type(calls) is not list or len(calls) > LIMITS["max_calls"]:
        _fail()
    checked = []
    for i, call in enumerate(calls, 1):
        _fields(call, {"id", "status", "elapsed_seconds"})
        if call["id"] != f"call-{i:04d}" or call["status"] not in ("completed", "failed"):
            _fail()
        checked.append({"id": call["id"], "status": call["status"], "elapsed_seconds": _elapsed(call["elapsed_seconds"])})
    if sum(c["elapsed_seconds"] for c in checked) > elapsed + 1e-6:
        _fail()
    completed = sum(c["status"] == "completed" for c in checked)
    observed = validate_engine_observation(value["engine_observation"]) if value["engine_observation"] is not None else None
    if completed and observed is None:
        _fail()
    effective = None
    if observed is not None:
        _same(observed["model_artifacts"], models)
        _same(observed["engine"], {"name": "rapidocr", "version": "3.9.2"})
        sessions = observed["sessions"]
        uniform = all(s["thread_settings"] == sessions[0]["thread_settings"] for s in sessions)
        effective = {"engine": observed["engine"], "execution_providers": sorted({p for s in sessions for p in s["execution_providers"]}),
                     "thread_settings": sessions[0]["thread_settings"] if uniform else {"intra_op": None, "inter_op": None},
                     "elapsed_seconds": elapsed, "model_artifacts": [{"id": m["id"], "sha256": m["sha256"]} for m in models]}
    runtime = _runtime(value["runtime"], lock_raw=inputs["requirements-full.lock"],
                       lock_sha256=hashes["requirements-full.lock"], effective=effective)
    rapidocr = next(p for p in runtime["packages"] if p["name"] == "rapidocr")
    if rapidocr["required_version"] != "3.9.2":
        _fail()
    _wheel_lock(inputs["requirements-full.lock"], environment=runtime["environment"], wheel_sha256=wheel_sha256)
    sources = value["project_sources_at_observation"]
    if type(sources) is not dict or not 1 <= len(sources) <= 256:
        _fail()
    for name, digest in sources.items():
        _safe(name)
        _sha(digest)
        path = "tools/diagnose_ocr_dispositions.py" if name == "entrypoint" else name.replace(".", "/") + ".py"
        if generation["identity"]["producer_sources"].get(path) != digest:
            _fail()
    expected = {"schema_version": 1, "kind": "ocr_execution_receipt", "operation": request["operation"],
                "inputs": request["inputs"], "output_sha256": report_sha,
                "environment_identity_sha256": generation["identity"]["environment_sha256"],
                "installation_sha256": hashes.get("installation.json"),
                "installation_evidence": "verified_local_bindings" if installation is not None else "not_supplied",
                "project_sources_at_observation": sources, "runtime": runtime, "engine_observation": observed,
                "calls": checked, "summary": {"attempted": len(checked), "completed": completed, "failed": len(checked) - completed},
                "elapsed_seconds": elapsed, "ocr_executed": bool(checked),
                "requires_attention": not checked or completed != len(checked) or installation is None or not runtime["package_versions_match"],
                "scope": _RECEIPT_SCOPE}
    _same(value, expected)
    return expected


def _report(value, recovery, plan, *, request, hashes, source_sha256, page_count):
    if recovery["source_sha256"] != source_sha256 or recovery["page_count"] != page_count:
        _fail()
    if request["operation"] == "regions":
        checked_plan = ocr_regions.validate_region_plan(plan, source_sha256=source_sha256,
            recovery_sha256=hashes["recovery.json"], page_count=page_count)
        report = ocr_regions.validate_region_review(value)
        _same(report["page_contexts"], ocr_regions._context(recovery, {r["page_number"] for r in checked_plan["regions"]}))
    else:
        checked_plan = ocr_hardscan.validate_plan(plan, source_sha256=source_sha256,
            recovery_sha256=hashes["recovery.json"], page_count=page_count)
        report = ocr_hardscan_io.validate_report(value)
        _same(report["baseline_recovery"], recovery)
        _same(report["retry_configuration"], ocr_hardscan_io._configuration(request["configuration"]["policy"]["dpi"]))
    _same(report["plan"], checked_plan)
    _same(report["inputs"], {"pdf_sha256": source_sha256, "recovery_sha256": hashes["recovery.json"], "plan_sha256": hashes["plan.json"]})
    if (report["source_sha256"] != source_sha256 or report["page_count"] != page_count
            or report["retry_configuration"]["dpi"] != request["configuration"]["policy"]["dpi"]):
        _fail()
    for row in report["regions"]:
        if row["candidate"] is not None and row["candidate"]["engine"]["version"] != "3.9.2":
            _fail()
    return report


def validate_disposition_archive(*, artifacts: dict[str, bytes], retained_inputs: dict[str, bytes],
                                 source_sha256: str, page_count: int) -> dict:
    """Validate exactly one retained regions/hardscan bundle, without any IO.

    Installation log/source hashes and full-environment identity hashes remain
    opaque declarations; their external files are intentionally not consulted.
    Returned objects are detached from caller bytes. Raw hashes preserve report
    occurrences even when semantically equal JSON has different formatting.
    """
    try:
        return _validate(artifacts, retained_inputs, source_sha256, page_count)
    except (ValueError, TypeError, KeyError, IndexError, OverflowError, RecursionError, UnicodeError):
        _fail()


def _validate(artifacts, retained_inputs, source_sha256, page_count):
    _sha(source_sha256)
    _integer(page_count, 1, 5000)
    _byte_map(artifacts, ARTIFACT_LIMITS)
    _byte_map(retained_inputs, INPUT_LIMITS, optional=("installation.json",))
    if sum(map(len, artifacts.values())) + sum(map(len, retained_inputs.values())) > MAX_TOTAL_BYTES:
        _fail()
    artifact_hashes, input_hashes = _hashes(artifacts), _hashes(retained_inputs)
    if artifacts["report.json"] != artifacts["work/report.json"]:
        _fail()
    budget = [MAX_NODES]
    parsed = {name: _decode(raw, ARTIFACT_LIMITS[name], budget) for name, raw in artifacts.items() if name != "work/report.json"}
    inputs = {name: _decode(raw, INPUT_LIMITS[name], budget) for name, raw in retained_inputs.items() if name.endswith(".json")}
    normalized_locks = {name: _normalized_lock(retained_inputs[name]) for name in _LOCK_NAMES}
    for raw in normalized_locks.values():
        _lock_records(raw)
    manifest = _fields(parsed["manifest.json"], {"schema_version", "kind", "request", "producer_generation", "report_sha256",
        "execution_sha256", "disposition_sha256", "requires_attention", "canonical_extraction_modified", "accuracy_verified", "scope"})
    _same({k: manifest[k] for k in ("schema_version", "kind", "requires_attention", "canonical_extraction_modified", "accuracy_verified", "scope")},
          {"schema_version": 1, "kind": "ocr_disposition_bundle", "requires_attention": True,
           "canonical_extraction_modified": False, "accuracy_verified": False, "scope": _SCOPE})
    for key, name in (("report_sha256", "report.json"), ("execution_sha256", "execution.json"), ("disposition_sha256", "disposition.json")):
        if manifest[key] != artifact_hashes[name]:
            _fail()
    generation = _fields(manifest["producer_generation"], {"schema_version", "kind", "identity", "upstream"})
    _integer(generation["schema_version"], 1, 1)
    if generation["kind"] != "ocr_disposition_producer":
        _fail()
    _same(generation["upstream"], dict(_UPSTREAM))
    identity = validate_identity(generation["identity"])
    models, wheel_sha256 = _models(inputs["model-artifact-policy.json"], inputs["model-artifacts.lock.json"])
    _same(identity["model_artifacts"], models)
    request = _fields(manifest["request"], {"schema_version", "kind", "operation", "configuration", "inputs", "recipe_id", "limits", "producer_generation_sha256"})
    _integer(request["schema_version"], 1, 1)
    if request["kind"] != "ocr_disposition_request" or request["recipe_id"] != RECIPE_ID or request["operation"] not in ("regions", "hardscan"):
        _fail()
    _same(request["limits"], dict(LIMITS))
    configuration = _fields(request["configuration"], {"operation", "policy", "requested_pages"})
    policy = _fields(configuration["policy"], {"dpi"})
    _integer(policy["dpi"], 300, 400)
    if policy["dpi"] not in (300, 400) or configuration["operation"] != request["operation"]:
        _fail()
    _same(configuration["requested_pages"], [])
    expected_inputs = {_INPUT_KEYS[name]: digest for name, digest in input_hashes.items()}
    expected_inputs.update(source_sha256=source_sha256, configuration_sha256=_digest(configuration))
    _same(request["inputs"], expected_inputs)
    if request["producer_generation_sha256"] != _digest(generation):
        _fail()
    installation = inputs.get("installation.json")
    if installation is not None:
        _installation(installation, identity, input_hashes)
    recovery = validate_recovery_report(inputs["recovery.json"])
    report = _report(parsed["report.json"], recovery, inputs["plan.json"], request=request, hashes=input_hashes,
                     source_sha256=source_sha256, page_count=page_count)
    receipt = _execution(parsed["execution.json"], request=request, generation=generation,
        report_sha=artifact_hashes["report.json"], hashes=input_hashes, inputs=normalized_locks,
        models=models, wheel_sha256=wheel_sha256, installation=installation)
    bindings = {"request_sha256": _digest(request), "report_sha256": artifact_hashes["report.json"],
                "execution_sha256": artifact_hashes["execution.json"], "producer_generation_sha256": _digest(generation), "source_sha256": source_sha256}
    disposition = validate_disposition(parsed["disposition.json"], request=request, report=report, execution=receipt,
                                      bindings=bindings, derivation=_DERIVATION)
    return {"manifest": manifest, "report": report, "execution": receipt, "disposition": disposition,
            "artifact_sha256": artifact_hashes, "retained_input_sha256": input_hashes,
            "request_sha256": bindings["request_sha256"], "validation_scope": "historical_local_declarations"}
