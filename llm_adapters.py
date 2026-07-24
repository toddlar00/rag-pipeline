"""Typed transport adapters for supported LLM providers.

The deterministic runtime remains provider-neutral in ``llm_runtime``.  This
module translates provider-specific HTTP/SDK responses into its typed
``ProviderResponse`` and ``ProviderCallError`` boundary.  Mutable process state
and compatibility policy are supplied by the ``rag`` facade through callbacks.
"""

from __future__ import annotations

import logging
import math
import os
import threading
import time
from collections.abc import Callable, Mapping
from typing import Protocol
from urllib.parse import urlparse

import requests

import endpoint_policy
from llm_runtime import LLMBudgetExceeded, ProviderCallError, ProviderResponse


class HttpResponseProtocol(Protocol):
    """Minimal response surface consumed by the HTTP adapters."""

    status_code: int
    headers: Mapping[str, object]

    def raise_for_status(self) -> None: ...

    def json(self) -> object: ...


class HttpPostFn(Protocol):
    """Callable protocol matching the subset of ``requests.post`` in use."""

    def __call__(self, url: str, **kwargs: object) -> HttpResponseProtocol: ...


class ThrottleProtocol(Protocol):
    """Concurrency lifecycle required by the OpenAI-compatible adapter."""

    def acquire(self) -> None: ...

    def release_ok(self) -> None: ...

    def release_429(self) -> None: ...

    def release_error(self) -> None: ...


ProviderValueFn = Callable[[object, str], object]
ProviderTokenCountFn = Callable[[object, str], int | None]
ProviderCallErrorFn = Callable[..., ProviderCallError]
ErrorCategoryFn = Callable[[BaseException], str]
EndpointValidatorFn = Callable[..., endpoint_policy.ValidatedEndpoint | None]
ThrottleFactoryFn = Callable[[int], ThrottleProtocol]
RetryAfterFn = Callable[[object], float]
SleepFn = Callable[[float], None]
LogFn = Callable[[str], None]
GeminiClientLoaderFn = Callable[[str], tuple[object, object]]
EnvironmentGetFn = Callable[[str, str], str]


_log = logging.getLogger(__name__)


class _BearerAuth(requests.auth.AuthBase):
    """Explicit Requests auth that prevents ambient netrc replacement."""

    __slots__ = ("_token",)

    def __init__(self, token: str):
        self._token = token

    def __call__(self, request):
        request.headers["Authorization"] = f"Bearer {self._token}"
        return request

    def __repr__(self) -> str:
        return "<_BearerAuth token=<redacted>>"


