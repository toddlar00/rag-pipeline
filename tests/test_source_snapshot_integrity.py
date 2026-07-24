"""Regression tests for immutable conversion and chunk source generations."""

from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import errno
import hashlib
import json
import os
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

import artifact_io
import rag


def _stat_view(result, **changes):
    values = {
        "st_dev": result.st_dev,
        "st_ino": result.st_ino,
        "st_size": result.st_size,
        "st_mtime_ns": result.st_mtime_ns,
        "st_ctime_ns": result.st_ctime_ns,
    }
    values.update(changes)
    return SimpleNamespace(**values)


def test_immutable_file_snapshot_pins_bytes_and_cleans_up(tmp_path):
    source = tmp_path / "book.pdf"
    source.write_bytes(b"generation-a")

    with artifact_io.immutable_file_snapshot(
        source, temporary_root=tmp_path
    ) as snapshot:
        staged_path = snapshot.path
        assert staged_path != source
        assert staged_path.name == source.name
        assert staged_path.read_bytes() == b"generation-a"
        assert snapshot.sha256 == hashlib.sha256(b"generation-a").hexdigest()
        source.write_bytes(b"generation-b")
        snapshot.verify()

    assert not staged_path.exists()
    assert list(tmp_path.glob("rag-source-*")) == []


def test_immutable_file_snapshot_rejects_staging_tamper(tmp_path):
    source = tmp_path / "book.pdf"
    source.write_bytes(b"generation-a")

    with pytest.raises(RuntimeError, match="Immutable snapshot changed"):
        with artifact_io.immutable_file_snapshot(
            source, temporary_root=tmp_path
        ) as snapshot:
            snapshot.path.write_bytes(b"generation-b")


def test_immutable_file_snapshot_surfaces_hardlink_privacy_breach(tmp_path):
    source = tmp_path / "book.pdf"
    source.write_bytes(b"private-generation")
    leaked = tmp_path / "leaked.pdf"
    snapshot_path = None

    try:
        with pytest.raises(RuntimeError, match="multiply linked"):
            with artifact_io.immutable_file_snapshot(
                    source, temporary_root=tmp_path) as snapshot:
                snapshot_path = snapshot.path
                try:
                    os.link(snapshot.path, leaked)
                except OSError as exc:
                    pytest.skip(f"hard links unavailable: {exc}")

        assert leaked.read_bytes() == b"private-generation"
        assert snapshot_path is not None
        assert snapshot_path.is_file()
        assert (snapshot_path.parent
                / artifact_io._SNAPSHOT_OWNER_MARKER).is_file()
    finally:
        leaked.unlink(missing_ok=True)
        if snapshot_path is not None and snapshot_path.parent.is_dir():
            marker_path = (
                snapshot_path.parent / artifact_io._SNAPSHOT_OWNER_MARKER)
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
            marker["pid"] = 2_147_483_647
            marker["process_birth"] = None
            marker["created_ns"] = 1
            artifact_io.atomic_write_private_json(marker_path, marker)
            artifact_io.cleanup_stale_snapshot_directories(
                tmp_path, min_age_seconds=0, now_ns=10, apply=True)


def test_immutable_file_snapshot_retries_only_ctime_churn(
    monkeypatch, tmp_path
):
    source = tmp_path / "book.pdf"
    source.write_bytes(b"stable")
    baseline = source.stat()
    real_fstat = artifact_io.os.fstat
    source_fstats = 0
    delays = []

    def churn_once(descriptor):
        nonlocal source_fstats
        result = real_fstat(descriptor)
        if (result.st_dev, result.st_ino) == (
            baseline.st_dev, baseline.st_ino
        ):
            source_fstats += 1
            if source_fstats == 2:
                return _stat_view(
                    result, st_ctime_ns=result.st_ctime_ns + 1
                )
        return result

    monkeypatch.setattr(artifact_io.os, "fstat", churn_once)
    monkeypatch.setattr(artifact_io.time, "sleep", delays.append)

    with artifact_io.immutable_file_snapshot(
        source, temporary_root=tmp_path
    ) as snapshot:
        assert snapshot.path.read_bytes() == b"stable"

    assert source_fstats >= 4
    assert delays == [artifact_io._SYNCED_FOLDER_READ_RETRY_DELAYS[0]]


def test_immutable_file_snapshot_rejects_new_bytes_across_retry(
    monkeypatch, tmp_path
):
    source = tmp_path / "book.pdf"
    source.write_bytes(b"AAAA")
    baseline = source.stat()
    real_fstat = artifact_io.os.fstat
    source_fstats = 0

    def pinned_metadata(descriptor):
        nonlocal source_fstats
        result = real_fstat(descriptor)
        if (result.st_dev, result.st_ino) != (
            baseline.st_dev, baseline.st_ino
        ):
            return result
        source_fstats += 1
        return _stat_view(
            result,
            st_dev=baseline.st_dev,
            st_ino=baseline.st_ino,
            st_size=baseline.st_size,
            st_mtime_ns=baseline.st_mtime_ns,
            st_ctime_ns=baseline.st_ctime_ns + (source_fstats > 1),
        )

    def replace_source(_delay):
        source.write_bytes(b"BBBB")

    monkeypatch.setattr(artifact_io.os, "fstat", pinned_metadata)
    monkeypatch.setattr(artifact_io.time, "sleep", replace_source)

    with pytest.raises(RuntimeError, match="changed while it was snapshotted"):
        with artifact_io.immutable_file_snapshot(
            source, temporary_root=tmp_path
        ):
            pass


def test_docling_loader_uses_one_exact_json_snapshot(monkeypatch, tmp_path):
    document_path = tmp_path / "book.json"
    raw = b'{"origin":{"filename":"book.pdf"},"texts":[]}'
    mappings = []

    class FakeDoclingDocument:
        @classmethod
        def model_validate(cls, value):
            mappings.append(value)
            return {"validated": value}

    monkeypatch.setattr(
        rag,
        "_read_index_artifact_snapshot",
        lambda path: (
            raw,
            hashlib.sha256(raw).hexdigest(),
            (1, 2, len(raw), 3, 4),
        ),
    )

    document, mapping, digest, size = rag._load_docling_document_snapshot(
        document_path, FakeDoclingDocument
    )

    assert document["validated"] == mapping
    assert mappings == [mapping]
    assert mappings[0] is mapping
    assert digest == hashlib.sha256(raw).hexdigest()
    assert size == len(raw)


