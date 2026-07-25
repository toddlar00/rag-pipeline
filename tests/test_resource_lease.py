"""Shared-runtime regressions for dependency-light filesystem leases."""

import rag
import resource_lease


def test_resource_and_rag_leases_share_one_reentrant_state(
        monkeypatch, tmp_path):
    attempts = []
    unlocks = []
    native_try = resource_lease.try_path_file_lock
    native_unlock = resource_lease.unlock_path_file

    def track_try(handle):
        attempts.append(handle)
        return native_try(handle)

    def track_unlock(handle):
        unlocks.append(handle)
        return native_unlock(handle)

    monkeypatch.setattr(resource_lease, "try_path_file_lock", track_try)
    monkeypatch.setattr(resource_lease, "unlock_path_file", track_unlock)
    path = tmp_path / "shared"
    direct = resource_lease.PathLease(
        path,
        backend="service",
        collection_name="service-instance",
        operation="service startup",
        timeout=0,
    )
    compatible = rag._VectorStoreLease(
        path,
        backend="qdrant",
        collection_name="book",
        operation="nested query",
        timeout=0,
    )

    assert direct.state is compatible.state
    with direct:
        with compatible:
            assert compatible._reentrant is True
            assert len(attempts) == 1
            assert unlocks == []

    assert len(attempts) == 1
    assert len(unlocks) == 1
