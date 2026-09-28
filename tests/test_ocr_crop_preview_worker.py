"""Bounded private preview protocol controls; no models, OCR or private PDF."""

import copy
import hashlib
import json
import os
from pathlib import Path

import pytest

import ocr_crop_comparison as comparison
import ocr_crop_preview_worker as worker
import ocr_crop_review_runtime as runtime


def scope_for(source):
    scope = {"source_sha256": hashlib.sha256(source).hexdigest(), "page_count": 1,
        "page_number": 1, "coordinate_system": "original_page_display_fraction",
        "bbox": [.1, .1, .4, .4], "page_geometry": {
            "display_rect_points": [0., 0., 200., 120.], "cropbox_points": [0., 0., 200., 120.],
            "rotation_degrees": 0}}
    scope["scope_sha256"] = comparison._digest(scope)
    return scope


def staging(tmp_path, monkeypatch, *, source=b"generated protocol source", render=None, preview_profile="fit"):
    image = pytest.importorskip("PIL.Image")
    folder = tmp_path / ("preview-" + "1" * 32)
    folder.mkdir()
    for name in worker.FILES:
        (folder / name).touch()
    identities = {name: list(worker._identity(folder / name)) for name in worker.FILES}
    request = {"schema_version": 2, "kind": "ocr_crop_preview_request", "preview_profile": preview_profile,
        "source_sha256": worker._sha(source), "source_size": len(source), "scope": scope_for(source),
        "producer_sha256": {name: data[0] for name, data in worker._producer_snapshots().items()},
        "directory_identity": list(worker._identity(folder, directory=True)), "file_identities": identities}
    (folder / "source.pdf").write_bytes(source)
    raw = worker._encoded(request, worker.MAX_REQUEST_BYTES)
    (folder / "request.json").write_bytes(raw)
    monkeypatch.setenv(worker.CHILD_ENV, "1")
    monkeypatch.setattr(runtime, "_render_pdf", render or
                        (lambda _raw, _scope, **_kwargs: image.new("RGB", (3, 2), "red")))
    return folder, request, worker._sha(raw)


def run(folder, digest):
    return worker.run_request(folder / "request.json", digest)


def test_fixed_request_writes_only_bound_raw_rgb_and_manifest_last(tmp_path, monkeypatch):
    folder, request, digest = staging(tmp_path, monkeypatch)
    before = {name: worker._identity(folder / name) for name in worker.FILES}
    assert run(folder, digest) == 0
    result = json.loads((folder / "result.json").read_bytes())
    assert result == {"schema_version": 2, "kind": "ocr_crop_preview_result", "preview_profile": "fit",
        "request_sha256": digest, "source_sha256": request["source_sha256"],
        "scope_sha256": request["scope"]["scope_sha256"], "status": "complete", "code": None,
        "mode": "RGB", "width": 3, "height": 2, "rgb_size": 18,
        "rgb_sha256": worker._sha(bytes([255, 0, 0]) * 6)}
    assert (folder / "rgb.bin").read_bytes() == bytes([255, 0, 0]) * 6
    assert {name: worker._identity(folder / name) for name in worker.FILES} == before
    assert set(p.name for p in folder.iterdir()) == set(worker.FILES)
    with pytest.raises(ValueError):
        run(folder, digest)  # No second write to a completed output.


@pytest.mark.parametrize("code", ["geometry_mismatch", "raster_limit", "pdf_render"])
def test_render_failure_has_only_static_bound_result(tmp_path, monkeypatch, code):
    def fail(*_args, **_kwargs):
        raise runtime.CropPreviewError(code=code)
    folder, _, digest = staging(tmp_path, monkeypatch, render=fail)
    assert run(folder, digest) == 2
    result = json.loads((folder / "result.json").read_bytes())
    assert result["status"] == "failed" and result["code"] == code
    assert all(result[k] is None for k in ("width", "height", "rgb_size", "rgb_sha256"))
    assert (folder / "rgb.bin").read_bytes() == b""


@pytest.mark.parametrize("code", ["cleanup_unconfirmed", "preview_busy", "preview_timeout", "preview_cancelled"])
def test_worker_cannot_construct_host_lifecycle_result(tmp_path, monkeypatch, code):
    _folder, request, digest = staging(tmp_path, monkeypatch)
    with pytest.raises(runtime.CropPreviewError) as caught:
        worker._result(request, digest, code=code)
    assert caught.value.code == "pdf_render"


