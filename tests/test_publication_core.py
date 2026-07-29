import copy
import json
import subprocess
import sys

import pytest

import publication_core


def _gate_evidence() -> dict[str, dict]:
    return {
        name: {
            "policy_version": 1,
            "observed": index,
            "required": index,
            "artifact_sha256": f"{index + 1:064x}",
        }
        for index, name in enumerate(publication_core.PUBLICATION_GATE_NAMES)
    }


def _receipt() -> dict:
    return publication_core.build_publication_receipt(_gate_evidence())


def test_publication_core_is_a_standard_library_only_leaf_module():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import publication_core; "
                "forbidden = {'rag', 'quality_core', 'markdown_validation', "
                "'artifact_io', 'requests', 'numpy', 'torch'}; "
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


def test_builder_emits_the_exact_ordered_pass_only_gate_contract():
    evidence = _gate_evidence()
    receipt = publication_core.build_publication_receipt(dict(
        reversed(list(evidence.items()))))

    assert set(receipt) == {
        "schema_version",
        "kind",
        "status",
        "gates",
        "evidence_root_sha256",
    }
    assert receipt["schema_version"] == 1
    assert receipt["kind"] == "publication_receipt"
    assert receipt["status"] == "pass"
    assert [gate["name"] for gate in receipt["gates"]] == list(
        publication_core.PUBLICATION_GATE_NAMES)
    assert {gate["status"] for gate in receipt["gates"]} == {"pass"}
    assert all(
        gate["evidence_sha256"]
        == publication_core.canonical_evidence_sha256(gate["evidence"])
        for gate in receipt["gates"]
    )
    assert publication_core.validate_publication_receipt(receipt) is receipt
    assert publication_core.publication_receipt_is_complete(receipt)


def test_canonical_hashes_and_serialization_ignore_mapping_insertion_order():
    first = _gate_evidence()
    second = {
        name: dict(reversed(list(evidence.items())))
        for name, evidence in reversed(list(first.items()))
    }

    first_receipt = publication_core.build_publication_receipt(first)
    second_receipt = publication_core.build_publication_receipt(second)

    assert first_receipt == second_receipt
    assert publication_core.serialize_publication_receipt(
        first_receipt) == publication_core.serialize_publication_receipt(
            second_receipt)
    canonical_digest = publication_core.canonical_evidence_sha256({
        "alpha": 1,
        "beta": [True, None, "café"],
    })
    assert canonical_digest == publication_core.canonical_evidence_sha256({
        "beta": [True, None, "café"],
        "alpha": 1,
    })
    assert canonical_digest == (
        "116061c0fa889eba899d2c6c7747c579d5bd1b0f486b63e8e16634f260cddafa"
    )
    assert first_receipt["evidence_root_sha256"] == (
        "3a9b876178db3195f438d9abb2fdc8e59a15296bb28dc81a5f5e76c75c97a326"
    )


def test_builder_detaches_caller_evidence_and_root_binds_gate_identity():
    evidence = _gate_evidence()
    receipt = publication_core.build_publication_receipt(evidence)
    initial_raw = publication_core.serialize_publication_receipt(receipt)

    evidence["source_completeness"]["observed"] = 999
    assert publication_core.serialize_publication_receipt(receipt) == initial_raw

    swapped = _gate_evidence()
    first, second = publication_core.PUBLICATION_GATE_NAMES[:2]
    swapped[first], swapped[second] = swapped[second], swapped[first]
    swapped_receipt = publication_core.build_publication_receipt(swapped)
    assert sorted(
        gate["evidence_sha256"] for gate in receipt["gates"]
    ) == sorted(
        gate["evidence_sha256"] for gate in swapped_receipt["gates"]
    )
    assert receipt["evidence_root_sha256"] != (
        swapped_receipt["evidence_root_sha256"])


@pytest.mark.parametrize("mutation", [
    lambda value: value.update({"schema_version": True}),
    lambda value: value.update({"schema_version": 2}),
    lambda value: value.update({"kind": "partial_publication_receipt"}),
    lambda value: value.update({"status": "fail"}),
    lambda value: value.update({"unexpected": False}),
    lambda value: value.pop("evidence_root_sha256"),
    lambda value: value["gates"].pop(),
    lambda value: value["gates"].append(copy.deepcopy(value["gates"][0])),
    lambda value: value["gates"].reverse(),
    lambda value: value["gates"][0].update({"status": "fail"}),
    lambda value: value["gates"][0].update({"name": "physical_order"}),
    lambda value: value["gates"][0].update({"extra": None}),
])
def test_validator_rejects_partial_failed_reordered_or_extended_receipts(
        mutation):
    receipt = _receipt()
    mutation(receipt)

    with pytest.raises(publication_core.PublicationReceiptError):
        publication_core.validate_publication_receipt(receipt)
    assert not publication_core.publication_receipt_is_complete(receipt)


