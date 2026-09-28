"""Leased, create-only page execution journals with actual interruption/resume.

The whole worker owns one lease. A later lease holder may seal an unfinished
start as interrupted, never execute that page again in the same generation.
"""

from __future__ import annotations

from dataclasses import asdict
import os
from pathlib import Path
import re
import stat
import time

import artifact_io
from evaluation_inputs import _strict_json_bytes
import ocr_checkpoint as core
from ocr_checkpoint_runtime import bounded_snapshot as _snapshot, capture_identity
from ocr_execution_receipt import ExecutionRecorder, capture_execution_receipt, validate_execution_receipt
import ocr_recovery
from resource_lease import PathLease
import storage_policy


ROOT = Path(__file__).resolve().parent
_RECORD = re.compile(r"(?:request|plan|report|manifest|segment-\d{4}\.(?:start|end)|page-\d{5}\.(?:start|result))\.json")
_STAGING = re.compile(r"\.(?:request|plan|report|manifest|segment-\d{4}\.(?:start|end)|page-\d{5}\.(?:start|result))\.json\.[a-z0-9_]{6,32}\.tmp")
_REGION_RECORD = re.compile(r"(?:request|plan|report|manifest|segment-\d{4}\.(?:start|end)|region-(?:000[1-9]|001[0-9]|0020)\.(?:start|result))\.json")
_REGION_STAGING = re.compile(r"\.(?:request|plan|report|manifest|segment-\d{4}\.(?:start|end)|region-(?:000[1-9]|001[0-9]|0020)\.(?:start|result))\.json\.[a-z0-9_]{6,32}\.tmp")


def checkpoint_request(pdf_path: Path, output_dir: Path, *, operation: str = "pages",
                       policy: ocr_recovery.RetryPolicy | None = None, requested_pages: tuple[int, ...] = (),
                       evidence_path: Path | None = None, installation_path: Path | None = None,
                       recovery_path: Path | None = None, plan_path: Path | None = None,
                       resume: bool = False) -> tuple:
    """Read-only exact generation preflight; supported only for page execution."""
    if operation != "pages" or recovery_path is not None or plan_path is not None:
        raise ValueError("checkpoints currently support page operations only")
    if type(resume) is not bool:
        raise ValueError("checkpoint resume flag must be boolean")
    policy = ocr_recovery.RetryPolicy() if policy is None else policy
    if not isinstance(policy, ocr_recovery.RetryPolicy):
        raise ValueError("invalid checkpoint retry policy")
    if (not isinstance(requested_pages, tuple) or len(requested_pages) > 20
            or any(type(number) is not int or not 1 <= number <= 5000 for number in requested_pages)
            or len(set(requested_pages)) != len(requested_pages)):
        raise ValueError("invalid checkpoint requested pages")
    source, output = Path(pdf_path).absolute(), Path(output_dir).absolute()
    specs = [(source, "source_sha256", 256 * 1024 * 1024)]
    for name, key in (("requirements-full.lock", "dependency_full_sha256"),
                       ("requirements-test.lock", "dependency_test_sha256"),
                       ("requirements-lock-tools.lock", "dependency_tools_sha256"),
                       ("model-artifact-policy.json", "model_policy_sha256"),
                       ("model-artifacts.lock.json", "model_lock_sha256")):
        specs.append((ROOT / name, key, 8 * 1024 * 1024))
    for path, key, limit in ((evidence_path, "evidence_sha256", ocr_recovery.MAX_EVIDENCE_BYTES),
                             (installation_path, "installation_sha256", 64 * 1024)):
        if path is not None:
            specs.append((Path(path).absolute(), key, limit))
    storage_policy.assert_no_link_components(output)
    if not output.parent.is_dir() or (resume and not output.is_dir()) or (not resume and output.exists()):
        raise ValueError("checkpoint requires a new directory or an explicit existing resume directory")
    inputs = {}
    for index, (path, key, limit) in enumerate(specs):
        if path.is_relative_to(output) or any(os.path.samefile(path, previous[0]) for previous in specs[:index]):
            raise ValueError("checkpoint inputs must be distinct and outside the generation")
        inputs[key] = _snapshot(path, limit)[1]
    configuration = {"operation": "pages", "policy": asdict(policy), "requested_pages": sorted(requested_pages)}
    identity = capture_identity(installation_path=installation_path)
    inputs.update(configuration_sha256=core.digest(configuration), identity_sha256=core.digest(identity))
    request = core.validate_request({"schema_version": 1, "kind": "ocr_checkpoint_request", "operation": "pages",
                                     "inputs": inputs, "configuration": configuration, "identity": identity})
    if resume:
        raw, _ = _snapshot(output / "request.json", 128 * 1024)
        previous = core.validate_request(_json(raw))
        if not core.same(previous, request):
            raise ValueError("checkpoint generation differs from current inputs or runtime")
    return source, output, policy, specs, request


