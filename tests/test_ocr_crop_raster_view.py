"""Pure controls for ``ocr_crop_raster_view`` declarations and mapping.

Pure declarations/numeric objects only: no PDF, PIL, native renderer or private
input. Explicit native observations below were copied from the retained numeric
report SHA c05ceffe609a4a4ff61948975d467d6ddf5d7755204ae0168783651afb46f98b
(PyMuPDF/MuPDF 1.28.2; 18 projections/69 rounding examples). These tests neither
read that report nor establish new native, image, browser or sampling evidence.
"""

from copy import deepcopy
import hashlib
import json
import math
import struct

import pytest

import ocr_crop_raster_view as raster
from ocr_crop_comparison import validate_crop_scope


VIEW_KEYS = (
    "schema_version", "kind", "source_sha256", "scope_sha256", "preview_profile",
    "page_geometry", "command_clip", "command_matrix", "native_clip", "native_matrix",
    "projected_rect", "pixel_rect", "mode", "width", "height", "rgb_sha256", "view_sha256",
)
VECTOR_SIZES = {"command_clip": 4, "command_matrix": 6, "native_clip": 4,
                "native_matrix": 6, "projected_rect": 4}


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def retag(value, key):
    value[key] = hashlib.sha256(encoded({name: item for name, item in value.items()
                                        if name != key})).hexdigest()
    return value


def scope_for(clip=(0.0, 0.0, 72.0, 72.0), *, display=(0.0, 0.0, 1024.0, 1024.0),
              rotation=0, cropbox=None):
    display = list(display)
    width, height = display[2] - display[0], display[3] - display[1]
    if cropbox is None:
        size = (height, width) if rotation in (90, 270) else (width, height)
        cropbox = [0.0, 0.0, size[0], size[1]]
    result = {"source_sha256": "a" * 64, "page_count": 1, "page_number": 1,
              "coordinate_system": "original_page_display_fraction",
              "bbox": [(clip[index] - display[index % 2])
                       / (width if index % 2 == 0 else height) for index in range(4)],
              "page_geometry": {"display_rect_points": display,
                                "cropbox_points": list(cropbox), "rotation_degrees": rotation}}
    retag(result, "scope_sha256")
    # Genuine shared validation; no geometry-validator bypass or native input.
    assert validate_crop_scope(result, source_sha256="a" * 64, page_count=1) == result
    return result


def matrix(scale):
    return [scale, 0.0, 0.0, scale, 0.0, 0.0]


def captured(scope, clip, *, profile="fit", scale=2.0, native_scale=None,
             native_clip=None, projected=None, pixels=(0, 0, 144, 144)):
    return {"scope": scope, "preview_profile": profile, "command_clip": list(clip),
            "command_matrix": matrix(scale),
            "native_clip": list(clip if native_clip is None else native_clip),
            "native_matrix": matrix(scale if native_scale is None else native_scale),
            "projected_rect": list([value * scale for value in clip] if projected is None else projected),
            "pixel_rect": list(pixels), "width": pixels[2] - pixels[0],
            "height": pixels[3] - pixels[1], "rgb_sha256": "b" * 64}


def basic():
    clip = [0.0, 0.0, 72.0, 72.0]
    scope = scope_for(clip, display=clip)
    return scope, raster.build_raster_view(**captured(scope, clip))


SHORT_CLIP = [20.125, 10.0625, 97.875, 17.0625]
SHORT_ROWS = (
    ("fit", 2.0, [40.25, 20.125, 195.75, 34.125], [40, 20, 196, 35]),
    ("dpi288", 4.0, [80.5, 40.25, 391.5, 68.25], [80, 40, 392, 69]),
    ("dpi576", 8.0, [161.0, 80.5, 783.0, 136.5], [161, 80, 783, 137]),
)
FIT_ROWS = (
    pytest.param(
        [0.1, 0.12, 1000.78, 800.83], 1.3970500059959228, 1.3970500230789185,
        [0.10000000149011612, 0.11999999731779099, 1000.780029296875, 800.8300170898438],
        [0.13970500230789185, 0.16764600574970245, 1398.1397705078125, 1118.799560546875],
        [0, 0, 1399, 1119], id="downscaled-asymmetric"),
    pytest.param(
        [0.0001, 0.9999, 699.0002, 233.333333], 1.999999713877008, 1.999999761581421,
        [9.999999747378752e-05, 0.9998999834060669, 699.0001831054688, 233.3333282470703],
        [0.00019999996584374458, 1.9997997283935547, 1398.000244140625, 466.6665954589844],
        [0, 2, 1398, 467], id="near-fit-threshold"),
)


