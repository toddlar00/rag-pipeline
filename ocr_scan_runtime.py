"""Bounded model-free hypotheses from original source pixels, never text proof.

Optional native libraries are loaded only by collection. Discovery receives
gray pixels alone: no saved OCR, layout, reference, or comparison can guide it.
"""
from __future__ import annotations

import bisect
import hashlib
import json
import math

from ocr_scan_omission import configuration_for_recipe, validate_scan_observation


MAX_SOURCE_BYTES = 256 * 1024 * 1024


class _Limit(Exception):
    """An internal fixed-label abstention, not an arbitrary native error."""


def _pixel_hash(gray, configuration):
    digest = hashlib.sha256()
    digest.update(configuration["pixel_digest_domain"].encode("ascii") + b"\0")
    digest.update(json.dumps(list(gray.shape), separators=(",", ":")).encode("ascii") + b"\0")
    digest.update(memoryview(gray))
    return digest.hexdigest()


def _empty(reason, *, spatial=False):
    result = {"status": "unavailable", "reason": reason, "threshold": None,
            "threshold_foreground_pixels": None, "foreground_accounting_complete": False,
            "regions": [], "work": {"rule_horizontal_runs": None, "residual_horizontal_runs": None,
                                     "rule_component_count": None, "residual_component_count": None,
                                     "pair_tests": 0}}
    if spatial:
        result["work"].update(glyph_count=None, spatial_phase="not_started", spatial_index_entries=0,
                              spatial_bucket_lookups=0, spatial_bucket_visits=0)
    return result


def _components(mask, *, name, work, configuration, cv2, np):
    runs = int(np.count_nonzero(mask[:, 0]))
    runs += int(np.count_nonzero((mask[:, 1:] != 0) & (mask[:, :-1] == 0)))
    work[name + "_horizontal_runs"] = runs
    # Every connected foreground component contains at least one horizontal
    # run. Bound native label-statistics allocation BEFORE calling OpenCV.
    if runs > configuration["max_horizontal_runs_per_mask"]:
        raise _Limit("horizontal_run_limit")
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(mask, connectivity=8, ltype=cv2.CV_32S)
    if (type(count) is not int or not 1 <= count <= runs + 1
            or labels.shape != mask.shape or labels.dtype != np.int32
            or stats.shape != (count, 5) or stats.dtype != np.int32
            or centroids.shape != (count, 2)):
        raise ValueError("invalid component output")
    del labels, centroids
    work[name + "_component_count"] = count - 1
    if count - 1 > configuration["max_components_per_page"]:
        raise _Limit("component_limit")
    rows = [tuple(map(int, row)) for row in stats[1:]]
    height, width = mask.shape
    if any(not (0 <= x < x + w <= width and 0 <= y < y + h <= height and 0 < area <= w * h)
           for x, y, w, h, area in rows):
        raise ValueError("invalid component statistics")
    if sum(row[4] for row in rows) != int(np.count_nonzero(mask)):
        raise ValueError("component support differs from foreground")
    rows.sort(key=lambda row: (row[1], row[0], row[3], row[2], row[4]))
    return rows


def _region(kind, rows, reason):
    return {"kind": kind, "reason": reason,
            "bbox": [min(row[0] for row in rows), min(row[1] for row in rows),
                     max(row[0] + row[2] for row in rows), max(row[1] + row[3] for row in rows)],
            "component_count": len(rows), "foreground_pixels": sum(row[4] for row in rows)}


