"""Private same-call diagnostic bundles; legacy reports and receipts stay unchanged.

The normal orchestrator writes an explicitly incomplete work report. Only its
strictly validated, unchanged bytes enter the final bundle. Every final commit
rechecks the captured inputs, producer generation and earlier artifacts. A
manifest is published last; exceptions never trigger another OCR invocation.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import stat
import sys
import time
from types import MappingProxyType

from evaluation_inputs import _strict_json_bytes
from ocr_checkpoint_runtime import capture_identity
from ocr_detection_disposition import (
    DispositionDerivation, LIMITS, RECIPE_ID, build_disposition, validate_disposition,
)
from ocr_disposition_geometry import map_source_polygon, replay_engine_box
from ocr_execution_receipt import (
    ExecutionRecorder, capture_execution_receipt, validate_execution_receipt,
)
import ocr_recovery
from ocr_recovery_comparison import validate_recovery_report
from ocr_stage_io import _snapshot
from resource_lease import PathLease
import storage_policy


ROOT = Path(__file__).resolve().parent
_SHA = re.compile(r"[0-9a-f]{64}")
_SCOPE = "Local same-call lineage, not authenticated execution, complete-source verification, accuracy or correction approval."
_DERIVATION = DispositionDerivation(replay_engine_box, map_source_polygon)
_ARTIFACT_LIMITS = MappingProxyType({
    "execution.json": 4 * 1024 * 1024,
    "disposition.json": LIMITS["max_serialized_bytes"],
    "manifest.json": 512 * 1024,
})
_UPSTREAM_PINS = MappingProxyType({
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


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def _digest(value):
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _encoded(value, limit):
    """Bound UTF-8 serialization before joining fragments; include the final LF."""
    chunks, size = [], 1
    encoder = json.JSONEncoder(ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
    for fragment in encoder.iterencode(value):
        block = fragment.encode("utf-8")
        size += len(block)
        if size > limit:
            raise ValueError("disposition artifact exceeds its serialized budget")
        chunks.append(block)
    return b"".join(chunks) + b"\n"


def _json(path, limit):
    raw, digest = _snapshot(path, limit, retain=True)
    try:
        value = _strict_json_bytes(raw, label="disposition artifact", max_bytes=limit)
        # JSON accepts escaped lone surrogates; the output contract does not.
        _encoded(value, limit)
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError("invalid disposition JSON artifact") from None
    return value, raw, digest


def _report_limit(operation):
    return (64 if operation == "pages" else 128) * 1024 * 1024


def _file_identity(path):
    storage_policy.assert_no_link_components(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("disposition artifact must remain a single-linked regular file")
    value = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
    return value if os.name == "nt" else value + (info.st_ctime_ns,)


def _bound_snapshot(path, limit):
    identity = _file_identity(path)
    digest = _snapshot(path, limit)[1]
    if _file_identity(path) != identity:
        raise RuntimeError("disposition artifact identity changed during capture")
    return digest, identity


def _admit_entrypoint(*, preparing=False):
    entrypoint = getattr(sys.modules.get("__main__"), "__file__", None)
    if isinstance(entrypoint, str):
        # Path.absolute() retains '..'; normalize lexical aliases before host
        # classification so a project launcher cannot look like an external host.
        # This routing check does not authenticate a hostile Python process.
        path = Path(os.path.abspath(entrypoint))
        allowed = path == ROOT / "tools" / "diagnose_ocr_dispositions.py"
        # The trusted review host may prepare the read-only parent request;
        # actual OCR still belongs to the contained diagnostic CLI worker.
        allowed = allowed or (preparing is True and path == ROOT / "tools" / "review_ocr.py")
        if path.parent in (ROOT, ROOT / "tools") and not allowed:
            raise ValueError("disposition execution requires its fixed CLI or an external API host")


def capture_producer_generation(*, installation_path=None):
    """Inspect fixed installed sources and local identity without loading models."""
    distribution = importlib.metadata.distribution("rapidocr")
    if distribution.version != "3.9.2":
        raise ValueError("disposition observation recipe requires its characterized engine version")
    upstream = {}
    for name, expected in _UPSTREAM_PINS.items():
        path = Path(distribution.locate_file(name)).absolute()
        observed = _snapshot(path, 4 * 1024 * 1024)[1]
        if observed != expected:
            raise ValueError("disposition upstream source differs from the characterized recipe")
        upstream[name] = observed
    return {"schema_version": 1, "kind": "ocr_disposition_producer",
            "identity": capture_identity(installation_path=installation_path), "upstream": upstream}


@dataclass(frozen=True)
class DispositionRequest:
    pdf_path: Path
    output_dir: Path
    policy: ocr_recovery.RetryPolicy
    evidence_path: Path | None
    recovery_path: Path | None
    plan_path: Path | None
    installation_path: Path | None
    specs: tuple
    input_identities: MappingProxyType
    input_identity_sha256: str
    payload: dict
    generation: dict
    plan: dict | None
    sha256: str


def _options(operation, policy, requested_pages, evidence_path, recovery_path, plan_path):
    if type(operation) is not str or operation not in {"pages", "regions", "hardscan"}:
        raise ValueError("unknown disposition operation")
    policy = ocr_recovery.RetryPolicy() if policy is None else policy
    if type(policy) is not ocr_recovery.RetryPolicy:
        raise ValueError("invalid disposition retry policy")
    # Revalidate even a frozen dataclass constructed through unusual Python APIs.
    policy = ocr_recovery.RetryPolicy(**asdict(policy))
    if (type(requested_pages) is not tuple or len(requested_pages) > 20
            or any(type(page) is not int or not 1 <= page <= 5000 for page in requested_pages)
            or len(set(requested_pages)) != len(requested_pages)):
        raise ValueError("invalid disposition requested pages")
    if operation == "pages":
        if recovery_path is not None or plan_path is not None:
            raise ValueError("page dispositions do not accept crop plans")
    elif (recovery_path is None or plan_path is None or evidence_path is not None or requested_pages
          or policy != ocr_recovery.RetryPolicy(dpi=policy.dpi) or policy.dpi not in (300, 400)):
        raise ValueError("crop dispositions require a plan, saved recovery and supported DPI only")
    return policy


def _validated_plan(operation, paths, inputs):
    if operation == "pages":
        if paths["evidence"] is not None:
            evidence = ocr_recovery.validate_evidence(_json(paths["evidence"], ocr_recovery.MAX_EVIDENCE_BYTES)[0])
            if evidence["source_sha256"] != inputs["source_sha256"]:
                raise ValueError("disposition evidence differs from source")
        return None
    recovery, _, recovery_digest = _json(paths["recovery"], 64 * 1024 * 1024)
    recovery = validate_recovery_report(recovery)
    plan, _, plan_digest = _json(paths["plan"], 1024 * 1024)
    if (recovery_digest != inputs["recovery_sha256"] or plan_digest != inputs["plan_sha256"]
            or recovery["source_sha256"] != inputs["source_sha256"]):
        raise ValueError("disposition plan or recovery differs from captured inputs")
    if operation == "regions":
        from ocr_regions import validate_region_plan as validate_plan
    else:
        from ocr_hardscan import validate_plan
    return validate_plan(plan, source_sha256=inputs["source_sha256"],
                         recovery_sha256=recovery_digest, page_count=recovery["page_count"])


def _input_specs(pdf, paths):
    specs = [(pdf, "source_sha256", 256 * 1024 * 1024)]
    for name, key in (("requirements-full.lock", "dependency_full_sha256"),
                      ("requirements-test.lock", "dependency_test_sha256"),
                      ("requirements-lock-tools.lock", "dependency_tools_sha256"),
                      ("model-artifact-policy.json", "model_policy_sha256"),
                      ("model-artifacts.lock.json", "model_lock_sha256")):
        specs.append((ROOT / name, key, 8 * 1024 * 1024))
    for key, limit in (("evidence", ocr_recovery.MAX_EVIDENCE_BYTES), ("recovery", 64 * 1024 * 1024),
                       ("plan", 1024 * 1024), ("installation", 64 * 1024)):
        if paths[key] is not None:
            specs.append((paths[key], key + "_sha256", limit))
    return tuple(specs)


def disposition_request(pdf_path, output_dir, *, operation="pages", policy=None, requested_pages=(),
                        evidence_path=None, recovery_path=None, plan_path=None, installation_path=None,
                        readback=False):
    """Read-only admission with streaming snapshots and exact producer bindings."""
    if type(readback) is not bool:
        raise ValueError("invalid disposition readback mode")
    if not readback:
        _admit_entrypoint(preparing=True)
    policy = _options(operation, policy, requested_pages, evidence_path, recovery_path, plan_path)
    pdf, output = Path(pdf_path).absolute(), Path(output_dir).absolute()
    paths = {key: None if path is None else Path(path).absolute() for key, path in
             (("evidence", evidence_path), ("recovery", recovery_path), ("plan", plan_path),
              ("installation", installation_path))}
    storage_policy.assert_no_link_components(output)
    if not output.parent.is_dir() or (not output.is_dir() if readback else output.exists()):
        raise ValueError("choose a new disposition directory under an existing parent")
    specs = _input_specs(pdf, paths)
    for index, (path, _, _) in enumerate(specs):
        storage_policy.assert_no_link_components(path)
        if any(os.path.samefile(path, previous[0]) for previous in specs[:index]):
            raise ValueError("disposition inputs must be distinct single-linked files")
        if output == path or output in path.parents:
            raise ValueError("disposition inputs cannot be inside the output bundle")
    inputs, identities = {}, {}
    for path, key, limit in specs:
        inputs[key], identities[key] = _bound_snapshot(path, limit)
    configuration = {"operation": operation, "policy": asdict(policy) if operation == "pages" else {"dpi": policy.dpi},
                     "requested_pages": list(requested_pages)}
    inputs["configuration_sha256"] = _digest(configuration)
    plan = _validated_plan(operation, paths, inputs)
    generation = capture_producer_generation(installation_path=paths["installation"])
    payload = {"schema_version": 1, "kind": "ocr_disposition_request", "operation": operation,
               "configuration": configuration, "inputs": inputs, "recipe_id": RECIPE_ID,
               "limits": dict(LIMITS), "producer_generation_sha256": _digest(generation)}
    request = DispositionRequest(pdf, output, policy, paths["evidence"], paths["recovery"], paths["plan"],
                                 paths["installation"], tuple(specs), MappingProxyType(dict(identities)), _digest(identities),
                                 payload, generation, plan, _digest(payload))
    recheck_disposition_request(request)
    return request


def recheck_disposition_request(request):
    if type(request) is not DispositionRequest or _digest(request.payload) != request.sha256:
        raise ValueError("disposition request changed")
    payload = request.payload
    if (type(payload) is not dict or set(payload) != {"schema_version", "kind", "operation", "configuration",
            "inputs", "recipe_id", "limits", "producer_generation_sha256"}
            or type(payload["schema_version"]) is not int or payload["schema_version"] != 1
            or payload["kind"] != "ocr_disposition_request" or payload["recipe_id"] != RECIPE_ID
            or _canonical(payload["limits"]) != _canonical(dict(LIMITS))):
        raise ValueError("disposition request envelope differs")
    paths = {key: getattr(request, key + "_path") for key in ("evidence", "recovery", "plan", "installation")}
    for path in (request.pdf_path, request.output_dir, *paths.values()):
        if path is not None and (not isinstance(path, Path) or not path.is_absolute()):
            raise ValueError("disposition request requires absolute paths")
    expected_specs = _input_specs(request.pdf_path, paths)
    if (type(request.specs) is not tuple or request.specs != expected_specs
            or type(payload["inputs"]) is not dict
            or set(payload["inputs"]) != {key for _, key, _ in expected_specs} | {"configuration_sha256"}
            or type(request.input_identities) is not MappingProxyType
            or set(request.input_identities) != {key for _, key, _ in expected_specs}
            or _digest(dict(request.input_identities)) != request.input_identity_sha256):
        raise ValueError("disposition input inventory differs")
    configuration = payload["configuration"]
    if (type(configuration) is not dict or set(configuration) != {"operation", "policy", "requested_pages"}
            or type(configuration["requested_pages"]) is not list):
        raise ValueError("disposition configuration differs")
    checked_policy = _options(payload["operation"], request.policy, tuple(configuration["requested_pages"]),
                              request.evidence_path, request.recovery_path, request.plan_path)
    expected_configuration = {"operation": payload["operation"], "requested_pages": configuration["requested_pages"],
                              "policy": asdict(checked_policy) if payload["operation"] == "pages" else {"dpi": checked_policy.dpi}}
    if _canonical(configuration) != _canonical(expected_configuration):
        raise ValueError("disposition request policy differs")
    if (_digest(request.generation) != request.payload["producer_generation_sha256"]
            or _digest(request.payload["configuration"]) != request.payload["inputs"]["configuration_sha256"]):
        raise ValueError("disposition request bindings changed")
    for index, (path, key, limit) in enumerate(expected_specs):
        if (request.output_dir == path or request.output_dir in path.parents
                or any(os.path.samefile(path, previous[0]) for previous in expected_specs[:index])):
            raise ValueError("disposition inputs must remain distinct from outputs")
        digest, identity = _bound_snapshot(path, limit)
        if digest != request.payload["inputs"][key] or identity != request.input_identities[key]:
            raise RuntimeError("disposition input changed")
    current_plan = _validated_plan(request.payload["operation"],
                                   {"evidence": request.evidence_path, "recovery": request.recovery_path,
                                    "plan": request.plan_path}, request.payload["inputs"])
    if _canonical(current_plan) != _canonical(request.plan):
        raise RuntimeError("disposition plan changed")
    if _canonical(capture_producer_generation(installation_path=request.installation_path)) != _canonical(request.generation):
        raise RuntimeError("disposition producer generation changed")


def _validated_report(value, request):
    operation = request.payload["operation"]
    configuration, inputs = request.payload["configuration"], request.payload["inputs"]
    if operation == "pages":
        report = validate_recovery_report(value)
        policy = configuration["policy"]
        selection = {"requested_pages": sorted(configuration["requested_pages"]), "max_pages": policy["max_pages"],
                     "min_chars": policy["min_chars"], "order": "requested_then_page_number"}
        retry = {name: policy[name] for name in ("dpi", "max_pixels", "max_side")}
        retry["attempts_per_page"] = 1
        if policy["preprocessing"] != "none":
            retry["preprocessing"] = policy["preprocessing"]
        if (_canonical(report["selection"]) != _canonical(selection)
                or _canonical(report["retry_configuration"]) != _canonical(retry)
                or report["evidence_sha256"] != inputs.get("evidence_sha256")):
            raise ValueError("disposition report differs from requested page policy")
    else:
        if operation == "regions":
            from ocr_regions import verify_region_review_request as verify
        else:
            from ocr_hardscan_io import verify_request as verify
        report = verify(value, pdf_path=request.pdf_path, recovery_path=request.recovery_path,
                        plan_path=request.plan_path, dpi=request.policy.dpi)
    if report["source_sha256"] != inputs["source_sha256"]:
        raise ValueError("disposition report differs from requested source")
    return report


def _join_execution(receipt, request, report_digest):
    receipt = validate_execution_receipt(receipt, installation_path=request.installation_path)
    if (receipt["operation"] != request.payload["operation"]
            or _canonical(receipt["inputs"]) != _canonical(request.payload["inputs"])
            or receipt["output_sha256"] != report_digest
            or receipt["installation_sha256"] != request.payload["inputs"].get("installation_sha256")
            or receipt["environment_identity_sha256"] != request.generation["identity"]["environment_sha256"]):
        raise ValueError("disposition execution differs from requested bindings")
    for name, digest in receipt["project_sources_at_observation"].items():
        path = "tools/diagnose_ocr_dispositions.py" if name == "entrypoint" else name.replace(".", "/") + ".py"
        if request.generation["identity"]["producer_sources"].get(path) != digest:
            raise ValueError("disposition execution source differs from producer generation")
    return receipt


def _directory_identity(path):
    storage_policy.assert_no_link_components(path)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError("disposition directory is unavailable")
    return info.st_dev, info.st_ino


class _Publisher:
    def __init__(self, request):
        self.request = request
        self.directory = _directory_identity(request.output_dir)
        self.work_directory = _directory_identity(request.output_dir / "work")
        self.published = {}

    def recheck(self):
        if (_directory_identity(self.request.output_dir) != self.directory
                or _directory_identity(self.request.output_dir / "work") != self.work_directory):
            raise RuntimeError("disposition output directory changed")
        recheck_disposition_request(self.request)
        for name, (digest, limit, identity) in self.published.items():
            current, current_identity = _bound_snapshot(self.request.output_dir / name, limit)
            if current != digest or current_identity != identity:
                raise RuntimeError("disposition artifact changed before completion")

    def register(self, name, digest, limit):
        current, identity = _bound_snapshot(self.request.output_dir / name, limit)
        if current != digest:
            raise RuntimeError("disposition artifact changed before registration")
        self.published[name] = current, limit, identity

    def publish(self, name, raw, limit):
        if type(raw) is not bytes or not 1 <= len(raw) <= limit:
            raise ValueError("disposition serialized artifact exceeds bounds")
        intended = hashlib.sha256(raw).hexdigest()

        def commit(temporary, destination):
            self.recheck()
            if _snapshot(temporary, limit)[1] != intended:
                raise RuntimeError("staged disposition artifact differs from intended bytes")
            ocr_recovery._publish_new_report(temporary, destination)

        storage_policy.atomic_write_private(self.request.output_dir / name, lambda handle: handle.write(raw),
                                            text=False, replace_fn=commit)
        self.register(name, intended, limit)
        return intended

    def publish_json(self, name, value):
        limit = _ARTIFACT_LIMITS[name]
        return self.publish(name, _encoded(value, limit), limit)


def _run_operation(request, execution, disposition, report_path):
    operation = request.payload["operation"]
    if operation == "pages":
        from ocr_recovery_runtime import RapidOCRPageReader as Reader
        factory = execution.reader_factory(disposition.reader_factory(Reader))
        return ocr_recovery.recover_pdf(request.pdf_path, report_path, policy=request.policy,
                                        requested_pages=tuple(request.payload["configuration"]["requested_pages"]),
                                        evidence_path=request.evidence_path, reader_factory=factory)
    if operation == "regions":
        from ocr_region_runtime import RapidOCRRegionReader as Reader
        from ocr_regions import recover_regions as recover
    else:
        from ocr_hardscan_runtime import RapidOCRHardScanReader as Reader
        from ocr_hardscan_io import recover_hardscan as recover
    factory = execution.reader_factory(disposition.reader_factory(Reader))
    return recover(request.pdf_path, request.recovery_path, request.plan_path, report_path,
                   dpi=request.policy.dpi, reader_factory=factory)


def _bindings(request, report_digest, execution_digest):
    return {"request_sha256": request.sha256, "report_sha256": report_digest,
            "execution_sha256": execution_digest,
            "producer_generation_sha256": request.payload["producer_generation_sha256"],
            "source_sha256": request.payload["inputs"]["source_sha256"]}


def run_disposition_bundle(pdf_path, output_dir, *, expected_request_sha256=None, **options):
    """Run once; retain private incomplete artifacts on failure or cancellation."""
    if "readback" in options:
        raise ValueError("disposition execution never reuses a readback directory")
    _admit_entrypoint()
    from ocr_detection_disposition import reserve_envelope
    from ocr_detection_disposition_runtime import DispositionRecorder

    request = disposition_request(pdf_path, output_dir, **options)
    if expected_request_sha256 is not None and (
            type(expected_request_sha256) is not str or _SHA.fullmatch(expected_request_sha256) is None
            or request.sha256 != expected_request_sha256):
        raise ValueError("disposition worker request differs from the parent snapshot")
    operation = request.payload["operation"]
    reserve_envelope(operation, item_count=5000 if request.plan is None else len(request.plan["regions"]))
    disposition = DispositionRecorder(operation, request.sha256, plan=request.plan)
    execution = ExecutionRecorder(disposition_binding=disposition.dispatch_binding)
    request.output_dir.mkdir(exist_ok=False)
    storage_policy.enforce_private_path(request.output_dir, directory=True)
    work = request.output_dir / "work"
    work.mkdir(exist_ok=False)
    storage_policy.enforce_private_path(work, directory=True)
    publisher = _Publisher(request)
    with PathLease(request.output_dir, backend="ocr-disposition", collection_name="bundle", operation="execute",
                   timeout=0, resource_description="OCR detection disposition bundle"):
        publisher.recheck()
        started = time.monotonic()
        _run_operation(request, execution, disposition, work / "report.json")
        elapsed = time.monotonic() - started
        value, raw, report_digest = _json(work / "report.json", _report_limit(operation))
        report = _validated_report(value, request)
        publisher.register("work/report.json", report_digest, _report_limit(operation))
        publisher.publish("report.json", raw, _report_limit(operation))
        receipt = capture_execution_receipt(operation=operation, inputs=request.payload["inputs"],
                                            output_sha256=report_digest, observation=execution.observation,
                                            calls=execution.calls, elapsed_seconds=elapsed,
                                            installation_path=request.installation_path)
        receipt = _join_execution(receipt, request, report_digest)
        receipt_digest = publisher.publish_json("execution.json", receipt)
        bindings = _bindings(request, report_digest, receipt_digest)
        diagnostic = build_disposition(disposition.observations(), request=request.payload, report=report,
                                        execution=receipt, bindings=bindings, derivation=_DERIVATION)
        diagnostic = validate_disposition(diagnostic, request=request.payload, report=report,
                                           execution=receipt, bindings=bindings, derivation=_DERIVATION)
        diagnostic_digest = publisher.publish_json("disposition.json", diagnostic)
        manifest = {"schema_version": 1, "kind": "ocr_disposition_bundle", "request": request.payload,
                    "producer_generation": request.generation, "report_sha256": report_digest,
                    "execution_sha256": receipt_digest, "disposition_sha256": diagnostic_digest,
                    "requires_attention": True, "canonical_extraction_modified": False,
                    "accuracy_verified": False, "scope": _SCOPE}
        publisher.publish_json("manifest.json", manifest)
        publisher.recheck()
    return {"manifest": manifest, "report": report, "execution": receipt, "disposition": diagnostic}


def read_disposition_completion(output_dir, *, request):
    """Fresh readback verifies every artifact against an independent request."""
    output = Path(output_dir).absolute()
    if type(request) is not DispositionRequest or output != request.output_dir:
        raise ValueError("disposition completion differs from requested directory")
    publisher = _Publisher(request)
    publisher.recheck()
    manifest, _, manifest_digest = _json(output / "manifest.json", _ARTIFACT_LIMITS["manifest.json"])
    keys = {"schema_version", "kind", "request", "producer_generation", "report_sha256", "execution_sha256",
            "disposition_sha256", "requires_attention", "canonical_extraction_modified", "accuracy_verified", "scope"}
    if (type(manifest) is not dict or set(manifest) != keys or type(manifest["schema_version"]) is not int
            or manifest["schema_version"] != 1 or manifest["kind"] != "ocr_disposition_bundle"
            or manifest["requires_attention"] is not True or manifest["canonical_extraction_modified"] is not False
            or manifest["accuracy_verified"] is not False or manifest["scope"] != _SCOPE
            or _canonical(manifest["request"]) != _canonical(request.payload)
            or _canonical(manifest["producer_generation"]) != _canonical(request.generation)):
        raise ValueError("disposition completion differs from requested bindings")
    payloads = {}
    publisher.register("manifest.json", manifest_digest, _ARTIFACT_LIMITS["manifest.json"])
    for name in ("report", "execution", "disposition"):
        filename = name + ".json"
        limit = _report_limit(request.payload["operation"]) if name == "report" else _ARTIFACT_LIMITS[filename]
        value, _, digest = _json(output / filename, limit)
        if type(manifest[name + "_sha256"]) is not str or digest != manifest[name + "_sha256"]:
            raise ValueError("disposition artifact differs from completion")
        payloads[name] = value
        publisher.register(filename, digest, limit)
    publisher.register("work/report.json", manifest["report_sha256"], _report_limit(request.payload["operation"]))
    report = _validated_report(payloads["report"], request)
    receipt = _join_execution(payloads["execution"], request, manifest["report_sha256"])
    diagnostic = validate_disposition(payloads["disposition"], request=request.payload, report=report,
                                       execution=receipt, derivation=_DERIVATION,
                                       bindings=_bindings(request, manifest["report_sha256"], manifest["execution_sha256"]))
    publisher.recheck()
    return {"manifest": manifest, "report": report, "execution": receipt, "disposition": diagnostic}
