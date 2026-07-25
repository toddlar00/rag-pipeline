"""Private, link-aware storage primitives for sensitive local artifacts.

The module is standard-library-only.  POSIX paths use owner-only modes;
Windows paths receive a protected DACL granting full control only to the
current user. Atomic writers pin their parent on POSIX and use an owner-only,
link-free parent plus identity checks on Windows.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import secrets
import stat
import tempfile
import threading
import time
from collections.abc import Callable, Iterable
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator


PRIVATE_DIRECTORY_MODE = 0o700
PRIVATE_FILE_MODE = 0o600
PRIVATE_TREE_POLICY_SCHEMA_VERSION = 1
PRIVATE_TREE_POLICY_DIRECTORY_NAME = ".rag-storage-policy"
_WINDOWS_SYMLINK_TAG = 0xA000000C
_WINDOWS_MOUNT_POINT_TAG = 0xA0000003
_PRIVATE_TREE_POLICY_GUARD = threading.RLock()
_SYNCED_FOLDER_RETRY_DELAYS = (0.01, 0.05, 0.15)
_TRANSIENT_WINDOWS_REPLACE_ERRORS = {5, 32, 33}

CleanupErrorFn = Callable[..., None]
ReplaceFn = Callable[[Any, Any], Any]
PathProducerFn = Callable[[Path], None]
_NATIVE_OS_REPLACE = os.replace


class StoragePolicyError(OSError):
    """Raised when a sensitive path cannot satisfy the storage policy."""


def _absolute(path: Path) -> Path:
    path = Path(path)
    if path.is_absolute():
        return path
    return Path(os.path.abspath(path))


def _is_link_like(path: Path) -> bool:
    """Return whether an existing path is a symlink or Windows junction."""
    try:
        result = os.lstat(path)
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(result.st_mode):
        return True
    if os.name != "nt":
        return False
    return getattr(result, "st_reparse_tag", 0) in {
        _WINDOWS_SYMLINK_TAG,
        _WINDOWS_MOUNT_POINT_TAG,
    }


def path_is_link_like(path: Path) -> bool:
    """Return whether an existing path is a symlink or Windows junction."""
    return _is_link_like(Path(path))


def assert_no_link_components(path: Path, *, include_leaf: bool = True) -> None:
    """Reject symlink/junction components in one existing path prefix."""
    absolute = _absolute(path)
    candidates = list(absolute.parents)
    candidates.reverse()
    if include_leaf:
        candidates.append(absolute)
    for candidate in candidates:
        if not candidate.exists() and not _is_link_like(candidate):
            continue
        if _is_link_like(candidate):
            raise StoragePolicyError(
                "sensitive storage paths cannot traverse links or junctions")


@lru_cache(maxsize=1)
def _windows_current_user_sid() -> str:
    if os.name != "nt":
        raise RuntimeError("Windows security APIs are unavailable")
    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    token = wintypes.HANDLE()
    open_process_token = advapi32.OpenProcessToken
    open_process_token.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    open_process_token.restype = wintypes.BOOL
    get_token_information = advapi32.GetTokenInformation
    get_token_information.argtypes = [
        wintypes.HANDLE, ctypes.c_uint, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD)]
    get_token_information.restype = wintypes.BOOL
    convert_sid = advapi32.ConvertSidToStringSidW
    convert_sid.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    convert_sid.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    local_free = kernel32.LocalFree
    local_free.argtypes = [wintypes.HLOCAL]
    local_free.restype = wintypes.HLOCAL
    get_current_process = kernel32.GetCurrentProcess
    get_current_process.argtypes = []
    get_current_process.restype = wintypes.HANDLE

    class SidAndAttributes(ctypes.Structure):
        _fields_ = [
            ("sid", ctypes.c_void_p),
            ("attributes", wintypes.DWORD),
        ]

    token_query = 0x0008
    token_user = 1
    if not open_process_token(
            get_current_process(), token_query, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        required = wintypes.DWORD()
        get_token_information(
            token, token_user, None, 0, ctypes.byref(required))
        if not required.value:
            raise ctypes.WinError(ctypes.get_last_error())
        buffer = ctypes.create_string_buffer(required.value)
        if not get_token_information(
                token, token_user, buffer, required,
                ctypes.byref(required)):
            raise ctypes.WinError(ctypes.get_last_error())
        token_data = ctypes.cast(
            buffer, ctypes.POINTER(SidAndAttributes)).contents
        sid_text = wintypes.LPWSTR()
        if not convert_sid(token_data.sid, ctypes.byref(sid_text)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return sid_text.value
        finally:
            local_free(ctypes.cast(sid_text, wintypes.HLOCAL))
    finally:
        close_handle(token)


def _windows_apply_private_dacl(path: Path, *, directory: bool) -> None:
    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    convert_descriptor = (
        advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW)
    convert_descriptor.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.DWORD)]
    convert_descriptor.restype = wintypes.BOOL
    set_file_security = advapi32.SetFileSecurityW
    set_file_security.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
    set_file_security.restype = wintypes.BOOL
    local_free = kernel32.LocalFree
    local_free.argtypes = [wintypes.HLOCAL]
    local_free.restype = wintypes.HLOCAL

    sid = _windows_current_user_sid()
    inheritance = "OICI" if directory else ""
    descriptor_text = f"D:P(A;{inheritance};FA;;;{sid})"
    descriptor = ctypes.c_void_p()
    if not convert_descriptor(
            descriptor_text, 1, ctypes.byref(descriptor), None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        dacl_security_information = 0x00000004
        protected_dacl_security_information = 0x80000000
        security_information = (
            dacl_security_information | protected_dacl_security_information)
        if not set_file_security(
                str(path), security_information, descriptor):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        local_free(ctypes.cast(descriptor, wintypes.HLOCAL))


def _windows_apply_private_dacl_handle(handle) -> None:
    """Apply the private file DACL to an already-open Windows handle."""
    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    convert_descriptor = (
        advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW)
    convert_descriptor.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.DWORD)]
    convert_descriptor.restype = wintypes.BOOL
    get_dacl = advapi32.GetSecurityDescriptorDacl
    get_dacl.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(wintypes.BOOL),
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.BOOL)]
    get_dacl.restype = wintypes.BOOL
    set_security = advapi32.SetSecurityInfo
    set_security.argtypes = [
        wintypes.HANDLE, ctypes.c_int, wintypes.DWORD, ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    set_security.restype = wintypes.DWORD
    local_free = kernel32.LocalFree
    local_free.argtypes = [wintypes.HLOCAL]
    local_free.restype = wintypes.HLOCAL

    descriptor_text = f"D:P(A;;FA;;;{_windows_current_user_sid()})"
    descriptor = ctypes.c_void_p()
    if not convert_descriptor(
            descriptor_text, 1, ctypes.byref(descriptor), None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        present = wintypes.BOOL()
        defaulted = wintypes.BOOL()
        dacl = ctypes.c_void_p()
        if not get_dacl(
                descriptor, ctypes.byref(present), ctypes.byref(dacl),
                ctypes.byref(defaulted)) or not present.value:
            raise ctypes.WinError(ctypes.get_last_error())
        file_object = 1
        dacl_security_information = 0x00000004
        protected_dacl_security_information = 0x80000000
        result = set_security(
            handle, file_object,
            dacl_security_information | protected_dacl_security_information,
            None, None, dacl, None)
        if result:
            raise ctypes.WinError(result)
    finally:
        local_free(ctypes.cast(descriptor, wintypes.HLOCAL))


def windows_dacl_sddl(path: Path) -> str:
    """Return the Windows DACL as SDDL for verification and diagnostics."""
    if os.name != "nt":
        raise RuntimeError("Windows security APIs are unavailable")
    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_file_security = advapi32.GetFileSecurityW
    get_file_security.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD)]
    get_file_security.restype = wintypes.BOOL
    convert_descriptor = (
        advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW)
    convert_descriptor.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
        ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(wintypes.DWORD)]
    convert_descriptor.restype = wintypes.BOOL
    local_free = kernel32.LocalFree
    local_free.argtypes = [wintypes.HLOCAL]
    local_free.restype = wintypes.HLOCAL

    dacl_security_information = 0x00000004
    required = wintypes.DWORD()
    get_file_security(
        str(path), dacl_security_information, None, 0,
        ctypes.byref(required))
    if not required.value:
        raise ctypes.WinError(ctypes.get_last_error())
    buffer = ctypes.create_string_buffer(required.value)
    if not get_file_security(
            str(path), dacl_security_information, buffer, required,
            ctypes.byref(required)):
        raise ctypes.WinError(ctypes.get_last_error())
    output = wintypes.LPWSTR()
    if not convert_descriptor(
            buffer, 1, dacl_security_information,
            ctypes.byref(output), None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return output.value
    finally:
        local_free(ctypes.cast(output, wintypes.HLOCAL))


def windows_path_is_private(path: Path, *, directory: bool) -> bool:
    """Return whether a Windows path has the required private DACL.

    Windows may serialize semantically identical security descriptors with
    additional control flags (for example, ``AI`` after ACL canonicalization).
    Inspect the binary descriptor instead of comparing its SDDL spelling.
    """
    if os.name != "nt":
        raise RuntimeError("Windows security APIs are unavailable")
    from ctypes import wintypes

    class Acl(ctypes.Structure):
        _fields_ = [
            ("revision", wintypes.BYTE),
            ("reserved", wintypes.BYTE),
            ("size", wintypes.WORD),
            ("ace_count", wintypes.WORD),
            ("reserved2", wintypes.WORD),
        ]

    class AceHeader(ctypes.Structure):
        _fields_ = [
            ("ace_type", wintypes.BYTE),
            ("ace_flags", wintypes.BYTE),
            ("ace_size", wintypes.WORD),
        ]

    class AccessAllowedAce(ctypes.Structure):
        _fields_ = [
            ("header", AceHeader),
            ("mask", wintypes.DWORD),
            ("sid_start", wintypes.DWORD),
        ]

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_file_security = advapi32.GetFileSecurityW
    get_file_security.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD)]
    get_file_security.restype = wintypes.BOOL
    get_control = advapi32.GetSecurityDescriptorControl
    get_control.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(wintypes.WORD),
        ctypes.POINTER(wintypes.DWORD)]
    get_control.restype = wintypes.BOOL
    get_dacl = advapi32.GetSecurityDescriptorDacl
    get_dacl.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(wintypes.BOOL),
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.BOOL)]
    get_dacl.restype = wintypes.BOOL
    get_ace = advapi32.GetAce
    get_ace.argtypes = [
        ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p)]
    get_ace.restype = wintypes.BOOL
    convert_sid = advapi32.ConvertStringSidToSidW
    convert_sid.argtypes = [
        wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
    convert_sid.restype = wintypes.BOOL
    equal_sid = advapi32.EqualSid
    equal_sid.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    equal_sid.restype = wintypes.BOOL
    is_valid_sid = advapi32.IsValidSid
    is_valid_sid.argtypes = [ctypes.c_void_p]
    is_valid_sid.restype = wintypes.BOOL
    get_sid_length = advapi32.GetLengthSid
    get_sid_length.argtypes = [ctypes.c_void_p]
    get_sid_length.restype = wintypes.DWORD
    local_free = kernel32.LocalFree
    local_free.argtypes = [wintypes.HLOCAL]
    local_free.restype = wintypes.HLOCAL

    dacl_security_information = 0x00000004
    required = wintypes.DWORD()
    get_file_security(
        str(path), dacl_security_information, None, 0,
        ctypes.byref(required))
    if not required.value:
        raise ctypes.WinError(ctypes.get_last_error())
    descriptor = ctypes.create_string_buffer(required.value)
    if not get_file_security(
            str(path), dacl_security_information, descriptor, required,
            ctypes.byref(required)):
        raise ctypes.WinError(ctypes.get_last_error())

    control = wintypes.WORD()
    revision = wintypes.DWORD()
    if not get_control(
            descriptor, ctypes.byref(control), ctypes.byref(revision)):
        raise ctypes.WinError(ctypes.get_last_error())
    se_dacl_protected = 0x1000
    if not control.value & se_dacl_protected:
        return False

    present = wintypes.BOOL()
    defaulted = wintypes.BOOL()
    dacl = ctypes.c_void_p()
    if not get_dacl(
            descriptor, ctypes.byref(present), ctypes.byref(dacl),
            ctypes.byref(defaulted)):
        raise ctypes.WinError(ctypes.get_last_error())
    if not present.value or not dacl.value:
        return False
    acl = ctypes.cast(dacl, ctypes.POINTER(Acl)).contents
    if acl.ace_count != 1:
        return False

    ace_pointer = ctypes.c_void_p()
    if not get_ace(dacl, 0, ctypes.byref(ace_pointer)):
        raise ctypes.WinError(ctypes.get_last_error())
    ace = ctypes.cast(
        ace_pointer, ctypes.POINTER(AccessAllowedAce)).contents
    access_allowed_ace_type = 0
    expected_flags = 0x01 | 0x02 if directory else 0
    file_all_access = 0x001F01FF
    sid_offset = AccessAllowedAce.sid_start.offset
    if (
        ace.header.ace_type != access_allowed_ace_type
        or ace.header.ace_flags != expected_flags
        or ace.mask != file_all_access
        or ace.header.ace_size < sid_offset + 8
    ):
        return False

    actual_sid = ctypes.c_void_p(ace_pointer.value + sid_offset)
    if not is_valid_sid(actual_sid):
        return False
    if ace.header.ace_size < sid_offset + get_sid_length(actual_sid):
        return False

    expected_sid = ctypes.c_void_p()
    if not convert_sid(
            _windows_current_user_sid(), ctypes.byref(expected_sid)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return bool(equal_sid(actual_sid, expected_sid))
    finally:
        local_free(ctypes.cast(expected_sid, wintypes.HLOCAL))


def enforce_private_path(path: Path, *, directory: bool | None = None) -> None:
    """Apply and verify the platform's private permission policy."""
    path = Path(path)
    if _is_link_like(path):
        raise StoragePolicyError(
            "refusing to change permissions through a link or junction")
    if directory is None:
        directory = path.is_dir()
    if os.name == "nt":
        _windows_apply_private_dacl(path, directory=directory)
        if not windows_path_is_private(path, directory=directory):
            raise StoragePolicyError(
                "Windows path did not retain the required private DACL")
        return
    mode = PRIVATE_DIRECTORY_MODE if directory else PRIVATE_FILE_MODE
    os.chmod(path, mode, follow_symlinks=False)
    actual = stat.S_IMODE(os.stat(
        path, follow_symlinks=False).st_mode)
    if actual != mode:
        raise StoragePolicyError(
            f"private permission verification failed (mode {actual:o})")


