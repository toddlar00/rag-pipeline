"""Server-owned, one-approval OCR diagnostic runs for an immutable review input.

Only the existing contained diagnostic worker executes OCR. Browser-facing
methods accept selections, not paths, launchers, environments or process IDs.
Process outcome and verified artifact availability are deliberately independent.
This local consent protocol is not authenticated proof of human review.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import asdict, dataclass, field
import hashlib
import hmac
import json
from pathlib import Path
import re
import subprocess
import threading
import time
from uuid import uuid4

import ocr_detection_disposition_io as disposition_io
from ocr_recovery import ReportCleanupError, RetryPolicy, _publish_new_report
from ocr_recovery_comparison import validate_recovery_report
import process_supervision
from resource_lease import PathLease
import storage_policy


MAX_RESULT_BYTES = 2 * 1024 * 1024
MAX_PLAN_BYTES = 1024 * 1024
CLOSE_TIMEOUT_SECONDS = 40.0
_ID = re.compile(r"[0-9a-f]{32}")
_SCRIPT = Path(__file__).resolve().parent / "tools" / "diagnose_ocr_dispositions.py"


class _PlanCleanupError(RuntimeError):
    """The plan publisher could not confirm private staging/lease cleanup."""


def _cleanup_failed(*_args):
    raise _PlanCleanupError("review plan cleanup is unconfirmed")


def _encoded(value, limit=MAX_PLAN_BYTES):
    return disposition_io._encoded(value, limit)


def _detached(value, limit=MAX_PLAN_BYTES):
    return json.loads(_encoded(value, limit))


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _identifier(value):
    if type(value) is not str or _ID.fullmatch(value) is None:
        raise ValueError("invalid review run identifier")
    return value


@dataclass(frozen=True)
class _Intent:
    identifier: str
    token: str
    digest: str
    operation: str
    selection_bytes: bytes
    policy: RetryPolicy
    plan_bytes: bytes | None
    plan_path: Path | None
    output: Path
    base_request: disposition_io.DispositionRequest
    expected_payload_bytes: bytes


@dataclass
class _Run:
    intent: _Intent
    cancel: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None
    phase: str = "starting"
    process_status: str = "not_started"
    artifact_state: str = "absent"
    actual_calls: int | None = 0
    summary: dict | None = None
    notice: str = "starting"
    handed_off: bool = False
    start_uncertain: bool = False
    request: disposition_io.DispositionRequest | None = None


class ReviewRunCoordinator:
    """One active run shared by all trusted local tabs of one review host.

    Preparation performs bounded read-only preflight synchronously. Start
    consumes its token and returns immediately; status/cancel never hash files
    or wait for OCR. Shutdown waits at most the fixed shared-cleanup allowance.
    Terminal history retains only small metadata and request identities, never
    complete reports. Eviction does not delete any private artifacts.
    """

    def __init__(self, workspace, *, installation_path=None, timeout_seconds=600, max_history=8):
        from ocr_crop_preview_supervision import CropPreviewController
        from ocr_review_runtime import ReviewWorkspace

        if not isinstance(workspace, ReviewWorkspace):
            raise ValueError("review execution requires a fixed review workspace")
        if (type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 3600
                or type(max_history) is not int or not 1 <= max_history <= 32):
            raise ValueError("review execution limits are invalid")
        self._workspace = workspace
        self._document = workspace.document
        self._pdf = workspace.pdf_path
        self._recovery = workspace.recovery_path
        self._output = workspace.output_dir
        self._source_sha256 = workspace.document.source_sha256
        self._recovery_sha256 = workspace.document.recovery_sha256
        self._page_count = workspace.document.page_count
        self._installation = None if installation_path is None else Path(installation_path).absolute()
        self._timeout, self._max_history = timeout_seconds, max_history
        self._directory = disposition_io._directory_identity(self._output)
        self._source_binding = disposition_io._bound_snapshot(self._pdf, 256 * 1024 * 1024)
        self._recovery_binding = disposition_io._bound_snapshot(self._recovery, 64 * 1024 * 1024)
        if (self._source_binding[0] != self._source_sha256
                or self._recovery_binding[0] != self._recovery_sha256):
            raise ValueError("review execution input binding differs")
        self._installation_binding = (None if self._installation is None else
                                      disposition_io._bound_snapshot(self._installation, 64 * 1024))
        self._verify_workspace()
        self._lock = threading.Lock()
        self._revision = 0
        self._pending = None
        self._active = None
        self._runs = OrderedDict()
        self._closed = False
        self._cleanup_uncertain = False
        self._preview = CropPreviewController(workspace)

    def _verify_workspace(self):
        workspace = self._workspace
        if (workspace.document is not self._document
                or (workspace.pdf_path, workspace.recovery_path, workspace.output_dir) !=
                (self._pdf, self._recovery, self._output)
                or (workspace.document.source_sha256, workspace.document.recovery_sha256,
                    workspace.document.page_count) != (self._source_sha256, self._recovery_sha256, self._page_count)):
            raise RuntimeError("review workspace generation changed")
        workspace.verify_inputs()
        if (disposition_io._directory_identity(self._output) != self._directory
                or disposition_io._bound_snapshot(self._pdf, 256 * 1024 * 1024) != self._source_binding
                or disposition_io._bound_snapshot(self._recovery, 64 * 1024 * 1024) != self._recovery_binding
                or (self._installation is not None and disposition_io._bound_snapshot(
                    self._installation, 64 * 1024) != self._installation_binding)):
            raise RuntimeError("review execution input generation changed")

    def _admit(self):
        if (self._closed or self._cleanup_uncertain or self._active is not None
                or self._preview.cleanup_uncertain):
            raise ValueError("review execution is closed, busy or cleanup is unconfirmed")

    @property
    def preview_private_root(self):
        """Fixed host-owned staging path; explicitly blocked by the launcher."""
        return self._preview.private_root

    def render_crop_preview(self, scope, *, cancel_requested=None, preview_profile="fit"):
        """Render only through the fixed preview worker; never fall back locally."""
        return self._render_crop_preview(scope, cancel_requested=cancel_requested,
                                         preview_profile=preview_profile, with_view=False)

    def render_crop_preview_with_view(self, scope, *, cancel_requested=None, preview_profile="fit"):
        """Explicit same-render image owner and metadata; never a v2 fallback."""
        return self._render_crop_preview(scope, cancel_requested=cancel_requested,
                                         preview_profile=preview_profile, with_view=True)

    def _render_crop_preview(self, scope, *, cancel_requested, preview_profile, with_view):
        from ocr_crop_review_runtime import CropPreviewError, validate_preview_profile

        preview_profile = validate_preview_profile(preview_profile)
        if cancel_requested is not None and not callable(cancel_requested):
            raise CropPreviewError(code="preview_unavailable")

        def cancelled():
            with self._lock:
                closed = self._closed or self._cleanup_uncertain
            return closed or (cancel_requested is not None and bool(cancel_requested()))

        with self._lock:
            if self._closed:
                raise CropPreviewError(code="preview_cancelled")
            if self._cleanup_uncertain or self._preview.cleanup_uncertain:
                raise CropPreviewError(code="cleanup_unconfirmed")
        image = None
        try:
            try:
                if cancelled():
                    raise CropPreviewError(code="preview_cancelled")
                render = self._preview.render_with_view if with_view else self._preview.render
                image = render(scope, cancel_requested=cancelled, preview_profile=preview_profile)
                if with_view:
                    from ocr_crop_review_runtime import CropRasterPreview

                    if type(image) is not CropRasterPreview:
                        raise CropPreviewError(code="preview_unavailable")
                if self._preview.cleanup_uncertain:
                    raise CropPreviewError(code="cleanup_unconfirmed")
                if cancelled():
                    raise CropPreviewError(code="preview_cancelled")
            except CropPreviewError as failure:
                raise CropPreviewError(code=failure.code) from None
            except Exception:
                raise CropPreviewError(code="preview_unavailable") from None
            finally:
                if self._preview.cleanup_uncertain:
                    with self._lock:
                        self._cleanup_uncertain = True
                        self._pending = None
        except BaseException:
            if image is not None:
                try:
                    image.close()
                except BaseException:
                    pass  # Preserve existing refusal/cancellation/finalizer precedence.
            raise
        # Ownership transfers only after the cleanup-latch finalizer succeeds.
        return image

    def invalidate_pending(self):
        """Revoke pending consent without changing or cancelling an active run."""
        with self._lock:
            self._revision += 1
            self._pending = None

    def _selection(self, operation, pages, regions, dpi, preprocessing):
        if type(operation) is not str or operation not in ("pages", "regions", "hardscan"):
            raise ValueError("invalid review execution operation")
        if operation == "pages":
            if (regions is not None or type(pages) is not list or not 1 <= len(pages) <= 20
                    or any(type(page) is not int or not 1 <= page <= self._page_count for page in pages)
                    or len(set(pages)) != len(pages)):
                raise ValueError("review execution requires an explicit bounded page selection")
            return {"pages": sorted(pages)}, RetryPolicy(dpi=dpi, max_pages=len(pages), preprocessing=preprocessing), None
        if (pages is not None or type(regions) is not list or not 1 <= len(regions) <= 20
                or type(dpi) is not int or dpi not in (300, 400) or preprocessing != "none"):
            raise ValueError("review crop execution requires an explicit supported plan")
        # Encode before copying to bound hostile nesting/strings in browser data.
        regions = _detached(regions)
        plan = {"schema_version": 1, "source_sha256": self._source_sha256,
                "recovery_sha256": self._recovery_sha256,
                "coordinate_system": "original_page_display_fraction", "regions": regions}
        if operation == "hardscan":
            from ocr_hardscan import validate_plan

            plan.update(kind="ocr_hardscan_plan", approval="operator_approved")
        else:
            from ocr_regions import validate_region_plan as validate_plan
        plan = validate_plan(plan, source_sha256=self._source_sha256,
                             recovery_sha256=self._recovery_sha256, page_count=self._page_count)
        return {"regions": plan["regions"]}, RetryPolicy(dpi=dpi), _encoded(plan)

    def prepare(self, *, operation="pages", pages=None, regions=None, dpi=300, preprocessing="none"):
        """Freeze an exact proposed request; no plan or output is published yet."""
        with self._lock:
            self._admit()
            self._revision += 1
            revision = self._revision
            self._pending = None
        try:
            selection, policy, plan_bytes = self._selection(operation, pages, regions, dpi, preprocessing)
            self._verify_workspace()
            identifier, token = uuid4().hex, uuid4().hex
            output = self._output / ("ocr-run-" + identifier)
            plan_path = None if plan_bytes is None else self._output / ("ocr-run-" + identifier + "-plan.json")
            if plan_path is not None:
                storage_policy.assert_no_link_components(plan_path)
                if plan_path.exists():
                    raise ValueError("review plan destination already exists")
            # A real admitted page request captures all fixed source/lock/model/
            # producer identities without publishing the not-yet-approved crop
            # plan. The eventual crop request must equal the derived payload.
            base = disposition_io.disposition_request(self._pdf, output, policy=policy,
                requested_pages=tuple(selection.get("pages", ())), installation_path=self._installation)
            expected = _detached(base.payload)
            configuration = {"operation": operation, "policy": asdict(policy) if operation == "pages" else {"dpi": dpi},
                             "requested_pages": selection.get("pages", [])}
            expected.update(operation=operation, configuration=configuration)
            expected["inputs"]["configuration_sha256"] = disposition_io._digest(configuration)
            if plan_bytes is not None:
                expected["inputs"].update(recovery_sha256=self._recovery_sha256, plan_sha256=_sha(plan_bytes))
            selection_bytes, expected_bytes = _encoded(selection), _encoded(expected)
            digest = disposition_io._digest({"request": expected, "selection": selection,
                "baseline_recovery_sha256": self._recovery_sha256, "run_id": identifier,
                "timeout_seconds": self._timeout})
            intent = _Intent(identifier, token, digest, operation, selection_bytes, policy, plan_bytes,
                             plan_path, output, base, expected_bytes)
            self._recheck_intent(intent)
            view = {"intent_id": identifier, "approval_token": token, "intent_sha256": digest,
                    "status": "awaiting_approval", "operation": operation, "selection": selection,
                    "configuration": configuration, "requires_attention": True}
            with self._lock:
                self._admit()
                if revision != self._revision:
                    raise ValueError("review execution preparation was revoked")
                self._pending = intent
            return _detached(view)
        except Exception:
            raise ValueError("review execution preparation failed or was revoked") from None

    def _recheck_intent(self, intent):
        expected_output = self._output / ("ocr-run-" + _identifier(intent.identifier))
        expected_plan = None if intent.plan_bytes is None else self._output / ("ocr-run-" + intent.identifier + "-plan.json")
        if (intent.output != expected_output or intent.plan_path != expected_plan
                or intent.base_request.pdf_path != self._pdf or intent.base_request.output_dir != expected_output
                or intent.base_request.installation_path != self._installation):
            raise RuntimeError("review execution server-owned destinations changed")
        self._verify_workspace()
        disposition_io.recheck_disposition_request(intent.base_request)
        expected = json.loads(intent.expected_payload_bytes)
        if (expected["producer_generation_sha256"] != disposition_io._digest(intent.base_request.generation)
                or (intent.plan_bytes is not None and expected["inputs"]["plan_sha256"] != _sha(intent.plan_bytes))
                or disposition_io._digest({"request": expected, "selection": json.loads(intent.selection_bytes),
                    "baseline_recovery_sha256": self._recovery_sha256, "run_id": intent.identifier,
                    "timeout_seconds": self._timeout}) != intent.digest):
            raise RuntimeError("review execution intent changed")

    @staticmethod
    def _view(run):
        return {"run_id": run.intent.identifier, "operation": run.intent.operation, "phase": run.phase,
                "process_status": run.process_status, "artifact_state": run.artifact_state,
                "cancellation_requested": run.cancel.is_set(), "actual_calls": run.actual_calls,
                "summary": None if run.summary is None else dict(run.summary), "notice": run.notice,
                "requires_attention": True}

    def start(self, intent_id, approval_token, *, confirmed):
        _identifier(intent_id)
        _identifier(approval_token)
        with self._lock:
            self._admit()
            intent = self._pending
            if (confirmed is not True or type(confirmed) is not bool or intent is None
                    or intent_id != intent.identifier or not hmac.compare_digest(approval_token, intent.token)):
                raise ValueError("review execution requires its current explicit approval")
            self._pending = None
            self._revision += 1
            run = _Run(intent)
            self._runs[intent_id] = run
            self._active = intent_id
            try:
                thread = threading.Thread(target=self._execute, args=(run,), name="ocr-review-run", daemon=True)
            except BaseException as exc:
                run.phase, run.process_status, run.notice = "terminal", "failed", "start_failed"
                self._active = None
                self._trim_history()
                if not isinstance(exc, Exception):
                    raise
                return self._view(run)
            run.thread = thread
            # Starting is short, but can launch then raise while waiting for
            # bootstrap. Never discard ownership based on ident/is_alive at
            # that instant. Conservatively latch uncertainty on any failure.
            try:
                thread.start()
            except BaseException as exc:
                run.cancel.set()
                run.start_uncertain = True
                self._cleanup_uncertain = True
                run.process_status, run.notice = "cleanup_unconfirmed", "cleanup_unconfirmed"
                if not isinstance(exc, Exception):
                    raise
            return self._view(run)

    def _find(self, run_id):
        _identifier(run_id)
        run = self._runs.get(run_id)
        if run is None:
            raise ValueError("review execution run is unknown or no longer retained")
        return run

    def status(self, run_id):
        with self._lock:
            return self._view(self._find(run_id))

    def cancel(self, run_id):
        with self._lock:
            run = self._find(run_id)
            if run.phase != "terminal":
                run.cancel.set()
                run.notice = "cancellation_requested"
            return self._view(run)

    @staticmethod
    def _cancelled(run):
        if run.cancel.is_set():
            raise KeyboardInterrupt

    def _publish_plan(self, run):
        intent = run.intent
        if intent.plan_path is None:
            return

        def commit(temporary, destination):
            self._cancelled(run)
            self._recheck_intent(intent)
            if disposition_io._snapshot(temporary, MAX_PLAN_BYTES)[1] != _sha(intent.plan_bytes):
                raise RuntimeError("review execution staged plan differs")
            _publish_new_report(temporary, destination)

        with PathLease(intent.plan_path, backend="ocr-review-run", collection_name="plan", operation="publish",
                       timeout=0, resource_description="OCR review run plan", cleanup_error_fn=_cleanup_failed):
            self._cancelled(run)
            self._recheck_intent(intent)
            storage_policy.atomic_write_private(intent.plan_path, lambda handle: handle.write(intent.plan_bytes),
                                                text=False, replace_fn=commit, cleanup_error_fn=_cleanup_failed)
        if disposition_io._snapshot(intent.plan_path, MAX_PLAN_BYTES)[1] != _sha(intent.plan_bytes):
            raise RuntimeError("review execution published plan differs")

    def _request(self, intent):
        return disposition_io.disposition_request(self._pdf, intent.output, operation=intent.operation,
            policy=intent.policy, requested_pages=tuple(json.loads(intent.selection_bytes).get("pages", ())),
            recovery_path=self._recovery if intent.plan_path is not None else None,
            plan_path=intent.plan_path, installation_path=self._installation)

    def _arguments(self, run):
        intent, policy = run.intent, run.intent.policy
        args = [f"--pdf={self._pdf}", f"--output-dir={intent.output}", f"--operation={intent.operation}",
                f"--dpi={policy.dpi}", f"--max-pages={policy.max_pages}", f"--min-chars={policy.min_chars}",
                f"--max-pixels={policy.max_pixels}", f"--max-side={policy.max_side}",
                f"--preprocessing={policy.preprocessing}"]
        args.extend(f"--page={page}" for page in json.loads(intent.selection_bytes).get("pages", ()))
        if intent.plan_path is not None:
            args.extend((f"--recovery={self._recovery}", f"--plan={intent.plan_path}"))
        if self._installation is not None:
            args.append(f"--installation={self._installation}")
        args.extend((f"--expected-request-sha256={run.request.sha256}", "--worker"))
        return args

    def _child_started(self, run, _process):
        # This hook precedes start_gate.release(). A cancellation already
        # observed here raises into the shared supervisor's verified cleanup.
        self._cancelled(run)
        with self._lock:
            self._cancelled(run)
            run.handed_off = True
            run.phase, run.process_status, run.actual_calls = "running", "running", None
            run.notice = "running"

    def _execute(self, run):
        process_status, notice, uncertain = "failed", "execution_failed", False
        try:
            self._cancelled(run)
            self._recheck_intent(run.intent)
            self._cancelled(run)
            self._publish_plan(run)
            self._cancelled(run)
            request = self._request(run.intent)
            if _encoded(request.payload) != run.intent.expected_payload_bytes:
                raise RuntimeError("review execution request differs from approved intent")
            self._recheck_intent(run.intent)
            disposition_io.recheck_disposition_request(request)
            with self._lock:
                run.request = request
            self._cancelled(run)
            code = process_supervision._run_cli_with_deadline(_SCRIPT, self._arguments(run),
                operation="ocr-disposition-diagnostics", timeout=float(self._timeout),
                config=process_supervision.SupervisionConfig("RAG_OCR_DISPOSITION_CHILD", "RAG_OCR_DISPOSITION_RUN", 5., .2, 30.),
                environment_overrides={"RAG_OCR_DISPOSITION_CHILD": "1", "HF_HUB_OFFLINE": "1",
                    "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1", "DO_NOT_TRACK": "1",
                    "PYTHONNOUSERSITE": "1"}, stdout_target=subprocess.DEVNULL, stderr_target=subprocess.DEVNULL,
                warn_fn=lambda _message: None, cancel_requested=run.cancel.is_set,
                on_child_started=lambda process: self._child_started(run, process))
            process_status = {3: "completed", 124: "timed_out", 130: "cancelled"}.get(code, "failed")
            notice = "review_required" if code == 3 else "worker_not_completed"
        except (process_supervision._SupervisorCleanupError, ReportCleanupError, _PlanCleanupError):
            process_status, notice, uncertain = "cleanup_unconfirmed", "cleanup_unconfirmed", True
        except KeyboardInterrupt:
            process_status, notice = "cancelled", "cancelled"
        except BaseException:
            # The background boundary never exposes paths, native exceptions or
            # payload text to UI state, including failures before dispatch.
            process_status, notice = "failed", "execution_failed"
        try:
            artifact, summary, calls = self._inspect_artifact(run)
        except BaseException:
            artifact, summary, calls = "present_unverified", None, None
        with self._lock:
            uncertain = uncertain or run.start_uncertain
            if uncertain:
                self._cleanup_uncertain = True
                process_status, notice = "cleanup_unconfirmed", "cleanup_unconfirmed"
            run.phase, run.process_status, run.artifact_state = "terminal", process_status, artifact
            run.summary = summary
            run.actual_calls = calls if calls is not None else (0 if not run.handed_off and not uncertain else None)
            if artifact == "verified_complete" and process_status != "completed":
                notice = "verified_artifact_after_abnormal_exit" if not uncertain else "cleanup_unconfirmed"
            elif process_status == "completed" and artifact != "verified_complete":
                notice = "completion_unverified"
            elif process_status == "completed" and run.cancel.is_set():
                notice = "completed_after_cancellation_request"
            run.notice = notice
            self._active = None
            self._trim_history()

    def _inspect_artifact(self, run):
        output = run.intent.output
        storage_policy.assert_no_link_components(output)
        if not output.exists():
            plan_exists = run.intent.plan_path is not None and run.intent.plan_path.exists()
            return ("incomplete" if plan_exists else "absent"), None, None
        disposition_io._directory_identity(output)
        storage_policy.assert_no_link_components(output / "manifest.json")
        if not (output / "manifest.json").exists():
            return "incomplete", None, None
        if run.request is None:
            return "present_unverified", None, None
        self._verify_workspace()
        bundle = self._read_bundle(run)
        self._verify_workspace()
        return "verified_complete", dict(bundle["disposition"]["summary"]), len(bundle["execution"]["calls"])

    def _read_bundle(self, run):
        bundle = disposition_io.read_disposition_completion(run.intent.output, request=run.request)
        if bundle["report"]["page_count"] != self._page_count:
            raise ValueError("review execution result differs from baseline page count")
        return bundle

    def _trim_history(self):
        terminal = [key for key, run in self._runs.items() if run.phase == "terminal"]
        for key in terminal[:-self._max_history]:
            del self._runs[key]

    def result(self, run_id, *, item_id=None):
        """Fresh strict readback, then one bounded item or metadata only.

        Reports are never cached here or substituted for workspace.document.
        Region results remain independent crops, not whole-page replacements.
        """
        if item_id is not None and (type(item_id) is not str or len(item_id) > 64):
            raise ValueError("invalid review execution item identifier")
        with self._lock:
            run = self._find(run_id)
            if run.phase != "terminal" or run.artifact_state != "verified_complete" or run.request is None:
                raise ValueError("review execution has no verified complete result")
        try:
            self._verify_workspace()
            bundle = self._read_bundle(run)
            diagnostics, report, manifest = bundle["disposition"], bundle["report"], bundle["manifest"]
            selected = None
            if item_id is not None:
                diagnostic = next((row for row in diagnostics["items"] if row["item_id"] == item_id), None)
                if diagnostic is None:
                    raise ValueError("unknown diagnostic item")
                rows = report["pages"] if run.intent.operation == "pages" else report["regions"]
                key = "page_number" if run.intent.operation == "pages" else "region_id"
                record = next((row for row in rows if row[key] == diagnostic[key]), None)
                baseline = validate_recovery_report(disposition_io._json(self._recovery, 64 * 1024 * 1024)[0])
                page = next((row for row in baseline["pages"] if row["page_number"] == diagnostic["page_number"]), None)
                selected = {"diagnostic": diagnostic, "report_record": record, "baseline_page": page}
            result = {"run_id": run_id, "operation": run.intent.operation, "request_sha256": run.request.sha256,
                "source_sha256": self._source_sha256, "baseline_recovery_sha256": self._recovery_sha256,
                "report_sha256": manifest["report_sha256"], "execution_sha256": manifest["execution_sha256"],
                "disposition_sha256": manifest["disposition_sha256"], "summary": diagnostics["summary"],
                "item_ids": [{key: row[key] for key in ("item_id", "page_number", "region_id")}
                             for row in diagnostics["items"]], "selected_item": selected, "requires_attention": True}
            result = _detached(result, MAX_RESULT_BYTES)
            self._verify_workspace()
            with self._lock:
                if self._runs.get(run_id) is not run:
                    raise ValueError("review execution result was evicted")
            return result
        except Exception:
            raise ValueError("review execution result is unavailable or exceeds its display bound") from None

    def _crop_pair_snapshot(self, baseline_run_id, baseline_item_id, retry_run_id, retry_item_id):
        from ocr_crop_comparison import crop_scope

        selections = ((baseline_run_id, baseline_item_id), (retry_run_id, retry_item_id))
        with self._lock:
            runs = [self._find(identifier) for identifier, _ in selections]
            if any(run.intent.operation not in ("regions", "hardscan") or run.phase != "terminal"
                   or run.artifact_state != "verified_complete" or run.request is None for run in runs):
                raise ValueError("crop comparison requires retained verified crop runs")
        self._verify_workspace()
        bundles, sides, scopes = [], [], []
        for run, (run_id, item_id) in zip(runs, selections):
            if type(item_id) is not str or not 1 <= len(item_id) <= 64:
                raise ValueError("invalid crop comparison item")
            bundle = self._read_bundle(run)
            diagnostic = next((row for row in bundle["disposition"]["items"] if row["item_id"] == item_id), None)
            if diagnostic is None:
                raise ValueError("crop comparison item is not in the selected run")
            region_id = diagnostic["region_id"]
            scope = crop_scope(bundle["report"], region_id=region_id)
            record = next(row for row in bundle["report"]["regions"] if row["region_id"] == region_id)
            if (record["page_number"] != diagnostic["page_number"]
                    or record["status"] != diagnostic["legacy_status"]):
                raise ValueError("crop comparison diagnostic differs from its report")
            manifest = bundle["manifest"]
            candidate = record["candidate"]
            sides.append({"run_id": run_id, "item_id": item_id, "region_id": region_id,
                "operation": run.intent.operation, "request_sha256": run.request.sha256,
                "report_sha256": manifest["report_sha256"], "execution_sha256": manifest["execution_sha256"],
                "disposition_sha256": manifest["disposition_sha256"], "status": record["status"],
                "text": None if candidate is None else candidate["text"],
                "geometry": record["geometry"], "recipe": record.get("recipe"),
                "configuration": bundle["report"]["retry_configuration"],
                "engine": None if candidate is None else candidate["engine"]})
            scopes.append(scope)
            bundles.append(bundle)
        if scopes[0] != scopes[1]:
            raise ValueError("crop comparison requires the same requested physical source scope")
        view = {"source_sha256": self._source_sha256, "baseline_recovery_sha256": self._recovery_sha256,
            "scope": scopes[0], "baseline": sides[0], "retry": sides[1], "requires_attention": True,
            "reference_status": "not_supplied", "canonical_extraction_modified": False}
        view["pair_sha256"] = _sha(_encoded(view, MAX_RESULT_BYTES))
        return _detached(view, MAX_RESULT_BYTES), runs, bundles

    def _recheck_crop_pair(self, runs, bundles):
        # Each side must still be the complete bundle read for this comparison.
        # This is bounded readback, not an OS-level atomic filesystem snapshot.
        for run, previous in zip(runs, bundles):
            current = self._read_bundle(run)
            if current["manifest"] != previous["manifest"]:
                raise ValueError("crop comparison artifact generation changed")
        self._verify_workspace()
        with self._lock:
            if any(self._runs.get(run.intent.identifier) is not run for run in runs):
                raise ValueError("crop comparison run was evicted")

    def crop_pair(self, baseline_run_id, baseline_item_id, retry_run_id, retry_item_id):
        """Read two exact crop occurrences for source review, without scoring.

        Browser callers choose only retained run/item IDs. Whole-page context
        never becomes a crop baseline. Matching requested physical rectangles
        may have different resolution, rounding or hard-scan recipes.
        """
        try:
            view, runs, bundles = self._crop_pair_snapshot(
                baseline_run_id, baseline_item_id, retry_run_id, retry_item_id)
            self._recheck_crop_pair(runs, bundles)
            return view
        except Exception:
            raise ValueError("review crop pair is unavailable, changed or exceeds its bound") from None

    def compare_crops(self, baseline_run_id, baseline_item_id, retry_run_id, retry_item_id, *,
                      expected_pair_sha256, reference, critical_tokens, confirmed):
        """Score an explicitly reviewed transcription against a captured pair.

        This is an operator declaration, not authenticated human-review proof.
        The UI must additionally bind its displayed crop/transcription revision.
        Both full retained bundles are revalidated before and after scoring;
        no paths, reports, publication or execution capabilities enter here.
        """
        from ocr_crop_comparison import build_crop_reference, compare_crop_candidates

        try:
            if (confirmed is not True or type(expected_pair_sha256) is not str
                    or re.fullmatch(r"[0-9a-f]{64}", expected_pair_sha256) is None):
                raise ValueError("crop comparison requires explicit captured-pair confirmation")
            view, runs, bundles = self._crop_pair_snapshot(
                baseline_run_id, baseline_item_id, retry_run_id, retry_item_id)
            if not hmac.compare_digest(view["pair_sha256"], expected_pair_sha256):
                raise ValueError("crop comparison view changed")
            before, after = view["baseline"], view["retry"]
            references = build_crop_reference(bundles[0]["report"],
                anchor_report_sha256=before["report_sha256"], anchor_region_id=before["region_id"],
                reference=reference, critical_tokens=critical_tokens, confirmed=True)
            comparison = compare_crop_candidates(bundles[0]["report"], bundles[1]["report"], references,
                baseline_report_sha256=before["report_sha256"], retry_report_sha256=after["report_sha256"],
                baseline_region_id=before["region_id"], retry_region_id=after["region_id"])
            result = _detached({"pair_sha256": view["pair_sha256"], "reference": references,
                "comparison": comparison, "requires_attention": True,
                "canonical_extraction_modified": False}, MAX_RESULT_BYTES)
            self._recheck_crop_pair(runs, bundles)
            return result
        except Exception:
            raise ValueError("review crop comparison is unavailable, changed or exceeds its bound") from None

    def _crop_v2_snapshot(self, ids, expected_pair_sha256, journal):
        """Admit the selected occurrence before any journal replay or scoring."""
        if (type(expected_pair_sha256) is not str or len(expected_pair_sha256) != 64
                or re.fullmatch(r"[0-9a-f]{64}", expected_pair_sha256) is None):
            raise ValueError("invalid captured pair identity")
        with self._lock:
            if self._closed:
                raise ValueError("review host is closed")
        view, runs, bundles = self._crop_pair_snapshot(*ids)
        if not hmac.compare_digest(view["pair_sha256"], expected_pair_sha256):
            raise ValueError("crop review pair changed")
        if journal is not None:
            binding = journal.get("binding") if type(journal) is dict else None
            anchor = binding.get("anchor") if type(binding) is dict else None
            if (type(anchor) is not dict or anchor.get("region_id") != view["baseline"]["region_id"]
                    or anchor.get("report_sha256") != view["baseline"]["report_sha256"]):
                raise ValueError("crop journal occurrence changed")
        return view, runs, bundles

    def _recheck_crop_v2(self, runs, bundles):
        self._recheck_crop_pair(runs, bundles)
        with self._lock:
            if self._closed:
                raise ValueError("review host is closed")

    def author_crops_v2(self, baseline_run_id, baseline_item_id, retry_run_id, retry_item_id, *,
                        expected_pair_sha256, journal, reference, critical_tokens, reset_anchors,
                        annotation_changes, reason, views=()):
        """Append explicit source-bound authoring, without review consent or metrics."""
        from ocr_crop_uncertainty_journal import build_crop_journal, append_crop_journal_declaration

        try:
            view, runs, bundles = self._crop_v2_snapshot(
                (baseline_run_id, baseline_item_id, retry_run_id, retry_item_id), expected_pair_sha256, journal)
            before, report = view["baseline"], bundles[0]["report"]
            if journal is None:
                journal = build_crop_journal(report, anchor_report_sha256=before["report_sha256"],
                    anchor_region_id=before["region_id"], reference=reference, critical_tokens=critical_tokens)
            replayed = append_crop_journal_declaration(journal, anchor_report=report,
                anchor_report_sha256=before["report_sha256"], reference=reference, critical_tokens=critical_tokens,
                reset_anchors=reset_anchors, annotation_changes=annotation_changes, reason=reason, views=views)
            result = _detached({"pair_sha256": view["pair_sha256"], "journal": replayed["journal"],
                "declaration": replayed["declaration"], "declaration_sha256": replayed["declaration_sha256"],
                "requires_attention": True, "canonical_extraction_modified": False}, MAX_RESULT_BYTES)
            self._recheck_crop_v2(runs, bundles)
            return result
        except Exception:
            raise ValueError("review crop authoring is unavailable, changed or exceeds its bound") from None

    def compare_crops_v2(self, baseline_run_id, baseline_item_id, retry_run_id, retry_item_id, *,
                         expected_pair_sha256, journal, confirmed):
        """Review a journal against the exact fresh pair, preserving unscorable uncertainty."""
        from ocr_crop_uncertainty_comparison import build_crop_reference_v2, compare_crop_candidates_v2

        try:
            if confirmed is not True:
                raise ValueError("explicit review confirmation required")
            view, runs, bundles = self._crop_v2_snapshot(
                (baseline_run_id, baseline_item_id, retry_run_id, retry_item_id), expected_pair_sha256, journal)
            before, after = view["baseline"], view["retry"]
            reviewed = build_crop_reference_v2(bundles[0]["report"],
                anchor_report_sha256=before["report_sha256"], journal=journal, confirmed=True)
            self._recheck_crop_v2(runs, bundles)
            comparison = compare_crop_candidates_v2(bundles[0]["report"], bundles[1]["report"], reviewed,
                baseline_report_sha256=before["report_sha256"], retry_report_sha256=after["report_sha256"],
                baseline_region_id=before["region_id"], retry_region_id=after["region_id"])
            result = _detached({"pair_sha256": view["pair_sha256"], "reference": reviewed,
                "comparison": comparison, "requires_attention": True,
                "canonical_extraction_modified": False}, MAX_RESULT_BYTES)
            self._recheck_crop_v2(runs, bundles)
            return result
        except Exception:
            raise ValueError("review crop comparison is unavailable, changed or exceeds its bound") from None

    def capture_crop_archives(self, baseline_run_id, baseline_item_id, retry_run_id, retry_item_id, *,
                              expected_pair_sha256):
        """Host-only raw-byte capture for explicit private save, never UI output.

        Retains verification files, not the source PDF/model assets or live
        approval state. Both complete runs remain subject to current live
        readback at export time. Historical reopening uses a separate validator
        and must never repopulate this coordinator's run registry.
        """
        from ocr_crop_review_pack import (
            ARTIFACT_LIMITS, INPUT_LIMITS, MAX_MANIFEST_BYTES, MAX_PACK_BYTES, MAX_REVIEW_BYTES,
        )

        input_names = {"dependency_full_sha256": "requirements-full.lock",
            "dependency_test_sha256": "requirements-test.lock", "dependency_tools_sha256": "requirements-lock-tools.lock",
            "model_policy_sha256": "model-artifact-policy.json", "model_lock_sha256": "model-artifacts.lock.json",
            "recovery_sha256": "recovery.json", "plan_sha256": "plan.json", "installation_sha256": "installation.json"}
        try:
            if type(expected_pair_sha256) is not str or re.fullmatch(r"[0-9a-f]{64}", expected_pair_sha256) is None:
                raise ValueError("invalid captured pair identity")
            with self._lock:
                if self._closed:
                    raise ValueError("review host is closed")
            view, runs, bundles = self._crop_pair_snapshot(
                baseline_run_id, baseline_item_id, retry_run_id, retry_item_id)
            if not hmac.compare_digest(view["pair_sha256"], expected_pair_sha256):
                raise ValueError("crop review pair changed before capture")
            # Reserve the maximum authored-review/manifest space before retaining
            # either raw archive. Reject oversized pairs; never truncate proof.
            total = MAX_MANIFEST_BYTES + MAX_REVIEW_BYTES
            inventory = []
            for side, run, bundle in zip(("baseline", "retry"), runs, bundles):
                output = run.intent.output
                manifest = bundle["manifest"]
                records = []
                for name, limit in ARTIFACT_LIMITS.items():
                    path = output / name
                    identity = disposition_io._file_identity(path)
                    expected = (manifest["report_sha256"] if name == "work/report.json" else
                                None if name == "manifest.json" else manifest[name[:-5] + "_sha256"])
                    records.append(("artifacts", name, path, limit, identity, expected))
                seen = set()
                for path, key, _ in run.request.specs:
                    if key == "source_sha256":
                        continue  # PDF stays external and fixed to this host.
                    if key not in input_names or key in seen:
                        raise ValueError("unsupported archive input inventory")
                    seen.add(key)
                    name = input_names[key]
                    identity = disposition_io._file_identity(path)
                    if identity != run.request.input_identities[key]:
                        raise ValueError("retained input identity changed")
                    records.append(("retained_inputs", name, path, INPUT_LIMITS[name], identity,
                                    run.request.payload["inputs"][key]))
                if seen != set(input_names) - ({"installation_sha256"} if run.request.installation_path is None else set()):
                    raise ValueError("archive input inventory is incomplete")
                for _, _, _, limit, identity, _ in records:
                    size = identity[2]
                    if not 1 <= size <= limit:
                        raise ValueError("archive input exceeds its slot bound")
                    total += size
                    if total > MAX_PACK_BYTES:
                        raise ValueError("crop archive pair exceeds the private pack bound")
                inventory.append((side, records, manifest))
            result = {"pair": view}
            captured = []
            for side, records, manifest in inventory:
                archive = {"artifacts": {}, "retained_inputs": {}}
                for group, name, path, limit, identity, expected in records:
                    raw, digest = disposition_io._snapshot(path, limit, retain=True)
                    if (disposition_io._file_identity(path) != identity or len(raw) != identity[2]
                            or expected is not None and digest != expected):
                        raise ValueError("crop archive file changed during capture")
                    if group == "artifacts" and name == "manifest.json":
                        from evaluation_inputs import _strict_json_bytes

                        decoded = _strict_json_bytes(raw, label="crop archive", max_bytes=limit)
                        if _encoded(decoded, limit) != _encoded(manifest, limit):
                            raise ValueError("crop archive completion changed")
                    archive[group][name] = raw
                    captured.append((path, limit, digest, identity))
                if archive["artifacts"]["report.json"] != archive["artifacts"]["work/report.json"]:
                    raise ValueError("crop archive work/report bytes differ")
                result[side] = archive
            self._recheck_crop_pair(runs, bundles)
            for path, limit, digest, identity in captured:
                if disposition_io._bound_snapshot(path, limit) != (digest, identity):
                    raise ValueError("crop archive generation changed after capture")
            self._verify_workspace()
            with self._lock:
                if self._closed or any(self._runs.get(run.intent.identifier) is not run for run in runs):
                    raise ValueError("review host changed during archive capture")
            return result
        except Exception:
            raise ValueError("review crop archive is unavailable, changed or exceeds its bound") from None

    def close(self):
        """Cancel outside the UI queue; retain all complete/partial artifacts."""
        # Float subtraction at a clock precision boundary can exceed the budget.
        deadline = time.monotonic() + CLOSE_TIMEOUT_SECONDS
        with self._lock:
            self._closed = True
            self._revision += 1
            self._pending = None
            run = None if self._active is None else self._runs[self._active]
            if run is not None:
                run.cancel.set()
            threads = [item.thread for item in self._runs.values()
                       if item.thread is not None and (item is run or item.start_uncertain)]
        preview_request_failed = False
        try:
            self._preview.request_close()
        except Exception:
            preview_request_failed = True
        join_failed = False
        for thread in threads:
            if thread is threading.current_thread():
                join_failed = True
                continue
            try:
                thread.join(min(CLOSE_TIMEOUT_SECONDS, max(0., deadline - time.monotonic())))
            except RuntimeError:
                # A failed Thread.start may not yet have a visible bootstrap;
                # absence of an ident is not proof that handoff never occurred.
                join_failed = True
        try:
            preview_confirmed = self._preview.close(timeout_seconds=min(CLOSE_TIMEOUT_SECONDS, max(0., deadline - time.monotonic()))) is True
        except Exception:
            preview_confirmed = False
        with self._lock:
            confirmed = (not self._cleanup_uncertain and not join_failed and not preview_request_failed
                         and preview_confirmed and all(not thread.is_alive() for thread in threads))
            if not confirmed:
                self._cleanup_uncertain = True
            return {"closed": True, "cleanup_confirmed": confirmed,
                    "notice": "closed" if confirmed else "cleanup_unconfirmed"}
