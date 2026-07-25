from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from pathlib import Path
import subprocess
import sys

import pytest

import job_application
import job_coordination
import job_coordination_contracts
import job_runtime


def test_default_binding_is_one_frozen_exact_generation():
    binding = job_application.default_job_application_binding()

    assert binding is job_application.default_job_application_binding()
    assert binding.job_store_factory is job_runtime.JobStore
    assert binding.launch_detached is job_coordination.launch_detached
    assert binding.reconcile_job is job_coordination.reconcile_job
    assert binding.reconcile_all_jobs is job_coordination.reconcile_all_jobs
    assert binding.manager_error_type is (
        job_coordination_contracts.JobManagerError)
    assert not hasattr(binding, "__dict__")
    with pytest.raises(FrozenInstanceError):
        binding.reconcile_job = lambda *_args, **_kwargs: None


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("job_store_factory", None, "job_store_factory"),
        ("launch_detached", None, "launch_detached"),
        ("reconcile_job", None, "reconcile_job"),
        ("reconcile_all_jobs", None, "reconcile_all_jobs"),
        (
            "manager_error_type",
            job_coordination_contracts.JobManagerError("instance"),
            "JobManagerError type",
        ),
        ("manager_error_type", RuntimeError, "JobManagerError type"),
        ("manager_error_type", KeyboardInterrupt, "JobManagerError type"),
    ],
)
def test_binding_rejects_every_invalid_capability(field, value, message):
    binding = job_application.default_job_application_binding()

    with pytest.raises(TypeError, match=message):
        replace(binding, **{field: value})


def test_binding_accepts_manager_error_subclasses():
    class SpecializedManagerError(
            job_coordination_contracts.JobManagerError):
        pass

    binding = replace(
        job_application.default_job_application_binding(),
        manager_error_type=SpecializedManagerError,
    )

    assert binding.manager_error_type is SpecializedManagerError


def test_isolated_import_does_not_load_outward_application_modules():
    project_root = Path(__file__).resolve().parents[1]
    forbidden = (
        "application_composition",
        "job_manager",
        "rag",
        "service_api",
        "service_runtime",
        "service_search_worker",
        "ui",
    )
    source = (
        "import sys; "
        f"sys.path.insert(0, {str(project_root)!r}); "
        "import job_application; "
        f"forbidden = {forbidden!r}; "
        "assert not [name for name in sys.modules "
        "if any(name == root or name.startswith(root + '.') "
        "for root in forbidden)]"
    )

    completed = subprocess.run(
        [sys.executable, "-I", "-c", source],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
