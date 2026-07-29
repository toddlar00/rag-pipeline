"""Deterministic, platform-specific derivatives of canonical chapter Markdown.

This module is intentionally standard-library only.  It owns no pipeline I/O;
callers provide receipt-bound chapter text and publish the returned documents.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import html
import json
from pathlib import Path
import re
from typing import Iterable, Sequence
from urllib.parse import unquote, urlsplit


AI_PROJECT_EXPORT_SCHEMA_VERSION = 2
AI_PROJECT_PROFILE_VERSION = 2
AI_PROJECT_GROUPING_VERSION = 1
AI_PROJECT_NORMALIZATION_VERSION = 2
AI_PROJECT_ENDNOTE_RELABEL_VERSION = 2
AI_PROJECT_COMMENT_POLICY_VERSION = 2
AI_PROJECT_LINK_POLICY_VERSION = 2
MAX_AI_PROJECT_RECEIPT_BYTES = 4 * 1024 * 1024

_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_CHAPTER_NAME_RE = re.compile(r"ch(?P<number>\d+)(?:_[\w-]+)?\.md")
_FOOTNOTE_REFERENCE_RE = re.compile(r"\[\^([1-9][0-9]*)\](?!:)")
_FOOTNOTE_DEFINITION_RE = re.compile(r"(?m)^\[\^([1-9][0-9]*)\]:")
_FOOTNOTE_TOKEN_RE = re.compile(r"\[\^([1-9][0-9]*)\]")
_ANY_FOOTNOTE_TOKEN_RE = re.compile(r"\[\^([^\]\r\n]+)\]")
_HTML_COMMENT_RE = re.compile(r"<!--[\s\S]*?-->")
_PAGE_MARKER_RE = re.compile(
    r"(?m)^\[PDF page(?:s)? [1-9][0-9]*(?:[\u2013-][1-9][0-9]*)?\]$")
_INLINE_MARKDOWN_LINK_RE = re.compile(
    r"(?P<image>!)?\[(?P<label>(?:\\.|[^\]\\])*)\]\("
    r"[ \t]*(?P<destination><(?:\\.|[^>\n])*>|(?:\\.|[^()\s])+?)"
    r"(?:[ \t]+(?:\"(?:\\.|[^\"\n])*\"|'(?:\\.|[^'\n])*'|"
    r"\((?:\\.|[^)\n])*\)))?[ \t]*\)")
_REFERENCE_DEFINITION_RE = re.compile(
    r"(?m)^[ \t]{0,3}\[(?!\^)(?P<label>(?:\\.|[^\]])+)\]:[ \t]*"
    r"(?:\n[ \t]{0,3})?"
    r"(?P<destination><[^>\n]+>|\S+)")
_RAW_HTML_DESTINATION_RE = re.compile(
    r"(?is)\b(?P<attribute>href|src|srcset|poster|data)\s*=\s*(?:"
    r"(?P<quote>['\"])(?P<quoted>.*?)(?P=quote)|(?P<bare>[^\s>]+))")
_ANGLE_LOCAL_PATH_RE = re.compile(
    r"<(?P<destination>(?:"
    r"file:[^<>\s]+|[A-Za-z]:[\\/][^<>\s]+|"
    r"(?:\.{1,2}[\\/]|[\\/]{1,2}|[A-Za-z0-9_.-]+[\\/])[^<>\s]+|"
    r"[^<>\s/@]+\.[A-Za-z0-9]{1,16}(?:#[^<>\s]+)?"
    r"))>", re.IGNORECASE)
_HTML_CLOSING_TAG_RE = re.compile(
    r"</[A-Za-z][A-Za-z0-9:-]*[ \t]*>")
_FENCED_CODE_OPEN_RE = re.compile(
    r"^[ \t]{0,3}(?P<fence>`{3,}|~{3,})(?P<info>.*)$")
_HTML_RAW_ELEMENT_OPEN_RE = re.compile(
    r"^[ \t]{0,3}<(?P<tag>script|pre|style|textarea)(?:[ \t]|>|$)",
    re.IGNORECASE)
_HTML_BLOCK_TAG_OPEN_RE = re.compile(
    r"^[ \t]{0,3}</?(?:address|article|aside|base|basefont|blockquote|body|"
    r"caption|center|col|colgroup|dd|details|dialog|dir|div|dl|dt|fieldset|"
    r"figcaption|figure|footer|form|frame|frameset|h[1-6]|head|header|hr|"
    r"html|iframe|legend|li|link|main|menu|menuitem|nav|noframes|ol|"
    r"optgroup|option|p|param|search|section|summary|table|tbody|td|tfoot|"
    r"th|thead|title|tr|track|ul)(?:[ \t]|/?>|$)", re.IGNORECASE)
_GENERATED_FIGURE_PATH_RE = re.compile(
    r"\.\./assets/figure-p[0-9]{4,}-[A-Za-z0-9_-]+\.png")
_NOTES_TAG_AND_LABEL_RE = re.compile(
    r"(?m)^<!-- NOTES AND QUESTIONS -->\n\n"
    r"\*\*Notes and Questions\*\*(?P<range> \([^\n]*\))?\n")
_KNOWN_COMMENT_REPLACEMENTS = {
    "<!-- -->": "",
    "<!-- CASE OPINION -->": "_Case opinion._",
    "<!-- STATUTE -->": "_Statutory excerpt._",
    "<!-- FOOTNOTE -->": "_Footnote._",
    "<!-- CHAPTER INTRODUCTION -->": "_Chapter introduction._",
    "<!-- NOTES AND QUESTIONS -->": "##### Notes and Questions",
    "<!-- TABLE -->": "",
}


class AIProjectExportError(ValueError):
    """Raised when a target package cannot be derived without ambiguity."""


@dataclass(frozen=True, slots=True)
class TargetProfile:
    name: str
    directory_name: str
    extension: str
    default_max_files: int | None
    max_file_bytes: int
    max_file_words: int | None


@dataclass(frozen=True, slots=True)
class SourceDocument:
    name: str
    text: str


@dataclass(frozen=True, slots=True)
class ProjectDocument:
    name: str
    text: str
    sources: tuple[str, ...]
    source_page_marker_counts: tuple[int, ...]


TARGET_PROFILES = {
    "notebooklm": TargetProfile(
        "notebooklm", "NotebookLM", ".md", 50,
        200 * 1024 * 1024, 500_000),
    "chatgpt": TargetProfile(
        "chatgpt", "ChatGPT", ".md", 25,
        512 * 1024 * 1024, 500_000),
    "claude": TargetProfile(
        "claude", "Claude", ".txt", None,
        30 * 1024 * 1024, None),
}


def target_profile(target: str) -> TargetProfile:
    normalized = str(target or "").strip().lower()
    try:
        return TARGET_PROFILES[normalized]
    except KeyError as exc:
        raise AIProjectExportError(
            "target must be one of: claude, chatgpt, notebooklm") from exc


def profile_parameters(
        target: str, *, max_files: int | None,
        validation_policy: str = "auto") -> dict:
    profile = target_profile(target)
    normalized_validation = str(validation_policy or "").strip().lower()
    if normalized_validation not in {"auto", "internal", "strict"}:
        raise AIProjectExportError(
            "validation_policy must be one of: auto, internal, strict")
    effective_max = profile.default_max_files if max_files is None else max_files
    if (effective_max is not None
            and (isinstance(effective_max, bool)
                 or not isinstance(effective_max, int)
                 or effective_max <= 0)):
        raise AIProjectExportError("max_files must be a positive integer")
    return {
        "target": profile.name,
        "profile_version": AI_PROJECT_PROFILE_VERSION,
        "markdown_validation": normalized_validation,
        "extension": profile.extension,
        "max_files": effective_max,
        "max_file_bytes": profile.max_file_bytes,
        "max_file_words": profile.max_file_words,
        "grouping_version": AI_PROJECT_GROUPING_VERSION,
        "normalization_version": AI_PROJECT_NORMALIZATION_VERSION,
        "endnote_relabel_version": AI_PROJECT_ENDNOTE_RELABEL_VERSION,
        "comment_policy_version": AI_PROJECT_COMMENT_POLICY_VERSION,
        "link_policy_version": AI_PROJECT_LINK_POLICY_VERSION,
    }


def _source_sort_key(name: str) -> tuple[int, int, str]:
    if name == "front_matter.md":
        return 0, 0, name
    match = _CHAPTER_NAME_RE.fullmatch(name)
    if match is not None:
        return 1, int(match.group("number")), name
    if name == "back_matter.md":
        return 2, 0, name
    raise AIProjectExportError(f"unsafe canonical chapter filename: {name!r}")


def order_source_documents(
        documents: Iterable[SourceDocument]) -> list[SourceDocument]:
    materialized = list(documents)
    if not materialized:
        raise AIProjectExportError("at least one canonical chapter is required")
    names = [document.name for document in materialized]
    if len(names) != len(set(names)):
        raise AIProjectExportError("canonical chapter filenames are duplicated")
    ordered = sorted(materialized, key=lambda document: _source_sort_key(
        document.name))
    chapter_numbers = [
        int(match.group("number"))
        for document in ordered
        if (match := _CHAPTER_NAME_RE.fullmatch(document.name)) is not None
    ]
    if len(chapter_numbers) != len(set(chapter_numbers)):
        raise AIProjectExportError("canonical chapter ordinals are duplicated")
    return ordered


def _validate_numeric_footnotes(text: str) -> int:
    tokens = list(_ANY_FOOTNOTE_TOKEN_RE.finditer(text))
    starts = [match.start() for match in re.finditer(r"\[\^", text)]
    if starts != [match.start() for match in tokens]:
        raise AIProjectExportError("serialized endnote syntax is malformed")
    if any(re.fullmatch(r"[1-9][0-9]*", match.group(1)) is None
           for match in tokens):
        raise AIProjectExportError(
            "serialized endnote labels must be positive integers")
    references = _FOOTNOTE_REFERENCE_RE.findall(text)
    definitions = _FOOTNOTE_DEFINITION_RE.findall(text)
    if (any(count != 1 for count in Counter(references).values())
            or any(count != 1 for count in Counter(definitions).values())):
        raise AIProjectExportError("duplicate serialized endnote label")
    if set(references) != set(definitions):
        raise AIProjectExportError(
            "serialized endnote references and definitions do not match")
    expected = [str(index) for index in range(1, len(references) + 1)]
    if references != expected or definitions != expected:
        raise AIProjectExportError(
            "serialized endnotes are not in first-reference order")
    return len(references)


def _materialize_comments(text: str) -> str:
    text = _NOTES_TAG_AND_LABEL_RE.sub(
        lambda match: (
            "##### Notes and Questions" + (match.group("range") or "") + "\n"),
        text,
    )

    def replace(match: re.Match[str]) -> str:
        comment = match.group(0).strip()
        replacement = _KNOWN_COMMENT_REPLACEMENTS.get(comment)
        if replacement is not None:
            return replacement
        case = re.fullmatch(r"<!-- CASE:\s*(.+?)\s*-->", comment)
        if case is not None and case.group(1).strip():
            return f"_Case opinion: {case.group(1).strip()}._"
        raise AIProjectExportError(
            f"unknown hidden Markdown comment: {comment[:80]}")

    return _HTML_COMMENT_RE.sub(replace, text)


def _markdown_destination(value: str) -> str:
    """Normalize one parsed Markdown/HTML destination for policy checks."""
    destination = html.unescape(str(value or "").strip())
    if destination.startswith("<") and destination.endswith(">"):
        destination = destination[1:-1].strip()
    destination = re.sub(r"\\([\\()<> ])", r"\1", destination)
    try:
        return unquote(destination)
    except UnicodeError:
        return destination


def _is_package_external_local_destination(value: str) -> bool:
    """Return whether a link would depend on a file outside the package."""
    destination = _markdown_destination(value)
    if not destination or destination.startswith("#"):
        return False
    if destination.startswith("//"):
        return False
    parsed = urlsplit(destination)
    scheme = parsed.scheme.casefold()
    if scheme in {"http", "https", "mailto", "data"}:
        return False
    # Any other explicit scheme (notably file: and Windows drive spellings)
    # is local or unsupported and therefore cannot survive an upload package.
    if scheme:
        return True
    return True


def _inline_markdown_destinations(text: str) -> list[str]:
    """Scan inline link destinations, including balanced parentheses."""
    destinations: list[str] = []
    cursor = 0
    while True:
        opener = text.find("](", cursor)
        if opener < 0:
            break
        index = opener + 2
        while index < len(text) and text[index] in " \t":
            index += 1
        if index >= len(text):
            break
        if text[index] == "<":
            end = index + 1
            while end < len(text):
                if text[end] == "\\":
                    end += 2
                    continue
                if text[end] == ">":
                    destinations.append(text[index:end + 1])
                    break
                if text[end] == "\n":
                    break
                end += 1
            cursor = max(index + 1, end + 1)
            continue

        depth = 1
        end = index
        while end < len(text):
            character = text[end]
            if character == "\\":
                end += 2
                continue
            if character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
                if depth == 0:
                    break
            elif character in " \t\n" and depth == 1:
                break
            end += 1
        if end > index:
            destinations.append(text[index:end])
        cursor = max(index + 1, end + 1)
    return destinations


def _local_link_destinations(text: str) -> list[str]:
    """Find package-external Markdown and raw-HTML destinations."""
    destinations: list[str] = []
    destinations.extend(
        destination for destination in _inline_markdown_destinations(text)
        if _is_package_external_local_destination(destination)
    )
    destinations.extend(
        match.group("destination")
        for match in _REFERENCE_DEFINITION_RE.finditer(text)
        if _is_package_external_local_destination(match.group("destination"))
    )
    for match in _RAW_HTML_DESTINATION_RE.finditer(text):
        destination = match.group("quoted") or match.group("bare") or ""
        candidates = [destination]
        if match.group("attribute").casefold() == "srcset":
            candidates = [
                candidate.strip().split()[0]
                for candidate in destination.split(",")
                if candidate.strip()
            ]
        destinations.extend(
            candidate for candidate in candidates
            if _is_package_external_local_destination(candidate)
        )
    destinations.extend(
        match.group("destination")
        for match in _ANGLE_LOCAL_PATH_RE.finditer(text)
        if _HTML_CLOSING_TAG_RE.fullmatch(match.group(0)) is None
        if _is_package_external_local_destination(match.group("destination"))
    )
    return destinations


def _visible_page_marker_count(text: str) -> int:
    """Count marker lines outside Markdown code and raw-HTML blocks."""
    count = 0
    fence_character = ""
    fence_length = 0
    raw_element_end = ""
    html_until_blank = False
    for line in text.splitlines():
        if fence_character:
            if re.fullmatch(
                    rf"[ \t]{{0,3}}{re.escape(fence_character)}"
                    rf"{{{fence_length},}}[ \t]*", line):
                fence_character = ""
                fence_length = 0
            continue

        if raw_element_end:
            if raw_element_end in line.casefold():
                raw_element_end = ""
            continue
        if html_until_blank:
            if not line.strip():
                html_until_blank = False
            continue

        fence = _FENCED_CODE_OPEN_RE.match(line)
        if fence is not None:
            marker = fence.group("fence")
            info = fence.group("info")
            if marker.startswith("~") or "`" not in info:
                fence_character = marker[0]
                fence_length = len(marker)
                continue

        stripped = line.lstrip(" \t")
        raw_element = _HTML_RAW_ELEMENT_OPEN_RE.match(line)
        if raw_element is not None:
            raw_element_end = f"</{raw_element.group('tag').casefold()}>"
            if raw_element_end in line.casefold():
                raw_element_end = ""
            continue
        if stripped.startswith("<!--"):
            if "-->" not in stripped[4:]:
                raw_element_end = "-->"
            continue
        if stripped.startswith("<?"):
            if "?>" not in stripped[2:]:
                raw_element_end = "?>"
            continue
        if stripped.startswith("<![CDATA["):
            if "]]>" not in stripped[9:]:
                raw_element_end = "]]>"
            continue
        if re.match(r"<![A-Z]", stripped):
            if ">" not in stripped[2:]:
                raw_element_end = ">"
            continue
        if _HTML_BLOCK_TAG_OPEN_RE.match(line):
            html_until_blank = True
            continue
        if line.startswith("    ") or line.startswith("\t"):
            continue
        if _PAGE_MARKER_RE.fullmatch(line) is not None:
            count += 1
    return count


def _canonical_destination_name(value: str) -> str:
    """Return a same-directory canonical filename, excluding its fragment."""
    destination = _markdown_destination(value)
    if not destination or urlsplit(destination).scheme:
        return ""
    path = destination.split("#", 1)[0].replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    if "/" in path or not path:
        return ""
    return path


def _materialize_known_inline_links(
        text: str, *, canonical_names: set[str]) -> str:
    """Turn generated cross-chapter links and figure embeds into text."""
    def replace(match: re.Match[str]) -> str:
        destination = _markdown_destination(match.group("destination"))
        label = match.group("label")
        if (match.group("image")
                and _GENERATED_FIGURE_PATH_RE.fullmatch(destination)):
            return f"_{label}._"
        if (not match.group("image")
                and _canonical_destination_name(destination) in canonical_names):
            return label
        return match.group(0)

    return _INLINE_MARKDOWN_LINK_RE.sub(replace, text)


def normalize_project_markdown(
        markdown: str, *, canonical_names: Iterable[str]) -> str:
    """Materialize generated semantics and remove package-external links."""
    if not isinstance(markdown, str) or not markdown.strip():
        raise AIProjectExportError("canonical chapter Markdown is empty")
    normalized = markdown.replace("\r\n", "\n").replace("\r", "\n")
    if normalized.startswith("\ufeff"):
        normalized = normalized[1:]
    normalized = _materialize_comments(normalized)

    names = set(canonical_names)
    normalized = _materialize_known_inline_links(
        normalized, canonical_names=names)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized).strip() + "\n"

    if _HTML_COMMENT_RE.search(normalized):
        raise AIProjectExportError("hidden Markdown comment survived normalization")
    local_links = _local_link_destinations(normalized)
    if local_links:
        raise AIProjectExportError(
            f"package-external local link survived normalization: "
            f"{local_links[0]}")
    _validate_numeric_footnotes(normalized)
    return normalized


def _partition_contiguous(weights: Sequence[int], groups: int) -> list[tuple[int, int]]:
    """Minimize the largest contiguous group with deterministic tie breaking."""
    count = len(weights)
    if not 1 <= groups <= count:
        raise AIProjectExportError("invalid contiguous partition size")
    prefix = [0]
    for weight in weights:
        prefix.append(prefix[-1] + weight)
    states: dict[tuple[int, int], tuple[int, tuple[int, ...]]] = {
        (0, 0): (0, ()),
    }
    for group_count in range(1, groups + 1):
        for end in range(group_count, count + 1):
            best: tuple[int, tuple[int, ...]] | None = None
            for start in range(group_count - 1, end):
                previous = states.get((group_count - 1, start))
                if previous is None:
                    continue
                cost = max(previous[0], prefix[end] - prefix[start])
                candidate = (cost, previous[1] + (start,))
                if best is None or candidate < best:
                    best = candidate
            if best is not None:
                states[(group_count, end)] = best
    result = states.get((groups, count))
    if result is None:
        raise AIProjectExportError("could not partition canonical chapters")
    starts = result[1]
    return [
        (start, starts[index + 1] if index + 1 < len(starts) else count)
        for index, start in enumerate(starts)
    ]


def _group_label(documents: Sequence[SourceDocument]) -> str:
    labels: list[str] = []
    for document in documents:
        if document.name == "front_matter.md":
            labels.append("front")
        elif document.name == "back_matter.md":
            labels.append("back")
        else:
            match = _CHAPTER_NAME_RE.fullmatch(document.name)
            if match is None:
                raise AIProjectExportError("invalid grouped source name")
            labels.append(f"ch{int(match.group('number')):02d}")
    return labels[0] if len(labels) == 1 else f"{labels[0]}-{labels[-1]}"


def _project_filename(
        documents: Sequence[SourceDocument], *, group_index: int,
        extension: str) -> str:
    if len(documents) == 1:
        return Path(documents[0].name).with_suffix(extension).name
    return f"part{group_index:02d}_{_group_label(documents)}{extension}"


def _relabel_footnotes(text: str, *, offset: int) -> tuple[str, int]:
    count = _validate_numeric_footnotes(text)
    if not count or not offset:
        return text, count
    return _FOOTNOTE_TOKEN_RE.sub(
        lambda match: f"[^{int(match.group(1)) + offset}]", text), count


def build_project_documents(
        documents: Iterable[SourceDocument], *, target: str,
        max_files: int | None = None,
        validation_policy: str = "auto",
) -> tuple[list[ProjectDocument], dict]:
    profile = target_profile(target)
    parameters = profile_parameters(
        profile.name, max_files=max_files,
        validation_policy=validation_policy)
    ordered = order_source_documents(documents)
    canonical_names = [document.name for document in ordered]
    normalized = [
        SourceDocument(
            document.name,
            normalize_project_markdown(
                document.text, canonical_names=canonical_names),
        )
        for document in ordered
    ]
    page_marker_counts = {
        document.name: _visible_page_marker_count(document.text)
        for document in normalized
    }
    missing_page_markers = [
        document.name for document in normalized
        if page_marker_counts[document.name] <= 0
    ]
    if missing_page_markers:
        raise AIProjectExportError(
            "canonical chapter Markdown lacks visible PDF page markers: "
            f"{missing_page_markers[0]}")
    effective_max = parameters["max_files"]
    group_count = len(normalized) if effective_max is None else min(
        len(normalized), effective_max)
    partitions = _partition_contiguous(
        [len(document.text.encode("utf-8")) for document in normalized],
        group_count,
    )

    outputs: list[ProjectDocument] = []
    observed_names: set[str] = set()
    for group_index, (start, end) in enumerate(partitions, 1):
        group = normalized[start:end]
        offset = 0
        parts: list[str] = []
        for document in group:
            relabeled, count = _relabel_footnotes(document.text, offset=offset)
            parts.append(relabeled.rstrip("\n"))
            offset += count
        text = "\n\n---\n\n".join(parts).strip() + "\n"
        _validate_numeric_footnotes(text)
        name = _project_filename(
            group, group_index=group_index, extension=profile.extension)
        if name in observed_names:
            raise AIProjectExportError("derived output filenames collide")
        observed_names.add(name)
        encoded_size = len(text.encode("utf-8"))
        word_count = len(text.split())
        if encoded_size > profile.max_file_bytes:
            raise AIProjectExportError(
                f"derived file exceeds {profile.name} byte limit: {name}")
        if (profile.max_file_words is not None
                and word_count > profile.max_file_words):
            raise AIProjectExportError(
                f"derived file exceeds {profile.name} word limit: {name}")
        outputs.append(ProjectDocument(
            name=name, text=text,
            sources=tuple(document.name for document in group),
            source_page_marker_counts=tuple(
                page_marker_counts[document.name] for document in group),
        ))
    return outputs, parameters


def content_validation(documents: Sequence[ProjectDocument]) -> dict:
    combined = "\n".join(document.text for document in documents)
    references = _FOOTNOTE_REFERENCE_RE.findall(combined)
    definitions = _FOOTNOTE_DEFINITION_RE.findall(combined)
    source_marker_counts = [
        count for document in documents
        for count in document.source_page_marker_counts
    ]
    return {
        "document_count": len(documents),
        "source_document_count": sum(len(document.sources)
                                     for document in documents),
        "hidden_comment_count": len(_HTML_COMMENT_RE.findall(combined)),
        "local_link_count": len(_local_link_destinations(combined)),
        "page_marker_count": sum(source_marker_counts),
        "source_documents_with_page_markers": sum(
            count > 0 for count in source_marker_counts),
        "output_documents_with_page_markers": sum(
            _visible_page_marker_count(document.text) > 0
            for document in documents),
        "endnote_reference_count": len(references),
        "endnote_definition_count": len(definitions),
    }


def canonical_sha256(value: object) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def build_export_receipt(
        *, target: str, publication_receipt_sha256: str,
        publication_evidence_root_sha256: str,
        canonical_split_manifest_sha256: str,
        canonical_chunks_sha256: str, source_record_count: int,
        parameters: dict, documents: Sequence[ProjectDocument],
        output_records: Sequence[dict]) -> dict:
    profile = target_profile(target)
    validation = content_validation(documents)
    payload = {
        "schema_version": AI_PROJECT_EXPORT_SCHEMA_VERSION,
        "kind": "ai_project_export",
        "status": "pass",
        "target": profile.name,
        "profile_version": AI_PROJECT_PROFILE_VERSION,
        "publication_receipt_sha256": publication_receipt_sha256,
        "publication_evidence_root_sha256": (
            publication_evidence_root_sha256),
        "canonical_split_manifest_sha256": (
            canonical_split_manifest_sha256),
        "canonical_chunks_sha256": canonical_chunks_sha256,
        "source_record_count": source_record_count,
        "parameters": parameters,
        "parameters_sha256": canonical_sha256(parameters),
        "groups": [
            {"output": document.name, "sources": list(document.sources)}
            for document in documents
        ],
        "source_page_markers": [
            {"source": source, "count": count}
            for document in documents
            for source, count in zip(
                document.sources, document.source_page_marker_counts)
        ],
        "outputs": list(output_records),
        "content_validation": validation,
    }
    return validate_export_receipt(payload)


_RECEIPT_FIELDS = frozenset({
    "schema_version", "kind", "status", "target", "profile_version",
    "publication_receipt_sha256", "publication_evidence_root_sha256",
    "canonical_split_manifest_sha256", "canonical_chunks_sha256",
    "source_record_count", "parameters", "parameters_sha256", "groups",
    "source_page_markers", "outputs", "content_validation",
})


def validate_export_receipt(payload: object) -> dict:
    if not isinstance(payload, dict) or set(payload) != _RECEIPT_FIELDS:
        raise AIProjectExportError("AI project receipt has an invalid field set")
    profile = target_profile(str(payload.get("target") or ""))
    if (payload.get("schema_version") != AI_PROJECT_EXPORT_SCHEMA_VERSION
            or payload.get("kind") != "ai_project_export"
            or payload.get("status") != "pass"
            or payload.get("profile_version") != AI_PROJECT_PROFILE_VERSION):
        raise AIProjectExportError("AI project receipt header is invalid")
    for field in (
            "publication_receipt_sha256",
            "publication_evidence_root_sha256",
            "canonical_split_manifest_sha256", "canonical_chunks_sha256",
            "parameters_sha256"):
        if not isinstance(payload.get(field), str) or _SHA256_RE.fullmatch(
                payload[field]) is None:
            raise AIProjectExportError(f"AI project receipt {field} is invalid")
    count = payload.get("source_record_count")
    if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
        raise AIProjectExportError("AI project source record count is invalid")
    parameters = payload.get("parameters")
    if not isinstance(parameters, dict):
        raise AIProjectExportError("AI project parameters are invalid")
    expected_parameters = profile_parameters(
        profile.name, max_files=parameters.get("max_files"),
        validation_policy=parameters.get("markdown_validation", ""))
    if (parameters != expected_parameters
            or payload["parameters_sha256"] != canonical_sha256(parameters)):
        raise AIProjectExportError("AI project parameters are invalid")

    groups = payload.get("groups")
    outputs = payload.get("outputs")
    if (not isinstance(groups, list) or not groups
            or not isinstance(outputs, list) or len(outputs) != len(groups)):
        raise AIProjectExportError("AI project output inventory is invalid")
    output_names: list[str] = []
    source_names: list[str] = []
    for group in groups:
        if (not isinstance(group, dict) or set(group) != {"output", "sources"}
                or not isinstance(group.get("output"), str)
                or Path(group["output"]).name != group["output"]
                or not group["output"].endswith(profile.extension)
                or not isinstance(group.get("sources"), list)
                or not group["sources"]):
            raise AIProjectExportError("AI project group inventory is invalid")
        output_names.append(group["output"])
        for source in group["sources"]:
            if (not isinstance(source, str) or Path(source).name != source
                    or _source_sort_key(source) is None):
                raise AIProjectExportError("AI project source name is invalid")
            source_names.append(source)
    if (len(output_names) != len(set(output_names))
            or len(source_names) != len(set(source_names))):
        raise AIProjectExportError("AI project groups overlap")
    if source_names != sorted(source_names, key=_source_sort_key):
        raise AIProjectExportError(
            "AI project groups are not in canonical source order")
    for group_index, group in enumerate(groups, 1):
        expected_name = _project_filename(
            [SourceDocument(source, "") for source in group["sources"]],
            group_index=group_index,
            extension=profile.extension,
        )
        if group["output"] != expected_name:
            raise AIProjectExportError(
                "AI project group output name is not canonical")

    source_page_markers = payload.get("source_page_markers")
    if (not isinstance(source_page_markers, list)
            or len(source_page_markers) != len(source_names)):
        raise AIProjectExportError("AI project page-marker proof is invalid")
    marker_sources: list[str] = []
    marker_total = 0
    for marker in source_page_markers:
        if (not isinstance(marker, dict)
                or set(marker) != {"source", "count"}
                or not isinstance(marker.get("source"), str)
                or isinstance(marker.get("count"), bool)
                or not isinstance(marker.get("count"), int)
                or marker["count"] <= 0):
            raise AIProjectExportError(
                "AI project page-marker proof is invalid")
        marker_sources.append(marker["source"])
        marker_total += marker["count"]
    if marker_sources != source_names:
        raise AIProjectExportError("AI project page-marker proof is invalid")

    manifested_names: list[str] = []
    for record in outputs:
        if (not isinstance(record, dict)
                or set(record) != {"name", "role", "size", "sha256"}
                or not isinstance(record.get("name"), str)
                or Path(record["name"]).name != record["name"]
                or record.get("role") != record["name"]
                or not record["name"].endswith(profile.extension)
                or isinstance(record.get("size"), bool)
                or not isinstance(record.get("size"), int)
                or record["size"] <= 0
                or not isinstance(record.get("sha256"), str)
                or _SHA256_RE.fullmatch(record["sha256"]) is None):
            raise AIProjectExportError("AI project output record is invalid")
        manifested_names.append(record["name"])
    if (len(manifested_names) != len(set(manifested_names))
            or manifested_names != output_names):
        raise AIProjectExportError("AI project outputs disagree with groups")

    validation = payload.get("content_validation")
    validation_fields = {
        "document_count", "source_document_count", "hidden_comment_count",
        "local_link_count", "page_marker_count",
        "source_documents_with_page_markers",
        "output_documents_with_page_markers", "endnote_reference_count",
        "endnote_definition_count",
    }
    if (not isinstance(validation, dict) or set(validation) != validation_fields
            or any(isinstance(value, bool) or not isinstance(value, int)
                   or value < 0 for value in validation.values())
            or validation["document_count"] != len(outputs)
            or validation["source_document_count"] != len(source_names)
            or validation["hidden_comment_count"] != 0
            or validation["local_link_count"] != 0
            or validation["page_marker_count"] != marker_total
            or validation["source_documents_with_page_markers"]
            != len(source_names)
            or validation["output_documents_with_page_markers"]
            != len(outputs)
            or validation["endnote_reference_count"]
            != validation["endnote_definition_count"]):
        raise AIProjectExportError("AI project content validation is invalid")
    if len(serialize_export_receipt_unchecked(payload)) > MAX_AI_PROJECT_RECEIPT_BYTES:
        raise AIProjectExportError("AI project receipt exceeds maximum size")
    return payload


def serialize_export_receipt_unchecked(payload: object) -> bytes:
    return (json.dumps(
        payload, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def serialize_export_receipt(payload: object) -> bytes:
    validated = validate_export_receipt(payload)
    return serialize_export_receipt_unchecked(validated)


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise AIProjectExportError(f"duplicate AI project receipt field: {key}")
        result[key] = value
    return result


def parse_export_receipt_bytes(raw: bytes) -> dict:
    if not isinstance(raw, bytes) or not raw:
        raise AIProjectExportError("AI project receipt input must be nonempty bytes")
    if len(raw) > MAX_AI_PROJECT_RECEIPT_BYTES:
        raise AIProjectExportError("AI project receipt exceeds maximum size")
    try:
        payload = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(
                AIProjectExportError(
                    f"non-finite AI project receipt constant: {value}")))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise AIProjectExportError("cannot parse AI project receipt") from exc
    return validate_export_receipt(payload)
