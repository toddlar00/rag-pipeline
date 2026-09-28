"""Synthetic rule characterization and bounded recovery-fragment audit contracts."""

import copy
import hashlib
import json
import random
import subprocess
import sys

import pytest

import chunking_core
import cleanup_audit as audit
import ocr_recovery


SOURCE = "a" * 64
RECOVERY = "b" * 64


def recovery(text="Synthetic text.", *, count=1, failed=(), maximum=20):
    class Reader:
        page_count = count

        def native_text(self, number):
            return ""

        def retry(self, number):
            if number in failed:
                raise RuntimeError("SYNTHETIC FAILURE")
            lines = [] if not text else [{"text": text, "score": .9,
                                         "box": [[0, 0], [90, 0], [90, 20], [0, 20]]}]
            return {"text": text, "lines": lines, "mean_confidence": .9 if lines else None,
                    "raster": {"width": 100, "height": 100, "dpi": 300, "coordinate_system": "rendered_image_pixels"},
                    "engine": {"name": "rapidocr", "version": "synthetic", "min_score": 0., "max_side": 6000}}

    result = ocr_recovery.build_recovery_report(Reader(), source_sha256=SOURCE,
                                               policy=ocr_recovery.RetryPolicy(max_pages=maximum))
    result["evidence_sha256"] = None
    return result


def plan(text="Synthetic text.", *, entries=None):
    return {"schema_version": 1, "kind": "cleanup_audit_plan", "source_sha256": SOURCE,
            "recovery_sha256": RECOVERY, "profile": audit.PROFILE, "offset_unit": "raw_unicode_code_points",
            "entries": entries if entries is not None else [{"page_number": 1, "candidate_span": [0, len(text)]}]}


def build(text, **kwargs):
    return audit.build_cleanup_audit(recovery(text, **kwargs), plan(text), recovery_sha256=RECOVERY)


def corpus():
    parts = ["Alpha", "Beta", "10", "20", "not", "USD", "12(b)(6)", "\n", "\n\n\n", "  ", "\t",
             "\u00a0", "\u2018", "\u2019", "\u201c", "\u201d", "\u2013", "\u2014", "\ufb01", "\ufb02",
             "\ufb00", "\ufb03", "\ufb04", "\ufffd", "\u200b", "\u200c", "\u200d", "\u2060", "\ufeff",
             "\uf643", "\uf64c", "A\n", "\u25a0\n", "clientlawyer", "text.Word", "http:// perma . cc / A1 - B2",
             "wrap-\nword", "javascript:\u200balert(1)", "| same |\n| same |", "repeat\nrepeat", "[I] t's"]
    rng = random.Random(20260906)
    return [""] + parts + ["".join(rng.choices(parts, k=rng.randrange(1, 60))) for _ in range(500)]


def test_preinstrumentation_542_case_fingerprint_preserves_default_output():
    # Captured from the actual working-tree implementation BEFORE instrumentation.
    values = [chunking_core._normalize_text(text) for text in corpus()]
    raw = json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode()
    assert len(values) == 542
    assert hashlib.sha256(raw).hexdigest() == "b5ebe89f345afda9a1f8085101ae3f94dce11e5b5cbf53db4f819690faa64538"


def test_hook_observes_same_output_and_only_changed_actual_boundaries():
    for text in corpus():
        observed = []
        result = chunking_core._normalize_text(text, audit_hook=lambda *event: observed.append(event))
        assert result == chunking_core._normalize_text(text)
        state, previous = text, -1
        for rule, before, after in observed:
            assert before == state and before != after
            assert rule in audit.RULE_IDS and audit.RULE_IDS.index(rule) > previous
            previous, state = audit.RULE_IDS.index(rule), after
        assert state == result


@pytest.mark.parametrize("value", [None, False, 0, [], {}, ""])
def test_existing_falsy_input_behavior_is_unchanged(value):
    assert chunking_core._normalize_text(value) == ""


@pytest.mark.parametrize("value,error", [(3, AttributeError), ([1], AttributeError), (b"abc", TypeError)])
def test_existing_invalid_truthy_input_error_types_are_unchanged(value, error):
    with pytest.raises(error):
        chunking_core._normalize_text(value)


def test_existing_callbacks_order_outputs_and_errors_preserved():
    calls = []

    def strip(text):
        calls.append(("strip", text))
        return text + " X"

    def dedup(text):
        calls.append(("dedup", text))
        return text + " Y"

    assert chunking_core._normalize_text("Alpha\u00a0Beta", strip_headers_footers_fn=strip,
                                        dedup_nearby_lines_fn=dedup) == "Alpha Beta X Y"
    assert calls == [("strip", "Alpha Beta"), ("dedup", "Alpha Beta X")]
    sentinel = RuntimeError("synthetic callback error")

    def failing(*_):
        raise sentinel

    with pytest.raises(RuntimeError) as caught:
        chunking_core._normalize_text("text", strip_headers_footers_fn=failing)
    assert caught.value is sentinel
    with pytest.raises(RuntimeError) as caught:
        chunking_core._normalize_text("text  here", audit_hook=failing)
    assert caught.value is sentinel


