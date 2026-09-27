"""Fresh coordinator crop comparisons over complete inert-session bundles.

The inherited fixture controls PDF/inference, producer and model-verification
observations. Run/request/report/receipt binding, comparison and private bundle
readback are real; no native OCR accuracy or human-review proof is asserted.
"""

import copy
import math
from types import SimpleNamespace as NS

import pytest

import ocr_review_execution as execution
from ocr_review_runtime import ReviewWorkspace
from test_ocr_detection_disposition_runtime import stack as stack, harness as harness, upstream as upstream
from test_ocr_disposition_bundle_routes import routes as routes
from test_ocr_review_execution import _fake_worker, preview_roots as preview_roots


@pytest.fixture
def pair_host(routes, tmp_path, monkeypatch, preview_roots):
    workspace = ReviewWorkspace(routes.source, routes.recovery_path, tmp_path)
    coordinator = execution.ReviewRunCoordinator(workspace)
    try:
        observed = _fake_worker(monkeypatch)
        host = NS(workspace=workspace, coordinator=coordinator, routes=routes, observed=observed)
        yield host
    finally:
        assert coordinator.close()["cleanup_confirmed"] is True


def launch(host, *, operation="regions", region_id="reference-crop", page=1, dpi=300):
    if operation == "pages":
        options = {"operation": "pages", "pages": [page]}
    else:
        region = {"region_id": region_id, "page_number": page, "bbox": [0., 0., 1., 1.]}
        if operation == "hardscan":
            region["recipe"] = {"orientation_clockwise": 90, "illumination": "none",
                                "bow_fraction": 0., "bow_assumption": "none"}
        options = {"operation": operation, "regions": [region]}
    prepared = host.coordinator.prepare(**options, dpi=dpi)
    view = host.coordinator.start(prepared["intent_id"], prepared["approval_token"], confirmed=True)
    run_id = view["run_id"]
    run = host.coordinator._runs[run_id]
    run.thread.join(15)
    assert not run.thread.is_alive()
    assert host.coordinator.status(run_id)["artifact_state"] == "verified_complete"
    result = host.coordinator.result(run_id)
    return run_id, next(row["item_id"] for row in result["item_ids"] if row["page_number"] == page)


def pair(host, *, retry_operation="regions", retry_page=1):
    return (*launch(host), *launch(host, operation=retry_operation, region_id="retry-crop", page=retry_page))


def compare(host, ids, view, **overrides):
    options = {"expected_pair_sha256": view["pair_sha256"], "reference": view["baseline"]["text"],
               "critical_tokens": [], "confirmed": True}
    options.update(overrides)
    return host.coordinator.compare_crops(*ids, **options)


@pytest.mark.parametrize("operation", ["regions", "hardscan"])
def test_two_actual_inert_bundles_pair_by_source_scope_not_region_name(pair_host, operation):
    host = pair_host
    document = host.workspace.document
    baseline_bytes = host.workspace.recovery_path.read_bytes()
    ids = pair(host, retry_operation=operation)
    view = host.coordinator.crop_pair(*ids)
    assert view["baseline"]["region_id"] == "reference-crop"
    assert view["retry"]["region_id"] == "retry-crop"
    assert view["scope"]["bbox"] == [0., 0., 1., 1.]
    assert view["baseline"]["recipe"] is None
    if operation == "hardscan":
        assert view["retry"]["recipe"]["orientation_clockwise"] == 90
    result = compare(host, ids, view)
    assert result["pair_sha256"] == view["pair_sha256"]
    assert result["reference"]["anchor"]["report_sha256"] == view["baseline"]["report_sha256"]
    assert result["reference"]["anchor"]["region_id"] == "reference-crop"
    metrics = result["comparison"]["comparison"]
    assert metrics["records"][0]["baseline"]["character"]["edit_distance"] == 0
    assert metrics["records"][0]["retry"]["character"]["edit_distance"] == 0
    assert metrics["regression_detected"] is False
    assert result["requires_attention"] is True and result["canonical_extraction_modified"] is False
    assert len(host.observed) == 2 and host.routes.stack.raw.calls == 2
    assert host.workspace.document is document and host.workspace.recovery_path.read_bytes() == baseline_bytes
    # Detached browser data cannot mutate the retained request or comparison.
    view["baseline"]["text"] = "browser mutation"
    view["scope"]["bbox"][0] = .9
    assert host.coordinator.crop_pair(*ids)["scope"]["bbox"] == [0., 0., 1., 1.]


