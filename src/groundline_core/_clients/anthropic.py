"""The Anthropic Messages API client.

Notes for Claude Opus 5.5:
- Thinking is always on, so `thinking` isn't sent. Effort is the only control,
  and Opus 5.5 defaults to `medium`, so it's always sent explicitly.
- The refusal-fallback beta is deliberately not used: it isn't on Anthropic's
  HIPAA feature-eligibility list, so it isn't covered by the BAA. A refusal is
  raised as an error instead.
"""

import asyncio
import logging
import math
import time
from email.utils import parsedate_to_datetime
from pathlib import Path

import anthropic
import httpx2
from anthropic import AsyncAnthropic

from groundline_core._clients.retry import retry_call
from groundline_core._core import (
    APIKeyRejectedError,
    Effort,
    ModelFailureError,
    ModelRefusal,
    ModelReply,
    ModelTimeoutError,
    SetupError,
    UnusableReplyError,
)
from groundline_core._core.deadline import raise_if_cancelling
from groundline_core._settings import EnvFile, read_model_settings

logger = logging.getLogger("groundline.model")


def _seconds(value: str | None, scale: float = 1) -> float | None:
    try:
        number = float(value) / scale if value is not None else float("nan")
    except ValueError:
        return None
    return number if math.isfinite(number) and number >= 0 else None


def retry_decision(
    error: Exception, *, now: float | None = None
) -> tuple[bool, float | None]:
    if isinstance(error, (ModelTimeoutError, anthropic.APIConnectionError)):
        return True, None
    if not isinstance(error, anthropic.APIStatusError):
        return False, None
    headers = error.response.headers
    transient = error.status_code in (408, 409, 429) or 500 <= error.status_code < 600
    if not transient or headers.get("x-should-retry", "").strip().lower() == "false":
        return False, None
    minimum = _seconds(headers.get("retry-after-ms"), 1000)
    if minimum is None:
        value = headers.get("retry-after")
        minimum = _seconds(value)
        if minimum is None and value:
            try:
                minimum = max(
                    0.0,
                    parsedate_to_datetime(value).timestamp()
                    - (time.time() if now is None else now),
                )
            except (ValueError, TypeError, OverflowError):
                pass
    return True, minimum


def _sdk_failure_text(error: anthropic.APIError) -> str:
    """What the Anthropic API did, named by status and error type. The SDK's own
    message is never quoted: it can repeat parts of the request."""
    if isinstance(error, anthropic.APIStatusError):
        what = f"answered {error.status_code}"
    else:
        what = "failed"
    return f"the Anthropic API {what} ({type(error).__name__})"


class AnthropicClient:
    def __init__(
        self,
        api_key: str,
        *,
        key_path: Path | None = None,
        key_name: str = "ANTHROPIC_API_KEY",
        env_file: EnvFile = None,
        max_retries: int | None = None,
        http_client: httpx2.AsyncClient | None = None,
    ) -> None:
        """Read model policy only; `api_key` is supplied by the caller.

        `key_path` is the `.env` file the key came from, if any, for a rejected
        key's error to point at. `max_retries=N` allows N+1
        total groundline attempts, bounded by model and enclosing check limits.
        SDK retries are disabled so all attempts and waits share those limits.
        """
        self._key_path = key_path
        self._key_name = key_name
        self._settings = read_model_settings(env_file)
        self._max_attempts = (
            (max_retries + 1)
            if max_retries is not None
            else self._settings.model_max_attempts
        )
        # One outage retry layer belongs to groundline.
        self._sdk = AsyncAnthropic(
            api_key=api_key, max_retries=0, http_client=http_client
        )

    def _rejected(self, what: str, fix: str) -> APIKeyRejectedError:
        """The error for a key that was refused. The file the key came from is in
        the error's `path`, never in its message."""
        return APIKeyRejectedError(f"{what}\n  Fix: {fix}", path=self._key_path)

    async def complete(
        self, prompt: str, *, model: str, effort: Effort, max_tokens: int
    ) -> ModelReply:
        settings = self._settings
        attempt = 0

        async def operation():
            nonlocal attempt
            attempt += 1
            started = time.monotonic()
            boundary = asyncio.timeout(settings.model_timeout_seconds)
            try:
                async with boundary:
                    return await self._sdk.messages.create(
                        model=model,
                        max_tokens=max_tokens,
                        output_config={"effort": effort},
                        messages=[{"role": "user", "content": prompt}],
                        timeout=httpx2.Timeout(
                            settings.model_timeout_seconds,
                            connect=settings.model_connect_timeout_seconds,
                        ),
                    )
            except anthropic.APITimeoutError:
                raise ModelTimeoutError("attempt") from None
            except Exception:
                if boundary.expired():
                    raise ModelTimeoutError("attempt") from None
                raise_if_cancelling()
                raise
            finally:
                logger.info(
                    "model attempt=%d duration=%.3f",
                    attempt,
                    time.monotonic() - started,
                )

        def decide(error: Exception) -> tuple[bool, float | None]:
            logger.info(
                "model attempt=%d status=%s",
                attempt,
                error.status_code
                if isinstance(error, anthropic.APIStatusError)
                else type(error).__name__,
            )
            return retry_decision(error)

        try:
            message = await retry_call(
                operation,
                decide=decide,
                max_attempts=self._max_attempts,
                budget_seconds=settings.model_retry_timeout_seconds,
                base_seconds=settings.model_retry_base_seconds,
                cap_seconds=settings.model_retry_cap_seconds,
            )
        except anthropic.AuthenticationError as error:
            raise self._rejected(
                f"{self._key_name} was rejected by the Anthropic API "
                f"({error.status_code}).",
                "check the key is complete, hasn't been revoked, "
                "and belongs to the workspace you expect.",
            ) from None
        except anthropic.PermissionDeniedError as error:
            raise self._rejected(
                f"{self._key_name} doesn't have access to {model} "
                f"({error.status_code}).",
                "use a key from a workspace that can use this model, "
                "or change the stage's model setting.",
            ) from None
        except anthropic.NotFoundError as error:
            # The model named in the spec doesn't exist, or has been retired:
            # trying again won't help until the spec changes.
            raise SetupError(
                f"the Anthropic API has no model {model} ({error.status_code}).\n"
                "  Fix: change the stage's model setting in the pipeline spec."
            ) from None
        except anthropic.APIError as error:
            raise ModelFailureError(_sdk_failure_text(error)) from None
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
            raise UnusableReplyError(
                f"reply cut off at max_tokens={max_tokens}; "
                "raise the stage's max_tokens",
                reply=reply,
            )
        return reply

    async def close(self) -> None:
        await self._sdk.close()
