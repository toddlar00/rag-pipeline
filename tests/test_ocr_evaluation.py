from __future__ import annotations

import hashlib
import itertools
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import ocr_evaluation as ocr
import storage_policy
from tools import evaluate_ocr as cli


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _payload(reference="The rule does not apply.", prediction=None, **extra):
    return {"schema_version": 1, "records": [{
        "id": "synthetic-001", "reference": reference,
        "prediction": reference if prediction is None else prediction, **extra,
    }]}


@pytest.mark.parametrize(("reference", "prediction", "counts"), [
    ("kitten", "sitting", (2, 1, 0)),
    ("", "abc", (0, 3, 0)),
    ("abc", "", (0, 0, 3)),
    ("", "", (0, 0, 0)),
    ("abc", "abc", (0, 0, 0)),
    ("same a same", "same b same", (1, 0, 0)),
    ("ab", "ba", (2, 0, 0)),
])
def test_known_character_edit_counts(reference, prediction, counts):
    metrics = ocr.evaluate_ocr(_payload(reference, prediction))["summary"]["character"]
    assert tuple(metrics[key] for key in ("substitutions", "insertions", "deletions")) == counts
    assert metrics["edit_distance"] == sum(counts)
    assert metrics["prediction_units"] == metrics["reference_units"] + counts[1] - counts[2]


def test_exhaustive_small_distances_match_independent_full_matrix():
    words = ["".join(items) for size in range(4) for items in itertools.product("ab", repeat=size)]
    for reference, prediction in itertools.product(words, repeat=2):
        matrix = [[0] * (len(prediction) + 1) for _ in range(len(reference) + 1)]
        for row in range(len(reference) + 1):
            matrix[row][0] = row
        for column in range(len(prediction) + 1):
            matrix[0][column] = column
        for row, expected in enumerate(reference, 1):
            for column, observed in enumerate(prediction, 1):
                matrix[row][column] = min(
                    matrix[row - 1][column] + 1, matrix[row][column - 1] + 1,
                    matrix[row - 1][column - 1] + (expected != observed))
        counts = ocr._alignment_counts(reference, prediction)
        assert sum(counts.values()) == matrix[-1][-1]
        assert len(prediction) == len(reference) + counts["insertions"] - counts["deletions"]


def test_normalization_is_explicit_and_case_and_punctuation_sensitive():
    report = ocr.evaluate_ocr(_payload(" cafe\u0301\t  rule\n", "café rule"))
    assert report["summary"]["exact_match_count"] == 1
    assert report["normalization"]["unicode"] == "NFC"
    report = ocr.evaluate_ocr(_payload("Rule.", "rule"))
    assert report["summary"]["character"]["edit_distance"] == 2
    assert report["summary"]["word"]["substitutions"] == 1


def test_micro_aggregation_includes_empty_reference_insertions():
    payload = _payload("a", "b")
    payload["records"] += [
        {"id": "two", "reference": "a " * 9, "prediction": "a " * 9},
        {"id": "blank", "reference": "", "prediction": "invented"},
    ]
    report = ocr.evaluate_ocr(payload)
    assert report["summary"]["word"]["error_rate"] == 0.2
    assert report["summary"]["exact_match_rate"] == pytest.approx(1 / 3)
    assert report["records"][2]["word"]["error_rate"] is None
    assert report["records"][2]["word"]["insertions"] == 1
    assert report["records"][2]["exact_match"] is False


def test_only_blank_references_have_null_rates_but_visible_insertions():
    report = ocr.evaluate_ocr(_payload("", "invented text"))
    assert report["summary"]["word"]["error_rate"] is None
    assert report["summary"]["word"]["insertions"] == 2
    assert report["summary"]["critical_occurrence_totals"]["occurrence_recall"] is None
    json.dumps(report, allow_nan=False)


def test_critical_occurrence_counts_preserve_punctuation_and_boundaries():
    payload = _payload(
        "not not notice § 123 § 123 Rule.", "not notice § 128 § 123 rule.",
        critical_tokens=["not", "§ 123", "Rule."])
    record = ocr.evaluate_ocr(payload)["records"][0]
    critical = record["critical_tokens"]
    assert [item["reference_occurrences"] for item in critical] == [2, 2, 1]
    assert [item["prediction_occurrences"] for item in critical] == [1, 1, 0]
    assert record["critical_occurrence_totals"]["occurrence_recall"] == 0.4
    assert [item["token_index"] for item in critical] == [0, 1, 2]
    assert all("token" not in item for item in critical)


