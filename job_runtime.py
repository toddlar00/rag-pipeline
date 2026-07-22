"""Private durable state primitives for background RAG jobs.

This module deliberately contains no subprocess or application imports.  It is
the persistence boundary shared by future CLI and UI adapters: command specs
are immutable, state publication is atomic, cancellation is attempt-bound, and
one cross-process lease serializes each job manager.

Public ``JobSummary`` values are safe to display.  ``JobExecution`` is an
explicitly private execution view because its arguments can contain source
paths; callers must never include it in public status responses.
"""

from __future__ import annotations

import errno
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import stat
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator, Mapping, Sequence
from uuid import uuid4

import storage_policy


JOB_SCHEMA_VERSION = 1
DEFAULT_JOB_ROOT = Path("output") / ".rag-jobs"
JOB_ROOT_ENV = "RAG_PIPELINE_JOB_ROOT"
JOB_ID_ENV = "RAG_PIPELINE_JOB_ID"
JOB_ATTEMPT_TOKEN_ENV = "RAG_PIPELINE_JOB_ATTEMPT_TOKEN"

# 4C.1 intentionally admits only bounded, non-interactive operations that are
# useful behind a future background manager.  Short read-only query/info and
# destructive storage policy commands stay foreground-only.
ALLOWED_JOB_COMMANDS = frozenset({
    "preprocess",
    "convert",
    "chunk",
    "index",
    "extract-questions",
    "generate-questions",
    "citations",
    "raptor",
    "brief",
    "export",
    "full",
    "batch",
})

SECRET_OPTIONS = frozenset({
    "--api-key",
    "--cloud-key",
    "--gemini-key",
})
RESERVED_JOB_OPTIONS = frozenset({
    "--operation-timeout",
    "--resume-run",
    "--run-events",
    "--run-id",
    "--run-report",
})

JOB_STATUSES = frozenset({
    "queued",
    "starting",
    "running",
    "cancel_requested",
    "succeeded",
    "partial",
    "failed",
    "cancelled",
    "interrupted",
})
TERMINAL_JOB_STATUSES = frozenset({
    "succeeded", "partial", "failed", "cancelled", "interrupted",
})
RESUMABLE_JOB_STATUSES = frozenset({
    "partial", "failed", "cancelled", "interrupted",
})

LEGAL_JOB_TRANSITIONS = {
    "queued": frozenset({"starting", "cancel_requested", "failed"}),
    "starting": frozenset({
        "running", "cancel_requested", "failed", "interrupted",
    }),
    "running": frozenset({
        "cancel_requested", "succeeded", "partial", "failed",
        "interrupted",
    }),
    "cancel_requested": frozenset({
        "cancelled", "succeeded", "partial", "failed", "interrupted",
    }),
    "succeeded": frozenset(),
    "partial": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
    "interrupted": frozenset(),
}

_JOB_ID = re.compile(r"^[0-9a-f]{32}$")
_ATTEMPT_TOKEN = re.compile(r"^[0-9a-f]{32}$")
_MAX_ARGUMENT_COUNT = 512
_MAX_ARGUMENT_BYTES = 64 * 1024
_MAX_TIMEOUT_SECONDS = 31 * 24 * 60 * 60
_MAX_SPEC_BYTES = 128 * 1024
_MAX_STATE_BYTES = 32 * 1024
_MAX_CANCEL_BYTES = 16 * 1024
_MAX_COUNTER = (1 << 63) - 1
_STORE_LOCK_NAME = ".store.lock"
_JOB_LOCK_NAME = ".lock"
_SPEC_NAME = "spec.json"
_STATE_NAME = "state.json"
_CANCEL_NAME = "cancel.json"
_BINDINGS_NAME = "bindings.json"
_BINDINGS_LOCK_NAME = ".bindings.lock"
_MAX_BINDINGS_BYTES = 256 * 1024
_POLL_INTERVAL_SECONDS = 0.025
PIPELINE_BINDING_STATUSES = frozenset({"allocated", "failed", "complete"})


class JobRuntimeError(Exception):
    """Base class for durable job-store failures."""


class JobValidationError(JobRuntimeError, ValueError):
    """Raised before invalid caller-controlled data reaches private storage."""


class JobNotFoundError(JobRuntimeError, LookupError):
    """Raised when a syntactically valid job ID has no job directory."""


class JobAlreadyExistsError(JobRuntimeError):
    """Raised when a requested job ID already has any filesystem entry."""


class JobCorruptError(JobRuntimeError):
    """Raised when persisted job data cannot be trusted."""


class JobStateError(JobRuntimeError):
    """Raised for stale attempts, revisions, or illegal state transitions."""


class JobBusyError(JobRuntimeError, TimeoutError):
    """Raised when a job or store lease cannot be acquired in time."""


@dataclass(frozen=True, slots=True)
class JobSummary:
    """Redacted job status safe for CLI, UI, and service responses."""

    job_id: str
    command: str
    status: str
    attempt_number: int
    revision: int
    created_at: float
    updated_at: float

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL_JOB_STATUSES

    def as_dict(self) -> dict:
        return {
            "schema_version": JOB_SCHEMA_VERSION,
            "job_id": self.job_id,
            "command": self.command,
            "status": self.status,
            "attempt_number": self.attempt_number,
            "revision": self.revision,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True, slots=True)
class JobExecution:
    """Private manager view of one exact attempt.

    ``argv`` and ``attempt_token`` are intentionally omitted from repr.  This
    record is not a public response contract.
    """

    job_id: str
    command: str
    argv: tuple[str, ...] = field(repr=False)
    timeout_seconds: float | None
    status: str
    attempt_number: int
    attempt_token: str = field(repr=False)
    revision: int


@dataclass(frozen=True, slots=True)
class PipelineRunBinding:
    """Private exact pipeline-run binding for one full/batch input."""

    item_index: int
    input_sha256: str = field(repr=False)
    run_name: str
    status: str


@dataclass(frozen=True, slots=True)
class JobWorkerContext:
    """Private worker identity loaded only from manager-owned environment."""

    store: "JobStore" = field(repr=False)
    execution: JobExecution = field(repr=False)


@dataclass(frozen=True, slots=True)
class _StoredSpec:
    job_id: str
    command: str
    argv: tuple[str, ...]
    timeout_seconds: float | None
    created_at: float


