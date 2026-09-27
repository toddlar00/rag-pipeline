"""Bounded local PDF rendering and verified RapidOCR recovery candidates.

This module deliberately returns candidates in the OCR engine's order. It does
not correct source documents or infer reading order for columns and tables.
Optional PDF, array, and OCR dependencies are loaded only when needed.
"""

from __future__ import annotations

from collections.abc import Mapping
from importlib.metadata import version as _package_version
import math
from numbers import Real
import os
from pathlib import Path

from ocr_engine_limits import EngineLimits, working_dimensions
from ocr_preprocessing import validate_metadata, validate_mode


def _bounded_integer(value, name: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer from {minimum} to {maximum}")
    return value


def _finite_number(value, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{name} must be a finite number")
    try:
        number = float(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} must be a finite number")
    return number


def _sequence(value, name: str, *, maximum: int = 100_000) -> list:
    if isinstance(value, (str, bytes, bytearray, Mapping)):
        raise ValueError(f"OCR {name} must be an array")
    try:
        size = len(value)
        if size > maximum:
            raise ValueError(f"OCR {name} exceeds the supported size")
        return list(value)
    except TypeError as exc:
        raise ValueError(f"OCR {name} must be an array") from exc


def _validated_lines(result, *, width: int, height: int,
                     min_score: float) -> list[dict]:
    try:
        values = (result.txts, result.scores, result.boxes)
    except AttributeError as exc:
        raise ValueError("OCR result is missing text, scores, or boxes") from exc
    if all(value is None for value in values):
        return []
    if any(value is None for value in values):
        raise ValueError("OCR result has incomplete text, scores, or boxes")
    texts, scores, boxes = (
        _sequence(value, name)
        for value, name in zip(values, ("text", "scores", "boxes"))
    )
    if len(texts) != len(scores) or len(texts) != len(boxes):
        raise ValueError("OCR text, scores, and boxes have different lengths")
    lines = []
    for text, raw_score, raw_box in zip(texts, scores, boxes):
        if not isinstance(text, str):
            raise ValueError("OCR line text must be a string")
        score = _finite_number(raw_score, "OCR score")
        if not 0 <= score <= 1:
            raise ValueError("OCR score must be between zero and one")
        points = _sequence(raw_box, "box", maximum=4)
        if len(points) != 4:
            raise ValueError("OCR box must contain four points")
        box = []
        for raw_point in points:
            point = _sequence(raw_point, "point", maximum=2)
            if len(point) != 2:
                raise ValueError("OCR box point must contain two coordinates")
            x, y = (_finite_number(value, "OCR coordinate") for value in point)
            if not (0 <= x <= width and 0 <= y <= height):
                raise ValueError("OCR box is outside the rendered page")
            box.append([x, y])
        area_twice = sum(
            box[i][0] * box[(i + 1) % 4][1]
            - box[(i + 1) % 4][0] * box[i][1]
            for i in range(4)
        )
        if area_twice == 0:
            raise ValueError("OCR box must have positive area")
        # Validate even excluded lines: a threshold must not hide malformed
        # engine output. No additional confidence threshold is applied here.
        if score >= min_score:
            lines.append({"text": text, "score": score, "box": box})
    return lines


class RapidOCRPageReader:
    """Read one local PDF and produce independent page OCR candidates.

    Page numbers are one-based. Pages exceeding the configured raster bounds
    are rejected before rendering rather than silently reducing their DPI.
    Use the reader as a context manager to release its PDF and model resources.
    """

    def __init__(self, path: Path, *, dpi: int = 300,
                 max_pixels: int = 25_000_000, max_side: int = 6000,
                 min_score: float = 0.0, preprocessing: str = "none"):
        self.path = Path(path)
        self.dpi = _bounded_integer(dpi, "dpi", 72, 600)
        self.max_pixels = _bounded_integer(
            max_pixels, "max_pixels", 1, 100_000_000)
        self.max_side = _bounded_integer(max_side, "max_side", 32, 12_000)
        self.min_score = _finite_number(min_score, "min_score")
        self.preprocessing = validate_mode(preprocessing)
        if not 0 <= self.min_score <= 1:
            raise ValueError("min_score must be between zero and one")
        self._document = None
        self._pymupdf = None
        self._page_count = 0
        self._engine = None
        self._engine_version = None
        self._verified_model_paths = None

    def __enter__(self) -> RapidOCRPageReader:
        if self._document is not None:
            raise RuntimeError("PDF reader is already open")
        import pymupdf

        document = pymupdf.open(str(self.path))
        try:
            if not document.is_pdf:
                raise ValueError("OCR recovery requires a PDF document")
            if document.needs_pass:
                raise ValueError("OCR recovery requires an unlocked PDF")
            page_count = len(document)
            if page_count < 1:
                raise ValueError("OCR recovery requires a PDF with pages")
        except BaseException:
            try:
                document.close()
            except Exception:
                pass
            raise
        self._document = document
        self._pymupdf = pymupdf
        self._page_count = page_count
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        try:
            self.close()
        except Exception:
            if exc_type is None:
                raise
        return False

    def close(self) -> None:
        document = self._document
        self._document = None
        self._pymupdf = None
        self._page_count = 0
        self._engine = None
        self._engine_version = None
        self._verified_model_paths = None
        if document is not None:
            document.close()

    @property
    def page_count(self) -> int:
        if self._document is None:
            raise RuntimeError("PDF reader must be used inside a with block")
        return self._page_count

    def _page(self, page_number: int):
        count = self.page_count
        _bounded_integer(page_number, "page_number", 1, count)
        return self._document[page_number - 1]

    def native_text(self, page_number: int) -> str:
        text = self._page(page_number).get_text("text", sort=True)
        if not isinstance(text, str):
            raise ValueError("PDF native text extraction did not return text")
        return text

    def _load_engine(self):
        if self._engine is None:
            from model_artifacts import verified_installed_package_model
            from ocr_engine_guard import RapidOCREngineGuard

            paths = verified_installed_package_model("rapidocr", "docling_ocr")
            from rapidocr import OCRVersion, RapidOCR

            engine_version = _package_version("rapidocr")
            engine = RapidOCR(params={
                "Det.model_path": str(paths["detection"]),
                "Det.ocr_version": OCRVersion.PPOCRV6,
                "Rec.model_path": str(paths["recognition"]),
                "Rec.ocr_version": OCRVersion.PPOCRV6,
                "Cls.model_path": str(paths["classification"]),
                "Global.log_level": "error",
                "Global.font_path": None,
                "Global.text_score": self.min_score,
                # RapidOCR otherwise shrinks the whole input to 2000 pixels,
                # defeating higher-resolution page rendering.
                "Global.max_side_len": self.max_side,
                # Three OCR sessions otherwise each create an automatic CPU
                # thread pool. Keep this interactive recovery job bounded.
                "EngineConfig.onnxruntime.intra_op_num_threads": min(
                    2, os.cpu_count() or 1),
                "EngineConfig.onnxruntime.inter_op_num_threads": 1,
            })
            # Wrap the raw instance before execution/timing decorators: their
            # attribute reads may forward, but writes need not reach the engine.
            self._engine = RapidOCREngineGuard(
                engine, max_side=self.max_side, max_pixels=self.max_pixels)
            self._engine_version = engine_version
            self._verified_model_paths = dict(paths)
        return self._engine

    def execution_observation(self) -> dict | None:
        """Observe constructed sessions and reverify models before closing."""
        if self._engine is None:
            return None
        from ocr_execution_receipt import observe_rapidocr_engine

        return observe_rapidocr_engine(self._engine, engine_version=self._engine_version,
                                      model_paths=self._verified_model_paths)

    def retry(self, page_number: int) -> dict:
        page = self._page(page_number)
        scale = self.dpi / 72
        for name in ("width", "height"):
            dimension = _finite_number(getattr(page.rect, name), "PDF dimension")
            if dimension <= 0:
                raise ValueError("PDF page dimensions must be positive")
            if dimension * scale > self.max_side:
                raise ValueError("Rendered PDF page exceeds max_side")
        matrix = self._pymupdf.Matrix(scale, scale)
        raster_rect = (page.rect * matrix).irect
        width, height = raster_rect.width, raster_rect.height
        _bounded_integer(width, "rendered width", 1, self.max_side)
        _bounded_integer(height, "rendered height", 1, self.max_side)
        if width * height > self.max_pixels:
            raise ValueError("Rendered PDF page exceeds max_pixels")
        limits = EngineLimits(max_side=self.max_side, max_pixels=self.max_pixels)
        if self.preprocessing == "none":
            # Source caps alone do not bound Global's thin-image enlargement
            # or the detector's min-side normalization. Reject before rendering.
            working_dimensions(width, height, detection=True, limits=limits)
        engine = self._load_engine()
        import numpy as np

        pixmap = page.get_pixmap(
            matrix=matrix, colorspace=self._pymupdf.csRGB, alpha=False)
        if (pixmap.width != width or pixmap.height != height
                or pixmap.n != 3 or pixmap.stride != width * 3):
            raise ValueError("PDF renderer returned an unexpected raster layout")
        samples = pixmap.samples_mv
        if len(samples) != width * height * 3:
            raise ValueError("PDF renderer returned an incomplete raster")
        rgb = np.frombuffer(samples, dtype=np.uint8).reshape(height, width, 3)
        # PyMuPDF's raster is RGB, but RapidOCR treats ndarray inputs as BGR.
        # A contiguous copy also releases dependence on the Pixmap buffer.
        pixels = rgb[:, :, ::-1].copy()
        preprocessing = None
        if self.preprocessing != "none":
            from ocr_preprocessing import preprocess_image

            original_raster = {"width": width, "height": height}
            pixels, preprocessing = preprocess_image(
                pixels, mode=self.preprocessing,
                max_pixels=self.max_pixels, max_side=self.max_side)
            if (not isinstance(pixels, np.ndarray) or pixels.dtype != np.uint8
                    or pixels.ndim != 3 or pixels.shape[2] != 3
                    or not pixels.flags.c_contiguous):
                raise ValueError("Preprocessing returned an invalid BGR raster")
            height, width = pixels.shape[:2]
            _bounded_integer(width, "processed width", 1, self.max_side)
            _bounded_integer(height, "processed height", 1, self.max_side)
            if width * height > self.max_pixels:
                raise ValueError("Preprocessed PDF page exceeds max_pixels")
            preprocessing = validate_metadata(
                preprocessing, width=width, height=height,
                max_pixels=self.max_pixels, max_side=self.max_side)
            if (preprocessing["mode"] != self.preprocessing
                    or preprocessing["original_raster"] != original_raster):
                raise ValueError("Preprocessing metadata differs from its source raster or mode")
            # Deskew may change a thin source's dimensions. Judge the validated
            # transformed raster, not a hypothetical untransformed OCR input.
            working_dimensions(width, height, detection=True, limits=limits)
        # Preflight is deliberately outside any recorder's actual-call counter.
        engine.prepare(pixels)
        result = engine(pixels)
        lines = _validated_lines(
            result, width=width, height=height, min_score=self.min_score)
        scores = [line["score"] for line in lines]
        candidate = {
            "text": "\n".join(line["text"] for line in lines),
            "lines": lines,
            "mean_confidence": sum(scores) / len(scores) if scores else None,
            "raster": {
                "width": width,
                "height": height,
                "dpi": self.dpi,
                "coordinate_system": (
                    "rendered_image_pixels" if preprocessing is None else "preprocessed_image_pixels"),
            },
            "engine": {
                "name": "rapidocr",
                "version": self._engine_version,
                "min_score": self.min_score,
                "max_side": self.max_side,
            },
        }
        if preprocessing is not None:
            candidate["preprocessing"] = preprocessing
        return candidate
