"""Hard-deadline regressions for killable CLI operation workers."""

from pathlib import Path
import json
import math
import os
import signal
import subprocess
import sys
import textwrap
import threading
import time

import pytest

import eval as retrieval_eval
import rag
import supervised_worker


@pytest.mark.parametrize(
    "timeout",
    [True, False, None, 0, -1, float("inf"), float("nan"), "invalid"],
)
def test_operation_timeout_rejects_invalid_values(timeout):
    with pytest.raises(ValueError, match="finite positive"):
        rag._normalize_operation_timeout(timeout)


def test_operation_timeout_rejects_platform_overflow():
    assert rag._normalize_operation_timeout(threading.TIMEOUT_MAX) == (
        threading.TIMEOUT_MAX)
    with pytest.raises(ValueError, match="no greater"):
        rag._normalize_operation_timeout(
            math.nextafter(threading.TIMEOUT_MAX, math.inf))


def test_cli_operation_timeout_uses_last_valid_value_and_safe_fallback():
    assert rag._cli_operation_timeout(
        ["query", "x", "--operation-timeout", "9",
         "--operation-timeout=3"],
        "query",
    ) == 3
    assert rag._cli_operation_timeout(
        ["query", "x", "--operation-timeout", "invalid"],
        "query",
    ) == rag.DEFAULT_OPERATION_TIMEOUTS["query"]


def test_cli_operation_timeout_ignores_tokens_after_option_terminator():
    assert rag._cli_operation_timeout(
        ["query", "terms", "--", "--operation-timeout=1"],
        "query",
    ) == rag.DEFAULT_OPERATION_TIMEOUTS["query"]


def test_cli_operation_timeout_allows_root_option_terminator():
    assert rag._cli_operation_timeout(
        ["--", "query", "terms", "--operation-timeout", "1"],
        "query",
    ) == 1


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["query", "terms"], "query"),
        (["--quiet", "index"], "index"),
        (["-v", "--", "info"], "info"),
        ([], None),
    ],
)
def test_rag_cli_command_finds_subcommand(argv, expected):
    assert rag._rag_cli_command(argv) == expected


def test_supervised_process_propagates_exit_and_marks_child_environment(
        tmp_path):
    script = tmp_path / "exit_worker.py"
    marker = tmp_path / "environment.txt"
    script.write_text(
        "\n".join([
            "import os",
            "from pathlib import Path",
            "import sys",
            f"Path(sys.argv[1]).write_text(os.environ.get("
            f"'{rag._SUPERVISED_CHILD_ENV}', '') + '|' + "
            "os.environ.get('RAG_TEST_SECRET', '') + '|' + "
            f"os.environ.get('{rag._RUN_ID_ENV}', ''), encoding='utf-8')",
            "raise SystemExit(7)",
        ]),
        encoding="utf-8",
    )

    code = rag._run_cli_with_deadline(
        script, [str(marker)], operation="test", timeout=5,
        environment_overrides={"RAG_TEST_SECRET": "child-only"},
        run_id="correlated-run")

    assert code == 7
    assert marker.read_text(encoding="utf-8") == (
        "1|child-only|correlated-run")


def test_start_gate_precedes_target_and_preserves_script_semantics(tmp_path):
    script = tmp_path / "semantic_worker.py"
    marker = tmp_path / "semantics.json"
    script.write_text(
        textwrap.dedent(
            """
            import json
            import os
            from pathlib import Path
            import sys

            Path(sys.argv[1]).write_text(json.dumps({
                "argv": sys.argv,
                "orig_argv": sys.orig_argv,
                "path_zero": sys.path[0],
                "name": __name__,
                "file": __file__,
                "pid": os.getpid(),
            }), encoding="utf-8")
            """
        ),
        encoding="utf-8",
    )
    observed = {}

    def register(process):
        observed["pid"] = process.pid
        time.sleep(0.1)
        assert not marker.exists()

    code = rag._run_cli_with_deadline(
        script, [str(marker), "alpha", "--", "omega"],
        operation="test", timeout=5, on_child_started=register)

    payload = json.loads(marker.read_text(encoding="utf-8"))
    resolved_script = str(script.resolve())
    assert code == 0
    assert payload["pid"] == observed["pid"]
    assert payload["argv"] == [
        resolved_script, str(marker), "alpha", "--", "omega"]
    assert payload["orig_argv"] == [
        sys.executable, "-u", resolved_script,
        str(marker), "alpha", "--", "omega"]
    assert Path(payload["path_zero"]) == script.parent.resolve()
    assert payload["name"] == "__main__"
    assert Path(payload["file"]) == script.resolve()


