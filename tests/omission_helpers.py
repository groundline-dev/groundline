"""Run omission stages offline, with the real clinical prompts and scripted replies."""

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
from groundline.evaluators.omissions import register_omissions
from groundline.profiles import load_builtin_profile
from tests.fakes import FakeClient

CLINICAL = load_builtin_profile("clinical")

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


def step_of(prompt: str) -> str:
    """Which step a clinical prompt belongs to, from its section tags."""
    if "<flagged_facts>" in prompt:
        return "refute"
    if "<rubric>" in prompt:
        return "rank"
    if "<note>" in prompt:
        return "check"
    return "extract"


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
    register_omissions(registry)
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
