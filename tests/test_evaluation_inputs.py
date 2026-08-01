from __future__ import annotations

import hashlib
import itertools
import subprocess
import sys
from pathlib import Path

import pytest

import evaluation_inputs
import evaluation_review
import storage_policy


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_evaluation_inputs_is_a_dependency_light_leaf_module():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import evaluation_inputs; "
                "forbidden = {'eval', 'evaluation_contract', "
                "'evaluation_release', 'evaluation_review', 'job_manager', "
                "'model_artifacts', 'rag', 'retrieval_core', "
                "'service_runtime', 'table_retrieval_core', 'requests', "
                "'numpy', 'torch'}; "
                "loaded = sorted(forbidden.intersection(sys.modules)); "
                "assert not loaded, loaded"
            ),
        ],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_evaluation_review_preserves_input_helper_compatibility_aliases():
    assert evaluation_review._hex_digest is evaluation_inputs._hex_digest
    assert evaluation_review._strict_json_bytes is evaluation_inputs._strict_json_bytes
    assert evaluation_review._read_snapshot is evaluation_inputs._read_snapshot
    assert evaluation_review._corpus_contract is evaluation_inputs._corpus_contract


@pytest.mark.parametrize(
    ("value", "accepted"),
    [
        ("a" * 64, True),
        ("A" * 64, False),
        ("a" * 63, False),
        (True, False),
        (None, False),
    ],
)
def test_hex_digest_accepts_only_canonical_lowercase_sha256(value, accepted):
    if accepted:
        assert evaluation_inputs._hex_digest(value, label="digest") == value
    else:
        with pytest.raises(ValueError, match="lowercase SHA-256"):
            evaluation_inputs._hex_digest(value, label="digest")


def test_strict_json_bytes_accepts_one_utf8_object_with_bom():
    assert evaluation_inputs._strict_json_bytes(
        b'\xef\xbb\xbf{"ok":true}', label="fixture", max_bytes=32,
    ) == {"ok": True}


@pytest.mark.parametrize(
    ("raw", "max_bytes", "message"),
    [
        (b'{"a":1,"a":2}', 64, "duplicate field 'a'"),
        (b'{"value":NaN}', 64, "non-finite number NaN"),
        (b'{"value":1e999}', 64, "non-finite number 1e999"),
        (
            b'{"value":' + b"9" * 400 + b"}",
            512,
            "integer outside the finite runtime range",
        ),
        (
            b'{"value":' + b"[" * 64 + b"0" + b"]" * 64 + b"}",
            512,
            "exceeds JSON nesting depth 64",
        ),
        (b"\xff", 64, "not valid UTF-8 JSON"),
        (b"[]", 64, "must be one JSON object"),
        (b"{}", 1, "exceeds 1 bytes"),
    ],
)
def test_strict_json_bytes_rejects_ambiguous_or_unbounded_input(
        raw, max_bytes, message):
    with pytest.raises(ValueError, match=message):
        evaluation_inputs._strict_json_bytes(
            raw, label="fixture", max_bytes=max_bytes)


def test_strict_json_depth_ignores_strings_and_accepts_the_exact_ceiling():
    nested = b"[" * 63 + b"0" + b"]" * 63
    raw = b'{"brackets":"[[[{{{]", "value":' + nested + b"}"
    payload = evaluation_inputs._strict_json_bytes(
        raw, label="fixture", max_bytes=len(raw))

    assert payload["brackets"] == "[[[{{{]"


