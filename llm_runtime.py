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

from llm_output_contracts import (
    INTERNAL_CONTRACT_ERROR,
    OutputContractRejected,
)
from run_telemetry import validate_distinct_output_paths
from storage_policy import (
    append_private_jsonl,
    atomic_write_private,
    ensure_private_tree,
)


CACHE_KEY_SCHEMA_VERSION = 3
CACHE_RECORD_SCHEMA_VERSION = 4
EVENT_SCHEMA_VERSION = 4
REPORT_SCHEMA_VERSION = 5
CACHE_MAX_BYTES = 32 * 1024 * 1024
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_CACHE_NAMESPACE_ID = re.compile(r"v[1-9][0-9]*:(?:default|sha256:[0-9a-f]{64})")
_OUTPUT_CONTRACT_ID = re.compile(
    r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*")
DEFAULT_CACHE_NAMESPACE_ID = "v1:default"


def _is_output_contract_id(value: object) -> bool:
    return (isinstance(value, str) and len(value) <= 128
            and _OUTPUT_CONTRACT_ID.fullmatch(value) is not None)

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
OutputContractStatus = Literal["accepted", "rejected", "not_evaluated"]


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
    output_contract_id: str | None = None
    output_fallback_id: str | None = None
    output_validator: Callable[[str], str] | None = field(
        default=None, repr=False, compare=False)
    _transport_retry_admission: Callable[[], None] | None = field(
        default=None, repr=False, compare=False)

    def admit_transport_retry(self) -> None:
        """Atomically admit one additional provider transport attempt."""
        if self._transport_retry_admission is None:
            raise RuntimeError(
                "transport retry admission is only available inside an "
                "LLMRuntime provider callback")
        self._transport_retry_admission()


@dataclass(frozen=True)
class ProviderSpec:
    """A secret-free provider identity paired with an in-memory callback."""

    name: str
    model: str
    endpoint_id: str
    invoke: Callable[[LLMRequest], str | ProviderResponse | None] = field(
        repr=False, compare=False)
    cache_namespace_id: str = DEFAULT_CACHE_NAMESPACE_ID


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
    cache_namespace_id: str | None = None
    output_contract_id: str | None = None
    output_contract_status: OutputContractStatus | None = None
    output_diagnostic_code: str | None = None
    output_fallback_id: str | None = None

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
    max_transport_attempts: int | None = None


class LLMBudgetExceeded(RuntimeError):
    """Raised internally when atomic admission would exceed a configured cap."""

    def __init__(self, message: str, *, transport_attempts: int = 0):
        self.transport_attempts = transport_attempts
        super().__init__(message)


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
                "transport_admissions": 0,
                "transport_contract_violations": 0,
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
                "output_contract_accepted": 0,
                "output_contract_rejected": 0,
                "output_contract_not_evaluated": 0,
                "output_contract_fallbacks": 0,
            }
            self._providers: dict[str, dict[str, Any]] = {}
            self._output_contracts: dict[
                tuple[str, str], dict[str, Any]
            ] = {}
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
                ("max_transport_attempts", config.max_transport_attempts),
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
        contract_fields = (
            request.output_contract_id,
            request.output_fallback_id,
            request.output_validator,
        )
        if request.output_contract_id is None:
            if any(value is not None for value in contract_fields[1:]):
                raise ValueError(
                    "output contract fields require output_contract_id")
        else:
            if not _is_output_contract_id(request.output_contract_id):
                raise ValueError("output_contract_id is invalid")
            if not _is_output_contract_id(request.output_fallback_id):
                raise ValueError("output_fallback_id is invalid")
            if not callable(request.output_validator):
                raise TypeError("output_validator must be callable")
            if (getattr(request.output_validator, "contract_id", None)
                    != request.output_contract_id):
                raise ValueError(
                    "output_validator contract_id must match the request")
        if (request._transport_retry_admission is not None
                and not callable(request._transport_retry_admission)):
            raise TypeError("transport retry admission must be callable")

    @staticmethod
    def _validate_providers(providers: list[ProviderSpec]) -> None:
        if not isinstance(providers, list):
            raise TypeError("providers must be a list")
        for provider in providers:
            if not isinstance(provider, ProviderSpec):
                raise TypeError("providers must contain ProviderSpec values")
            for name, value in (
                    ("name", provider.name), ("model", provider.model),
                    ("endpoint_id", provider.endpoint_id),
                    ("cache_namespace_id", provider.cache_namespace_id)):
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(f"provider {name} must not be blank")
            if _CACHE_NAMESPACE_ID.fullmatch(
                    provider.cache_namespace_id) is None:
                raise ValueError("provider cache_namespace_id is invalid")
            if not callable(provider.invoke):
                raise TypeError("provider invoke must be callable")

    @staticmethod
    def _validate_provider_transport_attempts(response: ProviderResponse) -> None:
        if (isinstance(response.transport_attempts, bool)
                or not isinstance(response.transport_attempts, int)
                or response.transport_attempts < 1):
            raise ProviderCallError("invalid_response")

    @classmethod
    def _validate_provider_response(cls, response: ProviderResponse) -> None:
        cls._validate_provider_transport_attempts(response)
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
            "output_contract_id": request.output_contract_id,
            "output_fallback_id": request.output_fallback_id,
            "providers": [
                {
                    "name": provider.name,
                    "model": provider.model,
                    "endpoint_id": provider.endpoint_id,
                    "cache_namespace_id": provider.cache_namespace_id,
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

    def _read_cache(self, path: Path, key: str, request_id: str,
                    request: LLMRequest) -> LLMResult | None:
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
            cache_namespace_id = (
                result.get("cache_namespace_id")
                if isinstance(result, dict) else None)
            output_contract_id = (
                result.get("output_contract_id")
                if isinstance(result, dict) else None)
            output_contract_status = (
                result.get("output_contract_status")
                if isinstance(result, dict) else None)
            output_diagnostic_code = (
                result.get("output_diagnostic_code")
                if isinstance(result, dict) else None)
            output_fallback_id = (
                result.get("output_fallback_id")
                if isinstance(result, dict) else None)
            if usage_source is None and isinstance(usage_exact, bool):
                usage_source = "exact" if usage_exact else "estimated"
            if (record_version != CACHE_RECORD_SCHEMA_VERSION
                    or payload.get("cache_key") != key
                    or payload.get("result_sha256") != result_digest
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
                    or usage_exact != (usage_source == "exact")
                    or not isinstance(cache_namespace_id, str)
                    or _CACHE_NAMESPACE_ID.fullmatch(
                        cache_namespace_id) is None
                    or output_contract_id != request.output_contract_id
                    or output_fallback_id != request.output_fallback_id
                    or output_diagnostic_code is not None
                    or output_contract_status != (
                        "accepted"
                        if request.output_contract_id is not None else None)):
                raise ValueError("cache record failed validation")
            normalized = text.strip()
            if request.output_validator is not None:
                if text != normalized:
                    raise ValueError(
                        "cached output is not canonical")
                try:
                    validated = request.output_validator(normalized)
                except Exception:
                    raise ValueError(
                        "cached output failed its contract") from None
                if not isinstance(validated, str) or validated != normalized:
                    raise ValueError(
                        "cached output is not canonical")
            return LLMResult(
                text=normalized, request_id=request_id,
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
                cache_namespace_id=cache_namespace_id,
                output_contract_id=output_contract_id,
                output_contract_status=output_contract_status,
                output_diagnostic_code=output_diagnostic_code,
                output_fallback_id=output_fallback_id,
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
            "cache_namespace_id": result.cache_namespace_id,
            "output_contract_id": result.output_contract_id,
            "output_contract_status": result.output_contract_status,
            "output_diagnostic_code": result.output_diagnostic_code,
            "output_fallback_id": result.output_fallback_id,
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
            next_transports = self._counts["transport_admissions"] + 1
            if (self._config.max_provider_calls is not None
                    and next_calls > self._config.max_provider_calls):
                self._counts["budget_rejections"] += 1
                raise LLMBudgetExceeded("provider-call budget exhausted")
            if (self._config.max_reserved_tokens is not None
                    and next_tokens > self._config.max_reserved_tokens):
                self._counts["budget_rejections"] += 1
                raise LLMBudgetExceeded("token budget exhausted")
            if (self._config.max_transport_attempts is not None
                    and next_transports
                    > self._config.max_transport_attempts):
                self._counts["budget_rejections"] += 1
                raise LLMBudgetExceeded("transport-attempt budget exhausted")
            self._counts["provider_calls"] = next_calls
            self._counts["reserved_tokens"] = next_tokens
            self._counts["transport_admissions"] = next_transports
            metrics = self._providers.setdefault(provider, {
                "attempts": 0, "succeeded": 0, "failed": 0,
                "transport_admissions": 0,
                "transport_contract_violations": 0,
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
                "cache_namespace_ids": set(),
            })
            metrics["attempts"] += 1
            metrics["transport_admissions"] += 1

    def _reserve_transport_retry(self, provider: str) -> None:
        """Atomically reserve one retry without another logical dispatch."""
        with self._lock:
            next_transports = self._counts["transport_admissions"] + 1
            if (self._config.max_transport_attempts is not None
                    and next_transports
                    > self._config.max_transport_attempts):
                self._counts["budget_rejections"] += 1
                raise LLMBudgetExceeded("transport-attempt budget exhausted")
            self._counts["transport_admissions"] = next_transports
            self._providers[provider]["transport_admissions"] += 1

    def _require_transport_admissions(
            self, provider: str, *, reported: int, admitted: int) -> None:
        """Fail closed when a capped callback reports an unadmitted retry."""
        with self._lock:
            if (self._config.max_transport_attempts is None
                    or reported <= admitted):
                return
            self._counts["transport_contract_violations"] += 1
            self._counts["budget_rejections"] += 1
            self._providers[provider]["transport_contract_violations"] += 1
        raise LLMBudgetExceeded(
            "provider reported an unadmitted transport attempt",
            transport_attempts=reported)

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

    @staticmethod
    def _apply_output_contract(
            request: LLMRequest, text: str,
    ) -> tuple[str, str | None, str | None]:
        """Canonicalize generated text without retaining rejected content."""
        if request.output_validator is None:
            return text, None, None
        try:
            canonical = request.output_validator(text)
        except OutputContractRejected as exc:
            return "", "rejected", exc.diagnostic_code
        except Exception:
            return "", "rejected", INTERNAL_CONTRACT_ERROR
        if (not isinstance(canonical, str) or not canonical
                or canonical != canonical.strip()):
            return "", "rejected", INTERNAL_CONTRACT_ERROR
        return canonical, "accepted", None

    @staticmethod
    def _bind_output_contract(
            result: LLMResult, request: LLMRequest) -> LLMResult:
        """Stamp a validated request contract onto every terminal outcome."""
        if request.output_contract_id is None:
            return result
        return replace(
            result,
            output_contract_id=request.output_contract_id,
            output_contract_status=(
                result.output_contract_status or "not_evaluated"),
            output_fallback_id=request.output_fallback_id,
        )

    @classmethod
    def _revalidate_shared_output(
            cls, result: LLMResult, request: LLMRequest) -> LLMResult:
        """Prevent a same-key waiter from trusting another validator blindly."""
        if not result.succeeded or request.output_validator is None:
            return result
        canonical, status, diagnostic_code = cls._apply_output_contract(
            request, result.text)
        if status == "accepted" and canonical == result.text:
            return result
        return replace(
            result,
            text="",
            provider=None,
            model=None,
            error_category="invalid_response",
            output_contract_status="rejected",
            output_diagnostic_code=(
                diagnostic_code or INTERNAL_CONTRACT_ERROR),
        )

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
            with self._lock:
                self._providers[provider.name]["cache_namespace_ids"].add(
                    provider.cache_namespace_id)
            fallback_path.append(provider.name)

            attempt_started = time.perf_counter()
            admitted_transport_attempts = 1

            def admit_transport_retry() -> None:
                nonlocal admitted_transport_attempts
                self._reserve_transport_retry(provider.name)
                admitted_transport_attempts += 1

            provider_request = replace(
                request,
                _transport_retry_admission=admit_transport_retry,
            )

            def record_budget_exhaustion(exc: LLMBudgetExceeded) -> None:
                latency = (time.perf_counter() - attempt_started) * 1000
                observed_attempts = max(
                    admitted_transport_attempts, exc.transport_attempts)
                self._record_provider_outcome(
                    provider.name, succeeded=False, latency_ms=latency,
                    transport_attempts=observed_attempts,
                    prompt_tokens=0, completion_tokens=0,
                    usage_source="unavailable",
                    error_category="budget_exceeded")
                attempt_records.append(LLMAttempt(
                    provider=provider.name, model=provider.model,
                    succeeded=False, latency_ms=latency,
                    error_category="budget_exceeded",
                    transport_attempts=observed_attempts,
                    prompt_tokens=0, completion_tokens=0,
                    cached_prompt_tokens=0, reasoning_tokens=0,
                    usage_exact=False, usage_source="unavailable"))

            try:
                raw_response = provider.invoke(provider_request)
                if isinstance(raw_response, ProviderResponse):
                    self._validate_provider_transport_attempts(raw_response)
                    self._require_transport_admissions(
                        provider.name,
                        reported=raw_response.transport_attempts,
                        admitted=admitted_transport_attempts)
                    self._validate_provider_response(raw_response)
                    generated_text = raw_response.text
                    transport_attempts = max(
                        raw_response.transport_attempts,
                        admitted_transport_attempts)
                    prompt_tokens = (
                        raw_response.prompt_tokens
                        if raw_response.prompt_tokens is not None
                        else estimated_prompt_tokens)
                    completion_tokens = (
                        raw_response.completion_tokens
                        if raw_response.completion_tokens is not None
                        else _estimate_tokens(generated_text))
                    cached_prompt_tokens = (
                        raw_response.cached_prompt_tokens or 0)
                    reasoning_tokens = raw_response.reasoning_tokens or 0
                    usage_source = (
                        "exact" if raw_response.prompt_tokens is not None
                        else "estimated")
                    transient_errors = (
                        raw_response.transient_error_categories)
                elif isinstance(raw_response, str):
                    generated_text = raw_response
                    transport_attempts = admitted_transport_attempts
                    prompt_tokens = estimated_prompt_tokens
                    completion_tokens = _estimate_tokens(generated_text)
                    cached_prompt_tokens = 0
                    reasoning_tokens = 0
                    usage_source = "estimated"
                    transient_errors = ()
                elif raw_response is None:
                    generated_text = ""
                    transport_attempts = admitted_transport_attempts
                    prompt_tokens = 0
                    completion_tokens = 0
                    cached_prompt_tokens = 0
                    reasoning_tokens = 0
                    usage_source = "unavailable"
                    transient_errors = ()
                else:
                    raise ProviderCallError("invalid_response")
            except LLMBudgetExceeded as exc:
                record_budget_exhaustion(exc)
                last_error = "budget_exceeded"
                break
            except Exception as exc:  # Provider boundary is intentionally broad.
                if isinstance(exc, ProviderCallError):
                    try:
                        self._require_transport_admissions(
                            provider.name,
                            reported=exc.transport_attempts,
                            admitted=admitted_transport_attempts)
                    except LLMBudgetExceeded as budget_exc:
                        record_budget_exhaustion(budget_exc)
                        last_error = "budget_exceeded"
                        break
                latency = (time.perf_counter() - attempt_started) * 1000
                category = self._error_category(exc)
                if isinstance(exc, ProviderCallError):
                    transport_attempts = (
                        0 if exc.transport_attempts == 0 else max(
                            exc.transport_attempts,
                            admitted_transport_attempts))
                else:
                    transport_attempts = admitted_transport_attempts
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

            normalized = generated_text.strip()
            if request.output_validator is None or not normalized:
                output_contract_status = None
                output_diagnostic_code = None
            else:
                (
                    normalized,
                    output_contract_status,
                    output_diagnostic_code,
                ) = self._apply_output_contract(request, generated_text)
                if output_contract_status == "rejected":
                    latency = (time.perf_counter() - attempt_started) * 1000
                    self._record_provider_outcome(
                        provider.name, succeeded=False, latency_ms=latency,
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        cached_prompt_tokens=cached_prompt_tokens,
                        reasoning_tokens=reasoning_tokens,
                        transport_attempts=transport_attempts,
                        usage_source=usage_source,
                        error_category="invalid_response",
                        transient_error_categories=transient_errors)
                    attempt_records.append(LLMAttempt(
                        provider=provider.name, model=provider.model,
                        succeeded=False, latency_ms=latency,
                        error_category="invalid_response",
                        transport_attempts=transport_attempts,
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        cached_prompt_tokens=cached_prompt_tokens,
                        reasoning_tokens=reasoning_tokens,
                        usage_exact=usage_source == "exact",
                        usage_source=usage_source,
                        transient_error_categories=transient_errors))
                    return LLMResult(
                        text="", request_id=request_id,
                        provider=None, model=None,
                        latency_ms=(time.perf_counter() - started) * 1000,
                        attempts=len(attempt_records),
                        fallback_path=tuple(fallback_path),
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        usage_exact=usage_source == "exact",
                        cache_status=cache_status,
                        error_category="invalid_response",
                        cached_prompt_tokens=cached_prompt_tokens,
                        reasoning_tokens=reasoning_tokens,
                        provider_attempts=tuple(attempt_records),
                        usage_source=usage_source,
                        output_contract_id=request.output_contract_id,
                        output_contract_status="rejected",
                        output_diagnostic_code=output_diagnostic_code,
                        output_fallback_id=request.output_fallback_id,
                    )

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
                    cache_namespace_id=provider.cache_namespace_id,
                    output_contract_id=request.output_contract_id,
                    output_contract_status=output_contract_status,
                    output_diagnostic_code=output_diagnostic_code,
                    output_fallback_id=request.output_fallback_id,
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
            "cache_namespace_id": result.cache_namespace_id,
            "error_category": result.error_category,
            "succeeded": result.succeeded,
            "output_contract_id": result.output_contract_id,
            "output_contract_status": result.output_contract_status,
            "output_diagnostic_code": result.output_diagnostic_code,
            "output_fallback_id": result.output_fallback_id,
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
            if result.output_contract_status in {
                    "accepted", "rejected", "not_evaluated"}:
                status = result.output_contract_status
                self._counts[f"output_contract_{status}"] += 1
                fallback_used = (
                    not result.succeeded
                    and result.error_category != "budget_exceeded"
                    and request.failure_policy == "best-effort"
                    and result.output_fallback_id is not None)
                if fallback_used:
                    self._counts["output_contract_fallbacks"] += 1
                key = (request.operation, result.output_contract_id or "")
                contract = self._output_contracts.setdefault(key, {
                    "accepted": 0,
                    "rejected": 0,
                    "not_evaluated": 0,
                    "fallbacks": 0,
                    "diagnostic_codes": {},
                    "fallback_ids": set(),
                })
                contract[status] += 1
                if fallback_used:
                    contract["fallbacks"] += 1
                if result.output_fallback_id is not None:
                    contract["fallback_ids"].add(
                        result.output_fallback_id)
                if result.output_diagnostic_code is not None:
                    diagnostic_codes = contract["diagnostic_codes"]
                    diagnostic_codes[result.output_diagnostic_code] = (
                        diagnostic_codes.get(
                            result.output_diagnostic_code, 0) + 1)
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
            cached = self._read_cache(
                cache_path, key, request_id, request)
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
            result = self._revalidate_shared_output(result, request)
            result = self._bind_output_contract(result, request)
            self._record_result(result, request)
            return self._finish(result, request)

        status = "refresh" if mode == "refresh" else (
            "disabled" if mode == "off" else "miss")
        try:
            result = self._execute_uncached(
                request, providers, request_id=request_id,
                cache_status=status)
            result = self._bind_output_contract(result, request)
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
            result = self._bind_output_contract(result, request)
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
                        else sorted(value) if isinstance(value, set)
                        else dict(sorted(value.items()))
                        if isinstance(value, dict) else value)
                    for key, value in metrics.items()
                }
                for name, metrics in self._providers.items()
            }
            output_contracts = [
                {
                    "operation": operation,
                    "contract_id": contract_id,
                    "accepted": metrics["accepted"],
                    "rejected": metrics["rejected"],
                    "not_evaluated": metrics["not_evaluated"],
                    "fallbacks": metrics["fallbacks"],
                    "diagnostic_codes": dict(sorted(
                        metrics["diagnostic_codes"].items())),
                    "fallback_ids": sorted(metrics["fallback_ids"]),
                }
                for (operation, contract_id), metrics
                in sorted(self._output_contracts.items())
            ]
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
                    "max_transport_attempts": config.max_transport_attempts,
                    "max_reserved_tokens": config.max_reserved_tokens,
                },
                "accounting": {
                    "provider_calls": "logical fallback-chain dispatches",
                    "transport_admissions": (
                        "atomic permissions for underlying adapter requests"),
                    "transport_contract_violations": (
                        "capped callbacks reporting unadmitted retries"),
                    "transport_attempts": (
                        "underlying adapter requests, including retries"),
                    "token_usage": (
                        "provider-reported when exact; otherwise characters/4"),
                    "output_contracts": (
                        "content-free accepted, rejected, not-evaluated, and "
                        "deterministic-fallback receipts grouped by operation "
                        "and contract"),
                },
                "counts": dict(self._counts),
                "terminal_error_categories": dict(sorted(
                    self._terminal_error_categories.items())),
                "latency_ms": {
                    "p50": _percentile(self._request_latencies, 0.50),
                    "p95": _percentile(self._request_latencies, 0.95),
                },
                "providers": providers,
                "output_contracts": output_contracts,
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
