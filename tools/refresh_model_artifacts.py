#!/usr/bin/env python3
"""Resolve reviewed Hugging Face refs into the model-artifact lock."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import model_artifacts  # noqa: E402


def refresh(
        policy_path: Path, *,
        fetch_fn=model_artifacts.fetch_hub_model,
        file_sha256_fn=model_artifacts.fetch_hub_file_sha256,
        pypi_fetch_fn=model_artifacts.fetch_pypi_release,
        wheel_fetch_fn=model_artifacts.fetch_url_bytes,
) -> dict:
    """Resolve every policy ref through *fetch_fn* and build lock data."""
    policy_data = model_artifacts.load_json(policy_path)
    policies = model_artifacts.validate_model_policy(policy_data)
    payloads = {}
    for record in policies:
        model_id = record["model_id"]
        payloads[model_id] = fetch_fn(
            model_id, record["source_revision"])
    package_payloads = {
        record["package"]: pypi_fetch_fn(
            record["package"], record["version"])
        for record in model_artifacts.validate_package_model_policy(policy_data)
    }
    return model_artifacts.build_model_artifact_lock(
        policy_data,
        payloads,
        pypi_payloads=package_payloads,
        file_sha256_fn=file_sha256_fn,
        wheel_fetch_fn=wheel_fetch_fn,
    )


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--policy",
        type=Path,
        default=model_artifacts.MODEL_ARTIFACT_POLICY_PATH,
        help="reviewed model artifact policy",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=model_artifacts.MODEL_ARTIFACT_LOCK_PATH,
        help="resolved lockfile destination",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if resolving policy refs would change the lock",
    )
    args = parser.parse_args(argv)
    try:
        lock = refresh(args.policy.resolve())
        payload = model_artifacts.json_bytes(lock)
        output = args.output.resolve()
        if args.check:
            try:
                current = output.read_bytes()
            except OSError as exc:
                raise model_artifacts.ModelArtifactError(
                    f"could not read {output}: {exc}") from exc
            if current != payload:
                print(
                    "Model artifact lock is stale; run "
                    "python tools/refresh_model_artifacts.py",
                    file=sys.stderr,
                )
                return 1
            print("Model artifact lock matches current reviewed refs.")
            return 0
        _atomic_write(output, payload)
    except model_artifacts.ModelArtifactError as exc:
        print(f"Model artifact refresh failed: {exc}", file=sys.stderr)
        return 1
    print(f"Wrote model artifact lock: {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
