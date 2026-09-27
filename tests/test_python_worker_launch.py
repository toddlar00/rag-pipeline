"""Same-interpreter launch must preserve the process registered by its owner."""

from types import SimpleNamespace
import importlib.util
import json
import os
import subprocess
import sys

import pytest

import process_supervision


def runtime(monkeypatch, *, platform="win32", implementation="cpython", frozen=False,
            executable="C:/env/Scripts/python.exe", base="C:/Python/python.exe"):
    monkeypatch.setattr(process_supervision, "sys", SimpleNamespace(
        platform=platform, implementation=SimpleNamespace(name=implementation), frozen=frozen,
        executable=executable, _base_executable=base))


def test_windows_venv_bypasses_redirector_without_changing_callers(monkeypatch):
    runtime(monkeypatch)
    arguments = ["-u", "C:/A folder/worker.py", "value with spaces", "é"]
    environment = {"KEEP": "value", "__pyvenv_launcher__": "untrusted"}
    command, child_environment = process_supervision.python_worker_launch(arguments, environment)
    assert command == ["C:/Python/python.exe", *arguments]
    assert child_environment == {"KEEP": "value", "__PYVENV_LAUNCHER__": "C:/env/Scripts/python.exe"}
    assert arguments == ["-u", "C:/A folder/worker.py", "value with spaces", "é"]
    assert environment == {"KEEP": "value", "__pyvenv_launcher__": "untrusted"}


def test_windows_base_python_does_not_inherit_a_launcher_override(monkeypatch):
    runtime(monkeypatch, executable="C:/PYTHON/python.exe", base="c:\\python\\python.exe")
    command, environment = process_supervision.python_worker_launch(["worker.py"], {
        "__PYVENV_LAUNCHER__": "wrong", "__pyvenv_launcher__": "also-wrong", "KEEP": "value"})
    assert command == ["C:/PYTHON/python.exe", "worker.py"]
    assert environment == {"KEEP": "value"}


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_other_platforms_preserve_launch_semantics(monkeypatch, platform):
    runtime(monkeypatch, platform=platform, executable="/env/bin/python", base="/usr/bin/python")
    original = {"__PYVENV_LAUNCHER__": "retained-platform-value"}
    command, environment = process_supervision.python_worker_launch(["-u", "worker.py"], original)
    assert command == ["/env/bin/python", "-u", "worker.py"]
    assert environment == original and environment is not original


@pytest.mark.parametrize("options", [{"implementation": "pypy"}, {"frozen": True}])
def test_non_cpython_or_frozen_windows_runtime_is_not_redirected(monkeypatch, options):
    runtime(monkeypatch, **options)
    command, environment = process_supervision.python_worker_launch(
        ["worker.py"], {"KEEP": "value", "__PyVENV_LAUNCHER__": "untrusted"})
    assert command == ["C:/env/Scripts/python.exe", "worker.py"]
    assert environment == {"KEEP": "value"}


@pytest.mark.parametrize("base", [None, "", "relative-python.exe", "\\root-relative.exe", 7,
                                 "C:relative.exe", "C:/Python/py\0thon.exe", True])
def test_unknown_windows_base_interpreter_fails_closed(monkeypatch, base):
    runtime(monkeypatch, base=base)
    with pytest.raises(RuntimeError, match="base interpreter"):
        process_supervision.python_worker_launch(["worker.py"], {})


def test_real_worker_pid_and_package_environment_match_parent():
    source = (
        "import importlib.util,json,os,sys; "
        "print(json.dumps(dict(pid=os.getpid(), executable=sys.executable, "
        "prefix=sys.prefix, base_prefix=sys.base_prefix, "
        "pytest_origin=importlib.util.find_spec('pytest').origin)))"
    )
    parent_environment = dict(os.environ)
    supplied = dict(parent_environment)
    if sys.platform == "win32":
        supplied["__PyVENV_LAUNCHER__"] = "untrusted-not-an-interpreter"
    command, environment = process_supervision.python_worker_launch(["-c", source], supplied)
    with subprocess.Popen(command, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
        try:
            stdout, stderr = process.communicate(timeout=10)
        except BaseException:
            process.kill()
            process.communicate(timeout=10)
            raise
        assert process.returncode == 0 and not stderr
        result = json.loads(stdout)
        assert result == {
            "pid": process.pid, "executable": sys.executable,
            "prefix": sys.prefix, "base_prefix": sys.base_prefix,
            "pytest_origin": importlib.util.find_spec("pytest").origin,
        }
        assert dict(os.environ) == parent_environment
