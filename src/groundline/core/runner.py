"""Run a pipeline: each stage starts as soon as the stages it reads have finished.

Stages that don't depend on each other run at the same time. If any stage fails,
the others are cancelled (so no further paid calls are made) and the run raises
a `StageError` naming the failed step.

With a cache, a stage whose cache key matches a stored entry isn't run: its
output and call records come from the cache. A stage's key covers its
implementation, version and settings, the inputs it uses, and the keys of the
stages it reads. So changing a stage re-runs it and everything after it, never
anything before it.
"""

import asyncio
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ValidationError

from groundline.core.cache import Cache, stable_hash
from groundline.core.calls import CallRecord, Usage, summarize_usage
from groundline.core.errors import SetupError
from groundline.core.implementation import StageContext
from groundline.core.inputs import Inputs
from groundline.core.model_client import ModelClient
from groundline.core.profile import Placeholders
from groundline.core.spec import Pipeline, PipelineStage


class StageError(Exception):
    """A stage failed. The original exception is kept as `__cause__`.

    `calls` holds every live model call the run made before it stopped,
    including those of the failed stage and of stages cancelled because of it,
    so a failed run's cost can still be reported."""

    def __init__(self, step: str, message: str) -> None:
        self.step = step
        self.calls: tuple[CallRecord, ...] = ()
        super().__init__(f"stage '{step}' failed: {message}")


@dataclass(frozen=True)
class StageRun:
    status: Literal["ran", "cached", "disabled"]
    seconds: float


@dataclass(frozen=True)
class RunResult:
    outputs: Mapping[str, BaseModel]
    stages: Mapping[str, StageRun]
    calls: tuple[CallRecord, ...]
    """Every model call, in pipeline order. Calls replayed from the cache are
    marked `cached`."""

    def output[T: BaseModel](self, step: str, kind: type[T]) -> T:
        """A step's output, checked to be of the expected type."""
        if step not in self.outputs:
            raise LookupError(f"the pipeline has no '{step}' stage")
        value = self.outputs[step]
        if not isinstance(value, kind):
            raise TypeError(
                f"output of '{step}' is {type(value).__name__}, not {kind.__name__}"
            )
        return value

    @property
    def usage(self) -> Usage:
        return summarize_usage(self.calls)


async def run_pipeline(
    pipeline: Pipeline,
    inputs: Inputs,
    *,
    cache: Cache | None = None,
    client: ModelClient | None = None,
) -> RunResult:
    """Run every stage. `client` is needed only if a stage calls a model."""
    outputs: dict[str, BaseModel] = {}
    runs: dict[str, StageRun] = {}
    calls: dict[str, list[CallRecord]] = {}
    keys: dict[str, str] = {}
    tasks: dict[str, asyncio.Task[None]] = {}
    contexts: dict[str, StageContext] = {}

    async def run_stage(stage: PipelineStage) -> None:
        impl = stage.implementation
        for needed in impl.reads:
            await tasks[needed]  # validation guarantees it was created earlier

        ctx = StageContext(
            inputs=inputs,
            step=stage.step,
            uses=impl.uses,
            outputs={needed: outputs[needed] for needed in impl.reads},
            texts=_texts(pipeline, stage),
            placeholders=pipeline.profile.placeholders
            if pipeline.profile
            else Placeholders(),
            client=client,
        )
        contexts[stage.step] = ctx
        upstream = {needed: keys[needed] for needed in impl.reads}
        started = time.perf_counter()
        try:
            if not stage.enabled:
                # Switched off: cheap, so never cached, but it gets its own key
                # so that stages after it notice the difference.
                keys[stage.step] = stable_hash(
                    {"disabled": impl.name, "upstream": upstream}
                )
                output = _checked(stage, impl.passthrough(ctx))
                stage_calls, status = [], "disabled"
            else:
                key = keys[stage.step] = _cache_key(pipeline, stage, inputs, upstream)
                hit = _read_cache(cache, stage, key) if cache else None
                if hit is not None:
                    output, stage_calls = hit
                    status = "cached"
                else:
                    output = _checked(stage, await impl.run(ctx))
                    stage_calls, status = ctx.calls, "ran"
                    if cache:
                        cache.put(
                            stage.step,
                            key,
                            {
                                "output": output.model_dump(mode="json"),
                                "calls": [c.model_dump() for c in stage_calls],
                            },
                        )
        except (StageError, SetupError):
            raise
        except Exception as error:
            raise StageError(stage.step, str(error)) from error

        outputs[stage.step] = output
        calls[stage.step] = stage_calls
        runs[stage.step] = StageRun(
            status=status, seconds=round(time.perf_counter() - started, 3)
        )

    try:
        async with asyncio.TaskGroup() as group:
            for stage in pipeline.stages:
                tasks[stage.step] = group.create_task(run_stage(stage))
    except ExceptionGroup as failed:
        # Report the first failure; the other stages were cancelled because of it.
        # Keep its original cause, and hide the task group's wrapper.
        first = failed.exceptions[0]
        if isinstance(first, StageError):
            first.calls = tuple(
                call
                for stage in pipeline.stages
                if stage.step in contexts
                for call in contexts[stage.step].calls
            )
        raise first from first.__cause__

    return RunResult(
        outputs=outputs,
        stages=runs,
        calls=tuple(c for stage in pipeline.stages for c in calls[stage.step]),
    )


def _checked(stage: PipelineStage, output: BaseModel) -> BaseModel:
    """Every implementation of a step must return that step's output type."""
    if not isinstance(output, stage.output_type):
        raise StageError(
            stage.step,
            f"{stage.implementation.name} returned {type(output).__name__}, "
            f"expected {stage.output_type.__name__}",
        )
    return output


def _texts(pipeline: Pipeline, stage: PipelineStage) -> dict[str, str]:
    """The profile texts a stage declared (validation checked they exist)."""
    names = stage.implementation.profile_texts()
    if not names or pipeline.profile is None:
        return {}
    return {name: pipeline.profile.texts[name] for name in names}


def _cache_key(
    pipeline: Pipeline, stage: PipelineStage, inputs: Inputs, upstream: dict[str, str]
) -> str:
    impl = stage.implementation
    texts = _texts(pipeline, stage)
    key: dict[str, object] = {
        "implementation": impl.name,
        "version": impl.version,
        "settings": impl.model_dump(mode="json"),
        "inputs": {name: inputs.sha256(name) for name in impl.uses},
        "upstream": upstream,
    }
    if texts and pipeline.profile:
        # The full text, so editing a prompt or rubric re-runs the stages using
        # it; and the placeholder names, which decide how the text is filled.
        key["texts"] = texts
        key["placeholders"] = pipeline.profile.placeholders.model_dump()
    return stable_hash(key)


def _read_cache(
    cache: Cache, stage: PipelineStage, key: str
) -> tuple[BaseModel, list[CallRecord]] | None:
    entry = cache.get(stage.step, key)
    if entry is None:
        return None
    try:
        output = stage.output_type.model_validate(entry["output"])
        calls = [
            CallRecord.model_validate({**c, "cached": True}) for c in entry["calls"]
        ]
    except (KeyError, TypeError, ValidationError):
        return None  # the wrong shape counts as a miss, and is overwritten
    return output, calls
