"""Authenticated-local-service application runtime and isolated search worker.

This module owns no HTTP concepts.  It binds public corpus IDs to immutable
server-side Qdrant configuration, executes retrieval behind the existing hard
process boundary, and adapts durable jobs without exposing their private specs.
"""

from __future__ import annotations

import json
import hashlib
import os
import stat
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import job_coordination
import job_coordination_contracts
import job_runtime
import release_security
import retention
import service_contracts
import service_runtime_binding
import storage_policy


SERVICE_CONFIG_SCHEMA_VERSION = 1
MAX_SERVICE_CONFIG_BYTES = 256 * 1024
MAX_SEARCH_RESULT_BYTES = 2 * 1024 * 1024
MAX_CORPORA = 64
DEFAULT_JOB_PAGE_LIMIT = 50
MAX_JOB_PAGE_LIMIT = 100
DEFAULT_SERVICE_JOB_ROOT_NAME = ".rag-service-jobs"
_JOB_ROOT_OWNER_MARKER_NAME = ".rag-service-owner.json"
_STATE_OWNER_MARKER_NAME = "job-root-owner.json"
_OWNER_MARKER_SCHEMA_VERSION = 1
_OWNER_MARKER_KIND = "rag_service_job_root_owner"
_MAX_OWNER_MARKER_BYTES = 4096
_SEARCH_WORKER_ACTION = "_search_worker"
_INTERNAL_SCHEMA_VERSION = 2
_ACTIVE_SERVICE_GUARD = threading.Lock()
_ACTIVE_SERVICE_KEYS: set[tuple[int, str]] = set()
_default_service_runtime_binding = (
    service_runtime_binding.default_service_runtime_binding)
_default_service_job_coordination_binding = (
    job_coordination.default_service_job_coordination_binding)
_LAUNCHER_UNSET = object()


class ServiceRuntimeError(RuntimeError):
    """A stable runtime failure with no raw exception message."""

    def __init__(self, code: str, *, job_id: str | None = None,
                 fatal: bool = False):
        if code not in service_contracts.PROBLEM_DEFINITIONS:
            code = "internal_error"
        self.code = code
        self.job_id = job_id
        self.fatal = bool(fatal)
        super().__init__(service_contracts.PROBLEM_DEFINITIONS[code][1])


@dataclass(frozen=True, slots=True)
class ServiceJobResult:
    job: dict[str, Any]
    idempotent_replay: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": service_contracts.SERVICE_SCHEMA_VERSION,
            "job": self.job,
            "idempotent_replay": self.idempotent_replay,
        }


def _exact_fields(
        payload: object, *, allowed: frozenset[str],
        required: frozenset[str] = frozenset()) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise service_contracts.ServiceContractError()
    fields = frozenset(payload)
    if not required.issubset(fields) or not fields.issubset(allowed):
        raise service_contracts.ServiceContractError()
    return payload


def _bounded_path(value: object, *, base: Path) -> Path:
    if (not isinstance(value, str) or not value or "\x00" in value
            or len(value.encode("utf-8")) > 32 * 1024):
        raise service_contracts.ServiceContractError()
    path = Path(value)
    if not path.is_absolute():
        path = base / path
    return Path(os.path.abspath(path))


