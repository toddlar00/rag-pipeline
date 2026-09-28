"""Synthetic-only review annotations; no PDF, UI or OCR imports required."""

import copy
import json

import pytest

import ocr_recovery
from ocr_review import ReviewDocument, click_rectangle, rectangle, text_difference


def report():
    lines = []
    for text, left, top, right, bottom in (
        ("Header", 5, 1, 95, 5), ("Alice", 5, 20, 40, 25), ("Bob", 60, 20, 95, 25),
        ("Yes", 5, 30, 40, 35), ("No", 60, 30, 95, 35), ("Footer", 5, 90, 95, 95),
    ):
        lines.append({"text": text, "score": .9, "box": [[left, top], [right, top], [right, bottom], [left, bottom]]})
    candidate = {"text": "\n".join(line["text"] for line in lines), "lines": lines, "mean_confidence": .9,
                 "raster": {"width": 100, "height": 100, "dpi": 300, "coordinate_system": "rendered_image_pixels"},
                 "engine": {"name": "rapidocr", "version": "synthetic", "min_score": 0., "max_side": 6000}}

    class Reader:
        page_count = 1

        def native_text(self, number):
            return ""

        def retry(self, number):
            return candidate

    value = ocr_recovery.build_recovery_report(Reader(), source_sha256="a" * 64,
                                              policy=ocr_recovery.RetryPolicy(), requested_pages=(1,))
    value["evidence_sha256"] = None
    return value


@pytest.fixture
def document():
    return ReviewDocument(report(), recovery_sha256="b" * 64)


@pytest.mark.parametrize("bad", [None, {}, "0,0,1,1", [], [0, 0, 1], [0, 0, 1, 1, 1],
                                  [True, 0, 1, 1], [0, 0, float("nan"), 1], [0, 0, float("inf"), 1],
                                  [-.1, 0, 1, 1], [0, 0, 2, 1], [0, 1, 1, 0], [0, 0, 0, 1],
                                  [0, 0, 10 ** 1000, 1]])
def test_rectangle_rejects_invalid_bounds(bad):
    with pytest.raises(ValueError):
        rectangle(bad)


@pytest.mark.parametrize("a,b", [([10, 20], [90, 80]), ([90, 80], [10, 20]), ([90, 20], [10, 80])])
def test_two_clicks_normalize_either_direction(a, b):
    assert click_rectangle(a, b, width=100, height=100) == [.1, .2, .9, .8]


@pytest.mark.parametrize("point", [None, [], [0], [0, 0, 0], [True, 2], [float("nan"), 2],
                                    [float("inf"), 2], [10 ** 1000, 2], [-1, 2], [101, 2]])
def test_clicks_are_bounded(point):
    with pytest.raises(ValueError):
        click_rectangle(point, [50, 50], width=100, height=100)


@pytest.mark.parametrize("width,height", [(0, 100), (6001, 100), (True, 100), (100, 100.)])
def test_canvas_dimensions_strict(width, height):
    with pytest.raises(ValueError):
        click_rectangle([0, 0], [50, 50], width=width, height=height)


@pytest.mark.parametrize("number", [0, 2, True, 1., "1", None])
def test_page_number_strict(document, number):
    with pytest.raises(ValueError):
        document.page(number)


def test_report_and_page_are_copied(document):
    value = report()
    another = ReviewDocument(value, recovery_sha256="b" * 64)
    value["pages"][0]["candidate"]["text"] = "changed"
    page = another.page(1)
    page["candidate"]["lines"].clear()
    assert another.page(1) == document.page(1)


def test_layout_uses_canvas_fractions_and_binds_digests(document):
    entry = document.layout_page(1, [0, .1, 1, .8], [.45, .1, .55, .8])
    plan = document.layout_plan([entry])
    assert plan["source_sha256"] == "a" * 64
    assert plan["recovery_sha256"] == "b" * 64
    assert plan["pages"][0]["body_band"] == [.1, .8]
    assert plan["pages"][0]["gutter"] == [.45, .55]
    before = json.dumps(document.page(1), sort_keys=True)
    result = document.preview_layout([entry])
    assert result["pages"][0]["line_order"] == [0, 1, 3, 2, 4, 5]
    assert result["canonical_extraction_modified"] is False
    assert before == json.dumps(document.page(1), sort_keys=True)


def test_ambiguous_layout_abstains(document):
    entry = document.layout_page(1, [0, .1, 1, .8], [.3, .1, .7, .8])
    assert document.preview_layout([entry])["pages"][0]["status"] == "abstained"


