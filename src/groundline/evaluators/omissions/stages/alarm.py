"""Alarm: decide whether to flag the summary. No model calls.

The alarm score is the highest missing-confidence among critical facts still
flagged after refute, with partials at half weight; 0 if there are none. The
summary is flagged when the score reaches the chosen threshold.

Thresholds are only meaningful for the pipeline they were calibrated on, so they
live in the pipeline spec as named levels. The all-Opus preset's levels were
fixed in advance on calibration data: strict 0.90 and moderate 0.425.
"""

from typing import ClassVar, Self

from pydantic import model_validator

from groundline.core import Implementation, StageContext
from groundline.evaluators.models import AlarmOutput
from groundline.evaluators.omissions.models import (
    RankedFact,
    RankOutput,
    RefuteOutput,
    Verdict,
)

PARTIAL_WEIGHT = 0.5


class AlarmCriticalMax(Implementation):
    name: ClassVar[str] = "alarm.critical_max"
    step: ClassVar[str] = "alarm"
    reads: ClassVar[tuple[str, ...]] = ("rank", "refute")
    selectable_settings: ClassVar[tuple[str, ...]] = ("level",)
    calibrated_settings: ClassVar[tuple[str, ...]] = ("thresholds",)

    thresholds: dict[str, float] = {"strict": 0.90, "moderate": 0.425}
    """Named alarm scores at or above which the summary is flagged. Only
    meaningful for the pipeline they were calibrated on."""
    level: str = "strict"
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
            ctx.output("rank", RankOutput).facts,
            ctx.output("refute", RefuteOutput).verdicts,
        )
        threshold = self.thresholds[self.level]
        return AlarmOutput(
            score=score,
            level=self.level,
            threshold=threshold,
            flagged=score >= threshold,
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
