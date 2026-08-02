import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import cli_policy
import rag


def _provider_defaults() -> cli_policy.ProviderCliDefaults:
    return cli_policy.ProviderCliDefaults(
        cloud_url="https://default.test/v1",
        cloud_model="default-model",
        deepseek_url="https://api.deepseek.com/v1",
        deepseek_model="deepseek-default",
        ollama_url="http://127.0.0.1:11434",
        ollama_model="local-model",
        llm_workers=4,
    )


def _resume_defaults() -> cli_policy.ResumeCommandDefaults:
    return cli_policy.ResumeCommandDefaults(
        executable="python-test",
        script_name="rag.py",
        embedding_model="embedding-default",
        structure_profile="us-law-casebook-v1",
        db_backend="chroma",
        conversion_backend="pypdfium2",
        max_tokens=4096,
        min_words=20,
        dedup_threshold=0.9,
        db_lock_timeout=30.0,
        full_operation_timeout=14400.0,
        llm_cache_mode="readwrite",
        llm_fallback="ordered",
        llm_failure_policy="best-effort",
        provider=_provider_defaults(),
    )


def test_cli_policy_is_a_standard_library_only_leaf_module():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import cli_policy; "
                "forbidden = {'rag', 'llm_adapters', 'llm_runtime', "
                "'requests', 'google', 'google.genai', 'docling', 'torch', "
                "'chromadb', 'qdrant_client'}; "
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


def test_rag_reexports_context_free_cli_policy_helpers():
    assert rag._rag_cli_command is cli_policy._rag_cli_command
    assert rag._menu_args_use_llm is cli_policy._menu_args_use_llm
    assert rag._redact_cli_secrets is cli_policy._redact_cli_secrets
    assert rag._namespace_uses_llm is cli_policy._namespace_uses_llm


def test_namespace_llm_detection_is_operation_and_feature_specific():
    assert not cli_policy._namespace_uses_llm(
        SimpleNamespace(command="info"))
    assert not cli_policy._namespace_uses_llm(
        SimpleNamespace(command="query", answer=False))
    assert cli_policy._namespace_uses_llm(
        SimpleNamespace(command="query", answer=True))
    assert not cli_policy._namespace_uses_llm(
        SimpleNamespace(command="chunk", llm_classify=False))
    assert cli_policy._namespace_uses_llm(
        SimpleNamespace(command="chunk", contextualize=True))
    assert cli_policy._namespace_uses_llm(
        SimpleNamespace(command="export", format="flashcards"))
    assert cli_policy._namespace_uses_llm(
        SimpleNamespace(command="packets"))
    assert cli_policy._pipeline_features_use_llm(
        SimpleNamespace(raptor=True))
    assert not cli_policy._pipeline_features_use_llm(SimpleNamespace())


def test_leaf_provider_options_use_injected_resolvers():
    observed = {}
    args = SimpleNamespace(
        ollama_url="https://custom-ollama.test",
        ollama_model="custom-local",
        gemini_key="gemini-secret",
        thinking=True,
        llm_workers=7,
    )

    def resolve_endpoint(namespace):
        observed["endpoint_args"] = namespace
        return "https://provider.test/v1", "provider-model"

    def resolve_key(namespace, *, cloud_url, cloud_model):
        observed["key_args"] = (namespace, cloud_url, cloud_model)
        return "provider-secret"

    options = cli_policy._llm_kwargs_from_args(
        args,
        include_workers=True,
        defaults=_provider_defaults(),
        resolve_cloud_endpoint_fn=resolve_endpoint,
        resolve_cloud_key_fn=resolve_key,
    )

    assert options == {
        "cloud_url": "https://provider.test/v1",
        "cloud_model": "provider-model",
        "cloud_key": "provider-secret",
        "ollama_url": "https://custom-ollama.test",
        "ollama_model": "custom-local",
        "gemini_key": "gemini-secret",
        "thinking": True,
        "llm_workers": 7,
    }
    assert observed["endpoint_args"] is args
    assert observed["key_args"] == (
        args, "https://provider.test/v1", "provider-model")


