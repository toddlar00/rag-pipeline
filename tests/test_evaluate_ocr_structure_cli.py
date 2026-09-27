"""File lifecycle and content-free CLI diagnostics for structural OCR metrics."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import ocr_benchmark_io as adapter
from ocr_recovery import ReportCleanupError
from test_ocr_benchmark import cohort, predictions, structure
from tools import evaluate_ocr_structure as cli


@pytest.fixture
def files(tmp_path):
    source = cohort()
    expected, actual, output = (tmp_path / name for name in ("cohort.json", "predictions.json", "output.json"))
    expected.write_text(json.dumps(source), encoding="utf-8")
    actual.write_text(json.dumps(predictions(source)), encoding="utf-8")
    return expected, actual, output


def arguments(files):
    return ["--cohort", str(files[0]), "--predictions", str(files[1]), "--output", str(files[2])]


def test_file_report_binds_exact_bytes_without_changing_inputs(files):
    before = [path.read_bytes() for path in files[:2]]
    report = adapter.evaluate_structure_files(*files)
    assert json.loads(files[2].read_text(encoding="utf-8")) == report
    assert report["inputs"] == {"cohort_file_sha256": hashlib.sha256(before[0]).hexdigest(),
                                "predictions_file_sha256": hashlib.sha256(before[1]).hexdigest()}
    assert [path.read_bytes() for path in files[:2]] == before


def test_real_cli_success_prints_counts_not_paths_or_annotations(files):
    result = subprocess.run([sys.executable, "tools/evaluate_ocr_structure.py", *arguments(files)],
                            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert "1 calibration and 1 held-out" in result.stdout
    assert str(files[0].parent) not in result.stdout + result.stderr
    assert "family-1" not in result.stdout + result.stderr


def test_partial_structure_exits_three_with_report(files, capsys):
    payload = json.loads(files[1].read_text(encoding="utf-8"))
    payload["records"][1]["structure"] = structure(())
    files[1].write_text(json.dumps(payload), encoding="utf-8")
    assert cli.main(arguments(files)) == 3
    assert files[2].exists()
    assert "PRIVATE" not in capsys.readouterr().out


@pytest.mark.parametrize("index", [0, 1])
def test_mutation_inside_atomic_staging_prevents_commit(files, monkeypatch, index):
    write = adapter.storage_policy.atomic_write_private_json
    def mutate(path, payload, **kwargs):
        files[index].write_bytes(b"changed while preparing output")
        return write(path, payload, **kwargs)
    monkeypatch.setattr(adapter.storage_policy, "atomic_write_private_json", mutate)
    with pytest.raises(RuntimeError, match="changed"):
        adapter.evaluate_structure_files(*files)
    assert not files[2].exists()
    assert not list(files[2].parent.glob(".*.tmp"))


def test_uncooperative_destination_creation_does_not_clobber(files, monkeypatch):
    publish = adapter._publish_new_report
    def race(temporary, destination):
        destination.write_bytes(b"competitor generation")
        publish(temporary, destination)
    monkeypatch.setattr(adapter, "_publish_new_report", race)
    with pytest.raises(FileExistsError):
        adapter.evaluate_structure_files(*files)
    assert files[2].read_bytes() == b"competitor generation"


@pytest.mark.parametrize("kind", ["existing", "same-input", "same-inputs", "directory"])
def test_invalid_paths_are_refused_without_overwriting(files, kind):
    paths = list(files)
    if kind == "existing":
        paths[2].write_bytes(b"existing")
    elif kind == "same-input":
        paths[2] = paths[0]
    elif kind == "same-inputs":
        paths[1] = paths[0]
    else:
        paths[2].mkdir()
    before = [(path, path.read_bytes()) for path in files if path.is_file()]
    with pytest.raises((OSError, ValueError, RuntimeError)):
        adapter.evaluate_structure_files(*paths)
    assert all(path.read_bytes() == raw for path, raw in before)


@pytest.mark.parametrize("kind", ["symlink", "hardlink"])
def test_linked_input_is_rejected(files, kind):
    alias = files[0].parent / "alias.json"
    try:
        if kind == "symlink":
            alias.symlink_to(files[0])
        else:
            os.link(files[0], alias)
    except OSError:
        pytest.skip("filesystem links unavailable")
    with pytest.raises((OSError, ValueError, RuntimeError)):
        adapter.evaluate_structure_files(alias, files[1], files[2])
    assert not files[2].exists()


@pytest.mark.parametrize("raw", [b'{"PRIVATE_FIELD":1,"PRIVATE_FIELD":2}', b'{"schema_version":NaN}',
                                b"not JSON", b'[]'], ids=["duplicate", "nonfinite", "invalid", "nonobject"])
def test_strict_json_errors_are_static_at_cli(files, capsys, raw):
    files[0].write_bytes(raw)
    assert cli.main(arguments(files)) == 2
    messages = capsys.readouterr()
    assert "PRIVATE" not in messages.out + messages.err
    assert str(files[0]) not in messages.err
    assert not files[2].exists()


def test_input_size_limit_prevents_publication(files, monkeypatch):
    monkeypatch.setattr(adapter, "MAX_STRUCTURE_BYTES", 8)
    with pytest.raises(ValueError):
        adapter.evaluate_structure_files(*files)
    assert not files[2].exists()


@pytest.mark.parametrize("error,code,notice", [(ReportCleanupError("PRIVATE"), 2, "may already exist"),
                                             (KeyboardInterrupt(), 130, "may already exist"),
                                             (OSError("PRIVATE/path"), 2, "failed")])
def test_failure_cleanup_and_cancellation_are_honest_static(files, monkeypatch, capsys, error, code, notice):
    def fail(*args):
        raise error
    monkeypatch.setattr(adapter, "evaluate_structure_files", fail)
    assert cli.main(arguments(files)) == code
    messages = capsys.readouterr()
    assert notice in messages.err
    assert "PRIVATE" not in messages.err


def test_help_is_dependency_light_and_bad_arguments_redacted(capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--help"])
    assert exit_info.value.code == 0
    assert cli.main(["--PRIVATE-PATH"]) == 2
    assert "PRIVATE-PATH" not in capsys.readouterr().err
    code = ("import sys,runpy; sys.modules.update({x:None for x in ['numpy','cv2','fitz','rapidocr','onnxruntime']}); "
            "sys.argv=['evaluate_ocr_structure.py','--help']; runpy.run_path('tools/evaluate_ocr_structure.py',run_name='__main__')")
    result = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0