def short_view(profile):
    row = next(row for row in SHORT_ROWS if row[0] == profile)
    scope = scope_for(SHORT_CLIP)
    return scope, raster.build_raster_view(**captured(
        scope, SHORT_CLIP, profile=row[0], scale=row[1], projected=row[2], pixels=row[3]))


def bits(value):
    # Results are already exactly representable binary32 values; this does not
    # generate any expected rounded value using the implementation under test.
    return struct.unpack("!I", struct.pack("!f", value))[0]


IEEE_VECTORS = (
    (0, 1, 0x00000000), (1, 1, 0x3F800000), (3, 2, 0x3FC00000),
    (1, 2, 0x3F000000), (2, 1, 0x40000000),
    ((1 << 26) + 3, 1 << 26, 0x3F800000),
    ((1 << 24) + 1, 1 << 24, 0x3F800000),  # Tie to even at 1.
    ((1 << 26) + 5, 1 << 26, 0x3F800001),
    ((1 << 24) + 3, 1 << 24, 0x3F800002),  # Odd lower significand loses tie.
    (1, 1 << 149, 0x00000001), (1, 1 << 150, 0x00000000),
    (3, 1 << 150, 0x00000002), (5, 1 << 150, 0x00000002),
    ((1 << 23) - 1, 1 << 149, 0x007FFFFF),
    ((1 << 24) - 1, 1 << 150, 0x00800000),  # Subnormal/normal midpoint.
    (1, 1 << 126, 0x00800000),
    ((1 << 24) - 1, 1 << 23, 0x3FFFFFFF),
    ((1 << 25) - 1, 1 << 24, 0x40000000),  # Carry into next exponent.
    (((1 << 24) - 1) << 104, 1, 0x7F7FFFFF),
    (((1 << 25) - 1) << 103, 2, 0x7F000000),
)


@pytest.mark.parametrize("sign", [1, -1], ids=["positive", "negative"])
@pytest.mark.parametrize("numerator,denominator,expected", IEEE_VECTORS,
                         ids=[f"ieee-{index}" for index in range(len(IEEE_VECTORS))])
def test_binary32_exact_rational_ieee_vectors(sign, numerator, denominator, expected):
    actual = raster._binary32_ratio(sign * numerator, denominator)
    signed = expected | 0x80000000 if sign < 0 and expected else expected
    assert bits(actual) == signed


@pytest.mark.parametrize("sign", [1, -1])
@pytest.mark.parametrize("numerator", [((1 << 25) - 1) << 103, 1 << 128, 1 << 1023],
                         ids=["overflow-midpoint", "next-exponent", "binary64-magnitude"])
def test_binary32_refuses_overflow(sign, numerator):
    with pytest.raises(ValueError):
        raster._binary32_ratio(sign * numerator, 1)


@pytest.mark.parametrize("value", [0.0, -0.0])
def test_binary32_zero_is_canonical_positive(value):
    assert bits(raster._binary32(value)) == 0


@pytest.mark.parametrize("sign", [1, -1])
@pytest.mark.parametrize("value", [2.0 ** -149, 2.0 ** -150, 2.0 ** -1074],
                         ids=["subnormal", "underflow-tie", "binary64-minimum"])
def test_native_domain_refuses_nonzero_subnormal_and_zero_underflow(sign, value):
    with pytest.raises(ValueError):
        raster._native(sign * value)


@pytest.mark.parametrize("value", [2.0 ** -126, -(2.0 ** -126), 0.0, -0.0])
def test_native_domain_admits_normal_boundary_and_zero(value):
    assert raster._native(value) == value
    if value == 0:
        assert bits(raster._native(value)) == 0


@pytest.mark.parametrize("left,right,expected", [
    (1.0, 2.0 ** -24, 0x3F800000),
    (1.0, 3.0 * 2.0 ** -24, 0x3F800002),
    (-1.0, -(2.0 ** -24), 0xBF800000),
    (2.0 ** -149, -(2.0 ** -149), 0x00000000),
])
def test_binary32_addition_rounds_once_from_exact_rational_sum(left, right, expected):
    assert bits(raster._binary32_sum(left, right)) == expected


