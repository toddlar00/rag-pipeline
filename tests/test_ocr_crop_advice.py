"""Generated pure padding/notice controls; no native image, OCR or UI execution.

Raster declarations are synthetic fixtures, not evidence of a real render.
Expected padding/rounding budgets are independently checked as exact rationals.
"""
from copy import deepcopy
from fractions import Fraction
import hashlib

import pytest

import ocr_crop_advice as advice
import ocr_crop_raster_view as raster
from ocr_crop_comparison import _bytes, validate_crop_scope
from test_ocr_crop_raster_view import scope_for


def fixture(*, clip=(8., 8., 24., 24.), display=(0., 0., 64., 64.), profile="fit", rotation=0):
    scope = scope_for(clip, display=display, rotation=rotation)
    geometry = raster._expected_geometry(scope, profile)
    rgb = bytes([255]) * (geometry["width"] * geometry["height"] * 3)
    view = raster.build_raster_view(scope=scope, preview_profile=profile,
                                   rgb_sha256=hashlib.sha256(rgb).hexdigest(), **geometry)
    return scope, view, rgb


def test_padding_is_detached_bounded_new_scope_not_render_or_approval():
    scope, view, _ = fixture()
    before = deepcopy((scope, view))
    result = advice.propose_crop_padding(scope=scope, raster_view=view)
    assert result["status"] == "proposed"
    assert result["original_bbox"] == [.125, .125, .375, .375]
    assert result["proposed_bbox"] == [.09375, .09375, .40625, .40625]
    assert result["available_margins_points"] == [2.] * 4
    assert result["requested_margins_points"] == [2.] * 4
    assert result["page_limited_edges"] == result["rounding_limited_edges"] == []
    assert result["planned_raster"] == {"width": 40, "height": 40, "scale": 2.}
    candidate = result["proposed_scope"]
    assert candidate["scope_sha256"] != scope["scope_sha256"]
    assert validate_crop_scope(candidate, source_sha256=scope["source_sha256"], page_count=1) == candidate
    assert result["render_performed"] is result["ocr_performed"] is False
    assert result["requires_new_scope_review"] is True
    assert (scope, view) == before
    candidate["page_geometry"]["display_rect_points"][0] = -1.
    assert (scope, view) == before


def test_page_limited_margins_are_reported_not_presented_as_two_points():
    scope, view, _ = fixture(clip=(1., 0., 63., 63.5))
    result = advice.propose_crop_padding(scope=scope, raster_view=view)
    assert result["status"] == "proposed"
    assert result["requested_margins_points"] == [2.] * 4
    assert result["available_margins_points"] == [1., 0., 1., .5]
    assert result["page_limited_edges"] == ["left", "top", "right", "bottom"]
    assert result["proposed_bbox"] == [0., 0., 1., 1.]


def test_whole_page_has_explicit_unchanged_outcome():
    scope, view, _ = fixture(clip=(0., 0., 64., 64.))
    result = advice.propose_crop_padding(scope=scope, raster_view=view)
    assert result["status"] == "unchanged"
    assert result["available_margins_points"] == [0.] * 4
    assert result["proposed_scope"] is result["planned_raster"] is None
    assert result["proposed_bbox"] == scope["bbox"]


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_padding_uses_display_axes_without_second_rotation(rotation):
    scope, view, _ = fixture(clip=(8., 16., 32., 64.), display=(0., 0., 64., 128.), rotation=rotation)
    result = advice.propose_crop_padding(scope=scope, raster_view=view)
    assert result["proposed_bbox"] == [6 / 64, 14 / 128, 34 / 64, 66 / 128]
    assert result["proposed_scope"]["page_geometry"] == scope["page_geometry"]


def test_nonzero_display_origin_is_retained():
    scope, view, _ = fixture(clip=(40., 72., 56., 88.), display=(32., 64., 96., 128.))
    result = advice.propose_crop_padding(scope=scope, raster_view=view)
    assert result["proposed_bbox"] == [6 / 64, 6 / 64, 26 / 64, 26 / 64]
    assert result["planned_raster"]["width"] == result["planned_raster"]["height"] == 40


@pytest.mark.parametrize("clip", [(100., 123., 201., 251.), (.25, 33., 999.75, 444.), (100.1, 101.1, 900.9, 901.9)])
def test_float_edges_never_exceed_the_exact_two_point_budget(clip):
    scope, view, _ = fixture(clip=clip, display=(0., 0., 1000., 1000.))
    result = advice.propose_crop_padding(scope=scope, raster_view=view)
    for index, (old, new) in enumerate(zip(scope["bbox"], result["proposed_bbox"])):
        exact = ((Fraction(old) - Fraction(new)) if index < 2 else (Fraction(new) - Fraction(old))) * 1000
        assert 0 <= exact <= 2
        assert result["available_margins_points"][index] == float(exact)


def test_exact_endpoint_extent_not_rounded_subtraction_controls_margin():
    display = (-2. ** -53, -2. ** -53, 64., 64.)
    scope, view, _ = fixture(display=display)
    assert display[2] - display[0] == 64.
    extent = Fraction(64) + Fraction(1, 2 ** 53)
    result = advice.propose_crop_padding(scope=scope, raster_view=view)
    assert result["proposed_bbox"][0] > .09375
    for index, (old, new) in enumerate(zip(scope["bbox"], result["proposed_bbox"])):
        exact = ((Fraction(old) - Fraction(new)) if index < 2 else (Fraction(new) - Fraction(old))) * extent
        assert 0 <= exact <= 2
        assert result["available_margins_points"][index] == float(exact)


