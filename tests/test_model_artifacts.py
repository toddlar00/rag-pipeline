import copy
import hashlib
import io
import json
import os
import subprocess
import sys
from types import ModuleType, SimpleNamespace
from urllib.error import URLError
import zipfile

import pytest

import model_artifacts
import rag
import release_security
from tools import check_model_artifacts


def _policy_data():
    return model_artifacts.load_json(
        model_artifacts.MODEL_ARTIFACT_POLICY_PATH)


def _lock_data():
    return model_artifacts.load_json(
        model_artifacts.MODEL_ARTIFACT_LOCK_PATH)


def _hub_payload(locked_model):
    siblings = []
    for file in locked_model["files"]:
        sibling = {
            "rfilename": file["path"],
            "size": file["size"],
            "blobId": file["git_blob_sha1"],
        }
        if "lfs_sha256" in file:
            sibling["lfs"] = {
                "sha256": file["lfs_sha256"],
                "size": file["size"],
            }
        siblings.append(sibling)
    return {
        "id": locked_model["model_id"],
        "sha": locked_model["revision"],
        "gated": False,
        "cardData": {"license": locked_model["hub_license"]},
        "siblings": siblings,
    }


def _raw_sha256_lookup(lock):
    return {
        (model["model_id"], file["path"]): file["content_sha256"]
        for model in lock["models"] for file in model["files"]
    }


class _FakeResponse:
    def __init__(self, payload, *, content_length=None):
        self.payload = payload
        self.headers = {}
        if content_length is not None:
            self.headers["Content-Length"] = str(content_length)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, limit):
        return self.payload[:limit]


def _git_blob_sha1(payload):
    return hashlib.sha1(  # noqa: S324 - Git object identity by design.
        b"blob " + str(len(payload)).encode("ascii") + b"\0" + payload
    ).hexdigest()


def _model_file(path, payload):
    return model_artifacts.ModelArtifactFile(
        path=path,
        size=len(payload),
        git_blob_sha1=_git_blob_sha1(payload),
        content_sha256=hashlib.sha256(payload).hexdigest(),
    )


def _synthetic_artifacts(tmp_path):
    prefix = b"code/repo--"
    config = (
        b'{"auto_map":{"AutoConfig":"code/repo--config.C",'
        b'"AutoModel":"code/repo--model.M"}}'
    )
    derived = config.replace(prefix, b"")
    weights = b"safe tensor payload"
    config_code = b"class C: pass\n"
    model_code = b"from .config import C\nclass M: pass\n"
    main = model_artifacts.PinnedModelArtifact(
        model_id="owner/model",
        aliases=(),
        source_revision="main",
        revision="1" * 40,
        hub_license="apache-2.0",
        spdx_license="Apache-2.0",
        consumers=("embedding",),
        trust_remote_code=True,
        code_model_id="code/repo",
        runtime_files=(("embedding", ("config.json", "model.safetensors")),),
        runtime_transforms=(model_artifacts.RuntimeTransform(
            consumer="embedding",
            path="config.json",
            transform="strip_auto_map_repo_prefix_v1",
            argument="code/repo--",
            expected_occurrences=2,
            output_sha256=hashlib.sha256(derived).hexdigest(),
        ),),
        files=(
            _model_file("config.json", config),
            _model_file("model.safetensors", weights),
        ),
    )
    code = model_artifacts.PinnedModelArtifact(
        model_id="code/repo",
        aliases=(),
        source_revision="main",
        revision="2" * 40,
        hub_license="apache-2.0",
        spdx_license="Apache-2.0",
        consumers=("embedding_remote_code",),
        trust_remote_code=True,
        code_model_id="code/repo",
        runtime_files=((
            "embedding_remote_code", ("config.py", "model.py")),),
        runtime_transforms=(),
        files=(
            _model_file("config.py", config_code),
            _model_file("model.py", model_code),
        ),
    )
    roots = {}
    for artifact, payloads in (
        (main, {"config.json": config, "model.safetensors": weights}),
        (code, {"config.py": config_code, "model.py": model_code}),
    ):
        root = tmp_path / artifact.model_id.replace("/", "--")
        root.mkdir()
        for relative, payload in payloads.items():
            (root / relative).write_bytes(payload)
        roots[artifact.model_id] = root
    return main, code, roots, derived


def test_model_artifacts_is_a_standard_library_only_leaf_module():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import model_artifacts; "
                "forbidden = {'rag', 'requests', 'huggingface_hub', "
                "'transformers', 'sentence_transformers', 'FlagEmbedding', "
                "'docling', 'torch'}; "
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


def test_transformers_remote_code_cache_is_fresh_and_process_private(
        tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import os, sys; from pathlib import Path; "
                "from types import ModuleType; import model_artifacts as m; "
                f"root=Path({str(tmp_path)!r}); attacker=root/'attacker'; "
                "(attacker/'transformers_modules').mkdir(parents=True); "
                "(attacker/'transformers_modules'/'evil.py').write_text('evil'); "
                "os.environ['HF_MODULES_CACHE']=str(attacker); "
                "mods=[]; "
                "[(setattr(x:=ModuleType(n),'HF_MODULES_CACHE',str(attacker)), "
                "sys.modules.__setitem__(n,x), mods.append(x)) for n in "
                "('transformers.dynamic_module_utils','transformers.utils',"
                "'transformers.utils.hub')]; "
                "sys.modules['transformers_modules.evil']=ModuleType('evil'); "
                "cache=m.configure_transformers_dynamic_module_cache("
                "cache_root=root/'cache'); "
                "assert cache != attacker and cache.is_dir(); "
                "assert not any(cache.iterdir()); "
                "assert os.environ['HF_MODULES_CACHE'] == str(cache); "
                "assert all(x.HF_MODULES_CACHE == str(cache) for x in mods); "
                "assert sys.path[0] == str(cache); "
                "assert 'transformers_modules.evil' not in sys.modules"
            ),
        ],
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr


