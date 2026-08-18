# Supply-Chain and Transport Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the three live transitive-dependency advisories with a
targeted lock refresh, add a transport timeout backstop, and aggregate
inspection-issue warning floods.

**Architecture:** `tools/refresh_locks.py` gains a `--upgrade-package`
passthrough; the seven locks are regenerated operationally (never by
hand); `rag.py` gains a fallback-timeout constant applied via
`kwargs.setdefault` in the two policy transport helpers and a
`_log_inspection_issues` aggregation helper used by the scan and
preprocess issue loops.

**Tech Stack:** stdlib + uv (already pinned). No new dependencies; no
direct requirement changes.

## Global Constraints

- Locks are generated only by `tools/refresh_locks.py`; a second
  regeneration must be byte-stable before commit; only the three target
  transitive records may change (probe-verified: aiohttp 3.14.2→3.14.3,
  cryptography 49.0.0→50.0.0, h2 4.3.0→4.4.1).
- No direct requirement record changes (domain gate); no
  vulnerability-policy exception edits.
- Fallback timeout constant `_TRANSPORT_FALLBACK_TIMEOUT_SECONDS = 300.0`;
  explicit caller timeouts must pass through unchanged.
- Aggregation threshold: first 3 issues per stage verbatim, then one
  summary line per stage with the exact remainder count.
- Ruff E4/E7/E9/F py310; 79-col style of the touched modules.

---

### Task 1: `refresh_locks.py --upgrade-package`

**Files:** Modify `tools/refresh_locks.py`; test
`tests/test_dependency_policy.py` (append near
`test_lock_refresh_commands_cover_every_lock_and_cpu_runtime`).

- [ ] Failing tests:

```python
def test_lock_refresh_upgrade_package_passthrough_and_exclusivity():
    commands = refresh_locks.lock_commands(
        "uv", upgrade_packages=("aiohttp", "cryptography", "h2"))
    for command in commands:
        tail = command[command.index("--output-file") + 2:]
        assert tail == [
            "--upgrade-package", "aiohttp",
            "--upgrade-package", "cryptography",
            "--upgrade-package", "h2",
        ]
        assert "--upgrade" not in command
    assert refresh_locks.main(
        ["--upgrade", "--upgrade-package", "aiohttp"]) == 2
```

- [ ] Implement: `lock_commands(uv, *, upgrade=False, upgrade_packages=())`
  appends `--upgrade` or the repeated `--upgrade-package NAME` pairs at
  the end; `main` gains
  `parser.add_argument("--upgrade-package", action="append", default=[],
  metavar="NAME")` and returns 2 with a stderr message when both flags
  are combined.
- [ ] Run the two tests + ruff; commit.

### Task 2: transport timeout backstop

**Files:** Modify `rag.py` (`_post_loopback_without_environment`,
`_post_cloud_with_policy`, constant near them); test
`tests/test_release_security_boundaries.py` (append).

- [ ] Failing tests (stub `requests.Session` via monkeypatch to capture
  kwargs without network; mirror that file's existing stubbing style):

```python
def test_policy_transports_apply_fallback_timeout(monkeypatch):
    captured = {}

    class _Session:
        trust_env = True
        def post(self, url, **kwargs):
            captured.update(kwargs)
            class _Resp:  # minimal owned-response shape
                def close(self): ...
            return _Resp()
        def close(self): ...

    monkeypatch.setattr(rag.requests, "Session", _Session)
    rag._post_loopback_without_environment("http://127.0.0.1:1/x")
    assert captured["timeout"] == rag._TRANSPORT_FALLBACK_TIMEOUT_SECONDS
    captured.clear()
    rag._post_cloud_with_policy(None, "https://example.invalid/x",
                                timeout=7.5)
    assert captured["timeout"] == 7.5
```

- [ ] Implement the constant and one `kwargs.setdefault("timeout", ...)`
  line in each helper, beside the existing `stream` default.
- [ ] Run the file's tests + ruff; commit.

### Task 3: inspection-issue warning aggregation

**Files:** Modify `rag.py` (new `_log_inspection_issues` above
`_analyze_pdf_images`; both issue loops route through it); test
`tests/test_scan_cli.py` (append, caplog).

- [ ] Failing tests: three issues on one stage log verbatim only; five
  issues log three verbatim + `and 2 more pages with text inspection
  issues`; mixed stages aggregate independently.
- [ ] Implement:

```python
_INSPECTION_ISSUE_LOG_LIMIT = 3


def _log_inspection_issues(issues) -> None:
    """Log inspection issues without per-page warning floods."""
    logged: dict[str, int] = {}
    totals: dict[str, int] = {}
    for issue in issues:
        totals[issue.stage] = totals.get(issue.stage, 0) + 1
    for issue in issues:
        count = logged.get(issue.stage, 0)
        if count >= _INSPECTION_ISSUE_LOG_LIMIT:
            continue
        logged[issue.stage] = count + 1
        if issue.stage == "text":
            log.warning(
                "Could not inspect the text layer on PDF page %s: %s",
                issue.page_number, issue.detail)
        else:
            log.warning(
                "Could not inspect images on PDF page %s: %s",
                issue.page_number, issue.detail)
    for stage, total in totals.items():
        remainder = total - min(total, _INSPECTION_ISSUE_LOG_LIMIT)
        if remainder > 0:
            log.warning(
                "... and %d more pages with %s inspection issues",
                remainder, stage)
```
  (Adapt the preprocess loop's message forms exactly as found at its
  call site; keep each call site's wording by parameterizing if they
  differ.)
- [ ] Run tests + ruff; commit.

### Task 4: operational lock refresh, gates, review, merge, evidence

- [ ] Run `python tools/refresh_locks.py --upgrade-package aiohttp
  --upgrade-package cryptography --upgrade-package h2` in the working
  tree; verify with `git diff --stat` that only locks containing the
  targets changed and the three records moved; run it a second time and
  require zero further diff (byte-stable).
- [ ] Resync both A0 environments against the new lock union; `uv pip
  check`; full suites both platforms; all static/policy gates; offline
  suites; inventory refresh if Python changed.
- [ ] Whole-branch subagent review; fix wave; merge to `main`
  history-preserving; regenerate paired Phase A0 evidence at the merged
  checkpoint (both platforms, independent checks); ROADMAP/README/policy
  provenance updates; evidence commit; push.

## Self-review notes

- Spec items 1/2/3 map to Tasks 1+4 / 2 / 3. The lock-content
  assertions are operational (Task 4) because tests must stay
  network-free. Names used: `lock_commands(..., upgrade_packages=...)`,
  `_TRANSPORT_FALLBACK_TIMEOUT_SECONDS`, `_log_inspection_issues`,
  `_INSPECTION_ISSUE_LOG_LIMIT` — consistent across tasks.
