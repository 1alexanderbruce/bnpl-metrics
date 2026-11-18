"""End-to-end runs of config/example.yaml with scripted model and EDGAR responses."""

from datetime import date, timedelta
from pathlib import Path

import pytest
from openpyxl import Workbook

from bnpl_metrics.errors import InputError
from bnpl_metrics.extraction.llm_extractor import LLMExtractor
from bnpl_metrics.models import Status
from bnpl_metrics.pipeline import Pipeline
from bnpl_metrics.sources.catalog import ValueCatalog
from bnpl_metrics.sources.edgar import EdgarClient
from bnpl_metrics.specs import load_specs
from bnpl_metrics.store import ResultStore
from helpers import FakeHttp, RoutedLLM, make_pdf

EXAMPLE = Path(__file__).resolve().parents[1] / "config" / "example.yaml"
DECK_TEXT = "Active consumers reached 4.4 million. Total assets of A$12.0 billion."
FILING_TEXT = (
    "United States accounted for 40% of total revenue. "
    "US revenue was $2,000 million. Total revenue was $5,000 million."
)
LLM_ROUTES = [
    ("share of total revenue", {"value": 40, "unit": "percent", "confidence": "high",
                                "evidence": "United States accounted for 40% of total revenue"}),
    ("revenue attributable to the United States", {"value": 2000, "unit": "million USD",
        "confidence": "high", "evidence": "US revenue was $2,000 million"}),
    ("total revenue, in USD", {"value": 5000, "unit": "million USD", "confidence": "high",
        "evidence": "Total revenue was $5,000 million"}),
    ("active consumers", {"value": 4.4, "unit": "million", "confidence": "high",
        "evidence": "Active consumers reached 4.4 million"}),
    ("total assets", {"value": 12.0, "unit": "billion AUD", "confidence": "high",
        "evidence": "Total assets of A$12.0 billion"}),
]  # fmt: skip


@pytest.fixture
def company():
    return load_specs(EXAMPLE)


@pytest.fixture
def files(tmp_path):
    book = Workbook()
    sheet = book.active
    sheet.title = "Operating Metrics"
    sheet.append(["Metric", "Q1", "Q2", "Q3", "Q4", "Q5"])
    sheet.append(["Gross merchandise volume", 10, 11, 12, 13, 14])
    xlsx = tmp_path / "financials.xlsx"
    book.save(xlsx)
    deck = make_pdf(tmp_path / "deck.pdf", DECK_TEXT)
    return {"historical_financials": xlsx, "annual_presentation": deck}


def edgar_client():
    filed = date.today() - timedelta(days=30)
    accession = "0001-26-000001"
    base = "https://www.sec.gov/Archives/edgar/data/123/000126000001"
    return EdgarClient(
        FakeHttp(
            {
                "https://www.sec.gov/files/company_tickers.json": {
                    "0": {"cik_str": 123, "ticker": "EXAMPLECO", "title": "Example"}
                },
                "https://data.sec.gov/submissions/CIK0000000123.json": {
                    "filings": {
                        "recent": {
                            "form": ["10-K"],
                            "filingDate": [filed.isoformat()],
                            "accessionNumber": [accession],
                            "primaryDocument": ["annual.htm"],
                        }
                    }
                },
                f"{base}/annual.htm": f"<html><body><p>{FILING_TEXT}</p></body></html>",
            }
        )
    )


def make_pipeline(tmp_path, companies, *, llm=True, edgar=True):
    return Pipeline(
        companies,
        ResultStore(tmp_path / "cache" / "results.json"),
        ValueCatalog(tmp_path / "cache" / "values.json"),
        extractor=LLMExtractor(RoutedLLM(LLM_ROUTES), use_embeddings=False) if llm else None,
        edgar=edgar_client() if edgar else None,
    )


