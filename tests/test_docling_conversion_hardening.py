"""Docling conversion hardening.

Docling 2.121 turns a stage exception into ``PARTIAL_SUCCESS`` with an empty
page instead of raising, reads OMP_NUM_THREADS and DOCLING_* variables into
its runtime options, never reads ``DOCLING_PDF_BACKEND``, and emits one DEBUG
profiling record per page and stage.  These tests pin how conversion treats
each of those: artifacts and receipts stay byte-identical, an incomplete
conversion fails closed before any Docling output is written, the runtime
knobs are explicit, the log names the backend Docling really uses, and
progress comes from the profiling records without flooding worker logs.
"""

from contextlib import ExitStack, contextmanager, nullcontext
import importlib.util
import inspect
import io
import itertools
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
from types import ModuleType, SimpleNamespace

import pytest
import tqdm

import ingestion_core
import rag


_PIPELINE_LOGGER = "docling.pipeline.standard_pdf_pipeline"
# Docling 2.121's exact profiling template (ThreadedPipelineStage).
_PROFILING_TEMPLATE = (
    "PIPELINE_PROFILING Stage %s: run_id=%d pages=%s start=%.3f end=%.3f "
    "duration=%.3fs")
_STAGES = ("preprocess", "layout", "layout_postprocess", "table", "assemble")
_COMMON_OPTIONS = {
    "do_ocr": False,
    "generate_page_images": False,
    "generate_picture_images": False,
    "do_picture_classification": False,
}
_DOCUMENT_JSON = '{\n  "name": "synthetic"\n}'
_DOCUMENT_MARKDOWN = "# Synthetic heading\n\nSynthetic body."
# Stands in for source text that a Docling error message may quote.
_PRIVATE_TEXT = "SYNTHETIC-PRIVATE-SOURCE-TEXT"
_HOSTILE_ENV = {
    "OMP_NUM_THREADS": "8",
    "DOCLING_NUM_THREADS": "9",
    "DOCLING_DEVICE": "cuda",
    "DOCLING_INFERENCE_COMPILE_TORCH_MODELS": "true",
    "DOCLING_DEBUG_VISUALIZE_LAYOUT": "true",
    "DOCLING_PERF_PAGE_BATCH_SIZE": "7",
}


class FakeParseBackend:
    """Stands in for Docling's default DoclingParseDocumentBackend."""


class _FakeSettings:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class BatchConcurrencySettings(_FakeSettings):
    pass


class DebugSettings(_FakeSettings):
    pass


class InferenceSettings(_FakeSettings):
    pass


