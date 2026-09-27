"""Synthetic, dependency-light OCR recovery policy and publication tests."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

import artifact_io
import ocr_recovery as recovery
import storage_policy
from tools import retry_ocr as cli


DIGEST = hashlib.sha256(b"synthetic PDF generation").hexdigest()
HEALTHY = "This synthetic paragraph contains enough readable text for normal extraction."


def _candidate(text="Recovered synthetic text"):
    lines = ([{"text": text, "score": 0.95,
               "box": [[0.0, 0.0], [100.0, 0.0], [100.0, 20.0], [0.0, 20.0]]}]
             if text else [])
    return {
        "text": text, "lines": lines,
        "mean_confidence": 0.95 if lines else None,
        "raster": {"width": 100, "height": 100, "dpi": 300,
                   "coordinate_system": "rendered_image_pixels"},
        "engine": {"name": "rapidocr", "version": "synthetic",
                   "min_score": 0.0, "max_side": 6000},
    }


class FakeReader:
    def __init__(self, pages, *, candidates=None, on_retry=None, on_close=None):
        self.pages = pages
        self.page_count = len(pages)
        self.candidates = {} if candidates is None else candidates
        self.native_calls = []
        self.retry_calls = []
        self.on_retry = on_retry
        self.on_close = on_close
        self.path = None
        self.options = None
        self.entered = False
        self.closed = False
        self.exit_error = None

    def __enter__(self):
        self.entered = True
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.closed = True
        self.exit_error = exc_type
        if self.on_close is not None:
            self.on_close(self)
        return False

    def native_text(self, page_number):
        self.native_calls.append(page_number)
        value = self.pages[page_number - 1]
        if isinstance(value, BaseException):
            raise value
        return value

    def retry(self, page_number):
        self.retry_calls.append(page_number)
        if self.on_retry is not None:
            self.on_retry(self, page_number)
        value = self.candidates.get(page_number, _candidate())
        if isinstance(value, BaseException):
            raise value
        return value


def _factory(reader):
    def create(path, **kwargs):
        reader.path = Path(path)
        reader.options = kwargs
        return reader
    return create


def _evidence(*pages, digest=DIGEST):
    return {"schema_version": 1, "source_sha256": digest, "pages": list(pages)}


def _build(reader, **kwargs):
    return recovery.build_recovery_report(
        reader, source_sha256=DIGEST,
        policy=kwargs.pop("policy", recovery.RetryPolicy()), **kwargs)


@pytest.fixture
def recovery_files(tmp_path, monkeypatch):
    source, output = tmp_path / "source.pdf", tmp_path / "review.json"
    source.write_bytes(b"synthetic PDF generation")
    # Exercise real snapshot/verification/cleanup in an isolated test directory.
    actual_snapshot = artifact_io.immutable_file_snapshot

    def snapshot(path, **kwargs):
        return actual_snapshot(path, scratch_root=tmp_path / "scratch", **kwargs)

    monkeypatch.setattr(recovery.artifact_io, "immutable_file_snapshot", snapshot)
    return source, output


def test_requested_pages_take_priority_with_deterministic_cap_and_deferred_reasons():
    reader = FakeReader(["", "short", HEALTHY, "\ufffdcorrupt", HEALTHY])
    report = _build(reader, requested_pages=(5, 3, 5), policy=recovery.RetryPolicy(max_pages=3))
    assert reader.native_calls == [3, 5, 1, 2, 4]
    assert reader.retry_calls == [3, 5, 1]
    assert [page["page_number"] for page in report["pages"]] == [3, 5, 1]
    assert report["selection"]["requested_pages"] == [3, 5]
    assert report["deferred_pages"] == [
        {"page_number": 2, "reasons": ["short_text"]},
        {"page_number": 4, "reasons": ["corrupt_text", "short_text"]},
    ]
    assert report["summary"] == {
        "inspected": 5, "selected": 3, "deferred": 2, "failed": 0,
        "empty": 0, "review_required": 3,
    }
    assert report["canonical_extraction_modified"] is False
    assert report["accuracy_verified"] is False
    assert report["retry_configuration"]["attempts_per_page"] == 1


def test_healthy_pages_never_call_ocr_and_confidence_does_not_claim_accuracy():
    reader = FakeReader([HEALTHY] * 3)
    report = _build(reader, evidence=_evidence(
        {"page_number": 2, "text": HEALTHY, "low_grade": "FAIR", "ocr_confidence": 0.01},
        {"page_number": 3, "text": HEALTHY, "low_grade": "EXCELLENT", "ocr_confidence": 1},
    ))
    assert reader.native_calls == [1]
    assert reader.retry_calls == []
    assert report["pages"] == []
    assert report["summary"]["selected"] == 0
    assert report["accuracy_verified"] is False


def test_poor_grade_retries_operator_evidence_and_preserves_original():
    original = "Operator transcription with sufficient text for a normal page."
    evidence = _evidence({"page_number": 1, "text": original,
                          "low_grade": "POOR", "ocr_confidence": 0.42})
    reader = FakeReader([RuntimeError("must not use native text")])
    report = _build(reader, evidence=evidence)
    page = report["pages"][0]
    assert page["reasons"] == ["poor_quality_grade"]
    assert page["original_text"] == original
    assert page["original_ocr_confidence"] == 0.42
    assert page["baseline_kind"] == "operator_evidence"
    assert reader.native_calls == []
    assert evidence["pages"][0]["text"] == original


@pytest.mark.parametrize(("text", "reasons"), [
    (None, ["extraction_unavailable"]), (" \n\t", ["empty_text"]),
    ("a b c", ["short_text"]), ("x" * 40, []),
    ("(CID:123)" + HEALTHY, ["corrupt_text"]),
    ("\ufffd" + HEALTHY, ["corrupt_text"]),
])
def test_selection_heuristics_are_explicit(text, reasons):
    assert recovery.page_reasons(text, requested=False, low_grade=None, min_chars=40) == reasons


@pytest.mark.parametrize(("name", "value"), [
    ("dpi", True), ("dpi", 71), ("dpi", 601), ("dpi", 300.0),
    ("max_pages", 0), ("max_pages", 21), ("max_pages", False),
    ("min_chars", 0), ("min_chars", 10001),
    ("max_pixels", 0), ("max_pixels", 25000001),
    ("max_side", 31), ("max_side", 6001),
])
def test_policy_bounds_do_not_coerce_values(name, value):
    with pytest.raises(ValueError):
        recovery.RetryPolicy(**{name: value})


@pytest.mark.parametrize("number", [0, -1, 5001, True, False, 1.0, "1", None])
def test_page_numbers_are_one_based_bounded_integers(number):
    reader = FakeReader([HEALTHY])
    with pytest.raises(ValueError, match="one-based"):
        _build(reader, requested_pages=(number,))
    assert reader.native_calls == reader.retry_calls == []


@pytest.mark.parametrize("count", [0, 5001, True, 1.0])
def test_reader_page_count_is_validated_before_extraction(count):
    reader = FakeReader([HEALTHY])
    reader.page_count = count
    with pytest.raises(ValueError, match="one-based"):
        _build(reader)
    assert not reader.native_calls


def test_requested_and_evidence_pages_must_be_in_this_document():
    reader = FakeReader([HEALTHY])
    with pytest.raises(ValueError, match="outside this PDF"):
        _build(reader, requested_pages=(2,))
    with pytest.raises(ValueError, match="outside this PDF"):
        _build(reader, evidence=_evidence({"page_number": 2, "text": "short"}))
    assert not reader.native_calls


@pytest.mark.parametrize("payload", [
    None, [], {}, {"schema_version": True, "source_sha256": DIGEST, "pages": []},
    {"schema_version": 2, "source_sha256": DIGEST, "pages": []},
    {"schema_version": 1, "source_sha256": "A" * 64, "pages": []},
    {"schema_version": 1, "source_sha256": DIGEST, "pages": {}},
    {"schema_version": 1, "source_sha256": DIGEST, "pages": [], "extra": "private"},
    _evidence(None), _evidence({"page_number": 1}),
    _evidence({"page_number": 1, "text": "short", "extra": "private"}),
    _evidence({"page_number": True, "text": "short"}),
    _evidence({"page_number": 1, "text": None}),
    _evidence({"page_number": 1, "text": "\ud800"}),
    _evidence({"page_number": 1, "text": "x" * (recovery.MAX_TEXT_CHARS + 1)}),
])
def test_evidence_contract_rejects_unknown_or_malformed_fields(payload):
    with pytest.raises(ValueError):
        recovery.validate_evidence(payload)


@pytest.mark.parametrize("grade", ["poor", "FAIR ", True, 0, [], {}])
def test_evidence_grades_are_strict(grade):
    with pytest.raises(ValueError, match="low_grade"):
        recovery.validate_evidence(_evidence({"page_number": 1, "text": HEALTHY, "low_grade": grade}))


@pytest.mark.parametrize("score", [True, False, float("nan"), float("inf"), float("-inf"), -0.1, 1.1, "0.5", {}, 10 ** 1000])
def test_evidence_confidence_is_finite_in_range_and_not_boolean(score):
    with pytest.raises(ValueError, match="ocr_confidence"):
        recovery.validate_evidence(_evidence({"page_number": 1, "text": HEALTHY, "ocr_confidence": score}))


@pytest.mark.parametrize("grade", [None, "POOR", "FAIR", "GOOD", "EXCELLENT"])
@pytest.mark.parametrize("score", [None, 0, 0.5, 1])
def test_valid_evidence_grades_and_confidences(grade, score):
    payload = _evidence({"page_number": 1, "text": HEALTHY,
                         "low_grade": grade, "ocr_confidence": score})
    assert recovery.validate_evidence(payload) == payload


def test_evidence_duplicate_and_excess_page_records_fail():
    page = {"page_number": 1, "text": "short"}
    with pytest.raises(ValueError, match="duplicate"):
        recovery.validate_evidence(_evidence(page, page))
    with pytest.raises(ValueError, match="at most 5000"):
        recovery.validate_evidence(_evidence(*([page] * 5001)))


def test_page_failures_continue_and_preserve_original_without_exception_text():
    reader = FakeReader(
        [RuntimeError("PRIVATE_EXTRACT"), "original two", "original three", "original four"],
        candidates={1: ValueError("PRIVATE_LIMIT"), 2: ImportError("PRIVATE_MODEL"),
                    3: RuntimeError("PRIVATE_RUNTIME"), 4: _candidate()},
    )
    report = _build(reader)
    assert reader.retry_calls == [1, 2, 3, 4]
    assert [page["error_code"] for page in report["pages"]] == [
        "retry_limit_or_validation", "retry_runtime_unavailable", "retry_runtime_failed", None,
    ]
    assert report["pages"][0]["original_text"] is None
    assert report["pages"][0]["original_error"] == "native_extraction_failed"
    assert report["pages"][0]["reasons"] == ["extraction_unavailable"]
    assert report["pages"][1]["original_text"] == "original two"
    assert report["summary"]["failed"] == 3
    assert report["summary"]["review_required"] == 1
    assert "PRIVATE_" not in json.dumps(report)


def test_empty_candidate_is_reported_and_original_is_preserved():
    reader = FakeReader(["original"], candidates={1: _candidate("")})
    page = _build(reader)["pages"][0]
    assert page["status"] == "empty_candidate"
    assert page["candidate"]["text"] == ""
    assert page["original_text"] == "original"


@pytest.mark.parametrize("candidate", [None, {}, {"text": None}, {"text": "\ud800"}, {"text": "x" * (recovery.MAX_TEXT_CHARS + 1)}])
def test_malformed_candidate_is_a_page_failure(candidate):
    reader = FakeReader(["original"], candidates={1: candidate})
    page = _build(reader)["pages"][0]
    assert page["status"] == "retry_failed"
    assert page["candidate"] is None
    assert page["original_text"] == "original"


@pytest.mark.parametrize(("path", "value"), [
    (("mean_confidence",), True), (("mean_confidence",), float("nan")),
    (("mean_confidence",), float("inf")), (("mean_confidence",), 10 ** 1000),
    (("mean_confidence",), 0.5), (("mean_confidence",), None),
    (("text",), "text inconsistent with line text"),
    (("lines", 0, "score"), False), (("lines", 0, "score"), -0.1),
    (("lines", 0, "score"), float("nan")),
    (("lines", 0, "box", 0, 0), True), (("lines", 0, "box", 0, 0), float("inf")),
    (("lines", 0, "box", 0, 0), 12001), (("lines", 0, "box", 0, 0), 101),
    (("lines", 0, "box"), [[0, 0]]), (("lines", 0, "text"), "\ud800"),
    (("lines", 0, "text"), "x" * (recovery.MAX_TEXT_CHARS + 1)),
    (("raster", "width"), True), (("raster", "height"), 0),
    (("raster", "dpi"), 601), (("raster", "coordinate_system"), "arbitrary"),
    (("engine", "name"), "arbitrary"), (("engine", "version"), 7),
    (("engine", "version"), "x" * 129), (("engine", "version"), "\ud800"),
    (("engine", "min_score"), float("nan")), (("engine", "max_side"), True),
], ids=lambda value: type(value).__name__)
def test_candidate_metadata_mutants_are_local_validation_failures(path, value):
    candidate = _candidate()
    owner = candidate
    for key in path[:-1]:
        owner = owner[key]
    owner[path[-1]] = value
    reader = FakeReader(["original"], candidates={1: candidate})
    page = _build(reader)["pages"][0]
    assert page["status"] == "retry_failed"
    assert page["error_code"] == "retry_limit_or_validation"
    assert page["original_text"] == "original"


@pytest.mark.parametrize("path", [(), ("raster",), ("engine",), ("lines", 0)])
def test_unknown_candidate_fields_fail_closed(path):
    candidate = _candidate()
    owner = candidate
    for key in path:
        owner = owner[key]
    owner["unexpected"] = "PRIVATE_INJECTED_TEXT"
    reader = FakeReader(["original"], candidates={1: candidate})
    report = _build(reader)
    assert report["pages"][0]["status"] == "retry_failed"
    assert "PRIVATE_INJECTED_TEXT" not in json.dumps(report)


def test_candidates_are_detached_from_reused_reader_objects():
    shared = _candidate("first candidate")

    def mutate(reader, number):
        if number == 2:
            shared["text"] = "second candidate"
            shared["lines"][0]["text"] = "second candidate"

    reader = FakeReader(["first original", "second original"],
                        candidates={1: shared, 2: shared}, on_retry=mutate)
    report = _build(reader)
    assert report["pages"][0]["candidate"]["text"] == "first candidate"
    assert report["pages"][0]["candidate"]["lines"][0]["text"] == "first candidate"
    assert report["pages"][1]["candidate"]["text"] == "second candidate"
    assert report["pages"][0]["original_text"] == "first original"


@pytest.mark.parametrize("where", ["native", "retry"])
@pytest.mark.parametrize("error", [KeyboardInterrupt("private"), SystemExit(9)])
def test_cancellation_propagates_without_continuing(where, error):
    reader = FakeReader(
        [error if where == "native" else "short", "short"],
        candidates={1: error} if where == "retry" else {},
    )
    with pytest.raises(type(error)):
        _build(reader)
    assert 2 not in reader.retry_calls


def test_recover_publishes_private_source_bound_report_and_closes_snapshot(recovery_files):
    source, output = recovery_files
    reader = FakeReader(["short"])
    report = recovery.recover_pdf(source, output, reader_factory=_factory(reader))
    assert reader.entered and reader.closed
    assert reader.path != source and reader.path.name == "source.pdf"
    assert not reader.path.exists()
    assert reader.options == {"dpi": 300, "max_pixels": 25000000, "max_side": 6000}
    assert report["source_sha256"] == DIGEST
    assert report["evidence_sha256"] is None
    assert json.loads(output.read_text(encoding="utf-8")) == report
    assert source.read_bytes() == b"synthetic PDF generation"
    if os.name != "nt":
        assert output.stat().st_mode & 0o077 == 0


def test_recover_evidence_is_bound_to_exact_bytes(recovery_files):
    source, output = recovery_files
    evidence_path = source.parent / "evidence.json"
    raw = json.dumps(_evidence({"page_number": 1, "text": "provided"})).encode()
    evidence_path.write_bytes(raw)
    report = recovery.recover_pdf(
        source, output, evidence_path=evidence_path, reader_factory=_factory(FakeReader([HEALTHY])))
    assert report["evidence_sha256"] == hashlib.sha256(raw).hexdigest()
    assert report["pages"][0]["original_text"] == "provided"


def test_evidence_source_mismatch_fails_before_reading_pages_or_publishing(recovery_files):
    source, output = recovery_files
    evidence_path = source.parent / "evidence.json"
    evidence_path.write_text(json.dumps(_evidence(digest="0" * 64)), encoding="utf-8")
    reader = FakeReader(["short"])
    with pytest.raises(ValueError, match="different PDF"):
        recovery.recover_pdf(source, output, evidence_path=evidence_path, reader_factory=_factory(reader))
    assert reader.closed
    assert not reader.native_calls and not reader.retry_calls
    assert not output.exists()


@pytest.mark.parametrize("target", ["source", "snapshot", "evidence"])
def test_input_mutation_during_retry_prevents_publication(recovery_files, target):
    source, output = recovery_files
    evidence_path = source.parent / "evidence.json"
    evidence_path.write_text(json.dumps(_evidence()), encoding="utf-8")

    def mutate(reader, page):
        if target == "source":
            source.write_bytes(b"changed PDF generation")
        elif target == "snapshot":
            reader.path.write_bytes(b"changed snapshot bytes")
        else:
            evidence_path.write_text(json.dumps(_evidence()) + "\n", encoding="utf-8")

    reader = FakeReader(["short"], on_retry=mutate)
    with pytest.raises(RuntimeError):
        recovery.recover_pdf(source, output, evidence_path=evidence_path, reader_factory=_factory(reader))
    assert reader.closed
    assert not output.exists()


def test_reader_close_failure_prevents_publication(recovery_files):
    source, output = recovery_files

    def fail_close(reader):
        raise RuntimeError("PRIVATE_CLOSE")

    reader = FakeReader(["short"], on_close=fail_close)
    with pytest.raises(RuntimeError, match="PRIVATE_CLOSE"):
        recovery.recover_pdf(source, output, reader_factory=_factory(reader))
    assert reader.closed
    assert not output.exists()


def test_recover_cancellation_closes_reader_and_cleans_snapshot(recovery_files):
    source, output = recovery_files
    reader = FakeReader(["short"], candidates={1: KeyboardInterrupt()})
    with pytest.raises(KeyboardInterrupt):
        recovery.recover_pdf(source, output, reader_factory=_factory(reader))
    assert reader.closed and reader.exit_error is KeyboardInterrupt
    assert not reader.path.exists()
    assert not output.exists()


def test_existing_output_is_never_overwritten_and_reader_is_not_opened(recovery_files):
    source, output = recovery_files
    output.write_bytes(b"prior report")
    reader = FakeReader(["short"])
    with pytest.raises(FileExistsError):
        recovery.recover_pdf(source, output, reader_factory=_factory(reader))
    assert output.read_bytes() == b"prior report"
    assert not reader.entered


def test_output_appearing_during_retry_is_not_overwritten(recovery_files):
    source, output = recovery_files
    reader = FakeReader(["short"], on_retry=lambda *_: output.write_bytes(b"concurrent report"))
    with pytest.raises(FileExistsError):
        recovery.recover_pdf(source, output, reader_factory=_factory(reader))
    assert output.read_bytes() == b"concurrent report"
    assert reader.closed


def test_final_publication_does_not_clobber_uncooperative_writer(recovery_files, monkeypatch):
    source, output = recovery_files
    actual_publish = recovery._publish_new_report

    def race(temporary, destination):
        assert not destination.exists()
        destination.write_bytes(b"uncooperative writer owns this file")
        actual_publish(temporary, destination)

    monkeypatch.setattr(recovery, "_publish_new_report", race)
    with pytest.raises(FileExistsError):
        recovery.recover_pdf(source, output, reader_factory=_factory(FakeReader(["short"])))
    assert output.read_bytes() == b"uncooperative writer owns this file"
    assert source.read_bytes() == b"synthetic PDF generation"
    assert not list(output.parent.glob(f".{output.name}.*.tmp"))


def test_publication_fails_closed_without_filesystem_hardlink_support(recovery_files, monkeypatch):
    source, output = recovery_files

    def unavailable(*args, **kwargs):
        raise OSError("hard links are unavailable")

    monkeypatch.setattr(recovery.os, "link", unavailable)
    with pytest.raises(OSError, match="unavailable"):
        recovery.recover_pdf(source, output, reader_factory=_factory(FakeReader(["short"])))
    assert not output.exists()
    assert source.read_bytes() == b"synthetic PDF generation"
    assert not list(output.parent.glob(f".{output.name}.*.tmp"))


def test_output_cannot_alias_source_or_evidence(recovery_files):
    source, output = recovery_files
    reader = FakeReader(["short"])
    with pytest.raises(ValueError, match="differ"):
        recovery.recover_pdf(source, source, reader_factory=_factory(reader))
    evidence_path = source.parent / "evidence.json"
    evidence_path.write_text(json.dumps(_evidence()), encoding="utf-8")
    with pytest.raises(ValueError, match="differ"):
        recovery.recover_pdf(source, evidence_path, evidence_path=evidence_path, reader_factory=_factory(reader))
    assert not reader.entered
    assert source.read_bytes() == b"synthetic PDF generation"


def test_hardlink_output_is_refused_without_changing_source(recovery_files):
    source, output = recovery_files
    try:
        os.link(source, output)
    except OSError:
        pytest.skip("hard links unavailable")
    with pytest.raises((FileExistsError, ValueError)):
        recovery.recover_pdf(source, output, reader_factory=_factory(FakeReader(["short"])))
    assert source.read_bytes() == b"synthetic PDF generation"


@pytest.mark.parametrize("target", ["source", "output", "evidence"])
def test_symlink_input_or_output_is_refused(recovery_files, target):
    source, output = recovery_files
    evidence_path = source.parent / "evidence.json"
    evidence_path.write_text(json.dumps(_evidence()), encoding="utf-8")
    link = source.parent / "linked"
    try:
        link.symlink_to({"source": source, "output": output, "evidence": evidence_path}[target])
    except OSError:
        pytest.skip("symbolic links unavailable")
    with pytest.raises(storage_policy.StoragePolicyError):
        recovery.recover_pdf(
            link if target == "source" else source,
            link if target == "output" else output,
            evidence_path=link if target == "evidence" else evidence_path,
            reader_factory=_factory(FakeReader(["short"])),
        )
    assert not output.exists()


@pytest.mark.parametrize("raw", [
    b'{"PRIVATE_FIELD":1,"PRIVATE_FIELD":2}', b'{"schema_version":NaN}',
    b'[]', b'\xff',
])
def test_strict_evidence_file_fails_before_opening_reader(recovery_files, raw):
    source, output = recovery_files
    evidence_path = source.parent / "evidence.json"
    evidence_path.write_bytes(raw)
    reader = FakeReader(["short"])
    with pytest.raises(ValueError):
        recovery.recover_pdf(source, output, evidence_path=evidence_path, reader_factory=_factory(reader))
    assert not reader.entered
    assert not output.exists()


def test_evidence_read_is_size_bounded(recovery_files, monkeypatch):
    source, output = recovery_files
    evidence_path = source.parent / "evidence.json"
    evidence_path.write_text(json.dumps(_evidence()), encoding="utf-8")
    monkeypatch.setattr(recovery, "MAX_EVIDENCE_BYTES", 10)
    with pytest.raises(ValueError, match="exceeds"):
        recovery.recover_pdf(source, output, evidence_path=evidence_path, reader_factory=_factory(FakeReader(["short"])))
    assert not output.exists()


@pytest.mark.parametrize("error", [
    ValueError("PRIVATE_TEXT"), OSError("PRIVATE_PATH"), RuntimeError("PRIVATE_MODEL"),
    ImportError("PRIVATE_MODEL"), FileExistsError("PRIVATE_PATH"),
])
def test_cli_sanitizes_error_paths_and_messages(monkeypatch, capsys, error):
    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(cli, "recover_pdf", fail)
    assert cli.main(["--pdf", "PRIVATE_SOURCE.pdf", "--output", "PRIVATE_OUTPUT.json"]) == 2
    captured = capsys.readouterr()
    assert "PRIVATE_" not in captured.out + captured.err
    assert "OCR retry" in captured.err


@pytest.mark.parametrize(("summary", "expected"), [
    ({"selected": 0, "deferred": 0, "review_required": 0, "empty": 0, "failed": 0}, 0),
    ({"selected": 1, "deferred": 0, "review_required": 1, "empty": 0, "failed": 0}, 0),
    ({"selected": 1, "deferred": 1, "review_required": 1, "empty": 0, "failed": 0}, 3),
    ({"selected": 1, "deferred": 0, "review_required": 0, "empty": 1, "failed": 0}, 3),
    ({"selected": 1, "deferred": 0, "review_required": 0, "empty": 0, "failed": 1}, 3),
])
def test_cli_partial_exit_code_and_accuracy_language(monkeypatch, capsys, summary, expected):
    monkeypatch.setattr(cli, "recover_pdf", lambda *args, **kwargs: {"summary": summary, "private": "PRIVATE_TEXT"})
    assert cli.main(["--pdf", "PRIVATE_SOURCE.pdf", "--output", "PRIVATE_OUTPUT.json"]) == expected
    captured = capsys.readouterr()
    assert "PRIVATE_" not in captured.out + captured.err
    assert "Original extraction unchanged" in captured.out
    assert "accuracy" in captured.out


def test_cli_forwards_requested_pages_policy_and_evidence(monkeypatch):
    captured = []

    def recover(*args, **kwargs):
        captured.append((args, kwargs))
        return {"summary": {"selected": 0, "deferred": 0, "review_required": 0, "empty": 0, "failed": 0}}

    monkeypatch.setattr(cli, "recover_pdf", recover)
    assert cli.main(["--pdf", "source.pdf", "--output", "report.json", "--evidence", "evidence.json", "--pages", "4", "2", "--max-pages", "2", "--dpi", "400"]) == 0
    args, kwargs = captured[0]
    assert args == (Path("source.pdf"), Path("report.json"))
    assert kwargs["requested_pages"] == (4, 2)
    assert kwargs["evidence_path"] == Path("evidence.json")
    assert kwargs["policy"].dpi == 400
    assert kwargs["policy"].max_pages == 2


def test_cli_does_not_swallow_cancellation(monkeypatch):
    def cancel(*args, **kwargs):
        raise KeyboardInterrupt()

    monkeypatch.setattr(cli, "recover_pdf", cancel)
    with pytest.raises(KeyboardInterrupt):
        cli.main(["--pdf", "source.pdf", "--output", "report.json"])


def test_no_clobber_commit_reports_post_commit_cleanup_failure(tmp_path, monkeypatch):
    staging = tmp_path / "synthetic.tmp"
    destination = tmp_path / "report.json"
    staging.write_bytes(b"synthetic report")

    def fail_unlink(self, *args, **kwargs):
        raise PermissionError("PRIVATE_PATH")

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", fail_unlink)
        with pytest.raises(recovery.ReportCleanupError):
            recovery._publish_new_report(staging, destination)
    assert destination.read_bytes() == b"synthetic report"
    assert staging.read_bytes() == b"synthetic report"


def test_cli_distinguishes_committed_report_cleanup_failure(monkeypatch, capsys):
    def fail(*args, **kwargs):
        raise recovery.ReportCleanupError("PRIVATE_PATH")

    monkeypatch.setattr(cli, "recover_pdf", fail)
    assert cli.main(["--pdf", "source.pdf", "--output", "report.json"]) == 2
    error = capsys.readouterr().err
    assert "report was created" in error
    assert "Inspect --output" in error
    assert "PRIVATE_PATH" not in error
