"""Pipeline specs: a pipeline described as data, and checked before it runs.

A spec can come from a preset file, a custom file, or (later) the pipeline-builder
UI or the API. `build_pipeline` checks it against the registry and reports every
problem at once, so a UI can show them all together.
"""

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from groundline.core.implementation import Implementation
from groundline.core.profile import DomainProfile
from groundline.core.registry import Registry


class StageSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step: str
    implementation: str
    enabled: bool = True
    settings: dict[str, Any] = Field(default_factory=dict[str, Any])


class PipelineSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    profile: str | None = None
    """The domain profile this pipeline is meant for, such as `clinical`."""
    stages: list[StageSpec]


@dataclass(frozen=True)
class PipelineStage:
    step: str
    implementation: Implementation
    enabled: bool
    output_type: type[BaseModel]


@dataclass(frozen=True)
class Pipeline:
    """A spec that has passed every check, ready to run."""

    name: str
    stages: tuple[PipelineStage, ...]
    profile: DomainProfile | None = None


class SpecError(ValueError):
    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__(
            "invalid pipeline spec:\n" + "\n".join(f"- {p}" for p in problems)
        )


def build_pipeline(
    spec: PipelineSpec, registry: Registry, profile: DomainProfile | None = None
) -> Pipeline:
    """Check a spec and build its implementations.

    Each step may appear once, and a stage may only read steps that come earlier
    in the list. That rule also rules out dependency cycles.
    """
    problems: list[str] = []
    stages: list[PipelineStage] = []
    earlier: set[str] = set()

    for number, stage in enumerate(spec.stages, 1):
        where = f"stage {number} ({stage.step})"
        if stage.step in earlier:
            problems.append(f"{where}: step '{stage.step}' appears more than once")
            continue
        earlier.add(stage.step)

        impl_type = registry.implementation(stage.implementation)
        if impl_type is None:
            problems.append(f"{where}: unknown implementation '{stage.implementation}'")
            continue
        if impl_type.step != stage.step:
            problems.append(
                f"{where}: '{stage.implementation}' implements step "
                f"'{impl_type.step}', not '{stage.step}'"
            )
            continue
        if not stage.enabled and not impl_type.can_disable:
            problems.append(f"{where}: '{stage.implementation}' can't be switched off")
        for needed in impl_type.reads:
            if needed not in earlier or needed == stage.step:
                problems.append(
                    f"{where}: reads '{needed}', "
                    "which must come earlier in the pipeline"
                )

        try:
            implementation = impl_type.model_validate(stage.settings)
        except ValidationError as error:
            for e in error.errors():
                # A check across several settings has no single setting to name.
                setting = ".".join(str(part) for part in e["loc"])
                message = e["msg"].removeprefix("Value error, ")
                problems.append(
                    f"{where}: setting '{setting}': {message}"
                    if setting
                    else f"{where}: settings: {message}"
                )
            continue

        for text in implementation.profile_texts():
            if profile is None:
                problems.append(
                    f"{where}: uses text '{text}', so it needs a domain profile"
                )
            elif text not in profile.texts:
                problems.append(
                    f"{where}: uses text '{text}', "
                    f"which profile '{profile.name}' doesn't have"
                )

        stages.append(
            PipelineStage(
                step=stage.step,
                implementation=implementation,
                enabled=stage.enabled,
                output_type=registry.output_type(stage.step),
            )
        )

    if problems:
        raise SpecError(problems)
    return Pipeline(name=spec.name, stages=tuple(stages), profile=profile)
