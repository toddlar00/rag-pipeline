"""Generated private storage controls, no PDF/native/model/browser execution.

Most fault controls explicitly reuse the policy archive-boundary double. The
separately named genuine archive cases exercise all production historical
validators over inert, self-consistent declarations (not real OCR attestations).
"""

import copy
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace as NS
import uuid

import pytest

import ocr_crop_review_pack as policy
import ocr_crop_review_pack_io as io
from test_ocr_crop_review_pack import policy_pack  # noqa: F401
from test_ocr_disposition_archive import archive_fixture


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _identity(path):
    info = path.stat()
    return info.st_dev, info.st_ino


def _write_pack(root, pack):
    """Generated storage input only, intentionally not publication evidence."""
    folder = root / ("pack-" + uuid.uuid4().hex)
    folder.mkdir()
    for name, raw in pack["files"].items():
        target = folder / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    (folder / "manifest.json").write_bytes(policy.encode_manifest(pack["manifest"]))
    return folder


@pytest.fixture
def storage_case(tmp_path, policy_pack):  # noqa: F811
    root = tmp_path / "private-packs"
    root.mkdir()
    pack = policy.build_crop_review_pack(**policy_pack.options())
    checks = []

    def verify():
        checks.append(True)

    def store(**extra):
        return io.CropReviewPackStore(root, **policy_pack.binding, verify_inputs=verify, **extra)

    def read(folder, **extra):
        kwargs = dict(expected_manifest_sha256=_sha(policy.encode_manifest(pack["manifest"])),
                      expected_root_identity=_identity(folder), **policy_pack.binding, verify_inputs=verify)
        kwargs.update(extra)
        return io.read_crop_review_pack(folder, **kwargs)

    return NS(root=root, pack=pack, checks=checks, verify=verify, store=store, read=read,
              binding=policy_pack.binding)


def test_create_only_publication_manifest_last_strict_readback_and_detached_catalog(storage_case, monkeypatch):
    case = storage_case
    store = case.store()
    commits = []
    original = io._publish_new_report

    def commit(temporary, destination):
        commits.append((destination.name, len(case.checks)))
        original(temporary, destination)

    monkeypatch.setattr(io, "_publish_new_report", commit)
    before = copy.deepcopy(case.pack)
    result = store.publish(case.pack)
    assert result["status"] == "verified_complete" and result["requires_attention"] is True
    assert commits[-1][0] == "manifest.json"
    assert len(commits) == len(case.pack["files"]) + 1
    assert all(a[1] < b[1] for a, b in zip(commits, commits[1:]))
    folder = store._entries[result["pack_id"]].path
    for name, raw in case.pack["files"].items():
        assert (folder / name).read_bytes() == raw
        assert (folder / name).stat().st_nlink == 1
    assert (folder / "manifest.json").read_bytes() == policy.encode_manifest(case.pack["manifest"])
    assert case.pack == before
    catalog = store.catalog()
    assert set(catalog[0]) == {"pack_id", "manifest_sha256", "status", "requires_attention"}
    catalog[0]["status"] = "forged"
    assert store.catalog()[0]["status"] == "verified_complete"
    evidence = store.read(result["pack_id"])
    evidence["review"]["draft"]["reference"] = "changed"
    assert store.read(result["pack_id"])["review"]["draft"]["reference"] == "not liable"


def test_restart_discovers_unverified_new_capability_without_auto_selection(storage_case):
    case = storage_case
    first = case.store()
    saved = first.publish(case.pack)
    restarted = case.store()
    [discovered] = restarted.catalog()
    assert discovered["pack_id"] != saved["pack_id"]
    assert discovered["manifest_sha256"] == saved["manifest_sha256"]
    assert discovered["status"] == "present_unverified"
    with pytest.raises(io.CropReviewPackIOError, match="retained data") as error:
        restarted.read(saved["pack_id"])
    assert error.value.code == "unknown_pack"
    evidence = restarted.read(discovered["pack_id"])
    assert evidence["requires_attention"] is True
    assert restarted.catalog()[0]["status"] == "verified_complete"


