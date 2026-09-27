"""Synthetic-only bounded generations, no-clobber publication and race tests."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat

import pytest

import ocr_docling
import ocr_omission_io as io
from ocr_recovery import ReportCleanupError
import storage_policy
from test_ocr_docling import PRIVATE, candidate, document, recovery
from test_ocr_omission import lines


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_files(tmp_path):
    paths = {name: tmp_path / f"{name}.json" for name in ("source", "recovery", "proposals", "output")}
    paths["source"].write_bytes(b"Synthetic source bytes, never parsed as a PDF.")
    report = recovery([lines(candidate(), [0, 1, 3, 5])], source=digest(paths["source"]))
    paths["recovery"].write_text(json.dumps(report), encoding="utf-8")
    proposals = ocr_docling.build_docling_proposals(document(), report, recovery_sha256=digest(paths["recovery"]),
        docling_sha256="c" * 64, manifest_sha256="d" * 64, effective_input_kind="original")
    paths["proposals"].write_text(json.dumps(proposals), encoding="utf-8")
    return paths


@pytest.fixture
def files(tmp_path):
    return make_files(tmp_path)


def run(files):
    return io.inspect_omission_files(*(files[key] for key in ("source", "recovery", "proposals", "output")))


def test_actual_private_create_only_report_records_exact_input_digests_without_text(files):
    before = {key: path.read_bytes() for key, path in files.items() if key != "output"}
    result = run(files)
    assert result["kind"] == "ocr_omission_diagnostics"
    assert result["source_sha256"] == digest(files["source"])
    assert result["recovery_sha256"] == digest(files["recovery"])
    assert result["proposals_sha256"] == digest(files["proposals"])
    assert result["summary"]["no_candidate_line_overlap"] == 1
    assert json.loads(files["output"].read_bytes()) == result
    assert PRIVATE not in files["output"].read_text(encoding="utf-8")
    assert all(path.read_bytes() == before[key] for key, path in files.items() if key != "output")
    if os.name == "nt":
        assert storage_policy.windows_path_is_private(files["output"], directory=False)
    else:
        assert stat.S_IMODE(files["output"].stat().st_mode) == 0o600
    saved = files["output"].read_bytes()
    with pytest.raises(FileExistsError):
        run(files)
    assert files["output"].read_bytes() == saved


@pytest.mark.parametrize("key", ["source", "recovery", "proposals"])
def test_each_input_rechecked_after_full_serialization_immediately_before_publication(files, monkeypatch, key):
    actual = storage_policy.atomic_write_private_json

    def race(path, payload, **options):
        publish = options["replace_fn"]

        def after_serialization(temporary, target):
            assert json.loads(Path(temporary).read_bytes())["kind"] == "ocr_omission_diagnostics"
            files[key].write_bytes(files[key].read_bytes() + b" ")
            publish(temporary, target)

        return actual(path, payload, **{**options, "replace_fn": after_serialization})

    monkeypatch.setattr(storage_policy, "atomic_write_private_json", race)
    with pytest.raises(RuntimeError, match="changed before publication"):
        run(files)
    assert not files["output"].exists()
    assert files[key].read_bytes().endswith(b" ")


@pytest.mark.parametrize("key", ["source", "recovery"])
def test_changed_bound_bytes_initially_fail_closed(files, key):
    files[key].write_bytes(files[key].read_bytes() + b" ")
    with pytest.raises(ValueError):
        run(files)
    assert not files["output"].exists()


def test_proposal_formatting_is_new_evidence_digest_not_an_unapproved_semantic_change(files):
    files["proposals"].write_bytes(files["proposals"].read_bytes() + b" ")
    result = run(files)
    assert result["proposals_sha256"] == digest(files["proposals"])


@pytest.mark.parametrize("key", ["recovery", "proposals"])
@pytest.mark.parametrize("raw", [b"[]", b"{", b'{"x":NaN}', b'{"x":1,"x":2}', b"\xff",
                                   b'{"SYNTHETIC_PRIVATE_TEXT":1,"SYNTHETIC_PRIVATE_TEXT":2}',
                                   b'{"x":' + b'[' * 65 + b'0' + b']' * 65 + b'}'])
def test_bad_strict_json_is_content_free_and_never_published(files, key, raw):
    files[key].write_bytes(raw)
    with pytest.raises(ValueError) as caught:
        run(files)
    assert PRIVATE not in str(caught.value)
    assert str(files[key]) not in str(caught.value)
    assert not files["output"].exists()


@pytest.mark.parametrize("key", ["source", "recovery", "proposals"])
def test_output_alias_cannot_overwrite_any_input(files, key):
    before = files[key].read_bytes()
    files["output"] = files[key]
    with pytest.raises(ValueError):
        run(files)
    assert files[key].read_bytes() == before


@pytest.mark.parametrize("key", ["source", "recovery", "proposals"])
def test_external_hardlink_is_rejected_before_processing(files, tmp_path, key):
    os.link(files[key], tmp_path / "synthetic-external-alias")
    with pytest.raises(ValueError):
        run(files)
    assert not files["output"].exists()


def test_input_aliases_are_rejected(files):
    files["proposals"] = files["recovery"]
    with pytest.raises(ValueError):
        run(files)
    assert not files["output"].exists()


def test_link_policy_applies_even_when_operator_passes_link_component(files, monkeypatch):
    actual = storage_policy.assert_no_link_components

    def reject(path):
        if Path(path) == files["recovery"]:
            raise ValueError("synthetic reparse-point rejection")
        return actual(path)

    monkeypatch.setattr(storage_policy, "assert_no_link_components", reject)
    with pytest.raises(ValueError):
        run(files)
    assert not files["output"].exists()


@pytest.mark.parametrize("key", ["source", "recovery", "proposals", "output"])
def test_directory_is_not_an_artifact(files, tmp_path, key):
    files[key] = tmp_path
    with pytest.raises(ValueError):
        run(files)


@pytest.mark.parametrize("limit", ["MAX_SOURCE_BYTES", "MAX_RECOVERY_BYTES", "MAX_PROPOSALS_BYTES"])
def test_all_file_byte_limits_are_enforced(files, monkeypatch, limit):
    monkeypatch.setattr(io, limit, 1)
    with pytest.raises(ValueError):
        run(files)
    assert not files["output"].exists()


def test_empty_source_is_rejected_even_if_hash_could_otherwise_match(files):
    files["source"].write_bytes(b"")
    with pytest.raises(ValueError):
        run(files)
    assert not files["output"].exists()


def test_uncooperating_late_output_writer_is_preserved(files, monkeypatch):
    actual = io.build_omission_diagnostics

    def race(*args, **kwargs):
        report = actual(*args, **kwargs)
        files["output"].write_bytes(b"Synthetic competing artifact")
        return report

    monkeypatch.setattr(io, "build_omission_diagnostics", race)
    with pytest.raises(FileExistsError):
        run(files)
    assert files["output"].read_bytes() == b"Synthetic competing artifact"


@pytest.mark.parametrize("exception", [KeyboardInterrupt, ReportCleanupError])
def test_late_cancel_or_cleanup_error_keeps_complete_output(files, monkeypatch, exception):
    actual = io._publish_new_report

    def fail(temporary, destination):
        actual(temporary, destination)
        raise exception(PRIVATE)

    monkeypatch.setattr(io, "_publish_new_report", fail)
    with pytest.raises(exception):
        run(files)
    assert json.loads(files["output"].read_bytes())["kind"] == "ocr_omission_diagnostics"


def test_cancel_before_publication_preserves_inputs_without_output(files, monkeypatch):
    before = digest(files["source"])

    def cancel(*_args, **_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(io, "build_omission_diagnostics", cancel)
    with pytest.raises(KeyboardInterrupt):
        run(files)
    assert not files["output"].exists()
    assert digest(files["source"]) == before


@pytest.mark.parametrize("json_input", [False, True])
def test_same_handle_second_pass_rejects_retimed_content_change(files, monkeypatch, json_input):
    path = files["recovery"] if json_input else files["source"]
    original = path.read_bytes()
    actual_open = Path.open

    class ChangingHandle:
        def __init__(self, wrapped):
            self.wrapped, self.seeks = wrapped, 0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.wrapped.close()

        def fileno(self):
            return self.wrapped.fileno()

        def read(self, size):
            return self.wrapped.read(size)

        def seek(self, offset):
            self.seeks += 1
            if self.seeks == 2:
                before = path.stat()
                with actual_open(path, "wb") as writer:
                    writer.write(bytes([original[0] ^ 1]) + original[1:])
                os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
            return self.wrapped.seek(offset)

    def opening(target, mode="r", *args, **kwargs):
        handle = actual_open(target, mode, *args, **kwargs)
        return ChangingHandle(handle) if target == path and mode == "rb" else handle

    monkeypatch.setattr(Path, "open", opening)
    with pytest.raises(RuntimeError, match="changed during reading"):
        io._snapshot(path, 1024 * 1024, json_input=json_input)


def test_second_pass_growth_is_bounded_and_does_not_follow_an_endless_writer(files, monkeypatch):
    actual_open = Path.open
    read_calls = []

    class GrowingHandle:
        def __init__(self, wrapped):
            self.wrapped, self.seeks = wrapped, 0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.wrapped.close()

        def fileno(self):
            return self.wrapped.fileno()

        def seek(self, offset):
            self.seeks += 1
            return self.wrapped.seek(offset)

        def read(self, size):
            read_calls.append(size)
            return b"x" * size if self.seeks == 2 else self.wrapped.read(size)

    def opening(path, mode="r", *args, **kwargs):
        handle = actual_open(path, mode, *args, **kwargs)
        return GrowingHandle(handle) if path == files["source"] and mode == "rb" else handle

    monkeypatch.setattr(Path, "open", opening)
    monkeypatch.setattr(io, "_CHUNK_BYTES", 17)
    with pytest.raises(ValueError, match="streaming byte budget"):
        io._snapshot(files["source"], 64, json_input=False)
    assert len(read_calls) < 10
    assert all(1 <= count <= 17 for count in read_calls)
