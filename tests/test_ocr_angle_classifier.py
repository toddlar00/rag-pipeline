"""Opt-in RapidOCR text-direction (angle) classifier override.

``--ocr-no-angle-classifier`` builds Docling's ``RapidOcrOptions`` with
``use_cls=False``.  It is off by default: every default parameter set, digest,
Docling option, and conversion manifest must stay byte-identical, and the
override is recorded in the parameters digest and the conversion manifest
only when it can affect OCR.
"""

from contextlib import ExitStack, nullcontext
import hashlib
import json
import logging
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest

import job_runtime
import rag
from test_pipeline_runner import _args, _index_outcome


_FIXED_LOCK = "0" * 64
_WATERMARK = rag._compile_watermark(rag.DEFAULT_WATERMARK)
_BASE = dict(batch_size_override=None, backend="pypdfium2",
             auto_preprocess=True, watermark=_WATERMARK)
# Characterized from the pre-change code (2026-09-26) with the model-artifact
# lock digest pinned to _FIXED_LOCK.  Unpinned, the "h26-batch" shape is the
# bd9ee6498c23... digest every stored h26 conversion receipt carries.
_DEFAULT_SHAPES = {
    "cli-default": (
        dict(_BASE, ocr=None, ocr_full_page=False),
        "9332a925a325ce645e08a2c03909a1a68f6ecbc0218223fd078dcdc8662bfb64"),
    "h26-batch": (
        dict(_BASE, batch_size_override=2, auto_preprocess=False,
             ocr=None, ocr_full_page=False),
        "1dc71bd1fbba8e4e85600511348ccd182256ec1fdb7e34191564b806e51da8ff"),
    "--ocr": (
        dict(_BASE, ocr=True, ocr_full_page=False),
        "95f6ac44e236df5bd5c5a46d15ded9204e3b145f7e0d876a90a8dac21f0594da"),
    "--no-ocr": (
        dict(_BASE, ocr=False, ocr_full_page=False),
        "7d8cf2e12143a50e982ad3685dd0c09810b4f4a30ed8685c7bd7ce2f949a7f81"),
    "--ocr-full-page": (
        dict(_BASE, ocr=True, ocr_full_page=True),
        "20bfabfb82aa10dffdf8844b22bb1b6ffe81ef52ec4ea4c066b1128709860dc9"),
}


@pytest.fixture
def pinned_lock(monkeypatch):
    monkeypatch.setattr(
        rag, "_model_artifact_lock_sha256", lambda: _FIXED_LOCK)


@pytest.mark.parametrize("shape", sorted(_DEFAULT_SHAPES))
def test_default_parameters_keep_their_golden_digest(pinned_lock, shape):
    kwargs, golden = _DEFAULT_SHAPES[shape]
    parameters = rag._conversion_parameters(**kwargs)

    assert "ocr_angle_classifier" not in parameters
    assert rag._artifact_parameters_sha256(parameters) == golden


@pytest.mark.parametrize("shape", sorted(_DEFAULT_SHAPES))
def test_explicit_enabled_classifier_is_the_default(pinned_lock, shape):
    kwargs, golden = _DEFAULT_SHAPES[shape]
    parameters = rag._conversion_parameters(
        **kwargs, ocr_angle_classifier=True)

    assert rag._artifact_parameters_sha256(parameters) == golden


@pytest.mark.parametrize(
    "shape", ["cli-default", "h26-batch", "--ocr", "--ocr-full-page"])
def test_disabled_classifier_is_recorded_when_ocr_may_run(
        pinned_lock, shape):
    kwargs, golden = _DEFAULT_SHAPES[shape]
    default = rag._conversion_parameters(**kwargs)
    disabled = rag._conversion_parameters(
        **kwargs, ocr_angle_classifier=False)

    assert disabled == {**default, "ocr_angle_classifier": False}
    assert rag._artifact_parameters_sha256(disabled) != golden


def test_disabled_classifier_is_normalized_away_with_no_ocr(pinned_lock):
    kwargs, golden = _DEFAULT_SHAPES["--no-ocr"]
    for full_page in (False, True):
        parameters = rag._conversion_parameters(
            **dict(kwargs, ocr_full_page=full_page),
            ocr_angle_classifier=False)
        assert "ocr_angle_classifier" not in parameters
        assert rag._artifact_parameters_sha256(parameters) == golden


