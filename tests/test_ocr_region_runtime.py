"""Synthetic PDF crop geometry tests; recognition is stubbed, models never loaded."""

import hashlib
from types import SimpleNamespace

import pytest

from ocr_region_runtime import RapidOCRRegionReader
from ocr_regions import _page_boxes, _validate_geometry


@pytest.fixture
def pdf_library():
    return pytest.importorskip("pymupdf")


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("dpi", [300, 400])
def test_rotated_cropped_page_maps_crop_boxes_back_to_original_display(
        tmp_path, monkeypatch, pdf_library, rotation, dpi):
    np = pytest.importorskip("numpy")
    fitz = pdf_library
    source = tmp_path / "synthetic.pdf"
    with fitz.open() as doc:
        page = doc.new_page(width=200, height=120)
        # Four physical source quadrants make a wrong rotated clip observable.
        for bounds, color in (([0, 0, 100, 60], (1, 0, 0)),
                              ([100, 0, 200, 60], (0, 1, 0)),
                              ([0, 60, 100, 120], (0, 0, 1)),
                              ([100, 60, 200, 120], (1, 1, 0))):
            page.draw_rect(fitz.Rect(bounds), fill=color, color=color)
        page.set_cropbox(fitz.Rect(20, 10, 180, 110))
        page.set_rotation(rotation)
        source.write_bytes(doc.tobytes())
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    seen = []

    def load(self):
        self._engine_version = "synthetic"

        def recognize(pixels):
            assert pixels.dtype == np.uint8 and pixels.flags.c_contiguous
            assert pixels.shape[2] == 3
            expected_bgr = {0: [0, 0, 255], 90: [255, 0, 0],
                            180: [0, 255, 255], 270: [0, 255, 0]}[rotation]
            assert pixels[pixels.shape[0]//2, pixels.shape[1]//2].tolist() == expected_bgr
            assert np.all(pixels[2:-2, 2:-2] == np.array(expected_bgr, dtype=np.uint8))
            seen.append(pixels.shape)
            height, width = pixels.shape[:2]
            return SimpleNamespace(txts=["synthetic"], scores=[0.9],
                                   boxes=[[[0, 0], [width, 0], [width, height], [0, height]]])

        recognize.prepare = lambda pixels: None
        return recognize

    monkeypatch.setattr(RapidOCRRegionReader, "_load_engine", load)
    bbox = [0.1, 0.1, 0.4, 0.4]
    with RapidOCRRegionReader(source, dpi=dpi) as reader:
        description = reader.describe_region(1, bbox)
        assert seen == []
        result = reader.retry_region(1, bbox)
        assert result["geometry"] == description
        _validate_geometry(description, bbox, dpi=dpi)
        assert description["page"]["rotation_degrees"] == rotation
        assert description["page"]["cropbox_points"] == [20, 10, 180, 110]
        mapped = _page_boxes(result["candidate"], description)[0]
        assert mapped[0] == pytest.approx(bbox[:2], abs=0.003)
        assert mapped[2] == pytest.approx(bbox[2:], abs=0.003)
    assert reader._document is None and reader._engine is None
    assert len(seen) == 1
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before


def test_region_budget_is_checked_before_engine_or_rendering(tmp_path, monkeypatch, pdf_library):
    source = tmp_path / "synthetic.pdf"
    with pdf_library.open() as doc:
        doc.new_page(width=14400, height=14400)
        source.write_bytes(doc.tobytes())
    monkeypatch.setattr(RapidOCRRegionReader, "_load_engine", lambda _self: pytest.fail("model loaded"))
    with RapidOCRRegionReader(source) as reader:
        with pytest.raises(ValueError, match="budget"):
            reader.retry_region(1, [0, 0, 1, 1])
        description = reader.describe_region(1, [0, 0, 0.001, 0.001])
        assert description["raster"]["width"] == description["raster"]["height"] == 60


@pytest.mark.parametrize("bbox", [None, [], [0, 0, 1], [True, 0, 1, 1], [0, 0, 10**1000, 1],
                                  [0, 0, float("nan"), 1], [-1, 0, 1, 1], [0, 0, 0, 1]])
def test_invalid_region_bounds_are_rejected_without_model_load(tmp_path, monkeypatch, pdf_library, bbox):
    source = tmp_path / "synthetic.pdf"
    with pdf_library.open() as doc:
        doc.new_page()
        source.write_bytes(doc.tobytes())
    monkeypatch.setattr(RapidOCRRegionReader, "_load_engine", lambda _self: pytest.fail("model loaded"))
    with RapidOCRRegionReader(source) as reader:
        with pytest.raises(ValueError):
            reader.describe_region(1, bbox)


def test_region_retry_does_not_silently_apply_page_preprocessing(tmp_path, monkeypatch, pdf_library):
    source = tmp_path / "synthetic.pdf"
    with pdf_library.open() as doc:
        doc.new_page()
        source.write_bytes(doc.tobytes())
    monkeypatch.setattr(RapidOCRRegionReader, "_load_engine", lambda _self: pytest.fail("model loaded"))
    with RapidOCRRegionReader(source, preprocessing="deskew") as reader:
        with pytest.raises(ValueError, match="preprocessing"):
            reader.retry_region(1, [0, 0, 1, 1])