@pytest.mark.parametrize("operation", ["regions", "hardscan"])
@pytest.mark.parametrize("state", ["candidate", "empty", "unavailable"])
def test_genuine_historical_archive_publish_reopen_without_current_runtime(tmp_path, monkeypatch, operation, state):
    import model_artifacts
    import ocr_execution_receipt
    import ocr_experiment_runtime

    before = archive_fixture(operation, text="original")
    after = archive_fixture(operation, dpi=400, text="retry", state=state, installation=True)
    binding = dict(source_sha256=before["source_sha256"], page_count=before["page_count"],
                   recovery_sha256=_sha(before["retained_inputs"]["recovery.json"]))
    pack = policy.build_crop_review_pack(
        baseline={k: before[k] for k in ("artifacts", "retained_inputs")},
        retry={k: after[k] for k in ("artifacts", "retained_inputs")}, **binding,
        baseline_region_id="r00", retry_region_id="r00", reference="own draft", critical_tokens_text="")

    def forbidden(*_args, **_kwargs):
        raise AssertionError("pack replay must not qualify or run the current OCR runtime")

    monkeypatch.setattr(model_artifacts, "load_model_artifact_registry", forbidden)
    monkeypatch.setattr(ocr_execution_receipt, "capture_execution_receipt", forbidden)
    monkeypatch.setattr(ocr_experiment_runtime, "capture_runtime_manifest", forbidden)
    root = tmp_path / "private"
    root.mkdir()
    store = io.CropReviewPackStore(root, **binding, verify_inputs=lambda: None)
    saved = store.publish(pack)
    evidence = store.read(saved["pack_id"])
    assert evidence["validation_scope"] == "historical_local_declarations"
    assert evidence["retry"]["execution"]["summary"]["attempted"] == (0 if state == "unavailable" else 1)
    assert evidence["retry"]["report"]["retry_configuration"]["dpi"] == 400
    restarted = io.CropReviewPackStore(root, **binding, verify_inputs=lambda: None)
    [metadata] = restarted.catalog()
    assert restarted.read(metadata["pack_id"])["pack_sha256"] == saved["manifest_sha256"]
    recovered = restarted.recover_draft(metadata["pack_id"])
    assert recovered["draft"] == {"reference": "own draft", "critical_tokens_text": ""}
    assert set(recovered) == {"draft", "scope", "pack_sha256", "status", "requires_attention",
                              "canonical_extraction_modified"}


def test_operator_registration_requires_exact_manifest_and_directory_pin(storage_case, tmp_path):
    case = storage_case
    external = tmp_path / "operator-input"
    external.mkdir()
    folder = _write_pack(external, case.pack)
    store = case.store()
    metadata = store.register(folder, expected_manifest_sha256=_sha(policy.encode_manifest(case.pack["manifest"])),
                              expected_root_identity=_identity(folder))
    assert store.read(metadata["pack_id"])["manifest"] == case.pack["manifest"]
    assert "path" not in metadata
    for kwargs in (dict(expected_manifest_sha256="0" * 64, expected_root_identity=_identity(folder)),
                   dict(expected_manifest_sha256=metadata["manifest_sha256"], expected_root_identity=(0, 0))):
        with pytest.raises(io.CropReviewPackIOError):
            store.register(folder, **kwargs)
    assert len(store.catalog()) == 1


@pytest.mark.parametrize("value", [None, True, [], {}, "../pack", "C:/secret", "a" * 64, "0" * 32])
def test_browser_like_ids_never_resolve_as_paths(storage_case, value):
    store = storage_case.store()
    with pytest.raises(io.CropReviewPackIOError) as error:
        store.read(value)
    assert error.value.code == "unknown_pack" and error.value.pack_id is None


@pytest.mark.parametrize("max_packs", [0, 129, True, 1.0, "2"])
def test_catalog_limit_strict(storage_case, max_packs):
    with pytest.raises(io.CropReviewPackIOError):
        storage_case.store(max_packs=max_packs)


def test_discovery_bounded_before_manifest_reads(storage_case, monkeypatch):
    for _ in range(3):
        (storage_case.root / ("pack-" + uuid.uuid4().hex)).mkdir()
    reads = []
    monkeypatch.setattr(io, "_snapshot", lambda *a, **k: reads.append(a) or (_ for _ in ()).throw(AssertionError()))
    with pytest.raises(io.CropReviewPackIOError) as error:
        storage_case.store(max_packs=2)
    assert error.value.code == "catalog_full" and not reads


def test_unknown_root_entries_refused_not_removed(storage_case):
    unknown = storage_case.root / "source.pdf"
    unknown.write_bytes(b"not a PDF; private test marker")
    with pytest.raises(io.CropReviewPackIOError):
        storage_case.store()
    assert unknown.read_bytes() == b"not a PDF; private test marker"


def test_missing_manifest_discovered_incomplete_never_full_evidence(storage_case):
    folder = storage_case.root / ("pack-" + uuid.uuid4().hex)
    folder.mkdir()
    (folder / "review.json").write_bytes(storage_case.pack["files"]["review.json"])
    store = storage_case.store()
    [item] = store.catalog()
    assert item["status"] == "incomplete" and item["manifest_sha256"] is None
    with pytest.raises(io.CropReviewPackIOError):
        store.read(item["pack_id"])
    assert (folder / "review.json").exists()


@pytest.mark.parametrize("name", ["extra.json", "source.pdf", "baseline/inputs/model.onnx",
                                "retry/artifacts/work/other.json"])
def test_extra_files_refused_before_payload_capture(storage_case, monkeypatch, name):
    case = storage_case
    folder = _write_pack(case.root, case.pack)
    (folder / name).write_bytes(b"unexpected")
    original = io._snapshot
    reads = []

    def snapshot(path, *args, **kwargs):
        reads.append(path.relative_to(folder).as_posix())
        return original(path, *args, **kwargs)

    monkeypatch.setattr(io, "_snapshot", snapshot)
    with pytest.raises(io.CropReviewPackIOError):
        case.read(folder)
    assert reads == ["manifest.json"]


@pytest.mark.parametrize("name", ["review.json", "baseline/artifacts/report.json", "retry/inputs/plan.json"])
def test_hardlinks_refused_before_payload_capture(storage_case, tmp_path, name):
    case = storage_case
    folder = _write_pack(case.root, case.pack)
    target = folder / name
    os.link(target, tmp_path / "linked")
    with pytest.raises(io.CropReviewPackIOError):
        case.read(folder)
    assert target.stat().st_nlink == 2


