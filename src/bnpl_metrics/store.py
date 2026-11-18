"""Persistent results.

Every metric keeps its ``latest`` result (whatever its status) and its ``last_good`` result
(status ok or fallback). A failed or flagged refresh therefore never overwrites a good value,
and readers can tell when the value they see is stale.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from bnpl_metrics.fileio import atomic_write_json, read_json
from bnpl_metrics.models import MetricResult

SCHEMA_VERSION = 1
HISTORY_LIMIT = 24


@dataclass(frozen=True)
class DisplayValue:
    value: float | None
    unit: str
    status: str
    stale: bool
    as_of: str | None
    note: str = ""


class ResultStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._data: dict = read_json(
            self.path, {"schema_version": SCHEMA_VERSION, "updated_at": None, "companies": {}}
        )
        if self._data.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"{self.path}: unsupported schema version")

    def update(self, company_name: str, result: MetricResult) -> None:
        company = self._data["companies"].setdefault(
            result.company_id, {"name": company_name, "metrics": {}}
        )
        company["name"] = company_name
        entry = company["metrics"].setdefault(
            result.metric, {"latest": None, "last_good": None, "history": []}
        )
        entry["latest"] = result.to_dict()
        if result.usable:
            entry["last_good"] = result.to_dict()
            entry["history"].append(
                {
                    "value": result.value,
                    "computed_at": result.computed_at,
                    "status": result.status.value,
                }
            )
            entry["history"] = entry["history"][-HISTORY_LIMIT:]
        self._data["updated_at"] = result.computed_at

    def previous(self, company_id: str) -> dict[str, MetricResult]:
        """Last good result per metric, for change checks in the next run."""
        metrics = self._data["companies"].get(company_id, {}).get("metrics", {})
        return {
            name: MetricResult.from_dict(entry["last_good"])
            for name, entry in metrics.items()
            if entry.get("last_good")
        }

    def latest(self, company_id: str, metric: str) -> MetricResult | None:
        entry = self._data["companies"].get(company_id, {}).get("metrics", {}).get(metric)
        return MetricResult.from_dict(entry["latest"]) if entry and entry["latest"] else None

    def history(self, company_id: str, metric: str) -> list[dict]:
        entry = self._data["companies"].get(company_id, {}).get("metrics", {}).get(metric)
        return list(entry["history"]) if entry else []

    def company_ids(self) -> list[str]:
        return sorted(self._data["companies"])

    def company_name(self, company_id: str) -> str:
        return self._data["companies"][company_id]["name"]

    def metric_names(self, company_id: str) -> list[str]:
        return list(self._data["companies"].get(company_id, {}).get("metrics", {}))

    def display(self, company_id: str, metric: str) -> DisplayValue | None:
        """What a reader should see: the latest value if it is good, else the last good value
        flagged as stale, else nothing."""
        entry = self._data["companies"].get(company_id, {}).get("metrics", {}).get(metric)
        if not entry or not entry["latest"]:
            return None
        latest = MetricResult.from_dict(entry["latest"])
        if latest.usable:
            return DisplayValue(
                latest.value, latest.unit, latest.status.value, False, latest.computed_at,
                "; ".join(latest.warnings),
            )  # fmt: skip
        reason = latest.error or "; ".join(latest.warnings) or latest.status.value
        note = f"latest run {latest.status.value}: {reason}"
        if entry["last_good"]:
            good = MetricResult.from_dict(entry["last_good"])
            return DisplayValue(
                good.value, good.unit, good.status.value, True, good.computed_at, note
            )
        return DisplayValue(None, latest.unit, latest.status.value, True, latest.computed_at, note)

    def save(self) -> None:
        atomic_write_json(self.path, self._data)

    def export_csv(self, path: str | Path) -> None:
        header = ["company", "metric", "value", "unit", "status", "stale", "as_of", "note"]
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(header)
            for company_id in self.company_ids():
                for metric in self.metric_names(company_id):
                    shown = self.display(company_id, metric)
                    if shown is None:
                        continue
                    value = "" if shown.value is None else shown.value
                    writer.writerow(
                        [company_id, metric, value, shown.unit, shown.status, shown.stale,
                         shown.as_of, shown.note]
                    )  # fmt: skip
