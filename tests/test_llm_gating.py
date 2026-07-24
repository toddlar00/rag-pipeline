import inspect

import pytest

import rag


class _FakeResponse:
    status_code = 200
    headers = {}

    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_disabled_agent_team_never_calls_an_llm(monkeypatch):
    monkeypatch.setattr(
        rag, "_call_llm",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("LLM must remain disabled")))
    team = rag._AgentTeam("Deterministic task", enabled=False)

    assert team.director_plan("Run deterministic validation") == ""
    assert team.manager_decompose("") == ""
    assert team.qc_criteria("") == ""


def test_deterministic_scaffold_skips_layout_llm(monkeypatch):
    doc = {
        "texts": [],
        "tables": [{
            "prov": [{"page_no": 1}],
            "data": {"table_cells": [{"text": "Chapter 1 Introduction 1"}]},
        }],
    }
    sections = {"toc": {"start": 1, "end": 1}}
    monkeypatch.setattr(rag, "_calculate_page_delta", lambda value: 0)
    monkeypatch.setattr(
        rag, "_analyze_toc_layout",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("layout LLM must remain disabled")))
    monkeypatch.setattr(
        rag, "_llm_parse_scaffold",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("scaffold LLM must remain disabled")))
    monkeypatch.setattr(
        rag, "_parse_toc_tables",
        lambda *args, **kwargs: [{
            "level": 1,
            "title": "Chapter 1 Introduction",
            "page": 1,
        }],
    )

    scaffold = rag._build_scaffold(
        doc,
        sections,
        ollama_url=rag.DEFAULT_OLLAMA_URL,
        use_llm=False,
    )

    assert scaffold[0]["chapter_num"] == 1
    assert scaffold[0]["path"] == "Chapter 1 Introduction"


def test_llm_token_budget_is_forwarded_to_cloud_provider(monkeypatch):
    observed = {}

    def fake_cloud(prompt, **kwargs):
        observed.update(kwargs)
        return "response"

    monkeypatch.setattr(rag, "_call_openai_compatible", fake_cloud)

    response = rag._call_llm(
        "prompt",
        cloud_url="https://example.test/v1",
        cloud_model="model",
        cloud_key="key",
        ollama_url="",
        llm_workers=7,
        thinking=True,
        max_tokens=2048,
    )

    assert response == "response"
    assert observed["max_tokens"] == 2048
    assert observed["max_workers"] == 7
    assert observed["thinking"] is True


@pytest.mark.parametrize(
    ("thinking", "expected_thinking", "has_temperature"),
    [
        (True, {"type": "enabled"}, False),
        (False, {"type": "disabled"}, True),
    ],
)
def test_deepseek_v4_thinking_payload(
        monkeypatch, thinking, expected_thinking, has_temperature):
    observed = {}

    def fake_post(url, **kwargs):
        observed["url"] = url
        observed.update(kwargs)
        return _FakeResponse({
            "choices": [{
                "message": {
                    "reasoning_content": "private reasoning",
                    "content": "final answer",
                },
            }],
        })

    monkeypatch.setattr(rag.requests, "post", fake_post)
    monkeypatch.setattr(rag, "_api_throttle", None)

    result = rag._call_openai_compatible(
        "Explain Erie.",
        base_url=rag.DEFAULT_DEEPSEEK_URL,
        model="deepseek-v4-pro",
        api_key="sk-deepseek",
        thinking=thinking,
        max_tokens=777,
    )

    assert result == "final answer"
    assert observed["url"] == "https://api.deepseek.com/chat/completions"
    assert observed["headers"]["Authorization"] == "Bearer sk-deepseek"
    assert observed["json"]["model"] == "deepseek-v4-pro"
    assert observed["json"]["messages"] == [
        {"role": "user", "content": "Explain Erie."}
    ]
    assert observed["json"]["max_tokens"] == 777
    assert observed["json"]["thinking"] == expected_thinking
    assert ("temperature" in observed["json"]) is has_temperature


