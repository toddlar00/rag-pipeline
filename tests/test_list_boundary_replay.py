"""Gate-driven replay for list items joined inline after a lead-in.

Boundary repair joins a continuation inline, so a record that opens with a
source list item's punctuation marker has that marker placed mid-line, where
the fidelity audit does not accept it.  The first chunking pass is the
previous pipeline: it only observes such joins.  Only when its published
quality report fails ``source_token_fidelity`` naming an observed item among
``source_coverage_issues`` does ``chunk_document`` replay the chunk once,
separating exactly those joins.  A deferred-group order replay takes
precedence and is replayed exactly as before, so no chunk replays twice.
``test_list_boundary_replay_locked`` drives the real locked pass.  All text
is synthetic.
"""

import contextlib

import pytest

import rag


GATE_MESSAGE = "Corpus quality gate failed: source_token_fidelity"
ITEM = "#/texts/1"
REFS = frozenset({ITEM})
TWO_REFS = frozenset({ITEM, "#/texts/5"})
NAMED = frozenset({("#/texts/42", 4)})
NAMED_EDGE = ("#/texts/42", "#/texts/39", 4)
DEFERRED = (rag.SourceTextGroupRecovery(
    text="deferred run", refs=("#/texts/37", "#/texts/42"), page=4),)
LEAD = ("The court considered several earlier decisions on the same "
        "question, for example,")
ALPHA = "Alpha v. Beta held that notice was required."


def _report(*, fidelity_status="fail", uncovered=(ITEM,), edges=()):
    return {
        "status": "fail",
        "checks": [
            {"name": "source_token_fidelity", "status": fidelity_status},
            {"name": "same_page_reading_order",
             "status": "fail" if edges else "pass"}],
        "source_lineage": {"fidelity": {
            "source_coverage_issues": list(uncovered),
            "geometry_violations": [
                {"before_ref": before, "after_ref": after, "page": page}
                for before, after, page in edges]}},
    }


@pytest.mark.parametrize(("report", "observed", "expected"), [
    (_report(), REFS, REFS),
    (_report(uncovered=(ITEM, "#/texts/9")), REFS | {"#/texts/5"}, REFS),
    # Uncovered, but no inline join placed its marker mid-line.
    (_report(uncovered=("#/texts/9",)), REFS, frozenset()),
    (_report(), frozenset(), frozenset()),
    # The check itself must fail; the issue list alone does not trigger.
    (_report(fidelity_status="pass"), REFS, frozenset()),
    # Another failing check does not stand in for the fidelity check.
    (_report(fidelity_status="pass", edges=[NAMED_EDGE]), REFS, frozenset()),
    ({"status": "fail", "checks": []}, REFS, frozenset()),
    ({"status": "fail", "checks": [
        {"name": "source_token_fidelity", "status": "fail"}]},
     REFS, frozenset()),
    (_report(uncovered=(7, None, ITEM)), REFS, REFS),
])
def test_trigger_names_observed_items_the_failed_report_leaves_uncovered(
        report, observed, expected):
    assert rag._list_boundary_replay_refs(report, observed) == expected


def _publisher(report=None, *, raised=None):
    def publish(failed_reports):
        if raised is None:
            return report
        if report is not None:
            failed_reports.append(report)
        raise raised
    return publish


def _publish(publish, *, order=None, lists=None, observed=REFS,
             deferred=()):
    return rag._publish_quality_report_or_request_order_replay(
        publish, deferred, order,
        inline_list_joins=observed, list_replay_requests=lists)


def test_passing_publication_records_no_list_request():
    report = {"status": "pass"}
    order, lists = [], []

    assert _publish(_publisher(report), order=order, lists=lists) is report
    assert (order, lists) == ([], [])


