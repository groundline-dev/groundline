"""Presenting an omission check: a terminal report."""

from groundline_core.evaluators.models import ALARM_STEP, AlarmOutput
from groundline_core.evaluators.omissions.models import ScoreOutput
from groundline_core.evaluators.report import (
    cost_line,
    number,
    pipeline_line,
    rescaled_score_text,
    result_line,
    row,
)
from groundline_core.result import EvaluationResult

REQUIRED_STEPS = ("score", ALARM_STEP)
"""The report reads these steps' outputs, so a pipeline needs them."""

LABEL_WIDTH = 17


def render_report(
    result: EvaluationResult, *, source: str, summary: str, seconds: float
) -> str:
    score = result.run.output("score", ScoreOutput)
    alarm = result.run.output(ALARM_STEP, AlarmOutput)

    lines = [
        _row("Omission check", f"{source} · {summary}"),
        _row("Pipeline", pipeline_line(result)),
        _row("Result", result_line(alarm)),
        _row("Omission score", rescaled_score_text(alarm)),
    ]
    lines += [_row("Warning", warning) for warning in result.warnings]

    lines += ["", "Not fully recorded (rank order):"]
    if score.missing_facts:
        lines.append(f"  {'severity':<10}  {'verdict':<7}  {'p_missing':<9}  fact")
        for m in score.missing_facts:
            p = number(m.score) if m.score is not None else "-"
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

    lines += [
        "",
        f"{score.total} facts: {score.present} present · {score.partial} partial · "
        f"{score.absent} absent",
    ]
    if checks:
        lines.append(_row("Checks", " · ".join(checks)))
    lines.append(_row("Cost", cost_line(result, seconds)))
    return "\n".join(lines)


def _row(label: str, value: str) -> str:
    return row(label, value, width=LABEL_WIDTH)