def test_read_snapshot_preserves_exact_bytes_digest_and_link_guard(
        monkeypatch, tmp_path):
    path = tmp_path / "policy.json"
    raw = b'{"schema_version":1}\n'
    path.write_bytes(raw)

    observed, digest = evaluation_inputs._read_snapshot(
        path, label="policy", max_bytes=len(raw))
    assert observed == raw
    assert digest == hashlib.sha256(raw).hexdigest()
    with pytest.raises(ValueError, match="exceeds"):
        evaluation_inputs._read_snapshot(
            path, label="policy", max_bytes=len(raw) - 1)

    def reject_link(_path):
        raise storage_policy.StoragePolicyError("injected link rejection")

    monkeypatch.setattr(
        evaluation_inputs.storage_policy,
        "assert_no_link_components",
        reject_link,
    )
    with pytest.raises(storage_policy.StoragePolicyError, match="link rejection"):
        evaluation_inputs._read_snapshot(
            path, label="policy", max_bytes=len(raw))


def test_corpus_contract_normalizes_one_shared_declaration():
    declaration = {
        "sha256": "A" * 64,
        "record_count": 12,
        "id_scheme": "  stable-v1  ",
    }
    assert evaluation_inputs._corpus_contract([
        {"corpus": dict(declaration)},
        {"corpus": dict(declaration)},
    ]) == {
        "sha256": "a" * 64,
        "record_count": 12,
        "id_scheme": "stable-v1",
    }


@pytest.mark.parametrize(
    ("queries", "message"),
    [
        ([], "must share one corpus"),
        ([{}], "requires every query"),
        ([{"corpus": {"sha256": "x", "record_count": 1,
                       "id_scheme": "stable"}}], "lowercase SHA-256"),
        ([{"corpus": {"sha256": None, "record_count": 1,
                       "id_scheme": "stable"}}], "lowercase SHA-256"),
        ([{"corpus": {"sha256": "a" * 64, "record_count": [],
                       "id_scheme": "stable"}}], "positive integer"),
        ([{"corpus": {"sha256": "a" * 64, "record_count": True,
                       "id_scheme": "stable"}}], "positive integer"),
        ([{"corpus": {"sha256": "a" * 64, "record_count": 0,
                       "id_scheme": "stable"}}], "positive integer"),
        ([{"corpus": {"sha256": "a" * 64, "record_count": 1,
                       "id_scheme": "  "}}], "non-empty string"),
        ([{"corpus": {"sha256": "a" * 64, "record_count": 1,
                       "id_scheme": []}}], "non-empty string"),
        ([
            {"corpus": {"sha256": "a" * 64, "record_count": 1,
                         "id_scheme": "stable"}},
            {"corpus": {"sha256": "b" * 64, "record_count": 1,
                         "id_scheme": "stable"}},
        ], "must share one corpus"),
    ],
)
def test_corpus_contract_rejects_incomplete_or_inconsistent_declarations(
        queries, message):
    with pytest.raises(ValueError, match=message):
        evaluation_inputs._corpus_contract(queries)


@pytest.mark.parametrize(
    "order", tuple(itertools.permutations(
        ("eval", "evaluation_queries", "evaluation_review",
         "evaluation_release"))),
)
def test_evaluation_modules_import_cleanly_in_every_order(order):
    statement = "; ".join(f"import {module}" for module in order)
    result = subprocess.run(
        [sys.executable, "-B", "-c", statement],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("module", "required", "forbidden"),
    [
        (
            "evaluation_review",
            {"evaluation_inputs", "evaluation_queries"},
            {"eval", "evaluation_release"},
        ),
        (
            "evaluation_release",
            {"evaluation_inputs"},
            {"eval", "evaluation_review"},
        ),
        (
            "eval",
            {"evaluation_queries"},
            {"evaluation_review", "evaluation_release"},
        ),
    ],
)
def test_evaluation_module_imports_do_not_eagerly_cross_boundaries(
        module, required, forbidden):
    source = (
        f"import sys; import {module}; "
        f"required={required!r}; forbidden={forbidden!r}; "
        "missing=sorted(required.difference(sys.modules)); "
        "loaded=sorted(forbidden.intersection(sys.modules)); "
        "assert not missing, missing; assert not loaded, loaded"
    )
    result = subprocess.run(
        [sys.executable, "-B", "-c", source],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
