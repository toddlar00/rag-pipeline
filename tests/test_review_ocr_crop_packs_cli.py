"""Opt-in archive launcher/UI composition, with inert host and render doubles."""

import sys
from types import SimpleNamespace as NS

import pytest

from tools import review_ocr
from test_review_ocr_execution_cli import launch_case  # noqa: F401


@pytest.fixture
def archive_launch(launch_case, monkeypatch):  # noqa: F811
    c = launch_case
    c.packs = []
    c.pack_timeouts = []
    c.pack_root = c.workspace.output_dir / "private-packs"

    class Packs:
        def __init__(self, workspace, root, **options):
            c.events.append("packs")
            assert workspace is c.workspace
            if "pack-constructor" in c.failures:
                raise c.failures["pack-constructor"]
            self.private_root = root.absolute()
            self.preview_private_root = options.get("preview_private_root", c.workspace.output_dir.parent / "pack-preview")
            c.packs.append((self, root, options))

        def request_close(self):
            c.events.append("pack-revoke")
            if "pack-revoke" in c.failures:
                raise c.failures["pack-revoke"]

        def close(self, *, timeout_seconds):
            c.events.append("pack-close")
            c.pack_timeouts.append(timeout_seconds)
            assert 0 <= timeout_seconds <= 40
            if "pack-close" in c.failures:
                raise c.failures["pack-close"]
            return c.failures.get("pack-shutdown", True)

    monkeypatch.setitem(sys.modules, "ocr_review_crop_packs", NS(CropReviewPackService=Packs))
    cls = sys.modules["ocr_review_execution"].ReviewRunCoordinator
    monkeypatch.setattr(cls, "render_crop_preview", lambda *_a, **_kw: None, raising=False)
    monkeypatch.setattr(cls, "render_crop_preview_with_view", lambda *_a, **_kw: None, raising=False)
    return c


def test_default_launcher_imports_neither_pack_service_nor_execution(archive_launch, monkeypatch):
    c = archive_launch
    monkeypatch.setitem(sys.modules, "ocr_review_crop_packs", None)
    monkeypatch.setitem(sys.modules, "ocr_review_execution", None)
    assert review_ocr.main(c.args) == 0
    assert c.builds == [{}] and not c.packs and not c.constructed


@pytest.mark.parametrize("execute", [False, True])
def test_private_archive_option_does_not_enable_ocr_and_shares_preview_only_when_enabled(archive_launch, execute):
    c = archive_launch
    args = c.args + ["--crop-review-pack-dir", str(c.pack_root)]
    if execute:
        args.append("--enable-ocr-execution")
    assert review_ocr.main(args) == 0
    service, root, options = c.packs[0]
    assert root == c.pack_root
    assert c.builds[0]["pack_service"] is service
    assert c.builds[0]["uncertainty_review"] is True
    assert bool(c.constructed) is execute
    if execute:
        coordinator = c.constructed[0][0]
        assert options == {"render_preview": coordinator.render_crop_preview,
                           "render_preview_with_view": coordinator.render_crop_preview_with_view,
                           "preview_private_root": coordinator.preview_private_root}
        assert c.builds[0]["execution_coordinator"] is coordinator
        assert c.events[-4:] == ["pack-revoke", "coordinator-close", "pack-close", "app-close"]
    else:
        assert options == {} and set(c.builds[0]) == {"pack_service", "uncertainty_review"}
        assert c.events[-3:] == ["pack-revoke", "pack-close", "app-close"]
    launch = c.launches[0]
    assert str(service.private_root) in launch["blocked_paths"]
    assert str(service.preview_private_root) in launch["blocked_paths"]
    assert launch["allowed_paths"] == [] and launch["share"] is launch["mcp_server"] is False
    assert launch["server_name"] == "127.0.0.1" and launch["auth"][0] == "review"


@pytest.mark.parametrize("where", ["build", "launch", "pack-revoke", "pack-close", "coordinator-close", "app-close"])
def test_archive_cleanup_attempts_all_remaining_owners_and_redacts_failures(archive_launch, capsys, where):
    c = archive_launch
    c.failures[where] = RuntimeError("SYNTHETIC_PRIVATE_DETAIL")
    assert review_ocr.main(c.args + ["--crop-review-pack-dir", str(c.pack_root), "--enable-ocr-execution"]) == 2
    assert "pack-close" in c.events and "coordinator-close" in c.events
    if where != "build":
        assert c.events[-4:] == ["pack-revoke", "coordinator-close", "pack-close", "app-close"]
    assert "SYNTHETIC_PRIVATE_DETAIL" not in capsys.readouterr().err


