import errno
import json
import os
import stat
from pathlib import Path

import pytest

import storage_policy


def _synced_folder_lock_error():
    error = PermissionError(errno.EACCES, "temporarily locked")
    error.winerror = 32
    return error


def _assert_private_permissions(path: Path, *, directory: bool) -> None:
    if os.name == "nt":
        assert storage_policy.windows_path_is_private(
            path, directory=directory
        )
        return

    expected = (
        storage_policy.PRIVATE_DIRECTORY_MODE
        if directory
        else storage_policy.PRIVATE_FILE_MODE
    )
    assert stat.S_IMODE(path.stat().st_mode) == expected


def _make_directory_link(link: Path, target: Path) -> None:
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
        return
    link.symlink_to(target, target_is_directory=True)


def test_atomic_text_json_and_jsonl_writers_publish_private_files(tmp_path):
    private = storage_policy.ensure_private_directory(tmp_path / "private")
    text_path = private / "note.txt"
    json_path = private / "payload.json"
    jsonl_path = private / "records.jsonl"

    storage_policy.atomic_write_private_text(text_path, "sensitive ✓\n")
    storage_policy.atomic_write_private_json(
        json_path, {"z": 1, "a": "café"}
    )
    storage_policy.atomic_write_private_jsonl(
        jsonl_path, ({"index": index} for index in range(2)), compact=True
    )
    storage_policy.append_private_jsonl(jsonl_path, {"index": 2})

    assert text_path.read_text(encoding="utf-8") == "sensitive ✓\n"
    assert json_path.read_text(encoding="utf-8") == '{"a":"café","z":1}'
    assert [
        json.loads(line)
        for line in jsonl_path.read_text(encoding="utf-8").splitlines()
    ] == [{"index": 0}, {"index": 1}, {"index": 2}]
    _assert_private_permissions(private, directory=True)
    for path in (text_path, json_path, jsonl_path):
        _assert_private_permissions(path, directory=False)


def test_atomic_private_text_preserves_canonical_lf_bytes(tmp_path):
    private = storage_policy.ensure_private_directory(tmp_path / "private")
    target = private / "canonical.txt"

    storage_policy.atomic_write_private_text(target, "first\nsecond\n")

    assert target.read_bytes() == b"first\nsecond\n"


def test_jsonl_lines_splits_only_on_written_terminators():
    assert storage_policy.jsonl_lines("a\nb\n") == ["a", "b", ""]
    assert storage_policy.jsonl_lines("a\r\nb\r\n") == ["a", "b", ""]
    assert storage_policy.jsonl_lines("") == [""]

    for code_point in (0x85, 0x2028, 0x2029):
        record = f"before{chr(code_point)}after"
        assert storage_policy.jsonl_lines(record) == [record]
        assert len(record.splitlines()) == 2


def test_private_append_handle_streams_bytes_and_text(tmp_path):
    private = storage_policy.ensure_private_directory(tmp_path / "private")
    binary_path = private / "worker.log"
    text_path = private / "manager.log"

    with storage_policy.open_private_append(binary_path) as handle:
        handle.write(b"first\n")
    with storage_policy.open_private_append(binary_path) as handle:
        handle.write(b"second\n")
    with storage_policy.open_private_append(text_path, text=True) as handle:
        handle.write("private ✓\n")

    assert binary_path.read_bytes() == b"first\nsecond\n"
    assert text_path.read_text(encoding="utf-8") == "private ✓\n"
    _assert_private_permissions(binary_path, directory=False)
    _assert_private_permissions(text_path, directory=False)


def test_private_append_handle_rejects_hardlinked_target(tmp_path):
    private = storage_policy.ensure_private_directory(tmp_path / "private")
    source = private / "source.log"
    alias = private / "alias.log"
    storage_policy.atomic_write_private_text(source, "unchanged")
    os.link(source, alias)

    with pytest.raises(storage_policy.StoragePolicyError, match="unlinked"):
        with storage_policy.open_private_append(alias):
            pytest.fail("hardlinked stream target must not be returned")

    assert source.read_text(encoding="utf-8") == "unchanged"


