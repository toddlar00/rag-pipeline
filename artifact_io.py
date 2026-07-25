"""Strict artifact reads and crash-safe publication primitives.

The module is intentionally limited to Python's standard library.  Callers
inject domain identity, cached hashing, logging, and manifest-version policy so
this leaf does not depend on pipeline orchestration or backend state.
"""

from __future__ import annotations

import errno
import hashlib
import json
import math
import os
import re
import shutil
import stat
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from storage_policy import (
    assert_no_link_components,
    atomic_write_private_json,
    atomic_write_private_jsonl,
    atomic_write_private_text,
    enforce_private_path,
    ensure_private_directory,
    jsonl_lines,
    path_is_link_like,
)


ArtifactFingerprint = tuple[int, int, int, int, int]
ArtifactContentSnapshot = tuple[int, int, int, int, bytes]
ChunkIdFn = Callable[[dict], str]
CleanupErrorFn = Callable[..., None]
ReplaceFn = Callable[[Any, Any], Any]
ArtifactHashFn = Callable[[Path], str]
AtomicJsonWriterFn = Callable[[Path, object], None]
_SYNCED_FOLDER_READ_RETRY_DELAYS = (0.01, 0.05, 0.15)
_FILE_STREAM_CHUNK_SIZE = 1024 * 1024
_SNAPSHOT_FREE_SPACE_MARGIN = 64 * 1024 * 1024
_SNAPSHOT_SCRATCH_DIRECTORY = "rag-pipeline-scratch-v1"
_SNAPSHOT_OWNER_MARKER = ".rag-snapshot-owner.json"
_SNAPSHOT_OWNER_SCHEMA_VERSION = 2
_SNAPSHOT_STALE_AGE_SECONDS = 15 * 60
_MAX_SNAPSHOT_OWNER_MARKER_BYTES = 4096


class _ArtifactCtimeChanged(RuntimeError):
    """Internal signal for content-identical synced-folder metadata churn."""

    def __init__(self, snapshot: ArtifactContentSnapshot):
        super().__init__("artifact ctime changed while reading")
        self.snapshot = snapshot


def _windows_process_state(process_id: int) -> tuple[bool, str | None]:
    """Return Windows liveness plus a PID-reuse-resistant birth token."""
    import ctypes
    from ctypes import wintypes

    process_query_limited_information = 0x1000
    still_active = 259
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = (
        wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    open_process.restype = wintypes.HANDLE
    get_exit_code = kernel32.GetExitCodeProcess
    get_exit_code.argtypes = (wintypes.HANDLE, wintypes.LPDWORD)
    get_exit_code.restype = wintypes.BOOL
    get_process_times = kernel32.GetProcessTimes
    get_process_times.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    )
    get_process_times.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL

    handle = open_process(
        process_query_limited_information, False, process_id)
    if not handle:
        # ERROR_INVALID_PARAMETER and ERROR_NOT_FOUND prove absence. Access
        # denial and all other failures preserve data conservatively.
        return ctypes.get_last_error() not in {87, 1168}, None
    try:
        exit_code = wintypes.DWORD()
        if not get_exit_code(handle, ctypes.byref(exit_code)):
            return True, None
        if exit_code.value != still_active:
            return False, None
        creation = wintypes.FILETIME()
        exit_time = wintypes.FILETIME()
        kernel_time = wintypes.FILETIME()
        user_time = wintypes.FILETIME()
        if not get_process_times(
                handle, ctypes.byref(creation), ctypes.byref(exit_time),
                ctypes.byref(kernel_time), ctypes.byref(user_time)):
            return True, None
        created = (
            int(creation.dwHighDateTime) << 32
        ) | int(creation.dwLowDateTime)
        return True, f"windows:{created}"
    finally:
        close_handle(handle)


def _linux_process_birth_token(process_id: int) -> str | None:
    """Read Linux /proc start ticks without confusing spaces in comm."""
    try:
        raw = (Path("/proc") / str(process_id) / "stat").read_text(
            encoding="ascii")
        close_paren = raw.rfind(")")
        if close_paren < 1:
            return None
        fields_after_comm = raw[close_paren + 2:].split()
        start_ticks = int(fields_after_comm[19])
        if start_ticks < 0:
            return None
        return f"linux:{start_ticks}"
    except (OSError, UnicodeError, ValueError, IndexError):
        return None


def _process_state(process_id: int) -> tuple[bool, str | None]:
    if process_id <= 0:
        return False, None
    if os.name == "nt":
        # ``os.kill(pid, 0)`` can terminate on Windows; query a read-only
        # process handle instead.
        return _windows_process_state(process_id)
    try:
        os.kill(process_id, 0)
    except ProcessLookupError:
        return False, None
    except PermissionError:
        return True, None
    except OSError as exc:
        # Only ESRCH proves the PID is absent. I/O, kernel, and platform
        # errors must retain private scratch conservatively.
        return exc.errno != errno.ESRCH, None
    return True, _linux_process_birth_token(process_id)


def _process_is_alive(process_id: int) -> bool:
    return _process_state(process_id)[0]


def _snapshot_owner_is_alive(
        process_id: int, expected_birth: str | None) -> bool:
    alive, observed_birth = _process_state(process_id)
    if not alive:
        return False
    if expected_birth is None or observed_birth is None:
        return True
    return observed_birth == expected_birth


def _snapshot_scratch_root_path(base: Path | None) -> Path:
    """Return the owned scratch pathname without creating or hardening it."""
    parent = Path(tempfile.gettempdir()) if base is None else Path(base)
    return parent / _SNAPSHOT_SCRATCH_DIRECTORY


def _snapshot_scratch_root(base: Path | None) -> Path:
    parent = _snapshot_scratch_root_path(base)
    return ensure_private_directory(
        parent,
        harden_existing=True,
    )


def _parse_snapshot_owner_marker(
        raw: bytes, *, directory_name: str) -> dict | None:
    try:
        payload = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError, ValueError):
        return None
    if (not isinstance(payload, dict)
            or set(payload) != {
                "schema_version", "kind", "pid", "process_birth",
                "created_ns", "nonce"}
            or payload.get("schema_version")
            != _SNAPSHOT_OWNER_SCHEMA_VERSION
            or payload.get("kind") != "rag_snapshot_scratch"
            or payload.get("nonce") != directory_name
            or not isinstance(payload.get("pid"), int)
            or isinstance(payload["pid"], bool)
            or payload["pid"] <= 0
            or (payload.get("process_birth") is not None
                and (not isinstance(payload["process_birth"], str)
                     or re.fullmatch(
                         r"(?:windows|linux):[0-9]+",
                         payload["process_birth"]) is None))
            or not isinstance(payload.get("created_ns"), int)
            or isinstance(payload["created_ns"], bool)
            or payload["created_ns"] <= 0):
        return None
    return payload


