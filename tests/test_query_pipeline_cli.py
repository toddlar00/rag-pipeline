from __future__ import annotations

import builtins
import io
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import ai_pipeline_client as client
from tools import query_pipeline as cli


PRIVATE = "PRIVATE_QUERY_OR_ARGUMENT"
ROOT = Path(__file__).resolve().parents[1]


class Input:
    def __init__(self, raw):
        self.buffer = io.BytesIO(raw)

    def isatty(self):
        return False


@pytest.fixture
def fake_reader(monkeypatch):
    class Reader:
        calls = []
        result = {"synthetic": True}
        error = None

        @classmethod
        def from_environment(cls, **kwargs):
            cls.calls.append(("config", kwargs))
            return cls()

        def _result(self, action, *args):
            self.calls.append((action, args))
            if self.error is not None:
                raise self.error
            return self.result

        def health(self, probe): return self._result("health", probe)
        def schema(self): return self._result("schema")
        def corpora(self): return self._result("corpora")
        def search(self, corpus, payload): return self._result("search", corpus, payload)
        def search_evidence(self, corpus, payload): return self._result("search_evidence", corpus, payload)

    monkeypatch.setattr(client, "PipelineReader", Reader)
    return Reader


def test_cli_search_accepts_private_query_only_on_stdin(fake_reader, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", Input(json.dumps({"query": PRIVATE}).encode()))
    assert cli.main(["--host", "::1", "--port", "8766", "--timeout", "4", "search", "--corpus", "synthetic"]) == 0
    assert fake_reader.calls == [
        ("config", {"host": "::1", "port": 8766, "timeout": 4.0, "authenticated": True}),
        ("search", ("synthetic", {"query": PRIVATE, "limit": 5, "mode": "auto", "filters": {}})),
    ]
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"synthetic": True}
    assert not captured.err and PRIVATE not in captured.out


@pytest.mark.parametrize("command", ["schema", "corpora"])
def test_cli_introspection_routes_are_fixed(fake_reader, command, capsys):
    assert cli.main([command]) == 0
    assert fake_reader.calls[1] == (command, ())
    assert json.loads(capsys.readouterr().out) == fake_reader.result


@pytest.mark.parametrize("probe,status,expected", [("live", "live", 0), ("ready", "ready", 0),
                                                 ("ready", "not_ready", 3)])
def test_health_never_requires_credentials(fake_reader, probe, status, expected, capsys):
    fake_reader.result = {"status": status}
    assert cli.main(["health", "--probe", probe]) == expected
    assert fake_reader.calls[0][1]["authenticated"] is False
    assert json.loads(capsys.readouterr().out) == {"status": status}


@pytest.mark.parametrize("arguments", [[], ["delete", PRIVATE], ["jobs"], ["reindex"],
    ["--token", PRIVATE, "corpora"], ["--host", PRIVATE, "health"],
    ["--port", PRIVATE, "health"], ["search", "--corpus", "synthetic", "--query", PRIVATE],
    ["--url", "https://invalid.example", "corpora"], ["raw", "GET", "/v1/jobs"]])
def test_invalid_arguments_are_static_and_do_not_call_client(fake_reader, capsys, arguments):
    with pytest.raises(SystemExit) as caught:
        cli.main(arguments)
    assert caught.value.code == 2 and not fake_reader.calls
    captured = capsys.readouterr()
    assert not captured.out and PRIVATE not in captured.err
    assert json.loads(captured.err)["error"]["code"] == "invalid_arguments"


@pytest.mark.parametrize("raw", [b'{"query":"a","query":"b"}', b'[]', b'\xff', b'x' * 65537],
                         ids=["duplicate", "array", "utf8", "oversized"])
def test_stdin_rejection_never_sends_request(fake_reader, monkeypatch, capsys, raw):
    monkeypatch.setattr(sys, "stdin", Input(raw))
    assert cli.main(["search", "--corpus", "synthetic"]) == 2
    assert len(fake_reader.calls) == 1
    captured = capsys.readouterr()
    assert not captured.out and json.loads(captured.err)["error"]["code"] == "invalid_request"


