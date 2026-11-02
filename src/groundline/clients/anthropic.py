"""The Anthropic Messages API client.

Notes for Claude Opus 5.5:
- Thinking is always on, so `thinking` isn't sent. Effort is the only control,
  and Opus 5.5 defaults to `medium`, so it's always sent explicitly.
- The refusal-fallback beta is deliberately not used: it isn't on Anthropic's
  HIPAA feature-eligibility list, so it isn't covered by the BAA. A refusal is
  raised as an error instead.
"""

import httpx2
from anthropic import AsyncAnthropic

from groundline.core import Effort, ModelError, ModelRefusal, ModelReply


class AnthropicClient:
    def __init__(
        self,
        api_key: str,
        *,
        max_retries: int = 4,
        http_client: httpx2.AsyncClient | None = None,
    ) -> None:
        # The SDK retries connection errors, 408, 409, 429 and 5xx with backoff.
        self._sdk = AsyncAnthropic(
            api_key=api_key, max_retries=max_retries, http_client=http_client
        )

    async def complete(
        self, prompt: str, *, model: str, effort: Effort, max_tokens: int
    ) -> ModelReply:
        message = await self._sdk.messages.create(
            model=model,
            max_tokens=max_tokens,
            output_config={"effort": effort},
            messages=[{"role": "user", "content": prompt}],
        )
        if message.stop_reason == "refusal":
            details = message.stop_details
            category = (
                details.category if details and details.category else "none given"
            )
            raise ModelRefusal(
                f"{message.model} declined to answer (refusal category: {category})"
            )
        if message.stop_reason == "max_tokens":
            raise ModelError(
                f"reply cut off at max_tokens={max_tokens}; "
                "raise the stage's max_tokens"
            )
        text = "".join(block.text for block in message.content if block.type == "text")
        return ModelReply(
            text=text,
            model=message.model,
            input_tokens=message.usage.input_tokens,
            output_tokens=message.usage.output_tokens,
        )

    async def close(self) -> None:
        await self._sdk.close()