def _write_conversion_manifest(
    document: Path, *, source_name: str, source_sha256: str
) -> None:
    raw = document.read_bytes()
    manifest = rag._artifact_completion_path(document, stage="conversion")
    rag._atomic_write_json(
        manifest,
        {
            "schema_version": rag.CONVERSION_COMPLETION_SCHEMA_VERSION,
            "stage": "conversion",
            "source_name": source_name,
            "source_sha256": source_sha256,
            "source_record_count": None,
            "parameters_sha256": "a" * 64,
            "source": {
                "name": source_name,
                "size": 123,
                "sha256": source_sha256,
                "capture_policy": "stream-copy-v1",
            },
            "effective_input": {
                "kind": "original",
                "name": source_name,
                "size": 123,
                "sha256": source_sha256,
            },
            "outputs": [
                {
                    "role": "docling_json",
                    "name": document.name,
                    "size": len(raw),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                },
                {
                    "role": "docling_markdown",
                    "name": "book_docling.md",
                    "size": 8,
                    "sha256": hashlib.sha256(b"complete").hexdigest(),
                },
            ],
        },
    )


def test_source_pdf_lookup_skips_nearer_same_name_with_wrong_hash(tmp_path):
    correct = tmp_path / "book.pdf"
    correct.write_bytes(b"correct-generation")
    run_dir = tmp_path / "output" / "run"
    run_dir.mkdir(parents=True)
    wrong = run_dir / "book.pdf"
    wrong.write_bytes(b"wrong-generation")
    document = run_dir / "book.json"
    document.write_text(
        '{"origin":{"filename":"book.pdf"}}', encoding="utf-8"
    )
    correct_sha = hashlib.sha256(correct.read_bytes()).hexdigest()
    _write_conversion_manifest(
        document, source_name="book.pdf", source_sha256=correct_sha
    )
    document_raw = document.read_bytes()

    identity = rag._conversion_source_identity(
        document,
        document_sha256=hashlib.sha256(document_raw).hexdigest(),
        document_size=len(document_raw),
    )

    assert identity == ("book.pdf", correct_sha)
    assert rag._find_docling_source_pdf(
        document, {"origin": {"filename": "book.pdf"}},
        source_identity=identity,
    ) == correct.resolve()


def test_source_pdf_lookup_rejects_unbound_or_mismatched_candidates(tmp_path):
    run_dir = tmp_path / "output" / "run"
    run_dir.mkdir(parents=True)
    candidate = run_dir / "book.pdf"
    candidate.write_bytes(b"wrong-generation")
    document = run_dir / "book.json"
    doc_dict = {"origin": {"filename": "book.pdf"}}

    assert rag._find_docling_source_pdf(document, doc_dict) is None
    assert rag._find_docling_source_pdf(
        document,
        doc_dict,
        source_identity=("book.pdf", hashlib.sha256(b"expected").hexdigest()),
    ) is None


def test_convert_wrapper_uses_snapshot_and_binds_original_source(
    monkeypatch, tmp_path
):
    source = tmp_path / "book.pdf"
    source.write_bytes(b"source-generation")
    staged = tmp_path / "private" / "book.pdf"
    document = tmp_path / "book.json"
    markdown = tmp_path / "book_docling.md"
    preprocessed = tmp_path / "book_preprocessed.pdf"
    preprocessed.write_bytes(b"stale-derived-generation")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    observed = {}

    class FakeSnapshot:
        path = staged
        source_name = "book.pdf"
        sha256 = digest
        size = len(b"source-generation")
        capture_policy = "stream-copy-v1"

    @contextmanager
    def fake_snapshot(path):
        observed["snapshot_source"] = path
        yield FakeSnapshot()

    def fake_generation(pdf_path, doc_output, **kwargs):
        observed["generation_path"] = pdf_path
        observed["generation_kwargs"] = kwargs
        rag._atomic_write_text(doc_output, '{"texts":[]}')
        rag._atomic_write_text(markdown, "complete")
        return kwargs["original_input"]

    monkeypatch.setattr(rag, "_immutable_file_snapshot", fake_snapshot)
    monkeypatch.setattr(rag, "_convert_pdf_generation", fake_generation)
    monkeypatch.setattr(
        rag, "_converted_outputs_complete", lambda *_args, **_kwargs: False
    )
    completion = {}
    monkeypatch.setattr(
        rag,
        "_write_artifact_completion",
        lambda *args, **kwargs: completion.update(kwargs),
    )

    rag.convert_pdf(
        source,
        document,
        backend="auto",
        auto_preprocess=False,
        preprocessed_output=preprocessed,
        markdown_output=markdown,
    )

    assert observed["snapshot_source"] == source
    assert observed["generation_path"] == staged
    assert observed["generation_kwargs"]["snapshot_stack"] is not None
    assert completion["source_name"] == "book.pdf"
    assert completion["source_sha256"] == digest
    assert completion["schema_version"] == 2
    assert set(completion["outputs"]) == {
        "docling_json", "docling_markdown"}
    assert not preprocessed.exists()
    assert completion["extra_fields"]["effective_input"] == {
        "kind": "original",
        "name": "book.pdf",
        "size": len(b"source-generation"),
        "sha256": digest,
    }


def test_hash_file_generation_uses_only_bounded_reads(monkeypatch, tmp_path):
    source = tmp_path / "large.pdf"
    source.write_bytes(b"0123456789abcdef")
    real_open = Path.open
    requested_sizes = []

    class GuardedReader:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            self.handle.__enter__()
            return self

        def __exit__(self, *args):
            return self.handle.__exit__(*args)

        def read(self, size=-1):
            requested_sizes.append(size)
            if size < 1 or size > 3:
                raise AssertionError(f"unbounded read requested: {size}")
            return self.handle.read(size)

        def __getattr__(self, name):
            return getattr(self.handle, name)

    def guarded_open(path, *args, **kwargs):
        handle = real_open(path, *args, **kwargs)
        if Path(path) == source and args and args[0] == "rb":
            return GuardedReader(handle)
        return handle

    monkeypatch.setattr(Path, "open", guarded_open)

    generation = artifact_io.hash_file_generation(source, chunk_size=3)

    assert generation.sha256 == hashlib.sha256(
        b"0123456789abcdef").hexdigest()
    assert generation.size == 16
    assert requested_sizes and max(requested_sizes) == 3


def test_default_snapshot_uses_owned_marked_scratch_outside_source(tmp_path):
    source = tmp_path / "private" / "book.pdf"
    source.parent.mkdir()
    source.write_bytes(b"source")

    with artifact_io.immutable_file_snapshot(source) as snapshot:
        assert snapshot.path.parent != source.parent
        assert snapshot.path.parent.parent.name == "rag-pipeline-scratch-v1"
        marker = snapshot.path.parent / ".rag-snapshot-owner.json"
        assert marker.is_file()
        payload = json.loads(marker.read_text(encoding="utf-8"))
        assert payload["nonce"] == snapshot.path.parent.name

    assert not snapshot.path.parent.exists()


