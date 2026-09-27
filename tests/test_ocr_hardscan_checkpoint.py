"""Hard-scan checkpoint behavior with synthetic inputs and inert guarded sessions.

Only the established engine-guard fixture is shared with another test module.
No PDF reader, model loader, subprocess or real OCR engine is used.
"""

import copy
import hashlib
import json
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

import model_artifacts
import ocr_checkpoint_io as journal_io
import ocr_hardscan as policy
import ocr_hardscan_checkpoint as core
import ocr_hardscan_checkpoint_io as workflow
import ocr_hardscan_io as ordinary
import ocr_recovery
import ocr_region_checkpoint_io as shared
from ocr_engine_guard import RapidOCREngineGuard
from ocr_execution_receipt import observe_rapidocr_engine, validate_execution_receipt
from test_ocr_engine_guard import harness as harness


def _load(path):
    return json.loads(path.read_bytes())


def _bytes(directory):
    return {path.name: path.read_bytes() for path in directory.iterdir() if path.is_file()}


def _forbid_reader(*_args, **_kwargs):
    raise AssertionError("completed or consumed-only recovery constructed a reader")


def _recipe(angle=0, bow=0.):
    return {"orientation_clockwise": angle, "bow_fraction": bow, "illumination": "none",
            "bow_assumption": "parallel_horizontal_baselines" if bow else "none"}


def _candidate(raster, text):
    width, height = raster["width"], raster["height"]
    return {"text": text, "lines": [{"text": text, "score": .9,
            "box": [[0., 0.], [width, 0.], [width, height], [0., height]]}] if text else [],
            "mean_confidence": .9 if text else None, "raster": raster,
            "engine": {"name": "rapidocr", "version": "3.9.2", "min_score": 0., "max_side": 6000}}


def _description(bbox, dpi, recipe, scale=1):
    pixels = dpi * scale
    x0, y0 = math.floor(bbox[0] * pixels), math.floor(bbox[1] * pixels)
    x1, y1 = math.ceil(bbox[2] * pixels), math.ceil(bbox[3] * pixels)
    geometry = {
        "page": {"display_rect_points": [0., 0., 72. * scale, 72. * scale],
                 "cropbox_points": [0., 0., 72. * scale, 72. * scale], "rotation_degrees": 0},
        "raster": {"width": x1-x0, "height": y1-y0, "dpi": dpi,
                   "coordinate_system": "rendered_image_pixels"},
        "pixel_bounds": [x0, y0, x1, y1],
        "crop_to_page_fraction": [[1/pixels, 0., x0/pixels], [0., 1/pixels, y0/pixels]],
        "boundary_policy": "clip_to_page_bounds"}
    return {"geometry": geometry, "transform": policy.describe_transform(x1-x0, y1-y0, recipe)}


