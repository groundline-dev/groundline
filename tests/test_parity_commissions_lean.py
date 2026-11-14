"""Parity with the measured lean commission check.

omission-pipeline's recorded lean run on the same note as the quick parity test (a
hedged "presumptive diagnosis, to be excluded" turned into a firm "confirmed
diagnosis") is replayed through groundline's `commission_reverse_lean` preset:
the claims call, the lean support call and the refute call. The recorded prompts
were built by omission-pipeline, not by groundline. groundline must send each
byte for byte, with the same model, effort and token limit, and reach the same
commission and alarm from the recorded replies. If this passes, the measured
results apply to groundline's preset. See the fixture's ATTRIBUTION.md.

The expected results were checked by hand against the transcript. They come from
groundline's own reading of the recorded replies, not from omission-pipeline.
Of the note's 42 claims, only the impression (c33) is contradicted; refute keeps
the flag, and its 0.92 confidence reaches the 0.80 threshold.
"""

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from groundline_core import EvaluationResult, evaluate, load_preset
from groundline_core.evaluators.commissions.models import CommissionsRefuteOutput
from groundline_core.evaluators.models import AlarmOutput
from tests.fakes import FakeClient

FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "parity"
    / "primock__day2_consultation05__change"
)
RECORDED: dict[str, Any] = json.loads(
    (FIXTURE / "recorded_reverse_lean.json").read_text()
)
EXPECTED: dict[str, Any] = RECORDED["expected"]


@pytest.fixture(scope="module")
def replay() -> tuple[EvaluationResult, FakeClient]:
    client = FakeClient([call["reply"] for call in RECORDED["calls"]])
    result = asyncio.run(
        evaluate(
            (FIXTURE / "source.txt").read_text(),
            (FIXTURE / "summary.txt").read_text(),
            spec=load_preset("commission_reverse_lean"),
            client=client,
        )
    )
    return result, client


def test_sends_the_same_prompts_in_order(
    replay: tuple[EvaluationResult, FakeClient],
):
    _, client = replay

    assert len(client.requests) == len(RECORDED["calls"]) == 3
    for request, call in zip(client.requests, RECORDED["calls"], strict=True):
        assert request.prompt == call["prompt"], call["step"]


def test_uses_the_same_models_efforts_and_token_limits(
    replay: tuple[EvaluationResult, FakeClient],
):
    _, client = replay

    assert [(r.model, r.effort, r.max_tokens) for r in client.requests] == [
        ("claude-opus-5-5", "high", 16000),  # claims
        ("claude-opus-5-5", "low", 16000),  # lean support
        ("claude-opus-5-5", "high", 16000),  # refute
    ]


def test_reaches_the_same_commissions(replay: tuple[EvaluationResult, FakeClient]):
    result, _ = replay

    refute = result.run.output("commissions_refute", CommissionsRefuteOutput)
    assert [c.model_dump() for c in refute.commissions] == EXPECTED["commissions"]
    assert refute.cleared == []


def test_flags_the_note_at_its_own_threshold(
    replay: tuple[EvaluationResult, FakeClient],
):
    result, _ = replay

    alarm = result.run.output("alarm", AlarmOutput)
    assert alarm.model_dump(exclude={"rescaled_score"}) == EXPECTED["alarm"]
    assert result.validated_preset == "commission_reverse_lean"
