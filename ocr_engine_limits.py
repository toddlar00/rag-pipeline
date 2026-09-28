"""Strict shape/work guards for the approved RapidOCR allocation recipe.

This standard-library-only leaf never resizes pixels or imports an OCR/native
runtime. It bounds selected image, rectification and normalization allocations,
not model weights, decoder temporaries, all native intermediates or process RSS.
Dimensions use (width, height); image shapes use native (height, width, 3).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import math
from numbers import Real


MAX_CROP_PIXELS = 100_000_000
MAX_BOXES = 2000
BATCH_SIZE = 6
RECOGNITION_SHAPE = (3, 48, 320)
CLASSIFICATION_SHAPE = (3, 48, 192)
MAX_RECOGNITION_WIDTH = 4096


class EngineAllocationLimit(ValueError):
    """Malformed geometry or an allocation outside the reviewed bounds."""


def _integer(value, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise EngineAllocationLimit("OCR allocation integer is outside bounds")
    return value


def _number(value):
    if isinstance(value, bool) or not isinstance(value, Real):
        raise EngineAllocationLimit("OCR allocation coordinate must be finite")
    try:
        result = float(value)
    except Exception:
        raise EngineAllocationLimit("OCR allocation coordinate must be finite") from None
    if not math.isfinite(result):
        raise EngineAllocationLimit("OCR allocation coordinate must be finite")
    return result


@dataclass(frozen=True)
class EngineLimits:
    """Reader-selected raster caps; other engine-work limits are fixed."""

    max_side: int = 6000
    max_pixels: int = 25_000_000

    def __post_init__(self):
        _integer(self.max_side, 32, 12000)
        _integer(self.max_pixels, 1, 100_000_000)


def _limits(value):
    if value is None:
        return EngineLimits()
    if type(value) is not EngineLimits:
        raise EngineAllocationLimit("OCR allocation limits are invalid")
    value.__post_init__()
    return value


def bounded_sequence(value, *, maximum):
    """Detach at most maximum items, even from a lying or unsized iterator.

    No list(value), length-hint allocation, or unbounded iteration is used.
    Blocking/user-defined iterator code is not a process-deadline substitute.
    """
    _integer(maximum, 0, MAX_BOXES)
    if isinstance(value, (str, bytes, bytearray, memoryview, Mapping)):
        raise EngineAllocationLimit("OCR allocation sequence is invalid")
    try:
        iterator = iter(value)
        result = []
        for _ in range(maximum + 1):
            try:
                item = next(iterator)
            except StopIteration:
                return tuple(result)
            if len(result) == maximum:
                raise EngineAllocationLimit("OCR allocation sequence exceeds bounds")
            result.append(item)
    except EngineAllocationLimit:
        raise
    except Exception:
        raise EngineAllocationLimit("OCR allocation sequence is invalid") from None
    raise EngineAllocationLimit("OCR allocation sequence exceeds bounds")


def _dimensions(width, height, limits):
    width = _integer(width, 1, limits.max_side)
    height = _integer(height, 1, limits.max_side)
    if width * height > limits.max_pixels:
        raise EngineAllocationLimit("OCR working raster exceeds bounds")
    return width, height


def image_dimensions(shape, *, limits=None):
    """Validate an already observed HWC3 shape without inspecting its pixels."""
    limits = _limits(limits)
    values = bounded_sequence(shape, maximum=3)
    if len(values) != 3 or type(values[2]) is not int or values[2] != 3:
        raise EngineAllocationLimit("OCR allocation requires an HWC3 image")
    return _dimensions(values[1], values[0], limits)


def _resize_minimum(width, height, minimum):
    ratio = minimum / min(width, height) if min(width, height) < minimum else 1.0
    return int(round(int(width * ratio) / 32) * 32), int(round(int(height * ratio) / 32) * 32)


def detector_dimensions(width, height, *, limits=None):
    """Preflight Det's min-736 normalization of its already padded input."""
    limits = _limits(limits)
    _dimensions(width, height, limits)
    return _dimensions(*_resize_minimum(width, height, 736), limits)


