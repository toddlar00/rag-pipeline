import json
import sys
import threading
import types as python_types
from concurrent.futures import ThreadPoolExecutor

import pytest
import requests

import rag
import release_security
from llm_runtime import (
    LLMBudgetExceeded,
    LLMRequest,
    LLMRuntime,
    LLMRuntimeConfig,
    ProviderCallError,
    ProviderResponse,
    ProviderSpec,
)


@pytest.fixture(autouse=True)
def _explicit_cloud_policy_for_provider_contracts(monkeypatch):
    monkeypatch.setattr(
        rag,
        "_DEFAULT_RELEASE_SECURITY_POLICY",
        release_security.ReleaseSecurityPolicy(
            profile="development",
            network_policy="allow-cloud",
            trust_environment_network=True,
        ),
    )
    monkeypatch.setattr(
        rag, "_post_cloud_with_policy",
        lambda _policy, url, **kwargs: rag.requests.post(url, **kwargs),
    )


class _Response:
    def __init__(self, payload=None, *, status=200, headers=None):
        self._payload = payload
        self.status_code = status
        self.closed = False
        self.close_count = 0
        self._body = (
            b'{"malformed":' if isinstance(payload, BaseException)
            else json.dumps(payload).encode("utf-8")
        )
        self.headers = {
            "Content-Type": "application/json",
            "Content-Length": str(len(self._body)),
            **(headers or {}),
        }

    def raise_for_status(self):
        if self.status_code >= 400:
            error = requests.exceptions.HTTPError(
                f"status {self.status_code}")
            error.response = self
            raise error

    def json(self):
        pytest.fail("provider path must not eagerly call response.json()")

    def iter_content(self, *, chunk_size):
        for offset in range(0, len(self._body), chunk_size):
            yield self._body[offset:offset + chunk_size]

    def close(self):
        self.closed = True
        self.close_count += 1


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
    observed = {}

    def post(url, **kwargs):
        observed.update(url=url, **kwargs)
        return _Response(_openai_body(" final answer "))

    monkeypatch.setattr(rag.requests, "post", post)
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
    assert observed["allow_redirects"] is False
    assert observed["stream"] is True
    assert isinstance(observed["auth"], rag._llm_adapters._BearerAuth)


@pytest.mark.parametrize(("usage_fields", "expected_counts"), [
    pytest.param({}, (None, None, None, None), id="missing-usage"),
    pytest.param({"usage": None}, (None, None, None, None), id="null-usage"),
    pytest.param({"usage": {}}, (None, None, None, None), id="empty-usage"),
    pytest.param(
        {"usage": {"total_tokens": 22}}, (None, None, None, None),
        id="total-only-usage"),
    pytest.param(
        {"usage": {"prompt_tokens": 0, "completion_tokens": 0}},
        (0, 0, None, None), id="zero-counts-without-total"),
    pytest.param(
        {"usage": {
            "prompt_tokens": 17, "completion_tokens": 5,
            "prompt_cache_hit_tokens": 0,
            "prompt_tokens_details": {"cached_tokens": "ignored"},
            "completion_tokens_details": {"reasoning_tokens": 2},
        }}, (17, 5, 0, 2), id="direct-cache-count-precedes-details"),
    pytest.param(
        {"usage": {
            "prompt_tokens": 17, "completion_tokens": 5,
            "prompt_cache_hit_tokens": None,
            "prompt_tokens_details": {"cached_tokens": 7},
        }}, (17, 5, 7, None), id="null-direct-cache-count-uses-details"),
])
def test_openai_optional_usage_characterization(
        monkeypatch, usage_fields, expected_counts):
    response = _Response({
        "choices": [{"message": {"content": " final answer "}}],
        **usage_fields,
    })
    throttle = _CountingThrottle()
    monkeypatch.setattr(
        rag.requests, "post", lambda *_args, **_kwargs: response)
    monkeypatch.setattr(rag, "_get_throttle", lambda _workers: throttle)

    result = rag._call_openai_compatible_result(
        "prompt", base_url="https://provider.test/v1",
        model="model", api_key="secret")

    assert result.text == "final answer"
    assert (
        result.prompt_tokens, result.completion_tokens,
        result.cached_prompt_tokens, result.reasoning_tokens,
    ) == expected_counts
    assert result.transport_attempts == 1
    assert result.transient_error_categories == ()
    assert response.closed
    assert (throttle.acquired, throttle.ok,
            throttle.rate_limited, throttle.errors) == (1, 1, 0, 0)