@dataclass(frozen=True, slots=True)
class _StoredState:
    job_id: str
    status: str
    attempt_number: int
    attempt_token: str
    revision: int
    spec_sha256: str
    created_at: float
    updated_at: float


@dataclass(frozen=True, slots=True)
class _CancelMarker:
    job_id: str
    attempt_number: int
    attempt_token: str
    requested_at: float


@dataclass(frozen=True, slots=True)
class _StoredBindings:
    job_id: str
    spec_sha256: str
    revision: int
    items: tuple[PipelineRunBinding, ...]


def _validate_job_id(job_id: str) -> str:
    if not isinstance(job_id, str) or not _JOB_ID.fullmatch(job_id):
        raise JobValidationError("job_id must be 32 lowercase hexadecimal characters")
    return job_id


def _validate_attempt_token(token: str) -> str:
    if not isinstance(token, str) or not _ATTEMPT_TOKEN.fullmatch(token):
        raise JobValidationError(
            "attempt_token must be 32 lowercase hexadecimal characters")
    return token


def _validate_command(command: str) -> str:
    if not isinstance(command, str) or command not in ALLOWED_JOB_COMMANDS:
        if command == "jobs":
            raise JobValidationError("nested background-job commands are not allowed")
        raise JobValidationError("command is not allowed for background execution")
    return command


def _validate_argv(argv: Sequence[str]) -> tuple[str, ...]:
    if isinstance(argv, (str, bytes)) or not isinstance(argv, Sequence):
        raise JobValidationError("argv must be a sequence of strings")
    if len(argv) > _MAX_ARGUMENT_COUNT:
        raise JobValidationError("background command has too many arguments")
    normalized = []
    total_bytes = 0
    for token in argv:
        if not isinstance(token, str):
            raise JobValidationError("every background command argument must be text")
        if "\x00" in token or "\r" in token or "\n" in token:
            raise JobValidationError("background command arguments cannot contain controls")
        try:
            total_bytes += len(token.encode("utf-8"))
        except UnicodeEncodeError as exc:
            raise JobValidationError(
                "background command arguments must be valid UTF-8 text") from exc
        option = token.split("=", 1)[0].lower()
        # argparse accepts unambiguous long-option abbreviations by default.
        # Reject prefixes too, otherwise ``--cloud-k VALUE`` could bypass the
        # durable-spec credential rule and later resolve to ``--cloud-key``.
        secret_option = option in SECRET_OPTIONS or (
            len(option) > 2
            and any(secret.startswith(option) for secret in SECRET_OPTIONS)
        )
        if secret_option:
            raise JobValidationError(
                "credential values cannot be submitted as background arguments; "
                "use provider environment configuration")
        reserved_option = option in RESERVED_JOB_OPTIONS or (
            len(option) > 2
            and any(reserved.startswith(option)
                    for reserved in RESERVED_JOB_OPTIONS)
        )
        if reserved_option:
            raise JobValidationError(
                "background manager owns timeout, resume binding, and run "
                "telemetry options")
        normalized.append(token)
    if total_bytes > _MAX_ARGUMENT_BYTES:
        raise JobValidationError("background command arguments are too large")
    return tuple(normalized)


def _validate_timeout(value: float | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise JobValidationError("timeout_seconds must be a finite positive number")
    normalized = float(value)
    if (not math.isfinite(normalized) or normalized <= 0
            or normalized > _MAX_TIMEOUT_SECONDS):
        raise JobValidationError(
            "timeout_seconds must be positive and no more than 31 days")
    return normalized


def _validate_timestamp(value, name: str) -> float:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(float(value)) or float(value) < 0):
        raise JobCorruptError(f"job {name} is invalid")
    return float(value)


def _validate_counter(value, name: str, *, minimum: int) -> int:
    if (isinstance(value, bool) or not isinstance(value, int)
            or value < minimum or value > _MAX_COUNTER):
        raise JobCorruptError(f"job {name} is invalid")
    return value


def _validate_digest(value: str) -> str:
    if (not isinstance(value, str) or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)):
        raise JobCorruptError("job spec digest is invalid")
    return value


def _now(clock: Callable[[], float]) -> float:
    value = clock()
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(float(value)) or float(value) < 0):
        raise JobRuntimeError("job clock returned an invalid timestamp")
    return float(value)


def _spec_payload(spec: _StoredSpec) -> dict:
    return {
        "schema_version": JOB_SCHEMA_VERSION,
        "kind": "job_spec",
        "job_id": spec.job_id,
        "command": spec.command,
        "argv": list(spec.argv),
        "timeout_seconds": spec.timeout_seconds,
        "created_at": spec.created_at,
    }


def _state_payload(state: _StoredState) -> dict:
    return {
        "schema_version": JOB_SCHEMA_VERSION,
        "kind": "job_state",
        "job_id": state.job_id,
        "status": state.status,
        "attempt_number": state.attempt_number,
        "attempt_token": state.attempt_token,
        "revision": state.revision,
        "spec_sha256": state.spec_sha256,
        "created_at": state.created_at,
        "updated_at": state.updated_at,
    }


def _cancel_payload(marker: _CancelMarker) -> dict:
    return {
        "schema_version": JOB_SCHEMA_VERSION,
        "kind": "job_cancel_request",
        "job_id": marker.job_id,
        "attempt_number": marker.attempt_number,
        "attempt_token": marker.attempt_token,
        "requested_at": marker.requested_at,
    }


def _bindings_payload(bindings: _StoredBindings) -> dict:
    return {
        "schema_version": JOB_SCHEMA_VERSION,
        "kind": "job_pipeline_bindings",
        "job_id": bindings.job_id,
        "spec_sha256": bindings.spec_sha256,
        "revision": bindings.revision,
        "items": [
            {
                "item_index": item.item_index,
                "input_sha256": item.input_sha256,
                "run_name": item.run_name,
                "status": item.status,
            }
            for item in sorted(bindings.items, key=lambda item: item.item_index)
        ],
    }