def test_rag_import_does_not_require_a_writable_model_cache(tmp_path):
    unavailable = tmp_path / "not-a-directory"
    unavailable.write_text("occupied", encoding="utf-8")
    environment = os.environ.copy()
    environment["RAG_MODEL_ARTIFACT_CACHE"] = str(unavailable)
    environment["HF_MODULES_CACHE"] = str(tmp_path / "preexisting")

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import os; import rag; "
                f"assert os.environ['HF_MODULES_CACHE'] == "
                f"{str(tmp_path / 'preexisting')!r}"
            ),
        ],
        env=environment,
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr


def test_committed_policy_lock_and_runtime_selection_are_complete():
    artifacts = model_artifacts.load_model_artifacts()
    packages = model_artifacts.load_package_models()

    assert len(artifacts) == 8
    assert sum(len(artifact.files) for artifact in artifacts) == 109
    assert len(packages) == 1
    assert packages[0].package == "rapidocr"
    assert len(packages[0].files) == 3
    assert all(
        len(file.content_sha256) == 64
        for artifact in artifacts for file in artifact.files
    )
    assert all(
        set(path for _consumer, paths in artifact.runtime_files for path in paths)
        <= {file.path for file in artifact.files}
        for artifact in artifacts
    )
    assert sum(artifact.trust_remote_code for artifact in artifacts) == 3

    nomic = model_artifacts.model_artifact(
        "nomic-ai/nomic-embed-text-v2-moe")
    assert nomic is not None
    config = next(file for file in nomic.files if file.path == "config.json")
    assert config.content_sha256 == (
        "4f076b4798fc2ba916f1900e0d10177714ee1aa94e0ef809102e723078d3efd3")
    assert nomic.runtime_transforms[0].output_sha256 == (
        "c0970f974acfcf7543d1cbafb71602e88b35a2e1980a5a340e1b0dd2e8917d69")


def test_model_lock_digest_is_validated_canonical_and_content_sensitive(
        tmp_path):
    lock = _lock_data()
    policy_path = tmp_path / "policy.json"
    lock_path = tmp_path / "lock.json"
    policy_path.write_text(
        json.dumps(_policy_data(), indent=3), encoding="utf-8")
    lock_path.write_text(json.dumps(lock, indent=4), encoding="utf-8")

    expected = model_artifacts.model_artifact_lock_sha256(
        lock_path, policy_path)
    model_artifacts.clear_model_artifact_registry_cache()
    compact = tmp_path / "compact-lock.json"
    compact.write_text(
        json.dumps(lock, separators=(",", ":")), encoding="utf-8")
    compact.replace(lock_path)
    assert model_artifacts.model_artifact_lock_sha256(
        lock_path, policy_path) == expected

    model, ordinary = next(
        (model, file)
        for model in lock["models"] for file in model["files"]
        if "lfs_sha256" not in file)
    original_checksum = ordinary["content_sha256"]
    ordinary["content_sha256"] = "0" * 64
    replacement = tmp_path / "replacement-lock.json"
    replacement.write_text(json.dumps(lock), encoding="utf-8")
    replacement.replace(lock_path)

    # An atomic on-disk replacement cannot mix new provenance with old cached
    # loader records inside a running operation/process.
    assert model_artifacts.model_artifact_lock_sha256(
        lock_path, policy_path) == expected
    cached_model = model_artifacts.model_artifact(
        model["model_id"], lock_path=lock_path, policy_path=policy_path)
    assert next(
        file for file in cached_model.files
        if file.path == ordinary["path"]).content_sha256 == original_checksum

    model_artifacts.clear_model_artifact_registry_cache()
    assert model_artifacts.model_artifact_lock_sha256(
        lock_path, policy_path) != expected

    lock["models"][0]["files"][0]["content_sha256"] = "invalid"
    lock_path.write_text(json.dumps(lock), encoding="utf-8")
    model_artifacts.clear_model_artifact_registry_cache()
    with pytest.raises(model_artifacts.ModelArtifactError, match="SHA-256"):
        model_artifacts.model_artifact_lock_sha256(lock_path, policy_path)


def test_alias_and_external_remote_code_revisions_are_pinned():
    stella = model_artifacts.model_artifact(
        "dunzhang/stella_en_400M_v5")
    assert stella is not None
    assert stella.model_id == "NovaSearch/stella_en_400M_v5"

    kwargs = model_artifacts.pinned_model_kwargs(
        "nomic-ai/nomic-embed-text-v2-moe",
        trust_remote_code=True,
    )
    assert kwargs == {
        "revision": "1066b6599d099fbb93dfcb64f9c37a7c9e503e85",
        "code_revision": "7710840340a098cfb869c4f65e87cf2b1b70caca",
    }
    with pytest.raises(model_artifacts.ModelArtifactError, match="not approved"):
        model_artifacts.pinned_model_kwargs(
            "BAAI/bge-reranker-v2-m3", trust_remote_code=True)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda policy: policy["models"][1].update(
                code_model_id="unreviewed/code"),
            "unpinned remote code",
        ),
        (
            lambda policy: policy["models"][1]["aliases"].append(
                "nomic-ai/nomic-embed-text-v2-moe"),
            "duplicated",
        ),
        (
            lambda policy: policy["models"][0]["runtime_files"][
                "embedding"].reverse(),
            "sorted",
        ),
        (
            lambda policy: policy["models"][0]["runtime_transforms"][0].update(
                transform="unknown"),
            "unknown transform",
        ),
    ],
)
def test_policy_rejects_unreviewed_or_ambiguous_artifacts(mutation, message):
    policy = copy.deepcopy(_policy_data())
    mutation(policy)

    with pytest.raises(model_artifacts.ModelArtifactError, match=message):
        model_artifacts.validate_model_policy(policy)


