"""Watchdogs that distinguish application timeouts from a stalled test."""

import asyncio
from collections.abc import Coroutine
from typing import Any


async def finish_before_watchdog[T](
    operation: Coroutine[Any, Any, T], *, seconds: float = 3
) -> T:
    task = asyncio.create_task(operation)
    try:
        done, _ = await asyncio.wait({task}, timeout=seconds)
        assert done, "application must finish before the test watchdog"
        return task.result()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
