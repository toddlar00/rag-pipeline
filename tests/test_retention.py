import json
import os
import threading
from types import SimpleNamespace

import pytest

import job_runtime
import retention


DAY = 86_400.0


def _stat_view(result, **changes):
    values = {
        "st_mode": result.st_mode,
        "st_nlink": result.st_nlink,
        "st_size": result.st_size,
        "st_dev": result.st_dev,
        "st_ino": result.st_ino,
        "st_mtime_ns": result.st_mtime_ns,
        "st_ctime_ns": result.st_ctime_ns,
    }
    values.update(changes)
    return SimpleNamespace(**values)


def _create_owned_run(tmp_path, *, with_sibling=True):
    output_root = tmp_path / "output"
    run_root = output_root / "book"
    sibling = output_root / "book_preprocessed.pdf"
    vector_stores = [
        {
            "backend": "chroma",
            "path": str(run_root / "chroma_db"),
            "collection": "book_chunks",
        },
        {
            "backend": "qdrant",
            "path": str(run_root / "qdrant_db"),
            "collection": "book_chunks",
        },
    ]
    manifest = retention.ensure_pipeline_run_manifest(
        output_root,
        run_root,
        job_scope="book-job",
        owned_siblings=[sibling] if with_sibling else [],
        vector_stores=vector_stores,
    )
    (run_root / "chunks.jsonl").write_text(
        '{"text":"private"}\n', encoding="utf-8")
    (run_root / "chroma_db").mkdir()
    (run_root / "chroma_db" / "vectors.bin").write_bytes(b"vectors")
    if with_sibling:
        sibling.write_bytes(b"pdf")
    return output_root, run_root, sibling, manifest, vector_stores


def _write_cache_record(cache_root, key, *, timestamp, result=None):
    record = cache_root / key[:2] / f"{key}.json"
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(json.dumps({
        "schema_version": 2,
        "cache_key": key,
        "result": {} if result is None else result,
    }), encoding="utf-8")
    os.utime(record, (timestamp, timestamp))
    return record


def _write_ui_export(output_root, token, *, state, updated_at):
    export_root = output_root / "book" / "ui_exports" / token
    export_root.mkdir(parents=True)
    (export_root / "export.json").write_text(
        '{"private":true}', encoding="utf-8")
    (export_root / retention.UI_EXPORT_MARKER_NAME).write_text(
        json.dumps({
            "schema_version": 1,
            "kind": "ui_export",
            "ownership_token": token,
            "state": state,
            "created_at": updated_at - 10,
            "updated_at": updated_at,
            "artifacts": ["export.json"],
        }),
        encoding="utf-8",
    )
    return export_root


def _write_quarantine_operation(root, token, *, created_at):
    operation_root = (
        root / retention.QUARANTINE_DIRECTORY_NAME / token)
    operation_root.mkdir(parents=True)
    (operation_root / "private.bin").write_bytes(b"private")
    (operation_root / retention.QUARANTINE_RECEIPT_NAME).write_text(
        json.dumps({
            "schema_version": retention.QUARANTINE_SCHEMA_VERSION,
            "kind": "retention_quarantine",
            "operation_id": token,
            "action": "delete_pipeline_run",
            "created_at": created_at,
            "state": "staged",
            "entries": ["book"],
        }),
        encoding="utf-8",
    )
    return operation_root


def test_owned_marker_read_retries_ctime_only_synced_folder_churn(
        monkeypatch, tmp_path):
    marker = tmp_path / "marker.json"
    marker.write_text('{"value":"stable"}', encoding="utf-8")
    real_fstat = retention.os.fstat
    fstat_calls = 0
    delays = []

    def churn_once(descriptor):
        nonlocal fstat_calls
        fstat_calls += 1
        result = real_fstat(descriptor)
        if fstat_calls == 2:
            return _stat_view(
                result, st_ctime_ns=result.st_ctime_ns + 1)
        return result

    monkeypatch.setattr(retention.os, "fstat", churn_once)
    monkeypatch.setattr(retention.time, "sleep", delays.append)

    assert retention._read_json_object(marker) == {"value": "stable"}
    assert fstat_calls == 4
    assert delays == [retention._SYNCED_FOLDER_READ_RETRY_DELAYS[0]]