def test_interactive_search_does_not_wait_for_a_query(fake_reader, monkeypatch, capsys):
    stream = Input(b"")
    monkeypatch.setattr(stream, "isatty", lambda: True)
    monkeypatch.setattr(sys, "stdin", stream)
    assert cli.main(["search", "--corpus", "synthetic"]) == 2
    assert json.loads(capsys.readouterr().err)["error"]["code"] == "invalid_request"


@pytest.mark.parametrize("code,expected", [("invalid_request", 2), ("credentials_missing", 2),
    ("unauthorized", 2), ("connection_failed", 3), ("deadline_exceeded", 3), ("concurrency_limited", 3)])
def test_expected_failures_are_structured_with_stable_exit_codes(fake_reader, capsys, code, expected):
    fake_reader.error = client.PipelineClientError(code)
    assert cli.main(["corpora"]) == expected
    captured = capsys.readouterr()
    assert not captured.out
    assert json.loads(captured.err) == fake_reader.error.as_dict()


@pytest.mark.parametrize("error,expected", [(ImportError(PRIVATE), 2), (OSError(PRIVATE), 2),
                                          (KeyboardInterrupt(PRIVATE), 130)])
def test_dependency_import_failure_is_private_and_cancellation_is_controlled(monkeypatch, capsys, error, expected):
    original = builtins.__import__

    def patched(name, *args, **kwargs):
        if name == "ai_pipeline_client":
            raise error
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", patched)
    assert cli.main(["corpora"]) == expected
    captured = capsys.readouterr()
    assert not captured.out and PRIVATE not in captured.err
    assert json.loads(captured.err)["error"]["code"] in {"client_unavailable", "cancelled"}


def test_late_cancellation_while_printing_does_not_escape(fake_reader, monkeypatch, capsys):
    original = builtins.print

    def interrupt_once(*args, **kwargs):
        if kwargs.get("file") is not sys.stderr:
            raise KeyboardInterrupt(PRIVATE)
        return original(*args, **kwargs)

    monkeypatch.setattr(builtins, "print", interrupt_once)
    assert cli.main(["corpora"]) == 130
    captured = capsys.readouterr()
    assert PRIVATE not in captured.err
    assert json.loads(captured.err)["error"]["code"] == "cancelled"


def test_cancellation_during_argument_parsing_is_private(fake_reader, monkeypatch, capsys):
    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt(PRIVATE)

    monkeypatch.setattr(cli._ArgumentParser, "parse_args", interrupted)
    assert cli.main(["corpora"]) == 130
    assert not fake_reader.calls
    captured = capsys.readouterr()
    assert not captured.out and PRIVATE not in captured.err
    assert json.loads(captured.err)["error"]["code"] == "cancelled"


