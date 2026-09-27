"""Synthetic page checkpoint crash/resume, strict bindings and no false success."""

import copy
import hashlib
import json
import os
from pathlib import Path

import pytest

import model_artifacts
import ocr_checkpoint as core
import ocr_checkpoint_io as workflow
import ocr_recovery
from ocr_recovery_comparison import validate_recovery_report
from tools import run_ocr_experiment as cli


def _identity():
    return {"environment_sha256": "a" * 64, "python_sha256": "b" * 64,
            "base_python_sha256": "c" * 64, "runtime_sha256": "d" * 64,
            "producer_sources": {"ocr_checkpoint.py": "e" * 64},
            "model_artifacts": [{"id": role, "sha256": "f" * 64, "bytes": 10}
                                for role in ("classification", "detection", "recognition")]}


class Reader:
    page_count = 3
    calls = []
    native_calls = []
    closed = 0
    interrupt = None
    fail = None
    empty = None

    def __init__(self, _path, **options):
        self._engine = None
        self.options = options

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    def close(self):
        Reader.closed += 1

    def _load_engine(self):
        if self._engine is None:
            self._engine = lambda: None
        return self._engine

    def execution_observation(self):
        artifact = model_artifacts.package_model("rapidocr")
        models = [{"id": item.role, "sha256": item.content_sha256, "bytes": item.size} for item in artifact.files]
        from ocr_execution_receipt import validate_engine_observation, _SCOPE
        return validate_engine_observation({
            "engine": {"name": "rapidocr", "version": artifact.version}, "model_artifacts": models,
            "sessions": [{"role": model["id"], "model_sha256": model["sha256"],
                          "execution_providers": ["CPUExecutionProvider"],
                          "thread_settings": {"intra_op": 2, "inter_op": 1}} for model in models],
            "verification_scope": _SCOPE})

    def native_text(self, number):
        Reader.native_calls.append(number)
        return ""

    def retry(self, number):
        Reader.calls.append(number)
        if number == Reader.interrupt:
            raise KeyboardInterrupt()
        if number == Reader.fail:
            raise ValueError("PRIVATE synthetic invalid candidate")
        self._load_engine()()
        text = "" if number == Reader.empty else f"PRIVATE synthetic candidate {number}"
        lines = [{"text": text, "score": .9, "box": [[0., 0.], [80., 0.], [80., 20.], [0., 20.]]}] if text else []
        return {"text": text, "lines": lines, "mean_confidence": .9 if lines else None,
                "raster": {"width": 100, "height": 100, "dpi": self.options["dpi"], "coordinate_system": "rendered_image_pixels"},
                "engine": {"name": "rapidocr", "version": model_artifacts.package_model("rapidocr").version,
                           "min_score": 0., "max_side": self.options["max_side"]}}


@pytest.fixture
def files(tmp_path, monkeypatch):
    source, output = tmp_path / "synthetic.pdf", tmp_path / "checkpoint"
    source.write_bytes(b"synthetic immutable input; no actual PDF or model needed")
    monkeypatch.setattr(workflow, "capture_identity", lambda **_kwargs: _identity())
    monkeypatch.setattr(model_artifacts, "verified_installed_package_model", lambda *_args: {})
    for name, value in (("calls", []), ("native_calls", []), ("closed", 0),
                        ("interrupt", None), ("fail", None), ("empty", None)):
        monkeypatch.setattr(Reader, name, value)
    return source, output


def run(files, **options):
    return workflow.run_checkpointed_pages(*files, reader_factory=Reader, **options)


def load(path):
    return json.loads(path.read_bytes())


def completion(files):
    _, _, _, specs, request = workflow.checkpoint_request(*files, resume=True)
    return workflow.read_checkpoint_completion(files[1], request=request, specs=specs)


def interrupt(files):
    Reader.interrupt = 2
    with pytest.raises(KeyboardInterrupt):
        run(files)
    Reader.interrupt = None


