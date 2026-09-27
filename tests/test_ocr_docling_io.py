"""Source/completion snapshots and private no-clobber proposal publication."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

import pytest

import ocr_docling_io as io
import ocr_recovery
import storage_policy
from test_ocr_docling import PRIVATE, document, recovery


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def files(tmp_path):
    paths = {key: tmp_path / name for key, name in (
        ("pdf", "synthetic.source"), ("docling", "synthetic.json"), ("recovery", "recovery.json"),
        ("manifest", ".synthetic.json.conversion.complete.json"), ("output", "proposals.json"))}
    paths["pdf"].write_bytes(b"SYNTHETIC SOURCE BYTES; not parsed as PDF")
    paths["docling"].write_text(json.dumps(document()), encoding="utf-8")
    paths["recovery"].write_text(json.dumps(recovery(source=digest(paths["pdf"]))), encoding="utf-8")
    source = {"name": paths["pdf"].name, "size": paths["pdf"].stat().st_size, "sha256": digest(paths["pdf"])}
    manifest = {"schema_version": 3, "stage": "conversion", "source_sha256": source["sha256"],
                "source_name": source["name"], "source_record_count": None, "parameters_sha256": "e"*64,
                "source": {**source, "capture_policy": "stream-copy-v1"}, "effective_input": {"kind": "original", **source},
                "outputs": [{"role": "docling_json", "name": paths["docling"].name,
                             "size": paths["docling"].stat().st_size, "sha256": digest(paths["docling"])},
                            {"role": "docling_markdown", "name": "synthetic.md", "size": 1, "sha256": "f"*64}]}
    paths["manifest"].write_text(json.dumps(manifest), encoding="utf-8")
    return paths


def run(files):
    return io.propose_docling_files(*(files[key] for key in ("pdf", "docling", "recovery", "output")))


def test_real_facade_v3_parser_and_exact_input_hashes(files):
    before = {key: path.read_bytes() for key, path in files.items() if key != "output"}
    result = run(files)
    assert result["source_sha256"] == digest(files["pdf"])
    assert all(result[key + "_sha256"] == digest(files[key]) for key in ("docling", "recovery", "manifest"))
    assert result["pages"][0]["line_order"] == [0, 1, 3, 2, 4, 5]
    assert json.loads(files["output"].read_bytes()) == result
    assert PRIVATE not in files["output"].read_text(encoding="utf-8")
    assert all(path.read_bytes() == before[key] for key, path in files.items() if key != "output")
    if os.name == "nt":
        assert storage_policy.windows_path_is_private(files["output"], directory=False)
    else:
        assert stat.S_IMODE(files["output"].stat().st_mode) == 0o600


@pytest.mark.parametrize("key", ["pdf", "docling", "recovery", "manifest"])
def test_every_input_rechecked_inside_final_serialized_commit(files, monkeypatch, key):
    actual = storage_policy.atomic_write_private_json

    def change(path, payload, **options):
        publish = options["replace_fn"]

        def during_commit(temporary, target):
            assert Path(temporary).is_file()
            files[key].write_bytes(files[key].read_bytes() + b" ")
            publish(temporary, target)

        return actual(path, payload, **{**options, "replace_fn": during_commit})

    monkeypatch.setattr(storage_policy, "atomic_write_private_json", change)
    with pytest.raises(RuntimeError, match="changed before publication"):
        run(files)
    assert not files["output"].exists()


@pytest.mark.parametrize("key", ["pdf", "docling", "recovery", "manifest"])
def test_each_source_hash_mismatch_rejected(files, key):
    if key == "manifest":
        value = json.loads(files[key].read_bytes())
        value["source"]["sha256"] = "0"*64
        files[key].write_text(json.dumps(value), encoding="utf-8")
    else:
        files[key].write_bytes(files[key].read_bytes() + b" ")
        if key == "recovery":
            value = json.loads(files[key].read_bytes())
            value["source_sha256"] = "0"*64
            files[key].write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError):
        run(files)
    assert not files["output"].exists()


@pytest.mark.parametrize("version", [1, 2, 4, True, "3"])
def test_old_ambiguous_or_unknown_completion_version_rejected(files, version):
    value = json.loads(files["manifest"].read_bytes())
    value["schema_version"] = version
    files["manifest"].write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError):
        run(files)
    assert not files["output"].exists()


def test_conversion_without_the_angle_classifier_rejected(files):
    # Retry OCR runs with the classifier on; never splice it into a cls-off document.
    value = json.loads(files["manifest"].read_bytes())
    value["ocr_angle_classifier"] = False
    files["manifest"].write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="angle classifier"):
        run(files)
    assert not files["output"].exists()


def test_missing_completion_rejected(files):
    files["manifest"].unlink()
    with pytest.raises(FileNotFoundError):
        run(files)
    assert not files["output"].exists()


@pytest.mark.parametrize("raw", [b"[]", b"{", b'{"x":NaN}', b'{"x":1,"x":2}', b"\xff"])
def test_malformed_completion_or_docling_rejected(files, raw):
    files["manifest"].write_bytes(raw)
    with pytest.raises((ValueError, UnicodeError)):
        run(files)
    assert not files["output"].exists()


def test_create_only_and_uncooperating_writer(files, monkeypatch):
    actual = io.build_docling_proposals

    def race(*args, **kwargs):
        report = actual(*args, **kwargs)
        files["output"].write_bytes(b"other writer")
        return report

    monkeypatch.setattr(io, "build_docling_proposals", race)
    with pytest.raises(FileExistsError):
        run(files)
    assert files["output"].read_bytes() == b"other writer"
    with pytest.raises(FileExistsError):
        run(files)


@pytest.mark.parametrize("key", ["pdf", "docling", "recovery", "manifest"])
def test_output_alias_never_clobbers_input(files, key):
    before = files[key].read_bytes()
    files["output"] = files[key]
    with pytest.raises((ValueError, FileExistsError)):
        run(files)
    assert files[key].read_bytes() == before


def test_hard_link_source_rejected(files, tmp_path):
    alias = tmp_path / "alias.source"
    os.link(files["pdf"], alias)
    with pytest.raises(ValueError):
        run(files)
    assert not files["output"].exists()


@pytest.mark.parametrize("exception", [KeyboardInterrupt, ocr_recovery.ReportCleanupError])
def test_late_cancel_or_cleanup_preserves_completed_output(files, monkeypatch, exception):
    actual = ocr_recovery._publish_new_report

    def fail(temporary, target):
        actual(temporary, target)
        raise exception(PRIVATE)

    monkeypatch.setattr(ocr_recovery, "_publish_new_report", fail)
    with pytest.raises(exception):
        run(files)
    assert json.loads(files["output"].read_bytes())["requires_attention"] is True


def test_manifest_change_during_facade_parse_is_rejected(files, monkeypatch):
    import rag

    actual = rag._load_conversion_source_binding

    def change(*args, **kwargs):
        result = actual(*args, **kwargs)
        files["manifest"].write_bytes(files["manifest"].read_bytes() + b" ")
        return result

    monkeypatch.setattr(rag, "_load_conversion_source_binding", change)
    with pytest.raises(RuntimeError):
        run(files)
    assert not files["output"].exists()


def test_real_cli_synthetic_saved_artifacts_and_create_only(files):
    args = [sys.executable, "tools/propose_ocr_layout.py"]
    for key in ("pdf", "docling", "recovery", "output"):
        args.append(f"--{key}={files[key]}")
    first = subprocess.run(args, cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=30)
    assert first.returncode == 3, first.stderr
    assert "1 proposed" in first.stdout
    assert PRIVATE not in first.stdout + first.stderr
    assert str(files["pdf"].parent) not in first.stdout + first.stderr
    before = files["output"].read_bytes()
    second = subprocess.run(args, cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=30)
    assert second.returncode == 2
    assert files["output"].read_bytes() == before


@pytest.mark.parametrize("key,limit", [("pdf", "MAX_SOURCE_BYTES"), ("docling", "MAX_DOCLING_BYTES"),
                                      ("recovery", "MAX_RECOVERY_BYTES"), ("manifest", "MAX_MANIFEST_BYTES")])
def test_file_size_bounds_apply_before_publication(files, monkeypatch, key, limit):
    monkeypatch.setattr(io, limit, max(1, files[key].stat().st_size - 1))
    with pytest.raises(ValueError):
        run(files)
    assert not files["output"].exists()


def test_valid_preprocessed_completion_abstains_without_opening_named_derived_pdf(files):
    value = json.loads(files["manifest"].read_bytes())
    effective = {"name": "deliberately-absent-derived.pdf", "size": 123, "sha256": "0"*64}
    value["effective_input"] = {"kind": "preprocessed", **effective}
    value["outputs"].append({"role": "preprocessed_pdf", **effective})
    files["manifest"].write_text(json.dumps(value), encoding="utf-8")
    result = run(files)
    assert result["pages"][0]["reasons"] == ["effective_input_preprocessed"]
    assert result["pages"][0]["regions"] == []
