"""Alarm: decide whether to flag the summary. No model calls.

The alarm score is the highest missing-confidence among critical facts still
flagged after refute, with partials at half weight; 0 if there are none. The
summary is flagged when the score reaches the chosen threshold.

Thresholds are only meaningful for the pipeline they were calibrated on, so they
live in the pipeline spec as named levels. The all-Opus preset's levels were
fixed in advance on calibration data: strict 0.90 and moderate 0.425.
"""

from typing import ClassVar

from groundline.core import StageContext
from groundline.evaluators.alarm import ThresholdAlarm
from groundline.evaluators.omissions.models import (
    RankedFact,
    RankOutput,
    RefuteOutput,
    Verdict,
)

PARTIAL_WEIGHT = 0.5


class AlarmCriticalMax(ThresholdAlarm):
    name: ClassVar[str] = "alarm.critical_max"
    reads: ClassVar[tuple[str, ...]] = ("rank", "refute")

    thresholds: dict[str, float] = {"strict": 0.90, "moderate": 0.425}
    """Named alarm scores at or above which the summary is flagged. Only
    meaningful for the pipeline they were calibrated on."""
    level: str = "strict"
    """Which of `thresholds` to flag at."""

    def score(self, ctx: StageContext) -> float:
        return alarm_score(
            ctx.output("rank", RankOutput).facts,
            ctx.output("refute", RefuteOutput).verdicts,
        )


def alarm_score(facts: list[RankedFact], verdicts: list[Verdict]) -> float:
    critical = {f.id for f in facts if f.severity == "critical"}
    best = 0.0
    for v in verdicts:
        if v.fact_id not in critical or v.label == "present":
            continue
        p = 1.0 if v.score is None else v.score
        best = max(best, p if v.label == "absent" else PARTIAL_WEIGHT * p)
    return best
