"""Gate-driven replay placing footnotes by the geometry the audit observes.

A tokenless body entry, such as an ornament row printed among the notes,
widens its record's page box although the fidelity audit never registers it.
The first chunking pass is the previous pipeline: a note that the widened box
overlaps falls back to semantic placement, which a page-end continuation can
move past the next page's body.  Only when its published quality report fails
``same_page_reading_order`` naming a footnote sidecar does ``chunk_document``
replay the chunk once, placing the footnotes on only those pages by the
lineage the audit observes.  The deferred-group order replay and the list
boundary replay take precedence, so no chunk replays twice.
``test_footnote_placement_geometry`` pins the placement itself.  All text is
synthetic.
"""

import contextlib

import pytest

import rag


GATE_MESSAGE = "Corpus quality gate failed: same_page_reading_order"
PAGES = frozenset({5})
NOTE = "#/texts/U10"
BODY = "#/texts/A0"
ORDER_REQUEST = frozenset({("#/texts/42", 4)})
LIST_REQUEST = frozenset({"#/texts/1"})


def _report(edges, *, status="fail"):
    return {
        "status": "fail",
        "checks": [{"name": "same_page_reading_order", "status": status}],
        "source_lineage": {"fidelity": {"geometry_violations": [
            {"before_ref": before, "after_ref": after, "page": page}
            for before, after, page in edges] + ["not a violation"]}},
    }


@pytest.mark.parametrize(("report", "expected"), [
    (_report([(NOTE, "#/texts/L10", 5)]), PAGES),
    (_report([(BODY, NOTE, 5)]), PAGES),
    (_report([(NOTE, BODY, 5), (BODY, NOTE, 7)]), frozenset({5, 7})),
    # A violation between body records does not name a footnote sidecar.
    (_report([(BODY, "#/texts/B0", 5)]), frozenset()),
    # The check itself must fail.
    (_report([(NOTE, BODY, 5)], status="pass"), frozenset()),
    ({"status": "fail", "checks": []}, frozenset()),
    ({"status": "fail", "checks": [
        {"name": "same_page_reading_order", "status": "fail"}]}, frozenset()),
    (_report([(NOTE, BODY, True), (NOTE, BODY, "5")]), frozenset()),
    # A malformed, unhashable ref never turns the gate failure into a
    # TypeError.
    (_report([([NOTE], BODY, 5), (BODY, {"ref": NOTE}, 5)]), frozenset()),
])
def test_trigger_names_pages_whose_violations_name_a_footnote(
        report, expected):
    assert rag._footnote_placement_replay_pages(
        report, frozenset({NOTE, "#/texts/L10"})) == expected


def _publisher(report=None, *, raised=None):
    def publish(failed_reports):
        if raised is None:
            return report
        if report is not None:
            failed_reports.append(report)
        raise raised
    return publish


def _publish(publish, *, footnotes, lists=None):
    return rag._publish_quality_report_or_request_order_replay(
        publish, (), [], list_replay_requests=lists,
        footnote_refs=frozenset({NOTE}), footnote_replay_requests=footnotes)


def test_passing_publication_records_no_footnote_request():
    report = {"status": "pass"}
    footnotes = []

    assert _publish(_publisher(report), footnotes=footnotes) is report
    assert footnotes == []


def test_named_footnote_failure_records_one_request_and_propagates():
    raised = RuntimeError(GATE_MESSAGE)
    footnotes = []

    with pytest.raises(RuntimeError) as failure:
        _publish(_publisher(_report([(NOTE, BODY, 5)]), raised=raised),
                 footnotes=footnotes, lists=[])

    assert failure.value is raised
    assert footnotes == [PAGES]


@pytest.mark.parametrize(("report", "footnotes", "expected"), [
    # A publication error raised before any report was built.
    (None, [], []),
    # The replay pass gets no sink, so it can never request another replay.
    (_report([(NOTE, BODY, 5)]), None, None),
    (_report([(BODY, "#/texts/B0", 5)]), [], []),
])
def test_other_failures_record_no_footnote_request(report, footnotes, expected):
    raised = RuntimeError(GATE_MESSAGE)

    with pytest.raises(RuntimeError) as failure:
        _publish(_publisher(report, raised=raised), footnotes=footnotes)

    assert failure.value is raised
    assert footnotes == expected


