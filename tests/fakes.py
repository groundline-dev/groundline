"""A scripted model client for offline tests: no network, no cost."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from groundline.core import Effort, ModelReply


@dataclass(frozen=True)
class FakeRequest:
    prompt: str
    model: str
    effort: Effort
    max_tokens: int


class FakeClient:
    """Replies with scripted text, in order, or computed from the prompt.

    Token counts are word counts. Every request is kept in `requests`.
    """

    def __init__(self, replies: Sequence[str] | Callable[[str], str]) -> None:
        self._replies = replies if callable(replies) else list(replies)
        self.requests: list[FakeRequest] = []

    async def complete(
        self, prompt: str, *, model: str, effort: Effort, max_tokens: int
    ) -> ModelReply:
        self.requests.append(FakeRequest(prompt, model, effort, max_tokens))
        if callable(self._replies):
            text = self._replies(prompt)
        else:
            assert self._replies, (
                f"FakeClient ran out of replies (call {len(self.requests)})"
            )
            text = self._replies.pop(0)
        return ModelReply(
            text=text,
            model=model,
            input_tokens=len(prompt.split()),
            output_tokens=len(text.split()),
        )