def _write_plan(case):
    case.plan.write_bytes(json.dumps(case.plan_value).encode("utf-8"))


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
                "producer_sources": {"ocr_hardscan_checkpoint.py": "e" * 64},
                "model_artifacts": [{"id": model.role, "sha256": model.content_sha256, "bytes": model.size}
                                    for model in models]}
    for module in (shared, journal_io):
        monkeypatch.setattr(module, "capture_identity", lambda **_kwargs: copy.deepcopy(identity))
    state = SimpleNamespace(source=tmp_path / "source.pdf", recovery=tmp_path / "recovery.json",
                            plan=tmp_path / "hardscan.json", output=tmp_path / "checkpoint",
                            events=[], retries=[], constructed=0, closed=0, raw=harness.raw,
                            empty=set(), image_abstain=set(), double=set(), changed_geometry=False)
    state.source.write_bytes(b"synthetic hard-scan checkpoint input; never parsed")
    source_hash = hashlib.sha256(state.source.read_bytes()).hexdigest()
    page_reader = SimpleNamespace(
        page_count=1, native_text=lambda _page: "Synthetic native context retained for review, never region ground truth.",
        retry=lambda _page: _candidate({"width": 300, "height": 300, "dpi": 300,
                                       "coordinate_system": "rendered_image_pixels"}, "synthetic baseline"))
    recovery = ocr_recovery.build_recovery_report(
        page_reader, source_sha256=source_hash, policy=ocr_recovery.RetryPolicy(), requested_pages=(1,))
    recovery["evidence_sha256"] = None
    state.recovery.write_bytes(json.dumps(recovery).encode("utf-8"))
    state.entries = [{"region_id": name, "page_number": 1, "bbox": bbox, "recipe": recipe}
                     for name, bbox, recipe in (
                         ("A", [.1, .1, .3, .35], _recipe(90)),
                         ("a", [.4, .1, .6, .35], _recipe(bow=.02)),
                         ("z", [.7, .1, .9, .35], _recipe(180)))]
    state.plan_value = {"schema_version": 1, "kind": "ocr_hardscan_plan", "approval": "operator_approved",
                        "source_sha256": source_hash,
                        "recovery_sha256": hashlib.sha256(state.recovery.read_bytes()).hexdigest(),
                        "coordinate_system": "original_page_display_fraction", "regions": list(reversed(state.entries))}
    _write_plan(state)
    names = {tuple(entry["bbox"]): (index, entry["region_id"])
             for index, entry in enumerate(state.entries, 1)}

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

        def describe_hardscan(self, number, bbox, recipe):
            assert number == 1
            _, name = names[tuple(bbox)]
            state.events.append(("describe", name))
            return _description(bbox, self.dpi, recipe, 2 if state.changed_geometry and name == "A" else 1)

        def retry_hardscan(self, number, bbox, recipe):
            assert number == 1
            ordinal, name = names[tuple(bbox)]
            # Observable dispatch boundary: own start and preceding result are durable.
            assert (state.output / f"region-{ordinal:04d}.start.json").is_file()
            if ordinal > 1:
                assert (state.output / f"region-{ordinal-1:04d}.result.json").is_file()
            state.retries.append(name)
            state.events.append(("retry", name))
            result = _description(bbox, self.dpi, recipe)
            libraries = {"opencv": "synthetic", "numpy": "synthetic"}
            if name in state.image_abstain:
                result.update(processing={"status": "abstained", "reason": "blank_or_low_ink",
                                          "illumination": None, "libraries": libraries}, candidate=None)
                return result
            raster = {**result["transform"]["processed_raster"], "dpi": self.dpi,
                      "coordinate_system": "hardscan_image_pixels"}
            self._load_engine()(harness.np.zeros((raster["height"], raster["width"], 3), dtype=harness.np.uint8))
            candidate = _candidate(raster, "" if name in state.empty else f"synthetic hardscan {name}")
            if name in state.double:
                candidate["lines"] *= 2
                candidate["text"] = "\n".join(line["text"] for line in candidate["lines"])
            result.update(candidate=candidate, processing={"status": "completed", "reason": None,
                "libraries": libraries, "illumination": {"status": "disabled", "reason": "mode_disabled",
                                                         "background_low": None, "background_high": None, "ink_fraction": None}})
            return result

    state.reader = Reader
    return state


def _run(case, **options):
    options.setdefault("reader_factory", case.reader)
    return workflow.run_checkpointed_hardscan(
        case.source, case.output, recovery_path=case.recovery, plan_path=case.plan, **options)


def _request(case, **options):
    return workflow.hardscan_checkpoint_request(
        case.source, case.output, recovery_path=case.recovery, plan_path=case.plan, **options)


def _completion(case):
    _, _, _, specs, request = _request(case, resume=True)
    return workflow.read_hardscan_checkpoint_completion(case.output, request=request, specs=specs)


def _interrupt_after(case, monkeypatch, filename):
    original = journal_io._Journal.put

    def cancel_after_put(journal, name, value, *, adopt=False):
        result = original(journal, name, value, adopt=adopt)
        if name == filename:
            raise KeyboardInterrupt()
        return result

    with monkeypatch.context() as patch:
        patch.setattr(journal_io._Journal, "put", cancel_after_put)
        with pytest.raises(KeyboardInterrupt):
            _run(case)


