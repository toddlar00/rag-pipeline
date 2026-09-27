"""Bounded internal geometry for same-call OCR disposition evidence.

No images, NumPy, PDF parser or model runtime are loaded. Callers must bind
mapping descriptors to the independently validated final report; this module
does not authenticate arbitrary geometry declarations or source pixels.
"""
from __future__ import annotations

from fractions import Fraction
import math
import re
import struct


MAX_VERTICES = 128
_MAX_NUMBER = 1_000_000_000_000
_RASTER_KEYS = {"width", "height", "pixel_sha256", "coordinate_system", "global_width",
                "global_height", "padded_width", "padded_height", "ratio_h", "ratio_w",
                "padding_top", "padding_left", "operations", "detector_box_dtype"}
_COORDINATES = "original_page_display_fraction"


class DispositionGeometryError(ValueError):
    """Invalid or unsupported bounded geometry; never includes source content."""


class _VertexLimit(DispositionGeometryError):
    pass


def _invalid():
    raise DispositionGeometryError("disposition geometry is invalid")


def _fields(value, keys):
    if type(value) is not dict or len(value) != len(keys) or set(value) != keys:
        _invalid()
    return value


def _number(value, low=-_MAX_NUMBER, high=_MAX_NUMBER):
    if type(value) not in (int, float) or not low <= value <= high:
        _invalid()
    result = float(value)
    if not math.isfinite(result):
        _invalid()
    return result


def _integer(value, low=1, high=12000):
    if type(value) is not int or not low <= value <= high:
        _invalid()
    return value


def _plain(value, *, depth=0, budget=None):
    """Bound projected metadata before any downstream validator copies it."""
    budget = [512] if budget is None else budget
    budget[0] -= 1
    if budget[0] < 0 or depth > 8:
        _invalid()
    if type(value) is dict:
        if len(value) > 32 or any(type(key) is not str or len(key) > 64 for key in value):
            _invalid()
        return {key: _plain(item, depth=depth + 1, budget=budget) for key, item in value.items()}
    if type(value) is list:
        if len(value) > 128:
            _invalid()
        return [_plain(item, depth=depth + 1, budget=budget) for item in value]
    if value is None or type(value) is bool:
        return value
    if type(value) is str:
        if len(value) > 128 or any(0xD800 <= ord(char) <= 0xDFFF for char in value):
            _invalid()
        return value
    _number(value)
    return value


def _dimensions(value, *, maximum=12000, pixels=100_000_000):
    _fields(value, {"width", "height"})
    width, height = (_integer(value[key], high=maximum) for key in ("width", "height"))
    if width * height > pixels:
        _invalid()
    return width, height


def _quad(value, width=12000, height=12000):
    if type(value) is not list or len(value) != 4:
        _invalid()
    result = []
    for point in value:
        if type(point) is not list or len(point) != 2:
            _invalid()
        result.append([_number(point[0], 0, width), _number(point[1], 0, height)])
    return result


def _f32(value):
    return struct.unpack(">f", struct.pack(">f", value))[0]


def _rounded_binary32(numerator, denominator, negative_zero=False):
    """Round an exact binary rational directly, avoiding a double-round step."""
    if not numerator:
        return -0.0 if negative_zero else 0.0
    negative = numerator < 0
    numerator = abs(numerator)
    exponent = numerator.bit_length() - denominator.bit_length()
    # Float.as_integer_ratio denominators and their products are powers of two.
    step = max(exponent - 23, -149)
    if step >= 0:
        denominator <<= step
    else:
        numerator <<= -step
    quotient, remainder = divmod(numerator, denominator)
    if 2 * remainder > denominator or (2 * remainder == denominator and quotient & 1):
        quotient += 1
    result = math.ldexp(float(quotient), step)
    if not math.isfinite(result) or result > 3.4028234663852886e38:
        _invalid()
    return -result if negative else result


def _operation(value, scalar, *, multiply, dtype):
    if dtype == "float64":
        return value * scalar if multiply else value - scalar
    scalar = _f32(scalar)  # NumPy 2.x weak Python-scalar conversion comes first.
    left, left_denominator = value.as_integer_ratio()
    right, right_denominator = scalar.as_integer_ratio()
    numerator = (left * right if multiply else left * right_denominator - right * left_denominator)
    denominator = left_denominator * right_denominator
    zero = value * scalar if multiply else value - scalar
    return _rounded_binary32(numerator, denominator, math.copysign(1.0, zero) < 0)