def test_owned_marker_read_exhausts_bounded_ctime_retries(
        monkeypatch, tmp_path):
    marker = tmp_path / "marker.json"
    marker.write_text('{"value":"stable"}', encoding="utf-8")
    real_fstat = retention.os.fstat
    fstat_calls = 0
    delays = []

    def always_churning(descriptor):
        nonlocal fstat_calls
        fstat_calls += 1
        result = real_fstat(descriptor)
        if fstat_calls % 2 == 0:
            return _stat_view(
                result, st_ctime_ns=result.st_ctime_ns + fstat_calls)
        return result

    monkeypatch.setattr(retention.os, "fstat", always_churning)
    monkeypatch.setattr(retention.time, "sleep", delays.append)

    with pytest.raises(retention.RetentionError, match="changed while reading"):
        retention._read_json_object(marker)

    assert fstat_calls == 2 * (
        len(retention._SYNCED_FOLDER_READ_RETRY_DELAYS) + 1)
    assert delays == list(retention._SYNCED_FOLDER_READ_RETRY_DELAYS)


def test_owned_marker_read_rejects_new_bytes_across_retry(
        monkeypatch, tmp_path):
    marker = tmp_path / "marker.json"
    marker.write_text('{"value":"aa"}', encoding="utf-8")
    baseline = marker.stat()
    real_fstat = retention.os.fstat
    fstat_calls = 0
    delays = []

    def pinned_metadata(descriptor):
        nonlocal fstat_calls
        fstat_calls += 1
        result = real_fstat(descriptor)
        ctime = baseline.st_ctime_ns + (1 if fstat_calls == 2 else 2)
        if fstat_calls == 1:
            ctime = baseline.st_ctime_ns
        return _stat_view(
            result,
            st_dev=baseline.st_dev,
            st_ino=baseline.st_ino,
            st_size=baseline.st_size,
            st_mtime_ns=baseline.st_mtime_ns,
            st_nlink=baseline.st_nlink,
            st_ctime_ns=ctime,
        )

    def replace_bytes(delay):
        delays.append(delay)
        marker.write_text('{"value":"bb"}', encoding="utf-8")

    monkeypatch.setattr(retention.os, "fstat", pinned_metadata)
    monkeypatch.setattr(retention.time, "sleep", replace_bytes)

    with pytest.raises(retention.RetentionError, match="changed while reading"):
        retention._read_json_object(marker)

    assert fstat_calls == 4
    assert delays == [retention._SYNCED_FOLDER_READ_RETRY_DELAYS[0]]


def test_invalid_owned_marker_is_not_retried(monkeypatch, tmp_path):
    marker = tmp_path / "marker.json"
    marker.write_text("not-json", encoding="utf-8")
    real_fstat = retention.os.fstat
    fstat_calls = 0
    delays = []

    def counting_fstat(descriptor):
        nonlocal fstat_calls
        fstat_calls += 1
        return real_fstat(descriptor)

    monkeypatch.setattr(retention.os, "fstat", counting_fstat)
    monkeypatch.setattr(retention.time, "sleep", delays.append)

    with pytest.raises(retention.RetentionError, match="invalid owned marker"):
        retention._read_json_object(marker)

    assert fstat_calls == 2
    assert delays == []