def test_snapshot_rejects_owner_marker_as_source_basename(tmp_path):
    source = tmp_path / artifact_io._SNAPSHOT_OWNER_MARKER
    source.write_bytes(b"must remain source data")

    with pytest.raises(ValueError, match="unsafe snapshot basename"):
        with artifact_io.immutable_file_snapshot(
                source, temporary_root=tmp_path):
            pytest.fail("unsafe source basename was snapshotted")

    assert source.read_bytes() == b"must remain source data"


def test_snapshot_cleanup_preserves_body_error_and_reports_cleanup(
        monkeypatch, tmp_path):
    source = tmp_path / "book.pdf"
    source.write_bytes(b"source")
    real_unlink = Path.unlink
    real_pinned_unlink = artifact_io._PinnedSnapshotDirectory.unlink_regular
    snapshot_path = None
    failed = False

    def fail_snapshot_unlink(pinned, name, *args, **kwargs):
        nonlocal failed
        path = pinned.path / name
        if (not failed and path.is_file()
                and path.name == source.name
                and path.parent.name.startswith("run-")):
            failed = True
            raise OSError("unlink blocked")
        return real_pinned_unlink(pinned, name, *args, **kwargs)

    monkeypatch.setattr(
        artifact_io._PinnedSnapshotDirectory,
        "unlink_regular",
        fail_snapshot_unlink,
    )

    class BodyFailure(RuntimeError):
        pass

    cleanup_reports = []
    with pytest.raises(BodyFailure, match="body failed"):
        with artifact_io.immutable_file_snapshot(
                source, temporary_root=tmp_path,
                cleanup_error_fn=lambda *args, **kwargs: cleanup_reports.append(
                    (args, kwargs)),
        ) as snapshot:
            snapshot_path = snapshot.path
            raise BodyFailure("body failed")

    assert cleanup_reports
    assert isinstance(cleanup_reports[0][1]["error"], OSError)
    assert snapshot_path is not None
    assert (snapshot_path.parent / artifact_io._SNAPSHOT_OWNER_MARKER).is_file()
    real_unlink(snapshot_path, missing_ok=True)
    real_unlink(
        snapshot_path.parent / artifact_io._SNAPSHOT_OWNER_MARKER,
        missing_ok=True,
    )
    snapshot_path.parent.rmdir()


def test_snapshot_cleanup_failure_after_success_is_an_error(
        monkeypatch, tmp_path):
    source = tmp_path / "book.pdf"
    source.write_bytes(b"source")
    real_pinned_unlink = artifact_io._PinnedSnapshotDirectory.unlink_regular
    snapshot_path = None
    failed = False

    def fail_snapshot_unlink(pinned, name, *args, **kwargs):
        nonlocal failed
        path = pinned.path / name
        if (not failed and path.is_file()
                and path.name == source.name
                and path.parent.name.startswith("run-")):
            failed = True
            raise OSError("unlink blocked")
        return real_pinned_unlink(pinned, name, *args, **kwargs)

    monkeypatch.setattr(
        artifact_io._PinnedSnapshotDirectory,
        "unlink_regular",
        fail_snapshot_unlink,
    )

    with pytest.raises(OSError, match="unlink blocked"):
        with artifact_io.immutable_file_snapshot(
                source, temporary_root=tmp_path) as snapshot:
            snapshot_path = snapshot.path

    assert snapshot_path is not None
    marker_path = snapshot_path.parent / artifact_io._SNAPSHOT_OWNER_MARKER
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    marker["pid"] = 2_147_483_647
    marker["created_ns"] = 1
    artifact_io.atomic_write_private_json(marker_path, marker)

    removed = artifact_io.cleanup_stale_snapshot_directories(
        tmp_path, min_age_seconds=0, now_ns=10, apply=True)

    assert removed == [snapshot_path.parent]
    assert not snapshot_path.parent.exists()


def test_stale_snapshot_cleanup_removes_only_marker_owned_dead_runs(tmp_path):
    owned_root = artifact_io._snapshot_scratch_root(tmp_path)
    owned = artifact_io.ensure_private_directory(owned_root / "run-owned")
    artifact_io.atomic_write_private_json(
        owned / artifact_io._SNAPSHOT_OWNER_MARKER,
        {
            "schema_version": artifact_io._SNAPSHOT_OWNER_SCHEMA_VERSION,
            "kind": "rag_snapshot_scratch",
            "pid": 2_147_483_647,
            "process_birth": None,
            "created_ns": 1,
            "nonce": owned.name,
        },
    )
    (owned / "sensitive.pdf").write_bytes(b"sensitive")
    unowned = artifact_io.ensure_private_directory(owned_root / "run-unowned")
    (unowned / "keep.pdf").write_bytes(b"keep")

    planned = artifact_io.cleanup_stale_snapshot_directories(
        tmp_path, min_age_seconds=0, now_ns=10, apply=False)
    removed = artifact_io.cleanup_stale_snapshot_directories(
        tmp_path, min_age_seconds=0, now_ns=10, apply=True)

    assert planned == [owned]
    assert removed == [owned]
    assert not owned.exists()
    assert unowned.is_dir()


def test_stale_snapshot_cleanup_dry_run_does_not_create_root(tmp_path):
    owned_root = artifact_io._snapshot_scratch_root_path(tmp_path)

    assert not owned_root.exists()
    assert artifact_io.cleanup_stale_snapshot_directories(
        tmp_path, min_age_seconds=0, now_ns=10, apply=False) == []
    assert not owned_root.exists()


def test_stale_snapshot_cleanup_validates_whole_tree_before_deleting(tmp_path):
    owned_root = artifact_io._snapshot_scratch_root(tmp_path)
    owned = artifact_io.ensure_private_directory(owned_root / "run-owned")
    artifact_io.atomic_write_private_json(
        owned / artifact_io._SNAPSHOT_OWNER_MARKER,
        {
            "schema_version": artifact_io._SNAPSHOT_OWNER_SCHEMA_VERSION,
            "kind": "rag_snapshot_scratch",
            "pid": 2_147_483_647,
            "process_birth": None,
            "created_ns": 1,
            "nonce": owned.name,
        },
    )
    safe = owned / "a-safe.pdf"
    linked = owned / "z-linked.pdf"
    safe.write_bytes(b"safe")
    linked.write_bytes(b"linked")
    external_link = tmp_path / "outside-link.pdf"
    try:
        os.link(linked, external_link)
    except OSError as exc:
        pytest.skip(f"hard links unavailable: {exc}")

    planned = artifact_io.cleanup_stale_snapshot_directories(
        tmp_path, min_age_seconds=0, now_ns=10, apply=False)
    removed = artifact_io.cleanup_stale_snapshot_directories(
        tmp_path, min_age_seconds=0, now_ns=10, apply=True)

    assert planned == []
    assert removed == []
    assert safe.read_bytes() == b"safe"
    assert linked.read_bytes() == b"linked"
    assert (owned / artifact_io._SNAPSHOT_OWNER_MARKER).is_file()


