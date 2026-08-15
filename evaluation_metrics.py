"""Dependency-light resource and cost measurements for retrieval evaluation.

The helpers in this module deliberately avoid provider price tables.  Monetary
figures are projections and are emitted only when a caller supplies rates for
the run being measured.
"""

from __future__ import annotations

import json
import math
import os
import random
import time
import tracemalloc
from pathlib import Path
from typing import Callable, Iterable


LLM_RUNTIME_REPORT_SCHEMA_VERSION = 5
LLM_RUNTIME_REPORT_SCHEMA_VERSIONS = frozenset({2, 3, 4, 5})

def estimate_text_tokens(text: str) -> int:
    """Return the runtime's documented characters/4 token estimate."""
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    return math.ceil(len(text) / 4) if text else 0


def percentile(values: Iterable[float], quantile: float) -> float:
    """Return a linearly interpolated percentile for finite values."""
    if (isinstance(quantile, bool) or not isinstance(quantile, (int, float))
            or not math.isfinite(float(quantile))
            or not 0 <= float(quantile) <= 1):
        raise ValueError("quantile must be a finite number from 0 to 1")
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return 0.0
    if not all(math.isfinite(value) for value in ordered):
        raise ValueError("percentile values must be finite")
    position = (len(ordered) - 1) * float(quantile)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


PAIRED_BOOTSTRAP_DEFAULT_RESAMPLES = 2000
PAIRED_BOOTSTRAP_DEFAULT_SEED = 20260814


def paired_bootstrap(
        baseline_values: Iterable[float],
        candidate_values: Iterable[float], *,
        resamples: int = PAIRED_BOOTSTRAP_DEFAULT_RESAMPLES,
        seed: int = PAIRED_BOOTSTRAP_DEFAULT_SEED,
        confidence: float = 0.95) -> dict:
    """Percentile bootstrap of the mean per-query delta.

    Deltas are candidate minus baseline, paired per query.  The p-value is
    the two-sided bootstrap achieved significance level: twice the smaller
    share of resampled mean deltas at or beyond zero, capped at 1.  The
    seeded generator makes every field deterministic for a given input.
    """
    baseline = [float(value) for value in baseline_values]
    candidate = [float(value) for value in candidate_values]
    if len(baseline) != len(candidate):
        raise ValueError(
            "paired bootstrap requires equal-length value lists")
    if not baseline:
        raise ValueError("paired bootstrap requires at least one pair")
    if not all(math.isfinite(value) for value in baseline + candidate):
        raise ValueError("paired bootstrap values must be finite")
    if (isinstance(resamples, bool) or not isinstance(resamples, int)
            or resamples < 1):
        raise ValueError("resamples must be a positive integer")
    if (isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not 0 < float(confidence) < 1):
        raise ValueError("confidence must be strictly between 0 and 1")
    deltas = [after - before for before, after in zip(baseline, candidate)]
    observed = sum(deltas) / len(deltas)
    rng = random.Random(seed)
    count = len(deltas)
    means = []
    for _ in range(resamples):
        total = 0.0
        for _ in range(count):
            total += deltas[rng.randrange(count)]
        means.append(total / count)
    alpha = (1.0 - float(confidence)) / 2.0
    lower_tail = sum(1 for mean in means if mean <= 0.0)
    upper_tail = sum(1 for mean in means if mean >= 0.0)
    p_value = min(1.0, 2.0 * min(lower_tail, upper_tail) / resamples)
    return {
        "pairs": count,
        "resamples": resamples,
        "seed": seed,
        "confidence": float(confidence),
        "mean_delta": round(observed, 6),
        "ci_low": round(percentile(means, alpha), 6),
        "ci_high": round(percentile(means, 1.0 - alpha), 6),
        "p_value": round(p_value, 6),
    }


