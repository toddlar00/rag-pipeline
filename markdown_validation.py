"""Deterministic and editor-compatible validation for generated Markdown.

The mandatory checks are standard-library only. Optional external checks use
an exact Pandoc release and a repository-locked copy of Zettlr's remark linter;
neither validator is installed or downloaded at runtime.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any, Iterable


log = logging.getLogger(__name__)

MARKDOWN_VALIDATION_POLICY_VERSION = 5
MARKDOWN_VALIDATION_RECEIPT_SCHEMA_VERSION = 2
MARKDOWN_VALIDATION_POLICIES = ("auto", "internal", "strict")
PANDOC_PROFILE = "pandoc-3.10.1"
PANDOC_VERSION = "3.10.1"
ZETTLR_PROFILE = "zettlr-4.7.0"
_ZETTLR_BUNDLE_SHA256 = {
    "package.json": (
        "0001ef58f5fcfc13b03f91b237a0897b5cb2ab40e2b1150ba2a584445b5b92a2"),
    "package-lock.json": (
        "a84557a79610607bf99423adc4bfe85a9bc9056e4ce2037f422c75f4a9e97c34"),
    "validate.mjs": (
        "365a31bcf04163ea5e617ad1905d2b94688f70d18e72f91fe3ed2a785647f270"),
}

_PROJECT_ROOT = Path(__file__).resolve().parent
_ZETTLR_VALIDATOR_ROOT_ENV = "RAG_ZETTLR_MARKDOWN_VALIDATOR_ROOT"
_PANDOC_ENV = "RAG_MARKDOWN_PANDOC"
_EXTERNAL_TIMEOUT_SECONDS = 30
_MAX_STDOUT_BYTES = 96 * 1024 * 1024
_MAX_STDERR_BYTES = 1024 * 1024
_MAX_ZETTLR_CONFIG_BYTES = 2 * 1024 * 1024

_TABLE_MARKER = "<!-- TABLE -->"
_FOOTNOTE_REFERENCE_RE = re.compile(r"\[\^([1-9][0-9]*)\](?!:)")
_FOOTNOTE_DEFINITION_RE = re.compile(
    r"(?m)^\[\^([1-9][0-9]*)\]:")
_CONSECUTIVE_BLANK_LINES_RE = re.compile(r"\n(?:[ \t]*\n){2,}")
_UNESCAPED_CURRENCY_RE = re.compile(
    r"(?<!\\)(?:\\\\)*\$"
    r"(?=[^\S\r\n]*[+-]?[^\S\r\n]*"
    r"(?:\d|[.,]\d|\([^\S\r\n]*\d))")
_BARE_ALNUM_BRACKET_RE = re.compile(
    r"(?<!!)(?<!\\)(?<!\[)(?:\\\\)*"
    r"\[([A-Za-z0-9])\](?!\]|\[|\(|\{|:)")
_SINGLE_TOKEN_DEFINITION_RE = re.compile(
    r"(?mi)^[ \t]{0,3}\[([A-Za-z0-9])\]:")
_LEGACY_ENDNOTE_PATTERNS = tuple(re.compile(pattern, re.IGNORECASE) for pattern in (
    r'<a\s+name=["\']rag-fn',
    r"\brag-fnref-[0-9a-f]{16}",
    r"\]\(#rag-fn-[0-9a-f]{16}\)",
    r"\[\^rag-fn-[0-9a-f]{16}\]",
    r"\bBack to (?:reference|related page context)\b",
))
_TABLE_SEPARATOR_CELL_RE = re.compile(r"^:?-{3,}:?$")
_PANDOC_VERSION_RE = re.compile(r"^pandoc\s+([^\s]+)", re.IGNORECASE)
_BULLET_WRAPPED_ORDERED_LABEL_RE = re.compile(
    r"^[ \t]*[-+*][ \t]+"
    r"(?:[0-9]+|[A-Za-z]|[ivxlcdm]{2,}|[IVXLCDM]{2,})"
    r"\.[ \t]+\S")
_NON_FOOTNOTE_REFERENCE_DEFINITION_RE = re.compile(
    r"^[ \t]{0,3}\[(?!\^)[^\]\r\n]+\]:(?=[ \t]|$)")


class MarkdownValidationError(ValueError):
    """Raised when a Markdown candidate is unsafe to publish."""


class _ExternalToolError(RuntimeError):
    """Raised when an optional external validator cannot complete."""


@dataclass(frozen=True)
class _ToolProbe:
    status: str
    command: tuple[str, ...] = ()
    version: str | None = None


def normalize_validation_policy(policy: str) -> str:
    """Return one supported validation policy or raise a stable error."""
    normalized = str(policy or "").strip().lower()
    if normalized not in MARKDOWN_VALIDATION_POLICIES:
        raise ValueError(
            "validation_policy must be one of: "
            + ", ".join(MARKDOWN_VALIDATION_POLICIES))
    return normalized


def _creation_flags() -> int:
    if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW"):
        return int(subprocess.CREATE_NO_WINDOW)
    return 0


def _run_command(command: tuple[str, ...], input_bytes: bytes | None = None):
    """Run one fixed argv without a shell and bound its retained output."""
    try:
        result = subprocess.run(
            list(command),
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            shell=False,
            timeout=_EXTERNAL_TIMEOUT_SECONDS,
            creationflags=_creation_flags(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise _ExternalToolError(type(exc).__name__) from exc
    if (len(result.stdout) > _MAX_STDOUT_BYTES
            or len(result.stderr) > _MAX_STDERR_BYTES):
        raise _ExternalToolError("validator output exceeded its bound")
    return result


def _pandoc_candidates() -> Iterable[Path]:
    configured = os.environ.get(_PANDOC_ENV)
    if configured:
        yield Path(configured)
        return
    discovered = shutil.which("pandoc")
    if discovered:
        yield Path(discovered)
    if os.name == "nt":
        program_files = os.environ.get("ProgramFiles")
        local_app_data = os.environ.get("LOCALAPPDATA")
        if program_files:
            yield Path(program_files) / "Zettlr" / "resources" / "pandoc.exe"
        if local_app_data:
            yield (Path(local_app_data) / "Programs" / "Zettlr"
                   / "resources" / "pandoc.exe")
    elif sys_platform() == "darwin":
        yield Path("/Applications/Zettlr.app/Contents/Resources/pandoc")
    else:
        yield Path("/opt/Zettlr/resources/pandoc")
        yield Path("/usr/lib/zettlr/resources/pandoc")


def sys_platform() -> str:
    """Small monkeypatchable platform seam without importing a GUI package."""
    import sys
    return sys.platform


def _probe_pandoc() -> _ToolProbe:
    seen: set[str] = set()
    unsupported_version: str | None = None
    for candidate in _pandoc_candidates():
        try:
            resolved = candidate.expanduser().resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        identity = os.path.normcase(str(resolved))
        if identity in seen or not resolved.is_file():
            continue
        seen.add(identity)
        try:
            result = _run_command((str(resolved), "--version"))
        except _ExternalToolError:
            continue
        first_line = result.stdout.decode("utf-8", errors="replace").splitlines()
        match = _PANDOC_VERSION_RE.match(first_line[0]) if first_line else None
        version = match.group(1) if match is not None else None
        if result.returncode == 0 and version == PANDOC_VERSION:
            return _ToolProbe(
                "available", (str(resolved),), version=PANDOC_VERSION)
        if version is not None:
            unsupported_version = version
    if unsupported_version is not None:
        return _ToolProbe("unsupported", version=unsupported_version)
    return _ToolProbe("unavailable")


def _zettlr_validator_root() -> Path:
    configured = os.environ.get(_ZETTLR_VALIDATOR_ROOT_ENV)
    if configured:
        return Path(configured)
    return _PROJECT_ROOT / "tools" / "zettlr-markdown-validator"


def _probe_zettlr_validator() -> _ToolProbe:
    node = shutil.which("node")
    if not node:
        return _ToolProbe("unavailable")
    root = _zettlr_validator_root()
    required = (
        root / "validate.mjs",
        root / "package.json",
        root / "package-lock.json",
        root / "node_modules" / "remark" / "package.json",
    )
    if not all(path.is_file() for path in required):
        return _ToolProbe("unavailable")
    try:
        for name, expected_sha256 in _ZETTLR_BUNDLE_SHA256.items():
            raw = (root / name).read_bytes().replace(b"\r\n", b"\n")
            digest = hashlib.sha256(raw).hexdigest()
            if digest != expected_sha256:
                return _ToolProbe("unsupported")
        manifest = json.loads((root / "package.json").read_text(encoding="utf-8"))
        dependencies = manifest.get("dependencies")
        if not isinstance(dependencies, dict) or not dependencies:
            return _ToolProbe("unsupported")
        for package_name, expected_version in dependencies.items():
            installed_manifest = (
                root / "node_modules" / package_name / "package.json")
            installed = json.loads(
                installed_manifest.read_text(encoding="utf-8"))
            if (not isinstance(expected_version, str)
                    or installed.get("version") != expected_version):
                return _ToolProbe("unsupported")
    except (OSError, UnicodeError, json.JSONDecodeError, AttributeError):
        return _ToolProbe("unsupported")
    try:
        script = required[0].resolve(strict=True)
        node_path = Path(node).resolve(strict=True)
    except (OSError, RuntimeError):
        return _ToolProbe("unavailable")
    return _ToolProbe(
        "available", (str(node_path), str(script)), version=ZETTLR_PROFILE)


def _split_pipe_row(line: str) -> list[str] | None:
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return None
    cells: list[str] = []
    current: list[str] = []
    escaped = False
    for character in stripped[1:-1]:
        if character == "|" and not escaped:
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(character)
        if character == "\\":
            escaped = not escaped
        else:
            escaped = False
    cells.append("".join(current).strip())
    return cells


def _validate_table_after_marker(lines: list[str], marker_index: int) -> None:
    first_pipe: int | None = None
    for index in range(marker_index + 1, len(lines)):
        stripped = lines[index].strip()
        if stripped == _TABLE_MARKER:
            break
        if (stripped.startswith("<!-- ") and stripped.endswith(" -->")
                or stripped.startswith("#")):
            break
        if stripped.startswith("|"):
            first_pipe = index
            break
    if first_pipe is None or first_pipe + 1 >= len(lines):
        raise MarkdownValidationError(
            "table marker is not followed by a Markdown table")
    if first_pipe == 0 or lines[first_pipe - 1].strip():
        raise MarkdownValidationError(
            "Markdown table caption is not separated from the table")
    _validate_table_lines(lines, first_pipe)


def _validate_table_lines(lines: list[str], first_pipe: int) -> None:
    """Validate one pipe table beginning at its header row."""
    table_lines: list[str] = []
    for line in lines[first_pipe:]:
        if not line.strip().startswith("|"):
            break
        table_lines.append(line)
    if len(table_lines) < 2:
        raise MarkdownValidationError("Markdown table lacks a separator row")
    parsed = [_split_pipe_row(line) for line in table_lines]
    if any(row is None for row in parsed):
        raise MarkdownValidationError("Markdown table contains an invalid row")
    rows = [row for row in parsed if row is not None]
    width = len(rows[0])
    if width == 0 or any(len(row) != width for row in rows):
        raise MarkdownValidationError("Markdown table rows are ragged")
    if not all(_TABLE_SEPARATOR_CELL_RE.fullmatch(cell)
               for cell in rows[1]):
        raise MarkdownValidationError(
            "Markdown table separator row is invalid")
    for line in table_lines:
        stripped = line.strip()
        if not stripped.startswith("| ") or not stripped.endswith(" |"):
            raise MarkdownValidationError(
                "Markdown table cells are not consistently padded")


def _validate_unmarked_tables(lines: list[str]) -> int:
    """Validate and count visible pipe tables after comment materialization."""
    table_count = 0
    index = 0
    while index + 1 < len(lines):
        header = _split_pipe_row(lines[index])
        separator = _split_pipe_row(lines[index + 1])
        if (header is None or separator is None or not separator
                or not all(_TABLE_SEPARATOR_CELL_RE.fullmatch(cell)
                           for cell in separator)):
            index += 1
            continue
        if index == 0 or lines[index - 1].strip():
            raise MarkdownValidationError(
                "Markdown table caption is not separated from the table")
        _validate_table_lines(lines, index)
        table_count += 1
        index += 2
        while index < len(lines) and lines[index].strip().startswith("|"):
            index += 1
    return table_count


def _validate_footnotes(markdown: str) -> int:
    reference_ids = _FOOTNOTE_REFERENCE_RE.findall(markdown)
    definition_ids = _FOOTNOTE_DEFINITION_RE.findall(markdown)
    reference_counts = Counter(reference_ids)
    definition_counts = Counter(definition_ids)
    if any(count != 1 for count in reference_counts.values()):
        raise MarkdownValidationError(
            "duplicate serialized footnote references")
    if any(count != 1 for count in definition_counts.values()):
        raise MarkdownValidationError(
            "duplicate serialized footnote definitions")
    if set(reference_ids) != set(definition_ids):
        raise MarkdownValidationError(
            "serialized footnote references and definitions do not match")
    expected = [str(index) for index in range(1, len(reference_ids) + 1)]
    if reference_ids != expected or definition_ids != expected:
        raise MarkdownValidationError(
            "serialized footnote labels are not in first-reference order")
    return len(reference_ids)


def _validate_fenced_code_blocks(markdown: str) -> None:
    """Reject a fence that would consume the remainder of the export."""
    fence_character: str | None = None
    fence_length = 0
    for line in markdown.splitlines():
        match = re.match(r"^[ ]{0,3}(?P<fence>`{3,}|~{3,})", line)
        if match is None:
            continue
        marker = match.group("fence")
        if fence_character is None:
            fence_character = marker[0]
            fence_length = len(marker)
            continue
        if (marker[0] == fence_character
                and len(marker) >= fence_length
                and not line[match.end():].strip()):
            fence_character = None
            fence_length = 0
    if fence_character is not None:
        raise MarkdownValidationError(
            "Markdown contains an unterminated fenced code block")


def _validate_bullet_wrapped_ordered_labels(markdown: str) -> None:
    """Reject literal ordered-list labels wrapped in generic bullets.

    Pandoc can reinterpret an unescaped numeric, alphabetic, or Roman label
    after a bullet as a nested one-item ordered list.  This ambiguity exists
    for a single line and at every nesting depth, so generated bullets must
    escape the label's period.  Fenced examples are outside this policy.
    """
    fence_character: str | None = None
    fence_length = 0
    for line in markdown.splitlines():
        fence = re.match(r"^[ \t]*(?P<fence>`{3,}|~{3,})", line)
        if fence is not None:
            marker = fence.group("fence")
            if fence_character is None:
                fence_character = marker[0]
                fence_length = len(marker)
            elif (marker[0] == fence_character
                  and len(marker) >= fence_length
                  and not line[fence.end():].strip()):
                fence_character = None
                fence_length = 0
            continue
        if (fence_character is None
                and _BULLET_WRAPPED_ORDERED_LABEL_RE.match(line)):
            raise MarkdownValidationError(
                "bullet-wrapped ordered-list label must escape its period")


def _validate_nonfootnote_reference_definitions(markdown: str) -> None:
    """Reject source prose that Pandoc would hide as a link definition."""
    fence_character: str | None = None
    fence_length = 0
    for line in markdown.splitlines():
        fence = re.match(r"^[ \t]*(?P<fence>`{3,}|~{3,})", line)
        if fence is not None:
            marker = fence.group("fence")
            if fence_character is None:
                fence_character = marker[0]
                fence_length = len(marker)
            elif (marker[0] == fence_character
                  and len(marker) >= fence_length
                  and not line[fence.end():].strip()):
                fence_character = None
                fence_length = 0
            continue
        if (fence_character is None
                and _NON_FOOTNOTE_REFERENCE_DEFINITION_RE.match(line)):
            raise MarkdownValidationError(
                "non-footnote reference definition could hide source prose")


def _validate_internal(
        markdown: str, *, expected_table_count: int,
        require_table_markers: bool = True) -> dict:
    if (isinstance(expected_table_count, bool)
            or not isinstance(expected_table_count, int)
            or expected_table_count < 0):
        raise ValueError("expected_table_count must be a nonnegative integer")
    if not isinstance(markdown, str) or not markdown:
        raise MarkdownValidationError("Markdown candidate is empty")
    if "\r" in markdown:
        raise MarkdownValidationError("Markdown contains noncanonical newlines")
    if not markdown.endswith("\n"):
        raise MarkdownValidationError("Markdown lacks its final newline")
    if _CONSECUTIVE_BLANK_LINES_RE.search(markdown):
        raise MarkdownValidationError(
            "Markdown contains more than one consecutive blank line")
    if any(pattern.search(markdown) for pattern in _LEGACY_ENDNOTE_PATTERNS):
        raise MarkdownValidationError(
            "legacy serialized endnote HTML is not permitted")
    _validate_fenced_code_blocks(markdown)
    _validate_bullet_wrapped_ordered_labels(markdown)
    _validate_nonfootnote_reference_definitions(markdown)
    if _UNESCAPED_CURRENCY_RE.search(markdown):
        raise MarkdownValidationError(
            "numeric currency could be parsed as inline math")
    definitions = {
        match.group(1).casefold()
        for match in _SINGLE_TOKEN_DEFINITION_RE.finditer(markdown)
    }
    bare_tokens = [
        match.group(1) for match in _BARE_ALNUM_BRACKET_RE.finditer(markdown)
        if match.group(1).casefold() not in definitions
    ]
    if bare_tokens:
        raise MarkdownValidationError(
            "bare bracket token could become an implicit reference link")
    if re.search(r"(?m)^<!-- CASE:\s*None\s*-->$", markdown, re.I):
        raise MarkdownValidationError("generated case marker contains None")
    if re.search(r"(?m)^#{1,6}\s+None\s*$", markdown, re.I):
        raise MarkdownValidationError("generated heading contains None")
    first_content = next(
        (line.strip() for line in markdown.splitlines() if line.strip()), "")
    if first_content == "---":
        raise MarkdownValidationError(
            "leading thematic rule is ambiguous YAML front matter")

    note_count = _validate_footnotes(markdown)
    lines = markdown.splitlines()
    marker_indices = [
        index for index, line in enumerate(lines)
        if line.strip() == _TABLE_MARKER
    ]
    if require_table_markers:
        observed_table_count = len(marker_indices)
        if observed_table_count != expected_table_count:
            raise MarkdownValidationError(
                "exported table marker count does not match source tables")
        for marker_index in marker_indices:
            _validate_table_after_marker(lines, marker_index)
    else:
        if marker_indices:
            raise MarkdownValidationError(
                "materialized Markdown retains a hidden table marker")
        observed_table_count = _validate_unmarked_tables(lines)
        if observed_table_count != expected_table_count:
            raise MarkdownValidationError(
                "materialized table count does not match source tables")
    return {
        "status": "pass",
        "table_count": observed_table_count,
        "note_count": note_count,
        "diagnostic_count": 0,
    }


def _decode_json_output(result, *, validator: str) -> dict:
    try:
        payload = json.loads(result.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _ExternalToolError(
            f"{validator} returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise _ExternalToolError(f"{validator} returned a non-object")
    return payload


def _walk_ast(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_ast(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_ast(child)


def _raw_table_marker(block: Any) -> bool:
    return (
        isinstance(block, dict)
        and block.get("t") == "RawBlock"
        and isinstance(block.get("c"), list)
        and len(block["c"]) == 2
        and block["c"][0] == "html"
        and str(block["c"][1]).strip() == _TABLE_MARKER
    )


def _fragment_link_nodes(ast: dict) -> Counter[str]:
    links: Counter[str] = Counter()
    for node in _walk_ast(ast):
        if node.get("t") != "Link":
            continue
        content = node.get("c")
        if (not isinstance(content, list) or len(content) != 3
                or not isinstance(content[2], list) or not content[2]):
            continue
        if str(content[2][0]).startswith("#"):
            links[json.dumps(node, ensure_ascii=False, sort_keys=True)] += 1
    return links


def _pandoc_parse(command: tuple[str, ...], markdown: str, reader: str):
    result = _run_command((
        *command,
        "--sandbox",
        f"--from={reader}",
        "--to=json",
    ), markdown.encode("utf-8"))
    if result.returncode != 0:
        raise _ExternalToolError("Pandoc parse failed")
    return _decode_json_output(result, validator="Pandoc"), result.stderr


def _validate_pandoc(
        markdown: str, *, expected_table_count: int, expected_note_count: int,
        probe: _ToolProbe, strict: bool,
        require_table_markers: bool = True) -> dict:
    ast, stderr = _pandoc_parse(probe.command, markdown, "markdown")
    blocks = ast.get("blocks")
    if not isinstance(blocks, list):
        raise _ExternalToolError("Pandoc AST lacks top-level blocks")
    top_level_tables = sum(
        isinstance(block, dict) and block.get("t") == "Table"
        for block in blocks)
    raw_markers = sum(_raw_table_marker(block) for block in blocks)
    note_count = sum(
        node.get("t") == "Note" for node in _walk_ast(ast))
    expected_raw_markers = expected_table_count if require_table_markers else 0
    if (top_level_tables != expected_table_count
            or raw_markers != expected_raw_markers):
        raise MarkdownValidationError(
            "Pandoc table count does not match exported table markers")
    if note_count != expected_note_count:
        raise MarkdownValidationError(
            "Pandoc note count does not match serialized endnotes")

    for index, block in enumerate(blocks) if require_table_markers else ():
        if not _raw_table_marker(block):
            continue
        paired = False
        for follower in blocks[index + 1:]:
            if (isinstance(follower, dict)
                    and follower.get("t") == "Table"):
                paired = True
                break
            if (_raw_table_marker(follower)
                    or isinstance(follower, dict)
                    and follower.get("t") == "Header"):
                break
            if (isinstance(follower, dict)
                    and follower.get("t") == "RawBlock"
                    and isinstance(follower.get("c"), list)
                    and len(follower["c"]) == 2
                    and str(follower["c"][1]).lstrip().startswith("<!--")):
                break
        if not paired:
            raise MarkdownValidationError(
                "Pandoc did not associate a table with its exporter marker")

    implicit_links = Counter()
    primary_fragment_links = _fragment_link_nodes(ast)
    if primary_fragment_links:
        control_ast, control_stderr = _pandoc_parse(
            probe.command, markdown, "markdown-implicit_header_references")
        implicit_links = primary_fragment_links - _fragment_link_nodes(control_ast)
        stderr += control_stderr
    if implicit_links:
        raise MarkdownValidationError(
            "Pandoc created an implicit internal reference link")

    warning_count = sum(bool(line.strip()) for line in stderr.splitlines())
    if strict and warning_count:
        raise MarkdownValidationError(
            "Pandoc emitted warnings under strict Markdown validation")
    return {
        "status": "warnings" if warning_count else "pass",
        "profile": PANDOC_PROFILE,
        "version": probe.version,
        "table_count": top_level_tables,
        "note_count": note_count,
        "warning_count": warning_count,
    }


def _zettlr_config_candidates() -> Iterable[Path]:
    app_data = os.environ.get("APPDATA")
    if app_data:
        yield Path(app_data) / "Zettlr" / "config.json"
    xdg_config = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config:
        yield Path(xdg_config) / "Zettlr" / "config.json"
    if sys_platform() == "darwin":
        yield Path.home() / "Library" / "Application Support" / "Zettlr" / "config.json"


def _zettlr_formatting_config() -> tuple[str, str, str]:
    italic, bold = "_", "**"
    for path in _zettlr_config_candidates():
        try:
            if not path.is_file() or path.stat().st_size > _MAX_ZETTLR_CONFIG_BYTES:
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        editor = payload.get("editor") if isinstance(payload, dict) else None
        if not isinstance(editor, dict):
            continue
        candidate_italic = editor.get("italicFormatting")
        candidate_bold = editor.get("boldFormatting")
        if candidate_italic in {"*", "_", "consistent"}:
            italic = candidate_italic
        if candidate_bold in {"**", "__", "consistent"}:
            bold = candidate_bold
        return italic, bold, "zettlr-config"
    return italic, bold, "export-default"


def _validate_zettlr(
        markdown: str, *, source_name: str, probe: _ToolProbe,
        strict: bool) -> dict:
    italic, bold, config_source = _zettlr_formatting_config()
    display_name = Path(source_name).name if source_name else "<memory>"
    result = _run_command((
        *probe.command,
        "--path", display_name,
        "--italic-formatting", italic,
        "--bold-formatting", bold,
    ), markdown.encode("utf-8"))
    payload = _decode_json_output(result, validator="Zettlr validator")
    if result.returncode not in {0, 1}:
        raise _ExternalToolError("Zettlr validator execution failed")
    diagnostics = payload.get("diagnostics")
    if not isinstance(diagnostics, list):
        raise _ExternalToolError("Zettlr validator omitted diagnostics")
    semantic_count = 0
    style_count = 0
    rules: Counter[str] = Counter()
    for diagnostic in diagnostics:
        if not isinstance(diagnostic, dict):
            raise _ExternalToolError("Zettlr diagnostic is malformed")
        rule_id = diagnostic.get("rule_id")
        if isinstance(rule_id, str):
            rules[rule_id] += 1
        if diagnostic.get("blocking") is True or diagnostic.get("category") in {
                "parser", "semantic"}:
            semantic_count += 1
        else:
            style_count += 1
    if semantic_count:
        raise MarkdownValidationError(
            "Zettlr-compatible validation found a semantic error")
    if strict and style_count:
        raise MarkdownValidationError(
            "Zettlr-compatible validation found style diagnostics")
    if result.returncode != 0:
        raise _ExternalToolError("Zettlr validator returned failure")
    return {
        "status": "warnings" if style_count else "pass",
        "profile": ZETTLR_PROFILE,
        "version": probe.version,
        "config_source": config_source,
        "italic_formatting": italic,
        "bold_formatting": bold,
        "diagnostic_count": len(diagnostics),
        "semantic_count": semantic_count,
        "style_count": style_count,
        "rules": dict(sorted(rules.items())),
    }


def _skipped_tool(status: str, *, profile: str) -> dict:
    return {
        "status": status,
        "profile": profile,
        "diagnostic_count": 0,
    }


class MarkdownValidationSession:
    """Reuse validator discovery across every candidate in one export."""

    def __init__(self, policy: str = "auto") -> None:
        self.policy = normalize_validation_policy(policy)
        self._warned: set[tuple[str, str]] = set()
        if self.policy == "internal":
            self.pandoc = _ToolProbe("skipped_by_policy")
            self.zettlr = _ToolProbe("skipped_by_policy")
        else:
            self.pandoc = _probe_pandoc()
            self.zettlr = _probe_zettlr_validator()

    def _warn_once(self, validator: str, status: str) -> None:
        key = (validator, status)
        if key in self._warned:
            return
        self._warned.add(key)
        log.warning(
            "%s Markdown validation %s; mandatory internal checks still ran",
            validator, status.replace("_", " "))

    def _unavailable(self, validator: str, probe: _ToolProbe, profile: str):
        status = (
            "skipped_unsupported" if probe.status == "unsupported"
            else "skipped_unavailable")
        if self.policy == "strict":
            raise MarkdownValidationError(
                f"strict Markdown validation requires pinned {validator}")
        self._warn_once(validator, status)
        receipt = _skipped_tool(status, profile=profile)
        if probe.version is not None:
            receipt["detected_version"] = probe.version
        return receipt

    def validate(
            self, markdown: str, *, expected_table_count: int,
            source_name: str = "<memory>",
            require_table_markers: bool = True) -> dict:
        internal = _validate_internal(
            markdown, expected_table_count=expected_table_count,
            require_table_markers=require_table_markers)
        if self.policy == "internal":
            pandoc = _skipped_tool(
                "skipped_by_policy", profile=PANDOC_PROFILE)
            zettlr = _skipped_tool(
                "skipped_by_policy", profile=ZETTLR_PROFILE)
        else:
            if self.pandoc.status != "available":
                pandoc = self._unavailable(
                    "Pandoc", self.pandoc, PANDOC_PROFILE)
            else:
                try:
                    pandoc = _validate_pandoc(
                        markdown,
                        expected_table_count=expected_table_count,
                        expected_note_count=internal["note_count"],
                        probe=self.pandoc,
                        strict=self.policy == "strict",
                        require_table_markers=require_table_markers,
                    )
                except _ExternalToolError:
                    if self.policy == "strict":
                        raise MarkdownValidationError(
                            "strict Pandoc validation could not complete")
                    self._warn_once("Pandoc", "skipped_error")
                    pandoc = _skipped_tool(
                        "skipped_error", profile=PANDOC_PROFILE)
            if self.zettlr.status != "available":
                zettlr = self._unavailable(
                    "Zettlr-compatible", self.zettlr, ZETTLR_PROFILE)
            else:
                try:
                    zettlr = _validate_zettlr(
                        markdown, source_name=source_name,
                        probe=self.zettlr,
                        strict=self.policy == "strict")
                except _ExternalToolError:
                    if self.policy == "strict":
                        raise MarkdownValidationError(
                            "strict Zettlr-compatible validation could not complete")
                    self._warn_once("Zettlr-compatible", "skipped_error")
                    zettlr = _skipped_tool(
                        "skipped_error", profile=ZETTLR_PROFILE)
        if zettlr.get("style_count", 0):
            log.warning(
                "Zettlr-compatible Markdown validation reported %d style "
                "diagnostic(s) for %s",
                zettlr["style_count"], Path(source_name).name)
        return {
            "schema_version": MARKDOWN_VALIDATION_RECEIPT_SCHEMA_VERSION,
            "policy_version": MARKDOWN_VALIDATION_POLICY_VERSION,
            "policy": self.policy,
            "candidate_count": 1,
            "publishable": True,
            "internal": internal,
            "pandoc": pandoc,
            "zettlr": zettlr,
        }


def validate_markdown_candidate(
        markdown: str, *, expected_table_count: int,
        source_name: str = "<memory>", policy: str = "auto",
        session: MarkdownValidationSession | None = None,
        require_table_markers: bool = True) -> dict:
    """Validate one in-memory candidate and return a content-free receipt."""
    active_session = session or MarkdownValidationSession(policy)
    if session is not None and normalize_validation_policy(policy) != session.policy:
        raise ValueError("validation session policy does not match policy")
    return active_session.validate(
        markdown, expected_table_count=expected_table_count,
        source_name=source_name,
        require_table_markers=require_table_markers)


def _aggregate_validator_receipts(receipts: list[dict], key: str) -> dict:
    sections = [receipt[key] for receipt in receipts]
    statuses = {section.get("status") for section in sections}
    if statuses <= {"pass", "warnings"}:
        aggregate_status = "warnings" if "warnings" in statuses else "pass"
    else:
        aggregate_status = (
            next(iter(statuses)) if len(statuses) == 1 else "mixed")
    output: dict[str, Any] = {
        "status": aggregate_status,
    }
    for identity_key in ("profile", "version", "config_source",
                         "italic_formatting", "bold_formatting"):
        values = {section.get(identity_key) for section in sections
                  if section.get(identity_key) is not None}
        if len(values) == 1:
            output[identity_key] = next(iter(values))
    count_keys = {
        name for section in sections for name, value in section.items()
        if name.endswith("_count") and isinstance(value, int)
    }
    for count_key in sorted(count_keys):
        output[count_key] = sum(
            int(section.get(count_key, 0)) for section in sections)
    rule_counts: Counter[str] = Counter()
    for section in sections:
        rules = section.get("rules")
        if isinstance(rules, dict):
            rule_counts.update({
                str(name): int(count) for name, count in rules.items()
                if isinstance(count, int)
            })
    if rule_counts:
        output["rules"] = dict(sorted(rule_counts.items()))
    return output


def combine_validation_receipts(receipts: Iterable[dict], *, policy: str) -> dict:
    """Combine per-file receipts without retaining filenames or content."""
    normalized_policy = normalize_validation_policy(policy)
    materialized = list(receipts)
    if not materialized:
        raise ValueError("at least one Markdown validation receipt is required")
    for receipt in materialized:
        if not validation_receipt_is_complete(
                receipt, policy=normalized_policy, candidate_count=1):
            raise ValueError("cannot combine an incomplete validation receipt")
    combined = {
        "schema_version": MARKDOWN_VALIDATION_RECEIPT_SCHEMA_VERSION,
        "policy_version": MARKDOWN_VALIDATION_POLICY_VERSION,
        "policy": normalized_policy,
        "candidate_count": len(materialized),
        "publishable": True,
        "internal": _aggregate_validator_receipts(materialized, "internal"),
        "pandoc": _aggregate_validator_receipts(materialized, "pandoc"),
        "zettlr": _aggregate_validator_receipts(materialized, "zettlr"),
    }
    if not validation_receipt_is_complete(
            combined, policy=normalized_policy,
            candidate_count=len(materialized)):
        raise ValueError("combined validation receipt has mixed attestations")
    return combined


def _nonnegative_receipt_integer(value: object) -> bool:
    return type(value) is int and value >= 0


def _valid_internal_receipt(section: dict) -> bool:
    return (
        set(section) == {
            "status", "table_count", "note_count", "diagnostic_count"}
        and section.get("status") == "pass"
        and all(_nonnegative_receipt_integer(section.get(name)) for name in (
            "table_count", "note_count", "diagnostic_count"))
        and section.get("diagnostic_count") == 0
    )


def _valid_skipped_external_receipt(
        section: dict, *, profile: str, allowed_statuses: set[str]) -> bool:
    keys = set(section)
    if keys not in (
            {"status", "profile", "diagnostic_count"},
            {"status", "profile", "diagnostic_count", "detected_version"}):
        return False
    if (
        section.get("status") not in allowed_statuses
        or section.get("profile") != profile
        or not _nonnegative_receipt_integer(section.get("diagnostic_count"))
        or section.get("diagnostic_count") != 0
    ):
        return False
    if "detected_version" in section:
        detected = section.get("detected_version")
        if not isinstance(detected, str) or not detected:
            return False
    return True


def _valid_pandoc_receipt(
        section: dict, *, policy: str, internal: dict) -> bool:
    status = section.get("status")
    if status in {"pass", "warnings"}:
        if set(section) != {
            "status", "profile", "version", "table_count", "note_count",
            "warning_count",
        }:
            return False
        if (
            section.get("profile") != PANDOC_PROFILE
            or section.get("version") != PANDOC_VERSION
            or not all(_nonnegative_receipt_integer(section.get(name))
                       for name in (
                           "table_count", "note_count", "warning_count"))
            or section.get("table_count") != internal.get("table_count")
            or section.get("note_count") != internal.get("note_count")
            or (status == "pass" and section.get("warning_count") != 0)
            or (status == "warnings" and section.get("warning_count") == 0)
        ):
            return False
        return policy == "auto" or (policy == "strict" and status == "pass")
    allowed_skips = (
        {"skipped_by_policy"} if policy == "internal" else
        {"skipped_unavailable", "skipped_unsupported", "skipped_error"}
        if policy == "auto" else set()
    )
    return _valid_skipped_external_receipt(
        section, profile=PANDOC_PROFILE, allowed_statuses=allowed_skips)


def _valid_zettlr_rules(value: object, *, diagnostic_count: int) -> bool:
    return (
        isinstance(value, dict)
        and all(isinstance(name, str) and name
                and _nonnegative_receipt_integer(count)
                for name, count in value.items())
        and sum(value.values()) <= diagnostic_count
    )


def _valid_zettlr_receipt(section: dict, *, policy: str) -> bool:
    status = section.get("status")
    if status in {"pass", "warnings"}:
        required = {
            "status", "profile", "version", "config_source",
            "italic_formatting", "bold_formatting", "diagnostic_count",
            "semantic_count", "style_count",
        }
        if set(section) not in (required, required | {"rules"}):
            return False
        counts = {
            name: section.get(name) for name in (
                "diagnostic_count", "semantic_count", "style_count")}
        if (
            section.get("profile") != ZETTLR_PROFILE
            or section.get("version") != ZETTLR_PROFILE
            or section.get("config_source") not in {
                "zettlr-config", "export-default"}
            or section.get("italic_formatting") not in {"*", "_", "consistent"}
            or section.get("bold_formatting") not in {"**", "__", "consistent"}
            or not all(_nonnegative_receipt_integer(value)
                       for value in counts.values())
            or counts["semantic_count"] != 0
            or counts["diagnostic_count"] != (
                counts["semantic_count"] + counts["style_count"])
            or (status == "pass" and counts["style_count"] != 0)
            or (status == "warnings" and counts["style_count"] == 0)
        ):
            return False
        if "rules" in section and not _valid_zettlr_rules(
                section["rules"], diagnostic_count=counts["diagnostic_count"]):
            return False
        return policy == "auto" or (policy == "strict" and status == "pass")
    allowed_skips = (
        {"skipped_by_policy"} if policy == "internal" else
        {"skipped_unavailable", "skipped_unsupported", "skipped_error"}
        if policy == "auto" else set()
    )
    return _valid_skipped_external_receipt(
        section, profile=ZETTLR_PROFILE, allowed_statuses=allowed_skips)


def validation_receipt_is_complete(
        receipt: object, *, policy: str, candidate_count: int) -> bool:
    """Validate the content-free receipt required for resume eligibility."""
    normalized_policy = normalize_validation_policy(policy)
    if (
        not isinstance(receipt, dict)
        or type(candidate_count) is not int
        or candidate_count <= 0
        or set(receipt) != {
            "schema_version", "policy_version", "policy", "candidate_count",
            "publishable", "internal", "pandoc", "zettlr"}
    ):
        return False
    if (
        type(receipt.get("schema_version")) is not int
        or receipt.get("schema_version")
        != MARKDOWN_VALIDATION_RECEIPT_SCHEMA_VERSION
        or type(receipt.get("policy_version")) is not int
        or receipt.get("policy_version") != MARKDOWN_VALIDATION_POLICY_VERSION
        or receipt.get("policy") != normalized_policy
        or type(receipt.get("candidate_count")) is not int
        or receipt.get("candidate_count") != candidate_count
        or receipt.get("publishable") is not True
    ):
        return False
    internal = receipt.get("internal")
    pandoc = receipt.get("pandoc")
    zettlr = receipt.get("zettlr")
    if not all(isinstance(section, dict)
               for section in (internal, pandoc, zettlr)):
        return False
    return (
        _valid_internal_receipt(internal)
        and _valid_pandoc_receipt(
            pandoc, policy=normalized_policy, internal=internal)
        and _valid_zettlr_receipt(zettlr, policy=normalized_policy)
    )