def _canonical_digest(payload: dict) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def pipeline_input_sha256(path: str | os.PathLike[str]) -> str:
    """Return a content-free, platform-normalized identity for an input path."""
    try:
        raw_path = os.fspath(path)
    except TypeError as exc:
        raise JobValidationError("pipeline input path is invalid") from exc
    if not isinstance(raw_path, str) or not raw_path or "\x00" in raw_path:
        raise JobValidationError("pipeline input path is invalid")
    canonical = os.path.normcase(os.path.abspath(os.path.normpath(raw_path)))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _validate_item_index(value: int) -> int:
    if (isinstance(value, bool) or not isinstance(value, int)
            or value < 1 or value > _MAX_ARGUMENT_COUNT):
        raise JobValidationError("pipeline item index is invalid")
    return value


def _validate_run_name_syntax(run_name: str) -> str:
    if not isinstance(run_name, str) or not run_name:
        raise JobValidationError("pipeline run name is invalid")
    try:
        encoded_size = len(run_name.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise JobValidationError("pipeline run name is invalid") from exc
    if (encoded_size > 1024
            or any(character in run_name for character in ("/", "\\", "\x00"))
            or any(ord(character) < 32 for character in run_name)):
        raise JobValidationError("pipeline run name is invalid")
    return run_name


def _validate_run_name(run_name: str, input_path: str | os.PathLike[str]) -> str:
    run_name = _validate_run_name_syntax(run_name)
    stem = Path(os.fspath(input_path)).stem
    if run_name == stem:
        return run_name
    prefix = f"{stem}_"
    suffix = run_name[len(prefix):] if run_name.startswith(prefix) else ""
    if not suffix.isdecimal() or int(suffix) < 2:
        raise JobValidationError(
            "pipeline run name does not match its input")
    return run_name


def _validate_binding_status(status: str) -> str:
    if not isinstance(status, str) or status not in PIPELINE_BINDING_STATUSES:
        raise JobValidationError("pipeline binding status is invalid")
    return status


def _raise_json_constant(value: str):
    raise ValueError(f"non-finite JSON constant {value}")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _private_mode_ok(path: Path, *, directory: bool) -> bool:
    if os.name == "nt":
        return storage_policy.windows_path_is_private(
            path, directory=directory)
    expected = (storage_policy.PRIVATE_DIRECTORY_MODE if directory
                else storage_policy.PRIVATE_FILE_MODE)
    return stat.S_IMODE(os.lstat(path).st_mode) == expected


def _directory_identity(path: Path, *, label: str) -> tuple[int, int]:
    try:
        storage_policy.assert_no_link_components(path)
        result = os.lstat(path)
        private = _private_mode_ok(path, directory=True)
    except (OSError, storage_policy.StoragePolicyError) as exc:
        raise JobCorruptError(f"{label} is unavailable or unsafe") from exc
    if not stat.S_ISDIR(result.st_mode) or not private:
        raise JobCorruptError(f"{label} is not one private directory")
    return int(result.st_dev), int(result.st_ino)


def _read_private_json(path: Path, *, maximum: int, label: str) -> dict:
    """Read a stable, private, unlinked regular file or fail closed."""
    descriptor = -1
    try:
        storage_policy.assert_no_link_components(path)
        private = _private_mode_ok(path, directory=False)
        before = os.lstat(path)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_size <= 0 or before.st_size > maximum
                or not private):
            raise JobCorruptError(f"{label} is not one bounded private file")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        flags |= getattr(os, "O_BINARY", 0)
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        before_identity = (
            int(before.st_dev), int(before.st_ino), int(before.st_size),
            int(before.st_mtime_ns), int(before.st_ctime_ns),
            int(before.st_nlink),
        )
        # Windows reports subtly different ctime precision through a pathname
        # and an open CRT descriptor.  Bind the two views with stable file
        # identity fields, then compare each view against itself below.
        opened_binding = (
            int(opened.st_dev), int(opened.st_ino), int(opened.st_size),
            int(opened.st_nlink),
        )
        before_binding = (
            int(before.st_dev), int(before.st_ino), int(before.st_size),
            int(before.st_nlink),
        )
        if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                or opened_binding != before_binding):
            raise JobCorruptError(f"{label} changed while being opened")
        chunks = []
        remaining = int(opened.st_size)
        while remaining:
            chunk = os.read(descriptor, min(remaining, 64 * 1024))
            if not chunk:
                raise JobCorruptError(f"{label} ended before its declared size")
            chunks.append(chunk)
            remaining -= len(chunk)
        after = os.fstat(descriptor)
        after_identity = (
            int(after.st_dev), int(after.st_ino), int(after.st_size),
            int(after.st_mtime_ns), int(after.st_ctime_ns),
            int(after.st_nlink),
        )
        opened_identity = (
            int(opened.st_dev), int(opened.st_ino), int(opened.st_size),
            int(opened.st_mtime_ns), int(opened.st_ctime_ns),
            int(opened.st_nlink),
        )
        named_after = os.lstat(path)
        named_after_identity = (
            int(named_after.st_dev), int(named_after.st_ino),
            int(named_after.st_size), int(named_after.st_mtime_ns),
            int(named_after.st_ctime_ns), int(named_after.st_nlink),
        )
        if (after_identity != opened_identity
                or named_after_identity != before_identity):
            raise JobCorruptError(f"{label} changed while being read")
        payload = json.loads(
            b"".join(chunks).decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_raise_json_constant,
        )
        if not isinstance(payload, dict):
            raise JobCorruptError(f"{label} must contain one JSON object")
        return payload
    except JobCorruptError:
        raise
    except FileNotFoundError as exc:
        raise JobCorruptError(f"{label} is missing") from exc
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError,
            RecursionError,
            storage_policy.StoragePolicyError) as exc:
        raise JobCorruptError(f"{label} could not be parsed safely") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _require_exact_keys(payload: dict, expected: frozenset[str], label: str) -> None:
    if frozenset(payload) != expected:
        raise JobCorruptError(f"{label} has missing or unknown fields")


