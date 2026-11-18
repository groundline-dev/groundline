from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.finding_severity_type_0 import FindingSeverityType0
from ..models.finding_type import FindingType
from ..models.finding_verdict import FindingVerdict

T = TypeVar("T", bound="Finding")


@_attrs_define
class Finding:
    """One omission or one commission, as a check reports it. The same fields serve
    both kinds, so a check that finds both can return them in one list.

        Attributes:
            type_ (FindingType): `omission`: a fact from the source that the summary leaves out or only
                partly records. `commission`: a claim in the summary that the source doesn't
                support.
            severity (FindingSeverityType0 | None): How much it would matter if the fact were left out, or the claim were
                wrong.
                Null when the check didn't grade it.
            verdict (FindingVerdict): For an omission, `partial` (the summary records only part of the fact) or
                `absent`. For a commission, `unsupported` (the source doesn't mention the
                claim) or `contradicted` (the source says otherwise).
            confidence (float | None): From 0 to 1: how sure the check is. For an omission, that the fact is
                missing; for a commission, that the source doesn't support the claim. Null
                when the check gave none.
            statement (str): The fact from the source that the summary leaves out, or the words in the
                summary that make the claim, word for word.
            source_evidence (None | str): The passage of the source behind the finding. For an omission, the passage
                the fact came from. For a contradicted commission, the passage the model quoted
                as contradicting the claim. Null for an unsupported commission, or if the model
                quoted none.
            summary_evidence (None | str): For a partial omission, the words in the summary that partly record the
                fact. Null otherwise.
            evidence_found (bool | None): Whether `source_evidence` appears in the source word for word, ignoring
                case, punctuation and spacing. True means verified. False only means it
                couldn't be matched, which a reworded quote or markup in the source, such as a
                scribe's tags, can cause; it doesn't mean the passage was invented. Null when
                there is no evidence.
            explanation (str): What kind of problem this is, in a fixed sentence for each type and verdict.
                It is the same for every finding of that kind, so use the other fields for the
                details.
    """

    type_: FindingType
    severity: FindingSeverityType0 | None
    verdict: FindingVerdict
    confidence: float | None
    statement: str
    source_evidence: None | str
    summary_evidence: None | str
    evidence_found: bool | None
    explanation: str

    def to_dict(self) -> dict[str, Any]:
        type_ = self.type_.value

        severity: None | str
        if isinstance(self.severity, FindingSeverityType0):
            severity = self.severity.value
        else:
            severity = self.severity

        verdict = self.verdict.value

        confidence: float | None
        confidence = self.confidence

        statement = self.statement

        source_evidence: None | str
        source_evidence = self.source_evidence

        summary_evidence: None | str
        summary_evidence = self.summary_evidence

        evidence_found: bool | None
        evidence_found = self.evidence_found

        explanation = self.explanation

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "type": type_,
                "severity": severity,
                "verdict": verdict,
                "confidence": confidence,
                "statement": statement,
                "source_evidence": source_evidence,
                "summary_evidence": summary_evidence,
                "evidence_found": evidence_found,
                "explanation": explanation,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        type_ = FindingType(d.pop("type"))

        def _parse_severity(data: object) -> FindingSeverityType0 | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                severity_type_0 = FindingSeverityType0(data)

                return severity_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(FindingSeverityType0 | None, data)

        severity = _parse_severity(d.pop("severity"))

        verdict = FindingVerdict(d.pop("verdict"))

        def _parse_confidence(data: object) -> float | None:
            if data is None:
                return data
            return cast(float | None, data)

        confidence = _parse_confidence(d.pop("confidence"))

        statement = d.pop("statement")

        def _parse_source_evidence(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        source_evidence = _parse_source_evidence(d.pop("source_evidence"))

        def _parse_summary_evidence(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        summary_evidence = _parse_summary_evidence(d.pop("summary_evidence"))

        def _parse_evidence_found(data: object) -> bool | None:
            if data is None:
                return data
            return cast(bool | None, data)

        evidence_found = _parse_evidence_found(d.pop("evidence_found"))

        explanation = d.pop("explanation")

        finding = cls(
            type_=type_,
            severity=severity,
            verdict=verdict,
            confidence=confidence,
            statement=statement,
            source_evidence=source_evidence,
            summary_evidence=summary_evidence,
            evidence_found=evidence_found,
            explanation=explanation,
        )

        return finding
