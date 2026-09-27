"""Independent source-wrapper observation, compatibility and replay contracts."""

from contextlib import nullcontext
import contextvars
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from types import FunctionType, SimpleNamespace

import pytest

import chunking_core
import rag
import source_cleanup_audit as audit


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "source_cleanup_baseline_v1.json"
FIXTURE_SHA = "aa3e5d5b74b5f178de77f4ee39c21d5f216978eebc42ce786c709662d49120bb"
BASELINE = json.loads(FIXTURE_PATH.read_bytes())
CASES = BASELINE["cases"]


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ProbeError(RuntimeError):
    pass


class ScriptedLabel:
    def __init__(self, item):
        self.item = item

    @property
    def value(self):
        return self.item.take("label_value", ["text"])


class ScriptedItem:
    """Test-owned source doubles; no import or execution of the capture helper."""

    def __init__(self, name, spec, events):
        self.name, self.spec, self.events, self.counts = name, spec, events, {}
        self.label_object = ScriptedLabel(self)

    def take(self, key, fallback):
        values = self.spec.get(key, fallback)
        ordinal = self.counts.get(key, 0)
        self.counts[key] = ordinal + 1
        value = values[min(ordinal, len(values) - 1)]
        self.events.append({"event": "property", "item": self.name, "field": key,
                            "ordinal": ordinal + 1, "value": value})
        if type(value) is dict and "raise" in value:
            raise ProbeError(value["raise"])
        return value

    def __getattr__(self, key):
        if key not in {"label", "self_ref", "text", "orig"}:
            raise AttributeError(key)
        if key == "label" and self.spec.get("label_object", False):
            self.take("label", ["<scripted-label-object>"])
            return self.label_object
        return self.take(key, ["text" if key == "label" else ""])


class ScriptedItems(list):
    def __init__(self, values, events):
        super().__init__(values)
        self.events = events

    def __bool__(self):
        value = len(self) != 0
        self.events.append({"event": "items_truthiness", "value": value})
        return value

    def __iter__(self):
        self.events.append({"event": "items_iteration"})
        return super().__iter__()


class ScriptedOverrides(dict):
    def __init__(self, values, events):
        super().__init__(values)
        self.events = events

    def __bool__(self):
        value = len(self) != 0
        self.events.append({"event": "override_truthiness", "value": value})
        return value

    def get(self, key, default=None):
        value = super().get(key, default)
        self.events.append({"event": "override_lookup", "key": key, "value": value})
        return value


class ScriptedRefs(set):
    def __init__(self, values, events):
        super().__init__(values)
        self.events = events

    def __contains__(self, key):
        value = super().__contains__(key)
        self.events.append({"event": "rebuild_membership", "key": key, "value": value})
        return value


def run_baseline_case(case, monkeypatch, *, captured):
    fixture, events = case["fixture"], []
    items = {name: ScriptedItem(name, spec, events) for name, spec in fixture["items"].items()}
    arguments = {}
    if fixture["preserve_source_identity"]:
        arguments["preserve_source_identity"] = True
    if items or fixture["item_order"]:
        arguments.update(source_items=ScriptedItems([items[name] for name in fixture["item_order"]], events),
                         canonical_text_overrides=ScriptedOverrides(fixture["overrides"], events),
                         text_rebuild_refs=ScriptedRefs(fixture["rebuild_refs"], events))
    core_code = chunking_core._normalize_text.__code__

    def profile(frame, event, result):
        # Only unchanged core callback boundaries, never incidental wrapper
        # locals or observer internals. The core itself is not being changed.
        parent = frame.f_back
        if event not in {"call", "return"} or parent is None or parent.f_code is not core_code:
            return
        for variable, role in (("strip_fn", "header_footer_callback"), ("dedup_fn", "nearby_line_callback")):
            callback = parent.f_locals.get(variable)
            if type(callback) is FunctionType and frame.f_code is callback.__code__:
                arguments = {key: value for key, value in frame.f_locals.items()
                             if key in {"text", "value"} and type(value) in (str, bool, type(None))}
                events.append({"event": "call" if event == "call" else "return_or_unwind", "function": role,
                               "arguments": arguments, "result": result if event == "return" and type(result) in (str, type(None)) else None})
                break

    def callback_fault(text):
        raise ProbeError("generated-" + fixture["fault"] + "-callback-error")

    collector = audit.SourceCleanupCollector() if captured else None
    output, error = None, None
    previous_profile = sys.getprofile()
    with monkeypatch.context() as patch:
        if fixture["fault"]:
            target = "_strip_headers_footers" if fixture["fault"] == "header" else "_dedup_nearby_lines"
            patch.setattr(rag, target, callback_fault)
        try:
            sys.setprofile(profile)
            with audit.capture_source_cleanup(collector) if captured else nullcontext():
                output = rag._normalize_source_chunk_text(fixture["text"], fixture["content_source"], **arguments)
        except ProbeError as exc:
            error = {"type": type(exc).__name__, "message": str(exc)}
        finally:
            sys.setprofile(previous_profile)
    return output, error, events, collector.report() if captured else None


def captured_call(text, content="body", **context):
    collector = audit.SourceCleanupCollector()
    with audit.capture_source_cleanup(collector):
        output = rag._normalize_source_chunk_text(text, content, **context)
    return output, collector.report()


