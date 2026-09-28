"""Preprocessing integration contracts using synthetic readers and pixel arrays."""

from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

import artifact_io
import ocr_recovery as recovery
import ocr_recovery_runtime as runtime
from tools import retry_ocr as cli


ORIGINAL = "Original synthetic page text is retained for independent review."
SOURCE_BYTES = b"Synthetic source generation; parsed only by the fake reader."
DIGEST = hashlib.sha256(SOURCE_BYTES).hexdigest()


def _candidate():
    text = "Synthetic retry text"
    return {
        "text": text,
        "lines": [{"text": text, "score": 0.95,
                   "box": [[0, 0], [20, 0], [20, 10], [0, 10]]}],
        "mean_confidence": 0.95,
        "raster": {"width": 72, "height": 36, "dpi": 72,
                   "coordinate_system": "rendered_image_pixels"},
        "engine": {"name": "rapidocr", "version": "synthetic",
                   "min_score": 0.0, "max_side": 6000},
    }


def _metadata(mode="contrast", *, rotated=False):
    forward = inverse = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
    width, height = 72, 36
    deskew = {
        "status": "disabled", "reason": "mode_disabled", "angle_degrees": 0.0,
        "estimated_angle_degrees": None, "gain": None,
    }
    if "deskew" in mode:
        deskew.update(status="skipped", reason="blank_or_low_ink")
    if rotated:
        from ocr_preprocessing import _rotation_geometry

        width, height, forward, inverse = _rotation_geometry(72, 36, 1.0)
        deskew.update(
            status="applied", reason="rotation_applied", angle_degrees=1.0,
            estimated_angle_degrees=1.0, gain=0.2)
    contrast = {
        "status": "disabled", "reason": "mode_disabled", "low_level": None, "high_level": None,
    }
    if "contrast" in mode:
        contrast.update(status="skipped", reason="already_high_contrast", low_level=0, high_level=255)
    return {
        "schema_version": 1,
        "algorithm": "bounded-deskew-contrast-v1",
        "mode": mode,
        "original_raster": {"width": 72, "height": 36},
        "processed_raster": {"width": width, "height": height},
        "source_to_processed": copy.deepcopy(forward),
        "processed_to_source": copy.deepcopy(inverse),
        "deskew": deskew,
        "contrast": contrast,
        "parameters": {
            "thumbnail_max_side": 1000, "max_angle_degrees": 5.0,
            "angle_step_degrees": 0.25, "min_gain": 0.025,
            "low_percentile": 0.5, "high_percentile": 99.5,
        },
        "libraries": {"opencv": "synthetic", "numpy": "synthetic"},
    }


def _processed_candidate(mode="contrast"):
    candidate = _candidate()
    candidate["preprocessing"] = _metadata(mode)
    candidate["raster"]["coordinate_system"] = "preprocessed_image_pixels"
    return candidate


class _Reader:
    page_count = 1

    def __init__(self, candidate=None):
        self.candidate = _candidate() if candidate is None else candidate
        self.options = None
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True
        return False

    def native_text(self, page_number):
        assert page_number == 1
        return ORIGINAL

    def retry(self, page_number):
        assert page_number == 1
        if isinstance(self.candidate, BaseException):
            raise self.candidate
        return self.candidate


def _factory(reader):
    def create(path, **kwargs):
        assert Path(path).read_bytes() == SOURCE_BYTES
        reader.options = kwargs
        return reader
    return create


@pytest.fixture
def source_files(tmp_path, monkeypatch):
    source, output = tmp_path / "synthetic.pdf", tmp_path / "review.json"
    source.write_bytes(SOURCE_BYTES)
    actual_snapshot = artifact_io.immutable_file_snapshot

    def snapshot(path, **kwargs):
        return actual_snapshot(path, scratch_root=tmp_path / "scratch", **kwargs)

    monkeypatch.setattr(recovery.artifact_io, "immutable_file_snapshot", snapshot)
    return source, output