@pytest.mark.parametrize("error", [RuntimeError("private path"), KeyboardInterrupt(), SystemExit(8)])
def test_native_errors_are_static_and_base_cancellation_propagates(tmp_path, monkeypatch, error):
    def fail(*_args, **_kwargs):
        raise error
    folder, _, digest = staging(tmp_path, monkeypatch, render=fail)
    if isinstance(error, Exception):
        assert run(folder, digest) == 2
        raw = (folder / "result.json").read_bytes()
        assert b"private path" not in raw and b'"code":"pdf_render"' in raw
    else:
        with pytest.raises(type(error)) as caught:
            run(folder, digest)
        assert caught.value is error
        assert (folder / "result.json").read_bytes() == b""


@pytest.mark.parametrize("field,value", [
    ("schema_version", True), ("source_size", True), ("source_size", 0),
    ("source_size", runtime.MAX_PDF_BYTES + 1), ("source_sha256", "x" * 64),
    ("directory_identity", [True, 1]), ("file_identities", {}), ("producer_sha256", {}),
    ("scope", None), ("kind", "other")])
def test_request_schema_rejects_malformed_before_renderer(tmp_path, monkeypatch, field, value):
    called = []
    folder, request, _ = staging(tmp_path, monkeypatch, render=lambda *args: called.append(args))
    request[field] = value
    raw = worker._encoded(request, worker.MAX_REQUEST_BYTES)
    (folder / "request.json").write_bytes(raw)
    with pytest.raises(ValueError):
        run(folder, worker._sha(raw))
    assert not called


@pytest.mark.parametrize("target", ["source", "request", "producer", "output", "identity"])
def test_generation_mismatch_precedes_native(tmp_path, monkeypatch, target):
    called = []
    folder, request, digest = staging(tmp_path, monkeypatch, render=lambda *args: called.append(args))
    if target == "source":
        (folder / "source.pdf").write_bytes(b"changed")
    elif target == "request":
        digest = "0" * 64
    elif target == "output":
        (folder / "rgb.bin").write_bytes(b"not empty")
    else:
        if target == "producer":
            request["producer_sha256"][worker.PRODUCERS[0]] = "0" * 64
        else:
            request["file_identities"]["rgb.bin"][1] += 1
        raw = worker._encoded(request, worker.MAX_REQUEST_BYTES)
        (folder / "request.json").write_bytes(raw)
        digest = worker._sha(raw)
    with pytest.raises(ValueError):
        run(folder, digest)
    assert not called


@pytest.mark.parametrize("name", ["source.pdf", "request.json", "rgb.bin", "result.json"])
def test_post_render_identity_replacement_refuses_publication(tmp_path, monkeypatch, name):
    image = pytest.importorskip("PIL.Image")
    folder, _, digest = staging(tmp_path, monkeypatch)
    def replace(*_args, **_kwargs):
        path = folder / name
        replacement = folder / "replacement"
        replacement.write_bytes(path.read_bytes())
        os.replace(replacement, path)
        return image.new("RGB", (3, 2))
    monkeypatch.setattr(runtime, "_render_pdf", replace)
    with pytest.raises(ValueError):
        run(folder, digest)
    assert (folder / "result.json").read_bytes() == b""


@pytest.mark.parametrize("name", ["source.pdf", "request.json", "rgb.bin", "result.json"])
def test_hardlinked_files_rejected(tmp_path, monkeypatch, name):
    folder, _, digest = staging(tmp_path, monkeypatch)
    os.link(folder / name, tmp_path / "alias")
    with pytest.raises(ValueError):
        run(folder, digest)


@pytest.mark.parametrize("argv", [[], ["--help"], ["secret-path"], ["--request=x"],
    ["--request=x", "--expected-request-sha256=x", "--renderer=evil"], None])
def test_cli_bad_arguments_are_silent_and_static(monkeypatch, capsys, argv):
    if argv is None:
        monkeypatch.setattr(worker.sys, "argv", ["worker", "secret-path"])
    assert worker.main(argv) == 2
    assert capsys.readouterr() == ("", "")


