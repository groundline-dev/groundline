import json

from groundline.evaluators.commissions.models import (
    CommissionsCheckOutput,
    CommissionsRefuteOutput,
)
from tests.commissions_helpers import (
    CHECK,
    DOSE,
    DOSE_ISSUE,
    DOSE_SOURCE,
    EXAM,
    EXAM_ISSUE,
    ISSUES,
    REFUTE,
    Script,
    issue,
    issues_reply,
    run,
    support_reply,
)
from tests.omission_helpers import SOURCE

KEEP_FLAG = support_reply(("q1", False, None))
CLEAR_FLAG = support_reply(("q1", True, "take motrin 600 mg every 6 hours"))


def refute(
    reply: str, check: str = ISSUES, enabled: bool = True
) -> tuple[CommissionsRefuteOutput, Script]:
    script = Script(commissions_check=check, commissions_refute=reply)
    result, _ = run([CHECK, {**REFUTE, "enabled": enabled}], script)
    return result.output("commissions_refute", CommissionsRefuteOutput), script


def test_refute_sees_only_the_critical_commissions():
    _, script = refute(KEEP_FLAG)

    (prompt,) = script.prompts["commissions_refute"]
    assert SOURCE in prompt
    assert DOSE in prompt
    assert EXAM not in prompt  # supporting: not challenged
    assert '"id": "q1"' in prompt and '"label": "contradicted"' in prompt


def test_refute_numbers_the_critical_commissions_from_q1():
    # The exam commission comes first in the check's list, but it isn't critical.
    check = issues_reply(EXAM_ISSUE, DOSE_ISSUE)
    out, script = refute(KEEP_FLAG, check)

    (prompt,) = script.prompts["commissions_refute"]
    assert '"id": "q1", "claim": "' + DOSE in prompt
    assert [i.id for i in out.commissions] == ["q1", "q2"]  # the check's own ids


def test_a_flag_that_stands_changes_nothing():
    out, _ = refute(KEEP_FLAG)

    assert [i.quote for i in out.commissions] == [DOSE, EXAM]
    assert out.cleared == []


def test_refute_clears_a_flag_with_a_quote_from_the_source():
    out, _ = refute(CLEAR_FLAG)

    assert [i.quote for i in out.commissions] == [EXAM]
    (cleared,) = out.cleared
    assert cleared.commission.quote == DOSE
    assert cleared.evidence == "take motrin 600 mg every 6 hours"


def test_refute_only_clears_it_never_adds_or_raises():
    out, _ = refute(KEEP_FLAG)
    checked = [DOSE_ISSUE, EXAM_ISSUE]

    assert len(out.commissions) == len(checked)
    assert [i.unsupported_confidence for i in out.commissions] == [0.95, 0.7]


def test_with_no_critical_commission_there_is_no_call():
    skiing = issue("She was skiing", "unsupported", 60, "peripheral")
    out, script = refute(KEEP_FLAG, issues_reply(EXAM_ISSUE, skiing))

    assert "commissions_refute" not in script.prompts
    assert out.cleared == []


def test_with_no_commission_at_all_there_is_no_call():
    out, script = refute(KEEP_FLAG, issues_reply())

    assert "commissions_refute" not in script.prompts
    assert out.commissions == [] and out.cleared == []


def test_switched_off_passes_the_check_commissions_through():
    script = Script(commissions_check=ISSUES)
    result, client = run([CHECK, {**REFUTE, "enabled": False}], script)

    assert len(client.requests) == 1  # just the check
    assert (
        result.output("commissions_refute", CommissionsRefuteOutput).commissions
        == result.output("commissions_check", CommissionsCheckOutput).commissions
    )
    assert result.stages["commissions_refute"].status == "disabled"


def test_refute_calls_haiku_at_medium_effort():
    script = Script(commissions_check=ISSUES, commissions_refute=KEEP_FLAG)
    _, client = run([CHECK, REFUTE], script)

    assert [(r.model, r.effort) for r in client.requests] == [
        ("claude-haiku-5-5", "medium"),
        ("claude-haiku-5-5", "medium"),
    ]


def test_the_prompt_block_is_json_the_model_can_read():
    _, script = refute(KEEP_FLAG)

    (prompt,) = script.prompts["commissions_refute"]
    block = prompt.split("<flagged_claims>\n")[1].split("\n</flagged_claims>")[0]
    assert json.loads(block) == [
        {"id": "q1", "claim": DOSE, "note_quote": DOSE, "label": "contradicted"}
    ]
    assert DOSE_SOURCE not in block  # refute is never shown the check's evidence
