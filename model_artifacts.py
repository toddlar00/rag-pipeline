"""Pinned model-artifact policy, lock, runtime verification, and ML-BOM.

This module is intentionally standard-library-only. Runtime callers use the
small revision lookup surface; supply-chain tooling also uses the strict policy,
Hub-response, checksum-inventory, and CycloneDX validation helpers.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.resources
import io
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import threading
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin, urlsplit
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parent
MODEL_ARTIFACT_POLICY_PATH = PROJECT_ROOT / "model-artifact-policy.json"
MODEL_ARTIFACT_LOCK_PATH = PROJECT_ROOT / "model-artifacts.lock.json"

_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")
_CONSUMER_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_HUB_RESPONSE_BYTES = 10 * 1024 * 1024
_MAX_HUB_REGULAR_FILE_BYTES = 16 * 1024 * 1024
_MAX_PYPI_RESPONSE_BYTES = 5 * 1024 * 1024
_MAX_PACKAGE_BYTES = 64 * 1024 * 1024
HUGGINGFACE_HUB_OFFICIAL_ENDPOINT = "https://huggingface.co"


class ModelArtifactError(ValueError):
    """Raised when model-artifact policy or lock data is unsafe or invalid."""


@dataclass(frozen=True)
class ModelArtifactFile:
    """One immutable file entry from a Hugging Face repository snapshot."""

    path: str
    size: int
    git_blob_sha1: str
    content_sha256: str
    lfs_sha256: str | None = None


@dataclass(frozen=True)
class RuntimeTransform:
    """Reviewed deterministic transformation for a local runtime bundle."""

    consumer: str
    path: str
    transform: str
    argument: str
    expected_occurrences: int
    output_sha256: str


@dataclass(frozen=True)
class PinnedModelArtifact:
    """One license- and revision-pinned model repository."""

    model_id: str
    aliases: tuple[str, ...]
    source_revision: str
    revision: str
    hub_license: str
    spdx_license: str
    consumers: tuple[str, ...]
    trust_remote_code: bool
    code_model_id: str | None
    runtime_files: tuple[tuple[str, tuple[str, ...]], ...]
    runtime_transforms: tuple[RuntimeTransform, ...]
    files: tuple[ModelArtifactFile, ...]

    def files_for(self, consumer: str) -> tuple[ModelArtifactFile, ...]:
        """Return the exact repository files approved for one consumer."""
        paths = dict(self.runtime_files).get(consumer)
        if paths is None:
            raise ModelArtifactError(
                f"{self.model_id} is not approved for consumer {consumer}")
        by_path = {file.path: file for file in self.files}
        return tuple(by_path[path] for path in paths)

    @property
    def purl(self) -> str:
        namespace, name = self.model_id.split("/", 1)
        return (
            "pkg:huggingface/"
            f"{quote(namespace, safe='')}/{quote(name, safe='')}@{self.revision}"
        )


@dataclass(frozen=True)
class PackageModelFile:
    """One model payload embedded in a pinned Python wheel."""

    path: str
    role: str
    size: int
    content_sha256: str


@dataclass(frozen=True)
class PinnedPackageModel:
    """Model payloads distributed inside one pinned Python package."""

    package: str
    version: str
    wheel_filename: str
    wheel_url: str
    wheel_size: int
    wheel_sha256: str
    spdx_license: str
    consumers: tuple[str, ...]
    files: tuple[PackageModelFile, ...]

    @property
    def purl(self) -> str:
        return f"pkg:pypi/{quote(self.package, safe='')}@{quote(self.version, safe='')}"


@dataclass(frozen=True)
class ModelArtifactRegistry:
    """One process-consistent, strictly validated model-lock snapshot."""

    artifacts: tuple[PinnedModelArtifact, ...]
    package_models: tuple[PinnedPackageModel, ...]
    lock_sha256: str


def _require_object(value: object, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ModelArtifactError(f"{context} must be a JSON object")
    return value


def _require_exact_keys(
        value: Mapping[str, object], *, required: set[str], optional: set[str],
        context: str) -> None:
    keys = set(value)
    missing = sorted(required - keys)
    unexpected = sorted(keys - required - optional)
    if missing:
        raise ModelArtifactError(
            f"{context} is missing fields: {', '.join(missing)}")
    if unexpected:
        raise ModelArtifactError(
            f"{context} has unexpected fields: {', '.join(unexpected)}")


def _require_string(value: object, context: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ModelArtifactError(f"{context} must be a non-empty trimmed string")
    if any(ord(character) < 32 for character in value):
        raise ModelArtifactError(f"{context} cannot contain control characters")
    return value


def _require_model_id(value: object, context: str) -> str:
    model_id = _require_string(value, context)
    if not _MODEL_ID_RE.fullmatch(model_id):
        raise ModelArtifactError(
            f"{context} must be a canonical namespace/model identifier")
    return model_id


def _require_string_list(
        value: object, context: str, *, pattern: re.Pattern[str] | None = None,
        allow_empty: bool = True) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ModelArtifactError(f"{context} must be a JSON array")
    result = tuple(
        _require_string(item, f"{context}[{index}]")
        for index, item in enumerate(value)
    )
    if not allow_empty and not result:
        raise ModelArtifactError(f"{context} cannot be empty")
    if len(set(result)) != len(result):
        raise ModelArtifactError(f"{context} cannot contain duplicates")
    if tuple(sorted(result)) != result:
        raise ModelArtifactError(f"{context} must be sorted")
    if pattern is not None:
        invalid = [item for item in result if not pattern.fullmatch(item)]
        if invalid:
            raise ModelArtifactError(
                f"{context} contains invalid values: {', '.join(invalid)}")
    return result


def _require_bool(value: object, context: str) -> bool:
    if not isinstance(value, bool):
        raise ModelArtifactError(f"{context} must be a boolean")
    return value


def _require_nonnegative_int(value: object, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ModelArtifactError(f"{context} must be a non-negative integer")
    return value


def _safe_repository_path(value: object, context: str) -> str:
    path = _require_string(value, context)
    pure_path = PurePosixPath(path)
    if (
        "\\" in path
        or pure_path.is_absolute()
        or path in {".", ".."}
        or any(part in {"", ".", ".."} for part in pure_path.parts)
    ):
        raise ModelArtifactError(
            f"{context} must be a safe repository-relative POSIX path")
    return path


def _validate_runtime_files(
        value: object, consumers: tuple[str, ...], context: str,
) -> dict[str, list[str]]:
    mapping = _require_object(value, context)
    if set(mapping) != set(consumers):
        raise ModelArtifactError(
            f"{context} keys must exactly match the approved consumers")
    normalized = {}
    for consumer in consumers:
        raw_paths = mapping[consumer]
        if not isinstance(raw_paths, list):
            raise ModelArtifactError(f"{context}.{consumer} must be a JSON array")
        paths = tuple(
            _safe_repository_path(path, f"{context}.{consumer}[{index}]")
            for index, path in enumerate(raw_paths)
        )
        if not paths:
            raise ModelArtifactError(f"{context}.{consumer} cannot be empty")
        if paths != tuple(sorted(paths)) or len(set(paths)) != len(paths):
            raise ModelArtifactError(
                f"{context}.{consumer} must be sorted and unique")
        normalized[consumer] = list(paths)
    return normalized


def _validate_runtime_transforms(
        value: object, runtime_files: Mapping[str, list[str]], context: str,
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ModelArtifactError(f"{context} must be a JSON array")
    transforms = []
    seen: set[tuple[str, str]] = set()
    for index, raw_transform in enumerate(value):
        item_context = f"{context}[{index}]"
        record = _require_object(raw_transform, item_context)
        _require_exact_keys(
            record,
            required={
                "consumer", "path", "transform", "argument",
                "expected_occurrences", "output_sha256",
            },
            optional=set(),
            context=item_context,
        )
        consumer = _require_string(record["consumer"], f"{item_context}.consumer")
        path = _safe_repository_path(record["path"], f"{item_context}.path")
        key = (consumer, path)
        if consumer not in runtime_files or path not in runtime_files[consumer]:
            raise ModelArtifactError(
                f"{item_context} must target an approved runtime file")
        if key in seen:
            raise ModelArtifactError(f"{item_context} duplicates a transform target")
        seen.add(key)
        transform = _require_string(
            record["transform"], f"{item_context}.transform")
        if transform != "strip_auto_map_repo_prefix_v1":
            raise ModelArtifactError(f"{item_context} uses an unknown transform")
        output_sha256 = record["output_sha256"]
        if not isinstance(output_sha256, str) or not _SHA256_RE.fullmatch(
                output_sha256):
            raise ModelArtifactError(
                f"{item_context}.output_sha256 must be a lowercase SHA-256")
        transforms.append({
            "consumer": consumer,
            "path": path,
            "transform": transform,
            "argument": _require_string(
                record["argument"], f"{item_context}.argument"),
            "expected_occurrences": _require_nonnegative_int(
                record["expected_occurrences"],
                f"{item_context}.expected_occurrences",
            ),
            "output_sha256": output_sha256,
        })
    if transforms != sorted(
            transforms, key=lambda item: (item["consumer"], item["path"])):
        raise ModelArtifactError(f"{context} must be sorted by consumer and path")
    return transforms


def validate_model_policy(data: object) -> tuple[dict[str, Any], ...]:
    """Validate and return normalized policy records."""
    root = _require_object(data, "model artifact policy")
    _require_exact_keys(
        root,
        required={"schema_version", "models", "package_models"},
        optional=set(),
        context="model artifact policy",
    )
    if root["schema_version"] != 2:
        raise ModelArtifactError("unsupported model artifact policy schema")
    if not isinstance(root["models"], list) or not root["models"]:
        raise ModelArtifactError("model artifact policy models cannot be empty")

    records: list[dict[str, Any]] = []
    all_names: set[str] = set()
    for index, raw_record in enumerate(root["models"]):
        context = f"model artifact policy models[{index}]"
        record = _require_object(raw_record, context)
        _require_exact_keys(
            record,
            required={
                "model_id", "source_revision", "hub_license",
                "spdx_license", "aliases", "consumers",
                "runtime_files", "trust_remote_code",
            },
            optional={"code_model_id", "runtime_transforms"},
            context=context,
        )
        model_id = _require_model_id(record["model_id"], f"{context}.model_id")
        aliases = _require_string_list(
            record["aliases"], f"{context}.aliases")
        for alias_index, alias in enumerate(aliases):
            _require_model_id(alias, f"{context}.aliases[{alias_index}]")
        names = (model_id, *aliases)
        duplicate = next((name for name in names if name in all_names), None)
        if duplicate is not None:
            raise ModelArtifactError(
                f"model identifier or alias is duplicated: {duplicate}")
        all_names.update(names)

        consumers = _require_string_list(
            record["consumers"], f"{context}.consumers",
            pattern=_CONSUMER_RE, allow_empty=False)
        runtime_files = _validate_runtime_files(
            record["runtime_files"], consumers, f"{context}.runtime_files")
        runtime_transforms = _validate_runtime_transforms(
            record.get("runtime_transforms", []), runtime_files,
            f"{context}.runtime_transforms",
        )
        trust_remote_code = _require_bool(
            record["trust_remote_code"], f"{context}.trust_remote_code")
        code_model_id = record.get("code_model_id")
        if code_model_id is not None:
            code_model_id = _require_model_id(
                code_model_id, f"{context}.code_model_id")
        if trust_remote_code and code_model_id is None:
            raise ModelArtifactError(
                f"{context} enables remote code without code_model_id")
        if not trust_remote_code and code_model_id is not None:
            raise ModelArtifactError(
                f"{context} declares code_model_id without remote code")

        records.append({
            "model_id": model_id,
            "source_revision": _require_string(
                record["source_revision"], f"{context}.source_revision"),
            "hub_license": _require_string(
                record["hub_license"], f"{context}.hub_license"),
            "spdx_license": _require_string(
                record["spdx_license"], f"{context}.spdx_license"),
            "aliases": list(aliases),
            "consumers": list(consumers),
            "runtime_files": runtime_files,
            **({"runtime_transforms": runtime_transforms}
               if runtime_transforms else {}),
            "trust_remote_code": trust_remote_code,
            **({"code_model_id": code_model_id}
               if code_model_id is not None else {}),
        })

    canonical_ids = {record["model_id"] for record in records}
    for record in records:
        code_model_id = record.get("code_model_id")
        if code_model_id is not None and code_model_id not in canonical_ids:
            raise ModelArtifactError(
                f"{record['model_id']} references unpinned remote code "
                f"repository {code_model_id}")
    return tuple(records)


def validate_package_model_policy(data: object) -> tuple[dict[str, Any], ...]:
    """Validate model payloads that ship inside pinned Python packages."""
    root = _require_object(data, "model artifact policy")
    if root.get("schema_version") != 2:
        raise ModelArtifactError("unsupported model artifact policy schema")
    raw_packages = root.get("package_models")
    if not isinstance(raw_packages, list):
        raise ModelArtifactError("model artifact package_models must be an array")
    records = []
    seen_packages: set[str] = set()
    for index, raw_package in enumerate(raw_packages):
        context = f"model artifact policy package_models[{index}]"
        record = _require_object(raw_package, context)
        _require_exact_keys(
            record,
            required={
                "package", "version", "wheel_filename", "spdx_license",
                "consumers", "files",
            },
            optional=set(),
            context=context,
        )
        package = _require_string(record["package"], f"{context}.package")
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", package):
            raise ModelArtifactError(f"{context}.package must be normalized")
        if package in seen_packages:
            raise ModelArtifactError(f"duplicate package model: {package}")
        seen_packages.add(package)
        consumers = _require_string_list(
            record["consumers"], f"{context}.consumers",
            pattern=_CONSUMER_RE, allow_empty=False,
        )
        raw_files = record["files"]
        if not isinstance(raw_files, list) or not raw_files:
            raise ModelArtifactError(f"{context}.files cannot be empty")
        files = []
        for file_index, raw_file in enumerate(raw_files):
            file_context = f"{context}.files[{file_index}]"
            item = _require_object(raw_file, file_context)
            _require_exact_keys(
                item, required={"path", "role"}, optional=set(),
                context=file_context,
            )
            files.append({
                "path": _safe_repository_path(
                    item["path"], f"{file_context}.path"),
                "role": _require_string(item["role"], f"{file_context}.role"),
            })
        paths = [item["path"] for item in files]
        if paths != sorted(paths) or len(set(paths)) != len(paths):
            raise ModelArtifactError(f"{context}.files must be sorted and unique")
        records.append({
            "package": package,
            "version": _require_string(record["version"], f"{context}.version"),
            "wheel_filename": _require_string(
                record["wheel_filename"], f"{context}.wheel_filename"),
            "spdx_license": _require_string(
                record["spdx_license"], f"{context}.spdx_license"),
            "consumers": list(consumers),
            "files": files,
        })
    return tuple(records)


def _normalize_hub_file(value: object, context: str) -> dict[str, Any]:
    record = _require_object(value, context)
    path = _safe_repository_path(record.get("rfilename"), f"{context}.rfilename")
    size = record.get("size")
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise ModelArtifactError(f"{context}.size must be a non-negative integer")
    blob_id = record.get("blobId")
    if not isinstance(blob_id, str) or not _SHA1_RE.fullmatch(blob_id):
        raise ModelArtifactError(f"{context}.blobId must be a lowercase Git SHA-1")
    normalized = {
        "path": path,
        "size": size,
        "git_blob_sha1": blob_id,
    }
    lfs = record.get("lfs")
    if lfs is not None:
        lfs_record = _require_object(lfs, f"{context}.lfs")
        lfs_sha256 = lfs_record.get("sha256")
        lfs_size = lfs_record.get("size")
        if not isinstance(lfs_sha256, str) or not _SHA256_RE.fullmatch(lfs_sha256):
            raise ModelArtifactError(
                f"{context}.lfs.sha256 must be a lowercase SHA-256")
        if lfs_size != size:
            raise ModelArtifactError(
                f"{context}.lfs.size must match the repository file size")
        normalized["lfs_sha256"] = lfs_sha256
    return normalized


def normalize_hub_model(
        policy_record: Mapping[str, Any], payload: object) -> dict[str, Any]:
    """Normalize one Hub API response into a deterministic lock record."""
    hub = _require_object(payload, f"Hub response for {policy_record['model_id']}")
    context = f"Hub response for {policy_record['model_id']}"
    if hub.get("id") != policy_record["model_id"]:
        raise ModelArtifactError(
            f"{context} resolved unexpected canonical id {hub.get('id')!r}")
    revision = hub.get("sha")
    if not isinstance(revision, str) or not _SHA1_RE.fullmatch(revision):
        raise ModelArtifactError(f"{context}.sha must be a full lowercase commit id")
    if hub.get("gated") not in {False, None}:
        raise ModelArtifactError(f"{context} unexpectedly requires gated access")
    card_data = _require_object(hub.get("cardData"), f"{context}.cardData")
    if card_data.get("license") != policy_record["hub_license"]:
        raise ModelArtifactError(
            f"{context} license {card_data.get('license')!r} does not match "
            f"policy {policy_record['hub_license']!r}")
    siblings = hub.get("siblings")
    if not isinstance(siblings, list) or not siblings:
        raise ModelArtifactError(f"{context}.siblings cannot be empty")
    files = sorted(
        (_normalize_hub_file(item, f"{context}.siblings[{index}]")
         for index, item in enumerate(siblings)),
        key=lambda item: item["path"],
    )
    paths = [item["path"] for item in files]
    if len(paths) != len(set(paths)):
        raise ModelArtifactError(f"{context}.siblings contains duplicate paths")
    return {
        **dict(policy_record),
        "revision": revision,
        "files": files,
    }


def add_hub_content_checksums(
        record: Mapping[str, Any], *,
        file_sha256_fn: Callable[[str, str, str, int, str], str],
) -> dict[str, Any]:
    files = []
    for raw_file in record["files"]:
        file = dict(raw_file)
        checksum = file.get("lfs_sha256")
        if checksum is None:
            checksum = file_sha256_fn(
                record["model_id"], record["revision"], file["path"],
                file["size"], file["git_blob_sha1"],
            )
        if not isinstance(checksum, str) or not _SHA256_RE.fullmatch(checksum):
            raise ModelArtifactError(
                f"invalid content SHA-256 for {record['model_id']}:{file['path']}")
        file["content_sha256"] = checksum
        files.append(file)
    return {**dict(record), "files": files}


def build_model_artifact_lock(
        policy_data: object,
        hub_payloads: Mapping[str, object], *,
        pypi_payloads: Mapping[str, object] | None = None,
        file_sha256_fn: Callable[[str, str, str, int, str], str] | None = None,
        wheel_fetch_fn: Callable[[str, int], bytes] | None = None,
) -> dict[str, Any]:
    """Build deterministic lock data from validated policy and Hub payloads."""
    policies = validate_model_policy(policy_data)
    package_policies = validate_package_model_policy(policy_data)
    missing = [
        record["model_id"] for record in policies
        if record["model_id"] not in hub_payloads
    ]
    unexpected = sorted(set(hub_payloads) - {
        record["model_id"] for record in policies})
    if missing:
        raise ModelArtifactError(
            "missing Hub responses: " + ", ".join(missing))
    if unexpected:
        raise ModelArtifactError(
            "unexpected Hub responses: " + ", ".join(unexpected))
    package_payloads = pypi_payloads or {}
    expected_packages = {record["package"] for record in package_policies}
    if set(package_payloads) != expected_packages:
        raise ModelArtifactError(
            "PyPI responses must exactly match package model policy")
    if file_sha256_fn is None:
        file_sha256_fn = fetch_hub_file_sha256
    if wheel_fetch_fn is None:
        wheel_fetch_fn = fetch_url_bytes
    package_models = []
    for policy in package_policies:
        normalized = normalize_pypi_package_model(
            policy, package_payloads[policy["package"]])
        wheel_bytes = wheel_fetch_fn(
            normalized["wheel_url"], normalized["wheel_size"])
        package_models.append(add_package_model_checksums(
            normalized, wheel_bytes))
    return {
        "schema_version": 2,
        "source": "https://huggingface.co",
        "models": [
            add_hub_content_checksums(
                normalize_hub_model(
                    record, hub_payloads[record["model_id"]]),
                file_sha256_fn=file_sha256_fn,
            )
            for record in policies
        ],
        "package_models": package_models,
    }


def fetch_hub_model(
        model_id: str, revision: str, *, timeout: float = 30.0,
        attempts: int = 3,
        opener: Callable[..., Any] = urlopen,
        sleep_fn: Callable[[float], None] = time.sleep,
) -> object:
    """Fetch one bounded public Hub model response with short retries."""
    _require_model_id(model_id, "Hub model id")
    _require_string(revision, "Hub model revision")
    if timeout <= 0 or attempts < 1:
        raise ModelArtifactError("Hub timeout and attempts must be positive")
    url = (
        "https://huggingface.co/api/models/"
        f"{quote(model_id, safe='/')}/revision/{quote(revision, safe='')}"
        "?blobs=true"
    )
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "rag-pipeline-model-artifact-audit/1",
        },
    )
    last_error: BaseException | None = None
    for attempt in range(attempts):
        try:
            with opener(request, timeout=timeout) as response:
                content_length = response.headers.get("Content-Length")
                if (
                    content_length is not None
                    and int(content_length) > _MAX_HUB_RESPONSE_BYTES
                ):
                    raise ModelArtifactError(
                        f"Hub response for {model_id} exceeds size limit")
                payload = response.read(_MAX_HUB_RESPONSE_BYTES + 1)
            if len(payload) > _MAX_HUB_RESPONSE_BYTES:
                raise ModelArtifactError(
                    f"Hub response for {model_id} exceeds size limit")
            try:
                return json.loads(payload.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise ModelArtifactError(
                    f"Hub returned invalid JSON for {model_id}: {exc}") from exc
        except ModelArtifactError:
            raise
        except (HTTPError, URLError, OSError, ValueError) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                sleep_fn(0.5 * (2 ** attempt))
    raise ModelArtifactError(
        f"could not fetch Hub metadata for {model_id}@{revision}: "
        f"{last_error}") from last_error


def fetch_hub_file_sha256(
        model_id: str, revision: str, path: str, expected_size: int,
        expected_git_blob_sha1: str, *, timeout: float = 30.0,
        attempts: int = 3, opener: Callable[..., Any] = urlopen,
        sleep_fn: Callable[[float], None] = time.sleep,
) -> str:
    """Download one ordinary Hub file and verify its size and Git object ID."""
    _require_model_id(model_id, "Hub model id")
    if not _SHA1_RE.fullmatch(revision):
        raise ModelArtifactError("Hub file revision must be a full commit id")
    safe_path = _safe_repository_path(path, "Hub file path")
    _require_nonnegative_int(expected_size, "Hub file expected size")
    if expected_size > _MAX_HUB_REGULAR_FILE_BYTES:
        raise ModelArtifactError(
            f"ordinary Hub file exceeds audit limit: {model_id}:{safe_path}")
    if not _SHA1_RE.fullmatch(expected_git_blob_sha1):
        raise ModelArtifactError("expected Git blob id must be a lowercase SHA-1")
    if timeout <= 0 or attempts < 1:
        raise ModelArtifactError("Hub timeout and attempts must be positive")
    url = (
        f"https://huggingface.co/{quote(model_id, safe='/')}/resolve/"
        f"{revision}/{quote(safe_path, safe='/')}?download=true"
    )
    request = Request(url, headers={
        "Accept": "application/octet-stream",
        "User-Agent": "rag-pipeline-model-artifact-audit/1",
    })
    last_error: BaseException | None = None
    for attempt in range(attempts):
        try:
            with opener(request, timeout=timeout) as response:
                content_length = response.headers.get("Content-Length")
                if content_length is not None and int(content_length) != expected_size:
                    raise ModelArtifactError(
                        f"Hub file size differs for {model_id}:{safe_path}")
                payload = response.read(expected_size + 1)
            if len(payload) != expected_size:
                raise ModelArtifactError(
                    f"Hub file size differs for {model_id}:{safe_path}")
            git_hash = hashlib.sha1(  # noqa: S324 - Git object identity by design.
                b"blob " + str(len(payload)).encode("ascii") + b"\0" + payload
            ).hexdigest()
            if git_hash != expected_git_blob_sha1:
                raise ModelArtifactError(
                    f"Hub file Git hash differs for {model_id}:{safe_path}")
            return hashlib.sha256(payload).hexdigest()
        except ModelArtifactError:
            raise
        except (HTTPError, URLError, OSError, ValueError) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                sleep_fn(0.5 * (2 ** attempt))
    raise ModelArtifactError(
        f"could not fetch Hub file {model_id}@{revision}:{safe_path}: "
        f"{last_error}") from last_error


def fetch_pypi_release(
        package: str, version: str, *, timeout: float = 30.0,
        attempts: int = 3, opener: Callable[..., Any] = urlopen,
        sleep_fn: Callable[[float], None] = time.sleep,
) -> object:
    """Fetch bounded public PyPI release metadata."""
    package = _require_string(package, "PyPI package")
    version = _require_string(version, "PyPI version")
    request = Request(
        f"https://pypi.org/pypi/{quote(package, safe='')}/"
        f"{quote(version, safe='')}/json",
        headers={
            "Accept": "application/json",
            "User-Agent": "rag-pipeline-model-artifact-audit/1",
        },
    )
    last_error: BaseException | None = None
    for attempt in range(attempts):
        try:
            with opener(request, timeout=timeout) as response:
                payload = response.read(_MAX_PYPI_RESPONSE_BYTES + 1)
            if len(payload) > _MAX_PYPI_RESPONSE_BYTES:
                raise ModelArtifactError(
                    f"PyPI response for {package} exceeds size limit")
            try:
                return json.loads(payload.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise ModelArtifactError(
                    f"PyPI returned invalid JSON for {package}: {exc}") from exc
        except ModelArtifactError:
            raise
        except (HTTPError, URLError, OSError, ValueError) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                sleep_fn(0.5 * (2 ** attempt))
    raise ModelArtifactError(
        f"could not fetch PyPI metadata for {package}=={version}: "
        f"{last_error}") from last_error


def normalize_pypi_package_model(
        policy_record: Mapping[str, Any], payload: object,
) -> dict[str, Any]:
    """Resolve one reviewed package-model policy against PyPI metadata."""
    root = _require_object(payload, f"PyPI response for {policy_record['package']}")
    info = _require_object(root.get("info"), "PyPI response info")
    if info.get("name", "").lower() != policy_record["package"]:
        raise ModelArtifactError("PyPI resolved an unexpected package name")
    if info.get("version") != policy_record["version"]:
        raise ModelArtifactError("PyPI resolved an unexpected package version")
    if info.get("license_expression") != policy_record["spdx_license"]:
        raise ModelArtifactError("PyPI package license differs from policy")
    urls = root.get("urls")
    if not isinstance(urls, list):
        raise ModelArtifactError("PyPI response urls must be an array")
    matches = [
        item for item in urls
        if isinstance(item, dict)
        and item.get("filename") == policy_record["wheel_filename"]
        and item.get("packagetype") == "bdist_wheel"
    ]
    if len(matches) != 1:
        raise ModelArtifactError("PyPI response must contain the reviewed wheel")
    wheel = matches[0]
    digests = _require_object(wheel.get("digests"), "PyPI wheel digests")
    wheel_sha256 = digests.get("sha256")
    if not isinstance(wheel_sha256, str) or not _SHA256_RE.fullmatch(wheel_sha256):
        raise ModelArtifactError("PyPI wheel must have a lowercase SHA-256")
    wheel_size = _require_nonnegative_int(wheel.get("size"), "PyPI wheel size")
    if wheel_size > _MAX_PACKAGE_BYTES:
        raise ModelArtifactError("PyPI wheel exceeds package audit limit")
    wheel_url = _require_string(wheel.get("url"), "PyPI wheel URL")
    if not wheel_url.startswith("https://files.pythonhosted.org/"):
        raise ModelArtifactError("PyPI wheel URL must use files.pythonhosted.org")
    return {
        **dict(policy_record),
        "wheel_url": wheel_url,
        "wheel_size": wheel_size,
        "wheel_sha256": wheel_sha256,
    }


def fetch_url_bytes(
        url: str, expected_size: int, *, timeout: float = 60.0,
        attempts: int = 3, opener: Callable[..., Any] = urlopen,
        sleep_fn: Callable[[float], None] = time.sleep,
) -> bytes:
    """Download a bounded approved package URL with retries."""
    if not url.startswith("https://files.pythonhosted.org/"):
        raise ModelArtifactError("package URL must use files.pythonhosted.org")
    _require_nonnegative_int(expected_size, "package expected size")
    if expected_size > _MAX_PACKAGE_BYTES or timeout <= 0 or attempts < 1:
        raise ModelArtifactError("package download bounds must be positive and safe")
    request = Request(url, headers={
        "Accept": "application/octet-stream",
        "User-Agent": "rag-pipeline-model-artifact-audit/1",
    })
    last_error: BaseException | None = None
    for attempt in range(attempts):
        try:
            with opener(request, timeout=timeout) as response:
                payload = response.read(expected_size + 1)
            if len(payload) != expected_size:
                raise ModelArtifactError("package download size differs from PyPI")
            return payload
        except ModelArtifactError:
            raise
        except (HTTPError, URLError, OSError, ValueError) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                sleep_fn(0.5 * (2 ** attempt))
    raise ModelArtifactError(f"could not download package: {last_error}") from last_error


def add_package_model_checksums(
        record: Mapping[str, Any], wheel_bytes: bytes,
) -> dict[str, Any]:
    """Verify a wheel and hash each reviewed embedded model payload."""
    if len(wheel_bytes) != record["wheel_size"]:
        raise ModelArtifactError("package wheel size differs from PyPI metadata")
    if hashlib.sha256(wheel_bytes).hexdigest() != record["wheel_sha256"]:
        raise ModelArtifactError("package wheel SHA-256 differs from PyPI metadata")
    try:
        with zipfile.ZipFile(io.BytesIO(wheel_bytes)) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)):
                raise ModelArtifactError("package wheel contains duplicate paths")
            locked_files = []
            for requested in record["files"]:
                try:
                    member = archive.getinfo(requested["path"])
                except KeyError as exc:
                    raise ModelArtifactError(
                        f"package wheel is missing {requested['path']}") from exc
                mode = member.external_attr >> 16
                if stat.S_ISLNK(mode) or member.is_dir():
                    raise ModelArtifactError(
                        f"package model is not a regular file: {requested['path']}")
                payload = archive.read(member)
                locked_files.append({
                    **requested,
                    "size": len(payload),
                    "content_sha256": hashlib.sha256(payload).hexdigest(),
                })
    except (OSError, zipfile.BadZipFile) as exc:
        raise ModelArtifactError(f"invalid package wheel: {exc}") from exc
    return {
        key: record[key]
        for key in (
            "package", "version", "wheel_filename", "wheel_url",
            "wheel_size", "wheel_sha256", "spdx_license", "consumers",
        )
    } | {"files": locked_files}


def _parse_locked_file(value: object, context: str) -> ModelArtifactFile:
    record = _require_object(value, context)
    _require_exact_keys(
        record,
        required={"path", "size", "git_blob_sha1", "content_sha256"},
        optional={"lfs_sha256"},
        context=context,
    )
    path = _safe_repository_path(record["path"], f"{context}.path")
    size = record["size"]
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise ModelArtifactError(f"{context}.size must be a non-negative integer")
    git_sha1 = record["git_blob_sha1"]
    if not isinstance(git_sha1, str) or not _SHA1_RE.fullmatch(git_sha1):
        raise ModelArtifactError(
            f"{context}.git_blob_sha1 must be a lowercase Git SHA-1")
    content_sha256 = record["content_sha256"]
    if not isinstance(content_sha256, str) or not _SHA256_RE.fullmatch(
            content_sha256):
        raise ModelArtifactError(
            f"{context}.content_sha256 must be a lowercase SHA-256")
    lfs_sha256 = record.get("lfs_sha256")
    if lfs_sha256 is not None and (
            not isinstance(lfs_sha256, str)
            or not _SHA256_RE.fullmatch(lfs_sha256)):
        raise ModelArtifactError(
            f"{context}.lfs_sha256 must be a lowercase SHA-256")
    if lfs_sha256 is not None and lfs_sha256 != content_sha256:
        raise ModelArtifactError(
            f"{context}.content_sha256 must match its LFS payload digest")
    return ModelArtifactFile(
        path=path,
        size=size,
        git_blob_sha1=git_sha1,
        content_sha256=content_sha256,
        lfs_sha256=lfs_sha256,
    )


def validate_model_artifact_lock(
        data: object, *, policy_data: object | None = None,
) -> tuple[PinnedModelArtifact, ...]:
    """Validate a lock and optionally prove it is synchronized with policy."""
    root = _require_object(data, "model artifact lock")
    _require_exact_keys(
        root,
        required={"schema_version", "source", "models", "package_models"},
        optional=set(),
        context="model artifact lock",
    )
    if root["schema_version"] != 2:
        raise ModelArtifactError("unsupported model artifact lock schema")
    if root["source"] != "https://huggingface.co":
        raise ModelArtifactError("model artifact lock source must be Hugging Face")
    models = root["models"]
    if not isinstance(models, list) or not models:
        raise ModelArtifactError("model artifact lock models cannot be empty")

    policy_records = (
        validate_model_policy(policy_data) if policy_data is not None else None)
    if policy_records is not None and len(policy_records) != len(models):
        raise ModelArtifactError("model artifact policy and lock counts differ")

    artifacts: list[PinnedModelArtifact] = []
    all_names: set[str] = set()
    for index, raw_model in enumerate(models):
        context = f"model artifact lock models[{index}]"
        record = _require_object(raw_model, context)
        _require_exact_keys(
            record,
            required={
                "model_id", "source_revision", "revision", "hub_license",
                "spdx_license", "aliases", "consumers",
                "runtime_files", "trust_remote_code", "files",
            },
            optional={"code_model_id", "runtime_transforms"},
            context=context,
        )
        model_id = _require_model_id(record["model_id"], f"{context}.model_id")
        aliases = _require_string_list(record["aliases"], f"{context}.aliases")
        for alias_index, alias in enumerate(aliases):
            _require_model_id(alias, f"{context}.aliases[{alias_index}]")
        for name in (model_id, *aliases):
            if name in all_names:
                raise ModelArtifactError(
                    f"model identifier or alias is duplicated: {name}")
            all_names.add(name)
        revision = record["revision"]
        if not isinstance(revision, str) or not _SHA1_RE.fullmatch(revision):
            raise ModelArtifactError(
                f"{context}.revision must be a full lowercase commit id")
        consumers = _require_string_list(
            record["consumers"], f"{context}.consumers",
            pattern=_CONSUMER_RE, allow_empty=False)
        runtime_files = _validate_runtime_files(
            record["runtime_files"], consumers, f"{context}.runtime_files")
        runtime_transforms = _validate_runtime_transforms(
            record.get("runtime_transforms", []), runtime_files,
            f"{context}.runtime_transforms",
        )
        trust_remote_code = _require_bool(
            record["trust_remote_code"], f"{context}.trust_remote_code")
        code_model_id = record.get("code_model_id")
        if code_model_id is not None:
            code_model_id = _require_model_id(
                code_model_id, f"{context}.code_model_id")
        if trust_remote_code != (code_model_id is not None):
            raise ModelArtifactError(
                f"{context} remote-code policy and code_model_id disagree")
        raw_files = record["files"]
        if not isinstance(raw_files, list) or not raw_files:
            raise ModelArtifactError(f"{context}.files cannot be empty")
        files = tuple(
            _parse_locked_file(item, f"{context}.files[{file_index}]")
            for file_index, item in enumerate(raw_files)
        )
        paths = tuple(file.path for file in files)
        if paths != tuple(sorted(paths)):
            raise ModelArtifactError(f"{context}.files must be sorted by path")
        if len(set(paths)) != len(paths):
            raise ModelArtifactError(f"{context}.files contains duplicate paths")
        locked_paths = set(paths)
        for consumer, approved_paths in runtime_files.items():
            missing_runtime = sorted(set(approved_paths) - locked_paths)
            if missing_runtime:
                raise ModelArtifactError(
                    f"{context}.runtime_files.{consumer} references missing files: "
                    + ", ".join(missing_runtime))

        artifact = PinnedModelArtifact(
            model_id=model_id,
            aliases=aliases,
            source_revision=_require_string(
                record["source_revision"], f"{context}.source_revision"),
            revision=revision,
            hub_license=_require_string(
                record["hub_license"], f"{context}.hub_license"),
            spdx_license=_require_string(
                record["spdx_license"], f"{context}.spdx_license"),
            consumers=consumers,
            trust_remote_code=trust_remote_code,
            code_model_id=code_model_id,
            runtime_files=tuple(
                (consumer, tuple(runtime_files[consumer]))
                for consumer in consumers
            ),
            runtime_transforms=tuple(
                RuntimeTransform(**transform) for transform in runtime_transforms),
            files=files,
        )
        if policy_records is not None:
            policy = policy_records[index]
            locked_policy = {
                key: record[key]
                for key in policy
            }
            if locked_policy != policy:
                raise ModelArtifactError(
                    f"{model_id} lock metadata is out of sync with policy")
        artifacts.append(artifact)

    canonical_ids = {artifact.model_id for artifact in artifacts}
    for artifact in artifacts:
        if (
            artifact.code_model_id is not None
            and artifact.code_model_id not in canonical_ids
        ):
            raise ModelArtifactError(
                f"{artifact.model_id} references unpinned remote code "
                f"repository {artifact.code_model_id}")
    validate_package_model_artifact_lock(data, policy_data=policy_data)
    return tuple(artifacts)


def validate_package_model_artifact_lock(
        data: object, *, policy_data: object | None = None,
) -> tuple[PinnedPackageModel, ...]:
    """Validate pinned model payloads embedded in Python packages."""
    root = _require_object(data, "model artifact lock")
    if root.get("schema_version") != 2:
        raise ModelArtifactError("unsupported model artifact lock schema")
    raw_packages = root.get("package_models")
    if not isinstance(raw_packages, list):
        raise ModelArtifactError("model artifact package_models must be an array")
    policies = (
        validate_package_model_policy(policy_data)
        if policy_data is not None else None
    )
    if policies is not None and len(policies) != len(raw_packages):
        raise ModelArtifactError("package model policy and lock counts differ")
    packages = []
    seen: set[str] = set()
    for index, raw_package in enumerate(raw_packages):
        context = f"model artifact lock package_models[{index}]"
        record = _require_object(raw_package, context)
        _require_exact_keys(
            record,
            required={
                "package", "version", "wheel_filename", "wheel_url",
                "wheel_size", "wheel_sha256", "spdx_license", "consumers",
                "files",
            },
            optional=set(),
            context=context,
        )
        package = _require_string(record["package"], f"{context}.package")
        if package in seen:
            raise ModelArtifactError(f"duplicate package model: {package}")
        seen.add(package)
        wheel_url = _require_string(record["wheel_url"], f"{context}.wheel_url")
        if not wheel_url.startswith("https://files.pythonhosted.org/"):
            raise ModelArtifactError(
                f"{context}.wheel_url must use files.pythonhosted.org")
        wheel_sha256 = record["wheel_sha256"]
        if not isinstance(wheel_sha256, str) or not _SHA256_RE.fullmatch(
                wheel_sha256):
            raise ModelArtifactError(
                f"{context}.wheel_sha256 must be a lowercase SHA-256")
        consumers = _require_string_list(
            record["consumers"], f"{context}.consumers",
            pattern=_CONSUMER_RE, allow_empty=False,
        )
        raw_files = record["files"]
        if not isinstance(raw_files, list) or not raw_files:
            raise ModelArtifactError(f"{context}.files cannot be empty")
        files = []
        for file_index, raw_file in enumerate(raw_files):
            file_context = f"{context}.files[{file_index}]"
            item = _require_object(raw_file, file_context)
            _require_exact_keys(
                item,
                required={"path", "role", "size", "content_sha256"},
                optional=set(),
                context=file_context,
            )
            checksum = item["content_sha256"]
            if not isinstance(checksum, str) or not _SHA256_RE.fullmatch(checksum):
                raise ModelArtifactError(
                    f"{file_context}.content_sha256 must be a lowercase SHA-256")
            files.append(PackageModelFile(
                path=_safe_repository_path(item["path"], f"{file_context}.path"),
                role=_require_string(item["role"], f"{file_context}.role"),
                size=_require_nonnegative_int(item["size"], f"{file_context}.size"),
                content_sha256=checksum,
            ))
        paths = tuple(file.path for file in files)
        if paths != tuple(sorted(paths)) or len(set(paths)) != len(paths):
            raise ModelArtifactError(f"{context}.files must be sorted and unique")
        package_model = PinnedPackageModel(
            package=package,
            version=_require_string(record["version"], f"{context}.version"),
            wheel_filename=_require_string(
                record["wheel_filename"], f"{context}.wheel_filename"),
            wheel_url=wheel_url,
            wheel_size=_require_nonnegative_int(
                record["wheel_size"], f"{context}.wheel_size"),
            wheel_sha256=wheel_sha256,
            spdx_license=_require_string(
                record["spdx_license"], f"{context}.spdx_license"),
            consumers=consumers,
            files=tuple(files),
        )
        if policies is not None:
            policy = policies[index]
            locked_policy = {
                key: record[key]
                for key in policy if key != "files"
            }
            locked_policy["files"] = [
                {"path": item["path"], "role": item["role"]}
                for item in record["files"]
            ]
            if locked_policy != policy:
                raise ModelArtifactError(
                    f"{package} package model lock is out of sync with policy")
        packages.append(package_model)
    return tuple(packages)


def load_json(path: Path) -> object:
    """Load one UTF-8 JSON object with deterministic error context."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ModelArtifactError(f"could not read {path}: {exc}") from exc


