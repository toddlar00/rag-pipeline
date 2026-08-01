import hashlib
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import artifact_io
import rag
import storage_policy


def _stat_view(result, **changes):
    values = {
        "st_dev": result.st_dev,
        "st_ino": result.st_ino,
        "st_size": result.st_size,
        "st_mtime_ns": result.st_mtime_ns,
        "st_ctime_ns": result.st_ctime_ns,
    }
    values.update(changes)
    return SimpleNamespace(**values)


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


@pytest.mark.parametrize(
    "separator", [chr(0x85), chr(0x2028), chr(0x2029)])
def test_written_chunks_round_trip_through_unicode_line_separators(
        tmp_path, separator):
    """A record the writer emits must always be readable back as one record.

    ``json.dumps(..., ensure_ascii=False)`` leaves U+0085, U+2028, and U+2029
    literal, but JSON does not treat them as line terminators.  Splitting on
    them tore a valid corpus record into unparsable fragments.
    """
    chunks = tmp_path / "chunks.jsonl"
    text = f"Rule 1.5(c){separator}contingent fee disclosure"
    written = [
        {"text": text, "metadata": {"page_start": 542, "note": text}},
        {"text": "second passage", "metadata": {"page_start": 543}},
    ]
    storage_policy.atomic_write_private_jsonl(chunks, written)

    raw = chunks.read_bytes()
    assert separator.encode("utf-8") in raw

    records = rag._parse_index_records_strict(raw, chunks)

    assert [record["text"] for record in records] == [
        text, "second passage"]
    assert records[0]["metadata"]["note"] == text
    assert len(records) == len(written)


def test_jsonl_reader_still_accepts_both_written_line_terminators():
    payload = '{"text":"a","metadata":{}}'
    other = '{"text":"b","metadata":{}}'

    for terminator in ("\n", "\r\n"):
        contents = f"{payload}{terminator}{other}{terminator}"
        records = rag._parse_index_records_strict(
            contents.encode("utf-8"), Path("chunks.jsonl"))
        assert [record["text"] for record in records] == ["a", "b"]


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


def test_snapshot_read_retries_ctime_only_synced_folder_churn(
        monkeypatch, tmp_path):
    artifact = tmp_path / "artifact.jsonl"
    raw = b'{"text":"stable","metadata":{}}\n'
    artifact.write_bytes(raw)
    real_fstat = artifact_io.os.fstat
    fstat_calls = 0
    delays = []

    def churn_once(descriptor):
        nonlocal fstat_calls
        fstat_calls += 1
        result = real_fstat(descriptor)
        if fstat_calls == 2:
            return _stat_view(
                result, st_ctime_ns=result.st_ctime_ns + 1)
        return result

    monkeypatch.setattr(artifact_io.os, "fstat", churn_once)
    monkeypatch.setattr(artifact_io.time, "sleep", delays.append)

    captured, digest, fingerprint = (
        artifact_io._read_index_artifact_snapshot(artifact))

    assert captured == raw
    assert digest == hashlib.sha256(raw).hexdigest()
    assert len(fingerprint) == 5
    assert fstat_calls == 4
    assert delays == [artifact_io._SYNCED_FOLDER_READ_RETRY_DELAYS[0]]


def test_snapshot_read_exhausts_bounded_ctime_retries(
        monkeypatch, tmp_path):
    artifact = tmp_path / "artifact.jsonl"
    artifact.write_bytes(b'{"text":"stable","metadata":{}}\n')
    real_fstat = artifact_io.os.fstat
    fstat_calls = 0
    delays = []

    def always_churning(descriptor):
        nonlocal fstat_calls
        fstat_calls += 1
        result = real_fstat(descriptor)
        if fstat_calls % 2 == 0:
            return _stat_view(
                result, st_ctime_ns=result.st_ctime_ns + fstat_calls)
        return result

    monkeypatch.setattr(artifact_io.os, "fstat", always_churning)
    monkeypatch.setattr(artifact_io.time, "sleep", delays.append)

    with pytest.raises(RuntimeError, match="changed while it was being read"):
        artifact_io._read_index_artifact_snapshot(artifact)

    assert fstat_calls == 2 * (
        len(artifact_io._SYNCED_FOLDER_READ_RETRY_DELAYS) + 1)
    assert delays == list(artifact_io._SYNCED_FOLDER_READ_RETRY_DELAYS)


