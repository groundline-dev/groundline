"""Live smoke tests: the presets against the real Anthropic API.

Cost money (about $0.04 each for the omission preset and the API, and under a
cent for the commission preset, on these short examples), so they're excluded
from the default `pytest` run. Run it with `uv run pytest -m live -s` from the repo
root, with ANTHROPIC_API_KEY in the environment or `.env`.

Model output varies between runs, so this checks that every stage ran and
produced well-formed output, not which facts were found.
"""

import pytest
from fastapi.testclient import TestClient

from groundline import DEFAULT_COMMISSIONS_PRESET, evaluate_sync, load_preset
from groundline.app import app
from groundline.evaluators.commissions.models import CommissionsCheckOutput
from groundline.evaluators.models import AlarmOutput
from groundline.evaluators.omissions.models import (
    CheckOutput,
    RankOutput,
    ScoreOutput,
)
from tests.auth_helpers import SECRET as API_KEY
from tests.auth_helpers import FakeKeys
from tests.commissions_helpers import SUMMARY as SUMMARY_WITH_ERRORS
from tests.omission_helpers import SOURCE, SUMMARY

pytestmark = pytest.mark.live


def test_the_all_opus_preset_runs_end_to_end():
    result = evaluate_sync(SOURCE, SUMMARY)

    usage = result.run.usage
    print(
        f"\nlive run: {usage.calls} calls, {usage.input_tokens:,} input / "
        f"{usage.output_tokens:,} output tokens, ${usage.cost_usd:.4f}"
    )

    assert result.validated_preset == "omission_all_opus"
    assert {step: s.status for step, s in result.run.stages.items()} == {
        "extract": "ran",
        "rank": "ran",
        "check": "ran",
        "refute": "ran",
        "score": "ran",
        "alarm": "ran",
    }
    facts = result.run.output("rank", RankOutput).facts
    assert facts, "extract found no facts"
    assert all(f.severity is not None for f in facts)
    verdicts = result.run.output("check", CheckOutput).verdicts
    assert [v.fact_id for v in verdicts] == [f.id for f in facts]
    assert result.run.output("score", ScoreOutput).total == len(facts)
    assert 0.0 <= result.run.output("alarm", AlarmOutput).score <= 1.0

    assert {c.model for c in result.run.calls} == {"claude-opus-5-5"}
    assert usage.cost_usd > 0 and not usage.unpriced_models


def test_the_quick_commission_preset_runs_end_to_end():
    spec = load_preset(DEFAULT_COMMISSIONS_PRESET)
    result = evaluate_sync(SOURCE, SUMMARY_WITH_ERRORS, spec=spec)

    usage = result.run.usage
    print(
        f"\nlive run: {usage.calls} calls, {usage.input_tokens:,} input / "
        f"{usage.output_tokens:,} output tokens, ${usage.cost_usd:.4f}"
    )

    assert result.validated_preset == "commission_quick"
    assert {step: s.status for step, s in result.run.stages.items()} == {
        "commissions_check": "ran",
        "commissions_refute": "disabled",
        "alarm": "ran",
    }
    found = result.run.output("commissions_check", CommissionsCheckOutput).commissions
    assert all(0.0 <= c.unsupported_confidence <= 1.0 for c in found)
    assert 0.0 <= result.run.output("alarm", AlarmOutput).score <= 1.0

    assert {c.model for c in result.run.calls} == {"claude-haiku-5-5"}
    assert usage.cost_usd > 0 and not usage.unpriced_models


def test_the_omissions_endpoint_runs_end_to_end(monkeypatch: pytest.MonkeyPatch):
    # Exercise real auth with fake storage; PostgreSQL has separate db tests.
    monkeypatch.setattr(app.state, "api_keys", FakeKeys(), raising=False)
    response = TestClient(app).post(
        "/v1/omissions",
        json={"source": SOURCE, "summary": SUMMARY},
        headers={"Authorization": f"Bearer {API_KEY}"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    print(f"\nlive API run: {body['usage']}")

    assert body["validated_preset"] == "omission_all_opus"
    assert body["usage"]["calls"] >= 3 and body["usage"]["cost_usd"] > 0
    assert 0.0 <= body["alarm"]["score"] <= 1.0
