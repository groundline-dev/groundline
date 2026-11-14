"""The interface stages use to call a model.

Stages depend only on this interface, never on a provider's SDK. The real
client lives in `groundline_core.clients`; tests use a scripted fake.
"""

from typing import Literal, Protocol

from pydantic import BaseModel

Effort = Literal["low", "medium", "high", "xhigh", "max"]


class ModelReply(BaseModel):
    text: str
    model: str
    """The model that actually answered."""
    input_tokens: int
    output_tokens: int


class ModelError(Exception):
    """The model call finished but its reply can't be used.

    `reply` is set when the call was still billed, such as a refusal or a reply
    cut off at max_tokens, so that it appears in the cost report."""

    def __init__(self, message: str, *, reply: ModelReply | None = None) -> None:
        super().__init__(message)
        self.reply = reply


class ModelRefusal(ModelError):
    """The model declined to answer."""


class ModelClient(Protocol):
    async def complete(
        self, prompt: str, *, model: str, effort: Effort, max_tokens: int
    ) -> ModelReply: ...