def _install_fake_rapidocr(monkeypatch, tmp_path):
    root = tmp_path / "docling"
    monkeypatch.setattr(
        rag._model_artifacts, "verified_docling_artifact_directory",
        lambda **_kwargs: root)

    class Options:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    options_module = ModuleType("docling.datamodel.pipeline_options")
    options_module.RapidOcrOptions = Options
    options_module.TableStructureOptions = Options
    options_module.TableFormerMode = SimpleNamespace(ACCURATE="accurate")
    options_module.OcrMode = SimpleNamespace(
        FULL_PAGE="full_page",
        PDF_AWARE_LAYOUT_REGIONS="pdf_aware_layout_regions")
    monkeypatch.setitem(sys.modules, "docling", ModuleType("docling"))
    monkeypatch.setitem(
        sys.modules, "docling.datamodel", ModuleType("docling.datamodel"))
    monkeypatch.setitem(
        sys.modules, "docling.datamodel.pipeline_options", options_module)


@pytest.mark.parametrize("ocr_full_page", [False, True])
def test_docling_options_set_use_cls_only_when_disabled(
        monkeypatch, tmp_path, ocr_full_page):
    _install_fake_rapidocr(monkeypatch, tmp_path)
    default, explicit, disabled = (
        SimpleNamespace(), SimpleNamespace(), SimpleNamespace())

    rag._configure_docling_model_artifacts(
        default, include_ocr=True, ocr_full_page=ocr_full_page)
    rag._configure_docling_model_artifacts(
        explicit, include_ocr=True, ocr_full_page=ocr_full_page,
        ocr_angle_classifier=True)
    rag._configure_docling_model_artifacts(
        disabled, include_ocr=True, ocr_full_page=ocr_full_page,
        ocr_angle_classifier=False)

    assert "use_cls" not in vars(default.ocr_options)
    assert vars(explicit.ocr_options) == vars(default.ocr_options)
    assert vars(disabled.ocr_options) == {
        **vars(default.ocr_options), "use_cls": False}


def test_docling_options_ignore_disabled_classifier_without_ocr(
        monkeypatch, tmp_path):
    _install_fake_rapidocr(monkeypatch, tmp_path)
    options = SimpleNamespace()

    rag._configure_docling_model_artifacts(
        options, include_ocr=False, ocr_angle_classifier=False)

    assert not hasattr(options, "ocr_options")


def _capture_generation_options(monkeypatch, *, usable_text):
    stats = {
        "total_pages": 1, "pages_with_large_images": 0,
        "pages_with_usable_text": int(usable_text),
        "large_image_pages_with_usable_text": 0,
        "text_chars": 100 if usable_text else 0, "replacement_chars": 0,
        "unique_dims": set(), "image_xrefs": set(),
    }
    monkeypatch.setattr(rag, "_analyze_pdf_images", lambda _path: stats)
    monkeypatch.setattr(rag, "_page_count", lambda _path: 1)

    class PipelineOptions:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    modules = {
        "docling.document_converter": dict(
            DocumentConverter=object, PdfFormatOption=object),
        "docling.datamodel.pipeline_options": dict(
            PdfPipelineOptions=PipelineOptions,
            ThreadedPdfPipelineOptions=PipelineOptions),
        "docling.datamodel.accelerator_options": dict(
            AcceleratorDevice=SimpleNamespace(CPU="cpu", CUDA="cuda"),
            AcceleratorOptions=SimpleNamespace),
        "docling.datamodel.base_models": dict(
            InputFormat=SimpleNamespace(PDF="pdf")),
        "docling.datamodel.settings": dict(
            scoped=lambda **_kwargs: nullcontext(),
            BatchConcurrencySettings=SimpleNamespace,
            DebugSettings=SimpleNamespace,
            InferenceSettings=SimpleNamespace),
    }
    for name, attributes in modules.items():
        module = ModuleType(name)
        module.__dict__.update(attributes)
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(rag, "_detect_gpu", lambda: ("cpu", 1, "CPU"))
    monkeypatch.setattr(
        rag, "_pin_docling_layout_revision", lambda _options: None)
    observed = {}

    class OptionsCaptured(Exception):
        pass

    def capture(options, **kwargs):
        observed.update(kwargs, do_ocr=options.do_ocr)
        raise OptionsCaptured

    monkeypatch.setattr(rag, "_configure_docling_model_artifacts", capture)
    return observed, OptionsCaptured


