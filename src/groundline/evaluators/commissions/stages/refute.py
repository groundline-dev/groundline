"""Commissions refute: a second look at the critical flags, which can only clear one.

The critical commissions the check found go, in one call, to a model asked to find
support for each in the source. A commission is cleared only when the model says the
source supports the claim and quotes a passage that really is in the source.
Nothing is ever made worse. Commissions that aren't critical are left alone, since
only critical ones move the alarm. With no critical commission there is no call.
"""

import json
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
    ClearedCommission,
    Commission,
    CommissionsCheckOutput,
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
        ids = {f"q{n}": c for n, c in enumerate(critical, 1)}
        block = json.dumps(
            [
                {
                    "id": qid,
                    "claim": c.quote,
                    "note_quote": c.quote,
                    "label": c.support_verdict,
                }
                for qid, c in ids.items()
            ]
        )
        prompt = ctx.fill(ctx.text(self.prompt), flagged_claims=block)
        support = await self.ask(
            ctx,
            prompt,
            lambda reply: parse_support(reply, list(ids), source=ctx.source),
        )
        return apply_support(found, ids, support)

    def passthrough(self, ctx: StageContext) -> CommissionsRefuteOutput:
        found = ctx.output("commissions_check", CommissionsCheckOutput).commissions
        return CommissionsRefuteOutput(commissions=found, cleared=[])


def parse_support(reply: str, ids: list[str], *, source: str) -> dict[str, str | None]:
    """For each flagged id, the source passage that supports the claim, or None
    if the flag stands."""
    items = get_list(parse_json_object(reply), "claims")
    if items is None:
        raise ParseError('expected a "claims" list')
    found: dict[str, str | None] = {}
    for raw in items:
        item = as_object(raw)
        if item is None or item.get("id") not in ids or item["id"] in found:
            raise ParseError("an entry has an unknown, missing or repeated id")
        qid: str = item["id"]
        if item.get("supported") is True:
            evidence = item.get("evidence")
            evidence = evidence.strip() if isinstance(evidence, str) else ""
            if not evidence or not evidence_in(evidence, source):
                raise ParseError(
                    f"{qid}: clearing a flag needs evidence quoted exactly "
                    "from the source"
                )
            found[qid] = evidence
        else:
            found[qid] = None
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
