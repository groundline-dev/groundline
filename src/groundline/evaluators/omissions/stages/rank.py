"""Rank: grade each fact's severity with the profile's rubric, and order them.

Rank order is severity (critical, supporting, peripheral), then the order the
model listed them in (it's asked to list the most important first). Every fact
appears exactly once; nothing is dropped.
"""

from typing import ClassVar

from groundline.core import (
    InputName,
    ParseError,
    PromptImplementation,
    StageContext,
    as_object,
    get_list,
    parse_json_object,
)
from groundline.evaluators.models import SEVERITIES, Severity
from groundline.evaluators.omissions.models import (
    ExtractOutput,
    Fact,
    RankedFact,
    RankOutput,
)


class RankLLM(PromptImplementation):
    name: ClassVar[str] = "rank.llm"
    step: ClassVar[str] = "rank"
    reads: ClassVar[tuple[str, ...]] = ("extract",)
    uses: ClassVar[tuple[InputName, ...]] = ("source",)
    can_disable: ClassVar[bool] = True

    prompt: str = "rank_v1"
    """The profile prompt to use."""
    rubric: str = "severity_v1"
    """The profile's severity rubric to grade with."""

    def profile_texts(self) -> tuple[str, ...]:
        return (self.prompt, self.rubric)

    async def run(self, ctx: StageContext) -> RankOutput:
        facts = ctx.output("extract", ExtractOutput).facts
        prompt = ctx.fill(
            ctx.text(self.prompt),
            facts=facts_block(facts),
            rubric=ctx.text(self.rubric),
        )
        return await self.ask(ctx, prompt, lambda reply: parse_ranking(reply, facts))

    def passthrough(self, ctx: StageContext) -> RankOutput:
        """Ranking off: extraction order, no severity (every fact weighs 1)."""
        facts = ctx.output("extract", ExtractOutput).facts
        return RankOutput(
            facts=[ranked(f, severity=None, rank=i) for i, f in enumerate(facts, 1)]
        )


def ranked(
    fact: Fact, *, severity: Severity | None, rank: int, rationale: str = ""
) -> RankedFact:
    return RankedFact(
        id=fact.id,
        fact=fact.fact,
        evidence=fact.evidence,
        evidence_found=fact.evidence_found,
        severity=severity,
        rank=rank,
        rationale=rationale,
    )


def facts_block(facts: list[Fact]) -> str:
    return "\n".join(f'{f.id}: {f.fact}  (evidence: "{f.evidence}")' for f in facts)


def parse_ranking(reply: str, facts: list[Fact]) -> RankOutput:
    items = get_list(parse_json_object(reply), "facts")
    if items is None:
        raise ParseError('expected a "facts" list')
    by_id = {f.id: f for f in facts}
    graded: list[tuple[Fact, Severity, str]] = []
    seen: set[str] = set()
    for raw in items:
        item = as_object(raw)
        if item is None:
            raise ParseError('every entry in "facts" must be an object')
        fid = item.get("id")
        if not isinstance(fid, str) or fid not in by_id:
            raise ParseError("an entry has an unknown or missing fact id")
        if fid in seen:
            raise ParseError(f"fact id {fid} appears more than once")
        seen.add(fid)
        severity = SEVERITIES.get(str(item.get("severity", "")).strip().lower())
        if severity is None:
            raise ParseError(f"{fid}: severity is not one of {', '.join(SEVERITIES)}")
        graded.append((by_id[fid], severity, str(item.get("why", "")).strip()))
    missing = [f.id for f in facts if f.id not in seen]
    if missing:
        raise ParseError(f"missing fact ids: {', '.join(missing)}")

    # A stable sort keeps the model's order within each severity.
    order = list(SEVERITIES)
    graded.sort(key=lambda g: order.index(g[1]))
    return RankOutput(
        facts=[
            ranked(fact, severity=severity, rank=i, rationale=why)
            for i, (fact, severity, why) in enumerate(graded, 1)
        ]
    )