def _parse_spec(payload: dict, *, expected_job_id: str) -> _StoredSpec:
    _require_exact_keys(payload, frozenset({
        "schema_version", "kind", "job_id", "command", "argv",
        "timeout_seconds", "created_at",
    }), "job spec")
    if (type(payload["schema_version"]) is not int
            or payload["schema_version"] != JOB_SCHEMA_VERSION
            or payload["kind"] != "job_spec"):
        raise JobCorruptError("job spec schema is unsupported")
    if payload["job_id"] != expected_job_id:
        raise JobCorruptError("job spec identity does not match its directory")
    try:
        command = _validate_command(payload["command"])
        argv = _validate_argv(payload["argv"])
        timeout_seconds = _validate_timeout(payload["timeout_seconds"])
    except JobValidationError as exc:
        raise JobCorruptError("persisted job spec is invalid") from exc
    return _StoredSpec(
        job_id=expected_job_id,
        command=command,
        argv=argv,
        timeout_seconds=timeout_seconds,
        created_at=_validate_timestamp(payload["created_at"], "created_at"),
    )


def _parse_state(payload: dict, *, expected_job_id: str) -> _StoredState:
    _require_exact_keys(payload, frozenset({
        "schema_version", "kind", "job_id", "status", "attempt_number",
        "attempt_token", "revision", "spec_sha256", "created_at",
        "updated_at",
    }), "job state")
    if (type(payload["schema_version"]) is not int
            or payload["schema_version"] != JOB_SCHEMA_VERSION
            or payload["kind"] != "job_state"):
        raise JobCorruptError("job state schema is unsupported")
    if payload["job_id"] != expected_job_id:
        raise JobCorruptError("job state identity does not match its directory")
    status = payload["status"]
    if not isinstance(status, str) or status not in JOB_STATUSES:
        raise JobCorruptError("job status is invalid")
    try:
        attempt_token = _validate_attempt_token(payload["attempt_token"])
    except JobValidationError as exc:
        raise JobCorruptError("job attempt token is invalid") from exc
    created_at = _validate_timestamp(payload["created_at"], "created_at")
    updated_at = _validate_timestamp(payload["updated_at"], "updated_at")
    if updated_at < created_at:
        raise JobCorruptError("job updated_at predates created_at")
    return _StoredState(
        job_id=expected_job_id,
        status=status,
        attempt_number=_validate_counter(
            payload["attempt_number"], "attempt_number", minimum=1),
        attempt_token=attempt_token,
        revision=_validate_counter(payload["revision"], "revision", minimum=0),
        spec_sha256=_validate_digest(payload["spec_sha256"]),
        created_at=created_at,
        updated_at=updated_at,
    )


def _parse_cancel(payload: dict, *, expected_job_id: str) -> _CancelMarker:
    _require_exact_keys(payload, frozenset({
        "schema_version", "kind", "job_id", "attempt_number",
        "attempt_token", "requested_at",
    }), "job cancel marker")
    if (type(payload["schema_version"]) is not int
            or payload["schema_version"] != JOB_SCHEMA_VERSION
            or payload["kind"] != "job_cancel_request"):
        raise JobCorruptError("job cancel marker schema is unsupported")
    if payload["job_id"] != expected_job_id:
        raise JobCorruptError("job cancel identity does not match its directory")
    try:
        attempt_token = _validate_attempt_token(payload["attempt_token"])
    except JobValidationError as exc:
        raise JobCorruptError("job cancel attempt token is invalid") from exc
    return _CancelMarker(
        job_id=expected_job_id,
        attempt_number=_validate_counter(
            payload["attempt_number"], "attempt_number", minimum=1),
        attempt_token=attempt_token,
        requested_at=_validate_timestamp(payload["requested_at"], "requested_at"),
    )


def _parse_bindings(payload: dict, *, expected_job_id: str,
                    expected_spec_sha256: str) -> _StoredBindings:
    _require_exact_keys(payload, frozenset({
        "schema_version", "kind", "job_id", "spec_sha256", "revision",
        "items",
    }), "job pipeline bindings")
    if (type(payload["schema_version"]) is not int
            or payload["schema_version"] != JOB_SCHEMA_VERSION
            or payload["kind"] != "job_pipeline_bindings"):
        raise JobCorruptError("job pipeline bindings schema is unsupported")
    if payload["job_id"] != expected_job_id:
        raise JobCorruptError(
            "job pipeline bindings identity does not match its directory")
    spec_sha256 = _validate_digest(payload["spec_sha256"])
    if not hmac.compare_digest(spec_sha256, expected_spec_sha256):
        raise JobCorruptError("job pipeline bindings do not match the spec")
    raw_items = payload["items"]
    if (not isinstance(raw_items, list)
            or len(raw_items) > _MAX_ARGUMENT_COUNT):
        raise JobCorruptError("job pipeline bindings items are invalid")
    items = []
    observed_indexes = set()
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            raise JobCorruptError("job pipeline binding is invalid")
        _require_exact_keys(raw_item, frozenset({
            "item_index", "input_sha256", "run_name", "status",
        }), "job pipeline binding")
        try:
            item_index = _validate_item_index(raw_item["item_index"])
            input_sha256 = _validate_digest(raw_item["input_sha256"])
            run_name = _validate_run_name_syntax(raw_item["run_name"])
            status = _validate_binding_status(raw_item["status"])
        except JobValidationError as exc:
            raise JobCorruptError("job pipeline binding is invalid") from exc
        if item_index in observed_indexes:
            raise JobCorruptError("job pipeline binding index is duplicated")
        observed_indexes.add(item_index)
        items.append(PipelineRunBinding(
            item_index=item_index,
            input_sha256=input_sha256,
            run_name=run_name,
            status=status,
        ))
    return _StoredBindings(
        job_id=expected_job_id,
        spec_sha256=spec_sha256,
        revision=_validate_counter(
            payload["revision"], "bindings revision", minimum=1),
        items=tuple(sorted(items, key=lambda item: item.item_index)),
    )


def _open_private_lock(path: Path) -> int:
    descriptor = -1
    try:
        storage_policy.assert_no_link_components(path.parent)
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        flags |= getattr(os, "O_BINARY", 0)
        descriptor = os.open(path, flags, storage_policy.PRIVATE_FILE_MODE)
        storage_policy.enforce_private_path(path, directory=False)
        opened = os.fstat(descriptor)
        named = os.lstat(path)
        if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                or not stat.S_ISREG(named.st_mode) or named.st_nlink != 1
                or (int(opened.st_dev), int(opened.st_ino)) !=
                (int(named.st_dev), int(named.st_ino))):
            raise JobCorruptError("job lease sentinel is not one regular file")
        if opened.st_size < 1:
            os.lseek(descriptor, 0, os.SEEK_SET)
            if os.write(descriptor, b"\0") != 1:
                raise OSError("job lease sentinel initialization was incomplete")
            os.fsync(descriptor)
        return descriptor
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        raise