class _FakeDocling:
    """Fake Docling modules that record what conversion builds and when."""

    def __init__(self, monkeypatch, *, pages=3, gpu=False):
        self.pages = pages
        self.status = "success"
        self.errors = []
        self.on_convert = None
        self.convert_error = None
        self.events = []
        self.options = []
        self.accelerators = []
        self.scopes = []
        self.format_options = []
        self.in_scope = False
        harness = self

        class AcceleratorDevice:
            CPU = "cpu"
            CUDA = "cuda"

        class AcceleratorOptions:
            def __init__(self, **kwargs):
                harness.accelerators.append(kwargs)
                self.__dict__.update(kwargs)

        def options_class(name):
            def __init__(self, **kwargs):
                harness.options.append((name, kwargs, harness.in_scope))
                self.__dict__.update(kwargs)
            return type(name, (), {"__init__": __init__})

        class PdfFormatOption:
            backend = FakeParseBackend

            def __init__(self, **kwargs):
                harness.format_options.append(kwargs)
                self.kwargs = kwargs

        class Document:
            def model_dump_json(self, indent=2):
                assert indent == 2
                return _DOCUMENT_JSON

            def export_to_markdown(self):
                return _DOCUMENT_MARKDOWN

        class DocumentConverter:
            def __init__(self, **kwargs):
                harness.events.append("converter")

            def convert(self, path):
                harness.events.append(("convert", harness.in_scope))
                if harness.on_convert is not None:
                    harness.on_convert()
                if harness.convert_error is not None:
                    raise harness.convert_error
                return SimpleNamespace(
                    document=Document(), status=harness.status,
                    errors=list(harness.errors), confidence=None)

        @contextmanager
        def scoped(**kwargs):
            harness.scopes.append(kwargs)
            harness.events.append("scope-enter")
            harness.in_scope = True
            try:
                yield
            finally:
                harness.in_scope = False
                harness.events.append("scope-exit")

        modules = {
            "docling.document_converter": dict(
                DocumentConverter=DocumentConverter,
                PdfFormatOption=PdfFormatOption),
            "docling.datamodel.pipeline_options": dict(
                PdfPipelineOptions=options_class("PdfPipelineOptions"),
                ThreadedPdfPipelineOptions=options_class(
                    "ThreadedPdfPipelineOptions")),
            "docling.datamodel.accelerator_options": dict(
                AcceleratorDevice=AcceleratorDevice,
                AcceleratorOptions=AcceleratorOptions),
            "docling.datamodel.base_models": dict(
                InputFormat=SimpleNamespace(PDF="pdf")),
            "docling.datamodel.settings": dict(
                scoped=scoped,
                BatchConcurrencySettings=BatchConcurrencySettings,
                DebugSettings=DebugSettings,
                InferenceSettings=InferenceSettings),
        }
        for name, attributes in modules.items():
            module = ModuleType(name)
            module.__dict__.update(attributes)
            monkeypatch.setitem(sys.modules, name, module)
        device = AcceleratorDevice.CUDA if gpu else AcceleratorDevice.CPU
        monkeypatch.setattr(
            rag, "_detect_gpu",
            lambda: (device, 8, "Synthetic GPU" if gpu else "cpu"))
        monkeypatch.setattr(rag, "_page_count", lambda _path: pages)
        monkeypatch.setattr(
            rag, "_pin_docling_layout_revision", lambda _options: None)
        monkeypatch.setattr(
            rag, "_configure_docling_model_artifacts",
            lambda *_args, **_kwargs: "synthetic-artifacts")
        if gpu:
            monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(
                cuda=SimpleNamespace(
                    max_memory_allocated=lambda _index: 0,
                    get_device_properties=lambda _index: SimpleNamespace(
                        total_memory=8 * 1024**3))))
        # A monotonically increasing clock keeps the pages/sec division safe.
        clock = itertools.count(1000.0, 0.5)
        monkeypatch.setattr(rag.time, "time", lambda: next(clock))

    def run(self, tmp_path, **kwargs):
        source = tmp_path / "book.pdf"
        source.write_bytes(b"%PDF-1.4 synthetic source")
        original = rag.ConversionInputBinding(
            kind="original", name=source.name, sha256="0" * 64,
            size=source.stat().st_size)
        kwargs.setdefault("auto_preprocess", False)
        kwargs.setdefault("ocr", False)
        with ExitStack() as snapshots:
            return rag._convert_pdf_generation(
                source, tmp_path / "book.json", snapshot_stack=snapshots,
                original_input=original, **kwargs)


def _error_item(page_no=2, message=_PRIVATE_TEXT, *, module="table",
                category="inference_failure", component="model"):
    """A fake Docling ErrorItem; its enums expose ``value`` like Docling's."""
    return SimpleNamespace(
        component_type=SimpleNamespace(value=component),
        module_name=module, error_message=message,
        category=SimpleNamespace(value=category), page_no=page_no)


def _emit_profiling(batches, *, run_id=1):
    """Log Docling's per-stage profiling records for each page batch."""
    logger = logging.getLogger(_PIPELINE_LOGGER)
    for batch in batches:
        for stage in _STAGES:
            logger.debug(
                _PROFILING_TEMPLATE, stage, run_id, list(batch), 1.0, 1.25,
                0.25)


class _RecordingBar:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.n = 0
        self.updates = []
        self.closed = False

    def update(self, count=1):
        self.n += count
        self.updates.append(count)

    def close(self):
        self.closed = True


