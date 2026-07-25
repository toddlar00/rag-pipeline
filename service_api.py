#!/usr/bin/env python3
"""Stable executable and import facade for the local RAG HTTP service."""

from __future__ import annotations

import argparse
import ipaddress
import os
import secrets
import stat
from pathlib import Path
from typing import Sequence

import application_composition
import release_security
import service_contracts
import service_http
import service_runtime
import storage_policy


DEFAULT_HOST = service_http.DEFAULT_HOST
DEFAULT_PORT = 8765
DEFAULT_RECONCILE_INTERVAL_SECONDS = (
    service_http.DEFAULT_RECONCILE_INTERVAL_SECONDS)
MAX_TOKEN_FILE_BYTES = 1024
_LOOPBACK_HOSTS = service_http._LOOPBACK_HOSTS
_bounded_decimal = service_http._bounded_decimal

# Preserve the established flat Python surface with object-identical aliases.
ServiceCredentials = service_http.ServiceCredentials
require_reader = service_http.require_reader
require_admin = service_http.require_admin
service_openapi_document = service_http.service_openapi_document
FastAPI = service_http.FastAPI


def create_app(
        runtime: service_runtime.RagApplicationService,
        credentials: ServiceCredentials, *,
        host: str = DEFAULT_HOST,
        reconcile_interval_seconds: float =
        DEFAULT_RECONCILE_INTERVAL_SECONDS,
        max_http_concurrency: int = 64,
        enforce_peer_loopback: bool = True) -> FastAPI:
    """Preserve the established embedded-app surface through composition."""
    return application_composition.create_service_http_app(
        runtime,
        credentials,
        host=host,
        reconcile_interval_seconds=reconcile_interval_seconds,
        max_http_concurrency=max_http_concurrency,
        enforce_peer_loopback=enforce_peer_loopback,
    )


def load_token_file(path: Path) -> str:
    """Read one owner-only token without accepting whitespace ambiguity."""
    path = Path(os.path.abspath(Path(path)))
    storage_policy.assert_no_link_components(path)
    storage_policy.enforce_private_path(path, directory=False)
    raw = service_runtime._read_bounded_regular_file(
        path, max_bytes=MAX_TOKEN_FILE_BYTES)
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise service_contracts.ServiceContractError() from exc
    if text.endswith("\r\n"):
        token = text[:-2]
    elif text.endswith("\n"):
        token = text[:-1]
    else:
        token = text
    if "\r" in token or "\n" in token:
        raise service_contracts.ServiceContractError()
    return service_contracts.validate_bearer_token(token)


def _cleanup_created_token(
        path: Path, expected_identity: tuple[int, int]) -> None:
    try:
        result = os.lstat(path)
    except FileNotFoundError:
        return
    if (not stat.S_ISREG(result.st_mode) or result.st_nlink != 1
            or (int(result.st_dev), int(result.st_ino)) != expected_identity):
        raise service_contracts.ServiceContractError()
    os.unlink(path)
    if os.path.lexists(path):
        raise service_contracts.ServiceContractError()


def _create_token_file(path: Path, token: str) -> tuple[int, int]:
    path = Path(os.path.abspath(Path(path)))
    parent = storage_policy.ensure_private_directory(path.parent)
    storage_policy.assert_no_link_components(path)
    parent_result = os.lstat(parent)
    parent_identity = (
        int(parent_result.st_dev), int(parent_result.st_ino))
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    descriptor = os.open(path, flags, storage_policy.PRIVATE_FILE_MODE)
    identity = None
    try:
        opened = os.fstat(descriptor)
        identity = int(opened.st_dev), int(opened.st_ino)
        encoded = (token + "\n").encode("ascii")
        written = os.write(descriptor, encoded)
        if written != len(encoded):
            raise OSError("token file write was incomplete")
        os.fsync(descriptor)
        result = os.fstat(descriptor)
        if (int(result.st_dev), int(result.st_ino)) != identity:
            raise service_contracts.ServiceContractError()
        os.close(descriptor)
        descriptor = -1
        named = os.lstat(path)
        if (not stat.S_ISREG(named.st_mode) or named.st_nlink != 1
                or (int(named.st_dev), int(named.st_ino)) != identity):
            raise service_contracts.ServiceContractError()
        storage_policy.enforce_private_path(path, directory=False)
        named = os.lstat(path)
        parent_after = os.lstat(parent)
        if ((int(named.st_dev), int(named.st_ino)) != identity
                or named.st_nlink != 1
                or (int(parent_after.st_dev), int(parent_after.st_ino)) !=
                parent_identity):
            raise service_contracts.ServiceContractError()
        storage_policy._fsync_parent_directory(path)
    except BaseException:
        if identity is None and descriptor >= 0:
            try:
                opened = os.fstat(descriptor)
                identity = int(opened.st_dev), int(opened.st_ino)
            except OSError:
                pass
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if identity is not None:
            try:
                _cleanup_created_token(path, identity)
            except BaseException as cleanup_error:
                raise service_contracts.ServiceContractError() from cleanup_error
        raise
    assert identity is not None
    return identity


