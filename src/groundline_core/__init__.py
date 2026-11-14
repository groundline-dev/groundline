"""groundline: checks AI-written summaries against their source.

Start with `evaluate` (or `evaluate_sync`), which runs the validated all-Opus
omission preset by default. Pass `spec=load_preset(DEFAULT_COMMISSIONS_PRESET)` to
run the commission check instead.
"""

from groundline_core.evaluate import (
    DEFAULT_COMMISSIONS_PRESET,
    DEFAULT_PRESET,
    EvaluationResult,
    default_registry,
    evaluate,
    evaluate_sync,
)
from groundline_core.presets import builtin_presets, load_preset

__all__ = [
    "DEFAULT_COMMISSIONS_PRESET",
    "DEFAULT_PRESET",
    "EvaluationResult",
    "builtin_presets",
    "default_registry",
    "evaluate",
    "evaluate_sync",
    "load_preset",
]
