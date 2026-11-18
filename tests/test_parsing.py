import pytest

from bnpl_metrics.extraction.parsing import (
    ParseError,
    extract_json,
    parse_amount,
    parse_percentage,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("$5.6 billion", (5.6, "billion")),
        ("$1,234.5M", (1234.5, "million")),
        ("revenue of 38.3bn in the quarter", (38.3, "billion")),
        ("(1,234)", (-1234.0, None)),
        ("-12", (-12.0, None)),
        ("5.6 K", (5.6, "thousand")),
        ("42", (42.0, None)),
    ],
)
def test_parse_amount(text, expected):
    assert parse_amount(text) == expected


def test_parse_amount_does_not_read_words_as_suffix():
    assert parse_amount("5 months")[1] is None
    assert parse_amount("7 bought")[1] is None


def test_parse_amount_requires_a_number():
    with pytest.raises(ParseError):
        parse_amount("no figures here")


def test_parse_percentage():
    assert parse_percentage("US share is 61%") == pytest.approx(0.61)
    assert parse_percentage("about 7.5 percent") == pytest.approx(0.075)


def test_parse_percentage_never_defaults():
    with pytest.raises(ParseError):
        parse_percentage("the share was large")
    with pytest.raises(ParseError):
        parse_percentage("0.61")


def test_parse_percentage_decimal_only_when_allowed():
    assert parse_percentage("ratio: 0.61", allow_decimal=True) == pytest.approx(0.61)


def test_extract_json_handles_fences_and_prose():
    text = 'Sure!\n```json\n{"value": 5.6, "unit": "billion USD", "note": "a } b"}\n```'
    assert extract_json(text) == {"value": 5.6, "unit": "billion USD", "note": "a } b"}


@pytest.mark.parametrize("bad", ["no json", '{"a": 1', "{bad json}"])
def test_extract_json_failures(bad):
    with pytest.raises(ParseError):
        extract_json(bad)