def ensure_private_directory(path: Path, *, harden_existing: bool = True) -> Path:
    """Create a link-free directory and enforce its private permissions."""
    absolute = _absolute(path)
    missing = []
    candidate = absolute
    while not candidate.exists():
        if _is_link_like(candidate):
            raise StoragePolicyError(
                "private directories cannot be links or junctions")
        missing.append(candidate)
        parent = candidate.parent
        if parent == candidate:
            break
        candidate = parent
    assert_no_link_components(candidate)
    for candidate in reversed(missing):
        candidate.mkdir(mode=PRIVATE_DIRECTORY_MODE, exist_ok=True)
        assert_no_link_components(candidate)
        if not candidate.is_dir():
            raise StoragePolicyError(
                "private directory path is not a directory")
        enforce_private_path(candidate, directory=True)
    assert_no_link_components(absolute)
    if not absolute.is_dir():
        raise StoragePolicyError(
            "private directory path is not a directory")
    if harden_existing or missing:
        enforce_private_path(absolute, directory=True)
    return absolute


def _parent_identity(parent: Path) -> tuple[int, int]:
    result = os.stat(parent, follow_symlinks=False)
    if not stat.S_ISDIR(result.st_mode):
        raise StoragePolicyError("sensitive output parent is not a directory")
    return int(result.st_dev), int(result.st_ino)