@pytest.mark.parametrize("confirmation", [False, 1, "true", None])
def test_invalid_comparison_confirmation_precedes_snapshot(pair_host, monkeypatch, confirmation):
    host = pair_host
    monkeypatch.setattr(host.coordinator, "_crop_pair_snapshot", lambda *_args: pytest.fail("unexpected readback"))
    with pytest.raises(ValueError, match="review crop comparison is unavailable"):
        host.coordinator.compare_crops("a" * 32, "a", "b" * 32, "b",
            expected_pair_sha256="c" * 64, reference="text", critical_tokens=[], confirmed=confirmation)
    assert not host.observed


def test_whole_page_result_cannot_be_used_as_a_full_page_crop_baseline(pair_host):
    ids = (*launch(pair_host, operation="pages"), *launch(pair_host))
    with pytest.raises(ValueError, match="review crop pair is unavailable"):
        pair_host.coordinator.crop_pair(*ids)


def test_same_rectangle_on_different_physical_pages_is_not_the_same_scope(pair_host):
    ids = pair(pair_host, retry_page=3)
    with pytest.raises(ValueError, match="review crop pair is unavailable"):
        pair_host.coordinator.crop_pair(*ids)


def test_unknown_item_does_not_fall_back_to_first_crop(pair_host):
    ids = list(pair(pair_host))
    ids[1] = "unknown-crop"
    with pytest.raises(ValueError, match="review crop pair is unavailable"):
        pair_host.coordinator.crop_pair(*ids)


def test_changed_captured_pair_refuses_before_scoring(pair_host, monkeypatch):
    import ocr_crop_comparison

    ids = pair(pair_host)
    view = pair_host.coordinator.crop_pair(*ids)
    monkeypatch.setattr(ocr_crop_comparison, "compare_crop_candidates", lambda *_a, **_k: pytest.fail("scored stale pair"))
    with pytest.raises(ValueError, match="review crop comparison is unavailable"):
        compare(pair_host, ids, view, expected_pair_sha256="0" * 64)


@pytest.mark.parametrize("fault", ["manifest", "source", "evicted"])
def test_mutation_during_scoring_cannot_return_a_valid_comparison(pair_host, monkeypatch, fault):
    import ocr_crop_comparison

    host = pair_host
    ids = pair(host)
    view = host.coordinator.crop_pair(*ids)
    original = ocr_crop_comparison.compare_crop_candidates

    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        if fault == "manifest":
            path = host.coordinator._runs[ids[0]].intent.output / "manifest.json"
            path.write_text("PRIVATE changed completion", encoding="utf-8")
        elif fault == "source":
            host.workspace.pdf_path.write_bytes(b"PRIVATE changed source")
        else:
            with host.coordinator._lock:
                del host.coordinator._runs[ids[0]]
        return result

    monkeypatch.setattr(ocr_crop_comparison, "compare_crop_candidates", changed)
    with pytest.raises(ValueError, match="review crop comparison is unavailable") as caught:
        compare(host, ids, view)
    assert "PRIVATE" not in str(caught.value)
    assert len(host.observed) == 2


def test_pair_display_budget_refuses_without_truncating(pair_host, monkeypatch):
    ids = pair(pair_host)
    monkeypatch.setattr(execution, "MAX_RESULT_BYTES", 32)
    with pytest.raises(ValueError, match="review crop pair is unavailable"):
        pair_host.coordinator.crop_pair(*ids)


