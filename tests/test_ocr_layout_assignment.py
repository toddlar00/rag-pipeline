"""Synthetic adversarial contracts for explicit human layout resolution."""

from __future__ import annotations

import copy
import json

import pytest

import ocr_layout_assignment as policy
from test_ocr_docling import DIGESTS, PRIVATE, bbox, build, candidate, document, recovery


def context(doc=None, report=None):
    report = recovery() if report is None else report
    return {"recovery": report, "recovery_sha256": DIGESTS["recovery_sha256"],
            "proposals": build(document() if doc is None else doc, report), "proposals_sha256": "e" * 64}


def page(ctx):
    return policy.suggest_assignment_page(1, **ctx)


def plan(ctx, value=None, **kwargs):
    return policy.build_assignment_plan([page(ctx) if value is None else value], **ctx, **kwargs)


def preview(ctx, value=None):
    return policy.preview_assignment_plan(plan(ctx, value), **ctx)["pages"][0]


def test_seed_complete_order_exact_raw_text_and_input_immutability():
    ctx = context()
    before = copy.deepcopy(ctx)
    seeded = page(ctx)
    assert [a["region_ref"] for a in seeded["assignments"]] == [
        "#/texts/0", "#/texts/1", "#/texts/2", "#/texts/1", "#/texts/2", "#/tables/0"]
    value = plan(ctx, seeded)
    assert PRIVATE not in json.dumps(value)
    result = policy.preview_assignment_plan(value, **ctx)
    p = result["pages"][0]
    assert p["status"] == "complete" and p["line_order"] == [0, 1, 3, 2, 4, 5]
    lines = ctx["recovery"]["pages"][0]["candidate"]["lines"]
    assert p["original_text"] == "\n".join(line["text"] for line in lines)
    assert p["proposed_text"] == "\n".join(lines[i]["text"] for i in p["line_order"])
    assert p["order_source"] == "saved_docling" and p["unresolved_lines"] == []
    assert result["summary"] == {"pages": 1, "complete": 1, "partial": 0, "unresolved_lines": 0, "retained_engine_slots": 0}
    assert ctx == before
    value["pages"][0]["assignments"][0]["region_ref"] = None
    assert seeded["assignments"][0]["region_ref"] == "#/texts/0"


@pytest.mark.parametrize("ambiguity", ["unmatched", "multiple", "shape"])
def test_partial_seed_does_not_invent_text_or_confirmation(ambiguity):
    doc, value = document(), candidate()
    if ambiguity == "unmatched":
        doc["texts"][1]["prov"][0]["bbox"] = bbox((5, 25, 35, 50))
    elif ambiguity == "multiple":
        doc["texts"][2]["prov"][0]["bbox"] = bbox((5, 25, 95, 50))
    else:
        value["lines"][1]["box"] = [[10, 30], [40, 35], [40, 40], [10, 35]]
    ctx = context(doc, recovery([value]))
    assert ctx["proposals"]["pages"][0]["status"] == "abstained"
    proposed = page(ctx)
    assert proposed["assignments"][1]["region_ref"] is None
    result = preview(ctx, proposed)
    assert result["status"] == "partial" and 1 in result["unresolved_lines"]
    assert result["line_order"] is result["proposed_text"] is None
    with pytest.raises(ValueError, match="complete"):
        policy.build_assignment_review(plan(ctx, proposed), **ctx, confirmed_pages=[1])


@pytest.mark.parametrize("ambiguity,relation", [("unmatched", "outside_region"),
    ("multiple", "ambiguous_containment"), ("shape", "ambiguous_line_geometry")])
def test_explicit_human_resolution_is_labeled_without_geometry_relaxation(ambiguity, relation):
    doc, value = document(), candidate()
    if ambiguity == "unmatched":
        doc["texts"][1]["prov"][0]["bbox"] = bbox((5, 25, 35, 50))
    elif ambiguity == "multiple":
        doc["texts"][2]["prov"][0]["bbox"] = bbox((5, 25, 95, 50))
    else:
        value["lines"][1]["box"] = [[10, 30], [40, 35], [40, 40], [10, 35]]
    ctx = context(doc, recovery([value]))
    before = copy.deepcopy(ctx)
    proposed = page(ctx)
    for index in (1, 3):
        proposed["assignments"][index]["region_ref"] = "#/texts/1"
    result = preview(ctx, proposed)
    assert result["status"] == "complete" and result["line_order"] == [0, 1, 3, 2, 4, 5]
    assert result["line_diagnostics"][1]["relation"] == relation
    assert result["proposal_reasons"] == ctx["proposals"]["pages"][0]["reasons"]
    assert ctx == before