# The report names only ITEM, so an observed join it leaves unnamed
# ("#/texts/5") is not requested and stays inline in the replay.
@pytest.mark.parametrize("observed", [REFS, TWO_REFS])
def test_named_list_failure_records_one_request_and_propagates_unchanged(
        observed):
    raised = RuntimeError(GATE_MESSAGE)
    order, lists = [], []

    with pytest.raises(RuntimeError) as failure:
        _publish(_publisher(_report(), raised=raised), order=order,
                 lists=lists, observed=observed)

    assert failure.value is raised
    assert (order, lists) == ([], [REFS])


def test_wrapper_records_both_requests_and_leaves_precedence_to_the_caller():
    raised = RuntimeError(GATE_MESSAGE)
    order, lists = [], []

    with pytest.raises(RuntimeError):
        _publish(_publisher(_report(edges=[NAMED_EDGE]), raised=raised),
                 order=order, lists=lists, deferred=DEFERRED)

    assert (order, lists) == ([NAMED], [REFS])


@pytest.mark.parametrize(("report", "lists", "observed"), [
    # A publication error raised before any report was built.
    (None, [], REFS),
    # The replay pass gets no sink, so it can never request another replay.
    (_report(), None, REFS),
    (_report(), [], frozenset()),
])
def test_other_failures_record_no_list_request(report, lists, observed):
    raised = RuntimeError(GATE_MESSAGE)

    with pytest.raises(RuntimeError) as failure:
        _publish(_publisher(report, raised=raised), order=[], lists=lists,
                 observed=observed)

    assert failure.value is raised
    assert lists in ([], None)


def _record_chunk_calls(monkeypatch, outcomes):
    """Stub the locked pass; each outcome is ``(order, lists, error)``."""
    calls = []

    def chunk_locked(doc_path, chunks_output, **kwargs):
        calls.append(dict(kwargs))
        order, lists, error = outcomes[len(calls) - 1]
        if order is not None:
            kwargs["order_replay_requests"].append(order)
        if lists is not None:
            kwargs["list_replay_requests"].append(lists)
        if error is not None:
            raise error

    monkeypatch.setattr(rag, "_chunk_document_locked", chunk_locked)
    return calls


def _telemetry():
    telemetry = rag._run_telemetry.RunTelemetry("chunk")
    telemetry.start()
    return telemetry


@pytest.mark.parametrize("replay_error", [None, RuntimeError(GATE_MESSAGE)])
def test_list_request_replays_once_separating_only_those_joins(
        monkeypatch, tmp_path, replay_error):
    calls = _record_chunk_calls(monkeypatch, [
        (None, TWO_REFS, RuntimeError(GATE_MESSAGE)),
        (None, None, replay_error)])
    telemetry = _telemetry()

    try:
        rag.chunk_document(
            tmp_path / "book.json", tmp_path / "book_chunks.jsonl",
            telemetry=telemetry)
    except RuntimeError as failure:
        assert failure is replay_error
    else:
        assert replay_error is None

    first, replay = calls
    assert first.pop("telemetry") is telemetry
    assert first.pop("order_replay_requests") == []
    assert first.pop("list_replay_requests") == [TWO_REFS]
    # The replay has no sink, so no pass can request a second replay.
    assert replay.pop("telemetry") is None
    assert replay.pop("separated_list_refs") == TWO_REFS
    assert replay == first


def test_first_pass_that_publishes_is_not_replayed(monkeypatch, tmp_path):
    """Only a raised gate failure replays; a recorded request alone does not.

    The publication hook records a request only just before re-raising, so
    this pins the dispatcher's success branch directly.
    """
    calls = _record_chunk_calls(monkeypatch, [(None, REFS, None)])
    telemetry = _telemetry()

    rag.chunk_document(
        tmp_path / "book.json", tmp_path / "book_chunks.jsonl",
        telemetry=telemetry)

    assert len(calls) == 1
    assert "chunk_list_boundary_replay" not in telemetry.report_payload()[
        "stages"]


