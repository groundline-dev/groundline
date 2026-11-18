"""The lean preset's claims step: one call lists every claim the summary makes."""

import pytest

from groundline_core._core import StageError
from groundline_core._evaluators.commissions.models import CommissionsClaimsOutput
from tests.commissions_helpers import (
    CLAIMS,
    CLAIMS_REPLY,
    DOSE,
    DOSE_CLAIM,
    EXAM,
    SKIING,
    SKIING_CLAIM,
    SUMMARY,
    Script,
    claims_reply,
    run,
)
from tests.omission_helpers import SOURCE


def claims(reply: str | list[str]) -> tuple[CommissionsClaimsOutput, Script]:
    script = Script(commissions_claims=reply)
    result, _ = run([CLAIMS], script)
    return result.output("commissions_claims", CommissionsClaimsOutput), script


def test_one_call_sees_only_the_summary():
    _, script = claims(CLAIMS_REPLY)

    (prompt,) = script.prompts["commissions_claims"]
    assert SUMMARY in prompt
    assert SOURCE not in prompt
    assert "{note}" not in prompt


def test_calls_opus_at_high_effort():
    _, client = run([CLAIMS], Script(commissions_claims=CLAIMS_REPLY))

    (request,) = client.requests
    assert (request.model, request.effort, request.max_tokens) == (
        "claude-opus-5-5",
        "high",
        16000,
    )


def test_numbers_each_claim_from_c1_with_its_text_and_quote():
    out, _ = claims(CLAIMS_REPLY)

    assert [(c.id, c.text, c.quote) for c in out.claims] == [
        ("c1", SKIING_CLAIM, SKIING),
        ("c2", "Neurovascular examination was intact", EXAM),
        ("c3", DOSE_CLAIM, DOSE),
    ]


def test_a_summary_with_no_claims_lists_none():
    out, _ = claims(claims_reply())

    assert out.claims == []


@pytest.mark.parametrize(
    "bad",
    [
        claims_reply(("A made-up claim", "words not in the note")),
        claims_reply(("", SKIING)),
        claims_reply((SKIING_CLAIM, "")),
        '{"facts": []}',
    ],
    ids=["quote not in the summary", "no claim", "no quote", "no claims list"],
)
def test_an_unusable_reply_is_asked_again(bad: str):
    out, script = claims([bad, CLAIMS_REPLY])

    assert len(script.prompts["commissions_claims"]) == 2
    assert len(out.claims) == 3


def test_two_unusable_replies_stop_the_run():
    bad = claims_reply(("A made-up claim", "words not in the note"))

    with pytest.raises(StageError, match="commissions_claims"):
        claims([bad, bad])