def test_inactive_provider_options_preserve_shape_without_credentials():
    args = SimpleNamespace(
        ollama_url="http://127.0.0.1:11434",
        ollama_model="local-model",
        gemini_key="explicit-gemini-secret",
        thinking=False,
        llm_workers=3,
    )

    options = cli_policy._llm_kwargs_from_args(
        args,
        include_workers=True,
        defaults=_provider_defaults(),
        resolve_cloud_endpoint_fn=lambda _args: (
            "https://provider.test/v1", "provider-model"),
        resolve_cloud_key_fn=lambda *_args, **_kwargs: pytest.fail(
            "inactive configuration must not resolve a cloud credential"),
        resolve_credentials=False,
    )

    assert options == {
        "cloud_url": "https://provider.test/v1",
        "cloud_model": "provider-model",
        "cloud_key": "",
        "ollama_url": "http://127.0.0.1:11434",
        "ollama_model": "local-model",
        "gemini_key": "",
        "thinking": False,
        "llm_workers": 3,
    }


@pytest.mark.parametrize("option", [
    "--cloud-u", "--CLOUD-URL", "--llm-u", "--ollama-u",
    "--cloud-k", "--API-KEY", "--gemini-k", "--cloud-urlx",
    "--cloud_url", "--cloud-keyx",
])
def test_sensitive_option_ambiguity_is_detected_without_values(option):
    assert cli_policy._has_ambiguous_sensitive_option(
        ["generate-questions", option, "private-value"])


def test_exact_sensitive_options_are_not_ambiguous_but_terminator_is_no_bypass():
    assert not cli_policy._has_ambiguous_sensitive_option([
        "generate-questions",
        "--cloud-url", "https://gateway.example/v1",
        "--api-key=secret",
    ])
    assert cli_policy._has_ambiguous_sensitive_option([
        "query", "--", "--cloud-u", "ordinary positional text",
    ])


def test_provider_facades_inject_current_defaults_and_callbacks(
        monkeypatch):
    args = SimpleNamespace()
    endpoint_observed = {}

    def endpoint_validator(*_args, **_kwargs):
        return None

    def endpoint_core(namespace, **kwargs):
        endpoint_observed.update(namespace=namespace, **kwargs)
        return "dynamic-url", "dynamic-model"

    monkeypatch.setattr(rag, "DEFAULT_CLOUD_URL", "dynamic-default-url")
    monkeypatch.setattr(rag, "DEFAULT_LLM_WORKERS", 17)
    monkeypatch.setattr(rag, "_validate_cloud_endpoint", endpoint_validator)
    monkeypatch.setattr(cli_policy, "_resolve_cloud_endpoint", endpoint_core)

    assert rag._resolve_cloud_endpoint(args) == (
        "dynamic-url", "dynamic-model")
    assert endpoint_observed["namespace"] is args
    assert endpoint_observed["defaults"].cloud_url == "dynamic-default-url"
    assert endpoint_observed["defaults"].llm_workers == 17
    assert endpoint_observed["validate_cloud_endpoint_fn"] is (
        endpoint_validator)

    key_observed = {}

    def endpoint_resolver(_args):
        return "current-url", "current-model"

    def key_endpoint_validator(*_args, **_kwargs):
        return None

    def key_core(namespace, **kwargs):
        key_observed.update(namespace=namespace, **kwargs)
        return kwargs["environment_get_fn"]("CLI_POLICY_TEST_KEY", "")

    monkeypatch.setenv("CLI_POLICY_TEST_KEY", "environment-secret")
    monkeypatch.setattr(rag, "_resolve_cloud_endpoint", endpoint_resolver)
    monkeypatch.setattr(
        rag, "_validate_cloud_endpoint", key_endpoint_validator)
    monkeypatch.setattr(cli_policy, "_resolve_cloud_key", key_core)

    assert rag._resolve_cloud_key(
        args, cloud_url="explicit-url", cloud_model="explicit-model"
    ) == "environment-secret"
    assert key_observed["namespace"] is args
    assert key_observed["cloud_url"] == "explicit-url"
    assert key_observed["cloud_model"] == "explicit-model"
    assert key_observed["resolve_cloud_endpoint_fn"] is endpoint_resolver
    assert key_observed["validate_cloud_endpoint_fn"] is (
        key_endpoint_validator)


