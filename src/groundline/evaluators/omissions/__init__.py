"""The omission check: which facts a summary leaves out, and how much each matters.

Steps, in order: extract → rank → check → refute → score → alarm.
"""

from pydantic import BaseModel

from groundline.core import Implementation, Registry
from groundline.evaluators.omissions.models import (
    AlarmOutput,
    CheckOutput,
    ExtractOutput,
    RankOutput,
    RefuteOutput,
    ScoreOutput,
)
from groundline.evaluators.omissions.stages import (
    AlarmCriticalMax,
    CheckLLMBatch,
    ExtractLLM,
    RankLLM,
    RefuteLLM,
    ScoreBasic,
)

STEPS: dict[str, type[BaseModel]] = {
    "extract": ExtractOutput,
    "rank": RankOutput,
    "check": CheckOutput,
    "refute": RefuteOutput,
    "score": ScoreOutput,
    "alarm": AlarmOutput,
}

IMPLEMENTATIONS: list[type[Implementation]] = [
    ExtractLLM,
    RankLLM,
    CheckLLMBatch,
    RefuteLLM,
    ScoreBasic,
    AlarmCriticalMax,
]


def register_omissions(registry: Registry) -> None:
    """Define the omission steps and register their implementations."""
    for step, output_type in STEPS.items():
        registry.define_step(step, output_type)
    for implementation in IMPLEMENTATIONS:
        registry.register(implementation)
