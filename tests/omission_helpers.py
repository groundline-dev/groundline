"""Run omission stages offline, with the real clinical prompts and scripted replies."""

import json
from typing import Any

from groundline.core import FileCache, RunResult
from groundline.evaluators.omissions import register_omissions
from tests.fakes import FakeClient, ScriptedReplies, run_stages

EXTRACT = {"step": "extract", "implementation": "extract.llm"}
RANK = {"step": "rank", "implementation": "rank.llm"}
CHECK = {"step": "check", "implementation": "check.llm_batch"}
REFUTE = {"step": "refute", "implementation": "refute.llm"}
SCORE = {"step": "score", "implementation": "score.basic"}
ALARM = {"step": "alarm", "implementation": "alarm.critical_max"}
ALL_STAGES = [EXTRACT, RANK, CHECK, REFUTE, SCORE, ALARM]

SOURCE = (
    "[doctor] hi , ms. brooks . how are you ?\n"
    "[patient] well , i was skiing over the weekend and it bent back .\n"
    "[doctor] does it hurt when you move it ?\n"
    "[patient] yes , it hurts more when i move it .\n"
    "[doctor] take motrin 600 mg every 6 hours for a week ."
)
SUMMARY = (
    "HPI: She was skiing and hyperextended her right index finger.\n"
    "PLAN: Motrin 600 mg every 6 hours for a week."
)
CLEAR_SUMMARY = SUMMARY + "\nPain is worse with movement."


def step_of(prompt: str) -> str:
    """Which step a clinical prompt belongs to, from its section tags."""
    if "<flagged_facts>" in prompt:
        return "refute"
    if "<rubric>" in prompt:
        return "rank"
    if "<note>" in prompt:
        return "check"
    return "extract"


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
        register_omissions, stages, script, source=source, summary=summary, cache=cache
    )


# --- standard replies for the source and summary above --------------------------


def facts_reply(*facts: tuple[str, str]) -> str:
    return json.dumps(
        {
            "facts": [
                {"id": f"f{i}", "fact": fact, "evidence": evidence}
                for i, (fact, evidence) in enumerate(facts, 1)
            ]
        }
    )


EXTRACTED = facts_reply(
    ("Injured the finger skiing.", "i was skiing over the weekend"),
    ("Pain is worse with movement.", "it hurts more when i move it"),
    ("Motrin 600 mg every 6 hours for a week.", "motrin 600 mg every 6 hours"),
)


def rank_reply(*rows: tuple[str, str]) -> str:
    return json.dumps(
        {"facts": [{"id": i, "severity": s, "why": f"because {i}"} for i, s in rows]}
    )


RANKED = rank_reply(("f3", "critical"), ("f1", "supporting"), ("f2", "supporting"))


def check_reply(confidence: dict[str, Any] | None = None) -> str:
    c = {"f3": 5, "f1": 60, "f2": 95, **(confidence or {})}
    return json.dumps(
        {
            "facts": [
                {
                    "id": "f3",
                    "verdict": "present",
                    "quote": "Motrin 600 mg every 6 hours for a week",
                    "missing_confidence": c["f3"],
                },
                {
                    "id": "f1",
                    "verdict": "partial",
                    "quote": "She was skiing and hyperextended her right index finger",
                    "missing_confidence": c["f1"],
                },
                {
                    "id": "f2",
                    "verdict": "absent",
                    "quote": None,
                    "missing_confidence": c["f2"],
                },
            ]
        }
    )


def refute_reply(*rows: tuple[str, str, str | None]) -> str:
    return json.dumps(
        {"facts": [{"id": i, "found": found, "quote": q} for i, found, q in rows]}
    )


def not_flagged_script() -> Script:
    """A full run that doesn't flag the summary: the critical fact (f3) is present."""
    return Script(
        extract=EXTRACTED,
        rank=RANKED,
        check=check_reply(),
        refute=refute_reply(("f1", "no", None), ("f2", "no", None)),
    )


def flagged_script() -> Script:
    """A full run that flags the summary: the critical fact (f3) is absent, at 95."""
    check = json.loads(check_reply({"f3": 95}))
    check["facts"][0].update(verdict="absent", quote=None)
    return Script(
        extract=EXTRACTED,
        rank=RANKED,
        check=json.dumps(check),
        refute=refute_reply(("f3", "no", None), ("f1", "no", None), ("f2", "no", None)),
    )


def clear_script() -> Script:
    """Every fact is present in CLEAR_SUMMARY, so refute needs no model call."""
    checked = json.loads(check_reply())
    for row in checked["facts"]:
        row.update(verdict="present", missing_confidence=0)
        if row["id"] == "f2":
            row["quote"] = "Pain is worse with movement."
    script = not_flagged_script()
    script.replies["check"] = [json.dumps(checked)]
    return script
