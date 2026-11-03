"""Commissions check: one model call lists the summary's claims that the source
doesn't support.

The model is asked to list only the problems. For each it gives the words in the
summary that make the claim (verified here), whether the source contradicts it
or just doesn't say it, a 0-100 confidence that the source doesn't support it,
how much it would matter if it were wrong, and, for a contradicted claim, the
passage of the source that contradicts it. confidence / 100 is the unsupported
confidence the alarm thresholds apply to.

The reply's own keys (`issues`, `label`, `confidence`, `importance`) are the
prompt's words, copied unchanged from omission-pipeline.
"""

from typing import ClassVar

from groundline.core import (
    Effort,
    InputName,
    ParseError,
    PromptImplementation,
    StageContext,
    as_object,
    evidence_in,
    get_list,
    parse_json_object,
)
from groundline.evaluators.commissions.models import (
    SUPPORT_VERDICTS,
    Commission,
    CommissionsCheckOutput,
)
from groundline.evaluators.models import SEVERITIES


class CommissionsCheckLLMQuick(PromptImplementation):
    name: ClassVar[str] = "commissions_check.llm_quick"
    step: ClassVar[str] = "commissions_check"
    uses: ClassVar[tuple[InputName, ...]] = ("source", "summary")

    model: str = "claude-haiku-5-5"
    """The model to call."""
    effort: Effort = "medium"
    """How much effort the model spends: low, medium, high, xhigh or max."""
    prompt: str = "quick_check_v1"
    """The profile prompt to use."""

    def profile_texts(self) -> tuple[str, ...]:
        return (self.prompt,)

    async def run(self, ctx: StageContext) -> CommissionsCheckOutput:
        prompt = ctx.fill(ctx.text(self.prompt))
        return await self.ask(
            ctx,
            prompt,
            lambda reply: parse_commissions(
                reply, summary=ctx.summary, source=ctx.source
            ),
        )


def parse_commissions(
    reply: str, *, summary: str, source: str
) -> CommissionsCheckOutput:
    """The commissions the model listed, each checked. An unusable quote or field is a
    parse error, so the model is asked again."""
    items = get_list(parse_json_object(reply), "issues")
    if items is None:
        raise ParseError('expected an "issues" list')
    commissions: list[Commission] = []
    for number, raw in enumerate(items, 1):
        item = as_object(raw) or {}
        where = f"commission {number}"

        quote = item.get("quote")
        quote = quote.strip() if isinstance(quote, str) else ""
        if not quote or not evidence_in(quote, summary):
            raise ParseError(f"{where}: quote is not in the note")

        verdict = SUPPORT_VERDICTS.get(str(item.get("label", "")).strip().lower())
        if verdict is None:
            raise ParseError(
                f"{where}: label is not one of {', '.join(SUPPORT_VERDICTS)}"
            )

        confidence = item.get("confidence")
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not 0 <= confidence <= 100
        ):
            raise ParseError(f"{where}: confidence is not a number from 0 to 100")

        severity = SEVERITIES.get(str(item.get("importance", "")).strip().lower())
        if severity is None:
            raise ParseError(
                f"{where}: importance is not one of {', '.join(SEVERITIES)}"
            )

        evidence = item.get("evidence")
        evidence = evidence.strip() if isinstance(evidence, str) else None
        if evidence and not evidence_in(evidence, source):
            evidence = None  # a quote that isn't in the source isn't evidence

        commissions.append(
            Commission(
                id=f"q{number}",
                quote=quote,
                support_verdict=verdict,
                unsupported_confidence=round(confidence / 100, 4),
                severity=severity,
                evidence=evidence or None,
            )
        )
    return CommissionsCheckOutput(commissions=commissions)
