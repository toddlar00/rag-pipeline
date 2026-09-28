"""Strict opt-in hard-scan recipes and analytically reversible edge geometry.

The bow model is a single vertical quadratic shear, not general page dewarping.
Pixel geometry is reversible; resampling and luminance changes are not lossless.
No image or model dependencies are imported by these validators.
"""

from __future__ import annotations

import copy
import math
import re

from ocr_regions import MAX_PIXELS, MAX_REGIONS, MAX_SIDE, MAX_TOTAL_PIXELS, validate_region_plan


COORDINATES = "original_page_display_fraction"
ALGORITHM = "quarter-turn-bow-illumination-v1"
PARAMETERS = {
    "max_bow_fraction": 0.025, "max_bow_slope": 0.25,
    "mapping_error_source_pixels": 0.5, "max_edge_segments": 32,
    "thumbnail_max_side": 768, "background_kernel": 31,
    "background_min_level": 32, "background_min_range": 16,
    "max_gain": 2.0, "strip_rows": 128,
}
MAX_LINES_PER_REGION = 1000
MAX_VERTICES_PER_REGION = 20_000
MAX_TOTAL_VERTICES = 100_000
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+!\-]{0,63}")


def fields(value: object, expected: set[str]) -> dict:
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("invalid hard-scan fields")
    return value


def number(value: object) -> float:
    if type(value) not in (int, float):
        raise ValueError("hard-scan numbers must be finite")
    try:
        result = float(value)
    except (ValueError, OverflowError):
        raise ValueError("hard-scan numbers must be finite") from None
    if not math.isfinite(result):
        raise ValueError("hard-scan numbers must be finite")
    return result


