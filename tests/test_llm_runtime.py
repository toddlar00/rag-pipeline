import hashlib
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

import rag
import release_security
import llm_output_contracts as output_contracts
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


def _contract_request(*, operation="test.output_contract",
                      failure_policy=None, contract_id="test-enum-v1",
                      fallback_id="deterministic-fallback-v1",
                      validator=None):
    validator = validator or output_contracts.ExactEnumContract(
        contract_id=contract_id,
        allowed_values=("case_opinion", "footnote"),
        max_bytes=64,
    )
    return LLMRequest(
        prompt="classify this text",
        operation=operation,
        failure_policy=failure_policy,
        output_contract_id=contract_id,
        output_fallback_id=fallback_id,
        output_validator=validator,
    )


def _toc_contract_request(*, failure_policy=None):
    return LLMRequest(
        prompt=(
            "parse bounded TOC source\n"
            + rag._TOC_HIERARCHY_CONTRACT_PROMPT_JSON),
        operation="toc.scaffold",
        prompt_version="2",
        failure_policy=failure_policy,
        output_contract_id=output_contracts.TOC_HIERARCHY_CONTRACT_ID,
        output_fallback_id=output_contracts.TOC_SCAFFOLD_FALLBACK_ID,
        output_validator=output_contracts.TOC_HIERARCHY_CONTRACT,
    )


def _layout_contract_value():
    return {
        "page_number_format": "trailing Arabic number",
        "division_pattern": "Part followed by Roman numeral",
        "division_examples": ["Part I"],
        "section_markers": ["A."],
        "subsection_markers": ["1."],
        "named_item_format": "indented title",
        "hierarchy_order": ["Primary division", "Section", "Named item"],
        "hierarchy_levels": {
            "1": "Primary division", "2": "Section", "3": "Named item",
            "4": "", "5": "",
        },
    }


def _layout_contract_request(*, failure_policy=None):
    return LLMRequest(
        prompt=(
            "analyze bounded TOC layout\n"
            + rag._TOC_LAYOUT_CONTRACT_PROMPT_JSON),
        operation="toc.layout",
        prompt_version="2",
        failure_policy=failure_policy,
        output_contract_id=output_contracts.TOC_LAYOUT_CONTRACT_ID,
        output_fallback_id=output_contracts.TOC_LAYOUT_FALLBACK_ID,
        output_validator=output_contracts.TOC_LAYOUT_CONTRACT,
    )


def _verification_contract_request(*, failure_policy=None):
    return LLMRequest(
        prompt=(
            "verify bounded TOC entry\n"
            + rag._TOC_VERIFICATION_CONTRACT_PROMPT_JSON),
        operation="toc.verify",
        prompt_version="2",
        failure_policy=failure_policy,
        output_contract_id=output_contracts.TOC_VERIFICATION_CONTRACT_ID,
        output_fallback_id=output_contracts.TOC_VERIFICATION_FALLBACK_ID,
        output_validator=output_contracts.TOC_VERIFICATION_CONTRACT,
    )


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


def test_deeply_nested_cache_record_is_corrupt_not_fatal(tmp_path):
    """A small, deeply nested cache file must not poison every later call."""
    calls = 0

    def invoke(_request):
        nonlocal calls
        calls += 1
        return f"answer-{calls}"

    cache_dir = tmp_path / "cache"
    runtime = LLMRuntime(LLMRuntimeConfig(
        cache_mode="readwrite", cache_dir=cache_dir))
    request = LLMRequest(prompt="prompt", operation="test.nesting")
    provider = _provider(invoke)
    assert runtime.execute(request, [provider]).text == "answer-1"

    # Deep enough to exceed the interpreter recursion limit on CPython 3.10-
    # 3.13 and the C stack guard on 3.14, while staying far below the 32 MiB
    # cache-record ceiling that would have rejected the file first.
    depth = 200_000
    cache_path = next(cache_dir.rglob("*.json"))
    cache_path.write_text(
        "[" * depth + "]" * depth, encoding="utf-8")

    assert runtime.execute(request, [provider]).cache_status == "miss"
    assert calls == 2
    assert runtime.report_payload()["counts"]["cache_corrupt"] == 1
    assert runtime.execute(request, [provider]).cache_status == "hit"


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