def context_values(frame, site):
    rows = [row for row in frame["context"] if row["site"] == site]
    assert all(row["observed"] for row in rows)
    return [row["value"] for row in rows]


def context_mapping(frame, site):
    counts = context_values(frame, site + ".count")
    keys, values = context_values(frame, site + ".key"), context_values(frame, site + ".value")
    assert counts == [len(keys)] and len(keys) == len(values)
    return dict(zip(keys, values))


def replay_edits(original, final, edits):
    """Independent exact edit application in both directions, including hashes."""
    state = original
    for edit in edits:
        assert digest(state) == edit["before_sha256"]
        start, end = edit["before_span"]
        assert 0 <= start <= end <= len(state)
        assert state[start:end] == edit["removed"]
        state = state[:start] + edit["inserted"] + state[end:]
        assert digest(state) == edit["after_sha256"]
        after_start, after_end = edit["after_span"]
        assert after_start == start and state[after_start:after_end] == edit["inserted"]
    assert state == final
    for edit in reversed(edits):
        start, end = edit["after_span"]
        assert digest(state) == edit["after_sha256"] and state[start:end] == edit["inserted"]
        state = state[:start] + edit["removed"] + state[end:]
        assert digest(state) == edit["before_sha256"]
    assert state == original


def assert_complete_replay(frame):
    assert digest(frame["original_text"]) == frame["original_sha256"]
    assert digest(frame["final_text"]) == frame["final_sha256"]
    passes = frame["passes"]
    assert len({step["pass_id"] for step in passes}) == len(passes)
    prior_passes = set()
    for attempt in passes:
        assert attempt["parent_pass_id"] is None or attempt["parent_pass_id"] in prior_passes
        prior_passes.add(attempt["pass_id"])
    committed = [step for step in passes if step["disposition"] == "committed"]
    assert len(committed) == 1 and committed[0]["pass_id"] == frame["committed_pass_id"]
    assert committed[0]["output_text"] == frame["final_text"]
    all_operations = {operation["operation_id"]: operation for attempt in passes for operation in attempt["operations"]}
    for attempt in passes:
        if attempt["output_text"] is None:
            continue
        assert digest(attempt["input_text"]) == attempt["input_sha256"]
        assert digest(attempt["output_text"]) == attempt["output_sha256"]
        replay_edits(attempt["input_text"], attempt["output_text"], attempt["steps"])
        operations = attempt["operations"]
        assert len({operation["operation_id"] for operation in operations}) == len(operations)
        for operation in operations:
            parent_id = operation["parent_operation_id"]
            assert parent_id is None or parent_id in all_operations and parent_id < operation["operation_id"]
            if operation["output_text"] is not None:
                assert digest(operation["input_text"]) == operation["input_sha256"]
                assert digest(operation["output_text"]) == operation["output_sha256"]
                replay_edits(operation["input_text"], operation["output_text"], operation["edits"])
        by_id = {operation["operation_id"]: operation for operation in operations}
        for step in attempt["steps"]:
            if "operation_id" in step:
                operation = by_id[step["operation_id"]]
                assert operation["is_pass_root"] is True
                assert step["before_sha256"] == operation["input_sha256"]
                assert step["after_sha256"] == operation["output_sha256"]


def test_portable_baseline_is_the_reviewed_prechange_capture():
    assert hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest() == FIXTURE_SHA
    assert BASELINE["case_count"] == len(CASES) == 24
    assert len({case["fixture"]["id"] for case in CASES}) == 24
    assert sum(case["exception"] is not None for case in CASES) == 3


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["fixture"]["id"])
@pytest.mark.parametrize("captured", [False, True], ids=["default", "observed"])
def test_prechange_outputs_errors_and_consumed_events(case, captured, monkeypatch):
    pristine = copy.deepcopy(case)
    output, error, events, report = run_baseline_case(case, monkeypatch, captured=captured)
    assert output == case["output"]
    assert error == case["exception"]
    assert events == case["observations"]
    assert case == pristine
    if captured:
        assert report["schema_version"] == 1
        assert report["kind"] == "source_cleanup_observation"
        assert report["profile"] == "source_normalization_v1"
        assert len(report["frames"]) == 1 and report["omitted_frame_count"] == 0
        frame = report["frames"][0]
        if error is None:
            assert frame["final_text"] == output
            assert_complete_replay(frame)
        else:
            assert frame["exception_type"] == "ProbeError"
            assert frame["committed_pass_id"] is None and frame["final_text"] is None


@pytest.mark.parametrize("case_id, expected", [
    ("distinct-refs", {"Date:": 2}), ("repeated-same-ref", {}),
    ("unsupported-label", {}), ("orig-fallback", {"Date:": 2}),
])
def test_allowances_report_actual_consumed_distinct_sources(case_id, expected, monkeypatch):
    case = next(case for case in CASES if case["fixture"]["id"] == case_id)
    _, error, _, report = run_baseline_case(case, monkeypatch, captured=True)
    assert error is None
    frame = report["frames"][0]
    assert context_mapping(frame, "allowances.distinct_refs") == expected
    assert context_mapping(frame, "allowances.final") == expected
    auxiliary = [attempt for attempt in frame["passes"] if attempt["role"] == "auxiliary"]
    assert all(attempt["name"] == "source_line" and attempt["disposition"] == "auxiliary" for attempt in auxiliary)
    assert all(attempt["pass_id"] != frame["committed_pass_id"] for attempt in auxiliary)
    if case_id == "unsupported-label":
        assert auxiliary == []
        assert context_values(frame, "source_item.decision") == ["unsupported_label", "unsupported_label"]


