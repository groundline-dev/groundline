"""The commission check's data: the commissions it finds, and each step's output."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from groundline.evaluators.models import SEVERITIES, Severity

SupportVerdict = Literal["unsupported", "contradicted"]
SUPPORT_VERDICTS: dict[str, SupportVerdict] = {
    "unsupported": "unsupported",
    "contradicted": "contradicted",
}
"""The two ways the source can fail to support a claim. A claim the source does
support isn't listed at all."""


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Commission(_Frozen):
    """A claim in the summary that the source doesn't support."""

    id: str
    quote: str
    """The words in the summary that make the claim, word for word."""
    support_verdict: SupportVerdict
    unsupported_confidence: float
    """From 0 to 1: how sure the check is that the source doesn't support it."""
    severity: Severity
    """How much it would matter if the claim were wrong."""
    evidence: str | None = None
    """For a contradicted claim, the passage of the source the model quoted as
    contradicting it, if it quoted one."""
    evidence_found: bool | None = None
    """Whether that quote really is in the source word for word. None when no
    quote was given."""


class ClearedCommission(_Frozen):
    """A flagged claim that refute found support for."""

    commission: Commission
    evidence: str
    """The passage of the source that supports the claim."""


# --- step outputs -----------------------------------------------------------


class CommissionsCheckOutput(_Frozen):
    commissions: list[Commission]
    """Every claim the source doesn't support, in the order the model listed
    them."""


class CommissionsRefuteOutput(_Frozen):
    commissions: list[Commission]
    """The check's commissions still flagged after refute; refute can only clear
    one."""
    cleared: list[ClearedCommission]


def ranked(commissions: list[Commission]) -> list[Commission]:
    """Most important first: by severity, then the highest confidence. The API and
    the report both list commissions this way."""
    order = list(SEVERITIES)
    return sorted(
        commissions,
        key=lambda c: (order.index(c.severity), -c.unsupported_confidence),
    )