def test_default_policy_keeps_v1_candidate_shape_and_reader_options(source_files):
    source, output = source_files
    reader = _Reader()
    policy = recovery.RetryPolicy(dpi=72)
    report = recovery.recover_pdf(
        source, output, policy=policy, requested_pages=(1,), reader_factory=_factory(reader))

    assert policy.preprocessing == "none"
    assert report["schema_version"] == 1
    assert reader.options == {"dpi": 72, "max_pixels": 25_000_000, "max_side": 6000}
    assert "preprocessing" not in report["retry_configuration"]
    assert report["pages"][0]["candidate"] == _candidate()
    assert "preprocessing" not in report["pages"][0]["candidate"]
    assert report["pages"][0]["original_text"] == ORIGINAL
    assert source.read_bytes() == SOURCE_BYTES
    assert json.loads(output.read_bytes()) == report
    assert reader.closed


@pytest.mark.parametrize("mode", ["deskew", "contrast", "deskew-contrast"])
def test_enabled_recovery_publishes_v2_preserves_source_and_refuses_overwrite(source_files, mode):
    source, output = source_files
    reader = _Reader(_processed_candidate(mode))
    policy = recovery.RetryPolicy(dpi=72, preprocessing=mode)
    report = recovery.recover_pdf(
        source, output, policy=policy, requested_pages=(1,), reader_factory=_factory(reader))

    assert report["schema_version"] == 2
    assert report["retry_configuration"]["preprocessing"] == mode
    assert reader.options == {
        "dpi": 72, "max_pixels": 25_000_000, "max_side": 6000, "preprocessing": mode,
    }
    assert report["pages"][0]["status"] == "review_required"
    assert report["pages"][0]["candidate"] == _processed_candidate(mode)
    assert report["pages"][0]["original_text"] == ORIGINAL
    assert report["source_sha256"] == DIGEST
    assert report["canonical_extraction_modified"] is False
    assert report["accuracy_verified"] is False
    assert source.read_bytes() == SOURCE_BYTES
    published = output.read_bytes()
    assert json.loads(published) == report
    assert reader.closed

    def unexpected(*args, **kwargs):
        pytest.fail("an existing report must fail before the reader starts")

    with pytest.raises(FileExistsError):
        recovery.recover_pdf(source, output, policy=policy, reader_factory=unexpected)
    assert output.read_bytes() == published
    assert source.read_bytes() == SOURCE_BYTES


@pytest.mark.parametrize("mode", [None, True, False, 1, 0.0, [], {}, "", "DESKEW", "auto", "deskew contrast"])
def test_policy_rejects_invalid_preprocessing_types_and_modes(mode):
    with pytest.raises(ValueError):
        recovery.RetryPolicy(preprocessing=mode)


@pytest.mark.parametrize("mode", ["none", "deskew", "contrast", "deskew-contrast"])
def test_cli_forwards_preprocessing_policy_without_exposing_source(monkeypatch, capsys, mode):
    calls = []

    def recover(*args, **kwargs):
        calls.append((args, kwargs))
        return {"summary": {"selected": 1, "deferred": 0, "review_required": 1,
                            "empty": 0, "failed": 0}}

    monkeypatch.setattr(cli, "recover_pdf", recover)
    assert cli.main([
        "--pdf", "PRIVATE_SOURCE.pdf", "--output", "PRIVATE_REPORT.json",
        "--pages", "1", "--preprocess", mode,
    ]) == 0
    args, kwargs = calls[0]
    assert args == (Path("PRIVATE_SOURCE.pdf"), Path("PRIVATE_REPORT.json"))
    assert kwargs["policy"].preprocessing == mode
    assert kwargs["requested_pages"] == (1,)
    public = capsys.readouterr()
    assert "PRIVATE_SOURCE" not in public.out + public.err
    assert "PRIVATE_REPORT" not in public.out + public.err


def test_cli_rejects_unknown_preprocessing_before_recovery(monkeypatch):
    monkeypatch.setattr(cli, "recover_pdf", lambda *args, **kwargs: pytest.fail("invalid mode ran recovery"))
    with pytest.raises(SystemExit) as exc:
        cli.main(["--pdf", "source.pdf", "--output", "review.json", "--preprocess", "auto"])
    assert exc.value.code == 2


class _Rect:
    def __init__(self, width=72, height=36):
        self.width, self.height = width, height

    def __mul__(self, matrix):
        return _Rect(self.width * matrix[0], self.height * matrix[1])

    @property
    def irect(self):
        return SimpleNamespace(width=math.ceil(self.width), height=math.ceil(self.height))


