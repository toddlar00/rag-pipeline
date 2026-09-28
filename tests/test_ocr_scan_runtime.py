"""Generated native arrays/in-memory pages; no private PDFs, OCR, or models."""
from __future__ import annotations

import copy
import hashlib
import inspect
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

import ocr_scan_runtime as runtime
from ocr_scan_omission import DEFAULT_CONFIGURATION, validate_scan_observation


@pytest.fixture
def native():
    return SimpleNamespace(cv2=pytest.importorskip("cv2"), np=pytest.importorskip("numpy"),
                           fitz=pytest.importorskip("pymupdf"))


def _discover(gray, native, **limits):
    return runtime._discover(gray, configuration={**DEFAULT_CONFIGURATION, **limits}, cv2=native.cv2, np=native.np)


def _white(native, height=200, width=400):
    return native.np.full((height, width), 255, native.np.uint8)


def _blocks(native):
    gray = _white(native)
    for left in (20, 50, 80, 110, 140):
        gray[80:100, left:left + 9] = 0
    return gray


class _Document:
    needs_pass = False

    def __init__(self, pages, events):
        self.pages, self.events, self.closed = pages, events, False

    def __len__(self):
        return len(self.pages)

    def __getitem__(self, index):
        self.events.append(("load", index + 1))
        page = self.pages[index]
        if isinstance(page, BaseException):
            raise page
        return page

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.closed = True


def _page(native, events, number, *, width=72, height=72, pixels=None, failure=None):
    rect = native.fitz.Rect(0, 0, width, height)

    def render(**kwargs):
        events.append(("render", number))
        assert kwargs["colorspace"] == native.fitz.csGRAY and kwargs["alpha"] is False
        assert kwargs["annots"] is DEFAULT_CONFIGURATION["render_annotations"] is True
        if failure is not None:
            raise failure
        bounds = (rect * kwargs["matrix"]).irect
        gray = pixels if pixels is not None else _white(native, bounds.height, bounds.width)
        return SimpleNamespace(x=0, y=0, width=bounds.width, height=bounds.height,
                               n=1, stride=bounds.width, samples_mv=memoryview(gray).cast("B"))

    return SimpleNamespace(rect=rect, cropbox=native.fitz.Rect(0, 0, width, height), rotation=0, get_pixmap=render)


def _install_document(monkeypatch, native, pages, events):
    document = _Document(pages, events)
    monkeypatch.setattr(native.fitz, "open", lambda **_kwargs: document)
    return document


def test_discovery_is_deterministic_immutable_and_does_not_claim_text(native):
    gray = _blocks(native)
    before = gray.copy()
    first, second = _discover(gray, native), _discover(gray, native)
    assert first == second and native.np.array_equal(gray, before)
    assert first["status"] == "available"
    assert [region["kind"] for region in first["regions"]] == ["text_like"]
    assert "not_text_proof" in first["regions"][0]["reason"]
    assert sum(region["foreground_pixels"] for region in first["regions"]) == first["threshold_foreground_pixels"]
    # Deliberately authored NON-TEXT rectangles: keep this false-positive
    # counterexample, rather than quietly training this fixture away.
    assert first["regions"][0]["component_count"] == 5


@pytest.mark.parametrize(("case", "status", "kinds"), [
    ("blank", "blank_at_threshold", []),
    ("solid", "ambiguous", ["ambiguous_ink"]),
    ("rule", "available", ["rule_like"]),
    ("tiny", "available", ["ambiguous_ink", "ambiguous_ink"]),
])
def test_blank_dense_rules_and_subscale_support_are_distinct(native, case, status, kinds):
    gray = _white(native)
    if case == "solid":
        gray[:] = 0
    elif case == "rule":
        gray[90:92, 10:390] = 0
    elif case == "tiny":
        gray[90:92, 20:23] = 0
        gray[90:92, 150:153] = 0
    result = _discover(gray, native)
    assert result["status"] == status
    assert [region["kind"] for region in result["regions"]] == kinds
    assert result["foreground_accounting_complete"] is True
    assert sum(region["foreground_pixels"] for region in result["regions"]) == result["threshold_foreground_pixels"]
    if case in {"blank", "solid"}:
        assert result["work"] == runtime._empty("stage_failed")["work"]


def test_small_print_and_picture_do_not_silently_disappear(native):
    text = _white(native)
    native.cv2.putText(text, "Footnote 7: small text.", (20, 160), native.cv2.FONT_HERSHEY_SIMPLEX,
                       .35, 0, 1, native.cv2.LINE_AA)
    result = _discover(text, native)
    assert result["status"] == "available" and result["regions"]
    assert any(region["kind"] == "text_like" for region in result["regions"])
    picture = _white(native)
    native.cv2.circle(picture, (200, 100), 45, 0, -1)
    result = _discover(picture, native)
    assert result["regions"] and all(region["kind"] != "text_like" for region in result["regions"])
    assert result["threshold_foreground_pixels"] == sum(region["foreground_pixels"] for region in result["regions"])