@pytest.mark.parametrize("profile,scale,projected,pixels", SHORT_ROWS)
def test_retained_short_native_captures(profile, scale, projected, pixels):
    scope, view = short_view(profile)
    assert view["command_clip"] == SHORT_CLIP == view["native_clip"]
    assert view["command_matrix"] == matrix(scale) == view["native_matrix"]
    assert view["projected_rect"] == projected
    assert view["pixel_rect"] == pixels
    assert (view["width"], view["height"]) == (pixels[2] - pixels[0], pixels[3] - pixels[1])
    assert raster.validate_raster_view(view, scope=scope) == view


@pytest.mark.parametrize("clip,command_scale,native_scale,native_clip,projected,pixels", FIT_ROWS)
def test_retained_fit_preserves_distinct_command_and_native_values(
        clip, command_scale, native_scale, native_clip, projected, pixels):
    scope = scope_for(clip)
    view = raster.build_raster_view(**captured(
        scope, clip, scale=command_scale, native_scale=native_scale,
        native_clip=native_clip, projected=projected, pixels=pixels))
    assert view["command_matrix"] != view["native_matrix"]
    assert view["command_matrix"][0] == command_scale
    assert view["native_matrix"][0] == native_scale
    assert view["command_clip"] == clip
    assert view["native_clip"] == native_clip
    assert view["projected_rect"] == projected
    assert view["pixel_rect"] == pixels
    assert raster.validate_raster_view(view, scope=scope) == view


def test_retained_negative_offset_native_projection_without_synthetic_scope_claim():
    command = [1234.123456789, -432.987654321, 4321.000123, 2345.999123]
    native = [1234.1234130859375, -432.9876403808594, 4321.0, 2345.9990234375]
    scale = raster._binary32(0.4528849549781272)
    assert scale == 0.4528849422931671
    assert [raster._binary32(value) for value in command] == native
    assert [raster._native(value * scale) for value in native] == [
        558.9158935546875, -196.0935821533203, 1956.9158935546875, 1062.4676513671875]


@pytest.mark.parametrize("coordinates,expected", [
    ([100.0, 100.0, 200.0, 100.001], [100, 100, 200, 100]),
    ([1.001, 0.999, 3.001, 3.999], [1, 1, 3, 4]),
    ([558.9158935546875, -196.0935821533203, 1956.9158935546875, 1062.4676513671875],
     [558, -197, 1957, 1063]),
])
def test_retained_tolerance_rounding_observations(coordinates, expected):
    tolerance = raster._binary32(0.001)
    actual = [(math.floor if index < 2 else math.ceil)(raster._binary32_sum(
        raster._binary32(value), tolerance if index < 2 else -tolerance))
        for index, value in enumerate(coordinates)]
    assert actual == expected


def test_builder_and_validator_return_detached_canonical_declarations():
    scope, original = basic()
    scope_before, view_before = encoded(scope), encoded(original)
    checked = raster.validate_raster_view(original, scope=scope)
    assert set(checked) == set(VIEW_KEYS)
    assert checked == original and checked is not original
    checked["native_clip"][0] = 12.0
    checked["page_geometry"]["display_rect_points"][0] = 12.0
    assert encoded(scope) == scope_before
    assert encoded(original) == view_before
    assert len(view_before) <= 4096 == raster.MAX_RASTER_VIEW_BYTES
    assert original["view_sha256"] == hashlib.sha256(encoded(
        {name: value for name, value in original.items() if name != "view_sha256"})).hexdigest()


def test_builder_normalizes_only_capture_numbers_without_aliasing():
    scope, _ = basic()
    args = captured(scope, [0, -0.0, 72, 72], scale=2)
    expected_scope = encoded(scope)
    view = raster.build_raster_view(**args)
    for key in VECTOR_SIZES:
        assert all(type(value) is float for value in view[key])
        assert all(math.copysign(1, value) == 1 for value in view[key] if value == 0)
    args["native_clip"][0] = 9
    args["pixel_rect"][0] = 9
    assert view["native_clip"][0] == 0.0 and view["pixel_rect"][0] == 0
    assert encoded(scope) == expected_scope
    assert raster.validate_raster_view(view, scope=scope) == view


