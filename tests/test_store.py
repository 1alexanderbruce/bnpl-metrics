import csv
import json

import pytest

from bnpl_metrics.models import MetricResult, Status
from bnpl_metrics.store import HISTORY_LIMIT, ResultStore


def result(status, value=1.0, error=None, warnings=(), metric="m"):
    return MetricResult(
        company_id="CO",
        metric=metric,
        display_name=metric,
        unit="billion USD",
        status=status,
        value=value,
        error=error,
        warnings=list(warnings),
    )


def test_good_result_is_displayed_fresh(tmp_path):
    store = ResultStore(tmp_path / "r.json")
    store.update("Company", result(Status.OK, 5.0))
    shown = store.display("CO", "m")
    assert (shown.value, shown.stale, shown.status) == (5.0, False, "ok")


def test_fallback_result_is_usable_but_labelled(tmp_path):
    store = ResultStore(tmp_path / "r.json")
    store.update("Company", result(Status.FALLBACK, 5.0, warnings=["used fallback"]))
    shown = store.display("CO", "m")
    assert shown.status == "fallback" and not shown.stale and "used fallback" in shown.note


def test_failed_refresh_keeps_last_good_value_and_marks_it_stale(tmp_path):
    store = ResultStore(tmp_path / "r.json")
    store.update("Company", result(Status.OK, 5.0))
    store.update("Company", result(Status.FAILED, None, error="input 'x': all sources failed"))
    shown = store.display("CO", "m")
    assert shown.value == 5.0 and shown.stale
    assert "latest run failed" in shown.note and "all sources failed" in shown.note
    assert store.latest("CO", "m").status is Status.FAILED
    assert store.previous("CO")["m"].value == 5.0


def test_needs_review_never_replaces_last_good(tmp_path):
    store = ResultStore(tmp_path / "r.json")
    store.update("Company", result(Status.OK, 5.0))
    store.update("Company", result(Status.NEEDS_REVIEW, 500.0, warnings=["above the maximum"]))
    shown = store.display("CO", "m")
    assert shown.value == 5.0 and shown.stale
    assert store.previous("CO")["m"].value == 5.0


def test_failure_with_no_history_displays_no_value(tmp_path):
    store = ResultStore(tmp_path / "r.json")
    store.update("Company", result(Status.FAILED, None, error="boom"))
    shown = store.display("CO", "m")
    assert shown.value is None and shown.stale and "boom" in shown.note
    assert store.previous("CO") == {}
    assert store.display("CO", "unknown") is None


def test_history_is_capped_and_only_records_usable_results(tmp_path):
    store = ResultStore(tmp_path / "r.json")
    for i in range(HISTORY_LIMIT + 5):
        store.update("Company", result(Status.OK, float(i)))
    store.update("Company", result(Status.FAILED, None, error="x"))
    history = store.history("CO", "m")
    assert len(history) == HISTORY_LIMIT
    assert history[-1]["value"] == HISTORY_LIMIT + 4


def test_round_trip_persistence(tmp_path):
    path = tmp_path / "r.json"
    store = ResultStore(path)
    store.update("Company", result(Status.OK, 5.0))
    store.save()
    reloaded = ResultStore(path)
    assert reloaded.company_ids() == ["CO"]
    assert reloaded.company_name("CO") == "Company"
    assert reloaded.display("CO", "m").value == 5.0
    assert [p.name for p in tmp_path.iterdir()] == ["r.json"]


def test_unsupported_schema_is_rejected_and_corrupt_file_raises(tmp_path):
    path = tmp_path / "r.json"
    path.write_text(json.dumps({"schema_version": 99, "companies": {}}))
    with pytest.raises(ValueError, match="schema"):
        ResultStore(path)
    path.write_text("{broken")
    with pytest.raises(json.JSONDecodeError):
        ResultStore(path)


def test_csv_export(tmp_path):
    store = ResultStore(tmp_path / "r.json")
    store.update("Company", result(Status.OK, 5.0, metric="a"))
    store.update("Company", result(Status.FAILED, None, error="boom", metric="b"))
    out = tmp_path / "out.csv"
    store.export_csv(out)
    rows = list(csv.DictReader(out.open()))
    assert {r["metric"]: (r["value"], r["stale"]) for r in rows} == {
        "a": ("5.0", "False"),
        "b": ("", "True"),
    }
