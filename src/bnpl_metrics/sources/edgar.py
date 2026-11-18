"""SEC EDGAR access via the official JSON APIs.

Follows SEC fair-access rules: a descriptive User-Agent with contact details (required) and
at most 10 requests per second. robots.txt is not consulted for the API hosts, which are
documented for programmatic use.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Protocol

from bs4 import BeautifulSoup

from bnpl_metrics.errors import InputError
from bnpl_metrics.logging_utils import get_logger
from bnpl_metrics.settings import Settings
from bnpl_metrics.sources.web import PoliteSession

log = get_logger(__name__)

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{name}"
EXHIBIT_FORMS = {"6-K", "8-K"}
_EXHIBIT_NAME = re.compile(r"ex-?99", re.IGNORECASE)


class Http(Protocol):
    def get(self, url: str, **kwargs: Any) -> Any: ...


@dataclass(frozen=True)
class Filing:
    ticker: str
    cik: int
    form: str
    filing_date: date
    accession: str
    primary_document: str

    @property
    def accession_path(self) -> str:
        return self.accession.replace("-", "")

    @property
    def url(self) -> str:
        return ARCHIVE_URL.format(
            cik=self.cik, accession=self.accession_path, name=self.primary_document
        )


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    lines = (line.strip() for line in soup.get_text("\n").splitlines())
    return "\n".join(line for line in lines if line)


class EdgarClient:
    def __init__(self, http: Http) -> None:
        self._http = http
        self._cik_cache: dict[str, int] = {}

    @classmethod
    def from_settings(cls, settings: Settings) -> EdgarClient:
        http = PoliteSession(
            settings.require_user_agent(),
            proxies=settings.proxies,
            min_delay=0.12,
            respect_robots=False,
        )
        return cls(http)

    def cik(self, ticker: str) -> int:
        key = ticker.upper()
        if key not in self._cik_cache:
            data = self._http.get(TICKERS_URL).json()
            for entry in data.values():
                self._cik_cache[entry["ticker"].upper()] = int(entry["cik_str"])
        if key not in self._cik_cache:
            raise InputError(f"ticker {ticker!r} not found in SEC company list")
        return self._cik_cache[key]

    def latest_filing(
        self,
        ticker: str,
        forms: list[str],
        max_age_days: int = 400,
        today: date | None = None,
    ) -> Filing:
        """Most recent filing of any of ``forms`` within ``max_age_days``."""
        cik = self.cik(ticker)
        recent = self._http.get(SUBMISSIONS_URL.format(cik=cik)).json()["filings"]["recent"]
        cutoff = (today or date.today()) - timedelta(days=max_age_days)
        wanted = {f.upper() for f in forms}
        candidates = []
        for form, filed, accession, document in zip(
            recent["form"],
            recent["filingDate"],
            recent["accessionNumber"],
            recent["primaryDocument"],
            strict=True,
        ):
            filing_date = datetime.strptime(filed, "%Y-%m-%d").date()
            if form.upper() in wanted and filing_date >= cutoff:
                candidates.append(
                    Filing(ticker.upper(), cik, form, filing_date, accession, document)
                )
        if not candidates:
            raise InputError(
                f"no {'/'.join(forms)} filing for {ticker} in the last {max_age_days} days"
            )
        return max(candidates, key=lambda f: f.filing_date)

    def filing_text(self, filing: Filing, include_exhibits: bool = True) -> str:
        """Primary document text, plus exhibit 99 documents for 6-K and 8-K filings, where the
        financial statements usually live."""
        parts = [html_to_text(self._http.get(filing.url).text)]
        if include_exhibits and filing.form.upper() in EXHIBIT_FORMS:
            for name in self._exhibit_names(filing):
                try:
                    exhibit = self._http.get(self._document_url(filing, name))
                    parts.append(html_to_text(exhibit.text))
                except InputError as exc:
                    log.warning("skipping exhibit %s: %s", name, exc)
        return "\n\n".join(p for p in parts if p)

    def _document_url(self, filing: Filing, name: str) -> str:
        return ARCHIVE_URL.format(cik=filing.cik, accession=filing.accession_path, name=name)

    def _exhibit_names(self, filing: Filing) -> list[str]:
        index_url = self._document_url(filing, "index.json")
        items = self._http.get(index_url).json().get("directory", {}).get("item", [])
        return [
            item["name"]
            for item in items
            if _EXHIBIT_NAME.search(item.get("name", ""))
            and item["name"].lower().endswith((".htm", ".html"))
        ]
