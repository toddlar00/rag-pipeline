"""Typed, dependency-free PDF ingestion safety policy and planning.

The core operates on small structural PDF page/document protocols.  Facades
own PyMuPDF imports, paths, logging, progress display, saving, and CLI output.
No image deletion is attempted until every page has been inspected completely.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Literal, Protocol, TypedDict, TypeAlias


class RectangleLike(Protocol):
    width: float
    height: float


class PDFPageLike(Protocol):
    number: int
    rect: RectangleLike

    def get_images(self, *, full: bool) -> Iterable[tuple]: ...

    def get_image_rects(self, xref: int) -> Iterable[RectangleLike]: ...

    def get_text(self, kind: str = "text") -> str: ...

    def delete_image(self, xref: int) -> None: ...


class PDFDocumentLike(Protocol):
    def __len__(self) -> int: ...

    def __iter__(self) -> Iterator[PDFPageLike]: ...

    def extract_image(self, xref: int) -> Mapping[str, object]: ...


@dataclass(frozen=True)
class PDFIngestionThresholds:
    """Thresholds controlling text and background-image safety policy."""

    min_usable_page_chars: int = 40
    min_usable_text_page_ratio: float = 0.60
    min_usable_scan_text_ratio: float = 1.00
    max_replacement_char_ratio: float = 0.02
    max_cid_char_ratio: float = 0.02
    min_invisible_text_share: float = 0.50
    min_background_image_page_coverage: float = 0.70


DEFAULT_PDF_INGESTION_THRESHOLDS = PDFIngestionThresholds()


class PDFImageStats(TypedDict):
    """Canonical document analysis shared by both ingestion facades."""

    total_pages: int
    pages_with_large_images: int
    pages_with_usable_text: int
    large_image_pages_with_usable_text: int
    text_chars: int
    replacement_chars: int
    cid_garbled_pages: int
    invisible_text_pages: int
    unique_dims: set[str]
    image_xrefs: set[int]
    inspection_complete: bool


InspectionStage: TypeAlias = Literal["document", "text", "images", "delete"]
BackgroundImage: TypeAlias = tuple[int, int, int]


@dataclass(frozen=True)
class PDFInspectionIssue:
    """One secret-free problem encountered while inspecting or mutating a page."""

    page_number: int | str
    stage: InspectionStage
    detail: str
    xref: int | None = None


@dataclass(frozen=True)
class PageBackgroundInspection:
    """Background candidates plus whether every candidate was inspectable."""

    candidates: tuple[BackgroundImage, ...]
    complete: bool
    issues: tuple[PDFInspectionIssue, ...] = ()


@dataclass(frozen=True)
class PDFAnalysis:
    """Canonical image/text statistics and inspection diagnostics."""

    stats: PDFImageStats
    issues: tuple[PDFInspectionIssue, ...] = ()

    @property
    def complete(self) -> bool:
        return self.stats["inspection_complete"]


@dataclass(frozen=True)
class PageStripAssessment:
    """One fully planned page, retained only while its document is open."""

    page: PDFPageLike
    page_number: int | str
    usable_text: bool
    background_images: tuple[BackgroundImage, ...]

    @property
    def background_xrefs(self) -> tuple[int, ...]:
        return tuple(image[0] for image in self.background_images)


@dataclass(frozen=True)
class BackgroundStripPlan:
    """Complete pre-mutation plan for safe, globally deduplicated deletion."""

    pages: tuple[PageStripAssessment, ...]
    removable_xrefs: frozenset[int]
    unsafe_xrefs: frozenset[int]
    complete: bool
    issues: tuple[PDFInspectionIssue, ...] = ()


@dataclass(frozen=True)
class BackgroundStripOutcome:
    """Result of applying a complete strip plan in memory."""

    removed_xrefs: frozenset[int]
    deletion_issues: tuple[PDFInspectionIssue, ...] = ()

    @property
    def removed_count(self) -> int:
        return len(self.removed_xrefs)


PageTextUsableFn: TypeAlias = Callable[[str], bool]
PageBackgroundInspectionFn: TypeAlias = Callable[
    [PDFDocumentLike, PDFPageLike, int], PageBackgroundInspection
]
DeleteImageFn: TypeAlias = Callable[[PDFPageLike, int], None]
ProgressPagesFn: TypeAlias = Callable[
    [Iterable[PageStripAssessment]], Iterable[PageStripAssessment]
]


@dataclass(frozen=True)
class PageGlyphStats:
    """Glyph-level decode and visibility counts from one page's trace.

    ``decode_failed_glyphs`` counts each glyph at most once when it is
    unmapped, ``.notdef``, or both; the per-channel counts are
    diagnostics and may overlap.
    """

    total_glyphs: int
    unmapped_glyphs: int
    notdef_glyphs: int
    decode_failed_glyphs: int
    invisible_glyphs: int


PageGlyphStatsFn: TypeAlias = Callable[
    [PDFPageLike], "PageGlyphStats | None"
]


def _page_number(page: object) -> int | str:
    try:
        number = getattr(page, "number")
    except Exception:
        return "?"
    return number if isinstance(number, int) else str(number)


def _issue(
        page: object, stage: InspectionStage,
        error: BaseException | str, *,
        xref: int | None = None) -> PDFInspectionIssue:
    detail = str(error) or (
        error.__class__.__name__ if isinstance(error, BaseException)
        else stage
    )
    return PDFInspectionIssue(
        page_number=_page_number(page), stage=stage, detail=detail,
        xref=xref)


def cid_suspect_ratio(text: str) -> float:
    """Share of characters that decode as unmapped-glyph placeholders."""
    if not text:
        return 0.0
    suspect = sum(
        1 for character in text
        if character == "\ufffd" or "\ue000" <= character <= "\uf8ff"
    )
    return suspect / len(text)


def pdf_page_text_is_usable(
        text: str, *,
        thresholds: PDFIngestionThresholds = (
            DEFAULT_PDF_INGESTION_THRESHOLDS)) -> bool:
    """Return whether one page has enough clean extracted text to preserve."""
    non_whitespace_chars = sum(
        1 for character in text if not character.isspace())
    replacement_ratio = text.count("\ufffd") / max(len(text), 1)
    return (
        non_whitespace_chars >= thresholds.min_usable_page_chars
        and replacement_ratio <= thresholds.max_replacement_char_ratio
        and cid_suspect_ratio(text) <= thresholds.max_cid_char_ratio
    )


def pdf_text_layer_is_usable(
        stats: Mapping[str, object], *,
        large_image_pages_only: bool = False,
        thresholds: PDFIngestionThresholds = (
            DEFAULT_PDF_INGESTION_THRESHOLDS)) -> bool:
    """Assess whether extracted PDF text is safe to preserve without OCR."""
    if stats.get("inspection_complete") is False:
        return False
    if "pages_with_usable_text" not in stats:
        return True

    if large_image_pages_only:
        total = int(stats.get("pages_with_large_images", 0))
        usable = int(stats.get("large_image_pages_with_usable_text", 0))
        minimum_ratio = thresholds.min_usable_scan_text_ratio
    else:
        total = int(stats.get("total_pages", 0))
        usable = int(stats.get("pages_with_usable_text", 0))
        minimum_ratio = thresholds.min_usable_text_page_ratio

    if total <= 0:
        return True
    replacement_chars = int(stats.get("replacement_chars", 0))
    text_chars = int(stats.get("text_chars", 0))
    replacement_ratio = replacement_chars / max(text_chars, 1)
    scan_pages = int(stats.get("pages_with_large_images", 0))
    usable_scan_pages = int(
        stats.get("large_image_pages_with_usable_text", 0))
    all_scan_pages_are_usable = (
        scan_pages == 0 or usable_scan_pages == scan_pages
    )
    return (
        usable / total >= minimum_ratio
        and replacement_ratio <= thresholds.max_replacement_char_ratio
        and (large_image_pages_only or all_scan_pages_are_usable)
    )


SCANNER_FINGERPRINTS = ("paper capture", "abbyy")
PREPROCESS_STRIP_RATIO = 0.1


@dataclass(frozen=True)
class PDFTriage:
    """Deterministic first-pass report-card verdicts for one PDF."""

    page_count: int
    large_image_pages: int
    text_layer_usable: bool
    usable_pages: int
    unusable_pages: int
    preprocess_forecast: str
    scanner_fingerprint: str | None
    watermark_page_matches: int
    sampled_pages: int
    has_outline: bool
    contents_page_found: bool
    ocr_recommended: bool
    sample_read_errors: int
    cid_garbled_pages: int = 0
    invisible_text_pages: int = 0


def _scanner_fingerprint(producer: str, creator: str) -> str | None:
    haystack = f"{producer} {creator}".lower()
    for fingerprint in SCANNER_FINGERPRINTS:
        if fingerprint in haystack:
            return fingerprint
    return None


def _preprocess_forecast(stats: Mapping[str, object], *,
                         strip_ratio: float) -> str:
    """Mirror preprocess_pdf's branch order without side effects."""
    if stats.get("inspection_complete") is False:
        return "inspection-incomplete"
    total = int(stats.get("total_pages", 0) or 0)
    large = int(stats.get("pages_with_large_images", 0) or 0)
    ratio = large / max(total, 1)
    if ratio < strip_ratio:
        return "no-preprocess"
    if ("large_image_pages_with_usable_text" in stats
            and stats["large_image_pages_with_usable_text"] == 0):
        return "keep-for-ocr"
    return "strip-backgrounds"


