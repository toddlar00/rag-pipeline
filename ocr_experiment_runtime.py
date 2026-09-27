"""Bounded runtime observations and hash-lock *version* checks, not attestation.

Installed metadata can lie and matching versions do not verify wheel bytes or
loaded native libraries. This manifest therefore never claims a verified locked
environment. It records those limits along with actual already-loaded versions
and explicitly caller-observed execution details, without importing OCR engines.
"""

from __future__ import annotations

import importlib.metadata
import math
import os
import platform
import re
import stat
import sys
from pathlib import Path

from evaluation_inputs import _hex_digest, _read_snapshot


MAX_LOCK_BYTES = 8 * 1024 * 1024
DEFAULT_OCR_PACKAGES = ("pymupdf", "opencv-python", "numpy", "rapidocr", "onnxruntime")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9.!+_-]{0,127}")
_PIN = re.compile(r"([A-Za-z0-9][A-Za-z0-9._-]{0,127})==([A-Za-z0-9][A-Za-z0-9.!+_-]{0,127})(?:\s*;\s*(.*))?")
_TOKEN = re.compile(r"\s*(?:(?P<string>'[^'\r\n]*'|\"[^\"\r\n]*\")|(?P<operator>==|!=|<=|>=|<|>)|(?P<word>[A-Za-z_]+)|(?P<paren>[()]))")
_MODULES = {"pymupdf": (("pymupdf", "VersionBind"), ("fitz", "VersionBind")),
            "opencv-python": (("cv2", "__version__"),), "numpy": (("numpy", "__version__"),),
            "rapidocr": (("rapidocr", "__version__"),), "onnxruntime": (("onnxruntime", "__version__"),)}


def _safe(value: object, pattern: re.Pattern = _NAME) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ValueError("runtime identifier or version has invalid syntax")
    return value


def _name(value: object) -> str:
    return re.sub(r"[-_.]+", "-", _safe(value)).lower()


def _environment() -> dict[str, str]:
    return {"python_full_version": platform.python_version(),
            "python_version": ".".join(platform.python_version_tuple()[:2]),
            "os_name": os.name, "sys_platform": sys.platform,
            "platform_machine": platform.machine(),
            "platform_python_implementation": platform.python_implementation()}


def _comparison(variable: str, operator: str, literal: str, environment: dict) -> bool:
    if variable not in environment:
        raise ValueError("unsupported lock marker variable")
    actual = environment[variable]
    if variable in {"python_version", "python_full_version"}:
        if not re.fullmatch(r"\d+(?:\.\d+){0,3}(?:\.\*)?", literal):
            raise ValueError("unsupported lock marker version")
        expected = tuple(int(part) for part in literal.removesuffix(".*").split("."))
        observed = tuple(int(part) for part in actual.split("."))
        if literal.endswith(".*"):
            if operator not in {"==", "!="}:
                raise ValueError("unsupported wildcard lock marker")
            same = observed[:len(expected)] == expected
            return same if operator == "==" else not same
        size = max(len(expected), len(observed))
        actual, literal = observed + (0,) * (size - len(observed)), expected + (0,) * (size - len(expected))
    elif operator not in {"==", "!="}:
        raise ValueError("unsupported non-version lock comparison")
    return {"==": actual == literal, "!=": actual != literal, "<": actual < literal,
            "<=": actual <= literal, ">": actual > literal, ">=": actual >= literal}[operator]