def test_uninterrupted_checkpoint_matches_ordinary_closed_report(files):
    result = run(files)
    reader = Reader(None, dpi=300, max_side=6000)
    expected = ocr_recovery.build_recovery_report(
        reader, source_sha256=hashlib.sha256(files[0].read_bytes()).hexdigest(), policy=ocr_recovery.RetryPolicy())
    expected["evidence_sha256"] = None
    assert result["report"] == expected
    assert validate_recovery_report(result["report"]) == expected
    assert completion(files) == result["manifest"]
    assert result["manifest"]["kind"] == "ocr_checkpoint_completion"
    assert result["manifest"]["coverage"] == {"selected_pages": [1, 2, 3], "committed_pages": [1, 2, 3],
                                              "interrupted_pages": [], "deferred_pages": [], "unstarted_pages": []}
    assert "PRIVATE" not in json.dumps(result["manifest"])
    assert not (files[1] / "execution.json").exists()
    for number in (1, 2, 3):
        saved = load(files[1] / f"page-{number:05d}.result.json")
        assert saved["origin_segment"] == saved["sealed_by_segment"] == 1
        assert saved["receipt"]["output_sha256"] == core.digest(saved["page"])
        assert saved["receipt"]["summary"] == {"attempted": 1, "completed": 1, "failed": 0}
        assert saved["receipt"]["ocr_executed"]  # Tiny fake engine, not a model/PDF operation.
        assert "PRIVATE" not in json.dumps(saved["receipt"])


def test_real_resume_preserves_committed_page_and_seals_interruption(files):
    interrupt(files)
    assert Reader.calls == [1, 2] and Reader.closed == 1
    assert not (files[1] / "manifest.json").exists()
    first = (files[1] / "page-00001.result.json").read_bytes()
    result = run(files, resume=True)
    assert Reader.calls == [1, 2, 3]  # Neither committed nor interrupted page is re-executed.
    assert Reader.native_calls == [1, 2, 3]  # Saved selection/baselines are reused.
    assert (files[1] / "page-00001.result.json").read_bytes() == first
    interrupted = load(files[1] / "page-00002.result.json")
    assert interrupted["state"] == "interrupted"
    assert interrupted["origin_segment"] == 1 and interrupted["sealed_by_segment"] == 2
    assert interrupted["receipt"] is None and interrupted["page"]["candidate"] is None
    assert result["report"]["pages"][1]["status"] == "retry_failed"
    assert result["manifest"]["coverage"]["interrupted_pages"] == [2]
    assert result["manifest"]["segments"][0]["unobserved_interrupted_pages"] == [2]
    assert result["manifest"]["segments"][1]["origin_pages"] == [3]
    assert completion(files) == result["manifest"]


@pytest.mark.parametrize("filename,expected_calls,interrupted_pages", [
    ("page-00001.result.json", [1, 2, 3], []),
    ("page-00002.start.json", [1, 3], [2]),
    ("segment-0001.end.json", [1, 2, 3], []),
    ("report.json", [1, 2, 3], []),
    ("manifest.json", [1, 2, 3], []),
])
def test_post_commit_cancel_boundaries_resume_without_duplicate_ocr(
        files, monkeypatch, filename, expected_calls, interrupted_pages):
    original = ocr_recovery._publish_new_report
    def cancel_after_commit(temporary, destination):
        original(temporary, destination)
        if Path(destination).name == filename:
            raise KeyboardInterrupt()
    monkeypatch.setattr(ocr_recovery, "_publish_new_report", cancel_after_commit)
    with pytest.raises(KeyboardInterrupt):
        run(files)
    monkeypatch.setattr(ocr_recovery, "_publish_new_report", original)
    preserved = {p.name: p.read_bytes() for p in files[1].glob("page-*.result.json")}
    result = run(files, resume=True)
    assert Reader.calls == expected_calls
    assert result["manifest"]["coverage"]["interrupted_pages"] == interrupted_pages
    if filename in {"segment-0001.end.json", "report.json", "manifest.json"}:
        assert len(result["manifest"]["segments"]) == 1
        assert Reader.closed == 1
    assert all((files[1] / name).read_bytes() == raw for name, raw in preserved.items())
    assert completion(files) == result["manifest"]


def test_empty_and_failed_outcomes_stay_local_and_are_not_retried(files):
    Reader.fail, Reader.empty = 1, 2
    result = run(files)
    assert [page["status"] for page in result["report"]["pages"]] == ["retry_failed", "empty_candidate", "review_required"]
    assert result["report"]["pages"][0]["original_text"] == ""
    assert result["report"]["pages"][0]["error_code"] == "retry_limit_or_validation"
    assert result["manifest"]["coverage"]["interrupted_pages"] == []
    run(files, resume=True)
    assert Reader.calls == [1, 2, 3]


def test_requested_priority_cap_and_deferred_coverage(files):
    result = run(files, requested_pages=(3, 1), policy=ocr_recovery.RetryPolicy(max_pages=1))
    assert Reader.calls == [1]
    assert result["manifest"]["coverage"]["selected_pages"] == [1]
    assert result["manifest"]["coverage"]["deferred_pages"] == [3, 2]


