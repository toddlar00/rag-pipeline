"""Occurrence-bound heading lineage for serialized Docling documents.

The module is deliberately standard-library-only.  Heading identity is an
exact Docling ``self_ref``; normalized text is never used as an identifier.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from typing import Iterable, Sequence


HEADING_LINEAGE_SCHEMA_VERSION = 3
HEADING_LINEAGE_POLICY = "docling-heading-occurrence-v5"
HEADING_PATH_FIELD = "heading_path_ids"
DIRECT_HEADING_FIELD = "direct_heading_ids"
HEADING_COMPONENTS_FIELD = "heading_path_components"
HEADING_SCHEMA_FIELD = "heading_lineage_schema_version"
RUNNING_FURNITURE_REASON = "running_page_furniture"
EMBEDDED_OUTLINE_REASON = "embedded_outline_entry"
SPARSE_OCR_HEADING_ARTIFACT_REASON = "sparse_ocr_heading_artifact"

_COLLECTIONS = (
    "texts", "tables", "pictures", "key_value_items", "form_items",
)
_BODY_CONTENT_LABELS = {
    "text", "list_item", "footnote", "caption", "code", "table",
    "picture", "document_index",
}
_CANONICAL_RE = re.compile(r"[^a-z0-9]+")
_CASE_TITLE_RE = re.compile(
    r"(?:\bv(?:s)?\.?\b|^in\s+re\b|^ex\s+parte\b)", re.IGNORECASE)
_MAX_CASE_ANNOTATION_SOURCE_DISTANCE = 32


def canonical_text(value: object) -> str:
    """Return a comparison key, never an occurrence identity."""
    return _CANONICAL_RE.sub("", str(value or "").casefold())


def _label(item: object) -> str:
    if not isinstance(item, dict):
        return ""
    value = item.get("label")
    if isinstance(value, dict):
        value = value.get("value")
    return str(value or "").strip().casefold()


def _text(item: object) -> str:
    if not isinstance(item, dict):
        return ""
    return str(item.get("text") or item.get("orig") or "")


def _ref(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    return str(value.get("self_ref") or "")


def _child_ref(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    return str(value.get("cref") or "")


def _page_size(document: dict, page: int, dimension: str) -> float | None:
    pages = document.get("pages") if isinstance(document, dict) else None
    value = None
    if isinstance(pages, dict):
        value = pages.get(str(page), pages.get(page))
    elif isinstance(pages, list) and 0 < page <= len(pages):
        value = pages[page - 1]
    size = value.get("size") if isinstance(value, dict) else None
    candidate = size.get(dimension) if isinstance(size, dict) else None
    try:
        result = float(candidate)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) and result > 0 else None


def _provenance_boxes(
        item: object,
) -> list[tuple[int, float, float, float, float]]:
    """Return ``page, top, bottom, left, right`` in top-origin order space."""
    if not isinstance(item, dict):
        return []
    result = []
    for span in item.get("prov") or []:
        if not isinstance(span, dict):
            return []
        page = span.get("page_no")
        bbox = span.get("bbox")
        if (not isinstance(page, int) or isinstance(page, bool)
                or not isinstance(bbox, dict)):
            return []
        try:
            left, right = sorted((float(bbox.get("l")), float(bbox.get("r"))))
            first, second = sorted((float(bbox.get("t")), float(bbox.get("b"))))
        except (TypeError, ValueError):
            return []
        origin = str(bbox.get("coord_origin") or "").upper()
        # A negative vertical axis preserves physical top-to-bottom ordering
        # for BOTTOMLEFT without requiring page dimensions.
        top, bottom = (
            (-second, -first) if origin == "BOTTOMLEFT" else (first, second)
        )
        if not all(math.isfinite(number) for number in (
                left, top, right, bottom)):
            return []
        result.append((page, top, bottom, left, right))
    return result


def _first_page(item: object) -> int | None:
    boxes = _provenance_boxes(item)
    return boxes[0][0] if boxes else None


def _inside_ranges(item: object, ranges: Sequence[tuple[int, int]]) -> bool:
    boxes = _provenance_boxes(item)
    return bool(boxes) and all(
        any(start <= page <= end for start, end in ranges)
        for page in {box[0] for box in boxes}
    )


def _catalog(document: dict) -> tuple[dict[str, dict], dict[str, dict]]:
    items: dict[str, dict] = {}
    for collection in _COLLECTIONS:
        for item in document.get(collection) or []:
            ref = _ref(item)
            if ref and isinstance(item, dict):
                items[ref] = item
    groups = {
        _ref(group): group for group in document.get("groups") or []
        if _ref(group) and isinstance(group, dict)
    }
    return items, groups


def _clean_marker(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().strip("*_ ")


def _ordered_source_refs(
        document: dict, items: dict[str, dict], groups: dict[str, dict],
        ranges: Sequence[tuple[int, int]],
) -> list[str]:
    """Flatten body order after the pipeline's bounded wrap repair.

    Only one strongly attested vertical wrap is repaired on a page, and only
    when the runs are horizontally aligned (or begin with a centered outline
    marker).  This mirrors the mutation applied to the live Docling model
    before chunking while retaining true multi-column order.
    """
    body = document.get("body") if isinstance(document, dict) else None
    children = list(body.get("children") or []) if isinstance(body, dict) else []
    descriptors: dict[
        str, tuple[int, float, float, float, float, str] | None
    ] = {}

    def descriptor(
            ref: str, active: frozenset[str] = frozenset(),
    ) -> tuple[int, float, float, float, float, str] | None:
        if ref in descriptors:
            return descriptors[ref]
        if not ref or ref in active:
            return None
        item = items.get(ref)
        if item is not None:
            boxes = _provenance_boxes(item)
            if not boxes or len({box[0] for box in boxes}) != 1:
                descriptors[ref] = None
                return None
            result = (
                boxes[0][0], min(box[1] for box in boxes),
                max(box[2] for box in boxes), min(box[3] for box in boxes),
                max(box[4] for box in boxes), _text(item),
            )
            descriptors[ref] = result
            return result
        group = groups.get(ref)
        child_refs = [
            _child_ref(child) for child in (group or {}).get("children") or []
            if _child_ref(child)
        ]
        values = [
            descriptor(child_ref, active | frozenset((ref,)))
            for child_ref in child_refs
        ]
        if (not values or any(value is None for value in values)
                or len({value[0] for value in values if value}) != 1):
            descriptors[ref] = None
            return None
        members = [value for value in values if value is not None]
        result = (
            members[0][0], min(value[1] for value in members),
            max(value[2] for value in members),
            min(value[3] for value in members),
            max(value[4] for value in members),
            " ".join(value[5] for value in members if value[5]),
        )
        descriptors[ref] = result
        return result

    by_page: defaultdict[int, list[tuple[int, dict, tuple]]] = defaultdict(list)
    for index, child in enumerate(children):
        ref = _child_ref(child)
        value = descriptor(ref)
        item = items.get(ref)
        if (value is not None
                and (item is None or _label(item) not in {
                    "page_header", "page_footer"})):
            by_page[value[0]].append((index, child, value))

    for page, nodes in sorted(by_page.items()):
        if (any(start <= page <= end for start, end in ranges)
                or len(nodes) < 4):
            continue
        page_height = _page_size(document, page, "height")
        page_width = _page_size(document, page, "width")
        if page_height is None or page_width is None:
            continue
        jump_threshold = max(140.0, page_height * 0.35)

        def attested_wrap(left_node: tuple, right_node: tuple) -> bool:
            left, right = left_node[2], right_node[2]
            if left[1] - right[1] < jump_threshold:
                return False
            overlap = max(0.0, min(left[4], right[4]) - max(left[3], right[3]))
            narrower = min(left[4] - left[3], right[4] - right[3])
            aligned = narrower > 0 and overlap / narrower >= 0.50
            marker = _clean_marker(right[5])
            center = (right[3] + right[4]) / 2
            centered = bool(
                re.fullmatch(r"(?:[IVXLCDM]+|[A-Z]|\d+)\.?", marker)
                and page_width * 0.35 <= center <= page_width * 0.65
            )
            return aligned or centered

        wraps = [
            index for index, pair in enumerate(zip(nodes, nodes[1:]))
            if attested_wrap(*pair)
        ]
        if len(wraps) != 1:
            continue
        wrap = wraps[0]
        prefix, late = nodes[:wrap + 1], nodes[wrap + 1:]
        if max(value[2][2] for value in late) <= (
                min(value[2][1] for value in prefix) + 4.0):
            ordered = [*late, *prefix]
        elif (len(late) <= 4
              and all(
                  _label(items.get(_child_ref(value[1]))) == "section_header"
                  or len(value[2][5].split()) <= 16
                  for value in late)):
            ordered = list(prefix)
            for value in late:
                key = (value[2][1], value[2][3], value[0])
                insert_at = next((
                    index for index, existing in enumerate(ordered)
                    if (existing[2][1], existing[2][3], existing[0]) > key
                ), len(ordered))
                ordered.insert(insert_at, value)
        else:
            continue
        for (target, _, _), (_, replacement, _) in zip(nodes, ordered):
            children[target] = replacement

    ordered_refs: list[str] = []
    visited: set[str] = set()

    def append(ref: str, active: frozenset[str] = frozenset()) -> None:
        if not ref or ref in active:
            return
        group = groups.get(ref)
        if group is not None:
            for child in group.get("children") or []:
                append(_child_ref(child), active | frozenset((ref,)))
            return
        if ref in items and ref not in visited:
            visited.add(ref)
            ordered_refs.append(ref)
            for child in items[ref].get("children") or []:
                append(_child_ref(child), active | frozenset((ref,)))

    for child in children:
        append(_child_ref(child))
    # Malformed or legacy documents may lack a body tree.  The collection
    # order is a deterministic, lossless fallback for otherwise unseen refs.
    for collection in _COLLECTIONS:
        for item in document.get(collection) or []:
            append(_ref(item))
    return ordered_refs


def running_section_heading_refs(document: dict) -> frozenset[str]:
    """Identify section-header occurrences proven to be page furniture."""
    texts = [item for item in document.get("texts") or []
             if isinstance(item, dict)]
    header_verticals: defaultdict[int, list[float]] = defaultdict(list)
    for item in texts:
        boxes = _provenance_boxes(item)
        if _label(item) == "page_header" and boxes:
            header_verticals[boxes[0][0]].append(boxes[0][1])

    candidates: list[dict] = []
    for item in texts:
        if _label(item) != "section_header":
            continue
        ref = _ref(item)
        boxes = _provenance_boxes(item)
        if not ref or not boxes:
            continue
        page, top = boxes[0][0], boxes[0][1]
        aligned = any(abs(top - value) <= 12.0
                      for value in header_verticals.get(page, ()))
        raw_prov = item.get("prov") or []
        bbox = raw_prov[0].get("bbox") if raw_prov else None
        height = _page_size(document, page, "height")
        top_margin = False
        if isinstance(bbox, dict) and height is not None:
            try:
                first, second = float(bbox.get("t")), float(bbox.get("b"))
                distance = (
                    height - max(first, second)
                    if str(bbox.get("coord_origin") or "").upper()
                    == "BOTTOMLEFT"
                    else min(first, second)
                )
                top_margin = -2.0 <= distance <= 58.0
            except (TypeError, ValueError):
                pass
        tokens = re.findall(r"[a-z]+|\d+", _text(item).casefold())
        while tokens and tokens[0].isdigit():
            tokens.pop(0)
        while tokens and tokens[-1].isdigit():
            tokens.pop()
        candidates.append({
            "ref": ref,
            "page": page,
            "top": top,
            "left": boxes[0][3],
            "right": boxes[0][4],
            "aligned": aligned,
            "top_margin": top_margin,
            "family": "\x1f".join(tokens),
            "tokens": tuple(re.findall(
                r"[a-z]+|\d+", _text(item).casefold())),
        })

    family_pages: defaultdict[str, set[int]] = defaultdict(set)
    for candidate in candidates:
        if candidate["top_margin"] and candidate["family"]:
            family_pages[candidate["family"]].add(candidate["page"])

    refs: set[str] = set()
    source_heading_tokens = {
        candidate["tokens"] for candidate in candidates
        if candidate["tokens"]
    }
    for candidate in candidates:
        repeated_text = (
            candidate["top_margin"] and candidate["family"]
            and len(family_pages[candidate["family"]]) >= 2
        )
        tokens = candidate["tokens"]
        trailing_page_number = (
            candidate["top_margin"] and len(tokens) >= 2
            and tokens[-1].isdigit()
            and tokens[:-1] in source_heading_tokens
            and any(token.isalpha() for token in tokens[:-1])
        )
        # Geometry without a page-header source object or repeated/source-
        # matched text is not evidence: unrelated substantive headings often
        # share a publisher's top-margin placement.
        if candidate["aligned"] or repeated_text or trailing_page_number:
            refs.add(candidate["ref"])
    # Some converters split one running header row into a textual title and
    # a narrow page-number object, labeling both as section headers.  The
    # numeric lane has no repeatable text family of its own, so inherit the
    # already-proven furniture classification only from a same-row,
    # nonoverlapping running peer.  Top-margin geometry and independent peer
    # proof keep ordinary numbered body headings attachable.
    for candidate in candidates:
        tokens = candidate["tokens"]
        if (candidate["ref"] in refs or not candidate["top_margin"]
                or not tokens or not all(token.isdigit() for token in tokens)):
            continue
        if any(
                peer["ref"] in refs
                and peer["page"] == candidate["page"]
                and abs(peer["top"] - candidate["top"]) <= 12.0
                and (candidate["right"] <= peer["left"] + 4.0
                     or peer["right"] <= candidate["left"] + 4.0)
                for peer in candidates
                if peer is not candidate):
            refs.add(candidate["ref"])
    return frozenset(refs)


def sparse_ocr_heading_artifact_refs(document: dict) -> frozenset[str]:
    """Return repeated page-top OCR fragments mislabeled as headings.

    A single odd-looking or short heading is never enough.  Every returned
    occurrence must have one complete provenance box, a known page size, a
    wide page-top footprint, visibly fragmented uppercase text, and matching
    geometry on at least two other pages.  The proof is deliberately based
    only on serialized source evidence so conversion and validation can
    independently recompute the same typed demotion.
    """
    candidates: list[dict[str, object]] = []
    texts = document.get("texts") if isinstance(document, dict) else None
    for item in texts if isinstance(texts, list) else []:
        if not isinstance(item, dict) or _label(item) != "section_header":
            continue
        ref = _ref(item)
        raw_prov = item.get("prov")
        if not ref or not isinstance(raw_prov, list) or len(raw_prov) != 1:
            continue
        span = raw_prov[0]
        bbox = span.get("bbox") if isinstance(span, dict) else None
        page = span.get("page_no") if isinstance(span, dict) else None
        if (not isinstance(page, int) or isinstance(page, bool)
                or not isinstance(bbox, dict)):
            continue
        origin = str(bbox.get("coord_origin") or "").upper()
        if origin not in {"BOTTOMLEFT", "TOPLEFT"}:
            continue
        try:
            left, right = sorted((float(bbox.get("l")), float(bbox.get("r"))))
            first, second = sorted((float(bbox.get("t")), float(bbox.get("b"))))
        except (TypeError, ValueError):
            continue
        if not all(math.isfinite(value) for value in (
                left, right, first, second)):
            continue
        page_width = _page_size(document, page, "width")
        page_height = _page_size(document, page, "height")
        if page_width is None or page_height is None:
            continue
        top_distance = (
            page_height - second if origin == "BOTTOMLEFT" else first
        )
        width_ratio = (right - left) / page_width
        left_ratio = left / page_width
        right_ratio = right / page_width
        height_ratio = (second - first) / page_height
        if (not -2.0 <= top_distance <= 60.0
                or not 0.55 <= width_ratio <= 1.02
                or not 0.75 <= right_ratio <= 1.02
                or not 0.0 < height_ratio <= 0.08):
            continue

        text = _text(item).strip()
        tokens = re.findall(r"[A-Za-z]+|\d+[A-Za-z]*", text)
        alpha = "".join(character for character in text
                        if character.isalpha())
        alpha_runs = re.findall(r"[A-Za-z]+", text)
        if (not re.search(r"[ \t]{2,}", text)
                or not 1 <= len(tokens) <= 5
                or not 1 <= len(alpha) <= 12
                or alpha != alpha.upper()
                or not alpha_runs
                or max(map(len, alpha_runs)) > 3):
            continue
        candidates.append({
            "ref": ref,
            "page": page,
            "top_distance": top_distance,
            "width_ratio": width_ratio,
            "left_ratio": left_ratio,
            "right_ratio": right_ratio,
            "height_ratio": height_ratio,
        })

    refs: set[str] = set()
    for candidate in candidates:
        matching_pages = {
            int(peer["page"]) for peer in candidates
            if (abs(float(candidate["top_distance"])
                    - float(peer["top_distance"])) <= 3.0
                and abs(float(candidate["width_ratio"])
                        - float(peer["width_ratio"])) <= 0.08
                and abs(float(candidate["left_ratio"])
                        - float(peer["left_ratio"])) <= 0.06
                and abs(float(candidate["right_ratio"])
                        - float(peer["right_ratio"])) <= 0.06
                and abs(float(candidate["height_ratio"])
                        - float(peer["height_ratio"])) <= 0.02)
        }
        if len(matching_pages) >= 3:
            refs.add(str(candidate["ref"]))
    return frozenset(refs)


def _source_refs(record: object) -> list[str]:
    if not isinstance(record, dict):
        return []
    metadata = record.get("metadata")
    if not isinstance(metadata, dict):
        return []
    result = []
    for item in metadata.get("source_items") or []:
        ref = str(item.get("ref") or "") if isinstance(item, dict) else ""
        if ref and ref not in result:
            result.append(ref)
    return result


def _source_outline_analysis(
        order: Sequence[str], items: dict[str, dict],
        groups: dict[str, dict], running_refs: frozenset[str],
) -> tuple[frozenset[str], dict[str, int]]:
    """Prove outline-only headings and scope ends from the source tree.

    A verified outline begins at an exact ``Summary of Contents`` source
    heading, contains at least two list items, and ends where ordinary body
    content begins.  The last non-running header before that body is the
    substantive section opener; earlier header occurrences qualify as
    embedded outline rows only when more list content follows them.  Chunk
    metadata is deliberately absent from this proof.
    """
    parent_refs: defaultdict[str, set[str]] = defaultdict(set)
    for ref, item in {**groups, **items}.items():
        parent = item.get("parent") if isinstance(item, dict) else None
        parent_ref = _child_ref(parent)
        if parent_ref and parent_ref != "#/body":
            parent_refs[ref].add(parent_ref)
        for child in item.get("children") or []:
            child_ref = _child_ref(child)
            if child_ref and ref != "#/body":
                parent_refs[child_ref].add(ref)

    def outline_relationship(
            heading_position: int, heading_ref: str,
            list_positions: Sequence[int], boundary: int,
    ) -> bool:
        following_lists = [
            (position, order[position]) for position in list_positions
            if heading_position < position < boundary
        ]
        shared_container_counts: Counter[str] = Counter(
            parent for _, ref in following_lists for parent in parent_refs[ref]
            if parent in parent_refs[heading_ref]
        )
        if any(count >= 2 for count in shared_container_counts.values()):
            return True

        heading_boxes = _provenance_boxes(items.get(heading_ref))
        if (not heading_boxes
                or len({box[0] for box in heading_boxes}) != 1):
            return False
        page = heading_boxes[0][0]
        heading_top = min(box[1] for box in heading_boxes)
        heading_bottom = max(box[2] for box in heading_boxes)
        heading_left = min(box[3] for box in heading_boxes)
        heading_right = max(box[4] for box in heading_boxes)
        same_page_lists = []
        for position in list_positions:
            if position >= boundary:
                continue
            ref = order[position]
            boxes = _provenance_boxes(items.get(ref))
            if not boxes or any(box[0] != page for box in boxes):
                continue
            top = min(box[1] for box in boxes)
            bottom = max(box[2] for box in boxes)
            left = min(box[3] for box in boxes)
            right = max(box[4] for box in boxes)
            if (position < heading_position and bottom > heading_top + 3.0
                    or position > heading_position
                    and top + 3.0 < heading_bottom):
                continue
            horizontal_overlap = max(
                0.0, min(heading_right, right) - max(heading_left, left))
            if abs(left - heading_left) > 48.0 and horizontal_overlap <= 0:
                continue
            same_page_lists.append((position, top, bottom))
        following = [
            value for value in same_page_lists
            if value[0] > heading_position
        ]
        if len(same_page_lists) < 2 or not following:
            return False
        preceding = [
            value for value in same_page_lists
            if value[0] < heading_position
        ]
        following.sort()
        preceding.sort()
        adjacent_gaps = [following[0][1] - heading_bottom]
        if preceding:
            adjacent_gaps.append(heading_top - preceding[-1][2])
        return (
            any(-3.0 <= gap <= 42.0 for gap in adjacent_gaps)
            and all(left[1] <= right[1] + 2.0 for left, right in zip(
                same_page_lists, same_page_lists[1:]))
            and heading_top <= following[0][1] + 2.0
        )

    result: set[str] = set()
    scope_ends: dict[str, int] = {}
    summary_identity = canonical_text("Summary of Contents")
    for summary_position, summary_ref in enumerate(order):
        summary_item = items.get(summary_ref)
        if (_label(summary_item) != "section_header"
                or canonical_text(_text(summary_item)) != summary_identity):
            continue
        list_positions: list[int] = []
        heading_positions: list[tuple[int, str]] = []
        content_position = None
        for position in range(summary_position + 1, len(order)):
            ref = order[position]
            item = items.get(ref)
            label = _label(item)
            furniture = "furniture" in str(
                (item or {}).get("content_layer") or "").casefold()
            if (furniture or label in {"page_header", "page_footer"}
                    or ref in running_refs):
                continue
            if label == "document_index":
                # A Docling document-index object is itself a verified
                # outline container.  Its next source position closes the
                # summary scope without consulting a chunk record.
                scope_ends[summary_ref] = position + 1
                content_position = None
                break
            if label == "list_item":
                list_positions.append(position)
                continue
            if label == "section_header":
                heading_positions.append((position, ref))
                continue
            if label in _BODY_CONTENT_LABELS:
                content_position = position
                break
        if summary_ref in scope_ends:
            continue
        if content_position is None or len(list_positions) < 2:
            continue
        boundary = (
            heading_positions[-1][0]
            if heading_positions else content_position
        )
        scope_ends[summary_ref] = boundary
        for position, ref in heading_positions:
            if position >= boundary:
                continue
            if any(position < list_position < boundary
                   for list_position in list_positions) and outline_relationship(
                       position, ref, list_positions, boundary):
                result.add(ref)
    return frozenset(result), scope_ends


def _common_prefix(values: Sequence[tuple[str, ...]]) -> tuple[str, ...]:
    if not values:
        return ()
    result = []
    for components in zip(*values):
        if len(set(components)) != 1:
            break
        result.append(components[0])
    return tuple(result)


def _section_parts(record: object) -> list[str]:
    metadata = record.get("metadata") if isinstance(record, dict) else None
    if not isinstance(metadata, dict):
        return []
    value = metadata.get("section_path")
    if not isinstance(value, str):
        return []
    return [
        part.strip() for part in re.split(r"\s*(?:>|→)\s*", value)
        if part.strip()
    ]


def _level_displays(
        record: object, heading_keys: frozenset[str],
) -> list[tuple[int, str]]:
    parts = _section_parts(record)
    metadata = record.get("metadata") if isinstance(record, dict) else None
    headings = list(metadata.get("headings") or []) if isinstance(
        metadata, dict) else []
    displays = list(parts)
    known = {canonical_text(display) for display in displays}
    for value in headings:
        display = str(value or "").strip()
        identity = canonical_text(display)
        if not identity or identity in known:
            continue
        displays.append(display)
        known.add(identity)
    result = []
    heading_depth = 0
    for display in displays:
        keys = {
            canonical_text(display),
            f"tokens:{_token_signature(display)}",
        }
        if not any(key and key in heading_keys for key in keys):
            continue
        heading_depth += 1
        result.append((heading_depth, display))
    return result


def _token_signature(value: str) -> str:
    return "\x1f".join(sorted(re.findall(r"[a-z]+|\d+", value.casefold())))


def _heading_display_keys(
        order: Sequence[str], items: dict[str, dict],
        heading_refs: frozenset[str],
) -> frozenset[str]:
    """Return exact display keys without counting ordinary path annotations."""
    keys: set[str] = set()

    def add(value: str) -> None:
        identity = canonical_text(value)
        signature = _token_signature(value)
        if identity:
            keys.add(identity)
        if signature:
            keys.add(f"tokens:{signature}")

    run: list[str] = []

    def flush() -> None:
        for start in range(len(run)):
            for size in range(1, min(3, len(run) - start) + 1):
                add(" ".join(
                    _text(items[ref]) for ref in run[start:start + size]))
        run.clear()

    for ref in order:
        if ref in heading_refs:
            run.append(ref)
        else:
            flush()
    flush()
    return frozenset(keys)


def _display_depth_hints(
        records: Sequence[dict], heading_keys: frozenset[str],
) -> dict[str, int]:
    candidates: defaultdict[str, Counter[int]] = defaultdict(Counter)
    for record in records:
        for depth, display in _level_displays(record, heading_keys):
            identity = canonical_text(display)
            if identity:
                candidates[identity][depth] += 1
            signature = _token_signature(display)
            if signature:
                candidates[f"tokens:{signature}"][depth] += 1
    result = {}
    for identity, counts in candidates.items():
        highest = max(counts.values())
        winners = [depth for depth, count in counts.items()
                   if count == highest]
        if len(winners) == 1:
            result[identity] = winners[0]
    return result


def _heading_level(
        item: dict, depth_hints: dict[str, int], *, ref: str = "",
) -> tuple[int, bool]:
    """Infer a semantic stack level without using text as identity."""
    text = _clean_marker(_text(item))
    identity = canonical_text(text)
    # These publisher-level markers are authoritative.  A tampered path may
    # not promote a book section to depth 1 and thereby erase its chapter.
    if re.match(r"^(?:Chapter|Part|Unit)\s+", text, re.IGNORECASE):
        return 1, True
    if re.match(r"^§\s*(?:[1-9]|1[0-9])\.\d{2}\b", text):
        return 2, True
    hinted = depth_hints.get(f"ref:{ref}") if ref else None
    if hinted is None:
        hinted = depth_hints.get(identity)
    if hinted is None:
        hinted = depth_hints.get(f"tokens:{_token_signature(text)}")
    if hinted is not None:
        return hinted, True
    if re.match(r"^[A-Z]\.\s+", text):
        return 3, True
    if re.match(r"^[IVXLCDM]+\.\s+", text):
        return 4, True
    if re.match(r"^\d+[.)]\s+", text):
        return 4, True
    if re.match(r"^[a-z][.)]\s+", text):
        return 5, True
    # Docling commonly labels every header level 1.  Treat an unattested
    # generic heading as a subsection, never as a new outer division.
    return 3, False


def _occurrence_depth_hints(
        records: Sequence[dict], items: dict[str, dict],
        order_index: dict[str, int], heading_keys: frozenset[str],
) -> dict[str, int]:
    """Bind a level hint to the first exact display after an occurrence."""
    events: defaultdict[str, list[tuple[int, int]]] = defaultdict(list)
    for index, record in enumerate(records):
        refs = [ref for ref in _source_refs(record) if ref in order_index]
        rank = min((order_index[ref] for ref in refs),
                   default=len(order_index) + index)
        for depth, display in _level_displays(record, heading_keys):
            identity = canonical_text(display)
            signature = _token_signature(display)
            if identity:
                events[identity].append((rank, depth))
            if signature:
                events[f"tokens:{signature}"].append((rank, depth))
    result: dict[str, int] = {}
    for ref, item in items.items():
        if _label(item) != "section_header" or ref not in order_index:
            continue
        rank = order_index[ref]
        keys = (
            canonical_text(_text(item)),
            f"tokens:{_token_signature(_text(item))}",
        )
        candidates = [
            (event_rank, depth)
            for key in keys for event_rank, depth in events.get(key, ())
            if event_rank > rank
        ]
        if candidates:
            first_rank = min(value[0] for value in candidates)
            depths = {depth for event_rank, depth in candidates
                      if event_rank == first_rank}
            if len(depths) == 1:
                result[f"ref:{ref}"] = next(iter(depths))
    return result


def _display_matches_refs(
        display: str, refs: Sequence[str], items: dict[str, dict],
) -> bool:
    display_identity = canonical_text(display)
    combined = " ".join(_text(items.get(ref)) for ref in refs)
    if display_identity and display_identity == canonical_text(combined):
        return True
    display_tokens = re.findall(r"[a-z]+|\d+", display.casefold())
    source_tokens = re.findall(r"[a-z]+|\d+", combined.casefold())
    # The only non-order-preserving repair accepted here is an exact token
    # permutation, used for OCR headings such as ``Settlement ... 4.`` ->
    # ``4. Settlement ...``.  No source token may be added or removed.
    return bool(display_tokens and sorted(display_tokens) == sorted(
        source_tokens))


def _running_heading_resumes(
        running_item: dict, ancestor_item: dict,
) -> bool:
    """Prove that page furniture repeats one exact ancestor display.

    Running heads in the source append the printed page number to the
    substantive heading.  Require the ancestor's complete token sequence,
    followed by exactly one numeric token; a short substring is not enough.
    Exact repetition is accepted as well.
    """
    running_tokens = re.findall(
        r"[a-z]+|\d+", _text(running_item).casefold())
    ancestor_tokens = re.findall(
        r"[a-z]+|\d+", _text(ancestor_item).casefold())
    if not ancestor_tokens:
        return False
    if running_tokens == ancestor_tokens:
        return True
    return (
        len(running_tokens) == len(ancestor_tokens) + 1
        and running_tokens[:-1] == ancestor_tokens
        and running_tokens[-1].isdigit()
        and any(token.isalpha() for token in ancestor_tokens)
    )


def _component_bindings(
        record: dict, path: Sequence[str], items: dict[str, dict],
) -> tuple[list[dict], bool]:
    displays = _section_parts(record)
    components: list[dict] = []
    path_index = 0
    valid = True
    for display in displays:
        match = None
        # Composite title lines are adjacent and small.  Bounding the search
        # prevents a display from laundering an arbitrary collection of refs.
        for end in range(path_index + 1, min(len(path), path_index + 3) + 1):
            candidate = list(path[path_index:end])
            if _display_matches_refs(display, candidate, items):
                match = candidate
                break
        if match is None:
            components.append({
                "display": display,
                "occurrence_ids": [],
                "binding": "unbound",
            })
            valid = False
            continue
        components.append({
            "display": display,
            "occurrence_ids": match,
            "binding": "source_heading",
        })
        path_index += len(match)
    if path_index != len(path):
        valid = False
    return components, valid


def _occurrence_component_bindings(
        record: dict, *, record_rank: int,
        heading_groups: Sequence[tuple[tuple[str, ...], int, int, int]],
        items: dict[str, dict],
        source_candidates: dict[str, Sequence[tuple[int, str]]],
        case_annotation_anchors: dict[
            str, Sequence[tuple[int, str, str]]],
) -> tuple[list[dict], bool]:
    """Bind each displayed component to one in-scope exact occurrence."""
    components: list[dict] = []
    valid = True
    lower_rank = -1
    for depth, display in enumerate(_section_parts(record), start=1):
        def strict_composite_member_alias(refs: Sequence[str]) -> bool:
            if not components or len(refs) != 1:
                return False
            previous = components[-1]
            previous_refs = previous.get("occurrence_ids")
            return (
                previous.get("binding") == "source_heading"
                and isinstance(previous_refs, list)
                and len(previous_refs) > 1
                and refs[0] in previous_refs
            )

        used_heading_refs = {
            ref for component in components
            if component.get("binding") == "source_heading"
            for ref in component.get("occurrence_ids") or []
        }
        candidates = [
            (start, refs)
            for refs, level, start, end in heading_groups
            if (start <= record_rank < end
                and _display_matches_refs(display, refs, items)
                and (start > lower_rank
                     or (start == lower_rank
                         and strict_composite_member_alias(refs)))
                and (not used_heading_refs.intersection(refs)
                     or strict_composite_member_alias(refs)))
        ]
        if candidates:
            start, refs = max(candidates, key=lambda value: value[0])
            ids = list(refs)
            components.append({
                "display": display,
                "occurrence_ids": ids,
                "binding": "source_heading",
            })
            lower_rank = start
            continue

        # Case captions promoted from ordinary text are still source-bound.
        # They cannot satisfy the heading-attachment count, but their exact
        # source occurrence prevents an invented legacy path component.
        candidate_values = {
            value for key in (
                canonical_text(display),
                f"tokens:{_token_signature(display)}",
            )
            for value in source_candidates.get(key, ())
            if lower_rank <= value[0] <= record_rank
            and any(
                anchor_rank <= record_rank
                and record_rank - anchor_rank
                <= _MAX_CASE_ANNOTATION_SOURCE_DISTANCE
                and (canonical_text(display) == identity
                     or _token_signature(display) == signature)
                for anchor_rank, identity, signature
                in case_annotation_anchors.get(value[1], ()))
        }
        if candidate_values:
            start, ref = max(candidate_values)
            components.append({
                "display": display,
                "occurrence_ids": [ref],
                "binding": "source_item",
            })
            lower_rank = start
            continue
        components.append({
            "display": display,
            "occurrence_ids": [],
            "binding": "unbound",
        })
        valid = False
    return components, valid


def expected_heading_bindings(
        document: dict, records: Sequence[dict], *,
        structural_ranges: Iterable[tuple[int, int]] = (),
        excluded_heading_refs: Iterable[str] = (),
) -> dict:
    """Return exact expected paths/direct claims for a document generation."""
    ranges = tuple(sorted(set(structural_ranges)))
    artifacts = sparse_ocr_heading_artifact_refs(document)
    # A caller may demote these refs while building records, but the heading
    # audit retains and types them in its raw source-heading partition rather
    # than silently folding them into generic exclusions.
    excluded = frozenset(excluded_heading_refs) - artifacts
    items, groups = _catalog(document)
    order = _ordered_source_refs(document, items, groups, ranges)
    order_index = {ref: index for index, ref in enumerate(order)}
    running = running_section_heading_refs(document) - artifacts
    embedded_outline, summary_scope_ends = _source_outline_analysis(
        order, items, groups, running | artifacts)
    depth_heading_refs = frozenset(
        ref for ref in order
        if (_label(items.get(ref)) == "section_header"
            and ref not in excluded and ref not in running
            and ref not in artifacts
            and ref not in embedded_outline
            and bool(canonical_text(_text(items.get(ref)))))
    )
    heading_keys = _heading_display_keys(
        order, items, depth_heading_refs)
    depth_hints = _display_depth_hints(records, heading_keys)
    depth_hints.update(_occurrence_depth_hints(
        records, items, order_index, heading_keys))
    eligible: list[str] = []
    attachable: list[str] = []
    paths_by_ref: dict[str, tuple[str, ...]] = {}
    path_before_heading: dict[str, tuple[str, ...]] = {}
    path_after_heading: dict[str, tuple[str, ...]] = {}
    active: list[tuple[int, tuple[str, ...], int]] = []
    heading_groups: list[tuple[tuple[str, ...], int, int, int]] = []
    content_since_heading = True
    in_structural_range = False

    def close_node(node: tuple[int, tuple[str, ...], int], end: int) -> None:
        level, refs, start = node
        heading_groups.append((refs, level, start, end))

    for order_position, ref in enumerate(order):
        while (active
               and any(
                   summary_scope_ends.get(value) is not None
                   and summary_scope_ends[value] <= order_position
                   for value in active[-1][1])):
            close_node(active.pop(), order_position)
        item = items[ref]
        structural = _inside_ranges(item, ranges)
        if structural:
            if not in_structural_range:
                for node in active:
                    close_node(node, order_position)
                active.clear()
                content_since_heading = True
            in_structural_range = True
            continue
        in_structural_range = False
        label = _label(item)
        furniture = "furniture" in str(
            item.get("content_layer") or "").casefold()
        is_heading = (
            label == "section_header" and ref not in excluded
            and not furniture and bool(canonical_text(_text(item)))
        )
        if is_heading:
            eligible.append(ref)
            if ref in artifacts:
                continue
            if ref in running:
                continue
            if ref in embedded_outline:
                continue
            attachable.append(ref)
            path_before_heading[ref] = tuple(
                value for _, values, _ in active for value in values)
            level, level_attested = _heading_level(
                item, depth_hints, ref=ref)
            previous_text = (
                " ".join(_text(items.get(value)) for value in active[-1][1])
                if active else ""
            )
            division_continuation = bool(
                not content_since_heading and active
                and len(active[-1][1]) == 1
                and (re.fullmatch(
                    r"(?:Chapter|Part|Unit)\s+\w+", previous_text.strip(),
                    re.IGNORECASE)
                    or (re.match(
                        r"^(?:Chapter|Part|Unit)\s+", previous_text,
                        re.IGNORECASE)
                        and _clean_marker(_text(item)).startswith("(")))
                and not re.match(
                    r"^(?:Chapter|Part|Unit)\s+|^§\s*\d+|"
                    r"^[A-Z]\.\s+|^[IVXLCDM]+\.\s+|^\d+[.)]\s+|"
                    r"^[a-z][.)]\s+",
                    _clean_marker(_text(item)), re.IGNORECASE)
            )
            if division_continuation:
                active[-1] = (
                    active[-1][0], (*active[-1][1], ref), active[-1][2])
                path_after_heading[ref] = tuple(
                    value for _, values, _ in active for value in values)
                content_since_heading = False
                continue
            # Consecutive headers form one observable composite/nested run,
            # even when Docling assigns every line level 1 (e.g. Chapter 6 /
            # Damages).  Once body content occurs, the declared level again
            # determines peer scope.
            if (not content_since_heading and active
                    and not level_attested):
                level = max(level, active[-1][0] + 1)
            else:
                while active and active[-1][0] >= level:
                    close_node(active.pop(), order_position)
            active.append((level, (ref,), order_position))
            path_after_heading[ref] = tuple(
                value for _, values, _ in active for value in values)
            content_since_heading = False
            continue
        paths_by_ref[ref] = tuple(
            ref for _, values, _ in active for ref in values)
        if (label in _BODY_CONTENT_LABELS and not furniture
                and ref not in running):
            content_since_heading = True

    for node in active:
        close_node(node, len(order))
    composite_members = [
        ((ref,), level, order_index[ref], end)
        for refs, level, _start, end in heading_groups if len(refs) > 1
        for ref in refs
    ]
    heading_groups.extend(composite_members)

    def table_occurrence_path(ref: str) -> tuple[str, ...] | None:
        base = paths_by_ref.get(ref)
        boxes = _provenance_boxes(items.get(ref))
        if base is None or not boxes or len({box[0] for box in boxes}) != 1:
            return base
        page = boxes[0][0]
        table_top = min(box[1] for box in boxes)
        table_rank = order_index.get(ref)
        if table_rank is None:
            return base
        candidates = []
        for heading_ref in attachable:
            heading_rank = order_index.get(heading_ref)
            if (heading_rank is None or heading_rank <= table_rank
                    or heading_rank - table_rank > 8):
                continue
            heading_boxes = _provenance_boxes(items.get(heading_ref))
            if (not heading_boxes
                    or any(box[0] != page for box in heading_boxes)
                    or max(box[2] for box in heading_boxes) > table_top + 0.5):
                continue
            candidate = path_after_heading.get(heading_ref)
            if not candidate:
                continue
            same_parent = (
                len(base) > 0 and len(candidate) > 0
                and candidate[:-1] == base[:-1]
            )
            nested = tuple(candidate[:len(base)]) == tuple(base)
            if not same_parent and not nested:
                continue
            candidates.append((heading_rank, candidate))
        if not candidates:
            return base
        return max(candidates, key=lambda value: value[0])[1]

    source_scope_paths: list[list[str]] = []
    scope_conflicts: list[int] = []
    record_order: list[tuple[int, int]] = []
    record_binding_ranks: list[int] = []
    for index, record in enumerate(records):
        refs = _source_refs(record)
        metadata = record.get("metadata") if isinstance(record, dict) else None
        included_refs = frozenset(refs)
        figure_record = bool(
            isinstance(metadata, dict)
            and (metadata.get("content_source") == "figure"
                 or metadata.get("content_type") == "figure")
        )
        ancillary_picture_refs: set[str] = set()
        if not figure_record:
            for ref in refs:
                if _label(items.get(ref)) != "picture":
                    continue
                descendants = [
                    _child_ref(child)
                    for child in (items.get(ref) or {}).get("children") or []
                ]
                visited: set[str] = {ref}
                while descendants:
                    descendant = descendants.pop()
                    if not descendant or descendant in visited:
                        continue
                    visited.add(descendant)
                    if descendant in included_refs:
                        ancillary_picture_refs.add(ref)
                        break
                    descendant_item = items.get(descendant) or groups.get(
                        descendant)
                    descendants.extend(
                        _child_ref(child)
                        for child in (descendant_item or {}).get(
                            "children") or [])
        candidates = []
        for ref in refs:
            # A non-figure record can carry both a picture container for
            # lineage/fidelity and one of that container's exact descendants
            # as its substantive source.  The ancestor's pre-child path is
            # not competing scope evidence; the descendant still undergoes
            # the ordinary occurrence and physical-order checks below.
            if ref in ancillary_picture_refs:
                continue
            path = paths_by_ref.get(ref)
            item_boxes = _provenance_boxes(items.get(ref))
            if path is not None and item_boxes:
                item_page, item_top = item_boxes[0][0], item_boxes[0][1]
                while True:
                    future_heading = next((
                        heading_ref for heading_ref in reversed(path)
                        if ((heading_boxes := _provenance_boxes(
                            items.get(heading_ref)))
                            and heading_boxes[0][0] == item_page
                            and heading_boxes[0][1] > item_top + 0.5
                            and heading_ref in path_before_heading)
                    ), None)
                    if future_heading is None:
                        break
                    # The serialized tree placed this item after a heading
                    # that is physically below it on the same page.  Roll
                    # back to the exact stack before that occurrence, then
                    # repeat because that stack can contain an earlier nested
                    # heading that is also physically below this source item.
                    previous_path = path
                    path = path_before_heading[future_heading]
                    if path == previous_path:
                        break
            if path is not None and path not in candidates:
                candidates.append(path)
        table_refs = [
            ref for ref in refs if _label(items.get(ref)) == "table"
            and paths_by_ref.get(ref) is not None
        ]
        if (isinstance(metadata, dict)
                and metadata.get("content_source") == "table" and table_refs):
            # A table's own occurrence defines the only hierarchy its parent
            # and retrieval-only children may inherit.  Layout fragments do
            # not authorize later or unrelated heading occurrences.
            candidates = [table_occurrence_path(table_refs[0])]
        elif len(candidates) > 1:
            scope_conflicts.append(index)
        path = candidates[0] if len(candidates) == 1 else _common_prefix(candidates)
        source_scope_paths.append(list(path))
        ranked_refs = [ref for ref in refs if ref in order_index]
        if (isinstance(metadata, dict)
                and metadata.get("content_source") == "table" and table_refs):
            source_rank = order_index[table_refs[0]]
        else:
            source_rank = min(
                (order_index[ref] for ref in ranked_refs),
                default=len(order) + index,
            )
        record_order.append((source_rank, index))
        binding_rank = max(
            (order_index[ref] for ref in ranked_refs), default=source_rank)
        if (isinstance(metadata, dict)
                and metadata.get("content_source") == "table" and table_refs):
            heading_ranks = [
                order_index[ref]
                for ref in (table_occurrence_path(table_refs[0]) or ())
                if ref in order_index
            ]
            if heading_ranks:
                binding_rank = max(binding_rank, *heading_ranks)
        record_binding_ranks.append(binding_rank)

    expected_components: list[list[dict]] = []
    expected_paths: list[list[str]] = []
    display_binding_issues: list[int] = []
    ancestor_resumptions: dict[int, list[dict]] = {}
    source_candidates: defaultdict[str, list[tuple[int, str]]] = defaultdict(
        list)
    for ref in order:
        item = items[ref]
        if _label(item) in {"section_header", "page_header", "page_footer"}:
            continue
        value = _text(item)
        identity = canonical_text(value)
        signature = _token_signature(value)
        if identity:
            source_candidates[identity].append((order_index[ref], ref))
        if signature:
            source_candidates[f"tokens:{signature}"].append((
                order_index[ref], ref))
    case_annotation_anchors: defaultdict[
        str, list[tuple[int, str, str]]] = defaultdict(list)
    for index, record in enumerate(records):
        metadata = record.get("metadata") if isinstance(record, dict) else None
        if (not isinstance(metadata, dict)
                or metadata.get("content_type") not in {
                    "case_opinion", "author_narrative"}):
            continue
        title = next((
            str(metadata.get(field) or "").strip()
            for field in ("primary_case", "case_title", "case_name")
            if isinstance(metadata.get(field), str)
            and str(metadata.get(field) or "").strip()
        ), "")
        if not title or _CASE_TITLE_RE.search(title) is None:
            continue
        for ref in _source_refs(record):
            item = items.get(ref)
            if (ref in order_index and item is not None
                    and _label(item) not in {
                        "section_header", "page_header", "page_footer"}
                    and _display_matches_refs(title, (ref,), items)):
                case_annotation_anchors[ref].append((
                    record_binding_ranks[index], canonical_text(title),
                    _token_signature(title),
                ))
    for index, record in enumerate(records):
        components, valid = _occurrence_component_bindings(
            record, record_rank=record_binding_ranks[index],
            heading_groups=heading_groups, items=items,
            source_candidates=source_candidates,
            case_annotation_anchors=case_annotation_anchors)
        section_components = list(components)
        bound_refs = {
            ref for component in components
            for ref in component["occurrence_ids"]
        }
        metadata = record.get("metadata") if isinstance(record, dict) else None
        local_headings = list(metadata.get("headings") or []) if isinstance(
            metadata, dict) else []
        for ref in source_scope_paths[index]:
            if ref in bound_refs or ref not in attachable:
                continue
            display = next((
                str(value) for value in local_headings
                if _display_matches_refs(str(value), (ref,), items)
            ), None)
            if display is None:
                continue
            unbound = next((
                component for component in components
                if (component["binding"] == "unbound"
                    and canonical_text(component["display"])
                    == canonical_text(display))
            ), None)
            replacement = {
                "display": display,
                "occurrence_ids": [ref],
                "binding": "local_source_heading",
            }
            if unbound is not None:
                unbound.update(replacement)
            else:
                components.append(replacement)
            bound_refs.add(ref)
        # A legacy local-heading field may recover an omitted source heading,
        # but it cannot legitimize replacing that section-path position with
        # an ordinary body item.  Ordinary source-item components remain
        # valid when they are genuine additions such as promoted case names.
        source_item_substitution = (
            any(component["binding"] == "source_item"
                for component in section_components)
            and any(component["binding"] == "local_source_heading"
                    for component in components)
        )
        valid = (
            not source_item_substitution
            and all(component["binding"] != "unbound"
                    for component in components)
        )
        expected_components.append(components)
        path = list(dict.fromkeys(
            ref for component in components
            for ref in component["occurrence_ids"]
            if ref in attachable
        ))
        expected_paths.append(path)
        component_heading_refs = {
            ref for ref in path if ref in attachable
        }
        required_scope_refs = set(source_scope_paths[index])
        extra_scope_refs = required_scope_refs - component_heading_refs
        resumption_proofs = []
        # More than one repeating page header can resume the same ancestor.
        # The nearest preceding occurrence is the authoritative physical
        # witness; set iteration order must never choose an older page.
        for running_ref in sorted(
                running,
                key=lambda ref: (-order_index.get(ref, -1), ref)):
            running_rank = order_index.get(running_ref)
            if (running_rank is None
                    or running_rank > record_binding_ranks[index]):
                continue
            ancestor_ref = next((
                ref for ref in reversed(source_scope_paths[index])
                if (ref in component_heading_refs
                    and order_index.get(ref, len(order)) < running_rank
                    and _running_heading_resumes(
                        items[running_ref], items[ref]))
            ), None)
            if ancestor_ref is None:
                continue
            superseded = sorted(
                (ref for ref in extra_scope_refs
                 if order_index.get(ancestor_ref, -1)
                 < order_index.get(ref, -1) < running_rank),
                key=order_index.get,
            )
            if not superseded:
                continue
            required_scope_refs.difference_update(superseded)
            extra_scope_refs.difference_update(superseded)
            resumption_proofs.append({
                "kind": "running_ancestor_resumption",
                "running_ref": running_ref,
                "ancestor_ref": ancestor_ref,
                "superseded_refs": superseded,
            })
        if resumption_proofs:
            ancestor_resumptions[index] = resumption_proofs
        scope_valid = component_heading_refs == required_scope_refs
        if not valid or not scope_valid:
            display_binding_issues.append(index)

    direct: list[list[str]] = [[] for _ in records]
    occurrences_by_record: defaultdict[str, list[int]] = defaultdict(list)
    for index, path in enumerate(expected_paths):
        metadata = records[index].get("metadata") if isinstance(
            records[index], dict) else None
        if (isinstance(metadata, dict)
                and metadata.get("retrieval_role") == "table_child"):
            continue
        for ref in path:
            occurrences_by_record[ref].append(index)
    for ref in attachable:
        candidates = occurrences_by_record.get(ref, [])
        if candidates:
            owner = min(candidates, key=lambda index: record_order[index])
            direct[owner].append(ref)

    missing = sorted(set(attachable) - set(occurrences_by_record))
    return {
        "schema_version": HEADING_LINEAGE_SCHEMA_VERSION,
        "policy": HEADING_LINEAGE_POLICY,
        "source_refs": eligible,
        "attachable_refs": attachable,
        "exception_refs": {
            RUNNING_FURNITURE_REASON: [
                ref for ref in eligible if ref in running
            ],
            EMBEDDED_OUTLINE_REASON: [
                ref for ref in eligible if ref in embedded_outline
                and ref not in running
            ],
        },
        "artifact_refs": {
            SPARSE_OCR_HEADING_ARTIFACT_REASON: [
                ref for ref in eligible if ref in artifacts
            ],
        },
        "expected_paths": expected_paths,
        "expected_direct": direct,
        "expected_components": expected_components,
        "source_scope_paths": source_scope_paths,
        "heading_groups": heading_groups,
        "missing_refs": missing,
        "scope_conflict_records": scope_conflicts,
        "display_binding_records": display_binding_issues,
        "ancestor_resumptions": ancestor_resumptions,
    }


def attach_heading_bindings(
        document: dict, records: Sequence[dict], *,
        structural_ranges: Iterable[tuple[int, int]] = (),
        excluded_heading_refs: Iterable[str] = (),
) -> dict:
    """Attach compact exact-ref bindings and return the expected audit."""
    expected = expected_heading_bindings(
        document, records, structural_ranges=structural_ranges,
        excluded_heading_refs=excluded_heading_refs)
    for record, path, direct, components in zip(
            records, expected["expected_paths"], expected["expected_direct"],
            expected["expected_components"]):
        metadata = record.setdefault("metadata", {})
        metadata[HEADING_SCHEMA_FIELD] = HEADING_LINEAGE_SCHEMA_VERSION
        metadata[HEADING_PATH_FIELD] = path
        metadata[DIRECT_HEADING_FIELD] = direct
        metadata[HEADING_COMPONENTS_FIELD] = components
    return expected


def audit_heading_bindings(
        document: dict, records: Sequence[dict], *,
        structural_ranges: Iterable[tuple[int, int]] = (),
        excluded_heading_refs: Iterable[str] = (),
) -> dict:
    """Recompute and validate every serialized occurrence binding."""
    expected = expected_heading_bindings(
        document, records, structural_ranges=structural_ranges,
        excluded_heading_refs=excluded_heading_refs)
    items, _ = _catalog(document)
    invalid_records: list[int] = []
    direct_counts: Counter[str] = Counter()
    attached: set[str] = set()
    attachable = set(expected["attachable_refs"])
    exception_refs = {
        ref for refs in expected["exception_refs"].values() for ref in refs
    }
    exception_refs.update(
        ref for refs in expected["artifact_refs"].values() for ref in refs)
    for index, (record, expected_path, expected_direct,
                expected_components) in enumerate(zip(
            records, expected["expected_paths"], expected["expected_direct"],
            expected["expected_components"])):
        metadata = record.get("metadata") if isinstance(record, dict) else None
        path = metadata.get(HEADING_PATH_FIELD) if isinstance(
            metadata, dict) else None
        direct = metadata.get(DIRECT_HEADING_FIELD) if isinstance(
            metadata, dict) else None
        components = metadata.get(HEADING_COMPONENTS_FIELD) if isinstance(
            metadata, dict) else None
        valid_lists = (
            isinstance(path, list) and isinstance(direct, list)
            and all(isinstance(ref, str) and ref for ref in path + direct)
            and path == list(dict.fromkeys(path))
            and direct == list(dict.fromkeys(direct))
        )
        if (not isinstance(metadata, dict)
                or metadata.get(HEADING_SCHEMA_FIELD)
                != HEADING_LINEAGE_SCHEMA_VERSION
                or not valid_lists
                or path != expected_path or direct != expected_direct
                or components != expected_components
                or any(ref not in items for ref in path)
                or any(ref not in attachable for ref in direct)
                or any(ref in exception_refs for ref in path + direct)
                or any(ref not in path for ref in direct)):
            invalid_records.append(index)
            continue
        attached.update(path)
        direct_counts.update(direct)
    duplicate_direct = sorted(
        ref for ref, count in direct_counts.items() if count != 1
    )
    unattached = sorted(attachable - attached)
    missing_direct = sorted(attachable - set(direct_counts))
    return {
        **expected,
        "attached_refs": sorted(attached),
        "unattached_refs": unattached,
        "missing_direct_refs": missing_direct,
        "duplicate_direct_refs": duplicate_direct,
        "invalid_binding_records": invalid_records,
    }
