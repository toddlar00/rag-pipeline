"""Dependency-light visual-review annotations bound to one saved OCR report.

Selections are made on the candidate canvas. Region crops are conservatively
mapped back to the original displayed PDF page; layout plans remain in their
existing candidate-canvas coordinate space. Neither operation accepts text.
"""

from __future__ import annotations

import copy
import difflib
import math

from evaluation_inputs import _hex_digest
from ocr_layout import build_layout_review, validate_layout_plan
from ocr_recovery_comparison import validate_recovery_report, validate_references


def rectangle(value: object) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError("select a rectangle on the page")
    if any(type(v) not in (int, float) or not 0 <= v <= 1 or not math.isfinite(v) for v in value):
        raise ValueError("rectangle coordinates must be finite page fractions")
    left, top, right, bottom = value
    if not left < right or not top < bottom:
        raise ValueError("select a rectangle with positive width and height")
    return [float(v) for v in value]


def click_rectangle(first: object, second: object, *, width: int, height: int) -> list[float]:
    """Convert two display-pixel clicks to a direction-independent rectangle."""
    if any(type(v) is not int or not 1 <= v <= 6000 for v in (width, height)):
        raise ValueError("invalid review canvas dimensions")
    for point in (first, second):
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise ValueError("select two page corners")
        if any(type(v) not in (int, float) for v in point):
            raise ValueError("invalid page click")
        if not 0 <= point[0] <= width or not 0 <= point[1] <= height:
            raise ValueError("page click lies outside the canvas")
    return rectangle([min(first[0], second[0]) / width, min(first[1], second[1]) / height,
                      max(first[0], second[0]) / width, max(first[1], second[1]) / height])