def _provider_hostname(url: str) -> str:
    """Return a normalized API hostname for provider-safe dispatch."""
    try:
        return (urlparse(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def _is_deepseek_cloud(
        url: str, model: str = "", *,
        validate_endpoint_fn: EndpointValidatorFn = (
            endpoint_policy.validate_cloud_endpoint)) -> bool:
    """Return whether an API URL is the official DeepSeek endpoint."""
    del model  # Kept for compatibility with callers that also have a model.
    try:
        endpoint = validate_endpoint_fn(url)
    except (TypeError, ValueError):
        return False
    return endpoint is not None and endpoint.provider == "deepseek"


def _is_minimax_cloud(
        url: str, *, validate_endpoint_fn: EndpointValidatorFn = (
            endpoint_policy.validate_cloud_endpoint)) -> bool:
    """Return whether an API URL is the official MiniMax endpoint."""
    try:
        endpoint = validate_endpoint_fn(url)
    except (TypeError, ValueError):
        return False
    return endpoint is not None and endpoint.provider == "minimax"


def _provider_value(source: object, name: str) -> object:
    """Read one response field from a mapping or SDK response object."""
    if isinstance(source, dict):
        return source.get(name)
    return getattr(source, name, None)


def _provider_token_count(
        source: object, name: str, *,
        provider_value_fn: ProviderValueFn = _provider_value) -> int | None:
    """Read and validate an optional provider-native token count."""
    value = provider_value_fn(source, name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ProviderCallError("invalid_response")
    return value


def _provider_error_category(exc: BaseException) -> str:
    """Map provider/transport exceptions to a fixed, secret-safe category."""
    if isinstance(exc, ProviderCallError):
        return exc.category
    if isinstance(exc, requests.exceptions.Timeout):
        return "timeout"
    if isinstance(exc, requests.exceptions.ConnectionError):
        return "connection_error"
    if isinstance(exc, (
            requests.exceptions.InvalidURL,
            requests.exceptions.InvalidSchema,
            requests.exceptions.MissingSchema)):
        return "configuration_error"

    response = getattr(exc, "response", None)
    candidates = (
        getattr(response, "status_code", None),
        getattr(exc, "status_code", None),
        getattr(exc, "code", None),
    )
    status = next(
        (value for value in candidates
         if isinstance(value, int) and not isinstance(value, bool)),
        None,
    )
    if status in {401, 403}:
        return "authentication_error"
    if status == 408:
        return "timeout"
    if status == 429:
        return "rate_limited"
    if status is not None and 400 <= status < 500:
        return "client_error"
    if status is not None and status >= 500:
        return "server_error"

    exception_name = type(exc).__name__.casefold()
    if "timeout" in exception_name:
        return "timeout"
    if "connection" in exception_name:
        return "connection_error"
    return "provider_error"


def _provider_call_error(
        exc: BaseException, *, transport_attempts: int,
        error_category_fn: ErrorCategoryFn = (
            _provider_error_category)) -> ProviderCallError:
    if isinstance(exc, ProviderCallError):
        if exc.transport_attempts == transport_attempts:
            return exc
        return ProviderCallError(
            exc.category, transport_attempts=transport_attempts)
    return ProviderCallError(
        error_category_fn(exc), transport_attempts=transport_attempts)


def _call_ollama_result(
        prompt: str, *, url: str, model: str,
        thinking: bool, max_tokens: int, timeout: int,
        post_fn: HttpPostFn,
        loopback_post_fn: HttpPostFn,
        validate_endpoint_fn: EndpointValidatorFn,
        provider_token_count_fn: ProviderTokenCountFn,
        provider_call_error_fn: ProviderCallErrorFn) -> ProviderResponse:
    """Return Ollama text with its native prompt/output token counts."""
    try:
        endpoint = validate_endpoint_fn(url)
    except (TypeError, ValueError):
        raise ProviderCallError(
            "configuration_error", transport_attempts=0) from None
    if endpoint is None:
        raise ProviderCallError(
            "configuration_error", transport_attempts=0)
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "think": thinking,
        "options": {"num_predict": max_tokens},
    }
    selected_post_fn = (
        loopback_post_fn if endpoint.is_loopback else post_fn)
    try:
        resp = selected_post_fn(
            f"{endpoint.base_url}/api/generate",
            json=payload,
            timeout=timeout,
            allow_redirects=False,
        )
        if 300 <= resp.status_code < 400:
            raise ProviderCallError(
                "configuration_error", transport_attempts=1)
        resp.raise_for_status()
        body = resp.json()
        if not isinstance(body, dict) or not isinstance(
                body.get("response"), str):
            raise ProviderCallError("invalid_response")
        prompt_tokens = provider_token_count_fn(body, "prompt_eval_count")
        completion_tokens = provider_token_count_fn(body, "eval_count")
        if (prompt_tokens is None) != (completion_tokens is None):
            raise ProviderCallError("invalid_response")
        return ProviderResponse(
            text=body["response"].strip(),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
    except LLMBudgetExceeded:
        raise
    except Exception as exc:
        if isinstance(exc, (ValueError, TypeError, KeyError, IndexError)):
            raise ProviderCallError(
                "invalid_response", transport_attempts=1) from None
        raise provider_call_error_fn(
            exc, transport_attempts=1) from None


def _gemini_content_filtered(
        response: object, *,
        provider_value_fn: ProviderValueFn = _provider_value) -> bool:
    feedback = provider_value_fn(response, "prompt_feedback")
    values = [provider_value_fn(feedback, "block_reason")]
    candidates = provider_value_fn(response, "candidates")
    if isinstance(candidates, list) and candidates:
        values.append(provider_value_fn(candidates[0], "finish_reason"))
    labels = {
        str(getattr(value, "name", value)).casefold()
        for value in values if value is not None
    }
    return any(any(marker in label for marker in (
        "safety", "block", "prohibited", "recitation"))
        for label in labels)


def _call_gemini_result(
        prompt: str, *, api_key: str, model: str,
        max_tokens: int, timeout: int,
        client_loader_fn: GeminiClientLoaderFn,
        provider_value_fn: ProviderValueFn,
        provider_token_count_fn: ProviderTokenCountFn,
        provider_call_error_fn: ProviderCallErrorFn,
        content_filtered_fn: Callable[[object], bool],
        environment_get_fn: EnvironmentGetFn = os.environ.get,
) -> ProviderResponse:
    """Return Gemini text and usage with SDK retries explicitly disabled."""
    api_key = api_key or environment_get_fn("GEMINI_API_KEY", "")
    if not api_key:
        raise ProviderCallError(
            "missing_credentials", transport_attempts=0)

    try:
        client, types = client_loader_fn(api_key)
    except ProviderCallError:
        raise
    except Exception:
        raise ProviderCallError(
            "configuration_error", transport_attempts=0) from None

    try:
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                max_output_tokens=max_tokens,
                temperature=0.0,
                http_options=types.HttpOptions(
                    timeout=timeout * 1000,
                    retry_options=types.HttpRetryOptions(attempts=1),
                ),
            ),
        )
    except LLMBudgetExceeded:
        raise
    except Exception as exc:
        raise provider_call_error_fn(
            exc, transport_attempts=1) from None

    try:
        text = response.text
    except Exception:
        text = None
    if text is None:
        category = (
            "content_filtered" if content_filtered_fn(response)
            else "invalid_response")
        raise ProviderCallError(category, transport_attempts=1)
    if not isinstance(text, str):
        raise ProviderCallError("invalid_response", transport_attempts=1)

    usage = provider_value_fn(response, "usage_metadata")
    prompt_tokens = provider_token_count_fn(usage, "prompt_token_count")
    candidate_tokens = provider_token_count_fn(
        usage, "candidates_token_count")
    total_tokens = provider_token_count_fn(usage, "total_token_count")
    cached_tokens = provider_token_count_fn(
        usage, "cached_content_token_count")
    reasoning_tokens = provider_token_count_fn(
        usage, "thoughts_token_count")

    completion_tokens = None
    if prompt_tokens is not None:
        if total_tokens is not None:
            if total_tokens < prompt_tokens:
                raise ProviderCallError("invalid_response")
            completion_tokens = total_tokens - prompt_tokens
        elif candidate_tokens is not None:
            completion_tokens = candidate_tokens + (reasoning_tokens or 0)
        else:
            raise ProviderCallError("invalid_response")
    elif any(value is not None for value in (
            candidate_tokens, total_tokens, cached_tokens, reasoning_tokens)):
        raise ProviderCallError("invalid_response")

    return ProviderResponse(
        text=text.strip(), prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cached_prompt_tokens=cached_tokens,
        reasoning_tokens=reasoning_tokens,
    )


class _AdaptiveThrottle:
    """Dynamically reduce provider concurrency after rate-limit responses."""

    def __init__(
            self, max_workers: int = 20, *,
            sleep_fn: SleepFn = time.sleep,
            info_fn: LogFn = _log.info,
            warning_fn: LogFn = _log.warning):
        self._max = max(1, max_workers)
        self._current = self._max
        self._ceiling = self._max
        self._active = 0
        self._condition = threading.Condition()
        self._consecutive_ok = 0
        self._total_429s = 0
        self._cooldown = 0.0
        self._cooldown_floor = 0.0
        self._sleep_fn = sleep_fn
        self._info_fn = info_fn
        self._warning_fn = warning_fn

    def acquire(self) -> None:
        """Wait for an available concurrency slot."""
        with self._condition:
            while self._active >= self._current:
                self._condition.wait()
            self._active += 1
            cooldown = self._cooldown
        if cooldown > 0:
            self._sleep_fn(cooldown)

    def release_ok(self) -> None:
        """Release a slot after a successful call."""
        with self._condition:
            self._active = max(0, self._active - 1)
            self._consecutive_ok += 1
            if self._consecutive_ok >= 20 and self._current < self._ceiling:
                self._current += 1
                self._cooldown = max(
                    self._cooldown_floor, self._cooldown - 0.1)
                self._info_fn(
                    f"Rate limit recovery: workers -> {self._current}, "
                    f"cooldown -> {self._cooldown:.1f}s "
                    f"(ceiling: {self._ceiling}, "
                    f"cooldown floor: {self._cooldown_floor:.1f}s)")
                self._consecutive_ok = 0
            self._condition.notify_all()

    def release_429(self) -> None:
        """Release a slot and reduce concurrency after a rate limit."""
        with self._condition:
            self._active = max(0, self._active - 1)
            self._total_429s += 1
            self._consecutive_ok = 0
            old = self._current
            self._ceiling = max(
                1, min(self._ceiling, self._current - 1))
            self._current = max(1, self._current // 2)
            self._cooldown = min(5.0, self._cooldown + 0.5)
            self._cooldown_floor = max(
                self._cooldown_floor, self._cooldown - 0.5)
            self._warning_fn(
                f"Rate limited (429)! Workers: {old} -> {self._current}, "
                f"cooldown: {self._cooldown:.1f}s "
                f"(ceiling: {self._ceiling}, "
                f"cooldown floor: {self._cooldown_floor:.1f}s, "
                f"total 429s: {self._total_429s})")
            self._condition.notify_all()

    def release_error(self) -> None:
        """Release a slot after a non-rate-limit error."""
        with self._condition:
            self._active = max(0, self._active - 1)
            self._condition.notify_all()

    @property
    def current_workers(self) -> int:
        with self._condition:
            return self._current

    @property
    def total_429s(self) -> int:
        with self._condition:
            return self._total_429s


def _retry_after_seconds(value: object) -> float:
    """Parse delta-seconds Retry-After safely, with a bounded default."""
    try:
        delay = float(value)
    except (TypeError, ValueError):
        return 2.0
    if not math.isfinite(delay) or delay < 0:
        return 2.0
    return min(delay, 5.0)


def _call_openai_compatible_result(
        prompt: str, *, base_url: str, model: str, api_key: str,
        thinking: bool, max_tokens: int, max_workers: int, timeout: int,
        post_fn: HttpPostFn,
        loopback_post_fn: HttpPostFn,
        get_throttle_fn: ThrottleFactoryFn,
        sleep_fn: SleepFn,
        validate_endpoint_fn: EndpointValidatorFn,
        provider_token_count_fn: ProviderTokenCountFn,
        provider_value_fn: ProviderValueFn,
        provider_call_error_fn: ProviderCallErrorFn,
        retry_after_fn: RetryAfterFn,
        admit_retry_fn: Callable[[], None] | None = None) -> ProviderResponse:
    """Return OpenAI-compatible text, usage, and retry provenance."""
    try:
        endpoint = validate_endpoint_fn(base_url)
    except (TypeError, ValueError):
        raise ProviderCallError(
            "configuration_error", transport_attempts=0) from None
    if endpoint is None:
        raise ProviderCallError(
            "configuration_error", transport_attempts=0)
    if not api_key:
        raise ProviderCallError(
            "missing_credentials", transport_attempts=0)

    throttle = get_throttle_fn(max(1, max_workers))
    is_deepseek = endpoint.provider == "deepseek"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
    }
    if is_deepseek:
        payload["thinking"] = {
            "type": "enabled" if thinking else "disabled"
        }
    if not (is_deepseek and thinking):
        payload["temperature"] = 0.0

    transient_errors: list[str] = []
    selected_post_fn = (
        loopback_post_fn if endpoint.is_loopback else post_fn)

    for attempt in range(2):
        throttle.acquire()
        try:
            if attempt > 0 and admit_retry_fn is not None:
                admit_retry_fn()
            resp = selected_post_fn(
                f"{endpoint.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                auth=_BearerAuth(api_key),
                json=payload,
                timeout=timeout,
                allow_redirects=False,
            )
        except LLMBudgetExceeded:
            throttle.release_error()
            raise
        except Exception as exc:
            throttle.release_error()
            raise provider_call_error_fn(
                exc, transport_attempts=attempt + 1) from None

        if 300 <= resp.status_code < 400:
            throttle.release_error()
            raise ProviderCallError(
                "configuration_error", transport_attempts=attempt + 1)
        if resp.status_code == 429:
            throttle.release_429()
            if attempt == 0:
                transient_errors.append("rate_limited")
                retry_after = retry_after_fn(
                    resp.headers.get("Retry-After", "2"))
                sleep_fn(retry_after)
                continue
            raise ProviderCallError(
                "rate_limited", transport_attempts=attempt + 1)

        try:
            resp.raise_for_status()
            body = resp.json()
            if not isinstance(body, dict):
                raise ProviderCallError("invalid_response")
            choices = body.get("choices")
            if not isinstance(choices, list) or not choices:
                raise ProviderCallError("invalid_response")
            message = choices[0].get("message")
            if not isinstance(message, dict) or not isinstance(
                    message.get("content"), str):
                raise ProviderCallError("invalid_response")

            usage = body.get("usage")
            if usage is not None and not isinstance(usage, dict):
                raise ProviderCallError("invalid_response")
            prompt_tokens = provider_token_count_fn(usage, "prompt_tokens")
            completion_tokens = provider_token_count_fn(
                usage, "completion_tokens")
            total_tokens = provider_token_count_fn(usage, "total_tokens")
            if (prompt_tokens is None) != (completion_tokens is None):
                raise ProviderCallError("invalid_response")
            if (total_tokens is not None and prompt_tokens is not None
                    and total_tokens != prompt_tokens + completion_tokens):
                raise ProviderCallError("invalid_response")

            cached_tokens = provider_token_count_fn(
                usage, "prompt_cache_hit_tokens")
            if cached_tokens is None:
                prompt_details = provider_value_fn(
                    usage, "prompt_tokens_details")
                cached_tokens = provider_token_count_fn(
                    prompt_details, "cached_tokens")
            completion_details = provider_value_fn(
                usage, "completion_tokens_details")
            reasoning_tokens = provider_token_count_fn(
                completion_details, "reasoning_tokens")
            if prompt_tokens is None and (
                    cached_tokens is not None
                    or reasoning_tokens is not None):
                raise ProviderCallError("invalid_response")
            if (cached_tokens is not None and prompt_tokens is not None
                    and cached_tokens > prompt_tokens):
                raise ProviderCallError("invalid_response")
            if (reasoning_tokens is not None
                    and completion_tokens is not None
                    and reasoning_tokens > completion_tokens):
                raise ProviderCallError("invalid_response")
            result = ProviderResponse(
                text=message["content"].strip(),
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cached_prompt_tokens=cached_tokens,
                reasoning_tokens=reasoning_tokens,
                transport_attempts=attempt + 1,
                transient_error_categories=tuple(transient_errors),
            )
        except Exception as exc:
            throttle.release_error()
            if isinstance(exc, (ValueError, TypeError, KeyError, IndexError)):
                raise ProviderCallError(
                    "invalid_response",
                    transport_attempts=attempt + 1) from None
            raise provider_call_error_fn(
                exc, transport_attempts=attempt + 1) from None

        throttle.release_ok()
        return result

    raise ProviderCallError("provider_error", transport_attempts=2)


def _llm_endpoint_id(url: str) -> str:
    """Return a credential-free endpoint identity for cache separation."""
    return endpoint_policy.cloud_endpoint_identity(url)
