from __future__ import annotations

from collections.abc import Mapping
from typing import (
    TYPE_CHECKING,
    Any,
    Literal,
    TypeVar,
    cast,
)

from attrs import define as _attrs_define

if TYPE_CHECKING:
    from ..models.commissions_metadata import CommissionsMetadata
    from ..models.counts import Counts
    from ..models.finding import Finding


T = TypeVar("T", bound="CommissionsResponse")


@_attrs_define
class CommissionsResponse:
    """
    Attributes:
        evaluation_type (Literal['commissions']): Which check made this response: `omissions` or `commissions`.
        flagged (bool): Whether the alarm score reached the threshold.
        score (float): From 0 to 1. Higher is better. A score below 0.5 means the summary is
            flagged, and a summary passes at 0.5 or above; the flag is exactly the
            alarm's. The score is the alarm score turned around and rescaled around the
            alarm's threshold. A flag means a claim wasn't found in the source, for
            a reviewer to check; the score doesn't say how good the summary is overall.
            A summary with no claims the source fails to support scores 1.
        findings (list[Finding]): The claims in the summary that the source doesn't support, most important
            first.
        counts (Counts | None): How many of the source's facts the summary records, fully, partly or not at
            all. Null for a check that doesn't count facts, such as the commission check.
        metadata (CommissionsMetadata):
    """

    evaluation_type: Literal["commissions"]
    flagged: bool
    score: float
    findings: list[Finding]
    counts: Counts | None
    metadata: CommissionsMetadata

    def to_dict(self) -> dict[str, Any]:
        from ..models.counts import Counts  # noqa: PLC0415

        evaluation_type = self.evaluation_type

        flagged = self.flagged

        score = self.score

        findings = []
        for findings_item_data in self.findings:
            findings_item = findings_item_data.to_dict()
            findings.append(findings_item)

        counts: dict[str, Any] | None
        if isinstance(self.counts, Counts):
            counts = self.counts.to_dict()
        else:
            counts = self.counts

        metadata = self.metadata.to_dict()

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "evaluation_type": evaluation_type,
                "flagged": flagged,
                "score": score,
                "findings": findings,
                "counts": counts,
                "metadata": metadata,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.commissions_metadata import CommissionsMetadata  # noqa: PLC0415
        from ..models.counts import Counts  # noqa: PLC0415
        from ..models.finding import Finding  # noqa: PLC0415

        d = dict(src_dict)
        evaluation_type = cast(Literal["commissions"], d.pop("evaluation_type"))
        if evaluation_type != "commissions":
            raise ValueError(
                f"evaluation_type must match const 'commissions', got '{evaluation_type}'"
            )

        flagged = d.pop("flagged")

        score = d.pop("score")

        findings = []
        _findings = d.pop("findings")
        for findings_item_data in _findings:
            findings_item = Finding.from_dict(findings_item_data)

            findings.append(findings_item)

        def _parse_counts(data: object) -> Counts | None:
            if data is None:
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                counts_type_0 = Counts.from_dict(data)

                return counts_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(Counts | None, data)

        counts = _parse_counts(d.pop("counts"))

        metadata = CommissionsMetadata.from_dict(d.pop("metadata"))

        commissions_response = cls(
            evaluation_type=evaluation_type,
            flagged=flagged,
            score=score,
            findings=findings,
            counts=counts,
            metadata=metadata,
        )

        return commissions_response