def _spatial_pairs(rows, *, configuration, work):
    """Canonical conservative candidates with bounded retained native-free work.

    Closed endpoints retain inclusive threshold ties. Each glyph occupies at
    most ten cells; its query occupies at most fourteen. Each i<j pair can
    therefore contribute at most ten bucket-entry visits.
    """
    cell_size = configuration["spatial_cell_pixels"]
    extension = configuration["neighbor_gap_in_max_heights"] * configuration["glyph_max_height_pixels"]
    if len(rows) > configuration["max_components_per_page"]:
        raise ValueError("spatial component bound violated")

    def cells(left, top, right, bottom):
        for cy in range(math.floor(top / cell_size), math.floor(bottom / cell_size) + 1):
            for cx in range(math.floor(left / cell_size), math.floor(right / cell_size) + 1):
                yield cx, cy

    index = {}
    work["spatial_phase"] = "indexing"
    for i, row in enumerate(rows):
        if type(row) is not tuple or len(row) != 5 or any(type(value) is not int for value in row):
            raise ValueError("invalid spatial glyph")
        x, y, width, height, area = row
        if not (0 <= x < x + width <= configuration["max_side"]
                and 0 <= y < y + height <= configuration["max_side"]
                and configuration["glyph_min_height_pixels"] <= height <= configuration["glyph_max_height_pixels"]
                and width <= configuration["glyph_max_width_to_height"] * height
                and configuration["glyph_min_area_pixels"] <= area <= width * height):
            raise ValueError("spatial glyph proof assumptions violated")
        occupied = tuple(cells(x, y, x + width, y + height))
        if len(occupied) > 10:
            raise ValueError("spatial index proof bound violated")
        for cell in occupied:
            if work["spatial_index_entries"] >= configuration["max_index_entries_per_page"]:
                raise ValueError("spatial index bound violated")
            work["spatial_index_entries"] += 1
            index.setdefault(cell, []).append(i)
    work["spatial_phase"] = "querying"
    for i, (x, y, width, height, _) in enumerate(rows):
        queried = tuple(cells(x - extension, y, x + width + extension, y + height))
        if len(queried) > 14:
            raise ValueError("spatial query proof bound violated")
        seen = set()
        for cell in queried:
            if work["spatial_bucket_lookups"] >= configuration["max_bucket_lookups_per_page"]:
                raise ValueError("spatial query bound violated")
            work["spatial_bucket_lookups"] += 1
            bucket = index.get(cell, ())
            for position in range(bisect.bisect_right(bucket, i), len(bucket)):
                if work["spatial_bucket_visits"] >= configuration["max_bucket_visits_per_page"]:
                    raise _Limit("bucket_visit_limit")
                work["spatial_bucket_visits"] += 1
                seen.add(bucket[position])
        for j in sorted(seen):
            xx, yy, other_width, other_height, _ = rows[j]
            if min(y + height, yy + other_height) <= max(y, yy):
                continue
            if max(x, xx) - min(x + width, xx + other_width) > extension:
                continue
            yield i, j
    work["spatial_phase"] = "grouping"


