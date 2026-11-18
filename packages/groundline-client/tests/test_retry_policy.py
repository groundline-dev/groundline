"""The retry rules, with no network: what is retried, how long to wait, when to stop."""

from datetime import UTC, datetime
from email.utils import format_datetime

import httpx2
import pytest
from groundline_client._retry import (  # pyright: ignore[reportPrivateUsage]
    backoff_cap,
    is_retryable_error,
    is_retryable_status,
    next_wait,
    retry_after_seconds,
)

NOW = 1_700_000_000.0  # a fixed "current time" in seconds since 1970


def http_date(seconds_from_now: float) -> str:
    moment = datetime.fromtimestamp(NOW + seconds_from_now, UTC)
    return format_datetime(moment, usegmt=True)


# R1 (retry temporary errors)


@pytest.mark.parametrize("status", [408, 429, 502, 503, 504])
def test_temporary_statuses_are_retried(status: int):
    assert is_retryable_status(status)


@pytest.mark.parametrize(
    "status", [200, 301, 400, 401, 403, 404, 409, 413, 422, 500, 501]
)
def test_permanent_and_unlisted_statuses_are_not_retried(status: int):
    assert not is_retryable_status(status)


def test_a_flagged_summary_status_is_never_retried():
    # R6 (flagged summary is final): a flagged summary is HTTP 200.
    assert not is_retryable_status(200)


@pytest.mark.parametrize(
    "error",
    [
        httpx2.ConnectTimeout("x"),
        httpx2.ReadTimeout("x"),
        httpx2.WriteTimeout("x"),
        httpx2.PoolTimeout("x"),
        httpx2.ConnectError("x"),
        httpx2.ReadError("x"),
        httpx2.WriteError("x"),
        httpx2.RemoteProtocolError("x"),
    ],
    ids=lambda error: type(error).__name__,
)
def test_timeouts_and_connection_errors_are_retried(error: Exception):
    assert is_retryable_error(error)


@pytest.mark.parametrize(
    "error",
    [
        httpx2.UnsupportedProtocol("x"),
        httpx2.ProxyError("x"),
        ValueError("x"),
        KeyboardInterrupt(),
    ],
    ids=lambda error: type(error).__name__,
)
def test_other_errors_are_not_retried(error: BaseException):
    assert not is_retryable_error(error)


# R2 (backoff with jitter)


@pytest.mark.parametrize(
    ("attempt", "cap"),
    [(1, 1.0), (2, 2.0), (3, 4.0), (4, 8.0), (5, 8.0), (50, 8.0)],
)
def test_the_wait_cap_doubles_up_to_eight_seconds(attempt: int, cap: float):
    assert backoff_cap(attempt) == cap


@pytest.mark.parametrize(
    ("draw", "expected"), [(0.0, 0.0), (0.25, 1.0), (0.5, 2.0), (0.999, 3.996)]
)
def test_the_wait_is_a_random_share_of_the_cap(draw: float, expected: float):
    wait = next_wait(
        attempt=3,
        retry_after=None,
        remaining=100.0,
        minimum_remaining=5.0,
        random=lambda: draw,
    )
    assert wait == pytest.approx(expected)


# R3 (Retry-After)


@pytest.mark.parametrize(
    ("header", "expected"),
    [("0", 0.0), ("7", 7.0), ("120", 120.0), (" 3 ", 3.0), ("2.5", 2.5)],
)
def test_retry_after_in_seconds(header: str, expected: float):
    assert retry_after_seconds(header, now=NOW) == expected


def test_retry_after_as_a_http_date_is_the_time_until_then():
    assert retry_after_seconds(http_date(30), now=NOW) == pytest.approx(30.0)


def test_retry_after_date_in_the_past_means_no_wait():
    assert retry_after_seconds(http_date(-3600), now=NOW) == 0.0


@pytest.mark.parametrize(
    "header", [None, "", "  ", "soon", "-1", "nan", "inf", "1e999", "Mon, 99 Foo"]
)
def test_a_missing_or_malformed_retry_after_is_ignored(header: str | None):
    assert retry_after_seconds(header, now=NOW) is None


def test_retry_after_is_a_minimum_the_wait_never_goes_below():
    wait = next_wait(
        attempt=1,
        retry_after=7.0,
        remaining=100.0,
        minimum_remaining=5.0,
        random=lambda: 0.5,
    )
    assert wait == 7.0


def test_a_short_retry_after_does_not_shorten_the_backoff():
    wait = next_wait(
        attempt=2,
        retry_after=0.1,
        remaining=100.0,
        minimum_remaining=5.0,
        random=lambda: 0.5,
    )
    assert wait == 1.0


# R4 (caps): when there is no time left for another attempt


def test_no_retry_when_the_wait_does_not_fit_the_time_left():
    wait = next_wait(
        attempt=1,
        retry_after=30.0,
        remaining=10.0,
        minimum_remaining=5.0,
        random=lambda: 0.5,
    )
    assert wait is None


def test_no_retry_when_less_than_the_connect_timeout_would_be_left_after_waiting():
    wait = next_wait(
        attempt=1,
        retry_after=None,
        remaining=5.4,
        minimum_remaining=5.0,
        random=lambda: 0.5,  # waits 0.5, leaving 4.9
    )
    assert wait is None


def test_a_retry_may_start_with_exactly_the_connect_timeout_left():
    wait = next_wait(
        attempt=1,
        retry_after=None,
        remaining=5.5,
        minimum_remaining=5.0,
        random=lambda: 0.5,  # waits 0.5, leaving 5.0
    )
    assert wait == 0.5


def test_no_retry_when_no_time_is_left_at_all():
    wait = next_wait(
        attempt=1,
        retry_after=None,
        remaining=0.0,
        minimum_remaining=5.0,
        random=lambda: 0.0,
    )
    assert wait is None
