"""Sanity checks on calculated values.

``check_range`` and ``check_change`` are deterministic and always run. ``LLMValidator`` is an
optional second opinion from a model; it can only add concerns, never approve a value the
deterministic checks flagged.
"""

from __future__ import annotations

from bnpl_metrics.extraction.parsing import extract_json
from bnpl_metrics.llm import LLM
from bnpl_metrics.models import MetricResult
from bnpl_metrics.specs import MetricSpec


class ValidatorUnavailable(Exception):
    """An optional validator could not run (network error, unusable reply)."""


def check_range(spec: MetricSpec, value: float) -> str | None:
    rules = spec.validation
    if rules.min is not None and value < rules.min:
        return f"value {value:g} is below the expected minimum {rules.min:g}"
    if rules.max is not None and value > rules.max:
        return f"value {value:g} is above the expected maximum {rules.max:g}"
    return None


def check_change(spec: MetricSpec, value: float, previous: float | None) -> str | None:
    limit = spec.validation.max_change_ratio
    if limit is None or previous is None or previous == 0:
        return None
    change = abs(value - previous) / abs(previous)
    if change > limit:
        return (
            f"value {value:g} differs from the previous value {previous:g} "
            f"by {change:.0%}, more than the allowed {limit:.0%}"
        )
    return None


_SYSTEM = (
    "You review a calculated financial metric for plausibility. You are given the inputs and "
    "the formula. Look for unit mix-ups, wrong periods, implausible magnitudes and "
    "inconsistent inputs. Reply with one JSON object and nothing else: "
    '{"recommended_action": "accept" | "review" | "reject", "concerns": ["..."]}'
)


class LLMValidator:
    def __init__(self, llm: LLM) -> None:
        self._llm = llm

    def __call__(
        self, spec: MetricSpec, result: MetricResult, previous: MetricResult | None
    ) -> list[str]:
        inputs = "\n".join(
            f"- {name}: {value.value:g} {value.unit or ''} (source: {value.source})"
            for name, value in result.inputs.items()
            if value.value is not None
        )
        prior = (
            f"{previous.value:g} {previous.unit} (computed {previous.computed_at})"
            if previous and previous.usable
            else "none"
        )
        prompt = (
            f"Metric: {spec.display_name} ({spec.unit})\n"
            f"Description: {spec.description or 'n/a'}\n"
            f"Formula: {spec.formula}\nInputs:\n{inputs}\n"
            f"Calculated value: {result.value:g} {spec.unit}\n"
            f"Previous value: {prior}"
        )
        try:
            data = extract_json(self._llm.complete(_SYSTEM, prompt))
        except Exception as exc:  # provider SDKs raise their own error types
            raise ValidatorUnavailable(f"AI validation failed: {exc}") from exc
        action = str(data.get("recommended_action", "")).lower()
        if action == "accept":
            return []
        if action not in ("review", "reject"):
            raise ValidatorUnavailable(f"AI validation gave no usable verdict: {action!r}")
        concerns = [str(c) for c in data.get("concerns") or []]
        return [f"AI validation ({action}): {c}" for c in concerns] or [
            f"AI validation recommends {action} without giving a reason"
        ]