def test_output_contract_canonicalizes_and_caches_only_accepted_text(tmp_path):
    calls = 0

    def invoke(_request):
        nonlocal calls
        calls += 1
        return " \tCASE_OPINION\r\n"

    runtime = LLMRuntime(LLMRuntimeConfig(
        cache_mode="readwrite", cache_dir=tmp_path / "cache"))
    request = _contract_request()
    provider = _provider(invoke)

    first = runtime.execute(request, [provider])
    second = runtime.execute(request, [provider])

    assert first.text == second.text == "case_opinion"
    assert first.output_contract_status == "accepted"
    assert second.output_contract_status == "accepted"
    assert first.output_diagnostic_code is None
    assert first.output_fallback_id == "deterministic-fallback-v1"
    assert second.cache_status == "hit"
    assert calls == 1
    cache_text = next((tmp_path / "cache").rglob("*.json")).read_text(
        encoding="utf-8")
    assert "CASE_OPINION" not in cache_text
    assert "case_opinion" in cache_text
    report = runtime.report_payload()
    assert report["counts"]["output_contract_accepted"] == 2
    assert report["output_contracts"] == [{
        "operation": "test.output_contract",
        "contract_id": "test-enum-v1",
        "accepted": 2,
        "rejected": 0,
        "not_evaluated": 0,
        "fallbacks": 0,
        "diagnostic_codes": {},
        "fallback_ids": ["deterministic-fallback-v1"],
    }]


def test_toc_contract_canonicalizes_live_output_and_revalidates_cache(
        tmp_path):
    calls = 0

    def invoke(_request):
        nonlocal calls
        calls += 1
        return ' [ { "title":"Chapter One", "page":7, "level":1 } ] '

    runtime = LLMRuntime(LLMRuntimeConfig(
        cache_mode="readwrite", cache_dir=tmp_path / "cache"))
    request = _toc_contract_request()
    provider = _provider(invoke)

    live = runtime.execute(request, [provider])
    cached = runtime.execute(request, [provider])

    canonical = '[{"level":1,"page":7,"title":"Chapter One"}]'
    assert live.text == cached.text == canonical
    assert live.output_contract_status == "accepted"
    assert cached.output_contract_status == "accepted"
    assert cached.cache_status == "hit"
    assert calls == 1
    cache_text = next((tmp_path / "cache").rglob("*.json")).read_text(
        encoding="utf-8")
    assert canonical.replace('"', '\\"') in cache_text


def test_toc_contract_rejection_is_content_free_in_best_effort_and_strict(
        tmp_path):
    response = (
        'MODEL_RESPONSE_CANARY '
        '[{"level":1,"title":"Chapter One","page":7}]')
    provider = _provider(lambda _request: response)

    best_effort = LLMRuntime(LLMRuntimeConfig(
        cache_mode="readwrite", cache_dir=tmp_path / "best-cache"))
    result = best_effort.execute(_toc_contract_request(), [provider])

    assert result.text == ""
    assert result.error_category == "invalid_response"
    assert result.output_contract_status == "rejected"
    assert result.output_diagnostic_code == output_contracts.JSON_SYNTAX
    assert result.output_fallback_id == (
        output_contracts.TOC_SCAFFOLD_FALLBACK_ID)
    assert not list((tmp_path / "best-cache").rglob("*.json"))
    assert "MODEL_RESPONSE_CANARY" not in json.dumps(
        best_effort.report_payload())

    strict = LLMRuntime(LLMRuntimeConfig(
        cache_mode="off", cache_dir=tmp_path / "strict-cache",
        failure_policy="strict"))
    with pytest.raises(LLMExecutionError) as caught:
        strict.execute(_toc_contract_request(), [provider])
    assert caught.value.result.output_diagnostic_code == (
        output_contracts.JSON_SYNTAX)
    assert "MODEL_RESPONSE_CANARY" not in str(caught.value)


