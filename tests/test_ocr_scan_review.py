"""Generated original-raster replay controls; historical IO has its own suite.

The local fixture stubs only the already validated bundle loader. It exercises
actual native pixel collection/replay and fixed artifact/input rechecks, not
authenticity of a synthetic manifest or representative recognition accuracy.
"""

import copy
import hashlib
import json
from types import SimpleNamespace

import pytest

import ocr_scan_io
import ocr_scan_review as review
from ocr_scan_omission import build_scan_omission_report
from test_ocr_review import missing_report


@pytest.fixture
def make_review(tmp_path, monkeypatch):
    fitz = pytest.importorskip("pymupdf")
    pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    pytest.importorskip("PIL")
    from ocr_scan_runtime import collect_scan_observation

    counter = 0

    def make(*, rotation=0, crop=False, mutate=None, annotations=False, large=False):
        nonlocal counter
        counter += 1
        directory = tmp_path / str(counter)
        directory.mkdir()
        pdf = directory / "source.pdf"
        with fitz.open() as document:
            page = document.new_page(width=600 if large else 240, height=800 if large else 320)
            page.insert_text((40, 70), "SYNTHETIC scan region", fontsize=10)
            if annotations:
                annotation = page.add_rect_annot(fitz.Rect(40, 110, 90, 150))
                annotation.set_colors(stroke=(0, 0, 0), fill=(0, 0, 0))
                annotation.update()
            if crop:
                page.set_cropbox(fitz.Rect(20, 30, 200, 290))
            page.set_rotation(rotation)
            document.new_page(width=240, height=320)
            document.new_page(width=240, height=320)
            raw = document.tobytes()
        pdf.write_bytes(raw)
        scan = collect_scan_observation(raw, requested_pages=[1, 2])
        if mutate is not None:
            mutate(scan)
        diagnostics = build_scan_omission_report(scan)
        recovery = missing_report()
        recovery["source_sha256"] = scan["source_sha256"]
        recovery_file = directory / "recovery.json"
        recovery_file.write_text(json.dumps(recovery), encoding="utf-8")
        recovery_sha = hashlib.sha256(recovery_file.read_bytes()).hexdigest()
        bundle = {"scan": scan, "diagnostics": diagnostics, "manifest": {"test": "stubbed historical loader"},
                  "review_diagnostics": build_scan_omission_report(scan, recovery=recovery, recovery_sha256=recovery_sha)}
        bundle_path = directory / "bundle"
        bundle_path.mkdir()
        for name in ("scan", "diagnostics", "manifest"):
            (bundle_path / (name + ".json")).write_text(json.dumps(bundle[name]), encoding="utf-8")
        monkeypatch.setattr(ocr_scan_io, "read_scan_review_bundle", lambda *a, **kw: copy.deepcopy(bundle))
        arguments = {"source_sha256": scan["source_sha256"], "page_count": scan["page_count"],
                     "recovery": recovery, "recovery_sha256": recovery_sha}
        adapter = review.ScanReview(bundle_path, pdf, **arguments)
        return SimpleNamespace(adapter=adapter, scan=scan, bundle=bundle, arguments=arguments,
                               pdf=pdf, bundle_path=bundle_path, recovery_file=recovery_file, output=directory)

    return make


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("crop", [False, True])
def test_exact_original_pixel_replay_for_rotations_and_cropboxes(make_review, rotation, crop):
    fixture = make_review(rotation=rotation, crop=crop)
    image = fixture.adapter.render(1)
    raster = fixture.scan["pages"][0]["raster"]
    assert image.mode == "RGB" and image.size == (raster["width"], raster["height"])
    page = fixture.adapter.page(1)
    assert page["candidate_state"] == "retry_failed"
    assert page["ocr_comparison_state"] == "candidate_unavailable"
    assert page["regions"]
    expected = page["regions"][0]["bbox"]
    assert fixture.adapter.region(1, page["regions"][0]["region_id"]) == expected
    expected[0] = 1
    assert fixture.adapter.page(1)["regions"][0]["bbox"][0] != 1


def test_display_is_bounded_without_replacing_original_region_coordinates(make_review):
    fixture = make_review(large=True, annotations=True)
    assert max(fixture.scan["pages"][0]["raster"][name] for name in ("width", "height")) > review.DISPLAY_SIDE
    image = fixture.adapter.render(1)
    assert max(image.size) == review.DISPLAY_SIDE
    region = fixture.adapter.page(1)["regions"][0]
    assert fixture.adapter.region(1, region["region_id"]) == region["bbox"]
    image.paste("red", (0, 0, image.width, image.height))
    assert fixture.adapter.render(1).getpixel((0, 0)) != (255, 0, 0)


def test_unrequested_and_threshold_blank_are_distinct_and_not_success_claims(make_review):
    adapter = make_review().adapter
    assert adapter.page(2)["scan_status"] == "blank_at_threshold"
    assert adapter.render(2) is not None
    assert adapter.page(3)["scan_status"] == "not_requested"
    assert adapter.page(3)["regions"] == [] and adapter.render(3) is None
    with pytest.raises(ValueError):
        adapter.region(2, "scan-00001")


def test_unavailable_raster_stays_unavailable(make_review):
    from test_ocr_scan_omission import unavailable

    adapter = make_review(mutate=lambda scan: unavailable(scan["pages"][0])).adapter
    assert adapter.page(1)["scan_status"] == "unavailable"
    assert adapter.render(1) is None
    with pytest.raises(ValueError):
        adapter.region(1, "scan-00001")


