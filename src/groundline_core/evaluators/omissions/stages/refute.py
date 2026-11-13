"""Refute: a second look at every flagged fact, which can only clear a flag.

Facts the check marked partial or absent go, in one call, to a model asked to
find them in the summary. A verdict changes to present only when the model
quotes at least three words that really are in the summary and says the whole
fact is there. Nothing is ever made worse, so the alarm still fires when part of
a critical fact is missing.
"""

from typing import ClassVar, Literal

from pydantic import BaseModel

from groundline_core.core import (
    InputName,
    ParseError,
    PromptImplementation,
    StageContext,
    as_object,
    choice,
    evidence_in,
    reply_list,
    stripped,
    words,
)
from groundline_core.evaluators.omissions.models import (
    CheckOutput,
    RankOutput,
    RefuteEntry,
    RefuteOutput,
    Verdict,
)

Found = Literal["full", "partial", "no"]
FOUND: dict[str, Found] = {"full": "full", "partial": "partial", "no": "no"}
MIN_QUOTE_WORDS = 3


class RefuteAnswer(BaseModel):
    found: Found
    quote: str | None


class RefuteLLM(PromptImplementation):
    name: ClassVar[str] = "refute.llm"
    step: ClassVar[str] = "refute"
    reads: ClassVar[tuple[str, ...]] = ("rank", "check")
    uses: ClassVar[tuple[InputName, ...]] = ("summary",)
    can_disable: ClassVar[bool] = True

    prompt: str = "refute_v1"
    """The profile prompt to use."""

    def profile_texts(self) -> tuple[str, ...]:
        return (self.prompt,)

    async def run(self, ctx: StageContext) -> RefuteOutput:
        verdicts = ctx.output("check", CheckOutput).verdicts
        facts = {f.id: f for f in ctx.output("rank", RankOutput).facts}
        flagged = [v for v in verdicts if v.label != "present"]
        if not flagged:
            return RefuteOutput(verdicts=verdicts, log=[])

        block = "\n".join(f"{v.fact_id}: {facts[v.fact_id].fact}" for v in flagged)
        prompt = ctx.fill(ctx.text(self.prompt), flagged_facts=block)
        ids = [v.fact_id for v in flagged]
        answers = await self.ask(ctx, prompt, lambda reply: parse_refute(reply, ids))
        return apply_refute(verdicts, answers, ctx.summary)

    def passthrough(self, ctx: StageContext) -> RefuteOutput:
        return RefuteOutput(verdicts=ctx.output("check", CheckOutput).verdicts, log=[])


def parse_refute(reply: str, ids: list[str]) -> dict[str, RefuteAnswer]:
    answers: dict[str, RefuteAnswer] = {}
    for raw in reply_list(reply, "facts"):
        item = as_object(raw)
        if item is None:
            raise ParseError('every entry in "facts" must be an object')
        if item.get("id") not in ids:
            raise ParseError("an entry has an unknown or missing fact id")
        fid: str = item["id"]
        found = choice(item, "found", FOUND)
        if found is None:
            raise ParseError(f"{fid}: found must be one of {', '.join(FOUND)}")
        answers[fid] = RefuteAnswer(found=found, quote=stripped(item.get("quote")))
    missing = [i for i in ids if i not in answers]
    if missing:
        raise ParseError(f"missing fact ids: {', '.join(missing)}")
    return answers


def apply_refute(
    verdicts: list[Verdict], answers: dict[str, RefuteAnswer], summary: str
) -> RefuteOutput:
    """Clear a flag only when refute found the whole fact and its quote checks out."""
    final: list[Verdict] = []
    log: list[RefuteEntry] = []
    for v in verdicts:
        answer = answers.get(v.fact_id)
        if answer is None:
            final.append(v)
            continue
        quote = answer.quote
        verified = (
            quote is not None
            and len(words(quote)) >= MIN_QUOTE_WORDS
            and evidence_in(quote, summary)
        )
        new = v
        if verified and answer.found == "full":
            new = v.model_copy(
                update={"label": "present", "quote": quote, "quote_found": True}
            )
        final.append(new)
        log.append(
            RefuteEntry(
                id=v.fact_id,
                found=answer.found,
                quote=quote,
                quote_found=verified,
                from_label=v.label,
                to_label=new.label,
                flipped=new.label != v.label,
            )
        )
    return RefuteOutput(verdicts=final, log=log)