def _record_bars(monkeypatch):
    bars = []

    def factory(*args, **kwargs):
        bars.append(_RecordingBar(*args, **kwargs))
        return bars[-1]

    monkeypatch.setattr(tqdm, "tqdm", factory)
    return bars


class _Records(logging.Handler):
    def __init__(self):
        super().__init__(logging.NOTSET)
        self.records = []

    def emit(self, record):
        self.records.append(record)


@contextmanager
def _root_capture(level):
    """A NOTSET handler on a root logger at *level*, as ``rag.main`` uses."""
    root = logging.getLogger()
    handler = _Records()
    previous = root.level
    root.addHandler(handler)
    root.setLevel(level)
    try:
        yield handler.records
    finally:
        root.removeHandler(handler)
        root.setLevel(previous)


@contextmanager
def _fails_closed():
    # Expect RuntimeError first so pre-change code fails with "DID NOT RAISE".
    with pytest.raises(RuntimeError) as raised:
        yield raised
    assert isinstance(
        raised.value, ingestion_core.DoclingConversionIncompleteError)


@pytest.fixture(autouse=True)
def _pipeline_logger_state():
    """Keep one test's Docling logger changes from leaking into the next."""
    logger = logging.getLogger(_PIPELINE_LOGGER)
    state = (logger.level, list(logger.filters), list(logger.handlers))
    yield
    logger.setLevel(state[0])
    logger.filters[:] = state[1]
    logger.handlers[:] = state[2]


@pytest.fixture
def no_backend_variable(monkeypatch):
    # Register the variable with monkeypatch before removing it so teardown
    # also removes any value the code under test writes.
    monkeypatch.setenv("DOCLING_PDF_BACKEND", "sentinel")
    monkeypatch.delenv("DOCLING_PDF_BACKEND")


# --- Characterization: behavior every change must preserve -----------------


@pytest.mark.parametrize(
    "status", ["success", SimpleNamespace(value="success")])
def test_successful_conversion_writes_the_same_artifacts(
        monkeypatch, tmp_path, status):
    docling = _FakeDocling(monkeypatch)
    docling.status = status

    effective = docling.run(tmp_path)

    assert effective.kind == "original"
    assert (tmp_path / "book.json").read_text(
        encoding="utf-8") == _DOCUMENT_JSON
    assert (tmp_path / "book_docling.md").read_text(
        encoding="utf-8") == _DOCUMENT_MARKDOWN


def test_cpu_options_keep_their_shared_settings(monkeypatch, tmp_path):
    docling = _FakeDocling(monkeypatch)

    docling.run(tmp_path)

    [(name, kwargs, _)] = docling.options
    assert name == "PdfPipelineOptions"
    assert {key: value for key, value in kwargs.items()
            if key != "accelerator_options"} == _COMMON_OPTIONS
    [format_kwargs] = docling.format_options
    assert set(format_kwargs) == {"pipeline_options"}


def test_gpu_options_keep_their_batch_settings(monkeypatch, tmp_path):
    docling = _FakeDocling(monkeypatch, gpu=True)

    docling.run(tmp_path, batch_size_override=6)

    [(name, kwargs, _)] = docling.options
    assert name == "ThreadedPdfPipelineOptions"
    kwargs = dict(kwargs)
    assert kwargs.pop("accelerator_options").device == "cuda"
    assert kwargs == {
        "layout_batch_size": 6, "table_batch_size": 4,
        "ocr_batch_size": 6, "queue_max_size": 8, **_COMMON_OPTIONS}


def test_raised_out_of_memory_keeps_the_batch_size_exit(
        monkeypatch, tmp_path, caplog):
    # Docling raises ConversionError (a RuntimeError carrying every error
    # message) for a complete FAILURE, and pipeline-init OOM propagates raw.
    docling = _FakeDocling(monkeypatch, gpu=True)
    docling.convert_error = RuntimeError("CUDA out of memory (synthetic)")

    with caplog.at_level(logging.ERROR, logger="rag"), \
            pytest.raises(SystemExit) as raised:
        docling.run(tmp_path, batch_size_override=8)

    assert raised.value.code == 1
    assert "Retry with: --batch-size 4" in caplog.text
    assert not (tmp_path / "book.json").exists()


