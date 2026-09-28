"""Opt-in v3 protocol controls; inert pixels unless a test explicitly says native.

Inert view fixtures synthesize declared geometry only to test protocol joins;
they are not render evidence. Real child cases use generated, non-private PDFs.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import threading

import pytest

import ocr_crop_comparison as crops
import ocr_crop_preview_supervision as host
import ocr_crop_preview_worker as worker
import ocr_crop_raster_view as geometry
import ocr_crop_review_runtime as runtime
from test_ocr_crop_preview_supervision import case as _case, fake_supervisor, scratch_parent  # noqa: F401
from test_ocr_crop_preview_worker import staging


case = _case


def inert_owner(scope, profile="fit", *, color="red", images=None, close_error=None):
    image_module = pytest.importorskip("PIL.Image")
    observed = geometry._expected_geometry(scope, profile)
    image = image_module.new("RGB", (observed["width"], observed["height"]), color)
    view = geometry.build_raster_view(scope=scope, preview_profile=profile, **observed,
                                      rgb_sha256=worker._sha(image.tobytes()))
    closes = []
    actual_close = image.close

    def close():
        closes.append(True)
        actual_close()
        if close_error is not None:
            raise close_error

    image.close = close
    if images is not None:
        images.append((image, closes))
    return runtime.CropRasterPreview(image, view)


def metadata_staging(tmp_path, monkeypatch, *, profile="fit", images=None, render=None):
    folder, request, _ = staging(tmp_path, monkeypatch, preview_profile=profile)
    request["schema_version"] = 3
    request["producer_sha256"] = {name: value[0] for name, value in worker._producer_snapshots(schema_version=3).items()}
    raw = worker._encoded(request, worker.MAX_REQUEST_BYTES)
    (folder / "request.json").write_bytes(raw)
    monkeypatch.setattr(runtime, "_render_pdf_with_view", render or (
        lambda _raw, scope, *, preview_profile: inert_owner(scope, preview_profile, images=images)))
    return folder, request, worker._sha(raw)


@pytest.fixture
def metadata_fake(monkeypatch):
    calls, images = [], []

    def render(raw, scope, *, preview_profile):
        assert worker._sha(raw) == scope["source_sha256"]
        calls.append(preview_profile)
        return inert_owner(scope, preview_profile, images=images)

    monkeypatch.setattr(runtime, "_render_pdf_with_view", render)
    monkeypatch.setattr(host.process_supervision, "_run_cli_with_deadline", fake_supervisor)
    return calls, images


def result_files(folder):
    return json.loads((folder / "result.json").read_bytes()), (folder / "rgb.bin").read_bytes()


def retag(view):
    view.pop("view_sha256", None)
    view["view_sha256"] = geometry._digest(view)


@pytest.mark.parametrize("profile", ["fit", "dpi288", "dpi576"])
def test_explicit_v3_worker_keeps_single_image_view_rgb_and_closes_before_publication(tmp_path, monkeypatch, profile):
    images, writes = [], []
    folder, request, digest = metadata_staging(tmp_path, monkeypatch, profile=profile, images=images)
    original = worker._write_preallocated

    def write(path, raw, identity):
        assert len(images) == 1 and images[0][1] == [True]
        writes.append(path.name)
        return original(path, raw, identity)

    monkeypatch.setattr(worker, "_write_preallocated", write)
    monkeypatch.setattr(runtime, "_render_pdf", lambda *_a, **_k: pytest.fail("v3 fell back to v2 renderer"))
    assert worker.run_request(folder / "request.json", digest) == 0
    result, pixels = result_files(folder)
    assert writes == ["rgb.bin", "result.json"]
    assert result["schema_version"] == 3 and result["request_sha256"] == digest
    assert result["raster_view"] == geometry.validate_raster_view(result["raster_view"], scope=request["scope"])
    assert result["raster_view"]["rgb_sha256"] == result["rgb_sha256"] == worker._sha(pixels)
    assert result["raster_view"]["width"] == result["width"]
    assert result["raster_view"]["height"] == result["height"]
    assert result["raster_view"]["preview_profile"] == profile
    assert pixels == bytes((255, 0, 0)) * (result["width"] * result["height"])
    assert set(path.name for path in folder.iterdir()) == set(worker.FILES)


@pytest.mark.parametrize("code", sorted(worker.WORKER_ERROR_CODES))
def test_v3_failed_worker_has_null_view_and_no_rgb(tmp_path, monkeypatch, code):
    def fail(*_a, **_k):
        raise runtime.CropPreviewError(code=code)

    folder, _, digest = metadata_staging(tmp_path, monkeypatch, render=fail)
    assert worker.run_request(folder / "request.json", digest) == 2
    result, pixels = result_files(folder)
    assert result["schema_version"] == 3 and result["raster_view"] is None
    assert result["code"] == code and result["status"] == "failed" and pixels == b""


@pytest.mark.parametrize("version", [None, True, False, 1, 4, 2.0, 3.0, "3", [], {}])
def test_actual_request_version_is_exact_and_refuses_before_producer_or_render(tmp_path, monkeypatch, version):
    folder, request, _ = staging(tmp_path, monkeypatch)
    request["schema_version"] = version
    raw = worker._encoded(request, worker.MAX_REQUEST_BYTES)
    (folder / "request.json").write_bytes(raw)
    monkeypatch.setattr(worker, "_producer_snapshots", lambda **_k: pytest.fail("invalid request reached producers"))
    monkeypatch.setattr(runtime, "_render_pdf", lambda *_a, **_k: pytest.fail("invalid request rendered"))
    with pytest.raises(runtime.CropPreviewError):
        worker.run_request(folder / "request.json", worker._sha(raw))
    assert (folder / "rgb.bin").read_bytes() == (folder / "result.json").read_bytes() == b""


@pytest.mark.parametrize("version", [2, 3])
def test_version_specific_producer_set_cannot_negotiate(tmp_path, monkeypatch, version):
    folder, request, _ = metadata_staging(tmp_path, monkeypatch)
    request["schema_version"] = version
    if version == 3:
        request["producer_sha256"].pop("ocr_crop_raster_view.py")
    raw = worker._encoded(request, worker.MAX_REQUEST_BYTES)
    (folder / "request.json").write_bytes(raw)
    with pytest.raises(runtime.CropPreviewError):
        worker.run_request(folder / "request.json", worker._sha(raw))
    assert (folder / "result.json").read_bytes() == b""


def test_metadata_geometry_producer_drift_refuses_before_render(tmp_path, monkeypatch):
    folder, request, _ = metadata_staging(tmp_path, monkeypatch)
    request["producer_sha256"]["ocr_crop_raster_view.py"] = "0" * 64
    raw = worker._encoded(request, worker.MAX_REQUEST_BYTES)
    (folder / "request.json").write_bytes(raw)
    monkeypatch.setattr(runtime, "_render_pdf_with_view", lambda *_a, **_k: pytest.fail("drift rendered"))
    with pytest.raises(runtime.CropPreviewError) as caught:
        worker.run_request(folder / "request.json", worker._sha(raw))
    assert caught.value.code == "input_changed"


@pytest.mark.parametrize("version", [2, 3])
@pytest.mark.parametrize("size", [4096, 4097, 8192, 8193])
def test_result_raw_caps_are_version_specific_before_rgb_allocation(case, metadata_fake, monkeypatch, version, size):
    controller = host.CropPreviewController(case[0])
    image_module = pytest.importorskip("PIL.Image")

    def supervise(script, args, **options):
        code = fake_supervisor(script, args, **options)
        path = Path(args[0].split("=", 1)[1]).parent / "result.json"
        raw = path.read_bytes()
        assert len(raw) < size
        path.write_bytes(raw + b" " * (size - len(raw)))
        if size > (4096 if version == 2 else 8192):
            monkeypatch.setattr(image_module, "frombytes", lambda *_a, **_k: pytest.fail("oversized result allocated"))
        return code

    monkeypatch.setattr(host.process_supervision, "_run_cli_with_deadline", supervise)
    try:
        render = controller.render if version == 2 else controller.render_with_view
        if size > (4096 if version == 2 else 8192):
            with pytest.raises(runtime.CropPreviewError):
                render(case[1])
        else:
            image = render(case[1])
            image.close()
        assert not controller.cleanup_uncertain and not list(controller.private_root.iterdir())
    finally:
        assert controller.close()


@pytest.mark.parametrize("mutation", ["missing", "null", "extra", "version2", "float_version", "view_hash",
    "source", "scope", "rgb", "dimensions", "profile", "oversized_view"])
def test_v3_wrong_envelope_or_view_join_refuses_before_host_allocation(case, metadata_fake, monkeypatch, mutation):
    controller = host.CropPreviewController(case[0])
    image_module = pytest.importorskip("PIL.Image")

    def supervise(script, args, **options):
        code = fake_supervisor(script, args, **options)
        path = Path(args[0].split("=", 1)[1]).parent / "result.json"
        result = json.loads(path.read_bytes())
        if mutation == "missing":
            result.pop("raster_view")
        elif mutation == "null":
            result["raster_view"] = None
        elif mutation == "extra":
            result["unexpected"] = None
        elif mutation in ("version2", "float_version"):
            result["schema_version"] = 2 if mutation == "version2" else 3.0
        elif mutation == "view_hash":
            result["raster_view"]["view_sha256"] = "0" * 64
        elif mutation == "oversized_view":
            result["raster_view"]["extra"] = "x" * 4096
        else:
            key = {"source": "source_sha256", "scope": "scope_sha256", "rgb": "rgb_sha256",
                   "dimensions": "width", "profile": "preview_profile"}[mutation]
            result["raster_view"][key] = (1 if mutation == "dimensions" else "dpi288" if mutation == "profile" else "0" * 64)
            retag(result["raster_view"])
        path.write_bytes(worker._encoded(result, worker.MAX_METADATA_RESULT_BYTES))
        monkeypatch.setattr(image_module, "frombytes", lambda *_a, **_k: pytest.fail("invalid view allocated RGB"))
        return code

    monkeypatch.setattr(host.process_supervision, "_run_cli_with_deadline", supervise)
    try:
        with pytest.raises(runtime.CropPreviewError):
            controller.render_with_view(case[1])
        assert not controller.cleanup_uncertain and not list(controller.private_root.iterdir())
    finally:
        assert controller.close()


@pytest.mark.parametrize("version", [2, 3])
def test_failed_envelope_mixed_metadata_refuses(case, monkeypatch, version):
    controller = host.CropPreviewController(case[0])

    def fail(*_a, **_k):
        raise runtime.CropPreviewError(code="geometry_mismatch")

    monkeypatch.setattr(runtime, "_render_pdf", fail)
    monkeypatch.setattr(runtime, "_render_pdf_with_view", fail)

    def supervise(script, args, **options):
        code = fake_supervisor(script, args, **options)
        path = Path(args[0].split("=", 1)[1]).parent / "result.json"
        result = json.loads(path.read_bytes())
        result["raster_view"] = {} if version == 3 else None
        path.write_bytes(worker._encoded(result, worker.MAX_METADATA_RESULT_BYTES))
        return code

    monkeypatch.setattr(host.process_supervision, "_run_cli_with_deadline", supervise)
    try:
        with pytest.raises(runtime.CropPreviewError) as caught:
            (controller.render if version == 2 else controller.render_with_view)(case[1])
        assert caught.value.code != "geometry_mismatch"
    finally:
        assert controller.close()


@pytest.mark.parametrize("profile", ["fit", "dpi288", "dpi576"])
def test_controller_v3_selects_before_hash_and_transfers_detached_open_owner(case, metadata_fake, monkeypatch, profile):
    calls, images = metadata_fake
    controller = host.CropPreviewController(case[0])
    observed = []

    def supervise(script, args, **options):
        path = Path(args[0].split("=", 1)[1])
        raw = path.read_bytes()
        request = json.loads(raw)
        assert request["schema_version"] == 3 and request["preview_profile"] == profile
        assert args[1].split("=", 1)[1] == worker._sha(raw)
        assert set(request["producer_sha256"]) == set(worker.METADATA_PRODUCERS)
        observed.append(request)
        return fake_supervisor(script, args, **options)

    monkeypatch.setattr(host.process_supervision, "_run_cli_with_deadline", supervise)
    try:
        result = controller.render_with_view(case[1], preview_profile=profile)
        assert type(result) is runtime.CropRasterPreview and len(observed) == 1 and calls == [profile]
        assert images[0][1] == [True]
        assert result.image.tobytes() and result.image.mode == "RGB"
        original = result.raster_view_bytes
        changed = result.raster_view
        changed["pixel_rect"][0] += 1
        assert result.raster_view_bytes == original and result.raster_view != changed
        assert not list(controller.private_root.iterdir()) and not controller.cleanup_uncertain
        result.close()
        result.close()
    finally:
        assert controller.close()


@pytest.mark.parametrize("primary", [RuntimeError("serialization"), KeyboardInterrupt(), SystemExit(19)])
@pytest.mark.parametrize("secondary", [None, RuntimeError("close"), KeyboardInterrupt(), SystemExit(23)])
def test_v3_child_serialization_primary_survives_exactly_once_close(tmp_path, monkeypatch, primary, secondary):
    images = []

    def render(_raw, scope, *, preview_profile):
        owner = inert_owner(scope, preview_profile, images=images, close_error=secondary)
        owner.image.tobytes = lambda: (_ for _ in ()).throw(primary)
        return owner

    folder, _, digest = metadata_staging(tmp_path, monkeypatch, render=render)
    if isinstance(primary, Exception):
        assert worker.run_request(folder / "request.json", digest) == 2
        result, pixels = result_files(folder)
        assert result["code"] == "pdf_render" and result["raster_view"] is None and not pixels
    else:
        with pytest.raises(type(primary)) as caught:
            worker.run_request(folder / "request.json", digest)
        assert caught.value is primary
        assert (folder / "result.json").read_bytes() == (folder / "rgb.bin").read_bytes() == b""
    assert len(images) == 1 and images[0][1] == [True]


@pytest.mark.parametrize("secondary", [RuntimeError("close"), KeyboardInterrupt(), SystemExit(23)])
def test_v3_child_close_only_failure_never_publishes_success(tmp_path, monkeypatch, secondary):
    images = []
    folder, _, digest = metadata_staging(tmp_path, monkeypatch, render=lambda _raw, scope, *, preview_profile:
        inert_owner(scope, preview_profile, images=images, close_error=secondary))
    if isinstance(secondary, Exception):
        assert worker.run_request(folder / "request.json", digest) == 2
        result, pixels = result_files(folder)
        assert result["status"] == "failed" and result["raster_view"] is None and not pixels
    else:
        with pytest.raises(type(secondary)) as caught:
            worker.run_request(folder / "request.json", digest)
        assert caught.value is secondary
    assert images[0][1] == [True]


@pytest.fixture
def allocated_host(case, metadata_fake, monkeypatch):
    controller = host.CropPreviewController(case[0])
    image_module = pytest.importorskip("PIL.Image")
    original_frombytes = image_module.frombytes
    allocated = []

    def allocate(*args, **kwargs):
        image = original_frombytes(*args, **kwargs)
        closes = []
        original_close = image.close

        def close():
            closes.append(True)
            original_close()

        image.close = close
        allocated.append((image, closes))
        return image

    monkeypatch.setattr(image_module, "frombytes", allocate)
    yield controller, allocated
    # Fault controls may deliberately latch uncertainty after actual safe leaf
    # removal. Reset only that injected latch to remove the still-owned empty root.
    if controller.cleanup_uncertain:
        assert not list(controller.private_root.iterdir())
        monkeypatch.setattr(controller, "_uncertain", False)
    assert controller.close()


@pytest.mark.parametrize("site", ["late_result", "late_rgb", "cancel", "cleanup", "owner_constructor"])
@pytest.mark.parametrize("failure_kind", ["ordinary", "keyboard", "exit"])
def test_v3_host_late_failure_closes_allocated_image_exactly_once(case, allocated_host, monkeypatch, site, failure_kind):
    controller, images = allocated_host
    failure = {"ordinary": RuntimeError("inert late failure"), "keyboard": KeyboardInterrupt(), "exit": SystemExit(21)}[failure_kind]
    cancelled = threading.Event()
    if site.startswith("late_"):
        original = runtime._snapshot
        target = "result.json" if site == "late_result" else "rgb.bin"
        reads = 0

        def snapshot(path, limit):
            nonlocal reads
            if path.name == target:
                reads += 1
                if reads == 2:
                    raise failure
            return original(path, limit)

        monkeypatch.setattr(runtime, "_snapshot", snapshot)
    elif site == "cancel":
        original = controller._read_result

        def read(*args):
            value = original(*args)
            cancelled.set()
            return value

        monkeypatch.setattr(controller, "_read_result", read)
    elif site == "cleanup":
        original = controller._cleanup_leaf

        def cleanup(*args):
            original(*args)
            raise failure

        monkeypatch.setattr(controller, "_cleanup_leaf", cleanup)
    else:
        def supervise(script, args, **options):
            code = fake_supervisor(script, args, **options)

            def constructor(*_args):
                raise failure

            monkeypatch.setattr(runtime, "CropRasterPreview", constructor)
            return code

        monkeypatch.setattr(host.process_supervision, "_run_cli_with_deadline", supervise)
    expected = runtime.CropPreviewError if site in {"cancel", "cleanup"} or isinstance(failure, Exception) else type(failure)
    with pytest.raises(expected) as caught:
        controller.render_with_view(case[1], cancel_requested=cancelled.is_set)
    if expected is not runtime.CropPreviewError:
        assert caught.value is failure
    if site in {"cancel", "cleanup"}:
        assert caught.value.code == ("preview_cancelled" if site == "cancel" else "cleanup_unconfirmed")
    assert len(images) == 1 and images[0][1] == [True]


@pytest.mark.parametrize("mutation", ["mode", "size", "pixels", "short_bytes", "mutable_bytes",
                                      "ordinary", "keyboard", "exit"])
def test_metadata_host_checks_actual_constructed_image_before_owner(case, allocated_host, monkeypatch, mutation):
    controller, images = allocated_host
    image_module = pytest.importorskip("PIL.Image")
    allocate = image_module.frombytes
    failure = {"ordinary": RuntimeError("inert RGB copy"), "keyboard": KeyboardInterrupt(),
               "exit": SystemExit(17)}.get(mutation)

    def frombytes(mode, size, raw):
        if mutation == "mode":
            mode = "L"
        elif mutation == "size":
            size = (size[0] - 1, size[1])
        elif mutation == "pixels":
            raw = bytes([raw[0] ^ 1]) + raw[1:]
        image = allocate(mode, size, raw)
        if mutation == "short_bytes":
            image.tobytes = lambda: b""
        elif mutation == "mutable_bytes":
            image.tobytes = lambda: bytearray(raw)
        elif failure is not None:
            image.tobytes = lambda: (_ for _ in ()).throw(failure)
        return image

    monkeypatch.setattr(image_module, "frombytes", frombytes)
    expected = type(failure) if mutation in {"keyboard", "exit"} else runtime.CropPreviewError
    with pytest.raises(expected) as caught:
        controller.render_with_view(case[1])
    if mutation in {"keyboard", "exit"}:
        assert caught.value is failure
    elif mutation != "ordinary":
        assert caught.value.code == "geometry_mismatch"
    assert len(images) == 1 and images[0][1] == [True]
    assert not controller.cleanup_uncertain and not list(controller.private_root.iterdir())


def test_v2_defaults_and_pil_admission_do_not_inherit_annotation_numeric_domain(case, monkeypatch):
    assert worker.PROTOCOL_SCHEMA_VERSION == 2 and worker.MAX_RESULT_BYTES == 4096
    assert worker.MAX_METADATA_RESULT_BYTES == 8192 and "ocr_crop_raster_view.py" not in worker.PRODUCERS
    scope = copy.deepcopy(case[1])
    scope["page_geometry"] = {"display_rect_points": [0., 0., 1e12, 1e12],
                               "cropbox_points": [0., 0., 1e12, 1e12], "rotation_degrees": 0}
    scope.pop("scope_sha256")
    scope["scope_sha256"] = crops._digest(scope)
    monkeypatch.setattr(host.process_supervision, "_run_cli_with_deadline", fake_supervisor)
    monkeypatch.setattr(runtime, "_render_pdf_with_view", lambda *_a, **_k: pytest.fail("ordinary preview requested metadata"))
    controller = host.CropPreviewController(case[0])
    try:
        result = controller.render(scope)
        assert result.mode == "RGB" and result.size == (3, 2)
        result.close()
        with pytest.raises(ValueError):
            geometry._expected_geometry(scope, "fit")
    finally:
        assert controller.close()


@pytest.mark.parametrize("profile", ["fit", "dpi288", "dpi576"])
@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_actual_generated_v3_child_matches_direct_metadata_and_legacy_rgb(tmp_path, rotation, profile):
    """Real contained decoder/render; generated shapes only, no OCR/model calls."""
    pytest.importorskip("pymupdf")
    pytest.importorskip("PIL")
    from test_ocr_crop_review_runtime import _scope, _workspace
    directory = tmp_path / "generated-review"
    directory.mkdir()
    workspace = _workspace(directory, rotation=rotation, cropped=True, annotations=True)
    scope = _scope(workspace, bbox=(.073, .121, .813, .857))
    legacy = direct = controller = result = None
    try:
        legacy = runtime.render_crop_scope(workspace, scope, preview_profile=profile)
        direct = runtime.render_crop_scope_with_view(workspace, scope, preview_profile=profile)
        controller = host.CropPreviewController(workspace)
        result = controller.render_with_view(scope, preview_profile=profile)
        assert type(result) is runtime.CropRasterPreview
        assert result.raster_view_bytes == direct.raster_view_bytes
        assert result.image.tobytes() == direct.image.tobytes() == legacy.tobytes()
        assert result.image.size == direct.image.size == legacy.size
        assert result.raster_view["rgb_sha256"] == worker._sha(result.image.tobytes())
        assert not controller.cleanup_uncertain and not list(controller.private_root.iterdir())
    finally:
        if result is not None:
            result.close()
        if direct is not None:
            direct.close()
        if legacy is not None:
            legacy.close()
        if controller is not None:
            assert controller.close()
