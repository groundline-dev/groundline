"""Run commission stages offline: the real clinical prompts, scripted replies."""

import json
from typing import Any

from groundline_core.core import FileCache, RunResult
from groundline_core.evaluators.commissions import register_commissions
from tests.fakes import FakeClient, ScriptedReplies, run_stages
from tests.omission_helpers import SOURCE

CHECK = {"step": "commissions_check", "implementation": "commissions_check.llm_quick"}
REFUTE = {"step": "commissions_refute", "implementation": "commissions_refute.llm"}
ALARM = {"step": "alarm", "implementation": "alarm.critical_unsupported_max"}
ALL_STAGES = [CHECK, {**REFUTE, "enabled": False}, ALARM]
"""The quick preset's stages: refute is switched off."""

CLAIMS = {"step": "commissions_claims", "implementation": "commissions_claims.llm"}
SUPPORT = {
    "step": "commissions_check",
    "implementation": "commissions_check.llm_support",
}
REFUTE_FLAGGED = {
    "step": "commissions_refute",
    "implementation": "commissions_refute.llm_flagged",
}


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
    """Which step a clinical prompt belongs to, from its section tags. The quick
    check and the lean support check both fill the commissions_check step."""
    if "<flagged_claims>" in prompt:
        return "commissions_refute"
    if "<transcript>" not in prompt:
        return "commissions_claims"  # the claims prompt sees only the note
    return "commissions_check"


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


# --- the lean preset's replies, in its prompts' own words -----------------------

SKIING = "She was skiing"
SKIING_CLAIM = "The patient was skiing"
EXAM_CLAIM = "Neurovascular examination was intact"
DOSE_CLAIM = "Motrin 800 mg every 6 hours for a week"


def claims_reply(*rows: tuple[str, str]) -> str:
    """The claims step's reply: (claim, quote) pairs, numbered c1, c2, ..."""
    return json.dumps({"claims": [{"claim": c, "quote": q} for c, q in rows]})


CLAIMS_REPLY = claims_reply(
    (SKIING_CLAIM, SKIING), (EXAM_CLAIM, EXAM), (DOSE_CLAIM, DOSE)
)


def verdict_entry(
    claim_id: str,
    label: str = "supported",
    confidence: Any = 5,
    importance: str = "supporting",
    evidence: str | None = None,
) -> dict[str, Any]:
    """One claim's verdict, as an entry of the lean support check's reply."""
    return {
        "id": claim_id,
        "label": label,
        "evidence": evidence,
        "not_supported_confidence": confidence,
        "importance": importance,
    }


def verdicts_reply(*entries: dict[str, Any]) -> str:
    return json.dumps({"claims": list(entries)})


SKIING_SUPPORTED = verdict_entry("c1", "supported", 5, "peripheral")
EXAM_UNSUPPORTED = verdict_entry("c2", "unsupported", 70, "supporting")
DOSE_CONTRADICTED = verdict_entry("c3", "contradicted", 95, "critical", DOSE_SOURCE)
VERDICTS_REPLY = verdicts_reply(SKIING_SUPPORTED, EXAM_UNSUPPORTED, DOSE_CONTRADICTED)

KEEP_LEAN_FLAGS = support_reply(("c2", False, None), ("c3", False, None))
"""Refute's reply when it clears neither of the lean replies' flagged claims."""


def lean_script(
    verdicts: str = VERDICTS_REPLY, refute: str | list[str] = KEEP_LEAN_FLAGS
) -> Script:
    """Scripted replies for a whole lean run on SUMMARY."""
    return Script(
        commissions_claims=CLAIMS_REPLY,
        commissions_check=verdicts,
        commissions_refute=refute,
    )
