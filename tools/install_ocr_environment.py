#!/usr/bin/env python3
"""Create a new isolated CPU environment from unchanged repository hash locks.

This records installer outcomes, not wheel/native-library attestation. It never
changes an existing environment, regenerates a lock, or downloads Hub models.
"""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluation_inputs import _read_snapshot, _strict_json_bytes  # noqa: E402
from ocr_recovery import _publish_new_report  # noqa: E402
import storage_policy  # noqa: E402
import process_supervision  # noqa: E402
from ocr_execution_receipt import environment_identity  # noqa: E402


LOCKS = ("requirements-full.lock", "requirements-test.lock", "requirements-lock-tools.lock")
MAX_LOG_BYTES = 16 * 1024 * 1024
# Monitored threshold, not a strict write cap: native stdout can overshoot
# between supervisor heartbeat polls. Oversized logs never qualify a step.


def _environment() -> dict[str, str]:
    allowed = {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "LOCALAPPDATA", "APPDATA",
               "USERPROFILE", "COMSPEC", "PATHEXT", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE"}
    result = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    result.update(PYTHONNOUSERSITE="1", PYTHONUTF8="1", PIP_NO_INPUT="1", PIP_DISABLE_PIP_VERSION_CHECK="1",
                  UV_NO_PROGRESS="1", UV_PYTHON_DOWNLOADS="never", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    return result


def _command(step: str, target: Path, interpreter: Path, log_path: Path, *, timeout: int) -> dict:
    """Run a fixed installer step in the existing process-tree supervisor."""
    started = time.monotonic()
    storage_policy.atomic_write_private_text(log_path, "", replace_fn=_publish_new_report)
    config = process_supervision.SupervisionConfig("RAG_OCR_INSTALL_CHILD", "RAG_OCR_INSTALL_RUN", 5., .1, 10.)
    environment = _environment()
    overrides = {key: None for key in os.environ if key not in environment}
    overrides.update(environment)
    overrides[config.supervised_child_env] = "1"

    def heartbeat(_process):
        if log_path.stat().st_size > MAX_LOG_BYTES:
            raise ValueError("installer log exceeds bounds")

    with log_path.open("ab") as log:
        try:
            exit_code = process_supervision._run_cli_with_deadline(
                Path(__file__), ["--worker-step", step, str(target), str(interpreter)],
                operation="ocr-environment-install", timeout=float(timeout), config=config,
                working_directory=ROOT, environment_overrides=overrides, heartbeat=heartbeat,
                stdout_target=log, stderr_target=log, warn_fn=lambda _: None)
            status = ("complete" if exit_code == 0 else "limit_exceeded" if exit_code == 124
                      else "cancelled" if exit_code == 130 else "failed")
        except ValueError:
            status, exit_code = "limit_exceeded", None
    # Installer logs are diagnostic, never parsed as evidence of wheel identity.
    size = log_path.stat().st_size
    if size > MAX_LOG_BYTES:
        status, digest = "limit_exceeded", None
    else:
        _, digest = _read_snapshot(log_path, label="installer log", max_bytes=MAX_LOG_BYTES)
    return {"status": status, "exit_code": exit_code, "elapsed_seconds": round(time.monotonic() - started, 6),
            "log_sha256": digest, "log_bytes": size}


def _commands(target: Path, interpreter: Path) -> dict[str, list[str]]:
    scripts = target / ("Scripts" if os.name == "nt" else "bin")
    python = scripts / ("python.exe" if os.name == "nt" else "python")
    uv = scripts / ("uv.exe" if os.name == "nt" else "uv")
    return {
        "create_environment": [str(interpreter), "-c", "import sys,venv; assert sys.version_info[:2] == (3,12); venv.EnvBuilder(with_pip=True).create(sys.argv[1])", str(target)],
        "bootstrap": [str(python), "-m", "pip", "--isolated", "install", "--require-hashes", "-r", str(ROOT / LOCKS[2])],
        "verify_installer": [str(python), "-c", "import importlib.metadata as m; assert m.version('uv') == '0.12.5'; print('uv 0.12.5')"],
        "sync": [str(uv), "--no-config", "pip", "sync", "--python", str(python), "--strict", "--torch-backend", "cpu",
                 "--require-hashes", "--link-mode", "copy", "--no-python-downloads", *(str(ROOT / name) for name in LOCKS)],
        "check": [str(uv), "--no-config", "pip", "check", "--python", str(python)],
        "inventory": [str(python), "-c", "import importlib.metadata as m,json,platform; print(json.dumps({'python_version':platform.python_version(),'packages':sorted([{'name':d.metadata['Name'],'version':d.version} for d in m.distributions()],key=lambda d:d['name'].lower())},sort_keys=True))"],
    }


def _worker(argv: list[str]) -> int:
    if len(argv) != 4 or os.environ.get("RAG_OCR_INSTALL_CHILD") != "1":
        raise ValueError("installer worker requires its supervisor")
    _, step, target, interpreter = argv
    target, interpreter = Path(target).absolute(), Path(interpreter).absolute()
    for path in (target, interpreter):
        storage_policy.assert_no_link_components(path)
    marker, _ = _read_snapshot(target / ".ocr-install-owner.json", label="installer ownership", max_bytes=4096)
    if _strict_json_bytes(marker, label="installer ownership", max_bytes=4096) != {"environment_identity_sha256": environment_identity(target)}:
        raise ValueError("installer environment ownership differs")
    commands = _commands(target, interpreter)
    if step not in commands:
        raise ValueError("unknown installer step")
    return subprocess.run(commands[step], cwd=ROOT, env=_environment(), stdin=subprocess.DEVNULL,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).returncode


def install_environment(environment_path: Path, evidence_dir: Path, *, python_executable: Path,
                        timeout: int = 1800) -> dict:
    """Create-only install with a completion/failure receipt and no global writes."""
    if type(timeout) is not int or not 1 <= timeout <= 3600:
        raise ValueError("installation timeout is outside bounds")
    target, evidence, interpreter = (Path(path).absolute() for path in (environment_path, evidence_dir, python_executable))
    for path in (target, evidence, interpreter):
        storage_policy.assert_no_link_components(path)
    if (target.exists() or evidence.exists() or target == evidence
            or target in evidence.parents or evidence in target.parents
            or target == ROOT or ROOT in target.parents or target in ROOT.parents
            or target in interpreter.parents):
        raise ValueError("choose distinct new environment and evidence directories outside the source tree")
    snapshots = {name: _read_snapshot(ROOT / name, label="environment lock", max_bytes=8 * 1024 * 1024)
                 for name in LOCKS}
    source_bytes = Path(__file__).read_bytes()
    source_digest = hashlib.sha256(source_bytes).hexdigest()
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.mkdir(exist_ok=False)
    storage_policy.enforce_private_path(evidence, directory=True)
    storage_policy.atomic_write_private_text(evidence / "installer-source.py", source_bytes.decode("utf-8"), replace_fn=_publish_new_report)
    # mkdir refuses a concurrent creator before the private venv tree is built.
    target.parent.mkdir(parents=True, exist_ok=True)
    target.mkdir(exist_ok=False)
    storage_policy.enforce_private_path(target, directory=True)
    storage_policy.atomic_write_private_json(target / ".ocr-install-owner.json",
                                             {"environment_identity_sha256": environment_identity(target)},
                                             replace_fn=_publish_new_report)
    scripts = target / ("Scripts" if os.name == "nt" else "bin")
    python = scripts / ("python.exe" if os.name == "nt" else "python")
    result = {"schema_version": 1, "kind": "ocr_locked_environment_installation",
              "recipe": "repository-full-cpu-hash-sync-v1", "installer_source_sha256": source_digest,
              "locks": {name: digest for name, (_, digest) in snapshots.items()},
              "steps": {}, "status": "failed", "installed_under_hash_locks": False,
              "environment_identity_sha256": environment_identity(target), "python_executable_sha256": None,
              "scope": "Installer execution and dependency consistency; not installed wheel, build-environment, or native-library byte attestation."}
    try:
        for name in _commands(target, interpreter):
            result["steps"][name] = _command(name, target, interpreter, evidence / (name + ".log"), timeout=timeout)
            print(f"OCR environment step {name}: {result['steps'][name]['status']}", flush=True)
            if result["steps"][name]["status"] != "complete":
                result["status"] = result["steps"][name]["status"]
                break
        else:
            executable_digest = _read_snapshot(python, label="installed interpreter", max_bytes=64 * 1024 * 1024)[1]
            result["status"] = "complete"
            result["installed_under_hash_locks"] = True
            result["python_executable_sha256"] = executable_digest
    except KeyboardInterrupt:
        result["status"] = "cancelled"
        raise
    finally:
        try:
            current = {name: _read_snapshot(ROOT / name, label="environment lock", max_bytes=8 * 1024 * 1024)[1] for name in LOCKS}
            unchanged = current == result["locks"] and hashlib.sha256(Path(__file__).read_bytes()).hexdigest() == source_digest
        except (OSError, ValueError, RuntimeError):
            unchanged = False
        if not unchanged:
            result.update(status="inputs_changed", installed_under_hash_locks=False)
        storage_policy.atomic_write_private_json(evidence / "installation.json", result, indent=2,
                                                 replace_fn=_publish_new_report)
    return result


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError("invalid isolated installation arguments")


def main(argv=None) -> int:
    try:
        argv = list(sys.argv[1:] if argv is None else argv)
        if argv and argv[0] == "--worker-step":
            return _worker(argv)
        parser = _Parser(description=__doc__, allow_abbrev=False)
        parser.add_argument("--environment", required=True, type=Path)
        parser.add_argument("--evidence-dir", required=True, type=Path)
        parser.add_argument("--python", required=True, type=Path)
        parser.add_argument("--timeout-seconds", type=int, default=1800)
        args = parser.parse_args(argv)
        result = install_environment(args.environment, args.evidence_dir, python_executable=args.python,
                                     timeout=args.timeout_seconds)
        return 0 if result["installed_under_hash_locks"] else 130 if result["status"] == "cancelled" else 2
    except KeyboardInterrupt:
        print("Isolated installation cancelled. Its partial environment and private evidence are retained.", file=sys.stderr)
        return 130
    except Exception:
        print("Isolated installation failed. No existing environment was selected; any new partial environment and evidence are retained.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
