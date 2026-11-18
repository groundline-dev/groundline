"""Commissions claims: one model call lists every claim the summary makes, so the
support check can judge each against the source.

Each claim comes with a short plain statement and the words in the summary that
make it (verified here). The claims are numbered c1, c2, ... in the order listed.

The reply's keys (`claims`, `claim`, `quote`) are the prompt's words, copied
unchanged from omission-pipeline's reverse pipeline.
"""

from typing import ClassVar

from groundline_core._core import (
    InputName,
    ParseError,
    PromptImplementation,
    StageContext,
    as_object,
    evidence_in,
    reply_list,
    stripped,
)
from groundline_core._evaluators.commissions.models import (
    Claim,
    CommissionsClaimsOutput,
)


class CommissionsClaimsLLM(PromptImplementation):
    name: ClassVar[str] = "commissions_claims.llm"
    step: ClassVar[str] = "commissions_claims"
    uses: ClassVar[tuple[InputName, ...]] = ("summary",)

    prompt: str = "note_claims_v1"
    """The profile prompt to use."""

    def profile_texts(self) -> tuple[str, ...]:
        return (self.prompt,)

    async def run(self, ctx: StageContext) -> CommissionsClaimsOutput:
        prompt = ctx.fill(ctx.text(self.prompt))
        return await self.ask(
            ctx, prompt, lambda reply: parse_claims(reply, summary=ctx.summary)
        )


def parse_claims(reply: str, *, summary: str) -> CommissionsClaimsOutput:
    """The claims the model listed. A claim without a statement, or whose quote
    isn't in the summary, is a parse error, so the model is asked again."""
    claims: list[Claim] = []
    for number, raw in enumerate(reply_list(reply, "claims"), 1):
        item = as_object(raw) or {}
        where = f"claim {number}"
        text, quote = stripped(item.get("claim")), stripped(item.get("quote"))
        if text is None or quote is None:
            raise ParseError(f"{where} needs a claim and a quote")
        if not evidence_in(quote, summary):
            raise ParseError(f"{where}: quote is not in the note")
        claims.append(Claim(id=f"c{number}", text=text, quote=quote))
    return CommissionsClaimsOutput(claims=claims)
