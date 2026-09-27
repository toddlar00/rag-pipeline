"""Fixed-host crop-pack review without granting historical runs OCR authority.

Raw archives stay behind this adapter. UI callers supply catalog capabilities,
not paths, and must release their session locks before calling any method. A
publication guard may acquire a session lock while the private store is locked.
This adapter verifies evidence, not human identity or browser display ordering.
"""

from __future__ import annotations

import json
from functools import wraps
import math
from pathlib import Path
import threading
import time

import ocr_crop_comparison as crops
import ocr_crop_review_pack as packs
from ocr_crop_review_pack_io import CropReviewPackStore
from ocr_crop_review_runtime import CropPreviewError, _binding as preview_binding, validate_preview_profile
from ocr_review_runtime import ReviewWorkspace


MAX_VIEW_BYTES = 4 * 1024 * 1024
MAX_CLOSE_SECONDS = 40.0


def _fail():
    raise ValueError("Crop review archive is unavailable or changed; retained data is unchanged.")


def _guard(callback):
    if not callable(callback) or callback() is False:
        _fail()


def _close_unreturned_image(image):
    """Dispose a rejected local image without replacing its primary failure."""
    if image is not None:
        try:
            image.close()
        except BaseException:
            pass


def _operation(function=None, *, returns_image=False, returns_preview=False):
    """Admit bounded work; open's image transfers only after shutdown bookkeeping."""
    if function is None:
        return lambda method: _operation(method, returns_image=returns_image, returns_preview=returns_preview)

    @wraps(function)
    def call(self, *args, **kwargs):
        with self._condition:
            if self._closed or self._active:
                _fail()
            self._active = True
        result = None
        try:
            try:
                result = function(self, *args, **kwargs)
            finally:
                with self._condition:
                    self._active = False
                    self._condition.notify_all()
        except BaseException:
            if (returns_image or returns_preview) and result is not None:
                _close_unreturned_image(result["preview" if returns_preview else "image"])
            raise
        return result
    return call


def _archive_side(evidence, side):
    archive, pin = evidence[side], evidence["manifest"]["pair"][side]
    report = archive["report"]
    operation = "regions" if report["kind"] == "ocr_region_review" else "hardscan"
    row = next(item for item in report["regions"] if item["region_id"] == pin["region_id"])
    return {**pin, "operation": operation, "status": row["status"],
        "text": None if row["candidate"] is None else row["candidate"]["text"],
        "geometry": row["geometry"], "recipe": None if operation == "regions" else row["recipe"],
        "configuration": report["retry_configuration"]}


def _comparison(evidence, reference, critical_tokens, confirmed):
    pins = evidence["manifest"]["pair"]
    reviewed = crops.build_crop_reference(evidence["baseline"]["report"],
        anchor_report_sha256=pins["baseline"]["report_sha256"],
        anchor_region_id=pins["baseline"]["region_id"], reference=reference,
        critical_tokens=critical_tokens, confirmed=confirmed)
    comparison = crops.compare_crop_candidates(evidence["baseline"]["report"], evidence["retry"]["report"], reviewed,
        baseline_report_sha256=pins["baseline"]["report_sha256"],
        retry_report_sha256=pins["retry"]["report_sha256"],
        baseline_region_id=pins["baseline"]["region_id"], retry_region_id=pins["retry"]["region_id"])
    return {"pack_sha256": evidence["pack_sha256"], "reference": reviewed, "comparison": comparison,
            "requires_attention": True, "canonical_extraction_modified": False}


def _parent_journal(evidence, journal, *, require_reset):
    """Bound transient authoring/scoring to already strict saved v2 history.

    This is a bounded prefix/declaration check, not another history or scorer
    replay. An author may submit the dirty parent head to append its reset;
    completed authoring and comparisons must actually contain that reset.
    """
    if evidence["manifest"]["schema_version"] != 2:
        return
    prior = evidence["review"]["draft"]["uncertainty"]
    old = prior["journal"]
    if old is None:
        return
    from ocr_crop_uncertainty_journal import _journal_shape, _bytes, _hex

    _journal_shape(journal)
    _bytes(journal)  # Bound the entire supplied cohort before membership/copies.
    for key in ("binding", "base", "base_declaration_sha256"):
        if _bytes(old[key]) != _bytes(journal[key]):
            _fail()
    prefix = len(old["revisions"])
    if len(journal["revisions"]) < prefix or _bytes(journal["revisions"][:prefix]) != _bytes(old["revisions"]):
        _fail()
    views = {}
    for view in journal["views"]:
        if type(view) is not dict:
            _fail()
        pin = _hex(view.get("view_sha256"))
        if pin in views:
            _fail()
        views[pin] = view
    for view in old["views"]:
        if view["view_sha256"] not in views or _bytes(view) != _bytes(views[view["view_sha256"]]):
            _fail()
    if require_reset and prior["dirty"] and not any(row["reset_anchors"] for row in journal["revisions"][prefix:]):
        _fail()


