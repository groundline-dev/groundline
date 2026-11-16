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
    from ..models.counts import Counts
    from ..models.finding import Finding
    from ..models.omissions_metadata import OmissionsMetadata


T = TypeVar("T", bound="OmissionsResponse")


@_attrs_define
class OmissionsResponse:
    """
    Attributes:
        evaluation_type (Literal['omissions']): Which check made this response: `omissions` or `commissions`.
        flagged (bool): Whether the alarm score reached the threshold.
        score (float): From 0 to 1. Higher is better. A score below 0.5 means the summary is
            flagged, and a summary passes at 0.5 or above; the flag is exactly the
            alarm's. The score is the alarm score turned around and rescaled around the
            alarm's threshold. It shows whether a critical omission was found, not how
            good the summary is overall. A source with no facts scores 1: nothing
            critical was found, and `counts.total` is 0.
        findings (list[Finding]): The facts not fully recorded, most important first.
        counts (Counts | None): How many of the source's facts the summary records, fully, partly or not at
            all. Null for a check that doesn't count facts, such as the commission check.
        metadata (OmissionsMetadata):
    """

    evaluation_type: Literal["omissions"]
    flagged: bool
    score: float
    findings: list[Finding]
    counts: Counts | None
    metadata: OmissionsMetadata

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
        from ..models.counts import Counts  # noqa: PLC0415
        from ..models.finding import Finding  # noqa: PLC0415
        from ..models.omissions_metadata import OmissionsMetadata  # noqa: PLC0415

        d = dict(src_dict)
        evaluation_type = cast(Literal["omissions"], d.pop("evaluation_type"))
        if evaluation_type != "omissions":
            raise ValueError(
                f"evaluation_type must match const 'omissions', got '{evaluation_type}'"
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

        metadata = OmissionsMetadata.from_dict(d.pop("metadata"))

        omissions_response = cls(
            evaluation_type=evaluation_type,
            flagged=flagged,
            score=score,
            findings=findings,
            counts=counts,
            metadata=metadata,
        )

        return omissions_response
