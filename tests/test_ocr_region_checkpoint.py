"""Synthetic region checkpoint mechanics with real guards/receipts and inert sessions.

Reuses the existing guard harness; no PDF/model loader.
"""

import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import model_artifacts
import ocr_checkpoint_io as page_workflow
import ocr_recovery
import ocr_region_checkpoint as core
import ocr_region_checkpoint_io as workflow
import ocr_regions
from ocr_engine_guard import RapidOCREngineGuard
from ocr_execution_receipt import observe_rapidocr_engine, validate_execution_receipt
from test_ocr_engine_guard import harness as harness
from test_ocr_regions import PageReader, candidate, geometry


def _load(path):
    return json.loads(path.read_bytes())


def _region_bytes(case):
    return {path.name: path.read_bytes() for path in case.output.glob("region-*.json")}


def _forbid_reader(*_args, **_kwargs):
    raise AssertionError("completed readback constructed a PDF/OCR reader")


def _description(bbox, dpi, scale=1):
    result = geometry(bbox, dpi * scale)
    result["raster"]["dpi"] = dpi
    for key in ("display_rect_points", "cropbox_points"):
        result["page"][key] = [0.0, 0.0, 72.0 * scale, 72.0 * scale]
    return result


@pytest.fixture
def case(tmp_path, monkeypatch, harness):
    roles = {"detection": "text_det", "classification": "text_cls", "recognition": "text_rec"}
    model_paths = {role: tmp_path / f"{role}.onnx" for role in roles}
    models = []
    for index, (role, attribute) in enumerate(sorted(roles.items()), 1):
        models.append(SimpleNamespace(role=role, content_sha256=str(index) * 64, size=100 + index))
        getattr(harness.raw, attribute).session.session = SimpleNamespace(
            get_providers=lambda: ["CPUExecutionProvider"],
            get_session_options=lambda: SimpleNamespace(intra_op_num_threads=2, inter_op_num_threads=1))
    monkeypatch.setattr(model_artifacts, "package_model", lambda _name: SimpleNamespace(version="3.9.2", files=models))
    monkeypatch.setattr(model_artifacts, "verified_installed_package_model", lambda *_args: dict(model_paths))
    identity = {"environment_sha256": "a" * 64, "python_sha256": "b" * 64,
                "base_python_sha256": "c" * 64, "runtime_sha256": "d" * 64,
                "producer_sources": {"ocr_region_checkpoint.py": "e" * 64},
                "model_artifacts": [{"id": model.role, "sha256": model.content_sha256, "bytes": model.size}
                                    for model in models]}
    monkeypatch.setattr(workflow, "capture_identity", lambda **_kwargs: copy.deepcopy(identity))
    monkeypatch.setattr(page_workflow, "capture_identity", lambda **_kwargs: copy.deepcopy(identity))

    state = SimpleNamespace(source=tmp_path / "source.pdf", recovery=tmp_path / "recovery.json",
                            plan=tmp_path / "regions.json", output=tmp_path / "checkpoint",
                            described=[], retries=[], constructed=0, closed=0,
                            empty=set(), fail=set(), changed_geometry=False, raw=harness.raw)
    state.source.write_bytes(b"generated checkpoint input; no PDF parser or models")
    source_hash = hashlib.sha256(state.source.read_bytes()).hexdigest()
    page_reader = PageReader()
    page_reader.page_count = 1
    saved = ocr_recovery.build_recovery_report(
        page_reader, source_sha256=source_hash, policy=ocr_recovery.RetryPolicy(), requested_pages=(1,))
    saved["evidence_sha256"] = None
    state.recovery.write_text(json.dumps(saved), encoding="utf-8")
    entries = [{"region_id": name, "page_number": 1, "bbox": bbox} for name, bbox in (
        ("A", [0.1, 0.1, 0.3, 0.3]), ("a", [0.4, 0.1, 0.6, 0.3]), ("z", [0.7, 0.1, 0.9, 0.3]))]
    raw_plan = {"schema_version": 1, "source_sha256": source_hash,
                "recovery_sha256": hashlib.sha256(state.recovery.read_bytes()).hexdigest(),
                "coordinate_system": "original_page_display_fraction", "regions": entries}
    state.plan.write_text(json.dumps(raw_plan), encoding="utf-8")
    names = {tuple(entry["bbox"]): entry["region_id"] for entry in entries}

    class Reader:
        page_count = 1

        def __init__(self, path, *, dpi, max_pixels, max_side):
            assert Path(path).read_bytes() == state.source.read_bytes()
            assert (max_pixels, max_side) == (25_000_000, 6000)
            self.dpi, self._engine = dpi, None
            state.constructed += 1

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

        def close(self):
            state.closed += 1
            self._engine = None

        def _load_engine(self):
            if self._engine is None:
                self._engine = RapidOCREngineGuard(harness.raw)
            return self._engine

        def execution_observation(self):
            return observe_rapidocr_engine(self._engine, engine_version="3.9.2", model_paths=model_paths)

        def describe_region(self, number, bbox):
            assert number == 1
            name = names[tuple(bbox)]
            state.described.append(name)
            return _description(bbox, self.dpi, 2 if state.changed_geometry and name == "A" else 1)

        def retry_region(self, number, bbox):
            assert number == 1
            name = names[tuple(bbox)]
            state.retries.append(name)
            if name in state.fail:
                raise ValueError("synthetic predispatch rejection")
            described = _description(bbox, self.dpi)
            raster = described["raster"]
            self._load_engine()(harness.np.zeros((raster["height"], raster["width"], 3), dtype=harness.np.uint8))
            result = candidate(raster, "" if name in state.empty else f"synthetic region {name}")
            result["engine"]["version"] = "3.9.2"
            return {"geometry": described, "candidate": result}

    state.reader = Reader
    return state


