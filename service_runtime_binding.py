"""Dependency-light capabilities for the local application service.

The service host resolves this frozen binding at its composition boundary.
That keeps process supervision, cloud-model classification, and the
single-instance path lease explicit without importing the large ``rag``
compatibility facade into the host runtime.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import embedding_policy
import resource_lease
import runtime_supervision


WORKER_SCRIPT_PATH = Path(__file__).with_name(
    "service_search_worker.py").resolve()
SupervisorFn = Callable[..., int]
InstanceLeaseFactory = Callable[[Path], Any]
EmbeddingModelPredicate = Callable[[str], bool]


@dataclass(frozen=True, slots=True)
class ServiceRuntimeBinding:
    """Atomic host capabilities required by the local service runtime."""

    worker_script_path: Path
    supervisor: SupervisorFn
    cleanup_error_type: type[RuntimeError]
    instance_lease_factory: InstanceLeaseFactory
    is_api_embedding_model: EmbeddingModelPredicate

    def __post_init__(self) -> None:
        worker_script_path = Path(self.worker_script_path)
        if not callable(self.supervisor):
            raise TypeError("supervisor must be callable")
        if (not isinstance(self.cleanup_error_type, type)
                or not issubclass(self.cleanup_error_type, RuntimeError)):
            raise TypeError(
                "cleanup_error_type must be a RuntimeError type")
        if not callable(self.instance_lease_factory):
            raise TypeError("instance_lease_factory must be callable")
        if not callable(self.is_api_embedding_model):
            raise TypeError("is_api_embedding_model must be callable")
        object.__setattr__(
            self, "worker_script_path", worker_script_path)


def _service_instance_lease(path: Path) -> resource_lease.PathLease:
    """Create the exclusive lease for one service-state directory."""
    return resource_lease.PathLease(
        path,
        backend="service",
        collection_name="service-instance",
        operation="local service startup",
        timeout=0,
        resource_description="local service instance",
        timeout_option="service instance lease",
    )


_DEFAULT_SERVICE_RUNTIME_BINDING = ServiceRuntimeBinding(
    worker_script_path=WORKER_SCRIPT_PATH,
    supervisor=runtime_supervision.run_cli_with_deadline,
    cleanup_error_type=runtime_supervision.SupervisorCleanupError,
    instance_lease_factory=_service_instance_lease,
    is_api_embedding_model=embedding_policy.is_api_embedding_model,
)


def default_service_runtime_binding() -> ServiceRuntimeBinding:
    """Return the immutable production binding for call-time resolution."""
    return _DEFAULT_SERVICE_RUNTIME_BINDING


__all__ = [
    "EmbeddingModelPredicate",
    "InstanceLeaseFactory",
    "ServiceRuntimeBinding",
    "SupervisorFn",
    "WORKER_SCRIPT_PATH",
    "default_service_runtime_binding",
]