def test_other_converter_errors_propagate_unchanged(monkeypatch, tmp_path):
    docling = _FakeDocling(monkeypatch)
    error = RuntimeError("synthetic converter failure")
    docling.convert_error = error

    with pytest.raises(RuntimeError) as raised:
        docling.run(tmp_path)

    assert raised.value is error
    assert not (tmp_path / "book.json").exists()


def test_gpu_path_does_not_warn_about_batch_size(
        monkeypatch, tmp_path, caplog):
    docling = _FakeDocling(monkeypatch, gpu=True)

    with caplog.at_level(logging.WARNING, logger="rag"):
        docling.run(tmp_path, batch_size_override=4)

    assert "--batch-size" not in caplog.text


def test_verbose_logging_still_shows_every_pipeline_record(
        monkeypatch, tmp_path):
    docling = _FakeDocling(monkeypatch, pages=2)
    docling.on_convert = lambda: _emit_profiling([[1], [2]])

    with _root_capture(logging.DEBUG) as records:
        docling.run(tmp_path)

    messages = [record.getMessage() for record in records
                if record.name == _PIPELINE_LOGGER]
    assert messages == [
        _PROFILING_TEMPLATE % (stage, 1, [page], 1.0, 1.25, 0.25)
        for page in (1, 2) for stage in _STAGES]


def test_interactive_progress_keeps_tqdm_defaults(monkeypatch, tmp_path):
    docling = _FakeDocling(monkeypatch, pages=2)
    bars = _record_bars(monkeypatch)
    monkeypatch.setattr(sys, "stderr", SimpleNamespace(
        isatty=lambda: True, write=lambda _text: None, flush=lambda: None))

    docling.run(tmp_path)

    [bar] = bars
    assert bar.kwargs == {
        "total": 2, "desc": "Converting", "unit": "pg",
        "bar_format": ("{l_bar}{bar}| {n_fmt}/{total_fmt} pages "
                       "[{elapsed}<{remaining}, {rate_fmt}]")}
    assert bar.n == 2
    assert bar.closed


# --- (a) Fail closed on an incomplete conversion ---------------------------


@pytest.mark.parametrize("status", [
    SimpleNamespace(value="partial_success"), "partial_success"])
def test_partial_success_fails_closed_before_any_docling_output(
        monkeypatch, tmp_path, caplog, status):
    docling = _FakeDocling(monkeypatch, pages=4)
    docling.status = status
    docling.errors = [_error_item(page_no=2)]
    source = tmp_path / "book.pdf"
    source.write_bytes(b"%PDF-1.4 synthetic source")
    document = tmp_path / "book.json"
    markdown = tmp_path / "book_docling.md"
    manifest = rag._artifact_completion_path(document, stage="conversion")

    with caplog.at_level(logging.DEBUG, logger="rag"), \
            _fails_closed() as raised:
        rag.convert_pdf(source, document, backend="auto",
                        auto_preprocess=False, ocr=False)

    message = str(raised.value)
    assert "partial_success" in message
    assert "[2]" in message
    assert _PRIVATE_TEXT not in message
    assert _PRIVATE_TEXT not in caplog.text
    assert not document.exists()
    assert not markdown.exists()
    assert not manifest.exists()


@pytest.mark.parametrize(("status", "errors"), [
    ("failure", [_error_item(page_no=None)]),
    ("skipped", []),
    ("pending", []),
    (None, []),
    # Docling never reports SUCCESS with errors; if it did, fail closed.
    ("success", [_error_item(page_no=1)]),
])
def test_any_other_outcome_than_a_clean_success_fails_closed(
        monkeypatch, tmp_path, status, errors):
    docling = _FakeDocling(monkeypatch)
    docling.status = status
    docling.errors = errors

    with _fails_closed():
        docling.run(tmp_path)

    assert not (tmp_path / "book.json").exists()
    assert not (tmp_path / "book_docling.md").exists()


