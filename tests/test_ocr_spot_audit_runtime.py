"""Private audit restart/export and real generated original-page previews."""

import hashlib
import json
from types import SimpleNamespace

import pytest

import ocr_review_runtime as runtime
from test_ocr_review import report


@pytest.fixture
def workspace(tmp_path):
    pdf = tmp_path / "source.pdf"
    pdf.write_bytes(b"synthetic source; I/O tests do not render")
    value = report()
    value["source_sha256"] = hashlib.sha256(pdf.read_bytes()).hexdigest()
    recovery = tmp_path / "recovery.json"
    recovery.write_text(json.dumps(value), encoding="utf-8")
    return runtime.ReviewWorkspace(pdf, recovery, tmp_path)


def audit(workspace):
    from ocr_spot_audit import create_audit

    return create_audit(workspace.document, threshold=.8, sample_size=10, seed="2" * 64)


def test_save_restart_child_does_not_mutate_parent_or_promote_draft(workspace):
    from ocr_spot_audit import remember_draft

    value = remember_draft(audit(workspace), workspace.document, page_number=1, text="unfinished")
    first = workspace.save("audit", value)
    parent_bytes = first.read_bytes()
    restored = runtime.ReviewWorkspace(workspace.pdf_path, workspace.recovery_path, workspace.output_dir,
                                       audit_path=first)
    assert restored.initial_audit["records"][0]["outcome"] == "pending"
    assert restored.initial_audit["drafts"][0]["text"] == "unfinished"
    assert restored.initial_audit["parent_audit_sha256"] == hashlib.sha256(parent_bytes).hexdigest()
    child = restored.save("audit", restored.initial_audit)
    assert child != first and first.read_bytes() == parent_bytes
    assert json.loads(child.read_text(encoding="utf-8")) == restored.initial_audit
    with pytest.raises(ValueError):
        restored.save("audit", value)


@pytest.mark.parametrize("field", ["pdf_path", "recovery_path", "audit_path"])
def test_changed_bound_input_refuses_any_export(workspace, field):
    path = workspace.save("audit", audit(workspace))
    restored = runtime.ReviewWorkspace(workspace.pdf_path, workspace.recovery_path, workspace.output_dir,
                                       audit_path=path)
    getattr(restored, field).write_bytes(b"changed")
    before = set(workspace.output_dir.glob("ocr-*.json"))
    with pytest.raises(RuntimeError, match="changed"):
        restored.save("audit", restored.initial_audit)
    with pytest.raises(RuntimeError, match="changed"):
        restored.save("references", restored.document.references(
            [{"page_number": 1, "reference": "checked"}], confirmed=True))
    assert set(workspace.output_dir.glob("ocr-*.json")) == before


@pytest.mark.parametrize("mutate", ["source", "loaded", "directory"])
def test_final_commit_rechecks_inputs_after_staging(workspace, monkeypatch, mutate):
    path = workspace.save("audit", audit(workspace))
    restored = runtime.ReviewWorkspace(workspace.pdf_path, workspace.recovery_path, workspace.output_dir,
                                       audit_path=path)
    write = runtime.storage_policy.atomic_write_private_json

    def change(path, value, **kwargs):
        if mutate == "source":
            restored.pdf_path.write_bytes(b"late source")
        elif mutate == "loaded":
            restored.audit_path.write_bytes(b"late parent")
        else:
            monkeypatch.setattr(restored, "_directory_generation", lambda: (-1, -1))
        return write(path, value, **kwargs)

    monkeypatch.setattr(runtime.storage_policy, "atomic_write_private_json", change)
    with pytest.raises(RuntimeError):
        restored.save("audit", restored.initial_audit)
    assert list(workspace.output_dir.glob("ocr-audit-*.json")) == [path]


def test_collision_never_overwrites_existing_audit(workspace, monkeypatch):
    monkeypatch.setattr(runtime, "uuid4", lambda: SimpleNamespace(hex="fixed"))
    path = workspace.output_dir / "ocr-audit-fixed.json"
    path.write_bytes(b"existing")
    with pytest.raises(FileExistsError):
        workspace.save("audit", audit(workspace))
    assert path.read_bytes() == b"existing"


@pytest.mark.parametrize("bad", [[], {"schema_version": 1}, None])
def test_restore_rejects_bad_closed_artifacts(workspace, bad):
    path = workspace.output_dir / "bad-audit.json"
    path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError):
        runtime.ReviewWorkspace(workspace.pdf_path, workspace.recovery_path, workspace.output_dir, audit_path=path)


def test_loaded_audit_byte_limit_applies_before_parse(workspace):
    path = workspace.output_dir / "large-audit.json"
    with path.open("wb") as stream:
        stream.truncate(runtime.MAX_AUDIT_BYTES + 1)
    with pytest.raises(ValueError):
        runtime.ReviewWorkspace(workspace.pdf_path, workspace.recovery_path, workspace.output_dir, audit_path=path)


