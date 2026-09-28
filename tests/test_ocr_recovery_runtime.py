"""Recovery runtime contracts exercised without loading OCR models."""

import math
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

import model_artifacts
import ocr_recovery_runtime as runtime


def _box(x=0, y=0, width=20, height=10):
    return [[x, y], [x + width, y], [x + width, y + height], [x, y + height]]


def _output(*, texts=("Case 1",), scores=(0.95,), boxes=None):
    return SimpleNamespace(
        txts=texts, scores=scores,
        boxes=[_box()] if boxes is None else boxes)


class _Rect:
    def __init__(self, width=72, height=36):
        self.width = width
        self.height = height

    def __mul__(self, matrix):
        return _Rect(self.width * matrix[0], self.height * matrix[1])

    @property
    def irect(self):
        return SimpleNamespace(
            width=math.ceil(self.width), height=math.ceil(self.height))


@pytest.fixture
def fake_runtime(monkeypatch):
    state = SimpleNamespace(
        opened=[], closed=0, rendered=[], params=[], verified=[], shapes=[],
        called=[], output=_output(), native="Original text", rect=_Rect(),
        is_pdf=True, needs_pass=False, count=2, failure=None,
        guard_limits=[], prepared=[],
    )

    class Page:
        @property
        def rect(self):
            return state.rect

        def get_text(self, kind, *, sort):
            assert (kind, sort) == ("text", True)
            return state.native

        def get_pixmap(self, *, matrix, colorspace, alpha):
            state.rendered.append((matrix, colorspace, alpha))
            rect = (state.rect * matrix).irect
            return SimpleNamespace(
                width=rect.width, height=rect.height, n=3,
                stride=rect.width * 3,
                samples_mv=memoryview(bytes(rect.width * rect.height * 3)))

    class Document:
        @property
        def is_pdf(self):
            return state.is_pdf

        @property
        def needs_pass(self):
            return state.needs_pass

        def __len__(self):
            return state.count

        def __getitem__(self, page_index):
            state.called.append(page_index)
            return Page()

        def close(self):
            state.closed += 1

    def open_document(path):
        state.opened.append(path)
        return Document()

    class Array:
        def reshape(self, *shape):
            state.shapes.append(shape)
            return self

        def __getitem__(self, key):
            assert key == (slice(None), slice(None), slice(None, None, -1))
            return self

        def copy(self):
            return self

    def frombuffer(samples, *, dtype):
        assert isinstance(samples, memoryview)
        assert dtype == "uint8"
        return Array()

    def verify(package, consumer):
        state.verified.append((package, consumer))
        if state.failure:
            raise state.failure
        return {role: Path(f"verified/{role}.onnx") for role in (
            "detection", "recognition", "classification")}

    class Engine:
        def __init__(self, *, params):
            state.params.append(params)

        def __call__(self, pixels):
            assert isinstance(pixels, Array)
            return state.output

    class Guard:
        """Explicit loader boundary double; native guard is tested separately."""

        def __init__(self, engine, *, max_side, max_pixels):
            assert isinstance(engine, Engine)
            state.guard_limits.append((max_side, max_pixels))
            self.engine = engine

        def prepare(self, pixels):
            assert isinstance(pixels, Array)
            state.prepared.append(pixels)

        def __call__(self, pixels):
            assert state.prepared[-1] is pixels
            return self.engine(pixels)

    monkeypatch.setitem(sys.modules, "pymupdf", SimpleNamespace(
        open=open_document, Matrix=lambda x, y: (x, y), csRGB="RGB"))
    monkeypatch.setitem(sys.modules, "numpy", SimpleNamespace(
        frombuffer=frombuffer, uint8="uint8"))
    monkeypatch.setitem(sys.modules, "rapidocr", SimpleNamespace(
        RapidOCR=Engine, OCRVersion=SimpleNamespace(PPOCRV6="PP-OCRv6")))
    monkeypatch.setitem(sys.modules, "ocr_engine_guard", SimpleNamespace(RapidOCREngineGuard=Guard))
    monkeypatch.setattr(model_artifacts, "verified_installed_package_model", verify)
    monkeypatch.setattr(runtime, "_package_version", lambda _: "3.9.2")
    return state


def test_native_baseline_uses_one_based_pages_without_models(fake_runtime):
    reader = runtime.RapidOCRPageReader(Path("synthetic.pdf"))
    assert not fake_runtime.opened
    with reader:
        assert reader.page_count == 2
        assert reader.native_text(2) == "Original text"
        assert fake_runtime.called == [1]
        assert not fake_runtime.verified
        assert not fake_runtime.rendered
    assert fake_runtime.closed == 1
    assert reader._engine is None