def assess_pdf_triage(
        stats: Mapping[str, object], *, producer: str, creator: str,
        watermark_page_matches: int, sampled_pages: int,
        has_outline: bool, contents_page_found: bool,
        sample_read_errors: int = 0,
        thresholds: PDFIngestionThresholds = (
            DEFAULT_PDF_INGESTION_THRESHOLDS),
        strip_ratio: float = PREPROCESS_STRIP_RATIO) -> PDFTriage:
    """Assess one analyzed PDF into the first-pass report-card verdicts."""
    page_count = int(stats.get("total_pages", 0) or 0)
    usable_pages = int(stats.get("pages_with_usable_text", 0) or 0)
    text_layer_usable = pdf_text_layer_is_usable(
        stats, thresholds=thresholds)
    return PDFTriage(
        page_count=page_count,
        large_image_pages=int(
            stats.get("pages_with_large_images", 0) or 0),
        text_layer_usable=text_layer_usable,
        usable_pages=usable_pages,
        unusable_pages=max(page_count - usable_pages, 0),
        preprocess_forecast=_preprocess_forecast(
            stats, strip_ratio=strip_ratio),
        scanner_fingerprint=_scanner_fingerprint(producer, creator),
        watermark_page_matches=watermark_page_matches,
        sampled_pages=sampled_pages,
        has_outline=has_outline,
        contents_page_found=contents_page_found,
        ocr_recommended=not text_layer_usable,
        sample_read_errors=sample_read_errors,
        cid_garbled_pages=int(stats.get("cid_garbled_pages", 0) or 0),
        invisible_text_pages=int(
            stats.get("invisible_text_pages", 0) or 0),
    )


