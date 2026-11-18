"""Strict parsers for numbers, percentages and JSON in model replies.

Every parser raises ParseError on failure. None of them returns a default.
"""

from __future__ import annotations

import json
import re
from typing import Any


class ParseError(ValueError):
    """Raised when text does not contain the expected value."""


_AMOUNT = re.compile(r"(\()?\s*(-)?\s*\$?\s*(\d[\d,]*(?:\.\d+)?)\s*(\))?")
_SUFFIX = re.compile(r"\s*(trillion|billion|million|thousand|tn|bn|mm|mn|[tbmk])(?![a-z])", re.I)
_SUFFIX_WORDS = {
    "t": "trillion", "tn": "trillion", "trillion": "trillion",
    "b": "billion", "bn": "billion", "billion": "billion",
    "m": "million", "mm": "million", "mn": "million", "million": "million",
    "k": "thousand", "thousand": "thousand",
}  # fmt: skip
_PERCENT = re.compile(r"(-?\d+(?:\.\d+)?)\s*(?:%|percent\b|per cent\b)", re.I)
_DECIMAL = re.compile(r"(?<![\d.])(0?\.\d+|1(?:\.0+)?|0)(?![\d.])")


def parse_amount(text: str) -> tuple[float, str | None]:
    """Parse the first amount in ``text``.

    Returns ``(number, magnitude_word)``: ``"$5.6 billion"`` -> ``(5.6, "billion")``,
    ``"(1,234)"`` -> ``(-1234.0, None)``. The magnitude is reported, not applied, so callers
    can convert units explicitly.
    """
    match = _AMOUNT.search(text)
    if not match:
        raise ParseError(f"no number found in {text!r}")
    opening, minus, digits, closing = match.groups()
    number = float(digits.replace(",", ""))
    if minus or (opening and closing):
        number = -number
    suffix = _SUFFIX.match(text, match.end())
    return number, _SUFFIX_WORDS[suffix.group(1).lower()] if suffix else None


def parse_percentage(text: str, allow_decimal: bool = False) -> float:
    """Return a ratio (0-1) from text such as ``"61%"`` or ``"61 percent"``.

    A bare decimal like ``0.61`` is accepted only when ``allow_decimal`` is true.
    """
    match = _PERCENT.search(text)
    if match:
        return float(match.group(1)) / 100
    if allow_decimal:
        decimal = _DECIMAL.search(text)
        if decimal:
            return float(decimal.group(1))
    raise ParseError(f"no percentage found in {text!r}")


def extract_json(text: str) -> dict[str, Any]:
    """Parse the first JSON object in ``text``, tolerating markdown code fences."""
    cleaned = re.sub(r"```(?:json)?", "", text).strip()
    start = cleaned.find("{")
    if start == -1:
        raise ParseError("no JSON object found")
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(cleaned)):
        char = cleaned[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    parsed = json.loads(cleaned[start : index + 1])
                except json.JSONDecodeError as exc:
                    raise ParseError(f"invalid JSON: {exc.msg}") from exc
                if not isinstance(parsed, dict):
                    raise ParseError("JSON value is not an object")
                return parsed
    raise ParseError("unterminated JSON object")
