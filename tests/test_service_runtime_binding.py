from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

import embedding_policy
import process_supervision
import resource_lease
import runtime_supervision
import service_runtime_binding


def test_default_binding_is_frozen_and_uses_exact_host_capabilities():
    binding = service_runtime_binding.default_service_runtime_binding()

    assert binding is (
        service_runtime_binding.default_service_runtime_binding())
    assert binding.worker_script_path == Path(
        service_runtime_binding.__file__).with_name(
            "service_search_worker.py").resolve()
    assert binding.supervisor is runtime_supervision.run_cli_with_deadline
    assert binding.cleanup_error_type is (
        process_supervision._SupervisorCleanupError)
    assert binding.instance_lease_factory is (
        service_runtime_binding._service_instance_lease)
    assert binding.is_api_embedding_model is (
        embedding_policy.is_api_embedding_model)
    with pytest.raises(FrozenInstanceError):
        binding.worker_script_path = Path("replacement.py")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("supervisor", None),
        ("cleanup_error_type", RuntimeError("instance")),
        ("cleanup_error_type", KeyboardInterrupt),
        ("instance_lease_factory", None),
        ("is_api_embedding_model", None),
    ],
)
def test_binding_rejects_invalid_capabilities(field, value):
    values = {
        "worker_script_path": "worker.py",
        "supervisor": lambda *_args, **_kwargs: 0,
        "cleanup_error_type": RuntimeError,
        "instance_lease_factory": lambda _path: object(),
        "is_api_embedding_model": lambda _model: False,
    }
    values[field] = value

    with pytest.raises(TypeError):
        service_runtime_binding.ServiceRuntimeBinding(**values)


def test_binding_normalizes_worker_path():
    binding = service_runtime_binding.ServiceRuntimeBinding(
        worker_script_path="worker.py",
        supervisor=lambda *_args, **_kwargs: 0,
        cleanup_error_type=RuntimeError,
        instance_lease_factory=lambda _path: object(),
        is_api_embedding_model=lambda _model: False,
    )

    assert binding.worker_script_path == Path("worker.py")


def test_service_instance_factory_binds_exact_path_lease_policy(
        monkeypatch, tmp_path):
    captured = {}
    lease = object()

    def path_lease(path, **kwargs):
        captured.update(path=path, kwargs=kwargs)
        return lease

    monkeypatch.setattr(resource_lease, "PathLease", path_lease)

    binding = service_runtime_binding.default_service_runtime_binding()
    assert binding.instance_lease_factory(tmp_path / "instance") is lease
    assert captured == {
        "path": tmp_path / "instance",
        "kwargs": {
            "backend": "service",
            "collection_name": "service-instance",
            "operation": "local service startup",
            "timeout": 0,
            "resource_description": "local service instance",
            "timeout_option": "service instance lease",
        },
    }
