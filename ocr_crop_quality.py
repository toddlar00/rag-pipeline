"""Bounded measurements of supplied original-crop RGB, never text/quality proof.

No rendering, OCR, image-library imports, padding, storage or review authority.
The host must supply its admitted annotation-on image, not browser declarations.
Pixel centers below define this measurement only, not annotation coordinates or
an inverse of native raster sampling. A zero count never means a safe/clear edge.
"""

from __future__ import annotations

import hashlib
import json
import math

from ocr_crop_comparison import _bytes
from ocr_crop_raster_view import (
    MAX_PREVIEW_PIXELS, MAX_PREVIEW_SIDE, MAX_RASTER_VIEW_BYTES,
    scope_bbox_to_raster_bbox, validate_raster_view,
)


MAX_OBSERVATION_BYTES = 16 * 1024
MAX_ADJACENT_PAIR_VISITS = 2 * MAX_PREVIEW_PIXELS
MAX_EDGE_SAMPLE_VISITS = 4 * MAX_PREVIEW_PIXELS
MAX_SAMPLE_VISITS = 5 * MAX_PREVIEW_PIXELS
EDGE_BAND_PIXELS = 2
_FAILURE = "invalid crop pixel advisory input or binding"
_DOMAIN = b"rag-pipeline:ocr-crop-quality-observation:v1\0"


def _fail():
    raise ValueError(_FAILURE)


def _admit(rgb, scope, raster_view):
    if type(rgb) is not bytes or type(scope) is not dict or type(raster_view) is not dict:
        _fail()
    # Fixed top-level cardinality is checked before serialization/hashing. The
    # existing validator then checks every exact nested schema and numeric bound.
    if len(scope) != 7 or len(raster_view) != 17:
        _fail()
    try:
        detached_scope = json.loads(_bytes(scope, MAX_RASTER_VIEW_BYTES))
        detached_view = json.loads(_bytes(raster_view, MAX_RASTER_VIEW_BYTES))
        view = validate_raster_view(detached_view, scope=detached_scope)
        requested = scope_bbox_to_raster_bbox(
            detached_scope["bbox"], scope=detached_scope, raster_view=view)
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
        raise ValueError(_FAILURE) from None
    width, height = view["width"], view["height"]
    if (not 1 <= width <= MAX_PREVIEW_SIDE or not 1 <= height <= MAX_PREVIEW_SIDE
            or width * height > MAX_PREVIEW_PIXELS or len(rgb) != width * height * 3):
        _fail()
    if hashlib.sha256(rgb).hexdigest() != view["rgb_sha256"]:
        _fail()
    return detached_scope, view, requested


def _pixel_box(box):
    # k + 0.5 in [lo, hi): ceil(lo - .5) <= k < ceil(hi - .5).
    return [math.ceil(value - 0.5) for value in box]


def _bands(requested, width, height):
    left, top, right, bottom = requested
    bands = (
        ("left", [left, top, min(right, left + EDGE_BAND_PIXELS), bottom]),
        ("top", [left, top, right, min(bottom, top + EDGE_BAND_PIXELS)]),
        ("right", [max(left, right - EDGE_BAND_PIXELS), top, right, bottom]),
        ("bottom", [left, max(top, bottom - EDGE_BAND_PIXELS), right, bottom]),
    )
    result = []
    for name, box in bands:
        ideal = _pixel_box(box)
        available = [min(width if index % 2 == 0 else height, max(0, value))
                     for index, value in enumerate(ideal)]
        ideal_count = (ideal[2] - ideal[0]) * (ideal[3] - ideal[1])
        count = (available[2] - available[0]) * (available[3] - available[1])
        # Continuous geometric loss is independent of missing pixel centers.
        missing_geometry = box[0] < 0 or box[1] < 0 or box[2] > width or box[3] > height
        result.append({"edge": name, "nominal_band_bbox": box,
            "available_pixel_box": available, "ideal_sample_count": ideal_count,
            "available_sample_count": count, "missing_sample_count": ideal_count - count,
            "geometric_missing_coverage": missing_geometry})
    visits = sum(edge["available_sample_count"] for edge in result)
    if visits > MAX_EDGE_SAMPLE_VISITS or visits + width * height > MAX_SAMPLE_VISITS:
        _fail()
    return result


def _percentile(histogram, count, percent):
    # Nearest rank: first level whose cumulative count reaches ceil(p*N/100).
    target, cumulative = (count * percent + 99) // 100, 0
    for level, frequency in enumerate(histogram):
        cumulative += frequency
        if cumulative >= target:
            return level
    raise AssertionError("crop histogram accounting differs")


def _measure(rgb, width, height):
    count = width * height
    gray = bytearray(count)  # The only auxiliary per-pixel allocation: one byte.
    histogram = [0] * 256
    horizontal_sum = vertical_sum = 0
    for y in range(height):
        row = y * width
        for x in range(width):
            index = row + x
            offset = index * 3
            level = (77 * rgb[offset] + 150 * rgb[offset + 1] + 29 * rgb[offset + 2] + 128) >> 8
            gray[index] = level
            histogram[level] += 1
            if x:
                horizontal_sum += abs(level - gray[index - 1])
            if y:
                vertical_sum += abs(level - gray[index - width])
    occupied = [level for level, frequency in enumerate(histogram) if frequency]
    return gray, {"sample_count": count, "histogram": histogram,
        "min": occupied[0], "max": occupied[-1],
        "p05": _percentile(histogram, count, 5), "p95": _percentile(histogram, count, 95),
        "horizontal_gradient": {"sum": horizontal_sum, "count": height * (width - 1)},
        "vertical_gradient": {"sum": vertical_sum, "count": width * (height - 1)}}


