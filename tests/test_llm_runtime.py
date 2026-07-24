import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

import rag
import release_security
from llm_runtime import (
    LLMBudgetExceeded,
    LLMExecutionError,
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


def _provider(invoke, *, name="primary", model="model-a",
              endpoint="https://provider.test/v1"):
    return ProviderSpec(
        name=name, model=model, endpoint_id=endpoint, invoke=invoke)


def test_success_is_cached_and_warm_read_skips_provider(tmp_path):
    calls = 0

    def invoke(_request):
        nonlocal calls
        calls += 1
        return "answer"

    runtime = LLMRuntime(LLMRuntimeConfig(
        cache_mode="readwrite", cache_dir=tmp_path / "cache",
        max_transport_attempts=1))
    request = LLMRequest(prompt="Explain Erie.", operation="test.answer")
    provider = _provider(invoke)

    first = runtime.execute(request, [provider])
    second = runtime.execute(request, [provider])

    assert first.cache_status == "miss"
    assert second.cache_status == "hit"
    assert first.request_id == second.request_id
    assert second.text == "answer"
    assert calls == 1
    assert len(list((tmp_path / "cache").rglob("*.json"))) == 1
    counts = runtime.report_payload()["counts"]
    assert counts["provider_calls"] == 1
    assert counts["transport_admissions"] == 1


def test_request_id_covers_semantic_inputs(tmp_path):
    runtime = LLMRuntime(LLMRuntimeConfig(
        cache_mode="off", cache_dir=tmp_path / "cache"))

    def invoke(_request):
        return "answer"

    primary = _provider(invoke)
    secondary = _provider(
        invoke, name="secondary", model="model-b",
        endpoint="https://secondary.test/v1")
    base = LLMRequest(prompt="prompt", operation="operation")

    cases = [
        (replace(base, prompt="different"), [primary]),
        (replace(base, operation="different"), [primary]),
        (replace(base, prompt_version="2"), [primary]),
        (replace(base, max_tokens=257), [primary]),
        (replace(base, thinking=True), [primary]),
        (replace(base, temperature=0.2), [primary]),
        (replace(base, timeout=31), [primary]),
        (replace(base, fallback_policy="none"), [primary]),
        (base, [replace(primary, model="model-c")]),
        (base, [replace(primary, endpoint_id="https://other.test/v1")]),
        (base, [replace(
            primary,
            cache_namespace_id=(
                "v1:sha256:" + "a" * 64),
        )]),
        (base, [secondary, primary]),
    ]

    base_id = runtime.execute(base, [primary]).request_id
    varied_ids = {
        runtime.execute(request, providers).request_id
        for request, providers in cases
    }

    assert base_id not in varied_ids
    assert len(varied_ids) == len(cases)


def test_cache_and_single_flight_are_isolated_by_opaque_namespace(tmp_path):
    calls = []

    def invoke(_request):
        calls.append(threading.current_thread().name)
        return f"answer-{len(calls)}"

    runtime = LLMRuntime(LLMRuntimeConfig(
        cache_mode="readwrite", cache_dir=tmp_path / "cache"))
    request = LLMRequest(prompt="same prompt", operation="test.namespace")
    first = _provider(invoke)
    first = replace(first, cache_namespace_id="v1:sha256:" + "a" * 64)
    second = replace(first, cache_namespace_id="v1:sha256:" + "b" * 64)

    result_a = runtime.execute(request, [first])
    result_b = runtime.execute(request, [second])
    warm_a = runtime.execute(request, [first])

    assert result_a.request_id != result_b.request_id
    assert result_a.text == warm_a.text
    assert result_b.text != result_a.text
    assert warm_a.cache_status == "hit"
    assert len(calls) == 2
    assert len(list((tmp_path / "cache").rglob("*.json"))) == 2
    assert runtime.report_payload()["providers"]["primary"][
        "cache_namespace_ids"] == [
            "v1:sha256:" + "a" * 64,
            "v1:sha256:" + "b" * 64,
        ]


def test_legacy_ambiguous_cache_record_fails_closed(tmp_path):
    calls = 0

    def invoke(_request):
        nonlocal calls
        calls += 1
        return f"answer-{calls}"

    cache_dir = tmp_path / "cache"
    runtime = LLMRuntime(LLMRuntimeConfig(
        cache_mode="readwrite", cache_dir=cache_dir))
    request = LLMRequest(prompt="prompt", operation="test.legacy_cache")
    provider = _provider(invoke)
    assert runtime.execute(request, [provider]).text == "answer-1"

    cache_path = next(cache_dir.rglob("*.json"))
    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    payload["schema_version"] = 2
    cache_path.write_text(json.dumps(payload), encoding="utf-8")

    repaired = runtime.execute(request, [provider])

    assert repaired.text == "answer-2"
    assert repaired.cache_status == "miss"
    assert runtime.report_payload()["counts"]["cache_corrupt"] == 1


def test_cache_omits_prompt_endpoint_and_credentials(tmp_path):
    prompt = "PROMPT_CANARY_7c3f"
    api_key = "KEY_CANARY_9d2a"
    endpoint = "https://user:password@provider.test/v1?token=URL_CANARY"

    def invoke(_request):
        assert api_key
        return "persisted response"

    runtime = LLMRuntime(LLMRuntimeConfig(
        cache_mode="readwrite", cache_dir=tmp_path / "cache"))
    runtime.execute(
        LLMRequest(prompt=prompt, operation="test.privacy"),
        [_provider(invoke, endpoint=endpoint)],
    )

    cache_text = next((tmp_path / "cache").rglob("*.json")).read_text(
        encoding="utf-8")
    assert "persisted response" in cache_text
    assert prompt not in cache_text
    assert api_key not in cache_text
    assert endpoint not in cache_text
    assert "password" not in cache_text
    assert "URL_CANARY" not in cache_text


def test_tampered_cache_is_missed_and_repaired(tmp_path):
    calls = 0

    def invoke(_request):
        nonlocal calls
        calls += 1
        return f"answer-{calls}"

    cache_dir = tmp_path / "cache"
    runtime = LLMRuntime(LLMRuntimeConfig(
        cache_mode="readwrite", cache_dir=cache_dir))
    request = LLMRequest(prompt="prompt", operation="test.corruption")
    provider = _provider(invoke)
    assert runtime.execute(request, [provider]).text == "answer-1"

    cache_path = next(cache_dir.rglob("*.json"))
    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    payload["result"]["text"] = "tampered"
    cache_path.write_text(json.dumps(payload), encoding="utf-8")

    assert runtime.execute(request, [provider]).text == "answer-2"
    assert runtime.execute(request, [provider]).cache_status == "hit"
    assert calls == 2
    assert runtime.report_payload()["counts"]["cache_corrupt"] == 1


def test_failed_results_are_not_cached(tmp_path):
    responses = iter([None, "recovered"])
    calls = 0

    def invoke(_request):
        nonlocal calls
        calls += 1
        return next(responses)

    runtime = LLMRuntime(LLMRuntimeConfig(
        cache_mode="readwrite", cache_dir=tmp_path / "cache"))
    request = LLMRequest(prompt="prompt", operation="test.retry")
    provider = _provider(invoke)

    assert runtime.execute(request, [provider]).succeeded is False
    assert runtime.execute(request, [provider]).text == "recovered"
    assert runtime.execute(request, [provider]).cache_status == "hit"
    assert calls == 2


def test_readonly_cache_miss_never_writes(tmp_path):
    calls = 0

    def invoke(_request):
        nonlocal calls
        calls += 1
        return "answer"

    cache_dir = tmp_path / "cache"
    runtime = LLMRuntime(LLMRuntimeConfig(
        cache_mode="readonly", cache_dir=cache_dir))
    request = LLMRequest(prompt="prompt", operation="test.readonly")
    provider = _provider(invoke)

    assert runtime.execute(request, [provider]).cache_status == "miss"
    assert runtime.execute(request, [provider]).cache_status == "miss"
    assert calls == 2
    assert not list(cache_dir.rglob("*.json"))


def test_refresh_replaces_cached_fallback_with_recovered_primary(tmp_path):
    primary_healthy = False
    calls = {"primary": 0, "secondary": 0}

    def primary(_request):
        calls["primary"] += 1
        return "preferred" if primary_healthy else None

    def secondary(_request):
        calls["secondary"] += 1
        return "fallback"

    runtime = LLMRuntime(LLMRuntimeConfig(
        cache_mode="readwrite", cache_dir=tmp_path / "cache"))
    request = LLMRequest(prompt="prompt", operation="test.refresh")
    providers = [
        _provider(primary),
        _provider(secondary, name="secondary", model="model-b"),
    ]

    assert runtime.execute(request, providers).provider == "secondary"
    assert runtime.execute(request, providers).provider == "secondary"
    primary_healthy = True
    refreshed = runtime.execute(
        replace(request, cache_mode="refresh"), providers)
    warm = runtime.execute(request, providers)

    assert refreshed.provider == "primary"
    assert refreshed.cache_status == "refresh"
    assert warm.provider == "primary"
    assert warm.cache_status == "hit"
    assert calls == {"primary": 2, "secondary": 1}


def test_cache_write_failure_preserves_live_result(monkeypatch, tmp_path):
    runtime = LLMRuntime(LLMRuntimeConfig(
        cache_mode="readwrite", cache_dir=tmp_path / "cache"))

    def fail_write(*_args, **_kwargs):
        raise ValueError("disk encoder failed")

    monkeypatch.setattr(runtime, "_write_cache", fail_write)
    result = runtime.execute(
        LLMRequest(prompt="prompt", operation="test.cache_write"),
        [_provider(lambda _request: "live answer")],
    )

    assert result.text == "live answer"
    assert result.succeeded is True
    assert runtime.report_payload()["counts"]["cache_write_errors"] == 1


def test_single_flight_collapses_concurrent_requests(monkeypatch, tmp_path):
    runtime = LLMRuntime(LLMRuntimeConfig(
        cache_mode="off", cache_dir=tmp_path / "cache",
        max_transport_attempts=1))
    request = LLMRequest(prompt="prompt", operation="test.single_flight")
    task_barrier = threading.Barrier(8)
    all_joined = threading.Event()
    join_lock = threading.Lock()
    joins = 0
    calls = 0
    original_join = runtime._join_flight

    def observed_join(key):
        nonlocal joins
        value = original_join(key)
        with join_lock:
            joins += 1
            if joins == 8:
                all_joined.set()
        return value

    def invoke(_request):
        nonlocal calls
        calls += 1
        assert all_joined.wait(timeout=5)
        return "shared answer"

    monkeypatch.setattr(runtime, "_join_flight", observed_join)
    provider = _provider(invoke)

    def run_one():
        task_barrier.wait(timeout=5)
        return runtime.execute(request, [provider])

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _index: run_one(), range(8)))

    assert calls == 1
    assert {result.text for result in results} == {"shared answer"}
    assert sum(result.cache_status == "disabled" for result in results) == 1
    assert sum(result.cache_status == "shared" for result in results) == 7
    assert len({result.request_id for result in results}) == 1
    counts = runtime.report_payload()["counts"]
    assert counts["provider_calls"] == 1
    assert counts["transport_admissions"] == 1


