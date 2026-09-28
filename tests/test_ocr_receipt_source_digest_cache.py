"""Executed-source digests are reused only for unchanged, settled files.

Every OCR execution receipt digests each loaded project module.  The content
read is reused while a file's ``lstat`` identity is unchanged and its last
modification is older than the racy window; the link-component refusal still
runs on every call.  All files here are synthetic.
"""

import hashlib
import os
import time

import pytest

import ocr_execution_receipt as receipt


@pytest.fixture
def reads(monkeypatch):
    monkeypatch.setattr(receipt, "_SOURCE_DIGESTS", {})
    calls = []
    real = receipt._read_snapshot

    def counting(path, **kwargs):
        calls.append(path)
        return real(path, **kwargs)

    monkeypatch.setattr(receipt, "_read_snapshot", counting)
    return calls


def settled(path, text):
    path.write_bytes(text.encode("utf-8"))
    old = time.time_ns() - 60 * 1_000_000_000
    os.utime(path, ns=(old, old))
    return path


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_a_settled_unchanged_file_is_read_once(tmp_path, reads):
    source = settled(tmp_path / "module.py", "VALUE = 1\n")

    first = receipt._executed_source_digest(source)
    second = receipt._executed_source_digest(source)

    assert first == second == sha256(source)
    assert reads == [source]


def test_a_rewrite_is_read_again(tmp_path, reads):
    source = settled(tmp_path / "module.py", "VALUE = 1\n")
    receipt._executed_source_digest(source)

    settled(source, "VALUE = 22\n")

    assert receipt._executed_source_digest(source) == sha256(source)
    assert len(reads) == 2


def test_a_same_size_rewrite_with_a_new_mtime_is_read_again(tmp_path, reads):
    source = settled(tmp_path / "module.py", "VALUE = 1\n")
    receipt._executed_source_digest(source)
    status = os.stat(source)

    source.write_bytes(b"VALUE = 2\n")
    later = status.st_mtime_ns + 1_000_000_000
    os.utime(source, ns=(later, later))

    assert receipt._executed_source_digest(source) == sha256(source)
    assert len(reads) == 2


def test_a_recently_modified_file_is_never_cached(tmp_path, reads):
    source = tmp_path / "module.py"
    source.write_bytes(b"VALUE = 1\n")

    receipt._executed_source_digest(source)
    receipt._executed_source_digest(source)

    assert len(reads) == 2
    assert source not in receipt._SOURCE_DIGESTS


def test_a_change_during_the_read_is_not_cached(tmp_path, reads, monkeypatch):
    source = settled(tmp_path / "module.py", "VALUE = 1\n")
    real = receipt._source_identity
    observed = []

    def shifting(path):
        identity = real(path)
        observed.append(identity)
        if len(observed) == 2:
            return identity[:2] + (identity[2] + 1,) + identity[3:]
        return identity

    monkeypatch.setattr(receipt, "_source_identity", shifting)

    receipt._executed_source_digest(source)

    assert source not in receipt._SOURCE_DIGESTS


def test_link_components_are_refused_even_after_caching(
        tmp_path, reads, monkeypatch):
    source = settled(tmp_path / "module.py", "VALUE = 1\n")
    receipt._executed_source_digest(source)
    checked = []

    def refuse(path):
        checked.append(path)
        raise ValueError("synthetic link component")

    monkeypatch.setattr(receipt.storage_policy, "assert_no_link_components",
                        refuse)

    with pytest.raises(ValueError, match="synthetic link component"):
        receipt._executed_source_digest(source)
    assert checked == [source]
    assert reads == [source]


def test_a_deleted_file_fails_even_when_cached(tmp_path, reads):
    source = settled(tmp_path / "module.py", "VALUE = 1\n")
    receipt._executed_source_digest(source)
    source.unlink()

    with pytest.raises(OSError):
        receipt._executed_source_digest(source)


def test_loaded_source_digests_match_the_files_on_disk(monkeypatch):
    monkeypatch.setattr(receipt, "_SOURCE_DIGESTS", {})

    first = receipt._loaded_source_digests()
    second = receipt._loaded_source_digests()

    assert first == second
    assert first["ocr_execution_receipt"] == hashlib.sha256(
        receipt.ROOT.joinpath("ocr_execution_receipt.py").read_bytes()
    ).hexdigest()
