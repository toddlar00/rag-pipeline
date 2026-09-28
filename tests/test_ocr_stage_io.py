"""Strict stage bundle bindings/publication with synthetic, non-OCR observations."""

import copy
import hashlib
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace as NS

import pytest

import ocr_stage_io as workflow
import ocr_stage_runtime as runtime
import storage_policy


def _hash(value):
    return hashlib.sha256(value).hexdigest()


@pytest.fixture
def setup(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    for name in ("requirements-full.lock", "requirements-test.lock", "requirements-lock-tools.lock",
                 "model-artifact-policy.json", "model-artifacts.lock.json"):
        (repo / name).write_bytes(b"synthetic lock " + name.encode())
    source, references, installation, output = (tmp_path / name for name in ("source.pdf", "gold.json", "installation.json", "bundle"))
    source.write_bytes(b"generated-only source stand-in")
    installation.write_bytes(b'{"synthetic":true}')
    ref = {"schema_version": 1, "kind": "ocr_stage_reference", "source_sha256": _hash(source.read_bytes()),
           "page_count": 1, "scope": "operator_declared_complete_selected_pages",
           "coordinate_system": "original_page_display_fraction", "annotation_provenance": "synthetic_generator",
           "pages": [{"page_number": 1, "geometry": {"width_points": 24, "height_points": 24, "rotation": 0},
                      "lines": [{"region_id": "line-1", "bbox": [.1, .1, .9, .2], "text": "PRIVATE synthetic gold",
                                 "order": 0, "cell_id": None}], "cells": [], "recognition_region_ids": ["line-1"]}]}
    references.write_text(json.dumps(ref), encoding="utf-8")
    generation = {"environment_sha256": "c" * 64, "producer_sources": {"ocr_stage_runtime.py": "d" * 64}}
    monkeypatch.setattr(workflow, "ROOT", repo)
    monkeypatch.setattr(workflow, "capture_identity", lambda **_kwargs: copy.deepcopy(generation))
    monkeypatch.setattr(workflow, "validate_installation_evidence", lambda _path: ({}, _hash(installation.read_bytes())))
    observed_calls, validated_receipts = [], []

    def collect(raw, ref, *, reference_sha256, configuration):
        observed_calls.append(raw)
        observation = {"schema_version": 1, "kind": "ocr_stage_observation", "source_sha256": ref["source_sha256"],
                       "reference_sha256": reference_sha256, "page_count": 1, "configuration": configuration,
                       "pages": [{"page_number": 1, "geometry": None, "raster": None,
                                  "detection": {"status": "unavailable", "reason": "geometry_unavailable", "call_id": None, "boxes": []},
                                  "full_page": {"status": "unavailable", "reason": "geometry_unavailable", "call_id": None, "lines": []},
                                  "gold_crops": [{"region_id": "line-1", "status": "unavailable", "reason": "geometry_unavailable",
                                                  "call_id": None, "raster_bbox": None, "pixel_sha256": None, "text": None}]}],
                       "calls": [], "canonical_extraction_modified": False, "requires_attention": True}
        return observation, NS(observation=None, receipt_calls=[])

    def capture(**kwargs):
        return {"schema_version": 1, "kind": "ocr_execution_receipt", "operation": kwargs["operation"],
                "inputs": kwargs["inputs"], "output_sha256": kwargs["output_sha256"],
                "installation_sha256": kwargs["inputs"]["installation_sha256"],
                "environment_identity_sha256": generation["environment_sha256"], "calls": kwargs["calls"],
                "project_sources_at_observation": {"ocr_stage_runtime": "d" * 64}}

    monkeypatch.setattr(runtime, "collect_stage_observation", collect)
    monkeypatch.setattr(workflow, "capture_execution_receipt", capture)
    monkeypatch.setattr(workflow, "validate_execution_receipt", lambda payload, **_kwargs: validated_receipts.append(payload))
    return NS(source=source, references=references, installation=installation, output=output, ref=ref,
              generation=generation, observed_calls=observed_calls, validated_receipts=validated_receipts)


def request(setup):
    return workflow.stage_request(setup.source, setup.references, setup.output, installation_path=setup.installation)


def run(setup):
    return workflow.run_stage_bundle(setup.source, setup.references, setup.output, installation_path=setup.installation)


def rebound(setup, filename, mutate):
    path = setup.output / filename
    value = json.loads(path.read_text(encoding="utf-8"))
    mutate(value)
    path.write_text(json.dumps(value), encoding="utf-8")
    if filename != "manifest.json":
        manifest = setup.output / "manifest.json"
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        payload[filename.removesuffix(".json") + "_sha256"] = _hash(path.read_bytes())
        manifest.write_text(json.dumps(payload), encoding="utf-8")


def test_bundle_is_create_only_private_and_readback_rebuilds_unavailable_coverage(setup):
    expected = request(setup)
    before = setup.source.read_bytes()
    result = run(setup)
    assert workflow.read_stage_completion(setup.output, request=expected) == result
    assert setup.source.read_bytes() == before and setup.observed_calls == [before]
    assert {path.name for path in setup.output.iterdir() if path.is_file()} == {
        "observation.json", "execution.json", "diagnostics.json", "manifest.json"}
    assert result["diagnostics"]["coverage"]["selected_gold_crops"] == 1
    assert result["diagnostics"]["coverage"]["available_gold_crops"] == 0
    assert result["manifest"]["requires_attention"] is True
    assert "PRIVATE" not in json.dumps(result)
    assert len(setup.validated_receipts) == 2
    assert result["manifest"]["inputs"]["reference_sha256"] == _hash(setup.references.read_bytes())
    if os.name != "nt":
        assert setup.output.stat().st_mode & 0o777 == 0o700
        assert (setup.output / "manifest.json").stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError):
        run(setup)
    fresh = workflow.stage_request(setup.source, setup.references, setup.output,
                                   installation_path=setup.installation, readback=True)
    assert workflow.read_stage_completion(setup.output, request=fresh) == result