def test_toc_layout_contract_canonicalizes_live_output_and_revalidates_cache(
        tmp_path):
    calls = 0
    value = _layout_contract_value()
    value["page_number_format"] = "  trailing Arabic number  "

    def invoke(_request):
        nonlocal calls
        calls += 1
        return " \n" + json.dumps(value, ensure_ascii=False) + "\t"

    runtime = LLMRuntime(LLMRuntimeConfig(
        cache_mode="readwrite", cache_dir=tmp_path / "cache"))
    request = _layout_contract_request()
    provider = _provider(invoke)

    live = runtime.execute(request, [provider])
    cached = runtime.execute(request, [provider])
    canonical = output_contracts.TOC_LAYOUT_CONTRACT(
        json.dumps(value, ensure_ascii=False))

    assert live.text == cached.text == canonical
    assert live.output_contract_status == "accepted"
    assert cached.output_contract_status == "accepted"
    assert cached.cache_status == "hit"
    assert calls == 1
    assert output_contracts.TOC_LAYOUT_CONTRACT.parse(cached.text)[
        "page_number_format"] == "trailing Arabic number"


def test_toc_layout_rejection_is_content_free_in_best_effort_and_strict(
        tmp_path):
    response = "MODEL_RESPONSE_CANARY " + json.dumps(
        _layout_contract_value())
    provider = _provider(lambda _request: response)

    best_effort = LLMRuntime(LLMRuntimeConfig(
        cache_mode="readwrite", cache_dir=tmp_path / "best-cache"))
    result = best_effort.execute(_layout_contract_request(), [provider])

    assert result.text == ""
    assert result.error_category == "invalid_response"
    assert result.output_contract_status == "rejected"
    assert result.output_diagnostic_code == output_contracts.JSON_SYNTAX
    assert result.output_fallback_id == output_contracts.TOC_LAYOUT_FALLBACK_ID
    assert not list((tmp_path / "best-cache").rglob("*.json"))
    assert "MODEL_RESPONSE_CANARY" not in json.dumps(
        best_effort.report_payload())

    strict = LLMRuntime(LLMRuntimeConfig(
        cache_mode="off", cache_dir=tmp_path / "strict-cache",
        failure_policy="strict"))
    with pytest.raises(LLMExecutionError) as caught:
        strict.execute(_layout_contract_request(), [provider])
    assert caught.value.result.output_diagnostic_code == (
        output_contracts.JSON_SYNTAX)
    assert "MODEL_RESPONSE_CANARY" not in str(caught.value)


def test_toc_layout_semantic_rejection_does_not_delegate_provider_authority(
        tmp_path):
    secondary_calls = 0

    def secondary(_request):
        nonlocal secondary_calls
        secondary_calls += 1
        return json.dumps(_layout_contract_value())

    runtime = LLMRuntime(LLMRuntimeConfig(
        cache_mode="off", cache_dir=tmp_path / "cache"))
    result = runtime.execute(
        _layout_contract_request(),
        [
            _provider(lambda _request: "{}"),
            _provider(secondary, name="secondary", model="model-b"),
        ],
    )

    assert result.error_category == "invalid_response"
    assert result.output_diagnostic_code == output_contracts.JSON_SHAPE_MISMATCH
    assert result.fallback_path == ("primary",)
    assert secondary_calls == 0


def test_toc_verification_contract_canonicalizes_live_and_cached_output(
        tmp_path):
    calls = 0

    def invoke(_request):
        nonlocal calls
        calls += 1
        return ' \n{ "verified" : true }\t'

    runtime = LLMRuntime(LLMRuntimeConfig(
        cache_mode="readwrite", cache_dir=tmp_path / "cache"))
    request = _verification_contract_request()
    provider = _provider(invoke)

    live = runtime.execute(request, [provider])
    cached = runtime.execute(request, [provider])

    assert live.text == cached.text == '{"verified":true}'
    assert live.output_contract_status == "accepted"
    assert cached.output_contract_status == "accepted"
    assert cached.cache_status == "hit"
    assert calls == 1
    assert output_contracts.TOC_VERIFICATION_CONTRACT.parse(cached.text) == {
        "verified": True,
    }
    cache_text = next((tmp_path / "cache").rglob("*.json")).read_text(
        encoding="utf-8")
    assert '{ \\"verified\\" : true }' not in cache_text