def test_ordered_fallback_records_actual_provider_and_attempts(tmp_path):
    def primary(_request):
        raise TimeoutError("timed out")

    runtime = LLMRuntime(LLMRuntimeConfig(
        cache_mode="off", cache_dir=tmp_path / "cache"))
    result = runtime.execute(
        LLMRequest(prompt="prompt", operation="test.fallback"),
        [
            _provider(primary),
            _provider(
                lambda _request: "secondary answer",
                name="secondary", model="model-b"),
        ],
    )

    assert result.provider == "secondary"
    assert result.model == "model-b"
    assert result.attempts == 2
    assert result.fallback_path == ("primary", "secondary")
    report = runtime.report_payload()
    assert report["providers"]["primary"]["failed"] == 1
    assert report["providers"]["secondary"]["succeeded"] == 1


def test_fallback_none_does_not_invoke_secondary(tmp_path):
    secondary_calls = 0

    def secondary(_request):
        nonlocal secondary_calls
        secondary_calls += 1
        return "unexpected"

    runtime = LLMRuntime(LLMRuntimeConfig(
        cache_mode="off", cache_dir=tmp_path / "cache"))
    result = runtime.execute(
        LLMRequest(
            prompt="prompt", operation="test.no_fallback",
            fallback_policy="none"),
        [_provider(lambda _request: None),
         _provider(secondary, name="secondary", model="model-b")],
    )

    assert result.succeeded is False
    assert result.error_category == "empty_response"
    assert result.fallback_path == ("primary",)
    assert secondary_calls == 0


