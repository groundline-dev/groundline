"""Presentation helpers shared by every evaluator's report."""

from typing import Any

from groundline.evaluate import EvaluationResult
from groundline.evaluators.models import AlarmOutput


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


def cost_line(result: EvaluationResult, seconds: float) -> str:
    """What the run cost: dollars, calls, tokens and time."""
    usage = result.run.usage
    calls = f"{usage.calls} calls"
    if usage.cached_calls:
        calls += f" ({usage.cached_calls} cached)"
    cost = (
        f"{format_cost(usage.cost_usd)} · {calls} · {usage.input_tokens:,} input / "
        f"{usage.output_tokens:,} output tokens · {seconds:.1f} s"
    )
    if usage.unpriced_models:
        cost += f" (no price for {', '.join(usage.unpriced_models)})"
    return cost


def result_json(result: EvaluationResult) -> dict[str, Any]:
    """Everything about a run, for `--json`. It includes text from the source and
    summary (facts, claims and quotes), so treat the file as sensitive."""
    alarm = result.run.output("alarm", AlarmOutput)
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
