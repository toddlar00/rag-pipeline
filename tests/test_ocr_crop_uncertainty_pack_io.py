"""Versioned private pack I/O over genuine generated historical declarations.

The shared pack factory uses real pure validators and inert archive declarations;
it does not execute OCR, render PDFs, load current models or confer human consent.
Direct fixture writes are labeled separately from actual create-only publication.
"""

import builtins
import copy
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import ocr_comparison
import ocr_crop_comparison as crops
import ocr_crop_review_pack as legacy
import ocr_crop_review_pack_io as io
import ocr_crop_uncertainty_pack as policy
import ocr_crop_uncertainty_journal as history
import ocr_evaluation
from test_ocr_crop_review_pack_io import _identity, _sha, _write_pack
from test_ocr_crop_uncertainty_pack import pack_fixture


def _binding(pack):
    return {key: pack["manifest"][key] for key in ("source_sha256", "recovery_sha256", "page_count")}


def _review(pack):
    return json.loads(pack["files"]["review.json"])


def _retag_review(pack, review):
    raw = crops._bytes(review, legacy.MAX_REVIEW_BYTES)
    pack["files"]["review.json"] = raw
    pack["manifest"]["files"]["review.json"] = {"bytes": len(raw), "sha256": _sha(raw)}
    pack["manifest"]["total_file_bytes"] = sum(len(value) for value in pack["files"].values())


def _legacy_pack(current, *, reviewed=False):
    sides = {side: {
        "artifacts": {name: current["files"][f"{side}/artifacts/{name}"] for name in legacy.ARTIFACT_LIMITS},
        "retained_inputs": {name: current["files"][f"{side}/inputs/{name}"] for name in legacy.INPUT_LIMITS
                            if f"{side}/inputs/{name}" in current["files"]}}
        for side in ("baseline", "retry")}
    saved = None
    reference_text = "synthetic region" if reviewed else "partial authored text"
    if reviewed:
        pair = current["manifest"]["pair"]
        reports = {side: json.loads(sides[side]["artifacts"]["report.json"]) for side in sides}
        reference = crops.build_crop_reference(reports["baseline"],
            anchor_report_sha256=pair["baseline"]["report_sha256"],
            anchor_region_id=pair["baseline"]["region_id"], reference=reference_text,
            critical_tokens=[], confirmed=True)
        comparison = crops.compare_crop_candidates(reports["baseline"], reports["retry"], reference,
            baseline_report_sha256=pair["baseline"]["report_sha256"], retry_report_sha256=pair["retry"]["report_sha256"],
            baseline_region_id=pair["baseline"]["region_id"], retry_region_id=pair["retry"]["region_id"])
        saved = {"reference": reference, "comparison": comparison,
                 "source_image": {"width": 1, "height": 1, "rgb_sha256": "0" * 64}}
    return legacy.build_crop_review_pack(**sides, **_binding(current),
        baseline_region_id=current["manifest"]["pair"]["baseline"]["region_id"],
        retry_region_id=current["manifest"]["pair"]["retry"]["region_id"], reference=reference_text,
        critical_tokens_text="" if reviewed else "unfinished\n", reviewed=saved)


def _ban_scoring(monkeypatch):
    calls = []

    def forbidden(*_args, **_kwargs):
        calls.append(True)
        raise AssertionError("unresolved/recovery-only archive reached scoring")

    monkeypatch.setattr(crops, "compare_ocr", forbidden)
    monkeypatch.setattr(ocr_comparison, "compare_ocr", forbidden)
    monkeypatch.setattr(ocr_evaluation, "evaluate_ocr", forbidden)
    return calls


@pytest.fixture
def store_case(tmp_path):
    pack = pack_fixture(state="unresolved")
    root = tmp_path / "private-packs"
    root.mkdir()
    checks = []

    def verify():
        checks.append(True)

    def store():
        return io.CropReviewPackStore(root, **_binding(pack), verify_inputs=verify)

    return SimpleNamespace(pack=pack, root=root, store=store, checks=checks, verify=verify)


