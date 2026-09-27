"""Runtime metadata agreement must never be confused with artifact attestation."""

import copy
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import types

import pytest

import ocr_experiment_runtime as runtime


ENVIRONMENT = {"python_full_version": "3.12.4", "python_version": "3.12", "os_name": "nt",
               "sys_platform": "win32", "platform_machine": "AMD64", "platform_python_implementation": "CPython"}


def lock_bytes(*requirements):
    return ("# synthetic lock\n--index-url https://example.invalid/simple\n" + "\n".join(
        line + " \\\n    --hash=sha256:" + "a" * 64 for line in requirements) + "\n").encode()


@pytest.fixture
def lock(tmp_path, monkeypatch):
    path = tmp_path / "requirements.lock"
    path.write_bytes(lock_bytes("sample==1.2.3"))
    monkeypatch.setattr(runtime, "_environment", lambda: dict(ENVIRONMENT))
    monkeypatch.setattr(importlib.metadata, "version", lambda name: "1.2.3")
    return path


def evidence():
    return {"engine": {"name": "rapidocr", "version": "3.9.2"},
            "execution_providers": ["CPUExecutionProvider"],
            "thread_settings": {"intra_op": 2, "inter_op": 1}, "elapsed_seconds": 1.25,
            "model_artifacts": [{"id": "recognizer", "sha256": "b" * 64}]}


def capture(path, **kwargs):
    return runtime.capture_runtime_manifest(path, packages=("sample",), **kwargs)


def test_matching_versions_never_imply_locked_artifacts(lock):
    result = capture(lock, require_version_match=True)
    assert result["package_versions_match"]
    assert not result["locked_environment_verified"]
    assert result["requires_attention"]
    assert result["effective_runtime"] is None
    assert result["effective_runtime_source"] is None
    assert result["artifact_verification"] == {"package_wheels": "not_verified",
                                               "model_files": "not_verified_by_capture",
                                               "loaded_native_libraries": "not_verified"}
    assert result["lock_sha256"] == hashlib.sha256(lock.read_bytes()).hexdigest()
    assert result["packages"][0]["loaded_version_agrees"] is None
    assert result["version_check_scope"] == "requested_packages_only"


def test_effective_runtime_is_detached_caller_observation_not_attestation(lock):
    supplied = evidence()
    saved = copy.deepcopy(supplied)
    result = capture(lock, effective_runtime=supplied)
    assert result["effective_runtime"] == supplied == saved
    assert result["effective_runtime_source"] == "caller_observation"
    assert not result["locked_environment_verified"]
    result["effective_runtime"]["thread_settings"]["intra_op"] = 8
    result["effective_runtime"]["model_artifacts"].clear()
    assert supplied == saved


@pytest.mark.parametrize("installed", ["1.2.2", "1.2.3+other", "9.0"])
def test_version_mismatch_is_visible_and_strict_gate_refuses(lock, monkeypatch, installed):
    monkeypatch.setattr(importlib.metadata, "version", lambda name: installed)
    result = capture(lock)
    assert not result["package_versions_match"]
    assert result["packages"][0]["status"] == "version_mismatch"
    with pytest.raises(ValueError, match="do not match"):
        capture(lock, require_version_match=True)


@pytest.mark.parametrize("error", [importlib.metadata.PackageNotFoundError("PRIVATE-PACKAGE"),
                                   RuntimeError("PRIVATE-PATH"), ValueError("PRIVATE-TEXT")])
def test_missing_or_broken_install_metadata_is_not_success_or_leaked(lock, monkeypatch, error):
    def fail(name):
        raise error
    monkeypatch.setattr(importlib.metadata, "version", fail)
    result = capture(lock)
    assert not result["package_versions_match"]
    assert result["packages"][0]["status"] == "not_installed_or_unreadable"
    assert "PRIVATE" not in json.dumps(result)


@pytest.mark.parametrize("installed", ["PRIVATE/path", "1.0\nPRIVATE", "", None])
def test_invalid_metadata_version_is_redacted(lock, monkeypatch, installed):
    monkeypatch.setattr(importlib.metadata, "version", lambda name: installed)
    result = capture(lock)
    assert result["packages"][0]["installed_version"] is None
    assert not result["package_versions_match"]
    assert "PRIVATE" not in json.dumps(result)


def test_already_loaded_version_disagreement_overrides_metadata_match(lock, monkeypatch):
    monkeypatch.setitem(sys.modules, "sample", types.SimpleNamespace(__version__="0.9"))
    result = capture(lock)
    assert result["packages"][0]["status"] == "version_match"
    assert result["packages"][0]["loaded_version_agrees"] is False
    assert not result["package_versions_match"]
    with pytest.raises(ValueError, match="do not match"):
        capture(lock, require_version_match=True)


def test_already_loaded_version_match_is_observed_without_import(lock, monkeypatch):
    monkeypatch.setitem(sys.modules, "sample", types.SimpleNamespace(__version__="1.2.3"))
    result = capture(lock)
    assert result["packages"][0]["loaded_versions"] == ["1.2.3"]
    assert result["packages"][0]["loaded_version_agrees"] is True
    assert result["package_versions_match"]
    assert not result["locked_environment_verified"]