def _marker_applies(expression: str, environment: dict) -> bool:
    """Interpret the bounded generated-lock subset without eval or imports."""
    if not expression:
        return True
    if len(expression) > 2048:
        raise ValueError("lock marker exceeds limit")
    tokens, offset = [], 0
    while offset < len(expression):
        match = _TOKEN.match(expression, offset)
        if match is None:
            raise ValueError("unsupported lock marker syntax")
        tokens.append((match.lastgroup, match.group(match.lastgroup)))
        offset = match.end()
    if len(tokens) > 256:
        raise ValueError("lock marker exceeds token limit")
    cursor = 0

    def term(depth):
        nonlocal cursor
        if depth > 32 or cursor >= len(tokens):
            raise ValueError("invalid lock marker expression")
        if tokens[cursor] == ("paren", "("):
            cursor += 1
            result = either(depth + 1)
            if cursor >= len(tokens) or tokens[cursor] != ("paren", ")"):
                raise ValueError("invalid lock marker parentheses")
            cursor += 1
            return result
        triple = tokens[cursor:cursor + 3]
        if len(triple) != 3 or [item[0] for item in triple] != ["word", "operator", "string"]:
            raise ValueError("unsupported lock marker comparison")
        cursor += 3
        return _comparison(triple[0][1], triple[1][1], triple[2][1][1:-1], environment)

    def both(depth):
        nonlocal cursor
        result = term(depth)
        while cursor < len(tokens) and tokens[cursor] == ("word", "and"):
            cursor += 1
            right = term(depth)
            result = result and right
        return result

    def either(depth):
        nonlocal cursor
        result = both(depth)
        while cursor < len(tokens) and tokens[cursor] == ("word", "or"):
            cursor += 1
            right = both(depth)
            result = result or right
        return result

    result = either(0)
    if cursor != len(tokens):
        raise ValueError("invalid lock marker trailing tokens")
    return result


def _lock_records(raw: bytes) -> dict[str, list[tuple[str, str]]]:
    try:
        lines = raw.decode("utf-8-sig").splitlines()
    except UnicodeError:
        raise ValueError("runtime lock is not UTF-8") from None
    records, pending = {}, ""
    for raw_line in lines:
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        if not pending and line.startswith(("--index-url ", "--extra-index-url ")):
            continue
        continued = line.endswith("\\")
        pending += " " + (line[:-1].rstrip() if continued else line)
        if continued:
            continue
        requirement, *hashes = pending.strip().split(" --hash=")
        match = _PIN.fullmatch(requirement.strip())
        if not match or not hashes or any(not re.fullmatch(r"sha256:[a-f0-9]{64}", item.strip()) for item in hashes):
            raise ValueError("runtime lock requires exact pins and SHA-256 hashes")
        records.setdefault(_name(match[1]), []).append((match[2], (match[3] or "").strip()))
        if sum(map(len, records.values())) > 4096:
            raise ValueError("runtime lock exceeds record limit")
        pending = ""
    if pending or not records:
        raise ValueError("runtime lock is incomplete or empty")
    return records


def _effective(payload: object) -> dict | None:
    if payload is None:
        return None
    keys = {"engine", "execution_providers", "thread_settings", "elapsed_seconds", "model_artifacts"}
    if not isinstance(payload, dict) or set(payload) != keys:
        raise ValueError("effective runtime has invalid fields")
    engine, threads = payload["engine"], payload["thread_settings"]
    if not isinstance(engine, dict) or set(engine) != {"name", "version"}:
        raise ValueError("effective engine has invalid fields")
    engine = {"name": _safe(engine["name"]), "version": _safe(engine["version"], _VERSION)}
    if not isinstance(threads, dict) or set(threads) != {"intra_op", "inter_op"}:
        raise ValueError("effective thread settings have invalid fields")
    if any(value is not None and (type(value) is not int or not 0 <= value <= 256) for value in threads.values()):
        raise ValueError("effective thread setting is outside bounds")
    providers, artifacts, elapsed = payload["execution_providers"], payload["model_artifacts"], payload["elapsed_seconds"]
    if not isinstance(providers, list) or len(providers) > 32:
        raise ValueError("effective providers exceed bounds")
    providers = [_safe(item) for item in providers]
    if len(set(providers)) != len(providers):
        raise ValueError("effective providers must be unique")
    if (type(elapsed) not in (int, float) or not 0 <= elapsed <= 86400
            or not math.isfinite(elapsed)):
        raise ValueError("effective elapsed time is outside bounds")
    if not isinstance(artifacts, list) or len(artifacts) > 32:
        raise ValueError("effective artifacts exceed bounds")
    result, ids = [], set()
    for artifact in artifacts:
        if not isinstance(artifact, dict) or set(artifact) != {"id", "sha256"}:
            raise ValueError("effective artifact has invalid fields")
        identifier = _safe(artifact["id"])
        if identifier in ids:
            raise ValueError("effective artifact identifiers must be unique")
        ids.add(identifier)
        result.append({"id": identifier, "sha256": _hex_digest(artifact["sha256"], label="runtime model digest")})
    return {"engine": engine, "execution_providers": providers, "thread_settings": dict(threads),
            "elapsed_seconds": elapsed, "model_artifacts": sorted(result, key=lambda item: item["id"])}


