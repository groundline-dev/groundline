"""`POST /v1/commissions`: run the validated quick commission preset on one summary.

The request and response are models of their own, not the pipeline's, so the
API's contract doesn't change when the internals do. Every finished check
answers 200, flagged or not (ADR 0008). No response or log line ever holds text
from the source or summary; see `groundline.api.common`.
"""

import logging
import time
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import ConfigDict

from groundline.api.common import (
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
from groundline.auth import require_api_key
from groundline.core import ModelClient
from groundline.evaluate import DEFAULT_COMMISSIONS_PRESET, evaluate
from groundline.evaluators.commissions.models import (
    CommissionsRefuteOutput,
    SupportVerdict,
    ranked,
)
from groundline.evaluators.models import ALARM_STEP, AlarmOutput, Severity
from groundline.evaluators.report import cost_line, result_line
from groundline.presets import load_preset
from groundline.result import EvaluationResult

logger = logging.getLogger("groundline.api")

router = APIRouter()


class CommissionsRequest(CheckRequest):
    pass


class CommissionsAlarm(Alarm):
    score: float
    """The highest unsupported confidence among the critical commissions; 0 if
    there are none."""


class Commission(ApiModel):
    claim: str
    """The words in the summary that make the claim, word for word."""
    severity: Severity
    """How much it would matter if the claim were wrong."""
    support_verdict: SupportVerdict
    """unsupported if the source doesn't mention it, contradicted if the source
    says otherwise."""
    unsupported_confidence: float
    """From 0 to 1: how sure the check is that the source doesn't support it."""
    evidence: str | None
    """For a contradicted claim, the passage of the source the model quoted as
    contradicting it; null if it quoted none, and always null for an unsupported
    claim."""
    evidence_found: bool | None
    """Whether `evidence` appears in the source word for word, ignoring case,
    punctuation and spacing. True means verified. False only means it couldn't
    be matched, which a reworded quote or markup in the source, such as a
    scribe's tags, can cause; it doesn't mean the passage was invented. Null
    when there is no evidence."""


class CommissionsResponse(ApiModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "flagged": True,
                    "commission_score": 0.1667,
                    "alarm": {"score": 0.95, "threshold": 0.85, "level": "default"},
                    "commissions": [
                        {
                            "claim": "Motrin 800 mg every 6 hours for a week",
                            "severity": "critical",
                            "support_verdict": "contradicted",
                            "unsupported_confidence": 0.95,
                            "evidence": "Take Motrin 600 mg every 6 hours for a week",
                            "evidence_found": True,
                        }
                    ],
                    "pipeline": "commission_quick",
                    "validated_preset": "commission_quick",
                    "usage": {
                        "calls": 1,
                        "input_tokens": 9800,
                        "output_tokens": 420,
                        "cost_usd": 0.0012,
                    },
                }
            ]
        }
    )

    flagged: bool
    """Whether the alarm score reached the threshold."""
    commission_score: float
    """From 0 to 1. Higher is better. A score below 0.5 means the summary is
    flagged, and a summary passes at 0.5 or above; the flag is exactly the
    alarm's. The score is the alarm score turned round and rescaled around the
    alarm's threshold. A flag means a statement wasn't found in the source, for
    a reviewer to check; the score doesn't say how good the summary is overall.
    A summary with no claims the source fails to support scores 1."""
    alarm: CommissionsAlarm
    commissions: list[Commission]
    """Claims in the summary that the source doesn't support, most important
    first."""
    pipeline: str
    """The pipeline that ran."""
    validated_preset: str | None
    """The validated preset the run used, whose measured results apply to it."""
    usage: Usage
    """The model calls the check made, and what they cost."""


@router.post(
    "/commissions",
    dependencies=[Depends(require_api_key)],
    responses=check_responses(load_preset(DEFAULT_COMMISSIONS_PRESET)),
)
async def commissions(
    request: CommissionsRequest,
    client: Annotated[ModelClient | None, Depends(model_client)],
) -> CommissionsResponse:
    """Find the statements a summary makes that its source doesn't support, and
    whether to flag it.

    Runs the validated quick commission check (often called hallucination
    detection): one Claude Haiku 5.5 call that costs about $0.002 for a typical
    clinical note. A statement is a commission if the source contradicts it or
    never mentions it. Answers 200 for every finished check, flagged or not."""
    spec = load_preset(DEFAULT_COMMISSIONS_PRESET)
    started = time.perf_counter()
    with check_failures():
        result = await evaluate(
            request.source, request.summary, spec=spec, client=client
        )
    response = _response(result)
    logger.info(
        "checked a summary: %s · %d commissions · %s",
        result_line(result.run.output(ALARM_STEP, AlarmOutput)),
        len(response.commissions),
        cost_line(result, time.perf_counter() - started),
    )
    return response


def _response(result: EvaluationResult) -> CommissionsResponse:
    # Refute is off in the quick preset, so this holds the check's commissions.
    found = result.run.output("commissions_refute", CommissionsRefuteOutput)
    alarm = result.run.output(ALARM_STEP, AlarmOutput)
    return CommissionsResponse(
        flagged=alarm.flagged,
        commission_score=alarm.rescaled_score,
        alarm=alarm_of(alarm, CommissionsAlarm),
        commissions=[
            Commission(
                claim=c.quote,
                severity=c.severity,
                support_verdict=c.support_verdict,
                unsupported_confidence=c.unsupported_confidence,
                evidence=c.evidence,
                evidence_found=c.evidence_found,
            )
            for c in ranked(found.commissions)
        ],
        pipeline=result.pipeline,
        validated_preset=result.validated_preset,
        usage=usage_of(result),
    )
