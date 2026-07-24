#!/usr/bin/env python3
"""Explicitly synchronize byte-pinned model artifacts for offline runtime use."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import model_artifacts  # noqa: E402
import release_security  # noqa: E402


def _disable_auxiliary_telemetry() -> None:
    """Disable Hub/Transformers usage telemetry before any network import."""
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["DO_NOT_TRACK"] = "1"
    os.environ["HF_HUB_DISABLE_XET"] = "1"
    os.environ["HF_HUB_ENABLE_HF_TRANSFER"] = "0"


def _selected_artifacts(model_ids: list[str]):
    if not model_ids:
        return model_artifacts.load_model_artifacts()
    selected = []
    seen = set()
    for model_id in model_ids:
        artifact = model_artifacts.model_artifact(model_id)
        if artifact is None:
            raise model_artifacts.ModelArtifactError(
                f"model is not in the reviewed lock: {model_id}")
        if artifact.model_id not in seen:
            seen.add(artifact.model_id)
            selected.append(artifact)
    return tuple(selected)


def sync_models(
        model_ids: list[str], *, cache_root: Path | None = None,
        security_policy: release_security.ReleaseSecurityPolicy | None = None,
        ) -> list[Path]:
    """Synchronize every safe runtime consumer for the selected models."""
    _disable_auxiliary_telemetry()
    policy = security_policy or release_security.ReleaseSecurityPolicy(
        model_download_policy="allow-reviewed-sync")
    release_security.require_model_download(
        policy, feature="reviewed model synchronization")
    endpoint = model_artifacts.HUGGINGFACE_HUB_OFFICIAL_ENDPOINT
    if policy.trust_environment_network:
        endpoint = os.environ.get("HF_ENDPOINT", endpoint) or endpoint
    synchronized = []
    for artifact in _selected_artifacts(model_ids):
        for consumer in artifact.consumers:
            if consumer.endswith("_remote_code"):
                # Reviewed code dependencies are copied automatically while
                # synchronizing the primary transformed model bundle.
                continue
            approved = artifact.files_for(consumer)
            if any(file.path.endswith(".bin") for file in approved):
                print(
                    f"SKIP {artifact.model_id}:{consumer} "
                    "(unsafe pickle weights)",
                    file=sys.stderr,
                )
                continue
            path = model_artifacts.verified_model_directory(
                artifact.model_id, consumer,
                cache_root=cache_root, allow_download=True,
                authorize_download_fn=lambda: (
                    release_security.require_model_download(
                        policy, feature="reviewed model synchronization")),
                download_endpoint=endpoint,
                trust_environment_network=policy.trust_environment_network)
            synchronized.append(path)
            print(f"SYNCED {artifact.model_id}:{consumer}")
    return synchronized


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=("Synchronize reviewed public model bytes before "
                     "processing private documents"),
        allow_abbrev=False,
    )
    parser.add_argument(
        "--model", action="append", default=[],
        help="Reviewed model ID or alias (repeatable; default: all)")
    parser.add_argument("--cache-root", type=Path)
    parser.add_argument(
        "--security-profile", choices=["release", "development"],
        default="release")
    parser.add_argument(
        "--trust-environment-network", action="store_true",
        help="Allow reviewed proxy/custom-CA environment overrides")
    parser.add_argument(
        "--list", action="store_true",
        help="List selected reviewed model consumers without downloading")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        artifacts = _selected_artifacts(args.model)
        if args.list:
            for artifact in artifacts:
                for consumer in artifact.consumers:
                    if not consumer.endswith("_remote_code"):
                        print(f"{artifact.model_id}:{consumer}")
            return 0
        policy = release_security.ReleaseSecurityPolicy(
            profile=args.security_profile,
            model_download_policy="allow-reviewed-sync",
            trust_environment_network=args.trust_environment_network,
        )
        synchronized = sync_models(
            args.model, cache_root=args.cache_root, security_policy=policy)
    except (OSError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    print(f"Synchronized {len(synchronized)} verified runtime bundle(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
