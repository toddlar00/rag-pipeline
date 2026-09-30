import hashlib
import json
import math
import os
import random
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


_CHUNKS_PATH = Path("chunks.jsonl")
_NONFINITE_LITERALS = (
    "NaN", "Infinity", "-Infinity", "1e999", "-1e999", "1E400",
    "1" * 400 + ".0",
)


def _text_identity(record):
    return record["text"]


def _parse_strict(payload):
    return artifact_io._parse_index_records_strict(
        payload.encode("utf-8"), _CHUNKS_PATH, chunk_id_fn=_text_identity)


def _nonfinite_message(line_number, field_path):
    return (
        f"Invalid chunk at {_CHUNKS_PATH}:{line_number}: "
        f"'{field_path}' must contain finite numbers"
    )


@pytest.mark.parametrize(
    "literal", _NONFINITE_LITERALS,
    ids=["nan", "inf", "-inf", "1e999", "-1e999", "1E400", "long-decimal"])
@pytest.mark.parametrize(
    ("template", "field_path"),
    [
        ('{"a":@}', "metadata.a"),
        ('{"a":{"b":@}}', "metadata.a.b"),
        ('{"a":[0,@]}', "metadata.a[1]"),
        ('{"a":[{"b":@}]}', "metadata.a[0].b"),
    ],
)
def test_strict_parser_reports_exact_nonfinite_metadata_path(
        literal, template, field_path):
    payload = (
        '{"text":"first","metadata":{"finite":1.5}}\n'
        '{"text":"second","metadata":'
        + template.replace("@", literal) + "}\n"
    )

    with pytest.raises(ValueError) as caught:
        _parse_strict(payload)

    assert str(caught.value) == _nonfinite_message(2, field_path)
    assert caught.value.__cause__ is None


@pytest.mark.parametrize(
    ("metadata", "field_path"),
    [
        ('{"a":NaN,"b":Infinity}', "metadata.b"),
        ('{"a":[NaN,-Infinity]}', "metadata.a[1]"),
        ('{"a":NaN,"b":{"c":1e999}}', "metadata.b.c"),
        ('{"a":{"c":NaN},"b":[1,2]}', "metadata.a.c"),
        ('{"a":[{"x":NaN},{"y":[Infinity,3]}],"b":"s"}',
         "metadata.a[1].y[0]"),
        ('{"x.y[0]":NaN}', "metadata.x.y[0]"),
    ],
)
def test_strict_parser_reports_the_last_pushed_nonfinite_path(
        metadata, field_path):
    """The reported path follows the parser's LIFO walk, not source order."""
    payload = '{"text":"t","metadata":' + metadata + "}\n"

    with pytest.raises(ValueError) as caught:
        _parse_strict(payload)

    assert str(caught.value) == _nonfinite_message(1, field_path)


def test_strict_parser_accepts_nonfinite_values_outside_metadata():
    payload = (
        '{"text":"t","metadata":{"n":1},"score":NaN,'
        '"extra":[Infinity,{"x":-1e999}]}\n'
    )

    [record] = _parse_strict(payload)

    assert math.isnan(record["score"])
    assert record["extra"][0] == math.inf
    assert record["extra"][1] == {"x": -math.inf}
    assert record["metadata"] == {"n": 1}


@pytest.mark.parametrize(
    ("payload", "metadata"),
    [
        ('{"text":"t","metadata":{"x":NaN,"x":1}}\n', {"x": 1}),
        ('{"text":"t","metadata":{"x":NaN},"metadata":{"y":2}}\n',
         {"y": 2}),
    ],
)
def test_strict_parser_accepts_nonfinite_value_replaced_by_duplicate_key(
        payload, metadata):
    [record] = _parse_strict(payload)

    assert record["metadata"] == metadata


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ('{"text":"a","metadata":{"x":NaN}}\nnope\n',
         _nonfinite_message(1, "metadata.x")),
        ('nope\n{"text":"a","metadata":{"x":NaN}}\n',
         f"Invalid JSON in chunks file {_CHUNKS_PATH}:1: Expecting value"),
        ('{"metadata":{"x":NaN}}\n',
         f"Invalid chunk at {_CHUNKS_PATH}:1: "
         "'text' must be a non-empty string"),
        ('{"text":"a","metadata":{}}\n{"text":"a","metadata":{"x":NaN}}\n',
         _nonfinite_message(2, "metadata.x")),
        ('\n \n{"text":"a","metadata":{"x":Infinity}}\n',
         _nonfinite_message(3, "metadata.x")),
    ],
)
def test_strict_parser_nonfinite_check_keeps_line_precedence(
        payload, message):
    with pytest.raises(ValueError) as caught:
        _parse_strict(payload)

    assert str(caught.value) == message


