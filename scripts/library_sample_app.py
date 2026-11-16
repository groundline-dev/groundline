# pyright: strict
"""A small app that uses groundline only through its public names, the way a
caller would. `make core-check` type-checks it with pyright strict against the
package installed with no extras, so a public name or result field whose type
a caller can't see fails the check. It is never run: a real check needs an
Anthropic API key."""

import asyncio
import os

from groundline_core import (
    DEFAULT_COMMISSIONS_PRESET,
    DEFAULT_PRESET,
    Alarm,
    CommissionPreset,
    Counts,
    EvaluationResult,
    EvaluationType,
    Finding,
    ModelClient,
    ModelReply,
    PipelineSpec,
    Severity,
    Usage,
    builtin_presets,
    evaluate,
    evaluate_sync,
    load_preset,
)


class CannedModel:
    """A stand-in model for an app's own tests, so they cost nothing. `effort` is
    a plain `str` here: a caller needn't import groundline's own effort type."""

    def __init__(self, reply: str) -> None:
        self.reply = reply

    async def complete(
        self, prompt: str, *, model: str, effort: str, max_tokens: int
    ) -> ModelReply:
        return ModelReply(
            text=self.reply,
            model=model,
            input_tokens=len(prompt),
            output_tokens=min(len(self.reply), max_tokens),
        )


def severity_rank(severity: Severity | None) -> int:
    return ["critical", "supporting", "peripheral", None].index(severity)


def describe_finding(finding: Finding) -> str:
    confidence = "-" if finding.confidence is None else f"{finding.confidence:.2f}"
    found = {True: "verified", False: "unverified", None: "-"}[finding.evidence_found]
    return " · ".join(
        [
            finding.type,
            finding.severity or "-",
            finding.verdict,
            confidence,
            finding.statement,
            finding.source_evidence or "-",
            finding.summary_evidence or "-",
            found,
            finding.explanation,
        ]
    )


def describe_counts(counts: Counts | None) -> str:
    if counts is None:
        return "no fact counts"
    return (
        f"{counts.total} facts: {counts.present} present, "
        f"{counts.partial} partial, {counts.absent} absent"
    )


def describe_alarm(alarm: Alarm) -> str:
    return f"alarm {alarm.score} against {alarm.threshold} ({alarm.level})"


def describe_usage(usage: Usage) -> str:
    return (
        f"{usage.calls} calls, {usage.input_tokens} in, "
        f"{usage.output_tokens} out, ${usage.cost_usd:.4f}"
    )


def describe(result: EvaluationResult) -> list[str]:
    check: EvaluationType = result.evaluation_type
    validated: str | None = result.validated_preset
    profile: str | None = result.profile
    lines = [
        f"{check} check by {result.pipeline} ({profile}, {validated})",
        f"flagged: {result.flagged}, score {result.score:.2f}",
        describe_alarm(result.alarm),
        describe_counts(result.counts),
        describe_usage(result.usage),
    ]
    findings = sorted(result.findings, key=lambda f: severity_rank(f.severity))
    lines += [describe_finding(finding) for finding in findings]
    lines += [f"warning: {warning}" for warning in result.warnings]
    return lines


def commission_check(preset: CommissionPreset) -> PipelineSpec:
    return load_preset(preset)


async def check_in_async_code(
    source: str, summary: str, client: ModelClient
) -> EvaluationResult:
    return await evaluate(
        source, summary, spec=load_preset(DEFAULT_PRESET), client=client
    )


def main() -> None:
    presets: list[str] = builtin_presets()
    print(presets)

    client: ModelClient = CannedModel("{}")
    omissions = asyncio.run(check_in_async_code("source", "summary", client))
    print("\n".join(describe(omissions)))

    lean = evaluate_sync(
        "source",
        "summary",
        spec=commission_check("commission_reverse_lean"),
        client=client,
    )
    quick = evaluate_sync(
        "source",
        "summary",
        spec=load_preset(DEFAULT_COMMISSIONS_PRESET),
        anthropic_api_key=os.environ["MY_APP_ANTHROPIC_KEY"],
    )
    for result in (lean, quick):
        print("\n".join(describe(result)))


if __name__ == "__main__":
    main()
