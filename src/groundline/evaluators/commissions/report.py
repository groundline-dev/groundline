"""Presenting a commission check: a terminal report."""

from groundline.evaluate import EvaluationResult
from groundline.evaluators.commissions.models import CommissionsRefuteOutput
from groundline.evaluators.models import SEVERITIES, AlarmOutput
from groundline.evaluators.report import (
    cost_line,
    number,
    pipeline_line,
    result_line,
    row,
)

REQUIRED_STEPS = ("commissions_refute", "alarm")
"""The report reads these steps' outputs, so a pipeline needs them."""

LABEL_WIDTH = 18

# Where a claim's text starts: after the severity, verdict and confidence columns.
CLAIM_INDENT = " " * (2 + 10 + 2 + 12 + 2 + 10 + 2)


def render_report(
    result: EvaluationResult, *, source: str, summary: str, seconds: float
) -> str:
    refute = result.run.output("commissions_refute", CommissionsRefuteOutput)
    alarm = result.run.output("alarm", AlarmOutput)

    lines = [
        _row("Commission check", f"{source} · {summary}"),
        _row("Pipeline", pipeline_line(result)),
        _row("Result", result_line(alarm)),
    ]
    lines += [_row("Warning", warning) for warning in result.warnings]

    lines += ["", "Not supported by the source (severity order):"]
    if refute.commissions:
        lines.append(f"  {'severity':<10}  {'verdict':<12}  {'confidence':<10}  claim")
        order = list(SEVERITIES)
        for commission in sorted(
            refute.commissions,
            key=lambda c: (order.index(c.severity), -c.unsupported_confidence),
        ):
            lines.append(
                f"  {commission.severity:<10}  {commission.support_verdict:<12}  "
                f"{number(commission.unsupported_confidence):<10}  {commission.quote}"
            )
            if commission.support_verdict == "contradicted" and commission.evidence:
                lines.append(f'{CLAIM_INDENT}source: "{commission.evidence}"')
    else:
        lines.append("  none")

    lines.append("")
    if result.run.stages["commissions_refute"].status != "disabled":
        lines.append(_row("Cleared by refute", str(len(refute.cleared))))
    lines.append(_row("Cost", cost_line(result, seconds)))
    return "\n".join(lines)


def _row(label: str, value: str) -> str:
    return row(label, value, width=LABEL_WIDTH)
