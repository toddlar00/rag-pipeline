"""Private, bounded CLI feedback for the synthetic-only OCR challenge builder."""

from __future__ import annotations

import copy
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

from tools import build_ocr_challenge as cli


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PRIVATE = "PRIVATE_PATH_OR_EXCEPTION_\u03b4_\u6cd5"
ARGS = ["--output-dir", PRIVATE]
SUCCESS = (
    "Synthetic OCR challenge: 8 image-only pages created with fixed references "
    "and a manifest. No OCR was run.\n"
    "Synthetic evidence only; review layout and use the same PDF/references "
    "for every run. See docs/ocr-accuracy.md.\n"
)


def test_success_forwards_only_the_output_directory_and_prints_fixed_summary(monkeypatch, capsys):
    manifest = {
        "page_count": 8,
        "cases": [{"private": PRIVATE}],
        "source_path": PRIVATE,
        "library_version": PRIVATE,
    }
    original = copy.deepcopy(manifest)
    seen = []

    def build(output_dir):
        seen.append(output_dir)
        return manifest

    monkeypatch.setattr(cli, "build_challenge", build)
    assert cli.main(ARGS) == 0
    assert seen == [Path(PRIVATE)]
    assert manifest == original
    output = capsys.readouterr()
    assert output.out == SUCCESS
    assert output.err == ""
    assert PRIVATE not in output.out + output.err


def test_success_does_not_render_arbitrary_returned_manifest_fields(monkeypatch, capsys):
    monkeypatch.setattr(cli, "build_challenge", lambda _path: {
        "page_count": PRIVATE, "cases": PRIVATE, "summary": PRIVATE,
    })
    assert cli.main(ARGS) == 0
    assert capsys.readouterr() == (SUCCESS, "")


@pytest.mark.parametrize("arguments", [
    [], ["--output-dir"], [PRIVATE],
    ["--output-dir", PRIVATE, PRIVATE],
    ["--output-dir", PRIVATE, f"--{PRIVATE}"],
    ["--output-dir", PRIVATE, "--pdf", PRIVATE],
    ["--output-dir", PRIVATE, "--pages", "1"],
    ["--output-dir", PRIVATE, "--dpi", "300"],
    ["--output-dir", PRIVATE, "--timeout-seconds", "1"],
])
def test_bad_arguments_never_echo_input_or_invoke_builder(monkeypatch, capsys, arguments):
    def unexpected(*_args, **_kwargs):
        pytest.fail("invalid arguments invoked builder")

    monkeypatch.setattr(cli, "build_challenge", unexpected)
    monkeypatch.setattr(sys, "argv", [f"{PRIVATE}/private_program.py"])
    with pytest.raises(SystemExit) as captured:
        cli.main(arguments)
    assert captured.value.code == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert "usage: build_ocr_challenge.py" in output.err
    assert "--help" in output.err
    assert PRIVATE not in output.err
    assert "Traceback" not in output.err


@pytest.mark.parametrize("error_type", [
    ValueError, OSError, RuntimeError, ImportError, FileExistsError,
    PermissionError, NotADirectoryError, ModuleNotFoundError,
])
def test_expected_failures_return_two_with_static_partial_output_guidance(
        monkeypatch, capsys, error_type):
    def fail(_path):
        raise error_type(PRIVATE)

    monkeypatch.setattr(cli, "build_challenge", fail)
    assert cli.main(ARGS) == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert "could not finish" in output.err
    for token in ("PyMuPDF", "OpenCV", "NumPy", "partial", "new directory"):
        assert token in output.err
    assert PRIVATE not in output.err
    assert "Traceback" not in output.err


def test_cancel_returns_130_without_exception_detail_or_success(monkeypatch, capsys):
    def cancel(_path):
        raise KeyboardInterrupt(PRIVATE)

    monkeypatch.setattr(cli, "build_challenge", cancel)
    assert cli.main(ARGS) == 130
    output = capsys.readouterr()
    assert output.out == ""
    assert "cancel" in output.err.lower()
    assert "partial" in output.err.lower()
    assert PRIVATE not in output.err
    assert "Traceback" not in output.err


@pytest.mark.parametrize(("error_type", "expected_code"), [
    (OSError, 2), (KeyboardInterrupt, 130),
])
def test_cli_preserves_partial_output_and_does_not_attempt_cleanup(
        monkeypatch, tmp_path, capsys, error_type, expected_code):
    destination = tmp_path / PRIVATE
    partial = destination / "synthetic_partial.bin"

    def fail_after_partial(output_dir):
        output_dir.mkdir()
        partial.write_bytes(b"synthetic-only partial output")
        raise error_type(PRIVATE)

    monkeypatch.setattr(cli, "build_challenge", fail_after_partial)
    assert cli.main(["--output-dir", str(destination)]) == expected_code
    assert partial.read_bytes() == b"synthetic-only partial output"
    output = capsys.readouterr()
    assert PRIVATE not in output.out + output.err


def test_help_does_not_invoke_builder(monkeypatch, capsys):
    def unexpected(*_args, **_kwargs):
        pytest.fail("help invoked builder")

    monkeypatch.setattr(cli, "build_challenge", unexpected)
    with pytest.raises(SystemExit) as captured:
        cli.main(["--help"])
    assert captured.value.code == 0
    output = capsys.readouterr()
    assert "--output-dir" in output.out
    assert "synthetic" in output.out.lower()
    assert output.err == ""


def test_real_help_imports_no_pdf_image_or_ocr_dependencies():
    program = textwrap.dedent("""\
        import sys
        from tools import build_ocr_challenge

        try:
            build_ocr_challenge.main(['--help'])
        except SystemExit as error:
            assert error.code == 0
        else:
            raise AssertionError('help did not exit')
        forbidden = {
            'rag', 'pymupdf', 'fitz', 'cv2', 'numpy', 'rapidocr', 'onnxruntime',
            'ocr_recovery_runtime', 'docling', 'torch', 'PIL',
        }
        assert not forbidden.intersection(sys.modules), sorted(forbidden.intersection(sys.modules))
        """)
    result = subprocess.run(
        [sys.executable, "-c", program], cwd=PROJECT_ROOT,
        stdin=subprocess.DEVNULL, capture_output=True, text=True,
        timeout=30, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--output-dir" in result.stdout
    assert result.stderr == ""


def test_real_parser_error_is_sanitized():
    result = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "tools" / "build_ocr_challenge.py"),
         *ARGS, f"--{PRIVATE}"],
        cwd=PROJECT_ROOT, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 2
    assert result.stdout == ""
    assert PRIVATE not in result.stderr
    assert "Traceback" not in result.stderr
    assert "--help" in result.stderr