def test_critical_summary_does_not_offset_missing_tokens_with_extra_ones():
    report = ocr.evaluate_ocr(_payload("not §", "not not", critical_tokens=["not", "§"]))
    totals = report["summary"]["critical_occurrence_totals"]
    assert totals["occurrence_recall"] == 0.5
    assert totals["missing_occurrences"] == totals["extra_occurrences"] == 1


def test_critical_occurrences_are_not_a_position_accuracy_claim():
    report = ocr.evaluate_ocr(_payload("not apply", "apply not", critical_tokens=["not"]))
    assert report["summary"]["critical_occurrence_totals"]["occurrence_recall"] == 1
    assert report["summary"]["exact_match_count"] == 0
    assert "positions and context are not verified" in report["critical_occurrence_semantics"]


def test_metrics_never_contain_reference_prediction_or_critical_text():
    report = ocr.evaluate_ocr(_payload("PRIVATE_REFERENCE", "PRIVATE_PREDICTION", critical_tokens=["PRIVATE_REFERENCE"]))
    rendered = json.dumps(report)
    assert "PRIVATE_REFERENCE" not in rendered
    assert "PRIVATE_PREDICTION" not in rendered


@pytest.mark.parametrize("payload", [
    [], {}, {"schema_version": True, "records": []},
    {"schema_version": 2, "records": []},
    {"schema_version": 1, "records": []},
    {"schema_version": 1, "records": {}, "secret": "x"},
    {"schema_version": 1, "records": [None]},
    {"schema_version": 1, "records": [{"id": "missing"}]},
])
def test_invalid_root_and_record_schemas(payload):
    with pytest.raises(ValueError):
        ocr.evaluate_ocr(payload)


@pytest.mark.parametrize(("key", "value"), [
    ("id", "../private"), ("id", "with space"), ("id", ""), ("id", True),
    ("id", "x" * 129), ("reference", None), ("prediction", 42),
    ("reference", "x" * (ocr.MAX_TEXT_CHARACTERS + 1)),
    ("prediction", "\ud800"), ("critical_tokens", "not"),
    ("critical_tokens", [""]), ("critical_tokens", ["absent"]),
    ("critical_tokens", ["not", " not "]), ("critical_tokens", [None]),
    ("critical_tokens", ["not"] * (ocr.MAX_CRITICAL_TOKENS + 1)),
    ("critical_tokens", ["x" * (ocr.MAX_CRITICAL_CHARACTERS + 1)]),
])
def test_invalid_fields_rejected_without_echoing_values(key, value):
    payload = _payload()
    payload["records"][0][key] = value
    with pytest.raises(ValueError) as error:
        ocr.evaluate_ocr(payload)
    assert "../private" not in str(error.value)
    assert "absent" not in str(error.value)


def test_duplicate_ids_unknown_fields_and_record_count_are_rejected():
    payload = _payload()
    payload["records"] *= 2
    with pytest.raises(ValueError, match="duplicate id"):
        ocr.evaluate_ocr(payload)
    payload = _payload(unexpected="private")
    with pytest.raises(ValueError, match="requires"):
        ocr.evaluate_ocr(payload)
    payload = _payload()
    payload["records"] *= ocr.MAX_RECORDS + 1
    with pytest.raises(ValueError, match="list of 1"):
        ocr.evaluate_ocr(payload)


def test_alignment_budgets_fail_before_any_dynamic_programming(monkeypatch):
    monkeypatch.setattr(ocr, "MAX_RECORD_ALIGNMENT_CELLS", 5)
    monkeypatch.setattr(ocr, "_alignment_counts", lambda *_: pytest.fail("must preflight first"))
    with pytest.raises(ValueError, match="split it into smaller regions"):
        ocr.evaluate_ocr(_payload("abc", "xyz"))


def test_total_alignment_budget_includes_word_cells(monkeypatch):
    monkeypatch.setattr(ocr, "MAX_TOTAL_ALIGNMENT_CELLS", 18)
    payload = _payload("abc", "xyz")
    payload["records"].append({"id": "second", "reference": "def", "prediction": "uvw"})
    with pytest.raises(ValueError, match="smaller batches"):
        ocr.evaluate_ocr(payload)


def test_equal_ends_reduce_required_alignment_budget(monkeypatch):
    monkeypatch.setattr(ocr, "MAX_RECORD_ALIGNMENT_CELLS", 2)
    report = ocr.evaluate_ocr(_payload("a" * 5000 + "x", "a" * 5000 + "y"))
    assert report["summary"]["character"]["substitutions"] == 1