def _run(case, **options):
    options.setdefault("reader_factory", case.reader)
    return workflow.run_checkpointed_regions(
        case.source, case.output, recovery_path=case.recovery, plan_path=case.plan, **options)


def _completion(case):
    _, _, _, specs, request = workflow.region_checkpoint_request(
        case.source, case.output, recovery_path=case.recovery, plan_path=case.plan, resume=True)
    return workflow.read_region_checkpoint_completion(case.output, request=request, specs=specs)


def _interrupt_after(case, monkeypatch, filename):
    original = page_workflow._Journal.put

    def cancel_after_put(journal, name, value, *, adopt=False):
        result = original(journal, name, value, adopt=adopt)
        if name == filename:
            raise KeyboardInterrupt()
        return result

    with monkeypatch.context() as patch:
        patch.setattr(page_workflow._Journal, "put", cancel_after_put)
        with pytest.raises(KeyboardInterrupt):
            _run(case)


def test_same_page_case_sensitive_ids_preserve_consumed_attempts_and_receipt_origins(case, monkeypatch):
    _interrupt_after(case, monkeypatch, "region-0002.start.json")
    assert case.retries == ["A"] and case.raw.calls == 1
    first = (case.output / "region-0001.result.json").read_bytes()
    result = _run(case, resume=True)
    assert case.retries == ["A", "z"] and case.raw.calls == 2
    assert case.described == ["A", "a", "z", "A", "a", "z"]
    assert (case.output / "region-0001.result.json").read_bytes() == first
    assert [item["region_id"] for item in result["report"]["regions"]] == ["A", "a", "z"]
    interrupted = _load(case.output / "region-0002.result.json")
    assert interrupted["state"] == "interrupted" and interrupted["receipt"] is None
    assert (interrupted["origin_segment"], interrupted["sealed_by_segment"]) == (1, 2)
    assert interrupted["region"]["status"] == "retry_failed"
    assert result["manifest"]["coverage"]["interrupted_ordinals"] == [2]
    assert result["manifest"]["segments"][0]["unobserved_interrupted_ordinals"] == [2]
    assert result["manifest"]["segments"][1]["origin_ordinals"] == [3]
    request = _load(case.output / "request.json")
    assert request["inputs"]["plan_sha256"] == hashlib.sha256(case.plan.read_bytes()).hexdigest()
    for ordinal, origin in ((1, 1), (3, 2)):
        saved = _load(case.output / f"region-{ordinal:04d}.result.json")
        receipt = saved["receipt"]
        assert validate_execution_receipt(receipt) == receipt
        assert saved["origin_segment"] == origin
        assert [(call["id"], call["status"]) for call in receipt["calls"]] == [("call-0001", "completed")]
        assert receipt["inputs"]["plan_sha256"] == request["inputs"]["plan_sha256"]
        assert receipt["inputs"]["checkpoint_plan_sha256"] == saved["checkpoint_plan_sha256"]
        assert receipt["inputs"]["region_start_sha256"] == saved["start_sha256"]
        assert receipt["output_sha256"] == core.outcome_digest(saved["region"])
    assert result["report"]["inputs"]["plan_sha256"] == request["inputs"]["plan_sha256"]
    assert ocr_regions.validate_region_review(result["report"]) == result["report"]
    assert _completion(case) == result["manifest"]


