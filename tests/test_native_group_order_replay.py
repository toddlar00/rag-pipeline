"""Gate-driven replay control for deferred native text groups.

A first chunking pass defers every proven multi-block overlapping group, so it
is the previous pipeline.  Only when its published quality report fails
``same_page_reading_order`` on an edge whose ``before_ref`` is a deferred
member on that member's page does ``chunk_document`` replay the chunk once,
admitting exactly those groups, under the same path-wide chunk lease, and
records that replay as a run telemetry observation.  Every failure still
propagates as the previous plain ``RuntimeError``, so run reports keep
recording its type.
"""

import contextlib
import dataclasses

import pytest

import rag


GATE_MESSAGE = "Corpus quality gate failed: same_page_reading_order"
DEFERRED = (rag.SourceTextGroupRecovery(
    text="deferred run", refs=("#/texts/37", "#/texts/42"), page=4),)
NAMED = frozenset({("#/texts/42", 4)})
NAMED_EDGE = ("#/texts/42", "#/texts/39", 4)


def _failed_report(*edges):
    return {
        "status": "fail",
        "checks": [{"name": "same_page_reading_order", "status": "fail"}],
        "source_lineage": {"fidelity": {"geometry_violations": [
            {"before_ref": before, "after_ref": after, "page": page}
            for before, after, page in edges
        ]}},
    }


def test_replay_violations_name_deferred_members_that_must_come_first():
    """Any failed edge kind qualifies: a vertical or a lane-transition edge."""
    report = _failed_report(
        NAMED_EDGE, ("#/texts/9", "#/texts/10", 4))

    assert rag._native_group_order_replay_violations(
        report, DEFERRED) == NAMED


@pytest.mark.parametrize(("report", "deferred"), [
    # A member only as the later item: recovery cannot move it earlier.
    (_failed_report(("#/texts/39", "#/texts/42", 4)), DEFERRED),
    (_failed_report(("#/texts/42", "#/texts/39", 5)), DEFERRED),
    (_failed_report(NAMED_EDGE), ()),
    # A failed report without any reading-order violation.
    (_failed_report(), DEFERRED),
    ({"status": "fail", "checks": []}, DEFERRED),
])
def test_replay_violations_require_a_named_deferred_member(report, deferred):
    assert rag._native_group_order_replay_violations(
        report, deferred) == frozenset()


def _publisher(report=None, *, raised=None):
    """Stand in for quality publication; a report reaches the failure sink."""
    received = []

    def publish(failed_reports):
        received.append(failed_reports)
        if raised is None:
            return report
        if report is not None:
            failed_reports.append(report)
        raise raised

    return publish, received


def test_passing_publication_returns_its_report_without_a_request():
    report = {"status": "pass"}
    publish, received = _publisher(report)
    requests = []

    assert rag._publish_quality_report_or_request_order_replay(
        publish, DEFERRED, requests) is report
    assert received == [[]]
    assert requests == []


def test_named_gate_failure_requests_one_replay_and_propagates_unchanged():
    raised = RuntimeError(GATE_MESSAGE)
    publish, _ = _publisher(_failed_report(NAMED_EDGE), raised=raised)
    requests = []

    with pytest.raises(RuntimeError) as failure:
        rag._publish_quality_report_or_request_order_replay(
            publish, DEFERRED, requests)

    assert failure.value is raised
    assert requests == [NAMED]


@pytest.mark.parametrize(("report", "requests"), [
    (_failed_report(("#/texts/39", "#/texts/42", 4)), []),
    # A publication error raised before any report was built.
    (None, []),
    # The replay pass records nothing, even for a named edge.
    (_failed_report(NAMED_EDGE), None),
])
def test_other_publication_failures_propagate_without_a_request(
        report, requests):
    raised = RuntimeError(GATE_MESSAGE)
    publish, _ = _publisher(report, raised=raised)

    with pytest.raises(RuntimeError) as failure:
        rag._publish_quality_report_or_request_order_replay(
            publish, DEFERRED, requests)

    assert failure.value is raised
    assert requests in ([], None)


