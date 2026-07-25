"""Dependency-light, bounded leases for sensitive local filesystem paths.

The lease identity is the canonical resource path, not a backend or logical
collection name.  All callers in this process therefore share one reentrant
thread gate and one operating-system lock for the same path.  A persistent
``.rag-locks`` sidecar supplies a stable inode; its presence alone never
represents ownership.
"""

from __future__ import annotations

import errno
import hashlib
import logging
import math
import os
import re
import stat
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import retention
import storage_policy


log = logging.getLogger(__name__)


class PathLeaseBusyError(TimeoutError):
    """Raised when another operation retains an exclusive path lease."""


CleanupErrorFn = Callable[..., None]
LockIdentityFn = Callable[[Path], tuple[Path, str]]
LockDirectoryFn = Callable[[Path], Path]
LockStateFn = Callable[[str], "PathLockState"]
TryFileLockFn = Callable[[Any], bool]
UnlockFileFn = Callable[[Any], None]


@dataclass
class PathLockState:
    """One reentrant in-process gate for a canonical resource path."""

    thread_lock: Any = field(default_factory=threading.RLock)
    handle: Any = None


_path_lock_states: dict[str, PathLockState] = {}
_path_lock_states_guard = threading.Lock()
_path_lock_local = threading.local()


def _shared_path_lock_registry(
        ) -> tuple[dict[str, PathLockState], Any, Any]:
    """Expose the shared registry objects to compatibility facades."""
    return _path_lock_states, _path_lock_states_guard, _path_lock_local


def reset_path_leases_after_fork() -> None:
    """Drop inherited handles and synchronization state in a forked child."""
    global _path_lock_states, _path_lock_states_guard, _path_lock_local
    for state in _path_lock_states.values():
        if state.handle is not None:
            try:
                # Close only: explicitly unlocking an inherited POSIX flock
                # could release the parent's shared open-file-description
                # lock.
                state.handle.close()
            except BaseException:
                pass
    _path_lock_states = {}
    _path_lock_states_guard = threading.Lock()
    _path_lock_local = threading.local()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=reset_path_leases_after_fork)


def normalize_lock_timeout(timeout: float) -> float:
    """Validate a finite, bounded path-lease timeout."""
    if isinstance(timeout, bool):
        raise ValueError("db lock timeout must be a finite non-negative number")
    try:
        value = float(timeout)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "db lock timeout must be a finite non-negative number") from exc
    if (not math.isfinite(value) or value < 0
            or value > threading.TIMEOUT_MAX):
        raise ValueError(
            "db lock timeout must be a finite non-negative number no greater "
            f"than {threading.TIMEOUT_MAX:g} seconds")
    return value


def strip_windows_extended_path_prefix(value: str) -> str:
    """Normalize Win32 extended paths to their ordinary drive/UNC spelling."""
    if os.name != "nt":
        return value
    if value.casefold().startswith("\\\\?\\unc\\"):
        return "\\\\" + value[8:]
    if re.match(r"^\\\\\?\\[A-Za-z]:[\\/]", value):
        return value[4:]
    return value


def resolved_path(resource_path: Path) -> Path:
    """Resolve one resource path while retaining its filesystem casing."""
    path = Path(strip_windows_extended_path_prefix(str(Path(resource_path))))
    try:
        resolved = path.resolve(strict=False)
    except (OSError, RuntimeError):
        resolved = Path(os.path.abspath(path))
    return Path(strip_windows_extended_path_prefix(str(resolved)))


def path_lock_identity(resource_path: Path) -> tuple[Path, str]:
    """Return the filesystem path and normalized comparison key together."""
    resolved = resolved_path(resource_path)
    return resolved, os.path.normcase(str(resolved))


def canonical_path_key(resource_path: Path) -> str:
    """Return one platform-normalized identity for a resource path."""
    return path_lock_identity(resource_path)[1]