def test_lock_rejects_paths_hashes_and_runtime_inventory_drift():
    lock = copy.deepcopy(_lock_data())
    lock["models"][0]["files"][0]["path"] = "../escape"
    with pytest.raises(model_artifacts.ModelArtifactError, match="safe"):
        model_artifacts.validate_model_artifact_lock(
            lock, policy_data=_policy_data())

    lock = copy.deepcopy(_lock_data())
    lock["models"][0]["files"][0]["content_sha256"] = "0" * 63
    with pytest.raises(model_artifacts.ModelArtifactError, match="SHA-256"):
        model_artifacts.validate_model_artifact_lock(
            lock, policy_data=_policy_data())

    lock = copy.deepcopy(_lock_data())
    lock["models"][0]["runtime_files"]["embedding"].append("missing.bin")
    with pytest.raises(model_artifacts.ModelArtifactError, match="sorted|missing"):
        model_artifacts.validate_model_artifact_lock(
            lock, policy_data=None)


def test_hub_normalization_rebuilds_source_and_raw_hash_inventory():
    policy = {
        item["model_id"]: item
        for item in model_artifacts.validate_model_policy(_policy_data())
    }
    lock = _lock_data()
    raw_hashes = _raw_sha256_lookup(lock)

    for locked in lock["models"]:
        observed = model_artifacts.add_hub_content_checksums(
            model_artifacts.normalize_hub_model(
                policy[locked["model_id"]], _hub_payload(locked)),
            file_sha256_fn=lambda model_id, _revision, path, _size, _blob: (
                raw_hashes[(model_id, path)]),
        )
        assert observed == locked


def test_hub_verification_detects_metadata_and_raw_content_mismatch():
    lock = _lock_data()
    payloads = {
        model["model_id"]: _hub_payload(model)
        for model in lock["models"]
    }
    raw_hashes = _raw_sha256_lookup(lock)
    package = lock["package_models"][0]
    common = {
        "fetch_fn": lambda model_id, _revision: payloads[model_id],
        "file_sha256_fn": (
            lambda model_id, _revision, path, _size, _blob:
            raw_hashes[(model_id, path)]),
        "pypi_fetch_fn": lambda *_args: {},
        "package_resolve_fn": lambda *_args: package,
    }
    check_model_artifacts.verify_hub(_policy_data(), lock, **common)

    changed_hashes = dict(raw_hashes)
    changed_hashes[next(iter(changed_hashes))] = "0" * 64
    with pytest.raises(model_artifacts.ModelArtifactError, match="differs"):
        check_model_artifacts.verify_hub(
            _policy_data(), lock,
            **(common | {
                "file_sha256_fn": (
                    lambda model_id, _revision, path, _size, _blob:
                    changed_hashes[(model_id, path)]),
            }),
        )


def test_hub_metadata_and_file_fetches_are_bounded_and_retry():
    calls = []
    sleeps = []

    def flaky(request, *, timeout):
        calls.append((request, timeout))
        if len(calls) == 1:
            raise URLError("temporary")
        return _FakeResponse(b'{"ok": true}')

    assert model_artifacts.fetch_hub_model(
        "owner/model", "a" * 40, opener=flaky,
        sleep_fn=sleeps.append) == {"ok": True}
    assert sleeps == [0.5]
    assert calls[-1][0].get_header("Authorization") is None

    payload = b"reviewed bytes"
    git_sha1 = _git_blob_sha1(payload)
    checksum = model_artifacts.fetch_hub_file_sha256(
        "owner/model", "a" * 40, "config.json", len(payload), git_sha1,
        opener=lambda *_args, **_kwargs: _FakeResponse(
            payload, content_length=len(payload)),
    )
    assert checksum == hashlib.sha256(payload).hexdigest()

    with pytest.raises(model_artifacts.ModelArtifactError, match="Git hash"):
        model_artifacts.fetch_hub_file_sha256(
            "owner/model", "a" * 40, "config.json", len(payload), "0" * 40,
            opener=lambda *_args, **_kwargs: _FakeResponse(payload),
        )


