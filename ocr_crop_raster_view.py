"""Pure original-crop raster declarations and coordinate mapping.

No PDF/native/image imports or execution authority. Validation proves bounded
declaration consistency, not rendering, human inspection or a sampling inverse.
Only the future opt-in metadata protocol may apply this narrower numeric domain;
existing protocol-2/PIL preview admission must remain unchanged.
"""

from __future__ import annotations

import hashlib
import math
import struct

from ocr_crop_comparison import _bytes, validate_crop_scope


MAX_RASTER_VIEW_BYTES = 4096
MAX_PREVIEW_SIDE = 1400
MAX_PREVIEW_PIXELS = 1960000
_MIN_NATIVE = 2.0 ** -126
_COORDINATE_MIN, _COORDINATE_MAX = -2147483648.0, 2147483520.0
_PIXEL_MIN, _PIXEL_MAX = -16777216, 16777216
_PROFILES = frozenset(("fit", "dpi288", "dpi576"))
_PAGE_KEYS = frozenset(("display_rect_points", "cropbox_points", "rotation_degrees"))
_SCOPE_KEYS = frozenset(("source_sha256", "page_count", "page_number", "coordinate_system",
                         "bbox", "page_geometry", "scope_sha256"))
_VECTOR_LENGTHS = {"command_clip": 4, "command_matrix": 6, "native_clip": 4,
                   "native_matrix": 6, "projected_rect": 4}
_VIEW_KEYS = frozenset(("schema_version", "kind", "source_sha256", "scope_sha256",
    "preview_profile", "page_geometry", *_VECTOR_LENGTHS, "pixel_rect", "mode",
    "width", "height", "rgb_sha256", "view_sha256"))


def _fail():
    raise ValueError("invalid crop raster view or selection")


def _fields(value, keys):
    # Cardinality/type guards precede set allocation or arbitrary key hashing.
    if (type(value) is not dict or len(value) != len(keys)
            or any(type(key) is not str or len(key) > 64 for key in value)
            or set(value) != keys):
        _fail()


def _digest(value):
    return hashlib.sha256(_bytes(value, MAX_RASTER_VIEW_BYTES)).hexdigest()


def _sha(value):
    if (type(value) is not str or len(value) != 64
            or any(char not in "0123456789abcdef" for char in value)):
        _fail()
    return value


def _number(value, *, canonical=False):
    if (type(value) not in (int, float) or type(value) is int and value.bit_length() > 1024
            or canonical and type(value) is not float):
        _fail()
    try:
        value = float(value)
    except OverflowError:
        _fail()
    if not math.isfinite(value) or canonical and value == 0.0 and math.copysign(1.0, value) < 0:
        _fail()
    return 0.0 if value == 0.0 else value


def _vector(value, length, *, canonical=False):
    if type(value) is not list or len(value) != length:
        _fail()
    return [_number(item, canonical=canonical) for item in value]


def _rectangle(value):
    if (not value[0] < value[2] or not value[1] < value[3]
            or not all(math.isfinite(value[index + 2] - value[index]) for index in (0, 1))):
        _fail()
    return value


def _inside(value, outer):
    if not (outer[0] <= value[0] < value[2] <= outer[2]
            and outer[1] <= value[1] < value[3] <= outer[3]):
        _fail()


def _page(value):
    _fields(value, _PAGE_KEYS)
    for key in ("display_rect_points", "cropbox_points"):
        _rectangle(_vector(value[key], 4, canonical=True))
    if type(value["rotation_degrees"]) is not int or value["rotation_degrees"] not in (0, 90, 180, 270):
        _fail()


def _scope(value):
    _fields(value, _SCOPE_KEYS)
    _sha(value["source_sha256"])
    _sha(value["scope_sha256"])
    for key in ("page_count", "page_number"):
        if type(value[key]) is not int or not 1 <= value[key] <= 5000:
            _fail()
    if type(value["coordinate_system"]) is not str or value["coordinate_system"] != "original_page_display_fraction":
        _fail()
    _inside(_rectangle(_vector(value["bbox"], 4, canonical=True)), [0.0, 0.0, 1.0, 1.0])
    _page(value["page_geometry"])
    _bytes(value, MAX_RASTER_VIEW_BYTES)
    return validate_crop_scope(value, source_sha256=value["source_sha256"], page_count=value["page_count"])


def _profile(value):
    if type(value) is not str or len(value) > 6 or value not in _PROFILES:
        _fail()
    return value


def _round_scaled(numerator, denominator, shift):
    if shift >= 0:
        numerator <<= shift
    else:
        denominator <<= -shift
    quotient, remainder = divmod(numerator, denominator)
    if remainder * 2 > denominator or remainder * 2 == denominator and quotient & 1:
        quotient += 1
    return quotient