def path_lock_directory(resolved: Path) -> Path:
    """Keep owned-run sentinels outside the deletable run directory."""
    run_root = resolved.parent
    output_root = run_root.parent
    marker = run_root / retention.RUN_MANIFEST_NAME
    if marker.exists() or storage_policy.path_is_link_like(marker):
        _, manifest = retention.load_pipeline_run_manifest(
            output_root, run_root.name)
        relative = resolved.relative_to(output_root).as_posix()
        if any(record["path"] == relative
               for record in manifest["vector_stores"]):
            return output_root / ".rag-locks"
    return run_root / ".rag-locks"


def _lock_path_from_identity(
        resolved: Path, key: str, *,
        lock_directory_fn: LockDirectoryFn | None = None) -> Path:
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", resolved.name)
    safe_name = safe_name.strip("._")[:40] or "vector-store"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:20]
    directory = (
        path_lock_directory(resolved)
        if lock_directory_fn is None else lock_directory_fn(resolved))
    return directory / f"{safe_name}-{digest}.lock"


def path_lock_path(resource_path: Path) -> Path:
    """Return the persistent sidecar used only as an OS-locking inode."""
    resolved, key = path_lock_identity(resource_path)
    return _lock_path_from_identity(resolved, key)


def path_lock_state(key: str) -> PathLockState:
    """Return the process-wide reentrant state for one canonical path."""
    with _path_lock_states_guard:
        return _path_lock_states.setdefault(key, PathLockState())


def try_path_file_lock(handle) -> bool:
    """Attempt one non-blocking exclusive OS lock of the sentinel's byte 0."""
    handle.seek(0)
    if os.name == "nt":
        import msvcrt
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            if (exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}
                    or getattr(exc, "winerror", None) in {33, 36, 158}):
                return False
            raise
        return True

    import fcntl
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        if exc.errno in {errno.EACCES, errno.EAGAIN}:
            return False
        raise
    return True


def unlock_path_file(handle) -> None:
    """Release the platform OS lock held by *handle*."""
    handle.seek(0)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl
    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _log_cleanup_error(message: str, *args,
                       error: BaseException) -> None:
    """Best-effort cleanup diagnostics that cannot alter error precedence."""
    try:
        log.error(
            message,
            *args,
            exc_info=(type(error), error, error.__traceback__),
        )
    except BaseException:
        pass


def finish_path_file_lock(
        handle, *, primary_error: BaseException | None,
        lock_path: Path,
        unlock_file_fn: UnlockFileFn | None = None,
        cleanup_error_fn: CleanupErrorFn | None = None) -> None:
    """Unlock and close a lease handle without changing error precedence."""
    unlock = unlock_path_file if unlock_file_fn is None else unlock_file_fn
    report_cleanup = (
        _log_cleanup_error
        if cleanup_error_fn is None else cleanup_error_fn)
    cleanup_errors: list[BaseException] = []
    try:
        unlock(handle)
    except BaseException as exc:
        cleanup_errors.append(exc)
    try:
        handle.close()
    except BaseException as exc:
        cleanup_errors.append(exc)

    if not cleanup_errors:
        return
    selected_error = primary_error or cleanup_errors[0]
    for cleanup_error in cleanup_errors:
        if cleanup_error is selected_error:
            continue
        report_cleanup(
            "Vector-store lease cleanup also failed for %s",
            lock_path,
            error=cleanup_error,
        )
    if primary_error is None:
        raise selected_error.with_traceback(selected_error.__traceback__)