def working_dimensions(width, height, *, detection, limits=None):
    """Exactly mirror admitted Global min-30, padding and Det rounding.

    The input must already satisfy the reader's max-side cap, so Global's
    max-side reduction is intentionally never admitted or silently simulated.
    Each intermediate is checked before the caller invokes native code.
    """
    limits = _limits(limits)
    if type(detection) is not bool:
        raise EngineAllocationLimit("OCR detection selection must be boolean")
    _dimensions(width, height, limits)
    if min(width, height) < 30:
        width, height = _dimensions(*_resize_minimum(width, height, 30), limits)
    result = {"global": [width, height]}
    if detection:
        if height <= 30 or width / height > 8:
            padding = int(abs(max(int(width / 8), 30) * 2 - height) / 2)
            width, height = _dimensions(width, height + 2 * padding, limits)
        result["padded"] = [width, height]
        result["detector"] = list(detector_dimensions(width, height, limits=limits))
    return result


def _quad(value, width, height):
    points = bounded_sequence(value, maximum=4)
    if len(points) != 4:
        raise EngineAllocationLimit("OCR crop requires a quadrilateral")
    result = []
    for point in points:
        point = bounded_sequence(point, maximum=2)
        if len(point) != 2:
            raise EngineAllocationLimit("OCR crop coordinate is invalid")
        x, y = map(_number, point)
        if not 0 <= x <= width or not 0 <= y <= height:
            raise EngineAllocationLimit("OCR crop lies outside its raster")
        result.append((x, y))
    turns = [((result[(i + 1) % 4][0] - result[i][0])
              * (result[(i + 2) % 4][1] - result[(i + 1) % 4][1])
              - (result[(i + 1) % 4][1] - result[i][1])
              * (result[(i + 2) % 4][0] - result[(i + 1) % 4][0])) for i in range(4)]
    if not (all(value > 0 for value in turns) or all(value < 0 for value in turns)):
        raise EngineAllocationLimit("OCR crop geometry is degenerate or nonconvex")
    return result


def crop_shapes(boxes, *, width, height, limits=None):
    """Bound warp allocations before rectification, without changing boxes.

    The approved detector supplies float32 coordinates. Eight float32 epsilons
    conservatively cover subtraction/norm rounding before upstream int truncation
    (float64 is also covered). This is an upper bound, not a predicted exact
    shape. The runtime must reject unsupported box dtypes and validate actual
    crops afterward. Rectification may rotate its output by 90 degrees; that
    swaps dimensions but preserves these side/pixel allocation bounds.
    """
    limits = _limits(limits)
    _dimensions(width, height, limits)
    rows = bounded_sequence(boxes, maximum=MAX_BOXES)
    shapes, total = [], 0
    for row in rows:
        box = _quad(row, width, height)
        horizontal = max(math.dist(box[0], box[1]), math.dist(box[2], box[3]))
        vertical = max(math.dist(box[0], box[3]), math.dist(box[1], box[2]))
        if min(horizontal, vertical) < 1:
            raise EngineAllocationLimit("OCR crop is below one pixel")
        upper = [int(math.nextafter(value * (1 + 8 * 2**-23), math.inf))
                 for value in (horizontal, vertical)]
        crop_width, crop_height = _dimensions(*upper, limits)
        total += crop_width * crop_height
        if total > MAX_CROP_PIXELS:
            raise EngineAllocationLimit("OCR crop aggregate exceeds bounds")
        shapes.append((crop_width, crop_height))
    return tuple(shapes)