def test_validator_rejects_evidence_digest_and_atomic_root_tampering():
    changed_evidence = _receipt()
    changed_evidence["gates"][0]["evidence"]["observed"] = 99
    with pytest.raises(
            publication_core.PublicationReceiptError,
            match="digest does not match"):
        publication_core.validate_publication_receipt(changed_evidence)

    changed_digest = _receipt()
    changed_digest["gates"][0]["evidence_sha256"] = "f" * 64
    with pytest.raises(
            publication_core.PublicationReceiptError,
            match="digest does not match"):
        publication_core.validate_publication_receipt(changed_digest)

    uppercase_digest = _receipt()
    uppercase_digest["gates"][0]["evidence_sha256"] = (
        uppercase_digest["gates"][0]["evidence_sha256"].upper())
    with pytest.raises(
            publication_core.PublicationReceiptError,
            match="invalid evidence digest"):
        publication_core.validate_publication_receipt(uppercase_digest)

    changed_root = _receipt()
    changed_root["evidence_root_sha256"] = "0" * 64
    with pytest.raises(
            publication_core.PublicationReceiptError,
            match="root does not match"):
        publication_core.validate_publication_receipt(changed_root)


@pytest.mark.parametrize("bad_evidence", [
    {},
    {"tuple": (1, 2)},
    {1: "non-string key"},
    {"number": float("nan")},
    {"number": float("inf")},
    {"text": "\ud800"},
])
def test_builder_rejects_empty_or_noncanonical_json_evidence(bad_evidence):
    evidence = _gate_evidence()
    evidence["source_completeness"] = bad_evidence

    with pytest.raises(publication_core.PublicationReceiptError):
        publication_core.build_publication_receipt(evidence)


def test_builder_requires_the_exact_gate_set_and_plain_mapping():
    missing = _gate_evidence()
    missing.pop("vector_store_parity")
    with pytest.raises(
            publication_core.PublicationReceiptError, match="exact gate set"):
        publication_core.build_publication_receipt(missing)

    extra = _gate_evidence()
    extra["another_gate"] = {"status": "pass"}
    with pytest.raises(
            publication_core.PublicationReceiptError, match="exact gate set"):
        publication_core.build_publication_receipt(extra)

    with pytest.raises(
            publication_core.PublicationReceiptError, match="mapping"):
        publication_core.build_publication_receipt([])  # type: ignore[arg-type]


def test_builder_rejects_evidence_beyond_the_nesting_bound():
    nested: object = "leaf"
    for _ in range(70):
        nested = [nested]
    evidence = _gate_evidence()
    evidence["source_completeness"] = {"nested": nested}

    with pytest.raises(
            publication_core.PublicationReceiptError, match="nesting depth"):
        publication_core.build_publication_receipt(evidence)


def test_canonical_bytes_round_trip_through_strict_parser():
    receipt = _receipt()
    raw = publication_core.serialize_publication_receipt(receipt)

    assert b"\n" not in raw
    assert b": " not in raw
    assert publication_core.parse_publication_receipt_bytes(raw) == receipt
    pretty = json.dumps(receipt, indent=2).encode("utf-8")
    parsed = publication_core.parse_publication_receipt_bytes(pretty)
    assert publication_core.serialize_publication_receipt(parsed) == raw


def test_strict_parser_rejects_duplicate_nonfinite_or_malformed_json():
    raw = publication_core.serialize_publication_receipt(_receipt())
    duplicate = raw.replace(
        b'"status":"pass"',
        b'"status":"pass","status":"pass"',
        1,
    )
    with pytest.raises(
            publication_core.PublicationReceiptError, match="duplicate"):
        publication_core.parse_publication_receipt_bytes(duplicate)

    for invalid in (
            b'{"value":NaN}',
            b'{"value":Infinity}',
            b'{"value":1e999}',
            b'{"value":1} trailing',
            b'[]'):
        with pytest.raises(publication_core.PublicationReceiptError):
            publication_core.parse_publication_receipt_bytes(invalid)

    with pytest.raises(
            publication_core.PublicationReceiptError, match="cannot parse"):
        publication_core.parse_publication_receipt_bytes(b"\xff")


def test_strict_parser_enforces_input_and_byte_bounds():
    raw = publication_core.serialize_publication_receipt(_receipt())

    with pytest.raises(TypeError, match="must be bytes"):
        publication_core.parse_publication_receipt_bytes(  # type: ignore[arg-type]
            raw.decode("utf-8"))
    with pytest.raises(
            publication_core.PublicationReceiptError, match="positive"):
        publication_core.parse_publication_receipt_bytes(raw, max_bytes=True)
    with pytest.raises(
            publication_core.PublicationReceiptError, match="empty"):
        publication_core.parse_publication_receipt_bytes(b"")
    with pytest.raises(
            publication_core.PublicationReceiptError, match="maximum"):
        publication_core.parse_publication_receipt_bytes(
            raw, max_bytes=len(raw) - 1)


def test_serializer_refuses_a_receipt_that_was_mutated_after_validation():
    receipt = _receipt()
    receipt["gates"][2]["evidence"]["required"] = 500

    with pytest.raises(publication_core.PublicationReceiptError):
        publication_core.serialize_publication_receipt(receipt)


def test_in_memory_validation_enforces_the_persisted_size_contract(monkeypatch):
    receipt = _receipt()
    serialized = publication_core.serialize_publication_receipt(receipt)
    monkeypatch.setattr(
        publication_core, "MAX_PUBLICATION_RECEIPT_BYTES",
        len(serialized) - 1)

    with pytest.raises(
            publication_core.PublicationReceiptError, match="maximum byte size"):
        publication_core.validate_publication_receipt(receipt)
    assert not publication_core.publication_receipt_is_complete(receipt)