def test_pipeline_manifest_create_resume_state_and_conflict(tmp_path):
    output_root, run_root, sibling, manifest, vector_stores = (
        _create_owned_run(tmp_path))
    initial = json.loads(manifest.read_text(encoding="utf-8"))

    assert initial["state"] == "active"
    assert initial["run_name"] == "book"
    assert initial["owned_siblings"] == [sibling.name]
    assert len(initial["ownership_token"]) == 32

    retention.mark_pipeline_run_state(manifest, "complete")
    completed = json.loads(manifest.read_text(encoding="utf-8"))
    assert completed["state"] == "complete"
    assert completed["ownership_token"] == initial["ownership_token"]

    resumed = retention.ensure_pipeline_run_manifest(
        output_root,
        run_root,
        job_scope="book-job",
        owned_siblings=[sibling],
        vector_stores=list(reversed(vector_stores)),
    )
    resumed_payload = json.loads(resumed.read_text(encoding="utf-8"))
    assert resumed_payload["state"] == "active"
    assert resumed_payload["created_at"] == initial["created_at"]
    assert resumed_payload["ownership_token"] == initial["ownership_token"]
    assert resumed_payload["updated_at"] >= completed["updated_at"]

    with pytest.raises(retention.RetentionError, match="conflicts"):
        retention.ensure_pipeline_run_manifest(
            output_root,
            run_root,
            job_scope="different-job",
            owned_siblings=[sibling],
            vector_stores=vector_stores,
        )
    with pytest.raises(ValueError, match="terminal"):
        retention.mark_pipeline_run_state(manifest, "active")


def test_new_manifest_refuses_to_claim_a_preexisting_sibling(tmp_path):
    output_root = tmp_path / "output"
    output_root.mkdir()
    sibling = output_root / "book_preprocessed.pdf"
    sibling.write_bytes(b"unowned")

    with pytest.raises(retention.RetentionError, match="pre-existing sibling"):
        retention.ensure_pipeline_run_manifest(
            output_root,
            output_root / "book",
            job_scope="book",
            owned_siblings=[sibling],
            vector_stores=[],
        )

    assert sibling.read_bytes() == b"unowned"
    assert not (output_root / "book").exists()


def test_pipeline_deletion_plan_is_a_non_mutating_dry_run(tmp_path):
    output_root, run_root, sibling, manifest, _ = _create_owned_run(tmp_path)
    before_manifest = manifest.read_bytes()
    before_chunks = (run_root / "chunks.jsonl").read_bytes()

    plan = retention.plan_pipeline_run_deletion(
        output_root, "book", now=2_000_000_000.0)

    assert plan.action == "delete_pipeline_run"
    assert [candidate.category for candidate in plan.candidates] == [
        "pipeline_run", "pipeline_sibling"]
    assert plan.as_dict()["mode"] == "dry_run"
    assert plan.as_dict()["apply_required"] is True
    assert manifest.read_bytes() == before_manifest
    assert (run_root / "chunks.jsonl").read_bytes() == before_chunks
    assert sibling.read_bytes() == b"pdf"
    assert not (output_root / retention.QUARANTINE_DIRECTORY_NAME).exists()


def test_applied_pipeline_deletion_removes_only_manifest_owned_paths(tmp_path):
    output_root, run_root, sibling, _, _ = _create_owned_run(tmp_path)
    unrelated = output_root / "keep.txt"
    unrelated.write_text("keep", encoding="utf-8")
    plan = retention.plan_pipeline_run_deletion(output_root, "book")

    result = retention.apply_retention_plan(plan)

    assert result == {
        "action": "delete_pipeline_run",
        "mode": "applied",
        "deleted_count": 2,
        "deleted_bytes": plan.total_bytes,
    }
    assert not run_root.exists()
    assert not sibling.exists()
    assert unrelated.read_text(encoding="utf-8") == "keep"
    quarantine = output_root / retention.QUARANTINE_DIRECTORY_NAME
    assert quarantine.is_dir()
    assert list(quarantine.iterdir()) == []


def test_pipeline_deletion_refuses_an_unowned_run(tmp_path):
    output_root = tmp_path / "output"
    unowned = output_root / "unowned"
    unowned.mkdir(parents=True)
    (unowned / "private.txt").write_text("private", encoding="utf-8")

    with pytest.raises(retention.RetentionError, match="manifest|owned"):
        retention.plan_pipeline_run_deletion(output_root, "unowned")

    assert unowned.is_dir()
    assert (unowned / "private.txt").is_file()