def test_stale_snapshot_cleanup_refuses_nested_directories_without_deleting(
        tmp_path):
    owned_root = artifact_io._snapshot_scratch_root(tmp_path)
    owned = artifact_io.ensure_private_directory(owned_root / "run-owned")
    artifact_io.atomic_write_private_json(
        owned / artifact_io._SNAPSHOT_OWNER_MARKER,
        {
            "schema_version": artifact_io._SNAPSHOT_OWNER_SCHEMA_VERSION,
            "kind": "rag_snapshot_scratch",
            "pid": 2_147_483_647,
            "process_birth": None,
            "created_ns": 1,
            "nonce": owned.name,
        },
    )
    safe = owned / "a-safe.pdf"
    safe.write_bytes(b"safe")
    nested = artifact_io.ensure_private_directory(owned / "nested")
    (nested / "outside-risk.pdf").write_bytes(b"do not traverse")

    planned = artifact_io.cleanup_stale_snapshot_directories(
        tmp_path, min_age_seconds=0, now_ns=10, apply=False)
    removed = artifact_io.cleanup_stale_snapshot_directories(
        tmp_path, min_age_seconds=0, now_ns=10, apply=True)

    assert planned == []
    assert removed == []
    assert safe.read_bytes() == b"safe"
    assert (nested / "outside-risk.pdf").read_bytes() == b"do not traverse"
    assert (owned / artifact_io._SNAPSHOT_OWNER_MARKER).is_file()


def test_stale_snapshot_cleanup_pins_root_against_scan_swap(
        monkeypatch, tmp_path):
    owned_root = artifact_io._snapshot_scratch_root(tmp_path)
    owned = artifact_io.ensure_private_directory(owned_root / "run-owned")
    marker = {
        "schema_version": artifact_io._SNAPSHOT_OWNER_SCHEMA_VERSION,
        "kind": "rag_snapshot_scratch",
        "pid": 2_147_483_647,
        "process_birth": None,
        "created_ns": 1,
        "nonce": owned.name,
    }
    artifact_io.atomic_write_private_json(
        owned / artifact_io._SNAPSHOT_OWNER_MARKER, marker)
    (owned / "sensitive.pdf").write_bytes(b"private")
    external = artifact_io.ensure_private_directory(tmp_path / "external")
    sentinel = external / "sentinel.txt"
    sentinel.write_text("must survive", encoding="utf-8")
    moved = owned_root / "run-moved"
    real_scandir = artifact_io.os.scandir
    swap_succeeded = False

    def swap_before_scan(target):
        nonlocal swap_succeeded
        if not moved.exists():
            try:
                owned.rename(moved)
            except OSError:
                pass
            else:
                swap_succeeded = True
                os.symlink(external, owned, target_is_directory=True)
        return real_scandir(target)

    monkeypatch.setattr(artifact_io.os, "scandir", swap_before_scan)

    if os.name == "nt":
        artifact_io._remove_owned_snapshot_tree(
            owned, owned_root, expected_marker=marker)
        assert not swap_succeeded
        assert not owned.exists()
    else:
        with pytest.raises(OSError, match="changed during validation"):
            artifact_io._remove_owned_snapshot_tree(
                owned, owned_root, expected_marker=marker)
        assert swap_succeeded
        assert (moved / "sensitive.pdf").read_bytes() == b"private"
    assert sentinel.read_text(encoding="utf-8") == "must survive"