def test_source_rectangle_identity(document):
    assert document.source_rectangle(1, [.1, .2, .8, .9]) == [.1, .2, .8, .9]


def test_source_rectangle_inverse_envelope_and_padding(document):
    # Test the conversion independently; the constructor's report validator
    # normally verifies the complete affine metadata contract.
    document._recovery["pages"][0]["candidate"]["preprocessing"] = {
        "original_raster": {"width": 80, "height": 80},
        "processed_to_source": [[1., 0., -10.], [0., 1., -10.]],
    }
    assert document.source_rectangle(1, [0, 0, 1, 1]) == [0, 0, 1, 1]
    assert document.source_rectangle(1, [.2, .2, .6, .6]) == [.125, .125, .625, .625]
    with pytest.raises(ValueError):
        document.source_rectangle(1, [0, 0, .05, .05])


@pytest.mark.parametrize("confirmed", [False, None, 1, "yes"])
def test_reference_requires_explicit_boolean_review(document, confirmed):
    with pytest.raises(ValueError):
        document.references([{"page_number": 1, "reference": "Human text"}], confirmed=confirmed)


def test_reference_schema_and_copy(document):
    pages = [{"page_number": 1, "reference": "Human text"}]
    result = document.references(pages, confirmed=True)
    pages[0]["reference"] = "changed"
    assert result["pages"][0]["reference"] == "Human text"
    with pytest.raises(ValueError):
        document.references([{"page_number": 1, "reference": "x" * 20_001}], confirmed=True)


def test_failed_candidate_is_reviewable_but_not_an_empty_success():
    value = report()
    value["pages"][0].update(status="retry_failed", candidate=None, error_code="retry_runtime_failed")
    value["summary"].update(failed=1, review_required=0)
    document = ReviewDocument(value, recovery_sha256="b" * 64)
    assert document.page_numbers == (1,)
    assert document.page(1)["candidate"] is None
    assert "No OCR candidate" in document.page_notice(1)
    assert "retry failed" in document.review_choices()[0][0]
    assert document.source_rectangle(1, [.1, .2, .8, .9]) == [.1, .2, .8, .9]
    assert document.references([{"page_number": 1, "reference": "From scan"}], confirmed=True)
    with pytest.raises(ValueError, match="available OCR candidate"):
        document.layout_page(1, [0, .1, 1, .8], [.45, .1, .55, .8])


def missing_report():
    class Reader:
        page_count = 3

        def native_text(self, number):
            return "" if number < 3 else "Generated native text. " * 10

        def retry(self, number):
            raise RuntimeError("synthetic failure")

    value = ocr_recovery.build_recovery_report(Reader(), source_sha256="a" * 64,
                                              policy=ocr_recovery.RetryPolicy(max_pages=1), requested_pages=(1,))
    value["evidence_sha256"] = None
    return value


def test_deferred_and_not_selected_pages_are_available_without_fabricating_candidates():
    value = missing_report()
    before = copy.deepcopy(value)
    document = ReviewDocument(value, recovery_sha256="b" * 64)
    assert document.page_numbers == (1, 2, 3)
    assert [document.page(n)["status"] for n in document.page_numbers] == ["retry_failed", "deferred", "not_selected"]
    assert all(document.page(n)["candidate"] is None for n in document.page_numbers)
    assert document.page(2)["reasons"] == ["empty_text"]
    assert document.page_text(3) == ""  # Unsaved native text is unavailable, not inferred.
    assert "not selected" in document.page_notice(3)
    assert value == before


def test_review_order_prioritizes_missing_candidates_not_confidence():
    value = report()
    value["page_count"] = 3
    value["selection"]["max_pages"] = 1
    value["summary"].update(inspected=3, deferred=1)
    value["deferred_pages"] = [{"page_number": 3, "reasons": ["empty_text"]}]
    document = ReviewDocument(value, recovery_sha256="b" * 64)
    assert [number for _, number in document.review_choices()] == [3, 2, 1]


def test_diff_plain_text_and_bounds():
    assert "-<script>" in text_difference("<script>", "<img>")
    assert text_difference("same", "same") == "No text differences."
    assert "omitted" in text_difference("a\n" * 501, "b\n" * 501)
    with pytest.raises(ValueError):
        text_difference("x" * 100_001, "")


def test_future_recovery_rejected():
    value = copy.deepcopy(report())
    value["schema_version"] = 99
    with pytest.raises(ValueError):
        ReviewDocument(value, recovery_sha256="b" * 64)