def test_pipeline_deletion_refuses_symlinks_within_owned_tree(tmp_path):
    output_root, run_root, _, _, _ = _create_owned_run(
        tmp_path, with_sibling=False)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    link = run_root / "linked.txt"
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable on this platform: {exc}")

    with pytest.raises(retention.RetentionError, match="link|junction"):
        retention.plan_pipeline_run_deletion(output_root, "book")

    assert outside.read_text(encoding="utf-8") == "outside"


def test_pipeline_deletion_refuses_hardlinks_within_owned_tree(tmp_path):
    output_root, run_root, _, _, _ = _create_owned_run(
        tmp_path, with_sibling=False)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    os.link(outside, run_root / "hardlinked.txt")

    with pytest.raises(retention.RetentionError, match="hard-linked"):
        retention.plan_pipeline_run_deletion(output_root, "book")

    assert outside.read_text(encoding="utf-8") == "outside"


def test_cache_prune_applies_ttl_only_to_valid_owned_records(tmp_path):
    cache_root = tmp_path / "cache"
    now = 2_000_000_000.0
    old_key = "ab" + "1" * 62
    fresh_key = "cd" + "2" * 62
    old_record = _write_cache_record(
        cache_root, old_key, timestamp=now - 10 * DAY)
    fresh_record = _write_cache_record(
        cache_root, fresh_key, timestamp=now - DAY)
    unrelated = cache_root / "ab" / "notes.txt"
    unrelated.write_text("not pipeline-owned", encoding="utf-8")

    plan = retention.plan_llm_cache_prune(
        cache_root, older_than_days=5, now=now)

    assert [candidate.path for candidate in plan.candidates] == [old_record]
    assert old_record.is_file()
    result = retention.apply_retention_plan(plan)
    assert result["deleted_count"] == 1
    assert not old_record.exists()
    assert fresh_record.is_file()
    assert unrelated.is_file()


def test_cache_prune_rejects_an_invalid_matching_ownership_record(tmp_path):
    cache_root = tmp_path / "cache"
    now = 2_000_000_000.0
    key = "ef" + "3" * 62
    record = _write_cache_record(
        cache_root, key, timestamp=now - 10 * DAY)
    payload = json.loads(record.read_text(encoding="utf-8"))
    payload["cache_key"] = "0" * 64
    record.write_text(json.dumps(payload), encoding="utf-8")
    os.utime(record, (now - 10 * DAY, now - 10 * DAY))

    with pytest.raises(retention.RetentionError, match="ownership"):
        retention.plan_llm_cache_prune(
            cache_root, older_than_days=5, now=now)

    assert record.is_file()


def test_cache_change_after_planning_fails_closed(tmp_path):
    cache_root = tmp_path / "cache"
    now = 2_000_000_000.0
    key = "aa" + "4" * 62
    record = _write_cache_record(
        cache_root, key, timestamp=now - 10 * DAY)
    plan = retention.plan_llm_cache_prune(
        cache_root, older_than_days=5, now=now)
    record.write_text(json.dumps({
        "schema_version": 2,
        "cache_key": key,
        "result": {"changed": True},
    }), encoding="utf-8")

    with pytest.raises(retention.RetentionError, match="changed after planning"):
        retention.apply_retention_plan(plan)

    assert record.is_file()
    assert not (cache_root / retention.QUARANTINE_DIRECTORY_NAME).exists()


def test_cache_replacement_between_validation_and_move_is_rolled_back(
        monkeypatch, tmp_path):
    cache_root = tmp_path / "cache"
    now = 2_000_000_000.0
    key = "bb" + "5" * 62
    record = _write_cache_record(
        cache_root, key, timestamp=now - 10 * DAY,
        result={"text": "planned"})
    plan = retention.plan_llm_cache_prune(
        cache_root, older_than_days=5, now=now)
    real_replace = retention.os.replace
    replacement = record.with_suffix(".replacement")
    replaced = False

    def replace_after_validation(source, destination):
        nonlocal replaced
        if not replaced and source == record:
            replacement.write_text(json.dumps({
                "schema_version": 2,
                "cache_key": key,
                "result": {"text": "new generation"},
            }), encoding="utf-8")
            real_replace(replacement, record)
            replaced = True
        return real_replace(source, destination)

    monkeypatch.setattr(retention.os, "replace", replace_after_validation)

    with pytest.raises(retention.RetentionError, match="changed after planning"):
        retention.apply_retention_plan(plan)

    assert json.loads(record.read_text(encoding="utf-8"))["result"] == {
        "text": "new generation"}
    quarantine = cache_root / retention.QUARANTINE_DIRECTORY_NAME
    assert quarantine.is_dir()
    assert list(quarantine.iterdir()) == []


