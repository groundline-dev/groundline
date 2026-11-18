"""The retry rules: what is worth retrying, how long to wait, and when to stop.

These are plain functions with no network or clock of their own, so the loops in
`_http.py` can share them and the tests can drive them directly. The numbers
match the server's own model retries.
"""

import re
from collections.abc import Callable
from datetime import UTC
from email.utils import parsedate_to_datetime

import httpx2

# The answers that mean "try again": the server or a proxy in front of it was
# briefly unable to answer. Anything else, a 4xx the caller must fix or a 500,
# is not retried: it raises the SDK's error at once.
RETRYABLE_STATUSES = frozenset({408, 429, 502, 503, 504})

# Full jitter: before the nth retry the wait is a random share of a cap that
# starts at 1 s, doubles each time, and stops at 8 s.
BACKOFF_BASE_SECONDS = 1.0
BACKOFF_CAP_SECONDS = 8.0

_SECONDS = re.compile(r"\d+(?:\.\d+)?")


def is_retryable_status(status: int) -> bool:
    return status in RETRYABLE_STATUSES


def is_retryable_error(error: BaseException) -> bool:
    """A timeout or a failure to talk to the server, as opposed to a mistake."""
    return isinstance(
        error,
        (httpx2.TimeoutException, httpx2.NetworkError, httpx2.RemoteProtocolError),
    )


def backoff_cap(attempt: int) -> float:
    """The longest wait after this attempt: 1, 2, 4 s, then 8 s."""
    return min(BACKOFF_CAP_SECONDS, BACKOFF_BASE_SECONDS * 2 ** min(attempt - 1, 10))


def retry_after_seconds(value: str | None, *, now: float) -> float | None:
    """The seconds a `Retry-After` header asks for, or None if it is absent or odd.

    The header is either a number of seconds or an HTTP date. `now` is the
    current time in seconds since 1970, used for the date form.
    """
    if value is None:
        return None
    value = value.strip()
    if _SECONDS.fullmatch(value):
        return float(value)
    try:
        moment = parsedate_to_datetime(value)
    except (ValueError, TypeError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    try:
        return max(0.0, moment.timestamp() - now)
    except (OverflowError, OSError):
        return None


def next_wait(
    *,
    attempt: int,
    retry_after: float | None,
    remaining: float,
    minimum_remaining: float,
    random: Callable[[], float],
) -> float | None:
    """How long to wait before retrying, or None when there is no time to retry.

    `attempt` is the attempt that just failed and `remaining` the seconds left of
    the retry budget. A `Retry-After` is a minimum: the wait is never shorter,
    and if it doesn't fit, there is no retry at all, rather than a shorter wait
    that would only postpone the same failure. A retry also needs at least
    `minimum_remaining` seconds (the connect timeout) left once the wait is over.
    """
    wait = max(random() * backoff_cap(attempt), retry_after or 0.0)
    if remaining - wait < minimum_remaining:
        return None
    return wait