@pytest.mark.parametrize("loaded,agrees", [("4.13.0", True), ("4.13", False), ("4.12.0", False)])
def test_opencv_distribution_build_suffix_is_explicit(lock, monkeypatch, loaded, agrees):
    lock.write_bytes(lock_bytes("opencv-python==4.13.0.92"))
    monkeypatch.setattr(importlib.metadata, "version", lambda name: "4.13.0.92")
    monkeypatch.setitem(sys.modules, "cv2", types.SimpleNamespace(__version__=loaded))
    result = runtime.capture_runtime_manifest(lock, packages=["opencv-python"])
    assert result["packages"][0]["loaded_version_agrees"] is agrees


@pytest.mark.parametrize("expression,expected", [
    ("", True), ("python_full_version < '3.11'", False), ("python_full_version >= '3.12'", True),
    ("python_full_version == '3.12.*'", True), ("python_full_version != '3.12.*'", False),
    ("python_full_version == '3.12.4.0'", True), ("python_version == '3.12'", True),
    ("python_version > '3.9'", True), ("python_version <= '3.12'", True),
    ("sys_platform == 'win32'", True), ("sys_platform != 'darwin'", True),
    ("platform_machine == 'aarch64' or platform_machine == 'AMD64'", True),
    ("os_name != 'nt' and sys_platform == 'win32' or os_name == 'nt'", True),
    ("(os_name != 'nt' and sys_platform == 'win32') or (os_name == 'nt' and sys_platform != 'darwin')", True),
    ("platform_python_implementation != 'PyPy' and sys_platform != 'win32'", False),
])
def test_supported_marker_semantics(expression, expected):
    assert runtime._marker_applies(expression, ENVIRONMENT) is expected


@pytest.mark.parametrize("expression", [
    "unknown == 'value'", "python_version in '3.12'", "sys_platform >= 'win32'",
    "python_full_version >= '3.12.*'", "python_full_version == '3.12rc1'",
    "__import__('os')", "sys_platform == 'win32' or unknown == 'x'",
    "os_name != 'nt' and unknown == 'x'", "(os_name == 'nt'", "os_name == 'nt')",
    "os_name == 'nt' garbage", "True", "'nt' == os_name", "os_name == 'nt' & True",
    "(" * 33 + "os_name == 'nt'" + ")" * 33,
], ids=["variable", "in", "string-order", "wildcard-order", "prerelease", "code", "no-or-shortcut",
        "no-and-shortcut", "open-paren", "close-paren", "trailing", "bare", "reverse", "ampersand", "depth"])
def test_unsupported_markers_fail_closed(expression):
    with pytest.raises(ValueError):
        runtime._marker_applies(expression, ENVIRONMENT)


def test_current_lock_markers_all_resolve_without_third_party_parser():
    raw = (Path(__file__).resolve().parents[1] / "requirements-core.lock").read_bytes()
    parsed = runtime._lock_records(raw)
    assert "rapidocr" in parsed
    for entries in parsed.values():
        for _, expression in entries:
            assert type(runtime._marker_applies(expression, ENVIRONMENT)) is bool


def test_marker_selects_one_environment_pin(lock):
    lock.write_bytes(lock_bytes("sample==1.0 ; python_full_version < '3.12'",
                                "sample==1.2.3 ; python_full_version >= '3.12'"))
    result = capture(lock)
    assert result["packages"][0]["required_version"] == "1.2.3"
    assert result["package_versions_match"]


@pytest.mark.parametrize("requirements,status", [
    (["other==1.2.3"], "not_locked_for_environment"),
    (["sample==1.2.3 ; sys_platform == 'linux'"], "not_locked_for_environment"),
    (["sample==1.2.3", "sample==1.2.3"], "ambiguous_pin"),
    (["sample==1.2.3", "sample==2.0"], "ambiguous_pin"),
    (["sample==1.2.3 ; extra == 'test'"], "unsupported_marker"),
    (["sample==1.2.3", "sample==2.0 ; extra == 'test'"], "unsupported_marker"),
])
def test_missing_ambiguous_and_unsupported_lock_pins_are_not_success(lock, requirements, status):
    lock.write_bytes(lock_bytes(*requirements))
    result = capture(lock)
    assert not result["package_versions_match"]
    assert result["packages"][0]["status"] == status
    with pytest.raises(ValueError):
        capture(lock, require_version_match=True)


@pytest.mark.parametrize("raw", [b"", b"\xff", b"sample==1.2.3\n", b"sample==1.2.3 \\",
                                b"-r PRIVATE-PATH\n", b"sample>=1.2.3 --hash=sha256:" + b"a" * 64,
                                b"sample==1.2.3 --hash=sha256:incorrect", b"sample @ https://PRIVATE.invalid\n"],
                         ids=["empty", "encoding", "unhashed", "incomplete", "include", "range", "hash", "url"])
