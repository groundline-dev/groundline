from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

T = TypeVar("T", bound="Usage")


@_attrs_define
class Usage:
    """The model calls a check made, and what they cost.

    Attributes:
        calls (int): Model calls made, including any answered from a cache.
        input_tokens (int): Tokens sent to the model, by calls not answered from a cache.
        output_tokens (int): Tokens the model wrote, by calls not answered from a cache.
        cost_usd (float): What the calls not answered from a cache cost, in US dollars, at the prices
            groundline knows. A model with no known price adds nothing.
    """

    calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: float

    def to_dict(self) -> dict[str, Any]:
        calls = self.calls

        input_tokens = self.input_tokens

        output_tokens = self.output_tokens

        cost_usd = self.cost_usd

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "calls": calls,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost_usd": cost_usd,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        calls = d.pop("calls")

        input_tokens = d.pop("input_tokens")

        output_tokens = d.pop("output_tokens")

        cost_usd = d.pop("cost_usd")

        usage = cls(
            calls=calls,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
        )

        return usage