@pytest.mark.parametrize("key", VIEW_KEYS)
def test_every_metadata_key_is_required(key):
    scope, view = basic()
    del view[key]
    with pytest.raises(ValueError):
        raster.validate_raster_view(view, scope=scope)


def test_unknown_metadata_key_is_rejected_even_with_new_hash():
    scope, view = basic()
    view["extension"] = None
    retag(view, "view_sha256")
    with pytest.raises(ValueError):
        raster.validate_raster_view(view, scope=scope)


def test_unknown_key_cannot_replace_required_key_at_same_cardinality():
    scope, view = basic()
    view["extension"] = view.pop("native_matrix")
    retag(view, "view_sha256")
    with pytest.raises(ValueError):
        raster.validate_raster_view(view, scope=scope)


FIELD_MUTATIONS = (
    (("schema_version",), 2), (("kind",), "other"), (("source_sha256",), "c" * 64),
    (("scope_sha256",), "c" * 64), (("preview_profile",), "dpi288"),
    (("page_geometry", "display_rect_points", 2), 73.0),
    (("page_geometry", "cropbox_points"), [1.0, 1.0, 73.0, 73.0]),
    (("page_geometry", "rotation_degrees"), 180),
    (("command_clip", 2), 71.0), (("command_matrix", 0), 4.0),
    (("native_clip", 2), 71.0), (("native_matrix", 0), 4.0),
    (("projected_rect", 2), 143.0), (("pixel_rect", 0), 1),
    (("width",), 143), (("height",), 143), (("mode",), "L"),
    (("rgb_sha256",), "B" * 64),
)


def replace_at(value, path, replacement):
    for key in path[:-1]:
        value = value[key]
    value[path[-1]] = replacement


@pytest.mark.parametrize("path,replacement", FIELD_MUTATIONS,
                         ids=["-".join(map(str, row[0])) for row in FIELD_MUTATIONS])
def test_field_tampering_is_not_authorized_by_rehash(path, replacement):
    scope, view = basic()
    replace_at(view, path, deepcopy(replacement))
    retag(view, "view_sha256")
    with pytest.raises(ValueError):
        raster.validate_raster_view(view, scope=scope)


@pytest.mark.parametrize("key", ["source_sha256", "scope_sha256", "rgb_sha256", "view_sha256"])
@pytest.mark.parametrize("bad", [None, 123, "a" * 63, "a" * 65, "A" * 64, "g" * 64])
def test_hash_fields_are_exact_lowercase_sha256(key, bad):
    scope, view = basic()
    view[key] = bad
    with pytest.raises(ValueError):
        raster.validate_raster_view(view, scope=scope)


def test_rgb_and_self_hash_binding_does_not_claim_actual_image_authentication():
    scope, view = basic()
    view["rgb_sha256"] = "d" * 64
    with pytest.raises(ValueError):
        raster.validate_raster_view(view, scope=scope)
    # Correctly rehashed metadata is a consistent declaration only. Actual RGB
    # byte equality belongs to the renderer/worker, which this suite never calls.
    retag(view, "view_sha256")
    assert raster.validate_raster_view(view, scope=scope)["rgb_sha256"] == "d" * 64
    view["view_sha256"] = "e" * 64
    with pytest.raises(ValueError):
        raster.validate_raster_view(view, scope=scope)


class FloatTrap:
    def __float__(self):
        raise AssertionError("non-built-in numeric conversion dispatched")


class ListSubclass(list):
    pass


class DictSubclass(dict):
    pass


class StringSubclass(str):
    pass


@pytest.mark.parametrize("bad_key", [1, StringSubclass("kind"), "x" * 65])
def test_noncanonical_mapping_key_is_refused_before_set(monkeypatch, bad_key):
    scope, view = basic()
    view[bad_key] = view.pop("kind")
    builtin_set = set

    def checked_set(value):
        if value is view:
            raise AssertionError("invalid key reached set allocation")
        return builtin_set(value)

    monkeypatch.setattr(raster, "set", checked_set, raising=False)
    with pytest.raises(ValueError):
        raster.validate_raster_view(view, scope=scope)


