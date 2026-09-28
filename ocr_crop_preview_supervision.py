"""Host-owned contained original-crop previews with known-file cleanup.

The 30-second worker deadline excludes host input checks/startup and bounded
termination grace. This is process containment, not a total-memory or OS sandbox.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
from uuid import uuid4

import ocr_crop_preview_worker as protocol
import ocr_crop_review_runtime as runtime
from ocr_review_runtime import ReviewWorkspace
import process_supervision
import storage_policy


WORKER_TIMEOUT_SECONDS = 30.0
CLOSE_TIMEOUT_SECONDS = 40.0
_SCRIPT = Path(__file__).with_name("ocr_crop_preview_worker.py")


class _Cancelled(KeyboardInterrupt):
    pass


class _CleanupUnconfirmed(RuntimeError):
    pass


@dataclass
class _Active:
    cancel: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)


def _environment():
    # Remove inherited Python startup/import selection and the review credential.
    # The shared supervisor alone restores the Windows venv launcher variable.
    removed = {name: None for name in os.environ if name.upper().startswith(("PYTHON", "_PYTHON"))
               or name.upper() in {"__PYVENV_LAUNCHER__", "RAG_OCR_REVIEW_TOKEN", "GRADIO_TEMP_DIR"}}
    removed.update({protocol.CHILD_ENV: "1", "PYTHONNOUSERSITE": "1", "PYTHONUTF8": "1",
        "PYTHONDONTWRITEBYTECODE": "1", "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1", "GRADIO_ANALYTICS_ENABLED": "False"})
    return removed


def _temporary_parent(output_path):
    try:
        parent = Path(tempfile.gettempdir())
        if not parent.is_absolute() or ".." in parent.parts:
            protocol._fail()
        excluded = [protocol.ROOT, output_path]
        cache = os.environ.get("GRADIO_TEMP_DIR")
        if cache:
            excluded.append(Path(os.path.abspath(cache)))
        if any(parent.is_relative_to(path) for path in excluded):
            protocol._fail()
        return parent, protocol._identity(parent, directory=True)
    except runtime.CropPreviewError:
        raise
    except Exception:
        protocol._fail()


class CropPreviewController:
    """One private preview at a time; no client paths, models or launch options."""

    def __init__(self, workspace):
        if type(workspace) is not ReviewWorkspace:
            protocol._fail()
        self._workspace = workspace
        self._binding = runtime._binding(workspace)
        self._output_path = workspace.output_dir
        self._output_identity = workspace._directory_generation()
        # Fixed host-local scratch selection, not a browser/caller path. Never
        # harden the shared temp parent or stage source bytes in the UI cache.
        self._storage_parent, self._storage_parent_identity = _temporary_parent(self._output_path)
        self._lock, self._close_lock = threading.Lock(), threading.Lock()
        self._active = None
        self._closed = self._uncertain = self._root_removed = False
        self._private_root = self._storage_parent / ("ocr-crop-preview-private-" + uuid4().hex)
        self._root_identity = None
        mkdir_attempted = False
        try:
            storage_policy.assert_no_link_components(self._private_root)
            mkdir_attempted = True
            self._private_root.mkdir(mode=0o700)
            self._root_identity = protocol._identity(self._private_root, directory=True)
            storage_policy.ensure_private_directory(self._private_root, harden_existing=True)
            self._verify_root()
        except BaseException:
            if self._root_identity is not None:
                try:
                    self._verify_storage()
                    self._private_root.rmdir()
                except BaseException:
                    protocol._fail("cleanup_unconfirmed")
            elif mkdir_attempted:
                protocol._fail("cleanup_unconfirmed")
            raise

    @property
    def private_root(self):
        return self._private_root

    @property
    def cleanup_uncertain(self):
        with self._lock:
            return self._uncertain

    def _latch(self):
        with self._lock:
            self._uncertain = True

    def _verify_storage(self):
        if (protocol._identity(self._storage_parent, directory=True) != self._storage_parent_identity
                or protocol._identity(self._private_root, directory=True) != self._root_identity):
            protocol._fail("input_changed")

    def _verify_root(self):
        self._verify_storage()
        if (runtime._binding(self._workspace) != self._binding
                or protocol._identity(self._output_path, directory=True) != self._output_identity
                or self._workspace._directory_generation() != self._output_identity):
            protocol._fail("input_changed")

    def _cancelled(self, active, callback):
        if active.cancel.is_set():
            return True
        if callback is None:
            return False
        value = callback()
        if type(value) is not bool:
            protocol._fail("preview_unavailable")
        if value:
            active.cancel.set()
        return value

    def _check_cancel(self, active, callback):
        if self._cancelled(active, callback):
            raise _Cancelled()

    def _cleanup_leaf(self, leaf, identity, owned):
        self._verify_storage()
        if protocol._identity(leaf, directory=True) != identity:
            raise _CleanupUnconfirmed()
        # Enumeration is bounded. Never delete unrecognized residue or descend.
        import itertools
        entries = list(itertools.islice(leaf.iterdir(), len(protocol.FILES) + 1))
        if len(entries) != len(owned) or {p.name for p in entries} != set(owned):
            raise _CleanupUnconfirmed()
        for name, expected in owned.items():
            if protocol._identity(leaf / name) != expected:
                raise _CleanupUnconfirmed()
        for name, expected in owned.items():
            self._verify_storage()
            if (protocol._identity(leaf, directory=True) != identity
                    or protocol._identity(leaf / name) != expected):
                raise _CleanupUnconfirmed()
            (leaf / name).unlink()
        self._verify_storage()
        if protocol._identity(leaf, directory=True) != identity:
            raise _CleanupUnconfirmed()
        leaf.rmdir()

    def _read_result(self, leaf, request, request_sha, code):
        protocol._check_files(leaf, request)
        result_limit = protocol._result_limit(request)
        version = protocol._schema_version(request.get("schema_version", protocol.PROTOCOL_SCHEMA_VERSION))
        metadata = version == protocol.METADATA_PROTOCOL_SCHEMA_VERSION
        result_raw, result_sha, result_identity = runtime._snapshot(leaf / "result.json", result_limit)
        result = protocol._strict(result_raw, result_limit)
        expected, _ = protocol._result(request, request_sha)
        if (type(result) is not dict or set(result) != set(expected)
                or type(result["schema_version"]) is not int or result["schema_version"] != version
                or any(result[key] != expected[key] for key in (
                    "kind", "request_sha256", "source_sha256", "scope_sha256", "preview_profile", "mode"))):
            protocol._fail("input_changed")
        if code == 2 and result["status"] == "failed":
            if type(result["code"]) is not str or result["code"] not in protocol.WORKER_ERROR_CODES:
                protocol._fail("pdf_render")
            failure = runtime.CropPreviewError(code=result["code"])
            if (failure.code != result["code"] or any(result[key] is not None
                    for key in ("width", "height", "rgb_size", "rgb_sha256"))
                    or metadata and result["raster_view"] is not None):
                protocol._fail("pdf_render")
            raise failure
        if code != 0 or result["status"] != "complete" or result["code"] is not None:
            protocol._fail("pdf_render")
        runtime._dimensions(result["width"], result["height"])
        if (type(result["rgb_size"]) is not int
                or result["rgb_size"] != result["width"] * result["height"] * 3):
            protocol._fail("pdf_render")
        raw, digest, rgb_identity = runtime._snapshot(leaf / "rgb.bin", protocol.MAX_RGB_BYTES)
        if len(raw) != result["rgb_size"] or digest != result["rgb_sha256"]:
            protocol._fail("input_changed")
        view = None
        if metadata:
            view = protocol._checked_raster_view(result["raster_view"], request,
                width=result["width"], height=result["height"], rgb_sha256=digest)
        protocol._check_files(leaf, request)
        from PIL import Image
        # The raw RGB decoder alone; no PNG/JPEG/pickle deserialization in host.
        image = Image.frombytes("RGB", (result["width"], result["height"]), raw)
        try:
            if metadata:
                # Bind the image actually returned by the raw decoder too,
                # not just the input buffer. Keep this opt-in: protocol 2's
                # established PIL return/acceptance behavior is unchanged.
                if (not isinstance(image, Image.Image) or image.mode != "RGB"
                        or image.size != (result["width"], result["height"])):
                    protocol._fail("geometry_mismatch")
                actual_rgb = image.tobytes()
                if (type(actual_rgb) is not bytes or len(actual_rgb) != len(raw)
                        or protocol._sha(actual_rgb) != digest):
                    protocol._fail("geometry_mismatch")
                # Constructor failure leaves the raw image owned by this guard.
                # A successful wrapper exposes idempotent close to every outer
                # late-input/cancellation/cleanup guard; no image is hidden.
                image = runtime.CropRasterPreview(image, view)
            for name, limit, expected in (
                    ("result.json", result_limit, (result_sha, result_identity)),
                    ("rgb.bin", protocol.MAX_RGB_BYTES, (digest, rgb_identity))):
                if runtime._snapshot(leaf / name, limit)[1:] != expected:
                    protocol._fail("input_changed")
            return image
        except BaseException:
            # No ownership transfer occurred; preserve the selected failure,
            # including cancellation, if disposal itself also fails.
            if image is not None:
                try:
                    image.close()
                except BaseException:
                    pass
            raise

    def _worker(self, source, scope, active, callback, *, preview_profile="fit",
                schema_version=protocol.PROTOCOL_SCHEMA_VERSION):
        preview_profile = runtime.validate_preview_profile(preview_profile)
        schema_version = protocol._schema_version(schema_version)
        self._verify_root()
        self._check_cancel(active, callback)
        leaf = self._private_root / ("preview-" + uuid4().hex)
        owned, identity, cleanup_confirmed = {}, None, True
        mkdir_attempted = False
        failure = None
        image = None
        try:
            try:
                mkdir_attempted = True
                leaf.mkdir(mode=0o700)
                identity = protocol._identity(leaf, directory=True)
                storage_policy.ensure_private_directory(leaf, harden_existing=True)
                for name in protocol.FILES:
                    path = leaf / name
                    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                    try:
                        info = os.fstat(fd)
                        owned[name] = (info.st_dev, info.st_ino)
                    finally:
                        os.close(fd)
                    storage_policy.enforce_private_path(path, directory=False)
                if type(source) is not bytes or not 0 < len(source) <= runtime.MAX_PDF_BYTES:
                    protocol._fail()
                producers = (protocol._producer_snapshots(schema_version=schema_version)
                             if schema_version == protocol.METADATA_PROTOCOL_SCHEMA_VERSION
                             else protocol._producer_snapshots())
                request = {"schema_version": schema_version, "kind": "ocr_crop_preview_request",
                    "source_sha256": protocol._sha(source), "source_size": len(source), "scope": scope,
                    "preview_profile": preview_profile,
                    "producer_sha256": {name: value[0] for name, value in producers.items()},
                    "directory_identity": list(identity),
                    "file_identities": {name: list(value) for name, value in owned.items()}}
                protocol._validate_request(request)
                request_raw = protocol._encoded(request, protocol.MAX_REQUEST_BYTES)
                request_sha = protocol._sha(request_raw)
                protocol._write_preallocated(leaf / "source.pdf", source, owned["source.pdf"])
                protocol._write_preallocated(leaf / "request.json", request_raw, owned["request.json"])
                input_snapshots = {name: runtime._snapshot(leaf / name, limit)[1:] for name, limit in (
                    ("source.pdf", runtime.MAX_PDF_BYTES), ("request.json", protocol.MAX_REQUEST_BYTES))}

                def recheck():
                    self._verify_root()
                    protocol._check_files(leaf, request)
                    if protocol._request_producers(request) != producers:
                        protocol._fail("input_changed")
                    for name, limit in (("source.pdf", runtime.MAX_PDF_BYTES), ("request.json", protocol.MAX_REQUEST_BYTES)):
                        if runtime._snapshot(leaf / name, limit)[1:] != input_snapshots[name]:
                            protocol._fail("input_changed")

                def before_release(_process):
                    recheck()
                    self._check_cancel(active, callback)

                recheck()
                self._check_cancel(active, callback)
                cleanup_confirmed = False
                try:
                    code = process_supervision._run_cli_with_deadline(_SCRIPT,
                        ["--request=" + str(leaf / "request.json"), "--expected-request-sha256=" + request_sha],
                        operation="ocr-crop-preview", timeout=WORKER_TIMEOUT_SECONDS,
                        config=process_supervision.SupervisionConfig(protocol.CHILD_ENV, "RAG_OCR_CROP_PREVIEW_RUN", 5., .1, 30.),
                        environment_overrides=_environment(), stdout_target=subprocess.DEVNULL,
                        stderr_target=subprocess.DEVNULL, warn_fn=lambda _message: None,
                        cleanup_error_type=_CleanupUnconfirmed,
                        cancel_requested=lambda: self._cancelled(active, callback), on_child_started=before_release)
                    cleanup_confirmed = True  # Shared supervisor confirmed the entire process tree.
                except _Cancelled:
                    cleanup_confirmed = True  # The shared pre-gate exception branch verified cleanup.
                    raise
                if type(code) is not int:
                    protocol._fail("preview_unavailable")
                if code == 124:
                    protocol._fail("preview_timeout")
                if code == 130:
                    raise _Cancelled()
                self._check_cancel(active, callback)
                recheck()
                image = self._read_result(leaf, request, request_sha, code)
                recheck()
                self._check_cancel(active, callback)
            except BaseException as error:
                failure = error
            finally:
                if identity is not None and cleanup_confirmed:
                    try:
                        self._cleanup_leaf(leaf, identity, owned)
                    except BaseException:
                        self._latch()
                elif identity is not None:
                    self._latch()
                elif mkdir_attempted:
                    self._latch()
            if self.cleanup_uncertain:
                protocol._fail("cleanup_unconfirmed")
            if failure is not None:
                raise failure
            return image
        except BaseException:
            # No ownership transfer occurred; preserve the selected failure,
            # including cancellation, if disposal itself also fails.
            if image is not None:
                try:
                    image.close()
                except BaseException:
                    pass
            raise

    def render(self, scope, *, cancel_requested=None, preview_profile="fit"):
        """Compatibility protocol-2 preview; returns the ordinary owned PIL RGB."""
        return self._render(scope, cancel_requested=cancel_requested, preview_profile=preview_profile)

    def render_with_view(self, scope, *, cancel_requested=None, preview_profile="fit"):
        """Explicit protocol-3 image plus bound raster declaration; no fallback.

        Returns runtime.CropRasterPreview. The caller owns its open RGB image
        and must close the wrapper after its final consumer. Version selection
        is host-owned here, before the request hash and fixed worker dispatch.
        """
        return self._render(scope, cancel_requested=cancel_requested, preview_profile=preview_profile,
                            schema_version=protocol.METADATA_PROTOCOL_SCHEMA_VERSION)

    def _render(self, scope, *, cancel_requested=None, preview_profile="fit",
                schema_version=protocol.PROTOCOL_SCHEMA_VERSION):
        schema_version = protocol._schema_version(schema_version)
        preview_profile = runtime.validate_preview_profile(preview_profile)
        if cancel_requested is not None and not callable(cancel_requested):
            protocol._fail("preview_unavailable")
        with self._lock:
            if self._uncertain:
                protocol._fail("cleanup_unconfirmed")
            if self._closed:
                protocol._fail("preview_cancelled")
            if self._active is not None:
                protocol._fail("preview_busy")
            active = _Active()
            self._active = active
        image = None
        try:
            try:
                self._verify_root()
                self._check_cancel(active, cancel_requested)
                if schema_version == protocol.METADATA_PROTOCOL_SCHEMA_VERSION:
                    image = runtime._render_crop_scope(self._workspace, scope,
                        lambda raw, checked: self._worker(raw, checked, active, cancel_requested,
                            preview_profile=preview_profile, schema_version=schema_version))
                    if type(image) is not runtime.CropRasterPreview:
                        protocol._fail("geometry_mismatch")
                else:
                    image = runtime._render_crop_scope(self._workspace, scope,
                        lambda raw, checked: self._worker(raw, checked, active, cancel_requested,
                                                          preview_profile=preview_profile))
                self._check_cancel(active, cancel_requested)
                if self.cleanup_uncertain:
                    protocol._fail("cleanup_unconfirmed")
            except _Cancelled:
                protocol._fail("preview_cancelled")
            except runtime.CropPreviewError:
                raise
            except Exception:
                protocol._fail("preview_unavailable")
            finally:
                with self._lock:
                    self._active = None
                    active.done.set()
            return image
        except BaseException:
            # No ownership transfer occurred; preserve the selected failure,
            # including cancellation, if disposal itself also fails.
            if image is not None:
                try:
                    image.close()
                except BaseException:
                    pass
            raise

    def request_close(self):
        with self._lock:
            self._closed = True
            if self._active is not None:
                self._active.cancel.set()

    def close(self, timeout_seconds=CLOSE_TIMEOUT_SECONDS):
        if (type(timeout_seconds) not in (int, float)
                or not 0 <= timeout_seconds <= CLOSE_TIMEOUT_SECONDS or not math.isfinite(timeout_seconds)):
            protocol._fail("preview_unavailable")
        deadline = time.monotonic() + timeout_seconds
        self.request_close()
        with self._lock:
            active = self._active
        if active is not None and not active.done.wait(max(0., deadline - time.monotonic())):
            self._latch()
            return False
        if not self._close_lock.acquire(timeout=max(0., deadline - time.monotonic())):
            self._latch()
            return False
        try:
            if self.cleanup_uncertain:
                return False
            if self._root_removed:
                return True
            try:
                self._verify_storage()
                self._private_root.rmdir()  # Empty owned root only, never recursive.
                self._root_removed = True
                return True
            except BaseException:
                self._latch()
                return False
        finally:
            self._close_lock.release()
