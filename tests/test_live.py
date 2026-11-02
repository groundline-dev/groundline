"""Live smoke test: the all-Opus preset against the real Anthropic API.

Costs money (roughly $0.10-0.20 per run on this short example), so it's excluded
from the default `pytest` run. Run it with `uv run pytest -m live -s` from the repo
root, with ANTHROPIC_API_KEY in the environment or `.env`.

Model output varies between runs, so this checks that every stage ran and
produced well-formed output, not which facts were found.
"""

import pytest

from groundline import evaluate_sync
from groundline.evaluators.omissions.models import (
    AlarmOutput,
    CheckOutput,
    RankOutput,
    ScoreOutput,
)
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