def test_horizontal_run_limit_precedes_native_component_allocation(native, monkeypatch):
    mask = native.np.zeros((1024, 1024), native.np.uint8)
    mask[::2, ::2] = 255

    def forbidden(*_args, **_kwargs):
        pytest.fail("native connected-component allocation occurred before run admission")

    monkeypatch.setattr(native.cv2, "connectedComponentsWithStats", forbidden)
    work = runtime._empty("stage_failed")["work"]
    with pytest.raises(runtime._Limit, match="horizontal_run_limit"):
        runtime._components(mask, name="residual", work=work, configuration=DEFAULT_CONFIGURATION,
                            cv2=native.cv2, np=native.np)
    assert work["residual_horizontal_runs"] == 262144 and work["residual_component_count"] is None


@pytest.mark.parametrize(("limits", "reason"), [
    ({"max_components_per_page": 4}, "component_limit"),
    ({"max_pairs_per_page": 9}, "component_pair_limit"),
    ({"max_regions_per_page": 0}, "region_limit"),
])
def test_component_pair_region_limits_abstain_without_partial_success(native, limits, reason):
    result = _discover(_blocks(native), native, **limits)
    assert result["status"] == "unavailable" and result["reason"] == reason
    assert result["regions"] == [] and result["foreground_accounting_complete"] is False
    assert result["threshold_foreground_pixels"] > 0
    if reason == "component_pair_limit":
        assert result["work"]["pair_tests"] == 0


def test_rule_and_residual_components_share_one_total_budget(native):
    gray = _blocks(native)
    gray[25:27, 10:390] = 0
    result = _discover(gray, native, max_components_per_page=5)
    assert result["reason"] == "component_limit" and result["regions"] == []
    assert result["work"]["rule_component_count"] == 1
    assert result["work"]["residual_component_count"] == 5


def test_recorded_minimum_glyph_area_controls_admission_without_discarding_ink(native):
    gray = _white(native)
    for left in (10, 13, 16):
        gray[5:8, left:left + 1] = 0
    admitted = _discover(gray, native)
    rejected = _discover(gray, native, glyph_min_area_pixels=4)
    assert DEFAULT_CONFIGURATION["glyph_min_area_pixels"] == 3
    assert [region["kind"] for region in admitted["regions"]] == ["text_like"]
    assert [region["kind"] for region in rejected["regions"]] == ["ambiguous_ink"] * 3
    assert admitted["threshold_foreground_pixels"] == rejected["threshold_foreground_pixels"] == 9
    assert sum(region["foreground_pixels"] for region in rejected["regions"]) == 9


@pytest.mark.parametrize("phase", ["threshold", "morphologyEx", "connectedComponentsWithStats"])
def test_native_discovery_errors_are_static_and_not_false_blank(native, monkeypatch, phase):
    def fail(*_args, **_kwargs):
        raise RuntimeError("PRIVATE_NATIVE_DETAILS")

    monkeypatch.setattr(native.cv2, phase, fail)
    result = _discover(_blocks(native), native)
    assert result["status"] == "unavailable" and result["reason"] == "stage_failed"
    assert "PRIVATE" not in json.dumps(result) and result["regions"] == []
    assert result["foreground_accounting_complete"] is False


def test_native_pixel_mutation_is_rejected(native, monkeypatch):
    original = native.cv2.threshold

    def mutate(gray, *args):
        gray[0, 0] = 0
        return original(gray, *args)

    monkeypatch.setattr(native.cv2, "threshold", mutate)
    with pytest.raises(ValueError, match="changed source raster"):
        _discover(_blocks(native), native)


def test_pixel_digest_includes_domain_dimensions_and_exact_gray_values(native):
    gray = _white(native, 2, 3)
    expected = hashlib.sha256(b"rag-pipeline:ocr-scan-gray8:v1\0[2,3]\0" + gray.tobytes()).hexdigest()
    assert runtime._pixel_hash(gray, DEFAULT_CONFIGURATION) == expected
    assert runtime._pixel_hash(gray.reshape(3, 2), DEFAULT_CONFIGURATION) != expected


