"""Build a fixed, synthetic-only OCR challenge without loading an OCR engine.

References precede rendering and recognition. This is a diagnostic fixture,
not a representative corpus or a held-out accuracy certification. Heavy image
libraries are lazy; no document path or custom source text is accepted.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path

from evaluation_inputs import _read_snapshot
from ocr_recovery import _publish_new_report
import storage_policy


CHALLENGE_VERSION = "synthetic-ocr-v1"
TITLE = "Synthetic OCR challenge"
FOOTER = "Fictional text for OCR testing only."
PAGE_SIZE = (432, 576)
SCAN_DPI = 180
MAX_PDF_BYTES = 16 * 1024 * 1024
_BODY = (
    "Review record 17: Notice and response.",
    "Alice is liable. Bob is not liable.",
    "The filing date is 04/12/2026.",
    "Notice must arrive within 30 days.",
    "The response period is 14 days, not 40.",
    "Section 12(b) lists three conditions.",
    "Items A, B, and C remain separate.",
    "An amount of $1,250.00 is disputed.",
    "No admission is implied by silence.",
    "Footnote 7 applies only to item B.",
    "Check every number against the scan.",
    "These statements are fictional.",
)
_CRITICAL = (
    "Alice is liable.", "Bob is not liable.", "within 30 days.",
    "14 days, not 40.", "$1,250.00 is disputed.",
)


@dataclass(frozen=True)
class ChallengeCase:
    case_id: str
    layout: str = "single"
    font_size: int = 12
    skew_degrees: float = 0.0
    ink_level: int = 0
    background_level: int = 255
    blocks: tuple[tuple[str, ...], ...] = (_BODY,)
    critical_tokens: tuple[str, ...] = _CRITICAL


CASES = (
    ChallengeCase("clean"),
    ChallengeCase("skew-positive", skew_degrees=3.0),
    ChallengeCase("skew-negative", skew_degrees=-3.0),
    ChallengeCase("faint", ink_level=200, background_level=230),
    ChallengeCase("small-print", font_size=7),
    ChallengeCase(
        "columns", layout="columns", font_size=10,
        blocks=(("LEFT RECORD", "Alice received notice.", "Alice has 14 days.",
                 "Alice is liable.", "Item A remains open.", "End of left record."),
                ("RIGHT RECORD", "Bob received no notice.", "Bob has 30 days.",
                 "Bob is not liable.", "Item B remains closed.", "End of right record.")),
        critical_tokens=("Alice has 14 days.", "Bob has 30 days.", "Bob is not liable.")),
    ChallengeCase(
        "table", layout="table", font_size=10,
        blocks=(("Item", "Days", "Outcome"), ("A", "14", "liable"),
                ("B", "30", "not liable"), ("C", "40", "review pending"),
                ("D", "12", "notice sent"), ("E", "7", "no notice")),
        critical_tokens=("A 14 liable", "B 30 not liable", "E 7 no notice")),
    ChallengeCase("skew-faint", skew_degrees=3.0, ink_level=200, background_level=230),
)


def _json_bytes(payload: object) -> bytes:
    return (json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=True,
                       allow_nan=False) + "\n").encode("utf-8")


def _recipe_payload() -> dict:
    return {
        "challenge_version": CHALLENGE_VERSION, "page_size_points": list(PAGE_SIZE),
        "scan_dpi": SCAN_DPI, "font": "Helvetica", "title": TITLE, "footer": FOOTER,
        "reading_order": "header; blocks then lines (columns left first, table rows left to right); footer",
        "cases": [asdict(case) for case in CASES],
    }


def _reference_payload(source_sha256: str) -> dict:
    return {
        "schema_version": 1, "source_sha256": source_sha256,
        "pages": [
            {"page_number": number,
             "reference": "\n".join((TITLE, *(line for block in case.blocks for line in block), FOOTER)),
             "critical_tokens": list(case.critical_tokens)}
            for number, case in enumerate(CASES, 1)
        ],
    }


def _draw_case(page, case: ChallengeCase, fitz) -> None:
    def text(x: float, y: float, value: str, size: int, width: float) -> None:
        if fitz.get_text_length(value, fontname="helv", fontsize=size) > width:
            raise ValueError("synthetic challenge text exceeds its fixed layout")
        page.insert_text((x, y), value, fontname="helv", fontsize=size)

    text(36, 42, TITLE, 14, 360)
    text(36, 540, FOOTER, 9, 360)
    if case.layout == "table":
        for row, cells in enumerate(case.blocks):
            y = 98 + row * 34
            for column, value in enumerate(cells):
                text(42 + column * 120, y, value, case.font_size, 108)
        for row in range(len(case.blocks) + 1):
            page.draw_line((36, 76 + row * 34), (396, 76 + row * 34), width=0.6)
        for column in range(4):
            page.draw_line((36 + column * 120, 76),
                           (36 + column * 120, 76 + len(case.blocks) * 34), width=0.6)
    else:
        for block, lines in enumerate(case.blocks):
            x = 36 + block * 192
            width = 168 if case.layout == "columns" else 360
            for index, value in enumerate(lines):
                text(x, 98 + index * 26, value, case.font_size, width)


def _render_pdf() -> tuple[bytes, dict[str, str]]:
    # Existing locked renderer/image dependencies; no new font, network, model,
    # or ReportLab dependency is needed to reproduce this development fixture.
    import pymupdf as fitz
    import cv2
    import numpy as np

    with fitz.open() as scan:
        for case in CASES:
            with fitz.open() as vector:
                page = vector.new_page(width=PAGE_SIZE[0], height=PAGE_SIZE[1])
                _draw_case(page, case, fitz)
                pixmap = page.get_pixmap(dpi=SCAN_DPI, colorspace=fitz.csGRAY, alpha=False)
                pixels = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.height, pixmap.width)
            if case.skew_degrees:
                matrix = cv2.getRotationMatrix2D(
                    (pixmap.width / 2, pixmap.height / 2), case.skew_degrees, 1.0)
                pixels = cv2.warpAffine(
                    pixels, matrix, (pixmap.width, pixmap.height),
                    flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=255)
            pixels = np.rint(
                case.ink_level + pixels.astype(np.float32)
                * ((case.background_level - case.ink_level) / 255.0)).astype(np.uint8)
            success, encoded = cv2.imencode(".png", pixels)
            if not success:
                raise RuntimeError("synthetic image encoding failed")
            output_page = scan.new_page(width=PAGE_SIZE[0], height=PAGE_SIZE[1])
            output_page.insert_image(output_page.rect, stream=encoded.tobytes())
        scan.set_metadata({"title": "Synthetic OCR challenge v1", "producer": CHALLENGE_VERSION})
        raw = scan.tobytes(garbage=4, deflate=True, no_new_id=True)
    with fitz.open(stream=raw, filetype="pdf") as verified:
        if len(verified) != len(CASES) or any(page.get_text().strip() for page in verified):
            raise ValueError("synthetic PDF must contain only the fixed image-only cohort")
    return raw, {"pymupdf": fitz.VersionBind, "opencv": cv2.__version__, "numpy": np.__version__}


def build_challenge(output_dir: Path) -> dict:
    """Publish a new fixture directory; manifest is the last completion marker.

    Requires an existing parent. Existing directories, links and junctions are
    refused, not reused or permission-hardened. Failure may leave partial files;
    they are never recursively removed or overwritten by a subsequent attempt.
    Generation is deterministic within recorded library versions, not a promise
    of byte-identical rendering across platforms or library upgrades.
    """
    output_dir = Path(output_dir).absolute()
    storage_policy.assert_no_link_components(output_dir)
    if output_dir.exists():
        raise FileExistsError("synthetic challenge output already exists")
    if not output_dir.parent.is_dir():
        raise ValueError("synthetic challenge output requires an existing parent directory")
    source = Path(__file__)
    generator_digest = hashlib.sha256(source.read_bytes()).hexdigest()
    recipe_bytes = _json_bytes(_recipe_payload())
    # Freeze authored references before any rendering or OCR. The source binding
    # can only be filled after the image-only PDF bytes have been produced.
    references = _reference_payload("0" * 64)
    output_dir.mkdir(mode=0o700, exist_ok=False)
    storage_policy.enforce_private_path(output_dir, directory=True)
    directory_identity = storage_policy._parent_identity(output_dir)

    def publish(temporary, destination) -> None:
        storage_policy.assert_no_link_components(output_dir)
        if storage_policy._parent_identity(output_dir) != directory_identity:
            raise RuntimeError("synthetic challenge directory changed before commit")
        _publish_new_report(temporary, destination)

    raw, libraries = _render_pdf()
    if not isinstance(raw, bytes) or not raw.startswith(b"%PDF-") or len(raw) > MAX_PDF_BYTES:
        raise ValueError("synthetic renderer returned invalid or oversized PDF bytes")
    if hashlib.sha256(source.read_bytes()).hexdigest() != generator_digest:
        raise RuntimeError("synthetic generator changed during rendering")
    references["source_sha256"] = hashlib.sha256(raw).hexdigest()
    reference_bytes = _json_bytes(references)
    manifest = {
        "schema_version": 1, "kind": "ocr_synthetic_challenge",
        "challenge_version": CHALLENGE_VERSION, "page_count": len(CASES),
        "source_sha256": references["source_sha256"],
        "references_sha256": hashlib.sha256(reference_bytes).hexdigest(),
        "recipe_sha256": hashlib.sha256(recipe_bytes).hexdigest(),
        "generator_sha256": generator_digest, "libraries": dict(libraries),
        "rendering": {
            "page_size_points": list(PAGE_SIZE), "source_dpi": SCAN_DPI,
            "font": "MuPDF built-in Helvetica (renderer-version-bound)",
            "rotation": "OpenCV linear interpolation; fixed canvas; white border before tone remap",
            "tone": "round(ink + gray * (background - ink) / 255)",
        },
        "cases": [{"page_number": number, **{
            key: value for key, value in asdict(case).items()
            if key not in {"blocks", "critical_tokens"}}} for number, case in enumerate(CASES, 1)],
        "scope": "fixed synthetic diagnostic only; not held-out or representative document accuracy",
    }
    for name, content in (("challenge.pdf", raw), ("references.json", reference_bytes),
                          ("manifest.json", _json_bytes(manifest))):
        storage_policy.assert_no_link_components(output_dir)
        if storage_policy._parent_identity(output_dir) != directory_identity:
            raise RuntimeError("synthetic challenge directory changed before publication")
        if name == "manifest.json":
            for artifact, limit, digest in (
                ("challenge.pdf", MAX_PDF_BYTES, manifest["source_sha256"]),
                ("references.json", 1024 * 1024, manifest["references_sha256"]),
            ):
                _, current = _read_snapshot(
                    output_dir / artifact, label="synthetic challenge artifact", max_bytes=limit)
                if current != digest:
                    raise RuntimeError("synthetic challenge artifact changed before completion")
            if hashlib.sha256(source.read_bytes()).hexdigest() != generator_digest:
                raise RuntimeError("synthetic generator changed before completion")
        storage_policy.atomic_write_private(
            output_dir / name, lambda handle, data=content: handle.write(data),
            text=False, replace_fn=publish)
    return manifest
