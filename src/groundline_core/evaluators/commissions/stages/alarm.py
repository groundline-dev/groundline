"""Commissions alarm: decide whether to flag the summary. No model calls.

The alarm score is the highest unsupported confidence among the critical
commissions still flagged after refute; 0 if there are none. The summary is
flagged when the score reaches the chosen threshold.

Thresholds are only meaningful for the pipeline they were calibrated on, so they
live in the pipeline spec as named levels. The quick preset's one level, 0.85,
was fixed in advance on development pairs.
"""

from typing import ClassVar

from groundline_core.core import StageContext
from groundline_core.evaluators.alarm import ThresholdAlarm
from groundline_core.evaluators.commissions.models import (
    Commission,
    CommissionsRefuteOutput,
)


class AlarmCriticalUnsupportedMax(ThresholdAlarm):
    name: ClassVar[str] = "alarm.critical_unsupported_max"
    reads: ClassVar[tuple[str, ...]] = ("commissions_refute",)

    thresholds: dict[str, float] = {"default": 0.85}
    """Named alarm scores at or above which the summary is flagged. Only
    meaningful for the pipeline they were calibrated on."""
    level: str = "default"
    """Which of `thresholds` to flag at."""

    def score(self, ctx: StageContext) -> float:
        return alarm_score(
            ctx.output("commissions_refute", CommissionsRefuteOutput).commissions
        )


def alarm_score(commissions: list[Commission]) -> float:
    return max(
        (c.unsupported_confidence for c in commissions if c.severity == "critical"),
        default=0.0,
    )