def test_recovery_does_not_touch_link_like_unrelated_proof(storage_case, monkeypatch):
    case = storage_case
    folder = _write_pack(case.root, case.pack)
    (folder / "baseline/artifacts/report.json").unlink()
    store = case.store()
    key = store.catalog()[0]["pack_id"]
    original = io.storage_policy.assert_no_link_components

    def refuse(path, *args, **kwargs):
        if Path(path) == folder / "baseline/artifacts/report.json":
            raise ValueError("generated broken-link refusal")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(io.storage_policy, "assert_no_link_components", refuse)
    assert store.recover_draft(key)["draft"]["reference"] == "not liable"


def test_full_payload_size_preflight_occurs_before_any_payload_read(storage_case, monkeypatch):
    case = storage_case
    folder = _write_pack(case.root, case.pack)
    target = folder / "retry/artifacts/report.json"
    with target.open("ab") as handle:
        handle.write(b"too large for its exact descriptor")
    reads, original = [], io._snapshot

    def snapshot(path, *args, **kwargs):
        reads.append(path.name)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(io, "_snapshot", snapshot)
    with pytest.raises(io.CropReviewPackIOError):
        case.read(folder)
    assert reads == ["manifest.json"]


def test_aggregate_bound_includes_manifest_before_payload_read(storage_case, monkeypatch):
    case = storage_case
    folder = _write_pack(case.root, case.pack)
    # Isolate storage's aggregate check after real closed manifest validation.
    admitted = copy.deepcopy(case.pack["manifest"])
    monkeypatch.setattr(io, "_manifest", lambda _raw, _binding: admitted)
    monkeypatch.setattr(policy, "MAX_PACK_BYTES", admitted["total_file_bytes"])
    reads, original = [], io._snapshot

    def snapshot(path, *args, **kwargs):
        reads.append(path.name)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(io, "_snapshot", snapshot)
    with pytest.raises(io.CropReviewPackIOError):
        case.read(folder)
    assert reads == ["manifest.json"]


def test_sparse_oversized_manifest_refused_without_loading(storage_case, monkeypatch):
    case = storage_case
    folder = _write_pack(case.root, case.pack)
    with (folder / "manifest.json").open("wb") as handle:
        handle.truncate(policy.MAX_MANIFEST_BYTES + 1)
    reads = []
    monkeypatch.setattr(io, "_read_snapshot", lambda *a, **k: reads.append(a))
    with pytest.raises(io.CropReviewPackIOError):
        case.read(folder)
    assert not reads


def test_same_json_noncanonical_manifest_is_not_same_pack_identity(storage_case):
    case = storage_case
    folder = _write_pack(case.root, case.pack)
    raw = json.dumps(case.pack["manifest"], indent=2).encode()
    (folder / "manifest.json").write_bytes(raw)
    with pytest.raises(io.CropReviewPackIOError):
        case.read(folder, expected_manifest_sha256=_sha(raw))


def test_recovery_only_missing_archive_keeps_only_authored_draft(storage_case, monkeypatch):
    case = storage_case
    folder = _write_pack(case.root, case.pack)
    (folder / "baseline/artifacts/report.json").unlink()
    store = case.store()
    key = store.catalog()[0]["pack_id"]
    with pytest.raises(io.CropReviewPackIOError):
        store.read(key)
    monkeypatch.setattr(policy, "validate_crop_review_pack", lambda *a, **k: (_ for _ in ()).throw(AssertionError()))
    recovered = store.recover_draft(key)
    assert recovered["status"] == "unverified_saved_draft"
    assert "baseline" not in recovered and "comparison" not in recovered
    assert store.catalog()[0]["status"] != "verified_complete"


def test_wrong_source_and_recovery_fail_before_full_validation(storage_case):
    case = storage_case
    folder = _write_pack(case.root, case.pack)
    for field, value in (("source_sha256", "0" * 64), ("recovery_sha256", "0" * 64), ("page_count", 3)):
        with pytest.raises(io.CropReviewPackIOError):
            case.read(folder, **{field: value})


def test_file_change_during_pure_validation_invalidates_readback(storage_case, monkeypatch):
    case = storage_case
    folder = _write_pack(case.root, case.pack)
    original = policy.validate_crop_review_pack

    def validate(*args, **kwargs):
        result = original(*args, **kwargs)
        target = folder / "review.json"
        target.write_bytes(target.read_bytes().replace(b"not liable", b"NOT LIABLE"))
        return result

    monkeypatch.setattr(policy, "validate_crop_review_pack", validate)
    with pytest.raises(io.CropReviewPackIOError) as error:
        case.read(folder)
    assert error.value.code == "input_changed"


def test_appended_file_during_validation_is_not_ignored(storage_case, monkeypatch):
    case = storage_case
    folder = _write_pack(case.root, case.pack)
    original = policy.validate_crop_review_pack

    def validate(*args, **kwargs):
        result = original(*args, **kwargs)
        (folder / "unexpected").write_bytes(b"keep")
        return result

    monkeypatch.setattr(policy, "validate_crop_review_pack", validate)
    with pytest.raises(io.CropReviewPackIOError):
        case.read(folder)
    assert (folder / "unexpected").read_bytes() == b"keep"


