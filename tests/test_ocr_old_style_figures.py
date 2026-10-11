"""Opt-in reading of OCR ``o`` as ``0`` inside numbers set in old-style figures.

Books set in old-style (lowercase) figures print a zero that RapidOCR reads as
the letter ``o``: ``2oo2`` for 2002, ``1o9(b)(1)`` for 109(b)(1), ``9,ooo`` for
9,000. With the option, every OCR text cell has ``o`` read as ``0`` inside a
run of digits, ``o`` and the separators ``,`` ``.`` ``/`` that holds at least
one digit and is not attached to a preceding letter. Native PDF text cells are
never changed, and nor is the raw ``orig`` reading of an OCR cell.

Off by default: every default parameter set, digest, converter option and
conversion manifest stays byte-identical; the setting is recorded only when it
can affect OCR. All text is synthetic.
"""

from contextlib import ExitStack
import itertools
import json
import logging
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest

import job_runtime
import rag
from test_ocr_angle_classifier import (
    _DEFAULT_SHAPES, _FIXED_LOCK, _binding, _capture_generation_options,
    _commit_fake_conversion, _parameters)
from test_pipeline_runner import _args, _index_outcome


_FLAG = "--ocr-old-style-figures"
_KEY = "ocr_old_style_figures"
_MERGE_KEY = "ocr_merge_interleaved_regions"


# --- The reading rule ------------------------------------------------------

@pytest.mark.parametrize(("ocr", "expected"), [
    ("2oo2", "2002"),
    ("20o2,", "2002,"),
    ("(2oo2).", "(2002)."),
    ("1o9(b)(1)", "109(b)(1)"),
    ("7o6(2)(A).", "706(2)(A)."),
    ("193os,", "1930s,"),
    ("1ob-5", "10b-5"),
    ("9,ooo", "9,000"),
    ("0.6o", "0.60"),
    ("1/o", "1/0"),
    ("MSQR-5ooo", "MSQR-5000"),
    ("pp. 1o-12 and 2o", "pp. 10-12 and 20"),
])
def test_o_inside_a_number_is_read_as_zero(ocr, expected):
    assert rag._ocr_old_style_figures_text(ocr) == expected


@pytest.mark.parametrize("text", [
    "No.", "co2", "too", "o", "oo", "Ohio", "2nd", "F.2d", "A1o", "Oo2",
    "U.S.", "1984", "Section 12.",
])
def test_other_text_is_unchanged(text):
    assert rag._ocr_old_style_figures_text(text) == text


def _cell(text, *, from_ocr):
    return SimpleNamespace(text=text, orig=text, from_ocr=from_ocr)


def test_the_ocr_model_wrapper_reads_only_ocr_cells():
    ocr_cell = _cell("In 2oo2 the rule", from_ocr=True)
    native_cell = _cell("Form 1o", from_ocr=False)
    page = SimpleNamespace(parsed_page=SimpleNamespace(
        textline_cells=[ocr_cell, native_cell]))
    seen = []

    def inner(conv_res, page_batch):
        seen.append((conv_res, page_batch))
        yield from page_batch

    model = rag._OldStyleFiguresOcrModel(inner)

    assert list(model("run", [page])) == [page]
    assert seen == [("run", [page])]
    assert ocr_cell.text == "In 2002 the rule"
    assert ocr_cell.orig == "In 2oo2 the rule"
    assert native_cell.text == "Form 1o"


def test_the_ocr_model_wrapper_passes_pages_without_cells():
    page = SimpleNamespace(parsed_page=None)

    model = rag._OldStyleFiguresOcrModel(lambda _run, batch: iter(batch))

    assert list(model("run", [page])) == [page]


def test_the_pipeline_wraps_only_the_ocr_model(monkeypatch):
    pytest.importorskip("docling")
    from docling.pipeline.standard_pdf_pipeline import StandardPdfPipeline

    inner = object()

    def init(self, pipeline_options):
        self.ocr_model = inner
        self.layout_postprocessing_model = "layout"

    monkeypatch.setattr(StandardPdfPipeline, "__init__", init)
    pipeline_cls = rag._old_style_figures_pipeline_cls(None)
    assert issubclass(pipeline_cls, StandardPdfPipeline)
    pipeline = pipeline_cls(pipeline_options=object())

    assert isinstance(pipeline.ocr_model, rag._OldStyleFiguresOcrModel)
    assert pipeline.ocr_model.inner is inner
    assert pipeline.layout_postprocessing_model == "layout"


def test_the_pipeline_can_extend_the_merging_pipeline(monkeypatch):
    pytest.importorskip("docling")

    class Merging:
        def __init__(self, pipeline_options):
            self.ocr_model = "ocr"
            self.layout_postprocessing_model = "merged-layout"

    pipeline = rag._old_style_figures_pipeline_cls(Merging)(
        pipeline_options=object())

    assert isinstance(pipeline, Merging)
    assert pipeline.ocr_model.inner == "ocr"
    assert pipeline.layout_postprocessing_model == "merged-layout"


