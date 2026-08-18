import builtins
import json
import sys
import threading
from types import ModuleType, SimpleNamespace

import pytest

from llm_runtime import ProviderResponse
import rag
import release_security


@pytest.mark.parametrize("model_name", [
    "voyage-law-2",
    "cohere-embed-v4",
    "embed-english-v3.0",
    "text-embedding-3-large",
    "embo-01",
    "minimax-embedding-01",
])
def test_every_cloud_embedding_family_is_blocked_before_provider_import(
        monkeypatch, model_name):
    real_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name.split(".", 1)[0] in {
                "voyageai", "cohere", "openai"}:
            pytest.fail("blocked embedding imported a provider SDK")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    with pytest.raises(
            release_security.ReleaseSecurityError,
            match="network-policy allow-cloud"):
        rag._get_embedding_fn(model_name)


def test_embedding_cache_cannot_reuse_prior_cloud_authority(monkeypatch):
    calls = []
    monkeypatch.setattr(rag, "_embed_fn_cache", {})

    def factory(_model_name, *, input_type, security_policy):
        def embed(texts):
            calls.append((input_type, security_policy, list(texts)))
            return [[1.0] for _ in texts]
        return embed

    monkeypatch.setattr(rag, "_get_embedding_fn", factory)
    allowed = release_security.ReleaseSecurityPolicy(
        profile="development", network_policy="allow-cloud")

    assert rag._embed_texts(
        ["first private text"], "voyage-law-2",
        security_policy=allowed) == [[1.0]]
    with pytest.raises(
            release_security.ReleaseSecurityError,
            match="network-policy allow-cloud"):
        rag._embed_texts(
            ["second private text"], "voyage-law-2",
            security_policy=release_security.ReleaseSecurityPolicy())

    assert [call[2] for call in calls] == [["first private text"]]


def test_embedding_cache_keys_the_complete_transport_policy(monkeypatch):
    constructed = []
    monkeypatch.setattr(rag, "_embed_fn_cache", {})

    def factory(_model_name, *, input_type, security_policy):
        constructed.append((input_type, security_policy))
        return lambda texts: [[1.0] for _ in texts]

    monkeypatch.setattr(rag, "_get_embedding_fn", factory)
    untrusted = release_security.ReleaseSecurityPolicy(
        profile="development", network_policy="allow-cloud")
    trusted = release_security.ReleaseSecurityPolicy(
        profile="development", network_policy="allow-cloud",
        trust_environment_network=True)

    rag._embed_texts(["one"], "voyage-law-2", security_policy=untrusted)
    rag._embed_texts(["two"], "voyage-law-2", security_policy=trusted)

    assert constructed == [("document", untrusted), ("document", trusted)]


def test_retained_embedding_closure_rechecks_ambient_network(monkeypatch):
    for name in release_security._NETWORK_OVERRIDE_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VOYAGE_API_KEY", "secret")
    policy = release_security.ReleaseSecurityPolicy(
        network_policy="allow-cloud")
    embedding = rag._get_embedding_fn(
        "voyage-law-2", security_policy=policy)
    monkeypatch.setenv("HTTPS_PROXY", "https://proxy.invalid")
    monkeypatch.setattr(
        rag, "_post_cloud_with_policy",
        lambda *_args, **_kwargs: pytest.fail(
            "changed environment reached cloud transport"))

    with pytest.raises(
            release_security.ReleaseSecurityError, match="HTTPS_PROXY"):
        embedding(["private text"])


