"""Presenting an omission check: a terminal report and a JSON-ready result."""

from typing import Any

from groundline.evaluate import EvaluationResult
from groundline.evaluators.omissions.models import AlarmOutput, ScoreOutput

REQUIRED_STEPS = ("score", "alarm")
"""The report reads these steps' outputs, so a pipeline needs them."""

LABEL_WIDTH = 17


def render_report(
    result: EvaluationResult, *, source: str, summary: str, seconds: float
) -> str:
    score = result.run.output("score", ScoreOutput)
    alarm = result.run.output("alarm", AlarmOutput)

    validated = (
        "validated preset" if result.validated_preset else "not a validated preset"
    )
    pipeline = result.pipeline
    if result.profile:
        pipeline += f" · {result.profile} profile"
    outcome = "FLAGGED" if alarm.flagged else "NOT FLAGGED"
    compare = "≥" if alarm.flagged else "<"

    lines = [
        _row("Omission check", f"{source} · {summary}"),
        _row("Pipeline", f"{pipeline} ({validated})"),
        _row(
            "Result",
            f"{outcome} (alarm {_number(alarm.score)} {compare} "
            f"{_number(alarm.threshold)}, {alarm.level})",
        ),
    ]
    lines += [_row("Warning", warning) for warning in result.warnings]

    lines += ["", "Not fully recorded (rank order):"]
    if score.missing_facts:
        lines.append(f"  {'severity':<10}  {'verdict':<7}  {'p_missing':<9}  fact")
        for m in score.missing_facts:
            p = _number(m.score) if m.score is not None else "-"
            lines.append(f"  {m.severity or '-':<10}  {m.verdict:<7}  {p:<9}  {m.fact}")
    else:
        lines.append("  none")

    checks: list[str] = []
    if score.quote_not_found:
        checks.append("quote not found in summary: " + ", ".join(score.quote_not_found))
    if score.evidence_not_found:
        checks.append(
            "evidence not found in source: " + ", ".join(score.evidence_not_found)
        )

    usage = result.run.usage
    calls = f"{usage.calls} calls"
    if usage.cached_calls:
        calls += f" ({usage.cached_calls} cached)"
    cost = (
        f"${usage.cost_usd:.2f} · {calls} · {usage.input_tokens:,} input / "
        f"{usage.output_tokens:,} output tokens · {seconds:.1f} s"
    )
    if usage.unpriced_models:
        cost += f" (no price for {', '.join(usage.unpriced_models)})"

    lines += [
        "",
        f"{score.total} facts: {score.present} present · {score.partial} partial · "
        f"{score.absent} absent",
    ]
    if checks:
        lines.append(_row("Checks", " · ".join(checks)))
    lines.append(_row("Cost", cost))
    return "\n".join(lines)


def result_json(result: EvaluationResult) -> dict[str, Any]:
    """Everything about a run, for `--json`. It includes text from the source and
    summary (facts and quotes), so treat the file as sensitive."""
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


def _row(label: str, value: str) -> str:
    return f"{label:<{LABEL_WIDTH}}{value}"


def _number(value: float) -> str:
    """Two decimals, or three when needed: 0.90, 0.95, 0.425."""
    text = f"{value:.3f}"
    return text[:-1] if text.endswith("0") else text