@pytest.mark.parametrize("options", [{"operation": "regions"}, {"operation": "hardscan"},
                                     {"recovery_path": Path("PRIVATE")}, {"plan_path": Path("PRIVATE")},
                                     {"requested_pages": (True,)}, {"requested_pages": (0,)},
                                     {"requested_pages": (1, 1)}, {"requested_pages": [1]},
                                     {"policy": {}}, {"resume": 1}])
def test_unsupported_requests_fail_before_directory_or_ocr(files, options):
    with pytest.raises(ValueError) as error:
        run(files, **options)
    assert "PRIVATE" not in str(error.value)
    assert Reader.calls == [] and not files[1].exists()


@pytest.mark.parametrize("mutation", ["source", "configuration", "runtime", "models", "producer"])
def test_generation_drift_blocks_resume_before_ocr_or_new_records(files, monkeypatch, mutation):
    interrupt(files)
    before = {p.name: p.read_bytes() for p in files[1].iterdir() if p.is_file()}
    options = {}
    if mutation == "source":
        files[0].write_bytes(b"different source")
    elif mutation == "configuration":
        options["policy"] = ocr_recovery.RetryPolicy(dpi=400)
    else:
        identity = _identity()
        if mutation == "runtime":
            identity["runtime_sha256"] = "0" * 64
        elif mutation == "models":
            identity["model_artifacts"][0]["sha256"] = "0" * 64
        else:
            identity["producer_sources"]["ocr_checkpoint.py"] = "0" * 64
        monkeypatch.setattr(workflow, "capture_identity", lambda **_kwargs: identity)
    with pytest.raises(ValueError, match="generation differs"):
        run(files, resume=True, **options)
    assert Reader.calls == [1, 2]
    assert before == {p.name: p.read_bytes() for p in files[1].iterdir() if p.is_file()}


def test_mutation_during_retry_preserves_start_but_does_not_commit_candidate(files, monkeypatch):
    original = Reader.retry
    def change(self, number):
        value = original(self, number)
        files[0].write_bytes(b"changed during OCR")
        return value
    monkeypatch.setattr(Reader, "retry", change)
    with pytest.raises(RuntimeError):
        run(files)
    assert (files[1] / "page-00001.start.json").exists()
    assert not (files[1] / "page-00001.result.json").exists()
    assert not (files[1] / "manifest.json").exists()


@pytest.mark.parametrize("field", ["page", "receipt", "origin", "unknown"])
def test_tampered_committed_record_is_rejected_before_resuming(files, field):
    interrupt(files)
    path = files[1] / "page-00001.result.json"
    value = load(path)
    if field == "page":
        value["page"]["original_text"] = "PRIVATE forged baseline"
    elif field == "receipt":
        value["receipt"]["summary"]["attempted"] = True
    elif field == "origin":
        value["origin_segment"] = 2
    else:
        value["PRIVATE"] = "PRIVATE"
    path.write_bytes(core.json_bytes(value))
    with pytest.raises(ValueError):
        run(files, resume=True)
    assert Reader.calls == [1, 2]
    assert not (files[1] / "segment-00002.start.json").exists()


def test_existing_generation_is_not_implicitly_resumed_or_overwritten(files):
    interrupt(files)
    with pytest.raises(ValueError):
        run(files)
    assert Reader.calls == [1, 2]


def test_incomplete_directory_without_request_does_not_fabricate_resume(files):
    files[1].mkdir()
    with pytest.raises((ValueError, OSError)):
        run(files, resume=True)
    assert Reader.calls == [] and list(files[1].iterdir()) == []


def test_hardlinks_in_generation_are_rejected(files):
    interrupt(files)
    os.link(files[1] / "page-00001.result.json", files[0].with_name("linked.json"))
    with pytest.raises(ValueError):
        run(files, resume=True)
    assert Reader.calls == [1, 2]


def test_linked_source_is_rejected_before_directory_creation(files):
    os.link(files[0], files[0].with_name("linked-source.pdf"))
    with pytest.raises(ValueError):
        run(files)
    assert not files[1].exists()


def test_staging_leftover_is_preserved_but_not_treated_as_committed(files):
    interrupt(files)
    leftover = files[1] / ".page-00002.result.json.abcd1234.tmp"
    leftover.write_bytes(b"PRIVATE incomplete JSON")
    run(files, resume=True)
    assert Reader.calls == [1, 2, 3]
    assert leftover.read_bytes() == b"PRIVATE incomplete JSON"


def test_unknown_or_oversized_leftovers_fail_closed(files, monkeypatch):
    interrupt(files)
    leftover = files[1] / "unrelated.txt"
    leftover.write_bytes(b"preserve")
    with pytest.raises(ValueError):
        run(files, resume=True)
    leftover.unlink()
    monkeypatch.setattr(core, "MAX_GENERATION_BYTES", 1)
    with pytest.raises(ValueError):
        run(files, resume=True)
    assert Reader.calls == [1, 2]


