from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

from ..models.commissions_request_preset import CommissionsRequestPreset
from ..types import UNSET, Unset

T = TypeVar("T", bound="CommissionsRequest")


@_attrs_define
class CommissionsRequest:
    """
    Attributes:
        source (str): The original material, such as a consultation transcript.
        summary (str): The AI-written summary to check, such as a clinical note.
        preset (CommissionsRequestPreset | Unset): Which validated preset to run.

            `commission_quick` (the default): Quick commission check (one Haiku call that lists the unsupported or
            contradicted claims, refute off, alarm). Measured on 172 held-out commission pairs: 151 caught, 1/110 clean
            notes flagged, about $0.001 per note. It passed its pre-registered test.

            `commission_reverse_lean`: Lean commission check (Opus claims, one Opus support check, Opus refute, alarm).
            Measured on 172 commission pairs: 166 caught, 0/110 clean notes flagged, about $0.14 per note. It matched the
            exact preset's catches, but failed both of its pre-registered tests, a paired-score margin less than two pairs
            wide and a false-alarm limit, so it isn't confirmed to that standard. Default:
            CommissionsRequestPreset.COMMISSION_QUICK.
    """

    source: str
    summary: str
    preset: CommissionsRequestPreset | Unset = CommissionsRequestPreset.COMMISSION_QUICK

    def to_dict(self) -> dict[str, Any]:
        source = self.source

        summary = self.summary

        preset: str | Unset = UNSET
        if not isinstance(self.preset, Unset):
            preset = self.preset.value

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "source": source,
                "summary": summary,
            }
        )
        if preset is not UNSET:
            field_dict["preset"] = preset

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        source = d.pop("source")

        summary = d.pop("summary")

        _preset = d.pop("preset", UNSET)
        preset: CommissionsRequestPreset | Unset
        if isinstance(_preset, Unset):
            preset = UNSET
        else:
            preset = CommissionsRequestPreset(_preset)

        commissions_request = cls(
            source=source,
            summary=summary,
            preset=preset,
        )

        return commissions_request