def test_missing_worker_env_never_imports_renderer(tmp_path, monkeypatch):
    called = []
    folder, _, digest = staging(tmp_path, monkeypatch, render=lambda *args: called.append(args))
    monkeypatch.delenv(worker.CHILD_ENV)
    assert worker.main(["--request=" + str(folder / "request.json"), "--expected-request-sha256=" + digest]) == 2
    assert not called


@pytest.mark.parametrize("raw", [b'{"a":1,"a":2}', b'{"a":NaN}', b'{}' * 40000, b"", b"\xff"],
                         ids=["duplicate", "nonfinite", "over-budget", "empty", "invalid-utf8"])
def test_parser_is_bounded_duplicate_and_nonfinite_strict(raw):
    with pytest.raises(ValueError):
        worker._strict(raw, worker.MAX_REQUEST_BYTES)


def test_validation_detaches_no_user_callback_and_preserves_original(tmp_path, monkeypatch):
    _, request, _ = staging(tmp_path, monkeypatch)
    before = copy.deepcopy(request)
    assert worker._validate_request(request) == before
    request["renderer"] = str(Path("untrusted"))
    with pytest.raises(ValueError):
        worker._validate_request(request)


@pytest.mark.parametrize("profile", ["fit", "dpi288", "dpi576"])
@pytest.mark.parametrize("failed", [False, True])
def test_v2_worker_passes_exact_profile_once_and_echoes_it_even_on_failure(tmp_path, monkeypatch, profile, failed):
    image = pytest.importorskip("PIL.Image")
    seen = []
    def render(raw, scope, *, preview_profile):
        seen.append((raw, copy.deepcopy(scope), preview_profile))
        if failed:
            raise runtime.CropPreviewError(code="raster_limit")
        return image.new("RGB", (3, 2), "red")
    folder, request, digest = staging(tmp_path, monkeypatch, render=render, preview_profile=profile)
    before = copy.deepcopy(request)
    assert worker.PROTOCOL_SCHEMA_VERSION == 2
    assert run(folder, digest) == (2 if failed else 0)
    result = json.loads((folder / "result.json").read_bytes())
    assert result["schema_version"] == 2 and result["preview_profile"] == profile
    assert result["scope_sha256"] == request["scope"]["scope_sha256"]
    assert result["request_sha256"] == digest and len(seen) == 1
    assert seen[0] == (b"generated protocol source", request["scope"], profile)
    assert request == before
    assert result["status"] == ("failed" if failed else "complete")
    assert result["code"] == ("raster_limit" if failed else None)


@pytest.mark.parametrize("mutation", ["v1-no-profile", "v1-with-profile", "v2-no-profile", "v3",
    "null", "bool", "numeric", "list", "dict", "unknown", "case", "oversized"])
def test_mixed_or_malformed_profile_protocol_refuses_before_source_or_producer_reads(tmp_path, monkeypatch, mutation):
    folder, request, _ = staging(tmp_path, monkeypatch)
    if mutation.startswith("v1"):
        request["schema_version"] = 1
    elif mutation == "v3":
        request["schema_version"] = 3
    if mutation.endswith("no-profile"):
        request.pop("preview_profile")
    elif mutation in {"null", "bool", "numeric", "list", "dict", "unknown", "case", "oversized"}:
        request["preview_profile"] = {"null": None, "bool": True, "numeric": 288, "list": [], "dict": {},
            "unknown": "dpi144", "case": "FIT", "oversized": "x" * 1000}[mutation]
    raw = worker._encoded(request, worker.MAX_REQUEST_BYTES)
    (folder / "request.json").write_bytes(raw)
    original = runtime._snapshot
    def snapshot(path, limit):
        assert path.name == "request.json", "invalid request reached source or producer bytes"
        return original(path, limit)
    monkeypatch.setattr(runtime, "_snapshot", snapshot)
    monkeypatch.setattr(worker, "_producer_snapshots", lambda: pytest.fail("invalid request read producer bytes"))
    monkeypatch.setattr(runtime, "_render_pdf", lambda *_a, **_k: pytest.fail("invalid request rendered"))
    with pytest.raises(runtime.CropPreviewError):
        run(folder, worker._sha(raw))
    assert (folder / "result.json").read_bytes() == (folder / "rgb.bin").read_bytes() == b""