@pytest.fixture
def pixel_runtime(monkeypatch):
    np = pytest.importorskip("numpy")
    state = SimpleNamespace(events=[], closed=0, processed=None, engine_pixels=None)
    state.rgb = np.empty((36, 72, 3), dtype=np.uint8)
    state.rgb[:] = [11, 22, 33]
    state.box = [[0, 0], [20, 0], [20, 10], [0, 10]]

    class Page:
        rect = _Rect()

        def get_text(self, kind, *, sort):
            assert (kind, sort) == ("text", True)
            return ORIGINAL

        def get_pixmap(self, *, matrix, colorspace, alpha):
            assert (matrix, colorspace, alpha) == ((1.0, 1.0), "RGB", False)
            state.events.append("render")
            return SimpleNamespace(
                width=72, height=36, n=3, stride=72 * 3,
                samples_mv=memoryview(state.rgb.tobytes()))

    class Document:
        is_pdf, needs_pass = True, False

        def __len__(self):
            return 1

        def __getitem__(self, page_index):
            assert page_index == 0
            return Page()

        def close(self):
            state.closed += 1

    def load_engine(reader):
        reader._engine_version = "synthetic"

        def engine(pixels):
            state.events.append("ocr")
            state.engine_pixels = pixels
            return SimpleNamespace(txts=["Synthetic retry text"], scores=[0.95], boxes=[state.box])

        engine.prepare = lambda pixels: None
        return engine

    monkeypatch.setitem(sys.modules, "pymupdf", SimpleNamespace(
        open=lambda _: Document(), Matrix=lambda x, y: (x, y), csRGB="RGB"))
    monkeypatch.setattr(runtime.RapidOCRPageReader, "_load_engine", load_engine)
    return state