@pytest.mark.parametrize("options", [
    {"operation": "regions"}, {"requested_pages": (1,)},
    {"policy": ocr_recovery.RetryPolicy(preprocessing="contrast")}, {"resume": 1},
], ids=["wrong-profile", "page-selection", "implicit-preprocessing", "nonboolean-resume"])
def test_public_rejection_precedes_input_or_runtime_work(case, monkeypatch, options):
    monkeypatch.setattr(shared, "_snapshot", lambda *_a, **_k: pytest.fail("input was read"))
    monkeypatch.setattr(shared, "capture_identity", lambda **_k: pytest.fail("runtime was inspected"))
    with pytest.raises(ValueError):
        _run(case, **options)
    assert not case.output.exists() and case.constructed == case.raw.calls == 0


@pytest.mark.parametrize("mutation", ["kind", "float-dpi", "larger-budget"])
def test_request_contract_rejects_rebound_nonordinary_configuration(case, mutation):
    *_, request = _request(case)
    if mutation == "kind":
        request["kind"] = "ocr_region_checkpoint_request"
    elif mutation == "float-dpi":
        request["configuration"]["retry_configuration"]["dpi"] = 300.0
    else:
        request["configuration"]["retry_configuration"]["max_total_vertices"] += 1
    request["inputs"]["configuration_sha256"] = core.digest(request["configuration"])
    with pytest.raises(ValueError):
        core.validate_request(request)
    assert not case.output.exists() and case.constructed == case.raw.calls == 0


def test_canonical_same_page_recipe_plan_and_real_receipt_joins(case):
    result = _run(case)
    report, manifest = result["report"], result["manifest"]
    plan, request = _load(case.output / "plan.json"), _load(case.output / "request.json")
    assert [item["region_id"] for item in _load(case.plan)["regions"]] == ["z", "a", "A"]
    assert [(item["ordinal"], item["region_id"], item["page_number"]) for item in plan["regions"]] == [
        (1, "A", 1), (2, "a", 1), (3, "z", 1)]
    assert case.events == [("describe", name) for name in ("A", "a", "z")] + [
        ("retry", name) for name in ("A", "a", "z")]
    assert [item["recipe"] for item in report["regions"]] == [item["recipe"] for item in case.entries]
    assert request["inputs"]["plan_sha256"] == hashlib.sha256(case.plan.read_bytes()).hexdigest()
    assert manifest["checkpoint_plan_sha256"] == hashlib.sha256((case.output / "plan.json").read_bytes()).hexdigest()
    assert manifest["checkpoint_plan_sha256"] != request["inputs"]["plan_sha256"]
    assert ordinary.validate_report(report) == report
    for ordinal in (1, 2, 3):
        saved = _load(case.output / f"region-{ordinal:04d}.result.json")
        receipt = saved["receipt"]
        assert validate_execution_receipt(receipt) == receipt and receipt["operation"] == "hardscan"
        assert receipt["inputs"]["plan_sha256"] == request["inputs"]["plan_sha256"]
        assert receipt["inputs"]["checkpoint_plan_sha256"] == manifest["checkpoint_plan_sha256"]
        assert receipt["inputs"]["hardscan_start_sha256"] == saved["start_sha256"]
        assert receipt["output_sha256"] == core.outcome_digest(saved["region"])
        assert [(call["id"], call["status"]) for call in receipt["calls"]] == [("call-0001", "completed")]
    assert _completion(case) == manifest and case.constructed == case.closed == 1


@pytest.mark.parametrize("mutation", ["case-sensitive-id", "transform"])
def test_derived_plan_cannot_rewrite_original_identity_or_transform(case, monkeypatch, mutation):
    _interrupt_after(case, monkeypatch, "plan.json")
    before = _bytes(case.output)
    plan, request = _load(case.output / "plan.json"), _load(case.output / "request.json")
    original = policy.validate_plan(_load(case.plan), source_sha256=request["inputs"]["source_sha256"],
                                   recovery_sha256=request["inputs"]["recovery_sha256"], page_count=1)
    if mutation == "case-sensitive-id":
        plan["regions"][0]["region_id"] = "a"
    else:
        plan["regions"][0]["transform"]["orientation_forward"][0][0] = 999.
    with pytest.raises(ValueError):
        core.validate_plan(plan, request, core.digest(request), recovery=_load(case.recovery), region_plan=original)
    assert _bytes(case.output) == before and case.retries == [] and case.raw.calls == 0


