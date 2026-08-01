import sys
from types import SimpleNamespace

import pytest

import cli_policy
import rag


def _cloud_args(**overrides):
    values = {
        "cloud_url": rag.DEFAULT_CLOUD_URL,
        "cloud_model": rag.DEFAULT_CLOUD_MODEL,
        "cloud_key": "",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.parametrize(
    ("url", "model", "environment", "expected"),
    [
        (
            "https://api.deepseek.com",
            "deepseek-v4-pro",
            {
                "DEEPSEEK_API_KEY": "deepseek-key",
                "MINIMAX_API_KEY": "minimax-key",
                "CLOUD_API_KEY": "generic-key",
            },
            "deepseek-key",
        ),
        (
            "https://api.deepseek.com/v1",
            "deepseek-v4-flash",
            {
                "MINIMAX_API_KEY": "minimax-key",
                "CLOUD_API_KEY": "generic-key",
            },
            "generic-key",
        ),
        (
            "https://api.minimax.io/v1",
            "MiniMax-M2.7-highspeed",
            {
                "DEEPSEEK_API_KEY": "deepseek-key",
                "MINIMAX_API_KEY": "minimax-key",
                "CLOUD_API_KEY": "generic-key",
            },
            "minimax-key",
        ),
        (
            "https://gateway.example/v1",
            "custom-model",
            {
                "DEEPSEEK_API_KEY": "deepseek-key",
                "MINIMAX_API_KEY": "minimax-key",
                "CLOUD_API_KEY": "generic-key",
            },
            "generic-key",
        ),
        (
            "https://gateway.example/v1",
            "custom-model",
            {
                "DEEPSEEK_API_KEY": "deepseek-key",
                "MINIMAX_API_KEY": "minimax-key",
            },
            "",
        ),
    ],
)
def test_cloud_key_resolution_is_provider_aware(
        monkeypatch, url, model, environment, expected):
    for name in (
            "DEEPSEEK_API_KEY", "MINIMAX_API_KEY", "CLOUD_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)

    result = rag._resolve_cloud_key(
        _cloud_args(cloud_url=url, cloud_model=model),
        cloud_url=url,
        cloud_model=model,
    )

    assert result == expected


def test_explicit_cloud_key_has_highest_precedence(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "environment-key")
    args = _cloud_args(
        cloud_url=rag.DEFAULT_DEEPSEEK_URL,
        cloud_model=rag.DEFAULT_DEEPSEEK_MODEL,
        cloud_key="typed-key",
    )

    assert rag._resolve_cloud_key(args) == "typed-key"


def test_deepseek_key_is_not_sent_to_a_lookalike_hostname(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-secret")
    monkeypatch.setenv("CLOUD_API_KEY", "generic-key")
    url = "https://api.deepseek.com.attacker.invalid/v1"

    with pytest.raises(ValueError, match="confusingly similar"):
        rag._resolve_cloud_key(
            _cloud_args(cloud_url=url, cloud_model="deepseek-v4-pro"),
            cloud_url=url,
            cloud_model="deepseek-v4-pro",
        )
    assert rag._is_deepseek_cloud(url) is False


@pytest.mark.parametrize("url", [
    "http://api.deepseek.com",
    "http://api.minimax.io/v1",
    "https://api.deepseek.com:444/v1",
    "https://user:secret@api.deepseek.com/v1",
    "https://api.deepseek.com/v1?token=secret",
    "https://api.deepseek.com./v1",
    "https://localhost/v1",
    "https://127.1/v1",
    "https://0.0.0.0/v1",
])
def test_invalid_endpoint_is_rejected_before_any_environment_lookup(url):
    observed = []

    with pytest.raises((TypeError, ValueError)):
        cli_policy._resolve_cloud_key(
            SimpleNamespace(cloud_key=""),
            cloud_url=url,
            cloud_model="model",
            resolve_cloud_endpoint_fn=lambda _args: (url, "model"),
            environment_get_fn=lambda name, default: (
                observed.append(name) or default),
        )

    assert observed == []


def test_explicit_key_does_not_bypass_endpoint_validation():
    with pytest.raises((TypeError, ValueError)):
        cli_policy._resolve_cloud_key(
            SimpleNamespace(cloud_key="typed-secret"),
            cloud_url="http://api.deepseek.com",
            cloud_model="deepseek-v4-pro",
            resolve_cloud_endpoint_fn=lambda _args: ("", ""),
            environment_get_fn=lambda *_args: pytest.fail(
                "invalid endpoint must not read the environment"),
        )


def test_loopback_endpoint_requires_an_explicit_key():
    observed = []

    def resolver(_args):
        return "http://127.0.0.1:8000/v1", "local"

    assert cli_policy._resolve_cloud_key(
        SimpleNamespace(cloud_key=""),
        cloud_url="http://127.0.0.1:8000/v1",
        cloud_model="local",
        resolve_cloud_endpoint_fn=resolver,
        environment_get_fn=lambda name, default: (
            observed.append(name) or "ambient-secret"),
    ) == ""
    assert observed == []
    assert cli_policy._resolve_cloud_key(
        SimpleNamespace(cloud_key="typed-local-key"),
        cloud_url="http://127.0.0.1:8000/v1",
        cloud_model="local",
        resolve_cloud_endpoint_fn=resolver,
        environment_get_fn=lambda *_args: pytest.fail(
            "explicit loopback key must not read the environment"),
    ) == "typed-local-key"


@pytest.mark.parametrize("url", [
    "http://api.deepseek.com",
    "https://api.deepseek.com:444/v1",
    "https://user@api.deepseek.com/v1",
    "https://api.deepseek.com/v1?tenant=x",
    "https://api.deepseek.com./v1",
    "https://api.deepseek.com.attacker.invalid/v1",
])
def test_official_provider_predicates_require_the_full_safe_contract(url):
    assert rag._is_deepseek_cloud(url) is False


def test_cli_rejects_secret_bearing_endpoint_without_echoing_it(capsys):
    canary = "CLI_URL_SECRET_CANARY_61aa"
    unsafe = f"https://user:{canary}@gateway.example/v1?token={canary}"

    with pytest.raises(SystemExit):
        rag.main(["generate-questions", "--cloud-url", unsafe])

    assert canary not in capsys.readouterr().err


@pytest.mark.parametrize("option", [
    "--cloud-u", "--CLOUD-URL", "--llm-u", "--ollama-u", "--cloud-k",
])
def test_cli_rejects_sensitive_option_ambiguity_without_echoing_value(
        option, capsys):
    canary = "CLI_AMBIGUOUS_SECRET_CANARY_89bc"
    with pytest.raises(SystemExit):
        rag.main([
            "generate-questions", option, canary,
        ])

    error = capsys.readouterr().err
    assert "argument values were omitted" in error
    assert canary not in error


def test_strict_subparser_rejects_endpoint_abbreviation_without_preflight(
        monkeypatch, capsys):
    monkeypatch.setattr(
        cli_policy, "_has_ambiguous_sensitive_option", lambda _args: False)
    monkeypatch.setattr(
        rag, "generate_exam_questions",
        lambda *_args, **_kwargs: pytest.fail(
            "endpoint abbreviation reached command dispatch"),
    )

    with pytest.raises(SystemExit):
        rag.main([
            "generate-questions",
            "--cloud-u", "https://gateway.example/v1",
        ])

    assert "argument values were omitted" in capsys.readouterr().err


@pytest.mark.parametrize("argv", [
    ["--cloud-key", "{canary}", "generate-questions"],
    ["info", "--cloud-key", "{canary}"],
    ["info", "--cloud-url", "https://user:{canary}@gateway.example/v1"],
    ["generate-questions", "--cloud-urlx",
     "https://user:{canary}@gateway.example/v1?token={canary}"],
    ["generate-questions", "--cloud_url",
     "https://user:{canary}@gateway.example/v1"],
    ["generate-questions", "--cloud-keyx", "{canary}"],
    ["generate-questions", "--clod-key", "{canary}"],
    ["generate-questions", "--", "--cloud-url",
     "https://user:{canary}@gateway.example/v1"],
])
def test_parser_errors_never_echo_sensitive_values_from_any_scope(
        argv, capsys):
    canary = "PARSER_SCOPE_SECRET_CANARY_b531"
    populated = [value.format(canary=canary) for value in argv]

    with pytest.raises(SystemExit):
        rag.main(populated)

    error = capsys.readouterr().err
    assert "argument values were omitted" in error
    assert canary not in error


def test_non_llm_command_never_resolves_provider_credentials(monkeypatch):
    monkeypatch.setattr(
        rag, "_llm_kwargs_from_args",
        lambda *_args, **_kwargs: pytest.fail(
            "non-LLM command must not resolve provider credentials"),
    )
    monkeypatch.setattr(rag, "show_info", lambda *_args, **_kwargs: None)

    rag.main(["info"])


def test_deepseek_model_and_url_shortcuts_resolve_the_matching_pair():
    assert rag._resolve_cloud_endpoint(_cloud_args(
        cloud_model="deepseek-v4-flash",
    )) == (rag.DEFAULT_DEEPSEEK_URL, "deepseek-v4-flash")
    assert rag._resolve_cloud_endpoint(_cloud_args(
        cloud_url="https://api.deepseek.com/v1",
    )) == ("https://api.deepseek.com/v1", rag.DEFAULT_DEEPSEEK_MODEL)
    assert rag._resolve_cloud_endpoint(_cloud_args(
        cloud_url="HTTPS://API.MINIMAX.IO:443/v1/",
        cloud_model="deepseek-v4-flash",
    )) == (rag.DEFAULT_DEEPSEEK_URL, "deepseek-v4-flash")


def test_deepseek_model_shortcut_resolves_endpoint_and_environment_key(
        monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-environment-key")

    kwargs = rag._llm_kwargs_from_args(_cloud_args(
        cloud_model="deepseek-v4-pro",
    ))

    assert kwargs["cloud_url"] == rag.DEFAULT_DEEPSEEK_URL
    assert kwargs["cloud_model"] == "deepseek-v4-pro"
    assert kwargs["cloud_key"] == "deepseek-environment-key"


@pytest.mark.parametrize(
    ("key_flag", "url_flag", "model_flag"),
    [
        ("--cloud-key", "--cloud-url", "--cloud-model"),
        ("--api-key", "--llm-url", "--llm-model"),
    ],
)
@pytest.mark.parametrize(
    ("thinking_args", "expected_thinking"),
    [
        (["--thinking"], True),
        (["--no-thinking"], False),
        ([], False),
    ],
)
def test_cli_provider_aliases_and_thinking_reach_command(
        monkeypatch, key_flag, url_flag, model_flag,
        thinking_args, expected_thinking):
    observed = {}

    def fake_generate(*args, **kwargs):
        observed["args"] = args
        observed["kwargs"] = kwargs

    monkeypatch.setattr(rag, "generate_exam_questions", fake_generate)
    monkeypatch.setattr(
        sys,
        "argv",
            [
                "rag.py", "generate-questions",
                "--security-profile", "development",
                "--network-policy", "allow-cloud",
                key_flag, "typed-secret",
            url_flag, "https://api.deepseek.com",
            model_flag, "deepseek-v4-pro",
            "--llm-workers", "3",
            *thinking_args,
        ],
    )

    rag.main()

    assert observed["kwargs"]["cloud_key"] == "typed-secret"
    assert observed["kwargs"]["cloud_url"] == "https://api.deepseek.com"
    assert observed["kwargs"]["cloud_model"] == "deepseek-v4-pro"
    assert observed["kwargs"]["llm_workers"] == 3
    assert observed["kwargs"]["thinking"] is expected_thinking


@pytest.mark.parametrize("secret_flag", [
    "--api-key", "--cloud-key", "--gemini-key",
])
def test_cli_secret_redaction_does_not_mutate_command(secret_flag):
    command = ["generate-questions", secret_flag, "super-secret", "--thinking"]

    redacted = rag._redact_cli_secrets(command)

    assert redacted == [
        "generate-questions", secret_flag, "<redacted>", "--thinking"
    ]
    assert command[2] == "super-secret"


def test_interactive_deepseek_key_is_hidden_and_argv_is_restored(
        monkeypatch, capsys):
    choices = iter(["generate-questions", "deepseek-v4-pro"])
    observed = {}
    original_argv = ["rag.py", "menu"]

    monkeypatch.setattr(rag, "_menu_choose", lambda *args, **kwargs: next(choices))
    monkeypatch.setattr(rag, "_menu_file", lambda *args, **kwargs: "")
    monkeypatch.setattr(rag, "_menu_yesno", lambda *args, **kwargs: True)
    monkeypatch.setattr(rag, "getpass", lambda prompt: "menu-secret")
    monkeypatch.setattr(sys, "argv", original_argv.copy())
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ambient-secret")

    def fake_main(argv=None):
        observed["argv"] = sys.argv.copy()
        observed["deepseek_key"] = __import__("os").environ.get(
            "DEEPSEEK_API_KEY")

    monkeypatch.setattr(rag, "main", fake_main)

    rag.interactive_menu()

    output = capsys.readouterr().out
    assert "menu-secret" not in output
    assert "<redacted>" in output
    assert observed["argv"] == [
        "rag.py", "generate-questions",
        "--network-policy", "allow-cloud",
        "--llm-url", rag.DEFAULT_DEEPSEEK_URL,
        "--llm-model", "deepseek-v4-pro",
        "--thinking",
    ]
    assert observed["deepseek_key"] == "menu-secret"
    assert __import__("os").environ["DEEPSEEK_API_KEY"] == "ambient-secret"
    assert sys.argv == original_argv


@pytest.mark.parametrize(
    ("provider", "inputs", "expected"),
    [
        ("deepseek-v4-flash", [], ["--llm-model", "deepseek-v4-flash"]),
        ("minimax", [], []),
        ("gemini", [], ["--gemini-key", "menu-secret"]),
        (
            "custom",
            ["https://example.test/v1", "model-name", "tenant-a"],
            ["--llm-cache-namespace", "tenant-a"],
        ),
    ],
)
def test_interactive_cloud_llm_providers_serialize_explicit_consent(
        monkeypatch, provider, inputs, expected):
    values = iter(inputs)
    monkeypatch.setattr(rag, "_menu_choose", lambda *_args, **_kwargs: provider)
    monkeypatch.setattr(rag, "_menu_yesno", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(rag, "getpass", lambda _prompt: "menu-secret")
    monkeypatch.setattr("builtins.input", lambda _prompt: next(values))

    args = rag._menu_llm_provider_args()

    assert args is not None
    assert ["--network-policy", "allow-cloud"] == args[:2]
    for offset in range(0, len(expected), 2):
        flag, value = expected[offset:offset + 2]
        assert args[args.index(flag) + 1] == value


def test_interactive_cloud_embedding_serializes_explicit_consent(monkeypatch):
    choices = iter(["index", "chroma", rag.DEFAULT_EMBEDDING_MODEL_LEGAL])
    captured = {}
    monkeypatch.setattr(
        rag, "_menu_choose", lambda *_args, **_kwargs: next(choices))
    monkeypatch.setattr(rag, "_menu_file", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(rag, "_menu_yesno", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        rag, "_run_rag_entrypoint",
        lambda args, **_kwargs: captured.update(args=list(args)) or 0)

    rag.interactive_menu()

    assert captured["args"][-2:] == ["--network-policy", "allow-cloud"]


@pytest.mark.parametrize(
    ("args", "environment_name"),
    [
        (["query", "x", "--llm-url", rag.DEFAULT_DEEPSEEK_URL,
          "--api-key", "secret"], "DEEPSEEK_API_KEY"),
        (["query", "x", "--llm-url", rag.DEFAULT_CLOUD_URL,
          "--api-key", "secret"], "MINIMAX_API_KEY"),
        (["query", "x", "--llm-url", "https://example.test/v1",
          "--api-key", "secret"], "CLOUD_API_KEY"),
        (["query", "x", "--gemini-key", "secret"], "GEMINI_API_KEY"),
    ],
)
def test_menu_secrets_are_removed_from_process_arguments(
        args, environment_name):
    safe_args, environment = rag._menu_secrets_to_environment(args)

    assert "secret" not in safe_args
    assert environment == {environment_name: "secret"}