def _read_bounded_regular_file(path: Path, *, max_bytes: int) -> bytes:
    """Read one exact regular-file generation without following its leaf."""
    path = Path(path)
    try:
        storage_policy.assert_no_link_components(path)
        parent_before = os.stat(path.parent, follow_symlinks=False)
        parent_identity = (
            int(parent_before.st_dev), int(parent_before.st_ino))
        before = os.lstat(path)
    except (OSError, storage_policy.StoragePolicyError) as exc:
        raise ServiceRuntimeError("service_unavailable") from exc
    if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
            or before.st_size > max_bytes):
        raise ServiceRuntimeError("service_unavailable")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ServiceRuntimeError("service_unavailable") from exc
    try:
        opened = os.fstat(descriptor)
        identity = (int(opened.st_dev), int(opened.st_ino))
        if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                or opened.st_size > max_bytes
                or identity != (int(before.st_dev), int(before.st_ino))):
            raise ServiceRuntimeError("service_unavailable")
        chunks = []
        remaining = max_bytes + 1
        while remaining:
            chunk = os.read(descriptor, min(remaining, 64 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        after = os.fstat(descriptor)
        named = os.lstat(path)
        parent_after = os.stat(path.parent, follow_symlinks=False)
        if ((int(after.st_dev), int(after.st_ino)) != identity
                or (int(named.st_dev), int(named.st_ino)) != identity
                or (int(parent_after.st_dev), int(parent_after.st_ino)) !=
                parent_identity
                or after.st_nlink != 1 or named.st_nlink != 1):
            raise ServiceRuntimeError("service_unavailable")
    except OSError as exc:
        raise ServiceRuntimeError("service_unavailable") from exc
    finally:
        os.close(descriptor)
    raw = b"".join(chunks)
    if len(raw) > max_bytes:
        raise ServiceRuntimeError("service_unavailable")
    return raw


def _read_private_json(path: Path, *, max_bytes: int) -> dict[str, Any]:
    raw = _read_bounded_regular_file(path, max_bytes=max_bytes)
    try:
        return service_contracts.decode_json_object(raw, max_bytes=max_bytes)
    except service_contracts.ServiceContractError as exc:
        raise ServiceRuntimeError("service_unavailable") from exc


def _path_identity(path: Path) -> tuple[int, int]:
    result = os.lstat(path)
    return int(result.st_dev), int(result.st_ino)


def _path_digest(path: Path) -> str:
    normalized = os.path.normcase(str(Path(path).resolve()))
    return hashlib.sha256(os.fsencode(normalized)).hexdigest()


def _unlink_exact_regular_file(
        path: Path, expected_identity: tuple[int, int]) -> None:
    """Unlink only the exact single-link regular-file generation observed."""
    result = os.lstat(path)
    if (not stat.S_ISREG(result.st_mode) or result.st_nlink != 1
            or (int(result.st_dev), int(result.st_ino)) != expected_identity):
        raise ServiceRuntimeError("service_unavailable", fatal=True)
    os.unlink(path)
    if os.path.lexists(path):
        raise ServiceRuntimeError("service_unavailable", fatal=True)


def _remove_search_directory(
        path: Path, expected_identity: tuple[int, int]) -> None:
    """Remove one exact private search directory without following entries."""
    path = Path(path)
    storage_policy.assert_no_link_components(path)
    result = os.lstat(path)
    if (not stat.S_ISDIR(result.st_mode)
            or (int(result.st_dev), int(result.st_ino)) != expected_identity):
        raise ServiceRuntimeError("service_unavailable", fatal=True)
    entries = list(os.scandir(path))
    if len(entries) > 16:
        raise ServiceRuntimeError("service_unavailable", fatal=True)
    for entry in entries:
        entry_path = path / entry.name
        entry_result = os.lstat(entry_path)
        _unlink_exact_regular_file(
            entry_path,
            (int(entry_result.st_dev), int(entry_result.st_ino)),
        )
    current = os.lstat(path)
    if (not stat.S_ISDIR(current.st_mode)
            or (int(current.st_dev), int(current.st_ino)) !=
            expected_identity):
        raise ServiceRuntimeError("service_unavailable", fatal=True)
    os.rmdir(path)
    if os.path.lexists(path):
        raise ServiceRuntimeError("service_unavailable", fatal=True)


def _cleanup_stale_search_directories(root: Path) -> None:
    """Fail closed while removing crash-left search request directories."""
    root = storage_policy.ensure_private_directory(root)
    entries = list(os.scandir(root))
    if len(entries) > 1024:
        raise ServiceRuntimeError("service_unavailable", fatal=True)
    for entry in entries:
        result = os.lstat(root / entry.name)
        if (not entry.name.startswith("request-")
                or not stat.S_ISDIR(result.st_mode)):
            raise ServiceRuntimeError("service_unavailable", fatal=True)
        _remove_search_directory(
            root / entry.name,
            (int(result.st_dev), int(result.st_ino)),
        )


def load_corpus_registry(path: Path) -> dict[str, service_contracts.CorpusConfig]:
    """Load one strict, credential-free static corpus registry."""
    path = Path(os.path.abspath(Path(path)))
    storage_policy.enforce_private_path(path, directory=False)
    payload = _read_private_json(path, max_bytes=MAX_SERVICE_CONFIG_BYTES)
    _exact_fields(
        payload,
        allowed=frozenset({"schema_version", "corpora"}),
        required=frozenset({"schema_version", "corpora"}))
    if payload["schema_version"] != SERVICE_CONFIG_SCHEMA_VERSION:
        raise service_contracts.ServiceContractError()
    records = payload["corpora"]
    if (not isinstance(records, list) or not records
            or len(records) > MAX_CORPORA):
        raise service_contracts.ServiceContractError()
    registry: dict[str, service_contracts.CorpusConfig] = {}
    allowed = frozenset({
        "corpus_id", "backend", "db_path", "chunks_path",
        "collection_name", "embedding_model", "search_timeout_seconds",
        "db_lock_timeout_seconds", "reindex_timeout_seconds",
    })
    required = frozenset({
        "corpus_id", "backend", "db_path", "chunks_path",
        "collection_name", "embedding_model",
    })
    for raw_record in records:
        record = _exact_fields(
            raw_record, allowed=allowed, required=required)
        corpus_id = service_contracts.validate_corpus_id(record["corpus_id"])
        if corpus_id in registry:
            raise service_contracts.ServiceContractError()
        config = service_contracts.CorpusConfig(
            corpus_id=corpus_id,
            backend=record["backend"],
            db_path=_bounded_path(record["db_path"], base=path.parent),
            chunks_path=_bounded_path(
                record["chunks_path"], base=path.parent),
            collection_name=record["collection_name"],
            embedding_model=record["embedding_model"],
            search_timeout_seconds=record.get(
                "search_timeout_seconds", 300.0),
            db_lock_timeout_seconds=record.get(
                "db_lock_timeout_seconds", 30.0),
            reindex_timeout_seconds=record.get(
                "reindex_timeout_seconds", 7200.0),
        )
        storage_policy.assert_no_link_components(config.db_path)
        storage_policy.assert_no_link_components(config.chunks_path)
        registry[corpus_id] = config
    return registry


def _search_request_payload(
        config: service_contracts.CorpusConfig,
        request: service_contracts.SearchRequest,
        request_id: str, *,
        security_policy: release_security.ReleaseSecurityPolicy | None = None,
        ) -> dict[str, Any]:
    policy = security_policy or release_security.ReleaseSecurityPolicy()
    if not isinstance(policy, release_security.ReleaseSecurityPolicy):
        raise TypeError("invalid release-security policy")
    return {
        "schema_version": _INTERNAL_SCHEMA_VERSION,
        "kind": "service_search_request",
        "request_id": request_id,
        "release_security": policy.provenance(),
        "corpus": {
            "corpus_id": config.corpus_id,
            "backend": config.backend,
            "db_path": str(config.db_path),
            "chunks_path": str(config.chunks_path),
            "collection_name": config.collection_name,
            "embedding_model": config.embedding_model,
            "db_lock_timeout_seconds": config.db_lock_timeout_seconds,
        },
        "search": {
            "query": request.query,
            "limit": request.limit,
            "mode": request.mode,
            "filters": request.filters.as_dict(),
        },
    }


def _parse_worker_request(
        payload: object,
        ) -> tuple[service_contracts.CorpusConfig,
                   service_contracts.SearchRequest, str,
                   release_security.ReleaseSecurityPolicy]:
    payload = _exact_fields(
        payload,
        allowed=frozenset({
            "schema_version", "kind", "request_id", "release_security",
            "corpus", "search"}),
        required=frozenset({
            "schema_version", "kind", "request_id", "release_security",
            "corpus", "search"}))
    if (payload["schema_version"] != _INTERNAL_SCHEMA_VERSION
            or payload["kind"] != "service_search_request"):
        raise service_contracts.ServiceContractError()
    request_id = service_contracts.validate_request_id(payload["request_id"])
    policy = release_security.ReleaseSecurityPolicy.from_provenance(
        payload["release_security"])
    corpus = _exact_fields(
        payload["corpus"],
        allowed=frozenset({
            "corpus_id", "backend", "db_path", "chunks_path",
            "collection_name", "embedding_model",
            "db_lock_timeout_seconds",
        }),
        required=frozenset({
            "corpus_id", "backend", "db_path", "chunks_path",
            "collection_name", "embedding_model",
            "db_lock_timeout_seconds",
        }))
    config = service_contracts.CorpusConfig(
        corpus_id=corpus["corpus_id"],
        backend=corpus["backend"],
        db_path=_bounded_path(corpus["db_path"], base=Path.cwd()),
        chunks_path=_bounded_path(corpus["chunks_path"], base=Path.cwd()),
        collection_name=corpus["collection_name"],
        embedding_model=corpus["embedding_model"],
        db_lock_timeout_seconds=corpus["db_lock_timeout_seconds"],
    )
    request = service_contracts.parse_search_request(payload["search"])
    return config, request, request_id, policy


def _execute_search(
        config: service_contracts.CorpusConfig,
        request: service_contracts.SearchRequest,
        request_id: str, *,
        search_index_fn: Callable[..., Any],
        security_policy: release_security.ReleaseSecurityPolicy | None = None,
        ) -> dict[str, Any]:
    hybrid = None
    if request.mode == "vector":
        hybrid = False
    elif request.mode == "hybrid":
        hybrid = True
    response = search_index_fn(
        request.query,
        config.db_path,
        db_backend="qdrant",
        n_results=request.limit,
        content_type=request.filters.content_type,
        chapter_num=request.filters.chapter_num,
        collection_name=config.collection_name,
        embedding_model=config.embedding_model,
        use_reranker=False,
        hybrid=hybrid,
        chunks_path=config.chunks_path,
        lock_timeout=config.db_lock_timeout_seconds,
        security_policy=security_policy,
    )
    return service_contracts.public_search_response(
        response, corpus_id=config.corpus_id, request_id=request_id)


def _write_worker_envelope(path: Path, payload: dict[str, Any]) -> None:
    encoded = json.dumps(
        payload, ensure_ascii=False, allow_nan=False,
        sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_SEARCH_RESULT_BYTES:
        payload = {
            "schema_version": _INTERNAL_SCHEMA_VERSION,
            "kind": "service_search_result",
            "ok": False,
            "error_code": "service_unavailable",
        }
    storage_policy.atomic_write_private_json(path, payload)


def search_worker_main(
        request_path: Path, result_path: Path, *,
        search_index_fn: Callable[..., Any]) -> int:
    """Run one trusted request file; emit no raw error or private log."""
    try:
        payload = _read_private_json(
            request_path, max_bytes=service_contracts.MAX_REQUEST_BYTES)
        config, request, request_id, policy = _parse_worker_request(payload)
        result = _execute_search(
            config, request, request_id,
            search_index_fn=search_index_fn,
            security_policy=policy)
        envelope = {
            "schema_version": _INTERNAL_SCHEMA_VERSION,
            "kind": "service_search_result",
            "ok": True,
            "result": result,
        }
        exit_code = 0
    except BaseException:
        envelope = {
            "schema_version": _INTERNAL_SCHEMA_VERSION,
            "kind": "service_search_result",
            "ok": False,
            "error_code": "service_unavailable",
        }
        exit_code = 1
    try:
        _write_worker_envelope(result_path, envelope)
    except BaseException:
        return 1
    return exit_code


def _parse_worker_result(
        payload: object, *, expected_corpus_id: str,
        expected_request_id: str) -> dict[str, Any]:
    payload = _exact_fields(
        payload,
        allowed=frozenset({
            "schema_version", "kind", "ok", "result", "error_code"}),
        required=frozenset({"schema_version", "kind", "ok"}))
    if (payload["schema_version"] != _INTERNAL_SCHEMA_VERSION
            or payload["kind"] != "service_search_result"
            or not isinstance(payload["ok"], bool)):
        raise ServiceRuntimeError("service_unavailable")
    if not payload["ok"]:
        if (frozenset(payload) != frozenset({
                "schema_version", "kind", "ok", "error_code"})
                or payload["error_code"] != "service_unavailable"):
            raise ServiceRuntimeError("service_unavailable")
        raise ServiceRuntimeError("service_unavailable")
    if frozenset(payload) != frozenset({
            "schema_version", "kind", "ok", "result"}):
        raise ServiceRuntimeError("service_unavailable")
    try:
        result = service_contracts.validate_public_search_response(
            payload["result"],
            expected_corpus_id=expected_corpus_id,
            expected_request_id=expected_request_id,
        )
    except service_contracts.ServiceContractError as exc:
        raise ServiceRuntimeError("service_unavailable") from exc
    return result


def supervised_search(
        config: service_contracts.CorpusConfig,
        request: service_contracts.SearchRequest,
        request_id: str, *,
        temporary_root: Path | None = None,
        security_policy: release_security.ReleaseSecurityPolicy | None = None,
        runtime_binding: (
            service_runtime_binding.ServiceRuntimeBinding | None) = None,
        ) -> dict[str, Any]:
    """Execute one search behind a hard, process-tree-cleaning deadline."""
    request_id = service_contracts.validate_request_id(request_id)
    binding = (
        _default_service_runtime_binding()
        if runtime_binding is None else runtime_binding)
    if not isinstance(
            binding, service_runtime_binding.ServiceRuntimeBinding):
        raise TypeError("runtime_binding must be a ServiceRuntimeBinding")
    parent = (
        storage_policy.ensure_private_directory(temporary_root)
        if temporary_root is not None else None)
    temporary = tempfile.mkdtemp(
        prefix="request-", dir=None if parent is None else str(parent))
    temporary_path = Path(temporary)
    temporary_identity = _path_identity(temporary_path)
    try:
        storage_policy.ensure_private_directory(temporary_path)
        request_path = temporary_path / "request.json"
        result_path = temporary_path / "result.json"
        storage_policy.atomic_write_private_json(
            request_path, _search_request_payload(
                config, request, request_id,
                security_policy=security_policy))
        try:
            with open(os.devnull, "wb") as output_sink:
                exit_code = binding.supervisor(
                    binding.worker_script_path,
                    [_SEARCH_WORKER_ACTION, str(request_path), str(result_path)],
                    operation="service search",
                    timeout=config.search_timeout_seconds,
                    stdout_target=output_sink,
                    stderr_target=output_sink,
                )
        except binding.cleanup_error_type as exc:
            raise ServiceRuntimeError(
                "service_unavailable", fatal=True) from exc
        if exit_code == 124:
            raise ServiceRuntimeError("deadline_exceeded")
        payload = _read_private_json(
            result_path, max_bytes=MAX_SEARCH_RESULT_BYTES)
        result = _parse_worker_result(
            payload,
            expected_corpus_id=config.corpus_id,
            expected_request_id=request_id,
        )
        if exit_code != 0:
            raise ServiceRuntimeError("service_unavailable")
        return result
    finally:
        try:
            _remove_search_directory(
                temporary_path, temporary_identity)
        except ServiceRuntimeError:
            raise
        except BaseException as exc:
            raise ServiceRuntimeError(
                "service_unavailable", fatal=True) from exc


class RagApplicationService:
    """Single-principal local application surface shared by HTTP adapters."""

    def __init__(
            self,
            corpora: Mapping[str, service_contracts.CorpusConfig], *,
            job_root: Path | None = None,
            working_directory: Path | None = None,
            output_root: Path | None = None,
            service_state_root: Path | None = None,
            ready_timeout_seconds: float = 10.0,
            max_concurrent_searches: int = 2,
            security_policy: (
                release_security.ReleaseSecurityPolicy | None) = None,
            runtime_binding: (
                service_runtime_binding.ServiceRuntimeBinding | None) = None,
            job_coordination_binding: (
                job_coordination_contracts.ServiceJobCoordinationBinding
                | None) = None,
            search_runner: Callable[[
                service_contracts.CorpusConfig,
                service_contracts.SearchRequest, str], dict[str, Any]
            ] = supervised_search,
            launcher: Callable[..., object] | object = _LAUNCHER_UNSET,
    ):
        if (not isinstance(corpora, Mapping) or not corpora
                or len(corpora) > MAX_CORPORA):
            raise service_contracts.ServiceContractError()
        checked: dict[str, service_contracts.CorpusConfig] = {}
        for key, config in corpora.items():
            if (not isinstance(config, service_contracts.CorpusConfig)
                    or key != config.corpus_id or key in checked):
                raise service_contracts.ServiceContractError()
            checked[key] = config
        self.security_policy = (
            security_policy or release_security.ReleaseSecurityPolicy())
        if not isinstance(
                self.security_policy,
                release_security.ReleaseSecurityPolicy):
            raise service_contracts.ServiceContractError()
        self._runtime_binding = (
            _default_service_runtime_binding()
            if runtime_binding is None else runtime_binding)
        if not isinstance(
                self._runtime_binding,
                service_runtime_binding.ServiceRuntimeBinding):
            raise service_contracts.ServiceContractError()
        self._job_coordination = (
            _default_service_job_coordination_binding()
            if job_coordination_binding is None
            else job_coordination_binding)
        if not isinstance(
                self._job_coordination,
                job_coordination_contracts.ServiceJobCoordinationBinding):
            raise service_contracts.ServiceContractError()
        for config in checked.values():
            if self._runtime_binding.is_api_embedding_model(
                    config.embedding_model):
                release_security.require_cloud_egress(
                    self.security_policy,
                    feature=f"cloud embedding for corpus {config.corpus_id}")
        if (isinstance(max_concurrent_searches, bool)
                or not isinstance(max_concurrent_searches, int)
                or not 1 <= max_concurrent_searches <= 16):
            raise service_contracts.ServiceContractError()
        self.ready_timeout_seconds = float(
            service_contracts.positive_finite(ready_timeout_seconds))
        if (self.ready_timeout_seconds >
                service_contracts.MAX_READY_TIMEOUT_SECONDS):
            raise service_contracts.ServiceContractError()
        requested_working_directory = Path(
            Path.cwd() if working_directory is None else working_directory)
        self.working_directory = Path(os.path.abspath(
            requested_working_directory))
        storage_policy.assert_no_link_components(self.working_directory)
        if not self.working_directory.is_dir():
            raise service_contracts.ServiceContractError()
        self.working_directory = self.working_directory.resolve()
        requested_output = (
            self.working_directory / "output"
            if output_root is None else Path(output_root))
        if not requested_output.is_absolute():
            requested_output = self.working_directory / requested_output
        self.output_root = storage_policy.ensure_private_directory(
            requested_output)
        requested_jobs = (
            self.output_root / DEFAULT_SERVICE_JOB_ROOT_NAME
            if job_root is None else Path(job_root))
        if not requested_jobs.is_absolute():
            requested_jobs = self.working_directory / requested_jobs
        self.store = job_runtime.JobStore(
            requested_jobs,
            permitted_root_metadata_names=frozenset({
                _JOB_ROOT_OWNER_MARKER_NAME}),
        )
        requested_state = (
            self.output_root / ".rag-service"
            if service_state_root is None else Path(service_state_root))
        if not requested_state.is_absolute():
            requested_state = self.working_directory / requested_state
        self.service_state_root = storage_policy.ensure_private_directory(
            requested_state)
        self.search_temporary_root = storage_policy.ensure_private_directory(
            self.service_state_root / "search-tmp")
        self._job_root_owner_path = (
            self.store.root / _JOB_ROOT_OWNER_MARKER_NAME)
        self._state_owner_path = (
            self.service_state_root / _STATE_OWNER_MARKER_NAME)
        job_device, job_inode = _path_identity(self.store.root)
        state_device, state_inode = _path_identity(self.service_state_root)
        self._owner_payload = {
            "schema_version": _OWNER_MARKER_SCHEMA_VERSION,
            "kind": _OWNER_MARKER_KIND,
            "job_root_sha256": _path_digest(self.store.root),
            "job_root_device_hex": format(job_device, "x"),
            "job_root_inode_hex": format(job_inode, "x"),
            "service_state_root_sha256": _path_digest(
                self.service_state_root),
            "service_state_root_device_hex": format(state_device, "x"),
            "service_state_root_inode_hex": format(state_inode, "x"),
        }
        self._service_key = (
            os.getpid(), os.path.normcase(str(self.service_state_root.resolve())))
        self.corpora = checked
        if search_runner is supervised_search:
            self._search_runner = lambda config, request, request_id: (
                supervised_search(
                    config, request, request_id,
                    temporary_root=self.search_temporary_root,
                    security_policy=self.security_policy,
                    runtime_binding=self._runtime_binding))
        else:
            self._search_runner = search_runner
        self._launcher = (
            self._job_coordination.launch_detached
            if launcher is _LAUNCHER_UNSET else launcher)
        self._search_slots = threading.BoundedSemaphore(
            max_concurrent_searches)
        self._state_lock = threading.Lock()
        self._state_changed = threading.Condition(self._state_lock)
        self._active_searches = 0
        self._closing = False
        self._job_operation_lock = threading.Lock()
        self._launching_job_ids: set[str] = set()
        self._started = False
        self._healthy = True
        self._instance_lease = None

    @property
    def started(self) -> bool:
        with self._state_lock:
            return self._started

    @property
    def healthy(self) -> bool:
        with self._state_lock:
            return self._started and self._healthy and not self._closing

    def mark_unhealthy(self) -> None:
        with self._state_lock:
            self._healthy = False

    def _read_owner_marker(self, path: Path) -> dict[str, Any]:
        try:
            storage_policy.enforce_private_path(path, directory=False)
            payload = _read_private_json(
                path, max_bytes=_MAX_OWNER_MARKER_BYTES)
        except (OSError, storage_policy.StoragePolicyError,
                ServiceRuntimeError) as exc:
            raise ServiceRuntimeError(
                "service_unavailable", fatal=True) from exc
        if payload != self._owner_payload:
            raise ServiceRuntimeError("service_unavailable", fatal=True)
        return payload

    def _job_root_has_only_lock(self) -> bool:
        try:
            entries = list(os.scandir(self.store.root))
        except OSError as exc:
            raise ServiceRuntimeError(
                "service_unavailable", fatal=True) from exc
        return all(entry.name == ".store.lock" for entry in entries)

    def _claim_job_root(self) -> None:
        """Bind the dedicated job root to this exact service-state root."""
        with self.store.store_lease():
            state_exists = os.path.lexists(self._state_owner_path)
            root_exists = os.path.lexists(self._job_root_owner_path)
            if state_exists:
                self._read_owner_marker(self._state_owner_path)
            if root_exists:
                self._read_owner_marker(self._job_root_owner_path)
            if state_exists and root_exists:
                return
            if root_exists and not state_exists:
                raise ServiceRuntimeError(
                    "service_unavailable", fatal=True)
            if not self._job_root_has_only_lock():
                raise ServiceRuntimeError(
                    "service_unavailable", fatal=True)
            if not state_exists:
                storage_policy.atomic_write_private_json(
                    self._state_owner_path, self._owner_payload)
            storage_policy.atomic_write_private_json(
                self._job_root_owner_path, self._owner_payload)
            self._read_owner_marker(self._state_owner_path)
            self._read_owner_marker(self._job_root_owner_path)

    def _require_job_root_owned(self) -> None:
        try:
            self._read_owner_marker(self._state_owner_path)
            self._read_owner_marker(self._job_root_owner_path)
        except ServiceRuntimeError:
            self.mark_unhealthy()
            raise

    def start(self) -> None:
        with self._state_lock:
            if self._started:
                return
        with _ACTIVE_SERVICE_GUARD:
            if self._service_key in _ACTIVE_SERVICE_KEYS:
                raise ServiceRuntimeError("service_unavailable")
            _ACTIVE_SERVICE_KEYS.add(self._service_key)
        lease = None
        lease_entered = False
        try:
            lease = self._runtime_binding.instance_lease_factory(
                self.service_state_root / "instance")
            lease.__enter__()
            lease_entered = True
            self._claim_job_root()
            _cleanup_stale_search_directories(self.search_temporary_root)
            self._reconcile_service_jobs(fail_queued=True)
        except BaseException as exc:
            with _ACTIVE_SERVICE_GUARD:
                _ACTIVE_SERVICE_KEYS.discard(self._service_key)
            try:
                if lease_entered:
                    lease.__exit__(type(exc), exc, exc.__traceback__)
            except BaseException:
                pass
            raise ServiceRuntimeError(
                "service_unavailable", fatal=True) from exc
        with self._state_changed:
            self._instance_lease = lease
            self._started = True
            self._healthy = True
            self._closing = False
            self._state_changed.notify_all()

    def close(self) -> None:
        with self._state_changed:
            self._closing = True
            self._healthy = False
            while self._active_searches:
                self._state_changed.wait()
            lease = self._instance_lease
            self._instance_lease = None
            self._started = False
            self._closing = False
            self._state_changed.notify_all()
        with _ACTIVE_SERVICE_GUARD:
            _ACTIVE_SERVICE_KEYS.discard(self._service_key)
        if lease is not None:
            lease.__exit__(None, None, None)

    def _require_started(self) -> None:
        if not self.started:
            raise ServiceRuntimeError("service_unavailable")

    def _require_healthy(self) -> None:
        if not self.healthy:
            raise ServiceRuntimeError("service_unavailable")

    def _begin_search(self) -> None:
        with self._state_changed:
            if (not self._started or not self._healthy or self._closing):
                raise ServiceRuntimeError("service_unavailable")
            self._active_searches += 1

    def _end_search(self) -> None:
        with self._state_changed:
            self._active_searches -= 1
            self._state_changed.notify_all()

    def readiness(self) -> bool:
        if not self.healthy:
            return False
        for config in self.corpora.values():
            try:
                storage_policy.assert_no_link_components(config.db_path)
                storage_policy.assert_no_link_components(config.chunks_path)
                if (not config.db_path.is_dir()
                        or not config.chunks_path.is_file()):
                    return False
            except (OSError, storage_policy.StoragePolicyError):
                return False
        return True

    def _execution_is_service_reindex(
            self, execution: job_runtime.JobExecution) -> bool:
        if (execution.command != "index"
                or execution.working_directory != self.working_directory
                or execution.output_root != self.output_root):
            return False
        for config in self.corpora.values():
            if execution.timeout_seconds != config.reindex_timeout_seconds:
                continue
            for full_reindex in (False, True):
                request = service_contracts.ReindexRequest(full_reindex)
                if execution.argv == self._reindex_argv(config, request):
                    return True
        return False

    def _require_service_execution(
            self, job_id: str, *, lease: job_runtime.JobLease | None = None
            ) -> job_runtime.JobExecution:
        try:
            execution = self.store.load_execution(job_id, lease=lease)
        except job_runtime.JobNotFoundError as exc:
            raise ServiceRuntimeError("not_found") from exc
        except job_runtime.JobBusyError as exc:
            raise ServiceRuntimeError("service_unavailable") from exc
        except (job_runtime.JobCorruptError,
                job_runtime.JobStateError) as exc:
            self.mark_unhealthy()
            raise ServiceRuntimeError(
                "service_unavailable", fatal=True) from exc
        if not self._execution_is_service_reindex(execution):
            raise ServiceRuntimeError("not_found")
        return execution

    def _reconcile_service_jobs(
            self, *, fail_queued: bool) -> list[job_runtime.JobSummary]:
        """Reconcile only exact service reindex specs; never touch foreign jobs."""
        self._require_job_root_owned()
        try:
            snapshots = self.store.list_jobs()
            results: list[job_runtime.JobSummary] = []
            for snapshot in snapshots:
                try:
                    execution = self.store.load_execution(snapshot.job_id)
                except job_runtime.JobNotFoundError:
                    continue
                if not self._execution_is_service_reindex(execution):
                    continue
                if snapshot.terminal:
                    results.append(snapshot)
                    continue
                try:
                    with self.store.lease(
                            snapshot.job_id, timeout=0) as lease:
                        execution = self.store.load_execution(
                            snapshot.job_id, lease=lease)
                        if not self._execution_is_service_reindex(execution):
                            continue
                        results.append(self._job_coordination.reconcile_job(
                            self.store, snapshot.job_id,
                            fail_queued=(
                                fail_queued
                                and snapshot.job_id not in
                                self._launching_job_ids),
                            lease=lease,
                        ))
                except job_runtime.JobBusyError:
                    results.append(snapshot)
                except job_runtime.JobNotFoundError:
                    continue
            return results
        except (job_runtime.JobCorruptError,
                self._job_coordination.corrupt_error_type) as exc:
            self.mark_unhealthy()
            raise ServiceRuntimeError(
                "service_unavailable", fatal=True) from exc
        except job_runtime.JobBusyError as exc:
            raise ServiceRuntimeError("service_unavailable") from exc
        except job_runtime.JobRuntimeError as exc:
            raise ServiceRuntimeError("service_unavailable") from exc

    def reconcile_jobs(self) -> list[dict[str, Any]]:
        self._require_started()
        try:
            with self._job_operation_lock:
                summaries = self._reconcile_service_jobs(fail_queued=True)
            return [service_contracts.public_job(item) for item in summaries]
        except (job_runtime.JobCorruptError,
                self._job_coordination.corrupt_error_type) as exc:
            self.mark_unhealthy()
            raise ServiceRuntimeError(
                "service_unavailable", fatal=True) from exc
        except job_runtime.JobRuntimeError as exc:
            raise ServiceRuntimeError("service_unavailable") from exc

    def public_corpora(self) -> list[dict[str, Any]]:
        self._require_started()
        return [
            self.corpora[key].public_dict() for key in sorted(self.corpora)]

    def corpus(self, corpus_id: str) -> service_contracts.CorpusConfig:
        self._require_started()
        service_contracts.validate_corpus_id(corpus_id)
        try:
            return self.corpora[corpus_id]
        except KeyError as exc:
            raise ServiceRuntimeError("not_found") from exc

    def search(
            self, corpus_id: str, request: service_contracts.SearchRequest,
            request_id: str) -> dict[str, Any]:
        config = self.corpus(corpus_id)
        if not isinstance(request, service_contracts.SearchRequest):
            raise service_contracts.ServiceContractError()
        service_contracts.validate_request_id(request_id)
        self._begin_search()
        acquired = False
        try:
            if not self._search_slots.acquire(blocking=False):
                raise ServiceRuntimeError("concurrency_limited")
            acquired = True
            result = self._search_runner(config, request, request_id)
            try:
                return service_contracts.validate_public_search_response(
                    result,
                    expected_corpus_id=corpus_id,
                    expected_request_id=request_id,
                )
            except service_contracts.ServiceContractError as exc:
                self.mark_unhealthy()
                raise ServiceRuntimeError(
                    "service_unavailable", fatal=True) from exc
        except ServiceRuntimeError as exc:
            if exc.fatal:
                self.mark_unhealthy()
            raise
        except BaseException as exc:
            self.mark_unhealthy()
            raise ServiceRuntimeError("service_unavailable") from exc
        finally:
            if acquired:
                self._search_slots.release()
            self._end_search()

    @staticmethod
    def _cursor_key(summary: job_runtime.JobSummary) -> tuple[float, str]:
        return summary.created_at, summary.job_id

    def list_jobs(
            self, *, status: str | None = None,
            limit: int = DEFAULT_JOB_PAGE_LIMIT,
            cursor: tuple[float, str] | None = None) -> dict[str, Any]:
        self._require_started()
        if status is not None and status not in service_contracts.JOB_STATUSES:
            raise service_contracts.ServiceContractError()
        if (isinstance(limit, bool) or not isinstance(limit, int)
                or not 1 <= limit <= MAX_JOB_PAGE_LIMIT):
            raise service_contracts.ServiceContractError()
        self._require_job_root_owned()
        try:
            snapshots = self.store.list_jobs()
            summaries = []
            for item in snapshots:
                try:
                    execution = self.store.load_execution(item.job_id)
                except job_runtime.JobNotFoundError:
                    continue
                if self._execution_is_service_reindex(execution):
                    summaries.append(item)
        except job_runtime.JobBusyError as exc:
            raise ServiceRuntimeError("service_unavailable") from exc
        except job_runtime.JobCorruptError as exc:
            self.mark_unhealthy()
            raise ServiceRuntimeError(
                "service_unavailable", fatal=True) from exc
        if status is not None:
            summaries = [item for item in summaries if item.status == status]
        if cursor is not None:
            created_at, job_id = cursor
            created_at = service_contracts.finite_timestamp(created_at)
            job_id = service_contracts.validate_job_id(job_id)
            summaries = [
                item for item in summaries
                if self._cursor_key(item) < (created_at, job_id)
            ]
        selected = summaries[:limit]
        next_cursor = None
        if len(summaries) > limit and selected:
            last = selected[-1]
            next_cursor = service_contracts.encode_job_cursor(
                last.created_at, last.job_id)
        return {
            "schema_version": service_contracts.SERVICE_SCHEMA_VERSION,
            "items": [service_contracts.public_job(item) for item in selected],
            "next_cursor": next_cursor,
        }

    def get_job(self, job_id: str) -> dict[str, Any]:
        self._require_started()
        job_id = service_contracts.validate_job_id(job_id)
        self._require_job_root_owned()
        try:
            self._require_service_execution(job_id)
            return service_contracts.public_job(self.store.get_job(job_id))
        except job_runtime.JobNotFoundError as exc:
            raise ServiceRuntimeError("not_found") from exc
        except job_runtime.JobCorruptError as exc:
            self.mark_unhealthy()
            raise ServiceRuntimeError("service_unavailable") from exc

    def _expected_job(
            self, job_id: str, *, attempt_number: int,
            revision: int) -> job_runtime.JobSummary:
        job_id = service_contracts.validate_job_id(job_id)
        service_contracts.job_etag(attempt_number, revision)
        self._require_job_root_owned()
        self._require_service_execution(job_id)
        try:
            current = self._job_coordination.reconcile_job(
                self.store, job_id)
        except job_runtime.JobNotFoundError as exc:
            raise ServiceRuntimeError("not_found") from exc
        except job_runtime.JobBusyError as exc:
            raise ServiceRuntimeError("service_unavailable") from exc
        except (job_runtime.JobCorruptError,
                self._job_coordination.corrupt_error_type) as exc:
            self.mark_unhealthy()
            raise ServiceRuntimeError(
                "service_unavailable", fatal=True) from exc
        except job_runtime.JobRuntimeError as exc:
            raise ServiceRuntimeError("service_unavailable") from exc
        if (current.attempt_number != attempt_number
                or current.revision != revision):
            raise ServiceRuntimeError("precondition_failed")
        return current

    def cancel_job(
            self, job_id: str, *, attempt_number: int,
            revision: int) -> ServiceJobResult:
        self._require_started()
        current = self._expected_job(
            job_id, attempt_number=attempt_number, revision=revision)
        if current.terminal:
            raise ServiceRuntimeError("conflict")
        try:
            summary = self.store.request_cancel(
                job_id,
                expected_revision=revision,
                expected_attempt_number=attempt_number,
            )
        except job_runtime.JobStateError as exc:
            raise ServiceRuntimeError("precondition_failed") from exc
        except job_runtime.JobBusyError as exc:
            raise ServiceRuntimeError("service_unavailable") from exc
        except job_runtime.JobNotFoundError as exc:
            raise ServiceRuntimeError("not_found") from exc
        except job_runtime.JobCorruptError as exc:
            self.mark_unhealthy()
            raise ServiceRuntimeError(
                "service_unavailable", fatal=True) from exc
        return ServiceJobResult(service_contracts.public_job(summary))

    def _raise_retention_failure(
            self, exc: retention.RetentionError, *,
            state_code: str) -> None:
        """Translate wrapped job races without misclassifying integrity loss."""
        cause = exc.__cause__
        if isinstance(cause, job_runtime.JobBusyError):
            raise ServiceRuntimeError("service_unavailable") from exc
        if isinstance(cause, job_runtime.JobNotFoundError):
            raise ServiceRuntimeError("not_found") from exc
        if isinstance(cause, job_runtime.JobStateError):
            raise ServiceRuntimeError(state_code) from exc
        self.mark_unhealthy()
        raise ServiceRuntimeError(
            "service_unavailable", fatal=True) from exc

    def _mark_launch_failed(self, job_id: str) -> None:
        try:
            execution = self.store.load_execution(job_id)
            if execution.status not in job_runtime.TERMINAL_JOB_STATUSES:
                self.store.transition_job(
                    job_id, "failed",
                    attempt_token=execution.attempt_token,
                    expected_revision=execution.revision,
                    lease_timeout=0,
                )
        except Exception as exc:
            self.mark_unhealthy()
            raise ServiceRuntimeError(
                "service_unavailable", job_id=job_id, fatal=True) from exc

    def _launch_job(self, job_id: str) -> None:
        try:
            self._launcher(
                self.store, job_id,
                ready_timeout=self.ready_timeout_seconds)
        except Exception as exc:
            try:
                self._mark_launch_failed(job_id)
            except ServiceRuntimeError:
                raise
            raise ServiceRuntimeError(
                "service_unavailable", job_id=job_id) from exc

    def _reindex_argv(
            self, config: service_contracts.CorpusConfig,
            request: service_contracts.ReindexRequest) -> tuple[str, ...]:
        arguments = [
            "--chunks", str(config.chunks_path),
            "--db", str(config.db_path),
            "--collection", config.collection_name,
            "--embedding-model", config.embedding_model,
            "--db-backend", "qdrant",
            "--db-lock-timeout", f"{config.db_lock_timeout_seconds:g}",
            "--release-security-policy-version",
            str(self.security_policy.schema_version),
            "--security-profile", self.security_policy.profile,
            "--network-policy", self.security_policy.network_policy,
            "--model-download-policy",
            self.security_policy.model_download_policy,
        ]
        if self.security_policy.cache_namespace_id is not None:
            arguments.extend([
                "--release-cache-namespace-id",
                self.security_policy.cache_namespace_id,
            ])
        if self.security_policy.trust_environment_network:
            arguments.append("--trust-environment-network")
        if request.full_reindex:
            arguments.append("--full-reindex")
        return tuple(arguments)

    def _same_reindex_spec(
            self, job_id: str, *, argv: Sequence[str],
            config: service_contracts.CorpusConfig) -> bool:
        execution = self.store.load_execution(job_id)
        return (
            execution.command == "index"
            and execution.argv == tuple(argv)
            and execution.working_directory == self.working_directory
            and execution.output_root == self.output_root
            and execution.timeout_seconds == config.reindex_timeout_seconds
        )

    def reindex(
            self, corpus_id: str, request: service_contracts.ReindexRequest,
            *, idempotency_key: str) -> ServiceJobResult:
        self._require_healthy()
        self._require_job_root_owned()
        config = self.corpus(corpus_id)
        if not isinstance(request, service_contracts.ReindexRequest):
            raise service_contracts.ServiceContractError()
        job_id = service_contracts.job_id_for_idempotency(
            corpus_id, idempotency_key)
        argv = self._reindex_argv(config, request)
        replay = False
        with self._job_operation_lock:
            try:
                summary = self.store.submit_job(
                    "index", argv,
                    timeout_seconds=config.reindex_timeout_seconds,
                    job_id=job_id,
                    working_directory=self.working_directory,
                    output_root=self.output_root,
                )
            except job_runtime.JobAlreadyExistsError:
                try:
                    if not self._same_reindex_spec(
                            job_id, argv=argv, config=config):
                        raise ServiceRuntimeError("conflict")
                    summary = self.store.get_job(job_id)
                    if (summary.status == "queued"
                            and job_id not in self._launching_job_ids):
                        summary = self._job_coordination.reconcile_job(
                            self.store, job_id, fail_queued=True)
                except (job_runtime.JobCorruptError,
                        self._job_coordination.corrupt_error_type) as exc:
                    self.mark_unhealthy()
                    raise ServiceRuntimeError(
                        "service_unavailable", fatal=True) from exc
                except job_runtime.JobNotFoundError as exc:
                    raise ServiceRuntimeError("not_found") from exc
                except job_runtime.JobBusyError as exc:
                    raise ServiceRuntimeError("service_unavailable") from exc
                except job_runtime.JobStateError as exc:
                    raise ServiceRuntimeError("conflict") from exc
                except job_runtime.JobRuntimeError as exc:
                    raise ServiceRuntimeError(
                        "service_unavailable") from exc
                replay = True
            except job_runtime.JobBusyError as exc:
                raise ServiceRuntimeError("service_unavailable") from exc
            except (job_runtime.JobCorruptError,
                    job_runtime.JobValidationError) as exc:
                self.mark_unhealthy()
                raise ServiceRuntimeError(
                    "service_unavailable", fatal=True) from exc
            except job_runtime.JobRuntimeError as exc:
                raise ServiceRuntimeError("service_unavailable") from exc
            if not replay:
                self._launching_job_ids.add(job_id)
        if replay:
            return ServiceJobResult(
                service_contracts.public_job(summary),
                idempotent_replay=True)
        try:
            self._launch_job(job_id)
            return ServiceJobResult(
                service_contracts.public_job(self.store.get_job(job_id)))
        finally:
            with self._job_operation_lock:
                self._launching_job_ids.discard(job_id)

    def resume_job(
            self, job_id: str, *, attempt_number: int,
            revision: int) -> ServiceJobResult:
        self._require_healthy()
        with self._job_operation_lock:
            current = self._expected_job(
                job_id, attempt_number=attempt_number, revision=revision)
            if current.status not in job_runtime.RESUMABLE_JOB_STATUSES:
                raise ServiceRuntimeError("conflict")
            try:
                self.store.prepare_resume(
                    job_id, expected_revision=revision)
            except job_runtime.JobStateError as exc:
                raise ServiceRuntimeError("precondition_failed") from exc
            except job_runtime.JobBusyError as exc:
                raise ServiceRuntimeError("service_unavailable") from exc
            except job_runtime.JobNotFoundError as exc:
                raise ServiceRuntimeError("not_found") from exc
            except job_runtime.JobCorruptError as exc:
                self.mark_unhealthy()
                raise ServiceRuntimeError(
                    "service_unavailable", fatal=True) from exc
            self._launching_job_ids.add(job_id)
        try:
            self._launch_job(job_id)
            return ServiceJobResult(
                service_contracts.public_job(self.store.get_job(job_id)))
        finally:
            with self._job_operation_lock:
                self._launching_job_ids.discard(job_id)

    def deletion_plan(self, job_id: str) -> dict[str, Any]:
        self._require_started()
        job_id = service_contracts.validate_job_id(job_id)
        with self._job_operation_lock:
            self._require_job_root_owned()
            self._require_service_execution(job_id)
            try:
                summary = self._job_coordination.reconcile_job(
                    self.store, job_id)
                plan = retention.plan_background_job_deletion(
                    self.store.root, job_id)
            except job_runtime.JobNotFoundError as exc:
                raise ServiceRuntimeError("not_found") from exc
            except job_runtime.JobStateError as exc:
                raise ServiceRuntimeError("conflict") from exc
            except job_runtime.JobBusyError as exc:
                raise ServiceRuntimeError("service_unavailable") from exc
            except retention.RetentionError as exc:
                self._raise_retention_failure(exc, state_code="conflict")
            except (job_runtime.JobCorruptError,
                    self._job_coordination.corrupt_error_type) as exc:
                self.mark_unhealthy()
                raise ServiceRuntimeError(
                    "service_unavailable", fatal=True) from exc
            except job_runtime.JobRuntimeError as exc:
                raise ServiceRuntimeError("service_unavailable") from exc
        return {
            "schema_version": service_contracts.SERVICE_SCHEMA_VERSION,
            "job": service_contracts.public_job(summary),
            "candidate_count": len(plan.candidates),
            "total_bytes": plan.total_bytes,
            "apply_required": True,
        }

    def delete_job(
            self, job_id: str, *, attempt_number: int,
            revision: int) -> dict[str, Any]:
        self._require_healthy()
        with self._job_operation_lock:
            current = self._expected_job(
                job_id, attempt_number=attempt_number, revision=revision)
            if current.status not in job_runtime.DELETABLE_JOB_STATUSES:
                raise ServiceRuntimeError("conflict")
            try:
                self.store.prepare_delete(
                    job_id,
                    expected_revision=revision,
                    expected_attempt_number=attempt_number,
                )
                plan = retention.plan_background_job_deletion(
                    self.store.root, job_id)
                outcome = retention.apply_retention_plan(plan)
            except job_runtime.JobStateError as exc:
                raise ServiceRuntimeError("precondition_failed") from exc
            except job_runtime.JobBusyError as exc:
                raise ServiceRuntimeError("service_unavailable") from exc
            except job_runtime.JobNotFoundError as exc:
                raise ServiceRuntimeError("not_found") from exc
            except retention.RetentionError as exc:
                self._raise_retention_failure(
                    exc, state_code="precondition_failed")
            except job_runtime.JobCorruptError as exc:
                self.mark_unhealthy()
                raise ServiceRuntimeError(
                    "service_unavailable", fatal=True) from exc
        return {
            "schema_version": service_contracts.SERVICE_SCHEMA_VERSION,
            "job_id": job_id,
            "deleted": True,
            "deleted_count": int(outcome.get("deleted_count", 0)),
            "deleted_bytes": int(outcome.get("deleted_bytes", 0)),
        }


def main(argv: Sequence[str] | None = None) -> int:
    """Fail closed; private worker dispatch lives in service_search_worker."""
    del argv
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
