"""Private saved-artifact layout I/O failure injection, without PDF/model access."""

from __future__ import annotations

import copy
import hashlib
import itertools
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

import pytest

import ocr_layout_io as layout_io
import ocr_recovery
import storage_policy


PRIVATE = "SYNTHETIC_PRIVATE_CANDIDATE_TEXT"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _result():
    return {
        "kind": "synthetic_layout_test", "schema_version": 1,
        "summary": {"review_pages": 1, "planned_pages": 1, "reordered": 1,
                    "unchanged": 0, "abstained": 0, "unavailable": 0},
        "comparison": None, "requires_attention": True,
        "pages": [{"original_text": PRIVATE, "proposed_text": PRIVATE}],
    }


@pytest.fixture
def inputs(tmp_path):
    values = {
        "recovery": {"source_sha256": "a" * 64, "synthetic": "recovery"},
        "plan": {"synthetic": "plan"},
        "references": {"synthetic": "references"},
    }
    paths = {name: tmp_path / (name + ".json") for name in values}
    for name, value in values.items():
        paths[name].write_text(json.dumps(value, indent=1) + "\n", encoding="utf-8")
    paths["output"] = tmp_path / "review.json"
    return paths


@pytest.fixture
def core(monkeypatch):
    calls = []

    def build(*args, **kwargs):
        calls.append((copy.deepcopy(args), copy.deepcopy(kwargs)))
        return _result()

    monkeypatch.setattr(layout_io, "build_layout_review", build)
    return calls


def _run(paths, *, references=True):
    return layout_io.review_layout_files(
        paths["recovery"], paths["plan"], paths["output"],
        reference_path=paths["references"] if references else None)


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _assert_private(path):
    if os.name == "nt":
        assert storage_policy.windows_path_is_private(path, directory=False)
    else:
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.parametrize("references", [False, True])
def test_exact_byte_hashes_and_sensitive_output_are_private(inputs, core, references):
    before = {key: path.read_bytes() for key, path in inputs.items() if key != "output"}
    result = _run(inputs, references=references)
    assert len(core) == 1
    arguments, options = core[0]
    assert arguments == (json.loads(before["recovery"]), json.loads(before["plan"]))
    assert options == {
        "recovery_sha256": _digest(inputs["recovery"]),
        "references": json.loads(before["references"]) if references else None,
    }
    assert options["recovery_sha256"] != arguments[0]["source_sha256"]
    assert result["inputs"] == {
        "recovery_sha256": _digest(inputs["recovery"]),
        "plan_sha256": _digest(inputs["plan"]),
        "references_sha256": _digest(inputs["references"]) if references else None,
    }
    assert json.loads(inputs["output"].read_bytes()) == result
    assert PRIVATE in inputs["output"].read_text(encoding="utf-8")
    _assert_private(inputs["output"])
    assert {key: path.read_bytes() for key, path in inputs.items() if key != "output"} == before


def test_all_reads_core_and_publication_are_inside_the_same_nonwaiting_lease(inputs, monkeypatch):
    active, events = [], []
    actual_load, actual_read = layout_io._load, layout_io._read_snapshot
    actual_write = storage_policy.atomic_write_private_json

    class Lease:
        def __init__(self, path, **kwargs):
            assert path == inputs["output"].absolute()
            assert kwargs == {
                "backend": "ocr-layout", "collection_name": "report", "operation": "reorder",
                "timeout": 0, "resource_description": "OCR layout review report",
            }

        def __enter__(self):
            active.append(True)

        def __exit__(self, *_args):
            active.pop()

    def load(path, **kwargs):
        assert active
        events.append(("load", path.name, kwargs["limit"]))
        return actual_load(path, **kwargs)

    def read(path, **kwargs):
        assert active
        events.append(("recheck", path.name, kwargs["max_bytes"]))
        return actual_read(path, **kwargs)

    def build(*_args, **_kwargs):
        assert active
        events.append(("core",))
        return _result()

    def write(path, payload, **kwargs):
        assert active
        assert kwargs == {"indent": 2, "replace_fn": ocr_recovery._publish_new_report}
        events.append(("write",))
        return actual_write(path, payload, **kwargs)

    monkeypatch.setattr(layout_io, "PathLease", Lease)
    monkeypatch.setattr(layout_io, "_load", load)
    monkeypatch.setattr(layout_io, "_read_snapshot", read)
    monkeypatch.setattr(layout_io, "build_layout_review", build)
    monkeypatch.setattr(storage_policy, "atomic_write_private_json", write)
    _run(inputs)
    expected = [
        ("load", "recovery.json", 64 * 1024 * 1024),
        ("load", "plan.json", 1024 * 1024),
        ("load", "references.json", 16 * 1024 * 1024),
        ("core",),
        ("recheck", "recovery.json", 64 * 1024 * 1024),
        ("recheck", "plan.json", 1024 * 1024),
        ("recheck", "references.json", 16 * 1024 * 1024),
        ("write",),
    ]
    assert events == expected
    assert not active


