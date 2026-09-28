"""Bounded rendered-crop observations and non-executing wider-scope advice.

No files, images, OCR, UI objects or approval enter this pure adapter. A padding
proposal is a NEW physical scope, never a replacement reference or permission
to render/run it. Numerical preflight does not prove a render occurred.
"""
from __future__ import annotations

from fractions import Fraction
import hashlib
import math

from ocr_crop_comparison import _bytes, _digest, _hex, validate_crop_scope
from ocr_crop_quality import inspect_crop_pixels
from ocr_crop_raster_view import _expected_geometry, validate_raster_view


MAX_ADVICE_BYTES = 32 * 1024
PADDING_POINTS = 2.0
_SIDES = ("left", "top", "right", "bottom")


def _fail():
    raise ValueError("invalid crop advice input or binding")


def _hash(value):
    return hashlib.sha256(_bytes(value, MAX_ADVICE_BYTES)).hexdigest()


def _inward_float(value, *, lower):
    """Round a rational new edge toward the old crop, never past the budget."""
    number = float(value)
    observed = Fraction(number)
    if lower and observed < value:
        number = math.nextafter(number, math.inf)
    elif not lower and observed > value:
        number = math.nextafter(number, -math.inf)
    return 0.0 if number == 0.0 else number


def propose_crop_padding(*, scope, raster_view):
    """Propose up to two source points per side, subject to page/profile bounds.

    Fractions keep rounding from accidentally exceeding the declared margin.
    Partial page margins and sub-ULP room stay explicit. No clipping is hidden,
    no fallback profile is selected, and no hypothetical raster view is minted.
    """
    view = validate_raster_view(raster_view, scope=scope)
    checked = validate_crop_scope(scope, source_sha256=view["source_sha256"],
                                  page_count=scope["page_count"])
    return _padding(checked, view)


def _padding(checked, view):
    """Numerical declaration only; callers validate scope and binding first."""
    display = checked["page_geometry"]["display_rect_points"]
    extents = [Fraction(display[i + 2]) - Fraction(display[i]) for i in (0, 1)]
    original = checked["bbox"]
    proposed, margins, page_limited, rounding_limited = [], [], [], []
    for index, edge in enumerate(original):
        extent = extents[index % 2]
        old = Fraction(edge)
        step = Fraction(PADDING_POINTS) / extent
        requested = old - step if index < 2 else old + step
        bounded = max(Fraction(0), min(Fraction(1), requested))
        value = _inward_float(bounded, lower=index < 2)
        margin = (old - Fraction(value) if index < 2 else Fraction(value) - old) * extent
        if not 0 <= margin <= Fraction(PADDING_POINTS):
            _fail()
        proposed.append(value)
        margins.append(float(margin))
        if bounded != requested:
            page_limited.append(_SIDES[index])
        if Fraction(value) != bounded:
            rounding_limited.append(_SIDES[index])

    result = {"schema_version": 1, "kind": "ocr_crop_padding_proposal",
        "source_sha256": view["source_sha256"], "original_scope_sha256": checked["scope_sha256"],
        "original_view_sha256": view["view_sha256"], "preview_profile": view["preview_profile"],
        "requested_margins_points": [PADDING_POINTS] * 4,
        "available_margins_points": margins, "page_limited_edges": page_limited,
        "rounding_limited_edges": rounding_limited, "original_bbox": list(original),
        "original_scope": checked,
        "proposed_bbox": proposed, "proposed_scope": None, "planned_raster": None,
        "status": "unchanged", "reason": "no_representable_wider_scope",
        "render_performed": False, "ocr_performed": False, "requires_new_scope_review": True}
    if proposed != original:
        candidate = {key: value for key, value in checked.items() if key != "scope_sha256"}
        candidate["bbox"] = proposed
        candidate["scope_sha256"] = _digest(candidate)
        candidate = validate_crop_scope(candidate, source_sha256=view["source_sha256"],
                                        page_count=checked["page_count"])
        # This private pure helper predicts the EXISTING profile admission; it
        # does not synthesize build_raster_view's actual-render observations.
        try:
            geometry = _expected_geometry(candidate, view["preview_profile"])
        except ValueError:
            result.update(status="unavailable", reason="profile_geometry_or_raster_limit")
        else:
            result.update(status="proposed", reason="explicit_new_scope_required", proposed_scope=candidate,
                planned_raster={"width": geometry["width"], "height": geometry["height"],
                                "scale": geometry["native_matrix"][0]})
    result["proposal_sha256"] = _hash(result)
    _bytes(result, MAX_ADVICE_BYTES)
    return result


