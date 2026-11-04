"""Evaluators: each checks one property of a summary against its source."""

from groundline.core import Registry
from groundline.evaluators.models import ALARM_STEP, AlarmOutput


def define_shared_steps(registry: Registry) -> None:
    """Define the steps every evaluator uses, if they aren't defined yet.

    Each evaluator's `register_*` function calls this, so evaluators can be
    registered in any order, or alone."""
    if ALARM_STEP not in registry.steps():
        registry.define_step(ALARM_STEP, AlarmOutput)
