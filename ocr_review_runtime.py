"""Fixed-input local OCR review workspace, bounded rendering and private exports."""

from __future__ import annotations

import math
import copy
from dataclasses import dataclass
import json
from pathlib import Path
import re
import stat
from uuid import uuid4

from evaluation_inputs import _read_snapshot
from ocr_layout import validate_layout_plan
from ocr_recovery import _publish_new_report
from ocr_recovery_comparison import _load, validate_references
from ocr_review import ReviewDocument, rectangle
from resource_lease import PathLease
import storage_policy


MAX_PDF_BYTES = 256 * 1024 * 1024
MAX_REPORT_BYTES = 64 * 1024 * 1024
MAX_DRAFT_BYTES = 16 * 1024 * 1024
DISPLAY_SIDE = 1400
MAX_AUDIT_BYTES = 8 * 1024 * 1024
MAX_CONTEXT_REFERENCE_BYTES = 8 * 1024 * 1024
MAX_CONTEXT_CORRESPONDENCE_BYTES = 1024 * 1024


@dataclass(frozen=True, repr=False)
class ContextExportBinding:
    """Host-created, session-owned file binding; not an approval credential."""

    generation: tuple
    owner: str
    kind: str
    path: Path
    sha256: str
    reference_sha256: str | None = None
    correspondence_sha256: str | None = None

    def __deepcopy__(self, memo):
        return self  # All fields are immutable; do not duplicate session ownership.


class ReviewDownloadError(RuntimeError):
    """An export was saved, but its browser copy was not verified."""


class ReviewDraftSizeError(ValueError):
    """A generated snapshot would exceed the exact restart input byte limit."""


def _check_draft_size(payload: dict) -> None:
    if _json_exceeds(payload, MAX_DRAFT_BYTES):
        raise ReviewDraftSizeError("review draft exceeds its restart byte limit")


def _json_exceeds(payload: dict, limit: int) -> bool:
    # Match storage_policy.atomic_write_private_json(indent=2), including its
    # trailing LF, without allocating a second full serialized snapshot.
    size = 1
    for chunk in json.JSONEncoder(ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False).iterencode(payload):
        size += len(chunk.encode("utf-8"))
        if size > limit:
            return True
    return False