def inspect_page_background_images(
        document: PDFDocumentLike, page: PDFPageLike, min_dimension: int, *,
        thresholds: PDFIngestionThresholds = (
            DEFAULT_PDF_INGESTION_THRESHOLDS),
) -> PageBackgroundInspection:
    """Inspect one page without confusing failures with an empty result."""
    try:
        page_rect = page.rect
        page_area = abs(float(page_rect.width) * float(page_rect.height))
        image_infos = page.get_images(full=True)
    except Exception as exc:
        return PageBackgroundInspection(
            candidates=(), complete=False,
            issues=(_issue(page, "images", exc),),
        )
    if page_area <= 0:
        return PageBackgroundInspection(
            candidates=(), complete=False,
            issues=(_issue(page, "images", "page area is not positive"),),
        )

    candidates: list[BackgroundImage] = []
    issues: list[PDFInspectionIssue] = []
    try:
        iterator = iter(image_infos)
    except Exception as exc:
        return PageBackgroundInspection(
            candidates=(), complete=False,
            issues=(_issue(page, "images", exc),),
        )

    while True:
        try:
            image_info = next(iterator)
        except StopIteration:
            break
        except Exception as exc:
            issues.append(_issue(page, "images", exc))
            break
        try:
            xref = image_info[0]
            if isinstance(xref, bool) or not isinstance(xref, int):
                raise TypeError("image xref is not an integer")
            image = document.extract_image(xref)
            if not image:
                raise ValueError(f"image {xref} could not be extracted")
            width = image["width"]
            height = image["height"]
            if (isinstance(width, bool) or not isinstance(width, int)
                    or isinstance(height, bool) or not isinstance(height, int)):
                raise TypeError(f"image {xref} dimensions are not integers")
            if width <= min_dimension or height <= min_dimension:
                continue
            rectangles = page.get_image_rects(xref)
            coverage = max(
                (
                    min(
                        abs(float(rect.width) * float(rect.height)),
                        page_area,
                    ) / page_area
                    for rect in rectangles
                ),
                default=0.0,
            )
            if coverage >= thresholds.min_background_image_page_coverage:
                candidates.append((xref, width, height))
        except Exception as exc:
            issues.append(_issue(page, "images", exc))
    return PageBackgroundInspection(
        candidates=tuple(candidates),
        complete=not issues,
        issues=tuple(issues),
    )