def test_committed_empty_and_predispatch_failure_are_reused_without_reader(case):
    case.empty.add("A")
    case.fail.add("a")
    first = _run(case)
    empty = _load(case.output / "region-0001.result.json")
    failed = _load(case.output / "region-0002.result.json")
    assert empty["state"] == failed["state"] == "committed"
    assert empty["region"]["status"] == "empty_candidate"
    assert failed["region"]["status"] == "retry_failed"
    assert len(empty["receipt"]["calls"]) == 1
    assert failed["receipt"]["calls"] == [] and not failed["receipt"]["ocr_executed"]
    assert validate_execution_receipt(failed["receipt"]) == failed["receipt"]
    before = _region_bytes(case)
    reopened = _run(case, resume=True, reader_factory=_forbid_reader)
    assert reopened == first and _completion(case) == first["manifest"]
    assert case.retries == ["A", "a", "z"] and case.raw.calls == 2 and case.constructed == 1
    assert _region_bytes(case) == before


def test_original_plan_byte_drift_refuses_resume_before_reader_or_new_items(case, monkeypatch):
    _interrupt_after(case, monkeypatch, "region-0002.start.json")
    before = {path.name: path.read_bytes() for path in case.output.iterdir() if path.is_file()}
    raw = case.plan.read_bytes()
    case.plan.write_bytes(raw + b"\n")
    assert _load(case.plan) == json.loads(raw)
    with pytest.raises((ValueError, RuntimeError)):
        _run(case, resume=True)
    assert case.retries == ["A"] and case.raw.calls == 1 and case.constructed == 1
    assert {path.name: path.read_bytes() for path in case.output.iterdir() if path.is_file()} == before


def test_valid_changed_geometry_of_committed_region_blocks_all_new_attempts(case, monkeypatch):
    _interrupt_after(case, monkeypatch, "region-0002.start.json")
    before = _region_bytes(case)
    starts = {name for name in before if name.endswith(".start.json")}
    bbox = [0.1, 0.1, 0.3, 0.3]
    ocr_regions._validate_geometry(_description(bbox, 300, 2), bbox, dpi=300)
    case.changed_geometry = True
    with pytest.raises(ValueError):
        _run(case, resume=True)
    assert case.described[-3:] == ["A", "a", "z"]
    assert case.retries == ["A"] and case.raw.calls == 1
    # Sealing the already consumed second start is allowed; fresh attempts are not.
    after = _region_bytes(case)
    assert all(after[name] == raw for name, raw in before.items())
    assert {name for name in after if name.endswith(".start.json")} == starts
    assert "region-0003.result.json" not in after
    assert not (case.output / "manifest.json").exists()