@pytest.mark.parametrize("key", tuple(VECTOR_SIZES))
@pytest.mark.parametrize("bad", [True, 0, -0.0, float("nan"), float("inf"), -float("inf"),
                                  "0", None, FloatTrap(), 1 << 4096],
                         ids=["bool", "int", "minus-zero", "nan", "inf", "minus-inf",
                              "string", "null", "float-dispatch", "huge-int"])
def test_metadata_coordinates_require_canonical_finite_builtin_floats(key, bad):
    scope, view = basic()
    view[key][0] = bad
    with pytest.raises(ValueError):
        raster.validate_raster_view(view, scope=scope)


@pytest.mark.parametrize("key", tuple(VECTOR_SIZES))
@pytest.mark.parametrize("shape", ["tuple", "subclass", "short", "long", "null"])
def test_vector_cardinality_and_container_type_are_exact(key, shape):
    scope, view = basic()
    values = view[key]
    view[key] = {"tuple": tuple(values), "subclass": ListSubclass(values),
                 "short": values[:-1], "long": values + [0.0], "null": None}[shape]
    with pytest.raises(ValueError):
        raster.validate_raster_view(view, scope=scope)


@pytest.mark.parametrize("key", ["schema_version", "width", "height"])
@pytest.mark.parametrize("bad", [True, 1.0, "1", None, 1 << 4096],
                         ids=["bool", "float", "string", "null", "huge-int"])
def test_integer_fields_never_accept_bool_or_float(key, bad):
    scope, view = basic()
    view[key] = bad
    with pytest.raises(ValueError):
        raster.validate_raster_view(view, scope=scope)


@pytest.mark.parametrize("bad", [True, 0.0, "0", None, -16777217, 16777217, 1 << 4096],
                         ids=["bool", "float", "string", "null", "low", "high", "huge"])
def test_pixel_rect_integer_type_and_range(bad):
    scope, view = basic()
    view["pixel_rect"][0] = bad
    with pytest.raises(ValueError):
        raster.validate_raster_view(view, scope=scope)


@pytest.mark.parametrize("bad", [None, [], (), DictSubclass()])
def test_top_level_metadata_requires_exact_dict(bad):
    scope, _ = basic()
    with pytest.raises(ValueError):
        raster.validate_raster_view(bad, scope=scope)


@pytest.mark.parametrize("target", ["view", "scope", "page"])
def test_oversized_mapping_is_refused_before_set_allocation(monkeypatch, target):
    scope, view = basic()
    value = {"view": view, "scope": scope, "page": view["page_geometry"]}[target]
    value["extra"] = None
    builtin_set = set

    def bounded_set(argument):
        if argument is value:
            raise AssertionError("set allocated before cardinality refusal")
        return builtin_set(argument)

    monkeypatch.setattr(raster, "set", bounded_set, raising=False)
    with pytest.raises(ValueError):
        raster.validate_raster_view(view, scope=scope)


def test_oversized_scalar_rejects_before_metadata_encoding(monkeypatch):
    scope, view = basic()
    view["kind"] = "x" * 4097
    original = raster._bytes

    def no_large_copy(value, limit):
        if value is view:
            raise AssertionError("malformed large metadata reached encoder")
        return original(value, limit)

    monkeypatch.setattr(raster, "_bytes", no_large_copy)
    with pytest.raises(ValueError):
        raster.validate_raster_view(view, scope=scope)


def test_final_encoded_bound_is_enforced_at_exact_valid_size(monkeypatch):
    scope, view = basic()
    size = len(encoded(view))
    assert size < 4096
    monkeypatch.setattr(raster, "MAX_RASTER_VIEW_BYTES", size)
    assert raster.validate_raster_view(view, scope=scope) == view
    monkeypatch.setattr(raster, "MAX_RASTER_VIEW_BYTES", size - 1)
    with pytest.raises(ValueError):
        raster.validate_raster_view(view, scope=scope)


@pytest.mark.parametrize("key,bad", [
    ("page_count", True), ("page_count", 0), ("page_count", 5001),
    ("page_number", True), ("page_number", 0), ("page_number", 2),
    ("source_sha256", "f" * 63), ("scope_sha256", "e" * 64),
    ("coordinate_system", "crop_pixels"),
])
def test_scope_closed_identity_and_page_bounds(key, bad):
    scope, view = basic()
    scope[key] = bad
    if key != "scope_sha256":
        retag(scope, "scope_sha256")
    with pytest.raises(ValueError):
        raster.validate_raster_view(view, scope=scope)