def test_unreleased_start_gate_times_out_without_executing_target(tmp_path):
    script = tmp_path / "must_not_run.py"
    marker = tmp_path / "executed.txt"
    script.write_text(
        "from pathlib import Path\n"
        "import sys\n"
        "Path(sys.argv[1]).write_text('executed', encoding='utf-8')\n",
        encoding="utf-8",
    )
    gate = rag._new_supervised_start_gate()
    command = [
        sys.executable, "-u",
        str(Path(supervised_worker.__file__).resolve()),
        gate.kind, gate.child_value, "0.2", str(script), str(marker),
    ]
    try:
        process = subprocess.Popen(command, **gate.popen_options())
        assert process.wait(timeout=5) == supervised_worker.STARTUP_FAILURE_EXIT
    finally:
        gate.close()
    assert not marker.exists()


def test_supervised_timeout_kills_worker_without_logging_arguments(
        tmp_path, capsys):
    script = tmp_path / "hung_worker.py"
    script.write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
    started = time.monotonic()

    code = rag._run_cli_with_deadline(
        script, ["--cloud-key", "do-not-log-this-secret"],
        operation="query", timeout=0.2)

    assert code == 124
    assert time.monotonic() - started < 5
    error_output = capsys.readouterr().err
    assert "exceeded its 0.2s deadline" in error_output
    assert "do-not-log-this-secret" not in error_output


def test_supervisor_synthesizes_timeout_telemetry_after_worker_exit(
        tmp_path):
    script = tmp_path / "hung_worker.py"
    script.write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
    events_path = tmp_path / "run" / "events.jsonl"
    report_path = tmp_path / "run" / "report.json"

    code = rag._run_cli_with_deadline(
        script, [], operation="index", timeout=0.2,
        run_id="supervised-run", run_events=events_path,
        run_report=report_path)

    assert code == 124
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["run_id"] == "supervised-run"
    assert report["status"] == "failed"
    assert report["failure"]["category"] == "timeout"
    assert "deadline exceeded" not in report_path.read_text(encoding="utf-8")


def test_supervisor_classifies_keyboard_interrupt_as_cancellation(
        monkeypatch, tmp_path):
    report_path = tmp_path / "report.json"

    class FakeProcess:
        pid = 12345

        def wait(self, timeout):
            raise KeyboardInterrupt()

        def poll(self):
            return None

    class FakeJob:
        def assign(self, _process):
            pass

        def terminate(self):
            return True

        def close(self):
            return True

    monkeypatch.setattr(rag, "_WindowsKillJob", FakeJob)
    monkeypatch.setattr(
        rag.subprocess, "Popen", lambda *_args, **_kwargs: FakeProcess())
    monkeypatch.setattr(
        rag, "_terminate_supervised_process",
        lambda *_args, **_kwargs: True)

    code = rag._run_cli_with_deadline(
        tmp_path / "worker.py", [], operation="query", timeout=5,
        run_id="ctrl-c-run", run_report=report_path)

    assert code == 130
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "cancelled"
    assert report["failure"]["category"] == "cancelled"