@pytest.mark.parametrize("state", ["draft", "unresolved", "resolved"])
def test_real_v2_publication_restart_and_exact_revision_capture(tmp_path, monkeypatch, state):
    pack = pack_fixture(state=state)
    root = tmp_path / "packs"
    root.mkdir()
    before = copy.deepcopy(pack)
    commits, checks = [], []
    original = io._publish_new_report

    def check():
        checks.append(True)

    def commit(temporary, target):
        commits.append((target, len(checks)))
        return original(temporary, target)

    monkeypatch.setattr(io, "_publish_new_report", commit)
    calls = _ban_scoring(monkeypatch) if state != "resolved" else []
    store = io.CropReviewPackStore(root, **_binding(pack), verify_inputs=check)
    saved = store.publish(pack, verify_current=check)
    folder = store._entries[saved["pack_id"]].path
    assert saved["status"] == "verified_complete" and saved["requires_attention"] is True
    assert commits[-1][0] == folder / "manifest.json"
    assert len(commits) == len(pack["files"]) + 1
    assert all(a[1] < b[1] for a, b in zip(commits, commits[1:]))
    assert all((folder / name).read_bytes() == raw for name, raw in pack["files"].items())
    assert (folder / "manifest.json").read_bytes() == legacy.encode_manifest(pack["manifest"])
    assert pack == before and not calls

    restarted = io.CropReviewPackStore(root, **_binding(pack), verify_inputs=check)
    [found] = restarted.catalog()
    assert found["status"] == "present_unverified" and found["pack_id"] != saved["pack_id"]
    with pytest.raises(io.CropReviewPackIOError):
        restarted.read(saved["pack_id"])
    revision = restarted.read_for_revision(found["pack_id"])
    assert set(revision) == {"evidence", "pack", "manifest_bytes"}
    assert revision["pack"] == pack
    assert revision["manifest_bytes"] == legacy.encode_manifest(pack["manifest"])
    evidence = revision["evidence"]
    assert evidence == restarted.read(found["pack_id"])
    assert evidence["validation_scope"] == "historical_local_declarations"
    assert evidence["requires_attention"] is True and evidence["canonical_extraction_modified"] is False
    assert evidence["review"] == _review(pack)
    if state == "unresolved":
        comparison = evidence["review"]["reviewed"]["comparison"]
        assert comparison["comparison"] is None and comparison["coverage"]["scored_pairs"] == 0
        assert comparison["coverage"]["unscorable_pairs"] == 1
    revision["pack"]["manifest"].clear()
    revision["pack"]["files"].clear()
    evidence["review"]["draft"].clear()
    assert restarted.read(found["pack_id"])["review"] == _review(pack)
    assert not calls


def test_v1_still_uses_original_bytes_and_never_imports_v2(tmp_path, monkeypatch):
    current = pack_fixture(state="draft")
    pack = _legacy_pack(current)
    expected = legacy.validate_crop_review_pack(pack["manifest"], files=pack["files"], **_binding(pack))
    imported, original = [], builtins.__import__

    def guarded(name, *args, **kwargs):
        if name == "ocr_crop_uncertainty_pack":
            imported.append(name)
            raise AssertionError("ordinary v1 imported v2 policy")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    root = tmp_path / "v1-packs"
    root.mkdir()
    store = io.CropReviewPackStore(root, **_binding(pack), verify_inputs=lambda: None)
    saved = store.publish(pack)
    assert store.read(saved["pack_id"]) == expected
    assert store.read_for_revision(saved["pack_id"])["pack"] == pack
    recovered = store.recover_draft(saved["pack_id"])
    assert recovered["draft"] == {"reference": "partial authored text", "critical_tokens_text": "unfinished\n"}
    assert not imported


