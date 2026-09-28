"""Lazy, content-free Docling proposal CLI guidance and cancellation."""

from pathlib import Path
import subprocess
import sys

import pytest

import ocr_recovery
from tools import propose_ocr_layout as cli


PRIVATE = "PRIVATE-SOURCE-OR-EXCEPTION"
ARGS = ["--pdf", PRIVATE + ".pdf", "--docling", "doc.json", "--recovery", "recovery.json", "--output", "new.json"]


def test_forwarding_and_static_review_summary(monkeypatch, capsys):
    calls = []

    def propose(*args):
        calls.append(args)
        return {"summary": {"pages": 3, "proposed": 1, "abstained": 1, "unavailable": 1, "regions": 4},
                "coverage": {"deferred_pages": [4], "not_selected_pages": [5]}, "untrusted": PRIVATE}

    monkeypatch.setattr(cli, "propose_docling_files", propose)
    assert cli.main(ARGS) == 3
    assert calls == [(Path(PRIVATE + ".pdf"), Path("doc.json"), Path("recovery.json"), Path("new.json"))]
    output = capsys.readouterr()
    assert "1 abstained" in output.out
    assert "Manual review" in output.out
    assert "1 deferred pages" in output.out
    assert PRIVATE not in output.out + output.err


@pytest.mark.parametrize("args", [[], [PRIVATE], ARGS + ["--unknown", PRIVATE], ARGS[:-1],
                                  ARGS + ["--pdf"], ["--help", PRIVATE]])
def test_parser_errors_never_echo_arbitrary_input(args, capsys):
    with pytest.raises(SystemExit) as caught:
        cli.main(args)
    assert caught.value.code in (0, 2)
    captured = capsys.readouterr()
    assert PRIVATE not in captured.out + captured.err


@pytest.mark.parametrize("exception,code,expected", [
    (ValueError(PRIVATE), 2, "could not complete"), (RuntimeError(PRIVATE), 2, "could not complete"),
    (ImportError(PRIVATE), 2, "could not complete"), (FileExistsError(PRIVATE), 2, "output exists"),
    (ocr_recovery.ReportCleanupError(PRIVATE), 2, "cleanup failed"),
    (KeyboardInterrupt(PRIVATE), 130, "cancelled"),
])
def test_safe_errors_and_cancellation(monkeypatch, capsys, exception, code, expected):
    def fail(*_args):
        raise exception

    monkeypatch.setattr(cli, "propose_docling_files", fail)
    assert cli.main(ARGS) == code
    captured = capsys.readouterr()
    assert expected in captured.err
    assert PRIVATE not in captured.out + captured.err


def test_parse_cancellation_is_inside_guard(monkeypatch, capsys):
    def cancel(*_args):
        raise KeyboardInterrupt(PRIVATE)

    monkeypatch.setattr(cli._ArgumentParser, "parse_args", cancel)
    assert cli.main(ARGS) == 130
    assert PRIVATE not in capsys.readouterr().err


def test_real_help_is_lightweight_and_useful():
    result = subprocess.run([sys.executable, "-c", "import sys; from tools.propose_ocr_layout import main; "
        "\ntry: main(['--help'])\nexcept SystemExit as e: assert e.code == 0\n"
        "assert not {'rag','docling','docling_core','pymupdf','cv2','numpy','rapidocr'} & set(sys.modules)"],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "--docling" in result.stdout
    assert "v3 conversion completion" in " ".join(result.stdout.split())
