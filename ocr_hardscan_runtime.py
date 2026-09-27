"""Bounded hard-scan image experiments over the existing verified local OCR reader."""

from __future__ import annotations

import ocr_hardscan as policy
from ocr_engine_limits import EngineLimits, working_dimensions
from ocr_region_runtime import RapidOCRRegionReader
from ocr_recovery_runtime import _validated_lines


def _thumbnail(gray, cv2):
    height, width = gray.shape
    ratio = min(1., policy.PARAMETERS["thumbnail_max_side"] / max(width, height))
    return (cv2.resize(gray, (max(1, round(width*ratio)), max(1, round(height*ratio))),
                       interpolation=cv2.INTER_AREA) if ratio < 1 else gray)


def _bow_gate(gray, cv2, np) -> str | None:
    thumbnail = _thumbnail(gray, cv2)
    _, mask = cv2.threshold(thumbnail, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    ink = int(np.count_nonzero(mask))
    if ink / mask.size < 0.002:
        return "blank_or_low_ink"
    height, width = thumbnail.shape
    if ink/mask.size > 0.35 or min(height, width) < 24:
        return "non_text_pattern"
    count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
    if count > 20_000:
        return "non_text_pattern"
    accepted, area = 0, 0
    for index in range(1, count):
        _, _, cw, ch, pixels = (int(value) for value in stats[index])
        if (3 <= pixels <= mask.size*0.025 and 2 <= cw <= width*0.65
                and 3 <= ch <= height*0.12 and 0.08 <= cw/ch <= 25):
            accepted += 1
            area += pixels
    return None if accepted >= 8 and area/ink >= 0.55 else "non_text_pattern"


def _illuminate(image, cv2, np) -> tuple:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    thumbnail = _thumbnail(gray, cv2)
    kernel = policy.PARAMETERS["background_kernel"]
    background = cv2.morphologyEx(thumbnail, cv2.MORPH_CLOSE, np.ones((kernel, kernel), np.uint8))
    background = cv2.GaussianBlur(background, (kernel, kernel), 0)
    histogram = cv2.calcHist([background], [0], None, [256], [0, 256]).reshape(-1)
    cumulative = np.cumsum(histogram, dtype=np.float64)
    low, high = [int(np.searchsorted(cumulative, cumulative[-1]*fraction)) for fraction in (0.1, 0.9)]
    ink = float(np.count_nonzero(background.astype(np.int16)-thumbnail.astype(np.int16) >= 5)/thumbnail.size)
    status, reason = policy.illumination_result(low, high, ink)
    metadata = {"status": status, "reason": reason, "background_low": low,
                "background_high": high, "ink_fraction": ink}
    if status != "applied":
        return image, metadata
    height, width = gray.shape
    # Only one uint8 full-size background; float work is bounded to row strips.
    background = cv2.resize(background, (width, height), interpolation=cv2.INTER_LINEAR)
    result = np.empty_like(image)
    rows = policy.PARAMETERS["strip_rows"]
    for top in range(0, height, rows):
        bottom = min(height, top+rows)
        gain = np.minimum(policy.PARAMETERS["max_gain"],
                          high/np.maximum(background[top:bottom].astype(np.float32),
                                          policy.PARAMETERS["background_min_level"]))
        adjusted = np.clip(np.rint(gray[top:bottom]*gain), 0, 255).astype(np.uint8)
        result[top:bottom] = adjusted[:, :, None]
    return result, metadata


def process_image(image, *, recipe: object, max_pixels: int = policy.MAX_PIXELS,
                  max_side: int = policy.MAX_SIDE) -> tuple:
    """Return (new BGR image or None, strict processing evidence, transform).

    Geometry abstention happens before allocation. Bow applicability is an
    operator declaration plus a conservative text-like-image gate, not an
    automatic physical-page model. No process-global CV settings are changed.
    """
    recipe = policy.validate_recipe(recipe)
    import numpy as np

    if (not isinstance(image, np.ndarray) or image.dtype != np.uint8
            or image.ndim != 3 or image.shape[2] != 3):
        raise ValueError("hard-scan requires a BGR uint8 array")
    height, width = image.shape[:2]
    transform = policy.describe_transform(width, height, recipe, max_pixels=max_pixels, max_side=max_side)
    if transform["eligibility"] != "ready":
        return None, None, transform
    import cv2

    libraries = {"opencv": cv2.__version__, "numpy": np.__version__}
    angle = recipe["orientation_clockwise"]
    rotation = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}
    oriented = cv2.rotate(image, rotation[angle]) if angle else image.copy(order="C")
    amplitude = transform["bow"]["amplitude_pixels"]
    if amplitude:
        reason = _bow_gate(cv2.cvtColor(oriented, cv2.COLOR_BGR2GRAY), cv2, np)
        if reason is not None:
            evidence = {"status": "abstained", "reason": reason, "illumination": None, "libraries": libraries}
            return None, policy.validate_processing(evidence, recipe=recipe), transform
        output = transform["processed_raster"]
        height, width = output["height"], output["width"]
        processed = np.empty((height, width, 3), dtype=np.uint8)
        # cv2.remap maps destination pixel centers to source pixel centers.
        # Converting our pixel-edge convention cancels the y half-pixel; the
        # nonlinear x-dependent term must still be sampled at (x+0.5)/width.
        xs = np.arange(width, dtype=np.float32)
        u = (xs+0.5)/width
        offset = 4*amplitude*u*(1-u)-transform["bow"]["translation_pixels"]
        rows = policy.PARAMETERS["strip_rows"]
        for top in range(0, height, rows):
            bottom = min(height, top+rows)
            map_x = np.tile(xs, (bottom-top, 1))
            map_y = np.arange(top, bottom, dtype=np.float32)[:, None]+offset[None, :]
            cv2.remap(oriented, map_x, map_y, cv2.INTER_LINEAR, dst=processed[top:bottom],
                      borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255))
    else:
        processed = oriented
    illumination = {"status": "disabled", "reason": "mode_disabled", "background_low": None,
                    "background_high": None, "ink_fraction": None}
    if recipe["illumination"] != "none":
        processed, illumination = _illuminate(processed, cv2, np)
    evidence = {"status": "completed", "reason": None, "illumination": illumination, "libraries": libraries}
    return processed, policy.validate_processing(evidence, recipe=recipe), transform