def test_mixed_catalog_discovers_both_versions_without_selection_or_restored_consent(store_case):
    case = store_case
    old = _legacy_pack(case.pack, reviewed=True)
    _write_pack(case.root, old)
    _write_pack(case.root, case.pack)
    store = case.store()
    catalog = store.catalog()
    assert len(catalog) == 2 and len({item["pack_id"] for item in catalog}) == 2
    assert all(item["status"] == "present_unverified" and item["requires_attention"] for item in catalog)
    values = [store.read(item["pack_id"]) for item in catalog]
    assert {value["manifest"]["schema_version"] for value in values} == {1, 2}
    assert all(value["requires_attention"] is True and value["canonical_extraction_modified"] is False for value in values)
    assert {value["pack_sha256"] for value in values} == {
        _sha(legacy.encode_manifest(pack["manifest"])) for pack in (old, case.pack)}


def test_v2_operator_registration_is_pinned_and_has_no_browser_path_result(store_case, tmp_path):
    case = store_case
    outside = tmp_path / "operator-approved-folder"
    outside.mkdir()
    folder = _write_pack(outside, case.pack)
    store = case.store()
    result = store.register(folder, expected_manifest_sha256=_sha(legacy.encode_manifest(case.pack["manifest"])),
                            expected_root_identity=_identity(folder))
    assert set(result) == {"pack_id", "manifest_sha256", "status", "requires_attention"}
    assert store.read(result["pack_id"])["manifest"]["schema_version"] == 2
    assert store.check_revision_parent(result["pack_id"], expected_pack_sha256=result["manifest_sha256"]) is None


@pytest.mark.parametrize("version", [None, True, False, 0, 3, 1.0, 2.0, "1", "2", [], {}])
def test_exact_manifest_version_refuses_before_decoders_or_publication(store_case, monkeypatch, version):
    case = store_case
    manifest = copy.deepcopy(case.pack["manifest"])
    manifest["schema_version"] = version
    store = case.store()
    calls = []

    def forbidden(*_args, **_kwargs):
        calls.append(True)
        raise AssertionError("unsupported version reached a versioned decoder")

    monkeypatch.setattr(legacy, "validate_crop_pack_manifest", forbidden)
    monkeypatch.setattr(policy, "validate_crop_pack_manifest_v2", forbidden)
    with pytest.raises(io.CropReviewPackIOError):
        io._manifest(legacy.encode_manifest(manifest), _binding(case.pack))
    with pytest.raises(io.CropReviewPackIOError):
        store.publish({"manifest": manifest, "files": case.pack["files"]})
    assert not calls and not list(case.root.iterdir())


@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize("mutation", ["float_review", "boolean_review", "opposite_review", "reference_version", "comparison_version"])
def test_mixed_inner_versions_never_promote_or_publish(store_case, version, mutation):
    case = store_case
    pack = _legacy_pack(case.pack, reviewed=True) if version == 1 else copy.deepcopy(case.pack)
    validator = legacy.validate_crop_review_pack if version == 1 else policy.validate_crop_review_pack_v2
    assert validator(pack["manifest"], files=pack["files"], **_binding(pack))["pack_sha256"]
    review = _review(pack)
    if mutation == "float_review":
        review["schema_version"] = float(version)
    elif mutation == "boolean_review":
        review["schema_version"] = True
    elif mutation == "opposite_review":
        review["schema_version"] = 3 - version
    elif mutation == "reference_version":
        review["reviewed"]["reference"]["schema_version"] = 3 - version
    else:
        review["reviewed"]["comparison"]["schema_version"] = 3 - version
    _retag_review(pack, review)
    store = case.store()
    with pytest.raises(io.CropReviewPackIOError):
        store.publish(pack)
    assert not list(case.root.iterdir())