def test_readback_admission_never_creates_or_resumes_directory(setup):
    with pytest.raises(ValueError):
        workflow.stage_request(setup.source, setup.references, setup.output,
                                installation_path=setup.installation, readback=True)
    assert not setup.output.exists()


def test_wrong_first_party_launcher_rejected_before_ocr(setup, monkeypatch):
    monkeypatch.setattr(sys.modules["__main__"], "__file__", str(workflow.ROOT / "other.py"), raising=False)
    with pytest.raises(ValueError):
        request(setup)
    assert not setup.observed_calls and not setup.output.exists()


@pytest.mark.parametrize("target", ["source", "references", "installation"])
def test_changed_inputs_rejected_on_parent_readback(setup, target):
    expected = request(setup)
    run(setup)
    getattr(setup, target).write_bytes(b"changed synthetic input")
    with pytest.raises((ValueError, RuntimeError)):
        workflow.read_stage_completion(setup.output, request=expected)


@pytest.mark.parametrize("filename,target", [("observation.json", "source"), ("execution.json", "references"),
                                              ("diagnostics.json", "installation"), ("manifest.json", "source"),
                                              ("manifest.json", "observation.json"), ("manifest.json", "execution.json"),
                                              ("manifest.json", "diagnostics.json")])
def test_changes_inside_final_commit_block_completion(setup, monkeypatch, filename, target):
    original = storage_policy.atomic_write_private_json

    def write(path, payload, **kwargs):
        commit = kwargs["replace_fn"]
        def replace(temporary, destination):
            if Path(path).name == filename:
                changed = setup.output / target if target.endswith(".json") else getattr(setup, target)
                changed.write_bytes(b"changed during private serialization")
            commit(temporary, destination)
        return original(path, payload, **{**kwargs, "replace_fn": replace})

    monkeypatch.setattr(storage_policy, "atomic_write_private_json", write)
    with pytest.raises((ValueError, RuntimeError)):
        run(setup)
    assert not (setup.output / "manifest.json").exists()


def test_generation_change_prevents_publication(setup, monkeypatch):
    original = storage_policy.atomic_write_private_json
    def write(path, payload, **kwargs):
        setup.generation["producer_sources"]["ocr_stage_runtime.py"] = "e" * 64
        return original(path, payload, **kwargs)
    monkeypatch.setattr(storage_policy, "atomic_write_private_json", write)
    with pytest.raises(RuntimeError):
        run(setup)
    assert not (setup.output / "manifest.json").exists()


def test_publication_collision_preserves_other_writer_file(setup, monkeypatch):
    original = storage_policy.atomic_write_private_json
    def write(path, payload, **kwargs):
        if Path(path).name == "manifest.json":
            Path(path).write_bytes(b"preserve other writer")
        return original(path, payload, **kwargs)
    monkeypatch.setattr(storage_policy, "atomic_write_private_json", write)
    with pytest.raises(FileExistsError):
        run(setup)
    assert (setup.output / "manifest.json").read_bytes() == b"preserve other writer"


@pytest.mark.parametrize("mutate", [lambda value: value.update(schema_version=True),
                                    lambda value: value.update(canonical_extraction_modified=True),
                                    lambda value: value.update(requires_attention=False),
                                    lambda value: value.update(scope="PRIVATE forged scope"),
                                    lambda value: value["configuration"].update(dpi=True),
                                    lambda value: value["generation"].update(environment_sha256="f" * 64),
                                    lambda value: value["inputs"].update(reference_sha256="f" * 64),
                                    lambda value: value.update(extra="PRIVATE")])
def test_strict_manifest_rejects_type_and_binding_forgeries(setup, mutate):
    expected = request(setup)
    run(setup)
    rebound(setup, "manifest.json", mutate)
    with pytest.raises(ValueError):
        workflow.read_stage_completion(setup.output, request=expected)