def test_real_help_has_no_service_or_model_import_and_no_side_effect(tmp_path):
    script = (
        "import sys; from tools import query_pipeline; "
        "\ntry: query_pipeline.main(['--help'])\nexcept SystemExit as e: assert e.code == 0\n"
        "assert not any(n in sys.modules for n in ('ai_pipeline_client','service_http','service_runtime',"
        "'fastapi','rag','rapidocr','pymupdf','numpy'))"
    )
    result = subprocess.run([sys.executable, "-B", "-c", script], cwd=ROOT, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "search" in result.stdout and "corpora" in result.stdout
    assert not list(tmp_path.iterdir())


def test_real_missing_credentials_has_static_json_and_no_network(tmp_path):
    environment = dict(os.environ)
    environment.pop(client.TOKEN_ENVIRONMENT_VARIABLE, None)
    result = subprocess.run([sys.executable, "-B", str(ROOT / "tools/query_pipeline.py"), "corpora"],
                            cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=10)
    assert result.returncode == 2 and not result.stdout
    assert json.loads(result.stderr)["error"]["code"] == "credentials_missing"


def test_real_stdin_error_and_argument_error_never_echo_query(tmp_path):
    environment = dict(os.environ, RAG_PIPELINE_READER_TOKEN="s" * 48)
    result = subprocess.run([sys.executable, "-B", str(ROOT / "tools/query_pipeline.py"),
                             "search", "--corpus", "synthetic"], cwd=tmp_path, env=environment,
                            input=json.dumps({"query": PRIVATE, "unexpected": PRIVATE}),
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 2 and not result.stdout and PRIVATE not in result.stderr
    assert json.loads(result.stderr)["error"]["code"] == "invalid_request"


def test_cli_evidence_routes_same_bounded_stdin_without_query_arguments(fake_reader, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", Input(json.dumps({"query": PRIVATE, "limit": 3}).encode()))
    assert cli.main(["search-evidence", "--corpus", "synthetic"]) == 0
    assert fake_reader.calls == [
        ("config", {"host": "127.0.0.1", "port": 8765, "timeout": 30.0, "authenticated": True}),
        ("search_evidence", ("synthetic", {"query": PRIVATE, "limit": 3, "mode": "auto", "filters": {}})),
    ]
    captured = capsys.readouterr()
    assert json.loads(captured.out) == fake_reader.result and not captured.err
    assert PRIVATE not in captured.out


@pytest.mark.parametrize("raw", [b'{"query":"a","query":"b"}', b'[]', b'\xff', b'x' * 65537,
                                 json.dumps({"query": PRIVATE, "recovery_path": PRIVATE}).encode()],
                         ids=["duplicate", "array", "utf8", "oversized", "private_path"])
def test_evidence_invalid_stdin_never_calls_reader_search(fake_reader, monkeypatch, capsys, raw):
    monkeypatch.setattr(sys, "stdin", Input(raw))
    assert cli.main(["search-evidence", "--corpus", "synthetic"]) == 2
    assert len(fake_reader.calls) == 1
    captured = capsys.readouterr()
    assert not captured.out and PRIVATE not in captured.err
    assert json.loads(captured.err)["error"]["code"] == "invalid_request"


@pytest.mark.parametrize("arguments", [["search-evidence"], ["search-evidence", "--corpus", "synthetic", "--query", PRIVATE],
    ["search-evidence", "--corpus", "synthetic", "--pdf", PRIVATE],
    ["search-evidence", "--corpus", "synthetic", "--recovery", PRIVATE]])
def test_evidence_has_no_path_or_query_flags(fake_reader, capsys, arguments):
    with pytest.raises(SystemExit) as caught:
        cli.main(arguments)
    assert caught.value.code == 2 and not fake_reader.calls
    captured = capsys.readouterr()
    assert not captured.out and PRIVATE not in captured.err
    assert json.loads(captured.err)["error"]["code"] == "invalid_arguments"


@pytest.mark.parametrize("failure,expected", [(client.PipelineClientError("invalid_response"), 2),
    (client.PipelineClientError("forbidden"), 2), (client.PipelineClientError("deadline_exceeded"), 3),
    (KeyboardInterrupt(PRIVATE), 130), (RuntimeError(PRIVATE), 2)])
def test_evidence_failures_and_cancellation_are_static(fake_reader, monkeypatch, capsys, failure, expected):
    monkeypatch.setattr(sys, "stdin", Input(b'{"query":"synthetic"}'))
    fake_reader.error = failure
    assert cli.main(["search-evidence", "--corpus", "synthetic"]) == expected
    captured = capsys.readouterr()
    assert not captured.out and PRIVATE not in captured.err
    assert json.loads(captured.err)["ok"] is False


def test_evidence_help_is_fixed_and_explains_unverified_accuracy_without_loading_client(monkeypatch, capsys):
    original = builtins.__import__

    def blocked(name, *args, **kwargs):
        assert name not in {"ai_pipeline_client", "service_evidence_contracts", "rag"}
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)
    monkeypatch.setattr(sys, "argv", [PRIVATE])
    with pytest.raises(SystemExit) as caught:
        cli.main(["--help"])
    assert caught.value.code == 0
    captured = capsys.readouterr()
    assert "search-evidence" in captured.out and "unverified" in captured.out
    assert PRIVATE not in captured.out and not captured.err
