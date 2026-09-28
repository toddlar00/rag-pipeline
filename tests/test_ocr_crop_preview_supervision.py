"""Isolated preview lifecycle/failure controls and generated-only child renders."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import threading
import time

import pytest

import ocr_crop_preview_supervision as host
import ocr_crop_preview_worker as worker
import ocr_crop_review_runtime as runtime
from ocr_review_runtime import ReviewWorkspace
from test_ocr_review import report
from test_ocr_crop_preview_worker import scope_for


@pytest.fixture(autouse=True)
def scratch_parent(tmp_path, monkeypatch):
    parent = tmp_path / "host-temp"
    parent.mkdir()
    monkeypatch.setattr(host.tempfile, "gettempdir", lambda: str(parent))
    return parent


@pytest.fixture
def case(tmp_path, monkeypatch):
    image = pytest.importorskip("PIL.Image")
    source = tmp_path / "generated-source.pdf"
    raw = b"Generated inert source; never decoded by deterministic controls"
    source.write_bytes(raw)
    saved = report()
    saved["source_sha256"] = hashlib.sha256(raw).hexdigest()
    recovery = tmp_path / "recovery.json"
    recovery.write_text(json.dumps(saved), encoding="utf-8")
    output = tmp_path / "review-output"
    output.mkdir()
    workspace = ReviewWorkspace(source, recovery, output)
    monkeypatch.setattr(runtime, "_render_pdf", lambda _raw, _scope, **_kwargs: image.new("RGB", (3, 2), "red"))
    monkeypatch.setenv(worker.CHILD_ENV, "1")
    return workspace, scope_for(raw)


def fake_supervisor(script, arguments, **options):
    assert script == Path(host.__file__).with_name("ocr_crop_preview_worker.py")
    assert len(arguments) == 2 and arguments[0].startswith("--request=")
    assert options["timeout"] == host.WORKER_TIMEOUT_SECONDS
    assert options["stdout_target"] == options["stderr_target"] == subprocess.DEVNULL
    options["on_child_started"](object())
    return worker.run_request(arguments[0].split("=", 1)[1], arguments[1].split("=", 1)[1])


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setattr(host.process_supervision, "_run_cli_with_deadline", fake_supervisor)


def expect_error(code, function):
    with pytest.raises(runtime.CropPreviewError) as caught:
        function()
    assert caught.value.code == code
    assert str(caught.value) == "original crop preview unavailable or inputs changed"


def test_success_raw_rgb_detaches_and_cleans_each_leaf_not_workspace(case, fake, scratch_parent):
    workspace, scope = case
    sentinel = workspace.output_dir / "operator-file"
    sentinel.write_bytes(b"keep")
    controller = host.CropPreviewController(workspace)
    root = controller.private_root
    assert root.parent == scratch_parent and root.is_dir()
    assert not root.is_relative_to(workspace.output_dir)
    for _ in range(2):
        image = controller.render(scope)
        assert image.mode == "RGB" and image.size == (3, 2)
        assert image.getpixel((0, 0)) == (255, 0, 0) and not image.info
        assert list(root.iterdir()) == []
        assert not controller.cleanup_uncertain
    assert controller.close() is True and controller.close() is True
    assert not root.exists() and sentinel.read_bytes() == b"keep"
    expect_error("preview_cancelled", lambda: controller.render(scope))


def test_shared_temp_parent_is_never_hardened_or_deleted(case, fake, scratch_parent, monkeypatch):
    original = host.storage_policy.ensure_private_directory
    hardenings = []
    sentinel = scratch_parent / "other-owner"
    sentinel.write_bytes(b"keep")
    def harden(path, **kwargs):
        assert path != scratch_parent
        hardenings.append(path)
        return original(path, **kwargs)
    monkeypatch.setattr(host.storage_policy, "ensure_private_directory", harden)
    controller = host.CropPreviewController(case[0])
    assert controller.render(case[1]).mode == "RGB"
    assert controller.close() and len(hardenings) == 2
    assert scratch_parent.is_dir() and sentinel.read_bytes() == b"keep"


@pytest.mark.parametrize("selection", ["relative", "parent-segment", "missing", "file", "output", "output-child",
                                       "repository", "cache", "cache-child", "link"])
def test_unsafe_temp_selection_refused_before_any_owned_directory(case, scratch_parent, monkeypatch, selection):
    output = case[0].output_dir
    cache = scratch_parent.parent / "gradio-cache"
    cache.mkdir()
    (cache / "child").mkdir()
    (output / "child").mkdir()
    regular = scratch_parent / "not-directory"
    regular.write_bytes(b"keep")
    link = scratch_parent.parent / "temp-link"
    if selection == "link":
        try:
            link.symlink_to(scratch_parent, target_is_directory=True)
        except OSError:
            pytest.skip("directory symlink unavailable")
    paths = {"relative": Path("relative-temp"), "parent-segment": scratch_parent / ".." / "host-temp",
        "missing": scratch_parent / "missing", "file": regular, "output": output,
        "output-child": output / "child", "repository": worker.ROOT,
        "cache": cache, "cache-child": cache / "child", "link": link}
    monkeypatch.setenv("GRADIO_TEMP_DIR", str(cache))
    monkeypatch.setattr(host.tempfile, "gettempdir", lambda: str(paths[selection]))
    expect_error("input_verification", lambda: host.CropPreviewController(case[0]))
    assert not list(scratch_parent.glob("ocr-crop-preview-private-*"))
    assert regular.read_bytes() == b"keep"


def test_temp_parent_inspection_failure_is_static_and_does_not_allocate(case, scratch_parent, monkeypatch):
    original = worker._identity
    def deny(path, **kwargs):
        if path == scratch_parent:
            raise PermissionError("private details")
        return original(path, **kwargs)
    monkeypatch.setattr(worker, "_identity", deny)
    expect_error("input_verification", lambda: host.CropPreviewController(case[0]))
    assert list(scratch_parent.iterdir()) == []


def test_later_temp_selection_drift_does_not_retarget_owned_storage(case, fake, scratch_parent, monkeypatch):
    controller = host.CropPreviewController(case[0])
    other = scratch_parent.parent / "different-host-temp"
    other.mkdir()
    monkeypatch.setattr(host.tempfile, "gettempdir", lambda: str(other))
    assert controller.render(case[1]).mode == "RGB"
    assert controller.private_root.parent == scratch_parent and not list(other.iterdir())
    assert controller.close()


@pytest.mark.parametrize("name", ["source.pdf", "request.json", "ocr_crop_preview_worker.py",
                                  "generated-source.pdf", "recovery.json"])
def test_external_staging_preserves_strict_ctime_only_rechecks(case, monkeypatch, name):
    controller = host.CropPreviewController(case[0])
    original = runtime._snapshot
    armed = False
    def run(*args, **kwargs):
        nonlocal armed
        result = fake_supervisor(*args, **kwargs)
        armed = True
        return result
    def snapshot(path, limit):
        raw, digest, fingerprint = original(path, limit)
        if armed and path.name == name:
            fingerprint = (*fingerprint[:4], fingerprint[4] + 1)
        return raw, digest, fingerprint
    monkeypatch.setattr(host.process_supervision, "_run_cli_with_deadline", run)
    monkeypatch.setattr(runtime, "_snapshot", snapshot)
    expect_error("input_changed", lambda: controller.render(case[1]))
    assert not controller.cleanup_uncertain and controller.close()


def test_shared_temp_ancestor_of_workspace_is_admitted(case, fake, scratch_parent, monkeypatch):
    parent = scratch_parent.parent
    monkeypatch.setattr(host.tempfile, "gettempdir", lambda: str(parent))
    controller = host.CropPreviewController(case[0])
    assert controller.private_root.parent == parent
    assert controller.render(case[1]).mode == "RGB" and controller.close()
    assert case[0].output_dir.is_dir()


@pytest.mark.parametrize("exit_code,error", [(124, "preview_timeout"), (130, "preview_cancelled"), (2, "pdf_render")])
def test_valid_looking_output_never_survives_nonzero_exit(case, monkeypatch, exit_code, error):
    controller = host.CropPreviewController(case[0])
    def run(*args, **kwargs):
        assert fake_supervisor(*args, **kwargs) == 0
        return exit_code
    monkeypatch.setattr(host.process_supervision, "_run_cli_with_deadline", run)
    expect_error(error, lambda: controller.render(case[1]))
    assert list(controller.private_root.iterdir()) == []
    assert controller.close() is True


@pytest.mark.parametrize("field,value", [("width", True), ("width", 1401), ("height", 0),
    ("rgb_size", 17), ("rgb_sha256", "0" * 64), ("request_sha256", "0" * 64),
    ("scope_sha256", "0" * 64), ("source_sha256", "0" * 64), ("mode", "RGBA"),
    ("schema_version", True), ("status", "failed"), ("code", "private path")])
def test_wrong_result_binding_dimensions_and_types_refused(case, monkeypatch, field, value):
    controller = host.CropPreviewController(case[0])
    def run(script, args, **kwargs):
        code = fake_supervisor(script, args, **kwargs)
        leaf = Path(args[0].split("=", 1)[1]).parent
        result_path = leaf / "result.json"
        result = json.loads(result_path.read_bytes())
        result[field] = value
        result_path.write_bytes(worker._encoded(result, worker.MAX_RESULT_BYTES))
        return code
    monkeypatch.setattr(host.process_supervision, "_run_cli_with_deadline", run)
    with pytest.raises(runtime.CropPreviewError):
        controller.render(case[1])
    assert not controller.cleanup_uncertain and controller.close()


def test_failed_geometry_result_preserves_static_failure_code(case, fake, monkeypatch):
    def fail(*_args, **_kwargs):
        raise runtime.CropPreviewError(code="geometry_mismatch")
    monkeypatch.setattr(runtime, "_render_pdf", fail)
    controller = host.CropPreviewController(case[0])
    expect_error("geometry_mismatch", lambda: controller.render(case[1]))
    assert controller.close()


@pytest.mark.parametrize("code", ["cleanup_unconfirmed", "preview_busy", "preview_timeout", "preview_cancelled",
                                  "private path", None, True, []])
def test_child_cannot_forge_host_lifecycle_failure_or_latch_state(case, monkeypatch, code):
    controller = host.CropPreviewController(case[0])
    def failed_render(*_args, **_kwargs):
        raise runtime.CropPreviewError(code="pdf_render")
    monkeypatch.setattr(runtime, "_render_pdf", failed_render)
    def run(script, args, **kwargs):
        result_code = fake_supervisor(script, args, **kwargs)
        path = Path(args[0].split("=", 1)[1]).parent / "result.json"
        value = json.loads(path.read_bytes())
        value["code"] = code
        path.write_bytes(worker._encoded(value, worker.MAX_RESULT_BYTES))
        return result_code
    monkeypatch.setattr(host.process_supervision, "_run_cli_with_deadline", run)
    expect_error("pdf_render", lambda: controller.render(case[1]))
    assert not controller.cleanup_uncertain and controller.close()


def test_pre_cancel_allocates_no_request_or_child(case, monkeypatch):
    controller = host.CropPreviewController(case[0])
    monkeypatch.setattr(host.process_supervision, "_run_cli_with_deadline", lambda *a, **k: pytest.fail("launched"))
    expect_error("preview_cancelled", lambda: controller.render(case[1], cancel_requested=lambda: True))
    assert list(controller.private_root.iterdir()) == []
    assert controller.close()


def test_start_gate_cancel_uses_shared_cleanup_boundary(case, monkeypatch):
    controller = host.CropPreviewController(case[0])
    cancelled = threading.Event()
    def run(_script, _args, **kwargs):
        cancelled.set()
        with pytest.raises(host._Cancelled):
            kwargs["on_child_started"](object())
        raise host._Cancelled()  # Shared supervisor re-raises only after confirmed cleanup.
    monkeypatch.setattr(host.process_supervision, "_run_cli_with_deadline", run)
    expect_error("preview_cancelled", lambda: controller.render(case[1], cancel_requested=cancelled.is_set))
    assert not controller.cleanup_uncertain and controller.close()


def test_cancel_after_worker_completion_discards_valid_rgb(case, monkeypatch):
    controller = host.CropPreviewController(case[0])
    cancelled = threading.Event()
    def run(*args, **kwargs):
        code = fake_supervisor(*args, **kwargs)
        cancelled.set()
        return code
    monkeypatch.setattr(host.process_supervision, "_run_cli_with_deadline", run)
    expect_error("preview_cancelled", lambda: controller.render(case[1], cancel_requested=cancelled.is_set))
    assert list(controller.private_root.iterdir()) == [] and controller.close()


@pytest.mark.parametrize("error", [host._CleanupUnconfirmed(), RuntimeError("private process failure")])
def test_uncertain_supervisor_retains_known_residue_and_blocks_all_starts(case, monkeypatch, error):
    controller = host.CropPreviewController(case[0])
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(host.process_supervision, "_run_cli_with_deadline", fail)
    expect_error("cleanup_unconfirmed", lambda: controller.render(case[1]))
    assert controller.cleanup_uncertain and len(list(controller.private_root.iterdir())) == 1
    expect_error("cleanup_unconfirmed", lambda: controller.render(case[1]))
    assert controller.close(0) is False


@pytest.mark.parametrize("name", ["rgb.bin", "source.pdf", "request.json", "result.json"])
def test_replaced_file_is_not_deleted(case, monkeypatch, name):
    controller = host.CropPreviewController(case[0])
    changed = []
    def run(script, args, **kwargs):
        code = fake_supervisor(script, args, **kwargs)
        path = Path(args[0].split("=", 1)[1]).parent / name
        replacement = path.parent / "replacement"
        replacement.write_bytes(path.read_bytes())
        os.replace(replacement, path)
        changed.append(path)
        return code
    monkeypatch.setattr(host.process_supervision, "_run_cli_with_deadline", run)
    expect_error("cleanup_unconfirmed", lambda: controller.render(case[1]))
    assert changed[0].exists() and controller.cleanup_uncertain and not controller.close(0)


def test_unexpected_file_never_recursively_removed(case, monkeypatch):
    controller = host.CropPreviewController(case[0])
    unknown = []
    def run(script, args, **kwargs):
        code = fake_supervisor(script, args, **kwargs)
        path = Path(args[0].split("=", 1)[1]).parent / "unknown"
        path.write_bytes(b"keep")
        unknown.append(path)
        return code
    monkeypatch.setattr(host.process_supervision, "_run_cli_with_deadline", run)
    expect_error("cleanup_unconfirmed", lambda: controller.render(case[1]))
    assert unknown[0].read_bytes() == b"keep" and not controller.close(0)


@pytest.mark.parametrize("target", ["document", "output_path", "source_path"])
def test_live_workspace_mutation_refuses_image_but_cleans_original_storage(case, monkeypatch, target):
    workspace, scope = case
    controller = host.CropPreviewController(workspace)
    root = controller.private_root
    def run(script, args, **kwargs):
        code = fake_supervisor(script, args, **kwargs)
        if target == "document":
            import copy
            workspace.document = copy.copy(workspace.document)
        elif target == "output_path":
            replacement = workspace.output_dir / "different-output"
            replacement.mkdir()
            workspace.output_dir = replacement
        else:
            workspace.pdf_path = workspace.pdf_path.with_name("different-source.pdf")
        return code
    monkeypatch.setattr(host.process_supervision, "_run_cli_with_deadline", run)
    expect_error("input_changed", lambda: controller.render(scope))
    assert list(root.iterdir()) == [] and not controller.cleanup_uncertain
    assert controller.close() and not root.exists()


@pytest.mark.parametrize("target", ["parent", "root", "leaf"])
def test_replaced_storage_is_retained_and_latches_cleanup_uncertainty(case, monkeypatch, target):
    workspace, scope = case
    controller = host.CropPreviewController(workspace)
    preserved = []
    def run(script, args, **kwargs):
        code = fake_supervisor(script, args, **kwargs)
        leaf = Path(args[0].split("=", 1)[1]).parent
        path = {"parent": controller.private_root.parent, "root": controller.private_root, "leaf": leaf}[target]
        moved = path.with_name(path.name + "-preserved")
        path.rename(moved)
        path.mkdir()
        sentinel = path / "not-owned"
        sentinel.write_bytes(b"retain")
        preserved.extend((moved, sentinel))
        return code
    monkeypatch.setattr(host.process_supervision, "_run_cli_with_deadline", run)
    expect_error("cleanup_unconfirmed", lambda: controller.render(scope))
    assert preserved[0].is_dir() and preserved[1].read_bytes() == b"retain"
    assert controller.cleanup_uncertain and not controller.close(0)


def test_replaced_workspace_output_refuses_image_but_cleans_independent_storage(case, monkeypatch):
    workspace, scope = case
    controller = host.CropPreviewController(workspace)
    original = workspace.output_dir
    def run(script, args, **kwargs):
        code = fake_supervisor(script, args, **kwargs)
        original.rename(original.with_name("retained-original-output"))
        original.mkdir()
        (original / "not-owned").write_bytes(b"keep")
        return code
    monkeypatch.setattr(host.process_supervision, "_run_cli_with_deadline", run)
    expect_error("input_changed", lambda: controller.render(scope))
    assert not controller.cleanup_uncertain and controller.close()
    assert (original / "not-owned").read_bytes() == b"keep"


@pytest.mark.parametrize("change", ["truncate", "grow", "same-size"])
def test_changed_raw_rgb_refused_without_image_or_cleanup_uncertainty(case, monkeypatch, change):
    controller = host.CropPreviewController(case[0])
    def run(script, args, **kwargs):
        code = fake_supervisor(script, args, **kwargs)
        path = Path(args[0].split("=", 1)[1]).parent / "rgb.bin"
        raw = path.read_bytes()
        path.write_bytes({"truncate": raw[:-1], "grow": raw + b"x", "same-size": b"x" * len(raw)}[change])
        return code
    monkeypatch.setattr(host.process_supervision, "_run_cli_with_deadline", run)
    expect_error("input_changed", lambda: controller.render(case[1]))
    assert not controller.cleanup_uncertain and controller.close()


@pytest.mark.parametrize("name", ["rgb.bin", "result.json"])
def test_post_allocation_output_mutation_is_rechecked(case, monkeypatch, name):
    controller = host.CropPreviewController(case[0])
    original = runtime._snapshot
    armed = []
    def run(script, args, **kwargs):
        code = fake_supervisor(script, args, **kwargs)
        armed.append(Path(args[0].split("=", 1)[1]).parent / name)
        return code
    def snapshot(path, limit):
        result = original(path, limit)
        if armed and path == armed[0]:
            armed.pop()
            path.write_bytes(path.read_bytes() + b" ")
        return result
    monkeypatch.setattr(host.process_supervision, "_run_cli_with_deadline", run)
    monkeypatch.setattr(runtime, "_snapshot", snapshot)
    expect_error("input_changed", lambda: controller.render(case[1]))
    assert not controller.cleanup_uncertain and controller.close()


def test_busy_request_close_and_bounded_close_do_not_hold_state_lock(case, monkeypatch):
    controller = host.CropPreviewController(case[0])
    entered, release = threading.Event(), threading.Event()
    errors = []
    def run(_script, _args, **kwargs):
        entered.set()
        assert release.wait(5)
        assert kwargs["cancel_requested"]()
        return 130
    monkeypatch.setattr(host.process_supervision, "_run_cli_with_deadline", run)
    def render():
        try:
            controller.render(case[1])
        except BaseException as error:
            errors.append(error)
    thread = threading.Thread(target=render)
    thread.start()
    try:
        assert entered.wait(5)
        expect_error("preview_busy", lambda: controller.render(case[1]))
        controller.request_close()
        started = time.monotonic()
        assert controller.close(0) is False and time.monotonic() - started < 1
        assert controller.cleanup_uncertain
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive() and errors[0].code == "cleanup_unconfirmed"


def test_close_waits_for_cancellation_and_returns_true_when_cleanup_confirmed(case, monkeypatch):
    controller = host.CropPreviewController(case[0])
    entered = threading.Event()
    errors = []
    def run(_script, _args, **kwargs):
        entered.set()
        deadline = time.monotonic() + 5
        while not kwargs["cancel_requested"]():
            assert time.monotonic() < deadline
            time.sleep(.005)
        return 130
    monkeypatch.setattr(host.process_supervision, "_run_cli_with_deadline", run)
    def render():
        try:
            controller.render(case[1])
        except BaseException as error:
            errors.append(error)
    thread = threading.Thread(target=render)
    thread.start()
    assert entered.wait(5)
    assert controller.close(5)
    thread.join(5)
    assert errors[0].code == "preview_cancelled" and not controller.private_root.exists()


@pytest.mark.parametrize("target", ["root", "leaf"])
def test_mkdir_then_unavailable_identity_is_explicit_uncertainty(case, monkeypatch, target, scratch_parent):
    original = worker._identity
    controller = None if target == "root" else host.CropPreviewController(case[0])
    def fail(path, *, directory=False):
        if directory and path.name.startswith("ocr-crop-preview-private-" if target == "root" else "preview-"):
            raise OSError("identity unavailable")
        return original(path, directory=directory)
    monkeypatch.setattr(worker, "_identity", fail)
    if target == "root":
        expect_error("cleanup_unconfirmed", lambda: host.CropPreviewController(case[0]))
    else:
        expect_error("cleanup_unconfirmed", lambda: controller.render(case[1]))
        assert controller.cleanup_uncertain
    assert list(scratch_parent.glob("ocr-crop-preview-private-*"))


def test_constructor_cleanup_does_not_follow_replaced_parent_even_with_same_root_inode(case, monkeypatch):
    workspace, _scope = case
    original = host.storage_policy.ensure_private_directory
    retained = []
    def replace_parent(path, **kwargs):
        original(path, **kwargs)
        parent = path.parent
        moved = parent.with_name(parent.name + "-preserved")
        parent.rename(moved)
        parent.mkdir()
        (moved / path.name).rename(path)
        retained.append(path)
    monkeypatch.setattr(host.storage_policy, "ensure_private_directory", replace_parent)
    expect_error("cleanup_unconfirmed", lambda: host.CropPreviewController(workspace))
    assert retained[0].is_dir()


def test_environment_cannot_inherit_auth_cache_or_python_startup_overrides(monkeypatch):
    for name in ("RAG_OCR_REVIEW_TOKEN", "GRADIO_TEMP_DIR", "PYTHONPATH", "PYTHONOPTIMIZE", "__PYVENV_LAUNCHER__"):
        monkeypatch.setenv(name, "private")
    environment = host._environment()
    assert all(environment[name] is None for name in
        ("RAG_OCR_REVIEW_TOKEN", "GRADIO_TEMP_DIR", "PYTHONPATH", "PYTHONOPTIMIZE", "__PYVENV_LAUNCHER__"))
    assert environment[worker.CHILD_ENV] == "1" and environment["PYTHONNOUSERSITE"] == "1"


@pytest.mark.parametrize("value", [True, None, "1", -1, 41, float("inf"), float("nan"), 10 ** 1000],
                         ids=["bool", "none", "string", "negative", "over-bound", "infinite", "nan", "huge-int"])
def test_close_timeout_has_fixed_strict_bound(case, value):
    controller = host.CropPreviewController(case[0])
    expect_error("preview_unavailable", lambda: controller.close(value))
    assert controller.close()


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_actual_fixed_child_matches_existing_generated_crop_pixels(tmp_path, rotation):
    pytest.importorskip("pymupdf")
    pytest.importorskip("PIL")
    from test_ocr_crop_review_runtime import _workspace, _scope
    output = tmp_path / "generated-review"
    output.mkdir()
    workspace = _workspace(output, rotation=rotation)
    scope = _scope(workspace)
    expected = runtime.render_crop_scope(workspace, scope)
    controller = host.CropPreviewController(workspace)
    try:
        actual = controller.render(scope)
        assert actual.mode == expected.mode == "RGB" and actual.size == expected.size
        assert actual.tobytes() == expected.tobytes()
        assert not controller.cleanup_uncertain and list(controller.private_root.iterdir()) == []
    finally:
        assert controller.close()


def test_actual_fixed_child_deadline_has_no_accepted_output(case, monkeypatch):
    controller = host.CropPreviewController(case[0])
    monkeypatch.setattr(host, "WORKER_TIMEOUT_SECONDS", .001)
    expect_error("preview_timeout", lambda: controller.render(case[1]))
    assert not controller.cleanup_uncertain and controller.close()


def test_actual_shared_start_gate_cancel_cleans_before_target_dispatch(case, monkeypatch):
    controller = host.CropPreviewController(case[0])
    actual = host.process_supervision._run_cli_with_deadline
    cancelled = threading.Event()
    def wrapped(script, args, **kwargs):
        before_release = kwargs["on_child_started"]
        def cancel(process):
            cancelled.set()
            before_release(process)
        return actual(script, args, **dict(kwargs, on_child_started=cancel))
    monkeypatch.setattr(host.process_supervision, "_run_cli_with_deadline", wrapped)
    expect_error("preview_cancelled", lambda: controller.render(case[1], cancel_requested=cancelled.is_set))
    assert not controller.cleanup_uncertain and controller.close()


@pytest.mark.parametrize("profile", [None, True, 4, [], {}, "dpi144", "FIT", "dpi288 "])
def test_controller_invalid_profile_refuses_before_source_or_staging(case, monkeypatch, profile):
    controller = host.CropPreviewController(case[0])
    try:
        monkeypatch.setattr(case[0], "verify_inputs", lambda: pytest.fail("invalid profile read workspace"))
        monkeypatch.setattr(runtime, "_snapshot", lambda *_a, **_k: pytest.fail("invalid profile read bytes"))
        monkeypatch.setattr(host.process_supervision, "_run_cli_with_deadline",
                            lambda *_a, **_k: pytest.fail("invalid profile launched worker"))
        expect_error("scope_validation", lambda: controller.render(case[1], preview_profile=profile))
        assert controller._active is None and not list(controller.private_root.iterdir())
        assert not controller.cleanup_uncertain
    finally:
        assert controller.close()


@pytest.mark.parametrize("profile", [None, "fit", "dpi288", "dpi576"])
def test_controller_binds_selected_profile_in_actual_inert_worker_protocol(case, monkeypatch, profile):
    controller = host.CropPreviewController(case[0])
    seen = []
    def run(script, args, **kwargs):
        path = Path(args[0].split("=", 1)[1])
        request = json.loads(path.read_bytes())
        assert request["schema_version"] == 2
        assert request["preview_profile"] == ("fit" if profile is None else profile)
        assert request["scope"] == case[1]
        code = fake_supervisor(script, args, **kwargs)
        result = json.loads((path.parent / "result.json").read_bytes())
        seen.append((request, result))
        return code
    monkeypatch.setattr(host.process_supervision, "_run_cli_with_deadline", run)
    try:
        kwargs = {} if profile is None else {"preview_profile": profile}
        image = controller.render(case[1], **kwargs)
        assert image.size == (3, 2) and image.tobytes() == bytes([255, 0, 0]) * 6
        assert len(seen) == 1 and seen[0][1]["preview_profile"] == seen[0][0]["preview_profile"]
        assert not controller.cleanup_uncertain and not list(controller.private_root.iterdir())
    finally:
        assert controller.close()


@pytest.mark.parametrize("failed", [False, True])
@pytest.mark.parametrize("mutation", ["wrong-profile", "missing-profile", "bad-profile-type", "legacy", "bool-schema"])
def test_controller_refuses_mixed_profile_result_before_rgb_allocation(case, monkeypatch, failed, mutation):
    image_module = pytest.importorskip("PIL.Image")
    controller = host.CropPreviewController(case[0])
    if failed:
        def fail(*_a, **_k):
            raise runtime.CropPreviewError(code="raster_limit")
        monkeypatch.setattr(runtime, "_render_pdf", fail)
    def run(script, args, **kwargs):
        code = fake_supervisor(script, args, **kwargs)
        path = Path(args[0].split("=", 1)[1]).parent / "result.json"
        result = json.loads(path.read_bytes())
        if mutation == "wrong-profile":
            result["preview_profile"] = "dpi576"
        elif mutation == "missing-profile":
            result.pop("preview_profile")
        elif mutation == "bad-profile-type":
            result["preview_profile"] = True
        else:
            result["schema_version"] = 1 if mutation == "legacy" else True
        path.write_bytes(worker._encoded(result, worker.MAX_RESULT_BYTES))
        # Intercept only host RGB reconstruction, after the inert worker return.
        monkeypatch.setattr(image_module, "frombytes", lambda *_a, **_k: pytest.fail("mismatched result allocated RGB"))
        return code
    monkeypatch.setattr(host.process_supervision, "_run_cli_with_deadline", run)
    try:
        expect_error("input_changed", lambda: controller.render(case[1], preview_profile="dpi288"))
        assert not controller.cleanup_uncertain and not list(controller.private_root.iterdir())
    finally:
        assert controller.close()


@pytest.mark.parametrize("profile", ["dpi288", "dpi576"])
@pytest.mark.parametrize("rotation", [0, 90])
def test_actual_detail_child_matches_generated_direct_source_raster(tmp_path, rotation, profile):
    """Real contained generated PDF rendering; no OCR/model inference."""
    pytest.importorskip("pymupdf")
    pytest.importorskip("PIL")
    from test_ocr_crop_review_runtime import _workspace, _scope
    output = tmp_path / "generated-detail-review"
    output.mkdir()
    workspace = _workspace(output, rotation=rotation, annotations=True)
    scope = _scope(workspace, bbox=(.073, .121, .813, .857))
    expected = runtime.render_crop_scope(workspace, scope, preview_profile=profile)
    controller = host.CropPreviewController(workspace)
    try:
        actual = controller.render(scope, preview_profile=profile)
        assert actual.mode == expected.mode == "RGB" and actual.size == expected.size
        assert actual.tobytes() == expected.tobytes() and not actual.info
        assert not controller.cleanup_uncertain and not list(controller.private_root.iterdir())
    finally:
        assert controller.close()


class _OwnedImage:
    """No raster allocation: observe only the explicit ownership contract."""

    def __init__(self, close_failure=None):
        self.closes = 0
        self.close_failure = close_failure

    def close(self):
        self.closes += 1
        if self.close_failure is not None:
            raise self.close_failure


def _ownership_failure(kind):
    if kind == "refusal":
        return runtime.CropPreviewError(code="input_changed")
    return {"ordinary": RuntimeError, "keyboard": KeyboardInterrupt,
            "exit": SystemExit, "cancelled": host._Cancelled}[kind]("original private failure")


@pytest.fixture
def read_result_owner(monkeypatch, tmp_path):
    """Actual closed result parser with inert bytes and a close-counting image."""
    image_module = pytest.importorskip("PIL.Image")
    controller = object.__new__(host.CropPreviewController)
    request = {"source_sha256": "a" * 64, "scope": {"scope_sha256": "b" * 64}, "preview_profile": "fit"}
    result, _ = worker._result(request, "c" * 64)
    pixels = b"x" * 18
    result.update(status="complete", width=3, height=2, rgb_size=18, rgb_sha256=worker._sha(pixels))
    raw = worker._encoded(result, worker.MAX_RESULT_BYTES)
    values = {"result.json": (raw, worker._sha(raw), (1, 2, len(raw), 4, 5)),
              "rgb.bin": (pixels, worker._sha(pixels), (1, 3, len(pixels), 4, 5))}
    calls, allocated = [], []
    image = _OwnedImage()

    def snapshot(path, limit):
        calls.append((path.name, limit))
        return values[path.name]

    def allocate(mode, dimensions, value):
        assert (mode, dimensions, value) == ("RGB", (3, 2), pixels)
        allocated.append(image)
        return image

    monkeypatch.setattr(worker, "_check_files", lambda *_args: None)
    monkeypatch.setattr(runtime, "_snapshot", snapshot)
    monkeypatch.setattr(image_module, "frombytes", allocate)
    return controller, tmp_path, request, image, calls, allocated, snapshot


@pytest.mark.parametrize("late_call", [3, 4])
@pytest.mark.parametrize("failure_kind", ["refusal", "ordinary", "keyboard", "exit"])
@pytest.mark.parametrize("close_kind", [None, "ordinary", "keyboard", "exit"])
def test_read_result_ownership_closes_once_on_late_failure(read_result_owner, monkeypatch,
                                                         late_call, failure_kind, close_kind):
    controller, leaf, request, image, calls, allocated, original = read_result_owner
    primary = _ownership_failure(failure_kind)
    image.close_failure = None if close_kind is None else _ownership_failure(close_kind)

    def snapshot(path, limit):
        value = original(path, limit)
        if len(calls) == late_call:
            raise primary
        return value

    monkeypatch.setattr(runtime, "_snapshot", snapshot)
    with pytest.raises(type(primary)) as caught:
        controller._read_result(leaf, request, "c" * 64, 0)
    assert caught.value is primary
    assert allocated == [image] and image.closes == 1 and len(calls) == late_call


def test_read_result_ownership_success_transfers_open_image(read_result_owner):
    controller, leaf, request, image, calls, allocated, _snapshot = read_result_owner
    image.close_failure = AssertionError("successful return closed image")
    assert controller._read_result(leaf, request, "c" * 64, 0) is image
    assert allocated == [image] and image.closes == 0 and len(calls) == 4


@pytest.fixture
def worker_owner(case, monkeypatch):
    """Real known-file staging/cleanup; no child, PDF decode, or producer reads."""
    controller = host.CropPreviewController(case[0])
    image = _OwnedImage()
    reads, dispatches = [], []
    producers = {name: ("d" * 64, (1, 2, 3, 4, 5)) for name in worker.PRODUCERS}
    monkeypatch.setattr(worker, "_producer_snapshots", lambda: producers)

    def read(*args):
        reads.append(args)
        return image

    def dispatch(_script, _arguments, **options):
        dispatches.append(options)
        options["on_child_started"](object())
        return 0

    monkeypatch.setattr(controller, "_read_result", read)
    monkeypatch.setattr(host.process_supervision, "_run_cli_with_deadline", dispatch)
    active = host._Active()
    yield controller, case, active, image, reads, dispatches
    # Fault tests retain the real cleanup semantics; only empty owned roots are
    # removed here when cleanup was actually confirmed.
    if not controller.cleanup_uncertain:
        assert controller.close()


@pytest.mark.parametrize("site", ["recheck", "cancel"])
@pytest.mark.parametrize("failure_kind", ["refusal", "ordinary", "keyboard", "exit", "cancelled"])
@pytest.mark.parametrize("close_kind", [None, "keyboard"])
def test_worker_ownership_closes_once_after_result(worker_owner, monkeypatch, site, failure_kind, close_kind):
    controller, case, active, image, reads, dispatches = worker_owner
    primary = _ownership_failure(failure_kind)
    image.close_failure = None if close_kind is None else _ownership_failure(close_kind)
    name = "_verify_root" if site == "recheck" else "_check_cancel"
    original = getattr(controller, name)

    def fail_after_result(*args):
        if reads:
            raise primary
        return original(*args)

    monkeypatch.setattr(controller, name, fail_after_result)
    with pytest.raises(type(primary)) as caught:
        controller._worker(case[0].pdf_path.read_bytes(), case[1], active, None)
    assert caught.value is primary and image.closes == 1
    assert len(reads) == len(dispatches) == 1 and not list(controller.private_root.iterdir())
    assert not controller.cleanup_uncertain


@pytest.mark.parametrize("prior_failure", [False, True])
@pytest.mark.parametrize("close_kind", [None, "ordinary", "keyboard", "exit"])
def test_worker_ownership_cleanup_refusal_keeps_existing_precedence(worker_owner, monkeypatch,
                                                                  prior_failure, close_kind):
    controller, case, active, image, reads, dispatches = worker_owner
    image.close_failure = None if close_kind is None else _ownership_failure(close_kind)
    original_cleanup = controller._cleanup_leaf

    def cleanup_then_refuse(*args):
        original_cleanup(*args)
        raise KeyboardInterrupt("cleanup uncertainty has existing precedence")

    monkeypatch.setattr(controller, "_cleanup_leaf", cleanup_then_refuse)
    if prior_failure:
        original = controller._check_cancel

        def cancel_after_read(*args):
            if reads:
                raise SystemExit("earlier render cancellation")
            return original(*args)

        monkeypatch.setattr(controller, "_check_cancel", cancel_after_read)
    expect_error("cleanup_unconfirmed", lambda:
        controller._worker(case[0].pdf_path.read_bytes(), case[1], active, None))
    assert image.closes == 1 and len(reads) == len(dispatches) == 1
    assert controller.cleanup_uncertain and not list(controller.private_root.iterdir())
    assert controller.close(0) is False


def test_worker_ownership_success_transfers_open_image(worker_owner):
    controller, case, active, image, reads, dispatches = worker_owner
    image.close_failure = AssertionError("successful return closed image")
    assert controller._worker(case[0].pdf_path.read_bytes(), case[1], active, None) is image
    assert image.closes == 0 and len(reads) == len(dispatches) == 1
    assert not controller.cleanup_uncertain and not list(controller.private_root.iterdir())


@pytest.mark.parametrize("failure_kind", ["refusal", "keyboard", "exit"])
def test_worker_ownership_inner_failed_transfer_is_not_closed_twice(worker_owner, monkeypatch, failure_kind):
    controller, case, active, image, reads, dispatches = worker_owner
    primary = _ownership_failure(failure_kind)

    def failed_inner(*args):
        reads.append(args)
        image.close()  # The failing inner owner has not returned an image.
        raise primary

    monkeypatch.setattr(controller, "_read_result", failed_inner)
    with pytest.raises(type(primary)) as caught:
        controller._worker(case[0].pdf_path.read_bytes(), case[1], active, None)
    assert caught.value is primary and image.closes == 1
    assert len(reads) == len(dispatches) == 1 and not controller.cleanup_uncertain


def test_worker_ownership_guard_encloses_cleanup_finalizer(worker_owner, monkeypatch):
    controller, case, active, image, reads, dispatches = worker_owner
    original_cleanup = controller._cleanup_leaf
    primary = KeyboardInterrupt("finalizer cancellation")
    image.close_failure = SystemExit("secondary close failure")

    def cleanup(*args):
        original_cleanup(*args)
        raise RuntimeError("cleanup failure")

    def latch():
        raise primary

    monkeypatch.setattr(controller, "_cleanup_leaf", cleanup)
    monkeypatch.setattr(controller, "_latch", latch)
    with pytest.raises(KeyboardInterrupt) as caught:
        controller._worker(case[0].pdf_path.read_bytes(), case[1], active, None)
    assert caught.value is primary and image.closes == 1
    assert len(reads) == len(dispatches) == 1 and not list(controller.private_root.iterdir())


@pytest.fixture
def render_owner(case, monkeypatch):
    controller = host.CropPreviewController(case[0])
    image, returned, active = _OwnedImage(), [], []
    original_active = host._Active

    def make_active():
        value = original_active()
        active.append(value)
        return value

    def render(workspace, scope, renderer):
        assert workspace is case[0] and scope is case[1]
        returned.append(image)
        return image

    monkeypatch.setattr(host, "_Active", make_active)
    monkeypatch.setattr(runtime, "_render_crop_scope", render)
    yield controller, case[1], image, returned, active
    if not controller.cleanup_uncertain:
        assert controller.close()


@pytest.mark.parametrize("failure_kind", ["refusal", "ordinary", "keyboard", "exit", "cancelled"])
@pytest.mark.parametrize("close_kind", [None, "ordinary", "keyboard", "exit"])
def test_render_ownership_closes_once_on_final_cancel_check(render_owner, monkeypatch, failure_kind, close_kind):
    controller, scope, image, returned, active = render_owner
    primary = _ownership_failure(failure_kind)
    image.close_failure = None if close_kind is None else _ownership_failure(close_kind)

    def cancel(*args):
        if returned:
            raise primary

    monkeypatch.setattr(controller, "_check_cancel", cancel)
    expected = runtime.CropPreviewError if failure_kind in ("ordinary", "cancelled") else type(primary)
    with pytest.raises(expected) as caught:
        controller.render(scope)
    if failure_kind in ("ordinary", "cancelled"):
        assert caught.value.code == ("preview_unavailable" if failure_kind == "ordinary" else "preview_cancelled")
    else:
        assert caught.value is primary
    assert returned == [image] and image.closes == 1
    assert controller._active is None and active[0].done.is_set()


@pytest.mark.parametrize("close_kind", [None, "ordinary", "keyboard", "exit"])
def test_render_ownership_final_latch_refusal_closes(render_owner, monkeypatch, close_kind):
    controller, scope, image, returned, active = render_owner
    image.close_failure = None if close_kind is None else _ownership_failure(close_kind)

    def after_return(*args):
        if returned:
            controller._latch()

    monkeypatch.setattr(controller, "_check_cancel", after_return)
    expect_error("cleanup_unconfirmed", lambda: controller.render(scope))
    assert image.closes == 1 and returned == [image]
    assert controller._active is None and active[0].done.is_set() and controller.close(0) is False


@pytest.mark.parametrize("failure_kind", ["ordinary", "keyboard", "exit"])
def test_render_ownership_finalizer_failure_does_not_transfer(render_owner, monkeypatch, failure_kind):
    controller, scope, image, returned, active = render_owner
    primary = _ownership_failure(failure_kind)
    image.close_failure = KeyboardInterrupt("secondary close failure")
    original = host._Active

    def make_active():
        value = original()

        def fail():
            raise primary

        monkeypatch.setattr(value.done, "set", fail)
        return value

    monkeypatch.setattr(host, "_Active", make_active)
    with pytest.raises(type(primary)) as caught:
        controller.render(scope)
    assert caught.value is primary and image.closes == 1 and returned == [image]
    assert controller._active is None and len(active) == 1


def test_render_ownership_success_transfers_after_finalizer(render_owner):
    controller, scope, image, returned, active = render_owner
    image.close_failure = AssertionError("successful return closed image")
    assert controller.render(scope) is image
    assert returned == [image] and image.closes == 0
    assert controller._active is None and active[0].done.is_set()


@pytest.mark.parametrize("failure_kind", ["refusal", "keyboard", "exit"])
def test_render_ownership_inner_failed_transfer_is_not_closed_twice(render_owner, monkeypatch, failure_kind):
    controller, scope, image, returned, active = render_owner
    primary = _ownership_failure(failure_kind)
    calls = []

    def failed_inner(*args):
        calls.append(args)
        image.close()  # An upstream owner disposes before raising, not returning.
        raise primary

    monkeypatch.setattr(runtime, "_render_crop_scope", failed_inner)
    with pytest.raises(type(primary)) as caught:
        controller.render(scope)
    assert caught.value is primary and image.closes == 1 and len(calls) == 1 and not returned
    assert controller._active is None and active[0].done.is_set()
