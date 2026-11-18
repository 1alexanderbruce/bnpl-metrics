import math

import pytest

from bnpl_metrics.calculation import formulas as f


def test_sum_last_uses_most_recent_periods():
    assert f.sum_last([1, 2, 3, 4, 5], 4) == 14


def test_sum_last_rejects_short_window():
    with pytest.raises(ValueError):
        f.sum_last([1, 2, 3], 4)


def test_missing_values_are_skipped_not_counted():
    assert f.sum_last([1, None, 2, math.nan, 3, 4], 4) == 10


def test_latest_ignores_trailing_missing():
    assert f.latest([1.0, 2.0, None]) == 2.0
    with pytest.raises(ValueError):
        f.latest([None, math.nan])


def test_annualize_and_per_period_round_trip():
    assert f.per_period(f.annualize(3.0)) == pytest.approx(3.0)


def test_apply_share_bounds():
    assert f.apply_share(200, 0.25) == 50
    with pytest.raises(ValueError):
        f.apply_share(200, 25)


def test_share_of_is_a_percentage():
    assert f.share_of(1, 4) == 25
    with pytest.raises(ZeroDivisionError):
        f.share_of(1, 0)


def test_fx_convert():
    assert f.fx_convert(10, 0.5) == 5
    with pytest.raises(ValueError):
        f.fx_convert(10, 0)
