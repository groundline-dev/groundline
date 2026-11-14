"""`POST /v1/commissions`: run a validated commission preset on one summary: the
quick one unless the caller chooses the lean one.

The request and response are models of their own, not the pipeline's, so the
API's contract doesn't change when the internals do. Every finished check
answers 200, flagged or not (ADR 0008). No response or log line ever holds text
from the source or summary; see `groundline_core.api.common`.
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
    Finding,
    Metadata,
    alarm_of,
    check_failures,
    check_responses,
    model_client,
    usage_of,
)
from groundline_core.api.findings import EXPLANATIONS, commission_finding
from groundline_core.auth import require_api_key
from groundline_core.core import ModelClient
from groundline_core.evaluate import DEFAULT_COMMISSIONS_PRESET, evaluate
from groundline_core.evaluators.commissions.models import (
    CommissionsRefuteOutput,
    ranked,
)
from groundline_core.evaluators.models import ALARM_STEP, AlarmOutput
from groundline_core.evaluators.report import cost_line, result_line
from groundline_core.presets import load_preset
from groundline_core.result import EvaluationResult

logger = logging.getLogger("groundline.api")

router = APIRouter()


CommissionPreset = Literal["commission_quick", "commission_reverse_lean"]
"""The commission presets a caller can choose."""


class CommissionsRequest(CheckRequest):
    preset: CommissionPreset = DEFAULT_COMMISSIONS_PRESET
    """Which validated preset to run. `commission_quick` (the default) is one
    Claude Haiku 5.5 call, about $0.002 per note; measured on 172 commission pairs
    it caught 151, with 1 of 110 clean notes flagged, confirmed to its
    pre-registered standard. `commission_reverse_lean` is three Claude Opus 5.5
    calls, about $0.28 per note: it caught 166, with 0 of 110 clean notes
    flagged. It matched the exact preset's catches, but missed its pre-registered
    margin by less than two pairs, so it isn't confirmed to that standard."""


class CommissionsAlarm(Alarm):
    score: float
    """The highest unsupported confidence among the critical commissions; 0 if
    there are none."""


class CommissionsMetadata(Metadata[CommissionsAlarm]):
    pass


class CommissionsResponse(CheckResponse[Literal["commissions"], CommissionsMetadata]):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "evaluation_type": "commissions",
                    "flagged": True,
                    "score": 0.1667,
                    "findings": [
                        {
                            "type": "commission",
                            "severity": "critical",
                            "verdict": "contradicted",
                            "confidence": 0.95,
                            "statement": "Motrin 800 mg every 6 hours for a week",
                            "source_evidence": "Take Motrin 600 mg every 6 hours",
                            "summary_evidence": None,
                            "evidence_found": True,
                            "explanation": EXPLANATIONS["commission", "contradicted"],
                        }
                    ],
                    "counts": None,
                    "metadata": {
                        "pipeline": "commission_quick",
                        "alarm": {"score": 0.95, "threshold": 0.85, "level": "default"},
                        "usage": {
                            "calls": 1,
                            "input_tokens": 9800,
                            "output_tokens": 420,
                            "cost_usd": 0.0012,
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
    alarm's threshold. A flag means a claim wasn't found in the source, for
    a reviewer to check; the score doesn't say how good the summary is overall.
    A summary with no claims the source fails to support scores 1."""
    findings: list[Finding]
    """The claims in the summary that the source doesn't support, most important
    first."""


@router.post(
    "/commissions",
    dependencies=[Depends(require_api_key)],
    responses=check_responses(load_preset(DEFAULT_COMMISSIONS_PRESET)),
)
async def commissions(
    request: CommissionsRequest,
    client: Annotated[ModelClient | None, Depends(model_client)],
) -> CommissionsResponse:
    """Find the claims a summary makes that its source doesn't support, and
    whether to flag it.

    Runs a validated commission check (often called hallucination detection),
    chosen with `preset`. `commission_quick`, the default, is one Claude Haiku 5.5
    call that costs about $0.002 for a typical clinical note.
    `commission_reverse_lean` lists the summary's claims and checks each against
    the source with Claude Opus 5.5, for about $0.28 and 20 seconds a note; it
    caught more commissions when measured, but isn't confirmed to its
    pre-registered standard. Each preset flags at its own threshold, and both
    answer in the same shape. A claim is a commission if the source contradicts
    it or never mentions it. Answers 200 for every finished check, flagged or
    not."""
    spec = load_preset(request.preset)
    started = time.perf_counter()
    with check_failures():
        result = await evaluate(
            request.source, request.summary, spec=spec, client=client
        )
    response = _response(result)
    logger.info(
        "checked a summary: %s · %d commissions · %s",
        result_line(result.run.output(ALARM_STEP, AlarmOutput)),
        len(response.findings),
        cost_line(result, time.perf_counter() - started),
    )
    return response


def _response(result: EvaluationResult) -> CommissionsResponse:
    # The commissions still flagged after refute; quick switches refute off.
    found = result.run.output("commissions_refute", CommissionsRefuteOutput)
    alarm = result.run.output(ALARM_STEP, AlarmOutput)
    return CommissionsResponse(
        evaluation_type="commissions",
        flagged=alarm.flagged,
        score=alarm.rescaled_score,
        findings=[commission_finding(c) for c in ranked(found.commissions)],
        counts=None,
        metadata=CommissionsMetadata(
            pipeline=result.pipeline,
            alarm=alarm_of(alarm, CommissionsAlarm),
            usage=usage_of(result),
        ),
    )