def test_reranker_cache_cannot_reuse_development_model_in_release(
        monkeypatch):
    constructed = []

    class FlagReranker:
        def __init__(self, source, **kwargs):
            constructed.append((source, kwargs))

    module = ModuleType("FlagEmbedding")
    module.FlagReranker = FlagReranker
    monkeypatch.setitem(sys.modules, "FlagEmbedding", module)
    monkeypatch.setattr(rag, "_reranker_instances", {})

    def model_source(_model_name, _consumer, *, security_policy, **_kwargs):
        if security_policy.profile == "release":
            raise release_security.ReleaseSecurityError(
                "release rejected unreviewed model")
        return "unreviewed-model", False

    monkeypatch.setattr(rag, "_model_loader_source", model_source)
    development = release_security.ReleaseSecurityPolicy(
        profile="development",
        model_download_policy="allow-reviewed-sync")

    rag._get_reranker("owner/unreviewed", security_policy=development)
    with pytest.raises(
            release_security.ReleaseSecurityError,
            match="release rejected"):
        rag._get_reranker(
            "owner/unreviewed",
            security_policy=release_security.ReleaseSecurityPolicy())

    assert constructed == [(
        "unreviewed-model",
        {"use_fp16": True, "trust_remote_code": True},
    )]


@pytest.mark.parametrize("model_name", [
    "cohere-rerank-v3.5",
    "jina-reranker-v2-base-multilingual",
])
def test_cloud_rerankers_are_blocked_before_sdk_key_or_transport(
        monkeypatch, model_name):
    monkeypatch.setattr(
        rag.requests, "post",
        lambda *_args, **_kwargs: pytest.fail(
            "blocked reranker reached transport"))
    monkeypatch.delenv("COHERE_API_KEY", raising=False)
    monkeypatch.delenv("JINA_API_KEY", raising=False)

    with pytest.raises(
            release_security.ReleaseSecurityError,
            match="network-policy allow-cloud"):
        rag._rerank(
            "private query", ["private document"], [{}], [0.1], 1,
            reranker_model=model_name)


def test_llm_cloud_policy_fails_before_runtime_cache_or_dispatch(monkeypatch):
    monkeypatch.setattr(
        rag._llm_runtime, "execute",
        lambda *_args, **_kwargs: pytest.fail(
            "blocked LLM request reached runtime cache/dispatch"))

    with pytest.raises(
            release_security.ReleaseSecurityError,
            match="network-policy allow-cloud"):
        rag._call_llm_result(
            "private prompt",
            cloud_url="https://api.deepseek.com/v1",
            cloud_model="model",
            cloud_key="secret",
            ollama_url="",
        )


def test_ollama_allows_literal_loopback_but_blocks_public_hosts(monkeypatch):
    observed = []
    monkeypatch.setattr(
        rag._llm_adapters, "_call_ollama_result",
        lambda *args, **kwargs: (
            observed.append((args, kwargs))
            or ProviderResponse(text="local")))

    assert rag._call_ollama_result(
        "private prompt", url="http://127.0.0.1:11434").text == "local"
    assert len(observed) == 1
    with pytest.raises(release_security.ReleaseSecurityError):
        rag._call_ollama_result(
            "private prompt", url="https://ollama.example.test")
    assert len(observed) == 1


def test_api_token_counting_never_imports_provider_tokenizers(monkeypatch):
    real_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name.split(".", 1)[0] in {"voyageai", "tiktoken"}:
            pytest.fail("API token counting attempted a hidden download path")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    counts, exact = rag._count_embedding_text_tokens(
        ["private legal text"], "voyage-law-2")

    assert counts[0] > 0
    assert exact is False


def test_chroma_settings_explicitly_disable_anonymized_telemetry():
    observed = {}

    class Settings:
        def __init__(self, **kwargs):
            observed.update(kwargs)
            self.anonymized_telemetry = kwargs["anonymized_telemetry"]

    module = ModuleType("chromadb")
    module.Settings = Settings

    kwargs = rag._chroma_settings_kwargs(module)

    assert observed == {"anonymized_telemetry": False}
    assert kwargs["settings"].anonymized_telemetry is False


def test_release_cli_rejects_inline_secret_before_operation(monkeypatch, capsys):
    monkeypatch.setattr(
        rag, "generate_exam_questions",
        lambda *_args, **_kwargs: pytest.fail(
            "release inline secret reached the operation"))

    with pytest.raises(SystemExit):
        rag.main([
            "generate-questions",
            "--cloud-key", "SECRET_CANARY_VALUE",
        ])

    assert "SECRET_CANARY_VALUE" not in capsys.readouterr().err