def test_failed_crop_is_unavailable_not_a_fabricated_empty_prediction(pair_host):
    host = pair_host
    before = launch(host)
    host.routes.settings.modes[1] = "raw_failure"
    after = launch(host, region_id="retry-crop")
    ids = (*before, *after)
    view = host.coordinator.crop_pair(*ids)
    assert view["retry"]["status"] == "retry_failed" and view["retry"]["text"] is None
    result = compare(host, ids, view)
    assert result["comparison"]["comparison"] is None
    assert result["requires_attention"] is True


def test_edited_reference_changes_reference_binding_without_changing_run_pair(pair_host):
    ids = pair(pair_host)
    view = pair_host.coordinator.crop_pair(*ids)
    first = compare(pair_host, ids, view)
    changed = compare(pair_host, ids, copy.deepcopy(view), reference="different reviewed transcription")
    assert changed["pair_sha256"] == first["pair_sha256"]
    assert changed["reference"]["reference_sha256"] != first["reference"]["reference_sha256"]
    assert len(pair_host.observed) == 2


def test_higher_dpi_with_different_rounded_pixel_support_keeps_same_physical_scope(pair_host, monkeypatch):
    import ocr_region_runtime

    host = pair_host
    reader = ocr_region_runtime.RapidOCRRegionReader
    original_candidate = reader._candidate
    side_points = 15.36  # Exactly64px at300DPI, outward-rounded86px at400DPI.

    def fixed_geometry(self, page, bbox):
        scale = self.dpi / 72
        extent = side_points * scale
        bounds = [math.floor(bbox[0] * extent), math.floor(bbox[1] * extent),
                  math.ceil(bbox[2] * extent), math.ceil(bbox[3] * extent)]
        return {"page": {"display_rect_points": [0., 0., side_points, side_points],
                          "cropbox_points": [0., 0., side_points, side_points], "rotation_degrees": 0},
                "raster": {"width": bounds[2] - bounds[0], "height": bounds[3] - bounds[1],
                           "dpi": self.dpi, "coordinate_system": "rendered_image_pixels"},
                "pixel_bounds": bounds,
                "crop_to_page_fraction": [[1 / extent, 0., bounds[0] / extent],
                                          [0., 1 / extent, bounds[1] / extent]],
                "boundary_policy": "clip_to_page_bounds"}

    def scaled_pixels(self, page, **options):
        side = math.ceil(side_points * self.dpi / 72)
        host.routes.stack.pixels = host.routes.stack.np.zeros((side, side, 3), dtype=host.routes.stack.np.uint8)
        return original_candidate(self, page, **options)

    monkeypatch.setattr(reader, "describe_region", fixed_geometry)
    monkeypatch.setattr(reader, "_candidate", scaled_pixels)
    ids = (*launch(host), *launch(host, region_id="higher-resolution", dpi=400))
    view = host.coordinator.crop_pair(*ids)
    assert view["baseline"]["geometry"]["raster"]["width"] == 64
    assert view["retry"]["geometry"]["raster"]["width"] == 86
    assert view["baseline"]["configuration"]["dpi"] == 300
    assert view["retry"]["configuration"]["dpi"] == 400
    result = compare(host, ids, view)
    assert result["comparison"]["comparison"] is not None
    assert host.routes.stack.raw.calls == 2


def test_equal_fraction_rectangles_with_different_page_geometry_are_refused(pair_host):
    # The inherited fixed64px fixture changes physical page size withDPI. This
    # is deliberately unlike the fixed-page positive control above.
    ids = (*launch(pair_host), *launch(pair_host, region_id="different-page-size", dpi=400))
    with pytest.raises(ValueError, match="review crop pair is unavailable"):
        pair_host.coordinator.crop_pair(*ids)