def _prepare_private_output(path: Path) -> tuple[Path, tuple[int, int]]:
    path = _absolute(path)
    parent = ensure_private_directory(path.parent, harden_existing=True)
    assert_no_link_components(parent)
    _validate_output_leaf(path)
    return path, _parent_identity(parent)


def _validate_output_leaf(path: Path) -> None:
    try:
        result = os.lstat(path)
    except FileNotFoundError:
        return
    if stat.S_ISLNK(result.st_mode) or _is_link_like(path):
        raise StoragePolicyError(
            "sensitive output cannot replace a link or junction")
    if not stat.S_ISREG(result.st_mode):
        raise StoragePolicyError(
            "sensitive output must be a regular file path")


def _temporary_identity(path: Path) -> tuple[int, int, int, int, bytes]:
    """Snapshot staging identity and exact bytes through one pinned handle."""
    linked = os.lstat(path)
    if not stat.S_ISREG(linked.st_mode) or linked.st_nlink != 1:
        raise StoragePolicyError(
            "private temporary file changed before publication")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise StoragePolicyError(
            "private temporary file changed before publication") from exc
    try:
        before = os.fstat(descriptor)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or (before.st_dev, before.st_ino) !=
                (linked.st_dev, linked.st_ino)):
            raise StoragePolicyError(
                "private temporary file changed before publication")
        digest = hashlib.sha256()
        remaining = int(before.st_size)
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                break
            digest.update(chunk)
            remaining -= len(chunk)
        after = os.fstat(descriptor)
    except OSError as exc:
        raise StoragePolicyError(
            "private temporary file changed before publication") from exc
    finally:
        os.close(descriptor)

    def identity(result) -> tuple[int, int, int, int, int]:
        return (
            int(result.st_dev), int(result.st_ino), int(result.st_size),
            int(result.st_mtime_ns), int(result.st_nlink))

    if remaining or identity(before) != identity(after):
        raise StoragePolicyError(
            "private temporary file changed before publication")
    return identity(before)[:-1] + (digest.digest(),)


