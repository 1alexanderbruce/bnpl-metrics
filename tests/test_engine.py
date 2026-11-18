import pytest

from bnpl_metrics.calculation.engine import Engine, InputError
from bnpl_metrics.models import ExtractedValue, MetricResult, Status
from bnpl_metrics.specs import parse_specs


def src(type_, **params):
    return {"type": type_, **params}


def metric(name, formula, inputs, unit="billion USD", **extra):
    return {"name": name, "unit": unit, "formula": formula, "inputs": inputs, **extra}


def inp(*sources, unit="billion USD"):
    return {"unit": unit, "sources": list(sources)}


def company(*metrics):
    return parse_specs({"companies": [{"id": "CO", "metrics": list(metrics)}]})[0]


class FakeResolvers(dict):
    """Resolvers keyed by source type, driven by a table of canned answers."""

    def __init__(self, **answers):
        super().__init__()
        self.answers = answers
        for source_type in ("excel", "filing", "pdf", "catalog"):
            self[source_type] = self.resolve
        self["constant"] = lambda source, spec: ExtractedValue(
            value=source.params["value"], source="constant"
        )

    def resolve(self, source, spec):
        key = source.params.get("key") or source.params.get("row") or source.params.get("ref")
        answer = self.answers.get(key)
        if isinstance(answer, Exception):
            raise answer
        if answer is None:
            raise InputError(f"nothing for {key!r}")
        if isinstance(answer, ExtractedValue):
            return answer
        return ExtractedValue(value=answer, source=f"fake:{key}")


def run(co, resolvers=None, **kwargs):
    return Engine(resolvers or FakeResolvers(), **kwargs).calculate_company(co)


def test_basic_calculation_with_unit_conversion():
    co = company(
        metric(
            "us_gmv",
            "gmv * share",
            {
                "gmv": inp(src("excel", file="f", sheet="s", row="gmv")),
                "share": inp(src("constant", value=0.5), unit="ratio"),
            },
        )
    )
    answers = FakeResolvers(gmv=ExtractedValue(40_000, unit="million USD"))
    result = run(co, answers)["us_gmv"]
    assert result.status is Status.OK
    assert result.value == pytest.approx(20.0)
    assert result.inputs["gmv"].value == pytest.approx(40.0)
    assert not result.warnings


def test_fallback_is_reported_not_hidden():
    co = company(
        metric(
            "m",
            "x",
            {
                "x": inp(
                    src("filing", forms=["10-K"], question="q", key="primary"),
                    src("constant", value=3, fallback=True, reason="placeholder"),
                )
            },
        )
    )
    resolvers = FakeResolvers(primary=InputError("filing not found"))
    result = run(co, resolvers)["m"]
    assert result.status is Status.FALLBACK
    assert result.value == 3
    assert result.inputs["x"].used_fallback
    assert any("fallback" in w for w in result.warnings)
    assert any("filing not found" in w for w in result.warnings)


def test_all_sources_failing_gives_failed_result_with_reason():
    co = company(metric("m", "x", {"x": inp(src("excel", file="f", sheet="s", row="missing"))}))
    result = run(co)["m"]
    assert result.status is Status.FAILED
    assert result.value is None
    assert "input 'x'" in result.error
    assert not result.usable


def test_incompatible_unit_falls_through_to_next_source():
    co = company(
        metric(
            "m",
            "x",
            {
                "x": inp(
                    src("excel", file="f", sheet="s", row="a"),
                    src("excel", file="f", sheet="s", row="b"),
                )
            },
        )
    )
    resolvers = FakeResolvers(a=ExtractedValue(5, unit="million consumers"), b=7)
    result = run(co, resolvers)["m"]
    assert result.status is Status.OK
    assert result.value == 7
    assert any("source failed" in w for w in result.warnings)


def test_missing_resolver_is_a_clear_failure():
    co = company(metric("m", "x", {"x": inp(src("pdf", file="f", question="q"))}))
    result = Engine({}).calculate_company(co)["m"]
    assert result.status is Status.FAILED
    assert "no resolver" in result.error