def _discover(gray, *, configuration, cv2, np):
    """No contextual text or geometry is supplied to this pixel-only policy."""
    spatial = configuration["algorithm"] == "dark-ink-spatial-component-hypotheses-v2"
    result = _empty("stage_failed", spatial=spatial)
    work = result["work"]
    before = _pixel_hash(gray, configuration)
    try:
        threshold, mask = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
        if (type(threshold) not in (int, float) or not math.isfinite(threshold) or not 0 <= threshold <= 255
                or mask.shape != gray.shape or mask.dtype != np.uint8):
            raise ValueError("invalid threshold output")
        foreground = int(np.count_nonzero(mask))
        result.update(threshold=float(threshold), threshold_foreground_pixels=foreground)
        height, width = map(int, gray.shape)
        if foreground == 0:
            result.update(status="blank_at_threshold", reason="no_dark_otsu_foreground",
                          foreground_accounting_complete=True)
        elif foreground / (width * height) > configuration["dense_foreground_fraction"]:
            result.update(status="ambiguous", reason="dense_foreground_not_text_classified",
                foreground_accounting_complete=True, regions=[{
                    "region_id": "scan-00001", "kind": "ambiguous_ink", "reason": "dense_foreground",
                    "bbox": [0, 0, width, height], "component_count": None, "foreground_pixels": foreground}])
        else:
            length = configuration["rule_length_pixels"]
            horizontal = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((1, length), np.uint8))
            vertical = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((length, 1), np.uint8))
            rule_mask = cv2.bitwise_and(cv2.bitwise_or(horizontal, vertical), mask)
            residual = cv2.bitwise_and(mask, cv2.bitwise_not(rule_mask))
            del horizontal, vertical
            rules = _components(rule_mask, name="rule", work=work, configuration=configuration, cv2=cv2, np=np)
            ink = _components(residual, name="residual", work=work, configuration=configuration, cv2=cv2, np=np)
            if len(rules) + len(ink) > configuration["max_components_per_page"]:
                raise _Limit("component_limit")
            regions = [_region("rule_like", [row], "long_axis_morphology_not_semantic_nontext") for row in rules]
            glyphs, other = [], []
            for row in ink:
                _, _, w, h, area = row
                if (configuration["glyph_min_height_pixels"] <= h <= configuration["glyph_max_height_pixels"]
                        and w <= configuration["glyph_max_width_to_height"] * h
                        and area >= configuration["glyph_min_area_pixels"]):
                    glyphs.append(row)
                else:
                    other.append(row)
            if spatial:
                work["glyph_count"] = len(glyphs)
                work["spatial_phase"] = "indexing"
            elif len(glyphs) * (len(glyphs) - 1) // 2 > configuration["max_pairs_per_page"]:
                raise _Limit("component_pair_limit")
            parent = list(range(len(glyphs)))

            def find(index):
                while parent[index] != index:
                    parent[index] = parent[parent[index]]
                    index = parent[index]
                return index

            if spatial:
                for i, j in _spatial_pairs(glyphs, configuration=configuration, work=work):
                    if work["pair_tests"] >= configuration["max_pairs_per_page"]:
                        raise _Limit("component_pair_limit")
                    work["pair_tests"] += 1
                    x, y, w, h, _ = glyphs[i]
                    xx, yy, ww, hh, _ = glyphs[j]
                    if max(h, hh) > configuration["glyph_max_height_ratio"] * min(h, hh):
                        continue
                    overlap = min(y + h, yy + hh) - max(y, yy)
                    gap = max(x, xx) - min(x + w, xx + ww)
                    if (overlap >= configuration["minimum_vertical_overlap"] * min(h, hh)
                            and gap <= configuration["neighbor_gap_in_max_heights"] * max(h, hh)):
                        a, b = find(i), find(j)
                        if a != b:
                            parent[b] = a
            else:
                for i, left in enumerate(glyphs):
                    for j in range(i + 1, len(glyphs)):
                        work["pair_tests"] += 1
                        x, y, w, h, _ = left
                        xx, yy, ww, hh, _ = glyphs[j]
                        if max(h, hh) > configuration["glyph_max_height_ratio"] * min(h, hh):
                            continue
                        overlap = min(y + h, yy + hh) - max(y, yy)
                        gap = max(x, xx) - min(x + w, xx + ww)
                        if (overlap >= configuration["minimum_vertical_overlap"] * min(h, hh)
                                and gap <= configuration["neighbor_gap_in_max_heights"] * max(h, hh)):
                            a, b = find(i), find(j)
                            if a != b:
                                parent[b] = a
            groups = {}
            for index, row in enumerate(glyphs):
                groups.setdefault(find(index), []).append(row)
            for rows in groups.values():
                item = _region("ambiguous_ink", rows, "isolated_or_non_line_components")
                left, top, right, bottom = item["bbox"]
                if (len(rows) >= configuration["minimum_group_components"]
                        and right - left >= configuration["minimum_group_width_to_height"] * (bottom - top)):
                    item.update(kind="text_like", reason="aligned_glyph_scale_components_not_text_proof")
                regions.append(item)
            regions.extend(_region("ambiguous_ink", [row], "tiny_or_large_residual_component") for row in other)
            if len(regions) > configuration["max_regions_per_page"]:
                raise _Limit("region_limit")
            regions.sort(key=lambda item: (item["bbox"][1], item["bbox"][0], item["kind"], item["bbox"][3:]))
            for number, item in enumerate(regions, 1):
                item["region_id"] = f"scan-{number:05d}"
            if sum(item["foreground_pixels"] for item in regions) != foreground:
                raise ValueError("foreground accounting differs")
            if spatial:
                work["spatial_phase"] = "complete"
            result.update(status="available", reason="bounded_pixel_discovery",
                          foreground_accounting_complete=True, regions=regions)
    except _Limit as error:
        result.update(status="unavailable", reason=str(error), regions=[], foreground_accounting_complete=False)
    except Exception:
        result.update(status="unavailable", reason="stage_failed", regions=[], foreground_accounting_complete=False)
    if _pixel_hash(gray, configuration) != before:
        raise ValueError("scan discovery changed source raster")
    return result


def _finite(value):
    if type(value) not in (int, float):
        raise ValueError("invalid displayed geometry")
    try:
        result = float(value)
    except (ValueError, OverflowError):
        raise ValueError("invalid displayed geometry") from None
    if not math.isfinite(result) or not -10_000_000 <= result <= 10_000_000:
        raise ValueError("invalid displayed geometry")
    return result