def test_strict_parser_accepts_finite_float_edges_unchanged():
    huge_integer = "1" * 400
    payload = (
        '{"text":"t","metadata":{"big":1e308,"negative_zero":-0.0,'
        '"subnormal":5e-324,"underflow":1e-400,"integer":'
        + huge_integer + "}}\n"
    )

    [record] = _parse_strict(payload)
    metadata = record["metadata"]

    assert metadata["big"] == 1e308
    assert metadata["negative_zero"] == 0.0
    assert math.copysign(1.0, metadata["negative_zero"]) == -1.0
    assert metadata["subnormal"] == 5e-324
    assert metadata["underflow"] == 0.0
    assert isinstance(metadata["underflow"], float)
    assert metadata["integer"] == int(huge_integer)


def test_strict_parser_keeps_stdlib_bom_error_for_later_lines():
    payload = (
        '﻿{"text":"a","metadata":{}}\n'
        '﻿{"text":"b","metadata":{}}\n'
    )

    with pytest.raises(ValueError) as caught:
        _parse_strict(payload)

    assert str(caught.value) == (
        f"Invalid JSON in chunks file {_CHUNKS_PATH}:2: "
        "Unexpected UTF-8 BOM (decode using utf-8-sig)"
    )


def test_strict_parser_checks_latin1_fallback_metadata():
    raw = '{"text":"caf\xe9","metadata":{"a":[-Infinity]}}\n'.encode(
        "latin-1")

    with pytest.raises(ValueError) as caught:
        artifact_io._parse_index_records_strict(
            raw, _CHUNKS_PATH, chunk_id_fn=_text_identity)

    assert str(caught.value) == _nonfinite_message(1, "metadata.a[0]")


def test_strict_parser_fails_closed_when_scan_and_path_walk_disagree(
        monkeypatch):
    monkeypatch.setattr(
        artifact_io, "_contains_nonfinite_float", lambda value: True)

    with pytest.raises(AssertionError, match="unreachable non-finite"):
        _parse_strict('{"text":"t","metadata":{"x":1.5}}\n')


def _parse_index_records_strict_3aede7d(raw, path, *, chunk_id_fn):
    """Frozen copy of the strict parser at 3aede7d (differential oracle)."""
    last_unicode_error = None
    for encoding in ("utf-8-sig", "latin-1"):
        try:
            contents = raw.decode(encoding)
        except UnicodeDecodeError as exc:
            last_unicode_error = exc
            continue

        records = []
        for line_number, line in enumerate(
                storage_policy.jsonl_lines(contents), 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid JSON in chunks file {path}:{line_number}: "
                    f"{exc.msg}"
                ) from exc
            if not isinstance(record, dict):
                raise ValueError(
                    f"Invalid chunk at {path}:{line_number}: "
                    "record must be a JSON object"
                )
            if (not isinstance(record.get("text"), str)
                    or not record["text"].strip()):
                raise ValueError(
                    f"Invalid chunk at {path}:{line_number}: "
                    "'text' must be a non-empty string"
                )
            if not isinstance(record.get("metadata"), dict):
                raise ValueError(
                    f"Invalid chunk at {path}:{line_number}: "
                    "'metadata' must be a JSON object"
                )
            pending = [("metadata", record["metadata"])]
            while pending:
                field_path, value = pending.pop()
                if isinstance(value, float) and not math.isfinite(value):
                    raise ValueError(
                        f"Invalid chunk at {path}:{line_number}: "
                        f"'{field_path}' must contain finite numbers"
                    )
                if isinstance(value, dict):
                    pending.extend(
                        (f"{field_path}.{key}", child)
                        for key, child in value.items()
                    )
                elif isinstance(value, list):
                    pending.extend(
                        (f"{field_path}[{index}]", child)
                        for index, child in enumerate(value)
                    )
            records.append(record)

        if not records:
            raise ValueError(f"Chunks file contains no records: {path}")
        stable_ids = [chunk_id_fn(record) for record in records]
        if len(set(stable_ids)) != len(stable_ids):
            raise ValueError(
                f"Chunks file contains duplicate stable chunk IDs: {path}")
        return records

    raise ValueError(
        f"Could not decode chunks file: {path}") from last_unicode_error


