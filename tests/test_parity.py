"""Parity with the measured all-Opus pipeline.

One recorded run of the pipeline the published results come from is replayed
through groundline's `omission_all_opus` preset: the recorded model replies are
fed back in order. groundline must send the same prompts, byte for byte, and
reach the same facts, verdicts, score and alarm. If this passes, the measured
results apply to groundline's preset. See the fixture's ATTRIBUTION.md.
"""

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from groundline import EvaluationResult, evaluate
from groundline.evaluators.omissions.models import (
    AlarmOutput,
    RankOutput,
    RefuteOutput,
    ScoreOutput,
)
from tests.fakes import FakeClient

FIXTURE = (
    Path(__file__).parent / "fixtures" / "parity" / "primock__day2_consultation05__omit"
)
RECORDED: dict[str, Any] = json.loads((FIXTURE / "recorded.json").read_text())
EXPECTED: dict[str, Any] = RECORDED["expected"]


@pytest.fixture(scope="module")
def replay() -> tuple[EvaluationResult, FakeClient]:
    client = FakeClient([call["reply"] for call in RECORDED["calls"]])
    result = asyncio.run(
        evaluate(
            (FIXTURE / "source.txt").read_text(),
            (FIXTURE / "summary.txt").read_text(),
            client=client,
        )
    )
    return result, client


def test_sends_the_same_prompts(replay: tuple[EvaluationResult, FakeClient]):
    _, client = replay

    assert len(client.requests) == len(RECORDED["calls"])
    for request, call in zip(client.requests, RECORDED["calls"], strict=True):
        assert request.prompt == call["prompt"], f"{call['step']} prompt differs"


def test_uses_the_same_models_and_efforts(
    replay: tuple[EvaluationResult, FakeClient],
):
    _, client = replay

    assert [(r.model, r.effort) for r in client.requests] == [
        ("claude-opus-5-5", "high"),  # extract
        ("claude-opus-5-5", "high"),  # rank
        ("claude-opus-5-5", "medium"),  # check
        ("claude-opus-5-5", "high"),  # refute
    ]


def test_reaches_the_same_ranked_facts(replay: tuple[EvaluationResult, FakeClient]):
    result, _ = replay

    facts = result.run.output("rank", RankOutput).facts
    assert [f.model_dump() for f in facts] == EXPECTED["facts"]


def test_reaches_the_same_final_verdicts(
    replay: tuple[EvaluationResult, FakeClient],
):
    result, _ = replay

    refute = result.run.output("refute", RefuteOutput)
    assert [v.model_dump() for v in refute.verdicts] == EXPECTED["verdicts"]
    assert sorted(e.id for e in refute.log if e.flipped) == EXPECTED["refute_flips"]


def test_reaches_the_same_score(replay: tuple[EvaluationResult, FakeClient]):
    result, _ = replay

    score = result.run.output("score", ScoreOutput)
    assert {key: getattr(score, key) for key in EXPECTED["score"]} == EXPECTED["score"]


def test_flags_the_note_at_the_strict_level(
    replay: tuple[EvaluationResult, FakeClient],
):
    result, _ = replay

    alarm = result.run.output("alarm", AlarmOutput)
    assert (alarm.score, alarm.threshold, alarm.flagged) == (0.97, 0.90, True)
    assert result.validated_preset == "omission_all_opus"
