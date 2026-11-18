"""Metric specifications: load and validate YAML into typed objects."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from bnpl_metrics.calculation.expr import FormulaError, formula_names
from bnpl_metrics.calculation.units import UnitError, parse_unit

SOURCE_TYPES = {"constant", "excel", "filing", "pdf", "catalog", "metric"}
_REQUIRED_PARAMS = {
    "constant": ("value",),
    "excel": ("file", "sheet"),
    "filing": ("forms", "question"),
    "pdf": ("file", "question"),
    "catalog": ("key",),
    "metric": ("ref",),
}


class SpecError(ValueError):
    """Raised when a specification file is invalid."""


@dataclass(frozen=True)
class SourceSpec:
    type: str
    params: dict[str, Any] = field(default_factory=dict)
    fallback: bool = False
    reason: str | None = None


@dataclass(frozen=True)
class InputSpec:
    name: str
    unit: str
    sources: tuple[SourceSpec, ...]


@dataclass(frozen=True)
class ValidationSpec:
    min: float | None = None
    max: float | None = None
    max_change_ratio: float | None = None


@dataclass(frozen=True)
class MetricSpec:
    company_id: str
    name: str
    display_name: str
    unit: str
    formula: str
    inputs: dict[str, InputSpec]
    validation: ValidationSpec = field(default_factory=ValidationSpec)
    description: str = ""

    @property
    def dependencies(self) -> set[str]:
        return {
            source.params["ref"]
            for spec in self.inputs.values()
            for source in spec.sources
            if source.type == "metric"
        }


@dataclass(frozen=True)
class ExtractionTarget:
    """A figure to pull out of an uploaded document and store in the value catalog."""

    key: str
    file: str
    question: str
    unit: str | None = None  # used only when the model does not report a unit
    min_confidence: str = "medium"


@dataclass(frozen=True)
class DocumentSource:
    """A public page listing documents to download, e.g. an investor-relations reports page.

    Only point this at pages whose terms allow automated access; robots.txt is always checked.
    """

    id: str
    index_url: str
    link_pattern: str  # regex matched against each link's URL and text
    doc_type: str = ""
    max_documents: int = 10
    javascript: bool = False  # render the page in a browser first (needs the selenium extra)


@dataclass(frozen=True)
class CompanySpec:
    id: str
    name: str
    metrics: tuple[MetricSpec, ...]
    files: dict[str, str] = field(default_factory=dict)
    ticker: str = ""  # SEC ticker used by filing sources; defaults to the id
    extractions: tuple[ExtractionTarget, ...] = ()
    documents: tuple[DocumentSource, ...] = ()

    def __post_init__(self) -> None:
        if not self.ticker:
            object.__setattr__(self, "ticker", self.id)

    def metric(self, name: str) -> MetricSpec:
        for spec in self.metrics:
            if spec.name == name:
                return spec
        raise KeyError(f"{self.id} has no metric {name!r}")

    def ordered_metrics(self) -> list[MetricSpec]:
        """Metrics ordered so every metric follows the metrics it depends on."""
        ordered: list[MetricSpec] = []
        done: set[str] = set()
        remaining = list(self.metrics)
        while remaining:
            ready = [m for m in remaining if m.dependencies <= done]
            if not ready:
                raise SpecError(f"{self.id}: circular metric dependencies")
            for metric in ready:
                ordered.append(metric)
                done.add(metric.name)
                remaining.remove(metric)
        return ordered


def _require(mapping: dict[str, Any], key: str, where: str) -> Any:
    if key not in mapping or mapping[key] in (None, ""):
        raise SpecError(f"{where}: missing '{key}'")
    return mapping[key]


def _parse_source(raw: dict[str, Any], where: str) -> SourceSpec:
    if not isinstance(raw, dict):
        raise SpecError(f"{where}: source must be a mapping")
    source_type = _require(raw, "type", where)
    if source_type not in SOURCE_TYPES:
        raise SpecError(f"{where}: unknown source type {source_type!r}")
    params = {k: v for k, v in raw.items() if k not in ("type", "fallback", "reason")}
    for key in _REQUIRED_PARAMS[source_type]:
        if key not in params:
            raise SpecError(f"{where}: '{source_type}' source needs '{key}'")
    if source_type == "excel" and not ({"row", "question"} & params.keys()):
        raise SpecError(f"{where}: 'excel' source needs 'row' or 'question'")
    fallback = bool(raw.get("fallback", False))
    reason = raw.get("reason")
    if fallback and not reason:
        raise SpecError(f"{where}: fallback sources must explain themselves with 'reason'")
    return SourceSpec(source_type, params, fallback, reason)


def _parse_metric(raw: dict[str, Any], company_id: str) -> MetricSpec:
    name = _require(raw, "name", f"{company_id} metric")
    where = f"{company_id}.{name}"
    unit = _require(raw, "unit", where)
    formula = _require(raw, "formula", where)
    try:
        parse_unit(unit)
        needed = formula_names(formula)
    except (UnitError, FormulaError) as exc:
        raise SpecError(f"{where}: {exc}") from exc
    raw_inputs = raw.get("inputs") or {}
    inputs: dict[str, InputSpec] = {}
    for input_name, raw_input in raw_inputs.items():
        input_where = f"{where}.{input_name}"
        input_unit = _require(raw_input, "unit", input_where)
        try:
            parse_unit(input_unit)
        except UnitError as exc:
            raise SpecError(f"{input_where}: {exc}") from exc
        raw_sources = raw_input.get("sources") or []
        if not raw_sources:
            raise SpecError(f"{input_where}: needs at least one source")
        sources = tuple(_parse_source(s, input_where) for s in raw_sources)
        inputs[input_name] = InputSpec(input_name, input_unit, sources)
    missing = needed - set(inputs)
    if missing:
        raise SpecError(f"{where}: formula uses undefined inputs {sorted(missing)}")
    validation = ValidationSpec(**(raw.get("validation") or {}))
    return MetricSpec(
        company_id=company_id,
        name=name,
        display_name=raw.get("display_name", name),
        unit=unit,
        formula=formula,
        inputs=inputs,
        validation=validation,
        description=raw.get("description", ""),
    )


def _parse_extraction(
    raw: dict[str, Any], company_id: str, files: dict[str, str]
) -> ExtractionTarget:
    where = f"{company_id}.extractions"
    key = _require(raw, "key", where)
    file = _require(raw, "file", f"{where}.{key}")
    if file not in files:
        raise SpecError(f"{where}.{key}: file '{file}' is not declared under 'files'")
    unit = raw.get("unit")
    if unit:
        try:
            parse_unit(unit)
        except UnitError as exc:
            raise SpecError(f"{where}.{key}: {exc}") from exc
    return ExtractionTarget(
        key=key,
        file=file,
        question=_require(raw, "question", f"{where}.{key}"),
        unit=unit,
        min_confidence=raw.get("min_confidence", "medium"),
    )


def _parse_document_source(raw: dict[str, Any], company_id: str) -> DocumentSource:
    where = f"{company_id}.documents"
    source_id = _require(raw, "id", where)
    where = f"{where}.{source_id}"
    index_url = _require(raw, "index_url", where)
    if not index_url.startswith(("http://", "https://")):
        raise SpecError(f"{where}: index_url must be an http(s) URL")
    pattern = _require(raw, "link_pattern", where)
    try:
        re.compile(pattern)
    except re.error as exc:
        raise SpecError(f"{where}: invalid link_pattern: {exc}") from exc
    return DocumentSource(
        id=source_id,
        index_url=index_url,
        link_pattern=pattern,
        doc_type=raw.get("doc_type", ""),
        max_documents=int(raw.get("max_documents", 10)),
        javascript=bool(raw.get("javascript", False)),
    )


def parse_specs(data: dict[str, Any]) -> list[CompanySpec]:
    companies_raw = (data or {}).get("companies")
    if not companies_raw:
        raise SpecError("spec file has no 'companies'")
    companies: list[CompanySpec] = []
    for raw in companies_raw:
        company_id = _require(raw, "id", "company")
        metrics = tuple(_parse_metric(m, company_id) for m in raw.get("metrics") or [])
        names = [m.name for m in metrics]
        if len(names) != len(set(names)):
            raise SpecError(f"{company_id}: duplicate metric names")
        for metric in metrics:
            unknown = metric.dependencies - set(names)
            if unknown:
                raise SpecError(
                    f"{company_id}.{metric.name}: unknown metric refs {sorted(unknown)}"
                )
        files = dict(raw.get("files") or {})
        extractions = tuple(
            _parse_extraction(e, company_id, files) for e in raw.get("extractions") or []
        )
        company = CompanySpec(
            id=company_id,
            name=raw.get("name", company_id),
            metrics=metrics,
            files=files,
            ticker=raw.get("ticker", ""),
            extractions=extractions,
            documents=tuple(
                _parse_document_source(d, company_id) for d in raw.get("documents") or []
            ),
        )
        company.ordered_metrics()
        companies.append(company)
    ids = [c.id for c in companies]
    if len(ids) != len(set(ids)):
        raise SpecError("duplicate company ids")
    return companies


def load_specs(path: str | Path) -> list[CompanySpec]:
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise SpecError(f"{path}: invalid YAML: {exc}") from exc
    return parse_specs(data)
