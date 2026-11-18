"""Turn source specifications into values.

``build_resolvers`` returns one resolver per source type, bound to the files, extractor, EDGAR
client and catalog available for a run. Missing pieces produce clear InputErrors at resolve
time, so a metric can fall through to its next source.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from bnpl_metrics.calculation.engine import Resolver
from bnpl_metrics.errors import InputError
from bnpl_metrics.extraction.llm_extractor import LLMExtractor
from bnpl_metrics.models import ExtractedValue
from bnpl_metrics.sources.catalog import ValueCatalog
from bnpl_metrics.sources.edgar import EdgarClient
from bnpl_metrics.sources.excel import Workbook
from bnpl_metrics.sources.pdf import extract_text
from bnpl_metrics.specs import CompanySpec, InputSpec, SourceSpec


def _file(files: Mapping[str, Path], key: str) -> Path:
    if key not in files:
        raise InputError(f"no file supplied for '{key}'")
    return Path(files[key])


def build_resolvers(
    company: CompanySpec,
    *,
    files: Mapping[str, Path] | None = None,
    extractor: LLMExtractor | None = None,
    edgar: EdgarClient | None = None,
    catalog: ValueCatalog | None = None,
) -> dict[str, Resolver]:
    files = files or {}
    workbooks: dict[Path, Workbook] = {}
    texts: dict[Path, str] = {}
    filing_texts: dict[tuple[str, tuple[str, ...], int], tuple[str, str]] = {}

    def need_extractor() -> LLMExtractor:
        if extractor is None:
            raise InputError("no LLM configured (set LLM_API_KEY)")
        return extractor

    def constant(source: SourceSpec, spec: InputSpec) -> ExtractedValue:
        value = source.params["value"]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise InputError(f"constant must be a number, got {value!r}")
        return ExtractedValue(value=float(value), unit=source.params.get("unit"), source="constant")

    def excel(source: SourceSpec, spec: InputSpec) -> ExtractedValue:
        path = _file(files, source.params["file"])
        if path not in workbooks:
            workbooks[path] = Workbook(path)
        workbook = workbooks[path]
        sheet = source.params["sheet"]
        label = f"excel:{path.name}!{sheet}"
        if "row" in source.params:
            row = source.params["row"]
            how = source.params.get("aggregate", "latest")
            periods = int(source.params.get("n", 4))
            return ExtractedValue(
                value=workbook.aggregate(sheet, row, how, periods),
                unit=source.params.get("unit"),
                source=f"{label}/{row} ({how})",
            )
        return need_extractor().ask_number(
            source.params["question"],
            workbook.to_markdown(sheet),
            source=label,
            min_confidence=source.params.get("min_confidence", "medium"),
        )

    def pdf(source: SourceSpec, spec: InputSpec) -> ExtractedValue:
        path = _file(files, source.params["file"])
        if path not in texts:
            texts[path] = extract_text(path)
        return need_extractor().ask_number(
            source.params["question"],
            texts[path],
            source=f"pdf:{path.name}",
            min_confidence=source.params.get("min_confidence", "medium"),
        )

    def filing(source: SourceSpec, spec: InputSpec) -> ExtractedValue:
        if edgar is None:
            raise InputError("EDGAR access not configured (set SEC_USER_AGENT)")
        forms = list(source.params["forms"])
        max_age = int(source.params.get("max_age_days", 400))
        cache_key = (company.ticker, tuple(forms), max_age)
        if cache_key not in filing_texts:
            found = edgar.latest_filing(company.ticker, forms, max_age)
            label = f"filing:{found.form} {found.filing_date} {found.accession}"
            filing_texts[cache_key] = (label, edgar.filing_text(found))
        label, text = filing_texts[cache_key]
        return need_extractor().ask_number(
            source.params["question"],
            text,
            source=label,
            min_confidence=source.params.get("min_confidence", "medium"),
        )

    def from_catalog(source: SourceSpec, spec: InputSpec) -> ExtractedValue:
        if catalog is None:
            raise InputError("no value catalog configured")
        entry = catalog.get(company.id, source.params["key"])
        as_of = f", as of {entry.as_of}" if entry.as_of else ""
        return ExtractedValue(
            value=entry.value,
            unit=entry.unit,
            source=f"catalog:{source.params['key']} ({entry.source}{as_of})",
        )

    return {
        "constant": constant,
        "excel": excel,
        "pdf": pdf,
        "filing": filing,
        "catalog": from_catalog,
    }