@pytest.mark.parametrize(("body", "category"), [
    pytest.param([], "invalid_response", id="non-object-body"),
    pytest.param({}, "invalid_response", id="missing-choices"),
    pytest.param({"choices": {}}, "invalid_response", id="non-list-choices"),
    pytest.param({"choices": [None]}, "invalid_response", id="null-first-choice"),
    pytest.param({"choices": [7]}, "invalid_response", id="integer-first-choice"),
    pytest.param({"choices": [{}]}, "invalid_response", id="missing-message"),
    pytest.param(
        {"choices": [{"message": []}]}, "invalid_response",
        id="non-object-message"),
    pytest.param(
        {"choices": [{"message": {"content": None}}]}, "invalid_response",
        id="null-content"),
    pytest.param(
        _openai_body() | {"usage": []}, "invalid_response",
        id="non-object-usage"),
    pytest.param(
        _openai_body() | {"usage": {"prompt_tokens": 17}}, "invalid_response",
        id="missing-completion-count"),
    pytest.param(
        _openai_body() | {"usage": {"completion_tokens": 5}}, "invalid_response",
        id="missing-prompt-count"),
    pytest.param(
        _openai_body() | {"usage": {
            "prompt_tokens": True, "completion_tokens": 5,
        }}, "invalid_response", id="boolean-count"),
    pytest.param(
        _openai_body() | {"usage": {
            "prompt_tokens": 17, "completion_tokens": -1,
        }}, "invalid_response", id="negative-count"),
    pytest.param(
        _openai_body() | {"usage": {
            "prompt_tokens": 17.0, "completion_tokens": 5,
        }}, "invalid_response", id="non-integer-count"),
    pytest.param(
        _openai_body() | {"usage": {
            "prompt_tokens": 17, "completion_tokens": 5, "total_tokens": 23,
        }}, "invalid_response", id="inconsistent-total"),
    pytest.param(
        _openai_body() | {"usage": {"prompt_cache_hit_tokens": 0}},
        "invalid_response", id="cache-count-without-native-counts"),
    pytest.param(
        _openai_body() | {"usage": {
            "completion_tokens_details": {"reasoning_tokens": 0},
        }}, "invalid_response", id="reasoning-count-without-native-counts"),
    pytest.param(
        _openai_body() | {"usage": {
            "prompt_tokens": 17, "completion_tokens": 5,
            "prompt_tokens_details": {"cached_tokens": 18},
        }}, "invalid_response", id="cache-count-exceeds-prompt-count"),
    pytest.param(
        _openai_body() | {"usage": {
            "prompt_tokens": 17, "completion_tokens": 5,
            "completion_tokens_details": {"reasoning_tokens": 6},
        }}, "invalid_response", id="reasoning-count-exceeds-completion-count"),
])
@pytest.mark.parametrize("attempts", [1, 2])
def test_openai_invalid_body_characterization(
        monkeypatch, body, category, attempts):
    responses = [
        *[_Response(status=429, headers={"Retry-After": "0"})
          for _ in range(attempts - 1)],
        _Response(body),
    ]
    response_iterator = iter(responses)
    throttle = _CountingThrottle()
    monkeypatch.setattr(
        rag.requests, "post", lambda *_args, **_kwargs: next(response_iterator))
    monkeypatch.setattr(rag, "_get_throttle", lambda _workers: throttle)
    monkeypatch.setattr(rag.time, "sleep", lambda _seconds: None)

    with pytest.raises(ProviderCallError) as error:
        rag._call_openai_compatible_result(
            "prompt", base_url="https://provider.test/v1",
            model="model", api_key="secret")

    assert error.value.category == category
    assert error.value.transport_attempts == attempts
    assert all(response.closed for response in responses)
    assert (throttle.acquired, throttle.ok,
            throttle.rate_limited, throttle.errors) == (
                attempts, 0, attempts - 1, 1)