@pytest.mark.parametrize("fault", ["missing", "changed", "oversized", "extra", "link_like", "hardlink"])
def test_v2_recovery_only_reads_authored_files_and_never_scores(store_case, monkeypatch, tmp_path, fault):
    case = store_case
    # Fixture write, not a successful publication claim.
    folder = _write_pack(case.root, case.pack)
    target = folder / "baseline/artifacts/report.json"
    if fault == "missing":
        target.unlink()
    elif fault == "changed":
        target.write_bytes(b"damaged retained proof")
    elif fault == "oversized":
        with target.open("wb") as handle:
            handle.truncate(legacy.ARTIFACT_LIMITS["report.json"] + 1)
    elif fault == "extra":
        (folder / "unrelated-private-marker").write_bytes(b"retained")
    elif fault == "hardlink":
        os.link(target, tmp_path / "external-proof-link")
    store = case.store()
    [metadata] = store.catalog()
    observed, original = [], io.storage_policy.assert_no_link_components

    def path_check(path, *args, **kwargs):
        path = Path(path)
        observed.append(path)
        if path == target and fault == "link_like":
            raise ValueError("inert linked proof")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(io.storage_policy, "assert_no_link_components", path_check)
    with pytest.raises(io.CropReviewPackIOError):
        store.read(metadata["pack_id"])
    observed.clear()
    calls = _ban_scoring(monkeypatch)

    def no_full_validator(*_args, **_kwargs):
        pytest.fail("recovery attempted full proof validation")

    monkeypatch.setattr(policy, "validate_crop_review_pack_v2", no_full_validator)
    monkeypatch.setattr(legacy, "validate_crop_review_pack", no_full_validator)
    recovered = store.recover_draft(metadata["pack_id"])
    assert set(recovered) == {"draft", "scope", "pack_sha256", "status", "requires_attention",
                              "canonical_extraction_modified"}
    assert recovered["status"] == "unverified_saved_draft"
    assert recovered["draft"] == _review(case.pack)["draft"]
    assert recovered["requires_attention"] is True and recovered["canonical_extraction_modified"] is False
    assert observed and all(path in {case.root, folder, folder / "manifest.json", folder / "review.json"}
                            for path in observed)
    assert not calls and store.catalog()[0]["status"] != "verified_complete"


@pytest.mark.parametrize("fault", ["missing", "changed", "hardlink", "oversized"])
def test_v2_recovery_refuses_broken_authored_state(store_case, tmp_path, fault):
    case = store_case
    folder = _write_pack(case.root, case.pack)
    store = case.store()
    key = store.catalog()[0]["pack_id"]
    target = folder / "review.json"
    if fault == "missing":
        target.unlink()
    elif fault == "changed":
        target.write_bytes(b"{}")
    elif fault == "hardlink":
        os.link(target, tmp_path / "authored-link")
    else:
        with target.open("wb") as handle:
            handle.truncate(legacy.MAX_REVIEW_BYTES + 1)
    with pytest.raises(io.CropReviewPackIOError):
        store.recover_draft(key)


@pytest.mark.parametrize("fault", ["head_hash", "missing_revision", "extra_field"])
def test_recovery_does_not_repair_or_drop_malformed_journal_suffix(store_case, monkeypatch, fault):
    case = store_case
    pack = copy.deepcopy(case.pack)
    review = _review(pack)
    journal = review["draft"]["uncertainty"]["journal"]
    if fault == "head_hash":
        journal["head_sha256"] = "0" * 64
    elif fault == "missing_revision":
        assert journal["revisions"]
        journal["revisions"].pop()
    else:
        journal["private_unknown_field"] = "not accepted"
    _retag_review(pack, review)
    folder = _write_pack(case.root, pack)
    (folder / "baseline/artifacts/report.json").unlink()
    store = case.store()
    [entry] = store.catalog()
    calls = _ban_scoring(monkeypatch)
    with pytest.raises(io.CropReviewPackIOError):
        store.recover_draft(entry["pack_id"])
    assert not calls and (folder / "review.json").read_bytes() == pack["files"]["review.json"]