_MODEL_ARTIFACT_REGISTRIES: dict[tuple[str, str], ModelArtifactRegistry] = {}
_MODEL_ARTIFACT_REGISTRY_LOCK = threading.RLock()


def load_model_artifact_registry(
        lock_path: Path = MODEL_ARTIFACT_LOCK_PATH,
        policy_path: Path = MODEL_ARTIFACT_POLICY_PATH,
) -> ModelArtifactRegistry:
    """Return one immutable model registry shared by this process.

    Artifact records, package records, and the canonical lock digest are
    derived from the same validated JSON object under one lock. A running
    operation therefore cannot label bytes from a cached old registry with a
    newly read digest after an on-disk lock replacement.
    """
    resolved_lock = Path(lock_path).resolve()
    resolved_policy = Path(policy_path).resolve()
    key = (os.path.normcase(str(resolved_lock)),
           os.path.normcase(str(resolved_policy)))
    with _MODEL_ARTIFACT_REGISTRY_LOCK:
        cached = _MODEL_ARTIFACT_REGISTRIES.get(key)
        if cached is not None:
            return cached
        lock_data = load_json(resolved_lock)
        policy_data = load_json(resolved_policy)
        artifacts = validate_model_artifact_lock(
            lock_data, policy_data=policy_data)
        packages = validate_package_model_artifact_lock(
            lock_data, policy_data=policy_data)
        canonical = json.dumps(
            lock_data, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        registry = ModelArtifactRegistry(
            artifacts=artifacts,
            package_models=packages,
            lock_sha256=hashlib.sha256(canonical).hexdigest(),
        )
        _MODEL_ARTIFACT_REGISTRIES[key] = registry
        return registry


def clear_model_artifact_registry_cache() -> None:
    """Forget process snapshots before intentionally starting a new generation."""
    with _MODEL_ARTIFACT_REGISTRY_LOCK:
        _MODEL_ARTIFACT_REGISTRIES.clear()


def model_artifact_lock_sha256(
        lock_path: Path = MODEL_ARTIFACT_LOCK_PATH,
        policy_path: Path = MODEL_ARTIFACT_POLICY_PATH,
) -> str:
    """Return the canonical digest of the process model-registry snapshot."""
    return load_model_artifact_registry(lock_path, policy_path).lock_sha256


def load_model_artifacts(
        lock_path: Path = MODEL_ARTIFACT_LOCK_PATH,
        policy_path: Path = MODEL_ARTIFACT_POLICY_PATH,
) -> tuple[PinnedModelArtifact, ...]:
    """Load and strictly validate the committed model-artifact lock."""
    return load_model_artifact_registry(lock_path, policy_path).artifacts


def load_package_models(
        lock_path: Path = MODEL_ARTIFACT_LOCK_PATH,
        policy_path: Path = MODEL_ARTIFACT_POLICY_PATH,
) -> tuple[PinnedPackageModel, ...]:
    """Load and strictly validate package-embedded model artifacts."""
    return load_model_artifact_registry(
        lock_path, policy_path).package_models


def package_model(
        package: str, *, lock_path: Path = MODEL_ARTIFACT_LOCK_PATH,
        policy_path: Path = MODEL_ARTIFACT_POLICY_PATH,
) -> PinnedPackageModel | None:
    """Resolve one package-embedded model bundle from the lock."""
    return next((
        artifact for artifact in load_package_models(lock_path, policy_path)
        if artifact.package == package
    ), None)


def model_artifact(
        model_id: str, *, lock_path: Path = MODEL_ARTIFACT_LOCK_PATH,
        policy_path: Path = MODEL_ARTIFACT_POLICY_PATH,
) -> PinnedModelArtifact | None:
    """Resolve one canonical model ID or reviewed alias from the lock."""
    for artifact in load_model_artifacts(lock_path, policy_path):
        if model_id == artifact.model_id or model_id in artifact.aliases:
            return artifact
    return None


def pinned_model_kwargs(
        model_id: str, *, trust_remote_code: bool,
        lock_path: Path = MODEL_ARTIFACT_LOCK_PATH,
        policy_path: Path = MODEL_ARTIFACT_POLICY_PATH,
) -> dict[str, str]:
    """Return immutable ``revision``/``code_revision`` loader arguments.

    Unknown custom model IDs retain the historical unpinned behavior. Reviewed
    built-in models always receive a commit hash. If a caller enables remote
    code for a model whose policy does not permit it, loading fails closed.
    """
    artifact = model_artifact(
        model_id, lock_path=lock_path, policy_path=policy_path)
    if artifact is None:
        return {}
    result = {"revision": artifact.revision}
    if trust_remote_code:
        if not artifact.trust_remote_code or artifact.code_model_id is None:
            raise ModelArtifactError(
                f"remote code is not approved for pinned model {model_id}")
        code_artifact = model_artifact(
            artifact.code_model_id,
            lock_path=lock_path,
            policy_path=policy_path,
        )
        if code_artifact is None:
            raise ModelArtifactError(
                f"remote code repository is not pinned: {artifact.code_model_id}")
        result["code_revision"] = code_artifact.revision
    return result


def _sha256_path(path: Path) -> tuple[int, str]:
    size = 0
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while block := handle.read(1024 * 1024):
                size += len(block)
                digest.update(block)
    except OSError as exc:
        raise ModelArtifactError(f"could not hash model artifact {path}: {exc}") from exc
    return size, digest.hexdigest()


def _is_link_or_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError as exc:
        raise ModelArtifactError(f"could not inspect model artifact {path}: {exc}") from exc
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    attributes = getattr(info, "st_file_attributes", 0)
    return path.is_symlink() or bool(reparse_flag and attributes & reparse_flag)


def _verify_source_file(path: Path, expected: ModelArtifactFile) -> None:
    """Verify one immutable download before it enters a runtime bundle."""
    if not path.is_file():
        raise ModelArtifactError(f"model snapshot is missing {expected.path}")
    size, checksum = _sha256_path(path)
    if size != expected.size or checksum != expected.content_sha256:
        raise ModelArtifactError(
            f"model snapshot checksum differs for {expected.path}")


def _verify_runtime_tree(
        root: Path, expected: Mapping[str, tuple[int, str]],
) -> None:
    """Verify an isolated runtime tree and reject filesystem ambiguity."""
    if not root.is_dir() or _is_link_or_reparse(root):
        raise ModelArtifactError(f"runtime model root is unsafe: {root}")
    observed: dict[str, Path] = {}
    casefolded: set[str] = set()
    try:
        entries = tuple(root.rglob("*"))
    except OSError as exc:
        raise ModelArtifactError(f"could not inspect runtime model root: {exc}") from exc
    for path in entries:
        relative = path.relative_to(root).as_posix()
        folded = relative.casefold()
        if folded in casefolded:
            raise ModelArtifactError(
                f"runtime model tree has a case-folded path collision: {relative}")
        casefolded.add(folded)
        if _is_link_or_reparse(path):
            raise ModelArtifactError(
                f"runtime model tree contains a link or reparse point: {relative}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ModelArtifactError(
                f"runtime model tree contains a non-regular file: {relative}")
        info = path.stat()
        if getattr(info, "st_nlink", 1) != 1:
            raise ModelArtifactError(
                f"runtime model tree contains a hard-linked file: {relative}")
        observed[relative] = path
    if set(observed) != set(expected):
        missing = sorted(set(expected) - set(observed))
        unexpected = sorted(set(observed) - set(expected))
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if unexpected:
            details.append("unexpected " + ", ".join(unexpected))
        raise ModelArtifactError("runtime model tree differs: " + "; ".join(details))
    for relative, path in observed.items():
        size, checksum = _sha256_path(path)
        if (size, checksum) != expected[relative]:
            raise ModelArtifactError(
                f"runtime model checksum differs for {relative}")


def _copy_regular_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with source.open("rb") as source_handle, destination.open("xb") as output:
            shutil.copyfileobj(source_handle, output, length=1024 * 1024)
            output.flush()
            os.fsync(output.fileno())
    except OSError as exc:
        raise ModelArtifactError(
            f"could not materialize model artifact {destination}: {exc}") from exc


def _transformed_payload(source: bytes, transform: RuntimeTransform) -> bytes:
    if transform.transform != "strip_auto_map_repo_prefix_v1":
        raise ModelArtifactError(f"unsupported runtime transform: {transform.transform}")
    argument = transform.argument.encode("utf-8")
    occurrences = source.count(argument)
    if occurrences != transform.expected_occurrences:
        raise ModelArtifactError(
            f"runtime transform occurrence count differs for {transform.path}")
    output = source.replace(argument, b"")
    if hashlib.sha256(output).hexdigest() != transform.output_sha256:
        raise ModelArtifactError(
            f"runtime transform checksum differs for {transform.path}")
    return output


def _runtime_cache_root(cache_root: Path | None) -> Path:
    if cache_root is not None:
        return cache_root.resolve()
    configured = os.environ.get("RAG_MODEL_ARTIFACT_CACHE")
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.home() / ".cache" / "rag-pipeline" / "model-artifacts").resolve()


