"""Score: turn the final verdicts into counts and ratios. No model calls.

The headline ratio is absent facts over all facts; partials are counted
separately. The weighted ratio weighs each fact by its severity (facts without a
severity, when ranking is off, weigh 1).
"""

from typing import ClassVar

from groundline.core import Implementation, StageContext
from groundline.evaluators.omissions.models import (
    MissingFact,
    RankedFact,
    RankOutput,
    RefuteOutput,
    ScoreOutput,
    Severity,
    Verdict,
)


class ScoreBasic(Implementation):
    name: ClassVar[str] = "score.basic"
    step: ClassVar[str] = "score"
    reads: ClassVar[tuple[str, ...]] = ("rank", "refute")

    weighted: bool = True
    """Whether to also report the severity-weighted missing ratio."""
    weights: dict[Severity, int] = {"critical": 3, "supporting": 2, "peripheral": 1}
    """How much a fact of each severity counts in the weighted ratio."""

    async def run(self, ctx: StageContext) -> ScoreOutput:
        return score_verdicts(
            ctx.output("rank", RankOutput).facts,
            ctx.output("refute", RefuteOutput).verdicts,
            self.weights if self.weighted else None,
        )


def score_verdicts(
    facts: list[RankedFact],
    verdicts: list[Verdict],
    weights: dict[Severity, int] | None,
) -> ScoreOutput:
    by_id = {v.fact_id: v for v in verdicts}
    unchecked = [f.id for f in facts if f.id not in by_id]
    if unchecked:
        raise ValueError(f"no verdict for {', '.join(unchecked)}")

    labels = [by_id[f.id].label for f in facts]
    total = len(facts)
    weighted_ratio = None
    if weights is not None:
        weight = {f.id: weights[f.severity] if f.severity else 1 for f in facts}
        absent_weight = sum(
            weight[f.id] for f in facts if by_id[f.id].label == "absent"
        )
        weighted_ratio = _ratio(absent_weight, sum(weight.values()))

    return ScoreOutput(
        total=total,
        present=labels.count("present"),
        partial=labels.count("partial"),
        absent=labels.count("absent"),
        missing_ratio=_ratio(labels.count("absent"), total),
        weighted_missing_ratio=weighted_ratio,
        missing_facts=[
            MissingFact(
                id=f.id,
                fact=f.fact,
                severity=f.severity,
                rank=f.rank,
                verdict=by_id[f.id].label,
                quote=by_id[f.id].quote,
                score=by_id[f.id].score,
            )
            for f in sorted(facts, key=lambda f: f.rank)
            if by_id[f.id].label != "present"
        ],
        quote_not_found=[f.id for f in facts if by_id[f.id].quote_found is False],
        evidence_not_found=[f.id for f in facts if f.evidence_found is False],
    )


def _ratio(part: float, whole: float) -> float:
    return round(part / whole, 4) if whole else 0.0