def _display_padding(pixels, padding):
    """Check displayed proposal semantics, not render/producer authenticity."""
    try:
        binding = pixels["binding"]
        original = padding["original_scope"]
        if type(binding) is not dict or type(original) is not dict:
            _fail()
        checked = validate_crop_scope(original, source_sha256=_hex(binding["source_sha256"]),
                                      page_count=original["page_count"])
        if (binding["scope_sha256"] != checked["scope_sha256"]
                or type(binding["page_number"]) is not int
                or binding["page_number"] != checked["page_number"]
                or type(binding["preview_profile"]) is not str
                or binding["preview_profile"] not in ("fit", "dpi288", "dpi576")):
            _fail()
        declared_view = {"source_sha256": checked["source_sha256"],
                         "view_sha256": _hex(binding["view_sha256"]),
                         "preview_profile": binding["preview_profile"]}
        expected = _padding(checked, declared_view)
        # Canonical bytes distinguish bool/int/float and include all statuses,
        # flags, scope/geometry joins and nested hashes. This does not replay RGB.
        if _bytes(padding, MAX_ADVICE_BYTES) != _bytes(expected, MAX_ADVICE_BYTES):
            _fail()
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
        raise ValueError("invalid crop advice input or binding") from None


def build_crop_advice(rgb, *, scope, raster_view):
    """Inspect only host-admitted immutable bytes; retain expected ambiguity.

    Invalid bindings are NOT advisory unavailability. Callers retain their
    existing owner/refusal and late cancellation/source-generation checks.
    """
    result = {"schema_version": 1, "kind": "ocr_crop_advice",
              "pixels": inspect_crop_pixels(rgb, scope=scope, raster_view=raster_view),
              "padding": propose_crop_padding(scope=scope, raster_view=raster_view)}
    result["advice_sha256"] = _hash(result)
    _bytes(result, MAX_ADVICE_BYTES)
    return result


def crop_advice_text(advice):
    """Bounded plain-text guidance from a locally built observation, not consent.

    Rehashing checks a declaration, not producer authenticity or image delivery.
    This text is never an input to execution, scoring, Review or Save admission.
    """
    _bytes(advice, MAX_ADVICE_BYTES)
    if (type(advice) is not dict or set(advice) != {"schema_version", "kind", "pixels", "padding", "advice_sha256"}
            or type(advice["schema_version"]) is not int or advice["schema_version"] != 1
            or advice["kind"] != "ocr_crop_advice"
            or advice["advice_sha256"] != _hash({key: value for key, value in advice.items() if key != "advice_sha256"})):
        _fail()
    pixels, padding = advice["pixels"], advice["padding"]
    if type(pixels) is not dict or type(padding) is not dict:
        _fail()
    _display_padding(pixels, padding)
    lines = ["Preview observations only: annotation-on rendered pixels include backgrounds, rules and overlays. "
             "They do not establish text completeness, blur, scanner resolution or OCR accuracy."]
    measurements = pixels.get("measurements")
    if type(measurements) is not dict:
        _fail()
    values = [measurements.get(key) for key in ("min", "max", "p05", "p95")]
    if any(type(value) is not int or not 0 <= value <= 255 for value in values):
        _fail()
    if not values[0] <= values[2] <= values[3] <= values[1]:
        _fail()
    lines.append(f"Rendered luminance (0–255): range {values[0]}–{values[1]}, "
                 f"5th–95th percentiles {values[2]}–{values[3]}. Sparse text can have flat percentiles; no pass/fail cutoff is applied.")
    lines.append("Inspect the four crop edges in the original image; low/high pixel counts in the details are polarity hypotheses, "
                 "not proof of clipped characters. Missing band coverage is not a clear edge.")
    status = padding.get("status")
    if status == "proposed":
        bbox = padding.get("proposed_bbox")
        if (type(bbox) is not list or len(bbox) != 4
                or any(type(value) is not float or not math.isfinite(value) or not 0 <= value <= 1 for value in bbox)):
            _fail()
        lines.append("Wider-context proposal (up to 2 source points per side), page fractions [left, top, right, bottom]: "
                     + repr(bbox) + ". Select this as a NEW region explicitly; no wider preview or OCR was run. "
                     "Keep the original reference/history separate; a new scope needs its own reference and approval. "
                     "The budget uses exact declared page extents; native raster rounding can differ. "
                     "Wider Fit can change scale.")
    elif status == "unavailable":
        lines.append("The wider scope is unavailable under the selected profile's geometry/raster limits; "
                     "no fallback or render was attempted.")
    elif status == "unchanged":
        lines.append("No representable wider in-page scope is available. The original crop is unchanged.")
    else:
        _fail()
    lines.append("If the original source is also illegible, consider a better source or rescan; enlarging this preview cannot restore lost detail.")
    return "\n".join(lines)
