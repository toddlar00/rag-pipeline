"""Reader allocation boundaries on generated pixels; no OCR model loads."""

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from ocr_engine_limits import EngineAllocationLimit
from ocr_execution_receipt import ExecutionRecorder
from ocr_hardscan_runtime import RapidOCRHardScanReader
from ocr_recovery_runtime import RapidOCRPageReader
from ocr_region_runtime import RapidOCRRegionReader


def _recipe(angle=0):
    return {"orientation_clockwise": angle, "bow_fraction": 0.0,
            "bow_assumption": "none", "illumination": "none"}


@contextmanager
def _reader(reader_type, *, width=72, height=36, **kwargs):
    fitz = pytest.importorskip("pymupdf")
    document = fitz.open()
    page = document.new_page(width=width, height=height)
    # MuPDF substitutes a 1x1-point page for sub-point dimensions. Never let
    # that fixture fallback masquerade as the requested pathological geometry.
    assert (page.rect.width, page.rect.height) == (width, height)
    reader = reader_type(Path("unused-generated-in-memory.pdf"), **kwargs)
    reader._document = document
    reader._pymupdf = fitz
    reader._page_count = 1
    try:
        yield reader
    finally:
        reader.close()


def _retry(reader, route, angle=0):
    if route == "page":
        return reader.retry(1)
    if route == "region":
        return reader.retry_region(1, [0, 0, 1, 1])
    return reader.retry_hardscan(1, [0, 0, 1, 1], _recipe(angle))


ROUTES = [("page", RapidOCRPageReader), ("region", RapidOCRRegionReader),
          ("hardscan", RapidOCRHardScanReader)]


@pytest.mark.parametrize("route,reader_type,max_side", [
    (route, reader_type, max_side)
    for route, reader_type in ROUTES for max_side in (6000, 12000)
    if route != "hardscan" or max_side == 6000
])
@pytest.mark.parametrize("vertical", [False, True])
def test_thin_source_rejected_before_model_load_or_render(
        monkeypatch, route, reader_type, vertical, max_side):
    width, height = max_side * 72 / 300, 1.0
    if vertical:
        width, height = height, width
    with _reader(reader_type, width=width, height=height,
                 max_side=max_side, max_pixels=25_000_000 if route == "hardscan" else 100_000_000) as reader:
        monkeypatch.setattr(reader, "_load_engine", lambda: pytest.fail("unsafe engine load"))
        monkeypatch.setattr(reader._pymupdf.Page, "get_pixmap",
                            lambda *_a, **_k: pytest.fail("unsafe render"))
        # Description is still usable during cohort planning; the failure is
        # inside this individual retry, not a whole-report planning failure.
        if route == "region":
            assert reader.describe_region(1, [0, 0, 1, 1])["raster"]["dpi"] == 300
        elif route == "hardscan":
            assert reader.describe_hardscan(1, [0, 0, 1, 1], _recipe())["transform"]["eligibility"] == "ready"
        with pytest.raises(ValueError):
            _retry(reader, route)


@pytest.mark.parametrize("angle", [0, 90, 180, 270])
def test_hardscan_rotation_checks_declared_processed_shape_before_render(monkeypatch, angle):
    with _reader(RapidOCRHardScanReader, width=1440, height=1.0) as reader:
        monkeypatch.setattr(reader, "_load_engine", lambda: pytest.fail("unsafe engine load"))
        monkeypatch.setattr(reader._pymupdf.Page, "get_pixmap",
                            lambda *_a, **_k: pytest.fail("unsafe render"))
        with pytest.raises(ValueError):
            _retry(reader, "hardscan", angle)


class _PreparedEngine:
    """Explicit fake engine for reader/recorder composition, not native OCR."""

    def __init__(self, *, fail_prepare=False):
        self.events = []
        self.fail_prepare = fail_prepare

    def prepare(self, pixels):
        self.events.append(("prepare", pixels.shape))
        if self.fail_prepare:
            raise ValueError("injected allocation limit")

    def __call__(self, pixels):
        self.events.append(("call", pixels.shape))
        height, width = pixels.shape[:2]
        return SimpleNamespace(txts=["synthetic"], scores=[0.1],
                               boxes=[[[0, 0], [width, 0], [width, height], [0, height]]])


@pytest.mark.parametrize("route,reader_type", ROUTES)
def test_admitted_input_prepared_before_recorded_call_and_original_candidate_preserved(
        monkeypatch, route, reader_type):
    pytest.importorskip("numpy")
    if route == "hardscan":
        pytest.importorskip("cv2")
    engine = _PreparedEngine()

    def load(reader):
        reader._engine_version = "synthetic"
        reader._engine = engine
        return engine

    monkeypatch.setattr(reader_type, "_load_engine", load)
    recorder = ExecutionRecorder()
    recorded_type = recorder.reader_factory(reader_type)
    with _reader(recorded_type) as reader:
        # This test isolates invocation accounting, not native session evidence.
        monkeypatch.setattr(reader, "execution_observation", lambda: None)
        result = _retry(reader, route)
        candidate = result if route == "page" else result["candidate"]
        assert candidate["text"] == "synthetic"
        assert candidate["lines"][0]["score"] == 0.1
        assert candidate["engine"]["min_score"] == 0.0
        assert candidate["raster"]["width"] == 300
        assert candidate["raster"]["height"] == 150
    assert [event[0] for event in engine.events] == ["prepare", "call"]
    assert len(recorder.calls) == 1 and recorder.calls[0]["status"] == "completed"


