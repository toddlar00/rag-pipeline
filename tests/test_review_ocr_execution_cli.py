"""Opt-in review launch/lifecycle wiring; no server, PDF parser or OCR starts."""

import socket
import sys
from types import SimpleNamespace

import pytest

from tools import review_ocr


@pytest.fixture
def launch_case(tmp_path, monkeypatch):
    events = []
    failures = {}
    constructed = []
    builds = []
    launches = []
    workspace = SimpleNamespace(
        pdf_path=tmp_path / "source.pdf", recovery_path=tmp_path / "recovery.json",
        output_dir=tmp_path, draft_path=None, proposals_path=None, scan_bundle_path=None,
    )

    def make_workspace(*_args, **_kwargs):
        events.append("workspace")
        return workspace

    def fail(where):
        if where in failures:
            raise failures[where]

    class Coordinator:
        def __init__(self, bound_workspace, **options):
            events.append("coordinator")
            assert bound_workspace is workspace
            constructed.append((self, options))
            self.preview_private_root = workspace.output_dir.parent / "platform-temp" / "private-preview-staging"
            fail("coordinator")

        def close(self):
            events.append("coordinator-close")
            fail("coordinator-close")
            if "shutdown-result" in failures:
                return failures["shutdown-result"]
            return {"closed": True, "cleanup_confirmed": "uncertain" not in failures,
                    "notice": "cleanup_unconfirmed" if "uncertain" in failures else "closed"}

    def launch(**options):
        events.append("launch")
        launches.append(options)
        fail("launch")

    def close_app():
        events.append("app-close")
        fail("app-close")

    app = SimpleNamespace(launch=launch, close=close_app)

    def build(bound_workspace, **options):
        events.append("build")
        assert bound_workspace is workspace
        builds.append(options)
        fail("build")
        return app

    monkeypatch.setitem(sys.modules, "ocr_review_runtime", SimpleNamespace(ReviewWorkspace=make_workspace))
    monkeypatch.setitem(sys.modules, "ocr_review_execution", SimpleNamespace(ReviewRunCoordinator=Coordinator))
    monkeypatch.setitem(sys.modules, "ocr_review_ui", SimpleNamespace(build_app=build))
    monkeypatch.setenv("RAG_OCR_REVIEW_TOKEN", "r_" + "x" * 32)
    arguments = ["--pdf", str(workspace.pdf_path), "--recovery", str(workspace.recovery_path),
                 "--output-dir", str(tmp_path), "--trusted-local-session"]
    return SimpleNamespace(events=events, failures=failures, constructed=constructed, builds=builds,
                           launches=launches, args=arguments, workspace=workspace)


def test_default_review_never_imports_or_constructs_execution(launch_case, monkeypatch):
    monkeypatch.setitem(sys.modules, "ocr_review_execution", None)
    assert review_ocr.main(launch_case.args) == 0
    assert launch_case.builds == [{}]
    assert launch_case.events == ["workspace", "build", "launch", "app-close"]


def test_review_launch_turns_off_fastapi_native_telemetry(launch_case):
    # Gradio forwards app_kwargs to its FastAPI App; FastAPI 0.142 otherwise
    # instruments it and configures OTLP export from OTEL_* variables.
    assert review_ocr.main(launch_case.args) == 0
    assert launch_case.launches[0]["app_kwargs"] == {"telemetry": {
        "auto_configure": False, "tracing": False, "metrics": False,
        "logs": False, "operation_spans": False}}


def test_review_launch_pins_history_ssr_and_root_path(launch_case):
    assert review_ocr.main(launch_case.args) == 0
    launch = launch_case.launches[0]
    assert launch["run_history"] is launch["ssr_mode"] is launch["mcp_server"] is False
    assert launch["root_path"] == ""


def test_review_refuses_ambient_gradio_allowed_paths_before_workspace(launch_case, monkeypatch, capsys, tmp_path):
    # Gradio treats allowed_paths=[] as unset and would serve this directory.
    monkeypatch.setenv("GRADIO_ALLOWED_PATHS", str(tmp_path / "SYNTHETIC_PRIVATE_DIR"))
    assert review_ocr.main(launch_case.args) == 2
    assert launch_case.events == []
    err = capsys.readouterr().err
    assert "GRADIO_ALLOWED_PATHS must be unset" in err and "SYNTHETIC_PRIVATE_DIR" not in err


class _StopBeforeBind(BaseException):
    """Raised in place of Gradio's server start; main's handlers cannot mask it."""


def test_review_launched_app_ignores_gradio_history_ssr_and_root_environment(launch_case, monkeypatch, tmp_path):
    gradio = pytest.importorskip("gradio")
    from gradio import http_server

    def refuse_bind(**_kwargs):
        raise _StopBeforeBind()

    # Blocks.launch resolves every setting before it starts the server. A
    # future Gradio that bound without start_server would block instead.
    monkeypatch.setattr(http_server, "start_server", refuse_bind)
    monkeypatch.setattr(gradio.Blocks, "block_thread", lambda self: pytest.fail("Gradio bound without start_server"))
    monkeypatch.delenv("GRADIO_LOCAL_DEV_MODE", raising=False)
    monkeypatch.delenv("GRADIO_ALLOWED_PATHS", raising=False)
    monkeypatch.setenv("GRADIO_NODE_PATH", str(tmp_path / "missing-node"))
    monkeypatch.setenv("GRADIO_TEMP_DIR", str(tmp_path))  # main replaces it; restore it afterwards
    for name, value in {"GRADIO_RUN_HISTORY": "true", "GRADIO_HISTORY_BUCKET": "synthetic-owner/private-runs",
                        "GRADIO_MCP_SERVER": "true", "GRADIO_SSR_MODE": "true",
                        "GRADIO_ROOT_PATH": "https://sink.invalid/app"}.items():
        monkeypatch.setenv(name, value)
    with gradio.Blocks(analytics_enabled=False) as app:
        gradio.Textbox()
    monkeypatch.setitem(sys.modules, "ocr_review_ui", SimpleNamespace(build_app=lambda _workspace, **_options: app))
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    # main closes the unbound app in its finally block.
    with pytest.raises(_StopBeforeBind):
        review_ocr.main(launch_case.args + ["--port", str(port)])
    assert launch_case.events == ["workspace"]
    # The app requires auth, so check the resolved settings, not routes.
    assert app.run_history is False and app.ssr_mode is False
    assert app.root_path == "" and app.allowed_paths == []
    assert app.mcp_server is False and app.mcp_error is None


