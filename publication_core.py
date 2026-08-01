"""Atomic, deterministic publication-gate receipts.

This module is deliberately standard-library only and performs no filesystem
I/O.  Callers supply the evidence produced by each publication gate, then
persist the returned receipt through their own atomic artifact machinery.

A receipt exists only for a fully passing publication generation.  Its five
gates are mandatory and ordered, each evidence object is hash-bound, and one
sequence-sensitive root binds the complete gate set together.
"""

from __future__ import annotations

import hashlib
import json
import math


PUBLICATION_RECEIPT_SCHEMA_VERSION = 1
PUBLICATION_RECEIPT_KIND = "publication_receipt"
PUBLICATION_GATE_NAMES = (
    "source_completeness",
    "physical_order",
    "semantic_hierarchy",
    "markdown_validity",
    "vector_store_parity",
)
MAX_PUBLICATION_RECEIPT_BYTES = 4 * 1024 * 1024

_RECEIPT_FIELDS = {
    "schema_version",
    "kind",
    "status",
    "gates",
    "evidence_root_sha256",
}
_GATE_FIELDS = {
    "name",
    "status",
    "evidence",
    "evidence_sha256",
}
_EVIDENCE_HASH_DOMAIN = b"rag-publication-evidence-v1\0"
_ROOT_HASH_DOMAIN = b"rag-publication-root-v1\0"
_MAX_JSON_DEPTH = 64
_SHA256_HEX = frozenset("0123456789abcdef")


class PublicationReceiptError(ValueError):
    """Raised when publication evidence or a receipt is not trustworthy."""


def _contains_surrogate(value: str) -> bool:
    return any(0xD800 <= ord(character) <= 0xDFFF for character in value)


def _validate_json_value(
        value: object, *, path: str, depth: int = 0) -> None:
    """Require plain, finite JSON values with valid Unicode scalar text."""
    if depth > _MAX_JSON_DEPTH:
        raise PublicationReceiptError(
            f"{path} exceeds the maximum JSON nesting depth")
    value_type = type(value)
    if value is None or value_type is bool or value_type is int:
        return
    if value_type is float:
        if not math.isfinite(value):
            raise PublicationReceiptError(f"{path} contains a non-finite number")
        return
    if value_type is str:
        if _contains_surrogate(value):
            raise PublicationReceiptError(
                f"{path} contains an invalid Unicode surrogate")
        return
    if value_type is list:
        for index, item in enumerate(value):
            _validate_json_value(
                item, path=f"{path}[{index}]", depth=depth + 1)
        return
    if value_type is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise PublicationReceiptError(
                    f"{path} contains a non-string object key")
            if _contains_surrogate(key):
                raise PublicationReceiptError(
                    f"{path} contains an invalid Unicode object key")
            _validate_json_value(
                item, path=f"{path}.{key}", depth=depth + 1)
        return
    raise PublicationReceiptError(
        f"{path} contains a non-JSON value of type {value_type.__name__}")


def _canonical_json_bytes(value: object) -> bytes:
    _validate_json_value(value, path="value")
    try:
        serialized = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return serialized.encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise PublicationReceiptError(
            "value cannot be serialized as canonical JSON") from exc


def _sha256(domain: bytes, payload: bytes) -> str:
    return hashlib.sha256(domain + payload).hexdigest()


def canonical_evidence_sha256(evidence: dict) -> str:
    """Hash one nonempty evidence object using canonical, domain-bound JSON."""
    if type(evidence) is not dict or not evidence:
        raise PublicationReceiptError(
            "publication gate evidence must be a nonempty JSON object")
    return _sha256(_EVIDENCE_HASH_DOMAIN, _canonical_json_bytes(evidence))


def _canonical_clone(evidence: dict) -> dict:
    """Detach evidence from caller mutation while retaining exact JSON values."""
    raw = _canonical_json_bytes(evidence)
    try:
        clone = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise PublicationReceiptError(
            "canonical publication evidence cannot be decoded") from exc
    if type(clone) is not dict:
        raise PublicationReceiptError(
            "publication gate evidence must be a JSON object")
    return clone


def _evidence_root_sha256(gates: list[dict]) -> str:
    root_evidence = {
        "schema_version": PUBLICATION_RECEIPT_SCHEMA_VERSION,
        "kind": PUBLICATION_RECEIPT_KIND,
        "gates": [
            {
                "name": gate["name"],
                "evidence_sha256": gate["evidence_sha256"],
            }
            for gate in gates
        ],
    }
    return _sha256(_ROOT_HASH_DOMAIN, _canonical_json_bytes(root_evidence))


def _valid_sha256(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(character in _SHA256_HEX for character in value)
    )


