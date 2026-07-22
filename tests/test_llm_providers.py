import json
import sys
import threading
import types as python_types
from concurrent.futures import ThreadPoolExecutor

import pytest
import requests

import rag
from llm_runtime import (
    LLMBudgetExceeded,
    LLMRequest,
    LLMRuntime,
    LLMRuntimeConfig,
    ProviderCallError,
    ProviderResponse,
    ProviderSpec,
)


class _Response:
    def __init__(self, payload=None, *, status=200, headers=None):
        self._payload = payload
        self.status_code = status
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            error = requests.exceptions.HTTPError(
                f"status {self.status_code}")
            error.response = self
            raise error

    def json(self):
        if isinstance(self._payload, BaseException):
            raise self._payload
        return self._payload


class _CountingThrottle:
    def __init__(self):
        self.acquired = 0
        self.ok = 0
        self.rate_limited = 0
        self.errors = 0

    def acquire(self):
        self.acquired += 1

    def release_ok(self):
        self.ok += 1

    def release_429(self):
        self.rate_limited += 1

    def release_error(self):
        self.errors += 1


def test_get_throttle_initialization_is_thread_safe(monkeypatch):
    start = threading.Barrier(3)
    constructors = threading.Barrier(2)
    calls = 0
    calls_lock = threading.Lock()

    class SlowThrottle:
        def __init__(self, max_workers, **_kwargs):
            nonlocal calls
            self._max = max_workers
            with calls_lock:
                calls += 1
            try:
                constructors.wait(timeout=1)
            except threading.BrokenBarrierError:
                pass

    def get_one():
        start.wait(timeout=2)
        return rag._get_throttle(4)

    monkeypatch.setattr(rag, "_AdaptiveThrottle", SlowThrottle)
    monkeypatch.setattr(rag, "_api_throttle", None)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(get_one) for _ in range(2)]
        start.wait(timeout=2)
        throttles = [future.result(timeout=3) for future in futures]

    assert throttles[0] is throttles[1]
    assert calls == 1


def _openai_body(text="answer", *, prompt_tokens=17,
                 completion_tokens=5):
    return {
        "choices": [{
            "message": {
                "content": text,
                "reasoning_content": "private reasoning",
            },
        }],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "prompt_tokens_details": {"cached_tokens": 7},
            "completion_tokens_details": {"reasoning_tokens": 2},
        },
    }


def test_openai_structured_response_parses_native_usage(monkeypatch):
    monkeypatch.setattr(
        rag.requests, "post", lambda *_args, **_kwargs: _Response(
            _openai_body(" final answer ")))
    monkeypatch.setattr(rag, "_api_throttle", None)

    result = rag._call_openai_compatible_result(
        "prompt", base_url="https://provider.test/v1",
        model="model", api_key="secret")

    assert result.text == "final answer"
    assert result.prompt_tokens == 17
    assert result.completion_tokens == 5
    assert result.cached_prompt_tokens == 7
    assert result.reasoning_tokens == 2
    assert result.transport_attempts == 1
    assert result.transient_error_categories == ()
    assert "private reasoning" not in result.text


def test_openai_string_facade_remains_compatible(monkeypatch):
    monkeypatch.setattr(
        rag.requests, "post", lambda *_args, **_kwargs: _Response(
            _openai_body("answer")))
    monkeypatch.setattr(rag, "_api_throttle", None)

    result = rag._call_openai_compatible(
        "prompt", base_url="https://provider.test/v1",
        model="model", api_key="secret")

    assert result == "answer"
    assert isinstance(result, str)


def test_openai_429_retry_reports_transport_attempts(monkeypatch):
    responses = iter([
        _Response(status=429, headers={"Retry-After": "not-a-number"}),
        _Response(_openai_body()),
    ])
    throttle = _CountingThrottle()
    monkeypatch.setattr(
        rag.requests, "post", lambda *_args, **_kwargs: next(responses))
    monkeypatch.setattr(rag, "_get_throttle", lambda _workers: throttle)
    monkeypatch.setattr(rag.time, "sleep", lambda _seconds: None)

    result = rag._call_openai_compatible_result(
        "prompt", base_url="https://provider.test/v1",
        model="model", api_key="secret")

    assert result.transport_attempts == 2
    assert result.transient_error_categories == ("rate_limited",)
    assert throttle.acquired == 2
    assert throttle.rate_limited == 1
    assert throttle.ok == 1
    assert throttle.errors == 0