def test_selected_detail_refusal_never_falls_back_to_fit():
    scope, view, _ = fixture(clip=(0., 0., 174., 174.), display=(0., 0., 512., 512.), profile="dpi576")
    result = advice.propose_crop_padding(scope=scope, raster_view=view)
    assert result["status"] == "unavailable"
    assert result["reason"] == "profile_geometry_or_raster_limit"
    assert result["preview_profile"] == "dpi576"
    assert result["proposed_scope"] is result["planned_raster"] is None
    assert result["render_performed"] is False


def test_wider_fit_has_separate_scale_not_reused_raster_declaration():
    scope, view, _ = fixture(clip=(10., 10., 710., 710.), display=(0., 0., 1024., 1024.))
    result = advice.propose_crop_padding(scope=scope, raster_view=view)
    assert result["status"] == "proposed"
    assert result["planned_raster"]["scale"] < view["native_matrix"][0]
    assert "raster_view" not in result and "view_sha256" not in result["planned_raster"]


@pytest.mark.parametrize("field", ["source_sha256", "scope_sha256", "view_sha256", "rgb_sha256", "width", "preview_profile"])
def test_padding_rejects_tampered_bound_raster(field):
    scope, view, _ = fixture()
    view[field] = 100 if field == "width" else "wrong"
    with pytest.raises(ValueError):
        advice.propose_crop_padding(scope=scope, raster_view=view)


def test_advice_contains_replayable_pixel_and_scope_bindings_not_authority():
    scope, view, rgb = fixture()
    before = deepcopy((scope, view))
    result = advice.build_crop_advice(rgb, scope=scope, raster_view=view)
    assert result == advice.build_crop_advice(rgb, scope=scope, raster_view=view)
    assert set(result) == {"schema_version", "kind", "pixels", "padding", "advice_sha256"}
    assert result["pixels"]["binding"]["rgb_sha256"] == hashlib.sha256(rgb).hexdigest()
    assert result["pixels"]["binding"]["view_sha256"] == view["view_sha256"]
    assert result["pixels"]["status"] == "ambiguous"
    assert len(_bytes(result, advice.MAX_ADVICE_BYTES)) <= advice.MAX_ADVICE_BYTES
    assert (scope, view) == before
    text = advice.crop_advice_text(result)
    for phrase in ("annotation-on", "Sparse text", "NEW region", "no wider preview or OCR", "own reference and approval", "cannot restore lost detail"):
        assert phrase in text


@pytest.mark.parametrize("clip,profile,phrase", [
    ((0., 0., 512., 512.), "fit", "No representable wider"),
    ((0., 0., 174., 174.), "dpi576", "unavailable under the selected profile"),
])
def test_nonproposed_padding_notice_is_explicit(clip, profile, phrase):
    scope, view, rgb = fixture(clip=clip, display=(0., 0., 512., 512.), profile=profile)
    result = advice.build_crop_advice(rgb, scope=scope, raster_view=view)
    assert phrase in advice.crop_advice_text(result)


def test_formatter_rejects_modified_declaration():
    scope, view, rgb = fixture()
    result = advice.build_crop_advice(rgb, scope=scope, raster_view=view)
    result["pixels"]["measurements"]["min"] = 0
    with pytest.raises(ValueError, match="invalid crop advice"):
        advice.crop_advice_text(result)


@pytest.mark.parametrize("path,value", [
    (("padding", "proposed_bbox"), [.4, .4, .1, .1]),
    (("padding", "proposed_bbox"), [.08, .08, .42, .42]),
    (("padding", "status"), "unavailable"),
    (("padding", "status"), "unchanged"),
    (("padding", "render_performed"), True),
    (("padding", "ocr_performed"), True),
    (("padding", "requires_new_scope_review"), False),
    (("padding", "available_margins_points"), [3.] * 4),
    (("padding", "proposed_scope", "bbox", 0), .08),
    (("padding", "planned_raster", "width"), 1),
    (("pixels", "binding", "view_sha256"), "b" * 64),
    (("pixels", "binding", "source_sha256"), "b" * 64),
    (("pixels", "binding", "scope_sha256"), "b" * 64),
    (("pixels", "binding", "preview_profile"), "dpi288"),
    (("pixels", "binding", "page_number"), True),
    (("pixels", "measurements", "max"), 0),
    (("pixels", "measurements", "p95"), 0),
])
def test_formatter_rejects_rehashed_semantic_mismatches(path, value):
    scope, view, rgb = fixture()
    result = advice.build_crop_advice(rgb, scope=scope, raster_view=view)
    destination = result
    for key in path[:-1]:
        destination = destination[key]
    destination[path[-1]] = value
    padding = result["padding"]
    padding["proposal_sha256"] = hashlib.sha256(_bytes(
        {key: item for key, item in padding.items() if key != "proposal_sha256"}, advice.MAX_ADVICE_BYTES)).hexdigest()
    result["advice_sha256"] = hashlib.sha256(_bytes(
        {key: item for key, item in result.items() if key != "advice_sha256"}, advice.MAX_ADVICE_BYTES)).hexdigest()
    with pytest.raises(ValueError, match="invalid crop advice"):
        advice.crop_advice_text(result)


def test_invalid_pixels_are_not_silently_converted_into_unavailability():
    scope, view, rgb = fixture()
    with pytest.raises(ValueError):
        advice.build_crop_advice(rgb[:-1], scope=scope, raster_view=view)
