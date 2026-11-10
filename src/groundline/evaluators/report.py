"""Presentation helpers shared by every evaluator's report."""

from collections.abc import Sequence
from typing import Any

from groundline.core import PipelineSpec, Usage
from groundline.evaluators.models import ALARM_STEP, AlarmOutput
from groundline.result import EvaluationResult


def require_steps(spec: PipelineSpec, steps: Sequence[str], *, report: str) -> None:
    """Raise a ValueError, before anything runs, if `spec` lacks a step that the
    `report` reads (each evaluator's report module lists them as
    `REQUIRED_STEPS`)."""
    have = {stage.step for stage in spec.stages}
    missing = [step for step in steps if step not in have]
    if missing:
        needs = " and ".join(f"'{step}'" for step in steps)
        raise ValueError(
            f"pipeline '{spec.name}' needs {needs} stages "
            f"for the {report} report (missing: {', '.join(missing)})"
        )


def row(label: str, value: str, *, width: int) -> str:
    """A report line: a label padded to `width`, then its value."""
    return f"{label:<{width}}{value}"


def number(value: float) -> str:
    """Two decimals, or three when needed: 0.90, 0.95, 0.425."""
    text = f"{value:.3f}"
    return text[:-1] if text.endswith("0") else text


def format_cost(dollars: float) -> str:
    """A dollar amount for a report: cents normally, but four decimals under a
    cent, so a cheap run doesn't read as free. Exactly zero (a fully cached run)
    stays $0.00, and anything too small for four decimals says so."""
    if dollars == 0 or dollars >= 0.01:
        return f"${dollars:.2f}"
    if dollars < 0.00005:
        return "<$0.0001"
    return f"${dollars:.4f}"


def pipeline_line(result: EvaluationResult) -> str:
    """The pipeline's name, its profile, and whether it is a validated preset."""
    validated = (
        "validated preset" if result.validated_preset else "not a validated preset"
    )
    pipeline = result.pipeline
    if result.profile:
        pipeline += f" · {result.profile} profile"
    return f"{pipeline} ({validated})"


def result_line(alarm: AlarmOutput) -> str:
    """Whether the summary was flagged, and the alarm score against its threshold."""
    outcome = "FLAGGED" if alarm.flagged else "NOT FLAGGED"
    compare = "≥" if alarm.flagged else "<"
    return (
        f"{outcome} (alarm {number(alarm.score)} {compare} "
        f"{number(alarm.threshold)}, {alarm.level})"
    )


def rescaled_score_text(alarm: AlarmOutput) -> str:
    """The rescaled score and how to read it. Up to four decimals with no trailing
    zeros: fewer decimals could round a flagged 0.4999 up to 0.5."""
    score = f"{alarm.rescaled_score:.4f}".rstrip("0").rstrip(".")
    return f"{score} (higher is better; below 0.5 is flagged)"


def cost_line(result: EvaluationResult, seconds: float) -> str:
    """What the run cost: dollars, calls, tokens and time."""
    return usage_text(result.run.usage, seconds)


def usage_text(usage: Usage, seconds: float | None = None) -> str:
    """Dollars, calls and tokens, then the time if given."""
    calls = f"{usage.calls} calls"
    if usage.cached_calls:
        calls += f" ({usage.cached_calls} cached)"
    text = (
        f"{format_cost(usage.cost_usd)} · {calls} · {usage.input_tokens:,} input / "
        f"{usage.output_tokens:,} output tokens"
    )
    if seconds is not None:
        text += f" · {seconds:.1f} s"
    if usage.unpriced_models:
        text += f" (no price for {', '.join(usage.unpriced_models)})"
    return text


def result_json(result: EvaluationResult) -> dict[str, Any]:
    """Everything about a run, for `--json`. It includes text from the source and
    summary (facts, claims and quotes), so treat the file as sensitive."""
    alarm = result.run.output(ALARM_STEP, AlarmOutput)
    return {
        "pipeline": result.pipeline,
        "profile": result.profile,
        "validated_preset": result.validated_preset,
        "warnings": result.warnings,
        "flagged": alarm.flagged,
        "outputs": {
            step: output.model_dump(mode="json")
            for step, output in result.run.outputs.items()
        },
        "stages": {
            step: {"status": run.status, "seconds": run.seconds}
            for step, run in result.run.stages.items()
        },
        "calls": [call.model_dump() for call in result.run.calls],
        "usage": result.run.usage.model_dump(),
    }