def test_explicit_retention_is_not_a_fake_region_assignment():
    ctx = context()
    proposed = page(ctx)
    proposed["assignments"][2].update(region_ref=None, retain_engine_slot=True)
    result = preview(ctx, proposed)
    assert result["status"] == "complete" and result["line_order"] == [0, 1, 2, 3, 4, 5]
    assert result["retained_engine_slots"] == [2]
    assert result["line_diagnostics"][2]["relation"] == "retained_engine_slot"
    assert result["line_diagnostics"][2]["region_ref"] is None
    review = policy.build_assignment_review(plan(ctx, proposed), **ctx, confirmed_pages=[1])
    assert review["pages"][0]["retained_engine_slots"] == [2]
    assert review["accuracy_verified"] is False


def furniture_context():
    doc, value = document(), candidate()
    doc["texts"][0]["children"].remove({"$ref": "#/tables/0"})
    doc["furniture"]["children"] = [{"$ref": "#/tables/0"}]
    value["lines"].insert(2, value["lines"].pop())
    value["text"] = "\n".join(line["text"] for line in value["lines"])
    return context(doc, recovery([value]))


def test_furniture_exact_engine_slot_survives_saved_and_human_body_orders():
    ctx = furniture_context()
    proposed = page(ctx)
    result = preview(ctx, proposed)
    assert result["line_order"] == [0, 1, 2, 4, 3, 5]
    assert result["furniture_locked_lines"] == result["retained_engine_slots"] == [2]
    proposed["body_region_order"] = ["#/texts/2", "#/texts/0", "#/texts/1"]
    result = preview(ctx, proposed)
    assert result["line_order"] == [3, 5, 2, 0, 1, 4]
    assert result["order_source"] == "operator_declared" and result["line_order"][2] == 2
    proposed["assignments"][2]["region_ref"] = "#/texts/1"
    with pytest.raises(ValueError, match="furniture"):
        plan(ctx, proposed)


def test_overlapping_furniture_body_cannot_silently_reclassify_furniture():
    doc, value = document(), candidate()
    doc["texts"][0]["children"].remove({"$ref": "#/tables/0"})
    doc["furniture"]["children"] = [{"$ref": "#/tables/0"}]
    doc["texts"][1]["prov"][0]["bbox"] = bbox((5, 25, 95, 90))
    ctx = context(doc, recovery([value]))
    proposed = page(ctx)
    assert proposed["assignments"][5]["region_ref"] is None
    proposed["assignments"][5]["region_ref"] = "#/texts/1"
    with pytest.raises(ValueError, match="furniture"):
        plan(ctx, proposed)
    proposed["assignments"][5].update(region_ref=None, retain_engine_slot=True)
    for index in (2, 4):
        proposed["assignments"][index]["region_ref"] = "#/texts/2"
    result = preview(ctx, proposed)
    assert result["furniture_locked_lines"] == [5]
    assert result["line_order"][5] == 5


def test_furniture_polygon_lock_survives_nonrectangular_line_geometry():
    value = candidate()
    value["lines"][5]["box"] = [[10, 72], [80, 85], [80, 89], [10, 76]]
    doc = document()
    doc["texts"][0]["children"].remove({"$ref": "#/tables/0"})
    doc["furniture"]["children"] = [{"$ref": "#/tables/0"}]
    ctx = context(doc, recovery([value]))
    proposed = page(ctx)
    assert proposed["assignments"][5]["region_ref"] is None
    proposed["assignments"][5]["region_ref"] = "#/texts/0"
    with pytest.raises(ValueError, match="furniture"):
        plan(ctx, proposed)


@pytest.mark.parametrize("mutate", [
    lambda p: p["assignments"].pop(),
    lambda p: p["assignments"].append(copy.deepcopy(p["assignments"][0])),
    lambda p: p["assignments"].reverse(),
    lambda p: p["assignments"][0].update(line_index=True),
    lambda p: p["assignments"][0].update(region_ref="#/texts/999"),
    lambda p: p["assignments"][0].update(region_ref=[]),
    lambda p: p["assignments"][0].update(retain_engine_slot=1),
    lambda p: p["assignments"][0].update(retain_engine_slot=True),
    lambda p: p["assignments"][0].update(text=PRIVATE),
    lambda p: p.update(page_number=True),
    lambda p: p.update(page_number=10**1000),
    lambda p: p.update(page_number=0),
    lambda p: p.update(line_order=[0, 1, 2, 3, 4, 5]),
    lambda p: p.update(body_region_order=[]),
    lambda p: p.update(body_region_order=["#/texts/0"] * 4),
    lambda p: p.update(body_region_order=["#/texts/0", "#/texts/1", "#/texts/2", "#/texts/999"]),
    lambda p: p.update(body_region_order=["#/texts/0", "#/texts/1", "#/texts/2", []]),
])
def test_bad_plan_pages_fail_closed_and_do_not_echo_private_values(mutate):
    ctx = context()
    proposed = page(ctx)
    mutate(proposed)
    with pytest.raises(ValueError) as caught:
        plan(ctx, proposed)
    assert PRIVATE not in str(caught.value)