def _record_chunk_calls(monkeypatch, outcomes):
    """Stub the locked pass; each outcome is ``(request, error)``."""
    calls = []

    def chunk_locked(doc_path, chunks_output, **kwargs):
        calls.append(dict(kwargs))
        request, error = outcomes[len(calls) - 1]
        if request is not None:
            kwargs["order_replay_requests"].append(request)
        if error is not None:
            raise error

    monkeypatch.setattr(rag, "_chunk_document_locked", chunk_locked)
    return calls


def _started_telemetry():
    telemetry = rag._run_telemetry.RunTelemetry("chunk")
    telemetry.start()
    return telemetry


def test_chunk_document_replays_a_named_failure_once_outside_its_handler(
        monkeypatch, tmp_path):
    replay_failure = RuntimeError(GATE_MESSAGE)
    calls = _record_chunk_calls(monkeypatch, [
        (NAMED, RuntimeError(GATE_MESSAGE)), (None, replay_failure)])
    telemetry = _started_telemetry()

    with pytest.raises(RuntimeError) as failure:
        rag.chunk_document(
            tmp_path / "book.json", tmp_path / "book_chunks.jsonl",
            telemetry=telemetry)

    assert failure.value is replay_failure
    # The replay's own failure is reported as a plain, unchained failure.
    assert failure.value.__context__ is None
    first, replay = calls
    assert first.pop("telemetry") is telemetry
    assert first.pop("order_replay_requests") == [NAMED]
    # The first pass already recorded its telemetry observation.
    assert replay.pop("telemetry") is None
    assert replay.pop("reading_order_violations") == NAMED
    assert replay == first


def test_chunk_document_returns_after_a_successful_replay(
        monkeypatch, tmp_path):
    calls = _record_chunk_calls(monkeypatch, [
        (NAMED, RuntimeError(GATE_MESSAGE)), (None, None)])

    assert rag.chunk_document(
        tmp_path / "book.json", tmp_path / "book_chunks.jsonl") is None
    assert len(calls) == 2


@pytest.mark.parametrize("error", [None, RuntimeError(GATE_MESSAGE)])
def test_chunk_document_runs_one_pass_without_a_replay_request(
        monkeypatch, tmp_path, error):
    calls = _record_chunk_calls(monkeypatch, [(None, error)])
    telemetry = _started_telemetry()

    if error is None:
        rag.chunk_document(
            tmp_path / "book.json", tmp_path / "book_chunks.jsonl",
            telemetry=telemetry)
    else:
        with pytest.raises(RuntimeError) as failure:
            rag.chunk_document(
                tmp_path / "book.json", tmp_path / "book_chunks.jsonl",
                telemetry=telemetry)
        assert failure.value is error
    assert len(calls) == 1
    assert "chunk_order_replay" not in telemetry.report_payload()["stages"]


@pytest.mark.parametrize("replay_error", [None, RuntimeError(GATE_MESSAGE)])
def test_chunk_document_replays_under_the_first_pass_lease(
        monkeypatch, tmp_path, replay_error):
    """One path-wide lease spans both publications, even a failed replay."""
    calls = _record_chunk_calls(monkeypatch, [
        (NAMED, RuntimeError(GATE_MESSAGE)), (None, replay_error)])
    leases = []

    @contextlib.contextmanager
    def lease(path):
        leases.append(("acquired", path, len(calls)))
        try:
            yield
        finally:
            leases.append(("released", path, len(calls)))

    monkeypatch.setattr(rag, "_chunk_output_lease", lease)
    chunks = tmp_path / "book_chunks.jsonl"

    if replay_error is None:
        rag.chunk_document(tmp_path / "book.json", chunks)
    else:
        with pytest.raises(RuntimeError) as failure:
            rag.chunk_document(tmp_path / "book.json", chunks)
        assert failure.value is replay_error
    # Acquired before the first pass; released only after the replay.
    assert leases == [("acquired", chunks, 0), ("released", chunks, 2)]