def test_pypi_wheel_model_payloads_are_verified_from_actual_bytes():
    files = {
        "rapidocr/models/a.onnx": b"detector",
        "rapidocr/models/b.onnx": b"recognizer",
    }
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for path, payload in files.items():
            archive.writestr(path, payload)
    wheel = stream.getvalue()
    policy = {
        "package": "rapidocr",
        "version": "1.2.3",
        "wheel_filename": "rapidocr-1.2.3-py3-none-any.whl",
        "spdx_license": "Apache-2.0",
        "consumers": ["docling_ocr"],
        "files": [
            {"path": "rapidocr/models/a.onnx", "role": "detection"},
            {"path": "rapidocr/models/b.onnx", "role": "recognition"},
        ],
    }
    url = "https://files.pythonhosted.org/packages/rapidocr.whl"
    payload = {
        "info": {
            "name": "rapidocr", "version": "1.2.3",
            "license_expression": "Apache-2.0",
        },
        "urls": [{
            "filename": policy["wheel_filename"],
            "packagetype": "bdist_wheel",
            "url": url,
            "size": len(wheel),
            "digests": {"sha256": hashlib.sha256(wheel).hexdigest()},
        }],
    }

    normalized = model_artifacts.normalize_pypi_package_model(policy, payload)
    locked = model_artifacts.add_package_model_checksums(normalized, wheel)

    assert locked["wheel_sha256"] == hashlib.sha256(wheel).hexdigest()
    assert [item["content_sha256"] for item in locked["files"]] == [
        hashlib.sha256(files[path]).hexdigest() for path in sorted(files)
    ]
    with pytest.raises(model_artifacts.ModelArtifactError, match="wheel SHA-256"):
        model_artifacts.add_package_model_checksums(normalized, wheel[:-1] + b"x")


def test_verified_model_directory_builds_offline_remote_code_bundle(
        monkeypatch, tmp_path):
    main, code, roots, derived = _synthetic_artifacts(tmp_path)
    artifacts = {main.model_id: main, code.model_id: code}
    calls = []

    monkeypatch.setattr(
        model_artifacts, "model_artifact", lambda model_id, **_kwargs:
        artifacts.get(model_id))

    def snapshot_download(**kwargs):
        calls.append(kwargs)
        return str(roots[kwargs["repo_id"]])

    result = model_artifacts.verified_model_directory(
        main.model_id, "embedding",
        cache_root=tmp_path / "cache",
        snapshot_download_fn=snapshot_download,
        allow_download=True,
        authorize_download_fn=lambda: None,
    )

    assert (result / "config.json").read_bytes() == derived
    assert (result / "config.py").read_bytes() == b"class C: pass\n"
    assert (result / "model.py").is_file()
    assert all(not path.is_symlink() for path in result.rglob("*"))
    assert calls == [
        {
            "repo_id": "owner/model",
            "revision": "1" * 40,
            "allow_patterns": ["config.json", "model.safetensors"],
            "endpoint": model_artifacts.HUGGINGFACE_HUB_OFFICIAL_ENDPOINT,
            "token": False,
            "etag_timeout": 10,
        },
        {
            "repo_id": "code/repo",
            "revision": "2" * 40,
            "allow_patterns": ["config.py", "model.py"],
            "endpoint": model_artifacts.HUGGINGFACE_HUB_OFFICIAL_ENDPOINT,
            "token": False,
            "etag_timeout": 10,
        },
    ]

    again = model_artifacts.verified_model_directory(
        main.model_id, "embedding",
        cache_root=tmp_path / "cache",
        snapshot_download_fn=lambda **_kwargs: pytest.fail(
            "an already verified bundle must be offline"),
    )
    assert again == result


def test_verified_model_directory_is_cache_only_until_explicit_sync(
        monkeypatch, tmp_path):
    main, code, roots, _derived = _synthetic_artifacts(tmp_path)
    artifacts = {main.model_id: main, code.model_id: code}
    monkeypatch.setattr(
        model_artifacts, "model_artifact",
        lambda model_id, **_kwargs: artifacts.get(model_id))
    observed = []

    with pytest.raises(
            model_artifacts.ModelArtifactError, match="not synchronized"):
        model_artifacts.verified_model_directory(
            main.model_id, "embedding", cache_root=tmp_path / "cache",
            snapshot_download_fn=lambda **_kwargs: pytest.fail(
                "cache-only runtime must not construct a download"))

    with pytest.raises(
            model_artifacts.ModelArtifactError,
            match="explicit download authorizer"):
        model_artifacts.verified_model_directory(
            main.model_id, "embedding", cache_root=tmp_path / "cache",
            allow_download=True,
            snapshot_download_fn=lambda **kwargs: str(
                roots[kwargs["repo_id"]]))

    result = model_artifacts.verified_model_directory(
        main.model_id, "embedding", cache_root=tmp_path / "cache",
        allow_download=True,
        authorize_download_fn=lambda: observed.append("authorized"),
        snapshot_download_fn=lambda **kwargs: str(roots[kwargs["repo_id"]]),
    )

    assert result.is_dir()
    assert observed == ["authorized"]