@pytest.mark.parametrize("bad", [True, 0, -0.0, -0.001, float("nan"), float("inf"),
                                  1.0, math.nextafter(1.0, math.inf)])
def test_scope_fraction_type_domain_and_positive_extent(bad):
    scope, view = basic()
    scope["bbox"][0] = bad
    with pytest.raises(ValueError):
        raster.validate_raster_view(view, scope=scope)


@pytest.mark.parametrize("bad", [True, 0.0, 45, -90, 360, None])
def test_scope_rotation_is_exact_closed_integer(bad):
    scope, view = basic()
    scope["page_geometry"]["rotation_degrees"] = bad
    with pytest.raises(ValueError):
        raster.validate_raster_view(view, scope=scope)


@pytest.mark.parametrize("clip,profile", [
    ([0.0, 0.0, 2147483649.0, 1.0], "fit"),
    ([-2147483649.0, 0.0, -2147483648.0, 1.0], "fit"),
    ([16777216.0, 0.0, 16777217.0, 1.0], "fit"),
    ([2.0 ** -150, 0.0, 1.0, 1.0], "fit"),
    ([2.0 ** -130, 0.0, 1.0, 1.0], "fit"),
    ([2.0 ** -126, 0.0, 1000000.0, 1.0], "fit"),
    ([9000000.0, 0.0, 9000001.0, 1.0], "fit"),
    ([0.0, 0.0, 0.0001, 0.0001], "fit"),
    ([0.0, 0.0, 350.25, 1.0], "dpi288"),
], ids=["command-high", "command-low", "native-collapse", "native-zero-underflow",
        "native-subnormal", "projected-subnormal", "integer-clamp-domain", "rounded-empty",
        "detail-side-over"])
def test_annotation_domain_refusals_are_not_silent_clamps(clip, profile):
    # A declared full scope remains structurally valid; the new geometry domain
    # is separately narrower. This does not call/alter legacy PIL admission.
    scope = scope_for(clip, display=clip)
    with pytest.raises(ValueError):
        raster._expected_geometry(scope, profile)


@pytest.mark.parametrize("clip,pixels", [
    ([-8388608.0, 0.0, -8388607.0, 1.0], [-16777216, 0, -16777214, 2]),
    ([8388607.0, 0.0, 8388608.0, 1.0], [16777214, 0, 16777216, 2]),
])
def test_projected_integer_range_boundaries_are_admitted_without_clamp(clip, pixels):
    scope = scope_for(clip, display=clip)
    view = raster.build_raster_view(**captured(scope, clip, pixels=pixels))
    assert view["pixel_rect"] == pixels
    assert view["width"] == view["height"] == 2


def test_native_command_lower_boundary_has_exact_fit_recipe():
    clip = [-2147483648.0, -2147483648.0, 0.0, 0.0]
    scope = scope_for(clip, display=clip)
    scale = 1398 / 2147483648
    view = raster.build_raster_view(**captured(
        scope, clip, scale=scale, pixels=[-1398, -1398, 0, 0]))
    assert view["command_matrix"] == view["native_matrix"] == matrix(scale)
    assert view["pixel_rect"] == [-1398, -1398, 0, 0]


def test_exact_pixel_caps_and_detail_refusal_do_not_change_fit_recipe():
    clip = [0.0, 0.0, 350.0, 350.0]
    scope = scope_for(clip, display=clip)
    fit = raster.build_raster_view(**captured(scope, clip, pixels=[0, 0, 700, 700]))
    detail = raster.build_raster_view(**captured(
        scope, clip, profile="dpi288", scale=4.0, pixels=[0, 0, 1400, 1400]))
    assert fit["command_matrix"] == matrix(2.0)
    assert detail["width"] * detail["height"] == 1960000
    with pytest.raises(ValueError):
        raster.build_raster_view(**captured(
            scope, clip, profile="dpi576", scale=8.0, pixels=[0, 0, 2800, 2800]))
    assert raster.validate_raster_view(fit, scope=scope) == fit
    # 2800-square exceeds both side and total caps; this is not an independent
    # pixel-only exhaustion control (the total cap equals the two-side product).


