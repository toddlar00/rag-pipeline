import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

import rag
from llm_runtime import (
    LLMBudgetExceeded,
    LLMExecutionError,
    LLMRequest,
    LLMRuntime,
    LLMRuntimeConfig,
    ProviderSpec,
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
        cache_mode="readwrite", cache_dir=tmp_path / "cache"))
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
    assert runtime.report_payload()["counts"]["provider_calls"] == 1


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
        (base, [secondary, primary]),
    ]

    base_id = runtime.execute(base, [primary]).request_id
    varied_ids = {
        runtime.execute(request, providers).request_id
        for request, providers in cases
    }

    assert base_id not in varied_ids
    assert len(varied_ids) == len(cases)


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
        cache_mode="off", cache_dir=tmp_path / "cache"))
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
    assert runtime.report_payload()["counts"]["provider_calls"] == 1


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
        cloud_key="secret", ollama_url="http://localhost:11434",
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


def test_endpoint_identity_strips_credentials_query_and_fragment():
    endpoint = rag._llm_endpoint_id(
        "HTTPS://user:password@Example.TEST:8443/v1/?token=secret#fragment")

    assert endpoint == "https://example.test:8443/v1"
    assert "user" not in endpoint
    assert "password" not in endpoint
    assert "secret" not in endpoint


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
    assert configured.max_reserved_tokens == 900
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["configuration"]["cache_mode"] == "refresh"
    assert report["counts"]["requests"] == 0
    assert isolated_rag_runtime.config.cache_mode == "off"