def test_all_requested_geometry_precedes_render_and_failed_page_is_retained(native, monkeypatch):
    events = []
    document = _install_document(monkeypatch, native, [
        _page(native, events, 1, failure=RuntimeError("PRIVATE_RENDER")),
        _page(native, events, 2), _page(native, events, 3)], events)
    requested = [2, 1]
    report = runtime.collect_scan_observation(b"synthetic fake source", requested_pages=requested)
    assert requested == [2, 1] and report["requested_pages"] == [1, 2] and report["page_count"] == 3
    assert events == [("load", 1), ("load", 2), ("render", 1), ("render", 2)] and document.closed
    assert report["pages"][0]["discovery"]["reason"] == "render_failed"
    assert report["pages"][0]["raster"] is None
    assert report["pages"][1]["discovery"]["status"] == "blank_at_threshold"
    assert "PRIVATE" not in json.dumps(report) and validate_scan_observation(report) == report


def test_geometry_failure_is_not_empty_candidate_or_missing_page(native, monkeypatch):
    events = []
    _install_document(monkeypatch, native, [ValueError("PRIVATE_GEOMETRY"), _page(native, events, 2)], events)
    report = runtime.collect_scan_observation(b"synthetic source", requested_pages=[1, 2])
    first = report["pages"][0]
    assert first["geometry"] is None and first["raster"] is None
    assert first["discovery"] == runtime._empty("geometry_unavailable")
    assert report["pages"][1]["discovery"]["status"] == "blank_at_threshold"


def test_oversized_page_never_renders_but_valid_peer_remains(native, monkeypatch):
    events = []
    _install_document(monkeypatch, native, [_page(native, events, 1, width=1500, height=1500),
                                           _page(native, events, 2)], events)
    report = runtime.collect_scan_observation(b"synthetic source", requested_pages=[1, 2])
    assert report["pages"][0]["discovery"]["reason"] == "raster_limit"
    assert ("render", 1) not in events and ("render", 2) in events


@pytest.mark.parametrize("geometry_failure", [False, True])
def test_aggregate_budget_abstains_every_requested_page_before_render(native, monkeypatch, geometry_failure):
    events = []
    pages = [_page(native, events, number, width=1100, height=1100) for number in range(1, 9)]
    if geometry_failure:
        pages[0] = ValueError("synthetic geometry failure")
    _install_document(monkeypatch, native, pages, events)
    report = runtime.collect_scan_observation(b"synthetic source", requested_pages=list(range(1, 9)))
    assert all(event[0] == "load" for event in events)
    assert len(report["pages"]) == 8
    assert all(page["discovery"]["reason"] == "cohort_pixel_limit" and page["raster"] is None for page in report["pages"])
    if geometry_failure:
        assert report["pages"][0]["geometry"] is None


@pytest.mark.parametrize("field", ["x", "y", "width", "height", "n", "stride", "samples_mv"])
def test_unexpected_native_pixmap_cannot_claim_a_bound_raster(native, monkeypatch, field):
    events = []
    page = _page(native, events, 1)
    original = page.get_pixmap

    def invalid(**kwargs):
        pixmap = original(**kwargs)
        setattr(pixmap, field, b"short" if field == "samples_mv" else getattr(pixmap, field) + 1)
        return pixmap

    page.get_pixmap = invalid
    _install_document(monkeypatch, native, [page], events)
    report = runtime.collect_scan_observation(b"synthetic source", requested_pages=[1])
    assert report["pages"][0]["raster"] is None
    assert report["pages"][0]["discovery"]["reason"] == "render_failed"


@pytest.mark.parametrize("phase", ["load", "render", "threshold"])
def test_cancellation_propagates_and_closes_source(native, monkeypatch, phase):
    events = []
    page = KeyboardInterrupt() if phase == "load" else _page(native, events, 1,
        failure=KeyboardInterrupt() if phase == "render" else None)
    document = _install_document(monkeypatch, native, [page], events)
    if phase == "threshold":
        def cancel(*_args, **_kwargs):
            raise KeyboardInterrupt()
        monkeypatch.setattr(native.cv2, "threshold", cancel)
    with pytest.raises(KeyboardInterrupt):
        runtime.collect_scan_observation(b"synthetic source", requested_pages=[1])
    assert document.closed


@pytest.mark.parametrize("value", [None, (), [], [True], [0], [5001], [1, 1], list(range(1, 10)), ["1"], [1.0]])
def test_invalid_cohorts_are_rejected_before_source_open(value, native, monkeypatch):
    monkeypatch.setattr(native.fitz, "open", lambda **_kwargs: pytest.fail("invalid cohort opened source"))
    with pytest.raises(ValueError, match="bounded explicit page cohort"):
        runtime.collect_scan_observation(b"source", requested_pages=value)


@pytest.mark.parametrize("value", [None, "pdf", bytearray(b"pdf"), b""])
def test_only_bounded_immutable_source_bytes_are_admitted(value):
    with pytest.raises(ValueError, match="bounded immutable bytes"):
        runtime.collect_scan_observation(value, requested_pages=[1])