def test_strict_policy_from_runtime_raises_structured_error(tmp_path):
    runtime = LLMRuntime(LLMRuntimeConfig(
        cache_mode="off", cache_dir=tmp_path / "cache",
        failure_policy="strict"))

    with pytest.raises(LLMExecutionError) as error:
        runtime.execute(
            LLMRequest(prompt="prompt", operation="test.strict"),
            [_provider(lambda _request: None)],
        )

    assert error.value.result.error_category == "empty_response"
    assert error.value.result.fallback_path == ("primary",)
    assert runtime.report_payload()["counts"]["failed"] == 1


def test_provider_dispatch_budget_is_hard(tmp_path):
    calls = 0

    def invoke(_request):
        nonlocal calls
        calls += 1
        return "answer"

    runtime = LLMRuntime(LLMRuntimeConfig(
        cache_mode="off", cache_dir=tmp_path / "cache",
        max_provider_calls=1))
    provider = _provider(invoke)
    runtime.execute(
        LLMRequest(prompt="first", operation="test.budget"), [provider])

    with pytest.raises(LLMBudgetExceeded):
        runtime.execute(
            LLMRequest(prompt="second", operation="test.budget"), [provider])

    counts = runtime.report_payload()["counts"]
    assert calls == 1
    assert counts["provider_calls"] == 1
    assert counts["transport_admissions"] == 1
    assert counts["budget_rejections"] == 1


