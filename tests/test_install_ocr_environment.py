"""Isolated install policy and process-tree supervision without real installs."""

import hashlib
import json
import os
from pathlib import Path
import sys

import pytest

from tools import install_ocr_environment as installer


@pytest.fixture
def setup(tmp_path, monkeypatch):
    source = tmp_path / "repository"
    source.mkdir()
    for name in installer.LOCKS:
        (source / name).write_bytes(b"synthetic lock bytes")
    monkeypatch.setattr(installer, "ROOT", source)
    target, evidence = tmp_path / "new-environment", tmp_path / "evidence"
    calls = []
    def command(step, environment, interpreter, log_path, *, timeout):
        calls.append(step)
        if step == "create_environment":
            scripts = environment / ("Scripts" if os.name == "nt" else "bin")
            scripts.mkdir()
            (scripts / ("python.exe" if os.name == "nt" else "python")).write_bytes(b"synthetic interpreter")
        raw = (step + " completed\n").encode()
        log_path.write_bytes(raw)
        return {"status": "complete", "exit_code": 0, "elapsed_seconds": .01,
                "log_sha256": hashlib.sha256(raw).hexdigest(), "log_bytes": len(raw)}
    monkeypatch.setattr(installer, "_command", command)
    return target, evidence, calls


def run(setup):
    return installer.install_environment(setup[0], setup[1], python_executable=Path(sys.executable))


def test_complete_install_is_bound_to_new_environment_and_source_snapshot(setup):
    report = run(setup)
    assert report["status"] == "complete" and report["installed_under_hash_locks"]
    assert setup[2] == ["create_environment", "bootstrap", "verify_installer", "sync", "check", "inventory"]
    assert report["environment_identity_sha256"] == installer.environment_identity(setup[0])
    assert report["python_executable_sha256"] == hashlib.sha256(b"synthetic interpreter").hexdigest()
    assert hashlib.sha256((setup[1] / "installer-source.py").read_bytes()).hexdigest() == report["installer_source_sha256"]
    assert json.loads((setup[1] / "installation.json").read_text(encoding="utf-8")) == report


@pytest.mark.parametrize("name", ["target", "evidence"])
def test_existing_directories_are_never_changed(setup, name):
    path = setup[0 if name == "target" else 1]
    path.mkdir()
    sentinel = path / "user-file"
    sentinel.write_bytes(b"preserve")
    with pytest.raises(ValueError):
        run(setup)
    assert sentinel.read_bytes() == b"preserve"
    assert not setup[2]


@pytest.mark.parametrize("relationship", ["same", "evidence-inside", "environment-inside", "repository-inside", "repository-root"])
def test_unsafe_directory_relationships_fail(setup, relationship):
    target, evidence, _ = setup
    if relationship == "same":
        evidence = target
    elif relationship == "evidence-inside":
        evidence = target / "evidence"
    elif relationship == "environment-inside":
        target = evidence / "environment"
    elif relationship == "repository-inside":
        target = installer.ROOT / "environment"
    else:
        target = installer.ROOT
    with pytest.raises(ValueError):
        installer.install_environment(target, evidence, python_executable=Path(sys.executable))


@pytest.mark.parametrize("stage,status,code", [("bootstrap", "failed", 1), ("sync", "limit_exceeded", 124),
                                             ("check", "cancelled", 130)])
def test_failure_retains_partial_environment_and_truthful_evidence(setup, monkeypatch, stage, status, code):
    original = installer._command
    def fail(step, *args, **kwargs):
        result = original(step, *args, **kwargs)
        if step == stage:
            result.update(status=status, exit_code=code)
        return result
    monkeypatch.setattr(installer, "_command", fail)
    result = run(setup)
    assert not result["installed_under_hash_locks"]
    assert result["status"] == status
    assert setup[2][-1] == stage
    assert setup[0].is_dir() and (setup[1] / "installation.json").exists()


def test_lock_mutation_cannot_qualify_a_successful_install(setup, monkeypatch):
    original = installer._command
    def mutate(step, *args, **kwargs):
        result = original(step, *args, **kwargs)
        if step == "inventory":
            (installer.ROOT / installer.LOCKS[0]).write_bytes(b"changed")
        return result
    monkeypatch.setattr(installer, "_command", mutate)
    result = run(setup)
    assert result["status"] == "inputs_changed"
    assert not result["installed_under_hash_locks"]