_TRANSFORMERS_MODULE_CACHE_LOCK = threading.Lock()
_TRANSFORMERS_MODULE_CACHE_TEMP: tempfile.TemporaryDirectory | None = None


def configure_transformers_dynamic_module_cache(
        *, cache_root: Path | None = None) -> Path:
    """Install one fresh process-private Transformers code cache.

    Transformers copies local ``trust_remote_code`` modules into a global
    import cache. Reusing that cache can execute previously tampered bytes
    before the verified source tree helps. Reserving a random empty directory
    before any Transformers import removes that stale-cache path; the
    temporary directory remains alive for the process so lazy imports work.
    """
    global _TRANSFORMERS_MODULE_CACHE_TEMP
    with _TRANSFORMERS_MODULE_CACHE_LOCK:
        created = _TRANSFORMERS_MODULE_CACHE_TEMP is None
        if created:
            try:
                if cache_root is None:
                    temporary = tempfile.TemporaryDirectory(
                        prefix="rag-transformers-modules-")
                else:
                    parent = _runtime_cache_root(cache_root) / "dynamic-modules"
                    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    os.chmod(parent, 0o700)
                    temporary = tempfile.TemporaryDirectory(
                        prefix="process-", dir=parent)
                os.chmod(temporary.name, 0o700)
            except OSError as exc:
                raise ModelArtifactError(
                    f"could not create isolated Transformers module cache: "
                    f"{exc}") from exc
            _TRANSFORMERS_MODULE_CACHE_TEMP = temporary

        cache_path = Path(_TRANSFORMERS_MODULE_CACHE_TEMP.name).resolve()
        cache_text = str(cache_path)
        os.environ["HF_MODULES_CACHE"] = cache_text

        # A tokenizer or another optional dependency may already have imported
        # Transformers. Update every public/internal binding used by supported
        # releases instead of relying only on the now-too-late environment.
        for module_name in (
            "transformers.dynamic_module_utils",
            "transformers.utils",
            "transformers.utils.hub",
        ):
            module = sys.modules.get(module_name)
            if module is not None and hasattr(module, "HF_MODULES_CACHE"):
                setattr(module, "HF_MODULES_CACHE", cache_text)

        if created:
            # A previously imported dynamic package must not mask the fresh
            # cache through sys.modules, and the new root wins path lookup.
            for module_name in tuple(sys.modules):
                if (module_name == "transformers_modules"
                        or module_name.startswith("transformers_modules.")):
                    sys.modules.pop(module_name, None)
        sys.path[:] = [entry for entry in sys.path if entry != cache_text]
        sys.path.insert(0, cache_text)
        return cache_path


