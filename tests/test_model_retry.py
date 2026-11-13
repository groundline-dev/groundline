"""Provider-independent retry scheduling, budgets and cancellation."""

import asyncio
from collections.abc import Awaitable, Callable

import pytest

from groundline_core.clients.retry import retry_call
from groundline_core.core.errors import ModelTimeoutError
from tests.async_helpers import finish_before_watchdog


class TemporaryFailure(Exception):
    pass


class Clock:
    def __init__(self):
        self.now = 0.0
        self.waits: list[float] = []

    def read(self) -> float:
        return self.now

    async def sleep(self, seconds: float):
        self.waits.append(seconds)
        self.now += seconds


async def scheduled[T](
    operation: Callable[[], Awaitable[T]],
    clock: Clock,
    *,
    attempts: int = 3,
    budget: float = 120,
    remaining: float = 120,
    minimum: float = 0,
    fraction: float = 0.5,
) -> T:
    return await retry_call(
        operation,
        decide=lambda error: (True, minimum),
        max_attempts=attempts,
        budget_seconds=budget,
        base_seconds=1,
        cap_seconds=8,
        remaining=lambda: remaining,
        clock=clock.read,
        sleep=clock.sleep,
        random=lambda: fraction,
    )


@pytest.mark.parametrize("fraction", [0, 0.25, 1])
def test_full_jitter_doubles_then_caps(fraction: float):
    clock = Clock()
    calls = 0

    async def operation():
        nonlocal calls
        calls += 1
        if calls < 8:
            raise TemporaryFailure()
        return "recovered"

    result = asyncio.run(scheduled(operation, clock, attempts=8, fraction=fraction))
    assert result == "recovered"
    assert calls == 8
    assert clock.waits == [fraction * cap for cap in (1, 2, 4, 8, 8, 8, 8)]


@pytest.mark.parametrize("minimum", [0, 3])
def test_provider_minimum_is_respected(minimum: float):
    clock = Clock()
    calls = 0

    async def operation():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TemporaryFailure()
        return "ok"

    asyncio.run(scheduled(operation, clock, budget=10, remaining=10, minimum=minimum))
    assert len(clock.waits) == 1
    assert clock.waits[0] >= max(minimum, 0.5)
    assert calls == 2


@pytest.mark.parametrize("attempts", [1, 2, 4])
def test_attempt_limit_includes_original(attempts: int):
    clock = Clock()
    calls = 0
    failure = TemporaryFailure()

    async def operation():
        nonlocal calls
        calls += 1
        raise failure

    with pytest.raises(TemporaryFailure) as error:
        asyncio.run(scheduled(operation, clock, attempts=attempts))
    assert error.value is failure
    assert calls == attempts


@pytest.mark.parametrize("check_remaining,retry_budget", [(0.5, 10), (10, 0.5)])
def test_minimum_wait_must_fit_both_allowances(
    check_remaining: float, retry_budget: float
):
    clock = Clock()
    calls = 0

    async def operation():
        nonlocal calls
        calls += 1
        raise TemporaryFailure()

    with pytest.raises(TemporaryFailure):
        asyncio.run(
            scheduled(
                operation,
                clock,
                budget=retry_budget,
                remaining=check_remaining,
                minimum=1,
                fraction=0,
            )
        )
    assert calls == 1
    assert clock.waits == []


def test_call_time_consumes_retry_budget():
    clock = Clock()
    calls = 0

    async def operation():
        nonlocal calls
        calls += 1
        clock.now += 0.75
        raise TemporaryFailure()

    with pytest.raises(TemporaryFailure):
        asyncio.run(
            scheduled(operation, clock, attempts=5, budget=1, minimum=1, fraction=0)
        )
    assert calls == 1
    assert clock.waits == []


@pytest.mark.parametrize("where", ["call", "wait"])
def test_cancellation_never_retries(where: str):
    async def run():
        entered = asyncio.Event()
        calls = 0

        async def operation():
            nonlocal calls
            calls += 1
            if where == "call":
                entered.set()
                await asyncio.Event().wait()
            raise TemporaryFailure()

        async def sleep(seconds: float):
            entered.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(
            retry_call(
                operation,
                decide=lambda error: (True, 0),
                max_attempts=3,
                budget_seconds=120,
                base_seconds=1,
                cap_seconds=8,
                remaining=lambda: 120,
                clock=lambda: 0,
                sleep=sleep,
                random=lambda: 0.5,
            )
        )
        try:
            await asyncio.wait_for(entered.wait(), 3)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert calls == 1
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())


def test_retry_budget_cancels_hung_operation_without_retry():
    async def run():
        calls = 0
        cancelled = 0
        decisions: list[Exception] = []
        waits: list[float] = []

        async def operation():
            nonlocal calls, cancelled
            calls += 1
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled += 1
                raise

        def decide(error: Exception) -> tuple[bool, float | None]:
            decisions.append(error)
            return True, 0

        async def sleep(seconds: float):
            waits.append(seconds)
            await asyncio.sleep(seconds)

        with pytest.raises(ModelTimeoutError) as error:
            await finish_before_watchdog(
                retry_call(
                    operation,
                    decide=decide,
                    max_attempts=3,
                    budget_seconds=0.3,
                    base_seconds=1,
                    cap_seconds=8,
                    remaining=lambda: 120,
                    clock=asyncio.get_running_loop().time,
                    sleep=sleep,
                    random=lambda: 0.5,
                )
            )
        assert error.value.reason == "retry"
        assert calls == cancelled == 1
        assert decisions == [], "budget exhaustion must bypass retry classification"
        assert waits == []

    asyncio.run(run())
