"""Retries and time limits, sync and async.

The first half drives the retrying clients directly, with a fake clock and a
scripted inner transport, so no test waits for a real backoff. The second half
goes through `GroundlineClient` and the fake server, for what only the whole
client can show: all four routes, the defaults, and a call that really hangs.
No test reaches the real API.
"""

import asyncio
import logging
import time
from dataclasses import replace
from typing import Any

import groundline_client as sdk
import httpx2
import pytest
from conftest import (
    OPERATIONS,
    FakeServer,
    Step,
    call,
    make_client,
    request_for,
)
from groundline_client._http import (  # pyright: ignore[reportPrivateUsage]
    AsyncRetryingClient,
    Limits,
    RetryingClient,
)

RETRY_LOGGER = "groundline_client.retry"
SECRET_KEY = "test-key-not-a-real-secret"  # betterleaks:allow a dummy value
LIMITS = Limits(
    max_attempts=3,
    max_retry_seconds=180.0,
    timeout_seconds=150.0,
    connect_timeout_seconds=5.0,
)
FIRST_ATTEMPT_LIMITS = {"connect": 5.0, "read": 150.0, "write": 150.0, "pool": 150.0}
SYNC_AND_ASYNC = pytest.mark.parametrize(
    "is_async", [False, True], ids=["sync", "async"]
)


def reply(
    status: int, headers: dict[str, str] | None = None, body: Any = None
) -> httpx2.Response:
    return httpx2.Response(
        status, headers=headers, json={"detail": "x"} if body is None else body
    )