def test_supervisor_does_not_rewrite_telemetry_before_cleanup_is_confirmed(
        monkeypatch, tmp_path):
    report_path = tmp_path / "report.json"

    class FakeProcess:
        pid = 12346

        def wait(self, timeout):
            raise subprocess.TimeoutExpired("worker", timeout)

        def poll(self):
            return None

    class FakeJob:
        def assign(self, _process):
            pass

        def terminate(self):
            return True

        def close(self):
            return True

    monkeypatch.setattr(rag, "_WindowsKillJob", FakeJob)
    monkeypatch.setattr(
        rag.subprocess, "Popen", lambda *_args, **_kwargs: FakeProcess())
    monkeypatch.setattr(
        rag, "_terminate_supervised_process",
        lambda *_args, **_kwargs: False)

    with pytest.raises(
            rag._SupervisorCleanupError,
            match="cleanup could not be confirmed"):
        rag._run_cli_with_deadline(
            tmp_path / "worker.py", [], operation="index", timeout=0.1,
            run_id="still-running", run_report=report_path)

    assert json.loads(report_path.read_text(encoding="utf-8"))["status"] == (
        "running")


def test_supervisor_propagates_unconfirmed_cleanup_after_cancellation(
        monkeypatch, tmp_path):
    class FakeProcess:
        pid = 12348

        def poll(self):
            return None

    class FakeJob:
        def assign(self, _process):
            pass

        def terminate(self):
            return True

        def close(self):
            return True

    monkeypatch.setattr(rag, "_WindowsKillJob", FakeJob)
    monkeypatch.setattr(
        rag.subprocess, "Popen", lambda *_args, **_kwargs: FakeProcess())
    monkeypatch.setattr(
        rag, "_terminate_supervised_process",
        lambda *_args, **_kwargs: False)

    with pytest.raises(
            rag._SupervisorCleanupError,
            match="cleanup could not be confirmed"):
        rag._run_cli_with_deadline(
            tmp_path / "worker.py", [], operation="index", timeout=5,
            cancel_requested=lambda: True)


def test_supervisor_honors_external_cancellation_after_child_registration(
        monkeypatch, tmp_path):
    report_path = tmp_path / "report.json"
    observed = {"registered": [], "waited": False}

    class FakeProcess:
        pid = 12347

        def wait(self, timeout):
            observed["waited"] = True
            return 0

        def poll(self):
            return None

    class FakeJob:
        def assign(self, _process):
            pass

        def terminate(self):
            return True

        def close(self):
            return True

    monkeypatch.setattr(rag, "_WindowsKillJob", FakeJob)
    monkeypatch.setattr(
        rag.subprocess, "Popen", lambda *_args, **_kwargs: FakeProcess())
    monkeypatch.setattr(
        rag, "_terminate_supervised_process",
        lambda *_args, **_kwargs: True)

    code = rag._run_cli_with_deadline(
        tmp_path / "worker.py", [], operation="index", timeout=5,
        run_id="cancel-request-run", run_report=report_path,
        cancel_requested=lambda: True,
        on_child_started=lambda process: observed["registered"].append(
            process.pid),
    )

    assert code == 130
    assert observed == {"registered": [12347], "waited": False}
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "cancelled"
    assert report["failure"]["category"] == "cancelled"


def test_supervisor_polls_heartbeat_and_forwards_output_targets(
        monkeypatch, tmp_path):
    observed = {"popen": None, "heartbeats": []}
    stdout_target = object()
    stderr_target = object()

    class FakeProcess:
        pid = 12348

        def __init__(self):
            self.wait_count = 0

        def wait(self, timeout):
            self.wait_count += 1
            if self.wait_count == 1:
                raise subprocess.TimeoutExpired("worker", timeout)
            return 0

        def poll(self):
            return 0 if self.wait_count >= 2 else None

    class FakeJob:
        def assign(self, _process):
            pass

        def terminate(self):
            return True

        def close(self):
            return True

    def fake_popen(_command, **options):
        observed["popen"] = options
        return FakeProcess()

    monkeypatch.setattr(rag, "_WindowsKillJob", FakeJob)
    monkeypatch.setattr(rag.subprocess, "Popen", fake_popen)

    assert rag._run_cli_with_deadline(
        tmp_path / "worker.py", [], operation="index", timeout=5,
        heartbeat=lambda process: observed["heartbeats"].append(process.pid),
        stdout_target=stdout_target, stderr_target=stderr_target,
    ) == 0

    assert observed["heartbeats"] == [12348]
    assert observed["popen"]["stdout"] is stdout_target
    assert observed["popen"]["stderr"] is stderr_target