def test_verified_engine_is_lazy_reused_and_keeps_low_scores(fake_runtime):
    fake_runtime.output = _output(
        texts=("second column", "footnote"), scores=(0.9, 0.1),
        boxes=[_box(30, 10), _box(0, 40)])
    with runtime.RapidOCRPageReader(Path("synthetic.pdf")) as reader:
        result = reader.retry(2)
        reader.retry(1)
    assert fake_runtime.verified == [("rapidocr", "docling_ocr")]
    assert len(fake_runtime.params) == 1
    assert fake_runtime.guard_limits == [(6000, 25_000_000)]
    assert len(fake_runtime.prepared) == 2
    params = fake_runtime.params[0]
    assert params["Det.model_path"] == str(Path("verified/detection.onnx"))
    assert params["Rec.model_path"] == str(Path("verified/recognition.onnx"))
    assert params["Cls.model_path"] == str(Path("verified/classification.onnx"))
    assert params["Det.ocr_version"] == params["Rec.ocr_version"] == "PP-OCRv6"
    assert params["Global.max_side_len"] == 6000
    assert params["Global.text_score"] == 0.0
    assert params["Global.font_path"] is None
    assert 1 <= params["EngineConfig.onnxruntime.intra_op_num_threads"] <= 2
    assert params["EngineConfig.onnxruntime.inter_op_num_threads"] == 1
    assert result["text"] == "second column\nfootnote"
    assert math.isclose(result["mean_confidence"], 0.5)
    assert result["raster"] == {
        "width": 300, "height": 150, "dpi": 300,
        "coordinate_system": "rendered_image_pixels"}
    assert result["engine"] == {
        "name": "rapidocr", "version": "3.9.2", "min_score": 0.0,
        "max_side": 6000}
    assert fake_runtime.shapes == [(150, 300, 3), (150, 300, 3)]
    assert fake_runtime.closed == 1


def test_only_explicit_score_threshold_filters_lines(fake_runtime):
    fake_runtime.output = _output(
        texts=("low", "boundary", "high"), scores=(0.1, 0.5, 0.9),
        boxes=[_box(), _box(0, 20), _box(0, 40)])
    with runtime.RapidOCRPageReader(Path("synthetic.pdf"), min_score=0.5) as reader:
        result = reader.retry(1)
    assert result["text"] == "boundary\nhigh"
    assert math.isclose(result["mean_confidence"], 0.7)
    assert fake_runtime.params[0]["Global.text_score"] == 0.5


@pytest.mark.parametrize("output", [
    SimpleNamespace(txts=None, scores=None, boxes=None),
    SimpleNamespace(txts=(), scores=[], boxes=[]),
])
def test_empty_ocr_is_an_explicit_empty_candidate(fake_runtime, output):
    fake_runtime.output = output
    with runtime.RapidOCRPageReader(Path("synthetic.pdf")) as reader:
        result = reader.retry(1)
    assert result["text"] == ""
    assert result["lines"] == []
    assert result["mean_confidence"] is None


@pytest.mark.parametrize("kwargs", [
    {"dpi": True}, {"dpi": 71}, {"dpi": 601}, {"dpi": 300.0},
    {"max_pixels": 0}, {"max_pixels": 100_000_001}, {"max_pixels": False},
    {"max_side": 31}, {"max_side": 12_001}, {"max_side": float("inf")},
    {"min_score": True}, {"min_score": "0.5"}, {"min_score": -0.1},
    {"min_score": 1.1}, {"min_score": float("nan")},
    {"min_score": float("inf")},
    {"min_score": 10 ** 1000},
])
def test_invalid_configuration_never_opens_pdf(fake_runtime, kwargs):
    with pytest.raises(ValueError):
        runtime.RapidOCRPageReader(Path("synthetic.pdf"), **kwargs)
    assert not fake_runtime.opened


@pytest.mark.parametrize("page_number", [0, -1, 3, True, 1.0, "1"])
def test_invalid_page_rejected_before_loading_or_rendering(fake_runtime, page_number):
    with runtime.RapidOCRPageReader(Path("synthetic.pdf")) as reader:
        with pytest.raises(ValueError):
            reader.retry(page_number)
        with pytest.raises(ValueError):
            reader.native_text(page_number)
    assert not fake_runtime.called
    assert not fake_runtime.verified
    assert not fake_runtime.rendered


@pytest.mark.parametrize("rect,kwargs", [
    (_Rect(0, 36), {}), (_Rect(-1, 36), {}),
    (_Rect(float("nan"), 36), {}), (_Rect(72, float("inf")), {}),
    (_Rect(1500, 36), {}), (_Rect(72, 36), {"max_pixels": 44_999}),
    (_Rect(72, 36), {"max_side": 299}),
])
def test_raster_budgets_checked_before_allocation(fake_runtime, rect, kwargs):
    fake_runtime.rect = rect
    with runtime.RapidOCRPageReader(Path("synthetic.pdf"), **kwargs) as reader:
        with pytest.raises(ValueError):
            reader.retry(1)
    assert not fake_runtime.rendered
    assert not fake_runtime.verified


def test_model_verification_failure_prevents_engine_and_render(fake_runtime):
    fake_runtime.failure = model_artifacts.ModelArtifactError("synthetic failure")
    with pytest.raises(model_artifacts.ModelArtifactError):
        with runtime.RapidOCRPageReader(Path("synthetic.pdf")) as reader:
            reader.retry(1)
    assert not fake_runtime.params
    assert not fake_runtime.rendered
    assert fake_runtime.closed == 1


