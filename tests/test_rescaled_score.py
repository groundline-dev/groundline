"""The rescaled score: the alarm score turned around and rescaled so that higher is
better. A summary is flagged below 0.5; 0.5 and up is not flagged. Worked examples
come from the omission-pipeline handoff (D46, deepeval_scores_groundline.md)."""

import asyncio
import math
from typing import ClassVar, cast

import pytest

from groundline import builtin_presets, load_preset
from groundline.core import StageContext
from groundline.evaluators import alarm as alarm_module
from groundline.evaluators.alarm import ThresholdAlarm
from groundline.evaluators.models import AlarmOutput
from tests import commissions_helpers as commissions
from tests import omission_helpers as omissions

SHIPPED_THRESHOLDS = sorted(
    {
        threshold
        for name in builtin_presets()
        for stage in load_preset(name).stages
        if stage.step == "alarm"
        for threshold in stage.settings["thresholds"].values()
    }
)


class FixedAlarm(ThresholdAlarm):
    """A real alarm whose alarm score is given rather than calculated."""

    name: ClassVar[str] = "alarm.fixed"
    reads: ClassVar[tuple[str, ...]] = ()

    fixed_score: float

    def score(self, ctx: StageContext) -> float:
        return self.fixed_score


def outcome(alarm: float, threshold: float) -> AlarmOutput:
    stage = FixedAlarm(
        fixed_score=alarm, thresholds={"named": threshold}, level="named"
    )
    return asyncio.run(stage.run(cast(StageContext, None)))


# --- the formula ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("alarm", "threshold", "expected"),
    [
        (0.0, 0.90, 1.0),
        (0.425, 0.90, 0.7639),  # the handoff's "moderate is score threshold 0.764"
        (0.475, 0.90, 0.7361),
        (0.90, 0.90, 0.4999),
        (0.95, 0.90, 0.25),
        (0.97, 0.90, 0.15),
        (1.0, 0.90, 0.0),
        (0.0, 0.85, 1.0),
        (0.50, 0.85, 0.7059),
        (0.85, 0.85, 0.4999),
        (0.95, 0.85, 0.1667),
        (1.0, 0.85, 0.0),
    ],
)
def test_the_formula_on_worked_examples(
    alarm: float, threshold: float, expected: float
):
    assert alarm_module.rescaled_score(alarm, threshold) == pytest.approx(
        expected, abs=1e-4
    )


@pytest.mark.parametrize("threshold", [0.90, 0.85])
def test_the_score_just_below_the_threshold_is_just_above_one_half(threshold: float):
    score = alarm_module.rescaled_score(math.nextafter(threshold, 0.0), threshold)

    assert score > 0.5
    assert score == pytest.approx(0.5)


@pytest.mark.parametrize("threshold", [0.90, 0.85])
def test_the_score_just_above_the_threshold_is_capped_below_one_half(threshold: float):
    score = alarm_module.rescaled_score(math.nextafter(threshold, 1.0), threshold)

    assert score == 0.4999


def test_a_threshold_of_one_never_divides_by_zero():
    assert alarm_module.rescaled_score(1.0, 1.0) == 0.0


# --- the score and the flag ---------------------------------------------------------


@pytest.mark.parametrize("threshold", SHIPPED_THRESHOLDS)
def test_the_score_and_the_flag_agree_at_every_shipped_threshold(threshold: float):
    alarms = [i / 100 for i in range(101)] + [
        threshold,
        math.nextafter(threshold, 0.0),
        math.nextafter(threshold, 1.0),
    ]

    for alarm in alarms:
        result = outcome(alarm, threshold)
        assert result.flagged == (result.rescaled_score < 0.5), alarm
        # The form deepeval and promptfoo use: they pass at score >= threshold.
        assert (not result.flagged) == (result.rescaled_score >= 0.5), alarm


@pytest.mark.parametrize("threshold", SHIPPED_THRESHOLDS)
def test_at_the_tie_the_score_is_below_one_half_and_the_summary_is_flagged(
    threshold: float,
):
    result = outcome(threshold, threshold)

    assert result.rescaled_score == 0.4999
    assert result.flagged is True


@pytest.mark.parametrize(
    ("alarm", "threshold", "flagged"),
    [
        (0.0, 0.90, False),
        (0.8999, 0.90, False),
        (0.90, 0.90, True),
        (0.9001, 0.90, True),
        (1.0, 0.90, True),
        (0.0, 0.85, False),
        (0.8499, 0.85, False),
        (0.85, 0.85, True),
        (0.8501, 0.85, True),
        (1.0, 0.85, True),
    ],
)
def test_the_alarm_decides_as_it_always_did(
    alarm: float, threshold: float, flagged: bool
):
    result = outcome(alarm, threshold)

    assert result.model_dump(exclude={"rescaled_score"}) == {
        "score": alarm,
        "level": "named",
        "threshold": threshold,
        "flagged": flagged,
    }


# --- what a pipeline run returns ----------------------------------------------------


def test_an_omission_run_has_the_score_and_no_extra_model_calls():
    result, _ = omissions.run(omissions.ALL_STAGES, omissions.flagged_script())

    alarm = result.output("alarm", AlarmOutput)
    assert alarm.rescaled_score == pytest.approx(0.25)
    assert [c.step for c in result.calls] == ["extract", "rank", "check", "refute"]


def test_a_commission_run_has_the_score_and_no_extra_model_calls():
    script = commissions.Script(commissions_check=commissions.ISSUES)

    result, _ = commissions.run(commissions.ALL_STAGES, script)

    alarm = result.output("alarm", AlarmOutput)
    assert alarm.rescaled_score == pytest.approx(0.1667, abs=1e-4)
    assert [c.step for c in result.calls] == ["commissions_check"]