def test_child_registration_failure_terminates_worker(monkeypatch, tmp_path):
    observed = {"cleanup": 0}

    class FakeProcess:
        pid = 12349

    class FakeJob:
        def assign(self, _process):
            pass

        def close(self):
            pass

    monkeypatch.setattr(rag, "_WindowsKillJob", FakeJob)
    monkeypatch.setattr(
        rag.subprocess, "Popen", lambda *_args, **_kwargs: FakeProcess())

    def fake_cleanup(*_args, **_kwargs):
        observed["cleanup"] += 1
        return True

    monkeypatch.setattr(rag, "_terminate_supervised_process", fake_cleanup)

    with pytest.raises(RuntimeError, match="registration failed"):
        rag._run_cli_with_deadline(
            tmp_path / "worker.py", [], operation="index", timeout=5,
            on_child_started=lambda _process: (_ for _ in ()).throw(
                RuntimeError("registration failed")),
        )

    assert observed["cleanup"] == 1


def test_registration_failure_never_releases_target_code(tmp_path):
    script = tmp_path / "must_remain_gated.py"
    marker = tmp_path / "executed.txt"
    script.write_text(
        "from pathlib import Path\n"
        "import sys\n"
        "Path(sys.argv[1]).write_text('executed', encoding='utf-8')\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="registration failed"):
        rag._run_cli_with_deadline(
            script, [str(marker)], operation="index", timeout=5,
            on_child_started=lambda _process: (_ for _ in ()).throw(
                RuntimeError("registration failed")),
        )

    assert not marker.exists()


def test_normal_exit_fails_closed_when_tree_cleanup_is_unconfirmed(
        monkeypatch, tmp_path):
    cleanup_calls = []

    class FakeProcess:
        pid = 12350

        def wait(self, timeout):
            return 0

        def poll(self):
            return 0

    class FakeJob:
        def assign(self, _process):
            pass

        def terminate(self):
            return True

        def close(self):
            return True

    monkeypatch.setattr(rag, "_WindowsKillJob", FakeJob)
    monkeypatch.setattr(
        rag.subprocess, "Popen", lambda *_args, **_kwargs: FakeProcess())
    monkeypatch.setattr(
        rag, "_terminate_supervised_process",
        lambda *_args, **_kwargs: cleanup_calls.append(True) or False)

    with pytest.raises(RuntimeError, match="cleanup could not be confirmed"):
        rag._run_cli_with_deadline(
            tmp_path / "worker.py", [], operation="index", timeout=5)

    assert len(cleanup_calls) == 1


def test_supervised_timeout_terminates_worker_descendants(tmp_path):
    script = tmp_path / "parent_worker.py"
    heartbeat = tmp_path / "heartbeat.txt"
    child_code = (
        "from pathlib import Path; import sys, time; "
        "p=Path(sys.argv[1]); "
        "[(p.write_text(str(i), encoding='utf-8'), time.sleep(0.05)) "
        "for i in range(200)]"
    )
    script.write_text(
        textwrap.dedent(
            f"""
            from pathlib import Path
            import subprocess
            import sys
            import time

            heartbeat = Path(sys.argv[1])
            subprocess.Popen([sys.executable, "-c", {child_code!r},
                              str(heartbeat)])
            deadline = time.monotonic() + 5
            while not heartbeat.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            time.sleep(60)
            """
        ),
        encoding="utf-8",
    )

    code = rag._run_cli_with_deadline(
        script, [str(heartbeat)], operation="query", timeout=1)

    assert code == 124
    assert heartbeat.is_file()
    time.sleep(0.2)
    stopped_value = heartbeat.read_text(encoding="utf-8")
    time.sleep(0.3)
    assert heartbeat.read_text(encoding="utf-8") == stopped_value


def test_normal_worker_exit_terminates_remaining_descendants(
        monkeypatch, tmp_path):
    script = tmp_path / "exiting_parent.py"
    started = tmp_path / "child.started"
    heartbeat = tmp_path / "child.heartbeat"
    child_code = textwrap.dedent(
        """
        import os
        from pathlib import Path
        import signal
        import sys
        import time

        if os.name != "nt":
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
        started = Path(sys.argv[1])
        heartbeat = Path(sys.argv[2])
        heartbeat.write_text("0", encoding="utf-8")
        started.write_text("ready", encoding="utf-8")
        for index in range(1, 400):
            heartbeat.write_text(str(index), encoding="utf-8")
            time.sleep(0.05)
        """
    )
    script.write_text(
        textwrap.dedent(
            f"""
            from pathlib import Path
            import subprocess
            import sys
            import time

            subprocess.Popen([
                sys.executable, "-c", {child_code!r},
                sys.argv[1], sys.argv[2]])
            deadline = time.monotonic() + 5
            while (not Path(sys.argv[1]).exists()
                   and time.monotonic() < deadline):
                time.sleep(0.01)
            raise SystemExit(0)
            """
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(rag, "_SUPERVISED_TERMINATE_GRACE", 0.3)

    assert rag._run_cli_with_deadline(
        script, [str(started), str(heartbeat)],
        operation="query", timeout=5) == 0

    assert started.is_file()
    assert heartbeat.is_file()
    time.sleep(0.15)
    stopped_value = heartbeat.read_text(encoding="utf-8")
    time.sleep(0.3)
    assert heartbeat.read_text(encoding="utf-8") == stopped_value


@pytest.mark.skipif(os.name == "nt", reason="POSIX parent watchdog regression")
def test_posix_watchdog_normal_exit_ignores_inherited_sigterm_disposition(
        tmp_path):
    project_root = Path(__file__).resolve().parents[1]
    completed = tmp_path / "completed.txt"
    worker = tmp_path / "worker.py"
    supervisor = tmp_path / "supervisor.py"
    worker.write_text(
        "from pathlib import Path\n"
        "import sys\n"
        "Path(sys.argv[1]).write_text('done', encoding='utf-8')\n",
        encoding="utf-8",
    )
    supervisor.write_text(
        "from pathlib import Path\n"
        "import signal\n"
        "import sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "import rag\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "raise SystemExit(rag._run_cli_with_deadline(\n"
        "    Path(sys.argv[2]), [sys.argv[3]], operation='watchdog-normal',\n"
        "    timeout=30))\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable, str(supervisor), str(project_root), str(worker),
            str(completed),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=5,
        check=False,
    )

    assert result.returncode == 0
    assert completed.read_text(encoding="utf-8") == "done"


@pytest.mark.skipif(os.name == "nt", reason="POSIX parent watchdog regression")
def test_posix_watchdog_kills_worker_tree_when_supervisor_is_killed(tmp_path):
    project_root = Path(__file__).resolve().parents[1]
    heartbeat = tmp_path / "heartbeat.txt"
    worker_pid_path = tmp_path / "worker.pid"
    child_pid_path = tmp_path / "child.pid"
    worker = tmp_path / "worker.py"
    supervisor = tmp_path / "supervisor.py"
    child_code = (
        "from pathlib import Path; import os, sys, time; "
        "p=Path(sys.argv[1]); "
        "Path(sys.argv[2]).write_text(str(os.getpid()), encoding='utf-8'); "
        "[(p.write_text(str(i), encoding='utf-8'), time.sleep(0.05)) "
        "for i in range(400)]"
    )
    worker.write_text(
        textwrap.dedent(
            f"""
            from pathlib import Path
            import os
            import subprocess
            import sys
            import time
            Path(sys.argv[2]).write_text(
                str(os.getpid()), encoding="utf-8")
            subprocess.Popen([
                sys.executable, "-c", {child_code!r},
                sys.argv[1], sys.argv[3]])
            time.sleep(60)
            """
        ),
        encoding="utf-8",
    )
    supervisor.write_text(
        textwrap.dedent(
            """
            from pathlib import Path
            import sys
            sys.path.insert(0, sys.argv[1])
            import rag
            raise SystemExit(rag._run_cli_with_deadline(
                Path(sys.argv[2]), sys.argv[3:],
                operation="parent-death test", timeout=60))
            """
        ),
        encoding="utf-8",
    )
    process = subprocess.Popen(
        [
            sys.executable, str(supervisor), str(project_root), str(worker),
            str(heartbeat), str(worker_pid_path), str(child_pid_path),
        ],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    watchdog_verified = False
    try:
        deadline = time.monotonic() + 10
        while (not all(path.exists() for path in (
                    heartbeat, worker_pid_path, child_pid_path))
               and time.monotonic() < deadline
               and process.poll() is None):
            time.sleep(0.05)
        assert process.poll() is None
        assert heartbeat.is_file()

        process.kill()
        process.wait(timeout=5)
        time.sleep(0.2)
        stopped_value = heartbeat.read_text(encoding="utf-8")
        time.sleep(0.3)
        assert heartbeat.read_text(encoding="utf-8") == stopped_value
        watchdog_verified = True
    finally:
        if process.poll() is None:
            process.kill()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        if not watchdog_verified:
            try:
                worker_pid = int(worker_pid_path.read_text(encoding="utf-8"))
                os.killpg(worker_pid, signal.SIGKILL)
            except (OSError, ValueError):
                pass


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object regression")
def test_windows_job_kills_worker_tree_when_supervisor_is_killed(tmp_path):
    project_root = Path(__file__).resolve().parents[1]
    heartbeat = tmp_path / "heartbeat.txt"
    worker_pid_path = tmp_path / "worker.pid"
    child_pid_path = tmp_path / "child.pid"
    worker = tmp_path / "worker.py"
    supervisor = tmp_path / "supervisor.py"
    child_code = (
        "from pathlib import Path; import os, sys, time; "
        "p=Path(sys.argv[1]); "
        "Path(sys.argv[2]).write_text(str(os.getpid()), encoding='utf-8'); "
        "[(p.write_text(str(i), encoding='utf-8'), time.sleep(0.05)) "
        "for i in range(400)]"
    )
    worker.write_text(
        textwrap.dedent(
            f"""
            from pathlib import Path
            import os
            import subprocess
            import sys
            import time
            Path(sys.argv[2]).write_text(
                str(os.getpid()), encoding="utf-8")
            subprocess.Popen([
                sys.executable, "-c", {child_code!r},
                sys.argv[1], sys.argv[3]])
            time.sleep(60)
            """
        ),
        encoding="utf-8",
    )
    supervisor.write_text(
        textwrap.dedent(
            """
            from pathlib import Path
            import sys
            sys.path.insert(0, sys.argv[1])
            import rag
            raise SystemExit(rag._run_cli_with_deadline(
                Path(sys.argv[2]), sys.argv[3:],
                operation="parent-death test", timeout=60))
            """
        ),
        encoding="utf-8",
    )
    process = subprocess.Popen(
        [
            sys.executable, str(supervisor), str(project_root), str(worker),
            str(heartbeat), str(worker_pid_path), str(child_pid_path),
        ],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    job_verified = False
    try:
        deadline = time.monotonic() + 10
        while (not all(path.exists() for path in (
                    heartbeat, worker_pid_path, child_pid_path))
               and time.monotonic() < deadline
               and process.poll() is None):
            time.sleep(0.05)
        assert process.poll() is None
        assert heartbeat.is_file()

        process.kill()
        process.wait(timeout=5)
        time.sleep(0.2)
        stopped_value = heartbeat.read_text(encoding="utf-8")
        time.sleep(0.3)
        assert heartbeat.read_text(encoding="utf-8") == stopped_value
        job_verified = True
    finally:
        if process.poll() is None:
            process.kill()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        if not job_verified:
            for pid_path in (child_pid_path, worker_pid_path):
                try:
                    pid = int(pid_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                try:
                    subprocess.run(
                        ["taskkill", "/PID", str(pid), "/T", "/F"],
                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL, check=False, timeout=5,
                    )
                except (OSError, subprocess.SubprocessError):
                    pass


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group regression")
def test_posix_escalation_kills_descendant_that_ignores_sigterm(
        monkeypatch, tmp_path):
    script = tmp_path / "parent_worker.py"
    heartbeat = tmp_path / "heartbeat.txt"
    child_code = (
        "from pathlib import Path; import signal, sys, time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "p=Path(sys.argv[1]); "
        "[(p.write_text(str(i), encoding='utf-8'), time.sleep(0.05)) "
        "for i in range(200)]"
    )
    script.write_text(
        "import subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}, "
        "sys.argv[1]])\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(rag, "_SUPERVISED_TERMINATE_GRACE", 0.2)

    assert rag._run_cli_with_deadline(
        script, [str(heartbeat)], operation="query", timeout=0.5) == 124
    time.sleep(0.2)
    stopped_value = heartbeat.read_text(encoding="utf-8")
    time.sleep(0.3)
    assert heartbeat.read_text(encoding="utf-8") == stopped_value


def test_external_cancel_terminates_real_worker_descendants(tmp_path):
    script = tmp_path / "parent_worker.py"
    started = tmp_path / "started.txt"
    cancel = tmp_path / "cancel.txt"
    heartbeat = tmp_path / "heartbeat.txt"
    child_code = (
        "from pathlib import Path; import sys, time; "
        "p=Path(sys.argv[1]); "
        "[(p.write_text(str(i), encoding='utf-8'), time.sleep(0.05)) "
        "for i in range(400)]"
    )
    script.write_text(
        "import subprocess, sys, time\n"
        "from pathlib import Path\n"
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}, "
        "sys.argv[2]])\n"
        "Path(sys.argv[1]).write_text('ready', encoding='utf-8')\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )

    def request_cancel():
        deadline = time.monotonic() + 5
        while not started.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert started.exists()
        time.sleep(0.2)
        cancel.write_text("cancel", encoding="utf-8")

    requester = threading.Thread(target=request_cancel)
    requester.start()
    code = rag._run_cli_with_deadline(
        script, [str(started), str(heartbeat)], operation="index", timeout=10,
        cancel_requested=cancel.exists,
        stdout_target=subprocess.DEVNULL,
        stderr_target=subprocess.DEVNULL,
    )
    requester.join(timeout=5)

    assert code == 130
    assert not requester.is_alive()
    assert heartbeat.is_file()
    time.sleep(0.2)
    stopped_value = heartbeat.read_text(encoding="utf-8")
    time.sleep(0.3)
    assert heartbeat.read_text(encoding="utf-8") == stopped_value


def test_killed_worker_releases_lease_and_leaves_recovery_marker(
        tmp_path, capsys):
    script = tmp_path / "locked_worker.py"
    db_path = tmp_path / "db"
    lock_path = rag._vector_store_lock_path(db_path)
    marker_path = rag._index_update_marker_path(
        db_path, backend="chroma", collection_name="book")
    script.write_text(
        textwrap.dedent(
            """
            import os
            from pathlib import Path
            import sys
            import time

            lock_path = Path(sys.argv[1])
            marker_path = Path(sys.argv[2])
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            marker_path.parent.mkdir(parents=True, exist_ok=True)
            with lock_path.open("a+b") as lock_handle:
                lock_handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(lock_handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
                marker_path.write_text("{}", encoding="utf-8")
                time.sleep(60)
            """
        ),
        encoding="utf-8",
    )

    code = rag._run_cli_with_deadline(
        script, [str(lock_path), str(marker_path)],
        operation="index", timeout=1)

    assert code == 124
    assert marker_path.is_file()
    with rag._vector_store_lock(
            db_path, backend="qdrant", collection_name="other",
            operation="post-timeout recovery", timeout=0):
        pass
    assert "retry the command" in capsys.readouterr().err


def test_rag_entrypoint_supervises_only_vector_commands(monkeypatch):
    calls = []
    monkeypatch.delenv(rag._SUPERVISED_CHILD_ENV, raising=False)
    monkeypatch.delenv(rag._RUN_ID_ENV, raising=False)
    monkeypatch.setattr(
        rag._run_telemetry, "new_run_id", lambda: "generated-run")
    monkeypatch.setattr(
        rag, "_run_cli_with_deadline",
        lambda script, argv, **kwargs: calls.append(
            (script, argv, kwargs)) or 23,
    )
    monkeypatch.setattr(
        rag, "main", lambda argv=None: calls.append(("main", argv)))

    assert rag._run_rag_entrypoint(["query", "terms"]) == 23
    assert calls[0][1] == ["query", "terms"]
    assert calls[0][2] == {
        "operation": "query",
        "timeout": rag.DEFAULT_OPERATION_TIMEOUTS["query"],
        "run_id": "generated-run",
        "run_events": None,
        "run_report": None,
    }
    calls.clear()

    assert rag._run_rag_entrypoint(
        ["--", "query", "terms", "--operation-timeout", "1"]) == 23
    assert calls[0][2]["timeout"] == 1
    calls.clear()

    assert rag._run_rag_entrypoint(["export"]) == 0
    assert calls == [("main", ["export"])]
    calls.clear()

    assert rag._run_rag_entrypoint(["jobs", "list"]) == 0
    assert calls == [("main", ["jobs", "list"])]
    calls.clear()

    assert rag._run_rag_entrypoint(["--help"]) == 0
    assert calls == [("main", ["--help"])]


def test_rag_entrypoint_propagates_explicit_run_outputs(monkeypatch):
    captured = {}
    monkeypatch.delenv(rag._SUPERVISED_CHILD_ENV, raising=False)
    monkeypatch.setattr(
        rag, "_run_cli_with_deadline",
        lambda script, argv, **kwargs: captured.update(kwargs) or 0)

    assert rag._run_rag_entrypoint([
        "index", "--run-id", "run-42",
        "--run-events", "private/events.jsonl",
        "--run-report=private/report.json",
    ]) == 0

    assert captured["run_id"] == "run-42"
    assert captured["run_events"] == Path("private/events.jsonl")
    assert captured["run_report"] == Path("private/report.json")


def test_supervised_child_does_not_recursively_spawn(monkeypatch):
    calls = []
    monkeypatch.setenv(rag._SUPERVISED_CHILD_ENV, "1")
    monkeypatch.setattr(
        rag, "main", lambda argv=None: calls.append(("main", argv)))
    monkeypatch.setattr(
        rag, "_run_cli_with_deadline",
        lambda *_args, **_kwargs: pytest.fail("child recursively supervised"),
    )

    assert rag._run_rag_entrypoint(["info"]) == 0
    assert calls == [("main", ["info"])]


def test_interactive_vector_action_uses_supervised_worker(monkeypatch):
    captured = {}
    monkeypatch.delenv(rag._SUPERVISED_CHILD_ENV, raising=False)
    monkeypatch.delenv(rag._RUN_ID_ENV, raising=False)
    monkeypatch.setattr(
        rag._run_telemetry, "new_run_id", lambda: "interactive-run")
    monkeypatch.setattr(rag, "_menu_choose", lambda *_args, **_kwargs: "info")
    monkeypatch.setattr(
        rag, "_run_cli_with_deadline",
        lambda script, argv, **kwargs: captured.update(
            script=script, argv=argv, kwargs=kwargs) or 0,
    )

    rag.interactive_menu()

    assert captured["argv"] == ["info"]
    assert captured["kwargs"] == {
        "operation": "info",
        "timeout": rag.DEFAULT_OPERATION_TIMEOUTS["info"],
        "run_id": "interactive-run",
        "run_events": None,
        "run_report": None,
    }


def test_eval_entrypoint_uses_evaluation_deadline(monkeypatch):
    captured = {}
    monkeypatch.delenv(rag._SUPERVISED_CHILD_ENV, raising=False)
    monkeypatch.setattr(
        rag, "_run_cli_with_deadline",
        lambda script, argv, **kwargs: captured.update(
            script=script, argv=argv, kwargs=kwargs) or 17,
    )

    result = retrieval_eval._run_eval_entrypoint(
        ["--chunks", "chunks.jsonl", "--db", "db",
         "--collection", "book", "--operation-timeout", "11"])

    assert result == 17
    assert captured["argv"][-2:] == ["--operation-timeout", "11"]
    assert captured["kwargs"] == {"operation": "evaluation", "timeout": 11}