def replay_engine_box(*, dtype, box, raster):
    """Replay the observed complete, approved operation vector in reverse.

    Inputs are detached JSON primitives. Float32 coordinates must already be
    exactly represented in that dtype; this is validation, not a lossy cast.
    Clipped degeneracy is retained and handled separately by physical mapping.
    """
    if type(dtype) is not str or dtype not in ("float32", "float64"):
        _invalid()
    _fields(raster, _RASTER_KEYS)
    dimensions = {}
    for prefix in ("", "global_", "padded_"):
        dimensions[prefix] = _dimensions({"width": raster[prefix + "width"], "height": raster[prefix + "height"]})
    width, height = dimensions[""]
    gw, gh = dimensions["global_"]
    pw, ph = dimensions["padded_"]
    rh, rw = _number(raster["ratio_h"], 0, 12000), _number(raster["ratio_w"], 0, 12000)
    top, left = _integer(raster["padding_top"], 0), _integer(raster["padding_left"], 0, 0)
    if (raster["coordinate_system"] != "engine_input_bgr_uint8_pixels"
            or raster["detector_box_dtype"] != dtype or rh != height / gh or rw != width / gw
            or pw != gw or ph != gh + 2 * top
            or type(raster["pixel_sha256"]) is not str
            or re.fullmatch(r"[0-9a-f]{64}", raster["pixel_sha256"]) is None):
        _invalid()
    operations = raster["operations"]
    if type(operations) is not list or len(operations) != 2:
        _invalid()
    preprocess = _fields(operations[0], {"kind", "ratio_h", "ratio_w"})
    padding = _fields(operations[1], {"kind", "top", "left"})
    if (preprocess["kind"] != "preprocess" or padding["kind"] != "padding_1"
            or _number(preprocess["ratio_h"], 0, 12000) != rh
            or _number(preprocess["ratio_w"], 0, 12000) != rw
            or _integer(padding["top"], 0) != top or _integer(padding["left"], 0, 0) != left):
        _invalid()
    points = _quad(box, pw, ph)
    if dtype == "float32" and any(_f32(value) != value for point in points for value in point):
        _invalid()
    # Iterate the observed vector. Never reconstruct op order from sorted keys.
    for operation in reversed(operations):
        multiply = operation["kind"] == "preprocess"
        scalars = (rw, rh) if multiply else (float(left), float(top))
        points = [[_operation(value, scalars[axis], multiply=multiply, dtype=dtype)
                   for axis, value in enumerate(point)] for point in points]
    # Match np.where's strict comparisons, including retaining negative zero.
    return [[0.0 if value < 0 else float(bound) if value > bound else value
             for value, bound in zip(point, (width, height))] for point in points]


def _rectangle(value):
    if type(value) is not list or len(value) != 4:
        _invalid()
    result = [_number(item) for item in value]
    if result[0] >= result[2] or result[1] >= result[3]:
        _invalid()
    return result