def test_writer_race_never_clobbers_uncooperative_file(files, monkeypatch):
    original = ocr_recovery._publish_new_report
    def race(temporary, destination):
        if Path(destination).name == "page-00001.result.json":
            Path(destination).write_bytes(b"preserve raced writer")
        original(temporary, destination)
    monkeypatch.setattr(ocr_recovery, "_publish_new_report", race)
    with pytest.raises(FileExistsError):
        run(files)
    assert (files[1] / "page-00001.result.json").read_bytes() == b"preserve raced writer"
    assert not (files[1] / "manifest.json").exists()


def test_checkpoint_cli_routes_quiet_contained_worker_and_strict_readback(files, monkeypatch, capsys):
    import process_supervision
    def supervise(_path, arguments, **options):
        assert "--checkpoint" in arguments and arguments[-1] == "--worker"
        assert options["stdout_target"] == options["stderr_target"] == cli.subprocess.DEVNULL
        assert options["environment_overrides"]["HF_HUB_OFFLINE"] == "1"
        run(files)
        return 3
    monkeypatch.setattr(process_supervision, "_run_cli_with_deadline", supervise)
    assert cli.main(["--pdf", str(files[0]), "--output-dir", str(files[1]), "--checkpoint"]) == 3
    output = capsys.readouterr()
    assert "origin evidence" in output.out and "PRIVATE" not in output.out + output.err


@pytest.mark.parametrize("flags", [["--resume"], ["--checkpoint", "--operation", "regions"],
                                   ["--checkpoint", "--operation", "hardscan"]])
def test_cli_unsupported_resume_does_not_start_worker(files, monkeypatch, capsys, flags):
    import process_supervision
    monkeypatch.setattr(process_supervision, "_run_cli_with_deadline", lambda *_a, **_k: pytest.fail("must not start"))
    assert cli.main(["--pdf", str(files[0]), "--output-dir", str(files[1]), *flags]) == 2
    assert "may remain" in capsys.readouterr().err and Reader.calls == []


def test_canonical_utf8_json_budget_matches_writer_size():
    payload = {"text": "\0é" * 40}
    expected = (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    assert core.json_bytes(payload, limit=len(expected)) == expected
    with pytest.raises(ValueError):
        core.json_bytes(payload, limit=len(expected) - 1)


@pytest.mark.parametrize("change", [lambda value: value.update(schema_version=True),
                                    lambda value: value["configuration"]["policy"].update(dpi=300.0),
                                    lambda value: value["identity"]["model_artifacts"][0].update(bytes=True)])
def test_strict_request_validation_rejects_numeric_coercions(files, change):
    *_, request = workflow.checkpoint_request(*files)
    changed = copy.deepcopy(request)
    change(changed)
    with pytest.raises(ValueError):
        core.validate_request(changed)


def test_ordinary_recovery_retains_inspect_all_then_retry_order():
    events = []
    class OrderedReader:
        page_count = 3
        def native_text(self, number):
            events.append(("native", number))
            return ""
        def retry(self, number):
            events.append(("retry", number))
            raise ValueError("synthetic local failure")
    report = ocr_recovery.build_recovery_report(
        OrderedReader(), source_sha256="a" * 64,
        policy=ocr_recovery.RetryPolicy(max_pages=2), requested_pages=(3,))
    assert events == [("native", 3), ("native", 1), ("native", 2), ("retry", 3), ("retry", 1)]
    assert [page["page_number"] for page in report["pages"]] == [3, 1]
    assert [page["page_number"] for page in report["deferred_pages"]] == [2]


@pytest.mark.parametrize("stage", ["native", "retry"])
def test_ordinary_recovery_cancellation_retains_pre_seam_call_sequence(stage):
    events = []
    class CancellingReader:
        page_count = 3
        def native_text(self, number):
            events.append(("native", number))
            if stage == "native" and number == 2:
                raise KeyboardInterrupt()
            return ""
        def retry(self, number):
            events.append(("retry", number))
            if number == 2:
                raise KeyboardInterrupt()
            raise ValueError("synthetic local failure")
    with pytest.raises(KeyboardInterrupt):
        ocr_recovery.build_recovery_report(CancellingReader(), source_sha256="a" * 64,
                                           policy=ocr_recovery.RetryPolicy())
    expected = ([("native", 1), ("native", 2)] if stage == "native" else
                [("native", 1), ("native", 2), ("native", 3), ("retry", 1), ("retry", 2)])
    assert events == expected


@pytest.mark.parametrize("mutation", ["evidence", "lock"])
def test_evidence_and_lock_changes_block_resume_before_ocr(files, tmp_path, monkeypatch, mutation):
    options = {}
    if mutation == "evidence":
        path = tmp_path / "evidence.json"
        path.write_bytes(core.json_bytes({"schema_version": 1,
            "source_sha256": hashlib.sha256(files[0].read_bytes()).hexdigest(), "pages": []}))
        options["evidence_path"] = path
    else:
        locks = tmp_path / "locks"
        locks.mkdir()
        for name in ("requirements-full.lock", "requirements-test.lock", "requirements-lock-tools.lock",
                     "model-artifact-policy.json", "model-artifacts.lock.json"):
            (locks / name).write_bytes(b"synthetic lock stand-in")
        monkeypatch.setattr(workflow, "ROOT", locks)
        path = locks / "requirements-full.lock"
    Reader.interrupt = 2
    with pytest.raises(KeyboardInterrupt):
        run(files, **options)
    Reader.interrupt = None
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="generation differs"):
        run(files, resume=True, **options)
    assert Reader.calls == [1, 2]