def _runtime_bundle_identity(
        artifact: PinnedModelArtifact, consumer: str,
) -> str:
    approved = artifact.files_for(consumer)
    transforms = [
        transform for transform in artifact.runtime_transforms
        if transform.consumer == consumer
    ]
    code_revision = None
    if transforms and artifact.code_model_id is not None:
        code_artifact = model_artifact(artifact.code_model_id)
        code_revision = code_artifact.revision if code_artifact else None
    payload = {
        "model_id": artifact.model_id,
        "revision": artifact.revision,
        "consumer": consumer,
        "files": [
            [file.path, file.size, file.content_sha256] for file in approved
        ],
        "transforms": [transform.__dict__ for transform in transforms],
        "code_revision": code_revision,
    }
    return hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def _validated_hub_endpoint(endpoint: str) -> str:
    """Return a canonical HTTPS Hub endpoint without exposing credentials."""
    if not isinstance(endpoint, str):
        raise TypeError("model download endpoint must be text")
    if endpoint != endpoint.strip() or not endpoint.isascii():
        raise ModelArtifactError("model download endpoint is not canonical")
    try:
        parsed = urlsplit(endpoint)
        port = parsed.port
    except ValueError:
        raise ModelArtifactError("model download endpoint is malformed") from None
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or port not in {None, 443}
    ):
        raise ModelArtifactError(
            "model download endpoint must be credential-free HTTPS")
    path = parsed.path.rstrip("/")
    authority = parsed.hostname.casefold()
    return f"https://{authority}{path}"