@pytest.mark.parametrize(("message", "finish_reason", "category"), [
    pytest.param({"content": None, "refusal": "declined"}, None,
                 "content_filtered", id="null-content-refusal"),
    pytest.param({"content": "partial", "refusal": "declined"}, "stop",
                 "content_filtered", id="nonempty-content-refusal"),
    pytest.param({"content": None}, "content_filter",
                 "content_filtered", id="null-content-filtered"),
    pytest.param({"content": "partial"}, "content_filter",
                 "content_filtered", id="nonempty-content-filtered"),
    pytest.param({"content": "answer", "refusal": False}, None,
                 "invalid_response", id="non-text-refusal"),
    pytest.param({"content": "answer"}, False,
                 "invalid_response", id="non-text-finish-reason"),
])
def test_openai_rejects_refusals_and_invalid_completion_metadata(
        monkeypatch, message, finish_reason, category):
    response = _Response({"choices": [{
        "message": message, "finish_reason": finish_reason,
    }]})
    throttle = _CountingThrottle()
    monkeypatch.setattr(
        rag.requests, "post", lambda *_args, **_kwargs: response)
    monkeypatch.setattr(rag, "_get_throttle", lambda _workers: throttle)

    with pytest.raises(ProviderCallError) as error:
        rag._call_openai_compatible_result(
            "prompt", base_url="https://provider.test/v1",
            model="model", api_key="secret")

    assert error.value.category == category
    assert error.value.transport_attempts == 1
    assert response.close_count == 1
    assert (throttle.acquired, throttle.ok,
            throttle.rate_limited, throttle.errors) == (1, 0, 0, 1)
    assert "partial" not in str(error.value)
    assert "declined" not in str(error.value)


@pytest.mark.parametrize("text", ["answer", "", " \t\r\n"])
@pytest.mark.parametrize("refusal", [None, ""])
def test_openai_unfiltered_text_preserves_native_usage(
        monkeypatch, text, refusal):
    body = _openai_body(text)
    body["choices"][0]["message"]["refusal"] = refusal
    body["choices"][0]["finish_reason"] = "stop"
    response = _Response(body)
    throttle = _CountingThrottle()
    monkeypatch.setattr(
        rag.requests, "post", lambda *_args, **_kwargs: response)
    monkeypatch.setattr(rag, "_get_throttle", lambda _workers: throttle)

    result = rag._call_openai_compatible_result(
        "prompt", base_url="https://provider.test/v1",
        model="model", api_key="secret")

    assert result == ProviderResponse(
        text=text.strip(), prompt_tokens=17, completion_tokens=5,
        cached_prompt_tokens=7, reasoning_tokens=2)
    assert response.close_count == 1
    assert (throttle.acquired, throttle.ok,
            throttle.rate_limited, throttle.errors) == (1, 1, 0, 0)


@pytest.mark.parametrize("field", [
    "prompt_tokens_details", "completion_tokens_details",
])
@pytest.mark.parametrize("details", [[], "unexpected", False])
def test_openai_rejects_malformed_usage_details(monkeypatch, field, details):
    body = _openai_body()
    body["usage"]["prompt_cache_hit_tokens"] = 3
    body["usage"][field] = details
    response = _Response(body)
    throttle = _CountingThrottle()
    monkeypatch.setattr(
        rag.requests, "post", lambda *_args, **_kwargs: response)
    monkeypatch.setattr(rag, "_get_throttle", lambda _workers: throttle)

    with pytest.raises(ProviderCallError) as error:
        rag._call_openai_compatible_result(
            "prompt", base_url="https://provider.test/v1",
            model="model", api_key="secret")

    assert error.value.category == "invalid_response"
    assert response.close_count == 1
    assert (throttle.acquired, throttle.ok,
            throttle.rate_limited, throttle.errors) == (1, 0, 0, 1)


