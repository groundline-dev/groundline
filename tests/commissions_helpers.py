"""Run commission stages offline: the real clinical prompts, scripted replies."""

import asyncio
import json
from collections.abc import Sequence
from typing import Any

from groundline.core import (
    FileCache,
    Inputs,
    PipelineSpec,
    Registry,
    RunResult,
    StageSpec,
    build_pipeline,
    run_pipeline,
)
from groundline.evaluators.commissions import register_commissions
from groundline.profiles import load_builtin_profile
from tests.fakes import FakeClient
from tests.omission_helpers import SOURCE

CLINICAL = load_builtin_profile("clinical")

CHECK = {"step": "commissions_check", "implementation": "commissions_check.llm_quick"}
REFUTE = {"step": "commissions_refute", "implementation": "commissions_refute.llm"}
ALARM = {"step": "alarm", "implementation": "alarm.critical_unsupported_max"}
ALL_STAGES = [CHECK, {**REFUTE, "enabled": False}, ALARM]
"""The quick preset's stages: refute is switched off."""

# SOURCE says Motrin 600 mg and never mentions an examination.
SUMMARY = (
    "HPI: She was skiing and hyperextended her right index finger.\n"
    "Exam: neurovascular examination intact.\n"
    "PLAN: Motrin 800 mg every 6 hours for a week."
)

DOSE = "Motrin 800 mg every 6 hours for a week"
EXAM = "neurovascular examination intact"
DOSE_SOURCE = "take motrin 600 mg every 6 hours for a week"


def step_of(prompt: str) -> str:
    """Which step a clinical prompt belongs to, from its section tags."""
    return "commissions_refute" if "<flagged_claims>" in prompt else "commissions_check"


class Script:
    """Scripted replies per step, given in order. Records each step's prompts."""

    def __init__(self, **replies: str | Sequence[str]) -> None:
        self.replies = {
            step: [r] if isinstance(r, str) else list(r) for step, r in replies.items()
        }
        self.prompts: dict[str, list[str]] = {}

    def __call__(self, prompt: str) -> str:
        step = step_of(prompt)
        self.prompts.setdefault(step, []).append(prompt)
        queue = self.replies.get(step)
        assert queue, f"no scripted reply left for {step}"
        return queue.pop(0) if len(queue) > 1 else queue[0]


def run(
    stages: list[dict[str, Any]],
    script: Script,
    *,
    source: str = SOURCE,
    summary: str = SUMMARY,
    cache: FileCache | None = None,
) -> tuple[RunResult, FakeClient]:
    registry = Registry()
    register_commissions(registry)
    spec = PipelineSpec(
        name="test", profile="clinical", stages=[StageSpec(**s) for s in stages]
    )
    pipeline = build_pipeline(spec, registry, profile=CLINICAL)
    client = FakeClient(script)
    result = asyncio.run(
        run_pipeline(
            pipeline,
            Inputs(source=source, summary=summary),
            client=client,
            cache=cache,
        )
    )
    return result, client


# --- standard replies for the source and summary above --------------------------


def issue(
    quote: str,
    label: str = "unsupported",
    confidence: Any = 70,
    importance: str = "supporting",
    evidence: str | None = None,
) -> dict[str, Any]:
    return {
        "quote": quote,
        "label": label,
        "confidence": confidence,
        "importance": importance,
        "evidence": evidence,
    }


def issues_reply(*issues: dict[str, Any]) -> str:
    return json.dumps({"issues": list(issues)})


DOSE_ISSUE = issue(DOSE, "contradicted", 95, "critical", DOSE_SOURCE)
EXAM_ISSUE = issue(EXAM, "unsupported", 70, "supporting")
ISSUES = issues_reply(DOSE_ISSUE, EXAM_ISSUE)


def support_reply(*rows: tuple[str, bool, str | None]) -> str:
    return json.dumps(
        {"claims": [{"id": i, "supported": s, "evidence": e} for i, s, e in rows]}
    )