@pytest.mark.parametrize("ineligible", [False, True], ids=["eligible-unknown-call", "known-geometry-unknown-call"])
def test_durable_start_consumes_attempt_with_truthful_origin_projection(case, monkeypatch, ineligible):
    if ineligible:
        case.entries[1]["recipe"] = _recipe(bow=.00001)
        _write_plan(case)
    _interrupt_after(case, monkeypatch, "region-0002.start.json")
    before = _bytes(case.output)
    assert case.retries == ["A"] and case.raw.calls == 1
    assert "region-0002.result.json" not in before and "region-0003.start.json" not in before
    result = _run(case, resume=True)
    assert all((case.output / name).read_bytes() == raw for name, raw in before.items())
    saved = _load(case.output / "region-0002.result.json")
    assert (saved["state"], saved["origin_segment"], saved["sealed_by_segment"], saved["receipt"]) == ("interrupted", 1, 2, None)
    assert saved["region"]["status"] == ("abstained" if ineligible else "retry_failed")
    assert saved["region"]["abstention_reason"] == ("bow_below_pixel_resolution" if ineligible else None)
    assert case.retries == ["A", "z"] and case.raw.calls == 2
    segments = result["manifest"]["segments"]
    assert segments[0]["origin_ordinals"] == [1, 2] and segments[0]["observed_calls"] == 1
    assert segments[0]["unobserved_interrupted_ordinals"] == [2]
    assert segments[1]["origin_ordinals"] == [3] and segments[1]["observed_calls"] == 1
    assert result["manifest"]["coverage"]["interrupted_ordinals"] == [2]
    assert _completion(case) == result["manifest"]


def test_committed_geometry_image_abstention_and_empty_remain_distinct(case):
    case.entries[0]["recipe"] = _recipe(bow=.00001)
    _write_plan(case)
    case.image_abstain.add("a")
    case.empty.add("z")
    first = _run(case)
    records = [_load(case.output / f"region-{ordinal:04d}.result.json") for ordinal in (1, 2, 3)]
    assert [item["region"]["status"] for item in records] == ["abstained", "abstained", "empty_candidate"]
    assert records[0]["region"]["processing"] is None
    assert records[1]["region"]["processing"]["status"] == "abstained"
    assert records[2]["region"]["candidate"]["text"] == ""
    assert all(item["state"] == "committed" for item in records)
    for item, calls in zip(records, (0, 0, 1)):
        assert validate_execution_receipt(item["receipt"]) == item["receipt"]
        assert len(item["receipt"]["calls"]) == calls
    assert case.retries == ["a", "z"] and case.raw.calls == 1
    before = _bytes(case.output)
    assert _run(case, resume=True, reader_factory=_forbid_reader) == first
    assert _completion(case) == first["manifest"] and _bytes(case.output) == before
    assert case.constructed == case.closed == 1