def test_explicit_bearer_auth_prevents_ambient_netrc_replacement(monkeypatch):
    netrc_calls = []
    monkeypatch.setattr(
        requests.sessions,
        "get_netrc_auth",
        lambda url: netrc_calls.append(url) or (
            "ambient-user", "ambient-password"),
    )
    session = requests.Session()
    prepared = session.prepare_request(requests.Request(
        "POST",
        "https://provider.test/v1/chat/completions",
        headers={"Authorization": "Bearer stale"},
        auth=rag._llm_adapters._BearerAuth("selected-secret"),
    ))

    assert netrc_calls == []
    assert prepared.headers["Authorization"] == "Bearer selected-secret"
    assert "selected-secret" not in repr(
        rag._llm_adapters._BearerAuth("selected-secret"))


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
    response_items = [
        _Response(status=429, headers={"Retry-After": "not-a-number"}),
        _Response(_openai_body()),
    ]
    responses = iter(response_items)
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
    assert all(response.closed for response in response_items)


@pytest.mark.parametrize("status", [429, 502, 503, 504])
@pytest.mark.parametrize("header_name", ["Retry-After", "retry-after"])
def test_openai_temporary_http_failure_retries_after_cleanup_and_admission(
        monkeypatch, status, header_name):
    events = []
    throttle = _CountingThrottle()

    class ObservedResponse(_Response):
        def close(self):
            super().close()
            events.append(("close", self.status_code))

    responses = [
        ObservedResponse(status=status, headers={header_name: "0.25"}),
        ObservedResponse(_openai_body()),
    ]
    response_iterator = iter(responses)

    def post(*_args, **_kwargs):
        response = next(response_iterator)
        events.append(("post", response.status_code))
        return response

    def sleep(delay):
        assert responses[0].close_count == 1
        assert throttle.acquired == (
            throttle.ok + throttle.errors + throttle.rate_limited)
        events.append(("sleep", delay))

    def admit():
        assert events[-1] == ("sleep", 0.25)
        events.append(("admit",))

    monkeypatch.setattr(rag.requests, "post", post)
    monkeypatch.setattr(rag, "_get_throttle", lambda _workers: throttle)
    monkeypatch.setattr(rag.time, "sleep", sleep)

    result = rag._call_openai_compatible_result(
        "prompt", base_url="https://provider.test/v1",
        model="model", api_key="secret", _admit_retry=admit)

    assert result.transport_attempts == 2
    assert result.transient_error_categories == (
        "rate_limited" if status == 429 else "server_error",)
    assert events == [
        ("post", status), ("close", status), ("sleep", 0.25),
        ("admit",), ("post", 200), ("close", 200),
    ]
    assert [response.close_count for response in responses] == [1, 1]
    assert (throttle.acquired, throttle.ok,
            throttle.rate_limited, throttle.errors) == (
                2, 1, int(status == 429), int(status != 429))


@pytest.mark.parametrize("status", [429, 502, 503, 504])
def test_openai_temporary_http_failure_exhausts_two_attempt_limit(
        monkeypatch, status):
    responses = []
    sleeps = []
    admissions = []
    throttle = _CountingThrottle()

    def post(*_args, **_kwargs):
        response = _Response(status=status, headers={"Retry-After": "0"})
        responses.append(response)
        return response

    monkeypatch.setattr(rag.requests, "post", post)
    monkeypatch.setattr(rag, "_get_throttle", lambda _workers: throttle)
    monkeypatch.setattr(rag.time, "sleep", sleeps.append)

    with pytest.raises(ProviderCallError) as error:
        rag._call_openai_compatible_result(
            "prompt", base_url="https://provider.test/v1",
            model="model", api_key="secret",
            _admit_retry=lambda: admissions.append("admitted"))

    assert error.value.category == (
        "rate_limited" if status == 429 else "server_error")
    assert error.value.transport_attempts == 2
    assert len(responses) == 2
    assert [response.close_count for response in responses] == [1, 1]
    assert sleeps == [0]
    assert admissions == ["admitted"]
    assert (throttle.acquired, throttle.ok,
            throttle.rate_limited, throttle.errors) == (
                2, 0, 2 if status == 429 else 0, 0 if status == 429 else 2)