def test_run_change_between_validation_and_move_is_rolled_back(
        monkeypatch, tmp_path):
    output_root, run_root, _, _, _ = _create_owned_run(
        tmp_path, with_sibling=False)
    chunks = run_root / "chunks.jsonl"
    plan = retention.plan_pipeline_run_deletion(output_root, "book")
    real_replace = retention.os.replace
    changed = False

    def replace_after_validation(source, destination):
        nonlocal changed
        if not changed and source == run_root:
            chunks.write_text(
                '{"text":"new generation"}\n', encoding="utf-8")
            changed = True
        return real_replace(source, destination)

    monkeypatch.setattr(retention.os, "replace", replace_after_validation)

    with pytest.raises(retention.RetentionError, match="changed after planning"):
        retention.apply_retention_plan(plan)

    assert chunks.read_text(encoding="utf-8") == (
        '{"text":"new generation"}\n')
    quarantine = output_root / retention.QUARANTINE_DIRECTORY_NAME
    assert quarantine.is_dir()
    assert list(quarantine.iterdir()) == []


def test_cache_prune_enforces_total_size_oldest_first(tmp_path):
    cache_root = tmp_path / "cache"
    now = 2_000_000_000.0
    older_key = "10" + "1" * 62
    newer_key = "20" + "2" * 62
    older = _write_cache_record(
        cache_root, older_key, timestamp=now - 2 * DAY,
        result={"text": "older"})
    newer = _write_cache_record(
        cache_root, newer_key, timestamp=now - DAY,
        result={"text": "newer"})

    plan = retention.plan_llm_cache_prune(
        cache_root, older_than_days=30,
        max_total_bytes=newer.stat().st_size, now=now)

    assert [candidate.path for candidate in plan.candidates] == [older]
    assert plan.context["owned_bytes_after_plan"] == newer.stat().st_size


def test_ui_prune_selects_only_old_completed_owned_exports(tmp_path):
    output_root = tmp_path / "output"
    now = 2_000_000_000.0
    old_complete = _write_ui_export(
        output_root, "1" * 32, state="complete",
        updated_at=now - 10 * DAY)
    old_creating = _write_ui_export(
        output_root, "2" * 32, state="creating",
        updated_at=now - 10 * DAY)
    fresh_complete = _write_ui_export(
        output_root, "3" * 32, state="complete",
        updated_at=now - DAY)

    plan = retention.plan_ui_export_prune(
        output_root, older_than_days=5, now=now)

    assert [candidate.path for candidate in plan.candidates] == [old_complete]
    result = retention.apply_retention_plan(plan)
    assert result["deleted_count"] == 1
    assert not old_complete.exists()
    assert old_creating.is_dir()
    assert fresh_complete.is_dir()


def test_quarantine_purge_is_dry_run_first_and_removes_only_old_receipts(
        tmp_path):
    root = tmp_path / "output"
    now = 2_000_000_000.0
    old_operation = _write_quarantine_operation(
        root, "4" * 32, created_at=now - 10 * DAY)
    fresh_operation = _write_quarantine_operation(
        root, "5" * 32, created_at=now - DAY)

    plan = retention.plan_quarantine_purge(
        root, older_than_days=5, now=now)

    assert [candidate.path for candidate in plan.candidates] == [old_operation]
    assert plan.as_dict()["mode"] == "dry_run"
    assert old_operation.is_dir()
    result = retention.apply_quarantine_purge(plan)
    assert result["deleted_count"] == 1
    assert not old_operation.exists()
    assert fresh_operation.is_dir()