@pytest.mark.parametrize("invalid_limit", [0, -1, True, 1.5])
def test_transport_attempt_budget_requires_positive_integer(
        tmp_path, invalid_limit):
    with pytest.raises(ValueError, match="max_transport_attempts"):
        LLMRuntime(LLMRuntimeConfig(
            cache_mode="off", cache_dir=tmp_path / "cache",
            max_transport_attempts=invalid_limit))


def test_transport_budget_preserves_legacy_provider_callbacks(tmp_path):
    calls = 0

    def invoke(_request):
        nonlocal calls
        calls += 1
        return "answer"

    runtime = LLMRuntime(LLMRuntimeConfig(
        cache_mode="off", cache_dir=tmp_path / "cache",
        max_transport_attempts=1))

    result = runtime.execute(
        LLMRequest(prompt="prompt", operation="test.legacy_transport"),
        [_provider(invoke)],
    )

    assert result.text == "answer"
    assert calls == 1
    counts = runtime.report_payload()["counts"]
    assert counts["provider_calls"] == 1
    assert counts["transport_admissions"] == 1
    assert counts["transport_attempts"] == 1


def test_custom_provider_retry_hook_accounts_additional_transport(tmp_path):
    calls = []

    def invoke(request):
        calls.append("first")
        request.admit_transport_retry()
        calls.append("retry")
        return "answer"

    runtime = LLMRuntime(LLMRuntimeConfig(
        cache_mode="off", cache_dir=tmp_path / "cache",
        max_transport_attempts=2))

    result = runtime.execute(
        LLMRequest(prompt="prompt", operation="test.custom_retry"),
        [_provider(invoke)],
    )

    assert result.text == "answer"
    assert result.transport_attempts == 2
    assert calls == ["first", "retry"]
    report = runtime.report_payload()
    assert report["counts"]["transport_admissions"] == 2
    assert report["counts"]["transport_attempts"] == 2
    assert report["providers"]["primary"]["transport_admissions"] == 2