def validate_crop_shapes(shapes, *, role, limits=None, expected_count=None):
    """Preflight actual crop copies and every sequential normalization batch.

    Recognition uses the existing conservative ceil-width ceiling although the
    approved engine truncates its batch padding width. Batch rows describe those
    actual truncating dimensions; their aggregate is work, not simultaneous RSS.
    """
    limits = _limits(limits)
    if type(role) is not str or role not in {"crops", "classification", "recognition"}:
        raise EngineAllocationLimit("OCR allocation role is invalid")
    values = bounded_sequence(shapes, maximum=MAX_BOXES)
    if expected_count is not None and len(values) != _integer(expected_count, 0, MAX_BOXES):
        raise EngineAllocationLimit("OCR crop count changed")
    dimensions, normalized, total = [], [], 0
    for shape in values:
        width, height = image_dimensions(shape, limits=limits)
        total += width * height
        if total > MAX_CROP_PIXELS:
            raise EngineAllocationLimit("OCR crop aggregate exceeds bounds")
        if role == "recognition" and max(320, math.ceil(48 * width / height)) > MAX_RECOGNITION_WIDTH:
            raise EngineAllocationLimit("OCR recognition normalized width exceeds bounds")
        dimensions.append((width, height))
        normalized.append((height, width, 3))
    batches = []
    if role != "crops":
        ordered = sorted(dimensions, key=lambda pair: pair[0] / pair[1])
        for offset in range(0, len(ordered), BATCH_SIZE):
            group = ordered[offset:offset + BATCH_SIZE]
            width = 192 if role == "classification" else int(48 * max(320 / 48, *(w / h for w, h in group)))
            batches.append(validate_tensor_shape((len(group), 3, 48, width), role=role, limits=limits))
    return {"shapes": tuple(normalized), "pixels": total, "batches": tuple(batches),
            "tensor_elements": sum(math.prod(shape) for shape in batches)}


def validate_tensor_shape(shape, *, role, expected=None, limits=None):
    """Check an actual NCHW input before normalization/session handoff.

    Native-produced output images use image_dimensions/validate_crop_shapes.
    This does not bound arbitrary model output tensors or authenticate dtypes.
    """
    limits = _limits(limits)
    shape = bounded_sequence(shape, maximum=4)
    if len(shape) != 4 or any(type(value) is not int for value in shape):
        raise EngineAllocationLimit("OCR normalized tensor shape is invalid")
    batch, channels, height, width = shape
    if channels != 3 or type(role) is not str:
        raise EngineAllocationLimit("OCR normalized tensor shape is invalid")
    if role == "detection":
        if batch != 1 or width % 32 or height % 32:
            raise EngineAllocationLimit("OCR detector tensor shape is invalid")
        _dimensions(width, height, limits)
    elif role in {"classification", "recognition"}:
        _integer(batch, 1, BATCH_SIZE)
        if height != 48 or (width != 192 if role == "classification" else not 320 <= width <= MAX_RECOGNITION_WIDTH):
            raise EngineAllocationLimit("OCR normalized tensor exceeds bounds")
    else:
        raise EngineAllocationLimit("OCR allocation role is invalid")
    if expected is not None:
        expected = bounded_sequence(expected, maximum=4)
        if (len(expected) != 4 or any(type(value) is not int for value in expected)
                or shape != expected):
            raise EngineAllocationLimit("OCR tensor differs from preflight")
    return shape


def remapped_crop_shapes(shapes, *, ratio_h, ratio_w, limits=None):
    """Preflight build_final_output's crop resize, including rotated crops."""
    limits = _limits(limits)
    ratio_h, ratio_w = _number(ratio_h), _number(ratio_w)
    if not 0 < min(ratio_h, ratio_w) or max(ratio_h, ratio_w) > 12000:
        raise EngineAllocationLimit("OCR remap ratio is outside bounds")
    original = validate_crop_shapes(shapes, role="crops", limits=limits)
    mapped = []
    for height, width, _ in original["shapes"]:
        remapped_width, remapped_height = _dimensions(round(width * ratio_w), round(height * ratio_h), limits)
        mapped.append((remapped_height, remapped_width, 3))
    return validate_crop_shapes(mapped, role="crops", limits=limits,
                                expected_count=len(original["shapes"]))["shapes"]
