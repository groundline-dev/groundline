from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

T = TypeVar("T", bound="Problem")


@_attrs_define
class Problem:
    """One thing wrong with a request. It never repeats the rejected value.

    Attributes:
        loc (list[int | str]): Where the problem is, such as ["body", "source"].
        msg (str):
        type_ (str):
    """

    loc: list[int | str]
    msg: str
    type_: str

    def to_dict(self) -> dict[str, Any]:
        loc = []
        for loc_item_data in self.loc:
            loc_item: int | str
            loc_item = loc_item_data
            loc.append(loc_item)

        msg = self.msg

        type_ = self.type_

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "loc": loc,
                "msg": msg,
                "type": type_,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        loc = []
        _loc = d.pop("loc")
        for loc_item_data in _loc:

            def _parse_loc_item(data: object) -> int | str:
                return cast(int | str, data)

            loc_item = _parse_loc_item(loc_item_data)

            loc.append(loc_item)

        msg = d.pop("msg")

        type_ = d.pop("type")

        problem = cls(
            loc=loc,
            msg=msg,
            type_=type_,
        )

        return problem
