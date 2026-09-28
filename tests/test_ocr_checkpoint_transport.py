"""Exercise the distinct page/region report transport bounds without OCR."""

import hashlib

import pytest

import ocr_checkpoint_io as storage
import ocr_region_checkpoint as regions


def test_region_report_transport_crosses_page_limit_and_adopts_exact_bytes(tmp_path, monkeypatch):
    # This is storage-layer JSON, deliberately independent of report semantics.
    # Actual bytes cross the legacy 64 MiB ceiling; no numeric limits are patched.
    payload = {"transport": "é" * (32 * 1024 * 1024)}
    request = {"identity": {}}
    monkeypatch.setattr(storage, "capture_identity", lambda **_kwargs: {})
    region_dir, page_dir = tmp_path / "regions", tmp_path / "pages"
    region_dir.mkdir()
    page_dir.mkdir()
    region = storage._Journal(region_dir, [], request, None, profile="regions")
    page = storage._Journal(page_dir, [], request, None)

    raw = regions.report_bytes(payload)
    assert 64 * 1024 * 1024 < len(raw) < 128 * 1024 * 1024
    expected = hashlib.sha256(raw).hexdigest()
    assert regions.report_digest(payload) == expected
    del raw
    assert region.put("report.json", payload) == expected
    loaded, digest = region.read("report.json")
    assert digest == expected and regions.report_same(loaded, payload)
    del loaded
    report = region_dir / "report.json"
    before = report.stat()
    assert region.put("report.json", payload, adopt=True) == expected
    after = report.stat()
    assert (after.st_ino, after.st_size, after.st_mtime_ns) == (
        before.st_ino, before.st_size, before.st_mtime_ns)

    with pytest.raises(ValueError, match="checkpoint JSON exceeds its byte budget"):
        page.put("report.json", payload)
    assert list(page_dir.iterdir()) == []
    page_read = storage._Journal(region_dir, [], request, None)
    with pytest.raises(ValueError, match="checkpoint artifact must be a bounded single-linked regular file"):
        page_read.read("report.json")
    assert report.stat().st_mtime_ns == before.st_mtime_ns
