from types import SimpleNamespace

import release_security
from tools import sync_model_artifacts


def test_sync_models_downloads_only_safe_primary_consumers(
        monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("HF_HUB_DISABLE_TELEMETRY", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    monkeypatch.delenv("HF_HUB_DISABLE_XET", raising=False)
    monkeypatch.delenv("HF_HUB_ENABLE_HF_TRANSFER", raising=False)
    class Artifact:
        model_id = "owner/model"
        consumers = ("embedding", "embedding_remote_code", "legacy")

        @staticmethod
        def files_for(consumer):
            suffix = ".bin" if consumer == "legacy" else ".safetensors"
            return (SimpleNamespace(path=f"model{suffix}"),)

    calls = []
    monkeypatch.setattr(
        sync_model_artifacts, "_selected_artifacts",
        lambda _models: (Artifact(),))
    monkeypatch.setattr(
        sync_model_artifacts.model_artifacts,
        "verified_model_directory",
        lambda model, consumer, **kwargs: (
            calls.append((model, consumer, kwargs))
            or tmp_path / consumer),
    )

    result = sync_model_artifacts.sync_models(
        [], cache_root=tmp_path / "cache",
        security_policy=release_security.ReleaseSecurityPolicy(
            profile="development",
            model_download_policy="allow-reviewed-sync"))

    assert result == [tmp_path / "embedding"]
    assert len(calls) == 1
    model, consumer, kwargs = calls[0]
    authorizer = kwargs.pop("authorize_download_fn")
    authorizer()
    assert (model, consumer, kwargs) == (
        "owner/model", "embedding",
        {
            "cache_root": tmp_path / "cache",
            "allow_download": True,
            "download_endpoint": (
                sync_model_artifacts.model_artifacts
                .HUGGINGFACE_HUB_OFFICIAL_ENDPOINT),
            "trust_environment_network": False,
        },
    )
    assert "unsafe pickle weights" in capsys.readouterr().err
    assert __import__("os").environ["HF_HUB_DISABLE_TELEMETRY"] == "1"
    assert __import__("os").environ["DO_NOT_TRACK"] == "1"
    assert __import__("os").environ["HF_HUB_DISABLE_XET"] == "1"
    assert __import__("os").environ["HF_HUB_ENABLE_HF_TRANSFER"] == "0"


def test_list_mode_is_offline_and_lists_reviewed_consumers(capsys):
    assert sync_model_artifacts.main([
        "--model", "BAAI/bge-reranker-v2-m3", "--list",
    ]) == 0

    assert capsys.readouterr().out.strip() == (
        "BAAI/bge-reranker-v2-m3:reranker")
