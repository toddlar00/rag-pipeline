"""Dependency-light contracts for treating generated model text as hostile.

Provider transports validate their outer HTTP envelopes.  This module owns the
operation-specific contract for text inside those envelopes.  Rejections retain
only a stable diagnostic code; model output is never attached to an exception.
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Callable, Collection


OUTPUT_CONTRACT_POLICY_VERSION = 1
CLASSIFICATION_CONTRACT_ID = "chunk-classification-v2"
CLASSIFICATION_FALLBACK_ID = "preserve-deterministic-content-type"
CLASSIFICATION_MAX_BYTES = 128
TOC_HIERARCHY_CONTRACT_ID = "toc-hierarchy-v1"
TOC_SCAFFOLD_FALLBACK_ID = "use-deterministic-toc-scaffold"
TOC_PARSE_FALLBACK_ID = "use-deterministic-toc-parser"
TOC_HIERARCHY_MAX_BYTES = 128 * 1024
TOC_HIERARCHY_MAX_DEPTH = 2
TOC_HIERARCHY_MIN_ITEMS = 1
TOC_HIERARCHY_MAX_ITEMS = 100
TOC_HIERARCHY_MAX_TITLE_CHARS = 512
TOC_HIERARCHY_MAX_TITLE_BYTES = 512
TOC_HIERARCHY_MAX_INTEGER_DIGITS = 7
TOC_HIERARCHY_MAX_PAGE = 1_000_000
TOC_HIERARCHY_MIN_LEVEL = 1
TOC_HIERARCHY_MAX_LEVEL = 5
TOC_HIERARCHY_UNICODE_DATA_VERSION = unicodedata.unidata_version
_TOC_HIERARCHY_UNICODE_VERSION_PARTS = tuple(
    int(part) for part in TOC_HIERARCHY_UNICODE_DATA_VERSION.split("."))
if len(_TOC_HIERARCHY_UNICODE_VERSION_PARTS) != 3:
    raise RuntimeError("unsupported Unicode data version format")
TOC_LAYOUT_CONTRACT_ID = "toc-layout-v1"
TOC_LAYOUT_FALLBACK_ID = "use-no-generated-toc-layout-hints"
TOC_LAYOUT_MAX_BYTES = 64 * 1024
TOC_LAYOUT_MAX_DEPTH = 2
TOC_LAYOUT_MAX_INTEGER_DIGITS = 7
TOC_LAYOUT_MAX_STRING_CHARS = 512
TOC_LAYOUT_MAX_STRING_BYTES = 512
TOC_LAYOUT_MAX_ARRAY_ITEMS = 16
TOC_LAYOUT_MAX_HIERARCHY_ORDER_ITEMS = 5
TOC_LAYOUT_HIERARCHY_LEVEL_COUNT = 5
TOC_LAYOUT_UNICODE_DATA_VERSION = unicodedata.unidata_version
_TOC_LAYOUT_UNICODE_VERSION_PARTS = tuple(
    int(part) for part in TOC_LAYOUT_UNICODE_DATA_VERSION.split("."))
if len(_TOC_LAYOUT_UNICODE_VERSION_PARTS) != 3:
    raise RuntimeError("unsupported Unicode data version format")

INVALID_TYPE = "llm-output-invalid-type"
INVALID_ENCODING = "llm-output-invalid-encoding"
BYTE_LIMIT_EXCEEDED = "llm-output-byte-limit"
EMPTY_OUTPUT = "llm-output-empty"
CONTROL_CHARACTER = "llm-output-control-character"
LABEL_MISMATCH = "llm-output-label-mismatch"
JSON_SYNTAX = "llm-output-json-syntax"
JSON_DUPLICATE_KEY = "llm-output-json-duplicate-key"
JSON_NON_FINITE_NUMBER = "llm-output-json-non-finite-number"
JSON_DEPTH_EXCEEDED = "llm-output-json-depth"
JSON_SHAPE_MISMATCH = "llm-output-json-shape"
JSON_ITEM_LIMIT = "llm-output-json-item-limit"
JSON_STRING_LIMIT = "llm-output-json-string-limit"
JSON_VALUE_OUT_OF_RANGE = "llm-output-json-value-range"
INTERNAL_CONTRACT_ERROR = "llm-output-contract-internal-error"

REJECTION_CODES = frozenset({
    INVALID_TYPE,
    INVALID_ENCODING,
    BYTE_LIMIT_EXCEEDED,
    EMPTY_OUTPUT,
    CONTROL_CHARACTER,
    LABEL_MISMATCH,
    JSON_SYNTAX,
    JSON_DUPLICATE_KEY,
    JSON_NON_FINITE_NUMBER,
    JSON_DEPTH_EXCEEDED,
    JSON_SHAPE_MISMATCH,
    JSON_ITEM_LIMIT,
    JSON_STRING_LIMIT,
    JSON_VALUE_OUT_OF_RANGE,
    INTERNAL_CONTRACT_ERROR,
})

_IDENTIFIER = re.compile(r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*")
_ENUM_VALUE = re.compile(r"[a-z][a-z0-9_-]{0,63}")
_OUTER_WHITESPACE = " \t\r\n"
_JSON_PROVENANCE_RESERVED_FIELDS = frozenset({
    "policy_version",
    "contract_id",
    "fallback_id",
    "max_bytes",
    "max_depth",
    "max_integer_digits",
})


class OutputContractRejected(ValueError):
    """A generated value violated a reviewed, content-free contract."""

    __slots__ = ("diagnostic_code",)

    def __init__(self, diagnostic_code: str):
        if diagnostic_code not in REJECTION_CODES:
            raise ValueError("unknown LLM output rejection code")
        self.diagnostic_code = diagnostic_code
        super().__init__(
            f"LLM output violated its contract ({diagnostic_code})")


def _validate_identifier(value: object, *, label: str) -> str:
    if (not isinstance(value, str) or len(value) > 128
            or _IDENTIFIER.fullmatch(value) is None):
        raise ValueError(f"{label} must be a canonical safe identifier")
    return value


def _bounded_response_text(response: object, *, max_bytes: int) -> str:
    """Return non-empty UTF-8 text within a raw response byte ceiling."""
    if not isinstance(response, str):
        raise OutputContractRejected(INVALID_TYPE)
    if len(response) > max_bytes:
        raise OutputContractRejected(BYTE_LIMIT_EXCEEDED)
    encoded = None
    try:
        encoded = response.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        pass
    if encoded is None:
        raise OutputContractRejected(INVALID_ENCODING)
    if len(encoded) > max_bytes:
        raise OutputContractRejected(BYTE_LIMIT_EXCEEDED)
    normalized = response.strip(_OUTER_WHITESPACE)
    if not normalized:
        raise OutputContractRejected(EMPTY_OUTPUT)
    return normalized


def _require_json_depth(value: str, *, max_depth: int) -> None:
    """Reject excessive structural depth before the JSON decoder recurses."""
    depth = 0
    in_string = False
    escaped = False
    for character in value:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            elif ord(character) < 0x20:
                raise OutputContractRejected(CONTROL_CHARACTER)
            continue
        if character == '"':
            in_string = True
        elif character in "[{":
            depth += 1
            if depth > max_depth:
                raise OutputContractRejected(JSON_DEPTH_EXCEEDED)
        elif character in "]}":
            depth -= 1
        elif (unicodedata.category(character).startswith("C")
              and character not in "\t\r\n"):
            raise OutputContractRejected(CONTROL_CHARACTER)


def _strict_json_value(response: object, *, max_bytes: int,
                       max_depth: int, max_integer_digits: int) -> Any:
    """Decode exactly one bounded JSON value with strict object semantics."""
    normalized = _bounded_response_text(response, max_bytes=max_bytes)
    _require_json_depth(normalized, max_depth=max_depth)

    def reject_constant(_value: str) -> None:
        raise OutputContractRejected(JSON_NON_FINITE_NUMBER)

    def bounded_integer(value: str) -> int:
        digits = value[1:] if value.startswith("-") else value
        if len(digits) > max_integer_digits:
            raise OutputContractRejected(JSON_VALUE_OUT_OF_RANGE)
        return int(value)

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise OutputContractRejected(JSON_DUPLICATE_KEY)
            result[key] = value
        return result

    decode_failed = False
    try:
        parsed = json.loads(
            normalized,
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
            parse_int=bounded_integer,
        )
    except OutputContractRejected:
        raise
    except (json.JSONDecodeError, RecursionError, ValueError):
        decode_failed = True
        parsed = None
    if decode_failed:
        raise OutputContractRejected(JSON_SYNTAX)
    return parsed


def _bounded_json_string(value: object, *, max_chars: int,
                         allow_empty: bool,
                         max_bytes: int | None = None) -> str:
    if not isinstance(value, str):
        raise OutputContractRejected(JSON_SHAPE_MISMATCH)
    if any(unicodedata.category(character) == "Cs" for character in value):
        raise OutputContractRejected(INVALID_ENCODING)
    if any(
            unicodedata.category(character).startswith("C")
            or unicodedata.category(character) in {"Zl", "Zp"}
            or (character.isspace() and character != " ")
            for character in value):
        raise OutputContractRejected(CONTROL_CHARACTER)
    normalized = value.strip(" ")
    if not normalized and not allow_empty:
        raise OutputContractRejected(JSON_SHAPE_MISMATCH)
    if len(normalized) > max_chars:
        raise OutputContractRejected(JSON_STRING_LIMIT)
    if (max_bytes is not None
            and len(normalized.encode("utf-8")) > max_bytes):
        raise OutputContractRejected(JSON_STRING_LIMIT)
    return normalized


def _require_finite_json_numbers(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise OutputContractRejected(JSON_NON_FINITE_NUMBER)
    if isinstance(value, dict):
        for key, child in value.items():
            _bounded_json_string(key, max_chars=128, allow_empty=False)
            _require_finite_json_numbers(child)
    elif isinstance(value, list):
        for child in value:
            _require_finite_json_numbers(child)


@dataclass(frozen=True)
class ExactEnumContract:
    """Accept one bounded enum value after only outer trim and case folding.

    The trusted enum declaration is validated at construction.  Model text is
    never Unicode-normalized: visually confusable or format-bearing text must
    not acquire authority through compatibility normalization.
    """

    contract_id: str
    allowed_values: tuple[str, ...]
    max_bytes: int

    def __post_init__(self) -> None:
        _validate_identifier(self.contract_id, label="output contract ID")
        if (isinstance(self.max_bytes, bool)
                or not isinstance(self.max_bytes, int)
                or self.max_bytes < 1):
            raise ValueError("output max_bytes must be a positive integer")
        if (not isinstance(self.allowed_values, tuple)
                or not self.allowed_values):
            raise ValueError("allowed_values must be a non-empty tuple")
        normalized: set[str] = set()
        for value in self.allowed_values:
            if (not isinstance(value, str)
                    or _ENUM_VALUE.fullmatch(value) is None):
                raise ValueError(
                    "allowed output labels must be canonical ASCII enums")
            if len(value.encode("ascii")) > self.max_bytes:
                raise ValueError(
                    "output max_bytes must fit every allowed label")
            folded = value.casefold()
            if folded in normalized:
                raise ValueError("allowed output labels must be unique")
            normalized.add(folded)

    def __call__(self, response: str) -> str:
        """Return the canonical enum or raise a response-free rejection."""
        normalized = _bounded_response_text(
            response, max_bytes=self.max_bytes)
        if any(unicodedata.category(character).startswith("C")
               for character in normalized):
            raise OutputContractRejected(CONTROL_CHARACTER)

        if not normalized.isascii():
            raise OutputContractRejected(LABEL_MISMATCH)
        expected = {
            value.lower(): value
            for value in self.allowed_values
        }
        canonical = expected.get(normalized.lower())
        if canonical is None:
            raise OutputContractRejected(LABEL_MISMATCH)
        return canonical

    def provenance(self, *, fallback_id: str) -> dict[str, object]:
        """Return the public contract fields that bind artifact reuse."""
        return {
            "policy_version": OUTPUT_CONTRACT_POLICY_VERSION,
            "contract_id": self.contract_id,
            "fallback_id": _validate_identifier(
                fallback_id, label="output fallback ID"),
            "max_bytes": self.max_bytes,
            "allowed_values": list(self.allowed_values),
        }


@dataclass(frozen=True)
class ExactJSONContract:
    """Canonicalize one strictly decoded JSON value through a fixed schema."""

    contract_id: str
    max_bytes: int
    max_depth: int
    schema_validator: Callable[[Any], Any]
    max_integer_digits: int = 64
    provenance_fields: tuple[tuple[str, int], ...] = ()

    def __post_init__(self) -> None:
        _validate_identifier(self.contract_id, label="output contract ID")
        for label, value in (
            ("max_bytes", self.max_bytes),
            ("max_depth", self.max_depth),
            ("max_integer_digits", self.max_integer_digits),
        ):
            if (isinstance(value, bool) or not isinstance(value, int)
                    or value < 1):
                raise ValueError(f"output {label} must be a positive integer")
        if not callable(self.schema_validator):
            raise TypeError("JSON schema validator must be callable")
        if not isinstance(self.provenance_fields, tuple):
            raise TypeError("JSON provenance fields must be a tuple")
        names: set[str] = set()
        for field in self.provenance_fields:
            if not isinstance(field, tuple) or len(field) != 2:
                raise TypeError("each JSON provenance field must be a pair")
            name, value = field
            _validate_identifier(name, label="JSON provenance field")
            if name in _JSON_PROVENANCE_RESERVED_FIELDS:
                raise ValueError(
                    "JSON provenance fields cannot replace contract metadata")
            if name in names:
                raise ValueError("JSON provenance fields must be unique")
            if (isinstance(value, bool) or not isinstance(value, int)
                    or value < 0):
                raise ValueError(
                    "JSON provenance values must be non-negative integers")
            names.add(name)

    def parse(self, response: object) -> Any:
        """Return the canonical typed value without retaining rejected text."""
        parsed = _strict_json_value(
            response, max_bytes=self.max_bytes, max_depth=self.max_depth,
            max_integer_digits=self.max_integer_digits)
        _require_finite_json_numbers(parsed)
        diagnostic_code = None
        validation_failed = False
        try:
            canonical = self.schema_validator(parsed)
        except OutputContractRejected as exc:
            diagnostic_code = exc.diagnostic_code
        except Exception:
            validation_failed = True
        if diagnostic_code is not None:
            raise OutputContractRejected(diagnostic_code)
        if validation_failed:
            raise OutputContractRejected(INTERNAL_CONTRACT_ERROR)
        return canonical

    def __call__(self, response: str) -> str:
        canonical = self.parse(response)
        serialization_failed = False
        try:
            canonical_text = json.dumps(
                canonical,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        except Exception:
            serialization_failed = True
            canonical_text = ""
        if serialization_failed:
            raise OutputContractRejected(INTERNAL_CONTRACT_ERROR)
        _bounded_response_text(canonical_text, max_bytes=self.max_bytes)
        return canonical_text

    def provenance(self, *, fallback_id: str) -> dict[str, object]:
        result: dict[str, object] = {
            "policy_version": OUTPUT_CONTRACT_POLICY_VERSION,
            "contract_id": self.contract_id,
            "fallback_id": _validate_identifier(
                fallback_id, label="output fallback ID"),
            "max_bytes": self.max_bytes,
            "max_depth": self.max_depth,
            "max_integer_digits": self.max_integer_digits,
        }
        result.update(dict(self.provenance_fields))
        return result


def _validate_toc_hierarchy(value: Any) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise OutputContractRejected(JSON_SHAPE_MISMATCH)
    if not TOC_HIERARCHY_MIN_ITEMS <= len(value) <= TOC_HIERARCHY_MAX_ITEMS:
        raise OutputContractRejected(JSON_ITEM_LIMIT)

    canonical: list[dict[str, object]] = []
    for item in value:
        if (not isinstance(item, dict)
                or set(item) != {"level", "title", "page"}):
            raise OutputContractRejected(JSON_SHAPE_MISMATCH)
        level = item["level"]
        page = item["page"]
        if type(level) is not int or type(page) is not int:
            raise OutputContractRejected(JSON_SHAPE_MISMATCH)
        if not TOC_HIERARCHY_MIN_LEVEL <= level <= TOC_HIERARCHY_MAX_LEVEL:
            raise OutputContractRejected(JSON_VALUE_OUT_OF_RANGE)
        if not 0 <= page <= TOC_HIERARCHY_MAX_PAGE:
            raise OutputContractRejected(JSON_VALUE_OUT_OF_RANGE)
        title = _bounded_json_string(
            item["title"], max_chars=TOC_HIERARCHY_MAX_TITLE_CHARS,
            max_bytes=TOC_HIERARCHY_MAX_TITLE_BYTES, allow_empty=False)
        canonical.append({"level": level, "title": title, "page": page})
    return canonical


TOC_HIERARCHY_CONTRACT = ExactJSONContract(
    contract_id=TOC_HIERARCHY_CONTRACT_ID,
    max_bytes=TOC_HIERARCHY_MAX_BYTES,
    max_depth=TOC_HIERARCHY_MAX_DEPTH,
    schema_validator=_validate_toc_hierarchy,
    max_integer_digits=TOC_HIERARCHY_MAX_INTEGER_DIGITS,
    provenance_fields=(
        ("min_items", TOC_HIERARCHY_MIN_ITEMS),
        ("max_items", TOC_HIERARCHY_MAX_ITEMS),
        ("max_title_chars", TOC_HIERARCHY_MAX_TITLE_CHARS),
        ("max_title_bytes", TOC_HIERARCHY_MAX_TITLE_BYTES),
        ("max_page", TOC_HIERARCHY_MAX_PAGE),
        ("min_level", TOC_HIERARCHY_MIN_LEVEL),
        ("max_level", TOC_HIERARCHY_MAX_LEVEL),
        ("unicode_data_major", _TOC_HIERARCHY_UNICODE_VERSION_PARTS[0]),
        ("unicode_data_minor", _TOC_HIERARCHY_UNICODE_VERSION_PARTS[1]),
        ("unicode_data_patch", _TOC_HIERARCHY_UNICODE_VERSION_PARTS[2]),
    ),
)


_TOC_LAYOUT_ROOT_FIELDS = frozenset({
    "page_number_format",
    "division_pattern",
    "division_examples",
    "section_markers",
    "subsection_markers",
    "named_item_format",
    "hierarchy_order",
    "hierarchy_levels",
})
_TOC_LAYOUT_SCALAR_FIELDS = (
    "page_number_format",
    "division_pattern",
    "named_item_format",
)
_TOC_LAYOUT_ARRAY_FIELDS = (
    "division_examples",
    "section_markers",
    "subsection_markers",
)
_TOC_LAYOUT_LEVEL_FIELDS = tuple(
    str(level) for level in range(1, TOC_LAYOUT_HIERARCHY_LEVEL_COUNT + 1))


def _validate_toc_layout(value: Any) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != _TOC_LAYOUT_ROOT_FIELDS:
        raise OutputContractRejected(JSON_SHAPE_MISMATCH)

    canonical: dict[str, object] = {}
    for field in _TOC_LAYOUT_SCALAR_FIELDS:
        canonical[field] = _bounded_json_string(
            value[field], max_chars=TOC_LAYOUT_MAX_STRING_CHARS,
            max_bytes=TOC_LAYOUT_MAX_STRING_BYTES, allow_empty=True)

    for field in _TOC_LAYOUT_ARRAY_FIELDS:
        items = value[field]
        if not isinstance(items, list):
            raise OutputContractRejected(JSON_SHAPE_MISMATCH)
        if len(items) > TOC_LAYOUT_MAX_ARRAY_ITEMS:
            raise OutputContractRejected(JSON_ITEM_LIMIT)
        canonical[field] = [
            _bounded_json_string(
                item, max_chars=TOC_LAYOUT_MAX_STRING_CHARS,
                max_bytes=TOC_LAYOUT_MAX_STRING_BYTES, allow_empty=False)
            for item in items
        ]

    hierarchy_order = value["hierarchy_order"]
    if not isinstance(hierarchy_order, list):
        raise OutputContractRejected(JSON_SHAPE_MISMATCH)
    if len(hierarchy_order) > TOC_LAYOUT_MAX_HIERARCHY_ORDER_ITEMS:
        raise OutputContractRejected(JSON_ITEM_LIMIT)
    canonical["hierarchy_order"] = [
        _bounded_json_string(
            item, max_chars=TOC_LAYOUT_MAX_STRING_CHARS,
            max_bytes=TOC_LAYOUT_MAX_STRING_BYTES, allow_empty=False)
        for item in hierarchy_order
    ]

    hierarchy_levels = value["hierarchy_levels"]
    if (not isinstance(hierarchy_levels, dict)
            or set(hierarchy_levels) != set(_TOC_LAYOUT_LEVEL_FIELDS)):
        raise OutputContractRejected(JSON_SHAPE_MISMATCH)
    canonical["hierarchy_levels"] = {
        level: _bounded_json_string(
            hierarchy_levels[level], max_chars=TOC_LAYOUT_MAX_STRING_CHARS,
            max_bytes=TOC_LAYOUT_MAX_STRING_BYTES, allow_empty=True)
        for level in _TOC_LAYOUT_LEVEL_FIELDS
    }
    return canonical


TOC_LAYOUT_CONTRACT = ExactJSONContract(
    contract_id=TOC_LAYOUT_CONTRACT_ID,
    max_bytes=TOC_LAYOUT_MAX_BYTES,
    max_depth=TOC_LAYOUT_MAX_DEPTH,
    schema_validator=_validate_toc_layout,
    max_integer_digits=TOC_LAYOUT_MAX_INTEGER_DIGITS,
    provenance_fields=(
        ("root_field_count", len(_TOC_LAYOUT_ROOT_FIELDS)),
        ("max_string_chars", TOC_LAYOUT_MAX_STRING_CHARS),
        ("max_string_bytes", TOC_LAYOUT_MAX_STRING_BYTES),
        ("max_array_items", TOC_LAYOUT_MAX_ARRAY_ITEMS),
        ("max_hierarchy_order_items",
         TOC_LAYOUT_MAX_HIERARCHY_ORDER_ITEMS),
        ("hierarchy_level_count", TOC_LAYOUT_HIERARCHY_LEVEL_COUNT),
        ("unicode_data_major", _TOC_LAYOUT_UNICODE_VERSION_PARTS[0]),
        ("unicode_data_minor", _TOC_LAYOUT_UNICODE_VERSION_PARTS[1]),
        ("unicode_data_patch", _TOC_LAYOUT_UNICODE_VERSION_PARTS[2]),
    ),
)


def exact_classification_contract(
        allowed_values: Collection[str]) -> ExactEnumContract:
    """Build the versioned legal-content classification contract."""
    if isinstance(allowed_values, (str, bytes)):
        raise TypeError("classification labels must be a collection of enums")
    return ExactEnumContract(
        contract_id=CLASSIFICATION_CONTRACT_ID,
        allowed_values=tuple(allowed_values),
        max_bytes=CLASSIFICATION_MAX_BYTES,
    )