def test_staging_failure_rolls_back_every_moved_candidate(
        monkeypatch, tmp_path):
    output_root, run_root, sibling, _, _ = _create_owned_run(tmp_path)
    plan = retention.plan_pipeline_run_deletion(output_root, "book")
    real_os = retention.os

    class FailingReplaceOS:
        def __init__(self):
            self.replace_calls = 0

        def __getattr__(self, name):
            return getattr(real_os, name)

        def replace(self, source, destination):
            self.replace_calls += 1
            if self.replace_calls == 2:
                raise OSError("injected staging failure")
            return real_os.replace(source, destination)

    failing_os = FailingReplaceOS()
    monkeypatch.setattr(retention, "os", failing_os)

    with pytest.raises(OSError, match="injected staging failure"):
        retention.apply_retention_plan(plan)

    assert failing_os.replace_calls == 3
    assert run_root.is_dir()
    assert (run_root / retention.RUN_MANIFEST_NAME).is_file()
    assert sibling.read_bytes() == b"pdf"
    quarantine = output_root / retention.QUARANTINE_DIRECTORY_NAME
    assert quarantine.is_dir()
    assert list(quarantine.iterdir()) == []


def test_background_job_deletion_is_terminal_and_dry_run_first(tmp_path):
    store = job_runtime.JobStore(tmp_path / "jobs")
    submitted = store.submit_job("export", ["--chunks", "Private.jsonl"])
    execution = store.load_execution(submitted.job_id)
    failed = store.transition_job(
        submitted.job_id, "failed",
        attempt_token=execution.attempt_token,
        expected_revision=execution.revision)

    plan = retention.plan_background_job_deletion(
        store.root, submitted.job_id)

    assert plan.action == "delete_background_job"
    assert plan.as_dict()["mode"] == "dry_run"
    assert plan.candidates[0].path.is_dir()
    assert store.get_job(submitted.job_id).status == failed.status
    with pytest.raises(retention.RetentionError, match="must be 'deleting'"):
        retention.apply_retention_plan(plan)
    assert store.get_job(submitted.job_id).status == "failed"

    deleting = store.prepare_delete(submitted.job_id)
    assert deleting.status == "deleting"
    applied_plan = retention.plan_background_job_deletion(
        store.root, submitted.job_id)
    result = retention.apply_retention_plan(applied_plan)

    assert result["action"] == "delete_background_job"
    assert result["deleted_count"] == 1
    assert not (store.root / submitted.job_id).exists()
    assert store.list_jobs() == []


def test_background_job_quarantine_serializes_with_job_listing(
        monkeypatch, tmp_path):
    store = job_runtime.JobStore(tmp_path / "jobs")
    submitted = store.submit_job("export", ["--chunks", "Private.jsonl"])
    execution = store.load_execution(submitted.job_id)
    store.transition_job(
        submitted.job_id, "failed",
        attempt_token=execution.attempt_token,
        expected_revision=execution.revision)
    store.prepare_delete(submitted.job_id)
    plan = retention.plan_background_job_deletion(
        store.root, submitted.job_id)

    listing_inside_lock = threading.Event()
    release_listing = threading.Event()
    apply_entered = threading.Event()
    real_get_job = store.get_job
    real_apply = retention._apply_retention_plan_unlocked

    def paused_get_job(job_id):
        listing_inside_lock.set()
        assert release_listing.wait(5)
        return real_get_job(job_id)

    def observed_apply(selected_plan):
        apply_entered.set()
        return real_apply(selected_plan)

    monkeypatch.setattr(store, "get_job", paused_get_job)
    monkeypatch.setattr(
        retention, "_apply_retention_plan_unlocked", observed_apply)
    outcomes = {}

    def list_jobs():
        try:
            outcomes["listed"] = store.list_jobs()
        except BaseException as exc:
            outcomes["list_error"] = exc

    def delete_job():
        try:
            outcomes["deleted"] = retention.apply_retention_plan(plan)
        except BaseException as exc:
            outcomes["delete_error"] = exc

    listing = threading.Thread(target=list_jobs)
    deleting = threading.Thread(target=delete_job)
    listing.start()
    assert listing_inside_lock.wait(5)
    deleting.start()
    assert not apply_entered.wait(0.2)
    release_listing.set()
    listing.join(timeout=5)
    deleting.join(timeout=5)

    assert not listing.is_alive()
    assert not deleting.is_alive()
    assert "list_error" not in outcomes
    assert "delete_error" not in outcomes
    assert outcomes["listed"][0].status == "deleting"
    assert outcomes["deleted"]["deleted_count"] == 1
    assert store.list_jobs() == []


