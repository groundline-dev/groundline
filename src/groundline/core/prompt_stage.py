"""Shared plumbing for stages that send a prompt to a model and parse the reply."""

from collections.abc import Callable

from groundline.core.implementation import Implementation, StageContext
from groundline.core.model_client import Effort
from groundline.core.parsing import ParseError

RETRY_NOTE = (
    "\n\nYour previous reply could not be used: {error}. "
    "Reply again with only the JSON object described above."
)


class ReplyError(Exception):
    """The model's reply was still unusable after one retry."""


def fill(template: str, **values: str) -> str:
    """Substitute `{name}` placeholders. Other braces, such as JSON examples in a
    prompt, are left alone."""
    for name, value in values.items():
        template = template.replace("{" + name + "}", value)
    return template


class PromptImplementation(Implementation):
    """Base for implementations that call a model. Its settings choose the model."""

    model: str = "claude-opus-5-5"
    effort: Effort = "high"
    max_tokens: int = 16000

    async def ask[T](
        self, ctx: StageContext, prompt: str, parse: Callable[[str], T]
    ) -> T:
        """Call the model and parse its reply. If the reply can't be parsed, ask
        once more with the problem appended to the prompt."""
        reply = await self._complete(ctx, prompt)
        try:
            return parse(reply)
        except ParseError as first:
            reply = await self._complete(ctx, prompt + RETRY_NOTE.format(error=first))
            try:
                return parse(reply)
            except ParseError as second:
                raise ReplyError(
                    f"unusable reply after 1 retry: {second}. "
                    f"Reply began: {reply[:200]!r}"
                ) from second

    async def _complete(self, ctx: StageContext, prompt: str) -> str:
        return await ctx.complete(
            prompt, model=self.model, effort=self.effort, max_tokens=self.max_tokens
        )
