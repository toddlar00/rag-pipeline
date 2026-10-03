#!/usr/bin/env python3
"""Launch the fixed-input, authenticated loopback OCR review editor."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
# The crop archive service's close bound, kept local so the default launch
# never imports that service.
_ARCHIVE_CLOSE_SECONDS = 40.0


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError("invalid OCR review arguments")


def main(argv=None) -> int:
    try:
        parser = _Parser(prog="review_ocr.py", allow_abbrev=False, description=__doc__)
        parser.add_argument("--pdf", type=Path, required=True)
        parser.add_argument("--recovery", type=Path, required=True)
        parser.add_argument("--output-dir", type=Path, required=True, help="Existing private review output directory")
        parser.add_argument("--draft", type=Path, help="Fixed source-bound review snapshot to resume; never an approval receipt")
        parser.add_argument("--enable-spot-audit", action="store_true",
                            help="Enable source-first random sampling of retained high-confidence OCR; runs no models")
        parser.add_argument("--audit", type=Path,
                            help="Fixed private spot-audit snapshot to resume; enables the lane, restores no approval")
        parser.add_argument("--proposals", type=Path, help="Fixed source-bound Docling layout proposals to review")
        parser.add_argument("--scan-bundle", type=Path, help="Fixed completed source-pixel observation bundle; hypotheses are not approvals")
        parser.add_argument("--crop-review-pack-dir", type=Path,
                            help="Existing private crop-pack directory; enables explicit Save/Open retention, not OCR execution")
        parser.add_argument("--enable-ocr-execution", action="store_true",
                            help="Enable explicit per-run approval of contained local OCR; default review runs no models")
        parser.add_argument("--ocr-timeout-seconds", type=int,
                            help="Worker deadline, 1-3600 seconds (default 600); requires --enable-ocr-execution")
        parser.add_argument("--installation-evidence", type=Path,
                            help="Fixed private installation evidence for enabled OCR execution")
        parser.add_argument("--port", type=int, default=7861)
        parser.add_argument("--trusted-local-session", action="store_true", required=True,
                            help="Acknowledge trusted single-user local operation and approved annotation inputs")
        args = parser.parse_args(argv)
        token = os.environ.get("RAG_OCR_REVIEW_TOKEN", "")
        if not 1 <= args.port <= 65535 or not 32 <= len(token) <= 256 or any(ord(c) < 33 or ord(c) > 126 for c in token):
            raise ValueError("invalid review launch configuration")
        if (not args.enable_ocr_execution
                and (args.ocr_timeout_seconds is not None or args.installation_evidence is not None)):
            raise ValueError("execution options require explicit enablement")
        if args.ocr_timeout_seconds is not None and not 1 <= args.ocr_timeout_seconds <= 3600:
            raise ValueError("invalid review OCR deadline")
        # Gradio treats allowed_paths=[] as unset and then serves every directory
        # in a non-empty GRADIO_ALLOWED_PATHS; refuse before any workspace exists.
        if os.environ.get("GRADIO_ALLOWED_PATHS", ""):
            raise ValueError("invalid review launch configuration")
        from ocr_review_runtime import ReviewWorkspace
        import storage_policy

        options = {"draft_path": args.draft, "proposals_path": args.proposals}
        if args.scan_bundle is not None:
            options["scan_bundle_path"] = args.scan_bundle
        if args.audit is not None:
            options["audit_path"] = args.audit
        workspace = ReviewWorkspace(args.pdf, args.recovery, args.output_dir, **options)
        # Gradio may persist displayed scans. Give this process a private cache
        # and remove its owned temporary leaf on normal shutdown.
        with tempfile.TemporaryDirectory(prefix="ocr-review-cache-", dir=workspace.output_dir) as cache:
            storage_policy.ensure_private_directory(Path(cache), harden_existing=True)
            os.environ["GRADIO_TEMP_DIR"] = cache
            app, coordinator, pack_service = None, None, None
            try:
                if args.enable_ocr_execution:
                    from ocr_review_execution import ReviewRunCoordinator

                    coordinator = ReviewRunCoordinator(workspace, installation_path=args.installation_evidence,
                        timeout_seconds=600 if args.ocr_timeout_seconds is None else args.ocr_timeout_seconds)
                if args.crop_review_pack_dir is not None:
                    from ocr_review_crop_packs import CropReviewPackService

                    preview_options = {} if coordinator is None else {
                        "render_preview": coordinator.render_crop_preview,
                        "render_preview_with_view": coordinator.render_crop_preview_with_view,
                        "preview_private_root": coordinator.preview_private_root}
                    pack_service = CropReviewPackService(workspace, args.crop_review_pack_dir, **preview_options)
                from ocr_review_ui import build_app

                ui_options = {} if coordinator is None else {"execution_coordinator": coordinator}
                if pack_service is not None:
                    ui_options["pack_service"] = pack_service
                if coordinator is not None or pack_service is not None:
                    ui_options["uncertainty_review"] = True
                if args.enable_spot_audit or args.audit is not None:
                    ui_options["spot_audit"] = True
                app = build_app(workspace, **ui_options)
                preview_style = {}
                if coordinator is not None or pack_service is not None:
                    from ocr_review_crop_preview_ui import PREVIEW_CSS, PREVIEW_JS

                    preview_style = {"css": PREVIEW_CSS, "js": PREVIEW_JS}
                app.launch(server_name="127.0.0.1", server_port=args.port, share=False,
                           auth=("review", token), show_error=False, enable_monitoring=False,
                           strict_cors=True, mcp_server=False, max_file_size=1,
                           allowed_paths=[], blocked_paths=[str(path) for path in
                               (workspace.pdf_path, workspace.recovery_path, workspace.draft_path,
                                workspace.proposals_path, getattr(workspace, "scan_bundle_path", None),
                                getattr(workspace, "audit_path", None),
                                getattr(coordinator, "preview_private_root", None),
                                getattr(pack_service, "private_root", None),
                                getattr(pack_service, "preview_private_root", None),
                                args.installation_evidence) if path is not None],
                           footer_links=[], quiet=True, inbrowser=False,
                           # Explicit values outrank GRADIO_* fallbacks: no run history or
                           # bucket upload, no Node SSR, and no full-URL API root.
                           run_history=False, ssr_mode=False, root_path="",
                           # Keep FastAPI's native OpenTelemetry and its
                           # OTEL_*-driven OTLP export off in Gradio's app.
                           app_kwargs={"telemetry": {
                               "auto_configure": False, "tracing": False, "metrics": False,
                               "logs": False, "operation_spans": False}},
                           **preview_style)
            finally:
                # Revoke approval/new starts and supervise worker shutdown before
                # closing the UI. Attempt both cleanups even if either fails.
                # Float subtraction at a clock precision boundary can exceed the budget.
                archive_deadline = time.monotonic() + _ARCHIVE_CLOSE_SECONDS
                try:
                    try:
                        if pack_service is not None:
                            pack_service.request_close()
                    finally:
                        try:
                            if coordinator is not None:
                                shutdown = coordinator.close()
                                if (type(shutdown) is not dict or shutdown.get("closed") is not True
                                        or shutdown.get("cleanup_confirmed") is not True):
                                    raise RuntimeError("review worker cleanup is unconfirmed")
                        finally:
                            if pack_service is not None and pack_service.close(timeout_seconds=min(
                                    _ARCHIVE_CLOSE_SECONDS, max(0., archive_deadline - time.monotonic()))) is not True:
                                raise RuntimeError("crop archive cleanup is unconfirmed")
                finally:
                    if app is not None:
                        app.close()
        return 0
    except KeyboardInterrupt:
        print("OCR review cancelled; previously exported artifacts are retained. If shutdown cleanup was interrupted, "
              "a private scan cache may remain in the configured output directory and private preview-worker "
              "source copies may remain in the host temporary directory.", file=sys.stderr)
        return 130
    except Exception:
        print("OCR review unavailable. Check matching inputs, output directory, optional dependencies, port, and "
              "RAG_OCR_REVIEW_TOKEN (32-256 printable characters). GRADIO_ALLOWED_PATHS must be unset. "
              "No canonical text was changed. "
              "OCR options require --enable-ocr-execution and a 1-3600 second deadline. "
              "Crop Save/Open requires an existing private --crop-review-pack-dir. "
              "If startup or shutdown cleanup failed, a private scan cache may remain in the configured output directory "
              "and private preview-worker source copies may remain in the host temporary directory.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