def test_toc_verification_rejection_is_content_free_best_effort_and_strict(
        tmp_path):
    response = 'MODEL_RESPONSE_CANARY {"verified":true}'
    provider = _provider(lambda _request: response)

    best_effort = LLMRuntime(LLMRuntimeConfig(
        cache_mode="readwrite", cache_dir=tmp_path / "best-cache"))
    result = best_effort.execute(
        _verification_contract_request(), [provider])

    assert result.text == ""
    assert result.error_category == "invalid_response"
    assert result.output_contract_status == "rejected"
    assert result.output_diagnostic_code == output_contracts.JSON_SYNTAX
    assert result.output_fallback_id == (
        output_contracts.TOC_VERIFICATION_FALLBACK_ID)
    assert not list((tmp_path / "best-cache").rglob("*.json"))
    assert "MODEL_RESPONSE_CANARY" not in json.dumps(
        best_effort.report_payload())

    strict = LLMRuntime(LLMRuntimeConfig(
        cache_mode="off", cache_dir=tmp_path / "strict-cache",
        failure_policy="strict"))
    with pytest.raises(LLMExecutionError) as caught:
        strict.execute(_verification_contract_request(), [provider])
    assert caught.value.result.output_diagnostic_code == (
        output_contracts.JSON_SYNTAX)
    assert "MODEL_RESPONSE_CANARY" not in str(caught.value)


def test_toc_verification_semantic_rejection_does_not_delegate_authority(
        tmp_path):
    secondary_calls = 0

    def secondary(_request):
        nonlocal secondary_calls
        secondary_calls += 1
        return '{"verified":true}'

    runtime = LLMRuntime(LLMRuntimeConfig(
        cache_mode="off", cache_dir=tmp_path / "cache"))
    result = runtime.execute(
        _verification_contract_request(),
        [
            _provider(lambda _request: '{"verified":"true"}'),
            _provider(secondary, name="secondary", model="model-b"),
        ],
    )

    assert result.error_category == "invalid_response"
    assert result.output_diagnostic_code == output_contracts.JSON_SHAPE_MISMATCH
    assert result.fallback_path == ("primary",)
    assert secondary_calls == 0


def test_toc_verification_false_is_accepted_and_not_missing(tmp_path):
    secondary_calls = 0

    def secondary(_request):
        nonlocal secondary_calls
        secondary_calls += 1
        return '{"verified":true}'

    runtime = LLMRuntime(LLMRuntimeConfig(
        cache_mode="off", cache_dir=tmp_path / "cache"))
    result = runtime.execute(
        _verification_contract_request(),
        [
            _provider(lambda _request: '{"verified":false}'),
            _provider(secondary, name="secondary", model="model-b"),
        ],
    )

    assert result.succeeded is True
    assert result.text == '{"verified":false}'
    assert result.output_contract_status == "accepted"
    assert result.fallback_path == ("primary",)
    assert secondary_calls == 0


def test_toc_verification_empty_response_can_use_ordered_provider_fallback(
        tmp_path):
    secondary_calls = 0

    def secondary(_request):
        nonlocal secondary_calls
        secondary_calls += 1
        return '{"verified":true}'

    runtime = LLMRuntime(LLMRuntimeConfig(
        cache_mode="off", cache_dir=tmp_path / "cache"))
    result = runtime.execute(
        _verification_contract_request(),
        [
            _provider(lambda _request: ""),
            _provider(secondary, name="secondary", model="model-b"),
        ],
    )

    assert result.succeeded is True
    assert result.text == '{"verified":true}'
    assert result.output_contract_status == "accepted"
    assert result.fallback_path == ("primary", "secondary")
    assert secondary_calls == 1