def test_runtime_transport_budget_blocks_openai_retry_without_second_post(
        monkeypatch, tmp_path):
    post_calls = 0

    def rate_limited_post(*_args, **_kwargs):
        nonlocal post_calls
        post_calls += 1
        return _Response(status=429, headers={"Retry-After": "0"})

    throttle = _CountingThrottle()
    runtime = LLMRuntime(LLMRuntimeConfig(
        cache_mode="off", cache_dir=tmp_path / "cache",
        max_transport_attempts=1))
    monkeypatch.setattr(rag, "_llm_runtime", runtime)
    monkeypatch.setattr(rag.requests, "post", rate_limited_post)
    monkeypatch.setattr(rag, "_get_throttle", lambda _workers: throttle)
    monkeypatch.setattr(rag.time, "sleep", lambda _seconds: None)

    with pytest.raises(LLMBudgetExceeded):
        rag._call_llm_result(
            "prompt", cloud_url="https://provider.test/v1",
            cloud_model="model", cloud_key="secret", ollama_url="",
            operation="test.retry_transport_budget")

    counts = runtime.report_payload()["counts"]
    assert post_calls == 1
    assert throttle.acquired == (
        throttle.ok + throttle.rate_limited + throttle.errors)
    assert throttle.rate_limited == 1
    assert counts["provider_calls"] == 1
    assert counts["transport_admissions"] == 1
    assert counts["transport_attempts"] == 1
    assert counts["budget_rejections"] == 1


def test_openai_two_rate_limits_raise_safe_error(monkeypatch):
    throttle = _CountingThrottle()
    monkeypatch.setattr(
        rag.requests, "post",
        lambda *_args, **_kwargs: _Response(status=429))
    monkeypatch.setattr(rag, "_get_throttle", lambda _workers: throttle)
    monkeypatch.setattr(rag.time, "sleep", lambda _seconds: None)

    with pytest.raises(ProviderCallError) as error:
        rag._call_openai_compatible_result(
            "prompt", base_url="https://provider.test/v1",
            model="model", api_key="secret")

    assert error.value.category == "rate_limited"
    assert error.value.transport_attempts == 2
    assert throttle.acquired == 2
    assert throttle.rate_limited == 2
    assert throttle.ok == 0
    assert throttle.errors == 0


def test_malformed_openai_response_releases_throttle_once(monkeypatch):
    throttle = _CountingThrottle()
    monkeypatch.setattr(
        rag.requests, "post",
        lambda *_args, **_kwargs: _Response({"choices": []}))
    monkeypatch.setattr(rag, "_get_throttle", lambda _workers: throttle)

    with pytest.raises(ProviderCallError) as error:
        rag._call_openai_compatible_result(
            "prompt", base_url="https://provider.test/v1",
            model="model", api_key="secret")

    assert error.value.category == "invalid_response"
    assert throttle.acquired == 1
    assert throttle.ok + throttle.rate_limited + throttle.errors == 1
    assert throttle.errors == 1


@pytest.mark.parametrize(
    ("response_or_exception", "expected"),
    [
        (requests.exceptions.Timeout("secret timeout"), "timeout"),
        (requests.exceptions.ConnectionError("secret host"),
         "connection_error"),
        (requests.exceptions.MissingSchema("secret url"),
         "configuration_error"),
        (_Response(status=401), "authentication_error"),
        (_Response(status=500), "server_error"),
        (_Response(ValueError("secret body")), "invalid_response"),
    ],
)
def test_openai_errors_use_safe_categories(
        monkeypatch, response_or_exception, expected):
    def post(*_args, **_kwargs):
        if isinstance(response_or_exception, BaseException):
            raise response_or_exception
        return response_or_exception

    monkeypatch.setattr(rag.requests, "post", post)
    monkeypatch.setattr(rag, "_api_throttle", None)

    with pytest.raises(ProviderCallError) as error:
        rag._call_openai_compatible_result(
            "prompt", base_url="https://provider.test/v1",
            model="model", api_key="secret")

    assert error.value.category == expected
    assert "secret" not in str(error.value)