@pytest.mark.parametrize("case_id, decision, allowed", [
    ("canonical-window", "window_accepted", True),
    ("canonical-inline-oracle", "window_accepted", True),
    ("canonical-no-rebuild", "ineligible", False),
    ("canonical-unrelated-fragment", "window_denied", False),
])
def test_canonical_context_distinguishes_lookup_from_actual_authorization(case_id, decision, allowed, monkeypatch):
    case = next(case for case in CASES if case["fixture"]["id"] == case_id)
    _, error, events, report = run_baseline_case(case, monkeypatch, captured=True)
    assert error is None
    frame = report["frames"][0]
    lookups = [event for event in events if event["event"] == "override_lookup"]
    assert len(lookups) == 1
    assert context_values(frame, "canonical.lookup_key") == [lookups[0]["key"]]
    assert context_values(frame, "canonical.lookup_value") == [lookups[0]["value"]]
    assert context_values(frame, "canonical.decision") == [decision]
    canonical = [attempt for attempt in frame["passes"] if attempt["name"] == "canonical_fragment"]
    assert len(canonical) == int(allowed)
    if allowed:
        assert canonical[0]["role"] == canonical[0]["disposition"] == "auxiliary"
        assert context_values(frame, "canonical.window_match") == [True]
        assert context_mapping(frame, "allowances.final")
    else:
        assert context_mapping(frame, "allowances.final") == {}
    if case_id == "canonical-no-rebuild":
        assert context_values(frame, "canonical.rebuild_denied") == [True]
        assert context_values(frame, "canonical.window_match") == []
        assert context_values(frame, "canonical.label_denied") == []


def test_dynamic_reference_filter_and_key_have_distinct_observed_values(monkeypatch):
    case = next(case for case in CASES if case["fixture"]["id"] == "dynamic-ref-and-label")
    _, error, _, report = run_baseline_case(case, monkeypatch, captured=True)
    assert error is None
    frame = report["frames"][0]
    assert context_values(frame, "source_item.ref") == ["#/first"]
    assert context_values(frame, "unique.ref_filter_raw") == ["#/condition"]
    assert context_values(frame, "unique.ref_key_raw") == ["#/key"]
    assert context_values(frame, "unique.ref_key") == ["#/key"]
    assert context_values(frame, "canonical.lookup_key") == ["#/key"]
    assert context_values(frame, "item.label") == ["text", "caption"]


@pytest.mark.parametrize("captured", [False, True])
@pytest.mark.parametrize("callback", ["_strip_headers_footers", "_dedup_nearby_lines"])
def test_callback_exception_identity_and_capture_reset(monkeypatch, callback, captured):
    sentinel = RuntimeError("identity must survive cleanup and observer bookkeeping")

    def fail(text):
        raise sentinel

    collector = audit.SourceCleanupCollector()
    with monkeypatch.context() as patch:
        patch.setattr(rag, callback, fail)
        with pytest.raises(RuntimeError) as caught:
            with audit.capture_source_cleanup(collector) if captured else nullcontext():
                rag._normalize_source_chunk_text("A\u00a0B", "body")
    assert caught.value is sentinel
    if captured:
        failed = collector.report()["frames"][0]
        assert failed["exception_type"] == "RuntimeError" and failed["committed_pass_id"] is None
    output, report = captured_call("Fresh\u00a0capture")
    assert output == "Fresh capture" and len(report["frames"]) == 1
    assert report["frames"][0]["parent_frame_id"] is None


@pytest.mark.parametrize("captured", [False, True])
@pytest.mark.parametrize("diagnostic_kind", ["getattribute", "data-descriptor"])
def test_exception_diagnostics_do_not_call_custom_metaclass_name(monkeypatch, captured, diagnostic_kind):
    diagnostic_calls = []

    class TrapMeta(type):
        def __getattribute__(cls, name):
            if name == "__name__" and diagnostic_kind == "getattribute":
                diagnostic_calls.append(name)
                raise AssertionError("exception name lookup is not a production effect")
            return super().__getattribute__(name)

        @property
        def __name__(cls):
            diagnostic_calls.append("name-descriptor")
            raise AssertionError("metaclass name descriptor is not a production effect")

    class TrapError(RuntimeError, metaclass=TrapMeta):
        pass

    sentinel = TrapError("original callback failure")

    def fail(text):
        raise sentinel

    monkeypatch.setattr(rag, "_strip_headers_footers", fail)
    collector, actual = audit.SourceCleanupCollector(), None
    try:
        with audit.capture_source_cleanup(collector) if captured else nullcontext():
            rag._normalize_source_chunk_text("Body", "body")
    except BaseException as exc:
        actual = exc
    assert actual is sentinel
    assert diagnostic_calls == []
    if captured:
        frame = collector.report()["frames"][0]
        assert frame["status"] == "failed" and frame["committed_pass_id"] is None


