"""`POST /v1/omissions`: run the validated omission preset on one summary.

The request and response are models of their own, not the pipeline's, so the
API's contract doesn't change when the internals do. Every finished check
answers 200, flagged or not (ADR 0008). No response or log line ever holds text
from the source or summary: error messages are either written to be safe, or
left to `groundline_core.api.errors`, which never repeats one.
"""

import logging
import time
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import ConfigDict

from groundline_core.api.common import (
    Alarm,
    ApiModel,
    CheckRequest,
    Usage,
    alarm_of,
    check_failures,
    check_responses,
    model_client,
    usage_of,
)
from groundline_core.auth import require_api_key
from groundline_core.core import ModelClient
from groundline_core.evaluate import DEFAULT_PRESET, evaluate
from groundline_core.evaluators.models import ALARM_STEP, AlarmOutput, Severity
from groundline_core.evaluators.omissions.models import ScoreOutput, VerdictLabel
from groundline_core.evaluators.report import cost_line, result_line
from groundline_core.presets import load_preset
from groundline_core.result import EvaluationResult

logger = logging.getLogger("groundline.api")

router = APIRouter()


class OmissionsRequest(CheckRequest):
    pass


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
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "flagged": False,
                    "omission_score": 0.7361,
                    "alarm": {"score": 0.475, "threshold": 0.9, "level": "strict"},
                    "omissions": [
                        {
                            "fact": "Motrin 600 mg every 6 hours for a week.",
                            "severity": "critical",
                            "verdict": "partial",
                            "missing_confidence": 0.95,
                            "quote": "Motrin 800 mg every 6 hours for a week.",
                        }
                    ],
                    "counts": {"total": 4, "present": 3, "partial": 1, "absent": 0},
                    "pipeline": "omission_all_opus",
                    "validated_preset": "omission_all_opus",
                    "usage": {
                        "calls": 4,
                        "input_tokens": 31620,
                        "output_tokens": 8140,
                        "cost_usd": 0.29,
                    },
                }
            ]
        }
    )

    flagged: bool
    """Whether the alarm score reached the threshold."""
    omission_score: float
    """From 0 to 1. Higher is better. A score below 0.5 means the summary is
    flagged, and a summary passes at 0.5 or above; the flag is exactly the
    alarm's. The score is the alarm score turned around and rescaled around the
    alarm's threshold. It shows whether a critical omission was found, not how
    good the summary is overall. A source with no facts scores 1: nothing
    critical was found, and `counts.total` is 0."""
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


@router.post(
    "/omissions",
    dependencies=[Depends(require_api_key)],
    responses=check_responses(load_preset(DEFAULT_PRESET)),
)
async def omissions(
    request: OmissionsRequest,
    client: Annotated[ModelClient | None, Depends(model_client)],
) -> OmissionsResponse:
    """Find the facts a summary leaves out, and whether to flag it.

    Runs the validated all-Opus omission check, which takes about 30-60 seconds
    and costs about $0.28 for a typical clinical note, so use a long client
    timeout. Answers 200 for every finished check, flagged or not."""
    spec = load_preset(DEFAULT_PRESET)
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
        omission_score=alarm.rescaled_score,
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