def test_removed_completed_page_cannot_be_silently_reexecuted(files):
    interrupt(files)
    (files[1] / "page-00001.result.json").unlink()
    with pytest.raises(ValueError, match="sequential commit order"):
        run(files, resume=True)
    assert Reader.calls == [1, 2]


def test_receipt_cannot_claim_candidate_without_actual_call(files):
    interrupt(files)
    path = files[1] / "page-00001.result.json"
    value = load(path)
    value["receipt"]["calls"] = []
    value["receipt"]["summary"] = {"attempted": 0, "completed": 0, "failed": 0}
    value["receipt"]["ocr_executed"] = False
    path.write_bytes(core.json_bytes(value))
    with pytest.raises(ValueError, match="completed engine call"):
        run(files, resume=True)
    assert Reader.calls == [1, 2]


def test_page_and_directory_budgets_fail_before_claimed_completion(files, monkeypatch):
    monkeypatch.setattr(core, "MAX_PAGE_BYTES", 1)
    with pytest.raises(ValueError, match="byte budget"):
        run(files)
    assert not (files[1] / "page-00001.result.json").exists()
    assert not (files[1] / "manifest.json").exists()


def test_symlinked_generation_is_not_followed(files):
    target = files[1].with_name("target")
    target.mkdir()
    try:
        files[1].symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises((ValueError, OSError, RuntimeError)):
        run(files, resume=True)
    assert Reader.calls == [] and list(target.iterdir()) == []


def test_lease_excludes_second_checkpoint_writer(files):
    from concurrent.futures import ThreadPoolExecutor
    from resource_lease import PathLease
    with PathLease(files[1], backend="ocr-checkpoint", collection_name="generation", operation="test",
                   timeout=0, resource_description="synthetic checkpoint"):
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(run, files)
            with pytest.raises(TimeoutError):
                future.result(timeout=10)
    assert Reader.calls == [] and not files[1].exists()


def test_rebound_old_origin_cannot_follow_an_interruption_sealed_by_new_segment(files):
    interrupt(files)
    run(files, resume=True)
    start_path, result_path = files[1] / "page-00003.start.json", files[1] / "page-00003.result.json"
    start, result = load(start_path), load(result_path)
    start["origin_segment"] = 1
    start["segment_sha256"] = hashlib.sha256((files[1] / "segment-0001.start.json").read_bytes()).hexdigest()
    start_path.write_bytes(core.json_bytes(start))
    new_start_digest = hashlib.sha256(start_path.read_bytes()).hexdigest()
    result.update(origin_segment=1, sealed_by_segment=1, start_sha256=new_start_digest)
    result["receipt"]["inputs"]["page_start_sha256"] = new_start_digest
    result_path.write_bytes(core.json_bytes(result))
    with pytest.raises(ValueError, match="origin segments contradict page order"):
        run(files, resume=True)
    assert Reader.calls == [1, 2, 3]


def test_finished_segment_cannot_hide_unstarted_selected_pages(files):
    interrupt(files)
    (files[1] / "page-00002.start.json").unlink()
    request_digest = hashlib.sha256((files[1] / "request.json").read_bytes()).hexdigest()
    (files[1] / "segment-0001.end.json").write_bytes(core.json_bytes({
        "schema_version": 1, "kind": "ocr_checkpoint_segment_end", "request_sha256": request_digest,
        "segment_number": 1, "status": "finished"}))
    with pytest.raises(ValueError, match="earlier finished segment"):
        run(files, resume=True)
    assert Reader.calls == [1, 2]
