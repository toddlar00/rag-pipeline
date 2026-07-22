import subprocess
import sys

import llm_adapters
import rag
from llm_runtime import LLMRuntime, ProviderCallError, ProviderResponse


def test_llm_adapters_import_is_provider_sdk_lazy_and_rag_independent():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import llm_adapters; "
                "forbidden = {'rag', 'artifact_io', 'chunking_core', "
                "'index_state', 'retrieval_core', 'google', 'google.genai', "
                "'docling', 'torch', 'chromadb', 'qdrant_client'}; "
                "loaded = forbidden.intersection(sys.modules); "
                "assert not loaded, sorted(loaded)"
            ),
        ],
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr


def test_rag_reexports_standalone_adapter_helpers():
    assert rag._provider_hostname is llm_adapters._provider_hostname
    assert rag._provider_value is llm_adapters._provider_value
    assert rag._provider_error_category is (
        llm_adapters._provider_error_category)
    assert rag._AdaptiveThrottle is llm_adapters._AdaptiveThrottle
    assert rag._retry_after_seconds is llm_adapters._retry_after_seconds
    assert rag._llm_endpoint_id is llm_adapters._llm_endpoint_id


def test_endpoint_predicates_use_current_rag_hostname_helper(monkeypatch):
    observed = []

    def fake_hostname(url):
        observed.append(url)
        return "api.deepseek.com" if "deep" in url else "api.minimax.io"

    monkeypatch.setattr(rag, "_provider_hostname", fake_hostname)

    assert rag._is_deepseek_cloud("deep")
    assert rag._is_minimax_cloud("mini")
    assert observed == ["deep", "mini"]


def test_response_helpers_use_current_rag_collaborators(monkeypatch):
    values = []

    def fake_value(source, name):
        values.append((source, name))
        return 7

    monkeypatch.setattr(rag, "_provider_value", fake_value)
    assert rag._provider_token_count("response", "tokens") == 7
    assert values == [("response", "tokens")]

    monkeypatch.setattr(
        rag, "_provider_error_category", lambda _error: "timeout")
    error = rag._provider_call_error(
        RuntimeError("secret"), transport_attempts=2)
    assert error.category == "timeout"
    assert error.transport_attempts == 2
    assert "secret" not in str(error)


def test_ollama_result_facade_injects_current_rag_hooks(monkeypatch):
    observed = {}
    sentinel = ProviderResponse(text="result")

    def fake_core(prompt, **kwargs):
        observed["prompt"] = prompt
        observed.update(kwargs)
        return sentinel

    def fake_post(*args, **kwargs):
        raise AssertionError((args, kwargs))

    def fake_count(source, name):
        return None

    def fake_error(error, *, transport_attempts):
        return ProviderCallError(
            "provider_error", transport_attempts=transport_attempts)

    monkeypatch.setattr(
        llm_adapters, "_call_ollama_result", fake_core)
    monkeypatch.setattr(rag.requests, "post", fake_post)
    monkeypatch.setattr(rag, "_provider_token_count", fake_count)
    monkeypatch.setattr(rag, "_provider_call_error", fake_error)

    result = rag._call_ollama_result(
        "prompt", url="http://local", model="model",
        thinking=True, max_tokens=10, timeout=4)

    assert result is sentinel
    assert observed["post_fn"] is fake_post
    assert observed["provider_token_count_fn"] is fake_count
    assert observed["provider_call_error_fn"] is fake_error


