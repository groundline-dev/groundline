import json
from typing import Any

import pytest

from groundline.core import StageError
from groundline.evaluators.omissions.models import CheckOutput, Verdict
from tests.omission_helpers import (
    CHECK,
    EXTRACT,
    EXTRACTED,
    RANK,
    RANKED,
    SUMMARY,
    Script,
    check_reply,
    run,
)

STAGES = [EXTRACT, RANK, CHECK]


def verdicts(check: str | list[str]) -> list[Verdict]:
    result, _ = run(STAGES, Script(extract=EXTRACTED, rank=RANKED, check=check))
    return result.output("check", CheckOutput).verdicts


def test_makes_one_call_and_keeps_rank_order():
    script = Script(extract=EXTRACTED, rank=RANKED, check=check_reply())
    result, _ = run(STAGES, script)

    assert len(script.prompts["check"]) == 1
    out = result.output("check", CheckOutput).verdicts
    assert [v.fact_id for v in out] == ["f3", "f1", "f2"]
    assert [v.label for v in out] == ["present", "partial", "absent"]


def test_prompt_has_the_summary_and_every_fact_in_rank_order():
    script = Script(extract=EXTRACTED, rank=RANKED, check=check_reply())
    run(STAGES, script)

    (prompt,) = script.prompts["check"]
    assert SUMMARY in prompt
    assert (
        "<facts>\nf3: Motrin 600 mg every 6 hours for a week.\n"
        "f1: Injured the finger skiing.\nf2: Pain is worse with movement.\n</facts>"
    ) in prompt
    assert "{note}" not in prompt and "{facts}" not in prompt


def test_uses_medium_effort_by_default():
    _, client = run(STAGES, Script(extract=EXTRACTED, rank=RANKED, check=check_reply()))

    assert client.requests[-1].effort == "medium"


def test_confidence_becomes_the_score_and_quotes_are_verified():
    out = verdicts(check_reply())

    assert [v.score for v in out] == [0.05, 0.6, 0.95]
    assert out[0].quote_found is True
    assert out[2].quote is None and out[2].quote_found is None


def test_a_reply_missing_a_fact_is_retried_then_fails():
    short = json.loads(check_reply())
    short["facts"] = short["facts"][:2]

    with pytest.raises(StageError, match="no verdict for f2"):
        verdicts([json.dumps(short)] * 2)


@pytest.mark.parametrize("bad", [-1, 101, "high", None, True])
def test_confidence_must_be_0_to_100(bad: Any):
    with pytest.raises(StageError, match="missing_confidence"):
        verdicts([check_reply({"f1": bad})] * 2)


def test_a_present_or_partial_verdict_needs_a_quote():
    reply = json.loads(check_reply())
    reply["facts"][0]["quote"] = None

    with pytest.raises(StageError, match='f3: a "present" verdict needs a quote'):
        verdicts([json.dumps(reply)] * 2)
