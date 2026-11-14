"""Commissions refute: a second look at the flags, which can only clear one.

The commissions go, in one call, to a model asked to find support for each in the
source. A commission is cleared only when the model says the source supports the
claim and quotes a passage that really is in the source. Nothing is ever made
worse. With nothing to send there is no call.

`commissions_refute.llm` (the quick preset's, switched off there) sends only the
critical commissions, renumbered q1, q2, ...: the others are left alone, since only
critical ones move the alarm. `commissions_refute.llm_flagged` (the lean preset's)
sends every commission under the id and statement the claims step gave it, as
omission-pipeline's reverse pipeline does.
"""

import json
from typing import Any, ClassVar

from groundline_core.core import (
    Effort,
    InputName,
    ParseError,
    PromptImplementation,
    StageContext,
    as_object,
    evidence_in,
    reply_list,
    stripped,
)
from groundline_core.evaluators.commissions.models import (
    ClearedCommission,
    Commission,
    CommissionsCheckOutput,
    CommissionsClaimsOutput,
    CommissionsRefuteOutput,
)


class CommissionsRefuteLLM(PromptImplementation):
    name: ClassVar[str] = "commissions_refute.llm"
    step: ClassVar[str] = "commissions_refute"
    reads: ClassVar[tuple[str, ...]] = ("commissions_check",)
    uses: ClassVar[tuple[InputName, ...]] = ("source",)
    can_disable: ClassVar[bool] = True

    model: str = "claude-haiku-5-5"
    """The model to call."""
    effort: Effort = "medium"
    """How much effort the model spends: low, medium, high, xhigh or max."""
    prompt: str = "support_refute_v1"
    """The profile prompt to use."""

    def profile_texts(self) -> tuple[str, ...]:
        return (self.prompt,)

    async def run(self, ctx: StageContext) -> CommissionsRefuteOutput:
        found = ctx.output("commissions_check", CommissionsCheckOutput).commissions
        critical = [c for c in found if c.severity == "critical"]
        if not critical:
            return CommissionsRefuteOutput(commissions=found, cleared=[])

        # The model sees its own numbering, q1, q2, ... over the critical commissions.
        return await self.refute(
            ctx, found, {f"q{n}": (c, c.quote) for n, c in enumerate(critical, 1)}
        )

    async def refute(
        self,
        ctx: StageContext,
        found: list[Commission],
        sent: dict[str, tuple[Commission, str]],
    ) -> CommissionsRefuteOutput:
        """Send the commissions in `sent` (by the id the model sees: the commission
        and the claim text to show for it) in one call, and clear the ones the
        model finds support for."""
        block = json.dumps(
            [
                {
                    "id": sent_id,
                    "claim": claim_text,
                    "note_quote": c.quote,
                    "label": c.support_verdict,
                }
                for sent_id, (c, claim_text) in sent.items()
            ]
        )
        prompt = ctx.fill(ctx.text(self.prompt), flagged_claims=block)
        support = await self.ask(
            ctx,
            prompt,
            lambda reply: parse_support(reply, list(sent), source=ctx.source),
        )
        return apply_support(
            found, {sent_id: c for sent_id, (c, _) in sent.items()}, support
        )

    def passthrough(self, ctx: StageContext) -> CommissionsRefuteOutput:
        found = ctx.output("commissions_check", CommissionsCheckOutput).commissions
        return CommissionsRefuteOutput(commissions=found, cleared=[])


class CommissionsRefuteLLMFlagged(CommissionsRefuteLLM):
    """Refute as the lean preset measured it: every commission the check found,
    whatever its severity, goes to the model under the id and plain statement the
    claims step gave it, so it reads that step too. Clearing works as above."""

    name: ClassVar[str] = "commissions_refute.llm_flagged"
    reads: ClassVar[tuple[str, ...]] = ("commissions_claims", "commissions_check")

    model: str = "claude-opus-5-5"
    """The model to call."""
    effort: Effort = "high"
    """How much effort the model spends: low, medium, high, xhigh or max."""

    async def run(self, ctx: StageContext) -> CommissionsRefuteOutput:
        found = ctx.output("commissions_check", CommissionsCheckOutput).commissions
        if not found:
            return CommissionsRefuteOutput(commissions=found, cleared=[])

        claim_texts = {
            c.id: c.text
            for c in ctx.output("commissions_claims", CommissionsClaimsOutput).claims
        }
        return await self.refute(
            ctx, found, {c.id: (c, claim_texts[c.id]) for c in found}
        )


def parse_support(reply: str, ids: list[str], *, source: str) -> dict[str, str | None]:
    """For each flagged id, the source passage that supports the claim, or None
    if the flag stands."""
    found: dict[str, str | None] = {}
    for qid, item in entries_by_id(reply, ids).items():
        if item.get("supported") is True:
            evidence = stripped(item.get("evidence"))
            if evidence is None or not evidence_in(evidence, source):
                raise ParseError(
                    f"{qid}: clearing a flag needs evidence quoted exactly "
                    "from the source"
                )
            found[qid] = evidence
        else:
            found[qid] = None
    return found


def entries_by_id(reply: str, ids: list[str]) -> dict[str, dict[str, Any]]:
    """The reply's `claims` entries by id: exactly one for each of `ids`, or a
    parse error."""
    found: dict[str, dict[str, Any]] = {}
    for raw in reply_list(reply, "claims"):
        item = as_object(raw)
        if item is None or item.get("id") not in ids or item["id"] in found:
            raise ParseError("an entry has an unknown, missing or repeated id")
        found[item["id"]] = item
    missing = [i for i in ids if i not in found]
    if missing:
        raise ParseError(f"no entry for {', '.join(missing)}")
    return found


def apply_support(
    found: list[Commission],
    ids: dict[str, Commission],
    support: dict[str, str | None],
) -> CommissionsRefuteOutput:
    """Remove the commissions refute found support for, keeping the support."""
    cleared = [
        ClearedCommission(commission=ids[qid], evidence=evidence)
        for qid, evidence in support.items()
        if evidence is not None
    ]
    gone = {c.commission.id for c in cleared}
    return CommissionsRefuteOutput(
        commissions=[c for c in found if c.id not in gone], cleared=cleared
    )
