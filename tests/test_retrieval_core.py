import subprocess
import sys

import rag
import retrieval_core


def test_rag_reexports_retrieval_domain_models():
    assert rag.SearchHit is retrieval_core.SearchHit
    assert rag.GroundedSource is retrieval_core.GroundedSource
    assert rag.GroundedAnswer is retrieval_core.GroundedAnswer
    assert rag.SearchResponse is retrieval_core.SearchResponse


def test_retrieval_core_is_a_lightweight_leaf_module():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import retrieval_core; "
                "forbidden = {'rag', 'llm_runtime', 'requests', 'chromadb', "
                "'qdrant_client'}; "
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


def test_search_hit_source_id_uses_current_rag_chunk_id(monkeypatch):
    observed = {}

    def fake_chunk_id(record):
        observed["record"] = record
        return "compatibility-id"

    monkeypatch.setattr(rag, "_chunk_id", fake_chunk_id)
    hit = rag.SearchHit(
        text="A retrieved passage.",
        metadata={"source_file": "book.pdf", "page_start": 4},
        score=0.9,
    )

    assert rag._search_hit_source_id(hit) == "compatibility-id"
    assert observed["record"] == {
        "text": hit.text,
        "metadata": hit.metadata,
    }


def test_grounded_sources_uses_current_rag_source_id_resolver(monkeypatch):
    hit = rag.SearchHit(
        text="A retrieved passage.",
        metadata={"forced_id": "patched-source"},
        score=0.9,
    )
    response = rag.SearchResponse(
        hits=[hit],
        backend="chroma",
        requested_mode="vector",
        effective_mode="vector",
        reranker_applied=False,
    )
    monkeypatch.setattr(
        rag, "_search_hit_source_id",
        lambda current_hit: current_hit.metadata["forced_id"],
    )

    sources = rag._grounded_sources(response)

    assert [source.source_id for source in sources] == ["patched-source"]
    assert hit.source_id == "patched-source"