@pytest.mark.parametrize("status", [429, 503])
def test_openai_temporary_failure_with_failed_cleanup_does_not_retry(
        monkeypatch, status):
    class CloseFailure(_Response):
        def close(self):
            super().close()
            raise RuntimeError("SECRET_CLOSE_CANARY")

    response = CloseFailure(status=status)
    throttle = _CountingThrottle()
    post_calls = []
    monkeypatch.setattr(
        rag.requests, "post",
        lambda *_a, **_kw: post_calls.append("post") or response)
    monkeypatch.setattr(rag, "_get_throttle", lambda _workers: throttle)
    monkeypatch.setattr(
        rag.time, "sleep", lambda _: pytest.fail("must not retry"))

    with pytest.raises(ProviderCallError) as error:
        rag._call_openai_compatible_result(
            "prompt", base_url="https://provider.test/v1",
            model="model", api_key="secret")

    assert error.value.category == "connection_error"
    assert error.value.transport_attempts == 1
    assert "SECRET_CLOSE_CANARY" not in str(error.value)
    assert post_calls == ["post"]
    assert response.close_count == 1
    assert (throttle.acquired, throttle.ok,
            throttle.rate_limited, throttle.errors) == (
                1, 0, int(status == 429), int(status != 429))


@pytest.mark.parametrize("failure_type", [KeyboardInterrupt, RuntimeError])
def test_openai_retry_sleep_failure_leaves_no_owned_resources(
        monkeypatch, failure_type):
    failure = failure_type("SECRET_SLEEP_CANARY")
    response = _Response(status=503)
    throttle = _CountingThrottle()
    post_calls = []

    def fail_sleep(_delay):
        assert response.close_count == 1
        assert throttle.errors == throttle.acquired == 1
        raise failure

    monkeypatch.setattr(
        rag.requests, "post",
        lambda *_a, **_kw: post_calls.append("post") or response)
    monkeypatch.setattr(rag, "_get_throttle", lambda _workers: throttle)
    monkeypatch.setattr(rag.time, "sleep", fail_sleep)

    expected = KeyboardInterrupt if failure_type is KeyboardInterrupt else (
        ProviderCallError)
    with pytest.raises(expected) as error:
        rag._call_openai_compatible_result(
            "prompt", base_url="https://provider.test/v1",
            model="model", api_key="secret",
            _admit_retry=lambda: pytest.fail("must not admit a retry"))

    if failure_type is KeyboardInterrupt:
        assert error.value is failure
    else:
        assert error.value.category == "provider_error"
        assert error.value.transport_attempts == 1
        assert "SECRET_SLEEP_CANARY" not in str(error.value)
    assert post_calls == ["post"]
    assert response.close_count == 1
    assert (throttle.acquired, throttle.ok,
            throttle.rate_limited, throttle.errors) == (1, 0, 0, 1)


@pytest.mark.parametrize("status", [400, 401, 403, 408, 500, 501])
def test_openai_other_http_failures_are_not_retried(monkeypatch, status):
    responses = []
    throttle = _CountingThrottle()

    def post(*_args, **_kwargs):
        response = _Response(status=status)
        responses.append(response)
        return response

    monkeypatch.setattr(rag.requests, "post", post)
    monkeypatch.setattr(rag, "_get_throttle", lambda _workers: throttle)
    monkeypatch.setattr(
        rag.time, "sleep", lambda _: pytest.fail("must not retry"))

    with pytest.raises(ProviderCallError) as error:
        rag._call_openai_compatible_result(
            "prompt", base_url="https://provider.test/v1",
            model="model", api_key="secret",
            _admit_retry=lambda: pytest.fail("must not admit a retry"))

    assert error.value.transport_attempts == 1
    assert len(responses) == 1
    assert responses[0].close_count == 1
    assert (throttle.acquired, throttle.ok,
            throttle.rate_limited, throttle.errors) == (1, 0, 0, 1)


@pytest.mark.parametrize("status", [429, 503])
def test_runtime_transport_budget_blocks_openai_retry_without_second_post(
        monkeypatch, tmp_path, status):
    post_calls = 0

    def rate_limited_post(*_args, **_kwargs):
        nonlocal post_calls
        post_calls += 1
        return _Response(status=status, headers={"Retry-After": "0"})

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
    assert throttle.rate_limited == (1 if status == 429 else 0)
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


