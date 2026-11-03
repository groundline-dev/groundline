"""The Anthropic Messages API client.

Notes for Claude Opus 5.5:
- Thinking is always on, so `thinking` isn't sent. Effort is the only control,
  and Opus 5.5 defaults to `medium`, so it's always sent explicitly.
- The refusal-fallback beta is deliberately not used: it isn't on Anthropic's
  HIPAA feature-eligibility list, so it isn't covered by the BAA. A refusal is
  raised as an error instead.
"""

import anthropic
import httpx2
from anthropic import AsyncAnthropic

from groundline.core import (
    APIKeyRejectedError,
    Effort,
    ModelError,
    ModelRefusal,
    ModelReply,
    as_object,
)


class AnthropicClient:
    def __init__(
        self,
        api_key: str,
        *,
        key_source: str = "ANTHROPIC_API_KEY",
        max_retries: int = 4,
        http_client: httpx2.AsyncClient | None = None,
    ) -> None:
        """`key_source` says where the key came from, for error messages."""
        self._key_source = key_source
        # The SDK retries connection errors, 408, 409, 429 and 5xx with backoff.
        self._sdk = AsyncAnthropic(
            api_key=api_key, max_retries=max_retries, http_client=http_client
        )

    async def complete(
        self, prompt: str, *, model: str, effort: Effort, max_tokens: int
    ) -> ModelReply:
        try:
            message = await self._sdk.messages.create(
                model=model,
                max_tokens=max_tokens,
                output_config={"effort": effort},
                messages=[{"role": "user", "content": prompt}],
            )
        except anthropic.AuthenticationError as error:
            raise APIKeyRejectedError(
                f"ANTHROPIC_API_KEY was rejected by the Anthropic API "
                f"({error.status_code}: {_detail(error)}).\n"
                f"  Key from: {self._key_source}\n"
                "  Fix: check the key is complete, hasn't been revoked, "
                "and belongs to the workspace you expect."
            ) from error
        except anthropic.PermissionDeniedError as error:
            raise APIKeyRejectedError(
                f"ANTHROPIC_API_KEY doesn't have access to {model} "
                f"({error.status_code}: {_detail(error)}).\n"
                f"  Key from: {self._key_source}\n"
                "  Fix: use a key from a workspace that can use this model, "
                "or change the stage's model setting."
            ) from error
        text = "".join(block.text for block in message.content if block.type == "text")
        reply = ModelReply(
            text=text,
            model=message.model,
            input_tokens=message.usage.input_tokens,
            output_tokens=message.usage.output_tokens,
        )
        # A refusal or a cut-off reply is still billed, so the errors carry the
        # reply's usage for the cost report.
        if message.stop_reason == "refusal":
            details = message.stop_details
            category = (
                details.category if details and details.category else "none given"
            )
            raise ModelRefusal(
                f"{message.model} declined to answer (refusal category: {category})",
                reply=reply,
            )
        if message.stop_reason == "max_tokens":
            raise ModelError(
                f"reply cut off at max_tokens={max_tokens}; "
                "raise the stage's max_tokens",
                reply=reply,
            )
        return reply

    async def close(self) -> None:
        await self._sdk.close()


def _detail(error: anthropic.APIStatusError) -> str:
    """The API's own explanation, such as "invalid x-api-key", if it gave one."""
    body = as_object(error.body)
    inner = as_object(body.get("error")) if body else None
    message = inner.get("message") if inner else None
    return message if isinstance(message, str) else error.message
