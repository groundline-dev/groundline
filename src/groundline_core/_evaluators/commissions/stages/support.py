"""Commissions check, lean support: one model call judges every claim from the
claims step against the source.

For each claim the model gives a label (supported, unsupported or contradicted), a
0-100 confidence that the source does NOT support it, and how much it would
matter if it were wrong. The claims the source doesn't support become the
commissions; supported claims are dropped. not_supported_confidence / 100 is the
unsupported confidence the alarm thresholds apply to.

Lean, as omission-pipeline measured it: only a contradicted claim needs a quote
from the source, and that quote must be in the source word for word, or the model
is asked again. A supported claim's quote, if it gives one, is never checked.
With no claims there is no call.

The reply's keys (`claims`, `label`, `evidence`, `not_supported_confidence`,
`importance`) are the prompt's words, copied unchanged from omission-pipeline.
"""

from typing import ClassVar

from groundline_core._core import (
    Effort,
    InputName,
    ParseError,
    PromptImplementation,
    StageContext,
    choice,
    evidence_in,
    percent,
    stripped,
)
from groundline_core._evaluators.commissions.models import (
    SUPPORT_VERDICTS,
    Claim,
    Commission,
    CommissionsCheckOutput,
    CommissionsClaimsOutput,
)
from groundline_core._evaluators.commissions.stages.refute import entries_by_id
from groundline_core._evaluators.models import SEVERITIES

VERDICTS = {"supported": "supported", **SUPPORT_VERDICTS}
"""The verdicts the model may give: the reply calls them labels."""


class CommissionsCheckLLMSupport(PromptImplementation):
    name: ClassVar[str] = "commissions_check.llm_support"
    step: ClassVar[str] = "commissions_check"
    reads: ClassVar[tuple[str, ...]] = ("commissions_claims",)
    uses: ClassVar[tuple[InputName, ...]] = ("source",)

    effort: Effort = "low"
    """How much effort the model spends: low, medium, high, xhigh or max."""
    prompt: str = "support_check_lean_v1"
    """The profile prompt to use."""

    def profile_texts(self) -> tuple[str, ...]:
        return (self.prompt,)

    async def run(self, ctx: StageContext) -> CommissionsCheckOutput:
        claims = ctx.output("commissions_claims", CommissionsClaimsOutput).claims
        if not claims:
            return CommissionsCheckOutput(commissions=[])
        block = "\n".join(f'{c.id}: {c.text}  (note: "{c.quote}")' for c in claims)
        prompt = ctx.fill(ctx.text(self.prompt), claims=block)
        return await self.ask(
            ctx, prompt, lambda reply: parse_support_check(reply, claims, ctx.source)
        )


def parse_support_check(
    reply: str, claims: list[Claim], source: str
) -> CommissionsCheckOutput:
    """The claims the source doesn't support, in the claims' order. Any unusable
    entry, or a missing, unknown or repeated id, is a parse error."""
    entries = entries_by_id(reply, [c.id for c in claims])
    commissions: list[Commission] = []
    for claim in claims:
        item = entries[claim.id]

        verdict = choice(item, "label", VERDICTS)
        if verdict is None:
            raise ParseError(f"{claim.id}: label is not one of {', '.join(VERDICTS)}")

        evidence = stripped(item.get("evidence"))
        if verdict == "contradicted" and (
            evidence is None or not evidence_in(evidence, source)
        ):
            raise ParseError(
                f"{claim.id}: a contradicted claim needs evidence quoted exactly "
                "from the source"
            )

        confidence = percent(item, "not_supported_confidence", claim.id)

        severity = choice(item, "importance", SEVERITIES)
        if severity is None:
            raise ParseError(
                f"{claim.id}: importance is not one of {', '.join(SEVERITIES)}"
            )

        if verdict == "supported":
            continue
        commissions.append(
            Commission(
                id=claim.id,
                quote=claim.quote,
                support_verdict=SUPPORT_VERDICTS[verdict],
                unsupported_confidence=confidence,
                severity=severity,
                evidence=evidence,
                evidence_found=evidence_in(evidence, source) if evidence else None,
            )
        )
    return CommissionsCheckOutput(commissions=commissions)