@pytest.mark.parametrize("raw", [
    b'{"PRIVATE_FIELD":1,"PRIVATE_FIELD":2}',
    b'{"schema_version":NaN,"records":[]}', b'[]', b'\xff',
])
def test_file_parser_is_strict_without_echoing_raw_fields(tmp_path, raw, capsys):
    source, output = tmp_path / "input.json", tmp_path / "metrics.json"
    source.write_bytes(raw)
    assert cli.main(["--input", str(source), "--output", str(output)]) == 2
    captured = capsys.readouterr()
    assert "PRIVATE_FIELD" not in captured.err
    assert "strict UTF-8 JSON" in captured.err
    assert not output.exists()


def test_input_size_is_bounded(tmp_path, monkeypatch, capsys):
    source = tmp_path / "input.json"
    source.write_text(json.dumps(_payload()), encoding="utf-8")
    monkeypatch.setattr(ocr, "MAX_INPUT_BYTES", 10)
    with pytest.raises(ValueError, match="exceeds"):
        ocr.evaluate_ocr_file(source, tmp_path / "output.json")
    assert cli.main(["--input", str(source), "--output", str(tmp_path / "output.json")]) == 2
    assert str(source) not in capsys.readouterr().err


def test_input_cannot_be_overwritten(tmp_path):
    source = tmp_path / "input.json"
    raw = json.dumps(_payload()).encode()
    source.write_bytes(raw)
    with pytest.raises(ValueError, match="distinct"):
        ocr.evaluate_ocr_file(source, source.parent / "." / source.name)
    assert source.read_bytes() == raw


def test_hard_link_to_input_cannot_be_output(tmp_path):
    source, output = tmp_path / "input.json", tmp_path / "output.json"
    source.write_text(json.dumps(_payload()), encoding="utf-8")
    try:
        os.link(source, output)
    except OSError:
        pytest.skip("hard links unavailable")
    with pytest.raises(ValueError, match="distinct"):
        ocr.evaluate_ocr_file(source, output)


@pytest.mark.parametrize("link_input", [False, True])
def test_link_paths_are_rejected(tmp_path, link_input):
    source = tmp_path / "input.json"
    source.write_text(json.dumps(_payload()), encoding="utf-8")
    target = source if link_input else tmp_path / "output.json"
    link = tmp_path / "linked.json"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symbolic links unavailable")
    with pytest.raises(storage_policy.StoragePolicyError):
        ocr.evaluate_ocr_file(link if link_input else source, target if link_input else link)


def test_output_failure_is_redacted_and_preserves_input(tmp_path, monkeypatch, capsys):
    source = tmp_path / "input.json"
    raw = json.dumps(_payload()).encode()
    source.write_bytes(raw)

    def fail(*args, **kwargs):
        raise OSError("PRIVATE_PATH")

    monkeypatch.setattr(storage_policy, "atomic_write_private_json", fail)
    assert cli.main(["--input", str(source), "--output", str(tmp_path / "output.json")]) == 2
    assert "PRIVATE_PATH" not in capsys.readouterr().err
    assert source.read_bytes() == raw


def test_file_metrics_use_private_atomic_writer_and_exact_input_digest(tmp_path, monkeypatch):
    source, output = tmp_path / "input.json", tmp_path / "output.json"
    raw = json.dumps(_payload()).encode()
    source.write_bytes(raw)
    observed = []
    monkeypatch.setattr(storage_policy, "atomic_write_private_json", lambda *args, **kwargs: observed.append((args, kwargs)))
    report = ocr.evaluate_ocr_file(source, output)
    assert observed == [((output, report), {"indent": 2})]
    assert report["input_sha256"] == hashlib.sha256(raw).hexdigest()


def test_real_cli_writes_private_report_from_synthetic_fixture(tmp_path):
    source, output = tmp_path / "input.json", tmp_path / "nested" / "metrics.json"
    source.write_text(json.dumps(_payload("A rule does not apply.", "A rule does apply.", critical_tokens=["not"])), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "tools" / "evaluate_ocr.py"),
         "--input", str(source), "--output", str(output)],
        cwd=tmp_path, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["summary"]["word"]["deletions"] == 1
    assert report["summary"]["critical_occurrence_totals"]["missing_occurrences"] == 1
    assert "1 records" in result.stdout
    assert "A rule" not in result.stdout + result.stderr
    assert str(source) not in result.stdout + result.stderr
    if os.name != "nt":
        assert output.stat().st_mode & 0o077 == 0


def test_import_does_not_load_ocr_or_ml_runtimes():
    result = subprocess.run(
        [sys.executable, "-c", "import sys; import ocr_evaluation; assert not ({'rag', 'torch', 'numpy', 'docling', 'requests'} & set(sys.modules))"],
        cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