def _json(raw: bytes, *, limit: int = core.MAX_REPORT_BYTES) -> dict:
    try:
        return _strict_json_bytes(raw, label="checkpoint artifact", max_bytes=limit)
    except (ValueError, TypeError, RecursionError):
        raise ValueError("checkpoint artifact is invalid JSON") from None


def _limit(name: str, *, profile: str = "pages") -> int:
    if profile in ("regions", "hardscan"):
        return (128 * 1024 * 1024 if name == "report.json" else 1024 * 1024 if name == "plan.json"
                else 8 * 1024 * 1024 if name.endswith(".result.json") else 128 * 1024)
    if profile != "pages":
        raise ValueError("unsupported checkpoint storage profile")
    return (core.MAX_REPORT_BYTES if name == "report.json" else core.MAX_PLAN_BYTES if name == "plan.json"
            else core.MAX_PAGE_BYTES if name.endswith(".result.json") else 128 * 1024)


class _Journal:
    def __init__(self, directory: Path, specs: list, request: dict, installation_path: Path | None,
                 *, profile: str = "pages"):
        if profile not in {"pages", "regions", "hardscan"}:
            raise ValueError("unsupported checkpoint storage profile")
        self.profile = profile
        self.record_pattern = _RECORD if profile == "pages" else _REGION_RECORD
        self.staging_pattern = _STAGING if profile == "pages" else _REGION_STAGING
        self.directory, self.specs, self.request = directory, specs, request
        self.installation_path = installation_path
        info = directory.stat()
        self.identity = (info.st_dev, info.st_ino)
        self.pins = {}

    def scan(self) -> set[str]:
        storage_policy.assert_no_link_components(self.directory)
        info = self.directory.stat()
        if (info.st_dev, info.st_ino) != self.identity:
            raise RuntimeError("checkpoint directory changed")
        names, total = set(), 0
        for index, path in enumerate(self.directory.iterdir()):
            if index >= core.MAX_GENERATION_FILES:
                raise ValueError("checkpoint generation contains too many files")
            storage_policy.assert_no_link_components(path)
            info = path.lstat()
            if path.name == ".rag-locks" and stat.S_ISDIR(info.st_mode):
                continue
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or not (self.record_pattern.fullmatch(path.name) or self.staging_pattern.fullmatch(path.name))):
                raise ValueError("checkpoint contains an unsupported artifact")
            total += info.st_size
            if total > core.MAX_GENERATION_BYTES:
                raise ValueError("checkpoint generation exceeds its byte budget")
            if self.record_pattern.fullmatch(path.name):
                names.add(path.name)
        return names

    def read(self, name: str) -> tuple[dict, str]:
        raw, digest = _snapshot(self.directory / name, _limit(name, profile=self.profile))
        if name in self.pins and self.pins[name] != digest:
            raise RuntimeError("checkpoint committed artifact changed")
        self.pins[name] = digest
        return _json(raw, limit=_limit(name, profile=self.profile)), digest

    def recheck(self) -> None:
        self.scan()
        for path, key, limit in self.specs:
            if _snapshot(path, limit)[1] != self.request["inputs"][key]:
                raise RuntimeError("checkpoint execution input changed")
        if not core.same(capture_identity(installation_path=self.installation_path), self.request["identity"]):
            raise RuntimeError("checkpoint runtime or producer generation changed")
        for name, digest in self.pins.items():
            if _snapshot(self.directory / name, _limit(name, profile=self.profile))[1] != digest:
                raise RuntimeError("checkpoint committed artifact changed")

    def put(self, name: str, value: dict, *, adopt: bool = False) -> str:
        raw = core.json_bytes(value, limit=_limit(name, profile=self.profile))
        if adopt and (self.directory / name).exists():
            current, digest = _snapshot(self.directory / name, _limit(name, profile=self.profile))
            if current != raw:
                raise ValueError("checkpoint existing final artifact differs")
            self.pins[name] = digest
            self.recheck()
            return digest

        def commit(temporary, destination):
            self.recheck()
            if _snapshot(temporary, _limit(name, profile=self.profile))[0] != raw:
                raise RuntimeError("checkpoint staging artifact changed")
            ocr_recovery._publish_new_report(temporary, destination)

        self.recheck()
        storage_policy.atomic_write_private_json(self.directory / name, value, indent=2, replace_fn=commit)
        actual, digest = _snapshot(self.directory / name, _limit(name, profile=self.profile))
        if actual != raw:
            raise RuntimeError("checkpoint published artifact changed")
        self.pins[name] = digest
        return digest