def test_toc_semantic_rejection_does_not_delegate_provider_authority(
        tmp_path):
    secondary_calls = 0

    def secondary(_request):
        nonlocal secondary_calls
        secondary_calls += 1
        return '[{"level":1,"title":"Chapter One","page":1}]'

    runtime = LLMRuntime(LLMRuntimeConfig(
        cache_mode="off", cache_dir=tmp_path / "cache"))
    result = runtime.execute(
        _toc_contract_request(),
        [
            _provider(lambda _request: "[]"),
            _provider(secondary, name="secondary", model="model-b"),
        ],
    )

    assert result.error_category == "invalid_response"
    assert result.output_diagnostic_code == output_contracts.JSON_ITEM_LIMIT
    assert result.fallback_path == ("primary",)
    assert secondary_calls == 0


def test_rejected_output_is_never_cached_and_best_effort_receipts_fallback(
        tmp_path):
    calls = 0

    def invoke(_request):
        nonlocal calls
        calls += 1
        return "MODEL_RESPONSE_CANARY says case_opinion"

    runtime = LLMRuntime(LLMRuntimeConfig(
        cache_mode="readwrite", cache_dir=tmp_path / "cache"))
    request = _contract_request()
    provider = _provider(invoke)

    first = runtime.execute(request, [provider])
    second = runtime.execute(request, [provider])

    assert first.succeeded is second.succeeded is False
    assert first.text == second.text == ""
    assert first.error_category == "invalid_response"
    assert first.output_contract_status == "rejected"
    assert first.output_diagnostic_code == output_contracts.LABEL_MISMATCH
    assert calls == 2
    assert not list((tmp_path / "cache").rglob("*.json"))
    report = runtime.report_payload()
    assert report["counts"]["output_contract_rejected"] == 2
    assert report["counts"]["output_contract_fallbacks"] == 2
    assert report["output_contracts"][0]["fallbacks"] == 2
    assert report["output_contracts"][0]["diagnostic_codes"] == {
        output_contracts.LABEL_MISMATCH: 2,
    }
    assert "MODEL_RESPONSE_CANARY" not in json.dumps(report)