class PathLease:
    """Bounded, reentrant, process-safe exclusive filesystem-path lease."""

    def __init__(
            self, resource_path: Path, *, backend: str,
            collection_name: str, operation: str, timeout: float,
            resource_description: str | None = None,
            timeout_option: str = "--db-lock-timeout",
            busy_error_type: type[TimeoutError] = PathLeaseBusyError,
            normalize_timeout_fn: Callable[[float], float] | None = None,
            lock_identity_fn: LockIdentityFn | None = None,
            lock_directory_fn: LockDirectoryFn | None = None,
            lock_state_fn: LockStateFn | None = None,
            try_file_lock_fn: TryFileLockFn | None = None,
            unlock_file_fn: UnlockFileFn | None = None,
            cleanup_error_fn: CleanupErrorFn | None = None,
            monotonic_fn: Callable[[], float] | None = None,
            sleep_fn: Callable[[float], None] | None = None):
        self.db_dir = Path(resource_path)
        self.backend = backend
        self.collection_name = collection_name
        self.operation = operation
        self.resource_description = resource_description or (
            f"vector store ({backend} collection '{collection_name}')")
        self.timeout_option = timeout_option
        self._busy_error_type = busy_error_type
        self._normalize_timeout_fn = normalize_timeout_fn
        self._lock_identity_fn = lock_identity_fn
        self._lock_directory_fn = lock_directory_fn
        self._lock_state_fn = lock_state_fn
        self._try_file_lock_fn = try_file_lock_fn
        self._unlock_file_fn = unlock_file_fn
        self._cleanup_error_fn = cleanup_error_fn
        self._monotonic_fn = monotonic_fn
        self._sleep_fn = sleep_fn
        self.timeout = self._normalize_timeout(timeout)
        resolved, self.key = self._lock_identity(self.db_dir)
        self.lock_path = _lock_path_from_identity(
            resolved, self.key,
            lock_directory_fn=self._lock_directory_fn)
        self.state = self._lock_state(self.key)
        self._process_id = os.getpid()
        self._owner_pid = None
        self._entered = False
        self._reentrant = False
        self._handle = None

    def _normalize_timeout(self, timeout: float) -> float:
        if self._normalize_timeout_fn is None:
            return normalize_lock_timeout(timeout)
        return self._normalize_timeout_fn(timeout)

    def _lock_identity(self, path: Path) -> tuple[Path, str]:
        if self._lock_identity_fn is None:
            return path_lock_identity(path)
        return self._lock_identity_fn(path)

    def _lock_state(self, key: str) -> PathLockState:
        if self._lock_state_fn is None:
            return path_lock_state(key)
        return self._lock_state_fn(key)

    def _try_file_lock(self, handle) -> bool:
        if self._try_file_lock_fn is None:
            return try_path_file_lock(handle)
        return self._try_file_lock_fn(handle)

    def _finish_file_lock(
            self, handle, *, primary_error: BaseException | None) -> None:
        finish_path_file_lock(
            handle,
            primary_error=primary_error,
            lock_path=self.lock_path,
            unlock_file_fn=self._unlock_file_fn,
            cleanup_error_fn=self._cleanup_error_fn,
        )

    def _report_cleanup(
            self, message: str, *args, error: BaseException) -> None:
        reporter = (
            _log_cleanup_error
            if self._cleanup_error_fn is None else self._cleanup_error_fn)
        reporter(message, *args, error=error)

    def _monotonic(self) -> float:
        if self._monotonic_fn is None:
            return time.monotonic()
        return self._monotonic_fn()

    def _sleep(self, seconds: float) -> None:
        if self._sleep_fn is None:
            time.sleep(seconds)
        else:
            self._sleep_fn(seconds)

    def _busy_error(self) -> TimeoutError:
        return self._busy_error_type(
            f"Timed out after {self.timeout:g}s waiting for exclusive "
            f"access during {self.operation}: {self.db_dir} "
            f"[{self.resource_description}]. Another local operation is "
            f"using this resource; retry after it finishes or increase "
            f"{self.timeout_option}."
        )

    def __enter__(self):
        if self._entered:
            raise RuntimeError("Vector-store lease objects cannot be reused")
        current_pid = os.getpid()
        if current_pid != self._process_id:
            # A lease object constructed (but not entered) before fork must use
            # the child's freshly initialized synchronization registry.
            self.state = self._lock_state(self.key)
            self._process_id = current_pid
        deadline = self._monotonic() + self.timeout
        if not self.state.thread_lock.acquire(timeout=self.timeout):
            raise self._busy_error()

        thread_leases = getattr(_path_lock_local, "leases", None)
        if thread_leases is None:
            thread_leases = {}
            _path_lock_local.leases = thread_leases
        if self.key in thread_leases:
            thread_leases[self.key] += 1
            self._entered = True
            self._reentrant = True
            self._owner_pid = current_pid
            return self

        handle = None
        os_locked = False
        try:
            storage_policy.ensure_private_directory(self.lock_path.parent)
            storage_policy.assert_no_link_components(self.lock_path)
            flags = os.O_RDWR | os.O_APPEND | os.O_CREAT
            flags |= getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(
                self.lock_path, flags, storage_policy.PRIVATE_FILE_MODE)
            handle = os.fdopen(descriptor, "a+b")
            lock_stat = os.fstat(handle.fileno())
            if (not stat.S_ISREG(lock_stat.st_mode)
                    or lock_stat.st_nlink != 1):
                raise storage_policy.StoragePolicyError(
                    "vector-store lock must be one regular, unlinked file")
            storage_policy.enforce_private_path(
                self.lock_path, directory=False)
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
                os.fsync(handle.fileno())

            first_attempt = True
            while True:
                remaining = deadline - self._monotonic()
                # Opening, permission-checking, and initially syncing the
                # private sidecar can consume a very small timeout on a slow
                # filesystem. The OS lock attempt is nonblocking, so always
                # make exactly one attempt before treating the deadline as
                # exhausted. This preserves true zero-timeout try-lock
                # semantics without making uncontended paths appear busy.
                if remaining <= 0 and not first_attempt:
                    raise self._busy_error()
                if self._try_file_lock(handle):
                    os_locked = True
                    break
                first_attempt = False
                remaining = deadline - self._monotonic()
                if remaining <= 0:
                    raise self._busy_error()
                self._sleep(min(0.05, remaining))

            thread_leases[self.key] = 1
            self._handle = handle
            self.state.handle = handle
            self._entered = True
            self._owner_pid = current_pid
            return self
        except BaseException as exc:
            thread_leases.pop(self.key, None)
            if self.state.handle is handle:
                self.state.handle = None
            if handle is not None:
                if os_locked:
                    self._finish_file_lock(handle, primary_error=exc)
                else:
                    try:
                        handle.close()
                    except BaseException as close_error:
                        self._report_cleanup(
                            "Vector-store lease handle cleanup failed for %s",
                            self.lock_path,
                            error=close_error,
                        )
            self.state.thread_lock.release()
            raise

    def __exit__(self, exc_type, exc, traceback):
        if not self._entered:
            return False
        if self._owner_pid != os.getpid():
            # ``after_in_child`` already closed the inherited descriptor and
            # replaced the lock registry. Unwinding the parent's context in a
            # forked child must not touch either parent's lock or stale TLS.
            self._entered = False
            self._handle = None
            return False
        thread_leases = _path_lock_local.leases
        cleanup_error = None
        try:
            depth = thread_leases.get(self.key)
            if not isinstance(depth, int) or depth < 1:
                raise RuntimeError("Vector-store lease ownership was lost")
            if depth > 1:
                thread_leases[self.key] = depth - 1
            else:
                thread_leases.pop(self.key)
                if self._handle is None:
                    raise RuntimeError("Vector-store lease handle was lost")
                try:
                    self._finish_file_lock(
                        self._handle, primary_error=exc)
                except BaseException as release_error:
                    cleanup_error = release_error
                finally:
                    self.state.handle = None
        finally:
            self._entered = False
            self.state.thread_lock.release()

        if cleanup_error is not None:
            raise cleanup_error.with_traceback(cleanup_error.__traceback__)
        return False


__all__ = [
    "PathLease",
    "PathLeaseBusyError",
    "PathLockState",
    "canonical_path_key",
    "finish_path_file_lock",
    "normalize_lock_timeout",
    "path_lock_directory",
    "path_lock_identity",
    "path_lock_path",
    "path_lock_state",
    "reset_path_leases_after_fork",
    "resolved_path",
    "strip_windows_extended_path_prefix",
    "try_path_file_lock",
    "unlock_path_file",
]
