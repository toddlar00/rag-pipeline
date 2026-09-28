"""Read-only generation identity for resumable OCR; no model downloads or loads."""

from __future__ import annotations

import importlib.metadata
import os
from pathlib import Path
import platform
import sys

from ocr_checkpoint import digest, validate_identity
from ocr_execution_receipt import environment_identity, validate_installation_evidence
from ocr_experiment_runtime import _name, _safe, _VERSION, capture_runtime_manifest
import storage_policy


ROOT = Path(__file__).resolve().parent


def _file_identity(info) -> tuple:
    result = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
    return result if os.name == "nt" else result + (info.st_ctime_ns,)


def bounded_snapshot(path: Path, limit: int) -> tuple[bytes, str]:
    """Bound both verification passes even if the source grows while open."""
    return storage_policy._bounded_snapshot(
        path, limit, min_size=0, retain=True, chunk_size=lambda: 1024 * 1024,
        identity=lambda info: _file_identity(info),
        invalid="checkpoint artifact must be a bounded single-linked regular file",
        opened_changed="checkpoint artifact identity changed",
        oversized="checkpoint artifact exceeds streaming byte budget",
        changed="checkpoint artifact changed during reading")


def capture_identity(*, installation_path: Path | None = None) -> dict:
    """Bind current metadata, interpreter files, producer files and verified models.

    This is local consistency, not wheel/native-library or loaded-byte
    attestation. Producer coverage is every immediate root/tools Python file;
    changing even an unrelated file in that conservative set blocks resume.
    """
    capture_runtime_manifest(ROOT / "requirements-full.lock", require_version_match=True)
    if installation_path is not None:
        validate_installation_evidence(installation_path)
    packages = {}
    for distribution in importlib.metadata.distributions():
        name = _name(distribution.metadata["Name"])
        if name in packages or len(packages) >= 512:
            raise ValueError("checkpoint installed inventory is ambiguous or too large")
        packages[name] = _safe(distribution.version, _VERSION)
    sources = {}
    for folder in (ROOT, ROOT / "tools"):
        for path in folder.glob("*.py"):
            if len(sources) >= 512 or path.stat().st_nlink != 1:
                raise ValueError("checkpoint producer inventory is unsafe")
            sources[path.relative_to(ROOT).as_posix()] = bounded_snapshot(path, 4 * 1024 * 1024)[1]
    from model_artifacts import package_model, verified_installed_package_model
    artifact = package_model("rapidocr")
    if artifact is None:
        raise ValueError("checkpoint approved OCR model is unavailable")
    verified_installed_package_model("rapidocr", "docling_ocr")
    models = sorted([{"id": item.role, "sha256": item.content_sha256, "bytes": item.size}
                     for item in artifact.files], key=lambda item: item["id"])
    executable, base = (storage_policy.interpreter_file(Path(path)) for path in (
        sys.executable, getattr(sys, "_base_executable", sys.executable)))
    identity = {
        "environment_sha256": environment_identity(Path(sys.prefix)),
        "python_sha256": bounded_snapshot(executable, 64 * 1024 * 1024)[1],
        "base_python_sha256": bounded_snapshot(base, 64 * 1024 * 1024)[1],
        "runtime_sha256": digest({"python": platform.python_version(), "implementation": sys.implementation.name,
                                  "platform": sys.platform, "machine": platform.machine(), "packages": packages}),
        "producer_sources": sources, "model_artifacts": models,
    }
    return validate_identity(identity)