def _try_lock(descriptor: int) -> bool:
    if os.name == "nt":
        import msvcrt

        os.lseek(descriptor, 0, os.SEEK_SET)
        try:
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            return True
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK,
                             errno.EPERM}:
                return False
            raise
    import fcntl

    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError as exc:
        if exc.errno in {errno.EACCES, errno.EAGAIN}:
            return False
        raise


def _unlock(descriptor: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(descriptor, fcntl.LOCK_UN)


def _validate_lease_timeout(timeout: float) -> float:
    if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
            or not math.isfinite(float(timeout)) or float(timeout) < 0):
        raise JobValidationError("lease timeout must be a finite non-negative number")
    return float(timeout)


class JobLease:
    """One owner-only cross-process advisory lease.

    Lease files contain no source paths, arguments, credentials, or process
    metadata.  Cancellation markers intentionally do not require this lease so
    a future manager can hold it for the life of an attempt.
    """

    def __init__(self, path: Path, *, store_key: tuple[str, int, int],
                 directory_identity: tuple[int, int],
                 job_id: str | None, timeout: float = 0.0):
        self._path = Path(path)
        self._store_key = store_key
        self._directory_identity = directory_identity
        self.job_id = job_id
        self.timeout = _validate_lease_timeout(timeout)
        self._descriptor = -1

    @property
    def active(self) -> bool:
        return self._descriptor >= 0

    def acquire(self) -> "JobLease":
        if self.active:
            raise JobStateError("job lease is already active")
        if _directory_identity(
                self._path.parent, label="job lease directory") != (
                self._directory_identity):
            raise JobCorruptError("job lease directory identity changed")
        descriptor = _open_private_lock(self._path)
        deadline = time.monotonic() + self.timeout
        try:
            while True:
                if _try_lock(descriptor):
                    if _directory_identity(
                            self._path.parent,
                            label="job lease directory") != (
                            self._directory_identity):
                        raise JobCorruptError(
                            "job lease directory identity changed")
                    self._descriptor = descriptor
                    return self
                if time.monotonic() >= deadline:
                    raise JobBusyError("job lease is held by another manager")
                time.sleep(min(
                    _POLL_INTERVAL_SECONDS,
                    max(0.0, deadline - time.monotonic())))
        except BaseException:
            os.close(descriptor)
            raise

    def release(self) -> None:
        if not self.active:
            return
        descriptor = self._descriptor
        self._descriptor = -1
        try:
            _unlock(descriptor)
        finally:
            os.close(descriptor)

    def __enter__(self) -> "JobLease":
        return self.acquire()

    def __exit__(self, exc_type, exc, traceback) -> bool:
        self.release()
        return False


