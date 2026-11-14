"""The lean preset's support check: one call judges every claim against the source,
and only the claims the source doesn't support come out as commissions."""

import pytest

from groundline_core.core import StageError
from groundline_core.evaluators.commissions.models import CommissionsCheckOutput
from tests.commissions_helpers import (
    CLAIMS,
    CLAIMS_REPLY,
    DOSE,
    DOSE_CLAIM,
    DOSE_CONTRADICTED,
    DOSE_SOURCE,
    EXAM,
    EXAM_CLAIM,
    EXAM_UNSUPPORTED,
    SKIING,
    SKIING_CLAIM,
    SKIING_SUPPORTED,
    SUMMARY,
    SUPPORT,
    VERDICTS_REPLY,
    Script,
    claims_reply,
    run,
    verdict_entry,
    verdicts_reply,
)
from tests.omission_helpers import SOURCE


def support(
    reply: str | list[str], claims: str = CLAIMS_REPLY
) -> tuple[CommissionsCheckOutput, Script]:
    script = Script(commissions_claims=claims, commissions_check=reply)
    result, _ = run([CLAIMS, SUPPORT], script)
    return result.output("commissions_check", CommissionsCheckOutput), script


def test_one_call_sees_the_source_and_every_claim_in_omission_pipelines_format():
    _, script = support(VERDICTS_REPLY)

    (prompt,) = script.prompts["commissions_check"]
    assert SOURCE in prompt
    assert SUMMARY not in prompt
    assert (
        f'c1: {SKIING_CLAIM}  (note: "{SKIING}")\n'
        f'c2: {EXAM_CLAIM}  (note: "{EXAM}")\n'
        f'c3: {DOSE_CLAIM}  (note: "{DOSE}")'
    ) in prompt
    assert "{transcript}" not in prompt and "{claims}" not in prompt


def test_calls_opus_at_low_effort():
    script = Script(commissions_claims=CLAIMS_REPLY, commissions_check=VERDICTS_REPLY)
    _, client = run([CLAIMS, SUPPORT], script)

    assert [(r.model, r.effort, r.max_tokens) for r in client.requests] == [
        ("claude-opus-5-5", "high", 16000),  # claims
        ("claude-opus-5-5", "low", 16000),  # support
    ]


def test_only_the_claims_the_source_does_not_support_are_commissions():
    out, _ = support(VERDICTS_REPLY)

    exam, dose = out.commissions
    assert (exam.id, exam.quote) == ("c2", EXAM)
    assert (exam.support_verdict, exam.unsupported_confidence) == ("unsupported", 0.7)
    assert exam.severity == "supporting"
    assert (dose.id, dose.quote) == ("c3", DOSE)
    assert (dose.support_verdict, dose.unsupported_confidence) == (
        "contradicted",
        0.95,
    )
    assert (dose.severity, dose.evidence, dose.evidence_found) == (
        "critical",
        DOSE_SOURCE,
        True,
    )


def test_an_unsupported_claim_needs_no_quote():
    out, _ = support(
        verdicts_reply(SKIING_SUPPORTED, EXAM_UNSUPPORTED, DOSE_CONTRADICTED)
    )

    exam = out.commissions[0]
    assert (exam.evidence, exam.evidence_found) == (None, None)


def test_a_supported_claim_with_a_quote_not_in_the_source_is_kept_without_it():
    # Lean: a supported claim needs no quote, so a bad one is dropped, not retried.
    skiing = verdict_entry(
        "c1", "supported", 5, "peripheral", "words not in the source"
    )
    out, script = support(verdicts_reply(skiing, EXAM_UNSUPPORTED, DOSE_CONTRADICTED))

    assert len(script.prompts["commissions_check"]) == 1
    assert [c.id for c in out.commissions] == ["c2", "c3"]


def test_with_no_claims_there_is_no_call():
    out, script = support(VERDICTS_REPLY, claims=claims_reply())

    assert "commissions_check" not in script.prompts
    assert out.commissions == []


@pytest.mark.parametrize(
    "bad",
    [
        verdicts_reply(
            SKIING_SUPPORTED,
            EXAM_UNSUPPORTED,
            {**DOSE_CONTRADICTED, "evidence": "words not in the source"},
        ),
        verdicts_reply(
            SKIING_SUPPORTED, EXAM_UNSUPPORTED, {**DOSE_CONTRADICTED, "evidence": None}
        ),
        verdicts_reply(SKIING_SUPPORTED, EXAM_UNSUPPORTED),
        verdicts_reply(
            SKIING_SUPPORTED, EXAM_UNSUPPORTED, DOSE_CONTRADICTED, SKIING_SUPPORTED
        ),
        verdicts_reply(
            SKIING_SUPPORTED, EXAM_UNSUPPORTED, {**DOSE_CONTRADICTED, "id": "c9"}
        ),
        verdicts_reply(
            SKIING_SUPPORTED, EXAM_UNSUPPORTED, {**DOSE_CONTRADICTED, "label": "maybe"}
        ),
        verdicts_reply(
            SKIING_SUPPORTED,
            EXAM_UNSUPPORTED,
            {**DOSE_CONTRADICTED, "not_supported_confidence": 101},
        ),
        verdicts_reply(
            SKIING_SUPPORTED,
            EXAM_UNSUPPORTED,
            {**DOSE_CONTRADICTED, "not_supported_confidence": True},
        ),
        verdicts_reply(
            SKIING_SUPPORTED,
            EXAM_UNSUPPORTED,
            {**DOSE_CONTRADICTED, "importance": "huge"},
        ),
        '{"issues": []}',
    ],
    ids=[
        "contradiction quote not in the source",
        "contradiction without a quote",
        "a claim missing",
        "a claim repeated",
        "an unknown id",
        "unknown label",
        "confidence over 100",
        "confidence not a number",
        "unknown importance",
        "no claims list",
    ],
)
def test_an_unusable_reply_is_asked_again(bad: str):
    out, script = support([bad, VERDICTS_REPLY])

    assert len(script.prompts["commissions_check"]) == 2
    assert [c.id for c in out.commissions] == ["c2", "c3"]


def test_two_unusable_replies_stop_the_run():
    with pytest.raises(StageError, match="commissions_check"):
        support("not json")