def test_dirty_raw_partial_draft_survives_reopen_recovery_without_cleaning_history(store_case, monkeypatch):
    case = store_case
    pack = copy.deepcopy(case.pack)
    review = _review(pack)
    review["reviewed"] = None
    review["draft"]["reference"] = "  partial edited draft\n"
    review["draft"]["critical_tokens_text"] = "unfinished\n\n"
    review["draft"]["uncertainty"]["dirty"] = True
    review["draft"]["uncertainty"]["annotations"] = policy._reset(review["draft"]["uncertainty"]["annotations"])
    pack["manifest"]["review_state"] = "draft"
    _retag_review(pack, review)
    calls = _ban_scoring(monkeypatch)
    policy.validate_crop_review_pack_v2(pack["manifest"], files=pack["files"], **_binding(pack))
    folder = _write_pack(case.root, pack)
    store = case.store()
    [entry] = store.catalog()
    assert store.read(entry["pack_id"])["review"]["draft"] == review["draft"]
    (folder / "baseline/artifacts/report.json").unlink()
    recovered = store.recover_draft(entry["pack_id"])
    assert recovered["draft"] == review["draft"]
    assert recovered["draft"]["uncertainty"]["dirty"] is True and not calls


@pytest.mark.parametrize("when", ["before", "after_first", "after_manifest", "after_readback"])
def test_v2_commit_guard_refuses_stale_generation_without_usable_success(store_case, monkeypatch, when):
    case = store_case
    store = case.store()
    valid = when != "before"
    committed, validations = [], []
    original_commit, original_validate = io._publish_new_report, policy.validate_crop_review_pack_v2

    def commit(temporary, target):
        nonlocal valid
        original_commit(temporary, target)
        committed.append(target)
        if when == "after_first" or when == "after_manifest" and target.name == "manifest.json" and target.parent.parent == case.root:
            valid = False

    def validate(*args, **kwargs):
        nonlocal valid
        result = original_validate(*args, **kwargs)
        validations.append(True)
        if when == "after_readback" and len(validations) == 2:
            valid = False
        return result

    monkeypatch.setattr(io, "_publish_new_report", commit)
    monkeypatch.setattr(policy, "validate_crop_review_pack_v2", validate)
    with pytest.raises(io.CropReviewPackIOError) as caught:
        store.publish(case.pack, verify_current=lambda: valid)
    assert caught.value.code == "input_changed"
    assert all(entry.status != "verified_complete" for entry in store._entries.values())
    if when == "before":
        assert not committed and not list(case.root.iterdir())
    elif when == "after_first":
        assert len(committed) == 1 and committed[0].exists()
    else:
        assert len(committed) == len(case.pack["files"]) + 1
        assert committed[-1].name == "manifest.json"


@pytest.mark.parametrize("failure", [KeyboardInterrupt("inert cancel"), SystemExit(17)])
def test_v2_cancellation_preserves_primary_and_partial_publication(store_case, monkeypatch, failure):
    case = store_case
    store = case.store()
    original = io._publish_new_report
    committed = []

    def commit(temporary, target):
        original(temporary, target)
        committed.append(target)
        raise failure

    monkeypatch.setattr(io, "_publish_new_report", commit)
    with pytest.raises(type(failure)) as caught:
        store.publish(case.pack)
    assert caught.value is failure and len(committed) == 1 and committed[0].exists()
    assert all(entry.status == "incomplete" and not (entry.path / "manifest.json").exists()
               for entry in store._entries.values())