def page_background_images(
        document: PDFDocumentLike, page: PDFPageLike, min_dimension: int, *,
        thresholds: PDFIngestionThresholds = (
            DEFAULT_PDF_INGESTION_THRESHOLDS),
) -> list[BackgroundImage]:
    """Compatibility view that fails closed to an empty candidate list."""
    inspection = inspect_page_background_images(
        document, page, min_dimension, thresholds=thresholds)
    return list(inspection.candidates) if inspection.complete else []


def _default_text_usable(
        text: str, thresholds: PDFIngestionThresholds) -> bool:
    return pdf_page_text_is_usable(text, thresholds=thresholds)


def _default_background_inspection(
        document: PDFDocumentLike, page: PDFPageLike, min_dimension: int,
        thresholds: PDFIngestionThresholds) -> PageBackgroundInspection:
    return inspect_page_background_images(
        document, page, min_dimension, thresholds=thresholds)


def page_glyph_stats(page: PDFPageLike) -> PageGlyphStats | None:
    """Fold one page's text trace into glyph decode/visibility counts.

    Pages without a ``get_texttrace`` method report ``None`` so existing
    protocol fakes and degraded objects keep today's behavior exactly.
    Char tuples are ``(unicode, glyph, origin, bbox)``; span type 3 marks
    invisible (Tr 3) text.  Clip-only text (Tr 7) produces no trace spans
    at all, so it is outside both counts.
    """
    get_texttrace = getattr(page, "get_texttrace", None)
    if get_texttrace is None:
        return None
    total = unmapped = notdef = decode_failed = invisible = 0
    for span in get_texttrace():
        if not isinstance(span, Mapping):
            raise TypeError("texttrace span is not a mapping")
        chars = span.get("chars") or ()
        span_invisible = span.get("type") == 3
        for char in chars:
            codepoint = int(char[0])
            glyph = int(char[1])
            total += 1
            is_unmapped = (
                codepoint == 0xFFFD or 0xE000 <= codepoint <= 0xF8FF)
            is_notdef = glyph == 0
            if is_unmapped:
                unmapped += 1
            if is_notdef:
                notdef += 1
            if is_unmapped or is_notdef:
                decode_failed += 1
            if span_invisible:
                invisible += 1
    return PageGlyphStats(
        total_glyphs=total, unmapped_glyphs=unmapped,
        notdef_glyphs=notdef, decode_failed_glyphs=decode_failed,
        invisible_glyphs=invisible)


def _glyph_page_verdicts(
        page: PDFPageLike, glyph_stats_fn: PageGlyphStatsFn,
        thresholds: PDFIngestionThresholds,
        issues: list[PDFInspectionIssue]) -> tuple[bool, bool]:
    """Return (glyph_garbled, invisible_overlay); failures degrade."""
    try:
        glyph_stats = glyph_stats_fn(page)
    except Exception as exc:
        issues.append(_issue(page, "text", f"glyph trace: {exc}"))
        return False, False
    if glyph_stats is None or glyph_stats.total_glyphs <= 0:
        return False, False
    # The CID boundary is exclusive to mirror pdf_page_text_is_usable's
    # ``<=`` acceptance; the invisible share is inclusive by design.
    garbled = (
        glyph_stats.decode_failed_glyphs / glyph_stats.total_glyphs
        > thresholds.max_cid_char_ratio)
    invisible = (
        glyph_stats.invisible_glyphs / glyph_stats.total_glyphs
        >= thresholds.min_invisible_text_share)
    return garbled, invisible