def test_snapshot_pin_resolves_root_and_direct_child_together(
        monkeypatch, tmp_path):
    owned_root = artifact_io._snapshot_scratch_root(tmp_path)
    owned = artifact_io.ensure_private_directory(owned_root / "run-owned")
    canonical_root = tmp_path / "canonical-root-spelling"
    real_resolve = Path.resolve
    real_lstat = os.lstat

    def simulate_windows_alias(path, strict=False):
        absolute = Path(os.path.abspath(path))
        if absolute == owned_root:
            return canonical_root
        if absolute == owned:
            return canonical_root / owned.name
        return real_resolve(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", simulate_windows_alias)
    monkeypatch.setattr(
        artifact_io.os, "lstat",
        lambda path: real_lstat(
            owned_root if Path(path) == canonical_root
            else owned if Path(path) == canonical_root / owned.name
            else path))
    monkeypatch.setattr(
        artifact_io, "_windows_open_pinned_directory",
        lambda path: object())
    monkeypatch.setattr(
        artifact_io, "_windows_close_handle", lambda _handle: None)

    # Path validation must accept two canonicalized spellings that preserve
    # the same direct-child relationship.  Entering then fails later on POSIX
    # descriptor operations only if the helper regresses before this point.
    if os.name == "nt":
        with artifact_io._pin_snapshot_directory(owned, owned_root) as pinned:
            assert pinned.path == canonical_root / owned.name
    else:
        # On POSIX, avoid exercising fake, nonexistent canonical descriptors;
        # the shared pre-pin validation is reached before the expected open.
        with pytest.raises(FileNotFoundError):
            with artifact_io._pin_snapshot_directory(owned, owned_root):
                pass


def test_snapshot_pin_rejects_cross_device_run(monkeypatch, tmp_path):
    owned_root = artifact_io._snapshot_scratch_root(tmp_path)
    owned = artifact_io.ensure_private_directory(owned_root / "run-owned")
    real_lstat = artifact_io.os.lstat

    def cross_device(path, *args, **kwargs):
        result = real_lstat(path, *args, **kwargs)
        if Path(path) != owned:
            return result
        return SimpleNamespace(
            st_dev=result.st_dev + 1,
            st_ino=result.st_ino,
            st_mode=result.st_mode,
            st_nlink=result.st_nlink,
            st_size=result.st_size,
        )

    monkeypatch.setattr(artifact_io.os, "lstat", cross_device)

    with pytest.raises(OSError, match="device boundary"):
        with artifact_io._pin_snapshot_directory(owned, owned_root):
            pytest.fail("cross-device scratch run was pinned")


def test_posix_process_probe_treats_only_esrch_as_dead(monkeypatch):
    monkeypatch.setattr(artifact_io.os, "name", "posix")

    def fail_probe(_pid, _signal):
        raise OSError(errno.EIO, "probe I/O failure")

    monkeypatch.setattr(artifact_io.os, "kill", fail_probe)
    assert artifact_io._process_is_alive(2_147_483_647)

    def missing_probe(_pid, _signal):
        raise OSError(errno.ESRCH, "no such process")

    monkeypatch.setattr(artifact_io.os, "kill", missing_probe)
    assert not artifact_io._process_is_alive(2_147_483_647)


def test_snapshot_owner_birth_token_detects_pid_reuse(monkeypatch):
    monkeypatch.setattr(
        artifact_io, "_process_state", lambda _pid: (True, "linux:222"))

    assert artifact_io._snapshot_owner_is_alive(123, "linux:222")
    assert not artifact_io._snapshot_owner_is_alive(123, "linux:111")
    assert artifact_io._snapshot_owner_is_alive(123, None)


def _conversion_binding_for_pdf(tmp_path, pdf: Path):
    digest = hashlib.sha256(pdf.read_bytes()).hexdigest()
    return rag.ConversionSourceBinding(
        manifest_path=tmp_path / ".book.json.conversion.complete.json",
        manifest_sha256="c" * 64,
        schema_version=rag.CONVERSION_COMPLETION_SCHEMA_VERSION,
        capture_verified=True,
        source_name=pdf.name,
        source_sha256=digest,
        source_size=pdf.stat().st_size,
        effective_input_kind="original",
        effective_input_name=pdf.name,
        effective_input_sha256=digest,
        effective_input_size=pdf.stat().st_size,
    )


def _write_conversion_v2(
        source: Path, document: Path, markdown: Path,
        parameters: dict, *, preprocessed: Path | None = None) -> None:
    source_digest = hashlib.sha256(source.read_bytes()).hexdigest()
    outputs = {
        "docling_json": document,
        "docling_markdown": markdown,
    }
    effective_input = {
        "kind": "original",
        "name": source.name,
        "size": source.stat().st_size,
        "sha256": source_digest,
    }
    if preprocessed is not None:
        preprocessed_digest = hashlib.sha256(
            preprocessed.read_bytes()).hexdigest()
        outputs["preprocessed_pdf"] = preprocessed
        effective_input = {
            "kind": "preprocessed",
            "name": preprocessed.name,
            "size": preprocessed.stat().st_size,
            "sha256": preprocessed_digest,
        }
    rag._write_artifact_completion(
        rag._artifact_completion_path(document, stage="conversion"),
        stage="conversion",
        source_sha256=source_digest,
        source_name=source.name,
        source_record_count=None,
        parameters=parameters,
        outputs=outputs,
        schema_version=rag.CONVERSION_COMPLETION_SCHEMA_VERSION,
        extra_fields={
            "source": {
                "name": source.name,
                "size": source.stat().st_size,
                "sha256": source_digest,
                "capture_policy": "stream-copy-v1",
            },
            "effective_input": effective_input,
        },
    )


@pytest.mark.parametrize("mutation", ["missing", "tampered"])
def test_conversion_resume_requires_exact_preprocessed_output(
        tmp_path, mutation):
    source = tmp_path / "book.pdf"
    document = tmp_path / "book.json"
    markdown = tmp_path / "book_docling.md"
    custom_dir = tmp_path / "derived"
    custom_dir.mkdir()
    preprocessed = custom_dir / "cleaned.pdf"
    source.write_bytes(b"source-generation")
    document.write_text('{"texts":[]}', encoding="utf-8")
    markdown.write_text("complete", encoding="utf-8")
    preprocessed.write_bytes(b"preprocessed-generation")
    parameters = {"backend": "auto"}
    _write_conversion_v2(
        source, document, markdown, parameters,
        preprocessed=preprocessed)

    assert rag._converted_outputs_complete(
        source, document, markdown, parameters=parameters,
        preprocessed_output=preprocessed)

    if mutation == "missing":
        preprocessed.unlink()
    else:
        preprocessed.write_bytes(b"tampered-generation")

    assert not rag._converted_outputs_complete(
        source, document, markdown, parameters=parameters,
        preprocessed_output=preprocessed)


def test_conversion_manifest_requires_preprocessed_output_record(tmp_path):
    source = tmp_path / "book.pdf"
    document = tmp_path / "book.json"
    markdown = tmp_path / "book_docling.md"
    preprocessed = tmp_path / "book_preprocessed.pdf"
    source.write_bytes(b"source")
    document.write_text('{"texts":[]}', encoding="utf-8")
    markdown.write_text("complete", encoding="utf-8")
    preprocessed.write_bytes(b"cleaned")
    _write_conversion_v2(
        source, document, markdown, {"backend": "auto"},
        preprocessed=preprocessed)
    manifest = rag._artifact_completion_path(document, stage="conversion")
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["outputs"] = [
        record for record in payload["outputs"]
        if record["role"] != "preprocessed_pdf"
    ]
    rag._atomic_write_json(manifest, payload)

    raw = document.read_bytes()
    with pytest.raises(ValueError, match="output records"):
        rag._load_conversion_source_binding(
            document,
            document_sha256=hashlib.sha256(raw).hexdigest(),
            document_size=len(raw),
        )


def test_conversion_resume_rechecks_source_after_output_validation(
        monkeypatch, tmp_path):
    source = tmp_path / "book.pdf"
    document = tmp_path / "book.json"
    markdown = tmp_path / "book_docling.md"
    source.write_bytes(b"source-A")
    document.write_text('{"texts":[]}', encoding="utf-8")
    markdown.write_text("complete", encoding="utf-8")
    parameters = {"backend": "auto"}
    _write_conversion_v2(source, document, markdown, parameters)
    real_read = rag._read_index_artifact_snapshot
    swapped = False

    def replace_source_after_document_read(path, **kwargs):
        nonlocal swapped
        result = real_read(path, **kwargs)
        if Path(path) == document and not swapped:
            swapped = True
            source.write_bytes(b"source-B")
        return result

    monkeypatch.setattr(
        rag, "_read_index_artifact_snapshot",
        replace_source_after_document_read,
    )

    assert not rag._converted_outputs_complete(
        source, document, markdown, parameters=parameters)


def test_conversion_manifest_rejects_unhashable_output_role(tmp_path):
    source = tmp_path / "book.pdf"
    document = tmp_path / "book.json"
    markdown = tmp_path / "book_docling.md"
    source.write_bytes(b"source")
    document.write_text('{"texts":[]}', encoding="utf-8")
    markdown.write_text("complete", encoding="utf-8")
    _write_conversion_v2(source, document, markdown, {"backend": "auto"})
    manifest = rag._artifact_completion_path(document, stage="conversion")
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["outputs"][0]["role"] = []
    rag._atomic_write_json(manifest, payload)

    with pytest.raises(ValueError, match="output records"):
        rag._load_conversion_source_binding(
            document,
            document_sha256=hashlib.sha256(document.read_bytes()).hexdigest(),
            document_size=document.stat().st_size,
        )


def test_recovery_discards_candidates_when_snapshot_exit_verification_fails(
        monkeypatch, tmp_path):
    private_pdf = tmp_path / "private.pdf"
    private_pdf.write_bytes(b"source")
    fake_source = SimpleNamespace(
        pdf=SimpleNamespace(path=private_pdf),
        manifest_input=lambda: {"provenance": "candidate"},
    )

    @contextmanager
    def failing_snapshot(*_args, **_kwargs):
        yield fake_source
        raise RuntimeError("exit verification failed")

    monkeypatch.setattr(
        rag, "_open_docling_source_pdf_snapshot", failing_snapshot)
    monkeypatch.setattr(
        rag, "_recover_incomplete_table_markdown",
        lambda *_args: {"#/tables/0": "unverified text"},
    )

    assert rag._recover_bound_table_markdown(
        object(), tmp_path / "book.json", None) == ({}, None)


def test_explicit_source_pdf_mismatch_is_fatal(monkeypatch, tmp_path):
    expected = tmp_path / "book.pdf"
    expected.write_bytes(b"expected")
    wrong = tmp_path / "wrong.pdf"
    wrong.write_bytes(b"wrong")
    binding = _conversion_binding_for_pdf(tmp_path, expected)
    monkeypatch.setattr(
        rag, "_recover_incomplete_table_markdown",
        lambda *_args: pytest.fail("mismatched PDF must not reach recovery"),
    )

    with pytest.raises(RuntimeError, match="Explicit source PDF"):
        rag._recover_bound_table_markdown(
            object(), tmp_path / "book.json", binding,
            source_pdf_path=wrong,
        )


def test_explicit_recovery_exposes_only_private_snapshot_path(
        monkeypatch, tmp_path):
    source = tmp_path / "book.pdf"
    source.write_bytes(b"expected")
    binding = _conversion_binding_for_pdf(tmp_path, source)
    observed = {}

    def recover(_document, pdf_path):
        observed["path"] = pdf_path
        observed["bytes"] = pdf_path.read_bytes()
        return {"#/tables/0": "recovered"}

    monkeypatch.setattr(rag, "_recover_incomplete_table_markdown", recover)

    overrides, evidence = rag._recover_bound_table_markdown(
        object(), tmp_path / "book.json", binding,
        source_pdf_path=source,
    )

    assert overrides == {"#/tables/0": "recovered"}
    assert observed["path"] != source
    assert observed["bytes"] == b"expected"
    assert not observed["path"].exists()
    assert evidence["pdf"]["sha256"] == binding.source_sha256
    assert evidence["discovery"] == "explicit"


def test_inferred_recovery_rejects_output_alias_to_source_pdf(
        monkeypatch, tmp_path):
    source = tmp_path / "book.pdf"
    source.write_bytes(b"must remain a PDF")
    binding = _conversion_binding_for_pdf(tmp_path, source)
    monkeypatch.setattr(
        rag, "_recover_incomplete_table_markdown",
        lambda *_args: pytest.fail("aliased PDF reached recovery"),
    )

    with pytest.raises(rag._SourceOutputAliasError, match="distinct files"):
        rag._recover_bound_table_markdown(
            object(), tmp_path / "book.json", binding,
            forbidden_output_paths={"chunks JSONL": source},
        )

    assert source.read_bytes() == b"must remain a PDF"


def _write_chunk_v3(document: Path, chunks: Path, parameters: dict) -> dict:
    receipt = rag._document_profiles.profile_provenance(
        rag._document_profiles.get_profile(rag.DEFAULT_STRUCTURE_PROFILE))
    parameters.setdefault("structure_profile", receipt)
    document_raw = document.read_bytes()
    inputs = {
        "docling_json": {
            "name": document.name,
            "size": len(document_raw),
            "sha256": hashlib.sha256(document_raw).hexdigest(),
        },
        "conversion_manifest": None,
        "table_recovery": None,
    }
    rag._write_artifact_completion(
        rag._artifact_completion_path(chunks, stage="chunking"),
        stage="chunking",
        source_sha256=inputs["docling_json"]["sha256"],
        source_record_count=None,
        parameters=parameters,
        outputs={"chunks_jsonl": chunks},
        schema_version=rag.CHUNK_COMPLETION_SCHEMA_VERSION,
        extra_fields={
            "inputs": inputs,
            "structure_profile": receipt,
            "structure_profile_parameters_sha256": (
                rag._structure_profile_parameters_binding(
                    rag._artifact_parameters_sha256(parameters), receipt)),
        },
    )
    return inputs


def test_chunk_v3_rejects_tampered_inputs_and_unbound_recovery(tmp_path):
    document = tmp_path / "book.json"
    chunks = tmp_path / "book_chunks.jsonl"
    document.write_text('{"texts":[]}', encoding="utf-8")
    record = {"text": "source", "metadata": {"chunk_index": 0}}
    rag._atomic_write_jsonl(chunks, [record])
    parameters = {"embedding_model": "model-a"}
    _write_chunk_v3(document, chunks, parameters)
    assert rag._chunks_complete(document, chunks, parameters=parameters)

    manifest_path = rag._artifact_completion_path(chunks, stage="chunking")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["inputs"]["docling_json"]["sha256"] = "f" * 64
    rag._atomic_write_json(manifest_path, payload)
    assert not rag._chunks_complete(document, chunks, parameters=parameters)

    record["metadata"]["table_recovered_from_pdf"] = True
    rag._atomic_write_jsonl(chunks, [record])
    _write_chunk_v3(document, chunks, parameters)
    assert not rag._chunks_complete(document, chunks, parameters=parameters)


def _quality_fixture(tmp_path):
    document = tmp_path / "book.json"
    chunks = tmp_path / "book_chunks.jsonl"
    mapping = {
        "pages": {"1": {}},
        "texts": [{
            "self_ref": "#/texts/0",
            "label": "text",
            "content_layer": "body",
            "text": "Source-backed professional responsibility discussion.",
            "prov": [{"page_no": 1}],
        }],
    }
    rag._atomic_write_json(document, mapping)
    record = {
        "text": "Source-backed professional responsibility discussion.",
        "metadata": {
            "chunk_index": 0,
            "source_file": "book",
            "source_lineage_schema_version": 1,
            "source_items": [{
                "ref": "#/texts/0", "label": "text",
                "parent_refs": [], "spans": [{"page": 1}],
            }],
            "page_start": 1, "page_end": 1,
            "chapter_num": 1, "content_type": "author_narrative",
            "content_source": "body", "token_count": 5,
            "embedding_token_count": 5, "case_names": [],
            "primary_case": None,
        },
    }
    rag._retrieval_core._attach_retrieval_linkage([record])
    rag._atomic_write_jsonl(chunks, [record])
    parameters = {"embedding_model": "model-a"}
    inputs = _write_chunk_v3(document, chunks, parameters)
    raw = document.read_bytes()
    return (
        document, chunks, parameters, inputs, mapping,
        hashlib.sha256(raw).hexdigest(), len(raw),
    )


def test_inline_quality_never_rereads_a_replaced_docling_generation(
        monkeypatch, tmp_path):
    (document, chunks, parameters, inputs, mapping,
     digest_a, size_a) = _quality_fixture(tmp_path)
    rag._atomic_write_json(document, {"texts": [], "generation": "B"})
    real_read = rag._read_index_artifact_snapshot

    def refuse_docling_reread(path, **kwargs):
        if Path(path) == document:
            raise AssertionError("inline quality reread the Docling pathname")
        return real_read(path, **kwargs)

    monkeypatch.setattr(rag, "_read_index_artifact_snapshot", refuse_docling_reread)

    with pytest.raises(RuntimeError, match="Source or chunks changed"):
        rag._publish_corpus_quality_report(
            document, chunks,
            parameters=parameters,
            structural_ranges=set(),
            document_snapshot=(mapping, digest_a, size_a),
            chunk_inputs=inputs,
        )

    report = json.loads(
        rag._quality_core.quality_report_path(chunks).read_text(
            encoding="utf-8"))
    assert report["source"]["docling_json"]["sha256"] == digest_a


def test_index_quality_snapshot_holds_artifact_set_lease(
        monkeypatch, tmp_path):
    (document, chunks, parameters, inputs, mapping,
     document_sha256, document_size) = _quality_fixture(tmp_path)
    rag._publish_corpus_quality_report(
        document,
        chunks,
        parameters=parameters,
        structural_ranges=set(),
        document_snapshot=(mapping, document_sha256, document_size),
        chunk_inputs=inputs,
    )
    real_read = rag._read_index_artifact_snapshot
    chunks_read = threading.Event()
    release_reader = threading.Event()
    writer_entered = threading.Event()

    def block_after_chunks_read(path, **kwargs):
        result = real_read(path, **kwargs)
        if Path(path) == chunks:
            chunks_read.set()
            if not release_reader.wait(3):
                raise AssertionError("test did not release index reader")
        return result

    def take_writer_lease():
        with rag._chunk_output_lease(chunks):
            writer_entered.set()

    monkeypatch.setattr(
        rag, "_read_index_artifact_snapshot", block_after_chunks_read)
    with ThreadPoolExecutor(max_workers=2) as pool:
        reader = pool.submit(rag._load_index_snapshot_with_quality, chunks)
        assert chunks_read.wait(3)
        writer = pool.submit(take_writer_lease)
        assert not writer_entered.wait(0.1)
        release_reader.set()
        reader.result(timeout=3)
        writer.result(timeout=3)
    assert writer_entered.is_set()


def test_index_quality_snapshot_bounds_report_read(monkeypatch, tmp_path):
    (document, chunks, parameters, inputs, mapping,
     document_sha256, document_size) = _quality_fixture(tmp_path)
    rag._publish_corpus_quality_report(
        document,
        chunks,
        parameters=parameters,
        structural_ranges=set(),
        document_snapshot=(mapping, document_sha256, document_size),
        chunk_inputs=inputs,
    )
    report_path = rag._quality_core.quality_report_path(chunks)
    real_read = rag._read_index_artifact_snapshot
    observed_limits = []

    def observe_report_limit(path, **kwargs):
        if Path(path) == report_path:
            observed_limits.append(kwargs.get("max_bytes"))
        return real_read(path, **kwargs)

    monkeypatch.setattr(
        rag, "_read_index_artifact_snapshot", observe_report_limit)
    rag._load_index_snapshot_with_quality(chunks)

    assert observed_limits == [rag._quality_core.MAX_QUALITY_REPORT_BYTES]


def test_index_quality_requires_exact_chunk_completion_inputs(tmp_path):
    (document, chunks, parameters, _inputs, mapping,
     document_sha256, document_size) = _quality_fixture(tmp_path)
    source = tmp_path / "book.pdf"
    markdown = tmp_path / "book_docling.md"
    source.write_bytes(b"captured PDF generation")
    markdown.write_text("converted markdown", encoding="utf-8")
    _write_conversion_v2(
        source, document, markdown, {"backend": "pypdfium2"})

    conversion = rag._load_conversion_source_binding(
        document,
        document_sha256=document_sha256,
        document_size=document_size,
    )
    assert conversion is not None
    conversion_input = {
        "name": conversion.manifest_path.name,
        "sha256": conversion.manifest_sha256,
        "schema_version": conversion.schema_version,
    }
    bound_inputs = {
        "docling_json": {
            "name": document.name,
            "size": document_size,
            "sha256": document_sha256,
        },
        "conversion_manifest": conversion_input,
        # The PDF was inspected, but this fixture intentionally has no
        # recovered tables. The binding must still survive index validation.
        "table_recovery": {
            "pdf": {
                "name": source.name,
                "size": source.stat().st_size,
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "capture_policy": "stream-copy-v1",
            },
            "conversion_manifest": conversion_input,
            "discovery": "explicit",
        },
    }
    rag._write_artifact_completion(
        rag._artifact_completion_path(chunks, stage="chunking"),
        stage="chunking",
        source_sha256=document_sha256,
        source_record_count=None,
        parameters=parameters,
        outputs={"chunks_jsonl": chunks},
        schema_version=rag.CHUNK_COMPLETION_SCHEMA_VERSION,
        extra_fields={
            "inputs": bound_inputs,
            "structure_profile": parameters["structure_profile"],
            "structure_profile_parameters_sha256": (
                rag._structure_profile_parameters_binding(
                    rag._artifact_parameters_sha256(parameters),
                    parameters["structure_profile"])),
        },
    )
    report = rag._publish_corpus_quality_report(
        document,
        chunks,
        parameters=parameters,
        structural_ranges=set(),
        document_snapshot=(mapping, document_sha256, document_size),
        chunk_inputs=bound_inputs,
    )
    assert report["tables"]["recovered_from_pdf"] == 0
    rag._load_index_snapshot_with_quality(chunks)

    report_path = rag._quality_core.quality_report_path(chunks)
    stripped = json.loads(report_path.read_text(encoding="utf-8"))
    stripped["inputs"]["conversion_manifest"] = None
    stripped["inputs"]["table_recovery"] = None
    rag._atomic_write_json(report_path, stripped)

    with pytest.raises(ValueError, match="input bindings"):
        rag._load_index_snapshot_with_quality(chunks)

    wrong_parameters = json.loads(json.dumps(report))
    wrong_parameters["parameters_sha256"] = (
        "f" * 64
        if report["parameters_sha256"] != "f" * 64 else "e" * 64)
    rag._atomic_write_json(report_path, wrong_parameters)

    with pytest.raises(ValueError, match="parameters"):
        rag._load_index_snapshot_with_quality(chunks)


def test_pdf_hashing_never_uses_unbounded_artifact_snapshot(
        monkeypatch, tmp_path):
    source = tmp_path / "book.pdf"
    source.write_bytes(b"pdf generation")

    def reject_pdf(path, **_kwargs):
        if Path(path).suffix.lower() == ".pdf":
            raise AssertionError("PDF routed through whole-file artifact read")
        raise AssertionError("unexpected artifact read")

    monkeypatch.setattr(rag, "_read_index_artifact_snapshot", reject_pdf)
    assert rag._cached_artifact_sha256(source) == hashlib.sha256(
        b"pdf generation").hexdigest()


@pytest.mark.parametrize("operation", ["conversion", "chunking"])
def test_artifact_publication_wrappers_serialize_concurrent_writers(
        monkeypatch, tmp_path, operation):
    first_entered = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()
    call_count = 0
    count_lock = threading.Lock()

    def coordinated_writer(*_args, **_kwargs):
        nonlocal call_count
        with count_lock:
            call_count += 1
            position = call_count
        if position == 1:
            first_entered.set()
            assert release_first.wait(2)
        else:
            second_entered.set()

    if operation == "conversion":
        source = tmp_path / "book.pdf"
        source.write_bytes(b"pdf")
        output = tmp_path / "book.json"
        monkeypatch.setattr(rag, "_convert_pdf_locked", coordinated_writer)

        def invoke():
            return rag.convert_pdf(source, output)
    else:
        document = tmp_path / "book.json"
        output = tmp_path / "book_chunks.jsonl"
        monkeypatch.setattr(rag, "_chunk_document_locked", coordinated_writer)

        def invoke():
            return rag.chunk_document(document, output)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(invoke)
        assert first_entered.wait(2)
        second = pool.submit(invoke)
        assert not second_entered.wait(0.1)
        release_first.set()
        first.result(timeout=2)
        second.result(timeout=2)

    assert second_entered.is_set()


def test_conversion_completion_reader_holds_output_set_lease(
        monkeypatch, tmp_path):
    source = tmp_path / "book.pdf"
    document = tmp_path / "book.json"
    markdown = tmp_path / "book_docling.md"
    preprocessed = tmp_path / "book_preprocessed.pdf"
    completion = rag._artifact_completion_path(
        document, stage="conversion")
    writer_entered = threading.Event()
    release_writer = threading.Event()
    reader_entered = threading.Event()

    def hold_writer_lease():
        with rag._conversion_output_lease(
                document, markdown, preprocessed, completion):
            writer_entered.set()
            assert release_writer.wait(3)

    def observe_reader(*_args, **_kwargs):
        reader_entered.set()
        return False

    monkeypatch.setattr(
        rag, "_converted_outputs_complete_locked", observe_reader)
    with ThreadPoolExecutor(max_workers=2) as pool:
        writer = pool.submit(hold_writer_lease)
        assert writer_entered.wait(3)
        reader = pool.submit(
            rag._converted_outputs_complete,
            source,
            document,
            markdown,
            parameters={},
            preprocessed_output=preprocessed,
        )
        assert not reader_entered.wait(0.1)
        release_writer.set()
        writer.result(timeout=3)
        assert reader.result(timeout=3) is False
    assert reader_entered.is_set()


def test_conversion_rejects_source_output_alias_before_writing(
        monkeypatch, tmp_path):
    source = tmp_path / "book.pdf"
    source.write_bytes(b"original")
    monkeypatch.setattr(
        rag, "_convert_pdf_locked",
        lambda *_args, **_kwargs: pytest.fail("aliased output reached writer"),
    )

    with pytest.raises(ValueError, match="distinct files"):
        rag.convert_pdf(source, source)

    assert source.read_bytes() == b"original"


def test_conversion_rejects_hard_linked_output_before_writing(
        monkeypatch, tmp_path):
    source = tmp_path / "book.pdf"
    source.write_bytes(b"original")
    output = tmp_path / "book.json"
    try:
        os.link(source, output)
    except OSError as exc:
        pytest.skip(f"hard links unavailable: {exc}")
    monkeypatch.setattr(
        rag, "_convert_pdf_locked",
        lambda *_args, **_kwargs: pytest.fail("aliased output reached writer"),
    )

    with pytest.raises(ValueError, match="distinct files"):
        rag.convert_pdf(source, output)

    assert source.read_bytes() == b"original"


@pytest.mark.parametrize("alias", ["document", "source_pdf", "conversion"])
def test_chunking_rejects_input_output_alias_before_writing(
        monkeypatch, tmp_path, alias):
    document = tmp_path / "book.json"
    document.write_text('{"texts":[]}', encoding="utf-8")
    source_pdf = tmp_path / "book.pdf"
    source_pdf.write_bytes(b"original PDF")
    chunks = tmp_path / "book_chunks.jsonl"
    if alias == "document":
        chunks = document
    elif alias == "source_pdf":
        chunks = source_pdf
    else:
        chunks = rag._artifact_completion_path(
            document, stage="conversion")
    monkeypatch.setattr(
        rag, "_chunk_document_locked",
        lambda *_args, **_kwargs: pytest.fail("aliased output reached writer"),
    )

    with pytest.raises(ValueError, match="distinct files"):
        rag.chunk_document(
            document, chunks,
            source_pdf_path=source_pdf if alias == "source_pdf" else None,
        )

    assert document.read_text(encoding="utf-8") == '{"texts":[]}'
    assert source_pdf.read_bytes() == b"original PDF"