@pytest.mark.parametrize("field,value", [("schema_version", True), ("schema_version", 2),
    ("source_sha256", "0" * 64), ("recovery_sha256", "0" * 64), ("proposals_sha256", "0" * 64),
    ("docling_sha256", "0" * 64), ("manifest_sha256", "0" * 64), ("page_count", True),
    ("coordinate_system", "original_page_display_fraction"), ("accuracy_verified", True),
    ("canonical_extraction_modified", 0), ("requires_attention", 1), ("recognition_rerun", True),
    ("history_authenticity", "authenticated")])
def test_plan_bindings_and_attestations_are_type_strict(field, value):
    ctx = context()
    payload = plan(ctx)
    payload[field] = value
    with pytest.raises(ValueError):
        policy.validate_assignment_plan(payload, **ctx)


def test_plan_deep_extra_and_cycle_rejected_before_serialization():
    ctx = context()
    payload = plan(ctx)
    payload["extra"] = payload
    with pytest.raises(ValueError):
        policy.validate_assignment_plan(payload, **ctx)
    payload = plan(ctx)
    payload["pages"][0]["assignments"][0]["extra"] = payload
    with pytest.raises(ValueError):
        policy.validate_assignment_plan(payload, **ctx)


@pytest.mark.parametrize("numbers", [None, [], [True], [0], [2], [1, 1], [1]*21, "1"])
def test_review_requires_explicit_unique_complete_page_confirmation(numbers):
    ctx = context()
    with pytest.raises(ValueError):
        policy.build_assignment_review(plan(ctx), **ctx, confirmed_pages=numbers)


@pytest.mark.parametrize("mutate", [
    lambda r: r["pages"][0].update(proposed_text="fabricated"),
    lambda r: r["pages"][0].update(original_text="fabricated"),
    lambda r: r["pages"][0].update(line_order=[0, 1, 1, 2, 4, 5]),
    lambda r: r["pages"][0]["line_order"].__setitem__(0, False),
    lambda r: r["pages"][0]["line_diagnostics"][0].update(relation="outside_region"),
    lambda r: r["pages"][0].update(retained_engine_slots=[0]),
    lambda r: r.update(operator_review="authenticated"),
    lambda r: r.update(accuracy_verified=True),
    lambda r: r.update(plan_sha256="invalid"),
])
def test_review_rebuilds_text_complete_permutation_and_diagnostics(mutate):
    ctx = context()
    result = policy.build_assignment_review(plan(ctx), **ctx, confirmed_pages=[1])
    assert policy.validate_assignment_review(result, **ctx) == result
    mutate(result)
    with pytest.raises(ValueError):
        policy.validate_assignment_review(result, **ctx)


def test_duplicate_text_and_empty_raw_line_remain_distinct_origin_slots():
    value = candidate()
    value["lines"][2]["text"] = value["lines"][1]["text"]
    value["lines"][4]["text"] = ""
    value["text"] = "\n".join(line["text"] for line in value["lines"])
    ctx = context(report=recovery([value]))
    result = preview(ctx)
    assert result["line_order"] == [0, 1, 3, 2, 4, 5]
    assert result["proposed_text"].split("\n")[4] == ""
    assert sorted(result["line_order"]) == list(range(6))


@pytest.mark.parametrize("history", [[{"sequence": True, "page_number": 1, "action": "line_assigned"}],
    [{"sequence": 0, "page_number": 1, "action": "line_assigned"}],
    [{"sequence": 10**1000, "page_number": 1, "action": "line_assigned"}],
    [{"sequence": 1, "page_number": True, "action": "line_assigned"}],
    [{"sequence": 1, "page_number": 1, "action": PRIVATE}],
    [{"sequence": 1, "page_number": 1, "action": "line_assigned"}]*2,
    [{"sequence": i+1, "page_number": 1, "action": "line_assigned"} for i in range(513)]])
def test_history_is_bounded_content_free_and_not_an_approval(history):
    ctx = context()
    with pytest.raises(ValueError):
        plan(ctx, history=history)


def test_history_max_boundary_and_detachment():
    ctx = context()
    history = [{"sequence": 1_000_000_000 - 511 + i, "page_number": 1, "action": "line_assigned"} for i in range(512)]
    result = plan(ctx, history=history)
    assert result["history"] == history
    assert result["acceptance"] == "manual_review_required"
    history[0]["action"] = "line_cleared"
    assert result["history"][0]["action"] == "line_assigned"