def _record_chunk_calls(monkeypatch, outcomes):
    """Stub the locked pass; each outcome is ``(requests, error)``."""
    calls = []
    sinks = ("order_replay_requests", "list_replay_requests",
             "footnote_replay_requests")

    def chunk_locked(doc_path, chunks_output, **kwargs):
        calls.append(dict(kwargs))
        requests, error = outcomes[len(calls) - 1]
        for sink, request in zip(sinks, requests or ()):
            if request is not None:
                kwargs[sink].append(request)
        if error is not None:
            raise error

    monkeypatch.setattr(rag, "_chunk_document_locked", chunk_locked)
    return calls


def _telemetry():
    telemetry = rag._run_telemetry.RunTelemetry("chunk")
    telemetry.start()
    return telemetry


def _chunk(tmp_path, telemetry=None):
    rag.chunk_document(
        tmp_path / "book.json", tmp_path / "book_chunks.jsonl",
        telemetry=telemetry)


def _first_pass_arguments(call):
    for key in ("order_replay_requests", "list_replay_requests",
                "footnote_replay_requests"):
        call.pop(key)
    return call


@pytest.mark.parametrize("replay_error", [None, RuntimeError(GATE_MESSAGE)])
def test_footnote_request_replays_once_on_only_those_pages(
        monkeypatch, tmp_path, replay_error):
    calls = _record_chunk_calls(monkeypatch, [
        ((None, None, frozenset({5, 7})), RuntimeError(GATE_MESSAGE)),
        (None, replay_error)])
    telemetry = _telemetry()

    try:
        _chunk(tmp_path, telemetry)
    except RuntimeError as failure:
        assert failure is replay_error
        # The replay's own failure is reported plainly, unchained.
        assert failure.__context__ is None
    else:
        assert replay_error is None

    first, replay = calls
    assert first.pop("telemetry") is telemetry
    assert first["footnote_replay_requests"] == [frozenset({5, 7})]
    # The replay has no sink, so no pass can request a second replay.
    assert replay.pop("telemetry") is None
    assert replay.pop("observed_footnote_geometry_pages") == frozenset({5, 7})
    assert replay == _first_pass_arguments(first)
    stage = telemetry.report_payload()["stages"][
        "chunk_footnote_placement_replay"]
    assert stage["completed"] == 1
    assert stage["metrics"]["footnote_pages"]["latest"] == 2


def test_first_pass_that_publishes_is_not_replayed(monkeypatch, tmp_path):
    calls = _record_chunk_calls(monkeypatch, [((None, None, PAGES), None)])
    telemetry = _telemetry()

    _chunk(tmp_path, telemetry)

    assert len(calls) == 1
    assert "chunk_footnote_placement_replay" not in (
        telemetry.report_payload()["stages"])


@pytest.mark.parametrize(("requests", "replayed", "stage"), [
    ((ORDER_REQUEST, None, PAGES), ("reading_order_violations",
                                    ORDER_REQUEST), "chunk_order_replay"),
    ((None, LIST_REQUEST, PAGES), ("separated_list_refs", LIST_REQUEST),
     "chunk_list_boundary_replay"),
])
def test_order_and_list_requests_take_precedence_over_footnotes(
        monkeypatch, tmp_path, requests, replayed, stage):
    """Those replays run exactly as before; the footnote request is dropped."""
    calls = _record_chunk_calls(monkeypatch, [
        (requests, RuntimeError(GATE_MESSAGE)), (None, None)])
    telemetry = _telemetry()

    _chunk(tmp_path, telemetry)

    first, replay = calls
    first.pop("telemetry")
    assert replay.pop("telemetry") is None
    assert replay.pop(replayed[0]) == replayed[1]
    assert replay == _first_pass_arguments(first)
    stages = telemetry.report_payload()["stages"]
    assert stage in stages
    assert "chunk_footnote_placement_replay" not in stages


@pytest.mark.parametrize("replay_error", [None, RuntimeError(GATE_MESSAGE)])
def test_footnote_replay_runs_under_the_first_pass_lease(
        monkeypatch, tmp_path, replay_error):
    calls = _record_chunk_calls(monkeypatch, [
        ((None, None, PAGES), RuntimeError(GATE_MESSAGE)),
        (None, replay_error)])
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

    with contextlib.suppress(RuntimeError):
        rag.chunk_document(tmp_path / "book.json", chunks)

    assert leases == [("acquired", chunks, 0), ("released", chunks, 2)]