def test_directory_replacement_even_with_equal_bytes_rejects_expected_inode(storage_case):
    case = storage_case
    folder = _write_pack(case.root, case.pack)
    old_identity = _identity(folder)
    moved = case.root / ("pack-" + uuid.uuid4().hex)
    folder.rename(moved)
    replacement = _write_pack(case.root, case.pack)
    replacement.rename(folder)
    with pytest.raises(io.CropReviewPackIOError) as error:
        case.read(folder, expected_root_identity=old_identity)
    assert error.value.code == "input_changed"


def test_no_clobber_existing_fixed_destination(storage_case, monkeypatch):
    case = storage_case
    store = case.store()
    token = uuid.uuid4()
    folder = case.root / ("pack-" + token.hex)
    folder.mkdir()
    marker = folder / "operator-marker"
    marker.write_bytes(b"preserve")
    monkeypatch.setattr(io.uuid, "uuid4", lambda: token)
    with pytest.raises(io.CropReviewPackIOError) as error:
        store.publish(case.pack)
    assert error.value.code == "pack_exists" and marker.read_bytes() == b"preserve"
    assert not store.catalog()


def test_noncooperating_file_creation_cannot_be_overwritten(storage_case, monkeypatch):
    case = storage_case
    store = case.store()
    original = io._publish_new_report
    targets = []

    def conflict(temporary, destination):
        destination.write_bytes(b"other-writer")
        targets.append(destination)
        original(temporary, destination)

    monkeypatch.setattr(io, "_publish_new_report", conflict)
    with pytest.raises(io.CropReviewPackIOError) as error:
        store.publish(case.pack)
    assert error.value.code == "pack_exists" and error.value.status == "incomplete"
    assert targets[0].read_bytes() == b"other-writer"
    assert not (store._entries[error.value.pack_id].path / "manifest.json").exists()


def test_partial_io_failure_preserves_earlier_original_bytes(storage_case, monkeypatch):
    case = storage_case
    store = case.store()
    original, committed = io._publish_new_report, []

    def fail_second(temporary, destination):
        if committed:
            raise OSError("PRIVATE injected pathname/token")
        original(temporary, destination)
        committed.append(destination)

    monkeypatch.setattr(io, "_publish_new_report", fail_second)
    with pytest.raises(io.CropReviewPackIOError) as error:
        store.publish(case.pack)
    assert "PRIVATE" not in str(error.value)
    assert error.value.status == "incomplete"
    assert committed[0].exists()
    assert not (store._entries[error.value.pack_id].path / "manifest.json").exists()


def test_host_callback_failure_before_commit_retains_no_manifest(storage_case, monkeypatch):
    case = storage_case
    store = case.store()
    original, committed = io._publish_new_report, []

    def after_first(temporary, destination):
        original(temporary, destination)
        committed.append(destination)
        store._verify_inputs = lambda: False

    monkeypatch.setattr(io, "_publish_new_report", after_first)
    with pytest.raises(io.CropReviewPackIOError) as error:
        store.publish(case.pack)
    assert error.value.code == "input_changed"
    assert len(committed) == 1 and committed[0].exists()


def test_committed_file_mutation_blocks_later_commit(storage_case, monkeypatch):
    case = storage_case
    store = case.store()
    original, committed = io._publish_new_report, []

    def mutate(temporary, destination):
        original(temporary, destination)
        committed.append(destination)
        if len(committed) == 2:
            committed[0].write_bytes(b"changed")

    monkeypatch.setattr(io, "_publish_new_report", mutate)
    with pytest.raises(io.CropReviewPackIOError):
        store.publish(case.pack)
    assert len(committed) == 2
    assert all(not (entry.path / "manifest.json").exists() for entry in store._entries.values())


def test_original_pack_dictionary_mutation_cannot_change_captured_publication(storage_case, monkeypatch):
    case = storage_case
    store = case.store()
    original = io._publish_new_report
    captured = copy.deepcopy(case.pack)

    def mutate(temporary, destination):
        case.pack["files"].clear()
        case.pack["manifest"].clear()
        original(temporary, destination)

    monkeypatch.setattr(io, "_publish_new_report", mutate)
    saved = store.publish(case.pack)
    assert store.read(saved["pack_id"])["manifest"] == captured["manifest"]


def test_post_manifest_validation_failure_returns_no_usable_success(storage_case, monkeypatch):
    case = storage_case
    store = case.store()
    original = policy.validate_crop_review_pack
    calls = []

    def second_fails(*args, **kwargs):
        calls.append(True)
        if len(calls) == 2:
            raise ValueError("PRIVATE final verification fault")
        return original(*args, **kwargs)

    monkeypatch.setattr(policy, "validate_crop_review_pack", second_fails)
    with pytest.raises(io.CropReviewPackIOError) as error:
        store.publish(case.pack)
    assert error.value.status == "present_unverified"
    entry = store._entries[error.value.pack_id]
    assert (entry.path / "manifest.json").exists()
    assert entry.status != "verified_complete"


