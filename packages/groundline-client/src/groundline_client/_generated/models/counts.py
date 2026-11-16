from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

T = TypeVar("T", bound="Counts")


@_attrs_define
class Counts:
    """
    Attributes:
        total (int): Facts found in the source.
        present (int): Facts the summary fully records.
        partial (int): Facts the summary only partly records.
        absent (int): Facts the summary leaves out.
    """

    total: int
    present: int
    partial: int
    absent: int

    def to_dict(self) -> dict[str, Any]:
        total = self.total

        present = self.present

        partial = self.partial

        absent = self.absent

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "total": total,
                "present": present,
                "partial": partial,
                "absent": absent,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        total = d.pop("total")

        present = d.pop("present")

        partial = d.pop("partial")

        absent = d.pop("absent")

        counts = cls(
            total=total,
            present=present,
            partial=partial,
            absent=absent,
        )

        return counts