def _region_geometry(value):
    """Check the bounded report projection; original plan binding is external."""
    _fields(value, {"page", "raster", "pixel_bounds", "crop_to_page_fraction", "boundary_policy"})
    page = _fields(value["page"], {"display_rect_points", "cropbox_points", "rotation_degrees"})
    rect, cropbox = (_rectangle(page[key]) for key in ("display_rect_points", "cropbox_points"))
    rotation = _integer(page["rotation_degrees"], 0, 270)
    if rotation not in (0, 90, 180, 270):
        _invalid()
    page_width, page_height = rect[2] - rect[0], rect[3] - rect[1]
    crop_width, crop_height = cropbox[2] - cropbox[0], cropbox[3] - cropbox[1]
    if rotation in (90, 270):
        crop_width, crop_height = crop_height, crop_width
    if not (math.isclose(page_width, crop_width, rel_tol=1e-6, abs_tol=1e-6)
            and math.isclose(page_height, crop_height, rel_tol=1e-6, abs_tol=1e-6)):
        _invalid()
    raster = _fields(value["raster"], {"width", "height", "dpi", "coordinate_system"})
    width, height = _dimensions({key: raster[key] for key in ("width", "height")}, maximum=6000, pixels=25_000_000)
    dpi = _integer(raster["dpi"], 300, 400)
    if dpi not in (300, 400) or raster["coordinate_system"] != "rendered_image_pixels":
        _invalid()
    pixels = value["pixel_bounds"]
    if type(pixels) is not list or len(pixels) != 4:
        _invalid()
    pixels = [_integer(item, -_MAX_NUMBER, _MAX_NUMBER) for item in pixels]
    if pixels[2] - pixels[0] != width or pixels[3] - pixels[1] != height:
        _invalid()
    scale = dpi / 72
    expected = [[1 / (scale * page_width), 0.0, (pixels[0] / scale - rect[0]) / page_width],
                [0.0, 1 / (scale * page_height), (pixels[1] / scale - rect[1]) / page_height]]
    matrix = value["crop_to_page_fraction"]
    if type(matrix) is not list or len(matrix) != 2:
        _invalid()
    for row, expected_row in zip(matrix, expected):
        if type(row) is not list or len(row) != 3 or any(
                not math.isclose(_number(item), target, rel_tol=1e-12, abs_tol=1e-12)
                for item, target in zip(row, expected_row)):
            _invalid()
    if value["boundary_policy"] != "clip_to_page_bounds":
        _invalid()
    return width, height, matrix


def _physical(reason, polygon=None, *, nonlinear=False):
    return {"state": "unavailable" if reason else "available", "reason": reason, "polygon": polygon,
            "coordinate_system": _COORDINATES,
            "edge_model": None if reason else "sampled_nonlinear_inverse" if nonlinear else "linear",
            "max_error_source_pixels": None if reason else 0.5 if nonlinear else 0.0}


def _append(points, point):
    if len(points) >= MAX_VERTICES:
        raise _VertexLimit("disposition polygon exceeds vertex limit")
    points.append(point)


def _clean(points):
    result = []
    for point in points:
        if not result or result[-1] != point:
            _append(result, point)
    if len(result) > 1 and result[-1] == result[0]:
        result.pop()
    return result


def _positive(points):
    if len(points) < 3:
        return False
    x, y = points[0]
    terms = [(points[index][0] - x) * (points[index + 1][1] - y)
             - (points[index + 1][0] - x) * (points[index][1] - y)
             for index in range(1, len(points) - 1)]
    return abs(sum(terms) if any(isinstance(value, Fraction) for value in terms) else math.fsum(terms)) > 0


def _convex(points):
    points = [[Fraction(value) for value in point] for point in points]
    signs = set()
    for index in range(4):
        a, b, c = points[index], points[(index + 1) % 4], points[(index + 2) % 4]
        cross = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
        if cross:
            signs.add(cross > 0)
    if len(signs) > 1:
        _invalid()


def _intersection(points, width=1.0, height=1.0):
    """Exact bounded affine intersection, not independent vertex clamping.

    Fractions avoid inventing support by rounding a tangent across a boundary.
    Inputs have already passed finite/size bounds; a quad clipped by four
    half-planes has bounded intermediate cardinality and arithmetic depth.
    """
    points = [[Fraction(value) for value in point] for point in points]
    width, height = Fraction(width), Fraction(height)
    points = _clean(points)
    for axis, boundary, greater in ((0, Fraction(0), True), (0, width, False),
                                    (1, Fraction(0), True), (1, height, False)):
        if not points:
            break
        output = []
        previous = points[-1]
        previous_inside = previous[axis] >= boundary if greater else previous[axis] <= boundary
        for current in points:
            inside = current[axis] >= boundary if greater else current[axis] <= boundary
            if inside != previous_inside:
                fraction = (boundary - previous[axis]) / (current[axis] - previous[axis])
                point = [previous[other] + fraction * (current[other] - previous[other]) for other in (0, 1)]
                point[axis] = boundary
                _append(output, point)
            if inside:
                _append(output, list(current))
            previous, previous_inside = current, inside
        points = _clean(output)
    return points


def _affine(points, matrix):
    coefficients = [[Fraction(value) for value in row] for row in matrix]
    result = [[row[0] * Fraction(x) + row[1] * Fraction(y) + row[2] for row in coefficients] for x, y in points]
    if any(abs(value) > _MAX_NUMBER for point in result for value in point):
        _invalid()
    return result


