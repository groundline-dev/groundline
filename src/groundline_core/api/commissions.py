"""`POST /v1/commissions`: run a validated commission preset on one summary: the
quick one unless the caller chooses the lean one.

The request and response are models of their own, not the pipeline's, so the
API's contract doesn't change when the internals do. Every finished check
answers 200, flagged or not (ADR 0008). No response or log line ever holds text
from the source or summary; see `groundline_core.api.common`.
"""

import logging
import time
from typing import Annotated, Literal, get_args

from fastapi import APIRouter, Depends
from pydantic import ConfigDict, Field

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


def _preset_choices() -> str:
    """What each choosable preset runs and how it was measured, from the presets'
    own descriptions, so the docs can't drift from them."""
    lines = ["Which validated preset to run."]
    for name in get_args(CommissionPreset):
        default = " (the default)" if name == DEFAULT_COMMISSIONS_PRESET else ""
        lines.append(f"`{name}`{default}: {load_preset(name).description}")
    return "\n\n".join(lines)


class CommissionsRequest(CheckRequest):
    preset: CommissionPreset = Field(
        default=DEFAULT_COMMISSIONS_PRESET, description=_preset_choices()
    )


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
    chosen with `preset`: `commission_quick` by default, or
    `commission_reverse_lean`, which caught more commissions when measured but
    costs far more and isn't confirmed to its pre-registered standard. The
    `preset` field says what each runs, how it was measured and what it costs.
    Each preset flags at its own threshold, and both answer in the same shape. A
    claim is a commission if the source contradicts it or never mentions it.
    Answers 200 for every finished check, flagged or not."""
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
