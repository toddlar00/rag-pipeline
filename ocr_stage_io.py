"""Fixed-input, create-only bundles for observed OCR-stage diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import time

from evaluation_inputs import _strict_json_bytes
from ocr_checkpoint_runtime import capture_identity
from ocr_execution_receipt import capture_execution_receipt, validate_execution_receipt, validate_installation_evidence
from ocr_recovery import _publish_new_report
from ocr_stage_diagnostics import (DEFAULT_CONFIGURATION, evaluate_stage_diagnostics,
                                   validate_stage_observation, validate_stage_reference)
from resource_lease import PathLease
import storage_policy


ROOT = Path(__file__).resolve().parent
MAX_SOURCE_BYTES = 256 * 1024 * 1024
MAX_REFERENCE_BYTES = 4 * 1024 * 1024
_LIMITS = {"observation.json": 32 * 1024 * 1024, "diagnostics.json": 32 * 1024 * 1024,
           "execution.json": 4 * 1024 * 1024, "manifest.json": 512 * 1024}
_SCOPE = "Local stage execution and declared gold annotations; not signed evidence, complete-source verification, or accuracy certification."


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _identity(info) -> tuple:
    identity = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
    return identity if os.name == "nt" else identity + (info.st_ctime_ns,)


def _snapshot(path: Path, limit: int, *, retain: bool = False) -> tuple[bytes | None, str]:
    """Both same-handle passes are bounded, including concurrently growing files."""
    storage_policy.assert_no_link_components(path)
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or not 1 <= before.st_size <= limit:
        raise ValueError("stage input must be a bounded single-linked regular file")
    raw, hashes = None, []
    with path.open("rb") as handle:
        if _identity(os.fstat(handle.fileno())) != _identity(before):
            raise RuntimeError("stage input identity changed")
        for attempt in range(2):
            handle.seek(0)
            digest, count = hashlib.sha256(), 0
            blocks = [] if retain and attempt == 0 else None
            while True:
                block = handle.read(min(1024 * 1024, limit + 1 - count))
                if not block:
                    break
                count += len(block)
                if count > limit:
                    raise ValueError("stage streaming input exceeds bounds")
                digest.update(block)
                if blocks is not None:
                    blocks.append(block)
            after = os.fstat(handle.fileno())
            if count != before.st_size or after.st_nlink != 1 or _identity(after) != _identity(before):
                raise RuntimeError("stage input changed during reading")
            hashes.append(digest.hexdigest())
            if blocks is not None:
                raw = b"".join(blocks)
    storage_policy.assert_no_link_components(path)
    after = path.lstat()
    if after.st_nlink != 1 or _identity(after) != _identity(before) or hashes[0] != hashes[1]:
        raise RuntimeError("stage input generation changed")
    return raw, hashes[0]


def _json(path: Path, limit: int) -> tuple[dict, str]:
    raw, digest = _snapshot(path, limit, retain=True)
    return _strict_json_bytes(raw, label="stage artifact", max_bytes=limit), digest


@dataclass(frozen=True)
class StageRequest:
    pdf_path: Path
    reference_path: Path
    output_dir: Path
    installation_path: Path
    specs: tuple
    inputs: dict
    configuration: dict
    reference: dict
    generation: dict


def stage_request(pdf_path: Path, reference_path: Path, output_dir: Path, *,
                  installation_path: Path, readback: bool = False) -> StageRequest:
    """Read-only admission, without importing the renderer or OCR engine."""
    if installation_path is None or type(readback) is not bool:
        raise ValueError("stage diagnostics require qualified installation evidence")
    pdf, reference_file, output, installation = (
        Path(path).absolute() for path in (pdf_path, reference_path, output_dir, installation_path))
    storage_policy.assert_no_link_components(output)
    if ((readback and not output.is_dir()) or (not readback and output.exists()) or not output.parent.is_dir()):
        raise ValueError("choose a new stage bundle directory under an existing parent")
    if not readback:
        entrypoint = getattr(sys.modules.get("__main__"), "__file__", None)
        if isinstance(entrypoint, str):
            launcher = Path(entrypoint).absolute()
            if launcher.parent in (ROOT, ROOT / "tools") and launcher != ROOT / "tools" / "diagnose_ocr_stages.py":
                raise ValueError("stage execution requires its fixed CLI or an external API host")
    specs = [(pdf, "source_sha256", MAX_SOURCE_BYTES), (reference_file, "reference_sha256", MAX_REFERENCE_BYTES),
             (installation, "installation_sha256", 64 * 1024)]
    for name, key in (("requirements-full.lock", "dependency_full_sha256"),
                      ("requirements-test.lock", "dependency_test_sha256"),
                      ("requirements-lock-tools.lock", "dependency_tools_sha256"),
                      ("model-artifact-policy.json", "model_policy_sha256"),
                      ("model-artifacts.lock.json", "model_lock_sha256")):
        specs.append((ROOT / name, key, 8 * 1024 * 1024))
    for index, (path, _, _) in enumerate(specs):
        storage_policy.assert_no_link_components(path)
        if any(os.path.samefile(path, previous[0]) for previous in specs[:index]):
            raise ValueError("stage inputs must be distinct")
    inputs = {key: _snapshot(path, limit)[1] for path, key, limit in specs}
    reference, reference_digest = _json(reference_file, MAX_REFERENCE_BYTES)
    reference = validate_stage_reference(reference)
    if reference_digest != inputs["reference_sha256"] or reference["source_sha256"] != inputs["source_sha256"]:
        raise ValueError("stage gold and source bindings differ")
    validate_installation_evidence(installation)
    generation = capture_identity(installation_path=installation)
    configuration = dict(DEFAULT_CONFIGURATION)
    inputs.update(configuration_sha256=_digest(configuration), generation_sha256=_digest(generation))
    request = StageRequest(pdf, reference_file, output, installation, tuple(specs), inputs,
                           configuration, reference, generation)
    recheck_stage_request(request)
    return request


def recheck_stage_request(request: StageRequest) -> None:
    for path, key, limit in request.specs:
        if _snapshot(path, limit)[1] != request.inputs[key]:
            raise RuntimeError("stage input changed")
    reference, reference_digest = _json(request.reference_path, MAX_REFERENCE_BYTES)
    if (reference_digest != request.inputs["reference_sha256"]
            or _canonical(validate_stage_reference(reference)) != _canonical(request.reference)
            or _canonical(request.configuration) != _canonical(DEFAULT_CONFIGURATION)):
        raise RuntimeError("stage request differs from its fixed input or recipe")
    if (_digest(request.configuration) != request.inputs["configuration_sha256"]
            or _digest(request.generation) != request.inputs["generation_sha256"]
            or _digest(capture_identity(installation_path=request.installation_path)) != request.inputs["generation_sha256"]):
        raise RuntimeError("stage interpreter or producer generation changed")


def _join_execution(observation: dict, receipt: dict, request: StageRequest, observation_digest: str) -> None:
    validate_execution_receipt(receipt, installation_path=request.installation_path)
    expected_calls = [{"id": call["id"], "status": call["status"]} for call in observation["calls"]]
    receipt_calls = [{"id": call["id"], "status": call["status"]} for call in receipt["calls"]]
    if (receipt["operation"] != "stage_diagnostics" or _canonical(receipt["inputs"]) != _canonical(request.inputs)
            or receipt["output_sha256"] != observation_digest
            or receipt["installation_sha256"] != request.inputs["installation_sha256"]
            or receipt["environment_identity_sha256"] != request.generation["environment_sha256"]
            or expected_calls != receipt_calls):
        raise ValueError("stage observations are detached from actual execution receipt")
    sources = receipt["project_sources_at_observation"]
    def source_path(name):
        return "tools/diagnose_ocr_stages.py" if name == "entrypoint" else name.replace(".", "/") + ".py"
    if any(request.generation["producer_sources"].get(source_path(name)) != digest for name, digest in sources.items()):
        raise ValueError("stage receipt producer differs from requested generation")


def run_stage_bundle(pdf_path: Path, reference_path: Path, output_dir: Path, *,
                     installation_path: Path) -> dict:
    """Execute a new private bundle; the final manifest is the completion marker."""
    from ocr_stage_runtime import collect_stage_observation

    request = stage_request(pdf_path, reference_path, output_dir, installation_path=installation_path)
    request.output_dir.mkdir(exist_ok=False)
    storage_policy.enforce_private_path(request.output_dir, directory=True)
    directory_id = (request.output_dir.stat().st_dev, request.output_dir.stat().st_ino)
    published = {}

    def recheck():
        storage_policy.assert_no_link_components(request.output_dir)
        info = request.output_dir.stat()
        if not stat.S_ISDIR(info.st_mode) or (info.st_dev, info.st_ino) != directory_id:
            raise RuntimeError("stage output directory changed")
        recheck_stage_request(request)
        for name, digest in published.items():
            if _snapshot(request.output_dir / name, _LIMITS[name])[1] != digest:
                raise RuntimeError("stage result changed before completion")

    def publish(name, payload):
        encoded = (json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
        if len(encoded) > _LIMITS[name]:
            raise ValueError("serialized stage artifact exceeds bounds")
        intended_digest = hashlib.sha256(encoded).hexdigest()

        def commit(temporary, destination):
            recheck()
            if _snapshot(temporary, _LIMITS[name])[1] != intended_digest:
                raise RuntimeError("staged diagnostic bytes differ from intended artifact")
            _publish_new_report(temporary, destination)

        storage_policy.atomic_write_private_json(request.output_dir / name, payload, indent=2, replace_fn=commit)
        published[name] = _snapshot(request.output_dir / name, _LIMITS[name])[1]
        if published[name] != intended_digest:
            raise RuntimeError("published stage artifact differs from intended bytes")
        return published[name]

    with PathLease(request.output_dir, backend="ocr-stage", collection_name="bundle", operation="execute",
                   timeout=0, resource_description="OCR stage diagnostic bundle"):
        raw_source, source_digest = _snapshot(request.pdf_path, MAX_SOURCE_BYTES, retain=True)
        if source_digest != request.inputs["source_sha256"]:
            raise RuntimeError("stage source changed before execution")
        started = time.monotonic()
        observation, recorder = collect_stage_observation(
            raw_source, request.reference, reference_sha256=request.inputs["reference_sha256"],
            configuration=request.configuration)
        del raw_source
        elapsed = time.monotonic() - started
        observation_digest = publish("observation.json", observation)
        execution = capture_execution_receipt(
            operation="stage_diagnostics", inputs=request.inputs, output_sha256=observation_digest,
            observation=recorder.observation, calls=recorder.receipt_calls,
            elapsed_seconds=elapsed, installation_path=request.installation_path)
        _join_execution(observation, execution, request, observation_digest)
        execution_digest = publish("execution.json", execution)
        diagnostics = evaluate_stage_diagnostics(request.reference, observation,
                                                  request.inputs["reference_sha256"], observation_digest)
        diagnostics_digest = publish("diagnostics.json", diagnostics)
        manifest = {"schema_version": 1, "kind": "ocr_stage_bundle", "inputs": request.inputs,
                    "configuration": request.configuration, "generation": request.generation,
                    "observation_sha256": observation_digest, "execution_sha256": execution_digest,
                    "diagnostics_sha256": diagnostics_digest, "requires_attention": True,
                    "canonical_extraction_modified": False, "scope": _SCOPE}
        publish("manifest.json", manifest)
    return {"manifest": manifest, "observation": observation, "execution": execution, "diagnostics": diagnostics}


def read_stage_completion(output_dir: Path, *, request: StageRequest) -> dict:
    """Rebuild diagnostics and verify the exact requested local generation."""
    output = Path(output_dir).absolute()
    if output != request.output_dir:
        raise ValueError("stage completion directory differs from request")
    storage_policy.assert_no_link_components(output)
    directory_id = (output.stat().st_dev, output.stat().st_ino)
    recheck_stage_request(request)
    manifest, manifest_digest = _json(output / "manifest.json", _LIMITS["manifest.json"])
    keys = {"schema_version", "kind", "inputs", "configuration", "generation", "observation_sha256",
            "execution_sha256", "diagnostics_sha256", "requires_attention", "canonical_extraction_modified", "scope"}
    if (not isinstance(manifest, dict) or set(manifest) != keys or type(manifest["schema_version"]) is not int
            or manifest["schema_version"] != 1 or manifest["kind"] != "ocr_stage_bundle"
            or manifest["requires_attention"] is not True or manifest["canonical_extraction_modified"] is not False
            or manifest["scope"] != _SCOPE or _canonical(manifest["inputs"]) != _canonical(request.inputs)
            or _canonical(manifest["configuration"]) != _canonical(request.configuration)
            or _canonical(manifest["generation"]) != _canonical(request.generation)):
        raise ValueError("stage completion differs from requested bindings")
    payloads, hashes = {}, {"manifest.json": manifest_digest}
    for name in ("observation", "execution", "diagnostics"):
        filename = name + ".json"
        payloads[name], digest = _json(output / filename, _LIMITS[filename])
        hashes[filename] = digest
        if digest != manifest[name + "_sha256"]:
            raise ValueError("stage artifact differs from completion")
    observation = validate_stage_observation(payloads["observation"], request.reference, request.inputs["reference_sha256"])
    if _canonical(observation["configuration"]) != _canonical(request.configuration):
        raise ValueError("stage observation ignored requested recipe")
    _join_execution(observation, payloads["execution"], request, hashes["observation.json"])
    rebuilt = evaluate_stage_diagnostics(request.reference, observation, request.inputs["reference_sha256"], hashes["observation.json"])
    if _canonical(rebuilt) != _canonical(payloads["diagnostics"]):
        raise ValueError("stage diagnostic summary contradicts its observations")
    recheck_stage_request(request)
    for filename, digest in hashes.items():
        if _snapshot(output / filename, _LIMITS[filename])[1] != digest:
            raise RuntimeError("stage completion changed during validation")
    storage_policy.assert_no_link_components(output)
    if (output.stat().st_dev, output.stat().st_ino) != directory_id:
        raise RuntimeError("stage completion directory changed")
    return {"manifest": manifest, **payloads}