def test_raw_source_observation_does_not_invoke_metaclass_equality():
    def run(captured):
        calls = []

        class TrapMeta(type):
            def __eq__(cls, other):
                calls.append("class-equality")
                raise AssertionError("type admission must use identity")

        class Scalar(metaclass=TrapMeta):
            def __init__(self, name, value):
                self.name, self.value = name, value

            def __bool__(self):
                calls.append(("bool", self.name))
                return True

            def __str__(self):
                calls.append(("str", self.name))
                return self.value

        item = SimpleNamespace(label=Scalar("label", "text"), self_ref=Scalar("ref", "#/raw"),
                               text=Scalar("text", "Date:"))
        collector = audit.SourceCleanupCollector()
        with audit.capture_source_cleanup(collector) if captured else nullcontext():
            output = rag._normalize_source_chunk_text("Date:\nDate:", "body", source_items=[item])
        return output, calls, collector.report() if captured else None

    default, observed = run(False), run(True)
    assert default[:2] == observed[:2] and observed[0] == "Date:"
    assert "class-equality" not in observed[1]
    frame = observed[2]["frames"][0]
    assert_complete_replay(frame)
    for site in ("item.label_raw", "source_item.ref_raw", "item.text_raw"):
        rows = [row for row in frame["context"] if row["site"] == site]
        assert rows and all(row["observed"] is False and row["value"] is None for row in rows)


@pytest.mark.parametrize("captured", [False, True])
def test_eager_canonical_lookup_error_precedes_missing_rebuild_authority(captured):
    sentinel, events = LookupError("generated eager override lookup"), []

    class RaisingOverride(dict):
        def get(self, key, default=None):
            events.append(("lookup", key))
            raise sentinel

    class Refs(set):
        def __contains__(self, key):
            events.append(("membership", key))
            return False

    item = SimpleNamespace(label="text", self_ref="#/source", text="Date:")
    collector = audit.SourceCleanupCollector()
    with pytest.raises(LookupError) as caught:
        with audit.capture_source_cleanup(collector) if captured else nullcontext():
            rag._normalize_source_chunk_text("Date:\nDate:", "body", source_items=[item],
                                             canonical_text_overrides=RaisingOverride({"present": "value"}), text_rebuild_refs=Refs())
    assert caught.value is sentinel and events == [("lookup", "#/source")]
    if captured:
        assert collector.report()["frames"][0]["exception_type"] == "LookupError"


@pytest.mark.parametrize("captured", [False, True])
def test_empty_reference_still_consumes_text_and_false_filter_skips_key(captured):
    events = []
    item = ScriptedItem("one", {"self_ref": ["", ""], "text": ["consumed despite empty ref"]}, events)
    collector = audit.SourceCleanupCollector()
    with audit.capture_source_cleanup(collector) if captured else nullcontext():
        assert rag._normalize_source_chunk_text("Keep", "body", source_items=[item]) == "Keep"
    assert [(row["field"], row["ordinal"]) for row in events] == [("label", 1), ("self_ref", 1), ("text", 1), ("self_ref", 2)]


def test_observation_does_not_add_truth_or_string_conversions():
    def run(captured):
        events = []

        class Scalar:
            def __init__(self, name, text, truth=True):
                self.name, self.text, self.truth = name, text, truth

            def __bool__(self):
                events.append(("bool", self.name))
                return self.truth

            def __str__(self):
                events.append(("str", self.name))
                return self.text

            def __repr__(self):
                raise AssertionError("observer must not inspect raw objects")

        class Item:
            @property
            def label(self):
                events.append(("get", "label"))
                return SimpleNamespace(value=Scalar("label.value", "text"))

            @property
            def self_ref(self):
                events.append(("get", "self_ref"))
                return Scalar("ref", "#/item")

            @property
            def text(self):
                events.append(("get", "text"))
                return Scalar("text", "", False)

            @property
            def orig(self):
                events.append(("get", "orig"))
                return Scalar("orig", "Date:")

        collector = audit.SourceCleanupCollector()
        with audit.capture_source_cleanup(collector) if captured else nullcontext():
            output = rag._normalize_source_chunk_text("Date:\nDate:", "body", source_items=[Item()])
        return output, events, collector.report() if captured else None

    default, observed = run(False), run(True)
    assert observed[:2] == default[:2]
    assert default[0] == "Date:"
    assert default[1].count(("get", "self_ref")) == 3
    assert default[1].count(("str", "ref")) == 2
    assert default[1].count(("get", "orig")) == 1
    assert default[1].count(("str", "orig")) == 1
    frame = observed[2]["frames"][0]
    raw = [row for row in frame["context"] if row["site"] in {
        "item.label_raw", "item.label_value_raw", "source_item.ref_raw", "item.text_raw", "item.orig_raw"}]
    assert raw and all(row["observed"] is False and row["value"] is None for row in raw)
    assert context_values(frame, "source_item.ref") == ["#/item"]
    assert context_values(frame, "item.source_text") == ["Date:"]


@pytest.mark.parametrize("text, expected_bool_calls", [("Body", 0), ("2003", 1), ("", 1)])
def test_preserve_identity_truthiness_keeps_original_short_circuit(text, expected_bool_calls):
    def run(captured):
        calls = []

        class Preserve:
            def __bool__(self):
                calls.append("preserve")
                return True

            def __repr__(self):
                raise AssertionError("raw preservation flag is not diagnostic text")

        collector = audit.SourceCleanupCollector()
        with audit.capture_source_cleanup(collector) if captured else nullcontext():
            output = rag._normalize_source_chunk_text(text, "body", preserve_source_identity=Preserve())
        return output, calls

    default, observed = run(False), run(True)
    assert default == observed
    assert len(default[1]) == expected_bool_calls