def test_capped_runtime_rejects_unadmitted_structured_retry(tmp_path):
    runtime = LLMRuntime(LLMRuntimeConfig(
        cache_mode="off", cache_dir=tmp_path / "cache",
        max_transport_attempts=1))
    provider = _provider(lambda _request: ProviderResponse(
        text="answer", transport_attempts=2))

    with pytest.raises(LLMBudgetExceeded):
        runtime.execute(
            LLMRequest(prompt="prompt", operation="test.unadmitted_retry"),
            [provider],
        )

    counts = runtime.report_payload()["counts"]
    assert counts["provider_calls"] == 1
    assert counts["transport_admissions"] == 1
    assert counts["transport_attempts"] == 2
    assert counts["transport_contract_violations"] == 1
    assert counts["budget_rejections"] == 1


def test_unadmitted_malformed_response_stops_fallback(tmp_path):
    secondary_calls = 0

    def secondary(_request):
        nonlocal secondary_calls
        secondary_calls += 1
        return "unexpected"

    runtime = LLMRuntime(LLMRuntimeConfig(
        cache_mode="off", cache_dir=tmp_path / "cache",
        max_transport_attempts=3))

    with pytest.raises(LLMBudgetExceeded):
        runtime.execute(
            LLMRequest(prompt="prompt", operation="test.malformed_retry"),
            [
                _provider(lambda _request: ProviderResponse(
                    text="answer", prompt_tokens=5,
                    transport_attempts=2)),
                _provider(secondary, name="secondary", model="model-b"),
            ],
        )

    counts = runtime.report_payload()["counts"]
    assert secondary_calls == 0
    assert counts["provider_calls"] == 1
    assert counts["transport_admissions"] == 1
    assert counts["transport_attempts"] == 2
    assert counts["transport_contract_violations"] == 1


def test_unadmitted_provider_error_stops_fallback(tmp_path):
    secondary_calls = 0

    def primary(_request):
        raise ProviderCallError("rate_limited", transport_attempts=2)

    def secondary(_request):
        nonlocal secondary_calls
        secondary_calls += 1
        return "unexpected"

    runtime = LLMRuntime(LLMRuntimeConfig(
        cache_mode="off", cache_dir=tmp_path / "cache",
        max_transport_attempts=3))

    with pytest.raises(LLMBudgetExceeded):
        runtime.execute(
            LLMRequest(prompt="prompt", operation="test.unadmitted_error"),
            [
                _provider(primary),
                _provider(secondary, name="secondary", model="model-b"),
            ],
        )

    counts = runtime.report_payload()["counts"]
    assert secondary_calls == 0
    assert counts["provider_calls"] == 1
    assert counts["transport_admissions"] == 1
    assert counts["transport_attempts"] == 2
    assert counts["transport_contract_violations"] == 1


def test_transport_attempt_budget_is_atomic_across_threads(tmp_path):
    workers = 12
    limit = 4
    task_barrier = threading.Barrier(workers)
    calls_lock = threading.Lock()
    calls = 0

    def invoke(_request):
        nonlocal calls
        with calls_lock:
            calls += 1
        return "answer"

    runtime = LLMRuntime(LLMRuntimeConfig(
        cache_mode="off", cache_dir=tmp_path / "cache",
        max_transport_attempts=limit))
    provider = _provider(invoke)

    def run_one(index):
        task_barrier.wait(timeout=5)
        try:
            runtime.execute(LLMRequest(
                prompt=f"prompt-{index}",
                operation="test.concurrent_transport_budget"), [provider])
        except LLMBudgetExceeded:
            return False
        return True

    with ThreadPoolExecutor(max_workers=workers) as pool:
        admitted = list(pool.map(run_one, range(workers)))

    counts = runtime.report_payload()["counts"]
    assert sum(admitted) == limit
    assert calls == limit
    assert counts["provider_calls"] == limit
    assert counts["transport_admissions"] == limit
    assert counts["transport_attempts"] == limit
    assert counts["budget_rejections"] == workers - limit


def test_exhausted_transport_budget_prevents_fallback_dispatch(tmp_path):
    calls = {"primary": 0, "secondary": 0}

    def primary(_request):
        calls["primary"] += 1
        return None

    def secondary(_request):
        calls["secondary"] += 1
        return "unexpected"

    runtime = LLMRuntime(LLMRuntimeConfig(
        cache_mode="off", cache_dir=tmp_path / "cache",
        max_transport_attempts=1))

    with pytest.raises(LLMBudgetExceeded):
        runtime.execute(
            LLMRequest(prompt="prompt", operation="test.transport_fallback"),
            [
                _provider(primary),
                _provider(secondary, name="secondary", model="model-b"),
            ],
        )

    counts = runtime.report_payload()["counts"]
    assert calls == {"primary": 1, "secondary": 0}
    assert counts["provider_calls"] == 1
    assert counts["transport_admissions"] == 1
    assert counts["transport_attempts"] == 1
    assert counts["budget_rejections"] == 1


