"""What `evaluate` returns. Kept apart from `evaluate` so that the evaluators'
reports can use it without importing the module that imports them."""

from dataclasses import dataclass, field

from groundline_core.core import RunResult


@dataclass(frozen=True)
class EvaluationResult:
    pipeline: str
    profile: str | None
    validated_preset: str | None
    """The built-in preset this run matched exactly (ignoring selectable
    settings), or None if the pipeline or profile was changed."""
    run: RunResult
    """Every stage's output, status, model calls, usage and cost."""
    warnings: list[str] = field(default_factory=list[str])
