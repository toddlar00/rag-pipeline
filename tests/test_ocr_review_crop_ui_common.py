"""Focused compatibility controls for the inward crop UI helper extraction.

No Blocks, server, browser, PDF or OCR is run. Tiny PIL images characterize
the existing salted identity only; callback/queue coverage remains elsewhere.
"""
from __future__ import annotations

import ast
import copy
import hashlib
import os
from pathlib import Path
import struct
import subprocess
import sys
import threading
from types import SimpleNamespace as NS

import pytest

import ocr_review_crop_ui_common as common
import ocr_review_crop_ui as legacy
import ocr_review_crop_archive_ui as archive
import ocr_review_crop_uncertainty_live_ui as live


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("module,names", (
    (legacy, "_ID _LIMIT _PREVIEW_NOTICES _SHA _CropState _ReferenceFieldError "
     "_SaveCropState _Stale _close_unreturned_image _context _display _hash "
     "_id _image_identity _raw_reference_digest _reference _sha _text"),
    (archive, "_PREVIEW_NOTICES _ReferenceFieldError _close_unreturned_image "
     "_display _fields _image_identity _metadata _raw_reference_digest _reference _sha _text"),
    (live, "_LIMIT _PREVIEW_NOTICES _ReferenceFieldError _SaveCropState _Stale "
     "_close_unreturned_image _context _display _hash _id _image_identity "
     "_metadata _raw_reference_digest _reference _sha _text"),
), ids=("legacy", "archive", "live-v2"))
def test_all_compatibility_aliases_are_the_same_objects(module, names):
    for name in names.split():
        assert getattr(module, name) is getattr(common, name), name


def test_archive_stale_exception_and_identifier_policy_remain_distinct():
    assert archive._Stale is not common._Stale
    assert not issubclass(archive._Stale, common._Stale)
    assert not issubclass(common._Stale, archive._Stale)
    assert live._LiveState.__bases__ == (common._SaveCropState,)
    assert common._SaveCropState.__bases__ == (common._CropState,)
    assert common._id("run-1") == "run-1"
    with pytest.raises(archive._Stale):
        archive._identifier("run-1")


class _MustNotCopy:
    def __deepcopy__(self, memo):
        raise AssertionError("authority must be dropped, not copied")


@pytest.mark.parametrize("state_type", (common._CropState, common._SaveCropState, live._LiveState))
def test_state_deepcopy_constructs_fresh_authority_and_lock(state_type):
    original = state_type()
    original.generation, original.revision = "old", 99
    original.preview_profile = "dpi576"
    original.reference_digest = "old digest"
    for name in ("selected", "baseline", "retry", "loaded", "pending", "draft_scope"):
        setattr(original, name, _MustNotCopy())
    if isinstance(original, common._SaveCropState):
        original.last_score, original.save_revision = _MustNotCopy(), 45
    if isinstance(original, live._LiveState):
        for name in ("uncertainty", "lineage", "selection", "edit_lease", "fields_digest"):
            setattr(original, name, _MustNotCopy())
        original.image_token, original.image_seen = "old image", True
        original.mode, original.annotation_id = "annotate", "a000001"

    first, second = copy.deepcopy(original), copy.deepcopy(original)
    assert type(first) is type(second) is state_type
    assert first.lock is not original.lock and second.lock is not first.lock
    assert len({original.generation, first.generation, second.generation}) == 3
    for fresh in (first, second):
        assert fresh.revision == 0 and fresh.preview_profile == "fit"
        assert fresh.reference_digest == common._raw_reference_digest("", "")
        assert all(getattr(fresh, name) is None for name in (
            "selected", "baseline", "retry", "loaded", "pending", "draft_scope"))
        if isinstance(fresh, common._SaveCropState):
            assert fresh.last_score is None and fresh.save_revision == 0
        if isinstance(fresh, live._LiveState):
            assert fresh.uncertainty == {"journal": None, "annotations": [], "dirty": False}
            assert fresh.lineage is fresh.image_token is fresh.selection is fresh.edit_lease is None
            assert fresh.fields_digest is fresh.annotation_id is None
            assert fresh.image_seen is False and fresh.mode == "browse"
    if isinstance(first, live._LiveState):
        first.uncertainty["annotations"].append({"local": True})
        assert second.uncertainty["annotations"] == []
    assert original.revision == 99 and original.preview_profile == "dpi576"