def test_source_byte_limit_is_checked_before_parser(monkeypatch):
    monkeypatch.setattr(runtime, "MAX_SOURCE_BYTES", 3)
    with pytest.raises(ValueError, match="bounded immutable bytes"):
        runtime.collect_scan_observation(b"four", requested_pages=[1])


@pytest.mark.parametrize("case", ["encrypted", "outside_page", "empty_document"])
def test_source_admission_never_fabricates_page_count(native, monkeypatch, case):
    events = []
    document = _install_document(monkeypatch, native, [] if case == "empty_document" else [_page(native, events, 1)], events)
    document.needs_pass = case == "encrypted"
    with pytest.raises(ValueError, match="source or requested pages"):
        runtime.collect_scan_observation(b"source", requested_pages=[2 if case == "outside_page" else 1])
    assert not events and document.closed


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_actual_generated_in_memory_pdf_binding_and_quarter_turns(native, rotation):
    with native.fitz.open() as source:
        page = source.new_page(width=72, height=120)
        page.insert_text((6, 20), "SYNTHETIC", fontsize=7)
        page.draw_rect(native.fitz.Rect(6, 40, 30, 55), fill=(0, 0, 0))
        page.set_rotation(rotation)
        raw = source.tobytes(no_new_id=True)
    before = hashlib.sha256(raw).hexdigest()
    report = runtime.collect_scan_observation(raw, requested_pages=[1])
    observed = report["pages"][0]
    assert report["source_sha256"] == before == hashlib.sha256(raw).hexdigest()
    assert observed["geometry"]["rotation"] == rotation
    with native.fitz.open(stream=raw, filetype="pdf") as source:
        pixmap = source[0].get_pixmap(matrix=native.fitz.Matrix(300 / 72, 300 / 72), colorspace=native.fitz.csGRAY, alpha=False)
        gray = native.np.frombuffer(pixmap.samples_mv, dtype=native.np.uint8).reshape(pixmap.height, pixmap.width).copy()
        assert observed["raster"]["pixel_sha256"] == runtime._pixel_hash(gray, DEFAULT_CONFIGURATION)
        assert observed["raster"]["width"] == pixmap.width and observed["raster"]["height"] == pixmap.height
    assert observed["discovery"]["foreground_accounting_complete"] is True
    assert validate_scan_observation(copy.deepcopy(report)) == report


def test_generated_annotation_appearance_is_explicitly_included_in_exact_pixels(native):
    with native.fitz.open() as source:
        page = source.new_page(width=72, height=72)
        page.insert_text((6, 20), "SYNTHETIC", fontsize=7)
        annotation = page.add_rect_annot(native.fitz.Rect(6, 30, 40, 50))
        annotation.set_colors(stroke=(0, 0, 0), fill=(0, 0, 0))
        annotation.update()
        raw = source.tobytes(no_new_id=True)
    report = runtime.collect_scan_observation(raw, requested_pages=[1])
    assert report["configuration"]["render_annotations"] is True
    hashes = {}
    with native.fitz.open(stream=raw, filetype="pdf") as source:
        for annots in (True, False):
            pixmap = source[0].get_pixmap(matrix=native.fitz.Matrix(300 / 72, 300 / 72),
                                         colorspace=native.fitz.csGRAY, alpha=False, annots=annots)
            gray = native.np.frombuffer(pixmap.samples_mv, dtype=native.np.uint8).reshape(
                pixmap.height, pixmap.width).copy()
            hashes[annots] = runtime._pixel_hash(gray, DEFAULT_CONFIGURATION)
    observed = report["pages"][0]["raster"]["pixel_sha256"]
    assert observed == hashes[True] != hashes[False]
    # This is annotation-inclusive source appearance, not a claim about the
    # unannotated page body or the semantic truth of any annotation text.
    assert report["source_sha256"] == hashlib.sha256(raw).hexdigest()


def test_public_surface_is_source_only_and_cold_import_has_no_native_or_model_runtime():
    assert list(inspect.signature(runtime.collect_scan_observation).parameters) == ["source", "requested_pages", "recipe"]
    assert inspect.signature(runtime.collect_scan_observation).parameters["recipe"].default == "legacy-v1"
    code = "import sys; import ocr_scan_runtime; assert not ({'cv2','numpy','pymupdf','rapidocr','onnxruntime'} & set(sys.modules))"
    outcome = subprocess.run([sys.executable, "-B", "-c", code], cwd=Path(__file__).resolve().parents[1],
                             capture_output=True, text=True, timeout=30, check=False)
    assert outcome.returncode == 0, outcome.stderr
