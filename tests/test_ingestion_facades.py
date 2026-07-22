import sys
from types import SimpleNamespace

import ingestion_core
import preprocess_pdf as preprocess_cli
import rag


class FakeRect:
    def __init__(self, width=100, height=200):
        self.width = width
        self.height = height


class FakePage:
    def __init__(
            self, number, text, xrefs=(), *, images_error=None,
            delete_errors=()):
        self.number = number
        self.text = text
        self.xrefs = tuple(xrefs)
        self.images_error = images_error
        self.delete_errors = set(delete_errors)
        self.deleted = []
        self.rect = FakeRect()

    def get_text(self, kind="text"):
        return self.text

    def get_images(self, *, full=True):
        if self.images_error is not None:
            raise self.images_error
        return [(xref,) for xref in self.xrefs]

    def get_image_rects(self, xref):
        return [FakeRect()]

    def delete_image(self, xref):
        if xref in self.delete_errors:
            raise RuntimeError(f"cannot delete {xref}")
        self.deleted.append(xref)


class FakeDocument:
    def __init__(self, pages, *, output_bytes=b"processed-pdf"):
        self.pages = list(pages)
        self.output_bytes = output_bytes
        self.saved = []
        self.closed = False

    def __len__(self):
        return len(self.pages)

    def __iter__(self):
        return iter(self.pages)

    def __getitem__(self, index):
        return self.pages[index]

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    def extract_image(self, xref):
        return {"width": 2000, "height": 3000}

    def save(self, path, **kwargs):
        self.saved.append((path, kwargs))
        rag.Path(path).write_bytes(self.output_bytes)

    def close(self):
        self.closed = True


def _analysis_stats(*, total=2, large=2, usable=1, usable_large=1):
    return {
        "total_pages": total,
        "pages_with_large_images": large,
        "pages_with_usable_text": usable,
        "large_image_pages_with_usable_text": usable_large,
        "text_chars": 140,
        "replacement_chars": 0,
        "unique_dims": {"2000x3000"},
        "image_xrefs": {1, 2},
        "inspection_complete": True,
    }


def _install_pymupdf(monkeypatch, *documents):
    pending = iter(documents)
    monkeypatch.setitem(
        sys.modules,
        "pymupdf",
        SimpleNamespace(open=lambda _path: next(pending)),
    )


def test_rag_policy_facades_inject_current_thresholds(monkeypatch):
    observed = {}

    def text_core(text, *, thresholds):
        observed["text"] = (text, thresholds)
        return True

    monkeypatch.setattr(rag, "_MIN_USABLE_PAGE_CHARS", 77)
    monkeypatch.setattr(
        ingestion_core, "pdf_page_text_is_usable", text_core)

    assert rag._pdf_page_text_is_usable("page")
    assert observed["text"][0] == "page"
    assert observed["text"][1].min_usable_page_chars == 77

    def layer_core(stats, **kwargs):
        observed["layer"] = (stats, kwargs)
        return False

    monkeypatch.setattr(rag, "_MIN_USABLE_SCAN_TEXT_RATIO", 0.75)
    monkeypatch.setattr(
        ingestion_core, "pdf_text_layer_is_usable", layer_core)
    stats = {"sentinel": True}

    assert not rag._pdf_text_layer_is_usable(
        stats, large_image_pages_only=True)
    assert observed["layer"][0] is stats
    assert observed["layer"][1]["large_image_pages_only"] is True
    assert observed["layer"][1][
        "thresholds"].min_usable_scan_text_ratio == 0.75


def test_rag_background_facade_fails_closed_and_uses_current_coverage(
        monkeypatch):
    observed = {}

    def inspect_core(document, page, min_dimension, *, thresholds):
        observed.update(
            document=document,
            page=page,
            min_dimension=min_dimension,
            thresholds=thresholds,
        )
        return ingestion_core.PageBackgroundInspection(
            candidates=((1, 2000, 3000),),
            complete=False,
            issues=(ingestion_core.PDFInspectionIssue(
                page_number=0, stage="images", detail="incomplete"),),
        )

    monkeypatch.setattr(
        rag, "_MIN_BACKGROUND_IMAGE_PAGE_COVERAGE", 0.85)
    monkeypatch.setattr(
        ingestion_core, "inspect_page_background_images", inspect_core)
    document = object()
    page = object()

    assert rag._page_background_images(document, page, 900) == []
    assert observed["document"] is document
    assert observed["page"] is page
    assert observed["min_dimension"] == 900
    assert observed["thresholds"].min_background_image_page_coverage == 0.85