class ReviewWorkspace:
    """Paths are fixed by the local operator, never supplied by browser events."""

    def __init__(self, pdf_path: Path, recovery_path: Path, output_dir: Path, *, draft_path: Path | None = None,
                 proposals_path: Path | None = None, scan_bundle_path: Path | None = None,
                 audit_path: Path | None = None):
        self.pdf_path, self.recovery_path, self.output_dir = (
            Path(path).absolute() for path in (pdf_path, recovery_path, output_dir))
        self._context_export_owner = object()
        for path in (self.pdf_path, self.recovery_path, self.output_dir):
            storage_policy.assert_no_link_components(path)
        if not self.output_dir.is_dir():
            raise ValueError("review output directory must already exist")
        self._directory_identity = self._directory_generation()
        recovery, digest = _load(self.recovery_path, label="OCR review report", limit=MAX_REPORT_BYTES)
        self.document = ReviewDocument(recovery, recovery_sha256=digest)
        self.draft_path, self.draft_sha256, self.initial_draft = None, None, None
        self.proposals_path, self.proposals_sha256, self.proposals = None, None, None
        self.scan_bundle_path, self._scan_review = None, None
        self.audit_path, self.audit_sha256, self.initial_audit = None, None, None
        if proposals_path is not None:
            from ocr_docling import validate_docling_proposals

            self.proposals_path = Path(proposals_path).absolute()
            storage_policy.assert_no_link_components(self.proposals_path)
            proposals, self.proposals_sha256 = _load(self.proposals_path, label="Docling proposals", limit=MAX_REPORT_BYTES)
            self.proposals = validate_docling_proposals(proposals, recovery=recovery, recovery_sha256=digest)
        if draft_path is not None:
            from ocr_review_drafts import restore_state

            self.draft_path = Path(draft_path).absolute()
            storage_policy.assert_no_link_components(self.draft_path)
            draft, self.draft_sha256 = _load(self.draft_path, label="OCR review draft", limit=MAX_DRAFT_BYTES)
            self.initial_draft = restore_state(draft, self.document, proposals=self.proposals,
                                               proposals_sha256=self.proposals_sha256)
        if audit_path is not None:
            from ocr_spot_audit import validate_audit

            self.audit_path = Path(audit_path).absolute()
            storage_policy.assert_no_link_components(self.audit_path)
            audit, self.audit_sha256 = _load(self.audit_path, label="OCR spot audit", limit=MAX_AUDIT_BYTES)
            if type(audit) is not dict:
                raise ValueError("invalid OCR spot audit snapshot")
            # Declared ancestry is historical data, not authenticated approval.
            audit = validate_audit(audit, self.document,
                                   parent_audit_sha256=audit.get("parent_audit_sha256"))
            audit["parent_audit_sha256"] = self.audit_sha256
            self.initial_audit = validate_audit(audit, self.document, parent_audit_sha256=self.audit_sha256)
        self.verify_inputs()
        if scan_bundle_path is not None:
            from ocr_scan_review import ScanReview

            self._scan_review = ScanReview(scan_bundle_path, self.pdf_path,
                source_sha256=self.document.source_sha256, page_count=self.document.page_count,
                recovery=self.document.recovery_snapshot(), recovery_sha256=self.document.recovery_sha256,
                proposals=self.proposals, proposals_sha256=self.proposals_sha256)
            self.scan_bundle_path = self._scan_review.bundle_path
            self.verify_inputs()

    def _directory_generation(self) -> tuple[int, int]:
        storage_policy.assert_no_link_components(self.output_dir)
        stat = self.output_dir.stat()
        if not self.output_dir.is_dir():
            raise ValueError("review output is no longer a directory")
        return stat.st_dev, stat.st_ino

    def verify_inputs(self) -> None:
        inputs = [
            (self.pdf_path, self.document.source_sha256, MAX_PDF_BYTES),
            (self.recovery_path, self.document.recovery_sha256, MAX_REPORT_BYTES),
        ]
        if self.draft_path is not None:
            inputs.append((self.draft_path, self.draft_sha256, MAX_DRAFT_BYTES))
        if self.proposals_path is not None:
            inputs.append((self.proposals_path, self.proposals_sha256, MAX_REPORT_BYTES))
        if self.audit_path is not None:
            inputs.append((self.audit_path, self.audit_sha256, MAX_AUDIT_BYTES))
        for path, expected, limit in inputs:
            storage_policy.assert_no_link_components(path)
            _, digest = _read_snapshot(path, label="OCR review input", max_bytes=limit)
            if digest != expected:
                raise RuntimeError("review inputs changed; restart with matching artifacts")
        if self._directory_generation() != self._directory_identity:
            raise RuntimeError("review output directory changed")
        if self._scan_review is not None:
            self._scan_review.verify_inputs()

    def scan_page(self, number: int) -> dict | None:
        self.document.page(number)
        self.verify_inputs()
        return None if self._scan_review is None else self._scan_review.page(number)

    def scan_render(self, number: int):
        self.document.page(number)
        self.verify_inputs()
        return None if self._scan_review is None else self._scan_review.render(number)

    def scan_region(self, number: int, region_id: str) -> list[float]:
        self.document.page(number)
        self.verify_inputs()
        if self._scan_review is None:
            raise ValueError("no fixed scan bundle loaded")
        return self._scan_review.region(number, region_id)

    def render(self, number: int):
        """Replay saved affine/contrast metadata, not fresh preprocessing guesses.

        The scan is a visual aid, not a byte-identical old raster attestation.
        No OCR engine/model is loaded. The returned PIL image is display-sized;
        annotations still refer to fractions of the exact candidate canvas.
        """
        candidate = self.document.page(number)["candidate"]
        storage_policy.assert_no_link_components(self.pdf_path)
        raw, digest = _read_snapshot(self.pdf_path, label="OCR review PDF", max_bytes=MAX_PDF_BYTES)
        if digest != self.document.source_sha256:
            raise RuntimeError("review source changed")
        import pymupdf
        import numpy as np
        from PIL import Image

        with pymupdf.open(stream=raw, filetype="pdf") as pdf:
            if not pdf.is_pdf or pdf.needs_pass or len(pdf) != self.document.page_count:
                raise ValueError("review PDF does not match the report")
            page = pdf[number - 1]
            if any(not math.isfinite(v) or v <= 0 for v in (page.rect.width, page.rect.height)):
                raise ValueError("invalid review page geometry")
            if candidate is None:
                # An unprocessed display preview has no OCR raster to replay.
                # Admit a small raster before allocation, even for huge pages.
                scale = min(2., DISPLAY_SIDE / max(page.rect.width, page.rect.height))
                matrix = pymupdf.Matrix(scale, scale)
                original = None
            else:
                raster = candidate["raster"]
                original = candidate.get("preprocessing", {}).get("original_raster", raster)
                matrix = pymupdf.Matrix(raster["dpi"] / 72, raster["dpi"] / 72)
            bounds = (page.rect * matrix).irect
            width, height = bounds.width, bounds.height
            if (original is not None and (width != original["width"] or height != original["height"])
                    or not 0 < width <= 6000 or not 0 < height <= 6000 or width * height > 25_000_000):
                raise ValueError("review raster does not match the bounded PDF geometry")
            if candidate is None:
                raster = {"width": width, "height": height}
            pixmap = page.get_pixmap(matrix=matrix, colorspace=pymupdf.csRGB, alpha=False)
            if (pixmap.width, pixmap.height, pixmap.n, pixmap.stride) != (width, height, 3, width * 3):
                raise ValueError("review renderer returned an unexpected raster")
            pixels = np.frombuffer(pixmap.samples_mv, dtype=np.uint8).reshape(height, width, 3).copy()
        if candidate is not None and "preprocessing" in candidate:
            import cv2

            metadata = candidate["preprocessing"]
            if metadata["deskew"]["status"] == "applied":
                pixels = cv2.warpAffine(pixels, np.array(metadata["source_to_processed"], dtype=np.float64),
                                        (raster["width"], raster["height"]), flags=cv2.INTER_LINEAR,
                                        borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255))
            contrast = metadata["contrast"]
            if contrast["status"] == "applied":
                low, high = contrast["low_level"], contrast["high_level"]
                table = np.clip(np.rint((np.arange(256, dtype=np.float64) - low) * 255 / (high - low)),
                                0, 255).astype(np.uint8)
                pixels = cv2.cvtColor(cv2.LUT(cv2.cvtColor(pixels, cv2.COLOR_RGB2GRAY), table), cv2.COLOR_GRAY2RGB)
        if pixels.shape[:2] != (raster["height"], raster["width"]):
            raise ValueError("review canvas differs from the OCR candidate")
        image = Image.fromarray(pixels)
        image.thumbnail((DISPLAY_SIDE, DISPLAY_SIDE))
        return image

    def render_original_page(self, number: int):
        """Bounded original PDF display, independent of OCR/preprocessing metadata.

        This is an in-process preview, not a decoder deadline/memory guarantee
        or a byte-identical historic OCR raster. No inference or overlays run.
        """
        def binding():
            return (self.document, self.document.source_sha256, self.document.recovery_sha256,
                    self.document.page_count, self.pdf_path, self.recovery_path, self.output_dir,
                    self._directory_identity, self.audit_path, self.audit_sha256,
                    self.draft_path, self.draft_sha256, self.proposals_path, self.proposals_sha256,
                    self.scan_bundle_path, self._scan_review)

        fixed = binding()
        self.document.page(number)
        self.verify_inputs()
        if binding() != fixed:
            raise RuntimeError("audit workspace binding changed")
        raw, digest = _read_snapshot(fixed[4], label="OCR audit source", max_bytes=MAX_PDF_BYTES)
        if digest != fixed[1]:
            raise RuntimeError("review inputs changed; restart with matching artifacts")
        import pymupdf
        from PIL import Image

        image = None
        try:
            with pymupdf.open(stream=raw, filetype="pdf") as pdf:
                if pdf.page_count != fixed[3]:
                    raise ValueError("audit source page count differs from the report")
                page = pdf.load_page(number - 1)
                rect = page.rect
                if any(not math.isfinite(value) for value in (rect.x0, rect.y0, rect.x1, rect.y1)) or not (
                        0 < rect.width <= 1_000_000 and 0 < rect.height <= 1_000_000):
                    raise ValueError("audit source page geometry is unavailable")
                # Leave a pixel for outward rectangle rounding. The actual
                # allocation, not a post-render thumbnail, must meet the cap.
                scale = min(2., (DISPLAY_SIDE - 1) / max(rect.width, rect.height))
                matrix = pymupdf.Matrix(scale, scale)
                bounds = (rect * matrix).irect
                width, height = bounds.width, bounds.height
                if not (0 < width <= DISPLAY_SIDE and 0 < height <= DISPLAY_SIDE
                        and width * height <= DISPLAY_SIDE * DISPLAY_SIDE):
                    raise ValueError("audit preview exceeds its raster bound")
                pixmap = page.get_pixmap(matrix=matrix, colorspace=pymupdf.csRGB, alpha=False)
                if (pixmap.width, pixmap.height, pixmap.n, pixmap.stride) != (width, height, 3, width * 3):
                    raise ValueError("audit renderer returned an unexpected raster")
                samples = pixmap.samples
                if len(samples) != width * height * 3:
                    raise ValueError("audit renderer returned an unexpected byte count")
                image = Image.frombytes("RGB", (width, height), samples)
            self.verify_inputs()
            if binding() != fixed:
                raise RuntimeError("audit workspace binding changed")
            result, image = image, None
            return result
        finally:
            if image is not None:
                try:
                    image.close()
                except BaseException:
                    pass  # Preserve the input/render failure; no image escapes.

    def region_plan(self, regions: list[dict]) -> dict:
        from ocr_regions import validate_region_plan

        return validate_region_plan({
            "schema_version": 1, "source_sha256": self.document.source_sha256,
            "recovery_sha256": self.document.recovery_sha256,
            "coordinate_system": "original_page_display_fraction", "regions": regions,
        }, source_sha256=self.document.source_sha256, recovery_sha256=self.document.recovery_sha256,
            page_count=self.document.page_count)

    def column_suggestion(self, number: int) -> dict:
        """One explicit page, no OCR execution or layout/accuracy approval."""
        from ocr_column_suggestions import build_column_suggestions

        self.verify_inputs()
        self.document.page(number)
        report = build_column_suggestions(self.document.recovery_snapshot(),
            recovery_sha256=self.document.recovery_sha256, requested_pages=[number])
        self.verify_inputs()
        return report

    def column_suggestion_plan(self, number: int, report: object) -> dict:
        """Rebuild a pending hypothesis against the fixed inputs before use."""
        from ocr_column_suggestions import validate_column_suggestions

        self.verify_inputs()
        self.document.page(number)
        rebuilt = validate_column_suggestions(report, recovery=self.document.recovery_snapshot(),
                                               recovery_sha256=self.document.recovery_sha256)
        if rebuilt["coverage"]["requested_pages"] != [number]:
            raise ValueError("column suggestion must describe only the current page")
        page = rebuilt["pages"][0]
        if page["status"] != "unconfirmed_hypothesis" or page["plan"] is None:
            raise ValueError("no column hypothesis to preview")
        self.verify_inputs()
        return copy.deepcopy(page["plan"])

    def proposal_page(self, number: int) -> dict | None:
        self.document.page(number)
        return copy.deepcopy(next((p for p in (self.proposals or {}).get("pages", [])
                                   if p["page_number"] == number), None))

    def docling_review(self, pages: list[int]) -> dict:
        from ocr_docling import build_docling_review

        if self.proposals is None:
            raise ValueError("no fixed Docling proposals loaded")
        return build_docling_review(self.proposals, recovery=self.document.recovery_snapshot(),
                                    recovery_sha256=self.document.recovery_sha256,
                                    proposals_sha256=self.proposals_sha256, confirmed_pages=pages)

    def assignment_bindings(self) -> dict:
        if self.proposals is None:
            raise ValueError("layout assignment requires fixed Docling proposals")
        return {"recovery": self.document.recovery_snapshot(), "recovery_sha256": self.document.recovery_sha256,
                "proposals": copy.deepcopy(self.proposals), "proposals_sha256": self.proposals_sha256}

    def assignment_suggestion(self, number: int) -> dict:
        from ocr_layout_assignment import suggest_assignment_page

        self.verify_inputs()
        return suggest_assignment_page(number, **self.assignment_bindings())

    def assignment_preview(self, pages: list[dict]) -> dict:
        from ocr_layout_assignment import build_assignment_plan, preview_assignment_plan

        self.verify_inputs()
        bindings = self.assignment_bindings()
        return preview_assignment_plan(build_assignment_plan(pages, **bindings), **bindings)

    def assignment_review(self, pages: list[dict], confirmed_pages: list[int]) -> dict:
        from ocr_layout_assignment import build_assignment_plan, build_assignment_review

        self.verify_inputs()
        bindings = self.assignment_bindings()
        return build_assignment_review(build_assignment_plan(pages, **bindings),
                                       confirmed_pages=confirmed_pages, **bindings)

    def omission_diagnostics(self) -> dict | None:
        """Saved-region geometry warnings, never a full-source completeness check."""
        from ocr_omission import build_omission_diagnostics

        self.verify_inputs()
        if self.proposals is None:
            return None
        return build_omission_diagnostics(**self.assignment_bindings())

    def omission_review(self, decisions: list[dict], *, confirmed: bool) -> dict:
        from ocr_omission import build_omission_review

        self.verify_inputs()
        return build_omission_review(**self.assignment_bindings(), decisions=decisions, confirmed=confirmed)

    def _context_generation(self) -> tuple:
        return (self._context_export_owner, self.pdf_path, self.recovery_path, self.output_dir, self._directory_identity,
                self.document.source_sha256, self.document.recovery_sha256, self.document.page_count)

    def _read_context_export(self, binding, *, kind: str, owner: str) -> dict:
        if (type(owner) is not str or not 1 <= len(owner) <= 128
                or type(binding) is not ContextExportBinding or binding.owner != owner
                or binding.kind != kind or binding.generation != self._context_generation()
                or not isinstance(binding.path, Path) or binding.path.parent != self.output_dir
                or re.fullmatch(r"ocr-" + re.escape(kind) + r"-[0-9a-f]{32}\.json", binding.path.name) is None):
            raise ValueError("context export does not belong to this session and source")
        limit = (MAX_CONTEXT_CORRESPONDENCE_BYTES if kind == "context-correspondence"
                 else MAX_CONTEXT_REFERENCE_BYTES)
        storage_policy.assert_no_link_components(binding.path)
        payload, digest = _load(binding.path, label="OCR context export", limit=limit)
        if digest != binding.sha256:
            raise RuntimeError("context export changed; retained files require fresh review")
        return payload

    def _write_context_export(self, payload: dict, *, kind: str, owner: str,
                              predecessors: tuple = ()) -> ContextExportBinding:
        if type(owner) is not str or not 1 <= len(owner) <= 128:
            raise ValueError("invalid context session owner")
        limit = (MAX_CONTEXT_REFERENCE_BYTES if kind == "context-reference"
                 else MAX_CONTEXT_CORRESPONDENCE_BYTES)
        if _json_exceeds(payload, limit):
            raise ValueError("context export exceeds its input byte limit")
        self.verify_inputs()
        generation = self._context_generation()
        destination = self.output_dir / f"ocr-{kind}-{uuid4().hex}.json"

        def recheck():
            self.verify_inputs()
            if self._context_generation() != generation:
                raise RuntimeError("context workspace changed")
            for binding in predecessors:
                self._read_context_export(binding, kind=binding.kind, owner=owner)

        with PathLease(destination, backend="ocr-editor", collection_name="context", operation="export",
                       timeout=0, resource_description="OCR context export"):
            def publish(temporary, target):
                recheck()
                if Path(temporary).stat().st_size > limit:
                    raise ValueError("serialized context export exceeds its input byte limit")
                _publish_new_report(temporary, target)

            storage_policy.atomic_write_private_json(destination, payload, indent=2, replace_fn=publish)
        recheck()
        retained, digest = _load(destination, label="OCR context export", limit=limit)
        if retained != payload:
            raise RuntimeError("published context export differs from the reviewed payload")
        recheck()
        return ContextExportBinding(generation, owner, kind, destination, digest,
                                    predecessors[0].sha256 if predecessors else None)

    def save_context_reference(self, authoring: object, *, confirmed: bool, owner: str) -> ContextExportBinding:
        from ocr_context_authoring import build_reference

        self.verify_inputs()
        generation = self._context_generation()
        payload = build_reference(authoring, self.document, confirmed=confirmed)
        if self._context_generation() != generation:
            raise RuntimeError("context workspace changed")
        return self._write_context_export(payload, kind="context-reference", owner=owner)

    def save_context_correspondence(self, authoring: object, reference_binding: ContextExportBinding, *,
                                    confirmed: bool, owner: str) -> ContextExportBinding:
        from ocr_context_authoring import build_correspondence

        self.verify_inputs()
        generation = self._context_generation()
        reference = self._read_context_export(reference_binding, kind="context-reference", owner=owner)
        payload = build_correspondence(authoring, self.document, reference, reference_binding.sha256,
                                       confirmed=confirmed)
        if self._context_generation() != generation:
            raise RuntimeError("context workspace changed")
        return self._write_context_export(payload, kind="context-correspondence", owner=owner,
                                          predecessors=(reference_binding,))

    def _read_context_pair(self, reference_binding, correspondence_binding, *, owner: str) -> tuple[dict, dict]:
        from ocr_context_evaluation import validate_context_inputs

        reference = self._read_context_export(reference_binding, kind="context-reference", owner=owner)
        correspondence = self._read_context_export(correspondence_binding, kind="context-correspondence", owner=owner)
        if correspondence_binding.reference_sha256 != reference_binding.sha256:
            raise ValueError("context exports belong to different reference generations")
        reference, correspondence, _ = validate_context_inputs(
            reference, correspondence, source_sha256=self.document.source_sha256,
            recovery_sha256=self.document.recovery_sha256, reference_sha256=reference_binding.sha256,
            page_count=self.document.page_count, candidate_pages=self.document.context_candidate_pages())
        return reference, correspondence

    def evaluate_context_exports(self, reference_binding: ContextExportBinding,
                                 correspondence_binding: ContextExportBinding, *, owner: str) -> tuple[dict, ContextExportBinding]:
        from ocr_context_evaluation_io import evaluate_context_files

        self.verify_inputs()
        generation = self._context_generation()
        self._read_context_pair(reference_binding, correspondence_binding, owner=owner)
        destination = self.output_dir / f"ocr-context-evaluation-{uuid4().hex}.json"
        result = evaluate_context_files(self.pdf_path, self.recovery_path, reference_binding.path,
                                        correspondence_binding.path, destination)
        self.verify_inputs()
        if self._context_generation() != generation:
            raise RuntimeError("context workspace changed")
        self._read_context_pair(reference_binding, correspondence_binding, owner=owner)
        retained, digest = _load(destination, label="OCR context result", limit=MAX_CONTEXT_REFERENCE_BYTES)
        if retained != result:
            raise RuntimeError("published context result differs from evaluation")
        binding = ContextExportBinding(generation, owner, "context-evaluation", destination, digest,
                                       reference_binding.sha256, correspondence_binding.sha256)
        checked = self.read_context_result(reference_binding, correspondence_binding, binding, owner=owner)
        return checked["report"], binding

    def read_context_result(self, reference_binding: ContextExportBinding,
                            correspondence_binding: ContextExportBinding, report_binding: ContextExportBinding, *,
                            owner: str) -> dict:
        """Recheck a saved result for focus/navigation without repeating scoring."""
        self.verify_inputs()
        reference, correspondence = self._read_context_pair(reference_binding, correspondence_binding, owner=owner)
        report = self._read_context_export(report_binding, kind="context-evaluation", owner=owner)
        bindings = {"source_sha256": self.document.source_sha256,
                    "recovery_sha256": self.document.recovery_sha256,
                    "reference_sha256": reference_binding.sha256,
                    "correspondence_sha256": correspondence_binding.sha256}
        expected_inputs = {("pdf_sha256" if key == "source_sha256" else key): value
                           for key, value in bindings.items()}
        if (report_binding.reference_sha256 != reference_binding.sha256
                or report_binding.correspondence_sha256 != correspondence_binding.sha256
                or type(report) is not dict or report.get("kind") != "ocr_context_evaluation"
                or report.get("inputs") != expected_inputs
                or any(report.get(key) != bindings[key] for key in
                       ("source_sha256", "recovery_sha256", "reference_sha256"))):
            raise ValueError("context result does not bind these exact inputs")
        self.verify_inputs()
        for binding in (reference_binding, correspondence_binding, report_binding):
            self._read_context_export(binding, kind=binding.kind, owner=owner)
        return {"report": report, "reference": reference, "correspondence": correspondence,
                "bindings": bindings, "report_sha256": report_binding.sha256}

    def prepare_download(self, export, cache_root: Path, *, owner: str | None = None) -> Path:
        """Copy one server-created export into the launcher's private Gradio cache."""
        try:
            self.verify_inputs()
            binding = export if type(export) is ContextExportBinding else None
            if binding is not None:
                self._read_context_export(binding, kind=binding.kind, owner=owner)
                path = binding.path
            else:
                if not isinstance(export, Path) or owner is not None:
                    raise ValueError("download requires a server-created export")
                path = export.absolute()
                if re.fullmatch(r"ocr-(?:layout|references|regions|draft|docling|assignment|omission-diagnostics|"
                                r"omission-review)-[0-9a-f]{32}\.json", path.name) is None:
                    raise ValueError("invalid review export name")
            if path.parent != self.output_dir or path in (
                    self.pdf_path, self.recovery_path, self.draft_path, self.proposals_path, self.audit_path):
                raise ValueError("download does not belong to the review output")
            cache = Path(cache_root).absolute()
            if cache.parent != self.output_dir or not cache.name.startswith("ocr-review-cache-"):
                raise ValueError("download requires the launcher's private cache")
            storage_policy.assert_no_link_components(cache)
            cache_stat = cache.lstat()
            if not stat.S_ISDIR(cache_stat.st_mode):
                raise ValueError("review cache is not a directory")
            storage_policy.assert_no_link_components(path)
            before = path.lstat()
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                    or not 0 < before.st_size <= MAX_REPORT_BYTES):
                raise ValueError("review export is not a bounded nonempty regular file")

            def identity(value):
                return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_nlink

            # Current export schemas fit this bound; retain their stricter publication limits.
            raw, digest = _read_snapshot(path, label="OCR review export", max_bytes=MAX_REPORT_BYTES)
            if identity(path.lstat()) != identity(before) or binding is not None and digest != binding.sha256:
                raise RuntimeError("review export changed")

            def streamed_digest(target):
                return storage_policy._bounded_snapshot(
                    target, before.st_size, min_size=before.st_size, retain=False,
                    chunk_size=lambda: 1024 * 1024, identity=identity,
                    invalid="invalid download file", opened_changed="download file changed",
                    oversized="download file grew", changed="download file changed")[1]

            def recheck():
                self.verify_inputs()
                storage_policy.assert_no_link_components(cache)
                current = cache.lstat()
                if (current.st_dev, current.st_ino) != (cache_stat.st_dev, cache_stat.st_ino):
                    raise RuntimeError("review cache directory changed")
                if (identity(path.lstat()) != identity(before) or streamed_digest(path) != digest
                        or binding is not None and binding.generation != self._context_generation()):
                    raise RuntimeError("review export changed")

            destination = cache / path.name

            def publish(temporary, target):
                recheck()
                _publish_new_report(temporary, target)

            storage_policy.atomic_write_private(destination, lambda handle: handle.write(raw), text=False,
                                                replace_fn=publish)
            if streamed_digest(destination) != digest:
                raise RuntimeError("download copy differs from the retained export")
            recheck()
            # Gradio admits this existing cache path and registers it without copying again.
            return destination
        except Exception:
            raise ReviewDownloadError("The artifact was saved, but its browser download could not be verified. "
                                      "Check the private output folder before exporting again.") from None

    def save(self, kind: str, payload: object) -> Path:
        """Revalidate generated schemas and exact inputs before a no-clobber export."""
        if kind == "layout":
            validated = validate_layout_plan(
                payload, source_sha256=self.document.source_sha256,
                recovery_sha256=self.document.recovery_sha256, page_count=self.document.page_count)
            self.document.layout_plan(validated["pages"])
        elif kind == "references":
            validated = validate_references(payload, page_count=self.document.page_count)
            if validated["source_sha256"] != self.document.source_sha256:
                raise ValueError("reference source does not match review")
        elif kind == "regions":
            if not isinstance(payload, dict):
                raise ValueError("invalid region plan")
            validated = self.region_plan(payload.get("regions"))
            if validated != payload:
                raise ValueError("region plan binding differs from review")
        elif kind == "draft":
            from ocr_review_drafts import validate_draft

            validated = validate_draft(payload, self.document, proposals=self.proposals,
                                        proposals_sha256=self.proposals_sha256)
            if validated["parent_draft_sha256"] != self.draft_sha256:
                raise ValueError("draft parent does not match the loaded snapshot")
            _check_draft_size(validated)
        elif kind == "audit":
            from ocr_spot_audit import validate_audit

            validated = validate_audit(payload, self.document, parent_audit_sha256=self.audit_sha256)
        elif kind == "docling":
            from ocr_docling import validate_docling_review

            validated = validate_docling_review(payload, proposals=self.proposals, recovery=self.document.recovery_snapshot(),
                                                recovery_sha256=self.document.recovery_sha256,
                                                proposals_sha256=self.proposals_sha256)
        elif kind == "assignment":
            from ocr_layout_assignment import validate_assignment_review

            validated = validate_assignment_review(payload, **self.assignment_bindings())
        elif kind in {"omission-diagnostics", "omission-review"}:
            from ocr_omission import validate_omission_diagnostics, validate_omission_review

            validator = validate_omission_diagnostics if kind == "omission-diagnostics" else validate_omission_review
            validated = validator(payload, **self.assignment_bindings())
        else:
            raise ValueError("unknown review export kind")
        self.verify_inputs()
        destination = self.output_dir / f"ocr-{kind}-{uuid4().hex}.json"
        with PathLease(destination, backend="ocr-editor", collection_name="review", operation="export",
                       timeout=0, resource_description="OCR editor export"):
            def publish(temporary, target):
                self.verify_inputs()
                if kind == "draft" and Path(temporary).stat().st_size > MAX_DRAFT_BYTES:
                    raise ReviewDraftSizeError("serialized review draft exceeds its restart byte limit")
                if kind == "audit" and Path(temporary).stat().st_size > MAX_AUDIT_BYTES:
                    raise ValueError("serialized spot audit exceeds its restart byte limit")
                _publish_new_report(temporary, target)

            storage_policy.atomic_write_private_json(destination, validated, indent=2, replace_fn=publish)
        return destination


