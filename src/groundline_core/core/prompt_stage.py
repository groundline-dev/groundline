"""Shared plumbing for stages that send a prompt to a model and parse the reply."""

from collections.abc import Callable

from groundline_core.core.implementation import Implementation, StageContext
from groundline_core.core.model_client import Effort
from groundline_core.core.parsing import ParseError

RETRY_NOTE = (
    "\n\nYour previous reply could not be used: {error}. "
    "Reply again with only the JSON object described above."
)


class ReplyError(Exception):
    """The model's reply was still unusable after one retry."""


class PromptImplementation(Implementation):
    """Base for implementations that call a model. Its settings choose the model."""

    model: str = "claude-opus-5-5"
    """The model to call."""
    effort: Effort = "high"
    """How much effort the model spends: low, medium, high, xhigh or max."""
    max_tokens: int = 16000
    """The longest reply allowed, in tokens. A reply cut off here is an error."""

    def models(self) -> tuple[str, ...]:
        return (self.model,)

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
                # Not the reply's text: it can quote the source or summary.
                raise ReplyError(
                    f"unusable reply after 1 retry: {second} "
                    f"(the reply was {len(reply):,} characters)"
                ) from second

    async def _complete(self, ctx: StageContext, prompt: str) -> str:
        return await ctx.complete(
            prompt, model=self.model, effort=self.effort, max_tokens=self.max_tokens
        )
