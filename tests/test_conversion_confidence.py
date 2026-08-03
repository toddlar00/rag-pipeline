"""Tests for advisory Docling confidence surfacing."""

from contextlib import ExitStack
import itertools
import logging
import math
import sys
from types import ModuleType, SimpleNamespace

import rag


def _page(low="GOOD"):
    return SimpleNamespace(low_grade=SimpleNamespace(name=low))


def _confidence(mean_score=0.8, low_score=0.4, pages=None):
    return SimpleNamespace(
        mean_score=mean_score, low_score=low_score,
        mean_grade=SimpleNamespace(name="GOOD"),
        low_grade=SimpleNamespace(name="FAIR"),
        pages=pages if pages is not None else {0: _page(), 1: _page()},
    )


def test_confidence_summary_maps_scores_and_poor_pages():
    metrics, poor = rag._confidence_summary(_confidence(
        pages={0: _page("POOR"), 1: _page("GOOD"), 2: _page("POOR")}))
    assert metrics["confidence_pages"] == 3
    assert metrics["confidence_poor_pages"] == 2
    assert metrics["confidence_mean_score"] == 0.8
    assert poor == [0, 2]


def test_confidence_summary_nan_guards_scores():
    metrics, poor = rag._confidence_summary(
        _confidence(mean_score=math.nan, low_score=math.nan, pages={}))
    assert metrics["confidence_mean_score"] is None
    assert metrics["confidence_low_score"] is None
    assert metrics["confidence_pages"] == 0
    assert poor == []


def test_confidence_summary_inf_guards_scores():
    metrics, poor = rag._confidence_summary(
        _confidence(mean_score=math.inf, low_score=-math.inf, pages={}))
    assert metrics["confidence_mean_score"] is None
    assert metrics["confidence_low_score"] is None
    assert metrics["confidence_pages"] == 0
    assert poor == []


def test_confidence_summary_non_float_score_is_empty():
    metrics, poor = rag._confidence_summary(
        _confidence(mean_score="bad", low_score="bad", pages={}))
    assert metrics["confidence_mean_score"] is None
    assert metrics["confidence_low_score"] is None
    assert poor == []


def test_confidence_summary_absent_is_empty():
    assert rag._confidence_summary(None) == ({}, [])
    assert rag._confidence_summary(object()) == ({}, [])


class _RecordingTelemetry:
    def __init__(self):
        self.calls = []

    def stage_observation(self, stage, *, metrics=None):
        self.calls.append((stage, metrics))


class _PoisonedTelemetry:
    """A telemetry collaborator whose observation call always fails."""

    def stage_observation(self, stage, *, metrics=None):
        raise RuntimeError("telemetry exploded")


