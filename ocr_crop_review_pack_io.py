"""Private create-only crop packs and bounded host-owned reopen capabilities.

Paths belong to the operator/host, never browser inputs. The fixed storage root
must already exist. Discovery assigns fresh opaque IDs and does not authenticate
historical declarations, select a pack, restore consent, or load an OCR runtime.
Incomplete publications are retained. There is no automatic cleanup or repair.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import stat
import threading
import uuid

from evaluation_inputs import _read_snapshot, _strict_json_bytes
import ocr_crop_review_pack as policy
from ocr_recovery import ReportCleanupError, _publish_new_report
from resource_lease import PathLease
import storage_policy


_HEX = re.compile(r"[0-9a-f]{64}\Z")
_ID = re.compile(r"[0-9a-f]{32}\Z")
_DIRECTORY = re.compile(r"pack-[0-9a-f]{32}\Z")
_CODES = frozenset({"invalid_pack", "input_changed", "storage_unavailable",
    "pack_exists", "pack_incomplete", "cleanup_uncertain", "catalog_full", "unknown_pack"})
_STATES = frozenset({"not_created", "incomplete", "present_unverified", "verified_complete",
                     "cleanup_uncertain"})


class CropReviewPackIOError(ValueError):
    """Static private notice; a failed commit may have left retained evidence."""

    def __init__(self, code="storage_unavailable", *, status="not_created", pack_id=None):
        self.code = code if type(code) is str and code in _CODES else "storage_unavailable"
        self.status = status if type(status) is str and status in _STATES else "incomplete"
        self.pack_id = pack_id if type(pack_id) is str and _ID.fullmatch(pack_id) else None
        super().__init__("Crop review pack is unavailable; retained data was not removed.")


def _fail(code="invalid_pack", *, status="not_created", pack_id=None):
    raise CropReviewPackIOError(code, status=status, pack_id=pack_id)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _identity(info):
    # Match the existing snapshot policy: Windows pathname/handle ctime may
    # differ without a content-generation change. Both byte passes still hash.
    fields = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
    return fields if os.name == "nt" else fields + (info.st_ctime_ns,)


def _directory(path):
    try:
        storage_policy.assert_no_link_components(path)
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode):
            _fail("storage_unavailable")
        return info.st_dev, info.st_ino
    except CropReviewPackIOError:
        raise
    except Exception:
        _fail("storage_unavailable")


def _root_identity(value):
    if (type(value) is not tuple or len(value) != 2
            or any(type(n) is not int or n < 0 for n in value)):
        _fail()
    return value


def _check_root(path, expected):
    if _directory(path) != expected:
        _fail("input_changed")


def _file_info(path, limit):
    storage_policy.assert_no_link_components(path)
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or not 1 <= info.st_size <= limit):
        _fail()
    return _identity(info)


def _snapshot(path, limit, *, expected=None):
    before = _file_info(path, limit)
    if expected is not None and before != expected:
        _fail("input_changed")
    # Never allow growth to the much larger per-slot cap after cohort preflight.
    raw, digest = _read_snapshot(path, label="crop review pack", max_bytes=before[2])
    if _file_info(path, limit) != before or len(raw) != before[2]:
        _fail("input_changed")
    return raw, digest, before


def _host_check(callback):
    try:
        result = callback()
        if result is False:
            _fail("input_changed")
    except CropReviewPackIOError:
        raise
    except Exception:
        _fail("input_changed")


def _binding(source_sha256, recovery_sha256, page_count, verify_inputs):
    if (type(source_sha256) is not str or not _HEX.fullmatch(source_sha256)
            or type(recovery_sha256) is not str or not _HEX.fullmatch(recovery_sha256)
            or type(page_count) is not int or not 1 <= page_count <= 5000
            or not callable(verify_inputs)):
        _fail()
    return dict(source_sha256=source_sha256, recovery_sha256=recovery_sha256, page_count=page_count)


def _pack_functions(manifest):
    """Select an explicit closed version; never infer it from review content.

    Version 1 retains the original policy and bytes. Version 2 is an outward
    pure dependency loaded only for that requested historical declaration.
    Storage bounds, fixed slots and commit/recovery I/O are shared unchanged.
    """
    if (type(manifest) is not dict or len(manifest) != len(policy._MANIFEST_KEYS)
            or any(type(key) is not str for key in manifest)
            or type(manifest.get("schema_version")) is not int
            or manifest["schema_version"] not in (1, 2)):
        _fail()
    if manifest["schema_version"] == 1:
        return (policy.validate_crop_pack_manifest, policy.validate_crop_review_pack,
                policy.recover_crop_pack_draft)
    from ocr_crop_uncertainty_pack import (validate_crop_pack_manifest_v2,
        validate_crop_review_pack_v2, recover_crop_pack_draft_v2)
    return (validate_crop_pack_manifest_v2, validate_crop_review_pack_v2,
            recover_crop_pack_draft_v2)


def _manifest(raw, binding):
    value = _strict_json_bytes(raw, label="crop review pack", max_bytes=policy.MAX_MANIFEST_BYTES)
    validate, _, _ = _pack_functions(value)
    value = validate(value, **binding)
    # One unambiguous pack identity, including canonical whitespace and no LF.
    if policy.encode_manifest(value) != raw:
        _fail()
    return value


def _expected_tree(names):
    tree = {"": set()}
    for name in names:
        parts = name.split("/")
        for index, part in enumerate(parts):
            parent = "/".join(parts[:index])
            tree.setdefault(parent, set()).add(part)
            if index < len(parts) - 1:
                tree.setdefault("/".join(parts[:index + 1]), set())
    return tree


def _preflight(path, manifest, manifest_size, *, expected_root):
    """Inspect all sizes/identities before retaining the payload byte cohort."""
    _check_root(path, expected_root)
    descriptors = manifest["files"]
    if manifest_size + sum(d["bytes"] for d in descriptors.values()) > policy.MAX_PACK_BYTES:
        _fail()
    names = set(descriptors) | {"manifest.json"}
    tree = _expected_tree(names)
    directories, files = {}, {}
    for relative in sorted(tree, key=lambda value: (value.count("/"), value)):
        folder = path / relative
        storage_policy.assert_no_link_components(folder)
        directories[relative] = _directory(folder)
        observed = set()
        with os.scandir(folder) as entries:
            for entry in entries:
                # Bound enumeration before accumulation, even in hostile dirs.
                if len(observed) >= len(tree[relative]) or entry.name not in tree[relative]:
                    _fail("pack_incomplete", status="incomplete")
                observed.add(entry.name)
        if observed != tree[relative]:
            _fail("pack_incomplete", status="incomplete")
    total = 0
    for name in sorted(names):
        limit = policy.MAX_MANIFEST_BYTES if name == "manifest.json" else policy.SLOT_LIMITS[name]
        leaf = path / name
        storage_policy.assert_no_link_components(leaf)
        info = _file_info(leaf, limit)
        expected_size = manifest_size if name == "manifest.json" else descriptors[name]["bytes"]
        if info[2] != expected_size:
            _fail("input_changed")
        total += info[2]
        if total > policy.MAX_PACK_BYTES:
            _fail()
        files[name] = info
    _check_root(path, expected_root)
    return directories, files


def _recheck(path, manifest, directories, identities, *, manifest_sha256):
    for name, identity in directories.items():
        _check_root(path / name, identity)
    for name, identity in identities.items():
        limit = policy.MAX_MANIFEST_BYTES if name == "manifest.json" else policy.SLOT_LIMITS[name]
        _, digest, _ = _snapshot(path / name, limit, expected=identity)
        expected = manifest_sha256 if name == "manifest.json" else manifest["files"][name]["sha256"]
        if digest != expected:
            _fail("input_changed")
    # Detect appended files/directories too, not merely changes to known files.
    current_dirs, current_files = _preflight(path, manifest,
        identities["manifest.json"][2], expected_root=directories[""])
    if current_dirs != directories or current_files != identities:
        _fail("input_changed")


def _read(path, *, expected_manifest_sha256, expected_root_identity, binding,
          verify_inputs, recovery_only=False, include_files=False):
    if recovery_only and include_files:
        _fail()
    if type(expected_manifest_sha256) is not str or not _HEX.fullmatch(expected_manifest_sha256):
        _fail()
    expected_root_identity = _root_identity(expected_root_identity)
    path = Path(os.path.abspath(path))
    _host_check(verify_inputs)
    _check_root(path, expected_root_identity)
    raw, digest, manifest_identity = _snapshot(path / "manifest.json", policy.MAX_MANIFEST_BYTES)
    if digest != expected_manifest_sha256:
        _fail("input_changed")
    manifest = _manifest(raw, binding)
    if recovery_only:
        # Recovery is deliberately a different admission boundary. Broken,
        # oversized or link-like unrelated proof must not prevent recovery of
        # the two independently pinned authored-state files. Do not traverse it.
        review_path = path / "review.json"
        review_raw, review_digest, review_identity = _snapshot(review_path, policy.MAX_REVIEW_BYTES)
        descriptor = manifest["files"]["review.json"]
        if len(review_raw) != descriptor["bytes"] or review_digest != descriptor["sha256"]:
            _fail("input_changed")
        _, _, recover = _pack_functions(manifest)
        result = recover(manifest, review_raw, **binding)
        _host_check(verify_inputs)
        if (_snapshot(path / "manifest.json", policy.MAX_MANIFEST_BYTES, expected=manifest_identity)[1] != digest
                or _snapshot(review_path, policy.MAX_REVIEW_BYTES, expected=review_identity)[1] != review_digest):
            _fail("input_changed")
        _host_check(verify_inputs)
        _check_root(path, expected_root_identity)
        return result
    directories, identities = _preflight(path, manifest, len(raw),
        expected_root=expected_root_identity)
    if identities.get("manifest.json") != manifest_identity:
        _fail("input_changed")
    files = {}
    for name in sorted(manifest["files"]):
        if name not in identities:
            _fail("pack_incomplete", status="incomplete")
        item, item_digest, _ = _snapshot(path / name, policy.SLOT_LIMITS[name], expected=identities[name])
        if item_digest != manifest["files"][name]["sha256"]:
            _fail("input_changed")
        files[name] = item
    _, validate, _ = _pack_functions(manifest)
    result = validate(manifest, files=files, **binding)
    _host_check(verify_inputs)
    _recheck(path, manifest, directories, identities, manifest_sha256=digest)
    _host_check(verify_inputs)
    _check_root(path, expected_root_identity)
    if include_files:
        return {"evidence": result, "pack": {"manifest": manifest, "files": files},
                "manifest_bytes": raw}
    return result


def read_crop_review_pack(path, *, expected_manifest_sha256, expected_root_identity,
                          source_sha256, recovery_sha256, page_count, verify_inputs):
    """Operator-only fixed path read, pinned to the pack directory's dev/ino.

    Returns full detached evidence only after strict historical replay and final
    input/file checks. Neither the path nor the expected pins come from a web
    field. Manifest hashes establish consistency, not authentication.
    """
    try:
        binding = _binding(source_sha256, recovery_sha256, page_count, verify_inputs)
        return _read(path, expected_manifest_sha256=expected_manifest_sha256,
            expected_root_identity=expected_root_identity, binding=binding, verify_inputs=verify_inputs)
    except CropReviewPackIOError:
        raise
    except Exception:
        _fail("invalid_pack", status="present_unverified")


@dataclass
class _Entry:
    path: Path
    identity: tuple[int, int]
    manifest_sha256: str | None
    status: str


class CropReviewPackStore:
    """One host's bounded catalog; IDs are fresh on restart, never filenames.

    The caller creates its fixed private root. Discovery reads only manifests;
    `read` performs complete historical verification. There is no newest/mtime
    selection, removal, fallback to live OCR, or automatic draft approval.
    """

    def __init__(self, fixed_root, *, source_sha256, recovery_sha256, page_count,
                 verify_inputs, max_packs=32):
        self._binding = _binding(source_sha256, recovery_sha256, page_count, verify_inputs)
        if type(max_packs) is not int or not 1 <= max_packs <= 128:
            _fail()
        self._root = Path(os.path.abspath(fixed_root))
        self._verify_inputs = verify_inputs
        self._maximum = max_packs
        self._entries = {}
        self._lock = threading.RLock()
        self._cleanup_uncertain = False
        try:
            _host_check(verify_inputs)
            self._root_identity = _directory(self._root)
            storage_policy.enforce_private_path(self._root, directory=True)
            self._check()
            self._discover()
            self._check()
        except CropReviewPackIOError:
            raise
        except Exception:
            _fail("storage_unavailable")

    @property
    def cleanup_uncertain(self):
        return self._cleanup_uncertain

    def _check(self):
        _host_check(self._verify_inputs)
        _check_root(self._root, self._root_identity)

    def _new_id(self):
        value = uuid.uuid4().hex
        if value in self._entries:
            _fail("pack_exists")
        return value

    def _folders(self):
        folders = []
        with os.scandir(self._root) as entries:
            for entry in entries:
                if len(folders) >= self._maximum:
                    _fail("catalog_full")
                if not _DIRECTORY.fullmatch(entry.name):
                    _fail("storage_unavailable")
                folder = self._root / entry.name
                _directory(folder)
                folders.append(folder)
        return sorted(folders)

    def _discover(self):
        folders = self._folders()
        for folder in folders:
            identity = _directory(folder)
            digest, status = None, "incomplete"
            try:
                raw, digest, _ = _snapshot(folder / "manifest.json", policy.MAX_MANIFEST_BYTES)
                _manifest(raw, self._binding)
                status = "present_unverified"
            except (OSError, ValueError):
                digest = None
            _check_root(folder, identity)
            self._entries[self._new_id()] = _Entry(folder, identity, digest, status)
        if self._folders() != folders:
            _fail("input_changed")

    def _metadata(self, pack_id, entry):
        return {"pack_id": pack_id, "manifest_sha256": entry.manifest_sha256,
                "status": entry.status, "requires_attention": True}

    def catalog(self):
        """Bounded last-observed metadata, not a fresh evidence verification."""
        with self._lock:
            self._check()
            return [self._metadata(key, entry) for key, entry in self._entries.items()]

    def _entry(self, pack_id):
        if type(pack_id) is not str or not _ID.fullmatch(pack_id) or pack_id not in self._entries:
            _fail("unknown_pack")
        return self._entries[pack_id]

    def _read_entry(self, pack_id, *, recovery_only=False, include_files=False):
        self._check()
        entry = self._entry(pack_id)
        if entry.manifest_sha256 is None or (entry.status == "cleanup_uncertain" and not recovery_only):
            _fail("pack_incomplete", status=entry.status, pack_id=pack_id)
        try:
            result = _read(entry.path, expected_manifest_sha256=entry.manifest_sha256,
                expected_root_identity=entry.identity, binding=self._binding,
                verify_inputs=self._check, recovery_only=recovery_only, include_files=include_files)
            self._check()
        except Exception as exc:
            if entry.status != "cleanup_uncertain":
                entry.status = "present_unverified"
            code = exc.code if isinstance(exc, CropReviewPackIOError) else "invalid_pack"
            _fail(code, status=entry.status, pack_id=pack_id)
        if not recovery_only:
            entry.status = "verified_complete"
        return result

    def read(self, pack_id):
        with self._lock:
            return self._read_entry(pack_id)

    def read_for_revision(self, pack_id):
        """Host-only complete read retaining its exact bounded input byte cohort.

        Returns ``{evidence, pack: {manifest, files}, manifest_bytes}``. Evidence
        has the unchanged `read` schema; payload bytes and canonical manifest
        bytes are the original verified capture, not reserialized declarations.
        All mappings are detached from store state. Immutable bytes are reused,
        not copied into a second cohort or cached by the store. Never put this
        large private host result in browser state or expose it as an event.

        The caller must build and separately publish a new immutable revision.
        This read grants no execution, scoring, adoption, or review authority.
        """
        with self._lock:
            return self._read_entry(pack_id, include_files=True)

    def recover_draft(self, pack_id):
        """Explicit recovery-only read; no candidate, metrics or saved approval."""
        with self._lock:
            return self._read_entry(pack_id, recovery_only=True)

    def check_revision_parent(self, pack_id, *, expected_pack_sha256):
        """Recheck retained parent manifest/authored bytes at a host commit.

        The host must previously obtain full evidence with read_for_revision.
        This narrow check performs no proof traversal, journal replay, scoring
        or recovery and returns no evidence or authority. It never invents a
        missing parent or accepts an ID/path from outside this host's catalog.
        """
        with self._lock:
            self._check()
            entry = self._entry(pack_id)
            if (type(expected_pack_sha256) is not str or not _HEX.fullmatch(expected_pack_sha256)
                    or entry.manifest_sha256 != expected_pack_sha256):
                _fail("input_changed", status=entry.status, pack_id=pack_id)
            if self._cleanup_uncertain or entry.status == "cleanup_uncertain":
                _fail("cleanup_uncertain", status="cleanup_uncertain", pack_id=pack_id)
            try:
                _check_root(entry.path, entry.identity)
                manifest_path = entry.path / "manifest.json"
                raw, digest, identity = _snapshot(manifest_path, policy.MAX_MANIFEST_BYTES)
                if digest != expected_pack_sha256:
                    _fail("input_changed")
                manifest = _manifest(raw, self._binding)
                review_path = entry.path / "review.json"
                review_raw, review_digest, review_identity = _snapshot(review_path, policy.MAX_REVIEW_BYTES)
                descriptor = manifest["files"]["review.json"]
                if len(review_raw) != descriptor["bytes"] or review_digest != descriptor["sha256"]:
                    _fail("input_changed")
                self._check()
                if (_snapshot(manifest_path, policy.MAX_MANIFEST_BYTES, expected=identity)[1] != digest
                        or _snapshot(review_path, policy.MAX_REVIEW_BYTES, expected=review_identity)[1] != review_digest):
                    _fail("input_changed")
                self._check()
                _check_root(entry.path, entry.identity)
            except Exception as exc:
                code = exc.code if isinstance(exc, CropReviewPackIOError) else "invalid_pack"
                _fail(code, status=entry.status, pack_id=pack_id)

    def register(self, path, *, expected_manifest_sha256, expected_root_identity):
        """Trusted operator registration, never an event accepting browser paths."""
        with self._lock:
            self._check()
            if len(self._entries) >= self._maximum:
                _fail("catalog_full")
            result = read_crop_review_pack(path, expected_manifest_sha256=expected_manifest_sha256,
                expected_root_identity=expected_root_identity, **self._binding, verify_inputs=self._check)
            self._check()
            pack_id = self._new_id()
            entry = _Entry(Path(os.path.abspath(path)), expected_root_identity,
                           result["pack_sha256"], "verified_complete")
            self._entries[pack_id] = entry
            return self._metadata(pack_id, entry)

    def publish(self, pack, *, verify_current=None):
        """Create one fresh fixed-slot directory; manifest is the final commit.

        A host callback runs before admission, each commit (including retries),
        and final strict readback. Earlier commits are hash/identity checked.
        The partial directory is retained on every failure or cancellation.
        Optional `verify_current` is a host-only per-save view/revision check;
        no callback name or serialized/client-supplied callable is accepted.
        """
        with self._lock:
            return self._publish(pack, verify_current=verify_current)

    def _publish(self, pack, *, verify_current):
        entry = None
        pack_id = None

        def check():
            self._check()
            if verify_current is not None:
                _host_check(verify_current)

        try:
            if verify_current is not None and not callable(verify_current):
                _fail()
            check()
            if self._cleanup_uncertain:
                _fail("cleanup_uncertain", status="cleanup_uncertain")
            if len(self._entries) >= self._maximum:
                _fail("catalog_full")
            if (type(pack) is not dict or len(pack) != 2
                    or any(type(key) is not str for key in pack)
                    or set(pack) != {"manifest", "files"}):
                _fail()
            _pack_functions(pack["manifest"])
            manifest_raw = policy.encode_manifest(pack["manifest"])
            manifest = _manifest(manifest_raw, self._binding)
            original_files = pack["files"]
            if (type(original_files) is not dict or len(original_files) != len(manifest["files"])
                    or any(type(key) is not str for key in original_files)
                    or set(original_files) != set(manifest["files"])):
                _fail()
            files = dict(original_files)  # Values must be immutable exact bytes.
            total = len(manifest_raw)
            for name, raw in files.items():
                descriptor = manifest["files"][name]
                if (type(raw) is not bytes or len(raw) != descriptor["bytes"]
                        or _sha(raw) != descriptor["sha256"]):
                    _fail()
                total += len(raw)
                if total > policy.MAX_PACK_BYTES:
                    _fail()
            _, validate, _ = _pack_functions(manifest)
            validate(manifest, files=files, **self._binding)
            check()
            pack_id = self._new_id()
            destination = self._root / ("pack-" + uuid.uuid4().hex)
            # Lease the fixed root: its sentinel stays in the parent, outside
            # the exact pack catalog. No cooperating publisher shares a slot.
            with PathLease(self._root, backend="ocr-crop-pack", collection_name="pack",
                           operation="publish", timeout=0,
                           cleanup_error_fn=self._cleanup_error):
                check()
                # Other host stores can publish after this store's discovery.
                # Check the current bounded root while holding its shared lease.
                if len(self._folders()) >= self._maximum:
                    _fail("catalog_full")
                try:
                    destination.mkdir(mode=0o700, exist_ok=False)
                except FileExistsError:
                    _fail("pack_exists")
                except BaseException:
                    # A failed mkdir can still have created an unowned path.
                    self._cleanup_uncertain = True
                    raise
                try:
                    identity = _directory(destination)
                except BaseException:
                    self._cleanup_uncertain = True
                    raise
                entry = _Entry(destination, identity, None, "incomplete")
                self._entries[pack_id] = entry
                storage_policy.enforce_private_path(destination, directory=True)
                folders = {"": identity}
                for relative in sorted(_expected_tree(files), key=lambda value: (value.count("/"), value)):
                    if not relative:
                        continue
                    check()
                    for existing, expected in folders.items():
                        _check_root(destination / existing, expected)
                    folder = destination / relative
                    folder.mkdir(mode=0o700, exist_ok=False)
                    folder_identity = _directory(folder)
                    storage_policy.enforce_private_path(folder, directory=True)
                    _check_root(folder, folder_identity)
                    folders[relative] = folder_identity
                committed = {}

                def recheck():
                    check()
                    for relative, expected in folders.items():
                        _check_root(destination / relative, expected)
                    for name, (digest, info) in committed.items():
                        limit = policy.MAX_MANIFEST_BYTES if name == "manifest.json" else policy.SLOT_LIMITS[name]
                        if _snapshot(destination / name, limit, expected=info)[1] != digest:
                            _fail("input_changed")

                for name in [*sorted(files), "manifest.json"]:
                    recheck()
                    raw = manifest_raw if name == "manifest.json" else files[name]
                    digest = _sha(raw)
                    limit = policy.MAX_MANIFEST_BYTES if name == "manifest.json" else policy.SLOT_LIMITS[name]

                    def commit(temporary, target):
                        recheck()
                        if Path(target) != destination / name or _snapshot(Path(temporary), limit)[1] != digest:
                            _fail("input_changed")
                        _publish_new_report(Path(temporary), Path(target))

                    storage_policy.atomic_write_private(destination / name,
                        lambda handle: handle.write(raw), text=False, replace_fn=commit,
                        cleanup_error_fn=self._cleanup_error)
                    item, current, info = _snapshot(destination / name, limit)
                    if item != raw or current != digest:
                        _fail("input_changed")
                    committed[name] = (current, info)
                    if name == "manifest.json":
                        entry.manifest_sha256 = digest
                        entry.status = "present_unverified"
                recheck()
                _read(entry.path, expected_manifest_sha256=entry.manifest_sha256,
                    expected_root_identity=entry.identity, binding=self._binding, verify_inputs=check)
            if self._cleanup_uncertain:
                _fail("cleanup_uncertain", status="cleanup_uncertain", pack_id=pack_id)
            check()
            entry.status = "verified_complete"
            return self._metadata(pack_id, entry)
        except BaseException as exc:
            if isinstance(exc, ReportCleanupError):
                self._cleanup_uncertain = True
            if entry is not None:
                entry.status = "cleanup_uncertain" if self._cleanup_uncertain else (
                    "present_unverified" if entry.manifest_sha256 else "incomplete")
            if not isinstance(exc, Exception):
                raise  # Never convert cancellation into a recoverable item error.
            code = ("cleanup_uncertain" if self._cleanup_uncertain else
                    exc.code if isinstance(exc, CropReviewPackIOError) else
                    "pack_exists" if isinstance(exc, FileExistsError) else "storage_unavailable")
            _fail(code, status=entry.status if entry else (
                "cleanup_uncertain" if self._cleanup_uncertain else "not_created"), pack_id=pack_id)

    def _cleanup_error(self, *_args, **_kwargs):
        self._cleanup_uncertain = True