def initialize_token_files(reader_path: Path, admin_path: Path) -> None:
    reader_path = Path(os.path.abspath(Path(reader_path)))
    admin_path = Path(os.path.abspath(Path(admin_path)))
    if (os.path.normcase(str(reader_path)) ==
            os.path.normcase(str(admin_path))):
        raise service_contracts.ServiceContractError()
    # token_urlsafe() may legally begin with "-" or "_", while bearer-token
    # validation deliberately requires an alphanumeric first byte.  A fixed,
    # role-specific prefix keeps every generated token valid and distinct
    # without reducing the random payload's entropy.
    reader_token = "r_" + secrets.token_urlsafe(48)
    admin_token = "a_" + secrets.token_urlsafe(48)
    reader_identity = None
    try:
        reader_identity = _create_token_file(reader_path, reader_token)
        _create_token_file(admin_path, admin_token)
    except BaseException:
        if reader_identity is not None:
            try:
                _cleanup_created_token(reader_path, reader_identity)
            except BaseException as cleanup_error:
                raise service_contracts.ServiceContractError() from cleanup_error
        raise


def _loopback_host(value: str) -> str:
    if value not in _LOOPBACK_HOSTS:
        raise argparse.ArgumentTypeError(
            "host must be the literal loopback address 127.0.0.1 or ::1")
    if not ipaddress.ip_address(value).is_loopback:
        raise argparse.ArgumentTypeError("host must be loopback")
    return value


def _port(value: str) -> int:
    try:
        return _bounded_decimal(value, minimum=1, maximum=65535)
    except service_contracts.ServiceContractError as exc:
        raise argparse.ArgumentTypeError(
            "port must be between 1 and 65535") from exc


def _positive(value: str) -> float:
    try:
        normalized = float(value)
        return service_contracts.positive_finite(normalized)
    except (ValueError, service_contracts.ServiceContractError) as exc:
        raise argparse.ArgumentTypeError(
            "value must be a finite positive number") from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Authenticated loopback-only RAG service",
        allow_abbrev=False)
    actions = parser.add_subparsers(dest="action", required=True)
    initialize = actions.add_parser(
        "init-tokens", help="Create distinct owner-only reader/admin tokens",
        allow_abbrev=False)
    initialize.add_argument("--reader-token-file", type=Path, required=True)
    initialize.add_argument("--admin-token-file", type=Path, required=True)

    serve = actions.add_parser(
        "serve", help="Run one local ASGI service", allow_abbrev=False)
    serve.add_argument("--config", type=Path, required=True)
    serve.add_argument("--reader-token-file", type=Path, required=True)
    serve.add_argument("--admin-token-file", type=Path, required=True)
    serve.add_argument("--host", type=_loopback_host, default=DEFAULT_HOST)
    serve.add_argument("--port", type=_port, default=DEFAULT_PORT)
    serve.add_argument("--job-root", type=Path, default=None)
    serve.add_argument("--working-directory", type=Path, default=Path.cwd())
    serve.add_argument("--output-root", type=Path, default=Path("output"))
    serve.add_argument("--service-state-root", type=Path, default=None)
    serve.add_argument("--ready-timeout", type=_positive, default=10.0)
    serve.add_argument(
        "--reconcile-interval", type=_positive,
        default=DEFAULT_RECONCILE_INTERVAL_SECONDS)
    serve.add_argument("--max-concurrent-searches", type=int, default=2)
    serve.add_argument(
        "--security-profile", choices=["release", "development"],
        default="release")
    serve.add_argument(
        "--release-security-policy-version", type=int,
        default=release_security.RELEASE_SECURITY_POLICY_VERSION,
        help=argparse.SUPPRESS)
    serve.add_argument(
        "--network-policy", choices=["local-only", "allow-cloud"],
        default="local-only")
    serve.add_argument(
        "--model-download-policy",
        choices=["cache-only", "allow-reviewed-sync"],
        default="cache-only")
    serve.add_argument("--llm-cache-namespace", default="")
    serve.add_argument(
        "--trust-environment-network", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.action == "init-tokens":
            initialize_token_files(
                args.reader_token_file, args.admin_token_file)
            print("Created owner-only reader and admin token files.")
            return 0
        registry = service_runtime.load_corpus_registry(args.config)
        security_policy = release_security.ReleaseSecurityPolicy.from_values(
            profile=args.security_profile,
            network_policy=args.network_policy,
            model_download_policy=args.model_download_policy,
            cache_namespace=args.llm_cache_namespace,
            trust_environment_network=args.trust_environment_network,
            schema_version=args.release_security_policy_version,
        )
        credentials = ServiceCredentials(
            load_token_file(args.reader_token_file),
            load_token_file(args.admin_token_file),
        )
        app = application_composition.create_service_application(
            corpora=registry,
            credentials=credentials,
            job_root=args.job_root,
            working_directory=args.working_directory,
            output_root=args.output_root,
            service_state_root=args.service_state_root,
            ready_timeout_seconds=args.ready_timeout,
            max_concurrent_searches=args.max_concurrent_searches,
            security_policy=security_policy,
            host=args.host,
            reconcile_interval_seconds=args.reconcile_interval,
        )
    except (OSError, service_contracts.ServiceContractError,
            service_runtime.ServiceRuntimeError,
            storage_policy.StoragePolicyError,
            release_security.ReleaseSecurityError) as exc:
        parser.error(f"service configuration failed ({type(exc).__name__})")
    try:
        import uvicorn
    except ImportError as exc:
        parser.error("uvicorn is required by the service dependency profile")
        raise AssertionError from exc
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        workers=1,
        access_log=False,
        proxy_headers=False,
        server_header=False,
        timeout_keep_alive=5,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