def test_deepseek_reasoning_content_is_never_used_as_the_answer(monkeypatch):
    monkeypatch.setattr(
        rag.requests,
        "post",
        lambda *args, **kwargs: _FakeResponse({
            "choices": [{
                "message": {
                    "reasoning_content": "do not expose this",
                    "content": "",
                },
            }],
        }),
    )
    monkeypatch.setattr(rag, "_api_throttle", None)

    result = rag._call_openai_compatible(
        "prompt",
        base_url=rag.DEFAULT_DEEPSEEK_URL,
        model="deepseek-v4-flash",
        api_key="key",
        thinking=True,
    )

    assert result == ""


def test_custom_openai_provider_does_not_receive_deepseek_fields(monkeypatch):
    observed = {}

    def fake_post(url, **kwargs):
        observed.update(kwargs)
        return _FakeResponse({
            "choices": [{"message": {"content": "answer"}}],
        })

    monkeypatch.setattr(rag.requests, "post", fake_post)
    monkeypatch.setattr(rag, "_api_throttle", None)

    assert rag._call_openai_compatible(
        "prompt",
        base_url="https://gateway.example/v1",
        model="deepseek-v4-pro",
        api_key="generic-key",
        thinking=True,
    ) == "answer"
    assert "thinking" not in observed["json"]
    assert observed["json"]["temperature"] == 0.0


def test_ollama_thinking_payload_returns_only_final_response(monkeypatch):
    observed = {}

    def fake_post(url, **kwargs):
        observed["url"] = url
        observed.update(kwargs)
        return _FakeResponse({
            "thinking": "private reasoning",
            "response": "final response",
        })

    monkeypatch.setattr(rag, "_post_loopback_without_environment", fake_post)

    result = rag._call_ollama(
        "prompt", url="http://127.0.0.1:11434", model="qwen3:30b",
        thinking=True, max_tokens=99,
    )

    assert result == "final response"
    assert observed["url"] == "http://127.0.0.1:11434/api/generate"
    assert observed["json"]["think"] is True
    assert observed["allow_redirects"] is False
    assert observed["json"]["options"]["num_predict"] == 99


def test_llm_classify_accepts_and_forwards_worker_count(monkeypatch):
    observed = {}

    def fake_call(prompt, **kwargs):
        observed.update(kwargs)
        return "case_opinion"

    monkeypatch.setattr(rag, "_call_llm", fake_call)

    result = rag._llm_classify(
        "Opinion text", ["Chapter 1"], llm_workers=7, thinking=True)

    assert result == "case_opinion"
    assert observed["llm_workers"] == 7
    assert observed["thinking"] is True


def test_generate_context_accepts_and_forwards_worker_count(monkeypatch):
    observed = {}

    def fake_call(prompt, **kwargs):
        observed.update(kwargs)
        return "First sentence. Second sentence. Third sentence."

    monkeypatch.setattr(rag, "_call_llm", fake_call)

    result = rag._generate_context(
        "Chunk text", ["Chapter 1"], llm_workers=6, thinking=True)

    assert result == "First sentence. Second sentence."
    assert observed["llm_workers"] == 6
    assert observed["thinking"] is True


def test_chunk_worker_default_matches_global_default():
    parameter = inspect.signature(rag.chunk_document).parameters["llm_workers"]

    assert parameter.default == rag.DEFAULT_LLM_WORKERS


def test_adaptive_throttle_respects_single_worker_floor():
    throttle = rag._AdaptiveThrottle(max_workers=2)
    throttle.acquire()

    throttle.release_429()

    assert throttle.current_workers == 1


def test_adaptive_throttle_reduces_limit_while_other_calls_are_active():
    throttle = rag._AdaptiveThrottle(max_workers=4)
    for _ in range(4):
        throttle.acquire()

    throttle.release_429()

    assert throttle.current_workers == 2
    for _ in range(3):
        throttle.release_error()