@pytest.mark.parametrize("replay_error", [None, RuntimeError(GATE_MESSAGE)])
def test_list_replay_is_recorded_before_it_runs(
        monkeypatch, tmp_path, replay_error):
    """A run report shows the replay, even when the replay itself fails."""
    _record_chunk_calls(monkeypatch, [
        (None, TWO_REFS, RuntimeError(GATE_MESSAGE)),
        (None, None, replay_error)])
    telemetry = _telemetry()

    with contextlib.suppress(RuntimeError):
        rag.chunk_document(
            tmp_path / "book.json", tmp_path / "book_chunks.jsonl",
            telemetry=telemetry)

    stages = telemetry.report_payload()["stages"]
    stage = stages["chunk_list_boundary_replay"]
    assert stage["completed"] == 1
    assert stage["metrics"]["list_boundary_items"]["latest"] == 2
    assert "chunk_order_replay" not in stages


def test_replay_failure_propagates_unchained(monkeypatch, tmp_path):
    replay_failure = RuntimeError(GATE_MESSAGE)
    calls = _record_chunk_calls(monkeypatch, [
        (None, REFS, RuntimeError(GATE_MESSAGE)),
        (None, None, replay_failure)])

    with pytest.raises(RuntimeError) as failure:
        rag.chunk_document(
            tmp_path / "book.json", tmp_path / "book_chunks.jsonl")

    assert failure.value is replay_failure
    assert failure.value.__context__ is None
    assert len(calls) == 2


def test_order_request_takes_precedence_and_replays_exactly_as_before(
        monkeypatch, tmp_path):
    calls = _record_chunk_calls(monkeypatch, [
        (NAMED, REFS, RuntimeError(GATE_MESSAGE)), (None, None, None)])
    telemetry = _telemetry()

    rag.chunk_document(
        tmp_path / "book.json", tmp_path / "book_chunks.jsonl",
        telemetry=telemetry)

    first, replay = calls
    for key in ("telemetry", "order_replay_requests", "list_replay_requests"):
        first.pop(key)
    assert replay.pop("telemetry") is None
    assert replay.pop("reading_order_violations") == NAMED
    # No list refs reach the order replay, and it gets no sink either.
    assert replay == first
    stages = telemetry.report_payload()["stages"]
    assert "chunk_order_replay" in stages
    assert "chunk_list_boundary_replay" not in stages


@pytest.mark.parametrize("replay_error", [None, RuntimeError(GATE_MESSAGE)])
def test_list_replay_runs_under_the_first_pass_lease(
        monkeypatch, tmp_path, replay_error):
    calls = _record_chunk_calls(monkeypatch, [
        (None, REFS, RuntimeError(GATE_MESSAGE)), (None, None, replay_error)])
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


def _lineage(ref):
    return {"ref": ref, "label": "text" if ref == "#/texts/0" else "list_item",
            "spans": [{"provenance_index": 0}],
            "scope": {"provenance_indexes": [0]}}


def _record(text, refs):
    return {"text": text, "metadata": {
        "page_start": 1, "page_end": 1, "headings": ["Deference"],
        "content_type": "author_narrative", "content_source": "body",
        "source_items": [_lineage(ref) for ref in refs]}}


def _coalesce(records, **kwargs):
    return rag._coalesce_chunk_boundaries(
        records, lambda value: len(value.split()), 200, **kwargs)


def _lead_then_item(item_text=f"- {ALPHA}"):
    return [_record(LEAD, ["#/texts/0"]), _record(item_text, [ITEM])]


def test_first_pass_join_is_unchanged_and_only_observed():
    observed = set()
    baseline = _coalesce(_lead_then_item())
    joined = _coalesce(
        _lead_then_item(), list_markers={ITEM: "-"},
        inline_list_joins=observed)

    assert joined == baseline
    assert [record["text"] for record in joined] == [f"{LEAD} - {ALPHA}"]
    assert observed == {ITEM}