def _model_download_session(requests_module, *, trust_environment_network: bool):
    """Build a session that may trust routing state but never ambient auth."""
    class _NoAmbientAuth(requests_module.auth.AuthBase):
        def __call__(self, request):
            request.headers.pop("Authorization", None)
            return request

    class _ModelDownloadSession(requests_module.Session):
        def get_redirect_target(self, response):
            target = super().get_redirect_target(response)
            if target is not None:
                resolved = urljoin(response.url, target)
                _require_https_model_download_url(
                    resolved, context="model download redirect")
            return target

        def rebuild_auth(self, prepared_request, _response):
            # Requests may rediscover .netrc credentials after a redirect even
            # when the initial request used explicit empty auth.  Reviewed
            # public artifacts never need authorization on Hub or CDN hosts.
            prepared_request.headers.pop("Authorization", None)

    session = _ModelDownloadSession()
    session.trust_env = trust_environment_network
    session.auth = _NoAmbientAuth()
    session.max_redirects = 5
    session.headers.clear()
    session.headers.update({
        "Accept-Encoding": "identity",
        "User-Agent": "rag-pipeline-model-sync/1",
    })
    return session


def _require_https_model_download_url(url: str, *, context: str) -> None:
    """Reject credential-bearing or plaintext model-download destinations."""
    try:
        parsed = urlsplit(url)
    except (TypeError, ValueError):
        raise ModelArtifactError(f"{context} URL is malformed") from None
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ModelArtifactError(
            f"{context} must remain credential-free HTTPS")