# --- Generation wiring -----------------------------------------------------

class _ConverterBuilt(Exception):
    pass


_MERGE_PIPELINE = object()


@pytest.mark.parametrize(
    ("ocr", "usable_text", "figures", "merge", "wired", "warns"), [
        (None, False, False, False, None, False),   # default: Docling's class
        (None, False, True, False, "figures", False),  # auto-enabled OCR
        (True, True, True, False, "figures", False),   # --ocr
        (None, False, True, True, "both", False),      # composed with merge
        (None, True, True, False, None, True),   # auto keeps OCR off
        (False, False, True, False, None, True),  # --no-ocr wins
    ])
def test_generation_wires_the_figures_pipeline_only_when_ocr_runs(
        monkeypatch, tmp_path, caplog, ocr, usable_text, figures, merge,
        wired, warns):
    observed, _ = _capture_generation_options(
        monkeypatch, usable_text=usable_text)
    monkeypatch.setattr(rag, "_configure_docling_model_artifacts",
                        lambda *_args, **_kwargs: Path("artifacts"))
    monkeypatch.setattr(
        rag, "_interleaved_region_merge_pipeline_cls",
        lambda: _MERGE_PIPELINE)
    monkeypatch.setattr(
        rag, "_old_style_figures_pipeline_cls",
        lambda base: ("figures", base))

    def converter(*, format_options):
        observed.update(format_options["pdf"])
        raise _ConverterBuilt

    module = ModuleType("docling.document_converter")
    module.DocumentConverter, module.PdfFormatOption = converter, dict
    monkeypatch.setitem(sys.modules, "docling.document_converter", module)
    source = tmp_path / "book.pdf"
    source.write_bytes(b"pdf")
    original = rag.ConversionInputBinding(
        kind="original", name=source.name, sha256="0" * 64,
        size=source.stat().st_size)

    with ExitStack() as snapshots, pytest.raises(_ConverterBuilt), \
            caplog.at_level(logging.INFO, logger="rag"):
        rag._convert_pdf_generation(
            source, tmp_path / "book.json", snapshot_stack=snapshots,
            original_input=original, auto_preprocess=True, ocr=ocr,
            ocr_merge_interleaved_regions=merge,
            ocr_old_style_figures=figures)

    observed.pop("pipeline_options")
    expected = {
        None: {},
        "figures": {"pipeline_cls": ("figures", None)},
        "both": {"pipeline_cls": ("figures", _MERGE_PIPELINE)},
    }[wired]
    assert observed == expected
    assert ("old-style figures read" in caplog.text) is (wired is not None)
    assert (f"{_FLAG} has no effect" in caplog.text) is warns


# --- Parameters, CLI, stages, resume and jobs ------------------------------

@pytest.fixture
def pinned_lock(monkeypatch):
    monkeypatch.setattr(
        rag, "_model_artifact_lock_sha256", lambda: _FIXED_LOCK)


@pytest.mark.parametrize("merge", [True, False])
@pytest.mark.parametrize("shape", sorted(_DEFAULT_SHAPES))
def test_figures_are_recorded_only_when_ocr_may_run(pinned_lock, shape, merge):
    # Unset or False keeps the golden digests; --no-ocr normalizes it away.
    kwargs, golden = _DEFAULT_SHAPES[shape]
    recorded = kwargs["ocr"] is not False
    for full_page in ((kwargs["ocr_full_page"],) if recorded
                      else (False, True)):
        shaped = dict(kwargs, ocr_full_page=full_page, **{_MERGE_KEY: merge})
        plain = rag._conversion_parameters(**shaped)
        figures = rag._conversion_parameters(**shaped, **{_KEY: True})
        assert rag._conversion_parameters(**shaped, **{_KEY: False}) == plain
        assert _KEY not in plain
        assert (rag._artifact_parameters_sha256(plain) == golden) is (
            not merge or not recorded)
        assert figures == ({**plain, _KEY: True} if recorded else plain)


class _StopAfterParse(Exception):
    pass


@pytest.mark.parametrize("command", ["convert", "full", "batch"])
@pytest.mark.parametrize(("flags", "figures", "merge"), [
    ([], False, False),
    ([_FLAG], True, False),
    (["--ocr-full-page", _FLAG], True, False),
    (["--ocr-merge-interleaved-regions", _FLAG], True, True),
    (["--no-ocr", _FLAG], True, False),
])
def test_cli_commands_forward_the_figures_choice(
        monkeypatch, tmp_path, command, flags, figures, merge):
    source = tmp_path / "book.pdf"
    source.write_bytes(b"pdf")
    observed = {}

    def convert(*_args, **kwargs):
        observed.update(kwargs)
        raise _StopAfterParse

    def job(_pdf, args, **_kwargs):
        observed[_KEY] = getattr(args, _KEY)
        observed[_MERGE_KEY] = getattr(args, _MERGE_KEY)
        raise _StopAfterParse

    monkeypatch.setattr(rag, "convert_pdf", convert)
    monkeypatch.setattr(rag, "_run_pipeline_job", job)
    pdf_args = [str(source)] if command == "batch" else ["--pdf", str(source)]

    with pytest.raises(_StopAfterParse):
        rag.main([command, *pdf_args, *flags])

    assert observed[_KEY] is figures
    assert observed[_MERGE_KEY] is merge


