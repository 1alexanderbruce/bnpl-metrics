from pathlib import Path

import pytest
from openpyxl import Workbook

from bnpl_metrics.apps.dashboard import Dashboard, match_uploads, metric_detail, results_table
from bnpl_metrics.errors import InputError
from bnpl_metrics.pipeline import Pipeline
from bnpl_metrics.sources.catalog import ValueCatalog
from bnpl_metrics.specs import load_specs
from bnpl_metrics.store import ResultStore
from helpers import make_pdf

EXAMPLE = Path(__file__).resolve().parents[1] / "config" / "example.yaml"


@pytest.fixture
def pipeline(tmp_path):
    return Pipeline(
        load_specs(EXAMPLE),
        ResultStore(tmp_path / "results.json"),
        ValueCatalog(tmp_path / "values.json"),
    )


@pytest.fixture
def spreadsheet(tmp_path):
    book = Workbook()
    sheet = book.active
    sheet.title = "Operating Metrics"
    sheet.append(["Metric", "Q1", "Q2", "Q3", "Q4"])
    sheet.append(["Gross merchandise volume", 10, 11, 12, 13])
    path = tmp_path / "historical_financials.xlsx"
    book.save(path)
    return path


def test_table_lists_every_configured_metric_even_before_any_run(pipeline):
    table = results_table(pipeline)
    assert len(table) == 6
    assert set(table["Status"]) == {"not calculated"}


def test_table_after_a_run_labels_fallback_and_failures(pipeline, spreadsheet):
    pipeline.run_company("EXAMPLECO", {"historical_financials": spreadsheet})
    table = results_table(pipeline).set_index("Metric")
    assert table.loc["US GMV", "Status"] == "fallback"
    assert table.loc["US GMV", "Value"] == "23"
    assert table.loc["US active consumers", "Value"] == "—"
    assert "failed" in table.loc["US active consumers", "Status"]


def test_stale_values_are_flagged_in_the_table(pipeline, spreadsheet):
    pipeline.run_company("EXAMPLECO", {"historical_financials": spreadsheet})
    pipeline.run_company("EXAMPLECO", {})  # spreadsheet missing: refresh fails
    table = results_table(pipeline).set_index("Metric")
    assert table.loc["US GMV", "Status"].endswith("(STALE)")
    assert table.loc["US GMV", "Value"] == "23"


def test_metric_detail_explains_how_the_value_was_built(pipeline, spreadsheet):
    pipeline.run_company("EXAMPLECO", {"historical_financials": spreadsheet})
    text = metric_detail(pipeline, "EXAMPLECO", "us_gmv")
    assert "global_gmv_ttm * us_share" in text
    assert "excel:historical_financials.xlsx" in text
    assert "used fallback" in text


def test_metric_detail_shows_last_good_when_latest_failed(pipeline, spreadsheet):
    pipeline.run_company("EXAMPLECO", {"historical_financials": spreadsheet})
    pipeline.run_company("EXAMPLECO", {})
    text = metric_detail(pipeline, "EXAMPLECO", "us_gmv")
    assert "Latest run" in text and "Last good value (stale)" in text


def test_metric_detail_before_any_run_and_for_unknown_metric(pipeline):
    assert "Not calculated yet" in metric_detail(pipeline, "EXAMPLECO", "us_gmv")
    with pytest.raises(KeyError):
        metric_detail(pipeline, "EXAMPLECO", "nope")
    with pytest.raises(InputError):
        metric_detail(pipeline, "NOPE", "us_gmv")


def test_uploads_are_matched_by_declared_key(pipeline, tmp_path):
    company = pipeline.company("EXAMPLECO")
    files, unmatched = match_uploads(
        company,
        [
            tmp_path / "Historical_Financials.XLSX",
            tmp_path / "random.pdf",
            "annual_presentation.pdf",
        ],
    )
    assert set(files) == {"historical_financials", "annual_presentation"}
    assert unmatched == ["random.pdf"]


def test_gradio_style_file_objects_keep_their_full_path(pipeline, tmp_path):
    class Upload:  # mimics the tempfile wrapper Gradio hands to handlers
        name = str(tmp_path / "historical_financials.xlsx")

    files, unmatched = match_uploads(pipeline.company("EXAMPLECO"), [Upload()])
    assert files == {"historical_financials": tmp_path / "historical_financials.xlsx"}
    assert unmatched == []


def test_calculate_reports_ignored_uploads_and_refreshes_the_table(pipeline, spreadsheet, tmp_path):
    stray = make_pdf(tmp_path / "unrelated.pdf", "x")
    dashboard = Dashboard(pipeline)
    log, table = dashboard.calculate("EXAMPLECO", [spreadsheet, stray])
    assert "Ignored uploads that match no declared key: unrelated.pdf" in log
    assert "Extraction 'total_assets' failed" in log
    assert "us_gmv" in log
    assert table.set_index("Metric").loc["US GMV", "Value"] == "23"


def test_upload_hint_lists_declared_keys(pipeline):
    hint = Dashboard(pipeline).upload_hint("EXAMPLECO")
    assert "`historical_financials`" in hint and "`annual_presentation`" in hint


def test_app_builds_with_gradio(pipeline):
    gradio = pytest.importorskip("gradio")
    from bnpl_metrics.apps.dashboard import build_app

    app = build_app(Dashboard(pipeline))
    assert isinstance(app, gradio.Blocks)
