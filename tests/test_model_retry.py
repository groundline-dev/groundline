"""Provider-independent scheduler contract for ALL-134.

retry_call is internal. It accepts an async operation, a decision callback returning
(retryable, minimum_wait), limits, and injectable clock/sleep/random callables.
budget_seconds bounds all attempts and waits together, including a hung operation.
The random source returns a fraction in [0, 1]; no provider exceptions or headers
belong here. Production code is deliberately not added in the tests session.
"""

import asyncio
import importlib
import importlib.util
from collections.abc import Awaitable, Callable
from typing import Any, Protocol, cast

import pytest


class TemporaryFailure(Exception):
    pass


class RetryCall(Protocol):
    async def __call__(
        self,
        operation: Callable[[], Awaitable[Any]],
        *,
        decide: Callable[[Exception], tuple[bool, float | None]],
        max_attempts: int,
        budget_seconds: float,
        base_seconds: float,
        cap_seconds: float,
        remaining: Callable[[], float],
        clock: Callable[[], float],
        sleep: Callable[[float], Awaitable[None]],
        random: Callable[[], float],
    ) -> Any: ...


@pytest.fixture
def retry_call() -> RetryCall:
    # Implementation session: replace this temporary lookup with a normal import
    # once groundline.clients.retry exists.
    async def run(*args: Any, **kwargs: Any) -> Any:
        assert importlib.util.find_spec("groundline.clients.retry") is not None, (
            "the shared provider-independent retry scheduler is not implemented"
        )
        module = importlib.import_module("groundline.clients.retry")
        call = getattr(module, "retry_call", None)
        assert callable(call), "shared retry_call must expose its scheduling contract"
        return await cast(RetryCall, call)(*args, **kwargs)

    return cast(RetryCall, run)


class Clock:
    def __init__(self):
        self.now = 0.0
        self.waits: list[float] = []

    def read(self) -> float:
        return self.now

    async def sleep(self, seconds: float):
        self.waits.append(seconds)
        self.now += seconds


@pytest.mark.parametrize("fraction", [0, 0.25, 1])
def test_full_jitter_doubles_then_caps(retry_call: RetryCall, fraction: float):
    clock = Clock()
    calls = 0

    async def operation():
        nonlocal calls
        calls += 1
        if calls < 8:
            raise TemporaryFailure()
        return "recovered"

    result = asyncio.run(
        retry_call(
            operation,
            decide=lambda error: (True, 0),
            max_attempts=8,
            budget_seconds=120,
            base_seconds=1,
            cap_seconds=8,
            remaining=lambda: 120,
            clock=clock.read,
            sleep=clock.sleep,
            random=lambda: fraction,
        )
    )
    assert result == "recovered"
    assert calls == 8
    assert clock.waits == [fraction * cap for cap in (1, 2, 4, 8, 8, 8, 8)]


@pytest.mark.parametrize("minimum", [0, 3])
def test_provider_minimum_is_respected(retry_call: RetryCall, minimum: float):
    clock = Clock()
    calls = 0

    async def operation():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TemporaryFailure()
        return "ok"

    asyncio.run(
        retry_call(
            operation,
            decide=lambda error: (True, minimum),
            max_attempts=3,
            budget_seconds=10,
            base_seconds=1,
            cap_seconds=8,
            remaining=lambda: 10,
            clock=clock.read,
            sleep=clock.sleep,
            random=lambda: 0.5,
        )
    )
    assert len(clock.waits) == 1
    assert clock.waits[0] >= max(minimum, 0.5)
    assert calls == 2


@pytest.mark.parametrize("attempts", [1, 2, 4])
def test_attempt_limit_includes_original(retry_call: RetryCall, attempts: int):
    clock = Clock()
    calls = 0
    failure = TemporaryFailure()

    async def operation():
        nonlocal calls
        calls += 1
        raise failure

    with pytest.raises(TemporaryFailure) as error:
        asyncio.run(
            retry_call(
                operation,
                decide=lambda error: (True, 0),
                max_attempts=attempts,
                budget_seconds=120,
                base_seconds=1,
                cap_seconds=8,
                remaining=lambda: 120,
                clock=clock.read,
                sleep=clock.sleep,
                random=lambda: 0.5,
            )
        )
    assert error.value is failure
    assert calls == attempts


@pytest.mark.parametrize("check_remaining,retry_budget", [(0.5, 10), (10, 0.5)])
def test_minimum_wait_must_fit_both_allowances(
    retry_call: RetryCall, check_remaining: float, retry_budget: float
):
    clock = Clock()
    calls = 0

    async def operation():
        nonlocal calls
        calls += 1
        raise TemporaryFailure()

    with pytest.raises(TemporaryFailure):
        asyncio.run(
            retry_call(
                operation,
                decide=lambda error: (True, 1),
                max_attempts=3,
                budget_seconds=retry_budget,
                base_seconds=1,
                cap_seconds=8,
                remaining=lambda: check_remaining,
                clock=clock.read,
                sleep=clock.sleep,
                random=lambda: 0,
            )
        )
    assert calls == 1
    assert clock.waits == []


def test_call_time_consumes_retry_budget(retry_call: RetryCall):
    clock = Clock()
    calls = 0

    async def operation():
        nonlocal calls
        calls += 1
        clock.now += 0.75
        raise TemporaryFailure()

    with pytest.raises(Exception):
        asyncio.run(
            retry_call(
                operation,
                decide=lambda error: (True, 1),
                max_attempts=5,
                budget_seconds=1,
                base_seconds=1,
                cap_seconds=8,
                remaining=lambda: 120,
                clock=clock.read,
                sleep=clock.sleep,
                random=lambda: 0,
            )
        )
    assert calls == 1
    assert clock.waits == []


@pytest.mark.parametrize("where", ["call", "wait"])
def test_cancellation_never_retries(retry_call: RetryCall, where: str):
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


def test_retry_budget_cancels_hung_operation_without_retry(retry_call: RetryCall):
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

        task = asyncio.create_task(
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
        try:
            done, _ = await asyncio.wait({task}, timeout=3)
            assert done, "retry budget must cancel a hung operation before the watchdog"
            # Budget exhaustion is an owned timeout, not external cancellation.
            with pytest.raises(Exception):
                task.result()
            assert calls == cancelled == 1
            assert decisions == [], "budget exhaustion must bypass retry classification"
            assert waits == []
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())
