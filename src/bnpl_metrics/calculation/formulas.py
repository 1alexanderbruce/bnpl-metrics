"""Pure financial primitives. No I/O, no hidden state."""

from __future__ import annotations

from collections.abc import Sequence


def clean(values: Sequence[float | None]) -> list[float]:
    """Drop missing entries (None or NaN) while keeping order."""
    return [v for v in values if v is not None and v == v]


def latest(values: Sequence[float | None]) -> float:
    present = clean(values)
    if not present:
        raise ValueError("no values available")
    return present[-1]


def sum_last(values: Sequence[float | None], n: int = 4) -> float:
    """Sum of the last ``n`` periods. Raises rather than summing a short window."""
    present = clean(values)
    if len(present) < n:
        raise ValueError(f"need {n} periods, found {len(present)}")
    return float(sum(present[-n:]))


def mean_last(values: Sequence[float | None], n: int = 4) -> float:
    return sum_last(values, n) / n


def annualize(value: float, periods_per_year: int = 4) -> float:
    return value * periods_per_year


def per_period(annual_value: float, periods_per_year: int = 4) -> float:
    return annual_value / periods_per_year


def apply_share(value: float, share: float) -> float:
    """Apply a ratio (0-1) to a value, e.g. a US share of a global total."""
    if not 0 <= share <= 1:
        raise ValueError(f"share must be between 0 and 1, got {share}")
    return value * share


def share_of(part: float, whole: float) -> float:
    """``part`` as a percentage of ``whole``."""
    if whole == 0:
        raise ZeroDivisionError("whole is zero")
    return part / whole * 100


def fx_convert(value: float, rate: float) -> float:
    """Convert a local-currency amount using ``rate`` (units of target currency per local unit)."""
    if rate <= 0:
        raise ValueError(f"exchange rate must be positive, got {rate}")
    return value * rate
