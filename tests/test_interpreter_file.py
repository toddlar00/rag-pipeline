"""Interpreter identity may follow symlinks; storage paths may not.

POSIX virtual environments expose ``bin/python`` as a symlink, so OCR
identity capture snapshots the real interpreter file instead of refusing it.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import storage_policy


def _link_or_skip(link: Path, target: Path, *, directory: bool = False) -> None:
    try:
        os.symlink(target, link, target_is_directory=directory)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks are unavailable here: {exc}")


def _interpreter(folder: Path) -> Path:
    folder.mkdir(parents=True)
    real = folder / "python3.12"
    real.write_bytes(b"interpreter bytes")
    return real


def test_link_free_interpreter_path_is_returned_unchanged(tmp_path):
    real = _interpreter(tmp_path / "base")

    assert storage_policy.interpreter_file(real) == real


def test_relative_interpreter_path_becomes_absolute(tmp_path, monkeypatch):
    _interpreter(tmp_path / "base")
    monkeypatch.chdir(tmp_path)

    assert storage_policy.interpreter_file(Path("base/python3.12")) == (
        tmp_path / "base" / "python3.12")


def test_symlinked_interpreter_resolves_to_its_real_file(tmp_path):
    real = _interpreter(tmp_path / "base")
    link = tmp_path / "python"
    _link_or_skip(link, real)

    resolved = storage_policy.interpreter_file(link)

    assert resolved == Path(os.path.realpath(real))
    assert resolved.read_bytes() == b"interpreter bytes"
    storage_policy.assert_no_link_components(resolved)


def test_linked_directory_component_resolves_to_the_real_file(tmp_path):
    real = _interpreter(tmp_path / "real-bin")
    linked_dir = tmp_path / "bin"
    _link_or_skip(linked_dir, real.parent, directory=True)

    resolved = storage_policy.interpreter_file(linked_dir / "python3.12")

    assert resolved == Path(os.path.realpath(real))
    storage_policy.assert_no_link_components(resolved)


def test_storage_paths_still_refuse_links(tmp_path):
    real = tmp_path / "report.json"
    real.write_text("{}", encoding="utf-8")
    link = tmp_path / "linked-report.json"
    _link_or_skip(link, real)

    with pytest.raises(storage_policy.StoragePolicyError):
        storage_policy.assert_no_link_components(link)
