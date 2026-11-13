"""`POST /v1/omissions`: run the validated omission preset on one summary.

The request and response are models of their own, not the pipeline's, so the
API's contract doesn't change when the internals do. Every finished check
answers 200, flagged or not (ADR 0008). No response or log line ever holds text
from the source or summary: error messages are either written to be safe, or
left to `groundline_core.api.errors`, which never repeats one.
"""

import logging
import time
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import ConfigDict

from groundline_core.api.common import (
    Alarm,
    CheckRequest,
    CheckResponse,
    Counts,
    Finding,
    Metadata,
    alarm_of,
    check_failures,
    check_responses,
    model_client,
    usage_of,
)
from groundline_core.api.findings import EXPLANATIONS, omission_finding
from groundline_core.auth import require_api_key
from groundline_core.core import ModelClient
from groundline_core.evaluate import DEFAULT_PRESET, evaluate
from groundline_core.evaluators.models import ALARM_STEP, AlarmOutput
from groundline_core.evaluators.omissions.models import RankOutput, ScoreOutput
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


class OmissionsMetadata(Metadata[OmissionsAlarm]):
    pass


class OmissionsResponse(CheckResponse[Literal["omissions"], OmissionsMetadata]):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "evaluation_type": "omissions",
                    "flagged": False,
                    "score": 0.7361,
                    "findings": [
                        {
                            "type": "omission",
                            "severity": "critical",
                            "verdict": "partial",
                            "confidence": 0.95,
                            "statement": "Motrin 600 mg every 6 hours for a week.",
                            "source_evidence": "take motrin 600 mg every 6 hours",
                            "summary_evidence": "Motrin 800 mg every 6 hours.",
                            "evidence_found": True,
                            "explanation": EXPLANATIONS["omission", "partial"],
                        }
                    ],
                    "counts": {"total": 4, "present": 3, "partial": 1, "absent": 0},
                    "metadata": {
                        "pipeline": "omission_all_opus",
                        "alarm": {"score": 0.475, "threshold": 0.9, "level": "strict"},
                        "usage": {
                            "calls": 4,
                            "input_tokens": 31620,
                            "output_tokens": 8140,
                            "cost_usd": 0.29,
                        },
                    },
                }
            ]
        }
    )

    score: float
    """From 0 to 1. Higher is better. A score below 0.5 means the summary is
    flagged, and a summary passes at 0.5 or above; the flag is exactly the
    alarm's. The score is the alarm score turned around and rescaled around the
    alarm's threshold. It shows whether a critical omission was found, not how
    good the summary is overall. A source with no facts scores 1: nothing
    critical was found, and `counts.total` is 0."""
    findings: list[Finding]
    """The facts not fully recorded, most important first."""


@router.post(
    "/omissions",
    operation_id="evaluate_omissions",
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
        len(response.findings),
        cost_line(result, time.perf_counter() - started),
    )
    return response


def _response(result: EvaluationResult) -> OmissionsResponse:
    scored = result.run.output("score", ScoreOutput)
    ranked_facts = {f.id: f for f in result.run.output("rank", RankOutput).facts}
    alarm = result.run.output(ALARM_STEP, AlarmOutput)
    return OmissionsResponse(
        evaluation_type="omissions",
        flagged=alarm.flagged,
        score=alarm.rescaled_score,
        findings=[
            omission_finding(m, ranked_facts[m.id]) for m in scored.missing_facts
        ],
        counts=Counts(
            total=scored.total,
            present=scored.present,
            partial=scored.partial,
            absent=scored.absent,
        ),
        metadata=OmissionsMetadata(
            pipeline=result.pipeline,
            alarm=alarm_of(alarm, OmissionsAlarm),
            usage=usage_of(result),
        ),
    )
