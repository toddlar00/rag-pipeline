from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import model_artifacts
import release_security
from tools import sync_model_artifacts


_BGE = "BAAI/bge-reranker-v2-m3"
_BART = "facebook/bart-large-mnli"
_LEGAL_BERT = "nlpaueb/legal-bert-base-uncased"
_NOMIC = "nomic-ai/nomic-embed-text-v2-moe"
_NOMIC_CODE = "nomic-ai/nomic-bert-2048"
_STELLA = "NovaSearch/stella_en_400M_v5"
_STELLA_ALIAS = "dunzhang/stella_en_400M_v5"
_DOCLING_LAYOUT = "docling-project/docling-layout-heron"
_DOCLING_TABLE = "docling-project/docling-models"
_AMPLE_FREE_BYTES = 1 << 60

_ALL_SAFE_KEYS = (
    (_NOMIC, "chunk_tokenizer"),
    (_NOMIC, "embedding"),
    (_NOMIC, "token_counter"),
    (_BGE, "reranker"),
    (_BART, "zero_shot_classifier"),
    (_STELLA, "chunk_tokenizer"),
    (_STELLA, "embedding"),
    (_STELLA, "token_counter"),
    (_LEGAL_BERT, "chunk_tokenizer"),
    (_LEGAL_BERT, "token_counter"),
    (_DOCLING_LAYOUT, "docling_layout"),
    (_DOCLING_TABLE, "docling_table_structure"),
)


def _disk_with_free(free: int):
    def disk_usage(_path: Path):
        return SimpleNamespace(free=free)

    return disk_usage


def _bundle_keys(plan: sync_model_artifacts.ModelSyncPlan):
    return tuple((bundle.model_id, bundle.consumer) for bundle in plan.bundles)


def _missing_plan(tmp_path: Path, model_ids=(), *, task_names=(),
                  select_all=False):
    cache_root = tmp_path / "model-cache" / "nested"
    plan = sync_model_artifacts.build_sync_plan(
        model_ids,
        task_names=task_names,
        select_all=select_all,
        cache_root=cache_root,
        disk_usage_fn=_disk_with_free(_AMPLE_FREE_BYTES),
    )
    assert not cache_root.exists()
    return plan


def _tiny_bundle(
        name: str, *, status: str, download_bytes: int,
        runtime_bytes: int) -> sync_model_artifacts.BundlePlan:
    return sync_model_artifacts.BundlePlan(
        model_id=f"owner/{name}",
        revision="a" * 40,
        consumer="embedding",
        identity_sha256="b" * 64,
        target=Path(name),
        status=status,
        primary_download_bytes=download_bytes,
        primary_runtime_bytes=runtime_bytes,
        auxiliary_model_id=None,
        auxiliary_download_bytes=0,
        auxiliary_runtime_bytes=0,
        transformed_bytes_removed=download_bytes - runtime_bytes,
    )


def _with_valid_plan_digest(
        plan: sync_model_artifacts.ModelSyncPlan,
        **changes,
) -> sync_model_artifacts.ModelSyncPlan:
    candidate = replace(plan, **changes)
    payload = sync_model_artifacts._plan_digest_payload(
        lock_sha256=candidate.lock_sha256,
        task_names=candidate.task_names,
        model_ids=candidate.model_ids,
        select_all=candidate.select_all,
        bundles=candidate.bundles,
        blocked_consumers=candidate.blocked_consumers,
        peak_additional_bytes=candidate.peak_additional_bytes,
        margin_bytes=candidate.margin_bytes,
    )
    digest = hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    return replace(candidate, plan_sha256=digest)


