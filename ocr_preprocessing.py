"""Conservative image experiments with explicit, reversible pixel geometry.

The public validators are dependency-light. OpenCV and NumPy are loaded only
when processing an in-memory BGR image. This never changes the input array or
sets process-global image-library options.
"""

from __future__ import annotations

import json
import math
import re


PREPROCESSING_MODES = ("none", "deskew", "contrast", "deskew-contrast")
_ALGORITHM = "bounded-deskew-contrast-v1"
_PARAMETERS = {
    "thumbnail_max_side": 1000,
    "max_angle_degrees": 5.0,
    "angle_step_degrees": 0.25,
    "min_gain": 0.025,
    "low_percentile": 0.5,
    "high_percentile": 99.5,
}
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+\-]{0,63}")
_TOP_FIELDS = {
    "schema_version", "algorithm", "mode", "original_raster", "processed_raster",
    "source_to_processed", "processed_to_source", "deskew", "contrast",
    "parameters", "libraries",
}


def validate_mode(value: object) -> str:
    if not isinstance(value, str) or value not in PREPROCESSING_MODES:
        raise ValueError("preprocessing mode must be none, deskew, contrast, or deskew-contrast")
    return value


def _integer(value: object, name: str, lower: int, upper: int) -> int:
    if type(value) is not int or not lower <= value <= upper:
        raise ValueError(f"invalid preprocessing {name}")
    return value


def _number(value: object, name: str) -> float:
    if type(value) not in (int, float):
        raise ValueError(f"invalid preprocessing {name}")
    try:
        result = float(value)
    except (OverflowError, ValueError):
        raise ValueError(f"invalid preprocessing {name}") from None
    if not math.isfinite(result):
        raise ValueError(f"invalid preprocessing {name}")
    return result


def _fields(value: object, expected: set[str], name: str) -> dict:
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError(f"invalid preprocessing {name} fields")
    return value


def _rotation_geometry(width: int, height: int, angle: float) -> tuple:
    """Return expanded dimensions and OpenCV-compatible forward/inverse maps.

    Positive angles are counterclockwise corrections. The continuous image
    rectangle [0,width] x [0,height] fits inside the expanded destination.
    """
    radians = math.radians(angle)
    cosine, sine = math.cos(radians), math.sin(radians)
    out_width = math.ceil(abs(cosine) * width + abs(sine) * height)
    out_height = math.ceil(abs(sine) * width + abs(cosine) * height)
    tx = (out_width - cosine * width - sine * height) / 2
    ty = (out_height + sine * width - cosine * height) / 2
    forward = [[cosine, sine, tx], [-sine, cosine, ty]]
    inverse = [[cosine, -sine, -cosine * tx + sine * ty],
               [sine, cosine, -sine * tx - cosine * ty]]
    return out_width, out_height, forward, inverse


def _angle(value: object, name: str) -> float:
    value = _number(value, name)
    if abs(value) > 5 or not math.isclose(value * 4, round(value * 4), abs_tol=1e-9):
        raise ValueError(f"invalid preprocessing {name}")
    return value


