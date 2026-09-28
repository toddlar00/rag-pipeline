"""Private create-only cleanup audit artifacts with final generation checks."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat

import chunking_core
import cleanup_audit
from evaluation_inputs import _strict_json_bytes
import ocr_recovery
from resource_lease import PathLease
import storage_policy


MAX_PDF_BYTES = 512 * 1024 * 1024
MAX_RECOVERY_BYTES = 64 * 1024 * 1024
MAX_PLAN_BYTES = 1024 * 1024
MAX_IMPLEMENTATION_BYTES = 1024 * 1024
MAX_OUTPUT_BYTES = 32 * 1024 * 1024
_CHUNK_BYTES = 1024 * 1024


def _identity(info) -> tuple:
    result = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
    return result if os.name == "nt" else result + (info.st_ctime_ns,)


def _snapshot(path: Path, limit: int, *, json_input: bool) -> tuple[object, str]:
    """Bound both same-handle passes, including files that grow while reading."""
    raw, digest = storage_policy._bounded_snapshot(
        path, limit, min_size=1, retain=json_input, chunk_size=lambda: _CHUNK_BYTES,
        identity=lambda info: _identity(info),
        invalid="cleanup input must be a bounded single-linked regular file",
        opened_changed="cleanup input generation changed",
        oversized="cleanup input exceeds streaming byte budget",
        changed="cleanup input changed during hashing")
    try:
        payload = _strict_json_bytes(raw, label="cleanup audit input", max_bytes=limit) if json_input else None
    except ValueError:
        raise ValueError("cleanup audit input requires bounded strict UTF-8 JSON") from None
    return payload, digest


def _distinct(paths: list[Path]) -> None:
    for path in paths:
        storage_policy.assert_no_link_components(path)
        if path.exists():
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("cleanup artifacts must be single-linked regular files")
    for index, path in enumerate(paths):
        for other in paths[index + 1:]:
            if path.resolve() == other.resolve() or (
                    path.exists() and other.exists() and os.path.samefile(path, other)):
                raise ValueError("cleanup inputs and output must be distinct")


def audit_cleanup_files(pdf_path: Path, recovery_path: Path, plan_path: Path, output_path: Path) -> dict:
    """Hash the PDF, replay core cleanup on selected saved text, publish no-clobber.

    Source-code digests describe observed module files, not native/loaded-bytecode
    attestation. No PDF parsing, OCR, source mutation, index or provider access.
    Link checks are not a component-pinned OS no-follow security guarantee.
    """
    paths = [Path(p).absolute() for p in (pdf_path, recovery_path, plan_path, output_path)]
    pdf_path, recovery_path, plan_path, output_path = paths
    code_specs = [(Path(cleanup_audit.__file__).absolute(), "cleanup_audit_sha256"),
                  (Path(chunking_core.__file__).absolute(), "chunking_core_sha256")]
    all_paths = paths + [path for path, _ in code_specs]
    _distinct(all_paths)
    with PathLease(output_path, backend="cleanup-audit", collection_name="report", operation="audit",
                   timeout=0, resource_description="cleanup fidelity audit report"):
        if output_path.exists():
            raise FileExistsError("cleanup audit output exists")
        specs = [(pdf_path, MAX_PDF_BYTES, False), (recovery_path, MAX_RECOVERY_BYTES, True),
                 (plan_path, MAX_PLAN_BYTES, True)]
        specs += [(path, MAX_IMPLEMENTATION_BYTES, False) for path, _ in code_specs]
        snapshots = [_snapshot(path, limit, json_input=is_json) for path, limit, is_json in specs]
        (_, source_digest), (recovery, recovery_digest), (plan, plan_digest) = snapshots[:3]
        report = cleanup_audit.build_cleanup_audit(recovery, plan, recovery_sha256=recovery_digest)
        if report["source_sha256"] != source_digest:
            raise ValueError("cleanup audit source differs from recovery")
        report["inputs"] = {"pdf_sha256": source_digest, "recovery_sha256": recovery_digest,
                            "plan_sha256": plan_digest}
        report["implementation"] = {
            "binding": "observed_module_source_snapshots_not_runtime_attestation",
            **{key: snapshot[1] for (_, key), snapshot in zip(code_specs, snapshots[3:])}}
        count, expected_output = 1, hashlib.sha256()  # Private JSON writer appends exactly one LF.
        for fragment in json.JSONEncoder(ensure_ascii=False, sort_keys=True, allow_nan=False, indent=2).iterencode(report):
            encoded = fragment.encode("utf-8")
            count += len(encoded)
            if count > MAX_OUTPUT_BYTES:
                raise ValueError("cleanup audit output exceeds byte budget")
            expected_output.update(encoded)
        expected_output.update(b"\n")

        def publish(temporary: Path, destination: Path) -> None:
            _distinct(all_paths)
            _, staged_digest = _snapshot(temporary, MAX_OUTPUT_BYTES, json_input=False)
            if staged_digest != expected_output.hexdigest():
                raise RuntimeError("cleanup audit staged output differs from computed report")
            for (path, limit, is_json), (_, digest) in zip(specs, snapshots):
                _, current = _snapshot(path, limit, json_input=is_json)
                if current != digest:
                    raise RuntimeError("cleanup audit input changed before publication")
            ocr_recovery._publish_new_report(temporary, destination)

        storage_policy.atomic_write_private_json(output_path, report, indent=2, replace_fn=publish)
    return report