def test_replay_separates_only_the_named_join():
    observed = set()
    separated = _coalesce(
        _lead_then_item(), list_markers={ITEM: "-"},
        inline_list_joins=observed, separated_list_refs=REFS)
    other = _coalesce(
        _lead_then_item(), list_markers={ITEM: "-"},
        separated_list_refs=frozenset({"#/texts/9"}))

    assert [record["text"] for record in separated] == [
        f"{LEAD}\n\n- {ALPHA}"]
    assert observed == set()
    assert [record["text"] for record in other] == [f"{LEAD} - {ALPHA}"]


def test_replay_ignores_a_named_item_that_does_not_open_the_record():
    """Only the record's first lineage item can separate its join.

    Here that item has no list marker, so the named later item stays
    inline and is not observed.
    """
    records = [_record(LEAD, ["#/texts/0"]),
               _record(f"- {ALPHA}", ["#/texts/3", ITEM])]
    observed = set()

    joined = _coalesce(records, list_markers={ITEM: "-"},
                       inline_list_joins=observed, separated_list_refs=REFS)

    assert [record["text"] for record in joined] == [f"{LEAD} - {ALPHA}"]
    assert observed == set()


def test_separation_keeps_every_other_join_condition():
    records = [_record(LEAD.rstrip(",") + ".", ["#/texts/0"]),
               _record(f"- {ALPHA}", [ITEM])]
    observed = set()

    kept = _coalesce(records, list_markers={ITEM: "-"},
                     inline_list_joins=observed, separated_list_refs=REFS)

    assert [record["text"] for record in kept] == [
        records[0]["text"], records[1]["text"]]
    assert observed == set()


def test_separated_join_must_still_fit_the_token_limit():
    """The inline join fits exactly; the separated one's blank line does not.

    Neither the separated join nor its observation is recorded, so the
    records stay separate and the marker stays line-initial.
    """
    def count(value):
        return len(value.split()) + value.count("\n")

    limit = count(f"{LEAD} - {ALPHA}")
    observed, unobserved = set(), set()

    joined = rag._coalesce_chunk_boundaries(
        _lead_then_item(), count, 8, hard_max_tokens=limit,
        list_markers={ITEM: "-"}, inline_list_joins=observed)
    kept = rag._coalesce_chunk_boundaries(
        _lead_then_item(), count, 8, hard_max_tokens=limit,
        list_markers={ITEM: "-"}, inline_list_joins=unobserved,
        separated_list_refs=REFS)

    assert [record["text"] for record in joined] == [f"{LEAD} - {ALPHA}"]
    assert observed == {ITEM}
    assert [record["text"] for record in kept] == [LEAD, f"- {ALPHA}"]
    assert unobserved == set()


@pytest.mark.parametrize(("text", "refs", "markers", "expected"), [
    (f"- {ALPHA}", [ITEM], {ITEM: "-"}, ITEM),
    (f"  -\t{ALPHA}", [ITEM], {ITEM: "-"}, ITEM),
    ("-", [ITEM], {ITEM: "-"}, ITEM),
    (f"* {ALPHA}", [ITEM], {ITEM: "*"}, ITEM),
    # A fused in-text dash, a line break after the marker, another marker,
    # or a later lineage item.
    (f"-{ALPHA}", [ITEM], {ITEM: "-"}, None),
    (f"-\n{ALPHA}", [ITEM], {ITEM: "-"}, None),
    (f"* {ALPHA}", [ITEM], {ITEM: "-"}, None),
    (f"- {ALPHA}", ["#/texts/0", ITEM], {ITEM: "-"}, None),
    (f"- {ALPHA}", [ITEM], {}, None),
    (f"- {ALPHA}", [], {ITEM: "-"}, None),
])
def test_opening_marker_belongs_to_the_first_lineage_item(
        text, refs, markers, expected):
    record = _record(text, refs)

    assert rag._opening_list_marker_ref(
        record, record["text"], markers) == expected
