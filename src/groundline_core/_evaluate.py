"""`evaluate`: the entry point for checking a summary against its source.

The CLI and the API both call it, so they behave the same.
Only where the Anthropic API key comes from differs: the CLI and the API also
read `.env`, and library callers name a `.env` file only if they want one.
"""

import asyncio
import logging
from typing import Literal

from pydantic import SecretStr

from groundline_core._clients import AnthropicClient
from groundline_core._core import (
    Cache,
    DomainProfile,
    Inputs,
    ModelClient,
    Pipeline,
    PipelineSpec,
    Registry,
    build_pipeline,
    pipeline_fingerprint,
    run_pipeline,
)
from groundline_core._core.deadline import check_deadline, has_deadline, remaining
from groundline_core._evaluators.builtin import default_registry
from groundline_core._evaluators.models import ALARM_STEP, AlarmOutput
from groundline_core._findings import evaluation_type_of, findings_and_counts
from groundline_core._result import Alarm, EvaluationResult, Usage
from groundline_core._settings import (
    EnvFile,
    ModelSettings,
    anthropic_key,
    read_check_settings,
)
from groundline_core.presets import builtin_presets, load_preset
from groundline_core.profiles import load_builtin_profile

logger = logging.getLogger("groundline.evaluate")

DEFAULT_PRESET = "omission_all_opus"
DEFAULT_COMMISSIONS_PRESET = "commission_quick"

CommissionPreset = Literal["commission_quick", "commission_reverse_lean"]
"""The commission presets a caller can choose, in the API and the CLI."""


async def evaluate(
    source: str,
    summary: str,
    *,
    spec: PipelineSpec | None = None,
    profile: DomainProfile | None = None,
    cache: Cache | None = None,
    client: ModelClient | None = None,
    registry: Registry | None = None,
    anthropic_api_key: str | SecretStr | None = None,
    env_file: EnvFile = None,
) -> EvaluationResult:
    """Check one summary against its source, by default with the all-Opus omission
    preset.

    The pipeline must be a full check: the alarm step, plus the steps one check's
    findings are read from. Anything else is a `SpecError`, raised before the key
    is looked up or any model is called.

    `profile` defaults to the built-in profile the spec names. `client` defaults
    to an Anthropic client using `anthropic_api_key`, or else ANTHROPIC_API_KEY
    from the environment, or else from `env_file` if one is named: no `.env` is
    read otherwise. The spec is checked and the key looked up before any model
    call, so mistakes cost nothing.

    Reuse an enclosing check deadline. Otherwise read only the check timeout
    (120 seconds by default), even with a supplied client.
    Provider configuration is loaded only when creating the default client.
    """
    spec = spec or load_preset(DEFAULT_PRESET)
    evaluation_type = evaluation_type_of(spec)
    if client is not None and anthropic_api_key is not None:
        raise ValueError("pass client or anthropic_api_key, not both")
    seconds = (
        remaining()
        if has_deadline()
        else read_check_settings(env_file).check_timeout_seconds
    )
    with check_deadline(seconds):
        registry = registry or default_registry()
        if profile is None and spec.profile is not None:
            profile = load_builtin_profile(spec.profile)
        pipeline = build_pipeline(spec, registry, profile)
        validated, warnings = _compare_with_presets(pipeline)

        own_client = None
        needed_by = model_users(pipeline)
        if client is None and needed_by:
            key = anthropic_key(
                anthropic_api_key, env_file=env_file, needed_by=needed_by
            )
            client = own_client = AnthropicClient(
                key.value.get_secret_value(),
                key_name=key.name,
                key_source=key.source,
                env_file=env_file,
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
                # Closing a provider connection is bounded even after the check
                # expired. Cleanup errors must not replace its original failure.
                try:
                    async with asyncio.timeout(
                        ModelSettings.model_connect_timeout_seconds
                    ):
                        await own_client.close()
                except Exception as error:
                    logger.warning(
                        "model client cleanup failed (%s)", type(error).__name__
                    )

    alarm = run.output(ALARM_STEP, AlarmOutput)
    findings, counts = findings_and_counts(run, evaluation_type)
    return EvaluationResult(
        pipeline=spec.name,
        profile=profile.name if profile else None,
        validated_preset=validated,
        evaluation_type=evaluation_type,
        flagged=alarm.flagged,
        score=alarm.rescaled_score,
        findings=findings,
        counts=counts,
        alarm=Alarm(score=alarm.score, threshold=alarm.threshold, level=alarm.level),
        usage=Usage(
            calls=run.usage.calls,
            input_tokens=run.usage.input_tokens,
            output_tokens=run.usage.output_tokens,
            cost_usd=run.usage.cost_usd,
        ),
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
    anthropic_api_key: str | SecretStr | None = None,
    env_file: EnvFile = None,
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
            anthropic_api_key=anthropic_api_key,
            env_file=env_file,
        )
    )


def model_users(pipeline: Pipeline) -> str:
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