def test_gpu_memory_exhaustion_logs_batch_size_guidance(
        monkeypatch, tmp_path, caplog):
    docling = _FakeDocling(monkeypatch, gpu=True)
    docling.status = "partial_success"
    docling.errors = [_error_item(
        page_no=3, message=f"CUDA out of memory near {_PRIVATE_TEXT}")]

    with caplog.at_level(logging.INFO, logger="rag"), \
            _fails_closed() as raised:
        docling.run(tmp_path, batch_size_override=8)

    assert "memory" in str(raised.value)
    assert "Retry with: --batch-size 4" in caplog.text
    assert _PRIVATE_TEXT not in str(raised.value) + caplog.text


def test_cpu_memory_exhaustion_gives_no_batch_size_advice(
        monkeypatch, tmp_path, caplog):
    docling = _FakeDocling(monkeypatch)
    docling.status = "partial_success"
    docling.errors = [_error_item(page_no=1, message="std::bad_alloc")]

    with caplog.at_level(logging.INFO, logger="rag"), \
            _fails_closed() as raised:
        docling.run(tmp_path)

    assert "memory" in str(raised.value)
    assert "--batch-size" not in caplog.text


def _evidence(page_no=2, *, module="table", category="inference_failure",
              message="synthetic"):
    return ingestion_core.conversion_error_evidence(
        page_no=page_no, component="model", module=module,
        category=category, message=message)


def test_only_a_clean_success_is_publishable():
    assert ingestion_core.docling_conversion_failure("success", ()) is None
    for status in ("partial_success", "failure", "skipped", "pending", None,
                   "SUCCESS"):
        assert ingestion_core.docling_conversion_failure(
            status, ()) is not None
    assert ingestion_core.docling_conversion_failure(
        "success", (_evidence(),)) is not None


def test_failure_summary_is_bounded_and_content_free():
    errors = [_evidence(page, message=f"{_PRIVATE_TEXT} {page}")
              for page in range(25, 0, -1)]
    errors += [_evidence(3), _evidence(None, module="StandardPdfPipeline",
                                      category="timeout")]
    errors += [_evidence(1, module=f"stage{index}") for index in range(9)]

    summary = ingestion_core.docling_conversion_failure(
        "partial_success", errors)

    assert summary == (
        "Docling conversion incomplete (status partial_success; 36 errors; "
        "25 affected pages: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, "
        "15, 16, 17, 18, 19, 20 (+5 more)]; 1 document-level error; "
        "sources: StandardPdfPipeline/timeout, stage0/inference_failure, "
        "stage1/inference_failure, stage2/inference_failure, "
        "stage3/inference_failure, stage4/inference_failure, "
        "stage5/inference_failure, stage6/inference_failure (+3 more)). "
        "No Docling JSON, Markdown or conversion manifest was written.")
    assert _PRIVATE_TEXT not in summary


def test_failure_summary_bounds_each_identifier_to_one_log_token():
    summary = ingestion_core.docling_conversion_failure(
        "partial\nsuccess", [_evidence(
            1, module="m" * 100, category="line\nbreak text")])

    assert "\n" not in summary
    assert "status partial?success" in summary
    assert f"sources: {'m' * 64}/line?break?text)" in summary
    assert "1 affected page: [1]" in summary


def test_memory_exhaustion_is_detected_without_keeping_the_message():
    exhausted = [_evidence(message=message) for message in (
        "CUDA out of memory. Tried to allocate 2.00 GiB",
        "std::bad_alloc", "Out Of Memory")]
    other = _evidence(message="index out of range")

    assert all(item.memory_exhausted for item in exhausted)
    assert not other.memory_exhausted
    assert set(vars(other)) == {
        "page_no", "component", "module", "category", "memory_exhausted"}
    summary = ingestion_core.docling_conversion_failure(
        "partial_success", exhausted + [other])
    assert "memory exhausted in 3 errors" in summary
    assert "Tried to allocate" not in summary