@pytest.mark.parametrize("filename", ["report.json", "manifest.json"])
def test_report_or_completion_publication_is_adopted_without_reader(case, monkeypatch, filename):
    _interrupt_after(case, monkeypatch, filename)
    before = _region_bytes(case)
    segments = {path.name: path.read_bytes() for path in case.output.glob("segment-*.json")}
    report = (case.output / "report.json").read_bytes()
    result = _run(case, resume=True, reader_factory=_forbid_reader)
    assert _completion(case) == result["manifest"]
    assert case.retries == ["A", "a", "z"] and case.raw.calls == 3 and case.constructed == 1
    assert _region_bytes(case) == before
    assert {path.name: path.read_bytes() for path in case.output.glob("segment-*.json")} == segments
    assert (case.output / "report.json").read_bytes() == report


def test_removed_completed_result_never_authorizes_reexecution(case):
    _run(case)
    removed = case.output / "region-0001.result.json"
    removed.unlink()
    before = _region_bytes(case)
    with pytest.raises((ValueError, RuntimeError)):
        _completion(case)
    with pytest.raises((ValueError, RuntimeError)):
        _run(case, resume=True, reader_factory=_forbid_reader)
    assert not removed.exists() and _region_bytes(case) == before
    assert case.retries == ["A", "a", "z"] and case.raw.calls == 3 and case.constructed == 1


def test_cli_region_parent_worker_and_readback_use_real_coordinator(case, monkeypatch, capsys):
    import ocr_region_runtime
    import process_supervision
    from tools import run_ocr_experiment as cli

    stages, checked = [], []
    original_request = workflow.region_checkpoint_request
    original_run = workflow.run_checkpointed_regions
    original_read = workflow.read_region_checkpoint_completion

    def request(*args, **kwargs):
        stages.append("request")
        assert kwargs["operation"] == "regions"
        return original_request(*args, **kwargs)

    def worker(*args, **kwargs):
        stages.append("worker")
        assert kwargs["operation"] == "regions" and kwargs["resume"] is False
        return original_run(*args, **kwargs)

    def readback(*args, **kwargs):
        stages.append("readback")
        result = original_read(*args, **kwargs)
        checked.append(result)
        return result

    def supervise(path, arguments, **options):
        stages.append("supervisor")
        assert Path(path) == Path(cli.__file__)
        assert arguments[-1] == "--worker" and "--checkpoint" in arguments
        assert options["stdout_target"] == options["stderr_target"] == cli.subprocess.DEVNULL
        assert options["environment_overrides"]["RAG_OCR_EXECUTION_CHILD"] == "1"
        assert options["environment_overrides"]["HF_HUB_OFFLINE"] == "1"
        with monkeypatch.context() as child:
            for name, value in options["environment_overrides"].items():
                child.setenv(name, value)
            return cli.main(arguments)

    monkeypatch.setattr(ocr_region_runtime, "RapidOCRRegionReader", case.reader)
    monkeypatch.setattr(workflow, "region_checkpoint_request", request)
    monkeypatch.setattr(workflow, "run_checkpointed_regions", worker)
    monkeypatch.setattr(workflow, "read_region_checkpoint_completion", readback)
    monkeypatch.setattr(process_supervision, "_run_cli_with_deadline", supervise)
    code = cli.main(["--pdf", str(case.source), "--output-dir", str(case.output),
                     "--checkpoint", "--operation", "regions", "--recovery", str(case.recovery),
                     "--plan", str(case.plan)])
    assert code == 3
    assert stages == ["request", "supervisor", "worker", "request", "readback"]
    assert len(checked) == 1 and checked[0]["kind"] == "ocr_region_checkpoint_completion"
    assert case.retries == ["A", "a", "z"] and case.raw.calls == 3 and case.constructed == 1
    output = capsys.readouterr()
    assert "region checkpoint generation completed" in output.out
    assert "per-region origin evidence" in output.out and output.err == ""