@pytest.mark.parametrize("route,reader_type", ROUTES)
def test_preparation_failure_is_not_recorded_as_an_actual_ocr_call(monkeypatch, route, reader_type):
    pytest.importorskip("numpy")
    if route == "hardscan":
        pytest.importorskip("cv2")
    engine = _PreparedEngine(fail_prepare=True)

    def load(reader):
        reader._engine = engine
        return engine

    monkeypatch.setattr(reader_type, "_load_engine", load)
    recorder = ExecutionRecorder()
    with _reader(recorder.reader_factory(reader_type)) as reader:
        monkeypatch.setattr(reader, "execution_observation", lambda: None)
        with pytest.raises(ValueError, match="injected allocation limit"):
            _retry(reader, route)
    assert [event[0] for event in engine.events] == ["prepare"]
    assert recorder.calls == []


@pytest.mark.parametrize("angle", [0.0, 4.0])
def test_page_guard_uses_actual_validated_preprocessing_dimensions(monkeypatch, angle):
    pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    import ocr_preprocessing

    engine = _PreparedEngine()

    def estimate(*_args):
        return {"status": "applied" if angle else "skipped",
                "reason": "rotation_applied" if angle else "blank_or_low_ink",
                "angle_degrees": angle, "estimated_angle_degrees": angle if angle else None,
                "gain": 1.0 if angle else None}

    def load(reader):
        reader._engine_version = "synthetic"
        reader._engine = engine
        return engine

    monkeypatch.setattr(ocr_preprocessing, "_estimate_deskew", estimate)
    monkeypatch.setattr(RapidOCRPageReader, "_load_engine", load)
    recorder = ExecutionRecorder()
    with _reader(recorder.reader_factory(RapidOCRPageReader), width=1440, height=1,
                 preprocessing="deskew") as reader:
        monkeypatch.setattr(reader, "execution_observation", lambda: None)
        if not angle:
            with pytest.raises(EngineAllocationLimit):
                reader.retry(1)
        else:
            # Real rotation/metadata validation, but a declared estimator and
            # fake recognizer: this tests shape routing, not deskew accuracy.
            candidate = reader.retry(1)
            metadata = candidate["preprocessing"]
            assert metadata["original_raster"] == {"width": 6000, "height": 5}
            assert metadata["processed_raster"] == {"width": 5986, "height": 424}
            assert engine.events == [("prepare", (424, 5986, 3)), ("call", (424, 5986, 3))]
    assert len(recorder.calls) == bool(angle)
    if not angle:
        assert engine.events == []


@pytest.mark.parametrize("stage", ["detection", "full_page", "gold_crop"])
@pytest.mark.parametrize("during_call", [False, True])
@pytest.mark.parametrize("cancel", [False, True])
def test_stage_allocation_failure_and_cancellation_preserve_actual_call_accounting(
        monkeypatch, stage, during_call, cancel):
    np = pytest.importorskip("numpy")
    from ocr_stage_runtime import RapidOCRStageReader, StageRecorder

    failure = KeyboardInterrupt() if cancel else EngineAllocationLimit("synthetic bound")

    class Engine:
        text_det = text_cls = text_rec = staticmethod(lambda *_a, **_k: None)

        def prepare(self, _pixels, **flags):
            assert flags == {"use_det": stage != "gold_crop", "use_cls": stage == "full_page",
                             "use_rec": stage != "detection"}
            if not during_call:
                raise failure

        def __call__(self, _pixels, **_flags):
            raise failure

    recorder = StageRecorder()
    reader = RapidOCRStageReader(b"generated stand-in; document is never opened", recorder=recorder)
    monkeypatch.setattr(reader, "_load_engine", lambda: Engine())

    def invoke():
        return reader.stage(np.zeros((100, 100, 3), np.uint8), stage=stage, page_number=1,
                            region_id="line-1" if stage == "gold_crop" else None)

    if cancel:
        with pytest.raises(KeyboardInterrupt):
            invoke()
    else:
        result = invoke()
        assert result["status"] == "unavailable" and result["reason"] == "resource_limit"
        assert result["call_id"] == ("call-0001" if during_call else None)
    assert len(recorder.calls) == during_call
    if during_call:
        assert recorder.calls[0]["status"] == "failed"
        assert all(count == {"attempted": 0, "completed": 0, "failed": 0}
                   for count in recorder.calls[0]["roles"].values())


def test_loaded_execution_source_inventory_includes_shared_guard_and_policy():
    import hashlib
    import ocr_engine_guard
    import ocr_engine_limits
    from ocr_execution_receipt import _loaded_source_digests

    sources = _loaded_source_digests()
    for module in (ocr_engine_guard, ocr_engine_limits):
        assert sources[module.__name__] == hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
