import subprocess
import sys

import pytest

import ingestion_core


class FakeRect:
    def __init__(self, width=100, height=200):
        self.width = width
        self.height = height


class FakePage:
    def __init__(
            self, number, text, images=(), *, rectangles=None,
            text_error=None, images_error=None, delete_errors=()):
        self.number = number
        self.text = text
        self.images = list(images)
        self.rect = FakeRect()
        self.rectangles = rectangles or {
            image[0]: [FakeRect()] for image in images
        }
        self.text_error = text_error
        self.images_error = images_error
        self.delete_errors = set(delete_errors)
        self.deleted = []

    def get_text(self, kind="text"):
        if self.text_error is not None:
            raise self.text_error
        return self.text

    def get_images(self, *, full=True):
        if self.images_error is not None:
            raise self.images_error
        return self.images

    def get_image_rects(self, xref):
        value = self.rectangles.get(xref, [])
        if isinstance(value, BaseException):
            raise value
        return value

    def delete_image(self, xref):
        if xref in self.delete_errors:
            raise RuntimeError(f"cannot delete {xref}")
        self.deleted.append(xref)


class FakeDocument:
    def __init__(self, pages, *, image_dimensions=None, extract_errors=()):
        self.pages = list(pages)
        self.image_dimensions = image_dimensions or {
            image[0]: (2000, 3000)
            for page in self.pages for image in page.images
        }
        self.extract_errors = set(extract_errors)

    def __len__(self):
        return len(self.pages)

    def __iter__(self):
        return iter(self.pages)

    def extract_image(self, xref):
        if xref in self.extract_errors:
            raise RuntimeError(f"cannot extract {xref}")
        width, height = self.image_dimensions[xref]
        return {"width": width, "height": height}


def test_ingestion_core_is_a_standard_library_only_leaf_module():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import ingestion_core; "
                "forbidden = {'rag', 'preprocess_pdf', 'pymupdf', 'tqdm', "
                "'docling', 'torch', 'requests', 'chromadb', "
                "'qdrant_client'}; "
                "loaded = forbidden.intersection(sys.modules); "
                "assert not loaded, sorted(loaded)"
            ),
        ],
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr


def test_page_text_policy_preserves_character_and_replacement_boundaries():
    assert ingestion_core.pdf_page_text_is_usable("x" * 40)
    assert not ingestion_core.pdf_page_text_is_usable("x" * 39)
    assert ingestion_core.pdf_page_text_is_usable("\ufffd" + "x" * 49)
    assert not ingestion_core.pdf_page_text_is_usable("\ufffd" + "x" * 48)
    assert not ingestion_core.pdf_page_text_is_usable("x " * 39)


def test_text_layer_policy_preserves_document_scan_and_legacy_rules():
    mixed_stats = {
        "total_pages": 10,
        "pages_with_large_images": 10,
        "pages_with_usable_text": 6,
        "large_image_pages_with_usable_text": 6,
        "text_chars": 1000,
        "replacement_chars": 0,
    }

    assert not ingestion_core.pdf_text_layer_is_usable(mixed_stats)
    assert not ingestion_core.pdf_text_layer_is_usable(
        mixed_stats, large_image_pages_only=True)
    assert ingestion_core.pdf_text_layer_is_usable({"total_pages": 10})
    assert not ingestion_core.pdf_text_layer_is_usable({
        **mixed_stats,
        "inspection_complete": False,
    })
    assert ingestion_core.pdf_text_layer_is_usable({
        **mixed_stats,
        "total_pages": 0,
        "pages_with_large_images": 0,
    })
    assert not ingestion_core.pdf_text_layer_is_usable({
        **mixed_stats,
        "total_pages": 4,
        "pages_with_large_images": 0,
        "pages_with_usable_text": 4,
        "text_chars": 100,
        "replacement_chars": 3,
    })


@pytest.mark.parametrize(
    ("dimensions", "rect", "expected"),
    [
        ((1000, 3000), FakeRect(), ()),
        ((2000, 3000), FakeRect(width=69, height=200), ()),
        ((2000, 3000), FakeRect(width=70, height=200), ((1, 2000, 3000),)),
    ],
)
def test_background_inspection_preserves_dimension_and_coverage_edges(
        dimensions, rect, expected):
    page = FakePage(0, "text", [(1,)], rectangles={1: [rect]})
    document = FakeDocument([page], image_dimensions={1: dimensions})

    inspection = ingestion_core.inspect_page_background_images(
        document, page, 1000)

    assert inspection.complete
    assert inspection.candidates == expected


@pytest.mark.parametrize("failure", ["images", "extract", "rectangles"])
def test_background_inspection_distinguishes_failure_from_no_candidates(
        failure):
    page = FakePage(
        0,
        "Reliable text " * 10,
        [(1,)],
        images_error=(RuntimeError("images failed")
                      if failure == "images" else None),
        rectangles={
            1: (RuntimeError("rectangles failed")
                if failure == "rectangles" else [FakeRect()])
        },
    )
    document = FakeDocument(
        [page], extract_errors=({1} if failure == "extract" else set()))

    inspection = ingestion_core.inspect_page_background_images(
        document, page, 1000)

    assert not inspection.complete
    assert inspection.issues[0].stage == "images"
    assert ingestion_core.page_background_images(
        document, page, 1000) == []


