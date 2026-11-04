"""`POST /v1/omissions`: run the validated omission preset on one summary.

The request and response are models of their own, not the pipeline's, so the
API's contract doesn't change when the internals do. Every finished check
answers 200, flagged or not (ADR 0008). No response or log line ever holds text
from the source or summary: error messages are either written to be safe, or
left to `groundline.api.errors`, which never repeats one.
"""

import logging
import time
from typing import Annotated, Literal

import anthropic
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from groundline.core import (
    ModelClient,
    ModelError,
    ReplyError,
    SetupError,
    StageError,
    summarize_usage,
    with_settings,
)
from groundline.evaluate import DEFAULT_PRESET, EvaluationResult, evaluate
from groundline.evaluators.models import AlarmOutput, Severity
from groundline.evaluators.omissions.models import ScoreOutput, VerdictLabel
from groundline.evaluators.report import cost_line, format_cost, result_line
from groundline.presets import load_preset

logger = logging.getLogger("groundline.api")

MAX_CHARS = 200_000
"""The longest source or summary accepted, far longer than any consultation, so
one request can't spend dollars on a huge input."""

ThresholdLevel = Literal["strict", "moderate"]
"""The preset's threshold levels. A test keeps this in step with the preset."""

# Stage failures caused by the model or Anthropic's API, which answer 502. Our
# own two are written to be safe to show; the SDK's can quote the request, so
# only its type is shown. Any other failure is a bug of ours, and its message
# may quote the source or summary.
UPSTREAM_CAUSES = (ReplyError, ModelError, anthropic.APIError)

router = APIRouter(prefix="/v1")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", use_attribute_docstrings=True)


class OmissionsRequest(_Model):
    source: Annotated[str, Field(min_length=1, max_length=MAX_CHARS)]
    """The original material, such as a consultation transcript."""
    summary: Annotated[str, Field(min_length=1, max_length=MAX_CHARS)]
    """The AI-written summary to check, such as a clinical note."""
    threshold: ThresholdLevel = "strict"
    """Which alarm level to flag at. strict (0.90) flags fewer clean summaries;
    moderate (0.425) catches more omissions. Either keeps the run on the
    validated preset."""

    @field_validator("source", "summary")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value


class Alarm(_Model):
    score: float
    """The highest missing confidence among critical facts still flagged, with
    partials at half weight; 0 if there are none."""
    threshold: float
    """The alarm score at or above which the summary is flagged."""
    level: str
    """The threshold level used, such as strict."""


class Omission(_Model):
    fact: str
    """The fact from the source that the summary leaves out or only partly records."""
    severity: Severity | None
    """How much it would matter if the summary left the fact out."""
    verdict: VerdictLabel
    """partial or absent."""
    missing_confidence: float | None
    """From 0 to 1: how sure the check is that the fact is missing."""
    quote: str | None
    """For a partial fact, the words in the summary that partly record it."""


class Counts(_Model):
    total: int
    """Facts found in the source."""
    present: int
    partial: int
    absent: int


class Usage(_Model):
    calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: float


class OmissionsResponse(_Model):
    flagged: bool
    """Whether the alarm score reached the threshold."""
    alarm: Alarm
    omissions: list[Omission]
    """Facts not fully recorded, most important first."""
    counts: Counts
    pipeline: str
    """The pipeline that ran."""
    validated_preset: str | None
    """The validated preset the run used, whose measured results apply to it."""
    usage: Usage
    """The model calls the check made, and what they cost."""


def model_client() -> ModelClient | None:
    """The model client to use; None means the default Anthropic client.
    Tests override this dependency with a scripted fake."""
    return None


@router.post(
    "/omissions",
    responses={
        502: {"description": "The model or Anthropic's API failed."},
        503: {"description": "The Anthropic API key is missing or rejected."},
    },
)
async def omissions(
    request: OmissionsRequest,
    client: Annotated[ModelClient | None, Depends(model_client)],
) -> OmissionsResponse:
    """Find the facts a summary leaves out, and whether to flag it.

    Runs the validated all-Opus omission check, which takes about 30-60 seconds
    and costs about $0.28 for a typical clinical note, so use a long client
    timeout. Answers 200 for every finished check, flagged or not."""
    spec = with_settings(load_preset(DEFAULT_PRESET), "alarm", level=request.threshold)
    started = time.perf_counter()
    try:
        result = await evaluate(
            request.source, request.summary, spec=spec, client=client
        )
    except SetupError as error:
        logger.warning("can't check: %s", error)
        raise HTTPException(503, detail=str(error)) from error
    except StageError as error:
        cause = type(error.__cause__).__name__
        logger.warning("stage '%s' failed (%s)%s", error.step, cause, _spent(error))
        if not isinstance(error.__cause__, UPSTREAM_CAUSES):
            raise  # a bug of ours: the app-wide handler answers 500
        raise HTTPException(502, detail=f"{_failure(error)}{_spent(error)}") from error
    response = _response(result)
    logger.info(
        "checked a summary: %s · %d omissions · %s",
        result_line(result.run.output("alarm", AlarmOutput)),
        len(response.omissions),
        cost_line(result, time.perf_counter() - started),
    )
    return response


def _response(result: EvaluationResult) -> OmissionsResponse:
    score = result.run.output("score", ScoreOutput)
    alarm = result.run.output("alarm", AlarmOutput)
    usage = result.run.usage
    return OmissionsResponse(
        flagged=alarm.flagged,
        alarm=Alarm(score=alarm.score, threshold=alarm.threshold, level=alarm.level),
        omissions=[
            Omission(
                fact=m.fact,
                severity=m.severity,
                verdict=m.verdict,
                missing_confidence=m.score,
                quote=m.quote,
            )
            for m in score.missing_facts
        ],
        counts=Counts(
            total=score.total,
            present=score.present,
            partial=score.partial,
            absent=score.absent,
        ),
        pipeline=result.pipeline,
        validated_preset=result.validated_preset,
        usage=Usage(
            calls=usage.calls,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cost_usd=usage.cost_usd,
        ),
    )


def _failure(error: StageError) -> str:
    """What went wrong upstream. An SDK error is named, never quoted."""
    cause = error.__cause__
    if not isinstance(cause, anthropic.APIError):
        return str(error)
    if isinstance(cause, anthropic.APIStatusError):
        what = f"answered {cause.status_code}"
    else:
        what = "failed"
    name = type(cause).__name__
    return f"stage '{error.step}' failed: the Anthropic API {what} ({name})"


def _spent(error: StageError) -> str:
    """What a failed check had cost, if it made any calls."""
    if not error.calls:
        return ""
    usage = summarize_usage(error.calls)
    return (
        f". Cost before stopping: {format_cost(usage.cost_usd)} · {usage.calls} calls"
    )