def test_cleanup_failure_latches_and_preserves_committed_bytes(storage_case, monkeypatch):
    case = storage_case
    store = case.store()
    original, committed = io._publish_new_report, []

    def dirty_commit(temporary, destination):
        original(temporary, destination)
        committed.append(destination)
        raise io.ReportCleanupError("PRIVATE cleanup detail")

    monkeypatch.setattr(io, "_publish_new_report", dirty_commit)
    with pytest.raises(io.CropReviewPackIOError) as error:
        store.publish(case.pack)
    assert error.value.code == error.value.status == "cleanup_uncertain"
    assert store.cleanup_uncertain and committed[0].exists()
    with pytest.raises(io.CropReviewPackIOError) as second:
        store.publish(case.pack)
    assert second.value.code == "cleanup_uncertain" and len(committed) == 1


@pytest.mark.parametrize("cleanup", [False, True])
def test_cancellation_primary_identity_preserved_with_optional_cleanup_fault(storage_case, monkeypatch, cleanup):
    case = storage_case
    store = case.store()
    cancellation = KeyboardInterrupt("cancel marker")
    original = io._publish_new_report

    def cancel(temporary, destination):
        original(temporary, destination)
        if cleanup:
            store._cleanup_error("private", RuntimeError("private"))
        raise cancellation

    monkeypatch.setattr(io, "_publish_new_report", cancel)
    with pytest.raises(KeyboardInterrupt) as error:
        store.publish(case.pack)
    assert error.value is cancellation and store.cleanup_uncertain is cleanup
    [entry] = store._entries.values()
    assert not (entry.path / "manifest.json").exists()
    assert any(entry.path.rglob("*.json"))


def test_static_input_callback_error_and_cancellation(storage_case):
    case = storage_case
    folder = _write_pack(case.root, case.pack)
    def broken():
        raise RuntimeError("PRIVATE source path")
    with pytest.raises(io.CropReviewPackIOError) as error:
        case.read(folder, verify_inputs=broken)
    assert error.value.code == "input_changed" and "PRIVATE" not in str(error.value)
    cancellation = KeyboardInterrupt("stop")
    def cancel():
        raise cancellation
    with pytest.raises(KeyboardInterrupt) as error:
        case.read(folder, verify_inputs=cancel)
    assert error.value is cancellation


@pytest.mark.parametrize("fault", ["missing", "wrong_size", "corrupt", "extra", "link_like"])
def test_draft_only_recovery_ignores_all_unrelated_proof_damage(storage_case, monkeypatch, fault):
    case = storage_case
    folder = _write_pack(case.root, case.pack)
    target = folder / "baseline/artifacts/report.json"
    if fault == "missing":
        target.unlink()
    elif fault == "wrong_size":
        target.write_bytes(b"x")
    elif fault == "corrupt":
        target.write_bytes(b"x" * target.stat().st_size)
    elif fault == "extra":
        (folder / "unrelated-extra").write_bytes(b"private unknown")
    store = case.store()
    key = store.catalog()[0]["pack_id"]
    touched = []
    original = io.storage_policy.assert_no_link_components

    def observe(path, *args, **kwargs):
        touched.append(Path(path))
        if fault == "link_like" and Path(path) == target:
            raise ValueError("generated unrelated linked evidence")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(io.storage_policy, "assert_no_link_components", observe)
    monkeypatch.setattr(policy, "validate_crop_review_pack", lambda *a, **k: (_ for _ in ()).throw(AssertionError()))
    recovered = store.recover_draft(key)
    assert recovered["status"] == "unverified_saved_draft"
    assert recovered["draft"] == {"reference": "not liable", "critical_tokens_text": "not liable"}
    assert target not in touched
    assert all(path in (case.root, folder, folder / "manifest.json", folder / "review.json") for path in touched)


@pytest.mark.parametrize("fault", ["missing", "changed", "hardlink"])
def test_draft_only_recovery_still_refuses_damaged_authored_file(storage_case, tmp_path, fault):
    case = storage_case
    folder = _write_pack(case.root, case.pack)
    store = case.store()
    key = store.catalog()[0]["pack_id"]
    target = folder / "review.json"
    if fault == "missing":
        target.unlink()
    elif fault == "changed":
        target.write_bytes(target.read_bytes().replace(b"not liable", b"NOT LIABLE"))
    else:
        os.link(target, tmp_path / "authored-link")
    with pytest.raises(io.CropReviewPackIOError):
        store.recover_draft(key)


def test_publish_per_save_guard_runs_before_every_commit_and_after_verification(storage_case, monkeypatch):
    case = storage_case
    store = case.store()
    checks, commits = [], []
    original = io._publish_new_report
    def verify_current():
        checks.append(True)
    def commit(temporary, destination):
        commits.append(len(checks))
        original(temporary, destination)
    monkeypatch.setattr(io, "_publish_new_report", commit)
    result = store.publish(case.pack, verify_current=verify_current)
    assert result["status"] == "verified_complete"
    assert commits[0] > 0 and all(a < b for a, b in zip(commits, commits[1:]))
    assert len(checks) > commits[-1]
    assert not (case.root / ".rag-locks").exists()
    assert (case.root.parent / ".rag-locks").is_dir()


