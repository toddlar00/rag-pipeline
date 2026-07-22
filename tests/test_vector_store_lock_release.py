"""Windows regressions for deterministic local vector-store lock release."""

import importlib.util
import os
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

    # This must happen before the process exits, without a retry or delay.
    # Windows rejects it if SQLite/Qdrant still holds an open file handle.
    shutil.rmtree(db_path)
    assert not db_path.exists()
    print(f"LOCK_RELEASED:{backend}")
    """
)


@pytest.mark.skipif(
    os.name != "nt",
    reason="Windows file locking is required for this regression",
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