def measure_path(path: Path, *, max_entries: int = 100_000) -> dict:
    """Measure regular-file storage without following filesystem links."""
    path = Path(path)
    if (isinstance(max_entries, bool) or not isinstance(max_entries, int)
            or max_entries < 1):
        raise ValueError("max_entries must be a positive integer")
    result = {
        "path": str(path.resolve()),
        "bytes": 0,
        "file_count": 0,
        "directory_count": 0,
        "skipped_symlinks": 0,
        "skipped_special": 0,
        "errors": 0,
        "truncated": False,
    }
    try:
        if path.is_symlink():
            result["skipped_symlinks"] = 1
            return result
        if path.is_file():
            result["bytes"] = path.stat().st_size
            result["file_count"] = 1
            return result
        if not path.is_dir():
            return result
    except OSError:
        result["errors"] = 1
        return result

    stack = [path]
    entries_seen = 0
    while stack:
        directory = stack.pop()
        result["directory_count"] += 1
        try:
            entries = os.scandir(directory)
        except OSError:
            result["errors"] += 1
            continue
        with entries:
            for entry in entries:
                entries_seen += 1
                if entries_seen > max_entries:
                    result["truncated"] = True
                    stack.clear()
                    break
                try:
                    if entry.is_symlink():
                        result["skipped_symlinks"] += 1
                    elif entry.is_file(follow_symlinks=False):
                        result["bytes"] += entry.stat(
                            follow_symlinks=False).st_size
                        result["file_count"] += 1
                    elif entry.is_dir(follow_symlinks=False):
                        stack.append(Path(entry.path))
                    else:
                        result["skipped_special"] += 1
                except OSError:
                    result["errors"] += 1
    return result


def process_rss_bytes() -> int | None:
    """Return current resident memory when the platform exposes it."""
    try:
        import psutil

        return int(psutil.Process().memory_info().rss)
    except (ImportError, OSError, RuntimeError):
        pass
    if os.name == "posix":
        try:
            statm = Path("/proc/self/statm").read_text(
                encoding="ascii").split()
            return int(statm[1]) * int(os.sysconf("SC_PAGE_SIZE"))
        except (OSError, ValueError, IndexError, AttributeError):
            return None
    if os.name == "nt":
        return _windows_process_rss_bytes()
    return None


def _windows_process_rss_bytes() -> int | None:
    """Return Windows working-set bytes through the stable process API."""
    try:
        import ctypes
        from ctypes import wintypes

        size_type = ctypes.c_size_t

        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", size_type),
                ("WorkingSetSize", size_type),
                ("QuotaPeakPagedPoolUsage", size_type),
                ("QuotaPagedPoolUsage", size_type),
                ("QuotaPeakNonPagedPoolUsage", size_type),
                ("QuotaNonPagedPoolUsage", size_type),
                ("PagefileUsage", size_type),
                ("PeakPagefileUsage", size_type),
            ]

        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        get_current_process = ctypes.windll.kernel32.GetCurrentProcess
        get_current_process.restype = wintypes.HANDLE
        get_process_memory_info = ctypes.windll.psapi.GetProcessMemoryInfo
        get_process_memory_info.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(ProcessMemoryCounters),
            wintypes.DWORD,
        ]
        get_process_memory_info.restype = wintypes.BOOL
        process = get_current_process()
        succeeded = get_process_memory_info(
            process, ctypes.byref(counters), counters.cb)
        return int(counters.WorkingSetSize) if succeeded else None
    except (AttributeError, OSError, ValueError):
        return None


