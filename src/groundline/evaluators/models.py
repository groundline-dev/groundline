"""Data shared by every evaluator: severity, and the alarm's output.

Each evaluator keeps its own data in its own package, such as
`evaluators/omissions/models.py`. What it shares with the others lives here, so
the evaluators don't depend on each other.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict

Severity = Literal["critical", "supporting", "peripheral"]
SEVERITIES: dict[str, Severity] = {
    "critical": "critical",
    "supporting": "supporting",
    "peripheral": "peripheral",
}
"""Severity names, most important first. A dict so a reply's text can be looked
up and come back typed."""


ALARM_STEP = "alarm"
"""The step every evaluator ends with. It decides whether to flag the summary,
and its output is an `AlarmOutput`."""


class Frozen(BaseModel):
    """The base for evaluator data: immutable, and strict about its fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class AlarmOutput(Frozen):
    """Whether to flag the summary: the alarm score, and the threshold it met or
    missed."""

    score: float
    """The highest confidence among the summary's critical problems, as each
    evaluator defines it; 0 if there are none."""
    level: str
    threshold: float
    flagged: bool
    rescaled_score: float
    """`score` turned round and rescaled from 0 to 1, for evaluation tools: higher
    is better, and the summary is flagged exactly when this is below 0.5. It only
    means something alongside the pipeline and level that produced it."""
