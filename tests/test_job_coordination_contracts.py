from dataclasses import FrozenInstanceError, replace
from pathlib import Path
import json
import pickle
import subprocess
import sys

import pytest

import job_coordination
import job_coordination_contracts
import job_manager
import job_runtime


def test_default_service_job_coordination_binding_is_one_frozen_generation():
    binding = job_coordination.default_service_job_coordination_binding()

    assert binding is job_coordination.default_service_job_coordination_binding()
    assert binding.launch_detached is job_coordination.launch_detached
    assert binding.reconcile_job is job_coordination.reconcile_job
    assert binding.corrupt_error_type is job_manager.JobManagerCorruptError
    with pytest.raises(FrozenInstanceError):
        binding.reconcile_job = lambda *_args, **_kwargs: None


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"launch_detached": None}, "launch_detached"),
        ({"reconcile_job": None}, "reconcile_job"),
        ({"corrupt_error_type": RuntimeError("instance")},
         "JobManagerCorruptError type"),
        ({"corrupt_error_type": RuntimeError},
         "JobManagerCorruptError type"),
        ({"corrupt_error_type": KeyboardInterrupt},
         "JobManagerCorruptError type"),
    ],
)
def test_service_job_coordination_binding_rejects_invalid_capabilities(
        changes, message):
    binding = job_coordination.default_service_job_coordination_binding()

    with pytest.raises(TypeError, match=message):
        replace(binding, **changes)


def test_legacy_manager_facade_preserves_public_contract_identity_and_pickle():
    public_contracts = (
        "JobManagerError",
        "JobManagerValidationError",
        "JobManagerCorruptError",
        "JobManagerLaunchError",
        "ManagerResult",
        "LaunchResult",
    )
    for name in public_contracts:
        assert getattr(job_manager, name) is getattr(
            job_coordination_contracts, name)
    assert job_manager.RuntimeMetadata is job_coordination.RuntimeMetadata
    assert job_manager.RuntimeMetadata.__module__ == "job_manager"

    result = job_manager.LaunchResult(
        job_id="j-1234567890123456",
        status="starting",
        attempt_number=1,
        ready=True,
    )
    assert pickle.loads(pickle.dumps(result)) == result
    assert result.as_dict() == {
        "schema_version": job_manager.MANAGER_SCHEMA_VERSION,
        "job_id": "j-1234567890123456",
        "status": "starting",
        "attempt_number": 1,
        "ready": True,
    }


@pytest.mark.parametrize(
    "value",
    [
        job_manager.JobManagerError("manager"),
        job_manager.JobManagerValidationError("validation"),
        job_manager.JobManagerCorruptError("corrupt"),
        job_manager.JobManagerLaunchError("launch"),
        job_manager.ManagerResult(
            "j-1234567890123456", "failed", 1, 1, True,
            "worker_failed"),
        job_manager.LaunchResult(
            "j-1234567890123456", "starting", 1, True),
        job_manager.RuntimeMetadata(
            job_id="j-1234567890123456",
            attempt_number=1,
            attempt_token_sha256="0" * 64,
            run_id="run-1",
            phase="running",
            job_status="running",
            manager_pid=123,
            manager_birth="birth",
            worker_pid=124,
            worker_birth="worker-birth",
            heartbeat_at=1.0,
            cleanup_confirmed=None,
            exit_code=None,
            updated_at=2.0,
        ),
    ],
)
def test_every_relocated_public_type_round_trips_through_legacy_pickle(value):
    restored = pickle.loads(pickle.dumps(value))

    assert type(restored) is type(value)
    if isinstance(value, Exception):
        assert restored.args == value.args
    else:
        assert restored == value


def test_legacy_manager_facade_points_to_engine_and_stable_child_script():
    assert job_manager.launch_detached is job_coordination.launch_detached
    assert job_manager.reconcile_job is job_coordination.reconcile_job
    assert job_manager.run_job is job_coordination.run_job
    assert job_coordination.MANAGER_SCRIPT_PATH.name == "job_manager.py"
    assert job_coordination.MANAGER_SCRIPT_PATH.resolve() == (
        Path(job_manager.__file__).resolve())


def test_legacy_manager_reconcile_cli_executes_facade(tmp_path):
    store = job_runtime.JobStore(tmp_path / "jobs")
    submitted = store.submit_job("export", ["--chunks", "private.jsonl"])

    completed = subprocess.run(
        [
            sys.executable,
            str(Path(job_manager.__file__).resolve()),
            "reconcile",
            "--root", str(store.root),
            "--job-id", submitted.job_id,
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 1
    assert completed.stderr == ""
    payload = json.loads(completed.stdout)
    assert payload == submitted.as_dict()
    assert "private.jsonl" not in completed.stdout
