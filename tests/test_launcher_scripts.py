import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_script(name: str) -> ModuleType:
    path = PROJECT_ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"test_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("name", "required_flags"),
    [
        (
            "run_civpro",
            {
                "--llm-classify",
                "--contextualize",
                "--reconstruct-headings",
                "--quality-score",
                "--force",
            },
        ),
        (
            "resume_civpro",
            {"--resume", "--full-reindex", "--force"},
        ),
    ],
)
def test_moved_launcher_runs_rag_from_repository_root(
    monkeypatch, name, required_flags
):
    module = _load_script(name)
    observed = {}

    def fake_run(command, *, cwd, check):
        observed.update(command=command, cwd=cwd, check=check)
        return SimpleNamespace(returncode=23)

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    assert module.main() == 23
    command = observed["command"]
    assert command[:2] == [sys.executable, str(PROJECT_ROOT / "rag.py")]
    assert command[2:4] == ["full", "--pdf"]
    assert Path(command[4]).parent == PROJECT_ROOT
    assert Path(command[4]).suffix == ".pdf"
    assert required_flags <= set(command)
    assert observed["cwd"] == PROJECT_ROOT
    assert observed["check"] is False