def test_llm_kwargs_facade_injects_current_resolvers_and_defaults(
        monkeypatch):
    observed = {}

    def endpoint_resolver(_args):
        return "current-url", "current-model"

    def key_resolver(_args, **_kwargs):
        return "current-key"

    def kwargs_core(namespace, **kwargs):
        observed.update(namespace=namespace, **kwargs)
        return {"sentinel": True}

    monkeypatch.setattr(rag, "DEFAULT_OLLAMA_MODEL", "dynamic-local")
    monkeypatch.setattr(rag, "_resolve_cloud_endpoint", endpoint_resolver)
    monkeypatch.setattr(rag, "_resolve_cloud_key", key_resolver)
    monkeypatch.setattr(cli_policy, "_llm_kwargs_from_args", kwargs_core)

    assert rag._llm_kwargs_from_args(
        SimpleNamespace(), include_workers=True
    ) == {"sentinel": True}
    assert observed["include_workers"] is True
    assert observed["defaults"].ollama_model == "dynamic-local"
    assert observed["resolve_cloud_endpoint_fn"] is endpoint_resolver
    assert observed["resolve_cloud_key_fn"] is key_resolver
    assert observed["resolve_credentials"] is True


def test_runtime_policy_mapping_and_facade_mutation_stay_separate(
        monkeypatch):
    args = SimpleNamespace(
        llm_cache_mode="write",
        llm_cache_dir=None,
        llm_events="events.jsonl",
        llm_report="report.json",
        run_id="run-123",
        max_llm_calls=8,
        max_llm_transport_attempts=12,
        max_llm_reserved_tokens=900,
        llm_fallback="none",
        llm_failure_policy="strict",
    )
    values = cli_policy._llm_runtime_config_values_from_args(
        args, default_cache_dir=Path("dynamic-cache"))

    assert values == {
        "cache_mode": "write",
        "cache_dir": Path("dynamic-cache"),
        "events_path": "events.jsonl",
        "report_path": "report.json",
        "run_id": "run-123",
        "max_provider_calls": 8,
        "max_transport_attempts": 12,
        "max_reserved_tokens": 900,
        "fallback_policy": "none",
        "failure_policy": "strict",
    }

    constructed = []

    class FakeConfig:
        def __init__(self, **kwargs):
            self.values = kwargs
            self.cache_dir = Path("facade-default-cache")
            constructed.append(self)

    class FakeRuntime:
        configured = None

        def configure(self, config):
            self.configured = config

    runtime = FakeRuntime()
    monkeypatch.setattr(rag, "LLMRuntimeConfig", FakeConfig)
    monkeypatch.setattr(rag, "_llm_runtime", runtime)

    rag._configure_llm_runtime_from_args(SimpleNamespace())

    assert len(constructed) == 2
    assert runtime.configured is constructed[1]
    assert runtime.configured.values == {
        "cache_mode": "off",
        "cache_dir": Path("facade-default-cache"),
        "events_path": None,
        "report_path": None,
        "run_id": None,
        "max_provider_calls": None,
        "max_transport_attempts": None,
        "max_reserved_tokens": None,
        "fallback_policy": "ordered",
        "failure_policy": "best-effort",
    }


