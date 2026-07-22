"""Deterministic execution, caching, telemetry, and budgets for LLM calls.

The runtime deliberately knows nothing about any provider SDK. Callers supply
secret-free provider descriptors plus an invocation callback. Prompts and
credentials are never written to event logs or reports; successful response
text is persisted only when the content cache is enabled.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Literal

from run_telemetry import validate_distinct_output_paths
from storage_policy import (
    append_private_jsonl,
    atomic_write_private,
    ensure_private_tree,
)


CACHE_KEY_SCHEMA_VERSION = 1
CACHE_RECORD_SCHEMA_VERSION = 2
EVENT_SCHEMA_VERSION = 2
REPORT_SCHEMA_VERSION = 2
CACHE_MAX_BYTES = 32 * 1024 * 1024
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")

PROVIDER_ERROR_CATEGORIES = frozenset({
    "missing_credentials",
    "configuration_error",
    "timeout",
    "connection_error",
    "rate_limited",
    "authentication_error",
    "client_error",
    "server_error",
    "content_filtered",
    "invalid_response",
    "empty_response",
    "provider_error",
})
USAGE_SOURCES = frozenset({"exact", "estimated", "unavailable"})

CacheMode = Literal["readwrite", "readonly", "refresh", "off"]
FallbackPolicy = Literal["ordered", "none"]
FailurePolicy = Literal["best-effort", "strict"]


def default_cache_dir() -> Path:
    """Return a persistent machine-local cache path, overridable by env var."""
    configured = os.environ.get("RAG_LLM_CACHE_DIR")
    if configured:
        return Path(configured).expanduser()
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "rag-pipeline" / "llm-cache"
    xdg_cache = os.environ.get("XDG_CACHE_HOME")
    if xdg_cache:
        return Path(xdg_cache) / "rag-pipeline" / "llm-cache"
    return Path.home() / ".cache" / "rag-pipeline" / "llm-cache"


@dataclass(frozen=True)
class LLMRequest:
    """One deterministic generation request; prompt is hidden from repr."""

    prompt: str = field(repr=False)
    operation: str = "generic"
    prompt_version: str = "1"
    max_tokens: int = 256
    thinking: bool = False
    temperature: float = 0.0
    timeout: int = 30
    fallback_policy: FallbackPolicy | None = None
    failure_policy: FailurePolicy | None = None
    cache_mode: CacheMode | None = None
    cache_dir: Path | None = None


@dataclass(frozen=True)
class ProviderSpec:
    """A secret-free provider identity paired with an in-memory callback."""

    name: str
    model: str
    endpoint_id: str
    invoke: Callable[[LLMRequest], str | ProviderResponse | None] = field(
        repr=False, compare=False)


@dataclass(frozen=True)
class ProviderResponse:
    """Provider-native text plus optional exact usage metadata."""

    text: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cached_prompt_tokens: int | None = None
    reasoning_tokens: int | None = None
    transport_attempts: int = 1
    transient_error_categories: tuple[str, ...] = ()


class ProviderCallError(RuntimeError):
    """A normalized provider failure that never embeds raw response data."""

    def __init__(self, category: str, *, transport_attempts: int = 1):
        if category not in PROVIDER_ERROR_CATEGORIES:
            raise ValueError(f"invalid provider error category: {category}")
        if (isinstance(transport_attempts, bool)
                or not isinstance(transport_attempts, int)
                or transport_attempts < 0):
            raise ValueError("transport_attempts must be a non-negative integer")
        self.category = category.strip()
        self.transport_attempts = transport_attempts
        super().__init__(f"LLM provider call failed: {self.category}")


@dataclass(frozen=True)
class LLMAttempt:
    """Secret-free provenance for one provider in a fallback chain."""

    provider: str
    model: str
    succeeded: bool
    latency_ms: float
    error_category: str | None
    transport_attempts: int
    prompt_tokens: int
    completion_tokens: int
    cached_prompt_tokens: int
    reasoning_tokens: int
    usage_exact: bool
    usage_source: str
    transient_error_categories: tuple[str, ...] = ()


@dataclass(frozen=True)
class LLMResult:
    """Structured outcome for one logical request."""

    text: str
    request_id: str
    provider: str | None
    model: str | None
    latency_ms: float
    attempts: int
    fallback_path: tuple[str, ...]
    prompt_tokens: int
    completion_tokens: int
    usage_exact: bool
    cache_status: str
    error_category: str | None = None
    cached_prompt_tokens: int = 0
    reasoning_tokens: int = 0
    provider_attempts: tuple[LLMAttempt, ...] = ()
    usage_source: str = "unavailable"

    @property
    def transport_attempts(self) -> int:
        return sum(attempt.transport_attempts
                   for attempt in self.provider_attempts)

    @property
    def retry_attempts(self) -> int:
        return sum(max(0, attempt.transport_attempts - 1)
                   for attempt in self.provider_attempts)

    @property
    def succeeded(self) -> bool:
        return bool(self.text) and self.error_category is None


@dataclass(frozen=True)
class LLMRuntimeConfig:
    """Process-scoped runtime settings; all fields are safe to report."""

    cache_mode: CacheMode = "off"
    cache_dir: Path = field(default_factory=default_cache_dir)
    events_path: Path | None = None
    report_path: Path | None = None
    run_id: str | None = None
    max_provider_calls: int | None = None
    max_reserved_tokens: int | None = None
    fallback_policy: FallbackPolicy = "ordered"
    failure_policy: FailurePolicy = "best-effort"


class LLMBudgetExceeded(RuntimeError):
    """Raised internally when atomic admission would exceed a configured cap."""


class LLMExecutionError(RuntimeError):
    """Raised for a failed structured result when strict mode is requested."""

    def __init__(self, result: LLMResult):
        self.result = result
        super().__init__(
            f"LLM request {result.request_id} failed: "
            f"{result.error_category or 'unknown error'}")


@dataclass
class _Flight:
    event: threading.Event = field(default_factory=threading.Event)
    result: LLMResult | None = None


def _estimate_tokens(text: str) -> int:
    """Return a deliberately simple, provider-neutral token estimate."""
    if not text:
        return 0
    return max(1, math.ceil(len(text) / 4))


def _atomic_write_json(path: Path, payload: object, *, indent: int | None) -> None:
    def write(handle) -> None:
        json.dump(
            payload, handle, ensure_ascii=False, sort_keys=True,
            indent=indent, separators=None if indent else (",", ":"))
        handle.write("\n")

    atomic_write_private(path, write, text=True)


def _append_private_jsonl(path: Path, payload: dict) -> None:
    """Append one event through the shared no-follow private policy."""
    append_private_jsonl(path, payload)


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return round(ordered[index], 3)


class LLMRuntime:
    """Thread-safe LLM execution runtime with durable successful-result cache."""

    def __init__(self, config: LLMRuntimeConfig | None = None):
        self._lock = threading.RLock()
        self._event_lock = threading.Lock()
        self._flights: dict[str, _Flight] = {}
        self._active_calls = 0
        self.configure(config or LLMRuntimeConfig())

    @property
    def config(self) -> LLMRuntimeConfig:
        with self._lock:
            return self._config

    def configure(self, config: LLMRuntimeConfig) -> None:
        self._validate_config(config)
        with getattr(self, "_lock", threading.RLock()):
            if (getattr(self, "_flights", {})
                    or getattr(self, "_active_calls", 0)):
                raise RuntimeError("cannot reconfigure while LLM calls are active")
            self._config = config
            self._started_at = time.time()
            self._counts = {
                "requests": 0,
                "succeeded": 0,
                "failed": 0,
                "cache_hits": 0,
                "cache_misses": 0,
                "cache_shared": 0,
                "cache_corrupt": 0,
                "cache_read_errors": 0,
                "cache_write_errors": 0,
                "event_write_errors": 0,
                "provider_calls": 0,
                "transport_attempts": 0,
                "transport_retries": 0,
                "exact_usage_attempts": 0,
                "estimated_usage_attempts": 0,
                "unknown_usage_attempts": 0,
                "exact_prompt_tokens": 0,
                "exact_completion_tokens": 0,
                "estimated_prompt_tokens": 0,
                "estimated_completion_tokens": 0,
                "fallback_requests": 0,
                "budget_rejections": 0,
                "reserved_tokens": 0,
            }
            self._providers: dict[str, dict[str, Any]] = {}
            self._terminal_error_categories: dict[str, int] = {}
            self._request_latencies: list[float] = []

    @staticmethod
    def _validate_config(config: LLMRuntimeConfig) -> None:
        if config.cache_mode not in {"readwrite", "readonly", "refresh", "off"}:
            raise ValueError(f"invalid LLM cache mode: {config.cache_mode}")
        if config.fallback_policy not in {"ordered", "none"}:
            raise ValueError(f"invalid LLM fallback policy: {config.fallback_policy}")
        if config.failure_policy not in {"best-effort", "strict"}:
            raise ValueError(f"invalid LLM failure policy: {config.failure_policy}")
        for name, value in (
                ("max_provider_calls", config.max_provider_calls),
                ("max_reserved_tokens", config.max_reserved_tokens)):
            if value is not None and (
                    isinstance(value, bool) or not isinstance(value, int)
                    or value < 1):
                raise ValueError(f"{name} must be a positive integer")
        for name, value in (
                ("cache_dir", config.cache_dir),
                ("events_path", config.events_path),
                ("report_path", config.report_path)):
            if value is not None and not isinstance(value, Path):
                raise TypeError(f"{name} must be a pathlib.Path")
        if (config.run_id is not None
                and (not isinstance(config.run_id, str)
                     or not _RUN_ID.fullmatch(config.run_id))):
            raise ValueError(
                "run_id must be 1-128 safe identifier characters")
        validate_distinct_output_paths({
            "LLM events": config.events_path,
            "LLM report": config.report_path,
        })

    @staticmethod
    def _validate_request(request: LLMRequest) -> None:
        if not isinstance(request.prompt, str) or not request.prompt.strip():
            raise ValueError("LLM prompt must not be blank")
        if (isinstance(request.max_tokens, bool)
                or not isinstance(request.max_tokens, int)
                or request.max_tokens < 1):
            raise ValueError("LLM max_tokens must be a positive integer")
        if (isinstance(request.timeout, bool)
                or not isinstance(request.timeout, int)
                or request.timeout < 1):
            raise ValueError("LLM timeout must be a positive integer")
        if not isinstance(request.thinking, bool):
            raise TypeError("LLM thinking must be a boolean")
        if (isinstance(request.temperature, bool)
                or not isinstance(request.temperature, (int, float))
                or not math.isfinite(request.temperature)):
            raise ValueError("LLM temperature must be finite")
        if (not isinstance(request.operation, str)
                or not request.operation.strip()):
            raise ValueError("LLM operation must not be blank")
        if (not isinstance(request.prompt_version, str)
                or not request.prompt_version.strip()):
            raise ValueError("LLM prompt_version must not be blank")
        if request.fallback_policy not in {None, "ordered", "none"}:
            raise ValueError("fallback_policy must be 'ordered' or 'none'")
        if request.failure_policy not in {None, "best-effort", "strict"}:
            raise ValueError("failure_policy must be 'best-effort' or 'strict'")
        if request.cache_mode not in {None, "readwrite", "readonly", "refresh", "off"}:
            raise ValueError("invalid request cache mode")
        if request.cache_dir is not None and not isinstance(
                request.cache_dir, Path):
            raise TypeError("LLM cache_dir must be a pathlib.Path")

    @staticmethod
    def _validate_providers(providers: list[ProviderSpec]) -> None:
        if not isinstance(providers, list):
            raise TypeError("providers must be a list")
        for provider in providers:
            if not isinstance(provider, ProviderSpec):
                raise TypeError("providers must contain ProviderSpec values")
            for name, value in (
                    ("name", provider.name), ("model", provider.model),
                    ("endpoint_id", provider.endpoint_id)):
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(f"provider {name} must not be blank")
            if not callable(provider.invoke):
                raise TypeError("provider invoke must be callable")

    @staticmethod
    def _validate_provider_response(response: ProviderResponse) -> None:
        if not isinstance(response.text, str):
            raise ProviderCallError("invalid_response")
        for name, value in (
                ("prompt_tokens", response.prompt_tokens),
                ("completion_tokens", response.completion_tokens),
                ("cached_prompt_tokens", response.cached_prompt_tokens),
                ("reasoning_tokens", response.reasoning_tokens)):
            if value is not None and (
                    isinstance(value, bool) or not isinstance(value, int)
                    or value < 0):
                raise ProviderCallError("invalid_response")
        has_prompt = response.prompt_tokens is not None
        has_completion = response.completion_tokens is not None
        if has_prompt != has_completion:
            raise ProviderCallError("invalid_response")
        if ((response.cached_prompt_tokens is not None
             or response.reasoning_tokens is not None)
                and not has_prompt):
            raise ProviderCallError("invalid_response")
        if (isinstance(response.transport_attempts, bool)
                or not isinstance(response.transport_attempts, int)
                or response.transport_attempts < 1):
            raise ProviderCallError("invalid_response")
        if (not isinstance(response.transient_error_categories, tuple)
                or any(category not in PROVIDER_ERROR_CATEGORIES
                       for category in response.transient_error_categories)
                or len(response.transient_error_categories)
                > response.transport_attempts - 1):
            raise ProviderCallError("invalid_response")

    def _cache_key(self, request: LLMRequest,
                   providers: list[ProviderSpec]) -> str:
        material = {
            "schema_version": CACHE_KEY_SCHEMA_VERSION,
            "operation": request.operation,
            "prompt_sha256": hashlib.sha256(
                request.prompt.encode("utf-8")).hexdigest(),
            "prompt_version": request.prompt_version,
            "max_tokens": request.max_tokens,
            "thinking": request.thinking,
            "temperature": request.temperature,
            "timeout": request.timeout,
            "fallback_policy": request.fallback_policy,
            "providers": [
                {
                    "name": provider.name,
                    "model": provider.model,
                    "endpoint_id": provider.endpoint_id,
                }
                for provider in providers
            ],
        }
        encoded = json.dumps(
            material, ensure_ascii=False, sort_keys=True,
            separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _cache_path(cache_dir: Path, key: str) -> Path:
        return cache_dir / key[:2] / f"{key}.json"

    @staticmethod
    def _flight_key(key: str, *, cache_dir: Path,
                    mode: CacheMode) -> str:
        material = json.dumps({
            "cache_key": key,
            "cache_dir": str(cache_dir.resolve(strict=False)),
            "cache_mode": mode,
        }, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def _read_cache(self, path: Path, key: str,
                    request_id: str) -> LLMResult | None:
        if not path.is_file():
            return None
        try:
            if path.stat().st_size > CACHE_MAX_BYTES:
                raise ValueError("cache record exceeds size limit")
            payload = json.loads(path.read_text(encoding="utf-8"))
            result = payload["result"]
            record_version = payload.get("schema_version")
            result_digest = hashlib.sha256(json.dumps(
                result, ensure_ascii=False, sort_keys=True,
                separators=(",", ":")).encode("utf-8")).hexdigest()
            text = result.get("text") if isinstance(result, dict) else None
            fallback_path = (
                result.get("fallback_path") if isinstance(result, dict)
                else None)
            prompt_tokens = (
                result.get("prompt_tokens") if isinstance(result, dict)
                else None)
            completion_tokens = (
                result.get("completion_tokens") if isinstance(result, dict)
                else None)
            cached_prompt_tokens = (
                result.get("cached_prompt_tokens", 0)
                if isinstance(result, dict) else None)
            reasoning_tokens = (
                result.get("reasoning_tokens", 0)
                if isinstance(result, dict) else None)
            usage_exact = (
                result.get("usage_exact") if isinstance(result, dict)
                else None)
            usage_source = (
                result.get("usage_source") if isinstance(result, dict)
                else None)
            if usage_source is None and isinstance(usage_exact, bool):
                usage_source = "exact" if usage_exact else "estimated"
            if (record_version not in {1, 2}
                    or payload.get("cache_key") != key
                    or (record_version == 2
                        and payload.get("result_sha256") != result_digest)
                    or not isinstance(result, dict)
                    or not isinstance(text, str)
                    or not text.strip()
                    or payload.get("response_sha256") != hashlib.sha256(
                        text.encode("utf-8")).hexdigest()
                    or not isinstance(result.get("provider"), str)
                    or not result["provider"].strip()
                    or not isinstance(result.get("model"), str)
                    or not result["model"].strip()
                    or not isinstance(fallback_path, list)
                    or not all(isinstance(item, str) and item.strip()
                               for item in fallback_path)
                    or isinstance(prompt_tokens, bool)
                    or not isinstance(prompt_tokens, int)
                    or prompt_tokens < 0
                    or isinstance(completion_tokens, bool)
                    or not isinstance(completion_tokens, int)
                    or completion_tokens < 0
                    or isinstance(cached_prompt_tokens, bool)
                    or not isinstance(cached_prompt_tokens, int)
                    or cached_prompt_tokens < 0
                    or isinstance(reasoning_tokens, bool)
                    or not isinstance(reasoning_tokens, int)
                    or reasoning_tokens < 0
                    or not isinstance(usage_exact, bool)
                    or usage_source not in USAGE_SOURCES
                    or usage_exact != (usage_source == "exact")):
                raise ValueError("cache record failed validation")
            return LLMResult(
                text=text.strip(), request_id=request_id,
                provider=result["provider"], model=result["model"],
                latency_ms=0.0, attempts=0,
                fallback_path=tuple(fallback_path),
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                usage_exact=usage_exact,
                cache_status="hit", error_category=None,
                cached_prompt_tokens=cached_prompt_tokens,
                reasoning_tokens=reasoning_tokens,
                usage_source=usage_source,
            )
        except OSError:
            with self._lock:
                self._counts["cache_read_errors"] += 1
            return None
        except (UnicodeError, json.JSONDecodeError, KeyError,
                TypeError, ValueError):
            with self._lock:
                self._counts["cache_corrupt"] += 1
            return None

    @staticmethod
    def _write_cache(path: Path, key: str, result: LLMResult) -> None:
        result_payload = {
            "text": result.text,
            "provider": result.provider,
            "model": result.model,
            "fallback_path": list(result.fallback_path),
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            "cached_prompt_tokens": result.cached_prompt_tokens,
            "reasoning_tokens": result.reasoning_tokens,
            "usage_exact": result.usage_exact,
            "usage_source": result.usage_source,
        }
        payload = {
            "schema_version": CACHE_RECORD_SCHEMA_VERSION,
            "cache_key": key,
            "response_sha256": hashlib.sha256(
                result.text.encode("utf-8")).hexdigest(),
            "result_sha256": hashlib.sha256(json.dumps(
                result_payload, ensure_ascii=False, sort_keys=True,
                separators=(",", ":")).encode("utf-8")).hexdigest(),
            "result": result_payload,
        }
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True,
            separators=(",", ":")).encode("utf-8")
        if len(encoded) > CACHE_MAX_BYTES:
            raise ValueError("cache record exceeds size limit")
        _atomic_write_json(path, payload, indent=None)

    def _reserve_provider_call(self, provider: str,
                               worst_case_tokens: int) -> None:
        with self._lock:
            next_calls = self._counts["provider_calls"] + 1
            next_tokens = self._counts["reserved_tokens"] + worst_case_tokens
            if (self._config.max_provider_calls is not None
                    and next_calls > self._config.max_provider_calls):
                self._counts["budget_rejections"] += 1
                raise LLMBudgetExceeded("provider-call budget exhausted")
            if (self._config.max_reserved_tokens is not None
                    and next_tokens > self._config.max_reserved_tokens):
                self._counts["budget_rejections"] += 1
                raise LLMBudgetExceeded("token budget exhausted")
            self._counts["provider_calls"] = next_calls
            self._counts["reserved_tokens"] = next_tokens
            metrics = self._providers.setdefault(provider, {
                "attempts": 0, "succeeded": 0, "failed": 0,
                "transport_attempts": 0,
                "transport_retries": 0,
                "latency_ms": 0.0, "prompt_tokens": 0,
                "completion_tokens": 0,
                "cached_prompt_tokens": 0, "reasoning_tokens": 0,
                "exact_prompt_tokens": 0,
                "exact_completion_tokens": 0,
                "estimated_prompt_tokens": 0,
                "estimated_completion_tokens": 0,
                "exact_usage_attempts": 0,
                "estimated_usage_attempts": 0,
                "unknown_usage_attempts": 0,
                "error_categories": {},
            })
            metrics["attempts"] += 1

    def _record_provider_outcome(
            self, provider: str, *, succeeded: bool, latency_ms: float,
            transport_attempts: int, prompt_tokens: int,
            completion_tokens: int, cached_prompt_tokens: int = 0,
            reasoning_tokens: int = 0, usage_source: str,
            error_category: str | None = None,
            transient_error_categories: tuple[str, ...] = ()) -> None:
        with self._lock:
            metrics = self._providers[provider]
            retry_attempts = max(0, transport_attempts - 1)
            metrics["succeeded" if succeeded else "failed"] += 1
            metrics["latency_ms"] += latency_ms
            metrics["transport_attempts"] += transport_attempts
            metrics["transport_retries"] += retry_attempts
            metrics["prompt_tokens"] += prompt_tokens
            metrics["completion_tokens"] += completion_tokens
            metrics["cached_prompt_tokens"] += cached_prompt_tokens
            metrics["reasoning_tokens"] += reasoning_tokens
            usage_key = {
                "exact": "exact_usage_attempts",
                "estimated": "estimated_usage_attempts",
                "unavailable": "unknown_usage_attempts",
            }[usage_source]
            metrics[usage_key] += 1
            if usage_source in {"exact", "estimated"}:
                token_prefix = usage_source
                prompt_key = f"{token_prefix}_prompt_tokens"
                completion_key = f"{token_prefix}_completion_tokens"
                metrics[prompt_key] += prompt_tokens
                metrics[completion_key] += completion_tokens
                self._counts[prompt_key] += prompt_tokens
                self._counts[completion_key] += completion_tokens
            self._counts["transport_attempts"] += transport_attempts
            self._counts["transport_retries"] += retry_attempts
            self._counts[usage_key] += 1
            categories = list(transient_error_categories)
            if error_category is not None:
                categories.append(error_category)
            for category in categories:
                errors = metrics["error_categories"]
                errors[category] = errors.get(category, 0) + 1

    @staticmethod
    def _error_category(exc: BaseException) -> str:
        if isinstance(exc, ProviderCallError):
            return exc.category
        name = type(exc).__name__.casefold()
        message = str(exc).casefold()
        if "timeout" in name or "timed out" in message:
            return "timeout"
        if "429" in message or "rate" in message and "limit" in message:
            return "rate_limited"
        return "provider_error"

    def _execute_uncached(
            self, request: LLMRequest, providers: list[ProviderSpec], *,
            request_id: str, cache_status: str) -> LLMResult:
        started = time.perf_counter()
        estimated_prompt_tokens = _estimate_tokens(request.prompt)
        fallback_path: list[str] = []
        attempt_records: list[LLMAttempt] = []
        last_error = "no_provider"
        selected = providers[:1] if request.fallback_policy == "none" else providers

        for provider in selected:
            try:
                self._reserve_provider_call(
                    provider.name,
                    estimated_prompt_tokens + request.max_tokens)
            except LLMBudgetExceeded:
                last_error = "budget_exceeded"
                break
            fallback_path.append(provider.name)

            attempt_started = time.perf_counter()
            try:
                raw_response = provider.invoke(request)
                if isinstance(raw_response, ProviderResponse):
                    self._validate_provider_response(raw_response)
                    normalized = raw_response.text.strip()
                    transport_attempts = raw_response.transport_attempts
                    prompt_tokens = (
                        raw_response.prompt_tokens
                        if raw_response.prompt_tokens is not None
                        else estimated_prompt_tokens)
                    completion_tokens = (
                        raw_response.completion_tokens
                        if raw_response.completion_tokens is not None
                        else _estimate_tokens(normalized))
                    cached_prompt_tokens = (
                        raw_response.cached_prompt_tokens or 0)
                    reasoning_tokens = raw_response.reasoning_tokens or 0
                    usage_source = (
                        "exact" if raw_response.prompt_tokens is not None
                        else "estimated")
                    transient_errors = (
                        raw_response.transient_error_categories)
                elif isinstance(raw_response, str):
                    normalized = raw_response.strip()
                    transport_attempts = 1
                    prompt_tokens = estimated_prompt_tokens
                    completion_tokens = _estimate_tokens(normalized)
                    cached_prompt_tokens = 0
                    reasoning_tokens = 0
                    usage_source = "estimated"
                    transient_errors = ()
                elif raw_response is None:
                    normalized = ""
                    transport_attempts = 1
                    prompt_tokens = 0
                    completion_tokens = 0
                    cached_prompt_tokens = 0
                    reasoning_tokens = 0
                    usage_source = "unavailable"
                    transient_errors = ()
                else:
                    raise ProviderCallError("invalid_response")
            except LLMBudgetExceeded:
                last_error = "budget_exceeded"
                break
            except Exception as exc:  # Provider boundary is intentionally broad.
                latency = (time.perf_counter() - attempt_started) * 1000
                category = self._error_category(exc)
                transport_attempts = (
                    exc.transport_attempts
                    if isinstance(exc, ProviderCallError) else 1)
                self._record_provider_outcome(
                    provider.name, succeeded=False, latency_ms=latency,
                    transport_attempts=transport_attempts,
                    prompt_tokens=0, completion_tokens=0,
                    usage_source="unavailable",
                    error_category=category)
                attempt_records.append(LLMAttempt(
                    provider=provider.name, model=provider.model,
                    succeeded=False, latency_ms=latency,
                    error_category=category,
                    transport_attempts=transport_attempts,
                    prompt_tokens=0,
                    completion_tokens=0, cached_prompt_tokens=0,
                    reasoning_tokens=0, usage_exact=False,
                    usage_source="unavailable"))
                last_error = category
                continue

            latency = (time.perf_counter() - attempt_started) * 1000
            if normalized:
                self._record_provider_outcome(
                    provider.name, succeeded=True, latency_ms=latency,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    cached_prompt_tokens=cached_prompt_tokens,
                    reasoning_tokens=reasoning_tokens,
                    transport_attempts=transport_attempts,
                    usage_source=usage_source,
                    transient_error_categories=transient_errors)
                attempt_records.append(LLMAttempt(
                    provider=provider.name, model=provider.model,
                    succeeded=True, latency_ms=latency,
                    error_category=None,
                    transport_attempts=transport_attempts,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    cached_prompt_tokens=cached_prompt_tokens,
                    reasoning_tokens=reasoning_tokens,
                    usage_exact=usage_source == "exact",
                    usage_source=usage_source,
                    transient_error_categories=transient_errors))
                return LLMResult(
                    text=normalized, request_id=request_id,
                    provider=provider.name, model=provider.model,
                    latency_ms=(time.perf_counter() - started) * 1000,
                    attempts=len(attempt_records),
                    fallback_path=tuple(fallback_path),
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    usage_exact=usage_source == "exact",
                    cache_status=cache_status,
                    error_category=None,
                    cached_prompt_tokens=cached_prompt_tokens,
                    reasoning_tokens=reasoning_tokens,
                    provider_attempts=tuple(attempt_records),
                    usage_source=usage_source,
                )
            self._record_provider_outcome(
                provider.name, succeeded=False, latency_ms=latency,
                transport_attempts=transport_attempts,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cached_prompt_tokens=cached_prompt_tokens,
                reasoning_tokens=reasoning_tokens,
                usage_source=usage_source,
                error_category="empty_response",
                transient_error_categories=transient_errors)
            attempt_records.append(LLMAttempt(
                provider=provider.name, model=provider.model,
                succeeded=False, latency_ms=latency,
                error_category="empty_response",
                transport_attempts=transport_attempts,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cached_prompt_tokens=cached_prompt_tokens,
                reasoning_tokens=reasoning_tokens,
                usage_exact=usage_source == "exact",
                usage_source=usage_source,
                transient_error_categories=transient_errors))
            last_error = "empty_response"

        return LLMResult(
            text="", request_id=request_id, provider=None, model=None,
            latency_ms=(time.perf_counter() - started) * 1000,
            attempts=len(attempt_records), fallback_path=tuple(fallback_path),
            prompt_tokens=0, completion_tokens=0,
            usage_exact=False, cache_status=cache_status,
            error_category=last_error,
            provider_attempts=tuple(attempt_records),
            usage_source="unavailable",
        )

    def _join_flight(self, key: str) -> tuple[_Flight, bool]:
        with self._lock:
            existing = self._flights.get(key)
            if existing is not None:
                return existing, False
            flight = _Flight()
            self._flights[key] = flight
            return flight, True

    def _record_result(self, result: LLMResult, request: LLMRequest) -> None:
        event = {
            "schema_version": EVENT_SCHEMA_VERSION,
            "timestamp": time.time(),
            "request_id": result.request_id,
            "operation": request.operation,
            "provider": result.provider,
            "model": result.model,
            "latency_ms": round(result.latency_ms, 3),
            "attempts": result.attempts,
            "fallback_path": list(result.fallback_path),
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            "cached_prompt_tokens": result.cached_prompt_tokens,
            "reasoning_tokens": result.reasoning_tokens,
            "usage_exact": result.usage_exact,
            "usage_source": result.usage_source,
            "transport_attempts": result.transport_attempts,
            "retry_attempts": result.retry_attempts,
            "provider_attempts": [
                {
                    "provider": attempt.provider,
                    "model": attempt.model,
                    "succeeded": attempt.succeeded,
                    "latency_ms": round(attempt.latency_ms, 3),
                    "error_category": attempt.error_category,
                    "transport_attempts": attempt.transport_attempts,
                    "prompt_tokens": attempt.prompt_tokens,
                    "completion_tokens": attempt.completion_tokens,
                    "cached_prompt_tokens": attempt.cached_prompt_tokens,
                    "reasoning_tokens": attempt.reasoning_tokens,
                    "usage_exact": attempt.usage_exact,
                    "usage_source": attempt.usage_source,
                    "transient_error_categories": list(
                        attempt.transient_error_categories),
                }
                for attempt in result.provider_attempts
            ],
            "cache_status": result.cache_status,
            "error_category": result.error_category,
            "succeeded": result.succeeded,
        }
        with self._lock:
            self._counts["requests"] += 1
            self._counts["succeeded" if result.succeeded else "failed"] += 1
            if result.cache_status == "hit":
                self._counts["cache_hits"] += 1
            elif result.cache_status == "shared":
                self._counts["cache_shared"] += 1
            elif result.cache_status in {"miss", "refresh"}:
                self._counts["cache_misses"] += 1
            if result.attempts > 1:
                self._counts["fallback_requests"] += 1
            if result.error_category is not None:
                category = result.error_category
                self._terminal_error_categories[category] = (
                    self._terminal_error_categories.get(category, 0) + 1)
            self._request_latencies.append(result.latency_ms)
            events_path = self._config.events_path
            run_id = self._config.run_id
        if run_id is not None:
            event["run_id"] = run_id
        if events_path is not None:
            try:
                with self._event_lock:
                    _append_private_jsonl(events_path, event)
            except Exception:
                with self._lock:
                    self._counts["event_write_errors"] += 1

    @staticmethod
    def _finish(result: LLMResult, request: LLMRequest) -> LLMResult:
        if result.error_category == "budget_exceeded":
            raise LLMBudgetExceeded(
                f"LLM budget exhausted before request {result.request_id} "
                "could complete")
        if not result.succeeded and request.failure_policy == "strict":
            raise LLMExecutionError(result)
        return result

    def execute(self, request: LLMRequest,
                providers: list[ProviderSpec]) -> LLMResult:
        """Execute or reuse one request and always return structured metadata."""
        with self._lock:
            self._active_calls += 1
        try:
            return self._execute(request, providers)
        finally:
            with self._lock:
                self._active_calls -= 1

    def _execute(self, request: LLMRequest,
                 providers: list[ProviderSpec]) -> LLMResult:
        config = self.config
        request = replace(
            request,
            fallback_policy=(
                request.fallback_policy or config.fallback_policy),
            failure_policy=(
                request.failure_policy or config.failure_policy),
        )
        self._validate_request(request)
        self._validate_providers(providers)
        mode = request.cache_mode or config.cache_mode
        cache_dir = request.cache_dir or config.cache_dir
        if mode != "off" and cache_dir.exists():
            ensure_private_tree(cache_dir)
        key = self._cache_key(request, providers)
        flight_key = self._flight_key(
            key, cache_dir=cache_dir, mode=mode)
        request_id = f"llm_{key[:16]}"
        cache_path = self._cache_path(cache_dir, key)

        if mode in {"readwrite", "readonly"}:
            cached = self._read_cache(cache_path, key, request_id)
            if cached is not None:
                self._record_result(cached, request)
                return self._finish(cached, request)

        flight, owner = self._join_flight(flight_key)
        if not owner:
            wait_started = time.perf_counter()
            flight.event.wait()
            wait_latency = (time.perf_counter() - wait_started) * 1000
            if flight.result is None:
                result = LLMResult(
                    text="", request_id=request_id, provider=None, model=None,
                    latency_ms=wait_latency, attempts=0, fallback_path=(),
                    prompt_tokens=_estimate_tokens(request.prompt),
                    completion_tokens=0, usage_exact=False,
                    cache_status="shared", error_category="runtime_error")
            else:
                result = replace(
                    flight.result, latency_ms=wait_latency, attempts=0,
                    cache_status="shared", provider_attempts=())
            self._record_result(result, request)
            return self._finish(result, request)

        status = "refresh" if mode == "refresh" else (
            "disabled" if mode == "off" else "miss")
        try:
            result = self._execute_uncached(
                request, providers, request_id=request_id,
                cache_status=status)
            if result.succeeded and mode in {"readwrite", "refresh"}:
                try:
                    self._write_cache(cache_path, key, result)
                except Exception:
                    # A cache is an optimization. A persistence failure must
                    # never replace a successful live provider response.
                    with self._lock:
                        self._counts["cache_write_errors"] += 1
            flight.result = result
        except Exception as exc:  # Guarantees waiters are always released.
            result = LLMResult(
                text="", request_id=request_id, provider=None, model=None,
                latency_ms=0.0, attempts=0, fallback_path=(),
                prompt_tokens=_estimate_tokens(request.prompt),
                completion_tokens=0, usage_exact=False,
                cache_status=status, error_category=self._error_category(exc))
            flight.result = result
        finally:
            with self._lock:
                self._flights.pop(flight_key, None)
                flight.event.set()

        self._record_result(result, request)
        return self._finish(result, request)

    def report_payload(self) -> dict:
        """Return a secret- and prompt-free aggregate runtime report."""
        with self._lock:
            config = self._config
            providers = {
                name: {
                    key: (
                        round(value, 3) if isinstance(value, float)
                        else dict(sorted(value.items()))
                        if isinstance(value, dict) else value)
                    for key, value in metrics.items()
                }
                for name, metrics in self._providers.items()
            }
            payload = {
                "schema_version": REPORT_SCHEMA_VERSION,
                "started_at": self._started_at,
                "finished_at": time.time(),
                "configuration": {
                    "cache_mode": config.cache_mode,
                    "cache_dir": str(config.cache_dir),
                    "fallback_policy": config.fallback_policy,
                    "failure_policy": config.failure_policy,
                    "max_provider_calls": config.max_provider_calls,
                    "max_reserved_tokens": config.max_reserved_tokens,
                },
                "accounting": {
                    "provider_calls": "logical fallback-chain dispatches",
                    "transport_attempts": (
                        "underlying adapter requests, including retries"),
                    "token_usage": (
                        "provider-reported when exact; otherwise characters/4"),
                },
                "counts": dict(self._counts),
                "terminal_error_categories": dict(sorted(
                    self._terminal_error_categories.items())),
                "latency_ms": {
                    "p50": _percentile(self._request_latencies, 0.50),
                    "p95": _percentile(self._request_latencies, 0.95),
                },
                "providers": providers,
            }
            if config.run_id is not None:
                payload["run_id"] = config.run_id
            return payload

    def write_report(self) -> Path | None:
        """Atomically write the configured report, if one was requested."""
        path = self.config.report_path
        if path is None:
            return None
        _atomic_write_json(path, self.report_payload(), indent=2)
        return path