@pytest.mark.parametrize("profile", [None, True, 288, "Fit", "dpi300", "dpi576 ", "x" * 4097])
def test_profile_is_fixed_and_not_an_arbitrary_dpi(profile):
    scope, _ = basic()
    with pytest.raises(ValueError):
        raster.build_raster_view(**captured(scope, [0.0, 0.0, 72.0, 72.0], profile=profile))


@pytest.mark.parametrize("key", tuple(VECTOR_SIZES) + ("pixel_rect", "width", "height", "rgb_sha256"))
def test_builder_requires_every_actual_observation(key):
    scope, _ = basic()
    args = captured(scope, [0.0, 0.0, 72.0, 72.0])
    args[key] = None
    with pytest.raises(ValueError):
        raster.build_raster_view(**args)


@pytest.mark.parametrize("key", tuple(VECTOR_SIZES))
def test_builder_rejects_inconsistent_captured_geometry(key):
    scope, _ = basic()
    args = captured(scope, [0.0, 0.0, 72.0, 72.0])
    args[key][0] += 0.125
    with pytest.raises(ValueError):
        raster.build_raster_view(**args)


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_declared_rotation_cropbox_do_not_apply_a_second_transform(rotation):
    display = [0.0, 0.0, 128.0, 256.0]
    cropbox = [16.0, 32.0, 272.0, 160.0] if rotation in (90, 270) else [16.0, 32.0, 144.0, 288.0]
    clip = [16.0, 32.0, 80.0, 96.0]
    scope = scope_for(clip, display=display, rotation=rotation, cropbox=cropbox)
    view = raster.build_raster_view(**captured(scope, clip, pixels=[32, 64, 160, 192]))
    assert view["page_geometry"] == scope["page_geometry"]
    result = raster.selection_to_scope_bbox({"kind": "raster_edges", "pixel_bbox": [0, 0, 64, 64]},
                                            raster_view=view, scope=scope)
    assert result == [0.125, 0.125, 0.375, 0.25]


def test_pixel_edges_map_without_pixel_center_shift():
    scope, view = basic()
    assert raster.selection_to_scope_bbox({"kind": "raster_edges", "pixel_bbox": [18, 36, 72, 108]},
                                          raster_view=view, scope=scope) == [0.125, 0.25, 0.5, 0.75]
    assert raster.selection_to_scope_bbox(
        {"kind": "raster_edges", "pixel_bbox": [18.5, 36.5, 72.5, 108.5]},
        raster_view=view, scope=scope) == [37 / 288, 73 / 288, 145 / 288, 217 / 288]


def test_whole_scope_is_exact_detached_nominal_box_not_raster_halo():
    scope, view = short_view("fit")
    result = raster.selection_to_scope_bbox({"kind": "whole_scope", "pixel_bbox": None},
                                           raster_view=view, scope=scope)
    assert result == scope["bbox"] and result is not scope["bbox"]
    result[0] = 0.0
    assert scope["bbox"][0] > 0
    with pytest.raises(ValueError):
        raster.selection_to_scope_bbox({"kind": "raster_edges", "pixel_bbox": [0, 0, 156, 15]},
                                       raster_view=view, scope=scope)
    assert raster.selection_to_scope_bbox({"kind": "raster_edges", "pixel_bbox": [1, 1, 100, 10]},
                                          raster_view=view, scope=scope) == [20.5 / 1024, 10.5 / 1024,
                                                                           70 / 1024, 15 / 1024]


