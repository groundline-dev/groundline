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
        )
