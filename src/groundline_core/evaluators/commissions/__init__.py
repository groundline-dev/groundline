"""The commission check: which claims in a summary the source doesn't support.

Steps, in order: commissions_check → commissions_refute → alarm. The alarm step
is shared with the omission check; this check supplies its own implementation.
"""

from pydantic import BaseModel

from groundline_core.core import Implementation, Registry
from groundline_core.evaluators import define_shared_steps
from groundline_core.evaluators.commissions.models import (
    CommissionsCheckOutput,
    CommissionsRefuteOutput,
)
from groundline_core.evaluators.commissions.stages import (
    AlarmCriticalUnsupportedMax,
    CommissionsCheckLLMQuick,
    CommissionsRefuteLLM,
)

STEPS: dict[str, type[BaseModel]] = {
    "commissions_check": CommissionsCheckOutput,
    "commissions_refute": CommissionsRefuteOutput,
}
"""The steps this check defines. `alarm` is shared with the omission check, so
`define_shared_steps` defines it."""

IMPLEMENTATIONS: list[type[Implementation]] = [
    CommissionsCheckLLMQuick,
    CommissionsRefuteLLM,
    AlarmCriticalUnsupportedMax,
]


def register_commissions(registry: Registry) -> None:
    """Define the commission steps and register their implementations."""
    define_shared_steps(registry)
    for step, output_type in STEPS.items():
        registry.define_step(step, output_type)
    for implementation in IMPLEMENTATIONS:
        registry.register(implementation)