def test_default_runtime_keeps_bgr_pixels_and_original_candidate_space(pixel_runtime, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("default runtime must not preprocess")

    monkeypatch.setitem(sys.modules, "ocr_preprocessing", SimpleNamespace(preprocess_image=unexpected))
    with runtime.RapidOCRPageReader(Path("synthetic.pdf"), dpi=72) as reader:
        candidate = reader.retry(1)
    assert pixel_runtime.events == ["render", "ocr"]
    assert pixel_runtime.engine_pixels[0, 0].tolist() == [33, 22, 11]
    assert candidate == _candidate()
    assert pixel_runtime.closed == 1


def test_runtime_preprocesses_bgr_before_ocr_and_uses_expanded_coordinates(pixel_runtime, monkeypatch):
    import numpy as np
    import ocr_preprocessing

    metadata = _metadata("deskew", rotated=True)
    width, height = metadata["processed_raster"]["width"], metadata["processed_raster"]["height"]
    assert (width, height) == (73, 38)
    processed = np.full((height, width, 3), 99, dtype=np.uint8)
    pixel_runtime.box = [[70, 0], [73, 0], [73, 10], [70, 10]]

    def preprocess(pixels, **kwargs):
        pixel_runtime.events.append("preprocess")
        assert kwargs == {"mode": "deskew", "max_pixels": 25_000_000, "max_side": 6000}
        assert pixels.shape == (36, 72, 3)
        assert pixels[0, 0].tolist() == [33, 22, 11]
        return processed, metadata

    monkeypatch.setattr(ocr_preprocessing, "preprocess_image", preprocess)
    with runtime.RapidOCRPageReader(Path("synthetic.pdf"), dpi=72, preprocessing="deskew") as reader:
        candidate = reader.retry(1)
    assert pixel_runtime.events == ["render", "preprocess", "ocr"]
    assert pixel_runtime.engine_pixels is processed
    assert pixel_runtime.rgb[0, 0].tolist() == [11, 22, 33]
    assert candidate["raster"] == {
        "width": 73, "height": 38, "dpi": 72, "coordinate_system": "preprocessed_image_pixels",
    }
    assert candidate["lines"][0]["box"] == pixel_runtime.box
    assert candidate["preprocessing"] == metadata
    assert recovery._candidate_snapshot(candidate, preprocessing="deskew") == candidate
    assert pixel_runtime.closed == 1


@pytest.mark.parametrize("failure", [ValueError("PRIVATE_PROCESSING_DETAIL"), RuntimeError("PRIVATE_PROCESSING_DETAIL")])
def test_preprocessing_failure_keeps_original_and_never_calls_ocr(pixel_runtime, monkeypatch, failure):
    import ocr_preprocessing

    def fail(*args, **kwargs):
        pixel_runtime.events.append("preprocess")
        raise failure

    monkeypatch.setattr(ocr_preprocessing, "preprocess_image", fail)
    with runtime.RapidOCRPageReader(Path("synthetic.pdf"), dpi=72, preprocessing="contrast") as reader:
        report = recovery.build_recovery_report(
            reader, source_sha256=DIGEST,
            policy=recovery.RetryPolicy(dpi=72, preprocessing="contrast"), requested_pages=(1,))
    page = report["pages"][0]
    assert page["original_text"] == ORIGINAL
    assert page["status"] == "retry_failed"
    assert page["candidate"] is None
    assert page["error_code"] == (
        "retry_limit_or_validation" if isinstance(failure, ValueError) else "retry_runtime_failed")
    assert "PRIVATE_PROCESSING_DETAIL" not in json.dumps(report)
    assert pixel_runtime.events == ["render", "preprocess"]
    assert pixel_runtime.engine_pixels is None
    assert pixel_runtime.closed == 1


def test_preprocessing_cancellation_propagates_and_closes_reader(pixel_runtime, monkeypatch):
    import ocr_preprocessing

    def cancel(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(ocr_preprocessing, "preprocess_image", cancel)
    with pytest.raises(KeyboardInterrupt):
        with runtime.RapidOCRPageReader(Path("synthetic.pdf"), dpi=72, preprocessing="contrast") as reader:
            recovery.build_recovery_report(
                reader, source_sha256=DIGEST,
                policy=recovery.RetryPolicy(dpi=72, preprocessing="contrast"), requested_pages=(1,))
    assert pixel_runtime.engine_pixels is None
    assert pixel_runtime.closed == 1


@pytest.mark.parametrize("mutation", ["missing", "wrong_mode", "unknown_field", "wrong_dimensions", "nonfinite_transform", "wrong_space"])
def test_processed_candidate_rejects_missing_or_malformed_metadata(mutation):
    candidate = _processed_candidate()
    if mutation == "missing":
        del candidate["preprocessing"]
    elif mutation == "wrong_mode":
        candidate["preprocessing"] = _metadata("deskew")
    elif mutation == "unknown_field":
        candidate["preprocessing"]["source_text"] = "PRIVATE_SOURCE_TEXT"
    elif mutation == "wrong_dimensions":
        candidate["preprocessing"]["processed_raster"]["width"] = 71
    elif mutation == "nonfinite_transform":
        candidate["preprocessing"]["source_to_processed"][0][0] = float("nan")
    else:
        candidate["raster"]["coordinate_system"] = "rendered_image_pixels"
    with pytest.raises(ValueError):
        recovery._candidate_snapshot(candidate, preprocessing="contrast")


def test_default_candidate_rejects_unexpected_preprocessing_metadata():
    candidate = _candidate()
    candidate["preprocessing"] = _metadata()
    with pytest.raises(ValueError):
        recovery._candidate_snapshot(candidate)


@pytest.mark.parametrize("mutation", ["wrong_mode", "wrong_source_dimensions"])
def test_runtime_rejects_preprocessing_metadata_before_ocr(pixel_runtime, monkeypatch, mutation):
    import ocr_preprocessing

    metadata = _metadata("deskew" if mutation == "wrong_mode" else "contrast")
    if mutation == "wrong_source_dimensions":
        metadata["original_raster"]["width"] = 71

    monkeypatch.setattr(ocr_preprocessing, "preprocess_image", lambda pixels, **kwargs: (pixels, metadata))
    with pytest.raises(ValueError):
        with runtime.RapidOCRPageReader(Path("synthetic.pdf"), dpi=72, preprocessing="contrast") as reader:
            reader.retry(1)
    assert pixel_runtime.engine_pixels is None
    assert pixel_runtime.closed == 1
