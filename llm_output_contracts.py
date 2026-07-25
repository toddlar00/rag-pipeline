"""Dependency-light contracts for treating generated model text as hostile.

Provider transports validate their outer HTTP envelopes.  This module owns the
operation-specific contract for text inside those envelopes.  Rejections retain
only a stable diagnostic code; model output is never attached to an exception.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Collection


OUTPUT_CONTRACT_POLICY_VERSION = 1
CLASSIFICATION_CONTRACT_ID = "chunk-classification-v2"
CLASSIFICATION_FALLBACK_ID = "preserve-deterministic-content-type"
CLASSIFICATION_MAX_BYTES = 128

INVALID_TYPE = "llm-output-invalid-type"
INVALID_ENCODING = "llm-output-invalid-encoding"
BYTE_LIMIT_EXCEEDED = "llm-output-byte-limit"
EMPTY_OUTPUT = "llm-output-empty"
CONTROL_CHARACTER = "llm-output-control-character"
LABEL_MISMATCH = "llm-output-label-mismatch"
INTERNAL_CONTRACT_ERROR = "llm-output-contract-internal-error"

REJECTION_CODES = frozenset({
    INVALID_TYPE,
    INVALID_ENCODING,
    BYTE_LIMIT_EXCEEDED,
    EMPTY_OUTPUT,
    CONTROL_CHARACTER,
    LABEL_MISMATCH,
    INTERNAL_CONTRACT_ERROR,
})

_IDENTIFIER = re.compile(r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*")
_ENUM_VALUE = re.compile(r"[a-z][a-z0-9_-]{0,63}")
_OUTER_WHITESPACE = " \t\r\n"


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
        if not isinstance(response, str):
            raise OutputContractRejected(INVALID_TYPE)
        if len(response) > self.max_bytes:
            raise OutputContractRejected(BYTE_LIMIT_EXCEEDED)
        try:
            encoded = response.encode("utf-8", errors="strict")
        except UnicodeEncodeError:
            raise OutputContractRejected(INVALID_ENCODING) from None
        if len(encoded) > self.max_bytes:
            raise OutputContractRejected(BYTE_LIMIT_EXCEEDED)

        normalized = response.strip(_OUTER_WHITESPACE)
        if not normalized:
            raise OutputContractRejected(EMPTY_OUTPUT)
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
