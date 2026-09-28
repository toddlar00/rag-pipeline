"""Fixed private raw-RGB preview protocol; native PDF work runs only in the child.

File/source/producer digests bind this local request, not loaded native bytes or
source authenticity. Empty preallocated outputs are never completion markers.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

import ocr_crop_review_runtime as runtime
from ocr_crop_comparison import validate_crop_scope
import storage_policy


ROOT = Path(__file__).resolve().parent
PROTOCOL_SCHEMA_VERSION = 2
METADATA_PROTOCOL_SCHEMA_VERSION = 3
MAX_REQUEST_BYTES = 64 * 1024
MAX_RESULT_BYTES = 4096
MAX_METADATA_RESULT_BYTES = 8192
MAX_RGB_BYTES = 1400 * 1400 * 3
FILES = ("source.pdf", "request.json", "rgb.bin", "result.json")
PRODUCERS = ("ocr_crop_preview_worker.py", "ocr_crop_preview_supervision.py",
    "ocr_crop_review_runtime.py", "ocr_crop_comparison.py", "artifact_io.py",
    "storage_policy.py", "process_supervision.py", "supervised_worker.py")
METADATA_PRODUCERS = (*PRODUCERS, "ocr_crop_raster_view.py")
CHILD_ENV = "RAG_OCR_CROP_PREVIEW_CHILD"
WORKER_ERROR_CODES = frozenset({"scope_validation", "input_verification", "input_changed",
    "pdf_render", "geometry_mismatch", "raster_limit", "preview_unavailable"})
_HEX = re.compile(r"[0-9a-f]{64}")


def _fail(code="input_verification"):
    raise runtime.CropPreviewError(code=code)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _encoded(value, limit):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                     allow_nan=False).encode("utf-8") + b"\n"
    if len(raw) > limit:
        _fail()
    return raw


def _strict(raw, limit):
    if type(raw) is not bytes or not 0 < len(raw) <= limit:
        _fail()
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                _fail()
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=pairs,
                          parse_constant=lambda _value: _fail())
    except (ValueError, TypeError, UnicodeError, RecursionError):
        _fail()


def _identity(path, *, directory=False):
    storage_policy.assert_no_link_components(path)
    info = path.lstat()
    if (not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
            or not directory and info.st_nlink != 1):
        _fail()
    return (info.st_dev, info.st_ino)


def _valid_identity(value):
    return (type(value) is list and len(value) == 2
            and all(type(item) is int and 0 <= item < 2 ** 128 for item in value))


def _schema_version(value=PROTOCOL_SCHEMA_VERSION):
    if type(value) is not int or value not in (PROTOCOL_SCHEMA_VERSION, METADATA_PROTOCOL_SCHEMA_VERSION):
        _fail()
    return value


def _result_limit(request):
    # Minimal private result-builder callers retain their historical v2 default;
    # the actual request admission below always requires an explicit integer.
    version = _schema_version(request.get("schema_version", PROTOCOL_SCHEMA_VERSION))
    return MAX_METADATA_RESULT_BYTES if version == METADATA_PROTOCOL_SCHEMA_VERSION else MAX_RESULT_BYTES


def _producer_snapshots(*, schema_version=PROTOCOL_SCHEMA_VERSION):
    names = METADATA_PRODUCERS if _schema_version(schema_version) == METADATA_PROTOCOL_SCHEMA_VERSION else PRODUCERS
    return {name: runtime._snapshot(ROOT / name, 1024 * 1024)[1:]
            for name in names}


def _request_producers(request):
    if request["schema_version"] == METADATA_PROTOCOL_SCHEMA_VERSION:
        return _producer_snapshots(schema_version=METADATA_PROTOCOL_SCHEMA_VERSION)
    return _producer_snapshots()


def _validate_request(value):
    keys = {"schema_version", "kind", "source_sha256", "source_size", "scope",
            "preview_profile", "producer_sha256", "directory_identity", "file_identities"}
    if type(value) is not dict or set(value) != keys:
        _fail()
    version = _schema_version(value["schema_version"])
    producers = METADATA_PRODUCERS if version == METADATA_PROTOCOL_SCHEMA_VERSION else PRODUCERS
    if (
            type(value["schema_version"]) is not int
            or value["kind"] != "ocr_crop_preview_request"
            or type(value["source_sha256"]) is not str or _HEX.fullmatch(value["source_sha256"]) is None
            or type(value["source_size"]) is not int or not 0 < value["source_size"] <= runtime.MAX_PDF_BYTES
            or not _valid_identity(value["directory_identity"])
            or type(value["file_identities"]) is not dict or set(value["file_identities"]) != set(FILES)
            or not all(_valid_identity(item) for item in value["file_identities"].values())
            or len({tuple(item) for item in value["file_identities"].values()}) != len(FILES)
            or type(value["producer_sha256"]) is not dict or set(value["producer_sha256"]) != set(producers)
            or not all(type(item) is str and _HEX.fullmatch(item) for item in value["producer_sha256"].values())):
        _fail()
    runtime.validate_preview_profile(value["preview_profile"])
    scope = value["scope"]
    if type(scope) is not dict:
        _fail("scope_validation")
    validate_crop_scope(scope, source_sha256=value["source_sha256"], page_count=scope.get("page_count"))
    return value


def _check_files(folder, request):
    if _identity(folder, directory=True) != tuple(request["directory_identity"]):
        _fail("input_changed")
    for name in FILES:
        if _identity(folder / name) != tuple(request["file_identities"][name]):
            _fail("input_changed")


def _write_preallocated(path, raw, expected_identity):
    """Write a known empty inode; never replace another file or follow a link."""
    if _identity(path) != tuple(expected_identity):
        _fail("input_changed")
    flags = os.O_WRONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "wb") as handle:
        info = os.fstat(handle.fileno())
        if ((info.st_dev, info.st_ino) != tuple(expected_identity) or info.st_size != 0
                or info.st_nlink != 1 or not stat.S_ISREG(info.st_mode)):
            _fail("input_changed")
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    if _identity(path) != tuple(expected_identity):
        _fail("input_changed")


def _checked_raster_view(value, request, *, width, height, rgb_sha256):
    """Validate bounded metadata and its exact same-result RGB/recipe joins."""
    from ocr_crop_raster_view import validate_raster_view
    try:
        view = validate_raster_view(value, scope=request["scope"])
    except (ValueError, TypeError, OverflowError, RecursionError):
        _fail("geometry_mismatch")
    if (view["preview_profile"] != request["preview_profile"] or view["width"] != width
            or view["height"] != height or view["rgb_sha256"] != rgb_sha256):
        _fail("geometry_mismatch")
    return view


def _result(request, request_sha256, *, image=None, code=None, raster_view=None):
    version = _schema_version(request.get("schema_version", PROTOCOL_SCHEMA_VERSION))
    preview_profile = runtime.validate_preview_profile(request["preview_profile"])
    raw = None
    if code is not None and (type(code) is not str or code not in WORKER_ERROR_CODES):
        _fail("pdf_render")
    if image is not None:
        from PIL import Image
        if not isinstance(image, Image.Image) or image.mode != "RGB":
            _fail("pdf_render")
        runtime._dimensions(image.width, image.height)
        raw = image.tobytes()
        if len(raw) != image.width * image.height * 3 or len(raw) > MAX_RGB_BYTES:
            _fail("pdf_render")
    result = {"schema_version": version, "kind": "ocr_crop_preview_result",
        "request_sha256": request_sha256, "source_sha256": request["source_sha256"],
        "scope_sha256": request["scope"]["scope_sha256"], "preview_profile": preview_profile,
        "status": "complete" if image is not None else "failed", "code": code,
        "mode": "RGB", "width": None if image is None else image.width,
        "height": None if image is None else image.height,
        "rgb_size": None if raw is None else len(raw),
        "rgb_sha256": None if raw is None else _sha(raw)}
    if version == METADATA_PROTOCOL_SCHEMA_VERSION:
        if image is None:
            if raster_view is not None:
                _fail("geometry_mismatch")
            result["raster_view"] = None
        else:
            result["raster_view"] = _checked_raster_view(raster_view, request,
                width=image.width, height=image.height, rgb_sha256=result["rgb_sha256"])
    elif raster_view is not None:
        _fail("geometry_mismatch")
    return result, raw


def run_request(path, expected_sha256):
    """Child-only fixed request. Callers cannot inject a renderer or executable."""
    if (os.environ.get(CHILD_ENV) != "1" or type(expected_sha256) is not str
            or _HEX.fullmatch(expected_sha256) is None):
        _fail()
    path = Path(path)
    if not path.is_absolute() or path.name != "request.json":
        _fail()
    folder = path.parent
    if re.fullmatch(r"preview-[0-9a-f]{32}", folder.name) is None:
        _fail()
    raw_request, actual, request_identity = runtime._snapshot(path, MAX_REQUEST_BYTES)
    if actual != expected_sha256:
        _fail("input_changed")
    request = _validate_request(_strict(raw_request, MAX_REQUEST_BYTES))
    _check_files(folder, request)
    producer_snapshots = _request_producers(request)
    if {name: item[0] for name, item in producer_snapshots.items()} != request["producer_sha256"]:
        _fail("input_changed")
    source, source_sha, source_identity = runtime._snapshot(folder / "source.pdf", runtime.MAX_PDF_BYTES)
    if source_sha != request["source_sha256"] or len(source) != request["source_size"]:
        _fail("input_changed")
    if any((folder / name).stat().st_size != 0 for name in ("rgb.bin", "result.json")):
        _fail("input_changed")

    def recheck():
        _check_files(folder, request)
        if _request_producers(request) != producer_snapshots:
            _fail("input_changed")
        for name, limit, digest, identity in (
                ("source.pdf", runtime.MAX_PDF_BYTES, source_sha, source_identity),
                ("request.json", MAX_REQUEST_BYTES, expected_sha256, request_identity)):
            _, current, current_identity = runtime._snapshot(folder / name, limit)
            if current != digest or current_identity != identity:
                _fail("input_changed")

    try:
        metadata = request["schema_version"] == METADATA_PROTOCOL_SCHEMA_VERSION
        renderer = runtime._render_pdf_with_view if metadata else runtime._render_pdf
        image = renderer(source, request["scope"], preview_profile=request["preview_profile"])
        try:
            if metadata:
                if type(image) is not runtime.CropRasterPreview:
                    _fail("geometry_mismatch")
                result, pixels = _result(request, expected_sha256, image=image.image, raster_view=image.raster_view)
            else:
                result, pixels = _result(request, expected_sha256, image=image)
        except BaseException:
            # The allocated image is ours even when RGB serialization fails.
            # A secondary disposal failure must not replace that active error
            # or cancellation; the existing outer policy handles the primary.
            if image is not None:
                try:
                    image.close()
                except BaseException:
                    pass
            raise
        else:
            # Pixels are detached bytes now. Close before any publication;
            # close-only failures follow the same refusal/cancellation policy.
            if image is not None:
                image.close()
    except runtime.CropPreviewError as error:
        result, pixels = _result(request, expected_sha256, code=error.code)
    except Exception:
        result, pixels = _result(request, expected_sha256, code="pdf_render")
    recheck()
    if pixels is not None:
        _write_preallocated(folder / "rgb.bin", pixels, request["file_identities"]["rgb.bin"])
    recheck()
    _write_preallocated(folder / "result.json", _encoded(result, _result_limit(request)),
                        request["file_identities"]["result.json"])
    recheck()
    return 0 if result["status"] == "complete" else 2


def main(argv=None):
    arguments = sys.argv[1:] if argv is None else argv
    if (type(arguments) is not list or len(arguments) != 2
            or not all(type(item) is str for item in arguments)
            or not arguments[0].startswith("--request=")
            or not arguments[1].startswith("--expected-request-sha256=")):
        return 2
    try:
        return run_request(arguments[0].split("=", 1)[1], arguments[1].split("=", 1)[1])
    except KeyboardInterrupt:
        return 130
    except Exception:
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