def _binary32_ratio(numerator, denominator):
    """Exact bounded rational -> IEEE binary32, nearest/ties-even; canonical zero.

    Integer rounding avoids using an ambient C rounding mode for conversion or
    tolerance addition. struct only decodes a chosen IEEE bit pattern here.
    Inputs originate solely in finite binary64/binary32 arithmetic below.
    """
    if numerator == 0:
        return 0.0
    sign = 0x80000000 if numerator < 0 else 0
    numerator = abs(numerator)
    exponent = numerator.bit_length() - denominator.bit_length()
    if ((numerator < denominator << exponent) if exponent >= 0
            else (numerator << -exponent < denominator)):
        exponent -= 1
    if exponent > 127:
        _fail()
    if exponent < -126:
        bits = _round_scaled(numerator, denominator, 149)
    else:
        significand = _round_scaled(numerator, denominator, 23 - exponent)
        if significand == 1 << 24:
            significand >>= 1
            exponent += 1
        if exponent > 127:
            _fail()
        bits = ((exponent + 127) << 23) | (significand - (1 << 23))
    if bits == 0:
        return 0.0
    return struct.unpack("!f", struct.pack("!I", sign | bits))[0]


def _binary32(value):
    return _binary32_ratio(*value.as_integer_ratio())


def _native(value):
    converted = _binary32(value)
    if value != 0.0 and (converted == 0.0 or abs(converted) < _MIN_NATIVE):
        _fail()
    return converted


def _binary32_sum(left, right):
    ln, ld = left.as_integer_ratio()
    rn, rd = right.as_integer_ratio()
    return _binary32_ratio(ln * rd + rn * ld, ld * rd)


def _expected_geometry(scope, profile):
    display, bbox = scope["page_geometry"]["display_rect_points"], scope["bbox"]
    width, height = display[2] - display[0], display[3] - display[1]
    command_clip = [_number(display[index % 2] + bbox[index] * (width if index % 2 == 0 else height))
                    for index in range(4)]
    _rectangle(command_clip)
    if any(not _COORDINATE_MIN <= value <= _COORDINATE_MAX for value in command_clip):
        _fail()
    extent = max(command_clip[2] - command_clip[0], command_clip[3] - command_clip[1])
    scale = min(2.0, 1398 / extent) if profile == "fit" else 4.0 if profile == "dpi288" else 8.0
    scale = _number(scale)
    if scale <= 0:
        _fail()
    command_matrix = [scale, 0.0, 0.0, scale, 0.0, 0.0]
    native_matrix = [_native(value) for value in command_matrix]
    native_clip = _rectangle([_native(value) for value in command_clip])
    # Two binary32 significands multiply exactly in finite binary64 here.
    projected = _rectangle([_native(value * native_matrix[0]) for value in native_clip])
    if any(not _PIXEL_MIN <= value <= _PIXEL_MAX for value in projected):
        _fail()
    tolerance = _binary32(0.001)
    pixel_rect = [(math.floor if index < 2 else math.ceil)(
        _binary32_sum(value, tolerance if index < 2 else -tolerance))
        for index, value in enumerate(projected)]
    pixel_width, pixel_height = pixel_rect[2] - pixel_rect[0], pixel_rect[3] - pixel_rect[1]
    if (not 1 <= pixel_width <= MAX_PREVIEW_SIDE or not 1 <= pixel_height <= MAX_PREVIEW_SIDE
            or pixel_width * pixel_height > MAX_PREVIEW_PIXELS):
        _fail()
    return {"command_clip": command_clip, "command_matrix": command_matrix,
            "native_clip": native_clip, "native_matrix": native_matrix,
            "projected_rect": projected, "pixel_rect": pixel_rect,
            "width": pixel_width, "height": pixel_height}


def _observed_geometry(value, *, canonical):
    observed = {key: _vector(value[key], length, canonical=canonical)
                for key, length in _VECTOR_LENGTHS.items()}
    pixel_rect = value["pixel_rect"]
    if (type(pixel_rect) is not list or len(pixel_rect) != 4
            or any(type(item) is not int or not _PIXEL_MIN <= item <= _PIXEL_MAX for item in pixel_rect)):
        _fail()
    for key in ("width", "height"):
        if type(value[key]) is not int or not 1 <= value[key] <= MAX_PREVIEW_SIDE:
            _fail()
    observed.update(pixel_rect=list(pixel_rect), width=value["width"], height=value["height"])
    return observed


def _view(scope, profile, geometry, rgb_sha256):
    result = {"schema_version": 1, "kind": "ocr_crop_raster_view",
              "source_sha256": scope["source_sha256"], "scope_sha256": scope["scope_sha256"],
              "preview_profile": profile, "page_geometry": scope["page_geometry"],
              **geometry, "mode": "RGB", "rgb_sha256": _sha(rgb_sha256)}
    result["view_sha256"] = _digest(result)
    _bytes(result, MAX_RASTER_VIEW_BYTES)
    return result


