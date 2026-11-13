"""Provider-neutral outage scheduling with cancellable elapsed budgets."""

import asyncio
import logging
import random as randomness
import time
from collections.abc import Awaitable, Callable

from groundline.core.deadline import raise_if_cancelling
from groundline.core.deadline import remaining as check_remaining
from groundline.core.errors import ModelTimeoutError

logger = logging.getLogger("groundline.retry")


async def retry_call[T](
    operation: Callable[[], Awaitable[T]],
    *,
    decide: Callable[[Exception], tuple[bool, float | None]],
    max_attempts: int,
    budget_seconds: float,
    base_seconds: float,
    cap_seconds: float,
    remaining: Callable[[], float] = check_remaining,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    random: Callable[[], float] = randomness.random,
) -> T:
    end = clock() + budget_seconds

    def allowance() -> float:
        return min(end - clock(), remaining())

    delay_cap = min(base_seconds, cap_seconds)
    for attempt in range(max_attempts):
        if allowance() <= 0:
            raise ModelTimeoutError("retry")
        boundary = asyncio.timeout(allowance())
        try:
            async with boundary:
                return await operation()
        except Exception as error:
            if boundary.expired() or allowance() <= 0:
                raise ModelTimeoutError("retry") from None
            raise_if_cancelling()
            retryable, minimum = decide(error)
            if not retryable or attempt + 1 >= max_attempts:
                raise
            wait = max(random() * delay_cap, minimum or 0)
            if wait >= allowance():
                raise
            logger.info(
                "outage retry after attempt=%d wait_seconds=%.3f", attempt + 1, wait
            )
            wait_boundary = asyncio.timeout(allowance())
            try:
                async with wait_boundary:
                    await sleep(wait)
            except Exception:
                if wait_boundary.expired():
                    raise ModelTimeoutError("retry") from None
                raise
            delay_cap = min(cap_seconds, delay_cap * 2)
    raise AssertionError("positive attempt limit required")