class ReviewDocument:
    """Immutable-by-copy report binding; browser state never chooses file paths."""

    def __init__(self, recovery: object, *, recovery_sha256: str):
        _hex_digest(recovery_sha256, label="review recovery digest")
        self._recovery = copy.deepcopy(validate_recovery_report(recovery))
        self.recovery_sha256 = recovery_sha256
        self.source_sha256 = self._recovery["source_sha256"]
        self.page_count = self._recovery["page_count"]
        self.page_numbers = tuple(range(1, self.page_count + 1))
        self._selected = {page["page_number"]: page for page in self._recovery["pages"]}
        self._deferred = {page["page_number"]: page for page in self._recovery["deferred_pages"]}

    def page(self, number: object) -> dict:
        if type(number) is not int or number not in self.page_numbers:
            raise ValueError("choose a page in the bound source")
        if number in self._selected:
            return copy.deepcopy(self._selected[number])
        # This is an editor view, not a manufactured successful recovery record.
        deferred = self._deferred.get(number)
        return {"page_number": number, "candidate": None, "original_text": None,
                "status": "deferred" if deferred is not None else "not_selected",
                "reasons": copy.deepcopy(deferred["reasons"]) if deferred is not None else []}

    def recovery_snapshot(self) -> dict:
        return copy.deepcopy(self._recovery)

    def context_candidate_pages(self) -> list[dict]:
        """Expose only actual candidate text, with explicit unavailable coverage."""
        pages = [{"page_number": page["page_number"],
                  "status": "retry_failed" if page["candidate"] is None else "available",
                  "text": None if page["candidate"] is None else page["candidate"]["text"]}
                 for page in self._recovery["pages"]]
        pages.extend({"page_number": page["page_number"], "status": "deferred", "text": None}
                     for page in self._recovery["deferred_pages"])
        return pages

    def review_choices(self) -> list[tuple[str, int]]:
        """Coverage-first navigation, not a calibrated estimate of error risk."""
        priority = {"retry_failed": 0, "empty_candidate": 1, "deferred": 2,
                    "not_selected": 3, "review_required": 4}
        pages = [self.page(number) for number in self.page_numbers]
        pages.sort(key=lambda page: (priority[page["status"]], page["page_number"]))
        return [(f"Page {p['page_number']} - {p['status'].replace('_', ' ')}", p["page_number"])
                for p in pages]

    def page_text(self, number: int) -> str:
        page = self.page(number)
        return page["candidate"]["text"] if page["candidate"] is not None else page["original_text"] or ""

    def page_notice(self, number: int) -> str:
        page = self.page(number)
        status = page["status"].replace("_", " ")
        if page["candidate"] is None:
            return (f"Page {number}: {status}. No OCR candidate is available. Original scan shown; "
                    "draw a retry crop or transcribe a reference. Layout reordering requires OCR boxes.")
        return f"Page {number}: {status}. Check the scan and OCR boxes; a candidate is not verified text."

    def layout_plan(self, pages: list[dict]) -> dict:
        plan = validate_layout_plan({
            "schema_version": 1, "source_sha256": self.source_sha256,
            "recovery_sha256": self.recovery_sha256,
            "coordinate_system": "candidate_raster_fraction", "pages": pages,
        }, source_sha256=self.source_sha256, recovery_sha256=self.recovery_sha256,
            page_count=self.page_count)
        if any(self.page(p["page_number"])["candidate"] is None for p in plan["pages"]):
            raise ValueError("layout reordering requires an available OCR candidate")
        return plan

    def layout_page(self, number: int, body: object, gutter: object) -> dict:
        self.page(number)
        body, gutter = rectangle(body), rectangle(gutter)
        return self.layout_plan([{
            "page_number": number, "body_band": body[1::2], "gutter": gutter[::2],
            "order": "left_then_right",
        }])["pages"][0]

    def preview_layout(self, pages: list[dict]) -> dict:
        return build_layout_review(self._recovery, self.layout_plan(pages),
                                   recovery_sha256=self.recovery_sha256)

    def source_rectangle(self, number: int, bounds: object) -> list[float]:
        candidate = self.page(number)["candidate"]
        bounds = rectangle(bounds)
        if candidate is None or "preprocessing" not in candidate:
            return bounds
        width, height = (candidate["raster"][key] for key in ("width", "height"))
        metadata = candidate["preprocessing"]
        source_width, source_height = (metadata["original_raster"][key] for key in ("width", "height"))
        matrix = metadata["processed_to_source"]
        points = []
        for x, y in ((bounds[0], bounds[1]), (bounds[2], bounds[1]),
                     (bounds[2], bounds[3]), (bounds[0], bounds[3])):
            x, y = x * width, y * height
            points.append(((matrix[0][0] * x + matrix[0][1] * y + matrix[0][2]) / source_width,
                           (matrix[1][0] * x + matrix[1][1] * y + matrix[1][2]) / source_height))
        # The expanded deskew canvas has padding. Intersect its inverse envelope
        # with the real page; a padding-only selection is rejected below.
        return rectangle([max(0., min(p[0] for p in points)), max(0., min(p[1] for p in points)),
                          min(1., max(p[0] for p in points)), min(1., max(p[1] for p in points))])

    def references(self, pages: list[dict], *, confirmed: bool) -> dict:
        if confirmed is not True:
            raise ValueError("confirm that reference text was checked against the scan")
        payload = {"schema_version": 1, "source_sha256": self.source_sha256, "pages": copy.deepcopy(pages)}
        return validate_references(payload, page_count=self.page_count)


def text_difference(original: str, proposed: str) -> str:
    """Bounded, plain-text line diff (never interpret OCR text as HTML)."""
    if any(not isinstance(text, str) or len(text) > 100_000 for text in (original, proposed)):
        raise ValueError("review text exceeds the display limit")
    # SequenceMatcher's worst case is quadratic. Long pages get a clear
    # side-by-side alternative instead of unbounded diff work.
    before, after = original.splitlines(), proposed.splitlines()
    if len(before) * len(after) > 250_000:
        return "Diff omitted for a large page; compare the original and proposed text panels."
    return "\n".join(difflib.unified_diff(before, after, fromfile="original", tofile="proposal", lineterm="")) or "No text differences."