# --- (b) Pin the runtime knobs ---------------------------------------------


@pytest.mark.parametrize("gpu", [False, True])
def test_accelerator_options_are_explicit(monkeypatch, tmp_path, gpu):
    for name, value in _HOSTILE_ENV.items():
        monkeypatch.setenv(name, value)
    docling = _FakeDocling(monkeypatch, gpu=gpu)

    docling.run(tmp_path)

    expected = {"num_threads": 4, "device": "cuda" if gpu else "cpu"}
    assert docling.accelerators == [expected]
    [(_, kwargs, _)] = docling.options
    assert vars(kwargs["accelerator_options"]) == expected


def test_default_settings_scope_covers_options_and_conversion(
        monkeypatch, tmp_path):
    docling = _FakeDocling(monkeypatch)

    docling.run(tmp_path)

    [scope] = docling.scopes
    assert {name: (type(value), value.kwargs)
            for name, value in scope.items()} == {
        "perf": (BatchConcurrencySettings, {}),
        "debug": (DebugSettings, {}),
        "inference": (InferenceSettings, {}),
    }
    [(_, _, constructed_in_scope)] = docling.options
    assert constructed_in_scope
    assert docling.events == [
        "scope-enter", "converter", ("convert", True), "scope-exit"]


_ENV_PROBE = """
import json
import os
import sys
from contextlib import ExitStack
from pathlib import Path

import rag
from docling.datamodel import settings as docling_settings
from docling.datamodel.accelerator_options import AcceleratorDevice

source = Path(sys.argv[1])
observed = {}


class Captured(Exception):
    pass


def capture(options, **_kwargs):
    accelerator = options.accelerator_options
    observed.update(
        num_threads=accelerator.num_threads,
        device=str(getattr(accelerator.device, "value", accelerator.device)),
        compile_model=options.layout_options.engine_options.compile_model,
        visualize_layout=docling_settings.settings.debug.visualize_layout,
        page_batch_size=docling_settings.settings.perf.page_batch_size,
        backend_variable="DOCLING_PDF_BACKEND" in os.environ)
    raise Captured


rag._detect_gpu = lambda: (AcceleratorDevice.CPU, 4, "cpu")
rag._page_count = lambda _path: 1
rag._pin_docling_layout_revision = lambda _options: None
rag._configure_docling_model_artifacts = capture
original = rag.ConversionInputBinding(
    kind="original", name=source.name, sha256="0" * 64,
    size=source.stat().st_size)
try:
    with ExitStack() as snapshots:
        rag._convert_pdf_generation(
            source, source.with_suffix(".json"), snapshot_stack=snapshots,
            original_input=original, auto_preprocess=False, ocr=False)
except Captured:
    pass
observed["visualize_layout_after"] = (
    docling_settings.settings.debug.visualize_layout)
print(json.dumps(observed, sort_keys=True))
"""


@pytest.mark.skipif(importlib.util.find_spec("docling") is None,
                    reason="requires the locked Docling runtime")
def test_real_docling_options_ignore_the_ambient_environment(tmp_path):
    # Docling reads these variables when its modules are imported, so the
    # probe needs a fresh interpreter started with them already set.
    source = tmp_path / "book.pdf"
    source.write_bytes(b"%PDF-1.4 synthetic source")
    environment = {
        name: value for name, value in os.environ.items()
        if not name.startswith(("DOCLING_", "OMP_"))}
    environment.update(_HOSTILE_ENV, PYTHONDONTWRITEBYTECODE="1")

    completed = subprocess.run(
        [sys.executable, "-c", _ENV_PROBE, str(source)],
        cwd=Path(rag.__file__).resolve().parent, env=environment,
        capture_output=True, text=True, timeout=300, check=False)

    assert completed.returncode == 0, completed.stderr[-4000:]
    observed = json.loads(completed.stdout.strip().splitlines()[-1])
    assert observed.pop("backend_variable") is False
    assert observed == {
        "num_threads": 4,
        "device": "cpu",
        "compile_model": False,
        "visualize_layout": False,
        "page_batch_size": 4,
        # The scope restores the ambient value for the rest of the process.
        "visualize_layout_after": True,
    }


