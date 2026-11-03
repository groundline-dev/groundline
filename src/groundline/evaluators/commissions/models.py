"""The commission check's data: the claims the source doesn't support, and each
step's output."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from groundline.evaluators.models import Severity

SupportVerdict = Literal["unsupported", "contradicted"]
SUPPORT_VERDICTS: dict[str, SupportVerdict] = {
    "unsupported": "unsupported",
    "contradicted": "contradicted",
}
"""The two ways the source can fail to support a claim. A claim the source does
support isn't listed at all."""


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Issue(_Frozen):
    """A claim in the summary that the source doesn't support: a commission."""

    id: str
    quote: str
    """The words in the summary that make the claim, word for word."""
    verdict: SupportVerdict
    confidence: float
    """From 0 to 1: how sure the check is that the source doesn't support it."""
    severity: Severity
    """How much it would matter if the claim were wrong."""
    evidence: str | None = None
    """For a contradicted claim, the passage of the source that contradicts it,
    when the model quoted one that really is in the source."""


class ClearedIssue(_Frozen):
    """A flagged claim that refute found support for."""

    issue: Issue
    evidence: str
    """The passage of the source that supports the claim."""


# --- step outputs -----------------------------------------------------------


class CommissionsCheckOutput(_Frozen):
    issues: list[Issue]
    """Every unsupported or contradicted claim, in the order the model listed
    them."""


class CommissionsRefuteOutput(_Frozen):
    issues: list[Issue]
    """The check's issues still flagged after refute; refute can only clear one."""
    cleared: list[ClearedIssue]
