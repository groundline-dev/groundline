"""Run commission stages offline: the real clinical prompts, scripted replies."""

import json
from typing import Any

from groundline.core import FileCache, RunResult
from groundline.evaluators.commissions import register_commissions
from tests.fakes import FakeClient, ScriptedReplies, run_stages
from tests.omission_helpers import SOURCE

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


class Script(ScriptedReplies):
    """Scripted replies per step, given in order. Records each step's prompts."""

    def step_of(self, prompt: str) -> str:
        return step_of(prompt)


def run(
    stages: list[dict[str, Any]],
    script: Script,
    *,
    source: str = SOURCE,
    summary: str = SUMMARY,
    cache: FileCache | None = None,
) -> tuple[RunResult, FakeClient]:
    return run_stages(
        register_commissions,
        stages,
        script,
        source=source,
        summary=summary,
        cache=cache,
    )


# --- standard replies for the source and summary above --------------------------


# The helpers below build the model's reply, which uses the prompt's own words
# (issues, label, confidence, importance), not groundline's.


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