def test_hub_download_owns_endpoint_auth_deadline_redirect_and_size_policy(
        monkeypatch, tmp_path):
    import requests

    sessions = []
    payload = b"reviewed model bytes"

    class Response:
        headers = {"Content-Length": str(len(payload))}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def raise_for_status(self):
            return None

        def iter_content(self, *, chunk_size):
            assert chunk_size == 1024 * 1024
            return iter((payload,))

    class Session:
        def __init__(self):
            self.trust_env = None
            self.auth = None
            self.max_redirects = None
            self.headers = {}
            self.gets = []
            self.closed = False
            sessions.append(self)

        def get(self, url, **kwargs):
            self.gets.append((url, kwargs))
            return Response()

        def close(self):
            self.closed = True

    monkeypatch.setattr(requests, "Session", Session)
    monkeypatch.setenv("HF_ENDPOINT", "https://sink.invalid")
    destination = tmp_path / "download"

    result = model_artifacts._snapshot_model_repository(
        repo_id="owner/model", revision="1" * 40,
        allow_patterns=["weights/model.safetensors"],
        endpoint=model_artifacts.HUGGINGFACE_HUB_OFFICIAL_ENDPOINT,
        trust_environment_network=False,
        destination=destination,
        expected_sizes={"weights/model.safetensors": len(payload)})

    assert result == str(destination)
    assert (destination / "weights/model.safetensors").read_bytes() == payload
    session = sessions[0]
    assert session.trust_env is False
    assert session.max_redirects == 5
    assert session.headers == {
        "Accept-Encoding": "identity",
        "User-Agent": "rag-pipeline-model-sync/1",
    }
    assert session.gets == [(
        "https://huggingface.co/owner/model/resolve/" + "1" * 40
        + "/weights/model.safetensors",
        {
            "stream": True,
            "allow_redirects": True,
            "timeout": (10, 60),
        },
    )]
    request = SimpleNamespace(headers={"Authorization": "ambient"})
    assert session.auth(request) is request
    assert "Authorization" not in request.headers
    request.headers["Authorization"] = "redirect-netrc"
    session.rebuild_auth(request, SimpleNamespace())
    assert "Authorization" not in request.headers
    assert session.closed is True

    trusted_destination = tmp_path / "trusted"
    model_artifacts._snapshot_model_repository(
        repo_id="owner/model", revision="1" * 40,
        allow_patterns=["config.json"],
        endpoint="https://reviewed-mirror.example",
        trust_environment_network=True,
        destination=trusted_destination,
        expected_sizes={"config.json": len(payload)})
    assert sessions[-1].trust_env is True
    assert sessions[-1].gets[0][0].startswith(
        "https://reviewed-mirror.example/owner/model/resolve/")


def test_model_download_redirect_never_rebuilds_netrc_auth(monkeypatch):
    import requests

    discovered = []
    monkeypatch.setattr(
        requests.sessions, "get_netrc_auth",
        lambda url: discovered.append(url) or ("ambient", "secret"))
    session = model_artifacts._model_download_session(
        requests, trust_environment_network=True)
    request = requests.Request(
        "GET", "https://cdn.example/model",
        headers={"Authorization": "Basic ambient"}).prepare()
    try:
        session.rebuild_auth(request, SimpleNamespace())
    finally:
        session.close()

    assert "Authorization" not in request.headers
    assert discovered == []


def test_model_download_rejects_https_to_http_redirect_before_following():
    import requests

    session = model_artifacts._model_download_session(
        requests, trust_environment_network=False)
    response = requests.Response()
    response.status_code = 302
    response.url = "https://huggingface.co/owner/model/resolve/rev/file"
    response.headers["Location"] = "http://cdn.example/file"
    try:
        with pytest.raises(
                model_artifacts.ModelArtifactError,
                match="redirect must remain credential-free HTTPS"):
            session.get_redirect_target(response)
    finally:
        session.close()


def test_model_download_stage_is_removed_when_publication_stage_fails(
        monkeypatch, tmp_path):
    main, code, roots, _derived = _synthetic_artifacts(tmp_path)
    artifacts = {main.model_id: main, code.model_id: code}
    monkeypatch.setattr(
        model_artifacts, "model_artifact",
        lambda model_id, **_kwargs: artifacts.get(model_id))
    real_mkdtemp = model_artifacts.tempfile.mkdtemp

    def fail_publication_stage(*args, **kwargs):
        if ".download." in kwargs.get("prefix", ""):
            return real_mkdtemp(*args, **kwargs)
        raise OSError("injected publication-stage failure")

    monkeypatch.setattr(model_artifacts.tempfile, "mkdtemp", fail_publication_stage)
    cache_root = tmp_path / "cache"

    with pytest.raises(OSError, match="publication-stage failure"):
        model_artifacts.verified_model_directory(
            main.model_id, "embedding", cache_root=cache_root,
            allow_download=True, authorize_download_fn=lambda: None,
            snapshot_download_fn=lambda **kwargs: str(
                roots[kwargs["repo_id"]]))

    assert not list(cache_root.glob(".*.download.*"))


def test_verified_model_directory_rejects_tampering_and_pickle(
        monkeypatch, tmp_path):
    main, code, roots, _derived = _synthetic_artifacts(tmp_path)
    artifacts = {main.model_id: main, code.model_id: code}
    monkeypatch.setattr(
        model_artifacts, "model_artifact", lambda model_id, **_kwargs:
        artifacts.get(model_id))
    def download(**kwargs):
        return str(roots[kwargs["repo_id"]])
    result = model_artifacts.verified_model_directory(
        main.model_id, "embedding", cache_root=tmp_path / "cache",
        snapshot_download_fn=download, allow_download=True,
        authorize_download_fn=lambda: None)
    (result / "unexpected.txt").write_text("tamper", encoding="utf-8")

    with pytest.raises(model_artifacts.ModelArtifactError, match="unexpected"):
        model_artifacts.verified_model_directory(
            main.model_id, "embedding", cache_root=tmp_path / "cache",
            snapshot_download_fn=download)

    legal = next(
        artifact for artifact in model_artifacts.load_model_artifacts()
        if artifact.model_id == "nlpaueb/legal-bert-base-uncased")
    monkeypatch.setattr(
        model_artifacts, "model_artifact", lambda *_args, **_kwargs: legal)
    with pytest.raises(model_artifacts.ModelArtifactError, match="pickle"):
        model_artifacts.verified_model_directory(
            legal.model_id, "embedding", cache_root=tmp_path / "legal",
            snapshot_download_fn=lambda **_kwargs: pytest.fail("must block first"))


