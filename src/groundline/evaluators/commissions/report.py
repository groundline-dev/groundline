"""Presenting a commission check: a terminal report."""

from groundline.evaluators.commissions.models import (
    Commission,
    CommissionsRefuteOutput,
    ranked,
)
from groundline.evaluators.models import ALARM_STEP, AlarmOutput
from groundline.evaluators.report import (
    cost_line,
    number,
    pipeline_line,
    rescaled_score_text,
    result_line,
    row,
)
from groundline.result import EvaluationResult

REQUIRED_STEPS = ("commissions_refute", ALARM_STEP)
"""The report reads these steps' outputs, so a pipeline needs them."""

LABEL_WIDTH = 18

# Where a claim's text starts: after the severity, verdict and confidence columns.
CLAIM_INDENT = " " * (2 + 10 + 2 + 12 + 2 + 10 + 2)


def render_report(
    result: EvaluationResult, *, source: str, summary: str, seconds: float
) -> str:
    refute = result.run.output("commissions_refute", CommissionsRefuteOutput)
    alarm = result.run.output(ALARM_STEP, AlarmOutput)

    lines = [
        _row("Commission check", f"{source} · {summary}"),
        _row("Pipeline", pipeline_line(result)),
        _row("Result", result_line(alarm)),
        _row("Commission score", rescaled_score_text(alarm)),
    ]
    lines += [_row("Warning", warning) for warning in result.warnings]

    lines += ["", "Not supported by the source (severity order):"]
    if refute.commissions:
        lines.append(f"  {'severity':<10}  {'verdict':<12}  {'confidence':<10}  claim")
        for commission in ranked(refute.commissions):
            lines.append(
                f"  {commission.severity:<10}  {commission.support_verdict:<12}  "
                f"{number(commission.unsupported_confidence):<10}  {commission.quote}"
            )
            if commission.support_verdict == "contradicted":
                lines.append(f"{CLAIM_INDENT}{_source_line(commission)}")
    else:
        lines.append("  none")

    lines.append("")
    if result.run.stages["commissions_refute"].status != "disabled":
        lines.append(_row("Cleared by refute", str(len(refute.cleared))))
    lines.append(_row("Cost", cost_line(result, seconds)))
    return "\n".join(lines)


def _row(label: str, value: str) -> str:
    return row(label, value, width=LABEL_WIDTH)


def _source_line(commission: Commission) -> str:
    """The source passage a contradicted claim conflicts with, and whether it
    could be verified."""
    if not commission.evidence:
        return "source: no passage quoted"
    line = f'source: "{commission.evidence}"'
    if commission.evidence_found is False:
        line += " (not found word for word in the source)"
    return line
