"""The omission check's data: facts, verdicts, and each step's output."""

from typing import Literal

from groundline.evaluators.models import Frozen, Severity

VerdictLabel = Literal["present", "partial", "absent"]
VERDICT_LABELS: dict[str, VerdictLabel] = {
    "present": "present",
    "partial": "partial",
    "absent": "absent",
}


class Fact(Frozen):
    """Something the source establishes that a complete summary should record."""

    id: str
    fact: str
    evidence: str
    """A quote from the source supporting the fact."""
    evidence_found: bool | None = None
    """Whether that quote really is in the source."""


class RankedFact(Fact):
    severity: Severity | None
    """None when ranking is switched off."""
    rank: int
    rationale: str = ""


class Verdict(Frozen):
    """How fully the summary records one fact."""

    fact_id: str
    label: VerdictLabel
    quote: str | None = None
    """The passage of the summary that records the fact (none if absent)."""
    quote_found: bool | None = None
    """Whether that quote really is in the summary."""
    score: float | None = None
    """Confidence from 0 to 1 that the summary does not fully record the fact."""


class RefuteEntry(Frozen):
    """What refute decided about one flagged fact."""

    id: str
    found: Literal["full", "partial", "no"]
    quote: str | None
    quote_found: bool
    from_label: VerdictLabel
    to_label: VerdictLabel
    flipped: bool


class MissingFact(Frozen):
    id: str
    fact: str
    severity: Severity | None
    rank: int
    verdict: VerdictLabel
    quote: str | None
    score: float | None


# --- step outputs -----------------------------------------------------------


class ExtractOutput(Frozen):
    facts: list[Fact]


class RankOutput(Frozen):
    facts: list[RankedFact]
    """In rank order: by severity, then the order the model listed them."""


class CheckOutput(Frozen):
    verdicts: list[Verdict]
    """In rank order."""


class RefuteOutput(Frozen):
    verdicts: list[Verdict]
    """The check's verdicts after refute; refute can only clear a flag."""
    log: list[RefuteEntry]


class ScoreOutput(Frozen):
    total: int
    present: int
    partial: int
    absent: int
    missing_ratio: float
    """Absent facts over all facts."""
    weighted_missing_ratio: float | None
    """Absent weight over total weight, by severity (None when off)."""
    missing_facts: list[MissingFact]
    """Facts not fully recorded, in rank order."""
    quote_not_found: list[str]
    """Facts whose verdict quote isn't in the summary."""
    evidence_not_found: list[str]
    """Facts whose evidence quote isn't in the source."""
