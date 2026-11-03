"""`evaluate`: the single entry point for running a pipeline on a source and summary.

The CLI, the API and evaluator adapters all call this, so they behave the same.
"""

import asyncio
from dataclasses import dataclass, field

from groundline.clients import AnthropicClient
from groundline.core import (
    Cache,
    DomainProfile,
    Inputs,
    ModelClient,
    Pipeline,
    PipelineSpec,
    Registry,
    RunResult,
    build_pipeline,
    pipeline_fingerprint,
    run_pipeline,
)
from groundline.evaluators.commissions import register_commissions
from groundline.evaluators.omissions import register_omissions
from groundline.presets import builtin_presets, load_preset
from groundline.profiles import load_builtin_profile
from groundline.settings import Settings

DEFAULT_PRESET = "omission_all_opus"
DEFAULT_COMMISSIONS_PRESET = "commission_quick"


@dataclass(frozen=True)
class EvaluationResult:
    pipeline: str
    profile: str | None
    validated_preset: str | None
    """The built-in preset this run matched exactly (ignoring selectable
    settings), or None if the pipeline or profile was changed."""
    run: RunResult
    """Every stage's output, status, model calls, usage and cost."""
    warnings: list[str] = field(default_factory=list[str])


def default_registry() -> Registry:
    """A registry with every built-in step and implementation."""
    registry = Registry()
    register_omissions(registry)
    register_commissions(registry)
    return registry


async def evaluate(
    source: str,
    summary: str,
    *,
    spec: PipelineSpec | None = None,
    profile: DomainProfile | None = None,
    cache: Cache | None = None,
    client: ModelClient | None = None,
    registry: Registry | None = None,
) -> EvaluationResult:
    """Run a pipeline (by default the all-Opus omission preset) on one summary.

    `profile` defaults to the built-in profile the spec names. `client` defaults
    to an Anthropic client using ANTHROPIC_API_KEY. The spec is checked and the
    key looked up before any model call, so mistakes cost nothing.
    """
    spec = spec or load_preset(DEFAULT_PRESET)
    registry = registry or default_registry()
    if profile is None and spec.profile is not None:
        profile = load_builtin_profile(spec.profile)
    pipeline = build_pipeline(spec, registry, profile)
    validated, warnings = _compare_with_presets(pipeline)

    own_client = None
    needed_by = _model_users(pipeline)
    if client is None and needed_by:
        settings = Settings()
        client = own_client = AnthropicClient(
            settings.anthropic_key(needed_by=needed_by),
            key_source=settings.anthropic_key_source(),
        )
    try:
        run = await run_pipeline(
            pipeline,
            Inputs(source=source, summary=summary),
            cache=cache,
            client=client,
        )
    finally:
        if own_client is not None:
            await own_client.close()

    return EvaluationResult(
        pipeline=spec.name,
        profile=profile.name if profile else None,
        validated_preset=validated,
        run=run,
        warnings=warnings,
    )


def evaluate_sync(
    source: str,
    summary: str,
    *,
    spec: PipelineSpec | None = None,
    profile: DomainProfile | None = None,
    cache: Cache | None = None,
    client: ModelClient | None = None,
    registry: Registry | None = None,
) -> EvaluationResult:
    """`evaluate` for code that isn't async, such as scripts."""
    return asyncio.run(
        evaluate(
            source,
            summary,
            spec=spec,
            profile=profile,
            cache=cache,
            client=client,
            registry=registry,
        )
    )


def _model_users(pipeline: Pipeline) -> str:
    """Which stages call which models, e.g. "extract, rank (claude-opus-5-5)";
    empty if no enabled stage calls a model."""
    steps_by_model: dict[str, list[str]] = {}
    for stage in pipeline.stages:
        if stage.enabled:
            for model in stage.implementation.models():
                steps_by_model.setdefault(model, []).append(stage.step)
    return "; ".join(
        f"{', '.join(steps)} ({model})" for model, steps in steps_by_model.items()
    )


def _compare_with_presets(pipeline: Pipeline) -> tuple[str | None, list[str]]:
    """Which preset this pipeline matches exactly, if any; otherwise warnings for
    calibrated settings (like alarm thresholds) copied from a preset it differs
    from."""
    fingerprint = pipeline_fingerprint(pipeline)
    builtins = default_registry()  # presets use only built-in stages
    presets: dict[str, Pipeline] = {}
    for name in builtin_presets():
        spec = load_preset(name)
        profile = load_builtin_profile(spec.profile) if spec.profile else None
        presets[name] = build_pipeline(spec, builtins, profile)
        if pipeline_fingerprint(presets[name]) == fingerprint:
            return name, []

    warnings: list[str] = []
    for name, preset in presets.items():
        preset_stages = {s.implementation.name: s for s in preset.stages}
        for stage in pipeline.stages:
            impl = stage.implementation
            match = preset_stages.get(impl.name)
            if match is None:
                continue
            for setting in impl.calibrated_settings:
                if getattr(impl, setting) == getattr(match.implementation, setting):
                    warnings.append(
                        f"the {stage.step} {setting} were calibrated for {name}, "
                        "and this pipeline differs from it, "
                        "so they haven't been calibrated for it"
                    )
    return None, warnings
