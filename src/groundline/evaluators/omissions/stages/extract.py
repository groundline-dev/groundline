"""Extract: list the facts the source establishes, each with a supporting quote."""

from typing import ClassVar

from groundline.core import (
    InputName,
    ParseError,
    PromptImplementation,
    StageContext,
    as_object,
    evidence_in,
    reply_list,
)
from groundline.evaluators.omissions.models import ExtractOutput, Fact


class ExtractLLM(PromptImplementation):
    name: ClassVar[str] = "extract.llm"
    step: ClassVar[str] = "extract"
    uses: ClassVar[tuple[InputName, ...]] = ("source",)

    prompt: str = "extract_coarse_v1"
    """The profile prompt to use."""

    def profile_texts(self) -> tuple[str, ...]:
        return (self.prompt,)

    async def run(self, ctx: StageContext) -> ExtractOutput:
        prompt = ctx.fill(ctx.text(self.prompt))
        return await self.ask(ctx, prompt, lambda reply: parse_facts(reply, ctx.source))


def parse_facts(reply: str, source: str) -> ExtractOutput:
    """Facts from a reply. Ids are reassigned f1..fN in reply order, and each
    evidence quote is checked against the source."""
    facts: list[Fact] = []
    for i, raw in enumerate(reply_list(reply, "facts"), 1):
        item = as_object(raw)
        if item is None:
            raise ParseError(f"facts[{i - 1}] is not an object")
        fact, evidence = item.get("fact"), item.get("evidence")
        if not (
            isinstance(fact, str)
            and fact.strip()
            and isinstance(evidence, str)
            and evidence.strip()
        ):
            raise ParseError(f'facts[{i - 1}] needs non-empty "fact" and "evidence"')
        facts.append(
            Fact(
                id=f"f{i}",
                fact=fact.strip(),
                evidence=evidence.strip(),
                evidence_found=evidence_in(evidence, source),
            )
        )
    return ExtractOutput(facts=facts)