def test_atomic_replace_failure_preserves_target_and_removes_temporary_file(
    tmp_path,
):
    private = storage_policy.ensure_private_directory(tmp_path / "private")
    target = private / "artifact.json"
    storage_policy.atomic_write_private_text(target, "old")
    observed = {}

    def fail_replace(source, destination):
        observed["source"] = Path(source)
        observed["destination"] = Path(destination)
        raise OSError("injected replace failure")

    with pytest.raises(OSError, match="injected replace failure"):
        storage_policy.atomic_write_private_text(
            target, "new", replace_fn=fail_replace
        )

    assert observed["destination"] == target
    assert target.read_text(encoding="utf-8") == "old"
    assert not observed["source"].exists()
    assert list(private.iterdir()) == [target]


def test_atomic_replace_retries_transient_synced_folder_interference(
        monkeypatch, tmp_path):
    private = storage_policy.ensure_private_directory(tmp_path / "private")
    target = private / "artifact.json"
    storage_policy.atomic_write_private_text(target, "old")
    attempts = []
    delays = []
    writer_calls = 0

    def flaky_replace(source, destination):
        attempts.append((Path(source), Path(destination)))
        if len(attempts) < 3:
            raise _synced_folder_lock_error()
        os.replace(source, destination)

    def write_once(handle):
        nonlocal writer_calls
        writer_calls += 1
        handle.write("new")

    monkeypatch.setattr(storage_policy.time, "sleep", delays.append)
    storage_policy.atomic_write_private(
        target, write_once, text=True, replace_fn=flaky_replace)

    assert target.read_text(encoding="utf-8") == "new"
    assert writer_calls == 1
    assert len(attempts) == 3
    assert delays == list(storage_policy._SYNCED_FOLDER_RETRY_DELAYS[:2])
    assert list(private.iterdir()) == [target]


def test_atomic_replace_exhausts_bounded_transient_retries(
        monkeypatch, tmp_path):
    private = storage_policy.ensure_private_directory(tmp_path / "private")
    target = private / "artifact.json"
    storage_policy.atomic_write_private_text(target, "old")
    attempts = 0
    delays = []

    def always_locked(*_args):
        nonlocal attempts
        attempts += 1
        raise _synced_folder_lock_error()

    monkeypatch.setattr(storage_policy.time, "sleep", delays.append)
    with pytest.raises(PermissionError, match="temporarily locked"):
        storage_policy.atomic_write_private_text(
            target, "new", replace_fn=always_locked)

    assert attempts == len(storage_policy._SYNCED_FOLDER_RETRY_DELAYS) + 1
    assert delays == list(storage_policy._SYNCED_FOLDER_RETRY_DELAYS)
    assert target.read_text(encoding="utf-8") == "old"
    assert list(private.iterdir()) == [target]


def test_path_producer_runs_once_across_transient_replace_retry(
        monkeypatch, tmp_path):
    private = storage_policy.ensure_private_directory(tmp_path / "private")
    target = private / "artifact.pdf"
    attempts = 0
    producer_calls = 0

    def produce(path):
        nonlocal producer_calls
        producer_calls += 1
        path.write_bytes(b"private artifact")

    def flaky_replace(source, destination):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise _synced_folder_lock_error()
        os.replace(source, destination)

    monkeypatch.setattr(storage_policy.time, "sleep", lambda _delay: None)
    storage_policy.atomic_publish_private_file(
        target, produce, replace_fn=flaky_replace)

    assert producer_calls == 1
    assert attempts == 2
    assert target.read_bytes() == b"private artifact"
    assert list(private.iterdir()) == [target]