def test_verified_installed_package_model_checks_version_and_bytes(
        monkeypatch, tmp_path):
    root = tmp_path / "rapidocr"
    model_path = root / "models/model.onnx"
    model_path.parent.mkdir(parents=True)
    model_path.write_bytes(b"onnx")
    package = model_artifacts.PinnedPackageModel(
        package="rapidocr",
        version="1.2.3",
        wheel_filename="rapidocr.whl",
        wheel_url="https://files.pythonhosted.org/rapidocr.whl",
        wheel_size=10,
        wheel_sha256="a" * 64,
        spdx_license="Apache-2.0",
        consumers=("docling_ocr",),
        files=(model_artifacts.PackageModelFile(
            path="rapidocr/models/model.onnx",
            role="detection",
            size=4,
            content_sha256=hashlib.sha256(b"onnx").hexdigest(),
        ),),
    )
    monkeypatch.setattr(model_artifacts, "package_model", lambda *_args: package)
    monkeypatch.setattr(model_artifacts.importlib.metadata, "version", lambda *_: "1.2.3")
    monkeypatch.setattr(model_artifacts.importlib.resources, "files", lambda *_: root)

    assert model_artifacts.verified_installed_package_model(
        "rapidocr", "docling_ocr") == {"detection": model_path}
    model_path.write_bytes(b"evil")
    with pytest.raises(model_artifacts.ModelArtifactError, match="checksum"):
        model_artifacts.verified_installed_package_model(
            "rapidocr", "docling_ocr")


def test_checker_uses_custom_lock_for_installed_package_verification(
        monkeypatch, tmp_path):
    policy = copy.deepcopy(_policy_data())
    lock = copy.deepcopy(_lock_data())
    policy_package = policy["package_models"][0]
    locked_package = lock["package_models"][0]
    policy_package["version"] = "9.9.9"
    policy_package["wheel_filename"] = "rapidocr-9.9.9-py3-none-any.whl"
    locked_package["version"] = "9.9.9"
    locked_package["wheel_filename"] = "rapidocr-9.9.9-py3-none-any.whl"
    policy_path = tmp_path / "custom-policy.json"
    lock_path = tmp_path / "custom-lock.json"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    lock_path.write_text(json.dumps(lock), encoding="utf-8")
    observed = []
    monkeypatch.setattr(
        model_artifacts, "verified_installed_package_model",
        lambda package, consumer, *, artifact: observed.append(
            (package, consumer, artifact.version)),
    )

    assert check_model_artifacts.main([
        "--policy", str(policy_path), "--lock", str(lock_path),
        "--verify-installed-packages",
    ]) == 0
    assert observed == [("rapidocr", "docling_ocr", "9.9.9")]


def test_model_sbom_has_model_file_hashes_and_dependency_edges():
    artifacts = model_artifacts.load_model_artifacts()
    packages = model_artifacts.load_package_models()

    first = model_artifacts.model_artifact_sbom(artifacts, packages)
    second = model_artifacts.model_artifact_sbom(artifacts, packages)

    assert first == second
    assert first["specVersion"] == "1.7"
    models = [
        item for item in first["components"]
        if item["type"] == "machine-learning-model"
    ]
    files = [item for item in first["components"] if item["type"] == "file"]
    assert len(models) == 9
    assert files
    assert all(item["hashes"][0]["alg"] == "SHA-256" for item in files)
    assert any(
        item["hashes"][0]["content"] ==
        "c0970f974acfcf7543d1cbafb71602e88b35a2e1980a5a340e1b0dd2e8917d69"
        for item in files
    )
    nomic = model_artifacts.model_artifact(
        "nomic-ai/nomic-embed-text-v2-moe")
    assert nomic is not None
    dependency = next(
        item for item in first["dependencies"] if item["ref"] == nomic.purl)
    assert model_artifacts.model_artifact(
        "nomic-ai/nomic-bert-2048").purl in dependency["dependsOn"]


def test_model_loader_source_is_verified_or_explicitly_opted_out(
        monkeypatch, tmp_path):
    artifact = SimpleNamespace(
        model_id="owner/model", trust_remote_code=True)
    cache_calls = []
    monkeypatch.setattr(
        rag._model_artifacts, "model_artifact",
        lambda model_id: artifact if model_id == "owner/model" else None)
    monkeypatch.setattr(
        rag._model_artifacts, "verified_model_directory",
        lambda *_args, **_kwargs: tmp_path)
    monkeypatch.setattr(
        rag._model_artifacts, "configure_transformers_dynamic_module_cache",
        lambda: cache_calls.append(True))

    assert rag._model_loader_source("owner/model", "embedding") == (
        str(tmp_path), True)
    assert cache_calls == []
    assert rag._model_loader_source(
        "owner/model", "embedding", execute_remote_code=True) == (
            str(tmp_path), True)
    assert cache_calls == [True]
    monkeypatch.delenv("RAG_ALLOW_UNPINNED_MODELS", raising=False)
    with pytest.raises(model_artifacts.ModelArtifactError, match="reviewed"):
        rag._model_loader_source("custom/model", "embedding")
    monkeypatch.setenv("RAG_ALLOW_UNPINNED_MODELS", "1")
    development_policy = release_security.ReleaseSecurityPolicy(
        profile="development",
        model_download_policy="allow-reviewed-sync")
    assert rag._model_loader_source(
        "custom/model", "embedding",
        security_policy=development_policy) == (
        "custom/model", False)