def _transient_replace_error(error: OSError) -> bool:
    return getattr(
        error, "winerror", None) in _TRANSIENT_WINDOWS_REPLACE_ERRORS


def _replace_with_revalidated_retry(
        source: Path, destination: Path, *, replace: ReplaceFn,
        parent_identity: tuple[int, int],
        source_identity: tuple[int, int, int, int, bytes]) -> None:
    """Retry transient synced-folder interference without relaxing identity."""
    retry_error: OSError | None = None
    for attempt in range(len(_SYNCED_FOLDER_RETRY_DELAYS) + 1):
        if attempt:
            time.sleep(_SYNCED_FOLDER_RETRY_DELAYS[attempt - 1])
            assert_no_link_components(destination.parent)
            if _parent_identity(destination.parent) != parent_identity:
                raise StoragePolicyError(
                    "sensitive output parent changed during publication retry"
                ) from retry_error
            _validate_output_leaf(destination)
            if _temporary_identity(source) != source_identity:
                raise StoragePolicyError(
                    "private temporary file changed during publication retry"
                ) from retry_error
            _verify_private_file(source)
        try:
            replace(source, destination)
            return
        except OSError as exc:
            if (not _transient_replace_error(exc)
                    or attempt == len(_SYNCED_FOLDER_RETRY_DELAYS)):
                raise
            retry_error = exc


def _verify_private_file(path: Path) -> None:
    result = os.lstat(path)
    if not stat.S_ISREG(result.st_mode):
        raise StoragePolicyError(
            "published sensitive output is not a regular file")
    if os.name == "nt":
        if not windows_path_is_private(path, directory=False):
            raise StoragePolicyError(
                "published output did not retain its private DACL")
    elif stat.S_IMODE(result.st_mode) != PRIVATE_FILE_MODE:
        raise StoragePolicyError(
            "published output did not retain its private mode")


