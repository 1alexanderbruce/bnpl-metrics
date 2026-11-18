import pytest

from bnpl_metrics.calculation.expr import FormulaError, evaluate, formula_names


def test_basic_arithmetic():
    assert evaluate("a * b / 2 + 1", {"a": 4, "b": 3}) == 7


def test_functions_and_unary():
    assert evaluate("max(a, b) - min(a, b)", {"a": 2, "b": 9}) == 7
    assert evaluate("-a + abs(b)", {"a": 1, "b": -5}) == 4
    assert evaluate("sum(a, b, 1)", {"a": 1, "b": 2}) == 4


def test_formula_names_excludes_functions():
    assert formula_names("max(a, b) * c") == {"a", "b", "c"}


def test_unknown_input_rejected():
    with pytest.raises(FormulaError):
        evaluate("a * missing", {"a": 1})


def test_division_by_zero_is_a_formula_error():
    with pytest.raises(FormulaError):
        evaluate("a / b", {"a": 1, "b": 0})


@pytest.mark.parametrize(
    "bad",
    [
        "__import__('os').system('true')",
        "a.real",
        "a[0]",
        "lambda: 1",
        "[a, a]",
        "'text'",
        "a if a else 1",
        "open('x')",
        "a ** 1000",
        "a and b",
        "a < b",
    ],
)
def test_unsafe_or_unsupported_syntax_rejected(bad):
    with pytest.raises(FormulaError):
        evaluate(bad, {"a": 2, "b": 3})


def test_syntax_error_reported():
    with pytest.raises(FormulaError):
        evaluate("a *", {"a": 1})
