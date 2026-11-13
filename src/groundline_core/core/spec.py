"""Pipeline specs: a pipeline described as data, and checked before it runs.

A spec can come from a preset file, a custom file, or (later) the pipeline-builder
UI or the API. `build_pipeline` checks it against the registry and reports every
problem at once, so a UI can show them all together.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic.json_schema import models_json_schema

from groundline_core.core.cache import stable_hash
from groundline_core.core.implementation import Implementation
from groundline_core.core.profile import DomainProfile, Placeholders
from groundline_core.core.registry import Registry


class StageSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", use_attribute_docstrings=True)

    step: str
    """The step this stage fills, such as `extract` or `check`."""
    implementation: str
    """How to do the step, such as `check.llm_batch`."""
    enabled: bool = True
    """Whether the stage runs. Only some stages can be switched off."""
    settings: dict[str, Any] = Field(default_factory=dict[str, Any])
    """The implementation's settings, such as its model and prompt."""


class PipelineSpec(BaseModel):
    model_config = ConfigDict(
        extra="forbid", use_attribute_docstrings=True, title="groundline pipeline spec"
    )

    name: str
    """The pipeline's name, such as `omission_all_opus`."""
    description: str = ""
    """What the pipeline is for."""
    profile: str | None = None
    """The domain profile this pipeline is meant for, such as `clinical`."""
    stages: list[StageSpec]
    """The stages, in order. A stage may only read steps that come before it."""


def spec_json_schema(registry: Registry) -> dict[str, Any]:
    """The JSON schema of a pipeline spec, for editors and the pipeline-builder
    UI. Each stage's settings are checked against the model of the
    implementation it names, which the plain `PipelineSpec` schema can't do.

    It can't check what depends on other files, such as whether a prompt exists
    in the profile; `build_pipeline` checks those before a run."""
    names = registry.implementations()
    impls = [registry.implementation(name) for name in names]
    refs, settings_defs = models_json_schema(
        [(impl, "validation") for impl in impls if impl is not None],
        ref_template="#/$defs/{model}",
    )

    schema = PipelineSpec.model_json_schema(ref_template="#/$defs/{model}")
    defs: dict[str, Any] = schema["$defs"]
    stage: dict[str, Any] = defs["StageSpec"]
    stage["properties"]["step"]["enum"] = registry.steps()
    stage["properties"]["implementation"]["enum"] = names
    rules: list[dict[str, Any]] = []
    for name, impl in zip(names, impls, strict=True):
        assert impl is not None
        then: dict[str, Any] = {
            "step": {"const": impl.step},
            "settings": refs[(impl, "validation")],
        }
        if not impl.can_disable:
            then["enabled"] = {"const": True}
        rules.append(
            {
                "if": {
                    "properties": {"implementation": {"const": name}},
                    "required": ["implementation"],
                },
                "then": {"properties": then},
            }
        )
    stage["allOf"] = rules

    for key, value in settings_defs.get("$defs", {}).items():
        if key in defs:
            raise ValueError(f"two schema definitions are both named '{key}'")
        defs[key] = value
    return schema


def load_spec(path: str | Path) -> PipelineSpec:
    """Load a spec from a YAML or JSON file (JSON is valid YAML)."""
    path = Path(path)
    try:
        return PipelineSpec.model_validate(yaml.safe_load(path.read_text()))
    except (OSError, yaml.YAMLError, ValidationError) as error:
        raise ValueError(f"{path}: {error}") from error


def with_settings(spec: PipelineSpec, step: str, **settings: Any) -> PipelineSpec:
    """A copy of `spec` with some of one stage's settings changed, e.g.
    `with_settings(spec, "check", effort="high")`."""
    if not any(s.step == step for s in spec.stages):
        raise ValueError(f"pipeline '{spec.name}' has no '{step}' stage")
    stages = [
        s.model_copy(update={"settings": {**s.settings, **settings}})
        if s.step == step
        else s
        for s in spec.stages
    ]
    return spec.model_copy(update={"stages": stages})


@dataclass(frozen=True)
class PipelineStage:
    step: str
    implementation: Implementation
    enabled: bool
    output_type: type[BaseModel]
    texts: Mapping[str, str] = field(default_factory=dict[str, str])
    """The profile texts the implementation declared, by name."""


@dataclass(frozen=True)
class Pipeline:
    """A spec that has passed every check, ready to run."""

    name: str
    stages: tuple[PipelineStage, ...]
    profile: DomainProfile | None = None

    @property
    def placeholders(self) -> Placeholders:
        """Which placeholders in the profile's texts mean the source and summary."""
        return self.profile.placeholders if self.profile else Placeholders()


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

        texts: dict[str, str] = {}
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
            else:
                texts[text] = profile.texts[text]

        stages.append(
            PipelineStage(
                step=stage.step,
                implementation=implementation,
                enabled=stage.enabled,
                output_type=registry.output_type(stage.step),
                texts=texts,
            )
        )

    if problems:
        raise SpecError(problems)
    return Pipeline(name=spec.name, stages=tuple(stages), profile=profile)


def pipeline_fingerprint(pipeline: Pipeline) -> str:
    """Identifies what a pipeline does, so two pipelines can be compared.

    Covers each stage's implementation, version, on/off state and settings
    (except selectable ones, such as which alarm level to use), plus the profile
    texts the stages use and the profile's placeholders. Names aren't included:
    a renamed copy that does the same thing has the same fingerprint.
    """
    stages: list[dict[str, Any]] = []
    texts: dict[str, str] = {}
    for stage in pipeline.stages:
        impl = stage.implementation
        settings = {
            name: value
            for name, value in impl.model_dump(mode="json").items()
            if name not in impl.selectable_settings
        }
        stages.append(
            {
                "step": stage.step,
                "implementation": impl.name,
                "version": impl.version,
                "enabled": stage.enabled,
                "settings": settings,
            }
        )
        texts.update(stage.texts)

    return stable_hash(
        {
            "stages": stages,
            "profile": None
            if pipeline.profile is None
            else {"placeholders": pipeline.placeholders.model_dump(), "texts": texts},
        }
    )
