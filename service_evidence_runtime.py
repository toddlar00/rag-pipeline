"""Private, bounded process transport for the opt-in evidence search worker.

The host imports no retrieval implementation.  The existing v2 search request
is nested unchanged inside a new contract; a success binds the complete private
request, not merely the public request ID.  Cleanup uncertainty remains fatal.
"""

from __future__ import annotations

from collections.abc import Callable
import hashlib
import json
import os
from pathlib import Path
import tempfile

import release_security
import service_contracts
import service_evidence_contracts as contracts
import service_runtime
import service_runtime_binding
import storage_policy


EVIDENCE_WORKER_ACTION = "_evidence_search_worker"
MAX_PRIVATE_REQUEST_BYTES = 256 * 1024
_REQUEST_FIELDS = {"schema_version", "kind", "search_request", "evidence"}
_FAILURE = {"schema_version": 1, "kind": "service_evidence_result",
            "ok": False, "error_code": "service_unavailable"}


def _encoded(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, allow_nan=False,
                      sort_keys=True, separators=(",", ":")).encode("utf-8")


def _request_digest(payload: dict) -> str:
    return hashlib.sha256(
        b"rag-service-evidence-request-v1\0" + _encoded(payload)).hexdigest()


def _parse_request(payload: object) -> tuple:
    if (type(payload) is not dict or payload.keys() != _REQUEST_FIELDS
            or type(payload["schema_version"]) is not int
            or payload["schema_version"] != 1
            or payload["kind"] != "service_evidence_request"):
        raise service_contracts.ServiceContractError()
    inner = payload["search_request"]
    if type(inner) is not dict or type(inner.get("schema_version")) is not int:
        raise service_contracts.ServiceContractError()
    config, request, request_id, policy = service_runtime._parse_worker_request(inner)
    evidence = contracts.parse_evidence_corpus(payload["evidence"], base=Path.cwd())
    if config.corpus_id != evidence.corpus_id:
        raise service_contracts.ServiceContractError()
    return config, evidence, request, request_id, policy


def _request_payload(config, evidence_config, request, request_id, *, security_policy):
    if (not isinstance(config, service_contracts.CorpusConfig)
            or not isinstance(evidence_config, contracts.EvidenceCorpusConfig)
            or not isinstance(request, service_contracts.SearchRequest)
            or config.corpus_id != evidence_config.corpus_id):
        raise service_contracts.ServiceContractError()
    service_contracts.validate_request_id(request_id)
    payload = {
        "schema_version": 1, "kind": "service_evidence_request",
        "search_request": service_runtime._search_request_payload(
            config, request, request_id, security_policy=security_policy),
        "evidence": contracts.config_payload(evidence_config),
    }
    _parse_request(payload)
    if len(_encoded(payload)) > MAX_PRIVATE_REQUEST_BYTES:
        raise service_contracts.ServiceContractError("payload_too_large")
    return payload


def evidence_worker_main(request_path: Path, result_path: Path, *,
                         execute_fn: Callable) -> int:
    """Run trusted contained code; publish only a bounded, redacted envelope."""
    try:
        payload = service_runtime._read_private_json(
            request_path, max_bytes=MAX_PRIVATE_REQUEST_BYTES)
        config, evidence, request, request_id, policy = _parse_request(payload)
        result = execute_fn(config, evidence, request, request_id,
                            security_policy=policy)
        result = contracts.validate_public_evidence_search_response(
            result, expected_corpus_id=config.corpus_id,
            expected_request_id=request_id, expected_request=request)
        envelope = {
            "schema_version": 1, "kind": "service_evidence_result", "ok": True,
            "request_sha256": _request_digest(payload), "result": result,
        }
        if len(_encoded(envelope)) > service_runtime.MAX_SEARCH_RESULT_BYTES:
            raise service_contracts.ServiceContractError("service_unavailable")
        exit_code = 0
    except BaseException:
        envelope, exit_code = dict(_FAILURE), 1
    try:
        storage_policy.atomic_write_private_json(result_path, envelope)
    except BaseException:
        return 1
    return exit_code


def _parse_result(payload: object, *, corpus_id: str, request_id: str,
                  request_sha256: str, request: service_contracts.SearchRequest) -> dict:
    if (type(payload) is not dict
            or payload.keys() != {"schema_version", "kind", "ok", "request_sha256", "result"}
            or type(payload["schema_version"]) is not int
            or payload["schema_version"] != 1
            or payload["kind"] != "service_evidence_result"
            or payload["ok"] is not True
            or payload["request_sha256"] != request_sha256):
        raise service_runtime.ServiceRuntimeError("service_unavailable")
    try:
        return contracts.validate_public_evidence_search_response(
            payload["result"], expected_corpus_id=corpus_id,
            expected_request_id=request_id, expected_request=request)
    except service_contracts.ServiceContractError as exc:
        raise service_runtime.ServiceRuntimeError("service_unavailable") from exc


def supervised_evidence_search(
        config: service_contracts.CorpusConfig,
        evidence_config: contracts.EvidenceCorpusConfig,
        request: service_contracts.SearchRequest, request_id: str, *,
        temporary_root: Path | None = None,
        security_policy: release_security.ReleaseSecurityPolicy | None = None,
        runtime_binding: service_runtime_binding.ServiceRuntimeBinding | None = None,
) -> dict:
    """Use one frozen supervisor generation with the existing deadline policy."""
    payload = _request_payload(config, evidence_config, request, request_id,
                               security_policy=security_policy)
    binding = (service_runtime_binding.default_service_runtime_binding()
               if runtime_binding is None else runtime_binding)
    if not isinstance(binding, service_runtime_binding.ServiceRuntimeBinding):
        raise TypeError("runtime_binding must be a ServiceRuntimeBinding")
    expected_digest = _request_digest(payload)
    parent = (storage_policy.ensure_private_directory(temporary_root)
              if temporary_root is not None else None)
    temporary = Path(tempfile.mkdtemp(
        prefix="request-", dir=None if parent is None else str(parent)))
    identity = service_runtime._path_identity(temporary)
    try:
        storage_policy.ensure_private_directory(temporary)
        request_path, result_path = temporary / "request.json", temporary / "result.json"
        storage_policy.atomic_write_private_json(request_path, payload)
        try:
            with open(os.devnull, "wb") as sink:
                exit_code = binding.supervisor(
                    binding.worker_script_path,
                    [EVIDENCE_WORKER_ACTION, str(request_path), str(result_path)],
                    operation="service evidence search", timeout=config.search_timeout_seconds,
                    stdout_target=sink, stderr_target=sink)
        except binding.cleanup_error_type as exc:
            raise service_runtime.ServiceRuntimeError("service_unavailable", fatal=True) from exc
        if exit_code == 124:
            raise service_runtime.ServiceRuntimeError("deadline_exceeded")
        result = _parse_result(
            service_runtime._read_private_json(
                result_path, max_bytes=service_runtime.MAX_SEARCH_RESULT_BYTES),
            corpus_id=config.corpus_id, request_id=request_id,
            request_sha256=expected_digest, request=request)
        if exit_code != 0:
            raise service_runtime.ServiceRuntimeError("service_unavailable")
        return result
    finally:
        try:
            service_runtime._remove_search_directory(temporary, identity)
        except service_runtime.ServiceRuntimeError:
            raise
        except BaseException as exc:
            raise service_runtime.ServiceRuntimeError("service_unavailable", fatal=True) from exc