def test_tokenizer_loader_receives_only_verified_local_path(monkeypatch):
    captured = {}
    encoded = []
    monkeypatch.setattr(
        rag, "_model_loader_source", lambda *_args: ("verified/tokenizer", True))

    class FakeTokenizer:
        @classmethod
        def from_pretrained(cls, model_name, **kwargs):
            captured.update(model_name=model_name, kwargs=kwargs)
            return cls()

        def encode(self, text, **kwargs):
            encoded.append((text, kwargs))
            return [1, 2]

    module = ModuleType("transformers")
    module.AutoTokenizer = FakeTokenizer
    monkeypatch.setitem(sys.modules, "transformers", module)

    counts, exact = rag._count_embedding_text_tokens(
        ["text"], rag.DEFAULT_EMBEDDING_MODEL_GENERAL)

    assert (counts, exact) == ([2], True)
    assert captured == {
        "model_name": "verified/tokenizer",
        "kwargs": {"trust_remote_code": False, "local_files_only": True},
    }
    assert encoded == [(
        "search_document: text",
        {"add_special_tokens": True, "truncation": False},
    )]


def test_generic_embedding_token_counter_does_not_add_nomic_prefix(
        monkeypatch):
    encoded = []
    monkeypatch.setattr(
        rag, "_model_loader_source", lambda *_args: ("verified/tokenizer", True))

    class FakeTokenizer:
        @classmethod
        def from_pretrained(cls, *_args, **_kwargs):
            return cls()

        def encode(self, text, **kwargs):
            encoded.append((text, kwargs))
            return [1, 2, 3]

    module = ModuleType("transformers")
    module.AutoTokenizer = FakeTokenizer
    monkeypatch.setitem(sys.modules, "transformers", module)

    counts, exact = rag._count_embedding_text_tokens(
        ["ordinary document"], "owner/generic-embedding-model")

    assert (counts, exact) == ([3], True)
    assert encoded == [(
        "ordinary document",
        {"add_special_tokens": True, "truncation": False},
    )]


def test_sentence_transformer_loader_uses_offline_verified_bundle(
        monkeypatch):
    captured = {}
    monkeypatch.setitem(sys.modules, "chromadb", None)
    monkeypatch.setattr(
        rag, "_model_loader_source",
        lambda *_args, **_kwargs: ("verified/embedding", True))
    monkeypatch.setattr(
        rag._model_artifacts, "model_artifact",
        lambda *_args: SimpleNamespace(trust_remote_code=True))

    class FakeSentenceTransformer:
        def __init__(self, model_name, **kwargs):
            captured.update(model_name=model_name, kwargs=kwargs)

    sentence_transformers = ModuleType("sentence_transformers")
    sentence_transformers.SentenceTransformer = FakeSentenceTransformer
    monkeypatch.setitem(sys.modules, "sentence_transformers", sentence_transformers)

    embedding = rag._get_embedding_fn(rag.DEFAULT_EMBEDDING_MODEL_GENERAL)
    embedding._load()

    assert captured == {
        "model_name": "verified/embedding",
        "kwargs": {
            "trust_remote_code": True,
            "local_files_only": True,
            "model_kwargs": {"use_safetensors": True},
        },
    }


@pytest.mark.parametrize("model_name", ["embo-01", "minimax-embedding-01"])
def test_minimax_embedding_models_fail_closed_without_transport(
        monkeypatch, model_name):
    monkeypatch.setattr(
        rag, "_post_cloud_with_policy",
        lambda *_args, **_kwargs: pytest.fail(
            "unsupported MiniMax embedding reached transport"))

    with pytest.raises(ValueError, match="no current reviewed MiniMax"):
        rag._get_embedding_fn(
            model_name,
            security_policy=release_security.ReleaseSecurityPolicy(
                profile="development", network_policy="allow-cloud"))


def test_sentence_transformer_rejects_overstated_configured_limit(
        monkeypatch):
    monkeypatch.setitem(sys.modules, "chromadb", None)
    monkeypatch.setattr(
        rag, "_model_loader_source",
        lambda *_args, **_kwargs: ("verified/embedding", True))
    monkeypatch.setattr(
        rag._model_artifacts, "model_artifact",
        lambda *_args: SimpleNamespace(trust_remote_code=True))

    class FakeSentenceTransformer:
        max_seq_length = 256

        def __init__(self, *_args, **_kwargs):
            pass

    sentence_transformers = ModuleType("sentence_transformers")
    sentence_transformers.SentenceTransformer = FakeSentenceTransformer
    monkeypatch.setitem(
        sys.modules, "sentence_transformers", sentence_transformers)

    embedding = rag._get_embedding_fn(
        rag.DEFAULT_EMBEDDING_MODEL_GENERAL)

    with pytest.raises(RuntimeError, match="truncates at 256"):
        embedding._load()