def test_output_contract_revalidates_and_repairs_noncanonical_cache(tmp_path):
    calls = 0

    def invoke(_request):
        nonlocal calls
        calls += 1
        return "case_opinion" if calls == 1 else "footnote"

    cache_dir = tmp_path / "cache"
    runtime = LLMRuntime(LLMRuntimeConfig(
        cache_mode="readwrite", cache_dir=cache_dir))
    request = _contract_request()
    provider = _provider(invoke)
    assert runtime.execute(request, [provider]).text == "case_opinion"

    cache_path = next(cache_dir.rglob("*.json"))
    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    payload["result"]["text"] = " case_opinion "
    encoded_result = json.dumps(
        payload["result"], ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")
    payload["result_sha256"] = hashlib.sha256(encoded_result).hexdigest()
    payload["response_sha256"] = hashlib.sha256(
        b" case_opinion ").hexdigest()
    cache_path.write_text(json.dumps(payload), encoding="utf-8")

    repaired = runtime.execute(request, [provider])

    assert repaired.text == "footnote"
    assert repaired.cache_status == "miss"
    assert calls == 2
    assert runtime.report_payload()["counts"]["cache_corrupt"] == 1


def test_semantic_rejection_never_delegates_authority_to_second_provider(
        tmp_path):
    secondary_calls = 0

    def secondary(_request):
        nonlocal secondary_calls
        secondary_calls += 1
        return "case_opinion"

    runtime = LLMRuntime(LLMRuntimeConfig(
        cache_mode="off", cache_dir=tmp_path / "cache"))
    result = runtime.execute(
        _contract_request(),
        [
            _provider(lambda _request: "not case_opinion"),
            _provider(secondary, name="secondary", model="model-b"),
        ],
    )

    assert result.error_category == "invalid_response"
    assert result.attempts == 1
    assert result.fallback_path == ("primary",)
    assert result.output_contract_status == "rejected"
    assert secondary_calls == 0


@pytest.mark.parametrize("empty_response", [None, "", " \t\r\n"])
def test_empty_provider_response_keeps_ordered_fallback_semantics(
        tmp_path, empty_response):
    secondary_calls = 0

    def secondary(_request):
        nonlocal secondary_calls
        secondary_calls += 1
        return "case_opinion"

    runtime = LLMRuntime(LLMRuntimeConfig(
        cache_mode="off", cache_dir=tmp_path / "cache"))
    result = runtime.execute(
        _contract_request(),
        [
            _provider(lambda _request: empty_response),
            _provider(secondary, name="secondary", model="model-b"),
        ],
    )

    assert result.text == "case_opinion"
    assert result.output_contract_status == "accepted"
    assert result.fallback_path == ("primary", "secondary")
    assert secondary_calls == 1


def test_contract_receipt_marks_no_provider_response_as_not_evaluated(
        tmp_path):
    runtime = LLMRuntime(LLMRuntimeConfig(
        cache_mode="off", cache_dir=tmp_path / "cache"))

    result = runtime.execute(_contract_request(), [])

    assert result.error_category == "no_provider"
    assert result.output_contract_id == "test-enum-v1"
    assert result.output_contract_status == "not_evaluated"
    assert result.output_diagnostic_code is None
    assert result.output_fallback_id == "deterministic-fallback-v1"
    report = runtime.report_payload()
    assert report["counts"]["output_contract_not_evaluated"] == 1
    assert report["counts"]["output_contract_fallbacks"] == 1
    assert report["output_contracts"][0]["not_evaluated"] == 1


def test_budget_exhaustion_is_not_counted_as_a_deterministic_fallback(
        tmp_path):
    runtime = LLMRuntime(LLMRuntimeConfig(
        cache_mode="off", cache_dir=tmp_path / "cache",
        max_provider_calls=1))
    request = _contract_request(operation="test.contract_budget")
    provider = _provider(lambda _request: "case_opinion")
    assert runtime.execute(request, [provider]).succeeded

    with pytest.raises(LLMBudgetExceeded) as caught:
        runtime.execute(request, [provider])

    assert caught.value.args
    report = runtime.report_payload()
    assert report["counts"]["output_contract_not_evaluated"] == 1
    assert report["counts"]["output_contract_fallbacks"] == 0


def test_strict_output_contract_rejection_raises_structured_error(tmp_path):
    runtime = LLMRuntime(LLMRuntimeConfig(
        cache_mode="off", cache_dir=tmp_path / "cache",
        failure_policy="strict"))

    with pytest.raises(LLMExecutionError) as caught:
        runtime.execute(
            _contract_request(),
            [_provider(lambda _request: "case_opinion and footnote")],
        )

    result = caught.value.result
    assert result.error_category == "invalid_response"
    assert result.output_contract_status == "rejected"
    assert result.output_diagnostic_code == output_contracts.LABEL_MISMATCH
    assert result.output_fallback_id == "deterministic-fallback-v1"
    assert runtime.report_payload()["counts"][
        "output_contract_fallbacks"] == 0


def test_unexpected_validator_failure_has_fixed_content_free_receipts(tmp_path):
    events_path = tmp_path / "events.jsonl"
    response_canary = "MODEL_RESPONSE_CANARY"
    validator_canary = "VALIDATOR_EXCEPTION_CANARY"

    def broken_validator(_text):
        raise RuntimeError(validator_canary)

    broken_validator.contract_id = "test-enum-v1"

    runtime = LLMRuntime(LLMRuntimeConfig(
        cache_mode="off", cache_dir=tmp_path / "cache",
        events_path=events_path, failure_policy="strict"))
    request = _contract_request(validator=broken_validator)

    with pytest.raises(LLMExecutionError) as caught:
        runtime.execute(request, [_provider(lambda _request: response_canary)])

    result = caught.value.result
    assert result.output_diagnostic_code == (
        output_contracts.INTERNAL_CONTRACT_ERROR)
    assert result.text == ""
    event_text = events_path.read_text(encoding="utf-8")
    report_text = json.dumps(runtime.report_payload())
    combined = event_text + report_text + str(caught.value)
    assert response_canary not in combined
    assert validator_canary not in combined
    event = json.loads(event_text)
    assert event["output_contract_id"] == "test-enum-v1"
    assert event["output_contract_status"] == "rejected"
    assert event["output_diagnostic_code"] == (
        output_contracts.INTERNAL_CONTRACT_ERROR)


def test_output_contract_and_fallback_ids_bind_request_identity(tmp_path):
    runtime = LLMRuntime(LLMRuntimeConfig(
        cache_mode="off", cache_dir=tmp_path / "cache"))
    provider = _provider(lambda _request: "case_opinion")

    base_id = runtime.execute(_contract_request(), [provider]).request_id
    contract_id = runtime.execute(
        _contract_request(contract_id="test-enum-v2"), [provider]).request_id
    fallback_id = runtime.execute(
        _contract_request(fallback_id="other-fallback-v1"),
        [provider],
    ).request_id

    assert len({base_id, contract_id, fallback_id}) == 3


def test_validator_declared_contract_id_must_match_request(tmp_path):
    runtime = LLMRuntime(LLMRuntimeConfig(
        cache_mode="off", cache_dir=tmp_path / "cache"))
    validator = output_contracts.ExactEnumContract(
        contract_id="declared-v1", allowed_values=("case_opinion",),
        max_bytes=64)
    request = LLMRequest(
        prompt="prompt", output_contract_id="requested-v1",
        output_fallback_id="fallback-v1", output_validator=validator)

    with pytest.raises(ValueError, match="must match"):
        runtime.execute(request, [])


def test_shared_waiter_revalidates_output_with_its_contract(
        monkeypatch, tmp_path):
    runtime = LLMRuntime(LLMRuntimeConfig(
        cache_mode="off", cache_dir=tmp_path / "cache"))
    owner_validator = output_contracts.ExactEnumContract(
        contract_id="shared-v1",
        allowed_values=("case_opinion", "footnote"), max_bytes=64)
    waiter_validator = output_contracts.ExactEnumContract(
        contract_id="shared-v1", allowed_values=("footnote",), max_bytes=64)
    owner_request = _contract_request(
        contract_id="shared-v1", validator=owner_validator)
    waiter_request = _contract_request(
        contract_id="shared-v1", validator=waiter_validator)
    provider_started = threading.Event()
    waiter_joined = threading.Event()
    join_lock = threading.Lock()
    joins = 0
    original_join = runtime._join_flight

    def observed_join(key):
        nonlocal joins
        value = original_join(key)
        with join_lock:
            joins += 1
            if joins == 2:
                waiter_joined.set()
        return value

    def invoke(_request):
        provider_started.set()
        assert waiter_joined.wait(timeout=5)
        return "case_opinion"

    monkeypatch.setattr(runtime, "_join_flight", observed_join)
    provider = _provider(invoke)
    with ThreadPoolExecutor(max_workers=2) as pool:
        owner_future = pool.submit(runtime.execute, owner_request, [provider])
        assert provider_started.wait(timeout=5)
        waiter_future = pool.submit(runtime.execute, waiter_request, [provider])
        owner = owner_future.result(timeout=5)
        waiter = waiter_future.result(timeout=5)

    assert owner.text == "case_opinion"
    assert owner.output_contract_status == "accepted"
    assert waiter.text == ""
    assert waiter.cache_status == "shared"
    assert waiter.output_contract_status == "rejected"
    assert waiter.output_diagnostic_code == output_contracts.LABEL_MISMATCH


@pytest.mark.parametrize("candidate", [
    LLMRequest(
        prompt="prompt", output_fallback_id="fallback-v1"),
    LLMRequest(
        prompt="prompt", output_contract_id="contract-v1",
        output_fallback_id="fallback-v1"),
    LLMRequest(
        prompt="prompt", output_contract_id="../contract",
        output_fallback_id="fallback-v1", output_validator=lambda text: text),
    LLMRequest(
        prompt="prompt", output_contract_id="a" * 129,
        output_fallback_id="fallback-v1", output_validator=lambda text: text),
])
def test_incomplete_or_unsafe_output_contract_requests_fail_closed(
        tmp_path, candidate):
    runtime = LLMRuntime(LLMRuntimeConfig(
        cache_mode="off", cache_dir=tmp_path / "cache"))

    with pytest.raises((TypeError, ValueError)):
        runtime.execute(candidate, [])


def test_rejected_single_flight_results_preserve_content_free_contract_receipts(
        monkeypatch, tmp_path):
    runtime = LLMRuntime(LLMRuntimeConfig(
        cache_mode="off", cache_dir=tmp_path / "cache"))
    request = _contract_request(operation="test.contract_single_flight")
    task_barrier = threading.Barrier(4)
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
            if joins == 4:
                all_joined.set()
        return value

    def invoke(_request):
        nonlocal calls
        calls += 1
        assert all_joined.wait(timeout=5)
        return "invalid explanation containing case_opinion"

    monkeypatch.setattr(runtime, "_join_flight", observed_join)
    provider = _provider(invoke)

    def run_one():
        task_barrier.wait(timeout=5)
        return runtime.execute(request, [provider])

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _index: run_one(), range(4)))

    assert calls == 1
    assert {result.output_contract_status for result in results} == {
        "rejected"}
    assert {result.output_diagnostic_code for result in results} == {
        output_contracts.LABEL_MISMATCH}
    assert {result.text for result in results} == {""}
    assert sum(result.cache_status == "shared" for result in results) == 3
    counts = runtime.report_payload()["counts"]
    assert counts["output_contract_rejected"] == 4
    assert counts["output_contract_fallbacks"] == 4