@pytest.mark.parametrize("captured", [False, True])
@pytest.mark.parametrize("genuine", [False, True], ids=["legacy-replacement", "genuine-core"])
def test_whole_core_alias_one_lookup_before_callback_arguments(monkeypatch, captured, genuine):
    original_core, events = chunking_core._normalize_text, []

    def replacement(text, *, strip_headers_footers_fn, dedup_nearby_lines_fn):
        events.append(("replacement", text))
        return dedup_nearby_lines_fn(strip_headers_footers_fn(text))

    def late_strip(text):
        events.append(("late-strip", text))
        return text + " kept"

    def dedup(text):
        events.append(("dedup", text))
        return text

    class CoreAlias:
        @property
        def _normalize_text(self):
            events.append(("core-lookup",))
            monkeypatch.setattr(rag, "_strip_headers_footers", late_strip)
            return original_core if genuine else replacement

    monkeypatch.setattr(rag, "_dedup_nearby_lines", dedup)
    monkeypatch.setattr(rag, "_chunking_core", CoreAlias())
    collector = audit.SourceCleanupCollector()
    with audit.capture_source_cleanup(collector) if captured else nullcontext():
        assert rag._normalize_source_chunk_text("Alpha", "body") == "Alpha kept"
    assert events.count(("core-lookup",)) == 1
    assert events[0] == ("core-lookup",)
    assert events[-2:] == [("late-strip", "Alpha"), ("dedup", "Alpha kept")]
    if captured:
        frame = collector.report()["frames"][0]
        if genuine:
            assert_complete_replay(frame)
        else:
            assert frame["status"] == "abstained"
            assert frame["final_text"] is None and frame["committed_pass_id"] is None


def test_limits_are_fixed_and_collector_has_no_external_hooks_or_budget_arguments():
    expected = {"max_frames": 64, "max_depth": 8, "max_input_chars": 4096, "max_state_chars": 16384,
                "max_events": 4096, "max_frame_trace_chars": 65536, "max_total_trace_chars": 262144}
    assert dict(audit.LIMITS) == expected
    with pytest.raises(TypeError):
        audit.LIMITS["max_frames"] = 1
    with pytest.raises(TypeError):
        audit.SourceCleanupCollector(limits={})
    with pytest.raises(TypeError):
        audit.SourceCleanupCollector(hook=lambda *args: None)


def test_single_use_context_active_report_rejection_and_detached_strict_json():
    collector = audit.SourceCleanupCollector()
    with audit.capture_source_cleanup(collector):
        with pytest.raises((RuntimeError, ValueError)):
            collector.report()
        with pytest.raises((RuntimeError, ValueError)):
            with audit.capture_source_cleanup(collector):
                pytest.fail("same collector reentry must fail")
        assert rag._normalize_source_chunk_text("A\u00a0B", "body") == "A B"
    pristine = collector.report()
    detached = collector.report()
    detached["frames"][0]["final_text"] = "tampered"
    detached["limits"]["max_frames"] = 0
    detached["frames"].clear()
    assert collector.report() == pristine
    assert json.loads(json.dumps(pristine, allow_nan=False)) == pristine
    with pytest.raises((RuntimeError, ValueError)):
        with audit.capture_source_cleanup(collector):
            pytest.fail("collector reuse must fail")


def test_distinct_nested_captures_restore_outer_after_inner_failure():
    outer, inner = audit.SourceCleanupCollector(), audit.SourceCleanupCollector()
    sentinel = RuntimeError("leave inner capture")
    with audit.capture_source_cleanup(outer):
        rag._normalize_source_chunk_text("First", "body")
        with pytest.raises(RuntimeError) as caught:
            with audit.capture_source_cleanup(inner):
                rag._normalize_source_chunk_text("Inner", "body")
                raise sentinel
        assert caught.value is sentinel
        rag._normalize_source_chunk_text("Last", "body")
    before, nested = outer.report(), inner.report()
    assert before["capture_id"] != nested["capture_id"]
    assert [row["final_text"] for row in before["frames"]] == ["First", "Last"]
    assert [row["final_text"] for row in nested["frames"]] == ["Inner"]
    assert all(row["parent_frame_id"] is None for row in before["frames"] + nested["frames"])


def test_saved_context_cannot_revive_a_closed_capture():
    collector = audit.SourceCleanupCollector()
    with audit.capture_source_cleanup(collector):
        rag._normalize_source_chunk_text("Before", "body")
        saved = contextvars.copy_context()
    before = collector.report()
    assert saved.run(rag._normalize_source_chunk_text, "After\u00a0close", "body") == "After close"
    assert collector.report() == before


def test_saved_callback_context_cannot_reopen_finished_wrapper_in_active_capture(monkeypatch):
    def run(invoke_saved):
        saved = []

        def strip(text):
            if text == "Seed":
                saved.append(contextvars.copy_context())
            return text

        with monkeypatch.context() as patch:
            patch.setattr(rag, "_strip_headers_footers", strip)
            collector = audit.SourceCleanupCollector()
            with audit.capture_source_cleanup(collector):
                assert rag._normalize_source_chunk_text("Seed", "body") == "Seed"
                assert len(saved) == 1
                if invoke_saved:
                    assert saved[0].run(rag._normalize_text, "Late\u00a0core") == "Late core"
                assert rag._normalize_source_chunk_text("Next", "body") == "Next"
        return collector.report()

    control, late = run(False), run(True)
    assert control["frames"] == late["frames"]
    assert late["status"] == "complete"
    assert [frame["final_text"] for frame in late["frames"]] == ["Seed", "Next"]


