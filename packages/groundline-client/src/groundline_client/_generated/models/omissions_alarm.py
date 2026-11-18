from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

T = TypeVar("T", bound="OmissionsAlarm")


@_attrs_define
class OmissionsAlarm:
    """
    Attributes:
        score (float): The highest missing confidence among critical facts still flagged, with
            partials at half weight; 0 if there are none.
        threshold (float): The alarm score at or above which the summary is flagged.
        level (str): The threshold level used, such as strict.
    """

    score: float
    threshold: float
    level: str

    def to_dict(self) -> dict[str, Any]:
        score = self.score

        threshold = self.threshold

        level = self.level

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "score": score,
                "threshold": threshold,
                "level": level,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        score = d.pop("score")

        threshold = d.pop("threshold")

        level = d.pop("level")

        omissions_alarm = cls(
            score=score,
            threshold=threshold,
            level=level,
        )

        return omissions_alarm