@pytest.mark.parametrize("when", ["before", "after_first", "after_manifest", "after_readback"])
def test_changed_per_save_view_cannot_report_success(storage_case, monkeypatch, when):
    case = storage_case
    store = case.store()
    valid = when != "before"
    committed = []
    original_commit = io._publish_new_report
    original_validate = policy.validate_crop_review_pack
    validations = []
    def verify_current():
        return valid
    def commit(temporary, destination):
        nonlocal valid
        original_commit(temporary, destination)
        committed.append(destination)
        if when == "after_first" or (when == "after_manifest" and destination.parent.name.startswith("pack-")
                                      and destination.name == "manifest.json"):
            valid = False
    def validate(*args, **kwargs):
        nonlocal valid
        result = original_validate(*args, **kwargs)
        validations.append(True)
        if when == "after_readback" and len(validations) == 2:
            valid = False
        return result
    monkeypatch.setattr(io, "_publish_new_report", commit)
    monkeypatch.setattr(policy, "validate_crop_review_pack", validate)
    with pytest.raises(io.CropReviewPackIOError) as error:
        store.publish(case.pack, verify_current=verify_current)
    assert error.value.code == "input_changed"
    assert all(entry.status != "verified_complete" for entry in store._entries.values())
    if when == "before":
        assert not committed and not list(case.root.iterdir())
    elif when == "after_first":
        assert len(committed) == 1
    else:
        assert len(committed) == len(case.pack["files"]) + 1


@pytest.mark.parametrize("value", [False, "callback_name", {}, 1])
def test_per_save_callback_cannot_be_a_serialized_or_browser_value(storage_case, value):
    store = storage_case.store()
    with pytest.raises(io.CropReviewPackIOError) as error:
        store.publish(storage_case.pack, verify_current=value)
    assert error.value.code == "invalid_pack" and not list(storage_case.root.iterdir())


def test_per_save_cancellation_propagates_before_any_creation(storage_case):
    store = storage_case.store()
    cancellation = KeyboardInterrupt("cancel")
    def verify_current():
        raise cancellation
    with pytest.raises(KeyboardInterrupt) as error:
        store.publish(storage_case.pack, verify_current=verify_current)
    assert error.value is cancellation and not list(storage_case.root.iterdir())


def test_distinct_store_instances_share_disk_catalog_capacity(storage_case):
    case = storage_case
    first, second = case.store(max_packs=1), case.store(max_packs=1)
    first.publish(case.pack)
    with pytest.raises(io.CropReviewPackIOError) as error:
        second.publish(case.pack)
    assert error.value.code == "catalog_full"
    assert len(list(case.root.iterdir())) == 1


def test_snapshot_caps_each_read_at_actual_preflight_size(storage_case, monkeypatch):
    case = storage_case
    folder = _write_pack(case.root, case.pack)
    original = io._read_snapshot
    observations = []
    def read(path, *, label, max_bytes):
        observations.append((max_bytes, Path(path).stat().st_size))
        return original(path, label=label, max_bytes=max_bytes)
    monkeypatch.setattr(io, "_read_snapshot", read)
    case.read(folder)
    assert observations and all(limit == actual for limit, actual in observations)


def test_fixed_storage_root_replacement_is_static_and_prevents_publication(storage_case):
    case = storage_case
    store = case.store()
    case.root.rename(case.root.with_name("saved-original"))
    case.root.mkdir()
    with pytest.raises(io.CropReviewPackIOError) as error:
        store.publish(case.pack)
    assert error.value.code == "input_changed" and not list(case.root.iterdir())
    case.root.rmdir()
    with pytest.raises(io.CropReviewPackIOError) as error:
        store.catalog()
    assert str(case.root) not in str(error.value)


def test_mkdir_uncertain_failure_latches_without_removing_possible_owned_data(storage_case, monkeypatch):
    case = storage_case
    store = case.store()
    original = Path.mkdir
    created = []
    def uncertain(path, *args, **kwargs):
        result = original(path, *args, **kwargs)
        if path.parent == case.root and path.name.startswith("pack-"):
            created.append(path)
            raise OSError("PRIVATE post-mkdir fault")
        return result
    monkeypatch.setattr(Path, "mkdir", uncertain)
    with pytest.raises(io.CropReviewPackIOError) as error:
        store.publish(case.pack)
    assert error.value.code == error.value.status == "cleanup_uncertain"
    assert store.cleanup_uncertain and len(created) == 1 and created[0].is_dir()


@pytest.mark.parametrize("code,status", [([], {}), (None, None), ("PRIVATE", "PRIVATE")])
def test_public_error_class_never_echoes_unrecognized_details(code, status):
    error = io.CropReviewPackIOError(code, status=status, pack_id="../PRIVATE")
    assert error.code == "storage_unavailable" and error.status == "incomplete"
    assert error.pack_id is None and "PRIVATE" not in str(error)