def test_nested_wrapper_failure_and_success_preserve_outer_frame_attribution(monkeypatch):
    sentinel = LookupError("nested callback failure")
    calls, nested = [], False

    def strip(text):
        nonlocal nested
        calls.append(("strip", text))
        if text == "Outer" and not nested:
            nested = True
            try:
                with pytest.raises(LookupError) as caught:
                    rag._normalize_source_chunk_text("Fail", "body")
                assert caught.value is sentinel
                assert rag._normalize_source_chunk_text("Inner\u00a0text", "body") == "Inner text"
            finally:
                nested = False
            return "Outer kept"
        if text == "Fail":
            raise sentinel
        return text

    monkeypatch.setattr(rag, "_strip_headers_footers", strip)
    expected = rag._normalize_source_chunk_text("Outer", "body")
    expected_calls = list(calls)
    calls.clear()
    output, report = captured_call("Outer")
    assert output == expected == "Outer kept" and calls == expected_calls
    frames = {frame["original_text"]: frame for frame in report["frames"]}
    outer, failed, inner = (frames[name] for name in ("Outer", "Fail", "Inner\u00a0text"))
    assert outer["parent_frame_id"] is None
    assert failed["parent_frame_id"] == inner["parent_frame_id"] == outer["frame_id"]
    assert failed["exception_type"] == "LookupError" and failed["committed_pass_id"] is None
    assert_complete_replay(outer)
    assert_complete_replay(inner)
    assert all(operation["input_text"] != "Inner\u00a0text"
               for attempt in outer["passes"] for operation in attempt["operations"])


def test_nested_facade_core_call_is_separate_operation_not_a_second_wrapper(monkeypatch):
    calls = []

    def strip(text):
        calls.append(text)
        if text == "Outer":
            assert rag._normalize_text("Inner\u00a0text") == "Inner text"
            return "Outer kept"
        return text

    monkeypatch.setattr(rag, "_strip_headers_footers", strip)
    expected = rag._normalize_source_chunk_text("Outer", "body")
    expected_calls = list(calls)
    calls.clear()
    output, report = captured_call("Outer")
    assert output == expected == "Outer kept" and calls == expected_calls
    assert len(report["frames"]) == 1
    frame = report["frames"][0]
    assert_complete_replay(frame)
    committed = next(attempt for attempt in frame["passes"] if attempt["disposition"] == "committed")
    operations = {operation["input_text"]: operation for operation in committed["operations"]}
    outer, inner = operations["Outer"], operations["Inner\u00a0text"]
    assert outer["parent_operation_id"] is None
    assert inner["parent_operation_id"] == outer["operation_id"]
    summaries = [step for step in committed["steps"] if "operation_id" in step]
    assert [step["operation_id"] for step in summaries] == [outer["operation_id"]]


def test_auxiliary_passes_inside_core_callback_keep_separate_replay_roots(monkeypatch):
    calls = []
    items = [SimpleNamespace(label="text", self_ref=ref, text="Date:\n") for ref in ("#/a", "#/b")]

    def strip(text):
        calls.append(text)
        if text == "Outer":
            allowances = rag._source_attested_duplicate_line_allowances(items)
            assert allowances == {"Date:": 2}
            return "Outer kept"
        return text

    monkeypatch.setattr(rag, "_strip_headers_footers", strip)
    expected = rag._normalize_source_chunk_text("Outer", "body")
    expected_calls = list(calls)
    calls.clear()
    output, report = captured_call("Outer")
    assert output == expected == "Outer kept" and calls == expected_calls
    assert len(report["frames"]) == 1
    frame = report["frames"][0]
    assert_complete_replay(frame)
    ordinary, first, second = frame["passes"]
    assert ordinary["name"] == "ordinary" and ordinary["disposition"] == "committed"
    outer_operation = ordinary["operations"][0]
    assert len(ordinary["operations"]) == 1
    for auxiliary in (first, second):
        assert auxiliary["name"] == "source_line"
        assert auxiliary["role"] == auxiliary["disposition"] == "auxiliary"
        assert auxiliary["parent_pass_id"] == ordinary["pass_id"]
        assert auxiliary["input_text"] == "Date:\n" and auxiliary["output_text"] == "Date:"
        assert len(auxiliary["operations"]) == 1
        operation = auxiliary["operations"][0]
        assert operation["parent_operation_id"] == outer_operation["operation_id"]
        assert operation["is_pass_root"] is True
        assert [step["operation_id"] for step in auxiliary["steps"]] == [operation["operation_id"]]


@pytest.mark.parametrize("case_id", ["markdown-list-duplicate", "figure-apostrophe"])
def test_transient_edits_are_retained_even_when_final_text_is_unchanged(case_id, monkeypatch):
    case = next(case for case in CASES if case["fixture"]["id"] == case_id)
    output, error, _, report = run_baseline_case(case, monkeypatch, captured=True)
    assert error is None and output == case["fixture"]["text"]
    frame = report["frames"][0]
    assert_complete_replay(frame)
    committed = next(attempt for attempt in frame["passes"] if attempt["disposition"] == "committed")
    assert len(committed["steps"]) >= 2
    assert any(step["before_sha256"] != step["after_sha256"] for step in committed["steps"])
    assert committed["input_sha256"] == committed["output_sha256"]
    if case_id == "markdown-list-duplicate":
        transient = "".join(step["inserted"] for step in committed["steps"])
        assert any("\ue100" <= char <= "\ue1ff" for char in transient)
        assert not any("\ue100" <= char <= "\ue1ff" for char in output)