def test_malformed_locks_fail_redacted(lock, raw):
    lock.write_bytes(raw)
    with pytest.raises(ValueError) as error:
        capture(lock)
    assert "PRIVATE" not in str(error.value)


@pytest.mark.parametrize("packages", [[], ["sample", "Sample"], ["a_b", "a-b"], ["PRIVATE/path"], "sample", ["a"] * 257])
def test_package_selection_is_bounded_normalized_and_strict(lock, packages):
    with pytest.raises(ValueError):
        runtime.capture_runtime_manifest(lock, packages=packages)


@pytest.mark.parametrize("flag", [None, 0, 1, "true"])
def test_gate_flag_requires_boolean(lock, flag):
    with pytest.raises(ValueError):
        capture(lock, require_version_match=flag)


@pytest.mark.parametrize("mutate", [
    lambda v: v.update(extra="PRIVATE"),
    lambda v: v["engine"].update(path="PRIVATE"),
    lambda v: v["engine"].update(version="PRIVATE/path"),
    lambda v: v.update(execution_providers=["CPUExecutionProvider"] * 2),
    lambda v: v.update(execution_providers=["PRIVATE/path"]),
    lambda v: v["thread_settings"].update(intra_op=True),
    lambda v: v["thread_settings"].update(inter_op=257),
    lambda v: v.update(thread_settings={}),
    lambda v: v.update(elapsed_seconds=True),
    lambda v: v.update(elapsed_seconds=float("nan")),
    lambda v: v.update(elapsed_seconds=float("inf")),
    lambda v: v.update(elapsed_seconds=10 ** 1000),
    lambda v: v.update(elapsed_seconds=-1),
    lambda v: v.update(model_artifacts=[{"id": "x", "sha256": "A" * 64}]),
    lambda v: v["model_artifacts"].append(copy.deepcopy(v["model_artifacts"][0])),
    lambda v: v["model_artifacts"][0].update(verified=True),
], ids=["unknown", "engine-path", "version-path", "duplicate-provider", "provider-path", "bool-threads", "large-threads",
        "missing-threads", "bool-elapsed", "nan", "infinity", "huge-int", "negative", "digest", "duplicate-artifact", "asserted-proof"])
def test_effective_evidence_is_strict_bounded_and_redacted(lock, mutate):
    supplied = evidence()
    mutate(supplied)
    with pytest.raises(ValueError) as error:
        capture(lock, effective_runtime=supplied)
    assert "PRIVATE" not in str(error.value)


def test_absent_thread_counts_and_zero_duration_are_not_fabricated(lock):
    supplied = evidence()
    supplied.update(thread_settings={"intra_op": None, "inter_op": None}, elapsed_seconds=0,
                    execution_providers=[], model_artifacts=[])
    assert capture(lock, effective_runtime=supplied)["effective_runtime"] == supplied


def test_lock_mutation_during_metadata_capture_fails(lock, monkeypatch):
    def mutate(name):
        lock.write_bytes(lock_bytes("sample==2.0"))
        return "1.2.3"
    monkeypatch.setattr(importlib.metadata, "version", mutate)
    with pytest.raises(ValueError, match="changed"):
        capture(lock)


def test_lock_bounds_and_read_errors_are_redacted(lock, monkeypatch):
    monkeypatch.setattr(runtime, "MAX_LOCK_BYTES", 8)
    with pytest.raises(ValueError, match="safely read"):
        capture(lock)
    with pytest.raises(ValueError) as error:
        capture(lock.parent / "PRIVATE-PATH.lock")
    assert "PRIVATE" not in str(error.value)


def test_symlink_lock_is_rejected(lock):
    linked = lock.parent / "linked.lock"
    try:
        linked.symlink_to(lock)
    except OSError:
        pytest.skip("symlinks are unavailable")
    with pytest.raises(ValueError, match="safely read"):
        capture(linked)


def test_hardlinked_lock_is_rejected(lock):
    linked = lock.parent / "linked.lock"
    try:
        os.link(lock, linked)
    except OSError:
        pytest.skip("hardlinks are unavailable")
    with pytest.raises(ValueError, match="safely read"):
        capture(linked)


def test_result_is_path_free_and_capture_does_not_write(lock):
    before = {path.name: path.read_bytes() for path in lock.parent.iterdir()}
    result = json.dumps(capture(lock, effective_runtime=evidence()))
    assert str(lock) not in result
    assert str(lock.parent) not in result
    assert "https://" not in result
    assert {path.name: path.read_bytes() for path in lock.parent.iterdir()} == before


def test_imports_do_not_load_optional_engines():
    code = ("import sys; import ocr_benchmark; import ocr_experiment_runtime; "
            "assert not ({'fitz','pymupdf','cv2','numpy','rapidocr','onnxruntime','torch'} & set(sys.modules))")
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                            cwd=Path(__file__).resolve().parents[1], timeout=20)
    assert result.returncode == 0, result.stderr
