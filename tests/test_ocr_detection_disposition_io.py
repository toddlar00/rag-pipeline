"""Bundle safety with real no-dispatch orchestration and synthetic identity pins.

These controls do not load a PDF parser or model. Runtime metadata/source
generation capture is replaced explicitly; report, receipt and diagnostic
validation and the private publication/readback graph are real.
"""

import copy
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import sys
from types import MappingProxyType, SimpleNamespace

import pytest

import ocr_detection_disposition_io as workflow
import ocr_execution_receipt as receipt_module
import ocr_recovery
import ocr_recovery_runtime
import storage_policy


class FailedReader:
    page_count = 3
    min_score = 0.0
    attempts = []

    def __init__(self, _path, **_options):
        self._engine = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    def close(self):
        pass

    def native_text(self, page):
        return "" if page != 2 else "Enough native text to remain unselected unless explicitly requested."

    def retry(self, page):
        self.attempts.append(page)
        raise RuntimeError("PRIVATE synthetic pre-dispatch failure")


@pytest.fixture
def case(tmp_path, monkeypatch):
    source, output = tmp_path / "source.pdf", tmp_path / "bundle"
    source.write_bytes(b"generated PDF stand-in; no parser or model")
    generation = {"schema_version": 1, "kind": "ocr_disposition_producer", "upstream": {"fixture": "a" * 64},
                  "identity": {"environment_sha256": receipt_module.environment_identity(Path(sys.prefix)),
                               "producer_sources": {"ocr_detection_disposition_io.py": "b" * 64}}}
    monkeypatch.setattr(workflow, "capture_producer_generation", lambda **_kw: copy.deepcopy(generation))
    monkeypatch.setattr(receipt_module, "_loaded_source_digests", lambda: {"ocr_detection_disposition_io": "b" * 64})
    monkeypatch.setattr(ocr_recovery_runtime, "RapidOCRPageReader", FailedReader)
    monkeypatch.setattr(FailedReader, "attempts", [])
    return SimpleNamespace(source=source, output=output, generation=generation)


def _run(case, **options):
    return workflow.run_disposition_bundle(case.source, case.output, **options)


def _request(case, **options):
    return workflow.disposition_request(case.source, case.output, **options)


def _read(case, request):
    return workflow.read_disposition_completion(case.output, request=request)


def test_no_dispatch_bundle_preserves_complete_coverage_and_exact_legacy_bytes(case):
    request = _request(case, requested_pages=(3,), policy=ocr_recovery.RetryPolicy(max_pages=1))
    result = _run(case, requested_pages=(3,), policy=request.policy)
    assert FailedReader.attempts == [3]
    assert result == _read(case, request)
    assert result == _read(case, _request(case, readback=True, requested_pages=(3,), policy=request.policy))
    assert (case.output / "report.json").read_bytes() == (case.output / "work" / "report.json").read_bytes()
    assert result["report"]["schema_version"] == 1
    assert result["execution"]["schema_version"] == 1
    assert result["execution"]["calls"] == []
    assert result["disposition"]["attempt_order"] == ["page-00003"]
    assert result["disposition"]["dispatch_order"] == []
    assert [item["selection"] for item in result["disposition"]["items"]] == ["deferred", "not_selected", "selected"]
    assert result["disposition"]["summary"]["diagnostics_not_run"] == 3
    assert result["disposition"]["summary"]["detections_unknown_items"] == 3
    assert result["manifest"]["accuracy_verified"] is False
    assert not any("PRIVATE" in json.dumps(result[name]) for name in result)


@pytest.mark.parametrize("options", [
    {"operation": "PRIVATE"}, {"policy": {}}, {"policy": False}, {"requested_pages": [1]},
    {"requested_pages": (True,)}, {"requested_pages": (0,)}, {"requested_pages": (5001,)},
    {"requested_pages": (1, 1)}, {"requested_pages": tuple(range(1, 22))},
    {"readback": 1}, {"operation": "regions"}, {"operation": "hardscan"},
    {"recovery_path": Path("PRIVATE")},
])
def test_invalid_admission_never_writes_or_retries(case, options):
    with pytest.raises(ValueError):
        _request(case, **options)
    assert not case.output.exists() and FailedReader.attempts == []


@pytest.mark.parametrize("change", [
    lambda r: replace(r, specs=()),
    lambda r: replace(r, specs=r.specs[:-1]),
    lambda r: replace(r, specs=r.specs + (r.specs[0],)),
    lambda r: replace(r, policy=ocr_recovery.RetryPolicy(max_pages=1)),
    lambda r: replace(r, pdf_path=Path("relative.pdf")),
    lambda r: replace(r, input_identities={}),
])
def test_recheck_rejects_forged_frozen_dataclass_fields(case, change):
    request = _request(case)
    with pytest.raises(ValueError):
        workflow.recheck_disposition_request(change(request))