def test_v2_aggregate_preflight_still_includes_manifest_before_payload_read(store_case, monkeypatch):
    case = store_case
    folder = _write_pack(case.root, case.pack)
    store = case.store()
    key = store.catalog()[0]["pack_id"]
    # Isolate existing I/O aggregate preflight after the real v2 manifest check.
    admitted = copy.deepcopy(case.pack["manifest"])
    monkeypatch.setattr(io, "_manifest", lambda _raw, _binding: admitted)
    monkeypatch.setattr(legacy, "MAX_PACK_BYTES", admitted["total_file_bytes"])
    reads, original = [], io._snapshot

    def snapshot(path, *args, **kwargs):
        reads.append(path.relative_to(folder).as_posix())
        return original(path, *args, **kwargs)

    monkeypatch.setattr(io, "_snapshot", snapshot)
    with pytest.raises(io.CropReviewPackIOError):
        store.read(key)
    assert reads == ["manifest.json"]


@pytest.mark.parametrize("style", ["newline", "pretty", "duplicate_version"])
def test_v2_manifest_identity_remains_exact_canonical_bytes(store_case, style):
    case = store_case
    folder = _write_pack(case.root, case.pack)
    raw = legacy.encode_manifest(case.pack["manifest"])
    if style == "newline":
        raw += b"\n"
    elif style == "pretty":
        raw = json.dumps(case.pack["manifest"], indent=2).encode()
    else:
        raw = raw[:-1] + b',"schema_version":2}'
    (folder / "manifest.json").write_bytes(raw)
    with pytest.raises(io.CropReviewPackIOError):
        io.read_crop_review_pack(folder, expected_manifest_sha256=_sha(raw),
            expected_root_identity=_identity(folder), **_binding(case.pack), verify_inputs=case.verify)
    [item] = case.store().catalog()
    assert item["status"] == "incomplete" and item["manifest_sha256"] is None


def test_manifest_key_preflight_cannot_rehash_unknown_object_before_rejection(store_case):
    case = store_case
    armed = False
    calls = []

    class Key:
        def __hash__(self):
            if armed:
                calls.append(True)
                raise AssertionError("unknown key rehashed")
            return hash("schema_version")

        def __eq__(self, _other):
            if armed:
                calls.append(True)
                raise AssertionError("unknown key compared")
            return False

    malformed = copy.deepcopy(case.pack)
    del malformed["manifest"]["schema_version"]
    malformed["manifest"][Key()] = 2
    armed = True
    with pytest.raises(io.CropReviewPackIOError):
        case.store().publish(malformed)
    assert not calls and not list(case.root.iterdir())


def test_parent_commit_check_is_exact_authored_bytes_only_without_replay(store_case, monkeypatch):
    case = store_case
    folder = _write_pack(case.root, case.pack)
    store = case.store()
    [entry] = store.catalog()
    store.read_for_revision(entry["pack_id"])
    # Only the preceding full read proves historical consistency. The commit
    # check deliberately does not use or inspect unrelated proof thereafter.
    (folder / "baseline/artifacts/report.json").unlink()
    seen, original = [], io._snapshot

    def snapshot(path, *args, **kwargs):
        seen.append(path.relative_to(folder).as_posix())
        return original(path, *args, **kwargs)

    def forbidden(*_args, **_kwargs):
        pytest.fail("lightweight parent check traversed/replayed evidence")

    monkeypatch.setattr(io, "_snapshot", snapshot)
    monkeypatch.setattr(policy, "validate_crop_review_pack_v2", forbidden)
    monkeypatch.setattr(policy, "recover_crop_pack_draft_v2", forbidden)
    monkeypatch.setattr(history, "replay_crop_journal", forbidden)
    monkeypatch.setattr(history, "replay_crop_journal_declaration", forbidden)
    scoring = _ban_scoring(monkeypatch)
    assert store.check_revision_parent(entry["pack_id"], expected_pack_sha256=entry["manifest_sha256"]) is None
    assert seen == ["manifest.json", "review.json", "manifest.json", "review.json"]
    assert not scoring


@pytest.mark.parametrize("fault", ["manifest", "review", "parent_directory", "storage_root", "wrong_pin",
                                  "unknown_id", "manifest_link", "review_link"])