@pytest.mark.parametrize("flag", [None, False, True])
@pytest.mark.parametrize(("resume", "reusable"), [
    (False, False), (True, False), (True, True)])
def test_pipeline_stages_bind_the_figures_to_conversion_and_publication(
        monkeypatch, tmp_path, flag, resume, reusable):
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
    args = _args(batch_size=2, backend="pypdfium2", no_preprocess=True,
                 **({} if flag is None else {_KEY: flag}))

    rag._run_pipeline_stages(
        Path("Book.pdf"), rag._output_paths_for_name("Book"), args,
        resume=resume, watermark=None)

    expected = rag._conversion_parameters(
        batch_size_override=2, backend="pypdfium2", auto_preprocess=False,
        ocr=None, ocr_full_page=False, watermark=None, **{_KEY: bool(flag)})
    assert ("convert" in seen) is not reusable
    if not reusable:
        assert seen["convert"][_KEY] is bool(flag)
    assert seen["publication"] == expected
    assert seen["complete"] and all(
        parameters == expected for parameters in seen["complete"])


@pytest.mark.parametrize(("ocr", "flag", "expected"), [
    (None, True, True), (True, True, True), (None, False, False),
    (None, None, False),   # arguments from before the flag existed
    (False, True, False),  # --no-ocr wins, as for --ocr-full-page
])
def test_resume_command_preserves_the_figures_choice(ocr, flag, expected):
    command = rag._build_resume_cmd(Path("Book.pdf"), SimpleNamespace(
        ocr=ocr, ocr_full_page=False,
        **({} if flag is None else {_KEY: flag})))

    assert (_FLAG in command) is expected


def test_background_jobs_pass_the_flag_through_unchanged():
    arguments = ["--pdf", "book.pdf", _FLAG]

    assert job_runtime._validate_argv(arguments) == tuple(arguments)
    canonical = rag._canonical_background_security_argv(
        "convert", list(arguments))
    assert canonical[:3] == arguments


# --- Conversion manifest -------------------------------------------------

_LEGACY_ROOT = {"schema_version", "stage", "source_sha256", "source_name",
                "source_record_count", "parameters_sha256", "outputs",
                "source", "effective_input"}


@pytest.mark.parametrize("merge", [True, False])
@pytest.mark.parametrize(("ocr", "figures"), [
    (None, False), (None, True), (True, True), (False, True)])
def test_conversion_threads_and_records_the_figures(
        monkeypatch, tmp_path, ocr, figures, merge):
    observed = {}
    source, document, markdown, manifest = _commit_fake_conversion(
        monkeypatch, tmp_path, observed, ocr=ocr,
        **{_MERGE_KEY: merge, _KEY: figures})
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    binding = _binding(document)
    recorded = figures and ocr is not False
    merged = merge and ocr is not False

    assert observed[_KEY] is figures
    assert set(payload) == _LEGACY_ROOT | {
        key for key, flagged in ((_MERGE_KEY, merged), (_KEY, recorded))
        if flagged}
    assert payload.get(_KEY, True) is True
    assert binding.ocr_old_style_figures is recorded
    assert binding.ocr_merge_interleaved_regions is merged
    committed = _parameters(ocr=ocr, **{_MERGE_KEY: merge, _KEY: figures})
    for other_merge, other_figures in itertools.product(
            (True, False), repeat=2):
        parameters = _parameters(
            ocr=ocr, **{_MERGE_KEY: other_merge, _KEY: other_figures})
        assert rag._converted_outputs_complete(
            source, document, markdown,
            parameters=parameters) is (parameters == committed)


@pytest.mark.parametrize("digest_figures", [False, True])
def test_manifest_record_that_contradicts_its_digest_is_not_resumable(
        monkeypatch, tmp_path, digest_figures):
    kwargs = {_KEY: True} if digest_figures else {}
    source, document, markdown, manifest = _commit_fake_conversion(
        monkeypatch, tmp_path, ocr=None, **kwargs)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    if digest_figures:
        del payload[_KEY]
    else:
        payload[_KEY] = True
    rag._atomic_write_json(manifest, payload)
    parameters = _parameters(ocr=None, **kwargs)
    binding = _binding(document)

    assert binding.ocr_old_style_figures is not digest_figures
    assert not rag._converted_outputs_complete(
        source, document, markdown, parameters=parameters)


@pytest.mark.parametrize("value", [False, None, "true", 1])
def test_manifest_loader_rejects_invalid_figures_values(
        monkeypatch, tmp_path, value):
    _, document, _, manifest = _commit_fake_conversion(
        monkeypatch, tmp_path, ocr=None, **{_KEY: True})
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload[_KEY] = value
    rag._atomic_write_json(manifest, payload)

    with pytest.raises(ValueError, match="OCR override"):
        _binding(document)