def test_openai_cleanup_failure_is_safe_and_releases_throttle(monkeypatch):
    class CloseFailure(_Response):
        def close(self):
            raise RuntimeError("SECRET_CLOSE_CANARY")

    throttle = _CountingThrottle()
    monkeypatch.setattr(
        rag.requests, "post",
        lambda *_args, **_kwargs: CloseFailure(_openai_body()),
    )
    monkeypatch.setattr(rag, "_get_throttle", lambda _workers: throttle)

    with pytest.raises(ProviderCallError) as error:
        rag._call_openai_compatible_result(
            "prompt", base_url="https://provider.test/v1",
            model="model", api_key="secret")

    assert error.value.category == "connection_error"
    assert "SECRET_CLOSE_CANARY" not in str(error.value)
    assert throttle.acquired == 1
    assert throttle.errors == 1


@pytest.mark.parametrize(("field", "value"), [
    ("status_code", None), ("status_code", True), ("status_code", "429"),
    ("status_code", 99), ("status_code", 600),
    ("headers", None), ("headers", []),
])
def test_openai_malformed_http_metadata_closes_and_releases(
        monkeypatch, field, value):
    response = _Response(status=429)
    setattr(response, field, value)
    throttle = _CountingThrottle()
    monkeypatch.setattr(
        rag.requests, "post", lambda *_args, **_kwargs: response)
    monkeypatch.setattr(rag, "_get_throttle", lambda _workers: throttle)
    monkeypatch.setattr(
        rag.time, "sleep", lambda _: pytest.fail("must not retry"))

    with pytest.raises(ProviderCallError) as error:
        rag._call_openai_compatible_result(
            "prompt", base_url="https://provider.test/v1",
            model="model", api_key="secret")

    assert error.value.category == "invalid_response"
    assert error.value.transport_attempts == 1
    assert response.close_count == 1
    assert throttle.acquired == 1
    assert throttle.ok == 0
    assert throttle.rate_limited + throttle.errors == 1


@pytest.mark.parametrize("field", ["status_code", "headers"])
def test_openai_http_metadata_getter_failure_is_safe(monkeypatch, field):
    class GetterFailure(_Response):
        def __getattribute__(self, name):
            if name == field:
                raise RuntimeError("SECRET_METADATA_CANARY")
            return super().__getattribute__(name)

    response = GetterFailure(status=429)
    throttle = _CountingThrottle()
    monkeypatch.setattr(
        rag.requests, "post", lambda *_args, **_kwargs: response)
    monkeypatch.setattr(rag, "_get_throttle", lambda _workers: throttle)

    with pytest.raises(ProviderCallError) as error:
        rag._call_openai_compatible_result(
            "prompt", base_url="https://provider.test/v1",
            model="model", api_key="secret")

    assert error.value.category == "provider_error"
    assert error.value.transport_attempts == 1
    assert "SECRET_METADATA_CANARY" not in str(error.value)
    assert response.close_count == 1
    assert throttle.acquired == 1
    assert throttle.ok == 0
    assert throttle.rate_limited + throttle.errors == 1


@pytest.mark.parametrize("phase", [
    "post", "status_code", "headers", "raise_for_status", "body", "close",
])
def test_openai_cancellation_closes_owned_response_and_releases_slot(
        monkeypatch, phase):
    interrupted = KeyboardInterrupt("cancelled")

    class InterruptedResponse(_Response):
        def __getattribute__(self, name):
            if name == phase and phase in {"status_code", "headers"}:
                raise interrupted
            return super().__getattribute__(name)

        def raise_for_status(self):
            if phase == "raise_for_status":
                raise interrupted
            super().raise_for_status()

        def iter_content(self, *, chunk_size):
            if phase == "body":
                raise interrupted
            yield from super().iter_content(chunk_size=chunk_size)

        def close(self):
            super().close()
            if phase == "close":
                raise interrupted

    response = InterruptedResponse(_openai_body())
    throttle = _CountingThrottle()

    def post(*_args, **_kwargs):
        if phase == "post":
            raise interrupted
        return response

    monkeypatch.setattr(rag.requests, "post", post)
    monkeypatch.setattr(rag, "_get_throttle", lambda _workers: throttle)

    with pytest.raises(KeyboardInterrupt) as error:
        rag._call_openai_compatible_result(
            "prompt", base_url="https://provider.test/v1",
            model="model", api_key="secret")

    assert error.value is interrupted
    assert response.close_count == (0 if phase == "post" else 1)
    assert (throttle.acquired, throttle.ok,
            throttle.rate_limited, throttle.errors) == (1, 0, 0, 1)


