"""The automation template: documents -> extracted figures -> metrics -> validated results.

``extract`` turns uploaded documents into catalog values, ``run_company`` calculates metrics
from every configured source, and results are persisted without ever overwriting a good value
with a failed one.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from bnpl_metrics.calculation.engine import Engine, Validator
from bnpl_metrics.errors import InputError
from bnpl_metrics.extraction.llm_extractor import LLMExtractor
from bnpl_metrics.logging_utils import get_logger
from bnpl_metrics.models import MetricResult, Status
from bnpl_metrics.sources.catalog import ValueCatalog
from bnpl_metrics.sources.edgar import EdgarClient
from bnpl_metrics.sources.pdf import extract_text
from bnpl_metrics.sources.resolvers import build_resolvers
from bnpl_metrics.specs import CompanySpec
from bnpl_metrics.store import ResultStore

log = get_logger(__name__)


@dataclass
class RunSummary:
    company_id: str
    results: dict[str, MetricResult]
    counts: Counter = field(default_factory=Counter)

    def __post_init__(self) -> None:
        self.counts = Counter(r.status for r in self.results.values())

    @property
    def exit_code(self) -> int:
        """0 = all values usable, 1 = at least one failure, 2 = values need review."""
        if self.counts[Status.FAILED]:
            return 1
        if self.counts[Status.NEEDS_REVIEW]:
            return 2
        return 0


class Pipeline:
    def __init__(
        self,
        companies: list[CompanySpec],
        store: ResultStore,
        catalog: ValueCatalog,
        extractor: LLMExtractor | None = None,
        edgar: EdgarClient | None = None,
        validators: list[Validator] | None = None,
    ) -> None:
        self.companies = {c.id: c for c in companies}
        self.store = store
        self.catalog = catalog
        self.extractor = extractor
        self.edgar = edgar
        self.validators = validators or []

    def company(self, company_id: str) -> CompanySpec:
        try:
            return self.companies[company_id]
        except KeyError:
            raise InputError(
                f"unknown company '{company_id}'; configured: {sorted(self.companies)}"
            ) from None

    def extract(self, company_id: str, files: Mapping[str, Path]) -> dict[str, str | None]:
        """Run the company's extraction targets. Returns ``{key: None}`` on success or
        ``{key: reason}`` on failure; one failure does not stop the others."""
        company = self.company(company_id)
        outcome: dict[str, str | None] = {}
        for target in company.extractions:
            try:
                if self.extractor is None:
                    raise InputError("no LLM configured (set LLM_API_KEY)")
                if target.file not in files:
                    raise InputError(f"no file supplied for '{target.file}'")
                path = Path(files[target.file])
                value = self.extractor.ask_number(
                    target.question,
                    extract_text(path),
                    source=path.name,
                    min_confidence=target.min_confidence,
                )
                self.catalog.set(
                    company_id, target.key, value.value, value.unit or target.unit, path.name
                )
                outcome[target.key] = None
            except Exception as exc:
                log.warning("extraction '%s' failed: %s", target.key, exc)
                outcome[target.key] = str(exc)
        self.catalog.save()
        return outcome

    def run_company(
        self,
        company_id: str,
        files: Mapping[str, Path] | None = None,
        only: set[str] | None = None,
        persist: bool = True,
    ) -> RunSummary:
        company = self.company(company_id)
        resolvers = build_resolvers(
            company,
            files=files,
            extractor=self.extractor,
            edgar=self.edgar,
            catalog=self.catalog,
        )
        engine = Engine(resolvers, validators=self.validators)
        results = engine.calculate_company(
            company, previous=self.store.previous(company_id), only=only
        )
        if persist:
            for result in results.values():
                self.store.update(company.name, result)
            self.store.save()
        return RunSummary(company_id, results)
