"""Commissions refute: a second look at the critical flags, which can only clear one.

The critical issues the check found go, in one call, to a model asked to find
support for each in the source. An issue is cleared only when the model says the
source supports the claim and quotes a passage that really is in the source.
Nothing is ever made worse. Issues that aren't critical are left alone, since
only critical ones move the alarm. With no critical issue there is no call.
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
    ClearedIssue,
    CommissionsCheckOutput,
    CommissionsRefuteOutput,
    Issue,
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
        issues = ctx.output("commissions_check", CommissionsCheckOutput).issues
        critical = [i for i in issues if i.severity == "critical"]
        if not critical:
            return CommissionsRefuteOutput(issues=issues, cleared=[])

        # The model sees its own numbering, q1, q2, ... over the critical issues.
        ids = {f"q{n}": issue for n, issue in enumerate(critical, 1)}
        block = json.dumps(
            [
                {
                    "id": qid,
                    "claim": issue.quote,
                    "note_quote": issue.quote,
                    "label": issue.verdict,
                }
                for qid, issue in ids.items()
            ]
        )
        prompt = ctx.fill(ctx.text(self.prompt), flagged_claims=block)
        support = await self.ask(
            ctx,
            prompt,
            lambda reply: parse_support(reply, list(ids), source=ctx.source),
        )
        return apply_support(issues, ids, support)

    def passthrough(self, ctx: StageContext) -> CommissionsRefuteOutput:
        issues = ctx.output("commissions_check", CommissionsCheckOutput).issues
        return CommissionsRefuteOutput(issues=issues, cleared=[])


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
    issues: list[Issue], ids: dict[str, Issue], support: dict[str, str | None]
) -> CommissionsRefuteOutput:
    """Remove the issues refute found support for, keeping the support."""
    cleared = [
        ClearedIssue(issue=ids[qid], evidence=evidence)
        for qid, evidence in support.items()
        if evidence is not None
    ]
    gone = {c.issue.id for c in cleared}
    return CommissionsRefuteOutput(
        issues=[i for i in issues if i.id not in gone], cleared=cleared
    )
