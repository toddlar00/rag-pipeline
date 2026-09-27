"""Inherited child startup selectors are scrubbed; explicit host policy remains.

The three real-child controls execute only generated standard-library scripts,
not OCR, rendering, model loading, browser code, or pipeline operations.
"""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import process_supervision as ps


SAFE = {"PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1", "PYTHONUTF8": "1"}
CONFIG = ps.SupervisionConfig("TEST_SUPERVISED_CHILD", "TEST_RUN_ID", 1., .01, 5.)


def captured_launch(monkeypatch, ambient, overrides=None):
    observed = {}

    class Gate:
        kind, child_value = "inert-gate", "7"
        released = closed = False

        def popen_options(self):
            return {}

        def release(self):
            self.released = True

        def close(self):
            self.closed = True

    class Process:
        def wait(self, timeout):
            return 0

    def popen(command, **options):
        observed.update(command=command, environment=options["env"])
        return Process()

    gate = Gate()
    original = dict(ambient)
    monkeypatch.setattr(ps.os, "environ", ambient)
    monkeypatch.setattr(ps.subprocess, "Popen", popen)
    result = ps._run_cli_with_deadline(
        Path("synthetic-child.py"), ["argument"], operation="environment-control",
        timeout=1., config=CONFIG, environment_overrides=overrides,
        kill_job_factory=lambda: None, start_gate_factory=lambda: gate,
        terminate_fn=lambda *_args, **_kwargs: True, run_id="fixed-run")
    assert result == 0 and gate.released and gate.closed
    assert ambient == original and observed["environment"] is not ambient
    assert observed["command"][-2:] == [str(Path("synthetic-child.py").resolve()), "argument"]
    assert observed["environment"][CONFIG.supervised_child_env] == "1"
    assert observed["environment"][CONFIG.run_id_env] == "fixed-run"
    return observed["environment"]


@pytest.mark.parametrize("name", [
    "PYTHONHOME", "PYTHONPATH", "pYtHoNoPtImIzE", "PYTHONHASHSEED",
    "_PYTHON_SYSCONFIGDATA_NAME", "_pYtHoN_future_selector",
    "__PyVENV_LAUNCHER__", "pYtHoNdOnTwRiTeByTeCoDe", "PYTHONNOUSERSITE", "pythonutf8",
])
def test_inherited_startup_selectors_are_removed_case_insensitively(monkeypatch, name):
    environment = captured_launch(monkeypatch, {name: "untrusted", "KEEP": "unchanged"})
    assert environment["KEEP"] == "unchanged"
    assert {key: environment.get(key) for key in SAFE} == SAFE
    selector_names = {key for key in environment
                      if key.upper().startswith(("PYTHON", "_PYTHON"))
                      or key.upper() == "__PYVENV_LAUNCHER__"}
    assert selector_names <= set(SAFE) | {"__PYVENV_LAUNCHER__"}
    if "__PYVENV_LAUNCHER__" in environment:
        assert sys.platform == "win32"
        assert environment["__PYVENV_LAUNCHER__"] == sys.executable


def test_trusted_overrides_remain_explicit_and_detached(monkeypatch):
    ambient = {"PYTHONPATH": "ambient-untrusted", "KEEP": "parent", "REMOVE": "parent"}
    overrides = {"PYTHONPATH": "host-owned-import-root", "PYTHONHASHSEED": "0",
                 "PYTHONUTF8": "0", "KEEP": "child", "REMOVE": None}
    original_overrides = dict(overrides)
    environment = captured_launch(monkeypatch, ambient, overrides)
    assert environment["PYTHONPATH"] == "host-owned-import-root"
    assert environment["PYTHONHASHSEED"] == "0"
    assert environment["PYTHONUTF8"] == "0"
    assert environment["KEEP"] == "child" and "REMOVE" not in environment
    assert environment["PYTHONNOUSERSITE"] == environment["PYTHONDONTWRITEBYTECODE"] == "1"
    assert overrides == original_overrides


@pytest.mark.parametrize("selector", ["path", "optimize", "home"])
def test_actual_harmless_child_ignores_inherited_startup_selectors(tmp_path, monkeypatch, selector):
    for name in tuple(os.environ):
        if name.upper().startswith(("PYTHON", "_PYTHON")) or name.upper() == "__PYVENV_LAUNCHER__":
            monkeypatch.delenv(name)
    injected = tmp_path / "ambient-imports"
    injected.mkdir()
    (injected / "startup_only_probe.py").write_text("VALUE = 'inert'\n", encoding="utf-8")
    selected = {"path": ("PYTHONPATH", str(injected)), "optimize": ("PYTHONOPTIMIZE", "2"),
                "home": ("PYTHONHOME", str(tmp_path / "not-a-python-home"))}
    name, value = selected[selector]
    monkeypatch.setenv(name, value)
    parent_environment = dict(os.environ)
    marker = tmp_path / "observed.json"
    script = tmp_path / "child.py"
    script.write_text(
        "import importlib.util,json,sys\nfrom pathlib import Path\n"
        "value = dict(optimize=sys.flags.optimize, no_user_site=sys.flags.no_user_site, "
        "dont_write_bytecode=sys.dont_write_bytecode, utf8_mode=sys.flags.utf8_mode, "
        "probe_visible=importlib.util.find_spec('startup_only_probe') is not None, "
        "executable=sys.executable, prefix=sys.prefix, base_prefix=sys.base_prefix)\n"
        "Path(sys.argv[1]).write_text(json.dumps(value), encoding='utf-8')\n",
        encoding="utf-8")
    result = ps._run_cli_with_deadline(
        script, [str(marker)], operation="stdlib-environment-control", timeout=10., config=CONFIG,
        stdout_target=subprocess.DEVNULL, stderr_target=subprocess.DEVNULL)
    assert result == 0, "generated standard-library child did not start successfully"
    observed = json.loads(marker.read_text(encoding="utf-8"))
    assert observed == {"optimize": 0, "no_user_site": 1, "dont_write_bytecode": True,
                        "utf8_mode": 1, "probe_visible": False, "executable": sys.executable,
                        "prefix": sys.prefix, "base_prefix": sys.base_prefix}
    assert dict(os.environ) == parent_environment
    assert not tuple(tmp_path.rglob("*.pyc"))


