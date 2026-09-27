"""Declaration-only recovery replay; generated data, no source authenticity.

The shared replay controls are also covered by the existing report-bound suite.
These tests exercise the separate report-free admission and its evidence limits.
"""

import copy

import pytest

import ocr_comparison
import ocr_crop_comparison as crops
import ocr_crop_uncertainty_journal as history
import ocr_evaluation
from test_ocr_crop_comparison import _report
from test_ocr_crop_uncertainty_comparison import _case, _journal
import test_ocr_crop_uncertainty_journal as jf


def _recover(case, value, **overrides):
    arguments = {"scope": case.scope, "anchor_report_sha256": case.report_sha,
                 "anchor_region_id": "a", **overrides}
    return history.replay_crop_journal_declaration(value, **arguments)


def _forbidden(*_args, **_kwargs):
    raise AssertionError("declaration recovery accessed a report or scorer")


def _forbid_report_and_scores(monkeypatch):
    monkeypatch.setattr(history, "_binding", _forbidden)
    monkeypatch.setattr(crops, "_report", _forbidden)
    monkeypatch.setattr(crops, "build_crop_reference", _forbidden)
    monkeypatch.setattr(crops, "compare_ocr", _forbidden)
    monkeypatch.setattr(ocr_comparison, "compare_ocr", _forbidden)
    monkeypatch.setattr(ocr_evaluation, "evaluate_ocr", _forbidden)


@pytest.mark.parametrize("operation", ["regions", "hardscan"])
@pytest.mark.parametrize("state", ["zero", "unresolved", "resolved", "dismissed"])
def test_declaration_replay_matches_report_bound_without_report_access(operation, state, monkeypatch):
    case = _case(_report(operation=operation, bbox=[0., 0., 1., 1.]))
    value = _journal(case, state, text="a\U0001f642e\u0301", tokens=["absent"])
    before = copy.deepcopy(value)
    expected = jf._replay(case, value)
    _forbid_report_and_scores(monkeypatch)
    actual = _recover(case, value)
    assert actual == expected
    assert set(actual) == {"journal", "declaration", "declaration_sha256"}
    assert not {"review", "comparison", "source_image", "consent", "approval_token"} & set(actual["declaration"])
    actual["journal"]["base"]["reference"] = "detached mutation"
    actual["declaration"]["reference"] = "another mutation"
    assert value == before


@pytest.mark.parametrize("field,bad", [
    ("report_sha256", "A" * 64), ("report_sha256", 0),
    ("record_sha256", "x" * 64), ("record_sha256", None),
    ("recipe_sha256", True), ("recipe_sha256", "0" * 64),
    ("region_id", "bad/id"), ("region_id", "b"),
    ("operation", "pages"), ("operation", False),
])
def test_bad_declared_anchor_refuses_before_shared_replay(field, bad, monkeypatch):
    case = _case()
    value = jf._new(case)
    value["binding"]["anchor"][field] = bad
    monkeypatch.setattr(history, "_replay_bound", _forbidden)
    with pytest.raises(ValueError):
        _recover(case, value)


@pytest.mark.parametrize("overrides", [
    {"anchor_report_sha256": "0" * 64}, {"anchor_report_sha256": False},
    {"anchor_region_id": "b"}, {"anchor_region_id": None},
    {"scope": None}, {"scope": {}},
])
def test_expected_manifest_pins_are_required(overrides, monkeypatch):
    case = _case()
    value = jf._new(case)
    monkeypatch.setattr(history, "_replay_bound", _forbidden)
    with pytest.raises(ValueError):
        _recover(case, value, **overrides)


def test_canonical_but_different_scope_refuses(monkeypatch):
    case = _case()
    value = jf._new(case)
    other = crops.crop_scope(_report(bbox=[.1, .1, .9, .9]), region_id="a")
    monkeypatch.setattr(history, "_replay_bound", _forbidden)
    with pytest.raises(ValueError):
        _recover(case, value, scope=other)


def test_reference_id_is_recomputed_not_trusted(monkeypatch):
    case = _case()
    value = jf._new(case)
    value["binding"]["reference_id"] = "crop-" + "0" * 64
    monkeypatch.setattr(history, "_replay_bound", _forbidden)
    with pytest.raises(ValueError):
        _recover(case, value)