def test_ollama_structured_response_uses_native_counts(monkeypatch):
    observed = {}

    def post(url, **kwargs):
        observed["url"] = url
        observed.update(kwargs)
        return _Response({
            "response": " local answer ",
            "thinking": "private reasoning",
            "prompt_eval_count": 23,
            "eval_count": 8,
        })

    monkeypatch.setattr(rag.requests, "post", post)
    result = rag._call_ollama_result(
        "prompt", url="http://localhost:11434/", model="local",
        thinking=True, max_tokens=99, timeout=12)

    assert result.text == "local answer"
    assert result.prompt_tokens == 23
    assert result.completion_tokens == 8
    assert result.transport_attempts == 1
    assert observed["url"] == "http://localhost:11434/api/generate"
    assert observed["json"]["think"] is True
    assert observed["json"]["options"]["num_predict"] == 99
    assert observed["timeout"] == 12
    assert "private reasoning" not in result.text


def test_ollama_rejects_partial_usage(monkeypatch):
    monkeypatch.setattr(
        rag.requests, "post", lambda *_args, **_kwargs: _Response({
            "response": "answer", "prompt_eval_count": 3,
        }))

    with pytest.raises(ProviderCallError) as error:
        rag._call_ollama_result("prompt")

    assert error.value.category == "invalid_response"


def _install_fake_gemini(monkeypatch, response=None, error=None):
    observed = {}

    class HttpRetryOptions:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class HttpOptions:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class GenerateContentConfig:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class Models:
        def generate_content(self, **kwargs):
            observed.update(kwargs)
            if error is not None:
                raise error
            return response

    client = python_types.SimpleNamespace(models=Models())
    genai_module = python_types.ModuleType("google.genai")
    genai_module.Client = lambda **_kwargs: client
    type_module = python_types.ModuleType("google.genai.types")
    type_module.HttpRetryOptions = HttpRetryOptions
    type_module.HttpOptions = HttpOptions
    type_module.GenerateContentConfig = GenerateContentConfig
    genai_module.types = type_module
    google_module = python_types.ModuleType("google")
    google_module.genai = genai_module
    monkeypatch.setitem(sys.modules, "google", google_module)
    monkeypatch.setitem(sys.modules, "google.genai", genai_module)
    monkeypatch.setitem(sys.modules, "google.genai.types", type_module)
    monkeypatch.setattr(rag, "_gemini_client_cache", None)
    monkeypatch.setattr(rag, "_gemini_client_key", "")
    return observed


def test_gemini_structured_usage_and_timeout(monkeypatch):
    usage = python_types.SimpleNamespace(
        prompt_token_count=31,
        candidates_token_count=9,
        total_token_count=44,
        cached_content_token_count=4,
        thoughts_token_count=4,
    )
    response = python_types.SimpleNamespace(
        text=" gemini answer ", usage_metadata=usage,
        prompt_feedback=None, candidates=[])
    observed = _install_fake_gemini(monkeypatch, response=response)

    result = rag._call_gemini_result(
        "prompt", api_key="secret", model="gemini-test",
        max_tokens=55, timeout=17)

    assert result.text == "gemini answer"
    assert result.prompt_tokens == 31
    assert result.completion_tokens == 13
    assert result.cached_prompt_tokens == 4
    assert result.reasoning_tokens == 4
    config = observed["config"]
    assert config.max_output_tokens == 55
    assert config.http_options.timeout == 17_000
    assert config.http_options.retry_options.attempts == 1


def test_gemini_error_and_content_filter_categories(monkeypatch):
    class APIError(Exception):
        code = 429

    _install_fake_gemini(monkeypatch, error=APIError("secret body"))
    with pytest.raises(ProviderCallError) as rate_error:
        rag._call_gemini_result("prompt", api_key="secret")
    assert rate_error.value.category == "rate_limited"
    assert "secret body" not in str(rate_error.value)

    filtered = python_types.SimpleNamespace(
        text=None, usage_metadata=None,
        prompt_feedback=python_types.SimpleNamespace(block_reason="SAFETY"),
        candidates=[])
    _install_fake_gemini(monkeypatch, response=filtered)
    with pytest.raises(ProviderCallError) as filtered_error:
        rag._call_gemini_result("prompt", api_key="secret")
    assert filtered_error.value.category == "content_filtered"