def test_reserved_token_budget_uses_atomic_worst_case_admission(tmp_path):
    calls = 0

    def invoke(_request):
        nonlocal calls
        calls += 1
        return "answer"

    runtime = LLMRuntime(LLMRuntimeConfig(
        cache_mode="off", cache_dir=tmp_path / "cache",
        max_reserved_tokens=11))
    provider = _provider(invoke)
    runtime.execute(LLMRequest(
        prompt="abcd", operation="test.token_budget", max_tokens=10),
        [provider])

    with pytest.raises(LLMBudgetExceeded):
        runtime.execute(LLMRequest(
            prompt="efgh", operation="test.token_budget", max_tokens=10),
            [provider])

    counts = runtime.report_payload()["counts"]
    assert calls == 1
    assert counts["provider_calls"] == 1
    assert counts["transport_admissions"] == 1
    assert counts["reserved_tokens"] == 11
    assert counts["budget_rejections"] == 1


def test_cache_hit_consumes_no_additional_provider_budget(tmp_path):
    calls = 0

    def invoke(_request):
        nonlocal calls
        calls += 1
        return "answer"

    runtime = LLMRuntime(LLMRuntimeConfig(
        cache_mode="readwrite", cache_dir=tmp_path / "cache",
        max_provider_calls=1))
    request = LLMRequest(prompt="prompt", operation="test.cached_budget")
    provider = _provider(invoke)

    runtime.execute(request, [provider])
    assert runtime.execute(request, [provider]).cache_status == "hit"
    assert calls == 1


def test_events_and_report_are_aggregate_and_secret_safe(tmp_path):
    events_path = tmp_path / "events.jsonl"
    report_path = tmp_path / "report.json"
    runtime = LLMRuntime(LLMRuntimeConfig(
        cache_mode="readwrite", cache_dir=tmp_path / "cache",
        events_path=events_path, report_path=report_path,
        run_id="run-telemetry-123"))

    def primary(_request):
        raise TimeoutError("EXCEPTION_CANARY")

    providers = [
        _provider(
            primary, endpoint="https://ENDPOINT_CANARY.test/v1"),
        _provider(
            lambda _request: "RESPONSE_CANARY",
            name="secondary", model="model-b"),
    ]
    request = LLMRequest(
        prompt="PROMPT_CANARY", operation="test.telemetry")
    runtime.execute(request, providers)
    runtime.execute(request, providers)
    runtime.write_report()

    events = [json.loads(line) for line in events_path.read_text(
        encoding="utf-8").splitlines()]
    report = json.loads(report_path.read_text(encoding="utf-8"))
    combined = events_path.read_text(encoding="utf-8") + json.dumps(report)

    assert len(events) == 2
    assert events[0]["fallback_path"] == ["primary", "secondary"]
    assert events[1]["cache_status"] == "hit"
    assert report["counts"]["requests"] == 2
    assert report["counts"]["provider_calls"] == 2
    assert report["counts"]["cache_hits"] == 1
    assert report["run_id"] == "run-telemetry-123"
    assert all(event["run_id"] == "run-telemetry-123" for event in events)
    for canary in (
            "PROMPT_CANARY", "RESPONSE_CANARY", "EXCEPTION_CANARY",
            "ENDPOINT_CANARY"):
        assert canary not in combined


def test_event_write_failure_does_not_fail_generation(tmp_path):
    events_directory = tmp_path / "events"
    events_directory.mkdir()
    runtime = LLMRuntime(LLMRuntimeConfig(
        cache_mode="off", cache_dir=tmp_path / "cache",
        events_path=events_directory))

    result = runtime.execute(
        LLMRequest(prompt="prompt", operation="test.event_error"),
        [_provider(lambda _request: "answer")],
    )

    assert result.text == "answer"
    assert runtime.report_payload()["counts"]["event_write_errors"] == 1