def test_document_analysis_returns_canonical_stats_and_completeness():
    safe = FakePage(0, "Reliable text " * 10, [(1,)])
    scan = FakePage(1, "", [(2,)])
    document = FakeDocument([safe, scan])

    analysis = ingestion_core.analyze_pdf_document(document)

    assert analysis.complete
    assert analysis.stats == {
        "total_pages": 2,
        "pages_with_large_images": 2,
        "pages_with_usable_text": 1,
        "large_image_pages_with_usable_text": 1,
        "text_chars": len(safe.text),
        "replacement_chars": 0,
        "unique_dims": {"2000x3000"},
        "image_xrefs": {1, 2},
        "inspection_complete": True,
    }


def test_text_inspection_failure_marks_analysis_and_plan_incomplete():
    safe = FakePage(0, "Reliable text " * 10, [(1,)])
    failed = FakePage(
        1, "", [(2,)], text_error=RuntimeError("text failed"))
    document = FakeDocument([safe, failed])

    analysis = ingestion_core.analyze_pdf_document(document)
    plan = ingestion_core.plan_background_image_removals(document)

    assert not analysis.complete
    assert not plan.complete
    assert plan.removable_xrefs == frozenset()
    assert any(issue.stage == "text" for issue in plan.issues)


def test_plan_preserves_mixed_scan_pages_and_removes_only_safe_xrefs():
    safe = FakePage(0, "Reliable text " * 10, [(1,)])
    scan = FakePage(1, "", [(2,)])
    document = FakeDocument([safe, scan])

    plan = ingestion_core.plan_background_image_removals(document)
    outcome = ingestion_core.apply_background_image_removals(plan)

    assert plan.complete
    assert plan.removable_xrefs == frozenset({1})
    assert plan.unsafe_xrefs == frozenset({2})
    assert outcome.removed_xrefs == frozenset({1})
    assert safe.deleted == [1]
    assert scan.deleted == []


def test_plan_excludes_background_xref_shared_with_scan_only_page():
    safe = FakePage(0, "Reliable text " * 10, [(7,)])
    scan = FakePage(1, "", [(7,)])
    document = FakeDocument([safe, scan])

    plan = ingestion_core.plan_background_image_removals(document)
    outcome = ingestion_core.apply_background_image_removals(plan)

    assert plan.complete
    assert plan.unsafe_xrefs == frozenset({7})
    assert plan.removable_xrefs == frozenset()
    assert outcome.removed_count == 0
    assert safe.deleted == []
    assert scan.deleted == []


def test_apply_deletes_xref_shared_only_by_safe_pages_once():
    first = FakePage(0, "Reliable text " * 10, [(7,)])
    second = FakePage(1, "More reliable text " * 10, [(7,)])
    document = FakeDocument([first, second])

    plan = ingestion_core.plan_background_image_removals(document)
    outcome = ingestion_core.apply_background_image_removals(plan)

    assert outcome.removed_xrefs == frozenset({7})
    assert first.deleted == [7]
    assert second.deleted == []


def test_incomplete_inspection_prevents_every_deletion():
    safe = FakePage(0, "Reliable text " * 10, [(1,)])
    failed = FakePage(
        1, "More reliable text " * 10, [(2,)],
        images_error=RuntimeError("inspection failed"))
    document = FakeDocument([safe, failed])

    plan = ingestion_core.plan_background_image_removals(document)
    outcome = ingestion_core.apply_background_image_removals(plan)

    assert not plan.complete
    assert plan.removable_xrefs == frozenset()
    assert outcome.removed_count == 0
    assert safe.deleted == []


def test_silently_short_document_iteration_fails_analysis_and_plan_closed():
    safe = FakePage(0, "Reliable text " * 10, [(1,)])

    class ShortDocument(FakeDocument):
        def __len__(self):
            return 2

    document = ShortDocument([safe])

    analysis = ingestion_core.analyze_pdf_document(document)
    plan = ingestion_core.plan_background_image_removals(document)
    outcome = ingestion_core.apply_background_image_removals(plan)

    assert not analysis.complete
    assert not plan.complete
    assert plan.removable_xrefs == frozenset()
    assert any("reported 2 pages but yielded 1" in issue.detail
               for issue in plan.issues)
    assert outcome.removed_count == 0
    assert safe.deleted == []


def test_deletion_errors_preserve_partial_success_and_continue():
    page = FakePage(
        0, "Reliable text " * 10, [(1,), (2,)], delete_errors={1})
    document = FakeDocument([page])

    plan = ingestion_core.plan_background_image_removals(document)
    outcome = ingestion_core.apply_background_image_removals(plan)

    assert outcome.removed_xrefs == frozenset({2})
    assert len(outcome.deletion_issues) == 1
    assert outcome.deletion_issues[0].stage == "delete"
    assert outcome.deletion_issues[0].xref == 1
    assert page.deleted == [2]