# --- (c) Truthful backend ----------------------------------------------------


@pytest.mark.parametrize("backend", ["pypdfium2", "auto"])
def test_conversion_logs_the_backend_docling_really_uses(
        monkeypatch, tmp_path, caplog, no_backend_variable, backend):
    docling = _FakeDocling(monkeypatch)

    with caplog.at_level(logging.INFO, logger="rag"):
        docling.run(tmp_path, backend=backend)

    assert "DOCLING_PDF_BACKEND" not in os.environ
    assert "Backend: pypdfium2" not in caplog.text
    assert (
        "Docling PDF backend: FakeParseBackend (requested "
        f"--backend={backend} is recorded but not used)") in caplog.text


def test_cpu_path_warns_that_batch_size_has_no_effect(
        monkeypatch, tmp_path, caplog):
    docling = _FakeDocling(monkeypatch)

    with caplog.at_level(logging.WARNING, logger="rag"):
        docling.run(tmp_path, batch_size_override=4)

    assert "--batch-size has no effect on the CPU conversion path" in (
        caplog.text)


@pytest.mark.parametrize("command", ["convert", "full", "batch"])
def test_backend_help_says_the_flag_is_only_recorded(capsys, command):
    with pytest.raises(SystemExit) as raised:
        rag.main([command, "--help"])

    assert raised.value.code == 0
    text = " ".join(capsys.readouterr().out.split())
    assert "pypdfium2 avoids" not in text
    assert ("recorded in conversion receipts but does not change Docling's "
            "PDF backend") in text


# --- (d) Progress and log volume -------------------------------------------


def test_progress_advances_from_assemble_records_during_conversion(
        monkeypatch, tmp_path):
    docling = _FakeDocling(monkeypatch, pages=5)
    bars = _record_bars(monkeypatch)
    during = {}

    def convert():
        _emit_profiling([[1, 2], [3], [4, 5]])
        during["n"] = bars[-1].n

    docling.on_convert = convert

    docling.run(tmp_path)

    [bar] = bars
    assert during["n"] == 5
    assert bar.updates == [2, 1, 2]
    assert bar.closed


def test_profiling_records_stay_out_of_an_info_level_log(
        monkeypatch, tmp_path):
    docling = _FakeDocling(monkeypatch, pages=2)

    def convert():
        logger = logging.getLogger(_PIPELINE_LOGGER)
        _emit_profiling([[1], [2]])
        logger.debug("Added %d failed/skipped pages to document: %s", 0, [])
        logger.warning("Stage %s thread did not terminate", "table")
        logger.error(
            "Stage %s failed for run %d: %s", "table", 1, "synthetic")

    docling.on_convert = convert

    with _root_capture(logging.INFO) as records:
        docling.run(tmp_path)

    pipeline = [(record.levelno, record.getMessage()) for record in records
                if record.name == _PIPELINE_LOGGER]
    assert pipeline == [
        (logging.WARNING, "Stage table thread did not terminate"),
        (logging.ERROR, "Stage table failed for run 1: synthetic"),
    ]


@pytest.mark.parametrize("fails", [False, True])
def test_pipeline_logger_state_is_restored(monkeypatch, tmp_path, fails):
    logger = logging.getLogger(_PIPELINE_LOGGER)
    logger.setLevel(logging.WARNING)
    before = (logger.level, list(logger.filters), list(logger.handlers))
    docling = _FakeDocling(monkeypatch)
    bars = _record_bars(monkeypatch)
    if fails:
        docling.convert_error = RuntimeError("synthetic converter failure")

    with (pytest.raises(RuntimeError, match="synthetic converter failure")
          if fails else nullcontext()):
        docling.run(tmp_path)

    assert (logger.level, logger.filters, logger.handlers) == before
    assert bars[-1].closed