def test_empty_specs_cannot_bypass_replaced_source_at_readback(case):
    request = _request(case)
    _run(case)
    case.source.write_bytes(b"different source")
    with pytest.raises(ValueError):
        _read(case, replace(request, specs=()))
    with pytest.raises(RuntimeError):
        _read(case, request)


def test_recheck_detects_nested_request_and_producer_changes(case):
    request = _request(case)
    request.payload["configuration"]["policy"]["max_pages"] = 1
    with pytest.raises(ValueError):
        workflow.recheck_disposition_request(request)
    request = _request(case)
    case.generation["upstream"]["fixture"] = "c" * 64
    with pytest.raises(RuntimeError):
        workflow.recheck_disposition_request(request)


def test_input_identity_snapshot_is_immutable_and_replacement_is_bound(case):
    request = _request(case)
    with pytest.raises(TypeError):
        request.input_identities["source_sha256"] = (0, 0, 0, 0)
    replacement = case.source.with_name("same-bytes.pdf")
    replacement.write_bytes(case.source.read_bytes())
    os.replace(replacement, case.source)
    changed = dict(request.input_identities)
    changed["source_sha256"] = workflow._file_identity(case.source)
    with pytest.raises(ValueError):
        workflow.recheck_disposition_request(replace(request, input_identities=MappingProxyType(changed)))


def test_parent_request_mismatch_prevents_any_output_or_ocr(case):
    with pytest.raises(ValueError):
        workflow.run_disposition_bundle(case.source, case.output, expected_request_sha256="f" * 64)
    assert not case.output.exists() and FailedReader.attempts == []


def test_existing_directory_and_hardlinked_inputs_are_preserved(case):
    case.output.mkdir()
    sentinel = case.output / "user-file"
    sentinel.write_bytes(b"preserve")
    with pytest.raises(ValueError):
        _run(case)
    assert sentinel.read_bytes() == b"preserve"
    alias = case.source.with_name("alias.pdf")
    os.link(case.source, alias)
    with pytest.raises(ValueError):
        workflow.disposition_request(case.source, case.output.with_name("new"))
    assert alias.read_bytes() == case.source.read_bytes()


@pytest.mark.parametrize("stage", ["report.json", "execution.json", "disposition.json", "manifest.json"])
@pytest.mark.parametrize("target", ["source", "producer", "work"])
def test_late_input_or_work_mutation_prevents_completion(case, monkeypatch, stage, target):
    original = storage_policy.atomic_write_private

    def write(path, callback, **kwargs):
        if Path(path).parent == case.output and Path(path).name == stage:
            if target == "source":
                case.source.write_bytes(b"changed")
            elif target == "producer":
                case.generation["upstream"]["fixture"] = "d" * 64
            else:
                (case.output / "work" / "report.json").write_bytes(b"changed")
        return original(path, callback, **kwargs)

    monkeypatch.setattr(storage_policy, "atomic_write_private", write)
    with pytest.raises((ValueError, RuntimeError)):
        _run(case)
    assert not (case.output / "manifest.json").exists()
    assert FailedReader.attempts == [1, 3]


@pytest.mark.parametrize("target", ["report.json", "execution.json", "disposition.json"])
def test_prior_final_artifact_mutation_prevents_manifest(case, monkeypatch, target):
    original = storage_policy.atomic_write_private

    def write(path, callback, **kwargs):
        if Path(path).name == "manifest.json":
            (case.output / target).write_bytes(b"changed")
        return original(path, callback, **kwargs)

    monkeypatch.setattr(storage_policy, "atomic_write_private", write)
    with pytest.raises((ValueError, RuntimeError)):
        _run(case)
    assert not (case.output / "manifest.json").exists()


@pytest.mark.parametrize("stage", ["report.json", "execution.json", "disposition.json", "manifest.json"])
def test_corrupt_staged_bytes_are_not_published(case, monkeypatch, stage):
    original = storage_policy.atomic_write_private

    def write(path, callback, **kwargs):
        if Path(path).parent == case.output and Path(path).name == stage:
            def callback(handle):
                handle.write(b"corrupt")
        return original(path, callback, **kwargs)

    monkeypatch.setattr(storage_policy, "atomic_write_private", write)
    with pytest.raises(RuntimeError):
        _run(case)
    assert not (case.output / stage).exists()


