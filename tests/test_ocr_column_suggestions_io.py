"""Synthetic-only fixed input, no-clobber, privacy and publication race checks."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import threading

import pytest

import ocr_column_suggestions_io as io
from ocr_recovery import ReportCleanupError
from resource_lease import PathLease, PathLeaseBusyError
import storage_policy
from test_ocr_layout import _candidate, _line, _report


PRIVATE = "SYNTHETIC_PRIVATE_OCR_TEXT"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_files(tmp_path, *, mode="none", candidates=None, max_pages=20):
    paths = {key: tmp_path / f"PRIVATE_{key}.json" for key in ("source", "recovery", "output")}
    paths["source"].write_bytes(b"Synthetic non-PDF bytes: hashed only, never parsed or rendered.")
    lines = [_line(PRIVATE, 80, 50, 700, 90)]
    for row in range(6):
        lines.extend([_line(f"{PRIVATE} left {row}", 80, 200 + row * 70, 330, 230 + row * 70),
                      _line(f"{PRIVATE} right {row}", 550, 200 + row * 70, 820, 230 + row * 70)])
    lines.append(_line(PRIVATE, 80, 940, 380, 965))
    report = _report([_candidate(lines, mode=mode)] if candidates is None else candidates,
                     mode=mode, max_pages=max_pages)
    report["source_sha256"] = digest(paths["source"])
    paths["recovery"].write_text(json.dumps(report), encoding="utf-8")
    return paths


@pytest.fixture
def files(tmp_path):
    return make_files(tmp_path)


def run(files, pages=None):
    return io.suggest_column_files(files["source"], files["recovery"], files["output"],
                                   requested_pages=[1] if pages is None else pages)


@pytest.mark.parametrize("mode", ["none", "contrast", "deskew", "deskew-contrast"])
def test_real_report_private_create_only_bound_and_content_free(tmp_path, mode):
    files = make_files(tmp_path, mode=mode)
    before = {key: path.read_bytes() for key, path in files.items() if key != "output"}
    result = run(files)
    assert result["source_sha256"] == digest(files["source"])
    assert result["recovery_sha256"] == digest(files["recovery"])
    assert result["kind"] == "ocr_column_suggestions"
    assert result["summary"]["suggested_pages"] == (1 if mode == "none" else 0)
    if mode != "none":
        assert result["pages"][0]["reason"] == "unsupported_preprocessed_candidate"
    assert result["requires_attention"] is True
    saved = files["output"].read_bytes()
    assert json.loads(saved) == result
    assert PRIVATE.encode() not in saved
    assert str(tmp_path).encode() not in saved
    assert all(path.read_bytes() == before[key] for key, path in files.items() if key != "output")
    if os.name == "nt":
        assert storage_policy.windows_path_is_private(files["output"], directory=False)
    else:
        assert stat.S_IMODE(files["output"].stat().st_mode) == 0o600
    with pytest.raises(FileExistsError):
        run(files)
    assert files["output"].read_bytes() == saved


@pytest.mark.parametrize("key", ["source", "recovery"])
@pytest.mark.parametrize("replacement", [False, True])
def test_input_content_or_identical_byte_generation_changed_after_serialization_refuses(files, monkeypatch, key, replacement):
    actual = storage_policy.atomic_write_private

    def race(path, writer, **options):
        publish = options["replace_fn"]

        def commit(temporary, target):
            content = files[key].read_bytes()
            if replacement:
                moved = files[key].with_suffix(".original")
                files[key].rename(moved)
                files[key].write_bytes(content)
            else:
                files[key].write_bytes(content + b" ")
            publish(temporary, target)

        return actual(path, writer, **{**options, "replace_fn": commit})

    monkeypatch.setattr(storage_policy, "atomic_write_private", race)
    with pytest.raises(RuntimeError, match="changed before publication"):
        run(files)
    assert not files["output"].exists()


def test_source_mismatch_is_not_published(files):
    files["source"].write_bytes(b"Different generated source")
    with pytest.raises(ValueError, match="source differs"):
        run(files)
    assert not files["output"].exists()


@pytest.mark.parametrize("raw", [b"[]", b"{", b'{"x":NaN}', b'{"x":1e999}', b"\xff",
                                    b'{"SYNTHETIC_PRIVATE_OCR_TEXT":1,"SYNTHETIC_PRIVATE_OCR_TEXT":2}',
                                    b'{"x":' + b'[' * 65 + b'0' + b']' * 65 + b'}'])
def test_strict_json_errors_are_redacted(files, raw):
    files["recovery"].write_bytes(raw)
    with pytest.raises(ValueError) as caught:
        run(files)
    assert PRIVATE not in str(caught.value)
    assert str(files["recovery"]) not in str(caught.value)
    assert not files["output"].exists()


@pytest.mark.parametrize("pages", [None, (), [], [True], [1.0], ["1"], [0], [-1], [5001], [1, 1], list(range(1, 22))])
def test_invalid_page_contract_fails_before_input_read(files, monkeypatch, pages):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("input must not be read")

    monkeypatch.setattr(io, "_snapshot", forbidden)
    with pytest.raises(ValueError):
        io.suggest_column_files(files["source"], files["recovery"], files["output"], requested_pages=pages)
    assert not files["output"].exists()


def test_out_of_source_page_is_rejected(files):
    with pytest.raises(ValueError):
        run(files, [2])
    assert not files["output"].exists()


def test_requested_pages_detached_before_io(files, monkeypatch):
    pages = [1]
    actual = io._snapshot

    def read(*args, **kwargs):
        pages[:] = [2]
        return actual(*args, **kwargs)

    monkeypatch.setattr(io, "_snapshot", read)
    result = run(files, pages)
    assert result["coverage"]["requested_pages"] == [1]


@pytest.mark.parametrize("mutate_argument", [False, True])
def test_valid_but_wrong_cohort_producer_cannot_publish(tmp_path, monkeypatch, mutate_argument):
    files = make_files(tmp_path, candidates=[_candidate(), _candidate()])
    actual = io.build_column_suggestions
    requested = [1]

    def wrong(recovery, *, recovery_sha256, requested_pages):
        if mutate_argument:
            requested_pages[:] = [2]
        return actual(recovery, recovery_sha256=recovery_sha256, requested_pages=[2])

    monkeypatch.setattr(io, "build_column_suggestions", wrong)
    with pytest.raises(ValueError, match="differs from requested pages"):
        run(files, requested)
    assert requested == [1]
    assert not files["output"].exists()


@pytest.mark.parametrize("key", ["source", "recovery"])
def test_output_alias_preserves_input(files, key):
    original = files[key].read_bytes()
    files["output"] = files[key]
    with pytest.raises(ValueError):
        run(files)
    assert files[key].read_bytes() == original


def test_inputs_cannot_alias(files):
    files["recovery"] = files["source"]
    with pytest.raises(ValueError):
        run(files)


@pytest.mark.parametrize("key", ["source", "recovery", "output"])
def test_hardlinked_artifact_refused(files, tmp_path, key):
    if key == "output":
        files[key].write_bytes(b"Existing generated output")
    before = files[key].read_bytes()
    os.link(files[key], tmp_path / "external-alias")
    with pytest.raises(ValueError):
        run(files)
    assert files[key].read_bytes() == before


@pytest.mark.parametrize("key", ["source", "recovery", "output"])
@pytest.mark.parametrize("dangling", [False, True])
def test_actual_symbolic_links_are_rejected(files, tmp_path, key, dangling):
    target = tmp_path / "link-target"
    if files[key].exists():
        files[key].rename(target)
    elif not dangling:
        target.write_bytes(b"Existing generated target")
    if dangling and target.exists():
        target.rename(tmp_path / "preserved-target")
    try:
        files[key].symlink_to(target)
    except OSError:
        pytest.skip("symbolic links are unavailable")
    with pytest.raises((ValueError, OSError)):
        run(files)
    assert files[key].is_symlink()


@pytest.mark.parametrize("key", ["source", "recovery", "output"])
def test_directories_are_not_artifacts(files, tmp_path, key):
    files[key] = tmp_path
    with pytest.raises(ValueError):
        run(files)


@pytest.mark.parametrize("constant", ["MAX_SOURCE_BYTES", "MAX_RECOVERY_BYTES", "MAX_REPORT_BYTES"])
def test_all_byte_limits_are_enforced(files, monkeypatch, constant):
    monkeypatch.setattr(io, constant, 1)
    with pytest.raises(ValueError):
        run(files)
    assert not files["output"].exists()


def test_empty_source_is_not_a_valid_binding(files):
    files["source"].write_bytes(b"")
    with pytest.raises(ValueError):
        run(files)
    assert not files["output"].exists()


def test_missing_output_parent_is_not_created(files, tmp_path):
    files["output"] = tmp_path / "missing" / "report.json"
    with pytest.raises(FileNotFoundError):
        run(files)
    assert not files["output"].parent.exists()


def test_output_parent_replacement_during_inference_is_rejected(files, tmp_path, monkeypatch):
    output_parent = tmp_path / "destination"
    output_parent.mkdir()
    files["output"] = output_parent / "report.json"
    actual = io.build_column_suggestions
    identity = storage_policy._parent_identity
    changed = False

    def replace(*args, **kwargs):
        nonlocal changed
        result = actual(*args, **kwargs)
        changed = True
        return result

    def observed(path):
        value = identity(path)
        return (value[0], value[1] + 1) if changed and Path(path) == output_parent else value

    # Windows may prevent renaming a directory containing the live path lease;
    # inject the replacement generation directly, without relaxing the check.
    monkeypatch.setattr(storage_policy, "_parent_identity", observed)
    monkeypatch.setattr(io, "build_column_suggestions", replace)
    with pytest.raises(RuntimeError, match="directory changed"):
        run(files)
    assert not files["output"].exists()


def test_staged_content_tampering_is_not_published(files, monkeypatch):
    actual = storage_policy.atomic_write_private

    def tamper(path, writer, **options):
        publish = options["replace_fn"]

        def commit(temporary, target):
            Path(temporary).write_bytes(b'{"forged":true}')
            publish(temporary, target)

        return actual(path, writer, **{**options, "replace_fn": commit})

    monkeypatch.setattr(storage_policy, "atomic_write_private", tamper)
    with pytest.raises(RuntimeError, match="staged report changed"):
        run(files)
    assert not files["output"].exists()


def test_noncooperating_output_writer_is_never_overwritten(files, monkeypatch):
    actual = io._publish_new_report

    def competing(temporary, destination):
        Path(destination).write_bytes(b"Synthetic competing artifact")
        actual(temporary, destination)

    monkeypatch.setattr(io, "_publish_new_report", competing)
    with pytest.raises(FileExistsError):
        run(files)
    assert files["output"].read_bytes() == b"Synthetic competing artifact"


@pytest.mark.parametrize("exception", [KeyboardInterrupt, ReportCleanupError])
def test_late_cancel_and_cleanup_failure_preserve_complete_report(files, monkeypatch, exception):
    actual = io._publish_new_report

    def late(temporary, destination):
        actual(temporary, destination)
        raise exception(PRIVATE)

    monkeypatch.setattr(io, "_publish_new_report", late)
    with pytest.raises(exception):
        run(files)
    assert json.loads(files["output"].read_bytes())["kind"] == "ocr_column_suggestions"


def test_cancel_before_publication_preserves_inputs(files, monkeypatch):
    before = digest(files["source"])

    def cancel(*_args, **_kwargs):
        raise KeyboardInterrupt(PRIVATE)

    monkeypatch.setattr(io, "build_column_suggestions", cancel)
    with pytest.raises(KeyboardInterrupt):
        run(files)
    assert not files["output"].exists()
    assert digest(files["source"]) == before


def test_busy_output_lease_refuses_without_waiting_or_reading(files, monkeypatch):
    ready, release = threading.Event(), threading.Event()

    def hold():
        with PathLease(files["output"], backend="ocr-column-suggestions", collection_name="suggestions",
                       operation="test", timeout=0):
            ready.set()
            release.wait(10)

    owner = threading.Thread(target=hold)
    owner.start()
    assert ready.wait(5)
    monkeypatch.setattr(io, "_snapshot", lambda *_args: pytest.fail("busy lease must precede reads"))
    try:
        with pytest.raises(PathLeaseBusyError):
            run(files)
    finally:
        release.set()
        owner.join(5)
    assert not owner.is_alive()
    assert not files["output"].exists()