def test_numeric_rescue_discards_ordinary_pass_and_restarts_original_core_only():
    output, report = captured_call("2003", preserve_source_identity=True)
    assert output == "2003"
    frame = report["frames"][0]
    assert_complete_replay(frame)
    attempts = [attempt for attempt in frame["passes"] if attempt["role"] == "output"]
    assert [attempt["disposition"] for attempt in attempts] == ["discarded", "committed"]
    ordinary, rescue = attempts
    assert ordinary["input_text"] == rescue["input_text"] == "2003"
    assert ordinary["output_text"] == "" and rescue["output_text"] == "2003"
    assert len(rescue["operations"]) == 1
    assert rescue["operations"][0]["input_text"] == "2003"
    assert all("operation_id" in step for step in rescue["steps"])


def test_footnote_marker_has_one_direct_core_pass_without_ordinary_cleanup():
    output, report = captured_call("7", "footnote")
    assert output == "7"
    frame = report["frames"][0]
    assert_complete_replay(frame)
    attempts = [attempt for attempt in frame["passes"] if attempt["role"] == "output"]
    assert len(attempts) == 1 and attempts[0]["disposition"] == "committed"
    assert len(attempts[0]["operations"]) == 1
    assert all("operation_id" in step for step in attempts[0]["steps"])


def test_sentinel_exhaustion_does_not_claim_temporary_protection(monkeypatch):
    case = next(case for case in CASES if case["fixture"]["id"] == "markdown-sentinels-exhausted")
    output, error, _, report = run_baseline_case(case, monkeypatch, captured=True)
    assert error is None and output == case["output"]
    frame = report["frames"][0]
    assert_complete_replay(frame)
    committed = next(attempt for attempt in frame["passes"] if attempt["disposition"] == "committed")
    assert len(committed["operations"]) == 1
    assert all("operation_id" in step for step in committed["steps"])
    assert context_values(frame, "markdown.decision") == ["sentinels_exhausted"]
    assert context_values(frame, "markdown.indent_sentinel") == []


@pytest.mark.parametrize("case_id, decision", [
    ("figure-token-count-mismatch", "token_count_mismatch"),
    ("figure-token-length-mismatch", "token_correspondence_mismatch"),
])
def test_rejected_figure_correspondence_has_no_claimed_restoration(case_id, decision, monkeypatch):
    case = next(case for case in CASES if case["fixture"]["id"] == case_id)
    output, error, _, report = run_baseline_case(case, monkeypatch, captured=True)
    assert error is None and output == case["output"]
    frame = report["frames"][0]
    assert context_values(frame, "figure.decision") == [decision]
    assert not any(step["rule_id"] == "source_apostrophe_restore" for attempt in frame["passes"] for step in attempt["steps"])


def test_report_before_capture_and_capture_wrong_type_are_rejected():
    with pytest.raises(RuntimeError):
        audit.SourceCleanupCollector().report()
    with pytest.raises(TypeError):
        with audit.capture_source_cleanup(object()):
            pytest.fail("unsupported collector must not be entered")


@pytest.mark.parametrize("kind", ["subclass", "surrogate"])
def test_unsupported_text_diagnostics_do_not_change_cleanup(kind):
    class Text(str):
        def encode(self, *args, **kwargs):
            raise AssertionError("observer cannot hash unsupported text subclass")

    text = Text("Body") if kind == "subclass" else "Body \ud800 text"
    expected = rag._normalize_source_chunk_text(text, "body")
    output, report = captured_call(text)
    assert output == expected
    frame = report["frames"][0]
    assert frame["status"] == "abstained" and frame["trace_complete"] is False
    assert frame["reason"] == ("unsupported_text" if kind == "subclass" else "unsupported_unicode")
    assert frame["original_text"] is frame["final_text"] is frame["committed_pass_id"] is None