def build_raster_view(*, scope, preview_profile, command_clip, command_matrix,
                      native_clip, native_matrix, projected_rect, pixel_rect,
                      width, height, rgb_sha256):
    """Bind actual captured numeric observations; do not synthesize missing ones.

    Caller must capture all lists from one real render and separately verify
    actual page/pixmap origin, RGB mode, channels, stride, bytes and image hash.
    This function cannot prove any such work occurred. Ordinary PIL-only paths
    must not acquire this opt-in annotation-domain admission requirement.
    """
    checked_scope = _scope(scope)
    profile = _profile(preview_profile)
    observed = _observed_geometry({"command_clip": command_clip, "command_matrix": command_matrix,
        "native_clip": native_clip, "native_matrix": native_matrix, "projected_rect": projected_rect,
        "pixel_rect": pixel_rect, "width": width, "height": height}, canonical=False)
    expected = _expected_geometry(checked_scope, profile)
    if observed != expected:
        _fail()
    return _view(checked_scope, profile, expected, rgb_sha256)


def _validated(payload, scope):
    checked_scope = _scope(scope)
    _fields(payload, _VIEW_KEYS)
    if (type(payload["schema_version"]) is not int or payload["schema_version"] != 1
            or type(payload["kind"]) is not str or payload["kind"] != "ocr_crop_raster_view"
            or type(payload["mode"]) is not str or payload["mode"] != "RGB"):
        _fail()
    for key in ("source_sha256", "scope_sha256", "rgb_sha256", "view_sha256"):
        _sha(payload[key])
    if (payload["source_sha256"] != checked_scope["source_sha256"]
            or payload["scope_sha256"] != checked_scope["scope_sha256"]):
        _fail()
    _page(payload["page_geometry"])
    profile = _profile(payload["preview_profile"])
    observed = _observed_geometry(payload, canonical=True)
    expected = _expected_geometry(checked_scope, profile)
    if observed != expected:
        _fail()
    result = _view(checked_scope, profile, expected, payload["rgb_sha256"])
    if _bytes(payload, MAX_RASTER_VIEW_BYTES) != _bytes(result, MAX_RASTER_VIEW_BYTES):
        _fail()
    return result, checked_scope


def validate_raster_view(payload, *, scope):
    """Return a detached canonical declaration, not actual-render/consent proof."""
    return _validated(payload, scope)[0]


def selection_to_scope_bbox(selection, *, raster_view, scope):
    """Map raster edges through the native-parameter affine; never clamp a halo.

    Selection is exactly {kind, pixel_bbox}: kind is raster_edges with four
    numeric edge coordinates, or whole_scope with pixel_bbox null. No +0.5
    pixel-center shift, OCR transform, second page rotation or scope resizing.
    """
    _fields(selection, frozenset(("kind", "pixel_bbox")))
    if type(selection["kind"]) is not str or selection["kind"] not in ("raster_edges", "whole_scope"):
        _fail()
    view, checked_scope = _validated(raster_view, scope)
    if selection["kind"] == "whole_scope":
        if selection["pixel_bbox"] is not None:
            _fail()
        return list(checked_scope["bbox"])
    pixels = _rectangle(_vector(selection["pixel_bbox"], 4))
    _inside(pixels, [0.0, 0.0, float(view["width"]), float(view["height"])])
    display = checked_scope["page_geometry"]["display_rect_points"]
    scale, origin = view["native_matrix"][0], view["pixel_rect"]
    result = [_number(((origin[index % 2] + pixels[index]) / scale - display[index % 2])
                      / (display[index % 2 + 2] - display[index % 2]))
              for index in range(4)]
    _rectangle(result)
    _inside(result, checked_scope["bbox"])
    return result


def scope_bbox_to_raster_bbox(bbox, *, raster_view, scope):
    """Project saved canonical source fractions for an overlay; never modify them.

    The returned pixel-edge bbox may extend slightly beyond the raster due to
    native tolerance contraction. Do not clamp it or round-trip it into storage.
    This is a continuous command-affine projection, not native resampling.
    """
    saved = _rectangle(_vector(bbox, 4, canonical=True))
    view, checked_scope = _validated(raster_view, scope)
    _inside(saved, checked_scope["bbox"])
    display = checked_scope["page_geometry"]["display_rect_points"]
    scale, origin = view["native_matrix"][0], view["pixel_rect"]
    return _rectangle([_number((display[index % 2] + saved[index]
        * (display[index % 2 + 2] - display[index % 2])) * scale - origin[index % 2])
        for index in range(4)])