def _edge_measurement(edge, gray, width, low, high, *, narrow):
    count = edge["available_sample_count"]
    reasons = []
    if not count:
        reasons.append("no_available_pixel_centers")
    if edge["geometric_missing_coverage"]:
        reasons.append("partial_geometric_coverage")
    if narrow:
        reasons.append("crop_narrower_than_two_edge_bands")
    minimum, maximum, low_count, high_count = 255, 0, 0, 0
    left, top, right, bottom = edge["available_pixel_box"]
    for y in range(top, bottom):
        for x in range(left, right):
            level = gray[y * width + x]
            minimum, maximum = min(minimum, level), max(maximum, level)
            low_count += 4 * (level - low) <= high - low
            high_count += 4 * (high - level) <= high - low
    if count and minimum == maximum:
        reasons.append("uniform_band")
    if low == high:
        reasons.append("uniform_raster_no_polarity_range")
    status = ("unavailable" if not count else "partial" if edge["geometric_missing_coverage"]
              else "ambiguous" if reasons else "observed")
    return {**edge, "status": status, "reasons": reasons,
        "min": minimum if count else None, "max": maximum if count else None,
        "low_extreme_count": low_count if count and low != high else None,
        "high_extreme_count": high_count if count and low != high else None}


def inspect_crop_pixels(rgb: bytes, *, scope, raster_view) -> dict:
    """Inspect exact admitted RGB bytes; return measurements, never a quality score.

    Invalid bindings raise one static ValueError before grayscale allocation or
    measurement. Expected empty-band/uniform/small-image ambiguity is retained.
    BaseException, allocation failure and unexpected implementation errors are
    not swallowed. A self-consistent caller declaration is not host/render proof.
    """
    checked_scope, view, requested = _admit(rgb, scope, raster_view)
    width, height = view["width"], view["height"]
    bands = _bands(requested, width, height)
    adjacent_visits = height * (width - 1) + width * (height - 1)
    if adjacent_visits > MAX_ADJACENT_PAIR_VISITS:
        _fail()
    gray, measurements = _measure(rgb, width, height)
    narrow = requested[2] - requested[0] < 4 or requested[3] - requested[1] < 4
    edges = [_edge_measurement(edge, gray, width, measurements["min"], measurements["max"],
                              narrow=narrow) for edge in bands]
    del gray
    reasons = []
    if measurements["min"] == measurements["max"]:
        reasons.append("uniform_raster")
    elif measurements["p05"] == measurements["p95"]:
        reasons.append("percentile_range_collapsed")
    if narrow:
        reasons.append("crop_narrower_than_two_edge_bands")
    if width == 1 or height == 1:
        reasons.append("adjacent_pairs_unavailable_on_an_axis")
    if any(edge["geometric_missing_coverage"] for edge in edges):
        reasons.append("partial_edge_geometry")
    if any(edge["available_sample_count"] == 0 for edge in edges):
        reasons.append("empty_edge_samples")
    advice = ["inspect_annotation_on_rendered_appearance", "acquisition_resolution_unknown",
              "measurements_are_not_text_or_accuracy", "compare_only_declared_render_profiles"]
    if reasons:
        advice.append("measurement_ambiguity_requires_visual_review")
    if any(edge["low_extreme_count"] or edge["high_extreme_count"] for edge in edges):
        advice.append("edge_extremes_require_visual_review_not_clipping_proof")
    edge_visits = sum(edge["available_sample_count"] for edge in edges)
    result = {"schema_version": 1, "kind": "ocr_crop_quality_observation",
        "recipe": {"name": "crop-pixel-measurements-v1", "grayscale": "(77R+150G+29B+128)>>8",
            "global_sampling": "entire_admitted_raster", "histogram_bins": 256,
            "percentiles": "nearest_rank_ceil_percent_times_count_over_100",
            "gradients": "sum_absolute_adjacent_difference_no_wrap_no_normalization",
            "edge_band_pixels": EDGE_BAND_PIXELS, "edge_direction": "inward_clipped_to_requested_scope",
            "edge_sampling": "pixel_centers_half_open_low_inclusive_high_exclusive",
            "corner_accounting": "independent_per_edge",
            "extremes": "4*(level-global_min)<=range;4*(global_max-level)<=range",
            "annotation_policy": "annots_on_rendered_appearance_only",
            "acquisition_resolution": "unknown"},
        "binding": {key: view[key] for key in ("source_sha256", "scope_sha256", "view_sha256",
            "rgb_sha256", "preview_profile", "width", "height")},
        "requested_pixel_bbox": requested, "status": "ambiguous" if reasons else "available",
        "reasons": reasons, "measurements": measurements, "edges": edges, "advice_codes": advice,
        "work": {"grayscale_samples": width * height, "edge_sample_visits": edge_visits,
            "sample_visits": width * height + edge_visits, "auxiliary_gray_bytes": width * height,
            "adjacent_pair_visits": adjacent_visits, "max_adjacent_pair_visits": MAX_ADJACENT_PAIR_VISITS,
            "max_pixels": MAX_PREVIEW_PIXELS, "max_side": MAX_PREVIEW_SIDE,
            "max_sample_visits": MAX_SAMPLE_VISITS, "max_edge_sample_visits": MAX_EDGE_SAMPLE_VISITS,
            "max_observation_bytes": MAX_OBSERVATION_BYTES}}
    result["binding"]["page_number"] = checked_scope["page_number"]
    result["observation_sha256"] = hashlib.sha256(_DOMAIN + _bytes(result, MAX_OBSERVATION_BYTES)).hexdigest()
    # Exact final encoded cap includes the digest; round-trip returns no mutable
    # input aliases and admits only bounded JSON scalars/lists/objects.
    return json.loads(_bytes(result, MAX_OBSERVATION_BYTES))
