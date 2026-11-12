"""Turning what a check found into the API's findings.

A finding's explanation is a fixed sentence for its type and verdict. It never
holds text from the source or summary, so it is safe to log and to show anywhere;
the details are in the finding's other fields.
"""

from groundline.api.common import Finding
from groundline.evaluators.commissions.models import Commission
from groundline.evaluators.omissions.models import MissingFact, RankedFact

EXPLANATIONS = {
    ("omission", "absent"): "The summary does not record this fact.",
    ("omission", "partial"): "The summary only partly records this fact.",
    ("commission", "unsupported"): "The source does not mention this claim.",
    ("commission", "contradicted"): "The source says something different.",
}


def omission_finding(missing: MissingFact, fact: RankedFact) -> Finding:
    """A fact the summary leaves out or only partly records. `fact` is the same
    fact as the extract and rank steps made it, which holds its source passage."""
    if missing.verdict == "present":
        raise ValueError("a fact the summary records is not a finding")
    return Finding(
        type="omission",
        severity=missing.severity,
        verdict=missing.verdict,
        confidence=missing.score,
        statement=missing.fact,
        source_evidence=fact.evidence,
        summary_evidence=missing.quote,
        evidence_found=fact.evidence_found,
        explanation=EXPLANATIONS["omission", missing.verdict],
    )


def commission_finding(commission: Commission) -> Finding:
    """A claim in the summary that the source doesn't support."""
    return Finding(
        type="commission",
        severity=commission.severity,
        verdict=commission.support_verdict,
        confidence=commission.unsupported_confidence,
        statement=commission.quote,
        source_evidence=commission.evidence,
        summary_evidence=None,
        evidence_found=commission.evidence_found,
        explanation=EXPLANATIONS["commission", commission.support_verdict],
    )
