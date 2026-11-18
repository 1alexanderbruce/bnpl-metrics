import json

import pytest

from bnpl_metrics import cli
from bnpl_metrics.calculation.engine import Engine
from bnpl_metrics.models import ExtractedValue, MetricResult, Status
from bnpl_metrics.specs import parse_specs
from bnpl_metrics.validation import LLMValidator, ValidatorUnavailable


class Reply:
    def __init__(self, reply=None, error=None):
        self.reply = reply
        self.error = error
        self.prompts = []

    def complete(self, system, user, *, temperature=0.0):
        self.prompts.append(user)
        if self.error:
            raise self.error
        return self.reply if isinstance(self.reply, str) else json.dumps(self.reply)

    def embed(self, texts):
        raise NotImplementedError


def company():
    return parse_specs(
        {
            "companies": [
                {
                    "id": "CO",
                    "metrics": [
                        {
                            "name": "m",
                            "unit": "billion USD",
                            "formula": "x * 2",
                            "description": "Twice x",
                            "inputs": {
                                "x": {
                                    "unit": "billion USD",
                                    "sources": [{"type": "constant", "value": 3}],
                                }
                            },
                        }
                    ],
                }
            ]
        }
    )[0]


def resolvers():
    return {"constant": lambda s, i: ExtractedValue(float(s.params["value"]), source="constant")}


def run(validator):
    spec = company()
    return Engine(resolvers(), validators=[validator]).calculate_company(spec)["m"]


def test_accept_adds_no_concerns():
    llm = Reply({"recommended_action": "accept", "concerns": []})
    result = run(LLMValidator(llm))
    assert result.status is Status.OK and not result.warnings
    prompt = llm.prompts[0]
    assert "x * 2" in prompt and "Twice x" in prompt and "x: 3 billion USD" in prompt


def test_review_flags_the_result_with_the_models_reasons():
    llm = Reply({"recommended_action": "review", "concerns": ["looks like millions"]})
    result = run(LLMValidator(llm))
    assert result.status is Status.NEEDS_REVIEW
    assert any("looks like millions" in w for w in result.warnings)


def test_reject_without_reason_still_flags():
    result = run(LLMValidator(Reply({"recommended_action": "reject"})))
    assert result.status is Status.NEEDS_REVIEW
    assert any("without giving a reason" in w for w in result.warnings)


@pytest.mark.parametrize(
    "llm",
    [
        Reply(error=RuntimeError("network down")),
        Reply("not json at all"),
        Reply({"recommended_action": "maybe"}),
    ],
)
def test_unavailable_validator_warns_but_does_not_change_status(llm):
    result = run(LLMValidator(llm))
    assert result.status is Status.OK
    assert any("extra validation skipped" in w for w in result.warnings)


def test_validator_sees_the_previous_value():
    llm = Reply({"recommended_action": "accept"})
    spec = company()
    previous = {
        "m": MetricResult(
            company_id="CO", metric="m", display_name="m", unit="billion USD",
            status=Status.OK, value=5.0,
        )
    }  # fmt: skip
    Engine(resolvers(), validators=[LLMValidator(llm)]).calculate_company(spec, previous=previous)
    assert "Previous value: 5 billion USD" in llm.prompts[0]


def test_validator_unavailable_is_an_exception_type():
    assert issubclass(ValidatorUnavailable, Exception)


def test_cli_ai_validate_needs_an_api_key(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("BNPL_METRICS_CACHE_DIR", str(tmp_path))
    example = str(__import__("pathlib").Path(__file__).resolve().parents[1] / "config/example.yaml")
    assert cli.main(["calculate", "--config", example, "--ai-validate", "--no-save"]) == 1
    assert "LLM_API_KEY" in capsys.readouterr().err