class JobStore:
    """Private, schema-checked durable job repository."""

    def __init__(self, root: Path = DEFAULT_JOB_ROOT, *,
                 clock: Callable[[], float] = time.time):
        self.root = storage_policy.ensure_private_directory(Path(root))
        self._clock = clock
        device, inode = _directory_identity(self.root, label="job root")
        self._root_identity = (device, inode)
        self._store_key = (
            os.path.normcase(str(self.root)), device, inode)

    def _validate_root(self) -> None:
        identity = _directory_identity(self.root, label="job root")
        if identity != self._root_identity:
            raise JobCorruptError("job root identity changed")

    def _job_dir(self, job_id: str, *, must_exist: bool = True) -> Path:
        job_id = _validate_job_id(job_id)
        self._validate_root()
        directory = self.root / job_id
        try:
            identity = _directory_identity(directory, label="job directory")
        except JobCorruptError as exc:
            if must_exist:
                try:
                    os.lstat(directory)
                except FileNotFoundError:
                    raise JobNotFoundError("job does not exist") from exc
            raise
        if not must_exist:
            raise JobAlreadyExistsError("job ID already exists")
        # Evaluating identity ensures the directory cannot be an accepted link;
        # no long-lived identity is needed because each atomic file read pins
        # and revalidates its own inode.
        del identity
        return directory

    def _store_lease(self, *, timeout: float = 5.0) -> JobLease:
        self._validate_root()
        return JobLease(
            self.root / _STORE_LOCK_NAME, store_key=self._store_key,
            directory_identity=self._root_identity,
            job_id=None, timeout=timeout)

    def lease(self, job_id: str, *, timeout: float = 0.0) -> JobLease:
        directory = self._job_dir(job_id)
        directory_identity = _directory_identity(
            directory, label="job directory")
        return JobLease(
            directory / _JOB_LOCK_NAME, store_key=self._store_key,
            directory_identity=directory_identity,
            job_id=job_id, timeout=timeout)

    def _bindings_lease(
            self, job_id: str, *, timeout: float = 5.0) -> JobLease:
        directory = self._job_dir(job_id)
        directory_identity = _directory_identity(
            directory, label="job directory")
        return JobLease(
            directory / _BINDINGS_LOCK_NAME,
            store_key=self._store_key,
            directory_identity=directory_identity,
            job_id=job_id, timeout=timeout)

    def _validate_supplied_lease(self, lease: JobLease, job_id: str) -> None:
        if (not isinstance(lease, JobLease) or not lease.active
                or lease.job_id != job_id
                or lease._store_key != self._store_key):
            raise JobStateError("active lease does not own this job")
        directory = self._job_dir(job_id)
        if _directory_identity(directory, label="job directory") != (
                lease._directory_identity):
            raise JobCorruptError("leased job directory identity changed")

    @contextmanager
    def _mutation_lease(self, job_id: str, *, timeout: float,
                        lease: JobLease | None) -> Iterator[None]:
        if lease is not None:
            self._validate_supplied_lease(lease, job_id)
            yield
            return
        with self.lease(job_id, timeout=timeout) as acquired:
            self._validate_supplied_lease(acquired, job_id)
            yield

    def _load_record(self, job_id: str) -> tuple[_StoredSpec, _StoredState]:
        directory = self._job_dir(job_id)
        spec = _parse_spec(_read_private_json(
            directory / _SPEC_NAME, maximum=_MAX_SPEC_BYTES,
            label="job spec"), expected_job_id=job_id)
        state = _parse_state(_read_private_json(
            directory / _STATE_NAME, maximum=_MAX_STATE_BYTES,
            label="job state"), expected_job_id=job_id)
        digest = _canonical_digest(_spec_payload(spec))
        if not hmac.compare_digest(state.spec_sha256, digest):
            raise JobCorruptError("job state does not bind the immutable spec")
        if state.created_at != spec.created_at:
            raise JobCorruptError("job state and spec creation times differ")
        return spec, state

    def _load_bindings(
            self, job_id: str, state: _StoredState, *,
            missing_ok: bool) -> _StoredBindings | None:
        path = self._job_dir(job_id) / _BINDINGS_NAME
        try:
            os.lstat(path)
        except FileNotFoundError:
            if missing_ok:
                return None
            raise JobCorruptError("job pipeline bindings are missing")
        return _parse_bindings(
            _read_private_json(
                path, maximum=_MAX_BINDINGS_BYTES,
                label="job pipeline bindings"),
            expected_job_id=job_id,
            expected_spec_sha256=state.spec_sha256,
        )

    @staticmethod
    def _summary(spec: _StoredSpec, state: _StoredState) -> JobSummary:
        return JobSummary(
            job_id=state.job_id,
            command=spec.command,
            status=state.status,
            attempt_number=state.attempt_number,
            revision=state.revision,
            created_at=state.created_at,
            updated_at=state.updated_at,
        )

    def submit_job(self, command: str, argv: Sequence[str] = (), *,
                   timeout_seconds: float | None = None,
                   job_id: str | None = None) -> JobSummary:
        """Persist one queued job without launching it."""
        command = _validate_command(command)
        argv = _validate_argv(argv)
        timeout_seconds = _validate_timeout(timeout_seconds)
        job_id = uuid4().hex if job_id is None else _validate_job_id(job_id)
        created_at = _now(self._clock)
        spec = _StoredSpec(
            job_id=job_id, command=command, argv=argv,
            timeout_seconds=timeout_seconds, created_at=created_at)
        spec_payload = _spec_payload(spec)
        state = _StoredState(
            job_id=job_id,
            status="queued",
            attempt_number=1,
            attempt_token=secrets.token_hex(16),
            revision=0,
            spec_sha256=_canonical_digest(spec_payload),
            created_at=created_at,
            updated_at=created_at,
        )
        with self._store_lease():
            directory = self.root / job_id
            try:
                os.lstat(directory)
            except FileNotFoundError:
                pass
            else:
                raise JobAlreadyExistsError("job ID already exists")
            try:
                directory.mkdir(mode=storage_policy.PRIVATE_DIRECTORY_MODE)
                storage_policy.enforce_private_path(directory, directory=True)
                storage_policy.atomic_write_private_json(
                    directory / _SPEC_NAME, spec_payload)
                storage_policy.atomic_write_private_json(
                    directory / _STATE_NAME, _state_payload(state))
            except FileExistsError as exc:
                raise JobAlreadyExistsError("job ID already exists") from exc
        return self._summary(spec, state)

    def get_job(self, job_id: str) -> JobSummary:
        spec, state = self._load_record(_validate_job_id(job_id))
        return self._summary(spec, state)

    def list_jobs(self) -> list[JobSummary]:
        """Return all jobs, failing rather than silently skipping corruption."""
        with self._store_lease():
            identifiers = []
            try:
                entries = list(os.scandir(self.root))
            except OSError as exc:
                raise JobCorruptError("job root could not be enumerated") from exc
            for entry in entries:
                if entry.name == _STORE_LOCK_NAME:
                    continue
                if (not _JOB_ID.fullmatch(entry.name)
                        or not entry.is_dir(follow_symlinks=False)):
                    raise JobCorruptError("job root contains an unexpected entry")
                identifiers.append(entry.name)
            summaries = [self.get_job(job_id) for job_id in identifiers]
        return sorted(
            summaries, key=lambda item: (item.created_at, item.job_id),
            reverse=True)

    def load_execution(self, job_id: str, *,
                       lease: JobLease | None = None) -> JobExecution:
        """Load the private execution view for a future manager."""
        job_id = _validate_job_id(job_id)
        if lease is not None:
            self._validate_supplied_lease(lease, job_id)
        spec, state = self._load_record(job_id)
        return JobExecution(
            job_id=job_id,
            command=spec.command,
            argv=spec.argv,
            timeout_seconds=spec.timeout_seconds,
            status=state.status,
            attempt_number=state.attempt_number,
            attempt_token=state.attempt_token,
            revision=state.revision,
        )

    @staticmethod
    def _authorize_pipeline_binding(
            spec: _StoredSpec, state: _StoredState,
            attempt_token: str) -> None:
        if spec.command not in {"full", "batch"}:
            raise JobStateError(
                "exact run bindings apply only to full and batch jobs")
        if not hmac.compare_digest(state.attempt_token, attempt_token):
            raise JobStateError("pipeline binding targets a stale attempt")
        if state.status in TERMINAL_JOB_STATUSES:
            raise JobStateError("terminal attempts cannot change run bindings")

    def get_pipeline_binding(
            self, job_id: str, *, item_index: int,
            input_path: str | os.PathLike[str]) -> PipelineRunBinding | None:
        """Load one exact run binding without exposing the input pathname."""
        job_id = _validate_job_id(job_id)
        item_index = _validate_item_index(item_index)
        input_sha256 = pipeline_input_sha256(input_path)
        with self._bindings_lease(job_id):
            spec, state = self._load_record(job_id)
            if spec.command not in {"full", "batch"}:
                raise JobStateError(
                    "exact run bindings apply only to full and batch jobs")
            bindings = self._load_bindings(job_id, state, missing_ok=True)
            if bindings is None:
                return None
            for item in bindings.items:
                if item.item_index != item_index:
                    continue
                if not hmac.compare_digest(item.input_sha256, input_sha256):
                    raise JobStateError(
                        "pipeline item index is bound to another input")
                return item
        return None

    def bind_pipeline_run(
            self, job_id: str, *, attempt_token: str, item_index: int,
            input_path: str | os.PathLike[str], run_name: str) -> PipelineRunBinding:
        """Bind one input index to exactly one allocated pipeline run."""
        job_id = _validate_job_id(job_id)
        attempt_token = _validate_attempt_token(attempt_token)
        item_index = _validate_item_index(item_index)
        input_sha256 = pipeline_input_sha256(input_path)
        run_name = _validate_run_name(run_name, input_path)
        with self._bindings_lease(job_id):
            spec, state = self._load_record(job_id)
            self._authorize_pipeline_binding(spec, state, attempt_token)
            stored = self._load_bindings(job_id, state, missing_ok=True)
            items = list(stored.items if stored is not None else ())
            selected = None
            for index, item in enumerate(items):
                if item.item_index != item_index:
                    continue
                if (not hmac.compare_digest(item.input_sha256, input_sha256)
                        or item.run_name != run_name):
                    raise JobStateError(
                        "pipeline item already has another exact run binding")
                selected = item
                if item.status != "complete":
                    selected = PipelineRunBinding(
                        item_index=item.item_index,
                        input_sha256=item.input_sha256,
                        run_name=item.run_name,
                        status="allocated",
                    )
                    items[index] = selected
                break
            if selected is None:
                selected = PipelineRunBinding(
                    item_index=item_index,
                    input_sha256=input_sha256,
                    run_name=run_name,
                    status="allocated",
                )
                items.append(selected)
            if stored is not None and tuple(items) == stored.items:
                return selected
            revision = 1 if stored is None else stored.revision + 1
            if revision > _MAX_COUNTER:
                raise JobStateError("pipeline binding revision is exhausted")
            updated = _StoredBindings(
                job_id=job_id,
                spec_sha256=state.spec_sha256,
                revision=revision,
                items=tuple(sorted(
                    items, key=lambda item: item.item_index)),
            )
            storage_policy.atomic_write_private_json(
                self.root / job_id / _BINDINGS_NAME,
                _bindings_payload(updated))
            return selected

    def mark_pipeline_binding(
            self, job_id: str, *, attempt_token: str, item_index: int,
            input_path: str | os.PathLike[str], status: str) -> PipelineRunBinding:
        """Publish the current outcome for one exact bound pipeline run."""
        job_id = _validate_job_id(job_id)
        attempt_token = _validate_attempt_token(attempt_token)
        item_index = _validate_item_index(item_index)
        input_sha256 = pipeline_input_sha256(input_path)
        status = _validate_binding_status(status)
        with self._bindings_lease(job_id):
            spec, state = self._load_record(job_id)
            self._authorize_pipeline_binding(spec, state, attempt_token)
            stored = self._load_bindings(job_id, state, missing_ok=False)
            assert stored is not None
            items = list(stored.items)
            selected = None
            for index, item in enumerate(items):
                if item.item_index != item_index:
                    continue
                if not hmac.compare_digest(item.input_sha256, input_sha256):
                    raise JobStateError(
                        "pipeline item index is bound to another input")
                if item.status == "complete" and status != "complete":
                    raise JobStateError(
                        "completed pipeline bindings are immutable")
                selected = PipelineRunBinding(
                    item_index=item.item_index,
                    input_sha256=item.input_sha256,
                    run_name=item.run_name,
                    status=status,
                )
                items[index] = selected
                break
            if selected is None:
                raise JobStateError("pipeline item has no exact run binding")
            if tuple(items) == stored.items:
                return selected
            if stored.revision >= _MAX_COUNTER:
                raise JobStateError("pipeline binding revision is exhausted")
            updated = _StoredBindings(
                job_id=job_id,
                spec_sha256=state.spec_sha256,
                revision=stored.revision + 1,
                items=tuple(items),
            )
            storage_policy.atomic_write_private_json(
                self.root / job_id / _BINDINGS_NAME,
                _bindings_payload(updated))
            return selected

    def _read_cancel_marker(self, job_id: str, *,
                            missing_ok: bool) -> _CancelMarker | None:
        directory = self._job_dir(job_id)
        path = directory / _CANCEL_NAME
        try:
            os.lstat(path)
        except FileNotFoundError:
            if missing_ok:
                return None
            raise JobCorruptError("job cancel marker is missing")
        return _parse_cancel(_read_private_json(
            path, maximum=_MAX_CANCEL_BYTES, label="job cancel marker"),
            expected_job_id=job_id)

    @staticmethod
    def _marker_matches_state(marker: _CancelMarker,
                              state: _StoredState) -> bool:
        if marker.requested_at < state.created_at:
            raise JobCorruptError("job cancel marker predates the job")
        if marker.attempt_number > state.attempt_number:
            raise JobCorruptError("job cancel marker targets a future attempt")
        if marker.attempt_number == state.attempt_number:
            if marker.attempt_token != state.attempt_token:
                raise JobCorruptError(
                    "job cancel marker conflicts with the current attempt")
            return True
        return False

    def request_cancel(self, job_id: str) -> JobSummary:
        """Atomically request cancellation for the currently observed attempt.

        The manager remains the sole state writer.  A concurrent resume can
        therefore only turn this into a harmless stale marker.
        """
        job_id = _validate_job_id(job_id)
        spec, state = self._load_record(job_id)
        if state.status in TERMINAL_JOB_STATUSES:
            raise JobStateError("terminal jobs cannot accept cancellation requests")
        existing = self._read_cancel_marker(job_id, missing_ok=True)
        if existing is not None and self._marker_matches_state(existing, state):
            return self._summary(spec, state)
        marker = _CancelMarker(
            job_id=job_id,
            attempt_number=state.attempt_number,
            attempt_token=state.attempt_token,
            requested_at=max(state.updated_at, _now(self._clock)),
        )
        storage_policy.atomic_write_private_json(
            self.root / job_id / _CANCEL_NAME, _cancel_payload(marker))
        return self._summary(spec, state)

    def is_cancel_requested(self, job_id: str, attempt_token: str) -> bool:
        """Return whether a valid marker targets the active exact attempt."""
        job_id = _validate_job_id(job_id)
        attempt_token = _validate_attempt_token(attempt_token)
        _, state = self._load_record(job_id)
        if (state.attempt_token != attempt_token
                or state.status in TERMINAL_JOB_STATUSES):
            return False
        marker = self._read_cancel_marker(job_id, missing_ok=True)
        return (marker is not None
                and self._marker_matches_state(marker, state))

    def transition_job(self, job_id: str, target_status: str, *,
                       attempt_token: str,
                       expected_revision: int | None = None,
                       lease_timeout: float = 5.0,
                       lease: JobLease | None = None) -> JobSummary:
        """Apply one legal, attempt-bound atomic state transition."""
        job_id = _validate_job_id(job_id)
        attempt_token = _validate_attempt_token(attempt_token)
        if target_status not in JOB_STATUSES:
            raise JobValidationError("target job status is invalid")
        if expected_revision is not None:
            if (isinstance(expected_revision, bool)
                    or not isinstance(expected_revision, int)
                    or expected_revision < 0
                    or expected_revision > _MAX_COUNTER):
                raise JobValidationError("expected_revision is invalid")
        with self._mutation_lease(
                job_id, timeout=lease_timeout, lease=lease):
            spec, state = self._load_record(job_id)
            if not hmac.compare_digest(state.attempt_token, attempt_token):
                raise JobStateError("state transition targets a stale attempt")
            if state.status == target_status:
                return self._summary(spec, state)
            if (expected_revision is not None
                    and state.revision != expected_revision):
                raise JobStateError("state transition revision is stale")
            if target_status not in LEGAL_JOB_TRANSITIONS[state.status]:
                raise JobStateError(
                    f"illegal job transition: {state.status} -> {target_status}")
            if target_status == "cancel_requested":
                marker = self._read_cancel_marker(job_id, missing_ok=True)
                if (marker is None
                        or not self._marker_matches_state(marker, state)):
                    raise JobStateError(
                        "cancel_requested requires a matching cancel marker")
            if state.revision >= _MAX_COUNTER:
                raise JobStateError("job state revision is exhausted")
            updated = _StoredState(
                job_id=job_id,
                status=target_status,
                attempt_number=state.attempt_number,
                attempt_token=state.attempt_token,
                revision=state.revision + 1,
                spec_sha256=state.spec_sha256,
                created_at=state.created_at,
                updated_at=max(state.updated_at, _now(self._clock)),
            )
            storage_policy.atomic_write_private_json(
                self.root / job_id / _STATE_NAME, _state_payload(updated))
        return self._summary(spec, updated)

    def prepare_resume(self, job_id: str, *,
                       expected_revision: int | None = None,
                       lease_timeout: float = 0.0) -> JobSummary:
        """Create exactly one new queued attempt for a recoverable terminal job."""
        job_id = _validate_job_id(job_id)
        if expected_revision is not None:
            if (isinstance(expected_revision, bool)
                    or not isinstance(expected_revision, int)
                    or expected_revision < 0
                    or expected_revision > _MAX_COUNTER):
                raise JobValidationError("expected_revision is invalid")
        with self.lease(job_id, timeout=lease_timeout):
            spec, state = self._load_record(job_id)
            if (expected_revision is not None
                    and state.revision != expected_revision):
                raise JobStateError("resume revision is stale")
            if state.status not in RESUMABLE_JOB_STATUSES:
                raise JobStateError(
                    f"job status {state.status!r} is not resumable")
            if (state.attempt_number >= _MAX_COUNTER
                    or state.revision >= _MAX_COUNTER):
                raise JobStateError("job attempt counters are exhausted")
            updated = _StoredState(
                job_id=job_id,
                status="queued",
                attempt_number=state.attempt_number + 1,
                attempt_token=secrets.token_hex(16),
                revision=state.revision + 1,
                spec_sha256=state.spec_sha256,
                created_at=state.created_at,
                updated_at=max(state.updated_at, _now(self._clock)),
            )
            storage_policy.atomic_write_private_json(
                self.root / job_id / _STATE_NAME, _state_payload(updated))
        return self._summary(spec, updated)


