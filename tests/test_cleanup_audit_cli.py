"""Fixed CLI diagnostics never echo private paths, text or raw exception strings."""

import builtins
from pathlib import Path
import subprocess
import sys

import pytest

from ocr_recovery import ReportCleanupError
from tools import audit_cleanup as cli


PRIVATE = "SYNTHETIC_PRIVATE_INPUT"
ARGS = sum((["--" + key, PRIVATE + "-" + key] for key in ("pdf", "recovery", "plan", "output")), [])


def report(attention=False):
    return {"requires_attention": attention, "private": PRIVATE,
            "summary": {"changed": 2, "unchanged": 3, "abstained": 1, "changed_steps": 7, "risk_entries": 2},
            "coverage": {"fully_selected_candidate_pages": [1, 2], "source_page_count": 5,
                         "unselected_recovery_pages": [3], "deferred_pages": [4]}}


@pytest.mark.parametrize("attention,expected", [(False, 0), (True, 3)])
def test_fixed_counts_scope_and_lazy_path_arguments(monkeypatch, capsys, attention, expected):
    calls = []
    monkeypatch.setattr(cli, "audit_cleanup_files", lambda *args: calls.append(args) or report(attention))
    assert cli.main(ARGS) == expected
    assert calls == [tuple(Path(arg) for arg in ARGS[1::2])]
    output = capsys.readouterr()
    assert PRIVATE not in output.out + output.err
    assert "2 changed, 3 unchanged, 1 abstained; 7 observed" in output.out
    assert "2 fully selected of 5" in output.out and "1 deferred" in output.out
    assert "do not prove errors" in output.out and "source-aware wrappers" in output.out


@pytest.mark.parametrize("args", [[], ARGS + [PRIVATE], ARGS + ["--unknown", PRIVATE], ARGS[:-1],
                                 ["--rec", PRIVATE] + ARGS])
def test_parser_sanitized_and_no_abbreviated_flags(capsys, args):
    with pytest.raises(SystemExit) as caught:
        cli.main(args)
    assert caught.value.code == 2
    output = capsys.readouterr()
    assert PRIVATE not in output.out + output.err
    assert "usage: audit_cleanup.py" in output.err


@pytest.mark.parametrize("failure,status", [(ValueError, 2), (OSError, 2), (RuntimeError, 2), (ImportError, 2),
                                          (FileExistsError, 2), (ReportCleanupError, 2), (KeyboardInterrupt, 130)])
def test_expected_errors_and_late_outcomes_are_static(monkeypatch, capsys, failure, status):
    def fail(*args):
        raise failure(PRIVATE)

    monkeypatch.setattr(cli, "audit_cleanup_files", fail)
    assert cli.main(ARGS) == status
    output = capsys.readouterr()
    assert PRIVATE not in output.out + output.err
    assert "--output" in output.err
    if failure is ReportCleanupError:
        assert "was created" in output.err
    if failure is KeyboardInterrupt:
        assert "may already exist" in output.err


@pytest.mark.parametrize("failure,status", [(ImportError, 2), (KeyboardInterrupt, 130)])
def test_dependency_import_guard(monkeypatch, capsys, failure, status):
    original = builtins.__import__

    def importing(name, *args, **kwargs):
        if name == "ocr_recovery":
            raise failure(PRIVATE)
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", importing)
    assert cli.main(ARGS) == status
    assert PRIVATE not in capsys.readouterr().err


def test_parser_cancellation_guard(monkeypatch, capsys):
    def cancel(*args):
        raise KeyboardInterrupt(PRIVATE)

    monkeypatch.setattr(cli._ArgumentParser, "parse_args", cancel)
    assert cli.main(ARGS) == 130
    assert PRIVATE not in capsys.readouterr().err


def test_help_is_dependency_light():
    code = "\n".join(["import sys", "from tools import audit_cleanup as cli",
                      "try: cli.main(['--help'])", "except SystemExit as error: assert error.code == 0",
                      "assert not {'cleanup_audit','cleanup_audit_io','ocr_recovery','rag','docling','rapidocr','numpy'}.intersection(sys.modules)"])
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_io_seam_delegation(monkeypatch):
    import cleanup_audit_io

    calls = []
    monkeypatch.setattr(cleanup_audit_io, "audit_cleanup_files", lambda *args: calls.append(args) or report())
    paths = tuple(Path(arg) for arg in ARGS[1::2])
    assert cli.audit_cleanup_files(*paths) == report() and calls == [paths]