def test_runtime_rejects_path_like_run_identifiers(tmp_path):
    with pytest.raises(ValueError, match="safe identifier"):
        LLMRuntime(LLMRuntimeConfig(
            cache_dir=tmp_path / "cache", run_id="../private"))


def test_runtime_rejects_aliased_event_and_report_outputs(tmp_path):
    output = tmp_path / "llm.json"

    with pytest.raises(ValueError, match="distinct files"):
        LLMRuntime(LLMRuntimeConfig(
            cache_dir=tmp_path / "cache", events_path=output,
            report_path=output))


@pytest.fixture
def isolated_rag_runtime(monkeypatch):
    runtime = LLMRuntime()
    monkeypatch.setattr(rag, "_llm_runtime", runtime)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    return runtime


def test_rag_structured_result_reports_actual_fallback(
        monkeypatch, isolated_rag_runtime):
    monkeypatch.setattr(
        rag, "_call_openai_compatible", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        rag, "_call_ollama", lambda *_args, **_kwargs: "local answer")

    result = rag._call_llm_result(
        "prompt", cloud_url="https://cloud.test/v1", cloud_model="cloud-m",
        cloud_key="secret", ollama_url="http://127.0.0.1:11434",
        ollama_model="local-m", operation="test.rag_fallback")

    assert result.text == "local answer"
    assert result.provider == "ollama"
    assert result.model == "local-m"
    assert result.attempts == 2
    assert result.fallback_path == ("cloud", "ollama")


def test_rag_facade_remains_string_or_none(monkeypatch, isolated_rag_runtime):
    monkeypatch.setattr(
        rag, "_call_ollama", lambda *_args, **_kwargs: "answer")
    assert rag._call_llm("prompt", cloud_url="") == "answer"
    assert rag._call_llm(
        "prompt", cloud_url="", ollama_url="", gemini_key="") is None


def test_rag_gemini_maps_request_thinking_to_current_model_contract(
        monkeypatch, isolated_rag_runtime):
    observed = []

    def fake_gemini(*_args, **kwargs):
        observed.append(kwargs)
        return ProviderResponse(text="answer")

    monkeypatch.setattr(rag, "_call_gemini", fake_gemini)
    policy = release_security.ReleaseSecurityPolicy(
        profile="development", network_policy="allow-cloud")

    for thinking in (False, True):
        result = rag._call_llm_result(
            "prompt", cloud_url="", ollama_url="", gemini_key="secret",
            thinking=thinking, security_policy=policy)
        assert result.text == "answer"

    assert [call["thinking_level"] for call in observed] == [
        "minimal", "high"]


def test_rag_minimax_m2_floor_is_bound_before_runtime_admission(
        monkeypatch, isolated_rag_runtime):
    observed = {}

    def fake_cloud(*_args, **kwargs):
        observed.update(kwargs)
        return ProviderResponse(text="answer")

    monkeypatch.setattr(rag, "_call_openai_compatible", fake_cloud)
    policy = release_security.ReleaseSecurityPolicy(
        profile="development", network_policy="allow-cloud")

    result = rag._call_llm_result(
        "prompt", cloud_url=rag.DEFAULT_CLOUD_URL,
        cloud_model="MiniMax-M2.7", cloud_key="secret", ollama_url="",
        thinking=True, max_tokens=64, security_policy=policy)

    assert result.text == "answer"
    assert observed["max_tokens"] == 512


def test_endpoint_identity_hashes_rejected_credential_bearing_url():
    raw = "HTTPS://user:password@Example.TEST:8443/v1/?token=secret#fragment"

    endpoint = rag._llm_endpoint_id(raw)

    assert endpoint.startswith("v1:invalid:sha256:")
    assert raw not in endpoint
    assert "user" not in endpoint
    assert "password" not in endpoint
    assert "secret" not in endpoint


