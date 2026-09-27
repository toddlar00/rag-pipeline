"""Create-only review exports and bounded, source-bound synthetic raster checks."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import ocr_review_runtime as runtime
from test_ocr_review import missing_report, report


@pytest.fixture
def workspace(tmp_path):
    pdf = tmp_path / "source.pdf"
    pdf.write_bytes(b"synthetic source, not rendered in I/O tests")
    value = report()
    value["source_sha256"] = hashlib.sha256(pdf.read_bytes()).hexdigest()
    recovery = tmp_path / "recovery.json"
    recovery.write_text(json.dumps(value), encoding="utf-8")
    return runtime.ReviewWorkspace(pdf, recovery, tmp_path)


def references(workspace):
    return workspace.document.references([{"page_number": 1, "reference": "SYNTHETIC REVIEW"}], confirmed=True)


def test_exports_are_create_only_distinct_and_bound(workspace):
    before = workspace.pdf_path.read_bytes(), workspace.recovery_path.read_bytes()
    payload = references(workspace)
    a = workspace.save("references", payload)
    b = workspace.save("references", payload)
    assert a != b
    assert json.loads(a.read_text(encoding="utf-8")) == payload
    assert before == (workspace.pdf_path.read_bytes(), workspace.recovery_path.read_bytes())


@pytest.mark.parametrize("field", ["pdf_path", "recovery_path"])
def test_changed_input_prevents_export(workspace, field):
    getattr(workspace, field).write_bytes(b"replaced")
    with pytest.raises(RuntimeError, match="changed"):
        workspace.save("references", references(workspace))
    assert not list(workspace.output_dir.glob("ocr-*.json"))


def test_changed_input_during_commit_recheck(workspace, monkeypatch):
    original = workspace.verify_inputs
    calls = 0

    def mutate_after_first_check():
        nonlocal calls
        original()
        calls += 1
        if calls == 1:
            workspace.pdf_path.write_bytes(b"changed between checks")

    monkeypatch.setattr(workspace, "verify_inputs", mutate_after_first_check)
    with pytest.raises(RuntimeError):
        workspace.save("references", references(workspace))
    assert not list(workspace.output_dir.glob("ocr-*.json"))


def test_output_directory_generation_must_match(workspace, monkeypatch):
    monkeypatch.setattr(workspace, "_directory_generation", lambda: (-1, -1))
    with pytest.raises(RuntimeError, match="directory changed"):
        workspace.save("references", references(workspace))


@pytest.mark.parametrize("target", ["source", "directory"])
def test_final_publication_rechecks_after_staging(workspace, monkeypatch, target):
    write = runtime.storage_policy.atomic_write_private_json

    def mutate_during_staging(path, value, **kwargs):
        if target == "source":
            workspace.pdf_path.write_bytes(b"changed during staging")
        else:
            monkeypatch.setattr(workspace, "_directory_generation", lambda: (-1, -1))
        return write(path, value, **kwargs)

    monkeypatch.setattr(runtime.storage_policy, "atomic_write_private_json", mutate_during_staging)
    with pytest.raises(RuntimeError):
        workspace.save("references", references(workspace))
    assert not list(workspace.output_dir.glob("ocr-*.json"))


def test_competing_writer_not_overwritten(workspace, monkeypatch):
    class Id:
        hex = "fixed"

    monkeypatch.setattr(runtime, "uuid4", lambda: Id())
    target = workspace.output_dir / "ocr-references-fixed.json"
    target.write_bytes(b"existing generation")
    with pytest.raises(FileExistsError):
        workspace.save("references", references(workspace))
    assert target.read_bytes() == b"existing generation"


def test_wrong_reference_binding_rejected(workspace):
    payload = references(workspace)
    payload["source_sha256"] = "f" * 64
    with pytest.raises(ValueError, match="source"):
        workspace.save("references", payload)


def test_draft_export_restart_and_parent_binding(workspace):
    from ocr_review_drafts import build_draft, initial_state, remember_reference

    state = initial_state(workspace.document)
    remember_reference(state, "UNCONFIRMED reference", workspace.document)
    first = workspace.save("draft", build_draft(state, workspace.document))
    restored = runtime.ReviewWorkspace(workspace.pdf_path, workspace.recovery_path, workspace.output_dir,
                                       draft_path=first)
    assert restored.initial_draft["references"] == []
    assert restored.initial_draft["reference_drafts"][0]["text"] == "UNCONFIRMED reference"
    assert restored.draft_sha256 == hashlib.sha256(first.read_bytes()).hexdigest()
    second = restored.save("draft", build_draft(restored.initial_draft, restored.document,
                                               parent_draft_sha256=restored.draft_sha256))
    assert json.loads(second.read_text(encoding="utf-8"))["parent_draft_sha256"] == restored.draft_sha256
    with pytest.raises(ValueError, match="parent"):
        restored.save("draft", build_draft(restored.initial_draft, restored.document))


def test_changed_loaded_draft_prevents_any_export(workspace):
    from ocr_review_drafts import build_draft, initial_state

    first = workspace.save("draft", build_draft(initial_state(workspace.document), workspace.document))
    restored = runtime.ReviewWorkspace(workspace.pdf_path, workspace.recovery_path, workspace.output_dir,
                                       draft_path=first)
    first.write_bytes(b"changed snapshot")
    with pytest.raises(RuntimeError, match="changed"):
        restored.save("references", references(restored))


def test_draft_bounds_and_input_binding_checked_at_startup(workspace):
    from ocr_review_drafts import build_draft, initial_state

    path = workspace.output_dir / "bad-draft.json"
    payload = build_draft(initial_state(workspace.document), workspace.document)
    payload["source_sha256"] = "f" * 64
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="binding"):
        runtime.ReviewWorkspace(workspace.pdf_path, workspace.recovery_path, workspace.output_dir, draft_path=path)
    with path.open("wb") as stream:
        stream.truncate(runtime.MAX_DRAFT_BYTES + 1)
    with pytest.raises(ValueError):
        runtime.ReviewWorkspace(workspace.pdf_path, workspace.recovery_path, workspace.output_dir, draft_path=path)


@pytest.mark.parametrize("kind", ["../references", "canonical", "recovery", "", None])
def test_export_kind_not_an_arbitrary_path(workspace, kind):
    with pytest.raises(ValueError):
        workspace.save(kind, references(workspace))


def test_link_components_rejected(workspace, monkeypatch):
    def refuse(path):
        raise ValueError("synthetic link")

    monkeypatch.setattr(runtime.storage_policy, "assert_no_link_components", refuse)
    with pytest.raises(ValueError, match="link"):
        workspace.save("references", references(workspace))


def test_real_synthetic_pdf_render_and_overlay(tmp_path):
    pymupdf = pytest.importorskip("pymupdf")
    pytest.importorskip("numpy")
    pytest.importorskip("PIL")
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=24, height=24)
        page.insert_text((2, 8), "AB", fontsize=5)
        raw = pdf.tobytes()
    source = tmp_path / "source.pdf"
    source.write_bytes(raw)
    value = report()
    value["source_sha256"] = hashlib.sha256(raw).hexdigest()
    recovery = tmp_path / "recovery.json"
    recovery.write_text(json.dumps(value), encoding="utf-8")
    workspace = runtime.ReviewWorkspace(source, recovery, tmp_path)
    image = workspace.render(1)
    assert image.size == (100, 100)
    before = image.tobytes()
    overlay = runtime.draw_overlay(image, workspace.document.page(1)["candidate"],
                                   selections={"crop": [.1, .1, .5, .5]})
    assert overlay.tobytes() != before
    assert image.tobytes() == before


def test_wrong_render_geometry_fails_before_allocation(tmp_path):
    pymupdf = pytest.importorskip("pymupdf")
    pytest.importorskip("numpy")
    pytest.importorskip("PIL")
    with pymupdf.open() as pdf:
        pdf.new_page(width=1000, height=1000)
        raw = pdf.tobytes()
    source = tmp_path / "source.pdf"
    source.write_bytes(raw)
    value = report()
    value["source_sha256"] = hashlib.sha256(raw).hexdigest()
    recovery = tmp_path / "recovery.json"
    recovery.write_text(json.dumps(value), encoding="utf-8")
    workspace = runtime.ReviewWorkspace(source, recovery, tmp_path)
    with pytest.raises(ValueError, match="geometry"):
        workspace.render(1)


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_failed_deferred_unselected_scans_render_original_content(tmp_path, rotation):
    pymupdf = pytest.importorskip("pymupdf")
    np = pytest.importorskip("numpy")
    pytest.importorskip("PIL")
    with pymupdf.open() as pdf:
        for _ in range(3):
            page = pdf.new_page(width=100, height=60)
            page.draw_rect(pymupdf.Rect(0, 0, 50, 30), color=(1, 0, 0), fill=(1, 0, 0))
            page.set_rotation(rotation)
        raw = pdf.tobytes()
    source = tmp_path / "source.pdf"
    source.write_bytes(raw)
    value = missing_report()
    value["source_sha256"] = hashlib.sha256(raw).hexdigest()
    recovery = tmp_path / "recovery.json"
    recovery.write_text(json.dumps(value), encoding="utf-8")
    workspace = runtime.ReviewWorkspace(source, recovery, tmp_path)
    # Physical colored content must follow the PDF's display rotation.
    corner = {0: (.25, .25), 90: (.75, .25), 180: (.75, .75), 270: (.25, .75)}[rotation]
    for number in (1, 2, 3):
        image = workspace.render(number)
        assert max(image.size) <= runtime.DISPLAY_SIDE
        pixels = np.asarray(image)
        assert tuple(pixels[int(corner[1] * image.height), int(corner[0] * image.width)]) == (255, 0, 0)
        assert workspace.document.page(number)["candidate"] is None
        overlay = runtime.draw_overlay(image, None, selections={"crop": [.1, .1, .9, .9]})
        assert overlay.tobytes() != image.tobytes()
    plan = workspace.region_plan([{"region_id": "deferred", "page_number": 2, "bbox": [.1, .2, .8, .9]}])
    assert workspace.save("regions", plan).exists()


def test_no_candidate_overlay_rejects_fabricated_line_order():
    Image = pytest.importorskip("PIL.Image")
    with pytest.raises(ValueError, match="every line"):
        runtime.draw_overlay(Image.new("RGB", (100, 100)), None, line_order=[0])


@pytest.mark.parametrize("order", [[0], [False, 1, 2, 3, 4, 5], [0, 1, 2, 3, 4, 4], "012345"])
def test_overlay_order_is_a_complete_permutation(order):
    Image = pytest.importorskip("PIL.Image")
    with pytest.raises(ValueError):
        runtime.draw_overlay(Image.new("RGB", (100, 100)), report()["pages"][0]["candidate"], line_order=order)


@pytest.mark.parametrize("arguments", [[], ["--unknown=SYNTHETIC_SECRET"],
                                      ["--pdf", "SYNTHETIC_SECRET", "--recovery", "missing", "--output-dir", ".",
                                       "--trusted-local-session", "--port", "bad"]])
def test_cli_errors_are_static_and_help_is_light(arguments):
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, str(root / "tools" / "review_ocr.py"), *arguments],
                            cwd=root, capture_output=True, text=True, timeout=10)
    assert result.returncode == 2
    assert "SYNTHETIC_SECRET" not in result.stdout + result.stderr
    assert "Traceback" not in result.stderr


def test_cli_requires_local_ack_and_token(tmp_path):
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env.pop("RAG_OCR_REVIEW_TOKEN", None)
    result = subprocess.run([sys.executable, str(root / "tools" / "review_ocr.py"), "--pdf", "missing",
                             "--recovery", "missing", "--output-dir", str(tmp_path), "--trusted-local-session"],
                            cwd=root, env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 2
    assert "RAG_OCR_REVIEW_TOKEN" in result.stderr


def test_cli_help_does_not_import_optional_dependencies():
    root = Path(__file__).resolve().parents[1]
    code = ("import runpy,sys; sys.argv=['review_ocr.py','--help']; "
            "sys.modules.update({k:None for k in ('pymupdf','numpy','gradio','rapidocr')}); "
            f"runpy.run_path({str(root / 'tools' / 'review_ocr.py')!r},run_name='__main__')")
    result = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0
    assert "--trusted-local-session" in result.stdout


@pytest.fixture
def download_cache(workspace):
    cache = workspace.output_dir / "ocr-review-cache-generated"
    cache.mkdir()
    return cache


def test_download_copies_exact_saved_bytes_without_reusing_a_cache_leaf(workspace, download_cache):
    inputs = workspace.pdf_path.read_bytes(), workspace.recovery_path.read_bytes()
    originals = [workspace.save("references", references(workspace)) for _ in range(2)]
    retained = [path.read_bytes() for path in originals]
    copies = [workspace.prepare_download(path, download_cache) for path in originals]
    assert copies[0] != copies[1]
    for original, cached, raw in zip(originals, copies, retained):
        assert original.parent == workspace.output_dir and cached.parent == download_cache
        assert cached.name == original.name and cached != original
        assert cached.read_bytes() == original.read_bytes() == raw
    # An existing leaf is never silently reused, even when its bytes are identical.
    existing = copies[0].stat()
    with pytest.raises(runtime.ReviewDownloadError, match="artifact was saved"):
        workspace.prepare_download(originals[0], download_cache)
    assert [path.read_bytes() for path in copies] == retained
    current = copies[0].stat()
    assert (current.st_dev, current.st_ino, current.st_mtime_ns) == (existing.st_dev, existing.st_ino, existing.st_mtime_ns)
    assert inputs == (workspace.pdf_path.read_bytes(), workspace.recovery_path.read_bytes())


@pytest.mark.parametrize("invalid", ["string", "browser_dict", "outside", "fixed_input", "wrong_kind", "owner"])
def test_download_rejects_unowned_paths_before_copy(workspace, download_cache, invalid):
    original = workspace.save("references", references(workspace))
    raw = original.read_bytes()
    owner = None
    if invalid == "string":
        supplied = str(original)
    elif invalid == "browser_dict":
        supplied = {"path": str(original)}
    elif invalid == "outside":
        folder = workspace.output_dir / "elsewhere"
        folder.mkdir()
        supplied = folder / original.name
        supplied.write_bytes(raw)
    elif invalid == "fixed_input":
        supplied = workspace.pdf_path
    elif invalid == "wrong_kind":
        supplied = workspace.output_dir / ("ocr-audit-" + "a" * 32 + ".json")
        supplied.write_bytes(raw)
    else:
        supplied, owner = original, "browser-supplied-owner"
    with pytest.raises(runtime.ReviewDownloadError) as failure:
        workspace.prepare_download(supplied, download_cache, owner=owner)
    assert str(original) not in str(failure.value)
    assert "Check the private output folder" in str(failure.value)
    assert not list(download_cache.iterdir()) and original.read_bytes() == raw


@pytest.mark.parametrize("invalid", ["wrong_parent", "wrong_name", "file"])
def test_download_rejects_non_session_cache(workspace, download_cache, invalid):
    original = workspace.save("references", references(workspace))
    if invalid == "wrong_parent":
        cache = download_cache / "ocr-review-cache-nested"
        cache.mkdir()
    elif invalid == "wrong_name":
        cache = workspace.output_dir / "ordinary-output"
        cache.mkdir()
    else:
        cache = workspace.output_dir / "ocr-review-cache-file"
        cache.write_bytes(b"not a directory")
    with pytest.raises(runtime.ReviewDownloadError):
        workspace.prepare_download(original, cache)
    assert original.exists() and not (cache / original.name).exists()


def test_download_rejects_hardlinked_export(workspace, download_cache):
    original = workspace.save("references", references(workspace))
    alias = workspace.output_dir / "retained-alias.json"
    try:
        os.link(original, alias)
    except OSError as exc:
        pytest.skip(f"hard links unavailable: {exc}")
    with pytest.raises(runtime.ReviewDownloadError):
        workspace.prepare_download(original, download_cache)
    assert original.read_bytes() == alias.read_bytes() and not list(download_cache.iterdir())


@pytest.mark.parametrize("kind", ["empty", "directory", "oversized"])
def test_download_rejects_invalid_source_without_reading_payload(workspace, download_cache, monkeypatch, kind):
    original = workspace.output_dir / ("ocr-references-" + "a" * 32 + ".json")
    if kind == "directory":
        original.mkdir()
    else:
        with original.open("wb") as handle:
            if kind == "oversized":
                handle.seek(runtime.MAX_REPORT_BYTES)
                handle.write(b"x")
    read = runtime._read_snapshot

    def reject_payload_read(path, **kwargs):
        if path == original:
            pytest.fail("invalid download payload was read")
        return read(path, **kwargs)

    monkeypatch.setattr(runtime, "_read_snapshot", reject_payload_read)
    with pytest.raises(runtime.ReviewDownloadError):
        workspace.prepare_download(original, download_cache)
    assert not list(download_cache.iterdir())


@pytest.mark.parametrize("changed", ["export_bytes", "cache_directory", "copied_bytes"])
def test_download_rechecks_source_cache_and_published_bytes(workspace, download_cache, monkeypatch, changed):
    original = workspace.save("references", references(workspace))
    retained = original.read_bytes()
    read, write = runtime._read_snapshot, runtime.storage_policy.atomic_write_private
    writes = []
    changed_once = False

    def corrupt_same_stat(path):
        before = path.stat()
        data = path.read_bytes()
        path.write_bytes(bytes([data[0] ^ 1]) + data[1:])
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        after = path.stat()
        assert (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_nlink) == (
            before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_nlink)

    def read_then_change(path, **kwargs):
        nonlocal changed_once
        result = read(path, **kwargs)
        if path == original and not changed_once:
            changed_once = True
            if changed == "export_bytes":
                corrupt_same_stat(original)
            elif changed == "cache_directory":
                download_cache.rename(workspace.output_dir / "retained-original-cache")
                download_cache.mkdir()
        return result

    def write_then_change(path, writer, **kwargs):
        writes.append(path)
        write(path, writer, **kwargs)
        if changed == "copied_bytes":
            corrupt_same_stat(path)

    monkeypatch.setattr(runtime, "_read_snapshot", read_then_change)
    monkeypatch.setattr(runtime.storage_policy, "atomic_write_private", write_then_change)
    with pytest.raises(runtime.ReviewDownloadError, match="artifact was saved"):
        workspace.prepare_download(original, download_cache)
    assert changed_once and len(writes) == 1
    assert list(workspace.output_dir.glob("ocr-references-*.json")) == [original]
    if changed != "export_bytes":
        assert original.read_bytes() == retained
    if changed != "copied_bytes":
        assert not (download_cache / original.name).exists()