def _snapshot_model_repository(
        *, repo_id: str, revision: str, allow_patterns: list[str],
        endpoint: str, trust_environment_network: bool,
        destination: Path | None = None,
        expected_sizes: Mapping[str, int] | None = None,
        snapshot_download_fn: Callable[..., str] | None = None,
) -> str:
    """Download exact public files without delegating transport policy to an SDK."""
    kwargs = {
        "repo_id": repo_id,
        "revision": revision,
        "allow_patterns": allow_patterns,
        "endpoint": endpoint,
        # Reviewed artifacts are public.  Never discover or transmit an
        # ambient stored Hub credential for them.
        "token": False,
        "etag_timeout": 10,
    }
    if snapshot_download_fn is not None:
        return snapshot_download_fn(**kwargs)
    if destination is None or expected_sizes is None:
        raise ModelArtifactError(
            "direct model synchronization requires a private destination")
    try:
        import requests
    except ImportError as exc:
        raise ModelArtifactError(
            "requests is required to synchronize model artifacts") from exc

    destination.mkdir(parents=True, exist_ok=False)
    session = _model_download_session(
        requests, trust_environment_network=trust_environment_network)
    try:
        for file_path in allow_patterns:
            expected_size = expected_sizes.get(file_path)
            if isinstance(expected_size, bool) or not isinstance(
                    expected_size, int) or expected_size < 0:
                raise ModelArtifactError(
                    f"model file has no safe size bound: {file_path}")
            url = (
                f"{endpoint}/{quote(repo_id, safe='/')}/resolve/"
                f"{revision}/{quote(file_path, safe='/')}"
            )
            output = destination / Path(*PurePosixPath(file_path).parts)
            output.parent.mkdir(parents=True, exist_ok=True)
            try:
                with session.get(
                        url, stream=True, allow_redirects=True,
                        timeout=(10, 60)) as response:
                    _require_https_model_download_url(
                        getattr(response, "url", url),
                        context="final model download")
                    response.raise_for_status()
                    length = response.headers.get("Content-Length")
                    if length is not None and (
                        not length.isdecimal() or int(length) > expected_size
                    ):
                        raise ModelArtifactError(
                            f"model file exceeds its size bound: {file_path}")
                    written = 0
                    with output.open("xb") as handle:
                        for chunk in response.iter_content(chunk_size=1024 * 1024):
                            if not chunk:
                                continue
                            written += len(chunk)
                            if written > expected_size:
                                raise ModelArtifactError(
                                    "model file exceeds its size bound: "
                                    f"{file_path}")
                            handle.write(chunk)
                        handle.flush()
                        os.fsync(handle.fileno())
                    if written != expected_size:
                        raise ModelArtifactError(
                            f"model file size differs: {file_path}")
            except ModelArtifactError:
                raise
            except (OSError, requests.RequestException) as exc:
                raise ModelArtifactError(
                    f"model file download failed for {file_path}: "
                    f"{type(exc).__name__}") from exc
    finally:
        session.close()
    return str(destination)