def test_gemini_result_facade_injects_cache_and_response_hooks(monkeypatch):
    observed = {}
    sentinel = ProviderResponse(text="result")

    def fake_core(prompt, **kwargs):
        observed["prompt"] = prompt
        observed.update(kwargs)
        return sentinel

    def fake_loader(api_key):
        raise AssertionError(api_key)

    def fake_value(source, name):
        return None

    def fake_count(source, name):
        return None

    def fake_error(error, *, transport_attempts):
        return ProviderCallError(
            "provider_error", transport_attempts=transport_attempts)

    def fake_filtered(response):
        return False

    monkeypatch.setattr(
        llm_adapters, "_call_gemini_result", fake_core)
    monkeypatch.setattr(rag, "_load_gemini_client", fake_loader)
    monkeypatch.setattr(rag, "_provider_value", fake_value)
    monkeypatch.setattr(rag, "_provider_token_count", fake_count)
    monkeypatch.setattr(rag, "_provider_call_error", fake_error)
    monkeypatch.setattr(rag, "_gemini_content_filtered", fake_filtered)

    result = rag._call_gemini_result(
        "prompt", api_key="key", model="model",
        max_tokens=10, timeout=4)

    assert result is sentinel
    assert observed["client_loader_fn"] is fake_loader
    assert observed["provider_value_fn"] is fake_value
    assert observed["provider_token_count_fn"] is fake_count
    assert observed["provider_call_error_fn"] is fake_error
    assert observed["content_filtered_fn"] is fake_filtered


def test_openai_result_facade_injects_current_transport_hooks(monkeypatch):
    observed = {}
    sentinel = ProviderResponse(text="result")

    def fake_core(prompt, **kwargs):
        observed["prompt"] = prompt
        observed.update(kwargs)
        return sentinel

    hooks = {
        "post_fn": lambda *args, **kwargs: None,
        "get_throttle_fn": lambda workers: None,
        "sleep_fn": lambda seconds: None,
        "is_deepseek_fn": lambda url, model="": False,
        "provider_token_count_fn": lambda source, name: None,
        "provider_value_fn": lambda source, name: None,
        "provider_call_error_fn": lambda error, **kwargs: error,
        "retry_after_fn": lambda value: 0.0,
    }

    monkeypatch.setattr(
        llm_adapters, "_call_openai_compatible_result", fake_core)
    monkeypatch.setattr(rag.requests, "post", hooks["post_fn"])
    monkeypatch.setattr(rag, "_get_throttle", hooks["get_throttle_fn"])
    monkeypatch.setattr(rag.time, "sleep", hooks["sleep_fn"])
    monkeypatch.setattr(rag, "_is_deepseek_cloud", hooks["is_deepseek_fn"])
    monkeypatch.setattr(
        rag, "_provider_token_count", hooks["provider_token_count_fn"])
    monkeypatch.setattr(rag, "_provider_value", hooks["provider_value_fn"])
    monkeypatch.setattr(
        rag, "_provider_call_error", hooks["provider_call_error_fn"])
    monkeypatch.setattr(rag, "_retry_after_seconds", hooks["retry_after_fn"])

    result = rag._call_openai_compatible_result(
        "prompt", base_url="https://provider.test/v1",
        model="model", api_key="key")

    assert result is sentinel
    for name, hook in hooks.items():
        assert observed[name] is hook


def test_throttle_factory_keeps_mutable_state_and_sleep_hook_in_rag(
        monkeypatch):
    sleeps = []
    monkeypatch.setattr(rag, "_api_throttle", None)
    monkeypatch.setattr(rag.time, "sleep", sleeps.append)

    throttle = rag._get_throttle(2)
    throttle._cooldown = 0.25
    throttle.acquire()
    throttle.release_error()

    assert rag._api_throttle is throttle
    assert sleeps == [0.25]


def test_runtime_composition_still_resolves_current_rag_text_facade(
        monkeypatch):
    observed = {}

    def fake_cloud(prompt, **kwargs):
        observed["prompt"] = prompt
        observed.update(kwargs)
        return ProviderResponse(text="answer")

    monkeypatch.setattr(rag, "_llm_runtime", LLMRuntime())
    monkeypatch.setattr(rag, "_call_openai_compatible", fake_cloud)

    result = rag._call_llm_result(
        "prompt", cloud_url="https://provider.test/v1",
        cloud_model="model", cloud_key="key", ollama_url="")

    assert result.text == "answer"
    assert result.provider == "cloud"
    assert observed["_structured"] is True