@pytest.mark.parametrize("result", [False, None, 1, {}, {"closed": True}])
def test_archive_uncertain_or_malformed_shutdown_is_not_success(archive_launch, result):
    c = archive_launch
    c.failures["pack-shutdown"] = result
    assert review_ocr.main(c.args + ["--crop-review-pack-dir", str(c.pack_root), "--enable-ocr-execution"]) == 2
    assert c.events[-4:] == ["pack-revoke", "coordinator-close", "pack-close", "app-close"]


def test_archive_startup_failure_still_closes_existing_execution_owner(archive_launch):
    c = archive_launch
    c.failures["pack-constructor"] = RuntimeError("private startup detail")
    assert review_ocr.main(c.args + ["--crop-review-pack-dir", str(c.pack_root), "--enable-ocr-execution"]) == 2
    assert c.events[-2:] == ["packs", "coordinator-close"]
    assert not c.builds


def test_cancelled_archive_launch_still_closes_all_resources(archive_launch):
    c = archive_launch
    c.failures["launch"] = KeyboardInterrupt()
    assert review_ocr.main(c.args + ["--crop-review-pack-dir", str(c.pack_root), "--enable-ocr-execution"]) == 130
    assert c.events[-4:] == ["pack-revoke", "coordinator-close", "pack-close", "app-close"]


@pytest.mark.parametrize("execute", [False, True])
@pytest.mark.parametrize(("readings", "expected"), [((1000., 1012.5), 27.5), ((1000., 1050.), 0.)])
def test_archive_close_receives_remaining_shared_shutdown_budget(archive_launch, monkeypatch, execute, readings,
                                                                  expected):
    c = archive_launch
    clock = iter(readings)
    args = c.args + ["--crop-review-pack-dir", str(c.pack_root)]
    if execute:
        args.append("--enable-ocr-execution")
    # Replace the launcher clock capability only; pytest keeps its real clock.
    monkeypatch.setattr(review_ocr, "time", NS(monotonic=lambda: next(clock)))
    assert review_ocr.main(args) == 0
    assert c.pack_timeouts == [expected]


def test_archive_option_does_not_bypass_token_validation(archive_launch, monkeypatch):
    c = archive_launch
    monkeypatch.delenv("RAG_OCR_REVIEW_TOKEN")
    assert review_ocr.main(c.args + ["--crop-review-pack-dir", str(c.pack_root)]) == 2
    assert not c.events


@pytest.mark.parametrize("execute", [False, True])
@pytest.mark.parametrize("uncertainty", [False, True])
def test_real_app_can_compose_independent_archive_tab_without_run_authority(monkeypatch, execute, uncertainty):
    pytest.importorskip("gradio")
    Image = pytest.importorskip("PIL.Image")
    from ocr_review import ReviewDocument
    from ocr_review_ui import build_app
    from test_ocr_review import report

    workspace = NS(document=ReviewDocument(report(), recovery_sha256="b" * 64),
                   render=lambda _number: Image.new("RGB", (50, 50), "white"))
    packs, coordinator = object(), object()
    archives, executions = [], []
    monkeypatch.setitem(sys.modules, "ocr_review_crop_archive_ui",
                        NS(build_crop_archive_panel=lambda *args, **kwargs: archives.append((args, kwargs))))
    monkeypatch.setitem(sys.modules, "ocr_review_execution_ui", None if not execute else NS(
        build_execution_panel=lambda *args, **kwargs: executions.append((args, kwargs))))
    options = {"execution_coordinator": coordinator} if execute else {}
    app = build_app(workspace, pack_service=packs, uncertainty_review=uncertainty, **options)
    try:
        assert archives == [((workspace, packs), {"uncertainty": True} if uncertainty else {})]
        assert bool(executions) is execute
        if execute:
            assert executions[0][0] == (workspace, coordinator)
            assert executions[0][1]["pack_service"] is packs
            assert executions[0][1].get("uncertainty_review", False) is uncertainty
        assert all(dep["api_visibility"] == "private" for dep in app.config["dependencies"])
    finally:
        app.close()