def test_atomic_replace_retry_fails_closed_if_temporary_changes(tmp_path):
    private = storage_policy.ensure_private_directory(tmp_path / "private")
    target = private / "artifact.json"
    storage_policy.atomic_write_private_text(target, "old")
    attempts = 0

    def tamper_then_fail(source, _destination):
        nonlocal attempts
        attempts += 1
        source = Path(source)
        original = source.stat()
        source.write_text("evil", encoding="utf-8")
        os.utime(source, ns=(original.st_atime_ns, original.st_mtime_ns))
        raise _synced_folder_lock_error()

    with pytest.raises(
            storage_policy.StoragePolicyError,
            match="temporary file changed"):
        storage_policy.atomic_write_private_text(
            target, "good", replace_fn=tamper_then_fail)

    assert attempts == 1
    assert target.read_text(encoding="utf-8") == "old"
    assert list(private.iterdir()) == [target]


def test_atomic_replace_retry_fails_closed_if_parent_identity_changes(
        monkeypatch, tmp_path):
    private = storage_policy.ensure_private_directory(tmp_path / "private")
    target = private / "artifact.json"
    storage_policy.atomic_write_private_text(target, "old")
    real_parent_identity = storage_policy._parent_identity
    parent_changed = False

    def changing_parent(path):
        identity = real_parent_identity(path)
        return (identity[0], identity[1] + 1) if parent_changed else identity

    def change_parent_during_backoff(_delay):
        nonlocal parent_changed
        parent_changed = True

    monkeypatch.setattr(storage_policy, "_parent_identity", changing_parent)
    monkeypatch.setattr(
        storage_policy.time, "sleep", change_parent_during_backoff)

    with pytest.raises(
            storage_policy.StoragePolicyError,
            match="parent changed during publication retry"):
        storage_policy.atomic_write_private_text(
            target, "new",
            replace_fn=lambda *_args: (_ for _ in ()).throw(
                _synced_folder_lock_error()))

    assert target.read_text(encoding="utf-8") == "old"
    assert list(private.iterdir()) == [target]


def test_atomic_writer_hardens_existing_parent_before_content_write(tmp_path):
    parent = tmp_path / "broad"
    parent.mkdir()
    observed = {}

    def write(handle):
        observed["parent"] = parent
        _assert_private_permissions(parent, directory=True)
        if isinstance(handle.name, (str, bytes, os.PathLike)):
            observed["temporary"] = Path(handle.name)
            _assert_private_permissions(
                observed["temporary"], directory=False
            )
        else:
            observed["temporary"] = None
            assert stat.S_IMODE(os.fstat(handle.fileno()).st_mode) == (
                storage_policy.PRIVATE_FILE_MODE
            )
        handle.write("private")

    storage_policy.atomic_write_private(
        parent / "artifact.txt", write, text=True)

    if observed["temporary"] is not None:
        assert observed["temporary"].parent == observed["parent"]
    assert (parent / "artifact.txt").read_text(encoding="utf-8") == "private"


def test_nested_private_directory_hardens_every_created_component(tmp_path):
    first = tmp_path / "one"
    second = first / "two"
    leaf = second / "three"

    storage_policy.ensure_private_directory(leaf)

    for directory in (first, second, leaf):
        _assert_private_permissions(directory, directory=True)


def test_append_rejects_a_hardlinked_target_without_modifying_it(tmp_path):
    private = storage_policy.ensure_private_directory(tmp_path / "private")
    source = private / "source.jsonl"
    alias = private / "alias.jsonl"
    storage_policy.atomic_write_private_text(source, '{"old":true}\n')
    try:
        os.link(source, alias)
    except OSError as exc:
        pytest.skip(f"hard links are unavailable on this filesystem: {exc}")

    with pytest.raises(
        storage_policy.StoragePolicyError, match="regular, unlinked file"
    ):
        storage_policy.append_private_jsonl(alias, {"new": True})

    assert source.read_text(encoding="utf-8") == '{"old":true}\n'
    assert alias.read_text(encoding="utf-8") == '{"old":true}\n'