def test_runtime_records_exact_usage_retries_and_transient_errors(tmp_path):
    runtime = LLMRuntime(LLMRuntimeConfig(
        cache_mode="off", cache_dir=tmp_path / "cache"))

    def invoke(request):
        request.admit_transport_retry()
        return ProviderResponse(
            text="answer", prompt_tokens=40, completion_tokens=12,
            cached_prompt_tokens=10, reasoning_tokens=3,
            transport_attempts=2,
            transient_error_categories=("rate_limited",))

    provider = ProviderSpec(
        name="cloud", model="model", endpoint_id="provider.test",
        invoke=invoke,
    )

    result = runtime.execute(
        LLMRequest(prompt="prompt", operation="test.native_usage"),
        [provider])
    report = runtime.report_payload()

    assert result.usage_exact is True
    assert result.usage_source == "exact"
    assert result.prompt_tokens == 40
    assert result.completion_tokens == 12
    assert result.cached_prompt_tokens == 10
    assert result.reasoning_tokens == 3
    assert result.transport_attempts == 2
    assert result.retry_attempts == 1
    assert result.provider_attempts[0].transient_error_categories == (
        "rate_limited",)
    assert report["counts"]["provider_calls"] == 1
    assert report["counts"]["transport_admissions"] == 2
    assert report["counts"]["transport_attempts"] == 2
    assert report["counts"]["transport_retries"] == 1
    assert report["counts"]["exact_prompt_tokens"] == 40
    assert report["providers"]["cloud"]["error_categories"] == {
        "rate_limited": 1}


def test_fallback_event_has_safe_attempt_provenance(tmp_path):
    events_path = tmp_path / "events.jsonl"
    runtime = LLMRuntime(LLMRuntimeConfig(
        cache_mode="off", cache_dir=tmp_path / "cache",
        events_path=events_path))

    def primary(request):
        request.admit_transport_retry()
        raise ProviderCallError("rate_limited", transport_attempts=2)

    providers = [
        ProviderSpec(
            name="primary", model="model-a", endpoint_id="SECRET_ENDPOINT",
            invoke=primary),
        ProviderSpec(
            name="secondary", model="model-b", endpoint_id="secondary.test",
            invoke=lambda _request: ProviderResponse(
                text="answer", prompt_tokens=11, completion_tokens=4)),
    ]
    result = runtime.execute(LLMRequest(
        prompt="SECRET_PROMPT", operation="test.fallback_event"), providers)
    event_text = events_path.read_text(encoding="utf-8")
    event = json.loads(event_text)

    assert result.attempts == 2
    assert result.transport_attempts == 3
    assert result.retry_attempts == 1
    assert event["transport_attempts"] == 3
    assert event["retry_attempts"] == 1
    assert event["provider_attempts"][0]["error_category"] == (
        "rate_limited")
    assert event["provider_attempts"][0]["usage_source"] == "unavailable"
    assert event["provider_attempts"][1]["usage_source"] == "exact"
    assert "SECRET_PROMPT" not in event_text
    assert "SECRET_ENDPOINT" not in event_text


def test_exact_usage_survives_cache_hit_without_live_attempts(tmp_path):
    calls = 0

    def invoke(_request):
        nonlocal calls
        calls += 1
        return ProviderResponse(
            text="answer", prompt_tokens=20, completion_tokens=6,
            cached_prompt_tokens=5, reasoning_tokens=2)

    runtime = LLMRuntime(LLMRuntimeConfig(
        cache_mode="readwrite", cache_dir=tmp_path / "cache"))
    request = LLMRequest(prompt="prompt", operation="test.cache_usage")
    provider = ProviderSpec(
        name="provider", model="model", endpoint_id="provider.test",
        invoke=invoke)

    runtime.execute(request, [provider])
    hit = runtime.execute(request, [provider])

    assert hit.cache_status == "hit"
    assert hit.usage_source == "exact"
    assert hit.prompt_tokens == 20
    assert hit.completion_tokens == 6
    assert hit.cached_prompt_tokens == 5
    assert hit.reasoning_tokens == 2
    assert hit.attempts == 0
    assert hit.provider_attempts == ()
    assert hit.transport_attempts == 0
    assert calls == 1