def test_committed_all_plan_has_exact_safe_order_totals_and_space_contract(
        tmp_path):
    plan = _missing_plan(tmp_path, select_all=True)

    assert _bundle_keys(plan) == _ALL_SAFE_KEYS
    assert len(plan.bundles) == 12
    assert plan.selected_download_bytes_if_uncached == 8_025_916_728
    assert plan.required_download_bytes == 8_025_916_728
    assert plan.selected_runtime_bytes == 8_025_916_546
    assert plan.already_present_runtime_bytes == 0
    assert plan.additional_runtime_bytes == 8_025_916_546
    assert plan.peak_additional_bytes == 9_386_092_085
    assert plan.margin_bytes == 469_304_605
    assert plan.required_free_bytes == 9_855_396_690
    assert plan.disk_probe_path == tmp_path
    assert plan.sufficient_space is True
    assert len(plan.lock_sha256) == 64
    assert len(plan.plan_sha256) == 64
    assert plan.blocked_consumers == (
        sync_model_artifacts.BlockedConsumerPlan(
            model_id=_LEGAL_BERT,
            consumer="embedding",
            reason="unsafe-pickle",
            paths=("pytorch_model.bin",),
        ),
    )


@pytest.mark.parametrize(
    ("task", "keys", "download_bytes", "runtime_bytes", "peak_bytes"),
    [
        (
            "pdf-ingestion",
            (
                (_NOMIC, "chunk_tokenizer"),
                (_DOCLING_LAYOUT, "docling_layout"),
                (_DOCLING_TABLE, "docling_table_structure"),
            ),
            406_584_534,
            406_584_534,
            619_349_982,
        ),
        (
            "default-retrieval",
            (
                (_NOMIC, "embedding"),
                (_NOMIC, "token_counter"),
                (_BGE, "reranker"),
            ),
            4_238_848_869,
            4_238_848_687,
            6_532_090_795,
        ),
        (
            "classification",
            ((_BART, "zero_shot_classifier"),),
            1_632_149_330,
            1_632_149_330,
            3_264_298_660,
        ),
    ],
)
def test_task_presets_have_exact_consumer_order_and_lock_derived_totals(
        tmp_path, task, keys, download_bytes, runtime_bytes, peak_bytes):
    plan = _missing_plan(tmp_path, task_names=[task])

    assert plan.task_names == (task,)
    assert plan.model_ids == ()
    assert _bundle_keys(plan) == keys
    assert plan.selected_download_bytes_if_uncached == download_bytes
    assert plan.required_download_bytes == download_bytes
    assert plan.selected_runtime_bytes == runtime_bytes
    assert plan.peak_additional_bytes == peak_bytes


def test_alias_tasks_and_models_deduplicate_in_registry_order(tmp_path):
    first = _missing_plan(
        tmp_path,
        [_STELLA_ALIAS, _BGE, _STELLA],
        task_names=[
            "default-retrieval", "pdf-ingestion", "default-retrieval"],
    )
    second = _missing_plan(
        tmp_path,
        [_BGE, _STELLA],
        task_names=["pdf-ingestion", "default-retrieval"],
    )

    expected = (
        (_NOMIC, "chunk_tokenizer"),
        (_NOMIC, "embedding"),
        (_NOMIC, "token_counter"),
        (_BGE, "reranker"),
        (_STELLA, "chunk_tokenizer"),
        (_STELLA, "embedding"),
        (_STELLA, "token_counter"),
        (_DOCLING_LAYOUT, "docling_layout"),
        (_DOCLING_TABLE, "docling_table_structure"),
    )
    assert first.task_names == ("pdf-ingestion", "default-retrieval")
    assert first.model_ids == (_BGE, _STELLA)
    assert _bundle_keys(first) == expected
    assert _bundle_keys(second) == expected
    assert first.plan_sha256 == second.plan_sha256


def test_nomic_embedding_spec_counts_transform_and_auxiliary_code_exactly(
        tmp_path):
    spec = model_artifacts.runtime_bundle_spec(
        _NOMIC, "embedding", cache_root=tmp_path / "cache")

    assert spec.primary_download_bytes == 1_923_344_862
    assert spec.primary_runtime_bytes == 1_923_344_680
    assert spec.auxiliary_model_id == _NOMIC_CODE
    assert spec.auxiliary_download_bytes == 105_521
    assert spec.auxiliary_runtime_bytes == 105_521
    assert spec.transformed_bytes_removed == 182
    assert spec.download_bytes == 1_923_450_383
    assert spec.runtime_bytes == 1_923_450_201
    transformed_config = next(
        file for file in spec.expected_files if file.path == "config.json")
    assert transformed_config.size == 2_300

    code = model_artifacts.model_artifact(_NOMIC_CODE)
    assert code is not None
    assert model_artifacts.classify_runtime_consumer(
        code, "embedding_remote_code") == "dependency-only"
    with pytest.raises(model_artifacts.ModelArtifactError,
                       match="dependency-only"):
        model_artifacts.runtime_bundle_spec(
            _NOMIC_CODE, "embedding_remote_code", cache_root=tmp_path)


