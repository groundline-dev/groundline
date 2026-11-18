"""The `httpx2` clients that retry a request and limit how long it may take.

They override `send`, the one step every request goes through, so proxies from
the environment, redirects and connection reuse work as they do in a plain
`httpx2` client. The rules are in `_retry.py`; the options are the ones
`GroundlineClient` takes. `clock`, `sleep` and `random` can be replaced so the
tests don't wait for real.

`ErrorRaisingClient` and its async twin wrap those two and turn every answer
that isn't a success, and every failure to get an answer, into the SDK's error
(`_error_mapping.py`), after the retries have run out. The generated code then
only ever sees a 2xx. The error is raised outside any `except` block, so it
has no `__context__`: the `httpx2` error it replaces carries the request, and
the request's headers hold the API key.
"""

import asyncio
import logging
import random as random_module
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx2

from ._error_mapping import error_for_failure, error_for_status
from ._retry import (
    is_retryable_error,
    is_retryable_status,
    next_wait,
    retry_after_seconds,
)

logger = logging.getLogger("groundline_client.retry")


def _jitter() -> float:
    # Looked up on each call, not bound once, so a test can replace random.random.
    return random_module.random()


_SMALLEST_ATTEMPT_SECONDS = 0.001
_TIMED_OUT = "The request took longer than the time allowed for it."


@dataclass(frozen=True)
class Limits:
    """The attempts and time limits a client was created with."""

    max_attempts: int
    max_retry_seconds: float
    timeout_seconds: float
    connect_timeout_seconds: float


class _RetryBudget:
    """The retry budget and attempt count of one call."""

    def __init__(
        self,
        limits: Limits,
        *,
        clock: Callable[[], float],
        random: Callable[[], float],
    ) -> None:
        self._limits = limits
        self._clock = clock
        self._random = random
        self._started = clock()
        self.attempt = 0

    def _remaining(self) -> float:
        return self._limits.max_retry_seconds - (self._clock() - self._started)

    def start_attempt(self, request: httpx2.Request) -> float:
        """Limit this attempt to the time the budget has left; return that limit.

        This replaces any timeout already on the request: the client's options are
        the only way to set one.
        """
        self.attempt += 1
        # Sleeping can overshoot the budget a little; an attempt then gets a sliver
        # of time, fails at once, and the call ends with that failure.
        remaining = max(self._remaining(), _SMALLEST_ATTEMPT_SECONDS)
        attempt_seconds = min(self._limits.timeout_seconds, remaining)
        request.extensions["timeout"] = {
            "connect": min(self._limits.connect_timeout_seconds, remaining),
            "read": attempt_seconds,
            "write": attempt_seconds,
            "pool": attempt_seconds,
        }
        return attempt_seconds

    def wait_before_retry(self, retry_after: float | None) -> float | None:
        """Seconds to wait before the next attempt, or None to stop."""
        if self.attempt >= self._limits.max_attempts:
            return None
        return next_wait(
            attempt=self.attempt,
            retry_after=retry_after,
            remaining=self._remaining(),
            minimum_remaining=self._limits.connect_timeout_seconds,
            random=self._random,
        )

    def wait_after_status(self, response: httpx2.Response) -> float | None:
        if not is_retryable_status(response.status_code):
            return None
        retry_after = retry_after_seconds(
            response.headers.get("retry-after"), now=time.time()
        )
        return self.wait_before_retry(retry_after)

    def log_retry(
        self, wait: float, *, status: int | None, error: Exception | None
    ) -> None:
        # Only counts, a status or an error's class name: never the URL, headers,
        # a body or an error's message, which can hold any of them.
        cause = f"status={status}" if error is None else f"error={type(error).__name__}"
        logger.info(
            "retry after attempt=%d %s wait_seconds=%.3f", self.attempt, cause, wait
        )


class RetryingClient(httpx2.Client):
    """A sync `httpx2` client that retries temporary failures within a time budget."""

    def __init__(
        self,
        *,
        max_attempts: int,
        max_retry_seconds: float,
        timeout_seconds: float,
        connect_timeout_seconds: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        random: Callable[[], float] = _jitter,
        **client_arguments: Any,
    ) -> None:
        super().__init__(**client_arguments)
        self._limits = Limits(
            max_attempts, max_retry_seconds, timeout_seconds, connect_timeout_seconds
        )
        self._clock = clock
        self._sleep = sleep
        self._random = random

    def send(
        self,
        request: httpx2.Request,
        *,
        stream: bool = False,
        auth: Any = httpx2.USE_CLIENT_DEFAULT,
        follow_redirects: Any = httpx2.USE_CLIENT_DEFAULT,
    ) -> httpx2.Response:
        call = _RetryBudget(self._limits, clock=self._clock, random=self._random)
        while True:
            call.start_attempt(request)
            try:
                response = super().send(
                    request, stream=stream, auth=auth, follow_redirects=follow_redirects
                )
            except Exception as error:
                wait = (
                    call.wait_before_retry(None) if is_retryable_error(error) else None
                )
                if wait is None:
                    raise
                call.log_retry(wait, status=None, error=error)
                self._sleep(wait)
                continue
            wait = call.wait_after_status(response)
            if wait is None:
                return response
            call.log_retry(wait, status=response.status_code, error=None)
            response.close()
            self._sleep(wait)


