"""groundline: checks AI-written summaries against their source.

Start with `evaluate` (or `evaluate_sync`), which runs the validated all-Opus
omission check by default. Pass `spec=load_preset(DEFAULT_COMMISSIONS_PRESET)` to
run the commission check instead. The result says whether the summary is flagged,
its score from 0 to 1, what the check found, and what it cost.

When a check can't finish, it raises a `GroundlineError`: a `SetupError` to fix
before trying again, an `InvalidInputError` for a source or summary that can't
be checked, or a `ModelFailureError` (a `ModelTimeoutError` when time ran out)
that a later try may get past. Exceptions from your own code pass through as
they were raised.

The names listed in `__all__`, and the documented fields of the result types,
are the public interface: they keep working across upgrades. Everything else,
including the modules whose names start with an underscore and
`EvaluationResult.run`, is internal and may change without notice.
"""

from groundline_core._core import ModelClient, ModelReply, PipelineSpec
from groundline_core._core.errors import (
    APIKeyRejectedError,
    GroundlineError,
    InvalidInputError,
    ModelFailureError,
    ModelTimeoutError,
    SetupError,
)
from groundline_core._core.profile import ProfileError
from groundline_core._core.spec import SpecError
from groundline_core._evaluate import (
    DEFAULT_COMMISSIONS_PRESET,
    DEFAULT_PRESET,
    CommissionPreset,
    evaluate,
    evaluate_sync,
)
from groundline_core._evaluators.models import Severity
from groundline_core._result import (
    Alarm,
    Counts,
    EvaluationResult,
    EvaluationType,
    Finding,
    Usage,
)
from groundline_core._settings import MissingAPIKeyError, SettingsError
from groundline_core.presets import builtin_presets, load_preset

__all__ = [
    "APIKeyRejectedError",
    "GroundlineError",
    "InvalidInputError",
    "MissingAPIKeyError",
    "ModelFailureError",
    "ModelTimeoutError",
    "ProfileError",
    "SettingsError",
    "SetupError",
    "SpecError",
    "DEFAULT_COMMISSIONS_PRESET",
    "DEFAULT_PRESET",
    "Alarm",
    "CommissionPreset",
    "Counts",
    "EvaluationResult",
    "EvaluationType",
    "Finding",
    "ModelClient",
    "ModelReply",
    "PipelineSpec",
    "Severity",
    "Usage",
    "builtin_presets",
    "evaluate",
    "evaluate_sync",
    "load_preset",
]