def test_missing_final_interpreter_cannot_leave_a_success_receipt(setup, monkeypatch):
    original = installer._read_snapshot
    def read(path, **kwargs):
        if kwargs["label"] == "installed interpreter":
            raise OSError("PRIVATE disappeared")
        return original(path, **kwargs)
    monkeypatch.setattr(installer, "_read_snapshot", read)
    with pytest.raises(OSError):
        run(setup)
    result = json.loads((setup[1] / "installation.json").read_text(encoding="utf-8"))
    assert result["status"] == "failed" and result["installed_under_hash_locks"] is False
    assert result["python_executable_sha256"] is None


def test_unreadable_final_lock_retains_a_failure_receipt(setup, monkeypatch):
    original = installer._read_snapshot
    def read(path, **kwargs):
        if setup[2] and setup[2][-1] == "inventory" and kwargs["label"] == "environment lock":
            raise OSError("PRIVATE disappeared")
        return original(path, **kwargs)
    monkeypatch.setattr(installer, "_read_snapshot", read)
    result = run(setup)
    assert result["status"] == "inputs_changed" and result["installed_under_hash_locks"] is False
    assert (setup[1] / "installation.json").is_file()


def test_keyboard_interrupt_preserves_failure_receipt(setup, monkeypatch):
    def cancel(*args, **kwargs):
        raise KeyboardInterrupt()
    monkeypatch.setattr(installer, "_command", cancel)
    with pytest.raises(KeyboardInterrupt):
        run(setup)
    result = json.loads((setup[1] / "installation.json").read_text(encoding="utf-8"))
    assert result["status"] == "cancelled" and not result["installed_under_hash_locks"]


def test_fixed_recipe_never_targets_system_or_unhashed_install(setup):
    commands = installer._commands(setup[0], Path(sys.executable))
    assert "--isolated" in commands["bootstrap"] and "--require-hashes" in commands["bootstrap"]
    assert "--strict" in commands["sync"] and "--require-hashes" in commands["sync"]
    assert commands["sync"][commands["sync"].index("--torch-backend") + 1] == "cpu"
    assert "--system" not in commands["sync"]
    assert "--no-python-downloads" in commands["sync"]
    assert "--link-mode" in commands["sync"]
    assert all(str(setup[0]) in " ".join(command) for command in commands.values())


def test_child_environment_does_not_forward_secrets_or_ambient_installer_indexes(monkeypatch):
    for name in ("PRIVATE_TOKEN", "UV_INDEX_URL", "PIP_INDEX_URL", "HTTP_PROXY", "PYTHONPATH"):
        monkeypatch.setenv(name, "PRIVATE")
    environment = installer._environment()
    assert "PRIVATE" not in environment.values()
    assert environment["HF_HUB_OFFLINE"] == environment["TRANSFORMERS_OFFLINE"] == "1"
    assert environment["UV_PYTHON_DOWNLOADS"] == "never"


def test_installer_steps_use_existing_tree_supervision(tmp_path, monkeypatch):
    seen = {}
    def supervise(script, argv, **kwargs):
        seen.update(script=script, argv=argv, kwargs=kwargs)
        return 0
    monkeypatch.setattr(installer.process_supervision, "_run_cli_with_deadline", supervise)
    result = installer._command("sync", tmp_path / "env", Path(sys.executable), tmp_path / "step.log", timeout=7)
    assert result["status"] == "complete"
    assert seen["argv"][:2] == ["--worker-step", "sync"]
    assert seen["kwargs"]["timeout"] == 7
    assert seen["kwargs"]["heartbeat"] is not None
    assert seen["kwargs"]["stdout_target"] is seen["kwargs"]["stderr_target"]


@pytest.mark.parametrize("value", [True, 0, -1, 3601, 1.0, None])
def test_timeout_is_strict(setup, value):
    with pytest.raises(ValueError):
        installer.install_environment(setup[0], setup[1], python_executable=Path(sys.executable), timeout=value)


def test_uncontained_worker_and_cli_errors_fail_static(monkeypatch, capsys):
    monkeypatch.delenv("RAG_OCR_INSTALL_CHILD", raising=False)
    assert installer.main(["--worker-step", "sync", "PRIVATE", "PRIVATE"]) == 2
    assert "PRIVATE" not in capsys.readouterr().err
