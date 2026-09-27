"""Portable characterization and bounded Windows snapshot verification."""

import hashlib
import io
from types import SimpleNamespace

import pytest

import artifact_io


class _Reader:
    def __init__(self, raw, verification=None, *, short_read=None):
        self.initial = io.BytesIO(raw)
        self.verification = io.BytesIO(raw if verification is None else verification)
        self.short_read = short_read
        self.verifying = False
        self.reads = []
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def fileno(self):
        return 17

    def seek(self, offset):
        assert offset == 0 and not self.verifying
        self.verifying = True
        return 0

    def read(self, size=-1):
        self.reads.append((self.verifying, size))
        if self.verifying and self.short_read is not None:
            size = min(size, self.short_read)
        return (self.verification if self.verifying else self.initial).read(size)


def _install(monkeypatch, tmp_path, readers, *, declared_size, ctimes=None, platform="nt"):
    path = tmp_path / "synthetic-artifact.bin"
    pending = iter(readers)
    stats = []
    sleeps = []
    ctimes = iter(ctimes) if ctimes is not None else None

    def fake_open(actual, mode):
        assert actual == path and mode == "rb"
        return next(pending)

    def fake_stat(descriptor):
        assert descriptor == 17
        result = SimpleNamespace(st_dev=3, st_ino=7, st_size=declared_size,
                                 st_mtime_ns=11, st_ctime_ns=next(ctimes) if ctimes else 13)
        stats.append(result)
        return result

    monkeypatch.setattr(type(path), "open", fake_open)
    # Replace only this module's OS capability: do not mutate global os.name,
    # which would change pathlib's platform selection in the test runner.
    monkeypatch.setattr(artifact_io, "os", SimpleNamespace(name=platform, fstat=fake_stat))
    monkeypatch.setattr(artifact_io.time, "sleep", sleeps.append)
    return path, stats, sleeps


@pytest.mark.parametrize("raw,limit", [(b"", None), (b"", 0), (b"data", None), (b"data", 4), (b"data", 8)])
def test_snapshot_success_preserves_bytes_digest_fingerprint(monkeypatch, tmp_path, raw, limit):
    reader = _Reader(raw)
    path, stats, sleeps = _install(monkeypatch, tmp_path, [reader], declared_size=len(raw))

    result = artifact_io._read_index_artifact_snapshot(path, max_bytes=limit)

    assert result == (raw, hashlib.sha256(raw).hexdigest(), (3, 7, len(raw), 11, 13))
    assert reader.reads[0] == (False, -1 if limit is None else limit + 1)
    assert len(stats) == 2 and sleeps == [] and reader.closed


@pytest.mark.parametrize("verification", [b"diff", b"dat", b"data plus growth"])
def test_snapshot_rejects_same_size_mutation_shrink_and_growth_without_retry(
        monkeypatch, tmp_path, verification):
    reader = _Reader(b"data", verification)
    path, stats, sleeps = _install(monkeypatch, tmp_path, [reader], declared_size=4)

    with pytest.raises(RuntimeError, match="Artifact changed while it was being read"):
        artifact_io._read_index_artifact_snapshot(path, max_bytes=4)

    assert len(stats) == 1 and sleeps == [] and reader.closed


def test_snapshot_ctime_only_retry_retains_original_content_identity(monkeypatch, tmp_path):
    readers = [_Reader(b"data"), _Reader(b"data")]
    path, stats, sleeps = _install(monkeypatch, tmp_path, readers, declared_size=4,
                                  ctimes=[13, 14, 15, 15])

    result = artifact_io._read_index_artifact_snapshot(path, max_bytes=4)

    assert result == (b"data", hashlib.sha256(b"data").hexdigest(), (3, 7, 4, 11, 15))
    assert len(stats) == 4
    assert sleeps == [artifact_io._SYNCED_FOLDER_READ_RETRY_DELAYS[0]]
    assert all(reader.closed for reader in readers)


def test_snapshot_changed_bytes_during_ctime_retry_fail(monkeypatch, tmp_path):
    readers = [_Reader(b"data"), _Reader(b"diff")]
    path, stats, sleeps = _install(monkeypatch, tmp_path, readers, declared_size=4,
                                  ctimes=[13, 14, 15, 15])

    with pytest.raises(RuntimeError, match="Artifact changed while it was being read"):
        artifact_io._read_index_artifact_snapshot(path)

    assert len(stats) == 4
    assert sleeps == [artifact_io._SYNCED_FOLDER_READ_RETRY_DELAYS[0]]
    assert all(reader.closed for reader in readers)


