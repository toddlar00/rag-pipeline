import subprocess
from pathlib import Path

import pytest

from tools import check_python_sources


def test_parse_tracked_python_paths_is_sorted_and_exact():
    assert check_python_sources.parse_tracked_python_paths(
        b"z.py\0nested/a.py\0"
    ) == (Path("nested/a.py"), Path("z.py"))


@pytest.mark.parametrize(
    "output, message",
    [
        (b"", "no tracked Python"),
        (b"rag.py", "not NUL-terminated"),
        (b"../escape.py\0", "invalid Python source path"),
        (b"C:escape.py\0", "invalid Python source path"),
        (b"README.md\0", "invalid Python source path"),
        (b"rag.py\0rag.py\0", "duplicate Python source path"),
    ],
)
def test_parse_tracked_python_paths_rejects_malformed_inventory(output, message):
    with pytest.raises(check_python_sources.SourceGateError, match=message):
        check_python_sources.parse_tracked_python_paths(output)


def test_compile_python_sources_uses_temporary_outputs(tmp_path):
    source = tmp_path / "package" / "module.py"
    source.parent.mkdir()
    source.write_text("VALUE: int = 1\n", encoding="utf-8")

    assert check_python_sources.compile_python_sources(
        tmp_path, (Path("package/module.py"),)
    ) == 1
    assert not list(tmp_path.rglob("*.pyc"))
    assert not list(tmp_path.rglob("__pycache__"))


def test_compile_python_sources_reports_the_failing_relative_path(tmp_path):
    source = tmp_path / "bad.py"
    source.write_text("if True print('broken')\n", encoding="utf-8")

    with pytest.raises(
        check_python_sources.SourceGateError,
        match=r"Python compilation failed for bad\.py",
    ):
        check_python_sources.compile_python_sources(tmp_path, (Path("bad.py"),))


def test_validate_source_paths_rejects_missing_and_duplicate_targets(tmp_path):
    source = tmp_path / "source.py"
    source.write_text("pass\n", encoding="utf-8")

    with pytest.raises(check_python_sources.SourceGateError, match="missing"):
        check_python_sources.validate_source_paths(
            tmp_path, (Path("missing.py"),)
        )
    with pytest.raises(check_python_sources.SourceGateError, match="same Python"):
        check_python_sources.validate_source_paths(
            tmp_path, (Path("source.py"), Path("source.py"))
        )


def test_repository_gate_includes_modules_omitted_by_the_old_ci_list():
    root = Path(__file__).resolve().parents[1]
    paths = {path.as_posix() for path in check_python_sources.tracked_python_paths(root)}

    assert {
        "attempt_reporting.py",
        "document_profiles.py",
        "evaluation_inputs.py",
        "evaluation_queries.py",
        "evaluation_release.py",
        "evaluation_review.py",
        "operational_drills.py",
        "operational_metrics.py",
        "quality_core.py",
        "table_retrieval_core.py",
        "vector_lifecycle.py",
    } <= paths
    assert check_python_sources.compile_python_sources(
        root, tuple(sorted((Path(path) for path in paths), key=lambda path: path.as_posix()))
    ) == len(paths)


def test_tracked_python_paths_fails_closed_when_git_fails(monkeypatch, tmp_path):
    def fail_git(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 128, b"", b"not a worktree")

    monkeypatch.setattr(check_python_sources.subprocess, "run", fail_git)

    with pytest.raises(check_python_sources.SourceGateError, match="exit code 128"):
        check_python_sources.tracked_python_paths(tmp_path)