def _read_segments(journal: _Journal, names: set[str], request_digest: str) -> tuple[dict, set[str]]:
    """Read common segment envelopes; adapters own their execution chronology."""
    segments, used = {}, set()
    for number in range(1, core.MAX_SEGMENTS + 1):
        name = f"segment-{number:04d}.start.json"
        if name not in names:
            break
        value, digest = journal.read(name)
        core.validate_segment(value, request_sha256=request_digest, number=number, finished=False)
        entry = {"start_sha256": digest, "status": "open", "end_sha256": None}
        used.add(name)
        end_name = f"segment-{number:04d}.end.json"
        if end_name in names:
            end, end_digest = journal.read(end_name)
            core.validate_segment(end, request_sha256=request_digest, number=number, finished=True)
            entry.update(status=end["status"], end_sha256=end_digest)
            used.add(end_name)
        segments[number] = entry
    return segments, used


def _state(journal: _Journal) -> dict:
    names = journal.scan()
    request, request_digest = journal.read("request.json")
    if not core.same(core.validate_request(request), journal.request):
        raise ValueError("checkpoint request changed")
    segments, segment_names = _read_segments(journal, names, request_digest)
    starts, results, used = {}, {}, {"request.json"} | segment_names
    plan, plan_digest = None, None
    if "plan.json" in names:
        plan, plan_digest = journal.read("plan.json")
        core.validate_plan(plan, request, request_digest)
        used.add("plan.json")
        missing_start, pending, prior_origin = False, False, 0
        for baseline in plan["selected_pages"]:
            number = baseline["page_number"]
            name = f"page-{number:05d}.start.json"
            if name not in names:
                missing_start = True
                continue
            if missing_start or pending:
                raise ValueError("checkpoint starts contradict sequential commit order")
            start, digest = journal.read(name)
            core.validate_start(start, request_sha256=request_digest, plan_sha256=plan_digest,
                                page_number=number, segments=segments)
            if start["origin_segment"] < prior_origin:
                raise ValueError("checkpoint origin segments contradict page order")
            prior_origin = start["origin_segment"]
            starts[number] = (start, digest)
            used.add(name)
            result_name = f"page-{number:05d}.result.json"
            if result_name in names:
                result, result_digest = journal.read(result_name)
                core.validate_result(result, request=request, plan=plan, plan_sha256=plan_digest,
                                     baseline=baseline, start=start, start_sha256=digest, segments=segments)
                if result["receipt"] is not None:
                    validate_execution_receipt(result["receipt"], installation_path=journal.installation_path)
                prior_origin = max(prior_origin, result["sealed_by_segment"])
                results[number] = (result, result_digest)
                used.add(result_name)
            else:
                pending = True
                if segments[start["origin_segment"]]["status"] == "finished":
                    raise ValueError("finished checkpoint segment has an unresolved attempt")
    if names - used - {"report.json", "manifest.json"}:
        raise ValueError("checkpoint contains out-of-sequence records")
    finished = [number for number, segment in segments.items() if segment["status"] == "finished"]
    if finished and (plan is None or len(results) != len(plan["selected_pages"])
                     or any(start["origin_segment"] > min(finished) for start, _ in starts.values())
                     or any(result["sealed_by_segment"] > min(finished) for result, _ in results.values())):
        raise ValueError("checkpoint work contradicts an earlier finished segment")
    return {"request_sha256": request_digest, "plan": plan, "plan_sha256": plan_digest,
            "segments": segments, "starts": starts, "results": results}