@pytest.mark.parametrize("mutate", [lambda value: value.update(operation="pages"),
                                    lambda value: value.update(output_sha256="f" * 64),
                                    lambda value: value.update(installation_sha256="f" * 64),
                                    lambda value: value.update(environment_identity_sha256="f" * 64),
                                    lambda value: value["project_sources_at_observation"].update(ocr_stage_runtime="f" * 64),
                                    lambda value: value["calls"].append({"id": "call-0001", "status": "completed"})])
def test_receipt_outer_binding_cannot_be_repaired_by_manifest_rehash(setup, mutate):
    expected = request(setup)
    run(setup)
    rebound(setup, "execution.json", mutate)
    with pytest.raises(ValueError):
        workflow.read_stage_completion(setup.output, request=expected)


def test_diagnostics_are_rebuilt_not_trusted_after_rebound_digest(setup):
    expected = request(setup)
    run(setup)
    rebound(setup, "diagnostics.json", lambda value: value["coverage"].update(available_gold_crops=1))
    with pytest.raises(ValueError):
        workflow.read_stage_completion(setup.output, request=expected)


def test_post_validation_change_prevents_readback_return(setup, monkeypatch):
    expected = request(setup)
    run(setup)
    original = workflow.evaluate_stage_diagnostics
    def evaluate(*args):
        result = original(*args)
        (setup.output / "observation.json").write_bytes(b"concurrent replacement")
        return result
    monkeypatch.setattr(workflow, "evaluate_stage_diagnostics", evaluate)
    with pytest.raises((ValueError, RuntimeError)):
        workflow.read_stage_completion(setup.output, request=expected)


def test_input_aliases_hardlinks_and_missing_installation_fail_before_output(setup):
    with pytest.raises(ValueError):
        workflow.stage_request(setup.source, setup.source, setup.output, installation_path=setup.installation)
    with pytest.raises(ValueError):
        workflow.stage_request(setup.source, setup.references, setup.output, installation_path=None)
    alias = setup.source.with_name("hardlink")
    os.link(setup.source, alias)
    with pytest.raises(ValueError):
        request(setup)
    assert not setup.output.exists()


def test_symlink_input_is_rejected(setup):
    alias = setup.source.with_name("symlink")
    try:
        alias.symlink_to(setup.source)
    except OSError:
        pytest.skip("symlink creation unavailable")
    with pytest.raises((ValueError, OSError)):
        workflow.stage_request(alias, setup.references, setup.output, installation_path=setup.installation)


@pytest.mark.parametrize("raw", [b"", b"{", b'{"x":1,"x":2}', b'{"x":NaN}', b"\xff"])
def test_invalid_reference_json_never_starts_ocr(setup, raw):
    setup.references.write_bytes(raw)
    with pytest.raises(ValueError):
        request(setup)
    assert not setup.output.exists() and not setup.observed_calls


def test_snapshot_exact_bound_and_oversize(tmp_path):
    path = tmp_path / "bounded"
    path.write_bytes(b"12345")
    assert workflow._snapshot(path, 5, retain=True) == (b"12345", _hash(b"12345"))
    with pytest.raises(ValueError):
        workflow._snapshot(path, 4)


@pytest.mark.parametrize("name", ["observation.json", "execution.json", "diagnostics.json", "manifest.json"])
@pytest.mark.parametrize("oversized", [False, True])
def test_staged_byte_mutation_cannot_publish_any_wrong_artifact(setup, monkeypatch, name, oversized):
    original = storage_policy.atomic_write_private_json
    def write(path, payload, **kwargs):
        commit = kwargs["replace_fn"]
        def replace(temporary, destination):
            if Path(path).name == name:
                if oversized:
                    with Path(temporary).open("r+b") as handle:
                        handle.truncate(workflow._LIMITS[name] + 1)
                else:
                    Path(temporary).write_bytes(b"wrong staged bytes")
            commit(temporary, destination)
        return original(path, payload, **{**kwargs, "replace_fn": replace})
    monkeypatch.setattr(storage_policy, "atomic_write_private_json", write)
    with pytest.raises((ValueError, RuntimeError)):
        run(setup)
    assert not (setup.output / name).exists() and not (setup.output / "manifest.json").exists()


def test_snapshot_second_pass_growth_is_stream_bounded(tmp_path, monkeypatch):
    path = tmp_path / "growing"
    path.write_bytes(b"12345")
    original, reads = Path.open, []
    class Handle:
        def __init__(self, real):
            self.real, self.seeks = real, 0
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            self.real.close()
        def fileno(self):
            return self.real.fileno()
        def seek(self, offset):
            self.seeks += 1
            if self.seeks == 2:
                with original(path, "ab") as writer:
                    writer.write(b"67890")
            return self.real.seek(offset)
        def read(self, count):
            reads.append((self.seeks, count))
            return self.real.read(count)
    def open_file(target, *args, **kwargs):
        result = original(target, *args, **kwargs)
        return Handle(result) if target == path and args == ("rb",) else result
    monkeypatch.setattr(Path, "open", open_file)
    with pytest.raises(ValueError):
        workflow._snapshot(path, 5)
    assert [count for attempt, count in reads if attempt == 2] == [6]