def test_directory_link_component_is_rejected(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "redirect"
    try:
        _make_directory_link(link, target)
    except OSError as exc:
        pytest.skip(f"directory links are unavailable on this system: {exc}")

    with pytest.raises(
        storage_policy.StoragePolicyError, match="links or junctions"
    ):
        storage_policy.ensure_private_directory(link / "sensitive")

    assert not (target / "sensitive").exists()


def test_harden_private_tree_recurses_and_reports_counts(tmp_path):
    root = tmp_path / "tree"
    child = root / "nested"
    child.mkdir(parents=True)
    top_file = root / "top.txt"
    nested_file = child / "nested.txt"
    top_file.write_text("top", encoding="utf-8")
    nested_file.write_text("nested", encoding="utf-8")

    counts = storage_policy.harden_private_tree(root)

    assert counts == {"directories": 2, "files": 2}
    _assert_private_permissions(root, directory=True)
    _assert_private_permissions(child, directory=True)
    _assert_private_permissions(top_file, directory=False)
    _assert_private_permissions(nested_file, directory=False)


def test_ensure_private_tree_migrates_descendants_once(monkeypatch, tmp_path):
    root = tmp_path / "vector-db"
    nested = root / "segments"
    nested.mkdir(parents=True)
    artifact = nested / "vectors.bin"
    artifact.write_bytes(b"vectors")
    if os.name != "nt":
        root.chmod(0o777)
        nested.chmod(0o777)
        artifact.chmod(0o666)

    result = storage_policy.ensure_private_tree(root)

    assert result == root.resolve()
    _assert_private_permissions(root, directory=True)
    _assert_private_permissions(nested, directory=True)
    _assert_private_permissions(artifact, directory=False)
    marker_parent = root.parent / (
        storage_policy.PRIVATE_TREE_POLICY_DIRECTORY_NAME)
    markers = list(marker_parent.glob("*.json"))
    assert len(markers) == 1
    _assert_private_permissions(marker_parent, directory=True)
    _assert_private_permissions(markers[0], directory=False)

    monkeypatch.setattr(
        storage_policy, "harden_private_tree",
        lambda _root: pytest.fail("valid policy marker should skip migration"),
    )
    assert storage_policy.ensure_private_tree(root) == root.resolve()


def test_harden_private_tree_rejects_links_without_traversing_them(tmp_path):
    root = tmp_path / "tree"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    marker = outside / "marker.txt"
    marker.write_text("unchanged", encoding="utf-8")
    link = root / "redirect"
    try:
        _make_directory_link(link, outside)
    except OSError as exc:
        pytest.skip(f"directory links are unavailable on this system: {exc}")

    with pytest.raises(
        storage_policy.StoragePolicyError, match="links or junctions"
    ):
        storage_policy.harden_private_tree(root)

    assert marker.read_text(encoding="utf-8") == "unchanged"


@pytest.mark.skipif(os.name == "nt", reason="Windows uses DACLs, not modes")
def test_harden_private_tree_corrects_existing_posix_modes(tmp_path):
    root = tmp_path / "tree"
    root.mkdir(mode=0o777)
    artifact = root / "artifact.txt"
    artifact.write_text("private", encoding="utf-8")
    root.chmod(0o777)
    artifact.chmod(0o666)

    storage_policy.harden_private_tree(root)

    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE(artifact.stat().st_mode) == 0o600


@pytest.mark.skipif(os.name != "nt", reason="Windows DACL regression")
def test_windows_dacl_is_protected_and_grants_only_the_current_user(tmp_path):
    private = storage_policy.ensure_private_directory(tmp_path / "private")
    artifact = private / "artifact.txt"
    storage_policy.atomic_write_private_text(artifact, "private")

    assert storage_policy.windows_path_is_private(private, directory=True)
    assert storage_policy.windows_path_is_private(artifact, directory=False)