def _completion(journal: _Journal, state: dict) -> tuple[dict, dict]:
    plan, results = state["plan"], state["results"]
    if (plan is None or len(results) != len(plan["selected_pages"])
            or not state["segments"] or any(value["status"] == "open" for value in state["segments"].values())):
        raise ValueError("checkpoint generation is incomplete")
    report = core.assemble_report(journal.request, plan, [results[p["page_number"]][0]["page"] for p in plan["selected_pages"]])
    pages, segments = [], []
    for baseline in plan["selected_pages"]:
        number = baseline["page_number"]
        result, result_digest = results[number]
        pages.append({"page_number": number, "state": result["state"], "origin_segment": result["origin_segment"],
                      "start_sha256": state["starts"][number][1], "result_sha256": result_digest})
    for number, value in state["segments"].items():
        origin = [result for result, _ in results.values() if result["origin_segment"] == number]
        segments.append({"segment_number": number, **value,
                         "origin_pages": [result["page_number"] for result in origin],
                         "sealed_pages": [result["page_number"] for result, _ in results.values() if result["sealed_by_segment"] == number],
                         "observed_calls": sum(len(result["receipt"]["calls"]) for result in origin if result["receipt"] is not None),
                         "unobserved_interrupted_pages": [result["page_number"] for result in origin if result["state"] == "interrupted"]})
    selected = [p["page_number"] for p in plan["selected_pages"]]
    manifest = {"schema_version": 1, "kind": "ocr_checkpoint_completion", "operation": "pages",
                "request_sha256": state["request_sha256"], "plan_sha256": state["plan_sha256"],
                "report_sha256": core.digest(report), "inputs": journal.request["inputs"],
                "configuration": journal.request["configuration"], "pages": pages, "segments": segments,
                "coverage": {"selected_pages": selected,
                             "committed_pages": [p["page_number"] for p in pages if p["state"] == "committed"],
                             "interrupted_pages": [p["page_number"] for p in pages if p["state"] == "interrupted"],
                             "deferred_pages": [p["page_number"] for p in plan["deferred_pages"]], "unstarted_pages": []},
                "requires_attention": True, "scope": core.SCOPE}
    return manifest, report


def read_checkpoint_completion(output_dir: Path, *, request: dict, specs: list,
                               installation_path: Path | None = None) -> dict:
    """Strict parent readback of all origin records and the assembled report."""
    journal = _Journal(Path(output_dir), specs, core.validate_request(request), installation_path)
    with PathLease(output_dir, backend="ocr-checkpoint", collection_name="generation", operation="read",
                   timeout=0, resource_description="OCR checkpoint generation"):
        state = _state(journal)
        expected, report = _completion(journal, state)
        saved_report, _ = journal.read("report.json")
        saved, _ = journal.read("manifest.json")
        if not core.same(saved_report, report) or not core.same(saved, expected):
            raise ValueError("checkpoint completion contradicts committed records")
        if journal.pins["report.json"] != expected["report_sha256"]:
            raise ValueError("checkpoint report bytes differ from completion")
        journal.recheck()
        return saved


