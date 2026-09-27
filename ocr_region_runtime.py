"""Bounded display-coordinate PDF crops using the existing verified OCR engine."""

from __future__ import annotations

import math

from ocr_engine_limits import EngineLimits, working_dimensions
from ocr_recovery_runtime import RapidOCRPageReader, _finite_number, _validated_lines


class RapidOCRRegionReader(RapidOCRPageReader):
    """Render only requested crops, retaining PDF rotation and cropbox evidence."""

    def describe_region(self, page_number: int, bbox: list[float]) -> dict:
        page = self._page(page_number)
        if not isinstance(bbox, list) or len(bbox) != 4:
            raise ValueError("region bounds require ordered page-display fractions")
        bbox = [_finite_number(value, "region fraction") for value in bbox]
        if (any(not 0 <= value <= 1 for value in bbox)
                or not bbox[0] < bbox[2] or not bbox[1] < bbox[3]):
            raise ValueError("region bounds require ordered page-display fractions")
        rect = page.rect
        coordinates = [_finite_number(value, "PDF display rectangle") for value in rect]
        width, height = coordinates[2] - coordinates[0], coordinates[3] - coordinates[1]
        scale = self.dpi / 72
        if width <= 0 or height <= 0 or not math.isfinite(max(width, height) * scale):
            raise ValueError("PDF display dimensions are invalid")
        clip = self._pymupdf.Rect(
            coordinates[0] + bbox[0] * width, coordinates[1] + bbox[1] * height,
            coordinates[0] + bbox[2] * width, coordinates[1] + bbox[3] * height)
        pixel_rect = (clip * self._pymupdf.Matrix(scale, scale)).irect
        if (not 1 <= pixel_rect.width <= self.max_side
                or not 1 <= pixel_rect.height <= self.max_side
                or pixel_rect.width * pixel_rect.height > self.max_pixels):
            raise ValueError("region exceeds its raster budget")
        cropbox = [_finite_number(value, "PDF cropbox") for value in page.cropbox]
        rotation = page.rotation
        if rotation not in (0, 90, 180, 270):
            raise ValueError("PDF rotation is unsupported")
        return {
            "page": {"display_rect_points": coordinates, "cropbox_points": cropbox,
                     "rotation_degrees": rotation},
            "raster": {"width": pixel_rect.width, "height": pixel_rect.height,
                       "dpi": self.dpi, "coordinate_system": "rendered_image_pixels"},
            "pixel_bounds": list(pixel_rect),
            "crop_to_page_fraction": [
                [1 / (scale * width), 0.0, (pixel_rect.x0 / scale - coordinates[0]) / width],
                [0.0, 1 / (scale * height), (pixel_rect.y0 / scale - coordinates[1]) / height],
            ],
            "boundary_policy": "clip_to_page_bounds",
        }

    def retry_region(self, page_number: int, bbox: list[float]) -> dict:
        if self.preprocessing != "none":
            raise ValueError("region retry does not enable page preprocessing")
        geometry = self.describe_region(page_number, bbox)
        raster = geometry["raster"]
        # Keep this inside the individual retry. Cohort description must not
        # turn one unsafe OCR intermediate into a whole-report planning failure.
        working_dimensions(raster["width"], raster["height"], detection=True,
                           limits=EngineLimits(max_side=self.max_side, max_pixels=self.max_pixels))
        page = self._page(page_number)
        rect = geometry["page"]["display_rect_points"]
        width, height = rect[2] - rect[0], rect[3] - rect[1]
        clip = self._pymupdf.Rect(
            rect[0] + bbox[0] * width, rect[1] + bbox[1] * height,
            rect[0] + bbox[2] * width, rect[1] + bbox[3] * height)
        engine = self._load_engine()
        import numpy as np

        pixmap = page.get_pixmap(
            matrix=self._pymupdf.Matrix(self.dpi / 72, self.dpi / 72), clip=clip,
            colorspace=self._pymupdf.csRGB, alpha=False)
        if ([pixmap.x, pixmap.y, pixmap.x + pixmap.width, pixmap.y + pixmap.height]
                != geometry["pixel_bounds"] or pixmap.n != 3
                or pixmap.stride != pixmap.width * 3
                or len(pixmap.samples_mv) != pixmap.width * pixmap.height * 3):
            raise ValueError("region renderer returned an unexpected raster")
        rgb = np.frombuffer(pixmap.samples_mv, dtype=np.uint8).reshape(pixmap.height, pixmap.width, 3)
        pixels = rgb[:, :, ::-1].copy()
        engine.prepare(pixels)
        lines = _validated_lines(engine(pixels), width=raster["width"], height=raster["height"],
                                 min_score=self.min_score)
        scores = [line["score"] for line in lines]
        return {
            "geometry": geometry,
            "candidate": {
                "text": "\n".join(line["text"] for line in lines), "lines": lines,
                "mean_confidence": sum(scores) / len(scores) if scores else None,
                "raster": dict(raster),
                "engine": {"name": "rapidocr", "version": self._engine_version,
                           "min_score": self.min_score, "max_side": self.max_side},
            },
        }
