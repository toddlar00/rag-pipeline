"""Live capture boundary over real bundles with explicitly inert OCR/models.

This verifies raw export capture, not historical pack replay or native accuracy.
The inherited stack controls PDF, inference, producer and model observations.
"""

import hashlib

import pytest

import ocr_crop_review_pack as packs
import ocr_detection_disposition_io as disposition_io
from test_ocr_detection_disposition_runtime import stack as stack, harness as harness, upstream as upstream
from test_ocr_disposition_bundle_routes import routes as routes
from test_ocr_review_execution import preview_roots as preview_roots
from test_ocr_review_crop_comparison import pair_host as pair_host, pair


@pytest.mark.parametrize("operation", ["regions", "hardscan"])
def test_capture_keeps_exact_raw_evidence_without_pdf_model_or_new_call(pair_host, operation):
    host = pair_host
    ids = pair(host, retry_operation=operation)
    view = host.coordinator.crop_pair(*ids)
    source_before = host.workspace.pdf_path.read_bytes()
    captured = host.coordinator.capture_crop_archives(*ids, expected_pair_sha256=view["pair_sha256"])
    assert set(captured) == {"pair", "baseline", "retry"}
    assert captured["pair"] == view
    for side, run_id in (("baseline", ids[0]), ("retry", ids[2])):
        archive = captured[side]
        run = host.coordinator._runs[run_id]
        assert set(archive["artifacts"]) == set(packs.ARTIFACT_LIMITS)
        assert set(archive["retained_inputs"]) == set(packs.INPUT_LIMITS) - {"installation.json"}
        for name, raw in archive["artifacts"].items():
            assert type(raw) is bytes and raw == (run.intent.output / name).read_bytes()
        assert archive["artifacts"]["report.json"] == archive["artifacts"]["work/report.json"]
        assert hashlib.sha256(archive["artifacts"]["report.json"]).hexdigest() == view[side]["report_sha256"]
        assert archive["retained_inputs"]["recovery.json"] == host.workspace.recovery_path.read_bytes()
        assert archive["retained_inputs"]["plan.json"] == run.intent.plan_path.read_bytes()
        assert not any(name.endswith((".pdf", ".onnx", ".py")) for values in archive.values() for name in values)
    assert host.workspace.pdf_path.read_bytes() == source_before
    assert len(host.observed) == host.routes.stack.raw.calls == 2
    captured["pair"]["baseline"]["text"] = "detached output edit"
    assert host.coordinator.crop_pair(*ids)["baseline"]["text"] != "detached output edit"


@pytest.mark.parametrize("token", [None, 1, "PRIVATE_BAD_TOKEN", "a" * 63])
def test_invalid_pair_token_refuses_before_any_read(pair_host, monkeypatch, token):
    monkeypatch.setattr(pair_host.coordinator, "_crop_pair_snapshot", lambda *_a: pytest.fail("unexpected read"))
    with pytest.raises(ValueError, match="review crop archive is unavailable") as caught:
        pair_host.coordinator.capture_crop_archives("a", "b", "c", "d", expected_pair_sha256=token)
    assert "PRIVATE" not in str(caught.value)
    assert not pair_host.observed


def test_changed_pair_token_refuses_before_raw_capture(pair_host, monkeypatch):
    ids = pair(pair_host)
    captured = pair_host.coordinator._crop_pair_snapshot(*ids)
    monkeypatch.setattr(pair_host.coordinator, "_crop_pair_snapshot", lambda *_a: captured)
    monkeypatch.setattr(disposition_io, "_snapshot", lambda *_a, **_k: pytest.fail("captured stale raw bytes"))
    with pytest.raises(ValueError, match="review crop archive is unavailable"):
        pair_host.coordinator.capture_crop_archives(*ids, expected_pair_sha256="0" * 64)


