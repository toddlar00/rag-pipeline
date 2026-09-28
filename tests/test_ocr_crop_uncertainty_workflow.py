"""Generated full archive/store/host uncertainty journeys, without a browser.

Every historical archive/report/receipt/disposition validator is real. Workspace
source checks and preview rendering are explicitly inert doubles: the RGB image
is generated, not a PDF observation. No OCR/model runtime is called. UI tokens,
pointer alignment and human inspection remain separate acceptance work.
"""

import copy
import hashlib

import pytest

pytest.importorskip("PIL")

from PIL import Image

import model_artifacts
import ocr_comparison
import ocr_crop_comparison as crops
import ocr_crop_raster_view as raster
import ocr_crop_review_pack as v1
import ocr_crop_uncertainty_journal as history
from ocr_crop_review_runtime import CropRasterPreview
import ocr_evaluation
import ocr_execution_receipt
import ocr_experiment_runtime
from ocr_review_crop_packs import CropReviewPackService
from test_ocr_disposition_archive import archive_fixture
from test_ocr_review_crop_packs import _workspace


def _forbidden(*_args, **_kwargs):
    raise AssertionError("this workflow must not invoke OCR or a forbidden scorer")


def _block_scores(patch):
    patch.setattr(crops, "compare_ocr", _forbidden)
    patch.setattr(ocr_comparison, "compare_ocr", _forbidden)
    patch.setattr(ocr_evaluation, "evaluate_ocr", _forbidden)


@pytest.fixture
def journey(tmp_path, monkeypatch, request):
    operation = request.param
    before = archive_fixture(operation, text="liable", bbox=[0., 0., 1., 1.])
    after = archive_fixture(operation, text="not liable", dpi=400, bbox=[0., 0., 1., 1.])
    binding = {"source_sha256": before["source_sha256"], "page_count": before["page_count"],
               "recovery_sha256": hashlib.sha256(before["retained_inputs"]["recovery.json"]).hexdigest()}
    sides = {side: {key: value[key] for key in ("artifacts", "retained_inputs")}
             for side, value in (("baseline", before), ("retry", after))}
    initial = v1.build_crop_review_pack(**sides, **binding, baseline_region_id="r00", retry_region_id="r00",
                                       reference="not liable", critical_tokens_text="not liable")
    root = tmp_path / "packs"
    root.mkdir()
    workspace = _workspace(binding, [])
    services, previews = [], []

    def render(scope, *, cancel_requested, preview_profile="fit"):
        assert not cancel_requested()
        assert scope["bbox"] == [0., 0., 1., 1.]
        scale = {"fit": 2., "dpi288": 4., "dpi576": 8.}[preview_profile]
        side = int(72 * scale)
        image = Image.new("RGB", (side, side), "white")
        view = raster.build_raster_view(scope=scope, preview_profile=preview_profile,
            command_clip=[0., 0., 72., 72.], native_clip=[0., 0., 72., 72.],
            command_matrix=[scale, 0., 0., scale, 0., 0.],
            native_matrix=[scale, 0., 0., scale, 0., 0.],
            projected_rect=[0., 0., float(side), float(side)],
            pixel_rect=[0, 0, side, side], width=side, height=side,
            rgb_sha256=hashlib.sha256(image.tobytes()).hexdigest())
        owner = CropRasterPreview(image, view)
        previews.append(owner)
        return owner

    def service():
        instance = CropReviewPackService(workspace, root,
            render_preview=lambda *a, **kw: render(*a, **kw).image,
            render_preview_with_view=render, preview_private_root=tmp_path / "inert-preview")
        services.append(instance)
        return instance

    # Fixtures have finished declaring their historical observations. All real
    # model/runtime acquisition and receipt capture are forbidden from here.
    monkeypatch.setattr(model_artifacts, "load_model_artifact_registry", _forbidden)
    monkeypatch.setattr(model_artifacts, "verified_installed_package_model", _forbidden)
    monkeypatch.setattr(ocr_execution_receipt, "capture_execution_receipt", _forbidden)
    monkeypatch.setattr(ocr_execution_receipt, "validate_installation_evidence", _forbidden)
    monkeypatch.setattr(ocr_experiment_runtime, "capture_runtime_manifest", _forbidden)
    current = service()
    saved = current._store.publish(initial)
    yield {"service": current, "restart": service, "initial": initial, "saved": saved}
    for instance in services:
        instance.close()
    for preview in previews:
        preview.close()


