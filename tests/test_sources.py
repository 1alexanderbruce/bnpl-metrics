from datetime import date

import pytest
from openpyxl import Workbook as XlWorkbook

from bnpl_metrics.errors import InputError
from bnpl_metrics.extraction.llm_extractor import LLMExtractor
from bnpl_metrics.models import ExtractedValue
from bnpl_metrics.sources.catalog import ValueCatalog
from bnpl_metrics.sources.edgar import EdgarClient, Filing, html_to_text
from bnpl_metrics.sources.excel import Workbook
from bnpl_metrics.sources.pdf import extract_text
from bnpl_metrics.sources.resolvers import build_resolvers
from bnpl_metrics.specs import CompanySpec, InputSpec, SourceSpec
from helpers import FakeHttp, ScriptedLLM, make_pdf


@pytest.fixture
def xlsx(tmp_path):
    book = XlWorkbook()
    sheet = book.active
    sheet.title = "Operating Metrics"
    sheet.append(["Metric", "Q1", "Q2", "Q3", "Q4", "Q5"])
    sheet.append(["Gross merchandise volume", 10, 11, 12, 13, 14])
    sheet.append(["Active consumers (millions)", 1.5, 1.6, 1.7, None, None])
    sheet.append(["Net revenue", "$1,000", "$1,100", "(50)", 1300, 1400])
    sheet.append(["Net revenue growth", 0.1, 0.1, 0.1, 0.1, 0.1])
    sheet.append(["Revenue per consumer", 1, 2, 3, 4, 5])
    other = book.create_sheet("Balance Sheet")
    other.append(["Total assets", 100, 110])
    path = tmp_path / "financials.xlsx"
    book.save(path)
    return path


# ---- spreadsheets ---------------------------------------------------------------------------


def test_excel_latest_and_sum(xlsx):
    book = Workbook(xlsx)
    assert book.aggregate("Operating Metrics", "Gross merchandise volume") == 14
    assert book.aggregate("Operating Metrics", "Gross merchandise volume", "sum_last_n", 4) == 50
    assert book.aggregate("Operating Metrics", "Gross merchandise volume", "mean_last_n", 2) == 13.5


def test_excel_skips_blank_cells_and_parses_formatted_numbers(xlsx):
    book = Workbook(xlsx)
    assert book.aggregate("operating metrics", "active consumers") == 1.7
    assert book.row_values("Operating Metrics", "Net revenue")[:3] == [1000.0, 1100.0, -50.0]


def test_excel_exact_match_beats_substring_but_ambiguous_substring_fails(xlsx):
    book = Workbook(xlsx)
    assert book.aggregate("Operating Metrics", "net revenue") == 1400
    with pytest.raises(InputError, match="matches 3 rows"):
        book.aggregate("Operating Metrics", "revenue")


def test_excel_short_window_is_an_error_not_a_partial_sum(xlsx):
    with pytest.raises(InputError, match="need 4 periods"):
        Workbook(xlsx).aggregate("Operating Metrics", "Active consumers", "sum_last_n", 4)


def test_excel_sheet_and_row_errors_are_specific(xlsx):
    book = Workbook(xlsx)
    with pytest.raises(InputError, match="available"):
        book.frame("Nope")
    with pytest.raises(InputError, match="not found"):
        book.aggregate("Operating Metrics", "Does not exist")
    with pytest.raises(InputError, match="unknown aggregate"):
        book.aggregate("Operating Metrics", "Net revenue", "median")
    with pytest.raises(InputError, match="not found"):
        Workbook("/nonexistent/file.xlsx")


def test_excel_markdown_for_llm_path(xlsx):
    text = Workbook(xlsx).to_markdown("Operating Metrics")
    assert "Gross merchandise volume" in text and "|" in text


# ---- PDFs -----------------------------------------------------------------------------------


def test_pdf_text_extraction(tmp_path):
    path = make_pdf(tmp_path / "deck.pdf", "Active consumers reached 4.4 million")
    text = extract_text(path)
    assert "[page 1]" in text and "4.4 million" in text


def test_pdf_errors(tmp_path):
    with pytest.raises(InputError, match="not found"):
        extract_text(tmp_path / "missing.pdf")
    (tmp_path / "bad.pdf").write_bytes(b"not a pdf")
    with pytest.raises(InputError):
        extract_text(tmp_path / "bad.pdf")


# ---- EDGAR ----------------------------------------------------------------------------------


SUBMISSIONS = {
    "filings": {
        "recent": {
            "form": ["10-Q", "8-K", "10-K", "10-K"],
            "filingDate": ["2026-05-01", "2026-04-01", "2026-02-20", "2024-02-20"],
            "accessionNumber": [
                "0001-26-000003",
                "0001-26-000002",
                "0001-26-000001",
                "0001-24-000009",
            ],
            "primaryDocument": ["q.htm", "k8.htm", "annual.htm", "old.htm"],
        }
    }
}
BASE = "https://www.sec.gov/Archives/edgar/data/123/000126000002"
EDGAR_ROUTES = {
    "https://www.sec.gov/files/company_tickers.json": {
        "0": {"cik_str": 123, "ticker": "EXMP", "title": "Example"}
    },
    "https://data.sec.gov/submissions/CIK0000000123.json": SUBMISSIONS,
    f"{BASE}/k8.htm": "<html><script>x</script><p>Cover page</p></html>",
    f"{BASE}/index.json": {
        "directory": {"item": [{"name": "ex99-1.htm"}, {"name": "ex99-1.pdf"}, {"name": "k8.htm"}]}
    },
    f"{BASE}/ex99-1.htm": "<table><tr><td>Revenue</td><td>$5 billion</td></tr></table>",
}