def test_linked_audit_path_refused(workspace, monkeypatch):
    path = workspace.save("audit", audit(workspace))
    check = runtime.storage_policy.assert_no_link_components

    def refuse(candidate):
        if candidate == path:
            raise ValueError("synthetic link")
        return check(candidate)

    monkeypatch.setattr(runtime.storage_policy, "assert_no_link_components", refuse)
    with pytest.raises(ValueError, match="link"):
        runtime.ReviewWorkspace(workspace.pdf_path, workspace.recovery_path, workspace.output_dir, audit_path=path)


def rendered_workspace(tmp_path, *, rotation=0, width=100, height=60, crop=False):
    pymupdf = pytest.importorskip("pymupdf")
    pytest.importorskip("PIL")
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=width, height=height)
        page.draw_rect(pymupdf.Rect(0, 0, width / 2, height / 2), color=(1, 0, 0), fill=(1, 0, 0))
        if crop:
            page.set_cropbox(pymupdf.Rect(5, 5, width - 5, height - 5))
        page.set_rotation(rotation)
        raw = pdf.tobytes()
    source = tmp_path / "source.pdf"
    source.write_bytes(raw)
    value = report()
    value["source_sha256"] = hashlib.sha256(raw).hexdigest()
    recovery = tmp_path / "recovery.json"
    recovery.write_text(json.dumps(value), encoding="utf-8")
    return runtime.ReviewWorkspace(source, recovery, tmp_path)


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("crop", [False, True])
def test_original_preview_respects_display_geometry(tmp_path, rotation, crop):
    workspace = rendered_workspace(tmp_path, rotation=rotation, crop=crop)
    image = workspace.render_original_page(1)
    try:
        assert image.mode == "RGB" and max(image.size) <= runtime.DISPLAY_SIDE
        corner = {0: (.25, .25), 90: (.75, .25), 180: (.75, .75), 270: (.25, .75)}[rotation]
        assert image.getpixel((int(corner[0] * image.width), int(corner[1] * image.height))) == (255, 0, 0)
    finally:
        image.close()


def test_original_preview_bounded_before_allocation_and_ignores_candidate_raster(tmp_path, monkeypatch):
    import pymupdf

    workspace = rendered_workspace(tmp_path, width=10_000, height=8_000)
    native = pymupdf.Page.get_pixmap
    observations = []

    def bounded(page, **kwargs):
        bounds = (page.rect * kwargs["matrix"]).irect
        assert max(bounds.width, bounds.height) <= runtime.DISPLAY_SIDE
        observations.append((bounds.width, bounds.height))
        return native(page, **kwargs)

    monkeypatch.setattr(pymupdf.Page, "get_pixmap", bounded)
    image = workspace.render_original_page(1)
    try:
        assert observations == [image.size]
        assert max(image.size) <= runtime.DISPLAY_SIDE
        # This geometry intentionally differs from the historic OCR canvas.
        assert image.size != (100, 100)
    finally:
        image.close()


def test_original_preview_pixels_independent_of_candidate_data(tmp_path):
    workspace = rendered_workspace(tmp_path)
    first = workspace.render_original_page(1)
    try:
        expected = first.tobytes(), first.size
    finally:
        first.close()
    value = workspace.document.recovery_snapshot()
    candidate = value["pages"][0]["candidate"]
    for line in candidate["lines"]:
        line["score"] = .99
    candidate["mean_confidence"] = .99
    candidate["raster"]["width"] = 101
    workspace.recovery_path.write_text(json.dumps(value), encoding="utf-8")
    second_workspace = runtime.ReviewWorkspace(workspace.pdf_path, workspace.recovery_path, workspace.output_dir)
    second = second_workspace.render_original_page(1)
    try:
        assert (second.tobytes(), second.size) == expected
    finally:
        second.close()


def test_late_source_change_closes_untransferred_image(tmp_path, monkeypatch):
    pytest.importorskip("PIL")
    from PIL import Image

    workspace = rendered_workspace(tmp_path)
    check = workspace.verify_inputs
    closed = []
    original_close = Image.Image.close
    calls = 0

    def verify():
        nonlocal calls
        calls += 1
        if calls == 2:
            workspace.pdf_path.write_bytes(b"late mutation")
        check()

    def close(image):
        closed.append(image)
        original_close(image)

    monkeypatch.setattr(workspace, "verify_inputs", verify)
    monkeypatch.setattr(Image.Image, "close", close)
    with pytest.raises(RuntimeError, match="changed"):
        workspace.render_original_page(1)
    assert len(closed) == 1