@pytest.mark.parametrize("state_type", (common._CropState, common._SaveCropState))
@pytest.mark.parametrize("clear_view,clear_draft", ((False, True), (True, False), (True, True)))
def test_revoke_retains_pins_but_clears_only_requested_authority(state_type, clear_view, clear_draft):
    state = state_type()
    pins = ({"run": "baseline"}, {"run": "retry"})
    state.baseline, state.retry = pins
    state.selected = ("run", "item", "context")
    loaded, draft = {"image": "identity only"}, {"scope": "declared"}
    state.loaded, state.draft_scope, state.pending = loaded, draft, {"ticket": "old"}
    state.reference_digest = common._raw_reference_digest("exact draft", "partial\n")
    digest, generation = state.reference_digest, state.generation
    if isinstance(state, common._SaveCropState):
        state.last_score = b"bounded old declaration"
    state.revoke(clear_view=clear_view, clear_draft=clear_draft)
    assert state.generation != generation and state.revision == 1 and state.pending is None
    assert state.baseline is pins[0] and state.retry is pins[1]
    assert state.selected == ("run", "item", "context")
    assert state.loaded is (None if clear_view else loaded)
    cleared = clear_view and clear_draft
    assert state.draft_scope is (None if cleared else draft)
    assert state.reference_digest == (common._raw_reference_digest("", "") if cleared else digest)
    if isinstance(state, common._SaveCropState):
        assert state.last_score is None and state.save_revision == 1


def _catalog_row():
    return {"pack_id": "a" * 32, "manifest_sha256": "b" * 64,
            "status": "verified_complete", "requires_attention": True}


def test_metadata_all_existing_statuses_and_unverified_hash_are_detached():
    for status in ("not_created", "incomplete", "present_unverified", "verified_complete", "cleanup_uncertain"):
        source = {**_catalog_row(), "status": status, "manifest_sha256": None}
        checked = common._metadata(source)
        assert checked == source and checked is not source
        source["pack_id"] = "c" * 32
        assert checked["pack_id"] == "a" * 32
        checked["status"] = "local change"
        assert source["status"] == status


@pytest.mark.parametrize("field,value", (
    ("pack_id", None), ("pack_id", "a" * 31), ("pack_id", "A" * 32),
    ("pack_id", "../pack"), ("manifest_sha256", "B" * 64),
    ("manifest_sha256", "b" * 63), ("manifest_sha256", 0),
    ("status", "approved"), ("status", None), ("status", []),
    ("requires_attention", False), ("requires_attention", 1),
), ids=("missing-id", "short-id", "upper-id", "path-id", "upper-sha", "short-sha",
        "integer-sha", "authority-status", "null-status", "unhashable-status", "false-attention", "int-attention"))
def test_metadata_rejects_malformed_fields_without_mutating_input(field, value):
    row = {**_catalog_row(), field: value}
    before = copy.deepcopy(row)
    # The extracted existing status membership check raises TypeError for a
    # list, rather than introducing a new error-normalization policy here.
    with pytest.raises((ValueError, TypeError)):
        common._metadata(row)
    assert row == before


class _StringSubclass(str):
    pass


@pytest.mark.parametrize("shape", ("missing", "extra", "key-subclass", "mapping-subclass"))
def test_metadata_closed_builtin_mapping_and_key_shape(shape):
    row = _catalog_row()
    if shape == "missing":
        del row["manifest_sha256"]
    elif shape == "extra":
        row["source_sha256"] = "c" * 64
    elif shape == "key-subclass":
        row = {_StringSubclass(key): value for key, value in row.items()}
    else:
        class MappingSubclass(dict):
            pass
        row = MappingSubclass(row)
    with pytest.raises(ValueError):
        common._metadata(row)


def test_raw_digest_and_reference_preserve_authored_fields_and_typed_error():
    reference, entries, digest = common._reference("", "literal ")
    assert reference == "" and entries == ["literal "]
    assert digest == common._raw_reference_digest("", "literal ")
    assert digest != common._raw_reference_digest("", "literal")
    partial = common._raw_reference_digest("draft", "literal\n")
    assert isinstance(partial, str) and len(partial) == 64
    with pytest.raises(legacy._ReferenceFieldError) as caught:
        common._reference("draft", "literal\n")
    assert type(caught.value) is archive._ReferenceFieldError is live._ReferenceFieldError
    assert caught.value.code == "critical_blank"