@pytest.mark.parametrize("phase", ["raise_for_status", "body"])
def test_openai_cleanup_failure_does_not_replace_cancellation(
        monkeypatch, phase):
    interrupted = KeyboardInterrupt("cancelled")

    class DoubleFailure(_Response):
        def raise_for_status(self):
            if phase == "raise_for_status":
                raise interrupted
            super().raise_for_status()

        def iter_content(self, *, chunk_size):
            raise interrupted
            yield

        def close(self):
            super().close()
            raise RuntimeError("SECRET_CLOSE_CANARY")

    response = DoubleFailure(_openai_body())
    throttle = _CountingThrottle()
    monkeypatch.setattr(
        rag.requests, "post", lambda *_args, **_kwargs: response)
    monkeypatch.setattr(rag, "_get_throttle", lambda _workers: throttle)

    with pytest.raises(KeyboardInterrupt) as error:
        rag._call_openai_compatible_result(
            "prompt", base_url="https://provider.test/v1",
            model="model", api_key="secret")

    assert error.value is interrupted
    assert response.close_count == 1
    assert (throttle.acquired, throttle.ok,
            throttle.rate_limited, throttle.errors) == (1, 0, 0, 1)


@pytest.mark.parametrize("timeout", [0, -1, True, "30", None, float("inf"),
                                     float("nan")])
def test_openai_invalid_timeout_does_not_acquire_or_post(monkeypatch, timeout):
    monkeypatch.setattr(
        rag.requests, "post", lambda *_a, **_kw: pytest.fail("must not post"))
    monkeypatch.setattr(
        rag, "_get_throttle", lambda _: pytest.fail("must not acquire"))

    with pytest.raises(ProviderCallError) as error:
        rag._call_openai_compatible_result(
            "prompt", base_url="https://provider.test/v1",
            model="model", api_key="secret", timeout=timeout)

    assert error.value.category == "configuration_error"
    assert error.value.transport_attempts == 0


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_openai_redirects_are_refused_as_one_transport_attempt(
        monkeypatch, status):
    calls = []
    throttle = _CountingThrottle()

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return _Response(status=status)

    monkeypatch.setattr(rag.requests, "post", post)
    monkeypatch.setattr(rag, "_get_throttle", lambda _workers: throttle)

    with pytest.raises(ProviderCallError) as error:
        rag._call_openai_compatible_result(
            "private prompt", base_url="https://provider.test/v1",
            model="model", api_key="secret")

    assert error.value.category == "configuration_error"
    assert error.value.transport_attempts == 1
    assert len(calls) == 1
    assert calls[0][1]["allow_redirects"] is False
    assert throttle.acquired == 1
    assert throttle.errors == 1


def test_invalid_direct_cloud_endpoint_never_reaches_throttle_or_transport(
        monkeypatch):
    monkeypatch.setattr(
        rag.requests, "post",
        lambda *_args, **_kwargs: pytest.fail("transport must not run"))
    monkeypatch.setattr(
        rag, "_get_throttle",
        lambda *_args, **_kwargs: pytest.fail("throttle must not be acquired"))

    with pytest.raises(ProviderCallError) as error:
        rag._call_openai_compatible_result(
            "private prompt", base_url="http://api.deepseek.com",
            model="deepseek-v4-pro", api_key="secret")

    assert error.value.category == "configuration_error"
    assert error.value.transport_attempts == 0


