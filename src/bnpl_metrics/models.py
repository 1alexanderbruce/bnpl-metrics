"""Typed results shared by the engine, cache, CLI and dashboard."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class Status(str, Enum):
    OK = "ok"
    FALLBACK = "fallback"  # computed, but at least one input came from a declared fallback
    NEEDS_REVIEW = "needs_review"  # computed, but validation flagged it
    FAILED = "failed"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class ExtractedValue:
    value: float | None
    unit: str | None = None
    source: str = ""
    confidence: str | None = None
    evidence: str | None = None
    used_fallback: bool = False
    note: str | None = None


@dataclass
class Step:
    description: str
    value: float | None = None
    detail: str | None = None
    source: str | None = None


@dataclass
class MetricResult:
    company_id: str
    metric: str
    display_name: str
    unit: str
    status: Status
    value: float | None = None
    steps: list[Step] = field(default_factory=list)
    inputs: dict[str, ExtractedValue] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    computed_at: str = field(default_factory=utc_now_iso)

    @property
    def usable(self) -> bool:
        """True when a value was produced that is safe to display as current."""
        return self.status in (Status.OK, Status.FALLBACK) and self.value is not None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MetricResult:
        return cls(
            company_id=data["company_id"],
            metric=data["metric"],
            display_name=data.get("display_name", data["metric"]),
            unit=data.get("unit", ""),
            status=Status(data["status"]),
            value=data.get("value"),
            steps=[Step(**s) for s in data.get("steps", [])],
            inputs={k: ExtractedValue(**v) for k, v in data.get("inputs", {}).items()},
            warnings=list(data.get("warnings", [])),
            error=data.get("error"),
            computed_at=data.get("computed_at", utc_now_iso()),
        )