def test_vertex_prefix_survives_resume_and_failed_mapping_does_not_consume_it(case, monkeypatch):
    monkeypatch.setattr(policy, "MAX_TOTAL_VERTICES", 8)
    monkeypatch.setattr(core, "MAX_TOTAL_VERTICES", 8)
    mapping_failures, original_mapping = [], ordinary._source_polygons

    def observed_mapping(*args, **kwargs):
        try:
            return original_mapping(*args, **kwargs)
        except ValueError as error:
            mapping_failures.append((kwargs["vertex_budget"], str(error)))
            raise

    monkeypatch.setattr(ordinary, "_source_polygons", observed_mapping)
    case.entries[1]["recipe"] = _recipe()
    _write_plan(case)
    case.double.add("a")  # Two real mapped rectangles need eight vertices; only four remain.
    _interrupt_after(case, monkeypatch, "region-0001.result.json")
    first = (case.output / "region-0001.result.json").read_bytes()
    result = _run(case, resume=True)
    assert (case.output / "region-0001.result.json").read_bytes() == first
    assert case.retries == ["A", "a", "z"] and case.raw.calls == 3
    outcomes = result["report"]["regions"]
    assert [item["status"] for item in outcomes] == ["review_required", "retry_failed", "review_required"]
    assert outcomes[1]["error_code"] == "retry_limit_or_validation" and outcomes[1]["source_polygons"] is None
    assert mapping_failures == [(4, "hard-scan source polygons exceed vertex budget")]
    assert sum(len(polygon) for item in outcomes for polygon in item["source_polygons"] or []) == 8
    failed = _load(case.output / "region-0002.result.json")
    assert failed["state"] == "committed" and len(failed["receipt"]["calls"]) == 1
    assert result["manifest"]["coverage"]["interrupted_ordinals"] == []
    assert _completion(case) == result["manifest"]


@pytest.mark.parametrize("filename", ["report.json", "manifest.json"])
def test_final_publication_is_adopted_without_reader_or_journal_rewrite(case, monkeypatch, filename):
    _interrupt_after(case, monkeypatch, filename)
    before = _bytes(case.output)
    result = _run(case, resume=True, reader_factory=_forbid_reader)
    after = _bytes(case.output)
    assert all(after[name] == raw for name, raw in before.items())
    assert set(after) - set(before) == ({"manifest.json"} if filename == "report.json" else set())
    assert _completion(case) == result["manifest"]
    assert case.retries == ["A", "a", "z"] and case.raw.calls == 3 and case.constructed == 1


def test_pending_final_start_seals_without_constructing_another_reader(case, monkeypatch):
    _interrupt_after(case, monkeypatch, "region-0003.start.json")
    before = _bytes(case.output)
    result = _run(case, resume=True, reader_factory=_forbid_reader)
    assert all((case.output / name).read_bytes() == raw for name, raw in before.items())
    assert case.retries == ["A", "a"] and case.raw.calls == 2 and case.constructed == 1
    assert result["manifest"]["coverage"]["interrupted_ordinals"] == [3]
    assert result["manifest"]["segments"][1]["origin_ordinals"] == []
    assert _load(case.output / "region-0003.result.json")["receipt"] is None
    assert _completion(case) == result["manifest"]


def test_raw_plan_byte_drift_blocks_resume_before_new_journal_or_reader(case, monkeypatch):
    _interrupt_after(case, monkeypatch, "region-0002.start.json")
    before, raw = _bytes(case.output), case.plan.read_bytes()
    case.plan.write_bytes(raw + b"\n")
    assert _load(case.plan) == json.loads(raw)
    with pytest.raises(ValueError, match="generation differs"):
        _run(case, resume=True)
    assert _bytes(case.output) == before and case.constructed == case.raw.calls == 1


def test_valid_changed_geometry_prevents_dispatch_after_consumed_start_is_sealed(case, monkeypatch):
    _interrupt_after(case, monkeypatch, "region-0002.start.json")
    before = _bytes(case.output)
    case.changed_geometry = True
    with pytest.raises(ValueError, match="geometry changed after its saved preflight"):
        _run(case, resume=True)
    assert all((case.output / name).read_bytes() == raw for name, raw in before.items())
    assert case.events[-3:] == [("describe", name) for name in ("A", "a", "z")]
    assert case.retries == ["A"] and case.raw.calls == 1
    assert _load(case.output / "region-0002.result.json")["state"] == "interrupted"
    assert not (case.output / "region-0003.start.json").exists()
    assert not (case.output / "manifest.json").exists()