@pytest.mark.parametrize("output", [
    object(), SimpleNamespace(txts=[], scores=[], boxes=None),
    _output(texts="string"), _output(scores={"score": 0.95}),
    _output(scores=[]), _output(texts=(123,)),
    _output(scores=(float("nan"),)), _output(scores=(float("inf"),)),
    _output(scores=(True,)), _output(scores=("0.95",)),
    _output(scores=(10 ** 1000,)),
    _output(scores=(-0.1,)), _output(scores=(1.1,)),
    _output(boxes=[[[0, 0], [1, 0], [1, 1]]]),
    _output(boxes=[[[0, 0, 1], [1, 0], [1, 1], [0, 1]]]),
    _output(boxes=[_box(-1)]), _output(boxes=[_box(299)]),
    _output(boxes=[_box(y=149)]), _output(boxes=[_box(width=0)]),
    _output(boxes=[_box(float("nan"))]),
    _output(boxes=[[[False, 0], [1, 0], [1, 1], [0, 1]]]),
])
def test_malformed_engine_output_fails_closed_and_closes_pdf(fake_runtime, output):
    fake_runtime.output = output
    with pytest.raises(ValueError):
        with runtime.RapidOCRPageReader(Path("synthetic.pdf")) as reader:
            reader.retry(1)
    assert fake_runtime.closed == 1


def test_invalid_low_confidence_box_is_not_hidden_by_threshold(fake_runtime):
    fake_runtime.output = _output(scores=(0.1,), boxes=[_box(-1)])
    with runtime.RapidOCRPageReader(Path("synthetic.pdf"), min_score=0.5) as reader:
        with pytest.raises(ValueError, match="outside"):
            reader.retry(1)


@pytest.mark.parametrize("field,value", [
    ("is_pdf", False), ("needs_pass", True), ("count", 0),
])
def test_invalid_pdf_closes_even_when_enter_fails(fake_runtime, field, value):
    setattr(fake_runtime, field, value)
    with pytest.raises(ValueError):
        with runtime.RapidOCRPageReader(Path("synthetic.pdf")):
            pytest.fail("invalid PDF must not enter context")
    assert fake_runtime.closed == 1


def test_reader_requires_context_and_rejects_nested_enter(fake_runtime):
    reader = runtime.RapidOCRPageReader(Path("synthetic.pdf"))
    for operation in (lambda: reader.page_count, lambda: reader.native_text(1),
                      lambda: reader.retry(1)):
        with pytest.raises(RuntimeError, match="with block"):
            operation()
    with reader:
        with pytest.raises(RuntimeError, match="already open"):
            reader.__enter__()
        assert reader.page_count == 2
    reader.close()
    assert fake_runtime.closed == 1


def test_pdf_cleanup_preserves_original_exception(fake_runtime):
    with pytest.raises(KeyboardInterrupt):
        with runtime.RapidOCRPageReader(Path("synthetic.pdf")):
            raise KeyboardInterrupt()
    assert fake_runtime.closed == 1


def test_actual_pymupdf_raster_api_uses_synthetic_page(tmp_path, monkeypatch):
    pymupdf = pytest.importorskip("pymupdf")
    np = pytest.importorskip("numpy")
    path = tmp_path / "synthetic.pdf"
    with pymupdf.open() as document:
        page = document.new_page(width=72.1, height=36.1)
        page.draw_rect((0, 0, 5, 5), fill=(1, 0, 0), color=None)
        page.insert_text((3, 18), "Case 1", fontsize=10)
        document.save(path)
    shapes = []

    def load_engine(reader):
        reader._engine_version = "synthetic"

        def engine(pixels):
            assert isinstance(pixels, np.ndarray)
            assert pixels.flags.c_contiguous
            assert pixels[0, 0].tolist() == [0, 0, 255]
            shapes.append(pixels.shape)
            return _output()

        engine.prepare = lambda pixels: None
        return engine

    monkeypatch.setattr(runtime.RapidOCRPageReader, "_load_engine", load_engine)
    with runtime.RapidOCRPageReader(path) as reader:
        assert reader.page_count == 1
        assert "Case 1" in reader.native_text(1)
        result = reader.retry(1)
    assert shapes == [(151, 301, 3)]
    assert result["raster"]["width"] == 301
    assert result["raster"]["height"] == 151


def test_actual_rapidocr_output_arrays_are_supported():
    pytest.importorskip("rapidocr")
    np = pytest.importorskip("numpy")
    from rapidocr.utils.output import RapidOCROutput

    output = RapidOCROutput(
        txts=("Case 1",), scores=(np.float32(0.95),),
        boxes=np.array([_box()], dtype=np.float32))
    lines = runtime._validated_lines(
        output, width=300, height=150, min_score=0.0)
    assert lines[0]["text"] == "Case 1"
    assert lines[0]["score"] == pytest.approx(0.95)
    assert lines[0]["box"] == _box()
