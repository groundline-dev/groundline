"""The alarm step every evaluator shares: flag the summary when its score reaches
the chosen threshold. No model calls.

Thresholds are only meaningful for the pipeline they were calibrated on, so they
live in the pipeline spec as named levels. Each evaluator subclasses
`ThresholdAlarm` with its own score and its own calibrated levels.
"""

from abc import abstractmethod
from typing import ClassVar, Self

from pydantic import model_validator

from groundline.core import Implementation, StageContext
from groundline.evaluators.models import ALARM_STEP, AlarmOutput

FLAGGED_SCORE_CAP = 0.4999
"""The highest rescaled score a flagged summary can have. Evaluation tools such as
deepeval pass a score at or above their cut of 0.5, and the alarm flags at or above
its threshold, so a summary on the threshold has to land below 0.5."""


def rescaled_score(alarm_score: float, threshold: float) -> float:
    """The alarm score turned around and rescaled from 0 to 1, so that higher is
    better and a summary is flagged exactly when its score is below 0.5. Rounded
    to four decimals, which can't move a score across 0.5."""
    if alarm_score < threshold:
        score = 0.5 + 0.5 * (threshold - alarm_score) / threshold
    elif alarm_score >= 1:  # also keeps a threshold of 1 from dividing by zero
        score = 0.0
    else:
        score = min(0.5 * (1 - alarm_score) / (1 - threshold), FLAGGED_SCORE_CAP)
    return round(score, 4)


class ThresholdAlarm(Implementation):
    """Subclasses set `name` and `reads`, declare `thresholds` and `level` with
    their calibrated defaults, and implement `score`."""

    step: ClassVar[str] = ALARM_STEP
    selectable_settings: ClassVar[tuple[str, ...]] = ("level",)
    calibrated_settings: ClassVar[tuple[str, ...]] = ("thresholds",)

    thresholds: dict[str, float]
    level: str

    @model_validator(mode="after")
    def _level_is_a_threshold(self) -> Self:
        if self.level not in self.thresholds:
            raise ValueError(
                f"level '{self.level}' is not one of the thresholds: "
                f"{', '.join(self.thresholds)}"
            )
        return self

    @abstractmethod
    def score(self, ctx: StageContext) -> float:
        """The alarm score: the highest confidence among the summary's critical
        problems, as this evaluator defines it; 0 if there are none."""

    async def run(self, ctx: StageContext) -> AlarmOutput:
        score = self.score(ctx)
        threshold = self.thresholds[self.level]
        return AlarmOutput(
            score=score,
            level=self.level,
            threshold=threshold,
            flagged=score >= threshold,
            rescaled_score=rescaled_score(score, threshold),
        )