def test_generic_valid_receipt_cannot_be_transplanted_between_origin_starts(case, monkeypatch):
    _interrupt_after(case, monkeypatch, "region-0002.start.json")
    _run(case, resume=True)
    donor = _load(case.output / "region-0001.result.json")
    path = case.output / "region-0003.result.json"
    target = _load(path)
    assert (donor["origin_segment"], target["origin_segment"]) == (1, 2)
    receipt = copy.deepcopy(donor["receipt"])
    receipt["output_sha256"] = core.outcome_digest(target["region"])
    assert validate_execution_receipt(receipt) == receipt
    assert receipt["inputs"]["hardscan_start_sha256"] != target["start_sha256"]
    target["receipt"] = receipt
    path.write_bytes(core.json_bytes(target, limit=core.MAX_RESULT_BYTES))
    manifest_path = case.output / "manifest.json"
    manifest = _load(manifest_path)
    next(item for item in manifest["regions"] if item["ordinal"] == 3)["result_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest_path.write_bytes(core.json_bytes(manifest, limit=core.MAX_MANIFEST_BYTES))
    before = _bytes(case.output)
    with pytest.raises(ValueError, match="hard-scan receipt differs from exact input/start/outcome bindings"):
        _completion(case)
    with pytest.raises(ValueError, match="hard-scan receipt differs from exact input/start/outcome bindings"):
        _run(case, resume=True, reader_factory=_forbid_reader)
    assert _bytes(case.output) == before and case.retries == ["A", "z"] and case.raw.calls == 2


@pytest.mark.parametrize("contained", [True, False], ids=["parent-worker-readback", "uncontained-worker"])
def test_cli_hardscan_route_preserves_containment_and_exact_completion(case, monkeypatch, capsys, contained):
    import ocr_hardscan_runtime
    import process_supervision
    from tools import run_ocr_experiment as cli

    stages = []
    request, run, read = (workflow.hardscan_checkpoint_request, workflow.run_checkpointed_hardscan,
                          workflow.read_hardscan_checkpoint_completion)

    def observed_request(*args, **kwargs):
        stages.append("request")
        assert kwargs["operation"] == "hardscan"
        return request(*args, **kwargs)

    def observed_run(*args, **kwargs):
        stages.append("worker")
        assert kwargs["operation"] == "hardscan" and kwargs["resume"] is False
        return run(*args, **kwargs)

    def observed_read(*args, **kwargs):
        stages.append("readback")
        result = read(*args, **kwargs)
        assert result["kind"] == "ocr_hardscan_checkpoint_completion"
        return result

    def supervise(path, arguments, **options):
        stages.append("supervisor")
        assert Path(path) == Path(cli.__file__) and arguments[-1] == "--worker"
        assert options["stdout_target"] == options["stderr_target"] == cli.subprocess.DEVNULL
        assert options["environment_overrides"]["RAG_OCR_EXECUTION_CHILD"] == "1"
        assert options["environment_overrides"]["HF_HUB_OFFLINE"] == "1"
        with monkeypatch.context() as child:
            for name, value in options["environment_overrides"].items():
                child.setenv(name, value)
            return cli.main(arguments)

    monkeypatch.delenv("RAG_OCR_EXECUTION_CHILD", raising=False)
    monkeypatch.setattr(ocr_hardscan_runtime, "RapidOCRHardScanReader", case.reader)
    monkeypatch.setattr(workflow, "hardscan_checkpoint_request", observed_request)
    monkeypatch.setattr(workflow, "run_checkpointed_hardscan", observed_run)
    monkeypatch.setattr(workflow, "read_hardscan_checkpoint_completion", observed_read)
    monkeypatch.setattr(process_supervision, "_run_cli_with_deadline", supervise)
    arguments = ["--pdf", str(case.source), "--output-dir", str(case.output), "--checkpoint",
                 "--operation", "hardscan", "--recovery", str(case.recovery), "--plan", str(case.plan)]
    code = cli.main(arguments if contained else arguments + ["--worker"])
    output = capsys.readouterr()
    if contained:
        assert code == 3 and stages == ["request", "supervisor", "worker", "readback"]
        assert "hard-scan region checkpoint generation completed" in output.out
        assert "per-region origin evidence" in output.out and output.err == ""
        assert case.retries == ["A", "a", "z"] and case.raw.calls == 3
    else:
        assert code == 2 and stages == [] and "OCR execution failed" in output.err
        assert not case.output.exists() and case.constructed == case.raw.calls == 0
