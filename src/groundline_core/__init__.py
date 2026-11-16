"""groundline: checks AI-written summaries against their source.

Start with `evaluate` (or `evaluate_sync`), which runs the validated all-Opus
omission check by default. Pass `spec=load_preset(DEFAULT_COMMISSIONS_PRESET)` to
run the commission check instead. The result says whether the summary is flagged,
its score from 0 to 1, what the check found, and what it cost.

The names listed in `__all__`, and the documented fields of the result types,
are the public interface: they keep working across upgrades. Everything else,
including the modules inside this package and `EvaluationResult.run`, is
internal and may change without notice.
"""

from groundline_core._core import ModelClient, ModelReply, PipelineSpec
from groundline_core._evaluate import (
    DEFAULT_COMMISSIONS_PRESET,
    DEFAULT_PRESET,
    CommissionPreset,
    evaluate,
    evaluate_sync,
)
from groundline_core._result import Alarm, Counts, EvaluationResult, Finding, Usage
from groundline_core.presets import builtin_presets, load_preset

__all__ = [
    "DEFAULT_COMMISSIONS_PRESET",
    "DEFAULT_PRESET",
    "Alarm",
    "CommissionPreset",
    "Counts",
    "EvaluationResult",
    "Finding",
    "ModelClient",
    "ModelReply",
    "PipelineSpec",
    "Usage",
    "builtin_presets",
    "evaluate",
    "evaluate_sync",
    "load_preset",
]
