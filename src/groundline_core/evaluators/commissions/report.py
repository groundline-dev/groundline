"""Presenting a commission check: a terminal report."""

from groundline_core.evaluators.commissions.models import CommissionsRefuteOutput
from groundline_core.evaluators.models import ALARM_STEP
from groundline_core.evaluators.report import (
    cost_line,
    number,
    pipeline_line,
    rescaled_score_text,
    result_line,
    row,
)
from groundline_core.result import EvaluationResult, Finding

REQUIRED_STEPS = ("commissions_refute", ALARM_STEP)
"""The report reads these steps' outputs, so a pipeline needs them."""

LABEL_WIDTH = 18

# Where a claim's text starts: after the severity, verdict and confidence columns.
CLAIM_INDENT = " " * (2 + 10 + 2 + 12 + 2 + 10 + 2)


def render_report(
    result: EvaluationResult, *, source: str, summary: str, seconds: float
) -> str:
    lines = [
        _row("Commission check", f"{source} · {summary}"),
        _row("Pipeline", pipeline_line(result)),
        _row("Result", result_line(result)),
        _row("Commission score", rescaled_score_text(result)),
    ]
    lines += [_row("Warning", warning) for warning in result.warnings]

    lines += ["", "Not supported by the source (severity order):"]
    if result.findings:
        lines.append(f"  {'severity':<10}  {'verdict':<12}  {'confidence':<10}  claim")
        for finding in result.findings:
            confidence = (
                number(finding.confidence) if finding.confidence is not None else "-"
            )
            lines.append(
                f"  {finding.severity or '-':<10}  {finding.verdict:<12}  "
                f"{confidence:<10}  {finding.statement}"
            )
            if finding.verdict == "contradicted":
                lines.append(f"{CLAIM_INDENT}{_source_line(finding)}")
    else:
        lines.append("  none")

    lines.append("")
    # What refute cleared isn't in the result's fields.
    if result.run.stages["commissions_refute"].status != "disabled":
        refute = result.run.output("commissions_refute", CommissionsRefuteOutput)
        lines.append(_row("Cleared by refute", str(len(refute.cleared))))
    lines.append(_row("Cost", cost_line(result, seconds)))
    return "\n".join(lines)


def _row(label: str, value: str) -> str:
    return row(label, value, width=LABEL_WIDTH)


def _source_line(finding: Finding) -> str:
    """The source passage a contradicted claim conflicts with, and whether it
    could be verified."""
    if not finding.source_evidence:
        return "source: no passage quoted"
    line = f'source: "{finding.source_evidence}"'
    if finding.evidence_found is False:
        line += " (not found word for word in the source)"
    return line
