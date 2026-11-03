"""Commissions alarm: decide whether to flag the summary. No model calls.

The alarm score is the highest unsupported confidence among the critical
commissions still flagged after refute; 0 if there are none. The summary is
flagged when the score reaches the chosen threshold.

Thresholds are only meaningful for the pipeline they were calibrated on, so they
live in the pipeline spec as named levels. The quick preset's one level, 0.85,
was fixed in advance on development pairs.
"""

from typing import ClassVar, Self

from pydantic import model_validator

from groundline.core import Implementation, StageContext
from groundline.evaluators.commissions.models import (
    Commission,
    CommissionsRefuteOutput,
)
from groundline.evaluators.models import AlarmOutput


class AlarmCriticalUnsupportedMax(Implementation):
    name: ClassVar[str] = "alarm.critical_unsupported_max"
    step: ClassVar[str] = "alarm"
    reads: ClassVar[tuple[str, ...]] = ("commissions_refute",)
    selectable_settings: ClassVar[tuple[str, ...]] = ("level",)
    calibrated_settings: ClassVar[tuple[str, ...]] = ("thresholds",)

    thresholds: dict[str, float] = {"default": 0.85}
    """Named alarm scores at or above which the summary is flagged. Only
    meaningful for the pipeline they were calibrated on."""
    level: str = "default"
    """Which of `thresholds` to flag at."""

    @model_validator(mode="after")
    def _level_is_a_threshold(self) -> Self:
        if self.level not in self.thresholds:
            raise ValueError(
                f"level '{self.level}' is not one of the thresholds: "
                f"{', '.join(self.thresholds)}"
            )
        return self

    async def run(self, ctx: StageContext) -> AlarmOutput:
        score = alarm_score(
            ctx.output("commissions_refute", CommissionsRefuteOutput).commissions
        )
        threshold = self.thresholds[self.level]
        return AlarmOutput(
            score=score,
            level=self.level,
            threshold=threshold,
            flagged=score >= threshold,
        )


def alarm_score(commissions: list[Commission]) -> float:
    return max(
        (c.unsupported_confidence for c in commissions if c.severity == "critical"),
        default=0.0,
    )