@pytest.mark.parametrize("mutation", ["source", "recovery", "dpi", "runtime"])
def test_region_resume_input_drift_stops_before_new_records_or_reader(case, monkeypatch, mutation):
    _interrupt_after(case, monkeypatch, "region-0002.start.json")
    before = {path.name: path.read_bytes() for path in case.output.iterdir() if path.is_file()}
    options = {}
    if mutation in {"source", "recovery"}:
        path = getattr(case, mutation)
        path.write_bytes(path.read_bytes() + b"\n")
    elif mutation == "dpi":
        options["policy"] = ocr_recovery.RetryPolicy(dpi=400)
    else:
        identity = _load(case.output / "request.json")["identity"]
        identity["runtime_sha256"] = "0" * 64
        monkeypatch.setattr(workflow, "capture_identity", lambda **_kwargs: copy.deepcopy(identity))
        monkeypatch.setattr(page_workflow, "capture_identity", lambda **_kwargs: copy.deepcopy(identity))
    with pytest.raises((ValueError, RuntimeError)):
        _run(case, resume=True, **options)
    assert case.retries == ["A"] and case.raw.calls == 1 and case.constructed == 1
    assert {path.name: path.read_bytes() for path in case.output.iterdir() if path.is_file()} == before


@pytest.mark.parametrize("mutation", ["page_filename", "wrong_kind", "unknown_artifact"])
def test_invalid_region_generation_artifacts_fail_before_completed_reuse(case, mutation):
    _run(case)
    if mutation == "page_filename":
        (case.output / "page-00001.start.json").write_bytes(
            (case.output / "region-0001.start.json").read_bytes())
    elif mutation == "wrong_kind":
        path = case.output / "region-0001.result.json"
        result = _load(path)
        result["kind"] = "ocr_checkpoint_page_result"
        path.write_bytes(core.json_bytes(result, limit=core.MAX_RESULT_BYTES))
    else:
        (case.output / "unknown.json").write_text("{}", encoding="utf-8")
    before = {path.name: path.read_bytes() for path in case.output.iterdir() if path.is_file()}
    with pytest.raises((ValueError, RuntimeError)):
        _run(case, resume=True, reader_factory=_forbid_reader)
    assert case.retries == ["A", "a", "z"] and case.raw.calls == 3 and case.constructed == 1
    assert {path.name: path.read_bytes() for path in case.output.iterdir() if path.is_file()} == before


def test_last_durable_start_is_sealed_without_constructing_resume_reader(case, monkeypatch):
    _interrupt_after(case, monkeypatch, "region-0003.start.json")
    assert case.retries == ["A", "a"] and case.raw.calls == 2 and case.constructed == 1
    before = _region_bytes(case)
    result = _run(case, resume=True, reader_factory=_forbid_reader)
    after = _region_bytes(case)
    assert all(after[name] == raw for name, raw in before.items())
    last = _load(case.output / "region-0003.result.json")
    assert last["state"] == "interrupted" and last["receipt"] is None
    assert (last["origin_segment"], last["sealed_by_segment"]) == (1, 2)
    assert result["manifest"]["coverage"]["interrupted_ordinals"] == [3]
    assert result["manifest"]["segments"][0]["unobserved_interrupted_ordinals"] == [3]
    assert result["manifest"]["segments"][1]["origin_ordinals"] == []
    assert result["manifest"]["segments"][1]["observed_calls"] == 0
    assert case.retries == ["A", "a"] and case.raw.calls == 2 and case.constructed == 1
    assert case.described == ["A", "a", "z"]
    assert _completion(case) == result["manifest"]


