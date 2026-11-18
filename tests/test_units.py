import pytest

from bnpl_metrics.calculation.units import UnitError, convert, parse_unit


def test_magnitude_conversion():
    assert convert(5600, "million USD", "billion USD") == pytest.approx(5.6)
    assert convert(2.5, "billion USD", "million USD") == pytest.approx(2500)


def test_dollar_sign_and_filler_words():
    assert convert(1, "$ billion", "million USD") == pytest.approx(1000)
    assert parse_unit("billion US dollars").kind == "money:usd"


def test_percent_and_ratio():
    assert convert(42, "percent", "ratio") == pytest.approx(0.42)
    assert convert(0.25, "ratio", "%") == pytest.approx(25)


def test_counts():
    assert convert(23_200_000, "count", "million") == pytest.approx(23.2)
    assert convert(4.4, "million consumers", "thousand") == pytest.approx(4400)


def test_different_currencies_are_not_convertible():
    with pytest.raises(UnitError):
        convert(1, "billion AUD", "billion USD")


def test_money_and_count_are_not_convertible():
    with pytest.raises(UnitError):
        convert(1, "million", "million USD")


def test_empty_unit_rejected():
    with pytest.raises(UnitError):
        parse_unit("  ")


def test_two_magnitudes_rejected():
    with pytest.raises(UnitError):
        parse_unit("million billion USD")
