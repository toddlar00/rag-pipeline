"""Strict pixel-observation and saved-geometry correspondence policy.

Discovery is supplied by a separate source-only renderer. This module never
reads files or pixels, runs a recognizer, authenticates observations, or treats
ink/box overlap as transcription accuracy or complete source coverage.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math

from evaluation_inputs import _hex_digest
from ocr_docling import _exact, validate_docling_proposals
from ocr_recovery_comparison import validate_recovery_report


MAX_OBSERVATION_BYTES = 32 * 1024 * 1024
MAX_REPORT_BYTES = 32 * 1024 * 1024
MAX_COMPARISONS = 20_000_000
MAX_LINES = 2000
MAX_LAYOUT_REGIONS = 500
DEFAULT_CONFIGURATION = {
    "algorithm": "dark-ink-component-hypotheses-v1", "dpi": 300,
    "coordinate_system": "original_displayed_page_raster_pixels",
    "pixel_digest_domain": "rag-pipeline:ocr-scan-gray8:v1",
    "render_annotations": True,
    "max_pages": 8, "max_side": 6000, "max_page_pixels": 25_000_000,
    "max_total_pixels": 100_000_000, "max_horizontal_runs_per_mask": 100_000,
    "max_components_per_page": 10_000, "max_pairs_per_page": 1_000_000,
    "max_total_pairs": 8_000_000, "max_regions_per_page": 5000,
    "dense_foreground_fraction": .30, "rule_length_pixels": 75,
    "glyph_min_height_pixels": 3, "glyph_max_height_pixels": 105, "glyph_min_area_pixels": 3,
    "glyph_max_width_to_height": 4., "glyph_max_height_ratio": 2.5,
    "neighbor_gap_in_max_heights": 1.25, "minimum_vertical_overlap": .45,
    "minimum_group_components": 3, "minimum_group_width_to_height": 2.,
    "threshold": "global_otsu_dark_foreground_no_blur_no_rescale",
    "connectivity": 8, "component_order": "top_left_then_size_then_area",
}
SPATIAL_CONFIGURATION = {
    **DEFAULT_CONFIGURATION,
    "algorithm": "dark-ink-spatial-component-hypotheses-v2",
    "neighbor_enumeration": "closed_aabb_grid_canonical_unique_pairs",
    "spatial_cell_pixels": 128,
    "max_index_entries_per_page": 100_000,
    "max_bucket_lookups_per_page": 140_000,
    "max_bucket_visits_per_page": 10_000_000,
    "max_total_index_entries": 800_000,
    "max_total_bucket_lookups": 1_120_000,
    "max_total_bucket_visits": 80_000_000,
}
_RECIPE_CONFIGURATIONS = {"legacy-v1": DEFAULT_CONFIGURATION, "spatial-v2": SPATIAL_CONFIGURATION}
_RECIPE_SCHEMAS = {"legacy-v1": 1, "spatial-v2": 2}
_STATUS_REASON = {
    "available": {"bounded_pixel_discovery"},
    "blank_at_threshold": {"no_dark_otsu_foreground"},
    "ambiguous": {"dense_foreground_not_text_classified"},
    "unavailable": {"geometry_unavailable", "render_failed", "raster_limit", "cohort_pixel_limit",
                    "horizontal_run_limit", "component_limit", "component_pair_limit", "region_limit", "stage_failed"},
}
_REGION_REASONS = {
    "text_like": {"aligned_glyph_scale_components_not_text_proof"},
    "rule_like": {"long_axis_morphology_not_semantic_nontext"},
    "ambiguous_ink": {"isolated_or_non_line_components", "tiny_or_large_residual_component", "dense_foreground"},
}
_WORK_KEYS = {"rule_horizontal_runs", "residual_horizontal_runs", "rule_component_count",
              "residual_component_count", "pair_tests"}
_SPATIAL_COUNTERS = {"spatial_index_entries": "index_entries",
                     "spatial_bucket_lookups": "bucket_lookups",
                     "spatial_bucket_visits": "bucket_visits"}
_SPATIAL_WORK_KEYS = _WORK_KEYS | set(_SPATIAL_COUNTERS) | {"glyph_count", "spatial_phase"}
_OBSERVATION_DIGEST = {
    "algorithm": "sha256", "domain": "rag-pipeline:ocr-scan-observation:v1",
    "serialization": "utf8-sorted-compact-json", "is_file_digest": False,
}
_ATTESTATIONS = {
    "requires_attention": True, "operator_review_required": True,
    "accuracy_verified": False, "full_page_coverage_verified": False,
    "recognition_rerun": False, "canonical_extraction_modified": False,
    "observation_authenticity_verified": False,
}


def _fields(value, keys):
    if type(value) is not dict or set(value) != keys:
        raise ValueError("invalid scan observation fields")
    return value


def _integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError("scan observation integer exceeds bounds")
    return value


def _number(value, low, high):
    if type(value) not in (int, float) or not low <= value <= high or not math.isfinite(value):
        raise ValueError("scan observation number exceeds bounds")
    return value


def _digest(value):
    if type(value) is not str:
        raise ValueError("invalid scan observation digest")
    return _hex_digest(value, label="scan observation digest")


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


def _encoded_bound(value, limit):
    size = 1
    for chunk in json.JSONEncoder(sort_keys=True, ensure_ascii=False, allow_nan=False, indent=2).iterencode(value):
        size += len(chunk.encode("utf-8"))
        if size > limit:
            raise ValueError("scan artifact exceeds its encoded byte budget")


def configuration_for_recipe(recipe="legacy-v1"):
    """Return a detached fixed recipe; selection never imports native libraries."""
    if type(recipe) is not str or recipe not in _RECIPE_CONFIGURATIONS:
        raise ValueError("unsupported scan recipe")
    return copy.deepcopy(_RECIPE_CONFIGURATIONS[recipe])


def recipe_for_configuration(configuration):
    """Reject unknown, hybrid and type-coerced configurations, including old edits."""
    for recipe, expected in _RECIPE_CONFIGURATIONS.items():
        if _exact(configuration, expected):
            return recipe
    raise ValueError("unsupported scan configuration")


def requested_scan_pages(value, page_count=5000):
    """Detach an explicit bounded page cohort; never infer all-page authority."""
    _integer(page_count, 1, 5000)
    if type(value) is not list or not 1 <= len(value) <= DEFAULT_CONFIGURATION["max_pages"]:
        raise ValueError("scan inspection requires one to eight explicit pages")
    checked = [_integer(number, 1, page_count) for number in value]
    if len(set(checked)) != len(checked):
        raise ValueError("scan inspection pages must be distinct")
    return sorted(checked)


def _geometry(value):
    _fields(value, {"width_points", "height_points", "rotation", "cropbox"})
    for name in ("width_points", "height_points"):
        _number(value[name], 1e-6, 10_000_000)
    if type(value["rotation"]) is not int or value["rotation"] not in (0, 90, 180, 270):
        raise ValueError("invalid scan page rotation")
    box = value["cropbox"]
    if type(box) is not list or len(box) != 4:
        raise ValueError("invalid scan CropBox")
    for coordinate in box:
        _number(coordinate, -10_000_000, 10_000_000)
    if box[0] >= box[2] or box[1] >= box[3]:
        raise ValueError("empty scan CropBox")


def _raster(value, geometry):
    _fields(value, {"width", "height", "dpi", "format", "pixel_sha256"})
    width, height = (_integer(value[key], 1, DEFAULT_CONFIGURATION["max_side"]) for key in ("width", "height"))
    if (width * height > DEFAULT_CONFIGURATION["max_page_pixels"] or type(value["format"]) is not str or value["format"] != "gray8"
            or type(value["dpi"]) is not int or value["dpi"] != DEFAULT_CONFIGURATION["dpi"]):
        raise ValueError("unsupported scan raster")
    _digest(value["pixel_sha256"])
    # Integer raster rounding is declared uncertainty, not an exact historical
    # pixel join. Source-only native collection checks actual renderer bounds.
    if geometry is None or any(abs(value[axis] - geometry[points] * value["dpi"] / 72) > 2
                               for axis, points in (("width", "width_points"), ("height", "height_points"))):
        raise ValueError("scan raster and displayed geometry differ")
    return width * height


def _region(value, index, raster):
    _fields(value, {"region_id", "kind", "reason", "bbox", "component_count", "foreground_pixels"})
    if (type(value["region_id"]) is not str or value["region_id"] != f"scan-{index:05d}"
            or type(value["kind"]) is not str or value["kind"] not in _REGION_REASONS):
        raise ValueError("invalid scan region identity or kind")
    if type(value["reason"]) is not str or value["reason"] not in _REGION_REASONS[value["kind"]]:
        raise ValueError("invalid scan region reason")
    box = value["bbox"]
    if type(box) is not list or len(box) != 4:
        raise ValueError("invalid scan region rectangle")
    for offset, coordinate in enumerate(box):
        _integer(coordinate, 0, raster["width"] if offset % 2 == 0 else raster["height"])
    if box[0] >= box[2] or box[1] >= box[3]:
        raise ValueError("empty scan region rectangle")
    _integer(value["foreground_pixels"], 1, (box[2] - box[0]) * (box[3] - box[1]))
    if value["component_count"] is None:
        if value["reason"] != "dense_foreground":
            raise ValueError("scan region components are missing")
    else:
        _integer(value["component_count"], 1, DEFAULT_CONFIGURATION["max_components_per_page"])
        if value["reason"] == "dense_foreground":
            raise ValueError("dense foreground must not invent component measurements")
    if value["kind"] == "text_like":
        if (value["component_count"] < DEFAULT_CONFIGURATION["minimum_group_components"]
                or box[2] - box[0] < DEFAULT_CONFIGURATION["minimum_group_width_to_height"] * (box[3] - box[1])):
            raise ValueError("text-like region contradicts its grouping rule")


def _discovery(value, raster, geometry):
    _fields(value, {"status", "reason", "threshold", "threshold_foreground_pixels",
                    "foreground_accounting_complete", "regions", "work"})
    status, reason = value["status"], value["reason"]
    if type(status) is not str or status not in _STATUS_REASON or type(reason) is not str or reason not in _STATUS_REASON[status]:
        raise ValueError("invalid scan discovery status or reason")
    if value["foreground_accounting_complete"] is not (status != "unavailable"):
        raise ValueError("scan foreground accounting claim contradicts availability")
    regions = value["regions"]
    if type(regions) is not list or len(regions) > DEFAULT_CONFIGURATION["max_regions_per_page"]:
        raise ValueError("scan region budget exceeded")
    work = _fields(value["work"], _WORK_KEYS)
    _integer(work["pair_tests"], 0, DEFAULT_CONFIGURATION["max_pairs_per_page"])
    for key in _WORK_KEYS - {"pair_tests"}:
        if work[key] is not None:
            # A limit-rejection records the measured count without claiming
            # that it was admitted for subsequent allocation or grouping.
            upper = DEFAULT_CONFIGURATION["max_page_pixels"]
            _integer(work[key], 0, upper)
    if raster is None:
        if (status != "unavailable" or regions or value["threshold"] is not None
                or value["threshold_foreground_pixels"] is not None or work["pair_tests"] != 0
                or any(work[key] is not None for key in _WORK_KEYS - {"pair_tests"})):
            raise ValueError("scan measurements require an observed raster")
        if reason not in {"geometry_unavailable", "render_failed", "raster_limit", "cohort_pixel_limit", "stage_failed"}:
            raise ValueError("scan processing cannot precede rendering")
        if geometry is None and reason not in {"geometry_unavailable", "cohort_pixel_limit", "stage_failed"}:
            raise ValueError("scan geometry is unavailable")
        return
    if geometry is None or reason in {"geometry_unavailable", "render_failed", "raster_limit", "cohort_pixel_limit"}:
        raise ValueError("rendered scan contradicts unavailable geometry or raster")
    if value["threshold"] is not None:
        _number(value["threshold"], 0, 255)
    foreground = value["threshold_foreground_pixels"]
    if foreground is not None:
        _integer(foreground, 0, raster["width"] * raster["height"])
    if (value["threshold"] is None) != (foreground is None):
        raise ValueError("partial threshold evidence")
    if status == "unavailable":
        if regions:
            raise ValueError("unavailable scan cannot retain a partial region success")
        return
    if foreground is None:
        raise ValueError("observed scan requires threshold evidence")
    for index, region in enumerate(regions, 1):
        _region(region, index, raster)
        if (region["reason"] == "dense_foreground") != (status == "ambiguous"):
            raise ValueError("dense foreground region contradicts discovery status")
    if sum(region["foreground_pixels"] for region in regions) != foreground:
        raise ValueError("scan foreground accounting does not balance")
    if regions != sorted(regions, key=lambda region: (region["bbox"][1], region["bbox"][0], region["kind"], region["bbox"][3:])):
        raise ValueError("scan regions are not in canonical geometric order")
    if status == "blank_at_threshold":
        if foreground != 0 or regions or any(work[key] is not None for key in _WORK_KEYS - {"pair_tests"}) or work["pair_tests"]:
            raise ValueError("threshold-blank scan contains fabricated component work")
    elif status == "ambiguous":
        if (foreground <= DEFAULT_CONFIGURATION["dense_foreground_fraction"] * raster["width"] * raster["height"]
                or len(regions) != 1 or regions[0]["reason"] != "dense_foreground"
                or regions[0]["bbox"] != [0, 0, raster["width"], raster["height"]]
                or any(work[key] is not None for key in _WORK_KEYS - {"pair_tests"}) or work["pair_tests"]):
            raise ValueError("dense foreground claim differs from its declared rule")
    else:
        if foreground <= 0 or foreground > DEFAULT_CONFIGURATION["dense_foreground_fraction"] * raster["width"] * raster["height"]:
            raise ValueError("available scan contradicts foreground admission")
        if any(work[key] is None for key in _WORK_KEYS - {"pair_tests"}):
            raise ValueError("component work observation is incomplete")
        components = sum(work[key] for key in ("rule_component_count", "residual_component_count"))
        if components > DEFAULT_CONFIGURATION["max_components_per_page"] or sum(region["component_count"] for region in regions) != components:
            raise ValueError("component accounting does not balance")
        for prefix in ("rule", "residual"):
            if not work[prefix + "_component_count"] <= work[prefix + "_horizontal_runs"] <= DEFAULT_CONFIGURATION["max_horizontal_runs_per_mask"]:
                raise ValueError("component run budget contradicts admission")


def _spatial_component_work(value, raster):
    """Validate measured component stages without inventing rejected allocations."""
    work, reason = value["work"], value["reason"]
    configuration = SPATIAL_CONFIGURATION
    counts, measured_runs = [], []
    for prefix in ("rule", "residual"):
        runs, count = work[prefix + "_horizontal_runs"], work[prefix + "_component_count"]
        if count is not None and (runs is None or count > runs
                                 or runs > configuration["max_horizontal_runs_per_mask"]):
            raise ValueError("spatial component measurements contradict run admission")
        if runs is not None and (raster is None or value["threshold_foreground_pixels"] is None):
            raise ValueError("spatial component work precedes thresholding")
        if runs is not None:
            measured_runs.append(runs)
        if prefix == "residual" and runs is not None:
            if (work["rule_component_count"] is None
                    or work["rule_component_count"] > configuration["max_components_per_page"]):
                raise ValueError("spatial residual work precedes rule admission")
        if count is not None:
            counts.append(count)
    if measured_runs:
        foreground = value["threshold_foreground_pixels"]
        if (foreground <= 0 or foreground > configuration["dense_foreground_fraction"] * raster["width"] * raster["height"]
                or sum(measured_runs) > foreground):
            raise ValueError("spatial runs contradict admitted disjoint foreground")
    if reason == "horizontal_run_limit":
        if not any(work[key] is not None and work[key] > configuration["max_horizontal_runs_per_mask"]
                   for key in ("rule_horizontal_runs", "residual_horizontal_runs")):
            raise ValueError("spatial run rejection lacks measured excess")
    if reason == "component_limit" and sum(counts) <= configuration["max_components_per_page"]:
        raise ValueError("spatial component rejection lacks measured excess")


def _spatial_partition(value):
    """Reconcile every successful region with the declared component partition."""
    work = value["work"]
    glyphs, groups, rules, other = 0, 0, 0, 0
    for region in value["regions"]:
        count, reason = region["component_count"], region["reason"]
        if reason in {"aligned_glyph_scale_components_not_text_proof", "isolated_or_non_line_components"}:
            glyphs += count
            groups += 1
            if region["foreground_pixels"] < SPATIAL_CONFIGURATION["glyph_min_area_pixels"] * count:
                raise ValueError("spatial glyph support contradicts admission")
            left, top, right, bottom = region["bbox"]
            text_like = (count >= SPATIAL_CONFIGURATION["minimum_group_components"]
                         and right - left >= SPATIAL_CONFIGURATION["minimum_group_width_to_height"] * (bottom - top))
            if (region["kind"] == "text_like") != text_like:
                raise ValueError("spatial region contradicts final group classification")
        else:
            if count != 1:
                raise ValueError("spatial rule and non-glyph regions must be single components")
            if region["kind"] == "rule_like":
                rules += 1
            else:
                other += 1
    if (glyphs != work["glyph_count"] or rules != work["rule_component_count"]
            or glyphs + other != work["residual_component_count"]
            or glyphs - groups > work["pair_tests"]):
        raise ValueError("spatial region partition does not balance")


def _spatial_discovery(value, raster, geometry):
    """The v2 work contract adds consistency checks, not execution attestation.

    An admitted glyph covers at most 10 closed 128px index cells; its query
    (original vertical extent, 131.25px horizontal padding) covers 3--14 cells.
    Each unordered pair is visited at most ten times and tested at most once.
    These constants are valid only for the exact, versioned recipe above.
    """
    _fields(value, {"status", "reason", "threshold", "threshold_foreground_pixels",
                    "foreground_accounting_complete", "regions", "work"})
    work = _fields(value["work"], _SPATIAL_WORK_KEYS)
    # Reuse the historical raster/foreground/region contract without widening
    # its accepted v1 reason set or work shape.
    base = {**value, "work": {key: work[key] for key in _WORK_KEYS}}
    if value["reason"] == "bucket_visit_limit":
        base["reason"] = "stage_failed"
    _discovery(base, raster, geometry)
    phase = work["spatial_phase"]
    if type(phase) is not str or phase not in {"not_started", "indexing", "querying", "grouping", "complete"}:
        raise ValueError("invalid spatial enumeration phase")
    for key, name in _SPATIAL_COUNTERS.items():
        _integer(work[key], 0, SPATIAL_CONFIGURATION["max_" + name + "_per_page"])
    _spatial_component_work(value, raster)
    status, reason = value["status"], value["reason"]
    if phase == "not_started":
        if (work["glyph_count"] is not None or any(work[key] for key in _SPATIAL_COUNTERS)
                or work["pair_tests"] or status == "available"
                or reason in {"bucket_visit_limit", "component_pair_limit", "region_limit"}):
            raise ValueError("spatial work contradicts an unstarted enumeration")
        return
    n = _integer(work["glyph_count"], 0, SPATIAL_CONFIGURATION["max_components_per_page"])
    foreground = value["threshold_foreground_pixels"]
    if (raster is None or foreground is None or foreground <= 0
            or foreground > SPATIAL_CONFIGURATION["dense_foreground_fraction"] * raster["width"] * raster["height"]
            or any(work[key] is None for key in _WORK_KEYS - {"pair_tests"})
            or n > work["residual_component_count"]
            or foreground < (SPATIAL_CONFIGURATION["glyph_min_area_pixels"] * n
                             + work["rule_component_count"] + work["residual_component_count"] - n)
            or work["rule_component_count"] + work["residual_component_count"] > SPATIAL_CONFIGURATION["max_components_per_page"]
            or any(work[key] > SPATIAL_CONFIGURATION["max_horizontal_runs_per_mask"]
                   for key in ("rule_horizontal_runs", "residual_horizontal_runs"))):
        raise ValueError("spatial enumeration precedes component admission")
    entries, lookups, visits = (work[key] for key in _SPATIAL_COUNTERS)
    pairs, possible_pairs = work["pair_tests"], n * (n - 1) // 2
    if (entries > 10 * n or lookups > 14 * n or visits > 10 * possible_pairs
            or pairs > min(possible_pairs, visits) or (lookups == 0 and visits != 0)):
        raise ValueError("spatial work exceeds its geometric bound")
    if phase == "indexing":
        if lookups or visits or pairs:
            raise ValueError("spatial query precedes a completed index")
    elif entries < n:
        raise ValueError("spatial index is incomplete")
    if phase in {"grouping", "complete"} and lookups < 3 * n:
        raise ValueError("spatial grouping precedes completed enumeration")
    if status == "available":
        if phase != "complete":
            raise ValueError("available spatial discovery requires completed grouping")
        _spatial_partition(value)
    elif status != "unavailable" or phase == "complete":
        raise ValueError("spatial completion contradicts discovery status")
    if reason == "component_pair_limit":
        if (phase != "querying" or pairs != SPATIAL_CONFIGURATION["max_pairs_per_page"]
                or possible_pairs <= pairs or visits <= pairs):
            raise ValueError("spatial predicate rejection lacks exhausted work")
    elif reason == "bucket_visit_limit":
        if (phase != "querying" or visits != SPATIAL_CONFIGURATION["max_bucket_visits_per_page"]
                or 10 * possible_pairs <= visits):
            raise ValueError("spatial bucket rejection lacks exhausted work")
    elif reason == "region_limit":
        if (phase != "grouping" or work["rule_component_count"] + work["residual_component_count"]
                <= SPATIAL_CONFIGURATION["max_regions_per_page"]):
            raise ValueError("spatial region rejection contradicts completed enumeration")
    elif status == "unavailable" and reason != "stage_failed":
        raise ValueError("spatial failure reason contradicts its enumeration stage")


def validate_scan_observation(payload):
    """Validate bounded declared observations; this does not authenticate pixels."""
    value = _fields(payload, {"schema_version", "kind", "source_sha256", "page_count",
                              "requested_pages", "configuration", "pages"})
    if (type(value["schema_version"]) is not int or value["schema_version"] not in (1, 2)
            or type(value["kind"]) is not str or value["kind"] != "ocr_scan_observation"):
        raise ValueError("unsupported scan observation schema")
    _digest(value["source_sha256"])
    count = _integer(value["page_count"], 1, 5000)
    requested = requested_scan_pages(value["requested_pages"], count)
    recipe = recipe_for_configuration(value["configuration"])
    if requested != value["requested_pages"] or value["schema_version"] != _RECIPE_SCHEMAS[recipe]:
        raise ValueError("scan observation differs from its fixed recipe or page order")
    pages = value["pages"]
    if type(pages) is not list or len(pages) != len(requested):
        raise ValueError("scan requested-page coverage is incomplete")
    pixels, pairs = 0, 0
    spatial_totals = dict.fromkeys(_SPATIAL_COUNTERS, 0)
    for number, page in zip(requested, pages):
        _fields(page, {"page_number", "geometry", "raster", "discovery"})
        if type(page["page_number"]) is not int or page["page_number"] != number:
            raise ValueError("scan page identity differs from requested cohort")
        if page["geometry"] is not None:
            _geometry(page["geometry"])
        if page["raster"] is not None:
            pixels += _raster(page["raster"], page["geometry"])
        if recipe == "spatial-v2":
            _spatial_discovery(page["discovery"], page["raster"], page["geometry"])
            for key in spatial_totals:
                spatial_totals[key] += page["discovery"]["work"][key]
        else:
            _discovery(page["discovery"], page["raster"], page["geometry"])
        pairs += page["discovery"]["work"]["pair_tests"]
    if pixels > DEFAULT_CONFIGURATION["max_total_pixels"] or pairs > DEFAULT_CONFIGURATION["max_total_pairs"]:
        raise ValueError("scan aggregate work exceeds its recipe")
    if any(spatial_totals[key] > SPATIAL_CONFIGURATION["max_total_" + name]
           for key, name in _SPATIAL_COUNTERS.items()):
        raise ValueError("scan aggregate spatial work exceeds its recipe")
    if any(page["discovery"]["reason"] == "cohort_pixel_limit" for page in pages) and pixels:
        raise ValueError("cohort raster rejection must precede every render")
    _encoded_bound(value, MAX_OBSERVATION_BYTES)
    return copy.deepcopy(value)


def _overlap(first, second):
    return not (first[0] - second[2] > 1e-12 or second[0] - first[2] > 1e-12
                or first[1] - second[3] > 1e-12 or second[1] - first[3] > 1e-12)


def _contains(outer, inner):
    return outer[0] <= inner[0] and outer[1] <= inner[1] and inner[2] <= outer[2] and inner[3] <= outer[3]


def _correspondence(page, candidate, proposal, has_recovery, has_proposals):
    if not has_recovery:
        return "not_supplied", "not_supplied", [], []
    if candidate is None:
        return "candidate_unavailable", "candidate_unavailable" if has_proposals else "not_supplied", [], []
    if candidate["raster"]["coordinate_system"] != "rendered_image_pixels":
        return "unsupported_preprocessing", "unsupported_preprocessing" if has_proposals else "not_supplied", [], []
    geometry, raster = page["geometry"], candidate["raster"]
    if geometry is None or any(abs(raster[axis] - geometry[points] * raster["dpi"] / 72) > 2
                               for axis, points in (("width", "width_points"), ("height", "height_points"))):
        return "geometry_mismatch", "geometry_mismatch" if has_proposals else "not_supplied", [], []
    if len(candidate["lines"]) > MAX_LINES:
        return "line_limit", "line_limit" if has_proposals else "not_supplied", [], []
    lines = []
    for line in candidate["lines"]:
        box = line["box"]
        left, top, right, bottom = min(p[0] for p in box), min(p[1] for p in box), max(p[0] for p in box), max(p[1] for p in box)
        # A slanted quadrilateral's bounding envelope is not its interior.
        # Do not reuse the reading-order helper's intentional slope tolerance.
        rectangular = set(map(tuple, box)) == {(left, top), (right, top), (right, bottom), (left, bottom)}
        bounds = [left / raster["width"], top / raster["height"], right / raster["width"], bottom / raster["height"]]
        lines.append((bounds, rectangular, bool(line["text"].strip())))
    if not has_proposals:
        layout_state, regions = "not_supplied", []
    elif proposal is None or proposal["page_geometry"] is None or set(proposal["reasons"]) & {
            "missing_page_geometry", "page_geometry_mismatch", "effective_input_preprocessed", "candidate_preprocessed"}:
        layout_state, regions = "geometry_unavailable", []
    elif len(proposal["regions"]) > MAX_LAYOUT_REGIONS:
        layout_state, regions = "region_limit", []
    else:
        layout_state, regions = "supported", [region["bbox"] for region in proposal["regions"]]
    return "supported", layout_state, lines, regions


def _compare_region(region, raster, ocr_state, layout_state, lines, saved_regions):
    box = [coordinate / (raster["width"] if index % 2 == 0 else raster["height"])
           for index, coordinate in enumerate(region["bbox"])]
    ocr_counts = dict.fromkeys(("containing_nonempty_boxes", "partial_or_ambiguous_nonempty_boxes", "empty_text_boxes"))
    if ocr_state == "supported":
        ocr_counts = dict.fromkeys(ocr_counts, 0)
        for bounds, rectangular, nonempty in lines:
            if _overlap(bounds, box):
                key = ("empty_text_boxes" if not nonempty else "containing_nonempty_boxes"
                       if rectangular and _contains(bounds, box) else "partial_or_ambiguous_nonempty_boxes")
                ocr_counts[key] += 1
        ocr_status = ("contained_in_nonempty_line_box" if ocr_counts["containing_nonempty_boxes"] else
                      "partial_or_ambiguous_overlap" if ocr_counts["partial_or_ambiguous_nonempty_boxes"] else
                      "empty_text_overlap_only" if ocr_counts["empty_text_boxes"] else "no_line_overlap")
    else:
        ocr_status = "unevaluated"
    layout_counts = dict.fromkeys(("containing_regions", "partial_regions"))
    if layout_state == "supported":
        layout_counts = dict.fromkeys(layout_counts, 0)
        for bounds in saved_regions:
            if _overlap(bounds, box):
                layout_counts["containing_regions" if _contains(bounds, box) else "partial_regions"] += 1
        layout_status = ("contained_in_saved_region" if layout_counts["containing_regions"] else
                         "partial_overlap" if layout_counts["partial_regions"] else "no_saved_region_overlap")
    else:
        layout_status = "unevaluated"
    text_like = region["kind"] == "text_like"
    return {"region_id": region["region_id"], "kind": region["kind"], "bbox": box,
            "ocr_status": ocr_status, "ocr_overlap_counts": ocr_counts,
            "layout_status": layout_status, "layout_overlap_counts": layout_counts,
            "potential_ocr_omission": text_like and ocr_status in {"no_line_overlap", "empty_text_overlap_only"},
            "unmatched_in_both_saved_inputs": text_like and ocr_status == "no_line_overlap"
                and layout_status == "no_saved_region_overlap",
            "semantic_role": "unknown", "transcription_verified": False}


def build_scan_omission_report(observation, *, recovery=None, recovery_sha256=None, proposals=None, proposals_sha256=None):
    """Compare already discovered regions; saved boxes never seed/mask discovery."""
    observed = validate_scan_observation(observation)
    if (recovery is None) != (recovery_sha256 is None) or (proposals is None) != (proposals_sha256 is None):
        raise ValueError("scan comparison input and digest must be supplied together")
    if proposals is not None and recovery is None:
        raise ValueError("saved proposals require their bound recovery")
    if recovery is not None:
        _digest(recovery_sha256)
        recovery = validate_recovery_report(recovery)
        if recovery["source_sha256"] != observed["source_sha256"] or recovery["page_count"] != observed["page_count"]:
            raise ValueError("scan and recovery source generations differ")
    if proposals is not None:
        _digest(proposals_sha256)
        proposals = validate_docling_proposals(proposals, recovery=recovery, recovery_sha256=recovery_sha256)
    selected = {page["page_number"]: page for page in (recovery or {}).get("pages", [])}
    deferred = {page["page_number"] for page in (recovery or {}).get("deferred_pages", [])}
    saved = {page["page_number"]: page for page in (proposals or {}).get("pages", [])}
    plans, comparisons = [], 0
    for page in observed["pages"]:
        number = page["page_number"]
        candidate = selected.get(number, {}).get("candidate")
        state = ("not_supplied" if recovery is None else ("available" if candidate["text"].strip() else "empty")
                 if candidate is not None else "retry_failed" if number in selected else
                 "deferred" if number in deferred else "not_selected")
        ocr_state, layout_state, lines, regions = _correspondence(page, candidate, saved.get(number), recovery is not None, proposals is not None)
        if page["discovery"]["status"] == "unavailable":
            ocr_state = layout_state = "scan_unavailable"
            lines, regions = [], []
        comparisons += len(page["discovery"]["regions"]) * (len(lines) + len(regions))
        plans.append((page, state, ocr_state, layout_state, lines, regions))
    pages = []
    for page, state, ocr_state, layout_state, lines, regions in plans:
        if comparisons > MAX_COMPARISONS:
            ocr_state = "work_limit" if ocr_state == "supported" else ocr_state
            layout_state = "work_limit" if layout_state == "supported" else layout_state
            lines, regions = [], []
        pages.append({"page_number": page["page_number"], "scan_status": page["discovery"]["status"],
                      "scan_reason": page["discovery"]["reason"], "candidate_state": state,
                      "ocr_comparison_state": ocr_state, "layout_comparison_state": layout_state,
                      "regions": [_compare_region(region, page["raster"], ocr_state, layout_state, lines, regions)
                                  for region in page["discovery"]["regions"]]})
    flattened = [region for page in pages for region in page["regions"]]
    requested = observed["requested_pages"]
    return_value = {
        "schema_version": 1, "kind": "ocr_scan_omission_diagnostics", "source_sha256": observed["source_sha256"],
        "recovery_sha256": recovery_sha256, "proposals_sha256": proposals_sha256,
        "observation_content_sha256": hashlib.sha256(_OBSERVATION_DIGEST["domain"].encode("ascii") + b"\0" + _canonical(observed)).hexdigest(),
        "observation_digest_policy": dict(_OBSERVATION_DIGEST), "coordinate_system": "original_page_display_fraction",
        "parameters": {"max_geometry_comparisons": MAX_COMPARISONS, "max_ocr_lines_per_page": MAX_LINES,
                       "max_saved_regions_per_page": MAX_LAYOUT_REGIONS, "geometry_rounding_tolerance_pixels": 2,
                       "overlap_roundoff_fraction": 1e-12, "containment_tolerance": 0,
                       "ocr_containment_shape": "exact_axis_aligned_rectangle_only"},
        "coverage": {"source_pages": observed["page_count"], "requested_pages": list(requested),
                     "unrequested_pages": [number for number in range(1, observed["page_count"] + 1) if number not in requested]},
        "pages": pages,
        "summary": {"requested_pages": len(pages),
                    **{status + "_pages": sum(page["scan_status"] == status for page in pages) for status in _STATUS_REASON},
                    **{kind + "_regions": sum(region["kind"] == kind for region in flattened) for kind in _REGION_REASONS},
                    "potential_ocr_omissions": sum(region["potential_ocr_omission"] for region in flattened),
                    "unmatched_in_both_saved_inputs": sum(region["unmatched_in_both_saved_inputs"] for region in flattened),
                    "ocr_unevaluated_regions": sum(region["ocr_status"] == "unevaluated" for region in flattened),
                    "layout_unevaluated_regions": sum(region["layout_status"] == "unevaluated" for region in flattened)},
        "scope": "Declared independent dark-ink observations and saved-box correspondence; not authenticated pixels, semantic text detection, or complete transcription.",
        **_ATTESTATIONS,
    }
    _encoded_bound(return_value, MAX_REPORT_BYTES)
    return return_value


def validate_scan_omission_report(payload, observation, *, recovery=None, recovery_sha256=None, proposals=None, proposals_sha256=None):
    expected = build_scan_omission_report(observation, recovery=recovery, recovery_sha256=recovery_sha256,
                                         proposals=proposals, proposals_sha256=proposals_sha256)
    if not _exact(payload, expected):
        raise ValueError("scan diagnostic differs from fixed observation and saved inputs")
    return expected