def test_custom_callback_attribution_is_explicit_and_not_inner_rule_claim():
    observed = []
    output = chunking_core._normalize_text("a", strip_headers_footers_fn=lambda _: "b",
                                          dedup_nearby_lines_fn=lambda _: "c",
                                          audit_hook=lambda *event: observed.append(event))
    assert output == "c"
    assert [event[0] for event in observed] == ["header_footer_callback", "nearby_line_callback"]


@pytest.mark.parametrize("text,rule,risk", [
    ("A\nSubstantive body", "section_marker_line", "word_or_identifier_changed"),
    ("The value is 10–20.", "unicode_u2013", "punctuation_or_symbol_changed"),
    ("value\n123\nbody", "strip_headers_footers", "numeric_sequence_changed"),
    ("Name\nName", "dedup_nearby_lines", "repetition_reduced"),
    ("ID\ufffd10", "unicode_ufffd", "word_or_identifier_changed"),
    ("clientlawyer", "known_fused_term", "word_or_identifier_changed"),
    ("123", "strip_headers_footers", "all_text_removed"),
])
def test_actual_rule_attribution_and_risks_are_review_warnings(text, rule, risk):
    report = build(text)
    entry = report["entries"][0]
    assert entry["status"] == "changed" and report["requires_attention"]
    assert rule in [edit["rule_id"] for edit in entry["edits"]]
    assert risk in entry["risks"]
    assert report["accuracy_verified"] is report["canonical_extraction_modified"] is False


def test_edits_replay_forward_and_backward_exactly_without_claiming_source_offsets():
    text = "  ‘Alpha’\u00a0and ﬁne.\n\n\nRepeat\nRepeat\n42\nBody  "
    entry = build(text)["entries"][0]
    state = text
    for edit in entry["edits"]:
        assert audit._hash(state) == edit["before_sha256"]
        start, end = edit["before_span"]
        assert state[start:end] == edit["removed"]
        state = state[:start] + edit["inserted"] + state[end:]
        assert audit._hash(state) == edit["after_sha256"]
    assert state == entry["normalized_text"] == chunking_core._normalize_text(text)
    for edit in reversed(entry["edits"]):
        start, end = edit["after_span"]
        assert state[start:end] == edit["inserted"]
        state = state[:start] + edit["removed"] + state[end:]
    assert state == text


@pytest.mark.parametrize("text", ["§ 12(b)(6) does not apply.", "| Amount |\n| 10 |\n| 10 |", "😀 synthetic name e\u0301."])
def test_meaningful_unchanged_controls_and_raw_unicode(text):
    report = build(text)
    assert report["summary"]["unchanged"] == 1
    assert report["entries"][0]["normalized_text"] == text
    assert not report["requires_attention"]


@pytest.mark.parametrize("text", ["", "   \n\t"])
def test_genuine_empty_or_whitespace_is_not_failed_candidate(text):
    entry = build(text)["entries"][0]
    assert entry["status"] != "abstained" and entry["normalized_text"] == ""
    assert "empty_input" in entry["risks"]


def test_failure_deferred_and_unselected_keep_denominators_without_fabricated_text():
    saved = recovery("body", count=3, failed=(1,), maximum=1)
    recipe = plan(entries=[{"page_number": n, "candidate_span": None} for n in (1, 2, 3)])
    report = audit.build_cleanup_audit(saved, recipe, recovery_sha256=RECOVERY)
    assert report["summary"]["abstained"] == 3
    assert [row["reason"] for row in report["entries"]] == ["retry_failed", "deferred", "deferred"]
    assert all(row["original_text"] is row["normalized_text"] is None for row in report["entries"])


def test_partial_selection_and_adjacent_fragments_do_not_claim_page_completeness():
    saved = recovery("One. Two.", count=2)
    recipe = plan(entries=[{"page_number": 1, "candidate_span": [0, 4]}])
    report = audit.build_cleanup_audit(saved, recipe, recovery_sha256=RECOVERY)
    assert report["coverage"]["partially_selected_candidate_pages"] == [1]
    assert report["coverage"]["unselected_recovery_pages"] == [2]
    assert report["requires_attention"] and not report["source_completeness_verified"]
    recipe["entries"].append({"page_number": 1, "candidate_span": [4, 9]})
    report = audit.build_cleanup_audit(saved, recipe, recovery_sha256=RECOVERY)
    assert report["coverage"]["fully_selected_candidate_pages"] == [1]