def test_rag_analysis_facade_injects_current_helpers(monkeypatch):
    document = FakeDocument([])
    observed = {}
    sentinel = ingestion_core.PDFAnalysis(
        stats={
            "total_pages": 0,
            "pages_with_large_images": 0,
            "pages_with_usable_text": 0,
            "large_image_pages_with_usable_text": 0,
            "text_chars": 0,
            "replacement_chars": 0,
            "unique_dims": set(),
            "image_xrefs": set(),
            "inspection_complete": True,
        }
    )

    def analyze_core(open_document, min_dimension, **kwargs):
        observed.update(
            document=open_document,
            min_dimension=min_dimension,
            **kwargs,
        )
        return sentinel

    _install_pymupdf(monkeypatch, document)
    monkeypatch.setattr(
        ingestion_core, "analyze_pdf_document", analyze_core)

    assert rag._analyze_pdf_images(rag.Path("book.pdf"), 777) is sentinel.stats
    assert observed["document"] is document
    assert observed["min_dimension"] == 777
    assert observed["page_text_is_usable_fn"] is (
        rag._pdf_page_text_is_usable)
    assert observed["page_background_inspection_fn"] is (
        rag._inspect_page_background_images)
    assert document.closed


def test_rag_analyze_only_rejects_incomplete_cached_analysis(
        monkeypatch, tmp_path, caplog):
    source = tmp_path / "book.pdf"
    source.write_bytes(b"source")
    output = tmp_path / "clean.pdf"
    stats = _analysis_stats()
    stats["inspection_complete"] = False
    monkeypatch.setitem(sys.modules, "pymupdf", SimpleNamespace())

    result = rag.preprocess_pdf(
        source,
        output,
        analyze_only=True,
        _analysis_cache=stats,
    )

    assert result is None
    assert not output.exists()
    assert "inspection was incomplete" in caplog.text
    assert "removable background scans" not in caplog.text


def test_rag_incomplete_plan_cancels_all_deletion_and_save(
        monkeypatch, tmp_path):
    source = tmp_path / "book.pdf"
    source.write_bytes(b"source")
    output = tmp_path / "clean.pdf"
    safe = FakePage(0, "Reliable text " * 10, [1])
    failed = FakePage(
        1,
        "More reliable text " * 10,
        [2],
        images_error=RuntimeError("inspection failed"),
    )
    document = FakeDocument([safe, failed])
    _install_pymupdf(monkeypatch, document)

    result = rag.preprocess_pdf(
        source,
        output,
        _analysis_cache=_analysis_stats(usable=2, usable_large=2),
    )

    assert result is None
    assert safe.deleted == []
    assert document.saved == []
    assert not output.exists()


def test_rag_shared_xref_on_scan_only_page_is_never_deleted_or_saved(
        monkeypatch, tmp_path):
    source = tmp_path / "book.pdf"
    source.write_bytes(b"source")
    output = tmp_path / "clean.pdf"
    safe = FakePage(0, "Reliable text " * 10, [7])
    scan = FakePage(1, "", [7])
    document = FakeDocument([safe, scan])
    _install_pymupdf(monkeypatch, document)

    result = rag.preprocess_pdf(
        source,
        output,
        _analysis_cache=_analysis_stats(),
    )

    assert result is None
    assert safe.deleted == []
    assert scan.deleted == []
    assert document.saved == []


def test_rag_deletion_failure_warns_and_saves_partial_success(
        monkeypatch, tmp_path, caplog):
    source = tmp_path / "book.pdf"
    source.write_bytes(b"source-pdf")
    output = tmp_path / "clean.pdf"
    page = FakePage(
        0, "Reliable text " * 10, [1, 2], delete_errors={1})
    document = FakeDocument([page])
    _install_pymupdf(monkeypatch, document)

    result = rag.preprocess_pdf(
        source,
        output,
        _analysis_cache=_analysis_stats(
            total=1, large=1, usable=1, usable_large=1),
    )

    assert result == output
    assert page.deleted == [2]
    assert len(document.saved) == 1
    assert "background image 1 safely" in caplog.text
    assert "cannot delete 1" in caplog.text


