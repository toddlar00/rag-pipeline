"""Lazy outer composition for executable application roles.

Importing this module is intentionally standard-library-only.  Concrete
service and HTTP modules are resolved only when the service role is requested,
so future CLI roles can share this root without eagerly requiring FastAPI or
loading pipeline implementations.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any


ApplicationFactory = Callable[..., object]
_UNSET = object()
_DEFAULT_COMPOSITION_LOCK = threading.Lock()
_DEFAULT_SERVICE_APPLICATION_COMPOSITION = None


@dataclass(frozen=True, slots=True)
class ServiceApplicationComposition:
    """One atomic generation of concrete service application capabilities."""

    runtime_factory: ApplicationFactory
    http_factory: ApplicationFactory
    runtime_binding: object
    job_coordination_binding: object
    http_binding: object

    def __post_init__(self) -> None:
        if not callable(self.runtime_factory):
            raise TypeError("runtime_factory must be callable")
        if not callable(self.http_factory):
            raise TypeError("http_factory must be callable")
        for name in (
                "runtime_binding", "job_coordination_binding",
                "http_binding"):
            if getattr(self, name) is None:
                raise TypeError(f"{name} must not be None")


def _build_default_service_application_composition(
        ) -> ServiceApplicationComposition:
    """Build the service role without making service extras eager imports."""
    import job_coordination
    import service_http
    import service_runtime
    import service_runtime_binding

    return ServiceApplicationComposition(
        runtime_factory=service_runtime.RagApplicationService,
        http_factory=service_http.create_app,
        runtime_binding=(
            service_runtime_binding.default_service_runtime_binding()),
        job_coordination_binding=(
            job_coordination.default_service_job_coordination_binding()),
        http_binding=service_http.ServiceHttpBinding(
            runtime_error_type=service_runtime.ServiceRuntimeError,
            default_job_page_limit=service_runtime.DEFAULT_JOB_PAGE_LIMIT,
            max_job_page_limit=service_runtime.MAX_JOB_PAGE_LIMIT,
        ),
    )


def default_service_application_composition(
        ) -> ServiceApplicationComposition:
    """Return one lazily constructed, process-local production generation."""
    global _DEFAULT_SERVICE_APPLICATION_COMPOSITION
    composition = _DEFAULT_SERVICE_APPLICATION_COMPOSITION
    if composition is not None:
        return composition
    with _DEFAULT_COMPOSITION_LOCK:
        composition = _DEFAULT_SERVICE_APPLICATION_COMPOSITION
        if composition is None:
            composition = _build_default_service_application_composition()
            _DEFAULT_SERVICE_APPLICATION_COMPOSITION = composition
    return composition


def _resolve_composition(
        composition: ServiceApplicationComposition | None,
) -> ServiceApplicationComposition:
    selected = (
        default_service_application_composition()
        if composition is None else composition)
    if not isinstance(selected, ServiceApplicationComposition):
        raise TypeError(
            "composition must be a ServiceApplicationComposition")
    return selected


def create_service_runtime(
        corpora: Mapping[str, Any], *,
        composition: ServiceApplicationComposition | None = None,
        job_root=None,
        working_directory=None,
        output_root=None,
        service_state_root=None,
        ready_timeout_seconds: float = 10.0,
        max_concurrent_searches: int = 2,
        security_policy=None,
        runtime_binding=None,
        job_coordination_binding=None,
        search_runner=_UNSET,
        launcher=_UNSET,
) -> object:
    """Construct one runtime from a single complete composition snapshot."""
    selected = _resolve_composition(composition)
    options = {
        "job_root": job_root,
        "working_directory": working_directory,
        "output_root": output_root,
        "service_state_root": service_state_root,
        "ready_timeout_seconds": ready_timeout_seconds,
        "max_concurrent_searches": max_concurrent_searches,
        "security_policy": security_policy,
        "runtime_binding": (
            selected.runtime_binding
            if runtime_binding is None else runtime_binding),
        "job_coordination_binding": (
            selected.job_coordination_binding
            if job_coordination_binding is None
            else job_coordination_binding),
    }
    if search_runner is not _UNSET:
        options["search_runner"] = search_runner
    if launcher is not _UNSET:
        options["launcher"] = launcher
    return selected.runtime_factory(corpora, **options)


def create_service_http_app(
        runtime: object,
        credentials: object, *,
        composition: ServiceApplicationComposition | None = None,
        http_binding=None,
        host: str = "127.0.0.1",
        reconcile_interval_seconds: float = 2.0,
        max_http_concurrency: int = 64,
        enforce_peer_loopback: bool = True,
) -> object:
    """Wrap an existing runtime with the captured concrete HTTP policy."""
    selected = _resolve_composition(composition)
    return selected.http_factory(
        runtime,
        credentials,
        http_binding=(
            selected.http_binding if http_binding is None else http_binding),
        host=host,
        reconcile_interval_seconds=reconcile_interval_seconds,
        max_http_concurrency=max_http_concurrency,
        enforce_peer_loopback=enforce_peer_loopback,
    )


def create_service_application(
        corpora: Mapping[str, Any],
        credentials: object, *,
        composition: ServiceApplicationComposition | None = None,
        job_root=None,
        working_directory=None,
        output_root=None,
        service_state_root=None,
        ready_timeout_seconds: float = 10.0,
        max_concurrent_searches: int = 2,
        security_policy=None,
        runtime_binding=None,
        job_coordination_binding=None,
        search_runner=_UNSET,
        launcher=_UNSET,
        http_binding=None,
        host: str = "127.0.0.1",
        reconcile_interval_seconds: float = 2.0,
        max_http_concurrency: int = 64,
        enforce_peer_loopback: bool = True,
) -> object:
    """Construct a runtime and its HTTP adapter from one root generation."""
    selected = _resolve_composition(composition)
    runtime = create_service_runtime(
        corpora,
        composition=selected,
        job_root=job_root,
        working_directory=working_directory,
        output_root=output_root,
        service_state_root=service_state_root,
        ready_timeout_seconds=ready_timeout_seconds,
        max_concurrent_searches=max_concurrent_searches,
        security_policy=security_policy,
        runtime_binding=runtime_binding,
        job_coordination_binding=job_coordination_binding,
        search_runner=search_runner,
        launcher=launcher,
    )
    return create_service_http_app(
        runtime,
        credentials,
        composition=selected,
        http_binding=http_binding,
        host=host,
        reconcile_interval_seconds=reconcile_interval_seconds,
        max_http_concurrency=max_http_concurrency,
        enforce_peer_loopback=enforce_peer_loopback,
    )


__all__ = [
    "ApplicationFactory",
    "ServiceApplicationComposition",
    "create_service_application",
    "create_service_http_app",
    "create_service_runtime",
    "default_service_application_composition",
]
