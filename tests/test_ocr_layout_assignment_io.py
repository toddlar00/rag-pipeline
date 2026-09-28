"""Synthetic-only input races, strict imports and private review publication."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat

import pytest

import ocr_docling
import ocr_layout_assignment as policy
import ocr_layout_assignment_io as io
from ocr_recovery import ReportCleanupError
import storage_policy
from test_ocr_docling import PRIVATE, document, recovery


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def files(tmp_path):
    paths = {name: tmp_path / f"{name}.json" for name in ("source", "recovery", "proposals", "plan", "output")}
    paths["source"].write_bytes(b"Synthetic source bytes, never parsed as a PDF.")
    report = recovery(source=digest(paths["source"]))
    paths["recovery"].write_text(json.dumps(report), encoding="utf-8")
    proposals = ocr_docling.build_docling_proposals(document(), report, recovery_sha256=digest(paths["recovery"]),
        docling_sha256="c"*64, manifest_sha256="d"*64, effective_input_kind="original")
    paths["proposals"].write_text(json.dumps(proposals), encoding="utf-8")
    ctx = {"recovery": report, "recovery_sha256": digest(paths["recovery"]),
           "proposals": proposals, "proposals_sha256": digest(paths["proposals"])}
    plan = policy.build_assignment_plan([policy.suggest_assignment_page(1, **ctx)], **ctx)
    paths["plan"].write_text(json.dumps(plan), encoding="utf-8")
    return paths


def run(files, *, confirmed_pages=None):
    return io.review_assignment_files(*(files[key] for key in ("source", "recovery", "proposals", "plan", "output")),
                                      confirmed_pages=[1] if confirmed_pages is None else confirmed_pages)


def test_real_private_create_only_review_with_all_fixed_byte_bindings(files):
    before = {key: path.read_bytes() for key, path in files.items() if key != "output"}
    result = run(files)
    assert result["kind"] == "ocr_layout_assignment_review"
    assert result["source_sha256"] == digest(files["source"])
    assert result["recovery_sha256"] == digest(files["recovery"])
    assert result["proposals_sha256"] == digest(files["proposals"])
    assert result["plan_sha256"] == digest(files["plan"])
    assert result["pages"][0]["line_order"] == [0, 1, 3, 2, 4, 5]
    assert json.loads(files["output"].read_bytes()) == result
    assert all(path.read_bytes() == before[key] for key, path in files.items() if key != "output")
    if os.name == "nt":
        assert storage_policy.windows_path_is_private(files["output"], directory=False)
    else:
        assert stat.S_IMODE(files["output"].stat().st_mode) == 0o600
    saved = files["output"].read_bytes()
    with pytest.raises(FileExistsError):
        run(files)
    assert files["output"].read_bytes() == saved


@pytest.mark.parametrize("key", ["source", "recovery", "proposals", "plan"])
def test_all_inputs_rechecked_after_full_serialization_before_final_commit(files, monkeypatch, key):
    actual = storage_policy.atomic_write_private_json

    def change(path, payload, **options):
        publish = options["replace_fn"]

        def during_commit(temporary, target):
            assert Path(temporary).is_file()
            files[key].write_bytes(files[key].read_bytes() + b" ")
            publish(temporary, target)

        return actual(path, payload, **{**options, "replace_fn": during_commit})

    monkeypatch.setattr(storage_policy, "atomic_write_private_json", change)
    with pytest.raises(RuntimeError, match="changed before publication"):
        run(files)
    assert not files["output"].exists()
    assert files[key].read_bytes().endswith(b" ")


@pytest.mark.parametrize("key", ["source", "recovery", "proposals"])
def test_changed_initial_fixed_input_fails_binding(files, key):
    files[key].write_bytes(files[key].read_bytes() + b" ")
    with pytest.raises(ValueError):
        run(files)
    assert not files["output"].exists()


@pytest.mark.parametrize("key", ["recovery", "proposals", "plan"])
@pytest.mark.parametrize("raw", [b"[]", b"{", b'{"x":NaN}', b'{"x":1,"x":2}', b"\xff"])
def test_bad_json_never_exposes_content_or_publishes(files, key, raw):
    files[key].write_bytes(raw)
    with pytest.raises(ValueError) as caught:
        run(files)
    assert PRIVATE not in str(caught.value)
    assert str(files[key]) not in str(caught.value)
    assert not files["output"].exists()


@pytest.mark.parametrize("key", ["source", "recovery", "proposals", "plan"])
def test_input_and_output_alias_never_clobbers_input(files, key):
    before = files[key].read_bytes()
    files["output"] = files[key]
    with pytest.raises(ValueError):
        run(files)
    assert files[key].read_bytes() == before


@pytest.mark.parametrize("key", ["source", "recovery", "proposals", "plan"])
def test_external_hardlink_rejected(files, tmp_path, key):
    os.link(files[key], tmp_path / "external-alias")
    with pytest.raises(ValueError):
        run(files)
    assert not files["output"].exists()


def test_final_uncooperating_output_writer_is_preserved(files, monkeypatch):
    actual = io.build_assignment_review

    def race(*args, **kwargs):
        result = actual(*args, **kwargs)
        files["output"].write_bytes(b"Other writer's synthetic artifact")
        return result

    monkeypatch.setattr(io, "build_assignment_review", race)
    with pytest.raises(FileExistsError):
        run(files)
    assert files["output"].read_bytes() == b"Other writer's synthetic artifact"


@pytest.mark.parametrize("exception", [KeyboardInterrupt, ReportCleanupError])
def test_late_cancel_and_cleanup_keep_complete_review(files, monkeypatch, exception):
    actual = io._publish_new_report

    def fail(temporary, target):
        actual(temporary, target)
        raise exception("synthetic failure")

    monkeypatch.setattr(io, "_publish_new_report", fail)
    with pytest.raises(exception):
        run(files)
    assert json.loads(files["output"].read_bytes())["kind"] == "ocr_layout_assignment_review"


def test_cancel_before_publication_leaves_no_output(files, monkeypatch):
    def cancel(*_args, **_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(io, "build_assignment_review", cancel)
    with pytest.raises(KeyboardInterrupt):
        run(files)
    assert not files["output"].exists()


@pytest.mark.parametrize("limit", ["MAX_SOURCE_BYTES", "MAX_RECOVERY_BYTES", "MAX_PROPOSALS_BYTES", "MAX_PLAN_BYTES"])
def test_each_input_has_an_enforced_byte_limit(files, monkeypatch, limit):
    monkeypatch.setattr(io, limit, 1)
    with pytest.raises(ValueError):
        run(files)
    assert not files["output"].exists()


def test_partial_draft_cannot_be_published_as_review(files):
    payload = json.loads(files["plan"].read_bytes())
    payload["pages"][0]["assignments"][0]["region_ref"] = None
    files["plan"].write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="complete"):
        run(files)
    assert not files["output"].exists()


def test_history_or_source_text_cannot_be_smuggled_through_plan(files):
    payload = json.loads(files["plan"].read_bytes())
    payload["history"] = [{"sequence": 1, "page_number": 1, "action": PRIVATE}]
    files["plan"].write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError) as caught:
        run(files)
    assert PRIVATE not in str(caught.value)
    assert not files["output"].exists()
