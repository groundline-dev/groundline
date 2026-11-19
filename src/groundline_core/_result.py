"""What `evaluate` returns: a check's result and the types it is made of.

These types are public (listed in `groundline_core.__all__`), and the API answers
with the same ones, so the library and the API report a check the same way. Kept
apart from `evaluate` so that the evaluators' reports can use them without
importing the module that imports the evaluators.

The parts the API shares (`Finding`, `Counts`, `Alarm` and `Usage`) say "null"
where the API's JSON has `null`; in Python that is `None`.
"""

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict

from groundline_core._core import RunResult
from groundline_core._evaluators.models import Severity

EvaluationType = Literal["omissions", "commissions"]


class ResultModel(BaseModel):
    """The base for the result's parts: immutable, strict about their fields, and
    described by their attribute docstrings (which the API's docs show too)."""

    model_config = ConfigDict(
        frozen=True, extra="forbid", use_attribute_docstrings=True
    )


class Alarm(ResultModel):
    """The flag decision: the alarm score, and the threshold it was compared with.
    The summary is flagged when the alarm score reaches the threshold."""

    score: float
    """The highest confidence among the summary's critical problems (for the
    omission check, with partly recorded facts at half weight); 0 if there are
    none."""
    threshold: float
    """The alarm score at or above which the summary is flagged."""
    level: str
    """The threshold level used, such as strict."""


class Usage(ResultModel):
    """The model calls a check made, and what they cost."""

    calls: int
    """Model calls made, including any answered from a cache."""
    input_tokens: int
    """Tokens sent to the model, by calls not answered from a cache."""
    output_tokens: int
    """Tokens the model wrote, by calls not answered from a cache."""
    cost_usd: float
    """What the calls not answered from a cache cost, in US dollars, at the prices
    groundline knows. A model with no known price adds nothing."""


class Counts(ResultModel):
    """How many of the source's facts the summary records."""

    total: int
    """Facts found in the source."""
    present: int
    """Facts the summary fully records."""
    partial: int
    """Facts the summary only partly records."""
    absent: int
    """Facts the summary leaves out."""


class Finding(ResultModel):
    """One omission or one commission, as a check reports it. The same fields serve
    both kinds, so a check that finds both can return them in one list."""

    type: Literal["omission", "commission"]
    """`omission`: a fact from the source that the summary leaves out or only
    partly records. `commission`: a claim in the summary that the source doesn't
    support."""
    severity: Severity | None
    """How much it would matter if the fact were left out, or the claim were wrong.
    Null when the check didn't grade it."""
    verdict: Literal["partial", "absent", "unsupported", "contradicted"]
    """For an omission, `partial` (the summary records only part of the fact) or
    `absent`. For a commission, `unsupported` (the source doesn't mention the
    claim) or `contradicted` (the source says otherwise)."""
    confidence: float | None
    """From 0 to 1: how sure the check is. For an omission, that the fact is
    missing; for a commission, that the source doesn't support the claim. Null
    when the check gave none."""
    statement: str
    """The fact from the source that the summary leaves out, or the words in the
    summary that make the claim, word for word."""
    source_evidence: str | None
    """The passage of the source behind the finding. For an omission, the passage
    the fact came from. For a contradicted commission, the passage the model quoted
    as contradicting the claim. Null for an unsupported commission, or if the model
    quoted none."""
    summary_evidence: str | None
    """For a partial omission, the words in the summary that partly record the
    fact. Null otherwise."""
    evidence_found: bool | None
    """Whether `source_evidence` appears in the source word for word, ignoring
    case, punctuation and spacing. True means verified. False only means it
    couldn't be matched, which a reworded quote or markup in the source, such as a
    scribe's tags, can cause; it doesn't mean the passage was invented. Null when
    there is no evidence."""
    explanation: str
    """What kind of problem this is, in a fixed sentence for each type and verdict.
    It is the same for every finding of that kind, so use the other fields for the
    details."""


@dataclass(frozen=True)
class EvaluationResult:
    """One check of one summary: whether it is flagged, its score, what the check
    found, and what it cost."""

    pipeline: str
    """The name of the pipeline that ran, such as `omission_all_opus`."""
    profile: str | None
    """The name of the domain profile whose wording the pipeline used, such as
    `clinical`; None if it used none."""
    validated_preset: str | None
    """The built-in preset this run matched exactly (ignoring selectable
    settings), or None if the pipeline or profile was changed."""
    evaluation_type: EvaluationType
    """Which check ran: `omissions` or `commissions`."""
    flagged: bool
    """Whether the alarm score reached the threshold."""
    score: float
    """From 0 to 1. Higher is better. A score below 0.5 means the summary is
    flagged, and a summary passes at 0.5 or above; the flag is exactly the
    alarm's."""
    findings: list[Finding]
    """What the check found, most important first."""
    counts: Counts | None
    """How many of the source's facts the summary records, fully, partly or not at
    all. None for a check that doesn't count facts, such as the commission check."""
    alarm: Alarm
    """The alarm score, the threshold it was compared with, and the threshold
    level."""
    usage: Usage
    """The model calls the check made, and what they cost."""
    disclaimer: str
    """What a person relying on this result should know: it helps them review
    the summary, and a summary that isn't flagged can still leave out or misstate
    important facts. It includes the sentence the domain profile adds, if any.
    Show it with the result."""
    run: RunResult
    """Every stage's output, status, model calls, usage and cost. For inspection
    only: its shape follows the pipeline's internal stages and may change without
    notice."""
    warnings: list[str] = field(default_factory=list[str])
    """Things to know about this run, such as alarm thresholds copied from a
    preset the pipeline no longer matches."""