def _package_observation(name: str, records: dict, environment: dict) -> dict:
    expected, marker_supported = [], True
    for version, marker in records.get(name, []):
        try:
            if _marker_applies(marker, environment):
                expected.append(version)
        except ValueError:
            marker_supported = False
    try:
        installed = _safe(importlib.metadata.version(name), _VERSION)
    except importlib.metadata.PackageNotFoundError:
        installed = None
    except Exception:
        installed = None
    loaded = []
    for module_name, attribute in _MODULES.get(name, ((name.replace("-", "_"), "__version__"),)):
        module = sys.modules.get(module_name)
        if module is not None:
            value = vars(module).get(attribute)
            if isinstance(value, str) and _VERSION.fullmatch(value):
                loaded.append(value)
    if not marker_supported:
        status = "unsupported_marker"
    elif len(expected) > 1:
        status = "ambiguous_pin"
    elif not expected:
        status = "not_locked_for_environment"
    elif installed is None:
        status = "not_installed_or_unreadable"
    elif installed != expected[0]:
        status = "version_mismatch"
    else:
        status = "version_match"
    # OpenCV's distribution has a wheel build suffix absent from cv2.__version__.
    loaded_match = None if not loaded or len(expected) != 1 else all(
        value == expected[0] or (name == "opencv-python" and len(expected[0].split(".")) == 4
                                and value == expected[0].rsplit(".", 1)[0])
        for value in loaded)
    return {"name": name, "required_version": expected[0] if len(expected) == 1 else None,
            "installed_version": installed, "status": status,
            "loaded_versions": sorted(set(loaded)), "loaded_version_agrees": loaded_match}


def _lock_snapshot(path: Path) -> tuple[bytes, str]:
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise ValueError("runtime lock must be a single-linked regular file")
    result = _read_snapshot(path, label="runtime dependency lock", max_bytes=MAX_LOCK_BYTES)
    after = path.lstat()
    if (not stat.S_ISREG(after.st_mode) or after.st_nlink != 1
            or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)):
        raise ValueError("runtime lock identity changed during capture")
    return result


def capture_runtime_manifest(lock_path: Path, *, packages=DEFAULT_OCR_PACKAGES,
                             effective_runtime: object = None,
                             require_version_match: bool = False) -> dict:
    """Capture a path-free snapshot; optionally gate requested package versions.

    The gate covers only requested packages, not the complete environment, and
    never verifies wheels. Execution evidence must come from the actual caller,
    not an available-provider list or an unexecuted configuration guess.
    """
    if type(require_version_match) is not bool:
        raise ValueError("require_version_match must be a boolean")
    if not isinstance(packages, (tuple, list)) or not 1 <= len(packages) <= 256:
        raise ValueError("runtime package list exceeds bounds")
    names = [_name(item) for item in packages]
    if len(set(names)) != len(names):
        raise ValueError("runtime packages must be unique after normalization")
    evidence = _effective(effective_runtime)
    try:
        raw, digest = _lock_snapshot(Path(lock_path))
    except (OSError, ValueError, RuntimeError):
        raise ValueError("runtime dependency lock could not be safely read") from None
    records, environment = _lock_records(raw), _environment()
    observations = [_package_observation(name, records, environment) for name in sorted(names)]
    matches = all(item["status"] == "version_match" and item["loaded_version_agrees"] is not False
                  for item in observations)
    if require_version_match and not matches:
        raise ValueError("requested runtime package versions do not match the dependency lock")
    try:
        _, current_digest = _lock_snapshot(Path(lock_path))
    except (OSError, ValueError, RuntimeError):
        raise ValueError("runtime dependency lock could not be safely rechecked") from None
    if current_digest != digest:
        raise ValueError("runtime dependency lock changed during capture")
    return {"schema_version": 1, "kind": "ocr_experiment_runtime", "lock_sha256": digest,
            "environment": environment, "packages": observations,
            "version_check_scope": "requested_packages_only", "package_versions_match": matches,
            "effective_runtime": evidence, "effective_runtime_source": "caller_observation" if evidence else None,
            "locked_environment_verified": False,
            "artifact_verification": {"package_wheels": "not_verified", "model_files": "not_verified_by_capture",
                                      "loaded_native_libraries": "not_verified"},
            "requires_attention": True}