def _fsync_parent_directory(path: Path) -> None:
    if os.name == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(Path(path).parent, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _report_cleanup_error(
        cleanup_error_fn: CleanupErrorFn | None,
        temporary: Path, error: BaseException) -> None:
    if cleanup_error_fn is not None:
        cleanup_error_fn(
            "Private temporary-file cleanup failed for %s", temporary,
            error=error)


def _atomic_write_private_posix(
        path: Path, writer: Callable[[Any], None], *, text: bool,
        cleanup_error_fn: CleanupErrorFn | None) -> None:
    """Publish relative to a pinned POSIX directory descriptor."""
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    directory_flags |= getattr(os, "O_NOFOLLOW", 0)
    parent_descriptor = os.open(path.parent, directory_flags)
    descriptor = -1
    temporary_name: str | None = None
    temporary_path: Path | None = None
    try:
        if _parent_identity(path.parent) != (
                int(os.fstat(parent_descriptor).st_dev),
                int(os.fstat(parent_descriptor).st_ino)):
            raise StoragePolicyError(
                "sensitive output parent changed before staging")
        for _ in range(128):
            candidate = f".{path.name}.{secrets.token_hex(12)}.tmp"
            flags = os.O_RDWR | os.O_CREAT | os.O_EXCL
            flags |= getattr(os, "O_NOFOLLOW", 0)
            try:
                descriptor = os.open(
                    candidate, flags, PRIVATE_FILE_MODE,
                    dir_fd=parent_descriptor)
            except FileExistsError:
                continue
            temporary_name = candidate
            temporary_path = path.parent / candidate
            break
        else:
            raise FileExistsError(
                "could not allocate a private temporary output")

        os.fchmod(descriptor, PRIVATE_FILE_MODE)
        mode = "w" if text else "wb"
        options = {"encoding": "utf-8"} if text else {}
        with os.fdopen(descriptor, mode, **options) as handle:
            descriptor = -1
            writer(handle)
            handle.flush()
            os.fsync(handle.fileno())

        assert_no_link_components(path.parent)
        pinned = os.fstat(parent_descriptor)
        if _parent_identity(path.parent) != (
                int(pinned.st_dev), int(pinned.st_ino)):
            raise StoragePolicyError(
                "sensitive output parent changed before publication")
        try:
            existing = os.stat(
                path.name, dir_fd=parent_descriptor,
                follow_symlinks=False)
        except FileNotFoundError:
            existing = None
        if existing is not None and not stat.S_ISREG(existing.st_mode):
            raise StoragePolicyError(
                "sensitive output changed into a non-regular path")
        _NATIVE_OS_REPLACE(
            temporary_name, path.name,
            src_dir_fd=parent_descriptor, dst_dir_fd=parent_descriptor)
        temporary_name = None
        temporary_path = None
        published = os.stat(
            path.name, dir_fd=parent_descriptor, follow_symlinks=False)
        if (not stat.S_ISREG(published.st_mode)
                or stat.S_IMODE(published.st_mode) != PRIVATE_FILE_MODE):
            raise StoragePolicyError(
                "published output did not retain its private mode")
        os.fsync(parent_descriptor)
    except BaseException:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name, dir_fd=parent_descriptor)
            except BaseException as cleanup_error:
                _report_cleanup_error(
                    cleanup_error_fn, temporary_path, cleanup_error)
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent_descriptor)


def _atomic_write_private_by_path(
        path: Path, writer: Callable[[Any], None], *, text: bool,
        replace: ReplaceFn,
        cleanup_error_fn: CleanupErrorFn | None) -> None:
    """Publish on platforms without POSIX relative replacement support."""
    parent_identity = _parent_identity(path.parent)
    temporary: Path | None = None
    mode = "w" if text else "wb"
    options = {"encoding": "utf-8"} if text else {}
    try:
        with tempfile.NamedTemporaryFile(
                mode=mode, dir=path.parent, prefix=f".{path.name}.",
                suffix=".tmp", delete=False, **options) as handle:
            temporary = Path(handle.name)
            # On Windows this happens before any sensitive bytes are written.
            enforce_private_path(temporary, directory=False)
            writer(handle)
            handle.flush()
            os.fsync(handle.fileno())
        temporary_identity = _temporary_identity(temporary)
        assert_no_link_components(path.parent)
        if _parent_identity(path.parent) != parent_identity:
            raise StoragePolicyError(
                "sensitive output parent changed before publication")
        _validate_output_leaf(path)
        _replace_with_revalidated_retry(
            temporary, path, replace=replace,
            parent_identity=parent_identity,
            source_identity=temporary_identity)
        temporary = None
        _verify_private_file(path)
        _fsync_parent_directory(path)
    except BaseException:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except BaseException as cleanup_error:
                _report_cleanup_error(
                    cleanup_error_fn, temporary, cleanup_error)
        raise


def atomic_write_private(
        path: Path, writer: Callable[[Any], None], *, text: bool,
        replace_fn: ReplaceFn | None = None,
        cleanup_error_fn: CleanupErrorFn | None = None) -> None:
    """Publish one private file atomically after parent-identity checks."""
    path, _ = _prepare_private_output(path)
    replace = _NATIVE_OS_REPLACE if replace_fn is None else replace_fn
    if os.name != "nt" and replace is _NATIVE_OS_REPLACE:
        _atomic_write_private_posix(
            path, writer, text=text, cleanup_error_fn=cleanup_error_fn)
        return
    _atomic_write_private_by_path(
        path, writer, text=text, replace=replace,
        cleanup_error_fn=cleanup_error_fn)