def two_step():
    return company(
        metric(
            "base",
            "x",
            {
                "x": inp(
                    src("excel", file="f", sheet="s", row="base"),
                    src("constant", value=4, fallback=True, reason="placeholder"),
                )
            },
            validation={"max": 100},
        ),
        metric("derived", "b * 2", {"b": inp(src("metric", ref="base"))}),
    )


def test_dependency_value_flows_through():
    results = run(two_step(), FakeResolvers(base=10))
    assert results["derived"].value == 20
    assert results["derived"].status is Status.OK


def test_fallback_propagates_to_dependents():
    results = run(two_step(), FakeResolvers())
    assert results["base"].status is Status.FALLBACK
    assert results["derived"].status is Status.FALLBACK


def test_failed_dependency_fails_dependents():
    co = company(
        metric("base", "x", {"x": inp(src("excel", file="f", sheet="s", row="nope"))}),
        metric("derived", "b", {"b": inp(src("metric", ref="base"))}),
    )
    results = run(co)
    assert results["derived"].status is Status.FAILED
    assert "dependency" in results["derived"].error


def test_out_of_range_needs_review_and_blocks_dependents():
    results = run(two_step(), FakeResolvers(base=1000))
    assert results["base"].status is Status.NEEDS_REVIEW
    assert results["base"].value == 1000
    assert any("above the expected maximum" in w for w in results["base"].warnings)
    assert results["derived"].status is Status.FAILED


def test_large_change_versus_previous_needs_review():
    co = company(
        metric(
            "m",
            "x",
            {"x": inp(src("excel", file="f", sheet="s", row="v"))},
            validation={"max_change_ratio": 0.5},
        )
    )
    previous = {
        "m": MetricResult(
            company_id="CO", metric="m", display_name="m", unit="billion USD",
            status=Status.OK, value=10.0,
        )
    }  # fmt: skip
    engine = Engine(FakeResolvers(v=30))
    result = engine.calculate_company(co, previous=previous)["m"]
    assert result.status is Status.NEEDS_REVIEW
    assert any("differs from the previous" in w for w in result.warnings)


def test_failed_previous_result_is_not_used_for_comparison():
    co = company(
        metric(
            "m",
            "x",
            {"x": inp(src("excel", file="f", sheet="s", row="v"))},
            validation={"max_change_ratio": 0.1},
        )
    )
    previous = {
        "m": MetricResult(
            company_id="CO", metric="m", display_name="m", unit="billion USD",
            status=Status.FAILED, value=1.0,
        )
    }  # fmt: skip
    result = Engine(FakeResolvers(v=30)).calculate_company(co, previous=previous)["m"]
    assert result.status is Status.OK


def test_division_by_zero_fails_cleanly():
    co = company(
        metric(
            "m",
            "a / b",
            {
                "a": inp(src("excel", file="f", sheet="s", row="a")),
                "b": inp(src("excel", file="f", sheet="s", row="b")),
            },
        )
    )
    result = run(co, FakeResolvers(a=1, b=0))["m"]
    assert result.status is Status.FAILED
    assert "division by zero" in result.error


def test_custom_validator_can_flag_results():
    co = company(metric("m", "x", {"x": inp(src("excel", file="f", sheet="s", row="v"))}))
    flag = lambda spec, result, previous: ["looks odd"]  # noqa: E731
    result = run(co, FakeResolvers(v=1), validators=[flag])["m"]
    assert result.status is Status.NEEDS_REVIEW
    assert "looks odd" in result.warnings


def test_only_runs_requested_metrics_and_their_dependencies():
    co = company(
        metric("a", "x", {"x": inp(src("excel", file="f", sheet="s", row="a"))}),
        metric("b", "a_", {"a_": inp(src("metric", ref="a"))}),
        metric("c", "y", {"y": inp(src("excel", file="f", sheet="s", row="c"))}),
    )
    results = Engine(FakeResolvers(a=1, c=2)).calculate_company(co, only={"b"})
    assert set(results) == {"a", "b"}


def test_result_round_trips_through_dict():
    results = run(two_step(), FakeResolvers(base=10))
    original = results["derived"]
    restored = MetricResult.from_dict(original.to_dict())
    assert restored == original