class RapidOCRHardScanReader(RapidOCRRegionReader):
    """One verified engine shared across explicitly planned independent crops."""

    def describe_hardscan(self, page_number: int, bbox: list, recipe: dict) -> dict:
        if self.preprocessing != "none" or self.min_score != 0.0:
            raise ValueError("hard-scan does not permit implicit preprocessing or score filtering")
        geometry = self.describe_region(page_number, bbox)
        raster = geometry["raster"]
        transform = policy.describe_transform(raster["width"], raster["height"], recipe,
                                              max_side=self.max_side, max_pixels=self.max_pixels)
        return {"geometry": geometry, "transform": transform}

    def retry_hardscan(self, page_number: int, bbox: list, recipe: dict) -> dict:
        described = self.describe_hardscan(page_number, bbox, recipe)
        if described["transform"]["eligibility"] != "ready":
            return {**described, "processing": None, "candidate": None}
        dimensions = described["transform"]["processed_raster"]
        working_dimensions(dimensions["width"], dimensions["height"], detection=True,
                           limits=EngineLimits(max_side=self.max_side, max_pixels=self.max_pixels))
        page = self._page(page_number)
        rect = page.rect
        clip = self._pymupdf.Rect(rect.x0+bbox[0]*rect.width, rect.y0+bbox[1]*rect.height,
                                 rect.x0+bbox[2]*rect.width, rect.y0+bbox[3]*rect.height)
        import numpy as np

        pixmap = page.get_pixmap(matrix=self._pymupdf.Matrix(self.dpi/72, self.dpi/72),
                                 clip=clip, colorspace=self._pymupdf.csRGB, alpha=False)
        geometry = described["geometry"]
        width, height = geometry["raster"]["width"], geometry["raster"]["height"]
        if ([pixmap.x, pixmap.y, pixmap.x+pixmap.width, pixmap.y+pixmap.height] != geometry["pixel_bounds"]
                or pixmap.n != 3 or pixmap.stride != width*3 or len(pixmap.samples_mv) != width*height*3):
            raise ValueError("hard-scan renderer returned an unexpected raster")
        rgb = np.frombuffer(pixmap.samples_mv, dtype=np.uint8).reshape(height, width, 3)
        pixels, processing, transform = process_image(rgb[:, :, ::-1].copy(), recipe=recipe,
                                                      max_side=self.max_side, max_pixels=self.max_pixels)
        if transform != described["transform"]:
            raise ValueError("hard-scan transform changed after preflight")
        if pixels is None:
            return {**described, "processing": processing, "candidate": None}
        dimensions = transform["processed_raster"]
        if (pixels.dtype != np.uint8 or not pixels.flags.c_contiguous
                or pixels.shape != (dimensions["height"], dimensions["width"], 3)):
            raise ValueError("hard-scan preprocessing returned an invalid raster")
        engine = self._load_engine()
        engine.prepare(pixels)
        result = engine(pixels)
        try:
            for values in (result.txts, result.scores, result.boxes):
                if values is not None and len(values) > policy.MAX_LINES_PER_REGION:
                    raise ValueError("hard-scan OCR result exceeds line budget")
            if result.txts is not None and sum(len(text) for text in result.txts) > 100_000:
                raise ValueError("hard-scan OCR result exceeds text budget")
        except (AttributeError, TypeError):
            raise ValueError("hard-scan OCR result has invalid arrays") from None
        lines = _validated_lines(result, width=dimensions["width"],
                                 height=dimensions["height"], min_score=0.0)
        scores = [line["score"] for line in lines]
        candidate = {"text": "\n".join(line["text"] for line in lines), "lines": lines,
                     "mean_confidence": sum(scores)/len(scores) if scores else None,
                     "raster": {**dimensions, "dpi": self.dpi, "coordinate_system": "hardscan_image_pixels"},
                     "engine": {"name": "rapidocr", "version": self._engine_version,
                                "min_score": 0.0, "max_side": self.max_side}}
        return {**described, "processing": processing, "candidate": candidate}
