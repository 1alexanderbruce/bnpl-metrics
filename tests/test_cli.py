import csv
from pathlib import Path

import pytest
from openpyxl import Workbook

from bnpl_metrics import cli

EXAMPLE = str(Path(__file__).resolve().parents[1] / "config" / "example.yaml")


@pytest.fixture
def cache_env(tmp_path, monkeypatch):
    monkeypatch.setenv("BNPL_METRICS_CACHE_DIR", str(tmp_path / "cache"))
    return tmp_path


@pytest.fixture
def spreadsheet(tmp_path):
    book = Workbook()
    sheet = book.active
    sheet.title = "Operating Metrics"
    sheet.append(["Metric", "Q1", "Q2", "Q3", "Q4"])
    sheet.append(["Gross merchandise volume", 10, 11, 12, 13])
    path = tmp_path / "financials.xlsx"
    book.save(path)
    return path


def test_validate_config(capsys):
    assert cli.main(["validate-config", "--config", EXAMPLE]) == 0
    assert "EXAMPLECO: 6 metrics" in capsys.readouterr().out


def test_invalid_config_is_a_clean_error(tmp_path, capsys):
    bad = tmp_path / "bad.yaml"
    bad.write_text("companies: []")
    assert cli.main(["validate-config", "--config", str(bad)]) == 1
    assert "error:" in capsys.readouterr().err


def test_calculate_without_credentials_degrades_and_exits_nonzero(cache_env, spreadsheet, capsys):
    code = cli.main(
        [
            "calculate",
            "--config",
            EXAMPLE,
            "--company",
            "EXAMPLECO",
            "--file",
            f"historical_financials={spreadsheet}",
        ]
    )
    out = capsys.readouterr().out
    assert code == 1  # some metrics cannot be computed without an LLM or EDGAR
    assert "us_gmv" in out and "fallback" in out
    assert "failed" in out and "no file supplied" in out


def test_show_export_and_stale_flag_after_calculate(cache_env, spreadsheet, capsys, tmp_path):
    cli.main(["calculate", "--config", EXAMPLE, "--file", f"historical_financials={spreadsheet}"])
    capsys.readouterr()
    assert cli.main(["show"]) == 0
    shown = capsys.readouterr().out
    assert "EXAMPLECO" in shown and "us_gmv" in shown

    out = tmp_path / "results.csv"
    assert cli.main(["export", "--output", str(out)]) == 0
    rows = {r["metric"]: r for r in csv.DictReader(out.open())}
    assert float(rows["us_gmv"]["value"]) == pytest.approx(23.0)  # 46 x 0.5 fallback share
    assert rows["us_gmv"]["status"] == "fallback"
    assert rows["active_consumers"]["value"] == ""


def test_show_with_no_results(cache_env, capsys):
    assert cli.main(["show"]) == 0
    assert "no results yet" in capsys.readouterr().out


def test_no_save_does_not_write(cache_env, spreadsheet):
    cli.main(["calculate", "--config", EXAMPLE, "--no-save"])
    assert not (cache_env / "cache" / "results.json").exists()


def test_bad_file_argument_and_unknown_company(cache_env, capsys):
    assert cli.main(["calculate", "--config", EXAMPLE, "--file", "oops"]) == 1
    assert "KEY=PATH" in capsys.readouterr().err
    assert cli.main(["calculate", "--config", EXAMPLE, "--company", "NOPE"]) == 1
    assert "unknown company" in capsys.readouterr().err


def test_extract_without_llm_reports_failure(cache_env, capsys):
    code = cli.main(["extract", "--config", EXAMPLE, "--company", "EXAMPLECO"])
    assert code == 1
    assert "FAILED" in capsys.readouterr().out


def test_help_lists_all_commands(capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--help"])
    assert exit_info.value.code == 0
    text = capsys.readouterr().out
    for command in ("validate-config", "calculate", "extract", "show", "export", "dashboard"):
        assert command in text