def _install_fake_docling(monkeypatch, *, fake_confidence):
    """Wire a fake Docling converter module set into ``rag`` for one run."""
    class AcceleratorDevice:
        CPU = "cpu"
        CUDA = "cuda"

    class PipelineOptions:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class FakePdfFormatOption:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeDoclingDocument:
        def model_dump_json(self, indent=2):
            return "{}"

        def export_to_markdown(self):
            return "markdown"

    class FakeResult:
        document = FakeDoclingDocument()
        confidence = fake_confidence

    class FakeDocumentConverter:
        def __init__(self, **kwargs):
            pass

        def convert(self, path):
            return FakeResult()

    document_converter = ModuleType("docling.document_converter")
    document_converter.DocumentConverter = FakeDocumentConverter
    document_converter.PdfFormatOption = FakePdfFormatOption
    pipeline_options = ModuleType("docling.datamodel.pipeline_options")
    pipeline_options.PdfPipelineOptions = PipelineOptions
    pipeline_options.ThreadedPdfPipelineOptions = PipelineOptions
    accelerator_options = ModuleType(
        "docling.datamodel.accelerator_options")
    accelerator_options.AcceleratorDevice = AcceleratorDevice
    accelerator_options.AcceleratorOptions = object
    base_models = ModuleType("docling.datamodel.base_models")
    base_models.InputFormat = SimpleNamespace(PDF="pdf")

    monkeypatch.setitem(
        sys.modules, "docling.document_converter", document_converter)
    monkeypatch.setitem(
        sys.modules, "docling.datamodel.pipeline_options", pipeline_options)
    monkeypatch.setitem(
        sys.modules, "docling.datamodel.accelerator_options",
        accelerator_options)
    monkeypatch.setitem(
        sys.modules, "docling.datamodel.base_models", base_models)

    monkeypatch.setattr(
        rag, "_detect_gpu", lambda: (AcceleratorDevice.CPU, 1, "CPU"))
    monkeypatch.setattr(
        rag, "_pin_docling_layout_revision", lambda _options: None)
    monkeypatch.setattr(
        rag, "_configure_docling_model_artifacts",
        lambda *args, **kwargs: "fake-artifacts-root")

    # Avoid real-clock flakiness in the elapsed-time division (tqdm also
    # reads the clock, so this must never be exhausted): a monotonically
    # increasing fake clock guarantees a nonzero elapsed duration.
    _clock = itertools.count(1000.0, 0.1)
    monkeypatch.setattr(rag.time, "time", lambda: next(_clock))


def test_convert_pdf_generation_surfaces_confidence(monkeypatch, tmp_path, caplog):
    source = tmp_path / "book.pdf"
    source.write_bytes(b"pdf")
    output = tmp_path / "book.json"

    monkeypatch.setattr(rag, "_page_count", lambda _path: 2)

    fake_confidence = _confidence(
        pages={0: _page("POOR"), 1: _page("GOOD")})
    _install_fake_docling(monkeypatch, fake_confidence=fake_confidence)

    original = rag.ConversionInputBinding(
        kind="original", name=source.name, sha256="0" * 64,
        size=source.stat().st_size,
    )
    telemetry = _RecordingTelemetry()

    with ExitStack() as snapshots, caplog.at_level(
            logging.INFO, logger="rag"):
        rag._convert_pdf_generation(
            source, output,
            snapshot_stack=snapshots,
            original_input=original,
            backend="auto",
            auto_preprocess=False,
            ocr=False,
            telemetry=telemetry,
        )

    assert "GOOD" in caplog.text
    assert "FAIR" in caplog.text
    assert "Docling low-confidence pages" in caplog.text
    assert telemetry.calls == [("convert_confidence", {
        "confidence_pages": 2,
        "confidence_poor_pages": 1,
        "confidence_mean_score": 0.8,
        "confidence_low_score": 0.4,
    })]


def test_convert_pdf_generation_confidence_failure_does_not_abort(
        monkeypatch, tmp_path, caplog):
    """A poisoned telemetry collaborator must not discard the conversion."""
    source = tmp_path / "book.pdf"
    source.write_bytes(b"pdf")
    output = tmp_path / "book.json"

    monkeypatch.setattr(rag, "_page_count", lambda _path: 2)

    fake_confidence = _confidence(
        pages={0: _page("POOR"), 1: _page("GOOD")})
    _install_fake_docling(monkeypatch, fake_confidence=fake_confidence)

    original = rag.ConversionInputBinding(
        kind="original", name=source.name, sha256="0" * 64,
        size=source.stat().st_size,
    )
    telemetry = _PoisonedTelemetry()

    with ExitStack() as snapshots, caplog.at_level(
            logging.WARNING, logger="rag"):
        effective_input = rag._convert_pdf_generation(
            source, output,
            snapshot_stack=snapshots,
            original_input=original,
            backend="auto",
            auto_preprocess=False,
            ocr=False,
            telemetry=telemetry,
        )

    assert effective_input is original
    assert output.exists()
    assert "Confidence surfacing skipped" in caplog.text
