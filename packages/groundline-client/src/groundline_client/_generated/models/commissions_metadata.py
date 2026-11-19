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
        usage (Usage): The model calls a check made, and what they cost.
        disclaimer (str): What a person relying on this result should know: it helps them review
            the summary, and a summary that isn't flagged can still leave out or misstate
            important facts. It includes the sentence the pipeline's domain profile adds,
            if any. Show it next to the result wherever people read it.
    """

    pipeline: str
    alarm: CommissionsAlarm
    usage: Usage
    disclaimer: str

    def to_dict(self) -> dict[str, Any]:
        pipeline = self.pipeline

        alarm = self.alarm.to_dict()

        usage = self.usage.to_dict()

        disclaimer = self.disclaimer

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "pipeline": pipeline,
                "alarm": alarm,
                "usage": usage,
                "disclaimer": disclaimer,
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

        disclaimer = d.pop("disclaimer")

        commissions_metadata = cls(
            pipeline=pipeline,
            alarm=alarm,
            usage=usage,
            disclaimer=disclaimer,
        )

        return commissions_metadata