def analyze_pdf_document(
        document: PDFDocumentLike, min_dimension: int = 1000, *,
        thresholds: PDFIngestionThresholds = (
            DEFAULT_PDF_INGESTION_THRESHOLDS),
        page_text_is_usable_fn: PageTextUsableFn | None = None,
        page_background_inspection_fn: (
            PageBackgroundInspectionFn | None) = None,
        page_glyph_stats_fn: PageGlyphStatsFn | None = None,
) -> PDFAnalysis:
    """Analyze page text and background candidates without opening a PDF."""
    text_is_usable = page_text_is_usable_fn or (
        lambda text: _default_text_usable(text, thresholds))
    inspect_backgrounds = page_background_inspection_fn or (
        lambda doc, page, min_dim: _default_background_inspection(
            doc, page, min_dim, thresholds))
    glyph_stats_fn = page_glyph_stats_fn or page_glyph_stats
    issues: list[PDFInspectionIssue] = []
    inspection_complete = True
    try:
        total_pages = len(document)
        expected_pages: int | None = total_pages
    except Exception as exc:
        total_pages = 0
        expected_pages = None
        inspection_complete = False
        issues.append(_issue(document, "document", exc))
    stats: PDFImageStats = {
        "total_pages": total_pages,
        "pages_with_large_images": 0,
        "pages_with_usable_text": 0,
        "large_image_pages_with_usable_text": 0,
        "text_chars": 0,
        "replacement_chars": 0,
        "cid_garbled_pages": 0,
        "invisible_text_pages": 0,
        "unique_dims": set(),
        "image_xrefs": set(),
        "inspection_complete": False,
    }

    observed_pages = 0
    try:
        for page in document:
            observed_pages += 1
            try:
                text = page.get_text("text") or ""
                usable_text = text_is_usable(text)
            except Exception as exc:
                issues.append(_issue(page, "text", exc))
                inspection_complete = False
                text = ""
                usable_text = False
            stats["text_chars"] += len(text)
            stats["replacement_chars"] += text.count("\ufffd")
            glyph_garbled, invisible_overlay = _glyph_page_verdicts(
                page, glyph_stats_fn, thresholds, issues)
            text_garbled = (
                cid_suspect_ratio(text) > thresholds.max_cid_char_ratio)
            if text_garbled or glyph_garbled:
                stats["cid_garbled_pages"] += 1
            if glyph_garbled:
                usable_text = False
            if invisible_overlay:
                stats["invisible_text_pages"] += 1
            if usable_text:
                stats["pages_with_usable_text"] += 1

            try:
                inspection = inspect_backgrounds(
                    document, page, min_dimension)
            except Exception as exc:
                inspection = PageBackgroundInspection(
                    candidates=(), complete=False,
                    issues=(_issue(page, "images", exc),),
                )
            if not inspection.complete:
                inspection_complete = False
            issues.extend(inspection.issues)
            if inspection.candidates:
                stats["pages_with_large_images"] += 1
                for xref, width, height in inspection.candidates:
                    stats["unique_dims"].add(f"{width}x{height}")
                    stats["image_xrefs"].add(xref)
                if usable_text:
                    stats["large_image_pages_with_usable_text"] += 1
    except Exception as exc:
        inspection_complete = False
        issues.append(_issue(document, "document", exc))
    if expected_pages is not None and observed_pages != expected_pages:
        inspection_complete = False
        issues.append(_issue(
            document,
            "document",
            f"document reported {expected_pages} pages but yielded "
            f"{observed_pages}",
        ))
    if total_pages == 0 and observed_pages:
        stats["total_pages"] = observed_pages
    stats["inspection_complete"] = inspection_complete
    return PDFAnalysis(stats=stats, issues=tuple(issues))