def test_snapshot_negative_limit_rejected_before_open(monkeypatch, tmp_path):
    path, stats, sleeps = _install(monkeypatch, tmp_path, [], declared_size=0)

    with pytest.raises(ValueError, match="max_bytes cannot be negative"):
        artifact_io._read_index_artifact_snapshot(path, max_bytes=-1)

    assert stats == [] and sleeps == []


def test_snapshot_stat_over_limit_rejected_before_read(monkeypatch, tmp_path):
    reader = _Reader(b"data")
    path, stats, sleeps = _install(monkeypatch, tmp_path, [reader], declared_size=4)

    with pytest.raises(ValueError, match="Artifact exceeds 3 bytes"):
        artifact_io._read_index_artifact_snapshot(path, max_bytes=3)

    assert reader.reads == [] and len(stats) == 1 and sleeps == [] and reader.closed


@pytest.mark.parametrize("platform", ["nt", "posix"])
def test_snapshot_first_read_over_limit_raises_size_error(monkeypatch, tmp_path, platform):
    reader = _Reader(b"data")
    path, _, sleeps = _install(monkeypatch, tmp_path, [reader], declared_size=3, platform=platform)

    with pytest.raises(ValueError, match="Artifact exceeds 3 bytes"):
        artifact_io._read_index_artifact_snapshot(path, max_bytes=3)

    assert reader.reads[0] == (False, 4)
    assert sleeps == [] and reader.closed


def test_snapshot_posix_preserves_single_unlimited_read(monkeypatch, tmp_path):
    reader = _Reader(b"data")
    path, _, _ = _install(monkeypatch, tmp_path, [reader], declared_size=4, platform="posix")

    assert artifact_io._read_index_artifact_snapshot(path)[0] == b"data"
    assert reader.reads == [(False, -1)]


def test_snapshot_windows_verification_accepts_short_reads(monkeypatch, tmp_path):
    reader = _Reader(b"0123456789", short_read=2)
    path, _, _ = _install(monkeypatch, tmp_path, [reader], declared_size=10)
    monkeypatch.setattr(artifact_io, "_FILE_STREAM_CHUNK_SIZE", 4)

    assert artifact_io._read_index_artifact_snapshot(path, max_bytes=10)[0] == b"0123456789"
    assert reader.closed


@pytest.mark.parametrize("raw,limit", [(b"", None), (b"", 0), (b"data", None), (b"data", 4), (b"data", 100)])
@pytest.mark.parametrize("short_read", [None, 2])
def test_snapshot_growing_verification_stops_at_captured_length_plus_one(
        monkeypatch, tmp_path, raw, limit, short_read):
    # This reader has a finite 128-byte growth tail. The old implementation
    # drains it; the hardened verification may inspect only one excess byte.
    reader = _Reader(raw, raw + b"x" * 128, short_read=short_read)
    path, stats, sleeps = _install(monkeypatch, tmp_path, [reader], declared_size=len(raw))
    monkeypatch.setattr(artifact_io, "_FILE_STREAM_CHUNK_SIZE", 4)

    with pytest.raises(RuntimeError, match="Artifact changed while it was being read"):
        artifact_io._read_index_artifact_snapshot(path, max_bytes=limit)

    assert reader.verification.tell() == len(raw) + 1
    assert all(0 < size <= min(4, len(raw) + 1) for verifying, size in reader.reads if verifying)
    assert len(stats) == 1 and sleeps == [] and reader.closed


@pytest.mark.parametrize("verification", [b"data", b"different"])
def test_snapshot_first_read_over_limit_never_enters_verification(
        monkeypatch, tmp_path, verification):
    reader = _Reader(b"data", verification)
    path, stats, sleeps = _install(monkeypatch, tmp_path, [reader], declared_size=3)

    with pytest.raises(ValueError, match="Artifact exceeds 3 bytes"):
        artifact_io._read_index_artifact_snapshot(path, max_bytes=3)

    assert reader.reads == [(False, 4)]
    assert not reader.verifying
    assert len(stats) == 1 and sleeps == [] and reader.closed
