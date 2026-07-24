# Process Supervision Extraction Implementation Plan

> [!IMPORTANT]
> **Historical plan — implemented and superseded.** The implementation shipped
> in draft [PR #33](https://github.com/toddlar00/rag-pipeline/pull/33) and is
> represented in the current stack and
> [ROADMAP.md](../../../ROADMAP.md). The unchecked boxes, branch instructions,
> source line numbers, baselines, and expected test counts below preserve the
> original execution record; they are not active work instructions. Use the
> maintained [process-supervision ADR](../../architecture/decisions/process-supervision-extraction.md)
> for the current boundary and re-survey the code before any follow-up change.

> **Historical execution instruction:** The original plan required
> `superpowers:subagent-driven-development` or `superpowers:executing-plans` and
> task-by-task execution. That tool-specific instruction is retained only as
> provenance for how this plan was intended to be run.

**Goal:** Move the deadline-supervision core out of `rag.py` into a new
stdlib-only `process_supervision.py` with identical observable behavior, as
recorded in the maintained
[process-supervision ADR](../../architecture/decisions/process-supervision-extraction.md).

**Architecture:** The containment classes, terminate logic, deadline loop, and entrypoint runner move verbatim-or-parameterized into `process_supervision.py`. `rag.py` keeps its constants and telemetry emission and rebinds every old private name with **late-binding facade wrappers** (`def` bodies that resolve collaborators from rag module globals at call time), so every existing `monkeypatch.setattr(rag, ...)` in the characterization suite keeps working. Consumers (`job_manager.py`, `service_runtime.py`, `eval.py`, `supervised_worker.py`) require zero edits.

**Tech Stack:** Python stdlib only in the new module (`os`, `sys`, `signal`, `subprocess`, `threading`, `time`, `dataclasses`, `pathlib`, `typing`).

## Global Constraints

- Strictly behavior-preserving: defects found during extraction are recorded in `ROADMAP.md` as follow-up, never fixed here.
- `process_supervision.py` is stdlib-only — it must not import `run_telemetry`, `cli_policy`, or `rag`.
- Zero edits to `job_manager.py`, `service_runtime.py`, `supervised_worker.py`, `eval.py`, and `tests/test_process_supervision.py` (the characterization suite must pass unchanged).
- All moved names keep their exact current spelling (`_WindowsKillJob`, `_terminate_supervised_process`, ...) inside the new module; `rag.py` re-exports/rebinds the same names.
- Facade wrappers must late-bind: resolve `_WindowsKillJob`, `_terminate_supervised_process`, `_SUPERVISED_TERMINATE_GRACE`, `main`, `_run_cli_with_deadline` etc. from rag globals **inside the wrapper body**, never via `functools.partial` at import time. The characterization tests monkeypatch all of these on `rag`.
- Flat repo layout; new tests go in `tests/test_process_supervision_module.py` (direct module tests, no real subprocesses, injected clock).
- Baseline: post-merge `main` containing PR #31. Line numbers below are from that tree (`rag.py` @ `2d84abf`); the supervision region is byte-identical to pre-PR-#31 `main` (verified: the Ethics diff touches zero supervision names).
- Full suite + Ruff + compileall + `git diff --check` green before every commit claim; CI runs Linux + Windows.

## Source inventory (rag.py @ 2d84abf)

| Lines | Symbol | Disposition |
|---|---|---|
| 95–99 | `_SUPERVISED_CHILD_ENV`, `_RUN_ID_ENV`, `_SUPERVISED_TERMINATE_GRACE`, `_SUPERVISED_POLL_INTERVAL`, `_SUPERVISED_START_GATE_TIMEOUT` | **Stay** in rag.py (fed into `SupervisionConfig` at call time) |
| 11443–11457 | `_normalize_operation_timeout`, `_cli_operation_timeout` | **Stay** (CLI policy delegates) |
| 11459–11617 | `_WindowsKillJob` | **Move verbatim** (one default-arg change, Task 1) |
| 11620–11660 | `_PosixSupervisedStartGate` | **Move verbatim** |
| 11663–11720 | `_WindowsSupervisedStartGate` | **Move verbatim** |
| 11723–11725 | `_new_supervised_start_gate` | **Move verbatim** |
| 11728–11733 | `_SupervisorSignal` | **Move verbatim** |
| 11736–11737 | `_SupervisorCleanupError` | **Move verbatim** |
| 11740–11861 | `_terminate_supervised_process` | **Move, parameterized** (grace/clock/sleep — Task 1) |
| 11864–11868 | `_supervised_telemetry_requested` | **Stay** (telemetry policy) |
| 11871–11887 | `_finalize_supervised_run_telemetry` | **Stay** (telemetry emission) |
| 11890–12117 | `_run_cli_with_deadline` | **Move, parameterized** (config + hooks + factories — Task 2) |
| 12120–12121 | `_rag_cli_command`, `_cli_run_telemetry_options` aliases | **Stay** |
| 12124–12173 | `_run_rag_entrypoint` | **Move as generic `_run_supervised_entrypoint`** (Task 3); rag keeps a thin `_run_rag_entrypoint` wrapper |

Consumers verified (attribute lookups on `rag` at call time — all keep working through the facade): `job_manager.py:847,997`, `service_runtime.py:466,474`, `eval.py` (`rag._run_cli_with_deadline`), `rag.py` `__main__` block, `interactive_menu`.

Monkeypatch matrix the facade must honor (from `tests/test_process_supervision.py`):

| Patched name | Mechanism that keeps it working |
|---|---|
| `rag._WindowsKillJob` | wrapper passes `kill_job_factory=_WindowsKillJob` (rag global, resolved per call) |
| `rag._terminate_supervised_process` | wrapper passes `terminate_fn=_terminate_supervised_process` (rag global, per call) |
| `rag._SUPERVISED_TERMINATE_GRACE` | wrapper builds `SupervisionConfig` + terminate wrapper reads rag global per call |
| `rag.subprocess.Popen` | same stdlib module object in both files — works automatically |
| `rag._run_cli_with_deadline` | entrypoint wrapper passes `supervisor_fn=_run_cli_with_deadline` (rag global, per call) |
| `rag.main` | entrypoint wrapper passes `main_fn=main` (rag global, per call) |
| `rag._run_telemetry.new_run_id` | attribute on shared `run_telemetry` module object, resolved per call |

---

### Task 1: Module skeleton — config, containment, gates, terminate

**Files:**
- Create: `process_supervision.py`
- Create: `tests/test_process_supervision_module.py`

**Interfaces:**
- Produces: `SupervisionConfig(supervised_child_env: str, run_id_env: str, terminate_grace: float, poll_interval: float, start_gate_timeout: float)` frozen dataclass; `_WindowsKillJob`, `_PosixSupervisedStartGate`, `_WindowsSupervisedStartGate`, `_new_supervised_start_gate()`, `_SupervisorSignal`, `_SupervisorCleanupError`; `_terminate_supervised_process(process, *, kill_job=None, terminate_grace=_DEFAULT_TERMINATE_GRACE, monotonic=time.monotonic, sleep_fn=time.sleep) -> bool`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_process_supervision_module.py`:

```python
"""Direct unit tests for the extracted process_supervision module.

These tests never launch real subprocesses; they exercise the injected
collaborator seams with fakes and a deterministic clock.  Real-process
characterization lives unchanged in tests/test_process_supervision.py.
"""

import dataclasses
import os
import subprocess
from pathlib import Path

import pytest

import process_supervision as ps


class FakeClock:
    def __init__(self, start: float = 100.0):
        self.now = start
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _config(**overrides) -> ps.SupervisionConfig:
    values = {
        "supervised_child_env": "TEST_SUPERVISED_CHILD",
        "run_id_env": "TEST_RUN_ID",
        "terminate_grace": 5.0,
        "poll_interval": 0.2,
        "start_gate_timeout": 60.0,
    }
    values.update(overrides)
    return ps.SupervisionConfig(**values)


def test_supervision_config_is_frozen():
    config = _config()
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.terminate_grace = 1.0


def test_terminate_kill_job_branch_confirms_with_injected_grace():
    calls = {}

    class FakeJob:
        def terminate_and_confirm(self, *, timeout):
            calls["timeout"] = timeout
            return True

    class FakeProcess:
        def wait(self, timeout):
            calls["wait"] = timeout

        def poll(self):
            return 0

    assert ps._terminate_supervised_process(
        FakeProcess(), kill_job=FakeJob(), terminate_grace=1.25) is True
    assert calls["timeout"] == 1.25
    assert calls["wait"] == 1.25


def test_terminate_kill_job_branch_fails_when_worker_survives():
    class FakeJob:
        def terminate_and_confirm(self, *, timeout):
            return True

    class FakeProcess:
        def wait(self, timeout):
            raise subprocess.TimeoutExpired("worker", timeout)

        def poll(self):
            return None

        def kill(self):
            pass

    assert ps._terminate_supervised_process(
        FakeProcess(), kill_job=FakeJob(), terminate_grace=0.01) is False


def test_terminate_kill_job_branch_fails_closed_when_confirm_raises():
    closed = {}

    class FakeJob:
        def terminate_and_confirm(self, *, timeout):
            raise OSError("job query failed")

        def close(self):
            closed["closed"] = True
            return True

    class FakeProcess:
        def wait(self, timeout):
            return 0

        def poll(self):
            return 0

    assert ps._terminate_supervised_process(
        FakeProcess(), kill_job=FakeJob(), terminate_grace=0.01) is False
    assert closed == {"closed": True}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_process_supervision_module.py -q`
Expected: FAIL/ERROR with `ModuleNotFoundError: No module named 'process_supervision'`

- [ ] **Step 3: Create `process_supervision.py`**

Module header:

```python
"""Deadline supervision for CLI worker processes.

Extracted from ``rag.py`` (ROADMAP P3 runtime-decomposition, first slice).
``rag.py`` remains the compatibility facade: it re-exports these names and
pre-binds its own telemetry, warning, and configuration collaborators.  This
module is stdlib-only and must not import other repository modules.

Behavior is intentionally identical to the pre-extraction ``rag.py``
implementation; any defect discovered here is recorded in ``ROADMAP.md`` as
follow-up work rather than fixed during the move.
"""

import os
import signal
import subprocess
import sys
import threading as _threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

_DEFAULT_TERMINATE_GRACE = 5.0


@dataclass(frozen=True)
class SupervisionConfig:
    """Environment names and timing constants owned by the facade."""

    supervised_child_env: str
    run_id_env: str
    terminate_grace: float
    poll_interval: float
    start_gate_timeout: float
```

Then move, in this order, from `rag.py` @ `2d84abf`:

1. Lines 11459–11617 (`class _WindowsKillJob`) **verbatim**, with exactly one change: the `terminate_and_confirm` keyword default `timeout: float = _SUPERVISED_TERMINATE_GRACE` becomes `timeout: float = _DEFAULT_TERMINATE_GRACE`.
2. Lines 11620–11660 (`class _PosixSupervisedStartGate`) verbatim.
3. Lines 11663–11720 (`class _WindowsSupervisedStartGate`) verbatim.
4. Lines 11723–11725 (`_new_supervised_start_gate`) verbatim.
5. Lines 11728–11733 (`class _SupervisorSignal`) verbatim.
6. Lines 11736–11737 (`class _SupervisorCleanupError`) verbatim.
7. Lines 11740–11861 (`_terminate_supervised_process`) with this parameterization — signature and the only body changes shown (every other line verbatim):

```python
def _terminate_supervised_process(
        process, *, kill_job=None,
        terminate_grace: float = _DEFAULT_TERMINATE_GRACE,
        monotonic: Callable[[], float] = time.monotonic,
        sleep_fn: Callable[[float], None] = time.sleep) -> bool:
    """Terminate a supervised tree and confirm the direct worker was reaped."""
```

Body substitutions (mechanical, apply to every occurrence inside this function only):
- `_SUPERVISED_TERMINATE_GRACE` → `terminate_grace` (8 occurrences: `terminate_and_confirm(timeout=...)`, four `process.wait(timeout=...)`, the `taskkill_options["timeout"]`, and two `deadline = ... + ...`)
- `time.monotonic()` → `monotonic()` (4 occurrences)
- `time.sleep(0.05)` → `sleep_fn(0.05)` (2 occurrences)

Do NOT copy `_supervised_telemetry_requested` / `_finalize_supervised_run_telemetry` — they stay in `rag.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_process_supervision_module.py -q`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add process_supervision.py tests/test_process_supervision_module.py
git commit -m "Extract supervision containment and terminate core"
```

---

### Task 2: Deadline supervisor core

**Files:**
- Modify: `process_supervision.py` (append)
- Modify: `tests/test_process_supervision_module.py` (append)

**Interfaces:**
- Consumes: Task 1's `SupervisionConfig`, `_new_supervised_start_gate`, `_WindowsKillJob`, `_terminate_supervised_process`, `_SupervisorSignal`, `_SupervisorCleanupError`.
- Produces:

```python
def _run_cli_with_deadline(
        script_path: Path, argv: list[str], *,
        operation: str, timeout: float,
        config: SupervisionConfig,
        normalize_timeout_fn: Callable[[float], float] | None = None,
        telemetry_start_fn: Callable[..., None] | None = None,
        telemetry_finalize_fn: Callable[..., None] | None = None,
        warn_fn: Callable[[str], None] | None = None,
        kill_job_factory: Callable[[], Any] | None = None,
        start_gate_factory: Callable[[], Any] | None = None,
        terminate_fn: Callable[..., bool] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        working_directory: Path | None = None,
        environment_overrides: dict[str, str | None] | None = None,
        run_id: str | None = None,
        run_events: Path | None = None,
        run_report: Path | None = None,
        cancel_requested: Callable[[], bool] | None = None,
        on_child_started: Callable[[Any], None] | None = None,
        heartbeat: Callable[[Any], None] | None = None,
        stdout_target: Any = None,
        stderr_target: Any = None) -> int
```

Hook calling conventions (rag.py implements these in Task 4):
- `telemetry_start_fn(operation, run_id=run_id, run_events=run_events, run_report=run_report)` — called once before the worker launches; the hook owns the "was telemetry requested" predicate.
- `telemetry_finalize_fn(operation, run_id=run_id, run_events=run_events, run_report=run_report, status=status, exc=exc)` — called exactly where `_finalize_supervised_run_telemetry` is called today, same `status`/`exc` values.
- `terminate_fn(process, kill_job=kill_job)` — same call shape as today's internal `_terminate_supervised_process(process, kill_job=kill_job)` sites.
- `warn_fn(message)` — receives the exact current deadline-exceeded message; default prints it to `sys.stderr` verbatim.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_process_supervision_module.py`)

```python
class FakeGate:
    kind = "fake-gate"
    child_value = "7"

    def __init__(self):
        self.released = False
        self.closed = False

    def popen_options(self):
        return {}

    def release(self):
        self.released = True

    def close(self):
        self.closed = True


class Recorder:
    def __init__(self):
        self.finalized = []
        self.started = []
        self.warnings = []

    def telemetry_start(self, operation, *, run_id, run_events, run_report):
        self.started.append((operation, run_id))

    def telemetry_finalize(self, operation, *, run_id, run_events,
                           run_report, status, exc):
        self.finalized.append((operation, status, type(exc).__name__))

    def warn(self, message):
        self.warnings.append(message)


def _core_kwargs(recorder, gate, clock, **overrides):
    values = {
        "operation": "index",
        "timeout": 5.0,
        "config": _config(),
        "telemetry_start_fn": recorder.telemetry_start,
        "telemetry_finalize_fn": recorder.telemetry_finalize,
        "warn_fn": recorder.warn,
        "start_gate_factory": lambda: gate,
        "monotonic": clock.monotonic,
    }
    values.update(overrides)
    return values


def test_deadline_expiry_terminates_finalizes_and_returns_124(monkeypatch):
    clock = FakeClock()
    gate = FakeGate()
    recorder = Recorder()
    terminated = []

    class FakeProcess:
        pid = 4242

        def wait(self, timeout):
            clock.now += timeout
            raise subprocess.TimeoutExpired("worker", timeout)

        def poll(self):
            return None

    monkeypatch.setattr(
        ps.subprocess, "Popen", lambda *_a, **_k: FakeProcess())

    code = ps._run_cli_with_deadline(
        Path("worker.py"), ["index"],
        terminate_fn=lambda process, *, kill_job=None: (
            terminated.append(kill_job) or True),
        **_core_kwargs(recorder, gate, clock))

    assert code == 124
    assert gate.released and gate.closed
    assert terminated == [None] if os.name != "nt" else terminated
    assert recorder.started == [("index", None)]
    assert recorder.finalized == [("index", "failed", "TimeoutError")]
    assert "exceeded its 5s deadline" in recorder.warnings[0]
    assert "was terminated" in recorder.warnings[0]


def test_unconfirmed_cleanup_raises_and_skips_finalize(monkeypatch):
    clock = FakeClock()
    gate = FakeGate()
    recorder = Recorder()

    class FakeProcess:
        pid = 4243

        def wait(self, timeout):
            clock.now += timeout
            raise subprocess.TimeoutExpired("worker", timeout)

        def poll(self):
            return None

    monkeypatch.setattr(
        ps.subprocess, "Popen", lambda *_a, **_k: FakeProcess())

    with pytest.raises(ps._SupervisorCleanupError):
        ps._run_cli_with_deadline(
            Path("worker.py"), ["index"],
            terminate_fn=lambda process, *, kill_job=None: False,
            **_core_kwargs(recorder, gate, clock))

    assert recorder.finalized == []
    # The deadline warning still explains the unconfirmed cleanup state.
    assert "could not be confirmed" in recorder.warnings[0]


def test_external_cancellation_returns_130_and_finalizes_cancelled(
        monkeypatch):
    clock = FakeClock()
    gate = FakeGate()
    recorder = Recorder()

    class FakeProcess:
        pid = 4244

        def poll(self):
            return None

    monkeypatch.setattr(
        ps.subprocess, "Popen", lambda *_a, **_k: FakeProcess())

    code = ps._run_cli_with_deadline(
        Path("worker.py"), ["index"],
        cancel_requested=lambda: True,
        terminate_fn=lambda process, *, kill_job=None: True,
        **_core_kwargs(recorder, gate, clock))

    assert code == 130
    assert recorder.finalized == [("index", "cancelled", "KeyboardInterrupt")]


def test_child_registration_failure_terminates_and_finalizes(monkeypatch):
    clock = FakeClock()
    gate = FakeGate()
    recorder = Recorder()
    terminated = []

    class FakeProcess:
        pid = 4245

        def poll(self):
            return None

    monkeypatch.setattr(
        ps.subprocess, "Popen", lambda *_a, **_k: FakeProcess())

    def failing_registration(_process):
        raise RuntimeError("registration failed")

    with pytest.raises(RuntimeError, match="registration failed"):
        ps._run_cli_with_deadline(
            Path("worker.py"), ["index"],
            on_child_started=failing_registration,
            terminate_fn=lambda process, *, kill_job=None: (
                terminated.append(process.pid) or True),
            **_core_kwargs(recorder, gate, clock))

    assert terminated == [4245]
    assert not gate.released
    assert gate.closed
    assert recorder.finalized == [("index", "failed", "RuntimeError")]


def test_start_gate_factory_failure_finalizes_before_launch(monkeypatch):
    clock = FakeClock()
    recorder = Recorder()
    launched = []

    monkeypatch.setattr(
        ps.subprocess, "Popen",
        lambda *_a, **_k: launched.append(True))

    def failing_gate():
        raise OSError("no gate")

    with pytest.raises(OSError, match="no gate"):
        ps._run_cli_with_deadline(
            Path("worker.py"), ["index"],
            **_core_kwargs(
                recorder, FakeGate(), clock,
                start_gate_factory=failing_gate))

    assert launched == []
    assert recorder.finalized == [("index", "failed", "OSError")]


def test_normalize_timeout_fn_rejects_before_launch(monkeypatch):
    clock = FakeClock()
    recorder = Recorder()
    launched = []
    monkeypatch.setattr(
        ps.subprocess, "Popen",
        lambda *_a, **_k: launched.append(True))

    def rejecting_normalize(value):
        raise ValueError(f"bad timeout {value}")

    with pytest.raises(ValueError, match="bad timeout 5.0"):
        ps._run_cli_with_deadline(
            Path("worker.py"), ["index"],
            **_core_kwargs(
                recorder, FakeGate(), clock,
                normalize_timeout_fn=rejecting_normalize))

    assert launched == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_process_supervision_module.py -q`
Expected: new tests FAIL with `AttributeError: module 'process_supervision' has no attribute '_run_cli_with_deadline'`

- [ ] **Step 3: Implement the core**

Append `_run_cli_with_deadline` to `process_supervision.py`: copy rag.py lines 11890–12117 and apply exactly these substitutions:

1. New signature: as pinned in **Interfaces** above (all pre-existing parameters keep their current names, order, and defaults; the new keyword-only collaborator parameters are added after `timeout`).
2. First body line `timeout = _normalize_operation_timeout(timeout)` becomes:

```python
    if normalize_timeout_fn is not None:
        timeout = normalize_timeout_fn(timeout)
    if kill_job_factory is None:
        kill_job_factory = _WindowsKillJob
    if start_gate_factory is None:
        start_gate_factory = _new_supervised_start_gate
    if terminate_fn is None:
        def terminate_fn(process, *, kill_job=None):
            return _terminate_supervised_process(
                process, kill_job=kill_job,
                terminate_grace=config.terminate_grace)
    if warn_fn is None:
        def warn_fn(message):
            print(message, file=sys.stderr)

    def _finalize(status: str, exc: BaseException) -> None:
        if telemetry_finalize_fn is not None:
            telemetry_finalize_fn(
                operation, run_id=run_id, run_events=run_events,
                run_report=run_report, status=status, exc=exc)
```

3. `environment[_SUPERVISED_CHILD_ENV] = "1"` → `environment[config.supervised_child_env] = "1"`
4. `environment[_RUN_ID_ENV] = run_id` → `environment[config.run_id_env] = run_id`
5. The telemetry-start block

```python
    if _supervised_telemetry_requested(run_id, run_events, run_report):
        _run_telemetry.RunTelemetry(
            operation, run_id=run_id, events_path=run_events,
            report_path=run_report).start()
```

becomes

```python
    if telemetry_start_fn is not None:
        telemetry_start_fn(
            operation, run_id=run_id, run_events=run_events,
            run_report=run_report)
```

6. `kill_job = _WindowsKillJob()` → `kill_job = kill_job_factory()`
7. `start_gate = _new_supervised_start_gate()` → `start_gate = start_gate_factory()`
8. `str(_SUPERVISED_START_GATE_TIMEOUT)` → `str(config.start_gate_timeout)`
9. Every `_finalize_supervised_run_telemetry(operation, run_id=run_id, run_events=run_events, run_report=run_report, status=<S>, exc=<E>)` call (7 sites) → `_finalize(<S>, <E>)` with the identical status/exc expressions, preserving each site's exact position relative to cleanup checks and raises.
10. Every `_terminate_supervised_process(process, kill_job=kill_job)` call (7 sites) → `terminate_fn(process, kill_job=kill_job)`.
11. `_SUPERVISED_POLL_INTERVAL` → `config.poll_interval` (1 site).
12. `time.monotonic()` → `monotonic()` (4 sites: deadline assignment, `remaining` computation, the poll-callback deadline recheck, and none other).
13. The deadline-exceeded stderr report

```python
        print(
            f"Operation '{operation}' exceeded its {timeout:g}s deadline and "
            f"was terminated. {cleanup_status}",
            file=sys.stderr,
        )
```

becomes

```python
        warn_fn(
            f"Operation '{operation}' exceeded its {timeout:g}s deadline and "
            f"was terminated. {cleanup_status}")
```

Everything else — the signal-handler installation, `_SupervisorSignal` re-raise mapping, all `except` clause ordering, the `finally` restore/close block, `supervised_worker.py` bootstrap resolution via `Path(__file__).with_name("supervised_worker.py")` — moves byte-identical.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_process_supervision_module.py -q`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add process_supervision.py tests/test_process_supervision_module.py
git commit -m "Extract deadline supervisor core with injected collaborators"
```

---

### Task 3: Generic supervised entrypoint runner

**Files:**
- Modify: `process_supervision.py` (append)
- Modify: `tests/test_process_supervision_module.py` (append)

**Interfaces:**
- Produces:

```python
def _run_supervised_entrypoint(
        argv: list[str] | None = None, *,
        environment_overrides: dict[str, str | None] | None = None,
        config: SupervisionConfig,
        script_path: Path,
        command_resolver: Callable[[list[str]], str | None],
        operation_timeouts: dict,
        telemetry_options_fn: Callable[[list[str], str], dict],
        timeout_fn: Callable[[list[str], str], float],
        run_id_factory: Callable[[], str],
        menu_fn: Callable[[], None],
        main_fn: Callable[[list[str]], Any],
        supervisor_fn: Callable[..., int]) -> int
```

- [ ] **Step 1: Write the failing tests** (append to `tests/test_process_supervision_module.py`)

```python
def _entrypoint_kwargs(calls, **overrides):
    values = {
        "config": _config(),
        "script_path": Path("rag.py"),
        "command_resolver": lambda args: args[0] if args else None,
        "operation_timeouts": {"index": 300.0, "query": 60.0},
        "telemetry_options_fn": lambda args, command: {
            "run_id": None, "events_path": None, "report_path": None},
        "timeout_fn": lambda args, command: 42.0,
        "run_id_factory": lambda: "factory-run",
        "menu_fn": lambda: calls.append(("menu",)),
        "main_fn": lambda args: calls.append(("main", args)),
        "supervisor_fn": lambda script, argv, **kwargs: (
            calls.append(("supervise", script, argv, kwargs)) or 9),
    }
    values.update(overrides)
    return values


def test_entrypoint_supervises_vector_commands(monkeypatch):
    calls = []
    monkeypatch.delenv("TEST_SUPERVISED_CHILD", raising=False)
    monkeypatch.delenv("TEST_RUN_ID", raising=False)

    assert ps._run_supervised_entrypoint(
        ["query", "terms"], **_entrypoint_kwargs(calls)) == 9

    kind, script, argv, kwargs = calls[0]
    assert (kind, argv) == ("supervise", ["query", "terms"])
    assert script == Path("rag.py")
    assert kwargs == {
        "operation": "query",
        "timeout": 42.0,
        "run_id": "factory-run",
        "run_events": None,
        "run_report": None,
    }


def test_entrypoint_runs_menu_and_direct_commands(monkeypatch):
    calls = []
    monkeypatch.delenv("TEST_SUPERVISED_CHILD", raising=False)

    assert ps._run_supervised_entrypoint([], **_entrypoint_kwargs(calls)) == 0
    assert ps._run_supervised_entrypoint(
        ["export"], **_entrypoint_kwargs(calls)) == 0
    assert calls == [("menu",), ("main", ["export"])]


def test_entrypoint_child_does_not_recursively_supervise(monkeypatch):
    calls = []
    monkeypatch.setenv("TEST_SUPERVISED_CHILD", "1")

    assert ps._run_supervised_entrypoint(
        ["index"], **_entrypoint_kwargs(calls)) == 0
    assert calls == [("main", ["index"])]


def test_entrypoint_restores_environment_overrides(monkeypatch):
    calls = []
    monkeypatch.setenv("TEST_SUPERVISED_CHILD", "1")
    monkeypatch.setenv("KEEP_ME", "original")
    monkeypatch.delenv("ADD_ME", raising=False)

    def checking_main(args):
        calls.append((os.environ.get("KEEP_ME"), os.environ.get("ADD_ME"),
                      os.environ.get("DROP_ME")))

    monkeypatch.setenv("DROP_ME", "present")
    assert ps._run_supervised_entrypoint(
        ["index"],
        environment_overrides={
            "KEEP_ME": "overridden", "ADD_ME": "added", "DROP_ME": None},
        **_entrypoint_kwargs(calls, main_fn=checking_main)) == 0

    assert calls == [("overridden", "added", None)]
    assert os.environ["KEEP_ME"] == "original"
    assert "ADD_ME" not in os.environ
    assert os.environ["DROP_ME"] == "present"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_process_supervision_module.py -q`
Expected: new tests FAIL with `AttributeError: ... '_run_supervised_entrypoint'`

- [ ] **Step 3: Implement the runner**

Copy rag.py lines 12124–12173 into `process_supervision.py` as `_run_supervised_entrypoint` with the **Interfaces** signature and these substitutions (all other lines verbatim):

- `command = _rag_cli_command(cli_args)` → `command = command_resolver(cli_args)`
- `interactive_menu()` → `menu_fn()`
- `command in DEFAULT_OPERATION_TIMEOUTS` → `command in operation_timeouts`
- `os.environ.get(_SUPERVISED_CHILD_ENV) != "1"` → `os.environ.get(config.supervised_child_env) != "1"`
- `telemetry_options = _cli_run_telemetry_options(cli_args, command)` → `telemetry_options = telemetry_options_fn(cli_args, command)`
- `os.environ.get(_RUN_ID_ENV) or _run_telemetry.new_run_id()` → `os.environ.get(config.run_id_env) or run_id_factory()`
- `"timeout": _cli_operation_timeout(cli_args, command),` → `"timeout": timeout_fn(cli_args, command),`
- `return _run_cli_with_deadline(Path(__file__), cli_args, **supervisor_options)` → `return supervisor_fn(Path(script_path), cli_args, **supervisor_options)`
- `main(cli_args)` → `main_fn(cli_args)`

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_process_supervision_module.py -q`
Expected: 14 passed

- [ ] **Step 5: Commit**

```bash
git add process_supervision.py tests/test_process_supervision_module.py
git commit -m "Extract generic supervised entrypoint runner"
```

---

### Task 4: Rewire rag.py as the compatibility facade

**Files:**
- Modify: `rag.py` (delete lines 11459–11861 moved bodies, 11890–12117, 12124–12173; add the facade block below in their place)

**Interfaces:**
- Consumes: everything Tasks 1–3 produce.
- Produces (unchanged public surface): `rag._WindowsKillJob`, `rag._PosixSupervisedStartGate`, `rag._WindowsSupervisedStartGate`, `rag._new_supervised_start_gate`, `rag._SupervisorSignal`, `rag._SupervisorCleanupError`, `rag._terminate_supervised_process(process, *, kill_job=None)`, `rag._run_cli_with_deadline(script_path, argv, *, operation, timeout, working_directory=None, environment_overrides=None, run_id=None, run_events=None, run_report=None, cancel_requested=None, on_child_started=None, heartbeat=None, stdout_target=None, stderr_target=None)`, `rag._run_rag_entrypoint(argv=None, *, environment_overrides=None)`.

- [ ] **Step 1: Add the module import**

Next to rag.py's existing repo-module imports (`import cli_policy as _cli_policy`, `import run_telemetry as _run_telemetry` — match their placement and style):

```python
import process_supervision as _process_supervision
```

- [ ] **Step 2: Replace the moved region**

Delete the moved definitions and insert (keeping `_normalize_operation_timeout`, `_cli_operation_timeout`, `_supervised_telemetry_requested`, `_finalize_supervised_run_telemetry`, and the constants exactly where they are):

```python
_WindowsKillJob = _process_supervision._WindowsKillJob
_PosixSupervisedStartGate = _process_supervision._PosixSupervisedStartGate
_WindowsSupervisedStartGate = _process_supervision._WindowsSupervisedStartGate
_new_supervised_start_gate = _process_supervision._new_supervised_start_gate
_SupervisorSignal = _process_supervision._SupervisorSignal
_SupervisorCleanupError = _process_supervision._SupervisorCleanupError


def _supervision_config() -> _process_supervision.SupervisionConfig:
    """Snapshot rag's supervision constants for one supervised call."""
    return _process_supervision.SupervisionConfig(
        supervised_child_env=_SUPERVISED_CHILD_ENV,
        run_id_env=_RUN_ID_ENV,
        terminate_grace=_SUPERVISED_TERMINATE_GRACE,
        poll_interval=_SUPERVISED_POLL_INTERVAL,
        start_gate_timeout=_SUPERVISED_START_GATE_TIMEOUT,
    )


def _terminate_supervised_process(process, *, kill_job=None) -> bool:
    """Terminate a supervised tree and confirm the direct worker was reaped."""
    return _process_supervision._terminate_supervised_process(
        process, kill_job=kill_job,
        terminate_grace=_SUPERVISED_TERMINATE_GRACE)


def _start_supervised_run_telemetry(
        operation: str, *, run_id: str | None,
        run_events: Path | None, run_report: Path | None) -> None:
    if _supervised_telemetry_requested(run_id, run_events, run_report):
        _run_telemetry.RunTelemetry(
            operation, run_id=run_id, events_path=run_events,
            report_path=run_report).start()


def _run_cli_with_deadline(script_path: Path, argv: list[str], *,
                           operation: str, timeout: float,
                           working_directory: Path | None = None,
                           environment_overrides: dict[str, str | None]
                           | None = None,
                           run_id: str | None = None,
                           run_events: Path | None = None,
                           run_report: Path | None = None,
                           cancel_requested: Callable[[], bool]
                           | None = None,
                           on_child_started: Callable[[Any], None]
                           | None = None,
                           heartbeat: Callable[[Any], None] | None = None,
                           stdout_target: Any = None,
                           stderr_target: Any = None) -> int:
    """Run one CLI operation in a killable process with a wall-clock deadline.

    Collaborators are resolved from rag module globals on every call so test
    monkeypatching of ``rag._WindowsKillJob``, ``rag._terminate_supervised_process``,
    and ``rag._SUPERVISED_TERMINATE_GRACE`` keeps its historical effect.
    """
    return _process_supervision._run_cli_with_deadline(
        script_path, argv, operation=operation, timeout=timeout,
        config=_supervision_config(),
        normalize_timeout_fn=_normalize_operation_timeout,
        telemetry_start_fn=_start_supervised_run_telemetry,
        telemetry_finalize_fn=_finalize_supervised_run_telemetry,
        kill_job_factory=_WindowsKillJob,
        start_gate_factory=_new_supervised_start_gate,
        terminate_fn=_terminate_supervised_process,
        working_directory=working_directory,
        environment_overrides=environment_overrides,
        run_id=run_id, run_events=run_events, run_report=run_report,
        cancel_requested=cancel_requested,
        on_child_started=on_child_started, heartbeat=heartbeat,
        stdout_target=stdout_target, stderr_target=stderr_target)
```

and, replacing `_run_rag_entrypoint` (after the existing `_rag_cli_command` / `_cli_run_telemetry_options` aliases):

```python
def _run_rag_entrypoint(
        argv: list[str] | None = None, *,
        environment_overrides: dict[str, str | None] | None = None) -> int:
    """Run the CLI, supervising vector-using commands in a child process."""
    return _process_supervision._run_supervised_entrypoint(
        argv, environment_overrides=environment_overrides,
        config=_supervision_config(),
        script_path=Path(__file__),
        command_resolver=_rag_cli_command,
        operation_timeouts=DEFAULT_OPERATION_TIMEOUTS,
        telemetry_options_fn=_cli_run_telemetry_options,
        timeout_fn=_cli_operation_timeout,
        run_id_factory=_run_telemetry.new_run_id,
        menu_fn=interactive_menu,
        main_fn=main,
        supervisor_fn=_run_cli_with_deadline)
```

Note: `_finalize_supervised_run_telemetry`'s existing signature `(operation, *, run_id, run_events, run_report, status, exc)` already matches the core's hook convention exactly — pass it through unmodified.

- [ ] **Step 3: Run the unchanged characterization suite**

Run: `python -m pytest tests/test_process_supervision.py -q`
Expected: all pass, zero modifications to that file (`git diff --stat tests/test_process_supervision.py` must be empty)

- [ ] **Step 4: Run the full suite and static checks**

Run: `python -m pytest -q`
Expected: everything passes (1,079+ passed / 7 skipped — prior 1,065 + 14 module tests)

Run: `python -m ruff check . && python -m compileall -q rag.py process_supervision.py tests && git diff --check`
Expected: all clean

- [ ] **Step 5: Commit**

```bash
git add rag.py
git commit -m "Rebind rag.py supervision names to extracted module"
```

---

### Task 5: Real-artifact smoke, docs, and publication

**Files:**
- Modify: `ROADMAP.md` (P3 row + milestone table entry)
- Modify: `README.md` only if it names supervision internals (verify with `grep -n "supervision" README.md`; expected: no code-level claims change)

- [ ] **Step 1: Real supervised smoke test**

Run: `python rag.py info`
Expected: exits 0, normal info output — this exercises the real supervised child path (`info` is a supervised operation) end to end through the extracted module: gate release, worker bootstrap, tree cleanup.

Run: `python rag.py search "duty to correct a false statement" --db output/Ethics_3/Ethics_3_chroma --collection ethics_3 --top-k 3`
Expected: exits 0 with ranked results — supervised vector path against the real Ethics corpus. (Skip only if the local Chroma artifacts are absent; note the skip in the PR.)

- [ ] **Step 2: Update ROADMAP.md**

In the milestone table add:

```markdown
| Process supervision extraction | In progress | Deadline-supervision core moved to stdlib-only `process_supervision.py`; `rag.py` facade preserves every name, signature, and monkeypatch seam; characterization suite unchanged |
```

and change the P3 row's milestone text from "Process supervision and vector lifecycle move out of `rag.py` in separate failure-injected milestones" to "Vector lifecycle moves out of `rag.py` in a separate failure-injected milestone (process supervision extracted)". Record any defect noticed during extraction as a new follow-up row instead of fixing it.

- [ ] **Step 3: Full verification pass**

Run: `python -m pytest -q && python -m ruff check . && python -m compileall -q rag.py process_supervision.py tests && git diff --check`
Expected: all green.

- [ ] **Step 4: Commit and publish**

```bash
git add ROADMAP.md
git commit -m "Record process-supervision extraction milestone status"
git push -u origin agent/extract-process-supervision
```

Then open a draft PR against `main` titled "Extract process supervision from rag.py" whose body records: the design/spec link, the behavior-preservation contract, the monkeypatch-seam table from this plan, characterization-suite-unchanged evidence, full-suite counts, smoke-test transcript, and CI expectations (Linux + Windows matrices must pass — the Windows job-object and start-gate paths only execute there).

- [ ] **Step 5: Post-CI gate**

Watch CI to completion; run the adversarial review workflow over the PR diff; post the surviving findings (or a no-blocker verdict) as a durable PR comment before any merge.
