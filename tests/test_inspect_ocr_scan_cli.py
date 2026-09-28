"""Content-silent contained scan CLI; no PDF, images, engines or models."""

from pathlib import Path
import subprocess
import sys

import pytest

import ocr_scan_io
import process_supervision
from tools import inspect_ocr_scan as cli


PRIVATE = "PRIVATE-source-or-error"


@pytest.fixture
def seams(monkeypatch):
    seen = {"requests": [], "workers": [], "supervised": [], "readback": []}
    request = object()
    bundle = {"scan": {"page_count": 8, "requested_pages": [1, 6],
                       "pages": [{"page_number": 1}, {"page_number": 6}]},
              "manifest": {"requires_attention": True}}
    for name, key, result in (("scan_request", "requests", request), ("run_scan_bundle", "workers", bundle),
                              ("read_scan_completion", "readback", bundle)):
        def call(*args, _key=key, _result=result, **kwargs):
            seen[_key].append((args, kwargs))
            return _result
        monkeypatch.setattr(ocr_scan_io, name, call)
    def supervise(*args, **kwargs):
        seen["supervised"].append((args, kwargs))
        return 3
    monkeypatch.setattr(process_supervision, "_run_cli_with_deadline", supervise)
    monkeypatch.delenv("RAG_OCR_SCAN_CHILD", raising=False)
    return seen


def args():
    return [f"--pdf=-{PRIVATE}.pdf", f"--output-dir=-{PRIVATE}-output", "--pages", "6", "1"]


def test_default_parent_is_contained_and_reads_exact_request_before_summary(seams, capsys):
    assert cli.main(args() + ["--timeout-seconds", "17"]) == 3
    assert len(seams["requests"]) == len(seams["supervised"]) == len(seams["readback"]) == 1
    assert not seams["workers"]
    positional, options = seams["supervised"][0]
    assert positional == (Path(cli.__file__), [args()[0], args()[1], "--pages", "1", "6", "--worker"])
    assert options["timeout"] == 17.0
    assert options["stdout_target"] == options["stderr_target"] == subprocess.DEVNULL
    assert options["environment_overrides"] == {"RAG_OCR_SCAN_CHILD": "1", "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1", "DO_NOT_TRACK": "1", "PYTHONNOUSERSITE": "1"}
    assert options["config"].supervised_child_env == "RAG_OCR_SCAN_CHILD"
    assert seams["requests"][0][1] == {"requested_pages": [1, 6], "recovery_path": None,
                                      "proposals_path": None, "installation_path": None}
    output = capsys.readouterr()
    assert PRIVATE not in output.out + output.err and "2 explicitly selected pages of 8" in output.out
    assert "do not establish text completeness" in output.out


def test_optional_bound_inputs_forward_exactly_without_shell_interpretation(seams):
    extra = [f"--{key}=-{PRIVATE}-{key}.json" for key in ("recovery", "proposals", "installation")]
    assert cli.main(args() + extra) == 3
    command = seams["supervised"][0][0][1]
    assert command == [args()[0], args()[1], "--pages", "1", "6", *extra, "--worker"]
    assert seams["requests"][0][1]["installation_path"] == Path(f"-{PRIVATE}-installation.json")


@pytest.mark.parametrize("code", [0, 2, -9, 99, True, 3.0, 124.0, 130.0, 124, 130])
def test_only_expected_worker_exit_can_start_readback(seams, monkeypatch, capsys, code):
    monkeypatch.setattr(process_supervision, "_run_cli_with_deadline", lambda *_a, **_k: code)
    assert cli.main(args()) == (code if type(code) is int and code in (124, 130) else 2)
    assert not seams["readback"]
    output = capsys.readouterr()
    assert PRIVATE not in output.out + output.err and "remain" in output.err
    assert "Scan bundle verified" not in output.out


@pytest.mark.parametrize("boundary", ["parse", "request", "supervise", "readback", "summary"])
@pytest.mark.parametrize("error,code", [(ValueError(PRIVATE), 2), (KeyboardInterrupt(PRIVATE), 130)])
def test_all_boundaries_including_postpublication_cancel_are_sanitized(seams, monkeypatch, capsys, boundary, error, code):
    def fail(*_args, **_kwargs):
        raise error
    module, name = {"parse": (cli._Parser, "parse_args"), "request": (ocr_scan_io, "scan_request"),
                    "supervise": (process_supervision, "_run_cli_with_deadline"),
                    "readback": (ocr_scan_io, "read_scan_completion"), "summary": (cli, "_print_summary")}[boundary]
    monkeypatch.setattr(module, name, fail)
    assert cli.main(args()) == code
    output = capsys.readouterr()
    assert PRIVATE not in output.out + output.err and "remain" in output.err
    assert "Scan bundle verified" not in output.out


@pytest.mark.parametrize("extra", [["--timeout-seconds", "0"], ["--timeout-seconds", "3601"],
    ["--timeout-seconds", "NaN"], ["--timeout-seconds", "1.5"], ["--pages", "0"], ["--pages", "5001"],
    ["--pages", "1", "1"], ["--pages", *map(str, range(1, 10))], ["--pages", "1-3"],
    ["--dpi", "600"], [f"--proposal={PRIVATE}"], [f"--proposals={PRIVATE}"], [f"--unknown={PRIVATE}"]])
