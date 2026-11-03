import pytest

from groundline.core import StageError
from groundline.evaluators.commissions.models import CommissionsCheckOutput
from tests.commissions_helpers import (
    CHECK,
    DOSE,
    DOSE_ISSUE,
    DOSE_SOURCE,
    EXAM,
    EXAM_ISSUE,
    ISSUES,
    SUMMARY,
    Script,
    issue,
    issues_reply,
    run,
)
from tests.omission_helpers import SOURCE


def check(reply: str | list[str]) -> tuple[CommissionsCheckOutput, Script]:
    script = Script(commissions_check=reply)
    result, _ = run([CHECK], script)
    return result.output("commissions_check", CommissionsCheckOutput), script


def test_one_call_sees_the_source_and_the_summary():
    _, script = check(ISSUES)

    (prompt,) = script.prompts["commissions_check"]
    assert SOURCE in prompt
    assert SUMMARY in prompt
    assert "{transcript}" not in prompt and "{note}" not in prompt


def test_calls_haiku_at_medium_effort():
    script = Script(commissions_check=ISSUES)
    _, client = run([CHECK], script)

    (request,) = client.requests
    assert (request.model, request.effort) == ("claude-haiku-5-5", "medium")


def test_lists_each_issue_with_its_details():
    out, _ = check(ISSUES)

    dose, exam = out.issues
    assert (dose.id, dose.quote, dose.verdict) == ("q1", DOSE, "contradicted")
    assert (dose.confidence, dose.severity) == (0.95, "critical")
    assert dose.evidence == DOSE_SOURCE
    assert (exam.id, exam.verdict, exam.severity) == ("q2", "unsupported", "supporting")
    assert exam.confidence == 0.7
    assert exam.evidence is None


def test_a_summary_with_no_problems_has_no_issues():
    out, _ = check(issues_reply())

    assert out.issues == []


def test_evidence_that_is_not_in_the_source_is_dropped():
    made_up = issue(DOSE, "contradicted", 90, "critical", "the doctor said 400 mg")
    out, _ = check(issues_reply(made_up))

    (only,) = out.issues
    assert only.verdict == "contradicted"
    assert only.evidence is None


def test_a_quote_that_is_not_in_the_summary_gets_one_retry():
    invented = issue("She has a fever", "unsupported", 80, "critical")
    out, script = check([issues_reply(invented), ISSUES])

    assert len(script.prompts["commissions_check"]) == 2
    assert "quote is not in the note" in script.prompts["commissions_check"][1]
    assert [i.quote for i in out.issues] == [DOSE, EXAM]


def test_an_unusable_reply_after_the_retry_is_an_error():
    with pytest.raises(StageError, match="commissions_check"):
        check("not json")


def test_issues_are_kept_in_the_order_listed():
    out, _ = check(issues_reply(EXAM_ISSUE, DOSE_ISSUE))

    assert [i.id for i in out.issues] == ["q1", "q2"]
    assert [i.quote for i in out.issues] == [EXAM, DOSE]