def test_cancel_request_cannot_resurrect_a_deleted_job(
        monkeypatch, tmp_path):
    store = job_runtime.JobStore(tmp_path / "jobs")
    submitted = store.submit_job("export", [])
    execution = store.load_execution(submitted.job_id)
    starting = store.transition_job(
        submitted.job_id, "starting",
        attempt_token=execution.attempt_token,
        expected_revision=execution.revision)
    running = store.transition_job(
        submitted.job_id, "running",
        attempt_token=execution.attempt_token,
        expected_revision=starting.revision)

    cancellation_read = threading.Event()
    release_cancellation = threading.Event()
    deletion_prepared = threading.Event()
    real_load = store._load_record
    pause_once = True

    def paused_load(job_id):
        nonlocal pause_once
        record = real_load(job_id)
        if pause_once:
            pause_once = False
            cancellation_read.set()
            assert release_cancellation.wait(5)
        return record

    monkeypatch.setattr(store, "_load_record", paused_load)
    outcomes = {}

    def cancel_job():
        try:
            outcomes["cancelled"] = store.request_cancel(submitted.job_id)
        except BaseException as exc:
            outcomes["cancel_error"] = exc

    cancellation = threading.Thread(target=cancel_job)
    cancellation.start()
    assert cancellation_read.wait(5)

    def delete_job():
        try:
            store.transition_job(
                submitted.job_id, "failed",
                attempt_token=execution.attempt_token,
                expected_revision=running.revision)
            store.prepare_delete(submitted.job_id)
            deletion_prepared.set()
            plan = retention.plan_background_job_deletion(
                store.root, submitted.job_id)
            outcomes["deleted"] = retention.apply_retention_plan(plan)
        except BaseException as exc:
            outcomes["delete_error"] = exc

    deletion = threading.Thread(target=delete_job)
    deletion.start()
    assert not deletion_prepared.wait(0.2)
    release_cancellation.set()
    cancellation.join(timeout=5)
    deletion.join(timeout=10)

    assert not cancellation.is_alive()
    assert not deletion.is_alive()
    assert "cancel_error" not in outcomes
    assert "delete_error" not in outcomes
    assert outcomes["deleted"]["deleted_count"] == 1
    assert not (store.root / submitted.job_id).exists()
    assert store.list_jobs() == []


def test_background_job_deletion_rejects_active_and_orphaned_jobs(tmp_path):
    store = job_runtime.JobStore(tmp_path / "jobs")
    active = store.submit_job("export", ["--chunks", "Private.jsonl"])
    with pytest.raises(retention.RetentionError, match="cannot be deleted"):
        retention.plan_background_job_deletion(store.root, active.job_id)

    execution = store.load_execution(active.job_id)
    starting = store.transition_job(
        active.job_id, "starting", attempt_token=execution.attempt_token,
        expected_revision=execution.revision)
    store.transition_job(
        active.job_id, "orphaned", attempt_token=execution.attempt_token,
        expected_revision=starting.revision)

    with pytest.raises(retention.RetentionError, match="cannot be deleted"):
        retention.plan_background_job_deletion(store.root, active.job_id)
    with pytest.raises(job_runtime.JobStateError, match="cannot be deleted"):
        store.prepare_delete(active.job_id)