def test_context_digest_uses_current_execution_snapshot_without_mutating_sources():
    workspace = NS(document=NS(page_count=1))
    review = {"annotation_context": "view", "page": 1, "regions": [{"id": "r00"}]}
    execution = NS(lock=threading.RLock(), runs=("run1",), display_run="run1", result_generation="one")
    original = copy.deepcopy(review)
    controls = tuple(range(9))
    before = common._context(workspace, review, "view", controls, execution)
    execution.result_generation = "two"
    assert before != common._context(workspace, review, "view", controls, execution)
    assert review == original and execution.runs == ("run1",)
    with pytest.raises(common._Stale):
        common._context(workspace, review, "other-view", controls, execution)


def test_image_identity_keeps_existing_dimension_salted_rgb_bytes():
    image_module = pytest.importorskip("PIL.Image")
    raw = bytes(range(12))
    with image_module.frombytes("RGB", (2, 2), raw) as square, image_module.frombytes("RGB", (1, 4), raw) as tall:
        expected = hashlib.sha256(b"ocr-crop-review-rgb-v1\0" + struct.pack("!II", 2, 2) + raw).hexdigest()
        assert common._image_identity(square) == [2, 2, expected]
        assert common._image_identity(tall)[2] != expected
        assert expected != hashlib.sha256(raw).hexdigest()
        assert square.tobytes() == raw  # Identity does not close transferred output.


@pytest.mark.parametrize("secondary", (None, RuntimeError("close failed"), KeyboardInterrupt(), SystemExit(2)))
def test_disposal_attempts_once_without_replacing_primary_failure(secondary):
    calls = []

    def close():
        calls.append("close")
        if secondary is not None:
            raise secondary

    primary = RuntimeError("selected primary")
    with pytest.raises(RuntimeError) as caught:
        try:
            raise primary
        finally:
            common._close_unreturned_image(NS(close=close))
    assert caught.value is primary and calls == ["close"]
    assert common._close_unreturned_image(None) is None


def test_import_direction_is_inward_even_for_lazy_imports():
    tree = ast.parse((ROOT / "ocr_review_crop_ui_common.py").read_text(encoding="utf-8"))
    imported = {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    imported |= {node.module.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
    assert {name for name in imported if name.startswith("ocr_")} == {"ocr_crop_comparison"}
    assert imported == {"__future__", "hashlib", "re", "struct", "threading", "uuid", "ocr_crop_comparison", "PIL"}
    for module in (archive, live):
        consumer = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        assert not any(isinstance(node, ast.ImportFrom) and node.module == "ocr_review_crop_ui"
                       for node in ast.walk(consumer))


def test_common_import_and_blank_state_need_neither_ui_consumers_nor_heavy_backends():
    code = f"""
import sys
sys.path.insert(0, {str(ROOT)!r})
forbidden = {{'gradio', 'PIL', 'pymupdf', 'fitz', 'rapidocr', 'onnxruntime', 'numpy',
    'cv2', 'torch', 'transformers', 'ocr_review_crop_ui', 'ocr_review_crop_archive_ui',
    'ocr_review_crop_uncertainty_live_ui', 'ocr_review_execution', 'ocr_review_crop_packs'}}
assert not forbidden.intersection(name.split('.', 1)[0] for name in sys.modules)
attempts = []
class RefuseImports:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.', 1)[0] in forbidden:
            attempts.append(fullname)
            raise ImportError('UI/heavy dependency refused')
sys.meta_path.insert(0, RefuseImports())
import ocr_review_crop_ui_common as common
assert common._CropState().loaded is None and common._SaveCropState().last_score is None
assert not attempts, attempts
assert not forbidden.intersection(name.split('.', 1)[0] for name in sys.modules)
print('inward import passed')
"""
    environment = {key: value for key, value in os.environ.items() if not key.upper().startswith("PYTHON")}
    result = subprocess.run([sys.executable, "-I", "-B", "-c", code], cwd=ROOT, env=environment,
                            capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "inward import passed"