def _validate_deskew(value: object, *, enabled: bool) -> float:
    step = _fields(value, {"status", "reason", "angle_degrees", "estimated_angle_degrees", "gain"}, "deskew")
    angle = _angle(step["angle_degrees"], "rotation angle")
    estimated = step["estimated_angle_degrees"]
    estimated = None if estimated is None else _angle(estimated, "estimated angle")
    gain = step["gain"]
    gain = None if gain is None else _number(gain, "projection gain")
    if gain is not None and not 0 <= gain <= 1_000_000:
        raise ValueError("invalid preprocessing projection gain")
    status, reason = step["status"], step["reason"]
    if not enabled:
        if (status != "disabled" or reason != "mode_disabled" or angle != 0
                or estimated is not None or gain is not None):
            raise ValueError("disabled deskew has active preprocessing evidence")
    elif status == "applied":
        if (reason != "rotation_applied" or estimated != angle or gain is None
                or gain < _PARAMETERS["min_gain"] or not 0.5 <= abs(angle) < 5):
            raise ValueError("invalid applied deskew evidence")
    elif status == "skipped":
        if angle != 0:
            raise ValueError("skipped deskew must have an identity rotation")
        if reason in ("blank_or_low_ink", "non_text_pattern"):
            if estimated is not None or gain is not None:
                raise ValueError("unmeasured deskew has angle evidence")
        elif reason in ("near_zero_angle", "ambiguous_angle", "boundary_angle",
                        "insufficient_gain", "expanded_raster_limit"):
            if estimated is None or gain is None:
                raise ValueError("measured deskew lacks angle evidence")
            if reason == "near_zero_angle" and abs(estimated) > 0.25:
                raise ValueError("invalid near-zero deskew evidence")
            if reason == "boundary_angle" and abs(estimated) != 5:
                raise ValueError("invalid boundary deskew evidence")
            if reason == "insufficient_gain" and gain >= _PARAMETERS["min_gain"]:
                raise ValueError("invalid insufficient-gain deskew evidence")
            if reason == "expanded_raster_limit" and (
                    not 0.5 <= abs(estimated) < 5 or gain < _PARAMETERS["min_gain"]):
                raise ValueError("invalid raster-limit deskew evidence")
        else:
            raise ValueError("unknown skipped deskew reason")
    else:
        raise ValueError("invalid enabled deskew status")
    return angle


def _validate_contrast(value: object, *, enabled: bool) -> None:
    step = _fields(value, {"status", "reason", "low_level", "high_level"}, "contrast")
    if not enabled:
        if (step["status"] != "disabled" or step["reason"] != "mode_disabled"
                or step["low_level"] is not None or step["high_level"] is not None):
            raise ValueError("disabled contrast has active preprocessing evidence")
        return
    low = _integer(step["low_level"], "contrast low level", 0, 255)
    high = _integer(step["high_level"], "contrast high level", low, 255)
    spread = high - low
    expected = (("skipped", "insufficient_dynamic_range") if spread < 16
                else ("skipped", "already_high_contrast") if spread >= 240
                else ("applied", "stretch_applied"))
    if (step["status"], step["reason"]) != expected:
        raise ValueError("contrast status contradicts measured dynamic range")


def validate_metadata(payload: object, *, width: int, height: int,
                      max_side: int = 12_000,
                      max_pixels: int = 100_000_000) -> dict:
    """Validate and detach bounded preprocessing evidence without image imports.

    Matrices must equal the declared rotation and expanded canvas geometry;
    arbitrary invertible affine maps are not accepted as rotation evidence.
    """
    max_side = _integer(max_side, "side limit", 1, 12_000)
    max_pixels = _integer(max_pixels, "pixel limit", 1, 100_000_000)
    width = _integer(width, "output width", 1, max_side)
    height = _integer(height, "output height", 1, max_side)
    value = _fields(payload, _TOP_FIELDS, "metadata")
    _integer(value["schema_version"], "schema version", 1, 1)
    if value["algorithm"] != _ALGORITHM:
        raise ValueError("unsupported preprocessing algorithm")
    mode = validate_mode(value["mode"])
    dimensions = []
    for key in ("original_raster", "processed_raster"):
        raster = _fields(value[key], {"width", "height"}, key)
        rw = _integer(raster["width"], "raster width", 1, max_side)
        rh = _integer(raster["height"], "raster height", 1, max_side)
        if rw * rh > max_pixels:
            raise ValueError("preprocessing raster exceeds pixel limit")
        dimensions.append((rw, rh))
    if dimensions[1] != (width, height):
        raise ValueError("preprocessing metadata disagrees with output raster")
    parameters = _fields(value["parameters"], set(_PARAMETERS), "parameters")
    for key, expected in _PARAMETERS.items():
        if (type(parameters[key]) not in (int, float)
                or parameters[key] != expected):
            raise ValueError("unsupported preprocessing algorithm parameters")
    libraries = _fields(value["libraries"], {"opencv", "numpy"}, "libraries")
    if any(not isinstance(version, str) or _VERSION.fullmatch(version) is None
           for version in libraries.values()):
        raise ValueError("invalid preprocessing library version")
    angle = _validate_deskew(value["deskew"], enabled=mode in ("deskew", "deskew-contrast"))
    _validate_contrast(value["contrast"], enabled=mode in ("contrast", "deskew-contrast"))
    expected_width, expected_height, forward, inverse = _rotation_geometry(*dimensions[0], angle)
    if (expected_width, expected_height) != dimensions[1]:
        raise ValueError("preprocessing rotation disagrees with expanded raster")
    for key, expected in (("source_to_processed", forward), ("processed_to_source", inverse)):
        matrix = value[key]
        if not isinstance(matrix, list) or len(matrix) != 2:
            raise ValueError("invalid preprocessing matrix")
        for row, expected_row in zip(matrix, expected):
            if not isinstance(row, list) or len(row) != 3:
                raise ValueError("invalid preprocessing matrix row")
            if any(not math.isclose(_number(entry, "matrix entry"), target,
                                    rel_tol=1e-12, abs_tol=1e-9)
                   for entry, target in zip(row, expected_row)):
                raise ValueError("preprocessing matrix disagrees with rotation")
    return json.loads(json.dumps(value, allow_nan=False, ensure_ascii=True))