def test_combined_budget_preflight_precedes_retaining_raw_archives(pair_host, monkeypatch):
    ids = pair(pair_host)
    captured = pair_host.coordinator._crop_pair_snapshot(*ids)
    monkeypatch.setattr(pair_host.coordinator, "_crop_pair_snapshot", lambda *_a: captured)
    monkeypatch.setattr(packs, "MAX_PACK_BYTES", packs.MAX_MANIFEST_BYTES + packs.MAX_REVIEW_BYTES + 1)
    monkeypatch.setattr(disposition_io, "_snapshot", lambda *_a, **_k: pytest.fail("retained oversized archive"))
    with pytest.raises(ValueError, match="review crop archive is unavailable"):
        pair_host.coordinator.capture_crop_archives(*ids, expected_pair_sha256=captured[0]["pair_sha256"])
    assert len(pair_host.observed) == 2


@pytest.mark.parametrize("fault", ["input_after_read", "completion_after_snapshot", "evicted_after_capture", "closed_after_capture"])
def test_capture_drift_fails_without_returning_exportable_bytes(pair_host, monkeypatch, fault):
    host = pair_host
    ids = pair(host)
    captured = host.coordinator._crop_pair_snapshot(*ids)
    monkeypatch.setattr(host.coordinator, "_crop_pair_snapshot", lambda *_a: captured)
    plan = host.coordinator._runs[ids[0]].intent.plan_path
    original = disposition_io._snapshot
    changed = False

    def snapshot(path, limit, **options):
        nonlocal changed
        result = original(path, limit, **options)
        if fault == "input_after_read" and path == plan and options.get("retain") and not changed:
            changed = True
            plan.write_bytes(b"PRIVATE changed input")
        return result

    monkeypatch.setattr(disposition_io, "_snapshot", snapshot)
    if fault == "completion_after_snapshot":
        (host.coordinator._runs[ids[0]].intent.output / "manifest.json").write_bytes(b'{"PRIVATE":true}')
    elif fault in ("evicted_after_capture", "closed_after_capture"):
        def recheck(_runs, _bundles):
            if fault == "evicted_after_capture":
                del host.coordinator._runs[ids[0]]
            else:
                host.coordinator._closed = True
        monkeypatch.setattr(host.coordinator, "_recheck_crop_pair", recheck)
    with pytest.raises(ValueError, match="review crop archive is unavailable") as caught:
        host.coordinator.capture_crop_archives(*ids, expected_pair_sha256=captured[0]["pair_sha256"])
    assert "PRIVATE" not in str(caught.value)
    assert len(host.observed) == 2


def test_closed_host_cannot_begin_archive_capture(pair_host, monkeypatch):
    pair_host.coordinator._closed = True
    monkeypatch.setattr(pair_host.coordinator, "_crop_pair_snapshot", lambda *_a: pytest.fail("read closed host"))
    with pytest.raises(ValueError, match="review crop archive is unavailable"):
        pair_host.coordinator.capture_crop_archives("a", "b", "c", "d", expected_pair_sha256="0" * 64)


def test_source_change_during_final_raw_recheck_refuses_capture(pair_host, monkeypatch):
    host = pair_host
    ids = pair(host)
    captured = host.coordinator._crop_pair_snapshot(*ids)
    monkeypatch.setattr(host.coordinator, "_crop_pair_snapshot", lambda *_a: captured)
    original_recheck = host.coordinator._recheck_crop_pair
    original_bound = disposition_io._bound_snapshot
    final_loop = False
    changed = False

    def pair_recheck(*args):
        nonlocal final_loop
        result = original_recheck(*args)
        final_loop = True
        return result

    def bound(path, limit):
        nonlocal changed
        result = original_bound(path, limit)
        if final_loop and not changed:
            changed = True
            host.workspace.pdf_path.write_bytes(b"PRIVATE changed during final archive checks")
        return result

    monkeypatch.setattr(host.coordinator, "_recheck_crop_pair", pair_recheck)
    monkeypatch.setattr(disposition_io, "_bound_snapshot", bound)
    with pytest.raises(ValueError, match="review crop archive is unavailable") as caught:
        host.coordinator.capture_crop_archives(*ids, expected_pair_sha256=captured[0]["pair_sha256"])
    assert changed and "PRIVATE" not in str(caught.value)
    assert len(host.observed) == 2
