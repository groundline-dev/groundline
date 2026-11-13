"""Internal monotonic check allowance, inherited by child tasks."""

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