def test_full_run_with_every_source_available(tmp_path, company, files):
    pipeline = make_pipeline(tmp_path, company)
    assert pipeline.extract("EXAMPLECO", files) == {"total_assets": None}

    summary = pipeline.run_company("EXAMPLECO", files)
    results = summary.results

    assert results["us_gmv"].value == pytest.approx(50 * 0.4)  # last four quarters x US share
    assert results["us_gmv"].status is Status.OK
    assert results["pay_in_4_gmv"].value == pytest.approx(5.0)
    assert results["pay_in_4_share"].value == pytest.approx(25.0)
    assert results["us_share_of_revenue"].value == pytest.approx(0.4)
    assert results["active_consumers"].value == pytest.approx(4.4 * 0.4)
    # The exchange rate comes from a declared fallback, so the result says so.
    assert results["total_assets_usd"].value == pytest.approx(12.0 * 0.65)
    assert results["total_assets_usd"].status is Status.FALLBACK
    assert summary.exit_code == 0
    assert results["us_gmv"].inputs["us_share"].source.startswith("filing:10-K")


def test_run_without_llm_or_edgar_degrades_visibly(tmp_path, company, files):
    pipeline = make_pipeline(tmp_path, company, llm=False, edgar=False)
    summary = pipeline.run_company("EXAMPLECO", files)
    results = summary.results

    # us_gmv still computes, but only via its declared fallback share.
    assert results["us_gmv"].status is Status.FALLBACK
    assert results["us_gmv"].value == pytest.approx(50 * 0.5)
    assert results["pay_in_4_gmv"].status is Status.FALLBACK
    # Metrics with no fallback fail, with reasons, instead of inventing values.
    assert results["active_consumers"].status is Status.FAILED
    assert results["us_share_of_revenue"].status is Status.FAILED
    assert results["total_assets_usd"].status is Status.FAILED
    assert "EDGAR access not configured" in results["us_share_of_revenue"].error
    assert "no LLM configured" in results["active_consumers"].error
    assert summary.exit_code == 1


def test_extraction_reports_per_target_failures(tmp_path, company, files):
    no_llm = make_pipeline(tmp_path, company, llm=False)
    outcome = no_llm.extract("EXAMPLECO", files)
    assert "no LLM configured" in outcome["total_assets"]

    missing_file = make_pipeline(tmp_path, company)
    outcome = missing_file.extract("EXAMPLECO", {})
    assert "no file supplied" in outcome["total_assets"]


def test_results_persist_and_a_failed_refresh_keeps_the_last_good_value(tmp_path, company, files):
    pipeline = make_pipeline(tmp_path, company)
    pipeline.extract("EXAMPLECO", files)
    pipeline.run_company("EXAMPLECO", files)

    reloaded = make_pipeline(tmp_path, company)
    assert reloaded.store.display("EXAMPLECO", "us_gmv").value == pytest.approx(20.0)

    # A later run without the spreadsheet fails the metric but must not lose the old value.
    reloaded.run_company(
        "EXAMPLECO", {k: v for k, v in files.items() if k != "historical_financials"}
    )
    shown = reloaded.store.display("EXAMPLECO", "us_gmv")
    assert shown.value == pytest.approx(20.0)
    assert shown.stale
    assert "no file supplied" in shown.note


def test_implausible_jump_is_flagged_for_review(tmp_path, company, files):
    pipeline = make_pipeline(tmp_path, company)
    pipeline.run_company("EXAMPLECO", files)

    book = Workbook()
    sheet = book.active
    sheet.title = "Operating Metrics"
    sheet.append(["Metric", "Q1", "Q2", "Q3", "Q4"])
    sheet.append(["Gross merchandise volume", 100, 110, 120, 130])
    big = tmp_path / "big.xlsx"
    book.save(big)

    summary = pipeline.run_company(
        "EXAMPLECO", {**files, "historical_financials": big}, only={"us_gmv"}
    )
    flagged = summary.results["us_gmv"]
    assert flagged.status is Status.NEEDS_REVIEW
    assert any("differs from the previous" in w for w in flagged.warnings)
    assert summary.exit_code == 2
    assert pipeline.store.display("EXAMPLECO", "us_gmv").value == pytest.approx(20.0)


def test_only_runs_requested_metrics_and_can_skip_persisting(tmp_path, company, files):
    pipeline = make_pipeline(tmp_path, company)
    summary = pipeline.run_company("EXAMPLECO", files, only={"pay_in_4_gmv"}, persist=False)
    assert set(summary.results) == {"us_gmv", "pay_in_4_gmv"}
    assert pipeline.store.company_ids() == []


def test_unknown_company(tmp_path, company):
    with pytest.raises(InputError, match="unknown company"):
        make_pipeline(tmp_path, company).run_company("NOPE")
