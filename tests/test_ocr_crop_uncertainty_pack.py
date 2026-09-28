"""Real pure archive/journal/pack replay on generated declarations only.

No validator doubles in positive cases; no PDF, native OCR, storage, browser or
human-consent proof. Raster fixtures are explicit 72-point declarations, not
observed images. Parent snapshots model the trusted host read_for_revision seam.
"""

import copy
import hashlib
import json
from types import SimpleNamespace

import pytest

import ocr_comparison
import ocr_crop_comparison as crops
import ocr_crop_review_pack as legacy
import ocr_crop_uncertainty_comparison as comparison
import ocr_crop_uncertainty_journal as history
import ocr_crop_uncertainty_pack as packs
import ocr_evaluation
import test_ocr_crop_uncertainty_journal as jf
from test_ocr_disposition_archive import archive_fixture


def raw(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


def sha(value):
    return hashlib.sha256(value).hexdigest()


def binding(pack):
    return {key: pack["manifest"][key] for key in ("source_sha256", "recovery_sha256", "page_count")}


def validate(pack):
    return packs.validate_crop_review_pack_v2(pack["manifest"], files=pack["files"], **binding(pack))


def recover(pack):
    return packs.recover_crop_pack_draft_v2(pack["manifest"], pack["files"]["review.json"], **binding(pack))


def read_review(pack):
    return json.loads(pack["files"]["review.json"])


def replace_file(pack, name, value):
    pack["files"][name] = value
    pack["manifest"]["files"][name] = {"sha256": sha(value), "bytes": len(value)}
    pack["manifest"]["total_file_bytes"] = sum(row["bytes"] for row in pack["manifest"]["files"].values())


def replace_review(pack, review):
    replace_file(pack, "review.json", raw(review))


def case_for(left):
    report_raw = left["artifacts"]["report.json"]
    report = json.loads(report_raw)
    case = SimpleNamespace(report=report, report_sha=sha(report_raw),
                           scope=crops.crop_scope(report, region_id="a"))
    case.view = jf._view(case)
    return case


def options_from(pack):
    review = read_review(pack)
    result = {**binding(pack), "baseline_region_id": "a", "retry_region_id": "a",
              **copy.deepcopy(review["draft"]), "reviewed": copy.deepcopy(review["reviewed"]),
              "parent_pack_sha256": sha(legacy.encode_manifest(pack["manifest"]))}
    for side in ("baseline", "retry"):
        result[side] = {group: {name.removeprefix(f"{side}/{slot}/"): value
            for name, value in pack["files"].items() if name.startswith(f"{side}/{slot}/")}
            for group, slot in (("artifacts", "artifacts"), ("retained_inputs", "inputs"))}
    return result


def review_for(options, journal):
    case = case_for(options["baseline"])
    ref = comparison.build_crop_reference_v2(case.report, anchor_report_sha256=case.report_sha,
                                              journal=journal, confirmed=True)
    retry_raw = options["retry"]["artifacts"]["report.json"]
    result = comparison.compare_crop_candidates_v2(case.report, json.loads(retry_raw), ref,
        baseline_report_sha256=case.report_sha, retry_report_sha256=sha(retry_raw),
        baseline_region_id="a", retry_region_id="a")
    return {"reference": ref, "comparison": result, "source_image": case.view}


def pack_options(*, state="draft", dirty=False, parent=None, candidate_state="candidate"):
    """Genuine raw archive fixture; dirty draft always retains a real journal."""
    assert state in ("draft", "unresolved", "resolved")
    left = archive_fixture(region_id="a", bbox=[0., 0., 1., 1.])
    right = archive_fixture(region_id="a", bbox=[0., 0., 1., 1.], dpi=400, state=candidate_state)
    result = {"baseline": {key: left[key] for key in ("artifacts", "retained_inputs")},
        "retry": {key: right[key] for key in ("artifacts", "retained_inputs")},
        "source_sha256": left["source_sha256"],
        "recovery_sha256": sha(left["retained_inputs"]["recovery.json"]), "page_count": left["page_count"],
        "baseline_region_id": "a", "retry_region_id": "a", "reference": "synthetic region",
        "critical_tokens_text": "synthetic", "uncertainty": {"journal": None, "annotations": [], "dirty": False},
        "reviewed": None, "parent_pack_sha256": None if parent is None else sha(legacy.encode_manifest(parent["manifest"]))}
    if state != "draft" or dirty:
        case = case_for(result["baseline"])
        journal = jf._append(case, jf._new(case, "synthetic region", ["synthetic"]), jf._add(case))
        if state == "resolved" or dirty:
            journal = jf._transition(case, journal, "resolve", status="resolved",
                resolution={"decision": "reading_confirmed", "reading": "synthetic"})
        head = jf._replay(case, journal)["declaration"]
        annotations = copy.deepcopy(head["annotations"])
        if dirty:
            for row in annotations:
                row["raw_span"] = None
                if row["status"] == "resolved":
                    row.update(status="unresolved", resolution=None)
        result["uncertainty"] = {"journal": journal, "annotations": annotations, "dirty": dirty}
        if state != "draft" and not dirty:
            result["reviewed"] = review_for(result, journal)
    return result


def pack_fixture(*, state="draft", dirty=False, parent=None):
    """Shared real v2 fixture for IO/service tests; always a detached new pack."""
    return packs.build_crop_review_pack_v2(**pack_options(state=state, dirty=dirty, parent=parent))


def snapshot(pack):
    validator = legacy.validate_crop_review_pack if pack["manifest"]["schema_version"] == 1 else packs.validate_crop_review_pack_v2
    return {"evidence": validator(pack["manifest"], files=pack["files"], **binding(pack)),
            "pack": copy.deepcopy(pack), "manifest_bytes": legacy.encode_manifest(pack["manifest"])}


def ban_scorers(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("forbidden scorer or proof access")
    for module, name in ((crops, "compare_ocr"), (ocr_comparison, "compare_ocr"), (ocr_evaluation, "evaluate_ocr")):
        monkeypatch.setattr(module, name, forbidden)
    return forbidden


@pytest.mark.parametrize("state,expected", [("draft", "draft"),
    ("unresolved", "historical_unscorable_declaration"), ("resolved", "historical_scored_declaration")])
def test_real_roundtrip_versions_raw_slots_and_detachment(state, expected):
    options = pack_options(state=state)
    before = copy.deepcopy(options)
    value = packs.build_crop_review_pack_v2(**options)
    evidence = validate(value)
    assert options == before
    assert value["manifest"]["schema_version"] == evidence["review"]["schema_version"] == 2
    assert value["manifest"]["review_state"] == expected
    assert evidence["pack_sha256"] == sha(legacy.encode_manifest(value["manifest"]))
    for side in ("baseline", "retry"):
        for group, values in options[side].items():
            slot = "inputs" if group == "retained_inputs" else group
            for name, item in values.items():
                assert value["files"][f"{side}/{slot}/{name}"] is item
    evidence["review"]["draft"]["reference"] = "mutated"
    assert read_review(value)["draft"]["reference"] == "synthetic region"


@pytest.mark.parametrize("text,critical", [("", ""), (" raw \n", "OCR\n"), ("x", "a\r\nb"),
    ("\x00🙂", "a\n\n"), ("x" * 20_000, "y" * (64 * 257))],
    ids=["empty", "partial", "crlf", "unicode", "bounded-maximum"])
def test_null_journal_partial_raw_drafts_remain_exact(text, critical):
    options = pack_options()
    options.update(reference=text, critical_tokens_text=critical)
    value = packs.build_crop_review_pack_v2(**options)
    assert recover(value)["draft"] == {"reference": text, "critical_tokens_text": critical,
        "uncertainty": {"journal": None, "annotations": [], "dirty": False}}


@pytest.mark.parametrize("candidate_state", ["candidate", "empty", "unavailable"])
def test_unresolved_build_validate_and_recovery_never_score(monkeypatch, candidate_state):
    ban_scorers(monkeypatch)
    options = pack_options(state="unresolved", candidate_state=candidate_state)
    value = packs.build_crop_review_pack_v2(**options)
    evidence = validate(value)
    result = evidence["review"]["reviewed"]["comparison"]
    assert result["comparison"] is None and result["coverage"]["scored_pairs"] == 0
    assert result["coverage"]["reference_unresolved_pairs"] == 1
    assert result["uncertainty"]["unknown_character_count"] is None
    assert result["reason"] == ("candidate_unavailable" if candidate_state == "unavailable" else "reference_uncertain")
    assert recover(value)["status"] == "unverified_saved_draft"


@pytest.mark.parametrize("state", ["draft", "unresolved", "resolved"])
def test_recovery_only_two_files_has_no_report_scorer_image_or_consent(monkeypatch, state):
    value = pack_fixture(state=state)
    forbidden = ban_scorers(monkeypatch)
    for module, name in ((legacy, "_archives"), (crops, "_report"), (history, "replay_crop_journal"),
                         (comparison, "compare_crop_candidates_v2")):
        monkeypatch.setattr(module, name, forbidden)
    answer = recover(value)
    assert set(answer) == {"draft", "scope", "pack_sha256", "status", "requires_attention", "canonical_extraction_modified"}
    assert set(answer["draft"]) == {"reference", "critical_tokens_text", "uncertainty"}
    assert answer["requires_attention"] is True and answer["canonical_extraction_modified"] is False


def test_dirty_partial_and_aba_drafts_preserve_reset_rows_and_no_saved_review():
    options = pack_options(state="draft", dirty=True)
    options["critical_tokens_text"] = "partial\n"
    value = packs.build_crop_review_pack_v2(**options)
    assert recover(value)["draft"] == read_review(value)["draft"]
    assert read_review(value)["draft"]["uncertainty"]["annotations"][0]["status"] == "unresolved"
    options["critical_tokens_text"] = "synthetic"  # observed A→B→A, still dirty
    assert validate(packs.build_crop_review_pack_v2(**options))["review"]["draft"]["uncertainty"]["dirty"] is True


@pytest.mark.parametrize("fault", ["reference", "tokens", "annotation", "null-annotation", "dirty-review", "dirty-row"])
def test_draft_head_and_review_consistency(fault):
    options = pack_options(state="resolved")
    if fault == "reference":
        options["reference"] = "another"
    elif fault == "tokens":
        options["critical_tokens_text"] = "region"
    elif fault == "annotation":
        options["uncertainty"]["annotations"][0]["tentative_text"] = "other"
    elif fault == "null-annotation":
        options["uncertainty"]["journal"] = None
    elif fault == "dirty-review":
        options["uncertainty"]["dirty"] = True
    else:
        options["reviewed"] = None
        options["uncertainty"]["dirty"] = True  # resolved rows not reset
    with pytest.raises(ValueError):
        packs.build_crop_review_pack_v2(**options)


def test_absent_critical_genesis_authoring_allowed_but_resolved_review_refused():
    options = pack_options()
    case = case_for(options["baseline"])
    journal = jf._new(case, "", ["OCR"])
    options.update(reference="", critical_tokens_text="OCR",
        uncertainty={"journal": journal, "annotations": [], "dirty": False})
    assert validate(packs.build_crop_review_pack_v2(**options))["review"]["reviewed"] is None
    with pytest.raises(ValueError):
        review_for(options, journal)
    journal = jf._append(case, journal, jf._add(case, kind="illegible"))
    options["uncertainty"].update(journal=journal, annotations=jf._replay(case, journal)["declaration"]["annotations"])
    options["reviewed"] = review_for(options, journal)
    assert validate(packs.build_crop_review_pack_v2(**options))["review"]["reviewed"]["comparison"]["reason"] == "reference_uncertain"


@pytest.mark.parametrize("text", ["", "synthetic region", "é\nedge"], ids=["empty", "ordinary", "unicode-edge"])
def test_resolved_full_embedded_legacy_comparison_bytes(text):
    options = pack_options()
    case = case_for(options["baseline"])
    journal = jf._new(case, text)
    options.update(reference=text, critical_tokens_text="", uncertainty={"journal": journal, "annotations": [], "dirty": False})
    options["reviewed"] = review_for(options, journal)
    value = packs.build_crop_review_pack_v2(**options)
    ref = crops.build_crop_reference(case.report, anchor_report_sha256=case.report_sha,
        anchor_region_id="a", reference=text, critical_tokens=[], confirmed=True)
    retry_raw = options["retry"]["artifacts"]["report.json"]
    expected = crops.compare_crop_candidates(case.report, json.loads(retry_raw), ref,
        baseline_report_sha256=case.report_sha, retry_report_sha256=sha(retry_raw), baseline_region_id="a", retry_region_id="a")
    assert raw(validate(value)["review"]["reviewed"]["comparison"]["comparison"]) == raw(expected["comparison"])


@pytest.mark.parametrize("fault", ["metric", "reference", "journal", "profile", "image-shape", "image-scope", "mixed-reference", "mixed-review", "review-state"])
def test_saved_replay_and_schema_tampering_refused(fault):
    value = pack_fixture(state="resolved")
    review = read_review(value)
    saved = review["reviewed"]
    if fault == "metric":
        saved["comparison"]["comparison"]["forged"] = True
    elif fault == "reference":
        saved["reference"]["reference"] += " extra"
    elif fault == "journal":
        saved["reference"]["journal"]["head_sha256"] = "f" * 64
    elif fault == "profile":
        saved["source_image"]["preview_profile"] = "dpi288"
    elif fault == "image-shape":
        saved["source_image"] = {"width": 144, "height": 144, "rgb_sha256": "f" * 64}
    elif fault == "image-scope":
        saved["source_image"]["scope_sha256"] = "f" * 64
    elif fault == "mixed-reference":
        saved["reference"]["schema_version"] = 1
    elif fault == "mixed-review":
        review["schema_version"] = 1
    else:
        value["manifest"]["review_state"] = "historical_unscorable_declaration"
    replace_review(value, review)
    with pytest.raises(ValueError):
        validate(value)


@pytest.mark.parametrize("fault", ["source", "recovery", "page", "region", "reversed", "raw", "slot", "duplicate", "deep", "oversize"])
def test_exact_archive_manifest_and_decode_bindings(fault):
    value = pack_fixture()
    if fault in ("source", "recovery"):
        value["manifest"][fault + "_sha256"] = "f" * 64
    elif fault == "page":
        value["manifest"]["page_count"] = 2
    elif fault == "region":
        value["manifest"]["pair"]["baseline"]["region_id"] = "wrong"
    elif fault == "reversed":
        pair = value["manifest"]["pair"]
        pair["baseline"], pair["retry"] = pair["retry"], pair["baseline"]
    elif fault == "raw":
        value["files"]["baseline/artifacts/report.json"] += b" "
    elif fault == "slot":
        value["files"]["../report.json"] = b"{}"
    elif fault == "duplicate":
        replace_file(value, "review.json", b'{"schema_version":2,"schema_version":2}')
    elif fault == "deep":
        replace_file(value, "review.json", b"[" * 65 + b"0" + b"]" * 65)
    else:
        replace_file(value, "review.json", b" " * (legacy.MAX_REVIEW_BYTES + 1))
    with pytest.raises(ValueError):
        validate(value)


def test_one_journal_replay_per_full_pack_validation(monkeypatch):
    value = pack_fixture(state="resolved")
    original = history.replay_crop_journal
    calls = []
    def spy(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)
    monkeypatch.setattr(history, "replay_crop_journal", spy)
    validate(value)
    assert len(calls) == 1


def test_v1_upgrade_requires_explicit_genesis_not_inherited_consent():
    options = pack_options()
    options.pop("uncertainty")
    parent = legacy.build_crop_review_pack(**options)
    captured = snapshot(parent)
    child = pack_fixture(parent=parent)
    with pytest.raises(ValueError):
        packs.validate_crop_pack_revision_v2(captured, child)
    options = options_from(child)
    options["parent_pack_sha256"] = sha(legacy.encode_manifest(parent["manifest"]))
    case = case_for(options["baseline"])
    options["uncertainty"]["journal"] = jf._new(case, "synthetic region", ["synthetic"])
    result = packs.validate_crop_pack_revision_v2(captured, packs.build_crop_review_pack_v2(**options))
    assert result["review"]["reviewed"] is None


def test_null_parent_stays_null_or_establishes_genesis():
    parent = pack_fixture()
    captured = snapshot(parent)
    for state in ("draft", "unresolved"):
        assert packs.validate_crop_pack_revision_v2(captured, pack_fixture(state=state, parent=parent))["manifest"]["schema_version"] == 2


def test_exact_parent_history_can_append_and_preserve_originals():
    parent = pack_fixture(state="unresolved")
    options = options_from(parent)
    case = case_for(options["baseline"])
    journal = jf._transition(case, options["uncertainty"]["journal"], "dismiss", status="dismissed")
    options["uncertainty"].update(journal=journal, annotations=jf._replay(case, journal)["declaration"]["annotations"])
    options["reviewed"] = review_for(options, journal)
    captured = snapshot(parent)
    before = copy.deepcopy(captured)
    child = packs.build_crop_review_pack_v2(**options)
    result = packs.validate_crop_pack_revision_v2(captured, child)
    assert captured == before
    assert result["review"]["reviewed"]["comparison"]["uncertainty"]["dismissed"] == 1


@pytest.mark.parametrize("fault", ["null", "truncated", "base", "prefix", "parent-hash", "downgrade"])
def test_parent_continuity_cannot_reset_or_replace_history(fault):
    parent = pack_fixture(state="resolved")
    captured = snapshot(parent)
    options = options_from(parent)
    options["reviewed"] = None
    case = case_for(options["baseline"])
    if fault == "null":
        options["uncertainty"].update(journal=None, annotations=[])
    elif fault == "truncated":
        old = options["uncertainty"]["journal"]
        old["revisions"].pop()
        journal = jf._rehash(old)
        options["uncertainty"].update(journal=journal, annotations=jf._replay(case, journal)["declaration"]["annotations"])
    elif fault == "base":
        journal = jf._new(case, "synthetic region", ["synthetic"])
        options["uncertainty"].update(journal=journal, annotations=[])
    elif fault == "prefix":
        old = options["uncertainty"]["journal"]
        old["revisions"][0]["reason"] = "rewritten ancestry"
        journal = jf._rehash(old)
        options["uncertainty"]["journal"] = journal
    elif fault == "parent-hash":
        options["parent_pack_sha256"] = "f" * 64
    child = packs.build_crop_review_pack_v2(**options)
    if fault == "downgrade":
        child["manifest"]["schema_version"] = 1
    with pytest.raises(ValueError):
        packs.validate_crop_pack_revision_v2(captured, child)


def test_dirty_aba_parent_requires_appended_reset_before_clean_child():
    parent = pack_fixture(dirty=True)
    captured = snapshot(parent)
    options = options_from(parent)
    case = case_for(options["baseline"])
    journal = options["uncertainty"]["journal"]
    options["uncertainty"].update(dirty=False, annotations=jf._replay(case, journal)["declaration"]["annotations"])
    child = packs.build_crop_review_pack_v2(**options)
    with pytest.raises(ValueError):
        packs.validate_crop_pack_revision_v2(captured, child)
    journal = jf._append(case, journal, reset=True)
    options["uncertainty"].update(journal=journal, annotations=jf._replay(case, journal)["declaration"]["annotations"])
    assert packs.validate_crop_pack_revision_v2(captured, packs.build_crop_review_pack_v2(**options))["review"]["draft"]["uncertainty"]["dirty"] is False


@pytest.mark.parametrize("fault", ["raw-manifest", "evidence-review", "evidence-hash", "file-bytes", "extra-field"])
def test_parent_capture_must_equal_actual_retained_raw_snapshot(fault):
    parent = pack_fixture()
    captured = snapshot(parent)
    child = pack_fixture(parent=parent)
    if fault == "raw-manifest":
        captured["manifest_bytes"] += b"\n"
    elif fault == "evidence-review":
        captured["evidence"]["review"]["draft"]["reference"] += "x"
    elif fault == "evidence-hash":
        captured["evidence"]["pack_sha256"] = "f" * 64
    elif fault == "file-bytes":
        captured["pack"]["files"]["review.json"] += b" "
    else:
        captured["path"] = "browser path"
    with pytest.raises(ValueError):
        packs.validate_crop_pack_revision_v2(captured, child)


def test_parent_historical_scorer_is_not_repeated(monkeypatch):
    parent = pack_fixture(state="resolved")
    captured = snapshot(parent)
    options = options_from(parent)
    options["reviewed"] = None
    child = packs.build_crop_review_pack_v2(**options)
    ban_scorers(monkeypatch)
    assert packs.validate_crop_pack_revision_v2(captured, child)["review"]["reviewed"] is None


@pytest.mark.parametrize("value", [True, 1, 2.0, "2", None], ids=["bool", "v1", "float", "string", "null"])
def test_manifest_version_dispatch_is_exact(value):
    pack = pack_fixture()
    pack["manifest"]["schema_version"] = value
    with pytest.raises(ValueError):
        packs.validate_crop_pack_manifest_v2(pack["manifest"], **binding(pack))


def test_legacy_manifest_and_review_acceptance_unchanged():
    options = pack_options()
    options.pop("uncertainty")
    pack = legacy.build_crop_review_pack(**options)
    assert legacy.validate_crop_review_pack(pack["manifest"], files=pack["files"], **binding(pack))["manifest"]["schema_version"] == 1
    with pytest.raises(ValueError):
        validate(pack)
    with pytest.raises(ValueError):
        legacy.validate_crop_pack_manifest(pack_fixture()["manifest"], **binding(pack))


@pytest.mark.parametrize("reset", [False, True], ids=["no-reset", "later-reset"])
def test_dirty_parent_suffix_may_reset_after_another_valid_action(reset):
    parent = pack_fixture(dirty=True)
    captured = snapshot(parent)
    options = options_from(parent)
    case = case_for(options["baseline"])
    journal = jf._transition(case, options["uncertainty"]["journal"], "dismiss", status="dismissed", resolution=None)
    if reset:
        journal = jf._append(case, journal, reset=True)
    options["uncertainty"].update(journal=journal, dirty=False,
        annotations=jf._replay(case, journal)["declaration"]["annotations"])
    child = packs.build_crop_review_pack_v2(**options)
    if reset:
        result = packs.validate_crop_pack_revision_v2(captured, child)
        suffix = result["review"]["draft"]["uncertainty"]["journal"]["revisions"][-2:]
        assert [row["reset_anchors"] for row in suffix] == [False, True]
    else:
        with pytest.raises(ValueError):
            packs.validate_crop_pack_revision_v2(captured, child)


@pytest.mark.parametrize("fault", ["dirty-type", "annotation-container", "annotation-count", "journal-container", "extra-field"])
def test_uncertainty_container_preflight_before_archive_or_replay(monkeypatch, fault):
    options = pack_options()
    if fault == "dirty-type":
        options["uncertainty"]["dirty"] = 1
    elif fault == "annotation-container":
        options["uncertainty"]["annotations"] = {}
    elif fault == "annotation-count":
        options["uncertainty"]["annotations"] = [None] * 129
    elif fault == "journal-container":
        options["uncertainty"]["journal"] = []
    else:
        options["uncertainty"]["ignored"] = True
    def forbidden(*_args, **_kwargs):
        raise AssertionError("preflight crossed archive or journal replay boundary")
    monkeypatch.setattr(legacy, "_archives", forbidden)
    monkeypatch.setattr(history, "replay_crop_journal", forbidden)
    with pytest.raises(ValueError):
        packs.build_crop_review_pack_v2(**options)


@pytest.mark.parametrize("fault", ["bad-suffix", "after-image", "view", "binding", "surrogate"])
def test_recovery_rejects_malformed_history_without_dropping_suffix(monkeypatch, fault):
    value = pack_fixture(state="unresolved")
    review = read_review(value)
    journal = review["draft"]["uncertainty"]["journal"]
    if fault == "bad-suffix":
        journal["revisions"].append({})
    elif fault == "after-image":
        journal["revisions"][0]["annotation_changes"][0]["annotation"]["status"] = "dismissed"
        review["draft"]["uncertainty"]["journal"] = jf._rehash(journal)
    elif fault == "view":
        journal["views"][0]["width"] += 1
    elif fault == "binding":
        journal["binding"]["anchor"]["report_sha256"] = "f" * 64
    else:
        review["draft"]["reference"] = "\ud800"
    encoded = json.dumps(review, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
    replace_file(value, "review.json", encoded)
    forbidden = ban_scorers(monkeypatch)
    monkeypatch.setattr(legacy, "_archives", forbidden)
    with pytest.raises(ValueError):
        recover(value)


def test_total_pack_preflight_refuses_before_archive_admission(monkeypatch):
    options = pack_options()
    def forbidden(*_args, **_kwargs):
        raise AssertionError("aggregate overflow reached archives")
    monkeypatch.setattr(legacy, "MAX_PACK_BYTES", legacy.MAX_MANIFEST_BYTES + 1)
    monkeypatch.setattr(legacy, "_archives", forbidden)
    with pytest.raises(ValueError):
        packs.build_crop_review_pack_v2(**options)


def test_complete_history_reading_budget_is_shared_in_strict_and_recovery(monkeypatch):
    value = pack_fixture(state="resolved")
    monkeypatch.setattr(history, "MAX_READING_SCAN", 1)
    for reader in (validate, recover):
        with pytest.raises(ValueError):
            reader(value)


def test_complete_child_revision_budget_not_only_new_suffix(monkeypatch):
    parent = pack_fixture(state="resolved")
    captured = snapshot(parent)
    options = options_from(parent)
    case = case_for(options["baseline"])
    journal = jf._append(case, options["uncertainty"]["journal"], reset=True)
    options["reviewed"] = None
    options["uncertainty"].update(journal=journal, annotations=jf._replay(case, journal)["declaration"]["annotations"])
    child = packs.build_crop_review_pack_v2(**options)
    assert len(journal["revisions"]) == 3
    monkeypatch.setattr(history, "MAX_REVISIONS", 2)
    with pytest.raises(ValueError):
        packs.validate_crop_pack_revision_v2(captured, child)


def test_changed_but_valid_original_report_is_not_same_parent_pair():
    parent = pack_fixture()
    captured = snapshot(parent)
    options = options_from(parent)
    changed = archive_fixture(region_id="a", bbox=[0., 0., 1., 1.], dpi=400, text="different retained prediction")
    options["retry"] = {key: changed[key] for key in ("artifacts", "retained_inputs")}
    child = packs.build_crop_review_pack_v2(**options)
    assert validate(child)["manifest"]["scope"] == parent["manifest"]["scope"]
    with pytest.raises(ValueError):
        packs.validate_crop_pack_revision_v2(captured, child)


@pytest.mark.parametrize("candidate_state,expected", [("empty", "compared"), ("unavailable", "candidate_unavailable"), ("metric-limit", "metric_limit")])
def test_actual_empty_failure_and_metric_limit_review_states_remain_distinct(candidate_state, expected):
    options = pack_options(state="resolved", candidate_state="candidate" if candidate_state == "metric-limit" else candidate_state)
    if candidate_state == "metric-limit":
        # A genuine disposition line is capped at4096; use a valid line and a
        # valid longer reference whose unequal alignment exceeds25M cells.
        changed = archive_fixture(region_id="a", bbox=[0., 0., 1., 1.], dpi=400, text="x" * 4096)
        options["retry"] = {key: changed[key] for key in ("artifacts", "retained_inputs")}
        case = case_for(options["baseline"])
        options.update(reference="y" * 7000, critical_tokens_text="", uncertainty={
            "journal": jf._new(case, "y" * 7000), "annotations": [], "dirty": False})
        options["reviewed"] = review_for(options, options["uncertainty"]["journal"])
    value = packs.build_crop_review_pack_v2(**options)
    result = validate(value)["review"]["reviewed"]["comparison"]
    if expected == "compared":
        assert result["comparison"] is not None and result["reason"] is None
        assert value["manifest"]["review_state"] == "historical_scored_declaration"
    else:
        assert result["reason"] == expected and result["comparison"] is None
        assert value["manifest"]["review_state"] == "historical_unscorable_declaration"


@pytest.mark.parametrize("fault", ["reference-type", "comparison-version", "profile", "scope"])
def test_recovery_keeps_bounded_nested_schema_checks_without_authenticating_metrics(fault):
    value = pack_fixture(state="resolved")
    review = read_review(value)
    saved = review["reviewed"]
    if fault == "reference-type":
        saved["reference"] = []
    elif fault == "comparison-version":
        saved["comparison"]["schema_version"] = True
    elif fault == "profile":
        saved["source_image"]["preview_profile"] = "unknown"
    else:
        saved["source_image"]["scope_sha256"] = "f" * 64
    replace_review(value, review)
    with pytest.raises(ValueError):
        recover(value)
