from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

import service_search_worker
import storage_policy


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["_search_worker"],
        ["search_worker", "request.json", "result.json"],
        ["_search_worker", "request.json", "result.json", "extra"],
    ],
)
def test_worker_rejects_every_noncanonical_argument_shape(
        monkeypatch, arguments):
    monkeypatch.setattr(
        service_search_worker,
        "_silence_worker_output",
        lambda: pytest.fail("invalid invocations must not start the worker"),
    )

    assert service_search_worker.main(arguments) == 2


def test_worker_composes_exact_search_dependency(monkeypatch, tmp_path):
    request_path = tmp_path / "request.json"
    result_path = tmp_path / "result.json"
    search_index_fn = service_search_worker.rag.search_index
    captured = {}

    monkeypatch.setattr(
        service_search_worker, "_silence_worker_output", lambda: None)

    def search_worker_main(request, result, *, search_index_fn):
        captured.update(
            request=request,
            result=result,
            search_index_fn=search_index_fn,
        )
        return 17

    monkeypatch.setattr(
        service_search_worker.service_runtime,
        "search_worker_main",
        search_worker_main,
    )

    assert service_search_worker.main([
        "_search_worker", str(request_path), str(result_path)]) == 17
    assert captured == {
        "request": Path(request_path),
        "result": Path(result_path),
        "search_index_fn": search_index_fn,
    }


def test_hidden_worker_executable_emits_only_redacted_envelope(tmp_path):
    request_path = tmp_path / "request.json"
    result_path = tmp_path / "result.json"
    stdout_path = tmp_path / "stdout.bin"
    stderr_path = tmp_path / "stderr.bin"
    storage_policy.atomic_write_private_json(request_path, {})

    with stdout_path.open("w+b") as stdout, stderr_path.open("w+b") as stderr:
        completed = subprocess.run(
            [
                sys.executable,
                str(Path(service_search_worker.__file__).resolve()),
                "_search_worker",
                str(request_path),
                str(result_path),
            ],
            cwd=Path(__file__).resolve().parents[1],
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            check=False,
            timeout=30,
        )

    assert completed.returncode == 1
    assert stdout_path.read_bytes() == b""
    assert stderr_path.read_bytes() == b""
    assert json.loads(result_path.read_text(encoding="utf-8")) == {
        "schema_version": 2,
        "kind": "service_search_result",
        "ok": False,
        "error_code": "service_unavailable",
    }