def _load_snapshot_owner_marker(directory: Path) -> dict | None:
    marker = directory / _SNAPSHOT_OWNER_MARKER
    try:
        raw, _, _ = _read_index_artifact_snapshot(
            marker, max_bytes=_MAX_SNAPSHOT_OWNER_MARKER_BYTES)
    except (OSError, RuntimeError, ValueError):
        return None
    return _parse_snapshot_owner_marker(
        raw, directory_name=directory.name)


def _snapshot_tree_identity(result) -> tuple[int, int, int, int, int]:
    return (
        int(result.st_dev), int(result.st_ino), int(result.st_mode),
        int(result.st_nlink), int(result.st_size),
    )


def _windows_open_pinned_directory(path: Path):
    """Open a directory without delete sharing and reject reparse points."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    get_information = kernel32.GetFileInformationByHandle

    class ByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("file_attributes", wintypes.DWORD),
            ("creation_time", wintypes.FILETIME),
            ("last_access_time", wintypes.FILETIME),
            ("last_write_time", wintypes.FILETIME),
            ("volume_serial_number", wintypes.DWORD),
            ("file_size_high", wintypes.DWORD),
            ("file_size_low", wintypes.DWORD),
            ("number_of_links", wintypes.DWORD),
            ("file_index_high", wintypes.DWORD),
            ("file_index_low", wintypes.DWORD),
        ]

    get_information.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(ByHandleFileInformation)]
    get_information.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL

    file_list_directory = 0x00000001
    file_read_attributes = 0x00000080
    file_share_read = 0x00000001
    file_share_write = 0x00000002
    open_existing = 3
    file_flag_backup_semantics = 0x02000000
    file_flag_open_reparse_point = 0x00200000
    invalid_handle = ctypes.c_void_p(-1).value
    handle = create_file(
        str(path), file_list_directory | file_read_attributes,
        file_share_read | file_share_write, None, open_existing,
        file_flag_backup_semantics | file_flag_open_reparse_point, None)
    if handle == invalid_handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        information = ByHandleFileInformation()
        if not get_information(handle, ctypes.byref(information)):
            raise ctypes.WinError(ctypes.get_last_error())
        file_attribute_directory = 0x00000010
        file_attribute_reparse_point = 0x00000400
        if (not information.file_attributes & file_attribute_directory
                or information.file_attributes
                & file_attribute_reparse_point):
            raise OSError("refusing non-directory or linked scratch path")
    except BaseException:
        close_handle(handle)
        raise
    return handle


def _windows_close_handle(handle) -> None:
    if handle is None:
        return
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    if not close_handle(handle):
        raise ctypes.WinError(ctypes.get_last_error())


@dataclass(slots=True)
class _PinnedSnapshotDirectory:
    """Pinned owned-root/run-directory pair for relative scratch operations."""

    path: Path
    owned_root: Path
    root_identity: tuple[int, int, int, int, int]
    directory_identity: tuple[int, int, int, int, int]
    root_descriptor: int | None = None
    directory_descriptor: int | None = None
    root_handle: Any | None = None
    directory_handle: Any | None = None

    def _safe_leaf(self, name: str) -> None:
        if (not isinstance(name, str) or not name
                or Path(name).name != name
                or (os.name == "nt" and ":" in name)):
            raise OSError("refusing unsafe snapshot scratch entry name")

    def stat_entry(self, name: str):
        self._safe_leaf(name)
        if self.directory_descriptor is not None:
            return os.stat(
                name, dir_fd=self.directory_descriptor,
                follow_symlinks=False)
        child = self.path / name
        if path_is_link_like(child):
            raise OSError("refusing link in snapshot scratch directory")
        return os.lstat(child)

    def path_matches(self) -> bool:
        try:
            if self.root_descriptor is not None:
                root = os.fstat(self.root_descriptor)
                current = os.stat(
                    self.path.name, dir_fd=self.root_descriptor,
                    follow_symlinks=False)
            else:
                root = os.lstat(self.owned_root)
                current = os.lstat(self.path)
                if (path_is_link_like(self.owned_root)
                        or path_is_link_like(self.path)):
                    return False
        except OSError:
            return False
        return (
            _snapshot_tree_identity(root)[:3] == self.root_identity[:3]
            and _snapshot_tree_identity(current)[:3]
            == self.directory_identity[:3]
            and stat.S_ISDIR(current.st_mode)
        )

    def read_entry(self, name: str, *, max_bytes: int) -> bytes:
        self._safe_leaf(name)
        if self.directory_descriptor is None:
            before = self.stat_entry(name)
            if (not stat.S_ISREG(before.st_mode)
                    or before.st_nlink != 1
                    or before.st_size > max_bytes):
                raise OSError("invalid snapshot scratch metadata file")
            raw, _, fingerprint = _read_index_artifact_snapshot(
                self.path / name, max_bytes=max_bytes)
            after = self.stat_entry(name)
            before_fingerprint = _artifact_stat_fingerprint(before)
            after_fingerprint = _artifact_stat_fingerprint(after)
            identity_length = 4 if os.name == "nt" else 5
            if (after.st_nlink != 1
                    or before_fingerprint[:identity_length]
                    != fingerprint[:identity_length]
                    or after_fingerprint[:identity_length]
                    != fingerprint[:identity_length]):
                raise OSError("snapshot scratch metadata changed while read")
            return raw

        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(
            name, flags, dir_fd=self.directory_descriptor)
        try:
            before = os.fstat(descriptor)
            if (not stat.S_ISREG(before.st_mode)
                    or before.st_nlink != 1
                    or before.st_size > max_bytes):
                raise OSError("invalid snapshot scratch metadata file")
            blocks = []
            remaining = max_bytes + 1
            while remaining:
                block = os.read(
                    descriptor, min(_FILE_STREAM_CHUNK_SIZE, remaining))
                if not block:
                    break
                blocks.append(block)
                remaining -= len(block)
            raw = b"".join(blocks)
            after = os.fstat(descriptor)
            if (len(raw) > max_bytes
                    or after.st_nlink != 1
                    or _artifact_stat_fingerprint(before)
                    != _artifact_stat_fingerprint(after)
                    or len(raw) != before.st_size):
                raise OSError("snapshot scratch metadata changed while read")
            return raw
        finally:
            os.close(descriptor)

    def load_marker(self) -> dict | None:
        try:
            raw = self.read_entry(
                _SNAPSHOT_OWNER_MARKER,
                max_bytes=_MAX_SNAPSHOT_OWNER_MARKER_BYTES)
        except (OSError, RuntimeError, ValueError):
            return None
        return _parse_snapshot_owner_marker(
            raw, directory_name=self.path.name)

    def write_marker_if_absent(self, payload: dict) -> None:
        if not self.path_matches():
            raise OSError("snapshot scratch root changed before marker write")
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True,
            separators=(",", ":")).encode("utf-8")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_NOFOLLOW", 0)
        flags |= getattr(os, "O_BINARY", 0)
        if self.directory_descriptor is None:
            descriptor = os.open(
                self.path / _SNAPSHOT_OWNER_MARKER, flags, 0o600)
        else:
            descriptor = os.open(
                _SNAPSHOT_OWNER_MARKER, flags, 0o600,
                dir_fd=self.directory_descriptor)
        try:
            if os.name == "nt":
                enforce_private_path(
                    self.path / _SNAPSHOT_OWNER_MARKER, directory=False)
            else:
                os.fchmod(descriptor, 0o600)
            view = memoryview(encoded)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("snapshot owner marker write was incomplete")
                view = view[written:]
            os.fsync(descriptor)
        except BaseException:
            try:
                if self.directory_descriptor is None:
                    (self.path / _SNAPSHOT_OWNER_MARKER).unlink()
                else:
                    os.unlink(
                        _SNAPSHOT_OWNER_MARKER,
                        dir_fd=self.directory_descriptor)
            except OSError:
                pass
            raise
        finally:
            os.close(descriptor)

    def restore_marker(self, payload: dict) -> None:
        if not self.path_matches():
            raise OSError("snapshot scratch root changed before marker restore")
        try:
            self.stat_entry(_SNAPSHOT_OWNER_MARKER)
        except FileNotFoundError:
            self.write_marker_if_absent(payload)
        else:
            if self.load_marker() != payload:
                raise OSError(
                    "snapshot owner marker changed before restoration")

    def scan(self) -> list[tuple[str, tuple[int, int, int, int, int]]]:
        target = (
            self.directory_descriptor
            if self.directory_descriptor is not None else self.path)
        entries = []
        with os.scandir(target) as iterator:
            for entry in iterator:
                result = self.stat_entry(entry.name)
                if stat.S_ISDIR(result.st_mode):
                    raise OSError(
                        "refusing nested snapshot scratch directory")
                if not stat.S_ISREG(result.st_mode):
                    raise OSError("refusing special snapshot scratch entry")
                if result.st_nlink != 1:
                    raise OSError(
                        "refusing multiply-linked snapshot scratch file")
                entries.append(
                    (entry.name, _snapshot_tree_identity(result)))
        return entries

    def unlink_regular(
            self, name: str, *,
            expected_identity: tuple[int, int, int, int, int] | None = None,
            expected_content_identity: tuple[int, int, int, int] | None = None,
            missing_ok: bool = False) -> None:
        try:
            result = self.stat_entry(name)
        except FileNotFoundError:
            if missing_ok:
                return
            raise
        identity = _snapshot_tree_identity(result)
        if not stat.S_ISREG(result.st_mode) or result.st_nlink != 1:
            raise OSError(
                "refusing non-regular or multiply-linked scratch file")
        if expected_identity is not None and identity != expected_identity:
            raise OSError("snapshot scratch file changed before cleanup")
        if (expected_content_identity is not None
                and _artifact_content_identity(result)
                != expected_content_identity):
            raise OSError("snapshot scratch file changed before cleanup")
        if self.directory_descriptor is not None:
            os.unlink(name, dir_fd=self.directory_descriptor)
        else:
            (self.path / name).unlink()

    def remove_root(self) -> None:
        if not self.path_matches():
            raise OSError("snapshot scratch root changed before cleanup")
        if self.root_descriptor is not None:
            os.rmdir(self.path.name, dir_fd=self.root_descriptor)
            return
        _windows_close_handle(self.directory_handle)
        self.directory_handle = None
        if not self.path_matches():
            raise OSError("snapshot scratch root changed before cleanup")
        self.path.rmdir()

    def close(self) -> None:
        errors = []
        if self.directory_descriptor is not None:
            try:
                os.close(self.directory_descriptor)
            except OSError as exc:
                errors.append(exc)
            self.directory_descriptor = None
        if self.root_descriptor is not None:
            try:
                os.close(self.root_descriptor)
            except OSError as exc:
                errors.append(exc)
            self.root_descriptor = None
        if self.directory_handle is not None:
            try:
                _windows_close_handle(self.directory_handle)
            except OSError as exc:
                errors.append(exc)
            self.directory_handle = None
        if self.root_handle is not None:
            try:
                _windows_close_handle(self.root_handle)
            except OSError as exc:
                errors.append(exc)
            self.root_handle = None
        if errors:
            raise errors[0]


@contextmanager
def _pin_snapshot_directory(
        directory: Path, owned_root: Path,
) -> Iterator[_PinnedSnapshotDirectory]:
    """Pin a direct owned child so cleanup never follows a swapped path."""
    lexical_root = Path(os.path.abspath(owned_root))
    absolute_directory = Path(os.path.abspath(directory))
    if (absolute_directory.parent != lexical_root
            or not absolute_directory.name.startswith("run-")):
        raise OSError("refusing unsafe snapshot scratch cleanup target")
    # Check the operator-visible path before resolving it.  Resolving only the
    # root can produce a long-name/short-name mismatch on Windows temporary
    # directories even when the child is genuinely direct.  Resolving both
    # after link rejection preserves that safety boundary without depending
    # on one spelling of the same filesystem path.
    assert_no_link_components(lexical_root)
    assert_no_link_components(absolute_directory)
    if (path_is_link_like(lexical_root)
            or path_is_link_like(absolute_directory)):
        raise OSError("refusing linked snapshot scratch directory")
    resolved_root = lexical_root.resolve(strict=True)
    resolved_directory = absolute_directory.resolve(strict=True)
    if (resolved_directory.parent != resolved_root
            or resolved_directory.name != absolute_directory.name):
        raise OSError("refusing resolved snapshot scratch escape")
    absolute_directory = resolved_directory
    root_before = os.lstat(resolved_root)
    directory_before = os.lstat(absolute_directory)
    if (not stat.S_ISDIR(root_before.st_mode)
            or not stat.S_ISDIR(directory_before.st_mode)
            or directory_before.st_dev != root_before.st_dev):
        raise OSError("snapshot scratch directory crossed a device boundary")
    root_identity = _snapshot_tree_identity(root_before)
    directory_identity = _snapshot_tree_identity(directory_before)

    pinned = _PinnedSnapshotDirectory(
        path=absolute_directory,
        owned_root=resolved_root,
        root_identity=root_identity,
        directory_identity=directory_identity,
    )
    try:
        if os.name == "nt":
            pinned.root_handle = _windows_open_pinned_directory(resolved_root)
            if (_snapshot_tree_identity(os.lstat(resolved_root))[:3]
                    != root_identity[:3]):
                raise OSError("snapshot scratch root changed while pinning")
            pinned.directory_handle = _windows_open_pinned_directory(
                absolute_directory)
            if (_snapshot_tree_identity(os.lstat(absolute_directory))[:3]
                    != directory_identity[:3]):
                raise OSError(
                    "snapshot scratch directory changed while pinning")
        else:
            flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            pinned.root_descriptor = os.open(resolved_root, flags)
            pinned_root = os.fstat(pinned.root_descriptor)
            if (_snapshot_tree_identity(pinned_root)[:3]
                    != root_identity[:3]):
                raise OSError("snapshot scratch root changed while pinning")
            relative_result = os.stat(
                absolute_directory.name, dir_fd=pinned.root_descriptor,
                follow_symlinks=False)
            if (_snapshot_tree_identity(relative_result)[:3]
                    != directory_identity[:3]):
                raise OSError(
                    "snapshot scratch directory changed while pinning")
            pinned.directory_descriptor = os.open(
                absolute_directory.name, flags,
                dir_fd=pinned.root_descriptor)
            opened = os.fstat(pinned.directory_descriptor)
            if (_snapshot_tree_identity(opened)[:3]
                    != directory_identity[:3]):
                raise OSError(
                    "snapshot scratch directory changed while pinning")
        yield pinned
    finally:
        pinned.close()


def _validated_pinned_snapshot_tree(
        pinned: _PinnedSnapshotDirectory, *, expected_marker: dict,
) -> list[tuple[str, tuple[int, int, int, int, int]]]:
    if not pinned.path_matches():
        raise OSError("snapshot scratch root changed before cleanup")
    if pinned.load_marker() != expected_marker:
        raise OSError("snapshot scratch ownership changed before cleanup")
    files = pinned.scan()
    if sum(name == _SNAPSHOT_OWNER_MARKER for name, _ in files) != 1:
        raise OSError("snapshot scratch owner marker is missing")
    if (not pinned.path_matches()
            or pinned.load_marker() != expected_marker):
        raise OSError("snapshot scratch ownership changed during validation")
    return files


def _validated_owned_snapshot_tree(
        directory: Path, owned_root: Path, *, expected_marker: dict,
) -> tuple[
    list[tuple[Path, tuple[int, int, int, int, int]]],
    list[tuple[Path, tuple[int, int, int, int, int]]],
    tuple[int, int, int, int, int],
]:
    """Validate an entire owned tree before any entry can be removed."""
    with _pin_snapshot_directory(directory, owned_root) as pinned:
        files = _validated_pinned_snapshot_tree(
            pinned, expected_marker=expected_marker)
        return (
            [(pinned.path / name, identity) for name, identity in files],
            [],
            pinned.directory_identity,
        )


def _remove_owned_snapshot_tree(
        directory: Path, owned_root: Path, *, expected_marker: dict) -> None:
    """Remove one fully validated, marker-owned direct child tree."""
    with _pin_snapshot_directory(directory, owned_root) as pinned:
        files = _validated_pinned_snapshot_tree(
            pinned, expected_marker=expected_marker)
        marker_identity = next(
            identity for name, identity in files
            if name == _SNAPSHOT_OWNER_MARKER)

        # Keep the marker until every sensitive payload is gone. A mid-cleanup
        # failure therefore leaves a retryable, explicitly owned directory.
        for name, expected_identity in files:
            if name == _SNAPSHOT_OWNER_MARKER:
                continue
            pinned.unlink_regular(
                name, expected_identity=expected_identity)
        if (not pinned.path_matches()
                or pinned.load_marker() != expected_marker):
            raise OSError("snapshot scratch ownership changed before cleanup")
        pinned.unlink_regular(
            _SNAPSHOT_OWNER_MARKER,
            expected_identity=marker_identity)
        try:
            pinned.remove_root()
        except BaseException as exc:
            try:
                pinned.restore_marker(expected_marker)
            except BaseException as restore_error:
                try:
                    exc.add_note(
                        f"Could not restore snapshot owner marker: "
                        f"{restore_error}")
                except (AttributeError, TypeError):
                    pass
            raise


def cleanup_stale_snapshot_directories(
        scratch_root: Path | None = None, *,
        min_age_seconds: float = _SNAPSHOT_STALE_AGE_SECONDS,
        now_ns: int | None = None,
        apply: bool = True) -> list[Path]:
    """Select or remove old, marker-owned trees whose process is gone."""
    if (not isinstance(min_age_seconds, (int, float))
            or isinstance(min_age_seconds, bool)
            or not math.isfinite(float(min_age_seconds))
            or min_age_seconds < 0):
        raise ValueError("min_age_seconds must be finite and non-negative")
    if apply:
        owned_root = _snapshot_scratch_root(scratch_root)
    else:
        owned_root = _snapshot_scratch_root_path(scratch_root)
        try:
            root_stat = os.lstat(owned_root)
        except FileNotFoundError:
            return []
        if (not stat.S_ISDIR(root_stat.st_mode)
                or path_is_link_like(owned_root)):
            return []
    current_ns = time.time_ns() if now_ns is None else int(now_ns)
    removed = []
    for candidate in owned_root.iterdir():
        try:
            if (not candidate.name.startswith("run-")
                    or not candidate.is_dir()
                    or path_is_link_like(candidate)):
                continue
            marker = _load_snapshot_owner_marker(candidate)
            if marker is None:
                continue
            age_ns = current_ns - marker["created_ns"]
            if (age_ns < int(float(min_age_seconds) * 1_000_000_000)
                    or _snapshot_owner_is_alive(
                        marker["pid"], marker["process_birth"])):
                continue
            if apply:
                _remove_owned_snapshot_tree(
                    candidate, owned_root, expected_marker=marker)
            else:
                _validated_owned_snapshot_tree(
                    candidate, owned_root, expected_marker=marker)
            removed.append(candidate)
        except OSError:
            # Corrupt, linked, raced, or permission-changed candidates are
            # deliberately left untouched for explicit operator inspection.
            continue
    return removed


@dataclass(frozen=True, slots=True)
class FileDigest:
    """Bounded-memory digest and opened-generation identity for one file."""

    sha256: str
    size: int
    fingerprint: ArtifactFingerprint
    nlink: int


@dataclass(frozen=True, slots=True)
class ImmutableFileSnapshot:
    """Private pathname snapshot of one exact source-file generation."""

    path: Path
    source_name: str
    sha256: str
    size: int
    source_fingerprint: ArtifactFingerprint
    capture_policy: str
    _staged_fingerprint: ArtifactFingerprint

    def verify(self, *, chunk_size: int = _FILE_STREAM_CHUNK_SIZE) -> None:
        """Fail if the private pathname snapshot changed after publication."""
        try:
            generation = hash_file_generation(
                self.path, expected_sha256=self.sha256,
                chunk_size=chunk_size)
        except (OSError, RuntimeError) as exc:
            raise RuntimeError(
                f"Immutable snapshot changed after staging: {self.path}"
            ) from exc
        if generation.nlink != 1:
            raise RuntimeError(
                f"Immutable snapshot was multiply linked; private bytes may "
                f"persist outside scratch: {self.path}")
        if (
            # NTFS may settle creation-time metadata after the first reopen;
            # device, inode, size, mtime, and the fresh digest are the relevant
            # content identity here.
            generation.fingerprint[:4] != self._staged_fingerprint[:4]
            or generation.size != self.size
        ):
            raise RuntimeError(
                f"Immutable snapshot changed after staging: {self.path}"
            )


def _hash_file_generation_once(
        path: Path, *, expected_snapshot: ArtifactContentSnapshot | None,
        expected_sha256: str | None, chunk_size: int,
) -> tuple[FileDigest, ArtifactContentSnapshot]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        before_stat = os.fstat(handle.fileno())
        for block in iter(lambda: handle.read(chunk_size), b""):
            size += len(block)
            digest.update(block)

        # Windows creation time is not a content-change counter and writers
        # may retain a sharing handle.  A second bounded pass through the same
        # opened generation catches same-size/restored-mtime in-place changes
        # that occur during the first pass.
        if os.name == "nt":
            handle.seek(0)
            verification_digest = hashlib.sha256()
            verification_size = 0
            for block in iter(lambda: handle.read(chunk_size), b""):
                verification_size += len(block)
                verification_digest.update(block)
            if (verification_size != size
                    or verification_digest.digest() != digest.digest()):
                raise RuntimeError(
                    f"File changed while it was being hashed: {path}")
        after_stat = os.fstat(handle.fileno())

    before = _artifact_content_identity(before_stat)
    after = _artifact_content_identity(after_stat)
    if before != after or size != before[2]:
        raise RuntimeError(f"File changed while it was being hashed: {path}")
    snapshot = before + (digest.digest(),)
    if expected_snapshot is not None and snapshot != expected_snapshot:
        raise RuntimeError(f"File changed while it was being hashed: {path}")
    value = digest.hexdigest()
    if expected_sha256 is not None and value != expected_sha256:
        raise RuntimeError(f"File hash does not match expected source: {path}")
    if int(before_stat.st_ctime_ns) != int(after_stat.st_ctime_ns):
        raise _ArtifactCtimeChanged(snapshot)
    return FileDigest(
        sha256=value,
        size=size,
        fingerprint=_artifact_stat_fingerprint(after_stat),
        nlink=int(after_stat.st_nlink),
    ), snapshot


def hash_file_generation(
        path: Path, *, expected_sha256: str | None = None,
        chunk_size: int = _FILE_STREAM_CHUNK_SIZE) -> FileDigest:
    """Stream one stable file generation without retaining its bytes."""
    path = Path(path)
    if chunk_size < 1:
        raise ValueError("chunk_size must be at least 1")
    if (expected_sha256 is not None
            and (not isinstance(expected_sha256, str)
                 or len(expected_sha256) != 64
                 or any(char not in "0123456789abcdef"
                        for char in expected_sha256))):
        raise ValueError("expected_sha256 must be a lowercase SHA-256 digest")

    expected_snapshot = None
    for attempt in range(len(_SYNCED_FOLDER_READ_RETRY_DELAYS) + 1):
        if attempt:
            time.sleep(_SYNCED_FOLDER_READ_RETRY_DELAYS[attempt - 1])
        try:
            generation, _ = _hash_file_generation_once(
                path,
                expected_snapshot=expected_snapshot,
                expected_sha256=expected_sha256,
                chunk_size=chunk_size,
            )
            return generation
        except _ArtifactCtimeChanged as exc:
            if expected_snapshot is None:
                expected_snapshot = exc.snapshot
            if attempt == len(_SYNCED_FOLDER_READ_RETRY_DELAYS):
                raise RuntimeError(
                    f"File metadata did not stabilize while hashing: {path}"
                ) from exc
    raise AssertionError("unreachable file hash retry state")


def _copy_file_generation_once(
    source: Path,
    destination: Path,
    *,
    expected_snapshot: ArtifactContentSnapshot | None,
    chunk_size: int,
    destination_directory_fd: int | None = None,
    destination_name: str | None = None,
) -> tuple[
    str, int, ArtifactFingerprint, ArtifactContentSnapshot,
    ArtifactFingerprint,
]:
    digest = hashlib.sha256()
    copied = 0
    with source.open("rb") as source_handle:
        before_stat = os.fstat(source_handle.fileno())
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_BINARY", 0)
        if destination_directory_fd is None:
            destination_descriptor = os.open(destination, flags, 0o600)
        else:
            if destination_name is None:
                raise ValueError(
                    "destination_name is required with a directory pin")
            flags |= getattr(os, "O_NOFOLLOW", 0)
            destination_descriptor = os.open(
                destination_name, flags, 0o600,
                dir_fd=destination_directory_fd)
        try:
            if destination_directory_fd is None or os.name == "nt":
                enforce_private_path(destination, directory=False)
            else:
                os.fchmod(destination_descriptor, 0o600)
            with os.fdopen(destination_descriptor, "wb") as destination_handle:
                destination_descriptor = -1
                for block in iter(lambda: source_handle.read(chunk_size), b""):
                    destination_handle.write(block)
                    digest.update(block)
                    copied += len(block)
                destination_handle.flush()
                os.fsync(destination_handle.fileno())
                staged_result = os.fstat(destination_handle.fileno())
        finally:
            if destination_descriptor >= 0:
                os.close(destination_descriptor)

        if os.name == "nt":
            source_handle.seek(0)
            verification_digest = hashlib.sha256()
            verification_size = 0
            for block in iter(lambda: source_handle.read(chunk_size), b""):
                verification_size += len(block)
                verification_digest.update(block)
            if (verification_size != copied
                    or verification_digest.digest() != digest.digest()):
                raise RuntimeError(
                    f"Source changed while it was snapshotted: {source}")
        after_stat = os.fstat(source_handle.fileno())

    before = _artifact_content_identity(before_stat)
    after = _artifact_content_identity(after_stat)
    if before != after or copied != before[2]:
        raise RuntimeError(
            f"Source changed while it was snapshotted: {source}"
        )
    content_snapshot = before + (digest.digest(),)
    if expected_snapshot is not None and content_snapshot != expected_snapshot:
        raise RuntimeError(
            f"Source changed while it was snapshotted: {source}"
        )
    if int(before_stat.st_ctime_ns) != int(after_stat.st_ctime_ns):
        raise _ArtifactCtimeChanged(content_snapshot)
    if (not stat.S_ISREG(staged_result.st_mode)
            or staged_result.st_nlink != 1):
        raise RuntimeError(
            f"Immutable snapshot is not one private regular file: "
            f"{destination}")
    staged_fingerprint = _artifact_stat_fingerprint(staged_result)
    return (
        digest.hexdigest(), copied,
        _artifact_stat_fingerprint(after_stat), content_snapshot,
        staged_fingerprint,
    )


@contextmanager
def immutable_file_snapshot(
    source: Path,
    *,
    temporary_root: Path | None = None,
    scratch_root: Path | None = None,
    snapshot_name: str | None = None,
    expected_sha256: str | None = None,
    chunk_size: int = _FILE_STREAM_CHUNK_SIZE,
    cleanup_error_fn: Callable[..., None] | None = None,
) -> Iterator[ImmutableFileSnapshot]:
    """Stage and yield one private immutable generation of a pathname source.

    The path-only consumer never observes later replacements of ``source``.
    Synced-folder ctime-only churn receives the same bounded retry policy as
    exact artifact reads; every retry remains pinned to device, inode, size,
    mtime, and exact bytes.
    """
    source = Path(source)
    if temporary_root is not None and scratch_root is not None:
        raise ValueError("choose only one snapshot scratch root")
    if chunk_size < 1:
        raise ValueError("chunk_size must be at least 1")
    snapshot_leaf = source.name if snapshot_name is None else snapshot_name
    if (not isinstance(snapshot_leaf, str) or not snapshot_leaf
            or Path(snapshot_leaf).name != snapshot_leaf
            or snapshot_leaf == _SNAPSHOT_OWNER_MARKER
            or (os.name == "nt" and ":" in snapshot_leaf)):
        raise ValueError("source has an unsafe snapshot basename")
    if (expected_sha256 is not None
            and (not isinstance(expected_sha256, str)
                 or len(expected_sha256) != 64
                 or any(char not in "0123456789abcdef"
                        for char in expected_sha256))):
        raise ValueError("expected_sha256 must be a lowercase SHA-256 digest")
    selected_root = scratch_root if scratch_root is not None else temporary_root
    owned_root = _snapshot_scratch_root(selected_root)
    cleanup_stale_snapshot_directories(selected_root)
    temporary_directory = Path(tempfile.mkdtemp(
        prefix="run-", dir=str(owned_root)))
    operation_error: BaseException | None = None
    cleanup_errors: list[BaseException] = []
    pinned: _PinnedSnapshotDirectory | None = None
    pin_manager = None
    marker_created = False
    staged: ImmutableFileSnapshot | None = None
    snapshot_path = temporary_directory / snapshot_leaf
    marker_payload = {
        "schema_version": _SNAPSHOT_OWNER_SCHEMA_VERSION,
        "kind": "rag_snapshot_scratch",
        "pid": os.getpid(),
        "process_birth": _process_state(os.getpid())[1],
        "created_ns": time.time_ns(),
        "nonce": temporary_directory.name,
    }
    try:
        temporary_directory = ensure_private_directory(
            temporary_directory, harden_existing=True)
        pin_manager = _pin_snapshot_directory(
            temporary_directory, owned_root)
        pinned = pin_manager.__enter__()
        pinned.write_marker_if_absent(marker_payload)
        marker_created = True
        source_size_hint = source.stat().st_size
        required_space = source_size_hint + _SNAPSHOT_FREE_SPACE_MARGIN
        if shutil.disk_usage(temporary_directory).free < required_space:
            raise OSError(
                f"Insufficient scratch space to snapshot {source_size_hint} "
                f"bytes from {source}")
        snapshot_path = temporary_directory / snapshot_leaf
        expected_snapshot = None
        for attempt in range(len(_SYNCED_FOLDER_READ_RETRY_DELAYS) + 1):
            if attempt:
                time.sleep(_SYNCED_FOLDER_READ_RETRY_DELAYS[attempt - 1])
            pinned.unlink_regular(snapshot_leaf, missing_ok=True)
            try:
                result = _copy_file_generation_once(
                    source,
                    snapshot_path,
                    expected_snapshot=expected_snapshot,
                    chunk_size=chunk_size,
                    destination_directory_fd=pinned.directory_descriptor,
                    destination_name=snapshot_leaf,
                )
                digest, size, source_fingerprint, _, staged_fingerprint = result
                if expected_sha256 is not None and digest != expected_sha256:
                    raise RuntimeError(
                        f"File hash does not match expected source: {source}")
                staged = ImmutableFileSnapshot(
                    path=snapshot_path,
                    source_name=source.name,
                    sha256=digest,
                    size=size,
                    source_fingerprint=source_fingerprint,
                    capture_policy="stream-copy-v1",
                    _staged_fingerprint=staged_fingerprint,
                )
                break
            except _ArtifactCtimeChanged as exc:
                if expected_snapshot is None:
                    expected_snapshot = exc.snapshot
                if attempt == len(_SYNCED_FOLDER_READ_RETRY_DELAYS):
                    raise RuntimeError(
                        f"Source changed while it was snapshotted: {source}"
                    ) from exc
        if staged is None:
            raise AssertionError("unreachable immutable snapshot retry state")
        yield staged
        staged.verify(chunk_size=chunk_size)
    except BaseException as exc:
        operation_error = exc
        raise
    finally:
        snapshot_removed = False
        if pinned is not None:
            try:
                pinned.unlink_regular(
                    snapshot_leaf,
                    expected_content_identity=(
                        staged._staged_fingerprint[:4]
                        if staged is not None else None),
                    missing_ok=True,
                )
                snapshot_removed = True
            except BaseException as exc:
                cleanup_errors.append(exc)

        # Never orphan sensitive bytes. Keep (or restore) the ownership marker
        # whenever payload removal or final directory removal fails so the
        # stale-owner janitor can safely retry after this process exits.
        marker_removed = False
        if snapshot_removed and marker_created and pinned is not None:
            try:
                if (not pinned.path_matches()
                        or pinned.load_marker() != marker_payload):
                    raise OSError(
                        "snapshot scratch ownership changed before cleanup")
                marker_identity = _snapshot_tree_identity(
                    pinned.stat_entry(_SNAPSHOT_OWNER_MARKER))
                pinned.unlink_regular(
                    _SNAPSHOT_OWNER_MARKER,
                    expected_identity=marker_identity)
                marker_removed = True
            except BaseException as exc:
                cleanup_errors.append(exc)
        if (snapshot_removed and pinned is not None
                and (marker_removed or not marker_created)):
            try:
                pinned.remove_root()
            except BaseException as exc:
                cleanup_errors.append(exc)
        if (cleanup_errors and marker_created and pinned is not None):
            try:
                pinned.restore_marker(marker_payload)
            except BaseException as exc:
                cleanup_errors.append(exc)
        if pinned is not None and pin_manager is not None:
            try:
                pin_manager.__exit__(None, None, None)
            except BaseException as exc:
                cleanup_errors.append(exc)
            pinned = None
        if cleanup_errors:
            if operation_error is not None:
                for cleanup_error in cleanup_errors:
                    try:
                        operation_error.add_note(
                            f"Snapshot cleanup also failed: {cleanup_error}")
                    except (AttributeError, TypeError):
                        pass
                    if cleanup_error_fn is not None:
                        try:
                            cleanup_error_fn(
                                "Could not remove snapshot scratch path %s",
                                temporary_directory,
                                error=cleanup_error,
                            )
                        except BaseException:
                            pass
            else:
                primary_cleanup_error = cleanup_errors[0]
                for extra_error in cleanup_errors[1:]:
                    try:
                        primary_cleanup_error.add_note(
                            f"Additional snapshot cleanup failure: {extra_error}")
                    except (AttributeError, TypeError):
                        pass
                raise primary_cleanup_error


def _artifact_stat_fingerprint(stat_result) -> ArtifactFingerprint:
    """Return a strong identity for one opened artifact generation."""
    return (
        int(stat_result.st_dev), int(stat_result.st_ino),
        int(stat_result.st_size), int(stat_result.st_mtime_ns),
        int(stat_result.st_ctime_ns),
    )


def _artifact_content_identity(stat_result) -> tuple[int, int, int, int]:
    """Return the opened-file fields that describe its content generation."""
    return (
        int(stat_result.st_dev), int(stat_result.st_ino),
        int(stat_result.st_size), int(stat_result.st_mtime_ns),
    )


def _read_index_artifact_snapshot_once(
        path: Path, *,
        expected_snapshot: ArtifactContentSnapshot | None,
        max_bytes: int | None,
        ) -> tuple[bytes, str, ArtifactFingerprint]:
    with path.open("rb") as handle:
        before_stat = os.fstat(handle.fileno())
        if max_bytes is not None and int(before_stat.st_size) > max_bytes:
            raise ValueError(f"Artifact exceeds {max_bytes} bytes: {path}")
        raw = handle.read() if max_bytes is None else handle.read(max_bytes + 1)
        digest = hashlib.sha256(raw)
        if os.name == "nt":
            handle.seek(0)
            verification_digest = hashlib.sha256()
            verification_size = 0
            for block in iter(
                    lambda: handle.read(_FILE_STREAM_CHUNK_SIZE), b""):
                verification_size += len(block)
                verification_digest.update(block)
            if (verification_size != len(raw)
                    or verification_digest.digest() != digest.digest()):
                raise RuntimeError(
                    f"Artifact changed while it was being read: {path}")
        after_stat = os.fstat(handle.fileno())

    if max_bytes is not None and len(raw) > max_bytes:
        raise ValueError(f"Artifact exceeds {max_bytes} bytes: {path}")

    before = _artifact_content_identity(before_stat)
    after = _artifact_content_identity(after_stat)
    if before != after or len(raw) != before[2]:
        raise RuntimeError(
            f"Artifact changed while it was being read: {path}")

    snapshot = before + (digest.digest(),)
    if expected_snapshot is not None and snapshot != expected_snapshot:
        raise RuntimeError(
            f"Artifact changed while it was being read: {path}")
    if int(before_stat.st_ctime_ns) != int(after_stat.st_ctime_ns):
        raise _ArtifactCtimeChanged(snapshot)
    return raw, digest.hexdigest(), _artifact_stat_fingerprint(after_stat)


def _read_index_artifact_snapshot(
        path: Path, *,
        max_bytes: int | None = None) -> tuple[bytes, str, ArtifactFingerprint]:
    """Read, identify, and hash the exact bytes from one file handle.

    Opening once per attempt prevents an atomic path replacement from mixing
    the hash of one chunks generation with records parsed from another.
    ``fstat`` guards against an in-place write racing the read. Synced folders
    can update only ``ctime`` while the same bytes are open, so that one benign
    case receives bounded retries pinned to the original content identity.
    """
    path = Path(path)
    if max_bytes is not None and max_bytes < 0:
        raise ValueError("max_bytes cannot be negative")
    expected_snapshot = None
    for attempt in range(len(_SYNCED_FOLDER_READ_RETRY_DELAYS) + 1):
        if attempt:
            time.sleep(_SYNCED_FOLDER_READ_RETRY_DELAYS[attempt - 1])
        try:
            return _read_index_artifact_snapshot_once(
                path, expected_snapshot=expected_snapshot,
                max_bytes=max_bytes)
        except _ArtifactCtimeChanged as exc:
            if expected_snapshot is None:
                expected_snapshot = exc.snapshot
            if attempt == len(_SYNCED_FOLDER_READ_RETRY_DELAYS):
                raise RuntimeError(
                    f"Artifact changed while it was being read: {path}"
                ) from exc
    raise AssertionError("unreachable artifact read retry state")


def _parse_index_records_strict(
        raw: bytes, path: Path, *, chunk_id_fn: ChunkIdFn) -> list[dict]:
    """Parse and validate one already-captured chunks byte snapshot."""
    last_unicode_error = None
    for encoding in ("utf-8-sig", "latin-1"):
        try:
            contents = raw.decode(encoding)
        except UnicodeDecodeError as exc:
            last_unicode_error = exc
            continue

        records = []
        for line_number, line in enumerate(jsonl_lines(contents), 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid JSON in chunks file {path}:{line_number}: "
                    f"{exc.msg}"
                ) from exc
            if not isinstance(record, dict):
                raise ValueError(
                    f"Invalid chunk at {path}:{line_number}: "
                    "record must be a JSON object"
                )
            if (not isinstance(record.get("text"), str)
                    or not record["text"].strip()):
                raise ValueError(
                    f"Invalid chunk at {path}:{line_number}: "
                    "'text' must be a non-empty string"
                )
            if not isinstance(record.get("metadata"), dict):
                raise ValueError(
                    f"Invalid chunk at {path}:{line_number}: "
                    "'metadata' must be a JSON object"
                )
            pending = [("metadata", record["metadata"])]
            while pending:
                field_path, value = pending.pop()
                if isinstance(value, float) and not math.isfinite(value):
                    raise ValueError(
                        f"Invalid chunk at {path}:{line_number}: "
                        f"'{field_path}' must contain finite numbers"
                    )
                if isinstance(value, dict):
                    pending.extend(
                        (f"{field_path}.{key}", child)
                        for key, child in value.items()
                    )
                elif isinstance(value, list):
                    pending.extend(
                        (f"{field_path}[{index}]", child)
                        for index, child in enumerate(value)
                    )
            records.append(record)

        if not records:
            raise ValueError(f"Chunks file contains no records: {path}")
        stable_ids = [chunk_id_fn(record) for record in records]
        if len(set(stable_ids)) != len(stable_ids):
            raise ValueError(
                f"Chunks file contains duplicate stable chunk IDs: {path}")
        return records

    raise ValueError(
        f"Could not decode chunks file: {path}") from last_unicode_error


def _atomic_write_text(
        path: Path, content: str, *, replace_fn: ReplaceFn | None = None,
        cleanup_error_fn: CleanupErrorFn | None = None) -> None:
    """Durably replace a text artifact without exposing partial contents."""
    atomic_write_private_text(
        path, content, replace_fn=replace_fn,
        cleanup_error_fn=cleanup_error_fn)


def _artifact_parameters_sha256(parameters: dict) -> str:
    """Return a stable credential-free configuration fingerprint."""
    serialized = json.dumps(
        parameters, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _artifact_completion_path(target: Path, *, stage: str) -> Path:
    if stage == "split_export":
        return target / ".rag-complete.json"
    return target.with_name(f".{target.name}.{stage}.complete.json")


def _write_artifact_completion(
        manifest_path: Path, *, stage: str, source_sha256: str,
        source_record_count: int | None, parameters: dict,
        outputs: dict[str, Path], schema_version: int,
        artifact_sha256_fn: ArtifactHashFn,
        atomic_write_json_fn: AtomicJsonWriterFn,
        source_name: str | None = None,
        extra_fields: dict[str, object] | None = None) -> None:
    output_records = []
    for role, path in sorted(outputs.items()):
        stat_result = path.stat()
        if not path.is_file() or stat_result.st_size <= 0:
            raise RuntimeError(
                f"Cannot commit incomplete {stage} output: {path}")
        output_records.append({
            "role": role,
            "name": path.name,
            "size": stat_result.st_size,
            "sha256": artifact_sha256_fn(path),
        })
    payload = {
        "schema_version": schema_version,
        "stage": stage,
        "source_sha256": source_sha256,
        "source_record_count": source_record_count,
        "parameters_sha256": _artifact_parameters_sha256(parameters),
        "outputs": output_records,
    }
    if source_name is not None:
        if not source_name or Path(source_name).name != source_name:
            raise ValueError("completion source_name must be one basename")
        payload["source_name"] = source_name
    if extra_fields:
        collisions = set(payload) & set(extra_fields)
        if collisions:
            raise ValueError(
                "completion extra fields collide with reserved fields: "
                + ", ".join(sorted(collisions)))
        payload.update(extra_fields)
    atomic_write_json_fn(manifest_path, payload)


def _fixed_artifacts_complete(
        manifest_path: Path, *, stage: str, source_sha256: str,
        source_record_count: int | None, parameters: dict,
        outputs: dict[str, Path], schema_version: int,
        artifact_sha256_fn: ArtifactHashFn,
        source_name: str | None = None) -> bool:
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return False
        expected_header = {
            "schema_version": schema_version,
            "stage": stage,
            "source_sha256": source_sha256,
            "source_record_count": source_record_count,
            "parameters_sha256": _artifact_parameters_sha256(parameters),
        }
        if any(payload.get(key) != value
               for key, value in expected_header.items()):
            return False
        manifested_source_name = payload.get("source_name")
        if (source_name is not None and manifested_source_name is not None
                and manifested_source_name != source_name):
            return False
        records = payload.get("outputs")
        if not isinstance(records, list) or len(records) != len(outputs):
            return False
        by_role = {
            record.get("role"): record for record in records
            if isinstance(record, dict) and isinstance(record.get("role"), str)
        }
        if set(by_role) != set(outputs):
            return False
        for role, path in outputs.items():
            record = by_role[role]
            if record.get("name") != path.name or not path.is_file():
                return False
            stat_result = path.stat()
            if (stat_result.st_size <= 0
                    or record.get("size") != stat_result.st_size
                    or record.get("sha256") != artifact_sha256_fn(path)):
                return False
        return True
    except (OSError, UnicodeError, json.JSONDecodeError, RuntimeError):
        return False


def _atomic_write_json(
        path: Path, payload: object, *, replace_fn: ReplaceFn | None = None,
        cleanup_error_fn: CleanupErrorFn | None = None) -> None:
    """Write JSON by replacing a fully flushed temporary file atomically."""
    atomic_write_private_json(
        path, payload, replace_fn=replace_fn,
        cleanup_error_fn=cleanup_error_fn)


def _atomic_write_jsonl(
        path: Path, records: list[dict], *,
        replace_fn: ReplaceFn | None = None,
        cleanup_error_fn: CleanupErrorFn | None = None) -> None:
    """Durably replace a JSONL artifact without exposing partial contents."""
    atomic_write_private_jsonl(
        path, records, replace_fn=replace_fn,
        cleanup_error_fn=cleanup_error_fn)