class FakeClock:
    """Time that only moves when a test or a sleep moves it."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.waits: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.waits.append(seconds)
        self.now += seconds

    async def async_sleep(self, seconds: float) -> None:
        self.sleep(seconds)


class Scripted(httpx2.BaseTransport, httpx2.AsyncBaseTransport):
    """Answers each attempt from `steps` (the last repeats), taking `took` seconds."""

    def __init__(
        self,
        clock: FakeClock,
        steps: list[httpx2.Response | Exception],
        took: float = 0.0,
    ) -> None:
        self.clock = clock
        self.steps = steps
        self.took = took
        self.limits: list[dict[str, float]] = []

    def _answer(self, request: httpx2.Request) -> httpx2.Response:
        self.limits.append(dict(request.extensions.get("timeout", {})))
        self.clock.now += self.took
        step = self.steps[min(len(self.limits), len(self.steps)) - 1]
        if isinstance(step, Exception):
            raise step
        return step

    def handle_request(self, request: httpx2.Request) -> httpx2.Response:
        return self._answer(request)

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        return self._answer(request)


def build(
    inner: Scripted, *, is_async: bool, draw: float = 0.5, **overrides: Any
) -> Any:
    limits = replace(LIMITS, **overrides)
    if is_async:
        return AsyncRetryingClient(
            transport=inner,
            limits=limits,
            clock=inner.clock,
            sleep=inner.clock.async_sleep,
            random=lambda: draw,
        )
    return RetryingClient(
        transport=inner,
        limits=limits,
        clock=inner.clock,
        sleep=inner.clock.sleep,
        random=lambda: draw,
    )


def send(client: Any, is_async: bool, request: httpx2.Request | None = None) -> Any:
    request = request or httpx2.Request("POST", "http://api.test/v1/omissions")
    if is_async:
        return asyncio.run(client.send(request))
    return client.send(request)


def retry_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == RETRY_LOGGER]


def retry_log_text(caplog: pytest.LogCaptureFixture) -> str:
    """Only what groundline_client logged; httpx2 logs its own URLs."""
    return "\n".join(r.getMessage() for r in retry_records(caplog))


@SYNC_AND_ASYNC
@pytest.mark.parametrize("status", [408, 429, 502, 503, 504])
def test_a_temporary_status_is_retried_and_the_next_answer_is_returned(
    status: int, is_async: bool
):
    clock = FakeClock()
    inner = Scripted(clock, [reply(status), reply(200, body={"ok": True})])

    response = send(build(inner, is_async=is_async), is_async)

    assert response.status_code == 200
    assert len(inner.limits) == 2


@SYNC_AND_ASYNC
@pytest.mark.parametrize("status", [400, 401, 403, 413, 422])
def test_a_permanent_status_is_returned_after_one_attempt(status: int, is_async: bool):
    inner = Scripted(FakeClock(), [reply(status)])

    response = send(build(inner, is_async=is_async), is_async)

    assert response.status_code == status
    assert len(inner.limits) == 1


@SYNC_AND_ASYNC
@pytest.mark.parametrize(
    "error",
    [httpx2.ConnectError("x"), httpx2.ReadTimeout("x")],
    ids=lambda error: type(error).__name__,
)
def test_a_timeout_or_connection_error_is_retried(error: Exception, is_async: bool):
    inner = Scripted(FakeClock(), [error, reply(200)])

    response = send(build(inner, is_async=is_async), is_async)

    assert response.status_code == 200
    assert len(inner.limits) == 2


@SYNC_AND_ASYNC
def test_any_other_error_is_raised_after_one_attempt(is_async: bool):
    inner = Scripted(FakeClock(), [httpx2.UnsupportedProtocol("x")])

    with pytest.raises(httpx2.UnsupportedProtocol):
        send(build(inner, is_async=is_async), is_async)

    assert len(inner.limits) == 1


# Attempts


@SYNC_AND_ASYNC
def test_the_last_status_is_returned_when_the_attempts_run_out(is_async: bool):
    inner = Scripted(FakeClock(), [reply(503, body={"detail": "still down"})])

    response = send(build(inner, is_async=is_async, max_attempts=3), is_async)

    assert response.json() == {"detail": "still down"}
    assert len(inner.limits) == 3


@SYNC_AND_ASYNC
def test_the_last_error_is_raised_when_the_attempts_run_out(is_async: bool):
    inner = Scripted(FakeClock(), [httpx2.ReadTimeout("x")])

    with pytest.raises(httpx2.ReadTimeout):
        send(build(inner, is_async=is_async, max_attempts=3), is_async)

    assert len(inner.limits) == 3


@SYNC_AND_ASYNC
def test_one_attempt_means_no_retries_and_no_waiting(is_async: bool):
    clock = FakeClock()
    inner = Scripted(clock, [reply(503)])

    response = send(build(inner, is_async=is_async, max_attempts=1), is_async)

    assert response.status_code == 503
    assert (len(inner.limits), clock.waits) == (1, [])


# Backoff with jitter


@SYNC_AND_ASYNC
def test_the_wait_doubles_each_attempt_up_to_a_maximum(is_async: bool):
    clock = FakeClock()
    inner = Scripted(clock, [reply(503)])

    send(build(inner, is_async=is_async, max_attempts=7, draw=0.5), is_async)

    # Half of each cap: 1, 2, 4, then 8 seconds, and 8 stays the cap.
    assert clock.waits == [0.5, 1.0, 2.0, 4.0, 4.0, 4.0]


@SYNC_AND_ASYNC
def test_the_wait_is_random_within_the_cap(is_async: bool):
    clock = FakeClock()
    inner = Scripted(clock, [reply(503)])

    send(build(inner, is_async=is_async, max_attempts=2, draw=0.8), is_async)

    assert clock.waits == [pytest.approx(0.8)]


# Retry-After


@SYNC_AND_ASYNC
def test_retry_after_sets_the_minimum_wait(is_async: bool):
    clock = FakeClock()
    inner = Scripted(clock, [reply(503, {"Retry-After": "7"}), reply(200)])

    send(build(inner, is_async=is_async), is_async)

    assert clock.waits == [7.0]


@SYNC_AND_ASYNC
def test_a_malformed_retry_after_falls_back_to_the_backoff(is_async: bool):
    clock = FakeClock()
    inner = Scripted(clock, [reply(503, {"Retry-After": "soon"}), reply(200)])

    send(build(inner, is_async=is_async), is_async)

    assert clock.waits == [0.5]


@SYNC_AND_ASYNC
def test_a_retry_after_longer_than_the_time_left_stops_at_once_with_the_error(
    is_async: bool,
):
    clock = FakeClock()
    inner = Scripted(clock, [reply(503, {"Retry-After": "30"}), reply(200)])

    response = send(build(inner, is_async=is_async, max_retry_seconds=10.0), is_async)

    assert response.status_code == 503
    assert (len(inner.limits), clock.waits) == (1, [])


# The time left


@SYNC_AND_ASYNC
def test_each_attempt_is_limited_to_the_time_the_retry_budget_has_left(
    is_async: bool,
):
    clock = FakeClock()
    inner = Scripted(clock, [reply(503)], took=100.0)

    response = send(build(inner, is_async=is_async), is_async)

    assert response.status_code == 503
    # 100 s, a 0.5 s wait, then 79.5 s of the 180 s are left. The second attempt
    # takes 100 s too, so the budget is spent and there is no third.
    assert len(inner.limits) == 2
    assert inner.limits[0] == FIRST_ATTEMPT_LIMITS
    assert inner.limits[1] == {
        "connect": 5.0,
        "read": pytest.approx(79.5),
        "write": pytest.approx(79.5),
        "pool": pytest.approx(79.5),
    }


@SYNC_AND_ASYNC
def test_no_retry_starts_with_less_than_the_connect_timeout_left(is_async: bool):
    clock = FakeClock()
    inner = Scripted(clock, [reply(503)], took=176.0)  # 4 s of 180 s are left

    response = send(build(inner, is_async=is_async), is_async)

    assert response.status_code == 503
    assert (len(inner.limits), clock.waits) == (1, [])


@SYNC_AND_ASYNC
def test_the_connect_timeout_and_the_total_timeout_are_sent_with_each_request(
    is_async: bool,
):
    inner = Scripted(FakeClock(), [reply(200)])

    send(
        build(
            inner, is_async=is_async, timeout_seconds=40.0, connect_timeout_seconds=2.0
        ),
        is_async,
    )

    assert inner.limits == [{"connect": 2.0, "read": 40.0, "write": 40.0, "pool": 40.0}]


class HangingAsync(httpx2.AsyncBaseTransport):
    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        await asyncio.Event().wait()
        raise AssertionError("a hung call never answers")


def test_an_async_call_that_never_answers_is_cut_off_at_the_total_timeout():
    client = AsyncRetryingClient(
        transport=HangingAsync(),
        limits=Limits(
            max_attempts=1,
            max_retry_seconds=5.0,
            timeout_seconds=0.2,
            connect_timeout_seconds=0.1,
        ),
    )
    started = time.monotonic()

    async def send_with_safety_limit() -> httpx2.Response:
        # The 5 s limit only stops this test from hanging if the client has
        # no limit of its own; it raises asyncio's TimeoutError, not httpx2's.
        return await asyncio.wait_for(
            client.send(httpx2.Request("GET", "http://api.test/health")), timeout=5
        )

    with pytest.raises(httpx2.TimeoutException):
        asyncio.run(send_with_safety_limit())

    assert time.monotonic() - started < 2.0


class FailsWhileCancelled(httpx2.AsyncBaseTransport):
    """Never answers, and fails with its own error when the time limit cancels it."""

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            raise RuntimeError("cleanup failed") from None
        raise AssertionError("a hung call never answers")


def test_an_error_that_is_not_a_timeout_is_not_relabeled_as_one():
    client = AsyncRetryingClient(
        transport=FailsWhileCancelled(),
        limits=Limits(
            max_attempts=1,
            max_retry_seconds=5.0,
            timeout_seconds=0.1,
            connect_timeout_seconds=0.05,
        ),
    )
    request = httpx2.Request("GET", "http://api.test/health")

    with pytest.raises(RuntimeError, match="cleanup failed"):
        asyncio.run(asyncio.wait_for(client.send(request), timeout=5))


# Retry logging


@SYNC_AND_ASYNC
def test_each_retry_logs_the_attempt_the_cause_and_the_wait(
    is_async: bool, caplog: pytest.LogCaptureFixture
):
    caplog.set_level(logging.INFO, logger=RETRY_LOGGER)
    inner = Scripted(
        FakeClock(),
        [reply(503, {"Retry-After": "2"}), httpx2.ReadTimeout("x"), reply(200)],
    )

    send(build(inner, is_async=is_async), is_async)

    messages = [r.getMessage() for r in retry_records(caplog)]
    assert len(messages) == 2
    assert all(m.startswith("retry after") for m in messages)
    assert "attempt=1" in messages[0]
    assert "status=503" in messages[0]
    assert messages[0].endswith("wait_seconds=2.00")
    assert "attempt=2" in messages[1]
    assert "error=ReadTimeout" in messages[1]


@SYNC_AND_ASYNC
def test_the_retry_log_has_no_key_url_header_or_body(
    is_async: bool, caplog: pytest.LogCaptureFixture
):
    caplog.set_level(logging.DEBUG)
    request = httpx2.Request(
        "POST",
        "http://api.test/v1/omissions",
        headers={"Authorization": f"Bearer {SECRET_KEY}", "X-Own": "own-header-value"},
        json={"source": "SECRET CLINICAL TEXT"},
    )
    inner = Scripted(
        FakeClock(),
        [
            reply(503, body={"detail": "SECRET REPLY TEXT"}),
            httpx2.ReadTimeout("secret-timeout-detail"),
            reply(200),
        ],
    )

    send(build(inner, is_async=is_async), is_async, request)

    assert len(retry_records(caplog)) == 2
    for secret in (
        SECRET_KEY,
        "api.test",
        "own-header-value",
        "SECRET CLINICAL TEXT",
        "SECRET REPLY TEXT",
        "secret-timeout-detail",
    ):
        assert secret not in retry_log_text(caplog)


@SYNC_AND_ASYNC
def test_nothing_is_logged_when_there_is_no_retry(
    is_async: bool, caplog: pytest.LogCaptureFixture
):
    caplog.set_level(logging.DEBUG)
    inner = Scripted(FakeClock(), [reply(200)])

    send(build(inner, is_async=is_async), is_async)

    assert retry_records(caplog) == []


# Through the whole client and the fake server


@SYNC_AND_ASYNC
@pytest.mark.parametrize(
    ("operation", "method", "path", "request_model", "response_model"), OPERATIONS
)
def test_every_route_retries_a_temporary_failure_and_returns_the_next_answer(
    operation: str,
    method: str,
    path: str,
    request_model: str | None,
    response_model: str | None,
    is_async: bool,
    fake_server: FakeServer,
    api_key: str,
):
    fake_server.script(method, path, [Step(503, {"detail": "busy"})])
    client = make_client(fake_server, api_key, max_attempts=2)

    result = call(
        client, operation, request_for(fake_server, request_model), is_async=is_async
    )

    assert [(r.method, r.path) for r in fake_server.requests] == [(method, path)] * 2
    assert result is not None


@SYNC_AND_ASYNC
def test_a_client_made_without_options_makes_three_attempts(
    is_async: bool, fake_server: FakeServer, api_key: str
):
    fake_server.reply("POST", "/v1/omissions", 503, {"detail": "still down"})
    client = make_client(fake_server, api_key)

    with pytest.raises(sdk.ServerUnavailableError) as raised:
        call(
            client,
            "evaluate_omissions",
            request_for(fake_server, "OmissionsRequest"),
            is_async=is_async,
        )

    assert len(fake_server.requests) == 3
    assert raised.value.detail == "still down"


@SYNC_AND_ASYNC
def test_the_retry_log_of_a_whole_client_never_holds_the_api_key(
    is_async: bool,
    fake_server: FakeServer,
    api_key: str,
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.DEBUG)
    fake_server.reply("GET", "/health", 503, {"detail": "still down"})
    client = make_client(fake_server, api_key)

    with pytest.raises(sdk.GroundlineError):
        call(client, "health", request_for(fake_server, None), is_async=is_async)

    assert len(retry_records(caplog)) == 2
    assert api_key not in caplog.text
    assert fake_server.url not in retry_log_text(caplog)


@SYNC_AND_ASYNC
@pytest.mark.parametrize("status", [400, 401, 403, 413])
def test_a_permanent_error_is_not_retried(
    status: int, is_async: bool, fake_server: FakeServer, api_key: str
):
    fake_server.reply("POST", "/v1/omissions", status, {"detail": "no"})
    client = make_client(fake_server, api_key)

    with pytest.raises(sdk.GroundlineError):
        call(
            client,
            "evaluate_omissions",
            request_for(fake_server, "OmissionsRequest"),
            is_async=is_async,
        )

    assert len(fake_server.requests) == 1


@SYNC_AND_ASYNC
def test_a_rejected_request_is_not_retried(
    is_async: bool, fake_server: FakeServer, api_key: str
):
    fake_server.reply("POST", "/v1/omissions", 422, {"detail": []})
    client = make_client(fake_server, api_key)

    with pytest.raises(sdk.InvalidRequestError):
        call(
            client,
            "evaluate_omissions",
            request_for(fake_server, "OmissionsRequest"),
            is_async=is_async,
        )

    assert len(fake_server.requests) == 1


@SYNC_AND_ASYNC
def test_a_flagged_summary_is_a_normal_result_and_is_not_retried(
    is_async: bool, fake_server: FakeServer, api_key: str
):
    flagged = {**fake_server.examples["OmissionsResponse"], "flagged": True}
    fake_server.reply("POST", "/v1/omissions", 200, flagged)
    client = make_client(fake_server, api_key)

    result = call(
        client,
        "evaluate_omissions",
        request_for(fake_server, "OmissionsRequest"),
        is_async=is_async,
    )

    assert len(fake_server.requests) == 1
    assert result.flagged is True


@SYNC_AND_ASYNC
def test_a_call_that_hangs_is_cut_off_at_the_total_timeout(
    is_async: bool, fake_server: FakeServer, api_key: str
):
    fake_server.hang("GET", "/health")
    client = make_client(fake_server, api_key, max_attempts=1, timeout_seconds=0.05)
    started = time.monotonic()

    with pytest.raises(sdk.GroundlineTimeoutError):
        call(client, "health", request_for(fake_server, None), is_async=is_async)

    assert time.monotonic() - started < 3.0
    assert len(fake_server.requests) == 1


@SYNC_AND_ASYNC
def test_the_timeout_raised_for_a_hung_call_knows_its_request(
    is_async: bool, fake_server: FakeServer
):
    # The retrying clients raise `httpx2`'s own timeout, and it has its request,
    # so `.request` works on it. `GroundlineClient` turns it into the SDK's
    # error, which has none (the request's headers hold the API key), so this
    # drives the retrying clients directly.
    fake_server.hang("GET", "/health")
    limits = Limits(
        max_attempts=1,
        max_retry_seconds=5.0,
        timeout_seconds=0.05,
        connect_timeout_seconds=0.1,
    )
    request = httpx2.Request("GET", f"{fake_server.url}/health")

    with pytest.raises(httpx2.TimeoutException) as timed_out:
        if is_async:
            asyncio.run(AsyncRetryingClient(limits=limits).send(request))
        else:
            RetryingClient(limits=limits).send(request)

    assert timed_out.value.request.url.path == "/health"


@SYNC_AND_ASYNC
def test_hung_attempts_are_retried_within_the_retry_budget(
    is_async: bool, fake_server: FakeServer, api_key: str
):
    fake_server.hang("GET", "/health")
    client = make_client(
        fake_server,
        api_key,
        max_attempts=5,
        max_retry_seconds=1.0,
        timeout_seconds=0.4,
        connect_timeout_seconds=0.05,
    )
    started = time.monotonic()

    with pytest.raises(sdk.GroundlineTimeoutError):
        call(client, "health", request_for(fake_server, None), is_async=is_async)

    assert time.monotonic() - started < 3.0  # the 1 s budget, not 5 x 0.4 s
    assert 2 <= len(fake_server.requests) <= 3


@SYNC_AND_ASYNC
def test_a_refused_connection_is_retried_then_raised(
    is_async: bool,
    api_key: str,
    closed_port: int,
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.INFO, logger=RETRY_LOGGER)
    client: Any = sdk.GroundlineClient(
        api_url=f"http://127.0.0.1:{closed_port}", api_key=api_key, max_attempts=3
    )

    with pytest.raises(sdk.ServerUnavailableError):
        if is_async:
            asyncio.run(client.health_async())
        else:
            client.health()

    assert len(retry_records(caplog)) == 2


@SYNC_AND_ASYNC
def test_a_proxy_set_in_the_environment_is_still_used(
    is_async: bool,
    fake_server: FakeServer,
    api_key: str,
    monkeypatch: pytest.MonkeyPatch,
):
    # The fake server stands in for the proxy: a proxied plain-HTTP request
    # names the target's full URL as its path.
    monkeypatch.setenv("HTTP_PROXY", fake_server.url)
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    client: Any = sdk.GroundlineClient(
        api_url="http://api.test.invalid", api_key=api_key
    )

    # The fake server has no route for that path, so the call raises; only the
    # request it received matters here.
    with pytest.raises(sdk.GroundlineError):
        call(client, "health", request_for(fake_server, None), is_async=is_async)

    assert fake_server.requests[0].path == "http://api.test.invalid/health"
