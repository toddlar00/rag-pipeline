"""Hard-deadline regressions for killable CLI operation workers."""

from pathlib import Path
import math
import os
import subprocess
import sys
import textwrap
import threading
import time

import pytest

import eval as retrieval_eval
import rag


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
            "os.environ.get('RAG_TEST_SECRET', ''), encoding='utf-8')",
            "raise SystemExit(7)",
        ]),
        encoding="utf-8",
    )

    code = rag._run_cli_with_deadline(
        script, [str(marker)], operation="test", timeout=5,
        environment_overrides={"RAG_TEST_SECRET": "child-only"})

    assert code == 7
    assert marker.read_text(encoding="utf-8") == "1|child-only"


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


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object regression")
def test_windows_job_kills_worker_tree_when_supervisor_is_killed(tmp_path):
    project_root = Path(__file__).resolve().parents[1]
    heartbeat = tmp_path / "heartbeat.txt"
    worker = tmp_path / "worker.py"
    supervisor = tmp_path / "supervisor.py"
    child_code = (
        "from pathlib import Path; import sys, time; "
        "p=Path(sys.argv[1]); "
        "[(p.write_text(str(i), encoding='utf-8'), time.sleep(0.05)) "
        "for i in range(400)]"
    )
    worker.write_text(
        textwrap.dedent(
            f"""
            import subprocess
            import sys
            import time
            subprocess.Popen([sys.executable, "-c", {child_code!r},
                              sys.argv[1]])
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
                Path(sys.argv[2]), [sys.argv[3]],
                operation="parent-death test", timeout=60))
            """
        ),
        encoding="utf-8",
    )
    process = subprocess.Popen([
        sys.executable, str(supervisor), str(project_root), str(worker),
        str(heartbeat),
    ])
    deadline = time.monotonic() + 10
    while not heartbeat.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert heartbeat.is_file()

    process.kill()
    process.wait(timeout=5)
    time.sleep(0.2)
    stopped_value = heartbeat.read_text(encoding="utf-8")
    time.sleep(0.3)
    assert heartbeat.read_text(encoding="utf-8") == stopped_value


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


def test_killed_worker_releases_lease_and_leaves_recovery_marker(
        tmp_path, capsys):
    project_root = Path(__file__).resolve().parents[1]
    script = tmp_path / "locked_worker.py"
    db_path = tmp_path / "db"
    script.write_text(
        textwrap.dedent(
            """
            from pathlib import Path
            import sys
            import time

            sys.path.insert(0, sys.argv[1])
            import rag

            db_path = Path(sys.argv[2])
            with rag._vector_store_lock(
                    db_path, backend="chroma", collection_name="book",
                    operation="hung test worker", timeout=2):
                rag._begin_index_update(
                    db_path, backend="chroma", collection_name="book",
                    source_sha256="pending", source_record_count=1,
                    owner_token="killed-worker")
                time.sleep(60)
            """
        ),
        encoding="utf-8",
    )

    code = rag._run_cli_with_deadline(
        script, [str(project_root), str(db_path)],
        operation="index", timeout=1)

    assert code == 124
    marker_path = rag._index_update_marker_path(
        db_path, backend="chroma", collection_name="book")
    assert marker_path.is_file()
    with rag._vector_store_lock(
            db_path, backend="qdrant", collection_name="other",
            operation="post-timeout recovery", timeout=0):
        pass
    assert "retry the command" in capsys.readouterr().err


def test_rag_entrypoint_supervises_only_vector_commands(monkeypatch):
    calls = []
    monkeypatch.delenv(rag._SUPERVISED_CHILD_ENV, raising=False)
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
    }
    calls.clear()

    assert rag._run_rag_entrypoint(
        ["--", "query", "terms", "--operation-timeout", "1"]) == 23
    assert calls[0][2]["timeout"] == 1
    calls.clear()

    assert rag._run_rag_entrypoint(["export"]) == 0
    assert calls == [("main", ["export"])]
    calls.clear()

    assert rag._run_rag_entrypoint(["--help"]) == 0
    assert calls == [("main", ["--help"])]


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