@pytest.mark.parametrize("count", [0, 21])
def test_page_limit_and_duplicates(count):
    ctx = context()
    with pytest.raises(ValueError):
        policy.build_assignment_plan([page(ctx)] * count, **ctx)
    with pytest.raises(ValueError):
        policy.build_assignment_plan([page(ctx)] * 2, **ctx)


def test_line_and_overlap_diagnostic_caps(monkeypatch):
    ctx = context()
    proposed = page(ctx)
    monkeypatch.setattr(policy, "MAX_DIAGNOSTIC_MATCHES", 1)
    with pytest.raises(ValueError, match="diagnostic"):
        page(ctx)
    with pytest.raises(ValueError, match="diagnostic"):
        plan(ctx, proposed)
    monkeypatch.undo()
    value = candidate()
    value["lines"] = [copy.deepcopy(value["lines"][0]) for _ in range(2001)]
    value["text"] = "\n".join(line["text"] for line in value["lines"])
    with pytest.raises(ValueError, match="unsupported"):
        page(context(report=recovery([value])))


@pytest.mark.parametrize("mode", ["failed", "no_geometry", "preprocessed", "geometry_mismatch", "not_selected"])
def test_unsupported_source_geometry_or_absent_candidate_cannot_be_manually_relaxed(mode):
    doc = document()
    report = recovery()
    if mode == "failed":
        report = recovery([ValueError("synthetic failure")])
    elif mode == "no_geometry":
        doc["pages"].clear()
    elif mode == "geometry_mismatch":
        doc["pages"]["1"]["size"]["width"] *= 2
    ctx = context(doc, report)
    if mode == "preprocessed":
        ctx["proposals"] = build(doc, report, effective_input_kind="preprocessed")
    with pytest.raises(ValueError):
        policy.suggest_assignment_page(2 if mode == "not_selected" else 1, **ctx)


def test_fixed_proposal_bytes_and_full_recovery_contract_remain_authoritative():
    ctx = context()
    payload = plan(ctx)
    with pytest.raises(ValueError):
        policy.validate_assignment_plan(payload, **{**ctx, "proposals_sha256": "f"*64})
    changed = copy.deepcopy(ctx)
    changed["recovery"]["pages"][0]["candidate"]["text"] = "not bound to lines"
    with pytest.raises(ValueError):
        policy.validate_assignment_plan(payload, **changed)


def test_multi_page_confirmation_excludes_partial_draft_and_no_region_retention_stays_explicit():
    doc = document()
    doc["pages"]["2"] = {"page_no": 2, "size": {"width": 24, "height": 24}}
    ctx = context(doc, recovery([candidate(), candidate()]))
    first = page(ctx)
    second = policy.suggest_assignment_page(2, **ctx)
    value = policy.build_assignment_plan([second, first], **ctx)
    assert [p["page_number"] for p in value["pages"]] == [1, 2]
    result = policy.preview_assignment_plan(value, **ctx)
    assert result["summary"] == {"pages": 2, "complete": 1, "partial": 1,
                                 "unresolved_lines": 6, "retained_engine_slots": 0}
    reviewed = policy.build_assignment_review(value, **ctx, confirmed_pages=[1])
    assert reviewed["confirmed_pages"] == [1]
    assert [p["page_number"] for p in reviewed["pages"]] == [1]
    assert len(reviewed["plan"]["pages"]) == 2
    with pytest.raises(ValueError):
        policy.build_assignment_review(value, **ctx, confirmed_pages=[1, 2])
    for assignment in second["assignments"]:
        assignment["retain_engine_slot"] = True
    value = policy.build_assignment_plan([first, second], **ctx)
    reviewed = policy.build_assignment_review(value, **ctx, confirmed_pages=[2, 1])
    assert reviewed["confirmed_pages"] == [1, 2]
    retained = reviewed["pages"][1]
    assert retained["line_order"] == retained["retained_engine_slots"] == list(range(6))
    assert all(line["region_ref"] is None for line in retained["line_diagnostics"])
    assert retained["body_region_order"] == [] and "no_regions" in retained["proposal_reasons"]
    assert reviewed["accuracy_verified"] is False


def test_maximum_line_boundary_is_reviewable():
    value = candidate()
    value["lines"] = [copy.deepcopy(value["lines"][0]) for _ in range(2000)]
    value["text"] = "\n".join(line["text"] for line in value["lines"])
    ctx = context(report=recovery([value]))
    assert preview(ctx)["line_order"] == list(range(2000))
