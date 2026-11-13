"""Commissions check: one model call lists the summary's claims that the source
doesn't support.

The model is asked to list only the problems. For each it gives the words in the
summary that make the claim (verified here), whether the source contradicts it
or just doesn't say it, a 0-100 confidence that the source doesn't support it,
how much it would matter if it were wrong, and, for a contradicted claim, the
passage of the source that contradicts it, which is kept even when it isn't found
word for word in the source, and marked. confidence / 100 is the unsupported
confidence the alarm thresholds apply to.

The reply's own keys (`issues`, `label`, `confidence`, `importance`) are the
prompt's words, copied unchanged from omission-pipeline.
"""

from typing import ClassVar

from groundline_core.core import (
    Effort,
    InputName,
    ParseError,
    PromptImplementation,
    StageContext,
    as_object,
    choice,
    evidence_in,
    percent,
    reply_list,
    stripped,
)
from groundline_core.evaluators.commissions.models import (
    SUPPORT_VERDICTS,
    Commission,
    CommissionsCheckOutput,
)
from groundline_core.evaluators.models import SEVERITIES


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
    commissions: list[Commission] = []
    for number, raw in enumerate(reply_list(reply, "issues"), 1):
        item = as_object(raw) or {}
        where = f"commission {number}"

        quote = stripped(item.get("quote"))
        if quote is None or not evidence_in(quote, summary):
            raise ParseError(f"{where}: quote is not in the note")

        verdict = choice(item, "label", SUPPORT_VERDICTS)
        if verdict is None:
            raise ParseError(
                f"{where}: label is not one of {', '.join(SUPPORT_VERDICTS)}"
            )

        confidence = percent(item, "confidence", where)

        severity = choice(item, "importance", SEVERITIES)
        if severity is None:
            raise ParseError(
                f"{where}: importance is not one of {', '.join(SEVERITIES)}"
            )

        evidence = stripped(item.get("evidence"))

        commissions.append(
            Commission(
                id=f"q{number}",
                quote=quote,
                support_verdict=verdict,
                unsupported_confidence=confidence,
                severity=severity,
                evidence=evidence,
                evidence_found=evidence_in(evidence, source) if evidence else None,
            )
        )
    return CommissionsCheckOutput(commissions=commissions)