class AsyncRetryingClient(httpx2.AsyncClient):
    """An async `httpx2` client that retries temporary failures within a time budget.

    Each attempt is also cut off at its time limit by the event loop, so an
    attempt that never answers, or trickles bytes, can't outlast it.
    """

    def __init__(
        self,
        *,
        max_attempts: int,
        max_retry_seconds: float,
        timeout_seconds: float,
        connect_timeout_seconds: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        random: Callable[[], float] = _jitter,
        **client_arguments: Any,
    ) -> None:
        super().__init__(**client_arguments)
        self._limits = Limits(
            max_attempts, max_retry_seconds, timeout_seconds, connect_timeout_seconds
        )
        self._clock = clock
        self._sleep = sleep
        self._random = random

    async def send(
        self,
        request: httpx2.Request,
        *,
        stream: bool = False,
        auth: Any = httpx2.USE_CLIENT_DEFAULT,
        follow_redirects: Any = httpx2.USE_CLIENT_DEFAULT,
    ) -> httpx2.Response:
        call = _RetryBudget(self._limits, clock=self._clock, random=self._random)
        while True:
            attempt_seconds = call.start_attempt(request)
            boundary = asyncio.timeout(attempt_seconds)
            try:
                async with boundary:
                    response = await super().send(
                        request,
                        stream=stream,
                        auth=auth,
                        follow_redirects=follow_redirects,
                    )
            except Exception as caught:
                # The event loop's timeout raises a TimeoutError. Any other error
                # that happens to arrive as the timer fires is a different problem
                # and is kept as it is.
                timed_out = boundary.expired() and isinstance(caught, TimeoutError)
                error = httpx2.TimeoutException(_TIMED_OUT) if timed_out else caught
                wait = (
                    call.wait_before_retry(None) if is_retryable_error(error) else None
                )
                if wait is None:
                    if error is caught:
                        raise
                    raise error from None
                call.log_retry(wait, status=None, error=error)
                await self._sleep(wait)
                continue
            wait = call.wait_after_status(response)
            if wait is None:
                return response
            call.log_retry(wait, status=response.status_code, error=None)
            await response.aclose()
            await self._sleep(wait)


class ErrorRaisingClient(RetryingClient):
    """A `RetryingClient` that raises the SDK's error for a final failure."""

    def send(
        self,
        request: httpx2.Request,
        *,
        stream: bool = False,
        auth: Any = httpx2.USE_CLIENT_DEFAULT,
        follow_redirects: Any = httpx2.USE_CLIENT_DEFAULT,
    ) -> httpx2.Response:
        try:
            response = super().send(
                request, stream=stream, auth=auth, follow_redirects=follow_redirects
            )
        except Exception as caught:
            failure = error_for_failure(caught)
            if failure is None:
                raise
        else:
            response.read()
            if response.is_success:
                return response
            failure = error_for_status(
                response.status_code, response.headers, response.content
            )
            response.close()
        # Outside the `except` block, so the error has no `__context__`.
        raise failure


class AsyncErrorRaisingClient(AsyncRetryingClient):
    """An `AsyncRetryingClient` that raises the SDK's error for a final failure."""

    async def send(
        self,
        request: httpx2.Request,
        *,
        stream: bool = False,
        auth: Any = httpx2.USE_CLIENT_DEFAULT,
        follow_redirects: Any = httpx2.USE_CLIENT_DEFAULT,
    ) -> httpx2.Response:
        try:
            response = await super().send(
                request, stream=stream, auth=auth, follow_redirects=follow_redirects
            )
        except Exception as caught:
            failure = error_for_failure(caught)
            if failure is None:
                raise
        else:
            await response.aread()
            if response.is_success:
                return response
            failure = error_for_status(
                response.status_code, response.headers, response.content
            )
            await response.aclose()
        raise failure