def test_loopback_cloud_transport_uses_proxy_free_post_hook(monkeypatch):
    observed = {}

    def loopback_post(url, **kwargs):
        observed.update(url=url, **kwargs)
        return _Response(_openai_body())

    monkeypatch.setattr(
        rag.requests, "post",
        lambda *_args, **_kwargs: pytest.fail(
            "loopback must not use ambient Requests transport"))
    monkeypatch.setattr(
        rag, "_post_loopback_without_environment", loopback_post)
    monkeypatch.setattr(rag, "_api_throttle", None)

    result = rag._call_openai_compatible_result(
        "prompt", base_url="http://127.0.0.1:8000/v1",
        model="local", api_key="explicit-local-key")

    assert result.text == "answer"
    assert observed["url"] == (
        "http://127.0.0.1:8000/v1/chat/completions")
    assert observed["allow_redirects"] is False
    assert observed["stream"] is True


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

    monkeypatch.setattr(rag, "_post_loopback_without_environment", post)
    result = rag._call_ollama_result(
        "prompt", url="http://127.0.0.1:11434/", model="local",
        thinking=True, max_tokens=99, timeout=12)

    assert result.text == "local answer"
    assert result.prompt_tokens == 23
    assert result.completion_tokens == 8
    assert result.transport_attempts == 1
    assert observed["url"] == "http://127.0.0.1:11434/api/generate"
    assert observed["allow_redirects"] is False
    assert observed["json"]["think"] is True
    assert observed["json"]["options"]["num_predict"] == 99
    assert observed["timeout"] == 12
    assert observed["stream"] is True
    assert "private reasoning" not in result.text


def test_ollama_rejects_partial_usage(monkeypatch):
    monkeypatch.setattr(
        rag, "_post_loopback_without_environment",
        lambda *_args, **_kwargs: _Response({
            "response": "answer", "prompt_eval_count": 3,
        }))

    with pytest.raises(ProviderCallError) as error:
        rag._call_ollama_result("prompt")

    assert error.value.category == "invalid_response"


def test_ollama_redirect_is_refused_without_a_second_request(monkeypatch):
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return _Response(status=307)

    monkeypatch.setattr(rag, "_post_loopback_without_environment", post)

    with pytest.raises(ProviderCallError) as error:
        rag._call_ollama_result(
            "private prompt", url="http://127.0.0.1:11434")

    assert error.value.category == "configuration_error"
    assert error.value.transport_attempts == 1
    assert len(calls) == 1
    assert calls[0][1]["allow_redirects"] is False


def test_loopback_post_disables_environment_proxy_configuration(monkeypatch):
    observed = {}

    class Session:
        trust_env = True

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def post(self, url, **kwargs):
            observed.update(
                url=url, kwargs=kwargs, trust_env=self.trust_env)
            return "response"

    monkeypatch.setattr(rag.requests, "Session", Session)

    response = rag._post_loopback_without_environment(
        "http://127.0.0.1:11434/api/generate", timeout=5)

    assert response.response == "response"
    assert observed == {
        "url": "http://127.0.0.1:11434/api/generate",
        "kwargs": {"timeout": 5, "stream": True},
        "trust_env": False,
    }


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

    class ThinkingConfig:
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
    type_module.ThinkingConfig = ThinkingConfig
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
        max_tokens=55, timeout=17, thinking_level="minimal")

    assert result.text == "gemini answer"
    assert result.prompt_tokens == 31
    assert result.completion_tokens == 13
    assert result.cached_prompt_tokens == 4
    assert result.reasoning_tokens == 4
    config = observed["config"]
    assert config.max_output_tokens == 55
    assert not hasattr(config, "temperature")
    assert config.thinking_config.thinking_level == "minimal"
    assert config.http_options.timeout == 17_000
    assert config.http_options.retry_options.attempts == 1


def test_default_gemini_model_tracks_live_stable_api():
    assert rag.DEFAULT_GEMINI_MODEL == "gemini-3.6-flash"


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


def test_legacy_v1_cache_record_fails_closed_after_namespace_upgrade(tmp_path):
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

    assert hit.cache_status == "miss"
    assert hit.usage_source == "exact"
    assert hit.prompt_tokens == 9
    assert hit.completion_tokens == 3
    assert hit.cached_prompt_tokens == 0
    assert hit.reasoning_tokens == 0
    assert runtime.report_payload()["counts"]["cache_corrupt"] == 1


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