def test_redirected_progress_refreshes_at_most_every_thirty_seconds(
        monkeypatch, tmp_path):
    docling = _FakeDocling(monkeypatch)
    bars = _record_bars(monkeypatch)
    monkeypatch.setattr(sys, "stderr", io.StringIO())

    docling.run(tmp_path)

    [bar] = bars
    assert bar.kwargs["mininterval"] >= 30
    assert bar.kwargs["maxinterval"] >= bar.kwargs["mininterval"]


def test_progress_filter_never_raises_into_docling_stage_threads(
        monkeypatch, tmp_path):
    # Docling logs from its stage threads inside ``except Exception``: an
    # error raised while filtering would fail the page being processed.
    docling = _FakeDocling(monkeypatch, pages=1)
    bars = _record_bars(monkeypatch)

    def convert():
        bars[-1].update = None  # the progress callback now raises
        _emit_profiling([[1]])
        logging.getLogger(_PIPELINE_LOGGER).debug(
            "PIPELINE_PROFILING Stage %s: %d", "assemble")  # bad arguments

    docling.on_convert = convert

    docling.run(tmp_path)

    assert (tmp_path / "book.json").exists()


@pytest.mark.skipif(importlib.util.find_spec("docling") is None,
                    reason="requires the locked Docling runtime")
def test_profiling_format_matches_the_installed_docling():
    pipeline = pytest.importorskip("docling.pipeline.standard_pdf_pipeline")
    stage_source = inspect.getsource(
        pipeline.ThreadedPipelineStage._process_batch)
    assert f'"{_PROFILING_TEMPLATE}"' in stage_source
    assert 'name="assemble"' in inspect.getsource(pipeline)
    assert pipeline._log.name == _PIPELINE_LOGGER
    assert rag._DOCLING_PIPELINE_LOGGER == _PIPELINE_LOGGER
    message = _PROFILING_TEMPLATE % ("assemble", 3, [4, 5], 1.0, 2.0, 1.0)
    assert ingestion_core.docling_assembled_page_count(message) == 2


@pytest.mark.parametrize(("message", "pages"), [
    (_PROFILING_TEMPLATE % ("assemble", 1, [7], 1.0, 2.0, 1.0), 1),
    (_PROFILING_TEMPLATE % ("assemble", 12, [1, 2, 3], 1.0, 2.0, 1.0), 3),
    (_PROFILING_TEMPLATE % ("assemble", 1, [], 1.0, 2.0, 1.0), 0),
    (_PROFILING_TEMPLATE % ("table", 1, [1, 2], 1.0, 2.0, 1.0), 0),
    ("PIPELINE_PROFILING Stage assemble: pages=[1, 2]", 0),
    ("Processing pages [1, 2]", 0),  # the shape the old regex expected
    ("", 0),
])
def test_assembled_page_count_reads_only_assemble_records(message, pages):
    assert ingestion_core.docling_assembled_page_count(message) == pages


@pytest.mark.parametrize(
    "floor", [logging.DEBUG, logging.INFO, logging.WARNING])
def test_progress_filter_drops_only_records_below_the_floor(floor):
    counted = []
    progress = rag._DoclingProgressFilter(floor, counted.append)

    def record(level, message, *args):
        return logging.LogRecord(
            _PIPELINE_LOGGER, level, __file__, 1, message, args, None)

    records = [
        record(logging.DEBUG, _PROFILING_TEMPLATE, "assemble", 1, [1, 2],
               1.0, 2.0, 1.0),
        record(logging.INFO, "Processing document %s", "book.pdf"),
        record(logging.ERROR, "Stage %s failed for run %d: %s", "table", 1,
               "synthetic"),
    ]

    assert [bool(progress.filter(item)) for item in records] == [
        floor <= logging.DEBUG, floor <= logging.INFO, True]
    assert counted == [2]