@pytest.mark.parametrize("bad", [True, None, "1", 0, -1, 4, 10 ** 1000])
def test_only_exact_requested_page_identifiers_are_accepted(make_review, bad):
    adapter = make_review().adapter
    for operation in (adapter.page, adapter.render):
        with pytest.raises(ValueError):
            operation(bad)


@pytest.mark.parametrize("bad", [None, 1, True, "../scan.json", "scan-99999"])
def test_browser_region_values_cannot_name_files_or_unobserved_regions(make_review, bad):
    with pytest.raises(ValueError):
        make_review().adapter.region(1, bad)


def test_declared_but_unreproducible_pixels_never_yield_overlay_or_crop(make_review):
    fixture = make_review(mutate=lambda scan: scan["pages"][0]["raster"].update(pixel_sha256="0" * 64))
    assert fixture.adapter.page(1)["regions"]  # Declaration is readable, not verified pixels.
    with pytest.raises(ValueError, match="original pixels"):
        fixture.adapter.render(1)
    with pytest.raises(ValueError, match="original pixels"):
        fixture.adapter.region(1, "scan-00001")


def test_declared_geometry_mismatch_precedes_native_allocation(make_review, monkeypatch):
    fixture = make_review(mutate=lambda scan: scan["pages"][0]["geometry"].update(width_points=240.1))
    fitz = pytest.importorskip("pymupdf")
    monkeypatch.setattr(fitz.Page, "get_pixmap", lambda *a, **kw: pytest.fail("render must not run"))
    with pytest.raises(ValueError, match="geometry"):
        fixture.adapter.render(1)


@pytest.mark.parametrize("name", ["source.pdf", "scan.json", "diagnostics.json", "manifest.json"])
def test_changed_fixed_inputs_disable_pages_rendering_and_region_actions(make_review, name):
    fixture = make_review()
    target = fixture.pdf if name == "source.pdf" else fixture.bundle_path / name
    target.write_bytes(b"changed fixed input")
    for action in (lambda: fixture.adapter.page(1), lambda: fixture.adapter.render(1),
                   lambda: fixture.adapter.region(1, "scan-00001")):
        with pytest.raises(RuntimeError, match="changed"):
            action()


def test_same_bytes_new_identity_and_directory_replacements_fail(make_review, monkeypatch):
    fixture = make_review()
    snapshot = review._snapshot

    def replaced(path, limit):
        raw, digest, identity = snapshot(path, limit)
        return raw, digest, (-1, -1) if path == fixture.pdf else identity

    monkeypatch.setattr(review, "_snapshot", replaced)
    with pytest.raises(RuntimeError, match="generation"):
        fixture.adapter.verify_inputs()
    monkeypatch.setattr(review, "_snapshot", snapshot)
    monkeypatch.setattr(review, "_directory", lambda path: (-1, -1))
    with pytest.raises(RuntimeError, match="directory"):
        fixture.adapter.verify_inputs()


def test_bundle_change_between_adapter_snapshot_and_loader_read_rejected(make_review, monkeypatch):
    fixture = make_review()
    changed = copy.deepcopy(fixture.bundle)
    changed["manifest"]["test"] = "different loader generation"
    monkeypatch.setattr(ocr_scan_io, "read_scan_review_bundle", lambda *a, **kw: changed)
    with pytest.raises(RuntimeError, match="admission"):
        review.ScanReview(fixture.bundle_path, fixture.pdf, **fixture.arguments)


def test_mutation_during_render_is_rechecked_before_preview_returns(make_review, monkeypatch):
    fixture = make_review()
    fitz = pytest.importorskip("pymupdf")
    original = fitz.Page.get_pixmap

    def changed(page, **kwargs):
        result = original(page, **kwargs)
        fixture.pdf.write_bytes(b"changed during native render")
        return result

    monkeypatch.setattr(fitz.Page, "get_pixmap", changed)
    with pytest.raises(RuntimeError, match="source generation"):
        fixture.adapter.render(1)


def test_workspace_binds_scan_bundle_and_rechecks_it_before_exports(make_review):
    from ocr_review_runtime import ReviewWorkspace

    fixture = make_review()
    workspace = ReviewWorkspace(fixture.pdf, fixture.recovery_file, fixture.output,
                                scan_bundle_path=fixture.bundle_path)
    assert workspace.scan_bundle_path == fixture.bundle_path
    assert workspace.scan_page(1) == fixture.adapter.page(1)
    assert workspace.scan_render(1).size == fixture.adapter.render(1).size
    region = workspace.scan_page(1)["regions"][0]
    assert workspace.scan_region(1, region["region_id"]) == region["bbox"]
    (fixture.bundle_path / "manifest.json").write_bytes(b"changed")
    payload = workspace.document.references([{"page_number": 1, "reference": "SYNTHETIC"}], confirmed=True)
    with pytest.raises(RuntimeError, match="bundle generation"):
        workspace.save("references", payload)
    assert list(fixture.output.glob("ocr-references-*.json")) == []


def test_no_bundle_preserves_existing_workspace_behavior(make_review):
    from ocr_review_runtime import ReviewWorkspace

    fixture = make_review()
    workspace = ReviewWorkspace(fixture.pdf, fixture.recovery_file, fixture.output)
    assert workspace.scan_bundle_path is None
    assert workspace.scan_page(1) is None and workspace.scan_render(1) is None
    with pytest.raises(ValueError, match="no fixed scan"):
        workspace.scan_region(1, "scan-00001")