@pytest.mark.parametrize("replay_error", [None, RuntimeError(GATE_MESSAGE)])
def test_chunk_document_records_the_replay_before_running_it(
        monkeypatch, tmp_path, replay_error):
    """A run report shows the replay, even when the replay itself fails."""
    request = frozenset({("#/texts/37", 4), ("#/texts/42", 4)})
    _record_chunk_calls(monkeypatch, [
        (request, RuntimeError(GATE_MESSAGE)), (None, replay_error)])
    telemetry = _started_telemetry()

    with contextlib.suppress(RuntimeError):
        rag.chunk_document(
            tmp_path / "book.json", tmp_path / "book_chunks.jsonl",
            telemetry=telemetry)

    stage = telemetry.report_payload()["stages"]["chunk_order_replay"]
    assert stage["completed"] == 1
    assert stage["metrics"]["reading_order_violations"]["latest"] == 2


def _stub_chunk_models(monkeypatch):
    """Keep the real locked pass; replace only its model-backed parts."""
    chunkers = pytest.importorskip("docling_core.transforms.chunker")
    tokenizers = pytest.importorskip(
        "docling_core.transforms.chunker.tokenizer.huggingface")

    class Tokenizer:
        def count_tokens(self, text):
            return rag._conservative_token_estimate(text)

    class Chunker:
        def __init__(self, *, tokenizer, **_kwargs):
            self.tokenizer = tokenizer
            self.hierarchy = chunkers.HierarchicalChunker()

        def chunk(self, dl_doc):
            return self.hierarchy.chunk(dl_doc)

    monkeypatch.setattr(
        rag, "_model_loader_source", lambda *_args, **_kwargs: ("stub", True))
    monkeypatch.setattr(
        tokenizers.HuggingFaceTokenizer, "from_pretrained",
        classmethod(lambda _cls, **_kwargs: Tokenizer()))
    monkeypatch.setattr(chunkers, "HybridChunker", Chunker)
    monkeypatch.setattr(
        rag, "_count_embedding_text_tokens", lambda texts, _model: (
            [rag._conservative_token_estimate(text) for text in texts],
            False))


def _write_document(path):
    doc = pytest.importorskip("docling_core.types.doc")
    text = "A short paragraph of body text."
    document = doc.DoclingDocument(name="book")
    document.add_page(page_no=1, size=doc.Size(width=612, height=792))
    document.add_text(
        label=doc.DocItemLabel.TEXT, text=text, prov=doc.ProvenanceItem(
            page_no=1, charspan=(0, len(text)), bbox=doc.BoundingBox(
                l=72, t=100, r=420, b=150,
                coord_origin=doc.CoordOrigin.TOPLEFT)))
    document.save_as_json(path)


@pytest.mark.parametrize(("edge", "passes"), [
    (NAMED_EDGE, 2),
    (("#/texts/39", "#/texts/42", 4), 1),
])
def test_locked_pass_turns_a_named_gate_failure_into_one_replay(
        monkeypatch, tmp_path, edge, passes):
    """Drive the real locked pass through enrichment and publication."""
    _stub_chunk_models(monkeypatch)
    document = tmp_path / "book.json"
    _write_document(document)
    violations = []
    recover_enrichments = rag._recover_bound_source_enrichments

    def enrichments(*args, **kwargs):
        violations.append(kwargs["reading_order_violations"])
        return dataclasses.replace(
            recover_enrichments(*args, **kwargs),
            deferred_text_groups=DEFERRED)

    reports = []

    def build_report(**_kwargs):
        reports.append(_failed_report(edge))
        return reports[-1]

    monkeypatch.setattr(rag, "_recover_bound_source_enrichments", enrichments)
    monkeypatch.setattr(
        rag._quality_core, "build_quality_report", build_report)

    with pytest.raises(RuntimeError, match=f"^{GATE_MESSAGE}$") as failure:
        rag.chunk_document(
            document, tmp_path / "book_chunks.jsonl",
            structure_profile="us-law-casebook-excerpt-v1")

    assert type(failure.value) is RuntimeError
    assert rag._run_telemetry.failure_diagnostic(
        failure.value)["error_type"] == "RuntimeError"
    assert violations == [frozenset(), NAMED][:passes]
    assert len(reports) == passes