def test_legacy_v1_cache_record_remains_readable(tmp_path):
    cache_dir = tmp_path / "cache"
    runtime = LLMRuntime(LLMRuntimeConfig(
        cache_mode="readwrite", cache_dir=cache_dir))
    request = LLMRequest(prompt="prompt", operation="test.v1_cache")
    provider = ProviderSpec(
        name="provider", model="model", endpoint_id="provider.test",
        invoke=lambda _request: ProviderResponse(
            text="answer", prompt_tokens=9, completion_tokens=3),
    )
    runtime.execute(request, [provider])
    cache_path = next(cache_dir.rglob("*.json"))
    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    payload["schema_version"] = 1
    payload["result"].pop("usage_source")
    payload["result"].pop("cached_prompt_tokens")
    payload["result"].pop("reasoning_tokens")
    cache_path.write_text(json.dumps(payload), encoding="utf-8")

    hit = runtime.execute(request, [provider])

    assert hit.cache_status == "hit"
    assert hit.usage_source == "exact"
    assert hit.prompt_tokens == 9
    assert hit.completion_tokens == 3
    assert hit.cached_prompt_tokens == 0
    assert hit.reasoning_tokens == 0


def test_v2_cache_detects_tampered_usage_metadata(tmp_path):
    calls = 0

    def invoke(_request):
        nonlocal calls
        calls += 1
        return ProviderResponse(
            text="answer", prompt_tokens=9 + calls,
            completion_tokens=3)

    cache_dir = tmp_path / "cache"
    runtime = LLMRuntime(LLMRuntimeConfig(
        cache_mode="readwrite", cache_dir=cache_dir))
    request = LLMRequest(prompt="prompt", operation="test.v2_integrity")
    provider = ProviderSpec(
        name="provider", model="model", endpoint_id="provider.test",
        invoke=invoke)
    runtime.execute(request, [provider])
    cache_path = next(cache_dir.rglob("*.json"))
    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    payload["result"]["prompt_tokens"] = 999_999
    cache_path.write_text(json.dumps(payload), encoding="utf-8")

    repaired = runtime.execute(request, [provider])

    assert repaired.cache_status == "miss"
    assert repaired.prompt_tokens == 11
    assert calls == 2
    assert runtime.report_payload()["counts"]["cache_corrupt"] == 1


def test_rag_structured_boundary_preserves_native_usage(monkeypatch, tmp_path):
    runtime = LLMRuntime(LLMRuntimeConfig(
        cache_mode="off", cache_dir=tmp_path / "cache"))
    monkeypatch.setattr(rag, "_llm_runtime", runtime)

    def fake_cloud(_prompt, **kwargs):
        assert kwargs["_structured"] is True
        return ProviderResponse(
            text="answer", prompt_tokens=14, completion_tokens=6,
            cached_prompt_tokens=4, reasoning_tokens=2)

    monkeypatch.setattr(rag, "_call_openai_compatible", fake_cloud)
    result = rag._call_llm_result(
        "prompt", cloud_url="https://provider.test/v1",
        cloud_model="model", cloud_key="secret", ollama_url="",
        operation="test.rag_native")

    assert result.text == "answer"
    assert result.usage_source == "exact"
    assert result.prompt_tokens == 14
    assert result.completion_tokens == 6
    assert result.cached_prompt_tokens == 4
    assert result.reasoning_tokens == 2
    assert result.provider == "cloud"


def test_runtime_rejects_partial_native_usage_as_safe_failure(tmp_path):
    runtime = LLMRuntime(LLMRuntimeConfig(
        cache_mode="off", cache_dir=tmp_path / "cache"))
    provider = ProviderSpec(
        name="provider", model="model", endpoint_id="provider.test",
        invoke=lambda _request: ProviderResponse(
            text="answer", prompt_tokens=5),
    )

    result = runtime.execute(
        LLMRequest(prompt="prompt", operation="test.invalid_usage"),
        [provider])

    assert result.succeeded is False
    assert result.error_category == "invalid_response"
    assert result.provider_attempts[0].usage_source == "unavailable"
    report = runtime.report_payload()
    assert report["counts"]["unknown_usage_attempts"] == 1
    assert report["counts"]["estimated_usage_attempts"] == 0
    assert report["terminal_error_categories"] == {"invalid_response": 1}
