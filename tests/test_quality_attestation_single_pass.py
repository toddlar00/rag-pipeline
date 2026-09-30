"""Characterize quality validation's record-attestation block.

``validate_quality_report(records=...)`` checks every non-table-child
record's stored source-fidelity attestation in index order, then the
aggregate output-token count, and only then the output attestation root.
These tests pin the observable outcome of that block -- whether it passes,
or the exact exception type and message -- against a frozen copy of the
original three-pass implementation. They cover which defect wins when
several coexist and when a later record would make attestation itself fail.

The index binding tests pin the completion-input and registry contracts
that quality-bound index readers observe.
"""

import copy
from collections import Counter
import hashlib
import json
from pathlib import Path
import random

import pytest

import quality_core
import rag
import retrieval_core
import source_fidelity_core
from test_quality_core import (
    _build, _record, _source_item, _validation_kwargs)
from test_source_snapshot_integrity import _quality_fixture


STALE = ("ValueError", "corpus quality report output attestation is stale")
MISMATCH = (
    "ValueError",
    "corpus quality report output fidelity does not match records")
NORMALIZATION = (
    "ValueError",
    "corpus quality report normalization does not match records")
PASSED = ("passed", "")

_TEXTS = (
    "Duty of care owed by the defendant.",
    "Breach requires unreasonable conduct here.",
    "Causation links the conduct and the harm.",
    "Damages compensate the injured plaintiff.",
    "Defenses may limit the recovery sought.",
)
_FUZZ_TRIALS = 300


class _AttestationBlockPassed(Exception):
    """Raised by the first records check that follows the block."""


def _frozen_output_attestation_root_sha256(records):
    """Pre-change ``source_fidelity_core.output_attestation_root_sha256``."""
    evidence = [
        {"record_index": index,
         "attestation": source_fidelity_core.record_attestation(record)}
        for index, record in enumerate(records)
        if not isinstance(record.get("metadata"), dict)
        or record["metadata"].get("retrieval_role") != "table_child"
    ]
    return hashlib.sha256(
        source_fidelity_core._canonical_json(evidence)).hexdigest()


def _frozen_output_lexical_count(records):
    """Pre-change ``source_fidelity_core.output_lexical_count``."""
    return sum(
        len(source_fidelity_core.lexical_tokens(
            str(record.get("text") or "")))
        for record in records
        if not isinstance(record.get("metadata"), dict)
        or record["metadata"].get("retrieval_role") != "table_child"
    )


def _frozen_attestation_block(records, fidelity_summary):
    """Pre-change attestation block of ``validate_quality_report``."""
    for record in records:
        metadata = record.get("metadata")
        if (not isinstance(metadata, dict)
                or metadata.get("retrieval_role") == "table_child"):
            continue
        if metadata.get("source_fidelity") \
                != source_fidelity_core.record_attestation(record):
            raise ValueError(
                "corpus quality report output attestation is stale")
    expected_output_tokens = _frozen_output_lexical_count(records)
    if (fidelity_summary.get("output_tokens") != expected_output_tokens
            or fidelity_summary.get("covered_output_tokens")
            != expected_output_tokens
            or fidelity_summary.get("output_attestation_root_sha256")
            != _frozen_output_attestation_root_sha256(records)):
        raise ValueError(
            "corpus quality report output fidelity does not match records")


def _outcome(function, *args):
    try:
        value = function(*args)
    except _AttestationBlockPassed:
        return PASSED
    except Exception as exc:
        return (type(exc).__name__, str(exc))
    return ("ok", value)


def _golden(outcome):
    """Keep full messages only where this repository owns the text."""
    if outcome[0] in {"ValueError", PASSED[0]}:
        return outcome
    return (outcome[0],)


def _observed(monkeypatch, report, kwargs, records):
    def stop_after_attestation_block(*_args, **_kwargs):
        raise _AttestationBlockPassed

    with monkeypatch.context() as patch:
        patch.setattr(
            retrieval_core, "_retrieval_linkage_summary",
            stop_after_attestation_block)
        return _outcome(
            lambda: quality_core.validate_quality_report(
                report, **kwargs, records=records))


def _expected(report, records):
    normalization = _outcome(quality_core._normalization_issues, records)
    if normalization[0] != "ok":
        return normalization
    if normalization[1] != report["normalization"]:
        return NORMALIZATION
    outcome = _outcome(
        _frozen_attestation_block, records,
        report["source_lineage"]["fidelity"])
    return PASSED if outcome[0] == "ok" else outcome