def test_snapshot_read_rejects_new_bytes_across_ctime_retry(
        monkeypatch, tmp_path):
    artifact = tmp_path / "artifact.jsonl"
    artifact.write_bytes(b'{"text":"aa","metadata":{}}\n')
    baseline = artifact.stat()
    real_fstat = artifact_io.os.fstat
    fstat_calls = 0
    delays = []

    def pinned_metadata(descriptor):
        nonlocal fstat_calls
        fstat_calls += 1
        result = real_fstat(descriptor)
        ctime = baseline.st_ctime_ns + (1 if fstat_calls == 2 else 2)
        if fstat_calls == 1:
            ctime = baseline.st_ctime_ns
        return _stat_view(
            result,
            st_dev=baseline.st_dev,
            st_ino=baseline.st_ino,
            st_size=baseline.st_size,
            st_mtime_ns=baseline.st_mtime_ns,
            st_ctime_ns=ctime,
        )

    def replace_bytes(delay):
        delays.append(delay)
        artifact.write_bytes(b'{"text":"bb","metadata":{}}\n')

    monkeypatch.setattr(artifact_io.os, "fstat", pinned_metadata)
    monkeypatch.setattr(artifact_io.time, "sleep", replace_bytes)

    with pytest.raises(RuntimeError, match="changed while it was being read"):
        artifact_io._read_index_artifact_snapshot(artifact)

    assert fstat_calls == 4
    assert delays == [artifact_io._SYNCED_FOLDER_READ_RETRY_DELAYS[0]]


def test_cached_hash_uses_resilient_snapshot_then_hits_cache(
        monkeypatch, tmp_path):
    artifact = tmp_path / "artifact.bin"
    raw = b"stable cached artifact"
    artifact.write_bytes(raw)
    real_fstat = artifact_io.os.fstat
    fstat_calls = 0
    delays = []

    def churn_during_first_hash(descriptor):
        nonlocal fstat_calls
        fstat_calls += 1
        result = real_fstat(descriptor)
        if fstat_calls == 3:
            return _stat_view(
                result, st_ctime_ns=result.st_ctime_ns + 1)
        return result

    rag._artifact_sha256_cache.clear()
    monkeypatch.setattr(rag, "_ARTIFACT_STAT_HASH_CACHE_SAFE", True)
    monkeypatch.setattr(artifact_io.os, "fstat", churn_during_first_hash)
    monkeypatch.setattr(artifact_io.time, "sleep", delays.append)

    expected = hashlib.sha256(raw).hexdigest()
    assert rag._cached_artifact_sha256(artifact) == expected
    assert rag._cached_artifact_sha256(artifact) == expected

    assert fstat_calls == 7
    assert delays == [artifact_io._SYNCED_FOLDER_READ_RETRY_DELAYS[0]]


def test_unsafe_stat_identity_cannot_return_stale_cached_hash(
        monkeypatch, tmp_path):
    artifact = tmp_path / "artifact.bin"
    artifact.write_bytes(b"AAAA")
    original_stat = artifact.stat()
    rag._artifact_sha256_cache.clear()
    monkeypatch.setattr(rag, "_ARTIFACT_STAT_HASH_CACHE_SAFE", False)

    first = rag._cached_artifact_sha256(artifact)
    artifact.write_bytes(b"BBBB")
    os.utime(
        artifact,
        ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
    )
    if os.name == "nt":
        assert rag._artifact_stat_fingerprint(artifact.stat()) == (
            rag._artifact_stat_fingerprint(original_stat))
    second = rag._cached_artifact_sha256(artifact)

    assert first == hashlib.sha256(b"AAAA").hexdigest()
    assert second == hashlib.sha256(b"BBBB").hexdigest()
    assert second != first


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