@pytest.mark.parametrize(
    ("ocr", "usable_text", "classifier", "expected_ocr", "warns"),
    [
        (None, False, True, True, False),    # default auto-enabled OCR
        (None, False, False, True, False),   # h04: auto-enabled, cls off
        (True, True, False, True, False),    # --ocr
        (None, True, False, False, True),    # auto keeps OCR off: no effect
        (False, False, False, False, True),  # --no-ocr wins
    ],
)
def test_generation_threads_the_classifier_choice(
        monkeypatch, tmp_path, caplog, ocr, usable_text, classifier,
        expected_ocr, warns):
    observed, captured = _capture_generation_options(
        monkeypatch, usable_text=usable_text)
    source = tmp_path / "book.pdf"
    source.write_bytes(b"pdf")
    original = rag.ConversionInputBinding(
        kind="original", name=source.name, sha256="0" * 64,
        size=source.stat().st_size)

    with ExitStack() as snapshots, pytest.raises(captured), \
            caplog.at_level(logging.WARNING, logger="rag"):
        rag._convert_pdf_generation(
            source, tmp_path / "book.json", snapshot_stack=snapshots,
            original_input=original, auto_preprocess=True, ocr=ocr,
            ocr_angle_classifier=classifier)

    assert observed["do_ocr"] is expected_ocr
    assert observed["include_ocr"] is expected_ocr
    assert observed["ocr_angle_classifier"] is classifier
    assert ("--ocr-no-angle-classifier has no effect" in caplog.text) is warns


@pytest.mark.parametrize(
    ("flags", "expected"),
    [
        ([], True),
        (["--ocr-no-angle-classifier"], False),
        (["--ocr-full-page", "--ocr-no-angle-classifier"], False),
        (["--no-ocr", "--ocr-no-angle-classifier"], False),
    ],
)
def test_convert_cli_forwards_the_classifier_choice(
        monkeypatch, flags, expected):
    observed = {}
    monkeypatch.setattr(
        rag, "convert_pdf", lambda *args, **kwargs: observed.update(kwargs))

    rag.main(["convert", "--pdf", "book.pdf", *flags])

    assert observed["ocr_angle_classifier"] is expected


class _StopAfterParse(Exception):
    pass


@pytest.mark.parametrize("command", ["full", "batch"])
@pytest.mark.parametrize("flags", [[], ["--ocr-no-angle-classifier"]])
def test_pipeline_commands_accept_the_classifier_flag(
        monkeypatch, tmp_path, command, flags):
    source = tmp_path / "book.pdf"
    source.write_bytes(b"pdf")
    observed = {}

    def stop(_pdf, args, **_kwargs):
        observed["flag"] = args.ocr_no_angle_classifier
        raise _StopAfterParse

    monkeypatch.setattr(rag, "_run_pipeline_job", stop)
    pdf_args = (["--pdf", str(source)] if command == "full"
                else [str(source)])

    with pytest.raises(_StopAfterParse):
        rag.main([command, *pdf_args, *flags])

    assert observed["flag"] is bool(flags)


@pytest.mark.parametrize("flag", [None, False, True])
@pytest.mark.parametrize(("resume", "reusable"), [
    (False, False), (True, False), (True, True)])