@pytest.mark.parametrize("first,second", list(itertools.combinations(
    ("recovery", "plan", "references", "output"), 2)))
def test_identical_inputs_or_output_paths_are_rejected(inputs, core, first, second):
    paths = dict(inputs)
    paths[second] = paths[first]
    with pytest.raises(ValueError, match="must be distinct"):
        _run(paths)
    assert core == []


@pytest.mark.parametrize("first,second", list(itertools.combinations(
    ("recovery", "plan", "references", "output"), 2)))
def test_hard_link_aliases_are_rejected(inputs, core, first, second):
    alias = inputs[second].with_name(second + "-alias.json")
    os.link(inputs[first], alias)
    paths = dict(inputs)
    paths[second] = alias
    with pytest.raises(ValueError, match="must be distinct"):
        _run(paths)
    assert core == []
    assert alias.read_bytes() == inputs[first].read_bytes()


def test_linked_parent_is_rejected_without_touching_input_or_output(inputs, core, tmp_path):
    alias = tmp_path / "linked"
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(inputs["recovery"].parent), str(alias))
    else:
        alias.symlink_to(inputs["recovery"].parent, target_is_directory=True)
    paths = dict(inputs)
    paths["recovery"] = alias / inputs["recovery"].name
    with pytest.raises(storage_policy.StoragePolicyError):
        _run(paths)
    assert core == []
    assert not inputs["output"].exists()


def test_existing_output_is_never_overwritten_or_used_as_a_resume_point(inputs, core):
    inputs["output"].write_bytes(b"existing review")
    with pytest.raises(FileExistsError):
        _run(inputs)
    assert inputs["output"].read_bytes() == b"existing review"
    assert core == []


@pytest.mark.parametrize("name", ["recovery", "plan", "references"])
def test_every_input_is_rechecked_and_byte_changes_refuse_publication(inputs, monkeypatch, name):
    def change(*_args, **_kwargs):
        inputs[name].write_bytes(inputs[name].read_bytes() + b" ")
        return _result()

    monkeypatch.setattr(layout_io, "build_layout_review", change)
    with pytest.raises(RuntimeError, match="changed before publication"):
        _run(inputs)
    assert not inputs["output"].exists()


@pytest.mark.parametrize("name", ["recovery", "plan", "references"])
def test_input_disappearance_after_core_never_publishes(inputs, monkeypatch, name):
    def remove(*_args, **_kwargs):
        inputs[name].unlink()
        return _result()

    monkeypatch.setattr(layout_io, "build_layout_review", remove)
    with pytest.raises(OSError):
        _run(inputs)
    assert not inputs["output"].exists()


@pytest.mark.parametrize("name,limit", [
    ("recovery", "MAX_RECOVERY_BYTES"), ("plan", "MAX_PLAN_BYTES"),
    ("references", "MAX_REFERENCE_BYTES"),
])
def test_initial_input_size_limits_fail_before_core(inputs, core, monkeypatch, name, limit):
    monkeypatch.setattr(layout_io, limit, 64)
    inputs[name].write_bytes(b'{"text":"' + b"x" * 80 + b'"}')
    with pytest.raises(ValueError):
        _run(inputs)
    assert core == []
    assert not inputs["output"].exists()


