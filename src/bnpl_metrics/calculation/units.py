"""Explicit unit handling.

Values are converted using the unit an extractor reports. Magnitudes are never guessed:
an unknown or incompatible unit raises UnitError so the failure is visible.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


class UnitError(ValueError):
    """Raised for unknown units or conversions between incompatible kinds."""


_SCALES = {
    "thousand": 1e3, "thousands": 1e3, "k": 1e3,
    "million": 1e6, "millions": 1e6, "m": 1e6, "mm": 1e6, "mn": 1e6,
    "billion": 1e9, "billions": 1e9, "b": 1e9, "bn": 1e9,
    "trillion": 1e12, "t": 1e12, "tn": 1e12,
}  # fmt: skip
_CURRENCIES = {"usd", "aud", "eur", "gbp", "sek", "cad", "nzd", "chf", "jpy"}
_RATIO_WORDS = {"ratio", "fraction", "share"}
_PERCENT_WORDS = {"percent", "percentage", "pct", "%"}
_DOLLAR_WORDS = {"dollar", "dollars"}
_FILLER = {"us", "of", "in"}


@dataclass(frozen=True)
class Unit:
    kind: str  # "money:usd", "ratio", or "count"
    scale: float


def parse_unit(text: str) -> Unit:
    if not text or not text.strip():
        raise UnitError("empty unit")
    normalized = text.strip().lower().replace("$", " usd ").replace("%", " % ")
    tokens = re.findall(r"[a-z]+|%", normalized)
    kind: str | None = None
    scale = 1.0
    scale_seen = False
    for token in tokens:
        if token in _SCALES:
            if scale_seen:
                raise UnitError(f"multiple magnitude words in unit {text!r}")
            scale *= _SCALES[token]
            scale_seen = True
        elif token in _CURRENCIES:
            kind = f"money:{token}"
        elif token in _DOLLAR_WORDS:
            kind = "money:usd"
        elif token in _RATIO_WORDS:
            kind = "ratio"
        elif token in _PERCENT_WORDS:
            kind = "ratio"
            scale *= 0.01
        elif token in _FILLER:
            continue
    return Unit(kind=kind or "count", scale=scale)


def convert(value: float, from_unit: str, to_unit: str) -> float:
    """Convert ``value`` between units of the same kind, e.g. 'million USD' -> 'billion USD'."""
    source, target = parse_unit(from_unit), parse_unit(to_unit)
    if source.kind != target.kind:
        raise UnitError(f"cannot convert {from_unit!r} to {to_unit!r}: different kinds")
    return value * source.scale / target.scale
