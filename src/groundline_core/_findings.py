"""Turning a check's run into what its result reports: the findings, and the
fact counts.

A finding's explanation is a fixed sentence for its type and verdict. It never
holds text from the source or summary, so it is safe to log and to show anywhere;
the details are in the finding's other fields.

The docs say when each evidence field is set, so the builders enforce it: the
model is asked to leave an evidence field empty when it doesn't apply, but a
reply that strays from that is not passed on.
"""

from groundline_core._core import PipelineSpec, RunResult, SpecError
from groundline_core._evaluators.commissions.models import (
    Commission,
    CommissionsRefuteOutput,
    ranked,
)
from groundline_core._evaluators.models import ALARM_STEP
from groundline_core._evaluators.omissions.models import (
    MissingFact,
    RankedFact,
    RankOutput,
    ScoreOutput,
)
from groundline_core._result import Counts, EvaluationType, Finding

CHECK_STEPS: dict[EvaluationType, tuple[str, ...]] = {
    "omissions": ("score", "rank"),
    "commissions": ("commissions_refute",),
}
"""The steps each check's findings are read from. Every check also needs the
alarm step, which decides the flag."""

CHECK_NAMES: dict[EvaluationType, str] = {
    "omissions": "omission check",
    "commissions": "commission check",
}
"""What each check is called in messages."""

EXPLANATIONS = {
    ("omission", "absent"): "The summary does not record this fact.",
    ("omission", "partial"): "The summary only partly records this fact.",
    ("commission", "unsupported"): "The source does not mention this claim.",
    ("commission", "contradicted"): "The source says something different.",
}


def omission_finding(missing: MissingFact, fact: RankedFact) -> Finding:
    """A fact the summary leaves out or only partly records. `fact` is the same
    fact as the extract and rank steps made it, which holds its source passage.
    Only a partial fact has summary evidence: for an absent one there is nothing
    in the summary to point at."""
    if missing.verdict == "present":
        raise ValueError("a fact the summary records is not a finding")
    return Finding(
        type="omission",
        severity=missing.severity,
        verdict=missing.verdict,
        confidence=missing.score,
        statement=missing.fact,
        source_evidence=fact.evidence,
        summary_evidence=missing.quote if missing.verdict == "partial" else None,
        evidence_found=fact.evidence_found,
        explanation=EXPLANATIONS["omission", missing.verdict],
    )


def commission_finding(commission: Commission) -> Finding:
    """A claim in the summary that the source doesn't support. Only a contradicted
    claim has source evidence: for an unsupported one the source says nothing."""
    contradicted = commission.support_verdict == "contradicted"
    return Finding(
        type="commission",
        severity=commission.severity,
        verdict=commission.support_verdict,
        confidence=commission.unsupported_confidence,
        statement=commission.quote,
        source_evidence=commission.evidence if contradicted else None,
        summary_evidence=None,
        evidence_found=commission.evidence_found if contradicted else None,
        explanation=EXPLANATIONS["commission", commission.support_verdict],
    )


def evaluation_type_of(spec: PipelineSpec) -> EvaluationType:
    """Which check a spec carries out, from its steps. A spec is an error when its
    result would have no flag, score or findings to report (no alarm, or not every
    step one check's findings are read from), or when it has both checks' steps,
    since a result reports one check."""
    steps = {stage.step for stage in spec.stages}
    kinds: list[EvaluationType] = [
        kind for kind, needed in CHECK_STEPS.items() if steps.issuperset(needed)
    ]
    missing: list[str] = []
    if ALARM_STEP not in steps:
        missing.append(f"the {ALARM_STEP} step")
    started: dict[EvaluationType, list[str]] = {
        kind: [step for step in needed if step not in steps]
        for kind, needed in CHECK_STEPS.items()
        if steps.intersection(needed)
    }
    if not kinds and len(started) == 1:
        kind, absent = next(iter(started.items()))
        plural = "s" if len(absent) > 1 else ""
        missing.append(f"the {CHECK_NAMES[kind]}'s {' and '.join(absent)} step{plural}")
    elif not kinds:
        missing.append(
            "the steps of a check: score and rank (omissions), "
            "or commissions_refute (commissions)"
        )
    if missing:
        raise SpecError(
            [
                f"spec '{spec.name}' doesn't make a full check, so it has no result "
                f"to report: it needs {' and '.join(missing)}"
            ]
        )
    if len(kinds) > 1:
        raise SpecError(
            [
                f"spec '{spec.name}' has the steps of both checks, but a result "
                "reports one check: run omissions and commissions separately"
            ]
        )
    return kinds[0]


def findings_and_counts(
    run: RunResult, check: EvaluationType
) -> tuple[list[Finding], Counts | None]:
    """What the check found, most important first, and for the omission check how
    many of the source's facts the summary records."""
    if check == "omissions":
        scored = run.output("score", ScoreOutput)
        facts = {f.id: f for f in run.output("rank", RankOutput).facts}
        findings = [omission_finding(m, facts[m.id]) for m in scored.missing_facts]
        counts = Counts(
            total=scored.total,
            present=scored.present,
            partial=scored.partial,
            absent=scored.absent,
        )
        return findings, counts
    # The commissions still flagged after refute; quick switches refute off.
    found = run.output("commissions_refute", CommissionsRefuteOutput)
    return [commission_finding(c) for c in ranked(found.commissions)], None
