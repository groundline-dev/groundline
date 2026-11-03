"""The commission check: which claims in a summary the source doesn't support.

Steps, in order: commissions_check → commissions_refute → alarm. The alarm step
is shared with the omission check; this check supplies its own implementation.
"""

from pydantic import BaseModel

from groundline.core import Implementation, Registry
from groundline.evaluators.commissions.models import (
    CommissionsCheckOutput,
    CommissionsRefuteOutput,
)
from groundline.evaluators.commissions.stages import (
    AlarmCriticalUnsupportedMax,
    CommissionsCheckLLMQuick,
    CommissionsRefuteLLM,
)

STEPS: dict[str, type[BaseModel]] = {
    "commissions_check": CommissionsCheckOutput,
    "commissions_refute": CommissionsRefuteOutput,
}
"""The steps this check defines. `alarm` is defined by the omission check and
shared, so it isn't listed here."""

IMPLEMENTATIONS: list[type[Implementation]] = [
    CommissionsCheckLLMQuick,
    CommissionsRefuteLLM,
    AlarmCriticalUnsupportedMax,
]


def register_commissions(registry: Registry) -> None:
    """Define the commission steps and register their implementations. The
    shared `alarm` step must already be defined, so register the omission check
    first."""
    for step, output_type in STEPS.items():
        registry.define_step(step, output_type)
    for implementation in IMPLEMENTATIONS:
        registry.register(implementation)
