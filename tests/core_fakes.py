"""Small fake stages for testing the core framework without any model calls."""

import asyncio
from typing import ClassVar

from pydantic import BaseModel

from groundline_core.core import (
    Implementation,
    InputName,
    Registry,
    SetupError,
    StageContext,
)

EXECUTED: list[str] = []
"""Steps that actually ran (not cached), in order. Tests clear it first."""


class Text(BaseModel):
    value: str


class Upper(Implementation):
    """Step `upper`: the source in capitals."""

    name: ClassVar[str] = "upper.basic"
    step: ClassVar[str] = "upper"
    uses: ClassVar[tuple[InputName, ...]] = ("source",)

    async def run(self, ctx: StageContext) -> Text:
        EXECUTED.append(self.step)
        ctx.record_call(model="fake-model", input_tokens=100, output_tokens=10)
        return Text(value=ctx.source.upper())


class Join(Implementation):
    """Step `join`: the `upper` output and the summary, joined by `sep`."""

    name: ClassVar[str] = "join.basic"
    step: ClassVar[str] = "join"
    reads: ClassVar[tuple[str, ...]] = ("upper",)
    uses: ClassVar[tuple[InputName, ...]] = ("summary",)

    sep: str = "+"

    async def run(self, ctx: StageContext) -> Text:
        EXECUTED.append(self.step)
        upper = ctx.output("upper", Text)
        return Text(value=f"{upper.value}{self.sep}{ctx.summary}")


class Polish(Implementation):
    """Step `polish`: optional; adds "!". Switched off, it passes `join` through."""

    name: ClassVar[str] = "polish.basic"
    step: ClassVar[str] = "polish"
    reads: ClassVar[tuple[str, ...]] = ("join",)
    can_disable: ClassVar[bool] = True

    async def run(self, ctx: StageContext) -> Text:
        EXECUTED.append(self.step)
        return Text(value=ctx.output("join", Text).value + "!")

    def passthrough(self, ctx: StageContext) -> Text:
        return ctx.output("join", Text)


class SlowLeft(Implementation):
    name: ClassVar[str] = "left.slow"
    step: ClassVar[str] = "left"

    delay: float = 0.3

    async def run(self, ctx: StageContext) -> Text:
        await asyncio.sleep(self.delay)
        return Text(value="left")


class SlowRight(Implementation):
    name: ClassVar[str] = "right.slow"
    step: ClassVar[str] = "right"

    delay: float = 0.3

    async def run(self, ctx: StageContext) -> Text:
        await asyncio.sleep(self.delay)
        return Text(value="right")


class Both(Implementation):
    name: ClassVar[str] = "both.basic"
    step: ClassVar[str] = "both"
    reads: ClassVar[tuple[str, ...]] = ("left", "right")

    async def run(self, ctx: StageContext) -> Text:
        left, right = ctx.output("left", Text), ctx.output("right", Text)
        return Text(value=f"{left.value}+{right.value}")


class Boom(Implementation):
    """Step `left`, failing at once."""

    name: ClassVar[str] = "left.boom"
    step: ClassVar[str] = "left"

    async def run(self, ctx: StageContext) -> Text:
        raise RuntimeError("model unavailable")


class PaidBoom(Implementation):
    """Step `left`, after `upper`: makes a model call, then fails."""

    name: ClassVar[str] = "left.paid_boom"
    step: ClassVar[str] = "left"
    reads: ClassVar[tuple[str, ...]] = ("upper",)

    async def run(self, ctx: StageContext) -> Text:
        ctx.record_call(model="fake-model", input_tokens=50, output_tokens=5)
        raise RuntimeError("reply unusable")


class SetupProblem(Implementation):
    """Step `left`, failing because of the environment, not the stage."""

    name: ClassVar[str] = "left.setup"
    step: ClassVar[str] = "left"

    async def run(self, ctx: StageContext) -> Text:
        raise SetupError("key was rejected", "key was rejected")


class Peeker(Implementation):
    """Step `upper`, reading the summary without declaring it."""

    name: ClassVar[str] = "upper.peeker"
    step: ClassVar[str] = "upper"

    async def run(self, ctx: StageContext) -> Text:
        return Text(value=ctx.summary)


class Sneaky(Implementation):
    """Step `join`, reading `upper` without declaring it."""

    name: ClassVar[str] = "join.sneaky"
    step: ClassVar[str] = "join"

    async def run(self, ctx: StageContext) -> Text:
        return ctx.output("upper", Text)


class WrongOutput(BaseModel):
    number: int


class WrongType(Implementation):
    """Step `upper`, returning the wrong output type."""

    name: ClassVar[str] = "upper.wrong"
    step: ClassVar[str] = "upper"

    async def run(self, ctx: StageContext) -> WrongOutput:
        return WrongOutput(number=1)


def make_registry() -> Registry:
    registry = Registry()
    for step in ("upper", "join", "polish", "left", "right", "both"):
        registry.define_step(step, Text)
    for impl in (
        Upper,
        Join,
        Polish,
        SlowLeft,
        SlowRight,
        Both,
        Boom,
        PaidBoom,
        Sneaky,
        Peeker,
        SetupProblem,
    ):
        registry.register(impl)
    return registry
