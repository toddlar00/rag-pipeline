"""Concurrency regressions for local vector-store leases."""

from concurrent.futures import ThreadPoolExecutor
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import textwrap
import threading
import time

import pytest

import rag
import retention


_PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_vector_store_lock_key_is_canonical_and_path_wide(
        monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    relative = Path("db")
    absolute = tmp_path / "db"

    assert rag._canonical_vector_store_key(relative) == (
        rag._canonical_vector_store_key(absolute))
    assert rag._vector_store_lock_path(relative) == (
        rag._vector_store_lock_path(absolute))
    assert rag._vector_store_lock_path(absolute).parent == (
        tmp_path / ".rag-locks")

    chroma = rag._vector_store_lock(
        relative, backend="chroma", collection_name="first",
        operation="test", timeout=0)
    qdrant = rag._vector_store_lock(
        absolute, backend="qdrant", collection_name="second",
        operation="test", timeout=0)
    assert chroma.key == qdrant.key
    assert chroma.lock_path == qdrant.lock_path


def test_owned_run_keeps_vector_lock_outside_deletable_root(tmp_path):
    output_root = tmp_path / "output"
    run_root = output_root / "book"
    db_root = run_root / "book_chroma"
    retention.ensure_pipeline_run_manifest(
        output_root,
        run_root,
        job_scope="book",
        owned_siblings=[],
        vector_stores=[{
            "backend": "chroma",
            "collection": "book",
            "path": db_root,
        }],
    )

    assert rag._vector_store_lock_path(db_root).parent == (
        output_root / ".rag-locks")


@pytest.mark.parametrize("timeout", [True, False, None, -1, float("inf"),
                                     float("nan"), "not-a-number"])
def test_vector_store_lock_rejects_invalid_timeout(tmp_path, timeout):
    with pytest.raises(ValueError, match="finite non-negative"):
        rag._vector_store_lock(
            tmp_path / "db", backend="chroma", collection_name="book",
            operation="test", timeout=timeout)


def test_vector_store_lock_rejects_timeout_above_threading_limit():
    assert rag._normalize_db_lock_timeout(threading.TIMEOUT_MAX) == (
        threading.TIMEOUT_MAX)
    too_large = math.nextafter(threading.TIMEOUT_MAX, math.inf)
    with pytest.raises(ValueError, match="no greater"):
        rag._normalize_db_lock_timeout(too_large)


@pytest.mark.skipif(os.name != "nt", reason="Windows path spelling regression")
def test_windows_extended_paths_share_key_and_preserve_lock_path_casing(
        tmp_path):
    mixed_parent = tmp_path / "MixedCaseParent"
    mixed_parent.mkdir()
    ordinary = mixed_parent / "VectorDB"
    extended = Path("\\\\?\\" + str(ordinary))

    assert rag._canonical_vector_store_key(extended) == (
        rag._canonical_vector_store_key(ordinary))
    assert rag._vector_store_lock_path(extended) == (
        rag._vector_store_lock_path(ordinary))
    assert rag._vector_store_lock_path(ordinary).parent == (
        mixed_parent / ".rag-locks")
    assert rag._strip_windows_extended_path_prefix(
        "\\\\?\\UNC\\server\\share\\db") == "\\\\server\\share\\db"
    assert rag._strip_windows_extended_path_prefix(
        "\\\\?\\unc\\server\\share\\db") == "\\\\server\\share\\db"
    assert rag._strip_windows_extended_path_prefix(
        "\\\\?\\GLOBALROOT\\Device") == "\\\\?\\GLOBALROOT\\Device"


def test_vector_store_lock_is_reentrant_without_relocking_os(
        monkeypatch, tmp_path):
    lock_attempts = 0
    unlocks = 0
    try_lock = rag._try_vector_file_lock
    unlock = rag._unlock_vector_file

    def track_lock(handle):
        nonlocal lock_attempts
        lock_attempts += 1
        return try_lock(handle)

    def track_unlock(handle):
        nonlocal unlocks
        unlocks += 1
        return unlock(handle)

    monkeypatch.setattr(rag, "_try_vector_file_lock", track_lock)
    monkeypatch.setattr(rag, "_unlock_vector_file", track_unlock)
    db_path = tmp_path / "db"

    with rag._vector_store_lock(
            db_path, backend="chroma", collection_name="first",
            operation="outer test", timeout=1):
        with rag._vector_store_lock(
                db_path.resolve(), backend="qdrant",
                collection_name="second", operation="nested test",
                timeout=0):
            assert lock_attempts == 1
            assert unlocks == 0

    assert lock_attempts == 1
    assert unlocks == 1


def test_vector_store_lock_bounds_thread_contention_and_isolates_paths(
        tmp_path):
    held_path = tmp_path / "held"
    other_path = tmp_path / "other"

    def contend():
        started = time.monotonic()
        with pytest.raises(rag.VectorStoreBusyError) as raised:
            with rag._vector_store_lock(
                    held_path, backend="chroma", collection_name="other",
                    operation="thread contender", timeout=0.05):
                pytest.fail("contender entered a held database")
        return time.monotonic() - started, str(raised.value)

    def use_other_path():
        with rag._vector_store_lock(
                other_path, backend="qdrant", collection_name="book",
                operation="independent test", timeout=0.1):
            return True

    with rag._vector_store_lock(
            held_path, backend="chroma", collection_name="book",
            operation="holder", timeout=1):
        with ThreadPoolExecutor(max_workers=2) as pool:
            elapsed, message = pool.submit(contend).result(timeout=2)
            assert pool.submit(use_other_path).result(timeout=2) is True

    assert 0.025 <= elapsed < 0.5
    assert "thread contender" in message
    assert "--db-lock-timeout" in message


def test_vector_store_lock_preserves_body_error_when_unlock_and_logging_fail(
        monkeypatch, tmp_path):
    primary_error = ValueError("primary body failure")

    def fail_unlock(_handle):
        raise OSError("secondary unlock failure")

    def fail_logging(*_args, **_kwargs):
        raise OSError("logging handler failure")

    monkeypatch.setattr(rag, "_unlock_vector_file", fail_unlock)
    monkeypatch.setattr(rag.log, "error", fail_logging)

    with pytest.raises(ValueError) as raised:
        with rag._vector_store_lock(
                tmp_path / "db", backend="chroma",
                collection_name="book", operation="failure test",
                timeout=1):
            raise primary_error

    assert raised.value is primary_error


def test_vector_store_lock_propagates_unlock_error_after_success(
        monkeypatch, tmp_path):
    unlock_error = OSError("unlock failure")

    def fail_unlock(_handle):
        raise unlock_error

    monkeypatch.setattr(rag, "_unlock_vector_file", fail_unlock)

    with pytest.raises(OSError) as raised:
        with rag._vector_store_lock(
                tmp_path / "db", backend="qdrant",
                collection_name="book", operation="failure test",
                timeout=1):
            pass

    assert raised.value is unlock_error


_HOLDER_SCRIPT = textwrap.dedent(
    """
    from pathlib import Path
    import sys

    import rag

    db_path = Path(sys.argv[1])
    with rag._vector_store_lock(
            db_path, backend="chroma", collection_name="holder",
            operation="subprocess holder", timeout=2):
        print("ACQUIRED", flush=True)
        sys.stdin.readline()
    """
)


def test_vector_store_lock_is_process_safe_and_crash_released(tmp_path):
    db_path = tmp_path / "db"
    other_path = tmp_path / "other"
    holder = subprocess.Popen(
        [sys.executable, "-c", _HOLDER_SCRIPT, str(db_path)],
        cwd=_PROJECT_ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        with ThreadPoolExecutor(max_workers=1) as pool:
            ready = pool.submit(holder.stdout.readline).result(timeout=10)
        assert ready.strip() == "ACQUIRED", (
            holder.stderr.read() if holder.poll() is not None else ready)

        with pytest.raises(rag.VectorStoreBusyError):
            with rag._vector_store_lock(
                    db_path, backend="qdrant", collection_name="different",
                    operation="process contender", timeout=0.1):
                pytest.fail("cross-process contender acquired a held path")

        with rag._vector_store_lock(
                other_path, backend="qdrant", collection_name="book",
                operation="independent process test", timeout=2):
            pass

        holder.kill()
        holder.wait(timeout=5)

        # The kernel releases a killed process's lease. The persistent sidecar
        # remains but is never itself interpreted as lock ownership.
        with rag._vector_store_lock(
                db_path, backend="chroma", collection_name="book",
                operation="crash recovery", timeout=2) as recovered:
            assert recovered.lock_path.is_file()
    finally:
        if holder.poll() is None:
            holder.kill()
            holder.wait(timeout=5)
        if holder.stdin is not None:
            holder.stdin.close()
        if holder.stdout is not None:
            holder.stdout.close()
        if holder.stderr is not None:
            holder.stderr.close()


def test_collection_count_times_out_before_client_construction(
        monkeypatch, tmp_path):
    db_path = tmp_path / "db"
    db_path.mkdir()
    constructed = []

    class FailClient:
        def __init__(self, path):
            constructed.append(path)

    # Use a different thread so the in-process lease is contention rather
    # than the deliberate same-thread reentrant case.
    import types
    chromadb = types.ModuleType("chromadb")
    chromadb.PersistentClient = FailClient
    monkeypatch.setitem(sys.modules, "chromadb", chromadb)

    def inspect():
        return rag._index_collection_count(
            db_path, "book", db_backend="chroma", lock_timeout=0.05)

    with rag._vector_store_lock(
            db_path, backend="qdrant", collection_name="other",
            operation="holder", timeout=1):
        with ThreadPoolExecutor(max_workers=1) as pool:
            with pytest.raises(rag.VectorStoreBusyError):
                pool.submit(inspect).result(timeout=2)

    assert constructed == []


@pytest.mark.parametrize("backend", ["chroma", "qdrant"])
def test_collection_count_refuses_dirty_marker_before_client_construction(
        monkeypatch, tmp_path, backend):
    db_path = tmp_path / "db"
    db_path.mkdir()
    constructed = []

    class FailClient:
        def __init__(self, *args, **kwargs):
            constructed.append((args, kwargs))
            pytest.fail("dirty collection inspection constructed a client")

    if backend == "chroma":
        import types
        module = types.ModuleType("chromadb")
        module.PersistentClient = FailClient
        monkeypatch.setitem(sys.modules, "chromadb", module)
    else:
        import types
        module = types.ModuleType("qdrant_client")
        module.QdrantClient = FailClient
        monkeypatch.setitem(sys.modules, "qdrant_client", module)

    marker_path = rag._begin_index_update(
        db_path, backend=backend, collection_name="book",
        source_sha256="pending", source_record_count=1,
        owner_token="interrupted-update")
    assert json.loads(marker_path.read_text(encoding="utf-8"))[
        "owner_token"] == "interrupted-update"

    with pytest.raises(ValueError, match="Index update is incomplete"):
        rag._index_collection_count(
            db_path, "book", db_backend=backend, lock_timeout=0)
    assert constructed == []


def test_after_fork_reset_discards_inherited_reranker_state(monkeypatch):
    inherited_lock = threading.Lock()
    inherited_lock.acquire()
    monkeypatch.setattr(rag, "_reranker_lock", inherited_lock)
    monkeypatch.setattr(rag, "_reranker_instances", {"model": object()})

    rag._reset_vector_store_locks_after_fork()

    assert rag._reranker_instances == {}
    assert rag._reranker_lock is not inherited_lock
    assert rag._reranker_lock.acquire(blocking=False) is True
    rag._reranker_lock.release()