def run_checkpointed_pages(pdf_path: Path, output_dir: Path, *, resume: bool = False,
                           reader_factory=None, **options) -> dict:
    """Execute only unstarted pages, durably preserving every committed origin."""
    source, output, policy, specs, request = checkpoint_request(pdf_path, output_dir, resume=resume, **options)
    installation = options.get("installation_path")
    with PathLease(output, backend="ocr-checkpoint", collection_name="generation", operation="resume" if resume else "execute",
                   timeout=0, resource_description="OCR checkpoint generation"):
        if not resume:
            output.mkdir(exist_ok=False)
            storage_policy.enforce_private_path(output, directory=True)
        journal = _Journal(output, specs, request, installation)
        journal.recheck()
        if not resume:
            journal.put("request.json", request)
        state = _state(journal)
        if (output / "manifest.json").exists():
            manifest = read_checkpoint_completion(output, request=request, specs=specs, installation_path=installation)
            report, _ = journal.read("report.json")
            return {"manifest": manifest, "report": report}
        # A report already published before interruption is adopted only if all
        # origin evidence and exact derived bytes still agree; no new OCR.
        if (output / "report.json").exists() or (
                state["plan"] is not None and state["segments"]
                and len(state["results"]) == len(state["plan"]["selected_pages"])
                and all(segment["status"] != "open" for segment in state["segments"].values())):
            manifest, report = _completion(journal, state)
            journal.put("report.json", report, adopt=True)
            journal.put("manifest.json", manifest)
            return {"manifest": manifest, "report": report}
        number = len(state["segments"]) + 1
        core.integer(number, 1, core.MAX_SEGMENTS)
        for previous, segment in state["segments"].items():
            if segment["status"] == "open":
                journal.put(f"segment-{previous:04d}.end.json", {
                    "schema_version": 1, "kind": "ocr_checkpoint_segment_end", "segment_number": previous,
                    "request_sha256": state["request_sha256"], "status": "interrupted"})
        segment_start = {"schema_version": 1, "kind": "ocr_checkpoint_segment_start",
                         "request_sha256": state["request_sha256"], "segment_number": number}
        segment_digest = journal.put(f"segment-{number:04d}.start.json", segment_start)
        recorder = ExecutionRecorder()
        if reader_factory is None:
            from ocr_recovery_runtime import RapidOCRPageReader
            reader_factory = RapidOCRPageReader
        evidence = None
        if options.get("evidence_path") is not None:
            evidence = ocr_recovery.validate_evidence(_json(_snapshot(Path(options["evidence_path"]), ocr_recovery.MAX_EVIDENCE_BYTES)[0]))
        with artifact_io.immutable_file_snapshot(source, snapshot_name="source.pdf") as snapshot:
            if snapshot.sha256 != request["inputs"]["source_sha256"]:
                raise RuntimeError("checkpoint source changed before reader creation")
            reader_options = {"dpi": policy.dpi, "max_pixels": policy.max_pixels, "max_side": policy.max_side}
            if policy.preprocessing != "none":
                reader_options["preprocessing"] = policy.preprocessing
            with recorder.reader_factory(reader_factory)(snapshot.path, **reader_options) as reader:
                if state["plan"] is None:
                    count, _, selected, deferred = ocr_recovery._inspect_recovery_pages(
                        reader, source_sha256=snapshot.sha256, policy=policy,
                        requested_pages=tuple(request["configuration"]["requested_pages"]), evidence=evidence)
                    plan = {"schema_version": 1, "kind": "ocr_checkpoint_plan", "request_sha256": state["request_sha256"],
                            "page_count": count, "selected_pages": selected, "deferred_pages": deferred}
                    core.validate_plan(plan, request, state["request_sha256"])
                    journal.put("plan.json", plan)
                state = _state(journal)
                if type(reader.page_count) is not int or reader.page_count != state["plan"]["page_count"]:
                    raise ValueError("checkpoint reader page count changed")
                for baseline in state["plan"]["selected_pages"]:
                    page_number = baseline["page_number"]
                    if page_number in state["results"]:
                        continue
                    journal.recheck()
                    snapshot.verify()
                    existing = state["starts"].get(page_number)
                    if existing is not None:
                        start, start_digest = existing
                        outcome, receipt, result_state = core.failed_page(baseline), None, "interrupted"
                    else:
                        start = {"schema_version": 1, "kind": "ocr_checkpoint_page_start", "request_sha256": state["request_sha256"],
                                 "plan_sha256": state["plan_sha256"], "page_number": page_number,
                                 "origin_segment": number, "segment_sha256": segment_digest}
                        start_digest = journal.put(f"page-{page_number:05d}.start.json", start)
                        journal.recheck()
                        before, started = len(recorder.calls), time.monotonic()
                        outcome = ocr_recovery._retry_recovery_page(reader, baseline, policy)
                        observation = reader.execution_observation() if reader._engine is not None else None
                        calls = [{**call, "id": f"call-{index:04d}"} for index, call in enumerate(recorder.calls[before:], 1)]
                        receipt = capture_execution_receipt(
                            operation="pages", inputs={**request["inputs"], "request_sha256": state["request_sha256"],
                                                       "plan_sha256": state["plan_sha256"], "page_start_sha256": start_digest},
                            output_sha256=core.digest(outcome), observation=observation, calls=calls,
                            elapsed_seconds=time.monotonic() - started, installation_path=installation)
                        result_state = "committed"
                    result = {"schema_version": 1, "kind": "ocr_checkpoint_page_result", "request_sha256": state["request_sha256"],
                              "plan_sha256": state["plan_sha256"], "page_number": page_number,
                              "origin_segment": start["origin_segment"], "start_sha256": start_digest,
                              "state": result_state, "sealed_by_segment": number, "page": outcome, "receipt": receipt}
                    core.validate_result(result, request=request, plan=state["plan"], plan_sha256=state["plan_sha256"],
                                         baseline=baseline, start=start, start_sha256=start_digest, segments=state["segments"])
                    if receipt is not None:
                        validate_execution_receipt(receipt, installation_path=installation)
                    snapshot.verify()
                    journal.put(f"page-{page_number:05d}.result.json", result)
            snapshot.verify()
        journal.put(f"segment-{number:04d}.end.json", {"schema_version": 1, "kind": "ocr_checkpoint_segment_end",
                    "request_sha256": state["request_sha256"], "segment_number": number, "status": "finished"})
        state = _state(journal)
        manifest, report = _completion(journal, state)
        journal.put("report.json", report)
        journal.put("manifest.json", manifest)
        return {"manifest": manifest, "report": report}