def tracked_image_staging(tmp_path, monkeypatch, *, preview_profile="fit", serialization_error=None,
                          close_error=None, mode="RGB"):
    """Real allocated PIL image, inert renderer, and exact disposal counters."""
    image_module = pytest.importorskip("PIL.Image")
    allocated = image_module.new(mode, (3, 2), "red")
    original_close, original_tobytes = allocated.close, allocated.tobytes
    events = []

    def close():
        events.append("close")
        original_close()
        if close_error is not None:
            raise close_error

    def tobytes(*args, **kwargs):
        events.append("serialize")
        if serialization_error is not None:
            raise serialization_error
        return original_tobytes(*args, **kwargs)

    def render(raw, scope, *, preview_profile):
        events.append("render:" + preview_profile)
        return allocated

    monkeypatch.setattr(allocated, "close", close)
    monkeypatch.setattr(allocated, "tobytes", tobytes)
    try:
        folder, request, digest = staging(tmp_path, monkeypatch, render=render,
                                          preview_profile=preview_profile)
    except BaseException:
        original_close()
        raise
    # Last-resort fixture cleanup does not masquerade as a worker close call.
    def cleanup():
        if "close" not in events:
            original_close()

    return folder, request, digest, allocated, events, cleanup


@pytest.mark.parametrize("profile", ["fit", "dpi288", "dpi576"])
def test_allocated_image_closed_once_before_checks_and_exact_rgb_publication(tmp_path, monkeypatch, profile):
    folder, request, digest, allocated, events, cleanup = tracked_image_staging(
        tmp_path, monkeypatch, preview_profile=profile)
    original_check = worker._check_files
    original_write = worker._write_preallocated
    checks = 0

    def check_files(*args):
        nonlocal checks
        checks += 1
        if checks > 1:
            assert events == ["render:" + profile, "serialize", "close"]
        return original_check(*args)

    def write(path, *args):
        assert events == ["render:" + profile, "serialize", "close"]
        return original_write(path, *args)

    monkeypatch.setattr(worker, "_check_files", check_files)
    monkeypatch.setattr(worker, "_write_preallocated", write)
    try:
        assert run(folder, digest) == 0
        assert events == ["render:" + profile, "serialize", "close"]
        assert checks == 4
        pixels = (folder / "rgb.bin").read_bytes()
        assert pixels == bytes([255, 0, 0]) * 6
        assert json.loads((folder / "result.json").read_bytes()) == {
            "schema_version": 2, "kind": "ocr_crop_preview_result", "preview_profile": profile,
            "request_sha256": digest, "source_sha256": request["source_sha256"],
            "scope_sha256": request["scope"]["scope_sha256"], "status": "complete", "code": None,
            "mode": "RGB", "width": 3, "height": 2, "rgb_size": 18, "rgb_sha256": worker._sha(pixels)}
        with pytest.raises(ValueError, match="closed image"):
            allocated.getpixel((0, 0))
    finally:
        cleanup()


@pytest.mark.parametrize("primary_kind", ["ordinary", "refusal", "keyboard", "exit"])
@pytest.mark.parametrize("close_kind", ["ordinary", "refusal", "keyboard", "exit"])
def test_serialization_primary_survives_every_secondary_close_failure(tmp_path, monkeypatch, primary_kind, close_kind):
    def errors():
        return {"ordinary": RuntimeError("private ordinary failure"),
            "refusal": runtime.CropPreviewError(code="geometry_mismatch"),
            "keyboard": KeyboardInterrupt("private cancellation"), "exit": SystemExit(9)}
    primary, secondary = errors()[primary_kind], errors()[close_kind]
    if close_kind == "refusal":
        secondary = runtime.CropPreviewError(code="input_verification")
    folder, _, digest, _, events, cleanup = tracked_image_staging(
        tmp_path, monkeypatch, serialization_error=primary, close_error=secondary)
    try:
        if isinstance(primary, Exception):
            assert run(folder, digest) == 2
            result = json.loads((folder / "result.json").read_bytes())
            assert result["status"] == "failed"
            assert result["code"] == ("geometry_mismatch" if primary_kind == "refusal" else "pdf_render")
            assert result["rgb_size"] is None and result["rgb_sha256"] is None
            assert b"private" not in (folder / "result.json").read_bytes()
        else:
            with pytest.raises(type(primary)) as caught:
                run(folder, digest)
            assert caught.value is primary
            assert (folder / "result.json").read_bytes() == b""
        assert (folder / "rgb.bin").read_bytes() == b""
        assert events == ["render:fit", "serialize", "close"]
    finally:
        cleanup()