@pytest.mark.parametrize("stage", ["report.json", "execution.json", "disposition.json", "manifest.json"])
def test_destination_race_preserves_other_writer_file(case, monkeypatch, stage):
    original = storage_policy.atomic_write_private

    def write(path, callback, **kwargs):
        if Path(path).parent == case.output and Path(path).name == stage:
            Path(path).write_bytes(b"other writer")
        return original(path, callback, **kwargs)

    monkeypatch.setattr(storage_policy, "atomic_write_private", write)
    with pytest.raises(FileExistsError):
        _run(case)
    assert (case.output / stage).read_bytes() == b"other writer"
    assert FailedReader.attempts == [1, 3]


@pytest.mark.parametrize("stage", ["report.json", "execution.json", "disposition.json", "manifest.json"])
@pytest.mark.parametrize("after_commit", [False, True])
def test_cancellation_propagates_without_restarting_ocr(case, monkeypatch, stage, after_commit):
    original = ocr_recovery._publish_new_report

    def publish(temporary, destination):
        if Path(destination).parent == case.output and Path(destination).name == stage:
            if after_commit:
                original(temporary, destination)
            raise KeyboardInterrupt()
        return original(temporary, destination)

    monkeypatch.setattr(ocr_recovery, "_publish_new_report", publish)
    with pytest.raises(KeyboardInterrupt):
        _run(case)
    assert FailedReader.attempts == [1, 3]
    assert (case.output / stage).exists() is after_commit
    assert (case.output / "work" / "report.json").exists()


@pytest.mark.parametrize("name", ["report.json", "execution.json", "disposition.json", "manifest.json", "work/report.json"])
def test_fresh_readback_rejects_changed_artifacts(case, name):
    request = _request(case)
    _run(case)
    (case.output / name).write_bytes(b"{}")
    with pytest.raises((ValueError, RuntimeError, KeyError)):
        _read(case, request)


def test_digest_consistent_manifest_cannot_replace_requested_policy(case):
    request = _request(case)
    result = _run(case)
    result["manifest"]["request"]["configuration"]["policy"]["max_pages"] = 1
    (case.output / "manifest.json").write_bytes(workflow._encoded(result["manifest"], 512 * 1024))
    with pytest.raises(ValueError):
        _read(case, request)


@pytest.mark.parametrize("raw", [b'{"PRIVATE":1,"PRIVATE":2}', b'{"x":"\\ud800"}', b'{"x":NaN}', b'{"x":1e999}'])
def test_json_reader_rejects_invalid_data_without_echo(case, raw):
    path = case.source.with_name("invalid.json")
    path.write_bytes(raw)
    with pytest.raises(ValueError) as error:
        workflow._json(path, 1024)
    assert str(error.value) == "invalid disposition JSON artifact"


def test_compact_encoding_limit_includes_utf8_and_final_newline():
    raw = workflow._encoded({"text": "é"}, 100)
    assert raw == b'{"text":"\xc3\xa9"}\n'
    assert workflow._encoded({"text": "é"}, len(raw)) == raw
    with pytest.raises(ValueError):
        workflow._encoded({"text": "é"}, len(raw) - 1)


def test_upstream_generation_requires_exact_pinned_bytes(tmp_path, monkeypatch):
    path = tmp_path / "source.py"
    path.write_bytes(b"inert upstream fixture")
    pins = {"source.py": hashlib.sha256(path.read_bytes()).hexdigest()}
    monkeypatch.setattr(workflow, "_UPSTREAM_PINS", pins)
    monkeypatch.setattr(workflow.importlib.metadata, "distribution", lambda _name: SimpleNamespace(
        version="3.9.2", locate_file=lambda name: tmp_path / name))
    monkeypatch.setattr(workflow, "capture_identity", lambda **_kw: {"fixture": True})
    assert workflow.capture_producer_generation()["upstream"] == pins
    path.write_bytes(b"changed upstream fixture")
    with pytest.raises(ValueError):
        workflow.capture_producer_generation()


def test_unsupported_project_entrypoint_refuses_before_input_reads(case, monkeypatch):
    monkeypatch.setattr(sys.modules["__main__"], "__file__", str(workflow.ROOT / "tools" / "other.py"))
    monkeypatch.setattr(workflow, "_snapshot", lambda *_a, **_kw: pytest.fail("must refuse before input reads"))
    with pytest.raises(ValueError, match="fixed CLI"):
        _request(case)
    assert not case.output.exists() and FailedReader.attempts == []