def test_interruption_before_geometry_plan_resumes_all_regions_with_empty_prior_origin(case, monkeypatch):
    _interrupt_after(case, monkeypatch, "segment-0001.start.json")
    assert not (case.output / "plan.json").exists()
    assert case.retries == [] and case.raw.calls == 0 and case.constructed == 0
    first_segment_start = (case.output / "segment-0001.start.json").read_bytes()
    result = _run(case, resume=True)
    assert (case.output / "segment-0001.start.json").read_bytes() == first_segment_start
    assert case.retries == ["A", "a", "z"] and case.raw.calls == 3 and case.constructed == 1
    assert case.described == ["A", "a", "z"]
    first, second = result["manifest"]["segments"]
    assert first["status"] == "interrupted" and first["origin_ordinals"] == []
    assert first["sealed_ordinals"] == [] and first["observed_calls"] == 0
    assert first["unobserved_interrupted_ordinals"] == []
    assert second["origin_ordinals"] == [1, 2, 3] and second["observed_calls"] == 3
    assert result["manifest"]["coverage"]["interrupted_ordinals"] == []
    assert all(_load(case.output / f"region-{ordinal:04d}.result.json")["origin_segment"] == 2
               for ordinal in (1, 2, 3))
    assert _completion(case) == result["manifest"]


def test_different_valid_report_is_not_adopted_or_overwritten(case, monkeypatch):
    _interrupt_after(case, monkeypatch, "report.json")
    report_path = case.output / "report.json"
    original = report_path.read_bytes()
    report = _load(report_path)
    changed = report["regions"][0]["candidate"]
    changed["text"] = changed["lines"][0]["text"] = "different valid synthetic region"
    assert ocr_regions.validate_region_review(report) == report
    replacement = core.report_bytes(report)
    assert replacement != original
    report_path.write_bytes(replacement)
    before = _region_bytes(case)
    with pytest.raises((ValueError, RuntimeError)):
        _run(case, resume=True, reader_factory=_forbid_reader)
    assert report_path.read_bytes() == replacement and _region_bytes(case) == before
    assert not (case.output / "manifest.json").exists()
    assert case.retries == ["A", "a", "z"] and case.raw.calls == 3 and case.constructed == 1


@pytest.mark.parametrize("earlier_status", ["open", "finished"])
def test_nonterminal_segment_cannot_be_open_or_finished(case, monkeypatch, earlier_status):
    _interrupt_after(case, monkeypatch, "region-0002.start.json")
    _run(case, resume=True)
    end_path = case.output / "segment-0001.end.json"
    manifest_path = case.output / "manifest.json"
    manifest = _load(manifest_path)
    if earlier_status == "open":
        end_path.unlink()
        end_digest = None
        message = "only the last region segment can be open"
    else:
        end = _load(end_path)
        end["status"] = "finished"
        end_path.write_bytes(core.json_bytes(end))
        end_digest = hashlib.sha256(end_path.read_bytes()).hexdigest()
        message = "region work cannot follow a finished segment"
    manifest["segments"][0].update(status=earlier_status, end_sha256=end_digest)
    manifest_path.write_bytes(core.json_bytes(manifest, limit=core.MAX_MANIFEST_BYTES))
    before = {path.name: path.read_bytes() for path in case.output.iterdir() if path.is_file()}
    with pytest.raises(ValueError, match=message):
        _completion(case)
    with pytest.raises(ValueError, match=message):
        _run(case, resume=True, reader_factory=_forbid_reader)
    assert {path.name: path.read_bytes() for path in case.output.iterdir() if path.is_file()} == before
    assert case.retries == ["A", "z"] and case.raw.calls == 2 and case.constructed == 2