@pytest.mark.parametrize("close_error", [RuntimeError("private close"), KeyboardInterrupt(), SystemExit(7)])
def test_close_only_failure_never_publishes_completed_pixels(tmp_path, monkeypatch, close_error):
    folder, _, digest, _, events, cleanup = tracked_image_staging(tmp_path, monkeypatch, close_error=close_error)
    try:
        if isinstance(close_error, Exception):
            assert run(folder, digest) == 2
            result = json.loads((folder / "result.json").read_bytes())
            assert result["status"] == "failed" and result["code"] == "pdf_render"
            assert all(result[key] is None for key in ("width", "height", "rgb_size", "rgb_sha256"))
        else:
            with pytest.raises(type(close_error)) as caught:
                run(folder, digest)
            assert caught.value is close_error
            assert (folder / "result.json").read_bytes() == b""
        assert (folder / "rgb.bin").read_bytes() == b""
        assert events == ["render:fit", "serialize", "close"]
    finally:
        cleanup()


def test_allocated_wrong_mode_is_disposed_before_static_refusal(tmp_path, monkeypatch):
    folder, _, digest, _, events, cleanup = tracked_image_staging(tmp_path, monkeypatch, mode="RGBA")
    try:
        assert run(folder, digest) == 2
        assert events == ["render:fit", "close"]
        result = json.loads((folder / "result.json").read_bytes())
        assert result["code"] == "pdf_render" and result["status"] == "failed"
        assert (folder / "rgb.bin").read_bytes() == b""
    finally:
        cleanup()


@pytest.mark.parametrize("stage", ["first_recheck", "rgb_write", "second_recheck", "encode", "result_write", "last_recheck"])
@pytest.mark.parametrize("error_type", [RuntimeError, KeyboardInterrupt, SystemExit])
def test_post_serialization_failure_never_recloses_image(tmp_path, monkeypatch, stage, error_type):
    folder, _, digest, _, events, cleanup = tracked_image_staging(tmp_path, monkeypatch)
    primary = error_type("private later failure")
    original_check, original_write, original_encode = worker._check_files, worker._write_preallocated, worker._encoded
    checks = 0

    def check_files(*args):
        nonlocal checks
        checks += 1
        if {2: "first_recheck", 3: "second_recheck", 4: "last_recheck"}.get(checks) == stage:
            raise primary
        return original_check(*args)

    def write(path, *args):
        if {"rgb.bin": "rgb_write", "result.json": "result_write"}[path.name] == stage:
            raise primary
        return original_write(path, *args)

    def encode(*args):
        if stage == "encode":
            raise primary
        return original_encode(*args)

    monkeypatch.setattr(worker, "_check_files", check_files)
    monkeypatch.setattr(worker, "_write_preallocated", write)
    monkeypatch.setattr(worker, "_encoded", encode)
    try:
        with pytest.raises(type(primary)) as caught:
            run(folder, digest)
        assert caught.value is primary
        assert events == ["render:fit", "serialize", "close"]
        if stage != "last_recheck":
            assert (folder / "result.json").read_bytes() == b""
    finally:
        cleanup()


@pytest.mark.parametrize("reporting_error", [RuntimeError("private report"), KeyboardInterrupt(), SystemExit(8)])
def test_failed_result_construction_does_not_repeat_image_disposal(tmp_path, monkeypatch, reporting_error):
    folder, _, digest, _, events, cleanup = tracked_image_staging(
        tmp_path, monkeypatch, serialization_error=RuntimeError("private serialization"),
        close_error=SystemExit("secondary disposal"))
    original_result = worker._result

    def result(*args, **kwargs):
        if kwargs.get("code") is not None:
            raise reporting_error
        return original_result(*args, **kwargs)

    monkeypatch.setattr(worker, "_result", result)
    try:
        with pytest.raises(type(reporting_error)) as caught:
            run(folder, digest)
        assert caught.value is reporting_error
        assert events == ["render:fit", "serialize", "close"]
        assert (folder / "rgb.bin").read_bytes() == (folder / "result.json").read_bytes() == b""
    finally:
        cleanup()