class _ParentStream:
    """A parent stdio stand-in with an OS descriptor and observable flushes."""

    def __init__(self, events, name):
        self.events, self.name = events, name

    def fileno(self):
        return 1

    def flush(self):
        self.events.append(f"flush:{self.name}")


def _windows_launch_options(monkeypatch, *, console_attached, stdout, stderr):
    observed = {}
    events = []

    class Gate:
        kind, child_value = "inert-gate", "7"

        def popen_options(self):
            return {}

        def release(self):
            pass

        def close(self):
            pass

    class Process:
        def wait(self, timeout):
            return 0

    def popen(command, **options):
        events.append("popen")
        observed.update(options)
        return Process()

    streams = {
        name: _ParentStream(events, name) if value == "usable" else value
        for name, value in (("stdout", stdout), ("stderr", stderr))
    }
    monkeypatch.setattr(ps, "_windows_console_attached", lambda: console_attached)
    monkeypatch.setattr(ps.subprocess, "Popen", popen)
    monkeypatch.setattr(ps.sys, "stdout", streams["stdout"])
    monkeypatch.setattr(ps.sys, "stderr", streams["stderr"])
    assert ps._run_cli_with_deadline(
        Path("synthetic-child.py"), [], operation="console-control", timeout=1.,
        config=CONFIG, kill_job_factory=lambda: None, start_gate_factory=Gate,
        terminate_fn=lambda *_args, **_kwargs: True) == 0
    return observed, streams, events


@pytest.mark.skipif(os.name != "nt", reason="Windows console behavior")
@pytest.mark.parametrize("console_attached", [False, True])
def test_worker_is_windowless_only_when_parent_has_no_console(monkeypatch, console_attached):
    observed, streams, _events = _windows_launch_options(
        monkeypatch, console_attached=console_attached,
        stdout="usable", stderr="usable")

    windowless = bool(observed["creationflags"] & subprocess.CREATE_NO_WINDOW)
    assert observed["creationflags"] & subprocess.CREATE_NEW_PROCESS_GROUP
    assert windowless is not console_attached
    # A console parent's worker shares that console and its handles; a
    # windowless worker needs the parent's streams passed explicitly.
    if windowless:
        assert observed["stdout"] is streams["stdout"]
        assert observed["stderr"] is streams["stderr"]
    else:
        assert "stdout" not in observed and "stderr" not in observed


@pytest.mark.skipif(os.name != "nt", reason="Windows console behavior")
def test_windowless_worker_discards_an_unusable_parent_stream(monkeypatch):
    observed, streams, _events = _windows_launch_options(
        monkeypatch, console_attached=False, stdout="usable", stderr=None)

    # Leaving the slot empty lets Popen substitute a pipe with no reader.
    assert observed["stdout"] is streams["stdout"]
    assert observed["stderr"] == subprocess.DEVNULL


@pytest.mark.skipif(os.name != "nt", reason="Windows console behavior")
def test_windowless_worker_flushes_handed_over_parent_streams(monkeypatch):
    _observed, _streams, events = _windows_launch_options(
        monkeypatch, console_attached=False, stdout="usable", stderr="usable")

    assert events.index("flush:stdout") < events.index("popen")
    assert events.index("flush:stderr") < events.index("popen")


def test_actual_default_worker_output_reaches_parent_streams(
        tmp_path, capfd, monkeypatch):
    if os.name == "nt":
        # Exercise the console-less launch that previously lost all output.
        monkeypatch.setattr(ps, "_windows_console_attached", lambda: False)
    script = tmp_path / "output_probe.py"
    script.write_text(
        "import sys\nprint('worker-stdout-marker')\n"
        "print('worker-stderr-marker', file=sys.stderr)\n",
        encoding="utf-8")
    result = ps._run_cli_with_deadline(
        script, [], operation="output-control", timeout=10., config=CONFIG)
    captured = capfd.readouterr()
    assert result == 0
    assert "worker-stdout-marker" in captured.out
    assert "worker-stderr-marker" in captured.err


@pytest.mark.skipif(os.name != "nt", reason="Windows console behavior")
def test_actual_supervised_worker_never_opens_a_new_console_window(tmp_path):
    import ctypes

    get_console = ctypes.windll.kernel32.GetConsoleWindow
    get_console.restype = ctypes.c_void_p
    parent_console = int(get_console() or 0)
    marker = tmp_path / "console-window.json"
    script = tmp_path / "console_probe.py"
    script.write_text(
        "import ctypes,json,sys\nfrom pathlib import Path\n"
        "get_console = ctypes.windll.kernel32.GetConsoleWindow\n"
        "get_console.restype = ctypes.c_void_p\n"
        "Path(sys.argv[1]).write_text(json.dumps({\"console\": "
        "int(get_console() or 0)}), encoding='utf-8')\n",
        encoding="utf-8")
    result = ps._run_cli_with_deadline(
        script, [str(marker)], operation="hidden-console-control", timeout=10.,
        config=CONFIG, stdout_target=subprocess.DEVNULL,
        stderr_target=subprocess.DEVNULL)
    assert result == 0
    # Windowless without a parent console; otherwise the parent's console.
    assert json.loads(marker.read_text(encoding="utf-8")) == {"console": parent_console}