def test_bad_arguments_never_enter_admission_or_echo_content(seams, capsys, extra):
    assert cli.main(args() + extra) == 2
    assert not any(seams.values())
    output = capsys.readouterr()
    assert PRIVATE not in output.out + output.err


def test_missing_pages_is_not_implicit_whole_pdf(seams, capsys):
    assert cli.main(args()[:2]) == 2
    assert not any(seams.values()) and PRIVATE not in capsys.readouterr().err


def test_inherited_worker_marker_cannot_bypass_parent(seams, monkeypatch):
    monkeypatch.setenv("RAG_OCR_SCAN_CHILD", "1")
    assert cli.main(args()) == 3
    assert len(seams["supervised"]) == 1 and not seams["workers"]


def test_unguarded_direct_worker_refused(seams):
    assert cli.main(args() + ["--worker"]) == 2
    assert not any(seams.values())


def test_marked_worker_never_recurses_or_prints(seams, monkeypatch, capsys):
    monkeypatch.setenv("RAG_OCR_SCAN_CHILD", "1")
    assert cli.main(args() + ["--worker"]) == 3
    assert len(seams["workers"]) == 1 and not seams["requests"] and not seams["supervised"]
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("mutate", [lambda b: b["scan"].update(page_count=True),
    lambda b: b["scan"].update(requested_pages=[1, 1]), lambda b: b["scan"].update(requested_pages=[1, 9]),
    lambda b: b["scan"].update(pages=[{"page_number": 6}]), lambda b: b["manifest"].update(requires_attention=False)])
def test_invalid_summary_fails_before_any_success_print(capsys, mutate):
    bundle = {"scan": {"page_count": 8, "requested_pages": [1, 6],
                       "pages": [{"page_number": 1}, {"page_number": 6}]}, "manifest": {"requires_attention": True}}
    mutate(bundle)
    with pytest.raises(ValueError):
        cli._print_summary(bundle)
    assert capsys.readouterr().out == ""


def test_real_help_is_light_static_and_hides_worker():
    script = ("import runpy,sys; sys.argv=['PRIVATE','--help']; "
              "blocked={'pymupdf','fitz','numpy','cv2','rapidocr','onnxruntime','rag','ocr_scan_runtime'}; "
              "\ntry: runpy.run_path('tools/inspect_ocr_scan.py',run_name='__main__')"
              "\nexcept SystemExit as e: assert e.code==0"
              "\nassert not blocked.intersection(sys.modules)")
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0 and not result.stderr
    assert "--pages" in result.stdout and "--worker" not in result.stdout and "PRIVATE" not in result.stdout


def test_real_bad_input_is_content_free_and_has_no_output(tmp_path):
    output = tmp_path / (PRIVATE + "-output")
    result = subprocess.run([sys.executable, str(Path(cli.__file__)), f"--pdf={tmp_path / PRIVATE}",
                             f"--output-dir={output}", "--pages", "1"], capture_output=True, text=True, timeout=30)
    assert result.returncode == 2 and PRIVATE not in result.stdout + result.stderr
    assert not output.exists()


@pytest.mark.parametrize("recipe", ["legacy-v1", "spatial-v2"])
def test_recipe_selection_matches_parent_admission_and_worker_arguments(seams, recipe):
    assert cli.main(args() + ["--recipe", recipe]) == 3
    options = seams["requests"][0][1]
    arguments = seams["supervised"][0][0][1]
    if recipe == "legacy-v1":
        assert "recipe" not in options
        assert arguments == [args()[0], args()[1], "--pages", "1", "6", "--worker"]
    else:
        assert options["recipe"] == "spatial-v2"
        assert arguments == [args()[0], args()[1], "--pages", "1", "6", "--recipe=spatial-v2", "--worker"]
    assert len(seams["readback"]) == 1 and not seams["workers"]


@pytest.mark.parametrize("recipe", ["legacy-v1", "spatial-v2"])
def test_recipe_selection_reaches_contained_worker_api_without_recursion(seams, monkeypatch, capsys, recipe):
    monkeypatch.setenv("RAG_OCR_SCAN_CHILD", "1")
    assert cli.main(args() + ["--recipe", recipe, "--worker"]) == 3
    assert len(seams["workers"]) == 1 and not seams["supervised"] and not seams["readback"]
    assert seams["workers"][0][1].get("recipe", "legacy-v1") == recipe
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("recipe", ["legacy", "spatial-v1", "SPATIAL-V2", "spatial-v2 ", PRIVATE, ""])
def test_unknown_recipe_fails_before_admission_and_echoes_no_value(seams, capsys, recipe):
    assert cli.main(args() + [f"--recipe={recipe}"]) == 2
    assert not any(seams.values())
    output = capsys.readouterr()
    assert PRIVATE not in output.out + output.err and "Scan bundle verified" not in output.out


def test_recipe_help_lists_both_choices_without_optional_imports():
    script = ("import runpy,sys; sys.argv=['PRIVATE','--help']; "
              "blocked={'pymupdf','fitz','numpy','cv2','rapidocr','onnxruntime','rag','ocr_scan_runtime','ocr_scan_omission'}; "
              "\ntry: runpy.run_path('tools/inspect_ocr_scan.py',run_name='__main__')"
              "\nexcept SystemExit as e: assert e.code==0"
              "\nassert not blocked.intersection(sys.modules)")
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0 and not result.stderr
    assert "--recipe {legacy-v1,spatial-v2}" in result.stdout and "explicit opt-in" in result.stdout