@pytest.mark.parametrize("field,value", [
    ("schema_version", True), ("schema_version", 2), ("kind", []), ("profile", "rag"),
    ("source_sha256", "c"*64), ("recovery_sha256", "c"*64), ("offset_unit", "utf8_bytes"),
    ("entries", []), ("entries", {}), ("entries", [None]),
])
def test_strict_plan_headers(field, value):
    recipe = plan()
    recipe[field] = value
    with pytest.raises(ValueError):
        audit.build_cleanup_audit(recovery(), recipe, recovery_sha256=RECOVERY)


@pytest.mark.parametrize("span", [None, [True, 3], [0., 3], [-1, 3], [3, 2], [0, 99999], [0], "all", [0, 0]])
def test_available_candidate_requires_exact_bounded_span(span):
    recipe = plan(entries=[{"page_number": 1, "candidate_span": span}])
    with pytest.raises(ValueError):
        audit.build_cleanup_audit(recovery(), recipe, recovery_sha256=RECOVERY)


@pytest.mark.parametrize("entries", [
    [{"page_number": True, "candidate_span": [0, 1]}],
    [{"page_number": 0, "candidate_span": [0, 1]}],
    [{"page_number": 2, "candidate_span": [0, 1]}],
    [{"page_number": 1, "candidate_span": [1, 3]}, {"page_number": 1, "candidate_span": [2, 4]}],
    [{"page_number": 1, "candidate_span": [2, 3]}, {"page_number": 1, "candidate_span": [0, 1]}],
    [{"page_number": 1, "candidate_span": None}] * 2,
])
def test_plan_pages_reused_overlapping_or_unordered_spans_rejected(entries):
    with pytest.raises(ValueError):
        audit.build_cleanup_audit(recovery(), plan(entries=entries), recovery_sha256=RECOVERY)


def test_bounds_detachment_and_exact_recovery_validation(monkeypatch):
    saved, recipe = recovery(), plan()
    before = copy.deepcopy((saved, recipe))
    report = audit.build_cleanup_audit(saved, recipe, recovery_sha256=RECOVERY)
    report["entries"][0]["candidate_span"][0] = 4
    assert (saved, recipe) == before
    saved["summary"]["retry_failed"] = 999
    with pytest.raises(ValueError):
        audit.build_cleanup_audit(saved, recipe, recovery_sha256=RECOVERY)
    monkeypatch.setattr(audit, "MAX_TOTAL_CHARS", 2)
    with pytest.raises(ValueError):
        build("long")


@pytest.mark.parametrize("budget", ["MAX_ENTRY_TRACE_CHARS", "MAX_TOTAL_TRACE_CHARS", "MAX_STEP_CHARS"])
def test_trace_budget_abstains_without_partial_success_or_partial_edits(monkeypatch, budget):
    monkeypatch.setattr(audit, budget, 1)
    entry = build("Alpha\u00a0Beta\ufffd")["entries"][0]
    assert entry["status"] == "abstained" and entry["reason"] == "trace_budget"
    assert entry["normalized_text"] is None and entry["edits"] == []


def test_missing_instrumentation_or_unknown_rule_fails_closed(monkeypatch):
    monkeypatch.setattr(chunking_core, "_normalize_text", lambda text, **_: text + "unobserved")
    with pytest.raises(RuntimeError):
        build("text")


def test_dependency_light_no_runtime_model_or_rag_import():
    code = "import sys, cleanup_audit; assert not {'rag','docling','rapidocr','numpy','cv2','fitz','torch'} & sys.modules.keys()"
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("field,value", [("entries", [None] * 65), ("extra", True)])
def test_closed_plan_and_entry_count_limit(field, value):
    recipe = plan()
    recipe[field] = value
    with pytest.raises(ValueError):
        audit.build_cleanup_audit(recovery(), recipe, recovery_sha256=RECOVERY)


def test_actual_fragment_limit_and_unavailable_mapping_fail_closed():
    with pytest.raises(ValueError, match="fragment exceeds bounds"):
        build("x" * (audit.MAX_FRAGMENT_CHARS + 1))
    with pytest.raises(ValueError, match="unavailable cleanup input"):
        audit.build_cleanup_audit(recovery("x", failed=(1,)), plan("x"), recovery_sha256=RECOVERY)


def test_selected_lone_surrogate_rejected_without_echoing_text():
    with pytest.raises(ValueError) as caught:
        build("Synthetic\ud800")
    assert "Synthetic" not in str(caught.value)


def test_unknown_rule_and_nonmonotonic_observer_fail_closed(monkeypatch):
    for events in [
            [("unknown", "text", "changed")],
            [("outer_whitespace", "text", "changed"), ("safe_zero_width", "changed", "again")]]:
        def injected(text, *, audit_hook):
            for event in events:
                audit_hook(*event)
            return events[-1][2]

        monkeypatch.setattr(chunking_core, "_normalize_text", injected)
        with pytest.raises(RuntimeError):
            build("text")