def test_html_to_text_drops_scripts_and_blank_lines():
    assert html_to_text("<p>a</p><script>bad()</script>\n\n<p> b </p>") == "a\nb"


def test_latest_filing_respects_form_and_age():
    client = EdgarClient(FakeHttp(EDGAR_ROUTES))
    today = date(2026, 6, 1)
    assert client.latest_filing("exmp", ["10-K"], 400, today).accession == "0001-26-000001"
    assert client.latest_filing("EXMP", ["10-K", "10-Q"], 400, today).form == "10-Q"
    with pytest.raises(InputError, match="no 10-K filing"):
        client.latest_filing("EXMP", ["10-K"], 30, today)
    with pytest.raises(InputError, match="no 20-F"):
        client.latest_filing("EXMP", ["20-F"], 4000, today)


def test_unknown_ticker():
    with pytest.raises(InputError, match="not found"):
        EdgarClient(FakeHttp(EDGAR_ROUTES)).cik("NOPE")


def test_exhibit_text_is_included_for_8k():
    client = EdgarClient(FakeHttp(EDGAR_ROUTES))
    filing = Filing("EXMP", 123, "8-K", date(2026, 4, 1), "0001-26-000002", "k8.htm")
    text = client.filing_text(filing)
    assert "Cover page" in text and "$5 billion" in text
    assert "Cover page" in client.filing_text(filing, include_exhibits=False)
    assert "$5 billion" not in client.filing_text(filing, include_exhibits=False)


# ---- resolvers ------------------------------------------------------------------------------


def source(type_, **params):
    return SourceSpec(type_, params)


COMPANY = CompanySpec(id="EXMP", name="Example", metrics=())
INPUT = InputSpec("x", "billion USD", ())


def test_constant_and_catalog_resolvers(tmp_path):
    catalog = ValueCatalog(tmp_path / "v.json")
    catalog.set("EXMP", "assets", 7.0, "billion AUD", "deck p2", "2025-06-30")
    resolvers = build_resolvers(COMPANY, catalog=catalog)
    assert resolvers["constant"](source("constant", value=0.5), INPUT).value == 0.5
    got = resolvers["catalog"](source("catalog", key="assets"), INPUT)
    assert (got.value, got.unit) == (7.0, "billion AUD")
    assert "as of 2025-06-30" in got.source
    with pytest.raises(InputError):
        resolvers["catalog"](source("catalog", key="missing"), INPUT)
    with pytest.raises(InputError, match="number"):
        resolvers["constant"](source("constant", value="high"), INPUT)


def test_excel_resolver_deterministic_and_llm_paths(xlsx):
    llm = ScriptedLLM(value=1.7, unit="million", confidence="high", evidence="Active consumers")
    resolvers = build_resolvers(
        COMPANY, files={"fin": xlsx}, extractor=LLMExtractor(llm, use_embeddings=False)
    )
    row = source(
        "excel", file="fin", sheet="Operating Metrics", row="Gross merchandise volume",
        aggregate="sum_last_n", n=4, unit="billion USD",
    )  # fmt: skip
    got = resolvers["excel"](row, INPUT)
    assert got.value == 50 and got.unit == "billion USD" and "sum_last_n" in got.source
    asked = source("excel", file="fin", sheet="Operating Metrics", question="How many consumers?")
    assert resolvers["excel"](asked, INPUT).value == 1.7
    assert "Active consumers" in llm.questions[0]


def test_excel_resolver_needs_the_uploaded_file(xlsx):
    resolvers = build_resolvers(COMPANY, files={})
    with pytest.raises(InputError, match="no file supplied"):
        resolvers["excel"](source("excel", file="fin", sheet="s", row="r"), INPUT)


def test_pdf_and_filing_resolvers_use_the_extractor(tmp_path):
    pdf = make_pdf(tmp_path / "deck.pdf", "Active consumers reached 4.4 million")
    evidence = "Active consumers reached 4.4 million"
    llm = ScriptedLLM(value=4.4, unit="million", confidence="high", evidence=evidence)
    annual_url = BASE.replace("000126000002", "000126000001") + "/annual.htm"
    http = FakeHttp({**EDGAR_ROUTES, annual_url: f"<p>{evidence}</p>"})
    resolvers = build_resolvers(
        COMPANY,
        files={"deck": pdf},
        extractor=LLMExtractor(llm, use_embeddings=False),
        edgar=EdgarClient(http),
    )
    got = resolvers["pdf"](source("pdf", file="deck", question="How many consumers?"), INPUT)
    assert got.value == 4.4 and got.source == "pdf:deck.pdf"

    ask = source("filing", forms=["10-K"], question="How many consumers?", max_age_days=5000)
    got = resolvers["filing"](ask, INPUT)
    assert got.value == 4.4 and got.source.startswith("filing:10-K")


def test_llm_sources_fail_clearly_without_an_llm_or_edgar(tmp_path):
    pdf = make_pdf(tmp_path / "deck.pdf", "x")
    resolvers = build_resolvers(COMPANY, files={"deck": pdf})
    with pytest.raises(InputError, match="no LLM configured"):
        resolvers["pdf"](source("pdf", file="deck", question="q"), INPUT)
    with pytest.raises(InputError, match="EDGAR access not configured"):
        resolvers["filing"](source("filing", forms=["10-K"], question="q"), INPUT)


def test_extracted_value_defaults():
    assert ExtractedValue(value=1.0).used_fallback is False