@pytest.mark.parametrize("location", ["pack", "files"])
@pytest.mark.parametrize("kind", ["object", "str_subclass"])
@pytest.mark.parametrize("oversized", [False, True])
def test_mapping_preflight_rejects_before_rehashing_untrusted_keys(storage_case, location, kind, oversized):
    case = storage_case
    store = case.store()
    calls = []
    armed = False
    class ObjectKey:
        def __hash__(self):
            if armed:
                calls.append(True)
                raise AssertionError("untrusted key was rehashed")
            return 147291
    class StringKey(str):
        def __hash__(self):
            if armed:
                calls.append(True)
                raise AssertionError("str subclass was rehashed")
            return super().__hash__()
    key_text = "extra" if oversized else "manifest" if location == "pack" else "review.json"
    key = ObjectKey() if kind == "object" else StringKey(key_text)
    target = case.pack if location == "pack" else case.pack["files"]
    if not oversized:
        del target["manifest" if location == "pack" else "review.json"]
    target[key] = b"generated marker"
    armed = True
    with pytest.raises(io.CropReviewPackIOError) as error:
        store.publish(case.pack)
    assert error.value.code == "invalid_pack"
    assert not calls and not list(case.root.iterdir())


def test_revision_read_reuses_exact_verified_byte_cohort_and_preserves_default_schemas(storage_case, monkeypatch):
    case = storage_case
    folder = _write_pack(case.root, case.pack)
    store = case.store()
    key = store.catalog()[0]["pack_id"]
    captures, first_raw = [], {}
    original = io._snapshot

    def capture(path, *args, **kwargs):
        result = original(path, *args, **kwargs)
        name = path.relative_to(folder).as_posix()
        captures.append(name)
        first_raw.setdefault(name, result[0])
        return result

    monkeypatch.setattr(io, "_snapshot", capture)
    normal = store.read(key)
    normal_reads = list(captures)
    captures.clear()
    first_raw.clear()
    revision = store.read_for_revision(key)
    assert set(revision) == {"evidence", "pack", "manifest_bytes"}
    assert set(revision["pack"]) == {"manifest", "files"}
    assert revision["evidence"] == normal
    assert revision["pack"] == case.pack
    assert revision["manifest_bytes"] == policy.encode_manifest(case.pack["manifest"])
    assert revision["manifest_bytes"] is first_raw["manifest.json"]
    assert all(raw is first_raw[name] for name, raw in revision["pack"]["files"].items())
    assert captures == normal_reads  # No additional cohort, decoder or I/O pass.
    assert _sha(revision["manifest_bytes"]) == revision["evidence"]["pack_sha256"]
    assert "pack" not in store.read(key) and "files" not in store.recover_draft(key)
    revision["pack"]["manifest"]["pair"]["baseline"]["region_id"] = "modified"
    revision["pack"]["files"].clear()
    revision["evidence"]["review"]["draft"]["reference"] = "modified"
    reread = store.read_for_revision(key)
    assert reread["pack"] == case.pack
    assert reread["evidence"]["review"]["draft"]["reference"] == "not liable"


@pytest.mark.parametrize("operation", ["regions", "hardscan"])
def test_genuine_revision_keeps_historical_bytes_and_publishes_new_draft_only(tmp_path, operation):
    baseline = archive_fixture(operation, text="baseline historical text")
    retry = archive_fixture(operation, dpi=400, text="retry historical text", installation=True)
    binding = {"source_sha256": baseline["source_sha256"], "page_count": baseline["page_count"],
               "recovery_sha256": _sha(baseline["retained_inputs"]["recovery.json"])}
    original = policy.build_crop_review_pack(**binding,
        baseline={k: baseline[k] for k in ("artifacts", "retained_inputs")},
        retry={k: retry[k] for k in ("artifacts", "retained_inputs")},
        baseline_region_id="r00", retry_region_id="r00", reference="old draft", critical_tokens_text="")
    root = tmp_path / "private-revisions"
    root.mkdir()
    _write_pack(root, original)
    store = io.CropReviewPackStore(root, **binding, verify_inputs=lambda: None)
    parent = store.catalog()[0]
    captured = store.read_for_revision(parent["pack_id"])
    sides = {}
    for side in ("baseline", "retry"):
        files = captured["pack"]["files"]
        sides[side] = {
            "artifacts": {name: files[f"{side}/artifacts/{name}"] for name in policy.ARTIFACT_LIMITS},
            "retained_inputs": {name: files[f"{side}/inputs/{name}"] for name in policy.INPUT_LIMITS
                                if f"{side}/inputs/{name}" in files}}
    revision = policy.build_crop_review_pack(**binding, **sides,
        baseline_region_id="r00", retry_region_id="r00", reference="new exact draft \n",
        critical_tokens_text="partial\n", parent_pack_sha256=captured["evidence"]["pack_sha256"])
    assert revision["manifest"]["review_state"] == "draft"
    assert revision["manifest"]["parent_pack_sha256"] == parent["manifest_sha256"]
    assert all(raw == original["files"][name] for name, raw in revision["files"].items() if name != "review.json")
    saved = store.publish(revision)
    assert saved["manifest_sha256"] != parent["manifest_sha256"]
    assert store.read_for_revision(parent["pack_id"])["pack"] == original
    assert store.recover_draft(saved["pack_id"])["draft"] == {
        "reference": "new exact draft \n", "critical_tokens_text": "partial\n"}
    fresh = io.CropReviewPackStore(root, **binding, verify_inputs=lambda: None)
    assert len(fresh.catalog()) == 2
    assert all(item["status"] == "present_unverified" for item in fresh.catalog())