class CropReviewPackService:
    """An operator-fixed store plus a narrow original-crop preview capability.

    With no injected preview, owns an isolated preview controller (no OCR).
    An OCR-enabled host can share its controller using the two explicit preview
    arguments; ownership/close then stays with that host. No live coordinator is
    retained here, and no archived run can become an execution capability.
    """

    def __init__(self, workspace, fixed_root, *, render_preview=None, preview_private_root=None,
                 render_preview_with_view=None):
        if type(workspace) is not ReviewWorkspace:
            _fail()
        if (render_preview is None) != (preview_private_root is None):
            _fail()
        if render_preview is not None and not callable(render_preview):
            _fail()
        if render_preview_with_view is not None and (render_preview is None or not callable(render_preview_with_view)):
            _fail()
        self._workspace = workspace
        self._binding = {"source_sha256": workspace.document.source_sha256,
            "recovery_sha256": workspace.document.recovery_sha256, "page_count": workspace.document.page_count}
        self._workspace_binding = preview_binding(workspace)
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._active = self._uncertain = False
        self._closed = False
        self._owned_preview = None
        self.private_root = Path(fixed_root).absolute()
        self._store = CropReviewPackStore(self.private_root, **self._binding, verify_inputs=self._verify)
        if render_preview is None:
            from ocr_crop_preview_supervision import CropPreviewController

            self._owned_preview = CropPreviewController(workspace)
            self._render = self._owned_preview.render
            self._render_with_view = self._owned_preview.render_with_view
            self.preview_private_root = self._owned_preview.private_root
        else:
            self._render = render_preview
            self._render_with_view = render_preview_with_view
            self.preview_private_root = Path(preview_private_root).absolute()

    def _verify(self):
        with self._lock:
            if self._closed:
                _fail()
        if self._workspace_binding != preview_binding(self._workspace):
            _fail()
        self._workspace.verify_inputs()
        if self._workspace_binding != preview_binding(self._workspace):
            _fail()
        with self._lock:
            if self._closed:
                _fail()

    @_operation
    def catalog(self):
        return self._store.catalog()

    def _read(self, pack_id, expected_pack_sha256=None):
        if expected_pack_sha256 is not None:
            crops._hex(expected_pack_sha256)
        evidence = self._store.read(pack_id)
        if expected_pack_sha256 is not None and evidence["pack_sha256"] != expected_pack_sha256:
            _fail()
        return evidence

    @_operation(returns_image=True)
    def open(self, pack_id, *, cancel_requested=None, preview_profile="fit"):
        """Fresh evidence/image at a fixed density, never restored review consent.

        The profile is transient display configuration, not historical pack or
        reference provenance. The saved physical scope and pack schema stay fixed.
        """
        return self._open(pack_id, cancel_requested=cancel_requested, preview_profile=preview_profile, with_view=False)

    @_operation(returns_preview=True)
    def open_with_view(self, pack_id, *, cancel_requested=None, preview_profile="fit"):
        """Explicit v2-capable historical view with a same-render image/metadata owner.

        Saved journals and declarations remain historical; none restores consent.
        Missing metadata capability refuses instead of invoking the old renderer.
        """
        return self._open(pack_id, cancel_requested=cancel_requested, preview_profile=preview_profile, with_view=True)

    def _open(self, pack_id, *, cancel_requested, preview_profile, with_view):
        preview_profile = validate_preview_profile(preview_profile)
        if cancel_requested is not None and not callable(cancel_requested):
            _fail()
        render = self._render_with_view if with_view else self._render
        if render is None:
            _fail()

        def cancelled():
            with self._lock:
                closed = self._closed
            return closed or (cancel_requested is not None and bool(cancel_requested()))

        if cancelled():
            raise CropPreviewError(code="preview_cancelled")
        evidence = self._read(pack_id)
        if not with_view and evidence["manifest"]["schema_version"] != 1:
            _fail()  # Old callers must not silently drop uncertainty fields.
        view = {"pack_id": pack_id, "pack_sha256": evidence["pack_sha256"],
            "scope": evidence["manifest"]["scope"],
            "baseline": _archive_side(evidence, "baseline"), "retry": _archive_side(evidence, "retry"),
            "draft": evidence["review"]["draft"], "historical_reviewed": evidence["review"]["reviewed"],
            "validation_scope": packs.VALIDATION_SCOPE,
            "requires_attention": True, "canonical_extraction_modified": False}
        # Only selected occurrences and bounded review text leave this adapter.
        view = json.loads(crops._bytes(view, MAX_VIEW_BYTES))
        del evidence
        image = render(view["scope"], cancel_requested=cancelled, preview_profile=preview_profile)
        try:
            if with_view:
                from ocr_crop_review_runtime import CropRasterPreview

                if type(image) is not CropRasterPreview:
                    raise CropPreviewError(code="preview_unavailable")
            self._read(pack_id, view["pack_sha256"])
            self._verify()
            if cancelled():
                raise CropPreviewError(code="preview_cancelled")
            return {**view, "preview" if with_view else "image": image}
        except BaseException:
            _close_unreturned_image(image)
            raise

    @_operation
    def recover_draft(self, pack_id):
        """V1-only text salvage; never route uncertainty into an unaware consumer."""
        result = self._store.recover_draft(pack_id)
        if set(result["draft"]) != {"reference", "critical_tokens_text"}:
            _fail()
        return result

    @_operation
    def recover_draft_v2(self, pack_id):
        """Explicit v2-capable declaration salvage, with complete history retained.

        A v1 draft stays v1; this does not synthesize genesis or restore consent.
        Only the store's manifest/review recovery path runs, never proof/scoring.
        """
        return self._store.recover_draft(pack_id)

    @_operation
    def compare(self, pack_id, *, expected_pack_sha256, reference, critical_tokens, confirmed):
        evidence = self._read(pack_id, expected_pack_sha256)
        if evidence["manifest"]["schema_version"] != 1:
            _fail()
        result = _comparison(evidence, reference, critical_tokens, confirmed)
        del evidence
        self._read(pack_id, expected_pack_sha256)
        self._verify()
        return result

    @_operation
    def save_revision(self, pack_id, *, expected_pack_sha256, reference, critical_tokens_text,
                      reviewed=None, verify_current):
        """Create a new revision from exact retained bytes; never replace a pack.

        ``reviewed`` is host-owned successful scoring, never a browser field or
        an implicitly reused archived declaration. The UI owns its one-use
        confirmation binding; policy independently recomputes supplied metrics.
        """
        crops._hex(expected_pack_sha256)
        _guard(verify_current)
        snapshot = self._store.read_for_revision(pack_id)
        evidence, raw = snapshot["evidence"], snapshot["pack"]["files"]
        if evidence["pack_sha256"] != expected_pack_sha256 or evidence["manifest"]["schema_version"] != 1:
            _fail()
        pins = evidence["manifest"]["pair"]
        sides = {side: {
            "artifacts": {name: raw[f"{side}/artifacts/{name}"] for name in packs.ARTIFACT_LIMITS},
            "retained_inputs": {name: raw[f"{side}/inputs/{name}"] for name in packs.INPUT_LIMITS
                                if f"{side}/inputs/{name}" in raw}}
            for side in ("baseline", "retry")}
        del snapshot, evidence, raw
        _guard(verify_current)
        pack = packs.build_crop_review_pack(**sides, **self._binding,
            baseline_region_id=pins["baseline"]["region_id"], retry_region_id=pins["retry"]["region_id"],
            reference=reference, critical_tokens_text=critical_tokens_text, reviewed=reviewed,
            parent_pack_sha256=expected_pack_sha256)
        _guard(verify_current)
        return self._store.publish(pack, verify_current=verify_current)

    @_operation
    def save_live(self, coordinator, baseline_run_id, baseline_item_id, retry_run_id, retry_item_id, *,
                  expected_pair_sha256, reference, critical_tokens_text, reviewed=None, verify_current):
        """Capture an explicitly selected live pair, without exposing raw bytes."""
        crops._hex(expected_pair_sha256)
        self._verify()
        _guard(verify_current)
        captured = coordinator.capture_crop_archives(baseline_run_id, baseline_item_id, retry_run_id, retry_item_id,
            expected_pair_sha256=expected_pair_sha256)
        pair = captured["pair"]
        if (pair["pair_sha256"] != expected_pair_sha256 or pair["source_sha256"] != self._binding["source_sha256"]
                or pair["baseline_recovery_sha256"] != self._binding["recovery_sha256"]):
            _fail()
        _guard(verify_current)
        pack = packs.build_crop_review_pack(baseline=captured["baseline"], retry=captured["retry"], **self._binding,
            baseline_region_id=pair["baseline"]["region_id"], retry_region_id=pair["retry"]["region_id"],
            reference=reference, critical_tokens_text=critical_tokens_text, reviewed=reviewed)
        del captured
        _guard(verify_current)
        return self._store.publish(pack, verify_current=verify_current)

    def _v2_evidence(self, pack_id, expected_pack_sha256, journal, *, require_reset=False):
        crops._hex(expected_pack_sha256)
        evidence = self._read(pack_id, expected_pack_sha256)
        if journal is not None:
            binding = journal.get("binding") if type(journal) is dict else None
            anchor = binding.get("anchor") if type(binding) is dict else None
            pin = evidence["manifest"]["pair"]["baseline"]
            if (type(anchor) is not dict or anchor.get("region_id") != pin["region_id"]
                    or anchor.get("report_sha256") != pin["report_sha256"]):
                _fail()
        _parent_journal(evidence, journal, require_reset=require_reset)
        return evidence

    @_operation
    def author_v2(self, pack_id, *, expected_pack_sha256, journal, reference, critical_tokens,
                  reset_anchors, annotation_changes, reason, views=()):
        """Explicit journal genesis/append against a selected retained occurrence.

        Trusted UI-retained views/actions are declarations, not image-inspection
        or approval proof. Authoring requests no new score, preview, publication
        or execution; strict evidence reads may replay saved historical metrics.
        """
        from ocr_crop_uncertainty_journal import build_crop_journal, append_crop_journal_declaration

        evidence = self._v2_evidence(pack_id, expected_pack_sha256, journal)
        before, report = evidence["manifest"]["pair"]["baseline"], evidence["baseline"]["report"]
        if journal is None:
            journal = build_crop_journal(report, anchor_report_sha256=before["report_sha256"],
                anchor_region_id=before["region_id"], reference=reference, critical_tokens=critical_tokens)
        replayed = append_crop_journal_declaration(journal, anchor_report=report,
            anchor_report_sha256=before["report_sha256"], reference=reference, critical_tokens=critical_tokens,
            reset_anchors=reset_anchors, annotation_changes=annotation_changes, reason=reason, views=views)
        _parent_journal(evidence, replayed["journal"], require_reset=True)
        result = json.loads(crops._bytes({"pack_sha256": expected_pack_sha256, "journal": replayed["journal"],
            "declaration": replayed["declaration"], "declaration_sha256": replayed["declaration_sha256"],
            "requires_attention": True, "canonical_extraction_modified": False}, MAX_VIEW_BYTES))
        del evidence
        self._read(pack_id, expected_pack_sha256)
        self._verify()
        return result

    @_operation
    def compare_v2(self, pack_id, *, expected_pack_sha256, journal, confirmed):
        """Fresh explicit comparison; unresolved visual uncertainty stays unscorable."""
        from ocr_crop_uncertainty_comparison import build_crop_reference_v2, compare_crop_candidates_v2

        if confirmed is not True:
            _fail()
        evidence = self._v2_evidence(pack_id, expected_pack_sha256, journal, require_reset=True)
        pins = evidence["manifest"]["pair"]
        reviewed = build_crop_reference_v2(evidence["baseline"]["report"],
            anchor_report_sha256=pins["baseline"]["report_sha256"], journal=journal, confirmed=True)
        _parent_journal(evidence, reviewed["journal"], require_reset=True)
        self._read(pack_id, expected_pack_sha256)
        self._verify()
        comparison = compare_crop_candidates_v2(evidence["baseline"]["report"], evidence["retry"]["report"], reviewed,
            baseline_report_sha256=pins["baseline"]["report_sha256"],
            retry_report_sha256=pins["retry"]["report_sha256"],
            baseline_region_id=pins["baseline"]["region_id"], retry_region_id=pins["retry"]["region_id"])
        result = json.loads(crops._bytes({"pack_sha256": expected_pack_sha256, "reference": reviewed,
            "comparison": comparison, "requires_attention": True,
            "canonical_extraction_modified": False}, MAX_VIEW_BYTES))
        del evidence
        self._read(pack_id, expected_pack_sha256)
        self._verify()
        return result

    @_operation
    def save_revision_v2(self, pack_id, *, expected_pack_sha256, reference, critical_tokens_text,
                         uncertainty, reviewed=None, verify_current):
        """Publish an immutable v2 child with actual retained parent continuity.

        The whole strict parent snapshot is host-owned, not browser evidence.
        Its manifest/review are checked again at every create-only commit; raw
        historical archives are copied unchanged, never reserialized.
        """
        from ocr_crop_uncertainty_pack import build_crop_review_pack_v2, validate_crop_pack_revision_v2

        crops._hex(expected_pack_sha256)
        _guard(verify_current)
        snapshot = self._store.read_for_revision(pack_id)
        evidence, raw = snapshot["evidence"], snapshot["pack"]["files"]
        if evidence["pack_sha256"] != expected_pack_sha256:
            _fail()
        pins = evidence["manifest"]["pair"]
        sides = {side: {
            "artifacts": {name: raw[f"{side}/artifacts/{name}"] for name in packs.ARTIFACT_LIMITS},
            "retained_inputs": {name: raw[f"{side}/inputs/{name}"] for name in packs.INPUT_LIMITS
                                if f"{side}/inputs/{name}" in raw}}
            for side in ("baseline", "retry")}
        _guard(verify_current)
        pack = build_crop_review_pack_v2(**sides, **self._binding,
            baseline_region_id=pins["baseline"]["region_id"], retry_region_id=pins["retry"]["region_id"],
            reference=reference, critical_tokens_text=critical_tokens_text, uncertainty=uncertainty,
            reviewed=reviewed, parent_pack_sha256=expected_pack_sha256)
        validate_crop_pack_revision_v2(snapshot, pack)
        del snapshot, evidence, raw, sides

        def current():
            _guard(verify_current)
            self._store.check_revision_parent(pack_id, expected_pack_sha256=expected_pack_sha256)
            _guard(verify_current)

        current()
        return self._store.publish(pack, verify_current=current)

    @_operation
    def save_live_v2(self, coordinator, baseline_run_id, baseline_item_id, retry_run_id, retry_item_id, *,
                     expected_pair_sha256, reference, critical_tokens_text, uncertainty,
                     reviewed=None, verify_current):
        """Save an explicitly selected live pair and bounded authored v2 state."""
        from ocr_crop_uncertainty_pack import build_crop_review_pack_v2

        crops._hex(expected_pair_sha256)
        self._verify()
        _guard(verify_current)
        captured = coordinator.capture_crop_archives(baseline_run_id, baseline_item_id, retry_run_id, retry_item_id,
            expected_pair_sha256=expected_pair_sha256)
        pair = captured["pair"]
        if (pair["pair_sha256"] != expected_pair_sha256 or pair["source_sha256"] != self._binding["source_sha256"]
                or pair["baseline_recovery_sha256"] != self._binding["recovery_sha256"]):
            _fail()
        _guard(verify_current)
        pack = build_crop_review_pack_v2(baseline=captured["baseline"], retry=captured["retry"], **self._binding,
            baseline_region_id=pair["baseline"]["region_id"], retry_region_id=pair["retry"]["region_id"],
            reference=reference, critical_tokens_text=critical_tokens_text, uncertainty=uncertainty, reviewed=reviewed)
        del captured
        _guard(verify_current)
        return self._store.publish(pack, verify_current=verify_current)

    def request_close(self):
        """Revoke admission and signal owned preview cancellation without waiting."""
        with self._lock:
            self._closed = True
        try:
            if self._owned_preview is not None:
                self._owned_preview.request_close()
        except BaseException:
            with self._lock:
                self._uncertain = True
            raise

    def close(self, timeout_seconds=MAX_CLOSE_SECONDS):
        """Revoke and drain active work, then close only the owned controller.

        A deadline or cleanup failure latches uncertainty. A later drain is not
        retroactive proof of the original shutdown; retained packs are untouched.
        """
        if (type(timeout_seconds) not in (int, float) or not 0 <= timeout_seconds <= MAX_CLOSE_SECONDS
                or not math.isfinite(timeout_seconds)):
            _fail()
        deadline = time.monotonic() + timeout_seconds
        try:
            self.request_close()
            with self._condition:
                drained = self._condition.wait_for(lambda: not self._active,
                                                   timeout=max(0., deadline - time.monotonic()))
            preview_closed = self._owned_preview is None or self._owned_preview.close(
                timeout_seconds=max(0., deadline - time.monotonic())) is True
        except BaseException:
            with self._lock:
                self._uncertain = True
            raise
        with self._lock:
            self._uncertain = self._uncertain or not drained or not preview_closed or self._store.cleanup_uncertain
            return not self._uncertain
