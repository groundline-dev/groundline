"""Presentation helpers shared by every evaluator's report."""

from typing import Any

# The run's own usage, which also counts cached calls and models with no price:
# more than the public `Usage` in a result reports.
from groundline_core._core import Usage as RunUsage
from groundline_core._result import EvaluationResult


def row(label: str, value: str, *, width: int) -> str:
    """A report line: a label padded to `width`, then its value."""
    return f"{label:<{width}}{value}"


def number(value: float | None) -> str:
    """Two decimals, or three when needed: 0.90, 0.95, 0.425. A dash for None."""
    if value is None:
        return "-"
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


def result_line(result: EvaluationResult) -> str:
    """Whether the summary was flagged, and the alarm score against its threshold."""
    alarm = result.alarm
    outcome = "FLAGGED" if result.flagged else "NOT FLAGGED"
    compare = "≥" if result.flagged else "<"
    return (
        f"{outcome} (alarm {number(alarm.score)} {compare} "
        f"{number(alarm.threshold)}, {alarm.level})"
    )


def rescaled_score_text(result: EvaluationResult) -> str:
    """The rescaled score, with no trailing zeros, and how to read it."""
    return f"{result.score:g} (higher is better; below 0.5 is flagged)"


def cost_line(result: EvaluationResult, seconds: float) -> str:
    """What the run cost: dollars, calls, tokens and time."""
    return usage_text(result.run.usage, seconds)


def usage_text(usage: RunUsage, seconds: float | None = None) -> str:
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
    return {
        "pipeline": result.pipeline,
        "profile": result.profile,
        "validated_preset": result.validated_preset,
        "warnings": result.warnings,
        "flagged": result.flagged,
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
