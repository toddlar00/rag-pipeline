import sys
from types import SimpleNamespace

import pytest

import rag


def test_effective_chunk_limit_respects_embedding_model_capacity():
    # Nomic v2 accepts at most 512 tokens. Raw chunks must leave room for the
    # tokenizer's two special tokens and the four-token document-task prefix.
    assert rag._effective_chunk_token_limit(
        rag.DEFAULT_EMBEDDING_MODEL_GENERAL, 4096) == 506
    assert rag._effective_chunk_token_limit(
        rag.DEFAULT_EMBEDDING_MODEL_GENERAL,
        4096,
        reserve_tokens=192,
    ) == 314
    assert rag._effective_chunk_token_limit(
        "nlpaueb/legal-bert-base-uncased", 4096) == 510
    assert rag._effective_chunk_token_limit(
        "nlpaueb/legal-bert-base-uncased",
        4096,
        reserve_tokens=192,
    ) == 318
    assert rag.EMBEDDING_MAX_TOKENS["voyage-law-2"] == 16_000
    assert rag.EMBEDDING_MAX_TOKENS["voyage-3-large"] == 32_000
    assert rag.EMBEDDING_MAX_TOKENS["voyage-4-large"] == 32_000
    assert rag._effective_chunk_token_limit("unknown/model", 9000) == 9000


@pytest.mark.parametrize("value", [0, -1])
def test_effective_chunk_limit_rejects_nonpositive_values(value):
    with pytest.raises(ValueError, match="at least 1"):
        rag._effective_chunk_token_limit(
            rag.DEFAULT_EMBEDDING_MODEL_GENERAL, value)


def test_embedding_validation_rejects_known_oversized_chunks():
    records = [{
        "text": "long",
        "metadata": {
            "token_count": 500,
            "embedding_token_count": 513,
        },
    }]

    with pytest.raises(ValueError, match="513"):
        rag._validate_embedding_token_counts(
            records, "nlpaueb/legal-bert-base-uncased")


def test_embedding_validation_keeps_legacy_chunks_without_counts():
    rag._validate_embedding_token_counts(
        [{"text": "legacy", "metadata": {}}],
        "nlpaueb/legal-bert-base-uncased",
    )


def test_embedding_validation_recomputes_instead_of_trusting_metadata(
        monkeypatch):
    records = [{
        "text": "forged count",
        "metadata": {"embedding_token_count": 1},
    }]
    monkeypatch.setattr(
        rag, "_count_embedding_text_tokens",
        lambda texts, model: ([513], True),
    )

    with pytest.raises(ValueError, match="513"):
        rag._validate_embedding_token_counts(
            records,
            "nlpaueb/legal-bert-base-uncased",
            recompute=True,
        )


def test_embedding_validation_rejects_actual_prefixed_nomic_count(
        monkeypatch):
    encoded_inputs = []

    class FakeTokenizer:
        @classmethod
        def from_pretrained(cls, *_args, **_kwargs):
            return cls()

        def encode(self, text, **kwargs):
            encoded_inputs.append((text, kwargs))
            # The unprefixed payload would fit. The actual Nomic document
            # payload, including its task prefix, crosses the model boundary.
            size = 513 if text.startswith("search_document: ") else 509
            return list(range(size))

    monkeypatch.setattr(
        rag, "_model_loader_source",
        lambda *_args: ("verified/tokenizer", True),
    )
    monkeypatch.setitem(
        sys.modules,
        "transformers",
        SimpleNamespace(AutoTokenizer=FakeTokenizer),
    )
    records = [{
        "text": "body",
        "metadata": {"context": "retrieval context"},
    }]

    with pytest.raises(ValueError) as error:
        rag._validate_embedding_token_counts(
            records,
            rag.DEFAULT_EMBEDDING_MODEL_GENERAL,
            recompute=True,
        )

    assert "512-token" in str(error.value)
    assert "513" in str(error.value)
    assert encoded_inputs == [(
        "search_document: retrieval context\n\nbody",
        {"add_special_tokens": True, "truncation": False},
    )]


def test_api_embedding_batches_respect_aggregate_token_budget(monkeypatch):
    monkeypatch.setattr(rag, "_API_EMBEDDING_BATCH_TOKEN_BUDGET", 100)
    records = [
        {
            "text": f"record-{index}",
            "metadata": {"embedding_token_count": token_count},
        }
        for index, token_count in enumerate((60, 60, 40, 40))
    ]

    batches = rag._batch_index_records(
        records, "voyage-law-2", max_records=25)

    assert [[item["text"] for item in batch] for batch in batches] == [
        ["record-0"],
        ["record-1", "record-2"],
        ["record-3"],
    ]


def test_pdf_text_layer_quality_distinguishes_scan_and_document_thresholds():
    stats = {
        "total_pages": 10,
        "pages_with_large_images": 10,
        "pages_with_usable_text": 6,
        "large_image_pages_with_usable_text": 6,
        "text_chars": 1000,
        "replacement_chars": 0,
    }

    assert rag._pdf_text_layer_is_usable(stats) is False
    assert rag._pdf_text_layer_is_usable(
        stats, large_image_pages_only=True) is False