def test_standalone_analysis_is_a_legacy_compatible_canonical_superset(
        monkeypatch):
    safe = FakePage(0, "Reliable text " * 10, [1])
    scan = FakePage(1, "", [2])
    document = FakeDocument([safe, scan])
    _install_pymupdf(monkeypatch, document)

    stats = preprocess_cli.analyze_pdf(rag.Path("book.pdf"))

    assert stats["pages_with_usable_text"] == 1
    assert stats["large_image_pages_with_usable_text"] == 1
    assert stats["unique_image_dims"] is stats["unique_dims"]
    assert stats["total_image_xrefs"] is stats["image_xrefs"]
    assert stats["text_ok_pages"] == stats["pages_with_usable_text"]
    assert stats["inspection_complete"] is True
    assert document.closed


def test_rag_and_standalone_analysis_facades_have_canonical_parity(
        monkeypatch):
    first_pages = [
        FakePage(0, "Reliable text " * 10, [1]),
        FakePage(1, "", [2]),
    ]
    second_pages = [
        FakePage(0, "Reliable text " * 10, [1]),
        FakePage(1, "", [2]),
    ]
    _install_pymupdf(
        monkeypatch,
        FakeDocument(first_pages),
        FakeDocument(second_pages),
    )

    rag_stats = rag._analyze_pdf_images(rag.Path("book.pdf"))
    standalone_stats = preprocess_cli.analyze_pdf(rag.Path("book.pdf"))

    canonical_keys = ingestion_core.PDFImageStats.__required_keys__
    assert {
        key: rag_stats[key] for key in canonical_keys
    } == {
        key: standalone_stats[key] for key in canonical_keys
    }


def test_standalone_stripping_uses_mixed_page_safety_and_still_reports(
        monkeypatch, tmp_path):
    source = tmp_path / "book.pdf"
    source.write_bytes(b"source-pdf")
    output = tmp_path / "clean.pdf"
    safe = FakePage(0, "Reliable text " * 10, [1])
    scan = FakePage(1, "", [2])
    document = FakeDocument([safe, scan])
    verification = FakeDocument([safe, scan])
    _install_pymupdf(monkeypatch, document, verification)

    stats = preprocess_cli.strip_background_images(source, output)

    assert safe.deleted == [1]
    assert scan.deleted == []
    assert stats["images_removed"] == 1
    assert stats["output_written"] is True
    assert stats["text_verify_total"] == 2
    assert output.is_file()


def test_standalone_shared_xref_is_preserved_but_complete_output_is_saved(
        monkeypatch, tmp_path):
    source = tmp_path / "book.pdf"
    source.write_bytes(b"source-pdf")
    output = tmp_path / "clean.pdf"
    safe = FakePage(0, "Reliable text " * 10, [7])
    scan = FakePage(1, "", [7])
    document = FakeDocument([safe, scan])
    verification = FakeDocument([safe, scan])
    _install_pymupdf(monkeypatch, document, verification)

    stats = preprocess_cli.strip_background_images(source, output)

    assert safe.deleted == []
    assert scan.deleted == []
    assert stats["images_removed"] == 0
    assert stats["output_written"] is True
    assert output.is_file()


def test_standalone_incomplete_inspection_cancels_all_deletion_and_save(
        monkeypatch, tmp_path):
    source = tmp_path / "book.pdf"
    source.write_bytes(b"source-pdf")
    output = tmp_path / "clean.pdf"
    safe = FakePage(0, "Reliable text " * 10, [1])
    failed = FakePage(
        1,
        "More reliable text " * 10,
        [2],
        images_error=RuntimeError("inspection failed"),
    )
    document = FakeDocument([safe, failed])
    _install_pymupdf(monkeypatch, document)

    stats = preprocess_cli.strip_background_images(source, output)

    assert safe.deleted == []
    assert document.saved == []
    assert stats["images_removed"] == 0
    assert stats["output_written"] is False
    assert stats["failed_pages"] == [(2, "inspection failed")]
    assert not output.exists()


def test_standalone_main_does_not_print_command_for_incomplete_output(
        monkeypatch, tmp_path, capsys):
    source = tmp_path / "book.pdf"
    source.write_bytes(b"source-pdf")
    output = tmp_path / "clean.pdf"
    monkeypatch.setattr(
        preprocess_cli,
        "strip_background_images",
        lambda *_args: {
            "elapsed_sec": 1.0,
            "images_removed": 0,
            "original_mb": 1.0,
            "stripped_mb": 1.0,
            "failed_pages": [(1, "failed")],
            "text_verify_passed": 0,
            "text_verify_total": 0,
            "output_written": False,
        },
    )

    result = preprocess_cli.main([str(source), "--output", str(output)])

    output_text = capsys.readouterr().out
    assert result == 1
    assert "No output was written" in output_text
    assert "python rag.py convert" not in output_text
