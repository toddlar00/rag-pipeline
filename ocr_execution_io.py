"""Create-only experiment bundles with unchanged OCR reports and runtime receipts."""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import time

from evaluation_inputs import _read_snapshot, _strict_json_bytes
from ocr_execution_receipt import (ExecutionRecorder, capture_execution_receipt, validate_execution_receipt,
                                   validate_installation_evidence)
from ocr_experiment_runtime import capture_runtime_manifest
import ocr_recovery
from resource_lease import PathLease
import storage_policy


ROOT = Path(__file__).resolve().parent
_BUNDLE_SCOPE = "Completed provenance bundle; candidates still require accuracy evaluation and manual review."


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, allow_nan=False, separators=(",", ":"))


def _report_limit(operation: str) -> int:
    return (64 if operation == "pages" else 128) * 1024 * 1024


def execution_request(pdf_path: Path, output_dir: Path, *, operation: str = "pages",
                      policy: ocr_recovery.RetryPolicy | None = None, requested_pages: tuple[int, ...] = (),
                      evidence_path: Path | None = None, recovery_path: Path | None = None,
                      plan_path: Path | None = None, installation_path: Path | None = None) -> tuple:
    """Validate and snapshot a request without importing OCR libraries or writing."""
    if operation not in {"pages", "regions", "hardscan"}:
        raise ValueError("unknown OCR execution operation")
    policy = ocr_recovery.RetryPolicy() if policy is None else policy
    if not isinstance(policy, ocr_recovery.RetryPolicy):
        raise ValueError("invalid OCR execution policy")
    if (not isinstance(requested_pages, tuple) or len(requested_pages) > 20
            or any(type(page) is not int or not 1 <= page <= ocr_recovery.MAX_DOCUMENT_PAGES for page in requested_pages)
            or len(set(requested_pages)) != len(requested_pages)):
        raise ValueError("invalid requested execution pages")
    if operation == "pages" and (recovery_path is not None or plan_path is not None):
        raise ValueError("page execution does not accept region inputs")
    if operation != "pages" and (recovery_path is None or plan_path is None or evidence_path is not None
                                 or requested_pages or policy != ocr_recovery.RetryPolicy(dpi=policy.dpi)):
        raise ValueError("crop execution accepts only DPI and its recovery and plan inputs")
    pdf_path, output_dir = Path(pdf_path).absolute(), Path(output_dir).absolute()
    specs = [(pdf_path, "source_sha256", 256 * 1024 * 1024)]
    for filename, key in (("requirements-full.lock", "dependency_full_sha256"),
                           ("requirements-test.lock", "dependency_test_sha256"),
                           ("requirements-lock-tools.lock", "dependency_tools_sha256"),
                           ("model-artifact-policy.json", "model_policy_sha256"),
                           ("model-artifacts.lock.json", "model_lock_sha256")):
        specs.append((ROOT / filename, key, 8 * 1024 * 1024))
    for path, key, limit in ((evidence_path, "evidence_sha256", 64 * 1024 * 1024),
                             (recovery_path, "recovery_sha256", 64 * 1024 * 1024),
                             (plan_path, "plan_sha256", 1024 * 1024),
                             (installation_path, "installation_sha256", 64 * 1024)):
        if path is not None:
            specs.append((Path(path).absolute(), key, limit))
    for path in [output_dir] + [item[0] for item in specs]:
        storage_policy.assert_no_link_components(path)
    if not output_dir.parent.is_dir() or output_dir.exists():
        raise ValueError("choose a new execution directory under an existing parent")
    for index, (path, _, _) in enumerate(specs):
        if path.stat().st_nlink != 1 or any(os.path.samefile(path, prior[0]) for prior in specs[:index]):
            raise ValueError("execution inputs must be distinct single-linked files")
    inputs = {key: _read_snapshot(path, label="OCR execution input", max_bytes=limit)[1] for path, key, limit in specs}
    configuration = {"operation": operation, "policy": asdict(policy) if operation == "pages" else {"dpi": policy.dpi},
                     "requested_pages": list(requested_pages)}
    inputs["configuration_sha256"] = hashlib.sha256(json.dumps(configuration, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return pdf_path, output_dir, policy, specs, inputs, configuration


def read_execution_completion(output_dir: Path, *, inputs: dict, configuration: dict,
                              pdf_path: Path, recovery_path: Path | None = None, plan_path: Path | None = None,
                              installation_path: Path | None = None) -> dict:
    """Read back local child completion and requested bindings, not authenticate it."""
    raw, _ = _read_snapshot(Path(output_dir) / "manifest.json", label="execution completion", max_bytes=64 * 1024)
    manifest = _strict_json_bytes(raw, label="execution completion", max_bytes=64 * 1024)
    keys = {"schema_version", "kind", "operation", "inputs", "configuration", "report_sha256",
            "execution_sha256", "requires_attention", "scope"}
    if (set(manifest) != keys or type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1
            or manifest["kind"] != "ocr_execution_bundle" or manifest["operation"] != configuration["operation"]
            or _canonical(manifest["configuration"]) != _canonical(configuration) or _canonical(manifest["inputs"]) != _canonical(inputs)
            or manifest["scope"] != _BUNDLE_SCOPE or manifest["requires_attention"] is not True):
        raise ValueError("execution completion differs from request")
    receipt = None
    report = None
    for filename, key, limit in (("report.json", "report_sha256", _report_limit(configuration["operation"])),
                                  ("execution.json", "execution_sha256", 4 * 1024 * 1024)):
        raw, digest = _read_snapshot(Path(output_dir) / filename, label="execution result", max_bytes=limit)
        if digest != manifest[key]:
            raise ValueError("execution result differs from completion")
        value = _strict_json_bytes(raw, label="execution result", max_bytes=limit)
        if filename == "execution.json":
            receipt = value
        else:
            report = value
    if (type(receipt.get("schema_version")) is not int or receipt["schema_version"] != 1
            or receipt.get("kind") != "ocr_execution_receipt" or receipt.get("operation") != configuration["operation"]
            or _canonical(receipt.get("inputs")) != _canonical(inputs) or receipt.get("output_sha256") != manifest["report_sha256"]
            or receipt.get("installation_sha256") != inputs.get("installation_sha256")):
        raise ValueError("execution receipt differs from requested bindings")
    validate_execution_receipt(receipt, installation_path=installation_path)
    operation = configuration["operation"]
    if operation == "pages":
        from ocr_recovery_comparison import validate_recovery_report
        report = validate_recovery_report(report)
        policy = configuration["policy"]
        expected_selection = {"requested_pages": sorted(configuration["requested_pages"]), "max_pages": policy["max_pages"],
                              "min_chars": policy["min_chars"], "order": "requested_then_page_number"}
        expected_retry = {name: policy[name] for name in ("dpi", "max_pixels", "max_side")}
        expected_retry["attempts_per_page"] = 1
        if policy["preprocessing"] != "none":
            expected_retry["preprocessing"] = policy["preprocessing"]
        if (_canonical(report["selection"]) != _canonical(expected_selection)
                or _canonical(report["retry_configuration"]) != _canonical(expected_retry)
                or report["evidence_sha256"] != inputs.get("evidence_sha256")):
            raise ValueError("execution report differs from requested policy")
    elif operation == "regions":
        from ocr_regions import verify_region_review_request
        report = verify_region_review_request(report, pdf_path=pdf_path, recovery_path=recovery_path,
                                               plan_path=plan_path, dpi=configuration["policy"]["dpi"])
    else:
        from ocr_hardscan_io import verify_request
        report = verify_request(report, pdf_path=pdf_path, recovery_path=recovery_path, plan_path=plan_path,
                                dpi=configuration["policy"]["dpi"])
    if report["source_sha256"] != inputs["source_sha256"]:
        raise ValueError("execution report differs from requested source")
    return manifest


def run_execution_bundle(pdf_path: Path, output_dir: Path, *, operation: str = "pages",
                         policy: ocr_recovery.RetryPolicy | None = None, requested_pages: tuple[int, ...] = (),
                         evidence_path: Path | None = None, recovery_path: Path | None = None,
                         plan_path: Path | None = None, installation_path: Path | None = None) -> dict:
    """One process performs OCR and records its effective session observations.

    The manifest is published last. Failure retains an incomplete private
    directory; existing directories are never reused or recursively removed.
    """
    pdf_path, output_dir, policy, specs, inputs, configuration = execution_request(
        pdf_path, output_dir, operation=operation, policy=policy, requested_pages=requested_pages,
        evidence_path=evidence_path, recovery_path=recovery_path, plan_path=plan_path, installation_path=installation_path)
    if installation_path is not None:
        validate_installation_evidence(installation_path)
    capture_runtime_manifest(ROOT / "requirements-full.lock", require_version_match=installation_path is not None)
    recorder, started = ExecutionRecorder(), time.monotonic()
    output_dir.mkdir(exist_ok=False)
    storage_policy.enforce_private_path(output_dir, directory=True)
    directory_identity = (output_dir.stat().st_dev, output_dir.stat().st_ino)
    report_path, receipt_path = output_dir / "report.json", output_dir / "execution.json"

    def recheck():
        storage_policy.assert_no_link_components(output_dir)
        if (output_dir.stat().st_dev, output_dir.stat().st_ino) != directory_identity:
            raise RuntimeError("execution directory changed")
        for path, key, limit in specs:
            if path.stat().st_nlink != 1 or _read_snapshot(path, label="OCR execution input", max_bytes=limit)[1] != inputs[key]:
                raise RuntimeError("OCR execution input changed")
        if installation_path is not None:
            validate_installation_evidence(installation_path)

    with PathLease(output_dir, backend="ocr-execution", collection_name="bundle", operation="execute",
                   timeout=0, resource_description="OCR execution bundle"):
        if operation == "pages":
            from ocr_recovery_runtime import RapidOCRPageReader
            report = ocr_recovery.recover_pdf(pdf_path, report_path, policy=policy, requested_pages=requested_pages,
                                               evidence_path=evidence_path, reader_factory=recorder.reader_factory(RapidOCRPageReader))
        elif operation == "regions":
            from ocr_region_runtime import RapidOCRRegionReader
            from ocr_regions import recover_regions
            report = recover_regions(pdf_path, recovery_path, plan_path, report_path, dpi=policy.dpi,
                                     reader_factory=recorder.reader_factory(RapidOCRRegionReader))
        else:
            from ocr_hardscan_runtime import RapidOCRHardScanReader
            from ocr_hardscan_io import recover_hardscan
            report = recover_hardscan(pdf_path, recovery_path, plan_path, report_path, dpi=policy.dpi,
                                      reader_factory=recorder.reader_factory(RapidOCRHardScanReader))
        elapsed = time.monotonic() - started
        _, report_digest = _read_snapshot(report_path, label="OCR execution report", max_bytes=_report_limit(operation))
        receipt = capture_execution_receipt(operation=operation, inputs=inputs, output_sha256=report_digest,
                                            observation=recorder.observation, calls=recorder.calls,
                                            elapsed_seconds=elapsed, installation_path=installation_path)

        def commit_receipt(temporary, destination):
            recheck()
            if _read_snapshot(report_path, label="OCR execution report", max_bytes=_report_limit(operation))[1] != report_digest:
                raise RuntimeError("OCR execution report changed")
            ocr_recovery._publish_new_report(temporary, destination)

        storage_policy.atomic_write_private_json(receipt_path, receipt, indent=2, replace_fn=commit_receipt)
        receipt_digest = _read_snapshot(receipt_path, label="OCR execution receipt", max_bytes=4 * 1024 * 1024)[1]
        manifest = {"schema_version": 1, "kind": "ocr_execution_bundle", "operation": operation,
                    "inputs": inputs, "configuration": configuration, "report_sha256": report_digest,
                    "execution_sha256": receipt_digest, "requires_attention": True,
                    "scope": _BUNDLE_SCOPE}

        def commit_manifest(temporary, destination):
            recheck()
            for path, digest, limit in ((report_path, report_digest, _report_limit(operation)), (receipt_path, receipt_digest, 4 * 1024 * 1024)):
                if _read_snapshot(path, label="OCR execution result", max_bytes=limit)[1] != digest:
                    raise RuntimeError("OCR execution result changed")
            ocr_recovery._publish_new_report(temporary, destination)

        storage_policy.atomic_write_private_json(output_dir / "manifest.json", manifest, indent=2, replace_fn=commit_manifest)
    return {"manifest": manifest, "receipt": receipt, "report": report}
