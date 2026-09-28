"""Differential controls for passive chunk disposition observation."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
import contextvars
import copy
import hashlib
import json
from types import SimpleNamespace

import pytest

import chunk_dedup_audit as audit
import rag
import table_retrieval_core as tables


TEXT = "The complete source proposition retains its meaningful repeated words."


def record(text=TEXT, ref=None):
    metadata = {} if ref is None else {"source_items": [{"ref": ref}]}
    return {"text": text, "metadata": metadata}


def capture(chunks, **kwargs):
    collector = audit.ChunkDedupCollector()
    with audit.capture_chunk_dedup(collector):
        result = rag._deduplicate_chunks(chunks, **kwargs)
    return result, collector.report()


def assert_frame(frame, texts, keepers):
    """Check hand-authored occurrence expectations, without rerunning dedup."""
    assert frame["status"] == "complete"
    assert frame["reason"] is None and frame["exception_type"] is None
    assert frame["input_count"] == len(texts)
    assert frame["entries"] == [
        {
            "input_ordinal": index,
            "text": text,
            "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "disposition": "kept" if keeper == index else "removed",
            "retained_input_ordinal": keeper,
        }
        for index, (text, keeper) in enumerate(zip(texts, keepers, strict=True))
    ]
    assert frame["output_ordinals"] == [
        index for index, keeper in enumerate(keepers) if index == keeper
    ]
    assert all(keeper <= index for index, keeper in enumerate(keepers))


def test_actual_winning_keeper_uses_occurrences_not_equal_text_or_object_ids():
    first, second, fourth = record(ref="A"), record(ref="B"), record(ref="A")
    chunks = [first, second, second, fourth]
    before = copy.deepcopy(chunks)
    default = rag._deduplicate_chunks(chunks)
    result, report = capture(chunks)

    assert chunks == before
    assert len(result) == len(default) == 2
    assert result[0] is default[0] is first
    assert result[1] is default[1] is second
    assert report["status"] == "complete"
    assert_frame(report["frames"][0], [TEXT] * 4, [0, 1, 1, 0])


def test_real_table_occurrence_annotation_survives_observation():
    text = "| Label | Value |\n| --- | --- |\n| Same | 10 |"
    chunks = [record(text, "#/tables/0"), record(text, "#/tables/0")]
    for chunk in chunks:
        chunk["metadata"].update(content_type="table", content_source="table")
    assert tables.annotate_table_fragment_occurrences(
        chunks, stable_id_fn=rag._chunk_id) == 2
    before = copy.deepcopy(chunks)
    result, report = capture(chunks)

    assert [chunk["metadata"][tables.TABLE_FRAGMENT_OCCURRENCE_FIELD]
            for chunk in result] == [0, 1]
    assert all(actual is expected for actual, expected in zip(result, chunks, strict=True))
    assert chunks == before
    assert_frame(report["frames"][0], [text, text], [0, 1])


@pytest.mark.parametrize("texts", [[], ["", "x", ""]])
def test_empty_and_low_signal_inputs_preserve_identity_and_complete_coverage(texts):
    chunks = [record(text) for text in texts]
    result, report = capture(chunks)
    if not chunks:
        assert result is chunks
    assert all(actual is expected for actual, expected in zip(result, chunks, strict=True))
    assert_frame(report["frames"][0], texts, list(range(len(texts))))


def test_consumed_sequence_includes_callback_appends_and_original_text(monkeypatch):
    def run(captured):
        chunks, calls = [record(), record()], []
        first = chunks[0]

        def fingerprint(text):
            calls.append(text)
            if len(calls) == 1:
                first["text"] = "Changed after the actual fingerprint input."
                chunks.append(record())
            return "constant"

        collector = audit.ChunkDedupCollector()
        with monkeypatch.context() as patch:
            patch.setattr(rag, "_text_fingerprint", fingerprint)
            with audit.capture_chunk_dedup(collector) if captured else nullcontext():
                result = rag._deduplicate_chunks(chunks)
        assert len(result) == 1 and result[0] is first
        assert len(chunks) == 3
        return calls, result[0]["text"], collector.report() if captured else None

    default, observed = run(False), run(True)
    assert default[:2] == observed[:2]
    assert observed[0] == [TEXT] * 3
    assert observed[1] != TEXT
    assert_frame(observed[2]["frames"][0], [TEXT] * 3, [0, 0, 0])


def test_dict_subclasses_have_exact_consumed_read_order(monkeypatch):
    def run(captured):
        reads = []

        class Reads(dict):
            def __init__(self, name, value):
                super().__init__(value)
                self.name = name

            def get(self, key, default=None):
                reads.append((self.name, "get", key))
                return super().get(key, default)

            def __getitem__(self, key):
                reads.append((self.name, "item", key))
                return super().__getitem__(key)

            def __contains__(self, key):
                reads.append((self.name, "contains", key))
                return super().__contains__(key)

        chunks = [
            Reads(name, {"text": TEXT, "metadata": Reads(name + "-meta", {
                "source_items": [Reads(name + "-ref", {"ref": "same"})],
            })})
            for name in ("kept", "candidate")
        ]
        collector = audit.ChunkDedupCollector()
        with audit.capture_chunk_dedup(collector) if captured else nullcontext():
            result = rag._deduplicate_chunks(chunks)
        assert len(result) == 1 and result[0] is chunks[0]
        return reads, collector.report() if captured else None

    default, observed = run(False), run(True)
    field = tables.TABLE_FRAGMENT_OCCURRENCE_FIELD
    expected = [
        ("kept", "item", "text"), ("candidate", "item", "text"),
        ("kept", "get", "metadata"), ("candidate", "get", "metadata"),
        ("kept-meta", "contains", field), ("candidate-meta", "contains", field),
        ("kept", "get", "metadata"), ("kept-meta", "get", "source_items"),
        ("kept-ref", "get", "ref"), ("kept-ref", "item", "ref"),
        ("candidate", "get", "metadata"), ("candidate-meta", "get", "source_items"),
        ("candidate-ref", "get", "ref"), ("candidate-ref", "item", "ref"),
        ("candidate", "get", "text"), ("kept", "get", "text"),
    ]
    assert default[0] == observed[0] == expected
    assert_frame(observed[1]["frames"][0], [TEXT, TEXT], [0, 0])


def test_opaque_getter_value_is_not_converted_or_kept_alive(monkeypatch):
    def run(captured):
        calls = []

        class TrapMeta(type):
            def __eq__(cls, other):
                calls.append("metaclass-equality")
                raise AssertionError("observation must use exact type identity")

        class Opaque(metaclass=TrapMeta):
            def __str__(self):
                calls.append("string")
                raise AssertionError("no string conversion")

            def __bool__(self):
                calls.append("truth")
                raise AssertionError("no truth conversion")

            def __del__(self):
                calls.append("finalize")

        class Chunk(dict):
            def __getitem__(self, key):
                assert key == "text"
                calls.append("get-text")
                return Opaque()

        def fingerprint(value):
            assert type(value) is Opaque
            calls.append("fingerprint")
            return "constant"

        def trigrams(value):
            assert value == "constant"
            calls.append("trigrams")
            return frozenset()

        chunk = Chunk()
        collector = audit.ChunkDedupCollector()
        with monkeypatch.context() as patch:
            patch.setattr(rag, "_text_fingerprint", fingerprint)
            patch.setattr(rag, "_make_trigrams", trigrams)
            with audit.capture_chunk_dedup(collector) if captured else nullcontext():
                result = rag._deduplicate_chunks([chunk])
        assert len(result) == 1 and result[0] is chunk
        return calls, collector.report() if captured else None

    default, observed = run(False), run(True)
    assert default[0] == observed[0] == ["get-text", "fingerprint", "finalize", "trigrams"]
    frame = observed[1]["frames"][0]
    assert frame["status"] == "abstained" and frame["reason"] == "unsupported_text"
    assert frame["entries"] == []


@pytest.mark.parametrize("empty", [False, True])
def test_list_subclass_keeps_original_truth_iteration_and_length_calls(empty):
    def run(captured):
        calls = []

        class Chunks(list):
            def __bool__(self):
                calls.append("truth")
                return list.__len__(self) != 0

            def __iter__(self):
                calls.append("iterate")
                return super().__iter__()

            def __len__(self):
                calls.append("length")
                return super().__len__()

        item = record()
        chunks = Chunks([] if empty else [item])
        collector = audit.ChunkDedupCollector()
        with audit.capture_chunk_dedup(collector) if captured else nullcontext():
            result = rag._deduplicate_chunks(chunks)
        if empty:
            assert result is chunks
        else:
            assert type(result) is list and result[0] is item
        return calls, collector.report() if captured else None

    default, observed = run(False), run(True)
    assert default[0] == observed[0] == (["truth"] if empty else ["truth", "iterate", "length"])
    frame = observed[1]["frames"][0]
    if empty:
        assert frame["status"] == "abstained" and frame["reason"] == "unobserved_result"
    else:
        assert_frame(frame, [TEXT], [0])


@pytest.mark.parametrize("genuine", [False, True])
def test_core_target_resolves_once_before_helper_arguments(monkeypatch, genuine):
    calls = []
    original = rag._CHUNK_DEDUP_CORE
    chunks = [record()]

    def fingerprint(text):
        calls.append("late-fingerprint")
        return text

    def trigrams(text):
        calls.append("late-trigrams")
        return frozenset()

    def replacement(items, threshold, *, text_fingerprint_fn, make_trigrams_fn,
                    can_deduplicate_fn, removed_callback):
        calls.append("replacement")
        assert threshold == rag.DEDUP_THRESHOLD
        assert callable(can_deduplicate_fn) and callable(removed_callback)
        make_trigrams_fn(text_fingerprint_fn(items[0]["text"]))
        return items

    class Core:
        @property
        def _deduplicate_chunks(self):
            calls.append("target")
            monkeypatch.setattr(rag, "_text_fingerprint", fingerprint)
            monkeypatch.setattr(rag, "_make_trigrams", trigrams)
            return original if genuine else replacement

    monkeypatch.setattr(rag, "_chunking_core", Core())
    result, report = capture(chunks)
    assert result[0] is chunks[0]
    assert calls == (["target"] + ([] if genuine else ["replacement"])
                     + ["late-fingerprint", "late-trigrams"])
    if genuine:
        assert_frame(report["frames"][0], [TEXT], [0])
    else:
        assert result is chunks
        assert report["status"] == "abstained"
        assert report["frames"][0]["reason"] == "unsupported_core"


def test_helpers_stay_bound_but_logger_is_resolved_after_processing(monkeypatch):
    def run(captured):
        calls = []

        def new_fingerprint(text):
            calls.append("new-fingerprint")
            return "constant"

        def new_trigrams(text):
            calls.append("new-trigrams")
            return frozenset({"same"})

        def old_fingerprint(text):
            calls.append("old-fingerprint")
            patch.setattr(rag, "_text_fingerprint", new_fingerprint)
            patch.setattr(rag, "_make_trigrams", new_trigrams)
            patch.setattr(rag, "log", SimpleNamespace(info=lambda value: calls.append(("late-log", value))))
            return "constant"

        def old_trigrams(text):
            calls.append("old-trigrams")
            return frozenset({"same"})

        collector = audit.ChunkDedupCollector()
        with monkeypatch.context() as patch:
            patch.setattr(rag, "_text_fingerprint", old_fingerprint)
            patch.setattr(rag, "_make_trigrams", old_trigrams)
            patch.setattr(rag, "log", SimpleNamespace(info=lambda value: calls.append("wrong-early-log")))
            with audit.capture_chunk_dedup(collector) if captured else nullcontext():
                rag._deduplicate_chunks([record(), record()])
                rag._deduplicate_chunks([record()])
        return calls, collector.report() if captured else None

    default, observed = run(False), run(True)
    assert default[0] == observed[0] == [
        "old-fingerprint", "old-trigrams", "old-fingerprint", "old-trigrams",
        ("late-log", "Deduplication: removed 1 source-overlapping near-duplicate chunks"),
        "new-fingerprint", "new-trigrams",
    ]
    assert observed[1]["status"] == "complete"


@pytest.mark.parametrize("stage", ["fingerprint", "trigrams", "policy", "log"])
def test_callback_error_identity_and_failed_terminal_state(monkeypatch, stage):
    sentinel = LookupError("deliberate dedup failure")

    def run(captured):
        calls = []

        def fingerprint(text):
            calls.append("fingerprint")
            if stage == "fingerprint":
                raise sentinel
            return "constant"

        def trigrams(text):
            calls.append("trigrams")
            if stage == "trigrams":
                raise sentinel
            return frozenset({"same"})

        class Chunk(dict):
            def get(self, key, default=None):
                if key == "metadata":
                    calls.append("policy")
                    if stage == "policy":
                        raise sentinel
                return super().get(key, default)

        def log(message):
            calls.append("log")
            raise sentinel

        collector = audit.ChunkDedupCollector()
        with monkeypatch.context() as patch:
            patch.setattr(rag, "_text_fingerprint", fingerprint)
            patch.setattr(rag, "_make_trigrams", trigrams)
            patch.setattr(rag, "log", SimpleNamespace(info=log))
            with pytest.raises(LookupError) as caught:
                with audit.capture_chunk_dedup(collector) if captured else nullcontext():
                    rag._deduplicate_chunks([Chunk(record()), Chunk(record())])
        assert caught.value is sentinel
        return calls, collector.report() if captured else None

    default, observed = run(False), run(True)
    assert default[0] == observed[0]
    frame = observed[1]["frames"][0]
    assert observed[1]["status"] == frame["status"] == "failed"
    assert frame["input_count"] is None and frame["exception_type"] == "LookupError"
    if stage == "log":
        assert frame["output_ordinals"] == [0]
        assert [entry["disposition"] for entry in frame["entries"]] == ["kept", "removed"]


def test_exception_name_diagnostic_does_not_dispatch_metaclass(monkeypatch):
    calls = []

    class Meta(type):
        @property
        def __name__(cls):
            calls.append("descriptor")
            raise AssertionError("not a production callback")

    class OriginalError(RuntimeError, metaclass=Meta):
        pass

    sentinel = OriginalError("original")

    def fail(text):
        raise sentinel

    monkeypatch.setattr(rag, "_text_fingerprint", fail)
    collector = audit.ChunkDedupCollector()
    with pytest.raises(RuntimeError) as caught:
        with audit.capture_chunk_dedup(collector):
            rag._deduplicate_chunks([record()])
    assert caught.value is sentinel and calls == []
    assert collector.report()["frames"][0]["exception_type"] == "OriginalError"


def test_single_use_detached_report_and_immutable_limits():
    collector = audit.ChunkDedupCollector()
    with pytest.raises(RuntimeError):
        collector.report()
    with pytest.raises(TypeError):
        audit.LIMITS["max_frames"] = 1
    with pytest.raises(TypeError):
        audit.ChunkDedupCollector(limits={})
    with pytest.raises(TypeError):
        with audit.capture_chunk_dedup(object()):
            pass
    with audit.capture_chunk_dedup(collector):
        with pytest.raises(RuntimeError):
            collector.report()
        with pytest.raises(RuntimeError):
            with audit.capture_chunk_dedup(collector):
                pass
        chunks = [record()]
        result = rag._deduplicate_chunks(chunks)
    report = collector.report()
    assert report["schema_version"] == 1
    assert report["kind"] == "chunk_dedup_observation"
    assert report["profile"] == "chunk_deduplication_v1"
    assert json.loads(json.dumps(report, allow_nan=False)) == report
    report["frames"][0]["entries"][0]["text"] = "Edited detached report"
    report["limits"]["max_frames"] = 0
    assert result[0] is chunks[0] and chunks[0]["text"] == TEXT
    assert collector.report()["frames"][0]["entries"][0]["text"] == TEXT
    assert collector.report()["limits"] == dict(audit.LIMITS)
    with pytest.raises(RuntimeError):
        with audit.capture_chunk_dedup(collector):
            pass


def test_nested_capture_failure_restores_outer_and_keeps_separate_frames(monkeypatch):
    outer, inner = audit.ChunkDedupCollector(), audit.ChunkDedupCollector()
    sentinel = LookupError("inner failure")

    def fingerprint(text):
        if text == "outer":
            with pytest.raises(LookupError) as caught:
                with audit.capture_chunk_dedup(inner):
                    rag._deduplicate_chunks([record("inner")])
            assert caught.value is sentinel
        elif text == "inner":
            raise sentinel
        return text

    monkeypatch.setattr(rag, "_text_fingerprint", fingerprint)
    monkeypatch.setattr(rag, "_make_trigrams", lambda text: frozenset())
    with audit.capture_chunk_dedup(outer):
        rag._deduplicate_chunks([record("outer")])
        rag._deduplicate_chunks([record("after")])
    assert outer.report()["status"] == "complete"
    assert inner.report()["status"] == "failed"
    assert [frame["parent_frame_id"] for frame in outer.report()["frames"]] == [None, None]
    assert inner.report()["frames"][0]["parent_frame_id"] is None
    assert_frame(outer.report()["frames"][1], ["after"], [0])


def test_saved_closed_frame_starts_new_root_only_while_capture_is_active(monkeypatch):
    saved = []

    def fingerprint(text):
        if text == "seed":
            saved.append(contextvars.copy_context())
        return text

    monkeypatch.setattr(rag, "_text_fingerprint", fingerprint)
    monkeypatch.setattr(rag, "_make_trigrams", lambda text: frozenset())
    collector = audit.ChunkDedupCollector()
    with audit.capture_chunk_dedup(collector):
        rag._deduplicate_chunks([record("seed")])
        result = saved[0].run(rag._deduplicate_chunks, [record("late")])
        assert result[0]["text"] == "late"
    before = collector.report()
    assert [frame["parent_frame_id"] for frame in before["frames"]] == [None, None]
    assert_frame(before["frames"][0], ["seed"], [0])
    assert_frame(before["frames"][1], ["late"], [0])
    assert saved[0].run(rag._deduplicate_chunks, [record("closed")])[0]["text"] == "closed"
    assert collector.report() == before


def test_copied_context_on_other_thread_does_not_record_worker_calls():
    collector = audit.ChunkDedupCollector()
    with audit.capture_chunk_dedup(collector):
        saved = contextvars.copy_context()
        chunk = record()
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(saved.run, rag._deduplicate_chunks, [chunk])
            result = future.result(timeout=5)
        assert result[0] is chunk
        rag._deduplicate_chunks([record("owner")])
    report = collector.report()
    assert report["status"] == "complete" and len(report["frames"]) == 1
    assert_frame(report["frames"][0], ["owner"], [0])


def test_depth_abstention_keeps_open_parent_and_does_not_change_recursion(monkeypatch):
    maximum = audit.LIMITS["max_depth"] + 2

    def run(captured):
        visited = []

        def fingerprint(text):
            depth = int(text)
            visited.append(depth)
            if depth < maximum:
                rag._deduplicate_chunks([record(str(depth + 1))])
            return text

        collector = audit.ChunkDedupCollector()
        with monkeypatch.context() as patch:
            patch.setattr(rag, "_text_fingerprint", fingerprint)
            patch.setattr(rag, "_make_trigrams", lambda text: frozenset())
            with audit.capture_chunk_dedup(collector) if captured else nullcontext():
                result = rag._deduplicate_chunks([record("1")])
        assert result[0]["text"] == "1"
        return visited, collector.report() if captured else None

    default, observed = run(False), run(True)
    assert default[0] == observed[0] == list(range(1, maximum + 1))
    frames = observed[1]["frames"]
    assert len(frames) == maximum
    for index, frame in enumerate(frames):
        assert frame["parent_frame_id"] == (index if index else None)
        if index >= audit.LIMITS["max_depth"]:
            assert frame["status"] == "abstained" and frame["reason"] == "depth_budget"
            assert frame["entries"] == []
        else:
            assert_frame(frame, [str(index + 1)], [0])


@pytest.mark.parametrize("budget", ["text", "characters", "occurrences", "unicode"])
def test_actual_payload_limits_abstain_without_skipping_processing(monkeypatch, budget):
    if budget == "text":
        texts = ["x" * (audit.LIMITS["max_text_chars"] + 1)]
        reason = "text_budget"
    elif budget == "characters":
        width = audit.LIMITS["max_text_chars"]
        texts = ["x" * width] * (audit.LIMITS["max_total_chars"] // width + 1)
        reason = "character_budget"
    elif budget == "occurrences":
        texts = [""] * (audit.LIMITS["max_occurrences"] + 1)
        reason = "occurrence_budget"
    else:
        texts = ["\ud800"]
        reason = "unsupported_unicode"

    def run(captured):
        calls = []

        def fingerprint(text):
            calls.append(text)
            return "constant"

        chunks = [record(text) for text in texts]
        collector = audit.ChunkDedupCollector()
        with monkeypatch.context() as patch:
            patch.setattr(rag, "_text_fingerprint", fingerprint)
            patch.setattr(rag, "_make_trigrams", lambda text: frozenset())
            with audit.capture_chunk_dedup(collector) if captured else nullcontext():
                result = rag._deduplicate_chunks(chunks)
        assert all(actual is expected for actual, expected in zip(result, chunks, strict=True))
        return calls, collector.report() if captured else None

    default, observed = run(False), run(True)
    assert default[0] == observed[0] == texts
    frame = observed[1]["frames"][0]
    assert observed[1]["status"] == frame["status"] == "abstained"
    assert frame["reason"] == reason
    assert frame["entries"] == frame["output_ordinals"] == []
    assert frame["input_count"] is None


def test_maximum_occurrence_count_can_complete_without_event_budget_failure(monkeypatch):
    monkeypatch.setattr(rag, "_make_trigrams", lambda text: frozenset())
    chunks = [record("") for _ in range(audit.LIMITS["max_occurrences"])]
    result, report = capture(chunks)
    assert all(actual is expected for actual, expected in zip(result, chunks, strict=True))
    assert report["status"] == "complete"
    assert_frame(report["frames"][0], [""] * len(chunks), list(range(len(chunks))))


def test_event_budget_is_shared_across_calls_without_changing_their_results(monkeypatch):
    calls = []

    def fingerprint(text):
        calls.append(text)
        return "constant"

    monkeypatch.setattr(rag, "_text_fingerprint", fingerprint)
    monkeypatch.setattr(rag, "_make_trigrams", lambda text: frozenset())
    collector = audit.ChunkDedupCollector()
    with audit.capture_chunk_dedup(collector):
        for _ in range(3):
            chunks = [record("") for _ in range(3000)]
            result = rag._deduplicate_chunks(chunks)
            assert all(actual is expected for actual, expected in zip(result, chunks, strict=True))
    assert calls == [""] * 9000
    frames = collector.report()["frames"]
    assert [frame["status"] for frame in frames] == ["complete", "complete", "abstained"]
    assert frames[-1]["reason"] == "event_budget"
    assert frames[-1]["entries"] == []


def test_frame_limit_preserves_explicit_omission_and_ordinary_calls():
    collector = audit.ChunkDedupCollector()
    with audit.capture_chunk_dedup(collector):
        for _ in range(audit.LIMITS["max_frames"] + 1):
            chunks = []
            assert rag._deduplicate_chunks(chunks) is chunks
    report = collector.report()
    assert report["status"] == "abstained"
    assert len(report["frames"]) == audit.LIMITS["max_frames"]
    assert report["omitted_frame_count"] == 1
    assert all(frame["status"] == "complete" for frame in report["frames"])


def test_replaced_unprocessed_record_is_not_retained_by_capture(monkeypatch):
    """The observer must not prolong the life of a never-consumed record."""
    import gc

    def run(captured):
        calls = []

        class Unprocessed(dict):
            def __del__(self):
                calls.append("unprocessed-finalized")

        # Do not retain the second instance outside the production input list.
        chunks = [record("seed"), Unprocessed(record("never consumed"))]

        def fingerprint(text):
            calls.append(("fingerprint", text))
            if text == "seed":
                chunks[1] = record("replacement")
                # Make lifetime observable without relying on immediate refcounts.
                gc.collect()
                calls.append("replacement-complete")
            return text

        def trigrams(text):
            calls.append(("trigrams", text))
            return frozenset()

        collector = audit.ChunkDedupCollector()
        with monkeypatch.context() as patch:
            patch.setattr(rag, "_text_fingerprint", fingerprint)
            patch.setattr(rag, "_make_trigrams", trigrams)
            with audit.capture_chunk_dedup(collector) if captured else nullcontext():
                result = rag._deduplicate_chunks(chunks)
        assert len(result) == 2
        assert all(actual is expected for actual, expected in zip(result, chunks, strict=True))
        return calls, collector.report() if captured else None

    default, observed = run(False), run(True)
    assert default[0] == observed[0] == [
        ("fingerprint", "seed"), "unprocessed-finalized", "replacement-complete",
        ("trigrams", "seed"), ("fingerprint", "replacement"), ("trigrams", "replacement"),
    ]
    assert_frame(observed[1]["frames"][0], ["seed", "replacement"], [0, 1])
