"""Generated crop-pack policy controls; no PDF decoding or OCR model calls.

The ``policy_pack`` fixture replaces only historical archive admission with an
explicit scalar-report boundary. Real crop report/reference/scoring validators
still run. These isolated policy cases are NOT archive integration evidence.
The separately named ``test_real_*`` cases use complete production archive,
receipt, disposition and report validators with generated inert declarations.
"""

import builtins
import copy
import hashlib
import importlib.metadata
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

import ocr_crop_comparison as crops
import ocr_crop_review_pack as packs
from test_ocr_crop_comparison import _recipe, _recovery, _report


SOURCE = "a" * 64


def _raw(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _replace_file(value, name, raw):
    value["files"][name] = raw
    manifest = value["manifest"]
    manifest["files"][name] = {"sha256": _sha(raw), "bytes": len(raw)}
    manifest["total_file_bytes"] = sum(item["bytes"] for item in manifest["files"].values())


@pytest.fixture
def policy_pack(monkeypatch):
    """Explicit archive-boundary double, never a substitute for archive tests."""
    recovery_raw = _raw(_recovery())
    recovery_sha = _sha(recovery_raw)
    calls = []

    def archives(files, *, source_sha256, recovery_sha256, page_count):
        calls.append(True)
        assert (source_sha256, recovery_sha256, page_count) == (SOURCE, recovery_sha, 2)
        result = {}
        for side in ("baseline", "retry"):
            report_raw = files[f"{side}/artifacts/report.json"]
            report = json.loads(report_raw)
            crops._report(report)  # Real full scalar report validator.
            result[side] = {"report": report,
                "artifact_sha256": {"report.json": _sha(report_raw)},
                "request_sha256": _sha(files[f"{side}/artifacts/manifest.json"]),
                "retained_input_sha256": {"recovery.json": recovery_sha},
                "validation_scope": packs.VALIDATION_SCOPE}
        return result

    monkeypatch.setattr(packs, "_archives", archives)

    def archive(report, *, tag):
        report_raw = _raw(report)
        artifacts = {name: _raw({"synthetic_archive_slot": name, "side": tag})
                     for name in packs.ARTIFACT_LIMITS}
        artifacts.update({"report.json": report_raw, "work/report.json": report_raw})
        retained = {name: b"synthetic retained input" for name in packs.INPUT_LIMITS
                    if name != "installation.json"}
        retained["recovery.json"] = recovery_raw
        return {"artifacts": artifacts, "retained_inputs": retained}

    def options(*, before=None, after=None, reference="not liable", critical="not liable",
                reviewed=False, baseline_id="a", retry_id="a"):
        before = _report("liable") if before is None else before
        after = _report("not liable", dpi=400) if after is None else after
        left, right = archive(before, tag="baseline"), archive(after, tag="retry")
        declaration = None
        if reviewed:
            tokens = [] if critical == "" else critical.split("\n")
            ref = crops.build_crop_reference(before,
                anchor_report_sha256=_sha(left["artifacts"]["report.json"]),
                anchor_region_id=baseline_id, reference=reference,
                critical_tokens=tokens, confirmed=True)
            result = crops.compare_crop_candidates(before, after, ref,
                baseline_report_sha256=_sha(left["artifacts"]["report.json"]),
                retry_report_sha256=_sha(right["artifacts"]["report.json"]),
                baseline_region_id=baseline_id, retry_region_id=retry_id)
            declaration = {"reference": ref, "comparison": result,
                           "source_image": {"width": 311, "height": 28, "rgb_sha256": "f" * 64}}
        return {"baseline": left, "retry": right, "source_sha256": SOURCE,
            "recovery_sha256": recovery_sha, "page_count": 2,
            "baseline_region_id": baseline_id, "retry_region_id": retry_id,
            "reference": reference, "critical_tokens_text": critical,
            "reviewed": declaration, "parent_pack_sha256": None}

    def validate(value):
        return packs.validate_crop_review_pack(value["manifest"], files=value["files"],
            source_sha256=SOURCE, recovery_sha256=recovery_sha, page_count=2)

    def recover(value):
        return packs.recover_crop_pack_draft(value["manifest"], value["files"]["review.json"],
            source_sha256=SOURCE, recovery_sha256=recovery_sha, page_count=2)

    return NS(options=options, validate=validate, recover=recover, calls=calls,
              binding={"source_sha256": SOURCE, "recovery_sha256": recovery_sha, "page_count": 2})


def test_policy_roundtrip_preserves_all_raw_slots_and_input_immutability(policy_pack):
    options = policy_pack.options()
    before = copy.deepcopy(options)
    value = packs.build_crop_review_pack(**options)
    result = policy_pack.validate(value)
    assert options == before
    assert set(value["files"]) == set(packs.SLOT_LIMITS) - packs._OPTIONAL
    for side in ("baseline", "retry"):
        for group, values in options[side].items():
            label = "inputs" if group == "retained_inputs" else group
            for name, raw in values.items():
                assert value["files"][f"{side}/{label}/{name}"] == raw
    assert result["pack_sha256"] == _sha(packs.encode_manifest(value["manifest"]))
    assert not packs.encode_manifest(value["manifest"]).endswith(b"\n")
    assert result["validation_scope"] == "historical_local_declarations"
    assert result["requires_attention"] is True and result["canonical_extraction_modified"] is False
    assert value["manifest"]["review_state"] == "draft"
    result["manifest"]["pair"]["baseline"]["region_id"] = "changed"
    result["review"]["draft"]["reference"] = "changed"
    assert value["manifest"]["pair"]["baseline"]["region_id"] == "a"
    assert json.loads(value["files"]["review.json"])["draft"]["reference"] == "not liable"


@pytest.mark.parametrize("reference,critical", [("", ""), ("  exact\ntext  ", "one\n"),
    ("draft", "\n\n"), ("draft", "one\r\ntwo"), ("\x00\U0001f642", " A \nA"),
    ("x" * 20_000, "x" * (64 * 257))],
    ids=["empty", "spacing", "blank-lines", "crlf", "escaped-unicode", "maximum-draft"])
def test_unreviewed_raw_draft_is_not_trimmed_or_semantically_promoted(policy_pack, reference, critical):
    value = packs.build_crop_review_pack(**policy_pack.options(reference=reference, critical=critical))
    recovered = policy_pack.recover(value)
    assert recovered["draft"] == {"reference": reference, "critical_tokens_text": critical}
    assert recovered["status"] == "unverified_saved_draft"
    assert set(recovered) == {"draft", "scope", "pack_sha256", "status", "requires_attention",
                              "canonical_extraction_modified"}


def test_recovery_does_not_read_archives_or_return_saved_metrics_and_authority(policy_pack, monkeypatch):
    value = packs.build_crop_review_pack(**policy_pack.options(reviewed=True))

    def forbidden(*_args, **_kwargs):
        raise AssertionError("recovery-only read must not admit archive evidence")

    monkeypatch.setattr(packs, "_archives", forbidden)
    value["files"] = {"review.json": value["files"]["review.json"]}
    recovered = policy_pack.recover(value)
    assert recovered["status"] == "unverified_saved_draft"
    assert set(recovered) == {"draft", "scope", "pack_sha256", "status", "requires_attention",
                              "canonical_extraction_modified"}
    assert recovered["requires_attention"] is True and recovered["canonical_extraction_modified"] is False
    assert all(key not in recovered for key in ("reviewed", "reference", "comparison", "image", "ticket", "confirmed"))


@pytest.mark.parametrize("field,bad", [("source_sha256", "b" * 64), ("recovery_sha256", "b" * 64),
    ("page_count", 3), ("page_count", True)])
def test_fixed_host_binding_mismatch_refuses_recovery(policy_pack, field, bad):
    value = packs.build_crop_review_pack(**policy_pack.options())
    binding = dict(policy_pack.binding, **{field: bad})
    with pytest.raises(ValueError):
        packs.recover_crop_pack_draft(value["manifest"], value["files"]["review.json"], **binding)


@pytest.mark.parametrize("field,bad", [("schema_version", True), ("kind", "ocr_review_draft"),
    ("review_state", "approved"), ("validation_scope", "authenticated"),
    ("requires_attention", 1), ("requires_attention", False), ("canonical_extraction_modified", 0),
    ("canonical_extraction_modified", True), ("total_file_bytes", True),
    ("parent_pack_sha256", "not-a-digest"), ("page_count", 0), ("page_count", 5001)])
def test_manifest_closed_typed_declarations(policy_pack, field, bad):
    value = packs.build_crop_review_pack(**policy_pack.options())
    value["manifest"][field] = bad
    with pytest.raises(ValueError):
        policy_pack.recover(value)


@pytest.mark.parametrize("extra", ["approved", "pending", "session_id", "pdf_path", "execution_ticket"])
def test_manifest_rejects_extra_session_or_path_authority(policy_pack, extra):
    value = packs.build_crop_review_pack(**policy_pack.options())
    value["manifest"][extra] = "not portable authority"
    with pytest.raises(ValueError):
        policy_pack.recover(value)


@pytest.mark.parametrize("slot", ["../source.pdf", "baseline/inputs/../source.pdf", "C:/source.pdf",
    "baseline\\inputs\\recovery.json", "/tmp/reference.json", "baseline/inputs/recovery.json/child",
    "baseline/artifacts/report.json:stream", "baseline/artifacts/REPORT.JSON", "source.pdf"])
def test_slot_names_are_closed_not_filesystem_paths(policy_pack, slot):
    value = packs.build_crop_review_pack(**policy_pack.options())
    value["files"][slot] = b"unsafe slot"
    with pytest.raises(ValueError):
        policy_pack.validate(value)
    value["manifest"]["files"][slot] = {"sha256": "b" * 64, "bytes": 1}
    with pytest.raises(ValueError):
        policy_pack.recover(value)


@pytest.mark.parametrize("slot", ["review.json", "baseline/artifacts/manifest.json",
    "retry/artifacts/work/report.json", "baseline/inputs/recovery.json", "retry/inputs/plan.json"])
def test_missing_required_raw_file_never_passes_full_validation(policy_pack, slot):
    value = packs.build_crop_review_pack(**policy_pack.options())
    del value["files"][slot]
    with pytest.raises(ValueError):
        policy_pack.validate(value)


@pytest.mark.parametrize("bad", [b"", bytearray(b"{}"), "{}", None, memoryview(b"{}")])
def test_slots_require_nonempty_exact_bytes(policy_pack, bad):
    options = policy_pack.options()
    options["baseline"]["artifacts"]["manifest.json"] = bad
    with pytest.raises(ValueError):
        packs.build_crop_review_pack(**options)
    assert not policy_pack.calls


def test_raw_hash_binding_does_not_accept_equal_parsed_json(policy_pack):
    value = packs.build_crop_review_pack(**policy_pack.options())
    value["files"]["baseline/artifacts/report.json"] += b"\n"
    with pytest.raises(ValueError):
        policy_pack.validate(value)


def test_rehashed_report_requires_matching_pair_pin(policy_pack):
    value = packs.build_crop_review_pack(**policy_pack.options())
    _replace_file(value, "baseline/artifacts/report.json", value["files"]["baseline/artifacts/report.json"] + b"\n")
    with pytest.raises(ValueError):
        policy_pack.validate(value)


def test_ordered_sides_and_request_pins_are_not_interchangeable(policy_pack):
    value = packs.build_crop_review_pack(**policy_pack.options())
    pair = value["manifest"]["pair"]
    pair["baseline"], pair["retry"] = pair["retry"], pair["baseline"]
    with pytest.raises(ValueError):
        policy_pack.validate(value)
    value = packs.build_crop_review_pack(**policy_pack.options())
    value["manifest"]["pair"]["retry"]["request_sha256"] = "b" * 64
    with pytest.raises(ValueError):
        policy_pack.validate(value)


@pytest.mark.parametrize("options", [{"bbox": [.101, .203, .798, .899]}, {"page_number": 2},
    {"crop_origin": (1., 0.)}, {"rotation": 90}, {"source": "b" * 64}])
def test_different_physical_scope_is_not_reopenable_as_same_crop(policy_pack, options):
    with pytest.raises(ValueError):
        packs.build_crop_review_pack(**policy_pack.options(after=_report(**options)))


def test_duplicate_rectangle_occurrence_is_bound_by_region_id(policy_pack):
    rows = [{"region_id": name, "page_number": 1, "bbox": [.1, .2, .8, .9]} for name in ("a", "b")]
    options = policy_pack.options(before=_report(rows=rows), after=_report(rows=rows), reviewed=True)
    value = packs.build_crop_review_pack(**options)
    value["manifest"]["pair"]["baseline"]["region_id"] = "b"
    with pytest.raises(ValueError):
        policy_pack.validate(value)


def test_missing_region_and_whole_page_are_not_synthesized_from_scope(policy_pack):
    for changes in ({"baseline_id": "missing"}, {"before": _recovery()}):
        with pytest.raises(ValueError):
            packs.build_crop_review_pack(**policy_pack.options(**changes))


def test_reviewed_roundtrip_replays_existing_metrics_without_approval(policy_pack):
    options = policy_pack.options(reviewed=True)
    value = packs.build_crop_review_pack(**options)
    result = policy_pack.validate(value)
    assert value["manifest"]["review_state"] == "historical_operator_declaration"
    assert result["review"]["reviewed"] == options["reviewed"]
    assert result["review"]["reviewed"]["comparison"]["status"] == "compared"
    assert result["review"]["reviewed"]["comparison"]["setting_differences"] == ["dpi"]
    assert result["requires_attention"] is True and "confirmed" not in result


@pytest.mark.parametrize("mutation", ["text", "critical", "anchor", "metric", "pair", "scope", "approval"])
def test_rehashed_reviewed_payload_tamper_fails_replay(policy_pack, mutation):
    value = packs.build_crop_review_pack(**policy_pack.options(reviewed=True))
    review = json.loads(value["files"]["review.json"])
    if mutation == "text":
        review["draft"]["reference"] = "other"
    elif mutation == "critical":
        review["draft"]["critical_tokens_text"] = "liable"
    elif mutation == "anchor":
        review["reviewed"]["reference"]["anchor"]["region_id"] = "other"
    elif mutation == "metric":
        review["reviewed"]["comparison"]["comparison"]["regression_detected"] = True
    elif mutation == "pair":
        review["reviewed"]["comparison"]["pair"]["retry"]["report_sha256"] = "b" * 64
    elif mutation == "scope":
        review["reviewed"]["reference"]["scope"]["page_number"] = 2
    else:
        review["reviewed"]["confirmed"] = True
    _replace_file(value, "review.json", _raw(review))
    with pytest.raises(ValueError):
        policy_pack.validate(value)


@pytest.mark.parametrize("critical", ["one\n", "\n", "one\r\ntwo"])
def test_partial_critical_draft_cannot_be_labelled_reviewed(policy_pack, critical):
    options = policy_pack.options(reviewed=True)
    options["critical_tokens_text"] = critical
    with pytest.raises(ValueError):
        packs.build_crop_review_pack(**options)


@pytest.mark.parametrize("after,expected", [(_report(""), "empty_candidate"),
    (_report(state="failed"), "retry_failed"),
    (_report(operation="hardscan", state="abstained", recipe=_recipe(bow=.01)), "abstained")])
def test_available_empty_and_unavailable_outcomes_survive_reviewed_pack(policy_pack, after, expected):
    value = packs.build_crop_review_pack(**policy_pack.options(after=after, reviewed=True))
    result = policy_pack.validate(value)["review"]["reviewed"]["comparison"]
    assert result["pair"]["retry"]["status"] == expected
    if expected == "empty_candidate":
        assert result["status"] == "compared" and result["coverage"]["scored_pairs"] == 1
    else:
        assert result["comparison"] is None and result["coverage"]["scored_pairs"] == 0
        assert result["reason"] == "candidate_unavailable"


def test_empty_reviewed_reference_preserves_null_rates(policy_pack):
    value = packs.build_crop_review_pack(**policy_pack.options(reference="", critical="", reviewed=True))
    result = policy_pack.validate(value)["review"]["reviewed"]["comparison"]
    assert result["comparison"]["records"][0]["baseline"]["character"]["error_rate"] is None


def test_saved_image_is_a_historical_declaration_not_a_rerender(policy_pack):
    options = policy_pack.options(reviewed=True)
    options["reviewed"]["source_image"]["rgb_sha256"] = "e" * 64
    value = packs.build_crop_review_pack(**options)
    assert policy_pack.validate(value)["review"]["reviewed"]["source_image"]["rgb_sha256"] == "e" * 64
    assert "image" not in policy_pack.recover(value)


@pytest.mark.parametrize("field,bad", [("width", True), ("height", 0), ("width", 1401),
    ("rgb_sha256", "not-a-sha")])
def test_historical_image_declaration_has_strict_bounded_fields(policy_pack, field, bad):
    options = policy_pack.options(reviewed=True)
    options["reviewed"]["source_image"][field] = bad
    with pytest.raises(ValueError):
        packs.build_crop_review_pack(**options)


@pytest.mark.parametrize("raw", [b'{"schema_version":1,"schema_version":1}', b'{"x":NaN}',
    b'{} trailing', b'\xff', b'[[]]', b'null', b'[' * 40 + b'0' + b']' * 40])
def test_review_json_strict_decode_and_depth(policy_pack, raw):
    value = packs.build_crop_review_pack(**policy_pack.options())
    _replace_file(value, "review.json", raw)
    with pytest.raises(ValueError):
        policy_pack.recover(value)


@pytest.mark.parametrize("field,bad", [("reference", "x" * 20_001),
    ("critical_tokens_text", "x" * (64 * 257 + 1)), ("reference", "\ud800"),
    ("reference", None), ("critical_tokens_text", [])],
    ids=["reference-over-limit", "critical-over-limit", "surrogate", "null", "list"])
def test_draft_boundaries_fail_before_archive_work(policy_pack, field, bad):
    options = policy_pack.options()
    options[field] = bad
    with pytest.raises(ValueError):
        packs.build_crop_review_pack(**options)
    assert not policy_pack.calls


def test_per_slot_and_total_raw_caps_precede_archive_work(policy_pack, monkeypatch):
    options = policy_pack.options()
    limits = dict(packs.SLOT_LIMITS, **{"baseline/artifacts/report.json": 1})
    monkeypatch.setattr(packs, "SLOT_LIMITS", limits)
    with pytest.raises(ValueError):
        packs.build_crop_review_pack(**options)
    assert not policy_pack.calls
    monkeypatch.setitem(limits, "baseline/artifacts/report.json", packs.ARTIFACT_LIMITS["report.json"])
    monkeypatch.setattr(packs, "MAX_PACK_BYTES", packs.MAX_MANIFEST_BYTES + 1)
    with pytest.raises(ValueError):
        packs.build_crop_review_pack(**options)
    assert not policy_pack.calls


def test_manifest_total_budget_without_large_allocations(policy_pack):
    value = packs.build_crop_review_pack(**policy_pack.options())
    manifest = value["manifest"]
    for name in ("baseline/artifacts/report.json", "retry/artifacts/report.json"):
        manifest["files"][name]["bytes"] = packs.SLOT_LIMITS[name]
    manifest["total_file_bytes"] = sum(v["bytes"] for v in manifest["files"].values())
    with pytest.raises(ValueError):
        policy_pack.recover(value)


def test_parent_hash_changes_pack_identity_without_granting_authority(policy_pack):
    options = policy_pack.options()
    first = packs.build_crop_review_pack(**options)
    first_sha = _sha(packs.encode_manifest(first["manifest"]))
    options["parent_pack_sha256"] = first_sha
    second = packs.build_crop_review_pack(**options)
    assert second["manifest"]["parent_pack_sha256"] == first_sha
    assert _sha(packs.encode_manifest(second["manifest"])) != first_sha
    assert policy_pack.recover(second)["status"] == "unverified_saved_draft"


def test_optional_installation_bytes_are_bound_without_other_optional_slots(policy_pack):
    options = policy_pack.options()
    options["retry"]["retained_inputs"]["installation.json"] = b'{"synthetic":true}'
    value = packs.build_crop_review_pack(**options)
    assert value["files"]["retry/inputs/installation.json"] == b'{"synthetic":true}'
    assert "baseline/inputs/installation.json" not in value["files"]
    policy_pack.validate(value)


def _real_options(*, baseline_operation="regions", retry_operation="regions", state="candidate",
                  installation=False, reference="synthetic region", critical="synthetic\nregion", reviewed=True):
    """Real archive/report/receipt/disposition validators; inert declarations only."""
    from test_ocr_disposition_archive import archive_fixture

    left = archive_fixture(baseline_operation, text="synthetic", installation=installation)
    right = archive_fixture(retry_operation, dpi=400, state=state, installation=installation)
    before, after = (json.loads(item["artifacts"]["report.json"]) for item in (left, right))
    declaration = None
    if reviewed:
        ref = crops.build_crop_reference(before, anchor_report_sha256=_sha(left["artifacts"]["report.json"]),
            anchor_region_id="r00", reference=reference,
            critical_tokens=[] if critical == "" else critical.split("\n"), confirmed=True)
        result = crops.compare_crop_candidates(before, after, ref,
            baseline_report_sha256=_sha(left["artifacts"]["report.json"]),
            retry_report_sha256=_sha(right["artifacts"]["report.json"]),
            baseline_region_id="r00", retry_region_id="r00")
        declaration = {"reference": ref, "comparison": result,
                       "source_image": {"width": 311, "height": 28, "rgb_sha256": "f" * 64}}
    return {"baseline": {key: left[key] for key in ("artifacts", "retained_inputs")},
        "retry": {key: right[key] for key in ("artifacts", "retained_inputs")},
        "source_sha256": left["source_sha256"],
        "recovery_sha256": _sha(left["retained_inputs"]["recovery.json"]), "page_count": left["page_count"],
        "baseline_region_id": "r00", "retry_region_id": "r00", "reference": reference,
        "critical_tokens_text": critical, "reviewed": declaration}


def _real_binding(options):
    return {key: options[key] for key in ("source_sha256", "recovery_sha256", "page_count")}


def _validate_real(value, options):
    return packs.validate_crop_review_pack(value["manifest"], files=value["files"], **_real_binding(options))


@pytest.mark.parametrize("baseline_operation,retry_operation", [
    ("regions", "regions"), ("regions", "hardscan"), ("hardscan", "regions"), ("hardscan", "hardscan")])
@pytest.mark.parametrize("state", ["candidate", "empty", "unavailable"])
@pytest.mark.parametrize("installation", [False, True])
def test_real_archive_validators_roundtrip_all_routes_states_and_installation_declarations(
        baseline_operation, retry_operation, state, installation):
    options = _real_options(baseline_operation=baseline_operation, retry_operation=retry_operation,
                            state=state, installation=installation)
    before = copy.deepcopy(options)
    value = packs.build_crop_review_pack(**options)
    result = _validate_real(value, options)
    assert options == before
    assert result["baseline"]["validation_scope"] == result["retry"]["validation_scope"] == "historical_local_declarations"
    assert result["review"]["reviewed"] == options["reviewed"]
    assert result["retry"]["execution"]["summary"]["attempted"] == (0 if state == "unavailable" else 1)
    comparison = result["review"]["reviewed"]["comparison"]
    assert comparison["coverage"]["scored_pairs"] == (0 if state == "unavailable" else 1)
    assert comparison["pair"]["retry"]["status"] == {
        "candidate": "candidate_available", "empty": "empty_candidate", "unavailable": "retry_failed"}[state]
    assert result["requires_attention"] is True and result["canonical_extraction_modified"] is False


def test_real_archive_replay_and_draft_recovery_do_not_consult_current_models_runtime_or_files(monkeypatch):
    import model_artifacts
    import ocr_execution_receipt
    import ocr_experiment_runtime

    options = _real_options(installation=True)
    value = packs.build_crop_review_pack(**options)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("historical pack attempted current filesystem/model/runtime work")

    with monkeypatch.context() as patch:
        patch.setattr(importlib.metadata, "version", forbidden)
        patch.setattr(importlib.metadata, "distributions", forbidden)
        patch.setattr(model_artifacts, "load_model_artifact_registry", forbidden)
        patch.setattr(model_artifacts, "verified_installed_package_model", forbidden)
        patch.setattr(ocr_execution_receipt, "capture_execution_receipt", forbidden)
        patch.setattr(ocr_execution_receipt, "validate_installation_evidence", forbidden)
        patch.setattr(ocr_experiment_runtime, "capture_runtime_manifest", forbidden)
        patch.setattr(Path, "open", forbidden)
        patch.setattr(builtins, "open", forbidden)
        result = _validate_real(value, options)
        draft = packs.recover_crop_pack_draft(value["manifest"], value["files"]["review.json"],
                                             **_real_binding(options))
    assert result["baseline"]["execution"]["runtime"]["environment"]["python_full_version"] == "3.12.9"
    assert draft["status"] == "unverified_saved_draft" and "comparison" not in draft


@pytest.mark.parametrize("reference,critical", [("", ""), ("  draft\n", "one\n"), ("", "\n\r")])
def test_real_archive_pack_keeps_unreviewed_partial_and_explicit_empty_text(reference, critical):
    options = _real_options(reference=reference, critical=critical, reviewed=False)
    value = packs.build_crop_review_pack(**options)
    result = _validate_real(value, options)
    assert result["review"]["draft"] == {"reference": reference, "critical_tokens_text": critical}
    assert result["review"]["reviewed"] is None and value["manifest"]["review_state"] == "draft"


@pytest.mark.parametrize("mutation", ["raw-reference", "ordered-critical", "reviewed-reference", "metric",
    "anchor-occurrence", "comparison-source", "comparison-side", "image-authority"])
def test_real_archive_reviewed_replay_rejects_rehashed_semantic_tampering(mutation):
    options = _real_options()
    value = packs.build_crop_review_pack(**options)
    review = json.loads(value["files"]["review.json"])
    if mutation == "raw-reference":
        review["draft"]["reference"] = "synthetic region "
    elif mutation == "ordered-critical":
        review["draft"]["critical_tokens_text"] = "region\nsynthetic"
    elif mutation == "reviewed-reference":
        review["reviewed"]["reference"]["reference"] = "forged"
    elif mutation == "metric":
        review["reviewed"]["comparison"]["comparison"]["regression_detected"] = True
    elif mutation == "anchor-occurrence":
        review["reviewed"]["reference"]["anchor"]["region_id"] = "r01"
    elif mutation == "comparison-source":
        review["reviewed"]["comparison"]["source_sha256"] = "b" * 64
    elif mutation == "comparison-side":
        pair = review["reviewed"]["comparison"]["pair"]
        pair["baseline"], pair["retry"] = pair["retry"], pair["baseline"]
    else:
        review["reviewed"]["source_image"]["approved"] = True
    _replace_file(value, "review.json", _raw(review))
    with pytest.raises(ValueError):
        _validate_real(value, options)


def test_real_valid_reversed_archives_do_not_reuse_old_baseline_reference():
    options = _real_options()
    options["baseline"], options["retry"] = options["retry"], options["baseline"]
    with pytest.raises(ValueError):
        packs.build_crop_review_pack(**options)


@pytest.mark.parametrize("slot", ["baseline/artifacts/manifest.json", "baseline/artifacts/report.json",
    "baseline/artifacts/execution.json", "retry/artifacts/disposition.json", "retry/artifacts/work/report.json",
    "baseline/inputs/plan.json", "retry/inputs/requirements-full.lock", "retry/inputs/model-artifacts.lock.json"])
def test_real_archive_rehashing_outer_descriptor_does_not_bypass_inner_evidence(slot):
    options = _real_options()
    value = packs.build_crop_review_pack(**options)
    raw = value["files"][slot]
    if slot.endswith("manifest.json"):
        manifest = json.loads(raw)
        manifest["request"]["operation"] = "hardscan"
        raw = _raw(manifest)
    else:
        raw += b" "
    _replace_file(value, slot, raw)
    with pytest.raises(ValueError):
        _validate_real(value, options)


def test_real_archive_duplicate_json_key_is_rejected_after_outer_rehash():
    options = _real_options()
    value = packs.build_crop_review_pack(**options)
    raw = value["files"]["retry/artifacts/manifest.json"]
    raw = b'{"schema_version":1,' + raw[1:]
    _replace_file(value, "retry/artifacts/manifest.json", raw)
    with pytest.raises(ValueError):
        _validate_real(value, options)


def test_real_pack_draft_recovery_survives_unavailable_archives_without_promoting_them(monkeypatch):
    import ocr_disposition_archive

    options = _real_options()
    value = packs.build_crop_review_pack(**options)

    def forbidden(**_kwargs):
        raise AssertionError("draft recovery must not replay unavailable archives")

    monkeypatch.setattr(ocr_disposition_archive, "validate_disposition_archive", forbidden)
    draft = packs.recover_crop_pack_draft(value["manifest"], value["files"]["review.json"], **_real_binding(options))
    assert set(draft) == {"draft", "scope", "pack_sha256", "status", "requires_attention",
                          "canonical_extraction_modified"}
    assert draft["draft"]["reference"] == options["reference"] and draft["status"] == "unverified_saved_draft"


@pytest.mark.parametrize("field", ["kind", "schema_version", "requires_attention"])
def test_strict_primitive_types_are_not_subclasses_or_bool_equivalents(policy_pack, field):
    class Text(str):
        pass

    class Integer(int):
        pass

    value = packs.build_crop_review_pack(**policy_pack.options())
    value["manifest"][field] = Text("ocr_crop_review_pack") if field == "kind" else Integer(1)
    with pytest.raises(ValueError):
        policy_pack.recover(value)


def test_exact_descriptor_aggregate_boundary_without_allocating_claimed_files(policy_pack):
    value = packs.build_crop_review_pack(**policy_pack.options())
    manifest = value["manifest"]
    target = packs.MAX_PACK_BYTES - packs.MAX_MANIFEST_BYTES
    first, second = "baseline/artifacts/report.json", "retry/artifacts/report.json"
    manifest["files"][first]["bytes"] = packs.SLOT_LIMITS[first]
    others = sum(v["bytes"] for name, v in manifest["files"].items() if name != second)
    manifest["files"][second]["bytes"] = target - others
    manifest["total_file_bytes"] = target
    assert packs.validate_crop_pack_manifest(manifest, **policy_pack.binding)["total_file_bytes"] == target
    manifest["files"][second]["bytes"] += 1
    manifest["total_file_bytes"] += 1
    with pytest.raises(ValueError):
        packs.validate_crop_pack_manifest(manifest, **policy_pack.binding)


def test_real_archive_manifest_whitespace_changes_pack_identity_not_semantic_pair():
    options = _real_options()
    first = packs.build_crop_review_pack(**options)
    options["baseline"]["artifacts"]["manifest.json"] += b"\n"
    second = packs.build_crop_review_pack(**options)
    _validate_real(second, options)
    assert first["manifest"]["pair"] == second["manifest"]["pair"]
    assert first["files"]["review.json"] == second["files"]["review.json"]
    assert _sha(packs.encode_manifest(first["manifest"])) != _sha(packs.encode_manifest(second["manifest"]))
    assert second["files"]["baseline/artifacts/manifest.json"].endswith(b"\n\n")


def test_real_cleared_draft_shadows_old_review_in_new_parent_generation():
    options = _real_options()
    first = packs.build_crop_review_pack(**options)
    options.update(reference="", critical_tokens_text="",
                   parent_pack_sha256=_sha(packs.encode_manifest(first["manifest"])))
    with pytest.raises(ValueError):
        packs.build_crop_review_pack(**options)  # Old reviewed text must not shadow a clear.
    options["reviewed"] = None
    second = packs.build_crop_review_pack(**options)
    result = _validate_real(second, options)
    assert result["review"]["draft"] == {"reference": "", "critical_tokens_text": ""}
    assert result["review"]["reviewed"] is None
    assert json.loads(first["files"]["review.json"])["draft"]["reference"] == "synthetic region"


def test_real_recovery_only_never_exposes_even_a_tampered_historical_metric_declaration():
    options = _real_options()
    value = packs.build_crop_review_pack(**options)
    review = json.loads(value["files"]["review.json"])
    review["reviewed"]["comparison"] = {"claimed_accuracy": "untrusted"}
    _replace_file(value, "review.json", _raw(review))
    draft = packs.recover_crop_pack_draft(value["manifest"], value["files"]["review.json"], **_real_binding(options))
    assert draft["status"] == "unverified_saved_draft"
    assert "claimed_accuracy" not in json.dumps(draft) and "comparison" not in draft
    with pytest.raises(ValueError):
        _validate_real(value, options)


@pytest.mark.parametrize("field,bad", [("source_sha256", "b" * 64), ("recovery_sha256", "b" * 64),
    ("page_count", 2), ("baseline_region_id", "r01"), ("retry_region_id", "r01")])
def test_real_archive_host_and_occurrence_mismatches_refuse_build(field, bad):
    options = _real_options(reviewed=False)
    options[field] = bad
    with pytest.raises(ValueError):
        packs.build_crop_review_pack(**options)


class _NonExactText(str):
    """A JSON-shaped key is still required to have the exact built-in type."""


def _forbid_set_allocation(_value):
    raise AssertionError("malformed shape reached set allocation")


@pytest.mark.parametrize("size", [0, 2], ids=["undersized", "oversized"])
def test_fields_cardinality_refuses_before_set_allocation(monkeypatch, size):
    value = {str(index): None for index in range(size)}
    keys = {"required"}
    monkeypatch.setattr(packs, "set", _forbid_set_allocation, raising=False)
    with pytest.raises(ValueError, match="invalid crop review pack"):
        packs._fields(value, keys)


@pytest.mark.parametrize("key", [1, _NonExactText("required")], ids=["integer", "str-subclass"])
def test_fields_exact_key_type_refuses_before_set_allocation(monkeypatch, key):
    value = {key: None}
    keys = {"required"}
    assert len(value) == len(keys)
    monkeypatch.setattr(packs, "set", _forbid_set_allocation, raising=False)
    with pytest.raises(ValueError, match="invalid crop review pack"):
        packs._fields(value, keys)


@pytest.mark.parametrize("boundary", ["undersized", "oversized"])
def test_files_cardinality_refuses_before_set_allocation(policy_pack, monkeypatch, boundary):
    value = packs.build_crop_review_pack(**policy_pack.options())
    files = dict(value["files"])
    if boundary == "undersized":
        files.pop("review.json")
        assert len(files) == len(packs._REQUIRED) - 1
    else:
        files.update({name: b"synthetic installation" for name in packs._OPTIONAL})
        files["extra"] = b"not an admitted slot"
        assert len(files) == len(packs.SLOT_LIMITS) + 1
    monkeypatch.setattr(packs, "set", _forbid_set_allocation, raising=False)
    with pytest.raises(ValueError, match="invalid crop review pack"):
        packs._files(files)


@pytest.mark.parametrize("key", [1, _NonExactText("review.json")], ids=["integer", "str-subclass"])
def test_files_exact_key_type_refuses_before_set_allocation(policy_pack, monkeypatch, key):
    value = packs.build_crop_review_pack(**policy_pack.options())
    files = dict(value["files"])
    files[key] = files.pop("review.json")
    assert len(files) == len(packs._REQUIRED)
    monkeypatch.setattr(packs, "set", _forbid_set_allocation, raising=False)
    with pytest.raises(ValueError, match="invalid crop review pack"):
        packs._files(files)


@pytest.mark.parametrize("fault", ["undersized", "oversized", "integer-key", "str-subclass-key"])
def test_builder_input_preflight_precedes_input_set_allocation(policy_pack, monkeypatch, fault):
    options = policy_pack.options()
    inputs = options["baseline"]["retained_inputs"]
    if fault == "undersized":
        inputs.pop("recovery.json")
        assert len(inputs) == len(packs.INPUT_LIMITS) - 2
    elif fault == "oversized":
        inputs.update({"installation.json": b"synthetic installation", "extra": b"not an input"})
        assert len(inputs) == len(packs.INPUT_LIMITS) + 1
    else:
        key = 1 if fault == "integer-key" else _NonExactText("recovery.json")
        inputs[key] = inputs.pop("recovery.json")
        assert len(inputs) == len(packs.INPUT_LIMITS) - 1

    def guarded_set(value):
        # The two preceding real _fields calls may validate their small fixed
        # shapes. No input-name or input-inventory set may be built afterward.
        if value is inputs or value is packs.INPUT_LIMITS:
            raise AssertionError("malformed retained inputs reached set allocation")
        return builtins.set(value)

    monkeypatch.setattr(packs, "set", guarded_set, raising=False)
    with pytest.raises(ValueError, match="invalid crop review pack"):
        packs.build_crop_review_pack(**options)
    assert not policy_pack.calls