@pytest.fixture(scope="module")
def base():
    document = {"texts": [
        _source_item(f"#/texts/{index}", 1, text=text)
        for index, text in enumerate(_TEXTS)]}
    records = [
        _record(index, f"#/texts/{index}", 1, text=text)
        for index, text in enumerate(_TEXTS)]
    report = _build(records, document)
    kwargs = _validation_kwargs(record_count=len(records))
    assert report["status"] == "pass"
    return records, report, kwargs


def _copies(base):
    records, report, kwargs = base
    return copy.deepcopy(records), copy.deepcopy(report), kwargs


def _rebind_fidelity(report, **changes):
    fidelity = report["source_lineage"]["fidelity"]
    fidelity.update(changes)
    core = {key: value for key, value in fidelity.items()
            if key != "evidence_sha256"}
    fidelity["evidence_sha256"] = hashlib.sha256(
        source_fidelity_core._canonical_json(core)).hexdigest()
    return report


def _rebind_to_records(report, records):
    count = _frozen_output_lexical_count(records)
    return _rebind_fidelity(
        report, output_tokens=count, covered_output_tokens=count,
        output_attestation_root_sha256=(
            _frozen_output_attestation_root_sha256(records)))


def _shift_count(report, delta):
    count = report["source_lineage"]["fidelity"]["output_tokens"] + delta
    return _rebind_fidelity(
        report, output_tokens=count, covered_output_tokens=count)


def _forge_root(report):
    return _rebind_fidelity(
        report, output_attestation_root_sha256="0" * 64)


def _stale(record):
    record["text"] += " altered"


def _reattest(record, text):
    record["text"] = text
    source_fidelity_core.attach_record_attestations([record])


def _extra_token(record):
    _reattest(record, record["text"] + " again")


def _same_count_other_words(record):
    words = record["text"].split(" ")
    _reattest(record, " ".join(["Other", *words[1:]]))


def _unhashable_transform(record):
    record["metadata"]["source_items"][0]["transform"] = ["table"]


def _surrogate_text(record):
    # A lone surrogate is not a lexical token, so the count is unchanged,
    # but the attestation's UTF-8 text digest cannot be computed.
    record["text"] = record["text"].replace(" ", " \ud800 ", 1)


def _opaque_surrogate_ref(record):
    record["metadata"]["source_items"] = [
        {"ref": "#/tables/\ud800", "transform": "table"}]
    source_fidelity_core.attach_record_attestations([record])


def _table_child(record):
    record["metadata"]["retrieval_role"] = "table_child"


def _without_metadata(record):
    record["metadata"] = None


def _replacement_character(record):
    record["text"] += " �"


def _apply(*mutations, summary=None):
    def mutate(records, report):
        for index, mutation in mutations:
            mutation(records[index])
        return report if summary is None else summary(report, records)
    return mutate


_CASES = [
    pytest.param(_apply(), PASSED, id="valid"),
    pytest.param(_apply((2, _stale)), STALE, id="stale_at_index_2"),
    pytest.param(
        _apply((1, _stale), summary=lambda report, _: _shift_count(
            _forge_root(report), 5)),
        STALE, id="stale_beats_count_and_root_mismatch"),
    pytest.param(
        _apply((1, _stale), (3, _unhashable_transform)),
        STALE, id="stale_then_unhashable_transform"),
    pytest.param(
        _apply((1, _unhashable_transform), (3, _stale)),
        ("TypeError",), id="unhashable_transform_then_stale"),
    pytest.param(
        _apply((1, _stale), (3, _surrogate_text)),
        STALE, id="stale_then_lone_surrogate_text"),
    pytest.param(
        _apply((1, _surrogate_text), (3, _stale)),
        ("UnicodeEncodeError",), id="lone_surrogate_text_then_stale"),
    pytest.param(
        _apply((1, _extra_token), (3, _opaque_surrogate_ref)),
        MISMATCH, id="count_mismatch_then_lone_surrogate_opaque_ref"),
    pytest.param(
        _apply((3, _opaque_surrogate_ref)),
        ("UnicodeEncodeError",),
        id="matching_count_then_lone_surrogate_opaque_ref"),
    pytest.param(
        _apply((2, _same_count_other_words)),
        MISMATCH, id="root_only_record_mismatch"),
    pytest.param(
        _apply(summary=lambda report, _: _forge_root(report)),
        MISMATCH, id="forged_root_only"),
    pytest.param(
        _apply(summary=lambda report, _: _shift_count(report, 1)),
        MISMATCH, id="forged_count_only"),
    pytest.param(
        _apply((2, _table_child), (2, _stale)),
        MISMATCH, id="table_child_skipped_but_excluded_from_count"),
    pytest.param(
        _apply((2, _table_child), (2, _stale), summary=_rebind_to_records),
        PASSED, id="table_child_with_rebound_summary"),
    pytest.param(
        _apply((2, _without_metadata)),
        PASSED, id="non_dict_metadata_still_attested"),
    pytest.param(
        _apply((1, _stale), (3, _without_metadata), (3, _surrogate_text)),
        STALE, id="stale_then_non_dict_lone_surrogate_text"),
    pytest.param(
        _apply((2, _without_metadata), (2, _surrogate_text),
               (2, _stale)),
        MISMATCH, id="non_dict_lone_surrogate_count_mismatch"),
    pytest.param(
        _apply((2, _without_metadata), (2, _surrogate_text)),
        ("UnicodeEncodeError",), id="non_dict_lone_surrogate_same_count"),
    pytest.param(
        _apply((1, _stale), (3, _replacement_character)),
        NORMALIZATION, id="normalization_precedes_stale"),
]