@pytest.mark.parametrize("selection", [
    None, [], {"kind": "whole_scope"}, {"kind": "whole_scope", "pixel_bbox": [0, 0, 1, 1]},
    {"kind": "raster_edges", "pixel_bbox": None},
    {"kind": "whole_scope", "pixel_bbox": None, "extra": 1},
    {"kind": True, "pixel_bbox": None}, {"kind": "pixels", "pixel_bbox": [0, 0, 1, 1]},
    {"kind": "raster_edges", "pixel_bbox": (0, 0, 1, 1)},
    {"kind": "raster_edges", "pixel_bbox": [0, 0, 1]},
    {"kind": "raster_edges", "pixel_bbox": [0, 0, 1, 1, 2]},
    {"kind": "raster_edges", "pixel_bbox": [0, 0, 0, 1]},
    {"kind": "raster_edges", "pixel_bbox": [2, 0, 1, 1]},
    {"kind": "raster_edges", "pixel_bbox": [-0.01, 0, 1, 1]},
    {"kind": "raster_edges", "pixel_bbox": [0, 0, 144.01, 1]},
    {"kind": "raster_edges", "pixel_bbox": [0, 0, 1, 144.01]},
    {"kind": "raster_edges", "pixel_bbox": [False, 0, 1, 1]},
    {"kind": "raster_edges", "pixel_bbox": [float("nan"), 0, 1, 1]},
    {"kind": "raster_edges", "pixel_bbox": [0, 0, float("inf"), 1]},
    {"kind": "raster_edges", "pixel_bbox": [0, 0, 1 << 4096, 1]},
], ids=[f"selection-{index}" for index in range(20)])
def test_selection_closed_shape_numeric_and_raster_bounds(selection):
    scope, view = basic()
    with pytest.raises(ValueError):
        raster.selection_to_scope_bbox(selection, raster_view=view, scope=scope)


@pytest.mark.parametrize("kind", ["raster_edges", "whole_scope"])
def test_selection_cannot_bypass_view_hash_or_scope_binding(kind):
    scope, view = basic()
    view["view_sha256"] = "f" * 64
    selection = {"kind": kind, "pixel_bbox": [0, 0, 1, 1] if kind == "raster_edges" else None}
    with pytest.raises(ValueError):
        raster.selection_to_scope_bbox(selection, raster_view=view, scope=scope)


def test_tolerance_contraction_overlay_remains_unclamped_and_whole_scope_distinct():
    clip = [0.000244140625, 0.000244140625, 16.000244140625, 16.000244140625]
    scope = scope_for(clip)
    view = raster.build_raster_view(**captured(scope, clip, pixels=[0, 0, 32, 32]))
    before = encoded(scope["bbox"])
    overlay = raster.scope_bbox_to_raster_bbox(scope["bbox"], raster_view=view, scope=scope)
    assert overlay == [0.00048828125, 0.00048828125, 32.00048828125, 32.00048828125]
    assert overlay[2] > view["width"] and overlay[3] > view["height"]
    assert encoded(scope["bbox"]) == before
    assert raster.selection_to_scope_bbox({"kind": "whole_scope", "pixel_bbox": None},
                                          raster_view=view, scope=scope) == scope["bbox"]
    with pytest.raises(ValueError):
        raster.selection_to_scope_bbox({"kind": "raster_edges", "pixel_bbox": overlay},
                                       raster_view=view, scope=scope)


def test_profile_reload_forward_overlay_preserves_saved_source_bytes_and_identity():
    saved = [21 / 1024, 11 / 1024, 64 / 1024, 16 / 1024]
    original_bytes, original_object = encoded(saved), saved
    expected = {"fit": [2.0, 2.0, 88.0, 12.0], "dpi288": [4.0, 4.0, 176.0, 24.0],
                "dpi576": [7.0, 8.0, 351.0, 48.0]}
    for profile in ("fit", "dpi288", "dpi576", "fit", "dpi576", "fit"):
        scope, view = short_view(profile)
        overlay = raster.scope_bbox_to_raster_bbox(saved, raster_view=view, scope=scope)
        assert overlay == expected[profile]
        recovered = raster.selection_to_scope_bbox({"kind": "raster_edges", "pixel_bbox": overlay},
                                                  raster_view=view, scope=scope)
        assert recovered == saved and recovered is not saved
        overlay[0] = -1.0
        assert saved is original_object and encoded(saved) == original_bytes


@pytest.mark.parametrize("bbox", [
    [0, 0.0, 0.5, 0.5], [-0.0, 0.0, 0.5, 0.5], [0.0, 0.0, 0.0, 0.5],
    [-0.01, 0.0, 0.5, 0.5], [0.0, 0.0, 1.01, 0.5],
    [0.0, 0.0, float("inf"), 0.5], (0.0, 0.0, 0.5, 0.5),
])
def test_overlay_rejects_noncanonical_or_outside_saved_boxes(bbox):
    scope, view = basic()
    with pytest.raises(ValueError):
        raster.scope_bbox_to_raster_bbox(bbox, raster_view=view, scope=scope)