class MeasurementCollector:
    """Collect query latency and bounded, best-effort memory observations."""

    def __init__(
            self, *, clock: Callable[[], float] = time.perf_counter,
            rss_reader: Callable[[], int | None] = process_rss_bytes,
            trace_python_memory: bool = True):
        self._clock = clock
        self._rss_reader = rss_reader
        self._trace_python_memory = trace_python_memory
        self._started_at: float | None = None
        self._latencies_ms: list[float] = []
        self._rss_samples: list[int] = []
        self._started_tracing = False

    def start(self) -> None:
        if self._started_at is not None:
            raise RuntimeError("measurement collection already started")
        self._latencies_ms.clear()
        self._rss_samples.clear()
        self._started_tracing = False
        if self._trace_python_memory and not tracemalloc.is_tracing():
            tracemalloc.start()
            self._started_tracing = True
        self._started_at = self._clock()
        self.sample_memory()

    def begin_query(self) -> float:
        if self._started_at is None:
            raise RuntimeError("measurement collection has not started")
        return self._clock()

    def end_query(self, started_at: float) -> float:
        latency_ms = max(0.0, (self._clock() - started_at) * 1000)
        self._latencies_ms.append(latency_ms)
        self.sample_memory()
        return latency_ms

    def sample_memory(self) -> None:
        value = self._rss_reader()
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            self._rss_samples.append(value)

    def finish(self) -> dict:
        if self._started_at is None:
            raise RuntimeError("measurement collection has not started")
        total_ms = max(0.0, (self._clock() - self._started_at) * 1000)
        self.sample_memory()
        python_current = None
        python_peak = None
        if self._trace_python_memory and tracemalloc.is_tracing():
            python_current, python_peak = tracemalloc.get_traced_memory()
        if self._started_tracing:
            tracemalloc.stop()
        result = {
            "query_latency_ms": {
                "count": len(self._latencies_ms),
                "mean": _rounded(
                    sum(self._latencies_ms) / len(self._latencies_ms)
                    if self._latencies_ms else 0.0),
                "p50": _rounded(percentile(self._latencies_ms, 0.50)),
                "p95": _rounded(percentile(self._latencies_ms, 0.95)),
                "max": _rounded(max(self._latencies_ms, default=0.0)),
            },
            "total_wall_ms": _rounded(total_ms),
            "sampled_peak_rss_bytes": max(self._rss_samples, default=None),
            "python_current_traced_bytes": python_current,
            "python_peak_traced_bytes": python_peak,
            "memory_methods": {
                "rss": "sampled process resident set; not a continuous peak",
                "python": "tracemalloc; excludes native allocations",
            },
        }
        self._started_at = None
        return result


def parse_llm_usage_report(path: Path) -> dict:
    """Read the aggregate, prompt-free usage fields from an LLMRuntime report."""
    path = Path(path)
    raw = path.read_bytes()
    payload = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(payload, dict) or not isinstance(payload.get("counts"), dict):
        raise ValueError(f"LLM report has no counts object: {path}")
    if payload.get("schema_version") not in LLM_RUNTIME_REPORT_SCHEMA_VERSIONS:
        raise ValueError(
            "LLM report schema version must be one of "
            f"{sorted(LLM_RUNTIME_REPORT_SCHEMA_VERSIONS)}: {path}")
    counts = payload["counts"]
    names = (
        "exact_prompt_tokens", "exact_completion_tokens",
        "estimated_prompt_tokens", "estimated_completion_tokens",
        "exact_usage_attempts", "estimated_usage_attempts",
        "unknown_usage_attempts",
    )
    normalized = {}
    for name in names:
        if name not in counts:
            raise ValueError(f"LLM report is missing count {name!r}: {path}")
        value = counts[name]
        if (isinstance(value, bool) or not isinstance(value, int) or value < 0):
            raise ValueError(f"LLM report count {name!r} must be an integer >= 0")
        normalized[name] = value
    if (not normalized["exact_usage_attempts"]
            and (normalized["exact_prompt_tokens"]
                 or normalized["exact_completion_tokens"])):
        raise ValueError("LLM report has exact tokens without exact attempts")
    if (not normalized["estimated_usage_attempts"]
            and (normalized["estimated_prompt_tokens"]
                 or normalized["estimated_completion_tokens"])):
        raise ValueError(
            "LLM report has estimated tokens without estimated attempts")
    return {
        "report_path": str(path.resolve()),
        "report_sha256": _sha256_bytes(raw),
        **normalized,
        "input_tokens": (
            normalized["exact_prompt_tokens"]
            + normalized["estimated_prompt_tokens"]),
        "output_tokens": (
            normalized["exact_completion_tokens"]
            + normalized["estimated_completion_tokens"]),
    }


