"""Regressions for deterministic local vector-store resource release."""

import importlib.util
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


_PROJECT_ROOT = Path(__file__).resolve().parents[1]

_LOCK_RELEASE_PROBE = textwrap.dedent(
    r"""
    import json
    from pathlib import Path
    import shutil
    import sys
    import time

    import rag


    backend = sys.argv[1]
    work_dir = Path(sys.argv[2])
    chunks_path = work_dir / f"{backend}_chunks.jsonl"
    db_path = work_dir / f"{backend}_db"
    collection_name = "lock_release"
    embedding_model = "lock-release-test-model"

    record = {
        "text": "Immediate lock release keeps local vector indexes movable.",
        "metadata": {
            "chunk_index": 0,
            "content_type": "author_narrative",
            "context": "Vector database lifecycle",
            "source_file": "lock-release-test.txt",
        },
    }
    chunks_path.write_text(json.dumps(record) + "\n", encoding="utf-8")

    rag._validate_embedding_token_counts = lambda *_args, **_kwargs: None
    rag._embed_texts = lambda texts, *_args, **_kwargs: [
        [1.0, 0.5, 0.25, 0.125] for _ in texts
    ]

    if backend == "chroma":
        rag.index_chunks(
            chunks_path,
            db_path,
            collection_name=collection_name,
            embedding_model=embedding_model,
        )
    elif backend == "qdrant":
        rag.index_chunks_qdrant(
            chunks_path,
            db_path,
            collection_name=collection_name,
            embedding_model=embedding_model,
        )
    else:
        raise AssertionError(f"unexpected backend: {backend}")

    assert rag._index_collection_count(
        db_path,
        collection_name,
        db_backend=backend,
    ) == 1
    response = rag.search_index(
        "local vector lock",
        db_path,
        db_backend=backend,
        n_results=1,
        collection_name=collection_name,
        embedding_model=embedding_model,
        use_reranker=False,
        hybrid=False,
        chunks_path=chunks_path,
    )
    assert len(response.hits) == 1

    if backend == "chroma":
        # Client.close() must synchronously release Chroma's shared System.
        # Check that invariant directly before tolerating any external Windows
        # scanner racing the subsequent filesystem deletion.
        from chromadb.api.shared_system_client import SharedSystemClient
        assert not SharedSystemClient._identifier_to_refcount
        assert not SharedSystemClient._identifier_to_system

    deadline = time.monotonic() + (2.0 if sys.platform == "win32" else 0.0)
    while True:
        try:
            shutil.rmtree(db_path)
            break
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.05)
    assert not db_path.exists()
    print(f"LOCK_RELEASED:{backend}")
    """
)