@pytest.mark.parametrize("mutate, golden", _CASES)
def test_attestation_block_outcome_matches_frozen_block(
        base, monkeypatch, mutate, golden):
    records, report, kwargs = _copies(base)
    report = mutate(records, report)

    observed = _observed(monkeypatch, report, kwargs, records)

    assert observed == _expected(report, records)
    assert _golden(observed) == golden


def test_valid_records_complete_quality_validation(base):
    records, report, kwargs = _copies(base)

    assert quality_core.validate_quality_report(
        report, **kwargs, records=records) is report


def _fuzz_record(record, rng):
    roll = rng.random()
    if roll < 0.70:
        return
    mutation = rng.choice((
        _stale, _extra_token, _same_count_other_words,
        _unhashable_transform, _surrogate_text, _opaque_surrogate_ref,
        _table_child, _without_metadata, _replacement_character,
    ))
    mutation(record)
    if mutation in {_table_child, _without_metadata} and rng.random() < 0.5:
        rng.choice((_stale, _surrogate_text))(record)


def _fuzz_summary(report, records, rng):
    roll = rng.random()
    if roll < 0.35:
        return report
    if roll < 0.70:
        try:
            return _rebind_to_records(report, records)
        except Exception:
            return report
    if roll < 0.85:
        return _shift_count(report, rng.choice((-2, -1, 1, 3)))
    return _forge_root(report)


def test_attestation_block_matches_frozen_block_on_seeded_fuzz(
        base, monkeypatch):
    rng = random.Random(20260930)
    seen = Counter()
    for _ in range(_FUZZ_TRIALS):
        records, report, kwargs = _copies(base)
        for record in records:
            _fuzz_record(record, rng)
        report = _fuzz_summary(report, records, rng)

        expected = _expected(report, records)
        assert _observed(monkeypatch, report, kwargs, records) == expected
        seen[_golden(expected)] += 1

    # The seeded corpus must keep exercising every precedence outcome.
    for outcome in (
            PASSED, STALE, MISMATCH, NORMALIZATION,
            ("TypeError",), ("UnicodeEncodeError",)):
        assert seen[outcome] >= 5, (outcome, seen)


def _published_quality_fixture(tmp_path):
    (document, chunks, parameters, inputs, mapping,
     document_sha256, document_size) = _quality_fixture(tmp_path)
    rag._publish_corpus_quality_report(
        document,
        chunks,
        parameters=parameters,
        structural_ranges=set(),
        document_snapshot=(mapping, document_sha256, document_size),
        chunk_inputs=inputs,
    )
    raw = chunks.read_bytes()
    records = rag._parse_index_records_strict(raw, chunks)
    return chunks, inputs, raw, records


def test_chunk_completion_inputs_keep_their_two_value_contract(tmp_path):
    chunks, inputs, raw, records = _published_quality_fixture(tmp_path)
    completion = json.loads(
        rag._artifact_completion_path(chunks, stage="chunking").read_text(
            encoding="utf-8"))

    result = rag._load_index_chunk_completion_inputs(
        chunks, chunks_sha256=hashlib.sha256(raw).hexdigest(),
        chunks_size=len(raw), records=records)

    assert result == (inputs, completion["parameters_sha256"])