def test_parent_commit_check_refuses_changed_identity_or_authored_bytes(store_case, tmp_path, fault):
    case = store_case
    folder = _write_pack(case.root, case.pack)
    store = case.store()
    [entry] = store.catalog()
    key, pin = entry["pack_id"], entry["manifest_sha256"]
    if fault in {"manifest", "review"}:
        path = folder / (fault + ".json")
        raw = path.read_bytes()
        path.write_bytes(b" " + raw[1:])
    elif fault == "parent_directory":
        folder.rename(folder.with_name("retained-original-parent"))
        _write_pack(case.root, case.pack).rename(folder)
    elif fault == "storage_root":
        case.root.rename(tmp_path / "retained-original-store")
        case.root.mkdir()
    elif fault == "wrong_pin":
        pin = "0" * 64
    elif fault == "unknown_id":
        key = "0" * 32
    else:
        name = "manifest.json" if fault == "manifest_link" else "review.json"
        os.link(folder / name, tmp_path / "linked-authored-evidence")
    with pytest.raises(io.CropReviewPackIOError):
        store.check_revision_parent(key, expected_pack_sha256=pin)


@pytest.mark.parametrize("name", ["manifest.json", "review.json"])
def test_parent_commit_check_rechecks_after_initial_capture(store_case, monkeypatch, name):
    case = store_case
    folder = _write_pack(case.root, case.pack)
    store = case.store()
    [entry] = store.catalog()
    original = io._snapshot
    target = folder / name
    changed = False

    def snapshot(path, *args, **kwargs):
        nonlocal changed
        result = original(path, *args, **kwargs)
        if path == folder / "review.json" and not changed:
            changed = True
            raw = target.read_bytes()
            target.write_bytes(b" " + raw[1:])
        return result

    monkeypatch.setattr(io, "_snapshot", snapshot)
    with pytest.raises(io.CropReviewPackIOError):
        store.check_revision_parent(entry["pack_id"], expected_pack_sha256=entry["manifest_sha256"])
    assert changed


@pytest.mark.parametrize("failure", [KeyboardInterrupt("inert parent cancel"), SystemExit(19)])
def test_parent_commit_check_preserves_cancellation_identity(store_case, monkeypatch, failure):
    case = store_case
    _write_pack(case.root, case.pack)
    store = case.store()
    [entry] = store.catalog()
    original = io._snapshot

    def snapshot(path, *args, **kwargs):
        if path.name == "review.json":
            raise failure
        return original(path, *args, **kwargs)

    monkeypatch.setattr(io, "_snapshot", snapshot)
    with pytest.raises(type(failure)) as caught:
        store.check_revision_parent(entry["pack_id"], expected_pack_sha256=entry["manifest_sha256"])
    assert caught.value is failure


@pytest.mark.parametrize("target_name", ["manifest.json", "review.json"])
def test_parent_change_between_child_commits_leaves_child_incomplete(store_case, monkeypatch, target_name):
    case = store_case
    store = case.store()
    parent = store.publish(case.pack)
    parent_folder = store._entries[parent["pack_id"]].path
    child = copy.deepcopy(case.pack)
    child["manifest"]["parent_pack_sha256"] = parent["manifest_sha256"]
    original, committed = io._publish_new_report, []

    def commit(temporary, destination):
        original(temporary, destination)
        committed.append(destination)
        target = parent_folder / target_name
        raw = target.read_bytes()
        target.write_bytes(b" " + raw[1:])

    def verify_current():
        store.check_revision_parent(parent["pack_id"], expected_pack_sha256=parent["manifest_sha256"])

    monkeypatch.setattr(io, "_publish_new_report", commit)
    with pytest.raises(io.CropReviewPackIOError) as caught:
        store.publish(child, verify_current=verify_current)
    assert caught.value.code == "input_changed"
    assert len(committed) == 1 and committed[0].exists()
    child_entry = store._entries[caught.value.pack_id]
    assert child_entry.status == "incomplete" and not (child_entry.path / "manifest.json").exists()
