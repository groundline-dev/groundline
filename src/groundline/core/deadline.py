"""Internal monotonic check allowance, inherited by child tasks."""

import asyncio
import time
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar

_deadline: ContextVar[float | None] = ContextVar("check_deadline", default=None)


def remaining() -> float:
    end = _deadline.get()
    return max(0.0, end - time.monotonic()) if end is not None else float("inf")


@contextmanager
def check_deadline(seconds: float) -> Generator[None]:
    if _deadline.get() is not None:
        yield
        return
    token = _deadline.set(time.monotonic() + seconds)
    try:
        yield
    finally:
        _deadline.reset(token)


def raise_if_cancelling() -> None:
    """Preserve pending cancellation before translating a cleanup failure.

    Call after exiting an owned timeout so it can withdraw its own cancellation.
    """
    task = asyncio.current_task()
    if task is not None and task.cancelling():
        raise asyncio.CancelledError() from None