def test_leaf_timeout_policy_preserves_scan_and_fallback_rules():
    defaults = {"query": 300.0, "evaluation": 600.0}

    assert cli_policy._cli_operation_timeout(
        ["query", "terms", "--operation-timeout", "9",
         "--operation-timeout=3"],
        "query",
        operation_timeouts=defaults,
        normalize_timeout_fn=float,
    ) == 3
    assert cli_policy._cli_operation_timeout(
        ["query", "terms", "--", "--operation-timeout=1"],
        "query",
        operation_timeouts=defaults,
        normalize_timeout_fn=float,
    ) == 300.0
    assert cli_policy._cli_operation_timeout(
        ["--operation-timeout", "4"],
        "evaluation",
        operation_timeouts=defaults,
        normalize_timeout_fn=float,
    ) == 4

    def reject(_value):
        raise ValueError("invalid")

    assert cli_policy._cli_operation_timeout(
        ["query", "terms", "--operation-timeout", "invalid"],
        "query",
        operation_timeouts=defaults,
        normalize_timeout_fn=reject,
    ) == 300.0


def test_timeout_facades_inject_current_platform_and_parser_policy(
        monkeypatch):
    observed = {}

    def normalize_core(value, *, timeout_max):
        observed["normalized"] = (value, timeout_max)
        return 12.5

    monkeypatch.setattr(rag._threading, "TIMEOUT_MAX", 123.5)
    monkeypatch.setattr(
        cli_policy, "_normalize_operation_timeout", normalize_core)

    assert rag._normalize_operation_timeout("12.5") == 12.5
    assert observed["normalized"] == ("12.5", 123.5)

    operation_timeouts = {"query": 77.0}

    def normalizer(value):
        return float(value)

    def timeout_core(argv, operation, **kwargs):
        observed["timeout"] = (argv, operation, kwargs)
        return 22.0

    monkeypatch.setattr(rag, "DEFAULT_OPERATION_TIMEOUTS", operation_timeouts)
    monkeypatch.setattr(rag, "_normalize_operation_timeout", normalizer)
    monkeypatch.setattr(cli_policy, "_cli_operation_timeout", timeout_core)

    argv = ["query", "terms"]
    assert rag._cli_operation_timeout(argv, "query") == 22.0
    assert observed["timeout"] == (
        argv,
        "query",
        {
            "operation_timeouts": operation_timeouts,
            "normalize_timeout_fn": normalizer,
        },
    )


def test_resume_serializer_preserves_quoting_secrets_and_raw_extra_flags():
    command = cli_policy._build_resume_cmd(
        Path("My Book.pdf"),
        SimpleNamespace(
            collection="My Collection",
            markdown_validation="strict",
            cloud_key="must-not-serialize",
            gemini_key="also-secret",
        ),
        "--force --custom-value raw",
        defaults=_resume_defaults(),
    )

    assert command.startswith(
        'python-test rag.py full --pdf "My Book.pdf" --resume')
    assert "--structure-profile us-law-casebook-v1" in command
    assert "--collection My Collection" in command
    assert "--markdown-validation strict" in command
    assert command.endswith("--force --custom-value raw")
    assert "must-not-serialize" not in command
    assert "also-secret" not in command


def test_resume_facade_injects_current_executable_and_defaults(monkeypatch):
    observed = {}

    def resume_core(pdf, args, extra_flags, *, defaults):
        observed.update(
            pdf=pdf, args=args, extra_flags=extra_flags, defaults=defaults)
        return "resume-command"

    monkeypatch.setattr(rag.sys, "executable", "dynamic-python")
    monkeypatch.setattr(rag, "DEFAULT_MAX_TOKENS", 1234)
    monkeypatch.setattr(
        rag, "DEFAULT_OPERATION_TIMEOUTS", {"full": 4321.0})
    monkeypatch.setattr(rag, "DEFAULT_CLOUD_MODEL", "dynamic-cloud-model")
    monkeypatch.setattr(cli_policy, "_build_resume_cmd", resume_core)
    args = SimpleNamespace()
    pdf = Path("Book.pdf")

    assert rag._build_resume_cmd(pdf, args, "--force") == "resume-command"
    assert observed["pdf"] == pdf
    assert observed["args"] is args
    assert observed["extra_flags"] == "--force"
    assert observed["defaults"].executable == "dynamic-python"
    assert observed["defaults"].max_tokens == 1234
    assert observed["defaults"].full_operation_timeout == 4321.0
    assert observed["defaults"].provider.cloud_model == (
        "dynamic-cloud-model")