def plan_background_image_removals(
        document: PDFDocumentLike, min_dimension: int = 1000, *,
        thresholds: PDFIngestionThresholds = (
            DEFAULT_PDF_INGESTION_THRESHOLDS),
        page_text_is_usable_fn: PageTextUsableFn | None = None,
        page_background_inspection_fn: (
            PageBackgroundInspectionFn | None) = None,
        page_glyph_stats_fn: PageGlyphStatsFn | None = None,
) -> BackgroundStripPlan:
    """Inspect every page and return a fail-closed shared-xref strip plan."""
    text_is_usable = page_text_is_usable_fn or (
        lambda text: _default_text_usable(text, thresholds))
    inspect_backgrounds = page_background_inspection_fn or (
        lambda doc, page, min_dim: _default_background_inspection(
            doc, page, min_dim, thresholds))
    glyph_stats_fn = page_glyph_stats_fn or page_glyph_stats
    pages: list[PageStripAssessment] = []
    issues: list[PDFInspectionIssue] = []
    inspection_complete = True
    try:
        expected_pages: int | None = len(document)
    except Exception as exc:
        expected_pages = None
        inspection_complete = False
        issues.append(_issue(document, "document", exc))

    try:
        for page in document:
            try:
                text = page.get_text("text") or ""
                usable_text = text_is_usable(text)
            except Exception as exc:
                issues.append(_issue(page, "text", exc))
                inspection_complete = False
                usable_text = False
            glyph_garbled, _invisible_overlay = _glyph_page_verdicts(
                page, glyph_stats_fn, thresholds, issues)
            if glyph_garbled:
                usable_text = False
            try:
                inspection = inspect_backgrounds(
                    document, page, min_dimension)
            except Exception as exc:
                inspection = PageBackgroundInspection(
                    candidates=(), complete=False,
                    issues=(_issue(page, "images", exc),),
                )
            if not inspection.complete:
                inspection_complete = False
            issues.extend(inspection.issues)
            pages.append(PageStripAssessment(
                page=page,
                page_number=_page_number(page),
                usable_text=usable_text,
                background_images=inspection.candidates,
            ))
    except Exception as exc:
        inspection_complete = False
        issues.append(_issue(document, "document", exc))
    if expected_pages is not None and len(pages) != expected_pages:
        inspection_complete = False
        issues.append(_issue(
            document,
            "document",
            f"document reported {expected_pages} pages but yielded "
            f"{len(pages)}",
        ))

    unsafe_xrefs = {
        xref
        for page in pages if not page.usable_text
        for xref in page.background_xrefs
    }
    safe_xrefs = {
        xref
        for page in pages if page.usable_text
        for xref in page.background_xrefs
    }
    complete = inspection_complete
    removable_xrefs = safe_xrefs - unsafe_xrefs if complete else set()
    return BackgroundStripPlan(
        pages=tuple(pages),
        removable_xrefs=frozenset(removable_xrefs),
        unsafe_xrefs=frozenset(unsafe_xrefs),
        complete=complete,
        issues=tuple(issues),
    )


def _delete_image(page: PDFPageLike, xref: int) -> None:
    page.delete_image(xref)


def apply_background_image_removals(
        plan: BackgroundStripPlan, *,
        delete_image_fn: DeleteImageFn = _delete_image,
        progress_pages_fn: ProgressPagesFn | None = None,
) -> BackgroundStripOutcome:
    """Apply only a complete plan, continuing after individual delete errors."""
    if not plan.complete:
        return BackgroundStripOutcome(removed_xrefs=frozenset())

    page_iterable: Iterable[PageStripAssessment] = plan.pages
    if progress_pages_fn is not None:
        page_iterable = progress_pages_fn(page_iterable)
    removed_xrefs: set[int] = set()
    issues: list[PDFInspectionIssue] = []
    for assessment in page_iterable:
        if not assessment.usable_text:
            continue
        for xref in assessment.background_xrefs:
            if (xref not in plan.removable_xrefs
                    or xref in removed_xrefs):
                continue
            try:
                delete_image_fn(assessment.page, xref)
            except Exception as exc:
                issues.append(_issue(
                    assessment.page, "delete", exc, xref=xref))
                continue
            removed_xrefs.add(xref)
    return BackgroundStripOutcome(
        removed_xrefs=frozenset(removed_xrefs),
        deletion_issues=tuple(issues),
    )
