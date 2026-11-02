"""The pipeline framework: steps, implementations, specs and the runner."""

from groundline.core.implementation import Implementation, StageContext
from groundline.core.inputs import Inputs
from groundline.core.registry import Registry
from groundline.core.runner import RunResult, StageError, StageRun, run_pipeline
from groundline.core.spec import (
    Pipeline,
    PipelineSpec,
    PipelineStage,
    SpecError,
    StageSpec,
    build_pipeline,
)

__all__ = [
    "Implementation",
    "Inputs",
    "Pipeline",
    "PipelineSpec",
    "PipelineStage",
    "Registry",
    "RunResult",
    "SpecError",
    "StageContext",
    "StageError",
    "StageRun",
    "StageSpec",
    "build_pipeline",
    "run_pipeline",
]