@pytest.mark.parametrize("relative", ["tools/review_ocr.py", "tools/../tools/review_ocr.py"])
def test_review_host_can_prepare_but_cannot_execute_in_process(case, monkeypatch, relative):
    monkeypatch.setattr(sys.modules["__main__"], "__file__", str(workflow.ROOT / relative))
    request = _request(case, requested_pages=(1,))
    assert request.payload["configuration"]["requested_pages"] == [1]
    assert not case.output.exists() and FailedReader.attempts == []
    monkeypatch.setattr(workflow, "_snapshot", lambda *_a, **_kw: pytest.fail("must refuse before input reads"))
    with pytest.raises(ValueError, match="fixed CLI"):
        _run(case, requested_pages=(1,))
    assert not case.output.exists() and FailedReader.attempts == []


@pytest.mark.parametrize("relative", ["review_ocr.py", "tools/other.py", "tools/review_ocr_copy.py",
                                      "tools/../other.py", "tools/../tools/other.py"])
def test_review_request_admission_does_not_admit_other_project_paths(case, monkeypatch, relative):
    monkeypatch.setattr(sys.modules["__main__"], "__file__", str(workflow.ROOT / relative))
    monkeypatch.setattr(workflow, "_snapshot", lambda *_a, **_kw: pytest.fail("must refuse before input reads"))
    with pytest.raises(ValueError, match="fixed CLI"):
        _request(case)
    with pytest.raises(ValueError, match="fixed CLI"):
        _run(case)
    assert not case.output.exists() and FailedReader.attempts == []


@pytest.mark.parametrize("host", ["diagnostic", "external"])
def test_existing_execution_hosts_remain_admitted(case, monkeypatch, host):
    entrypoint = (workflow.ROOT / "tools" / "diagnose_ocr_dispositions.py" if host == "diagnostic"
                  else case.source.parent / "external_host.py")
    monkeypatch.setattr(sys.modules["__main__"], "__file__", str(entrypoint))
    request = _request(case)
    result = _run(case)
    assert result == _read(case, request)
    assert result["execution"]["calls"] == []


def test_review_host_readback_still_checks_independently_supplied_request(case, monkeypatch):
    request = _request(case)
    _run(case)
    monkeypatch.setattr(sys.modules["__main__"], "__file__", str(workflow.ROOT / "tools" / "review_ocr.py"))
    assert _read(case, request) == _read(case, _request(case, readback=True))
    case.source.write_bytes(b"changed reviewed source")
    with pytest.raises(RuntimeError):
        _read(case, request)


@pytest.mark.parametrize("target", ["source", "work/report.json", "report.json", "execution.json", "disposition.json"])
def test_same_byte_replacement_between_commits_is_detected(case, monkeypatch, target):
    original = storage_policy.atomic_write_private

    def write(path, callback, **kwargs):
        if Path(path).name == "manifest.json":
            victim = case.source if target == "source" else case.output / target
            replacement = victim.with_name("replacement.bin")
            replacement.write_bytes(victim.read_bytes())
            os.replace(replacement, victim)
        return original(path, callback, **kwargs)

    monkeypatch.setattr(storage_policy, "atomic_write_private", write)
    with pytest.raises(RuntimeError):
        _run(case)
    assert not (case.output / "manifest.json").exists()


@pytest.mark.parametrize("directory", ["bundle", "work"])
def test_directory_replacement_between_commits_is_detected(case, monkeypatch, directory):
    original = storage_policy.atomic_write_private

    def write(path, callback, **kwargs):
        if Path(path).name == "manifest.json":
            victim = case.output if directory == "bundle" else case.output / "work"
            victim.rename(victim.with_name(victim.name + "-preserved"))
            victim.mkdir()
        return original(path, callback, **kwargs)

    monkeypatch.setattr(storage_policy, "atomic_write_private", write)
    with pytest.raises(RuntimeError):
        _run(case)
    assert not (case.output / "manifest.json").exists()


@pytest.mark.parametrize("stage", ["report.json", "execution.json", "disposition.json", "manifest.json"])
def test_post_link_cleanup_error_does_not_retry_or_hide_published_artifact(case, monkeypatch, stage):
    original = ocr_recovery._publish_new_report

    def publish(temporary, destination):
        original(temporary, destination)
        if Path(destination).parent == case.output and Path(destination).name == stage:
            raise ocr_recovery.ReportCleanupError("synthetic post-link cleanup failure")

    monkeypatch.setattr(ocr_recovery, "_publish_new_report", publish)
    with pytest.raises(ocr_recovery.ReportCleanupError):
        _run(case)
    assert (case.output / stage).exists()
    assert FailedReader.attempts == [1, 3]