def atomic_write_private_text(
        path: Path, content: str, *, replace_fn: ReplaceFn | None = None,
        cleanup_error_fn: CleanupErrorFn | None = None) -> None:
    atomic_write_private(
        path, lambda handle: handle.write(content), text=True,
        replace_fn=replace_fn, cleanup_error_fn=cleanup_error_fn)


def atomic_publish_private_file(
        path: Path, producer: PathProducerFn, *,
        replace_fn: ReplaceFn | None = None,
        cleanup_error_fn: CleanupErrorFn | None = None) -> None:
    """Atomically publish output from a pathname-only library.

    The producer receives a new random staging path in an already-private
    parent. It must create exactly one regular file at that path. This is for
    APIs such as PDF writers that cannot target an existing open handle.
    """
    path, parent_identity = _prepare_private_output(path)
    replace = _NATIVE_OS_REPLACE if replace_fn is None else replace_fn
    temporary = path.parent / (
        f".{path.name}.{secrets.token_hex(16)}.staging")
    if temporary.exists() or _is_link_like(temporary):
        raise FileExistsError("private staging path unexpectedly exists")
    published = False
    try:
        producer(temporary)
        result = os.lstat(temporary)
        if not stat.S_ISREG(result.st_mode) or result.st_nlink != 1:
            raise StoragePolicyError(
                "private producer must create one unlinked regular file")
        enforce_private_path(temporary, directory=False)
        flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(temporary, flags)
        try:
            verified = os.fstat(descriptor)
            if (not stat.S_ISREG(verified.st_mode)
                    or verified.st_nlink != 1
                    or (verified.st_dev, verified.st_ino) !=
                    (result.st_dev, result.st_ino)):
                raise StoragePolicyError(
                    "private staging file changed before publication")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        temporary_identity = _temporary_identity(temporary)
        assert_no_link_components(path.parent)
        if _parent_identity(path.parent) != parent_identity:
            raise StoragePolicyError(
                "sensitive output parent changed before publication")
        _validate_output_leaf(path)
        _replace_with_revalidated_retry(
            temporary, path, replace=replace,
            parent_identity=parent_identity,
            source_identity=temporary_identity)
        published = True
        _verify_private_file(path)
        _fsync_parent_directory(path)
    except BaseException:
        if not published:
            try:
                temporary.unlink(missing_ok=True)
            except BaseException as cleanup_error:
                _report_cleanup_error(
                    cleanup_error_fn, temporary, cleanup_error)
        raise


def atomic_write_private_json(
        path: Path, payload: object, *, indent: int | None = None,
        replace_fn: ReplaceFn | None = None,
        cleanup_error_fn: CleanupErrorFn | None = None) -> None:
    def write(handle) -> None:
        json.dump(
            payload, handle, ensure_ascii=False, sort_keys=True,
            separators=None if indent is not None else (",", ":"),
            indent=indent)
        if indent is not None:
            handle.write("\n")

    atomic_write_private(
        path, write, text=True, replace_fn=replace_fn,
        cleanup_error_fn=cleanup_error_fn)


def jsonl_lines(contents: str) -> list[str]:
    """Split one decoded JSONL artifact into its physical records.

    ``str.splitlines`` also breaks on U+0085, U+2028, and U+2029, which JSON
    treats as ordinary string characters and which ``json.dumps`` leaves
    literal under ``ensure_ascii=False``.  Splitting there would tear a record
    that this module itself wrote, so the reader recognizes only the ``\\r\\n``
    and ``\\n`` terminators that :func:`atomic_write_private` can emit.
    """
    return contents.replace("\r\n", "\n").split("\n")


def atomic_write_private_jsonl(
        path: Path, records: Iterable[dict], *,
        replace_fn: ReplaceFn | None = None,
        cleanup_error_fn: CleanupErrorFn | None = None,
        compact: bool = False) -> None:
    def write(handle) -> None:
        for record in records:
            options = ({"sort_keys": True, "separators": (",", ":")}
                       if compact else {})
            handle.write(json.dumps(
                record, ensure_ascii=False, **options) + "\n")

    atomic_write_private(
        path, write, text=True, replace_fn=replace_fn,
        cleanup_error_fn=cleanup_error_fn)