def map_source_polygon(*, engine_box, mapping):
    """Map a validated report projection; absence/support refusal is explicit.

    Malformed inputs raise a static ValueError for the enclosing builder to
    isolate. The submitted-payload validator must reject such mismatches, not
    repair them to unavailable. Aggregate vertex budgets belong to the leaf.
    """
    if engine_box is None:
        return _physical("engine_mapping_unobserved")
    points = _quad(engine_box)
    _convex(points)
    if mapping is None:
        return _physical("report_geometry_unavailable")
    mapping = _plain(mapping)
    try:
        kind = mapping.get("kind") if type(mapping) is dict else None
        if kind == "page":
            _fields(mapping, {"kind", "raster"})
            width, height = _dimensions(mapping["raster"])
            points = _quad(points, width, height)
            polygon = _intersection([[Fraction(x) / width, Fraction(y) / height] for x, y in points])
        elif kind == "preprocessed_page":
            from ocr_preprocessing import validate_metadata

            _fields(mapping, {"kind", "metadata"})
            metadata = mapping["metadata"]
            width, height = _dimensions(metadata["processed_raster"])
            metadata = validate_metadata(metadata, width=width, height=height)
            points = _quad(points, width, height)
            original_width, original_height = _dimensions(metadata["original_raster"])
            polygon = _intersection(_affine(points, metadata["processed_to_source"]), original_width, original_height)
            polygon = [[x / original_width, y / original_height] for x, y in polygon]
        elif kind in ("region", "hardscan"):
            _fields(mapping, {"kind", "geometry"} | ({"transform"} if kind == "hardscan" else set()))
            width, height, matrix = _region_geometry(mapping["geometry"])
            if kind == "region":
                polygon = _intersection(_affine(_quad(points, width, height), matrix))
            else:
                import ocr_hardscan as hardscan

                transform = mapping["transform"]
                transform = hardscan.validate_transform(transform, width=width, height=height, recipe=transform["recipe"])
                if transform["eligibility"] != "ready":
                    return _physical("mapping_failed")
                points = _quad(points, *(_integer(transform["processed_raster"][key]) for key in ("width", "height")))
                # Identical clip/matrix contract to hardscan IO _source_polygons;
                # the nonlinear helper's sampled/clipped semantics stay intact.
                clip = [max(0., -matrix[0][2] / matrix[0][0]), max(0., -matrix[1][2] / matrix[1][1]),
                        min(width, (1 - matrix[0][2]) / matrix[0][0]),
                        min(height, (1 - matrix[1][2]) / matrix[1][1])]
                if clip[0] >= clip[2] or clip[1] >= clip[3]:
                    return _physical("no_positive_source_support")
                try:
                    polygon = hardscan.source_polygon(points, transform, source_clip=clip)
                except ValueError as error:
                    if str(error) == "hard-scan source polygon exceeds mapping budget":
                        return _physical("mapping_limit")
                    if str(error) == "hard-scan OCR polygon has no positive-area source support":
                        return _physical("no_positive_source_support")
                    raise
                if type(polygon) is not list:
                    _invalid()
                if len(polygon) > MAX_VERTICES:
                    raise _VertexLimit("disposition polygon exceeds vertex limit")
                if len(polygon) < 3:
                    _invalid()
                checked = []
                for point in polygon:
                    if type(point) is not list or len(point) != 2:
                        _invalid()
                    checked.append([_number(point[0], clip[0], clip[2]), _number(point[1], clip[1], clip[3])])
                polygon = [[max(0., min(1., row[0] * x + row[1] * y + row[2])) for row in matrix]
                           for x, y in checked]
        else:
            _invalid()
        if not _positive(polygon):
            return _physical("no_positive_source_support")
        if kind != "hardscan":
            polygon = [[float(value) for value in point] for point in polygon]
            if not _positive(polygon):
                return _physical("no_positive_source_support")
        return _physical(None, polygon, nonlinear=kind == "hardscan")
    except _VertexLimit:
        return _physical("mapping_limit")
    except (ValueError, KeyError, TypeError, OverflowError, ZeroDivisionError):
        raise DispositionGeometryError("disposition geometry is invalid") from None
