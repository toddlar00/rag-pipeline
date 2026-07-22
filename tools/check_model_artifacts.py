#!/usr/bin/env python3
"""Validate pinned model artifacts and emit their CycloneDX ML-BOM."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import model_artifacts  # noqa: E402


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


def verify_hub(
        policy_data: object, lock_data: object, *,
        fetch_fn=model_artifacts.fetch_hub_model,
        file_sha256_fn=model_artifacts.fetch_hub_file_sha256,
        pypi_fetch_fn=model_artifacts.fetch_pypi_release,
        wheel_fetch_fn=model_artifacts.fetch_url_bytes,
        package_resolve_fn=None,
) -> None:
    """Prove every committed checksum inventory against its pinned commit."""
    policies = {
        record["model_id"]: record
        for record in model_artifacts.validate_model_policy(policy_data)
    }
    lock = lock_data if isinstance(lock_data, dict) else {}
    raw_models = lock.get("models")
    if not isinstance(raw_models, list):
        raise model_artifacts.ModelArtifactError(
            "model artifact lock models must be a JSON array")
    for raw_model in raw_models:
        if not isinstance(raw_model, dict):
            raise model_artifacts.ModelArtifactError(
                "model artifact lock model must be an object")
        model_id = raw_model.get("model_id")
        revision = raw_model.get("revision")
        if model_id not in policies or not isinstance(revision, str):
            raise model_artifacts.ModelArtifactError(
                "model artifact lock is not synchronized with policy")
        payload = fetch_fn(model_id, revision)
        observed = model_artifacts.add_hub_content_checksums(
            model_artifacts.normalize_hub_model(
                policies[model_id], payload),
            file_sha256_fn=file_sha256_fn,
        )
        if observed != raw_model:
            raise model_artifacts.ModelArtifactError(
                f"Hub checksum inventory differs for {model_id}@{revision}")
    package_policies = {
        record["package"]: record
        for record in model_artifacts.validate_package_model_policy(policy_data)
    }
    if package_resolve_fn is None:
        def package_resolve_fn(policy, payload):
            normalized = model_artifacts.normalize_pypi_package_model(
                policy, payload)
            return model_artifacts.add_package_model_checksums(
                normalized,
                wheel_fetch_fn(
                    normalized["wheel_url"], normalized["wheel_size"]),
            )
    raw_packages = lock.get("package_models")
    if not isinstance(raw_packages, list):
        raise model_artifacts.ModelArtifactError(
            "model artifact lock package_models must be an array")
    for raw_package in raw_packages:
        if not isinstance(raw_package, dict):
            raise model_artifacts.ModelArtifactError(
                "model artifact package model must be an object")
        package = raw_package.get("package")
        if package not in package_policies:
            raise model_artifacts.ModelArtifactError(
                "package model lock is not synchronized with policy")
        policy = package_policies[package]
        payload = pypi_fetch_fn(package, policy["version"])
        observed = package_resolve_fn(policy, payload)
        if observed != raw_package:
            raise model_artifacts.ModelArtifactError(
                f"PyPI model payload inventory differs for {package}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--policy",
        type=Path,
        default=model_artifacts.MODEL_ARTIFACT_POLICY_PATH,
        help="reviewed model artifact policy",
    )
    parser.add_argument(
        "--lock",
        type=Path,
        default=model_artifacts.MODEL_ARTIFACT_LOCK_PATH,
        help="resolved model artifact lock",
    )
    parser.add_argument(
        "--verify-hub",
        action="store_true",
        help="compare every file checksum with its pinned Hub commit",
    )
    parser.add_argument(
        "--output-sbom",
        type=Path,
        help="write a deterministic CycloneDX ML-BOM companion report",
    )
    parser.add_argument(
        "--verify-installed-packages",
        action="store_true",
        help="rehash model payloads from their installed Python packages",
    )
    args = parser.parse_args(argv)
    try:
        policy_data = model_artifacts.load_json(args.policy.resolve())
        lock_data = model_artifacts.load_json(args.lock.resolve())
        artifacts = model_artifacts.validate_model_artifact_lock(
            lock_data, policy_data=policy_data)
        package_models = model_artifacts.validate_package_model_artifact_lock(
            lock_data, policy_data=policy_data)
        if args.verify_hub:
            verify_hub(policy_data, lock_data)
        if args.verify_installed_packages:
            for package in package_models:
                for consumer in package.consumers:
                    model_artifacts.verified_installed_package_model(
                        package.package, consumer, artifact=package)
        if args.output_sbom is not None:
            _atomic_write(
                args.output_sbom.resolve(),
                model_artifacts.json_bytes(
                    model_artifacts.model_artifact_sbom(
                        artifacts, package_models)),
            )
    except model_artifacts.ModelArtifactError as exc:
        print(f"Model artifact policy failed: {exc}", file=sys.stderr)
        return 1
    total_files = sum(len(artifact.files) for artifact in artifacts) + sum(
        len(artifact.files) for artifact in package_models)
    remote_code = sum(
        artifact.trust_remote_code for artifact in artifacts)
    checks = []
    if args.verify_hub:
        checks.append("remote source checksums")
    if args.verify_installed_packages:
        checks.append("installed package payloads")
    suffix = " and " + " and ".join(checks) if checks else ""
    print(
        f"Model artifact policy{suffix} passed: "
        f"{len(artifacts) + len(package_models)} models, "
        f"{total_files} files, {remote_code} remote-code approvals."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