@pytest.mark.parametrize("supply_inputs", [False, True])
def test_quality_binding_validates_against_the_bound_registry(
        monkeypatch, tmp_path, supply_inputs):
    chunks, inputs, raw, records = _published_quality_fixture(tmp_path)
    registry = json.loads(
        rag._source_oracle_registry_path(chunks).read_text(encoding="utf-8"))
    report_path = rag._quality_core.quality_report_path(chunks)
    real_parse = rag._quality_core.parse_quality_report_bytes
    seen = []

    def record_registry(raw_report, **kwargs):
        seen.append(kwargs["source_oracle_registry"])
        return real_parse(raw_report, **kwargs)

    monkeypatch.setattr(
        rag._quality_core, "parse_quality_report_bytes", record_registry)
    schema_version, report_sha256, payload = (
        rag._validated_quality_report_binding(
            chunks, records, hashlib.sha256(raw).hexdigest(), len(raw),
            input_bindings=inputs if supply_inputs else None))

    assert seen == [registry]
    assert schema_version == rag._quality_core.QUALITY_REPORT_SCHEMA_VERSION
    assert report_sha256 == hashlib.sha256(
        report_path.read_bytes()).hexdigest()
    assert payload == json.loads(report_path.read_text(encoding="utf-8"))


def _count_registry_loads(monkeypatch):
    real_load = rag._load_source_oracle_registry
    loaded = []

    def count_load(chunks_path, inputs):
        registry = real_load(chunks_path, inputs)
        loaded.append(registry)
        return registry

    monkeypatch.setattr(rag, "_load_source_oracle_registry", count_load)
    return loaded


@pytest.mark.parametrize("supply_inputs", [False, True])
def test_quality_binding_loads_the_registry_once(
        monkeypatch, tmp_path, supply_inputs):
    chunks, inputs, raw, records = _published_quality_fixture(tmp_path)
    registry_path = rag._source_oracle_registry_path(chunks)
    real_read = rag._read_index_artifact_snapshot
    registry_reads = []

    def count_registry_reads(path, **kwargs):
        if Path(path) == registry_path:
            registry_reads.append(path)
        return real_read(path, **kwargs)

    monkeypatch.setattr(
        rag, "_read_index_artifact_snapshot", count_registry_reads)
    loaded = _count_registry_loads(monkeypatch)
    real_parse = rag._quality_core.parse_quality_report_bytes
    validated = []

    def record_registry(raw_report, **kwargs):
        validated.append(kwargs["source_oracle_registry"])
        return real_parse(raw_report, **kwargs)

    monkeypatch.setattr(
        rag._quality_core, "parse_quality_report_bytes", record_registry)
    rag._validated_quality_report_binding(
        chunks, records, hashlib.sha256(raw).hexdigest(), len(raw),
        input_bindings=inputs if supply_inputs else None)

    assert len(loaded) == 1
    assert len(registry_reads) == 1
    assert len(validated) == 1 and validated[0] is loaded[0]


def test_chunk_completion_inputs_still_load_and_validate_the_registry(
        monkeypatch, tmp_path):
    chunks, inputs, raw, records = _published_quality_fixture(tmp_path)
    loaded = _count_registry_loads(monkeypatch)

    result = rag._load_index_chunk_completion_inputs(
        chunks, chunks_sha256=hashlib.sha256(raw).hexdigest(),
        chunks_size=len(raw), records=records)

    assert result[0] == inputs
    assert len(loaded) == 1


def test_attestation_root_reuses_only_supplied_attestations(base):
    records, _report, _kwargs = _copies(base)
    records[1]["metadata"]["retrieval_role"] = "table_child"
    records[3]["metadata"] = None
    expected = _frozen_output_attestation_root_sha256(records)
    supplied = {
        index: source_fidelity_core.record_attestation(record)
        for index, record in enumerate(records)
        if index in {0, 2, 4}
    }

    assert source_fidelity_core.output_attestation_root_sha256(
        records) == expected
    assert source_fidelity_core.output_attestation_root_sha256(
        records, precomputed=supplied) == expected
    assert source_fidelity_core.output_attestation_root_sha256(
        records, precomputed={}) == expected
    assert source_fidelity_core.output_lexical_count(records) == (
        _frozen_output_lexical_count(records))
    with pytest.raises(TypeError):
        source_fidelity_core.output_attestation_root_sha256(
            records, supplied)


def test_quality_binding_rejects_a_registry_detached_from_its_completion(
        tmp_path):
    chunks, _inputs, _raw, _records = _published_quality_fixture(tmp_path)
    registry_path = rag._source_oracle_registry_path(chunks)
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry["evidence_sha256"] = "0" * 64
    rag._atomic_write_json(registry_path, registry)

    with pytest.raises(
            ValueError, match="source oracle registry file binding changed"):
        rag._load_index_snapshot_with_quality(chunks)
