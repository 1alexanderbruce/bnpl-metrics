"""Safe arithmetic evaluator for metric formulas.

Formulas are written in YAML, e.g. ``"global_gmv * us_share"``. Only arithmetic, numeric
literals, named inputs and a few allow-listed functions are accepted; ``eval`` is never used.
"""

from __future__ import annotations

import ast
import operator
from collections.abc import Callable, Mapping
from typing import Any


class FormulaError(ValueError):
    """Raised for invalid formulas or evaluation failures."""


def _sum(*args: float) -> float:
    return float(sum(args))


_BINARY: dict[type, Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
}
_UNARY: dict[type, Callable[[Any], Any]] = {ast.USub: operator.neg, ast.UAdd: operator.pos}
FUNCTIONS: dict[str, Callable[..., Any]] = {
    "min": min,
    "max": max,
    "abs": abs,
    "round": round,
    "sum": _sum,
}
_MAX_EXPONENT = 100


def _parse(formula: str) -> ast.Expression:
    try:
        tree = ast.parse(formula.strip(), mode="eval")
    except SyntaxError as exc:
        raise FormulaError(f"invalid formula {formula!r}: {exc.msg}") from exc
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in FUNCTIONS:
                raise FormulaError(f"function not allowed in formula {formula!r}")
            if node.keywords:
                raise FormulaError("keyword arguments are not allowed in formulas")
        elif isinstance(node, ast.BinOp):
            if type(node.op) not in _BINARY:
                raise FormulaError(f"operator not allowed in formula {formula!r}")
        elif isinstance(node, ast.UnaryOp):
            if type(node.op) not in _UNARY:
                raise FormulaError(f"operator not allowed in formula {formula!r}")
        elif isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                raise FormulaError(f"only numeric literals are allowed in {formula!r}")
        elif not isinstance(node, (ast.Expression, ast.Name, ast.Load, *_BINARY, *_UNARY)):
            raise FormulaError(f"unsupported syntax in formula {formula!r}")
    return tree


def formula_names(formula: str) -> set[str]:
    """Names of the inputs a formula refers to (function names excluded)."""
    tree = _parse(formula)
    called = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call)}
    return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} - called


def evaluate(formula: str, variables: Mapping[str, float]) -> float:
    tree = _parse(formula)

    def visit(node: ast.AST) -> Any:
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if node.id not in variables:
                raise FormulaError(f"unknown input {node.id!r} in formula {formula!r}")
            return variables[node.id]
        if isinstance(node, ast.UnaryOp):
            return _UNARY[type(node.op)](visit(node.operand))
        if isinstance(node, ast.BinOp):
            left, right = visit(node.left), visit(node.right)
            if isinstance(node.op, ast.Pow) and abs(right) > _MAX_EXPONENT:
                raise FormulaError("exponent too large")
            try:
                return _BINARY[type(node.op)](left, right)
            except ZeroDivisionError as exc:
                raise FormulaError(f"division by zero in formula {formula!r}") from exc
        if isinstance(node, ast.Call):
            assert isinstance(node.func, ast.Name)
            return FUNCTIONS[node.func.id](*(visit(arg) for arg in node.args))
        raise FormulaError(f"unsupported syntax in formula {formula!r}")

    result = visit(tree)
    if not isinstance(result, (int, float)):
        raise FormulaError(f"formula {formula!r} did not produce a number")
    return float(result)