def verified_model_directory(
        model_id: str, consumer: str, *, cache_root: Path | None = None,
        snapshot_download_fn: Callable[..., str] | None = None,
        allow_download: bool = False,
        authorize_download_fn: Callable[[], None] | None = None,
        download_endpoint: str = HUGGINGFACE_HUB_OFFICIAL_ENDPOINT,
        trust_environment_network: bool = False,
) -> Path:
    """Return an isolated, byte-verified local directory for a model loader.

    Runtime loaders never receive a Hub model ID. The initial synchronization
    resolves an immutable commit with an exact allowlist, verifies every byte,
    copies regular files into a private tree, and atomically publishes it.
    """
    if not isinstance(allow_download, bool):
        raise TypeError("allow_download must be boolean")
    if not isinstance(trust_environment_network, bool):
        raise TypeError("trust_environment_network must be boolean")
    download_endpoint = _validated_hub_endpoint(download_endpoint)
    if (
        not trust_environment_network
        and download_endpoint != HUGGINGFACE_HUB_OFFICIAL_ENDPOINT
    ):
        raise ModelArtifactError(
            "a custom model endpoint requires explicit environment trust")
    artifact = model_artifact(model_id)
    if artifact is None:
        raise ModelArtifactError(f"model is not in the reviewed lock: {model_id}")
    approved = artifact.files_for(consumer)
    pickle_files = [file.path for file in approved if file.path.endswith(".bin")]
    if pickle_files:
        raise ModelArtifactError(
            f"unsafe pickle model weights are blocked for {artifact.model_id}: "
            + ", ".join(pickle_files))
    transforms = {
        transform.path: transform
        for transform in artifact.runtime_transforms
        if transform.consumer == consumer
    }
    expected: dict[str, tuple[int, str]] = {}
    for file in approved:
        transform = transforms.get(file.path)
        if transform is None:
            expected[file.path] = (file.size, file.content_sha256)
        else:
            output_size = (
                file.size
                - transform.expected_occurrences
                * len(transform.argument.encode("utf-8"))
            )
            if output_size < 0:
                raise ModelArtifactError(
                    f"runtime transform has an invalid size for {file.path}")
            expected[file.path] = (output_size, transform.output_sha256)
    # Transformed sizes are calculated from the verified input below. The lock
    # pins their output checksum and deterministic occurrence count.
    identity = _runtime_bundle_identity(artifact, consumer)
    safe_name = artifact.model_id.replace("/", "--")
    target = _runtime_cache_root(cache_root) / (
        f"{safe_name}--{consumer}--{identity[:20]}")
    if target.exists():
        code_expected: dict[str, tuple[int, str]] = {}
        if transforms and artifact.code_model_id is not None:
            code_artifact = model_artifact(artifact.code_model_id)
            if code_artifact is None:
                raise ModelArtifactError("remote code artifact is missing")
            for file in code_artifact.files_for(f"{consumer}_remote_code"):
                code_expected[file.path] = (file.size, file.content_sha256)
        _verify_runtime_tree(target, expected | code_expected)
        return target

    if not allow_download:
        raise ModelArtifactError(
            f"reviewed model is not synchronized for {model_id}:{consumer}; "
            "run tools/sync_model_artifacts.py before processing private data"
        )
    if authorize_download_fn is None:
        raise ModelArtifactError(
            "model synchronization requires an explicit download authorizer")
    authorize_download_fn()

    target.parent.mkdir(parents=True, exist_ok=True)
    download_stage = Path(tempfile.mkdtemp(
        prefix=f".{target.name}.download.", dir=target.parent))
    code_files: tuple[ModelArtifactFile, ...] = ()
    code_root: Path | None = None
    try:
        source_root = Path(_snapshot_model_repository(
            repo_id=artifact.model_id,
            revision=artifact.revision,
            allow_patterns=[file.path for file in approved],
            endpoint=download_endpoint,
            trust_environment_network=trust_environment_network,
            destination=download_stage / "primary",
            expected_sizes={file.path: file.size for file in approved},
            snapshot_download_fn=snapshot_download_fn,
        ))
        for file in approved:
            _verify_source_file(
                source_root / Path(*PurePosixPath(file.path).parts), file)

        if transforms:
            if artifact.code_model_id is None:
                raise ModelArtifactError(
                    "runtime transform has no reviewed code model")
            code_artifact = model_artifact(artifact.code_model_id)
            if code_artifact is None:
                raise ModelArtifactError("remote code artifact is missing")
            code_consumer = f"{consumer}_remote_code"
            code_files = code_artifact.files_for(code_consumer)
            code_root = Path(_snapshot_model_repository(
                repo_id=code_artifact.model_id,
                revision=code_artifact.revision,
                allow_patterns=[file.path for file in code_files],
                endpoint=download_endpoint,
                trust_environment_network=trust_environment_network,
                destination=download_stage / "code",
                expected_sizes={file.path: file.size for file in code_files},
                snapshot_download_fn=snapshot_download_fn,
            ))
            for file in code_files:
                _verify_source_file(
                    code_root / Path(*PurePosixPath(file.path).parts), file)
    except BaseException:
        shutil.rmtree(download_stage, ignore_errors=True)
        raise

    stage: Path | None = None
    try:
        stage = Path(tempfile.mkdtemp(
            prefix=f".{target.name}.", dir=target.parent))
        for file in approved:
            source = source_root / Path(*PurePosixPath(file.path).parts)
            destination = stage / Path(*PurePosixPath(file.path).parts)
            transform = transforms.get(file.path)
            if transform is None:
                _copy_regular_file(source, destination)
                continue
            try:
                source_bytes = source.read_bytes()
            except OSError as exc:
                raise ModelArtifactError(
                    f"could not read transform source {source}: {exc}") from exc
            output = _transformed_payload(source_bytes, transform)
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                with destination.open("xb") as handle:
                    handle.write(output)
                    handle.flush()
                    os.fsync(handle.fileno())
            except OSError as exc:
                raise ModelArtifactError(
                    f"could not write transformed model file: {exc}") from exc
            expected[file.path] = (len(output), transform.output_sha256)
        if code_root is not None:
            for file in code_files:
                destination = stage / Path(*PurePosixPath(file.path).parts)
                if destination.exists():
                    raise ModelArtifactError(
                        f"remote code path collides in runtime bundle: {file.path}")
                _copy_regular_file(
                    code_root / Path(*PurePosixPath(file.path).parts), destination)
                expected[file.path] = (file.size, file.content_sha256)
        _verify_runtime_tree(stage, expected)
        try:
            os.rename(stage, target)
        except OSError:
            if not target.exists():
                raise
        _verify_runtime_tree(target, expected)
        return target
    finally:
        if stage is not None and stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
        if download_stage.exists():
            shutil.rmtree(download_stage, ignore_errors=True)


def verified_installed_package_model(
        package: str, consumer: str, *,
        artifact: PinnedPackageModel | None = None,
) -> dict[str, Path]:
    """Verify model payloads from an installed, exactly pinned Python package."""
    if artifact is None:
        artifact = package_model(package)
    elif artifact.package != package:
        raise ModelArtifactError(
            f"package model identity differs: {artifact.package} != {package}")
    if artifact is None or consumer not in artifact.consumers:
        raise ModelArtifactError(
            f"package model is not approved for {package}:{consumer}")
    try:
        installed_version = importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError as exc:
        raise ModelArtifactError(f"required model package is not installed: {package}") from exc
    if installed_version != artifact.version:
        raise ModelArtifactError(
            f"{package}=={artifact.version} is required; found {installed_version}")
    package_root = importlib.resources.files(package)
    verified = {}
    for file in artifact.files:
        parts = PurePosixPath(file.path).parts
        if not parts or parts[0] != package:
            raise ModelArtifactError(f"package model path is outside {package}")
        resource = package_root.joinpath(*parts[1:])
        path = Path(str(resource))
        if not path.is_file() or _is_link_or_reparse(path):
            raise ModelArtifactError(f"installed package model is unsafe: {file.path}")
        size, checksum = _sha256_path(path)
        if (size, checksum) != (file.size, file.content_sha256):
            raise ModelArtifactError(
                f"installed package model checksum differs: {file.path}")
        verified[file.role] = path
    return verified


_DOCLING_OCR_INSTALL_PATHS = {
    "classification": (
        "RapidOcr/onnx/PP-OCRv4/cls/"
        "ch_ppocr_mobile_v2.0_cls_mobile.onnx"
    ),
    "detection": "RapidOcr/onnx/PP-OCRv6/det/PP-OCRv6_det_small.onnx",
    "recognition": "RapidOcr/onnx/PP-OCRv6/rec/PP-OCRv6_rec_small.onnx",
}