def test_pipeline_stages_bind_the_choice_to_conversion_and_publication(
        monkeypatch, tmp_path, flag, resume, reusable):
    # full/batch reach conversion only through _run_pipeline_stages: the
    # resume check, convert_pdf, and publication must share one parameter set.
    monkeypatch.setattr(rag, "OUTPUT_DIR", tmp_path / "output")
    seen = {"complete": []}

    def complete(*_args, parameters, **_kwargs):
        seen["complete"].append(parameters)
        return reusable or "convert" in seen

    def publish(*_args, conversion_parameters, **_kwargs):
        seen["publication"] = conversion_parameters
        return {"gates": [{"name": name, "status": "pass"} for name
                          in rag._publication_core.PUBLICATION_GATE_NAMES]}

    monkeypatch.setattr(rag, "_converted_outputs_complete", complete)
    for name in ("_chunks_complete", "_quality_report_complete",
                 "_unified_export_complete", "_split_export_complete",
                 "_raptor_output_complete"):
        monkeypatch.setattr(rag, name, lambda *_a, **_k: True)
    monkeypatch.setattr(rag, "_publish_pipeline_publication", publish)
    monkeypatch.setattr(
        rag, "convert_pdf", lambda *_a, **k: seen.setdefault("convert", k))
    monkeypatch.setattr(rag, "chunk_document", lambda *_a, **_k: None)
    monkeypatch.setattr(rag, "export_markdown", lambda *_a, **_k: None)
    monkeypatch.setattr(
        rag, "_index_chunks_for_backend", lambda *_a, **_k: _index_outcome())
    extra = {} if flag is None else {"ocr_no_angle_classifier": flag}
    args = _args(batch_size=2, backend="pypdfium2", no_preprocess=True,
                 **extra)

    rag._run_pipeline_stages(
        Path("Book.pdf"), rag._output_paths_for_name("Book"), args,
        resume=resume, watermark=None)

    classifier = not flag
    expected = rag._conversion_parameters(
        batch_size_override=2, backend="pypdfium2", auto_preprocess=False,
        ocr=None, ocr_full_page=False, ocr_angle_classifier=classifier,
        watermark=None)
    assert ("convert" in seen) is not reusable  # reusable: [SKIP] convert
    if not reusable:
        assert seen["convert"]["ocr_angle_classifier"] is classifier
    assert seen["publication"] == expected
    assert seen["complete"]
    assert all(parameters == expected for parameters in seen["complete"])


@pytest.mark.parametrize(
    ("ocr", "flag", "expected"),
    [
        (None, True, True),
        (True, True, True),
        (None, False, False),
        (False, True, False),  # --no-ocr wins, as for --ocr-full-page
    ],
)
def test_resume_command_preserves_the_classifier_choice(ocr, flag, expected):
    command = rag._build_resume_cmd(
        Path("Book.pdf"), SimpleNamespace(
            ocr=ocr, ocr_full_page=False, ocr_no_angle_classifier=flag))

    assert ("--ocr-no-angle-classifier" in command) is expected


def test_resume_command_without_the_attribute_is_unchanged():
    legacy = SimpleNamespace(ocr=None, ocr_full_page=False)

    assert "angle" not in rag._build_resume_cmd(Path("Book.pdf"), legacy)


def test_background_jobs_pass_the_flag_through_unchanged():
    arguments = ["--pdf", "book.pdf", "--ocr-no-angle-classifier"]

    assert job_runtime._validate_argv(arguments) == tuple(arguments)
    canonical = rag._canonical_background_security_argv(
        "convert", list(arguments))
    assert canonical[:3] == arguments


def _commit_fake_conversion(monkeypatch, tmp_path, observed=None,
                            **convert_kwargs):
    source = tmp_path / "book.pdf"
    source.write_bytes(b"%PDF-1.4 synthetic source")
    document = tmp_path / "book.json"
    markdown = tmp_path / "book_docling.md"

    def fake_generation(pdf_path, doc_output, *, original_input, **kwargs):
        if observed is not None:
            observed.update(kwargs)
        rag._atomic_write_text(doc_output, '{"name": "book"}')
        rag._atomic_write_text(markdown, "# Synthetic conversion")
        return original_input

    monkeypatch.setattr(rag, "_convert_pdf_generation", fake_generation)
    rag.convert_pdf(source, document, backend="auto", **convert_kwargs)
    manifest = rag._artifact_completion_path(document, stage="conversion")
    return source, document, markdown, manifest


def _parameters(**kwargs):
    return rag._conversion_parameters(
        batch_size_override=None, backend="auto", auto_preprocess=True,
        watermark=None, **kwargs)


def _binding(document):
    raw = document.read_bytes()
    return rag._load_conversion_source_binding(
        document, document_sha256=hashlib.sha256(raw).hexdigest(),
        document_size=len(raw))


