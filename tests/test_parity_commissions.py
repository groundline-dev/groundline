"""Parity with the measured quick commission check.

One recorded run of the quick check on a note with a planted commission (a hedged
"presumptive diagnosis, to be excluded" turned into a firm "confirmed diagnosis")
is replayed through groundline's `commission_quick` preset. The recorded prompt
was built from omission-pipeline's own prompt file, not by groundline. groundline
must send it byte for byte, with the same model and effort, and reach the same
commission and alarm from the recorded reply. If this passes, the measured results
apply to groundline's preset. See the fixture's ATTRIBUTION.md.

The expected results were checked by hand against the transcript. They come from
groundline's own parsing of the recorded reply, not from omission-pipeline.

The model's source quote for the contradiction is not kept: the transcript marks the
word "is" as `<UNSURE>is</UNSURE>`, so the quote doesn't match word for word and is
dropped, as the check is designed to do. The claim is still flagged.
"""

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from groundline import (
    DEFAULT_COMMISSIONS_PRESET,
    EvaluationResult,
    evaluate,
    load_preset,
)
from groundline.evaluators.commissions.models import CommissionsRefuteOutput
from groundline.evaluators.models import AlarmOutput
from tests.fakes import FakeClient

FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "parity"
    / "primock__day2_consultation05__change"
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
            spec=load_preset(DEFAULT_COMMISSIONS_PRESET),
            client=client,
        )
    )
    return result, client


def test_sends_the_same_prompt(replay: tuple[EvaluationResult, FakeClient]):
    _, client = replay

    assert len(client.requests) == len(RECORDED["calls"]) == 1
    assert client.requests[0].prompt == RECORDED["calls"][0]["prompt"]


def test_uses_the_same_model_effort_and_token_limit(
    replay: tuple[EvaluationResult, FakeClient],
):
    _, client = replay

    request = client.requests[0]
    assert (request.model, request.effort, request.max_tokens) == (
        "claude-haiku-5-5",
        "medium",
        16000,
    )


def test_reaches_the_same_issues(replay: tuple[EvaluationResult, FakeClient]):
    result, _ = replay

    refute = result.run.output("commissions_refute", CommissionsRefuteOutput)
    assert [i.model_dump() for i in refute.commissions] == EXPECTED["commissions"]
    assert refute.cleared == []


def test_flags_the_note_at_the_default_level(
    replay: tuple[EvaluationResult, FakeClient],
):
    result, _ = replay

    alarm = result.run.output("alarm", AlarmOutput)
    assert alarm.model_dump() == EXPECTED["alarm"]
    assert result.validated_preset == "commission_quick"