def verified_docling_artifact_directory(
        *, include_ocr: bool, cache_root: Path | None = None,
        snapshot_download_fn: Callable[..., str] | None = None,
        package_files_fn: Callable[[str, str], dict[str, Path]] | None = None,
        allow_download: bool = False,
        authorize_download_fn: Callable[[], None] | None = None,
        download_endpoint: str = HUGGINGFACE_HUB_OFFICIAL_ENDPOINT,
        trust_environment_network: bool = False,
) -> Path:
    """Build Docling's exact local layout, TableFormer, and optional OCR tree."""
    layout = model_artifact("docling-project/docling-layout-heron")
    table = model_artifact("docling-project/docling-models")
    if layout is None or table is None:
        raise ModelArtifactError("Docling model artifacts are missing from the lock")
    package = package_model("rapidocr") if include_ocr else None
    identity_payload = {
        "layout": [layout.revision, [
            file.content_sha256 for file in layout.files_for("docling_layout")]],
        "table": [table.revision, [
            file.content_sha256
            for file in table.files_for("docling_table_structure")]],
        "ocr": ([package.version, [
            file.content_sha256 for file in package.files]]
            if package is not None else None),
    }
    identity = hashlib.sha256(json.dumps(
        identity_payload, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    target = _runtime_cache_root(cache_root) / (
        f"docling--{'ocr' if include_ocr else 'text'}--{identity[:20]}")

    expected: dict[str, tuple[int, str]] = {}
    sources: dict[str, Path] = {}
    for artifact, consumer in (
        (layout, "docling_layout"),
        (table, "docling_table_structure"),
    ):
        model_root = verified_model_directory(
            artifact.model_id,
            consumer,
            cache_root=cache_root,
            snapshot_download_fn=snapshot_download_fn,
            allow_download=allow_download,
            authorize_download_fn=authorize_download_fn,
            download_endpoint=download_endpoint,
            trust_environment_network=trust_environment_network,
        )
        prefix = artifact.model_id.replace("/", "--")
        for file in artifact.files_for(consumer):
            installed = f"{prefix}/{file.path}"
            expected[installed] = (file.size, file.content_sha256)
            sources[installed] = model_root / Path(*PurePosixPath(file.path).parts)

    if include_ocr:
        if package is None:
            raise ModelArtifactError("RapidOCR model artifacts are missing from the lock")
        if package_files_fn is None:
            package_files_fn = verified_installed_package_model
        package_files = package_files_fn("rapidocr", "docling_ocr")
        by_role = {file.role: file for file in package.files}
        if set(package_files) != set(_DOCLING_OCR_INSTALL_PATHS):
            raise ModelArtifactError("RapidOCR package model roles differ from policy")
        for role, installed in _DOCLING_OCR_INSTALL_PATHS.items():
            file = by_role[role]
            expected[installed] = (file.size, file.content_sha256)
            sources[installed] = package_files[role]

    if target.exists():
        _verify_runtime_tree(target, expected)
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{target.name}.", dir=target.parent))
    try:
        for installed, source in sources.items():
            _copy_regular_file(
                source, stage / Path(*PurePosixPath(installed).parts))
        _verify_runtime_tree(stage, expected)
        try:
            os.rename(stage, target)
        except OSError:
            if not target.exists():
                raise
        _verify_runtime_tree(target, expected)
        return target
    finally:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)


def _file_bom_ref(owner: str, path: str, checksum: str, *, derived: bool = False) -> str:
    kind = "derived-file" if derived else "file"
    identity = hashlib.sha256(
        f"{owner}\0{path}\0{checksum}\0{kind}".encode("utf-8")
    ).hexdigest()
    return f"urn:rag:model-artifact:{kind}:{identity}"


def model_artifact_sbom(
        artifacts: tuple[PinnedModelArtifact, ...],
        package_models: tuple[PinnedPackageModel, ...] = (),
) -> dict[str, Any]:
    """Build a deterministic CycloneDX 1.7 ML-BOM with byte-level files."""
    components: list[dict[str, Any]] = []
    dependencies: list[dict[str, Any]] = []
    for artifact in sorted(artifacts, key=lambda item: item.model_id):
        selected_paths = sorted({
            path
            for _consumer, paths in artifact.runtime_files
            for path in paths
        })
        by_path = {file.path: file for file in artifact.files}
        inventory = [
            {
                "path": path,
                "size": by_path[path].size,
                "sha256": by_path[path].content_sha256,
            }
            for path in selected_paths
        ]
        inventory_sha256 = hashlib.sha256(json.dumps(
            inventory, ensure_ascii=False, separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")).hexdigest()
        namespace, _name = artifact.model_id.split("/", 1)
        properties = {
            "rag:model-artifact:aliases": ",".join(artifact.aliases),
            "rag:model-artifact:code-model-id": artifact.code_model_id or "",
            "rag:model-artifact:consumers": ",".join(artifact.consumers),
            "rag:model-artifact:runtime-file-count": str(len(selected_paths)),
            "rag:model-artifact:runtime-inventory-sha256": inventory_sha256,
            "rag:model-artifact:source-file-count": str(len(artifact.files)),
            "rag:model-artifact:source-revision": artifact.source_revision,
            "rag:model-artifact:runtime-total-bytes": str(sum(
                by_path[path].size for path in selected_paths)),
            "rag:model-artifact:trust-remote-code": str(
                artifact.trust_remote_code).lower(),
        }
        components.append({
            "type": "machine-learning-model",
            "bom-ref": artifact.purl,
            "group": namespace,
            "name": artifact.model_id,
            "version": artifact.revision,
            "purl": artifact.purl,
            "licenses": [{"license": {"id": artifact.spdx_license}}],
            "externalReferences": [
                {
                    "type": "model-card",
                    "url": f"https://huggingface.co/{artifact.model_id}",
                },
                {
                    "type": "vcs",
                    "url": (
                        f"https://huggingface.co/{artifact.model_id}/tree/"
                        f"{artifact.revision}"
                    ),
                },
            ],
            "properties": [
                {"name": name, "value": value}
                for name, value in sorted(properties.items())
            ],
        })
        dependency_refs = []
        for path in selected_paths:
            file = by_path[path]
            file_ref = _file_bom_ref(
                artifact.model_id, path, file.content_sha256)
            dependency_refs.append(file_ref)
            file_properties = {
                "rag:model-artifact:git-blob-sha1": file.git_blob_sha1,
                "rag:model-artifact:model-id": artifact.model_id,
                "rag:model-artifact:path": path,
                "rag:model-artifact:size": str(file.size),
            }
            if file.lfs_sha256 is not None:
                file_properties["rag:model-artifact:lfs-sha256"] = (
                    file.lfs_sha256)
            components.append({
                "type": "file",
                "bom-ref": file_ref,
                "group": artifact.model_id,
                "name": path,
                "hashes": [{
                    "alg": "SHA-256",
                    "content": file.content_sha256,
                }],
                "properties": [
                    {"name": name, "value": value}
                    for name, value in sorted(file_properties.items())
                ],
            })
        for transform in artifact.runtime_transforms:
            derived_ref = _file_bom_ref(
                f"{artifact.model_id}:{transform.consumer}",
                transform.path,
                transform.output_sha256,
                derived=True,
            )
            dependency_refs.append(derived_ref)
            source_file = by_path[transform.path]
            derived_size = (
                source_file.size
                - transform.expected_occurrences
                * len(transform.argument.encode("utf-8"))
            )
            components.append({
                "type": "file",
                "bom-ref": derived_ref,
                "group": artifact.model_id,
                "name": f"{transform.consumer}/{transform.path}",
                "hashes": [{
                    "alg": "SHA-256",
                    "content": transform.output_sha256,
                }],
                "properties": [
                    {"name": "rag:model-artifact:consumer",
                     "value": transform.consumer},
                    {"name": "rag:model-artifact:derived-from-sha256",
                     "value": source_file.content_sha256},
                    {"name": "rag:model-artifact:size", "value": str(derived_size)},
                    {"name": "rag:model-artifact:transform",
                     "value": transform.transform},
                ],
            })
            dependencies.append({
                "ref": derived_ref,
                "dependsOn": [_file_bom_ref(
                    artifact.model_id, transform.path,
                    source_file.content_sha256)],
            })
        if artifact.code_model_id and artifact.code_model_id != artifact.model_id:
            code_artifact = next(
                item for item in artifacts
                if item.model_id == artifact.code_model_id
            )
            dependency_refs.append(code_artifact.purl)
        dependencies.append({
            "ref": artifact.purl,
            "dependsOn": sorted(set(dependency_refs)),
        })

    for package in sorted(package_models, key=lambda item: item.package):
        properties = {
            "rag:model-artifact:consumers": ",".join(package.consumers),
            "rag:model-artifact:source": "python-wheel",
            "rag:model-artifact:wheel-filename": package.wheel_filename,
            "rag:model-artifact:wheel-sha256": package.wheel_sha256,
        }
        components.append({
            "type": "machine-learning-model",
            "bom-ref": package.purl,
            "group": package.package,
            "name": f"{package.package} embedded model bundle",
            "version": package.version,
            "purl": package.purl,
            "licenses": [{"license": {"id": package.spdx_license}}],
            "externalReferences": [{
                "type": "distribution",
                "url": package.wheel_url,
                "hashes": [{
                    "alg": "SHA-256", "content": package.wheel_sha256,
                }],
            }],
            "properties": [
                {"name": name, "value": value}
                for name, value in sorted(properties.items())
            ],
        })
        file_refs = []
        for file in package.files:
            file_ref = _file_bom_ref(
                package.package, file.path, file.content_sha256)
            file_refs.append(file_ref)
            components.append({
                "type": "file",
                "bom-ref": file_ref,
                "group": package.package,
                "name": file.path,
                "hashes": [{
                    "alg": "SHA-256", "content": file.content_sha256,
                }],
                "properties": [
                    {"name": "rag:model-artifact:role", "value": file.role},
                    {"name": "rag:model-artifact:size", "value": str(file.size)},
                ],
            })
        dependencies.append({"ref": package.purl, "dependsOn": sorted(file_refs)})

    components.sort(key=lambda item: item["bom-ref"])
    dependencies.sort(key=lambda item: item["ref"])
    return {
        "$schema": "https://cyclonedx.org/schema/bom-1.7.schema.json",
        "bomFormat": "CycloneDX",
        "specVersion": "1.7",
        "version": 1,
        "components": components,
        "dependencies": dependencies,
    }


def json_bytes(data: object) -> bytes:
    """Serialize a stable, human-reviewable JSON artifact."""
    return (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