def test_reconfigure_resets_output_contract_aggregates(tmp_path):
    runtime = LLMRuntime(LLMRuntimeConfig(
        cache_mode="off", cache_dir=tmp_path / "cache"))
    runtime.execute(
        _contract_request(), [_provider(lambda _request: "case_opinion")])
    assert runtime.report_payload()["output_contracts"]

    runtime.configure(LLMRuntimeConfig(
        cache_mode="off", cache_dir=tmp_path / "other-cache"))

    report = runtime.report_payload()
    assert report["counts"]["output_contract_accepted"] == 0
    assert report["counts"]["output_contract_rejected"] == 0
    assert report["counts"]["output_contract_not_evaluated"] == 0
    assert report["counts"]["output_contract_fallbacks"] == 0
    assert report["output_contracts"] == []


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
        return ' [ {"page":1,"title":"Chapter One","level":1} ] '

    monkeypatch.setattr(rag, "_call_llm", fake_call)
    entries = rag._llm_parse_toc("Chapter One 1", cloud_key="key")

    assert entries == [{
        "level": 1,
        "title": "Chapter One",
        "page": 1,
        "marker": "",
    }]
    assert len(observed) == 1
    assert observed[0][1]["operation"] == "toc.parse"
    assert observed[0][1]["prompt_version"] == "2"
    assert observed[0][1]["timeout"] == 60
    assert observed[0][1]["max_tokens"] == 4000
    assert observed[0][1]["output_contract_id"] == "toc-hierarchy-v1"
    assert observed[0][1]["output_fallback_id"] == (
        "use-deterministic-toc-parser")
    assert observed[0][1]["output_validator"] is (
        rag._TOC_HIERARCHY_OUTPUT_CONTRACT)


@pytest.mark.parametrize("parser_name", [
    "_llm_parse_scaffold",
    "_llm_parse_toc",
])
def test_toc_parser_does_not_swallow_budget_exhaustion(
        monkeypatch, parser_name):
    monkeypatch.setattr(
        rag, "_call_llm",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            LLMBudgetExceeded("limit")))

    with pytest.raises(LLMBudgetExceeded):
        getattr(rag, parser_name)("Chapter One 1")


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