@pytest.mark.parametrize("provider_kwargs", [
    {
        "cloud_url": "http://api.deepseek.com",
        "cloud_model": "deepseek-v4-pro",
        "cloud_key": "secret",
        "ollama_url": "",
    },
    {
        "cloud_url": "",
        "ollama_url": "http://localhost:11434",
    },
])
def test_rag_composition_validates_enabled_endpoints_before_runtime_cache(
        monkeypatch, isolated_rag_runtime, provider_kwargs):
    monkeypatch.setattr(
        isolated_rag_runtime,
        "execute",
        lambda *_args, **_kwargs: pytest.fail(
            "invalid endpoint must not reach runtime or its cache"),
    )

    with pytest.raises((TypeError, ValueError)):
        rag._call_llm_result("private prompt", **provider_kwargs)


def test_invalid_endpoint_cannot_reuse_a_warm_runtime_cache(tmp_path):
    cache_dir = tmp_path / "cache"
    runtime = LLMRuntime(LLMRuntimeConfig(
        cache_mode="readwrite", cache_dir=cache_dir))
    raw_url = "https://user:secret@provider.test/v1?tenant=private"
    request = LLMRequest(
        prompt="private prompt",
        operation="generic",
        prompt_version="1",
        max_tokens=256,
        thinking=False,
        timeout=30,
        fallback_policy="ordered",
        failure_policy="best-effort",
    )
    poisoned_provider = ProviderSpec(
        name="cloud",
        model="model",
        endpoint_id=rag._llm_endpoint_id(raw_url),
        invoke=lambda _request: "poisoned cached answer",
    )
    assert runtime.execute(request, [poisoned_provider]).succeeded

    original_runtime = rag._llm_runtime
    try:
        rag._llm_runtime = runtime
        with pytest.raises((TypeError, ValueError)):
            rag._call_llm_result(
                "private prompt",
                cloud_url=raw_url,
                cloud_model="model",
                cloud_key="secret",
                ollama_url="",
            )
    finally:
        rag._llm_runtime = original_runtime


def test_toc_parser_uses_one_runtime_dispatch(monkeypatch):
    observed = []

    def fake_call(prompt, **kwargs):
        observed.append((prompt, kwargs))
        return '[{"level": 1, "title": "Chapter One", "page": 1}]'

    monkeypatch.setattr(rag, "_call_llm", fake_call)
    entries = rag._llm_parse_toc("Chapter One 1", cloud_key="key")

    assert len(entries) == 1
    assert len(observed) == 1
    assert observed[0][1]["operation"] == "toc.parse"
    assert observed[0][1]["timeout"] == 60


def test_scaffold_parser_does_not_swallow_budget_exhaustion(monkeypatch):
    monkeypatch.setattr(
        rag, "_call_llm",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            LLMBudgetExceeded("limit")))

    with pytest.raises(LLMBudgetExceeded):
        rag._llm_parse_scaffold("Chapter One 1")


def test_cli_runtime_flags_configure_run_and_write_report(
        monkeypatch, tmp_path, isolated_rag_runtime):
    report_path = tmp_path / "llm-report.json"
    cache_dir = tmp_path / "cache"
    observed = {}

    def fake_query(*_args, **_kwargs):
        observed["config"] = rag._llm_runtime.config

    monkeypatch.setattr(rag, "_query_index_for_backend", fake_query)
    monkeypatch.setattr(sys, "argv", [
        "rag.py", "query", "Erie doctrine",
        "--llm-cache-mode", "refresh",
        "--llm-cache-dir", str(cache_dir),
        "--llm-report", str(report_path),
        "--llm-fallback", "none",
        "--llm-failure-policy", "strict",
        "--max-llm-calls", "7",
        "--max-llm-transport-attempts", "11",
        "--max-llm-reserved-tokens", "900",
    ])

    rag.main()

    configured = observed["config"]
    assert configured.cache_mode == "refresh"
    assert configured.cache_dir == cache_dir
    assert configured.report_path == report_path
    assert configured.fallback_policy == "none"
    assert configured.failure_policy == "strict"
    assert configured.max_provider_calls == 7
    assert configured.max_transport_attempts == 11
    assert configured.max_reserved_tokens == 900
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["configuration"]["cache_mode"] == "refresh"
    assert report["configuration"]["max_transport_attempts"] == 11
    assert report["counts"]["requests"] == 0
    assert isolated_rag_runtime.config.cache_mode == "off"
