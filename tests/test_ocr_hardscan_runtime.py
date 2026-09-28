"""Synthetic in-memory PDF crops with stubbed OCR; no models or file inputs."""

from pathlib import Path
from types import SimpleNamespace

import pytest

import ocr_hardscan as policy
from ocr_hardscan_runtime import RapidOCRHardScanReader


@pytest.fixture
def document():
    fitz = pytest.importorskip("pymupdf")
    document = fitz.open()
    page = document.new_page(width=200, height=120)
    for rect, color in (([0, 0, 100, 60], (1, 0, 0)), ([100, 0, 200, 60], (0, 1, 0)),
                        ([0, 60, 100, 120], (0, 0, 1)), ([100, 60, 200, 120], (1, 1, 0))):
        page.draw_rect(fitz.Rect(rect), fill=color, color=color)
    page.set_cropbox(fitz.Rect(20, 10, 180, 110))
    yield fitz, document
    if not document.is_closed:
        document.close()


def reader_for(document, dpi):
    fitz, pdf = document
    reader = RapidOCRHardScanReader(Path("unused-synthetic.pdf"), dpi=dpi)
    reader._pymupdf, reader._document, reader._page_count = fitz, pdf, len(pdf)
    return reader


def recipe(angle=0, bow=0.):
    return {"orientation_clockwise": angle, "illumination": "none", "bow_fraction": bow,
            "bow_assumption": "parallel_horizontal_baselines" if bow else "none"}


@pytest.mark.parametrize("intrinsic", [0, 90, 180, 270])
@pytest.mark.parametrize("correction", [0, 90, 180, 270])
def test_real_pdf_crop_and_recipe_rotation_preserve_distinct_physical_quadrants(
        document, monkeypatch, intrinsic, correction):
    np = pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    document[1][0].set_rotation(intrinsic)
    reader = reader_for(document, 300)
    seen = []
    expected = {0: [0, 0, 255], 90: [255, 0, 0], 180: [0, 255, 255], 270: [0, 255, 0]}[intrinsic]

    def load():
        reader._engine_version = "synthetic"

        def engine(pixels):
            assert pixels.flags.c_contiguous and pixels.dtype == np.uint8
            assert np.all(pixels[2:-2, 2:-2] == np.asarray(expected, np.uint8))
            seen.append(pixels.shape)
            height, width = pixels.shape[:2]
            return SimpleNamespace(txts=["synthetic"], scores=[.9],
                                   boxes=[[[0, 0], [width, 0], [width, height], [0, height]]])

        engine.prepare = lambda pixels: None
        return engine

    monkeypatch.setattr(reader, "_load_engine", load)
    bbox = [.1, .1, .4, .4]
    description = reader.describe_hardscan(1, bbox, recipe(correction))
    assert seen == []
    result = reader.retry_hardscan(1, bbox, recipe(correction))
    assert result["geometry"] == description["geometry"]
    assert result["transform"] == description["transform"]
    assert result["candidate"]["raster"]["coordinate_system"] == "hardscan_image_pixels"
    assert result["processing"]["status"] == "completed" and len(seen) == 1
    polygon = policy.source_polygon(result["candidate"]["lines"][0]["box"], result["transform"])
    assert len(polygon) == 4
    reader.close()
    assert reader._document is None


def test_bow_geometry_abstention_never_loads_engine_or_renders(document, monkeypatch):
    reader = reader_for(document, 300)
    monkeypatch.setattr(reader, "_load_engine", lambda: pytest.fail("engine loaded"))
    result = reader.retry_hardscan(1, [.1, .1, .4, .4], recipe(0, .0001))
    assert result["candidate"] is None and result["processing"] is None
    assert result["transform"]["eligibility"] == "abstained"


def test_bow_on_large_flat_color_abstains_without_engine(document, monkeypatch):
    pytest.importorskip("cv2")
    reader = reader_for(document, 300)
    monkeypatch.setattr(reader, "_load_engine", lambda: pytest.fail("engine loaded"))
    result = reader.retry_hardscan(1, [.1, .1, .4, .4], recipe(0, .02))
    assert result["candidate"] is None and result["processing"]["status"] == "abstained"


@pytest.mark.parametrize("setting,value", [("preprocessing", "deskew"), ("min_score", .2)])
def test_implicit_old_preprocessing_or_filtering_is_not_allowed(document, setting, value):
    reader = reader_for(document, 300)
    setattr(reader, setting, value)
    with pytest.raises(ValueError, match="implicit"):
        reader.describe_hardscan(1, [0, 0, 1, 1], recipe())


@pytest.mark.parametrize("oversized", ["lines", "text"])
def test_engine_output_budget_precedes_general_array_copy_and_join(document, monkeypatch, oversized):
    pytest.importorskip("cv2")
    reader = reader_for(document, 300)
    raw = (SimpleNamespace(txts=["x"]*1001, scores=[.9]*1001, boxes=[[]]*1001) if oversized == "lines" else
           SimpleNamespace(txts=["x"*100001], scores=[.9], boxes=[[]]))
    def engine(_pixels):
        return raw

    engine.prepare = lambda pixels: None
    monkeypatch.setattr(reader, "_load_engine", lambda: engine)
    with pytest.raises(ValueError, match="budget"):
        reader.retry_hardscan(1, [.1, .1, .4, .4], recipe())
