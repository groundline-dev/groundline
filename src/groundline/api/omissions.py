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

from fastapi import APIRouter, Depends

from groundline.api.common import (
    FAILURE_RESPONSES,
    Alarm,
    ApiModel,
    CheckRequest,
    Usage,
    alarm_of,
    check_failures,
    model_client,
    usage_of,
)
from groundline.core import ModelClient, with_settings
from groundline.evaluate import DEFAULT_PRESET, evaluate
from groundline.evaluators.models import ALARM_STEP, AlarmOutput, Severity
from groundline.evaluators.omissions.models import ScoreOutput, VerdictLabel
from groundline.evaluators.report import cost_line, result_line
from groundline.presets import load_preset
from groundline.result import EvaluationResult

logger = logging.getLogger("groundline.api")

ThresholdLevel = Literal["strict", "moderate"]
"""The preset's threshold levels. A test keeps this in step with the preset."""

router = APIRouter(prefix="/v1")


class OmissionsRequest(CheckRequest):
    threshold: ThresholdLevel = "strict"
    """Which alarm level to flag at. strict (0.90) flags fewer clean summaries;
    moderate (0.425) catches more omissions. Either keeps the run on the
    validated preset."""


class OmissionsAlarm(Alarm):
    score: float
    """The highest missing confidence among critical facts still flagged, with
    partials at half weight; 0 if there are none."""


class Omission(ApiModel):
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


class Counts(ApiModel):
    total: int
    """Facts found in the source."""
    present: int
    partial: int
    absent: int


class OmissionsResponse(ApiModel):
    flagged: bool
    """Whether the alarm score reached the threshold."""
    alarm: OmissionsAlarm
    omissions: list[Omission]
    """Facts not fully recorded, most important first."""
    counts: Counts
    pipeline: str
    """The pipeline that ran."""
    validated_preset: str | None
    """The validated preset the run used, whose measured results apply to it."""
    usage: Usage
    """The model calls the check made, and what they cost."""


@router.post("/omissions", responses=FAILURE_RESPONSES)
async def omissions(
    request: OmissionsRequest,
    client: Annotated[ModelClient | None, Depends(model_client)],
) -> OmissionsResponse:
    """Find the facts a summary leaves out, and whether to flag it.

    Runs the validated all-Opus omission check, which takes about 30-60 seconds
    and costs about $0.28 for a typical clinical note, so use a long client
    timeout. Answers 200 for every finished check, flagged or not."""
    spec = with_settings(
        load_preset(DEFAULT_PRESET), ALARM_STEP, level=request.threshold
    )
    started = time.perf_counter()
    with check_failures():
        result = await evaluate(
            request.source, request.summary, spec=spec, client=client
        )
    response = _response(result)
    logger.info(
        "checked a summary: %s · %d omissions · %s",
        result_line(result.run.output(ALARM_STEP, AlarmOutput)),
        len(response.omissions),
        cost_line(result, time.perf_counter() - started),
    )
    return response


def _response(result: EvaluationResult) -> OmissionsResponse:
    score = result.run.output("score", ScoreOutput)
    alarm = result.run.output(ALARM_STEP, AlarmOutput)
    return OmissionsResponse(
        flagged=alarm.flagged,
        alarm=alarm_of(alarm, OmissionsAlarm),
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
        usage=usage_of(result),
    )