def test_pdf_text_layer_quality_rejects_corrupt_extracted_text():
    stats = {
        "total_pages": 4,
        "pages_with_large_images": 0,
        "pages_with_usable_text": 4,
        "large_image_pages_with_usable_text": 0,
        "text_chars": 100,
        "replacement_chars": 3,
    }

    assert rag._pdf_text_layer_is_usable(stats) is False


def test_preprocess_does_not_strip_image_only_scan(monkeypatch, tmp_path):
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"pdf")
    output = tmp_path / "stripped.pdf"
    stats = {
        "total_pages": 5,
        "pages_with_large_images": 5,
        "pages_with_usable_text": 0,
        "large_image_pages_with_usable_text": 0,
        "text_chars": 0,
        "replacement_chars": 0,
        "unique_dims": {"2000x3000"},
        "image_xrefs": {1},
    }
    monkeypatch.setitem(sys.modules, "pymupdf", SimpleNamespace())

    result = rag.preprocess_pdf(
        source, output, _analysis_cache=stats)

    assert result is None
    assert not output.exists()


def test_preprocess_strips_only_pages_with_safe_text(monkeypatch, tmp_path):
    source = tmp_path / "mixed.pdf"
    source.write_bytes(b"source-pdf")
    output = tmp_path / "stripped.pdf"

    class FakePage:
        def __init__(self, number, text, xref):
            self.number = number
            self.text = text
            self.xref = xref
            self.deleted = []
            self.rect = SimpleNamespace(width=100, height=200)

        def get_text(self, kind):
            return self.text

        def get_images(self, full=True):
            return [(self.xref,)]

        def get_image_rects(self, xref):
            return [SimpleNamespace(width=100, height=200)]

        def delete_image(self, xref):
            self.deleted.append(xref)

    safe_page = FakePage(0, "Reliable text " * 10, 1)
    scan_only_page = FakePage(1, "", 2)

    class FakeDocument:
        def __len__(self):
            return 2

        def __iter__(self):
            return iter([safe_page, scan_only_page])

        def extract_image(self, xref):
            return {"width": 2000, "height": 3000}

        def save(self, path, **kwargs):
            rag.Path(path).write_bytes(b"smaller")

        def close(self):
            pass

    monkeypatch.setitem(
        sys.modules,
        "pymupdf",
        SimpleNamespace(open=lambda path: FakeDocument()),
    )
    stats = {
        "total_pages": 2,
        "pages_with_large_images": 2,
        "pages_with_usable_text": 1,
        "large_image_pages_with_usable_text": 1,
        "text_chars": 140,
        "replacement_chars": 0,
        "unique_dims": {"2000x3000"},
        "image_xrefs": {1, 2},
    }

    result = rag.preprocess_pdf(
        source, output, _analysis_cache=stats)

    assert result == output
    assert safe_page.deleted == [1]
    assert scan_only_page.deleted == []


def test_large_substantive_figure_is_not_treated_as_page_background():
    page = SimpleNamespace(
        rect=SimpleNamespace(width=100, height=200),
        get_images=lambda full=True: [(7,)],
        get_image_rects=lambda xref: [SimpleNamespace(width=40, height=40)],
    )
    document = SimpleNamespace(
        extract_image=lambda xref: {"width": 2000, "height": 2000})

    assert rag._page_background_images(document, page, 1000) == []


def test_forced_ocr_keeps_page_images(monkeypatch, tmp_path):
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"pdf")
    output = tmp_path / "doc.json"
    monkeypatch.setattr(
        rag,
        "_analyze_pdf_images",
        lambda path: {
            "total_pages": 1,
            "pages_with_large_images": 1,
            "pages_with_usable_text": 1,
            "large_image_pages_with_usable_text": 1,
            "text_chars": 100,
            "replacement_chars": 0,
            "unique_dims": {"2000x3000"},
            "image_xrefs": {1},
        },
    )
    monkeypatch.setattr(
        rag,
        "preprocess_pdf",
        lambda *args, **kwargs: pytest.fail(
            "OCR must retain the original page images"),
    )

    class StopAfterPreprocessing(Exception):
        pass

    monkeypatch.setattr(
        rag,
        "_page_count",
        lambda path: (_ for _ in ()).throw(StopAfterPreprocessing),
    )

    with pytest.raises(StopAfterPreprocessing):
        rag.convert_pdf(source, output, backend="auto", ocr=True)


@pytest.mark.parametrize(
    ("ocr_args", "expected"),
    [(["--ocr"], True), (["--no-ocr"], False), ([], None)],
)
def test_convert_cli_forwards_ocr_override(monkeypatch, ocr_args, expected):
    observed = {}

    def fake_convert(*args, **kwargs):
        observed.update(kwargs)

    monkeypatch.setattr(rag, "convert_pdf", fake_convert)
    monkeypatch.setattr(
        sys,
        "argv",
        ["rag.py", "convert", "--pdf", "book.pdf", *ocr_args],
    )

    rag.main()

    assert observed["ocr"] is expected


@pytest.mark.parametrize(
    ("ocr", "expected_flag"),
    [(True, "--ocr"), (False, "--no-ocr")],
)
def test_resume_command_preserves_explicit_ocr_choice(ocr, expected_flag):
    command = rag._build_resume_cmd(
        rag.Path("Book.pdf"), SimpleNamespace(ocr=ocr))

    assert expected_flag in command
