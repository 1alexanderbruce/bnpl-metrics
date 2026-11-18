"""Config-driven metric calculation.

A metric is computed from named inputs. Each input is resolved by trying its sources in order;
a source marked ``fallback`` is only reached when the earlier ones fail, and using one is
recorded on the result (status ``fallback``), never hidden. Failures produce a ``failed``
result with the reason, rather than a default value.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from bnpl_metrics.calculation.expr import FormulaError, evaluate
from bnpl_metrics.calculation.units import UnitError, convert
from bnpl_metrics.errors import InputError
from bnpl_metrics.extraction.parsing import ParseError
from bnpl_metrics.logging_utils import get_logger
from bnpl_metrics.models import ExtractedValue, MetricResult, Status, Step
from bnpl_metrics.specs import CompanySpec, InputSpec, MetricSpec, SourceSpec
from bnpl_metrics.validation import ValidatorUnavailable, check_change, check_range

log = get_logger(__name__)


Resolver = Callable[[SourceSpec, InputSpec], ExtractedValue]
Validator = Callable[[MetricSpec, MetricResult, "MetricResult | None"], list[str]]

_EXPECTED_ERRORS = (InputError, ParseError, UnitError, ValueError, KeyError, OSError)


@dataclass
class RunContext:
    """State for one calculation run. Nothing is shared between runs."""

    company: CompanySpec
    previous: Mapping[str, MetricResult] = field(default_factory=dict)
    results: dict[str, MetricResult] = field(default_factory=dict)


class Engine:
    def __init__(
        self,
        resolvers: Mapping[str, Resolver],
        validators: list[Validator] | None = None,
    ) -> None:
        self.resolvers = dict(resolvers)
        self.validators = list(validators or [])

    def calculate_company(
        self,
        company: CompanySpec,
        previous: Mapping[str, MetricResult] | None = None,
        only: set[str] | None = None,
    ) -> dict[str, MetricResult]:
        """Calculate metrics in dependency order. ``only`` restricts the run to those metrics
        plus whatever they depend on."""
        ctx = RunContext(company=company, previous=previous or {})
        wanted = self._closure(company, only) if only else None
        for metric in company.ordered_metrics():
            if wanted is None or metric.name in wanted:
                ctx.results[metric.name] = self.calculate_metric(metric, ctx)
        return ctx.results

    def calculate_metric(self, metric: MetricSpec, ctx: RunContext) -> MetricResult:
        result = MetricResult(
            company_id=metric.company_id,
            metric=metric.name,
            display_name=metric.display_name,
            unit=metric.unit,
            status=Status.FAILED,
        )
        values: dict[str, float] = {}
        used_fallback = False
        for name, spec in metric.inputs.items():
            try:
                extracted = self._resolve_input(spec, ctx, result.warnings)
            except InputError as exc:
                result.error = f"input '{name}': {exc}"
                log.warning("%s.%s failed: %s", metric.company_id, metric.name, result.error)
                return result
            result.inputs[name] = extracted
            used_fallback = used_fallback or extracted.used_fallback
            values[name] = extracted.value  # type: ignore[assignment]
            result.steps.append(
                Step(
                    description=f"{name} ({spec.unit})",
                    value=extracted.value,
                    detail=extracted.note,
                    source=extracted.source,
                )
            )
        try:
            result.value = evaluate(metric.formula, values)
        except FormulaError as exc:
            result.error = str(exc)
            return result
        result.steps.append(Step(description=f"{metric.formula} = result", value=result.value))

        result.status = Status.FALLBACK if used_fallback else Status.OK
        previous = ctx.previous.get(metric.name)
        concerns = self._validate(metric, result, previous)
        if concerns:
            result.status = Status.NEEDS_REVIEW
            result.warnings.extend(concerns)
        return result

    def _validate(
        self, metric: MetricSpec, result: MetricResult, previous: MetricResult | None
    ) -> list[str]:
        assert result.value is not None
        concerns = [
            c
            for c in (
                check_range(metric, result.value),
                check_change(
                    metric,
                    result.value,
                    previous.value if previous and previous.usable else None,
                ),
            )
            if c
        ]
        for validator in self.validators:
            try:
                concerns.extend(validator(metric, result, previous))
            except ValidatorUnavailable as exc:
                # An unreachable checker is worth a warning, but must not flag a good value.
                result.warnings.append(f"extra validation skipped: {exc}")
        return concerns

    def _resolve_input(
        self, spec: InputSpec, ctx: RunContext, warnings: list[str]
    ) -> ExtractedValue:
        failures: list[str] = []
        for source in spec.sources:
            try:
                extracted = self._normalize(self._from_source(source, ctx, spec), spec)
            except _EXPECTED_ERRORS as exc:
                failures.append(f"{source.type}: {exc}")
                warnings.append(f"input '{spec.name}': {source.type} source failed ({exc})")
                continue
            if source.fallback:
                extracted.used_fallback = True
                extracted.note = f"fallback: {source.reason}"
                warnings.append(f"input '{spec.name}' used fallback ({source.reason})")
            return extracted
        raise InputError("all sources failed: " + "; ".join(failures))

    def _from_source(self, source: SourceSpec, ctx: RunContext, spec: InputSpec) -> ExtractedValue:
        if source.type == "metric":
            dependency = ctx.results.get(source.params["ref"])
            if dependency is None or not dependency.usable:
                raise InputError(f"dependency '{source.params['ref']}' has no usable value")
            return ExtractedValue(
                value=dependency.value,
                unit=dependency.unit,
                source=f"metric:{dependency.metric}",
                used_fallback=dependency.status is Status.FALLBACK,
            )
        resolver = self.resolvers.get(source.type)
        if resolver is None:
            raise InputError(f"no resolver configured for source type '{source.type}'")
        extracted = resolver(source, spec)
        if extracted.value is None:
            raise InputError("source returned no value")
        return extracted

    @staticmethod
    def _normalize(extracted: ExtractedValue, spec: InputSpec) -> ExtractedValue:
        """Convert to the unit the input declares. A source that reports no unit is taken to
        already be in the declared unit; one that reports an incompatible unit is rejected."""
        if extracted.unit is not None and extracted.value is not None:
            extracted.value = convert(extracted.value, extracted.unit, spec.unit)
        extracted.unit = spec.unit
        return extracted

    @staticmethod
    def _closure(company: CompanySpec, names: set[str]) -> set[str]:
        needed: set[str] = set()
        stack = list(names)
        while stack:
            name = stack.pop()
            if name in needed:
                continue
            needed.add(name)
            stack.extend(company.metric(name).dependencies)
        return needed