def test_oversized_input_abstains_but_real_transform_and_callbacks_finish(monkeypatch):
    text = "A " * (audit.LIMITS["max_input_chars"] // 2 + 2)
    calls = []

    def strip(value):
        calls.append(("strip", len(value)))
        return value + "tail"

    def dedup(value):
        calls.append(("dedup", len(value)))
        return value

    monkeypatch.setattr(rag, "_strip_headers_footers", strip)
    monkeypatch.setattr(rag, "_dedup_nearby_lines", dedup)
    expected = rag._normalize_source_chunk_text(text, "body")
    expected_calls = list(calls)
    calls.clear()
    output, report = captured_call(text)
    assert output == expected and calls == expected_calls
    frame = report["frames"][0]
    assert frame["status"] == "abstained" and frame["reason"]
    assert frame["original_text"] is frame["final_text"] is None
    assert frame["original_sha256"] is frame["final_sha256"] is None
    assert frame["passes"] == [] and frame["committed_pass_id"] is None


def test_missing_genuine_core_cannot_claim_complete_unchanged_pass(monkeypatch):
    def bypass(text, *, dedup_nearby_lines_fn=None):
        return text

    monkeypatch.setattr(rag, "_normalize_source_markdown_text", bypass)
    output, report = captured_call("Body")
    assert output == "Body"
    frame = report["frames"][0]
    assert frame["status"] == "abstained" and frame["reason"] == "missing_core"
    assert frame["final_text"] is frame["committed_pass_id"] is None


def test_state_budget_does_not_interrupt_actual_callbacks(monkeypatch):
    calls = []
    expanded = "x" * (audit.LIMITS["max_state_chars"] + 1)

    def strip(text):
        calls.append("strip")
        return expanded

    def dedup(text):
        calls.append("dedup")
        return text + " done"

    monkeypatch.setattr(rag, "_strip_headers_footers", strip)
    monkeypatch.setattr(rag, "_dedup_nearby_lines", dedup)
    expected = rag._normalize_source_chunk_text("Seed", "body")
    expected_calls = list(calls)
    calls.clear()
    output, report = captured_call("Seed")
    assert output == expected == expanded + " done" and calls == expected_calls
    frame = report["frames"][0]
    assert frame["status"] == "abstained" and frame["reason"] == "state_budget"
    assert frame["passes"] == [] and frame["final_text"] is None


def test_depth_budget_does_not_reset_under_an_abstained_but_open_parent(monkeypatch):
    entered = []
    depth, maximum = 0, audit.LIMITS["max_depth"] + 3

    def strip(text):
        nonlocal depth
        depth += 1
        entered.append(depth)
        try:
            if depth < maximum:
                assert rag._normalize_source_chunk_text("Nested", "body") == "Nested"
            return text
        finally:
            depth -= 1

    monkeypatch.setattr(rag, "_strip_headers_footers", strip)
    expected = rag._normalize_source_chunk_text("Nested", "body")
    expected_entered = list(entered)
    entered.clear()
    output, report = captured_call("Nested")
    assert output == expected == "Nested" and entered == expected_entered
    assert len(report["frames"]) == maximum
    for ordinal, frame in enumerate(report["frames"], 1):
        assert frame["parent_frame_id"] == (None if ordinal == 1 else ordinal - 1)
        if ordinal > audit.LIMITS["max_depth"]:
            assert frame["status"] == "abstained" and frame["reason"] == "depth_budget"
            assert frame["passes"] == [] and frame["final_text"] is None
        else:
            assert_complete_replay(frame)


@pytest.mark.parametrize("budget", ["frame-characters", "events"])
def test_context_trace_budget_stops_retention_without_skipping_getters(budget):
    count = 40 if budget == "frame-characters" else audit.LIMITS["max_events"] + 1

    def run(captured):
        calls = []

        class Item:
            @property
            def label(self):
                calls.append("label")
                return "text" if budget == "frame-characters" else "section_header"

            @property
            def self_ref(self):
                calls.append("ref")
                return "#/item" if budget == "frame-characters" else ""

            @property
            def text(self):
                calls.append("text")
                return "Context " + "x" * 1000

        items = [Item() for _ in range(count)]
        collector = audit.SourceCleanupCollector()
        with audit.capture_source_cleanup(collector) if captured else nullcontext():
            output = rag._normalize_source_chunk_text("Body", "body", source_items=items)
        return output, calls, collector.report() if captured else None

    default, observed = run(False), run(True)
    assert observed[:2] == default[:2] and observed[0] == "Body"
    assert observed[1].count("label") >= count
    frame = observed[2]["frames"][0]
    assert frame["status"] == "abstained" and frame["reason"] == "trace_budget"
    assert frame["context"] == frame["passes"] == []


def test_total_character_budget_is_shared_across_successive_frames():
    collector = audit.SourceCleanupCollector()
    text = "Body " + "x" * 1495
    with audit.capture_source_cleanup(collector):
        outputs = [rag._normalize_source_chunk_text(text, "body") for _ in range(audit.LIMITS["max_frames"])]
    assert outputs == [text] * audit.LIMITS["max_frames"]
    report = collector.report()
    assert report["omitted_frame_count"] == 0
    assert report["frames"][0]["status"] == "complete"
    assert report["frames"][-1]["status"] == "abstained"
    assert report["frames"][-1]["reason"] == "trace_budget"


def test_frame_budget_retains_explicit_missing_coverage_without_stopping_cleanup():
    collector = audit.SourceCleanupCollector()
    expected = audit.LIMITS["max_frames"] + 2
    with audit.capture_source_cleanup(collector):
        outputs = [rag._normalize_source_chunk_text(f"Body {number}", "body") for number in range(expected)]
    assert outputs == [f"Body {number}" for number in range(expected)]
    report = collector.report()
    assert len(report["frames"]) == audit.LIMITS["max_frames"]
    assert report["omitted_frame_count"] == 2
    assert report["status"] != "complete"


def test_leaf_import_requires_only_standard_library_and_does_not_load_facade():
    root = Path(audit.__file__).resolve().parent
    script = """
import builtins, sys
sys.path.insert(0, sys.argv[1])
original = builtins.__import__
def guarded(name, *args, **kwargs):
    top = name.split('.', 1)[0]
    if top not in sys.stdlib_module_names and top != 'source_cleanup_audit':
        raise AssertionError('non-stdlib import: ' + name)
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
import source_cleanup_audit
assert 'rag' not in sys.modules
"""
    completed = subprocess.run([sys.executable, "-I", "-B", "-c", script, str(root)],
                               capture_output=True, text=True, timeout=10)
    assert completed.returncode == 0, completed.stderr