def project_costs(
        query_texts: Iterable[str], *,
        embedding_rate_per_million: float | None = None,
        embedding_requests: bool = True,
        llm_usage: dict | None = None,
        llm_input_rate_per_million: float | None = None,
        llm_output_rate_per_million: float | None = None) -> dict:
    """Project embedding/LLM cost using only explicitly supplied rates."""
    embedding_rate = _validate_rate(
        embedding_rate_per_million, "embedding_rate_per_million")
    input_rate = _validate_rate(
        llm_input_rate_per_million, "llm_input_rate_per_million")
    output_rate = _validate_rate(
        llm_output_rate_per_million, "llm_output_rate_per_million")
    texts = list(query_texts)
    if not all(isinstance(text, str) for text in texts):
        raise TypeError("query_texts must contain strings")
    embedding_tokens = (
        sum(estimate_text_tokens(text) for text in texts)
        if embedding_requests else 0)
    embedding_usd = (
        embedding_tokens * embedding_rate / 1_000_000
        if embedding_rate is not None else None)
    embedding = {
        "status": "estimated" if embedding_requests else "not_used",
        "requests": len(texts) if embedding_requests else 0,
        "input_tokens": embedding_tokens,
        "token_method": "characters/4 estimate",
        "rate_per_million_tokens": embedding_rate,
        "projected_usd": _rounded(embedding_usd, digits=9),
    }

    if llm_usage is None:
        llm = {
            "status": "not_run",
            "input_tokens": 0,
            "output_tokens": 0,
            "input_rate_per_million_tokens": input_rate,
            "output_rate_per_million_tokens": output_rate,
            "projected_usd": None,
        }
    else:
        required = (
            "input_tokens", "output_tokens", "exact_usage_attempts",
            "estimated_usage_attempts", "unknown_usage_attempts")
        if any(isinstance(llm_usage.get(key), bool)
               or not isinstance(llm_usage.get(key), int)
               or llm_usage[key] < 0 for key in required):
            raise ValueError("llm_usage contains invalid aggregate counts")
        total_attempts = (
            llm_usage["exact_usage_attempts"]
            + llm_usage["estimated_usage_attempts"]
            + llm_usage["unknown_usage_attempts"])
        if not total_attempts:
            status = "not_run"
        elif llm_usage["unknown_usage_attempts"]:
            status = "partial_unknown_usage"
        elif llm_usage["estimated_usage_attempts"]:
            status = "estimated"
        else:
            status = "exact"
        llm_usd = None
        if (status not in {"not_run", "partial_unknown_usage"}
                and input_rate is not None and output_rate is not None):
            llm_usd = (
                llm_usage["input_tokens"] * input_rate
                + llm_usage["output_tokens"] * output_rate) / 1_000_000
        llm = {
            "status": status,
            "input_tokens": llm_usage["input_tokens"],
            "output_tokens": llm_usage["output_tokens"],
            "exact_usage_attempts": llm_usage["exact_usage_attempts"],
            "estimated_usage_attempts": llm_usage[
                "estimated_usage_attempts"],
            "unknown_usage_attempts": llm_usage["unknown_usage_attempts"],
            "input_rate_per_million_tokens": input_rate,
            "output_rate_per_million_tokens": output_rate,
            "projected_usd": _rounded(llm_usd, digits=9),
            "report_sha256": llm_usage.get("report_sha256"),
        }
    return {
        "currency": "USD",
        "rates_source": "caller_supplied",
        "embedding": embedding,
        "llm": llm,
    }


def _validate_rate(value: float | None, name: str) -> float | None:
    if value is None:
        return None
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(float(value)) or value < 0):
        raise ValueError(f"{name} must be a finite number >= 0")
    return float(value)


def _rounded(value: float | None, *, digits: int = 3) -> float | None:
    return None if value is None else round(float(value), digits)


def _sha256_bytes(payload: bytes) -> str:
    import hashlib

    return hashlib.sha256(payload).hexdigest()