@pytest.mark.parametrize("fault", ["payload", "manifest", "extra", "directory", "source"])
def test_revision_read_rejects_mutation_during_complete_validation(storage_case, monkeypatch, fault):
    case = storage_case
    folder = _write_pack(case.root, case.pack)
    store = case.store()
    key = store.catalog()[0]["pack_id"]
    original = policy.validate_crop_review_pack

    def validate(*args, **kwargs):
        evidence = original(*args, **kwargs)
        if fault == "payload":
            target = folder / "review.json"
            target.write_bytes(target.read_bytes().replace(b"not liable", b"NOT LIABLE"))
        elif fault == "manifest":
            target = folder / "manifest.json"
            target.write_bytes(target.read_bytes() + b"\n")
        elif fault == "extra":
            (folder / "unexpected.json").write_bytes(b"preserved unknown")
        elif fault == "directory":
            moved = case.root / ("pack-" + uuid.uuid4().hex)
            folder.rename(moved)
            replacement = _write_pack(case.root, case.pack)
            replacement.rename(folder)
        else:
            store._verify_inputs = lambda: False
        return evidence

    monkeypatch.setattr(policy, "validate_crop_review_pack", validate)
    with pytest.raises(io.CropReviewPackIOError):
        store.read_for_revision(key)
    assert store._entries[key].status == "present_unverified"


@pytest.mark.parametrize("fault", ["missing", "wrong_size", "hardlink"])
def test_revision_read_is_not_draft_recovery_and_requires_all_proof(storage_case, tmp_path, fault):
    case = storage_case
    folder = _write_pack(case.root, case.pack)
    store = case.store()
    key = store.catalog()[0]["pack_id"]
    target = folder / "retry/artifacts/report.json"
    if fault == "missing":
        target.unlink()
    elif fault == "wrong_size":
        target.write_bytes(b"corrupt")
    else:
        os.link(target, tmp_path / "linked-proof")
    assert store.recover_draft(key)["status"] == "unverified_saved_draft"
    with pytest.raises(io.CropReviewPackIOError):
        store.read_for_revision(key)


@pytest.mark.parametrize("key", [None, "../path", "C:/private", "0" * 32])
def test_revision_read_requires_catalog_owned_opaque_id(storage_case, key):
    store = storage_case.store()
    with pytest.raises(io.CropReviewPackIOError) as error:
        store.read_for_revision(key)
    assert error.value.code == "unknown_pack"


def test_revision_read_old_session_id_is_not_reopened_by_filename(storage_case):
    case = storage_case
    folder = _write_pack(case.root, case.pack)
    first = case.store()
    key = first.catalog()[0]["pack_id"]
    second = case.store()
    with pytest.raises(io.CropReviewPackIOError):
        second.read_for_revision(key)
    with pytest.raises(io.CropReviewPackIOError):
        second.read_for_revision(folder.name.removeprefix("pack-"))
    assert second.read_for_revision(second.catalog()[0]["pack_id"])["pack"] == case.pack


def test_revision_read_rejects_source_change_before_payload_capture(storage_case, monkeypatch):
    case = storage_case
    _write_pack(case.root, case.pack)
    store = case.store()
    key = store.catalog()[0]["pack_id"]
    store._verify_inputs = lambda: False
    monkeypatch.setattr(io, "_snapshot", lambda *a, **k: (_ for _ in ()).throw(AssertionError("unexpected I/O")))
    with pytest.raises(io.CropReviewPackIOError) as error:
        store.read_for_revision(key)
    assert error.value.code == "input_changed"


def test_revision_read_cancellation_is_not_partial_byte_return(storage_case, monkeypatch):
    case = storage_case
    _write_pack(case.root, case.pack)
    store = case.store()
    key = store.catalog()[0]["pack_id"]
    cancellation = KeyboardInterrupt("generated cancellation")
    def cancel(*args, **kwargs):
        raise cancellation
    monkeypatch.setattr(policy, "validate_crop_review_pack", cancel)
    with pytest.raises(KeyboardInterrupt) as error:
        store.read_for_revision(key)
    assert error.value is cancellation
    assert store.catalog()[0]["status"] == "present_unverified"


def test_revision_read_retains_aggregate_preflight_before_payload_capture(storage_case, monkeypatch):
    case = storage_case
    folder = _write_pack(case.root, case.pack)
    store = case.store()
    key = store.catalog()[0]["pack_id"]
    manifest = copy.deepcopy(case.pack["manifest"])
    monkeypatch.setattr(io, "_manifest", lambda _raw, _binding: manifest)
    monkeypatch.setattr(policy, "MAX_PACK_BYTES", manifest["total_file_bytes"])
    original, reads = io._snapshot, []
    def capture(path, *args, **kwargs):
        reads.append(path.relative_to(folder).as_posix())
        return original(path, *args, **kwargs)
    monkeypatch.setattr(io, "_snapshot", capture)
    with pytest.raises(io.CropReviewPackIOError):
        store.read_for_revision(key)
    assert reads == ["manifest.json"]