def build_publication_receipt(gate_evidence: dict[str, dict]) -> dict:
    """Build one atomic pass receipt from the exact five evidence objects.

    Input mapping order is irrelevant.  The serialized contract always uses
    :data:`PUBLICATION_GATE_NAMES` order, and the returned evidence is detached
    from subsequent mutation of the caller's dictionaries.
    """
    if type(gate_evidence) is not dict:
        raise PublicationReceiptError(
            "publication gate evidence must be a JSON object mapping")
    if set(gate_evidence) != set(PUBLICATION_GATE_NAMES):
        raise PublicationReceiptError(
            "publication gate evidence must contain the exact gate set")

    gates = []
    for name in PUBLICATION_GATE_NAMES:
        evidence = gate_evidence[name]
        if type(evidence) is not dict or not evidence:
            raise PublicationReceiptError(
                f"{name} evidence must be a nonempty JSON object")
        detached = _canonical_clone(evidence)
        gates.append({
            "name": name,
            "status": "pass",
            "evidence": detached,
            "evidence_sha256": canonical_evidence_sha256(detached),
        })

    receipt = {
        "schema_version": PUBLICATION_RECEIPT_SCHEMA_VERSION,
        "kind": PUBLICATION_RECEIPT_KIND,
        "status": "pass",
        "gates": gates,
        "evidence_root_sha256": _evidence_root_sha256(gates),
    }
    validate_publication_receipt(receipt)
    if len(_canonical_json_bytes(receipt)) > MAX_PUBLICATION_RECEIPT_BYTES:
        raise PublicationReceiptError(
            "publication receipt exceeds the maximum byte size")
    return receipt


def validate_publication_receipt(payload: object) -> dict:
    """Validate the exact schema, pass state, evidence hashes, and root."""
    if type(payload) is not dict:
        raise PublicationReceiptError(
            "publication receipt must be a JSON object")
    _validate_json_value(payload, path="receipt")
    if set(payload) != _RECEIPT_FIELDS:
        raise PublicationReceiptError(
            "publication receipt has an invalid field set")
    schema_version = payload.get("schema_version")
    if (type(schema_version) is not int
            or schema_version != PUBLICATION_RECEIPT_SCHEMA_VERSION):
        raise PublicationReceiptError(
            "unsupported publication receipt schema")
    if payload.get("kind") != PUBLICATION_RECEIPT_KIND:
        raise PublicationReceiptError("invalid publication receipt kind")
    if payload.get("status") != "pass":
        raise PublicationReceiptError(
            "publication receipts attest passing generations only")

    gates = payload.get("gates")
    if type(gates) is not list or len(gates) != len(PUBLICATION_GATE_NAMES):
        raise PublicationReceiptError(
            "publication receipt must contain exactly five gates")
    for index, expected_name in enumerate(PUBLICATION_GATE_NAMES):
        gate = gates[index]
        if type(gate) is not dict or set(gate) != _GATE_FIELDS:
            raise PublicationReceiptError(
                "publication receipt gate has an invalid field set")
        if gate.get("name") != expected_name:
            raise PublicationReceiptError(
                "publication receipt gates are missing or out of order")
        if gate.get("status") != "pass":
            raise PublicationReceiptError(
                "publication receipts cannot contain a failed gate")
        evidence = gate.get("evidence")
        if type(evidence) is not dict or not evidence:
            raise PublicationReceiptError(
                f"{expected_name} evidence must be a nonempty JSON object")
        evidence_sha256 = gate.get("evidence_sha256")
        if not _valid_sha256(evidence_sha256):
            raise PublicationReceiptError(
                f"{expected_name} has an invalid evidence digest")
        if evidence_sha256 != canonical_evidence_sha256(evidence):
            raise PublicationReceiptError(
                f"{expected_name} evidence digest does not match evidence")

    root = payload.get("evidence_root_sha256")
    if not _valid_sha256(root):
        raise PublicationReceiptError(
            "publication receipt has an invalid evidence root")
    if root != _evidence_root_sha256(gates):
        raise PublicationReceiptError(
            "publication receipt evidence root does not match its gates")
    if len(_canonical_json_bytes(payload)) > MAX_PUBLICATION_RECEIPT_BYTES:
        raise PublicationReceiptError(
            "publication receipt exceeds the maximum byte size")
    return payload


def serialize_publication_receipt(payload: object) -> bytes:
    """Return the canonical UTF-8 representation of one valid receipt."""
    validated = validate_publication_receipt(payload)
    raw = _canonical_json_bytes(validated)
    if len(raw) > MAX_PUBLICATION_RECEIPT_BYTES:
        raise PublicationReceiptError(
            "publication receipt exceeds the maximum byte size")
    return raw


def _reject_json_constant(value: str) -> object:
    raise PublicationReceiptError(
        f"publication receipt contains non-finite JSON constant {value}")


def _strict_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise PublicationReceiptError(
                f"duplicate publication receipt field: {key}")
        result[key] = value
    return result


def parse_publication_receipt_bytes(
        raw: bytes, *,
        max_bytes: int = MAX_PUBLICATION_RECEIPT_BYTES) -> dict:
    """Strictly parse and validate one bounded UTF-8 receipt snapshot."""
    if type(raw) is not bytes:
        raise TypeError("publication receipt input must be bytes")
    if (type(max_bytes) is not int or max_bytes < 1):
        raise PublicationReceiptError(
            "publication receipt byte limit must be a positive integer")
    if not raw:
        raise PublicationReceiptError("publication receipt is empty")
    if len(raw) > max_bytes:
        raise PublicationReceiptError(
            "publication receipt exceeds the maximum byte size")
    try:
        text = raw.decode("utf-8", errors="strict")
        payload = json.loads(
            text,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except PublicationReceiptError:
        raise
    except (UnicodeError, json.JSONDecodeError, ValueError,
            RecursionError) as exc:
        raise PublicationReceiptError(
            "cannot parse publication receipt JSON") from exc
    return validate_publication_receipt(payload)


def publication_receipt_is_complete(payload: object) -> bool:
    """Return whether *payload* satisfies the complete pass-only contract."""
    try:
        validate_publication_receipt(payload)
    except (PublicationReceiptError, TypeError, ValueError):
        return False
    return True