def test_leaf_secret_policy_removes_and_redacts_separate_and_inline_values():
    args = [
        "query",
        "terms",
        "--llm-url",
        "https://deepseek.test/v1",
        "--cloud-url",
        "https://minimax.test/v1",
        "--api-key",
        "provider-secret",
        "--api-key=inline-secret",
        "--gemini-key",
    ]

    safe_args, environment = cli_policy._menu_secrets_to_environment(
        args,
        default_cloud_url="https://default.test/v1",
        is_deepseek_cloud_fn=lambda url: "deepseek" in url,
        is_minimax_cloud_fn=lambda url: "minimax" in url,
    )

    assert safe_args == [
        "query",
        "terms",
        "--llm-url",
        "https://deepseek.test/v1",
        "--cloud-url",
        "https://minimax.test/v1",
        "--gemini-key",
    ]
    assert environment == {"MINIMAX_API_KEY": "inline-secret"}
    assert cli_policy._redact_cli_secrets(args) == [
        "query",
        "terms",
        "--llm-url",
        "https://deepseek.test/v1",
        "--cloud-url",
        "https://minimax.test/v1",
        "--api-key",
        "<redacted>",
        "--api-key=<redacted>",
        "--gemini-key",
    ]


def test_menu_secret_facade_injects_current_endpoint_policy(monkeypatch):
    observed = {}

    def deepseek_predicate(url):
        return url == "dynamic-deepseek"

    def minimax_predicate(url):
        return url == "dynamic-minimax"

    def secret_core(args, **kwargs):
        observed.update(args=args, **kwargs)
        return ["safe"], {"KEY": "secret"}

    monkeypatch.setattr(rag, "DEFAULT_CLOUD_URL", "dynamic-default")
    monkeypatch.setattr(rag, "_is_deepseek_cloud", deepseek_predicate)
    monkeypatch.setattr(rag, "_is_minimax_cloud", minimax_predicate)
    monkeypatch.setattr(
        cli_policy, "_menu_secrets_to_environment", secret_core)
    args = ["query", "terms"]

    assert rag._menu_secrets_to_environment(args) == (
        ["safe"], {"KEY": "secret"})
    assert observed["args"] is args
    assert observed["default_cloud_url"] == "dynamic-default"
    assert observed["is_deepseek_cloud_fn"] is deepseek_predicate
    assert observed["is_minimax_cloud_fn"] is minimax_predicate


def test_run_telemetry_options_use_last_values_before_terminator():
    assert cli_policy._cli_run_telemetry_options([
        "full", "--pdf", "book.pdf", "--run-id", "first",
        "--run-id=second", "--run-events", "events.jsonl",
        "--run-report=report.json", "--", "--run-id=ignored",
    ], "full") == {
        "run_id": "second",
        "events_path": "events.jsonl",
        "report_path": "report.json",
    }


def test_run_telemetry_options_are_empty_without_the_command():
    assert cli_policy._cli_run_telemetry_options(
        ["--run-id", "wrong-scope"], "full") == {
            "run_id": None, "events_path": None, "report_path": None,
        }


def test_run_telemetry_options_do_not_consume_another_flag_as_a_value():
    assert cli_policy._cli_run_telemetry_options([
        "index", "--run-events", "--run-report", "report.json",
        "--run-id", "--quiet",
    ], "index") == {
        "run_id": None,
        "events_path": None,
        "report_path": "report.json",
    }