def test_late_valid_workspace_swap_cannot_relabel_old_pixels(tmp_path, monkeypatch):
    pytest.importorskip("PIL")
    from PIL import Image

    workspace = rendered_workspace(tmp_path)
    other_path = tmp_path / "other"
    other_path.mkdir()
    other = rendered_workspace(other_path, rotation=90)
    native_check = workspace.verify_inputs
    calls, closed = [], []
    original_close = Image.Image.close

    def check():
        calls.append(True)
        if len(calls) == 2:
            workspace.document = other.document
            workspace.pdf_path = other.pdf_path
            workspace.recovery_path = other.recovery_path
        native_check()

    def close(image):
        closed.append(image)
        original_close(image)

    monkeypatch.setattr(workspace, "verify_inputs", check)
    monkeypatch.setattr(Image.Image, "close", close)
    with pytest.raises(RuntimeError, match="binding changed"):
        workspace.render_original_page(1)
    assert len(closed) == 1


@pytest.mark.parametrize("secondary", [RuntimeError("close"), KeyboardInterrupt(), SystemExit()])
def test_secondary_close_failure_preserves_primary_identity(tmp_path, monkeypatch, secondary):
    pytest.importorskip("PIL")
    from PIL import Image

    workspace = rendered_workspace(tmp_path)
    native_check = workspace.verify_inputs
    calls, closes = [], []
    primary = RuntimeError("primary")

    def check():
        calls.append(True)
        if len(calls) == 2:
            raise primary
        native_check()

    def close(image):
        closes.append(image)
        raise secondary

    monkeypatch.setattr(workspace, "verify_inputs", check)
    monkeypatch.setattr(Image.Image, "close", close)
    with pytest.raises(RuntimeError) as captured:
        workspace.render_original_page(1)
    assert captured.value is primary
    assert len(closes) == 1


@pytest.mark.parametrize("bad", [0, 2, True, 1., "1", None])
def test_original_preview_rejects_page_before_optional_import(workspace, bad):
    with pytest.raises(ValueError):
        workspace.render_original_page(bad)


def test_opt_in_panel_preserves_default_ui_dependency_boundary(monkeypatch):
    import sys

    pytest.importorskip("gradio")
    from PIL import Image
    from ocr_review import ReviewDocument
    from ocr_review_ui import build_app

    workspace = SimpleNamespace(document=ReviewDocument(report(), recovery_sha256="b" * 64),
                                render=lambda _: Image.new("RGB", (100, 100), "white"))
    calls = []
    monkeypatch.setitem(sys.modules, "ocr_spot_audit_ui",
                        SimpleNamespace(build_spot_audit_panel=lambda value: calls.append(value)))
    app = build_app(workspace)
    try:
        assert calls == []
    finally:
        app.close()
    app = build_app(workspace, spot_audit=True)
    try:
        assert calls == [workspace]
    finally:
        app.close()


@pytest.mark.parametrize("bad", [None, 0, 1, "true", []])
def test_ui_opt_in_strict_bool(bad):
    from ocr_review_ui import build_app

    with pytest.raises(ValueError, match="spot audit"):
        build_app(None, spot_audit=bad)


@pytest.mark.parametrize("mode", ["default", "enable", "restore"])
def test_cli_fixed_audit_opt_in_keeps_loopback_and_blocks_input(tmp_path, monkeypatch, mode):
    import sys
    from tools import review_ocr

    path = tmp_path / "private-audit.json"
    workspaces, launches, builds = [], [], []

    def make_workspace(*args, **kwargs):
        workspaces.append(kwargs)
        return SimpleNamespace(output_dir=tmp_path, pdf_path=tmp_path / "synthetic.pdf",
            recovery_path=tmp_path / "recovery.json", draft_path=None, proposals_path=None,
            audit_path=kwargs.get("audit_path"))

    app = SimpleNamespace(launch=lambda **kwargs: launches.append(kwargs), close=lambda: None)

    def build(workspace, **kwargs):
        builds.append(kwargs)
        return app

    monkeypatch.setattr(runtime, "ReviewWorkspace", make_workspace)
    monkeypatch.setitem(sys.modules, "ocr_review_ui", SimpleNamespace(build_app=build))
    monkeypatch.setenv("RAG_OCR_REVIEW_TOKEN", "r_" + "x" * 32)
    # main refuses both; a developer shell must not fail this launch.
    monkeypatch.delenv("GRADIO_ALLOWED_PATHS", raising=False)
    monkeypatch.delenv("GRADIO_LOCAL_DEV_MODE", raising=False)
    args = ["--pdf", "synthetic.pdf", "--recovery", "recovery.json", "--output-dir", str(tmp_path),
            "--trusted-local-session"]
    if mode == "enable":
        args += ["--enable-spot-audit"]
    elif mode == "restore":
        args += ["--audit", str(path)]
    assert review_ocr.main(args) == 0
    assert builds == ([{}] if mode == "default" else [{"spot_audit": True}])
    assert ("audit_path" in workspaces[0]) is (mode == "restore")
    assert (str(path) in launches[0]["blocked_paths"]) is (mode == "restore")
    assert launches[0]["server_name"] == "127.0.0.1" and launches[0]["share"] is False
    assert launches[0]["allowed_paths"] == [] and launches[0]["mcp_server"] is False