def test_self_consistent_record_digest_is_only_a_declaration(monkeypatch):
    case = _case()
    value = jf._new(case, "authored")
    value["binding"]["anchor"]["record_sha256"] = "0" * 64
    binding = value["binding"]
    binding["reference_id"] = "crop-" + jf._sha({key: binding[key] for key in ("scope", "anchor")})
    value = jf._rehash(value)
    with pytest.raises(ValueError):
        jf._replay(case, value)  # The real report join detects the forged record.
    _forbid_report_and_scores(monkeypatch)
    assert _recover(case, value)["journal"] == value


@pytest.mark.parametrize("fault", ["head", "truncated", "after_image", "view", "extra"])
def test_recovery_does_not_repair_or_trim_malformed_history(fault):
    case = _case()
    value = _journal(case, "resolved")
    before = copy.deepcopy(value)
    if fault == "head":
        value["head_sha256"] = "0" * 64
    elif fault == "truncated":
        value["revisions"].pop()
    elif fault == "after_image":
        value["revisions"][1]["annotation_changes"][0]["annotation"]["kind"] = "illegible"
        value = jf._rehash(value)
    elif fault == "view":
        value["views"][0]["rgb_sha256"] = "0" * 64
    else:
        value["unexpected"] = True
    invalid = copy.deepcopy(value)
    with pytest.raises(ValueError):
        _recover(case, value)
    assert value == invalid
    assert _recover(case, before)["journal"] == before


@pytest.mark.parametrize("name,limit", [
    ("MAX_REVISIONS", 1), ("MAX_VIEWS", 0), ("MAX_ANNOTATIONS", 0),
    ("MAX_CHANGES", 1), ("MAX_READING_SCAN", 1),
    ("MAX_NODES", 10), ("MAX_DEPTH", 2),
])
def test_recovery_uses_complete_shared_replay_budgets(name, limit, monkeypatch):
    case = _case()
    value = _journal(case, "resolved")
    monkeypatch.setattr(history, name, limit)
    with pytest.raises(ValueError):
        _recover(case, value)


def test_complete_reference_envelope_still_enforces_real_byte_ceiling():
    case = _case()
    value = _journal(case, "resolved")
    reading = "\U0001f642" * 20_000
    value["base"]["reference"] = reading
    value["revisions"][1]["annotation_changes"][0]["annotation"]["resolution"]["reading"] = reading
    value = jf._rehash(value)
    assert len(history._bytes(value)) <= history.MAX_BYTES
    # The journal alone fits; its required current-state projection does not.
    with pytest.raises(ValueError):
        _recover(case, value)


def test_shared_core_retains_single_cumulative_reading_budget(monkeypatch):
    case = _case()
    value = jf._resolved(case, "foo")
    value = jf._transition(case, value, "anchor", raw_span={
        "reference_text_sha256": jf._text_sha("foo"), "start": 0, "end": 3})
    monkeypatch.setattr(history, "MAX_READING_SCAN", 11)
    with pytest.raises(ValueError):
        _recover(case, value)
    monkeypatch.setattr(history, "MAX_READING_SCAN", 12)
    assert _recover(case, value)["journal"] == value


@pytest.mark.parametrize("change", [False, True])
def test_append_returns_current_declaration_without_a_second_replay(change, monkeypatch):
    case = _case()
    value = jf._new(case, "not liable")
    actions = [jf._add(case)] if change else []
    views = [case.view] if change else []
    kwargs = {"anchor_report": case.report, "anchor_report_sha256": case.report_sha,
              "annotation_changes": actions, "reason": "explicit operator action", "views": views}
    expected = history.append_crop_journal(value, **kwargs)
    replayed = jf._replay(case, expected)
    calls, original = [], history._replay

    def replay(*args):
        calls.append(True)
        return original(*args)

    monkeypatch.setattr(history, "_replay", replay)
    actual = history.append_crop_journal_declaration(value, **kwargs)
    assert actual == replayed and calls == [True]
    actual["declaration"]["reference"] = "detached"
    assert value["base"]["reference"] == "not liable"