def _estimate_deskew(gray, cv2, np) -> dict:
    result = {"status": "skipped", "reason": "blank_or_low_ink", "angle_degrees": 0.0,
              "estimated_angle_degrees": None, "gain": None}
    height, width = gray.shape
    ratio = min(1.0, _PARAMETERS["thumbnail_max_side"] / max(width, height))
    if ratio < 1:
        gray = cv2.resize(gray, (max(1, round(width * ratio)), max(1, round(height * ratio))),
                          interpolation=cv2.INTER_AREA)
    height, width = gray.shape
    _, mask = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    ink = int(np.count_nonzero(mask))
    if ink / mask.size < 0.002:
        return result
    result["reason"] = "non_text_pattern"
    if ink / mask.size > 0.35 or min(width, height) < 24:
        return result
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    if count > 20_000:
        return result
    keep = np.zeros(count, dtype=np.uint8)
    accepted_area, accepted_count = 0, 0
    for index in range(1, count):
        _, _, cw, ch, area = (int(v) for v in stats[index])
        if (3 <= area <= mask.size * 0.025 and 2 <= cw <= width * 0.65
                and 3 <= ch <= height * 0.12 and 0.08 <= cw / ch <= 25):
            keep[index] = 255
            accepted_area += area
            accepted_count += 1
    if accepted_count < 8 or accepted_area / ink < 0.55:
        return result
    text_mask = keep[labels]
    # One fixed canvas for every candidate prevents clipping and canvas-size
    # differences from masquerading as improved horizontal text alignment.
    candidates = [(step * _PARAMETERS["angle_step_degrees"],
                   _rotation_geometry(width, height, step * _PARAMETERS["angle_step_degrees"]))
                  for step in range(-20, 21)]
    search_width = max(geometry[0] for _, geometry in candidates)
    search_height = max(geometry[1] for _, geometry in candidates)
    scores = []
    for angle, (ow, oh, matrix, _) in candidates:
        matrix[0][2] += (search_width - ow) / 2
        matrix[1][2] += (search_height - oh) / 2
        rotated = cv2.warpAffine(
            text_mask, np.asarray(matrix, dtype=np.float64), (search_width, search_height),
            flags=cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
        projection = np.count_nonzero(rotated, axis=1).astype(np.float64)
        total = float(projection.sum())
        score = float(np.dot(projection, projection)) / total if total else 0.0
        scores.append((angle, score))
    baseline = scores[20][1]
    best_angle, best_score = min(scores, key=lambda item: (-item[1], abs(item[0]), item[0]))
    gain = max(0.0, (best_score - baseline) / baseline) if baseline else 0.0
    result.update(estimated_angle_degrees=best_angle, gain=gain)
    if abs(best_angle) == 5:
        result["reason"] = "boundary_angle"
    elif abs(best_angle) <= 0.25:
        result["reason"] = "near_zero_angle"
    elif gain < _PARAMETERS["min_gain"]:
        result["reason"] = "insufficient_gain"
    else:
        runner_up = max(score for angle, score in scores if abs(angle - best_angle) >= 0.75)
        if best_score <= runner_up * 1.01:
            result["reason"] = "ambiguous_angle"
        else:
            result.update(status="applied", reason="rotation_applied", angle_degrees=best_angle)
    return result


def _contrast_levels(gray, cv2, np) -> dict:
    # Histogram quantiles are bounded to 256 bins, with no full-image sorting.
    histogram = cv2.calcHist([gray], [0], None, [256], [0, 256]).reshape(-1)
    cumulative = np.cumsum(histogram, dtype=np.float64)
    low, high = [int(np.searchsorted(cumulative, cumulative[-1] * percentile / 100))
                 for percentile in (_PARAMETERS["low_percentile"], _PARAMETERS["high_percentile"])]
    spread = high - low
    status, reason = (("skipped", "insufficient_dynamic_range") if spread < 16
                      else ("skipped", "already_high_contrast") if spread >= 240
                      else ("applied", "stretch_applied"))
    return {"status": status, "reason": reason, "low_level": low, "high_level": high}


def preprocess_image(image, *, mode: str, max_pixels: int, max_side: int) -> tuple:
    """Return a new contiguous BGR uint8 image and explicit processing metadata.

    Deskew uses a <=1000-pixel thumbnail, connected text-like components, and
    a horizontal projection search from -5 to +5 degrees in 0.25-degree steps.
    Blank, noisy, ambiguous, nearly horizontal, and boundary results are skipped.
    Rotation uses an expanded white canvas. Contrast uses source-image histogram
    quantiles so any added white border cannot distort its measured levels.
    """
    mode = validate_mode(mode)
    max_side = _integer(max_side, "side limit", 1, 12_000)
    max_pixels = _integer(max_pixels, "pixel limit", 1, 100_000_000)
    import numpy as np

    if (not isinstance(image, np.ndarray) or image.dtype != np.uint8
            or image.ndim != 3 or image.shape[2] != 3):
        raise ValueError("preprocessing requires a three-channel BGR uint8 image")
    height, width = image.shape[:2]
    _integer(width, "input width", 1, max_side)
    _integer(height, "input height", 1, max_side)
    if width * height > max_pixels:
        raise ValueError("preprocessing input exceeds pixel limit")
    import cv2

    deskew = {"status": "disabled", "reason": "mode_disabled", "angle_degrees": 0.0,
              "estimated_angle_degrees": None, "gain": None}
    contrast = {"status": "disabled", "reason": "mode_disabled", "low_level": None, "high_level": None}
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if mode != "none" else None
    if mode in ("deskew", "deskew-contrast"):
        deskew = _estimate_deskew(gray, cv2, np)
    if mode in ("contrast", "deskew-contrast"):
        contrast = _contrast_levels(gray, cv2, np)
    out_width, out_height, forward, inverse = _rotation_geometry(width, height, deskew["angle_degrees"])
    if out_width > max_side or out_height > max_side or out_width * out_height > max_pixels:
        deskew.update(status="skipped", reason="expanded_raster_limit", angle_degrees=0.0)
        out_width, out_height, forward, inverse = _rotation_geometry(width, height, 0.0)
    if deskew["status"] == "applied":
        processed = cv2.warpAffine(
            image, np.asarray(forward, dtype=np.float64), (out_width, out_height),
            flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255))
    else:
        processed = image.copy(order="C")
    if contrast["status"] == "applied":
        low, high = contrast["low_level"], contrast["high_level"]
        table = np.clip(np.rint((np.arange(256, dtype=np.float64) - low) * 255 / (high - low)),
                        0, 255).astype(np.uint8)
        stretched = cv2.LUT(cv2.cvtColor(processed, cv2.COLOR_BGR2GRAY), table)
        processed = cv2.cvtColor(stretched, cv2.COLOR_GRAY2BGR)
    metadata = {
        "schema_version": 1, "algorithm": _ALGORITHM, "mode": mode,
        "original_raster": {"width": width, "height": height},
        "processed_raster": {"width": out_width, "height": out_height},
        "source_to_processed": forward, "processed_to_source": inverse,
        "deskew": deskew, "contrast": contrast, "parameters": dict(_PARAMETERS),
        "libraries": {"opencv": cv2.__version__, "numpy": np.__version__},
    }
    return processed, validate_metadata(metadata, width=out_width, height=out_height,
                                        max_side=max_side, max_pixels=max_pixels)