def test_transplanted_receipt_keeps_a_wrong_internal_origin_despite_rebound_file_hashes(case, monkeypatch):
    _interrupt_after(case, monkeypatch, "region-0002.start.json")
    _run(case, resume=True)
    donor = _load(case.output / "region-0001.result.json")
    result_path = case.output / "region-0003.result.json"
    target = _load(result_path)
    assert (donor["origin_segment"], target["origin_segment"]) == (1, 2)
    receipt = copy.deepcopy(donor["receipt"])
    receipt["output_sha256"] = core.outcome_digest(target["region"])
    assert receipt["inputs"]["region_start_sha256"] == donor["start_sha256"]
    assert receipt["inputs"]["region_start_sha256"] != target["start_sha256"]
    assert validate_execution_receipt(receipt) == receipt
    target["receipt"] = receipt
    result_path.write_bytes(core.json_bytes(target, limit=core.MAX_RESULT_BYTES))
    manifest_path = case.output / "manifest.json"
    manifest = _load(manifest_path)
    row = next(item for item in manifest["regions"] if item["ordinal"] == 3)
    row["result_sha256"] = hashlib.sha256(result_path.read_bytes()).hexdigest()
    manifest_path.write_bytes(core.json_bytes(manifest, limit=core.MAX_MANIFEST_BYTES))
    # A valid receipt and updated enclosing hashes do not repair its start binding.
    before = {path.name: path.read_bytes() for path in case.output.iterdir() if path.is_file()}
    with pytest.raises(ValueError, match="region receipt differs from exact input/start/outcome bindings"):
        _completion(case)
    with pytest.raises(ValueError, match="region receipt differs from exact input/start/outcome bindings"):
        _run(case, resume=True, reader_factory=_forbid_reader)
    assert {path.name: path.read_bytes() for path in case.output.iterdir() if path.is_file()} == before
    assert case.retries == ["A", "z"] and case.raw.calls == 2 and case.constructed == 2


def test_unfinished_region_staging_is_preserved_without_adoption_or_a_second_attempt(case, monkeypatch):
    _interrupt_after(case, monkeypatch, "region-0002.start.json")
    before = _region_bytes(case)
    staging = case.output / ".region-0002.result.json.abcdef12.tmp"
    unfinished = b'{"unfinished'
    staging.write_bytes(unfinished)
    result = _run(case, resume=True)
    after = _region_bytes(case)
    assert all(after[name] == raw for name, raw in before.items())
    assert staging.read_bytes() == unfinished
    interrupted = _load(case.output / "region-0002.result.json")
    assert interrupted["state"] == "interrupted" and interrupted["receipt"] is None
    assert (interrupted["origin_segment"], interrupted["sealed_by_segment"]) == (1, 2)
    assert result["manifest"]["coverage"]["interrupted_ordinals"] == [2]
    assert _completion(case) == result["manifest"]
    assert staging.read_bytes() == unfinished
    assert case.retries == ["A", "z"] and case.raw.calls == 2 and case.constructed == 2


@pytest.mark.parametrize("resume", [False, True])
def test_region_writer_lease_blocks_new_and_resumed_work(case, monkeypatch, resume):
    from concurrent.futures import ThreadPoolExecutor
    from resource_lease import PathLease, PathLeaseBusyError

    if resume:
        _interrupt_after(case, monkeypatch, "region-0002.start.json")
    before = ({path.name: path.read_bytes() for path in case.output.iterdir() if path.is_file()}
              if case.output.exists() else {})
    reader_state = (case.constructed, list(case.retries), case.raw.calls)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with PathLease(case.output, backend="ocr-checkpoint", collection_name="generation",
                       operation="test", timeout=0, resource_description="synthetic region checkpoint"):
            future = pool.submit(_run, case, resume=resume)
            with pytest.raises(PathLeaseBusyError):
                future.result(timeout=10)
    assert case.output.exists() is resume
    after = ({path.name: path.read_bytes() for path in case.output.iterdir() if path.is_file()}
             if case.output.exists() else {})
    assert after == before
    assert (case.constructed, case.retries, case.raw.calls) == reader_state


def test_region_writer_rejects_recovery_plan_alias_before_creating_generation(case):
    alias_parent = case.recovery.parent / "alias-parent"
    alias_parent.mkdir()
    alias = alias_parent / ".." / case.recovery.name
    assert alias != case.recovery and alias.samefile(case.recovery)
    inputs = {path: path.read_bytes() for path in (case.source, case.recovery, case.plan)}
    with pytest.raises(ValueError, match="inputs must be distinct and outside the generation"):
        workflow.run_checkpointed_regions(
            case.source, case.output, recovery_path=case.recovery, plan_path=alias,
            reader_factory=case.reader)
    assert not case.output.exists()
    assert case.constructed == 0 and case.retries == [] and case.raw.calls == 0
    assert {path: path.read_bytes() for path in inputs} == inputs
