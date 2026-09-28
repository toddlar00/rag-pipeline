"""Static context-check diagnostics, cancellation, and dependency-light help."""

import builtins
from pathlib import Path
import subprocess
import sys

import pytest

from ocr_recovery import ReportCleanupError
from tools import evaluate_ocr_context as cli


PRIVATE = "SYNTHETIC_PRIVATE_INPUT"
ARGS = sum((["--"+key, PRIVATE+"-"+key] for key in
            ("pdf", "recovery", "reference", "correspondence", "output")), [])


def report(attention=False):
    return {"requires_attention": attention, "sensitive": PRIVATE,
            "coverage": {"checks": {"total": 4, "evaluated": 3, "passed": 2, "failed": 1, "abstained": 1},
                         "contexts": {"total": 3, "mapped": 2},
                         "pages": {"with_reference_contexts": [1, 2], "source_page_count": 5,
                                   "unchecked_selected_pages": [3], "unchecked_deferred_pages": [4]}}}


@pytest.mark.parametrize("attention,expected", [(False, 0), (True, 3)])
def test_fixed_counts_and_scoped_disclosures_only(monkeypatch, capsys, attention, expected):
    calls = []
    monkeypatch.setattr(cli, "evaluate_context_files", lambda *args: calls.append(args) or report(attention))
    assert cli.main(ARGS) == expected
    assert calls == [tuple(Path(arg) for arg in ARGS[1::2])]
    output = capsys.readouterr()
    assert PRIVATE not in output.out+output.err
    assert "2 passed, 1 failed, 1 abstained; 3 evaluated of 4" in output.out
    assert "2 mapped of 3" in output.out and "2 pages" in output.out
    assert "not full-page or semantic verification" in output.out
    assert "1 selected; 1 deferred" in output.out


@pytest.mark.parametrize("args", [[], ARGS+[PRIVATE], ARGS+["--unknown", PRIVATE], ARGS[:-1],
                                 ["--rec", PRIVATE]+ARGS])
def test_parser_never_echoes_private_values(capsys, args):
    with pytest.raises(SystemExit) as error:
        cli.main(args)
    assert error.value.code == 2
    output = capsys.readouterr()
    assert PRIVATE not in output.out+output.err
    assert "usage: evaluate_ocr_context.py" in output.err


@pytest.mark.parametrize("failure,status", [(ValueError, 2), (OSError, 2), (RuntimeError, 2), (ImportError, 2),
                                          (FileExistsError, 2), (ReportCleanupError, 2), (KeyboardInterrupt, 130)])
def test_operation_and_late_cleanup_errors_are_sanitized(monkeypatch, capsys, failure, status):
    def fail(*args):
        raise failure(PRIVATE)

    monkeypatch.setattr(cli, "evaluate_context_files", fail)
    assert cli.main(ARGS) == status
    output = capsys.readouterr()
    assert PRIVATE not in output.out+output.err
    assert "--output" in output.err
    if failure is ReportCleanupError:
        assert "was created" in output.err
    if failure is KeyboardInterrupt:
        assert "may already exist" in output.err


@pytest.mark.parametrize("failure,status", [(ImportError, 2), (KeyboardInterrupt, 130)])
def test_dependency_import_is_inside_guard(monkeypatch, capsys, failure, status):
    original = builtins.__import__

    def importing(name, *args, **kwargs):
        if name == "ocr_recovery":
            raise failure(PRIVATE)
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", importing)
    assert cli.main(ARGS) == status
    output = capsys.readouterr()
    assert PRIVATE not in output.out+output.err


def test_parser_cancellation_is_sanitized(monkeypatch, capsys):
    def cancel(*args):
        raise KeyboardInterrupt(PRIVATE)

    monkeypatch.setattr(cli._ArgumentParser, "parse_args", cancel)
    assert cli.main(ARGS) == 130
    assert PRIVATE not in capsys.readouterr().err


def test_help_never_loads_source_ocr_or_evaluator_dependencies():
    script = "\n".join(["import sys", "from tools import evaluate_ocr_context as cli",
                        "try: cli.main(['--help'])", "except SystemExit as error: assert error.code == 0",
                        "assert not {'ocr_recovery','ocr_context_evaluation_io','rapidocr','numpy','cv2','pymupdf','rag'}.intersection(sys.modules)"])
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_lazy_io_seam_delegates_exact_paths(monkeypatch):
    import ocr_context_evaluation_io

    called = []
    monkeypatch.setattr(ocr_context_evaluation_io, "evaluate_context_files", lambda *args: called.append(args) or report())
    paths = tuple(Path(x) for x in ARGS[1::2])
    assert cli.evaluate_context_files(*paths) == report()
    assert called == [paths]
