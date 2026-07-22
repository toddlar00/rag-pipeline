import hashlib
import subprocess
import sys

import pytest

import artifact_io
import rag


def test_artifact_io_is_a_lightweight_leaf_module():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import artifact_io; "
                "forbidden = {'rag', 'retrieval_core', 'llm_runtime', "
                "'requests', 'chromadb', 'qdrant_client'}; "
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


def test_strict_parser_uses_current_rag_chunk_identity(monkeypatch, tmp_path):
    observed = []

    def fake_chunk_id(record):
        observed.append(record)
        return f"stable-{len(observed)}"

    monkeypatch.setattr(rag, "_chunk_id", fake_chunk_id)
    raw = b'{"text":"passage","metadata":{"page_start":4}}\n'

    records = rag._parse_index_records_strict(raw, tmp_path / "chunks.jsonl")

    assert records == observed


def test_snapshot_loader_uses_current_rag_parser(monkeypatch, tmp_path):
    chunks = tmp_path / "chunks.jsonl"
    raw = b'{"text":"captured","metadata":{}}\n'
    chunks.write_bytes(raw)
    observed = {}
    sentinel = [{"text": "from patched parser", "metadata": {}}]

    def fake_parser(captured, path):
        observed["raw"] = captured
        observed["path"] = path
        return sentinel

    monkeypatch.setattr(rag, "_parse_index_records_strict", fake_parser)

    records, source_sha256, fingerprint = (
        rag._load_index_snapshot_strict(chunks))

    assert records is sentinel
    assert observed == {"raw": raw, "path": chunks}
    assert source_sha256 == hashlib.sha256(raw).hexdigest()
    assert len(fingerprint) == 5


def test_atomic_writer_uses_current_rag_replace(monkeypatch, tmp_path):
    target = tmp_path / "artifact.json"
    target.write_text('{"old":true}', encoding="utf-8")
    observed = {}

    def fail_replace(source, destination):
        observed["source"] = source
        observed["destination"] = destination
        raise OSError("injected replace failure")

    monkeypatch.setattr(rag.os, "replace", fail_replace)

    with pytest.raises(OSError, match="injected replace failure"):
        rag._atomic_write_json(target, {"new": True})

    assert observed["destination"] == target
    assert target.read_text(encoding="utf-8") == '{"old":true}'
    assert not observed["source"].exists()


def test_completion_writer_uses_current_rag_hash_and_json_hooks(
        monkeypatch, tmp_path):
    output = tmp_path / "book.md"
    output.write_text("# Complete", encoding="utf-8")
    manifest = tmp_path / ".book.complete.json"
    observed = {"hash_paths": []}

    def fake_hash(path):
        observed["hash_paths"].append(path)
        return "injected-output-hash"

    def fake_json_writer(path, payload):
        observed["manifest_path"] = path
        observed["payload"] = payload

    monkeypatch.setattr(rag, "_cached_artifact_sha256", fake_hash)
    monkeypatch.setattr(rag, "_atomic_write_json", fake_json_writer)

    rag._write_artifact_completion(
        manifest,
        stage="unified_export",
        source_sha256="source-hash",
        source_record_count=3,
        parameters={"format": "markdown"},
        outputs={"export": output},
    )

    assert observed["hash_paths"] == [output]
    assert observed["manifest_path"] == manifest
    assert observed["payload"]["schema_version"] == (
        rag.ARTIFACT_COMPLETION_SCHEMA_VERSION)
    assert observed["payload"]["outputs"] == [{
        "role": "export",
        "name": output.name,
        "size": output.stat().st_size,
        "sha256": "injected-output-hash",
    }]


def test_rag_reexports_pure_artifact_helpers():
    assert rag._artifact_stat_fingerprint is (
        artifact_io._artifact_stat_fingerprint)
    assert rag._read_index_artifact_snapshot is (
        artifact_io._read_index_artifact_snapshot)
    assert rag._artifact_parameters_sha256 is (
        artifact_io._artifact_parameters_sha256)
    assert rag._artifact_completion_path is artifact_io._artifact_completion_path