def test_nomic_inference_uses_same_task_prefix_as_token_validation(
        monkeypatch):
    encoded = []
    monkeypatch.setitem(sys.modules, "chromadb", None)
    monkeypatch.setattr(
        rag, "_model_loader_source",
        lambda *_args, **_kwargs: ("verified/embedding", True))
    monkeypatch.setattr(
        rag._model_artifacts, "model_artifact",
        lambda *_args: SimpleNamespace(trust_remote_code=True))

    class Vector:
        def tolist(self):
            return [1.0, 2.0]

    class FakeSentenceTransformer:
        max_seq_length = 512

        def __init__(self, *_args, **_kwargs):
            pass

        def encode(self, texts, **kwargs):
            encoded.append((texts, kwargs))
            return [Vector() for _ in texts]

    sentence_transformers = ModuleType("sentence_transformers")
    sentence_transformers.SentenceTransformer = FakeSentenceTransformer
    monkeypatch.setitem(
        sys.modules, "sentence_transformers", sentence_transformers)

    document_embedding = rag._get_embedding_fn(
        rag.DEFAULT_EMBEDDING_MODEL_GENERAL, input_type="document")
    query_embedding = rag._get_embedding_fn(
        rag.DEFAULT_EMBEDDING_MODEL_GENERAL, input_type="query")

    assert document_embedding(["body"]) == [[1.0, 2.0]]
    assert query_embedding(["question"]) == [[1.0, 2.0]]
    assert encoded == [
        (["search_document: body"], {"convert_to_numpy": True}),
        (["search_query: question"], {"convert_to_numpy": True}),
    ]


def test_zero_shot_and_reranker_use_verified_local_paths(monkeypatch):
    monkeypatch.setattr(
        rag, "_model_loader_source",
        lambda _model, consumer, **_kwargs: (
            f"verified/{consumer}", True))
    pipeline_call = {}

    def fake_pipeline(*args, **kwargs):
        pipeline_call.update(args=args, kwargs=kwargs)
        return lambda *_args, **_kwargs: {
            "labels": [rag._ZS_LABELS[0]], "scores": [0.9]}

    transformers = ModuleType("transformers")
    transformers.pipeline = fake_pipeline
    monkeypatch.setitem(sys.modules, "transformers", transformers)
    monkeypatch.setattr(rag, "_zeroshot_classifier", None)

    assert rag._zeroshot_classify("procedural doctrine", None) == "case_opinion"
    assert pipeline_call["kwargs"]["model"] == "verified/zero_shot_classifier"
    assert pipeline_call["kwargs"]["tokenizer"] == "verified/zero_shot_classifier"
    assert pipeline_call["kwargs"]["model_kwargs"]["use_safetensors"] is True

    reranker_call = {}

    class FakeFlagReranker:
        def __init__(self, source, **kwargs):
            reranker_call.update(source=source, kwargs=kwargs)

    flag_embedding = ModuleType("FlagEmbedding")
    flag_embedding.FlagReranker = FakeFlagReranker
    monkeypatch.setitem(sys.modules, "FlagEmbedding", flag_embedding)
    monkeypatch.setattr(rag, "_reranker_instances", {})

    rag._get_reranker(rag.DEFAULT_RERANKER_MODEL)

    assert reranker_call == {
        "source": "verified/reranker",
        "kwargs": {"use_fp16": True, "trust_remote_code": False},
    }


def test_docling_configuration_is_local_accurate_and_explicit_english_ocr(
        monkeypatch, tmp_path):
    root = tmp_path / "docling"
    monkeypatch.setattr(
        rag._model_artifacts, "verified_docling_artifact_directory",
        lambda **_kwargs: root)

    class TableFormerMode:
        ACCURATE = "accurate"

    class TableStructureOptions:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class RapidOcrOptions:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    docling = ModuleType("docling")
    datamodel = ModuleType("docling.datamodel")
    options_module = ModuleType("docling.datamodel.pipeline_options")
    options_module.RapidOcrOptions = RapidOcrOptions
    options_module.TableFormerMode = TableFormerMode
    options_module.TableStructureOptions = TableStructureOptions
    monkeypatch.setitem(sys.modules, "docling", docling)
    monkeypatch.setitem(sys.modules, "docling.datamodel", datamodel)
    monkeypatch.setitem(
        sys.modules, "docling.datamodel.pipeline_options", options_module)
    options = SimpleNamespace()

    assert rag._configure_docling_model_artifacts(
        options, include_ocr=True) == root
    assert options.artifacts_path == root
    assert options.table_structure_options.mode == "accurate"
    assert options.ocr_options.backend == "onnxruntime"
    assert options.ocr_options.lang == ["english"]
    assert options.ocr_options.det_model_path.endswith("PP-OCRv6_det_small.onnx")
    assert options.ocr_options.cls_model_path.endswith(
        "ch_ppocr_mobile_v2.0_cls_mobile.onnx")
    assert options.ocr_options.rec_model_path.endswith("PP-OCRv6_rec_small.onnx")


def test_docling_layout_model_ref_is_replaced_with_pinned_commit():
    observed = {}

    class FakeSpec:
        repo_id = "docling-project/docling-layout-heron"

        def model_copy(self, *, update):
            observed.update(update)
            return SimpleNamespace(repo_id=self.repo_id, **update)

    options = SimpleNamespace(
        layout_options=SimpleNamespace(model_spec=FakeSpec()))

    revision = rag._pin_docling_layout_revision(options)

    assert revision == "8f39ad3c0b4c58e9c2d2c84a38465abf757272d8"
    assert observed == {"revision": revision}


def test_model_lock_and_sbom_serialization_are_valid_utf8_json():
    lock_bytes = model_artifacts.MODEL_ARTIFACT_LOCK_PATH.read_bytes()
    lock = json.loads(lock_bytes.decode("utf-8"))
    assert lock["schema_version"] == 2

    payload = model_artifacts.json_bytes(model_artifacts.model_artifact_sbom(
        model_artifacts.load_model_artifacts(),
        model_artifacts.load_package_models(),
    ))
    assert payload.endswith(b"\n")
    assert json.loads(payload.decode("utf-8"))["bomFormat"] == "CycloneDX"