def _author(service, saved, journal, changes, *, views=(), reset=False):
    return service.author_v2(saved["pack_id"], expected_pack_sha256=saved["manifest_sha256"],
        journal=journal, reference="not liable", critical_tokens=["not liable"],
        reset_anchors=reset, annotation_changes=changes, reason="explicit generated author action", views=views)


def _save(service, parent, authored, *, reviewed=None, dirty=False):
    return service.save_revision_v2(parent["pack_id"], expected_pack_sha256=parent["manifest_sha256"],
        reference="not liable", critical_tokens_text="not liable",
        uncertainty={"journal": authored["journal"],
                     "annotations": authored["declaration"]["annotations"], "dirty": dirty},
        reviewed=reviewed, verify_current=lambda: True)


def _unresolved(service, saved):
    opened = service.open_with_view(saved["pack_id"])
    view = opened["preview"].raster_view
    annotation = {"annotation_id": "a000001", "bbox": opened["scope"]["bbox"],
        "kind": "illegible", "status": "unresolved", "tentative_text": None,
        "raw_span": None, "resolution": None}
    change = {"action": "add", "annotation": annotation,
        "selection": {"kind": "whole_scope", "pixel_bbox": None},
        "view_sha256": view["view_sha256"], "reason": "operator marks an unreadable source region"}
    authored = _author(service, saved, None, [change], views=[view])
    result = service.compare_v2(saved["pack_id"], expected_pack_sha256=saved["manifest_sha256"],
                                journal=authored["journal"], confirmed=True)
    assert result["comparison"]["reason"] == "reference_uncertain"
    assert result["comparison"]["comparison"] is None
    assert result["comparison"]["coverage"]["unscorable_pairs"] == 1
    reviewed = {"reference": result["reference"], "comparison": result["comparison"], "source_image": view}
    return _save(service, saved, authored, reviewed=reviewed), authored


