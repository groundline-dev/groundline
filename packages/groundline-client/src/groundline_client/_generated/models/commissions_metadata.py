from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define

if TYPE_CHECKING:
    from ..models.commissions_alarm import CommissionsAlarm
    from ..models.usage import Usage


T = TypeVar("T", bound="CommissionsMetadata")


@_attrs_define
class CommissionsMetadata:
    """
    Attributes:
        pipeline (str): The pipeline that ran. It is a validated preset, whose measured results
            apply to this run.
        alarm (CommissionsAlarm):
        usage (Usage):
    """

    pipeline: str
    alarm: CommissionsAlarm
    usage: Usage

    def to_dict(self) -> dict[str, Any]:
        pipeline = self.pipeline

        alarm = self.alarm.to_dict()

        usage = self.usage.to_dict()

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "pipeline": pipeline,
                "alarm": alarm,
                "usage": usage,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.commissions_alarm import CommissionsAlarm  # noqa: PLC0415
        from ..models.usage import Usage  # noqa: PLC0415

        d = dict(src_dict)
        pipeline = d.pop("pipeline")

        alarm = CommissionsAlarm.from_dict(d.pop("alarm"))

        usage = Usage.from_dict(d.pop("usage"))

        commissions_metadata = cls(
            pipeline=pipeline,
            alarm=alarm,
            usage=usage,
        )

        return commissions_metadata