def test_identity_v2_preserves_self_contained_cache_targets(tmp_path):
    artifact = model_artifacts.model_artifact(_BGE)
    assert artifact is not None
    consumer = "reranker"
    approved = artifact.files_for(consumer)
    legacy_payload = {
        "model_id": artifact.model_id,
        "revision": artifact.revision,
        "consumer": consumer,
        "files": [
            [file.path, file.size, file.content_sha256] for file in approved
        ],
        "transforms": [],
        "code_revision": None,
    }
    legacy_identity = hashlib.sha256(json.dumps(
        legacy_payload, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()

    spec = model_artifacts.runtime_bundle_spec(
        _BGE, consumer, cache_root=tmp_path / "cache")

    assert spec.identity_sha256 == legacy_identity
    assert spec.target.name.endswith(legacy_identity[:20])


def test_legalbert_plan_keeps_safe_tokenizers_and_reports_blocked_pickle(
        tmp_path):
    plan = _missing_plan(tmp_path, [_LEGAL_BERT])

    assert _bundle_keys(plan) == (
        (_LEGAL_BERT, "chunk_tokenizer"),
        (_LEGAL_BERT, "token_counter"),
    )
    assert plan.selected_download_bytes_if_uncached == 445_940
    assert plan.selected_runtime_bytes == 445_940
    assert plan.peak_additional_bytes == 668_910
    assert [(item.consumer, item.reason, item.paths)
            for item in plan.blocked_consumers] == [
        ("embedding", "unsafe-pickle", ("pytorch_model.bin",)),
    ]


def test_dependency_only_model_cannot_produce_successful_empty_plan(tmp_path):
    with pytest.raises(model_artifacts.ModelArtifactError,
                       match="dependency-only"):
        _missing_plan(tmp_path, [_NOMIC_CODE])


def test_verified_cache_status_reduces_download_runtime_and_peak(
        monkeypatch, tmp_path):
    monkeypatch.setattr(
        model_artifacts,
        "verify_cached_runtime_bundle",
        lambda spec: spec.consumer == "embedding",
    )
    plan = _missing_plan(tmp_path, task_names=["default-retrieval"])

    assert [bundle.status for bundle in plan.bundles] == [
        "verified-present", "missing", "missing"]
    assert plan.already_present_runtime_bytes == 1_923_450_201
    assert plan.required_download_bytes == 2_315_398_486
    assert plan.additional_runtime_bytes == 2_315_398_486
    assert plan.peak_additional_bytes == 4_608_640_594
    assert plan.margin_bytes == 230_432_030
    assert plan.required_free_bytes == 4_839_072_624

    monkeypatch.setattr(
        model_artifacts, "verify_cached_runtime_bundle", lambda _spec: True)
    present = _missing_plan(tmp_path, task_names=["default-retrieval"])
    assert present.required_download_bytes == 0
    assert present.already_present_runtime_bytes == 4_238_848_687
    assert present.additional_runtime_bytes == 0
    assert present.peak_additional_bytes == 0
    assert present.margin_bytes == 0
    assert present.required_free_bytes == 0


def test_corrupt_existing_cache_fails_instead_of_becoming_a_download(
        tmp_path):
    cache_root = tmp_path / "cache"
    spec = model_artifacts.runtime_bundle_spec(
        _BGE, "reranker", cache_root=cache_root)
    spec.target.mkdir(parents=True)
    (spec.target / "unexpected.txt").write_text("tamper", encoding="utf-8")

    with pytest.raises(model_artifacts.ModelArtifactError,
                       match="runtime model tree differs"):
        sync_model_artifacts.build_sync_plan(
            [_BGE],
            cache_root=cache_root,
            disk_usage_fn=_disk_with_free(_AMPLE_FREE_BYTES),
        )


def test_peak_formula_retains_previous_destinations_and_skips_present():
    bundles = (
        _tiny_bundle("first", status="missing", download_bytes=100,
                     runtime_bytes=80),
        _tiny_bundle("present", status="verified-present",
                     download_bytes=10_000, runtime_bytes=10_000),
        _tiny_bundle("last", status="missing", download_bytes=50,
                     runtime_bytes=60),
    )

    assert sync_model_artifacts._peak_additional_bytes(bundles) == 190
    assert sync_model_artifacts._space_margin(0) == 0
    assert sync_model_artifacts._space_margin(190) == 64 * 1024 * 1024
    assert sync_model_artifacts._space_margin(2_000_000_001) == 100_000_001


def test_insufficient_execution_preflight_has_no_download_or_env_mutation(
        monkeypatch, tmp_path):
    plan = _missing_plan(tmp_path, [_BGE])
    download_calls = []
    telemetry_calls = []
    monkeypatch.setattr(
        model_artifacts,
        "verified_model_directory",
        lambda *_args, **_kwargs: download_calls.append("download"),
    )
    monkeypatch.setattr(
        sync_model_artifacts,
        "_disable_auxiliary_telemetry",
        lambda: telemetry_calls.append("telemetry"),
    )

    with pytest.raises(model_artifacts.ModelArtifactError,
                       match="insufficient free space"):
        sync_model_artifacts.sync_models(
            plan,
            security_policy=release_security.ReleaseSecurityPolicy(
                profile="development",
                model_download_policy="allow-reviewed-sync",
            ),
            disk_usage_fn=_disk_with_free(plan.required_free_bytes - 1),
        )

    assert download_calls == []
    assert telemetry_calls == []
    assert not plan.cache_root.exists()


def test_executor_revalidates_and_uses_exact_plan_order_at_threshold(
        monkeypatch, tmp_path, capsys):
    for name in (
        "HF_HUB_DISABLE_TELEMETRY", "DO_NOT_TRACK", "HF_HUB_DISABLE_XET",
        "HF_HUB_ENABLE_HF_TRANSFER",
    ):
        monkeypatch.delenv(name, raising=False)
    plan = _missing_plan(tmp_path, task_names=["default-retrieval"])
    targets = {
        (bundle.model_id, bundle.consumer): bundle.target
        for bundle in plan.bundles
    }
    calls = []

    def verified(model_id, consumer, **kwargs):
        calls.append((model_id, consumer, kwargs))
        return targets[(model_id, consumer)]

    monkeypatch.setattr(
        model_artifacts, "verified_model_directory", verified)
    result = sync_model_artifacts.sync_models(
        plan,
        security_policy=release_security.ReleaseSecurityPolicy(
            profile="development",
            model_download_policy="allow-reviewed-sync",
        ),
        disk_usage_fn=_disk_with_free(plan.required_free_bytes),
    )

    assert result == [targets[key] for key in _bundle_keys(plan)]
    assert [(model, consumer) for model, consumer, _kwargs in calls] == list(
        _bundle_keys(plan))
    for _model, _consumer, kwargs in calls:
        assert kwargs["cache_root"] == plan.cache_root
        assert kwargs["allow_download"] is True
        assert kwargs["download_endpoint"] == (
            model_artifacts.HUGGINGFACE_HUB_OFFICIAL_ENDPOINT)
        assert kwargs["trust_environment_network"] is False
        assert callable(kwargs["authorize_download_fn"])
    assert os.environ["HF_HUB_DISABLE_TELEMETRY"] == "1"
    assert os.environ["DO_NOT_TRACK"] == "1"
    assert os.environ["HF_HUB_DISABLE_XET"] == "1"
    assert os.environ["HF_HUB_ENABLE_HF_TRANSFER"] == "0"
    assert capsys.readouterr().out.count("SYNCED ") == len(plan.bundles)


def test_legacy_empty_python_selection_still_means_all_safe_bundles(
        monkeypatch, tmp_path):
    calls = []

    def verified(model_id, consumer, *, cache_root, **_kwargs):
        calls.append((model_id, consumer))
        return cache_root / f"bundle-{len(calls)}"

    monkeypatch.setattr(
        model_artifacts, "verified_model_directory", verified)
    result = sync_model_artifacts.sync_models(
        [],
        cache_root=tmp_path / "cache",
        security_policy=release_security.ReleaseSecurityPolicy(
            profile="development",
            model_download_policy="allow-reviewed-sync",
        ),
        disk_usage_fn=_disk_with_free(_AMPLE_FREE_BYTES),
    )

    assert tuple(calls) == _ALL_SAFE_KEYS
    assert len(result) == len(_ALL_SAFE_KEYS)
    assert not (tmp_path / "cache").exists()


def test_executor_reverifies_present_bundle_after_an_earlier_download(
        monkeypatch, tmp_path):
    phase = {"value": "planning"}

    def verify_cached(spec):
        if (
            phase["value"] == "after-first-download"
            and spec.consumer == "token_counter"
        ):
            raise model_artifacts.ModelArtifactError(
                "runtime model checksum differs after preflight")
        return spec.consumer == "token_counter"

    monkeypatch.setattr(
        model_artifacts, "verify_cached_runtime_bundle", verify_cached)
    plan = _missing_plan(tmp_path, task_names=["default-retrieval"])
    phase["value"] = "execution"
    download_calls = []

    def verified(model_id, consumer, **_kwargs):
        download_calls.append((model_id, consumer))
        phase["value"] = "after-first-download"
        return next(
            bundle.target for bundle in plan.bundles
            if (bundle.model_id, bundle.consumer) == (model_id, consumer)
        )

    monkeypatch.setattr(
        model_artifacts, "verified_model_directory", verified)

    with pytest.raises(
            model_artifacts.ModelArtifactError,
            match="checksum differs after preflight"):
        sync_model_artifacts.sync_models(
            plan,
            security_policy=release_security.ReleaseSecurityPolicy(
                profile="development",
                model_download_policy="allow-reviewed-sync",
            ),
            disk_usage_fn=_disk_with_free(_AMPLE_FREE_BYTES),
        )

    assert download_calls == [(_NOMIC, "embedding")]


def test_executor_rejects_stale_plan_before_download(monkeypatch, tmp_path):
    plan = _missing_plan(tmp_path, [_BGE])
    stale_lock = "0" * 64
    digest_payload = sync_model_artifacts._plan_digest_payload(
        lock_sha256=stale_lock,
        task_names=plan.task_names,
        model_ids=plan.model_ids,
        select_all=plan.select_all,
        bundles=plan.bundles,
        blocked_consumers=plan.blocked_consumers,
        peak_additional_bytes=plan.peak_additional_bytes,
        margin_bytes=plan.margin_bytes,
    )
    stale_digest = hashlib.sha256(json.dumps(
        digest_payload, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    stale = replace(
        plan, lock_sha256=stale_lock, plan_sha256=stale_digest)
    calls = []
    monkeypatch.setattr(
        model_artifacts,
        "verified_model_directory",
        lambda *_args, **_kwargs: calls.append("download"),
    )

    with pytest.raises(model_artifacts.ModelArtifactError,
                       match="lock digest is stale"):
        sync_model_artifacts.sync_models(
            stale,
            disk_usage_fn=_disk_with_free(_AMPLE_FREE_BYTES),
        )
    assert calls == []


def test_executor_rejects_forged_plan_shapes_before_download(
        monkeypatch, tmp_path):
    plan = _missing_plan(tmp_path, [_BGE])
    bad_digest = replace(plan, plan_sha256="0" * 64)
    truncated = _with_valid_plan_digest(
        plan,
        bundles=(),
        selected_download_bytes_if_uncached=0,
        required_download_bytes=0,
        selected_runtime_bytes=0,
        already_present_runtime_bytes=0,
        additional_runtime_bytes=0,
        peak_additional_bytes=0,
        margin_bytes=0,
        required_free_bytes=0,
        sufficient_space=True,
    )
    forged_bundle = replace(
        plan.bundles[0], target=tmp_path / "different-target")
    mismatched_spec = _with_valid_plan_digest(
        plan, bundles=(forged_bundle,))
    download_calls = []
    monkeypatch.setattr(
        model_artifacts,
        "verified_model_directory",
        lambda *_args, **_kwargs: download_calls.append("download"),
    )

    for candidate, message in (
        (bad_digest, "digest is invalid"),
        (truncated, "selection is not canonical"),
        (mismatched_spec, "no longer matches"),
    ):
        with pytest.raises(model_artifacts.ModelArtifactError, match=message):
            sync_model_artifacts.sync_models(
                candidate,
                disk_usage_fn=_disk_with_free(_AMPLE_FREE_BYTES),
            )
    assert download_calls == []


def test_executor_rechecks_capacity_before_each_missing_bundle(
        monkeypatch, tmp_path):
    plan = _missing_plan(tmp_path, task_names=["default-retrieval"])
    free_values = iter((
        plan.required_free_bytes,
        plan.required_free_bytes,
        0,
    ))
    downloads = []

    def disk_usage(_path):
        return SimpleNamespace(free=next(free_values))

    def verified(model_id, consumer, **kwargs):
        downloads.append((model_id, consumer))
        return kwargs["cache_root"] / f"download-{len(downloads)}"

    monkeypatch.setattr(
        model_artifacts, "verified_model_directory", verified)

    with pytest.raises(
            sync_model_artifacts.ModelSyncCapacityError,
            match="before model bundle publication"):
        sync_model_artifacts.sync_models(
            plan,
            security_policy=release_security.ReleaseSecurityPolicy(
                profile="development",
                model_download_policy="allow-reviewed-sync",
            ),
            disk_usage_fn=disk_usage,
        )

    assert downloads == [(_NOMIC, "embedding")]


def test_json_plan_is_deterministic_offline_and_environment_pure(
        monkeypatch, tmp_path, capsys):
    cache_root = tmp_path / "missing-cache"
    environment = {
        "HF_ENDPOINT": "https://untrusted.invalid/private-value",
        "HTTPS_PROXY": "http://proxy.invalid/private-value",
        "HF_HUB_DISABLE_TELEMETRY": "sentinel-telemetry",
        "DO_NOT_TRACK": "sentinel-dnt",
        "HF_HUB_DISABLE_XET": "sentinel-xet",
        "HF_HUB_ENABLE_HF_TRANSFER": "sentinel-transfer",
    }
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(
        sync_model_artifacts.shutil,
        "disk_usage",
        _disk_with_free(_AMPLE_FREE_BYTES),
    )
    monkeypatch.setattr(
        sync_model_artifacts,
        "_disable_auxiliary_telemetry",
        lambda: pytest.fail("offline planning must not mutate telemetry"),
    )
    monkeypatch.setattr(
        release_security,
        "require_model_download",
        lambda *_args, **_kwargs: pytest.fail(
            "offline planning must not authorize transport"),
    )
    monkeypatch.setattr(
        model_artifacts,
        "verified_model_directory",
        lambda *_args, **_kwargs: pytest.fail(
            "offline planning must not download"),
    )

    first_args = [
        "--json", "--cache-root", str(cache_root),
        "--task", "default-retrieval", "--task", "pdf-ingestion",
        "--model", _STELLA_ALIAS, "--model", _BGE,
    ]
    second_args = [
        "--model", _BGE, "--task", "pdf-ingestion",
        "--model", _STELLA, "--task", "default-retrieval",
        "--cache-root", str(cache_root), "--json",
    ]
    assert sync_model_artifacts.main(first_args) == 0
    first = capsys.readouterr()
    assert sync_model_artifacts.main(second_args) == 0
    second = capsys.readouterr()

    assert first.err == second.err == ""
    assert first.out == second.out
    assert first.out.endswith("\n") and not first.out.endswith("\n\n")
    payload = json.loads(first.out)
    assert payload["schema"] == "rag.model-sync-plan"
    assert payload["schema_version"] == 1
    assert payload["offline"] is True
    assert payload["selection"] == {
        "all": False,
        "models": [_BGE, _STELLA],
        "tasks": ["pdf-ingestion", "default-retrieval"],
    }
    assert payload["scope"]["excludes"] == [
        "derived Docling composite caches",
        "RapidOCR assets verified from the installed package",
    ]
    assert "timestamp" not in first.out.casefold()
    assert "private-value" not in first.out
    assert not cache_root.exists()
    assert {name: os.environ[name] for name in environment} == environment


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["--all", "--model", _BGE],
        ["--json", "--list"],
        ["--plan", "--list"],
        ["--task", "unknown", "--plan"],
        ["--pla"],
    ],
)
def test_parser_rejects_ambiguous_or_invalid_cli_combinations(argv):
    with pytest.raises(SystemExit) as raised:
        sync_model_artifacts.main(argv)
    assert raised.value.code == 2


def test_json_alone_defaults_to_all_and_reports_insufficient_space(
        monkeypatch, tmp_path, capsys):
    cache_root = tmp_path / "cache"
    monkeypatch.setattr(
        sync_model_artifacts.shutil,
        "disk_usage",
        _disk_with_free(0),
    )

    assert sync_model_artifacts.main([
        "--json", "--cache-root", str(cache_root),
    ]) == 1
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert captured.err == ""
    assert payload["selection"] == {"all": True, "models": [], "tasks": []}
    assert payload["disk"]["sufficient"] is False
    assert payload["totals"]["selected_bundle_count"] == 12
    assert payload["totals"]["blocked_consumer_count"] == 1
    assert not cache_root.exists()


def test_human_plan_reports_every_space_total_and_insufficient_exit(
        monkeypatch, tmp_path, capsys):
    cache_root = tmp_path / "cache"
    monkeypatch.setattr(
        sync_model_artifacts.shutil, "disk_usage", _disk_with_free(0))

    assert sync_model_artifacts.main([
        "--task", "classification", "--plan",
        "--cache-root", str(cache_root),
    ]) == 1
    captured = capsys.readouterr()

    assert captured.err == ""
    assert "Uncached selected download: 1632149330" in captured.out
    assert "Required download: 1632149330" in captured.out
    assert "Selected runtime: 1632149330" in captured.out
    assert "Already present runtime: 0 (0 B)" in captured.out
    assert "Additional runtime: 1632149330" in captured.out
    assert "Peak additional space: 3264298660" in captured.out
    assert "Free-space margin: 163214933" in captured.out
    assert "Required free space with margin: 3427513593" in captured.out
    assert "Available free space: 0 (0 B)" in captured.out
    assert "Sufficient free space: no" in captured.out
    assert not cache_root.exists()


def test_sync_cli_reports_capacity_failure_as_operational_exit_one(
        monkeypatch, tmp_path, capsys):
    cache_root = tmp_path / "cache"
    monkeypatch.setattr(
        sync_model_artifacts.shutil, "disk_usage", _disk_with_free(0))
    monkeypatch.setattr(
        model_artifacts, "verified_model_directory",
        lambda *_args, **_kwargs: pytest.fail(
            "capacity refusal must happen before download"),
    )

    assert sync_model_artifacts.main([
        "--model", _BGE, "--cache-root", str(cache_root),
    ]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("ERROR: insufficient free space")
    assert "usage:" not in captured.err
    assert not cache_root.exists()


def test_list_mode_is_offline_filters_blocked_and_preserves_bge_output(
        monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(
        sync_model_artifacts.shutil,
        "disk_usage",
        _disk_with_free(_AMPLE_FREE_BYTES),
    )
    cache_root = tmp_path / "cache"

    assert sync_model_artifacts.main([
        "--model", _BGE, "--list", "--cache-root", str(cache_root),
    ]) == 0
    bge = capsys.readouterr()
    assert bge.out.strip() == f"{_BGE}:reranker"
    assert bge.err == ""

    assert sync_model_artifacts.main([
        "--list", "--cache-root", str(cache_root),
    ]) == 0
    all_models = capsys.readouterr()
    assert tuple(all_models.out.strip().splitlines()) == tuple(
        f"{model}:{consumer}" for model, consumer in _ALL_SAFE_KEYS)
    assert all_models.err.strip() == (
        f"BLOCKED {_LEGAL_BERT}:embedding (unsafe-pickle)")
    assert not cache_root.exists()