def _append_private_jsonl_posix(
        path: Path, encoded: bytes, parent_identity: tuple[int, int]) -> None:
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    directory_flags |= getattr(os, "O_NOFOLLOW", 0)
    parent_descriptor = os.open(path.parent, directory_flags)
    descriptor = -1
    try:
        pinned = os.fstat(parent_descriptor)
        if parent_identity != (int(pinned.st_dev), int(pinned.st_ino)):
            raise StoragePolicyError(
                "private append parent changed before opening the file")
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(
                path.name, flags, PRIVATE_FILE_MODE,
                dir_fd=parent_descriptor)
        except OSError as exc:
            if _is_link_like(path):
                raise StoragePolicyError(
                    "private append target cannot be a link") from exc
            raise
        result = os.fstat(descriptor)
        if not stat.S_ISREG(result.st_mode) or result.st_nlink != 1:
            raise StoragePolicyError(
                "private append target must be one regular, unlinked file")
        os.fchmod(descriptor, PRIVATE_FILE_MODE)
        assert_no_link_components(path.parent)
        if _parent_identity(path.parent) != parent_identity:
            raise StoragePolicyError(
                "private append parent changed before the write")
        written = os.write(descriptor, encoded)
        if written != len(encoded):
            raise OSError("private event append was incomplete")
        os.fsync(descriptor)
        os.fsync(parent_descriptor)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent_descriptor)


