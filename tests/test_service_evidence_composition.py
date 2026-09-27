from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import subprocess
import sys

import pytest

import application_composition as root


def _composition(**kwargs):
    return root.ServiceApplicationComposition(
        runtime_factory=lambda *a, **k: object(), http_factory=lambda *a, **k: object(),
        runtime_binding=object(), job_coordination_binding=object(), http_binding=object(), **kwargs)


@pytest.mark.parametrize("kwargs", [
    {"evidence_runner": lambda: None}, {"evidence_http_installer": lambda: None},
    {"evidence_http_binding": object()},
    {"evidence_runner": 1, "evidence_http_installer": lambda: None, "evidence_http_binding": object()},
])
def test_partial_evidence_composition_is_rejected(kwargs):
    with pytest.raises(TypeError, match="complete"):
        _composition(**kwargs)


def test_opt_in_requires_complete_composition_before_factories():
    def never(*a, **k):
        pytest.fail("factory must not run")

    selected = replace(_composition(), runtime_factory=never, http_factory=never)
    with pytest.raises(TypeError, match="not configured"):
        root.create_service_application({}, object(), composition=selected, evidence_configs={"book": object()})
    with pytest.raises(TypeError, match="not configured"):
        root.create_service_http_app(object(), object(), composition=selected, evidence_enabled=True)


def test_one_complete_generation_is_captured_before_runtime_construction(monkeypatch):
    calls = []
    configs = {"book": object()}
    runtime, app, credential = object(), object(), object()

    def runtime_factory(corpora, **kwargs):
        calls.append(("runtime", corpora, kwargs))
        monkeypatch.setattr(root, "_DEFAULT_SERVICE_APPLICATION_COMPOSITION", _composition())
        return runtime

    def http_factory(*args, **kwargs):
        calls.append(("http", args, kwargs))
        return app

    def install(*args, **kwargs):
        calls.append(("install", args, kwargs))

    selected = replace(_composition(evidence_runner=lambda: None,
                                    evidence_http_installer=install, evidence_http_binding=object()),
                       runtime_factory=runtime_factory, http_factory=http_factory)
    monkeypatch.setattr(root, "_DEFAULT_SERVICE_APPLICATION_COMPOSITION", selected)
    assert root.create_service_application({}, credential, evidence_configs=configs) is app
    assert [call[0] for call in calls] == ["runtime", "http", "install"]
    assert calls[0][2]["evidence_configs"] is configs
    assert calls[0][2]["evidence_runner"] is selected.evidence_runner
    assert calls[1][1] == (runtime, credential)
    assert calls[1][2]["http_binding"] is selected.http_binding
    assert "evidence_enabled" not in calls[1][2]
    assert calls[2][1] == (app, runtime)
    assert calls[2][2]["binding"] is selected.evidence_http_binding


@pytest.mark.parametrize("module,resolve", [("application_composition", False),
                                           ("application_composition", True),
                                           ("service_evidence_runtime", False)])
def test_new_host_path_stays_cold_without_rag_or_model_stack(module, resolve):
    script = f"import sys; import {module}; "
    if resolve:
        pytest.importorskip("fastapi")
        script += "application_composition.default_service_application_composition(); "
    script += ("forbidden=('rag','job_manager','service_evidence_search','qdrant_client',"
               "'torch','transformers','sentence_transformers'); "
               "assert not any(name==part or name.startswith(part+'.') for name in sys.modules for part in forbidden)")
    if module == "application_composition" and not resolve:
        script += "; assert 'fastapi' not in sys.modules and 'service_runtime' not in sys.modules"
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, timeout=30,
                             cwd=Path(__file__).resolve().parents[1], check=False)
    assert result.returncode == 0, result.stderr.decode(errors="replace")


def test_service_cli_opt_in_loads_separate_registry_before_construction(monkeypatch, tmp_path):
    pytest.importorskip("fastapi")
    import service_api
    import service_runtime
    import uvicorn

    calls = []
    corpora, evidence, app = {"book": object()}, {"book": object()}, object()
    monkeypatch.setattr(service_runtime, "load_corpus_registry", lambda p: corpora)

    def load(path, allowed):
        assert path == tmp_path / "evidence.json" and allowed is corpora
        calls.append("load")
        return evidence

    monkeypatch.setattr(service_runtime, "load_evidence_registry", load)
    monkeypatch.setattr(service_api, "load_token_file", lambda p: ("r" if p.name == "reader" else "a") * 48)

    def create(**kwargs):
        assert kwargs["corpora"] is corpora and kwargs["evidence_configs"] is evidence
        calls.append("create")
        return app

    def run(actual, **kwargs):
        assert actual is app and kwargs["host"] == "127.0.0.1" and kwargs["workers"] == 1
        calls.append("run")

    monkeypatch.setattr(root, "create_service_application", create)
    monkeypatch.setattr(uvicorn, "run", run)
    assert service_api.main(["serve", "--config", str(tmp_path / "corpora.json"),
                             "--evidence-config", str(tmp_path / "evidence.json"),
                             "--reader-token-file", str(tmp_path / "reader"),
                             "--admin-token-file", str(tmp_path / "admin")]) == 0
    assert calls == ["load", "create", "run"]