def test_explicit_execution_wires_fixed_options_and_shutdown_order(launch_case):
    evidence = launch_case.workspace.output_dir / "installation.json"
    assert review_ocr.main(launch_case.args + ["--enable-ocr-execution", "--ocr-timeout-seconds", "120",
                                              "--installation-evidence", str(evidence)]) == 0
    coordinator, options = launch_case.constructed[0]
    assert options == {"installation_path": evidence, "timeout_seconds": 120}
    assert launch_case.builds == [{"execution_coordinator": coordinator, "uncertainty_review": True}]
    assert launch_case.events[-2:] == ["coordinator-close", "app-close"]
    launch = launch_case.launches[0]
    assert launch["server_name"] == "127.0.0.1"
    assert launch["share"] is launch["mcp_server"] is launch["inbrowser"] is False
    assert launch["strict_cors"] is True and launch["allowed_paths"] == []
    assert str(evidence) in launch["blocked_paths"]
    assert str(coordinator.preview_private_root) in launch["blocked_paths"]
    assert not coordinator.preview_private_root.is_relative_to(launch_case.workspace.output_dir)


def test_execution_defaults_to_bounded_ten_minute_deadline(launch_case):
    assert review_ocr.main(launch_case.args + ["--enable-ocr-execution"]) == 0
    assert launch_case.constructed[0][1] == {"installation_path": None, "timeout_seconds": 600}


@pytest.mark.parametrize("seconds", [1, 3600])
def test_execution_deadline_endpoints_are_not_silently_changed(launch_case, seconds):
    assert review_ocr.main(launch_case.args + ["--enable-ocr-execution", "--ocr-timeout-seconds", str(seconds)]) == 0
    assert launch_case.constructed[0][1]["timeout_seconds"] == seconds


@pytest.mark.parametrize("extra", [
    ["--ocr-timeout-seconds", "600"], ["--installation-evidence", "SYNTHETIC_SECRET"],
    ["--enable-ocr-execution", "--ocr-timeout-seconds", "0"],
    ["--enable-ocr-execution", "--ocr-timeout-seconds", "3601"],
    ["--enable-ocr-execution", "--ocr-timeout-seconds", "1.5"],
])
def test_execution_option_errors_refuse_before_workspace(launch_case, capsys, extra):
    assert review_ocr.main(launch_case.args + extra) == 2
    assert launch_case.events == []
    assert "SYNTHETIC_SECRET" not in capsys.readouterr().err


@pytest.mark.parametrize("where", ["build", "launch", "coordinator-close", "app-close", "uncertain"])
def test_execution_cleanup_is_attempted_and_uncertainty_is_not_success(launch_case, capsys, where):
    launch_case.failures[where] = RuntimeError("SYNTHETIC_SECRET")
    assert review_ocr.main(launch_case.args + ["--enable-ocr-execution"]) == 2
    assert "coordinator-close" in launch_case.events
    if where != "build":
        assert launch_case.events[-2:] == ["coordinator-close", "app-close"]
    assert "SYNTHETIC_SECRET" not in capsys.readouterr().err


def test_interrupted_launch_closes_execution_before_ui(launch_case, capsys):
    launch_case.failures["launch"] = KeyboardInterrupt()
    assert review_ocr.main(launch_case.args + ["--enable-ocr-execution"]) == 130
    assert launch_case.events[-2:] == ["coordinator-close", "app-close"]
    assert "host temporary directory" in capsys.readouterr().err


@pytest.mark.parametrize("shutdown", [None, True, {}, {"closed": False, "cleanup_confirmed": True},
                                     {"closed": 1, "cleanup_confirmed": True},
                                     {"closed": True, "cleanup_confirmed": 1}])
def test_malformed_shutdown_cannot_report_clean_success(launch_case, shutdown):
    launch_case.failures["shutdown-result"] = shutdown
    assert review_ocr.main(launch_case.args + ["--enable-ocr-execution"]) == 2
    assert launch_case.events[-2:] == ["coordinator-close", "app-close"]


def test_cleanup_failure_after_launch_cancellation_still_closes_both(launch_case):
    launch_case.failures.update(launch=KeyboardInterrupt(), **{"coordinator-close": RuntimeError("uncertain")})
    assert review_ocr.main(launch_case.args + ["--enable-ocr-execution"]) == 2
    assert launch_case.events[-2:] == ["coordinator-close", "app-close"]


def test_failed_constructor_does_not_build_ui(launch_case):
    launch_case.failures["coordinator"] = RuntimeError("failed preflight")
    assert review_ocr.main(launch_case.args + ["--enable-ocr-execution"]) == 2
    assert launch_case.events == ["workspace", "coordinator"]


def test_execution_options_do_not_weaken_existing_token_check(launch_case, monkeypatch):
    monkeypatch.delenv("RAG_OCR_REVIEW_TOKEN")
    assert review_ocr.main(launch_case.args + ["--enable-ocr-execution"]) == 2
    assert launch_case.events == []
