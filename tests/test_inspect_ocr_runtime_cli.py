"""Runtime manifest file adapter: mismatches, provenance, privacy, and CLI gates."""

import hashlib
import importlib.metadata
import json
from pathlib import Path
import subprocess
import sys

import pytest

import ocr_benchmark_io as adapter
from ocr_recovery import ReportCleanupError
from test_ocr_experiment_runtime import evidence, lock_bytes
from tools import inspect_ocr_runtime as cli


@pytest.fixture
def files(tmp_path):
    locked, observation, output = (tmp_path / name for name in ("test.lock", "observed.json", "manifest.json"))
    locked.write_bytes(lock_bytes("pytest==" + importlib.metadata.version("pytest")))
    observation.write_text(json.dumps(evidence()), encoding="utf-8")
    return locked, observation, output


def arguments(files):
    return ["--lock", str(files[0]), "--effective-runtime", str(files[1]),
            "--output", str(files[2]), "--package", "pytest"]


def test_real_runtime_cli_records_matching_metadata_but_never_attestation(files):
    result = subprocess.run([sys.executable, "tools/inspect_ocr_runtime.py", *arguments(files)],
                            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=20)
    assert result.returncode == 3, result.stderr
    assert "0 version checks need attention" in result.stdout
    assert "remain unverified" in result.stdout
    assert str(files[0].parent) not in result.stdout + result.stderr
    report = json.loads(files[2].read_text(encoding="utf-8"))
    assert report["package_versions_match"]
    assert not report["locked_environment_verified"]
    assert report["inputs"] == {"lock_file_sha256": hashlib.sha256(files[0].read_bytes()).hexdigest(),
                                "effective_observation_file_sha256": hashlib.sha256(files[1].read_bytes()).hexdigest()}


def test_runtime_without_execution_does_not_invent_evidence(files):
    report = adapter.inspect_runtime_file(files[0], files[2], packages=["pytest"])
    assert report["effective_runtime"] is None
    assert set(report["inputs"]) == {"lock_file_sha256"}


def test_strict_version_gate_refuses_output_but_default_records_mismatch(files, capsys):
    files[0].write_bytes(lock_bytes("pytest==0.0.0"))
    assert cli.main(arguments(files) + ["--require-version-match"]) == 2
    assert not files[2].exists()
    assert cli.main(arguments(files)) == 3
    assert files[2].exists()
    assert "1 version checks need attention" in capsys.readouterr().out


@pytest.mark.parametrize("index", [0, 1])
def test_runtime_inputs_rechecked_inside_publication(files, monkeypatch, index):
    writer = adapter.storage_policy.atomic_write_private_json
    def mutate(path, payload, **kwargs):
        files[index].write_bytes(b"changed at publication")
        return writer(path, payload, **kwargs)
    monkeypatch.setattr(adapter.storage_policy, "atomic_write_private_json", mutate)
    with pytest.raises(RuntimeError, match="changed"):
        adapter.inspect_runtime_file(files[0], files[2], effective_path=files[1], packages=["pytest"])
    assert not files[2].exists()


def test_lock_capture_digest_must_match_outer_snapshot(files, monkeypatch):
    capture = adapter.capture_runtime_manifest
    def wrong(*args, **kwargs):
        report = capture(*args, **kwargs)
        report["lock_sha256"] = "f" * 64
        return report
    monkeypatch.setattr(adapter, "capture_runtime_manifest", wrong)
    with pytest.raises(RuntimeError, match="between snapshots"):
        adapter.inspect_runtime_file(files[0], files[2], packages=["pytest"])
    assert not files[2].exists()


@pytest.mark.parametrize("raw", [b'{"private-field":0,"private-field":1}', b'{"elapsed_seconds":NaN}', b"[]"])
def test_observation_strict_json_and_redacted_errors(files, capsys, raw):
    files[1].write_bytes(raw)
    assert cli.main(arguments(files)) == 2
    assert "private-field" not in capsys.readouterr().err
    assert not files[2].exists()


def test_observation_size_limit(files, monkeypatch):
    monkeypatch.setattr(adapter, "MAX_OBSERVATION_BYTES", 8)
    with pytest.raises(ValueError):
        adapter.inspect_runtime_file(files[0], files[2], effective_path=files[1], packages=["pytest"])
    assert not files[2].exists()


@pytest.mark.parametrize("alias", ["lock-output", "observation-output", "lock-observation", "existing"])
def test_runtime_paths_cannot_alias_or_clobber(files, alias):
    locked, observation, output = files
    if alias == "lock-output":
        output = locked
    elif alias == "observation-output":
        output = observation
    elif alias == "lock-observation":
        observation = locked
    else:
        output.write_bytes(b"existing")
    before = [(path, path.read_bytes()) for path in files if path.exists()]
    with pytest.raises((ValueError, OSError, RuntimeError)):
        adapter.inspect_runtime_file(locked, output, effective_path=observation, packages=["pytest"])
    assert all(path.read_bytes() == raw for path, raw in before)


@pytest.mark.parametrize("error,code,notice", [(ReportCleanupError("PRIVATE"), 2, "may already exist"),
                                             (KeyboardInterrupt(), 130, "may already exist"),
                                             (OSError("PRIVATE/path"), 2, "failed")])
def test_runtime_cli_errors_and_late_cancel_are_honest(files, monkeypatch, capsys, error, code, notice):
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(adapter, "inspect_runtime_file", fail)
    assert cli.main(arguments(files)) == code
    message = capsys.readouterr().err
    assert notice in message and "PRIVATE" not in message


def test_runtime_help_is_light_and_argument_errors_redacted(capsys):
    assert cli.main(["--PRIVATE-PATH"]) == 2
    assert "PRIVATE-PATH" not in capsys.readouterr().err
    code = ("import sys,runpy; sys.modules.update({x:None for x in ['numpy','cv2','fitz','rapidocr','onnxruntime']}); "
            "sys.argv=['inspect_ocr_runtime.py','--help']; runpy.run_path('tools/inspect_ocr_runtime.py',run_name='__main__')")
    result = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0
    assert "--require-version-match" in result.stdout
