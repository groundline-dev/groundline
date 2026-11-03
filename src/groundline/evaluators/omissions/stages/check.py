"""Check: one model call decides, for every fact, how fully the summary records it.

For each fact the model answers present, partial or absent, quotes the summary
passage that records it (verified here), and gives a 0-100 confidence that the
summary does not fully record it. score = confidence / 100, which is what the
alarm thresholds apply to.
"""

from typing import Any, ClassVar

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
from groundline.evaluators.omissions.models import (
    VERDICT_LABELS,
    CheckOutput,
    RankedFact,
    RankOutput,
    Verdict,
)


class CheckLLMBatch(PromptImplementation):
    name: ClassVar[str] = "check.llm_batch"
    step: ClassVar[str] = "check"
    reads: ClassVar[tuple[str, ...]] = ("rank",)
    uses: ClassVar[tuple[InputName, ...]] = ("summary",)

    prompt: str = "check_batch_v1"
    """The profile prompt to use."""
    effort: Effort = "medium"
    """How much effort the model spends: low, medium, high, xhigh or max."""

    def profile_texts(self) -> tuple[str, ...]:
        return (self.prompt,)

    async def run(self, ctx: StageContext) -> CheckOutput:
        facts = ctx.output("rank", RankOutput).facts
        block = "\n".join(f"{f.id}: {f.fact}" for f in facts)
        prompt = ctx.fill(ctx.text(self.prompt), facts=block)
        return await self.ask(
            ctx, prompt, lambda reply: parse_batch_verdicts(reply, facts, ctx.summary)
        )


def parse_batch_verdicts(
    reply: str, facts: list[RankedFact], summary: str
) -> CheckOutput:
    """One verdict per fact, in rank order; every fact exactly once."""
    items = get_list(parse_json_object(reply), "facts")
    if items is None:
        raise ParseError('expected a "facts" list')
    by_id: dict[str, dict[str, Any]] = {}
    for raw in items:
        item = as_object(raw)
        if item is not None and isinstance(item.get("id"), str):
            if item["id"] in by_id:
                known = any(f.id == item["id"] for f in facts)
                name = item["id"] if known else "an unknown fact id"
                raise ParseError(f"{name} appears twice")
            by_id[item["id"]] = item
    missing = [f.id for f in facts if f.id not in by_id]
    if missing:
        raise ParseError(f"no verdict for {', '.join(missing)}")
    return CheckOutput(verdicts=[_verdict(f.id, by_id[f.id], summary) for f in facts])


def _verdict(fact_id: str, item: dict[str, Any], summary: str) -> Verdict:
    label = VERDICT_LABELS.get(str(item.get("verdict", "")).strip().lower())
    if label is None:
        raise ParseError(
            f"{fact_id}: verdict is not one of {', '.join(VERDICT_LABELS)}"
        )
    raw_quote = item.get("quote")
    quote = (
        raw_quote.strip() if isinstance(raw_quote, str) and raw_quote.strip() else None
    )
    if label != "absent" and quote is None:
        raise ParseError(f'{fact_id}: a "{label}" verdict needs a quote from the note')
    confidence = item.get("missing_confidence")
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not 0 <= confidence <= 100
    ):
        raise ParseError(f"{fact_id}: missing_confidence is not a number from 0 to 100")
    return Verdict(
        fact_id=fact_id,
        label=label,
        quote=quote,
        quote_found=evidence_in(quote, summary) if quote else None,
        score=round(confidence / 100, 4),
    )