@pytest.mark.parametrize("name,limit", [
    ("recovery", "MAX_RECOVERY_BYTES"), ("plan", "MAX_PLAN_BYTES"),
    ("references", "MAX_REFERENCE_BYTES"),
])
def test_size_limit_also_applies_to_final_recheck(inputs, monkeypatch, name, limit):
    monkeypatch.setattr(layout_io, limit, 256)

    def grow(*_args, **_kwargs):
        inputs[name].write_bytes(b'{"text":"' + b"x" * 300 + b'"}')
        return _result()

    monkeypatch.setattr(layout_io, "build_layout_review", grow)
    with pytest.raises(ValueError, match="invalid bounded artifact"):
        _run(inputs)
    assert not inputs["output"].exists()


@pytest.mark.parametrize("invalid", [
    b"[]", b"null", b"{", b"\xff", b'{"x":NaN}', b'{"x":Infinity}',
    ('{"' + PRIVATE + '":1,"' + PRIVATE + '":2}').encode("ascii"),
    b'{"x":1e9999}',
])
def test_malformed_json_is_strict_bounded_and_redacted(inputs, core, invalid):
    inputs["plan"].write_bytes(invalid)
    with pytest.raises(ValueError) as captured:
        _run(inputs)
    assert PRIVATE not in str(captured.value)
    assert "bounded strict UTF-8 JSON" in str(captured.value)
    assert core == []
    assert not inputs["output"].exists()


def test_noncooperating_writer_wins_without_being_overwritten(inputs, monkeypatch):
    def race(*_args, **_kwargs):
        inputs["output"].write_bytes(b"other writer report")
        return _result()

    monkeypatch.setattr(layout_io, "build_layout_review", race)
    with pytest.raises(FileExistsError):
        _run(inputs)
    assert inputs["output"].read_bytes() == b"other writer report"


@pytest.mark.parametrize("error", [ValueError("invalid plan"), KeyboardInterrupt(), RuntimeError("failed")])
def test_core_failure_or_cancellation_does_not_publish_and_lease_is_released(inputs, monkeypatch, error):
    def fail(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(layout_io, "build_layout_review", fail)
    with pytest.raises(type(error)):
        _run(inputs)
    assert not inputs["output"].exists()
    monkeypatch.setattr(layout_io, "build_layout_review", lambda *_args, **_kwargs: _result())
    _run(inputs)
    assert inputs["output"].exists()


def test_publication_failure_is_propagated_without_fabricating_output(inputs, core, monkeypatch):
    def fail(*_args, **_kwargs):
        raise OSError("synthetic publication failure")

    monkeypatch.setattr(ocr_recovery, "_publish_new_report", fail)
    with pytest.raises(OSError):
        _run(inputs)
    assert not inputs["output"].exists()


@pytest.mark.parametrize("exception", [ocr_recovery.ReportCleanupError, KeyboardInterrupt])
def test_postcommit_failure_preserves_output_and_propagates_distinct_outcome(
        inputs, core, monkeypatch, exception):
    publish = ocr_recovery._publish_new_report

    def fail_after_commit(temporary, destination):
        publish(temporary, destination)
        raise exception("synthetic postcommit failure")

    monkeypatch.setattr(ocr_recovery, "_publish_new_report", fail_after_commit)
    with pytest.raises(exception):
        _run(inputs)
    assert json.loads(inputs["output"].read_bytes())["pages"][0]["original_text"] == PRIVATE
    _assert_private(inputs["output"])


def test_io_import_does_not_load_pdf_image_model_or_facade_modules():
    result = subprocess.run(
        [sys.executable, "-c", (
            "import sys; import ocr_layout_io; "
            "forbidden={'rag','ocr_recovery_runtime','pymupdf','fitz','cv2','numpy','rapidocr','onnxruntime'}; "
            "assert not forbidden.intersection(sys.modules)"
        )], cwd=PROJECT_ROOT, stdin=subprocess.DEVNULL, capture_output=True,
        text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr
