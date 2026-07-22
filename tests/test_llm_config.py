import sys
from types import SimpleNamespace

import pytest

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

    result = rag._resolve_cloud_key(
        _cloud_args(cloud_url=url, cloud_model="deepseek-v4-pro"),
        cloud_url=url,
        cloud_model="deepseek-v4-pro",
    )

    assert result == "generic-key"
    assert rag._is_deepseek_cloud(url) is False


def test_deepseek_model_and_url_shortcuts_resolve_the_matching_pair():
    assert rag._resolve_cloud_endpoint(_cloud_args(
        cloud_model="deepseek-v4-flash",
    )) == (rag.DEFAULT_DEEPSEEK_URL, "deepseek-v4-flash")
    assert rag._resolve_cloud_endpoint(_cloud_args(
        cloud_url="https://api.deepseek.com/v1",
    )) == ("https://api.deepseek.com/v1", rag.DEFAULT_DEEPSEEK_MODEL)


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
        "--llm-url", rag.DEFAULT_DEEPSEEK_URL,
        "--llm-model", "deepseek-v4-pro",
        "--thinking",
    ]
    assert observed["deepseek_key"] == "menu-secret"
    assert __import__("os").environ["DEEPSEEK_API_KEY"] == "ambient-secret"
    assert sys.argv == original_argv


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
