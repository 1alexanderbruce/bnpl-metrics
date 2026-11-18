from pathlib import Path

import pytest

from bnpl_metrics.specs import SpecError, load_specs, parse_specs

EXAMPLE = Path(__file__).resolve().parents[1] / "config" / "example.yaml"


def minimal(**overrides):
    metric = {
        "name": "m",
        "unit": "billion USD",
        "formula": "a * 2",
        "inputs": {"a": {"unit": "billion USD", "sources": [{"type": "constant", "value": 1}]}},
    }
    metric.update(overrides)
    return {"companies": [{"id": "C", "metrics": [metric]}]}


def test_example_config_is_valid():
    companies = load_specs(EXAMPLE)
    assert [c.id for c in companies] == ["EXAMPLECO"]
    assert len(companies[0].metrics) >= 5


def test_dependency_order():
    company = load_specs(EXAMPLE)[0]
    order = [m.name for m in company.ordered_metrics()]
    assert order.index("us_gmv") < order.index("pay_in_4_gmv") < order.index("pay_in_4_share")
    assert order.index("us_share_of_revenue") < order.index("active_consumers")


def test_formula_must_use_declared_inputs():
    with pytest.raises(SpecError, match="undefined inputs"):
        parse_specs(minimal(formula="a * missing"))


def test_unknown_source_type_rejected():
    bad = minimal()
    bad["companies"][0]["metrics"][0]["inputs"]["a"]["sources"] = [{"type": "magic"}]
    with pytest.raises(SpecError, match="unknown source type"):
        parse_specs(bad)


def test_source_requires_its_parameters():
    bad = minimal()
    bad["companies"][0]["metrics"][0]["inputs"]["a"]["sources"] = [{"type": "excel", "file": "f"}]
    with pytest.raises(SpecError, match="needs 'sheet'"):
        parse_specs(bad)


def test_fallback_requires_reason():
    bad = minimal()
    bad["companies"][0]["metrics"][0]["inputs"]["a"]["sources"] = [
        {"type": "constant", "value": 1, "fallback": True}
    ]
    with pytest.raises(SpecError, match="reason"):
        parse_specs(bad)


def test_invalid_unit_rejected():
    with pytest.raises(SpecError):
        parse_specs(minimal(unit=" "))


def metric_ref_metric(name, formula_input, ref):
    source = {"type": "metric", "ref": ref}
    return {
        "name": name,
        "unit": "ratio",
        "formula": formula_input,
        "inputs": {formula_input: {"unit": "ratio", "sources": [source]}},
    }


def test_circular_dependencies_rejected():
    metrics = [metric_ref_metric("x", "y", "z"), metric_ref_metric("z", "w", "x")]
    with pytest.raises(SpecError, match="circular"):
        parse_specs({"companies": [{"id": "C", "metrics": metrics}]})


def test_unknown_metric_reference_rejected():
    bad = minimal()
    bad["companies"][0]["metrics"][0]["inputs"]["a"]["sources"] = [
        {"type": "metric", "ref": "nope"}
    ]
    with pytest.raises(SpecError, match="unknown metric refs"):
        parse_specs(bad)


def test_empty_file_rejected():
    with pytest.raises(SpecError):
        parse_specs({})