@pytest.mark.parametrize("journey", ["regions", "hardscan"], indirect=True)
def test_unresolved_save_restart_explicit_resolution_and_dirty_reset_keep_complete_lineage(journey, monkeypatch):
    first, initial = journey["service"], journey["saved"]
    with monkeypatch.context() as patch:
        _block_scores(patch)
        saved, authored = _unresolved(first, initial)
        assert first._store.read(saved["pack_id"])["manifest"]["review_state"] == "historical_unscorable_declaration"
        first.close()
        restarted = journey["restart"]()
        entry = next(item for item in restarted.catalog() if item["manifest_sha256"] == saved["manifest_sha256"])
        assert entry["pack_id"] != saved["pack_id"] and entry["status"] == "present_unverified"
        opened = restarted.open_with_view(entry["pack_id"], preview_profile="dpi288")
        assert opened["draft"]["uncertainty"]["journal"] == authored["journal"]
        assert opened["historical_reviewed"]["comparison"]["comparison"] is None
        assert not {"confirmed", "approval_token", "ticket", "intent_id"} & set(opened)
        with pytest.raises(ValueError):
            restarted.compare_v2(entry["pack_id"], expected_pack_sha256=entry["manifest_sha256"],
                                 journal=authored["journal"], confirmed=False)
        with pytest.raises(ValueError):
            restarted.save_revision(entry["pack_id"], expected_pack_sha256=entry["manifest_sha256"],
                reference="not liable", critical_tokens_text="not liable", verify_current=lambda: True)
        with pytest.raises(ValueError):
            _author(restarted, entry, None, [])
        erased = copy.deepcopy(authored["journal"])
        erased.update(views=[], revisions=[])
        erased["head_sha256"] = history._hash({key: erased[key] for key in
            ("binding", "base", "base_declaration_sha256")})
        # The standalone genesis is internally consistent, but cannot replace
        # the selected parent's retained uncertainty for a transient score.
        with pytest.raises(ValueError):
            restarted.compare_v2(entry["pack_id"], expected_pack_sha256=entry["manifest_sha256"],
                                 journal=erased, confirmed=True)

    view = opened["preview"].raster_view
    after = copy.deepcopy(authored["declaration"]["annotations"][0])
    after.update(status="resolved", resolution={"decision": "reading_confirmed", "reading": "not liable"})
    change = {"action": "resolve", "annotation": after, "selection": None,
              "view_sha256": view["view_sha256"], "reason": "operator explicitly supplies the complete reading"}
    resolved = _author(restarted, entry, authored["journal"], [change], views=[view])
    result = restarted.compare_v2(entry["pack_id"], expected_pack_sha256=entry["manifest_sha256"],
                                  journal=resolved["journal"], confirmed=True)
    assert result["comparison"]["reference_state"] == "resolved"
    assert result["comparison"]["coverage"]["scored_pairs"] == 1
    assert result["comparison"]["comparison"] is not None
    reviewed = {"reference": result["reference"], "comparison": result["comparison"], "source_image": view}
    child = _save(restarted, entry, resolved, reviewed=reviewed)
    snapshot = restarted._store.read_for_revision(child["pack_id"])
    assert snapshot["evidence"]["manifest"]["review_state"] == "historical_scored_declaration"
    assert snapshot["evidence"]["manifest"]["parent_pack_sha256"] == entry["manifest_sha256"]
    assert resolved["journal"]["revisions"][:len(authored["journal"]["revisions"])] == authored["journal"]["revisions"]
    for name, raw in journey["initial"]["files"].items():
        if name != "review.json":
            assert snapshot["pack"]["files"][name] == raw

    # An observed text-away/back draft cannot silently restore the old resolved
    # state, even when the final raw text happens to match its former value.
    dirty = copy.deepcopy(resolved)
    dirty["declaration"]["annotations"][0].update(status="unresolved", raw_span=None, resolution=None)
    draft = _save(restarted, child, dirty, dirty=True)
    count = len(restarted.catalog())
    with pytest.raises(ValueError):
        _save(restarted, draft, resolved)
    assert len(restarted.catalog()) == count
    reset = _author(restarted, draft, resolved["journal"], [], reset=True)
    assert reset["journal"]["revisions"][-1]["reset_anchors"] is True
    assert reset["declaration"]["annotations"][0]["status"] == "unresolved"
    with monkeypatch.context() as patch:
        _block_scores(patch)
        _save(restarted, draft, reset)


@pytest.mark.parametrize("journey", ["regions", "hardscan"], indirect=True)
def test_damaged_proof_recovers_full_authored_journal_without_metrics_or_runtime(journey, monkeypatch):
    current = journey["service"]
    _block_scores(monkeypatch)
    saved, authored = _unresolved(current, journey["saved"])
    folder = current._store._entries[saved["pack_id"]].path
    (folder / "baseline" / "artifacts" / "report.json").write_bytes(b"deliberately damaged generated proof")
    with pytest.raises(ValueError):
        current.open_with_view(saved["pack_id"])
    with pytest.raises(ValueError):
        current.recover_draft(saved["pack_id"])  # An unaware v1 UI must not drop history.
    recovered = current.recover_draft_v2(saved["pack_id"])
    assert recovered["status"] == "unverified_saved_draft"
    assert recovered["draft"]["uncertainty"]["journal"] == authored["journal"]
    assert recovered["draft"]["uncertainty"]["annotations"] == authored["declaration"]["annotations"]
    assert set(recovered) == {"draft", "scope", "pack_sha256", "status", "requires_attention",
                              "canonical_extraction_modified"}
