"""Run a pipeline: each stage starts as soon as the stages it reads have finished.

Stages that don't depend on each other run at the same time. If any stage fails,
the others are cancelled (so no further paid calls are made) and the run raises
a `StageError` naming the failed step.
"""

import asyncio
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

from groundline.core.implementation import StageContext
from groundline.core.inputs import Inputs
from groundline.core.spec import Pipeline, PipelineStage


class StageError(Exception):
    """A stage failed. The original exception is kept as `__cause__`."""

    def __init__(self, step: str, message: str) -> None:
        self.step = step
        super().__init__(f"stage '{step}' failed: {message}")


@dataclass(frozen=True)
class StageRun:
    status: Literal["ran", "disabled"]
    seconds: float


@dataclass(frozen=True)
class RunResult:
    outputs: Mapping[str, BaseModel]
    stages: Mapping[str, StageRun]

    def output[T: BaseModel](self, step: str, kind: type[T]) -> T:
        value = self.outputs[step]
        if not isinstance(value, kind):
            raise TypeError(
                f"output of '{step}' is {type(value).__name__}, not {kind.__name__}"
            )
        return value


async def run_pipeline(pipeline: Pipeline, inputs: Inputs) -> RunResult:
    outputs: dict[str, BaseModel] = {}
    runs: dict[str, StageRun] = {}
    tasks: dict[str, asyncio.Task[None]] = {}

    async def run_stage(stage: PipelineStage) -> None:
        impl = stage.implementation
        for needed in impl.reads:
            await tasks[needed]  # validation guarantees it was created earlier

        ctx = StageContext(
            inputs=inputs,
            step=stage.step,
            outputs={needed: outputs[needed] for needed in impl.reads},
        )
        started = time.perf_counter()
        try:
            if stage.enabled:
                output = await impl.run(ctx)
            else:
                output = impl.passthrough(ctx)
        except Exception as error:
            raise StageError(stage.step, str(error)) from error

        if not isinstance(output, stage.output_type):
            raise StageError(
                stage.step,
                f"{impl.name} returned {type(output).__name__}, "
                f"expected {stage.output_type.__name__}",
            )
        outputs[stage.step] = output
        runs[stage.step] = StageRun(
            status="ran" if stage.enabled else "disabled",
            seconds=round(time.perf_counter() - started, 3),
        )

    try:
        async with asyncio.TaskGroup() as group:
            for stage in pipeline.stages:
                tasks[stage.step] = group.create_task(run_stage(stage))
    except ExceptionGroup as failed:
        # Report the first failure; the other stages were cancelled because of it.
        # Keep its original cause, and hide the task group's wrapper.
        first = failed.exceptions[0]
        raise first from first.__cause__

    return RunResult(outputs=outputs, stages=runs)