def integer(value: object, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError("hard-scan integer exceeds bounds")
    return value


def validate_recipe(payload: object) -> dict:
    recipe = fields(payload, {"orientation_clockwise", "illumination", "bow_fraction", "bow_assumption"})
    angle = integer(recipe["orientation_clockwise"], 0, 270)
    if angle not in (0, 90, 180, 270):
        raise ValueError("hard-scan orientation must be an explicit quarter turn")
    if recipe["illumination"] not in ("none", "background-normalize-v1"):
        raise ValueError("unsupported hard-scan illumination recipe")
    bow = number(recipe["bow_fraction"])
    if abs(bow) > PARAMETERS["max_bow_fraction"]:
        raise ValueError("hard-scan bow exceeds limit")
    assumption = "parallel_horizontal_baselines" if bow else "none"
    if recipe["bow_assumption"] != assumption:
        raise ValueError("bow correction requires an explicit applicability assumption")
    return {"orientation_clockwise": angle, "illumination": recipe["illumination"],
            "bow_fraction": bow, "bow_assumption": assumption}


def validate_plan(payload: object, *, source_sha256: str, recovery_sha256: str, page_count: int) -> dict:
    plan = fields(payload, {"schema_version", "kind", "source_sha256", "recovery_sha256",
                            "coordinate_system", "approval", "regions"})
    if (type(plan["schema_version"]) is not int or plan["schema_version"] != 1
            or plan["kind"] != "ocr_hardscan_plan" or plan["approval"] != "operator_approved"):
        raise ValueError("hard-scan plan requires explicit operator approval")
    regions = plan["regions"]
    if not isinstance(regions, list) or not 1 <= len(regions) <= MAX_REGIONS:
        raise ValueError("hard-scan plan requires 1..20 regions")
    recipes, plain = {}, []
    for region in regions:
        fields(region, {"region_id", "page_number", "bbox", "recipe"})
        plain.append({key: region[key] for key in ("region_id", "page_number", "bbox")})
    normalized = validate_region_plan({
        "schema_version": 1, "source_sha256": plan["source_sha256"],
        "recovery_sha256": plan["recovery_sha256"], "coordinate_system": plan["coordinate_system"],
        "regions": plain,
    }, source_sha256=source_sha256, recovery_sha256=recovery_sha256, page_count=page_count)
    for region in regions:
        recipes[region["region_id"]] = validate_recipe(region["recipe"])
    normalized.update(kind="ocr_hardscan_plan", approval="operator_approved")
    for region in normalized["regions"]:
        region["recipe"] = recipes[region["region_id"]]
    return normalized


def describe_transform(width: int, height: int, recipe: object, *,
                       max_side: int = MAX_SIDE, max_pixels: int = MAX_PIXELS) -> dict:
    max_side = integer(max_side, 1, MAX_SIDE)
    max_pixels = integer(max_pixels, 1, MAX_PIXELS)
    width, height = integer(width, 1, max_side), integer(height, 1, max_side)
    if width * height > max_pixels:
        raise ValueError("hard-scan source raster exceeds budget")
    recipe = validate_recipe(recipe)
    angle = recipe["orientation_clockwise"]
    maps = {
        0: ([[1., 0., 0.], [0., 1., 0.]], [[1., 0., 0.], [0., 1., 0.]]),
        90: ([[0., -1., float(height)], [1., 0., 0.]], [[0., 1., 0.], [-1., 0., float(height)]]),
        180: ([[-1., 0., float(width)], [0., -1., float(height)]],
              [[-1., 0., float(width)], [0., -1., float(height)]]),
        270: ([[0., 1., 0.], [-1., 0., float(width)]], [[0., -1., float(width)], [1., 0., 0.]]),
    }
    ow, oh = (height, width) if angle in (90, 270) else (width, height)
    amplitude = recipe["bow_fraction"] * oh
    slope = 4 * abs(amplitude) / ow
    out_height = oh + math.ceil(abs(amplitude))
    reason = ("bow_below_pixel_resolution" if 0 < abs(amplitude) < 0.5
              else "bow_slope_limit" if slope > PARAMETERS["max_bow_slope"]
              else "expanded_raster_limit" if out_height > max_side or ow * out_height > max_pixels
              else None)
    return {
        "schema_version": 1, "algorithm": ALGORITHM,
        "coordinate_convention": "pixel_edges_centers_at_half",
        "original_raster": {"width": width, "height": height},
        "oriented_raster": {"width": ow, "height": oh},
        "processed_raster": {"width": ow, "height": out_height},
        "recipe": recipe, "orientation_forward": maps[angle][0], "orientation_inverse": maps[angle][1],
        "bow": {"model": "vertical_quadratic", "amplitude_pixels": amplitude,
                "translation_pixels": max(0., amplitude), "max_slope": slope},
        "eligibility": "ready" if reason is None else "abstained", "reason": reason,
        "parameters": dict(PARAMETERS),
    }


def validate_transform(payload: object, *, width: int, height: int, recipe: object) -> dict:
    expected = describe_transform(width, height, recipe)
    if not isinstance(payload, dict) or payload != expected:
        raise ValueError("hard-scan transform differs from its fixed recipe geometry")
    # Equality alone would accept booleans as numeric coordinates.
    def types(actual, target):
        if isinstance(target, dict):
            if not isinstance(actual, dict) or set(actual) != set(target):
                raise ValueError("invalid hard-scan transform fields")
            for key in target:
                types(actual[key], target[key])
        elif isinstance(target, list):
            if not isinstance(actual, list) or len(actual) != len(target):
                raise ValueError("invalid hard-scan transform shape")
            for left, right in zip(actual, target):
                types(left, right)
        elif type(target) in (int, float):
            number(actual)
            if type(target) is int:
                integer(actual, target, target)
        elif type(actual) is not type(target):
            raise ValueError("invalid hard-scan transform type")
    types(payload, expected)
    return copy.deepcopy(expected)


def source_to_processed(point: list[float], transform: dict) -> list[float]:
    x, y = (number(value) for value in point)
    forward = transform["orientation_forward"]
    x, y = [row[0] * x + row[1] * y + row[2] for row in forward]
    u = x / transform["oriented_raster"]["width"]
    bow = transform["bow"]
    return [x, y - 4 * bow["amplitude_pixels"] * u * (1-u) + bow["translation_pixels"]]


def processed_to_source(point: list[float], transform: dict) -> list[float]:
    x, y = (number(value) for value in point)
    u = x / transform["oriented_raster"]["width"]
    bow = transform["bow"]
    y += 4 * bow["amplitude_pixels"] * u * (1-u) - bow["translation_pixels"]
    return [row[0] * x + row[1] * y + row[2] for row in transform["orientation_inverse"]]


def source_polygon(box: list, transform: dict, *, source_clip: list[float] | None = None) -> list[list[float]]:
    """Map each straight OCR edge to a bounded polyline with <=0.5px error.

    Quadratic interpolation error on one edge segment is |A|*(dx/W)^2.
    Split first at exact source-boundary intersections. Each remaining interval
    has one fixed clipping regime, so interpolation retains the error bound.
    """
    if not isinstance(box, list) or len(box) != 4:
        raise ValueError("hard-scan mapping requires four OCR corners")
    out = transform["processed_raster"]
    points = []
    for point in box:
        if not isinstance(point, list) or len(point) != 2:
            raise ValueError("invalid hard-scan OCR point")
        x, y = [number(value) for value in point]
        if not 0 <= x <= out["width"] or not 0 <= y <= out["height"]:
            raise ValueError("hard-scan OCR point is outside its canvas")
        points.append((x, y))
    original = transform["original_raster"]
    clip = [0., 0., float(original["width"]), float(original["height"])]
    if source_clip is not None:
        if not isinstance(source_clip, list) or len(source_clip) != 4:
            raise ValueError("hard-scan source clip requires four boundaries")
        clip = [number(value) for value in source_clip]
        if (not 0 <= clip[0] < clip[2] <= original["width"]
                or not 0 <= clip[1] < clip[3] <= original["height"]):
            raise ValueError("hard-scan source clip exceeds original raster")
    result = []
    for index, (x0, y0) in enumerate(points):
        x1, y1 = points[(index+1) % 4]
        bound = abs(transform["bow"]["amplitude_pixels"]) * ((x1-x0)/out["width"])**2
        dx, dy = x1-x0, y1-y0
        amplitude = transform["bow"]["amplitude_pixels"]
        width = out["width"]
        y_coefficients = (-4*amplitude*(dx/width)**2,
                          dy+4*amplitude*(dx/width)*(1-2*x0/width),
                          y0+4*amplitude*(x0/width)*(1-x0/width)-transform["bow"]["translation_pixels"])
        cuts = {0., 1.}
        for row, lower, upper in zip(transform["orientation_inverse"], clip[:2], clip[2:]):
            a = row[1]*y_coefficients[0]
            b = row[0]*dx+row[1]*y_coefficients[1]
            c = row[0]*x0+row[1]*y_coefficients[2]+row[2]
            for boundary in (lower, upper):
                constant = c-boundary
                if a == 0:
                    roots = [-constant/b] if b else []
                else:
                    discriminant = b*b-4*a*constant
                    if discriminant < 0:
                        roots = []
                    else:
                        q = -.5*(b+math.copysign(math.sqrt(discriminant), b))
                        roots = [q/a, constant/q] if q else [-b/(2*a)]
                cuts.update(root for root in roots if 0 < root < 1)
        cuts = sorted(cuts)
        used = 0
        for left, right in zip(cuts, cuts[1:]):
            count = max(1, math.ceil((right-left)*math.sqrt(bound/PARAMETERS["mapping_error_source_pixels"])))
            used += count
            if used > PARAMETERS["max_edge_segments"]:
                raise ValueError("hard-scan source polygon exceeds mapping budget")
            for segment in range(count):
                fraction = left+(right-left)*segment/count
                x, y = processed_to_source([x0+dx*fraction, y0+dy*fraction], transform)
                result.append([max(clip[0], min(clip[2], x)), max(clip[1], min(clip[3], y))])
    area = sum(result[index][0]*result[(index+1) % len(result)][1]
               - result[(index+1) % len(result)][0]*result[index][1] for index in range(len(result)))
    if abs(area) <= 1e-6:
        raise ValueError("hard-scan OCR polygon has no positive-area source support")
    return result


def illumination_result(low: int, high: int, ink_fraction: float) -> tuple[str, str]:
    reason = ("blank_or_low_ink" if ink_fraction < 0.002 else
              "unreliable_background" if ink_fraction > 0.35 else
              "already_uniform_background" if high-low < PARAMETERS["background_min_range"] else
              "background_too_dark" if low < PARAMETERS["background_min_level"] else
              "excessive_gain" if high/max(low, 1) > PARAMETERS["max_gain"] else "normalization_applied")
    return ("applied" if reason == "normalization_applied" else "skipped", reason)


def validate_processing(payload: object, *, recipe: object) -> dict:
    recipe = validate_recipe(recipe)
    value = fields(payload, {"status", "reason", "illumination", "libraries"})
    libraries = fields(value["libraries"], {"opencv", "numpy"})
    if any(not isinstance(version, str) or _VERSION.fullmatch(version) is None for version in libraries.values()):
        raise ValueError("invalid hard-scan library version")
    if value["status"] == "abstained":
        if (not recipe["bow_fraction"] or value["reason"] not in ("blank_or_low_ink", "non_text_pattern")
                or value["illumination"] is not None):
            raise ValueError("invalid hard-scan image abstention")
    elif value["status"] == "completed" and value["reason"] is None:
        step = fields(value["illumination"], {"status", "reason", "background_low", "background_high", "ink_fraction"})
        if recipe["illumination"] == "none":
            if step != {"status": "disabled", "reason": "mode_disabled", "background_low": None,
                        "background_high": None, "ink_fraction": None}:
                raise ValueError("disabled illumination has active evidence")
        else:
            low = integer(step["background_low"], 0, 255)
            high = integer(step["background_high"], low, 255)
            ink = number(step["ink_fraction"])
            if not 0 <= ink <= 1 or (step["status"], step["reason"]) != illumination_result(low, high, ink):
                raise ValueError("illumination evidence contradicts its status")
    else:
        raise ValueError("invalid hard-scan processing status")
    return copy.deepcopy(value)


__all__ = ["ALGORITHM", "COORDINATES", "MAX_PIXELS", "MAX_REGIONS", "MAX_SIDE", "MAX_TOTAL_PIXELS",
           "MAX_LINES_PER_REGION", "MAX_VERTICES_PER_REGION", "MAX_TOTAL_VERTICES",
           "PARAMETERS", "describe_transform", "processed_to_source", "source_polygon", "source_to_processed",
           "validate_plan", "validate_processing", "validate_recipe", "validate_transform"]
