"""Model-free, fixed-input scan bundles with strict private completion readback.

Native discovery is imported only by the executing worker. Hashes and runtime
versions establish local consistency, not signed or loaded-native-byte proof.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import time

from evaluation_inputs import _read_snapshot, _strict_json_bytes
from ocr_execution_receipt import environment_identity, validate_installation_evidence
from ocr_experiment_runtime import capture_runtime_manifest
from ocr_recovery import _publish_new_report
from resource_lease import PathLease
import storage_policy


ROOT = Path(__file__).resolve().parent
MAX_SOURCE_BYTES = 256 * 1024 * 1024
MAX_RECOVERY_BYTES = 64 * 1024 * 1024
MAX_PROPOSALS_BYTES = 32 * 1024 * 1024
_LIMITS = {"scan.json": 32 * 1024 * 1024, "diagnostics.json": 32 * 1024 * 1024,
           "manifest.json": 512 * 1024}
_PACKAGES = ("pymupdf", "opencv-python", "numpy")
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9.!+_-]{0,127}")
_LOCKS = ("requirements-full.lock", "requirements-test.lock", "requirements-lock-tools.lock")
_PRODUCERS = (
    "artifact_io.py", "evaluation_inputs.py", "ocr_comparison.py", "ocr_docling.py",
    "ocr_evaluation.py", "ocr_execution_receipt.py", "ocr_experiment_runtime.py", "ocr_layout.py",
    "ocr_preprocessing.py", "ocr_recovery.py", "ocr_recovery_comparison.py", "ocr_scan_io.py",
    "ocr_scan_omission.py", "ocr_scan_runtime.py", "process_supervision.py", "resource_lease.py", "retention.py",
    "storage_policy.py", "supervised_worker.py", "tools/inspect_ocr_scan.py",
)
# Historical schema-v1 declaration scope is deliberately independent of future
# execution-source additions. Changing it requires an explicit contract review.
_HISTORICAL_PRODUCERS_V1 = frozenset({
    "artifact_io.py", "evaluation_inputs.py", "ocr_comparison.py", "ocr_docling.py",
    "ocr_evaluation.py", "ocr_execution_receipt.py", "ocr_experiment_runtime.py", "ocr_layout.py",
    "ocr_preprocessing.py", "ocr_recovery.py", "ocr_recovery_comparison.py", "ocr_scan_io.py",
    "ocr_scan_omission.py", "ocr_scan_runtime.py", "process_supervision.py", "resource_lease.py", "retention.py",
    "storage_policy.py", "supervised_worker.py", "tools/inspect_ocr_scan.py",
})
_SCOPE = ("Local source-pixel observations and optional saved-geometry correspondence; not signed evidence, "
          "loaded-native-byte attestation, text recognition, or source completeness certification.")


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _snapshot(path: Path, limit: int) -> tuple[bytes, str, tuple[int, int]]:
    storage_policy.assert_no_link_components(path)
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or not 1 <= before.st_size <= limit:
        raise ValueError("scan input must be a bounded single-linked regular file")
    raw, digest = _read_snapshot(path, label="scan artifact", max_bytes=limit)
    storage_policy.assert_no_link_components(path)
    after = path.lstat()
    identity = (before.st_dev, before.st_ino)
    if (not stat.S_ISREG(after.st_mode) or after.st_nlink != 1
            or identity != (after.st_dev, after.st_ino)):
        raise RuntimeError("scan input identity changed")
    return raw, digest, identity


def _json(path: Path, limit: int) -> tuple[dict, str, tuple[int, int]]:
    raw, digest, identity = _snapshot(path, limit)
    return _strict_json_bytes(raw, label="scan artifact", max_bytes=limit), digest, identity


def _directory(path: Path) -> tuple[int, int]:
    storage_policy.assert_no_link_components(path)
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError("scan bundle parent must already be a directory")
    return info.st_dev, info.st_ino


def _pages(value: object) -> tuple[int, ...]:
    if (type(value) is not list or not 1 <= len(value) <= 8
            or any(type(n) is not int or not 1 <= n <= 5000 for n in value)
            or len(set(value)) != len(value)):
        raise ValueError("scan requires one to eight distinct one-based pages")
    return tuple(sorted(value))


def _runtime_metadata() -> dict:
    return capture_runtime_manifest(ROOT / _LOCKS[0], packages=_PACKAGES, require_version_match=True)


def _package_baseline(runtime: dict) -> dict:
    # Import state differs legitimately between parent and native worker.
    result = json.loads(_canonical(runtime))
    for package in result["packages"]:
        package["loaded_versions"] = []
        package["loaded_version_agrees"] = None
    return result


def _generation() -> dict:
    runtime = _package_baseline(_runtime_metadata())
    executables = {name: _snapshot(Path(path).absolute(), 64 * 1024 * 1024)[1]
                   for name, path in (("python", sys.executable),
                                      ("base_python", getattr(sys, "_base_executable", sys.executable)))}
    return {"environment_identity_sha256": environment_identity(Path(sys.prefix)),
            "executables": executables, "runtime": runtime,
            "producer_sources": {name: _snapshot(ROOT / name, 4 * 1024 * 1024)[1] for name in _PRODUCERS}}


@dataclass(frozen=True)
class _Input:
    path: Path
    key: str
    limit: int
    sha256: str
    identity: tuple[int, int]


@dataclass(frozen=True)
class ScanRequest:
    pdf_path: Path
    output_dir: Path
    recovery_path: Path | None
    proposals_path: Path | None
    installation_path: Path | None
    requested_pages: tuple[int, ...]
    parent_identity: tuple[int, int]
    inputs: tuple[_Input, ...]
    request_json: str
    recovery_json: str | None
    proposals_json: str | None
    recipe: str = "legacy-v1"


def scan_request(pdf_path: Path, output_dir: Path, *, requested_pages: list[int],
                 recovery_path: Path | None = None, proposals_path: Path | None = None,
                 installation_path: Path | None = None, readback: bool = False,
                 recipe: str = "legacy-v1") -> ScanRequest:
    """Read-only admission; neither native libraries nor OCR models are loaded."""
    from ocr_scan_omission import configuration_for_recipe

    configuration = configuration_for_recipe(recipe)
    from ocr_docling import validate_docling_proposals
    from ocr_recovery_comparison import validate_recovery_report

    pages = _pages(requested_pages)
    if type(readback) is not bool or (proposals_path is not None and recovery_path is None):
        raise ValueError("scan proposals require a bound recovery")
    pdf, output = Path(pdf_path).absolute(), Path(output_dir).absolute()
    recovery_file, proposals_file, installation = (
        None if path is None else Path(path).absolute()
        for path in (recovery_path, proposals_path, installation_path))
    storage_policy.assert_no_link_components(output)
    parent = _directory(output.parent)
    if (readback and not output.is_dir()) or (not readback and output.exists()):
        raise ValueError("choose a new scan output directory")
    specs = [(pdf, "source_sha256", MAX_SOURCE_BYTES)]
    for path, key, limit in ((recovery_file, "recovery_sha256", MAX_RECOVERY_BYTES),
                             (proposals_file, "proposals_sha256", MAX_PROPOSALS_BYTES),
                             (installation, "installation_sha256", 64 * 1024)):
        if path is not None:
            specs.append((path, key, limit))
    specs.extend((ROOT / name, name, 8 * 1024 * 1024) for name in _LOCKS)
    captured, raw_inputs = [], {}
    for path, key, limit in specs:
        if path == output or any(os.path.samefile(path, previous.path) for previous in captured):
            raise ValueError("scan inputs and output must be distinct")
        raw, digest, identity = _snapshot(path, limit)
        captured.append(_Input(path, key, limit, digest, identity))
        if key in {"recovery_sha256", "proposals_sha256"}:
            raw_inputs[key] = _strict_json_bytes(raw, label="scan comparison input", max_bytes=limit)
    hashes = {item.key: item.sha256 for item in captured}
    recovery = proposals = None
    if recovery_file is not None:
        recovery = validate_recovery_report(raw_inputs["recovery_sha256"])
        if recovery["source_sha256"] != hashes["source_sha256"] or max(pages) > recovery["page_count"]:
            raise ValueError("scan recovery differs from requested source or pages")
    if proposals_file is not None:
        proposals = validate_docling_proposals(raw_inputs["proposals_sha256"], recovery=recovery,
                                               recovery_sha256=hashes["recovery_sha256"])
    if installation is not None and validate_installation_evidence(installation)[1] != hashes["installation_sha256"]:
        raise RuntimeError("scan installation evidence changed")
    facts = {"source_sha256": hashes["source_sha256"], "requested_pages": list(pages),
             "recovery_sha256": hashes.get("recovery_sha256"), "proposals_sha256": hashes.get("proposals_sha256"),
             "installation_sha256": hashes.get("installation_sha256"),
             "locks": {name: hashes[name] for name in _LOCKS},
             "configuration": configuration, "generation": _generation()}
    request = ScanRequest(pdf, output, recovery_file, proposals_file, installation, pages, parent, tuple(captured),
                          _canonical(facts), None if recovery is None else _canonical(recovery),
                          None if proposals is None else _canonical(proposals), recipe=recipe)
    recheck_scan_request(request)
    return request


def recheck_scan_request(request: ScanRequest) -> None:
    from ocr_scan_omission import configuration_for_recipe

    if type(request) is not ScanRequest or _directory(request.output_dir.parent) != request.parent_identity:
        raise RuntimeError("scan output parent generation changed")
    configuration = configuration_for_recipe(request.recipe)
    facts = json.loads(request.request_json)
    hashes = {}
    for item in request.inputs:
        _, digest, identity = _snapshot(item.path, item.limit)
        if digest != item.sha256 or identity != item.identity:
            raise RuntimeError("scan input generation changed")
        hashes[item.key] = digest
    expected = {"source_sha256": hashes["source_sha256"], "requested_pages": list(_pages(list(request.requested_pages))),
                "recovery_sha256": hashes.get("recovery_sha256"), "proposals_sha256": hashes.get("proposals_sha256"),
                "installation_sha256": hashes.get("installation_sha256"),
                "locks": {name: hashes[name] for name in _LOCKS}, "configuration": configuration,
                "generation": _generation()}
    if _canonical(facts) != _canonical(expected):
        raise RuntimeError("scan recipe or interpreter/producer generation changed")
    if request.installation_path is not None:
        if validate_installation_evidence(request.installation_path)[1] != hashes["installation_sha256"]:
            raise RuntimeError("scan installation generation changed")
    if request.recovery_path is not None:
        from ocr_recovery_comparison import validate_recovery_report

        value, _, _ = _json(request.recovery_path, MAX_RECOVERY_BYTES)
        if _canonical(validate_recovery_report(value)) != request.recovery_json:
            raise RuntimeError("scan recovery snapshot changed")
    if request.proposals_path is not None:
        from ocr_docling import validate_docling_proposals

        value, _, _ = _json(request.proposals_path, MAX_PROPOSALS_BYTES)
        if _canonical(validate_docling_proposals(value, recovery=json.loads(request.recovery_json),
                recovery_sha256=hashes["recovery_sha256"])) != request.proposals_json:
            raise RuntimeError("scan proposal snapshot changed")


def _comparison(request: ScanRequest) -> dict:
    facts = json.loads(request.request_json)
    return {"recovery": None if request.recovery_json is None else json.loads(request.recovery_json),
            "recovery_sha256": facts["recovery_sha256"],
            "proposals": None if request.proposals_json is None else json.loads(request.proposals_json),
            "proposals_sha256": facts["proposals_sha256"]}


def _validate_scan(value: object, request: ScanRequest) -> dict:
    from ocr_scan_omission import configuration_for_recipe, validate_scan_observation

    scan = validate_scan_observation(value)
    facts = json.loads(request.request_json)
    if (scan["source_sha256"] != facts["source_sha256"]
            or _canonical(scan["requested_pages"]) != _canonical(facts["requested_pages"])
            or _canonical(scan["configuration"]) != _canonical(facts["configuration"])
            or _canonical(scan["configuration"]) != _canonical(configuration_for_recipe(request.recipe))):
        raise ValueError("scan observation differs from requested source, cohort or recipe")
    if request.recovery_json is not None and scan["page_count"] != json.loads(request.recovery_json)["page_count"]:
        raise ValueError("scan actual page count differs from saved recovery")
    return scan


def _runtime_evidence(elapsed: float) -> dict:
    return {"elapsed_seconds": elapsed, "packages": _runtime_metadata()}


def _validate_runtime(value: object, request: ScanRequest, scan: dict) -> dict:
    if type(value) is not dict or set(value) != {"elapsed_seconds", "packages"}:
        raise ValueError("scan runtime has invalid fields")
    elapsed = value["elapsed_seconds"]
    if type(elapsed) not in (int, float) or not 0 <= elapsed <= 86400 or not math.isfinite(elapsed):
        raise ValueError("scan elapsed duration exceeds bounds")
    baseline = json.loads(request.request_json)["generation"]["runtime"]
    packages = value["packages"]
    if _canonical(_package_baseline(packages)) != _canonical(baseline):
        raise ValueError("scan runtime package baseline differs from request")
    for package in packages["packages"]:
        versions = package["loaded_versions"]
        if (type(versions) is not list or len(versions) > 2 or versions != sorted(set(versions))
                or any(type(version) is not str or _VERSION.fullmatch(version) is None for version in versions)):
            raise ValueError("scan loaded version observations are invalid")
        required = package["required_version"]
        accepted = {required}
        if package["name"] == "opencv-python" and len(required.split(".")) == 4:
            accepted.add(required.rsplit(".", 1)[0])
        agrees = None if not versions else all(version in accepted for version in versions)
        if package["loaded_version_agrees"] is not agrees or agrees is False or not versions:
            raise ValueError("scan loaded library versions are unavailable or differ from locked recipe")
    return json.loads(_canonical(value))


def run_scan_bundle(pdf_path: Path, output_dir: Path, *, requested_pages: list[int],
                    recovery_path: Path | None = None, proposals_path: Path | None = None,
                    installation_path: Path | None = None, recipe: str = "legacy-v1") -> dict:
    """Execute one new bundle. Direct API hosts must provide native containment.

    The CLI supplies containment by default. A failure can leave a private
    incomplete directory; only manifest.json signifies completed publication.
    """
    from ocr_scan_omission import build_scan_omission_report, validate_scan_omission_report

    request = scan_request(pdf_path, output_dir, requested_pages=requested_pages, recovery_path=recovery_path,
                           proposals_path=proposals_path, installation_path=installation_path, recipe=recipe)
    with PathLease(request.output_dir, backend="ocr-scan", collection_name="bundle", operation="execute",
                   timeout=0, resource_description="OCR scan bundle"):
        recheck_scan_request(request)
        request.output_dir.mkdir(exist_ok=False)
        storage_policy.enforce_private_path(request.output_dir, directory=True)
        directory_id = _directory(request.output_dir)
        published = {}

        def recheck():
            if _directory(request.output_dir) != directory_id:
                raise RuntimeError("scan output directory changed")
            recheck_scan_request(request)
            for name, (digest, identity) in published.items():
                _, current, current_id = _snapshot(request.output_dir / name, _LIMITS[name])
                if (current, current_id) != (digest, identity):
                    raise RuntimeError("published scan artifact changed")

        def publish(name: str, payload: dict) -> str:
            raw = (json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
            if len(raw) > _LIMITS[name]:
                raise ValueError("serialized scan artifact exceeds bounds")
            digest = hashlib.sha256(raw).hexdigest()

            def commit(temporary, destination):
                recheck()
                if _snapshot(Path(temporary), _LIMITS[name])[0] != raw:
                    raise RuntimeError("staged scan artifact differs from intended bytes")
                if _directory(request.output_dir) != directory_id:
                    raise RuntimeError("scan publication directory changed")
                _publish_new_report(temporary, destination)

            storage_policy.atomic_write_private(request.output_dir / name, lambda handle: handle.write(raw),
                                                text=False, replace_fn=commit)
            _, current, identity = _snapshot(request.output_dir / name, _LIMITS[name])
            if current != digest:
                raise RuntimeError("published scan bytes differ from intended artifact")
            published[name] = digest, identity
            return digest

        raw, digest, _ = _snapshot(request.pdf_path, MAX_SOURCE_BYTES)
        if digest != json.loads(request.request_json)["source_sha256"]:
            raise RuntimeError("scan source changed before native execution")
        from ocr_scan_runtime import collect_scan_observation

        native_options = {"requested_pages": list(request.requested_pages)}
        if request.recipe != "legacy-v1":
            native_options["recipe"] = request.recipe
        started = time.monotonic()
        scan = _validate_scan(collect_scan_observation(raw, **native_options), request)
        del raw
        runtime = _validate_runtime(_runtime_evidence(time.monotonic() - started), request, scan)
        scan_json = _canonical(scan)
        diagnostics = build_scan_omission_report(json.loads(scan_json), **_comparison(request))
        diagnostics = validate_scan_omission_report(diagnostics, json.loads(scan_json), **_comparison(request))
        scan_digest = publish("scan.json", scan)
        diagnostics_digest = publish("diagnostics.json", diagnostics)
        facts = json.loads(request.request_json)
        manifest = {"schema_version": 1, "kind": "ocr_scan_bundle", "request": facts,
                    "request_sha256": _digest(facts), "scan_sha256": scan_digest,
                    "diagnostics_sha256": diagnostics_digest, "runtime": runtime,
                    "installation_evidence": "not_supplied" if installation_path is None else "verified_local_bindings",
                    "requires_attention": True, "recognition_rerun": False,
                    "canonical_extraction_modified": False, "scope": _SCOPE}
        publish("manifest.json", manifest)
    return {"scan": scan, "diagnostics": diagnostics, "manifest": manifest}


def read_scan_completion(output_dir: Path, *, request: ScanRequest) -> dict:
    """Rebuild derived diagnostics and recheck exact request/artifact generations."""
    from ocr_scan_omission import validate_scan_omission_report

    output = Path(output_dir).absolute()
    if output != request.output_dir:
        raise ValueError("scan completion differs from requested output")
    with PathLease(output, backend="ocr-scan", collection_name="bundle", operation="readback",
                   timeout=0, resource_description="OCR scan bundle"):
        directory_id = _directory(output)
        recheck_scan_request(request)
        manifest, digest, identity = _json(output / "manifest.json", _LIMITS["manifest.json"])
        records = {"manifest.json": (digest, identity)}
        keys = {"schema_version", "kind", "request", "request_sha256", "scan_sha256", "diagnostics_sha256",
                "runtime", "installation_evidence", "requires_attention", "recognition_rerun",
                "canonical_extraction_modified", "scope"}
        facts = json.loads(request.request_json)
        if (type(manifest) is not dict or set(manifest) != keys or type(manifest["schema_version"]) is not int
                or manifest["schema_version"] != 1 or manifest["kind"] != "ocr_scan_bundle"
                or manifest["scope"] != _SCOPE or manifest["requires_attention"] is not True
                or manifest["recognition_rerun"] is not False or manifest["canonical_extraction_modified"] is not False
                or _canonical(manifest["request"]) != request.request_json or manifest["request_sha256"] != _digest(facts)
                or manifest["installation_evidence"] != ("not_supplied" if request.installation_path is None else "verified_local_bindings")):
            raise ValueError("scan completion differs from exact requested evidence")
        payloads = {}
        for name in ("scan", "diagnostics"):
            filename = name + ".json"
            payloads[name], digest, identity = _json(output / filename, _LIMITS[filename])
            records[filename] = digest, identity
            if digest != manifest[name + "_sha256"]:
                raise ValueError("scan artifact differs from completed digest")
        scan = _validate_scan(payloads["scan"], request)
        _validate_runtime(manifest["runtime"], request, scan)
        diagnostics = validate_scan_omission_report(payloads["diagnostics"], scan, **_comparison(request))
        recheck_scan_request(request)
        for filename, (digest, identity) in records.items():
            _, current, current_id = _snapshot(output / filename, _LIMITS[filename])
            if (current, current_id) != (digest, identity):
                raise RuntimeError("scan completion changed during readback")
        if _directory(output) != directory_id:
            raise RuntimeError("scan completion directory changed during readback")
    return {"scan": scan, "diagnostics": diagnostics, "manifest": manifest}


def _fields(value: object, keys: set[str]) -> dict:
    if type(value) is not dict or set(value) != keys:
        raise ValueError("invalid historical scan declaration fields")
    return value


def _sha(value: object) -> str:
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("invalid historical scan digest")
    return value


def _declared_packages(value: object, *, loaded: bool) -> dict:
    """Check recorded versions structurally, without inspecting this environment."""
    value = _fields(value, {"schema_version", "kind", "lock_sha256", "environment", "packages",
        "version_check_scope", "package_versions_match", "effective_runtime", "effective_runtime_source",
        "locked_environment_verified", "artifact_verification", "requires_attention"})
    if (type(value["schema_version"]) is not int or value["schema_version"] != 1
            or value["kind"] != "ocr_experiment_runtime" or value["version_check_scope"] != "requested_packages_only"
            or value["package_versions_match"] is not True or value["effective_runtime"] is not None
            or value["effective_runtime_source"] is not None or value["locked_environment_verified"] is not False
            or value["requires_attention"] is not True):
        raise ValueError("unsupported historical scan runtime declaration")
    _sha(value["lock_sha256"])
    verification = {"package_wheels": "not_verified", "model_files": "not_verified_by_capture",
                    "loaded_native_libraries": "not_verified"}
    if _canonical(value["artifact_verification"]) != _canonical(verification):
        raise ValueError("historical scan cannot claim native byte attestation")
    environment = _fields(value["environment"], {"python_full_version", "python_version", "os_name",
        "sys_platform", "platform_machine", "platform_python_implementation"})
    if any(type(item) is not str or _VERSION.fullmatch(item) is None for item in environment.values()):
        raise ValueError("invalid historical scan environment declaration")
    if environment["python_version"] != ".".join(environment["python_full_version"].split(".")[:2]):
        raise ValueError("historical Python version declarations disagree")
    packages = value["packages"]
    if type(packages) is not list or len(packages) != 3:
        raise ValueError("historical scan requires three package declarations")
    for name, package in zip(sorted(_PACKAGES), packages):
        _fields(package, {"name", "required_version", "installed_version", "status",
                          "loaded_versions", "loaded_version_agrees"})
        required = package["required_version"]
        if (package["name"] != name or type(required) is not str or _VERSION.fullmatch(required) is None
                or package["installed_version"] != required or package["status"] != "version_match"):
            raise ValueError("historical scan package declaration contradicts its pin")
        versions = package["loaded_versions"]
        if not loaded:
            if type(versions) is not list or versions or package["loaded_version_agrees"] is not None:
                raise ValueError("historical request baseline contains loaded runtime observations")
            continue
        if (type(versions) is not list or not 1 <= len(versions) <= 2
                or any(type(version) is not str or _VERSION.fullmatch(version) is None for version in versions)
                or versions != sorted(set(versions)) or package["loaded_version_agrees"] is not True):
            raise ValueError("historical loaded versions are missing or malformed")
        accepted = {required}
        if name == "opencv-python" and len(required.split(".")) == 4:
            accepted.add(required.rsplit(".", 1)[0])
        if not set(versions) <= accepted:
            raise ValueError("historical loaded versions contradict declared package pins")
    return value


def _historical_manifest(value: object, scan: dict) -> dict:
    manifest = _fields(value, {"schema_version", "kind", "request", "request_sha256", "scan_sha256",
        "diagnostics_sha256", "runtime", "installation_evidence", "requires_attention", "recognition_rerun",
        "canonical_extraction_modified", "scope"})
    if (type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1
            or manifest["kind"] != "ocr_scan_bundle" or manifest["scope"] != _SCOPE
            or manifest["requires_attention"] is not True or manifest["recognition_rerun"] is not False
            or manifest["canonical_extraction_modified"] is not False):
        raise ValueError("unsupported historical scan completion")
    facts = _fields(manifest["request"], {"source_sha256", "requested_pages", "recovery_sha256",
        "proposals_sha256", "installation_sha256", "locks", "configuration", "generation"})
    for name in ("source_sha256", "recovery_sha256", "proposals_sha256", "installation_sha256"):
        if name == "source_sha256" or facts[name] is not None:
            _sha(facts[name])
    if facts["proposals_sha256"] is not None and facts["recovery_sha256"] is None:
        raise ValueError("historical proposals lack their bound recovery")
    if (facts["source_sha256"] != scan["source_sha256"]
            or _canonical(facts["requested_pages"]) != _canonical(scan["requested_pages"])
            or _canonical(facts["configuration"]) != _canonical(scan["configuration"])
            or _sha(manifest["request_sha256"]) != _digest(facts)):
        raise ValueError("historical scan request differs from its observation")
    for name in ("scan_sha256", "diagnostics_sha256"):
        _sha(manifest[name])
    locks = _fields(facts["locks"], set(_LOCKS))
    for digest in locks.values():
        _sha(digest)
    generation = _fields(facts["generation"], {"environment_identity_sha256", "executables", "runtime", "producer_sources"})
    _sha(generation["environment_identity_sha256"])
    for digest in _fields(generation["executables"], {"python", "base_python"}).values():
        _sha(digest)
    for digest in _fields(generation["producer_sources"], _HISTORICAL_PRODUCERS_V1).values():
        _sha(digest)
    baseline = _declared_packages(generation["runtime"], loaded=False)
    if baseline["lock_sha256"] != locks["requirements-full.lock"]:
        raise ValueError("historical runtime differs from its declared full lock")
    runtime = _fields(manifest["runtime"], {"elapsed_seconds", "packages"})
    elapsed = runtime["elapsed_seconds"]
    if type(elapsed) not in (int, float) or not 0 <= elapsed <= 86400 or not math.isfinite(elapsed):
        raise ValueError("historical scan elapsed duration exceeds bounds")
    if _canonical(_package_baseline(_declared_packages(runtime["packages"], loaded=True))) != _canonical(baseline):
        raise ValueError("historical runtime differs from requested package declarations")
    if manifest["installation_evidence"] != ("not_supplied" if facts["installation_sha256"] is None else "verified_local_bindings"):
        raise ValueError("historical installation declaration contradicts its binding")
    return manifest


def read_scan_review_bundle(output_dir: Path, *, source_sha256: str, page_count: int,
                            recovery: dict, recovery_sha256: str,
                            proposals=None, proposals_sha256=None) -> dict:
    """Import fixed historical declarations, separately deriving current review.

    Old producer/lock/environment/install hashes are not checked against current
    files or interpreted as current qualification. Original diagnostic bindings
    must be available exactly; a source-only bundle can gain a separate current
    comparison without rewriting its original diagnostics. No native imports.
    """
    from ocr_recovery_comparison import validate_recovery_report
    from ocr_scan_omission import (build_scan_omission_report, validate_scan_observation,
                                   validate_scan_omission_report)

    source_sha256, recovery_sha256 = _sha(source_sha256), _sha(recovery_sha256)
    if type(page_count) is not int or not 1 <= page_count <= 5000:
        raise ValueError("invalid review source page count")
    recovery = validate_recovery_report(recovery)
    if recovery["source_sha256"] != source_sha256 or recovery["page_count"] != page_count:
        raise ValueError("scan review source and recovery differ")
    if (proposals is None) != (proposals_sha256 is None):
        raise ValueError("scan review proposals require their exact digest")
    if proposals is not None:
        from ocr_docling import validate_docling_proposals

        proposals_sha256 = _sha(proposals_sha256)
        proposals = validate_docling_proposals(proposals, recovery=recovery, recovery_sha256=recovery_sha256)
    output = Path(output_dir).absolute()
    with PathLease(output, backend="ocr-scan", collection_name="bundle", operation="review",
                   timeout=0, resource_description="OCR scan bundle"):
        directory_id = _directory(output)
        payloads, snapshots = {}, {}
        for name in ("scan", "diagnostics", "manifest"):
            filename = name + ".json"
            payloads[name], digest, identity = _json(output / filename, _LIMITS[filename])
            snapshots[filename] = digest, identity
        scan = validate_scan_observation(payloads["scan"])
        if scan["source_sha256"] != source_sha256 or scan["page_count"] != page_count:
            raise ValueError("historical scan source differs from current review")
        manifest = _historical_manifest(payloads["manifest"], scan)
        for name in ("scan", "diagnostics"):
            if manifest[name + "_sha256"] != snapshots[name + ".json"][0]:
                raise ValueError("historical scan artifact differs from completed digest")
        facts = manifest["request"]
        if ((facts["recovery_sha256"] is not None and facts["recovery_sha256"] != recovery_sha256)
                or (facts["proposals_sha256"] is not None and facts["proposals_sha256"] != proposals_sha256)):
            raise ValueError("historical scan comparison inputs are not available exactly")
        original = {"recovery": recovery if facts["recovery_sha256"] is not None else None,
                    "recovery_sha256": facts["recovery_sha256"],
                    "proposals": proposals if facts["proposals_sha256"] is not None else None,
                    "proposals_sha256": facts["proposals_sha256"]}
        diagnostics = validate_scan_omission_report(payloads["diagnostics"], scan, **original)
        review = build_scan_omission_report(scan, recovery=recovery, recovery_sha256=recovery_sha256,
                                            proposals=proposals, proposals_sha256=proposals_sha256)
        for filename, (digest, identity) in snapshots.items():
            _, current, current_id = _snapshot(output / filename, _LIMITS[filename])
            if (current, current_id) != (digest, identity):
                raise RuntimeError("historical scan artifacts changed during review import")
        if _directory(output) != directory_id:
            raise RuntimeError("historical scan directory changed during review import")
    return {"scan": scan, "diagnostics": diagnostics, "manifest": manifest, "review_diagnostics": review}