_RELEASE_MIGRATION_PROBE = textwrap.dedent(
    r"""
    import json
    from pathlib import Path
    import shutil
    import sys
    import time

    import rag


    backend = sys.argv[1]
    work_dir = Path(sys.argv[2])
    chunks_path = work_dir / f"{backend}_migration_chunks.jsonl"
    sibling_path = work_dir / f"{backend}_sibling_chunks.jsonl"
    db_path = work_dir / f"{backend}_migration_db"
    collection_name = "release_migration"
    sibling_name = "release_migration_sibling"
    embedding_model = "release-migration-test-model"

    first = {
        "text": "A schema migration must rebuild the exact collection.",
        "metadata": {
            "chunk_index": 0,
            "content_type": "author_narrative",
            "context": "Release migration generation one",
            "source_file": "release-migration.txt",
        },
    }
    added = {
        "text": "A sibling collection must survive that scoped rebuild.",
        "metadata": {
            "chunk_index": 1,
            "content_type": "author_narrative",
            "context": "Release migration generation two",
            "source_file": "release-migration.txt",
        },
    }
    sibling = {
        "text": "This independent collection remains byte-for-byte logical state.",
        "metadata": {
            "chunk_index": 0,
            "content_type": "author_narrative",
            "context": "Sibling collection sentinel",
            "source_file": "release-migration-sibling.txt",
        },
    }

    def write_records(path, records):
        path.write_text(
            "".join(json.dumps(record) + "\n" for record in records),
            encoding="utf-8",
        )

    write_records(chunks_path, [first])
    write_records(sibling_path, [sibling])

    rag._validate_embedding_token_counts = lambda *_args, **_kwargs: None
    rag._embed_texts = lambda texts, *_args, **_kwargs: [
        [1.0, 0.5, 0.25, 0.125] for _ in texts
    ]

    if backend == "chroma":
        index_fn = rag.index_chunks
    elif backend == "qdrant":
        index_fn = rag.index_chunks_qdrant
    else:
        raise AssertionError(f"unexpected backend: {backend}")

    created = index_fn(
        chunks_path,
        db_path,
        collection_name=collection_name,
        embedding_model=embedding_model,
    )
    sibling_created = index_fn(
        sibling_path,
        db_path,
        collection_name=sibling_name,
        embedding_model=embedding_model,
    )
    assert created.disposition == "created"
    assert sibling_created.disposition == "created"

    manifest_path = rag._index_manifest_path(
        db_path, backend=backend, collection_name=collection_name)
    sibling_manifest_path = rag._index_manifest_path(
        db_path, backend=backend, collection_name=sibling_name)
    sibling_manifest_before = sibling_manifest_path.read_bytes()
    current_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert current_manifest["schema_version"] == rag.INDEX_MANIFEST_SCHEMA_VERSION

    # Reproduce the exact field set emitted by the last integrated release,
    # whose index manifest policy is schema 5.  The current release candidate
    # must rebuild this collection rather than trusting it for a skip.
    main_v5_manifest = {
        key: current_manifest[key]
        for key in (
            "backend",
            "collection",
            "embedding_model",
            "embedding_dimension",
            "model_artifact_lock_sha256",
            "chunk_hashes",
            "source_sha256",
            "source_record_count",
        )
    }
    main_v5_manifest["schema_version"] = 5
    manifest_path.write_text(
        json.dumps(main_v5_manifest, sort_keys=True), encoding="utf-8")

    write_records(chunks_path, [first, added])
    migrated = index_fn(
        chunks_path,
        db_path,
        collection_name=collection_name,
        embedding_model=embedding_model,
    )

    expected_hashes = {
        rag._chunk_id(record): rag._chunk_hash(record)
        for record in (first, added)
    }
    migrated_manifest = json.loads(
        manifest_path.read_text(encoding="utf-8"))
    assert migrated.disposition == "rebuilt"
    assert migrated.changed_records == 2
    assert migrated.unchanged_records == 0
    assert migrated.removed_records == 0
    assert migrated.upserted_records == 2
    assert migrated.physical_count == 2
    assert migrated.committed is True
    assert migrated_manifest["schema_version"] == rag.INDEX_MANIFEST_SCHEMA_VERSION
    assert migrated_manifest["chunk_hashes"] == expected_hashes
    assert rag._index_collection_count(
        db_path, collection_name, db_backend=backend) == 2
    assert rag._index_collection_count(
        db_path, sibling_name, db_backend=backend) == 1
    assert sibling_manifest_path.read_bytes() == sibling_manifest_before
    assert not rag._index_update_marker_path(
        db_path, backend=backend, collection_name=collection_name).exists()

    unchanged = index_fn(
        chunks_path,
        db_path,
        collection_name=collection_name,
        embedding_model=embedding_model,
    )
    assert unchanged.disposition == "unchanged"
    assert unchanged.changed_records == 0
    assert unchanged.unchanged_records == 2
    assert unchanged.upserted_records == 0
    assert unchanged.physical_count == 2
    assert unchanged.committed is True

    response = rag.search_index(
        "sibling collection scoped rebuild",
        db_path,
        db_backend=backend,
        n_results=2,
        collection_name=collection_name,
        embedding_model=embedding_model,
        use_reranker=False,
        hybrid=False,
        chunks_path=chunks_path,
    )
    assert {hit.source_id for hit in response.hits} == set(expected_hashes)
    sibling_response = rag.search_index(
        "independent collection sentinel",
        db_path,
        db_backend=backend,
        n_results=1,
        collection_name=sibling_name,
        embedding_model=embedding_model,
        use_reranker=False,
        hybrid=False,
        chunks_path=sibling_path,
    )
    assert [hit.source_id for hit in sibling_response.hits] == [
        rag._chunk_id(sibling)]

    if backend == "chroma":
        from chromadb.api.shared_system_client import SharedSystemClient
        assert not SharedSystemClient._identifier_to_refcount
        assert not SharedSystemClient._identifier_to_system

    deadline = time.monotonic() + (2.0 if sys.platform == "win32" else 0.0)
    while True:
        try:
            shutil.rmtree(db_path)
            break
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.05)
    assert not db_path.exists()
    print(
        f"MIGRATION_REHEARSED:{backend}:5->"
        f"{rag.INDEX_MANIFEST_SCHEMA_VERSION}"
    )
    """
)


@pytest.mark.parametrize(
    ("backend", "dependency"),
    [
        pytest.param("chroma", "chromadb", id="chroma"),
        pytest.param("qdrant", "qdrant_client", id="qdrant"),
    ],
)
def test_local_vector_store_releases_lock_before_process_exit(
    tmp_path, backend, dependency
):
    if importlib.util.find_spec(dependency) is None:
        pytest.skip(f"optional dependency {dependency!r} is not installed")

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            _LOCK_RELEASE_PROBE,
            backend,
            str(tmp_path),
        ],
        cwd=_PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )

    assert completed.returncode == 0, (
        f"child stdout:\n{completed.stdout}\n"
        f"child stderr:\n{completed.stderr}"
    )
    assert f"LOCK_RELEASED:{backend}" in completed.stdout


@pytest.mark.parametrize(
    ("backend", "dependency"),
    [
        pytest.param("chroma", "chromadb", id="chroma"),
        pytest.param("qdrant", "qdrant_client", id="qdrant"),
    ],
)
def test_local_vector_store_releases_lock_after_v5_release_migration(
    tmp_path, backend, dependency
):
    if importlib.util.find_spec(dependency) is None:
        pytest.skip(f"optional dependency {dependency!r} is not installed")

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            _RELEASE_MIGRATION_PROBE,
            backend,
            str(tmp_path),
        ],
        cwd=_PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )

    assert completed.returncode == 0, (
        f"child stdout:\n{completed.stdout}\n"
        f"child stderr:\n{completed.stderr}"
    )
    assert f"MIGRATION_REHEARSED:{backend}:5->8" in completed.stdout