def _append_private_jsonl_windows(
        path: Path, encoded: bytes, parent_identity: tuple[int, int]) -> None:
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create_file.restype = wintypes.HANDLE
    get_information = kernel32.GetFileInformationByHandle
    write_file = kernel32.WriteFile
    flush_file = kernel32.FlushFileBuffers
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL

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
    write_file.argtypes = [
        wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    write_file.restype = wintypes.BOOL
    flush_file.argtypes = [wintypes.HANDLE]
    flush_file.restype = wintypes.BOOL

    file_append_data = 0x00000004
    read_control = 0x00020000
    write_dac = 0x00040000
    file_share_read = 0x00000001
    file_share_write = 0x00000002
    open_always = 4
    file_attribute_normal = 0x00000080
    file_flag_open_reparse_point = 0x00200000
    invalid_handle = ctypes.c_void_p(-1).value
    handle = create_file(
        str(path), file_append_data | read_control | write_dac,
        file_share_read | file_share_write, None, open_always,
        file_attribute_normal | file_flag_open_reparse_point, None)
    if handle == invalid_handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        information = ByHandleFileInformation()
        if not get_information(handle, ctypes.byref(information)):
            raise ctypes.WinError(ctypes.get_last_error())
        file_attribute_directory = 0x00000010
        file_attribute_reparse_point = 0x00000400
        if (information.file_attributes & (
                file_attribute_directory | file_attribute_reparse_point)
                or information.number_of_links != 1):
            raise StoragePolicyError(
                "private append target must be one regular, unlinked file")
        _windows_apply_private_dacl_handle(handle)
        # The open handle denies delete/rename sharing, so normalizing the
        # path descriptor cannot be redirected after the handle checks.
        _windows_apply_private_dacl(path, directory=False)
        _verify_private_file(path)
        assert_no_link_components(path.parent)
        if _parent_identity(path.parent) != parent_identity:
            raise StoragePolicyError(
                "private append parent changed before the write")
        buffer = ctypes.create_string_buffer(encoded)
        written = wintypes.DWORD()
        if not write_file(
                handle, buffer, len(encoded), ctypes.byref(written), None):
            raise ctypes.WinError(ctypes.get_last_error())
        if written.value != len(encoded):
            raise OSError("private event append was incomplete")
        if not flush_file(handle):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        close_handle(handle)
    _verify_private_file(path)


def append_private_jsonl(path: Path, payload: dict) -> None:
    """Append one private JSON event without following the final path."""
    path, parent_identity = _prepare_private_output(path)
    encoded = (json.dumps(
        payload, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")) + "\n").encode("utf-8")
    if os.name == "nt":
        _append_private_jsonl_windows(path, encoded, parent_identity)
    else:
        _append_private_jsonl_posix(path, encoded, parent_identity)


@contextmanager
def open_private_append(
        path: Path, *, text: bool = False) -> Iterator[Any]:
    """Open one verified private regular file for streaming append.

    The returned handle is suitable for direct ``subprocess`` stdout/stderr
    redirection. The parent is private, links and hardlinks are rejected, the
    descriptor is owner-only, and publication identity is checked again when
    the context exits.
    """
    path, parent_identity = _prepare_private_output(path)
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    if not text:
        flags |= getattr(os, "O_BINARY", 0)
    descriptor = os.open(path, flags, PRIVATE_FILE_MODE)
    handle = None
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            raise StoragePolicyError(
                "private stream target must be one regular, unlinked file")
        if os.name == "nt":
            # New files inherit the already-private parent DACL. Normalize the
            # pathname while this descriptor pins the verified regular file;
            # the private parent excludes cross-user replacement races.
            _windows_apply_private_dacl(path, directory=False)
        else:
            os.fchmod(descriptor, PRIVATE_FILE_MODE)
        assert_no_link_components(path.parent)
        if _parent_identity(path.parent) != parent_identity:
            raise StoragePolicyError(
                "private stream parent changed before use")
        published = os.stat(path, follow_symlinks=False)
        identity = (int(opened.st_dev), int(opened.st_ino))
        if (int(published.st_dev), int(published.st_ino)) != identity:
            raise StoragePolicyError(
                "private stream target changed while opening")

        if text:
            handle = os.fdopen(descriptor, "a", encoding="utf-8", newline="")
        else:
            handle = os.fdopen(descriptor, "ab", buffering=0)
        descriptor = -1
        try:
            yield handle
        finally:
            handle.flush()
            os.fsync(handle.fileno())
            final_handle = os.fstat(handle.fileno())
            final_path = os.stat(path, follow_symlinks=False)
            if (
                not stat.S_ISREG(final_handle.st_mode)
                or final_handle.st_nlink != 1
                or (int(final_handle.st_dev), int(final_handle.st_ino)) !=
                identity
                or (int(final_path.st_dev), int(final_path.st_ino)) != identity
            ):
                raise StoragePolicyError(
                    "private stream target changed before close")
    finally:
        if handle is not None:
            handle.close()
        elif descriptor >= 0:
            os.close(descriptor)
    _verify_private_file(path)
    _fsync_parent_directory(path)


def harden_private_tree(root: Path) -> dict[str, int]:
    """Apply the private policy recursively without traversing links."""
    root = ensure_private_directory(root)
    directories = 1
    files = 0
    for current, directory_names, file_names in os.walk(
            root, topdown=True, followlinks=False):
        current_path = Path(current)
        kept = []
        for name in directory_names:
            candidate = current_path / name
            if _is_link_like(candidate):
                raise StoragePolicyError(
                    "private trees cannot contain links or junctions")
            enforce_private_path(candidate, directory=True)
            directories += 1
            kept.append(name)
        directory_names[:] = kept
        for name in file_names:
            candidate = current_path / name
            if _is_link_like(candidate):
                raise StoragePolicyError(
                    "private trees cannot contain symbolic links")
            result = os.lstat(candidate)
            if (not stat.S_ISREG(result.st_mode)
                    or result.st_nlink != 1):
                raise StoragePolicyError(
                    "private trees can contain only unlinked regular files")
            enforce_private_path(candidate, directory=False)
            files += 1
    return {"directories": directories, "files": files}


def _private_tree_policy_marker(root: Path) -> Path:
    identity = os.path.normcase(str(_absolute(root))).encode("utf-8")
    digest = hashlib.sha256(identity).hexdigest()
    return (
        root.parent / PRIVATE_TREE_POLICY_DIRECTORY_NAME
        / f"{digest}.json"
    )


def _private_tree_policy_matches(marker: Path, root: Path) -> bool:
    try:
        if _is_link_like(marker):
            return False
        result = os.lstat(marker)
        if (not stat.S_ISREG(result.st_mode) or result.st_nlink != 1
                or result.st_size <= 0 or result.st_size > 16 * 1024):
            return False
        enforce_private_path(marker, directory=False)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        flags |= getattr(os, "O_BINARY", 0)
        descriptor = os.open(marker, flags)
        try:
            opened = os.fstat(descriptor)
            if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                    or (opened.st_dev, opened.st_ino) !=
                    (result.st_dev, result.st_ino)):
                return False
            chunks = []
            remaining = int(opened.st_size)
            while remaining:
                chunk = os.read(descriptor, remaining)
                if not chunk:
                    return False
                chunks.append(chunk)
                remaining -= len(chunk)
            after = os.fstat(descriptor)
            if (
                int(after.st_dev), int(after.st_ino), int(after.st_size),
                int(after.st_mtime_ns), int(after.st_ctime_ns),
                int(after.st_nlink),
            ) != (
                int(opened.st_dev), int(opened.st_ino), int(opened.st_size),
                int(opened.st_mtime_ns), int(opened.st_ctime_ns),
                int(opened.st_nlink),
            ):
                return False
            raw = b"".join(chunks)
        finally:
            os.close(descriptor)
        payload = json.loads(raw.decode("utf-8"))
        root_result = os.lstat(root)
        return (
            isinstance(payload, dict)
            and payload.get("schema_version") ==
            PRIVATE_TREE_POLICY_SCHEMA_VERSION
            and payload.get("kind") == "private_tree_policy"
            and payload.get("root_device") == int(root_result.st_dev)
            and payload.get("root_inode") == int(root_result.st_ino)
        )
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False


def ensure_private_tree(root: Path) -> Path:
    """Migrate existing descendants once, then enforce the private root.

    A root-identity marker is kept in a private sibling metadata directory so
    vector-store libraries never see policy bookkeeping in their database.
    New Windows descendants inherit the root's current-user-only DACL; on
    POSIX the owner-only root prevents traversal while direct artifacts retain
    their individually enforced modes.
    """
    root = ensure_private_directory(root)
    marker = _private_tree_policy_marker(root)
    with _PRIVATE_TREE_POLICY_GUARD:
        marker_parent = ensure_private_directory(marker.parent)
        marker = marker_parent / marker.name
        if _private_tree_policy_matches(marker, root):
            return root
        harden_private_tree(root)
        root_result = os.lstat(root)
        atomic_write_private_json(marker, {
            "schema_version": PRIVATE_TREE_POLICY_SCHEMA_VERSION,
            "kind": "private_tree_policy",
            "root_device": int(root_result.st_dev),
            "root_inode": int(root_result.st_ino),
        })
    return root