def draw_overlay(image, candidate: dict | None, *, selections: dict | None = None, line_order: list[int] | None = None,
                 highlighted_line: int | None = None, region_outline: list[float] | None = None):
    """Draw numbered OCR quadrilaterals and visual selection envelopes on a copy."""
    from PIL import ImageDraw

    result = image.copy()
    draw = ImageDraw.Draw(result)
    width, height = result.size
    raster = candidate["raster"] if candidate is not None else {"width": width, "height": height}
    lines = candidate["lines"] if candidate is not None else []
    if highlighted_line is not None and (type(highlighted_line) is not int or not 0 <= highlighted_line < len(lines)):
        raise ValueError("invalid highlighted OCR line")
    if len(lines) > 2000:
        raise ValueError("too many OCR lines for an interactive review canvas")
    order = list(range(len(lines))) if line_order is None else line_order
    if (not isinstance(order, list) or any(type(n) is not int for n in order)
            or sorted(order) != list(range(len(lines)))):
        raise ValueError("review order must include every line exactly once")
    for rank, index in enumerate(order, 1):
        box = [(p[0] * width / raster["width"], p[1] * height / raster["height"]) for p in lines[index]["box"]]
        draw.line(box + box[:1], fill="#C52A48" if index == highlighted_line else "#087F8C",
                  width=4 if index == highlighted_line else 2)
        draw.text((box[0][0], max(0, box[0][1] - 11)), str(rank), fill="#8C2131", stroke_width=1, stroke_fill="white")
    colors = {"body": "#AA5200", "gutter": "#703BB5", "crop": "#C52A48"}
    if region_outline is not None:
        left, top, right, bottom = rectangle(region_outline)
        draw.rectangle((left * width, top * height, right * width, bottom * height), outline="#AA5200", width=3)
    for name, bounds in (selections or {}).items():
        if name not in colors:
            raise ValueError("unknown visual selection")
        left, top, right, bottom = rectangle(bounds)
        draw.rectangle((left * width, top * height, right * width, bottom * height), outline=colors[name], width=3)
    return result