@pytest.mark.parametrize(("ocr", "classifier"), [
    (None, True), (None, False), (True, False), (False, False)])
def test_conversion_threads_the_choice_to_generation(
        monkeypatch, tmp_path, ocr, classifier):
    # convert_pdf -> _convert_pdf_locked -> _convert_pdf_generation: losing
    # the last hop would record the override over classifier-on bytes.
    observed = {}
    _, _, _, manifest = _commit_fake_conversion(
        monkeypatch, tmp_path, observed, ocr=ocr,
        ocr_angle_classifier=classifier)
    payload = json.loads(manifest.read_text(encoding="utf-8"))

    assert observed["ocr_angle_classifier"] is classifier
    assert payload.get("ocr_angle_classifier", True) is (
        classifier or ocr is False)


def test_default_manifest_keeps_the_legacy_field_set(monkeypatch, tmp_path):
    source, document, markdown, manifest = _commit_fake_conversion(
        monkeypatch, tmp_path, ocr=None)
    payload = json.loads(manifest.read_text(encoding="utf-8"))

    assert set(payload) == {
        "schema_version", "stage", "source_sha256", "source_name",
        "source_record_count", "parameters_sha256", "outputs", "source",
        "effective_input"}
    assert rag._converted_outputs_complete(
        source, document, markdown, parameters=_parameters(ocr=None))


def test_disabled_classifier_is_recorded_in_the_manifest(
        monkeypatch, tmp_path):
    source, document, markdown, manifest = _commit_fake_conversion(
        monkeypatch, tmp_path, ocr=None, ocr_angle_classifier=False)
    payload = json.loads(manifest.read_text(encoding="utf-8"))

    assert payload["schema_version"] == 3
    assert payload["ocr_angle_classifier"] is False
    assert _binding(document).ocr_angle_classifier is False
    assert rag._converted_outputs_complete(
        source, document, markdown,
        parameters=_parameters(ocr=None, ocr_angle_classifier=False))
    assert not rag._converted_outputs_complete(
        source, document, markdown, parameters=_parameters(ocr=None))


@pytest.mark.parametrize("digest_classifier", [True, False])
def test_manifest_record_that_contradicts_its_digest_is_not_resumable(
        monkeypatch, tmp_path, digest_classifier):
    # Isolates the resume check's own comparison: the parameters digest and
    # the output records still match; only the readable override disagrees.
    kwargs = {} if digest_classifier else {"ocr_angle_classifier": False}
    source, document, markdown, manifest = _commit_fake_conversion(
        monkeypatch, tmp_path, ocr=None, **kwargs)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    if digest_classifier:
        payload["ocr_angle_classifier"] = False
    else:
        del payload["ocr_angle_classifier"]
    rag._atomic_write_json(manifest, payload)
    parameters = _parameters(ocr=None, **kwargs)
    binding = _binding(document)

    assert binding.ocr_angle_classifier is not digest_classifier
    assert rag._fixed_artifacts_complete(
        manifest, stage="conversion", source_sha256=binding.source_sha256,
        source_record_count=None, parameters=parameters,
        outputs={"docling_json": document, "docling_markdown": markdown},
        source_name=binding.source_name,
        schema_version=rag.CONVERSION_COMPLETION_SCHEMA_VERSION)
    assert not rag._converted_outputs_complete(
        source, document, markdown, parameters=parameters)


def test_no_ocr_conversion_does_not_record_the_override(
        monkeypatch, tmp_path):
    _, _, _, manifest = _commit_fake_conversion(
        monkeypatch, tmp_path, ocr=False, ocr_angle_classifier=False)
    payload = json.loads(manifest.read_text(encoding="utf-8"))

    assert "ocr_angle_classifier" not in payload


@pytest.mark.parametrize("value", [True, None, "false", 0])
def test_manifest_loader_rejects_invalid_override_values(
        monkeypatch, tmp_path, value):
    _, document, _, manifest = _commit_fake_conversion(
        monkeypatch, tmp_path, ocr=None, ocr_angle_classifier=False)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["ocr_angle_classifier"] = value
    rag._atomic_write_json(manifest, payload)

    with pytest.raises(ValueError, match="OCR override"):
        _binding(document)
