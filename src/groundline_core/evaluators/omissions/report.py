"""Presenting an omission check: a terminal report."""

from groundline_core.evaluators.models import ALARM_STEP
from groundline_core.evaluators.omissions.models import ScoreOutput
from groundline_core.evaluators.report import (
    cost_line,
    number,
    pipeline_line,
    rescaled_score_text,
    result_line,
    row,
)
from groundline_core.findings import CHECK_STEPS
from groundline_core.result import EvaluationResult

REQUIRED_STEPS = (*CHECK_STEPS["omissions"], ALARM_STEP)
"""The result and the report read these steps' outputs, so a pipeline needs
them."""

LABEL_WIDTH = 17


def render_report(
    result: EvaluationResult, *, source: str, summary: str, seconds: float
) -> str:
    # The quote and evidence checks aren't in the result's fields.
    score = result.run.output("score", ScoreOutput)

    lines = [
        _row("Omission check", f"{source} · {summary}"),
        _row("Pipeline", pipeline_line(result)),
        _row("Result", result_line(result)),
        _row("Omission score", rescaled_score_text(result)),
    ]
    lines += [_row("Warning", warning) for warning in result.warnings]

    lines += ["", "Not fully recorded (rank order):"]
    if result.findings:
        lines.append(f"  {'severity':<10}  {'verdict':<7}  {'p_missing':<9}  fact")
        for f in result.findings:
            p = number(f.confidence) if f.confidence is not None else "-"
            lines.append(
                f"  {f.severity or '-':<10}  {f.verdict:<7}  {p:<9}  {f.statement}"
            )
    else:
        lines.append("  none")

    checks: list[str] = []
    if score.quote_not_found:
        checks.append("quote not found in summary: " + ", ".join(score.quote_not_found))
    if score.evidence_not_found:
        checks.append(
            "evidence not found in source: " + ", ".join(score.evidence_not_found)
        )

    if result.counts is not None:
        counts = result.counts
        lines += [
            "",
            f"{counts.total} facts: {counts.present} present · "
            f"{counts.partial} partial · {counts.absent} absent",
        ]
    if checks:
        lines.append(_row("Checks", " · ".join(checks)))
    lines.append(_row("Cost", cost_line(result, seconds)))
    return "\n".join(lines)


def _row(label: str, value: str) -> str:
    return row(label, value, width=LABEL_WIDTH)