def collect_scan_observation(source: bytes, *, requested_pages: list[int], recipe: str = "legacy-v1") -> dict:
    """Observe a bounded explicit cohort from immutable original PDF bytes.

    Invalid/encrypted source admission fails without fabricating a page count.
    Once the count is known, each requested page is retained, even when geometry,
    rendering or component work is unavailable. No OCR or reference is accepted.
    """
    configuration = configuration_for_recipe(recipe)
    spatial = recipe == "spatial-v2"
    if type(source) is not bytes or not 0 < len(source) <= MAX_SOURCE_BYTES:
        raise ValueError("scan source must be bounded immutable bytes")
    if (type(requested_pages) is not list or not 1 <= len(requested_pages) <= 8
            or any(type(number) is not int or not 1 <= number <= 5000 for number in requested_pages)
            or len(set(requested_pages)) != len(requested_pages)):
        raise ValueError("scan requires a bounded explicit page cohort")
    import cv2
    import numpy as np
    import pymupdf

    requested = sorted(requested_pages)
    plans, total_pixels = [], 0
    try:
        document = pymupdf.open(stream=source, filetype="pdf")
    except Exception:
        raise ValueError("scan source cannot be opened") from None
    with document:
        page_count = len(document)
        if document.needs_pass or not 1 <= page_count <= 5000 or requested[-1] > page_count:
            raise ValueError("scan source or requested pages are unsupported")
        matrix = pymupdf.Matrix(configuration["dpi"] / 72, configuration["dpi"] / 72)
        for number in requested:
            plan = {"page_number": number, "geometry": None, "page": None, "size": None,
                    "reason": "geometry_unavailable"}
            try:
                page = document[number - 1]
                width_points, height_points = _finite(page.rect.width), _finite(page.rect.height)
                cropbox = [_finite(value) for value in page.cropbox]
                rotation = page.rotation
                if (width_points < 1e-6 or height_points < 1e-6 or page.rect.x0 != 0 or page.rect.y0 != 0
                        or type(rotation) is not int or rotation not in (0, 90, 180, 270)
                        or len(cropbox) != 4 or not cropbox[0] < cropbox[2] or not cropbox[1] < cropbox[3]):
                    raise ValueError("unsupported displayed geometry")
                plan["geometry"] = {"width_points": width_points, "height_points": height_points,
                                    "rotation": rotation, "cropbox": cropbox}
                rect = (page.rect * matrix).irect
                width, height = rect.width, rect.height
                if (rect.x0 != 0 or rect.y0 != 0 or not 1 <= min(width, height)
                        or max(width, height) > configuration["max_side"]
                        or width * height > configuration["max_page_pixels"]):
                    plan["reason"] = "raster_limit"
                else:
                    total_pixels += width * height
                    plan.update(page=page, size=(width, height), reason=None)
            except Exception:
                pass
            plans.append(plan)
        cohort_limit = total_pixels > configuration["max_total_pixels"]
        pages = []
        for plan in plans:
            reason = "cohort_pixel_limit" if cohort_limit else plan["reason"]
            result = {"page_number": plan["page_number"], "geometry": plan["geometry"], "raster": None,
                      "discovery": _empty(reason or "render_failed", spatial=spatial)}
            if reason is None:
                try:
                    width, height = plan["size"]
                    pixmap = plan["page"].get_pixmap(matrix=matrix, colorspace=pymupdf.csGRAY, alpha=False,
                                                   annots=configuration["render_annotations"])
                    if (pixmap.x != 0 or pixmap.y != 0 or pixmap.width != width or pixmap.height != height
                            or pixmap.n != 1 or pixmap.stride != width or len(pixmap.samples_mv) != width * height):
                        raise ValueError("scan renderer returned unsupported raster")
                    gray = np.frombuffer(pixmap.samples_mv, dtype=np.uint8).reshape(height, width).copy()
                    result["raster"] = {"width": width, "height": height, "dpi": configuration["dpi"],
                                        "format": "gray8", "pixel_sha256": _pixel_hash(gray, configuration)}
                except Exception:
                    pass
                else:
                    try:
                        result["discovery"] = _discover(gray, configuration=configuration, cv2=cv2, np=np)
                    except Exception:
                        result["discovery"] = _empty("stage_failed", spatial=spatial)
            pages.append(result)
    report = {"schema_version": 2 if spatial else 1, "kind": "ocr_scan_observation", "source_sha256": hashlib.sha256(source).hexdigest(),
              "page_count": page_count, "requested_pages": requested, "configuration": configuration, "pages": pages}
    return validate_scan_observation(report)