_DIFFERENTIAL_NUMBERS = _NONFINITE_LITERALS + (
    "1e308", "-0.0", "5e-324", "1e-400", "0.5", "7", "-3", "2.5e+10",
    "1" * 400,
)
_DIFFERENTIAL_SCALARS = ('"s"', "true", "false", "null")


def _random_json_value(rng, depth):
    roll = rng.random()
    if depth > 3 or roll < 0.45:
        if rng.random() < 0.6:
            return rng.choice(_DIFFERENTIAL_NUMBERS)
        return rng.choice(_DIFFERENTIAL_SCALARS)
    size = rng.randint(0, 4)
    if roll < 0.75:
        keys = [rng.choice("abcxyz") for _ in range(size)]
        return "{" + ",".join(
            f'"{key}":{_random_json_value(rng, depth + 1)}' for key in keys
        ) + "}"
    return "[" + ",".join(
        _random_json_value(rng, depth + 1) for _ in range(size)) + "]"


def _random_chunk_line(rng, index):
    roll = rng.random()
    special = (
        "nope", "[]", '{"metadata":{}}', '{"text":"x","metadata":[]}',
        '﻿{"text":"b","metadata":{}}', '{"text":"b","metadata":{}}',
        "",
    )
    if roll < 0.08:
        return special[int(roll * 100) % len(special)]
    extra = ""
    if rng.random() < 0.3:
        extra = f',"score":{rng.choice(_DIFFERENTIAL_NUMBERS)}'
    text_index = 0 if rng.random() < 0.2 else index
    return (
        f'{{"text":"t{text_index}","metadata":{{"chunk_index":{index},'
        f'"m":{_random_json_value(rng, 0)}}}{extra}}}'
    )


def _strict_parse_outcome(parser, raw):
    try:
        records = parser(raw, _CHUNKS_PATH, chunk_id_fn=_text_identity)
    except (ValueError, TypeError) as exc:
        cause = exc.__cause__
        return ("error", type(exc), str(exc), type(cause), str(cause))
    return ("records", repr(records))


def _strict_parse_outcome_kind(outcome):
    if outcome[0] == "records":
        return "records"
    for marker in (
            "finite numbers", "Invalid JSON", "non-empty string",
            "duplicate stable"):
        if marker in outcome[2]:
            return marker
    return outcome[2]


def test_strict_parser_matches_frozen_oracle_on_generated_corpora():
    rng = random.Random(20260930)
    outcomes = set()
    for _ in range(400):
        lines = [
            _random_chunk_line(rng, index)
            for index in range(rng.randint(1, 6))
        ]
        text = "\n".join(lines) + "\n"
        payloads = [text.encode("utf-8")]
        if all(ord(character) < 256 for character in text):
            payloads.append(
                text.encode("latin-1").replace(b"t0", b"t\xe90"))
        for raw in payloads:
            expected = _strict_parse_outcome(
                _parse_index_records_strict_3aede7d, raw)
            actual = _strict_parse_outcome(
                artifact_io._parse_index_records_strict, raw)
            assert actual == expected, raw
            outcomes.add(_strict_parse_outcome_kind(expected))

    assert {
        "records", "finite numbers", "Invalid JSON", "non-empty string",
        "duplicate stable",
    } <= outcomes


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
