"""Fixed historical scan bundles and exact original-raster review previews.

Historical execution metadata is a declaration, not a current environment or
authenticity attestation. Overlay/crop access additionally requires reproducing
the declared original gray raster from the exact local source bytes. Neither
that pixel match nor region overlap verifies transcription or source coverage.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path

from evaluation_inputs import _strict_json_bytes
from ocr_docling import _exact
from ocr_scan_io import _directory, _LIMITS, _snapshot, MAX_SOURCE_BYTES


DISPLAY_SIDE = 1400


class ScanReview:
    """The operator fixes every path; browser events supply only page/region IDs."""

    def __init__(self, bundle_path: Path, pdf_path: Path, *, source_sha256: str,
                 page_count: int, recovery: dict, recovery_sha256: str,
                 proposals: dict | None = None, proposals_sha256: str | None = None):
        from ocr_scan_io import read_scan_review_bundle

        self.bundle_path, self.pdf_path = Path(bundle_path).absolute(), Path(pdf_path).absolute()
        self._directory_identity = _directory(self.bundle_path)
        self._source_sha256, self._page_count = source_sha256, page_count
        _, digest, self._source_identity = _snapshot(self.pdf_path, MAX_SOURCE_BYTES)
        if digest != source_sha256:
            raise ValueError("scan review source differs from the current workspace")
        snapshots = {name: _snapshot(self.bundle_path / name, limit) for name, limit in _LIMITS.items()}
        bundle = read_scan_review_bundle(self.bundle_path, source_sha256=source_sha256,
            page_count=page_count, recovery=recovery, recovery_sha256=recovery_sha256,
            proposals=proposals, proposals_sha256=proposals_sha256)
        # Bind the loader's values to the exact snapshots this adapter will
        # recheck, not merely to a second loosely corresponding read.
        for name, (raw, _, _) in snapshots.items():
            parsed = _strict_json_bytes(raw, label="scan review artifact", max_bytes=_LIMITS[name])
            if not _exact(parsed, bundle[name.removesuffix(".json")]):
                raise RuntimeError("scan review bundle changed during admission")
        self._artifacts = {name: (digest, identity) for name, (_, digest, identity) in snapshots.items()}
        self._scan = copy.deepcopy(bundle["scan"])
        self._diagnostics = copy.deepcopy(bundle["review_diagnostics"])
        self.verify_inputs()

    def verify_inputs(self) -> None:
        if _directory(self.bundle_path) != self._directory_identity:
            raise RuntimeError("scan review bundle directory changed")
        _, digest, identity = _snapshot(self.pdf_path, MAX_SOURCE_BYTES)
        if digest != self._source_sha256 or identity != self._source_identity:
            raise RuntimeError("scan review source generation changed")
        for name, expected in self._artifacts.items():
            _, digest, identity = _snapshot(self.bundle_path / name, _LIMITS[name])
            if (digest, identity) != expected:
                raise RuntimeError("scan review bundle generation changed")
        if _directory(self.bundle_path) != self._directory_identity:
            raise RuntimeError("scan review bundle directory changed")

    def _number(self, number: int) -> int:
        if type(number) is not int or not 1 <= number <= self._page_count:
            raise ValueError("invalid scan review page")
        return number

    def page(self, number: int) -> dict:
        self._number(number)
        self.verify_inputs()
        page = next((page for page in self._diagnostics["pages"] if page["page_number"] == number), None)
        if page is None:
            return {"page_number": number, "scan_status": "not_requested", "scan_reason": "outside_scan_cohort",
                    "candidate_state": "unevaluated", "ocr_comparison_state": "scan_not_requested",
                    "layout_comparison_state": "scan_not_requested", "regions": []}
        return copy.deepcopy(page)

    def render(self, number: int):
        """Return a bounded original preview only after an exact gray pixel join.

        No preprocessing replay, OCR calls, model imports, or pixel discovery.
        Missing/unrequested rasters return None, not a fabricated blank scan.
        The returned image is display-sized; region coordinates remain original
        displayed-page fractions, never candidate-preprocessed fractions.
        """
        self._number(number)
        self.verify_inputs()
        observed = next((page for page in self._scan["pages"] if page["page_number"] == number), None)
        if observed is None or observed["raster"] is None:
            return None
        raw, digest, identity = _snapshot(self.pdf_path, MAX_SOURCE_BYTES)
        if digest != self._source_sha256 or identity != self._source_identity:
            raise RuntimeError("scan review source generation changed")
        import pymupdf
        from PIL import Image

        geometry, raster = observed["geometry"], observed["raster"]
        with pymupdf.open(stream=raw, filetype="pdf") as document:
            if not document.is_pdf or document.needs_pass or len(document) != self._page_count:
                raise ValueError("scan review PDF does not match the declared source")
            page = document[number - 1]
            actual = {"width_points": float(page.rect.width), "height_points": float(page.rect.height),
                      "rotation": page.rotation, "cropbox": list(page.cropbox)}
            if (not all(math.isfinite(value) for value in page.rect)
                    or page.rect.x0 != 0 or page.rect.y0 != 0 or actual != geometry):
                raise ValueError("scan review displayed geometry differs from the observation")
            matrix = pymupdf.Matrix(raster["dpi"] / 72, raster["dpi"] / 72)
            bounds = (page.rect * matrix).irect
            width, height = bounds.width, bounds.height
            if (bounds.x0 != 0 or bounds.y0 != 0 or not 0 < width <= 6000 or not 0 < height <= 6000 or width * height > 25_000_000
                    or (width, height) != (raster["width"], raster["height"])):
                raise ValueError("scan review raster differs from bounded original geometry")
            pixmap = page.get_pixmap(matrix=matrix, colorspace=pymupdf.csGRAY, alpha=False,
                                    annots=self._scan["configuration"]["render_annotations"])
            if (pixmap.x != 0 or pixmap.y != 0 or len(pixmap.samples_mv) != width * height
                    or (pixmap.width, pixmap.height, pixmap.n, pixmap.stride) != (width, height, 1, width)):
                raise ValueError("scan review renderer returned an unexpected raster")
            pixel_digest = hashlib.sha256(self._scan["configuration"]["pixel_digest_domain"].encode("ascii") + b"\0")
            pixel_digest.update(json.dumps([height, width], separators=(",", ":")).encode("ascii") + b"\0")
            pixel_digest.update(pixmap.samples_mv)
            if pixel_digest.hexdigest() != raster["pixel_sha256"]:
                raise ValueError("scan review could not reproduce the declared original pixels")
            image = Image.frombytes("L", (width, height), pixmap.samples)
            image.thumbnail((DISPLAY_SIDE, DISPLAY_SIDE))
            result = image.convert("RGB")
        self.verify_inputs()
        return result

    def region(self, number: int, region_id: str) -> list[float]:
        """A crop proposal requires a reproducible original canvas, not approval."""
        if type(region_id) is not str:
            raise ValueError("invalid scan region identity")
        page = self.page(number)
        region = next((region for region in page["regions"] if region["region_id"] == region_id), None)
        if region is None or self.render(number) is None:
            raise ValueError("scan region cannot be located on a reproduced original canvas")
        self.verify_inputs()
        return list(region["bbox"])
