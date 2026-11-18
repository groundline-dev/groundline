from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

T = TypeVar("T", bound="OmissionsRequest")


@_attrs_define
class OmissionsRequest:
    """
    Attributes:
        source (str): The original material, such as a consultation transcript.
        summary (str): The AI-written summary to check, such as a clinical note.
    """

    source: str
    summary: str

    def to_dict(self) -> dict[str, Any]:
        source = self.source

        summary = self.summary

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "source": source,
                "summary": summary,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        source = d.pop("source")

        summary = d.pop("summary")

        omissions_request = cls(
            source=source,
            summary=summary,
        )

        return omissions_request