class _CloudResponse:
    def __init__(self, payload, *, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self._body = json.dumps(payload).encode("utf-8")
        self.headers = {
            "Content-Type": "application/json",
            "Content-Length": str(len(self._body)),
        }

    def raise_for_status(self):
        return None

    def json(self):
        pytest.fail("provider path must not eagerly call response.json()")

    def iter_content(self, *, chunk_size):
        for offset in range(0, len(self._body), chunk_size):
            yield self._body[offset:offset + chunk_size]

    def close(self):
        return None


def _capture_cloud_sessions(monkeypatch, response):
    sessions = []

    class Session:
        def __init__(self):
            self.trust_env = None
            self.posts = []
            sessions.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def post(self, url, **kwargs):
            self.posts.append((url, kwargs))
            return response

    monkeypatch.setattr(rag.requests, "Session", Session)
    return sessions


@pytest.mark.parametrize(
    ("model_name", "key_name", "payload", "expected_url"),
    [
        (
            "voyage-law-2", "VOYAGE_API_KEY",
            {"data": [{"embedding": [1, 2]}]},
            rag._VOYAGE_EMBEDDINGS_URL,
        ),
        (
            "embed-v4.0", "COHERE_API_KEY",
            {"embeddings": [[1, 2]]},
            rag._COHERE_EMBEDDINGS_URL,
        ),
        (
            "text-embedding-3-large", "OPENAI_API_KEY",
            {"data": [{"index": 0, "embedding": [1, 2]}]},
            rag._OPENAI_EMBEDDINGS_URL,
        ),
    ],
)
def test_cloud_embeddings_pin_origins_and_reviewed_environment_trust(
        monkeypatch, model_name, key_name, payload, expected_url):
    monkeypatch.setenv(key_name, "secret")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://sink.invalid/v1")
    monkeypatch.setenv("CO_API_URL", "https://sink.invalid/v2")
    sessions = _capture_cloud_sessions(
        monkeypatch, _CloudResponse(payload))
    policy = release_security.ReleaseSecurityPolicy(
        network_policy="allow-cloud",
        trust_environment_network=True,
    )

    embedding = rag._get_embedding_fn(
        model_name, security_policy=policy)

    assert embedding(["private text"]) == [[1.0, 2.0]]
    assert len(sessions) == 1
    assert sessions[0].trust_env is True
    assert sessions[0].posts[0][0] == expected_url
    assert sessions[0].posts[0][1]["timeout"] == 60
    assert sessions[0].posts[0][1]["allow_redirects"] is False
    assert sessions[0].posts[0][1]["stream"] is True
    assert isinstance(
        sessions[0].posts[0][1]["auth"],
        rag._llm_adapters._BearerAuth)


def test_cloud_transport_ignores_environment_by_default(monkeypatch):
    response = _CloudResponse({})
    sessions = _capture_cloud_sessions(monkeypatch, response)
    policy = release_security.ReleaseSecurityPolicy(
        profile="development", network_policy="allow-cloud")

    owned_response = rag._post_cloud_with_policy(
        policy, "https://provider.test/v1", timeout=1)

    assert owned_response.response is response
    assert sessions[0].trust_env is False
    assert sessions[0].posts[0][1]["stream"] is True


@pytest.mark.parametrize(
    ("model_name", "key_name", "payload"),
    [
        ("voyage-law-2", "VOYAGE_API_KEY", {"data": []}),
        ("embed-v4.0", "COHERE_API_KEY", {"embeddings": []}),
        ("text-embedding-3-large", "OPENAI_API_KEY", {"data": []}),
    ],
)
def test_every_embedding_provider_refuses_redirects(
        monkeypatch, model_name, key_name, payload):
    monkeypatch.setenv(key_name, "secret")
    _capture_cloud_sessions(
        monkeypatch, _CloudResponse(payload, status_code=307))
    policy = release_security.ReleaseSecurityPolicy(
        profile="development", network_policy="allow-cloud")

    with pytest.raises(RuntimeError, match="returned a redirect"):
        rag._get_embedding_fn(
            model_name, security_policy=policy)(["private text"])


@pytest.mark.parametrize("model_name", ["embo-01", "minimax-embedding-01"])
def test_minimax_embedding_models_fail_closed_after_cloud_authorization(
        monkeypatch, model_name):
    monkeypatch.setattr(
        rag, "_post_cloud_with_policy",
        lambda *_args, **_kwargs: pytest.fail(
            "unsupported MiniMax embedding reached transport"))
    policy = release_security.ReleaseSecurityPolicy(
        profile="development", network_policy="allow-cloud")

    with pytest.raises(ValueError, match="no current reviewed MiniMax"):
        rag._get_embedding_fn(model_name, security_policy=policy)


@pytest.mark.parametrize(
    ("model_name", "key_name", "expected_url"),
    [
        ("cohere-rerank-v3.5", "COHERE_API_KEY", rag._COHERE_RERANK_URL),
        (
            "jina-reranker-v2-base-multilingual", "JINA_API_KEY",
            rag._JINA_RERANK_URL,
        ),
    ],
)
def test_cloud_rerankers_pin_origins_and_refuse_redirects(
        monkeypatch, model_name, key_name, expected_url):
    monkeypatch.setenv(key_name, "secret")
    monkeypatch.setenv("CO_API_URL", "https://sink.invalid/v2")
    sessions = _capture_cloud_sessions(
        monkeypatch, _CloudResponse({"results": []}, status_code=308))
    policy = release_security.ReleaseSecurityPolicy(
        network_policy="allow-cloud",
        trust_environment_network=True,
    )

    with pytest.raises(RuntimeError, match="returned a redirect"):
        rag._rerank(
            "private query", ["private text"], [{}], [0.1], 1,
            reranker_model=model_name, security_policy=policy)
    assert sessions[0].posts[0][0] == expected_url
    assert sessions[0].posts[0][1]["allow_redirects"] is False


@pytest.mark.parametrize(
    ("model_name", "key_name"),
    [
        ("cohere-rerank-v3.5", "COHERE_API_KEY"),
        ("jina-reranker-v2-base-multilingual", "JINA_API_KEY"),
    ],
)
def test_cloud_rerankers_use_bounded_streaming_json(
        monkeypatch, model_name, key_name):
    monkeypatch.setenv(key_name, "secret")
    sessions = _capture_cloud_sessions(
        monkeypatch,
        _CloudResponse({
            "results": [{"index": 0, "relevance_score": 0.9}],
        }),
    )
    policy = release_security.ReleaseSecurityPolicy(
        profile="development", network_policy="allow-cloud")

    documents, metadatas, scores = rag._rerank(
        "query", ["document"], [{"stable_id": "one"}], [0.2], 1,
        reranker_model=model_name, security_policy=policy,
    )

    assert documents == ["document"]
    assert metadatas == [{"stable_id": "one"}]
    assert scores == [0.9]
    assert sessions[0].posts[0][1]["stream"] is True


@pytest.mark.parametrize(
    "vectors",
    [
        [],
        [[1.0], [2.0]],
        [[]],
        [[1.0, float("nan")]],
        [[True, 1.0]],
    ],
)
def test_embedding_response_validation_fails_closed(vectors):
    with pytest.raises(RuntimeError):
        rag._validated_embedding_vectors(
            vectors, expected_count=1, provider="fixture")


@pytest.mark.parametrize(
    "results",
    [
        [{"index": -1, "relevance_score": 1.0}],
        [{"index": 1, "relevance_score": 1.0}],
        [
            {"index": 0, "relevance_score": 1.0},
            {"index": 0, "relevance_score": 0.5},
        ],
        [{"index": 0, "relevance_score": float("inf")}],
    ],
)
def test_reranker_response_validation_fails_closed(results):
    with pytest.raises(RuntimeError):
        rag._validated_reranker_rows(
            {"results": results}, document_count=1, top_k=2,
            provider="fixture")


def test_gemini_pins_provider_mode_origin_and_transport_policy(monkeypatch):
    clients = []

    class Options:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class Client:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.closed = False
            clients.append(self)

        def close(self):
            self.closed = True

    types_module = ModuleType("google.genai.types")
    types_module.HttpOptions = Options
    types_module.HttpRetryOptions = Options
    genai_module = ModuleType("google.genai")
    genai_module.Client = Client
    genai_module.types = types_module
    google_module = ModuleType("google")
    google_module.genai = genai_module
    monkeypatch.setitem(sys.modules, "google", google_module)
    monkeypatch.setitem(sys.modules, "google.genai", genai_module)
    monkeypatch.setitem(sys.modules, "google.genai.types", types_module)
    monkeypatch.setattr(rag, "_gemini_client_cache", None)
    monkeypatch.setattr(rag, "_gemini_client_key", "")
    monkeypatch.setattr(rag, "_gemini_client_trust_environment", None)
    monkeypatch.setenv(
        "GOOGLE_GEMINI_BASE_URL", "https://sink.invalid/v1beta")
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    policy = release_security.ReleaseSecurityPolicy(
        network_policy="allow-cloud",
        trust_environment_network=True,
    )

    client, returned_types = rag._load_gemini_client(
        "secret", security_policy=policy)

    assert client is clients[0]
    assert returned_types is types_module
    assert client.kwargs["vertexai"] is False
    options = client.kwargs["http_options"]
    assert options.base_url == rag._GEMINI_API_BASE_URL
    assert options.api_version == "v1beta"
    assert options.timeout == 60_000
    assert options.retry_options.attempts == 1
    expected = {
        "trust_env": True, "follow_redirects": False, "verify": True}
    assert options.client_args == expected
    assert options.async_client_args == expected

    # Transport trust is part of the cache identity.
    development = release_security.ReleaseSecurityPolicy(
        profile="development", network_policy="allow-cloud")
    second, _ = rag._load_gemini_client(
        "secret", security_policy=development)
    assert second is clients[1]
    assert clients[0].closed is False
    assert second.kwargs["http_options"].client_args["trust_env"] is False


def test_gemini_cache_rotation_does_not_close_an_active_client(monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    clients = []
    outcome = {}

    class Options:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class Client:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.models = self
            self.closed = False
            clients.append(self)

        def close(self):
            self.closed = True

        def generate_content(self, **_kwargs):
            if self.kwargs["api_key"] == "first-key":
                entered.set()
                assert release.wait(5)
            if self.closed:
                raise RuntimeError("client was closed during request")
            return SimpleNamespace(text="answer")

    types_module = ModuleType("google.genai.types")
    types_module.HttpOptions = Options
    types_module.HttpRetryOptions = Options
    types_module.GenerateContentConfig = Options
    genai_module = ModuleType("google.genai")
    genai_module.Client = Client
    genai_module.types = types_module
    google_module = ModuleType("google")
    google_module.genai = genai_module
    monkeypatch.setitem(sys.modules, "google", google_module)
    monkeypatch.setitem(sys.modules, "google.genai", genai_module)
    monkeypatch.setitem(sys.modules, "google.genai.types", types_module)
    monkeypatch.setattr(rag, "_gemini_client_cache", None)
    monkeypatch.setattr(rag, "_gemini_client_key", "")
    monkeypatch.setattr(rag, "_gemini_client_trust_environment", None)
    policy = release_security.ReleaseSecurityPolicy(
        profile="development", network_policy="allow-cloud")

    def call_first():
        try:
            outcome["result"] = rag._call_gemini_result(
                "private prompt", api_key="first-key",
                security_policy=policy)
        except BaseException as exc:
            outcome["error"] = exc

    thread = threading.Thread(target=call_first)
    thread.start()
    assert entered.wait(5)
    rag._load_gemini_client("second-key", security_policy=policy)
    assert clients[0].closed is False
    release.set()
    thread.join(5)

    assert not thread.is_alive()
    assert "error" not in outcome
    assert outcome["result"].text == "answer"


def test_installed_gemini_client_uses_the_pinned_transport(monkeypatch):
    pytest.importorskip("google.genai")
    monkeypatch.setattr(rag, "_gemini_client_cache", None)
    monkeypatch.setattr(rag, "_gemini_client_key", "")
    monkeypatch.setattr(rag, "_gemini_client_trust_environment", None)
    monkeypatch.setenv(
        "GOOGLE_GEMINI_BASE_URL", "https://sink.invalid/v1beta")
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    policy = release_security.ReleaseSecurityPolicy(
        profile="development", network_policy="allow-cloud")

    client, _ = rag._load_gemini_client(
        "nonworking-test-key", security_policy=policy)
    try:
        api_client = client.models._api_client
        options = api_client.get_read_only_http_options()
        assert options["base_url"] == rag._GEMINI_API_BASE_URL
        assert options["api_version"] == "v1beta"
        assert options["timeout"] == 60_000
        assert options["retry_options"]["attempts"] == 1
        assert options["client_args"] == {
            "trust_env": False,
            "follow_redirects": False,
            "verify": True,
        }
        assert options["async_client_args"] == options["client_args"]
        assert api_client._httpx_client.follow_redirects is False
        assert api_client._httpx_client.trust_env is False
        assert api_client._async_httpx_client.follow_redirects is False
        assert api_client._async_httpx_client.trust_env is False
    finally:
        client.close()


def test_local_only_ignores_ambient_gemini_key(monkeypatch):
    observed = {}
    sentinel = SimpleNamespace(text="local")
    monkeypatch.setenv("GEMINI_API_KEY", "ambient-secret")
    monkeypatch.setattr(
        rag._llm_runtime, "execute",
        lambda request, providers: (
            observed.update(names=[provider.name for provider in providers])
            or sentinel),
    )

    result = rag._call_llm_result(
        "private prompt", ollama_url="http://127.0.0.1:11434")

    assert result is sentinel
    assert observed["names"] == ["ollama"]


class _ClosableResponse:
    def close(self):
        return None


def test_cloud_transport_applies_fallback_timeout(monkeypatch):
    sessions = _capture_cloud_sessions(monkeypatch, _ClosableResponse())

    rag._post_cloud_with_policy(None, "https://provider.test/v1")

    _url, kwargs = sessions[0].posts[0]
    assert kwargs["timeout"] == rag._TRANSPORT_FALLBACK_TIMEOUT


def test_cloud_transport_preserves_explicit_timeout(monkeypatch):
    sessions = _capture_cloud_sessions(monkeypatch, _ClosableResponse())

    rag._post_cloud_with_policy(
        None, "https://provider.test/v1", timeout=7.5)

    _url, kwargs = sessions[0].posts[0]
    assert kwargs["timeout"] == 7.5


def test_cloud_transport_coerces_explicit_none_timeout(monkeypatch):
    sessions = _capture_cloud_sessions(monkeypatch, _ClosableResponse())

    rag._post_cloud_with_policy(
        None, "https://provider.test/v1", timeout=None)

    _url, kwargs = sessions[0].posts[0]
    assert kwargs["timeout"] == rag._TRANSPORT_FALLBACK_TIMEOUT


def test_loopback_transport_applies_fallback_timeout(monkeypatch):
    sessions = _capture_cloud_sessions(monkeypatch, _ClosableResponse())

    rag._post_loopback_without_environment("http://127.0.0.1:11434/api")

    _url, kwargs = sessions[0].posts[0]
    assert kwargs["timeout"] == rag._TRANSPORT_FALLBACK_TIMEOUT
    assert sessions[0].trust_env is False
