"""Read figures from an uploaded spreadsheet."""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from bnpl_metrics.calculation import formulas
from bnpl_metrics.errors import InputError

AGGREGATES = {"latest", "sum_last_n", "mean_last_n"}
_LABEL_COLUMNS = 3


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", str(text)).strip().lower().rstrip(":")


def _to_number(value: object) -> float | None:
    if value is None or (isinstance(value, float) and value != value):
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = re.sub(r"[$,\s]", "", str(value))
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    cleaned = cleaned.strip("()")
    try:
        number = float(cleaned)
    except ValueError:
        return None
    return -number if negative else number


class Workbook:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if not self.path.is_file():
            raise InputError(f"spreadsheet not found: {self.path}")
        try:
            self._sheets: dict[str, pd.DataFrame] = pd.read_excel(
                self.path, sheet_name=None, header=None
            )
        except Exception as exc:
            raise InputError(f"could not read spreadsheet {self.path.name}: {exc}") from exc

    @property
    def sheet_names(self) -> list[str]:
        return list(self._sheets)

    def frame(self, sheet: str) -> pd.DataFrame:
        wanted = _squash(sheet)
        exact = [n for n in self._sheets if _squash(n) == wanted]
        if exact:
            return self._sheets[exact[0]]
        partial = [n for n in self._sheets if wanted in _squash(n)]
        if len(partial) == 1:
            return self._sheets[partial[0]]
        problem = "is ambiguous" if partial else "was not found"
        raise InputError(f"sheet '{sheet}' {problem}; available: {self.sheet_names}")

    def row_values(self, sheet: str, label: str) -> list[float | None]:
        """Numeric cells of the row labelled ``label``, in column order.

        The label must identify exactly one row: an exact match wins, otherwise a unique
        substring match is accepted. Ambiguity is an error, never a guess.
        """
        frame = self.frame(sheet)
        wanted = _squash(label)
        exact: list[tuple[int, int]] = []
        partial: list[tuple[int, int]] = []
        for index in range(len(frame)):
            for column in range(min(_LABEL_COLUMNS, frame.shape[1])):
                cell = frame.iat[index, column]
                if not isinstance(cell, str):
                    continue
                text = _squash(cell)
                if text == wanted:
                    exact.append((index, column))
                    break
                if wanted in text:
                    partial.append((index, column))
                    break
        matches = exact or partial
        if not matches:
            raise InputError(f"row '{label}' not found in sheet '{sheet}'")
        if len(matches) > 1:
            raise InputError(f"row '{label}' matches {len(matches)} rows in sheet '{sheet}'")
        row_index, label_column = matches[0]
        return [_to_number(c) for c in frame.iloc[row_index, label_column + 1 :]]

    def aggregate(self, sheet: str, label: str, how: str = "latest", n: int = 4) -> float:
        if how not in AGGREGATES:
            raise InputError(f"unknown aggregate '{how}'; use one of {sorted(AGGREGATES)}")
        values = self.row_values(sheet, label)
        try:
            if how == "latest":
                return formulas.latest(values)
            if how == "sum_last_n":
                return formulas.sum_last(values, n)
            return formulas.mean_last(values, n)
        except ValueError as exc:
            raise InputError(f"row '{label}' in '{sheet}': {exc}") from exc

    def to_markdown(self, sheet: str, max_rows: int = 80) -> str:
        frame = self.frame(sheet).dropna(how="all").dropna(axis=1, how="all")
        frame = frame.head(max_rows).fillna("")
        lines = [" | ".join(str(c) for c in row) for row in frame.itertuples(index=False)]
        return "\n".join(lines)