def load_worker_context(
        command: str, *, environ: Mapping[str, str] | None = None
        ) -> JobWorkerContext | None:
    """Load and authenticate an optional manager-provided worker context."""
    command = _validate_command(command)
    environment = os.environ if environ is None else environ
    values = (
        environment.get(JOB_ROOT_ENV),
        environment.get(JOB_ID_ENV),
        environment.get(JOB_ATTEMPT_TOKEN_ENV),
    )
    if all(value is None for value in values):
        return None
    if any(not isinstance(value, str) or not value for value in values):
        raise JobValidationError(
            "background worker environment is incomplete")
    root, job_id, attempt_token = values
    assert root is not None and job_id is not None and attempt_token is not None
    store = JobStore(Path(root))
    execution = store.load_execution(job_id)
    if execution.command != command:
        raise JobStateError("background worker command does not match its spec")
    if not hmac.compare_digest(execution.attempt_token, attempt_token):
        raise JobStateError("background worker targets a stale attempt")
    if execution.status in TERMINAL_JOB_STATUSES:
        raise JobStateError("terminal background attempts cannot start workers")
    return JobWorkerContext(store=store, execution=execution)


__all__ = [
    "ALLOWED_JOB_COMMANDS",
    "DEFAULT_JOB_ROOT",
    "JOB_ATTEMPT_TOKEN_ENV",
    "JOB_ID_ENV",
    "JOB_ROOT_ENV",
    "JOB_SCHEMA_VERSION",
    "JOB_STATUSES",
    "LEGAL_JOB_TRANSITIONS",
    "RESUMABLE_JOB_STATUSES",
    "RESERVED_JOB_OPTIONS",
    "SECRET_OPTIONS",
    "TERMINAL_JOB_STATUSES",
    "JobAlreadyExistsError",
    "JobBusyError",
    "JobCorruptError",
    "JobExecution",
    "JobLease",
    "JobNotFoundError",
    "JobRuntimeError",
    "JobStateError",
    "JobStore",
    "JobSummary",
    "JobValidationError",
    "JobWorkerContext",
    "PIPELINE_BINDING_STATUSES",
    "PipelineRunBinding",
    "load_worker_context",
    "pipeline_input_sha256",
]
