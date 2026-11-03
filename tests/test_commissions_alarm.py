import json

import pytest

from groundline.core import SpecError
from groundline.evaluators.commissions.models import Commission
from groundline.evaluators.commissions.stages.alarm import alarm_score
from groundline.evaluators.models import AlarmOutput
from tests.commissions_helpers import (
    ALARM,
    ALL_STAGES,
    CHECK,
    DOSE,
    EXAM_ISSUE,
    ISSUES,
    REFUTE,
    Script,
    issue,
    issues_reply,
    run,
    support_reply,
)


def found(id: str, severity: str, confidence: float) -> Commission:
    return Commission.model_validate(
        {
            "id": id,
            "quote": "q",
            "support_verdict": "unsupported",
            "unsupported_confidence": confidence,
            "severity": severity,
        }
    )


def alarm(check: str, level: str | None = None) -> AlarmOutput:
    stages = ALL_STAGES[:-1] + [
        {**ALARM, "settings": {"level": level} if level else {}}
    ]
    result, _ = run(stages, Script(commissions_check=check))
    return result.output("alarm", AlarmOutput)


# --- the score ---------------------------------------------------------------------


def test_the_score_is_the_highest_confidence_among_critical_commissions():
    commissions = [
        found("q1", "critical", 0.6),
        found("q2", "critical", 0.9),
        found("q3", "supporting", 0.99),
    ]

    assert alarm_score(commissions) == 0.9


def test_only_critical_commissions_count():
    assert alarm_score([found("q1", "supporting", 1.0)]) == 0.0
    assert alarm_score([found("q1", "peripheral", 1.0)]) == 0.0


def test_no_commissions_scores_zero():
    assert alarm_score([]) == 0.0


def test_a_contradicted_claim_counts_the_same_as_an_unsupported_one():
    contradicted = found("q1", "critical", 0.8).model_copy(
        update={"support_verdict": "contradicted"}
    )

    assert alarm_score([contradicted]) == alarm_score([found("q1", "critical", 0.8)])


# --- the threshold -------------------------------------------------------------------


def test_the_default_level_is_the_preset_threshold_of_0_85():
    out = alarm(ISSUES)

    assert (out.score, out.level, out.threshold, out.flagged) == (
        0.95,
        "default",
        0.85,
        True,
    )


def test_a_summary_with_only_a_non_critical_commission_is_not_flagged():
    out = alarm(issues_reply(EXAM_ISSUE))

    assert (out.score, out.flagged) == (0.0, False)


def test_a_summary_with_no_commissions_is_not_flagged():
    assert alarm(issues_reply()).flagged is False


@pytest.mark.parametrize("confidence,flagged", [(84, False), (85, True), (86, True)])
def test_the_alarm_fires_at_the_threshold_and_above(confidence: int, flagged: bool):
    out = alarm(issues_reply(issue(DOSE, "unsupported", confidence, "critical")))

    assert out.flagged is flagged


def test_a_cleared_critical_commission_no_longer_raises_the_alarm():
    script = Script(
        commissions_check=ISSUES,
        commissions_refute=support_reply(
            ("q1", True, "take motrin 600 mg every 6 hours")
        ),
    )
    result, _ = run([CHECK, REFUTE, ALARM], script)

    out = result.output("alarm", AlarmOutput)
    assert (out.score, out.flagged) == (0.0, False)


def test_a_flag_refute_cannot_clear_still_raises_the_alarm():
    script = Script(
        commissions_check=ISSUES,
        commissions_refute=support_reply(("q1", False, None)),
    )
    result, _ = run([CHECK, REFUTE, ALARM], script)

    assert result.output("alarm", AlarmOutput).flagged is True


def test_custom_levels_are_chosen_by_name():
    settings = {"thresholds": {"default": 0.85, "lax": 0.99}, "level": "lax"}
    result, _ = run(
        ALL_STAGES[:-1] + [{**ALARM, "settings": settings}],
        Script(commissions_check=ISSUES),
    )

    out = result.output("alarm", AlarmOutput)
    assert (out.level, out.threshold, out.flagged) == ("lax", 0.99, False)


def test_an_unknown_level_is_rejected_before_any_call():
    with pytest.raises(SpecError, match="level 'extreme' is not one of the thresholds"):
        alarm(ISSUES, level="extreme")


def test_the_alarm_needs_the_refute_step_before_it():
    with pytest.raises(SpecError, match="reads 'commissions_refute'"):
        run([CHECK, ALARM], Script(commissions_check=ISSUES))


def test_the_alarm_output_survives_json():
    out = alarm(ISSUES)

    assert AlarmOutput.model_validate(json.loads(out.model_dump_json())) == out
